"""Diarise the recorded room lines together (no references), then show each speaker
cluster with its lines, so the clusters can be named from what was said."""
import json, sys, wave, numpy as np
from pathlib import Path
from mlx_audio.vad import load
SR = 16000
# The diarizer's pinned snapshot: equal to ears.services.SPEAKER_REVISION (a test holds them).
REVISION = "59ed2dbfc1346dcea9d423c71306a3a2499c568f"
d = Path(sys.argv[1])
rows = sorted((json.loads(j.read_text()) for j in d.glob("*.json")), key=lambda r: r["seq"])
rows = [r for r in rows if r["kind"] == "partial"]
def rd(p):
    with wave.open(str(p)) as w: return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
gap = np.zeros(SR // 4, np.float32)
xs, spans, t = [], [], 0
for r in rows:
    x = rd(d / f"{r['seq']}.wav"); xs += [x, gap]
    spans.append((t // 160, (t + len(x)) // 160)); t += len(x) + len(gap)
X = np.concatenate(xs)
m = load("mlx-community/Nemotron-3-Diarization", strict=True, revision=REVISION); m.set_streaming_config("offline")
p = np.array(m.generate(X).speaker_probs)
print(f"{len(rows)} lines, {len(X)/SR:.0f}s; frames per label {(p > 0.5).sum(0).tolist()}")
out = []
for r, (f0, f1) in zip(rows, spans):
    act = (p[f0:f1] > 0.5).sum(0); tot = act.sum()
    lab = int(act.argmax()) if tot >= 10 else -1
    share = act[lab] / tot if tot else 0
    out.append({"seq": r["seq"], "label": lab, "share": round(float(share), 2), "text": r["text"], "ts": r["ts"][11:19]})
    print(f"{r['ts'][11:19]} {r['seq']} L{lab:>2} {share:4.2f}  {r['text'][:80]}")
json.dump(out, open(d / "clusters.json", "w"), indent=0)
