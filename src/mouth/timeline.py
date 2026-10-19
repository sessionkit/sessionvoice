"""timeline - one call's rows, past and queued, for the ride-along.

When two agents ride along,
each should see what is QUEUED in the mouth and from whom, so it can amend
or retract its own opener, or answer the other, while the user is still talking.
The noun is the CALL, with a timeline that runs into the
future.

This module is the reader. It is READ-ONLY: it never takes
``player.lock``, never runs ``ensure_player``, ``ensure()`` or ``mkdir``, and
reads the queue through ``Queue.lines(act=False)`` only, which files nothing.
It must not import ``mouth.player`` either (+115 ms per run, and a notifier
runs it after every tool batch).

Rows, one dict each. Every key in ``ROW_KEYS`` is always present, as
None when there is no value, so a reader never probes for a key; ``text``
is added only when ``words=True``.

- **now**: ``<mouth dir>/playing.json``, which the player writes while a
  line plays. Its ``start`` is that file's mtime: the player writes it as it
  takes the line.
- **future**: the queue maildir ``new/``, in play order, supersedes
  resolved. ``position`` counts from 1. An amended line keeps its utt and
  its place, and says ``amended: true``; a retracted line is gone.
  ``state`` is ``held`` for a ``hold: turn-end`` line while the user's turn is
  open (``hold.floor`` reads the heard log's tail, as the player does), and
  ``queued`` otherwise.

- **past**: the heard JSONL, read backwards from the newest file, parsing
  only the lines whose bytes could be a ``turn``, ``saying`` or ``said``. A
  ``said`` is a line, paired with its ``saying`` by ``utt``; a turn starts
  ``t1 - t0`` before its ts, and its ``who`` is its speaker label or null.
  A line the user's words cut names them in ``cut_by``, from its said row; the human
  row ends ``cut by: <words>``.
- **the call**: ``room_of`` puts a record in ``local`` or a Delta
  call's ``call:<N>``; a room's calls are runs with no gap over LINGER_S;
  ``call_of`` is the one seam that says which call a record is in, a Call-ID
  when the record carries one, else its window.

``document()`` assembles one call as the JSON document; every row is built with
``row()``, so all rows have one shape. ``main``/``run`` are the CLI (its flags,
the JSON and human forms): ``mouth timeline``, and ``bin/mouth-timeline``
until a cutover puts it on PATH.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import hold as _hold
from .box import Queue
from .paths import mouth_dir

#: The row keys, in output order. ``text`` is added only with words.
ROW_KEYS = (
    "when", "kind", "who", "utt", "seq", "position", "start", "dur_s", "queued_at",
    "state", "cut", "priority", "hold", "expires_at", "amended", "device",
    "mail_id", "in_reply_to", "call_id", "cut_by",
)


def row(*, words: bool = False, text: Optional[str] = None, **kw) -> dict:
    """One row: every ``ROW_KEYS`` key, None unless given; ``text`` only with words."""
    bad = set(kw) - set(ROW_KEYS)
    if bad:
        raise ValueError(f"timeline: not a row key: {', '.join(sorted(bad))}")
    r = dict.fromkeys(ROW_KEYS)
    r.update(kw)
    if words:
        r["text"] = text
    return r


def _iso(t: Optional[float]) -> Optional[str]:
    if t is None:
        return None
    try:
        return datetime.fromtimestamp(float(t)).astimezone().isoformat(timespec="milliseconds")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _line_cols(x: dict) -> dict:
    """What a queued or playing line carries, whichever it is."""
    return {
        "who": x.get("agent"),
        "utt": x.get("utt"),
        "queued_at": x.get("requested_ts") or _iso(x.get("requested_t")),
        "priority": x.get("priority"),
        "hold": x.get("hold"),
        "expires_at": _iso(x.get("expires_t")),
        "amended": bool(x.get("amended_from")),
        "device": x.get("device"),
        "mail_id": x.get("mail_id"),
        "in_reply_to": x.get("in_reply_to"),
        "call_id": x.get("call_id"),
    }


def read_playing(d: Optional[Path] = None) -> Optional[tuple[dict, float]]:
    """(playing.json, its mtime), or None when nothing plays. Never locks, never writes."""
    p = Path(d or mouth_dir()) / "playing.json"
    try:
        raw = p.read_bytes()
        mtime = os.stat(p).st_mtime
        rec = json.loads(raw)
    except (OSError, ValueError):  # gone (the line ended), or caught mid-replace
        return None
    return (rec, mtime) if isinstance(rec, dict) else None


def now_row(d: Optional[Path] = None, *, words: bool = False,
            playing: Optional[tuple[dict, float]] = None) -> Optional[dict]:
    """The line playing now, as a ``when: now`` row; None when nothing plays."""
    playing = read_playing(d) if playing is None else playing
    if playing is None:
        return None
    p, mtime = playing
    return row(when="now", kind="line", start=_iso(mtime), state="playing",
               words=words, text=p.get("text"), **_line_cols(p))


def future_rows(d: Optional[Path] = None, *, words: bool = False,
                heard_dir: Optional[Path] = None, now: Optional[float] = None,
                skip_mail_id: Optional[str] = None) -> list:
    """The queued lines, in play order, as ``when: future`` rows.

    ``heard_dir`` is where the heard logs are (None: the ears' own, as the
    player's hold check reads). ``skip_mail_id`` drops the line playing now,
    should the read race the player taking it.
    """
    lines = Queue(d).lines(act=False)
    floors: dict = {}
    out = []
    for x in lines:
        if skip_mail_id is not None and x.get("mail_id") == skip_mail_id:
            continue
        state = "queued"
        if x.get("hold") == "turn-end":
            ears = _hold.sources_for(x.get("device"))
            if ears not in floors:
                floors[ears] = _hold.floor(heard_dir, now, max_age_s=0.0, sources=ears)
            if floors[ears] == "speaking":
                state = "held"
        out.append(row(when="future", kind="line", position=len(out) + 1, state=state,
                       words=words, text=x.get("text"), **_line_cols(x)))
    return out


def future(d: Optional[Path] = None, *, words: bool = False,
           heard_dir: Optional[Path] = None, now: Optional[float] = None) -> list:
    """The now row (if a line plays), then the future rows: ``--future``'s rows."""
    playing = read_playing(d)
    rows = []
    nr = now_row(d, words=words, playing=playing) if playing is not None else None
    if nr is not None:
        rows.append(nr)
    rows.extend(future_rows(d, words=words, heard_dir=heard_dir, now=now,
                            skip_mail_id=nr["mail_id"] if nr is not None else None))
    return rows


# -- past rows: the heard log --------------------
#
# The heard JSONL is the timeline (docs/logs.md, "The heard log"). Only three kinds are
# conversation: ``turn`` (the user's, or a caller's), ``saying`` (a line's first frame)
# and ``said`` (a line's end, or a line that never played). Partials and events
# never count, for the rows or for a call's gaps: a heartbeat is not talk.

#: A call is a run with no gap over this many seconds.
LINGER_S = 600.0
#: The heard kinds a call is made of.
PAST_KINDS = frozenset({"turn", "saying", "said"})
#: The cheap byte test before json: a record of one of PAST_KINDS has one of these.
_PREFILTER = tuple(b'"' + k.encode() + b'"' for k in sorted(PAST_KINDS))
_CHUNK = 256 * 1024
#: How many heard files the current call reads back through (today, yesterday).
MAX_FILES = 2

LOCAL = "local"


class _Past:
    """One heard record, with its span in epoch seconds and the file it came from."""

    __slots__ = ("rec", "start", "end", "path")

    def __init__(self, rec: dict, start: float, end: float, path: Path):
        self.rec, self.start, self.end, self.path = rec, start, end, path

    @property
    def seq(self) -> int:
        return self.rec["seq"]


def room_of(device: Optional[str]) -> str:
    """``call:<N>`` (or bare ``call``) for a Delta call device, else ``local``.

    The call follows the user across the mic, the AirPods and the speaker, so
    every device that is not a Delta call is one room.
    """
    d = device or ""
    return d if d == "call" or d.startswith("call:") else LOCAL


def call_of(rec: dict, window: Optional[str] = None) -> tuple[str, Optional[str]]:
    """THE seam: which call a record (or a line row) is in, as (key, id).

    A record carrying ``call_id`` is in that call: ``("call-id", <Call-ID>)``.
    Any other is in its v1 window, whose id only the gap scan can know, so the
    caller passes it: ``("window", window)``. When heard turns carry Call-ID,
    this function is the one place that reads it.
    """
    cid = rec.get("call_id")
    if cid:
        return ("call-id", str(cid))
    return ("window", window)


def _epoch(ts) -> Optional[float]:
    try:
        return datetime.fromisoformat(ts).timestamp()
    except (TypeError, ValueError):
        return None


def _num(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return v if v > 0 else 0.0


def _turn_dur(rec: dict) -> float:
    """A turn's length, ``t1 - t0`` (seconds into the listener's audio); 0 if unknown."""
    try:
        return max(0.0, float(rec["t1"]) - float(rec["t0"]))
    except (KeyError, TypeError, ValueError):
        return 0.0


def _span(rec: dict) -> Optional[tuple[float, float]]:
    """(start, end) of a record: a turn ends at its ts and began ``t1 - t0`` before;
    a ``saying`` is an instant (the first frame); a ``said`` ends at its ts, after
    ``played_s`` of audio (0 for a line that never played)."""
    t = _epoch(rec.get("ts"))
    if t is None:
        return None
    kind = rec.get("kind")
    if kind == "turn":
        return (t - _turn_dur(rec), t)
    if kind == "said":
        return (t - _num(rec.get("played_s")), t)
    return (t, t)


def _lines_backwards(path: Path):
    """The file's whole lines, last first, read in chunks from the end.

    The trailing line with no newline is a write in flight: never yielded.
    """
    try:
        f = open(path, "rb")
    except OSError:
        return
    with f:
        pos = f.seek(0, os.SEEK_END)
        carry = b""
        drop_last = True
        while pos > 0:
            n = min(_CHUNK, pos)
            pos -= n
            f.seek(pos)
            parts = (f.read(n) + carry).split(b"\n")
            carry = parts.pop(0) if pos > 0 else b""
            if drop_last and parts:
                parts.pop()  # after the last newline: empty, or a line being written
                drop_last = False
            yield from reversed(parts)


def _records_backwards(heard_dir: Optional[Path] = None, max_files: Optional[int] = MAX_FILES):
    """Turns, sayings and saids, newest first, from the newest heard file back.

    ``max_files`` bounds the files read (None: every one). A line is parsed only
    when its bytes could be one of PAST_KINDS; a torn or foreign line is skipped.
    """
    from ears import heard as _heard

    files = list(reversed(_heard.log_files(heard_dir)))
    if max_files is not None:
        files = files[:max_files]
    for path in files:
        for raw in _lines_backwards(path):
            if not any(k in raw for k in _PREFILTER):
                continue
            try:
                rec = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                continue
            if (not isinstance(rec, dict) or rec.get("kind") not in PAST_KINDS
                    or not isinstance(rec.get("seq"), int)):
                continue
            span = _span(rec)
            if span is None:
                continue
            yield _Past(rec, span[0], span[1], path)


def _split(recs: list) -> list:
    """Records of one room, oldest first -> its calls: runs with no gap over LINGER_S."""
    runs: list = []
    last_end = None
    for p in sorted(recs, key=lambda p: p.seq):
        if last_end is None or p.start - last_end > LINGER_S:
            runs.append([])
        runs[-1].append(p)
        last_end = p.end if last_end is None else max(last_end, p.end)
    return runs


def _window_id(room: str, run: list) -> str:
    return f"window:{room}:{run[0].seq}"


def _newest_run(room: str, heard_dir: Optional[Path], max_files: Optional[int]) -> list:
    """The room's newest call, oldest record first; [] when the room has none.

    Reads backwards and stops at the first gap over LINGER_S before the run (the
    record that proves the gap is read, then left out), or when the files end.
    """
    got: list = []
    earliest = None
    for p in _records_backwards(heard_dir, max_files):
        if room_of(p.rec.get("device")) != room:
            continue
        got.append(p)
        if earliest is not None and earliest - p.end > LINGER_S:
            break
        earliest = p.start if earliest is None else min(earliest, p.start)
    runs = _split(got)
    return runs[-1] if runs else []


def _window_run(room: str, first_seq: int, heard_dir: Optional[Path]) -> Optional[tuple[list, bool]]:
    """The window call ``window:<room>:<first_seq>``: (its records, is it the room's
    newest), or None when no call in the room starts at that seq."""
    got: list = []
    for p in _records_backwards(heard_dir, None):
        if room_of(p.rec.get("device")) != room:
            continue
        got.append(p)
        if p.seq < first_seq:
            break  # one record before it is enough to prove the gap
    runs = _split(got)
    for i, run in enumerate(runs):
        if run[0].seq == first_seq:
            return run, i == len(runs) - 1
    return None


def _speakers(recs: list) -> dict:
    """seq -> speaker label for the turns among ``recs``: the label written on the
    record wins, then the speakers sidecar. Never a guess."""
    from ears import heard as _heard

    names: dict = {}
    turns = [p for p in recs if p.rec.get("kind") == "turn"]
    if any(not p.rec.get("speaker") for p in turns):
        names.update(_heard.load_speakers({p.path for p in turns},
                                          min_seq=min(p.seq for p in turns)))
    for p in turns:
        sp = p.rec.get("speaker")
        if sp and sp != "silent":
            names[p.seq] = str(sp)
    return names


def _iso_at(rec: dict, t: float) -> Optional[str]:
    """``t`` in the record's own UTC offset, so a row's start reads like its ts."""
    try:
        tz = datetime.fromisoformat(rec["ts"]).tzinfo
        return datetime.fromtimestamp(t, tz).isoformat(timespec="milliseconds")
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return _iso(t)


def _past_rows(recs: list, *, words: bool = False) -> list:
    """Past rows for a call's records, by start time.

    A ``said`` is a line: paired with its ``saying`` by ``utt``, it starts at the
    saying and lasts ``played_s``; with no saying it never played, so it starts
    at the said, lasts 0, and its state is the said's reason. A ``saying`` with
    no ``said`` yet is the line playing now: it is the now row, not a past one.
    """
    sayings = {p.rec.get("utt"): p for p in recs if p.rec.get("kind") == "saying"}
    names = _speakers(recs)
    keyed = []
    for p in recs:
        r = p.rec
        kind = r.get("kind")
        if kind == "turn":
            keyed.append((p.start, p.seq, row(
                when="past", kind="turn", who=names.get(p.seq), seq=p.seq,
                start=_iso_at(r, p.start), dur_s=round(_turn_dur(r), 2),
                device=r.get("device"), mail_id=r.get("mail_id"),
                in_reply_to=r.get("in_reply_to"), call_id=r.get("call_id"),
                words=words, text=r.get("text"))))
        elif kind == "said":
            s = sayings.get(r.get("utt"))
            start = s.start if s is not None else p.end
            dur = round(_num(r.get("played_s")), 2) if s is not None else 0.0
            keyed.append((start, p.seq, row(
                when="past", kind="line", who=r.get("agent"), utt=r.get("utt"), seq=p.seq,
                start=_iso_at(r, start), dur_s=dur, queued_at=r.get("requested_ts"),
                state=r.get("reason"), cut=r.get("cut"), hold=r.get("hold"),
                expires_at=_iso(r.get("expires_t")), device=r.get("device"),
                mail_id=r.get("mail_id"), in_reply_to=r.get("in_reply_to"),
                call_id=r.get("call_id"),
                # the words that cut it: a barge-in's said row names them
                cut_by=_words_of(r.get("cut_by")),
                words=words, text=r.get("text_played_est") or "")))
    keyed.sort(key=lambda k: (k[0], k[1]))
    return [k[2] for k in keyed]


def _words_of(v) -> Optional[list]:
    """A said row's ``cut_by`` as a list of words, or None."""
    if isinstance(v, str):
        v = v.split()
    if not isinstance(v, (list, tuple)):
        return None
    return [str(w) for w in v if str(w)] or None


def _call_dict(cid: str, key: str, room: str, recs: list, open_: bool) -> dict:
    return {"id": cid, "key": key, "room": room,
            "started": _iso_at(recs[0].rec, min(p.start for p in recs)) if recs else None,
            "last": _iso_at(recs[-1].rec, max(p.end for p in recs)) if recs else None,
            "open": open_}


def document(d: Optional[Path] = None, heard_dir: Optional[Path] = None, *,
             which: str = "all", words: bool = False, call: Optional[str] = None,
             last: int = 20, now: Optional[float] = None,
             max_files: Optional[int] = MAX_FILES) -> dict:
    """One call as the JSON document: ``{"v", "call", "now", "rows"}``.

    ``which`` is ``all``, ``future`` (the now and queued rows: the notifier's read)
    or ``past`` (finished rows only). ``call`` picks a call by id; the default is
    the current call of the ``local`` room. ``last`` keeps the newest N past
    rows (0: all). Raises LookupError for a ``call`` id that names no call.

    The current call:

    - the local room's newest run of heard records; it is open while its last
      record is under LINGER_S old;
    - a queued (or playing) line in the room joins it while it is open; when it
      has closed, the queued lines are a new call, ``window:local:new``, which is
      open; with nothing queued, the closed run is still the current call;
    - if any of those records or lines carries a Call-ID, the newest one's
      Call-ID is the call (key ``call-id``), and its rows are the ones carrying
      it; the rest stay in the window.
    """
    if which not in ("all", "future", "past"):
        raise ValueError(f"timeline: which is all, future or past, not {which!r}")
    t_now = time.time() if now is None else now
    lines = future(d, words=words, heard_dir=heard_dir, now=now)

    if call is None:
        room = LOCAL
        run = _newest_run(room, heard_dir, max_files)
        run_open = bool(run) and t_now - max(p.end for p in run) <= LINGER_S
        mine = [r for r in lines if room_of(r["device"]) == room]
        if run and not run_open and mine:
            run = []  # a closed call: the queued lines start the next one
        window = _window_id(room, run) if run else f"window:{room}:new"
        with_id = [p.rec for p in run] + mine
        cid = next((x["call_id"] for x in reversed(with_id) if x.get("call_id")), None)
        key, cid = ("call-id", str(cid)) if cid else ("window", window)
        open_ = run_open or not run
        if not run and not mine and key == "window":
            open_ = False  # nothing heard, nothing queued: no call at all
    elif call.startswith("window:"):
        room, _, first = call[len("window:"):].rpartition(":")
        if not room or room != room_of(room) or not (first == "new" or first.isdigit()):
            raise LookupError(f"timeline: not a call id: {call!r}")
        mine = [r for r in lines if room_of(r["device"]) == room]
        if first == "new":
            newest = _newest_run(room, heard_dir, max_files)
            if not mine or (newest and t_now - max(p.end for p in newest) <= LINGER_S):
                raise LookupError(f"timeline: no call {call!r}")  # the lines join the open call
            run, open_ = [], True
        else:
            found = _window_run(room, int(first), heard_dir)
            if found is None:
                raise LookupError(f"timeline: no call {call!r}")
            run, newest_ = found
            open_ = newest_ and t_now - max(p.end for p in run) <= LINGER_S
            if not open_:
                mine = []  # a closed call has no future
        key, cid = "window", call
    else:
        key, cid = "call-id", call
        run = [p for p in _records_backwards(heard_dir, max_files)
               if p.rec.get("call_id") == call][::-1]
        mine = [r for r in lines if r.get("call_id") == call]
        if not run and not mine:
            raise LookupError(f"timeline: no call {call!r}")
        room = room_of((run[-1].rec if run else mine[0]).get("device"))
        open_ = bool(mine) or (bool(run) and t_now - max(p.end for p in run) <= LINGER_S)

    # the rows are the records whose call_of is this call
    run = [p for p in run if call_of(p.rec, cid) == (key, cid)]
    mine = [r for r in mine if call_of(r, cid) == (key, cid)]
    info = _call_dict(cid, key, room, run, open_)

    past = _past_rows(run, words=words) if which != "future" else []
    if last and len(past) > last:
        past = past[-last:]
    if which == "past":
        mine = []
    # a line that ended between the two reads shows once, as past; read off the
    # call's saids, not the past rows, so --future (no past rows) drops it too
    done = {p.rec.get("utt") for p in run if p.rec.get("kind") == "said"}
    mine = [r for r in mine if not (r["when"] == "now" and r["utt"] in done)]
    return {"v": 1, "call": info, "now": _iso(t_now), "rows": past + mine}


# -- the CLI -----------------------------------------
#
# ``mouth timeline`` and ``bin/mouth-timeline`` both land here. The parser is
# built by ``add_arguments`` so ``mouth --help`` and ``mouth timeline --help``
# list the same flags. Exit 0 with rows, and 0 with no rows (a quiet mouth is
# the common case); exit 2 for a bad flag or a --call that names no call.

TIMELINE_HELP = "one call's rows, past and queued: who said what, and who queued what"


def add_arguments(p) -> None:
    """The flags, on an argparse parser (``mouth timeline``'s, or the wrapper's)."""
    side = p.add_mutually_exclusive_group()
    side.add_argument("--future", dest="which", action="store_const", const="future",
                      help="only the line playing now and the queued lines (cheap: the notifier's read)")
    side.add_argument("--past", dest="which", action="store_const", const="past",
                      help="only finished rows: the user's turns and the lines played (or dropped)")
    p.set_defaults(which="all")
    p.add_argument("--words", action="store_true",
                   help="add each row's text: as played, as heard, or as it will be spoken")
    p.add_argument("--json", action="store_true",
                   help="one JSON document, schema v1 (keys always present, null when empty)")
    p.add_argument("--call", metavar="ID",
                   help="a call by id: window:<room>:<seq>, window:<room>:new, or a Call-ID "
                        "(default: the current call); exit 2 if it names no call")
    p.add_argument("--last", type=_nonneg_int, default=20, metavar="N",
                   help="keep the newest N past rows (default 20; 0 keeps all); "
                        "queued rows are never capped")


def _nonneg_int(s: str) -> int:
    import argparse

    try:
        n = int(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {s!r}") from None
    if n < 0:
        raise argparse.ArgumentTypeError(f"0 or more, not {n}")
    return n


def _hms(iso: Optional[str]) -> str:
    """HH:MM:SS of an ISO time, in local time; ``-`` when there is none."""
    if not iso:
        return "-"
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%H:%M:%S")
    except (TypeError, ValueError):
        return "-"


def human(doc: dict, *, words: bool = False, which: str = "all") -> str:
    """The human form: a header, then one line per row, ``-- now --`` before
    the playing and queued rows (unless only past rows were asked for)."""
    c = doc["call"]
    since = f"since {_hms(c['started'])}" if c["started"] else "nothing heard yet"
    state = "open" if c["open"] else "closed"
    out = [f"call {c['id']}  {since}  {state}  (keyed: {c['key']})"]
    past = [r for r in doc["rows"] if r["when"] == "past"]
    rest = [r for r in doc["rows"] if r["when"] != "past"]
    for r in past:
        out.append(_human_row(r, words))
    if rest or which != "past":
        out.append(" -- now --")
        if not rest:
            out.append(" (nothing playing, nothing queued)")
    for r in rest:
        out.append(_human_row(r, words))
    return "\n".join(out)


def _human_row(r: dict, words: bool) -> str:
    if r["when"] == "future":
        lead = f"next {r['position']:<3d}"
    else:
        lead = _hms(r["start"])
    dur = f"{r['dur_s']:.1f}s" if r["dur_s"] is not None and r["when"] == "past" else ""
    s = f" {lead:<8s}  {(r['who'] or '-'):<6s} {r['kind']:<4s}  {dur:>6s}  {(r['state'] or ''):<12s}  {r['utt'] or ''}"
    if r["when"] == "future" and r["queued_at"]:
        s += f"  queued {_hms(r['queued_at'])}"
    if r.get("amended"):
        s += "  (amended)"
    if r.get("cut_by"):   # the words that cut it
        s += "  cut by: " + " ".join(str(w) for w in r["cut_by"])
    if words and r.get("text"):
        s += f"\n           {json.dumps(r['text'], ensure_ascii=False)}"
    return s.rstrip()


def run(a) -> int:
    """Act on parsed ``add_arguments`` flags: print the document, return the exit code."""
    import sys

    try:
        doc = document(which=a.which, words=a.words, call=a.call, last=a.last)
    except LookupError as e:
        msg = str(e.args[0] if e.args else e)
        print(f"mouth {msg}" if msg.startswith("timeline: ") else f"mouth timeline: {msg}",
              file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(doc, ensure_ascii=False))
    else:
        print(human(doc, words=a.words, which=a.which))
    return 0


_FLAGS = ("--future", "--past", "--words", "--json")


def fast_args(argv: list):
    """The flags without argparse, for a well-formed argv only; else None.

    Python 3.14's argparse imports ``_colorize`` and ``shutil`` on the first
    ``add_argument`` (a validation formatter): about 8 ms of a ~60 ms cold call
    that a notifier makes after every tool batch. This takes exactly the argvs
    argparse would take to the same namespace, each flag once, and gives up
    (None) on anything else, so ``--help``, every error message and every
    abbreviation still come from argparse. A test holds the two equal.
    """
    from types import SimpleNamespace

    a = SimpleNamespace(which="all", words=False, json=False, call=None, last=20)
    seen = set()
    it = iter(argv)
    for tok in it:
        flag, eq, val = tok.partition("=")
        if flag in seen:
            return None
        seen.add(flag)
        if tok in _FLAGS:
            if tok in ("--future", "--past"):
                if a.which != "all":
                    return None
                a.which = tok[2:]
            else:
                setattr(a, tok[2:], True)
        elif flag in ("--call", "--last"):
            if not eq:
                val = next(it, None)
                if val is None or val.startswith("-"):
                    return None
            if flag == "--call":
                a.call = val
            else:
                try:
                    a.last = _nonneg_int(val)
                except Exception:
                    return None
        else:
            return None
    return a


def main(argv=None) -> int:
    """``mouth timeline`` on its own (``python -m mouth.timeline``, the wrapper's way in)."""
    import sys

    argv = sys.argv[1:] if argv is None else list(argv)
    a = fast_args(argv)
    if a is None:
        import argparse

        ap = argparse.ArgumentParser(prog="mouth timeline", description=TIMELINE_HELP)
        add_arguments(ap)
        a = ap.parse_args(argv)
    return run(a)


if __name__ == "__main__":
    import sys

    sys.exit(main())
