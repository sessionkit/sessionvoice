"""ears-sounds: with `pair`, a call's mic is paired with that call.

Sounds for his voice, while he is on
a call, play back through the call. The script is run as he would
run it (`--test KIND --mic call:N`) against a fake sounds bot on a real Unix
socket; a call that can't take the sound plays at the default instead.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# keeper/ears-sounds.py imports sounddevice (PortAudio). It runs here with the FAKE
# one first on its PYTHONPATH (tests/fixtures/fake_sd, as test_ears_sounds_stream.py
# does): a fallback "plays" on the fake's default, never the real one (EARS-320 A2-A5:
# with the capture extra installed, three of these opened the system default output).
# The fake notes every open, so the default fallback is now asserted, not excused.
pytest.importorskip("numpy")

sys.path.insert(0, str(Path(__file__).parent / "mouth"))
from test_mouth_call_sound import SoundBot  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[1] / "keeper" / "ears-sounds.py"
FAKE = Path(__file__).resolve().parent / "fixtures" / "fake_sd"


@pytest.fixture
def run(tmp_path):
    made = []

    def go(*, pair, mic, bots=(), sounds=True):
        for calls in bots:
            made.append(SoundBot(calls, sounds=sounds))
        st = tmp_path / "ears-sounds.json"
        st.write_text(json.dumps({"allow": True, "to": [], "pair": pair,
                                  "sounds": {"turn": {"on": True, "freq": 1320, "ms": 45, "volume": 0.0}}}))   # silent, and a fallback opens the FAKE default
        env = dict(os.environ,
                   MOUTH_CALL_SOCKS=":".join(b.path for b in made) or "/tmp/nope-nothing-here.sock",
                   EARS_SOUNDS_DEFAULT_CMD="echo Fake Speakers",
                   FAKE_SD_LOG=str(tmp_path / "sd.log"), FAKE_SD_FAIL="",
                   PYTHONPATH=os.pathsep.join([str(FAKE), str(SCRIPT.parents[1] / "src"),
                                               os.environ.get("PYTHONPATH", "")]))
        r = subprocess.run([sys.executable, str(SCRIPT), "--settings", str(st), "--test", "turn", "--mic", mic],
                           capture_output=True, text=True, env=env, timeout=60)
        return r, made
    yield go
    for b in made:
        b.close()


def opened(tmp_path) -> list[str]:
    """Every stream the fake sounddevice opened, by device name."""
    log = tmp_path / "sd.log"
    return [ln[5:] for ln in log.read_text().splitlines() if ln.startswith("open:")] if log.exists() else []


def test_a_call_mic_with_pair_mixes_the_tick_into_that_call(run, tmp_path):
    r, (bot,) = run(pair=True, mic="call:5531", bots=[[5531]])
    assert r.returncode == 0, r.stderr
    assert "-> call:5531 (pair:call)" in r.stdout
    (snd,) = bot.mixed
    assert snd["header"] == {"call": 5531, "rate": 48000, "sound": True, "name": "turn"}
    assert snd["samples"] == int(48000 * 45 / 1000) and snd["ended"] == "done"
    assert bot.lines == []
    assert opened(tmp_path) == []                          # into the call: no local stream at all


def test_without_pair_a_call_mic_stays_at_the_default(run, tmp_path):
    r, (bot,) = run(pair=False, mic="call:5531", bots=[[5531]])
    assert bot.mixed == [] and bot.lines == []
    assert r.returncode == 0, r.stderr
    assert "(default)" in r.stdout
    assert opened(tmp_path) == ["Fake Speakers"]           # the fake's default, never the host's


def test_a_bot_from_before_sounds_falls_back_to_the_default(run, tmp_path):
    r, (bot,) = run(pair=True, mic="call:5531", bots=[[5531]], sounds=False)
    assert bot.mixed == [] and bot.lines == []             # nothing sent: no line, no cut
    assert r.returncode == 0, r.stderr
    assert "pair:call->default (DeviceAbsent" in r.stdout
    assert opened(tmp_path) == ["Fake Speakers"]


def test_a_call_that_ended_falls_back_to_the_default(run, tmp_path):
    r, _ = run(pair=True, mic="call:9")                      # no bot at all
    assert r.returncode == 0, r.stderr
    assert "pair:call->default (DeviceAbsent" in r.stdout
    assert opened(tmp_path) == ["Fake Speakers"]
