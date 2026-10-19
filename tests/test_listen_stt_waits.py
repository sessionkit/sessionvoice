"""The frame loop's waits on the STT are bounded (EARS-359 do-002).

Every heartbeat, STOP check and effect of SIGTERM runs in the frame loop, so a wait
there that never returns is a wedge: no beat, STOP ignored. These are EARS-344's repro
cases (``redecode-hang``, ``chunk-hang``, ``stop-drain-hang``) as tests, plus a turn
under streaming partials: an STT call that never answers costs a bounded wait, the turn
lands with its joined text, the beats go on, and STOP ends the capture with
``listen-stopped``.

Each capture runs on a thread the test joins with a timeout, so the code before the
bound fails the test instead of hanging the suite. A hung fake call waits on an Event
the fixture sets at teardown, so no thread outlives its test. Synthetic audio, a fake
STT, no device, no server, no sound.
"""

from __future__ import annotations

import threading
from concurrent.futures import Future

import pytest

from ears.listen import capture
from ears.listen.sources import FRAME_S
from ears.listen import loop as loop_mod
from ears.listen.streaming import Partials, Streamer
from tests.listen_helpers import CountingSource, RecordingSink, energy_detector, read_lines, synth
from tests.test_listen_streaming import SCRIPT, SPEECH, FakeChild
from tests.test_listen_streaming import ev as stream_ev

WAIT_S = 0.3        # both bounds, in these tests
HEARTBEAT_S = 0.2
JOIN_S = 10.0       # a capture that is not back by then is wedged


class HangingSTT:
    """Numbers its calls. A chunk call in ``hang_chunks`` (or every one, ``"all"``) and a
    final call in ``hang_finals`` block until ``release``, then answer ``late``."""

    name = "hanging"

    def __init__(self, release: threading.Event, hang_chunks=(), hang_finals=(), raise_on=()):
        self.release = release
        self.hang_chunks, self.hang_finals, self.raise_on = hang_chunks, hang_finals, set(raise_on)
        self.chunks = 0
        self.finals = 0
        self.hung = threading.Event()   # set when any call starts to block
        self._lock = threading.Lock()

    def _hang(self) -> str:
        self.hung.set()
        self.release.wait()
        return "late"

    def transcribe(self, audio):
        with self._lock:
            self.chunks += 1
            n = self.chunks
        if n in self.raise_on:
            raise TimeoutError("the server's own timeout")
        if self.hang_chunks == "all" or n in self.hang_chunks:
            return self._hang()
        return f"w{n}"

    def transcribe_final(self, audio):
        with self._lock:
            self.finals += 1
            n = self.finals
        if self.hang_finals == "all" or n in self.hang_finals:
            return self._hang()
        return f"final{n}"


class Tap(RecordingSink):
    """Keeps each line it writes, so a stop rule can read the log as it grows."""

    def __init__(self, directory, source):
        super().__init__(directory, source)
        self.lines: list[dict] = []

    def write(self, line):
        seq = super().write(line)
        self.lines.append({**line, "seq": seq})
        return seq


def beats_after_last_turn(lines) -> int:
    turns = [ln["seq"] for ln in lines if ln["kind"] == "turn"]
    if not turns:
        return 0
    return sum(1 for ln in lines if ln.get("event") == "heartbeat" and ln["seq"] > turns[-1])


@pytest.fixture
def release():
    ev = threading.Event()
    yield ev
    ev.set()  # every hung fake call returns; its abandoned worker thread ends


class GatedSource(CountingSource):
    """Holds frame ``hold_at`` back until ``until`` is set (at most ``wait_s``), so a test can
    order the frame loop against an STT worker. ``opened`` says whether the gate opened."""

    def __init__(self, samples, *, hold_at: int, until: threading.Event, wait_s: float = 5.0, **kw):
        super().__init__(samples, **kw)
        self.hold_at, self.until, self.wait_s = hold_at, until, wait_s
        self.opened = False

    def frames(self):
        for n, frame in enumerate(super().frames()):
            if n == self.hold_at:
                self.opened = self.until.wait(self.wait_s)
            yield frame


def run(tmp_path, segments, stt, *, stop=None, tail=30.0, silence=2.0, tap=None, source=None, **kw):
    """The capture on a thread; by default STOP once three beats follow the last turn.
    ``tap``, a list, is the sink's own list of lines, for a stop rule of the caller's.
    ``source`` replaces the plain one made from ``segments``."""
    source = source or CountingSource(synth(segments), tail_silence_s=tail)
    sink = Tap(tmp_path / "logs", source)
    if tap is not None:
        sink.lines = tap
    stop = stop or (lambda: beats_after_last_turn(sink.lines) >= 3)
    kw.setdefault("stt_workers", 1)
    out: dict = {}

    def go():
        out["result"] = capture(source, stt, sink, detector=energy_detector(silence), heartbeat=HEARTBEAT_S,
                                stop=stop, stt_wait_s=WAIT_S, redecode_wait_s=WAIT_S, **kw)

    th = threading.Thread(target=go, daemon=True, name="test-capture")
    th.start()
    th.join(JOIN_S)
    lines = read_lines(tmp_path / "logs")
    assert not th.is_alive(), (f"the capture wedged: no return in {JOIN_S}s; last lines: "
                               f"{[ln.get('event', ln['kind']) for ln in lines[-5:]]}")
    return out["result"], lines


def kind(lines, k):
    return [ln for ln in lines if ln["kind"] == k]


def events(lines, name):
    return [ln for ln in lines if ln.get("event") == name]


def stopped(lines):
    (row,) = events(lines, "listen-stopped")
    return row


# -- redecode-hang: the re-decode never answers (loop.py _redecoded_text) -------------------


def test_a_redecode_that_never_answers_keeps_the_joined_text_and_stop_ends_it(tmp_path, release):
    stt = HangingSTT(release, hang_finals="all")
    result, lines = run(tmp_path, [("silence", 0.5), ("speech", 3.0)], stt, turn_text="redecode")
    (turn,) = kind(lines, "turn")
    assert turn["redecode"] == "timeout" and turn["text_from"] == "joined"
    assert turn["text"] == " ".join(p["text"] for p in kind(lines, "partial"))
    assert turn["redecode_wait_s"] >= WAIT_S and turn["speculative"] is True
    assert beats_after_last_turn(lines) >= 3
    assert (result.reason, stopped(lines)["reason"]) == ("stop", "stop")
    assert stt.hung.is_set()


def test_after_a_redecode_timeout_the_next_turn_decodes_on_a_fresh_worker(tmp_path, release):
    stt = HangingSTT(release, hang_finals={1})
    segs = [("silence", 0.5), ("speech", 3.0), ("silence", 3.0), ("speech", 3.0)]
    sink_lines: list = []

    def two_turns_then_beats():
        return len(kind(sink_lines, "turn")) >= 2 and beats_after_last_turn(sink_lines) >= 3

    result, lines = run(tmp_path, segs, stt, turn_text="redecode", stop=two_turns_then_beats, tap=sink_lines)
    first, second = kind(lines, "turn")
    assert first["redecode"] == "timeout"
    assert second["text_from"] == "redecode" and second["text"].startswith("final")
    assert not events(lines, "stt-error")  # chunks queued behind the stuck call moved, none lost
    assert result.reason == "stop"


# -- chunk-hang: a chunk's STT never answers (loop.py _collect at the end of a turn) --------


def test_a_chunk_that_never_answers_is_an_stt_error_timeout_and_the_turn_lands(tmp_path, release):
    stt = HangingSTT(release, hang_chunks={1})
    result, lines = run(tmp_path, [("silence", 0.5), ("speech", 5.0)], stt)
    (err,) = events(lines, "stt-error")
    assert err["detail"] == "timeout" and err["wait_s"] == WAIT_S
    (turn,) = kind(lines, "turn")
    # the chunks that queued behind the stuck call were moved to a fresh worker and heard
    assert turn["text"] and "late" not in turn["text"]
    assert turn["text"] == " ".join(p["text"] for p in kind(lines, "partial"))
    assert beats_after_last_turn(lines) >= 3
    assert (result.reason, stopped(lines)["reason"]) == ("stop", "stop")


def test_a_dead_stt_still_ends_the_capture_with_error(tmp_path, release):
    stt = HangingSTT(release, hang_chunks="all")
    result, lines = run(tmp_path, [("silence", 0.5), ("speech", 8.0)], stt, stop=lambda: False)
    errs = events(lines, "stt-error")
    assert [e["detail"] for e in errs] == ["timeout"] * 3
    assert result.reason == "error" and "STT failed 3 times in a row: timeout" in result.error
    assert stopped(lines)["reason"] == "error"


def test_the_stts_own_timeout_error_is_an_ordinary_stt_error(tmp_path, release):
    stt = HangingSTT(release, raise_on={1})
    result, lines = run(tmp_path, [("silence", 0.5), ("speech", 5.0)], stt)
    (err,) = events(lines, "stt-error")
    assert err["detail"] == "the server's own timeout" and "wait_s" not in err
    assert result.reason == "stop"


# -- stop-drain-hang: STOP while a chunk hangs (loop.py _collect after the loop) ------------


def test_stop_while_a_chunk_hangs_drains_in_bounded_time_and_writes_listen_stopped(tmp_path, release):
    stt = HangingSTT(release, hang_chunks={1})
    result, lines = run(tmp_path, [("silence", 0.5), ("speech", 6.0)], stt, stop=stt.hung.is_set)
    assert (result.reason, stopped(lines)["reason"]) == ("stop", "stop")
    (err,) = events(lines, "stt-error")
    assert err["detail"] == "timeout"
    # the turn the STOP cut short still lands, with what was heard
    (turn,) = kind(lines, "turn")
    assert turn["text"]


# -- streaming partials: the final engine's re-decode hangs ---------------------------------


def test_a_streaming_turn_whose_redecode_never_answers_lands_with_the_streams_words(tmp_path, release):
    stt = HangingSTT(release, hang_finals="all")
    parts = Partials("nemotron", make=lambda: Streamer(FakeChild(SCRIPT)))
    result, lines = run(tmp_path, SPEECH, stt, turn_text="redecode", partials=parts)
    (turn,) = kind(lines, "turn")
    assert turn["redecode"] == "timeout" and turn["text_from"] == "joined"
    assert turn["text"] == "hello world how are you"
    assert stt.chunks == 0  # the stream wrote the partials; no chunk went to the STT
    assert beats_after_last_turn(lines) >= 3
    assert (result.reason, stopped(lines)["reason"]) == ("stop", "stop")


def test_the_bounds_default_to_the_module_constants():
    import inspect

    sig = inspect.signature(capture)
    assert sig.parameters["stt_wait_s"].default == loop_mod.STT_WAIT_S
    assert sig.parameters["redecode_wait_s"].default == loop_mod.REDECODE_WAIT_S
    assert 0 < loop_mod.STT_WAIT_S <= loop_mod.REDECODE_WAIT_S


# -- the fresh pool: each half of the executor replacement, on its own -----------------------
# The two turns above pass if EITHER half works: a chunk that queues behind a stuck
# re-decode is rescued by _collect's own swap. These pin each half alone.


SCRIPT_TWO_TURNS = SCRIPT + [
    (7.0, stream_ev("partial", "second tur", 2, 6.6, 7.0)),
    (8.0, stream_ev("partial", "second turn here", 2, 6.6, 8.0)),
    (10.5, stream_ev("final", "second turn here", 2, 6.6, 9.5)),
]


def test_under_streaming_partials_a_redecode_timeout_frees_the_next_turns_redecode(tmp_path, release):
    """No chunk goes to the STT under streaming partials, so only the re-decode's own
    swap keeps the next turn's re-decode from queueing behind the stuck one."""
    stt = HangingSTT(release, hang_finals={1})
    parts = Partials("nemotron", make=lambda: Streamer(FakeChild(SCRIPT_TWO_TURNS)))
    sink_lines: list = []

    def two_turns_then_beats():
        return len(kind(sink_lines, "turn")) >= 2 and beats_after_last_turn(sink_lines) >= 3

    segs = SPEECH + [("silence", 3.0), ("speech", 3.0)]
    result, lines = run(tmp_path, segs, stt, turn_text="redecode", partials=parts,
                        stop=two_turns_then_beats, tap=sink_lines)
    first, second = kind(lines, "turn")
    assert first["redecode"] == "timeout"
    assert second["text_from"] == "redecode" and second["text"] == "final2"
    assert stt.chunks == 0
    assert result.reason == "stop"


def test_a_chunk_queued_behind_a_stuck_speculative_redecode_is_waited_for_again_not_lost(tmp_path, release):
    """A pause mid-turn launches the speculative re-decode, which hangs; speech resumes and
    the next chunk queues behind it on the one worker. That chunk never started, so it is
    moved to the fresh pool and heard, not counted as a timeout.

    The source holds the frame where speech resumes until the speculative call is hung
    (EARS-359 do-006). Unheld, the loop could reach it first: the queued speculative was
    then cancelled before it started, the hang moved to the turn's end, and the test failed
    1 run in 20 on an order the code handles either way."""
    stt = HangingSTT(release, hang_finals={1})
    segs = [("silence", 0.5), ("speech", 2.0), ("silence", 0.8), ("speech", 2.0)]
    resume = int(round(3.3 / FRAME_S))  # the first frame of the second speech
    source = GatedSource(synth(segs), hold_at=resume, until=stt.hung, tail_silence_s=30.0)
    result, lines = run(tmp_path, segs, stt, turn_text="redecode", source=source)
    assert source.opened, "the speculative re-decode never started before speech resumed"
    assert not events(lines, "stt-error")
    (turn,) = kind(lines, "turn")
    assert turn["text_from"] == "redecode" and turn["text"] == "final2"  # launched again, on the fresh pool
    assert stt.finals == 2 and stt.hung.is_set()
    assert result.reason == "stop"


# -- a call that ends as the deadline passes is an answer, not an error ----------------------


class _GapFuture(Future):
    """Finished, yet a timed ``result()`` raises ``TimeoutError``: what a reader sees when
    the call ends in the gap between the wait's deadline and a ``done()`` check."""

    def result(self, timeout=None):
        if timeout is not None:
            raise TimeoutError()
        return super().result()


class _GapExecutor:
    """Runs each call at once and hands back a :class:`_GapFuture`."""

    def submit(self, fn, *args):
        fut = _GapFuture()
        try:
            fut.set_result(fn(*args))
        except Exception as exc:
            fut.set_exception(exc)
        return fut

    def shutdown(self, wait=True, cancel_futures=False):
        pass


def test_a_call_that_ends_as_the_wait_times_out_is_read_as_its_answer(tmp_path, release, monkeypatch):
    monkeypatch.setattr(loop_mod._Capture, "_new_executor", lambda self: _GapExecutor())
    stt = HangingSTT(release)
    result, lines = run(tmp_path, [("silence", 0.5), ("speech", 3.0)], stt, turn_text="redecode")
    assert not events(lines, "stt-error")
    assert [p["text"] for p in kind(lines, "partial")] == [f"w{n}" for n in range(1, stt.chunks + 1)]
    (turn,) = kind(lines, "turn")
    assert turn["text_from"] == "redecode" and turn["text"].startswith("final")
    assert result.reason == "stop"
