"""ears eval - a clip, or every turn of a call, through each STT engine, side by side.

  ears eval FILE... [--ref TEXT | --refs TSV] [--engines a,b] [--out DIR]
  ears eval --call DIR [--refs TSV] [--offset S] [--limit N] [--engines a,b]
  ears eval engines                       which engines run here, and why not

Each clip prints vertically: the reference (if any), what the live ear heard (call
mode), then one line per engine, its WER in brackets when there is a reference. Totals
per engine follow. Everything cut, and every engine's raw output, stays under --out
(default ~/.voicemode/eval/<stamp>/).

--refs TSV: KEY<TAB>TEXT per line; KEY is a file's name (file mode) or a heard-log seq
(call mode). An empty TEXT means "nothing was said": every word an engine writes is
an insertion. WER uses a plain normaliser (lowercase, no punctuation, no um/uh).

--call DIR is a Delta call directory (caller-call.wav + caller-call.json; a call dir from
from an older sessionphone holds mike-call.wav + mike-call.json, still read), named
...-msg<N>, or a sessionphone web call's directory, named <N>. Its turns come
from the heard log (kind turn, device call:N), cut from the wav at t0+OFFSET (0.3 s,
measured on one call).

Built on the eval harness: the same models and flags. Its models (models/...) and
mlx venv (venv-mlx054/) live in EARS_EVAL_HARNESS, by default ~/.cache/ears/eval beside
ears' other downloads (it was under an agent's home, which a host keeping
that harness sets). The mlx engines run in EARS_EVAL_MLX_PY until ears' own venv carries
mlx-audio.
"""
import argparse, json, os, re, shutil, subprocess, sys, tempfile, time, urllib.request, uuid, wave
from datetime import datetime
from pathlib import Path

from . import callrec

HOME = Path.home()
HARNESS = Path(os.environ.get("EARS_EVAL_HARNESS") or HOME / ".cache/ears/eval").expanduser()
WBIN = Path(os.environ.get("EARS_EVAL_WHISPER_BIN", HOME / ".voicemode/services/whisper/build/bin"))
MLX_PY = os.environ.get("EARS_EVAL_MLX_PY", str(HARNESS / "venv-mlx054/bin/python"))
NEMOTRON = HARNESS / "models/nemotron-3.5-asr-streaming-0.6b"

ENGINES = {
    "apple": dict(kind="http", url=os.environ.get("EARS_EVAL_APPLE_URL", "http://127.0.0.1:2023/v1"),
                  about="Apple SpeechTranscriber via ears serve :2023 - the live call ear"),
    "whisper-v2": dict(kind="http", url=os.environ.get("EARS_EVAL_WHISPER_URL", "http://127.0.0.1:2022/v1"),
                       about="whisper.cpp large-v2, VoiceMode's server :2022"),
    "whisper-turbo": dict(kind="whisper-cli", bin=WBIN / "whisper-cli",
                          model=HARNESS / "models/whisper/ggml-large-v3-turbo.bin",
                          about="whisper.cpp large-v3-turbo, greedy"),
    "parakeet-cli": dict(kind="parakeet-cli", bin=WBIN / "parakeet-cli",
                         model=HARNESS / "models/parakeet-gguf/ggml-parakeet-tdt-0.6b-v3-q8_0.bin",
                         about="Parakeet TDT 0.6B v3, ggml q8_0"),
    "parakeet-mlx": dict(kind="mlx", mlx="parakeet", model="mlx-community/parakeet-tdt-0.6b-v3",
                         about="Parakeet TDT 0.6B v3, mlx-audio"),
    "nemotron": dict(kind="mlx", mlx="nemotron",
                     model=str(NEMOTRON) if NEMOTRON.is_dir() else "mlx-community/nemotron-3.5-asr-streaming-0.6b",
                     about="Nemotron 3.5 streaming 0.6B [56,6], mlx-audio"),
}
CALL_OFFSET = 0.3      # wav time = heard-log t0 + this, one call (67 turns, onset energy)
PAD = (0.2, 0.3)       # s kept before t0 and after t1


# -------------------------------------------------------------- engines
def why_not(name):
    """None when the engine can run here, else the reason it cannot."""
    e = ENGINES[name]
    if e["kind"] == "http":
        try:
            urllib.request.urlopen(e["url"].rsplit("/v1", 1)[0] + "/", timeout=2)
        except urllib.error.HTTPError:
            pass                              # it answered; the root just is not a page
        except Exception as x:
            return f"{e['url']} not answering ({type(x).__name__})"
        return None
    if e["kind"] in ("whisper-cli", "parakeet-cli"):
        for k in ("bin", "model"):
            if not Path(e[k]).exists():
                return f"no {k}: {e[k]}"
        return None
    if not Path(MLX_PY).exists():
        return f"no mlx python: {MLX_PY} (set EARS_EVAL_MLX_PY)"
    return None


def _post(url, wav):
    b = uuid.uuid4().hex
    parts = []
    for k, v in (("model", "whisper-1"), ("response_format", "json")):
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{Path(wav).name}"\r\n'
                 f'Content-Type: audio/wav\r\n\r\n'.encode() + Path(wav).read_bytes() + b"\r\n")
    parts.append(f"--{b}--\r\n".encode())
    req = urllib.request.Request(url.rstrip("/") + "/audio/transcriptions", data=b"".join(parts),
                                 headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r).get("text", "")


def _cli(e, wav, tmp):
    if e["kind"] == "whisper-cli":
        of = os.path.join(tmp, uuid.uuid4().hex)
        p = subprocess.run([str(e["bin"]), "-m", str(e["model"]), "-f", wav, "-l", "en", "-t", "8", "-nt",
                            "-otxt", "-of", of, "-bs", "1", "-bo", "2"], capture_output=True, text=True)
        if p.returncode:
            raise RuntimeError(p.stderr.strip().splitlines()[-1] if p.stderr.strip() else f"rc={p.returncode}")
        return open(of + ".txt").read() if os.path.exists(of + ".txt") else ""
    p = subprocess.run([str(e["bin"]), "-t", "8", "-m", str(e["model"]), "-f", wav], capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(p.stderr.strip().splitlines()[-1] if p.stderr.strip() else f"rc={p.returncode}")
    return p.stdout


def run_engine(name, clips, out_dir):
    """Every clip through one engine -> {id: {text, proc_s[, error]}}; also OUT/<name>.jsonl."""
    e, res = ENGINES[name], {}
    jl = out_dir / f"{name}.jsonl"
    if e["kind"] == "mlx":
        lst = out_dir / "list.tsv"
        here = Path(__file__).with_name("eval_mlx.py")
        p = subprocess.run([MLX_PY, str(here), e["mlx"], e["model"], str(lst), str(jl)],
                           capture_output=True, text=True)
        if p.returncode:
            err = (p.stderr.strip().splitlines() or [f"rc={p.returncode}"])[-1]
            return {c["id"]: {"text": "", "error": err} for c in clips}
        for line in open(jl):
            r = json.loads(line); res[r["id"]] = r
        return res
    tmp = tempfile.mkdtemp(prefix=f"{name}-", dir=out_dir)
    with open(jl, "w") as f:
        for c in clips:
            t = time.perf_counter()
            try:
                txt = _post(e["url"], c["wav"]) if e["kind"] == "http" else _cli(e, c["wav"], tmp)
                r = {"id": c["id"], "text": " ".join(txt.split())}
            except Exception as x:
                r = {"id": c["id"], "text": "", "error": f"{type(x).__name__}: {x}"}
            r["proc_s"] = round(time.perf_counter() - t, 4)
            res[c["id"]] = r
            f.write(json.dumps(r) + "\n"); f.flush()
    shutil.rmtree(tmp, ignore_errors=True)
    return res


# -------------------------------------------------------------- scoring
FILLERS = {"um", "uh", "umm", "uhm", "hmm", "mm", "mhm", "er", "erm", "ah", "eh"}


def norm(s):
    s = (s or "").lower().replace("’", "'")
    s = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", s)          # [INAUDIBLE], (laughs)
    s = re.sub(r"[-–—/]", " ", s)
    s = re.sub(r"[^a-z0-9' ]+", " ", s)
    s = re.sub(r"(?<![a-z])'|'(?![a-z])", " ", s)
    return [w for w in s.split() if w not in FILLERS]


def edits(ref, hyp):
    """(S, D, I) of the cheapest word alignment."""
    R, H = len(ref), len(hyp)
    d = [[(0, 0, 0, 0)] * (H + 1) for _ in range(R + 1)]   # (cost, S, D, I)
    for i in range(1, R + 1): d[i][0] = (i, 0, i, 0)
    for j in range(1, H + 1): d[0][j] = (j, 0, 0, j)
    for i in range(1, R + 1):
        for j in range(1, H + 1):
            if ref[i - 1] == hyp[j - 1]:
                d[i][j] = d[i - 1][j - 1]; continue
            s, dl, ins = d[i - 1][j - 1], d[i - 1][j], d[i][j - 1]
            d[i][j] = min((s[0] + 1, s[1] + 1, s[2], s[3]), (dl[0] + 1, dl[1], dl[2] + 1, dl[3]),
                          (ins[0] + 1, ins[1], ins[2], ins[3] + 1))
    return d[R][H][1:]


# -------------------------------------------------------------- clips
def to_wav16(src, dst):
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(src), "-ar", "16000", "-ac", "1",
                    "-c:a", "pcm_s16le", str(dst)], check=True)


def wav_dur(p):
    with wave.open(str(p)) as w:
        return w.getnframes() / w.getframerate()


def file_clips(paths, out_dir):
    clips = []
    for i, p in enumerate(paths, 1):
        dst = out_dir / "clips" / f"{i:03d}-{Path(p).stem}.wav"
        to_wav16(p, dst)
        clips.append(dict(id=Path(p).name, wav=str(dst), dur=wav_dur(dst), label=Path(p).name))
    return clips


def heard_turns(call_id, start, end):
    from . import heard
    from datetime import date, timedelta
    d0, d1 = datetime.fromtimestamp(start).date(), datetime.fromtimestamp(end).date()
    seen, out = set(), []
    while d0 <= d1:
        p = heard.log_path(d0)
        if p.exists():
            for line in open(p):
                if f'"call:{call_id}"' not in line:
                    continue
                r = json.loads(line)
                if r.get("kind") == "turn" and r.get("device") == f"call:{call_id}" and r.get("seq") not in seen:
                    seen.add(r.get("seq")); out.append(r)
        d0 += timedelta(days=1)
    return sorted(out, key=lambda r: r["t0"])


def call_clips(call_dir, out_dir, offset, limit):
    call_dir = Path(call_dir).expanduser()
    meta_path, wav_path = callrec.call_file(call_dir, "json"), callrec.call_file(call_dir, "wav")
    for path, ext in ((meta_path, "json"), (wav_path, "wav")):
        if path is None:
            raise SystemExit(f"ears eval: no {callrec.looked_for(ext)} in {call_dir}")
    meta = json.loads(meta_path.read_text())
    m = re.fullmatch(r"(?:.*msg)?(\d+)", call_dir.name)   # a Delta ...-msg<N>, or a web call's <N>
    if not m:
        raise SystemExit(f"ears eval: cannot read the call id from {call_dir.name} (want ...-msgNNNN or NNNN)")
    cid = m.group(1)
    turns = heard_turns(cid, meta["start_epoch"], meta.get("end_epoch") or time.time())
    if limit:
        turns = turns[:limit]
    clips = []
    with wave.open(str(wav_path)) as w:
        rate, n = w.getframerate(), w.getnframes()
        for i, r in enumerate(turns, 1):
            a = max(0, int((r["t0"] + offset - PAD[0]) * rate))
            b = min(n, int((r["t1"] + offset + PAD[1]) * rate))
            if b <= a:
                continue
            w.setpos(a)
            dst = out_dir / "clips" / f"{i:03d}-seq{r['seq']}.wav"
            with wave.open(str(dst), "wb") as o:
                o.setnchannels(w.getnchannels()); o.setsampwidth(w.getsampwidth()); o.setframerate(rate)
                o.writeframes(w.readframes(b - a))
            clips.append(dict(id=str(r["seq"]), wav=str(dst), dur=(b - a) / rate, heard=r.get("text", ""),
                              label=f"seq {r['seq']}  {r.get('ts', '')[11:19]}"))
    return cid, clips


# -------------------------------------------------------------- report
def report(clips, results, refs, names, emit):
    tot = {n: dict(S=0, D=0, I=0, N=0, proc=0.0, err=0) for n in names}
    w = max(len(n) for n in names + ["heard", "ref"]) + 2
    scored = 0
    for k, c in enumerate(clips, 1):
        ref = refs.get(c["id"])
        emit(f"\n#{k}  {c['label']}  {c['dur']:.1f} s")
        rw = norm(ref) if ref is not None else None
        if ref is not None:
            scored += 1
            emit(f"  {'ref':{w}}{ref if ref else '(nothing said)'}")
        if "heard" in c:
            emit(f"  {'heard':{w}}{c['heard']}")
        for n in names:
            r = results[n].get(c["id"], {"text": "", "error": "no result"})
            tot[n]["proc"] += r.get("proc_s") or 0
            txt = r["text"] if not r.get("error") else f"(error: {r['error']})"
            tot[n]["err"] += bool(r.get("error"))
            tag = ""
            if rw is not None:
                S, D, I = edits(rw, norm(r["text"]))
                for key, v in (("S", S), ("D", D), ("I", I), ("N", len(rw))):
                    tot[n][key] += v
                tag = f"[{100 * (S + D + I) / len(rw):3.0f}%] " if rw else f"[+{I} ins] "
            emit(f"  {n:{w}}{tag}{txt}")
    audio = sum(c["dur"] for c in clips)
    emit(f"\n== {len(clips)} clips, {audio:.0f} s of audio"
         + (f"; {scored} with a reference ({sum(1 for c in clips if refs.get(c['id']) is not None and norm(refs[c['id']]))} with words)" if scored else "; no references, so no WER"))
    for n in names:
        t = tot[n]
        wer = f"WER {100 * (t['S'] + t['D'] + t['I']) / t['N']:5.1f}%  S/D/I {t['S']}/{t['D']}/{t['I']}  " if t["N"] else \
              (f"+{t['I']} ins  " if scored else "")
        emit(f"  {n:{w}}{wer}{t['proc']:.1f} s wall" + (f"  {t['err']} errors" if t["err"] else ""))


def load_refs(path):
    refs = {}
    for line in open(path):
        if not line.strip() or line.startswith("#"):
            continue
        k, _, v = line.rstrip("\n").partition("\t")
        refs[k.strip()] = v.strip()
    return refs


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["engines"]:
        for n, e in ENGINES.items():
            why = why_not(n)
            print(f"{n:14} {'ok' if why is None else 'no: ' + why}\n{'':14} {e['about']}")
        return 0
    ap = argparse.ArgumentParser(prog="ears eval", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", help="audio files (any format ffmpeg reads)")
    ap.add_argument("--call", help="a call directory (a Delta bot's ...-msg<N>, or a web call's <N>): "
                                   "every heard turn of the call")
    ap.add_argument("--ref", help="the reference text (one file only)")
    ap.add_argument("--refs", help="KEY<TAB>TEXT per line (file name, or heard seq with --call)")
    ap.add_argument("--engines", help=f"comma list (default: every one that runs here): {','.join(ENGINES)}")
    ap.add_argument("--offset", type=float, default=CALL_OFFSET, help="wav s = heard t0 + this (--call)")
    ap.add_argument("--limit", type=int, help="first N turns only (--call)")
    ap.add_argument("--out", help="keep everything here (default ~/.voicemode/eval/<stamp>)")
    a = ap.parse_args(argv)
    if bool(a.files) == bool(a.call):
        ap.error("give FILE... or --call DIR (one of them)")
    if a.ref is not None and len(a.files) != 1:
        ap.error("--ref goes with exactly one FILE; use --refs for more")

    names = a.engines.split(",") if a.engines else list(ENGINES)
    bad = [n for n in names if n not in ENGINES]
    if bad:
        ap.error(f"unknown engine(s): {','.join(bad)}; known: {','.join(ENGINES)}")
    skipped = {n: why_not(n) for n in names}
    skipped = {n: w for n, w in skipped.items() if w}
    if a.engines and skipped:
        for n, w in skipped.items():
            print(f"ears eval: {n}: {w}", file=sys.stderr)
        return 2
    names = [n for n in names if n not in skipped]

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(a.out).expanduser() if a.out else HOME / ".voicemode/eval" / stamp
    (out_dir / "clips").mkdir(parents=True, exist_ok=True)
    refs = load_refs(a.refs) if a.refs else {}
    if a.call:
        cid, clips = call_clips(a.call, out_dir, a.offset, a.limit)
        head = f"call:{cid}  {len(clips)} turns"
    else:
        clips = file_clips(a.files, out_dir)
        if a.ref is not None:
            refs[clips[0]["id"]] = a.ref
        head = f"{len(clips)} file(s)"
    if not clips:
        print("ears eval: nothing to run (no clips)", file=sys.stderr); return 2
    with open(out_dir / "list.tsv", "w") as f:
        for c in clips:
            f.write(f"{c['wav']}\t{c['id']}\t{c['dur']:.3f}\n")

    rep = open(out_dir / "report.txt", "w")
    def emit(s=""):
        print(s, flush=True); rep.write(s + "\n")
    emit(f"ears eval  {head}  -> {out_dir}")
    emit(f"engines: {' '.join(names)}" + (f"   (skipped: {'; '.join(f'{n}: {w}' for n, w in skipped.items())})" if skipped else ""))
    results = {}
    for n in names:
        t = time.monotonic()
        print(f"  running {n} ...", end="", file=sys.stderr, flush=True)
        results[n] = run_engine(n, clips, out_dir)
        print(f" {time.monotonic() - t:.1f} s", file=sys.stderr, flush=True)
    report(clips, results, refs, names, emit)
    rep.close()
    with open(out_dir / "results.json", "w") as f:
        json.dump(dict(clips=clips, refs=refs, engines=names, results=results), f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
