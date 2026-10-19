"""ears-sounds holds ONE output stream open (EARS-281).

The output stream is kept open between sounds.
keeper/ears-sounds.py is run as launchd runs it, following a temp heard
log, with a FAKE sounddevice first on PYTHONPATH (tests/fixtures/fake_sd): no
PortAudio, no audio, nothing live read. The fake notes every open, sound and close,
so the count of stream opens over a run is a number the tests assert.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import date, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from tests._anchor import ANCHOR_SUMMER

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "keeper" / "ears-sounds.py"
FAKE = Path(__file__).resolve().parent / "fixtures" / "fake_sd"
# every line's stamp: the summer anchor at the +11:00 it was written with (nothing compares it)
ROW_TS = ANCHOR_SUMMER.astimezone(timezone(timedelta(hours=11))).isoformat(timespec="milliseconds")

sys.path.insert(0, str(ROOT / "keeper"))
from held_stream import HeldStream  # noqa: E402  (keeper/ is not a package)

SOUNDS = {"turn": {"on": True, "freq": 1320, "ms": 5, "volume": 0.2},
          "partial": {"on": True, "freq": 2400, "ms": 3, "volume": 0.1},
          "event:turn-discarded": {"on": True, "freq": 440, "ms": 4, "volume": 0.1}}


def wait_for(pred, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


class Rig:
    """One ears-sounds process against temp files and the fake sounddevice."""

    def __init__(self, t: Path, *, settings=None, devices=("AirPods", "MacBook Pro Speakers"),
                 default="AirPods", fail="", env=None):
        self.t = t
        self.heard = t / "heard"
        self.heard.mkdir(exist_ok=True)
        self.log = self.heard / f"heard_{date.today():%Y-%m-%d}.jsonl"
        self.log.write_text("")
        self.settings = t / "settings.json"
        self.set(settings or {"to": [], "pair": False, "sounds": SOUNDS})
        (t / "devices").write_text("".join(d + "\n" for d in devices))
        (t / "default").write_text(default + "\n")
        self.env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(FAKE), str(ROOT / "src")]),
                        FAKE_SD_LOG=str(t / "sd.log"), FAKE_SD_DEVICES=str(t / "devices"),
                        FAKE_SD_DEFAULT=str(t / "default"), FAKE_SD_FAIL=fail,
                        EARS_SOUNDS_HEARD_DIR=str(self.heard), NOTIFY_GLOBAL=str(t / "no-global.json"),
                        EARS_SOUNDS_DEFAULT_CMD=f"cat {t / 'default'}", **(env or {}))
        self.args = ["--settings", str(self.settings), "--sound-log", str(t / "sounds.jsonl"),
                     "--stop-file", str(t / "STOP")]
        self.seq = 0
        self.p = None

    def set(self, settings):
        before = self.settings.stat().st_mtime if self.settings.exists() else None
        self.settings.write_text(json.dumps(settings))
        if before is not None:   # the reread keys on mtime: make sure it moved
            os.utime(self.settings, (before + 2, before + 2))

    def start(self):
        self.out = open(self.t / "stdout", "w")
        self.p = subprocess.Popen([sys.executable, str(SCRIPT), *self.args], env=self.env,
                                  stdout=self.out, stderr=subprocess.STDOUT, text=True)
        assert wait_for(lambda: "following" in self.stdout()), self.stdout()
        return self

    def stdout(self):
        p = self.t / "stdout"
        return p.read_text() if p.exists() else ""

    def feed(self, kind="turn", n=1, event=None):
        """n lines of `kind`, each waited on until its record is in the sound log."""
        for _ in range(n):
            self.seq += 1
            e = {"ts": ROW_TS, "seq": self.seq, "kind": kind}
            if event:
                e["event"] = event
            with open(self.log, "a") as f:
                f.write(json.dumps(e) + "\n")
            want = len(self.records()) + 1
            assert wait_for(lambda want=want: len(self.records()) >= want), (self.sd(), self.stdout())

    def records(self):
        p = self.t / "sounds.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def sd(self):
        p = self.t / "sd.log"
        return p.read_text().splitlines() if p.exists() else []

    def stop(self):
        (self.t / "STOP").write_text("")
        try:
            self.p.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.p.kill()
            self.p.wait()
        self.out.close()
        return self.p.returncode


@pytest.fixture
def rig(tmp_path):
    made = []

    def go(**kw):
        d = tmp_path / f"r{len(made)}"
        d.mkdir()
        made.append(Rig(d, **kw))
        return made[-1].start()
    yield go
    for r in made:
        if r.p and r.p.poll() is None:
            r.p.kill()
            r.p.wait()


def opens(sd_log):
    return [x for x in sd_log if x.startswith("open:")]


def test_a_run_with_partial_on_opens_one_stream(rig):
    """The ask itself: turn, partial and turn-discarded tones all go into ONE stream."""
    r = rig()
    r.feed("partial", 20)
    r.feed("turn", 2)
    r.feed("event", 1, event="turn-discarded")
    r.feed("partial", 5)
    sd_log = r.sd()
    assert opens(sd_log) == ["open:AirPods"], sd_log
    assert sd_log.count("play:AirPods") == 28, sd_log
    assert not any(x.startswith("close:") for x in sd_log), sd_log      # held, not closed per blip
    recs = r.records()
    assert [x["kind"] for x in recs] == ["partial"] * 20 + ["turn"] * 2 + ["event:turn-discarded"] + ["partial"] * 5
    assert all(x["opens"] == 1 and x["error"] is None and x["device"] == "AirPods" for x in recs), recs
    assert r.stop() == 0
    assert r.sd()[-1] == "close:AirPods"                                 # closed once, at the stop
    assert "streams opened: 1" in r.stdout()


def test_the_default_moving_reopens_the_stream_on_the_new_default(rig):
    """Device follow, EARS-74's case: AirPods -> MacBook Pro Speakers under a live process."""
    r = rig()
    r.feed("partial", 3)
    (r.t / "default").write_text("MacBook Pro Speakers\n")
    r.feed("partial", 3)
    sd_log = r.sd()
    assert opens(sd_log) == ["open:AirPods", "open:MacBook Pro Speakers"], sd_log
    i = sd_log.index("close:AirPods")
    assert sd_log[i:i + 3] == ["close:AirPods", "terminate", "initialize:MacBook Pro Speakers"], sd_log
    assert [x["device"] for x in r.records()] == ["AirPods"] * 3 + ["MacBook Pro Speakers"] * 3
    assert [x["opens"] for x in r.records()] == [1, 1, 1, 2, 2, 2]
    assert "'AirPods' -> 'MacBook Pro Speakers'" in r.stdout()
    assert r.stop() == 0


def test_a_new_to_output_reopens_the_stream_there(rig):
    """`to` changed live in the settings: the stream follows it; the default never moved."""
    s = {"to": ["MacBook Pro Speakers"], "pair": False, "sounds": SOUNDS}
    r = rig(settings=s)
    r.feed("turn", 2)
    r.set(dict(s, to=["AirPods"]))
    r.feed("turn", 2)
    sd_log = r.sd()
    assert opens(sd_log) == ["open:MacBook Pro Speakers", "open:AirPods"], sd_log
    assert "close:MacBook Pro Speakers" in sd_log and "terminate" not in sd_log, sd_log
    assert [(x["device"], x["route"], x["opens"]) for x in r.records()] == [
        ("MacBook Pro Speakers", "to", 1)] * 2 + [("AirPods", "to", 2)] * 2
    assert r.stop() == 0


def test_a_stream_whose_device_went_quiet_is_rescanned_and_reopened(rig):
    """No error, the callbacks just stop (a device gone): the blip times out, the
    devices are rescanned and the stream reopened once - the sound still plays."""
    r = rig(fail="..F")          # open ok, sound 1 ok, sound 2 finds the stream dead
    r.feed("turn", 3)
    sd_log = r.sd()
    assert sd_log[:3] == ["open:AirPods", "play:AirPods", "dead:AirPods"], sd_log
    assert opens(sd_log) == ["open:AirPods", "open:AirPods"], sd_log
    assert "terminate" in sd_log, sd_log
    recs = r.records()
    assert [x["error"] for x in recs] == [None, None, None], recs
    assert [x["opens"] for x in recs] == [1, 2, 2], recs
    assert r.stop() == 0


def test_an_open_that_fails_is_rescanned_and_tried_once_more(rig):
    """EARS-74's -9986 recovery, kept: one rescan, one retry, then it plays."""
    r = rig(fail="F")
    r.feed("turn", 2)
    sd_log = r.sd()
    assert sd_log[:4] == ["open-fail:AirPods", "terminate", "initialize:AirPods", "open:AirPods"], sd_log
    assert [x["error"] for x in r.records()] == [None, None]
    assert [x["opens"] for x in r.records()] == [1, 1]   # a failed open opened nothing
    assert r.stop() == 0


def test_every_open_failing_still_exits_for_launchd(rig):
    """MAX_FAILS is unchanged: open, rescan, open fails each time -> exit 1."""
    r = rig(fail="F" * 40, env={"EARS_SOUNDS_MAX_FAILS": "3"})
    for _ in range(3):
        r.feed("turn")
    assert wait_for(lambda: r.p.poll() is not None)
    assert r.p.returncode == 1
    assert len(r.records()) == 3 and all("-9986" in x["error"] for x in r.records())
    r.out.close()


def test_sounds_switched_off_closes_the_stream_and_on_reopens_it(rig):
    """While no sound is on nothing is held open; the next sound opens it again."""
    r = rig()
    r.feed("partial", 2)
    off = {"to": [], "pair": False, "sounds": {k: dict(v, on=False) for k, v in SOUNDS.items()}}
    r.set(off)
    assert wait_for(lambda: "close:AirPods" in r.sd()), (r.sd(), r.stdout())
    assert "output stream closed" in r.stdout()
    r.set({"to": [], "pair": False, "sounds": SOUNDS})
    r.feed("partial", 2)
    assert opens(r.sd()) == ["open:AirPods", "open:AirPods"], r.sd()
    assert [x["opens"] for x in r.records()] == [1, 1, 2, 2]
    assert r.stop() == 0


def test_test_mode_opens_plays_and_closes_once(tmp_path):
    r = Rig(tmp_path)
    out = subprocess.run([sys.executable, str(SCRIPT), *r.args, "--test", "turn"], env=r.env,
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert "opens 1" in out.stdout and "device 'AirPods'" in out.stdout
    assert r.sd() == ["open:AirPods", "play:AirPods", "close:AirPods"]


def test_a_call_opens_no_local_stream(tmp_path):
    """Call routing is unchanged: a call mic with pair goes down the call, no stream here."""
    sys.path.insert(0, str(Path(__file__).parent / "mouth"))
    from test_mouth_call_sound import SoundBot
    bot = SoundBot([5531])
    try:
        r = Rig(tmp_path, settings={"to": [], "pair": True, "sounds": SOUNDS},
                env={"MOUTH_CALL_SOCKS": bot.path})
        out = subprocess.run([sys.executable, str(SCRIPT), *r.args, "--test", "turn", "--mic", "call:5531"],
                             env=r.env, capture_output=True, text=True, timeout=30)
        assert out.returncode == 0, out.stderr
        assert "-> call:5531 (pair:call)" in out.stdout and "opens 0" in out.stdout
        assert len(bot.mixed) == 1 and r.sd() == []
    finally:
        bot.close()


# HeldStream itself, in-process, against a stub stream driven by hand.

class StubStream:
    made: list = []

    def __init__(self, device=None, samplerate=None, channels=None, dtype=None, blocksize=0, callback=None):
        self.device, self.samplerate, self.blocksize, self.cb = device, samplerate, blocksize, callback
        self.active = self.stopped = self.closed = False
        StubStream.made.append(self)

    def start(self):
        self.active = True

    def stop(self):
        self.stopped, self.active = True, False

    def close(self):
        self.closed, self.active = True, False


class StubSD:
    OutputStream = StubStream

    class PortAudioError(Exception):
        pass


def pump(stream, frames=4):
    buf = np.full((frames, 1), 9.0, np.float32)
    stream.cb(buf, frames, None, None)
    return buf[:, 0].tolist()


def test_held_stream_callback_plays_the_queue_then_silence():
    StubStream.made = []
    h = HeldStream(StubSD, slack_s=5)
    data = np.array([1, 2, 3, 4, 5, 6], np.float32)
    t = threading.Thread(target=h.write, args=(None, "Dev", 48000, data))
    t.start()
    assert wait_for(lambda: StubStream.made and len(h._queue) == 1, 5)
    s = StubStream.made[0]
    assert (s.device, s.samplerate, s.blocksize, s.active) == (None, 48000, 960, True)   # a 20 ms block
    assert pump(s) == [1, 2, 3, 4]          # spans two callbacks
    assert t.is_alive()                     # not all taken yet: still waiting
    assert pump(s) == [5, 6, 0, 0]          # the rest, then silence (not the 9s)
    t.join(5)
    assert not t.is_alive() and h.opens == 1
    assert pump(s) == [0, 0, 0, 0]          # held open, silent between blips


def test_held_stream_reopens_only_when_the_key_changes():
    StubStream.made = []
    h = HeldStream(StubSD)
    h._idle.wait = lambda timeout: True     # nothing pumps here: count the opens only
    d = np.zeros(4, np.float32)
    h.write("A", "A", 48000, d)
    h.write("A", "A", 48000, d)
    h.write(None, "A", 48000, d)            # the default, even with the same name: reopen
    h.write(None, "A", 44100, d)            # a new rate: reopen
    assert StubStream.made[-1].blocksize == 882   # the block is a time: 20 ms at the new rate
    h.write(None, "A", 44100, d)
    assert h.opens == 3 and len(StubStream.made) == 3
    assert all(s.stopped and s.closed for s in StubStream.made[:2]) and not StubStream.made[2].closed
    StubStream.made[2].active = False       # a stream that stopped on its own is not reused
    h.write(None, "A", 44100, d)
    assert h.opens == 4 and StubStream.made[2].closed


def test_held_stream_that_takes_nothing_raises_portaudio_error_and_closes():
    StubStream.made = []
    h = HeldStream(StubSD, slack_s=0.05)
    with pytest.raises(StubSD.PortAudioError, match="took nothing"):
        h.write(None, "Gone", 48000, np.zeros(48, np.float32))
    assert h.stream is None and StubStream.made[0].closed and not h._queue


def test_held_stream_open_that_fails_to_start_is_closed():
    class NoStart(StubStream):
        def start(self):
            raise StubSD.PortAudioError("start failed")

    class SD(StubSD):
        OutputStream = NoStart
    StubStream.made = []
    h = HeldStream(SD)
    with pytest.raises(StubSD.PortAudioError):
        h.write(None, "X", 48000, [0.0])
    assert h.stream is None and StubStream.made[0].closed and h.opens == 1
