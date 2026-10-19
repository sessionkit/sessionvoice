"""``listen``, the spike: capture that writes what it hears.

Split so that capture outlives the call: the ears are one long-lived
process, and the waiter a separate short one.

- **capture** ("the ears"): one long-lived process with its own capture,
  VAD and chunking (not ``converse``'s capture path). It writes a
  ``partial`` per recognised chunk and a ``turn`` per end of turn through
  the ``heard`` writer, and does not return on a turn.
- **aged_turn**: the waiter's rule as a pure function over
  ``heard`` lines.

It never takes the conch and never makes a sound.

    python -m ears.listen capture --source mic --device C930e
"""

from .aged import aged_turn
from .chunker import Chunk, Chunker
from .detector import EnergyClassifier, SilenceTurnDetector, WebRtcClassifier, make_detector
from .loop import DEFAULT_HEARTBEAT_S, CaptureResult, capture
from .sink import HeardSink, MemorySink, Sink
from .sources import (
    FRAME_S,
    FRAME_SAMPLES,
    SAMPLE_RATE,
    ArraySource,
    FileSource,
    MicSource,
    Source,
    SourceError,
    open_source,
    resolve_input_device,
)
from .stt import STT, FollowingSTT, WhisperSTT

__all__ = [
    "ArraySource",
    "CaptureResult",
    "Chunk",
    "Chunker",
    "DEFAULT_HEARTBEAT_S",
    "EnergyClassifier",
    "FRAME_S",
    "FRAME_SAMPLES",
    "FileSource",
    "HeardSink",
    "MemorySink",
    "MicSource",
    "SAMPLE_RATE",
    "STT",
    "SilenceTurnDetector",
    "Sink",
    "Source",
    "SourceError",
    "WebRtcClassifier",
    "WhisperSTT",
    "FollowingSTT",
    "aged_turn",
    "capture",
    "make_detector",
    "open_source",
    "resolve_input_device",
]
