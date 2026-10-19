"""The mlx-audio half of `ears eval`, run in an interpreter that has mlx-audio
(EARS_EVAL_MLX_PY; ears' own venv does not carry it yet).

  python eval_mlx.py parakeet|nemotron MODEL LIST.tsv OUT.jsonl [RIGHT_CTX]

One model load, one untimed warm-up, then every clip in LIST (wav<TAB>id<TAB>dur),
one JSON line each: id, text, proc_s. Parakeet: whole-file generate. Nemotron: the
streaming session fed 1 s at a time (stepped after each), closed and drained, attention [56, RIGHT_CTX]
(6, as ears' backend). Lifted from the run_parakeet_mlx.py / run_nemotron.py.
"""
import json, os, sys, time
os.environ.setdefault("HF_HUB_OFFLINE", "1")


NEEDS = "numpy soundfile mlx-audio"


def _imports():
    """What this interpreter must carry. It is NOT ears' venv (EARS_EVAL_MLX_PY), so a
    missing one exits with ONE stderr line naming this interpreter and the pip line
    for it: `ears eval` reports the last stderr line as each clip's error."""
    try:
        import numpy as np
        import soundfile as sf
        from mlx_audio.stt.utils import load_model
    except ImportError as exc:
        sys.exit(f"eval_mlx: {sys.executable} (EARS_EVAL_MLX_PY) lacks {exc.name or exc}; "
                 f"it needs {NEEDS}: {sys.executable} -m pip install {NEEDS}")
    return np, sf, load_model


def main(argv):
    kind, model_id, lst, out = argv[:4]
    rc = int(argv[4]) if len(argv) > 4 else 6
    np, sf, load_model = _imports()
    model = load_model(model_id)
    if kind == "nemotron":
        model.default_att_context_size = [56, rc]

    def run(wav):
        if kind == "parakeet":
            return model.generate(wav).text
        a, sr = sf.read(wav, dtype="float32")
        if a.ndim > 1:
            a = a.mean(axis=1)
        s = model.create_streaming_session(language="en")
        limit = s._encoder.chunk_mel * model.preprocessor_config.hop_length
        parts = []
        for off in range(0, len(a), 16000):        # 1 s blocks: its queue refuses more than 30 s
            s.feed(np.ascontiguousarray(a[off:off + 16000]))
            while True:
                parts += s.step(max_decode_tokens=4096)
                if not s._encoded and s._queued < limit:
                    break
        s.close()
        while not s.done:
            parts += s.step(max_decode_tokens=4096)
        return "".join(parts)

    rows = [l.rstrip("\n").split("\t") for l in open(lst) if l.strip()]
    if rows:
        run(rows[0][0])
    with open(out, "w") as f:
        for wav, uid, _dur in rows:
            t = time.perf_counter()
            try:
                txt, err = run(wav), None
            except Exception as e:  # one bad clip must not sink the batch
                txt, err = "", f"{type(e).__name__}: {e}"
            rec = {"id": uid, "text": " ".join((txt or "").split()), "proc_s": round(time.perf_counter() - t, 4)}
            if err:
                rec["error"] = err
            f.write(json.dumps(rec) + "\n"); f.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
