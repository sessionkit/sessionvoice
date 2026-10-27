"""EARS-489: ``mouth play`` with no ``--device`` must end on the output ``mouth say`` uses,
and a PortAudio open error is never filed as ``device-absent``.

The field record (a laptop, a play of a song with no ``--device``): the play was
filed ``device="default" device_from="settings" reason="device-absent"
played_s=0.0`` with the detail ``PortAudioError: Error opening OutputStream: Invalid
number of channels [-9998]``. A ``mouth say`` on the same seat played on the
AirPods, ``device_from="settings"``. The AirPods were the current output, and the
ears held their microphone. A play with ``--device <the AirPods>`` of a 48 kHz
2-channel file played at once, so the file's channels are not the cause.

The rig here is that host, faked: the host settings file names the same device
for both lines, and the system default output is a 1-channel device (AirPods in
hands-free mode while their microphone is in use). A spoken line and a played
file are queued the same way and played by one player.

These tests FAILED before the fix (EARS-489 repro-001; the fix is fix-001):

- ``test_say_and_play_end_on_the_same_output``: the say line plays on the
  default's own name; the play is filed ``device-absent`` on ``default`` with the
  PortAudio -9998 detail, exactly the field record.
- ``test_a_portaudio_open_error_is_not_device_absent``: an open the device
  refuses (it is present) is filed ``device-absent``.

``test_say_and_play_queue_the_same_device`` is a control and passes today: the
caller's device order (``mouth.resolve_device``) gives the two lines the same
device and rung.

Fakes only: a tmp mouth dir, maildir, logs and settings file, the ``silence``
backend, a generated WAV, and a fake PortAudio (``output._sd``). No real device,
HOME or mail.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import mouth
from ears import heard
from mouth import hold, output, player

from tests import env_fence

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg")

#: The PortAudio message the field record carries, word for word.
INVALID_CHANNELS = "Error opening OutputStream: Invalid number of channels [-9998]"


class PortAudioError(Exception):
    """Stands in for ``sounddevice.PortAudioError``: the record names the type."""


class FakeSd:
    """A fake PortAudio: a microphone, and the AirPods as the default output with ONE
    output channel (hands-free, while the ears hold their microphone). Opening a
    stream with more channels than the device has raises -9998, as PortAudio does.
    ``refuse`` makes every open raise it, whatever the channels."""

    def __init__(self, out_channels: int = 1, refuse: bool = False):
        self.default = SimpleNamespace(device=(0, 1))
        self.devs = [{"name": "MacBook Pro Microphone", "max_output_channels": 0},
                     {"name": "AirPods", "max_output_channels": out_channels}]
        self.refuse = refuse
        self.opened: list = []   # (device as asked, channels)

    def _terminate(self):
        pass

    def _initialize(self):
        pass

    def query_devices(self, i=None):
        return self.devs if i is None else self.devs[i]

    def OutputStream(self, *, device, samplerate, channels, dtype, callback):
        have = self.devs[self.default.device[1] if device is None else device]["max_output_channels"]
        if self.refuse or channels > have:
            raise PortAudioError(INVALID_CHANNELS)
        self.opened.append((device, channels))

        class S:
            latency = 0.01

            def __init__(s):
                s.run = True

            def start(s):
                def loop():
                    buf = np.zeros((1200, channels), dtype="float32")
                    while s.run:
                        callback(buf, 1200, None, None)
                        time.sleep(1200 / samplerate)
                threading.Thread(target=loop, daemon=True).start()

            def abort(s):
                s.run = False

            def close(s):
                s.run = False

        return S()


@pytest.fixture
def rig(tmp_path, monkeypatch):
    d, logs, mail = tmp_path / "mouth", tmp_path / "logs", tmp_path / "maildir"
    for sub in ("new", "cur", "tmp"):
        (mail / sub).mkdir(parents=True)
    settings = tmp_path / "mouth.json"
    monkeypatch.setenv("MOUTH_DIR", str(d))
    monkeypatch.setenv("MOUTH_MAILBOX", str(mail))
    monkeypatch.setenv("MOUTH_LOG_DIR", str(logs))
    monkeypatch.setenv("MOUTH_SETTINGS", str(settings))
    monkeypatch.setenv("MOUTH_BACKEND", "silence")
    monkeypatch.setenv("MOUTH_SESSION_BIN", "")      # the cascade stays off this machine
    monkeypatch.setenv("MOUTH_RESUME_S", "0")
    monkeypatch.setenv("MOUTH_NOTICE", "off")        # no drop notice: no mailbox is looked up
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm"))   # the ears' heard log
    env_fence.delenv(monkeypatch, "MOUTH_DEVICE")
    env_fence.delenv(monkeypatch, "MOUTH_MAIL_ALLOW")
    env_fence.delenv(monkeypatch, "MOUTH_BARGE_DEVICES")
    heard.log_dir().mkdir(parents=True, exist_ok=True)
    for c in hold._caches.values():
        c.update(t=0.0, v=None)
    return SimpleNamespace(d=d, logs=logs, settings=settings, tmp=tmp_path)


def song(rig, seconds=0.6, rate=48000) -> Path:
    """A 48 kHz 2-channel WAV, like the file in the field record."""
    import wave

    path = rig.tmp / "song.wav"
    x = (0.3 * np.sin(2 * np.pi * 440 * np.arange(int(seconds * rate)) / rate) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.repeat(x, 2).tobytes())
    return path


def said_rows(logs: Path) -> dict:
    rows = [json.loads(x) for f in sorted(Path(logs).glob("heard_*.jsonl"))
            for x in f.read_text().splitlines()]
    return {r["utt"]: r for r in rows if r.get("kind") == "said"}


def brief(rec: dict) -> dict:
    """The fields the field record named, for a failure message."""
    return {k: rec.get(k) for k in ("reason", "device", "device_from", "played_s", "detail")}


def run_player(d: Path, idle: float = 0.5) -> None:
    t = threading.Thread(target=player.serve, kwargs={"d": d, "idle_exit_s": idle}, daemon=True)
    t.start()
    t.join(20)


#: The host settings that leave the line's device to the system default: the
#: file's ``output`` written as ``default`` (the field record's ``device_from:
#: settings``), and no ``output`` at all (``device_from: default``).
HOSTS = {"settings-default": ({"output": "default"}, "settings"),
         "nothing-set": ({}, "default")}


@pytest.mark.parametrize("host", HOSTS)
def test_say_and_play_queue_the_same_device(rig, host):
    """Control (passes before the fix): the caller's ONE device order gives a
    spoken line and a played file the same device and rung."""
    cfg, rung = HOSTS[host]
    rig.settings.write_text(json.dumps(cfg))
    s = mouth.say("hello", d=rig.d, spawn=False)
    p = mouth.play(str(song(rig)), d=rig.d, spawn=False, instrumental=False)
    assert (s["device"], s["device_from"]) == (p["device"], p["device_from"]) == ("default", rung)


@pytest.mark.parametrize("host", HOSTS)
def test_say_and_play_end_on_the_same_output(rig, monkeypatch, host):
    """FAILS before the fix. With the same host settings, the say line plays on the
    system default (the AirPods), and the play of a file is filed ``device-absent`` on
    ``default`` with PortAudio's -9998: the field record."""
    cfg, rung = HOSTS[host]
    rig.settings.write_text(json.dumps(cfg))
    sd = FakeSd(out_channels=1)
    monkeypatch.setattr(output, "_sd", lambda: sd)
    s = mouth.say("hello", d=rig.d, spawn=False)
    p = mouth.play(str(song(rig)), d=rig.d, spawn=False, instrumental=False)
    run_player(rig.d)
    rows = said_rows(rig.logs)
    said, played = rows[s["utt"]], rows[p["utt"]]
    assert (said["reason"], said["device"], said["device_from"]) == ("done", "AirPods", rung), brief(said)
    assert (played["reason"], played["device"], played["device_from"]) == \
        (said["reason"], said["device"], said["device_from"]), brief(played)
    assert played["played_s"] > 0, brief(played)


@pytest.mark.parametrize("kind", ["play", "say"])
def test_a_portaudio_open_error_is_not_device_absent(rig, monkeypatch, kind):
    """FAILS before the fix. The device is present (``mouth devices`` lists it) and
    PortAudio refuses to open it: the record says the open failed, with PortAudio's
    words, never ``device-absent`` (player.py ``play_one``, bed.py ``Bed._play``)."""
    sd = FakeSd(out_channels=2, refuse=True)
    monkeypatch.setattr(output, "_sd", lambda: sd)
    if kind == "play":
        item = mouth.play(str(song(rig)), device="AirPods", d=rig.d, spawn=False, instrumental=False)
    else:
        item = mouth.say("hello", device="AirPods", d=rig.d, spawn=False)
    run_player(rig.d)
    rec = said_rows(rig.logs)[item["utt"]]
    assert rec["played_s"] == 0.0, brief(rec)
    assert "Invalid number of channels [-9998]" in (rec.get("detail") or ""), brief(rec)
    assert rec["reason"] != "device-absent", brief(rec)


# -- fix-001: the default's own channel count, and open-failed -------------------

@pytest.mark.parametrize("have, channels, ignored", [(1, 1, "1-channel device"), (2, 2, None)])
def test_a_panned_open_on_the_default_asks_its_real_channel_count(monkeypatch, have, channels, ignored):
    """FAILED BEFORE (have=1): ``DeviceOut._open`` took the default's count as 2 without
    asking it, so pan 0 (a bed's mixer, ``say --pan``) opened 2 channels and PortAudio
    said -9998. Now the default is asked, as a named device is: 1 channel opens mono
    and the pan is ignored; 2 opens stereo, as before."""
    sd = FakeSd(out_channels=have)
    monkeypatch.setattr(output, "_sd", lambda: sd)
    out = output.DeviceOut("default", 48000, 0.0)
    try:
        assert sd.opened == [(None, channels)]
        assert out.name == "AirPods" and out.pan_ignored == ignored
        assert (out.gains is not None) == (channels == 2)
    finally:
        out.abort()


def test_say_with_a_pan_plays_on_a_one_channel_default(rig, monkeypatch):
    """FAILED BEFORE: not play-only. A spoken line with a pan asked the 1-channel
    default for 2 channels too, and was filed device-absent with -9998."""
    rig.settings.write_text(json.dumps({"output": "default"}))
    sd = FakeSd(out_channels=1)
    monkeypatch.setattr(output, "_sd", lambda: sd)
    s = mouth.say("hello", pan=0.0, d=rig.d, spawn=False)
    run_player(rig.d)
    rec = said_rows(rig.logs)[s["utt"]]
    assert (rec["reason"], rec["device"], rec["device_from"]) == ("done", "AirPods", "settings"), brief(rec)
    assert sd.opened == [(None, 1)]


@pytest.mark.parametrize("kind", ["play", "say"])
def test_a_portaudio_open_error_is_open_failed_with_its_cause(rig, monkeypatch, kind):
    """The reason a present device that refuses to open gets: ``open-failed``, with
    PortAudio's words, its type first, in ``detail``."""
    sd = FakeSd(out_channels=2, refuse=True)
    monkeypatch.setattr(output, "_sd", lambda: sd)
    if kind == "play":
        item = mouth.play(str(song(rig)), device="AirPods", d=rig.d, spawn=False, instrumental=False)
    else:
        item = mouth.say("hello", device="AirPods", d=rig.d, spawn=False)
    run_player(rig.d)
    rec = said_rows(rig.logs)[item["utt"]]
    assert (rec["reason"], rec["played_s"], rec["cut"]) == ("open-failed", 0.0, True), brief(rec)
    assert rec["detail"] == f"PortAudioError: {INVALID_CHANNELS}", brief(rec)


@pytest.mark.parametrize("kind", ["play", "say"])
def test_an_absent_device_is_still_device_absent(rig, monkeypatch, kind):
    """Only a DeviceAbsent is device-absent: a named device PortAudio does not list."""
    sd = FakeSd(out_channels=2)
    monkeypatch.setattr(output, "_sd", lambda: sd)
    if kind == "play":
        item = mouth.play(str(song(rig)), device="nowhere", d=rig.d, spawn=False, instrumental=False)
    else:
        item = mouth.say("hello", device="nowhere", d=rig.d, spawn=False)
    run_player(rig.d)
    rec = said_rows(rig.logs)[item["utt"]]
    assert rec["reason"] == "device-absent", brief(rec)
    assert rec["detail"].startswith("output device 'nowhere' is not present"), brief(rec)
    assert sd.opened == []


def test_said_not_opened_files_only_device_absent_as_absent(monkeypatch):
    """The one place the reason is chosen, for a spoken line and a played file alike."""
    item = {"utt": "u1", "text": "hello", "device": "AirPods"}
    seen = []
    monkeypatch.setattr(player, "said_unplayed",
                        lambda it, reason, detail=None, **kw: seen.append((reason, detail)))
    player.said_not_opened(item, output.DeviceAbsent("output device 'x' is not present"))
    player.said_not_opened(item, output.PortAudioMissing("PortAudio is not installed"))
    player.said_not_opened(item, PortAudioError(INVALID_CHANNELS))
    player.said_not_opened(item, RuntimeError("x" * 400))
    assert seen[0] == ("device-absent", "output device 'x' is not present")
    assert seen[1] == ("device-absent", "PortAudio is not installed")
    assert seen[2] == ("open-failed", f"PortAudioError: {INVALID_CHANNELS}")
    assert seen[3][0] == "open-failed" and len(seen[3][1]) == 300


def test_an_open_failed_notice_says_the_device_was_there():
    from mouth import notice

    rec = {"reason": "open-failed", "played_s": 0.0, "cut": True,
           "detail": f"PortAudioError: {INVALID_CHANNELS}"}
    b = notice.body({"utt": "u1", "text": "hello", "device": "AirPods"}, rec)
    assert "Your line was not said: open-failed." in b
    assert f"why:    open-failed (PortAudioError: {INVALID_CHANNELS})" in b
    assert "was there but would not open" in b
