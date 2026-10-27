"""Where the audio goes: ONE named device, or nowhere. Never another device by accident.

When AirPods drop, the system default falls back to the laptop's own
speakers. So a device is named exactly
(case-insensitive), never matched as a substring: ``airpods`` must not pick
the ``Speakers + AirPods`` aggregate, which plays through the speakers too.
A named device that is absent is ``device-absent``, never the default instead.
``default`` is the system default, by that name: asked for, or the last rung of
the one device order when nothing names a device (``mouth.resolve_device``).
``null`` plays nothing, in real time, so stop and cut behave as they would live.
``call:<N>`` speaks into live Delta Chat call N through a Delta bot's mouth
leg (``call`` alone: the one live call). See :class:`CallOut`.

``pan`` puts a voice in one ear (the left AirPod, or the right):
-1 is left only, 1 right only, equal-power
between. Without a pan the stream is mono, exactly as before. A 1-channel
device plays mono and says so (``pan_ignored``) rather than dropping words.
"""

from __future__ import annotations

import json
import math
import os
import queue
import socket
import struct
import threading
import time
from collections import deque
from typing import Optional

import numpy as np

from ears import envnames

from . import canplay as _canplay
from .canplay import DeviceAbsent, NoOutputDevice, PortAudioMissing  # noqa: F401 - output.* names


def _sd():
    """``sounddevice``; :class:`PortAudioMissing` (a :class:`DeviceAbsent`) without PortAudio."""
    return _canplay._sd()


#: PortAudio's rescan is a terminate and an initialize, and a terminate closes
#: EVERY open stream in the process. With beds a file plays on one
#: device while a line opens another, so the rescan waits until no DeviceOut is
#: open; until then the device list is the one PortAudio already has.
_PA = threading.RLock()
_open_streams = 0


def output_names(rescan: bool = True) -> list[str]:
    sd = _sd()
    with _PA:
        if rescan and not _open_streams:  # PortAudio caches the list at init; a resident player must look again
            sd._terminate()
            sd._initialize()
        return [d["name"] for d in sd.query_devices() if d["max_output_channels"] > 0]


def resolve(name: str) -> tuple[Optional[int], str]:
    """(PortAudio index or None for the default, the device's own name). Exact names only."""
    sd = _sd()
    names = output_names()
    if name == "default":
        idx = sd.default.device[1]
        if idx is None or idx < 0:
            raise DeviceAbsent("no default output device")
        return None, sd.query_devices(idx)["name"]
    for i, d in enumerate(sd.query_devices()):
        if d["max_output_channels"] > 0 and d["name"].lower() == name.lower():
            return i, d["name"]
    raise DeviceAbsent(f"output device {name!r} is not present; present: {', '.join(names)}")


class DeviceLost(RuntimeError):
    """The device stopped taking audio mid-play (AirPods out of the ear, into the case)."""


def pan_gains(pan: float) -> tuple[float, float]:
    """Equal-power (left, right) gains for pan in [-1, 1]; -1 is left only."""
    pan = max(-1.0, min(1.0, float(pan)))
    theta = (pan + 1.0) * math.pi / 4.0
    left, right = math.cos(theta), math.sin(theta)
    return (0.0 if left < 1e-9 else left, 0.0 if right < 1e-9 else right)


class NullOut:
    """Plays nothing, in real time, with a one-block buffer like a real device's."""

    name = "null"
    latency = 0.05  # the writer may run this far ahead; a cut drops it
    pan_ignored = None
    # no device callback, so no device gaps: always 0 (DeviceOut has the measure)
    device_gaps = 0
    device_gap_s = 0.0
    device_gap_max_s = 0.0

    def __init__(self, sample_rate: int, pan: Optional[float] = None) -> None:
        self.sample_rate = sample_rate
        self.frames_played = 0
        self.underrun_frames = 0
        self.underruns = 0
        self._clock: Optional[float] = None  # when the audio written so far ends

    def write(self, block: np.ndarray) -> None:
        now = time.monotonic()
        if self._clock is not None and now > self._clock + 0.005:  # the buffer ran dry
            self.underrun_frames += int((now - self._clock) * self.sample_rate)
            self.underruns += 1
        start = now if self._clock is None else max(now, self._clock)
        self._clock = start + len(block) / self.sample_rate
        time.sleep(max(0.0, self._clock - self.latency - now))
        self.frames_played += len(block)

    def drain(self) -> None:
        if self._clock is not None:
            time.sleep(max(0.0, self._clock - time.monotonic()))

    def abort(self) -> None:
        pass


LINE_DUCK_DOWN_S = 0.05   # a barge duck's ramp down, full depth: fast, and still no click
LINE_DUCK_UP_S = 0.5      # and back up when no cut follows (the bed's DUCK_UP_S)


class LineDuck:
    """A gain on everything a :class:`DeviceOut` plays, applied in its audio
    callback: AFTER the ~200 ms queue, so a duck is heard as soon as the device
    pulls its next buffer, not 4 blocks later (measured: the gain at write
    leaves up to 200 ms queued at full level). A
    :class:`CallOut` has one too, applied in its ``write``: a call has no
    callback, so the write is the latest point the mouth holds.

    :meth:`set` moves the target (1.0 is no duck); the callback ramps to it per
    sample, down over ``down_s`` and up over ``up_s`` for the full depth.
    At 1.0 and still, :meth:`apply` returns at once: a line nobody barges in
    on costs one comparison per buffer. ``trace`` keeps (asked, applied,
    ahead) for each duck down: asked and applied are monotonic times, asked
    when :meth:`set` was called, applied when the callback first scaled a
    buffer for it. ``ahead`` is :attr:`ahead_s` at that moment, the seconds of
    audio already past the duck, which it can never reach: the owner keeps it
    current (the device's output latency; a call's paced lead), None if
    unknown."""

    def __init__(self, rate: int, down_s: float = LINE_DUCK_DOWN_S, up_s: float = LINE_DUCK_UP_S) -> None:
        self.rate, self.down_s, self.up_s = rate, down_s, up_s
        self.gain = 1.0          # now (the callback's)
        self.target = 1.0        # where it is going
        self._depth = 0.0
        self._asked: Optional[float] = None
        self.ahead_s: Optional[float] = None   # audio already past the duck, s (the owner's)
        self.trace: deque = deque(maxlen=256)

    def set(self, target: float) -> None:
        target = min(1.0, max(0.0, float(target)))
        if target < 1.0:
            self._depth = 1.0 - target
            if target < self.target:
                self._asked = time.monotonic()
        self.target = target

    @property
    def ducked(self) -> bool:
        return self.target < 1.0

    def apply(self, outdata: np.ndarray) -> None:
        """Scale ``outdata`` (frames, channels) in place: the callback's one call."""
        cur, tgt = self.gain, self.target
        if cur == tgt:
            if cur != 1.0:
                outdata *= cur
            return
        if self._asked is not None and tgt < cur:
            self.trace.append((self._asked, time.monotonic(), self.ahead_s))
            self._asked = None
        n = len(outdata)
        depth = self._depth or abs(tgt - cur)
        step = depth / ((self.down_s if tgt < cur else self.up_s) * self.rate)
        k = np.arange(1, n + 1, dtype=np.float32) * np.float32(step)
        g = np.maximum(cur - k, tgt) if tgt < cur else np.minimum(cur + k, tgt)
        self.gain = float(g[-1]) if n else cur
        outdata *= g.reshape(-1, 1).astype(outdata.dtype, copy=False)


class DeviceOut:
    """A callback stream fed from a short queue, so the player never blocks on the device.

    A blocking ``stream.write`` can wait forever when CoreAudio stops pulling
    (seen in review): the player hangs, the queue stalls and
    no ``said`` is written. Here ``write`` waits at most ``lost_after_s`` for
    room in the queue, and ``drain`` at most the audio still queued plus
    ``lost_after_s``; past either, ``DeviceLost``. ``frames_played`` counts
    what the device actually pulled, not what was handed over.
    """

    queue_blocks = 4     # ~200 ms of 50 ms blocks between the player and the device
    lost_after_s = 1.0   # Jefferson's "standard maximum" silence, as it happens

    # Device gaps: time the device played that this callback never
    # filled, because the callback ran LATE (the host was starved: load 58,
    # measured). Not an underrun: there the callback ran on time
    # and found the queue empty. PortAudio's output_underflow flag read 0
    # through every such gap on CoreAudio, so the gap
    # is measured off the callback, from the clock: the callback only adds the
    # frames it was asked for (``frames_pulled``), and :meth:`_gap_sample`, on
    # the writer's side, compares wall time passed with audio pulled. Late
    # samples in a row are ONE episode: ``device_gaps`` counts episodes,
    # ``device_gap_s`` totals their lost time (to about one buffer) and
    # ``device_gap_max_s`` is the longest episode, at ~50 ms resolution (not
    # the longest single silence).
    GAP_MIN_S = 0.02     # never call less than this a gap, whatever the buffer

    # Underruns: silence the device got MID-line because synthesis fell behind
    # (once a clone dropped below real time and every
    # line broke up, 1.5-8.2 s of gaps each). Always counted. With
    # $MOUTH_REBUFFER_S > 0, a dry-out HOLDS until that much is
    # buffered again: a few longer pauses instead of many 50 ms stutters. Off
    # (0) by default: it changes how a line sounds, so it waits for a word.

    def __init__(self, name: str, sample_rate: int, pan: Optional[float] = None) -> None:
        self._counted = False
        with _PA:   # no rescan between finding the device and opening it
            self._open(name, sample_rate, pan)

    def _open(self, name: str, sample_rate: int, pan: Optional[float]) -> None:
        global _open_streams
        idx, self.name = resolve(name)
        sd = _sd()
        self.sample_rate = sample_rate
        self.gains: Optional[np.ndarray] = None
        self.pan_ignored: Optional[str] = None
        channels = 1
        if pan is not None:
            # the device's own count, the default's too: AirPods hands-free while
            # their mic is held have 1, and asking a 1-channel device for 2 is -9998
            have = sd.query_devices(idx if idx is not None
                                    else sd.default.device[1])["max_output_channels"]
            if have >= 2:
                channels = 2
                self.gains = np.array(pan_gains(pan), dtype=np.float32)
            else:
                self.pan_ignored = f"{have}-channel device"
        self.rebuffer_s = float(envnames.get("MOUTH_REBUFFER_S") or 0)
        self.q: queue.Queue = queue.Queue(
            maxsize=max(self.queue_blocks, math.ceil(self.rebuffer_s / 0.05) + 2))
        self.frames_queued = 0
        self.frames_played = 0
        self.underrun_frames = 0
        self.underruns = 0
        self.frames_pulled = 0          # every frame the device asked for, zeros too
        self.device_gaps = 0
        self.device_gap_s = 0.0
        self.device_gap_max_s = 0.0
        self._gap_ep = 0.0              # the open episode's lost time so far (a mixer reads it)
        self._gap_last = 0.0            # the last closed episode's lost time
        self._gap_t: Optional[float] = None   # the last sample: monotonic time ...
        self._gap_pulled = 0                  # ... and frames_pulled then
        self._gap_b = 0                 # the callback's buffer, learned: gcd of pulled steps
        self._gap_done = False          # aborted: no more samples
        self._gap_lock = threading.Lock()     # writer side only, never the callback
        self.draining = False
        self._dry = False
        self._hold = False
        self._cur: Optional[np.ndarray] = None
        self._pos = 0
        self.duck = LineDuck(sample_rate)   # a barge duck, after the queue
        self.stream = sd.OutputStream(device=idx, samplerate=sample_rate, channels=channels,
                                      dtype="float32", callback=self._callback)
        _open_streams += 1
        self._counted = True
        try:
            self.stream.start()
        except BaseException:
            self.abort()
            raise
        self.latency = float(getattr(self.stream, "latency", 0.0) or 0.0)
        self.duck.ahead_s = self.latency   # after the callback: only the device's own buffer

    def _callback(self, outdata, frames, time_info, status) -> None:  # noqa: ARG002
        self.frames_pulled += frames    # the only gap work here; _gap_sample does the rest
        if self._hold:
            ahead = (self.frames_queued - self.frames_played) / self.sample_rate
            if ahead >= self.rebuffer_s or self.draining:
                self._hold = False
            else:
                outdata[:] = 0
                self.underrun_frames += frames
                return
        filled = 0
        while filled < frames:
            if self._cur is None or self._pos >= len(self._cur):
                try:
                    self._cur, self._pos = self.q.get_nowait(), 0
                except queue.Empty:
                    break
            n = min(frames - filled, len(self._cur) - self._pos)
            outdata[filled:filled + n] = self._cur[self._pos:self._pos + n]
            self._pos += n
            filled += n
        if filled < frames:
            outdata[filled:] = 0
            if self.frames_queued > 0 and not self.draining:
                self.underrun_frames += frames - filled
                if not self._dry:
                    self.underruns += 1
                    self._dry = True
                if self.rebuffer_s > 0:
                    self._hold = True
        else:
            self._dry = False
        self.frames_played += filled
        self.duck.apply(outdata)

    def write(self, block: np.ndarray) -> None:
        if block.ndim == 2:
            frames = block            # a mixer's block: its lanes are panned already
        else:
            frames = block.reshape(-1, 1)
            if self.gains is not None:
                frames = frames * self.gains  # (n, 1) * (2,) -> (n, 2)
        try:
            self.q.put(frames, timeout=self.lost_after_s)
        except queue.Full:
            raise DeviceLost(f"{self.name!r} took no audio for {self.lost_after_s:.1f}s") from None
        self.frames_queued += len(block)
        self._gap_sample()

    def _gap_sample(self, final: bool = False) -> None:
        """One slip sample, on the writer's side (never the callback): the wall
        time since the last sample less the audio pulled since. More than a
        threshold is lost time; late samples in a row merge into one episode.
        ``final`` closes an open episode (the line's end). The baseline waits
        for the device's first callback, so its start-up is never a gap."""
        with self._gap_lock:
            if self._gap_done:
                return
            now, pulled = time.monotonic(), self.frames_pulled
            if self._gap_t is None:
                if pulled > 0:            # the device has called back: start here
                    self._gap_t, self._gap_pulled, self._gap_b = now, pulled, pulled
            else:
                step = pulled - self._gap_pulled
                if step > 0:
                    self._gap_b = math.gcd(self._gap_b, step)
                slip = (now - self._gap_t) - step / self.sample_rate
                late = slip > max(self.GAP_MIN_S, 1.5 * self._gap_b / self.sample_rate)
                if not step and not late and not final:
                    # the device took nothing since the last sample, yet not long
                    # enough to call it a gap: keep the baseline, or drain()'s
                    # 10 ms polls slice a stall into pieces under the threshold
                    return
                self._gap_t, self._gap_pulled = now, pulled
                if late:
                    self._gap_ep += slip
                    self.device_gap_s += slip
                elif self._gap_ep:
                    self._gap_close()
            if final:
                if self._gap_ep:
                    self._gap_close()
                self._gap_done = True

    def _gap_close(self) -> None:
        self.device_gaps += 1
        self.device_gap_max_s = max(self.device_gap_max_s, self._gap_ep)
        self._gap_last, self._gap_ep = self._gap_ep, 0.0

    def drain(self) -> None:
        self.draining = True
        left = (self.frames_queued - self.frames_played) / self.sample_rate
        deadline = time.monotonic() + left + self.lost_after_s
        while self.frames_played < self.frames_queued:
            if time.monotonic() > deadline:
                raise DeviceLost(f"{self.name!r} stopped pulling with "
                                 f"{(self.frames_queued - self.frames_played) / self.sample_rate:.2f}s left")
            time.sleep(0.01)
            self._gap_sample()
        time.sleep(self.latency)  # the last frames are in the device's own buffer
        self.abort()

    def abort(self) -> None:
        global _open_streams
        if hasattr(self, "_gap_lock"):    # the line's last sample: a lost device's dead time counts
            self._gap_sample(final=True)
        try:
            self.stream.abort()
        finally:
            try:
                self.stream.close()
            finally:
                with _PA:
                    if self._counted:
                        self._counted = False
                        _open_streams -= 1


# -- a live call: call:<N> -----------------------------------------------------
#
# A user calls a Delta bot, the call joins the ears, and the agents' voices
# have to reach it. The bot that holds the call serves a Unix socket (its
# mouth leg); the mouth hands it
# finished audio, paced in real time a little ahead of the call, so hold,
# barge-in, retract and the playlist work exactly as they do on the AirPods.

CALL_RATE = 48000       # what the call's voice track holds
CALL_LOOK_S = 1.0       # how long a bot may take to answer a first probe
CALL_SLOW_S = 4.0       # the second look, and a line's header: under the bot's own 5 s
                        # (sessionphone mouthleg.py HEADER_TIMEOUT_S): a slow bot
                        # is not an absent one.


def call_socks() -> list[str]:
    """Every bot's mouth-leg socket. $MOUTH_CALL_SOCKS (colon-separated) overrides the
    search of every bot root the ears search (ears.listen.sources.bot_roots: sessionphone's
    root, $DC_ROOT, ~/.<agent>/delta-chat), each [/<profile>]/call-mouth.sock."""
    env = envnames.get("MOUTH_CALL_SOCKS")
    if env:
        return [os.path.expanduser(p) for p in env.split(":") if p]
    from ears.listen.sources import bot_socks   # here: the mouth needs the listen package only on a call
    return bot_socks("call-mouth.sock")


def _ask(sock_path: str, header: dict, timeout: Optional[float] = None) -> tuple[socket.socket, dict]:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(CALL_LOOK_S if timeout is None else timeout)
    try:
        s.connect(sock_path)
        s.sendall((json.dumps(header) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            got = s.recv(4096)
            if not got:
                break
            buf += got
        return s, json.loads(buf or b"{}")
    except BaseException:
        s.close()
        raise


def _look(timeout: Optional[float] = None) -> tuple[list[tuple[int, str, str]], list[str]]:
    """:func:`live_calls`, plus the sockets whose bot did not answer in time."""
    out, slow = [], []
    for path in call_socks():
        try:
            s, info = _ask(path, {"probe": True}, timeout)
            s.close()
        except socket.timeout:
            slow.append(path)   # a busy bot, not an absent one
            continue
        except (OSError, ValueError):
            continue            # a stale socket: that bot is not running
        who = info.get("agent", "?") + (f"/{info['profile']}" if info.get("profile") else "")
        out += [(int(c), who, path) for c in info.get("calls") or []]
    return out, slow


def live_calls() -> list[tuple[int, str, str]]:
    """(call id, who holds it, socket) for every live call any bot can speak into."""
    return _look()[0]


def find_call(name: str) -> tuple[int, str]:
    """``call:<N>`` (or ``call``: the only live call) -> (N, the socket to use)."""
    want = name.split(":", 1)[1].strip() if ":" in name else ""
    if want and not want.isdigit():
        raise DeviceAbsent(f"{name!r}: a call device is call:<N>, N the call's id")
    live, slow = _look()
    hits = [x for x in live if not want or x[0] == int(want)]
    if not hits:                # look again, longer, before dropping a line
        live, slow = _look(CALL_SLOW_S)
        hits = [x for x in live if not want or x[0] == int(want)]
    have = ", ".join(f"call:{c} ({who})" for c, who, _ in live) or "none"
    if not hits:
        home = os.path.expanduser("~")
        quiet = ", ".join("~" + p[len(home):] if p.startswith(home + os.sep) else p for p in slow)
        raise DeviceAbsent((f"call {want} is not live" if want else "no call is live")
                           + f"; live calls: {have}"
                           + (f"; no answer in {CALL_SLOW_S:.1f}s from {quiet}" if slow else ""))
    if len(hits) > 1:
        raise DeviceAbsent(f"{name!r} is ambiguous; live calls: {have}")
    return hits[0][0], hits[0][2]


class CallOut:
    """A live call as a device: finished audio into a Delta bot's mouth leg.

    Paced like :class:`NullOut` - real time, ``latency`` ahead - so the bot's
    queue only ever holds that much, and a cut or a retract drops a fraction
    of a second. ``abort`` tells the bot to cut; if the mouth dies instead,
    the bot sees EOF and cuts anyway. The call ending mid-line is
    :class:`DeviceLost`. Audio is resampled to 48 kHz (linear: speech into
    Opus) and sent as int16.

    ``duck`` is the barge duck (a :class:`LineDuck`): a call has no
    device callback, so :meth:`write` applies it to each block before the
    resample, on a copy (the caller's block is the synth's own, which
    ``audio.keep`` saves). What it can never reach is the ``latency`` already
    paced ahead plus up to one block, sitting in the bot's queue: that is the
    ``ahead_ms`` of a call's duck record.
    """

    latency = 0.2
    lost_after_s = 1.0
    # paced in software, no device callback: always 0. Its stalls are underruns.
    device_gaps = 0
    device_gap_s = 0.0
    device_gap_max_s = 0.0

    def __init__(self, name: str, sample_rate: int, pan: Optional[float] = None,
                 meta: Optional[dict] = None) -> None:
        self.call, path = find_call(name)
        self.name = f"call:{self.call}"
        self.sample_rate = sample_rate
        self.pan_ignored = "a call is mono" if pan is not None else None
        self.frames_played = 0
        self.underrun_frames = 0
        self.underruns = 0
        self.result: Optional[dict] = None     # the bot's word on how the line ended
        self._clock: Optional[float] = None
        self._last = np.zeros(0, dtype=np.float32)
        self._pos = 0.0
        self.duck = LineDuck(sample_rate)   # the barge duck, applied where the call is paced
        meta = meta or {}
        header = {"call": self.call, "rate": CALL_RATE, "text": meta.get("text") or "",
                  "who": meta.get("who") or ""}
        try:
            self._sock, reply = _ask(path, header, CALL_SLOW_S)
        except (OSError, ValueError) as e:
            raise DeviceAbsent(f"call {self.call}: the bot did not answer ({e})") from None
        if not reply.get("ok"):
            self._sock.close()
            raise DeviceAbsent(f"call {self.call}: {reply.get('error') or 'refused'}")
        self._sock.settimeout(self.lost_after_s)

    def _resample(self, block: np.ndarray) -> np.ndarray:
        """Linear, carrying the position across blocks so the seams are smooth."""
        if self.sample_rate == CALL_RATE:
            return block
        x = np.concatenate([self._last, block.astype(np.float32).reshape(-1)])
        if len(x) < 2:
            self._last = x
            return np.zeros(0, dtype=np.float32)
        step = self.sample_rate / CALL_RATE
        n = int(math.floor((len(x) - 1 - self._pos) / step)) + 1 if self._pos <= len(x) - 1 else 0
        pos = self._pos + step * np.arange(n)
        y = np.interp(pos, np.arange(len(x)), x).astype(np.float32)
        self._pos = (pos[-1] + step if n else self._pos) - (len(x) - 1)
        self._last = x[-1:]
        return y

    def _send(self, kind: bytes, payload: bytes = b"") -> None:
        try:
            self._sock.sendall(kind + struct.pack(">I", len(payload)) + payload)
        except socket.timeout:
            raise DeviceLost(f"{self.name} took no audio for {self.lost_after_s:.1f}s") from None
        except OSError as e:
            raise DeviceLost(f"{self.name} ended mid-line ({e})") from None

    def _read_result(self, timeout: float) -> None:
        try:
            self._sock.settimeout(timeout)
            buf = b""
            while not buf.endswith(b"\n"):
                got = self._sock.recv(4096)
                if not got:
                    break
                buf += got
            self.result = json.loads(buf) if buf.strip() else None
        except (OSError, ValueError):
            self.result = None

    def write(self, block: np.ndarray) -> None:
        now = time.monotonic()
        if self._clock is not None and now > self._clock + 0.005:  # synthesis fell behind
            self.underrun_frames += int((now - self._clock) * self.sample_rate)
            self.underruns += 1
        if self.duck.gain != 1.0 or self.duck.target != 1.0:   # an untouched line: today's bytes
            # ahead of the duck: what is paced out already (the bot's queue)
            self.duck.ahead_s = 0.0 if self._clock is None else max(0.0, self._clock - now)
            x = np.array(block, dtype=np.float32, copy=True).reshape(-1, 1)   # never the caller's
            self.duck.apply(x)
            block = x.reshape(-1)
        y = self._resample(block)
        pcm = (np.clip(y, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        if pcm:
            self._send(b"A", pcm)
        start = now if self._clock is None else max(now, self._clock)
        self._clock = start + len(block) / self.sample_rate
        time.sleep(max(0.0, self._clock - self.latency - time.monotonic()))
        self.frames_played += len(block)

    def drain(self) -> None:
        self._send(b"D")
        self._read_result(self.lost_after_s)
        if self.result is not None and not self.result.get("ok"):
            self._close()
            raise DeviceLost(f"{self.name}: {self.result.get('ended')} "
                             f"({self.result.get('error') or 'the bot did not keep the line'})")
        if self._clock is not None:        # the call plays what is queued in real time
            time.sleep(max(0.0, self._clock - time.monotonic()))
        self._close()

    def abort(self) -> None:
        try:
            self._send(b"C")
            self._read_result(0.5)
        except DeviceLost:
            pass
        self._close()

    # -- segments: a mixer keeps ONE line connection at a
    # time, and the connection's header is what the bot's echo guard and
    # transcript see, so it re-segments when a line starts or ends over music.

    def segment(self, text: str = "", who: str = "") -> None:
        """End this connection as done and go on in a new one headed ``text``.

        ``D``, then WAIT for the bot's ``ended: done``: the bot serves each
        connection in its own coroutine onto one deque, and the reply is what
        proves this connection's frames are all queued ahead of the next one's.
        Queued audio plays on, so the pacing clock and the resampler carry
        across. No reply, or not ``done``, is :class:`DeviceLost`."""
        path = self._sock.getpeername()
        self._send(b"D")
        self._read_result(self.lost_after_s)
        ended = self.result
        if ended is None or not ended.get("ok"):
            self._close()
            raise DeviceLost(f"{self.name}: the bot did not end the segment "
                             f"({(ended or {}).get('ended') or 'no reply'})")
        self._close()
        self._reconnect(path, text, who)

    def cut_segment(self, text: str = "", who: str = "") -> None:
        """``C`` on this connection (the bot drops what it holds of it, at most
        ``latency``), then a new one headed ``text``. The bot's queue is empty
        after a cut, so the pacing clock starts again."""
        path = self._sock.getpeername()
        try:
            self._send(b"C")
            self._read_result(0.5)
        except DeviceLost:
            pass
        self._close()
        self._clock = None
        self._reconnect(path, text, who)

    def _reconnect(self, path: str, text: str, who: str) -> None:
        header = {"call": self.call, "rate": CALL_RATE, "text": text or "", "who": who or ""}
        try:
            # a new segment's header waits as long as a first line's: a slow bot
            # is not an absent one, so not CALL_LOOK_S
            self._sock, reply = _ask(path, header, CALL_SLOW_S)
        except (OSError, ValueError) as e:
            raise DeviceLost(f"{self.name}: the bot did not take a new segment ({e})") from None
        if not reply.get("ok"):
            self._close()
            raise DeviceLost(f"{self.name}: {reply.get('error') or 'the bot refused a new segment'}")
        self._sock.settimeout(self.lost_after_s)

    def _close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


def _readline(sock: socket.socket) -> bytes:
    """One reply line from a bot, or what came before its EOF."""
    buf = b""
    while not buf.endswith(b"\n"):
        got = sock.recv(4096)
        if not got:
            break
        buf += got
    return buf


def call_sound(name: str, samples: np.ndarray, sample_rate: int, label: str = "sound") -> dict:
    """Play a short SOUND into a live call (``call:<N>``), mixed over whatever the call
    is playing: never queued behind a line, never in the transcript, never cut by a
    barge-in (sessionphone's mouth leg, ``"sound": true``): sounds follow the ears into
    the call. Unpaced: the whole sound goes at
    once, and the bot mixes it from its next frame. The bot's reply, or DeviceAbsent."""
    call, path = find_call(name)
    try:
        probe, info = _ask(path, {"probe": True})
        probe.close()
    except (OSError, ValueError) as e:
        raise DeviceAbsent(f"call {call}: the bot did not answer ({e})") from None
    if not info.get("sounds"):
        # a bot from before sounds would take the header for a LINE, and the line's EOF
        # is a cut: whatever the call was saying would stop. Ask first, never risk it.
        raise DeviceAbsent(f"call {call}: this bot predates sounds (restart it on sessionphone with them)")
    x = np.asarray(samples, dtype=np.float32).reshape(-1)
    if sample_rate != CALL_RATE and len(x) > 1:
        n = max(1, int(round(len(x) * CALL_RATE / sample_rate)))
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)
    pcm = (np.clip(x, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    try:
        sock, reply = _ask(path, {"call": call, "rate": CALL_RATE, "sound": True, "name": label})
    except (OSError, ValueError) as e:
        raise DeviceAbsent(f"call {call}: the bot did not answer ({e})") from None
    try:
        if not reply.get("ok") or not reply.get("sound"):
            if reply.get("ok"):          # it took a line after all: end it as done, never a cut,
                sock.sendall(b"D" + struct.pack(">I", 0))   # and hear it ended before we close
                try:
                    _readline(sock)
                except OSError:
                    pass
            raise DeviceAbsent(f"call {call}: no sounds here ({reply.get('error') or 'the bot predates them'})")
        sock.sendall(b"A" + struct.pack(">I", len(pcm)) + pcm + b"D" + struct.pack(">I", 0))
        buf = _readline(sock)
        return json.loads(buf) if buf.strip() else {"ok": False, "ended": "eof"}
    except OSError as e:
        raise DeviceLost(f"call {call}: {e}") from None
    finally:
        sock.close()


# -- the mixer: one output per device, a bed under a line ---
#
# While a played file (a bed, bed.py) plays with ducking on, the player owns ONE
# output on that device, and a line spoken to the device is mixed over the bed
# instead of opening an output of its own. That matters most on a call, where
# two line connections at once would interleave in the bot's one deque.

MIX_BLOCK_S = 0.05      # the pacing thread's block
LIMIT_KNEE = 0.9        # the soft limiter's knee: identity below it
DUCK_DOWN_S = 0.15      # the duck's ramp down, full depth
DUCK_UP_S = 0.5         # and back up


def soft_limit(x: np.ndarray, k: float = LIMIT_KNEE) -> np.ndarray:
    """Identity for ``|x| <= k``; above it ``sign(x) * (k + (1-k) * tanh((|x|-k)/(1-k)))``,
    continuous in slope at the knee and never past 1.0 (headroom)."""
    a = np.abs(x)
    over = a > k
    if not over.any():
        return x
    y = np.array(x, dtype=np.float32, copy=True)
    y[over] = np.sign(x[over]) * (k + (1.0 - k) * np.tanh((a[over] - k) / (1.0 - k)))
    return y


class MixLane:
    """One lane of a :class:`Mixer`, with the same surface as an output (``name``,
    ``write``, ``drain``, ``abort``, ``frames_played``, ``latency``,
    ``underrun_frames``, ``underruns``, ``pan_ignored``, ``device_gaps``,
    ``device_gap_s``, ``device_gap_max_s``), so ``play_one`` and a bed use it
    exactly as they use a device, and every ``saying``/``said`` field keeps its
    meaning. The device-gap fields are the shared device's gaps while this lane
    was live, charged by the mixer (:meth:`Mixer._charge_gaps`). On a
    shared device (a bed), per-line attribution is by mix time, +/- ~0.2 s
    (:attr:`Mixer.lead`) at each end of the line.

    ``write`` puts a block into a bounded FIFO (``queue_blocks``, like
    :class:`DeviceOut`'s queue) and is :class:`DeviceLost` if the mixer takes
    nothing for ``lost_after_s``. ``frames_played`` counts what the mixer took.
    A lane that runs dry mid-play counts underruns as a device does. ``abort``
    drops the FIFO."""

    queue_blocks = 4
    lost_after_s = 1.0

    def __init__(self, mixer: "Mixer", kind: str, sample_rate: int,
                 gains: Optional[np.ndarray], pan_ignored: Optional[str]) -> None:
        self.mixer, self.kind = mixer, kind
        self.name = mixer.name
        self.sample_rate = sample_rate
        self.latency = mixer.lead           # what the mixer took is this far ahead of the ear
        self.pan_ignored = pan_ignored
        self.gains = gains                  # (left, right) on a stereo mixer, else None
        self.q: queue.Queue = queue.Queue(maxsize=self.queue_blocks)
        self.frames_queued = 0
        self.frames_played = 0
        self.underrun_frames = 0
        self.underruns = 0
        self.draining = False
        self.closed = False
        self._dry = False
        self._cur: Optional[np.ndarray] = None
        self._pos = 0
        self._aborted = False
        self.over: Optional[str] = None     # a speech lane: the music under it (its said's ``over``)
        # the device's gaps while this lane was live, charged by the mixer
        self.device_gaps = 0
        self.device_gap_s = 0.0
        self.device_gap_max_s = 0.0
        self._gap_ep = 0.0                  # the lost time of the episode this lane is hearing
        # the mixer's thread pulls while another thread (play_one's _abort) may
        # drop the FIFO: the pull and the drop never interleave (review 1525)
        self._pull_lock = threading.Lock()

    @property
    def duck(self) -> Optional[LineDuck]:
        """The device's barge duck, applied after BOTH queues, the
        lane's and the device's. It scales the whole mix, so a bed under the
        line dips with it while the user talks (it is ducked under the user's voice already)."""
        return getattr(self.mixer.out, "duck", None)

    def write(self, block: np.ndarray) -> None:
        x = np.asarray(block, dtype=np.float32).reshape(-1)
        deadline = time.monotonic() + self.lost_after_s
        while True:
            if self.mixer.error is not None:
                raise DeviceLost(f"{self.name!r}: {self.mixer.error}")
            if self.closed:
                raise DeviceLost(f"{self.name!r}: the mixer closed this lane")
            try:
                self.q.put(x, timeout=0.05)
                break
            except queue.Full:
                if time.monotonic() >= deadline:
                    raise DeviceLost(f"{self.name!r} took no audio for {self.lost_after_s:.1f}s") from None
        self.frames_queued += len(x)

    def _pull(self, n: int) -> tuple[np.ndarray, int]:
        """The mixer takes ``n`` frames (zeros where the lane has none, and
        all zeros once the lane is aborted)."""
        with self._pull_lock:
            return self._pull_locked(n)

    def _pull_locked(self, n: int) -> tuple[np.ndarray, int]:
        out = np.zeros(n, dtype=np.float32)
        if self._aborted:
            return out, 0
        filled = 0
        while filled < n:
            if self._cur is None or self._pos >= len(self._cur):
                try:
                    self._cur, self._pos = self.q.get_nowait(), 0
                except queue.Empty:
                    break
            m = min(n - filled, len(self._cur) - self._pos)
            out[filled:filled + m] = self._cur[self._pos:self._pos + m]
            self._pos += m
            filled += m
        if filled < n:
            if self.frames_queued > 0 and not self.draining:
                self.underrun_frames += n - filled
                if not self._dry:
                    self.underruns += 1
                    self._dry = True
        else:
            self._dry = False
        self.frames_played += filled
        return out, filled

    def drain(self) -> None:
        self.draining = True
        left = (self.frames_queued - self.frames_played) / self.sample_rate
        deadline = time.monotonic() + left + self.lost_after_s
        while self.frames_played < self.frames_queued:
            if self.mixer.error is not None:
                raise DeviceLost(f"{self.name!r}: {self.mixer.error}")
            if time.monotonic() > deadline:
                raise DeviceLost(f"{self.name!r} stopped pulling with "
                                 f"{(self.frames_queued - self.frames_played) / self.sample_rate:.2f}s left")
            time.sleep(0.01)
        self.mixer._detach(self, "done")

    def abort(self) -> None:
        with self._pull_lock:
            self._aborted = True
            try:
                while True:
                    self.q.get_nowait()
            except queue.Empty:
                pass
            self._cur = None
        self.mixer._detach(self, "cut")


class Mixer:
    """ONE output on a device and a pacing thread that writes ``MIX_BLOCK_S``
    blocks to it::

        block = bed * (bed_volume * duck_ramp)  +  speech (or zeros)

    ``bed_volume`` is the bed item's own volume / 100; ``duck_ramp`` runs
    between 1.0 and the duck level, per sample (:meth:`set_duck`). A line's block
    arrives scaled by its own volume already (``play_one``), so the mixer only
    sums. A block that sums two lanes goes through :func:`soft_limit`; a lane
    alone is written as it came. On a device with two or more channels the
    mixer opens stereo and each lane is panned on its own; a call is mono.

    On ``call:<N>`` the mixer holds one line connection at a time and
    re-segments it (:meth:`CallOut.segment`): to the line's header when a line
    starts over the bed, back to the bed's when it ends, and ``C`` then a new
    bed segment when the line is cut.

    The mixer lives while it has a lane, and closes its output (drain, or abort
    after a cut) when the last one leaves."""

    def __init__(self, device: str, sample_rate: int, meta: Optional[dict] = None,
                 on_close=None) -> None:
        self.device = device
        self.rate = sample_rate
        self.meta = dict(meta or {})               # the bed's header, on a call
        # pan 0.0 asks a device for two channels when it has them; the lanes pan
        self.out = _plain_output(device, sample_rate, 0.0, self.meta)
        self.name = getattr(self.out, "name", device)
        self.stereo = getattr(self.out, "gains", None) is not None
        self.lead = float(getattr(self.out, "latency", 0.0) or 0.0) + \
            (getattr(self.out, "queue_blocks", 0) or 0) * MIX_BLOCK_S
        self.bed: Optional[MixLane] = None
        self.speech: Optional[MixLane] = None
        self.bed_volume = 1.0
        self.duck_ramp = 1.0                       # the duck's gain now, per sample
        self.duck_target = 1.0                     # where the ramp is going (set_duck)
        self._duck_depth = 0.0                     # 1 - the duck level: the ramp's full travel
        self.bed_utt: Optional[str] = None         # the music under a line: its said's ``over``
        self.trace: deque = deque(maxlen=4000)     # (monotonic t, the bed's gain) per block
        self.error: Optional[BaseException] = None
        self._lock = threading.Lock()
        self._segs: list = []                      # call re-segments, for the pacing thread
        self._closing = False
        self._end_how = "done"
        self._closed = threading.Event()
        self._on_close = on_close
        # the device's gap counters already charged to the lanes
        self._gap_seen = (getattr(self.out, "device_gaps", 0), getattr(self.out, "device_gap_s", 0.0))
        self.thread = threading.Thread(target=self._run, daemon=True, name=f"mixer-{self.name}")
        self.thread.start()

    # -- lanes ---------------------------------------------------------------

    def _lane_gains(self, pan: Optional[float]) -> tuple[Optional[np.ndarray], Optional[str]]:
        if self.stereo:
            g = (1.0, 1.0) if pan is None else pan_gains(pan)   # no pan: today's mono on both
            return np.array(g, dtype=np.float32), None
        return None, (getattr(self.out, "pan_ignored", None) if pan is not None else None)

    @property
    def closing(self) -> bool:
        return self._closing

    def takes_speech(self) -> bool:
        """A line may join: a bed is here, no line is, and the mixer is not closing."""
        return self.bed is not None and self.speech is None and not self._closing and self.error is None

    def bed_lane(self, pan: Optional[float] = None, volume: Optional[float] = None,
                 meta: Optional[dict] = None, utt: Optional[str] = None) -> Optional[MixLane]:
        """The bed's lane (None if the mixer is closing or holds a bed already)."""
        gains, ignored = self._lane_gains(pan)
        with self._lock:
            if self._closing or self.bed is not None or self.error is not None:
                return None
            self.bed = MixLane(self, "bed", self.rate, gains, ignored)
            self.bed_volume = 1.0 if volume is None else float(volume)
            self.bed_utt = utt
            self.duck_ramp = self.duck_target = 1.0   # a new bed starts at its own level
            if meta is not None:
                self.meta = dict(meta)
            return self.bed

    def speech_lane(self, sample_rate: int, pan: Optional[float] = None,
                    meta: Optional[dict] = None) -> Optional[MixLane]:
        """A line's lane over the bed, or None (then the line opens its own output).
        Every backend runs at the mixer's rate today; a line that does not is not mixed."""
        if sample_rate != self.rate:
            return None
        gains, ignored = self._lane_gains(pan)
        meta = meta or {}
        with self._lock:
            if not self.takes_speech():
                return None
            self.speech = MixLane(self, "speech", sample_rate, gains, ignored)
            self.speech.over = self.bed_utt
            self._segs.append(("to", meta.get("text") or "", meta.get("who") or ""))
            return self.speech

    def _detach(self, lane: MixLane, how: str) -> None:
        """A lane leaves (``done`` after its drain, or ``cut``). The last one closes the mixer."""
        with self._lock:
            if lane.closed:
                return
            lane.closed = True
            self._lane_gap_close(lane)   # an episode it was hearing ends with it
            if lane is self.speech:
                self.speech = None
                if self.bed is not None:   # back to the bed's own segment
                    self._segs.append(("to" if how == "done" else "cut",
                                       self.meta.get("text") or "", self.meta.get("who") or ""))
            elif lane is self.bed:
                self.bed = None
            last = self.bed is None and self.speech is None
            if last:
                self._closing = True
                self._end_how = how
        if last:
            self._closed.wait(self.lead + 3.0 if how == "done" else 1.0)
            if how == "done" and self.error is not None:
                raise DeviceLost(f"{self.name!r}: {self.error}")
        elif how == "done":
            time.sleep(self.lead)   # its last frames are in the device's own buffer

    # -- the duck ---------------------------------------------------

    def set_duck(self, target: float) -> None:
        """Where the bed's duck gain goes: the duck level (0-1), or 1.0 to come
        back. The pacing thread ramps to it per sample, down over ``DUCK_DOWN_S``
        and up over ``DUCK_UP_S`` (for the full depth), so it never clicks."""
        target = min(1.0, max(0.0, float(target)))
        if target < 1.0:
            self._duck_depth = 1.0 - target
        self.duck_target = target

    def _duck_gains(self, n: int) -> Optional[np.ndarray]:
        """The next ``n`` samples' duck gain (float64), or None when it is not
        moving (then ``duck_ramp`` is the gain, one number)."""
        cur, tgt = self.duck_ramp, self.duck_target
        if cur == tgt:
            return None
        depth = self._duck_depth or abs(tgt - cur)
        secs = DUCK_DOWN_S if tgt < cur else DUCK_UP_S
        step = depth / (secs * self.rate)
        k = np.arange(1, n + 1, dtype=np.float64) * step
        g = np.maximum(cur - k, tgt) if tgt < cur else np.minimum(cur + k, tgt)
        self.duck_ramp = float(g[-1])
        return g

    # -- the pacing thread -----------------------------------------------------

    def _segment(self, how: str, text: str, who: str) -> None:
        if how == "cut" and hasattr(self.out, "cut_segment"):
            self.out.cut_segment(text, who)
        elif how == "to" and hasattr(self.out, "segment"):
            self.out.segment(text, who)

    def _run(self) -> None:
        n = int(self.rate * MIX_BLOCK_S)
        try:
            while True:
                with self._lock:
                    segs, self._segs = self._segs, []
                    if self._closing:
                        break
                    bed, speech = self.bed, self.speech
                for s in segs:
                    self._segment(*s)
                lanes = [x for x in (bed, speech) if x is not None]
                if not any(x.frames_queued > 0 for x in lanes):
                    time.sleep(0.005)    # nothing written yet: no leading silence
                    continue
                parts = []
                for lane in lanes:
                    x, filled = lane._pull(n)
                    if lane is bed:
                        ramp = self._duck_gains(n)
                        if ramp is not None:     # the duck moving: per sample
                            x = (x * (self.bed_volume * ramp)).astype(np.float32, copy=False)
                        elif self.bed_volume * self.duck_ramp != 1.0:
                            x = (x * (self.bed_volume * self.duck_ramp)).astype(np.float32, copy=False)
                        self.trace.append((time.monotonic(), self.bed_volume * self.duck_ramp))
                    parts.append((lane, x, filled))
                if self.stereo:
                    block = sum(x.reshape(-1, 1) * lane.gains for lane, x, _ in parts)
                else:
                    block = sum(x for _, x, _ in parts)
                if sum(1 for _, _, f in parts if f) >= 2:
                    block = soft_limit(block)
                self.out.write(np.asarray(block, dtype=np.float32))
                with self._lock:
                    self._charge_gaps()
        except Exception as e:  # noqa: BLE001 - the lanes surface it as DeviceLost
            self.error = e
            self._end_how = "cut"
        finally:
            try:
                if self._end_how == "done" and self.error is None:
                    self.out.drain()
                else:
                    self.out.abort()
            except Exception as e:  # noqa: BLE001
                if self.error is None:
                    self.error = e
                try:
                    self.out.abort()
                except Exception:  # noqa: BLE001
                    pass
            with self._lock:
                self._closing = True
            if self._on_close is not None:
                try:
                    self._on_close(self)
                except Exception:  # noqa: BLE001
                    pass
            self._closed.set()

    # -- device gaps ------------------------------------------------

    def _charge_gaps(self) -> None:
        """The shared device's gaps since the last block go on every live lane:
        bed and line both heard them. Called under ``_lock`` after each
        ``out.write``. The device samples once per write, so a block brings at
        most one change: lost time added to an open episode, or the episode
        closed (the line's end may bring both). A lane that joins later is never
        charged what came before it."""
        n = getattr(self.out, "device_gaps", 0)
        s = getattr(self.out, "device_gap_s", 0.0)
        dn, ds = n - self._gap_seen[0], s - self._gap_seen[1]
        if not dn and ds <= 0.0:
            return
        self._gap_seen = (n, s)
        for lane in (self.bed, self.speech):
            if lane is None:
                continue
            if ds > 0.0:
                lane._gap_ep += ds
                lane.device_gap_s += ds
            if dn:
                self._lane_gap_close(lane)

    @staticmethod
    def _lane_gap_close(lane: MixLane) -> None:
        if lane._gap_ep:
            lane.device_gaps += 1
            lane.device_gap_max_s = max(lane.device_gap_max_s, lane._gap_ep)
            lane._gap_ep = 0.0

    def wait_closed(self, timeout: float) -> bool:
        return self._closed.wait(timeout)


def open_output(name: str, sample_rate: int, pan: Optional[float] = None,
                meta: Optional[dict] = None):
    """``meta`` (the line's text and who says it) matters only to a call: the bot
    tells its echo guard and its transcript.

    While a bed with ducking on plays on the device, the line is a
    :class:`MixLane` of that device's :class:`Mixer` instead: the same surface."""
    from . import bed as _bed   # bed.py imports this module

    mix = _bed.mixer_for(name)
    if mix is not None:
        lane = mix.speech_lane(sample_rate, pan, meta)
        if lane is not None:
            return lane
        if mix.closing:
            # its bed ended between mixer_for() and speech_lane(): the line opens
            # its own output only once the mixer's is closed
            mix.wait_closed(mix.lead + 3.0)
    return _plain_output(name, sample_rate, pan, meta)


def _plain_output(name: str, sample_rate: int, pan: Optional[float] = None,
                  meta: Optional[dict] = None):
    """Today's output on a device, never a mixer's lane."""
    if name == "null":
        return NullOut(sample_rate, pan)
    if name == "call" or name.startswith("call:"):
        return CallOut(name, sample_rate, pan, meta)
    return DeviceOut(name, sample_rate, pan)
