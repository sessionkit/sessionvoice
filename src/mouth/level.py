"""Voice normalization: every voice at one loudness, a gain per voice.

Voices sat about 14 dB
under the user's music (af_sky -27.3 LUFS, peak -9.0 dBFS; a song -12.9 LUFS) while
lines already played at volume 100, so a ducked song still drowned the voice.

- **On/off**: ``normalize`` (bool, default on) in the mouth's settings and the
  cascade above them; a line's own ``mouth say --normalize/--no-normalize``
  wins (``voices.normalize_setting``). Read for every line, so a flip in the
  settings app applies to the next line.
- **Target**: ``normalize_lufs`` (default -16, a usual speech level).
- **A gain per voice**, in an optional ``settings.json`` beside the voice::

      a clone voice:  <voices dir>/<voice>/settings.json
                      ($VOICEMODE_VOICES_DIR, else ~/.voicemode/voices)
      any other:      <mouth dir>/voices/<voice>/settings.json   (Kokoro, Apple)

  ``{"gain_db", "measured_lufs", "measured_at", "gain_from": "measured"|"hand"}``.
  VoiceMode's loader only globs a clone dir's ``*.wav``, so the file is safe
  there; a Kokoro voice gets no dir under the voices dir, where it would read
  as a clone.
- **Lazy**: the first line of a voice with no ``measured_lufs`` measures what
  that voice has already said: the mouth keeps every line (``audio.py``), so
  no extra synthesis and no added delay beyond one ffmpeg ``ebur128`` pass
  (~0.2 s, once). It needs MEASURE_MIN_S of the voice; with less, the line
  plays unlevelled and the voice is measured again after the line is kept.
- **The gain**: measured, ``normalize_lufs - measured_lufs`` (so a new target
  applies with no re-measure; ``gain_db`` in the file is what it was then);
  hand, ``gain_db`` as written, never re-measured, so a tuning sticks.
  Clamped to +-GAIN_LIMIT_DB. A ``gain_db`` the mouth did not write is a
  hand's (``_hand``): ``{"gain_db": 6}`` with no ``gain_from``, or
  a ``gain_from`` other than ``measured``, is ``hand``.
- **Applied per block as it streams** (``Leveler``), with a light limiter:
  +11 dB on a -9 dBFS peak would clip. A gain rider holds each block's peak
  at or under KNEE (instant attack over a short ramp, RELEASE_DB_PER_S back
  up), and a soft knee keeps every sample under CEILING.

Never levelled: a played file (``mouth play``), the ``silence`` backend
(not kept, nothing to measure), and ``mouth render`` (the Settings
audition compares a raw clip with its raw clone; ``audio.render`` writes the
raw synthesis). An unmeasured voice plays as today, byte for
byte. Nothing here raises into the player: a failure is no levelling.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path
from typing import Optional

import numpy as np

from .paths import mouth_dir

GAIN_LIMIT_DB = 24.0
MEASURE_MIN_S = 4.0     # of the voice's kept speech before a measure counts
MEASURE_MAX_S = 60.0    # newest first, at most this much is measured
RETRY_S = 60.0          # an unmeasurable voice is not looked at again for this long
SILENT_LUFS = -60.0     # integrated loudness below this is not speech
KNEE = 10 ** (-2.0 / 20)       # the gain rider holds a block's peak here
CEILING = 10 ** (-1.0 / 20)    # no sample goes over this (the soft knee)
RELEASE_DB_PER_S = 20.0
ATTACK_SAMPLES = 64

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}")
_tried: dict = {}
_lock = threading.Lock()


def voices_dir() -> Path:
    return Path(os.environ.get("VOICEMODE_VOICES_DIR") or "~/.voicemode/voices").expanduser()


def _segments(voice) -> Optional[list]:
    """The voice's path segments, or None when it can't name a directory
    (an absolute .wav path, a selector like ``alice[2]``, ``..``)."""
    if not isinstance(voice, str) or not voice.strip():
        return None
    parts = voice.strip().split("/")
    if not all(_NAME.fullmatch(p) and p not in (".", "..") for p in parts):
        return None
    return parts


def settings_path(voice, d: Optional[Path] = None) -> Optional[Path]:
    """Where VOICE's own settings live: beside a clone, else under the mouth dir."""
    parts = _segments(voice)
    if parts is None:
        return None
    clone = voices_dir().joinpath(*parts)
    if clone.is_dir():
        return clone / "settings.json"
    return (d or mouth_dir()).joinpath("voices", *parts, "settings.json")


def voice_settings(voice, d: Optional[Path] = None) -> dict:
    p = settings_path(voice, d)
    if p is None:
        return {}
    try:
        s = json.loads(p.read_text())
    except (OSError, ValueError):
        return {}
    return s if isinstance(s, dict) else {}


def _num(v) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if np.isfinite(v) else None


def _hand(vs: dict) -> bool:
    """A hand's gain: ``gain_from: hand``, or a ``gain_db`` the
    mouth did not write. The mouth only ever writes ``gain_db`` with
    ``gain_from: measured`` (``ensure_measured``), so a ``gain_db`` with no
    ``gain_from``, a null one or any other value was written by a hand."""
    return vs.get("gain_from") == "hand" or ("gain_db" in vs and vs.get("gain_from") != "measured")


def gain_db(vs: dict, target_lufs: float) -> Optional[tuple]:
    """(gain dB, 'hand' | 'measured') from a voice's settings, or None (unmeasured)."""
    if _hand(vs):
        g = _num(vs.get("gain_db"))
        src = "hand"
    else:
        m = _num(vs.get("measured_lufs"))
        g = None if m is None else target_lufs - m
        src = "measured"
    if g is None:
        return None
    return max(-GAIN_LIMIT_DB, min(GAIN_LIMIT_DB, g)), src


# -- measuring ----------------------------------------------------------------

def lufs(pcm: bytes, sample_rate: int) -> Optional[float]:
    """Integrated loudness (EBU R128) of 16-bit mono PCM, by ffmpeg; None if it can't."""
    exe = shutil.which("ffmpeg")
    if not exe or not pcm:
        return None
    cmd = [exe, "-nostdin", "-hide_banner", "-nostats", "-f", "s16le", "-ar", str(int(sample_rate)),
           "-ac", "1", "-i", "-", "-af", "ebur128", "-f", "null", "-"]
    try:
        r = subprocess.run(cmd, input=pcm, capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    found = re.findall(rb"\bI:\s+(-?\d+(?:\.\d+)?) LUFS", r.stderr)
    if r.returncode != 0 or not found:
        return None
    v = float(found[-1])
    return v if v > SILENT_LUFS else None


def _read_wav(path: Path) -> Optional[tuple]:
    try:
        with wave.open(str(path), "rb") as w:
            if w.getnchannels() != 1 or w.getsampwidth() != 2:
                return None
            return w.readframes(w.getnframes()), w.getframerate()
    except (OSError, EOFError, wave.Error):
        return None


def measure(voice: str, backend: Optional[str] = None, root: Optional[Path] = None) -> Optional[tuple]:
    """(LUFS, seconds measured) over VOICE's newest kept lines (of BACKEND, if
    given), at most MEASURE_MAX_S; None under MEASURE_MIN_S or on any failure."""
    from . import audio as _audio

    chunks, rate, total = [], None, 0.0
    for note in _audio.kept(root, voice=voice):
        if backend and note.get("backend") != backend:
            continue
        got = _read_wav(Path(note.get("wav") or ""))
        if got is None:
            continue
        data, sr = got
        if rate is None:
            rate = sr
        if sr != rate or not data:
            continue
        chunks.append(data)
        total += len(data) / 2 / sr
        if total >= MEASURE_MAX_S:
            break
    if rate is None or total < MEASURE_MIN_S:
        return None
    v = lufs(b"".join(chunks), rate)
    return None if v is None else (v, round(total, 1))


def _write(p: Path, s: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(s, indent=2) + "\n")
    os.replace(tmp, p)


def _hand_broke(p: Optional[Path]) -> bool:
    """P is there but holds no JSON object (a typo, a list, unreadable). This
    module only ever writes whole objects, atomically (``_write``), so a hand
    made it: it is never measured over or written over (SC3); the voice plays
    unlevelled until the hand fixes it."""
    if p is None:
        return False
    try:
        return not isinstance(json.loads(p.read_text()), dict)
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return True


def _settled(p: Path, vs: dict, target_lufs: float) -> bool:
    """Nothing to measure: the voice has a gain, or its file is a hand's
    (``_hand``: a hand file is never measured over, even one whose ``gain_db``
    is missing or bad: that voice plays unlevelled until the hand fixes it), or
    its file is one a hand broke (``_hand_broke``)."""
    return _hand(vs) or gain_db(vs, target_lufs) is not None or _hand_broke(p)


def ensure_measured(voice, target_lufs: float, backend: Optional[str] = None,
                    d: Optional[Path] = None, root: Optional[Path] = None,
                    now: Optional[float] = None) -> dict:
    """VOICE's settings, measured first if they have no gain yet and enough of
    the voice is kept. Written once; a hand gain is never touched. Never raises."""
    try:
        p = settings_path(voice, d)
        if p is None:
            return {}
        vs = voice_settings(voice, d)
        if _settled(p, vs, target_lufs):
            return vs
        now = time.time() if now is None else now
        key = (str(p), backend)
        with _lock:
            if now - _tried.get(key, -RETRY_S) < RETRY_S:
                return vs
            _tried[key] = now
        got = measure(voice, backend, root)
        if got is None:
            return vs
        m, secs = got
        vs = voice_settings(voice, d)   # again: a hand edit made meanwhile wins
        if _settled(p, vs, target_lufs):
            return vs
        vs = {**vs, "gain_db": round(target_lufs - m, 1), "measured_lufs": round(m, 1),
              "measured_at": _dt.datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds"),
              "measured_s": secs, "gain_from": "measured"}
        _write(p, vs)
        with _lock:
            _tried.pop(key, None)
        return vs
    except Exception:  # noqa: BLE001 - levelling must never stop the speech
        return {}


# -- the CLI: mouth gain VOICE [DB|reset] ----------------------------

def parse_gain(text) -> float:
    """A hand gain in dB from the CLI: a finite number, -GAIN_LIMIT_DB to
    +GAIN_LIMIT_DB inclusive. Out of range is refused, not clamped: the file
    should say what plays. ValueError otherwise."""
    try:
        v = float(str(text).strip())
    except ValueError:
        v = None
    if v is None or not np.isfinite(v) or abs(v) > GAIN_LIMIT_DB:
        raise ValueError(f"mouth gain: a gain is a number of dB, {-GAIN_LIMIT_DB:g} to {GAIN_LIMIT_DB:g}")
    return v


def _gain_path(voice, d: Optional[Path] = None, *, writing: bool = False) -> Path:
    """VOICE's settings path, or ValueError: a voice that names no directory, or
    (WRITING) a file a hand broke, which the mouth never writes over."""
    p = settings_path(voice, d)
    if p is None:
        raise ValueError(f"mouth gain: {voice!r} has no settings (a path or a [N] selector "
                         "names no voice directory)")
    if writing and _hand_broke(p):
        raise ValueError(f"mouth gain: {p} is not a JSON object (a hand's edit?); fix or remove it")
    return p


def gain_state(voice, d: Optional[Path] = None, cascaded: Optional[dict] = None) -> dict:
    """The ``mouth gain`` readout; writes nothing, measures nothing. The file's
    own ``gain_db``/``measured_lufs``/``measured_at``/``gain_from`` (null when
    absent), ``file`` none | ok | broken, and ``level``: the record a line of
    the voice would carry now, read through CASCADED (``voices.cascade``)."""
    from . import voices as _voices

    p = _gain_path(voice, d)
    broken = _hand_broke(p)
    vs = {} if broken else voice_settings(voice, d)
    on, on_from = _voices.normalize_setting(cascaded=cascaded, d=d)
    target, _ = _voices.lufs_setting(cascaded=cascaded, d=d)
    rec = {"on": on, "on_from": on_from, "lufs": target}
    if on:
        g = gain_db(vs, target)
        if g is None:
            rec["gain_db"], rec["from"] = None, "hand" if _hand(vs) or broken else "unmeasured"
        else:
            rec["gain_db"], rec["from"] = round(g[0], 1), g[1]
    return {"voice": voice, "settings": str(p),
            "file": "broken" if broken else "ok" if p.exists() else "none",
            **{k: vs.get(k) for k in ("gain_db", "measured_lufs", "measured_at", "gain_from")},
            "level": rec}


def set_gain(voice, db: float, d: Optional[Path] = None) -> dict:
    """Write a hand gain: ``gain_db`` DB and ``gain_from: hand``, the file's other
    keys kept, atomically. ValueError for no path or a broken file."""
    p = _gain_path(voice, d, writing=True)
    vs = {**voice_settings(voice, d), "gain_db": db, "gain_from": "hand"}
    _write(p, vs)
    return vs


def reset_gain(voice, target_lufs: float, d: Optional[Path] = None) -> dict:
    """Back to measured. A hand file with a kept ``measured_lufs`` gets
    ``gain_from: measured`` and the gain ``ensure_measured`` would write; one
    with none loses ``gain_db``/``gain_from``, so its next line measures it.
    No file, or not a hand's: nothing is written. ValueError as ``set_gain``."""
    p = _gain_path(voice, d, writing=True)
    vs = voice_settings(voice, d)
    if not p.exists() or not _hand(vs):
        return vs
    m = _num(vs.get("measured_lufs"))
    if m is not None:
        vs = {**vs, "gain_db": round(target_lufs - m, 1), "gain_from": "measured"}
    else:
        vs = {k: v for k, v in vs.items() if k not in ("gain_db", "gain_from")}
    _write(p, vs)
    return vs


def forget_tries() -> None:
    """Tests: let every voice be measured again at once."""
    with _lock:
        _tried.clear()


# -- applying -----------------------------------------------------------------

class Leveler:
    """A fixed gain on each block, then a light limiter; float32 in and out."""

    def __init__(self, gain_db: float, sample_rate: int) -> None:
        self.gain_db = float(gain_db)
        self.g = float(10 ** (self.gain_db / 20))
        self.sr = int(sample_rate)
        self.r = 1.0          # the rider's gain reduction now, <= 1
        self.limited = 0      # blocks the rider turned down

    def apply(self, x: np.ndarray) -> np.ndarray:
        y = x.astype(np.float32, copy=True)
        if not len(y):
            return y
        y *= np.float32(self.g)
        peak = float(np.max(np.abs(y)))
        want = min(1.0, KNEE / peak) if peak > 0 else 1.0
        if want < self.r:      # attack: down at once, over a short ramp
            n = min(ATTACK_SAMPLES, len(y))
            env = np.full(len(y), want, dtype=np.float32)
            env[:n] = np.linspace(self.r, want, n, dtype=np.float32)
            self.limited += 1
        else:                  # release: back up no faster than RELEASE_DB_PER_S
            up = 10 ** (RELEASE_DB_PER_S * len(y) / self.sr / 20)
            want = min(want, self.r * up, 1.0)
            env = None if self.r == want == 1.0 else np.linspace(self.r, want, len(y), dtype=np.float32)
        if env is not None:
            y *= env
        self.r = want
        a = np.abs(y)
        over = a > KNEE
        if over.any():         # the soft knee: (KNEE, inf) -> (KNEE, CEILING)
            span = CEILING - KNEE
            y[over] = np.sign(y[over]) * (KNEE + span * np.tanh((a[over] - KNEE) / span))
        return y


def for_line(item: dict, backend_name: str, sample_rate: int, d: Optional[Path] = None,
             root: Optional[Path] = None) -> tuple:
    """(Leveler or None, the line's ``level`` record or None) for a line about to play.

    The record says what levelling did: ``{"on", "on_from", "lufs", "gain_db",
    "from"}``, ``on_from`` where ``on`` came from (as ``volume_from``), ``from``
    'hand' | 'measured' | 'unmeasured' (absent when off); None when the line is
    one the mouth never levels (a played file, the silence backend, a voice
    that names no directory). Never raises.

    A say-again replay (``again_of``) plays its line's kept WAV,
    which is the voice's raw synthesis, kept before levelling. So it is
    levelled as its voice is, measured under the backend that made it
    (``backend_asked``), not under ``file``, the decoder that plays it. A
    replay re-synthesised from its text (no ``file``) is measured under the
    backend that ran, as any line is: its ``backend_asked`` may be ``auto``.
    """
    from . import audio as _audio
    from . import voices as _voices

    try:
        if item.get("again_of") and item.get("file"):   # the kept WAV, not a re-synthesis
            backend_name = item.get("backend_asked") or backend_name
        if _voices.played_file(item) or backend_name in _audio.NOT_KEPT_BACKENDS:
            return None, None
        voice = item.get("voice")
        if settings_path(voice, d) is None:
            return None, None
        on, on_from = _voices.normalize_setting(item=item, d=d)
        target, _ = _voices.lufs_setting(item=item, d=d)
        rec = {"on": on, "on_from": on_from, "lufs": target}
        if not on:
            return None, rec
        vs = ensure_measured(voice, target, backend_name, d=d, root=root)
        g = gain_db(vs, target)
        if g is None:   # a hand file with no usable gain, or one a hand broke, stays 'hand'
            hand = _hand(vs) or _hand_broke(settings_path(voice, d))
            return None, {**rec, "gain_db": None, "from": "hand" if hand else "unmeasured"}
        return Leveler(g[0], sample_rate), {**rec, "gain_db": round(g[0], 1), "from": g[1]}
    except Exception:  # noqa: BLE001
        return None, None


def after_kept(item: dict, backend_name: str, rec: Optional[dict], d: Optional[Path] = None,
               root: Optional[Path] = None) -> None:
    """A line of an unmeasured voice was just kept: measure the voice now, off
    the player's thread, so its next line is levelled."""
    if not rec or not rec.get("on") or rec.get("from") != "unmeasured":
        return
    forget = (str(settings_path(item.get("voice"), d)), backend_name)
    with _lock:
        _tried.pop(forget, None)
    threading.Thread(target=ensure_measured, args=(item.get("voice"), rec["lufs"], backend_name),
                     kwargs={"d": d, "root": root}, daemon=True, name="mouth-level-measure").start()
