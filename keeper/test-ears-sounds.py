#!/usr/bin/env python3
"""test-ears-sounds: ears-sounds.py's recovery from PortAudio errors, with a FAKE
sounddevice on PYTHONPATH - no audio plays, and nothing live is touched: a temp
heard dir (EARS_SOUNDS_HEARD_DIR), temp settings, temp sound log, own stop file.

  1. --test turn: the first open fails -9986, the rescan (_terminate/_initialize)
     cures it, the sound plays, exit 0.
  2. following a heard log: every open fails, even after rescans; after
     EARS_SOUNDS_MAX_FAILS sounds in a row it exits 1 (so launchd restarts it).
  3. a success resets the count: fail, play, fail, play never reaches 2 in a row.
  4. --test turn: the device list fails in route(), before any stream opens; the
     rescan cures that too.
  5. the sound records go to the sound log only, never stdout.
  6. the system default output changes between two plays (AirPods ->
     MacBook Pro Speakers) while PortAudio keeps its CACHED default and raises
     nothing - a measured silence (557 plays, 0 errors).
     The second play must re-resolve (re-init PortAudio) and land on the NEW
     default, and each sound record must carry the resolved device NAME.
     Hardware-free: the fake sounddevice's cached default is set at import and at
     _initialize from a file, and a fake `SwitchAudioSource` first on PATH reads
     the same file (`SwitchAudioSource -c -t output` = the current default).
     EARS_SOUNDS_DEFAULT_CMD points ears-sounds' default query (CoreAudio, live)
     at that fake, in EVERY case: no test reads the host's real default.
  7. a `to` output that arrives WITHOUT the default moving: absent -> rescan,
     still absent -> the default; it appears -> rescan -> plays there, route "to".
  8. the default query fails (prints nothing): no rescan on its account, and the
     sound still plays on PortAudio's default, no error.

ears-sounds holds ONE OutputStream and writes every sound into it:
both fakes give it a callback stream (STREAM below), noting each open and each sound
that starts in it; an "F" step in pattern mode now also kills a held stream quietly
(its callbacks stop), the way a device that went away does.

Run with the package's venv (ears-sounds imports numpy):
  $W/.venv/bin/python keeper/test-ears-sounds.py
"""
import json, os, subprocess, sys, tempfile, time
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOUNDS = HERE / "ears-sounds.py"
fails = []

STREAM = '''
import threading as _th, time as _time
import numpy as _np
class OutputStream:
    """A callback stream: a thread pumps the callback; each new sound it takes is noted."""
    def __init__(self, device=None, samplerate=None, channels=None, dtype=None, callback=None, **kw):
        self.name = _opened(device)      # the fake's own open: may raise, notes the open
        self._cb, self.active, self._stop = callback, False, _th.Event()
    def start(self):
        self.active = True
        _th.Thread(target=self._run, daemon=True).start()
    def _run(self):
        held = getattr(self._cb, "__self__", None)
        while not self._stop.is_set():
            if held is not None and held._cur is None and held._queue:
                if _dies():
                    self.active = False
                    _note("dead")
                    return
                _note(_played(self.name))
            self._cb(_np.zeros((256, 1), _np.float32), 256, None, None)
            _time.sleep(0.002)
    def stop(self):
        self._stop.set(); self.active = False
    def close(self):
        self.stop()
'''


FAKE = '''
import json, os
class PortAudioError(Exception):
    pass
_LOG = os.environ["FAKE_SD_LOG"]
def _note(what):
    with open(_LOG, "a") as f:
        f.write(what + "\\n")
def _state():
    try:
        return json.load(open(_LOG + ".state"))
    except (OSError, ValueError):
        return {"pattern": os.environ.get("FAKE_SD_PATTERN", ""), "stale": True}
def _save(s):
    json.dump(s, open(_LOG + ".state", "w"))
def query_devices(device=None, kind=None):
    d = {"name": "Fake Speakers", "max_output_channels": 2, "default_samplerate": 48000.0}
    if device is None and not kind and os.environ.get("FAKE_SD_MODE") == "route" and _state()["stale"]:
        _note("route-fail")   # the device LIST is what goes stale
        raise PortAudioError("Error querying device -1 [PaErrorCode -9986]")
    return d if (device is not None or kind) else [d]
def _step():
    s = _state()
    step, s["pattern"] = s["pattern"][:1], s["pattern"][1:]
    _save(s)
    return s, step
def _opened(device):
    s, step = _step()
    mode = os.environ.get("FAKE_SD_MODE", "stale")
    if mode == "always" or (mode == "stale" and s["stale"]) or (mode == "pattern" and step == "F"):
        _note("fail")
        raise PortAudioError("Error opening OutputStream: Internal PortAudio error [PaErrorCode -9986]")
    _note("open")
    return "Fake Speakers"
def _dies():   # pattern mode: a sound in a held stream takes a step too; F kills the stream
    return os.environ.get("FAKE_SD_MODE") == "pattern" and _step()[1] == "F"
def _played(name):
    return "play"
def _terminate():
    _note("terminate")
def _initialize():
    s = _state(); s["stale"] = False; _save(s)
    _note("initialize")
''' + STREAM


FAKE_SWITCH = '''#!/bin/sh
# fake SwitchAudioSource: -c (-t output) prints the current default output
# -a (-t output) lists both outputs; anything else (a SWITCH) is refused: the test never moves the route
case " $* " in
  *" -c "*) cat "$FAKE_SD_DEFAULT" ;;
  *" -a "*) printf 'AirPods\\nMacBook Pro Speakers\\n' ;;
  *) echo "fake SwitchAudioSource: refused: $*" >&2; exit 2 ;;
esac
'''


def check(name, ok, got=None):
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"  (got {got!r})"))
    if not ok:
        fails.append(name)


def rig(t, mode, **env):
    fake = t / "fake"
    fake.mkdir(exist_ok=True)
    (fake / "sounddevice.py").write_text(FAKE)
    (t / "settings.json").write_text(json.dumps(
        {"sounds": {"turn": {"on": True, "ms": 5}, "partial": {"on": True, "ms": 5}}}))
    heard = t / "heard"
    heard.mkdir(exist_ok=True)
    (heard / f"heard_{date.today():%Y-%m-%d}.jsonl").write_text("")
    (fake / "SwitchAudioSource").write_text(FAKE_SWITCH)   # the default query's seam
    (fake / "SwitchAudioSource").chmod(0o755)
    (t / "default-output").write_text("Fake Speakers\n")
    e = dict(os.environ, PYTHONPATH=str(fake), FAKE_SD_LOG=str(t / "sd.log"), FAKE_SD_MODE=mode,
             EARS_SOUNDS_HEARD_DIR=str(heard), NOTIFY_GLOBAL=str(t / "no-global.json"),
             PATH=f"{fake}:{os.environ.get('PATH', '')}", FAKE_SD_DEFAULT=str(t / "default-output"),
             EARS_SOUNDS_DEFAULT_CMD="SwitchAudioSource -c -t output", **env)
    args = ["--settings", str(t / "settings.json"), "--sound-log", str(t / "sounds.jsonl"),
            "--stop-file", str(t / "STOP")]
    return e, args, heard / f"heard_{date.today():%Y-%m-%d}.jsonl"


def calls(t):
    p = t / "sd.log"
    return p.read_text().split() if p.exists() else []


def feed(log, n, kind="turn"):
    with open(log, "a") as f:
        for i in range(n):
            f.write(json.dumps({"ts": "2030-06-09T22:34:56.000+10:00", "seq": i, "kind": kind}) + "\n")


with tempfile.TemporaryDirectory() as d:
    t = Path(d)
    e, args, _ = rig(t, "stale")
    r = subprocess.run([sys.executable, str(SOUNDS), *args, "--test", "turn"], env=e,
                       capture_output=True, text=True, timeout=30)
    check("1 --test: a stale list is rescanned and the sound plays (exit 0)", r.returncode == 0,
          (r.returncode, r.stderr[-300:]))
    check("1 --test: fail, terminate, initialize, open, play",
          calls(t) == ["fail", "terminate", "initialize", "open", "play"], calls(t))

with tempfile.TemporaryDirectory() as d:
    t = Path(d)
    e, args, log = rig(t, "always", EARS_SOUNDS_MAX_FAILS="3")
    p = subprocess.Popen([sys.executable, str(SOUNDS), *args], env=e,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    time.sleep(1.5)
    feed(log, 5)
    try:
        out, _ = p.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        p.kill()
        out, _ = p.communicate()
    check("2 follow: 3 failures in a row exit 1", p.returncode == 1, (p.returncode, out[-400:]))
    sounds = [json.loads(line) for line in (t / "sounds.jsonl").read_text().splitlines()]
    check("2 follow: exactly 3 sounds logged, each with its error",
          len(sounds) == 3 and all("-9986" in (s["error"] or "") for s in sounds), sounds)
    check("2 follow: each failure rescanned once", calls(t).count("initialize") == 3, calls(t))

with tempfile.TemporaryDirectory() as d:
    t = Path(d)
    # a failed open is two steps (the open, then the retry after the rescan); a sound that
    # plays in a fresh stream is two (its open, then the sound); the 3rd sound finds the
    # HELD stream dead (F: ~1 s to time out), and its reopen fails (F)
    e, args, log = rig(t, "pattern", EARS_SOUNDS_MAX_FAILS="2", FAKE_SD_PATTERN="FFPPFFPP")
    p = subprocess.Popen([sys.executable, str(SOUNDS), *args], env=e,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    time.sleep(1.5)
    feed(log, 4)   # fail, play, fail, play
    time.sleep(3.5)
    alive = p.poll() is None
    (t / "STOP").write_text("")
    try:
        p.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        p.kill()
    sounds = [json.loads(line) for line in (t / "sounds.jsonl").read_text().splitlines()]
    check("3 follow: fail, play, fail, play keeps it running", alive, sounds)
    check("3 follow: 4 sounds, errors on the 1st and 3rd only",
          [bool(s["error"]) for s in sounds] == [True, False, True, False], sounds)

with tempfile.TemporaryDirectory() as d:
    t = Path(d)
    e, args, _ = rig(t, "route")
    r = subprocess.run([sys.executable, str(SOUNDS), *args, "--test", "turn"], env=e,
                       capture_output=True, text=True, timeout=30)
    check("4 --test: a stale list in route() is rescanned, then it plays (exit 0)", r.returncode == 0,
          (r.returncode, r.stderr[-300:]))
    check("4 --test: route-fail, terminate, initialize, open, play",
          calls(t) == ["route-fail", "terminate", "initialize", "open", "play"], calls(t))

with tempfile.TemporaryDirectory() as d:
    t = Path(d)
    e, args, log = rig(t, "none")
    p = subprocess.Popen([sys.executable, str(SOUNDS), *args], env=e,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    time.sleep(1.5)
    feed(log, 2)
    time.sleep(1.0)
    (t / "STOP").write_text("")
    try:
        out, _ = p.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        p.kill()
        out, _ = p.communicate()
    logged = (t / "sounds.jsonl").read_text().splitlines()
    check("5 records: 2 in the sound log", len(logged) == 2, logged)
    check("5 records: none on stdout", '"seq"' not in out, out[-400:])

# 6. the default output moves under a live process, and PortAudio doesn't notice.
FAKE_ROUTED = '''
import json, os
class PortAudioError(Exception):
    pass
_LOG, _DEFAULT = os.environ["FAKE_SD_LOG"], os.environ["FAKE_SD_DEFAULT"]
def _devices():   # the stale device still EXISTS; FAKE_SD_DEVICES (a file, one a line) for case 7
    p = os.environ.get("FAKE_SD_DEVICES")
    return open(p).read().split("\\n")[:-1] if p else ["AirPods", "MacBook Pro Speakers"]
_DEVICES = _devices()
def _note(what):
    with open(_LOG, "a") as f:
        f.write(what + "\\n")
def _system_default():
    return open(_DEFAULT).read().strip()
_cached = _system_default() or "AirPods"   # Pa_Initialize at import reads the default once
def _dev(name):
    return {"name": name, "max_output_channels": 2, "default_samplerate": 48000.0}
def query_devices(device=None, kind=None):
    if device is not None:
        return _dev(device if isinstance(device, str) else _DEVICES[device])
    if kind:
        return _dev(_cached)    # PortAudio's CACHED default, never re-read
    return [_dev(n) for n in _DEVICES]
def _opened(device):   # no error, ever
    name = device if isinstance(device, str) else _cached
    _note("open:" + name)
    return name
def _dies():
    return False
def _played(name):
    return "play:" + name
def _terminate():
    _note("terminate")
def _initialize():
    global _cached, _DEVICES
    _cached, _DEVICES = _system_default() or _cached, _devices()
    _note("initialize:" + _cached)
''' + STREAM

def routed(default, settings=None, devices=None, change=None):
    """Two turns through ears-sounds with the routed fake: the default output starts as
    `default`; between the turns `change(t)` moves things. (plays, sd log, records, stdout)."""
    with tempfile.TemporaryDirectory() as d:
        t = Path(d)
        e, args, log = rig(t, "none")
        (t / "fake" / "sounddevice.py").write_text(FAKE_ROUTED)
        (t / "default-output").write_text(default)
        if settings:
            (t / "settings.json").write_text(json.dumps(settings))
        if devices is not None:
            (t / "devices").write_text("".join(n + "\n" for n in devices))
            e["FAKE_SD_DEVICES"] = str(t / "devices")
        p = subprocess.Popen([sys.executable, str(SOUNDS), *args], env=e,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        time.sleep(1.5)
        feed(log, 1)
        time.sleep(1.0)
        if change:
            change(t)
        feed(log, 1)
        time.sleep(1.0)
        (t / "STOP").write_text("")
        try:
            out, _ = p.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
            out, _ = p.communicate()
        sd_log = (t / "sd.log").read_text().splitlines() if (t / "sd.log").exists() else []
        sl = t / "sounds.jsonl"
        sounds = [json.loads(line) for line in sl.read_text().splitlines()] if sl.exists() else []
        return [x.split(":", 1)[1] for x in sd_log if x.startswith("play:")], sd_log, sounds, out


def between(sd_log):
    """The sd log lines between the first play and the second."""
    at = [i for i, x in enumerate(sd_log) if x.startswith("play:")] + [len(sd_log)] * 2
    return sd_log[at[0] + 1:at[1]]


plays, sd_log, sounds, out = routed(
    "AirPods\n", change=lambda t: (t / "default-output").write_text("MacBook Pro Speakers\n"))
check("6 route change: two plays, neither raised", len(plays) == 2 and len(sounds) == 2
      and not any(s["error"] for s in sounds), (plays, sounds, out[-400:]))
check("6 route change: the first play on AirPods (the default at start)", plays[:1] == ["AirPods"], plays)
check("6 route change: PortAudio re-initialised between the two plays",
      any(x.startswith("initialize:") for x in between(sd_log)), sd_log)
check("6 route change: the second play on the NEW default, MacBook Pro Speakers",
      plays[1:2] == ["MacBook Pro Speakers"], sd_log)
check("6 route change: the sound log names the resolved device per play",
      [s.get("device") or s.get("output") for s in sounds] == ["AirPods", "MacBook Pro Speakers"], sounds)
check("6 route change: no rescan before the first play (the default hadn't moved)",
      sd_log[:2] == ["open:AirPods", "play:AirPods"], sd_log)
check("6 route change: two opens, one per device (the stream follows the move)",
      [x for x in sd_log if x.startswith("open:")] == ["open:AirPods", "open:MacBook Pro Speakers"], sd_log)
check("6 route change: one line on stdout names the move", "'AirPods' -> 'MacBook Pro Speakers'" in out, out[-400:])

S7 = {"sounds": {"turn": {"on": True, "ms": 5}}, "to": ["Car"]}
plays, sd_log, sounds, out = routed(
    "AirPods\n", S7, ["AirPods", "MacBook Pro Speakers"],
    change=lambda t: (t / "devices").write_text("AirPods\nCar\nMacBook Pro Speakers\n"))
check("7 to arrives: the first play on the default (Car absent, even after a rescan)",
      plays[:1] == ["AirPods"] and sounds[:1] and sounds[0]["route"] == "default:to-none-present"
      and sd_log[:1] == ["terminate"], (sd_log, sounds))
check("7 to arrives: rescanned, then the second play on Car, route to",
      plays[1:2] == ["Car"] and sounds[1:2] and sounds[1]["route"] == "to"
      and "terminate" in between(sd_log), (sd_log, sounds))
check("7 to arrives: the device field names both",
      [s.get("device") for s in sounds] == ["AirPods", "Car"], sounds)

plays, sd_log, sounds, out = routed("\n")   # the query answers nothing (fake: AirPods cached)
check("8 query fails: both play on PortAudio's default, ONE stream, no rescan, no error",
      sd_log == ["open:AirPods", "play:AirPods", "play:AirPods"] and len(sounds) == 2
      and not any(s["error"] for s in sounds), (sd_log, sounds, out[-300:]))

print("ALL GREEN" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
