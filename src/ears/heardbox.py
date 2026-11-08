"""Where the heard box is: the one rule `ears services install` and the mouth share.

`ears services install` makes the heard box or finds it (``services._heard_drop`` and
``services._heard_mta``), and ``mouth say --in-reply-to`` looks a heard turn up in it
when ``$MOUTH_HEARD_BOX`` is unset (``mouth.reply.heard_boxes``). Both ask this module,
so they never disagree about where the box is; and this module asks sessionmail
(:mod:`ears.boxes`), so it never spells the mail root or the folder boxes live in.

The rule:

1. **Nested:** ``<store>/heard``, under a store box (``message box list`` lists it as
   ``store NAME``; ``message path NAME`` is its folder): the fleet's layout. Install
   keeps it on a mail-drop host and makes it on an MTA host. With more than one store,
   the first by name.
2. **Flat:** ``<boxes>/heard``, the box ``message setup --box heard`` makes on a
   mail-drop host (``message path --boxes`` is the folder, sessionmail 0.3.3).

:func:`find` asks ``message path heard`` first: on an MTA host sessionmail's own map
names the nested box, so one call answers. A box counts only once its ``new/`` is a
directory. Reading this never runs ``message setup`` and makes nothing. With no
``message`` on the host there is no heard box: every answer here is None or empty.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from . import boxes

#: The heard box's name, nested or flat.
HEARD = "heard"
#: The file that marks a store box's folder (sessionmail's `.tm-store`): a heard box
#: whose parent carries it is nested.
STORE_MARK = ".tm-store"
#: The words for the boxes folder when sessionmail cannot name it (older than 0.3.3).
BOXES_WORDS = "<boxes>"


def _quiet(fn, *args):
    """FN(*ARGS), or None on a host without `message`."""
    try:
        return fn(*args)
    except boxes.NoMessage:
        return None


def where() -> Optional[Path]:
    """The folder boxes live in, as sessionmail names it (`message path --boxes`), or
    None when it cannot (no `message`, or one older than 0.3.3)."""
    return _quiet(boxes.boxes_dir)


def stores() -> list:
    """Every store box's folder on this host, sorted by name: the `store NAME` rows of
    `message box list`, each resolved with `message path NAME`. Empty with no `message`."""
    names = _quiet(boxes.stores) or []
    found = []
    for name in names:
        d = _quiet(boxes.path, name)
        if d is not None and d.is_dir():
            found.append(d)
    return found


def nested() -> list:
    """The nested heard boxes that are there: ``<store>/heard`` with a ``new/``."""
    return [d / HEARD for d in stores() if (d / HEARD / "new").is_dir()]


def flat() -> Optional[Path]:
    """The flat heard box's path, ``<boxes>/heard``, there or not; None when sessionmail
    cannot name the boxes folder."""
    w = where()
    return w / HEARD if w is not None else None


def _under_a_store(box: Path) -> bool:
    return (box.parent / STORE_MARK).is_file()


def find() -> Optional[Path]:
    """The heard box install made or found, or None. Nested wins over flat, as before:
    sessionmail's own answer for ``heard`` (`message path heard`) is asked first, and on
    an MTA host its map names the nested box, so one call answers; a flat answer is kept
    only when no store box holds a ``heard``. Only a box whose ``new/`` is a directory is
    returned."""
    own = _quiet(boxes.path, HEARD)
    if own is not None and (own / "new").is_dir() and _under_a_store(own):
        return own
    found = nested()
    if found:
        return found[0]
    if own is not None and (own / "new").is_dir():
        return own
    box = flat()
    return box if box is not None and (box / "new").is_dir() else None


def looked_for() -> str:
    """Where :func:`find` looks, said in one phrase for a one-line message."""
    w = where()
    base = str(w) if w is not None else BOXES_WORDS
    return (f"{base}/<store>/{HEARD} (under a store box, `message box list`) "
            f"or {base}/{HEARD}, each with a new/ (`message path {HEARD}` names it)")
