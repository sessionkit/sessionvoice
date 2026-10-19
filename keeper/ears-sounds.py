#!/usr/bin/env python3
"""ears-sounds: play a short sound in the user's ears when the ears log an event.

Audio indicators: a sound for events, to help the user follow how the
ears perform.
A sound plays when a turn happens, and another, which the user can
enable or disable, when a partial is sent. Each kind is its own
switch.

A watcher, NOT built into the ears: it follows the heard
log and, for each line whose kind is switched on in the settings file,
plays that kind's sound. Kinds: `turn`, `partial`, `saying`, `said`, and an
event as `event:NAME` (heartbeat, device-change, turn-discarded,
listen-started, listen-stopped).

SETTINGS, ears-sounds.json beside this file (--settings), reread live on
every change: per-service notification settings, switch a sound
on or off with no restart. Keys: `sounds` {kind: {on, freq, ms, volume}},
`to` [outputs], `pair` bool.

WHERE it plays (the default output, unless it is told to go to
certain devices when they are
available):
  - `to` names outputs, first present wins (presenting: the AirPods, never
    the room); else
  - `pair` true: the output paired with the line's mic (`map` IN=OUT, the
    same name, or Microphone -> Speakers) - and a CALL's mic (`call:N`) is
    paired with that call: the sound is mixed into it, so the tick for the user's
    voice on the phone plays in the phone (sounds for the user's voice, while the user
    is on a call, play
    back through the call). A call that can't take it (no bot, a bot from
    before sounds, the call just ended) plays at the default instead, the
    way it did before calls were paired; else
  - the default output.
PortAudio reads the outputs, and which one is the default, ONCE (at import), so
before each sound it asks CoreAudio for the current default (~0.1 ms) and rescans
when that changed, or when a `to` output is absent (it may have arrived since).
Without it, after AirPods -> Car -> speakers -> AirPods, every sound went
to the stale default with no error, 557 of them, until a restart.
Each record names the `device` PortAudio opened.

ONE output stream, held open (kept open between sounds, not opened
for each one): the first local sound opens it,
and every sound after is written into it, where before each one opened and
closed its own (sd.play). It is closed and opened again only when the device
changes: a `to` output, the paired output, or the default moving (a rescan
closes it first, since PortAudio's terminate takes every stream with it). It is
closed while no sound is on, or the global switch is off, and opened again by
the next sound. Each record carries `opens`, the streams opened so far by this
process. A call (`call:N`) is mixed by the mouth as before; it opens nothing here.

Every service writes a local log: one JSON line per sound
to ears-sounds.jsonl (--sound-log), with the lag from the event's own
timestamp and the play time, so we can see how it performs.

Runs until killed, or until --stop-file exists. Follows the log across
midnight (heard_YYYY-MM-DD.jsonl rolls; partial-wake once stuck on
yesterday's). Spike-grade, beside partial-wake in keeper/ (it
was turn-tick.py for its first 8 minutes); moves into the notifications
project as a verb once the user has lived with it. Run with the spike's venv
(sounddevice, numpy).
"""
import argparse, ctypes, ctypes.util, json, os, shlex, subprocess, sys, time
from datetime import date, datetime
from pathlib import Path

import numpy as np
import sounddevice as sd

from held_stream import HeldStream   # beside this file (the script's dir is on sys.path)

#: The voice settings folder under $HOME: a copy of ears.home.SETTINGS (keeper/ imports
#: nothing from src/; tests/test_settings_dir.py holds every copy to it).
VOICE_SETTINGS = ".sessionkit/voice/settings"


def sessionvoice_home():
    """sessionvoice's home: a copy of ears.home.sessionvoice_home (tests/test_home.py holds
    it). $SESSIONVOICE_HOME, $VOICEMODE_BASE_DIR, ~/.sessionvoice or ~/.voicemode if it
    holds the heard log, else ~/.sessionvoice."""
    import glob, os
    for name in ("SESSIONVOICE_HOME", "VOICEMODE_BASE_DIR"):
        if os.environ.get(name):
            return os.path.expanduser(os.path.expandvars(os.environ[name]))
    for d in ("~/.sessionvoice", "~/.voicemode"):
        c = os.path.expanduser(d + "/logs/conversations")
        if os.path.exists(c + "/heard.lock") or glob.glob(glob.escape(c) + "/heard_*.jsonl"):
            return os.path.expanduser(d)
    return os.path.expanduser("~/.sessionvoice")


LOGDIR = Path(os.environ.get("EARS_SOUNDS_HEARD_DIR") or Path(sessionvoice_home()) / "logs/conversations")
# In a row, then exit 1 so launchd's KeepAlive starts a fresh process. The night of Tue
# 2030-06-09 it logged 599 failures in a row (-9986) and never exited, so KeepAlive never fired.
MAX_FAILS = int(os.environ.get("EARS_SOUNDS_MAX_FAILS") or 20)
HERE = Path(__file__).resolve().parent

ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
ap.add_argument("--settings", default=os.environ.get("EARS_SOUNDS_SETTINGS") or str(Path.home() / VOICE_SETTINGS / "ears-sounds.json"))
ap.add_argument("--sound-log", default=os.environ.get("EARS_SOUNDS_LOG") or str(Path.home() / "Library/Logs/ears/ears-sounds.jsonl"))
ap.add_argument("--stop-file", default=str(HERE / "STOP-ears-sounds"))
ap.add_argument("--test", metavar="KIND", help="play KIND's sound once, where it would go, and exit")
ap.add_argument("--mic", metavar="DEVICE", help="with --test: as if the line came from DEVICE (e.g. call:N)")
a = ap.parse_args()

_settings, _mtime = {}, None


def settings() -> dict:
    """The settings file, reread when it changes; the last good copy on a bad edit."""
    global _settings, _mtime
    try:
        m = os.stat(a.settings).st_mtime
        if m != _mtime:
            with open(a.settings) as f:
                _settings = json.load(f)
            _mtime = m
            on = sorted(k for k, v in _settings.get("sounds", {}).items() if v.get("on"))
            print(f"ears-sounds: settings loaded, on: {on}, to: {_settings.get('to') or 'default'}, "
                  f"pair: {bool(_settings.get('pair'))}", flush=True)
    except (OSError, ValueError) as x:
        if _mtime != "bad":
            print(f"ears-sounds: settings unreadable ({x}); keeping the last good copy", flush=True)
            _mtime = "bad"
    return _settings


GLOBAL = Path(os.environ.get("NOTIFY_GLOBAL") or Path.home() / ".settings/notifications/global.json")
_g, _gm = True, None


def global_allow() -> bool:
    """notify's one global switch (the Notifications webxdc app), reread on change."""
    global _g, _gm
    try:
        m = GLOBAL.stat().st_mtime
        if m != _gm:
            _g, _gm = json.loads(GLOBAL.read_text()).get("allow", True) is not False, m
            print(f"ears-sounds: global allow {_g}", flush=True)
    except (OSError, ValueError):
        pass
    return _g


def outputs() -> list[str]:
    return [d["name"] for d in sd.query_devices() if d["max_output_channels"] > 0]


_ca = None


def _coreaudio_default() -> tuple[int, str]:
    """(device id, name) of the system default output, from CoreAudio (~0.1 ms, no fork).
    The id changes when a device is re-added (AirPods back with the same name)."""
    global _ca
    if _ca is None:
        class Addr(ctypes.Structure):
            _fields_ = [("sel", ctypes.c_uint32), ("scope", ctypes.c_uint32), ("elem", ctypes.c_uint32)]
        lib = ctypes.CDLL(ctypes.util.find_library("CoreAudio"))
        lib.AudioObjectGetPropertyData.argtypes = [ctypes.c_uint32, ctypes.POINTER(Addr), ctypes.c_uint32,
                                                   ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
        lib.AudioObjectGetPropertyData.restype = ctypes.c_int32
        cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
        cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
        cf.CFStringGetCString.restype = ctypes.c_bool
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        _ca = (Addr, lib, cf)
    Addr, lib, cf = _ca

    def get(obj, sel, out):  # global scope, main element
        n = ctypes.c_uint32(ctypes.sizeof(out))
        r = lib.AudioObjectGetPropertyData(obj, Addr(int.from_bytes(sel, "big"), int.from_bytes(b"glob", "big"), 0),
                                           0, None, ctypes.byref(n), ctypes.byref(out))
        if r:
            raise OSError(f"AudioObjectGetPropertyData {sel.decode()} -> {r}")
    dev, ref = ctypes.c_uint32(), ctypes.c_void_p()
    get(1, b"dOut", dev)          # kAudioObjectSystemObject, kAudioHardwarePropertyDefaultOutputDevice
    get(dev.value, b"lnam", ref)  # kAudioObjectPropertyName: a CFString we own
    buf = ctypes.create_string_buffer(512)
    ok = cf.CFStringGetCString(ref, buf, len(buf), 0x08000100)  # kCFStringEncodingUTF8
    cf.CFRelease(ref)
    return dev.value, buf.value.decode() if ok else ""


def default_output() -> tuple[int | None, str] | None:
    """(id, name) of the system's CURRENT default output; None if it can't be read
    (then nothing is rescanned on its account). EARS_SOUNDS_DEFAULT_CMD, for tests:
    a command whose stdout is the default's name (id None), instead of CoreAudio."""
    try:
        cmd = os.environ.get("EARS_SOUNDS_DEFAULT_CMD")
        if cmd:
            name = subprocess.run(shlex.split(cmd), capture_output=True, text=True, timeout=5).stdout.strip()
            return (None, name) if name else None
        return _coreaudio_default()
    except Exception:  # a failed query must not cost a sound
        return None


_seen = default_output()  # the default PortAudio's list was read with (at import)


def follow_default() -> None:
    """Rescan when the system default moved since PortAudio last read the devices."""
    global _seen
    now = default_output()
    if now is None:
        return
    if _seen is None:
        _seen = now
    elif now != _seen:
        print(f"ears-sounds: default output {_seen[1]!r} -> {now[1]!r} - rescanning the devices", flush=True)
        reinit()


def route(mic: str | None) -> tuple[str | None, str]:
    """(output name, or None for the default output; why)."""
    s, outs = settings(), outputs()
    if s.get("to") and not any(o in outs for o in s["to"]):
        reinit()          # none present in the list PortAudio read; one may have arrived since
        outs = outputs()
    present = [o for o in s.get("to", []) if o in outs]
    if present:
        return present[0], "to"
    if s.get("pair") and mic:
        if mic.startswith("call:"):
            return mic, "pair:call"   # the user's voice on a call: the tick goes back down that call
        m = s.get("map", {}).get(mic)
        if m in outs:
            return m, "pair:map"
        if mic in outs:
            return mic, "pair:same-name"
        if mic.replace("Microphone", "Speakers") in outs:
            return mic.replace("Microphone", "Speakers"), "pair:mic->speakers"
    return None, "default" + (":to-none-present" if s.get("to") else "")


def wave(sr: int, freq: float, ms: float, volume: float) -> np.ndarray:
    n = max(2, int(sr * ms / 1000))
    t = np.arange(n) / sr
    fade = min(0.004, t[-1] / 3)
    env = np.clip(np.minimum(t / fade, (t[-1] - t) / fade), 0, 1)
    return (volume * env * np.sin(2 * np.pi * freq * t)).astype(np.float32)


CALL_RATE = 48000   # what a call leg mixes at; wave() is made at it, never resampled
held = HeldStream(sd)   # the ONE local output stream


def _play(out: str | None, snd: dict, label: str = "sound") -> str:
    """Play once; the name of the device PortAudio opened, or the call (``call:N``)
    the sound was mixed into, through the mouth's call leg."""
    if out and out.startswith("call:"):
        from mouth.output import call_sound   # only on a call: the mouth's own leg to the bot
        call_sound(out, wave(CALL_RATE, snd.get("freq", 1320), snd.get("ms", 45), snd.get("volume", 0.25)),
                   CALL_RATE, label)
        return out
    dev = sd.query_devices(out, "output") if out else sd.query_devices(kind="output")
    sr = int(dev["default_samplerate"])
    held.write(out, dev["name"], sr, wave(sr, snd.get("freq", 1320), snd.get("ms", 45), snd.get("volume", 0.25)))
    return dev["name"]


def reinit() -> None:
    """Throw away PortAudio's device list and read the current one.

    PortAudio reads the device list once, at Pa_Initialize, and never again. When
    the outputs change under it (AirPods in or out, the default moved), a stream
    can fail to open, as every sound once did for an hour with
    PaErrorCode -9986 (paInternalError). A stale list is the likely cause, not a
    measured one; if a rescan doesn't cure it, MAX_FAILS hands over to launchd.
    `_terminate`/`_initialize` are sounddevice's own (private) PortAudio calls.
    Also called before a sound when the default moved (follow_default).
    It closes the held stream first: terminate would take it anyway,
    and the next sound opens one on whatever the device is now."""
    global _seen
    held.close()
    sd._terminate()
    sd._initialize()
    _seen = default_output()


def sound(mic: str | None, snd: dict, label: str = "sound") -> tuple[str | None, str, str]:
    """Follow the default, route and play once; on a PortAudio error in any, rescan the
    devices and route and play once more (route() reads the same device list).
    A call that won't take the sound falls back to the default output, and `why`
    says so. (output, why, the device opened)."""
    try:
        follow_default()
        out, why = route(mic)
        if out and out.startswith("call:"):
            try:
                return out, why, _play(out, snd, label)
            except Exception as x:   # DeviceAbsent / DeviceLost: never a lost tick, never a fail streak
                out, why = None, f"{why}->default ({type(x).__name__}: {x})"
        dev = _play(out, snd, label)
    except sd.PortAudioError as x:
        print(f"ears-sounds: {x} - rescanning the devices, one retry", flush=True)
        reinit()
        out, why = route(mic)
        if out and out.startswith("call:"):   # only a LOCAL play raises this: the call already failed over
            out, why = None, f"{why}->default (retry)"
        dev = _play(out, snd, label)
    return out, why, dev


def kind_of(e: dict) -> str:
    k = e.get("kind", "")
    return f"event:{e.get('event')}" if k == "event" else k


if a.test:
    snd = settings().get("sounds", {}).get(a.test)
    if not snd:
        sys.exit(f"ears-sounds: no sound named {a.test!r} in {a.settings}")
    out, why, dev = sound(a.mic, snd, a.test)
    held.drain()
    held.close()
    print(f"ears-sounds: test {a.test} -> {out or 'default'} ({why}), device {dev!r}, "
          f"opens {held.opens}", flush=True)
    sys.exit(0)


def open_today(at_end: bool):
    p = LOGDIR / f"heard_{date.today():%Y-%m-%d}.jsonl"
    while not p.exists():
        if os.path.exists(a.stop_file):
            sys.exit(0)
        time.sleep(1)
    f = open(p)
    if at_end:
        f.seek(0, os.SEEK_END)
    return p, f


def sounds_on() -> bool:
    """Could a line make a sound now? (any sound on, and both switches allow it)"""
    s = settings()
    return (any(v.get("on") for v in s.get("sounds", {}).values())
            and s.get("allow") is not False and global_allow())


settings()
path, f = open_today(at_end=True)
fails = 0
idle_check = 0.0
print(f"ears-sounds: following {path}, settings {a.settings}, sound-log {a.sound_log}, "
      f"stop-file {a.stop_file}, outputs {outputs()}", flush=True)
while True:
    if os.path.exists(a.stop_file):
        held.drain()
        held.close()
        print(f"ears-sounds: stop file seen, exiting (streams opened: {held.opens})", flush=True)
        sys.exit(0)
    line = f.readline()
    if not line:
        if held.stream is not None and time.monotonic() - idle_check > 1:
            idle_check = time.monotonic()
            if not sounds_on():   # blips off: hold nothing open
                held.close()
                print(f"ears-sounds: no sound on - output stream closed (streams opened: {held.opens})",
                      flush=True)
        today = LOGDIR / f"heard_{date.today():%Y-%m-%d}.jsonl"
        if today != path and today.exists():
            f.close()
            path, f = open_today(at_end=False)  # the new day: read it from its start
            print(f"ears-sounds: day rolled, now following {path}", flush=True)
        time.sleep(0.05)
        continue
    try:
        e = json.loads(line)
    except ValueError:
        continue
    k = kind_of(e)
    snd = settings().get("sounds", {}).get(k)
    if not snd or not snd.get("on") or settings().get("allow") is False or not global_allow():
        continue
    mic = e.get("device")
    t0 = time.time()
    err, out, why, dev = None, None, "unrouted", None
    try:
        out, why, dev = sound(mic, snd, k)
    except Exception as x:  # a missing device must not kill the watcher
        err = f"{type(x).__name__}: {x}"
    try:
        lag = round(t0 - datetime.fromisoformat(e["ts"]).timestamp(), 3)
    except (KeyError, ValueError):
        lag = None
    rec = {"ts": datetime.now().astimezone().isoformat(timespec="milliseconds"),
           "seq": e.get("seq"), "kind": k, "mic": mic, "output": out or "default",
           "device": dev, "route": why, "lag_s": lag, "play_s": round(time.time() - t0, 3), "error": err,
           "opens": held.opens}
    with open(a.sound_log, "a") as lf:
        lf.write(json.dumps(rec) + "\n")   # the record lives here ONLY; stdout (launchd's
                                            # ears-sounds.log) gets just the lines that say something
    fails = fails + 1 if err else 0
    if fails >= MAX_FAILS:
        print(f"ears-sounds: {fails} sounds failed in a row, even after rescans - exiting 1 "
              "so launchd starts a fresh process", flush=True)
        sys.exit(1)
