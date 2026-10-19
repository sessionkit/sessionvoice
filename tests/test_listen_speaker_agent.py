"""EARS-360: the mouth's agent yields to ONE named person.

A mouth line heard back by the mic is labelled with the line's agent by the side-car's
mouth attribution (``speaker_by: "mouth"``), not by voice. A person's short reply at the
tail of that echo is often ONE partial, and the split needed K=2 in a row, so the reply
stayed in the agent's turn and was named the agent's. Now ONE partial labelled with a
named person takes a turn from the mouth's agent, unless its text is the line's own echo
(``mouth.hold.is_echo``: the voice matcher can score the line's last word as the person).
Every other edge keeps K=2.

Synthetic labels and text only: ``ivy`` is the agent, ``alice``/``bob`` are people, the
STT says ``w1``, ``w2``, ... one per burst, and the mouth lines are made up here.
"""
from __future__ import annotations

import json

import pytest

from ears import heard
from ears.listen.speakers import Label, LabelTail, LineTexts, first_cut, guard_flags, turn_speaker
from tests.listen_helpers import read_lines
from tests.test_listen_speaker_split import run, said

A = Label("ivy", True, "u1")          # the agent, by mouth attribution, over line u1
U = Label("unheard")                  # neutral
O = Label("other")                    # neutral
P = Label("alice")                    # a person, no mouth_utt: the reply after the line
PN = Label("alice", False, "u2")      # a person whose words fell in line u2, NOT its echo
PE = Label("alice", False, "u1")      # a person label on line u1's own echo (its last word)

# u1 ends in the word the STT gives the 5th burst; u2 shares no word with any w<n>
LINES = {"u1": "here is the build status and the last word is w5", "u2": "nothing in this line matches"}


class MouthLabels:
    """A LabelTail stand-in: each partial's Label by its text (w1, w2, ...), read back from
    the heard log, and the mouth lines' texts by utt."""

    def __init__(self, logs, by_text, lines=LINES):
        self.logs, self.by_text, self.lines = logs, by_text, lines

    def read(self, seqs):
        text = {ln["seq"]: ln.get("text") for ln in read_lines(self.logs)}
        return [self.by_text.get(text.get(s)) for s in seqs]

    def labels(self, seqs):
        return [r.speaker if r else None for r in self.read(seqs)]

    def line_text(self, utt):
        return self.lines.get(utt)


def go(tmp_path, rows):
    """A capture of len(rows) bursts in ONE turn, partial n labelled rows[n-1]."""
    lab = MouthLabels(tmp_path / "logs", {f"w{i + 1}": r for i, r in enumerate(rows)})
    turns, lines, _ = run(tmp_path, len(rows), labels=lab)
    return turns


def words(n, m):
    return " ".join(f"w{i}" for i in range(n, m + 1))


# The 4 shapes the reply came in on 2030-06-09 (made up here, same form): an agent run,
# neutral partials, ONE person partial, sometimes a neutral after it.
SHAPES = [
    [A, A, A, U, "P", U],
    [A, A, A, U, U, "P"],
    [A, U, A, O, A, U, U, "P"],
    [A, A, U, A, U, "P", U],
]


# -- 1. the reply is cut out of the agent's turn ------------------------------------

@pytest.mark.parametrize("person", [P, PN], ids=["no-mouth_utt", "mouth_utt-not-echo"])
@pytest.mark.parametrize("shape", SHAPES, ids=["A3-u-P-u", "A3-uu-P", "AuAoAuu-P", "AAuAu-P-u"])
def test_the_agents_turn_is_cut_at_one_person_partial(tmp_path, shape, person):
    rows = [person if r == "P" else r for r in shape]
    at = rows.index(person)                       # 0-based partial index of the reply
    turns = go(tmp_path, rows)
    assert said(turns) == [(words(1, at), "ivy"), (words(at + 1, len(rows)), "alice")]
    assert turns[0]["split"] == "speaker-change" and turns[1]["opened_by"] == "speaker-change"


# -- 2. an echo-only turn is left alone ------------------------------------------------

def test_an_echo_only_turn_does_not_cut(tmp_path):
    turns = go(tmp_path, [A, A, U, A, O, A, A, U])
    assert said(turns) == [(words(1, 8), "ivy")]
    assert "split" not in turns[0] and turns[0]["speaker_from"] == "labels"


# -- 3. the guard: a person label on the line's own echo -------------------------------

def test_a_person_label_on_the_lines_echo_does_not_cut(tmp_path):
    # the 5th partial is "w5", the last word of line u1, scored alice by the voice match
    turns = go(tmp_path, [A, A, A, U, PE, U])
    assert said(turns) == [(words(1, 6), "ivy")]
    assert "split" not in turns[0]


def test_an_echo_person_partial_still_counts_toward_k(tmp_path):
    # the guard takes the K=1 edge away, not the K=2 rule: echo alice, then a real alice
    # partial with no mouth_utt cuts at the REAL one (K=1), and the echo word stays the agent's
    turns = go(tmp_path, [A, A, A, U, PE, P])
    assert said(turns) == [(words(1, 5), "ivy"), ("w6", "alice")]


# -- 4. two named persons keep K=2 ------------------------------------------------------

@pytest.mark.parametrize("rows,want", [
    ([P, P, Label("bob"), U, P], [(words(1, 5), "alice")]),                      # one bob: no cut
    ([P, P, Label("bob"), Label("bob")], [("w1 w2", "alice"), ("w3 w4", "bob")]),  # two: a cut
])
def test_a_turn_with_two_named_persons_keeps_k2(tmp_path, rows, want):
    assert said(go(tmp_path, rows)) == want


# -- the remainder of a cut is held by the person it opened with -------------------------

def test_the_agent_retakes_the_remainder_only_by_k2(tmp_path):
    # ivy's line, alice's one-partial reply, ivy's next line: three turns. Unseeded, the
    # remainder "alice ivy ivy" would be taken back into ivy's turn by first_cut's first holder.
    turns = go(tmp_path, [A, A, U, P, A, A])
    assert said(turns) == [("w1 w2 w3", "ivy"), ("w4", "alice"), ("w5 w6", "ivy")]


def test_person_to_agent_keeps_k2(tmp_path):
    turns = go(tmp_path, [A, A, P, U, A])
    assert said(turns) == [("w1 w2", "ivy"), ("w3 w4 w5", "alice")]


def test_the_first_holder_keeps_k2(tmp_path):
    # no holder yet: one agent label then one person label is not a change
    turns = go(tmp_path, [A, P, P, P])
    assert said(turns) == [(words(1, 4), "alice")]


# -- the pure rule ------------------------------------------------------------------------

@pytest.mark.parametrize("labels,agent,echo,held,want", [
    (["ivy", "ivy", None, "alice"], [1, 1, 0, 0], None, None, (3, "ivy", "alice")),
    (["ivy", "ivy", None, "alice"], None, None, None, None),                  # no marks: K=2
    (["ivy", "ivy", None, "alice"], [1, 1, 0, 0], [0, 0, 0, 1], None, None),  # echo: no cut
    (["ivy", "alice"], [0, 0], None, None, None),                             # ivy by voice: K=2
    (["alice", "ivy", "ivy"], [0, 1, 1], None, "alice", (1, "alice", "ivy")),  # held: agent by K=2
    (["alice", "ivy", "ivy"], [0, 1, 1], None, None, None),
    (["alice", "ivy"], [0, 1], None, "alice", None),
    (["ivy", "ivy"], [1, 1], None, "alice", None),                            # contradicted at 0
])
def test_first_cut_agent_rule(labels, agent, echo, held, want):
    agent = [bool(x) for x in agent] if agent is not None else None
    echo = [bool(x) for x in echo] if echo is not None else None
    assert first_cut(labels, 2, agent=agent, echo=echo, held=held) == want


def test_turn_speaker_a_reply_the_split_did_not_cut_is_a_conflict():
    # closed before the split saw it: two holders, so no name (never the agent's on his words)
    assert turn_speaker(["ivy", "ivy", "alice"], 2, agent=[True, True, False]) is None
    assert turn_speaker(["ivy", "ivy", "alice"], 2, agent=[True, True, False], echo=[False, False, True]) == "ivy"
    assert turn_speaker(["ivy", "ivy", "alice"], 2) == "ivy"                 # no marks: as before


def test_guard_flags_uses_is_echo_against_the_lines_text():
    rows = [A, None, U, P, PN, PE, Label("alice", False, "gone")]
    texts = ["w1", "w2", "w3", "yes please", "yes please", "w5", "w5"]
    agent, echo = guard_flags(rows, texts, LINES.get)
    assert agent == [True, False, False, False, False, False, False]
    # no mouth_utt: not echo; not the line's words: not echo; the line's last word: echo;
    # a line whose text is unknown: echo (keep today's K, never a cut it cannot check)
    assert echo == [False, False, False, False, False, True, True]


# -- the readers ----------------------------------------------------------------------------

def test_label_tail_keeps_the_mouth_marker_and_mouth_utt(tmp_path):
    sp = tmp_path / "speakers.jsonl"
    rows = [{"seq": 1, "speaker": "ivy", "speaker_by": "mouth", "mouth_utt": "u1"},
            {"seq": 2, "speaker": "alice", "mouth_utt": "u1"},
            {"seq": 3, "speaker": "alice"},
            {"seq": 4, "speaker": "bob", "speaker_by": "voice", "mouth_utt": 7}]
    sp.write_text("".join(json.dumps(r) + "\n" for r in rows))
    tail = LabelTail(lambda: sp, lines=LINES.get)
    assert tail.read([1, 2, 3, 4, 5]) == [Label("ivy", True, "u1"), Label("alice", False, "u1"),
                                          Label("alice"), Label("bob"), None]
    assert tail.labels([1, 2, 3, 4, 5]) == ["ivy", "alice", "alice", "bob", None]
    assert tail.line_text("u2") == LINES["u2"]
    assert LabelTail(lambda: sp).line_text("u1") is None


def test_line_texts_reads_saying_rows_incrementally(tmp_path):
    log = tmp_path / "heard.jsonl"
    log.write_text(json.dumps({"kind": heard.SAYING, "utt": "old", "text": "before the capture"}) + "\n")
    lines = LineTexts(lambda: log)
    assert lines("old") is None                                  # read from the END, like SaidTail
    with open(log, "a") as f:
        f.write(json.dumps({"kind": "partial", "text": "w1"}) + "\n")
        f.write(json.dumps({"kind": heard.SAYING, "utt": "u1", "text": LINES["u1"]}) + "\n")
        f.write(json.dumps({"kind": heard.SAID, "utt": "u1"}) + "\n")
        f.write('{"kind": "saying", "utt": "u2", "te')                # half a line: next time
    assert lines("u1") == LINES["u1"] and lines("u2") is None
    with open(log, "a") as f:
        f.write('xt": "a line"}\n')
    assert lines("u2") == "a line"
    small = LineTexts(lambda: log, keep=1)
    with open(log, "a") as f:
        for u in ("a", "b"):
            f.write(json.dumps({"kind": heard.SAYING, "utt": u, "text": u}) + "\n")
    assert small("a") is None and small("b") == "b"               # bounded: the oldest goes


# -- the final split at the turn's close (fix-002) ----------------------------------------
# A reply partial that lands after the frame loop's last split check, just before the VAD
# close, used to stay in the agent's turn, and the turn was named None (two holders). The
# close now runs the split once. Here the frame loop's check is put out of reach, so every
# cut below is the close's.

@pytest.fixture
def close_only(monkeypatch):
    from ears.listen import loop
    monkeypatch.setattr(loop, "SPLIT_CHECK_S", 1e6)   # frame 0 only: before any partial


@pytest.mark.parametrize("person", [P, PN], ids=["no-mouth_utt", "mouth_utt-not-echo"])
def test_a_reply_just_before_the_close_is_split_at_the_close(tmp_path, close_only, person):
    turns = go(tmp_path, [A, A, A, U, person])
    assert said(turns) == [(words(1, 4), "ivy"), ("w5", "alice")]
    assert turns[0]["split"] == "speaker-change" and turns[1]["opened_by"] == "speaker-change"
    assert "split" not in turns[1]


def test_the_close_split_keeps_the_echo_guard(tmp_path, close_only):
    # w5 is line u1's last word, scored alice: the line's own echo, so no cut at the close
    turns = go(tmp_path, [A, A, A, U, PE])
    assert said(turns) == [(words(1, 5), "ivy")]
    assert "split" not in turns[0]


def test_the_close_split_keeps_k2_between_named_persons(tmp_path, close_only):
    assert said(go(tmp_path, [P, P, Label("bob")])) == [(words(1, 3), "alice")]
    assert said(go(tmp_path / "two", [P, P, Label("bob"), Label("bob")])) == [("w1 w2", "alice"),
                                                                              ("w3 w4", "bob")]


def test_the_close_split_runs_once_and_never_on_a_halfs_close(tmp_path, close_only):
    # two changes waiting at the close: ONE cut (the first); the remainder is not re-split
    # by its own close, nor is the before-half (it closes with split=...)
    turns = go(tmp_path, [P, P, Label("bob"), Label("bob"), P, P])
    assert [t["text"] for t in turns] == ["w1 w2", "w3 w4 w5 w6"]
    assert turns[0]["speaker"] == "alice" and turns[1]["speaker"] == "bob"
