"""Turns: finals + audio clock -> turns. Pure Python, backend-agnostic.

Constants are the Delta bot's (calls.py), MEASURED on a 12 min call
2030-06-09: within-thought gaps <= 0.98 s, turn gaps >= 2.46 s.
"""
import re, time
from .events import Event

TURN_JOIN = 1.60
TURN_JOIN_OPEN = 2.20
OPEN_END = re.compile(
    r"(?:\b(?:and|or|but|so|if|then|the|a|an|to|of|that|with|because|when|which|for|is|are|was|were|"
    r"it'?s|i|we|you|they|my|your|about|like|as|at|in|on|by|from|would|could|should|can|will|do|does|did|"
    r"there|this|these|those|what|how|why|um+|uh+)\b|[,;:\-])\s*$", re.I)

def mid_thought(text):
    t = (text or "").strip().rstrip("\"'")
    return bool(t) and (t[-1] not in ".!?" or bool(OPEN_END.search(t)))

class Turns:
    def __init__(self, join=TURN_JOIN, join_open=TURN_JOIN_OPEN, hold_on_partials=True):
        """hold_on_partials: a partial past the last final holds the turn. A
        crutch for running WITHOUT a VAD - a partial's range end is the audio
        clock, not speech, so in noise it holds turns open for nothing
        (replay of a noisy call, 2030-06-09). Off with a VAD."""
        self.join, self.join_open, self.hold_on_partials = join, join_open, hold_on_partials
        self.parts, self.last_end, self.heard_since = [], None, False

    def on_event(self, e):
        if e.type == "final" and e.text.strip():
            self.parts.append(e.text.strip())
            self.last_end = e.words[-1][2] if e.words else e.end
            self.heard_since = False
        elif self.hold_on_partials and e.type == "partial" and e.text.strip() and self.last_end is not None and e.end > self.last_end:
            self.heard_since = True        # the user is talking again: hold the turn

    def tick(self, audio_now, last_voice=None):
        """Call often with the audio clock and the VAD's last-voice time (audio s).
        Returns the turn text when it closes. The STT's partials come ~1 s apart
        (Apple, measured), too coarse to see the user start again inside a 1.6 s join;
        the VAD is what holds the turn open."""
        if not self.parts or self.heard_since:
            return None
        quiet_from = max(self.last_end, last_voice or 0.0)
        wait = self.join_open if mid_thought(self.parts[-1]) else self.join
        if audio_now - quiet_from >= wait:
            turn, self.parts, self.last_end = " ".join(self.parts), [], None
            return turn
        return None


class SilenceTurns:
    """The silence fallback, as a TURN STAGE. Every turn stage (this one, and
    the Smart Turn TurnDetector) implements the same four calls, and the
    Pipeline (cli.py) drives them in stream order, in-process, because Smart
    Turn and a Parakeet re-decode need the audio, not only the events:

        pcm(pcm_bytes, audio_end)   the SAME bytes the backend saw; audio_end is
                                    the audio clock after them
        event(e)      -> [Event]    every backend and vad event, in stream order
        tick(audio_now) -> [Event]  after every block (20 ms)
        flush(audio_now) -> [Event] end of stream

    Each returns the turn / preview events it decided (docs/events.md); the
    Pipeline stamps `t`. It is built with commit=backend.commit.

    THE TURN INCLUDES THE OPEN PARTIAL. Apple holds the last phrase before a
    silence open until the user speaks again (measured 2030-06-09: 4 s of quiet, no
    final), so a turn built from finals alone loses its last phrase to the next
    turn. When silence says "done" and a segment is still open, this stage
    calls commit() and waits up to COMMIT_WAIT for that segment's final, then
    closes the turn - on the partial's text if the final never came.

    Two seams for the smarter layers (smartturn.py / redecode.py):

      gate(pcm, text) -> wait_s | (wait_s, p) | (wait_s, p, complete) | None
          asked once at each end of speech (a vad offset), with the turn's
          audio so far (s16le, PREROLL before its first voice) and its text.
          The wait replaces the join rule for that pause; None keeps the rule.
          reason is "smart-turn" only when the model judged it complete
          (`complete`, else p > 0.5); a fallback wait that fires is "silence".
      redecode(pcm) -> text | None
          asked at each preview and when the turn closes, with the turn's
          audio; its text replaces the STT's and `by` becomes "redecode".
    """
    name = "silence"
    COMMIT_WAIT = 0.6      # WALL seconds: in fast replay the audio clock outruns the backend
    PREROLL = 0.5

    def __init__(self, previews=False, have_vad=True, commit=None, join=TURN_JOIN, join_open=TURN_JOIN_OPEN,
                 gate=None, redecode=None, rate=16000):
        from .previews import Previews
        self.join, self.join_open, self.have_vad, self.commit = join, join_open, have_vad, commit
        self.gate, self.redecode, self.rate = gate, redecode, rate
        self.prev, self.emit_previews = Previews(), previews
        self.speaking, self.last_voice, self.last_final_seg, self.consumed_seg = False, None, 0, 0
        self.buf, self.buf_end = bytearray(), 0.0      # the turn's audio (+PREROLL); audio time of its end
        self.gate_wait, self.gate_p = None, None
        self.onset = False                             # a vad onset since the last close: the turn has begun
        self._reset()

    def _reset(self):
        self.finals, self.partial, self.pending = [], None, None   # [(text, start, end)]; (seg, text, start, end); (seg, wall deadline)
        self.gate_wait = self.gate_p = self.gate_done = None

    def turn_pcm(self):
        """The open turn's audio, s16le mono, from PREROLL before its first voice."""
        return bytes(self.buf)

    def text_so_far(self):
        parts = [f[0] for f in self.finals] + ([self.partial[1]] if self.partial else [])
        return " ".join(p for p in parts if p)

    def pcm(self, pcm_bytes, audio_end):
        self.buf += pcm_bytes
        self.buf_end = audio_end
        if not self.onset and not self.text_so_far():          # between turns: keep only the pre-roll
            # (not `not speaking`: between the user's first offset and the STT's first
            # partial, 0.8 s+, that trimmed away the turn's opening)
            keep = int(self.PREROLL * self.rate) * 2
            if len(self.buf) > keep:
                del self.buf[:len(self.buf) - keep]

    def event(self, e):
        if e.type == "vad":
            self.speaking = bool(e.get("speech"))
            self.onset = self.onset or self.speaking
            if self.pending:
                return []        # decided: the verdict that decided it stands
            self.gate_wait = self.gate_p = self.gate_done = None
            if not self.speaking:
                self.last_voice = e.get("at")
                if self.gate:
                    r = self.gate(self.turn_pcm(), self.text_so_far())
                    if isinstance(r, tuple):
                        r, self.gate_p, *rest = r
                        self.gate_done = rest[0] if rest else (self.gate_p is not None and self.gate_p > 0.5)
                    self.gate_wait = r
            return []
        if e.seg is not None and e.type in ("partial", "final") and e.seg <= self.consumed_seg:
            return []            # its turn already closed on the partial: never a second turn
        if e.type == "partial" and e.text.strip() and (e.seg or 0) > self.last_final_seg:
            self.partial = (e.seg, e.text.strip(), e.start, e.end)
        elif e.type == "final":
            self.last_final_seg = max(self.last_final_seg, e.seg or 0)
            if self.partial and (e.seg is None or e.seg >= self.partial[0]):
                self.partial = None
            if e.text.strip():
                end = e.words[-1][2] if e.words else e.end
                self.finals.append((e.text.strip(), e.words[0][1] if e.words else e.start, end))
                return self._preview(self.prev.on_phrase(e.text, end))
        return []

    def tick(self, audio_now):
        out = self._preview(self.prev.tick(audio_now))
        if self.pending:
            seg, deadline = self.pending
            if self.partial is None or time.monotonic() >= deadline:
                out += self._close(audio_now, "silence")
            return out
        text = self.text_so_far()
        if not text or (self.have_vad and self.speaking):
            return out
        ends = [f[2] for f in self.finals] + ([self.partial[3]] if self.partial and not self.have_vad else [])
        quiet_from = self.last_voice if self.have_vad and self.last_voice is not None else max(ends or [0.0])
        wait = self.gate_wait if self.gate_wait is not None else (self.join_open if mid_thought(text) else self.join)
        if audio_now - quiet_from < wait:
            return out
        self.wait_s = round(audio_now - quiet_from, 3)
        if self.partial and self.commit:
            self.commit()
            self.pending = (self.partial[0], time.monotonic() + self.COMMIT_WAIT)
            return out
        return out + self._close(audio_now, "silence")

    def _close(self, audio_now, reason):
        text = self.text_so_far()
        if not text:
            self._reset(); return []
        spans = [(f[1], f[2]) for f in self.finals] + ([(self.partial[2], self.partial[3])] if self.partial else [])
        start = min(s for s, _ in spans)
        end = max([e for _, e in spans] + ([self.last_voice] if self.last_voice else []))
        n = self.prev.on_turn(text, audio_now).turn
        by = "stt"
        if self.redecode:
            better = self.redecode(self.turn_pcm())
            if better:
                text, by = better, "redecode"
        x = dict(turn=n, reason=("smart-turn" if self.gate_done and reason == "silence" else reason), by=by)
        if reason == "silence":
            x["wait_s"] = getattr(self, "wait_s", None)
        if self.gate_p is not None:
            x["p"] = self.gate_p
        if self.partial and by == "stt":
            x["partial_tail"] = True        # the final never came: the last phrase is volatile text
        if self.partial:
            self.consumed_seg = max(self.consumed_seg, self.partial[0])
        self._reset()
        self.onset = self.speaking             # the user may already be talking again
        keep = int(self.PREROLL * self.rate) * 2
        if len(self.buf) > keep:
            del self.buf[:len(self.buf) - keep]
        return [Event("turn", text, start, end, extra=x)]

    def flush(self, audio_now):
        return self._close(audio_now, "eof")

    def _preview(self, p):
        if not (p and self.emit_previews):
            return []
        text, by = p.text, "stt"
        if self.redecode:
            better = self.redecode(self.turn_pcm())
            if better:
                text, by = better, "redecode"
        x = dict(turn=p.turn, seq=p.seq, by=by)
        if p.supersedes:
            x["supersedes"] = p.supersedes
        return [Event("preview", text, p.turn_start, p.at, extra=x)]


def open_turns(name, previews=False, have_vad=True, commit=None):
    """'none' | 'silence' | 'smart' (the TurnDetector, in ears.smartturn: it
    may be SilenceTurns built with its gate= and redecode=)."""
    if name in (None, "none"):
        return None
    if name == "silence":
        return SilenceTurns(previews=previews, have_vad=have_vad, commit=commit)
    if name == "smart":
        from .smartturn import TurnDetector      # the smart stage; same four calls
        return TurnDetector(previews=previews, commit=commit)
    raise ValueError(f"no turn stage {name!r}: none | silence | smart")
