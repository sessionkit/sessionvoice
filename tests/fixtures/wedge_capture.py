"""The capture CLI, as its own process, with ONE fake that never returns (EARS-359 do-003).

    python wedge_capture.py SCENARIO LOGDIR STOPFILE WAV PIDFILE

EARS-344's repro (``findings/repro/wedge_child.py``) as a test fixture. It runs the REAL
``ears.listen.__main__.main`` with ``own_process=True``, so the capture has its real
watchdog, which really ``os._exit``s: this process, never the test's. Only these seams are
patched:

- ``detector.default_classifier`` -> EnergyClassifier(500), so a tone is speech;
- ``__main__.make_stt`` -> a fake STT (never a socket);
- ``__main__.open_source`` -> a fake mic, for ``close-hang`` only;
- ``__main__.Partials`` -> a fake streaming child that says ready and never reads its stdin,
  for ``pipe-full`` and ``pipe-full-shifted`` (which writes one extra frame first, so the
  64 KB pipe fills one frame earlier, off the loop's 10-frame stop-check grid), and for
  ``pipe-full-blocking``. The child writes its pid to PIDFILE. Since EARS-359 do-005 the
  capture feeds it in live mode (non-blocking, frame-whole: ``ProcessBackend.live``), and
  these two run paced at real time, saying ``first frame dropped`` when the backlog first
  overflows. ``pipe-full-blocking`` turns live mode off (the old blocking write), so the
  watchdog's guard against that wedge is still tested;
- ``__main__.capture`` -> the real one with the STT waits' bounds (EARS-359 do-002) set far
  out of reach, so that only the watchdog can end a hung STT wait (``exit-join`` keeps a
  short chunk bound: there the capture must finish, leaving a stuck thread behind);
- the watchdog's times, shortened: ``WEDGE_S`` 3 s, ``STOP_GRACE_S`` 1 s, polled at 50 ms.

``stop-drain-hang``'s fake touches STOPFILE itself as its call starts to hang: a STOP that
comes while the capture still beats. No audio device is opened: the source is an unpaced
``file:`` WAV, or the fake mic. Nothing here signals any other process.
"""

from __future__ import annotations

import functools
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np

SCENARIO, LOGDIR, STOPFILE, WAV, PIDFILE = sys.argv[1:6]

from ears.listen import __main__ as cli  # noqa: E402
from ears.listen import detector as detector_mod  # noqa: E402
from ears.listen import loop  # noqa: E402
from ears.listen import watchdog  # noqa: E402
from ears.listen.detector import EnergyClassifier  # noqa: E402
from ears.listen.sources import FRAME_S, FRAME_SAMPLES, SourceError  # noqa: E402

detector_mod.default_classifier = lambda: EnergyClassifier(500)
watchdog.WEDGE_S, watchdog.STOP_GRACE_S, watchdog.POLL_S = 3.0, 1.0, 0.05

NEVER = threading.Event()  # never set: a wait on it never returns
FAR = 1e6


def say(msg: str) -> None:
    print(f"wedge: {msg}", file=sys.stderr, flush=True)


class FakeSTT:
    """``hang`` in ('', 'chunk', 'chunk-once', 'final'): which call never returns."""

    name = "fake"

    def __init__(self, hang: str = "", touch_stop: bool = False) -> None:
        self.hang, self.touch_stop, self.chunks = hang, touch_stop, 0

    def _block(self, what: str) -> None:
        say(f"{what} called: never returning")
        if self.touch_stop:
            Path(STOPFILE).touch()
            say("STOP touched while the capture still beats")
        NEVER.wait()

    def transcribe(self, audio):
        self.chunks += 1
        if self.hang == "chunk" or (self.hang == "chunk-once" and self.chunks == 1):
            self._block("transcribe (chunk)")
        return "hello"

    def transcribe_final(self, audio):
        if self.hang == "final":
            self._block("transcribe_final (redecode)")
        return "hello world"

    def close(self):
        pass


class StallingMic:
    """A MicSource stand-in: real-time silence, then the stall MicSource raises when the
    device stops calling back (sources.py), then a ``close()`` that never returns, as a
    PortAudio stop on a device that left might."""

    name = "mic"
    device = "fake-mic"
    realtime = True
    input_overflows = 0

    def frames(self):
        start = time.monotonic()
        silence = np.zeros(FRAME_SAMPLES, dtype=np.int16)
        i = 0
        while True:
            delay = start + i * FRAME_S - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            if i * FRAME_S >= 1.0:
                say("fake mic: no audio (the stall MicSource raises)")
                raise SourceError("input device 'fake-mic' delivered no audio")
            yield silence
            i += 1

    def close(self):
        say("fake mic close(): never returning")
        NEVER.wait()


def fake_partials(shift: bool, blocking: bool = False):
    from ears.backends import proc
    from ears.backends.proc import ProcessBackend
    from ears.listen.streaming import Partials, Streamer

    child = ("import json,os,sys,time; "
             f"open({PIDFILE!r},'w').write(str(os.getpid())); "
             "print(json.dumps({'type':'ready','v':1,'backend':'fake','model':'fake','rate':16000,'load_s':0}), flush=True); "
             "time.sleep(1e9)")
    if blocking:
        proc.ProcessBackend.live = lambda self, **kw: self   # the old, blocking write
    real_live_feed = getattr(proc.ProcessBackend, "_feed_live", None)  # None before do-005

    def traced_feed_live(self, frame):
        before = self.dropped_frames
        real_live_feed(self, frame)
        if before == 0 and self.dropped_frames == 1:
            say(f"first frame dropped ({len(self._pending)} bytes waiting)")
    if real_live_feed is not None:
        proc.ProcessBackend._feed_live = traced_feed_live
    if shift:
        real_feed, n = proc.ProcessBackend.feed, {"w": 0}

        def feed(self, pcm):
            n["w"] += 1
            if n["w"] == 1:
                real_feed(self, pcm)  # one frame ahead
            real_feed(self, pcm)
        proc.ProcessBackend.feed = feed

    def make():
        say("fake streaming child started: says ready, never reads stdin")
        return Streamer(ProcessBackend([sys.executable, "-c", child], env=dict(os.environ)))

    return lambda mode: Partials("nemotron", make=make)


argv = ["capture", "--source", f"file:{WAV}", "--no-pace", "--tail-silence", "100000", "--heartbeat", "1",
        "--log-dir", LOGDIR, "--stop-file", STOPFILE, "--silence", "0.6", "--speaker-split", "0",
        "--session", "ears-359-wedge", "--agent", "test", "--turn-text", "redecode"]
stt_wait = FAR

if SCENARIO in ("control", "lock-held"):
    stt = FakeSTT()
elif SCENARIO == "redecode-hang":      # A: _redecoded_text
    stt = FakeSTT("final")
elif SCENARIO == "chunk-hang":         # B: _collect at the end of a turn
    stt = FakeSTT("chunk")
    argv[-1] = "joined"
elif SCENARIO == "stop-drain-hang":    # C: _collect after the loop broke on STOP
    stt = FakeSTT("chunk", touch_stop=True)
    argv[-1] = "joined"
elif SCENARIO == "close-hang":         # E: source.close()
    stt = FakeSTT()
    cli.open_source = lambda *a, **k: StallingMic()
elif SCENARIO in ("pipe-full", "pipe-full-shifted"):   # F, in live mode: it must keep beating
    stt = FakeSTT()
    cli.Partials = fake_partials(shift=SCENARIO == "pipe-full-shifted")
    argv.remove("--no-pace")   # real time, so the child "not reading" lasts as it would
elif SCENARIO == "pipe-full-blocking":  # F, the old blocking write: the watchdog's case
    stt = FakeSTT()
    cli.Partials = fake_partials(shift=False, blocking=True)
elif SCENARIO == "exit-join":          # G: an STT worker joined at interpreter exit
    stt = FakeSTT("chunk-once")
    argv[-1] = "joined"
    argv[argv.index("--tail-silence") + 1] = "1"   # the file ends: eof
    stt_wait = 0.2
else:
    raise SystemExit(f"unknown scenario {SCENARIO!r}")

cli.make_stt = lambda args: stt
cli.capture = functools.partial(loop.capture, stt_wait_s=stt_wait, redecode_wait_s=FAR)
say(f"scenario {SCENARIO}")
rc = cli.main(argv, own_process=True)
say(f"main returned {rc}; interpreter exiting")
sys.exit(rc)
