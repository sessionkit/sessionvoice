"""Ducking's one switch: a timed override, and the decision every duck asks.

Ducking can be turned on and off, and overridden for a while: someone
singing along to karaoke does not want the karaoke to duck, so a
temporary override turns it off and the music plays on at its own
level.

Whether a played file dips under speech is decided here, and nowhere else. The
order, highest first:

1. the line's own argument: ``mouth play --duck`` / ``--no-duck`` (the item's
   ``duck.from == "flag"``);
2. the override: ``mouth duck off|on [--for T]``, until it expires;
3. the ``music_duck`` cascade, as stamped on the item at enqueue: session,
   seat, agent, then the settings file;
4. the built-in: on.

The override lives in ``<mouth dir>/duck.json``, beside ``bed.json``: the CLI
that sets it is not the player, and the player starts on demand and idles out.
Expiry is worked out by the READER against ``now`` (epoch seconds, shared
between processes): nothing has to run at the moment it ends. Every function
takes ``now``; None asks :data:`clock`, which a test may replace.

Nothing here raises on a bad or missing file: a duck that cannot read its
switch falls back to the cascade, and never stops the music.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from . import voices as _voices

DUCK_JSON = "duck.json"
#: The order, said once: the schema text and ``mouth duck --help``
#: carry this exact line, and a test holds them to it.
PRECEDENCE = ("per-call --duck/--no-duck > the mouth duck override > the music_duck "
              "cascade (session > seat > agent > settings) > built-in on")
#: An override with no ``--for`` lasts an hour.
OVERRIDE_DEFAULT_S = 3600.0

#: The clock every ``now=None`` reads: epoch seconds. Tests replace it.
clock: Callable[[], float] = time.time

_CACHE: dict = {}
_CACHE_LOCK = threading.Lock()


def path(d: Path) -> Path:
    return Path(d) / DUCK_JSON


def _now(now: Optional[float]) -> float:
    return float(clock() if now is None else now)


def _valid(rec) -> Optional[dict]:
    """The record if it has the shape ``set`` writes, else None."""
    if not isinstance(rec, dict) or not isinstance(rec.get("on"), bool):
        return None
    for k in ("until", "set_t"):
        v = rec.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            return None
    return rec


def read(d: Path) -> Optional[dict]:
    """The override as written, expired or not; None if there is none or it is
    unreadable. Re-read only when the file changes (``decide`` runs every bed
    poll): one ``stat`` otherwise."""
    p = path(d)
    try:
        st = os.stat(p)
    except OSError:
        with _CACHE_LOCK:
            _CACHE.pop(str(p), None)
        return None
    key = (st.st_ino, st.st_mtime_ns, st.st_size)
    with _CACHE_LOCK:
        hit = _CACHE.get(str(p))
    if hit is not None and hit[0] == key:
        return hit[1]
    try:
        rec = _valid(json.loads(p.read_text()))
    except (OSError, ValueError):
        rec = None
    with _CACHE_LOCK:
        _CACHE[str(p)] = (key, rec)
    return rec


def state(d: Path, now: Optional[float] = None) -> Optional[dict]:
    """The LIVE override at ``now``: ``{"on", "until", "set_t", ...}``, or None
    when there is none or it has expired (``now >= until``)."""
    rec = read(d)
    if rec is None or _now(now) >= rec["until"]:
        return None
    return rec


def set(d: Path, *, on: bool, for_s: Optional[float] = None, now: Optional[float] = None,
        by: Optional[str] = None) -> dict:  # noqa: A001 - the verb's own name
    """Write an override: ducking ``on`` (or off) for ``for_s`` seconds, else
    :data:`OVERRIDE_DEFAULT_S`. Replaces any override. Returns what it wrote.
    ValueError for a bad ``on`` or ``for_s``, before anything is written."""
    if not isinstance(on, bool):
        raise ValueError("duckswitch: on is true or false")
    if for_s is None:
        for_s = OVERRIDE_DEFAULT_S
    if isinstance(for_s, bool) or not isinstance(for_s, (int, float)) or not math.isfinite(for_s) \
            or for_s <= 0:
        raise ValueError("duckswitch: for_s is a number of seconds above 0")
    t = _now(now)
    rec = {"on": on, "set_t": t, "until": t + float(for_s)}
    if by:
        rec["by"] = str(by)
    d = Path(d)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f".{DUCK_JSON}.{os.getpid()}.{threading.get_ident()}.part"
    tmp.write_text(json.dumps(rec))
    tmp.rename(path(d))
    return rec


def reset(d: Path) -> bool:
    """Drop the override: the cascade answers again at once. True if there was a file."""
    try:
        path(d).unlink()
    except FileNotFoundError:
        return False
    return True


_FOR_UNITS = {"": 1.0, "s": 1.0, "m": 60.0, "h": 3600.0}


def parse_for(text: str) -> float:
    """``mouth duck --for``'s value, in seconds: ``90s``, ``30m``, ``1h`` or plain
    seconds (``1.5h`` too). ValueError for anything else, or for zero: there is
    no "forever", a standing change is the setting's job."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d*)?|\.\d+)\s*([smh]?)\s*", str(text).lower())
    s = float(m.group(1)) * _FOR_UNITS[m.group(2)] if m else 0.0
    if not (s > 0 and math.isfinite(s)):
        raise ValueError(f"mouth duck: --for {text!r} is not a time: say 90s, 30m, 1h or seconds")
    return s


def _local(t: float) -> str:
    """Epoch seconds as a local time, the way a speaking agent can say it."""
    return datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds")


def readout(d: Path, now: Optional[float] = None, *, cascaded: Optional[dict] = None) -> dict:
    """``mouth duck`` with no arguments: what a duck does
    now and why, the override and the time it has left, and the setting under it.

    ``effective``/``by`` are :func:`decide`'s answer with no item (a spoken line;
    a played file's own ``--duck``/``--no-duck`` still beats it, and the caller
    shows those per bed). ``override`` is None when there is none, has
    ``until`` and ``remaining_s`` while it is live, and ``expired_at`` once it
    has run out (the file is left in place until the next off/on/reset)."""
    t = _now(now)
    on, by = decide(None, d, now=t, cascaded=cascaded)
    host = _voices.duck_setting(cascaded=cascaded, d=d)
    out = {"effective": on, "by": by, "override": None,
           "setting": {"on": host["on"], "from": host["from"]}}
    rec = read(d)
    if rec is not None:
        ov: dict = {"on": rec["on"]}
        if t < rec["until"]:
            ov.update(until=_local(rec["until"]), remaining_s=math.ceil(rec["until"] - t))
        else:
            ov["expired_at"] = _local(rec["until"])
        if rec.get("by"):
            ov["by"] = rec["by"]
        out["override"] = ov
    return out


def decide(item: Optional[dict], d: Path, now: Optional[float] = None, *,
           cascaded: Optional[dict] = None) -> tuple[bool, str]:
    """Dip or not, and why: ``(on, by)``.

    ``by`` is ``"argument"``, ``"override"``, or the cascade layer the item's
    duck came from (``session``, ``seat``, ``agent``, ``settings``, ``builtin``).
    ``item`` is a played file's item (its stamped ``duck``); None (the DJ dip at
    a spoken line, which carries no duck of its own) reads the host's
    ``music_duck`` instead, with ``cascaded`` above it if the caller has one."""
    duck = item.get("duck") if isinstance(item, dict) else None
    if not isinstance(duck, dict):
        duck = None
    if duck is not None and duck.get("from") == "flag":
        return bool(duck.get("on", True)), "argument"
    live = state(d, now)
    if live is not None:
        return live["on"], "override"
    if duck is not None:
        return bool(duck.get("on", True)), str(duck.get("from") or "builtin")
    if item is not None:
        # a file with no duck stamped: no layer set one, so the built-in (bed.ducks)
        return bool(_voices.BUILTIN_DUCK["music_duck"]), "builtin"
    host = _voices.duck_setting(cascaded=cascaded, d=d)
    return host["on"], host["from"]
