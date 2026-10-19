"""A call's box (/RULINGS/260): mail to calls+<bot>-<id> is a line for that call.

Since EARS-335 only a SIGNED mail (or a local drop) routes into a call: a v1 signature
covers a call box that is the ``To`` it signs, a v2 one covers ``To`` and the
``X-Mouth-*`` headers it names. These mails are signed by the ``signed`` fake
(tests/mouth/conftest.py); an unsigned one is refused ``untrusted``."""
from __future__ import annotations

import json

import pytest

from mouth import box

from maildir_put import put
from tests import env_fence


@pytest.fixture
def d(tmp_path, monkeypatch):
    d = tmp_path / "mouth"
    monkeypatch.setenv("MOUTH_DIR", str(d))
    monkeypatch.setenv("MOUTH_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MOUTH_DEVICE", "null")
    monkeypatch.setenv("MOUTH_BACKEND", "silence")
    monkeypatch.setattr("mouth.voices.clones", lambda: ["ivy", "other"])
    monkeypatch.setattr(box, "_CLONES", (float("-inf"), ()))
    env_fence.delenv(monkeypatch, "MOUTH_MAILBOX")
    env_fence.delenv(monkeypatch, "MOUTH_MAIL_LOG")
    env_fence.delenv(monkeypatch, "MOUTH_VOICE")
    return d


def mail(d, name, sender, to, body="Hello from the box.", orig=None, **headers):
    bx = box.ensure(box.box_dir(d))
    host = box._host()
    lines = [f"From: {sender}@{host}", f"To: {to}", f"Subject: {body[:40]}",
             f"Message-ID: <{name}@test>"]
    if orig:
        lines.insert(0, f"X-Original-To: {orig}")
    lines += [f"{k.replace('_', '-')}: {v}" for k, v in headers.items()]
    p = put(bx, name, "\n".join(lines) + "\n\n" + body + "\n")
    return p


@pytest.mark.parametrize("addr,want", [
    ("calls+ivy-1317@host1.example.net", ("ivy", 1317)),
    ("calls+ivy-1317", ("ivy", 1317)),
    ("Calls+Vera-5@host1", ("vera", 5)),
    ("calls+ivy-public-17@host1", ("ivy-public", 17)),
    ("mouth@host1", None),
    ("calls@host1", None),
    ("heard+airpods@host1", None),
    ("", None),
])
def test_call_box_names_the_bot_and_the_call(addr, want):
    assert box.call_box(addr) == want


def test_call_box_takes_the_first_box_of_several():
    assert box.call_box(None, "mouth@host1, Ivy <calls+ivy-9@host1>") == ("ivy", 9)


def test_a_line_to_a_call_box_plays_into_that_call_politely_in_its_own_voice(d, signed):
    mail(d, "a", "ivy", "calls+ivy-1317@host1", orig="calls+ivy-1317", X_Session_Signature=signed("1"))
    x = box.read(next((box.box_dir(d) / "new").iterdir()), d)
    assert x["device"] == "call:1317"
    assert x["hold"] == "turn-end"
    assert x["voice"] == "ivy"
    assert x["agent"] == "ivy"
    assert not x["_heard"] and not x.get("_untrusted")


def test_the_same_line_unsigned_is_refused_and_noticed(d, tmp_path):
    mail(d, "a", "ivy", "calls+ivy-1317@host1", orig="calls+ivy-1317")
    q = box.Queue(d)
    assert q.lines() == [], "a reader leaves it out"
    assert q.lines(act=True) == []
    assert list((box.box_dir(d) / "cur").glob("a:2,T"))
    rows = [json.loads(x) for f in sorted((tmp_path / "logs").glob("heard_*.jsonl"))
            for x in f.read_text().splitlines()]
    (row,) = [r for r in rows if r["kind"] == "said" and r.get("mail_id") == "<a@test>"]
    assert row["reason"] == "untrusted"
    assert row["detail"] == "To calls+ivy-1317 needs a signed or local line (unsigned)"
    assert row["notice"].startswith("sent ivy@") or row["notice"].startswith("none:"), row["notice"]


def test_the_original_to_wins_for_a_subscriber_copy(d, signed):
    # the mouth's copy arrives To the box but Delivered-To the mouth
    mail(d, "a", "ivy", "calls+ivy-1317@host1", orig="calls+ivy-1317", Delivered_To="mouth@host1",
         X_Session_Signature=signed("1"))
    assert box.read(box.box_dir(d) / "new" / "a", d)["device"] == "call:1317"


def test_a_barge_line_does_not_wait(d, signed):
    mail(d, "a", "ivy", "calls+ivy-1317@host1", X_Mouth_Priority="now",
         X_Session_Signature=signed("2", "x-mouth-priority"))
    mail(d, "b", "ivy", "calls+ivy-1317@host1", X_Mouth_When="now",
         X_Session_Signature=signed("2", "x-mouth-when"))
    assert box.read(box.box_dir(d) / "new" / "a", d)["hold"] is None
    assert box.read(box.box_dir(d) / "new" / "b", d)["hold"] is None


def test_its_own_headers_still_win(d):
    mail(d, "a", "ivy", "calls+ivy-1317@host1", X_Mouth_Device="airpods", X_Mouth_Voice="af_sky")
    x = box.read(box.box_dir(d) / "new" / "a", d)
    assert (x["device"], x["voice"]) == ("airpods", "af_sky")


def test_a_sender_with_no_voice_of_its_own_gets_the_default(d, signed):
    mail(d, "a", "vera", "calls+ivy-1317@host1", X_Session_Signature=signed("1"))
    x = box.read(box.box_dir(d) / "new" / "a", d)
    assert x["voice"] == box.default_voice(d)[0]
    assert x["device"] == "call:1317"


def test_mail_to_the_mouth_is_unchanged(d):
    mail(d, "a", "ivy", "mouth@host1")
    x = box.read(box.box_dir(d) / "new" / "a", d)
    assert x["device"] == "null"
    assert x["hold"] is None
    assert x["voice"] == box.default_voice(d)[0]


def test_the_ears_own_turns_are_filed_unspoken(d, capsys, signed):
    mail(d, "turn", "ears", "calls+ivy-1317@host1", body="Okay, please confirm.",
         orig="calls+ivy-1317", Ears_Kind="turn", Ears_Seq="215130")
    mail(d, "line", "ivy", "calls+ivy-1317@host1", body="It arrived.", orig="calls+ivy-1317",
         X_Session_Signature=signed("1"))
    q = box.Queue(d)
    assert [x["text"] for x in q.lines(act=True)] == ["It arrived."]
    out = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.startswith("{")]
    assert {"skipped": "heard", "mail_id": "<turn@test>"} in out
    assert not (box.box_dir(d) / "new" / "turn").exists()
    assert list((box.box_dir(d) / "cur").glob("turn*T"))


def test_ears_kind_outside_a_call_box_is_left_alone(d):
    mail(d, "a", "ears", "mouth@host1", Ears_Kind="turn")
    assert not box.read(box.box_dir(d) / "new" / "a", d)["_heard"]


def test_a_voice_the_resolver_does_not_know_is_not_used(d, monkeypatch, tmp_path):
    # a bare directory named for the sender is not a voice (it would play silent)
    (tmp_path / "voices" / "vera").mkdir(parents=True)
    monkeypatch.setenv("VOICEMODE_VOICES_DIR", str(tmp_path / "voices"))
    mail(d, "a", "vera", "calls+ivy-1317@host1")
    assert box.read(box.box_dir(d) / "new" / "a", d)["voice"] == box.default_voice(d)[0]


def test_the_clone_list_is_looked_up_once_a_minute(d, monkeypatch):
    calls = []
    monkeypatch.setattr("mouth.voices.clones", lambda: calls.append(1) or ["ivy"])
    for n in "abc":
        mail(d, n, "ivy", "calls+ivy-1317@host1")
        assert box.read(box.box_dir(d) / "new" / n, d)["voice"] == "ivy"
    assert len(calls) == 1


def test_a_resolver_that_breaks_falls_back_to_the_default_voice(d, monkeypatch):
    def boom():
        raise RuntimeError("bad profile")
    monkeypatch.setattr("mouth.voices.clones", boom)
    mail(d, "a", "ivy", "calls+ivy-1317@host1")
    x = box.read(box.box_dir(d) / "new" / "a", d)
    assert (x["voice"], x["device"]) == (box.default_voice(d)[0], "call:1317")
