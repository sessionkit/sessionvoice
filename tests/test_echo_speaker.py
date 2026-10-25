"""EARS-467: a turn the user spoke over a seat's line kept that seat's Ears-Speaker
after the echo cut, so his words rode as the seat's own echo and woke nobody.

The shape, measured live: a seat's line played on the laptop speakers; the user spoke
over its tail ("Yes please, go. Go."). The capture labelled the WHOLE turn with the seat's
name (its `speaker`), since most of the turn's audio was the seat's voice. ears-mail's
echo cut (Room.trim_echo) took the line's words out and named the cut in Ears-Echo, but:

- the device mail's Ears-Speaker was speaker_of(rec, ...) on the RAW record, so the
  remainder, which is all his, still said <seat>@<domain>; a pager rule that rides
  `ears-speaker:<seat>@*` rode his words as the seat's echo;
- verdicts() took `own = speaker_label(rec)` from the same raw label, so his reply to
  the line he had just heard was dropped as the seat answering itself: no `<seat>=reply`
  in Ears-Rule, and on the seats path no mail at all.

The fix (heard_rec): after a cut that leaves words, a label naming a seat whose line
was cut is dropped; the remainder gets the default speaker and can be a reply. These
were strict xfails at repro-001; the fix made them pass. The controls pin what it
keeps: the cut itself, the label on a turn with nothing cut, an all-echo turn mailing
nothing, and a label naming anyone else.

Synthetic words, synthetic 2030-06-09 dates; offline (`--dry-run`), no mic, no mouth.
"""
import pytest

from tests.test_ears_mail import em_module, run

SEAT = "vera"
DOMAIN = "host1"                     # run()'s --domain
RULES = {"seats": {"vera": {"names": "vera|vira"}, "ivy": {"names": "ivy"}}}
S = "2030-06-09T12:00:%06.3f+10:00"

# The seat's line: what the mouth played, and what the mic heard of it.
LINE = ("The branch is rebased on master and every test passes, so the only thing "
        "left is your go to merge it and tag the release for tonight.")
HIS = "Yes please, go. Go."


def _rows(text=LINE + " " + HIS, label="Vera", line=True):
    """The stream's heartbeat (stream start 0.0 s), the seat's line on the laptop
    speakers (queued 2.0 s, played 9.0 s, said 11.0 s) and the user's turn over its
    tail (real span 2.5 .. 14.0 s, row at 14.5 s), labelled LABEL."""
    rows = [{"ts": S % 1.0, "seq": 5, "kind": "event", "event": "heartbeat", "source": "mic",
             "device": "MacBook Pro Microphone", "session": "ears-keeper", "stream_s": 1.0}]
    if line:
        rows += [
            {"ts": S % 2.0, "seq": 20, "kind": "saying", "agent": SEAT, "utt": "c0ffee467001",
             "device": "MacBook Pro Speakers", "text": LINE},
            {"ts": S % 11.0, "seq": 21, "kind": "said", "agent": SEAT, "utt": "c0ffee467001",
             "device": "MacBook Pro Speakers", "played_s": 9.0, "cut": False,
             "text_played_est": LINE}]
    turn = {"ts": S % 14.5, "seq": 900, "kind": "turn", "source": "mic",
            "device": "MacBook Pro Microphone", "session": "ears-keeper",
            "t0": 2.5, "t1": 14.0, "text": text}
    if label is not None:
        turn["speaker"] = label
    return rows + [turn]


def _device_mail(rows):
    mails = [m for m in run(rows, RULES, to=None) if m["seq"] == 900]
    assert len(mails) == 1, mails
    return mails[0]["mail"]


# -- controls: today's behaviour that the fix must keep ------------------------

def test_control_the_echo_is_cut_and_his_words_kept():
    """The cut works: the line is gone from the body, his words are all there, and
    Ears-Echo names the seat's line. (The bug is not in the cut.)"""
    mail = _device_mail(_rows())
    assert mail["body"] == HIS + "\n"
    assert mail["headers"]["Ears-Echo"] == "vera/c0ffee467001 27 words cut"


def test_control_no_echo_keeps_the_label():
    """With no line in the room nothing is cut, and the label is the speaker as before:
    the fix is scoped to a turn an echo cut left words in."""
    mail = _device_mail(_rows(text=HIS, line=False))
    assert "Ears-Echo" not in mail["headers"]
    assert mail["headers"]["Ears-Speaker"] == f"{SEAT}@{DOMAIN}"


def test_control_an_all_echo_turn_mails_nothing():
    """A turn that is only the line's echo is dropped whole, label or not."""
    assert [m for m in run(_rows(text=LINE), RULES, to=None) if m["seq"] == 900] == []


# -- the fix: the remainder is never the echoed seat ---------------------------

def test_the_remainder_is_never_the_echoed_seats_speaker():
    """After a cut that leaves words, Ears-Speaker is the default, never the seat whose
    echo was cut: `ears-speaker:<seat>@*` must not ride his words."""
    mail = _device_mail(_rows())
    assert mail["headers"]["Ears-Echo"] == "vera/c0ffee467001 27 words cut"
    assert mail["headers"]["Ears-Speaker"] == f"unknown@{DOMAIN}"


def test_the_remainder_takes_the_configured_speaker():
    """The default is --speaker's, as for any turn the matcher could not place."""
    mails = [m for m in run(_rows(), RULES, to=None, args=["--speaker", "theo@host1"])
             if m["seq"] == 900]
    assert len(mails) == 1
    assert mails[0]["mail"]["headers"]["Ears-Speaker"] == "theo@host1"


def test_his_words_after_the_cut_are_a_reply_to_the_seat():
    """His unaddressed words right after the seat's line are a reply to it (Ears-Rule
    <seat>@<domain>=reply), not the seat answering itself."""
    mail = _device_mail(_rows())
    assert mail["headers"]["Ears-Rule"] == f"{SEAT}@{DOMAIN}=reply"


def test_the_seats_path_mails_his_reply():
    """--to seats: the same turn is one mail to the seat, rule reply, his words."""
    mails = [m for m in run(_rows(), RULES, to="seats") if m["seq"] == 900]
    assert [(m["to"], m["rule"]) for m in mails] == [([SEAT], f"{SEAT}=reply")]
    assert mails[0]["mail"]["body"].startswith(HIS + "\n")


@pytest.mark.parametrize("label", ["VERA", " Vera ", "vera"])
def test_the_cut_seats_label_is_dropped_in_any_case(label):
    """The label is compared as speaker_label() reads it: case and spaces do not
    keep the echoed seat's name on his words."""
    mail = _device_mail(_rows(label=label))
    assert mail["headers"]["Ears-Speaker"] == f"unknown@{DOMAIN}"
    assert mail["headers"]["Ears-Rule"] == f"{SEAT}@{DOMAIN}=reply"


def test_a_persons_label_over_the_cut_is_kept():
    """A label naming someone else (the side-car placed his voice) is kept: only the
    seat whose echo was cut loses its label."""
    mail = _device_mail(_rows(label="Alice"))
    assert mail["headers"]["Ears-Echo"] == "vera/c0ffee467001 27 words cut"
    assert mail["headers"]["Ears-Speaker"] == f"alice@{DOMAIN}"
    assert mail["headers"]["Ears-Rule"] == f"{SEAT}@{DOMAIN}=reply"


def test_another_seats_label_over_the_cut_is_kept():
    """A label naming a seat whose line was NOT cut is kept, and is still that seat's
    own voice: it is no reply to it, but the cut seat's line is answered."""
    mail = _device_mail(_rows(label="Ivy"))
    assert mail["headers"]["Ears-Speaker"] == f"ivy@{DOMAIN}"
    assert mail["headers"]["Ears-Rule"] == f"{SEAT}@{DOMAIN}=reply"


# Two lines, two seats, both cut from one turn, then his words.
IVY_LINE = "The keeper restarted cleanly and the heard log is rolling again on the laptop."


def _two_lines(label):
    rows = _rows(text=IVY_LINE + " " + LINE + " " + HIS, label=label)
    ivy = [{"ts": S % 1.5, "seq": 10, "kind": "saying", "agent": "ivy", "utt": "c0ffee467002",
            "device": "MacBook Pro Speakers", "text": IVY_LINE},
           {"ts": S % 1.9, "seq": 11, "kind": "said", "agent": "ivy", "utt": "c0ffee467002",
            "device": "MacBook Pro Speakers", "played_s": 0.4, "cut": False,
            "text_played_est": IVY_LINE}]
    return rows[:1] + ivy + rows[1:]


@pytest.mark.parametrize("label", ["Ivy", "Vera"])
def test_two_lines_cut_the_label_of_either_seat_is_dropped(label):
    """Two lines cut from one turn: a label naming either line's seat is dropped."""
    mail = _device_mail(_two_lines(label))
    assert mail["body"] == HIS + "\n"
    assert [e.split()[0] for e in mail["headers"]["Ears-Echo"].split(", ")] == [
        "ivy/c0ffee467002", "vera/c0ffee467001"]
    assert mail["headers"]["Ears-Speaker"] == f"unknown@{DOMAIN}"


# -- heard_rec, the helper ---------------------------------------------------

def test_heard_rec_drops_only_a_cut_seats_label_and_never_mutates():
    em = em_module()
    rec = {"seq": 1, "text": "x", "speaker": " Vera "}
    cuts = [("vera", "c0ffee467001", 27)]
    got = em.heard_rec(rec, "Yes please.", cuts)
    assert got == {"seq": 1, "text": "x"} and rec["speaker"] == " Vera "
    assert em.heard_rec(rec, "Yes please.", []) is rec                  # nothing cut
    assert em.heard_rec(rec, "", cuts) is rec                           # all echo
    assert em.heard_rec(rec, "Yes please.", [("ivy", "c0ffee467002", 9)]) is rec
    alice = {"speaker": "alice"}
    assert em.heard_rec(alice, "Yes please.", cuts) is alice            # a person
    plain = {"seq": 2}
    assert em.heard_rec(plain, "Yes please.", cuts) is plain            # no label
    neutral = {"speaker": "mixed"}
    assert em.heard_rec(neutral, "Yes please.", cuts) is neutral        # names nobody


def test_verdicts_on_the_heard_rec_reply_to_the_cut_seat():
    """One level down: trim_echo leaves his words; verdicts() on the raw label sees the
    seat's own voice (the guard against a seat answering itself, still right for an
    uncut turn), and on heard_rec's record his words are a reply."""
    em = em_module()
    room, rec = _room_and_turn(em)
    his, began, cuts = room.trim_echo(rec)
    assert his == HIS and [c[:2] for c in cuts] == [(SEAT, "c0ffee467001")]
    rules = em.load_rules(None, [SEAT, "ivy"])
    assert em.verdicts(rec, rules, room, his, began) == {}
    assert em.verdicts(em.heard_rec(rec, his, cuts), rules, room, his, began) \
        == {SEAT: "reply"}


def _room_and_turn(em):
    room = em.Room()
    rows = _rows()
    for r in rows[:-1]:
        room.note(r)
    return room, rows[-1]
