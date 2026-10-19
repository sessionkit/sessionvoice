"""The one way a mouth test puts a mail into a maildir (EARS-367). Stdlib only.

``put`` delivers as an MTA does, and as ``box.drop`` does (``src/mouth/box.py``):
the bytes go to ``tmp/``, then ONE rename moves the whole file into ``new/`` (or
``cur/``). A reader that lists ``new/`` sees the mail complete, or not at all.

Writing straight into ``new/`` breaks that contract. The file exists empty between
``open()`` and ``write()``, and a player already running can read it in that gap.
``Queue.entries`` caches its first parse, so one torn read files the mail as
``refused: From (none)`` and the line never plays. Measured in EARS-367: a 10 ms
gap failed ``test_another_senders_unsigned_supersede_cannot_cut_a_taken_line`` 5
times in 6.

``fixtures/fake_sendmail.py`` runs as its own process and imports this file too.
"""
from __future__ import annotations

from pathlib import Path


def put(box, name: str, data, sub: str = "new") -> Path:
    """Deliver DATA (bytes, or text written as UTF-8) into maildir BOX as NAME,
    via ``tmp/`` and one rename into SUB. Makes ``tmp/`` and SUB if they are
    missing. Returns the delivered path."""
    box = Path(box)
    if isinstance(data, str):
        data = data.encode()
    tmp, dest = box / "tmp" / name, box / sub / name
    tmp.parent.mkdir(parents=True, exist_ok=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(data)
    tmp.rename(dest)
    return dest
