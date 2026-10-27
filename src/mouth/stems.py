"""stems - ``mouth play --stem NAME[,NAME]``: one stem of a song, or a mix of several.

A song split by ``mouth split`` has four stems in its sidecar (``sidecar.STEMS``:
``vocals``, ``drums``, ``bass``, ``other``). ``--stem`` plays one of them, or a comma
list of them mixed in ONE ffmpeg (``backends.FileSound`` with N inputs), so the mix is
sample-locked.

It has two halves:

- **The caller's** (:func:`names`, :func:`check`), in ``mouth.play``: the names are
  checked (duplicates dropped, an unknown one refused, listing the four), and every
  stem asked for must be in the sidecar already. A missing one is refused (exit 2)
  with the ONE next command, ``mouth split FILE`` (``mouth split --install`` when no
  separator is installed). A stem is never made silently: the on-demand split is the
  instrumental's alone.
- **The player's** (:func:`resolve`), at take time: the line carries the NAMES only
  (``stem``), never paths. The player finds them in the source file's sidecar then, so
  a mail cannot name a file to play: any ``stem_files`` a line arrives with is dropped
  and rebuilt from the names. For a mail from outside, each stem's real path must be a
  file under the sounds directory, as its source had to be (``box._sound``). A stem
  that cannot be played then is not guessed at: the line carries ``stem_error``, and
  the bed files it ``said reason=error`` with that detail, which names the command.

The resolved line keeps everything else (its ``start``/``end``, level, duck, label):

- ``stem``: the names, as asked;
- ``stem_files``: the stems' paths, in that order, what the one ffmpeg decodes;
- ``file``: the first of them; ``source_file``: the song asked for.

``--stem`` is exclusive with ``--instrumental``/``--no-instrumental`` and ignores the
setting: a stem line is never swapped for the instrumental (``instrumental.swap``) and
never starts a split (``mouth.autosplit``). Stems are never levelled, like every
played file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from . import instrumental as _instrumental
from . import sidecar
from . import voices as _voices

#: What the player builds from the names at take time; a line never brings its own.
BUILT = ("stem_files", "stem_error")


def _four() -> str:
    return ", ".join(sidecar.STEMS)


def names(value) -> list:
    """VALUE (``"drums,bass"``, or a list of names) as the list of stem names to play.

    Each name is stripped and lowercased; a duplicate is dropped, keeping the first.
    Raises ValueError for no name at all, or a name that is not one of the four."""
    if isinstance(value, str):
        parts = value.split(",")
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        raise ValueError(f"mouth: --stem names one or more of {_four()}")
    out: list = []
    for p in parts:
        if not isinstance(p, str):
            raise ValueError(f"mouth: --stem names one or more of {_four()}")
        n = p.strip().lower()
        if not n:
            continue
        if n not in sidecar.STEMS:
            raise ValueError(f"mouth: no stem {p.strip()!r}: --stem takes {_four()}")
        if n not in out:
            out.append(n)
    if not out:
        raise ValueError(f"mouth: --stem names one or more of {_four()}")
    return out


def _probe() -> Callable:
    return _instrumental._once(_instrumental.probe_duration)


def _find(source: str, wanted: list, probe: Optional[Callable]) -> tuple:
    """(paths found, names missing) for WANTED in SOURCE's sidecar."""
    found, missing = [], []
    for n in wanted:
        try:
            hit = sidecar.stem(source, n, probe)
        except OSError:
            hit = None
        if hit is None:
            missing.append(n)
        else:
            found.append(str(hit.path))
    return found, missing


def _missing_line(source: str, missing: list) -> str:
    from .autosplit import split_command

    what = " and ".join(missing) + (" stems" if len(missing) > 1 else " stem")
    return (f'mouth: "{Path(source).name}" has no {what} yet: '
            f"run `{split_command(Path(source))}` first")


def check(source, wanted: list, probe: Optional[Callable] = None) -> list:
    """The caller's check, before anything is queued: SOURCE's sidecar holds every stem
    in WANTED (names from :func:`names`). Returns their paths, in order.

    Raises ValueError naming the ONE next command when any is missing:
    ``mouth split FILE``, or ``mouth split --install`` with no separator."""
    source = str(source)
    found, missing = _find(source, wanted, probe if probe is not None else _probe())
    if missing:
        raise ValueError(_missing_line(source, missing))
    return found


def resolve(item: dict, *, local: bool, d: Optional[Path] = None,
            probe: Optional[Callable] = None) -> dict:
    """ITEM with its stems found, at take time; called once, as the player takes it.

    ``local``: the line is the user's own; False for a mail from outside, whose stems
    must lie in the sounds directory (``box.sounds_root(d)``). A line with no ``stem``
    comes back as it is, less any ``stem_files``/``stem_error`` it brought. Only this
    names a stem line's ``source_file``: a mail from outside that brings one has it
    dropped, so a stem it cannot play never claims a song."""
    if not any(k in item for k in ("stem", *BUILT)):
        return item
    out = {k: v for k, v in item.items()
           if k not in BUILT and not (k == "source_file" and not local)}
    if "stem" not in out or not _voices.played_file(out):
        return out
    src = out.get("file")
    try:
        wanted = names(out["stem"])
    except ValueError as e:
        return {**out, "stem_error": str(e)}
    if not isinstance(src, str) or not src:
        return {**out, "stem_error": "mouth: a stem is played from a file's sidecar; no file"}
    found, missing = _find(src, wanted, probe if probe is not None else _probe())
    if missing:
        return {**out, "stem": wanted, "stem_error": _missing_line(src, missing)}
    if not local:
        from . import box

        root = box.sounds_root(d)
        real = [box._sound(p, root) for p in found]
        if any(r is None for r in real):
            return {**out, "stem": wanted,
                    "stem_error": f"mouth: a stem of {Path(src).name} is outside the sounds "
                                  f"directory {root}"}
        found = real
    return {**out, "stem": wanted, "stem_files": found, "file": found[0], "source_file": src}
