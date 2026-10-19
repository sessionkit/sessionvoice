"""Silero VAD as the frame classifier.

It slots into :class:`~ears.listen.detector.SilenceTurnDetector` as the
``classifier``: the SAME end-of-turn rule (a turn opens on a short run of
speech, ends after ``silence_s`` of non-speech), with the speech/non-speech
call made by Silero instead of webrtcvad. webrtcvad at aggressiveness 3 calls
many non-speech sounds speech, and whisper then hallucinates words into them
("Bye.", "Mmm."): measured false turns in a silent room. Silero is trained to tell
speech from noise.

The model is Silero VAD v5 as ONNX (MIT). It needs ``onnxruntime`` and the
``silero_vad.onnx`` file; neither comes with the base install (onnxruntime is
in the ``capture`` and ``silero`` extras), so this is opt-in: ``--detector silero`` with ``EARS_SILERO_MODEL`` (or
``--silero-model``) naming the file. Nothing here is imported unless asked for.

Framing: Silero v5 at 16 kHz takes 512-sample windows with the previous
window's last 64 samples as context. The loop's frames are 30 ms (480
samples), so frames are rebuffered; each frame is classified by the most
recent window's probability (at most one 32 ms window behind). Hysteresis as
in Silero's own ``get_speech_timestamps``: speech starts at ``threshold``, and
once on, stays on until the probability falls below ``threshold - 0.15``.

The capture's heartbeat carries the highest probability any window scored in
its period (``speech_prob_max``): a 30 ms frame runs 0, 1 or 2
windows, and every one counts, not only the latest. It tells "the user's voice was
too quiet to reach the mic" apart from "silero heard it and scored it under
the threshold". :meth:`SileroClassifier.take_prob_max` hands it over and resets.
``frame_prob_max`` is the same max over the windows of the latest frame alone, so a
missed-reply clip (:mod:`ears.listen.missed`) keeps its own max without taking the
heartbeat's.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np

from .. import envnames
from .sources import SAMPLE_RATE

WINDOW = 512  # samples per Silero call at 16 kHz
CONTEXT = 64  # samples of the previous window prepended (Silero v5)
DEFAULT_THRESHOLD = 0.5
MODEL_ENV = "EARS_SILERO_MODEL"


class SileroError(RuntimeError):
    pass


def model_path(explicit: Optional[str] = None) -> Path:
    raw = explicit or envnames.get(MODEL_ENV)
    if not raw:
        raise SileroError(f"Silero VAD needs its model file: pass --silero-model PATH or set {MODEL_ENV} (silero_vad.onnx, v5)")
    p = Path(raw).expanduser()
    if not p.is_file():
        raise SileroError(f"Silero model not found: {p}")
    return p


class SileroClassifier:
    """``classify(frame) -> bool`` backed by Silero VAD v5 (onnxruntime)."""

    name = "silero"

    def __init__(self, model: Optional[str] = None, *, threshold: float = DEFAULT_THRESHOLD, session=None) -> None:
        if session is None:
            from ..extras import need

            ort = need("onnxruntime", "silero", "Silero VAD")
            opts = ort.SessionOptions()
            opts.inter_op_num_threads = 1
            opts.intra_op_num_threads = 1
            session = ort.InferenceSession(str(model_path(model)), sess_options=opts, providers=["CPUExecutionProvider"])
        self._session = session
        self.threshold = threshold
        self.off_threshold = max(0.0, threshold - 0.15)
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(CONTEXT, dtype=np.float32)
        self._buf = np.zeros(0, dtype=np.float32)
        self._sr = np.array(SAMPLE_RATE, dtype=np.int64)
        self.prob = 0.0  # the latest window's speech probability
        self.prob_max: Optional[float] = None  # the highest since the last take_prob_max(); None = no window
        self.frame_prob_max: Optional[float] = None  # the highest of the windows the LAST frame ran; None = none
        self._on = False

    def _window(self, x: np.ndarray) -> float:
        inp = np.concatenate([self._context, x])[None, :]
        out, self._state = self._session.run(None, {"input": inp, "state": self._state, "sr": self._sr})
        self._context = x[-CONTEXT:]
        return float(out[0][0])

    def take_prob_max(self) -> Optional[float]:
        """The highest window probability since the last call (None if no window ran), and reset."""
        m, self.prob_max = self.prob_max, None
        return m

    def __call__(self, frame: np.ndarray) -> bool:
        self._buf = np.concatenate([self._buf, frame.astype(np.float32) / 32768.0])
        self.frame_prob_max = None
        while len(self._buf) >= WINDOW:
            self.prob = self._window(self._buf[:WINDOW])
            if self.frame_prob_max is None or self.prob > self.frame_prob_max:
                self.frame_prob_max = self.prob
            if self.prob_max is None or self.prob > self.prob_max:
                self.prob_max = self.prob
            self._buf = self._buf[WINDOW:]
        if self._on:
            self._on = self.prob >= self.off_threshold
        else:
            self._on = self.prob >= self.threshold
        return self._on
