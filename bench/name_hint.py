"""name_hint.py - does the STT name hint make "hey <seat>" wake?

  python3 bench/name_hint.py build [--rebuild]                 the corpus, from the manifest
  python3 bench/name_hint.py run --url http://127.0.0.1:2122/v1 [--hint TEXT] [--modes off,on]

whisper can hear "hey Dave" as "Davis", "Dane" or "caves", and the seat never wakes.
The ears send whisper a prompt built from the wake file's seat names. This bench
measures it: the wake-name hit rate with the hint OFF and ON, and the false-wake rate.

build: a reproducible corpus from bench/name_hint.json (the manifest, in git). macOS
``say`` speaks each line in several voices and rates; each clip is then rendered
through every condition (clean, an 8 kHz phone band with mu-law, a 16 kHz Bluetooth-like
band-limit, and that band plus noise), ending 16 kHz mono int16, as the ears send.
  positives  "hey <name>, <tail>" for each seat of the manifest's test wake file
  negatives  speech with no seat; "near": "hey <real word near a name>" (a real
             "hey Davis" must NOT become Dave); silence and noise-only (whisper can
             return its PROMPT as the text of silence: the prompt-echo false wake)
The clips, an index (index.jsonl, with each clip's sha256) and the test wake file live
under $EARS_EVAL_HARNESS/name-hint (default ~/.cache/ears/eval/name-hint), never in git.
A build whose manifest has not changed is reused; --rebuild remakes it.

run: every clip through the REAL ``WhisperSTT`` (the ears' own HTTP path, prompt field
included) at --url, once per mode: ``off`` sends no prompt; ``on`` sends the prompt
``ears.wake.WakeHint`` builds from the test wake file ("Dave, Bob, Carol."),
or --hint TEXT verbatim (to compare casings or shapes). Each transcript is scored by
keeper/ears-mail.py's own ``_call`` regex, built by its own ``load_rules`` from the wake
file's PRODUCTION names, so a hit is exactly what would wake the seat.
  hit         a positive whose transcript calls its own seat
  wrong       a positive that calls a different seat
  false wake  a negative that calls any seat (nonspeech broken out: prompt echo)
Per run: results.jsonl and report.txt under runs/<stamp>/; the report also prints.

NEVER a live whisper (:2022): a URL that is not loopback is refused unless
--allow-remote, and port 2022 is refused unless --allow-2022. Run a scratch
whisper-server on another port.

If the hint-OFF hit rate is already ~100%, the synthetic corpus has not reproduced the
mishearing and the comparison proves nothing: the report says so.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import ipaddress
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import wave
import zlib
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
MANIFEST = HERE / "name_hint.json"
EARS_MAIL = REPO / "keeper" / "ears-mail.py"
SR = 16000
LIVE_PORT = 2022          # VoiceMode's whisper: live on a host, never benched
NEAR_ALL = 0.95           # a hint-OFF hit rate at or above this proves nothing


class BenchError(Exception):
    """A refusal: the message says why."""


def harness_root() -> Path:
    base = os.environ.get("EARS_EVAL_HARNESS") or "~/.cache/ears/eval"
    return Path(base).expanduser() / "name-hint"


# -- the manifest ----------------------------------------------------------------

def load_manifest(path=MANIFEST) -> dict:
    return json.loads(Path(path).read_text())


def manifest_hash(m: dict) -> str:
    return hashlib.sha256(json.dumps(m, sort_keys=True).encode()).hexdigest()[:16]


def _slug(s) -> str:
    return "".join(c if c.isalnum() else "-" for c in str(s).lower()).strip("-")


def utterances(m: dict) -> list[dict]:
    """Every clip before its condition: id, kind (pos|speech|near|silence|noise), seat
    (positives only), text, voice, rate. Order is the manifest's, so ids are stable."""
    out = []
    voices, rates = m["voices"], m["rates"]
    opener = m["positives"].get("opener", "hey")
    for seat in m["wake"]["seats"]:
        name = m.get("say", {}).get(seat, seat)
        for t, tail in enumerate(m["positives"]["tails"]):
            for v in voices:
                for r in rates:
                    out.append(dict(id=f"pos-{_slug(seat)}-{t}-{_slug(v)}-{r}", kind="pos", seat=seat,
                                    text=f"{opener} {name}, {tail}", voice=v, rate=r))
    for kind in ("speech", "near"):
        for t, text in enumerate(m["negatives"].get(kind, [])):
            for v in voices:
                for r in rates:
                    out.append(dict(id=f"{kind}-{t}-{_slug(v)}-{r}", kind=kind, seat=None,
                                    text=text, voice=v, rate=r))
    for ns in m["negatives"].get("nonspeech", []):
        out.append(dict(ns, id=ns["id"], kind=ns["kind"], seat=None, text="", voice=None, rate=None))
    return out


# -- speech: macOS say -------------------------------------------------------------

def say_synth(text: str, voice: str, rate: int) -> np.ndarray:
    """TEXT spoken by macOS say in VOICE at RATE wpm: 16 kHz mono int16."""
    if not shutil.which("say"):
        raise BenchError("macOS `say` not found: build the corpus on a Mac (or copy a built one)")
    with tempfile.TemporaryDirectory() as d:
        wav = Path(d) / "say.wav"
        p = subprocess.run(["say", "-v", voice, "-r", str(rate), "-o", str(wav),
                            f"--data-format=LEI16@{SR}", text], capture_output=True, text=True)
        if p.returncode != 0 or not wav.exists():
            raise BenchError(f"say -v {voice}: {p.stderr.strip() or 'no output'}")
        return read_wav(wav)


def read_wav(path) -> np.ndarray:
    with wave.open(str(path)) as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise BenchError(f"{path}: want mono int16, got {w.getnchannels()} ch x {w.getsampwidth()} B")
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        return resample(x.astype(np.float64), w.getframerate(), SR).round().astype(np.int16) \
            if w.getframerate() != SR else x.copy()


def write_wav(path, x: np.ndarray, sr: int = SR) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.asarray(x, dtype=np.int16).tobytes())


# -- the degradations (numpy only: no ffmpeg, no scipy needed) -------------------------

def _rms(x) -> float:
    return float(np.sqrt(np.mean(np.square(x)))) if len(x) else 0.0


def _db(dbfs) -> float:
    return 10 ** (dbfs / 20)


def resample(x: np.ndarray, src: int, dst: int) -> np.ndarray:
    """X from SRC Hz to DST Hz by FFT (a downsample drops everything over DST/2)."""
    n = len(x)
    if src == dst or n == 0:
        return np.asarray(x, dtype=np.float64)
    m = max(1, int(round(n * dst / src)))
    X = np.fft.rfft(x)
    k = m // 2 + 1
    Y = np.zeros(k, dtype=complex)
    Y[:min(k, len(X))] = X[:min(k, len(X))]
    return np.fft.irfft(Y, m) * (m / n)


def bandlimit(x: np.ndarray, sr: int, lo: float, hi: float) -> np.ndarray:
    """X band-passed to LO..HI Hz, with short linear tapers at the edges."""
    n = len(x)
    if n == 0:
        return np.asarray(x, dtype=np.float64)
    f = np.fft.rfftfreq(n, 1 / sr)
    wl, wh = max(lo * 0.5, 1.0), 200.0
    g = np.clip((f - (lo - wl)) / wl, 0, 1) * np.clip(((hi + wh) - f) / wh, 0, 1)
    g[f < lo - wl] = 0
    g[f > hi + wh] = 0
    return np.fft.irfft(np.fft.rfft(x) * g, n)


def mulaw(x: np.ndarray, mu: int = 255) -> np.ndarray:
    """G.711-like: X (full scale 1.0) through 8-bit mu-law and back."""
    y = np.sign(x) * np.log1p(mu * np.minimum(np.abs(x), 1.0)) / np.log1p(mu)
    y = np.round(y * 127) / 127
    return np.sign(y) * np.expm1(np.abs(y) * np.log1p(mu)) / mu


def noise(n: int, rng, color: str = "white") -> np.ndarray:
    """N samples of unit-rms noise; pink is white shaped 1/sqrt(f)."""
    w = rng.standard_normal(n)
    if color == "pink" and n > 1:
        W = np.fft.rfft(w)
        f = np.arange(len(W), dtype=np.float64)
        f[0] = 1.0
        w = np.fft.irfft(W / np.sqrt(f), n)
    r = _rms(w)
    return w / r if r else w


def nonspeech(item: dict, rng) -> np.ndarray:
    """A silence or noise-only clip, float, full scale 1.0, at SR."""
    n = int(round(item["seconds"] * SR))
    if item["kind"] == "silence":
        return np.zeros(n)
    return noise(n, rng, item.get("color", "white")) * _db(item.get("dbfs", -30))


def normalise(x: np.ndarray, dbfs: float) -> np.ndarray:
    r = _rms(x)
    return x * (_db(dbfs) / r) if r else x


def degrade(x: np.ndarray, cond: dict, rng) -> np.ndarray:
    """X (float, full scale 1.0, at SR) through COND: band, rate (and back to SR),
    mulaw, snr_db (noise at that SNR; on a silent clip, at floor_dbfs). 16 kHz int16 out."""
    y = np.asarray(x, dtype=np.float64)
    rate = cond.get("rate", SR)
    if rate != SR:
        y = resample(y, SR, rate)
    if cond.get("band"):
        lo, hi = cond["band"]
        y = bandlimit(y, rate, lo, min(hi, rate / 2))
    if cond.get("mulaw"):
        y = mulaw(np.clip(y, -1, 1))
    if rate != SR:
        y = resample(y, rate, SR)
    if cond.get("snr_db") is not None:
        r = _rms(y)
        level = r / _db(cond["snr_db"]) if r > _db(-80) else _db(cond.get("floor_dbfs", -45))
        nz = noise(len(y), rng, "pink") * level
        if cond.get("band"):
            nz = bandlimit(nz, SR, *cond["band"])
            nz *= level / (_rms(nz) or 1)
        y = y + nz
    return np.clip(np.round(y * 32767), -32768, 32767).astype(np.int16)


def _rng(m: dict, clip_id: str):
    return np.random.default_rng([int(m.get("seed", 0)), zlib.crc32(clip_id.encode())])


# -- build --------------------------------------------------------------------------

def write_wake(m: dict, path) -> Path:
    """The manifest's TEST wake file, which both the hint and the scorer read."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(m["wake"], indent=2) + "\n")
    return p


def load_index(root) -> tuple[dict, list[dict]]:
    p = Path(root) / "index.jsonl"
    if not p.exists():
        return {}, []
    lines = [json.loads(s) for s in p.read_text().splitlines() if s.strip()]
    return (lines[0], lines[1:]) if lines and lines[0].get("manifest") else ({}, lines)


def build(m: dict, root, *, synth=say_synth, rebuild=False, log=print) -> list[dict]:
    """Render every utterance through every condition under ROOT/corpus; write the test
    wake file and ROOT/index.jsonl (a header line, then one line per clip). Reuses a build
    of the same manifest unless REBUILD. A voice say lacks is skipped and logged."""
    root = Path(root)
    mh = manifest_hash(m)
    head, clips = load_index(root)
    if clips and head.get("manifest") == mh and not rebuild:
        log(f"corpus: {len(clips)} clips, manifest {mh} unchanged ({root}); --rebuild remakes it")
        return clips
    corpus = root / "corpus"
    if corpus.exists():
        shutil.rmtree(corpus)
    corpus.mkdir(parents=True)
    write_wake(m, root / "wake.json")
    clips, missing = [], {}
    for u in utterances(m):
        if u["kind"] in ("silence", "noise"):
            base = nonspeech(u, _rng(m, u["id"]))
        else:
            if u["voice"] in missing:
                continue
            try:
                raw = synth(u["text"], u["voice"], u["rate"])
            except BenchError as e:
                if not shutil.which("say") and synth is say_synth:
                    raise
                missing[u["voice"]] = str(e)
                log(f"skip voice {u['voice']}: {e}")
                continue
            base = normalise(np.asarray(raw, dtype=np.float64) / 32768, m.get("speech_dbfs", -20))
        for cname, cond in m["conditions"].items():
            cid = f"{u['id']}-{cname}"
            pcm = degrade(base, cond, _rng(m, cid))
            wav = corpus / f"{cid}.wav"
            write_wav(wav, pcm)
            clips.append(dict(id=cid, kind=u["kind"], seat=u["seat"], text=u["text"], voice=u["voice"],
                              rate=u["rate"], condition=cname, wav=str(wav.relative_to(root)),
                              samples=int(len(pcm)), sha256=hashlib.sha256(pcm.tobytes()).hexdigest()))
    header = dict(manifest=mh, built=datetime.now().isoformat(timespec="seconds"),
                  clips=len(clips), skipped_voices=sorted(missing))
    with open(root / "index.jsonl", "w") as f:
        for row in [header] + clips:
            f.write(json.dumps(row) + "\n")
    log(f"corpus: built {len(clips)} clips ({root})")
    return clips


# -- the URL guard --------------------------------------------------------------------

def check_url(url: str, *, allow_remote=False, allow_2022=False) -> str:
    """URL, or BenchError: not http(s), not loopback (unless ALLOW_REMOTE), or port 2022
    (VoiceMode's live whisper; unless ALLOW_2022)."""
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise BenchError(f"{url!r}: give an http(s) URL, e.g. http://127.0.0.1:2122/v1")
    host = u.hostname
    try:
        loop = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loop = host.lower() == "localhost"
    if not loop and not allow_remote:
        raise BenchError(f"{url}: {host} is not loopback; a remote STT may be a live one "
                         f"(never the live whisper's :2022). --allow-remote if you mean it")
    port = u.port or (443 if u.scheme == "https" else 80)
    if port == LIVE_PORT and not allow_2022:
        raise BenchError(f"{url}: port {LIVE_PORT} is VoiceMode's whisper, live on a host; run a scratch "
                         f"whisper-server on another port (127.0.0.1:2122). --allow-2022 if you mean it")
    return url


# -- scoring: ears-mail's own regex ---------------------------------------------------

def ears_mail():
    spec = importlib.util.spec_from_file_location("ears_mail_bench", EARS_MAIL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_rules(wake_path) -> dict:
    """{seat: rules} exactly as ears-mail builds them from WAKE_PATH."""
    return ears_mail().load_rules(str(wake_path), [])


def score(clip: dict, text: str, rules: dict) -> dict:
    """called: the seats TEXT calls (ears-mail's _call); hit/wrong for a positive,
    false_wake for a negative."""
    called = [s for s, r in rules.items() if r["_call"].search(text or "")]
    if clip["kind"] == "pos":
        hit = clip["seat"] in called
        return dict(called=called, hit=hit, wrong=bool(called) and not hit, false_wake=False)
    return dict(called=called, hit=False, wrong=False, false_wake=bool(called))


# -- run ------------------------------------------------------------------------------

def whisper_stt(url: str, prompt):
    """The ears' REAL client: WhisperSTT(url, prompt=...), as make_stt builds it."""
    from ears.listen.stt import WhisperSTT
    return WhisperSTT(url, prompt=prompt)


def hint_for(mode: str, wake_path, hint_text=None):
    """MODE's prompt: None for off; for on, HINT_TEXT verbatim or ears.wake.WakeHint."""
    if mode == "off":
        return None
    if hint_text is not None:
        return hint_text
    from ears.wake import WakeHint
    return WakeHint(str(wake_path))


def run(clips: list[dict], root, wake_path, url: str, *, modes=("off", "on"), hint_text=None,
        make_stt=whisper_stt, log=print, give_up=5) -> list[dict]:
    """Each clip through MAKE_STT(url, prompt) once per mode, scored. A clip whose STT
    fails is recorded with its error; the first GIVE_UP requests all failing aborts."""
    root = Path(root)
    rules = load_rules(wake_path)
    out, tried, failed = [], 0, 0
    for mode in modes:
        prompt = hint_for(mode, wake_path, hint_text)
        stt = make_stt(url, prompt)
        shown = prompt() if callable(prompt) else prompt
        log(f"mode {mode}: prompt {shown!r}")
        try:
            for c in clips:
                audio = read_wav(root / c["wav"])
                t0 = time.monotonic()
                try:
                    text, err = stt.transcribe(audio), None
                except Exception as e:  # STTError, or a fake's
                    text, err = "", f"{type(e).__name__}: {e}"
                    failed += 1
                tried += 1
                if tried == failed and tried >= give_up:
                    raise BenchError(f"the first {tried} requests to {url} all failed; last: {err}")
                row = dict(id=c["id"], kind=c["kind"], seat=c["seat"], condition=c["condition"],
                           voice=c["voice"], rate=c["rate"], mode=mode, prompt=shown, text=text,
                           error=err, ms=round(1000 * (time.monotonic() - t0)))
                row.update(score(c, text, rules) if err is None else
                           dict(called=[], hit=False, wrong=False, false_wake=False))
                out.append(row)
        finally:
            close = getattr(stt, "close", None)
            if close:
                close()
    return out


NONSPEECH = ("silence", "noise")


def summarize(results: list[dict]) -> list[dict]:
    """One row per (condition, mode), then an ALL row per mode."""
    conds = list(dict.fromkeys(r["condition"] for r in results))
    modes = list(dict.fromkeys(r["mode"] for r in results))
    rows = []
    for cond in conds + ["ALL"]:
        for mode in modes:
            rs = [r for r in results if r["mode"] == mode and (cond == "ALL" or r["condition"] == cond)]
            pos = [r for r in rs if r["kind"] == "pos" and not r["error"]]
            neg = [r for r in rs if r["kind"] != "pos" and not r["error"]]
            ns = [r for r in neg if r["kind"] in NONSPEECH]
            rows.append(dict(condition=cond, mode=mode,
                             pos=len(pos), hits=sum(r["hit"] for r in pos), wrong=sum(r["wrong"] for r in pos),
                             neg=len(neg), false_wakes=sum(r["false_wake"] for r in neg),
                             nonspeech=len(ns), nonspeech_wakes=sum(r["false_wake"] for r in ns),
                             errors=sum(1 for r in rs if r["error"])))
    return rows


def _pct(n, d) -> str:
    return f"{n}/{d} {100 * n / d:5.1f}%" if d else "-"


def report(results: list[dict], url: str = "") -> str:
    rows = summarize(results)
    lines = [f"name_hint bench: {url}",
             f"{'condition':<13} {'hint':<4} {'hit':>14} {'wrong':>6} {'false wake':>14} "
             f"{'of nonspeech':>14} {'errors':>6}"]
    for r in rows:
        lines.append(f"{r['condition']:<13} {r['mode']:<4} {_pct(r['hits'], r['pos']):>14} {r['wrong']:>6} "
                     f"{_pct(r['false_wakes'], r['neg']):>14} {_pct(r['nonspeech_wakes'], r['nonspeech']):>14} "
                     f"{r['errors']:>6}")
    for mode in dict.fromkeys(r["mode"] for r in results):
        seats = list(dict.fromkeys(r["seat"] for r in results if r["kind"] == "pos"))
        per = []
        for s in seats:
            ps = [r for r in results if r["mode"] == mode and r["seat"] == s and not r["error"]]
            per.append(f"{s} {_pct(sum(r['hit'] for r in ps), len(ps)).strip()}")
        if per:
            lines.append(f"per seat, hint {mode}: " + "; ".join(per))
    prompts = {r["mode"]: r["prompt"] for r in results}
    for mode, p in prompts.items():
        lines.append(f"prompt, hint {mode}: {p!r}")
    off = [r for r in rows if r["condition"] == "ALL" and r["mode"] == "off"]
    if off and off[0]["pos"] and off[0]["hits"] / off[0]["pos"] >= NEAR_ALL:
        lines.append(f"NOTE: hint OFF already hits {_pct(off[0]['hits'], off[0]['pos']).strip()}: this corpus "
                     f"has NOT reproduced the mishearing, so OFF vs ON proves nothing. Real clips are needed.")
    echo = [r for r in rows if r["condition"] == "ALL" and r["mode"] == "on" and r["nonspeech_wakes"]]
    if echo:
        lines.append(f"WARNING: the hint woke a seat on {echo[0]['nonspeech_wakes']} silence/noise clip(s): "
                     f"prompt echo, a design-level finding (design.md s4)")
    return "\n".join(lines)


# -- main -----------------------------------------------------------------------------

def main(argv=None, *, synth=say_synth, make_stt=whisper_stt) -> int:
    ap = argparse.ArgumentParser(prog="name_hint.py", description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("\n", 2)[2])
    ap.add_argument("--manifest", default=str(MANIFEST), help="the corpus manifest (default bench/name_hint.json)")
    ap.add_argument("--root", help="corpus + runs dir (default $EARS_EVAL_HARNESS/name-hint)")
    sub = ap.add_subparsers(dest="verb", required=True)
    b = sub.add_parser("build", help="build (or reuse) the corpus")
    b.add_argument("--rebuild", action="store_true", help="remake it even if the manifest is unchanged")
    r = sub.add_parser("run", help="build if needed, then every clip through STT, hint off and on")
    r.add_argument("--url", required=True, help="the STT base URL, e.g. http://127.0.0.1:2122/v1")
    r.add_argument("--modes", default="off,on", help="off,on (default), or one of them")
    r.add_argument("--hint", default=None, help="the prompt for mode on, verbatim (default: WakeHint "
                                                "from the test wake file)")
    r.add_argument("--only", default=None, help="only clips whose id contains this")
    r.add_argument("--allow-remote", action="store_true", help="allow a URL that is not loopback")
    r.add_argument("--allow-2022", action="store_true", help="allow port 2022 (VoiceMode's live whisper)")
    a = ap.parse_args(argv)
    try:
        root = Path(a.root).expanduser() if a.root else harness_root()
        m = load_manifest(a.manifest)
        if a.verb == "run":
            check_url(a.url, allow_remote=a.allow_remote, allow_2022=a.allow_2022)
            modes = [x.strip() for x in a.modes.split(",") if x.strip()]
            if not modes or any(x not in ("off", "on") for x in modes):
                raise BenchError(f"--modes {a.modes!r}: off, on, or off,on")
        clips = build(m, root, synth=synth, rebuild=getattr(a, "rebuild", False),
                      log=lambda s: print(s, file=sys.stderr))
        if a.verb == "build":
            return 0
        if a.only:
            clips = [c for c in clips if a.only in c["id"]]
        if not clips:
            raise BenchError("no clips to run")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        results = run(clips, root, root / "wake.json", a.url, modes=modes, hint_text=a.hint,
                      make_stt=make_stt, log=lambda s: print(s, file=sys.stderr))
        out = root / "runs" / stamp
        out.mkdir(parents=True, exist_ok=True)
        with open(out / "results.jsonl", "w") as f:
            for row in results:
                f.write(json.dumps(row) + "\n")
        text = report(results, a.url)
        (out / "report.txt").write_text(text + "\n")
        print(text)
        print(f"results: {out}", file=sys.stderr)
        return 0
    except BenchError as e:
        print(f"name_hint: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
