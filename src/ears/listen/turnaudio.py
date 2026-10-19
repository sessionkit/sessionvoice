"""Kept turn audio: one WAV per final turn, only while recording is on.

The recording switch (:mod:`ears.recording`) is off by default. While it is off the
capture holds no frames for this, makes no directory and writes nothing. While it is
on, every final turn's audio is kept, whoever spoke: every voice the mic hears is
kept, guests and TV included.

- **Frames.** :class:`TurnAudio` holds the open turn's frames from the turn's ``t0``
  (pre-roll included) to the VAD close, apart from the re-decode's own capped copy.
  A turn longer than :data:`CAP_S` keeps its first :data:`CAP_S` seconds and the
  record says ``audio_truncated: true``. Whether to hold is re-read from the switch
  at each turn start and every poll (a few times a second), so a turn that opened
  while it was off holds nothing.
- **File.** ``<heard base>/audio/<YYYY-MM-DD>/<HHMMSS-mmm>-<n>.wav``: 16 kHz mono
  int16, 0600 in 0700 dirs. ``n`` is 1, or the next free number when that name is
  taken. It is written to a temp name in the same dir, then linked into place (never
  over another file), before the turn row, so the row can name it.
- **Rule 8.** The switch is checked again when the file is about to be written: off
  then means no file at all, and the turn row carries no ``audio`` field.
- **Discarded turns** (no text) keep no audio.
- **Retention.** ``record_days`` (default 7, 0 = never) is read from the switch at
  each prune. The prune runs at capture start and then at most once an hour, and only
  when the switch reads ON at that moment and ``record_days`` is more than 0: with
  recording off nothing is pruned, whatever is on disk. It is
  SCOPED: it looks only at ``<heard base>/audio/``, only at children named like a
  date (``YYYY-MM-DD``) that are real dirs, and in them deletes only regular files
  named as this module names them (``HHMMSS-mmm-N.wav`` and its own temp files).
  It then removes the date dir only if it is empty. Nothing else is ever touched:
  not the missed-reply clips, not the mouth's audio, not a file or dir it did not
  write. Disk: 32 kB a second of speech, about 115 MB an hour.

A write or prune error never stops the capture: the loop says it once, as a
``turn-audio-error`` event, and the turn row lands without ``audio``.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import time
from collections import deque
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .. import heard, recording
from .missed import DIR_MODE, write_wav
from .sources import FRAME_S

CAP_S = 120.0  # a turn keeps at most its first 120 s
PRUNE_EVERY_S = 3600.0
DIR_NAME = "audio"
HISTORY_S = 4.0  # pre-roll held before a turn opens (as the loop's re-decode history)

DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
#: the names this module writes: the kept file, and its temp file
OURS_RE = re.compile(r"[0-9]{6}-[0-9]{3}-[0-9]+\.wav|\.[0-9]{6}-[0-9]{3}-[0-9]+\.tmp\.wav")


def default_dir() -> Path:
    """``<heard base>/audio``: beside the heard logs, under the sessionvoice home."""
    return heard.base_dir() / DIR_NAME


def prune(directory: Path, now: float, days: int) -> int:
    """Delete this module's kept files in date dirs older than ``days``; return how many.

    ``days`` 0 never prunes. A date dir is older when its date is before
    ``today - days`` (local time), as the mouth's own prune. Only regular files
    matching :data:`OURS_RE`, in real dirs matching :data:`DATE_RE`, are removed; a
    date dir is then removed only if nothing is left in it."""
    if days <= 0 or not directory.is_dir() or directory.is_symlink():
        return 0
    cutoff = _dt.date.fromtimestamp(now) - _dt.timedelta(days=days)
    gone = 0
    for d in directory.iterdir():
        if not DATE_RE.fullmatch(d.name) or d.is_symlink() or not d.is_dir():
            continue
        try:
            day = _dt.date.fromisoformat(d.name)
        except ValueError:
            continue
        if day >= cutoff:
            continue
        for f in d.iterdir():
            if not OURS_RE.fullmatch(f.name) or f.is_symlink() or not f.is_file():
                continue
            try:
                f.unlink()
                gone += 1
            except FileNotFoundError:
                continue  # another capture pruned it first
        try:
            d.rmdir()  # only if empty: anything we did not write stays, and so does its dir
        except OSError:
            pass
    return gone


class TurnAudio:
    """One capture's kept turn audio. The loop calls :meth:`open` once, :meth:`poll`
    every few frames, :meth:`frame` with every frame, and :meth:`take` / :meth:`write`
    as a turn closes. ``switch`` is the capture's :class:`ears.recording.Switch`."""

    def __init__(self, switch=None, directory: Optional[Path] = None,
                 clock: Callable[[], float] = time.time, cap_s: float = CAP_S) -> None:
        self.switch = switch if switch is not None else recording.Switch()
        self.directory = Path(directory) if directory is not None else default_dir()
        self.clock = clock
        self._cap = max(1, int(round(cap_s / FRAME_S)))
        self._pre: deque = deque(maxlen=int(round(HISTORY_S / FRAME_S)))
        self._turn: Optional[list] = None  # frames of the open turn; None: none held
        self._truncated = False
        self._holding = False
        self._next_prune: Optional[float] = None

    # -- the loop's calls -----------------------------------------------------

    def open(self) -> int:
        """Capture start: read the switch, and prune once (if recording is on). Returns
        how many were pruned."""
        self._holding = self.switch()
        return self._prune()

    def poll(self) -> int:
        """Re-read the switch (off drops whatever is held), and prune if an hour passed."""
        self._set_holding(self.switch())
        if self._next_prune is not None and self.clock() >= self._next_prune:
            return self._prune()
        return 0

    def frame(self, frame: np.ndarray, turn_started: bool, back: int) -> None:
        """One frame. ``turn_started``: a turn opened at this frame, ``back`` frames ago
        counting this one (the pre-roll included)."""
        if turn_started:
            self._set_holding(self.switch())
        if not self._holding:
            return
        self._pre.append(np.array(frame, dtype=np.int16))
        if turn_started:
            hist = list(self._pre)
            self._turn = hist[-back:] if 0 < back < len(hist) else hist
            self._truncated = len(self._turn) > self._cap
            del self._turn[self._cap:]
            return
        if self._turn is None or self._truncated:
            return  # truncated: the turn keeps its first frames, and nothing after the gap
        if len(self._turn) < self._cap:
            self._turn.append(self._pre[-1])
        else:
            self._truncated = True

    def take(self) -> tuple[Optional[list], bool]:
        """The closed turn's frames and whether it was truncated; nothing is held after."""
        frames, trunc = self._turn, self._truncated
        self._turn, self._truncated = None, False
        return frames, trunc

    def cut(self, k: int) -> Optional[tuple[list, bool]]:
        """A speaker split at frame ``k`` of the open turn: keep [:k] as the closing turn,
        and return the rest (and its truncated flag) for :meth:`resume`."""
        if self._turn is None:
            return None
        trunc, held = self._truncated, len(self._turn)
        k = max(0, min(k, held))
        rest = self._turn[k:]
        self._turn = self._turn[:k]
        # frames past the cap were dropped: they were the END, so the closing half is
        # truncated only if the cut fell past what was held; the rest always is
        self._truncated = trunc and k >= held
        return rest, trunc

    def resume(self, rest: Optional[tuple[list, bool]]) -> None:
        """The open turn goes on from a split, with the frames :meth:`cut` returned."""
        if rest is not None and self._holding:
            self._turn, self._truncated = rest

    def write(self, frames: Optional[list], truncated: bool) -> dict:
        """Write the turn's WAV if the switch is on NOW (rule 8). Returns the turn row's
        fields: ``audio``, ``audio_s`` and, when cut, ``audio_truncated``; or {}."""
        if not frames or not self.switch():
            return {}
        now = self.clock()
        stamp = _dt.datetime.fromtimestamp(now)
        d = self.directory / stamp.strftime("%Y-%m-%d")
        for p in (self.directory, d):
            p.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
            os.chmod(p, DIR_MODE)  # whatever the umask, or an older dir
        hms = stamp.strftime("%H%M%S-") + f"{int(now * 1000) % 1000:03d}"
        tmp = d / f".{hms}-{os.getpid()}.tmp.wav"
        write_wav(tmp, frames)
        try:
            n = 1
            while True:
                path = d / f"{hms}-{n}.wav"
                try:
                    os.link(tmp, path)  # never over another file
                    break
                except FileExistsError:
                    n += 1
        finally:
            tmp.unlink(missing_ok=True)
        out: dict = {"audio": str(path), "audio_s": round(len(frames) * FRAME_S, 3)}
        if truncated:
            out["audio_truncated"] = True
        return out

    # -- inside ----------------------------------------------------------------

    def _set_holding(self, on: bool) -> None:
        self._holding = on
        if not on:
            self._pre.clear()
            self._turn, self._truncated = None, False

    def _prune(self) -> int:
        """Prune, but ONLY when the switch is on now and ``record_days`` is more than 0.
        Both are read here, at prune time: recording off deletes nothing."""
        now = self.clock()
        self._next_prune = now + PRUNE_EVERY_S
        if not self.switch.record().on:
            return 0
        return prune(self.directory, now, self.switch.days().value)
