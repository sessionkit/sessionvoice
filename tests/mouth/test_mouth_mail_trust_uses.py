"""What a LOCAL or SIGNED line may still make the mouth do (EARS-335 design §7).

The other side of ``test_mouth_mail_trust.py``. A local drop (``mouth say``, ``play``,
``amend``) carries a MAC only the box's own writer can mint, and keeps every ask: a file
anywhere, ``call:N``, ``now``, a retract that cuts. A signed mail (the ``signed`` fake:
the verifier says yes, and its ``--json`` verdict names the ``h=`` and values the mail
carries) keeps the asks its signature covers, and only while the value it acts on is
the value signed (EARS-345). Any other mail plays a sound from the sounds directory and nothing
more, and a refused line is filed ``untrusted``, said and noticed.

In tmp_path, on the ``null`` device and the ``silence`` backend; nothing runs ffmpeg or
the real verifier (``$MOUTH_VERIFY_BIN`` is the fence; a test that asks for
the real lookup puts a fake ``message`` on PATH).
"""

import json
import os
import shutil
import threading
from pathlib import Path

import pytest

import mouth
from mouth import box, player

from maildir_put import put
from tests import env_fence

PLAYING_ID = "<playing@test>"


@pytest.fixture
def d(tmp_path, monkeypatch):
    d = tmp_path / "mouth"
    monkeypatch.setenv("MOUTH_DIR", str(d))
    monkeypatch.setenv("MOUTH_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MOUTH_DEVICE", "null")
    monkeypatch.setenv("MOUTH_BACKEND", "silence")
    env_fence.delenv(monkeypatch, "MOUTH_MAILBOX")
    env_fence.delenv(monkeypatch, "MOUTH_MAIL_LOG")
    env_fence.delenv(monkeypatch, "MOUTH_SOUNDS_DIR")
    box.ensure(box.box_dir(d))
    return d


def mail(d, name, headers=(), body="hello there", frm=None, to=None, sig=None):
    """A mail delivered into new/ (tmp/, then a rename): an on-host From, signed only if SIG."""
    host = box._host()
    lines = [f"From: {frm or f'ivy@{host}'}", f"To: {to or f'mouth@{host}'}",
             f"Subject: {body[:40]}", f"Message-ID: <{name}@test>"]
    if sig:
        lines.append(f"X-Session-Signature: {sig}")
    lines += [f"{k}: {v}" for k, v in dict(headers).items()]
    p = put(box.box_dir(d), name, "\n".join(lines) + "\n\n" + body + "\n")
    return p


def said(tmp_path) -> dict:
    rows = [json.loads(x) for f in sorted((tmp_path / "logs").glob("heard_*.jsonl"))
            for x in f.read_text().splitlines()]
    return {r["mail_id"]: r for r in rows if r["kind"] == "said" and r.get("mail_id")}


def calls(q) -> list:
    return [x for x in q.lines() if box._is_call(x.get("device"))]


# -- a local drop: the MAC -----------------------------------------------------------

def test_a_drop_is_local_and_nothing_else_is(d, capsys):
    item = mouth.say("mine", d=d, spawn=False)
    (p,) = (box.box_dir(d) / "new").iterdir()
    raw = p.read_bytes()
    assert raw.startswith(b"X-Mouth-Local: v1 ") and len(raw.split(b"\n", 1)[0]) == 18 + 64
    assert box.read(p, d)["_raw"] is True
    key = box.box_dir(d) / box.KEY_NAME
    assert key.stat().st_mode & 0o777 == 0o600 and len(key.read_bytes()) == 32

    mac, rest = raw.split(b"\n", 1)
    for how, forged in {
        "a header added": mac + b"\nX-Mouth-Device: call:1317\n" + rest,
        "the body edited": raw.replace(b"mine", b"yours"),
        "the MAC line moved": rest.replace(b"\n\n", b"\n" + mac + b"\n\n", 1),
        "the MTA's headers on top": b"Return-Path: <ivy@x>\n" + raw,
    }.items():
        put(p.parent.parent, p.name, forged)     # tmp/, then a rename over the drop
        x = box.read(p, d)
        assert x["_raw"] is False, how
    put(p.parent.parent, p.name, raw)
    key.unlink()
    assert box.read(p, d)["_raw"] is False, "the key deleted: no line is local"
    assert not key.exists(), "read() never makes the key"
    assert item["mail_id"]


def test_a_forged_raw_header_is_read_as_mail_from_outside(d, capsys):
    p = mail(d, "1.r", {"X-Mouth-Raw": "1"}, body="**bold** words")
    x = box.read(p, d)
    assert x["_raw"] is False and x["text"] == "bold words" and x["notice_to"]
    logged = [json.loads(s) for s in capsys.readouterr().err.splitlines() if s.startswith("{")]
    assert {"forged_raw": "<1.r@test>", "from": f"ivy@{box._host()}"} in logged


def test_two_first_drops_share_one_key(d):
    bx = box.box_dir(d)
    keys = []
    ts = [threading.Thread(target=lambda: keys.append(box._key(bx, create=True))) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(set(keys)) == 1 and len(keys[0]) == 32
    assert not [n for n in os.listdir(bx) if n.endswith(".part")]


def test_a_local_line_keeps_a_call_now_and_a_file_anywhere(d, tmp_path):
    wav = tmp_path / "anywhere" / "tone.wav"
    wav.parent.mkdir()
    wav.write_bytes(b"RIFF....WAVE")
    first = mouth.say("queued first", d=d, spawn=False)
    now = mouth.say("into the call, now", device="call:1317", priority="now", d=d, spawn=False)
    snd = mouth.play(str(wav), d=d, spawn=False)
    lines = {x["utt"]: x for x in box.Queue(d).lines()}
    x = lines[now["utt"]]
    assert (x["device"], x["priority"], x["_rank"]) == ("call:1317", "now", -1.0)
    assert lines[snd["utt"]]["file"] == str(wav.resolve())
    playing = {"utt": first["utt"], "mail_id": "<x@y>", "requested_t": 0.0, "device": "null"}
    assert player._cut_by_mail(box.Queue(d), playing) == "interrupted"


def test_an_amended_local_line_keeps_its_file_and_its_now(d, tmp_path):
    wav = tmp_path / "anywhere" / "tone.wav"
    wav.parent.mkdir()
    wav.write_bytes(b"RIFF....WAVE")
    snd = mouth.play(str(wav), d=d, spawn=False)
    now = mouth.say("now line", priority="now", d=d, spawn=False)
    mouth.amend(snd["utt"], "[sound renamed]", d=d)
    mouth.amend(now["utt"], "now line, amended", d=d)
    lines = {x["utt"]: x for x in box.Queue(d).lines()}
    assert lines[snd["utt"]]["file"] == str(wav.resolve())
    assert (lines[now["utt"]]["text"], lines[now["utt"]]["_rank"]) == ("now line, amended", -1.0)


# -- the sounds directory -------------------------------------------------------------

def test_any_admitted_mail_plays_a_sound_from_the_sounds_dir(d, tmp_path, monkeypatch):
    root = tmp_path / "sounds"
    (root / "sub").mkdir(parents=True)
    tink = root / "sub" / "tink.wav"
    tink.write_bytes(b"RIFF....WAVE")
    outside = tmp_path / "private.wav"
    outside.write_bytes(b"RIFF....WAVE")
    (root / "link.wav").symlink_to(outside)
    monkeypatch.setenv("MOUTH_SOUNDS_DIR", str(root))
    mail(d, "1.ok", {"X-Mouth-File": str(tink)})
    mail(d, "2.dots", {"X-Mouth-File": f"{root}/sub/../../private.wav"})
    mail(d, "3.link", {"X-Mouth-File": str(root / "link.wav")})
    mail(d, "4.url", {"X-Mouth-File": f"file://{tink}"})
    mail(d, "5.none", {"X-Mouth-File": str(root / "sub" / "absent.wav")})
    lines = box.Queue(d).lines()
    assert [(x["mail_id"], x["file"]) for x in lines] == [("<1.ok@test>", os.path.realpath(tink))]


def test_signing_never_widens_the_file_rule(d, tmp_path, signed):
    outside = tmp_path / "private.wav"
    outside.write_bytes(b"RIFF....WAVE")
    mail(d, "1.s", {"X-Mouth-File": str(outside)}, sig=signed("2", "x-mouth-file"))
    assert box.Queue(d).lines() == []


def test_the_sounds_dir_defaults_beside_the_voices(d):
    assert box.sounds_root(d) == d / "sounds"
    assert not (d / "sounds").exists(), "never made by the mouth"


def test_play_refuses_a_url_before_anything_is_dropped(d):
    with pytest.raises(ValueError, match="URL is not played"):
        mouth.play("https://example.invalid/a.mp3", d=d, spawn=False)
    assert list((box.box_dir(d) / "new").iterdir()) == []


# -- a signed mail: what its signature covers ------------------------------------------

def test_a_signed_v2_mail_routes_into_a_call_and_cuts_in(d, signed):
    mail(d, "1.a", body="queued first")
    mail(d, "2.c", {"X-Mouth-Device": "call:1317", "X-Mouth-Priority": "now"}, body="barge",
         sig=signed("2", "x-mouth-device", "x-mouth-priority"))
    signed.settle(d)   # a reader never waits on the verifier: let the verdicts land
    q = box.Queue(d)
    (x,) = calls(q)
    assert (x["device"], x["priority"], x["_rank"], x["hold"]) == ("call:1317", "now", -1.0, None)
    playing = {"utt": "p1", "mail_id": PLAYING_ID, "requested_t": 0.0, "device": "null"}
    assert player._cut_by_mail(box.Queue(d), playing) == "interrupted"


def test_a_signed_mail_counts_only_what_its_h_covers(d, signed):
    mail(d, "1.dev", {"X-Mouth-Device": "call:1317"}, sig=signed("2"))
    mail(d, "2.now", {"X-Mouth-Priority": "now"}, sig=signed("2"))
    mail(d, "3.ext", {"X-Mouth-Extra": json.dumps({"device": "call:1317"})},
         sig=signed("2", "x-mouth-device"))
    # the field that WON is judged: an extra's now, under an h= naming only the header
    mail(d, "4.exn", {"X-Mouth-Extra": json.dumps({"priority": "now"})}, sig=signed("2", "x-mouth-priority"))
    signed.settle(d)
    lines = {x["mail_id"]: x for x in box.Queue(d).lines()}
    assert set(lines) == {"<2.now@test>", "<4.exn@test>"}, "an uncovered call is refused whole"
    for mid in ("<2.now@test>", "<4.exn@test>"):
        assert (lines[mid]["priority"], lines[mid]["_rank"]) == (None, box.DEFAULT_RANK), mid
    detail = box.read(box.box_dir(d) / "new" / "1.dev", d)["_untrusted"]
    assert detail == ("X-Mouth-Device call:1317 needs a signed or local line "
                      "(signed v2, but h= does not cover x-mouth-device)")


def test_two_copies_of_a_covered_field_are_not_covered(d, signed):
    p = mail(d, "1.c", {"X-Mouth-Device": "call:1317"}, sig=signed("2", "x-mouth-device"))
    put(p.parent.parent, p.name, p.read_text().replace("X-Mouth-Device: call:1317\n",
                                                       "X-Mouth-Device: call:1317\nX-Mouth-Device: call:9\n"))
    assert "signs 2 x-mouth-device fields" in box.read(p, d)["_untrusted"]


def test_a_signed_mail_to_a_call_box_plays_into_that_call(d, signed):
    mail(d, "1.v1", to=f"calls+ivy-1317@{box._host()}", sig=signed("1"))
    mail(d, "2.v2", to=f"Ivy <calls+ivy-1318@{box._host()}>", sig=signed("2"))
    signed.settle(d)
    assert sorted(x["device"] for x in calls(box.Queue(d))) == ["call:1317", "call:1318"]


@pytest.mark.parametrize("to", ["mouth@h.example, calls+ivy-1317@h.example",
                                "calls+ivy-1317@h.example, <mouth@h.example>",
                                "Mouth <mouth@h.example>, calls+ivy-1317@h.example"])
def test_v1_signs_one_reduction_of_to_and_a_call_box_beside_it_is_not_covered(d, signed, to):
    assert box.call_box(to) == ("ivy", 1317)
    p = mail(d, "1.v1", to=to, sig=signed("1"))
    assert "signed v1, which signs only its To 'mouth'" in box.read(p, d)["_untrusted"]


def test_v1_never_covers_a_call_box_the_mta_added(d, signed):
    host = box._host()
    p = mail(d, "1.v1", {"X-Original-To": "calls+ivy-1317", "Delivered-To": f"calls+ivy-1317@{host}"},
             sig=signed("1"))
    assert box.read(p, d)["_untrusted"]


def test_v1_keeps_a_call_box_line_polite(d, signed):
    host = box._host()
    mail(d, "1.p", {"X-Mouth-Priority": "now"}, to=f"calls+ivy-1317@{host}", sig=signed("1"))
    mail(d, "2.w", {"X-Mouth-When": "now"}, to=f"calls+ivy-1317@{host}", sig=signed("1"))
    mail(d, "3.v2", {"X-Mouth-When": "now"}, to=f"calls+ivy-1317@{host}", sig=signed("2", "x-mouth-when"))
    mail(d, "4.x", {"X-Mouth-Extra": json.dumps({"hold": None})}, to=f"calls+ivy-1317@{host}", sig=signed("1"))
    signed.settle(d)
    holds = {x["mail_id"]: (x["hold"], x["priority"]) for x in calls(box.Queue(d))}
    assert holds == {"<1.p@test>": ("turn-end", None), "<2.w@test>": ("turn-end", None),
                     "<3.v2@test>": (None, None), "<4.x@test>": ("turn-end", None)}


#: A v1 signature signs a FIXED list (From and To reduced, Subject, Date, Message-ID,
#: the text: sessionmail ``session-mail-verify`` ``canonical()``). Its own ``h=`` is not
#: in what it signs, so a relay may write any ``h=`` on a captured v1 mail and it still
#: verifies (review of fix-001).
_V1_WIDE = ("v=1; a=sshsig; n=test; h=from:to:subject:date:message-id:x-mouth-device:"
            "x-mouth-priority:x-mouth-when:x-mouth-extra:supersedes; b=fake")


def test_a_v1_h_is_not_signed_so_it_covers_no_mouth_header(d, signed):
    host = box._host()
    mail(d, "1.dev", {"X-Mouth-Device": "call:1317"}, sig=_V1_WIDE)
    mail(d, "2.now", {"X-Mouth-Priority": "now"}, sig=_V1_WIDE)
    mail(d, "3.ext", {"X-Mouth-Extra": json.dumps({"priority": "now"})}, sig=_V1_WIDE)
    mail(d, "4.when", {"X-Mouth-When": "now"}, to=f"calls+ivy-1317@{host}", sig=_V1_WIDE)
    mail(d, "5.neg", {"X-Mouth-Priority": "-5"}, sig=_V1_WIDE)
    signed.settle(d)
    lines = {x["mail_id"]: x for x in box.Queue(d).lines()}
    assert "<1.dev@test>" not in lines, "a relay's call:N on a v1 mail is refused whole"
    assert box.read(box.box_dir(d) / "new" / "1.dev", d)["_untrusted"] == (
        "X-Mouth-Device call:1317 needs a signed or local line "
        "(signed v1, which does not sign x-mouth-device)")
    for mid in ("<2.now@test>", "<3.ext@test>", "<5.neg@test>"):
        assert (lines[mid]["priority"], lines[mid]["_rank"]) == (None, box.DEFAULT_RANK), mid
    assert (lines["<4.when@test>"]["device"], lines["<4.when@test>"]["hold"]) == ("call:1317", "turn-end")
    playing = {"utt": "p1", "mail_id": PLAYING_ID, "requested_t": 0.0, "device": "null"}
    assert player._cut_by_mail(box.Queue(d), playing) is None


_H = "h=from:to:subject:date:message-id:supersedes:x-mouth-device:x-mouth-priority"


def test_a_non_ascii_signature_header_is_never_read_as_v2(d, signed):
    """The verifier picked v1 or v2 from the mail read as UTF-8 TEXT (before SM-787),
    where ``str.strip()`` takes a Unicode space (U+00A0 and the like) off a tag's key.
    So a relay rewrites a captured v1 mail's header to ``v=2; ... h=<wide>; b=<the v1
    b>; <U+00A0>v=1``, and the verifier keeps the last ``v=1``. The mouth no longer reads
    the header (EARS-345): the verdict says v1, which signs no header of the mouth's."""
    host = box._host()
    wide = (f"v=2; a=sshsig; n=test; {_H}:x-mouth-extra:x-mouth-when; b=fake; \u00a0v=1")
    mail(d, "1.c", body="ivy's own line")
    mail(d, "2.dev", {"X-Mouth-Device": "call:1317", "X-Mouth-Priority": "now"}, sig=wide,
         frm=f"vera@{host}")
    mail(d, "3.sup", {"Supersedes": "<1.c@test>"}, body="vera's words", sig=wide, frm=f"vera@{host}")
    signed.settle(d)
    assert [x["text"] for x in box.Queue(d).lines()] == ["ivy's own line"], \
        "neither the call line nor the take is played"
    assert box.read(box.box_dir(d) / "new" / "2.dev", d)["_untrusted"] == (
        "X-Mouth-Device call:1317 needs a signed or local line "
        "(signed v1, which does not sign x-mouth-device)")
    playing = {"utt": "p1", "mail_id": PLAYING_ID, "requested_t": 0.0, "device": "null"}
    assert player._cut_by_mail(box.Queue(d), playing) is None


def test_an_upper_case_h_name_covers_nothing(d, signed):
    """The verifier's v2 ``canonical()`` matches each ``h=`` name, AS WRITTEN, to the
    lower-cased field names (sessionmail ``_sig.canonical``, ``n == name``). An upper-case
    name matches no field, so it signs that field as empty whatever the mail carries, and
    anyone holding the mail may add one. The verdict lists the name as signed
    (``X-Mouth-Device``, ``[""]``), and the mouth looks up its gate's lower-case name
    exactly, so it covers nothing."""
    up = signed("2").replace("; p=", ": X-Mouth-Device: X-Mouth-Priority; p=")
    assert ": X-Mouth-Device: X-Mouth-Priority; p=" in up
    mail(d, "1.dev", {"X-Mouth-Device": "call:1317"}, sig=up)
    mail(d, "2.now", {"X-Mouth-Priority": "now"}, sig=up)
    signed.settle(d)
    lines = {x["mail_id"]: x for x in box.Queue(d).lines()}
    assert "<1.dev@test>" not in lines, "an upper-case h= name covers nothing"
    assert box.read(box.box_dir(d) / "new" / "1.dev", d)["_untrusted"] == (
        "X-Mouth-Device call:1317 needs a signed or local line "
        "(signed v2, but h= does not cover x-mouth-device)")
    assert (lines["<2.now@test>"]["priority"], lines["<2.now@test>"]["_rank"]) == (None, box.DEFAULT_RANK)


def test_a_field_hidden_behind_a_bare_cr_is_not_covered(d, tmp_path, signed):
    """The verifier verifies the fields ``_sig.fields()`` splits on LF alone; Python's
    parser (the mouth's) also ends a line at a bare CR. So a relay can hide a field from
    the signature inside another field's value: a ``Supersedes`` (a floor name, signed
    as empty when absent), or a second, wider ``X-Session-Signature`` read ahead of the
    one verified. sessionmail now refuses a bare CR (SM-787); even a verifier that did
    not, the mouth checks the value it acts on against the one signed (EARS-345)."""
    host = box._host()
    mail(d, "1.c", body="vera's own line", frm=f"vera@{host}")
    mail(d, "2.p", {"X-Relay": "x\rSupersedes: <1.c@test>"}, body="ivy's words", sig=signed("2"))
    mail(d, "3.dev", {"X-Relay": f"x\rX-Session-Signature: {signed('2', 'x-mouth-device')}",
                      "X-Session-Signature": signed("2"), "X-Mouth-Device": "call:1317"})
    assert b"\rSupersedes" in (box.box_dir(d) / "new" / "2.p").read_bytes()
    signed.settle(d)
    lines = box.Queue(d).lines(act=True)
    assert [x["text"] for x in lines] == ["vera's own line"], "neither is played"
    assert said(tmp_path)["<2.p@test>"]["detail"] == (
        f"Supersedes <1.c@test> names a line from vera@{host}: "
        "needs that sender, or a signed or local line (signed v2, but it signs no supersedes)")
    assert box.read(box.box_dir(d) / "cur" / "3.dev:2,T", d)["_untrusted"] == (
        "X-Mouth-Device call:1317 needs a signed or local line "
        "(signed v2, but h= does not cover x-mouth-device)")


def test_an_unsigned_number_beside_an_extras_now_keeps_its_number(d):
    mail(d, "1.n", {"X-Mouth-Priority": "10", "X-Mouth-Extra": json.dumps({"priority": "now"})})
    (x,) = box.Queue(d).lines()
    assert (x["priority"], x["_rank"]) == (None, 10.0)


# -- the sender's own header, end to end -----------------------------------------------

_FLOOR2 = ["from", "to", "cc", "subject", "date", "message-id", "in-reply-to", "references",
           "supersedes", "mime-version", "content-type", "x-session-from"]
_DIG = "A" * 43 + "="
_B64 = "AAAAC3NzaC1lZDI1NTE5AAAAIA" * 6 + "ab=="


def _v2_header(names=(), p=(_DIG, _DIG)) -> str:
    """An ``X-Session-Signature`` folded exactly as the sender folds it (sessionmail
    ``_sig.format_header`` at 5dc20f8: h= at 78 columns, one p= entry a line, b= in 72s)."""
    h = [*_FLOOR2, *names]
    lines = ["X-Session-Signature: v=2; a=sshsig; n=session-mail@example.com;"]
    cur = " h=" + h[0]
    for name in h[1:]:
        if len(cur) + 1 + len(name) + 1 > 78:
            lines.append(cur + ":")
            cur = " " + name
        else:
            cur += ":" + name
    lines.append(cur + ";")
    lines += [(" p=" if i == 0 else " ") + d + ("," if i < len(p) - 1 else ";") for i, d in enumerate(p)]
    lines += [" b=" + _B64[:72]] + [" " + _B64[i:i + 72] for i in range(72, len(_B64), 72)]
    return "\n".join(lines)


def test_an_honest_folded_v2_mail_still_routes_and_cuts_and_a_rewritten_one_does_not(d, signed):
    """End to end: the sender's own folded header keeps its call and its ``now``; the
    same mail with the header rewritten (a last ``v=1``) is judged as the verdict reads
    it, v1, which signs no X-Mouth-* header."""
    host = box._host()
    sig = _v2_header(["x-mouth-device", "x-mouth-priority"]).split(": ", 1)[1]
    mail(d, "1.a", body="queued first")
    mail(d, "2.c", {"X-Mouth-Device": "call:1317", "X-Mouth-Priority": "now"}, body="barge",
         sig=sig, frm=f"vera@{host}")
    mail(d, "3.c", {"X-Mouth-Device": "call:1317", "X-Mouth-Priority": "now"}, body="forged",
         sig=sig + "; v=1", frm=f"vera@{host}")
    signed.settle(d)
    lines = {x["mail_id"]: x for x in box.Queue(d).lines()}
    assert (lines["<2.c@test>"]["device"], lines["<2.c@test>"]["priority"]) == ("call:1317", "now")
    assert "<3.c@test>" not in lines
    assert box.read(box.box_dir(d) / "new" / "3.c", d)["_untrusted"] == (
        "X-Mouth-Device call:1317 needs a signed or local line "
        "(signed v1, which does not sign x-mouth-device)")


# -- a refused line: filed T, said untrusted, noticed ------------------------------------

def test_a_refused_line_is_filed_said_and_noticed(d, tmp_path, monkeypatch):
    mail(d, "1.c", {"X-Mouth-Device": "call:1317"}, body="for the caller")
    q = box.Queue(d)
    assert q.lines(act=True) == []
    assert not (box.box_dir(d) / "new" / "1.c").exists()
    assert list((box.box_dir(d) / "cur").glob("1.c:2,T"))
    row = said(tmp_path)["<1.c@test>"]
    assert row["reason"] == "untrusted" and row["cut"] and row["played_s"] == 0.0
    assert row["detail"] == "X-Mouth-Device call:1317 needs a signed or local line (unsigned)"
    assert row["notice"].startswith("sent ivy@"), row["notice"]


# -- fail closed: no verifier, the switch, and the real lookup ---------------------------

#: A good v2 verdict (sessionmail golden ``16-trust-unblessed``, cut to three names).
def _verdict(rc: int = 4, **over) -> dict:
    trust = ({"verdict": "FULL", "leg": None, "reason": None, "agent": "ivy", "line": "TRUST: FULL"}
             if rc == 0 else
             {"verdict": "UNBLESSED", "leg": None, "reason": "bless-absent", "agent": "ivy",
              "line": "TRUST: UNBLESSED reason=bless-absent"})
    sig = {"version": "2", "h": ["from", "to", "x-mouth-device"],
           "covered": [{"name": "from", "values": ["ivy@h.example"]},
                       {"name": "to", "values": ["mouth@h.example"]},
                       {"name": "x-mouth-device", "values": ["call:1317"]}],
           "parts": [{"index": 1, "type": "text/plain", "digest": _DIG}],
           "signer": {"key": "ssh-ed25519 AAAAfake", "session_id": "fake"}}
    v = {"schema": 1, "rc": rc, "result": "good", "reason": trust["reason"], "signature": sig,
         "trust": trust}
    for k, val in over.items():
        where, _, key = k.partition("__")
        if key:
            v[where][key] = val
        else:
            v[where] = val
    return v


def _line(v) -> bytes:
    return (json.dumps(v, sort_keys=True) + "\n").encode("ascii")


_SIGNED = box.Signed("2", {"from": ("ivy@h.example",), "to": ("mouth@h.example",),
                           "x-mouth-device": ("call:1317",)})


def fake_message(tmp_path, monkeypatch, rc: int, out: bytes = b"") -> Path:
    """``message`` on PATH: logs its argv, prints OUT only when asked ``--json`` (as the
    real one does: an older one has no ``--json`` and prints nothing), exits RC."""
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    (tmp_path / "verdict.out").write_bytes(out)
    m = bin_ / "message"
    m.write_text(f"#!/bin/sh\necho \"$@\" >> {tmp_path / 'argv.txt'}\n"
                 f"case \" $* \" in *\" --json \"*) '{shutil.which('cat')}' '{tmp_path / 'verdict.out'}' ;; esac\n"
                 f"exit {rc}\n")
    m.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_))
    env_fence.delenv(monkeypatch, box.VERIFY_VAR)
    return m


@pytest.mark.parametrize("rc,signed_", [(0, True), (4, True), (1, False), (2, False), (5, False)])
def test_the_real_verifier_passes_on_full_or_unblessed_only(d, tmp_path, monkeypatch, rc, signed_):
    p = mail(d, "1.s", {"X-Mouth-Device": "call:1317"},
             sig="v=2; a=sshsig; n=x; h=from:to; p=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=; b=x")
    fake_message(tmp_path, monkeypatch, rc, _line(_verdict(rc if rc in (0, 4) else 4)))
    got = box.verify_signed(p)
    assert got == (_SIGNED if signed_ else None), "the verdict's h and values, not the mail's h="
    assert (tmp_path / "argv.txt").read_text().split() == ["verify", "--json", "--trust", "--quiet", str(p)]


def test_a_verifier_without_json_signs_nothing(d, tmp_path, monkeypatch):
    """A ``message`` older than SM-788 refuses ``--json`` (click's usage error: exit 2,
    no object). Every signed ask is refused then: fail closed (deploy sessionmail first)."""
    host = box._host()
    p = mail(d, "1.c", {"X-Mouth-Device": "call:1317"}, to=f"calls+ivy-1317@{host}",
             sig="v=2; a=sshsig; n=x; h=from:to:x-mouth-device; p=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=; b=x")
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "message").write_text("#!/bin/sh\ncase \" $* \" in *\" --json \"*) "
                                  "echo \"Error: No such option: --json\" >&2; exit 2 ;; esac\nexit 0\n")
    (bin_ / "message").chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_))
    env_fence.delenv(monkeypatch, box.VERIFY_VAR)
    assert box.verify_signed(p) is None
    assert box.read(p, d)["_untrusted"].endswith("needs a signed or local line (unsigned)")


def test_a_signed_verdict_is_immutable():
    """One verdict is memoised and handed to every reader: none of them may change it."""
    got = box.parse_verdict(4, _line(_verdict()))
    with pytest.raises(TypeError):
        got.covered["x-mouth-device"] = ("call:9",)
    with pytest.raises(AttributeError):
        got.version = "1"
    assert got.covered["x-mouth-device"] == ("call:1317",)


def _covered(*entries):
    return {"signature__covered": list(entries)}


#: Every way a verdict is no verdict (sessionmail ``docs/verify-json-verdict.md``: "No
#: object means no verdict. Fail closed."), as (exit, stdout).
_GOOD_LINE = _line(_verdict())
_NO_VERDICT = {
    "exit 1": (1, _GOOD_LINE),
    "exit 2": (2, _GOOD_LINE),
    "exit 3": (3, _GOOD_LINE),
    "exit 5": (5, _GOOD_LINE),
    "no object": (4, b""),
    "no line feed": (4, _GOOD_LINE[:-1]),
    "CRLF": (4, _GOOD_LINE[:-1] + b"\r\n"),
    "two lines": (4, _GOOD_LINE + _GOOD_LINE),
    "junk, then the object": (4, b"warning: x\n" + _GOOD_LINE),
    "a space first": (4, b" " + _GOOD_LINE),
    "not ASCII": (4, _GOOD_LINE.replace(b"call:1317", "call:1317\u00e9".encode())),
    "unparseable": (4, b'{"schema": 1, "rc": 4, "signature": {\n'),
    "an array": (4, b"[]\n"),
    "too deep": (4, b"{" + b'"a":[' * 100000 + b"]" * 100000 + b"}\n"),
    "too long": (4, _line(_verdict(pad="x" * (1 << 20)))),
    "schema 2": (4, _line(_verdict(schema=2))),
    "schema true": (4, _line(_verdict(schema=True))),
    "schema '1'": (4, _line(_verdict(schema="1"))),
    "no schema": (4, _line({k: v for k, v in _verdict().items() if k != "schema"})),
    "rc says 0": (4, _line(_verdict(rc=0))),
    "rc says 4, exit 0": (0, _line(_verdict(4))),
    "rc a string": (4, _line(_verdict(rc="4"))),
    "trust null (plain mode)": (4, _line(_verdict(trust=None))),
    "trust FAIL": (4, _line(_verdict(trust__verdict="FAIL"))),
    "trust ERROR": (4, _line(_verdict(trust__verdict="ERROR"))),
    "trust null verdict": (4, _line(_verdict(trust__verdict=None))),
    "FULL on exit 4": (4, _line(_verdict(trust__verdict="FULL"))),
    "UNBLESSED on exit 0": (0, _line(_verdict(0, trust__verdict="UNBLESSED"))),
    "signature null": (4, _line(_verdict(signature=None))),
    "version 3": (4, _line(_verdict(signature__version="3"))),
    "version 2, an int": (4, _line(_verdict(signature__version=2))),
    "h null": (4, _line(_verdict(signature__h=None))),
    "h a string": (4, _line(_verdict(signature__h="from:to:x-mouth-device"))),
    "h holds a number": (4, _line(_verdict(signature__h=["from", "to", 1]))),
    "a name twice": (4, _line(_verdict(signature__h=["from", "to", "to"], **_covered(
        {"name": "from", "values": ["a"]}, {"name": "to", "values": ["b"]},
        {"name": "to", "values": ["c"]})))),
    "covered null": (4, _line(_verdict(signature__covered=None))),
    "covered short": (4, _line(_verdict(**_covered({"name": "from", "values": ["a"]})))),
    "covered out of order": (4, _line(_verdict(**_covered(
        {"name": "to", "values": ["b"]}, {"name": "from", "values": ["a"]},
        {"name": "x-mouth-device", "values": ["call:1317"]})))),
    "an entry not a dict": (4, _line(_verdict(**_covered(
        {"name": "from", "values": ["a"]}, {"name": "to", "values": ["b"]}, "x-mouth-device")))),
    "values_b64": (4, _line(_verdict(**_covered(
        {"name": "from", "values": ["a"]}, {"name": "to", "values": ["b"]},
        {"name": "x-mouth-device", "values_b64": ["Y2FsbDoxMzE3"]})))),
    "values and values_b64": (4, _line(_verdict(**_covered(
        {"name": "from", "values": ["a"]}, {"name": "to", "values": ["b"]},
        {"name": "x-mouth-device", "values": ["call:1317"], "values_b64": ["eA=="]})))),
    "no values": (4, _line(_verdict(**_covered(
        {"name": "from", "values": ["a"]}, {"name": "to", "values": ["b"]},
        {"name": "x-mouth-device"})))),
    "values empty": (4, _line(_verdict(**_covered(
        {"name": "from", "values": ["a"]}, {"name": "to", "values": ["b"]},
        {"name": "x-mouth-device", "values": []})))),
    "a value not a string": (4, _line(_verdict(**_covered(
        {"name": "from", "values": ["a"]}, {"name": "to", "values": ["b"]},
        {"name": "x-mouth-device", "values": [1317]})))),
}


@pytest.mark.parametrize("case", list(_NO_VERDICT), ids=list(_NO_VERDICT))
def test_anything_but_a_well_formed_yes_is_no_verdict(case):
    assert box.parse_verdict(4, _GOOD_LINE) == _SIGNED, "the control is signed"
    assert box.parse_verdict(0, _line(_verdict(0))) == _SIGNED, "FULL too"
    assert box.parse_verdict(*_NO_VERDICT[case]) is None, case


_GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "verify_json"


@pytest.mark.parametrize("name", sorted(p.name for p in _GOLDEN.glob("*.json")))
def test_sessionmails_own_goldens_are_read_as_the_contract_says(name):
    """sessionmail's goldens (copied byte for byte): only a ``--trust`` FULL or
    UNBLESSED verdict is signed, and it reads as the verifier wrote it."""
    raw = (_GOLDEN / name).read_bytes()
    obj = json.loads(raw)
    got = box.parse_verdict(obj["rc"], raw)
    if name.startswith(("15-", "16-", "23-")):
        assert got is not None, name
        sig = obj["signature"]
        assert got.version == sig["version"] and list(got.covered) == sig["h"]
        assert {n: list(v) for n, v in got.covered.items()} == \
            {c["name"]: c["values"] for c in sig["covered"]}
    else:
        assert got is None, name


def test_v1s_covered_to_is_the_verifiers_reduction():
    """v1 signs To reduced to its local part (golden ``23-trust-v1-full``: ``kim``). A
    call box reduces to ``calls+<bot>-<N>``, which ``call_box`` reads; a plain To never."""
    got = box.parse_verdict(0, (_GOLDEN / "23-trust-v1-full.json").read_bytes())
    assert got.covered["to"] == ("kim",) and box.call_box("kim") is None
    assert box.call_box("calls+ivy-1317") == ("ivy", 1317)


def test_the_switch_set_at_all_turns_verification_off(d, tmp_path, monkeypatch):
    p = mail(d, "1.s", sig="v=2; a=sshsig; n=x; h=from; p=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=; b=x")
    fake_message(tmp_path, monkeypatch, 0)
    monkeypatch.setenv(box.VERIFY_VAR, str(tmp_path / "bin" / "message"))  # a path is not honoured
    assert box.verify_signed(p) is None
    assert not (tmp_path / "argv.txt").exists()


def test_no_verifier_on_path_fails_closed_and_mail_still_speaks(d, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    env_fence.delenv(monkeypatch, box.VERIFY_VAR)
    host = box._host()
    mail(d, "1.c", to=f"calls+ivy-1317@{host}", sig="v=1; a=sshsig; n=x; h=from:to; b=x")
    mail(d, "2.m", body="a plain mail")
    assert [x["text"] for x in box.Queue(d).lines()] == ["a plain mail"]


def test_a_reader_never_waits_on_the_verifier_and_an_acting_pass_does(d, monkeypatch, signed):
    gate, asked, fake = threading.Event(), [], box.verify_signed   # the signed fixture's verdict

    def slow(path):
        asked.append(Path(path).name)
        gate.wait(5)
        return fake(path)
    monkeypatch.setattr(box, "verify_signed", slow)
    mail(d, "1.n", {"X-Mouth-Priority": "now"}, sig="v=2; a=sshsig; n=x; h=from:to:x-mouth-priority; p=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=; b=x")
    q = box.Queue(d)
    (x,) = q.lines()   # a reader: unsigned until the verdict lands, and it did not wait
    assert (x["priority"], x["_verify_pending"]) == (None, True)
    gate.set()
    for t in [t for t in threading.enumerate() if t.name == "mouth-verify"]:
        t.join(5)
    (x,) = q.lines()   # the verdict landed: read again
    assert (x["priority"], x["_rank"], x["_verify_pending"]) == ("now", -1.0, False)
    assert asked == ["1.n"], "once per file"
    mail(d, "2.n", {"X-Mouth-Priority": "now"}, sig="v=2; a=sshsig; n=x; h=from:to:x-mouth-priority; p=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=; b=x")
    assert [x["priority"] for x in box.Queue(d).lines(act=True)] == ["now", "now"], \
        "an acting pass hears every verdict first"


# -- Supersedes (§3a): the keepers -----------------------------------------------------

def local_retract(d, target: str) -> None:
    """A local drop that retracts TARGET: an empty body, minted like any drop."""
    box.drop(box.box_dir(d), {"utt": "r1", "text": "", "requested_t": 1.0}, supersedes=target)


@pytest.mark.parametrize("site", ["poll", "act"])
def test_a_local_retract_still_cuts_the_line_playing(d, site):
    local_retract(d, PLAYING_ID)
    playing = {"utt": "p1", "mail_id": PLAYING_ID, "requested_t": 1.0, "device": "null"}
    if site == "poll":
        assert player._cut_by_mail(box.Queue(d), playing) == "retracted"
    else:
        box.Queue(d).lines(act=True, playing=playing)
        assert json.loads((d / "stop").read_text())["utt"] == "p1"


@pytest.mark.parametrize("version,cuts", [("2", True), ("1", False)])
def test_a_signed_v2_retract_cuts_and_a_v1_does_not(d, signed, version, cuts):
    mail(d, "1.r", {"Supersedes": PLAYING_ID}, body="", sig=signed(version))
    signed.settle(d)
    playing = {"utt": "p1", "mail_id": PLAYING_ID, "requested_t": 1.0, "device": "null"}
    assert (player._cut_by_mail(box.Queue(d), playing) == "retracted") is cuts
    box.Queue(d).lines(act=True, playing=playing)
    assert (d / "stop").exists() is cuts


def test_an_unsigned_retract_of_the_line_playing_is_refused_and_noticed(d, tmp_path):
    mail(d, "1.r", {"Supersedes": PLAYING_ID}, body="")
    playing = {"utt": "p1", "mail_id": PLAYING_ID, "requested_t": 1.0, "device": "null"}
    box.Queue(d).lines(act=True, playing=playing)
    assert not (d / "stop").exists()
    row = said(tmp_path)["<1.r@test>"]
    assert row["reason"] == "untrusted"
    assert row["detail"] == (f"Supersedes {PLAYING_ID} would cut the line playing: "
                             "needs a signed or local line (unsigned)")


def test_an_unsigned_same_from_replace_still_replaces_case_blind(d):
    host = box._host()
    mail(d, "1.a", body="the old words")
    mail(d, "2.b", {"Supersedes": "<1.a@test>"}, body="the new words", frm=f"Ivy@{host.upper()}")
    assert [x["text"] for x in box.Queue(d).lines()] == ["the new words"]


def test_the_same_from_is_the_parsed_address_case_blind_and_never_two_empties():
    same = box._same_from
    assert same({"_from": "Ivy@H.Example"}, {"_from": "ivy@h.example"})
    assert not same({"_from": "ivy@h.example"}, {"_from": "vera@h.example"})
    assert not same({"_from": ""}, {"_from": ""}) and not same({"_from": None}, {"_from": None})
    assert not same({"_from": "ivy"}, {"_from": "ivy@h.example"}), "never a local part"


@pytest.mark.parametrize("kind", ["replace", "retract"])
def test_another_senders_unsigned_supersede_is_refused_whole(d, tmp_path, kind):
    host = box._host()
    mail(d, "1.c", body="vera's own line", frm=f"vera@{host}")
    mail(d, "2.p", {"Supersedes": "<1.c@test>"}, body="ivy's words" if kind == "replace" else "")
    lines = box.Queue(d).lines(act=True)
    assert [x["text"] for x in lines] == ["vera's own line"], "refused whole: never played as news"
    assert list((box.box_dir(d) / "cur").glob("2.p:2,T"))
    row = said(tmp_path)["<2.p@test>"]
    assert row["reason"] == "untrusted"
    assert row["detail"] == (f"Supersedes <1.c@test> names a line from vera@{host}: "
                             "needs that sender, or a signed or local line (unsigned)")


def test_a_signed_v2_supersede_takes_another_senders_line(d, signed):
    host = box._host()
    mail(d, "1.c", body="vera's own line", frm=f"vera@{host}")
    mail(d, "2.p", {"Supersedes": "<1.c@test>"}, body="ivy's words", sig=signed("2"))
    signed.settle(d)
    assert [x["text"] for x in box.Queue(d).lines()] == ["ivy's words"]


def test_a_replacement_of_a_now_line_keeps_its_place_by_trust(d, signed, capsys):
    now = mouth.say("local now", priority="now", d=d, spawn=False)
    (mine,) = box.Queue(d).lines()
    frm = mine["_from"]
    mail(d, "2.u", {"Supersedes": now["mail_id"]}, body="unsigned replacement", frm=frm)
    (x,) = box.Queue(d).lines(act=True)
    assert (x["text"], x["_rank"], x["utt"]) == ("unsigned replacement", 0.0, now["utt"])
    logged = [json.loads(s) for s in capsys.readouterr().err.splitlines() if s.startswith("{")]
    assert {"untrusted": "Supersedes", "value": now["mail_id"], "mail_id": "<2.u@test>",
            "rank": -1.0, "as": 0.0} in logged

    now2 = mouth.say("local now two", priority="now", d=d, spawn=False)
    mail(d, "3.s", {"Supersedes": now2["mail_id"]}, body="signed replacement", sig=signed("2"))
    signed.settle(d)
    rank = {x["text"]: x["_rank"] for x in box.Queue(d).lines()}
    assert rank["signed replacement"] == -1.0
