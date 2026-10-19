"""Barge-in's timed override: ``mouth barge``, the model's layer for a while.

Timed, it may also carry the model's ignore words.
Built in duckswitch's shape: one JSON file, ``<mouth dir>/barge.json``,
written by rename; expiry worked out by the READER against ``now`` (epoch
seconds, shared between processes), so nothing has to run when it ends; and the
precedence said once, in :data:`PRECEDENCE`.

What it sets, each optional, at least one:

- ``words``: N, a whole number of 1 or more, or ``"off"`` (no cut on a word
  count; the user's voice still ducks the line and a stop ask still cuts it);
- ``duck_level``: the line's level while ducked, 0-100 (``--duck off`` is 100:
  no dip);
- ``duck_max_s``: the longest a line stays ducked under unbroken voice, in
  seconds (``barge_duck_max_s``'s model layer);
- ``ignore``: words barge-in never counts. A UNION with the user's or the
  default list: the model may make more words never count, and can never
  make a backchannel count.

Inside the model layer a line's own argument (``mouth say --barge-words``,
``--no-barge``) beats this override: the line sets N only, so the override's
duck, cap and ignore words still apply to it. Each set REPLACES the override
(as ``mouth duck`` does): name every value you want together. The override is
host-wide, not per device. Every function takes ``now``; None asks
:data:`clock`, which a test may replace.

Nothing here raises on a bad or missing file: a policy that cannot read its
override falls back to the user and the default, and never stops a line.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

BARGE_JSON = "barge.json"
#: The order, said once: the schema's ``barge_words`` text and ``mouth barge --help``
#: carry this exact line, and a test holds them to it.
PRECEDENCE = ("per-line --barge-words/--no-barge > the mouth barge override > the user's "
              "barge_* settings > $MOUTH_BARGE_WORDS (N only) > built-in")
#: An override with no ``--for`` lasts an hour, as ``mouth duck``'s.
OVERRIDE_DEFAULT_S = 3600.0
#: The values an override may set, as the record and the policy name them.
KEYS = ("words", "duck_level", "duck_max_s", "ignore")

#: The clock every ``now=None`` reads: epoch seconds. Tests replace it.
clock: Callable[[], float] = time.time


def path(d: Path) -> Path:
    return Path(d) / BARGE_JSON


def _now(now: Optional[float]) -> float:
    return float(clock() if now is None else now)


def _num(v) -> bool:
    return not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v)


def check_words(v):
    """N for the override: a whole number of 1 or more, or ``"off"``. ValueError else."""
    from .hold import check_barge_words

    try:
        if v is None:
            raise ValueError
        return check_barge_words(v)
    except ValueError:
        raise ValueError("mouth barge: N is a whole number, 1 or more, or off") from None


def check_duck(v) -> int:
    """``--duck``: a level 0-100 (a whole number), or ``off``, which is 100: no dip."""
    if isinstance(v, str):
        if v.strip().lower() == "off":
            return 100
        try:
            v = float(v.strip())
        except ValueError:
            v = None
    if not _num(v) or not 0 <= v <= 100 or v != int(v):
        raise ValueError("mouth barge: --duck is a level, 0-100, or off")
    return int(v)


def check_duck_max(v) -> float:
    """``--duck-max``: seconds, 0 or more (``4`` or ``4s``), as ``barge_duck_max_s``."""
    if isinstance(v, str):
        t = v.strip().lower()
        t = t[:-1] if t.endswith("s") else t
        try:
            v = float(t)
        except ValueError:
            v = None
    if not _num(v) or v < 0:
        raise ValueError("mouth barge: --duck-max is seconds, 0 or more")
    return float(v)


def check_ignore(v) -> list:
    """``--ignore``: words, as ``"shell,uh-huh"`` or a list; each split into words
    as the user's partials are (``uh-huh`` is ``uh`` and ``huh``), deduplicated in
    order. ValueError for none at all."""
    from .hold import _words

    parts = v.split(",") if isinstance(v, str) else v
    if not isinstance(parts, (list, tuple)) or not all(isinstance(x, str) for x in parts):
        raise ValueError("mouth barge: --ignore is words, comma-separated")
    out: list = []
    for x in parts:
        for w in _words(x):
            if w not in out:
                out.append(w)
    if not out:
        raise ValueError("mouth barge: --ignore names no word")
    return out


def _valid(rec) -> Optional[dict]:
    """The record if it has the shape ``set`` writes, else None: a hand-edited or
    half-written file is as if there were none."""
    if not isinstance(rec, dict) or not all(_num(rec.get(k)) for k in ("until", "set_t")):
        return None
    if not any(k in rec for k in KEYS) or not isinstance(rec.get("ignore", []), list):
        return None
    try:
        if "words" in rec:
            check_words(rec["words"])
        if "duck_level" in rec:
            check_duck(rec["duck_level"])
        if "duck_max_s" in rec:
            check_duck_max(rec["duck_max_s"])
        if "ignore" in rec:
            check_ignore(rec["ignore"])
    except ValueError:
        return None
    return rec


def read(d: Path) -> Optional[dict]:
    """The override as written, expired or not; None if there is none or it is
    unreadable. Read once a line (``hold.barge_policy``), so no cache."""
    try:
        return _valid(json.loads(path(d).read_text()))
    except (OSError, ValueError):
        return None


def state(d: Path, now: Optional[float] = None) -> Optional[dict]:
    """The LIVE override at ``now``, or None when there is none or it has
    expired (``now >= until``)."""
    rec = read(d)
    if rec is None or _now(now) >= rec["until"]:
        return None
    return rec


def set(d: Path, *, words=None, duck_level=None, duck_max_s=None, ignore=None,  # noqa: A001
        for_s: Optional[float] = None, now: Optional[float] = None, by: Optional[str] = None) -> dict:
    """Write an override for ``for_s`` seconds (else :data:`OVERRIDE_DEFAULT_S`),
    replacing any override. Each value is optional, at least one is given.
    Returns what it wrote. ValueError for a bad value, before anything is written."""
    rec: dict = {}
    if words is not None:
        rec["words"] = check_words(words)
    if duck_level is not None:
        rec["duck_level"] = check_duck(duck_level)
    if duck_max_s is not None:
        rec["duck_max_s"] = check_duck_max(duck_max_s)
    if ignore is not None:
        rec["ignore"] = check_ignore(ignore)
    if not rec:
        raise ValueError("mouth barge: say what to set: N or off, --duck, --duck-max or --ignore")
    if for_s is None:
        for_s = OVERRIDE_DEFAULT_S
    if not _num(for_s) or for_s <= 0:
        raise ValueError("mouth barge: for_s is a number of seconds above 0")
    t = _now(now)
    rec.update(set_t=t, until=t + float(for_s))
    if by:
        rec["by"] = str(by)
    d = Path(d)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f".{BARGE_JSON}.{os.getpid()}.{threading.get_ident()}.part"
    tmp.write_text(json.dumps(rec))
    tmp.rename(path(d))
    return rec


def reset(d: Path) -> bool:
    """Drop the override: the user and the default answer again at once. True if there was a file."""
    try:
        path(d).unlink()
    except FileNotFoundError:
        return False
    return True


def parse_for(text: str) -> float:
    """``mouth barge --for``'s value, in seconds: ``90s``, ``30m``, ``1h`` or plain
    seconds; no "forever" (a standing change is the user's setting). The one
    parser is ``duckswitch.parse_for``; only the verb in the message is
    ours."""
    from . import duckswitch

    try:
        return duckswitch.parse_for(text)
    except ValueError as e:
        raise ValueError(str(e).replace("mouth duck:", "mouth barge:", 1)) from None


def _local(t: float) -> str:
    """Epoch seconds as a local time, the way a speaking agent can say it."""
    return datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds")


def shown(d: Path, now: Optional[float] = None) -> Optional[dict]:
    """The override as ``mouth status`` and ``mouth barge`` show it, or None when
    there is no file: its values, then ``until`` and ``remaining_s`` while it is
    live, or ``expired_at`` once it has run out (the file stays until the next
    set or reset), and ``by``."""
    rec = read(d)
    if rec is None:
        return None
    t = _now(now)
    out = {k: rec[k] for k in KEYS if k in rec}
    if t < rec["until"]:
        out.update(until=_local(rec["until"]), remaining_s=math.ceil(rec["until"] - t))
    else:
        out["expired_at"] = _local(rec["until"])
    if rec.get("by"):
        out["by"] = rec["by"]
    return out
