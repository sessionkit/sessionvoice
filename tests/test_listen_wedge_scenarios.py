"""Every way a capture wedged ends by itself, within its bound (EARS-359 do-003).

EARS-344's repro scenarios A-G (``findings/repro/``), each run as the real capture CLI
in a process of its own (``tests/fixtures/wedge_capture.py``), with its real watchdog
(``WEDGE_S`` 3 s, ``STOP_GRACE_S`` 1 s there). Before the watchdog, each of these needed
a SIGKILL from outside. Now each one ends by itself: exit 3, a ``capture-wedged`` heard
row (or, with ``heard.lock`` held, a line saying it was skipped), every thread's stack on
stderr, and no streaming child left behind.

``os._exit`` only ever runs in the child capture process, never in pytest. The tests stand
in for the keeper (a STOP file, a SIGTERM to the child's pid) and for another heard writer
(``heard.lock``), and kill nothing but their own child's process group, at teardown, if
it is still there (which is the failure). No device, no network, no live capture.
"""

from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np
import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "wedge_capture.py"
RATE = 16000
WEDGE_S, STOP_GRACE_S = 3.0, 1.0   # as the fixture sets them
BOUND_S = 15.0  # start-up (imports) + the wedge + the watchdog's own bound, with room


def _wav(path: Path) -> Path:
    t = np.arange(int(1.5 * RATE)) / RATE
    tone = (4000 * np.sin(2 * np.pi * 220 * t)).astype(np.int16)
    audio = np.concatenate([np.zeros(RATE // 2, dtype=np.int16), tone, np.zeros(RATE // 2, dtype=np.int16)])
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


def _gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


class Wedge:
    def __init__(self, tmp_path: Path, scenario: str, *, unbuffered: bool = True):
        self.scenario = scenario
        self.logs = tmp_path / "logs"
        self.logs.mkdir()
        self.stop_file = tmp_path / "STOP"
        self.pid_file = tmp_path / "child.pid"
        self.err_path = tmp_path / "stderr.txt"
        home = tmp_path / "home"
        home.mkdir()
        env = dict(os.environ, HOME=str(home), VOICEMODE_BASE_DIR=str(tmp_path / "vm"), PYTHONUNBUFFERED="1")
        if not unbuffered:  # stdout block-buffered, as the keeper's log file is (EARS-359 do-004)
            del env["PYTHONUNBUFFERED"]
        self._err = open(self.err_path, "w")
        self.lock_fd = None
        self.t0 = time.monotonic()
        self.proc = subprocess.Popen(
            [sys.executable, str(FIXTURE), scenario, str(self.logs), str(self.stop_file),
             str(_wav(tmp_path / "turn.wav")), str(self.pid_file)],
            stdout=subprocess.PIPE, stderr=self._err, text=True, env=env, start_new_session=True)

    def stderr(self) -> str:
        return self.err_path.read_text()

    def lines(self) -> list[dict]:
        out = []
        for p in sorted(self.logs.glob("heard_*.jsonl")):
            for raw in p.read_text().splitlines():
                try:
                    out.append(json.loads(raw))
                except ValueError:
                    pass  # a line the capture was writing as it was killed
        return out

    def events(self, name: str) -> list[dict]:
        return [ln for ln in self.lines() if ln.get("event") == name]

    def wait_for(self, text: str, timeout: float = 10) -> None:
        assert _until(lambda: text in self.stderr(), timeout), f"never saw {text!r}:\n{self.stderr()}"

    def hold_heard_lock(self) -> None:
        self.lock_fd = os.open(str(self.logs / "heard.lock"), os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self.lock_fd, fcntl.LOCK_EX)

    def ends_by_itself(self) -> int:
        """Its exit code; fails if it is still running after BOUND_S (it would need a SIGKILL)."""
        try:
            self.proc.wait(timeout=max(0.1, BOUND_S - (time.monotonic() - self.t0)))
        except subprocess.TimeoutExpired:
            pytest.fail(f"{self.scenario}: still running after {BOUND_S}s; it needs a SIGKILL\n{self.stderr()}")
        return self.proc.returncode

    def close(self) -> None:
        if self.proc.poll() is None:  # the failure case: only our own child's own group
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.proc.wait(timeout=5)
        if self.pid_file.exists():  # a streaming child left behind fails the test, and is ended here
            pid = int(self.pid_file.read_text() or 0)
            if pid and not _gone(pid):
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)  # it is in our child's group
                except ProcessLookupError:
                    pass
        if self.lock_fd is not None:
            fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
            os.close(self.lock_fd)
        self._err.close()


@pytest.fixture
def wedge(tmp_path):
    made = []

    def start(scenario, **kw):
        made.append(Wedge(tmp_path, scenario, **kw))
        return made[-1]

    yield start
    for w in made:
        w.close()


def assert_tripped(w: Wedge, why: str, frame: str | None) -> dict:
    """Exit 3 by itself, the watchdog said why, the stacks name ``frame``, and the row."""
    rc = w.ends_by_itself()
    err = w.stderr()
    assert rc == 3, err
    assert f"ears capture watchdog: {why} (" in err, err
    if frame is not None:
        assert f" in {frame}\n" in err, f"no stack naming {frame}:\n{err}"
    (row,) = w.events("capture-wedged")
    assert (row["why"], row["exit"], row["pid"]) == (why, 3, w.proc.pid)
    stopped = w.events("listen-stopped")
    if stopped and why != "exit":  # after ``exit`` it came first: the capture had finished
        # Killing the streaming child can free the write the loop was stuck in (F): the loop
        # then writes listen-stopped in the moment before os._exit(3) wins (EARS-359 do-006).
        # Tolerated only then, and only after the watchdog's row.
        assert "ears capture watchdog: SIGKILL streaming child pid" in err, f"listen-stopped, no kill:\n{err}"
        assert all(s["seq"] > row["seq"] for s in stopped), "listen-stopped before capture-wedged"
    return row


# -- a healthy capture: the watchdog never trips ------------------------------------------


def test_control_a_healthy_capture_stops_on_stop_and_the_watchdog_stays_quiet(wedge):
    w = wedge("control")
    assert _until(lambda: len(w.events("heartbeat")) >= 2, 10), w.stderr()
    w.stop_file.touch()
    assert w.ends_by_itself() == 0, w.stderr()
    assert w.events("listen-stopped")[-1]["reason"] == "stop"
    assert not w.events("capture-wedged") and "ears capture watchdog" not in w.stderr()


# -- A, B, C: a hung STT wait (its do-002 bound set out of reach) ------------------------


def test_A_redecode_hang_sigterm_is_honoured_off_the_main_thread(wedge):
    w = wedge("redecode-hang")
    w.wait_for("transcribe_final (redecode) called")
    time.sleep(0.3)
    w.proc.send_signal(signal.SIGTERM)  # the keeper's TERM: the handler sets the Event, the loop never reads it
    assert_tripped(w, "stop-ignored", "_redecoded_text")


def test_B_chunk_hang_with_no_stop_at_all_ends_as_a_wedge(wedge):
    w = wedge("chunk-hang")
    w.wait_for("transcribe (chunk) called")
    row = assert_tripped(w, "wedge", "_collect")
    assert row["stalled_s"] >= WEDGE_S


def test_C_stop_drain_hang_a_stop_while_beating_is_honoured_within_the_grace(wedge):
    w = wedge("stop-drain-hang")
    w.wait_for("STOP touched")
    row = assert_tripped(w, "stop-ignored", "_collect")
    assert STOP_GRACE_S <= row["waited_s"] < WEDGE_S


# -- D: another writer holds heard.lock: the row must never block -------------------------


def test_D_lock_held_the_watchdog_skips_its_row_and_still_exits(wedge):
    w = wedge("lock-held")
    assert _until(lambda: len(w.events("heartbeat")) >= 2, 10), w.stderr()
    w.hold_heard_lock()  # held for the rest of the test: the capture's next write waits on it
    t = time.monotonic()
    rc = w.ends_by_itself()
    err = w.stderr()
    assert rc == 3, err
    assert "ears capture watchdog: wedge (" in err and " in append\n" in err, err
    assert "capture-wedged row skipped: heard.lock is held by another writer" in err, err
    assert time.monotonic() - t < WEDGE_S + 3, "the watchdog waited on heard.lock"
    assert not w.events("capture-wedged")


# -- E: the mic's close never returns -------------------------------------------------------


def test_E_close_hang_ends_as_a_wedge(wedge):
    w = wedge("close-hang")
    w.wait_for("fake mic close(): never returning")
    assert_tripped(w, "wedge", "close")


# -- F: the streaming child stops reading its stdin ------------------------------------------
# Since do-005 the capture feeds the child in live mode (non-blocking, frames whole, whole
# frames dropped past a bound): F no longer wedges, in either alignment, and STOP ends it
# at once. The old blocking write is kept as its own scenario: the watchdog's guard.


def _ts(row) -> float:
    from datetime import datetime

    return datetime.fromisoformat(row["ts"]).timestamp()


@pytest.mark.parametrize("scenario", ["pipe-full", "pipe-full-shifted"])
def test_F_live_feed_keeps_beating_with_the_child_not_reading_and_stop_ends_it_at_once(wedge, scenario):
    w = wedge(scenario)
    assert _until(lambda: w.pid_file.exists() and w.pid_file.read_text(), 10), w.stderr()
    child = int(w.pid_file.read_text())
    w.wait_for("first frame dropped", timeout=10)  # the pipe and the 1 s backlog are full
    dropped_at = time.time()
    # the frame loop goes on: three beats after the child stopped taking frames
    assert _until(lambda: sum(1 for b in w.events("heartbeat") if _ts(b) > dropped_at) >= 3, 10), w.stderr()
    assert w.proc.poll() is None
    stop_at = time.time()
    w.stop_file.touch()
    assert w.ends_by_itself() == 0, w.stderr()
    (stopped,) = w.events("listen-stopped")
    assert stopped["reason"] == "stop"
    assert _ts(stopped) - stop_at < 0.5, f"STOP took {_ts(stopped) - stop_at:.2f}s"
    assert not w.events("capture-wedged") and "ears capture watchdog" not in w.stderr()
    assert _until(lambda: _gone(child), 5), f"streaming child {child} left behind"


def test_F_blocking_feed_the_watchdog_still_ends_it_and_kills_the_streaming_child(wedge):
    w = wedge("pipe-full-blocking")
    assert _until(lambda: w.pid_file.exists() and w.pid_file.read_text(), 10), w.stderr()
    child = int(w.pid_file.read_text())
    time.sleep(0.5)  # the 64 KB pipe is full within ~70 frames: the loop is stuck in the write
    w.stop_file.touch()  # the keeper's STOP: no frame reaches the stop check
    assert_tripped(w, "stop-ignored", "feed")
    assert f"SIGKILL streaming child pid {child}" in w.stderr()
    assert _until(lambda: _gone(child), 5), f"streaming child {child} left behind"


# -- G: an STT worker still in its call when the interpreter exits ---------------------------


def test_G_exit_join_the_process_is_gone_within_the_grace_after_the_capture_ends(wedge):
    w = wedge("exit-join")
    row = assert_tripped(w, "exit", None)
    assert w.events("listen-stopped")[-1]["reason"] == "eof"  # the capture had finished...
    assert "main returned 0; interpreter exiting" in w.stderr()  # ...and the exit hung
    assert "_python_exit" in w.stderr() or "_shutdown" in w.stderr()
    assert row["waited_s"] >= STOP_GRACE_S
    out = w.proc.stdout.read()
    assert json.loads(out.strip().splitlines()[-1])["reason"] == "eof"  # the result was printed


def test_G_without_pythonunbuffered_the_trip_still_flushes_the_printed_result(wedge):
    """The keeper's stdout is a file, block-buffered. After the ``exit`` trip the result line
    the finished capture printed is still there (EARS-359 do-004). A guard, not a before/after
    test: it passes without the watchdog's own flush too, because CPython flushes stdout on
    ``sys.exit`` BEFORE the join that hangs (measured, 3.13). The flush the trip path needs
    is pinned in-process (``test_listen_watchdog.py``); the one the ``__main__`` exit needs,
    by ``test_listen_clean_exit.py``."""
    w = wedge("exit-join", unbuffered=False)
    assert_tripped(w, "exit", None)
    out = w.proc.stdout.read()
    assert out.strip(), "no result line: the trip's os._exit dropped stdout's buffer"
    assert json.loads(out.strip().splitlines()[-1])["reason"] == "eof"
