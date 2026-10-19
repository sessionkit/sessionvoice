"""The bed lane: a played file leaves the speech queue and plays on its own thread.

Every played file is a BED: the player takes
it from the queue, hands it to a ``Bed`` and goes straight back to the queue,
so a line or a sound for ANOTHER device never waits behind a song (README
"other devices never wait"). There is one bed per
device, keyed by :func:`device_key`.

Every bed owns a MIXER on its device (``output.Mixer``), whatever
its duck: a line spoken to the device is mixed over the bed
(``output.open_output`` asks :func:`mixer_for`), so it never waits behind the
song, and a ``now`` line to the device cuts the line playing, never the bed
(now-spares-music). Its volume is the mixer's ``bed_volume``. It DIPS instead
of being cut (:class:`Ducker`): under any line the player plays
(``say``), and under the user's voice where barge-in counts (``barge``,
``hold.speaking_over``). No barge-in cuts it, its own or another lane's. A
stop aimed at its utt, a general stop that is not a barge-in, or a retract
cuts it: it READS ``stop`` (never unlinks it), and ``player._handle_stop``
calls :meth:`Beds.on_stop` before it unlinks, so a stop is never missed.

Whether it dips is asked live, on every weigh:
``duckswitch.decide`` answers per-call --duck/--no-duck > the mouth duck
override > the music_duck cascade (session > seat > agent > settings) >
built-in on. Off means no dip: the music plays on at its own level, lines mix
over it, and the user's voice never cuts it. A ``mouth duck`` override set mid-song
reaches the playing bed with no restart.

Once, "ducking off" on the music's own device meant the user's words cut the
music, and a line waited for it to end.

Now, with ducking off, the music must not dip, and singing along must not
stop it (karaoke). And an override must reach a bed that is already
playing, which only a gain inside an open mixer can do.

So off means "don't dip". Every played file mixes, the user's voice never cuts
it, and lines mix over it. ``mouth stop`` and ``mouth retract`` still stop
the music, and the user's voice still cuts a spoken line. A file for another
device never waits.

State is ``bed.json`` beside ``playing.json`` (never inside it): one entry per
canonical device with a live bed, with ``ducked`` and ``duck_by``. ``mouth
retract``, ``mouth status`` and ``mouth duck`` read it.
"""

from __future__ import annotations

import json
import math
import os
import queue
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from . import backends as _backends
from . import box as _box
from . import duckswitch as _duckswitch
from . import hold as _hold
from . import output as _output
from . import voices as _voices

BED_JSON = "bed.json"
LOOKAHEAD_S = 2.0     # decoded audio held ahead of the device: bounded, unlike Synth
POLL_S = 0.1          # how often a bed looks for a stop, a now line, a retract, the user's words
KEY_TTL_S = 30.0      # how long a looked-up device key ("call", "default") is trusted


# -- which device: one key per device, whatever string named it ---------------

_KEYS: dict = {}
_KEYS_LOCK = threading.Lock()


def device_key(raw: Optional[str]) -> Optional[str]:
    """The canonical device a line or file plays on, as one key: ``null``,
    ``call:<N>``, or the CoreAudio name in lower case (``resolve`` matches names
    case-insensitively, exactly). Only ``call`` (the one live call) and
    ``default`` need a look-up: those are cached for ``KEY_TTL_S``, because
    ``find_call`` probes a socket and ``_next`` asks on every poll."""
    if not raw:
        return None
    raw = str(raw).strip()
    low = raw.lower()
    if low == "null":
        return "null"
    if low.startswith("call:") and low[5:].strip().isdigit():
        return f"call:{int(low[5:])}"
    if low != "call" and low != "default":
        return low
    now = time.monotonic()
    with _KEYS_LOCK:
        hit = _KEYS.get(low)
        if hit and hit[1] > now:
            return hit[0]
    try:
        key = f"call:{_output.find_call(raw)[0]}" if low == "call" else _output.resolve("default")[1].lower()
    except Exception:  # noqa: BLE001 - absent: its own name; opening it says so
        key = low
    with _KEYS_LOCK:
        _KEYS[low] = (key, now + KEY_TTL_S)
    return key


def _duck_level(duck: dict) -> int:
    """The item's duck level (0-100), the built-in where it carries none or a bad one."""
    v = _voices._pct(duck.get("level"))
    return _voices.BUILTIN_DUCK["music_duck_level"] if v is None else v


def _restore_s(duck: dict) -> float:
    v = _voices._secs(duck.get("restore_s"))
    return _voices.BUILTIN_DUCK["music_duck_restore_s"] if v is None else v


def is_file(item: dict) -> bool:
    """A played file, which becomes a bed. A say-again replay (``again_of``)
    has a ``file`` but is a spoken line: ``voices.played_file``."""
    return _voices.played_file(item)


def _from(item: dict) -> str:
    """Who set an item's stamped duck, in ``duckswitch.decide``'s words: ``argument``
    for a per-call ``--duck``/``--no-duck``, else its cascade layer (``builtin`` if none)."""
    duck = item.get("duck")
    if not isinstance(duck, dict):
        return "builtin"
    src = duck.get("from")
    return "argument" if src == "flag" else str(src or "builtin")


def ducks(item: dict) -> bool:
    """Whether a played file DIPS under speech: its duck is on (the default).
    Every bed mixes either way: this names only the dip."""
    duck = item.get("duck")
    return bool(duck.get("on", True)) if isinstance(duck, dict) else True


# -- the mixers: one per device while a bed plays --------------

_MIX: dict = {}
_MIX_LOCK = threading.Lock()


def mixer_for(device: Optional[str]) -> "Optional[_output.Mixer]":
    """The mixer a line to ``device`` joins, if a bed plays there (keyed by
    :func:`device_key`, the canonical device). With no mixer live anywhere it
    returns at once, without a look-up: no bed, no change to the speech path."""
    if not _MIX or not device:
        return None
    key = device_key(device)
    with _MIX_LOCK:
        mix = _MIX.get(key)
    if mix is None:
        return None
    if mix.takes_speech():
        return mix
    if mix.closing:
        # its bed has ended and it is draining: the line opens its own output
        # only once the mixer's is closed (on a call, never two connections)
        mix.wait_closed(mix.lead + 3.0)
    return None


def _closed(mix: "_output.Mixer") -> None:
    with _MIX_LOCK:
        for k, m in list(_MIX.items()):
            if m is mix:
                del _MIX[k]


def open_bed_lane(key: Optional[str], device: str, sample_rate: int, pan: Optional[float],
                  volume: Optional[float], meta: dict, utt: Optional[str] = None) -> "_output.MixLane":
    """The bed's lane on its device's mixer. A mixer a line is still finishing
    over the last bed is joined, never doubled (one connection on a call); one
    that is closing is waited for first."""
    with _MIX_LOCK:
        old = _MIX.get(key)
    if old is not None:
        lane = old.bed_lane(pan, volume, meta, utt)
        if lane is not None:
            return lane
        old.wait_closed(old.lead + 3.0)
    mix = _output.Mixer(device, sample_rate, meta=meta, on_close=_closed)
    lane = mix.bed_lane(pan, volume, meta, utt)
    with _MIX_LOCK:
        _MIX[key] = mix
    return lane


# -- the decode: ~2 s ahead, never the whole song ---------------------------

_END = object()


class Lookahead:
    """A file decoded on its own thread into a bounded queue (about ``ahead_s``).

    Not :class:`player.Synth`, which keeps every block: 17 MB for a 3-minute
    song, unbounded for a long mix."""

    def __init__(self, backend, text: str, ahead_s: float = LOOKAHEAD_S) -> None:
        self.backend = backend
        self.sample_rate = backend.sample_rate
        self.q: queue.Queue = queue.Queue(maxsize=max(2, math.ceil(ahead_s / 0.1)))
        self.frames = 0
        self.finished = False
        self.error: Optional[BaseException] = None
        self.t0 = time.monotonic()
        self.t_first: Optional[float] = None
        self._cancel = threading.Event()
        threading.Thread(target=self._run, args=(text,), daemon=True, name="bed-decode").start()

    def _put(self, x) -> bool:
        while not self._cancel.is_set():
            try:
                self.q.put(x, timeout=0.1)
                return True
            except queue.Full:
                continue
        return False

    def _run(self, text: str) -> None:
        gen = None
        try:
            gen = self.backend.stream(text, "", None)
            for block in gen:
                if self.t_first is None:
                    self.t_first = time.monotonic()
                if not self._put(block):
                    break
                self.frames += len(block)
            else:
                self.finished = True
        except BaseException as e:  # noqa: BLE001 - surfaced through blocks()
            self.error = e
        finally:
            if gen is not None:
                try:
                    gen.close()  # a cut stops ffmpeg now
                except Exception:  # noqa: BLE001
                    pass
            self._put(_END)

    @property
    def gen_s(self) -> Optional[float]:
        return round(self.t_first - self.t0, 3) if self.t_first is not None else None

    def cancel(self) -> None:
        self._cancel.set()

    def blocks(self, tick: float = _backends.BLOCK_S):
        """Decoded blocks in order; ``None`` every ``tick`` while waiting."""
        while True:
            try:
                b = self.q.get(timeout=tick)
            except queue.Empty:
                yield None
                continue
            if b is _END:
                if self.error is not None:
                    raise self.error
                return
            yield b


# -- the duck: two causes, one gain ------------------------------

class Ducker:
    """A bed's duck: a small state machine with two causes, behind a gate.

    - ``say``: a line is playing in this player (:attr:`Beds.saying`), on any
      device, from its first frame until the player finds nothing
      playable, so stacked lines hold ONE duck (no pumping);
    - ``barge``: the user has the floor on the bed's device (``hold.speaking_over``),
      only where ``hold.barge_device`` says barge-in counts.

    Either cause sends the mixer to ``level``; it comes back to 1.0 once both
    have been clear for ``restore_s``. The mixer ramps per sample. Rows: an
    ``event: duck`` when the ramp down starts, ``event: unduck`` when the ramp
    up starts; a change of cause mid-dip stays ONE duck. ``count`` and
    ``ducked_s`` go on the music's ``said``.

    The gate: ``gate()`` answers ``(dip?, by)`` on
    every weigh (``duckswitch.decide``: argument > override > the cascade), so a
    ``mouth duck`` override set mid-song reaches this bed with no restart. Gate
    no: the mixer stays at 1.0 (a dip already down comes up at once, ``unduck``
    with that ``by``), and a would-be dip writes ONE ``event: duck-skipped`` row,
    latched like ``down`` (``skipping``) until the causes have been clear for
    ``restore_s``. The latch never blocks a dip: gate yes, and a live cause dips
    on that weigh. ``skipped`` goes on the ``said`` as ``ducks_skipped``."""

    def __init__(self, bed: "Bed", mixer, level: int, restore_s: float,
                 over: Optional[Callable[[], bool]], device: Optional[str],
                 gate: Optional[Callable[[], tuple]] = None) -> None:
        self.bed, self.mixer = bed, mixer
        self.gate = gate
        self.by: Optional[str] = None      # who answered the gate last (bed.json's duck_by)
        self.skipping = False              # a would-be dip the gate stopped is live (one row)
        self.skipped = 0
        self.level = level
        self.restore_s = max(0.0, float(restore_s))
        self.over = over
        self.device = device
        self.down = False
        self.count = 0
        self._ducked = 0.0
        self._t_down = 0.0
        self._last_cause = 0.0
        self.causes: list = []
        self._lock = threading.Lock()

    @property
    def ducked_s(self) -> float:
        extra = time.monotonic() - self._t_down if self.down else 0.0
        return round(self._ducked + extra, 3)

    def _row(self, name: str, **kw) -> None:
        """An ``event`` row where the music's saying/said go (``source: mouth``,
        so it never moves the floor). A row that cannot be written never stops it."""
        from ears import heard

        from . import maillog
        from . import player as _player

        item = self.bed.item
        try:
            rec = heard.event(name, utt=self.bed.utt, device=self.device, source="mouth",
                              directory=_player._log_dir(item), session=item.get("session"),
                              agent=item.get("agent"), **kw)
        except (OSError, ValueError):
            return
        maillog.entry(item, name, rec)

    def tick(self, barge: Optional[bool] = None) -> None:
        """Weigh both causes now (the bed's 100 ms poll, and the player at a
        line's first frame). ``barge`` is the floor's answer if the caller has
        it; None asks ``over``."""
        if barge is None:
            barge = bool(self.over()) if self.over is not None else False
        say = self.bed.beds.saying
        causes = [c for c, on in (("say", say), ("barge", barge)) if on]
        if self._weigh(causes, time.monotonic()):
            self.bed.beds._write()   # bed.json's ``ducked``/``duck_by``: mouth status shows them

    def _ask(self) -> tuple:
        """The gate's ``(dip?, by)``; a gate that cannot answer leaves the
        stamped duck in charge (the bed's own ``ducks``), never the music stopped."""
        if self.gate is None:
            return self.bed.ducks, _from(self.bed.item)
        try:
            on, by = self.gate()
            return bool(on), str(by)
        except Exception:  # noqa: BLE001 - a duck that cannot weigh never stops the music
            return self.bed.ducks, _from(self.bed.item)

    def _weigh(self, causes: list, now: float) -> bool:
        """True if the duck went down or came back up, or the gate's ``by`` changed."""
        on, by = self._ask()
        with self._lock:
            moved = by != self.by
            self.by = by
            if causes:
                self._last_cause = now
            quiet = not causes and now - self._last_cause >= self.restore_s
            if not on:
                if self.down:   # the gate turned mid-dip (an override): up at once
                    self._up(now, by)
                    self.skipping = True   # the dip it was is the would-be dip: no second row
                    moved = True
                if causes and not self.skipping:
                    self.skipping = True
                    self.skipped += 1
                    self._row("duck-skipped", lane="bed", why=causes[0], causes=list(causes), by=by)
                elif self.skipping and quiet:
                    self.skipping = False
                return moved
            self.skipping = False
            if not self.down:
                if not causes:
                    return moved
                self.down, self._t_down = True, now
                self.count += 1
                self.causes = list(causes)
                self.mixer.set_duck(self.level / 100.0)
                self._row("duck", level=self.level, why=causes[0], causes=list(causes), by=by)
                return True
            for c in causes:
                if c not in self.causes:
                    self.causes.append(c)
            if not quiet:
                return moved
            self._up(now, by)
            return True

    def _up(self, now: float, by: str) -> None:
        """The dip ends: the mixer ramps back to 1.0 and the ``unduck`` row says why."""
        self.down = False
        dur = now - self._t_down
        self._ducked += dur
        self.mixer.set_duck(1.0)
        self._row("unduck", ducked_s=round(dur, 3), causes=list(self.causes), by=by)


# -- one bed -------------------------------------------------------------------

class _Cut(Exception):
    pass


class Bed:
    """One played file on its own thread, on one device, until it ends or is cut."""

    def __init__(self, beds: "Beds", item: dict, taken: Optional[Path], key: Optional[str],
                 leapfrog: bool = False) -> None:
        self.beds, self.item, self.taken, self.key = beds, item, taken, key
        self.leapfrog = leapfrog                    # started ahead of a queued line
        self.d = beds.d
        self.utt = item["utt"]
        self.text = str(item.get("text") or "")
        self.requested_t = float(item.get("requested_t") or 0.0)
        self.started_t: Optional[float] = None     # epoch of its first frame
        self.device: Optional[str] = item.get("device")
        self.ducks = ducks(item)                    # it dips under speech; every bed mixes
        self.ready = threading.Event()              # its output is open (or failed): lines may go
        self._why: Optional[str] = None
        self._lock = threading.Lock()
        self._q = _box.Queue(self.d)                # its OWN view, read-only: the player acts
        self._next_look = 0.0
        self.ducker: Optional[Ducker] = None        # every bed's, once its first frame plays
        self.rec: Optional[dict] = None
        self.thread = threading.Thread(target=self._run, daemon=True, name=f"bed-{self.utt}")

    # -- being cut -----------------------------------------------------------

    def cut(self, reason: str) -> bool:
        """Cut it (once; the first reason stands). True if this call cut it."""
        with self._lock:
            if self._why is not None:
                return False
            self._why = reason
            return True

    def hit_by(self, stop: Optional[dict]) -> bool:
        """Whether a stop record cuts this bed: one aimed at its utt, or a
        general stop at or after it was requested that is NOT a barge-in (a
        bed never obeys a barge-in: the user's voice ducks it, or with ducking off
        leaves it be)."""
        if not stop:
            return False
        if stop.get("utt"):
            return stop["utt"] == self.utt
        if (stop.get("reason") or "stop") == "barge-in":
            return False
        return float(stop.get("t", 0) or 0) >= self.requested_t

    def on_stop(self, stop: Optional[dict]) -> None:
        """``_handle_stop`` hands every bed the stop before it unlinks it."""
        if self.hit_by(stop):
            self.cut(stop.get("reason") or "stop")

    def _mine(self, x: dict) -> bool:
        """A ``now`` line that cuts this bed: none. The line mixes over the bed,
        and a ``now`` cuts the line playing, never the music (every bed). A
        retract still cuts it."""
        return False

    def _poll(self) -> None:
        """Every ``POLL_S``: a stop (read, never unlinked) or a retract (mail or
        CLI: the same queue) cuts it; the user's words never do, they feed its
        :class:`Ducker`. Raises _Cut."""
        if self._why is not None:
            raise _Cut
        now = time.monotonic()
        if now < self._next_look:
            return
        self._next_look = now + POLL_S
        from . import player as _player

        stop = _player._read_stop(self.d)
        if self.hit_by(stop):
            self.cut(stop.get("reason") or "stop")
            raise _Cut
        why = _player._cut_by_mail(self._q, self.item, same=self._mine)
        if why:
            self.cut(why)
            raise _Cut
        if self.ducker is not None:
            try:
                self.ducker.tick()
            except Exception:  # noqa: BLE001 - a duck that cannot weigh never stops the music
                pass

    # -- playing -------------------------------------------------------------

    def _run(self) -> None:
        from . import player as _player

        try:
            self.rec = self._play()
        except Exception as e:  # noqa: BLE001 - one bad file must never stall anything
            self.rec = _player.said_unplayed(self.item, "error", detail=f"{type(e).__name__}: {e}"[:300])
        finally:
            self.ready.set()
            self.beds._end(self)

    def _play(self) -> dict:
        from . import player as _player

        item, text = self.item, self.text
        try:
            dec = Lookahead(_backends.FileSound(item["file"], item.get("start"), item.get("end")), text)
        except Exception as e:  # noqa: BLE001
            return _player.said_unplayed(item, "error", detail=str(e)[:300])
        sr = dec.sample_rate
        meta = {"text": text, "who": item.get("agent")}
        try:   # every bed: the mixer's bed_volume is its gain; lines join it
            level = _voices._pct(item.get("volume"))
            out = open_bed_lane(self.key, item["device"], sr, item.get("pan"),
                                None if level is None else level / 100.0, meta, self.utt)
        except Exception as e:  # noqa: BLE001 - absent, or PortAudio refusing to open it
            dec.cancel()
            detail = str(e) if isinstance(e, _output.DeviceAbsent) else f"{type(e).__name__}: {e}"
            return _player.said_unplayed(item, "device-absent", detail=detail[:300])
        finally:
            self.ready.set()   # a line to its device waits for this, not longer
        name = getattr(out, "name", item["device"])
        self.device = name
        step = int(sr * _backends.BLOCK_S)
        written, started = 0, False
        reason, detail = "done", None
        over = None
        if _hold.barge_device(name):   # the user's voice ducks it, never cuts it: the floor, polled
            who = _hold.barge_in(item["device"])

            def over():
                return _hold.speaking_over(item["device"], who=who) is not None
        duck = item.get("duck") if isinstance(item.get("duck"), dict) else {}
        try:
            for block in dec.blocks():
                if block is None:
                    self._poll()
                    continue
                for i in range(0, len(block), step):
                    self._poll()
                    if not started:
                        self.started_t = time.time()
                        _player._duck_on(item, self.d)   # its own --duck/--no-duck beats the override
                        _player._saying(item, text, **_player._common(item), device=name,
                                        speed=item.get("speed"), gen_s=dec.gen_s,
                                        pan_ignored=getattr(out, "pan_ignored", None),
                                        lane="file", duck=item.get("duck"),
                                        leapfrog=self.leapfrog or None)
                        self.beds._write()
                        started = True
                        # a line already playing ducks it at once (unless its duck is off)
                        self.ducker = Ducker(self, out.mixer, _duck_level(duck),
                                             _restore_s(duck), over, name, gate=self._gate)
                        self.ducker.tick()
                    sub = block[i:i + step]
                    out.write(sub)
                    written += len(sub)
            out.drain()
        except _Cut:
            dec.cancel()
            _player._abort(out)
            reason = self._why or "stop"
        except Exception as e:  # noqa: BLE001
            dec.cancel()
            try:
                _player._abort(out)
            except Exception:  # noqa: BLE001
                pass
            ename = type(e).__name__
            lost = isinstance(e, _output.DeviceLost) or "PortAudio" in ename
            reason = "device-lost" if lost else "error"
            detail = f"{ename}: {e}"[:300]

        cut = reason != "done"
        played = getattr(out, "frames_played", written) / sr
        if cut:
            played = max(0.0, played - getattr(out, "latency", 0.0))
        dur = dec.frames / sr if dec.finished else None
        est_dur = dur or dec.frames / sr
        return _player._said(
            item, **_player._common(item), device=name,
            played_s=round(played, 3), dur_s=round(dur, 3) if dur is not None else None,
            cut=cut, cut_at_s=round(played, 3) if cut else None,
            text_played_est=text if not cut else _player.text_at(text, played / est_dur if est_dur else 0.0),
            underrun_s=round(getattr(out, "underrun_frames", 0) / sr, 3),
            underruns=getattr(out, "underruns", 0),
            # the device's own gaps (its callback ran late), beside the underruns
            device_gaps=getattr(out, "device_gaps", 0),
            device_gap_s=round(getattr(out, "device_gap_s", 0.0), 3),
            device_gap_max_s=round(getattr(out, "device_gap_max_s", 0.0), 3),
            reason=reason, detail=detail,
            lane="file", duck=item.get("duck"),
            ducks=self.ducker.count if self.ducker else 0,
            ducked_s=self.ducker.ducked_s if self.ducker else 0.0,
            ducks_skipped=self.ducker.skipped if self.ducker else 0)

    def _gate(self) -> tuple:
        """Dip or not, asked live: its own item, so its
        per-call ``--duck``/``--no-duck`` beats a ``mouth duck`` override."""
        return _duckswitch.decide(self.item, self.d)

    def entry(self) -> dict:
        """Its ``bed.json`` entry (and ``mouth status``'s music row)."""
        it = self.item
        return {"utt": self.utt, "device": self.device, "key": self.key, "file": it.get("file"),
                "text": self.text, "mail_id": it.get("mail_id"), "requested_t": self.requested_t,
                "started_t": self.started_t, "volume": it.get("volume"),
                "volume_from": it.get("volume_from"), "duck": it.get("duck"),
                "ducked": bool(self.ducker and self.ducker.down),
                "duck_by": self.ducker.by if self.ducker else None}


# -- every bed of one player ---------------------------------------------------

class Beds:
    """The live beds of one player (one ``serve``), one per device key."""

    def __init__(self, d: Path) -> None:
        self.d = Path(d)
        self._lock = threading.RLock()
        self._live: dict = {}
        self.saying = False   # the duck's ``say`` cause: a line is playing in this player
        (self.d / BED_JSON).unlink(missing_ok=True)   # a dead player's beds are not playing

    def any(self) -> bool:
        return bool(self._live)

    def on(self, x: dict) -> Optional[Bed]:
        """The live bed on the device ``x`` plays on, if there is one."""
        if not self._live:
            return None
        key = device_key(x.get("device"))
        with self._lock:
            return self._live.get(key)

    def live(self) -> list:
        with self._lock:
            return list(self._live.values())

    def items(self) -> list:
        """What each bed plays (``public`` items), for the box's retract look."""
        return [b.item for b in self.live()]

    def start(self, item: dict, taken: Optional[Path], leapfrog: bool = False) -> Bed:
        """Hand a taken file to a new bed on its device's thread, and return at once.
        ``leapfrog``: it jumped a line queued ahead of it (``player._leapfrog``)."""
        key = device_key(item.get("device"))
        bed = Bed(self, item, taken, key, leapfrog)
        with self._lock:
            self._live[key] = bed
            self._write()
        bed.thread.start()
        return bed

    def on_stop(self, stop: Optional[dict]) -> None:
        for bed in self.live():
            bed.on_stop(stop)

    def line_started(self) -> None:
        """A line's first frame (``play_one``): every bed weighs now, not at its
        next poll, so the dip (if its gate says dip) starts with the line."""
        self.saying = True
        for bed in self.live():
            if bed.ducker is not None:
                try:
                    bed.ducker.tick(barge=False)   # the user's voice is the bed's own poll's
                except Exception:  # noqa: BLE001 - a duck must never stop a line
                    pass

    def lines_done(self) -> None:
        """The player found nothing playable: the ``say`` cause lets go (its beds
        come back up after ``restore_s``). Between stacked lines it never is."""
        self.saying = False

    def wait(self, timeout: float = 5.0) -> bool:
        """Join every bed (tests, shutdown). True if none is left."""
        deadline = time.monotonic() + timeout
        for bed in self.live():
            bed.thread.join(max(0.0, deadline - time.monotonic()))
        return not self._live

    def _write(self) -> None:
        with self._lock:
            data = {str(k): b.entry() for k, b in self._live.items()}
            p = self.d / BED_JSON
            if not data:
                p.unlink(missing_ok=True)
                return
            tmp = self.d / f".{BED_JSON}.{os.getpid()}.part"
            tmp.write_text(json.dumps(data))
            tmp.rename(p)

    def _end(self, bed: Bed) -> None:
        """A bed ended: file its mail, answer a waiter, then free its device
        (the ``said`` is written before a line waiting on the device starts)."""
        from . import player as _player

        rec = bed.rec or {}
        try:
            if bed.taken is not None:
                _box.file_to(bed.taken, _player._flags(rec))
            _player._done(self.d, bed.item, rec)
            # a stop aimed at this bed has done its work; left in place it would
            # hide the mail look of every line after it (check_stop skips it then)
            stop = _player._read_stop(self.d)
            if stop and stop.get("utt") == bed.utt:
                (self.d / "stop").unlink(missing_ok=True)
        finally:
            with self._lock:
                if self._live.get(bed.key) is bed:
                    del self._live[bed.key]
                self._write()
        print(json.dumps({k: rec.get(k) for k in ("seq", "utt", "reason", "played_s", "dur_s")}),
              flush=True)


def read(d: Path) -> list:
    """The live beds as ``bed.json`` lists them: ``[entry, ...]``."""
    try:
        data = json.loads((Path(d) / BED_JSON).read_text())
    except (FileNotFoundError, ValueError):
        return []
    return list(data.values()) if isinstance(data, dict) else []
