"""A FAKE sounddevice for keeper/ears-sounds.py (EARS-281): no PortAudio, no audio.

Put this directory first on PYTHONPATH. Every call is noted, one a line, in the file
FAKE_SD_LOG:

  open:<device>    an OutputStream opened (PortAudio's Pa_OpenStream)
  play:<device>    a sound started in an open stream (the callback took a new one)
  dead:<device>    the stream stopped calling back, with no error (a device gone quietly)
  close:<device>   the stream closed
  terminate / initialize:<default>   sounddevice's own rescan

The outputs: FAKE_SD_DEVICES (a file, one name a line; default "Fake Speakers"). The
system default: FAKE_SD_DEFAULT (a file), read ONCE at import and again only at
_initialize, the way PortAudio caches it. FAKE_SD_FAIL (a string of steps, consumed
one at a time, kept in FAKE_SD_LOG + ".fail"): each open, and each sound started in
an open stream, takes the next step; "F" fails it (an open raises; a sound kills the
stream), anything else lets it through.
"""
import os
import threading
import time

import numpy as np


class PortAudioError(Exception):
    pass


_LOG = os.environ["FAKE_SD_LOG"]


def _note(what):
    with open(_LOG, "a") as f:
        f.write(what + "\n")


def _fails() -> bool:
    p = _LOG + ".fail"
    try:
        steps = open(p).read()
    except OSError:
        steps = os.environ.get("FAKE_SD_FAIL", "")
    step, rest = steps[:1], steps[1:]
    with open(p, "w") as f:
        f.write(rest)
    return step == "F"


def _read(env, default):
    p = os.environ.get(env)
    try:
        return open(p).read() if p else default
    except OSError:
        return default


def _devices():
    return [n for n in _read("FAKE_SD_DEVICES", "Fake Speakers\n").split("\n") if n]


def _system_default():
    return _read("FAKE_SD_DEFAULT", "").strip()


_DEVICES = _devices()
_cached = _system_default() or _DEVICES[0]   # Pa_Initialize at import reads the default once


def _dev(name):
    return {"name": name, "max_output_channels": 2, "default_samplerate": 48000.0}


def query_devices(device=None, kind=None):
    if device is not None:
        name = device if isinstance(device, str) else _DEVICES[device]
        if name not in _DEVICES:
            raise ValueError(f"No output device matching {name!r}")
        return _dev(name)
    if kind:
        return _dev(_cached)    # PortAudio's CACHED default, never re-read
    return [_dev(n) for n in _DEVICES]


def play(data, samplerate, device=None, blocking=False):
    """The per-sound path ears-sounds used before EARS-281: open, play, close."""
    s = OutputStream(device=device, samplerate=samplerate, channels=1, dtype="float32")
    _note("play:" + s.name)
    s.close()


class OutputStream:
    def __init__(self, device=None, samplerate=None, channels=None, dtype=None, callback=None, **kw):
        self.name = query_devices(device)["name"] if device is not None else _cached
        if _fails():
            _note("open-fail:" + self.name)
            raise PortAudioError("Error opening OutputStream: Internal PortAudio error [PaErrorCode -9986]")
        _note("open:" + self.name)
        self.samplerate, self._cb, self.active, self.closed = samplerate, callback, False, False
        self._stop = threading.Event()

    def start(self):
        self.active = True
        if self._cb is not None:
            threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        held = getattr(self._cb, "__self__", None)   # the HeldStream: is a new sound about to start?
        while not self._stop.is_set():
            new = held is not None and held._cur is None and len(held._queue) > 0
            if new and _fails():
                self.active = False
                _note("dead:" + self.name)
                return                                # no callback ever again, no error
            if new:
                _note("play:" + self.name)
            buf = np.zeros((256, 1), np.float32)
            self._cb(buf, 256, None, None)
            time.sleep(0.002)

    def stop(self):
        self._stop.set()
        self.active = False

    def close(self):
        if not self.closed:
            self.closed = True
            self.stop()
            _note("close:" + self.name)


def _terminate():
    _note("terminate")


def _initialize():
    global _cached, _DEVICES
    _DEVICES = _devices()
    _cached = _system_default() or _cached
    _note("initialize:" + _cached)
