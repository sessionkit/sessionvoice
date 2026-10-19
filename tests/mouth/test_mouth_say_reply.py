"""``mouth say --in-reply-to``: a said line threads to its heard turn (EARS-30, design.md D4).

A line sent to the mouth is a reply to what the ears just delivered to the
seat: that is the conversation thread.

No sound: device ``null``, backend ``silence``, ``spawn=False`` (the CLI stubs
``ensure_player``). The queue box is ``<tmp>/mouth/box``; the heard box is
conftest's tmp maildir. Every turn is synthetic: a real turn's header shape with
invented words (a real heard turn holds the user's words and is never committed).
References folds onto two lines (design-001 review N1), so headers are parsed,
never grepped.
"""

from __future__ import annotations

import email
import email.policy
import json
import os
import socket
import sys
import threading
import time
from email.message import EmailMessage
from pathlib import Path

import pytest

import mouth
from mouth import box, player, reply
from mouth.__main__ import main

from maildir_put import put
from tests import env_fence

MID = "<h1@host1.ears>"
ROOT = "<r@host1.call>"
# The shape of a real ears id and call root, at real length.
REAL_MID = "<1921365219.404504.1484.24045@host1.ears>"
REAL_ROOT = "<1921364900.404504.1480.24001@host1.ears>"
HOST = socket.gethostname().split(".")[0].lower()
OUTSIDE = f"<1.test@{HOST}.session-mail>"


@pytest.fixture
def d(tmp_path, monkeypatch):
    d = tmp_path / "mouth"
    monkeypatch.setenv("MOUTH_DIR", str(d))
    monkeypatch.setenv("MOUTH_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MOUTH_DEVICE", "null")
    monkeypatch.setenv("MOUTH_BACKEND", "silence")
    monkeypatch.setattr(mouth, "ensure_player", lambda *a, **k: False)  # the CLI never spawns
    return d


def turn(heard: Path, n: int, mid: str, *, sub: str = "new", flags: str = "",
         extra: bytes = b"") -> Path:
    """A heard turn shaped like today's (ears-mail.py), synthetic words."""
    name = f"{1921391800 + n}.V1000013I{n:x}M0.host1" + (f":2,{flags}" if sub == "cur" else "")
    return put(heard, name, b"From: ears@host1\nTo: heard+airpods@host1\nSubject: heard: synthetic\n"
               b"Message-ID: " + mid.encode() + b"\nEars-Seq: 183360\n" + extra +
               b"Content-Type: text/plain; charset=\"utf-8\"\n\nsynthetic words\n", sub=sub)


def mails(d: Path, sub: str = "new") -> list:
    """The queue box's mails in SUB, oldest first, parsed."""
    folder = d / "box" / sub
    if not folder.is_dir():
        return []
    return [email.message_from_bytes(f.read_bytes(), policy=email.policy.default)
            for f in sorted(folder.iterdir())]


def only(d: Path):
    (m,) = mails(d)
    return m


def refs(m) -> list:
    """References as a list of ids: parsed, so a fold never matters (N1)."""
    v = m["References"]
    return str(v).split() if v is not None else []


# -- criterion 1: the CLI, no audio ---------------------------------------------

def test_cli_say_in_reply_to_threads_the_mail(d, capsys):
    assert main(["say", "--in-reply-to", MID, "--device", "null", "hi"]) == 0
    m = only(d)
    assert m["In-Reply-To"] == MID
    assert refs(m)[-1] == MID
    assert m["X-Mouth-Extra"] is None, "the reply rides as headers, never as extras"


def test_cli_bare_id_is_bracketed(d):
    assert main(["say", "--in-reply-to", "h1@host1.ears", "--device", "null", "hi"]) == 0
    m = only(d)
    assert (m["In-Reply-To"], refs(m)) == (MID, [MID])


def test_cli_bare_id_finds_its_parent(d, heard_box):
    """O-2: say() looks the parent up by norm_id(ID), never the raw flag. A bare ID
    with its turn in the heard box finds it: root and Call-ID copied."""
    turn(heard_box, 1, MID, extra=f"References: {ROOT} <x@host1>\nCall-ID: {ROOT}\n".encode())
    assert main(["say", "--in-reply-to", "h1@host1.ears", "--device", "null", "hi"]) == 0
    m = only(d)
    assert refs(m) == [ROOT, MID]
    assert m["Call-ID"] == ROOT


# -- criterion 2: the parent's root and Call-ID ---------------------------------

def test_parent_with_root_and_call_id(d, heard_box, capsys):
    turn(heard_box, 1, MID, extra=f"References: {ROOT} <x@host1>\nCall-ID: {ROOT}\n".encode())
    item = mouth.say("hi", in_reply_to=MID, d=d, spawn=False)
    m = only(d)
    assert m["In-Reply-To"] == MID
    assert refs(m) == [ROOT, MID]
    assert m["Call-ID"] == ROOT
    assert item["_parent_found"] is True, "F6: an API caller sees it without stderr"
    assert capsys.readouterr().err == ""


def test_parent_like_todays_turn(d, heard_box):
    """Today's turns carry no References: the line's References is the ID alone."""
    turn(heard_box, 1, MID)
    item = mouth.say("hi", in_reply_to=MID, d=d, spawn=False)
    m = only(d)
    assert (m["In-Reply-To"], refs(m), m["Call-ID"]) == (MID, [MID], None)
    assert item["_parent_found"] is True


def test_parent_not_found_threads_on_the_id_alone(d, capsys):
    assert main(["say", "--in-reply-to", MID, "--device", "null", "hi"]) == 0
    m = only(d)
    assert (m["In-Reply-To"], refs(m), m["Call-ID"]) == (MID, [MID], None)
    err = capsys.readouterr().err
    assert "not in the heard box" in err and MID in err
    assert mouth.say("again", in_reply_to=MID, d=d, spawn=False)["_parent_found"] is False


def test_no_heard_box_set_still_says_and_names_the_variable(d, capsys, monkeypatch):
    """EARS-202 G20: with $MOUTH_HEARD_BOX unset there is no default box; the
    line is said, threads on the ID alone, and stderr says which variable to set."""
    env_fence.delenv(monkeypatch, "MOUTH_HEARD_BOX")
    assert main(["say", "--in-reply-to", MID, "--device", "null", "hi"]) == 0
    m = only(d)
    assert (m["In-Reply-To"], refs(m), m["Call-ID"]) == (MID, [MID], None)
    err = capsys.readouterr().err
    assert "$MOUTH_HEARD_BOX is not set" in err and "not in the heard box" in err


def test_no_option_sets_no_reply_keys(d):
    item = mouth.say("hi", d=d, spawn=False)
    m = only(d)
    assert all(m[h] is None for h in ("In-Reply-To", "References", "Call-ID"))
    assert not {"in_reply_to", "references", "call_id", "_parent_found"} & set(item)


# -- the scan --------------------------------------------------------------------

def test_parent_found_in_cur_seen(d, heard_box):
    turn(heard_box, 1, MID, sub="cur", flags="S", extra=f"References: {ROOT}\n".encode())
    mouth.say("hi", in_reply_to=MID, d=d, spawn=False)
    assert refs(only(d)) == [ROOT, MID]


def test_parent_past_the_cap_is_not_found(d, heard_box, capsys):
    turn(heard_box, 0, MID, extra=f"References: {ROOT}\n".encode())   # the oldest
    for n in range(1, reply.SCAN_CAP + 1):                              # 2000 newer turns
        turn(heard_box, n, f"<t{n}@host1.ears>")
    item = mouth.say("hi", in_reply_to=MID, d=d, spawn=False)
    assert refs(only(d)) == [MID], "past the cap: ID alone, no root"
    assert item["_parent_found"] is False
    assert "not in the heard box" in capsys.readouterr().err


def test_an_unreadable_heard_box_is_not_found(d, heard_box, capsys):
    """do-002 review N-1: a heard box the mouth can't list (mode 000) is 'not found':
    the line still queues and threads on the ID alone, with the stderr note."""
    turn(heard_box, 1, MID, extra=f"References: {ROOT}\n".encode())
    (heard_box / "new").chmod(0)
    try:
        if os.access(heard_box / "new", os.R_OK):
            pytest.skip("running as a user who can read a mode-000 dir (root)")
        item = mouth.say("hi", in_reply_to=MID, d=d, spawn=False)
    finally:
        (heard_box / "new").chmod(0o755)
    m = only(d)
    assert (m["In-Reply-To"], refs(m)) == (MID, [MID])
    assert item["_parent_found"] is False
    assert "not in the heard box" in capsys.readouterr().err


# -- the ID: refused before anything is written --------------------------------

@pytest.mark.parametrize("bad", ["183360", "", "<a@b> <c@d>", "<hé@host1>", "<h1\x1b@host1>", "<@b>"])
def test_a_bad_id_exits_2_and_queues_nothing(d, tmp_path, monkeypatch, capsys, bad):
    log = tmp_path / "maillog"          # the mail log ON, in tmp: never '1' (the live box)
    monkeypatch.setenv("MOUTH_MAIL_LOG", str(log))
    assert main(["say", "--in-reply-to", bad, "--device", "null", "hi"]) == 2
    assert "--in-reply-to" in capsys.readouterr().err
    assert mails(d) == [], "new/ is missing or empty: the check runs before mkdir"
    assert not log.exists() or not any(log.rglob("*")), "no queued entry"


def test_a_bad_id_by_the_api_raises(d):
    with pytest.raises(ValueError, match="--in-reply-to"):
        mouth.say("hi", in_reply_to="183360", d=d, spawn=False)
    assert mails(d) == []


# -- amend ---------------------------------------------------------------------

def test_amend_of_a_reply_keeps_the_thread(d, heard_box):
    turn(heard_box, 1, MID, extra=f"References: {ROOT}\nCall-ID: {ROOT}\n".encode())
    first = mouth.say("draft", in_reply_to=MID, d=d, spawn=False)
    mouth.amend(first["utt"], "final", d=d)
    old, new = mails(d)
    assert new["Supersedes"] == first["mail_id"]
    assert (new["In-Reply-To"], refs(new), new["Call-ID"]) == (MID, [ROOT, MID], ROOT)
    assert new["X-Mouth-Extra"] is None or "in_reply_to" not in new["X-Mouth-Extra"]
    (x,) = box.Queue(d).lines()
    assert (x["text"], x["utt"], x["in_reply_to"]) == ("final", first["utt"], MID)


def test_a_plain_supersede_reads_back_with_no_reply_keys(d):
    first = mouth.say("draft", d=d, spawn=False)
    mouth.amend(first["utt"], "final", d=d)
    f = sorted((d / "box" / "new").iterdir())[-1]
    m = email.message_from_bytes(f.read_bytes(), policy=email.policy.default)
    assert m["Supersedes"] == m["In-Reply-To"] == m["References"] == first["mail_id"]
    assert not {"in_reply_to", "references", "call_id"} & set(box.read(f, d))


# -- resume ----------------------------------------------------------------------

def _cut_and_resume(d: Path, item: dict) -> None:
    """The player cut ITEM after its first sentence; it queues the rest (player._resume)."""
    player._resume(d, item, {"text_played_est": "First part."})


def test_a_resumed_reply_carries_its_headers(d, heard_box):
    turn(heard_box, 1, MID, extra=f"References: {ROOT}\nCall-ID: {ROOT}\n".encode())
    mouth.say("First part. Second part.", in_reply_to=MID, d=d, spawn=False)
    (x,) = box.Queue(d).lines()
    _cut_and_resume(d, box.public(x))
    _, rest = mails(d)
    assert rest["X-Mouth-Resume-Of"] == x["utt"]
    assert (rest["In-Reply-To"], refs(rest), rest["Call-ID"]) == (MID, [ROOT, MID], ROOT)


def test_a_resumed_plain_line_carries_no_reply(d):
    mouth.say("First part. Second part.", d=d, spawn=False)
    (x,) = box.Queue(d).lines()
    _cut_and_resume(d, box.public(x))
    _, rest = mails(d)
    assert rest["X-Mouth-Resume-Of"] == x["utt"]
    assert all(rest[h] is None for h in ("In-Reply-To", "References", "Call-ID"))


# -- an outside mail (postfix, `message send -r <turn>` to mouth@) --------------

def _outside(d: Path) -> Path:
    """A mail an agent sent to mouth@ with `message send -r <turn>`: no X-Mouth-Raw."""
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = f"vera@{HOST}", f"mouth@{HOST}", "hi"
    m["Message-ID"] = OUTSIDE
    m["In-Reply-To"] = MID
    m["References"] = f"{ROOT} {MID}"
    m.set_content("First part. Second part.\n")
    box.ensure(d / "box")
    return put(d / "box", f"{int(time.time()) - 60}.1.host1", bytes(m))  # older than what the mouth writes now


def test_an_outside_mail_reads_as_a_reply_and_its_resume_keeps_the_thread(d):
    f = _outside(d)
    x = box.read(f, d)
    assert (x["in_reply_to"], x["references"]) == (MID, f"{ROOT} {MID}")
    assert "call_id" not in x
    _cut_and_resume(d, box.public(x))
    rest = mails(d)[-1]
    assert (rest["In-Reply-To"], refs(rest)) == (MID, [ROOT, MID])


def test_an_amend_of_an_outside_reply_stays_in_the_senders_thread(d):
    f = _outside(d)
    (x,) = box.Queue(d).lines()
    mouth.amend(x["utt"], "better", d=d)
    new = mails(d)[-1]
    assert new["Supersedes"] == OUTSIDE   # today it would also thread under OUTSIDE
    assert (new["In-Reply-To"], refs(new)) == (MID, [ROOT, MID])
    assert f.exists()


# -- the player ignores them -------------------------------------------------------

def _spoken(tmp_path) -> list:
    return [json.loads(x)["text"] for f in sorted((tmp_path / "logs").glob("heard_*.jsonl"))
            for x in f.read_text().splitlines() if json.loads(x)["kind"] == "saying"]


def test_a_reply_plays_like_any_other_line(d, tmp_path, heard_box):
    turn(heard_box, 1, MID, extra=f"References: {ROOT}\nCall-ID: {ROOT}\n".encode())
    mouth.say("one", d=d, spawn=False)
    mouth.say("two", in_reply_to=MID, d=d, spawn=False)
    mouth.say("three", d=d, spawn=False)
    t = threading.Thread(target=player.serve, kwargs={"d": d, "idle_exit_s": 0.3}, daemon=True)
    t.start()
    t.join(10)
    assert _spoken(tmp_path) == ["one", "two", "three"]
    done = sorted((d / "box" / "cur").iterdir())
    assert [f.name.split(":2,")[1] for f in done] == ["S", "S", "S"]


# -- serialisation (O-3: the golden compares decoded values; this is the raw bytes) --
#
# No log dir here: pytest's tmp path is longer than a line, and X-Mouth-Log-Dir (not
# an id; read() decodes it) would be RFC 2047-encoded, as it is today without a reply.

def raw_header(raw: bytes, name: str) -> bytes:
    """NAME's raw value, its fold lines joined, exactly as written."""
    lines = raw.split(b"\n\n", 1)[0].split(b"\n")
    for i, line in enumerate(lines):
        if line.lower().startswith(name.lower().encode() + b":"):
            val = line.split(b":", 1)[1]
            for cont in lines[i + 1:]:
                if cont[:1] not in (b" ", b"\t"):
                    break
                val += cont
            return val.strip()
    raise AssertionError(f"no {name}")

def test_a_realistic_reply_is_written_plainly(d, heard_box, monkeypatch):
    env_fence.delenv(monkeypatch, "MOUTH_LOG_DIR")
    turn(heard_box, 1, REAL_MID,
         extra=f"References: {REAL_ROOT}\nCall-ID: {REAL_ROOT}\n".encode())
    mouth.say("A realistic line, with a found parent and so a Call-ID.",
              in_reply_to=REAL_MID, d=d, spawn=False)
    (f,) = (d / "box" / "new").iterdir()
    raw = f.read_bytes()
    assert b"=?" not in raw
    unfolded = raw.replace(b"\n ", b" ").replace(b"\n\t", b" ")
    assert unfolded.count(REAL_MID.encode()) >= 2      # In-Reply-To and References
    assert unfolded.count(REAL_ROOT.encode()) == 2     # References[0] and Call-ID
    assert raw_header(raw, "In-Reply-To") == REAL_MID.encode()
    assert raw_header(raw, "References").split() == [REAL_ROOT.encode(), REAL_MID.encode()]
    assert raw_header(raw, "Call-ID") == REAL_ROOT.encode()
    m = email.message_from_bytes(raw, policy=email.policy.default)
    assert refs(m) == [REAL_ROOT, REAL_MID] and m["Call-ID"] == REAL_ROOT


@pytest.mark.skipif(sys.version_info < (3, 13), reason="before 3.13 the stdlib folds In-Reply-To "
                    "and References as unstructured text too: an id longer than a line is "
                    "encoded there (a real id is about 40 chars)")
def test_a_long_call_id_is_never_encoded(d, heard_box, monkeypatch):
    """An id longer than a line: as unstructured text the folder would RFC 2047-encode
    Call-ID (=?utf-8?q?...), so box.drop writes it as a Message-ID-shaped header. On
    3.13 In-Reply-To and References are already id lists (ReferencesHeader)."""
    env_fence.delenv(monkeypatch, "MOUTH_LOG_DIR")
    long_root = "<" + "c" * 90 + "@host1.ears>"
    turn(heard_box, 1, MID, extra=f"References: {long_root}\nCall-ID: {long_root}\n".encode())
    mouth.say("hi", in_reply_to=MID, d=d, spawn=False)
    (f,) = (d / "box" / "new").iterdir()
    raw = f.read_bytes()
    assert b"=?" not in raw
    assert raw_header(raw, "Call-ID") == long_root.encode()
    m = email.message_from_bytes(raw, policy=email.policy.default)
    assert str(m["Call-ID"]).strip() == long_root
    assert refs(m) == [long_root, MID]
    assert box.read(f, d)["call_id"] == long_root
