"""Voice activity: when is the user talking? Two detectors, one interface, every platform.

feed(pcm_bytes) -> speaking now (bool, raw per frame); .last_voice (audio s).
Edges (the `vad` event) come from VadEdges, which debounces the raw per-frame
decision: onset after MIN_SPEECH of voice, offset after MIN_SILENCE of quiet,
and an offset's `at` is the end of the last voiced frame, not when we noticed.

- SileroVAD: Silero v5/v6 ONNX, CPU, ~2 MB. The default (the pick over
  Apple's SpeechDetector, whose results are not readable - measured 2030-06-09).
- EnergyVAD: the Delta bot's floor-tracking gate. No dependencies; the fallback.
"""
import os
import numpy as np
from . import events

MIN_SPEECH, MIN_SILENCE = 0.064, 0.20

def silero_model_path():
    for p in (os.environ.get("EARS_SILERO_ONNX"), os.path.expanduser("~/.cache/ears/silero_vad.onnx")):
        if p and os.path.exists(p):
            return p
    return None


# The one Silero VAD model `ears install` fetches, pinned: release tag v6.2.3 of
# snakers4/silero-vad, by the commit the tag points at, checked against its SHA-256 before it
# lands. The file is unchanged from v6.2 to v6.2.3 and is the one the repo's master served.
# docs/models.md quotes these; change them together.
SILERO_TAG = "v6.2.3"
SILERO_COMMIT = "5cd7945676eb32225748052e2e6a0580e4686a08"
SILERO_URL = f"https://github.com/snakers4/silero-vad/raw/{SILERO_COMMIT}/src/silero_vad/data/silero_vad.onnx"
SILERO_SHA256 = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"
SILERO_SIZE = 2327524


class SileroFetchError(RuntimeError):
    """The pinned download failed or did not match; nothing was written at the model path."""


def _sha256_file(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_silero(dst, *, url=SILERO_URL, sha256=SILERO_SHA256, opener=None, timeout=60):
    """Download `url` to a temp file beside `dst`, check its SHA-256, and os.replace it onto
    `dst` only on a match. On a mismatch or any fetch error the temp file is deleted, nothing
    is written at `dst`, and SileroFetchError carries one line naming the URL."""
    import hashlib
    import tempfile
    if opener is None:
        import urllib.request
        opener = urllib.request.urlopen
    d = os.path.dirname(os.path.abspath(dst))
    try:
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".silero_vad.", suffix=".part")
    except OSError as e:
        why = " ".join(str(e).split()) or type(e).__name__
        raise SileroFetchError(f"could not write in {d} to fetch {url}: {why}; nothing written at {dst}") from e
    try:
        h = hashlib.sha256()
        try:
            with os.fdopen(fd, "wb") as f, opener(url, timeout=timeout) as r:
                for chunk in iter(lambda: r.read(1 << 16), b""):
                    h.update(chunk); f.write(chunk)
        except Exception as e:
            why = " ".join(str(e).split()) or type(e).__name__
            raise SileroFetchError(f"could not fetch {url}: {why}; nothing written at {dst}") from e
        got = h.hexdigest()
        if got != sha256:
            raise SileroFetchError(f"checksum mismatch for {url}: expected sha256 {sha256[:12]}, "
                                   f"got {got[:12]}; nothing written at {dst}")
        try:
            # mkstemp makes the temp 0600; give the model the mode a plain download had (0666 less umask)
            umask = os.umask(0); os.umask(umask)
            os.chmod(tmp, 0o666 & ~umask)
            os.replace(tmp, dst)
        except OSError as e:
            why = " ".join(str(e).split()) or type(e).__name__
            raise SileroFetchError(f"could not write {dst} from {url}: {why}; nothing written at {dst}") from e
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def silero_pin_check(path, *, sha256=SILERO_SHA256, tag=SILERO_TAG):
    """One line on a model already on disk: does it match the pin? Never raises, never touches it."""
    try:
        got = _sha256_file(path)
    except OSError as e:
        why = " ".join(str(e.strerror or e).split())
        return f"silero: {path} is present but could not be read to check against the pin ({tag}): {why}; left as it is"
    if got == sha256:
        return f"silero: {path} matches the pin ({tag}, sha256 {sha256[:12]})"
    return (f"silero: {path} differs from the pin ({tag}, sha256 {sha256[:12]}; this file {got[:12]}) and is "
            f"not replaced; for the pinned model, remove it and re-run `ears install`")


class EnergyVAD:
    name = "energy"

    def __init__(self, rate=16000):
        self.rate, self.floor, self.in_speech, self.last_voice, self.t, self.prob = rate, 200.0, False, None, 0.0, None

    def feed(self, pcm_bytes):
        x = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
        self.t += len(x) / self.rate
        rms = float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0
        if not self.in_speech:
            self.floor = (min(self.floor * 1.02 + 1, max(self.floor * 0.98, rms)) if rms < self.floor else self.floor * 1.005)
        start, keep = max(350.0, self.floor * 3.5), max(200.0, self.floor * 2.0)
        self.in_speech = rms > (keep if self.in_speech else start)
        if self.in_speech:
            self.last_voice = self.t
        return self.in_speech


class SileroVAD:
    """Silero over 32 ms windows (512 samples at 16 kHz, with the model's
    64-sample context), hysteresis 0.5 on / 0.35 off. CPU only (onnxruntime)."""
    name = "silero"
    WIN, CTX = 512, 64

    def __init__(self, rate=16000, model=None, on=0.5, off=0.35):
        from .extras import need
        ort = need("onnxruntime", "silero", "Silero VAD")
        if rate != 16000:
            raise ValueError("silero here is wired for 16 kHz")
        model = model or silero_model_path()
        if not model:
            raise FileNotFoundError("no silero_vad.onnx: set EARS_SILERO_ONNX or run `ears install`")
        opts = ort.SessionOptions(); opts.intra_op_num_threads = 1; opts.inter_op_num_threads = 1
        self.s = ort.InferenceSession(model, sess_options=opts, providers=["CPUExecutionProvider"])
        self.rate, self.on, self.off = rate, on, off
        self.state = np.zeros((2, 1, 128), np.float32)
        self.ctx = np.zeros(self.CTX, np.float32)
        self.pending = np.zeros(0, np.float32)
        self.t, self.in_speech, self.last_voice, self.prob = 0.0, False, None, 0.0

    def feed(self, pcm_bytes):
        x = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        self.pending = np.concatenate([self.pending, x])
        while len(self.pending) >= self.WIN:
            w, self.pending = self.pending[:self.WIN], self.pending[self.WIN:]
            inp = np.concatenate([self.ctx, w])[None, :]
            out, self.state = self.s.run(None, {"input": inp, "state": self.state, "sr": np.array(self.rate, np.int64)})
            self.ctx = w[-self.CTX:]
            self.t += self.WIN / self.rate
            self.prob = float(out[0][0])
            self.in_speech = self.prob >= (self.off if self.in_speech else self.on)
            if self.in_speech:
                self.last_voice = self.t
        return self.in_speech


def open_vad(name="auto", rate=16000):
    """'silero' | 'energy' | 'none' | 'auto' (silero when its model and onnxruntime are here)."""
    if name == "none":
        return None
    if name in ("silero", "auto"):
        try:
            return SileroVAD(rate)
        except Exception:
            if name == "silero":
                raise
    return EnergyVAD(rate)


class VadEdges:
    """Debounced edges over a raw VAD, as `vad` events. feed() returns a list."""

    def __init__(self, vad, min_speech=MIN_SPEECH, min_silence=MIN_SILENCE):
        self.vad, self.min_speech, self.min_silence = vad, min_speech, min_silence
        self.speaking, self.run_start = False, None      # debounced state; start of the current raw voiced run

    def feed(self, pcm_bytes, t=None):
        v, out = self.vad, []
        raw = v.feed(pcm_bytes)
        if not self.speaking:
            if raw:
                if self.run_start is None:
                    self.run_start = max(0.0, v.t - len(pcm_bytes) / 2 / v.rate)
                if v.t - self.run_start >= self.min_speech:
                    self.speaking = True
                    out.append(events.vad(True, self.run_start, v.name, v.prob, t))
            else:
                self.run_start = None
        elif v.last_voice is not None and v.t - v.last_voice >= self.min_silence:
            self.speaking, self.run_start = False, None
            out.append(events.vad(False, v.last_voice, v.name, v.prob, t))
        return out

    def flush(self, t=None):
        """End of stream: close an open speech run."""
        if self.speaking and self.vad.last_voice is not None:
            self.speaking = False
            return [events.vad(False, self.vad.last_voice, self.vad.name, self.vad.prob, t)]
        return []
