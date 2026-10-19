"""reply.py: the ID, the heard box, the parent, the headers (EARS-30, design.md D3).

Every turn here is synthetic: a real turn's header shape with invented words.
A real heard turn is never committed (it holds the user's words).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mouth import maillog, reply

from maildir_put import put
from tests import env_fence
from ears import envnames

MID = "<h1@host1.ears>"


def turn(box: Path, name: str, mid: str, *, sub: str = "new", extra: bytes = b"",
         body: bytes = b"synthetic words, never Theo's\n") -> Path:
    """A heard turn shaped like today's (ears-mail.py), written raw so a test owns every byte."""
    return put(box, name, b"From: ears@host1\nTo: heard+airpods@host1\nSubject: heard: synthetic\n"
               b"Message-ID: " + mid.encode() + b"\nEars-Seq: 183360\n" + extra +
               b"Content-Type: text/plain; charset=\"utf-8\"\n\n" + body, sub=sub)


def name(n: int) -> str:
    """A maildir name that sorts by delivery time, as postfix's do."""
    return f"{1921391800 + n}.V1000013I{n:x}M0.host1"


# -- the fences ---------------------------------------------------------------

def test_the_fences_hold(heard_box):
    """conftest: no mouth test can reach ~/.mail. The heard box is tmp, HOME is tmp,
    and neither the queue box nor the mail log is set."""
    assert os.environ["MOUTH_HEARD_BOX"] == str(heard_box)
    assert reply.heard_boxes() == [heard_box]
    for name in ("MOUTH_MAILBOX", "MOUTH_MAIL_LOG"):   # under either name (EARS-294)
        assert not envnames.isset(name)
    assert maillog.box_from_env() is None
    assert Path.home() == heard_box.parent / "home"
    assert all((heard_box / s).is_dir() for s in ("tmp", "new", "cur"))


# -- norm_id ------------------------------------------------------------------

@pytest.mark.parametrize("raw", [MID, "h1@host1.ears"])
def test_norm_id_brackets_a_bare_id(raw):
    assert reply.norm_id(raw) == MID


def test_norm_id_keeps_every_printable_char():
    odd = "<1921365219.404504.1484.24045!#$%&'*+-/=?^_`{|}~@host1.ears>"
    assert reply.norm_id(odd) == odd
    assert reply.norm_id(odd[1:-1]) == odd


@pytest.mark.parametrize("raw, why", [
    ("", "empty"),
    ("183360", "an Ears-Seq: no @"),
    ("a1b2c3d4e5f6", "a utt: no @"),
    ("<a1b2c3d4e5f6>", "bracketed, still no @"),
    ("<a@b> <c@d>", "two ids, and a space"),
    ("<a@b><c@d>", "two ids: an inner > and <"),
    ("<hé@host1>", "non-ASCII: str.isprintable() would pass it"),
    ("<h1\x1b@host1>", "ESC, a control character"),
    ("<h1\x7f@host1>", "DEL, just past %x7E"),
    ("h1 x@host1.ears", "a space: str.isprintable() would pass it"),
    ("h1\tx@host1.ears", "a tab"),
    (" ", "a lone space"),
    ("\t", "a lone tab"),
    (MID + "\n", "a trailing newline"),
    (" " + MID, "a leading space"),
    ("<h1@host1.ears", "unbalanced: no >"),
    ("h1@host1.ears>", "unbalanced: no <"),
    ("<", "a lone <"),
    (">", "a lone >"),
    ("<>", "empty brackets"),
    ("<a<b@c>", "an inner <"),
    ("a>b@c", "an inner >"),
    ("<@>", "empty either side of the @"),
    ("<a@>", "an empty id-right"),
    ("<@b>", "an empty id-left"),
])
def test_norm_id_refuses(raw, why):
    with pytest.raises(ValueError, match="mouth: --in-reply-to"):
        reply.norm_id(raw)


def test_norm_id_refuses_a_non_string():
    with pytest.raises(ValueError):
        reply.norm_id(None)  # type: ignore[arg-type]


@pytest.mark.parametrize("value, ok", [
    (MID, True),
    ("h1@host1.ears", False),           # bare: not as norm_id writes it
    ("<a@b> <c@d>", False),
    ("<r\x1b@host1>", False),
    ("", False),
    (None, False),
])
def test_is_msg_id(value, ok):
    assert reply.is_msg_id(value) is ok


# -- heard_boxes --------------------------------------------------------------

def test_heard_boxes_has_no_default(monkeypatch):
    """EARS-202 G20: unset is no box, never our store's ~/.mail/agents/failmode/heard."""
    env_fence.delenv(monkeypatch, "MOUTH_HEARD_BOX")
    assert reply.heard_boxes() == []
    monkeypatch.setenv("MOUTH_HEARD_BOX", "")       # empty counts as unset
    assert reply.heard_boxes() == []
    monkeypatch.setenv("MOUTH_HEARD_BOX", " : ")    # so does blank
    assert reply.heard_boxes() == []


def test_find_parent_unset_names_the_variable_and_never_guesses(monkeypatch, capsys, heard_box):
    """Unset: None and one stderr line naming the variable. A turn in the old default's
    place (under the fake HOME) is never read."""
    old = Path.home() / ".mail" / "agents" / "failmode" / "heard"
    turn(old, name(1), MID)
    env_fence.delenv(monkeypatch, "MOUTH_HEARD_BOX")
    assert reply.find_parent(MID) is None
    err = capsys.readouterr().err
    assert "$MOUTH_HEARD_BOX is not set" in err and err.count("\n") == 1


def test_find_parent_explicit_boxes_never_warn_about_the_variable(monkeypatch, capsys, heard_box):
    env_fence.delenv(monkeypatch, "MOUTH_HEARD_BOX")
    turn(heard_box, name(1), MID)
    assert reply.find_parent(MID, [heard_box]) is not None
    assert reply.find_parent("<x@host1.ears>", []) is None
    assert capsys.readouterr().err == ""


def test_heard_boxes_colon_separated(monkeypatch, tmp_path):
    monkeypatch.setenv("MOUTH_HEARD_BOX", f"{tmp_path}/a::~/b:")
    assert reply.heard_boxes() == [tmp_path / "a", Path.home() / "b"]


# -- find_parent --------------------------------------------------------------

def test_find_parent_in_new(heard_box):
    turn(heard_box, name(1), "<other@host1.ears>")
    turn(heard_box, name(2), MID, extra=b"Ears-Device: airpods\n")
    turn(heard_box, name(3), "<newer@host1.ears>")
    got = reply.find_parent(MID)
    assert got is not None and str(got["Message-ID"]) == MID and str(got["Ears-Device"]) == "airpods"


def test_find_parent_in_cur_flagged_seen(heard_box):
    turn(heard_box, name(1) + ":2,S", MID, sub="cur")
    assert str(reply.find_parent(MID)["Message-ID"]) == MID


def test_find_parent_never_reads_tmp(heard_box):
    turn(heard_box, name(1), MID, sub="tmp")
    assert reply.find_parent(MID) is None


def test_find_parent_not_found(heard_box):
    turn(heard_box, name(1), "<other@host1.ears>")
    assert reply.find_parent(MID) is None


def test_find_parent_matches_the_message_id_exactly(heard_box):
    turn(heard_box, name(1), "<h1@host1.ears.x>")
    turn(heard_box, name(2), "<xh1@host1.ears>")
    turn(heard_box, name(3), "<said@host1.mouth>", extra=b"In-Reply-To: " + MID.encode() + b"\n"
         b"References: " + MID.encode() + b"\n")
    assert reply.find_parent(MID) is None


def test_find_parent_never_reads_the_body(heard_box):
    """The id in a body (the user's words) is not a match: only the header block is read."""
    turn(heard_box, name(1), "<other@host1.ears>", body=b"Message-ID: " + MID.encode() + b"\n")
    assert reply.find_parent(MID) is None
    turn(heard_box, name(2), MID, body=b"words that must never be read back\n")
    got = reply.find_parent(MID)
    assert got is not None and not got.get_payload().strip()


def test_find_parent_folded_and_encoded_message_ids(heard_box):
    turn(heard_box, name(1), "\n " + MID)                                # folded onto a second line
    assert reply.find_parent(MID) is not None
    for p in (heard_box / "new").iterdir():
        p.unlink()
    turn(heard_box, name(2), "=?utf-8?q?=3Ch1=40host1=2Eears=3E?=")         # an RFC 2047 encoded-word
    assert reply.find_parent(MID) is not None


def test_find_parent_newest_first_and_first_hit_wins(heard_box, tmp_path):
    turn(heard_box, name(1), MID, extra=b"Ears-Seq-Tag: older\n")
    turn(heard_box, name(2), MID, extra=b"Ears-Seq-Tag: newer\n")
    assert str(reply.find_parent(MID)["Ears-Seq-Tag"]) == "newer"
    second = tmp_path / "second"
    turn(second, name(9), MID, extra=b"Ears-Seq-Tag: second-box\n")
    assert str(reply.find_parent(MID, [heard_box, second])["Ears-Seq-Tag"]) == "newer"
    assert str(reply.find_parent(MID, [second, heard_box])["Ears-Seq-Tag"]) == "second-box"


def test_find_parent_searches_every_box(monkeypatch, heard_box, tmp_path):
    other = tmp_path / "other"
    turn(other, name(1), MID)
    monkeypatch.setenv("MOUTH_HEARD_BOX", f"{heard_box}:{other}")
    assert reply.find_parent(MID) is not None


def test_find_parent_past_the_cap_is_not_found(heard_box):
    assert reply.SCAN_CAP == 2000
    turn(heard_box, name(0), MID)                                 # the oldest
    for n in range(1, reply.SCAN_CAP):                             # 1999 newer: MID is 2000th
        turn(heard_box, name(n), f"<t{n}@host1.ears>")
    assert reply.find_parent(MID) is not None
    turn(heard_box, name(reply.SCAN_CAP), "<newest@host1.ears>", sub="cur")  # cur/ counts too
    assert reply.find_parent(MID) is None
    assert reply.find_parent(MID, cap=reply.SCAN_CAP + 1) is not None


def test_find_parent_a_missing_box_or_subdir_is_no_error(tmp_path):
    (tmp_path / "only-cur" / "cur").mkdir(parents=True)
    assert reply.find_parent(MID, [tmp_path / "absent", tmp_path / "only-cur"]) is None


def test_find_parent_skips_a_file_that_moved(heard_box, monkeypatch):
    real = turn(heard_box, name(1), MID)
    gone = heard_box / "new" / name(2)
    monkeypatch.setattr(reply, "_newest", lambda box, cap: [gone, real])
    assert reply.find_parent(MID) is not None


def test_find_parent_ignores_dotfiles(heard_box):
    turn(heard_box, "." + name(1), MID)
    assert reply.find_parent(MID) is None


# -- reply_headers ------------------------------------------------------------

def parent(heard_box: Path, extra: bytes):
    turn(heard_box, name(1), MID, extra=extra)
    p = reply.find_parent(MID)
    assert p is not None
    return p


def test_reply_headers_parent_with_root_and_call_id(heard_box, capsys):
    p = parent(heard_box, b"References: <r@host1.call> <x@host1>\nCall-ID: <r@host1.call>\n")
    got = reply.reply_headers(MID, p)
    assert got == {"In-Reply-To": MID, "References": f"<r@host1.call> {MID}", "Call-ID": "<r@host1.call>"}
    assert list(got) == ["In-Reply-To", "References", "Call-ID"]
    assert capsys.readouterr().err == ""


def test_reply_headers_parent_like_todays_turn(heard_box, capsys):
    """Today's turns carry no References and no Call-ID: ID alone, and no note."""
    got = reply.reply_headers(MID, parent(heard_box, b""))
    assert got == {"In-Reply-To": MID, "References": MID}
    assert capsys.readouterr().err == ""


def test_reply_headers_not_found(capsys):
    got = reply.reply_headers("h1@host1.ears", None)
    assert got == {"In-Reply-To": MID, "References": MID}
    err = capsys.readouterr().err
    assert MID in err and "not in the heard box" in err and err.count("\n") == 1


def test_reply_headers_root_equal_to_the_id_is_not_doubled(heard_box):
    got = reply.reply_headers(MID, parent(heard_box, b"References: " + MID.encode() + b"\n"))
    assert got["References"] == MID


def test_reply_headers_root_is_the_first_token(heard_box):
    p = parent(heard_box, b"References: <r@host1.call><x@host1>\n <y@host1>\n")
    assert reply.reply_headers(MID, p)["References"] == f"<r@host1.call> {MID}"


def test_reply_headers_call_id_alone(heard_box):
    got = reply.reply_headers(MID, parent(heard_box, b"Call-ID: <r@host1.call>\n"))
    assert got == {"In-Reply-To": MID, "References": MID, "Call-ID": "<r@host1.call>"}


@pytest.mark.parametrize("refs", [b"<r\x1b@host1> <x@host1>", b"garbage <x@host1>", b"<r@host1 <x@host1>",
                                  b"<no-at-sign> <x@host1>", "<ré@host1> <x@host1>".encode()])
def test_reply_headers_a_bad_root_is_dropped(heard_box, capsys, refs):
    """Review 2129 item 6: a copied value must pass the msg-id check, or it is dropped;
    the line still threads on ID, and the Call-ID still copies."""
    got = reply.reply_headers(MID, parent(heard_box, b"References: " + refs + b"\n"
                                          b"Call-ID: <r@host1.call>\n"))
    assert got == {"In-Reply-To": MID, "References": MID, "Call-ID": "<r@host1.call>"}
    assert "References[0]" in capsys.readouterr().err


@pytest.mark.parametrize("call", [b"r@host1.call", b"<r@host1.call> <s@host1.call>", b"<r\x1b@host1.call>",
                                  b"<no-at-sign>", "<ré@host1.call>".encode()])
def test_reply_headers_a_bad_call_id_is_dropped(heard_box, capsys, call):
    got = reply.reply_headers(MID, parent(heard_box, b"References: <r@host1.call>\nCall-ID: " + call + b"\n"))
    assert got == {"In-Reply-To": MID, "References": f"<r@host1.call> {MID}"}
    assert "Call-ID" in capsys.readouterr().err


def test_reply_headers_refuses_a_bad_id():
    with pytest.raises(ValueError):
        reply.reply_headers("183360", None)


def test_reply_headers_copies_the_value_not_its_whitespace(heard_box):
    """Folding and the space around a header's value are not the value; the id is
    copied byte for byte, brackets included (Q-a)."""
    p = parent(heard_box, b"References:\n <r@host1.call>  <x@host1>\nCall-ID:  <r@host1.call> \n")
    assert reply.reply_headers(MID, p) == {"In-Reply-To": MID, "References": f"<r@host1.call> {MID}",
                                           "Call-ID": "<r@host1.call>"}


def test_reply_headers_decodes_an_encoded_call_id(heard_box):
    """Python writes a Call-ID of 78+ chars as an RFC 2047 encoded-word (design.md)."""
    p = parent(heard_box, b"Call-ID: =?utf-8?q?=3Cr=40host1=2Ecall=3E?=\n")
    assert reply.reply_headers(MID, p)["Call-ID"] == "<r@host1.call>"
