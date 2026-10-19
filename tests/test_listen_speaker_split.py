"""EARS-14: a speaker change ends the turn: when the speaker's name changes, the
turn of the one before ends, for any speaker, not only the user. The change had
three conditions: the capture never
breaks on the side-car's file; no split on a single label (K in a row); replay evidence.
Synthetic audio, a fake STT (w1, w2, ... one per burst), labels by partial text."""
from __future__ import annotations

import json

import pytest

from ears.listen import capture
from ears.listen.__main__ import build_parser
from ears.listen.speakers import LabelTail, first_cut
from tests.listen_helpers import CountingSource, FakeSTT, RecordingSink, energy_detector, read_lines, synth
from tests.test_listen_redecode import DurationSTT, dur


def bursts(n, speech=1.0, gap=0.6):
    """N bursts of speech in ONE turn: each gap cuts a chunk (quiet 0.35 s) and so a
    partial, and is shorter than the 2 s end of turn."""
    segs = [("silence", 0.5)]
    for i in range(n):
        segs += [("speech", speech)] + ([("silence", gap)] if i < n - 1 else [])
    return segs


class TextLabels:
    """labels(seqs) by each partial's text (w1, w2, ...), read back from the heard log."""
    def __init__(self, logs, by_text, boom=False):
        self.logs, self.by_text, self.boom, self.calls = logs, by_text, boom, 0
    def labels(self, seqs):
        self.calls += 1
        if self.boom:
            raise ValueError("garbage in the speakers file")
        text = {ln["seq"]: ln.get("text") for ln in read_lines(self.logs)}
        return [self.by_text.get(text.get(s)) for s in seqs]


def run(tmp_path, n, by_text=None, *, k=2, labels=None, stt=None, **kw):
    logs = tmp_path / "logs"
    source = CountingSource(synth(bursts(n)), tail_silence_s=3.0)
    sink = RecordingSink(logs, source)
    lab = labels if labels is not None else (TextLabels(logs, by_text) if by_text is not None else None)
    capture(source, stt or FakeSTT(), sink, detector=energy_detector(), heartbeat=0, stt_workers=0,
            speaker_split=k, speaker_labels=lab, **kw)
    return [ln for ln in read_lines(logs) if ln["kind"] == "turn"], read_lines(logs), lab


def said(turns):
    return [(t["text"], t.get("speaker")) for t in turns]


# -- the rule ------------------------------------------------------------------

@pytest.mark.parametrize("labels,k,want", [
    (["alice", "alice", "theo", "theo"], 2, (2, "alice", "theo")),
    (["alice", "theo", "theo", "theo"], 2, None),                     # a stray first label
    (["theo", "theo", "alice", "theo", "theo"], 2, None),             # a stray label mid-turn
    (["theo", "theo", "mixed", "other", "silent", "unheard", None, "theo"], 2, None),
    (["vera", "vera", "other", "theo", None, "theo"], 2, (3, "vera", "theo")),  # neutrals skipped
    (["alice", "theo"], 1, (1, "alice", "theo")),
    (["alice", "alice", "theo", "theo"], 3, None),
    (["alice", "alice", "alice", "theo", "theo", "theo"], 3, (3, "alice", "theo")),
    ([], 2, None), (["theo"], 2, None), (["alice", "alice", "theo", "theo"], 0, None),
])
def test_first_cut(labels, k, want):
    assert first_cut(labels, k) == want


# -- the capture -----------------------------------------------------------------

def test_alice_then_theo_with_no_pause_is_two_turns(tmp_path):
    turns, lines, _ = run(tmp_path, 4, {"w1": "alice", "w2": "alice", "w3": "theo", "w4": "theo"})
    assert said(turns) == [("w1 w2", "alice"), ("w3 w4", "theo")]
    assert turns[0]["split"] == "speaker-change" and turns[1]["opened_by"] == "speaker-change"
    parts = {ln["text"]: ln for ln in lines if ln["kind"] == "partial"}
    assert turns[0]["t1"] == parts["w2"]["t1"] and turns[1]["t0"] == parts["w3"]["t0"]
    assert len([ln for ln in lines if ln["kind"] == "partial"]) == 4   # partials untouched


def test_three_speakers_in_a_group_each_get_their_own_turn(tmp_path):
    turns, *_ = run(tmp_path, 6, {"w1": "alice", "w2": "alice", "w3": "theo", "w4": "theo",
                                  "w5": "dave", "w6": "dave"})
    assert said(turns) == [("w1 w2", "alice"), ("w3 w4", "theo"), ("w5 w6", "dave")]


def test_one_speakers_turn_is_never_split(tmp_path):
    # EARS-106: never split, but named by its labels (EARS-14 alone left it with no speaker)
    turns, *_ = run(tmp_path, 4, {"w1": "theo", "w2": "theo", "w3": "theo", "w4": "theo"})
    assert said(turns) == [("w1 w2 w3 w4", "theo")]
    assert "split" not in turns[0] and "opened_by" not in turns[0]
    assert turns[0]["speaker_from"] == "labels"


@pytest.mark.parametrize("by_text,who", [
    ({"w1": "alice", "w2": "theo", "w3": "theo", "w4": "theo"}, "theo"),     # one stray label
    ({"w1": "theo", "w2": "theo", "w3": "mixed", "w4": "other"}, "theo"),  # neutrals
    ({}, None),                                                            # no labels yet
])
def test_no_split_without_k_named_labels_in_a_row(tmp_path, by_text, who):
    turns, *_ = run(tmp_path, 4, by_text)
    assert said(turns) == [("w1 w2 w3 w4", who)]          # one turn; EARS-106 names it
    assert "split" not in turns[0] and "opened_by" not in turns[0]


def test_off_or_no_side_car_is_todays_turns_exactly(tmp_path):
    def strip(lines):
        return [{k: v for k, v in ln.items() if k not in ("ts", "wall_s")} for ln in lines]
    base = strip(run(tmp_path / "a", 4, k=0)[1])
    assert strip(run(tmp_path / "b", 4, k=2)[1]) == base                  # no labels object
    assert strip(run(tmp_path / "c", 4, {}, k=2)[1]) == base              # labels, all None
    assert strip(run(tmp_path / "d", 4, {"w1": "alice", "w2": "alice", "w3": "theo", "w4": "theo"}, k=0)[1]) == base


def test_a_broken_label_reader_never_breaks_the_capture(tmp_path):
    logs = tmp_path / "logs"
    lab = TextLabels(logs, {}, boom=True)
    turns, lines, _ = run(tmp_path, 4, labels=lab)
    assert said(turns) == [("w1 w2 w3 w4", None)]
    errs = [ln for ln in lines if ln.get("event") == "speaker-split-error"]
    assert len(errs) == 1 and "garbage" in errs[0]["detail"] and lab.calls > 1


def test_a_garbage_speakers_file_never_breaks_the_capture(tmp_path):
    logs = tmp_path / "logs"
    sp = tmp_path / "speakers.jsonl"
    sp.write_bytes(b'not json\n{"seq": "x", "speaker": "alice"}\n[1,2]\n{"seq": 1}\n\x00\xff\n{"seq": 3, "spea')
    turns, lines, _ = run(tmp_path, 4, labels=LabelTail(lambda: sp))
    assert said(turns) == [("w1 w2 w3 w4", None)]
    assert not [ln for ln in lines if ln.get("event") == "speaker-split-error"]


def test_redecode_gives_each_half_its_own_audio(tmp_path):
    """The re-decode of each half hears only that half: DurationSTT names the audio by its
    length, so the halves' lengths add up to about the whole turn's, and each is about half."""
    logs = tmp_path / "logs"
    order = ["alice", "alice", "theo", "theo"]

    class Ordered:
        def labels(self, seqs):
            parts = [ln["seq"] for ln in read_lines(logs) if ln["kind"] == "partial"]
            return [order[parts.index(s)] if s in parts else None for s in seqs]
    turns, *_ = run(tmp_path, 4, labels=Ordered(), stt=DurationSTT(), turn_text="redecode")
    whole, *_ = run(tmp_path / "whole", 4, k=0, stt=DurationSTT(), turn_text="redecode")
    assert [t["speaker"] for t in turns] == ["alice", "theo"]
    assert [t["text_from"] for t in turns] == ["redecode", "redecode"]
    one, two, all_ = dur(turns[0]["text"]), dur(turns[1]["text"]), dur(whole[0]["text"])
    assert one + two == pytest.approx(all_, abs=0.05)
    assert 2.6 < one < 4.0 and 2.6 < two < 4.0      # two bursts (and a gap) each


# -- the label file -----------------------------------------------------------------

def test_label_tail_reads_whole_lines_only_and_follows_the_day(tmp_path):
    a, b = tmp_path / "speakers_a.jsonl", tmp_path / "speakers_b.jsonl"
    cur = [a]
    tail = LabelTail(lambda: cur[0])
    assert tail.labels([1]) == [None]                       # no file yet
    a.write_text('{"seq": 1, "speaker": "alice"}\n{"seq": 2, "spea')
    assert tail.labels([1, 2]) == ["alice", None]             # the half line waits
    with open(a, "a") as f:
        f.write('ker": "theo"}\n')
    assert tail.labels([1, 2]) == ["alice", "theo"]
    a.write_text('{"seq": 9, "speaker": "dave"}\n')         # rewritten shorter
    assert tail.labels([9]) == ["dave"]
    b.write_text('{"seq": 10, "speaker": "vera"}\n')
    cur[0] = b                                              # midnight
    assert tail.labels([9, 10]) == ["dave", "vera"]


def test_label_tail_is_bounded(tmp_path):
    p = tmp_path / "speakers.jsonl"
    p.write_text("".join(json.dumps({"seq": i, "speaker": "theo"}) + "\n" for i in range(50)))
    tail = LabelTail(lambda: p, keep=10)
    assert tail.labels([0, 39, 40, 49]) == [None, None, "theo", "theo"]


def test_the_cli_splits_at_two_by_default_and_zero_turns_it_off():
    p = build_parser()
    args = p.parse_args(["capture", "--source", "mic"])
    assert args.speaker_split == 2
    assert p.parse_args(["capture", "--source", "mic", "--speaker-split", "0"]).speaker_split == 0
