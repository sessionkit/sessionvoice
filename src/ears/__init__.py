"""ears - one listening engine for coding agents and the Delta bot.

audio (s16le mono PCM) -> backend (Apple | Nemotron | whisper) -> events
(docs/events.md) -> VAD edges -> turns and previews. Backends share one
interface: feed(pcm_bytes), events() (non-blocking drain), close().
"""
from .events import Event
from .turns import Turns
from .backends import open_backend, available

__all__ = ["Event", "Turns", "open_backend", "available"]
