#!/usr/bin/env python3
"""sound-tour: name each ears and mouth sound aloud, then play it, so the user can learn them.

Which blip is which is not obvious, so this plays each one
after its name. Each name is a mouth line (af_sky, held to the user's turn end), so the mouth's
own droplet plays just before every name; the tour waits until the name has been said,
then plays the sound from the live settings files (ears-sounds.json, mouth-sounds.json).

Run with a Python that has sounddevice and numpy (the ears venv, <checkout>/.venv):
  <checkout>/.venv/bin/python keeper/sound-tour.py [--device airpods] [NAME ...]
NAMES: turn partial drop front canned bud (default: all, in that order).
"""
import json, subprocess, sys, time
from datetime import date
from pathlib import Path

import numpy as np
import sounddevice as sd

HERE = Path(__file__).resolve().parent


def voice_home():
    """ears/home.py, the ONE place voice names its state root and its log folder.
    keeper/ imports nothing from the ears package, so it loads home.py
    (standard library only) by its path, once: the checkout's src/ears/home.py (keeper/ ships in the
    checkout and the plugin only).
    Not there: exit 2, naming where it looked; there is no copy of the rule to fall back
    on."""
    mod = getattr(voice_home, "mod", None)
    if mod is None:
        import importlib.util, sys
        tried = [HERE.parent / "src/ears/home.py"]
        f = next((f for f in tried if f.is_file()), None)
        if f is None:
            print(f"sound-tour: no ears/home.py at {' nor '.join(map(str, tried))}: it names "
                  "voice's state root. Run it from a sessionvoice checkout, the plugin or the "
                  "package", file=sys.stderr)
            sys.exit(2)
        spec = importlib.util.spec_from_file_location("_voice_home", f)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        voice_home.mod = mod
    return mod


def voice_state_dir() -> Path:
    """voice's state root, as the PACKAGE answers it (ears.home.asked_state_dir, design.md
    D4.1): asked once per process, and exit 2, naming both answers, when the package cannot
    be asked or disagrees with this checkout's home.py."""
    return voice_home().asked_state_dir("sound-tour")


HEARD = Path(voice_state_dir()) / f"logs/conversations/heard_{date.today().isoformat()}.jsonl"

TOUR = [
    ("turn", "ears", "One. Ears: you finished a turn."),
    ("partial", "ears", "Two. Ears: a tick for each bit of you they hear while you talk."),
    ("drop", "mouth", "Three. The droplet: a line landed in the mouth, waiting to be spoken."),
    ("front", "mouth", "Four. The higher droplet: an opener jumped to the front."),
    ("canned", "mouth", "Five. One low note: the automatic canned opener."),
    ("bud", "mouth", "Six. Two rising notes: I woke while you were still talking."),
]


def notes_for(name, src):
    if src == "ears":
        s = json.loads((HERE / "ears-sounds.json").read_text())["sounds"][name]
        return [[s["freq"], s["ms"]]], s.get("volume", 0.1)
    s = json.loads((HERE / "mouth-sounds.json").read_text())["sounds"][name]
    return s["notes"], s.get("volume", 0.08)


def tone(sr, notes, vol):
    parts = []
    for f, ms in notes:
        n = max(2, int(sr * ms / 1000)); t = np.arange(n) / sr
        fade = min(0.004, t[-1] / 3)
        env = np.clip(np.minimum(t / fade, (t[-1] - t) / fade), 0, 1)
        parts.append(vol * env * np.sin(2 * np.pi * f * t))
    return np.concatenate(parts).astype(np.float32)


def wait_said(utt, since, timeout=45):
    """Block until the heard log says utt finished (said), or it was cut; return the kind."""
    end = time.time() + timeout
    with open(HEARD) as f:
        f.seek(since)
        while time.time() < end:
            line = f.readline()
            if not line:
                time.sleep(0.05); continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("utt") == utt and d.get("kind") == "said":
                return "cut" if d.get("cut") else "said"
    return "timeout"


def main(argv):
    device = "airpods"
    if argv[:1] == ["--device"]:
        device, argv = argv[1], argv[2:]
    names = argv or [t[0] for t in TOUR]
    for name, src, label in [t for t in TOUR if t[0] in names]:
        since = HEARD.stat().st_size if HEARD.exists() else 0
        utt = subprocess.run(["mouth", "say", "--device", device, "--voice", "af_sky",
                              "--hold", "turn-end", "--expires", "60", label],
                             capture_output=True, text=True).stdout.strip().split("\n")[-1]
        got = wait_said(utt, since)
        print(f"{name}: label {utt} {got}", flush=True)
        if got != "said":
            print("tour stopped: a name was not spoken", flush=True)
            return 1
        time.sleep(0.5)
        dev = sd.query_devices(kind="output")
        sr = int(dev["default_samplerate"])
        notes, vol = notes_for(name, src)
        sd.play(tone(sr, notes, vol), sr, blocking=True)
        print(f"{name}: played on {dev['name']}", flush=True)
        time.sleep(1.2)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
