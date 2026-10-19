"""The capture CLI with a fake STT whose re-decode hangs: a wedge to signal (EARS-359).

    python hung_stt_capture.py RELEASE CAPTURE-ARGS...

It runs the REAL ``ears.listen.__main__.main`` (its signal handlers, the frame loop,
the heard sink, the STT executor). Only two seams are patched: an energy classifier,
so a tone is speech, and ``make_stt``, a fake that never opens a socket. Its
``transcribe_final`` (the turn's re-decode) says ``hung-stt: redecode blocking`` on
stderr and blocks until the file RELEASE exists, so the main thread waits in
``_redecoded_text`` until the test lets it go. Nothing here opens a device: the
source is a ``file:`` WAV.
"""

from __future__ import annotations

import os
import sys
import time

RELEASE = sys.argv[1]

from ears.listen import __main__ as cli  # noqa: E402
from ears.listen import detector as detector_mod  # noqa: E402
from ears.listen.detector import EnergyClassifier  # noqa: E402

detector_mod.default_classifier = lambda: EnergyClassifier(500)


class HungRedecodeSTT:
    name = "fake"

    def transcribe(self, audio):
        return "hello"

    def transcribe_final(self, audio):
        print("hung-stt: redecode blocking", file=sys.stderr, flush=True)
        while not os.path.exists(RELEASE):
            time.sleep(0.02)
        return "hello world"

    def close(self):
        pass


cli.make_stt = lambda args: HungRedecodeSTT()
sys.exit(cli.main(sys.argv[2:]))
