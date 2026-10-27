"""autosplit - the caller's decision: does this ``mouth play`` start a split of its file?

``mouth play`` plays a song's instrumental by default (``music_instrumental``). The player
swaps in an instrumental that exists (:mod:`mouth.instrumental`). This module is the
CALLER's half: when there is none yet, it decides, before the play is queued, whether to
start ``mouth split FILE`` detached, so the play can wait for the instrumental in the
queue. Only ``say()`` calls it, for a played file, and so only the user's own play (a
``mouth play`` or a ``play()`` call): a mail from outside never comes here, and never
starts a split.

**A split starts, for the default, only when all of these hold**, checked cheapest first:

1. the setting resolves on (the flag, ``$MOUTH_MUSIC_INSTRUMENTAL``, the layers, on);
2. the play's device is not a call (``call``, ``call:<N>``): a call uses an instrumental
   that exists and never starts or waits on a split;
3. the sidecar has no instrumental (one that exists is the player's to play);
4. no failed split is recorded for these bytes (the source's size and mtime): a file that
   cannot be split does not make every play wait again;
5. the span that plays is :data:`FLOOR_S` or more, the whole file or its
   ``--start``/``--end`` section, from a file of :data:`CEILING_S` or less, by the
   duration probe (``ffmpeg -i``). A play whose own ``--end`` is under the floor runs no
   probe at all. A probe that cannot run or gives no duration makes the file
   ineligible, silently;
6. a separator is present (``$MOUTH_SEPARATOR``, or the install's stamp), checked last,
   so a chime never prints the install line.

Then, under the record's own short lock (:func:`mouth.split.record_lock`), held across
the read, the spawn and the first write: a live ``running`` record is joined (nothing is
spawned, the play waits on it); else :func:`mouth.split.spawn` starts the child and the
first ``running`` record, naming the child's pid, is written before the play is queued.
The child takes the same lock for its first write, so it never sees a missing record.

The play is stamped ``split_wait: true`` and ``split_eta_s`` (the queue ahead included),
and ``say()`` extends a ``--wait`` timeout to the split's deadline.

**``--instrumental``** (the flag) lifts 4 and 5: any span, any length, and a failed file
is tried again. It is refused (ValueError, exit 2) when it cannot be met: no instrumental
and no separator, a file with no duration, and, on a call device with no instrumental, a
play that would have to make one first (the line names the command that makes it).
``--no-instrumental`` (or the setting off) does nothing here.

**The caller is told** on its own stderr, never spoken: the split started (or joined) and
about how long; no separator, and the one command that installs one (on every such play);
a file over the ceiling; a recorded failure. A short span, a call, and a file the probe
cannot read say nothing.
"""

from __future__ import annotations

import json
import shlex
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import instrumental as _instrumental
from . import separator, sidecar
from . import split as _split
from . import splitwait as _splitwait

#: The shortest span that starts a split, in seconds: a chime or a section never does.
FLOOR_S = 30.0

#: The longest file that starts a split by default, in seconds (10 min). It bounds the
#: separator's memory: measured on one Apple-silicon Mac, a 10-minute file's split peaks
#: at about 4.5 GB of footprint and a 20-minute file's at about 7.3 GB (docs/models.md).
CEILING_S = 10 * 60.0


@dataclass
class Plan:
    """What the decision did. ``stamp`` goes on the queued play; ``lines`` go to the
    caller's stderr; ``deadline_t`` is the split's, for a ``--wait`` timeout."""
    action: str                       # none | spawned | joined
    why: str                          # the check that decided it
    stamp: dict = field(default_factory=dict)
    lines: list = field(default_factory=list)
    deadline_t: Optional[float] = None
    pid: Optional[int] = None


def _is_call(device) -> bool:
    from .box import _is_call as is_call

    return is_call(device)


def _name(path: Path) -> str:
    return f'"{path.name}"'


def split_command(path: Path) -> str:
    """The one command that makes PATH's instrumental now: ``mouth split FILE``, or the
    install when no separator is present."""
    if not _split.separator_present():
        return separator.INSTALL
    return f"mouth split {shlex.quote(str(path))}"


def _live(rec: Optional[dict], now: float) -> bool:
    """Whether REC is a split to join: the player's own test (:func:`mouth.splitwait.live`).
    A ``running`` record whose pid lives is not a failure inside the grace past its first
    deadline: a split queued behind another on the one-split lock gets a new deadline only
    when its turn comes. Caller and player agree, so a joined play is a held one."""
    return _splitwait.live(rec, now)


def _failed(rec: Optional[dict], path: Path, now: float) -> bool:
    """A failed split recorded for these bytes (a dead or overdue ``running`` one counts;
    a live one, :func:`_live`, does not)."""
    if _live(rec, now) or _split.record_state(rec, now) != _split.FAILED:
        return False
    try:
        st = path.stat()
    except OSError:
        return False
    return rec.get("size") == st.st_size and rec.get("mtime_ns") == st.st_mtime_ns


def _span(duration: float, start: Optional[float], end: Optional[float]) -> float:
    """The seconds that play: the whole file, or its ``--start``/``--end`` section."""
    lo = max(0.0, float(start or 0.0))
    hi = duration if end is None else min(float(end), duration)
    return max(0.0, hi - lo)


def _ahead_s(path: Path, d: Path, now: float) -> float:
    """Seconds until the splits already running on this host have finished: one split
    runs at a time, so a new one waits behind them. Each caller's record already counts
    the queue ahead of it, so the latest expected end is when the queue clears."""
    me = _split.record_path(path, d).name
    end = now
    try:
        recs = [p for p in _split.splits_dir(d).glob("*.json") if p.name != me]
    except OSError:
        return 0.0
    for p in recs:
        try:
            rec = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict) or rec.get("schema") != _split.RECORD_SCHEMA:
            continue
        if _split.record_state(rec, now) != _split.RUNNING:
            continue
        t0, eta = rec.get("started_t"), rec.get("eta_s")
        if isinstance(t0, (int, float)) and isinstance(eta, (int, float)):
            end = max(end, float(t0) + float(eta))
    return max(0.0, end - now)


def _making_line(path: Path, eta: float) -> str:
    return (f"mouth: making the instrumental of {_name(path)} first, about {max(1, round(eta))} s; "
            "it plays when ready (--no-instrumental plays it now)")


def decide(path, *, on: bool, src: str, device: str, start: Optional[float] = None,
           end: Optional[float] = None, d: Path) -> Plan:
    """Decide for one local play of PATH (a real file). ON and SRC are the resolved
    setting and its layer (``flag`` is ``--instrumental``/``--no-instrumental``); DEVICE
    is the play's resolved device. Raises ValueError for a ``--instrumental`` that cannot
    be met, before anything is written or spawned."""
    path = Path(path)
    asked = on and src == "flag"
    if not on:
        return Plan("none", "off")
    call = _is_call(device)
    if call and not asked:
        return Plan("none", "call")                  # a call costs nothing: no read, no probe
    probe = _instrumental._once(lambda p: _instrumental.probe_duration(p))
    try:
        hit = sidecar.instrumental(str(path), probe)
    except OSError:
        hit = None
    if hit is not None:
        return Plan("none", "instrumental")          # the player swaps it in at take time
    if call:
        raise ValueError(f"mouth: {_name(path)} has no instrumental, and a play on a call "
                         f"never makes one: run `{split_command(path)}` first")
    present = _split.separator_present()
    if asked and not present:
        raise ValueError(f"mouth: {_name(path)} has no instrumental and no separator is "
                         f"installed: run `{separator.INSTALL}`")
    now = time.time()
    if not asked:
        if end is not None and _span(float("inf"), start, end) < FLOOR_S:
            return Plan("none", "short")             # no probe needed: the section is short
        rec = _split.read_record(path, d)
        if _failed(rec, path, now):
            if rec.get("state") == _split.RUNNING and _split.pid_alive(rec.get("pid")):
                # overdue, not failed: its process still runs, past its deadline and grace
                line = (f"mouth: {_name(path)} plays as it is: its split is still running, "
                        "past its deadline; --instrumental tries again")
            else:
                why = rec.get("detail") if rec.get("state") == _split.FAILED else None
                why = why or "it stopped before it finished"
                line = (f"mouth: {_name(path)} plays as it is: its split failed ({why}); "
                        "--instrumental tries again")
            return Plan("none", "failed", lines=[line])
    duration = probe(str(path))
    if duration is None:
        if asked:
            raise ValueError(f"mouth: ffmpeg -i gives no duration for {_name(path)}: its "
                             "instrumental cannot be made")
        return Plan("none", "no-duration")
    if not asked:
        if _span(duration, start, end) < FLOOR_S:
            return Plan("none", "short")
        if duration > CEILING_S:
            return Plan("none", "long", lines=[
                f"mouth: {_name(path)} is over {CEILING_S / 60:g} min, so it plays as it is; "
                "--instrumental makes its instrumental first"])
        if not present:
            return Plan("none", "no-separator", lines=[
                f"mouth: {_name(path)} plays with its vocals: no separator is installed; "
                f"`{separator.INSTALL}` makes one (music_instrumental: false silences this)"])
    return _start(path, duration, d)


def _start(path: Path, duration: float, d: Path) -> Plan:
    """Join the file's live split, or spawn one and write its first record: one lock."""
    with _split.record_lock(path, d):
        now = time.time()
        rec = _split.read_record(path, d)
        if _live(rec, now):
            t0, eta = rec.get("started_t"), rec.get("eta_s")
            left = (float(t0) + float(eta) - now) if isinstance(t0, (int, float)) \
                and isinstance(eta, (int, float)) else _split.eta_s(duration, d)
            left = round(max(0.0, left), 1)
            dl = rec.get("deadline_t")
            if isinstance(dl, (int, float)) and now > dl:
                dl = dl + _splitwait.GRACE_S          # queued past its first: the player's bound
            return Plan("joined", "running", stamp={"split_wait": True, "split_eta_s": left},
                        lines=[_making_line(path, left)],
                        deadline_t=float(dl) if isinstance(dl, (int, float)) else None,
                        pid=rec.get("pid"))
        eta = round(_split.eta_s(duration, d) + _ahead_s(path, d, now), 1)
        try:
            st = path.stat()
            pid = _split.spawn(path, d)
        except OSError as e:
            return Plan("none", "spawn-failed", lines=[
                f"mouth: {_name(path)} plays as it is: cannot start mouth split ({e})"])
        deadline = round(now + _split.deadline_for(eta), 3)
        _split.write_record(path, {"source": str(path), "pid": pid, "size": st.st_size,
                                   "mtime_ns": st.st_mtime_ns, "state": _split.RUNNING,
                                   "started_t": round(now, 3), "eta_s": eta,
                                   "deadline_t": deadline, "duration_s": duration,
                                   "by": "play"}, d)
    return Plan("spawned", "eligible", stamp={"split_wait": True, "split_eta_s": eta},
                lines=[_making_line(path, eta)], deadline_t=deadline, pid=pid)


def tell(plan: Plan, err=None) -> None:
    """The plan's lines, on the caller's stderr: never spoken."""
    err = err if err is not None else sys.stderr
    for line in plan.lines:
        print(line, file=err, flush=True)
