"""A person's one-word turn, right after a mouth line, is named the line's agent with no echo cut.

The case (EARS-469): the agent's line (``ivy``) ends, and a person answers with one word
that is also one of the line's words ("Anything." after "... say anything ..."). The
capture closes it as a one-partial turn. Nothing is cut as echo (ears-mail's echo span
needs 3 words), yet the turn carried ``speaker: ivy``, so its mail said the agent spoke
and the person's words woke nobody.

Two steps made that label:

1. the side-car's ``mouth_name`` renamed the partial's ``other`` voice label to the
   line's agent (``speaker_by: "mouth"``) when the partial fell in the line's window and
   at least half its words were the line's. One word that is the line's was "all of them".
   A line whose end was unknown (no ``said`` row seen) had no end, so it covered every
   later partial.
2. ``turn_speaker``'s fallback for a turn shorter than K partials names it by its one
   label. That stays: it is how a one-partial pure echo of the agent stays the agent.

The fix is in step 1: a partial mostly AFTER the line ended can only be its echo if its
words are the line's ENDING (an in-order run reaching the line's last word), and an
unended line ends at the next line's start, else at a bounded estimate.

What never reproduced, as passing tests: a side-car start or a capture start primes
nothing (the side-car's mouth map starts empty and skips lines from before its start; a
fresh LabelTail can only name a seq it has a row for; seqs never repeat), and a partial
that starts more than ``tail`` after the line's end is never renamed.

No model, no audio, no process: the side-car's functions are lifted by name (as
tests/test_speakers.py does), and every record and date is synthetic (2030-06-09).
"""
from __future__ import annotations

import ast
import datetime as dt
import json
import types
from pathlib import Path

import pytest

from ears.listen.speakers import Label, LabelTail, first_cut, read_turn, turn_speaker

SC = Path(__file__).resolve().parents[1] / "speakers" / "speaker-sidecar2.py"

AGENT = "ivy"     # the seat whose mouth line played
PERSON = "theo"   # an enrolled person
T0 = "2030-06-09T12:00:00"  # the line starts; no offset, so fromisoformat reads it on 3.10
LINE = "The listener is back on the new code. Say anything and I will check the label it gets."
LINE_S = 17.0     # the line plays 17 s
ONE_WORD = "Anything."  # the person's whole turn: one word, and it is one of the line's


def lift(*names, today=None):
    """The side-car's top-level functions by name, in a fresh namespace with its own
    ``mouth``/``starts``/``listen_t`` state. ``today``: what ``dt.date.today()`` says."""
    fake_dt = types.SimpleNamespace(datetime=dt.datetime,
                                    date=types.SimpleNamespace(today=lambda: today or dt.date(2030, 6, 9)))
    ns = {"dt": fake_dt, "Path": Path, "json": json, "mouth": {}, "starts": {}, "listen_t": {},
          "agent_refs": frozenset()}
    exec("def epoch(ts): return dt.datetime.fromisoformat(ts).timestamp()", ns)
    for node in ast.parse(SC.read_text()).body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            exec(compile(ast.Module([node], []), "sidecar", "exec"), ns)
    return ns


def at(s0, dt_s):
    return dt.datetime.fromtimestamp(s0 + dt_s).isoformat()


@pytest.fixture
def sc():
    ns = lift("words", "note_mouth", "mouth_name", "named_by_mouth", "note_start", "open_today")
    ns["s0"] = ns["epoch"](T0)
    return ns


def saying(sc):
    sc["note_mouth"]({"kind": "saying", "utt": "u1", "agent": AGENT, "ts": T0, "text": LINE})


def said(sc, end_s=LINE_S):
    sc["note_mouth"]({"kind": "said", "utt": "u1", "agent": AGENT, "ts": at(sc["s0"], end_s)})


def label(sc, text, w0_s, w1_s, who="other"):
    """What the side-car writes for one partial the voice matcher heard as ``who``:
    (speaker, speaker_by-mouth, mouth_utt)."""
    w0, w1 = sc["s0"] + w0_s, sc["s0"] + w1_s
    got, _fr, utt = sc["mouth_name"](who, {who: 1.0}, text, w0, w1, sc["mouth"], 1.5, frozenset())
    return got, sc["named_by_mouth"](who, utt, frozenset()), utt


# -- (b) the side-car's mouth rename --------------------------------------------------

def test_unended_line_does_not_rename_a_later_one_word_partial(sc):
    """b1: a line with no said row seen is bounded, so a one-word partial long after it
    keeps its voice label."""
    saying(sc)  # its said row never reached the side-car: s1 stays None
    got, by_mouth, _ = label(sc, ONE_WORD, LINE_S + 60.0, LINE_S + 61.0)
    assert (got, by_mouth) == ("other", False), "the person's voice label, not the agent's"


def test_one_word_inside_the_tail_after_the_end_is_not_renamed(sc):
    """b2: a word from the line's middle, inside the tail after the line ended, is not
    the line's ending, so it keeps its voice label."""
    saying(sc)
    said(sc)
    got, by_mouth, _ = label(sc, ONE_WORD, LINE_S + 1.0, LINE_S + 1.94)
    assert (got, by_mouth) == ("other", False)


def test_one_word_past_the_tail_is_not_renamed(sc):
    """Does NOT reproduce: the said row seen and the partial 2.6 s after the line's end
    (the gap the case measured) is outside the 1.5 s tail, so it keeps ``other``."""
    saying(sc)
    said(sc)
    assert label(sc, ONE_WORD, LINE_S + 2.6, LINE_S + 3.54)[:2] == ("other", False)


def test_a_word_not_in_the_line_is_not_renamed_even_unended(sc):
    """Does NOT reproduce: with no end known, a word the line does not have keeps ``other``."""
    saying(sc)
    assert label(sc, "Nope.", LINE_S + 60.0, LINE_S + 61.0)[:2] == ("other", False)


def test_a_person_label_is_never_renamed(sc):
    """Does NOT reproduce: a voice the matcher named (an enrolled person) keeps its name."""
    saying(sc)
    assert label(sc, ONE_WORD, LINE_S + 60.0, LINE_S + 61.0, who=PERSON)[:2] == (PERSON, False)


# -- (a) the capture's turn speaker -----------------------------------------------------

def test_one_partial_by_mouth_names_the_turn_the_agent():
    """Kept on purpose (EARS-469 RCA): a one-partial turn the side-car named by mouth is
    the agent's, so a one-partial pure echo of the agent's line stays the agent and never
    wakes as the user. The fix is upstream: such a label is no longer made for a person's
    answer after the line."""
    assert turn_speaker([AGENT], agent=[True], echo=[False]) == AGENT


def test_one_partial_by_voice_still_names_the_turn():
    """Does NOT reproduce: the short-turn fallback for a VOICE label (the reason it exists)."""
    assert turn_speaker([PERSON], agent=[False], echo=[False]) == PERSON


def test_two_partials_one_by_mouth_name_nobody():
    """Does NOT reproduce: at K=2 a turn of 2 partials needs 2 labels in a row; one is None."""
    assert turn_speaker([AGENT, None], agent=[True, False], echo=[False, False]) is None


def test_one_partial_is_never_cut():
    """No cut is possible on one partial: so no echo cut, as the case's mail showed."""
    assert first_cut([AGENT], agent=[True], echo=[False]) is None


# -- the chain, end to end: side-car row -> speakers file -> capture's turn speaker ------

def write_row(path, seq, got, by_mouth, utt):
    row = {"seq": seq, "kind": "partial", "speaker": got, "by": "speaker-sidecar2", "v": 2,
           **({"mouth_utt": utt} if utt else {}), **({"speaker_by": "mouth"} if by_mouth else {})}
    with open(path, "a") as f:
        f.write(json.dumps(row) + "\n")


@pytest.mark.parametrize("ended,w0_s", [
    pytest.param(False, LINE_S + 2.6, id="said-row-unseen"),
    pytest.param(True, LINE_S + 1.0, id="inside-tail"),
])
def test_one_word_turn_after_a_mouth_line_is_not_named_the_agent(sc, tmp_path, ended, w0_s):
    saying(sc)
    if ended:
        said(sc)
    speakers = tmp_path / "speakers_2030-06-09.jsonl"
    write_row(speakers, 101, *label(sc, ONE_WORD, w0_s, w0_s + 0.94))
    tail = LabelTail(lambda: speakers, lines=lambda utt: LINE if utt == "u1" else None)
    labels, guards = read_turn(tail, [101], [ONE_WORD])
    got = turn_speaker(labels, **guards)
    assert got != AGENT, f"the person's one-word turn was named {got!r}"


# -- (c) nothing is primed at a start -----------------------------------------------------

def test_sidecar_start_skips_mouth_lines_before_it(sc, tmp_path):
    """Does NOT reproduce: a side-car that starts after a line began replays only stream
    starts (open_today), so the line is not in its mouth map and its ``said`` row, read
    after the start, is ignored: a word of that line keeps ``other``."""
    log = tmp_path / "heard_2030-06-09.jsonl"
    log.write_text(json.dumps({"kind": "saying", "utt": "u1", "agent": AGENT, "ts": T0, "text": LINE,
                               "seq": 1}) + "\n")
    sc["a"] = types.SimpleNamespace(logdir=str(tmp_path), stop_file=None)
    day, f = sc["open_today"]()
    f.close()
    assert day == "2030-06-09" and sc["mouth"] == {}
    said(sc)  # the line ends after the side-car started
    assert sc["mouth"] == {}
    assert label(sc, ONE_WORD, LINE_S + 1.0, LINE_S + 1.94)[:2] == ("other", False)


def test_sidecar_start_takes_the_new_capture_stream_start(sc):
    """Does NOT reproduce: a new capture's listen-started replaces the old capture's
    stream start for the same (session, device), so its first line's window is its own."""
    old = {"session": "s", "device": "mic-a", "event": "listen-started", "ts": T0}
    new = dict(old, ts=at(sc["s0"], 300.0))
    sc["note_start"](old)
    sc["note_start"](new)
    assert sc["starts"][("s", "mic-a")] == sc["s0"] + 300.0


def test_fresh_label_tail_names_only_seqs_it_has_rows_for(tmp_path):
    """Does NOT reproduce: a capture's fresh LabelTail reads the whole day's file, but
    rows are keyed by seq and seqs never repeat, so an earlier agent row cannot name the
    new capture's first partial."""
    speakers = tmp_path / "speakers_2030-06-09.jsonl"
    for seq in (7, 8, 9):
        write_row(speakers, seq, AGENT, True, "u0")
    tail = LabelTail(lambda: speakers)
    assert tail.read([10]) == [None]
    assert tail.read([9]) == [Label(AGENT, True, "u0")]
    labels, guards = read_turn(tail, [10], [ONE_WORD])
    assert turn_speaker(labels, **guards) is None


# -- (d) the fix: after the line, only its ENDING is its echo (EARS-469) -----------------

REAL = "The listener's back on the new code. Say anything, and I'll confirm I heard you."
REAL_S = 5.255  # saying -> said, as measured in the case (17 words, as words() splits them)


def real_line(sc, end_s=REAL_S):
    sc["note_mouth"]({"kind": "saying", "utt": "u1", "agent": AGENT, "ts": T0, "text": REAL})
    if end_s is not None:
        sc["note_mouth"]({"kind": "said", "utt": "u1", "agent": AGENT, "ts": at(sc["s0"], end_s)})


def test_the_measured_case_keeps_other(sc):
    """The case's turn, by its own numbers: "Anything." 0.351-1.529 s after said. Word 10 of 17
    is not the line's ending, so it keeps ``other`` and no mouth_utt."""
    real_line(sc)
    assert label(sc, ONE_WORD, REAL_S + 0.351, REAL_S + 1.529) == ("other", False, None)


def test_the_measured_case_end_to_end_names_nobody(sc, tmp_path):
    real_line(sc)
    speakers = tmp_path / "speakers_2030-06-09.jsonl"
    write_row(speakers, 1230995, *label(sc, ONE_WORD, REAL_S + 0.351, REAL_S + 1.529))
    tail = LabelTail(lambda: speakers, lines=lambda utt: REAL if utt == "u1" else None)
    labels, guards = read_turn(tail, [1230995], [ONE_WORD])
    assert turn_speaker(labels, **guards) is None


@pytest.mark.parametrize("text,w0_s,w1_s", [
    pytest.param("heard you.", REAL_S + 0.2, REAL_S + 0.9, id="tail-two-words"),
    pytest.param("you.", REAL_S + 0.3, REAL_S + 0.7, id="tail-last-word"),
    pytest.param("I'll confirm I heard you.", REAL_S - 1.0, REAL_S + 0.6, id="straddles-the-end"),
    pytest.param("and I'll confirm I heard you", REAL_S - 0.4, REAL_S + 1.2, id="straddle-mostly-after"),
])
def test_the_agents_own_ending_is_still_the_agent(sc, text, w0_s, w1_s):
    real_line(sc)
    assert label(sc, text, w0_s, w1_s) == (AGENT, True, "u1")


def test_the_agents_own_ending_one_partial_turn_is_the_agent(sc, tmp_path):
    real_line(sc)
    speakers = tmp_path / "speakers_2030-06-09.jsonl"
    write_row(speakers, 7, *label(sc, "heard you.", REAL_S + 0.2, REAL_S + 0.9))
    tail = LabelTail(lambda: speakers, lines=lambda utt: REAL if utt == "u1" else None)
    labels, guards = read_turn(tail, [7], ["heard you."])
    assert turn_speaker(labels, **guards) == AGENT


@pytest.mark.parametrize("text,w0_s,w1_s", [
    pytest.param("on the new code", 1.0, 2.0, id="mid-line"),
    pytest.param("Anything.", 2.6, 3.1, id="mid-line-one-word"),
])
def test_an_echo_while_the_line_plays_is_still_the_agent(sc, text, w0_s, w1_s):
    """The rule while the line plays is unchanged: half the heard words in the line."""
    real_line(sc)
    assert label(sc, text, w0_s, w1_s) == (AGENT, True, "u1")


def test_ending_words_past_the_tail_are_not_renamed(sc):
    real_line(sc)
    assert label(sc, "heard you.", REAL_S + 1.6, REAL_S + 2.2) == ("other", False, None)


def test_a_person_answer_with_the_ending_plus_more_words_keeps_other(sc):
    """"you" alone of four words is under half: the answer is the person's."""
    real_line(sc)
    assert label(sc, "Yes I hear you", REAL_S + 0.3, REAL_S + 1.4)[:2] == ("other", False)


# b1: no said row seen. The line ends at the next line's start, else at an estimate.

def test_unended_line_ends_at_the_next_lines_start(sc):
    real_line(sc, end_s=None)
    sc["note_mouth"]({"kind": "saying", "utt": "u2", "agent": AGENT, "ts": at(sc["s0"], 6.0),
                      "text": "Something else entirely now."})
    # before the next line: still the first line's mid-line echo
    assert label(sc, "Say anything", 2.5, 3.2) == (AGENT, True, "u1")
    # after the next line started, a mid-line word of the first is not its ending
    assert label(sc, ONE_WORD, 6.4, 7.0) == ("other", False, None)
    # the first line's ending, just after the next started: still the first line's echo
    assert label(sc, "heard you.", 6.1, 6.6) == (AGENT, True, "u1")


def test_unended_line_with_no_next_line_is_bounded_by_the_estimate(sc):
    real_line(sc, end_s=None)
    n = len(sc["words"](REAL))
    assert n == 17
    est = max(10.0, 0.6 * n)   # 10.2 s
    assert label(sc, ONE_WORD, 3.0, 3.6) == (AGENT, True, "u1")          # inside the estimate
    assert label(sc, ONE_WORD, est + 0.2, est + 0.8)[:2] == ("other", False)  # after it, mid-line word
    assert label(sc, "heard you.", est + 0.2, est + 0.8) == (AGENT, True, "u1")  # its ending
    assert label(sc, "heard you.", est + 1.6, est + 2.2)[:2] == ("other", False)  # past it + tail


def test_note_mouth_keeps_the_lines_words_in_order(sc):
    real_line(sc)
    assert sc["mouth"]["u1"][3][-3:] == ["i", "heard", "you"]
