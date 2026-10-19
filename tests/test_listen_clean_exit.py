"""A finished capture leaves at once, and a mic that will not close does not hold it
(EARS-359 do-004: EARS-344's O4 and O5, the ``exit-join`` and ``close-hang`` cases).

- **exit-join.** A plain interpreter exit joins every non-daemon thread, the STT
  executor's workers included, so one worker still stuck in its call kept a capture
  alive after it had printed its result. Run as ``python -m ears.listen``, the capture
  now flushes and ends with ``os._exit``. The test runs that very path (``runpy`` on
  ``ears.listen.__main__``, ``tests/fixtures/script_exit_capture.py``), WITHOUT
  ``PYTHONUNBUFFERED``: stdout is a pipe, block-buffered, as the keeper's log file is,
  so the result line is there only if the exit flushed it.
- **close-hang.** ``MicSource.close`` aborts the stream (no drain) instead of stopping it,
  on a thread it waits on for at most ``close_wait_s``. A stream whose stop, abort and
  close all hang still lets the capture write ``listen-stopped``.

Fakes only: a fake STT, a ``file:`` WAV, a fake ``sounddevice`` at the one seam. The
subprocess is the test's own; only its own process group is killed, and only if it is
still running (the failure).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np
import pytest

from ears.listen import HeardSink, MicSource, capture
from tests.listen_helpers import FakeSTT, read_lines
from tests.test_listen_parts import fake_sd  # noqa: F401 - a fixture, used by name

FIXTURE = Path(__file__).parent / "fixtures" / "script_exit_capture.py"
RATE = 16000
BOUND_S = 20.0  # start-up (imports), the capture (a few seconds unpaced) and its exit, with room


def _wav(path: Path) -> Path:
    t = np.arange(int(2.0 * RATE)) / RATE
    tone = (4000 * np.sin(2 * np.pi * 220 * t)).astype(np.int16)
    audio = np.concatenate([np.zeros(RATE // 2, dtype=np.int16), tone, np.zeros(RATE, dtype=np.int16)])
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(audio.tobytes())
    return path


def test_run_as_a_script_a_stuck_stt_worker_does_not_hold_the_process_and_the_result_line_is_kept(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if k != "PYTHONUNBUFFERED"}
    env.update(HOME=str(home), VOICEMODE_BASE_DIR=str(tmp_path / "vm"))
    err_path = tmp_path / "stderr.txt"
    with open(err_path, "w") as err:
        proc = subprocess.Popen([sys.executable, str(FIXTURE), str(_wav(tmp_path / "turn.wav")), str(logs)],
                                stdout=subprocess.PIPE, stderr=err, text=True, env=env, start_new_session=True)
        try:
            out, _ = proc.communicate(timeout=BOUND_S)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)  # only our own child's own group
            proc.wait(timeout=5)
            pytest.fail(f"still running after {BOUND_S}s: the stuck STT worker held the process\n"
                        f"{err_path.read_text()}")
    stderr = err_path.read_text()
    assert proc.returncode == 0, stderr
    assert "chunk 1 called: never returning" in stderr  # the worker really was stuck at exit
    assert "the __main__ block returned" not in stderr  # os._exit ended it, inside the block
    lines = read_lines(logs)
    assert [e["detail"] for e in lines if e.get("event") == "stt-error"] == ["timeout"]
    assert lines[-1]["event"] == "listen-stopped" and lines[-1]["reason"] == "eof"
    # stdout is a pipe and PYTHONUNBUFFERED is not set: the line is here only if it was flushed
    assert out.strip(), "no result line: the exit dropped stdout's buffer"
    assert json.loads(out.strip().splitlines()[-1])["reason"] == "eof"


def _run_capture(mic, sink, timeout):
    out: dict = {}
    th = threading.Thread(target=lambda: out.update(result=capture(mic, FakeSTT(), sink, stt_workers=0)),
                          daemon=True, name="test-capture")
    th.start()
    th.join(timeout)
    assert not th.is_alive(), "the capture never returned: the mic's close held it"
    return out["result"]


def test_a_mic_whose_stream_never_closes_still_lets_the_capture_write_listen_stopped(fake_sd, tmp_path, capfd):
    fake_sd.blocks = 2  # two blocks, then the device goes quiet: the stall raises
    fake_sd.hang = {"stop", "abort", "close"}  # and PortAudio never comes back from any of them
    mic = MicSource(stall_s=0.1)
    mic.close_wait_s = 0.3
    t = time.monotonic()
    result = _run_capture(mic, HeardSink(tmp_path), timeout=5)
    assert time.monotonic() - t < 3
    assert result.reason == "error" and "delivered no audio" in result.error
    stopped = [ln for ln in read_lines(tmp_path) if ln.get("event") == "listen-stopped"]
    assert [s["reason"] for s in stopped] == ["error"]
    assert mic.close_timed_out and fake_sd.calls[0] == "abort"
    assert "did not close in" in capfd.readouterr().err


def test_a_mic_closes_by_abort_never_stop_and_waits_no_longer_than_it_must(fake_sd, tmp_path):
    fake_sd.blocks = 2
    mic = MicSource(stall_s=0.1, close_wait_s=5.0)
    t = time.monotonic()
    result = _run_capture(mic, HeardSink(tmp_path), timeout=5)
    assert time.monotonic() - t < 2  # a close that returns is not waited out to close_wait_s
    assert result.reason == "error"
    assert fake_sd.calls == ["abort", "close"] and not fake_sd.stopped and not mic.close_timed_out
    mic.close()  # a second close is a no-op
    assert fake_sd.calls == ["abort", "close"]


def test_a_stream_with_no_abort_still_stops_and_still_closes(fake_sd, tmp_path):
    """EARS-359 do-005 (do-004 review nit 1): abort is looked up inside its own try, so a
    stream without one falls back to stop() and still reaches close()."""
    fake_sd.blocks = 2
    delattr(fake_sd.InputStream, "abort")
    mic = MicSource(stall_s=0.1, close_wait_s=2.0)
    result = _run_capture(mic, HeardSink(tmp_path), timeout=5)
    assert result.reason == "error"
    assert fake_sd.calls == ["stop", "close"] and not mic.close_timed_out
