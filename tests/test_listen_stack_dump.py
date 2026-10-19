"""A wedged capture names its own stack when it is signalled (EARS-359 do-001).

Every effect of SIGTERM runs on the capture's main thread, so a capture whose main
thread is stuck in a wait looks the same from outside wherever it is stuck. With
``faulthandler`` registered on SIGTERM (chained, after the capture's own handler)
and on SIGUSR1, the capture writes every thread's stack to stderr when signalled,
and the keeper's capture log shows where it was stuck.

Each test runs the real capture CLI in a child process
(``tests/fixtures/hung_stt_capture.py``), whose fake STT hangs in the turn's
re-decode, so the main thread waits in ``_redecoded_text``. No device, no network:
a ``file:`` WAV and a fake STT. The tests signal only their own child, by pid, and
kill only its own process group.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np
import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "hung_stt_capture.py"
RATE = 16000
HEARTBEAT_S = 0.5


def _wav(path: Path) -> Path:
    """One second of a loud tone, then a second of silence: one turn."""
    t = np.arange(RATE) / RATE
    tone = (4000 * np.sin(2 * np.pi * 220 * t)).astype(np.int16)
    audio = np.concatenate([tone, np.zeros(RATE, dtype=np.int16)])
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(audio.tobytes())
    return path


def _until(pred, timeout: float, step: float = 0.05):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = pred()
        if v:
            return v
        time.sleep(step)
    return pred()


def _newest_mtime(d: Path) -> float:
    return max((p.stat().st_mtime for p in d.rglob("*") if p.is_file()), default=0.0)


def _thread_blocks(text: str) -> list[str]:
    """faulthandler's dump, one string per thread (``Thread 0x...`` / ``Current thread 0x...``)."""
    starts = [m.start() for m in re.finditer(r"^(?:Current thread|Thread) 0x", text, re.M)]
    return [text[a:b] for a, b in zip(starts, starts[1:] + [len(text)])]


def _main_thread_block(text: str) -> str | None:
    """The dumped thread that is in the CLI's ``main`` and in ``_redecoded_text``: the main thread."""
    for block in _thread_blocks(text):
        if re.search(r"__main__\.py\", line \d+ in main\b", block) and " in _redecoded_text" in block:
            return block
    return None


class HungCapture:
    """The child capture, wedged in ``_redecoded_text`` until :meth:`release`."""

    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.release_file = tmp_path / "release"
        self.stop_file = tmp_path / "STOP"
        self.logs = tmp_path / "logs"
        self.logs.mkdir()
        self.err_path = tmp_path / "stderr.txt"
        home = tmp_path / "home"
        home.mkdir()
        env = dict(os.environ, HOME=str(home), VOICEMODE_BASE_DIR=str(tmp_path / "voicemode"))
        argv = [sys.executable, str(FIXTURE), str(self.release_file),
                "capture", "--source", f"file:{_wav(tmp_path / 'turn.wav')}", "--tail-silence", "100000",
                "--heartbeat", str(HEARTBEAT_S), "--log-dir", str(self.logs), "--stop-file", str(self.stop_file),
                "--silence", "0.6", "--speaker-split", "0", "--turn-text", "redecode",
                "--session", "ears-359-test", "--agent", "test"]
        self._err = open(self.err_path, "w")
        self.proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=self._err, text=True, env=env,
                                     start_new_session=True)

    def stderr(self) -> str:
        return self.err_path.read_text()

    def wait_wedged(self) -> None:
        """The re-decode has started AND the main thread has stopped beating (it waits on it)."""
        assert _until(lambda: "hung-stt: redecode blocking" in self.stderr(), 20), self.stderr()
        assert _until(lambda: time.time() - _newest_mtime(self.logs) > 3 * HEARTBEAT_S, 10), \
            "the main thread kept beating: it is not waiting on the re-decode"
        assert self.proc.poll() is None, self.stderr()

    def release(self) -> None:
        self.release_file.touch()

    def finish(self, timeout: float = 15) -> tuple[int, dict]:
        out, _ = self.proc.communicate(timeout=timeout)
        return self.proc.returncode, json.loads(out.strip().splitlines()[-1])

    def close(self) -> None:
        if self.proc.poll() is None:  # only our own child's own group
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.proc.wait(timeout=5)
        self._err.close()


@pytest.fixture
def hung(tmp_path):
    cap = HungCapture(tmp_path)
    try:
        yield cap
    finally:
        cap.close()


def test_sigterm_dumps_the_main_thread_stack_and_still_stops_the_capture(hung):
    hung.wait_wedged()
    hung.proc.send_signal(signal.SIGTERM)
    block = _until(lambda: _main_thread_block(hung.stderr()), 5)
    assert block, "no stack dump naming _redecoded_text on SIGTERM:\n" + hung.stderr()
    # the capture's own SIGTERM handler still ran (faulthandler chains to it): once the
    # re-decode returns, the loop reads the stop Event and stops cleanly. Nothing else
    # could stop it: no STOP file, no ceiling, and the source's tail is endless.
    hung.release()
    rc, result = hung.finish()
    assert (rc, result["reason"]) == (0, "stop"), hung.stderr()
    assert not hung.stop_file.exists()


def test_sigusr1_dumps_the_stack_and_does_not_stop_the_capture(hung):
    hung.wait_wedged()
    hung.proc.send_signal(signal.SIGUSR1)
    assert _until(lambda: _main_thread_block(hung.stderr()), 5), \
        "no stack dump naming _redecoded_text on SIGUSR1:\n" + hung.stderr()
    time.sleep(0.5)
    assert hung.proc.poll() is None, "SIGUSR1 ended the capture:\n" + hung.stderr()
    # ...and it did not ask the capture to stop either: released, it beats on until STOP
    hung.release()
    time.sleep(1.0)
    assert hung.proc.poll() is None, hung.stderr()
    hung.stop_file.touch()
    rc, result = hung.finish()
    assert (rc, result["reason"]) == (0, "stop"), hung.stderr()


def test_registered_after_the_handler_it_dumps_and_chains_even_on_a_second_main():
    """The order rule, and a second ``main()`` in one process: each round sets the Python
    handler (as ``main`` does) and then registers. A child that TERMs only itself."""
    code = (
        "import os, signal, sys, time\n"
        "from ears.listen.__main__ import _register_stack_dumps\n"
        "for rnd in (1, 2):\n"
        "    signal.signal(signal.SIGTERM, lambda *_, r=rnd: os.write(2, f'handler {r} ran\\n'.encode()))\n"
        "    _register_stack_dumps()\n"
        "os.kill(os.getpid(), signal.SIGTERM); time.sleep(0.3)\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert re.search(r"^Current thread 0x", proc.stderr, re.M), proc.stderr
    assert "handler 2 ran" in proc.stderr, proc.stderr
