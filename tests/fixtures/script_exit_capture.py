"""The capture run exactly as ``python -m ears.listen capture`` runs it, with one STT
worker stuck in its call when the capture ends (EARS-359 do-004, EARS-344's ``exit-join``).

    python script_exit_capture.py WAV LOGDIR

``runpy`` runs ``ears.listen.__main__`` as ``__main__``, so its own
``if __name__ == "__main__":`` block, the path the keeper's capture takes, is what ends
this process. Only seams it imports are patched first: an energy classifier (a tone is
speech), the STT classes (a fake: the first chunk's call never returns, the others
answer; never a socket), the chunk wait's bound (0.2 s, so the capture gives up on the
stuck call and finishes), and the watchdog's exit grace (60 s, out of the test's reach,
so only the ``__main__`` path's own exit can end the process in time). The source is an
unpaced ``file:`` WAV that ends: reason ``eof``. No device, no network.
"""

from __future__ import annotations

import functools
import runpy
import sys
import threading

WAV, LOGDIR = sys.argv[1:3]

from ears.listen import detector as detector_mod  # noqa: E402
from ears.listen import loop  # noqa: E402
from ears.listen import stt as stt_mod  # noqa: E402
from ears.listen import watchdog  # noqa: E402
from ears.listen.detector import EnergyClassifier  # noqa: E402

NEVER = threading.Event()  # never set: the first chunk's call never returns


class StuckOnceSTT:
    name = "fake"

    def __init__(self) -> None:
        self.chunks = 0

    def transcribe(self, audio):
        self.chunks += 1
        if self.chunks == 1:
            print("script-exit: chunk 1 called: never returning", file=sys.stderr, flush=True)
            NEVER.wait()
        return "hello"

    def transcribe_final(self, audio):
        return "hello world"

    def close(self):
        pass


FAKE = StuckOnceSTT()
detector_mod.default_classifier = lambda: EnergyClassifier(500)
stt_mod.FollowingSTT = lambda *a, **k: FAKE
stt_mod.WhisperSTT = lambda *a, **k: FAKE
loop.capture = functools.partial(loop.capture, stt_wait_s=0.2)
watchdog.STOP_GRACE_S = 60.0

sys.argv = ["ears.listen", "capture", "--source", f"file:{WAV}", "--no-pace", "--tail-silence", "1",
            "--heartbeat", "1", "--log-dir", LOGDIR, "--silence", "0.6", "--speaker-split", "0",
            "--session", "ears-359-exit", "--agent", "test", "--turn-text", "joined"]
print("script-exit: running ears.listen as __main__", file=sys.stderr, flush=True)
runpy.run_module("ears.listen", run_name="__main__", alter_sys=True)
print("script-exit: the __main__ block returned", file=sys.stderr, flush=True)
