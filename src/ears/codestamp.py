"""The code stamp: which ears code a process runs.

A keeper restart is how a deploy reloads the keeper, but the room capture is a separate,
long-lived process that survives it, still running the code it started with. The capture
writes this stamp on every heartbeat (``code``), and the keeper computes the stamp of the code
it would start, with the same python and the same package; when the two differ (or the
capture's beat carries none: older code) the keeper restarts the capture once.

ONE function computes it, here, and both sides call it, so they agree by construction:

    python -m ears.codestamp     # prints this package's stamp, e.g. 0.3.3+1a2b3c4d5e6f

The stamp is the distribution's version, then ``+``, then the first 12 hex digits of a SHA-256
over the package's own Python source: every ``*.py`` under ``ears/``, by its path relative to
the package and its bytes, in sorted order. The package-data folders (``_keeper``,
``_speakers``, ``_speech``, ``_settings``: other processes' files) and ``__pycache__`` are left
out, so the same source gives the same stamp from a checkout and from a wheel. It is computed
once per process and kept: a capture's stamp is the code it loaded at its start, not what is on
disk later. It never raises; with no readable source it is the version alone.
"""
from __future__ import annotations

import functools
import hashlib
import os
import sys
from pathlib import Path

DIST = "sessionvoice"
PACKAGE = Path(__file__).resolve().parent
HASH_LEN = 12


def _version() -> str:
    try:
        from importlib.metadata import version
        return version(DIST)
    except Exception:  # not installed as a distribution (a bare source tree): no version
        return "0"


def _source_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # package data and bytecode are not this package's code; never follow a link out
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("_"))
        out.extend(Path(dirpath) / f for f in filenames if f.endswith(".py"))
    return sorted(out, key=lambda p: p.relative_to(root).as_posix())


def compute(root: Path = PACKAGE, version: str | None = None) -> str:
    """The stamp of the source under ``root`` (the ears package by default), uncached."""
    v = _version() if version is None else version
    h = hashlib.sha256()
    n = 0
    for p in _source_files(root):
        try:
            data = p.read_bytes()
        except OSError:
            continue
        h.update(p.relative_to(root).as_posix().encode() + b"\0" + data + b"\0")
        n += 1
    return f"{v}+{h.hexdigest()[:HASH_LEN]}" if n else v


@functools.lru_cache(maxsize=1)
def stamp() -> str:
    """This process's stamp: computed at the first call, then kept for the process's life."""
    try:
        return compute()
    except Exception:  # never break a capture over its stamp
        return ""


def main() -> int:
    s = stamp()
    if not s:
        return 1
    print(s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
