"""The missed-reply clip: keep the mic audio of a reply the ears missed.

When a reply is expected (just
after a mouth line ends) and no turn arrives, keep the few seconds of mic audio from that
window. Retention follows the recording switch's ``record_days``, as kept turn audio
does. Store it under the ears' own state dir, mode 0600, never in a synced location. Log
one line per capture naming the file. Capture only on a missed reply, never
continuously: it is the user's voice on disk.

How it runs inside the capture (:mod:`ears.listen.loop`):

- The capture follows the heard log it already writes to (:class:`SaidTail`, the same
  incremental read as :class:`~ears.listen.speakers.LabelTail`). A mouth ``said`` record
  (the line ended) opens a WINDOW of ``window_s`` (30 s in the keeper's room capture)
  of the capture's own frames, the same frames the classifier scored, from the line's
  end. ``said`` lines from before the capture started, and lines that never played
  (``played_s`` 0), open nothing.
- A ``turn`` the capture writes while the window is open (or a turn open when it fills,
  once that turn lands) cancels it: nothing is written. Another ``said`` while it is open
  starts the window again from that line's end (the reply is owed to the latest line).
- A window that fills with no turn is written as ONE 16 kHz mono WAV, mode 0600, in a
  0700 directory (default ``<sessionvoice home>/state/missed-reply/``), and ONE heard
  ``event`` line, ``missed-reply``, names the file. A capture that ends with a window
  still open writes nothing.
- In memory: at most one window, plus a 1 s pre-roll ring that covers the half second
  between the line's end and the capture reading it. Nothing touches disk otherwise.
- Retention follows ``record_days`` (:mod:`ears.recording`, default 7; 0 = never
  pruned, kept forever). Clips older than that many days are deleted at capture start, at
  each new clip, and once an hour of stream time, so no cron is needed. The prune runs
  ONLY when the switch reads ON and ``record_days`` is more than 0, both read at prune
  time: with recording off nothing is pruned, whatever is on disk. Only
  ``missed-reply_*.wav`` files in this module's own directory are touched.
- A directory in a synced location (:func:`synced_reason`) is refused at start with one
  ``missed-reply-off`` event; the capture goes on without clips.

Any error in this path never stops the capture: the loop says it once
(``missed-reply-error``) and carries on.

OPT-IN: the CLI keeps nothing unless ``--missed-reply-window S`` is given (default 0,
every source). ``keeper/ears-keeper.sh``'s room capture, the one on the user's mic, gives it
(``EARS_MISSED_REPLY_S``, default 30).

AND THE RECORDING SWITCH (:mod:`ears.recording`): the window says when a clip
would be cut; the switch says whether it may be written, and it is OFF by default. It is
checked when a window would open and again when a clip is about to be written, so a
change to ears.json's ``record`` lands at the next clip. Off: no window is held, nothing
is written, and ONE ``missed-reply-off`` event (``why: recording off``) says so each
time the switch turns off (at the start, if it is off then).
"""

from __future__ import annotations

import json
import os
import re
import stat
import time
import wave
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .. import heard, recording
from .sources import FRAME_S, SAMPLE_RATE

DEFAULT_WINDOW_S = 30.0  # no reply within 30 s of the line's end
DAY_S = 24 * 3600.0
PREROLL_S = 1.0  # covers the lag from the line's end to the capture reading its said
PRUNE_EVERY_S = 3600.0  # a capture that runs for days still drops old clips on time
DIR_NAME = "missed-reply"
PREFIX = "missed-reply_"
DIR_MODE = 0o700
FILE_MODE = 0o600

# A path whose components include one of these is in a sync client's folder.
_SYNC_NAMES = frozenset({
    "dropbox", "google drive", "googledrive", "my drive", "onedrive", "icloud drive",
    "icloud", "box", "box sync", "nextcloud", "owncloud", "pcloud drive", "syncthing",
})
# Marker files a sync client leaves at the root of the folder it syncs.
_SYNC_MARKERS = (".dropbox", ".dropbox.cache", ".stfolder", ".sync", ".nextcloudsync.log", ".owncloudsync.log")
_REMOTE = re.compile(r'^\s*\[\s*remote\s+"', re.MULTILINE)


def default_dir() -> Path:
    """``<sessionvoice home>/state/missed-reply``: the ears' own state dir (as heard-cursor)."""
    return heard.base_dir() / "state" / DIR_NAME


def _stat(path: Path) -> Optional[os.stat_result]:
    """``os.stat(path)``, or None when ``path`` is absent. Any other OSError (an unsearchable
    folder on the way, say) is raised: it must be told apart from absent, and from Python 3.14
    ``Path.exists``/``is_dir``/``is_file`` read it as False, so they cannot be used for this."""
    try:
        return os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None


def _git_config(dot_git: Path) -> Optional[str]:
    """The text of the repo's config, for a ``.git`` dir or a worktree's ``.git`` file.
    Anything that cannot be read raises OSError."""
    if stat.S_ISDIR(os.stat(dot_git).st_mode):
        return (dot_git / "config").read_text(errors="replace")
    gitdir = dot_git.read_text(errors="replace").strip()
    if not gitdir.startswith("gitdir:"):
        raise OSError(f"not a .git file: {dot_git}")
    d = Path(gitdir[len("gitdir:"):].strip())
    d = d if d.is_absolute() else dot_git.parent / d
    common = d / "commondir"
    st = _stat(common)
    if st is not None and stat.S_ISREG(st.st_mode):
        c = Path(common.read_text().strip())
        d = c if c.is_absolute() else d / c
    return (d / "config").read_text(errors="replace")


def synced_reason(path: Path, home: Optional[Path] = None) -> Optional[str]:
    """Why ``path`` is in a synced location, or None when it is local only.

    Resolved first (symlinks followed as far as the path exists), then refused if it is:

    - under iCloud Drive (``~/Library/Mobile Documents``) or a File Provider cloud
      folder (``~/Library/CloudStorage``: Dropbox, Google Drive, OneDrive, Box);
    - under ``~/Desktop`` or ``~/Documents`` (iCloud "Desktop & Documents" syncs them);
    - under a folder named like a sync client's (Dropbox, OneDrive, ...), or one holding
      a sync client's marker file (``.dropbox``, ``.stfolder``, ...);
    - inside a git work tree whose config names a remote (a store that is pushed). A git
      repo with NO remote is local: ``~/.voicemode`` can be one (one tracked file).
      A ``.git`` that cannot be read is refused, to be safe.

    A marker or ``.git`` that cannot be looked up (an unsearchable folder on the way) is
    refused too, never read as absent, on every Python version.
    """
    p = Path(os.path.realpath(Path(path).expanduser()))
    h = Path(os.path.realpath(home if home is not None else Path.home()))
    roots = (
        (h / "Library" / "Mobile Documents", "iCloud Drive (~/Library/Mobile Documents)"),
        (h / "Library" / "CloudStorage", "a cloud-storage folder (~/Library/CloudStorage)"),
        (h / "Desktop", "~/Desktop (iCloud Desktop & Documents)"),
        (h / "Documents", "~/Documents (iCloud Desktop & Documents)"),
    )
    for root, why in roots:
        if p == root or root in p.parents:
            return why
    chain = [p, *p.parents]
    for a in chain:
        if a.name.lower() in _SYNC_NAMES:
            return f"a sync client's folder ({a})"
    for a in chain:
        for marker in _SYNC_MARKERS:
            try:
                there = _stat(a / marker) is not None
            except OSError as exc:
                return f"a folder whose sync markers cannot be read ({a / marker}: {exc})"
            if there:
                return f"a synced folder ({a / marker} is there)"
    for a in chain:
        dot_git = a / ".git"
        try:
            if _stat(dot_git) is None:
                continue
            config = _git_config(dot_git)
        except OSError as exc:
            return f"a git work tree whose config cannot be read ({a}: {exc})"
        if config and _REMOTE.search(config):
            return f"a git work tree with a remote ({a})"
        return None  # the innermost repo is local only
    return None


def prune(directory: Path, now: float, days: int) -> int:
    """Delete this module's clips older than ``days`` days (by mtime); return how many.

    ``days`` 0 (or less) never prunes: kept forever. Only regular files named
    ``missed-reply_*.wav`` directly in ``directory`` are removed. The caller decides
    whether a prune may run at all (:meth:`MissedReply._prune`: recording on)."""
    if days <= 0 or not directory.is_dir():
        return 0
    keep_s = days * DAY_S
    gone = 0
    for f in directory.glob(f"{PREFIX}*.wav"):
        try:
            if f.is_file() and not f.is_symlink() and now - f.stat().st_mtime > keep_s:
                f.unlink()
                gone += 1
        except FileNotFoundError:
            continue  # another capture pruned it first
    return gone


def write_wav(path: Path, frames: list[np.ndarray]) -> None:
    """One 16 kHz mono int16 WAV, created 0600 (never there before: O_EXCL)."""
    audio = np.concatenate(frames).astype(np.int16) if frames else np.zeros(0, dtype=np.int16)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
    try:
        os.fchmod(fd, FILE_MODE)  # whatever the umask
        f = os.fdopen(fd, "wb")
    except BaseException:
        os.close(fd)
        raise
    with f, wave.open(f, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(audio.tobytes())


class HeardTail:
    """The records of one ``kind`` appended to the heard log since this tail was made.

    ``path`` is called on every read (the day rolls over at midnight). The file that is
    there when the tail is made is read from its END: a line spoken before the capture
    started is owed nothing. A new day's file is read from its start. Only whole lines
    are taken; a file that shrank is read again from the start. Never the whole file
    again: each call reads only what was appended since the last.
    """

    def __init__(self, path: Callable[[], Path], kind: str) -> None:
        self.path, self.kind = path, kind
        self._needle = b'"' + kind.encode() + b'"'
        self._file: Optional[Path] = path()
        try:
            self._offset = self._file.stat().st_size
        except OSError:
            self._offset = 0

    def __call__(self) -> list[dict]:
        p = self.path()
        if p != self._file:
            self._file, self._offset = p, 0
        try:
            size = p.stat().st_size
        except OSError:
            return []
        if size < self._offset:
            self._offset = 0
        if size == self._offset:
            return []
        with open(p, "rb") as f:
            f.seek(self._offset)
            data = f.read(size - self._offset)
        end = data.rfind(b"\n")
        if end < 0:
            return []
        self._offset += end + 1
        out = []
        for raw in data[: end + 1].splitlines():
            if self._needle not in raw:  # the capture's own lines: not parsed
                continue
            try:
                rec = json.loads(raw)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("kind") == self.kind:
                out.append(rec)
        return out


class SaidTail(HeardTail):
    """The mouth's ``said`` records appended to the heard log since this tail was made
    (:class:`HeardTail` for ``said``)."""

    def __init__(self, path: Callable[[], Path]) -> None:
        super().__init__(path, heard.SAID)


def _lag_s(rec: dict, now: float) -> float:
    """Wall seconds from the line's end (its ``ts``) to now; 0 if it cannot be read."""
    try:
        return max(0.0, now - datetime.fromisoformat(rec["ts"]).timestamp())
    except (KeyError, TypeError, ValueError):
        return 0.0


Event = tuple[str, dict]


class MissedReply:
    """One capture's missed-reply clips. The loop calls :meth:`open` once, then
    :meth:`poll` every few frames, :meth:`frame` on every frame, :meth:`turn` on every
    turn it writes. Each returns the heard events to write, as ``(name, fields)``."""

    def __init__(
        self,
        said: Callable[[], list[dict]],
        directory: Optional[Path] = None,
        *,
        window_s: float = DEFAULT_WINDOW_S,
        clock: Callable[[], float] = time.time,
        home: Optional[Path] = None,
        switch=None,
    ) -> None:
        if window_s <= 0:
            raise ValueError("window_s must be > 0 (no MissedReply at all is off)")
        self.said = said
        self.directory = Path(directory).expanduser() if directory is not None else None
        self.window_s = window_s
        self.clock = clock
        self.home = home
        self.off: Optional[str] = None  # why clips are off (a synced dir), once open
        # The recording switch; None is this process's own (default off)
        self.switch = switch if switch is not None else recording.Switch()
        self._rec_off: Optional[bool] = None  # the switch's state last said (None: not yet)
        self._window_frames = max(1, int(round(window_s / FRAME_S)))
        self._pre: deque = deque(maxlen=max(1, int(round(PREROLL_S / FRAME_S))))
        self._win: Optional[list] = None  # (frame, prob max) of the open window
        self._said: Optional[dict] = None  # the said that opened it
        self._t0 = 0.0
        self._lag = 0.0
        self._next_prune = PRUNE_EVERY_S

    def open(self) -> list[Event]:
        """Choose the directory, refuse a synced one, and drop old clips. Say the recording
        switch is off, if it is."""
        if self.directory is None:
            self.directory = default_dir()
        why = synced_reason(self.directory, self.home)
        if why is not None:
            self.off = why
            return [("missed-reply-off", {"why": f"synced location: {why}", "dir": str(self.directory)})]
        _on, said = self._recording()
        self._prune()
        return said

    def _prune(self) -> int:
        """Prune this module's old clips, but ONLY when the switch is on now and
        ``record_days`` is more than 0 (0 = kept forever). Both are read here, at prune
        time: with recording off nothing is deleted, whatever is on disk."""
        if not self.switch.record().on:
            return 0
        days = self.switch.days().value
        if days <= 0:
            return 0
        return prune(self.directory, self.clock(), days)

    def _recording(self) -> tuple[bool, list[Event]]:
        """Is the switch on now? Plus the one event when it has just turned off. Off also
        drops an open window: audio taken while it is off is never written later."""
        r = self.switch.record()
        said: list[Event] = []
        if not r.on:
            self._win, self._said = None, None
            if self._rec_off is not True:
                fields = {"why": "recording off", "record_from": r.source}
                if r.invalid:
                    fields["record_invalid"] = True
                said.append(("missed-reply-off", fields))
        self._rec_off = not r.on
        return r.on, said

    @property
    def pending(self) -> bool:
        return self._win is not None

    def poll(self, t: float) -> list[Event]:
        """Read new ``said`` lines; the latest that played opens (or restarts) the window.
        ``t`` is the stream time the next :meth:`frame` starts at."""
        if self.off is not None:
            return []
        on, said = self._recording()
        wall = time.time()  # the said's ts is wall time; ``clock`` is only for retention
        for rec in self.said():
            if not on:
                continue  # read and passed over: no window while recording is off
            if rec.get("played_s") == 0:
                continue  # never played: no reply is owed to it
            lag = _lag_s(rec, wall)
            if lag > self.window_s:
                continue  # read too late to be the window it names
            k = min(len(self._pre), int(round(min(lag, PREROLL_S) / FRAME_S)))
            self._win = list(self._pre)[len(self._pre) - k:] if k else []
            self._said, self._lag = rec, lag
            self._t0 = t - k * FRAME_S
        if t >= self._next_prune:
            self._next_prune = t + PRUNE_EVERY_S
            self._prune()
        return said

    def turn(self) -> list[Event]:
        """A turn landed: the reply was heard, the open window is dropped unwritten."""
        self._win, self._said = None, None
        return []

    def frame(self, frame: np.ndarray, prob_max: Optional[float], in_turn: bool) -> list[Event]:
        """Keep this frame in the pre-roll and the open window; write a full window
        once no turn is open (a turn open when it filled may yet cancel it)."""
        if self.off is not None:
            return []
        item = (np.array(frame, dtype=np.int16), prob_max)
        self._pre.append(item)
        if self._win is None:
            return []
        if len(self._win) < self._window_frames:
            self._win.append(item)
        if len(self._win) < self._window_frames or in_turn:
            return []
        on, said = self._recording()  # rule 8: checked as the clip is about to be written
        if not on:
            return said
        return [self._write()]

    def _write(self) -> Event:
        win, rec = self._win, self._said or {}
        self._win, self._said = None, None
        now = self.clock()
        d = self.directory
        d.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
        os.chmod(d, DIR_MODE)
        pruned = self._prune()
        utt = re.sub(r"[^A-Za-z0-9._-]", "-", str(rec.get("utt") or "line"))[:48]
        stamp = datetime.fromtimestamp(now).strftime("%Y%m%d-%H%M%S-") + f"{int(now * 1000) % 1000:03d}"
        path = d / f"{PREFIX}{stamp}_{utt}.wav"
        n = 1
        while path.exists():
            path = d / f"{PREFIX}{stamp}_{utt}-{n}.wav"
            n += 1
        write_wav(path, [f for f, _ in win])
        probs = [p for _, p in win if p is not None]
        audio_s = len(win) * FRAME_S
        fields = {
            "file": str(path),
            "utt": rec.get("utt"),
            "said_seq": rec.get("seq"),
            "window_s": self.window_s,
            "t0": round(self._t0, 3),
            "t1": round(self._t0 + audio_s, 3),
            "audio_s": round(audio_s, 3),
            "lag_s": round(self._lag, 3),
            "speech_prob_max": round(max(probs), 3) if probs else None,
            "pruned": pruned or None,
        }
        return (heard.EV_MISSED_REPLY, fields)
