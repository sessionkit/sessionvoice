"""EARS-349: ``mouth amend`` of a line that is not yet heard.

Measured twice on a call: ``mouth amend`` of a barge-in replay HELD for the end
of his turn refused with "already playing", though nothing of it was audible.
A held line stays in the queue (``player._next`` skips it), so the refusal came
from the gap after the player TAKES a line (``playing.json`` is written) and
before its first frame: synthesis, the lead hold, the device opening. Amend
now replaces a line in that gap too, and refuses only a line being heard,
which ``playing.json``'s ``first_frame_t`` marks.

No sound: the silence backend on the null device; the ears are faked by
writing partial/turn records into a scratch heard log.
"""
from __future__ import annotations

import json
import threading
import time

import pytest

import mouth
from mouth import backends, box as _box, player

from test_mouth_hold import _saying, _wait_for, ears  # noqa: F401  (fixture)
from test_mouth_inbox import HOST, box, deliver, lines, run_player  # noqa: F401  (fixture)
from test_mouth_mail_trust_uses import mail


def _said(b, utt):
    return [x for x in lines(b.logs) if x["kind"] == "said" and x["utt"] == utt]


def _playing(b):
    try:
        return json.loads((b.d / "playing.json").read_text())
    except (FileNotFoundError, ValueError):
        return None


@pytest.fixture
def slow_render(monkeypatch):
    """A synthesis that has not yet produced a frame: a line whose text holds
    ``old`` renders nothing until the gate opens (a clone rendering, the lead
    hold). Other lines speak at once.

    ``gate.self_released`` names each render the gate let go by ITSELF, after 10 s.
    A test that opens the gate names it in its "not cut" failure and asserts it is
    empty at the end, so a stalled host reads as "the render released itself", not
    as a line that was cut."""
    gate = threading.Event()
    gate.self_released = []

    class Gated(backends.Silence):
        def stream(self, text, voice, speed):
            if "old" in text and not gate.wait(10):
                gate.self_released.append(text)
            yield from super().stream(text, voice, speed)

    real = backends.resolve
    monkeypatch.setattr(backends, "resolve",
                        lambda b, v: (Gated(), v) if b == "silence" else real(b, v))
    yield gate
    gate.set()   # free a render the test left waiting


def _replay(b, text="First sentence heard. The second one he cut, and it was not heard."):
    """A barge-in replay made as the player makes one (``player._resume``): the
    cut line's unheard rest, held for the end of his turn."""
    cut = {"utt": "c0ffee000001", "text": text, "device": "null", "backend": "silence",
           "agent": "ivy", "session": "s-test", "requested_t": time.time() - 3}
    rec = {"resume": "will", "resume_utt": "c0ffee000002", "resume_s": 20.0,
           "text_played_est": "First sentence heard. The second"}
    player._resume(b.d, cut, rec)
    (x,) = [x for x in _box.Queue(b.d).lines() if x["utt"] == "c0ffee000002"]
    assert x["hold"] == "turn-end" and x["resume_of"] == "c0ffee000001"
    return x


def test_amend_replaces_a_held_replay_and_it_keeps_its_place(box, ears):
    ears("partial", text="hang on a moment")           # he is talking: replays wait
    rp = _replay(box)
    later = mouth.say("queued behind the replay", d=box.d, spawn=False, hold="turn-end")
    th = run_player(box.d, idle=0.5)
    time.sleep(0.4)                                    # the player has looked: both held
    assert _playing(box) is None and not _saying(box)
    out = mouth.amend(rp["utt"], "The corrected rest.", d=box.d)
    assert out["utt"] == rp["utt"] and out["amended_from"] == rp["text"]
    q = [(x["utt"], x["text"], x.get("hold")) for x in _box.Queue(box.d).lines()]
    assert q == [(rp["utt"], "The corrected rest.", "turn-end"),
                 (later["utt"], "queued behind the replay", "turn-end")]   # its place, still held
    ears("turn", text="hang on a moment")              # his turn ends: they play, in order
    assert _wait_for(lambda: len(_saying(box)) == 2, 8)
    th.join(8)
    assert [x["text"] for x in _saying(box)] == ["The corrected rest.", "queued behind the replay"]
    (said,) = _said(box, rp["utt"])
    assert said["reason"] == "done" and said["text_played_est"] == "The corrected rest."


def test_amend_replaces_a_line_taken_but_not_yet_heard(box, slow_render):
    """The measured refusal: the player has taken the line (playing.json names
    it) and no frame has gone out. It is replaced, and the player speaks the new
    text, never the old."""
    old = mouth.say("the old words, stale now", d=box.d, spawn=False)
    after = mouth.say("queued after it", d=box.d, spawn=False)
    th = run_player(box.d, idle=0.5)
    assert _wait_for(lambda: (_playing(box) or {}).get("utt") == old["utt"])
    assert "first_frame_t" not in _playing(box) and not _saying(box)   # taken, not audible
    out = mouth.amend(old["utt"], "the new words", d=box.d)   # was: "is already playing"
    assert out["utt"] == old["utt"] and out["amended_from"] == "the old words, stale now"
    assert _wait_for(lambda: len(_saying(box)) == 2, 8)
    th.join(8)
    assert [x["text"] for x in _saying(box)] == ["the new words", "queued after it"]   # its place
    cut, done = _said(box, old["utt"])
    assert cut["reason"] == "amended" and cut["played_s"] == 0.0
    assert cut["notice"] == "none: amended before it was heard"      # not told as a drop
    assert done["reason"] == "done" and done["text_played_est"] == "the new words"
    assert [x["reason"] for x in _said(box, after["utt"])] == ["done"]


def test_amend_refuses_a_line_being_heard_and_says_so(box):
    a = mouth.say("word " * 30, d=box.d, spawn=False)
    th = run_player(box.d, idle=0.5)
    assert _wait_for(lambda: _saying(box))
    assert (_playing(box) or {}).get("first_frame_t") is not None    # the player's own mark
    with pytest.raises(ValueError) as e:
        mouth.amend(a["utt"], "too late", d=box.d)
    assert str(e.value) == f"mouth: {a['utt']} is being heard now; retract it and say again"
    mouth.stop(d=box.d)
    th.join(5)
    assert [x["reason"] for x in _said(box, a["utt"])] == ["stop"]   # untouched by the refusal


def test_a_held_line_is_never_reported_as_playing(box, ears):
    ears("partial", text="still talking")
    x = mouth.say("held for his turn", d=box.d, spawn=False, hold="turn-end")
    th = run_player(box.d, idle=0.5)
    time.sleep(0.4)
    mouth.amend(x["utt"], "held, amended", d=box.d)     # no "already playing", no "being heard"
    mouth.retract(x["utt"], d=box.d)
    th.join(5)
    assert not _saying(box)


def test_a_superseded_line_is_cut_only_before_its_first_frame(box):
    """The player's side: a supersede that lands after the first frame never
    cuts the line; it plays after it, as news (docs/mouth.md)."""
    words = " ".join(["word"] * 12)
    a = mouth.say(words, d=box.d, spawn=False)
    th = run_player(box.d, idle=0.5)
    assert _wait_for(lambda: _saying(box))
    item = {k: v for k, v in _playing(box).items() if k not in ("mail_id", "box_file", "first_frame_t")}
    _box.drop(_box.box_dir(box.d), {**item, "text": "the correction"},
              supersedes=_playing(box)["mail_id"])
    th.join(10)
    assert [x["reason"] for x in _said(box, a["utt"])] == ["done", "done"]
    assert [x["text"] for x in _saying(box)] == [words, "the correction"]


# -- fix-002: the cut is gated on EARS-335's trust ----------------------------------
#
# HAZARD (found by the EARS-350 reviewer): fix-001's ``_superseded`` cut a taken,
# unheard line for ANY ``Supersedes:`` mail that passed ``_allowed``. EARS-335 gates a
# queued line's replacement on ``_sup_trust`` or the same From (its rules (a)/(b)),
# so once both landed an UNSIGNED mail could cut ANOTHER sender's line before its
# first frame, silently (``amended`` is never noticed). The cut now asks what 335
# asks: a trusted Supersedes (local, or signed v2), or the line's own sender.

def _taken(frm):
    """A line as the player holds it: ``public()``, so ``mail_from``, never ``_from``."""
    return {"utt": "p1", "mail_id": "<1.c@test>", "mail_from": frm, "requested_t": 1.0}


@pytest.mark.parametrize("who,sig,cuts", [
    ("ivy", None, False),        # another sender, unsigned: rule (b) says no
    ("Vera", None, True),        # the line's own sender, unsigned: rule (b)'s door, case-blind
    ("ivy", "2", True),          # another sender, signed v2 covering Supersedes: rule (a)
    ("ivy", "1", False),         # v1 never signs Supersedes
], ids=["another-unsigned", "same-from-unsigned", "another-signed-v2", "another-signed-v1"])
def test_superseded_cuts_only_on_trust_or_the_same_from(box, signed, who, sig, cuts):
    host = _box._host()
    mail(box.d, "2.s", {"Supersedes": "<1.c@test>"}, body="the new words",
         frm=f"{who}@{host.upper() if who == 'Vera' else host}", sig=signed(sig) if sig else None)
    signed.settle(box.d)
    assert player._superseded(_box.Queue(box.d), _taken(f"vera@{host}")) is cuts


def test_superseded_never_matches_two_empty_froms(box):
    mail(box.d, "2.s", {"Supersedes": "<1.c@test>"}, body="the new words", frm="")
    assert player._superseded(_box.Queue(box.d), _taken(None)) is False


def test_a_supersede_refused_whole_never_cuts_a_taken_line(box):
    """The line's own From, but the mail asks for a file outside the sounds dir:
    EARS-335 refuses it whole, so it never plays in the line's place. Cutting the
    taken line for it would lose the line; a queued target is left untouched too."""
    host = _box._host()
    mail(box.d, "2.s", {"Supersedes": "<1.c@test>", "X-Mouth-File": "/etc/passwd"},
         body="the new words", frm=f"vera@{host}")
    (e,) = _box.Queue(box.d).entries()
    assert e["_untrusted"] and _box._same_from(e, {"_from": f"vera@{host}"})   # (b) alone would cut
    assert player._superseded(_box.Queue(box.d), _taken(f"vera@{host}")) is False


def test_a_notice_reply_never_cuts_a_taken_line(box):
    """The line's own From, but the mail is a drop notice (``Mouth-Notice``): it is
    never a line (EARS-164), so it never plays in the line's place, and must not
    close the taken line unspoken either."""
    host = _box._host()
    mail(box.d, "2.s", {"Supersedes": "<1.c@test>", "Mouth-Notice": "dropped"},
         body="the new words", frm=f"vera@{host}")
    (e,) = _box.Queue(box.d).entries()
    assert e["_notice_reply"] and _box._same_from(e, {"_from": f"vera@{host}"})   # (b) alone would cut
    assert player._superseded(_box.Queue(box.d), _taken(f"vera@{host}")) is False


def test_mouth_amend_is_a_local_drop_and_so_trusted_to_cut(box):
    """``mouth amend`` writes through ``box.drop``: the MAC line makes it local, and
    a local line's Supersedes is trusted whoever's line it names."""
    host = _box._host()
    _box.drop(_box.box_dir(box.d), {"utt": "p1", "text": "the new words", "requested_t": 1.0,
                                    "agent": "someone-else"}, supersedes="<1.c@test>")
    (e,) = _box.Queue(box.d).entries()
    assert e["_raw"] is True and e["_sup_trust"] is True
    assert player._superseded(_box.Queue(box.d), _taken(f"vera@{host}")) is True


def test_another_senders_unsigned_supersede_cannot_cut_a_taken_line(box, slow_render, monkeypatch):
    """The hazard, end to end: vera's line is taken and not yet heard; ivy's unsigned
    Supersedes names it. It is not cut: it plays, and ivy's mail plays after it, as
    news (EARS-335: an untrusted supersede that names no QUEUED line, and is no
    retract, is an ordinary line).

    No fixed sleep (EARS-367): the player's own looks are counted. Each call of
    ``player._superseded`` is one look; the test waits for five that SAW ivy's mail
    in the queue, and none of them may cut. Under load a sleep could pass with no
    look at ivy's mail at all."""
    looks = []   # (ivy's mail in the queue?, did the look cut?) per call
    ivy = []
    real = player._superseded

    def spy(q, item):
        saw = bool(ivy) and any(e.get("mail_id") == ivy[0] for e in q.entries())
        cut = real(q, item)
        looks.append((saw, cut))
        return cut

    monkeypatch.setattr(player, "_superseded", spy)   # play_one calls the module global
    vera = deliver(box, "vera's old line", sender=f"vera@{HOST}.example.net")
    th = run_player(box.d, idle=0.5)
    assert _wait_for(lambda: (_playing(box) or {}).get("mail_id") == vera)
    assert "first_frame_t" not in _playing(box)                         # taken, not audible
    ivy.append(deliver(box, "ivy's words", sender=f"ivy@{HOST}.example.net", Supersedes=vera))
    # five looks at it, or the first cut: a cut ends the looks, so it fails here at once, by name
    assert _wait_for(lambda: any(cut for _, cut in list(looks))
                     or sum(saw for saw, _ in list(looks)) >= 5), looks
    assert not any(cut for _, cut in list(looks)), looks
    assert (_playing(box) or {}).get("mail_id") == vera and not _saying(box), (
        "not cut", slow_render.self_released)
    slow_render.set()
    assert _wait_for(lambda: len(_saying(box)) == 2, 8)
    th.join(8)
    assert not th.is_alive()                                           # the player exited
    assert slow_render.self_released == []                             # the test let go of it
    assert [x["text"] for x in _saying(box)] == ["vera's old line", "ivy's words"]
    said = {x["mail_id"]: x["reason"] for x in lines(box.logs) if x["kind"] == "said"}
    assert said[vera] == "done" and "amended" not in said.values()


def test_the_same_senders_unsigned_supersede_still_cuts_its_taken_line(box, slow_render):
    """Rule (b)'s door, end to end: the line's own From (case-blind) may replace it
    before its first frame. The player's item holds ``mail_from``, not ``_from``."""
    vera = deliver(box, "vera's old line", sender=f"vera@{HOST}.example.net")
    th = run_player(box.d, idle=0.5)
    assert _wait_for(lambda: (_playing(box) or {}).get("mail_id") == vera)
    deliver(box, "vera's new line", sender=f"Vera@{HOST}.example.net".upper(), Supersedes=vera)
    assert _wait_for(lambda: len(_saying(box)) == 1, 8)
    th.join(8)
    assert [x["text"] for x in _saying(box)] == ["vera's new line"]       # the old one never spoke
    said = {x["mail_id"]: x["reason"] for x in lines(box.logs) if x["kind"] == "said"}
    assert said[vera] == "amended"
