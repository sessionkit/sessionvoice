#!/usr/bin/env python3
"""test-mouth-sounds: mouth-sounds.py follows the CURRENT default output, with a
FAKE sounddevice and a FAKE `SwitchAudioSource` first on PATH - no audio plays, nothing
live is touched: temp maildir, run dir, settings, sound log and stop file.

  1. the system default output changes between two sounds (AirPods -> MacBook Pro
     Speakers) while PortAudio keeps its CACHED default and raises nothing, as
     ears-sounds once did (557 plays, 0 errors, and the user heard none);
     mouth-sounds.py:93 has the same shape (sd.query_devices(kind="output") and a
     device-less sd.play). The second sound must re-resolve (re-init PortAudio) and
     land on the NEW default, and each record in the sound log must carry the
     resolved device NAME.

The fake sounddevice's cached default is read at import (Pa_Initialize) and again at
_initialize, from a file; `SwitchAudioSource -c -t output` prints the same file, and
MOUTH_SOUNDS_DEFAULT_CMD points mouth-sounds' default query (CoreAudio, live) at it,
so the test never reads the host's real default.
Driven by two lines landing in the mouth maildir's new/, one "drop" each.

Run with the ears venv (mouth-sounds imports numpy):
  <checkout>/.venv/bin/python keeper/test-mouth-sounds.py
"""
import json, os, subprocess, sys, tempfile, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOUNDS = HERE / "mouth-sounds.py"
fails = []

FAKE = '''
import os
class PortAudioError(Exception):
    pass
_LOG, _DEFAULT = os.environ["FAKE_SD_LOG"], os.environ["FAKE_SD_DEFAULT"]
_DEVICES = ["AirPods", "MacBook Pro Speakers"]   # the stale device still EXISTS
def _note(what):
    with open(_LOG, "a") as f:
        f.write(what + "\\n")
def _system_default():
    return open(_DEFAULT).read().strip()
_cached = _system_default()   # Pa_Initialize at import reads the default once
def _dev(name):
    return {"name": name, "max_output_channels": 2, "default_samplerate": 48000.0}
def query_devices(device=None, kind=None):
    if device is not None:
        return _dev(device if isinstance(device, str) else _DEVICES[device])
    if kind:
        return _dev(_cached)    # PortAudio's CACHED default, never re-read
    return [_dev(n) for n in _DEVICES]
def play(data, sr, device=None, blocking=False):
    _note("play:" + (device if isinstance(device, str) else _cached))   # no error, ever
def _terminate():
    _note("terminate")
def _initialize():
    global _cached
    _cached = _system_default()
    _note("initialize:" + _cached)
'''
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


with tempfile.TemporaryDirectory() as d:
    t = Path(d)
    fake, run, new = t / "fake", t / "run", t / "mouth" / "new"
    for p in (fake, run, new, t / "mouth" / "cur"):
        p.mkdir(parents=True)
    (fake / "sounddevice.py").write_text(FAKE)
    (fake / "SwitchAudioSource").write_text(FAKE_SWITCH)
    (fake / "SwitchAudioSource").chmod(0o755)
    default = t / "default-output"
    default.write_text("AirPods\n")
    (t / "settings.json").write_text(json.dumps(
        {"sounds": {"drop": {"on": True, "notes": [[1568, 5]], "volume": 0.1}}}))
    e = dict(os.environ, PYTHONPATH=str(fake), PATH=f"{fake}:{os.environ.get('PATH', '')}",
             FAKE_SD_LOG=str(t / "sd.log"), FAKE_SD_DEFAULT=str(default),
             MOUTH_MAILDIR=str(t / "mouth"), EARS_RUN=str(run),
             MOUTH_SOUNDS_FIRED=str(run / "mid-turn.fired"), MOUTH_SOUNDS_CANNED=str(run / "canned"),
             MOUTH_SOUNDS_SETTINGS=str(t / "settings.json"), MOUTH_SOUNDS_LOG=str(t / "sounds.jsonl"),
             MOUTH_SOUNDS_STOP=str(t / "STOP"), MOUTH_SOUNDS_DEFAULT_CMD="SwitchAudioSource -c -t output",
             # the scoop watch must not read the live ~/.voicemode cursors
             MOUTH_SOUNDS_CURSORS=str(t / "cursors"), MOUTH_SOUNDS_HEARD_DIR=str(t / "heard"))
    e.pop("MOUTH_SOUNDS_DRY", None)
    p = subprocess.Popen([sys.executable, str(SOUNDS)], env=e,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    time.sleep(1.5)
    (new / "1.utta.host").write_text("X-Mouth-Utt: utta\n\na line\n")
    time.sleep(1.0)
    default.write_text("MacBook Pro Speakers\n")   # the route change: AirPods -> speakers
    (new / "2.uttb.host").write_text("X-Mouth-Utt: uttb\n\na line\n")
    time.sleep(1.0)
    (t / "STOP").write_text("")
    try:
        out, _ = p.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        p.kill()
        out, _ = p.communicate()
    sd_log = (t / "sd.log").read_text().splitlines() if (t / "sd.log").exists() else []
    plays = [x.split(":", 1)[1] for x in sd_log if x.startswith("play:")]
    sl = t / "sounds.jsonl"
    sounds = [json.loads(line) for line in sl.read_text().splitlines()] if sl.exists() else []
    check("1 route change: two drops played, neither raised", len(plays) == 2 and len(sounds) == 2
          and not any(s["error"] for s in sounds), (plays, sounds, out[-400:]))
    check("1 route change: the first drop on AirPods (the default at start)",
          plays[:1] == ["AirPods"], plays)
    at = [i for i, x in enumerate(sd_log) if x.startswith("play:")] + [len(sd_log)] * 2
    check("1 route change: PortAudio re-initialised between the two drops",
          any(x.startswith("initialize:") for x in sd_log[at[0] + 1:at[1]]), sd_log)
    check("1 route change: the second drop on the NEW default, MacBook Pro Speakers",
          plays[1:2] == ["MacBook Pro Speakers"], sd_log)
    check("1 route change: no rescan before the first drop (the default hadn't moved)",
          sd_log[:1] == ["play:AirPods"], sd_log)
    named = [s.get("device") or s.get("output") for s in sounds]
    check("1 route change: the sound log names the resolved device per sound",
          named == ["AirPods", "MacBook Pro Speakers"], sounds)

print("ALL GREEN" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
