"""held_stream: ONE output stream, held open, that every local blip is written into.

The blips can be turned off, but they are useful for debugging, so the
stream is kept open rather than opened per blip.
Before, ears-sounds called sd.play() per blip: one PortAudio stream opened
and closed for every turn tick and every partial blip, up to 75 a minute, and on
AirPods each one an open and a close of the Bluetooth output.

A callback stream, fed from a queue, plays silence between blips. A blip is queued
and then waited on until the callback has taken all of it, so a sound still counts
as played only once PortAudio has it, and a device that went away without an error
(its callbacks simply stop) is caught: the wait times out and raises
PortAudioError, which ears-sounds already answers with a rescan and one retry. A
blocking write() would hang there instead, once the ring buffer filled.

The stream is keyed on (the output asked for, the device PortAudio resolved, the
rate): when any of them changes it is closed and opened again on the new one.
`opens` counts every open, so a run's records show the number.

Held open, the callback runs all the time, silent or not. With PortAudio's own
block (blocksize 0) that was ~94 callbacks a second on the DELL, 0.93% of a core;
a 20 ms block (BLOCK_S) is ~47 a second, 0.36%, and the output latency goes from
24.5 to 35 ms (measured). Set as a time, not frames, so a
96 kHz output gets the same rate.
"""
from __future__ import annotations

import collections
import threading

BLOCK_S = 0.02   # the callback's block, as a time: fewer wakeups while nothing plays


class HeldStream:
    """ONE sd.OutputStream, opened on the first blip, reopened only when the device changes."""

    def __init__(self, sd, *, slack_s: float = 1.0, block_s: float = BLOCK_S):
        self.sd = sd
        self.block_s = block_s
        self.slack_s = slack_s      # beyond the blip's own length, before the device counts as lost
        self.stream = None
        self.key = None
        self.opens = 0
        self._lock = threading.Lock()
        self._queue: collections.deque = collections.deque()
        self._cur = None
        self._pos = 0
        self._idle = threading.Event()
        self._idle.set()

    # the PortAudio thread
    def _callback(self, outdata, frames, time_info, status):
        outdata.fill(0)
        n = 0
        with self._lock:
            while n < frames:
                if self._cur is None:
                    if not self._queue:
                        break
                    self._cur, self._pos = self._queue.popleft(), 0
                take = min(frames - n, len(self._cur) - self._pos)
                outdata[n:n + take, 0] = self._cur[self._pos:self._pos + take]
                n += take
                self._pos += take
                if self._pos >= len(self._cur):
                    self._cur = None
            if self._cur is None and not self._queue:
                self._idle.set()

    def _open(self, device, samplerate: int, key) -> None:
        self.close()
        s = self.sd.OutputStream(device=device, samplerate=samplerate, channels=1, dtype="float32",
                                 blocksize=max(1, round(samplerate * self.block_s)), callback=self._callback)
        self.opens += 1          # counted once PortAudio opened it, whether or not it starts
        try:
            s.start()
        except Exception:
            s.close()
            raise
        self.stream, self.key = s, key

    def write(self, device, name: str, samplerate: int, data) -> None:
        """Play `data` (mono float32) on `device` (None: the default), whose resolved name
        is `name`; return once the stream has taken all of it."""
        key = (device, name, samplerate)
        if self.stream is None or self.key != key or not self.stream.active:
            self._open(device, samplerate, key)
        with self._lock:
            self._idle.clear()
            self._queue.append(data)
        if not self._idle.wait(len(data) / samplerate + self.slack_s):
            self.close()
            raise self.sd.PortAudioError(f"held stream on {name!r} took nothing in "
                                         f"{len(data) / samplerate + self.slack_s:.2f}s - device lost?")

    def drain(self, timeout: float = 2.0) -> None:
        """Wait for what was queued to be taken (before a close that should not cut a blip)."""
        self._idle.wait(timeout)

    def close(self) -> None:
        """Close the stream (quietly: a device that went away can't close cleanly)."""
        s, self.stream, self.key = self.stream, None, None
        with self._lock:
            self._queue.clear()
            self._cur = None
            self._idle.set()
        if s is not None:
            try:
                s.stop()     # Pa_StopStream: let the buffers out, then stop
            except Exception:
                pass
            try:
                s.close()
            except Exception:
                pass
