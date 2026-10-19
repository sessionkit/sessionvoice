"""Smart Turn: the `smart` turn stage. Silence decides WHEN to look, and Smart
Turn v3 decides HOW LONG to wait. The smart half of ears (docs/events.md, "Turn
stages"). It is ears.turns.SilenceTurns with a gate: SmartTurnGate is
asked once at each VAD offset (0.2 s of quiet, VadEdges' MIN_SILENCE) and
returns the wait for that pause.

THE RULE, MEASURED (apple-fm/research/streaming-eval.md 2.5, 20 turns
with 233 pauses):
- At every VAD offset, once 0.2 s of quiet has passed, ask Smart Turn "does
  this sound finished?" of the last 8 s of audio.
- If yes (p > 0.5), close after 1.0 s of quiet in all. If no, wait 2.0 s.
- Results: 9/20 turns cut off early, against 18/20 for a 0.7 s silence rule.
  End latency p50 was 1.23 s.

WHAT IT CAN'T DO: know the user will keep talking. It called 82% of mid-turn
sentence ends "complete", because they do sound finished. Its real strength is
the veto: after the MLX port called 1 of 18 hesitations "complete", the 2 s wait
covers them. The pause lengths in that eval were synthetic, and real thinking
pauses run longer. So both waits are constructor arguments, to be retuned
on a live-mic test.

Backends: MLX (mlx-audio's port, 1.2 ms per call on the M5 GPU; the Mac
default) or ONNX v3.2 fp32 (19 ms on one CPU core; Linux; needs
`transformers` for the Whisper feature extractor). The MLX port agreed with
ONNX v3.2 on 215 of 233 decisions (corr 0.92), so it is probably not the
identical weights (INFERRED). On the hesitation veto it did BETTER: 1/18
against 3/18.
"""
import os
import numpy as np

SR = 16000
WINDOW_S = 8.0          # Smart Turn's input: the last 8 s, left zero-padded
WAIT_COMPLETE = 1.0     # total quiet before closing when it sounds finished
WAIT_INCOMPLETE = 2.0   # ... when it does not
THRESHOLD = 0.5
VETO_WAIT = 2.5         # veto mode: "not finished" waits at least this long


def _first_existing(*paths):
    for p in paths:
        if p and os.path.exists(os.path.expanduser(p)):
            return os.path.expanduser(p)
    return None


class SmartTurnModel:
    """probability(float32 mono 16 kHz audio) -> P(the turn is complete)."""

    def __init__(self, backend="auto", model=None):
        if backend == "auto":
            try:
                import mlx_audio.vad.utils  # noqa: F401
                backend = "mlx"
            except ImportError:
                backend = "onnx"
        self.backend = backend
        if backend == "mlx":
            from mlx_audio.vad.utils import load
            path = model or _first_existing(os.environ.get("EARS_SMART_TURN_MLX"),
                                            "~/.cache/ears/smart-turn-v3-mlx") or "mlx-community/smart-turn-v3"
            self.model_id = path
            self._m = load(path)
            self._run = lambda a: float(self._m.predict_endpoint(a).probability)
        elif backend == "onnx":
            from .extras import need
            ort = need("onnxruntime", "smart-turn", "Smart Turn (onnx)")
            WhisperFeatureExtractor = need("transformers", "smart-turn", "Smart Turn (onnx)").WhisperFeatureExtractor
            path = model or _first_existing(os.environ.get("EARS_SMART_TURN_ONNX"),
                                            "~/.cache/ears/smart-turn-v3.2-gpu.onnx")
            if not path:
                raise FileNotFoundError("Smart Turn ONNX: set EARS_SMART_TURN_ONNX or put "
                                        "smart-turn-v3.2-gpu.onnx in ~/.cache/ears/")
            self.model_id = path
            o = ort.SessionOptions(); o.inter_op_num_threads = 1; o.intra_op_num_threads = 1
            sess = ort.InferenceSession(path, sess_options=o, providers=["CPUExecutionProvider"])
            fe = WhisperFeatureExtractor(chunk_length=int(WINDOW_S))

            def run(a):   # pipecat's reference preprocessing (the eval's)
                f = fe(a, sampling_rate=SR, return_tensors="np", padding="max_length",
                       max_length=int(WINDOW_S * SR), truncation=True,
                       do_normalize=True).input_features.astype(np.float32)
                return float(sess.run(None, {"input_features": f})[0].reshape(-1)[0])
            self._run = run
        else:
            raise ValueError(f"smart turn backend {backend!r}: auto | mlx | onnx")
        self.probability(np.zeros(SR, np.float32))     # warm up: the first call compiles

    def probability(self, audio):
        a = np.asarray(audio, np.float32)[-int(WINDOW_S * SR):]
        if len(a) < WINDOW_S * SR:
            a = np.pad(a, (int(WINDOW_S * SR) - len(a), 0))
        return self._run(a)


def _f32(pcm):
    """s16le bytes (or an array) -> float32 in [-1, 1)."""
    if isinstance(pcm, (bytes, bytearray, memoryview)):
        return np.frombuffer(pcm, "<i2").astype(np.float32) / 32768.0
    a = np.asarray(pcm)
    return a.astype(np.float32) / 32768.0 if a.dtype == np.int16 else a.astype(np.float32)


class SmartTurnGate:
    """The gate for SilenceTurns(gate=...): asked once at each VAD offset with
    the turn's audio so far and its text. Returns (wait_s, p). The wait replaces
    the join rule for that pause.

    mode="shorten": the eval's rule. Wait 1.0 s if it sounds complete, else
      2.0 s. It is fast, and it cuts read speech at sentence ends.
    mode="veto": Smart Turn only LENGTHENS the text-aware join rule
      (ears.turns: 1.6 s, or 2.2 s when the text looks mid-thought). When it
      says "not finished", wait at least wait_incomplete. It can't cut off more
      often than the silence stage, by construction.
    """

    def __init__(self, model=None, mode=None, wait_complete=WAIT_COMPLETE, wait_incomplete=WAIT_INCOMPLETE,
                 threshold=THRESHOLD, backend="auto"):
        self.model = model or SmartTurnModel(backend=backend)
        self.mode = mode or os.environ.get("EARS_SMART_MODE", "veto")
        if self.mode not in ("shorten", "veto"):
            raise ValueError(f"smart turn mode {self.mode!r}: shorten | veto")
        if self.mode == "veto" and wait_incomplete == WAIT_INCOMPLETE:
            wait_incomplete = VETO_WAIT
        self.wait_complete, self.wait_incomplete, self.threshold = wait_complete, wait_incomplete, threshold

    def __call__(self, pcm, text=None):
        p = round(self.model.probability(_f32(pcm)), 3)
        done = p > self.threshold
        if self.mode == "shorten":
            return (self.wait_complete if done else self.wait_incomplete), p
        from .turns import TURN_JOIN, TURN_JOIN_OPEN, mid_thought
        base = TURN_JOIN_OPEN if mid_thought(text or "") else TURN_JOIN
        return (base if done else max(base, self.wait_incomplete)), p


def TurnDetector(previews=False, commit=None, have_vad=True, gate=None, redecode=None, **kw):
    """The `smart` turn stage: SilenceTurns with the Smart Turn gate and,
    when it loads, the Parakeet re-decode (ears.redecode). Same four calls."""
    from .turns import SilenceTurns
    if not have_vad:
        raise ValueError("the smart stage needs a VAD: the gate is asked at VAD offsets")
    if redecode is None and os.environ.get("EARS_REDECODE", "1") != "0":
        try:
            from .redecode import Redecoder
            redecode = Redecoder()
        except ImportError:
            redecode = None
    return SilenceTurns(previews=previews, have_vad=True, commit=commit,
                        gate=gate or SmartTurnGate(**kw), redecode=redecode)
