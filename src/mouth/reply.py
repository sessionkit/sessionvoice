"""A said line as a reply to the heard turn it answers.

A line sent to the mouth is in reply to what the ears just delivered:
that is the conversation
thread.

``mouth say --in-reply-to ID`` writes three headers on the line's mail
(References is trimmed to root + parent):

    In-Reply-To: ID
    References:  <root> ID      the parent's References[0], if it has one
    Call-ID:     <the parent's>  copied byte for byte, if it has one

The parent is looked up in the heard box, not notmuch: the index runs up to
5 minutes behind, and the turn being answered is seconds old. If it isn't
found, the line still threads on ID alone, and stderr says so. That is never
an error.

This module only answers questions; ``say``/``box`` write the headers.
"""

from __future__ import annotations

import email
import email.policy
import heapq
import os
import re
import sys
from email.message import EmailMessage
from pathlib import Path
from typing import Callable, Iterable, Optional

from ears import envnames

HEARD_VAR = "MOUTH_HEARD_BOX"
#: No default box: where ``heard@<host>`` lands is the host's mail setup,
#: not ours to guess. Unset, there is no box to look in: the line threads on the ID alone
#: and stderr names $MOUTH_HEARD_BOX (``NO_HEARD_BOX``). A host that used the old
#: default (``~/.mail/agents/failmode/heard``) sets the variable to it.
NO_HEARD_BOX = (f"mouth: ${HEARD_VAR} is not set, so there is no heard box to look the "
                "parent up in; set it to the maildir(s) heard turns land in (':'-separated)")
#: The newest this many files in a box are read: about 2.5 days of turns.
SCAN_CAP = 2000
#: A header block longer than this is not a mail we wrote; stop reading it.
_HEAD_MAX = 64 * 1024
_TOKEN = re.compile(r"\s*(<[^<>]*>)")


def _warn(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# -- the ID -------------------------------------------------------------------

def norm_id(raw: str) -> str:
    """``<id>`` or a bare ``id`` -> ``<id>``. ValueError (the CLI's exit 2) unless it
    is one msg-id: printable ASCII only (VCHAR, %x21-7E: no space, tab, control
    character or non-ASCII letter), brackets balanced and only at the ends, and an
    ``@`` (an Ears-Seq like ``183360`` or a utt passed for a Message-ID has none)."""
    if not isinstance(raw, str) or not raw:
        raise ValueError("mouth: --in-reply-to needs a Message-ID, <id@host>")
    bad = next((c for c in raw if not "\x21" <= c <= "\x7e"), None)
    if bad is not None:
        raise ValueError(f"mouth: --in-reply-to {raw!r}: a Message-ID is printable ASCII only, "
                         f"no {bad!r}")
    opens, closes = raw.startswith("<"), raw.endswith(">")
    if opens != closes or (opens and len(raw) < 2):
        raise ValueError(f"mouth: --in-reply-to {raw!r}: unbalanced angle brackets")
    inner = raw[1:-1] if opens else raw
    if "<" in inner or ">" in inner:
        raise ValueError(f"mouth: --in-reply-to {raw!r}: one Message-ID, no < or > inside")
    if "@" not in inner:
        raise ValueError(f"mouth: --in-reply-to {raw!r}: not a Message-ID (no @); "
                         "an Ears-Seq or a utt is not one")
    if inner.startswith("@") or inner.endswith("@"):  # RFC 5322: id-left and id-right
        raise ValueError(f"mouth: --in-reply-to {raw!r}: empty before or after the @")
    return f"<{inner}>"


def is_msg_id(value: Optional[str]) -> bool:
    """True if VALUE is already one bracketed msg-id, exactly as ``norm_id`` writes it.
    The check a value copied from the parent (root, Call-ID) must pass."""
    try:
        return value is not None and norm_id(value) == value
    except ValueError:
        return False


# -- the heard box ------------------------------------------------------------

def heard_boxes() -> list:
    """``$MOUTH_HEARD_BOX`` (``:``-separated maildirs; empty = unset), else
    ``[]``: no default box."""
    env = envnames.get(HEARD_VAR) or ""
    parts = [p for p in env.split(":") if p.strip()]
    return [Path(p).expanduser() for p in parts]


def _newest(box: Path, cap: int) -> list:
    """The newest CAP files in BOX's new/ and cur/ (never tmp/), newest first by name.
    A maildir file's name starts with its delivery time."""
    names = []
    for sub in ("new", "cur"):
        try:
            names += [(n, sub) for n in os.listdir(box / sub) if not n.startswith(".")]
        except OSError:  # missing, not a dir, unreadable: nothing found there, never a traceback
            continue
    return [box / sub / n for n, sub in heapq.nlargest(cap, names)]


def _head(path: Path) -> Optional[bytes]:
    """PATH's header block, up to the blank line. The body is never parsed or kept:
    a heard turn's body is the user's words. None if the file has gone."""
    out = []
    size = 0
    try:
        with open(path, "rb") as f:
            for line in f:
                if line in (b"\n", b"\r\n"):
                    break
                out.append(line)
                size += len(line)
                if size > _HEAD_MAX:
                    break
    except OSError:  # moved between the listing and the read, or unreadable
        return None
    return b"".join(out)


def find_parent(mid: str, boxes: Optional[Iterable[Path]] = None,
                cap: int = SCAN_CAP) -> Optional[EmailMessage]:
    """The header block of the mail whose Message-ID is exactly MID, or None.

    Each box in turn (``heard_boxes()`` by default): its newest CAP files in new/
    and cur/, newest first. The first match wins. No box configured: None, and a
    line on stderr naming $MOUTH_HEARD_BOX (never an error: the line
    still threads on MID).
    """
    want = mid.encode("ascii")
    if boxes is None:
        boxes = heard_boxes()
        if not boxes:
            _warn(NO_HEARD_BOX)
    for box in boxes:
        for path in _newest(Path(box), cap):
            head = _head(path)
            raw = _raw_mid(head) if head else None
            if raw is None or (raw != want and b"=?" not in raw):
                continue  # cheap: no parse unless the Message-ID could be MID
            msg = email.message_from_bytes(head + b"\n\n", policy=email.policy.default)
            if _str(msg, "Message-ID").strip() == mid:
                return msg
    return None


def _raw_mid(head: bytes) -> Optional[bytes]:
    """The Message-ID's raw value in a header block (folded lines joined), or None."""
    lines = head.splitlines()
    for i, line in enumerate(lines):
        if line[:11].lower() == b"message-id:":
            val = line[11:]
            for cont in lines[i + 1:]:
                if not cont[:1] in (b" ", b"\t"):
                    break
                val += cont
            return val.strip()
    return None


def _str(msg: EmailMessage, name: str) -> str:
    """A header of the parent as it was written: the raw value, unfolded and trimmed,
    "" if absent. Raw, because the parser repairs a broken msg-id list (it reads
    ``<r@h <x@h>`` as ``<r@h> <x@h>``), and a copied value must be one the parent
    really carries. Only an RFC 2047 encoded-word is decoded. A header the parser
    chokes on reads as one that fails ``is_msg_id``, so it is dropped, never raised."""
    try:
        raw = next((v for k, v in msg.raw_items() if k.lower() == name.lower()), None)
        if raw is None:
            return ""
        val = re.sub(r"\r?\n(?=[ \t])", "", str(raw)).strip()
        return str(msg.get(name)).strip() if "=?" in val else val
    except Exception:  # noqa: BLE001 - the parent is someone else's mail
        return "\x00"


# -- the headers --------------------------------------------------------------

def reply_headers(mid: str, parent: Optional[EmailMessage],
                  warn: Callable[[str], None] = _warn) -> dict:
    """The headers a reply to MID carries, in order: In-Reply-To, References, Call-ID.

    - In-Reply-To is MID.
    - References is the parent's root (its References[0], unless that is MID) then
      MID; MID alone with no parent, or a parent with no References.
    - Call-ID is the parent's, verbatim, if it has one.

    A value copied from the parent must be one msg-id (``is_msg_id``); one that
    isn't is dropped, with a line on stderr, and the line still threads on MID.
    No parent: MID alone, and a line on stderr. Never raises for the parent.
    """
    mid = norm_id(mid)
    out = {"In-Reply-To": mid, "References": mid}
    if parent is None:
        warn(f"mouth: {mid} is not in the heard box; the line threads on it alone")
        return out
    refs = _str(parent, "References")
    if refs.strip():
        m = _TOKEN.match(refs)
        root = m.group(1) if m else refs.split()[0]
        if root != mid:
            if is_msg_id(root):
                out["References"] = f"{root} {mid}"
            else:
                warn(f"mouth: the parent's References[0] {root!r} is not a Message-ID; "
                     f"dropped, the line threads on {mid}")
    call = _str(parent, "Call-ID")
    if call:
        if is_msg_id(call):
            out["Call-ID"] = call
        else:
            warn(f"mouth: the parent's Call-ID {call!r} is not a Message-ID; dropped")
    return out
