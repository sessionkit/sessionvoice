"""Backends: PCM in, ears events out. One interface:

    b = open_backend("auto", rate=16000)
    b.feed(pcm_bytes)      # s16le mono, any block size; never blocks on the model
    for e in b.events():   # non-blocking drain
        ...
    b.close()              # flush; the last event is eof

auto = Apple on macOS 27 (the helper built), else Nemotron where MLX imports,
else whisper at $EARS_WHISPER_URL.
"""
ORDER = ("apple", "nemotron", "whisper")

def _mod(name):
    if name == "apple":
        from . import apple as m
    elif name == "nemotron":
        from . import nemotron as m
    elif name == "whisper":
        from . import whisper as m
    else:
        raise ValueError(f"no backend {name!r}; one of {', '.join(ORDER)}")
    return m

class NoBackend(RuntimeError):
    """No ears backend can run here. str() is the one line a person reads: every
    backend tried, with its reason (`ears` prints it on stderr and exits 1)."""

    def __init__(self, reasons):
        self.reasons = dict(reasons)       # {name: why not}
        # one line, even when a reason carries an exception's own newlines
        super().__init__("no ears backend usable here: " + "; ".join(f"{n}: {' '.join(str(w).split())}" for n, w in self.reasons.items()))

def available():
    """{name: None if usable here, else why not}."""
    return {n: _mod(n).why_not() for n in ORDER}

def pick(name="auto"):
    """The backend to open: `name`, or under "auto" the first in ORDER that runs
    here. Raises NoBackend (a RuntimeError) when "auto" finds none."""
    if name != "auto":
        return name
    reasons = {}
    for n in ORDER:
        why = _mod(n).why_not()
        if why is None:
            return n
        reasons[n] = why
    raise NoBackend(reasons)

def open_backend(name="auto", rate=16000, **kw):
    n = pick(name)
    m = _mod(n)
    cls = {"apple": "AppleBackend", "nemotron": "NemotronBackend", "whisper": "WhisperBackend"}[n]
    return getattr(m, cls)(rate=rate, **kw)
