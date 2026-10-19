"""ears events v1 - the one stream format (docs/events.md).

Every stage reads and writes these: backends (ready/partial/final/error/eof),
the VAD (vad), the turn layer (turn/preview). JSON lines on the wire; unknown
fields ride in `extra` and survive a round trip; unknown types pass through.
"""
import json
from dataclasses import dataclass, field

V = 1
_CORE = ("type", "text", "start", "end", "words", "seg", "t")

@dataclass
class Event:
    type: str                  # ready | partial | final | vad | turn | preview | error | eof
    text: str = ""
    start: float = 0.0         # audio seconds since stream start
    end: float = 0.0
    words: list = field(default_factory=list)   # [(word, start, end)], finals only
    seg: int | None = None     # partial/final: the segment; a final closes it
    t: float | None = None     # wall seconds since stream start, when emitted
    extra: dict = field(default_factory=dict)   # every other field (backend, speech, at, reason...)

    def __getitem__(self, k):  # e["load_s"], e["speech"]: one lookup for core and extra
        return getattr(self, k) if k in _CORE else self.extra[k]

    def get(self, k, default=None):
        try: return self[k]
        except KeyError: return default

    @property
    def raw(self):             # the prototype's name for the whole object
        return self.to_dict()

    def to_dict(self):
        d = {"type": self.type}
        if self.type in ("partial", "final", "turn", "preview"):
            d["text"] = self.text
        if self.seg is not None:
            d["seg"] = self.seg
        if self.type in ("partial", "final", "turn", "preview"):
            d["start"], d["end"] = round(self.start, 3), round(self.end, 3)
        if self.type == "final":
            d["words"] = [[w[0], round(w[1], 3), round(w[2], 3)] for w in self.words]
        d.update(self.extra)
        if self.t is not None:
            d["t"] = round(self.t, 4)
        return d

    def to_json(self):
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(", ", ": "))

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        return cls(type=d.pop("type"), text=d.pop("text", "") or "", start=float(d.pop("start", 0.0) or 0.0),
                   end=float(d.pop("end", 0.0) or 0.0), words=[tuple(w) for w in d.pop("words", []) or []],
                   seg=d.pop("seg", None), t=d.pop("t", None), extra=d)

    @classmethod
    def from_json(cls, line):
        return cls.from_dict(json.loads(line))


def ready(backend, model, rate, load_s, **kw):
    return Event("ready", extra=dict(v=V, backend=backend, model=model, rate=rate, load_s=round(load_s, 4), **kw))

def vad(speech, at, by, p=None, t=None):
    x = dict(speech=speech, at=round(at, 4), by=by)
    if p is not None: x["p"] = round(p, 3)
    return Event("vad", t=t, extra=x)

def error(message, fatal=False):
    return Event("error", extra=dict(message=str(message), fatal=fatal))

def eof(audio_s, t=None):
    return Event("eof", t=t, extra=dict(audio_s=round(audio_s, 4)))
