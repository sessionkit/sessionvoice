#!/usr/bin/env python3
"""make-refs DIR LABEL=NAME [LABEL=NAME ...] [--out refs] [--raw] [--seconds 12]

Turn named clusters into reference clips, using no voice from anywhere else.
cluster-room.py DIR prints each cluster's lines. A person reads them, sees who
said what, and names the clusters here: `make-refs.py room L0=alice L1=bob`.

Each NAME gets refs/NAME.wav and refs/NAME.txt (the lines pooled). Both are
0600: they are someone's voice, and they never go in git.

The clip is made the way the 2030-06-09 room references were made by hand:
pool the cluster's lines with share >= 0.6, run the diarizer on the pool, keep
only the 10 ms frames where its main speaker talks ALONE, cut at --seconds,
then check the clip comes back as one speaker. --raw skips the model: the
lines with share >= 0.85, pooled as they are (worse, but needs no model).

Replaces name-clusters.py for a new mic: that matched a person with a clip from
another mic. A new mic or room needs new references (speaker-sidecar2 checks
each is one speaker and all are different at start).
"""
import argparse, json, os, re, sys, wave
from pathlib import Path
import numpy as np

SR = 16000
# The diarizer's pinned snapshot: equal to ears.services.SPEAKER_REVISION (a test holds them).
REVISION = "59ed2dbfc1346dcea9d423c71306a3a2499c568f"
ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
ap.add_argument("dir")
ap.add_argument("names", nargs="+", metavar="LABEL=NAME")
ap.add_argument("--out", default="refs")
ap.add_argument("--raw", action="store_true", help="no model: pool share >= 0.85 lines as they are")
ap.add_argument("--seconds", type=float, default=12.0)
a = ap.parse_args()

d, out = Path(a.dir), Path(a.out)
cl = json.loads((d / "clusters.json").read_text())
specs = []
for spec in a.names:
    m = re.fullmatch(r"L?(\d+)=([a-z][a-z0-9_-]*)", spec)
    if not m: sys.exit(f"make-refs: {spec!r} is not LABEL=NAME (e.g. L0=alice)")
    specs.append((int(m[1]), m[2]))
if len({n for _, n in specs}) < len(specs): sys.exit("make-refs: a name is given twice")
if len({l for l, _ in specs}) < len(specs): sys.exit("make-refs: a cluster is named twice")

def rd(p):
    with wave.open(str(p)) as w:
        assert w.getframerate() == SR and w.getnchannels() == 1, f"{p}: need 16 kHz mono"
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768

def private(p):
    return os.fdopen(os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb")

P = None
if not a.raw:
    from mlx_audio.vad import load
    m = load("mlx-community/Nemotron-3-Diarization", strict=True, revision=REVISION); m.set_streaming_config("offline")
    P = lambda x: np.array(m.generate(x).speaker_probs) > 0.5

out.mkdir(mode=0o700, exist_ok=True)
for lab, name in specs:
    rows = [r for r in cl if r["label"] == lab and r["share"] >= (0.85 if a.raw else 0.6)]
    if not rows: sys.exit(f"make-refs: cluster L{lab} has no clean lines")
    x = np.concatenate([rd(d / f"{r['seq']}.wav") for r in rows])
    note = "raw"
    if P is not None:
        p = P(x); act = p.sum(0); dom = int(act.argmax())
        alone = np.flatnonzero(p[:, dom] & (p.sum(1) == 1))
        x = np.concatenate([x[i * 160:(i + 1) * 160] for i in alone]) if len(alone) else x[:0]
        x = x[: int(a.seconds * SR)]
        chk = P(x).sum(0) if len(x) else np.zeros(1)
        note = f"alone frames of label {dom}; check {chk.tolist()}"
        if chk.sum() == 0 or chk.max() / chk.sum() < 0.9:
            sys.exit(f"make-refs: {name} (L{lab}) does not come back as one speaker ({note}) - refused")
    x = x[: int(a.seconds * SR)]
    if len(x) < 5 * SR:
        print(f"make-refs: warning - {name} has only {len(x)/SR:.1f}s; record more lines", file=sys.stderr)
    with private(out / f"{name}.wav") as f, wave.open(f, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())
    with private(out / f"{name}.txt") as f:
        f.write(f"# {name}: cluster L{lab} of {d.name}, {note}\n".encode())
        f.write("".join(f"{r['seq']}\t{r['text']}\n" for r in rows).encode())
    print(f"{name}: L{lab}, {len(rows)} lines -> {len(x)/SR:.1f}s ({note}) -> {out / (name + '.wav')}")
