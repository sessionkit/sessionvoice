"""bench/clock_check.py reads the call leg's recording by its name: caller-call.wav/.json since
PHONE-127 (EARS-298), and no other name (EARS-490). The script is run whole,
against a stand-in bot: EARS_BOT_DIR holds a calls.py that records the track it is given under
the names a case asks for, and an av.py with the one AudioFrame call the script makes. No Delta
bot, no PyAV, no network; about 1 s of audio, paced in real time, per case."""
import json, os, subprocess, sys
from pathlib import Path

import pytest

from tests._anchor import ANCHOR

OTHER = "other-call"     # a stand-in for any recording not named caller-call

ROOT = Path(__file__).resolve().parents[1]
CLOCK = ROOT / "bench" / "clock_check.py"

CALLS = '''
import json, os, pathlib, time, wave
import numpy as np
def setup(*a, **k): pass
class CallSession:
    def __init__(self, manager, account, chat_id, offer_sdp): pass
    async def _listen_ears(self, track):
        """Every stem in STUB_STEMS gets a .wav and a .json; only STUB_REAL's wav holds the
        track (a burst 1.0 s in), the others hold silence, so the onset says which was read."""
        self.dir = pathlib.Path(os.environ["PIP_CHAT_CALLS_DIR"]) / "''' + f"{ANCHOR:%Y%m%d-%H%M%S}" + '''-chat0-msg1"
        self.dir.mkdir()
        t0, got = time.time(), []
        while sum(map(len, got)) < 48000 + 4800:
            got.append((await track.recv()).to_ndarray().reshape(-1))
        a = np.concatenate(got)
        for stem in filter(None, os.environ["STUB_STEMS"].split(",")):
            with wave.open(str(self.dir / (stem + ".wav")), "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000)
                w.writeframes((a if stem == os.environ["STUB_REAL"] else np.zeros_like(a)).tobytes())
            (self.dir / (stem + ".json")).write_text(json.dumps(dict(
                stem=stem, rate=48000, start_epoch=t0, end_epoch=time.time(), preroll_s=0.0,
                audio_s=len(a) / 48000)))
'''

AV = '''
class AudioFrame:
    @staticmethod
    def from_ndarray(a, format=None, layout=None):
        f = AudioFrame(); f.a = a; return f
    def to_ndarray(self): return self.a
'''


def run(tmp_path, stems, real=""):
    bot = tmp_path / "bot"; bot.mkdir()
    (bot / "calls.py").write_text(CALLS); (bot / "av.py").write_text(AV)
    env = dict(os.environ, EARS_BOT_DIR=str(bot), STUB_STEMS=",".join(stems), STUB_REAL=real,
               TMPDIR=str(tmp_path))
    return subprocess.run([sys.executable, str(CLOCK), "0"], capture_output=True, text=True,
                          env=env, timeout=60)


@pytest.mark.parametrize("stems,want", [
    (["caller-call"], "caller-call"),                  # a call since PHONE-127
    ([OTHER, "caller-call"], "caller-call"),           # another recording beside it: never read
])
def test_clock_check_reads_caller_call_only(tmp_path, stems, want):
    p = run(tmp_path, stems, real=want)
    assert p.returncode == 0, p.stderr
    meta = json.loads(p.stdout.splitlines()[0])
    assert meta["stem"] == want                        # the json it read
    assert "onset 1.000s in wav" in p.stdout           # the wav it read: the burst, not silence


@pytest.mark.parametrize("stems", [[], [OTHER]])     # none, or only another name: not read
def test_clock_check_with_no_caller_call_names_the_file(tmp_path, stems):
    p = run(tmp_path, stems, real=OTHER)
    assert p.returncode != 0
    assert "no caller-call.json in " in p.stderr and OTHER not in p.stderr
