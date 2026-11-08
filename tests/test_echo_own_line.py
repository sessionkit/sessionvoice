"""EARS-503 repro-001: a seat's own line, heard back on the built-in speakers, reaches
that seat as part of the user's reply.

The report (a laptop's built-in speakers, 0.3.2): "The room ears transcribed my own
spoken line from the speakers. It arrived as part of the user's reply, under the
`reply:claude` rule." The heard log of that turn is not to hand, so these cases are
INFERRED from the report and the code. They are not a recorded turn.

Two facts limit the shape:

- `reply:claude` comes only from `Room.answered_line`, which reads `Room.finished`.
  Only a mouth `said` row fills that. So the keeper DID read the seat's said rows.
  `mouth say` writes saying/said rows to the same heard log, and it is the kit's only
  supported speaking path (skills/ears). That rules out "the mouth wrote no said row".
- The line answered is one that FINISHED before the turn began. The echoed line was
  still playing when the turn opened, or it would have been the line answered. So the
  turn opened partway into the echoed line.

`trim_echo` misses one shape that fits both facts. The turn opens partway into the
seat's line, holds only the line's TAIL (under COVER, half the line's words), and then
the user's reply. A turn that opens later than OPEN_S into a line, and is not mostly
that line, also fails the open guard. Each guard alone refuses the cut. The tail is
mailed as the user's words, To the seat, as its reply to the line before.

The control case shows the boundary: the same line heard WHOLE ahead of the same
reply is cut today.

The words and timings are synthetic. fix-001 cuts the tail: on the real span, a turn
that opened while the line played is matched against the words left in the line at
its open (TAIL_SLACK). The quote cases below hold that a user QUOTING the tail keeps
his words.
"""
import pytest

from tests.test_ears_mail import run

RULES = {"defaults": {"openers": "hey|hay"},
         "seats": {"claude": {"names": "claude"}, "mini-me": {"names": "mini-me|minnie me"},
                   "third": {"names": "third"}}}
SPEAKERS = "MacBook Air Speakers"
MIC = "MacBook Air Microphone"

BEFORE = "Looking now."
LINE = ("Done. The heard box default is fixed on the branch, and the tests are green. "
        "I have minted the ready tag, so Alice can merge it when she wakes. "
        "Shall I start on the next card?")
# the line's last 15 of 36 words, as the mic heard them once the turn opened
TAIL = "so Alice can merge it when she wakes. Shall I start on the next card?"
REPLY = "Yes please, go ahead and start the next one, and tell me when it is ready."


def ts(sec):
    return "2030-06-09T12:00:%06.3f+10:00" % sec


def heartbeat():
    """The stream began at 12:00:00, so a turn's t0..t1 are its seconds past it: the
    keeper judges the turn on its REAL span."""
    return {"ts": ts(1.0), "seq": 1, "kind": "event", "event": "heartbeat", "source": "mic",
            "device": MIC, "session": "ears-keeper", "stream_s": 1.0}


def line(sec, played, utt, text, seq):
    return [{"ts": ts(sec), "seq": seq, "kind": "saying", "agent": "claude", "utt": utt,
             "device": SPEAKERS, "text": text},
            {"ts": ts(sec + played), "seq": seq + 1, "kind": "said", "agent": "claude", "utt": utt,
             "device": SPEAKERS, "played_s": played, "cut": False, "text_played_est": text}]


def turn(t0, t1, text, seq):
    return {"ts": ts(t1 + 1.0), "seq": seq, "kind": "turn", "source": "mic", "device": MIC,
            "session": "ears-keeper", "t0": t0, "t1": t1, "text": text}


def rows(*turns):
    """The seat said BEFORE (2-3 s), then LINE (10-20 s) on the built-in speakers."""
    out = [heartbeat()] + line(2.0, 1.0, "c1a0de000001", BEFORE, 10)
    out += line(10.0, 10.0, "c1a0de000503", LINE, 20)
    return sorted(out + list(turns), key=lambda r: r["seq"])


def mails(*turns):
    got = run(rows(*turns), RULES, to=None)
    return [(m["seq"], m["mail"]) for m in got]


def test_control_the_whole_line_ahead_of_his_reply_is_cut():
    """Today's cut works when the turn opens with the line: only his reply is mailed."""
    got = mails(turn(10.3, 26.0, LINE + " " + REPLY, 100))
    assert [m["body"] for _, m in got] == [REPLY + "\n"]
    assert got[0][1]["headers"]["Ears-Echo"] == "claude/c1a0de000503 36 words cut"


@pytest.mark.parametrize("fragment", [False, True], ids=["opening-unheard", "opening-own-turn"])
def test_the_lines_tail_ahead_of_his_reply_is_cut(fragment):
    """The turn opens 6.5 s into the 10 s line, holding its last 15 words, then his reply.
    The opening was either not heard as speech, or was a turn of its own. That
    fragment is dropped whole as echo (Room.echoes). Only his reply is mailed, with
    the cut flagged: the tail match judges the words left in the line at the open."""
    turns = [turn(16.5, 25.0, TAIL + " " + REPLY, 100)]
    if fragment:
        turns.insert(0, turn(10.3, 14.0, "Done. The heard box default is fixed on the branch, "
                                         "and the tests are green. I have minted", 90))
    got = mails(*turns)
    assert [m["body"] for _, m in got] == [REPLY + "\n"]
    assert got[0][1]["headers"].get("Ears-Echo") == "claude/c1a0de000503 15 words cut"


def test_the_tail_no_longer_reaches_the_seat_as_his_reply():
    """The failure as reported, inverted: before the fix the tail and his reply went out
    as one mail, his words, To the seat whose line it is. Now his words are his reply
    alone, still To that seat as its reply; the tail is flagged as cut, and the seat
    whose line was cut is shown the turn heard whole below its own marker."""
    got = run(rows(turn(16.5, 25.0, TAIL + " " + REPLY, 100)), RULES, to="seats")
    assert [(m["seq"], m["to"], m["rule"]) for m in got] == [(100, ["claude"], "claude=reply")]
    mail = got[0]["mail"]
    assert mail["headers"]["Ears-Echo"] == "claude/c1a0de000503 15 words cut"
    his, _, whole = mail["body"].partition("\n-- heard whole, echo included --\n")
    assert his == REPLY + "\n"
    assert whole == TAIL + " " + REPLY + "\n"


# -- quotes of the line's tail are the user's words, and are kept ---------------------
# The tail match must not cut a user who QUOTES the line's tail. A quote repeats words
# already heard; it is made after the line ended, or names words played before the turn
# opened, or is led by the user's own words. Each of those is kept whole.

@pytest.mark.parametrize("t0, said", [
    (21.5, "You said shall I start on the next card? Yes, start on it."),
    (21.5, "Shall I start on the next card? Yes, start on it."),
    (19.0, "So Alice can merge it when she wakes? No, wait for me to look first."),
    (18.5, "Wait, so Alice can merge it when she wakes? Yes."),
], ids=["after-the-line-led-by-his-words", "after-the-line-bare-tail",
        "mid-line-earlier-words", "mid-line-led-by-his-word"])
def test_a_user_quoting_the_lines_tail_keeps_his_words(t0, said):
    """He quotes the line's tail (or words just before it) back at the seat. Nothing is
    cut: the whole turn is mailed as his, with no Ears-Echo."""
    got = mails(turn(t0, t0 + 4.0, said, 100))
    assert [m["body"] for _, m in got] == [said + "\n"]
    assert "Ears-Echo" not in got[0][1]["headers"]
