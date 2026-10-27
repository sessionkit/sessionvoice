"""A fake separator: speaks the runner's protocol (``src/mouth/_separator/run.py``) with the
standard library only, and no model.

    python fake_separator.py --out DIR --model FILE --sha256 HEX [--threads N]

It reads the s16le, 44.1 kHz stereo PCM on stdin and writes the five outputs as 16-bit
WAV (stdlib ``wave``) under DIR: ``stems/{vocals,drums,bass,other}.wav`` and
``mixes/instrumental.wav``. The vocals are half the mix and each other stem a sixth, so
the stems sum to the mix and the instrumental is half of it (6 dB below). It prints the
runner's one JSON line, with the levels measured on what it wrote. The model file is not
read.

A test sets ``MOUTH_SEPARATOR`` to ``<python> tests/fixtures/fake_separator.py``. Knobs,
each read from the environment:

- ``MOUTH_FAKE_SEPARATOR_LOG``: a file; each run appends one JSON line (``argv``,
  ``pid``, ``nice``, ``bytes``), so a test can count the runs.
- ``MOUTH_FAKE_SEPARATOR_FAIL``: write the vocals, then fail (exit 1, ``ok: false``), so a
  test sees a partial output left behind by the separator.
- ``MOUTH_FAKE_SEPARATOR_SLEEP``: seconds to sleep after reading stdin.
- ``MOUTH_FAKE_SEPARATOR_SPEECH``: speech-like levels: the instrumental is 1/100 of the
  mix (40 dB below), the vocals the rest.
"""
from __future__ import annotations

import argparse
import array
import json
import math
import os
import sys
import time
import wave

RATE, CHANNELS = 44100, 2
STEMS = ("vocals", "drums", "bass", "other")


def level_db(samples) -> float:
    if not samples:
        return -120.0
    rms = math.sqrt(sum(float(x) * x for x in samples) / len(samples)) / 32768.0
    return -120.0 if rms <= 0 else max(-120.0, round(20 * math.log10(rms), 2))


def scaled(mix, g):
    return array.array("h", (int(round(x * g)) for x in mix))


def write_wav(path, samples) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(samples.tobytes())


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--sha256", required=True)
    p.add_argument("--threads", type=int)
    args = p.parse_args()
    raw = sys.stdin.buffer.read()
    raw = raw[: len(raw) - len(raw) % (2 * CHANNELS)]
    log = os.environ.get("MOUTH_FAKE_SEPARATOR_LOG")
    if log:
        with open(log, "a") as f:
            f.write(json.dumps({"argv": sys.argv[1:], "pid": os.getpid(), "nice": os.nice(0),
                                "bytes": len(raw)}) + "\n")
    if os.environ.get("MOUTH_FAKE_SEPARATOR_SLEEP"):
        time.sleep(float(os.environ["MOUTH_FAKE_SEPARATOR_SLEEP"]))
    if not raw:
        print(json.dumps({"ok": False, "detail": "no audio on stdin"}), flush=True)
        return 2
    mix = array.array("h")
    mix.frombytes(raw)
    if sys.byteorder != "little":
        mix.byteswap()
    speech = bool(os.environ.get("MOUTH_FAKE_SEPARATOR_SPEECH"))
    gains = ({"vocals": 0.99, "drums": 0.01 / 3, "bass": 0.01 / 3, "other": 0.01 / 3}
             if speech else {"vocals": 0.5, "drums": 1 / 6, "bass": 1 / 6, "other": 1 / 6})
    outputs = {**{s: f"stems/{s}.wav" for s in STEMS}, "instrumental": "mixes/instrumental.wav"}
    parts = {s: scaled(mix, gains[s]) for s in STEMS}
    write_wav(os.path.join(args.out, outputs["vocals"]), parts["vocals"])
    if os.environ.get("MOUTH_FAKE_SEPARATOR_FAIL"):
        print(json.dumps({"ok": False, "detail": "the fake separator was told to fail"}),
              flush=True)
        return 1
    for s in STEMS[1:]:
        write_wav(os.path.join(args.out, outputs[s]), parts[s])
    inst = scaled(mix, sum(gains[s] for s in STEMS[1:]))
    write_wav(os.path.join(args.out, outputs["instrumental"]), inst)
    frames = len(mix) // CHANNELS
    print(json.dumps({
        "ok": True, "separator": "fake", "signature": "fake0001",
        "versions": {"fake": "1"}, "device": "cpu", "threads": args.threads, "rate": RATE,
        "channels": CHANNELS, "duration_s": round(frames / RATE, 3), "split_s": 0.0,
        "outputs": outputs,
        "levels_db": {"mix": level_db(mix), "vocals": level_db(parts["vocals"]),
                      "instrumental": level_db(inst)},
        "peak_scaled_db": None,
    }), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
