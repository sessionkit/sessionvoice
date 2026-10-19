"""Parakeet re-decode: the `redecode` seam of SilenceTurns (the smart half of ears).

Asked at each preview and at turn close with the WHOLE turn's audio so far.
Its text replaces the STT's, and `by` becomes "redecode". It re-reads the turn
with a full-context offline model instead of trusting a streaming decoder's
running guess.

MEASURED (apple-fm/research/streaming-eval.md), Parakeet TDT 0.6B v3
under mlx-audio 0.5.4:
- Previews: 3.9% WER at the moment speech stops. Apple with fastResults
  gets 13%, Nemotron 9-14%.
- The final: 90 ms p50 for a 33 s turn. It lifts Nemotron's 2.4-4.1% to 1.5%.
  Apple's own final is ALREADY 1.5% on turns, so with the Apple backend the
  final re-decode buys nothing measurable. OPEN: the seam asks the same call
  for previews and the close, so it can't keep Apple's final
  yet, and it re-decodes for both: the previews and the
  close.
- Cost: 3.4 GB of RAM, and 5-21% of the GPU when previewing every 0.5 s.
- NEVER mlx-audio's chunked Parakeet streaming: it drops words (10-40% WER
  at 5/1, 5/2 and 2/0.5 s chunk/overlap).
"""
import os
import numpy as np

SR = 16000
MIN_S = 0.3        # shorter than this is a click, not a phrase: keep the STT's text
DEFAULT_MODEL = "mlx-community/parakeet-tdt-0.6b-v3"


def _f32(pcm):
    if isinstance(pcm, (bytes, bytearray, memoryview)):
        return np.frombuffer(pcm, "<i2").astype(np.float32) / 32768.0
    a = np.asarray(pcm)
    return a.astype(np.float32) / 32768.0 if a.dtype == np.int16 else a.astype(np.float32)


class Redecoder:
    """redecode(pcm) -> text | None. Loads once (about 2 s), then ~0.1 s per call."""

    def __init__(self, model=None):
        from .extras import need
        mx = need("mlx.core", "nemotron", "The Parakeet re-decode")
        load_model = need("mlx_audio.stt.utils", "nemotron", "The Parakeet re-decode").load_model
        self._mx = mx
        self.model_id = model or os.environ.get("EARS_PARAKEET") or DEFAULT_MODEL
        self._m = load_model(self.model_id)
        self._m.generate(mx.array(np.zeros(SR, np.float32)))      # warm up: the first call compiles

    def __call__(self, pcm):
        a = _f32(pcm)
        if len(a) < MIN_S * SR:
            return None
        text = self._m.generate(self._mx.array(a)).text.strip()
        return text or None
