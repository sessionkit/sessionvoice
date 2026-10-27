"""instrumental - a played file's instrumental, swapped in when the player takes it.

When ``music_instrumental`` resolves on for a played file (``voices.instrumental_setting``,
filled in at take time by ``voices.fill_item``), the player plays the file's
instrumental in its place, if its sidecar holds one (``sidecar.instrumental``).
This happens at TAKE time, in the player, so the instrumental that exists then
is the one that plays, on any device, a call's included: a call never starts a
split, but it may use an instrumental that is already made.

The item that comes back keeps everything the line asked (its ``start``/``end``
section, its level, its duck, its label) and changes only what is decoded:

- ``file``: the instrumental, the file that plays;
- ``source_file``: the original, the file that was asked for.

``instrumental_from`` (the layer that answered) is already on the item. A file
that plays as it is carries no ``source_file``.

It stays the original when:

- the setting resolves off (``--no-instrumental``, a layer, or a mail's ask);
- the line plays stems (``--stem``, :mod:`mouth.stems`), which ignore the setting;
- the sidecar has no instrumental (making one is ``mouth split``'s, not this);
- the sidecar's verdict is ``speech`` (a spoken file), unless the line asked
  for the instrumental itself (``--instrumental``, or a mail's own ask);
- the line is a mail from outside and the instrumental's real path is not a
  file under the sounds directory, as its source had to be
  (``box.confined``): a link in a sidecar never leads a mail out of it.

Nothing here writes or splits. The one process it may run is the duration
probe (``ffmpeg -i``), and only to judge a written sidecar whose source changed
size or mtime since its split (``sidecar.is_stale``).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable, Optional

from . import sidecar
from . import voices as _voices

#: The layers that ask for the instrumental on the line itself, not by a setting:
#: they play it even for a file the split judged to be speech.
ASKED = ("flag", "mail")

#: How long the duration probe may take (seconds); past it, no duration.
PROBE_TIMEOUT_S = 5.0


def probe_duration(path) -> Optional[float]:
    """PATH's duration in seconds by ``ffmpeg -i`` (no ffprobe), or None.

    Files only (``-protocol_whitelist file``). Fails soft: no ffmpeg, a timeout,
    or ``Duration: N/A`` is None. ``ffmpeg -i`` with no output exits 1 by
    design; only its text is read."""
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-protocol_whitelist", "file", "-i", str(path)]
    try:
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE, timeout=PROBE_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return None
    return sidecar.parse_duration(r.stderr.decode(errors="replace"))


def _once(probe: Optional[Callable]) -> Optional[Callable]:
    """PROBE, run at most once per path: the reader may judge the same stale
    folder twice (its layouts, then the raw outputs in them), and each probe is
    a process."""
    if probe is None:
        return None
    seen: dict = {}

    def run(path):
        key = str(path)
        if key not in seen:
            seen[key] = probe(path)
        return seen[key]
    return run


def swap(item: dict, *, local: bool, d: Optional[Path] = None,
         probe: Optional[Callable] = probe_duration) -> dict:
    """ITEM with its file's instrumental in place of the file, or ITEM as it is.

    ``local``: the line is the user's own (a local drop: ``mouth play``, a
    ``play()`` call); False for a mail from outside, whose instrumental must lie
    in the sounds directory (``box.sounds_root(d)``). ``probe`` judges a written
    sidecar whose source changed (``sidecar.instrumental``).

    A line that is not a played file, carries no ``instrumental: true``, or was
    already swapped (it carries a ``source_file``) comes back unchanged. Only this
    function names a ``source_file``: a mail from outside that carries one (its extras
    merge onto the item) has it dropped first, so its rows never claim a swap that did
    not happen, and its file is swapped as any other."""
    if not local and "source_file" in item and "stem" not in item:   # stems.resolve names a stem line's
        item = {k: v for k, v in item.items() if k != "source_file"}
    if not _voices.played_file(item) or item.get("source_file") or "stem" in item:
        return item   # a --stem line ignores the setting (mouth.stems)
    if item.get("instrumental") is not True:
        return item
    src = item.get("file")
    if not isinstance(src, str) or not src:
        return item
    try:
        hit = sidecar.instrumental(src, _once(probe))
    except OSError:
        return item
    if hit is None:
        return item
    if hit.verdict == "speech" and item.get("instrumental_from") not in ASKED:
        return item
    path = str(hit.path)
    if not local:
        from . import box

        path = box._sound(path, box.sounds_root(d))
        if path is None:
            return item
    return {**item, "file": path, "source_file": src}
