"""The mouth's default voice, and the voices it can speak.

The Settings app picks the default voice: the one a line gets
when it names no voice of its
own.

A line's voice is the first of these that answers:

1. the line's own: ``say --voice``, or a mail's ``X-Mouth-Voice``;
2. the caller's session layer: ``mouth.voice`` in
   ``~/.session/sessions/<sid>/settings/mouth.json`` (or ``mouth.d/``);
3. the caller's seat layer: ``~/.session/seats/<seat>/settings/mouth.json``;
4. the caller's agent layer: ``settings: {mouth.voice: ...}`` in its agent
   file. Steps 2-4 are the settings cascade above the host, as
   ``session settings --json <sid> mouth`` merges them;
5. ``$MOUTH_VOICE``, in the CALLER's environment. It is
   skipped when the host (or the template under it) locks ``voice``:
   ``_locked: ["voice"]`` in the host file, or in the cascade's ``locked``;
6. ``voice`` in the settings file (the host layer), which the phone's
   Settings app writes: ``$MOUTH_SETTINGS``, else
   ``~/.sessionkit/voice/settings/mouth.json`` when it exists, else the old
   ``<mouth dir>/settings.json``;
7. ``af_sky``, the founding voice (``~/.voicemode/voices/FAVORITES.md``).

Steps 2-5 run in the CALLER, when ``say`` queues the line: the caller has the
seat's environment and session id, and the voice rides on the queued job.
The player never guesses whose session a line belongs to: the caller
names it.

Step 5 is the caller's environment, never the mouth's own. A mailed line with
no ``X-Mouth-Voice``, and a cut line the player replays, are resolved in the
PLAYER, whose environment may be whichever seat started it
(``ensure_player``). There the order is the box's: the host file, then
``$MOUTH_VOICE``, then ``af_sky``. So the mouth's own environment
never beats the Settings app (the same rule as for
``$MOUTH_BARGE_WORDS``). ``default_voice(caller=True)`` asks for the
caller's order; the default is the box's.

The files are read for every line, so a pick applies to the next line queued.
A missing or unreadable file, or no ``session`` command, is not an error: it
just doesn't answer.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Optional

from ears import envnames
from ears.home import settings_dir

from .paths import mouth_dir

BUILTIN = "af_sky"

# Kokoro-82M v1.0's English voices: what the mlx-audio service on 8890
# speaks. It has no /audio/voices to ask, so the list lives here.
KOKORO = [
    "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore", "af_nicole",
    "af_nova", "af_river", "af_sarah", "af_sky",
    "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael", "am_onyx",
    "am_puck", "am_santa",
    "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
    "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
]


#: The mouth's settings file, in the voice settings folder (``ears.home.settings_dir``),
#: the home the schema and the Settings app use.
SETTINGS_FILE = "mouth.json"


def home_settings_path() -> Path:
    """``~/.sessionkit/voice/settings/mouth.json``: ``~`` is ``$HOME``, read on every
    call (like ``backends.tts_key_path``, its neighbour)."""
    return settings_dir() / SETTINGS_FILE


def settings_path(d: Optional[Path] = None) -> Path:
    """The host layer's file: ``$MOUTH_SETTINGS``; else
    ``~/.sessionkit/voice/settings/mouth.json`` when it exists; else the old
    ``<d or mouth dir>/settings.json``.

    The new home is checked even when ``d`` is passed: ``mouth.say`` always
    passes the mouth dir explicitly, so a "``d is None`` only" rule would
    never reach it."""
    env = envnames.get("MOUTH_SETTINGS")
    if env:
        return Path(env).expanduser()
    home = home_settings_path()
    return home if home.exists() else (d or mouth_dir()) / "settings.json"


def settings(d: Optional[Path] = None) -> dict:
    try:
        s = json.loads(settings_path(d).read_text())
    except (OSError, ValueError):
        return {}
    return s if isinstance(s, dict) else {}


#: The cascade's layers that outrank the host file, highest first. The host
#: layer itself is read as the settings file below, as it always was.
CASCADE = ("session", "seat", "agent")

#: The `session` command; "" turns the cascade off (the tests do, so they
#: never read this machine's seats and sessions).
SESSION_BIN_ENV = "MOUTH_SESSION_BIN"

#: The layers at or under the host file: a lock there pins the host's pick
#: over the caller's environment. The mouth ships no template.
HOST_LAYERS = ("host", "template")

#: ``cascade()``'s reserved entry: ``(keys, "host")``, the asked-for keys that
#: the host (or template) layer locks. sessiontools reserves ``_locked`` as a
#: meta key, so no setting can take the name, and every reader looks its keys
#: up by name, so none of them sees this entry.
HOST_LOCKS = "_locked"


def cascade(sid: Optional[str], keys) -> dict:
    """``{key: (value, layer)}`` for each of ``keys`` that the session, seat or
    agent layer sets, in ONE ``session settings --json <sid> mouth`` run.

    A key that nothing above the host sets is absent; so is every key when there
    is no session id, or ``session settings`` can't be run or read. The values
    are as the cascade merged them, unchecked: each reader checks its own.
    When the host (or template) layer locks any of ``keys``, they ride under
    ``HOST_LOCKS`` as ``(keys, "host")``.
    Never raises. (It generalises ``cascade_voice``.)
    """
    if not sid:
        return {}
    import shutil
    import subprocess  # here, not at the top: importing mouth stays cheap (test_mouth_timeline_cli)

    exe = envnames.get(SESSION_BIN_ENV)
    if exe is None:
        exe = shutil.which("session")
    if not exe:
        return {}
    try:
        r = subprocess.run([exe, "settings", "--json", sid, "mouth"],
                           capture_output=True, text=True, timeout=5, check=False)
        tool = json.loads(r.stdout)["tools"]["mouth"]
        values, origins = tool["values"], tool["origins"]
        layers = {s.get("path"): s.get("layer") for s in tool.get("sources", [])}
        locked = tool.get("locked") or {}
        out = {}
        for k in keys:
            if k in values:
                layer = layers.get(origins.get(k))
                if layer in CASCADE:
                    out[k] = (values[k], layer)
        pinned = tuple(k for k in keys if layers.get(locked.get(k)) in HOST_LAYERS)
        if pinned:
            out[HOST_LOCKS] = (pinned, "host")
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, AttributeError):
        return {}
    return out


def cascade_voice(sid: Optional[str], cascaded: Optional[dict] = None) -> Optional[tuple[str, str]]:
    """(voice, layer) when the session, seat or agent layer sets ``mouth.voice``.

    None when nothing above the host sets it, there is no session id, or
    ``session settings`` can't be run or read. Never raises. ``cascaded`` is a
    ``cascade()`` answer already read, so one ``session`` run serves a line.
    """
    c = (cascade(sid, ("voice",)) if cascaded is None else cascaded).get("voice")
    if c and isinstance(c[0], str) and c[0].strip():
        return c[0].strip(), c[1]
    return None


def host_locks(s: dict) -> frozenset:
    """The keys a settings file locks: its ``_locked``, when that is a list of
    strings (sessiontools refuses any other shape). Anything else is no lock."""
    locks = s.get("_locked")
    if isinstance(locks, list) and all(isinstance(x, str) for x in locks):
        return frozenset(locks)
    return frozenset()


def default_voice(d: Optional[Path] = None, sid: Optional[str] = None,
                  cascaded: Optional[dict] = None, *, caller: bool = False) -> tuple[str, str]:
    """(voice, where it came from: 'session' | 'seat' | 'agent' | 'settings' | 'env' | 'builtin').

    ``sid`` is the caller's session: give it to read the cascade above the host.
    ``cascaded``: a ``cascade()`` answer already read (no second ``session`` run).

    ``caller``: resolve for a CALLER's own line (``say`` queueing it, ``mouth
    voices``), where ``$MOUTH_VOICE`` outranks the host file unless the
    host locks ``voice``. Off by default: the box's order, host file first, which
    is what the player uses for a line it resolves itself (the module docstring).
    """
    if cascaded is None:
        cascaded = cascade(sid, ("voice",))
    c = cascade_voice(sid, cascaded)
    if c:
        return c
    host = settings(d)
    env = envnames.get("MOUTH_VOICE", "").strip()
    if caller:
        locked = "voice" in host_locks(host) or "voice" in cascaded.get(HOST_LOCKS, ((), ""))[0]
        if env and not locked:
            return env, "env"
        env = ""   # the caller's env never answers below the host file
    v = host.get("voice")
    if isinstance(v, str) and v.strip():
        return v.strip(), "settings"
    if env:
        return env, "env"
    return BUILTIN, "builtin"


def clones() -> list[str]:
    """The clone voices under ~/.voicemode/voices ($VOICEMODE_VOICES_DIR), read fresh."""
    from .resolver import load_profiles

    return sorted(load_profiles())


def voices(d: Optional[Path] = None, sid: Optional[str] = None) -> dict:
    """What `mouth voices` prints: the default (for session ``sid``), where it came from, and every voice."""
    v, src = default_voice(d, sid, caller=True)   # what the caller's next bare `say` would speak
    return {"default": v, "from": src, "settings": str(settings_path(d)),
            "kokoro": list(KOKORO), "clone": clones()}


# -- the line's level and the music's duck -------
#
# Flat keys in the mouth's settings (the cascade merges keys; a nested object
# would merge whole). Resolution, highest first: the line's own (a flag, or a
# mail header), the caller's cascade (session, seat, agent), the host file, the
# built-in. A bad value is skipped and the next layer answers; nothing raises.
# ``session settings`` hands back only the merged value, so a bad value in the
# cascade falls to the host file, not to the layer just under it.

#: The keys a line reads from the cascade: one ``session settings`` run for all.
LINE_KEYS = ("voice", "volume", "play_volume", "music_duck", "music_duck_level",
             "music_duck_restore_s", "normalize", "normalize_lufs", "lead_s")

BUILTIN_VOLUME = 100
BUILTIN_DUCK = {"music_duck": True, "music_duck_level": 20, "music_duck_restore_s": 1.0}
#: voices levelled to one loudness (``level.py``), on unless turned off.
BUILTIN_NORMALIZE = {"normalize": True, "normalize_lufs": -16.0}
#: seconds of a line's AUDIO the player buffers before its first
#: frame. 0 (the default, for every backend) plays a line as it streams. A
#: clone (Qwen3-TTS via mlx-audio) renders at only 0.93-0.96x of the time it
#: plays for on the Air, even idle (measured), so a
#: clone line started on its first 0.3 s chunk chops on any stall. A 2-3 s
#: lead covers every stall measured but one 90 s starve: a seat that wants
#: no chop sets ``lead_s`` to 2.5 (``mouth say --lead 2.5`` for one line).
BUILTIN_LEAD_S = 0.0


def _pct(v) -> Optional[int]:
    """An integer 0-100 (a whole float or a numeric string counts), else None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, str):
        v = v.strip()
        try:
            v = float(v) if v else None
        except ValueError:
            return None
    if not isinstance(v, (int, float)) or not math.isfinite(v) or v != int(v):
        return None
    v = int(v)
    return v if 0 <= v <= 100 else None


def pct(v, what: str) -> Optional[int]:
    """A line's own 0-100 (``--volume``, ``--duck-level``): None stays None; else
    an integer 0-100, or ValueError before anything is written."""
    if v is None:
        return None
    n = _pct(v)
    if n is None:
        raise ValueError(f"mouth: {what} is 0-100")
    return n


def _secs(v) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
        return None
    return float(v)


def _flag(v) -> Optional[bool]:
    return v if isinstance(v, bool) else None


def _lufs(v) -> Optional[float]:
    """A loudness target, -40..-5 LUFS."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v) if -40.0 <= v <= -5.0 else None


_CHECK = {"volume": _pct, "play_volume": _pct, "music_duck": _flag,
          "music_duck_level": _pct, "music_duck_restore_s": _secs,
          "normalize": _flag, "normalize_lufs": _lufs, "lead_s": _secs}


def _resolve(key: str, cascaded: dict, host: dict) -> tuple[object, str]:
    """(value, layer) for ``key`` below the line's own: cascade, host, built-in.
    ``(None, 'unset')`` when no layer sets a good value and there is no built-in."""
    check = _CHECK[key]
    if key in cascaded:
        v = check(cascaded[key][0])
        if v is not None:
            return v, cascaded[key][1]
    v = check(host.get(key))
    if v is not None:
        return v, "settings"
    if key == "volume":
        return BUILTIN_VOLUME, "builtin"
    if key in BUILTIN_DUCK:
        return BUILTIN_DUCK[key], "builtin"
    if key in BUILTIN_NORMALIZE:
        return BUILTIN_NORMALIZE[key], "builtin"
    if key == "lead_s":
        return BUILTIN_LEAD_S, "builtin"
    return None, "unset"


def volume_setting(*, file: bool, flag: Optional[int] = None, cascaded: Optional[dict] = None,
                   d: Optional[Path] = None) -> tuple[int, str]:
    """(volume, volume_from) for a line (``volume``) or a played file
    (``play_volume``, which follows ``volume`` when no layer sets it).

    ``flag`` is the line's own, already checked (``pct``). ``volume_from`` is
    'flag' | 'session' | 'seat' | 'agent' | 'settings' | 'builtin'.
    """
    if flag is not None:
        return flag, "flag"
    cascaded, host = cascaded or {}, settings(d)
    if file:
        v, layer = _resolve("play_volume", cascaded, host)
        if v is not None:
            return v, layer
    return _resolve("volume", cascaded, host)


def duck_setting(*, on: Optional[bool] = None, level: Optional[int] = None,
                 cascaded: Optional[dict] = None, d: Optional[Path] = None,
                 none_if_builtin: bool = False) -> Optional[dict]:
    """A played file's duck: ``{"on", "level", "restore_s", "from"}``.

    ``on`` and ``level`` are the line's own (``mouth play --duck/--no-duck
    --duck-level N``), already checked; None reads the layers. ``from`` says
    where ``on`` came from: 'flag' | 'session' | 'seat' | 'agent' | 'settings'
    | 'builtin'. ``none_if_builtin``: None when no layer above the built-in
    set any of the three (the player fills it in when it takes the line).
    """
    cascaded, host = cascaded or {}, settings(d)
    if on is None:
        on, src = _resolve("music_duck", cascaded, host)
    else:
        src = "flag"
    lsrc = "flag"
    if level is None:
        level, lsrc = _resolve("music_duck_level", cascaded, host)
    restore, rsrc = _resolve("music_duck_restore_s", cascaded, host)
    if none_if_builtin and src == lsrc == rsrc == "builtin":
        return None
    return {"on": bool(on), "level": int(level), "restore_s": float(restore), "from": src}


def _carried(item: dict, key: str) -> dict:
    """The cascade's answer for KEY that rode on the line from its caller, as
    ``cascade()`` gives it: ``{key: (value, layer)}``, or {}."""
    v, src = item.get(key), item.get(key + "_from")
    if src in ("flag", "mail", *CASCADE) and _CHECK[key](v) is not None:
        return {key: (v, src)}
    return {}


def normalize_setting(*, flag: Optional[bool] = None, item: Optional[dict] = None,
                      cascaded: Optional[dict] = None, d: Optional[Path] = None) -> tuple[bool, str]:
    """(on, from) for levelling a line's voice: the line's own
    (``--normalize/--no-normalize``), what its caller's cascade answered (on
    the item as ``normalize``/``normalize_from``), the host file, on."""
    if flag is not None:
        if not isinstance(flag, bool):
            raise ValueError("mouth: normalize is on or off")
        return flag, "flag"
    c = {**(cascaded or {}), **(_carried(item, "normalize") if item else {})}
    v, src = _resolve("normalize", c, settings(d))
    return bool(v), src


def lufs_setting(*, item: Optional[dict] = None, cascaded: Optional[dict] = None,
                 d: Optional[Path] = None) -> tuple[float, str]:
    """(target LUFS, from): the caller's cascade (on the item), the host file, -16."""
    c = {**(cascaded or {}), **(_carried(item, "normalize_lufs") if item else {})}
    v, src = _resolve("normalize_lufs", c, settings(d))
    return float(v), src


def secs(v, what: str) -> Optional[float]:
    """A line's own seconds (``--lead``): None stays None; else a finite number
    0 or more (a numeric string counts), or ValueError before anything is written."""
    if v is None:
        return None
    try:
        n = _secs(float(v.strip()) if isinstance(v, str) else v)
    except ValueError:
        n = None
    if n is None:
        raise ValueError(f"mouth: {what} is seconds, 0 or more")
    return n


def played_file(item: dict) -> bool:
    """Whether ITEM is a PLAYED FILE (``mouth play``, a mail's sound): it names
    a ``file`` (or the ``file`` backend) and is not a say-again replay.

    A replay (``again_of``) plays a line's kept WAV, but it is a
    spoken line: it stays in the speech lane (never a bed), it is levelled as
    its voice is, it takes ``volume`` (not ``play_volume``) and no duck, and
    it holds a line's lead. This is the one definition: ``bed.is_file``,
    :func:`fill_item`, :func:`lead_setting` and ``level.for_line`` all ask
    it. It lives here, below them all, because ``bed`` imports this module.
    """
    if not item or item.get("again_of"):
        return False
    return item.get("backend") == "file" or bool(item.get("file"))


def lead_setting(*, item: Optional[dict] = None, cascaded: Optional[dict] = None,
                 d: Optional[Path] = None) -> tuple[float, str]:
    """(lead seconds, from) for a line: the line's own (``mouth say
    --lead``, ``mouth.say(lead_s=)``, a mail's ``X-Mouth-Lead``), its caller's
    cascade (both ride on the item as ``lead_s``/``lead_s_from``), the host
    file, ``BUILTIN_LEAD_S`` (0). A played file never holds: ``(0, 'file')``.
    A say-again replay is a line, not a played file (:func:`played_file`)."""
    if item and played_file(item):
        return 0.0, "file"
    c = {**(cascaded or {}), **(_carried(item, "lead_s") if item else {})}
    v, src = _resolve("lead_s", c, settings(d))
    return float(v), src


def fill_item(item: dict, d: Optional[Path] = None) -> dict:
    """A line taken by the player that carries no level: fill ``volume``/
    ``volume_from``, and a file's ``duck``, from the host file and the built-in.

    Two kinds carry none: a mail from outside (no caller to read a cascade
    for), and a ``say`` whose caller found only the built-in (left off so its
    mail stays today's, byte for byte). A level given by a mail header alone
    is ``volume_from: mail``. Lines that carry them are returned as they are.
    """
    file = played_file(item)   # a say-again replay takes a line's volume, and no duck
    add = {}
    if _pct(item.get("volume")) is None:
        add["volume"], add["volume_from"] = volume_setting(file=file, d=d)
    elif not item.get("volume_from"):
        add["volume_from"] = "mail"
    if file and not isinstance(item.get("duck"), dict):
        add["duck"] = duck_setting(d=d)
    return {**item, **add} if add else item
