"""regress.py BACKEND LIST OUT.jsonl - the ears pipeline over an eval list, for WER.

Fast feed, no turn stage: the text is the backend's finals joined, which is
what the backend itself heard. Score with the eval harness, EARS_EVAL_DIR being
its root (data/, bin/score.py, venv-eval/):
  $EARS_EVAL_DIR/venv-eval/bin/python $EARS_EVAL_DIR/bin/score.py --ids singles OUT.jsonl
LIST rows are the harness's: WAV<TAB>ID<TAB>DURATION.
"""
import json, sys, time
from ears.cli import Pipeline, decode

backend, lst, out = sys.argv[1], sys.argv[2], sys.argv[3]
rows = [l.rstrip("\n").split("\t") for l in open(lst) if l.strip()]
t_all = a_all = 0.0
with open(out, "w") as f:
    for wav, uid, dur in (r[:3] for r in rows):
        evs = []
        t0 = time.monotonic()
        p = Pipeline(backend, 16000, vad="auto", turns="none", emit=evs.append)
        pcm = decode(wav, 16000) + b"\0" * 32000          # 1 s tail
        for off in range(0, len(pcm), 640):
            p.feed(pcm[off:off + 640])
        p.close()
        wall = time.monotonic() - t0
        finals = sorted((e for e in evs if e.type == "final" and e.text.strip()), key=lambda e: e.seg or 0)
        errs = [e.get("message") for e in evs if e.type == "error"]
        f.write(json.dumps(dict(id=uid, text=" ".join(e.text.strip() for e in finals), segs=len(finals),
                                wall_s=round(wall, 3), dur=float(dur), errors=errs)) + "\n"); f.flush()
        t_all += wall; a_all += float(dur)
print(f"{backend}: {len(rows)} files, {a_all:.0f} s audio in {t_all:.1f} s wall (incl. per-file startup)", file=sys.stderr)
