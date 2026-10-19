"""rt_turns.py OUT.jsonl [SPEED] [GAP] - the eval set's 20 turns as ONE call, gaps of GAP s
(default 3.0) between them, fed through the ears Pipeline (silence turns, commits on)
at SPEED x real time (default 2). Scores what a caller would get: turns out vs the
20 turns in, and WER of the joined turn text (the harness's whisper normaliser). Backend
from $EARS_BACKEND (default apple); helper from $EARS_APPLE_HELPER. The eval set and
its scorer come from $EARS_EVAL_DIR (data/list_turns.txt, bin/score.py, venv-eval/).
"""
import json, os, subprocess, sys, time
from ears.cli import Pipeline, decode

out, speed = sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 2.0
gap = float(sys.argv[3]) if len(sys.argv) > 3 else 3.0
EVAL = os.environ.get("EARS_EVAL_DIR") or sys.exit("set EARS_EVAL_DIR to the eval root (data/, bin/score.py, venv-eval/)")
EVAL = os.path.expanduser(EVAL)
D = os.path.join(EVAL, "data")
rows = [l.rstrip("\n").split("\t") for l in open(f"{D}/list_turns.txt") if l.strip()]
pcm, bounds, t = b"", [], 0.0
for wav, uid, dur in (r[:3] for r in rows):
    a = decode(wav, 16000)
    bounds.append((uid, t, t + len(a) / 32000)); pcm += a + b"\0" * int(gap * 32000); t = len(pcm) / 32000
evs = []
p = Pipeline(os.environ.get("EARS_BACKEND", "apple"), 16000, vad="auto", turns="silence", emit=evs.append)
t0 = time.monotonic()
for off in range(0, len(pcm), 640):
    p.feed(pcm[off:off + 640])
    ahead = (off + 640) / 32000 / speed - (time.monotonic() - t0)
    if ahead > 0: time.sleep(ahead)
p.close()
turns = [e for e in evs if e.type == "turn"]
with open(out, "w") as f:
    for e in evs:
        f.write(e.to_json() + "\n")
# which input turn does each output turn belong to (by its end time)?
def owner(x):
    for uid, s, e in bounds:
        if s - 0.5 <= x <= e + gap: return uid
    return None
got = {}
for e in turns:
    got.setdefault(owner(e.end), []).append(e)
split = sum(1 for v in got.values() if len(v) > 1)
merged = sum(1 for e in turns if e.start < max(s for _, s, _ in bounds if s <= e.end) - 1.0) if turns else 0
hyp = os.path.splitext(out)[0] + ".hyp.jsonl"
with open(hyp, "w") as f:
    for uid, _, _ in bounds:
        f.write(json.dumps(dict(id=uid, text=" ".join(e.text for e in got.get(uid, [])))) + "\n")
tail = sum(1 for e in turns if e.get("partial_tail"))
print(f"{len(bounds)} turns in, {len(turns)} out; split {split}, merged-across {merged}, partial_tail {tail}; audio {t:.0f} s at {speed}x")
print(subprocess.run([os.path.join(EVAL, "venv-eval/bin/python"),
                      os.path.join(EVAL, "bin/score.py"), "--ids", "turns", hyp],
                     capture_output=True, text=True).stdout.strip())
