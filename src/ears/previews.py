"""Previews: the turn so far, sent to the agent WHILE the user is still talking.

Previews give the agent time to look things up while the user is still
talking, and then to respond as though it all came from memory.
A preview is never a turn: the agent may
think, search and read on it, but only the turn (Turns.tick) says "reply now".

Each preview carries the WHOLE turn so far (idempotent: the latest wins) and
supersedes the previous preview of the same turn; the turn supersedes the last
preview. That is the fleet's own Supersedes convention, so a reader who only
looks at the newest mail in the thread always sees the right thing.

Pure Python, clock-agnostic (audio seconds or wall seconds, the caller's
choice), backend-agnostic: whisper chunks and Apple finals both feed it.
"""
from dataclasses import dataclass, field

FIRST_AFTER = 3.0     # the turn must be this old (s) before its first preview
EVERY = 5.0           # then at most one preview per this many seconds
MIN_WORDS = 8         # and only when this many NEW words arrived since the last

@dataclass
class Preview:
    turn: int          # turn number, 1-based
    seq: int           # preview number within the turn, 1-based
    text: str          # the whole turn so far
    at: float          # clock time it was emitted
    turn_start: float  # clock time the turn's first phrase arrived
    supersedes: int | None = None   # seq of the preview this replaces

@dataclass
class TurnOut:
    turn: int
    text: str
    at: float
    turn_start: float
    previews: list = field(default_factory=list)   # the Previews sent for it

class Previews:
    def __init__(self, first_after=FIRST_AFTER, every=EVERY, min_words=MIN_WORDS):
        self.first_after, self.every, self.min_words = first_after, every, min_words
        self.turn_n = 0
        self._reset()

    def _reset(self):
        self.parts, self.start, self.sent, self.words_at_last, self.last_at = [], None, [], 0, None

    def on_phrase(self, text, now):
        """A phrase was finalised (Apple FINAL, or a whisper chunk came back)."""
        text = (text or "").strip()
        if not text:
            return None
        if self.start is None:
            self.turn_n += 1
            self.start = now
        self.parts.append(text)
        return self.tick(now)

    def tick(self, now):
        """Maybe emit a preview. Call on each phrase and on a timer."""
        if self.start is None:
            return None
        words = sum(len(p.split()) for p in self.parts)
        if words - self.words_at_last < self.min_words:
            return None
        if not self.sent and now - self.start < self.first_after:
            return None
        if self.sent and now - self.last_at < self.every:
            return None
        p = Preview(self.turn_n, len(self.sent) + 1, " ".join(self.parts), now, self.start,
                    self.sent[-1].seq if self.sent else None)
        self.sent.append(p)
        self.words_at_last, self.last_at = words, now
        return p

    def on_turn(self, text, now):
        """The turn closed (Turns.tick returned text): close it out."""
        out = TurnOut(self.turn_n, text, now, self.start if self.start is not None else now, list(self.sent))
        self._reset()
        return out
