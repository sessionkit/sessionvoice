#!/usr/bin/env python3
"""heard - the heard log, which sessionvoice owns (heard-log v1; it began as
VoiceMode's): one JSON line per partial, turn, event, saying or said.

The mouth (``src/mouth``) writes its speech here too, mirroring the
ears: ``saying`` at the first audio frame (like a partial), ``said`` when the
playback ends (like a turn), so a partial of the user's between the two IS a
barge-in, visible on one timeline.

The log is ``<home>/logs/conversations/heard_YYYY-MM-DD.jsonl``, where ``<home>``
is sessionvoice's home (``ears.home``: ``$SESSIONVOICE_HOME``, else ``~/.sessionvoice``
or, on a host that already keeps it there, ``~/.voicemode``). It rolls at the
same local midnight as VoiceMode's ``exchanges_YYYY-MM-DD.jsonl``. Append-only: a line is never edited or
removed; a correction is a new line.

Every writer (``listen`` here; ``converse`` and a Delta bot's bridge as guests)
writes v1 lines through the same contract, so one reader serves every source:

1. take an exclusive ``flock`` on ``heard.lock`` beside the logs (one lock
   for every day's file, so two writers either side of midnight cannot mint
   the same ``seq``);
2. ``seq`` = the last ``seq`` in the newest ``heard_*.jsonl`` + 1. It keeps
   counting across midnight, so a ``seq`` names one line on its own and a
   cursor is one integer;
3. append the record as ONE ``write()`` of ``json + "\\n"`` to a file opened
   ``O_APPEND``, so a reader never sees half a line in practice;
4. release the lock.

This module imports nothing from ``voice_mode`` and nothing outside the
standard library, on purpose: the ride-along hook runs this same file as a
script on every tool call, under whatever ``python3`` is on PATH, and must
start fast and never fail on an import.
"""

from __future__ import annotations

import errno
import fcntl
import glob
import json
import os
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Optional

SCHEMA = 1

# kind
PARTIAL = "partial"
TURN = "turn"
EVENT = "event"
SAYING = "saying"   # the mouth: playback started (written at the first audio frame)
SAID = "said"       # the mouth: playback ended (what played, and where it was cut)
KINDS = (PARTIAL, TURN, EVENT, SAYING, SAID)

# event names (kebab-case; the design's "listen started" etc.)
EV_LISTEN_STARTED = "listen-started"
EV_LISTEN_STOPPED = "listen-stopped"
EV_HEARTBEAT = "heartbeat"
EV_DEVICE_CHANGED = "device-changed"
EV_WAKE_WORD = "wake-word"
EV_END_WORD = "end-word"
EV_BARGE_IN = "barge-in"
EV_CALL_STARTED = "call-started"
EV_CALL_ENDED = "call-ended"
EV_VAD = "vad"   # a voice onset or offset edge from capture: the mouth ducks on onset
EV_MISSED_REPLY = "missed-reply"  # capture kept a window's audio: a line ended, no turn came

LOCK_NAME = "heard.lock"
RESERVED = frozenset({"ts", "seq", "kind", "v"})
_TAIL_BYTES = 64 * 1024


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

def sessionvoice_home():
    """sessionvoice's state root. A copy of ``ears.home.sessionvoice_home``, the rule:
    $SESSIONVOICE_HOME, $VOICEMODE_BASE_DIR, ~/.sessionvoice or ~/.voicemode if it holds
    the heard log, else ~/.sessionvoice. tests/test_home.py holds this copy to it."""
    for name in ("SESSIONVOICE_HOME", "VOICEMODE_BASE_DIR"):
        if os.environ.get(name):
            return os.path.expanduser(os.path.expandvars(os.environ[name]))
    for d in ("~/.sessionvoice", "~/.voicemode"):
        c = os.path.expanduser(d + "/logs/conversations")
        if os.path.exists(c + "/heard.lock") or glob.glob(glob.escape(c) + "/heard_*.jsonl"):
            return os.path.expanduser(d)
    return os.path.expanduser("~/.sessionvoice")


def base_dir() -> Path:
    """sessionvoice's home (:func:`sessionvoice_home`): the heard log is under it."""
    return Path(sessionvoice_home())


def log_dir() -> Path:
    return base_dir() / "logs" / "conversations"


def log_path(day: Optional[date] = None, directory: Optional[Path] = None) -> Path:
    day = day or datetime.now().date()
    return (directory or log_dir()) / f"heard_{day.strftime('%Y-%m-%d')}.jsonl"


def log_files(directory: Optional[Path] = None) -> list[Path]:
    """Every heard log in the directory, oldest first (the name sorts by date)."""
    d = directory or log_dir()
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("heard_????-??-??.jsonl") if p.is_file())


# --------------------------------------------------------------------------
# Reading the tail of a file
# --------------------------------------------------------------------------

def _last_seq_in(path: Path) -> Optional[int]:
    """The ``seq`` of the last whole, parseable line in ``path``, or None.

    Reads at most the last 64 KiB. A final line with no trailing newline is
    a write in flight (or a torn one) and is skipped, not trusted.
    """
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if size == 0:
                return None
            f.seek(max(0, size - _TAIL_BYTES))
            chunk = f.read()
    except OSError:
        return None
    lines = chunk.split(b"\n")
    # lines[-1] is b"" when the file ends in a newline; otherwise it is a
    # partial line. Either way it is not a whole record.
    for raw in reversed(lines[:-1]):
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            continue
        seq = rec.get("seq") if isinstance(rec, dict) else None
        if isinstance(seq, int):
            return seq
    return None


def last_seq(directory: Optional[Path] = None) -> int:
    """The highest ``seq`` written so far, across days; 0 when none."""
    for path in reversed(log_files(directory)):
        seq = _last_seq_in(path)
        if seq is not None:
            return seq
    return 0


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def append(kind: str,
           *,
           text: Optional[str] = None,
           source: str = "mic",
           device: Optional[str] = None,
           session: Optional[str] = None,
           agent: Optional[str] = None,
           final: Optional[bool] = None,
           event: Optional[str] = None,
           directory: Optional[Path] = None,
           **extra: Any) -> dict:
    """Append one record and return it, ``seq`` and ``ts`` filled in.

    ``kind`` is ``partial``, ``turn``, ``event``, ``saying`` or ``said``.
    ``final`` defaults to False for a partial or a saying, and True for a
    turn or a said. Extra keyword fields (``via``,
    ``detector``, ``age_at_return``, ``word``, ``listen_id`` ...) are written
    as given; None values are dropped. Raises ValueError on a bad kind, and
    OSError if the log cannot be written: callers that must not fail
    (``converse``) wrap the call; a listener that cannot write should stop
    and say so.
    """
    if kind not in KINDS:
        raise ValueError(f"heard: kind must be one of {KINDS}, not {kind!r}")
    if kind in (PARTIAL, TURN, SAYING) and not isinstance(text, str):
        raise ValueError(f"heard: a {kind} needs text")
    if kind in (SAYING, SAID) and not extra.get("utt"):
        raise ValueError(f"heard: a {kind} needs its utterance id (utt=...)")
    if kind == EVENT and not event:
        raise ValueError("heard: an event needs its name (event=...)")
    clash = RESERVED & extra.keys()
    if clash:
        raise ValueError(f"heard: {sorted(clash)} are the writer's to set, not the caller's")
    if final is None and kind != EVENT:
        final = kind in (TURN, SAID)

    d = directory or log_dir()
    d.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(str(d / LOCK_NAME), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        record = _clean({
            "ts": _now_iso(),
            "seq": last_seq(d) + 1,
            "kind": kind,
            "event": event,
            "text": text,
            "source": source,
            "device": device,
            "session": session,
            "agent": agent,
            "final": final,
            **extra,
            "v": SCHEMA,
        })
        line = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
        path = log_path(directory=d)
        fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            written = os.write(fd, line)
            if written != len(line):  # never seen on a local disk; say so if it is
                raise OSError(errno.EIO, f"heard: short write {written}/{len(line)} to {path}")
        finally:
            os.close(fd)
        return record
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def partial(text: str, **kw: Any) -> dict:
    """A recognised chunk that a later turn will subsume (``final: false``)."""
    return append(PARTIAL, text=text, **kw)


def turn(text: str, *, detector: Optional[str] = None, **kw: Any) -> dict:
    """A finished turn: the joined text, and the detector that ended it."""
    return append(TURN, text=text, detector=detector, **kw)


def event(name: str, **kw: Any) -> dict:
    """An event line: listen-started, heartbeat, listen-stopped, wake-word ..."""
    return append(EVENT, event=name, **kw)


def saying(text: str, *, utt: str, **kw: Any) -> dict:
    """The mouth started playing ``text`` (``final: false``): the fast line."""
    return append(SAYING, text=text, utt=utt, source=kw.pop("source", "mouth"), **kw)


def said(*, utt: str, **kw: Any) -> dict:
    """The mouth stopped: what played, and where it was cut (``final: true``)."""
    return append(SAID, utt=utt, source=kw.pop("source", "mouth"), **kw)


# --------------------------------------------------------------------------
# Identity helpers: the session and agent a writer stamps on its lines
# --------------------------------------------------------------------------

def caller_session() -> Optional[str]:
    """The harness session id, by converse's own precedence."""
    return (os.environ.get("VOICEMODE_SESSION_ID")
            or os.environ.get("CLAUDE_CODE_SESSION_ID")
            or os.environ.get("CLAUDE_SESSION_ID"))


def caller_agent() -> Optional[str]:
    return os.environ.get("VOICEMODE_AGENT") or os.environ.get("CLAUDE_CODE_AGENT")


def input_device_name() -> Optional[str]:
    """The OS name of the default input device, or None. Never raises.

    A label for WHERE the words came from, never WHO said them.
    Imports sounddevice lazily so the hook path never loads it.
    """
    try:
        import sounddevice as sd  # type: ignore
        info = sd.query_devices(kind="input")
        name = info.get("name") if isinstance(info, dict) else None
        return str(name) if name else None
    except Exception:
        return None


# --------------------------------------------------------------------------
# Reading: whole records past a seq, and where each sits in its file
# --------------------------------------------------------------------------

class _Rec:
    __slots__ = ("rec", "path", "start", "end")

    def __init__(self, rec: dict, path: Path, start: int, end: int):
        self.rec, self.path, self.start, self.end = rec, path, start, end

    @property
    def seq(self) -> int:
        return self.rec["seq"]


def _scan(path: Path, offset: int = 0) -> tuple[list[_Rec], int]:
    """Whole records in ``path`` from byte ``offset``.

    Returns the records and the byte offset just past the last whole line.
    A trailing line with no newline is a write in flight: not returned, and
    the offset stops before it, so the next read picks it up whole.
    """
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
    except OSError:
        return [], offset
    out: list[_Rec] = []
    pos = offset
    for raw in data.split(b"\n")[:-1]:
        start = pos
        pos += len(raw) + 1
        s = raw.strip()
        if not s:
            continue
        try:
            rec = json.loads(s)
        except (ValueError, UnicodeDecodeError):
            continue  # a torn or foreign line mid-file: skipped
        if isinstance(rec, dict) and isinstance(rec.get("seq"), int):
            out.append(_Rec(rec, path, start, pos))
    return out, pos


def read_after(seq: int, *, hint_file: Optional[str] = None, hint_offset: int = 0,
               directory: Optional[Path] = None, max_files: int = 2
               ) -> tuple[list[_Rec], Optional[tuple[Path, int]]]:
    """Records with ``seq`` greater than ``seq``, oldest first.

    ``hint_file``/``hint_offset`` (from a cursor) let the read start where
    the last one stopped instead of parsing the whole day. At most the
    newest ``max_files`` days are read: a ride-along is not an archive.
    Returns the records and (file, offset) just past the last whole line.
    """
    files = log_files(directory)
    if not files:
        return [], None
    names = [p.name for p in files]
    if hint_file in names:
        chosen = files[names.index(hint_file):]
    else:
        chosen, hint_file = files, None
    chosen = chosen[-max_files:]
    recs: list[_Rec] = []
    tail: Optional[tuple[Path, int]] = None
    for p in chosen:
        off = hint_offset if p.name == hint_file else 0
        try:
            if off > p.stat().st_size:
                off = 0  # the file was replaced under us; trust seq, not bytes
        except OSError:
            continue
        rs, end = _scan(p, off)
        recs.extend(r for r in rs if r.seq > seq)
        tail = (p, end)
    return recs, tail


# --------------------------------------------------------------------------
# The cursor: per session, the highest seq that session has been shown
# --------------------------------------------------------------------------

def cursor_dir() -> Path:
    return base_dir() / "state" / "heard-cursor"


def _session_file_name(session: str) -> str:
    safe = "".join(c if (c.isalnum() or c in "._-") else "_" for c in session)[:128]
    return safe.lstrip(".") or "default"


# Who moved a cursor: every write records its mutator beside the
# offset, as ``by``: {event, caller, pid, at, heard, session}. Additive: an
# old reader reads only seq/file/offset and ignores it, and an old writer
# rewrites the whole file without it (so a scoop after it says ``by: null``,
# never the writer before). A caller names only ``event`` and ``caller``;
# ``save_cursor`` stamps the rest at the moment it writes.
HEARD_EVENT_ENV = "EARS_HEARD_EVENT"
HEARD_CALLER_ENV = "EARS_HEARD_CALLER"
# Their old names, still read: NEW if it is set at all, else OLD. The
# table is src/ears/envnames.py; this file imports nothing outside the standard
# library, so it keeps its own copy, which tests/test_envnames.py holds to it.
HEARD_ENV_OLD = {"EARS_HEARD_EVENT": "VOICEMODE_HEARD_EVENT", "EARS_HEARD_CALLER": "VOICEMODE_HEARD_CALLER"}
EVENT_TOOL = "tool"     # an agent's own tool call (heard_wait)
EVENT_OTHER = "other"   # nobody said


def _default_caller() -> Optional[str]:
    """The running script: ``abspath(argv[0])`` when it names a file, else
    ``argv[0]`` as given (``python3 -c`` is ``"-c"``, not ``<cwd>/-c``)."""
    import sys
    argv0 = sys.argv[0] if getattr(sys, "argv", None) else None
    if not argv0:
        return None
    return os.path.abspath(argv0) if os.path.isfile(argv0) else argv0


def _env(new: str) -> Optional[str]:
    """``$new`` if it is set (``""`` counts), else its old name's value, else None."""
    if new in os.environ:
        return os.environ[new]
    return os.environ.get(HEARD_ENV_OLD.get(new, new))


def mutator(event: Optional[str] = None, caller: Optional[str] = None) -> dict:
    """Who is writing a cursor, now: the ``by`` a cursor write records.

    ``event`` is the hook event (``PostToolBatch``, ``UserPromptSubmit``
    ...), ``"tool"`` for an agent's own tool call, else
    ``$EARS_HEARD_EVENT``, else ``"other"``. ``caller`` is a name the
    caller gives, else ``$EARS_HEARD_CALLER``, else the running script.
    ``pid``, ``at``, ``heard`` (which copy of this file) and ``session``
    (the writer's own) are always measured here.
    """
    return {
        "event": event or _env(HEARD_EVENT_ENV) or EVENT_OTHER,
        "caller": caller or _env(HEARD_CALLER_ENV) or _default_caller(),
        "pid": os.getpid(),
        "at": _now_iso(),
        "heard": os.path.abspath(__file__),
        "session": caller_session(),
    }


class Cursor:
    __slots__ = ("seq", "file", "offset", "by")

    def __init__(self, seq: int, file: Optional[str] = None, offset: int = 0,
                 by: Optional[dict] = None):
        self.seq, self.file, self.offset, self.by = seq, file, offset, by

    def to_json(self) -> str:
        return json.dumps(_clean({"seq": self.seq, "file": self.file,
                                  "offset": self.offset if self.file else None,
                                  "by": self.by if isinstance(self.by, dict) else None}))


def load_cursor(session: str, directory: Optional[Path] = None) -> Optional[Cursor]:
    path = (directory or cursor_dir()) / _session_file_name(session)
    try:
        raw = json.loads(path.read_text())
        by = raw.get("by")
        return Cursor(int(raw["seq"]), raw.get("file"), int(raw.get("offset") or 0),
                      by if isinstance(by, dict) else None)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None  # unreadable: treated as absent, re-initialised at the end


def save_cursor(session: str, cursor: Cursor, directory: Optional[Path] = None,
                by: Optional[dict] = None) -> None:
    """Write ``cursor`` for ``session``, with who wrote it.

    ``event`` and ``caller`` come from ``by``, else from ``mutator()``'s
    defaults; never from ``cursor.by`` (a loaded cursor's, re-saved, would
    name the previous writer). ``pid``, ``at``, ``heard`` and ``session``
    are stamped here, at the write, over anything the caller built: a
    waiter armed minutes ago, or a hook that waited for the lock, must not
    date the write to its arming.
    """
    named = by if isinstance(by, dict) else {}
    cursor.by = mutator(event=named.get("event"), caller=named.get("caller"))
    d = directory or cursor_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / _session_file_name(session)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    tmp.write_text(cursor.to_json() + "\n")
    os.replace(tmp, path)


class _SessionLock:
    """flock on ``<session>.lock``; polls up to ``timeout`` s, never blocks longer."""

    def __init__(self, session: str, timeout: float, directory: Optional[Path] = None):
        d = directory or cursor_dir()
        d.mkdir(parents=True, exist_ok=True)
        self.path = d / (_session_file_name(session) + ".lock")
        self.timeout = timeout
        self.fd: Optional[int] = None

    def __enter__(self) -> bool:
        self.fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except OSError as e:
                if e.errno not in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                    raise
                if time.monotonic() >= deadline:
                    return False
                time.sleep(0.02)

    def __exit__(self, *exc) -> None:
        if self.fd is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            finally:
                os.close(self.fd)
                self.fd = None


def _hint_at(seq: int, cur: Optional[Cursor], log_directory: Optional[Path]
             ) -> tuple[Optional[str], int]:
    """The byte hint for a cursor at ``seq``: the end of the newest record
    with a seq <= ``seq``, read on from ``cur``'s hint. ``cur``'s own hint
    (or none) when no record past it qualifies."""
    after = cur.seq if cur is not None else -1
    recs, _ = read_after(after, hint_file=cur.file if cur else None,
                         hint_offset=cur.offset if cur else 0, directory=log_directory)
    at = None
    for r in recs:
        if r.seq <= seq and (at is None or r.seq > at.seq):
            at = r
    if at is not None:
        return at.path.name, at.end
    return (cur.file, cur.offset) if cur is not None else (None, 0)


def advance_cursor(session: str, seq: int, directory: Optional[Path] = None,
                   timeout: float = 2.0, *, log_directory: Optional[Path] = None,
                   by: Optional[dict] = None) -> bool:
    """Move ``session``'s cursor forward to ``seq`` (never back).

    ``listen`` calls this when it returns a turn, so the next hook does not
    print the turn the agent was just woken with. Returns False if the lock
    was not had in ``timeout`` seconds.

    The byte hint moves with the seq, to the end of the newest record with
    a seq <= ``seq``, in the same write. A seq move that kept the
    old hint left the bytes for the next ``take()`` to jump, so the scoop
    the keeper hears fired at that later write and named its writer.
    """
    with _SessionLock(session, timeout, directory) as got:
        if not got:
            return False
        cur = load_cursor(session, directory)
        if cur is None or seq > cur.seq:
            name, offset = _hint_at(seq, cur, log_directory)
            save_cursor(session, Cursor(seq, name, offset), directory, by=by)
        return True


# --------------------------------------------------------------------------
# Rendering: what a busy agent is shown on its next tool call
# --------------------------------------------------------------------------

QUIET_EVENTS = frozenset({EV_HEARTBEAT, EV_LISTEN_STARTED, EV_LISTEN_STOPPED, EV_VAD})
# The hook shows ONLY these events; every other event (the quiet three, and
# a capture's diagnostics such as turn-discarded or stt-error, and anything
# added later) moves the cursor silently. Context-conservative by default:
# a new event is invisible to agents until someone decides it should not be.
# `exchanges tail --heard` still shows every line.
SHOWN_EVENTS = frozenset({EV_WAKE_WORD, EV_END_WORD, EV_BARGE_IN, EV_DEVICE_CHANGED,
                          EV_CALL_STARTED, EV_CALL_ENDED})
DEFAULT_BUDGET_TOKENS = 400
_CUT_RESERVE_TOKENS = 20


def estimate_tokens(text: str) -> int:
    """Four characters a token, rounded up: an estimate, stated as one."""
    return (len(text) + 3) // 4


def _where(rec: dict) -> str:
    src = rec.get("source") or "?"
    dev = rec.get("device")
    return f"{src}/{dev}" if dev else str(src)


def _hms(rec: dict) -> str:
    ts = rec.get("ts") or ""
    return ts[11:19] if len(ts) >= 19 else "??:??:??"


def _via(rec: dict) -> str:
    via = rec.get("via")
    if not via:
        return ""
    agent = rec.get("agent")
    return f" {via}:{agent}" if agent else f" {via}"


# --------------------------------------------------------------------------
# Who spoke: names from the speaker side-car, when one is running
# --------------------------------------------------------------------------
# The name of who is
# speaking on each heard line. The speaker-sidecar2 writes
# speakers_YYYY-MM-DD.jsonl beside the heard log, one line per heard seq:
# {"seq": N, "speaker": "alice" | "bob" | "other" | "mixed" | "silent",
#  "fracs": {"alice": 0.6, ...}}. That file is read here only; a missing or
# unreadable file, or a seq with no label yet, leaves every line as it was.

_SPEAKERS_TAIL_BYTES = 512 * 1024


def speakers_path(heard_file: Path) -> Path:
    """speakers_YYYY-MM-DD.jsonl beside heard_YYYY-MM-DD.jsonl."""
    return heard_file.with_name(heard_file.name.replace("heard_", "speakers_", 1))


def load_speakers(heard_files: Iterable[Path], min_seq: int = 0) -> dict[int, str]:
    """seq -> a name for each labelled seq >= ``min_seq``; "mixed" becomes the
    leading voice with a question mark (``alice?``); silent lines are left out.
    Reads only the last ``_SPEAKERS_TAIL_BYTES`` of each file, so it stays
    cheap inside the hook's timeout. Never raises."""
    names: dict[int, str] = {}
    for hf in {Path(h) for h in heard_files}:
        sp = speakers_path(hf)
        try:
            with open(sp, "rb") as f:
                size = f.seek(0, 2)
                f.seek(max(0, size - _SPEAKERS_TAIL_BYTES))
                raw = f.read()
        except OSError:
            continue
        for line in raw.split(b"\n"):
            try:
                r = json.loads(line)
                seq, who = int(r["seq"]), r.get("speaker")
            except (ValueError, KeyError, TypeError):
                continue  # the first line of a tail read is usually cut
            if seq < min_seq or not who or who == "silent":
                continue
            if who == "mixed":
                fr = r.get("fracs") or {}
                if not fr:
                    continue
                top = max(fr, key=fr.get)
                # an unreferenced voice was once written s3; it is "other"
                who = ("other" if top[:1] == "s" and top[1:].isdigit() else top) + "?"
            names[seq] = str(who)
    return names


def _who(seqs: Iterable[int], names: dict[int, str]) -> str:
    """The voices of these seqs in order of first appearance: "alice",
    "alice+bob"; a voice that was only ever the leader of a mixed line keeps
    its "?". Empty when none is labelled."""
    order: list[str] = []
    sure: set[str] = set()
    for s in seqs:
        n = names.get(s)
        if not n:
            continue
        base = n.rstrip("?")
        if base not in order:
            order.append(base)
        if not n.endswith("?"):
            sure.add(base)
    return "+".join(n if n in sure else n + "?" for n in order[:3])


class _Item:
    __slots__ = ("text", "first", "last")

    def __init__(self, text: Optional[str], first: int, last: int):
        self.text, self.first, self.last = text, first, last  # text None = silent


def collapse(recs: list[_Rec], session: Optional[str] = None,
             names: Optional[dict[int, str]] = None) -> list[_Item]:
    """Records -> what to show, ordered by the seq that completed each.

    A run of partials from one source and device is subsumed by the turn
    that follows; partials with no turn yet are shown joined, as a preview.
    Quiet events and the reading session's own converse turns (it already
    has them as converse's result) are silent: they move the cursor and
    cost nothing. ``names`` (seq -> speaker, from ``load_speakers``) adds who
    spoke after the time, when known: ``[heard mic 08:00:00 alice partial]``.
    """
    names = dict(names or {})
    for r in recs:  # a speaker written on the record itself (by ears) wins
        sp = r.rec.get("speaker")
        if sp and sp != "silent":
            names[r.seq] = str(sp)
    pending: dict[tuple, list[dict]] = {}
    items: list[_Item] = []
    for r in recs:
        rec = r.rec
        kind = rec.get("kind")
        # via is in the key: converse writes turns with no partials, so its
        # turn must never subsume a listener's partials on the same mic.
        key = (rec.get("source"), rec.get("device"), rec.get("via"))
        if kind == PARTIAL:
            pending.setdefault(key, []).append(rec)
        elif kind == TURN:
            covers = pending.pop(key, [])
            first = covers[0]["seq"] if covers else rec["seq"]
            if rec.get("via") == "converse" and session and rec.get("session") == session:
                items.append(_Item(None, first, rec["seq"]))
            else:
                who = _who([p["seq"] for p in covers] + [rec["seq"]], names)
                who = f" {who}" if who else ""
                items.append(_Item(
                    f"[heard {_where(rec)} {_hms(rec)}{_via(rec)}{who}] {rec.get('text', '')}",
                    first, rec["seq"]))
        elif kind == SAYING:
            items.append(_Item(None, rec["seq"], rec["seq"]))  # the said line carries it
        elif kind == SAID:
            if session and rec.get("session") == session:
                items.append(_Item(None, rec["seq"], rec["seq"]))  # its own speech
            else:
                items.append(_Item(_said_line(rec), rec["seq"], rec["seq"]))
        elif kind == EVENT:
            name = rec.get("event") or "?"
            if name not in SHOWN_EVENTS:
                items.append(_Item(None, rec["seq"], rec["seq"]))
                continue
            detail = rec.get("word") or rec.get("text") or ""
            reason = rec.get("reason")
            line = f"[heard {_where(rec)} {_hms(rec)} {name}]"
            if detail:
                line += f" {detail}"
            if reason:
                line += f" ({reason})"
            items.append(_Item(line, rec["seq"], rec["seq"]))
        else:
            items.append(_Item(None, rec["seq"], rec["seq"]))  # unknown kind: skipped
    for key, parts in pending.items():
        first = parts[0]
        joined = " ".join(p.get("text", "").strip() for p in parts).strip()
        who = _who([p["seq"] for p in parts], names)
        who = f" {who}" if who else ""
        items.append(_Item(
            f"[heard {_where(first)} {_hms(first)}{who} partial] {joined} …",
            first["seq"], parts[-1]["seq"]))
    items.sort(key=lambda it: it.last)
    return items


def render(items: list[_Item], *, budget_tokens: int = DEFAULT_BUDGET_TOKENS,
           max_lines: Optional[int] = None, file_name: str = "the heard log"
           ) -> tuple[str, Optional[int]]:
    """Fit ``items`` to the budget, oldest first.

    Returns the text (empty when there is nothing to show) and the seq the
    cursor may move to (None when there were no items at all). On a cut the
    last line is ``[heard] +k more, seq a-b`` and the cursor stops at the
    last item printed. The first shown line is always printed, cut short
    if it alone is over budget, so one long turn cannot jam the cursor.
    """
    if not items:
        return "", None
    out: list[str] = []
    used = 0
    cursor = None
    shown = 0
    limit = max(budget_tokens - _CUT_RESERVE_TOKENS, 1)
    for i, it in enumerate(items):
        if it.text is None:
            cursor = it.last
            continue
        cost = estimate_tokens(it.text) + 1
        over_lines = max_lines is not None and shown >= max_lines
        if over_lines or (shown and used + cost > limit):
            rest = [x for x in items[i:] if x.text is not None]
            a = items[i].first if cursor is None else cursor + 1
            b = max(x.last for x in items[i:])
            out.append(f"[heard] +{len(rest)} more, seq {a}-{b}")
            return "\n".join(out), cursor
        text = it.text
        if cost > limit:
            keep = max(limit * 4 - 80, 40)
            text = (f"{text[:keep]} … (+{len(text) - keep} chars; whole line: "
                    f"seq {it.last} in {file_name})")
            cost = estimate_tokens(text) + 1
        out.append(text)
        used += cost
        shown += 1
        cursor = it.last
    return "\n".join(out), cursor


# --------------------------------------------------------------------------
# Following: every line, for a human (`voicemode exchanges tail --heard`)
# --------------------------------------------------------------------------

def _said_line(rec: dict) -> str:
    who = rec.get("agent") or "?"
    text = rec.get("text_played_est") or ""
    if rec.get("cut"):
        tail = f" … (cut at {rec.get('cut_at_s', 0):.1f}s: {rec.get('reason', '?')})"
    else:
        tail = ""
    return f"[said {who} {_where(rec)} {_hms(rec)}] {text}{tail}"


def format_record(rec: dict) -> str:
    """One log line, whole: every kind, quiet events included (this is the
    diagnostic view; the hook is the context-conservative one)."""
    kind = rec.get("kind") or "?"
    head = f"#{rec.get('seq', '?')} {_hms(rec)} {kind:<7} {_where(rec)}{_via(rec)}"
    if kind == EVENT:
        body = " ".join(str(x) for x in (rec.get("event"), rec.get("word") or rec.get("text"),
                                         f"({rec['reason']})" if rec.get("reason") else None) if x)
    elif kind == SAYING:
        body = f"{rec.get('text', '')}  [{rec.get('voice', '?')} {rec.get('backend', '?')} utt {rec.get('utt')}]"
    elif kind == SAID:
        body = (f"{rec.get('text_played_est', '')}  [{rec.get('reason', '?')} "
                f"{rec.get('played_s', 0):.1f}/{rec.get('dur_s') or 0:.1f}s utt {rec.get('utt')}]")
    else:
        body = rec.get("text", "")
        if kind == TURN and rec.get("detector"):
            body += f"  [{rec['detector']}]"
    return f"{head}  {body}"


def follow(*, backlog: int = 10, after_seq: Optional[int] = None, poll: float = 0.5,
           directory: Optional[Path] = None, stop=None) -> Iterable[dict]:
    """Yield records as they are written, across midnight, oldest first.

    Starts with the last ``backlog`` records (or everything after
    ``after_seq``), then polls every ``poll`` seconds. ``stop()`` returning
    True ends it (tests); otherwise it runs until interrupted.
    """
    if after_seq is None:
        recs, tail = read_after(-1, directory=directory, max_files=1)
        for r in recs[-backlog:] if backlog > 0 else []:
            yield r.rec
        seq = recs[-1].seq if recs else last_seq(directory)
    else:
        recs, tail = [], None
        seq = after_seq
    hint = tail
    while True:
        recs, tail = read_after(seq, hint_file=hint[0].name if hint else None,
                                hint_offset=hint[1] if hint else 0, directory=directory)
        for r in recs:
            yield r.rec
            seq = r.seq
        if tail:
            hint = tail
        if stop is not None and stop():
            return
        time.sleep(poll)


# --------------------------------------------------------------------------
# The ride-along hook
# --------------------------------------------------------------------------

def _env_int(name: str, default: Optional[int]) -> Optional[int]:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def hook_disabled() -> bool:
    return os.environ.get("VOICEMODE_HEARD_HOOK", "").strip().lower() in (
        "0", "off", "false", "no")


def _init_cursor(session: str, log_directory: Optional[Path],
                 cursor_directory: Optional[Path], by: Optional[dict] = None) -> Cursor:
    """A session with no cursor starts at the end of the log."""
    files = log_files(log_directory)
    if files:
        recs, tail = read_after(-1, hint_file=files[-1].name, directory=log_directory,
                                max_files=1)
        seq = recs[-1].seq if recs else last_seq(log_directory)
        cur = Cursor(seq, tail[0].name if tail else None, tail[1] if tail else 0)
    else:
        cur = Cursor(0)
    save_cursor(session, cur, cursor_directory, by=by)
    return cur


def pending(session: str, *, log_directory: Optional[Path] = None,
            cursor_directory: Optional[Path] = None, lock_timeout: float = 1.0,
            by: Optional[dict] = None) -> Optional[list[dict]]:
    """Records past ``session``'s cursor, oldest first; the cursor does not move.

    For a waiter (``pager listen``'s shape): poll this, decide whether an
    aged turn is there, then ``take()``. A session with no cursor gets one
    at the end of the log, and ``[]``. None if the lock was not had.
    """
    with _SessionLock(session, lock_timeout, cursor_directory) as got:
        if not got:
            return None
        cur = load_cursor(session, cursor_directory)
        if cur is None:
            _init_cursor(session, log_directory, cursor_directory, by=by)
            return []
        recs, _ = read_after(cur.seq, hint_file=cur.file, hint_offset=cur.offset,
                             directory=log_directory)
        return [r.rec for r in recs]


def take(session: str, *, budget_tokens: Optional[int] = None,
         max_lines: Optional[int] = None, log_directory: Optional[Path] = None,
         cursor_directory: Optional[Path] = None, lock_timeout: float = 1.0,
         by: Optional[dict] = None) -> tuple[str, Optional[int]]:
    """Render what ``session`` has not been shown, and advance its cursor.

    Exactly what the hook prints (collapse, budget, cut line), as plain
    text, plus the cursor's new seq. ``("", seq)`` when there is nothing
    to show; ``("", None)`` when the lock was not had. A waiter that wakes
    an idle agent prints this and exits, so the hook does not repeat it.
    ``by`` names who is taking (``mutator()``), for the cursor's write.
    """
    budget = budget_tokens if budget_tokens is not None else _env_int(
        "VOICEMODE_HEARD_BUDGET", DEFAULT_BUDGET_TOKENS)
    if max_lines is None:
        max_lines = _env_int("VOICEMODE_HEARD_MAX_LINES", None)
    with _SessionLock(session, lock_timeout, cursor_directory) as got:
        if not got:
            return "", None
        cur = load_cursor(session, cursor_directory)
        if cur is None:
            cur = _init_cursor(session, log_directory, cursor_directory, by=by)
            return "", cur.seq
        recs, tail = read_after(cur.seq, hint_file=cur.file, hint_offset=cur.offset,
                                directory=log_directory)
        if not recs:
            if tail and (tail[0].name != cur.file or tail[1] != cur.offset):
                save_cursor(session, Cursor(cur.seq, tail[0].name, tail[1]), cursor_directory,
                            by=by)
            return "", cur.seq
        names = load_speakers({r.path for r in recs}, recs[0].seq)
        items = collapse(recs, session, names)
        text, new_seq = render(items, budget_tokens=budget, max_lines=max_lines,
                               file_name=recs[-1].path.name)
        if new_seq is not None and new_seq > cur.seq:
            if new_seq == recs[-1].seq and tail:
                where = (tail[0].name, tail[1])
            else:
                at = next((r for r in recs if r.seq == new_seq), None)
                where = (at.path.name, at.end) if at else (None, 0)
            save_cursor(session, Cursor(new_seq, where[0], where[1]), cursor_directory,
                        by=by)
            return text, new_seq
        return text, cur.seq


def run_hook(stdin_text: str, *, budget_tokens: Optional[int] = None,
             max_lines: Optional[int] = None, log_directory: Optional[Path] = None,
             cursor_directory: Optional[Path] = None, lock_timeout: float = 1.0
             ) -> Optional[str]:
    """One hook run. Returns the JSON Claude Code reads, or None for silence.

    The session is the hook input's ``session_id`` (else the environment's).
    A session with no cursor starts at the end of the log and is shown
    nothing: a new session is not handed what was said before it existed.
    If another reader of the same session holds the lock, this one is
    silent (the holder is printing the same lines).
    """
    try:
        data = json.loads(stdin_text) if stdin_text.strip() else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    session = str(data.get("session_id") or caller_session() or "default")
    event_name = str(data.get("hook_event_name") or "PostToolUse")
    # The cursor records the RAW hook event, never event_name's guess: an
    # input that names none is "other" (or what $EARS_HEARD_EVENT says).
    raw_event = data.get("hook_event_name")
    by = mutator(event=str(raw_event) if raw_event else None)
    text, _ = take(session, budget_tokens=budget_tokens, max_lines=max_lines,
                   log_directory=log_directory, cursor_directory=cursor_directory,
                   lock_timeout=lock_timeout, by=by)
    if not text:
        return None
    return json.dumps({"hookSpecificOutput": {
        "hookEventName": event_name, "additionalContext": text}}, ensure_ascii=False)


# --------------------------------------------------------------------------
# Script entry: `voicemode-heard-hook hook` (installed copy of this file)
# --------------------------------------------------------------------------

def _usage() -> str:
    return ("usage: heard.py hook [--budget TOKENS] [--max-lines N]   (reads hook JSON on stdin)\n"
            "       heard.py write partial|turn TEXT [--source S] [--device D] [--detector D]\n"
            "       heard.py write event NAME [--source S] [--device D] [--text T]\n"
            "       heard.py last-seq\n")


def _opts(argv: list[str]) -> tuple[list[str], dict[str, str]]:
    pos: list[str] = []
    opts: dict[str, str] = {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--") and i + 1 < len(argv):
            opts[a[2:].replace("-", "_")] = argv[i + 1]
            i += 2
        else:
            pos.append(a)
            i += 1
    return pos, opts


def main(argv: Optional[list[str]] = None) -> int:
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    verb = argv.pop(0) if argv else "hook"
    pos, opts = _opts(argv)
    if verb == "hook":
        if hook_disabled():
            return 0
        stdin_text = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
        try:
            out = run_hook(stdin_text,
                           budget_tokens=int(opts["budget"]) if "budget" in opts else None,
                           max_lines=int(opts["max_lines"]) if "max_lines" in opts else None)
        except Exception as e:
            # Fail LOUD, but with 1, never 2. Measured
            # (claude -p, 2.1.280): exit 1 on PostToolUse/PostToolBatch shows
            # as a hook error and the agent carries on; exit 2 STOPS the
            # agent's turn after the tool. A zero here would claim a quiet
            # room while the ride-along is broken (Charter 2).
            sys.stderr.write(f"[heard] the hook failed: {type(e).__name__}: {e}\n")
            return 1
        if out:
            print(out)
        return 0
    if verb == "write":
        if len(pos) < 2:
            sys.stderr.write(_usage())
            return 1
        kind, text = pos[0], pos[1]
        kw: dict[str, Any] = {k: v for k, v in opts.items()}
        if kind == EVENT:
            rec = event(text, **kw)
        else:
            rec = append(kind, text=text, **kw)
        print(json.dumps(rec, ensure_ascii=False))
        return 0
    if verb == "last-seq":
        print(last_seq())
        return 0
    sys.stderr.write(_usage())
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
