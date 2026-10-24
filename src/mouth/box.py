"""The queue is a maildir: new = to speak, cur = done.

The mouth's queue is a maildir, and every line is dropped into it as a
mail, whichever way it comes in (``mouth say`` or postfix), so the
player reads every line from one place, in one
format.

    new/   lines to speak, one mail each. Every way in lands here:
           `mouth say` drops one (tmp/ then rename, about 1 ms), and
           postfix delivers mail to mouth@<host> (about 250 ms).
    cur/   done, each flagged in its name (maildir ``:2,FLAGS``): S spoken
           (all or part), T not spoken (retracted, replaced, stopped,
           refused, an error), P passed (expired, or stale). A line
           playing now sits in cur/ with no flag yet.
    tmp/   being written.

Where: ``$MOUTH_MAILBOX``; else, for the live mouth dir, the
mouth's own mailbox ``~/.mail/agents/mouth`` (the box is the queue and the
log, the single source of truth); else
``<mouth dir>/box``, so a test or a dry run never touches the live one.

Order, a playlist: ``X-Mouth-Priority`` sorts first,
lower plays sooner: ``now`` (-1, and it cuts the line playing), ``next``
(0), a number (10, 20, 30...), none (50: the back of the list). Within a
priority, arrival order: ``X-Mouth-Order`` (nanoseconds), else the file's
mtime. ``Importance: high`` is ``next``.

Replace is a supersede: a mail whose ``Supersedes:`` names a line still in
new/ takes that line's place (its priority, order and utt); the older is
filed T when the newer is taken. An EMPTY body retracts: both are filed T
at once. A supersede of a line already gone is news, played on its own; an
empty one is filed T, and cuts the line if it is playing. Nothing is ever
edited in place.

A mail from outside (not a local drop) is spoken the way ``mouth
mail`` spoke it: the plain body un-markdowned, or the Subject; who may
speak is the same From check (``allowed``).

What a line may make the mouth DO is a second question: read a
file, route into a call, cut in (``now``, or a retract of the line playing),
or take another sender's line. A LOCAL drop may do all of it: its first line
is a MAC that only ``drop()`` mints (``X-Mouth-Raw: 1`` alone is a header any
sender can write). A SIGNED mail may do what its signature covers, as the
verifier's own verdict states it (``message verify --json --trust``):
an ask is kept only when the value acted on is the value signed. Any other mail
plays a sound only from the sounds directory, and nothing more. ``_trust``
judges the final item.
"""

from __future__ import annotations

import email
import email.headerregistry
import email.parser
import email.policy
import email.utils
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import socket
import sys
import threading
import time
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, NamedTuple, Optional

from ears import envnames, message_bin, operator_config
from ears.maildir import private as _private_maildir

from .paths import default_mouth_dir, mouth_dir
from .voices import _pct, default_voice

DEFAULT_RANK = 50.0
RANKS = {"now": -1.0, "next": 0.0}
INTERESTS = ("high", "normal", "low", "zero")
_PANS = {"left": -1.0, "right": 1.0, "both": None}

#: item key -> header. Anything else a line carries rides in X-Mouth-Extra (JSON).
FIELDS = {
    "utt": "X-Mouth-Utt", "voice": "X-Mouth-Voice", "speed": "X-Mouth-Speed",
    "backend": "X-Mouth-Backend", "device": "X-Mouth-Device", "pan": "X-Mouth-Pan",
    "priority": "X-Mouth-Priority", "hold": "X-Mouth-When", "interest": "X-Mouth-Interest",
    "expires_s": "X-Mouth-Expires", "file": "X-Mouth-File", "start": "X-Mouth-Start",
    "end": "X-Mouth-End", "log_dir": "X-Mouth-Log-Dir", "mail_log": "X-Mouth-Mail-Log",
    "agent": "X-Mouth-Agent", "session": "X-Session-From", "resume_of": "X-Mouth-Resume-Of",
    "volume": "X-Mouth-Volume", "lead_s": "X-Mouth-Lead",
}
#: A reply: item key -> header. ``mouth say --in-reply-to`` sets them; the
#: mail carries them, straight after Message-ID.
REPLY = {"in_reply_to": "In-Reply-To", "references": "References", "call_id": "Call-ID"}
#: Carried by the mail itself (body, Message-ID, From, order, reply), never as extras.
_OWN = ("text", "requested_t", "requested_ts", "expires_t", "wait", "mail_id", "mail_from",
        *REPLY)

# Call-ID holds one msg-id. As an unknown header it would be unstructured text, and
# the folder RFC 2047-encodes a token longer than a line (=?utf-8?q?...): no longer
# an id anyone can match. As a Message-ID-shaped header it is written plainly.
_REGISTRY = email.headerregistry.HeaderRegistry()
_REGISTRY.map_to_type("call-id", email.headerregistry.MessageIDHeader)
_POLICY = email.policy.default.clone(header_factory=_REGISTRY)


# -- where, and the maildir moves ---------------------------------------------

def box_dir(d: Optional[Path] = None) -> Path:
    env = envnames.get("MOUTH_MAILBOX")
    if env:
        return Path(env).expanduser()
    d = Path(d or mouth_dir()).expanduser()
    if os.path.abspath(d) == os.path.abspath(default_mouth_dir()):
        return Path.home() / ".mail" / "agents" / "mouth"
    return d / "box"


def ensure(bx: Path) -> Path:
    """BX as a maildir, the box and its tmp/new/cur 0700 (an existing 0755 one repaired)."""
    _private_maildir(bx)
    return bx


def _host() -> str:
    return socket.gethostname().split(".")[0].lower()


def _base(name: str) -> str:
    return name.split(":2,")[0]


def file_to(path: Path, flags: str) -> Optional[Path]:
    """Move a mail from new/ (or a taken one in cur/) to cur/NAME:2,FLAGS. None if it is gone."""
    dest = path.parent.parent / "cur" / f"{_base(path.name)}:2,{''.join(sorted(flags))}"
    try:
        path.rename(dest)
    except FileNotFoundError:
        return None
    return dest


# -- markdown to speech, and who may speak (from inbox.py) --------------------

def speakable(text: str) -> str:
    """Markdown to something a voice can read aloud."""
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)          # [text](url) -> text
    t = re.sub(r"`{1,3}([^`]*)`{1,3}", r"\1", t)                 # `code` -> code
    t = re.sub(r"(\*\*|__)(.+?)\1", r"\2", t)                    # **bold**
    t = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", t)                  # # heads
    t = re.sub(r"(?m)^\s*(?:[-*+]|\d+[.)])\s+", "", t)           # list bullets
    t = re.sub(r"(?m)^\s*>\s?", "", t)                           # > quotes
    return re.sub(r"[ \t]+", " ", t).strip()


def _admits(sender: str) -> tuple:
    """``(in_allow, on_host, extra)``: today's two From tests, and ALLOW's split."""
    s = sender.lower()
    extra = [x.strip().lower() for x in envnames.get("MOUTH_MAIL_ALLOW", "").split(",") if x.strip()]
    in_allow = any(s == x or (x.startswith("@") and s.endswith(x)) for x in extra)
    at = s[s.rfind("@"):] if "@" in s else ""
    host = _host()
    return in_allow, at == f"@{host}" or at.startswith(f"@{host}."), extra


def allowed(sender: str) -> bool:
    """A From on this host (``@<host>``, ``@<host>.<tailnet>``) or in $MOUTH_MAIL_ALLOW.

    With an operator block (``session operator``): on this host or privileged,
    AND in ALLOW when ALLOW is set. ALLOW then only narrows; it never adds."""
    in_allow, on_host, extra = _admits(sender)
    if not operator_config.current().privileged:
        return in_allow or on_host                                    # unconfigured: today
    ok = on_host or operator_config.is_privileged(sender, _host())
    return ok and (in_allow or not extra)


def _via_allow_fallback(sender: str) -> bool:
    """ALLOW alone admits SENDER while the operator reader did not answer."""
    in_allow, on_host, _ = _admits(sender)
    if not in_allow or on_host:
        return False
    operator_config.current()  # fallback_reason() reports the answer current() last gave
    return operator_config.fallback_reason() is not None


# -- a line as a mail, and back ------------------------------------------------

def _subject(text: str) -> str:
    line = " ".join((text or "").split())
    return line if len(line) <= 69 else line[:68] + "…"


def drop(bx: Path, item: dict, *, supersedes: Optional[str] = None) -> Path:
    """Write ITEM into BX/new as a mail: tmp/, then one rename. Sets ``item['mail_id']``.

    A hold the room default set (``hold_from: room``, ``_room_hold``) is the reader's,
    never the line's: an amend re-drops the line it read, and writes it unheld, as
    its sender asked, so the re-read decides again."""
    given = item
    if item.get("hold_from") == "room":
        item = {k: v for k, v in item.items() if k != "hold_from"}
        item["hold"] = None
    ensure(bx)
    host = _host()
    order = time.time_ns()
    mid = f"<{order}.{item['utt']}@{host}.mouth>"
    m = EmailMessage(policy=_POLICY)
    m["From"] = f"{item.get('agent') or 'mouth'}@{host}"
    m["To"] = f"mouth@{host}"
    m["Date"] = email.utils.formatdate(item["requested_t"], localtime=True)
    m["Subject"] = _subject(item["text"])
    m["Message-ID"] = mid
    reply = item.get("in_reply_to")
    if supersedes:
        m["Supersedes"] = supersedes
    if reply:  # a reply keeps its thread, amended or resumed
        m["In-Reply-To"] = reply
        m["References"] = item.get("references") or reply
        if item.get("call_id"):
            m["Call-ID"] = item["call_id"]
    elif supersedes:
        m["In-Reply-To"] = supersedes
        m["References"] = supersedes
    m["X-Mouth-Raw"] = "1"
    m["X-Mouth-Order"] = str(order)
    m["X-Mouth-Requested"] = repr(float(item["requested_t"]))
    if item.get("wait"):
        m["X-Mouth-Wait"] = "1"
    for k, h in FIELDS.items():
        v = item.get(k)
        if v is not None:
            m[h] = str(v)
    extras = {k: v for k, v in item.items()
              if k not in FIELDS and k not in _OWN and not k.startswith("_") and v is not None}
    if extras:
        m["X-Mouth-Extra"] = json.dumps(extras, ensure_ascii=True, separators=(",", ":"))
    m.set_content(item["text"] + "\n")  # read() takes exactly this newline back off
    name = f"{order}.{item['utt']}.{host}"
    tmp = bx / "tmp" / name
    tmp.write_bytes(_minted(bx, bytes(m)))   # a local drop: the MAC line first
    dest = bx / "new" / name
    tmp.rename(dest)
    item["mail_id"] = given["mail_id"] = mid
    return dest


def _float(v) -> Optional[float]:
    """A header's number, or None: a numeric fact is None or finite.
    nan, inf, -inf and 1e400 (which parses to inf) read as ABSENT, as garbage does, so
    no caller (priority's ``int()``, expiry and speed sums) ever meets one."""
    try:
        f = float(v) if v not in (None, "") else None
    except ValueError:
        return None
    return f if f is not None and math.isfinite(f) else None


def _when(v) -> Optional[float]:
    """A header's epoch seconds, or None if it is not one a clock can hold:
    nan, inf, -inf, 1e20 or garbage is treated as ABSENT, never raised on."""
    t = _float(v)
    if t is None or not math.isfinite(t):
        return None
    try:
        datetime.fromtimestamp(t)
    except (OverflowError, OSError, ValueError):
        return None
    return t


#: A utt is ONE file name under ``done/``: ``_done`` writes
#: ``d/done/<utt>.json``. Anything else from a header or an extra is replaced.
_SAFE_UTT = re.compile(r"[A-Za-z0-9._-]{1,128}")


def safe_utt(utt) -> bool:
    """True if UTT is one safe path component: no ``/``, no ``..``, nothing outside the charset."""
    return isinstance(utt, str) and utt not in (".", "..") and _SAFE_UTT.fullmatch(utt) is not None


# -- where a mail may make the player write --------------------------

def heard_root() -> Path:
    """The root of every heard log the player writes: ``$MOUTH_LOG_DIR``, else
    the ears' default (``<sessionvoice home>/logs/conversations``). The PLAYER's env."""
    env = envnames.get("MOUTH_LOG_DIR")
    if env:
        return Path(env).expanduser()
    from ears import heard

    return heard.log_dir()


def mail_log_root() -> Path:
    """The root of the mouth's mail log: ``$MOUTH_MAIL_LOG``'s box, else the
    mouth's own box (what ``1`` means there). The PLAYER's env."""
    from . import maillog

    return Path(maillog.box_from_env() or Path.home() / ".mail" / "agents" / "mouth")


def confined(path, root: Path) -> Optional[str]:
    """PATH, as given, if its REAL path lies under ROOT's real path (or is it), else None.

    Both sides go through ``os.path.realpath``: symlinks are resolved and ``..``
    collapsed, so ``<root>/../x`` and a link in the root pointing out both fail, and a
    root reached through a link (macOS ``/var`` is ``/private/var``) still matches. A
    relative path is taken against the cwd, as the write that follows takes it. What
    is not a non-empty string (an extra's number, list, null) is None."""
    if not isinstance(path, str) or not path or "\0" in path:
        return None
    try:
        real, top = os.path.realpath(path), os.path.realpath(root)
        return path if os.path.commonpath([real, top]) == top else None
    except (OSError, ValueError):   # a loop, or two paths with no common root
        return None


def in_a_queue(path: str, *boxes) -> bool:
    """True if PATH's real path lies in the ``tmp/``, ``new/`` or ``cur/`` of a maildir in
    BOXES. Under a root is not enough when the root is a maildir (the mail log's root is
    the mouth's own box by default): a log written in ``<box>/new`` comes back as
    directories in the queue, which serve cannot file aside twice (ENOTEMPTY), so the
    mouth crash-loops.

    The paths are compared, and so are the directories themselves (device and inode):
    on a case-insensitive filesystem (macOS's default) ``<box>/NEW`` IS ``<box>/new``,
    but ``realpath`` keeps the case it was given, so a string test alone lets it by."""
    subs = [Path(bx) / sub for bx in boxes for sub in ("tmp", "new", "cur")]
    if any(confined(path, q) is not None for q in subs):
        return True
    if not isinstance(path, str) or not path or "\0" in path:
        return False
    ids = set()
    for q in subs:
        try:
            st = os.stat(q)
        except (OSError, ValueError):   # not made yet: nothing to write into either
            continue
        ids.add((st.st_dev, st.st_ino))
    if not ids:
        return False
    try:
        p = Path(os.path.realpath(path))
    except (OSError, ValueError):
        return False
    for a in (p, *p.parents):   # the path or the nearest of its parents that exists
        try:
            st = os.stat(a)
        except (OSError, ValueError):
            continue
        if (st.st_dev, st.st_ino) in ids:
            return True
    return False


#: Keys naming a place the player WRITES (``heard.append``'s directory, ``maillog``'s
#: box) or the thread it writes under (``log_root``, a Message-ID). The mouth sets each
#: from the mail's own header or its env; an extra may never set one it has set.
_PLACES = ("log_dir", "mail_log", "log_root")


def _log(rec: dict) -> None:
    """One JSON line to stderr: the player's log (its plist sends both streams to one
    file), and never in a reader's stdout (``mouth status`` prints JSON there)."""
    print(json.dumps(rec), file=sys.stderr, flush=True)


# -- who may make the mouth fetch, route into a call, or cut ----------
#
# The mouth's From check (``allowed``) says who may SPEAK. What a line may make it
# DO is judged here, on the FINAL item (after the extras), by the trust of the field
# that set each effect: a LOCAL drop (a MAC only the box's own writer can mint) is
# trusted; a SIGNED mail only for a field whose value the verifier's verdict says it
# signed (``message verify --json --trust``; never the mouth's own read of the
# signature header); any other mail for
# nothing.

#: A local drop's first line: HMAC-SHA256, keyed by the box's own key, of every byte
#: after it. Only ``drop()`` mints it; ``read()`` checks it and never creates the key.
_LOCAL = b"X-Mouth-Local: v1 "
#: The box's key: 32 random bytes at the box's root, outside tmp/, new/ and cur/.
KEY_NAME = ".mouth-key"
_KEY_LEN = 32


def _key(bx: Path, create: bool = False) -> Optional[bytes]:
    """The box's MAC key, or None (absent, unreadable, or not 32 bytes: no file is local).

    Only ``drop()`` passes ``create``. Two first drops may race: each writes its own
    tmp file ``O_EXCL`` and ``link()``s it to the key's name, and the loser (EEXIST)
    reads the winner's key, so every line minted in the box checks against one key."""
    p = Path(bx) / KEY_NAME
    try:
        k = p.read_bytes()
    except OSError:
        if not create:
            return None
    else:
        return k if len(k) == _KEY_LEN else None
    tmp = Path(bx) / f"{KEY_NAME}.{os.getpid()}.{secrets.token_hex(6)}.part"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, secrets.token_bytes(_KEY_LEN))
        finally:
            os.close(fd)
        os.link(tmp, p)
    except FileExistsError:   # another first drop won the race: its key is the box's
        pass
    finally:
        tmp.unlink(missing_ok=True)
    return _key(bx)


def _mac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).hexdigest().encode("ascii")


def _minted(bx: Path, data: bytes) -> bytes:
    """DATA (a whole mail) as a local drop: the MAC line, then DATA unchanged."""
    key = _key(bx, create=True)
    if key is None:
        raise ValueError(f"mouth: {Path(bx) / KEY_NAME} is not a mouth key ({_KEY_LEN} bytes); "
                         "delete it and say again")
    return _LOCAL + _mac(key, data) + b"\n" + data


def is_local(raw: bytes, bx: Path) -> bool:
    """True if RAW, read from box BX, is a local drop: its first line is exactly the
    MAC line, and the MAC of every byte after it checks against BX's key. A mail
    through the MTA has no key, and a replayed drop has the MTA's headers on top."""
    if not raw.startswith(_LOCAL):
        return False
    nl = raw.find(b"\n")
    key = _key(bx) if nl > 0 else None
    return key is not None and hmac.compare_digest(raw[len(_LOCAL):nl], _mac(key, raw[nl + 1:]))


def sounds_root(d: Optional[Path] = None) -> Path:
    """Where a mail's ``file`` may be: ``$MOUTH_SOUNDS_DIR``, else
    ``<mouth dir>/sounds``. The PLAYER's env. Never created by the mouth: absent,
    no mail's file passes."""
    env = envnames.get("MOUTH_SOUNDS_DIR")
    if env:
        return Path(env).expanduser()
    return Path(d or mouth_dir()).expanduser() / "sounds"


def _sound(v, root: Path) -> Optional[str]:
    """V's real path if a mail may play it: a non-empty string, no NUL, no ``://``,
    really under ROOT (``confined``: ``..`` and a link out both fail), a file."""
    if not isinstance(v, str) or "://" in v or confined(v, root) is None:
        return None
    real = os.path.realpath(v)
    return real if os.path.isfile(real) else None


#: The off switch and the test fence: set AT ALL (``""`` included), no mail is
#: signed. Only when it is unset is ``message`` looked up on PATH. A path in it is
#: not honoured: an env var must not choose the trust oracle (as
#: ``MOUTH_SESSION_BIN`` in ``ears.operator_config``).
VERIFY_VAR = "MOUTH_VERIFY_BIN"
VERIFY_TIMEOUT_S = operator_config.TIMEOUT_S


#: The verifier's verdict is one JSON object on one stdout line, ASCII, at most this
#: long (a real one is a few KB: the covered values of one header block).
_VERDICT_MAX = 1 << 20
#: The only (exit, trust.verdict) pairs that make a mail SIGNED.
_TRUSTED = frozenset({(0, "FULL"), (4, "UNBLESSED")})


class Signed(NamedTuple):
    """What the verifier says it verified (``message verify --json --trust``, schema 1):
    the version, and each name of its ``h`` (as signed, case included) with the values
    it signed for that name, read back from the bytes ``ssh-keygen`` verified. A name
    outside ``h`` is not in ``covered``; an absent field is signed as ``("",)``.
    Immutable: one is memoised and handed to every reader."""
    version: str
    covered: Mapping[str, tuple]


def parse_verdict(rc: int, out) -> Optional[Signed]:
    """The ``Signed`` that verifier exit RC and stdout OUT (bytes) state, else None.

    sessionmail ``docs/verify-json-verdict.md``: no object means no verdict, so this
    fails closed. All must hold: RC is 0 or 4; OUT is ONE ASCII line, one JSON object
    and then one LF, nothing before or after; ``schema`` is the int 1 and ``rc`` equals
    RC; ``trust.verdict`` is FULL (exit 0) or UNBLESSED (exit 4); ``signature.version``
    is "1" or "2"; ``h`` is a list of distinct strings, and ``covered`` lists one entry
    per name of ``h``, in order, each with ``values``, a non-empty list of strings. An
    entry carried as ``values_b64`` (a value that is not UTF-8) fails the whole
    verdict: no gated field, and no field of the floor, is anything but ASCII when
    honestly sent. Pure: no subprocess, no I/O (the reader's cold path)."""
    if rc not in (0, 4) or not isinstance(out, bytes) or len(out) > _VERDICT_MAX:
        return None
    if not out.endswith(b"\n") or out.count(b"\n") != 1:
        return None
    try:
        text = out[:-1].decode("ascii")
    except UnicodeDecodeError:
        return None
    if not (text.startswith("{") and text.endswith("}")):
        return None
    try:
        v = json.loads(text)
    except (ValueError, RecursionError):
        return None
    if not isinstance(v, dict) or type(v.get("schema")) is not int or v["schema"] != 1:
        return None
    if type(v.get("rc")) is not int or v["rc"] != rc:
        return None
    trust, sig = v.get("trust"), v.get("signature")
    if not isinstance(trust, dict) or (rc, trust.get("verdict")) not in _TRUSTED:
        return None
    if not isinstance(sig, dict) or sig.get("version") not in ("1", "2"):
        return None
    h, cov = sig.get("h"), sig.get("covered")
    if not isinstance(h, list) or not all(isinstance(n, str) for n in h) or len(set(h)) != len(h):
        return None
    if not isinstance(cov, list) or len(cov) != len(h):
        return None
    covered: dict = {}
    for name, entry in zip(h, cov):
        if not isinstance(entry, dict) or entry.get("name") != name or "values_b64" in entry:
            return None
        vals = entry.get("values")
        if not isinstance(vals, list) or not vals or not all(isinstance(x, str) for x in vals):
            return None
        covered[name] = tuple(vals)
    return Signed(sig["version"], MappingProxyType(covered))


def verify_signed(path) -> Optional[Signed]:
    """What the mail at PATH is signed with (a ``Signed``), or None: it is unsigned.

    Asks ``message verify --json --trust --quiet PATH`` and believes only its verdict
    (``parse_verdict``): FULL (exit 0) or UNBLESSED (exit 4), the signature good AND a
    registry cert binding its key to the From's local part. Plain mode proves nothing
    about who (the signature carries its own key). The mouth never re-reads the mail's
    ``X-Session-Signature`` itself. Every other outcome is unsigned: no
    object (a ``message`` older than sessionmail's ``--json`` exits 2 with
    none), another exit, a timeout, an OSError, no ``message`` on PATH or where uv
    installs it (``ears.message_bin``; this repo ships alone: fail closed), or
    ``$MOUTH_VERIFY_BIN`` set at all. The seam the tests fake."""
    if envnames.isset(VERIFY_VAR):
        return None
    import subprocess  # here: a reader's cold path (mouth timeline) never imports it

    exe = message_bin.find()     # PATH, then where uv installs it: a launchd PATH holds neither
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "verify", "--json", "--trust", "--quiet", os.path.abspath(path)],
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=VERIFY_TIMEOUT_S)
        return parse_verdict(r.returncode, r.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


#: Verdicts, once per file: ``(box, name, size, mtime_ns)`` -> verify_signed's answer.
#: A line's bytes never change after delivery (the maildir only renames). Past
#: ``_MEMO_MAX`` entries the memo starts again: a mail asked twice costs a second run.
_MEMO_MAX = 4096
_VERDICTS: dict = {}
_PENDING: set = set()
_VLOCK = threading.Lock()


def _ask_verifier(path: Path, key: tuple) -> None:
    try:
        v = verify_signed(path)
    except Exception:  # noqa: BLE001 - a verifier that raises is a mail that is unsigned
        v = None
    with _VLOCK:
        if len(_VERDICTS) >= _MEMO_MAX:
            _VERDICTS.clear()
        _VERDICTS[key] = v
        _PENDING.discard(key)


def _verdict(path: Path, key: tuple, sync: bool) -> tuple:
    """``(known, verdict)``. SYNC runs the verifier now (an acting ``Queue``, between
    lines). Otherwise a miss starts ONE daemon thread for the file and answers
    ``(False, None)``: the mail reads as unsigned until the verdict lands, so the
    audio path (``play_one``'s poll, a bed's, prefetch) never waits on a verifier."""
    with _VLOCK:
        if key in _VERDICTS:
            return True, _VERDICTS[key]
        if not sync:
            if key not in _PENDING:
                _PENDING.add(key)
                threading.Thread(target=_ask_verifier, args=(path, key), daemon=True,
                                 name="mouth-verify").start()
            return False, None
    _ask_verifier(path, key)
    with _VLOCK:
        return True, _VERDICTS.get(key)


_SAID_ONCE: set = set()


def _log_once(key: tuple, rec: dict) -> None:
    """``_log`` REC once per KEY in this process: ``read()`` runs once per file per
    ``Queue``, and the player makes a ``Queue`` a line."""
    if key not in _SAID_ONCE:
        if len(_SAID_ONCE) >= _MEMO_MAX:
            _SAID_ONCE.clear()
        _SAID_ONCE.add(key)
        _log(rec)


def _is_call(device) -> bool:
    """The output's own test of a call device (``output.py``): ``call`` or ``call:*``."""
    dev = str(device or "").strip().lower()
    return dev == "call" or dev.startswith("call:")


#: The facts the player and hold do sums on (``float()``, ``+``, ``<``, ``>``) that an
#: extra can set: ``hold.check`` floats ``expires_t``, ``expires_s``
#: and ``requested_t``; the player compares ``requested_t`` (player.py:366/448/596/749),
#: divides by ``speed``, and hands ``start``/``end`` to the file decoder and ``pan`` to
#: the output. ``lead_s`` is checked here too (the player's ``lead_setting`` checks it
#: again). ``volume`` is not: every reader takes it through ``voices._pct``.
_NUMERIC = ("requested_t", "expires_t", "expires_s", "speed", "start", "end", "lead_s", "pan")


def _real(v) -> bool:
    """A finite int or float, never a bool (JSON's NaN, Infinity and 10**400 are not)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    try:
        return math.isfinite(v)
    except OverflowError:   # an int too big for a float
        return False


def _numeric_extras(item: dict, own: dict, extras: dict, mid: str) -> None:
    """Put back the mail's OWN value of each numeric fact an extra set to something
    the player cannot do sums on, and log it (one JSON line each). ``requested_t``
    must be a time a clock can hold; the rest may also be None. Extras still set
    them, and any other key (test_mouth_notice.py:957): only the value is checked."""
    for k in _NUMERIC:
        if k not in extras:
            continue
        v = item[k]
        if k == "requested_t":
            good = _real(v) and _when(v) is not None
        elif k == "pan":   # one range, as ``_pan`` reads a header's
            good = v is None or (_real(v) and -1.0 <= v <= 1.0)
        else:
            good = v is None or _real(v)
        if not good:
            _log({"bad_extra": k, "mail_id": mid[:200], "value": repr(v)[:40], "as": own[k]})
            item[k] = own[k]


def _order(v: Optional[str], mtime_ns: int, mid: str) -> int:
    """``X-Mouth-Order``'s nanoseconds, or the file's MTIME_NS if it is not a time a
    clock can hold (as ``X-Mouth-Requested`` is read): 1e30, inf,
    nan, garbage, and digits that ``int()`` takes but no clock does (10**30, -10**30,
    10**400) all read as ABSENT, logged once, never raised on. Requested or not."""
    try:
        n = int(v or "")
        good = _real(n) and _when(n / 1e9) is not None   # _real first: 10**400 / 1e9 raises
    except (ValueError, OverflowError):   # int(): garbage, a float, or past 4300 digits
        n, good = None, False
    if good:
        return n
    if v:
        _log({"bad_header": "X-Mouth-Order", "mail_id": mid[:200], "value": v[:40], "as": "absent"})
    return mtime_ns


def _places(item: dict, extras: dict, own: dict, mid: str, sender: str, bx: Path) -> None:
    """Fence every place a mail can make the player write.

    1. An extra never sets a key of ``_PLACES`` that the mouth set itself from the
       mail or its env (``item.update(extras)`` has just overridden it): the
       mouth's OWN value is put back, and logged. ``log_root`` rides as an extra
       only on a raw line (``mouth say``'s thread root), where the mouth sets none.
    2. ``log_dir`` must lie under ``heard_root()`` and ``mail_log`` under
       ``mail_log_root()`` (``confined``). One that does not is IGNORED, with one
       WARNING naming the sender and the refused path, and the line still plays:
       its heard rows go to the root itself (``$MOUTH_LOG_DIR``, else the
       heard default), and its mail log to the player's own (``maillog``), or none.
       The player's own ``$MOUTH_LOG_DIR``, as given, is the root: it
       passes even unexpanded (``~/logs``), as the write that follows takes it.
    3. Neither may lie in the ``tmp/``, ``new/`` or ``cur/`` of BX, the box this mail
       was read from, nor ``mail_log`` in its root's (``in_a_queue``): the same refusal."""
    for k in _PLACES:
        if k in extras and k in own:
            _log({"bad_extra": k, "mail_id": mid[:200], "from": sender[:200],
                  "value": repr(extras[k])[:200], "as": own[k]})
            item[k] = own[k]
    from . import maillog

    for k, header, root, home, maildir in (
            ("log_dir", "X-Mouth-Log-Dir", heard_root, envnames.get("MOUTH_LOG_DIR") or None, False),
            ("mail_log", "X-Mouth-Mail-Log", mail_log_root, maillog.box_from_env(), True)):
        v = item.get(k)
        if v is None or v == home:
            continue
        top = root()
        if confined(v, top) is None or in_a_queue(v, bx, *((top,) if maildir else ())):
            _log({"level": "WARNING", "refused_path": header, "from": sender[:200] or None,
                  "value": str(v)[:200], "root": str(top), "mail_id": mid[:200], "as": home})
            item[k] = home


def _ts(t: float) -> str:
    return datetime.fromtimestamp(t).astimezone().isoformat(timespec="milliseconds")


def _volume(v: Optional[str]) -> Optional[int]:
    return _pct(v) if v is not None else None


def _pan(v: Optional[str]) -> Optional[float]:
    if v is None:
        return None
    v = v.strip().lower()
    if v in _PANS:
        return _PANS[v]
    p = _float(v)
    return p if p is not None and -1.0 <= p <= 1.0 else None


def _body(msg) -> str:
    part = msg.get_body(preferencelist=("plain",))
    return part.get_content() if part is not None else ""


#: A call's box:
#: ``calls+<bot>-<call id>``, carrying both directions. A mail to it is a line
#: for THAT call: it plays into ``call:<id>``, in the sender's own voice, polite
#: (it waits for the user's turn to end) unless it asks to barge (``X-Mouth-Priority:
#: now`` or ``X-Mouth-When: now``). The ears' own turn mails in the same box
#: (``Ears-Kind``) are the user's words, never a line: they are filed unspoken.
_CALL_BOX = re.compile(r"^calls\+(?P<bot>[a-z0-9_.-]+)-(?P<call>\d+)(?:@|$)", re.I)


def call_box(*addrs: Optional[str]) -> Optional[tuple]:
    """(bot, call id) for the first address that is a call's box, else None."""
    for a in addrs:
        for one in (a or "").split(","):
            m = _CALL_BOX.match(email.utils.parseaddr(one.strip())[1] or one.strip())
            if m:
                return m.group("bot").lower(), int(m.group("call"))
    return None


#: A field's name as said rows and logs spell it.
_FIELD_NAMES = {"x-mouth-extra": "X-Mouth-Extra", "x-mouth-device": "X-Mouth-Device",
                "x-mouth-priority": "X-Mouth-Priority", "x-mouth-when": "X-Mouth-When",
                "importance": "Importance", "to": "To", "x-mouth-file": "X-Mouth-File"}


def _trust(item: dict, msg, extras: dict, on_call, local: bool, rank: float,
           path: Path, key: tuple, sync: bool, d: Optional[Path]) -> float:
    """Gate the FINAL item: what it reads, where it plays,
    and whether it cuts in. Called after the extras, before the file block. Sets
    ``_untrusted`` (the line is refused whole), drops an untrusted ``now``, puts a
    call line's hold back, sets ``_sup_trust``; returns the line's rank."""
    mid, sender = item["mail_id"], item["mail_from"] or ""
    item.update(_sup_trust=local, _sup_why=None, _verify_pending=False, _vkey=key)
    if local:
        return rank   # the user's own line: anything it asks (a file anywhere, a call, now)

    def h(k: str) -> Optional[str]:
        v = msg.get(k)
        return str(v).strip() if v is not None else None

    # the field that set each effect, as read() set it
    dev_src = ("x-mouth-extra" if "device" in extras else "x-mouth-device" if h("X-Mouth-Device")
               else "to" if on_call else None)   # None: the player's own (env, settings, default)
    call = dev_src is not None and _is_call(item.get("device"))
    pri_src = ("x-mouth-extra" if "priority" in extras
               else "x-mouth-priority" if h("X-Mouth-Priority") else "importance")
    now = str(item.get("priority")).strip().lower() == "now"
    jump = rank < 0   # only X-Mouth-Priority sets the rank: now (-1) or a negative number
    hdr_now = (h("X-Mouth-Priority") or "").lower() == "now"
    barge_src = ("x-mouth-extra" if "hold" in extras else "x-mouth-when" if h("X-Mouth-When")
                 else "x-mouth-priority" if on_call and hdr_now else dev_src)
    barge = call and item.get("hold") != "turn-end"
    barge_key = "hold" if "hold" in extras else "device"   # the extra's key, if it is the extra
    sup = msg.get("Supersedes") is not None

    verdict, pending = None, False
    if msg.get("X-Session-Signature") is not None and (call or now or jump or barge or sup):
        known, verdict = _verdict(path, key, sync)
        pending = not known
    item["_verify_pending"] = pending

    def note(k: str, rec: dict) -> None:   # once per mail; never on a verdict not yet heard
        if not pending:
            _log_once(("untrusted", k, mid), {"mail_id": mid[:200], "from": sender[:200], **rec})

    def mine(field: str, key: Optional[str]):
        """The value the effect acts on: the header as ``read()`` set the item from it,
        or KEY of the extra, as ``read()`` parsed it."""
        if field == "x-mouth-extra":
            return extras.get(key)
        return h(field)

    def why(field: str, key: Optional[str] = None) -> Optional[str]:
        """None if the value the mouth acts on for FIELD (KEY of ``X-Mouth-Extra``) is
        the value the verifier's verdict says was signed; else why not.

        Names and values come from the verdict alone (``Signed``), never from the
        mail's own ``X-Session-Signature``. Python's parse is one side of an equality
        check only: it may refuse an ask, never grant one."""
        if verdict is None:
            return "unsigned, verdict pending" if pending else "unsigned"
        ver, covered = verdict
        vals = covered.get(field)   # case-exact: a name signed upper-case covers nothing
        if vals is None:   # v1's h is five fixed names: never an X-Mouth-* nor Supersedes
            return (f"signed v{ver}, which does not sign {field}" if ver == "1"
                    else f"signed v{ver}, but h= does not cover {field}")
        if len(vals) != 1:
            return f"signed v{ver}, but it signs {len(vals)} {field} fields"
        if vals[0] == "":   # an absent field is signed empty; Python reads one here
            return f"signed v{ver}, but it signs no {field}"
        if field == "to":   # on_call: the call box must be the To the signature signs
            if call_box(vals[0]) != on_call:
                return (f"signed v1, which signs only its To {vals[0]!r}, not the call box"
                        if ver == "1" else f"signed v{ver}, but its To does not name the call box")
        elif field == "x-mouth-extra":
            try:
                ext = json.loads(vals[0])
            except (ValueError, RecursionError):
                ext = None
            ext = {k: v for k, v in ext.items() if not str(k).startswith("_")} \
                if isinstance(ext, dict) else {}
            if key not in ext or json.dumps(ext[key], sort_keys=True) != \
                    json.dumps(mine(field, key), sort_keys=True):
                return f"signed v{ver}, but the {key} its {field} signs is not the one read"
        elif vals[0] != mine(field, key):
            return f"signed v{ver}, but the {field} it signs is not the one read"
        n = len(msg.get_all(field) or [])   # a second refusal only: it never grants
        return None if n == 1 else f"signed v{ver}, but it carries {n} {field} fields"

    def said(field: str, value) -> str:
        return f"{_FIELD_NAMES.get(field, field)} {str(value)[:200]}"

    # Supersedes: trusted only when local, or its value is the one signed
    if sup:
        item["_sup_why"] = why("supersedes")
        item["_sup_trust"] = item["_sup_why"] is None
    # file: signing never widens it: a mail plays a file in the sounds dir, or none
    if item.get("file"):
        root = sounds_root(d)
        real = _sound(item["file"], root)
        if real is None:
            f = "x-mouth-extra" if "file" in extras else "x-mouth-file"
            item["_untrusted"] = f"{said(f, item['file'])} is outside the sounds directory {root}"
        else:
            item["file"] = real   # absolute: ffmpeg can never read it as a protocol
    # call: where it plays (refused whole)
    if call and not item.get("_untrusted") and why(dev_src, "device"):
        asked = f"calls+{on_call[0]}-{on_call[1]}" if dev_src == "to" else item["device"]
        item["_untrusted"] = f"{said(dev_src, asked)} needs a signed or local line ({why(dev_src, 'device')})"
    if item.get("_untrusted"):
        return rank   # refused whole: nothing else it asks matters
    # now: when it plays (the ask is dropped; the line plays)
    if now and why(pri_src, "priority"):
        note("priority", {"untrusted": _FIELD_NAMES[pri_src], "value": "now",
                          "why": why(pri_src, "priority"), "as": None})
        item["priority"] = None
    if jump and why("x-mouth-priority"):
        if not now:   # a negative number: its own ask to go ahead of now (second review F-B)
            note("rank", {"untrusted": "X-Mouth-Priority", "value": rank,
                          "why": why("x-mouth-priority"), "as": DEFAULT_RANK})
        if pri_src == "x-mouth-priority":
            item["priority"] = None
        rank = DEFAULT_RANK
    # barge: a call line that does not wait for the user's turn (now by another name)
    if barge and why(barge_src, barge_key):
        note("hold", {"untrusted": _FIELD_NAMES.get(barge_src, barge_src),
                      "value": item.get("hold"), "why": why(barge_src, barge_key), "as": "turn-end"})
        item["hold"] = "turn-end"
    return rank


_CLONES: tuple = (float("-inf"), ())   # (when looked, the clone voices then)
CLONES_TTL_S = 60.0


def _own_voice(agent: Optional[str]) -> Optional[str]:
    """The sender's own clone voice, if the clone resolver knows one by its name.

    Asks the resolver's own list (``voices.clones``), not the directory: a
    bare ``~/.voicemode/voices/<agent>`` with no reference clip would pass a
    directory test, then be refused at play time, and the line would close
    as an error and say nothing. Looked up at most once a minute."""
    global _CLONES
    a = (agent or "").strip().lower()
    if not a or not re.fullmatch(r"[a-z0-9_.-]+", a):
        return None
    t, names = _CLONES
    if time.monotonic() - t > CLONES_TTL_S:
        from . import voices

        try:
            names = tuple(voices.clones())
        except Exception:  # a broken profile must not stop the line; default voice
            names = ()
        _CLONES = (time.monotonic(), names)
    return a if a in names else None


def _device(item: dict, extras: dict, is_raw: bool, asked_from: str, d: Optional[Path]) -> None:
    """The line's device by ``say()``'s own order, ONE function (``mouth.resolve_device``):
    its ask (``X-Mouth-Device``, an extra's ``device``, or the call box it is To),
    else the player's ``$MOUTH_DEVICE``, else the host settings ``output``, else the
    system default output. Sets ``device`` and ``device_from``, the rung.

    A raw line (``mouth say``) was resolved by ``say()``: its device is kept, with
    the ``device_from`` it carries, which rides only past the caller's own choice
    (``mouth.DEVICE_RUNGS_RIDE``); a line its caller named has none. A mail from
    outside never names its own rung: one is set here."""
    from . import DEVICE_RUNGS, resolve_device

    asked = item.get("device") or None
    said_from = item.get("device_from")
    if is_raw and asked is not None:
        item["device_from"] = said_from if said_from in DEVICE_RUNGS else None
        return
    if "device" in extras:
        asked_from = "mail"
    item["device"], item["device_from"] = resolve_device(asked, asked_from, d)


def _room_hold(item: dict, when: str, extras: dict, d: Optional[Path]) -> None:
    """A line on a ROOM device that asks no hold is held ``turn-end``
    (``hold.ROOM_HOLD``), decided here, where its device is resolved (``_device``)
    and its ``now`` is trusted (``_trust``). Sets ``hold_from: room``.

    Left as it is: a line that holds already (its own ``turn-end``, or a call's);
    a line on a call (``call``/``call:<N>``: calls keep their own default); a
    ``now`` line (``--now``, a trusted ``X-Mouth-Priority: now``); a line whose
    sender said no hold (``X-Mouth-When: now``, ``mouth say --hold none``: that one
    sets ``_unheld``, or a ``hold`` in its extras); a played file (``mouth play``: a sound, never a
    voice over the user). And every line when ``hold.room_hold`` is off."""
    if item.get("hold_from") == "room":
        del item["hold_from"]   # the box's word alone: never an extra's
    if item.get("hold") == "turn-end" or _is_call(item.get("device")):
        return
    if when == "now":
        item["_unheld"] = True   # the sender's own no-hold: an amend keeps it (mouth.amend)
        return
    if str(item.get("priority")).strip().lower() == "now" or "hold" in extras:
        return
    if item.get("file"):
        return
    from . import hold, voices

    if hold.room_hold(voices.settings(d))[0]:
        item["hold"], item["hold_from"] = "turn-end", hold.ROOM


def asked_no_hold(path) -> bool:
    """True if the mail at PATH asked no hold itself (``X-Mouth-When: now``, as
    ``mouth say --hold none`` writes it): the ``_unheld`` that ``_room_hold`` sets,
    read again from the mail. A line the player has TAKEN keeps only its public
    keys in playing.json, so ``mouth.amend`` reads its mail in cur/ for it.
    False if the mail has gone."""
    try:
        raw = Path(path).read_bytes()
    except (OSError, TypeError):
        return False
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    return str(msg.get("X-Mouth-When") or "").strip().lower() == "now"


def read(path: Path, d: Optional[Path] = None, *, sync: bool = True) -> Optional[dict]:
    """One mail in new/ as a line the player can speak (``_``-keys: the mail's own
    facts). None if it has gone meanwhile.

    ``sync``: a mail whose asks need the verifier's verdict waits for it (an acting
    pass, or a caller reading one mail). A ``Queue`` that only reads passes False:
    the mail reads as unsigned until the verdict lands (``_verdict``)."""
    try:
        raw = path.read_bytes()
        mtime_ns = path.stat().st_mtime_ns
    except FileNotFoundError:
        return None
    msg = email.message_from_bytes(raw, policy=email.policy.default)

    def h(k: str) -> Optional[str]:
        v = msg.get(k)
        return str(v).strip() if v is not None else None

    mid = h("Message-ID") or f"<{path.name}@mouth>"
    sender = email.utils.parseaddr(h("From") or "")[1]
    body = _body(msg)
    is_raw = is_local(raw, path.parent.parent)   # the MAC, never the X-Mouth-Raw header
    if not is_raw and h("X-Mouth-Raw") == "1":
        _log_once(("forged_raw", mid), {"forged_raw": mid[:200], "from": sender[:200]})
    on_call = None if is_raw else call_box(h("X-Original-To"), h("Delivered-To"), h("To"))
    if is_raw:
        text = body[:-1] if body.endswith("\n") else body
    else:
        text = speakable(body.strip()) or speakable(h("Subject") or "")
    own_order = _order(h("X-Mouth-Order"), mtime_ns, mid)
    asked = h("X-Mouth-Requested")
    requested_t = _when(asked)
    if asked and requested_t is None:  # nan, inf, 1e20, garbage: as if absent
        _log({"bad_header": "X-Mouth-Requested", "mail_id": mid[:200], "value": asked[:40], "as": "absent"})
    requested_t = requested_t or own_order / 1e9

    pri = (h("X-Mouth-Priority") or "").lower() or \
        ("next" if (h("Importance") or "").lower() == "high" else "")
    priority: object = None
    rank = DEFAULT_RANK
    if pri in RANKS:
        priority, rank = pri, RANKS[pri]
    elif _float(pri) is not None:
        rank = _float(pri)
        priority = int(rank) if rank == int(rank) else rank
    try:
        extras = json.loads(h("X-Mouth-Extra") or "{}")
    except ValueError:
        extras = {}
    if not isinstance(extras, dict):
        extras = {}
    # The mail's own facts are ``_`` keys, set below from the mail itself: an extra
    # may never set one (``_gone`` would hide the line, and the line it supersedes).
    extras = {k: v for k, v in extras.items() if not str(k).startswith("_")}
    expires_s = _float(h("X-Mouth-Expires"))
    want = (h("X-Mouth-Interest") or "").lower()
    item = {
        "utt": h("X-Mouth-Utt") or "m" + hashlib.sha1(mid.encode()).hexdigest()[:11],
        "text": text,
        "voice": h("X-Mouth-Voice")
        or (_own_voice(h("X-Mouth-Agent") or sender.split("@")[0]) if on_call else None)
        or default_voice(d)[0],
        "speed": _float(h("X-Mouth-Speed")),
        "backend": h("X-Mouth-Backend") or envnames.get("MOUTH_BACKEND") or "auto",
        # the line's own ask; the rest of the order is resolve_device's, below
        "device": h("X-Mouth-Device") or (f"call:{on_call[1]}" if on_call else None),
        "pan": _pan(h("X-Mouth-Pan") or h("X-Mouth-Channel")),
        "requested_t": requested_t,
        "requested_ts": _ts(requested_t),
        "agent": h("X-Mouth-Agent") or (sender.split("@")[0] if sender else None),
        "session": h("X-Session-From"),
        "log_dir": h("X-Mouth-Log-Dir") or (None if is_raw else envnames.get("MOUTH_LOG_DIR")),
        "wait": h("X-Mouth-Wait") == "1",
        "priority": priority,
        "hold": "turn-end" if (h("X-Mouth-When") or "").lower() == "turn-end"
        or (on_call and (h("X-Mouth-When") or "").lower() != "now" and priority != "now") else None,
        "interest": want if want in INTERESTS else None,
        "expires_s": expires_s,
        "expires_t": requested_t + expires_s if expires_s is not None else None,
        "file": h("X-Mouth-File"),
        "start": _float(h("X-Mouth-Start")),
        "end": _float(h("X-Mouth-End")),
        "resume_of": h("X-Mouth-Resume-Of"),
        "volume": _volume(h("X-Mouth-Volume")),  # a bad one: the player reads the host
        "lead_s": _float(h("X-Mouth-Lead")),     # seconds buffered first; bad: the host's
        "mail_id": mid,
        "mail_from": sender or None,
    }
    if is_raw:
        item["mail_log"] = h("X-Mouth-Mail-Log")
    else:  # a mail from outside threads its log under itself (maillog)
        from . import maillog

        item["mail_log"] = maillog.box_from_env()
        item["log_root"] = mid
    own = {k: item[k] for k in _NUMERIC}
    places = {k: item[k] for k in _PLACES if k in item}
    item.update(extras)
    _numeric_extras(item, own, extras, mid)   # no extra makes hold or the player raise
    _places(item, extras, places, mid, sender, path.parent.parent)   # nor picks where it writes
    item["requested_ts"] = _ts(item["requested_t"])   # one time, whoever set it
    if not safe_utt(item["utt"]):  # X-Mouth-Utt or an extra: one name in done/, or derived
        derived = "m" + hashlib.sha1(mid.encode()).hexdigest()[:11]
        _log({"bad_utt": str(item["utt"])[:80], "mail_id": mid[:200], "as": derived})
        item["utt"] = derived
    if item["lead_s"] is not None and not item.get("lead_s_from"):
        item["lead_s_from"] = "mail"   # a header alone: the line's own, as a flag is
    _device(item, extras, is_raw, "call" if on_call and not h("X-Mouth-Device") else "mail", d)
    if not is_raw:  # a mail from outside: its drop is told To its From, not an extra's
        item["notice_to"] = sender or None
    vkey = (str(path.parent.parent), path.name, len(raw), mtime_ns)
    rank = _trust(item, msg, extras, on_call, is_raw, rank, path, vkey, sync, d)
    _room_hold(item, (h("X-Mouth-When") or "").lower(), extras, d)
    if item["file"]:
        item["backend"] = "file"
        if not is_raw:
            s, e = item["start"], item["end"]
            span = f" {s or 0:g}-{e:g}s" if e is not None else (f" from {s:g}s" if s else "")
            item["text"] = f"[sound {Path(item['file']).name}{span}]"
    sup = h("Supersedes")
    irt = h("In-Reply-To")
    if irt and irt != sup:  # a reply; a plain supersede's In-Reply-To is its Supersedes
        item.update(in_reply_to=irt, references=h("References") or irt)
        if h("Call-ID"):
            item["call_id"] = h("Call-ID")
    item.update(_path=path, _name=path.name, _sup=sup, _raw=is_raw,
                _retract=bool(sup) and not body.strip() and not item["file"],
                _allowed=allowed(sender), _heard=bool(on_call and h("Ears-Kind")), _rank=rank, _order=own_order, _own_order=own_order,
                _chain=[], _mid=mid[:200], _from=sender[:200],
                _notice_reply=_notice_reply(msg, mid, is_raw, irt, h("References")))
    if not is_raw and _delivery_report(msg, sender):   # a bounce: filed unspoken
        item.update(_notice_reply=True, _dsn=True)
    return item


#: The ids a drop notice carries (mouth.notice). The OLD form, minted by an
#: older mouth, ends ``@<host>.mouth-notice>``; a reply to an old notice still files
#: unspoken. The NEW form is ``<ns.uid.mouth-notice@<host>>``, the only shape
#: ``message send --message-id`` takes (a bare short host after the ``@``). The
#: shape is tested, not the host.
_NOTICE_ID_SUFFIX = ".mouth-notice>"
_NOTICE_ID_NEW = re.compile(r"<[^<>@\s]+\.mouth-notice@[^<>@\s]+>")
_MSG_IDS = re.compile(r"<[^<>\s]+>")


def _is_notice_id(i: Optional[str]) -> bool:
    """Is I a drop notice's id, in either form?"""
    i = (i or "").strip()
    return i.endswith(_NOTICE_ID_SUFFIX) or bool(_NOTICE_ID_NEW.fullmatch(i))


def _notice_reply(msg, mid: str, is_raw: bool, irt: Optional[str], refs: Optional[str]) -> bool:
    """A reply to a drop notice, or a notice itself: never a line.

    A mail from outside whose In-Reply-To or References names a notice id, in
    either form (``_is_notice_id``): an amend's re-drop answers
    ``<...@<host>.mouth>``, and must still play. And, whatever its route, a mail
    that is itself a notice (it carries ``Mouth-Notice``, or its own id is a
    notice id), however it reached the queue. A raw line is a CLI ``mouth say``,
    never a mail reply: its ``--in-reply-to`` is the caller's own choice."""
    if msg.get("Mouth-Notice") is not None or _is_notice_id(mid):
        return True
    if is_raw:
        return False
    return any(_is_notice_id(i) for i in _MSG_IDS.findall(" ".join(x for x in (irt, refs) if x)))


#: The parts a delivery report carries the bounced mail in: whole, or its headers only.
_ORIGINAL_PARTS = ("message/rfc822", "message/global", "text/rfc822-headers",
                   "message/global-headers")
_REPORT_PARTS = ("message/delivery-status", "message/global-delivery-status")
_DAEMONS = ("mailer-daemon", "postmaster")


def _names_a_notice(part) -> bool:
    """Does PART, a bounced mail or its headers, name a drop notice: its own
    ``Message-ID`` in either form, or ``Mouth-Notice``?"""
    try:
        if part.get_content_type() in ("message/rfc822", "message/global"):
            inner = part.get_payload(0)
        else:
            inner = email.parser.HeaderParser(policy=email.policy.default).parsestr(
                str(part.get_content()))
        return inner.get("Mouth-Notice") is not None or _is_notice_id(str(inner.get("Message-ID") or ""))
    except Exception:  # noqa: BLE001 - a malformed part names nothing
        return False


def _delivery_report(msg, sender: str) -> bool:
    """A bounce, on either test. Never on Auto-Submitted alone.

    (i) A delivery report from any sender: ``multipart/report;
    report-type=delivery-status``, or a mail carrying a ``message/delivery-status``
    part (the form a report takes when only the headers came back too).
    (ii) From ``MAILER-DAEMON`` or ``postmaster`` on this host, with a bounced mail
    or its headers inside naming a drop notice (``_names_a_notice``).

    ``Queue.lines`` tests ``_allowed`` first, so a foreign host's report keeps its
    refusal; a plain mail from this host's MAILER-DAEMON that names no notice
    follows the ordinary rules."""
    try:
        if (msg.get_content_type() == "multipart/report"
                and str(msg.get_param("report-type") or "").lower() == "delivery-status"):
            return True
        parts = list(msg.walk()) if msg.is_multipart() else []
        if any(p.get_content_type() in _REPORT_PARTS for p in parts):
            return True
        s = (sender or "").lower()
        if s.split("@", 1)[0] not in _DAEMONS or not _admits(s)[1]:
            return False
        return any(p.get_content_type() in _ORIGINAL_PARTS and _names_a_notice(p) for p in parts)
    except Exception:  # noqa: BLE001 - a mail too strange to read is no report
        return False


def _refused_row(e: dict) -> None:
    """A said row ``refused``, a record only, never a mail.

    Built from a fixed minimal item: every other key of a refused mail
    (its extras, X-Mouth-Log-Dir, X-Session-From, X-Mouth-Utt) is the refused
    sender's own claim. The utt is fresh, the time is the refusal's, the ids
    are the ones ``read()`` parsed after the extras (``_mid``, ``_from``), and
    the row goes to the player's own heard log (``$MOUTH_LOG_DIR``,
    as for any mail from outside) and mail log."""
    import uuid  # here: box.py has none at the top, and only the player gets here

    from . import maillog
    from .player import said_unplayed

    item = {"utt": uuid.uuid4().hex[:12], "mail_id": e["_mid"],
            "requested_ts": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "mail_log": maillog.box_from_env(),
            # the player's own default for a mail from outside (read()), never the mail's
            "log_dir": envnames.get("MOUTH_LOG_DIR") or None}
    who = e["_from"] or "(none)"
    if not operator_config.current().privileged:
        detail = f"From {who} is not on this host or in $MOUTH_MAIL_ALLOW"
    elif not _admits(who)[2]:
        detail = f"From {who} is not on this host or in the operator's privileged list"
    else:
        detail = f"From {who} is not both (on this host or privileged) and in $MOUTH_MAIL_ALLOW"
    try:
        said_unplayed(item, "refused", detail=detail)
    except Exception as x:  # noqa: BLE001 - a record that cannot be written must not stop the player
        print(json.dumps({"refused_row_failed": e["_mid"], "error": str(x)[:200]}), flush=True)


def _same_from(e: dict, t: dict) -> bool:
    """E and T have one From, the parsed address, lower-cased;
    two empties are never a match. A courtesy bound, not a proof: a From is forgeable."""
    a, b = (e.get("_from") or "").lower(), (t.get("_from") or "").lower()
    return bool(a) and a == b


def public(item: dict) -> dict:
    """The line without the mail's ``_`` facts: what playing.json and done/ hold."""
    return {k: v for k, v in item.items() if not k.startswith("_")}


# -- the queue: new/, parsed once per file, in play order ---------------------

class Queue:
    """The lines in a mouth's maildir, supersedes resolved, in play order.

    ``lines(act=True)`` also files what needs no player: a refused sender,
    a retract (and what it retracts), and it cuts a playing line a retract
    names. Only the player acts; readers (status, prefetch) pass False.
    """

    def __init__(self, d: Optional[Path] = None) -> None:
        self.d = Path(d or mouth_dir())
        self.bx = box_dir(self.d)
        self._cache: dict = {}
        self._acked: set = set()
        self._via_allow: set = set()  # fallback warned; not _acked: X-Mouth-Raw is the sender's own
        self._clamped: set = set()  # a replacement's rank, clamped: logged once
        self._recovered = False  # player.recover runs once, on the first lines(act=True)

    @property
    def new(self) -> Path:
        return self.bx / "new"

    def entries(self, sync: bool = False) -> list:
        """Every mail in new/, parsed once. ``sync``: an acting pass, which must judge
        each mail on its verifier's verdict, so a mail read while its verdict was
        pending is read again, now; a reader re-reads it once it lands."""
        try:
            names = sorted(n for n in os.listdir(self.new) if not n.startswith("."))
        except FileNotFoundError:
            return []
        live = set(names)
        for n in [n for n in self._cache if n not in live]:
            del self._cache[n]
        out = []
        for n in names:
            e = self._cache.get(n)
            if e is not None and e.get("_verify_pending") and (sync or e["_vkey"] in _VERDICTS):
                e = None
            if e is None:
                try:
                    e = read(self.new / n, self.d, sync=sync)
                except Exception as x:  # noqa: BLE001 - one bad mail never stops serve
                    if file_to(self.new / n, "T"):   # aside, never left in new/ for the next start
                        _log({"unreadable": n, "error": f"{type(x).__name__}: {x}"[:200]})
                    continue
                if e is None:
                    continue
                self._cache[n] = e
            out.append(e)
        return out

    def lines(self, act: bool = False, playing=None) -> list:
        """``playing``: what is playing now, one item or a list (the line in
        ``playing.json`` and each bed's file): a retract naming one of
        them cuts it."""
        # Only act=True needs the player (_done, said_unplayed); a reader
        # (status, timeline) must not pay its import (+115 ms).
        plays = [p for p in (playing if isinstance(playing, list) else [playing]) if p]
        eff: dict = {}
        if act and not self._recovered:  # a dead player's lines, once per Queue
            self._recovered = True
            from .player import recover
            recover(self.d, self)
        for e in sorted(self.entries(sync=act), key=lambda e: (e["_own_order"], e["_name"])):
            if e.get("_heard"):  # the ears' own turn in a call's box: the user's words, not a line
                if act and file_to(e["_path"], "T"):
                    print(json.dumps({"skipped": "heard", "mail_id": e["mail_id"]}), flush=True)
                continue
            if not e["_allowed"]:
                if act and file_to(e["_path"], "T"):
                    print(json.dumps({"refused": e["_from"], "mail_id": e["_mid"]}), flush=True)
                    _refused_row(e)
                continue
            if e.get("_notice_reply"):  # a reply to a drop notice: never spoken, never told
                if act and file_to(e["_path"], "T"):   # a bounce is one too
                    why = "delivery-report" if e.get("_dsn") else "notice-reply"
                    print(json.dumps({"skipped": why, "mail_id": e["_mid"], "from": e["_from"]}),
                          flush=True)
                continue
            if e.get("_untrusted"):  # it asked for a call, or a file, it may not have
                if act:
                    self._refuse(e, e["_untrusted"], field=e["_untrusted"].split(" ", 1)[0])
                continue
            if act and e["mail_id"] not in self._via_allow and _via_allow_fallback(e["_from"]):
                self._via_allow.add(e["mail_id"])
                _log({"warning": "allow-fallback", "from": e["_from"], "mail_id": e["_mid"],
                      "reason": operator_config.fallback_reason()})
            if act and not e["_raw"] and e["mail_id"] not in self._acked:
                self._acked.add(e["mail_id"])  # a mail from outside: say it was queued, in its thread
                from . import maillog

                maillog.entry(e, "queued", {k: e.get(k) for k in (
                    "utt", "text", "voice", "device", "device_from", "pan", "priority", "hold", "expires_t",
                    "agent", "session", "requested_ts", "file", "mail_id")})
            x = dict(e)
            t = eff.get(e["_sup"]) if e["_sup"] else None
            if t is not None and not t.get("_gone"):
                if not e["_sup_trust"] and not _same_from(e, t):   # another's line
                    x["_gone"] = "untrusted"
                    if act:
                        self._refuse(e, f"Supersedes {e['_sup'][:200]} names a line from "
                                        f"{t['_from'] or '(none)'}: needs that sender, or a signed "
                                        f"or local line ({e['_sup_why']})",
                                     field="Supersedes", why="another sender's line")
                elif e["_retract"]:
                    t["_gone"], x["_gone"] = "retracted", "retract"
                    if act:
                        from .player import _done, said_unplayed

                        for p in [t, *t["_chain"]]:
                            file_to(p["_path"], "T")
                        file_to(e["_path"], "T")
                        # quiet if its own sender retracted it: the retract mail's parsed From
                        gone = {**public(t), "retracted_by": e["_from"]}
                        _done(self.d, gone, said_unplayed(gone, "retracted"))
                else:
                    t["_gone"] = "replaced"
                    rank = t["_rank"] if e["priority"] is None else e["_rank"]
                    if e["priority"] is None and not e["_sup_trust"] and rank < RANKS["next"]:
                        # an untrusted replacement keeps its target's place, never
                        # ahead of next (any mail may ask for next; none may inherit now)
                        if act and e["mail_id"] not in self._clamped:
                            self._clamped.add(e["mail_id"])
                            _log({"untrusted": "Supersedes", "value": e["_sup"][:200],
                                  "mail_id": e["_mid"], "rank": rank, "as": RANKS["next"]})
                        rank = RANKS["next"]
                    x.update(utt=t["utt"], _rank=rank,
                             _order=t["_order"], requested_t=t["requested_t"],
                             requested_ts=t["requested_ts"],
                             amended_from=t.get("amended_from") or t["text"],
                             _chain=[*t["_chain"], t])
            elif e["_sup"] and e["_retract"]:
                x["_gone"] = "retract"
                if act:
                    hit = next((p for p in plays if p.get("mail_id") == e["_sup"]), None)
                    if not e["_sup_trust"]:   # it could only cut, and it may not
                        what = "would cut the line playing" if hit is not None else \
                            "names no queued line, so it could only cut one"
                        self._refuse(e, f"Supersedes {e['_sup'][:200]} {what}: needs a signed or "
                                        f"local line ({e['_sup_why']})",
                                     field="Supersedes", why="cuts the line playing")
                    elif hit is not None:
                        from . import _stop_file

                        _stop_file(self.d, {"t": time.time(), "reason": "retracted", "flush": False,
                                            "utt": hit["utt"]})
                    file_to(e["_path"], "T")
            eff[e["mail_id"]] = x
        out = [x for x in eff.values() if not x.get("_gone")]
        return sorted(out, key=lambda x: (x["_rank"], x["_order"], x["_name"]))

    def _refuse(self, e: dict, detail: str, field: str, why: Optional[str] = None) -> None:
        """An acting pass's refusal of a line that asked for what it may not have:
        filed T, one log line, its said row ``untrusted``, and
        so its sender's notice. Its target, if it named one, is untouched."""
        if not file_to(e["_path"], "T"):
            return
        rec = {"untrusted": field, "mail_id": e["_mid"], "from": e["_from"], "detail": detail[:400]}
        if field == "Supersedes":
            rec.update(value=(e["_sup"] or "")[:200], why=why)
        _log(rec)
        from .player import _done, said_unplayed

        item = public(e)
        try:
            _done(self.d, item, said_unplayed(item, "untrusted", detail=detail[:400]))
        except Exception as x:  # noqa: BLE001 - a record that cannot be written must not stop the player
            _log({"untrusted_row_failed": e["_mid"], "error": str(x)[:200]})

    def find(self, utt: str) -> Optional[dict]:
        return next((x for x in self.lines() if x["utt"] == utt), None)

    def take(self, x: dict) -> Optional[Path]:
        """Claim X to play: new/ -> cur/NAME:2, (no flag yet); what it replaced -> T."""
        dest = self.bx / "cur" / f"{_base(x['_name'])}:2,"
        try:
            x["_path"].rename(dest)
        except FileNotFoundError:
            return None
        for p in x["_chain"]:
            file_to(p["_path"], "T")
        return dest

    def drop_line(self, x: dict, flags: str) -> bool:
        """File a line (and what it replaced) without playing it. False if it had gone."""
        ok = file_to(x["_path"], flags) is not None
        for p in x["_chain"]:
            file_to(p["_path"], "T")
        return ok

    def ack(self, x: dict) -> None:
        """Say a mail from outside was queued, once, in its thread: what
        ``lines(act=True)`` does for each line. The player's poll takes a file
        from a read-only ``lines()`` (``player._leapfrog``), so it
        acks it here first, or the file's maillog thread has no ``queued`` root."""
        if x.get("_raw") or not x.get("mail_id") or x["mail_id"] in self._acked:
            return
        self._acked.add(x["mail_id"])
        from . import maillog

        maillog.entry(x, "queued", {k: x.get(k) for k in (
            "utt", "text", "voice", "device", "device_from", "pan", "priority", "hold", "expires_t",
            "agent", "session", "requested_ts", "file", "mail_id")})
