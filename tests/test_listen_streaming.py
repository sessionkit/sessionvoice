"""Streaming partials: Nemotron's settled words, appended.
The backend is faked: a scripted child that says ready, sends partials and
finals at audio times, and answers commit. Synthetic audio, fake STT, no device, no model.
"""

from __future__ import annotations

import json
import os

import pytest

from ears.events import Event, eof, error, ready
from ears.listen import capture
from ears.listen.__main__ import build_parser
from ears.listen.sources import FRAME_S, SAMPLE_RATE
from ears.listen.streaming import Partials, Settled, Streamer
from tests.listen_helpers import CountingSource, FakeSTT, RecordingSink, energy_detector, read_lines, synth


def ev(type_, text, seg, start, end):
    return Event(type_, text, start, end, seg=seg)


# -- the settled-word rule --------------------------------------------------------------


def test_a_partials_last_word_is_held_back_and_never_written_twice():
    s = Settled()
    assert s.take(ev("partial", "hello", 1, 0.0, 0.6)) is None            # one word: still growing
    assert s.take(ev("partial", "hello wor", 1, 0.0, 1.1)) == ("hello", 0.0, 1.1)
    assert s.take(ev("partial", "hello world", 1, 0.0, 1.7)) is None       # "world" may still grow
    assert s.take(ev("partial", "hello world, how are", 1, 0.0, 2.2)) == ("world, how", 1.1, 2.2)
    assert s.take(ev("final", "hello world, how are you", 1, 0.0, 3.0)) == ("are you", 2.2, 3.0)
    assert s.rewrites == 0


def test_a_new_segment_starts_over_and_a_final_with_nothing_new_is_nothing():
    s = Settled()
    assert s.take(ev("partial", "one two", 1, 0.0, 1.0)) == ("one", 0.0, 1.0)
    assert s.take(ev("final", "one two", 1, 0.0, 1.4)) == ("two", 1.0, 1.4)
    assert s.take(ev("final", "one two", 1, 0.0, 1.4)) is None
    assert s.take(ev("partial", "three four", 2, 2.0, 2.9)) == ("three", 2.0, 2.9)   # t0 = the segment's start


def test_a_word_the_backend_changes_after_it_was_written_stands_as_written():
    # Nemotron only adds tokens, so this never happens there; the rule is for a backend that does
    s = Settled()
    assert s.take(ev("partial", "Amery is here", 1, 0.0, 1.0)) == ("Amery is", 0.0, 1.0)
    assert s.take(ev("partial", "Ameri is here now", 1, 0.0, 1.6)) == ("here", 1.0, 1.6)   # not "Ameri is here"
    assert s.rewrites == 1
    assert s.take(ev("final", "Ameri is here now", 1, 0.0, 2.0)) == ("now", 1.6, 2.0)
    assert s.rewrites == 1   # counted once: what was written has moved on


def test_only_partials_and_finals_count():
    s = Settled()
    assert s.take(ready("x", "m", 16000, 0.1)) is None and s.take(eof(1.0)) is None


# -- a scripted child --------------------------------------------------------------------


class FakeChild:
    """A backend whose events come at audio times (seconds of PCM fed), like the child's.
    ``ready_at`` is on the stream clock (one ``events()`` call per frame): nothing is fed
    before ready, as in the loop."""

    name = "fake-stream"

    def __init__(self, script, *, ready_at=0.0, on_commit=None):
        self.script = sorted(script, key=lambda x: x[0])   # (audio_s, Event)
        self.fed = 0.0
        self.polls = 0
        self.ready_at = ready_at
        self.queued = []
        self.on_commit = on_commit
        self.commits = 0
        self.closed = False

    def feed(self, pcm):
        self.fed += len(pcm) / 2 / SAMPLE_RATE

    def commit(self):
        self.commits += 1
        if self.on_commit is not None:
            self.queued.append(self.on_commit)

    def events(self):
        self.polls += 1
        due = self.queued
        self.queued = []
        if self.ready_at is not None and self.polls * FRAME_S >= self.ready_at:
            due.append(ready("fake-stream", "m", 16000, 0.01, chunk_s=0.56))
            self.ready_at = None
        due += [e for t, e in self.script if t <= self.fed]
        self.script = [(t, e) for t, e in self.script if t > self.fed]
        return due

    def close(self):
        self.closed = True


def run(tmp_path, segments, *, child, stt=None, tail=3.0, mode="nemotron", **kw):
    source = CountingSource(synth(segments), tail_silence_s=tail)
    sink = RecordingSink(tmp_path / "logs", source)
    stt = stt or FakeSTT()
    parts = Partials(mode, make=lambda: Streamer(child))
    result = capture(source, stt, sink, detector=energy_detector(), heartbeat=0, stt_workers=0,
                     partials=parts, **kw)
    lines = read_lines(tmp_path / "logs")
    return result, lines, stt, parts


def kind(lines, k):
    return [ln for ln in lines if ln["kind"] == k]


SPEECH = [("silence", 0.5), ("speech", 3.0)]   # the turn opens near 0.6 s, speech ends at 3.5 s
SCRIPT = [
    (1.0, ev("partial", "hello wor", 1, 0.4, 1.0)),
    (1.5, ev("partial", "hello world how", 1, 0.4, 1.5)),
    (2.0, ev("partial", "hello world how are yo", 1, 0.4, 2.0)),
    (4.5, ev("final", "hello world how are you", 1, 0.4, 3.5)),   # the child's 1 s close
]


def test_partials_are_the_streams_settled_words_and_no_chunk_goes_to_the_stt(tmp_path):
    child = FakeChild(SCRIPT)
    _, lines, stt, _ = run(tmp_path, SPEECH, child=child)
    partials = kind(lines, "partial")
    assert [p["text"] for p in partials] == ["hello", "world", "how are", "you"]   # each partial holds its last word
    assert all(p["engine"] == "fake-stream" and p["final"] is False for p in partials)
    # spans tile the segment, in stream time (the child's clock is offset by the frames fed before ready)
    t0s, t1s = [p["t0"] for p in partials], [p["t1"] for p in partials]
    assert t0s[1:] == t1s[:-1] and t0s[0] < t1s[0] < t1s[1] < t1s[2] < t1s[3]
    assert stt.calls == 0                       # no per-chunk transcription while the stream is live
    (turn,) = kind(lines, "turn")
    assert turn["text"] == "hello world how are you"    # joined: the partials as written
    started = next(ln for ln in lines if ln.get("event") == "listen-started")
    assert started["partials"] == "nemotron"
    assert child.closed and child.commits == 0  # the segment closed on its own: nothing to commit


def test_the_turns_text_is_still_the_final_engines_redecode(tmp_path):
    child = FakeChild(SCRIPT)
    _, lines, stt, _ = run(tmp_path, SPEECH, child=child, stt=FakeSTT(["the kept line"]), turn_text="redecode")
    (turn,) = kind(lines, "turn")
    assert turn["text"] == "the kept line" and turn["text_from"] == "redecode"
    assert turn["joined"] == "hello world how are you"
    assert stt.calls == 1                       # the whole turn, once


def test_the_turns_end_commits_an_open_segment_and_takes_the_rest(tmp_path):
    script = SCRIPT[:3]                         # no final of its own: the child still hears speech
    child = FakeChild(script, on_commit=ev("final", "hello world how are you", 1, 0.4, 3.5))
    _, lines, _, _ = run(tmp_path, SPEECH, child=child)
    assert [p["text"] for p in kind(lines, "partial")] == ["hello", "world", "how are", "you"]
    assert child.commits == 1
    (turn,) = kind(lines, "turn")
    assert turn["text"] == "hello world how are you"


def test_a_child_that_never_answers_a_commit_costs_a_bounded_wait(tmp_path, monkeypatch):
    import ears.listen.streaming as streaming
    monkeypatch.setattr(streaming, "FINAL_WAIT_S", 0.05)
    child = FakeChild(SCRIPT[:3])               # commit answered by nothing
    _, lines, _, _ = run(tmp_path, SPEECH, child=child)
    assert [p["text"] for p in kind(lines, "partial")] == ["hello", "world", "how are"]
    (turn,) = kind(lines, "turn")
    assert turn["text"] == "hello world how are"   # what was settled; the held word is the re-decode's to find


def test_until_the_child_is_ready_the_chunks_are_the_partials_and_nothing_is_lost(tmp_path):
    # ready 1.6 s in, mid-turn: the chunk open at that moment is sent on, then the stream takes over
    script = [(1.0, ev("partial", "later words here", 1, 0.0, 1.0)), (3.0, ev("final", "later words here", 1, 0.0, 2.0))]
    child = FakeChild(script, ready_at=1.6)
    _, lines, stt, _ = run(tmp_path, SPEECH, child=child)
    partials = kind(lines, "partial")
    chunked = [p for p in partials if "engine" not in p]
    streamed = [p for p in partials if p.get("engine") == "fake-stream"]
    assert len(chunked) == 1 and streamed and stt.calls == 1
    assert partials[: len(chunked)] == chunked  # chunks first, then the stream
    assert [p["text"] for p in streamed] == ["later words", "here"]
    assert chunked[0]["t1"] < 2.0               # the open chunk was flushed as the stream went live, not at its 2 s max
    assert streamed[0]["t0"] >= chunked[0]["t1"] - FRAME_S
    (turn,) = kind(lines, "turn")
    assert turn["text"] == " ".join(p["text"] for p in partials)


def test_a_child_that_dies_is_said_once_and_the_chunks_carry_on(tmp_path):
    child = FakeChild([(1.0, error("nemotron: boom", fatal=True)), (1.0, eof(1.0))])
    _, lines, stt, parts = run(tmp_path, SPEECH + [("silence", 3.0), ("speech", 1.0)], child=child)
    errs = [ln for ln in lines if ln.get("event") == "partials-error"]
    assert len(errs) == 1 and "boom" in errs[0]["detail"]
    assert child.closed and parts.stream is None and parts.retry_at > 0
    partials = kind(lines, "partial")
    assert partials and all("engine" not in p for p in partials) and stt.calls == len(partials)
    assert len(kind(lines, "turn")) == 2        # the second turn did not try the child again (backoff)


def test_a_child_that_cannot_start_is_said_once_and_backed_off(tmp_path):
    made = []

    def make():
        made.append(1)
        raise RuntimeError("mlx-audio with Nemotron not importable")

    source = CountingSource(synth(SPEECH + [("silence", 3.0), ("speech", 1.0)]), tail_silence_s=3.0)
    sink = RecordingSink(tmp_path / "logs", source)
    capture(source, FakeSTT(), sink, detector=energy_detector(), heartbeat=0, stt_workers=0,
            partials=Partials("nemotron", make=make))
    lines = read_lines(tmp_path / "logs")
    notes = [ln for ln in lines if ln.get("event") == "partials"]
    assert len(made) == 1 and len(notes) == 1 and "not started" in notes[0]["detail"]
    assert len(kind(lines, "turn")) == 2 and all("engine" not in p for p in kind(lines, "partial"))


# -- following ears.json ----------------------------------------------------------------


def test_the_setting_is_followed_as_each_turn_opens(tmp_path, monkeypatch):
    conf = tmp_path / "ears.json"
    monkeypatch.setenv("EARS_CONF", str(conf))
    made = []
    parts = Partials(None, make=lambda: made.append(Streamer(FakeChild([]))) or made[-1])
    assert parts.want() == "final" and parts.sync(0.0) is None and parts.stream is None
    conf.write_text(json.dumps({"partials": "nemotron"}))
    assert "starting" in parts.sync(1.0) and parts.stream is made[0]
    assert parts.sync(2.0) is None              # already running
    conf.write_text(json.dumps({"partials": "final"}))
    assert "per chunk" in parts.sync(3.0) and parts.stream is None and made[0].b.closed


def test_a_pinned_mode_ignores_the_file(tmp_path, monkeypatch):
    conf = tmp_path / "ears.json"
    monkeypatch.setenv("EARS_CONF", str(conf))
    conf.write_text(json.dumps({"partials": "nemotron"}))
    parts = Partials("final", make=lambda: pytest.fail("must not start"))
    assert parts.sync(0.0) is None and parts.stream is None
    with pytest.raises(ValueError):
        Partials("parakeet")


def test_the_flag(tmp_path):
    p = build_parser()
    assert p.parse_args(["capture"]).partials is None
    assert p.parse_args(["capture", "--partials", "nemotron"]).partials == "nemotron"
    with pytest.raises(SystemExit):
        p.parse_args(["capture", "--partials", "whisper"])
