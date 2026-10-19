"""The capture's watchdog, in-process, with its exit injected (EARS-359 do-003).

``Watchdog(exit=...)`` here never gets ``os._exit``: a fake records the code instead, so
nothing in this file can end the pytest process. ``capture()`` has no watchdog unless
given one, and the CLI gives one only when it owns its process (``argv`` None). Both
are pinned below. The real ``os._exit`` path runs in a child process of its own in
``tests/test_listen_wedge_scenarios.py``.
"""

from __future__ import annotations

import fcntl
import os
import signal
import threading
import time

import pytest

from ears.listen import capture
from ears.listen import __main__ as cli
from ears.listen import watchdog as wd_mod
from ears.listen.loop import CaptureResult
from ears.listen.watchdog import Watchdog, heard_lock_busy
from tests.listen_helpers import CountingSource, FakeSTT, RecordingSink, energy_detector, read_lines, synth

FAST = dict(wedge_s=0.3, stop_grace_s=0.3, poll_s=0.02, row_wait_s=0.3)


class Rig:
    """The watchdog's seams, recorded in the order they were used."""

    def __init__(self):
        self.calls: list = []
        self.exited = threading.Event()

    def exit(self, code):
        self.calls.append(("exit", code))
        self.exited.set()

    def kill(self, pid, sig):
        self.calls.append(("kill", pid, sig))

    def dump(self):
        self.calls.append(("dump",))

    def row(self, fields):
        self.calls.append(("row", fields))

    def names(self):
        return [c[0] for c in self.calls]

    def dog(self, **kw):
        return Watchdog(exit=self.exit, kill=self.kill, dump=self.dump, **{**FAST, **kw})


@pytest.fixture
def rig():
    r = Rig()
    made: list[Watchdog] = []
    orig = r.dog

    def dog(**kw):
        made.append(orig(**kw))
        return made[-1]

    r.dog = dog
    yield r
    for d in made:
        d.stop()


def test_no_frame_for_wedge_s_trips_once_dump_row_kill_then_exit_3(rig):
    d = rig.dog()
    d.start(stop_requested=lambda: False, write_row=rig.row, children=lambda: [4242], stream_s=lambda: 12.5)
    assert rig.exited.wait(3)
    assert rig.names() == ["dump", "row", "kill", "exit"]
    assert rig.calls[2] == ("kill", 4242, signal.SIGKILL)
    assert rig.calls[3] == ("exit", 3)
    fields = rig.calls[1][1]
    assert fields["why"] == "wedge" and fields["exit"] == 3 and fields["pid"] == os.getpid()
    assert fields["stalled_s"] >= 0.3 and fields["stream_s"] == 12.5
    assert d.tripped == "wedge"
    time.sleep(0.2)
    assert rig.names().count("exit") == 1  # once


def test_a_frame_loop_that_beats_never_trips(rig):
    d = rig.dog()
    d.start(stop_requested=lambda: False, write_row=rig.row)
    end = time.monotonic() + 1.0
    while time.monotonic() < end:
        d.progress()
        time.sleep(0.02)
    d.run_ended()  # library mode: it stops
    assert not rig.calls and d.tripped is None
    assert not d._thread.is_alive()


def test_a_stop_honoured_inside_the_grace_never_trips(rig):
    asked = threading.Event()
    d = rig.dog(wedge_s=60)
    d.start(stop_requested=asked.is_set, write_row=rig.row)
    asked.set()
    time.sleep(0.15)  # well inside the 0.3 s grace
    d.run_ended()
    time.sleep(0.4)
    assert not rig.calls


def test_a_stop_not_honoured_trips_stop_ignored_even_while_frames_go_on(rig):
    asked = threading.Event()
    d = rig.dog(wedge_s=60)
    d.start(stop_requested=asked.is_set, write_row=rig.row)
    asked.set()
    end = time.monotonic() + 3
    while not rig.exited.is_set() and time.monotonic() < end:
        d.progress()  # the frames go on; the stop is still not honoured
        time.sleep(0.02)
    assert rig.exited.is_set() and rig.calls[1][1]["why"] == "stop-ignored"
    assert rig.calls[1][1]["waited_s"] >= 0.3


def test_armed_for_exit_a_process_still_alive_after_the_grace_trips_exit(rig):
    d = rig.dog(wedge_s=60, exit_grace_s=0.3)
    d.start(stop_requested=lambda: False, write_row=rig.row)
    d.progress()
    d.run_ended()
    assert rig.exited.wait(3)
    assert rig.calls[1][1]["why"] == "exit"


def test_without_exit_grace_the_watchdog_stops_when_the_capture_ends(rig):
    d = rig.dog()
    d.start(stop_requested=lambda: False, write_row=rig.row)
    d.run_ended()
    time.sleep(0.6)  # twice wedge_s
    assert not rig.calls


def test_a_row_writer_that_blocks_never_holds_up_the_exit(rig):
    forever = threading.Event()
    d = rig.dog(row_wait_s=0.2)
    d.start(stop_requested=lambda: False, write_row=lambda fields: forever.wait())
    t = time.monotonic()
    assert rig.exited.wait(3)
    assert time.monotonic() - t < 0.3 + 0.2 + 0.5
    forever.set()


def test_a_held_heard_lock_skips_the_row_without_waiting_on_it(rig, tmp_path):
    fd = os.open(str(tmp_path / "heard.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        assert heard_lock_busy(tmp_path)
        d = rig.dog()
        d.start(stop_requested=lambda: False, write_row=rig.row, lock_dir=tmp_path)
        assert rig.exited.wait(3)
        assert "row" not in rig.names() and rig.names()[-1] == "exit"
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    assert not heard_lock_busy(tmp_path)  # the probe let go of it
    assert not heard_lock_busy(None)


def test_a_failing_stop_check_and_a_gone_child_do_not_stop_the_exit(rig):
    def bad_stop():
        raise RuntimeError("stop check broke")

    def gone(pid, sig):
        rig.calls.append(("kill", pid, sig))
        raise ProcessLookupError

    d = rig.dog()
    d._kill = gone
    d.start(stop_requested=bad_stop, write_row=rig.row, children=lambda: [1, 2])
    assert rig.exited.wait(3)
    assert [c for c in rig.calls if c[0] == "kill"] == [("kill", 1, signal.SIGKILL), ("kill", 2, signal.SIGKILL)]


def test_the_values_and_why(rig):
    # WEDGE_S above the longest bounded wait at one end of turn (do-002: ~40-50 s) and below
    # the keeper's 120 s beat-stale; STOP_GRACE_S below the keeper's 15 s STOP-to-TERM wait.
    from ears.listen import loop

    assert 2 * loop.STT_WAIT_S + loop.REDECODE_WAIT_S + 10 < wd_mod.WEDGE_S < 120 - 15
    assert 0 < wd_mod.STOP_GRACE_S < 15
    assert time.get_clock_info("monotonic").monotonic
    d = Watchdog()
    assert (d.wedge_s, d.stop_grace_s, d.exit_grace_s, d._exit) == (wd_mod.WEDGE_S, wd_mod.STOP_GRACE_S, None, os._exit)


# -- in the capture -----------------------------------------------------------------------


def test_capture_stamps_progress_and_stops_its_watchdog_on_return(rig, tmp_path):
    d = rig.dog(wedge_s=5)
    stamps = []
    real = d.progress
    d.progress = lambda: (stamps.append(1), real())
    source = CountingSource(synth([("silence", 0.5), ("speech", 1.0)]), tail_silence_s=3.0)
    result = capture(source, FakeSTT(), RecordingSink(tmp_path / "logs", source), detector=energy_detector(),
                     heartbeat=1.0, stt_workers=1, watchdog=d)
    assert result.reason == "eof"
    assert len(stamps) == source.yielded > 0  # once a frame
    assert not d._thread.is_alive() and not rig.calls
    assert not [ln for ln in read_lines(tmp_path / "logs") if ln.get("event") == "capture-wedged"]


def test_the_watchdog_writes_its_row_through_the_capture_and_kills_the_streaming_child(rig, tmp_path):
    """A frame loop that stops (a source whose next frame never comes): the row lands in the
    heard log, with the capture's fields, and the child's pid is the one killed."""
    hold = threading.Event()

    class Child:
        pid = 31337

        def poll(self):
            return None

    class Stream:
        name = "fake-stream"

        class b:
            p = Child()

        def feed(self, frame, t):
            pass

        def drain(self):
            return []

        def finish(self):
            return []

        def close(self):
            pass

    class Parts:
        stream = Stream()
        live = False

        def want(self):
            return "nemotron"

        def sync(self, t):
            return None

        def failed(self, t):
            return None

        def close(self):
            pass

    class StuckSource(CountingSource):
        def frames(self):
            for i, f in enumerate(super().frames()):
                if i == 20:
                    hold.wait(5)
                yield f

    d = rig.dog(wedge_s=0.3)
    source = StuckSource(synth([("silence", 2.0)]))
    out = {}
    th = threading.Thread(target=lambda: out.setdefault("r", capture(
        source, FakeSTT(), RecordingSink(tmp_path / "logs", source), detector=energy_detector(), heartbeat=0,
        stt_workers=0, watchdog=d, partials=Parts())), daemon=True)
    th.start()
    try:
        assert rig.exited.wait(5)
        assert ("kill", 31337, signal.SIGKILL) in rig.calls
        rows = [ln for ln in read_lines(tmp_path / "logs") if ln.get("event") == "capture-wedged"]
        assert len(rows) == 1 and rows[0]["why"] == "wedge" and rows[0]["source"] == "file"
        assert th.is_alive()  # the fake exit returned; the frame loop is still stuck
    finally:
        hold.set()
        th.join(5)


def test_capture_has_no_watchdog_unless_given_one():
    import inspect

    assert inspect.signature(capture).parameters["watchdog"].default is None


@pytest.mark.parametrize("own, armed", [(None, False), (False, False), (True, True)])
def test_the_cli_arms_a_watchdog_only_when_it_owns_the_process(monkeypatch, tmp_path, own, armed):
    seen = {}

    def fake_capture(*a, **kw):
        seen.update(kw)
        return CaptureResult(reason="eof", text="", cursor=0)

    monkeypatch.setattr(cli, "capture", fake_capture)
    wav = tmp_path / "x.wav"
    import wave

    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0\0" * 1600)
    args = ["capture", "--source", f"file:{wav}", "--no-pace", "--log-dir", str(tmp_path / "logs")]
    kw = {} if own is None else {"own_process": own}
    assert cli.main(args, **kw) == 0
    dog = seen["watchdog"]
    if armed:
        assert isinstance(dog, Watchdog) and dog.exit_grace_s == wd_mod.STOP_GRACE_S and dog._thread is None
    else:
        assert dog is None


def test_ears_capture_in_process_is_not_its_own_process(monkeypatch):
    from ears import cli as ears_cli

    got = {}
    monkeypatch.setattr("ears.listen.__main__.main", lambda argv, own_process=None: got.update(own=own_process) or 0)
    assert ears_cli.main(["capture", "--source", "file:x.wav"]) == 0
    assert got["own"] is False


# -- the trip flushes stdout before os._exit, and never waits on it (EARS-359 do-004) ------


class _Stdout:
    """A stand-in for ``sys.stdout``: notes each flush in the rig; ``block`` makes it hang."""

    def __init__(self, rig, block: threading.Event | None = None):
        self.rig, self.block = rig, block

    def write(self, s):
        return len(s)

    def flush(self):
        self.rig.calls.append(("flush-stdout",))
        if self.block is not None:
            self.block.wait()


def test_a_trip_flushes_stdout_before_it_exits(rig, monkeypatch):
    monkeypatch.setattr("sys.stdout", _Stdout(rig))
    d = rig.dog()
    d.start(stop_requested=lambda: False, children=lambda: [4242])
    assert rig.exited.wait(3)
    names = rig.names()
    assert names.index("kill") < names.index("flush-stdout") < names.index("exit") == len(names) - 1


def test_a_stdout_that_blocks_never_holds_up_the_exit(rig, monkeypatch):
    never = threading.Event()
    monkeypatch.setattr("sys.stdout", _Stdout(rig, block=never))
    monkeypatch.setattr(wd_mod, "FLUSH_WAIT_S", 0.2)
    try:
        d = rig.dog()
        d.start(stop_requested=lambda: False)
        assert rig.exited.wait(3), "the watchdog waited on a stdout that never flushes"
        assert rig.names()[-2:] == ["flush-stdout", "exit"]
    finally:
        never.set()
