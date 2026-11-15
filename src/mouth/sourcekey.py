"""sourcekey - which played files are URL sources, and the sha256 of their bytes.

A file played by URL (``file://`` or ``http(s)://``) is somebody else's: a kept
copy in another tool's store, or a file in the mouth's own play cache. Its
sidecar never sits beside it. It lives in the mouth's own folder, keyed by the
file's bytes (``docs/sidecar.md``)::

    <mouth dir>/url-sidecars/
      .paths/<sha256(realpath)[:24]>.json   path, size, mtime_ns, ino, sha256, t
      <sha256>/                             the written layout: stems/, mixes/, split.json

``mouth.play`` registers the real path of every URL source before anything reads
its sidecar (:func:`register`). From then on every reader of that path, the
player's included, finds ``url-sidecars/<sha256>/`` by :func:`lookup`, however
the path is later named. A path never registered keeps the folder beside it.

- :func:`register` hashes the file once: an entry whose size, mtime and inode
  still match is a memo, and the file is not read again.
- :func:`lookup` never hashes and never writes: one ``stat`` and one small read.
  Its ``fresh`` says whether the file is still the bytes that were hashed. A
  reader finds nothing in a stale entry's folder (``mouth.sidecar``).
- :func:`forget` drops a path's entry (a swept spool file). Its folder is kept:
  the same bytes played again find their stems.

The user-facing rule: ``docs/sidecar.md``, "A URL source's sidecar".
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import NamedTuple, Optional

from .paths import mouth_dir

#: The folder under the mouth dir that holds every URL source's sidecar.
ROOT = "url-sidecars"

#: The registry, inside :data:`ROOT`.
PATHS = ".paths"

#: Bytes read at a time while hashing.
CHUNK = 1024 * 1024

#: Hex characters of the realpath's sha256 that name a registry entry.
ENTRY_KEY_LEN = 24


class Entry(NamedTuple):
    """A registered path: the ``sha256`` of its bytes when registered, and whether
    the file is still the one that was hashed (``fresh``: same size, mtime and
    inode now)."""

    sha256: str
    fresh: bool


def root(d: Optional[Path] = None) -> Path:
    """``<mouth dir>/url-sidecars``."""
    return Path(d or mouth_dir()) / ROOT


def folder(sha256: str, d: Optional[Path] = None) -> Path:
    """A URL source's sidecar folder: ``<mouth dir>/url-sidecars/<sha256>``."""
    return root(d) / sha256


def _entry_path(real: str, d: Optional[Path]) -> Path:
    k = hashlib.sha256(real.encode()).hexdigest()[:ENTRY_KEY_LEN]
    return root(d) / PATHS / f"{k}.json"


def _stat_of(st: os.stat_result) -> dict:
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "ino": st.st_ino}


def _read(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _valid(data: Optional[dict], real: str) -> bool:
    if not data or data.get("path") != real:
        return False
    sha = data.get("sha256")
    return isinstance(sha, str) and len(sha) == 64 and all(c in "0123456789abcdef" for c in sha)


def hash_file(path) -> str:
    """The sha256 of PATH's bytes, read :data:`CHUNK` at a time."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def lookup(path, d: Optional[Path] = None) -> Optional[Entry]:
    """PATH's registry entry, or None when it was never registered.

    Never hashes and never writes. ``fresh`` is False when the file's size, mtime
    or inode moved since it was hashed, or the file is gone."""
    real = os.path.realpath(path)
    data = _read(_entry_path(real, d))
    if not _valid(data, real):
        return None
    try:
        now = _stat_of(os.stat(real))
    except OSError:
        return Entry(data["sha256"], False)
    fresh = all(data.get(k) == v for k, v in now.items())
    return Entry(data["sha256"], fresh)


def register(path, d: Optional[Path] = None) -> str:
    """Record PATH as a URL source and return the sha256 of its bytes.

    A fresh entry is a memo: its sha256 comes back with no read of the file.
    Otherwise the file is hashed and the entry written atomically. ValueError
    (``mouth: cannot read PATH: why``) when the file cannot be read, or the
    registry cannot be written."""
    real = os.path.realpath(path)
    entry = _entry_path(real, d)

    def cannot(e: OSError) -> ValueError:
        return ValueError(f"mouth: cannot read {real}: {e.strerror or type(e).__name__}")

    for _ in range(3):   # a file that changes while it is read is hashed again
        try:
            st = _stat_of(os.stat(real))
        except OSError as e:
            raise cannot(e) from None
        data = _read(entry)
        if _valid(data, real) and all(data.get(k) == v for k, v in st.items()):
            return data["sha256"]
        try:
            sha = hash_file(real)
            if _stat_of(os.stat(real)) == st:
                break
        except OSError as e:
            raise cannot(e) from None
    else:
        raise ValueError(f"mouth: cannot read {real}: it keeps changing while it is read")
    rec = {"path": real, **st, "sha256": sha, "t": round(time.time(), 3)}
    tmp = entry.with_name(f".{entry.name}.{os.getpid()}.{threading.get_ident()}.part")
    try:
        entry.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(rec) + "\n")
        os.replace(tmp, entry)
    except OSError as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise ValueError(f"mouth: cannot record {real} in {entry.parent}: "
                         f"{e.strerror or type(e).__name__}") from None
    return sha


def forget(path, d: Optional[Path] = None) -> bool:
    """Drop PATH's registry entry. True when one was removed. Its folder is kept.
    Never raises."""
    real = os.path.realpath(path)
    entry = _entry_path(real, d)
    if not _valid(_read(entry), real):
        return False
    try:
        entry.unlink()
    except OSError:
        return False
    return True
