"""maildir - make an agent maildir private: the box and its tmp/new/cur, 0700.

Every agent box under the mail root is 0700: it holds what was said and heard.
sessionvoice makes two kinds of box itself (the mouth's queue and log box, and the
store's ``heard`` box at install), so both go through :func:`private`.

Two facts about ``Path.mkdir`` make a bare ``mkdir(parents=True, exist_ok=True)``
leave a box 0755 under the usual umask:

- ``mode=`` is masked by the umask, and ``parents=True`` makes every parent with the
  default mode, never ``mode=``. A box made as the parent of its ``tmp/`` gets 0755.
- ``exist_ok=True`` never touches a directory that is already there, so a box made
  0755 once stays 0755.

So each level is made with ``mode=0o700`` and then chmodded, and a box that already
exists with another mode is repaired. The box's parents (the mail root and the folder
boxes live in) are sessionmail's: they are NEVER made here (SM 0.3.3, the mail root
move: a folder of ours under the root could make an old root look real on a fresh
host). A box whose parent is missing raises ``FileNotFoundError``; the caller asks
sessionmail for the box (``ears.boxes.make``, which runs ``message setup --box``).
"""

from __future__ import annotations

import os
from pathlib import Path

DIR_MODE = 0o700
SUBDIRS = ("tmp", "new", "cur")


def private(box: str | os.PathLike) -> list[Path]:
    """BOX and its tmp/new/cur as directories, each 0700 whatever the umask.

    Made if missing; one with another mode is chmodded to 0700. BOX's parent must
    exist: it is sessionmail's, and ``FileNotFoundError`` says so when it is not there.
    Returns the directories this call chmodded, the box first: an existing box of
    another mode, or a level a umask that masks owner bits made narrower. Empty when
    all four are 0700.
    """
    box = Path(box)
    changed: list[Path] = []
    for d in (box, *(box / s for s in SUBDIRS)):
        d.mkdir(parents=False, exist_ok=True, mode=DIR_MODE)
        if (d.stat().st_mode & 0o777) != DIR_MODE:
            d.chmod(DIR_MODE)
            changed.append(d)
    return changed
