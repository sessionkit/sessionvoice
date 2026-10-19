"""Settled-word partials from a streaming backend (Nemotron), beside the chunked ones.

Nemotron for the partials, Parakeet for the final, and the partials are settled
words. Measured first (``~/.<agent>/research/2030-06-09-nemotron-
partials``, 3 calls, 349 turns): the partials go from ~20% off the kept line to 8-14%, with
twice the updates; the first words come no sooner (a median of ~2.05 s either way).

Nemotron's partial is the whole segment so far, while the kept line's partials APPEND, one
chunk each, and everything downstream - the wake rules, the speaker side-car's spans, the
speaker split, echo, Talk - reads them that way. This layer turns the one into the other.
Of each Nemotron partial it passes on only the words that have stopped changing: all but
the last, which may still be growing ("Ameri" -> "Americans"), and never a word twice; the
segment's final flushes the rest. Nemotron only ever adds tokens, so an earlier word never
changes; should one ever differ from what was written, what was written stands (counted in
``Settled.rewrites``, never rewritten). The kept line (the turn) is the final engine's
re-decode, as before; the partials' text is only ever a stand-in for it.

The backend is :class:`ears.backends.nemotron.NemotronBackend`, a child process (the model
never shares the audio loop's interpreter, see ``backends/proc.py``). It runs its own VAD
and closes a segment after 1 s of quiet, so a segment's final usually lands before the
turn's 2 s silence does; the turn's end commits it anyway, with a bounded wait. The capture
feeds it every frame once it has said ``ready`` and never before: the child reads nothing
while its model loads, and a full pipe would block the audio loop. Until then, and whenever
the child is down, the partials come from the final engine chunk by chunk, as before.

Which engine writes the partials is ``partials`` in ears.json (``final`` or ``nemotron``),
looked at as each turn opens (:class:`Partials`), so the phone's settings app flips it with
no restart - the same way ``final`` is followed (:class:`FollowingSTT`). A flag pins it.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

import numpy as np

from .sources import SAMPLE_RATE
from .stt import conf_partials

RETRY_AFTER_S = 60.0   # a child that died is not restarted for this long (stream seconds)
FINAL_WAIT_S = 0.5     # the most a turn's end waits for a committed segment's final


class Settled:
    """The settled-word rule over one backend's partial/final events: what to APPEND."""

    def __init__(self) -> None:
        self.seg = None
        self.emitted: list[str] = []   # the words written for the current segment
        self.t_prev = 0.0              # where the last span written in this segment ended
        self.rewrites = 0              # a written word the backend later said differently

    def take(self, e) -> Optional[tuple[str, float, float]]:
        """``(text, t0, t1)`` to append for a partial or final event; None when nothing new.

        A partial's last word is held back (it may still grow); a final has no last word
        to hold. Spans tile the segment: each starts where the one before ended."""
        if e.type not in ("partial", "final"):
            return None
        if e.seg != self.seg:
            self.seg, self.emitted, self.t_prev = e.seg, [], float(e.start)
        words = e.text.split()
        settled = words if e.type == "final" else words[:-1]
        n = len(self.emitted)
        if settled[:n] != self.emitted:
            self.rewrites += 1
        new = settled[n:]
        self.emitted = list(settled) if len(settled) >= n else self.emitted
        if not new:
            return None
        t0, t1 = self.t_prev, max(self.t_prev, float(e.end))
        self.t_prev = t1
        return " ".join(new), t0, t1


class Streamer:
    """A streaming backend as the capture's partials: feed frames, take spans to append."""

    def __init__(self, backend, *, rate: int = SAMPLE_RATE) -> None:
        # the capture's frame loop must never wait on the child. A backend with a
        # live mode (ProcessBackend) feeds without blocking, keeps frames whole, drops and
        # counts whole frames past a bound, fails the stream past a longer one, and kills
        # its child on close.
        live = getattr(backend, "live", None)
        if callable(live):
            live(rate=rate)
        self.b = backend
        self.name = getattr(backend, "name", "stream")
        self.rate = rate
        self.ready = False
        self.failed: Optional[str] = None
        self.settled = Settled()
        self.open_seg = None       # a segment the child has started and not yet finalised
        self.t_offset: Optional[float] = None   # stream time of the first frame fed
        self.info: dict = {}

    def feed(self, frame: np.ndarray, t: float) -> None:
        """Feed one frame starting at stream time ``t``; dropped until the child is ready."""
        if not self.ready or self.failed:
            return
        if self.t_offset is None:
            self.t_offset = t
        self.b.feed(np.asarray(frame, dtype=np.int16).tobytes())

    def drain(self) -> list[tuple[str, float, float]]:
        """Spans to append, from every event the child has sent since the last call."""
        out = []
        for e in self.b.events():
            if e.type == "ready":
                self.ready = True
                self.info = {k: e.get(k) for k in ("backend", "model", "chunk_s", "vad", "load_s")}
            elif e.type == "error":
                if e.get("fatal"):
                    self.failed = str(e.get("message", "error"))
            elif e.type == "eof":
                self.failed = self.failed or "backend ended"
            elif e.type in ("partial", "final"):
                self.open_seg = e.seg if e.type == "partial" else None
                span = self.settled.take(e)
                if span is not None:
                    off = self.t_offset or 0.0
                    out.append((span[0], span[1] + off, span[2] + off))
        return out

    def finish(self, timeout: float = FINAL_WAIT_S) -> list[tuple[str, float, float]]:
        """The turn ended: commit the open segment, if any, and take what it still held.
        Bounded: a child that does not answer in ``timeout`` costs nothing more."""
        out = self.drain()
        if self.open_seg is None or self.failed or not self.ready:
            return out
        self.b.commit()
        deadline = time.monotonic() + timeout
        while self.open_seg is not None and self.failed is None and time.monotonic() < deadline:
            time.sleep(0.01)
            out += self.drain()
        return out

    def close(self) -> None:
        try:
            self.b.close()
        except Exception:  # noqa: BLE001 - a dead child has nothing to close
            pass


def open_nemotron() -> Streamer:
    """The streaming backend this is for: Nemotron as a child process."""
    from ..backends import nemotron
    why = nemotron.why_not()
    if why:
        raise RuntimeError(why)
    return Streamer(nemotron.NemotronBackend(rate=SAMPLE_RATE))


class Partials:
    """Which engine writes the partials, followed live: ears.json's ``partials`` unless pinned.

    ``sync(t)`` is called as each turn opens (and once at the start): it starts the streamer
    when the setting says ``nemotron`` and there is none, and closes it when the setting says
    ``final``. A child that cannot start, or dies, is said once and not tried again for
    :data:`RETRY_AFTER_S`; the chunked partials carry on meanwhile. ``live`` is the question
    the loop asks every frame: are the partials coming from the stream right now?
    """

    def __init__(self, mode: Optional[str] = None, *, make: Callable[[], Streamer] = open_nemotron) -> None:
        if mode not in (None, "final", "nemotron"):
            raise ValueError(f"partials must be final or nemotron, not {mode!r}")
        self.pinned = mode
        self._make = make
        self.stream: Optional[Streamer] = None
        self.retry_at = 0.0

    def want(self) -> str:
        return self.pinned or conf_partials()

    @property
    def live(self) -> bool:
        s = self.stream
        return s is not None and s.ready and s.failed is None

    def sync(self, t: float) -> Optional[str]:
        """Match the streamer to the setting; a note when something changed, else None."""
        want = self.want()
        if want == "nemotron" and self.stream is None and t >= self.retry_at:
            try:
                self.stream = self._make()
            except Exception as exc:  # noqa: BLE001 - not importable, no model: say so, carry on
                self.retry_at = t + RETRY_AFTER_S
                return f"nemotron not started ({exc}); partials from the final engine, per chunk"
            return "nemotron starting; partials from the final engine until it is ready"
        if want == "final" and self.stream is not None:
            self.stream.close()
            self.stream = None
            return "partials from the final engine, per chunk (ears.json)"
        return None

    def failed(self, t: float) -> Optional[str]:
        """The streamer died: close it, say why, and hold off restarting. None if it is fine."""
        s = self.stream
        if s is None or s.failed is None:
            return None
        s.close()
        self.stream = None
        self.retry_at = t + RETRY_AFTER_S
        return s.failed

    def close(self) -> None:
        if self.stream is not None:
            self.stream.close()
            self.stream = None
