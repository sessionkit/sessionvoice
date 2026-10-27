"""sidecar - find a played file's instrumental and stems in the folder beside it.

A song's separated parts live in a folder next to it, its sidecar. The mouth
writes ONE layout and reads four.

**Written** (by ``mouth split``), for a played file ``<dir>/<name>``, where
``<name>`` keeps its extension::

    <dir>/.<name>.d/                 e.g. .01-song.m4a.d/
      stems/vocals.flac
      stems/drums.flac
      stems/bass.flac
      stems/other.flac
      mixes/instrumental.flac        drums + bass + other
      split.json                     the manifest: what made them

**Read**, never moved or written. The first hit wins:

1. ``.<name>.d/``, the layout above (``dot``).
2. ``<name>.d/``, the same folder with no dot (``name``).
3. ``<base>.d/``, where ``<base>`` is the name without its extension
   (``base``).
4. Raw separator outputs inside any of those folders, in the same folder
   order (``raw``): ``htdemucs*/<base>/<part>.*`` (Demucs's own output
   tree), then, for the instrumental only, ``mlx-roformer/no_vocals.*``.

- **An instrumental** is ``mixes/instrumental``, then ``mixes/karaoke``, then
  ``stems/instrumental``, in each of folders 1 to 3; then step 4's
  ``no_vocals``.
- **A stem NAME** is ``stems/<NAME>`` in each of folders 1 to 3; then step 4's
  ``<NAME>``.
- **Extensions, best first:** :data:`EXTENSIONS`. A folder holding both
  ``instrumental.wav`` and ``instrumental.flac`` gives the FLAC.

**Staleness** applies to the written layout only, the one folder with a
manifest. When the source's size and mtime match ``split.json``'s, its
outputs are current. When they differ, the source's duration is probed: a
duration within :data:`STALE_TOLERANCE_S` of the manifest's is still current
(a re-tag changes the bytes, never the length). Anything else is stale: a
duration off by more, a probe that gives none, or a manifest that cannot be
read. A stale folder is skipped whole, and the read goes on to the next
layout. A dot folder with no manifest at all was filled by hand and is read
as it stands, like the other layouts.

**The speech verdict.** After a split, an instrumental
:data:`SPEECH_GAP_DB` or more below the mix means the file is speech (a
podcast, a voice memo), not music: :func:`speech_verdict`. The manifest
records it as ``verdict``, and a :class:`Hit` from the written layout reports
it, so the caller can play the original for speech.

Nothing here writes, runs a process or raises on a bad folder: a duration
probe is passed in by the caller (:func:`parse_duration` reads the
``ffmpeg -i`` text). Paths are taken as given; the caller resolves links
first, so the sidecar sits beside the real file.
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

#: The manifest's file name, inside the written folder.
MANIFEST = "split.json"

#: The manifest version this reader knows.
MANIFEST_VERSION = 1

#: Audio extensions, best first.
EXTENSIONS = ("flac", "wav", "aiff", "aif", "m4a", "mp3", "ogg", "opus")

#: The four stem names a separator writes.
STEMS = ("vocals", "drums", "bass", "other")

#: Where an instrumental sits in folders 1 to 3, best first.
INSTRUMENTAL_NAMES = ("mixes/instrumental", "mixes/karaoke", "stems/instrumental")

#: A source whose duration moved by more than this (seconds) has stale outputs.
STALE_TOLERANCE_S = 1.0

#: An instrumental this many dB or more below the mix makes the file speech.
SPEECH_GAP_DB = 20.0

#: The verdicts a manifest may carry.
VERDICTS = ("music", "speech")

#: The layouts, in read order.
LAYOUTS = ("dot", "name", "base", "raw")


@dataclass(frozen=True)
class Hit:
    """One found part.

    ``path`` is the audio file; ``folder`` the sidecar folder it was found in;
    ``layout`` one of :data:`LAYOUTS`. ``manifest`` and ``verdict`` are set
    for the written layout only (``verdict`` is None when the manifest has no
    valid one)."""

    path: Path
    folder: Path
    layout: str
    manifest: Optional[dict] = None
    verdict: Optional[str] = None


def base_name(source) -> str:
    """The file name without its last extension: ``song.flac`` -> ``song``.

    A name with no extension, or a dot file such as ``.song``, is its own
    base."""
    name = Path(source).name
    stem, ext = os.path.splitext(name)
    return stem if ext else name


def canonical_dir(source) -> Path:
    """The folder the mouth writes for SOURCE: ``<dir>/.<name>.d``."""
    p = Path(source)
    return p.parent / f".{p.name}.d"


def read_dirs(source) -> list[tuple[str, Path]]:
    """SOURCE's sidecar folders in read order, as ``(layout, folder)`` pairs.

    Layouts 1 to 3 (``dot``, ``name``, ``base``). A file with no extension
    has the same ``name`` and ``base`` folder, listed once. Whether each
    exists is not checked here."""
    p = Path(source)
    out = [("dot", canonical_dir(p)), ("name", p.parent / f"{p.name}.d")]
    base = p.parent / f"{base_name(p)}.d"
    if base not in [d for _, d in out]:
        out.append(("base", base))
    return out


def find_audio(folder, name: str) -> Optional[Path]:
    """``<folder>/<name>.<ext>`` for the best extension that exists as a file.

    NAME may hold a subfolder (``mixes/instrumental``). None when there is
    none."""
    folder = Path(folder)
    for ext in EXTENSIONS:
        cand = folder / f"{name}.{ext}"
        if cand.is_file():
            return cand
    return None


def read_manifest(folder) -> Optional[dict]:
    """The folder's ``split.json`` as a dict.

    None when there is none. An empty dict when one exists but is not a JSON
    object (unreadable, truncated, a list): no source matches it, so its
    folder is stale."""
    path = Path(folder) / MANIFEST
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError):
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _number(v) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def is_stale(manifest: dict, source, probe: Optional[Callable] = None) -> bool:
    """Whether the outputs MANIFEST describes no longer fit SOURCE.

    Current when the source's ``size`` and ``mtime_ns`` match the manifest's
    ``source`` record. Otherwise PROBE(SOURCE) gives the source's duration in
    seconds (or None); the outputs are current only when it is within
    :data:`STALE_TOLERANCE_S` of the recorded ``duration_s``. No probe, no
    duration, an unreadable manifest or a missing source: stale."""
    rec = manifest.get("source") if isinstance(manifest, dict) else None
    if not isinstance(rec, dict):
        return True
    try:
        st = os.stat(source)
    except OSError:
        return True
    if rec.get("size") == st.st_size and rec.get("mtime_ns") == st.st_mtime_ns:
        return False
    was = _number(rec.get("duration_s"))
    if was is None or probe is None:
        return True
    try:
        now = _number(probe(source))
    except Exception:
        return True
    if now is None:
        return True
    return abs(now - was) > STALE_TOLERANCE_S


def manifest_verdict(manifest) -> Optional[str]:
    """The manifest's ``verdict`` when it is one of :data:`VERDICTS`, else None."""
    v = manifest.get("verdict") if isinstance(manifest, dict) else None
    return v if v in VERDICTS else None


def speech_verdict(mix_db, instrumental_db) -> str:
    """``speech`` when the instrumental is :data:`SPEECH_GAP_DB` or more below
    the mix, else ``music``.

    Levels in dB (any common reference). An instrumental with no level at all
    (``-inf``, digital silence) is speech; a mix with no level, or a value
    that is not a number, is ``music``: the check never hides a song on bad
    data."""
    mix, inst = _level(mix_db), _level(instrumental_db)
    if mix is None or inst is None or mix == -math.inf:
        return "music"
    return "speech" if mix - inst >= SPEECH_GAP_DB else "music"


def _level(v) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return None if math.isnan(f) or f == math.inf else f


_DURATION = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")


def parse_duration(text: str) -> Optional[float]:
    """Seconds from ``ffmpeg -i`` output (``Duration: 00:03:25.12``).

    None when it carries no duration (``Duration: N/A``, no input). ffmpeg
    alone gives it, with no ffprobe."""
    m = _DURATION.search(text or "")
    if not m:
        return None
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def _layered(source, probe, names) -> Optional[Hit]:
    """Folders 1 to 3, NAMES in order in each; then step 4 is the caller's."""
    for layout, folder in read_dirs(source):
        if not folder.is_dir():
            continue
        manifest = verdict = None
        if layout == "dot":
            m = read_manifest(folder)
            if m is not None:
                if is_stale(m, source, probe):
                    continue
                manifest, verdict = m, manifest_verdict(m)
        for name in names:
            hit = find_audio(folder, name)
            if hit is not None:
                return Hit(hit, folder, layout, manifest, verdict)
    return None


def _live_folders(source, probe) -> list[Path]:
    """Folders 1 to 3 that exist and are not stale, for step 4."""
    out = []
    for layout, folder in read_dirs(source):
        if not folder.is_dir():
            continue
        if layout == "dot":
            m = read_manifest(folder)
            if m is not None and is_stale(m, source, probe):
                continue
        out.append(folder)
    return out


def _raw(source, probe, part: str, mlx: bool) -> Optional[Hit]:
    base = base_name(source)
    for folder in _live_folders(source, probe):
        try:
            demucs = sorted(d for d in folder.iterdir() if d.is_dir() and d.name.startswith("htdemucs"))
        except OSError:
            continue
        for d in demucs:
            hit = find_audio(d / base, part)
            if hit is not None:
                return Hit(hit, folder, "raw")
        if mlx:
            hit = find_audio(folder / "mlx-roformer", part)
            if hit is not None:
                return Hit(hit, folder, "raw")
    return None


def instrumental(source, probe: Optional[Callable] = None) -> Optional[Hit]:
    """SOURCE's instrumental, by the read order above, or None.

    PROBE(SOURCE) -> seconds or None is used only to judge a written folder
    whose source changed size or mtime (:func:`is_stale`)."""
    return _layered(source, probe, INSTRUMENTAL_NAMES) or _raw(source, probe, "no_vocals", mlx=True)


def stem(source, name: str, probe: Optional[Callable] = None) -> Optional[Hit]:
    """SOURCE's stem NAME (one of :data:`STEMS`), by the read order above, or None.

    A name that is not one of the four raises ValueError: the caller checks
    names before it looks."""
    if name not in STEMS:
        raise ValueError(f"unknown stem {name!r}: one of {', '.join(STEMS)}")
    return _layered(source, probe, (f"stems/{name}",)) or _raw(source, probe, name, mlx=False)
