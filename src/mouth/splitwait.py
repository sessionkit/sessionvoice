"""splitwait - the player's half: a play that waits in the queue for its file's split.

``mouth play`` of a song with no instrumental may start ``mouth split FILE`` and stamp the
play ``split_wait: true`` (:mod:`mouth.autosplit`, the caller's half). This module is
what the player does with that stamp.

**The hold.** While the file's split runs, the player skips the play and keeps its place,
the way it skips a line held for the user's turn (``player._next``, ``player._playable``).
It holds later FILES for the same device too, so "play A, then B" keeps its order. Spoken
lines are not held: no bed exists until the instrumental does.

A play is held (:func:`waiting`) only while ALL of these hold:

- it is a local line (the box's ``_raw`` mark: ``mouth play`` or a ``play()`` call). A
  mail from outside may carry ``split_wait`` in its extras, but it never started a split,
  so its stamp is ignored and it plays at once;
- it is a played file, not on a call device (a call never waits on a split);
- the file's record (:func:`mouth.split.read_record`) is live: ``running``, naming a pid
  that is alive. The stamp alone never holds a play.

**A running record past its deadline, with its pid alive, still holds** for
:data:`GRACE_S` more. The child writes its ``deadline_t`` before it waits its turn on the
host's one-split lock, and sets a new one when its turn comes, so a split queued behind
another can be past its first deadline while it has not yet started. Its own watchdog
stops the separation at the deadline it runs under and records ``failed``. Past the grace
the play is let go. The outputs are looked for again when the play is taken, so an
instrumental that appeared meanwhile is the one that plays.

**When the play is let go** (:func:`settle`, at take time), its rows carry ``split``:
``state`` and ``eta_s``, and ``detail`` when it plays as it is.

- ``done``: the split finished. The instrumental plays (``source_file`` names the song),
  unless the split judged the file speech, which plays as it is.
- ``failed``: the split failed; ``detail`` is its recorded reason.
- ``dead``: the record names a pid that is gone, before the split finished.
- ``overdue``: the split ran past its deadline and the grace.
- ``missing``: no record of the split is left.

Nothing here starts, stops or kills a split. ``mouth stop`` and ``mouth retract`` drop the
queued play, never the job, whose outputs serve the next play.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from . import split as _split
from . import voices as _voices

#: How long past its ``deadline_t`` a live split still holds its play (seconds): a split
#: queued behind another on the host's one-split lock gets a new deadline when its turn
#: comes.
GRACE_S = _split.DEADLINE_MIN_S

#: The stamps the caller puts on a waiting play.
STAMPS = ("split_wait", "split_eta_s")

DONE, FAILED, DEAD, OVERDUE, MISSING = "done", "failed", "dead", "overdue", "missing"


def _is_call(device) -> bool:
    from .box import _is_call as is_call

    return is_call(device)


def stamped(x: dict) -> bool:
    """Whether X is a play the caller stamped to wait for its split: a local played file,
    not on a call device. A mail from outside never is, whatever its extras say."""
    return (x.get("split_wait") is True and bool(x.get("_raw")) and _voices.played_file(x)
            and isinstance(x.get("file"), str) and not _is_call(x.get("device")))


def live(rec: Optional[dict], now: Optional[float] = None) -> bool:
    """Whether REC holds its play: ``running``, its pid alive, and not past its
    ``deadline_t`` by more than :data:`GRACE_S`."""
    if not isinstance(rec, dict) or rec.get("state") != _split.RUNNING:
        return False
    if not _split.pid_alive(rec.get("pid")):
        return False
    deadline = rec.get("deadline_t")
    now = time.time() if now is None else now
    return not (isinstance(deadline, (int, float)) and now > deadline + GRACE_S)


def waiting(x: dict, d: Optional[Path] = None, now: Optional[float] = None) -> bool:
    """Whether the player holds X now: a stamped play (:func:`stamped`) whose file's split
    is live (:func:`live`). Read-only; one small file read per stamped line."""
    if not stamped(x):
        return False
    return live(_split.read_record(x["file"], d), now)


def outcome(rec: Optional[dict], now: Optional[float] = None) -> tuple[str, Optional[str]]:
    """What became of the split REC records, once its play is let go: a state (``done``,
    ``failed``, ``dead``, ``overdue``, ``missing``) and the reason it plays as it is."""
    now = time.time() if now is None else now
    if not isinstance(rec, dict):
        return MISSING, "no record of its split is left"
    state = rec.get("state")
    if state == _split.DONE:
        return DONE, None
    if state == _split.FAILED:
        return FAILED, str(rec.get("detail") or "its split failed")
    if state == _split.RUNNING:
        pid = rec.get("pid")
        if not _split.pid_alive(pid):
            return DEAD, f"its split (pid {pid}) stopped before it finished"
        return OVERDUE, "its split ran past its deadline"
    return MISSING, "no record of its split is left"


def settle(item: dict, x: dict, d: Optional[Path] = None, now: Optional[float] = None) -> dict:
    """ITEM (the taken play, after ``instrumental.swap``) with ``split`` set, when X is a
    stamped play: what its split came to, and why it plays as it is when it does. Any
    other item comes back as it is."""
    if not stamped(x):
        return item
    rec = _split.read_record(x["file"], d)
    state, detail = outcome(rec, now)
    out = {"state": state, "eta_s": x.get("split_eta_s")}
    if item.get("source_file"):            # the instrumental plays
        out["state"] = DONE
    elif detail is None:                   # done, and still the original
        detail = ("the split judged it speech" if (rec or {}).get("verdict") == "speech"
                  else "no instrumental was found after its split")
    if not item.get("source_file") and detail:
        out["detail"] = detail
    return {**item, "split": out}


def running(d: Path, queued: list = (), now: Optional[float] = None) -> list:
    """``mouth status``'s ``splits``: one row per split running on this host (a live
    record, :func:`live`), with the utts of the queued plays that wait on it."""
    now = time.time() if now is None else now
    try:
        paths = sorted(_split.splits_dir(d).glob("*.json"))
    except OSError:
        return []
    waits: dict = {}
    for x in queued:
        if stamped(x):
            waits.setdefault(_split.key(x["file"]), []).append(x.get("utt"))
    out = []
    for p in paths:
        try:
            rec = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict) or rec.get("schema") != _split.RECORD_SCHEMA or not live(rec, now):
            continue
        t0, eta = rec.get("started_t"), rec.get("eta_s")
        left = None
        if isinstance(t0, (int, float)) and isinstance(eta, (int, float)):
            left = round(max(0.0, float(t0) + float(eta) - now), 1)
        out.append({"file": rec.get("source"), "pid": rec.get("pid"), "started_t": t0,
                    "eta_s": eta, "left_s": left, "deadline_t": rec.get("deadline_t"),
                    "plays": waits.get(p.stem, [])})
    return out
