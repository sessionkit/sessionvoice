"""onset - duck a line the moment the user starts talking over it.

A barge-in is cut on WORDS (`hold.barge`): content words of the user's that are
not the line's own (three when this was written, two now). At
three that was 1.3-2 s after the user's first word at p50 (up to ~6 s at p90 on
chunk partials, measured), and on Bluetooth buds it felt
like no barge-in at all.

So the line now answers the user's VOICE at once and their words after:

1. The ears write a ``vad`` event at each voice onset (``speech: true``),
   ~60 ms of voice after it began (`ears.listen.loop._Edges`).
2. :class:`OnsetTail` reads the heard log incrementally - one ``stat`` per
   poll, and only the new bytes when it grew - so a 10 ms poll costs nothing.
3. :class:`BargeDuck` dips the line's device to ``barge_duck_level`` on an
   onset, through the :class:`~mouth.output.LineDuck` that the device applies
   in its audio callback, after the 200 ms queue. A call has no callback: its
   ``CallOut`` applies the same duck in ``write``, where the call is paced
   into the bot's socket.
4. The word-gated cut stays the CONFIRMATION, on a call as off it: one
   policy, N content words. A false
   onset (road noise, the television: Silero scores them .95-.98) only dips
   the line. Only the null device has no duck.
5. The duck follows the user's voice: down
   at the user's onset and down while their voice is on; ``barge_duck_recover_s``
   after the ears' OFFSET row (``speech: false``, 210 ms after the user's last
   voiced frame) with no cut, it springs back over 0.5 s and the line plays
   on. With no offset for ``barge_duck_max_s`` (4 s) the offset is
   taken as lost and it comes back anyway (``capped``). A line that starts
   while the user is mid-voice starts ducked.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from ears import heard

POLL_S = 0.01   # how often the duck looks for an onset: well under one 50 ms block
SCAN_BYTES = 64 * 1024   # how far back a new line looks for the user's voice already on


class OnsetTail:
    """The ears' voice edges (``vad`` events) from ``sources`` written to
    today's heard log after this tail was made: :meth:`edges` returns both,
    the onsets (``speech: true``) and the offsets (``speech: false``);
    :meth:`poll` returns the onsets only. Each call stats the
    file and reads only what was appended since the last one; a new day's
    file (midnight) is read from its start. Never raises."""

    def __init__(self, sources: tuple = ("mic",), directory: Optional[Path] = None) -> None:
        self.sources, self.directory = tuple(sources), directory
        self.path = heard.log_path(directory=directory)
        try:
            self.offset = os.stat(self.path).st_size
        except OSError:
            self.offset = 0
        self._buf = b""

    def poll(self) -> list:
        """The new onsets, in order (an offset read here is passed over)."""
        return [r for r in self.edges() if r["speech"] is True]

    def edges(self) -> list:
        """The new onset and offset rows, in the order they were written."""
        path = heard.log_path(directory=self.directory)
        if path != self.path:                       # a new day: its file is new
            self.path, self.offset, self._buf = path, 0, b""
        try:
            size = os.stat(path).st_size
        except OSError:
            return []
        if size < self.offset:                      # cut short or replaced: start over
            self.offset, self._buf = 0, b""
        if size == self.offset:
            return []
        try:
            with open(path, "rb") as f:
                f.seek(self.offset)
                data = f.read(size - self.offset)
        except OSError:
            return []
        self.offset += len(data)
        lines = (self._buf + data).split(b"\n")
        self._buf = lines.pop()                     # a write still in flight, or b""
        return [r for r in map(self._edge, lines) if r is not None]

    def _edge(self, raw: bytes) -> Optional[dict]:
        """``raw`` as a vad edge row of one of our sources, or None."""
        if b'"vad"' not in raw:                     # most rows: no parse at all
            return None
        try:
            rec = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return None
        if isinstance(rec, dict) and rec.get("kind") == heard.EVENT \
                and rec.get("event") == heard.EV_VAD and isinstance(rec.get("speech"), bool) \
                and rec.get("source", "mic") in self.sources:
            return rec
        return None

    def last_edge(self, scan: int = SCAN_BYTES) -> Optional[dict]:
        """The newest edge row of ours written BEFORE this tail was made (the
        last ``scan`` bytes of today's log, as `hold._scan` reads), or None.
        Reads nothing the tail will return later. Never raises."""
        end = self.offset
        if end <= 0:
            return None
        try:
            with open(self.path, "rb") as f:
                start = max(0, end - scan)
                f.seek(start)
                data = f.read(end - start)
        except OSError:
            return None
        lines = data.split(b"\n")
        if start > 0:
            lines = lines[1:]                       # the first is cut part-way
        for raw in reversed(lines):
            rec = self._edge(raw)
            if rec is not None:
                return rec
        return None


class BargeDuck:
    """Dip a playing line while the user's voice is on; bring it back if no cut follows.

    ``duck`` is the line's :class:`~mouth.output.LineDuck`. :meth:`step` is one
    look (the thread runs it every ``poll_s``) at the voice edges since the
    last:

    - an onset sets the duck to ``level`` (0-1 of the line's own level): the user
      is speaking, and any recovery pending is off;
    - an offset: the user has stopped, and the recover clock starts;
    - ``recover_s`` after the offset with the line still playing and no new
      onset, the duck goes back to 1.0 (the springback, ``LINE_DUCK_UP_S``:
      the line plays on);
    - ``max_s`` (``barge_duck_max_s``) speaking with no offset, the offset
      is taken as lost and the duck goes back to 1.0 all the same:
      ``capped``, with ``capped_words``, the words the user's partials had counted
      toward the cut at that moment (``words``, from the player; a capped
      duck with none is noise or filler, one with some is talk). Each onset
      restarts it.

    Made at the line's first frame. If the user's voice is already on then (the
    newest edge before the line is an onset no more than ``max_s`` old, with
    no offset after it), the line starts ducked (``started_ducked``), and the
    cap counts from that first frame. :meth:`stop` (the line ended, done or
    cut) always leaves the duck at 1.0, so a bed sharing the device comes back.

    ``clock`` (monotonic) times the recover and the cap. ``wall`` stamps the
    moment the duck was first set by an onset (``ducked_wall``), so
    :meth:`record` can set it against the onset row's ``ts``, which is wall
    time too: the two clocks are never mixed in one
    subtraction."""

    def __init__(self, duck, sources: tuple, level: float, recover_s: float,
                 directory: Optional[Path] = None, poll_s: float = POLL_S,
                 clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time,
                 max_s: Optional[float] = None,
                 words: Optional[Callable[[], list]] = None) -> None:
        if max_s is None:
            from . import hold
            max_s = hold.BARGE_DUCK_MAX_S
        self.duck, self.level, self.recover_s = duck, float(level), float(recover_s)
        self.max_s = float(max_s)
        self.poll_s, self.clock, self.wall, self.words = poll_s, clock, wall, words
        self.ducked_wall: Optional[float] = None    # wall time an onset first set the duck
        self.tail = OnsetTail(sources, directory)
        self.speaking = False                       # the user's voice is on, as the edges say
        self.onset_t: Optional[float] = None        # clock time of the last onset (the cap's)
        self.offset_t: Optional[float] = None       # clock time of the offset (the recover's)
        self.onsets = 0                             # onsets seen while this line played
        self.offsets = 0                            # offsets seen while this line played
        self.recovered = 0                          # dips that came back up uncut
        self.capped = False                         # the cap brought one back (no offset)
        self.capped_words: Optional[list] = None    # the counted words when it did
        self.started_ducked = False                 # the user's voice was on at the first frame
        self.seen: list = []                        # (clock time seen, the onset row)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        try:
            self._start_ducked()
        except Exception:  # noqa: BLE001 - a duck must never stop a line
            pass

    def _start_ducked(self) -> None:
        last = self.tail.last_edge()
        if last is None or last["speech"] is not True:
            return
        onset_wall = _wall_of(last)
        if onset_wall is None or not 0 <= self.wall() - onset_wall <= self.max_s:
            return
        self.speaking, self.started_ducked = True, True
        self.onset_t = self.clock()                 # the cap counts from the first frame
        self.duck.set(self.level)

    def step(self, now: Optional[float] = None) -> None:
        now = self.clock() if now is None else now
        for r in self.tail.edges():
            if r["speech"] is True:
                self.onsets += 1
                self.seen.append((now, r))
                self.speaking, self.onset_t, self.offset_t = True, now, None
                self.duck.set(self.level)
                if self.ducked_wall is None:        # the first dip: its wall time
                    self.ducked_wall = self.wall()
            else:
                self.offsets += 1
                if self.speaking:
                    self.speaking, self.offset_t = False, now
        if not self.duck.ducked:
            return
        if self.speaking:
            if self.onset_t is not None and now - self.onset_t >= self.max_s:
                self.speaking, self.onset_t = False, None   # the offset is lost: up anyway
                self.capped = True
                self.capped_words = self._counted()
                self._recover()
        elif self.offset_t is not None and now - self.offset_t >= self.recover_s:
            self._recover()

    def _recover(self) -> None:
        self.duck.set(1.0)
        self.recovered += 1
        self.offset_t = None

    def _counted(self) -> list:
        try:
            return list(self.words()) if self.words is not None else []
        except Exception:  # noqa: BLE001 - a record field never stops a line
            return []

    def record(self, utt: Optional[str] = None, device: Optional[str] = None,
               cut: Optional[bool] = None, reason: Optional[str] = None) -> dict:
        """The line's duck record (``kind: duck``), for
        player.log once the line has ended and :meth:`stop` has run.

        ``recover_s`` (counted from the user's offset) and ``max_s``
        (the cap) are the values in force. ``onsets``, ``offsets`` and
        ``recovered`` are the counters; ``started_ducked`` says the user's voice was
        on at the first frame; ``capped`` says the cap brought the line back
        with no offset, and ``capped_words`` the words counted toward the cut
        then (``[]``: none, so noise or filler; null when not capped). ``first_onset_seq`` and
        ``first_onset_ts`` come from the first onset row (null with no onset).
        ``ducked_ts`` is the wall time of the first dip, and ``dip_ms`` is
        ``ducked_ts`` minus the row's ``ts``, both wall time: the row is written
        about 60 ms into the user's voice, so this is the tail's poll latency, not
        voice to duck. ``applied_ms`` (monotonic on both sides) is the time
        from that dip's ask to the first buffer the device callback (or a
        call's write) scaled, from :attr:`LineDuck.trace`. ``ahead_ms`` is
        the audio already past the duck when it bit: the device's output
        latency, or on a call the paced lead in the
        bot's queue, about 0.2 s. A call's ``applied_ms`` is measured at the
        write, so it is ``applied_ms + ahead_ms`` that reaches the user's ear; null
        when unknown. Never raises: on a failure it returns what it has, with
        the line's ``cut`` and ``reason`` always last."""
        rec: dict = {"kind": "duck", "utt": utt, "device": device}
        try:
            rec.update(level=self.level, recover_s=self.recover_s, max_s=self.max_s,
                       onsets=self.onsets, offsets=self.offsets, started_ducked=self.started_ducked)
            first = self.seen[0][1] if self.seen else {}
            rec.update(first_onset_seq=first.get("seq"), first_onset_ts=first.get("ts"))
            rec["ducked_ts"] = None if self.ducked_wall is None else _iso(self.ducked_wall)
            onset_wall = _wall_of(first)
            rec["dip_ms"] = None if onset_wall is None or self.ducked_wall is None \
                else round((self.ducked_wall - onset_wall) * 1000)
            rec["applied_ms"], rec["ahead_ms"] = self._applied()
            rec["recovered"] = self.recovered
            rec.update(capped=self.capped, capped_words=self.capped_words)
        except Exception:  # noqa: BLE001 - a record that cannot be built never stops a line
            pass
        rec.update(cut=cut, reason=reason)
        return rec

    def _applied(self) -> tuple:
        """(applied - asked, ahead) in ms for the first duck down the device
        callback took that was asked at or after this line's first dip (the
        trace is the device's, so it can hold an earlier line's). ``ahead`` is
        None when the trace entry does not carry it. (None, None) if there is
        none."""
        if not self.seen:
            return None, None
        first_dip = self.seen[0][0]                 # clock time of the first set
        try:
            trace = list(getattr(self.duck, "trace", None) or ())
        except RuntimeError:                        # the callback appended mid-copy
            return None, None
        for t in trace:
            asked, applied = t[0], t[1]
            if asked >= first_dip:
                ahead = t[2] if len(t) > 2 else None
                return (round((applied - asked) * 1000),
                        None if ahead is None else round(ahead * 1000))
        return None, None

    def _run(self) -> None:
        while not self._stop.wait(self.poll_s):
            try:
                self.step()
            except Exception:  # noqa: BLE001 - a duck must never stop a line
                pass

    def start(self) -> "BargeDuck":
        self._thread = threading.Thread(target=self._run, daemon=True, name="barge-duck")
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self.duck.set(1.0)


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t).astimezone().isoformat(timespec="milliseconds")


def _wall_of(rec: dict) -> Optional[float]:
    """A heard row's ``ts`` as wall time, or None."""
    try:
        return datetime.fromisoformat(rec["ts"]).timestamp()
    except (KeyError, TypeError, ValueError):
        return None


def for_line(out, device: Optional[str], settings: Optional[dict] = None,
             directory: Optional[Path] = None, policy: Optional[dict] = None,
             words: Optional[Callable[[], list]] = None) -> Optional[BargeDuck]:
    """A started :class:`BargeDuck` for a line playing on ``out``, or None when
    the output has no duck (the null device), or the setting turns it off
    (``barge_duck_level`` 100). A call has one (``CallOut.duck``,
    applied in its write), so a call line ducks on the call's own onsets
    (``barge_sources_for``). The caller has checked ``barge_device``.

    The level, the recover time and the cap (``duck_max_s``) come from
    ``policy``, the line's ``hold.barge_policy`` that ``play_one`` read once,
    so a line reads them from its own mouth dir once; with
    none it is read here, from ``settings``. ``words`` (no I/O) returns the
    words counted toward the cut so far, for a capped duck's record."""
    from . import hold
    duck = getattr(out, "duck", None)
    if duck is None:
        return None
    if policy is None:
        policy = hold.barge_policy(device, settings=settings)
    level = policy["duck_level"]
    if level >= 100:
        return None
    return BargeDuck(duck, hold.barge_sources_for(device), level / 100.0,
                     policy["recover_s"], directory, max_s=policy["duck_max_s"],
                     words=words).start()
