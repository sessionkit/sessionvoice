"""A line on a ROOM device waits for the end of the user's turn by default (EARS-434).

At 03:31 a line queued 0.2 s into a 28 s turn played at once and ran 10 s over the
user: only a line that ASKED for ``--hold turn-end`` waited. Now a room line that
asks no hold is held ``turn-end`` (``hold.ROOM_HOLD``), decided by ``box.read``
where its device is known. ``--now``, ``--hold none`` (``X-Mouth-When: now``), a
played file and a call line keep today's behaviour.

The order: the line's own ask (``--hold``, ``--now``, ``X-Mouth-When``) >
$MOUTH_ROOM_HOLD > ``room_hold`` in the mouth's settings > on.

The one-line revert is ``"room_hold": false`` in mouth.json (or
``MOUTH_ROOM_HOLD=off``): ``test_the_revert_*`` proves either one gives back
the old behaviour, a no-header room line read with no hold and played over the user.
"""
from __future__ import annotations

import json
import time

import pytest

import mouth
from mouth import box as _box
from mouth import hold

from test_mouth_hold import ears  # noqa: F401  (fixture)
from test_mouth_inbox import box, deliver, lines, run_player  # noqa: F401  (fixture)
from tests import env_fence


@pytest.fixture(autouse=True)
def settings(tmp_path, monkeypatch):
    """The mouth's settings file, in tmp, empty; and no runner's $MOUTH_ROOM_HOLD."""
    path = tmp_path / "mouth.json"
    monkeypatch.setenv("MOUTH_SETTINGS", str(path))
    env_fence.delenv(monkeypatch, "MOUTH_ROOM_HOLD")

    def write(**keys):
        path.write_text(json.dumps(keys))
    return write


def read_all(box) -> list:
    """Every queued mail, oldest first, as the player reads it."""
    return [_box.read(p, box.d, sync=False) for p in sorted((box.mail / "new").iterdir())]


def only(box) -> dict:
    got = read_all(box)
    assert len(got) == 1, got
    return got[0]


def spoken(box) -> list:
    return [x["text"] for x in lines(box.logs) if x["kind"] == "saying"]


# -- the default ---------------------------------------------------------------------


def test_a_room_mail_with_no_hold_is_held_turn_end(box):
    """The 03:31 mail: no X-Mouth-When, on the room's device."""
    deliver(box, "Here is the answer")
    item = only(box)
    assert item["device"] == "null" and not _box._is_call(item["device"])
    assert (item["hold"], item["hold_from"]) == ("turn-end", hold.ROOM)


def test_a_room_say_with_no_hold_is_held_turn_end(box):
    """The local path: ``mouth say`` with no flag, on a named room device."""
    mouth.say("Here is the answer", device="AirPods Pro", d=box.d, spawn=False)
    item = only(box)
    assert (item["device"], item["hold"], item["hold_from"]) == ("AirPods Pro", "turn-end", "room")


def test_the_mail_bytes_are_unchanged(box):
    """Decided at read: a say with no hold writes no X-Mouth-When (the golden mail)."""
    mouth.say("Here is the answer", d=box.d, spawn=False)
    raw = next((box.mail / "new").iterdir()).read_bytes()
    assert b"X-Mouth-When" not in raw and b"hold_from" not in raw


def test_an_explicit_turn_end_is_the_senders_own(box):
    mouth.say("held by its sender", hold="turn-end", d=box.d, spawn=False)
    item = only(box)
    assert item["hold"] == "turn-end" and "hold_from" not in item


@pytest.mark.parametrize("how", ["say-now", "say-none", "mail-when-now"])
def test_now_or_no_hold_plays_at_once(box, how):
    if how == "say-now":
        mouth.say("urgent", priority="now", d=box.d, spawn=False)
    elif how == "say-none":
        mouth.say("no hold", hold="none", d=box.d, spawn=False)
    else:
        deliver(box, "no hold", X_Mouth_When="now")
    item = only(box)
    assert not item.get("hold") or item["hold"] == "now"
    assert item.get("hold") != "turn-end" and "hold_from" not in item


def test_hold_none_rides_the_mail_as_x_mouth_when_now(box):
    mouth.say("no hold", hold="none", d=box.d, spawn=False)
    raw = next((box.mail / "new").iterdir()).read_text()
    assert "X-Mouth-When: now" in raw


def test_an_untrusted_now_is_held_as_any_room_line(box):
    """An unsigned mail's ``X-Mouth-Priority: now`` is dropped by the trust gate; the
    line it leaves is an ordinary room line, so the default holds it."""
    deliver(box, "jump the queue", X_Mouth_Priority="now")
    item = only(box)
    assert item["priority"] is None
    assert (item["hold"], item["hold_from"]) == ("turn-end", "room")


def test_a_call_line_keeps_its_default(box):
    """A local line to a call device was unheld and stays so (the room default is the
    room's); a mail To a call's box already held, and still does."""
    mouth.say("into the call", device="call:5531", d=box.d, spawn=False)
    item = only(box)
    assert item["device"] == "call:5531" and item["hold"] is None and "hold_from" not in item


def test_a_played_file_keeps_its_default(box, tmp_path):
    """A played file (``mouth play``: a sound, a chime) is the alert lane: it plays at once."""
    wav = tmp_path / "tink.wav"
    wav.write_bytes(b"RIFF")
    mouth.play(str(wav), d=box.d, spawn=False)
    item = only(box)
    assert item["hold"] is None and "hold_from" not in item


def test_an_extra_cannot_claim_the_room_default(box):
    """``hold_from: room`` is the box's own word: a mail's extra never sets it."""
    deliver(box, "an opener", X_Mouth_When="turn-end", X_Mouth_Extra=json.dumps({"hold_from": "room"}))
    item = only(box)
    assert item["hold"] == "turn-end" and "hold_from" not in item


def test_a_hold_in_the_extras_is_the_senders_own(box):
    deliver(box, "no hold", X_Mouth_Extra=json.dumps({"hold": None}))
    item = only(box)
    assert item["hold"] is None and "hold_from" not in item


def test_say_refuses_a_hold_it_does_not_know(box):
    with pytest.raises(ValueError, match="turn-end or none"):
        mouth.say("x", hold="whenever", d=box.d, spawn=False)


def test_the_cli_takes_hold_none_and_turn_end(box, monkeypatch):
    from mouth.__main__ import main

    monkeypatch.setattr(mouth, "ensure_player", lambda d=None: False)
    assert main(["say", "--hold", "none", "one"]) == 0
    assert main(["say", "--hold", "turn-end", "two"]) == 0
    assert main(["say", "three"]) == 0
    got = {x["text"]: (x.get("hold"), x.get("hold_from")) for x in read_all(box)}
    assert got == {"one": (None, None), "two": ("turn-end", None), "three": ("turn-end", "room")}


# -- the player ----------------------------------------------------------------------


def test_a_room_line_waits_for_the_end_of_his_turn(box, ears):
    """The incident, end to end: he is mid-turn; a no-header mail arrives."""
    ears("partial")
    deliver(box, "Here is the answer")
    th = run_player(box.d, idle=0.3)
    time.sleep(0.6)
    assert spoken(box) == [], "spoke over him"
    ears("turn")
    th.join(8)
    saying = [x for x in lines(box.logs) if x["kind"] == "saying"]
    assert [x["text"] for x in saying] == ["Here is the answer"]
    assert (saying[0]["hold"], saying[0]["hold_from"]) == ("turn-end", "room")


def test_now_plays_over_him(box, ears):
    ears("partial")
    mouth.say("urgent", priority="now", d=box.d, spawn=False)
    run_player(box.d, idle=0.3).join(8)
    assert spoken(box) == ["urgent"]


def test_hold_none_plays_over_him(box, ears):
    ears("partial")
    mouth.say("no hold", hold="none", d=box.d, spawn=False)
    run_player(box.d, idle=0.3).join(8)
    assert spoken(box) == ["no hold"]


def test_a_played_file_plays_over_him(box, ears, tmp_path):
    import shutil
    import wave

    if not shutil.which("ffmpeg"):
        pytest.skip("no ffmpeg")
    wav = tmp_path / "chime.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
        w.writeframes(b"\x00\x10" * 6000)
    ears("partial")
    mouth.play(str(wav), d=box.d, spawn=False)
    run_player(box.d, idle=0.3).join(8)
    said = [x for x in lines(box.logs) if x["kind"] == "said"]
    assert said and said[0]["backend"] == "file", "the file waited for him"


def test_a_room_held_line_from_before_his_turn_is_not_stale(box, ears):
    """An opener its sender held goes stale when he starts again; a line held only by
    the room default is not an opener, so it waits and is spoken."""
    ears("turn", age_s=5)
    room = mouth.say("an ordinary line", d=box.d, spawn=False)
    opener = mouth.say("an opener", hold="turn-end", d=box.d, spawn=False)
    time.sleep(0.05)
    ears("partial")                                          # he starts again first
    th = run_player(box.d, idle=0.5)
    time.sleep(0.4)
    ears("turn")
    th.join(8)
    said = {x["utt"]: x["reason"] for x in lines(box.logs) if x["kind"] == "said"}
    assert said == {room["utt"]: "done", opener["utt"]: "stale"}


# -- the same timeout as an explicit --hold turn-end ---------------------------------


def _pair(**more) -> tuple:
    now = time.time()
    base = {"device": "null", "requested_t": now, **more}
    return {**base, "hold": "turn-end", "hold_from": "room"}, {**base, "hold": "turn-end"}


def test_the_timeout_is_an_explicit_turn_ends(ears):
    """Never waits forever: when the ears go quiet for ``idle_s`` (they died), the floor
    is free and the line plays, as an explicit turn-end line does."""
    room, explicit = _pair()
    ears("partial")
    assert hold.check(room) == hold.check(explicit) == "wait"
    later = time.time() + hold.idle_s() + 0.1
    assert hold.check(room, now=later) == hold.check(explicit, now=later) == "play"


def test_the_idle_timeout_setting_is_shared(ears, monkeypatch):
    monkeypatch.setenv("MOUTH_HOLD_IDLE_S", "0.5")
    room, explicit = _pair()
    ears("partial", age_s=0.6)
    assert hold.check(room) == hold.check(explicit) == "play"


def test_expiry_counts_from_the_end_of_his_turn_alike(ears):
    room, explicit = _pair(expires_s=1.0, expires_t=time.time() + 1.0)
    ears("partial")
    t = time.time() + 5                     # past expires_t, but he is still talking
    assert hold.check(room, now=t) == hold.check(explicit, now=t) == "wait"
    ears("turn")
    assert hold.check(room) == hold.check(explicit) == "play"
    t = time.time() + 1.5                   # past 1 s from his turn's end
    assert hold.check(room, now=t) == hold.check(explicit, now=t) == "expire"


def test_the_grace_beat_is_shared(ears, monkeypatch):
    monkeypatch.setenv("MOUTH_HOLD_GRACE_S", "0.7")
    room, explicit = _pair()
    ears("partial")
    ears("turn")
    assert hold.check(room) == hold.check(explicit) == "wait"
    t = time.time() + 0.8
    assert hold.check(room, now=t) == hold.check(explicit, now=t) == "play"


# -- the setting: flag > env > file > default ----------------------------------------


def test_the_layers(settings, monkeypatch):
    assert hold.room_hold({}) == (True, "default")
    assert hold.room_hold({"room_hold": False}) == (False, "settings")
    assert hold.room_hold({"room_hold": "off"}) == (True, "default"), "not a boolean: unset"
    monkeypatch.setenv("MOUTH_ROOM_HOLD", "on")
    assert hold.room_hold({"room_hold": False}) == (True, "env"), "the env beats the file"
    for off in ("off", "0", "no", "false", "OFF"):
        monkeypatch.setenv("MOUTH_ROOM_HOLD", off)
        assert hold.room_hold({"room_hold": True}) == (False, "env")
    monkeypatch.setenv("MOUTH_ROOM_HOLD", "  ")
    assert hold.room_hold({"room_hold": False}) == (False, "settings"), "empty env: unset"


def test_the_settings_file_turns_it_off(box, settings):
    settings(room_hold=False)
    deliver(box, "Here is the answer")
    item = only(box)
    assert item["hold"] is None and "hold_from" not in item


def test_the_env_turns_it_off(box, monkeypatch):
    monkeypatch.setenv("MOUTH_ROOM_HOLD", "off")
    deliver(box, "Here is the answer")
    assert only(box)["hold"] is None


def test_the_env_beats_the_file(box, settings, monkeypatch):
    settings(room_hold=False)
    monkeypatch.setenv("MOUTH_ROOM_HOLD", "on")
    deliver(box, "Here is the answer")
    assert only(box)["hold"] == "turn-end"


def test_the_flag_beats_the_env_both_ways(box, settings, monkeypatch):
    monkeypatch.setenv("MOUTH_ROOM_HOLD", "off")
    mouth.say("held anyway", hold="turn-end", d=box.d, spawn=False)
    monkeypatch.setenv("MOUTH_ROOM_HOLD", "on")
    settings(room_hold=True)
    mouth.say("not held", hold="none", d=box.d, spawn=False)
    got = {x["text"]: x.get("hold") for x in read_all(box)}
    assert got["held anyway"] == "turn-end" and got["not held"] != "turn-end"


# -- the one-line revert -------------------------------------------------------------


@pytest.mark.parametrize("revert", ["settings", "env"])
def test_the_revert_gives_back_the_old_behaviour(box, ears, settings, monkeypatch, revert):
    """``"room_hold": false`` in mouth.json, or ``MOUTH_ROOM_HOLD=off``: a no-header
    room line reads exactly as before (no hold, no ``hold_from``) and plays at once
    while he talks, as the 03:31 line did."""
    if revert == "settings":
        settings(room_hold=False)
    else:
        monkeypatch.setenv("MOUTH_ROOM_HOLD", "off")
    deliver(box, "Here is the answer")
    item = only(box)
    assert item["hold"] is None and "hold_from" not in item
    ears("partial")
    assert hold.check(item) == "play"
    run_player(box.d, idle=0.3).join(8)
    saying = [x for x in lines(box.logs) if x["kind"] == "saying"]
    assert [x["text"] for x in saying] == ["Here is the answer"]
    assert saying[0].get("hold") is None and "hold_from" not in saying[0]


# -- an amend re-drops the line it read ----------------------------------------------


def test_an_amend_keeps_the_mail_unheld_and_the_default_decides_again(box, settings):
    """``amend`` reads the queued line and drops it again: the room default the read
    set must not ride the new mail, or the line would become its sender's own hold."""
    item = mouth.say("first words", d=box.d, spawn=False)
    mouth.amend(item["utt"], "better words", d=box.d)
    mails = sorted((box.mail / "new").iterdir())
    raw = mails[-1].read_bytes()
    assert b"better words" in raw
    assert b"X-Mouth-When" not in raw and b"hold_from" not in raw
    settings(room_hold=False)
    assert _box.read(mails[-1], box.d, sync=False)["hold"] is None


def test_an_amend_keeps_a_lines_own_no_hold(box):
    """A ``--hold none`` line amended is still the sender's no-hold, not a room line."""
    item = mouth.say("first words", hold="none", d=box.d, spawn=False)
    mouth.amend(item["utt"], "better words", d=box.d)
    mails = sorted((box.mail / "new").iterdir())
    assert "X-Mouth-When: now" in mails[-1].read_text()
    got = _box.read(mails[-1], box.d, sync=False)
    assert got["hold"] is None and "hold_from" not in got


def _take(box, utt):
    """Take the queued line UTT as the player does (player.py: ``q.take``, then
    playing.json = the line's public keys and its ``box_file``), with no first frame:
    taken, not yet heard, so ``amend`` replaces it."""
    q = _box.Queue(box.d)
    x = q.find(utt)
    taken = q.take(x)
    (box.d / "playing.json").write_text(json.dumps({**_box.public(x), "box_file": str(taken)},
                                                   default=str))


def test_an_amend_of_a_taken_line_keeps_its_own_no_hold(box):
    """playing.json holds public keys only, so a taken ``--hold none`` line has no
    ``_unheld``: ``amend`` reads it from the line's mail in cur/, or the amended
    line would be held for the user's turn after all."""
    item = mouth.say("first words", hold="none", d=box.d, spawn=False)
    _take(box, item["utt"])
    mouth.amend(item["utt"], "better words", d=box.d)
    mails = sorted((box.mail / "new").iterdir())
    assert "X-Mouth-When: now" in mails[-1].read_text()
    got = _box.read(mails[-1], box.d, sync=False)
    assert got["hold"] is None and "hold_from" not in got


def test_an_amend_of_a_taken_room_line_is_decided_again(box, settings):
    """A taken line the room default held is still the reader's hold, not its sender's."""
    item = mouth.say("first words", d=box.d, spawn=False)
    _take(box, item["utt"])
    mouth.amend(item["utt"], "better words", d=box.d)
    mails = sorted((box.mail / "new").iterdir())
    raw = mails[-1].read_bytes()
    assert b"X-Mouth-When" not in raw and b"hold_from" not in raw
    assert _box.read(mails[-1], box.d, sync=False)["hold_from"] == "room"
    settings(room_hold=False)
    assert _box.read(mails[-1], box.d, sync=False)["hold"] is None


def test_asked_no_hold_is_false_for_a_mail_that_has_gone(tmp_path):
    assert _box.asked_no_hold(tmp_path / "gone") is False
    assert _box.asked_no_hold(None) is False
