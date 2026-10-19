"""The streaming child's feed never blocks the capture (EARS-359 do-005, EARS-344's O7).

A child that stops reading its stdin filled the 64 KB pipe in about 2 s, and the next
blocking write stopped the frame loop: no beat, STOP and SIGTERM ignored. In live mode
(``ProcessBackend.live``, which the capture's ``Streamer`` turns on):

- ``feed`` never waits; frames are kept WHOLE, so the child's int16 stream never goes out
  of step;
- past ``buffer_s`` waiting, new frames are dropped whole and counted;
- past ``fail_s`` dropped with no byte moved, a fatal ``error``: the capture says
  ``partials-error`` and goes back to chunk partials;
- ``close`` SIGKILLs and reaps the child, so a failed one is never left behind (EARS-344
  review-002 section 5).

``ears listen`` and ``ears file`` do not use live mode, and keep their blocking,
nothing-dropped feed (pinned below). Real child processes, the test's own, each one
killed at teardown if still there. No device, no network.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time

import numpy as np
import pytest

from ears.backends import proc
from ears.backends.proc import ProcessBackend
from ears.listen import capture
from ears.listen.sources import FRAME_SAMPLES
from ears.listen.streaming import Partials, Streamer
from tests.listen_helpers import CountingSource, FakeSTT, RecordingSink, energy_detector, read_lines, synth

FRAME_BYTES = FRAME_SAMPLES * 2

# Never reads stdin: says ready (an events v1 line), writes its pid, sleeps.
NOT_READING = ("import json,os,sys,time; open(sys.argv[1],'w').write(str(os.getpid())); "
               "print(json.dumps({'type':'ready','v':1,'backend':'fake','model':'fake','rate':16000,'load_s':0}),"
               " flush=True); time.sleep(1e9)")

# Sleeps WAKE_S, then reads stdin to EOF and checks every frame: whole (all 480 samples one
# value) and in order (that value, the frame's index, only ever goes up). Writes a report.
CHECKER = r"""
import json, sys, time
import numpy as np
report, wake = sys.argv[1], float(sys.argv[2])
time.sleep(wake)
data = sys.stdin.buffer.read()
x = np.frombuffer(data[: len(data) // 2 * 2], dtype=np.int16)
n = len(x) // 480
frames = x[: n * 480].reshape(n, 480) if n else x[:0].reshape(0, 480)
whole = bool(len(data) % 960 == 0 and all((f == f[0]).all() for f in frames))
idx = [int(f[0]) for f in frames]
json.dump({"bytes": len(data), "frames": n, "whole": whole,
           "in_order": all(b > a for a, b in zip(idx, idx[1:])), "first": idx[:1], "last": idx[-1:]},
          open(report, "w"))
"""


def frame(i: int) -> bytes:
    return np.full(FRAME_SAMPLES, i, dtype=np.int16).tobytes()


@pytest.fixture
def children():
    made: list[ProcessBackend] = []
    yield made
    for b in made:
        if b.p.poll() is None:
            b.p.kill()
            b.p.wait(5)


def spawn(children, code, *args, live=True, **kw):
    b = ProcessBackend([sys.executable, "-c", code, *map(str, args)], env=dict(os.environ))
    children.append(b)
    return b.live(**kw) if live else b


def gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


# -- the feed itself -------------------------------------------------------------------------


def test_a_child_that_stops_reading_never_blocks_the_feed_and_frames_stay_whole_and_in_step(children, tmp_path):
    report = tmp_path / "report.json"
    b = spawn(children, CHECKER, report, 1.0, buffer_s=0.3, fail_s=60)
    worst = 0.0
    for i in range(1, 301):  # 288 KB: well past the 64 KB pipe and the 0.3 s (9.6 KB) backlog
        t = time.monotonic()
        b.feed(frame(i))
        worst = max(worst, time.monotonic() - t)
    assert worst < 0.05, f"a feed waited {worst:.3f}s"
    assert b.dropped_frames > 0 and not b.stalled
    assert len(b._pending) <= int(0.3 * 16000) * 2
    # the child wakes and reads: what was waiting goes first, then the rest
    deadline = time.monotonic() + 10
    while b._pending and time.monotonic() < deadline:
        b._write_some()
        time.sleep(0.01)
    assert not b._pending
    for i in range(301, 311):
        b.feed(frame(i))
    while b._pending and time.monotonic() < deadline:
        b._write_some()
        time.sleep(0.01)
    b.p.stdin.close()  # EOF for the checker (not b.close(), which would kill it)
    b.p.wait(10)
    got = json.loads(report.read_text())
    assert got["whole"] and got["in_order"], got  # never part of a frame, never out of step
    assert got["frames"] == 310 - b.dropped_frames and got["first"] == [1] and got["last"] == [310]


class _Pipe:
    """A stand-in for the child's stdin: each write takes what ``take`` says (None = EAGAIN)."""

    def __init__(self, takes):
        self.takes, self.got = list(takes), bytearray()

    def write(self, data):
        n = self.takes.pop(0) if self.takes else None
        if n:
            self.got += bytes(data[:n])
        return n

    def close(self):
        pass


def test_a_short_write_keeps_the_rest_of_its_frame_first_and_eagain_none_moves_nothing(children, tmp_path):
    b = spawn(children, NOT_READING, tmp_path / "pid")
    b.p.stdin, real = _Pipe([256, None, None, None, 704, FRAME_BYTES, None]), b.p.stdin
    try:
        b.feed(frame(1))  # 256 of 960 go; then EAGAIN (None): nothing more moved, no error
        assert len(b._pending) == FRAME_BYTES - 256
        b.feed(frame(2))  # EAGAIN first: frame 2 waits behind the rest of frame 1
        assert len(b._pending) == 2 * FRAME_BYTES - 256
        b.feed(frame(3))  # 704 (the rest of frame 1), then all of frame 2, then EAGAIN
        assert bytes(b.p.stdin.got) == frame(1) + frame(2)
        assert bytes(b._pending) == frame(3)
    finally:
        b.p.stdin = real


def test_past_the_fail_bound_the_stream_fails_once_and_the_feed_goes_quiet(children, tmp_path):
    b = spawn(children, NOT_READING, tmp_path / "pid", buffer_s=0.1, fail_s=0.3)
    b.p.stdin, real = _Pipe([]), b.p.stdin  # takes nothing, ever
    try:
        for i in range(1, 100):
            b.feed(frame(i))
        assert b.stalled
        errors = [e for e in b.events() if e.type == "error"]
        assert len(errors) == 1 and errors[0].get("fatal") and "not reading its stdin" in errors[0].get("message")
        n = b.dropped_frames
        b.feed(frame(100))
        assert b.dropped_frames == n  # failed: the feed is a no-op now
    finally:
        b.p.stdin = real


class _Gate:
    """A stand-in stdin that takes nothing while ``shut``, and everything when open."""

    def __init__(self):
        self.shut = True

    def write(self, data):
        return None if self.shut else len(data)

    def close(self):
        pass


def test_a_child_that_keeps_catching_up_is_never_failed_however_many_frames_it_cost(children, tmp_path):
    """The fail bound is audio dropped with NO byte moved: each stall here drops less than
    it, though all of them together drop four times as much."""
    b = spawn(children, NOT_READING, tmp_path / "pid", buffer_s=0.1, fail_s=0.3)  # cap 3 frames, fail 10
    b.p.stdin, real = _Gate(), b.p.stdin
    try:
        i = 0
        for _ in range(5):
            b.p.stdin.shut = True
            for _ in range(3 + 8):  # 3 wait, 8 are dropped: under the 10 that fail it
                i += 1
                b.feed(frame(i))
            b.p.stdin.shut = False
            i += 1
            b.feed(frame(i))  # it reads again: everything waiting goes
        assert b.dropped_frames == 40 and not b.stalled
        assert not [e for e in b.events() if e.type == "error"]
    finally:
        b.p.stdin = real


def test_live_close_kills_and_reaps_a_child_that_never_reads(children, tmp_path):
    pidf = tmp_path / "pid"
    b = spawn(children, NOT_READING, pidf)
    deadline = time.monotonic() + 10
    while not (pidf.exists() and pidf.read_text()) and time.monotonic() < deadline:
        time.sleep(0.02)
    pid = int(pidf.read_text())
    t = time.monotonic()
    b.close()
    assert time.monotonic() - t < 0.1  # close never waits
    deadline = time.monotonic() + 3
    while b.p.returncode is None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert b.p.returncode is not None and gone(pid)  # killed AND reaped: no zombie, no orphan


def test_without_live_mode_the_engine_feed_still_waits_and_close_lets_the_child_finish(children, tmp_path):
    """``ears file`` / ``ears listen``: a slow child is waited for, nothing is dropped, and
    close only closes stdin: the child reads to EOF and finishes by itself."""
    report = tmp_path / "report.json"
    b = spawn(children, CHECKER, report, 0.5, live=False)
    for i in range(1, 201):  # 192 KB: blocks until the child wakes and reads; nothing dropped
        b.feed(frame(i))
    b.close()
    assert b.p.wait(10) == 0
    got = json.loads(report.read_text())
    assert got["whole"] and got["in_order"] and got["frames"] == 200 and b.dropped_frames == 0


def test_streamer_turns_live_mode_on_for_the_capture(children, tmp_path):
    b = spawn(children, NOT_READING, tmp_path / "pid", live=False)
    assert not b._live
    Streamer(b)
    assert b._live and not os.get_blocking(b.p.stdin.fileno())


# -- in the capture --------------------------------------------------------------------------


def test_a_capture_whose_child_stops_reading_says_partials_error_and_goes_back_to_chunks(children, tmp_path,
                                                                                            monkeypatch):
    monkeypatch.setattr(proc, "LIVE_FAIL_S", 2.0)
    pidf = tmp_path / "pid"

    def make():
        b = ProcessBackend([sys.executable, "-c", NOT_READING, str(pidf)], env=dict(os.environ))
        children.append(b)
        # the source is unpaced: wait (in the test only) for the child to say ready, or the
        # whole file would go by before its first line
        deadline = time.monotonic() + 10
        while b.q.empty() and time.monotonic() < deadline:
            time.sleep(0.01)
        return Streamer(b)

    source = CountingSource(synth([("silence", 0.5), ("speech", 2.0), ("silence", 3.0), ("speech", 2.0)]),
                            tail_silence_s=20.0, realtime=False)
    sink = RecordingSink(tmp_path / "logs", source)
    out: dict = {}
    th = threading.Thread(target=lambda: out.update(result=capture(
        source, FakeSTT(), sink, detector=energy_detector(1.0), heartbeat=1.0, stt_workers=0,
        partials=Partials("nemotron", make=make))), daemon=True, name="test-capture")
    th.start()
    th.join(20)  # a feed that blocks fails here, not by hanging the suite (the teardown kills
    assert not th.is_alive(), "the capture is stuck feeding a child that does not read"  # the child)
    result = out["result"]
    assert result.reason == "eof"
    lines = read_lines(tmp_path / "logs")
    errs = [ln for ln in lines if ln.get("event") == "partials-error"]
    assert len(errs) == 1 and "not reading its stdin" in errs[0]["detail"]
    # after the failure the chunks are the partials again (no streaming engine on them)
    after = [ln for ln in lines if ln["kind"] == "partial" and ln["seq"] > errs[0]["seq"]]
    assert after and all("engine" not in ln for ln in after)
    assert len([ln for ln in lines if ln.get("event") == "heartbeat"]) >= 20  # it beat all along
    pid = int(pidf.read_text())
    deadline = time.monotonic() + 3
    while not gone(pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert gone(pid), "the failed stream's child was left behind"
