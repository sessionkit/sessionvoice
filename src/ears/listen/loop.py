"""Capture, "the ears": a long-lived loop that writes what it hears.

Capture outlives the call (the design split). It writes
through ``heard``: a ``listen-started`` event before the first frame, a
``partial`` per chunk as it is recognised, a ``turn`` at each end of turn,
a ``heartbeat`` on a period (with the input level over that period, so a
dead-zero mic is visible), a ``vad`` event at each voice onset and offset
(the mouth ducks a line on the onset, before any word is decoded),
a ``missed-reply`` event naming a WAV when a mouth line ended and no turn came
within the window (:mod:`ears.listen.missed`; off unless given, and
written only while the recording switch is on, :mod:`ears.recording`),
and a ``listen-stopped`` event with the reason on every exit. While that switch is on,
each final turn's audio is kept as one WAV, written before the turn row, which names it
in ``audio`` (:mod:`ears.listen.turnaudio`).

It does NOT return on a turn. It stops only on:

- ``stop``: the caller asked (a ``threading.Event``/callable, SIGINT/SIGTERM
  in the CLI, or a stop file)
- ``error``: the source failed, or STT failed ``max_stt_failures`` times in a row
- ``eof``: a file source ran out (the mic never does)
- ``ceiling``: only if one was given

Turn text (``turn_text``). ``joined`` -- the DEFAULT, and
what the spec says -- writes the turn as the join of its
partials. ``redecode`` re-decodes the turn's WHOLE audio and writes that
instead, because joined chunks split and duplicate words cut by the 2 s soft
max (a split word written twice, 17 of 32 turns measured). The re-decode is launched
speculatively once ``quiet_s`` of quiet follows speech inside the turn, so it
runs inside the ``silence_s`` wait and costs no turn latency; speech resuming
drops it. Partials are unchanged in both modes. A turn is still discarded when
its partials heard nothing, so the mode changes turn TEXT, never turn COUNT.
It is an option, not a spec change: the default stays ``joined``, and
``redecode`` runs only when asked for.

Partials (``partials``, :mod:`ears.listen.streaming`). By default each chunk goes
to the STT and comes back a partial, as above. With a :class:`Partials` whose
setting says ``nemotron``, a streaming child hears every frame instead and the
partials are its settled words, appended in the same lines; the chunker still
runs (it marks where a turn's audio begins) but its chunks go nowhere while the
stream is live. The turn's text is untouched either way: the re-decode is the
final engine's.

Waking an agent on an aged turn is a separate waiter's job; its
rule is :func:`ears.listen.aged.aged_turn`.

Time is STREAM time -- seconds of audio consumed -- so silence, heartbeat
and ceiling are measured on the audio. For the mic, and for a ``file:``
source paced at real time, stream time is wall time.

Audio wall time (:mod:`ears.lag`). A record's ``ts`` is when it was
WRITTEN; when the recogniser falls behind, that can be minutes after the user spoke.
So on a real-time source each ``partial``, ``turn``, ``turn-discarded``, ``vad``
and ``heartbeat`` also carries ``audio_ts`` (``listen-started``'s wall time plus
the record's stream time) and ``lag_s`` (how late it was written). A heartbeat
adds ``stt_backlog_s``, the age of the oldest chunk still waiting on the STT (in
chunk mode partials can lag while the frame loop, and so the heartbeat, is on
time), and the mic's ``input_overflows`` (dropped audio makes ``lag_s`` read
large). A source that is not real time (an unpaced ``file:``) gets none of them:
its stream time is not wall time.

When a partial's ``lag_s``, or a heartbeat's ``lag_s`` or ``stt_backlog_s``, goes
over ``LAG_WARN_S`` (2 s), the capture writes ONE ``recogniser-lag`` event, and one
``recogniser-lag-ok`` when it has caught up (:class:`ears.lag.LagAlarm`). The keeper
mails the first; the capture itself mails nothing and restarts nothing.

It never takes the conch and never makes a sound: nothing here takes a
speaking lock or opens an output stream.
"""

from __future__ import annotations

import math
import os
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import wait as wait_futures
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .. import heard, lag, recording
from ..vad import MIN_SILENCE, MIN_SPEECH
from .chunker import Chunk, Chunker
from .detector import SilenceTurnDetector, TurnDetector
from .missed import MissedReply
from .turnaudio import TurnAudio
from .sink import HeardSink, Sink
from .sources import FRAME_S, Source
from .speakers import LabelTail, first_cut, read_turn, turn_speaker
from .streaming import Partials
from .stt import STT, final_transcriber
from .watchdog import ROW_EVENT as WEDGED_EVENT, Watchdog

DEFAULT_HEARTBEAT_S = 60.0
TURN_TEXT_MODES = ("joined", "redecode")
DEFAULT_REDECODE_MAX_S = 300.0  # longer turns fall back to joined partials
# Was 60 s. A 155 s turn fell back to joined partials, and they wrote a wrong word for a
# name; the same audio re-decoded whole read the name right
# on all three backends (research/2030-06-09-stt-three-way). A whole-turn decode of 155 s
# took Apple 2.5 s and Whisper large-v2 3.7 s, and 300 s of audio is 19 MB: cheap.
SPLIT_CHECK_S = 0.5  # how often an open turn looks for a speaker change
SPECULATIVE_MAX_S = 60.0  # past this, re-decode once at the turn's end, not at every pause
_HISTORY_S = 4.0  # audio kept before a turn opens; covers pre-roll and a chunk opened up to max_s early
STOP_FILE_CHECK_S = 0.3
# the frame loop's waits on the STT are bounded. The loop is the only place a
# heartbeat, a STOP check or SIGTERM takes effect, so an unbounded wait there is a wedge.
# A chunk is at most a few seconds of audio and decodes in well under a second; a whole
# turn's re-decode took 2.5-3.7 s for 155 s of audio, and it usually starts during the
# silence wait. Each timeout is written to the log, and the stuck worker is left behind
# (a fresh pool takes the queued chunks), so a later wait never queues on it. Chunk
# timeouts count as STT failures: ``max_stt_failures`` in a row end the capture with
# ``error``, as a dead server's errors do.
STT_WAIT_S = 10.0       # one chunk's partial; a timeout is an stt-error ("timeout")
REDECODE_WAIT_S = 20.0  # the turn's re-decode; a timeout keeps the joined text


class _SttTimeout(Exception):
    """The frame loop stopped waiting for an STT call (``STT_WAIT_S``)."""


def _finished(fut: Future, timeout_s: float) -> bool:
    """Wait up to ``timeout_s`` for ``fut``; True once it has finished, however it ended.

    Asked of the future itself, never read off a ``TimeoutError``: from 3.11 the wait's
    timeout and an STT's own ``TimeoutError`` are one class, and a call that ended
    between the deadline and a ``done()`` check would be taken for an error."""
    return bool(wait_futures((fut,), timeout=timeout_s).done)


@dataclass
class CaptureResult:
    reason: str
    text: str  # every turn this capture wrote, one per line
    cursor: int  # seq of the last line written (the listen-stopped event)
    error: Optional[str] = None

    def to_dict(self) -> dict:
        out = {"reason": self.reason, "text": self.text, "cursor": self.cursor}
        if self.error:
            out["error"] = self.error
        return out


def _stop_checker(stop, stop_file: Optional[Path]) -> Callable[[], bool]:
    if stop is None:
        requested: Callable[[], bool] = lambda: False
    elif hasattr(stop, "is_set"):  # threading.Event
        requested = stop.is_set
    elif callable(stop):
        requested = stop
    else:
        raise TypeError("stop must be a threading.Event, a callable returning bool, or None")
    if stop_file is None:
        return requested
    path = Path(stop_file).expanduser()
    return lambda: requested() or path.exists()


def _r(x: float) -> float:
    return round(x, 3)


class _Level:
    """Peak and RMS of the input over one heartbeat period (int16 full scale)."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._peak = 0
        self._sumsq = 0.0
        self._n = 0

    def add(self, frame: np.ndarray) -> None:
        x = frame.astype(np.int64)
        self._peak = max(self._peak, int(np.abs(x).max(initial=0)))
        self._sumsq += float(np.dot(x, x))
        self._n += len(x)

    def fields(self) -> dict:
        rms = math.sqrt(self._sumsq / self._n) if self._n else 0.0
        dbfs = round(20 * math.log10(rms / 32768.0), 1) if rms > 0 else None
        return {"peak": self._peak, "rms": round(rms, 1), "rms_dbfs": dbfs}


class _Edges:
    """Debounced voice edges over the detector's per-frame speech call, as the
    ``vad`` events :class:`ears.vad.VadEdges` writes elsewhere: an onset after
    ``MIN_SPEECH`` of voice (2 frames, 60 ms), an offset after ``MIN_SILENCE``
    of quiet (7 frames, 210 ms). An onset's ``at`` is where the voiced run
    began; an offset's, the end of the last voiced frame (stream seconds).

    Unlike a turn (``silence_s`` of quiet closes one, 2 s by default), every
    burst of voice after a short quiet is an onset: the user may barge in a beat
    after the user's last turn, while the detector still holds it open."""

    def __init__(self, min_speech: float = MIN_SPEECH, min_silence: float = MIN_SILENCE) -> None:
        self.on_frames = max(1, round(min_speech / FRAME_S))
        self.off_frames = max(1, round(min_silence / FRAME_S))
        self.speaking = False
        self._run = 0       # consecutive voiced frames while quiet
        self._quiet = 0     # consecutive quiet frames while speaking
        self._last_voice = 0.0

    def feed(self, speech: bool, t_frame: float) -> Optional[tuple[bool, float]]:
        """(speech, at) when this frame makes an edge, else None."""
        if speech:
            self._last_voice = t_frame + FRAME_S
        if not self.speaking:
            self._run = self._run + 1 if speech else 0
            if self._run >= self.on_frames:
                self.speaking, self._quiet = True, 0
                return True, t_frame + FRAME_S - self._run * FRAME_S
            return None
        self._quiet = 0 if speech else self._quiet + 1
        if self._quiet >= self.off_frames:
            self.speaking, self._run = False, 0
            return False, self._last_voice
        return None


class _Capture:
    def __init__(
        self,
        source: Source,
        sink: Sink,
        stt: STT,
        *,
        heartbeat: float,
        ceiling: Optional[float],
        detector: TurnDetector,
        chunker: Chunker,
        stop: Callable[[], bool],
        session: Optional[str],
        agent: Optional[str],
        stt_workers: int,
        max_stt_failures: int,
        turn_text: str = "joined",
        redecode_max_s: float = DEFAULT_REDECODE_MAX_S,
        speaker_split: int = 0,
        speaker_labels: Optional[LabelTail] = None,
        partials: Optional[Partials] = None,
        missed_reply: Optional[MissedReply] = None,
        record=None,
        turn_audio: Optional[TurnAudio] = None,
        clock: Callable[[], float] = time.time,
        stt_wait_s: float = STT_WAIT_S,
        redecode_wait_s: float = REDECODE_WAIT_S,
        watchdog: Optional[Watchdog] = None,
    ) -> None:
        if turn_text not in TURN_TEXT_MODES:
            raise ValueError(f"turn_text must be one of {TURN_TEXT_MODES}, not {turn_text!r}")
        self.source = source
        self.sink = sink
        self.stt = stt
        self.heartbeat = heartbeat
        self.ceiling = ceiling
        self.detector = detector
        self.chunker = chunker
        self.stop_requested = stop
        self.session = session
        self.agent = agent
        self.max_stt_failures = max_stt_failures
        self._stt_workers = stt_workers
        self._executor = self._new_executor()
        self.stt_wait_s = stt_wait_s
        self.redecode_wait_s = redecode_wait_s
        self._watchdog = watchdog  # None = none (a library caller); the CLI gives one
        self._pending: deque = deque()  # (Future | text | Exception, Chunk), in cut order
        self._partials: list[str] = []  # texts of partials not yet in a turn
        self._partial_meta: list[tuple[int, float, float]] = []  # (seq, t0, t1) of each
        # a turn is cut where another named speaker holds K partials in a row.
        # 0 = off. Labels come from the speaker side-car's file; none = today's turns.
        self._split_k = speaker_split if speaker_labels is not None else 0
        self._labels = speaker_labels
        self._split_warned = False
        self._next_speaker: Optional[str] = None  # the speaker of a turn a split opened
        self._turns: list[str] = []  # texts of turns this capture wrote
        self._turn_t0: Optional[float] = None
        self._stt_failures = 0
        self._level = _Level()
        # silero's max speech probability per heartbeat period. A classifier
        # with no probability (webrtc, energy) has no take_prob_max: the field is omitted.
        self._prob_max = getattr(getattr(detector, "classifier", None), "take_prob_max", None)
        # a mouth line, then no turn within the window, keeps that window's
        # audio. None = off. Its errors never break the capture: said once.
        self._missed = missed_reply
        self._missed_warned = False
        # The one recording switch (ears.recording). None: this process's own
        # ($EARS_RECORD, then ears.json, then off). Read at listen-started for the record,
        # and by each path that keeps audio when it is about to write.
        self._record = record if record is not None else recording.Switch()
        # Kept turn audio, one WAV per final turn while the switch is on (None:
        # this capture's own, under <heard base>/audio). Its errors never break the capture.
        self._kept: Optional[TurnAudio] = turn_audio if turn_audio is not None else TurnAudio(self._record)
        self._kept_warned = False
        # The waiter's contract: `period` on
        # listen-started and every heartbeat; it calls capture down after 2x
        # period with no line. No heartbeat, no period (heard drops None).
        self._period = heartbeat if heartbeat > 0 else None
        # turn text re-decode; all unused when turn_text == "joined"
        self.turn_text = turn_text
        self._redecode = turn_text == "redecode"
        self._redecode_max_frames = max(1, int(round(redecode_max_s / FRAME_S)))
        # a long turn is re-decoded once, at its end: a speculative launch at every pause of a
        # 5-minute turn would send the STT minutes of audio each time the user breathes
        self._spec_max_frames = min(self._redecode_max_frames, max(1, int(round(SPECULATIVE_MAX_S / FRAME_S))))
        self._history: deque = deque(maxlen=int(round(_HISTORY_S / FRAME_S)))
        self._turn_audio: Optional[list] = None  # frames of the open turn (None: no turn open)
        self._turn_too_long = False
        self._quiet_run = 0  # non-speech frames since the turn's last speech frame
        self._quiet_frames = max(1, int(round(getattr(chunker, "quiet_s", 0.35) / FRAME_S)))
        self._spec: Optional[Future] = None  # the speculative whole-turn decode
        # streaming partials (settled words); None = every partial is a chunk's, as before
        self._parts = partials
        self._streaming = False  # the stream wrote the partials on the last frame
        self._edges = _Edges()   # voice onsets/offsets as ``vad`` events
        self.cursor = 0
        self.t = 0.0
        # audio wall time = wall0 + stream time, on a real-time source only (an
        # unpaced file runs faster than the wall). wall0 is set as listen-started is written.
        self._clock = clock
        self._live = bool(getattr(source, "realtime", True))
        self._wall0: Optional[float] = None
        # one recogniser-lag event per episode, from partials and heartbeats
        self._lag_alarm = lag.LagAlarm()

    # -- writing -----------------------------------------------------------

    def _write(self, line: dict) -> int:
        line = {**line, "source": self.source.name, "device": self.source.device, "session": self.session, "agent": self.agent}
        self.cursor = self.sink.write(line)
        return self.cursor

    def _stamp(self, stream_s: float) -> dict:
        """``audio_ts`` and ``lag_s`` for a record whose audio ended at ``stream_s``, taken
        now, just before the write; {} when the source has no wall clock (not real time)."""
        if self._wall0 is None:
            return {}
        return lag.stamp(self._wall0 + stream_s, self._clock())

    def _backlog_fields(self) -> dict:
        """A heartbeat's view of what its own ``lag_s`` cannot see: the oldest chunk still
        waiting on the STT (``stt_backlog_s``, 0 with none), and the mic's dropped blocks."""
        out: dict = {}
        if self._wall0 is not None:
            oldest = self._pending[0][1].t1 if self._pending else None
            out["stt_backlog_s"] = 0.0 if oldest is None else _r(max(0.0, self._clock() - (self._wall0 + oldest)))
        overflows = getattr(self.source, "input_overflows", None)
        if overflows is not None:
            out["input_overflows"] = int(overflows)
        return out

    def _lag_said(self, said: Optional[tuple[str, dict]]) -> None:
        """Write the lag alarm's event, if it raised or cleared."""
        if said is not None:
            self.event(said[0], **said[1])

    def _frame_prob(self) -> Optional[float]:
        """Silero's max over the windows of the frame just classified (None if not silero)."""
        return getattr(getattr(self.detector, "classifier", None), "frame_prob_max", None)

    def _missed_do(self, step: Callable, *args) -> None:
        """One missed-reply step, and the events it hands back. Any error is said once and
        never breaks the capture; a failed start turns clips off for this capture."""
        if self._missed is None:
            return
        try:
            for name, fields in step(*args):
                self.event(name, **fields)
        except Exception as exc:  # noqa: BLE001 - a clip is never worth the capture
            if step == self._missed.open:
                self._missed = None
            if not self._missed_warned:
                self._missed_warned = True
                self.event("missed-reply-error", detail=f"{type(exc).__name__}: {exc}"[:200])

    def _kept_do(self, step: Callable, *args, default=None):
        """One kept-turn-audio step. Any error is said once (``turn-audio-error``) and the
        capture goes on: the turn lands without ``audio``."""
        if self._kept is None:
            return default
        try:
            return step(*args)
        except Exception as exc:  # noqa: BLE001 - a full disk must not stop the capture
            if not self._kept_warned:
                self._kept_warned = True
                self.event("turn-audio-error", detail=f"{type(exc).__name__}: {exc}"[:200])
            return default

    def _record_fields(self) -> dict:
        """``record`` (bool) and ``record_from`` (cli, env, file or default) for
        listen-started; ``record_invalid`` only when that layer's value was invalid."""
        r = self._record.record()
        out = {"record": r.on, "record_from": r.source}
        if r.invalid:
            out["record_invalid"] = True
        return out

    def _beat_fields(self) -> dict:
        """A heartbeat's level fields, plus ``speech_prob_max`` under silero (reset per beat)."""
        fields = self._level.fields()
        if self._prob_max is not None:
            m = self._prob_max()
            if m is not None:
                fields["speech_prob_max"] = round(m, 3)
        return fields

    def event(self, name: str, **extra) -> int:
        return self._write({"kind": "event", "event": name, **extra})

    def _partial(self, text: str, t0: float, t1: float, **extra) -> None:
        stamp = self._stamp(t1)
        seq = self._write({"kind": "partial", "text": text, "final": False, "t0": _r(t0), "t1": _r(t1), **extra,
                           **stamp})
        self._partials.append(text)
        self._partial_meta.append((seq, t0, t1))
        if stamp:
            self._lag_said(self._lag_alarm.partial(stamp[lag.LAG_S], self._clock()))

    def _clear_partials(self) -> None:
        self._partials, self._partial_meta = [], []

    def _turn(self, t_end_speech: float, **fields) -> None:
        if self._split_k and "split" not in fields:
            # the turn closes. A reply partial that came after the frame loop's
            # last split check (just before the VAD close) is cut off here, once, and not
            # left in the agent's turn to name it None. A split's own halves never re-check.
            self._maybe_split()
        text = " ".join(p for p in self._partials if p)
        seqs, texts = [m[0] for m in self._partial_meta], self._partials
        self._clear_partials()
        extra: dict = {}
        if self._next_speaker is not None and "speaker" not in fields:
            fields = {"speaker": self._next_speaker, "opened_by": "speaker-change", **fields}
        self._next_speaker = None
        if self._split_k and text and "speaker" not in fields:
            who = self._labelled_speaker(seqs, texts)
            if who is not None:
                fields = {"speaker": who, "speaker_from": "labels", **fields}
        if self._redecode:
            # a turn whose partials heard nothing is discarded as before: the
            # re-decode changes a turn's text, never whether it is one
            text, extra = self._redecoded_text(text) if text else (text, {})
            self._turn_audio, self._spec, self._turn_too_long, self._quiet_run = None, None, False, 0
        t0 = self._turn_t0 if self._turn_t0 is not None else t_end_speech
        self._turn_t0 = None
        kept = self._kept_do(self._kept.take, default=(None, False)) if self._kept is not None else (None, False)
        if not text:
            # whisper heard nothing in it: a noise burst must not become a turn
            self.event("turn-discarded", why="no text recognised", t0=_r(t0), t1=_r(t_end_speech),
                       **self._stamp(t_end_speech))
            return
        if self._missed is not None:
            self._missed_do(self._missed.turn)
        # The WAV is written BEFORE the row, so the row can name it
        audio = self._kept_do(self._kept.write, *kept, default={}) if kept[0] else {}
        self._write(
            {
                "kind": "turn",
                "text": text,
                "final": True,
                "detector": self.detector.name,
                "t0": _r(t0),
                "t1": _r(t_end_speech),
                **extra,
                **fields,
                **audio,
                **self._stamp(t_end_speech),
            }
        )
        self._turns.append(text)

    # -- speaker change -------------------------------------------

    def _read_labels(self, seqs: list[int], texts: list[str], rule: Callable, **kw) -> object:
        """``rule(labels, K)`` over the side-car's labels for ``seqs`` (with the mouth
        marks and echo guard, from the partials' ``texts``), or None. Reading the
        labels can never break the capture: any error is None, said once."""
        try:
            labels, guards = read_turn(self._labels, seqs, texts)
            return rule(labels, self._split_k, **guards, **kw)
        except Exception as exc:  # noqa: BLE001 - the side-car's file is not ours to trust
            if not self._split_warned:
                self._split_warned = True
                self.event("speaker-split-error", detail=f"{type(exc).__name__}: {exc}"[:200])
            return None

    def _maybe_split(self) -> None:
        """Close the open turn where another named speaker took over. Reading the labels
        can never break the capture: any error leaves the turn as it is, said once."""
        if len(self._partials) < 2:  # the mouth's agent yields to ONE partial
            return
        # a turn a split opened is held from its first partial
        cut = self._read_labels([m[0] for m in self._partial_meta], self._partials, first_cut,
                                held=self._next_speaker)
        if cut is not None:
            self._split(*cut)

    def _labelled_speaker(self, seqs: list[int], texts: list[str]) -> Optional[str]:
        """The speaker of a turn no split named, by its partials' labels
        (:func:`turn_speaker`), so a one-speaker turn carries ``speaker`` too, marked
        ``speaker_from: labels``. None (no field written) for a TV/neutral-only turn, a
        turn with no labels yet, a change the split had not cut, or a label read error."""
        return self._read_labels(seqs, texts, turn_speaker) if seqs else None

    def _split(self, i: int, before: str, after: str) -> None:
        """Turn = partials [:i] (the BEFORE speaker's), closed at partial i-1's end; the
        open turn goes on from partial i (AFTER's), with its own audio for the re-decode."""
        texts, meta = self._partials, self._partial_meta
        t_cut, t_end = meta[i][1], meta[i - 1][2]
        rest_audio, too_long, quiet_run = None, self._turn_too_long, self._quiet_run
        if self._redecode and self._turn_audio is not None and not too_long and self._turn_t0 is not None:
            k = min(len(self._turn_audio), max(0, int(round((t_cut - self._turn_t0) / FRAME_S))))
            rest_audio, self._turn_audio = self._turn_audio[k:], self._turn_audio[:k]
            self._drop_spec()  # it covered both speakers
        kept_rest = None
        if self._kept is not None and self._turn_t0 is not None:
            kept_rest = self._kept_do(self._kept.cut, int(round((t_cut - self._turn_t0) / FRAME_S)))
        self._partials, self._partial_meta = texts[:i], meta[:i]
        self._turn(t_end, speaker=before, split="speaker-change")
        self._partials, self._partial_meta = texts[i:], meta[i:]
        self._turn_t0 = t_cut
        if self._kept is not None:
            self._kept_do(self._kept.resume, kept_rest)
        self._next_speaker = after
        if self._redecode:
            self._turn_audio = rest_audio if rest_audio is not None else []
            self._turn_too_long, self._quiet_run = too_long, quiet_run  # _turn() zeroed them
            # already past the quiet cut (the usual case: the deciding partial is cut there):
            # re-launch the early decode on this half, on the audio up to that cut, exactly
            # as the dropped one was (not on the up-to-0.5 s of quiet since)
            upto = len(self._turn_audio) - (self._quiet_run - self._quiet_frames)
            if (self._quiet_run >= self._quiet_frames and not too_long and upto > 0
                    and upto <= self._spec_max_frames):
                self._spec = self._launch(np.concatenate(self._turn_audio[:upto]))

    # -- the STT pool --------------------------------------------

    def _new_executor(self) -> Optional[ThreadPoolExecutor]:
        if self._stt_workers <= 0:
            return None
        return ThreadPoolExecutor(max_workers=self._stt_workers, thread_name_prefix="listen-stt")

    def _abandon_executor(self) -> None:
        """A wait on the STT timed out: a worker is stuck in a call. Leave that pool to it
        and start a fresh one. Every chunk still queued (not started) moves to the new pool,
        in cut order; the speculative re-decode is dropped (the turn's end launches one
        again). The stuck thread lives on until its call returns."""
        old = self._executor
        if old is None:
            return
        self._executor = self._new_executor()
        moved: deque = deque()
        for result, chunk in self._pending:
            if isinstance(result, Future) and result.cancel():
                result = self._executor.submit(self.stt.transcribe, chunk.audio)
            moved.append((result, chunk))
        self._pending = moved
        self._drop_spec()
        old.shutdown(wait=False, cancel_futures=True)

    # -- turn re-decode -------------------------------------------

    def _launch(self, audio: np.ndarray) -> Future:
        # the turn's FINAL text: the one request the seat-name hint rides
        final = final_transcriber(self.stt)
        if self._executor is not None:
            return self._executor.submit(final, audio)
        fut: Future = Future()  # inline STT (tests): the same shape, already done
        try:
            fut.set_result(final(audio))
        except Exception as exc:
            fut.set_exception(exc)
        return fut

    def _drop_spec(self) -> None:
        if self._spec is not None:
            self._spec.cancel()  # a no-op if whisper already has it; its result is ignored
            self._spec = None

    def _track_turn_audio(self, frame: np.ndarray, verdict) -> None:
        """Keep the open turn's audio, and launch/drop the speculative re-decode."""
        self._history.append(frame)
        if verdict.turn_started:
            # the turn began at _turn_t0 (the first chunk's pre-roll included)
            back = int(round((self.t - self._turn_t0) / FRAME_S))
            hist = list(self._history)
            self._turn_audio = hist[-back:] if 0 < back < len(hist) else hist
            self._turn_too_long = False
            self._quiet_run = 0
            self._drop_spec()
            return
        if self._turn_audio is None:
            return
        if not self._turn_too_long:
            self._turn_audio.append(frame)
            if len(self._turn_audio) > self._redecode_max_frames:
                self._turn_too_long = True
                self._turn_audio = []  # free it; this turn's text will be joined
                self._drop_spec()
        if verdict.speech:
            self._quiet_run = 0
            self._drop_spec()  # speech resumed: what was launched is now short
        else:
            self._quiet_run += 1
            if (self._quiet_run == self._quiet_frames and not self._turn_too_long
                    and len(self._turn_audio) <= self._spec_max_frames):
                self._spec = self._launch(np.concatenate(self._turn_audio))

    def _redecoded_text(self, joined: str) -> tuple[str, dict]:
        """The turn's text from its whole audio; the joined partials on any failure."""
        audio, spec, too_long = self._turn_audio, self._spec, self._turn_too_long
        if too_long or not audio:
            return joined, {"text_from": "joined", "redecode": "too-long" if too_long else "no-audio"}
        waited = time.monotonic()
        fut = spec if spec is not None else self._launch(np.concatenate(audio))
        if not _finished(fut, self.redecode_wait_s):
            # no answer in time. The turn lands with its joined text, and the
            # worker stuck in that call is left behind, so the next turn does not queue on it.
            self._abandon_executor()
            return joined, {"text_from": "joined", "redecode": "timeout",
                            "redecode_wait_s": _r(time.monotonic() - waited), "speculative": spec is not None}
        try:
            text = str(fut.result()).strip()
        except Exception as exc:  # whisper hiccup: the turn still lands, with the joined text
            return joined, {"text_from": "joined", "redecode": f"error: {exc}"}
        info = {"redecode_wait_s": _r(time.monotonic() - waited), "speculative": spec is not None}
        if not text:
            return joined, {"text_from": "joined", "redecode": "empty", **info}
        return text, {"text_from": "redecode", "joined": joined, **info}

    # -- transcription -----------------------------------------------------

    def _submit(self, chunk: Optional[Chunk]) -> None:
        if chunk is None:
            return
        if self._executor is None:
            try:
                self._pending.append((self.stt.transcribe(chunk.audio), chunk))
            except Exception as exc:
                self._pending.append((exc, chunk))
        else:
            self._pending.append((self._executor.submit(self.stt.transcribe, chunk.audio), chunk))

    def _collect(self, wait: bool) -> None:
        """Write partials for finished chunks, in cut order; ``wait`` drains all."""
        while self._pending:
            result, chunk = self._pending[0]
            if isinstance(result, Future):
                if not wait and not result.done():
                    return
                fut = result
                if not _finished(fut, self.stt_wait_s):
                    # leave the stuck worker behind. A chunk that never started
                    # (it queued behind the stuck call) moves to the fresh pool and is
                    # waited for again; the one that was running is a failure.
                    self._abandon_executor()
                    if self._pending[0][0] is not fut:
                        continue
                    result = _SttTimeout("timeout")
                else:
                    try:
                        result = fut.result()
                    except Exception as exc:
                        result = exc
            self._pending.popleft()
            if isinstance(result, Exception):
                self._stt_failures += 1
                extra = {"wait_s": self.stt_wait_s} if isinstance(result, _SttTimeout) else {}
                self.event("stt-error", detail=str(result), t0=_r(chunk.t0), t1=_r(chunk.t1), **extra)
                if self._stt_failures >= self.max_stt_failures:
                    raise RuntimeError(f"STT failed {self._stt_failures} times in a row: {result}")
                continue
            self._stt_failures = 0
            text = str(result).strip()
            if text:
                self._partial(text, chunk.t0, chunk.t1)

    # -- streaming partials (settled words) ---------------------------------

    def _stream_sync(self) -> None:
        """As a turn opens: match the streamer to ears.json's ``partials``; say any change."""
        note = self._parts.sync(self.t)
        if note:
            self.event("partials", detail=note, stream_s=_r(self.t))

    def _stream_feed(self, frame: np.ndarray, t_frame: float, verdict) -> None:
        """Every frame to the stream (dropped until it is ready); its spans into partials.
        The moment the stream takes over mid-turn, the chunk already open is sent on, so
        the words in it are not lost; from then on the chunker's cuts go nowhere."""
        s = self._parts.stream
        if s is not None:
            s.feed(frame, t_frame)
            self._stream_take(s.drain())
            why = self._parts.failed(self.t)
            if why:
                self.event("partials-error", detail=why[:200], stream_s=_r(self.t))
        live = self._parts.live
        if live and not self._streaming and verdict.in_turn:
            self._submit(self.chunker.flush(self.t))
        self._streaming = live

    def _stream_take(self, spans) -> None:
        for text, t0, t1 in spans:
            if text:
                self._partial(text, t0, t1, engine=self._parts.stream.name)

    def _stream_end_of_turn(self) -> None:
        """The turn ended on the stream: close its segment (bounded) and take the rest."""
        self.chunker.flush(self.t)  # the open chunk is the stream's audio: not sent
        self._stream_take(self._parts.stream.finish())

    # -- the loop ----------------------------------------------------------

    # -- the watchdog --------------------------------------------

    def _child_pids(self) -> list[int]:
        """The streaming child this capture started, if it is still running: the one
        process the watchdog SIGKILLs before it exits."""
        stream = self._parts.stream if self._parts is not None else None
        proc = getattr(getattr(stream, "b", None), "p", None)
        if proc is None or getattr(proc, "pid", None) is None:
            return []
        try:
            return [proc.pid] if proc.poll() is None else []
        except Exception:  # noqa: BLE001
            return [proc.pid]

    def _heard_dir(self) -> Optional[Path]:
        if isinstance(self.sink, HeardSink):
            return self.sink.directory or heard.log_dir()
        return None

    def run(self) -> CaptureResult:
        wd = self._watchdog
        if wd is None:
            return self._run(lambda: None)
        wd.start(stop_requested=self.stop_requested,
                 write_row=lambda fields: self.event(WEDGED_EVENT, **fields),
                 children=self._child_pids, lock_dir=self._heard_dir(), stream_s=lambda: self.t)
        try:
            return self._run(wd.progress)
        finally:
            wd.run_ended()

    def _run(self, progress: Callable[[], None]) -> CaptureResult:
        wall0 = time.monotonic()
        reason, error = "error", None
        next_hb = self.heartbeat  # on a fixed grid, so beats do not drift by a frame each
        stop_every = max(1, int(round(STOP_FILE_CHECK_S / FRAME_S)))
        split_every = max(1, int(round(SPLIT_CHECK_S / FRAME_S)))
        try:
            if self._live:
                self._wall0 = self._clock()  # the minimal audio clock
            self.event(
                heard.EV_LISTEN_STARTED,
                detector=self.detector.name,
                stt=getattr(self.stt, "name", type(self.stt).__name__),
                partials=self._parts.want() if self._parts is not None else "final",
                heartbeat=self.heartbeat,
                period=self._period,
                ceiling=self.ceiling,
                pid=os.getpid(),
                missed_reply_s=self._missed.window_s if self._missed is not None else None,
                **self._record_fields(),
            )
            if self._missed is not None:
                self._missed_do(self._missed.open)
            if self._kept is not None:
                self._kept_do(self._kept.open)  # read the switch, prune once
            reason = "eof"
            for n, frame in enumerate(self.source.frames()):
                progress()  # the watchdog's stamp: the frame loop is alive
                t_frame = n * FRAME_S
                self.t = (n + 1) * FRAME_S
                self._level.add(frame)
                verdict = self.detector.feed(frame)
                edge = self._edges.feed(verdict.speech, t_frame)
                if edge is not None:
                    # written before the frame's partials: the onset is the
                    # earliest sign of the user's voice the mouth can act on
                    self.event(heard.EV_VAD, speech=edge[0], at=_r(edge[1]), stream_s=_r(self.t),
                               by=self.detector.name, **self._stamp(edge[1]))
                if verdict.turn_started:
                    self._turn_t0 = t_frame
                if self._parts is not None:
                    if n == 0 or verdict.turn_started:
                        self._stream_sync()
                    self._stream_feed(frame, t_frame, verdict)
                chunk = self.chunker.feed(frame, verdict.speech, t_frame)
                if not self._streaming:
                    self._submit(chunk)
                if verdict.turn_started and self.chunker.start_t is not None:
                    # the turn began where its first chunk did (pre-roll included)
                    self._turn_t0 = min(self._turn_t0, self.chunker.start_t)
                if self._redecode:
                    self._track_turn_audio(frame, verdict)
                if self._kept is not None:
                    if n % stop_every == 0:
                        self._kept_do(self._kept.poll)
                    back = int(round((self.t - self._turn_t0) / FRAME_S)) if verdict.turn_started else 0
                    self._kept_do(self._kept.frame, frame, verdict.turn_started, back)
                if verdict.end_of_turn:
                    if self._streaming:
                        self._stream_end_of_turn()
                    else:
                        self._submit(self.chunker.flush(self.t))
                    self._collect(wait=True)
                    self._turn(self.t - getattr(self.detector, "silence_s", 0.0))
                else:
                    self._collect(wait=False)
                    if not verdict.in_turn and not self.chunker.open and not self._pending:
                        # Partials with no turn around them (a blip): they stay in
                        # the log, but are not glued onto the next turn's text.
                        self._clear_partials()
                    elif self._split_k and n % split_every == 0:
                        self._maybe_split()
                if self._missed is not None:
                    if n % stop_every == 0:
                        self._missed_do(self._missed.poll, t_frame)
                    if self._missed is not None:
                        self._missed_do(self._missed.frame, frame, self._frame_prob(), self.detector.in_turn)

                if n % stop_every == 0 and self.stop_requested():
                    reason = "stop"
                    break
                if self.ceiling is not None and self.t >= self.ceiling:
                    reason = "ceiling"
                    break
                if self.heartbeat > 0 and self.t >= next_hb - 1e-9:
                    next_hb += self.heartbeat
                    stamp, backlog = self._stamp(self.t), self._backlog_fields()
                    # pid: the keeper tells its room capture's beats from a call leg's
                    self.event(heard.EV_HEARTBEAT, period=self._period, stream_s=_r(self.t), pid=os.getpid(),
                               **self._beat_fields(), **stamp, **backlog)
                    self._level.reset()
                    if stamp:
                        self._lag_said(self._lag_alarm.heartbeat(
                            stamp[lag.LAG_S], backlog.get("stt_backlog_s"), self._clock(),
                            words_waiting=self._streaming or bool(self._pending)))
            # Nothing already heard is lost: finish the open chunk's partial,
            # and close a turn the source ended in the middle of.
            if self._streaming:
                self._stream_end_of_turn()
            else:
                self._submit(self.chunker.flush(self.t))
            self._collect(wait=True)
            if self.detector.in_turn or self._partials:
                self._turn(self.t)
        except KeyboardInterrupt:  # Ctrl-C outside the CLI's signal handler: a stop, and still logged
            reason = "stop"
        except Exception as exc:  # the source died, STT kept failing, or the log cannot be written
            reason, error = "error", f"{type(exc).__name__}: {exc}"
        finally:
            try:
                self.source.close()
            finally:
                if self._parts is not None:
                    self._parts.close()
                if self._executor is not None:
                    self._executor.shutdown(wait=False, cancel_futures=True)
        extra = {"reason": reason, "stream_s": _r(self.t), "wall_s": _r(time.monotonic() - wall0), "turns": len(self._turns)}
        if error:
            extra["error"] = error
        try:
            self.event(heard.EV_LISTEN_STOPPED, **extra)
        except Exception as exc:  # the log itself is gone: say so in the return
            error = f"{error + '; ' if error else ''}listen-stopped not written: {exc}"
            reason = "error"
        return CaptureResult(reason=reason, text="\n".join(self._turns), cursor=self.cursor, error=error)


def capture(
    source: Source,
    stt: STT,
    sink: Optional[Sink] = None,
    *,
    heartbeat: float = DEFAULT_HEARTBEAT_S,
    ceiling: Optional[float] = None,
    detector: Optional[TurnDetector] = None,
    chunker: Optional[Chunker] = None,
    stop=None,
    stop_file: Optional[str | Path] = None,
    session: Optional[str] = None,
    agent: Optional[str] = None,
    stt_workers: int = 1,
    max_stt_failures: int = 3,
    turn_text: str = "joined",
    redecode_max_s: float = DEFAULT_REDECODE_MAX_S,
    speaker_split: int = 0,
    speaker_labels: Optional[LabelTail] = None,
    partials: Optional[Partials] = None,
    missed_reply: Optional[MissedReply] = None,
    record=None,
    turn_audio: Optional[TurnAudio] = None,
    clock: Callable[[], float] = time.time,
    stt_wait_s: float = STT_WAIT_S,
    redecode_wait_s: float = REDECODE_WAIT_S,
    watchdog: Optional[Watchdog] = None,
) -> CaptureResult:
    """Capture from ``source`` until stopped, an error, the end of a file, or ``ceiling``.

    Blocking; run it in its own process (the CLI) or off the event loop.
    ``sink`` defaults to :class:`HeardSink` (the ``heard`` writer).
    ``stt_workers=0`` transcribes inline (deterministic, for tests).
    ``turn_text`` is ``joined`` (the default, per the spec) or ``redecode``.
    ``speaker_split`` K > 0 with ``speaker_labels`` cuts a turn where another named speaker
    holds K partials in a row, and names an unsplit turn by the same rule
    (``speaker_from: labels``); 0, or no labels, is today's turns exactly.
    ``partials`` (a :class:`Partials`) lets a streaming child write the partials as its
    settled words when ears.json says so; None is every partial a chunk's, as before.
    ``missed_reply`` (a :class:`~ears.listen.missed.MissedReply`) keeps the audio of a
    window after a mouth line in which no turn came; None is off.
    ``record`` is the recording switch (:class:`ears.recording.Switch`); None
    resolves this process's own ($EARS_RECORD, then ears.json, then off).
    ``turn_audio`` (a :class:`~ears.listen.turnaudio.TurnAudio`) keeps each final turn's
    audio as a WAV while that switch is on; None is one under ``<heard base>/audio``.
    ``clock`` is the wall clock behind ``audio_ts``/``lag_s``: a seam for tests.
    ``stt_wait_s`` and ``redecode_wait_s`` bound the frame loop's waits on the STT:
    a chunk's partial, and the turn's re-decode.
    ``watchdog`` (a :class:`~ears.listen.watchdog.Watchdog`) honours STOP and SIGTERM off
    the main thread and ends a wedged capture with ``os._exit``. None, the
    default, is none: only the CLI, a process of its own, gives one.
    """
    return _Capture(
        source,
        sink if sink is not None else HeardSink(),
        stt,
        heartbeat=heartbeat,
        ceiling=ceiling,
        detector=detector or SilenceTurnDetector(),
        chunker=chunker or Chunker(),
        stop=_stop_checker(stop, Path(stop_file) if stop_file else None),
        session=session,
        agent=agent,
        stt_workers=stt_workers,
        max_stt_failures=max_stt_failures,
        turn_text=turn_text,
        redecode_max_s=redecode_max_s,
        speaker_split=speaker_split,
        speaker_labels=speaker_labels,
        partials=partials,
        missed_reply=missed_reply,
        record=record,
        turn_audio=turn_audio,
        clock=clock,
        stt_wait_s=stt_wait_s,
        redecode_wait_s=redecode_wait_s,
        watchdog=watchdog,
    ).run()
