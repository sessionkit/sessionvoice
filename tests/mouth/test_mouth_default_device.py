"""EARS-368: ONE device order for ``say`` and a mailed line, ending on the system default.

``mouth.resolve_device`` is the one function. ``say()`` (and so ``play`` and
``again``) and ``box.read()`` both call it. The order is: the line's own ask
(``--device``, a mail's ``X-Mouth-Device`` or its extra's ``device``, a call box),
then ``$MOUTH_DEVICE``, then ``output`` in the host settings file, then
``"default"``, the system default output, by name. ``null`` at any rung plays
nothing. The line's ``device_from`` names the rung, on the queued item and on
its ``saying`` and ``said`` rows.

Before EARS-368, ``say`` with nothing set refused, and a mailed line with
nothing set reached the player with device None and was filed
``said reason=device-absent``. It played nothing.

Fakes only: a tmp mouth dir and maildir, a tmp settings file, the ``silence``
backend, and a fake PortAudio (``output._sd``). No real device or HOME.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace

import pytest

import mouth
from mouth import box as _box
from mouth import output, player

from maildir_put import put
from tests import env_fence

HOST = socket.gethostname().split(".")[0].lower()


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
    monkeypatch.setenv("MOUTH_SESSION_BIN", "")
    env_fence.delenv(monkeypatch, "MOUTH_DEVICE")
    env_fence.delenv(monkeypatch, "MOUTH_MAIL_ALLOW")
    return SimpleNamespace(d=d, logs=logs, mail=mail, settings=settings, n=[0])


def deliver(rig, body="hello", sender=f"vera@{HOST}.example.net", **headers) -> Path:
    """A mail from outside, as an MTA delivers it: no MAC, so never a raw line."""
    rig.n[0] += 1
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = sender, f"mouth@{HOST}", "hi"
    m["Message-ID"] = f"<{rig.n[0]}.test@{HOST}.session-mail>"
    for k, v in headers.items():
        m[k.replace("_", "-")] = v
    m.set_content(body)
    return put(rig.mail, f"{time.time_ns()}.{rig.n[0]}.{HOST}", bytes(m))


def lines(logs: Path) -> list[dict]:
    return [json.loads(x) for f in sorted(Path(logs).glob("heard_*.jsonl"))
            for x in f.read_text().splitlines()]


def run_player(d: Path, idle: float = 0.3) -> None:
    t = threading.Thread(target=player.serve, kwargs={"d": d, "idle_exit_s": idle}, daemon=True)
    t.start()
    t.join(10)


# -- the one function ---------------------------------------------------------

def test_the_order_is_asked_env_settings_default(rig, monkeypatch):
    rig.settings.write_text(json.dumps({"output": "MacBook Pro Speakers"}))
    monkeypatch.setenv("MOUTH_DEVICE", "AirPods")
    assert mouth.resolve_device("Studio", "flag", rig.d) == ("Studio", "flag")
    assert mouth.resolve_device("Studio", "mail", rig.d) == ("Studio", "mail")
    assert mouth.resolve_device("call:3", "call", rig.d) == ("call:3", "call")
    assert mouth.resolve_device(None, "flag", rig.d) == ("AirPods", "env")
    env_fence.delenv(monkeypatch, "MOUTH_DEVICE")
    assert mouth.resolve_device(None, "mail", rig.d) == ("MacBook Pro Speakers", "settings")
    rig.settings.write_text("{}")
    assert mouth.resolve_device(None, "mail", rig.d) == ("default", "default")
    assert mouth.resolve_device("", "flag", rig.d) == ("default", "default")


@pytest.mark.parametrize("rung", ["asked", "env", "settings"])
def test_null_at_any_rung_is_silence_never_the_default(rig, monkeypatch, rung):
    asked = "null" if rung == "asked" else None
    if rung == "env":
        monkeypatch.setenv("MOUTH_DEVICE", "null")
    if rung == "settings":
        rig.settings.write_text(json.dumps({"output": "null"}))
    device, _ = mouth.resolve_device(asked, "flag", rig.d)
    assert device == "null"


def test_an_unknown_rung_is_refused(rig):
    with pytest.raises(ValueError, match="asked by"):
        mouth.resolve_device("x", "settings", rig.d)


def test_box_read_calls_the_same_function_not_a_copy(rig, monkeypatch):
    """One resolver: a stand-in for ``mouth.resolve_device`` decides the mailed line too."""
    seen = []

    def stand_in(asked, asked_from="flag", d=None):
        seen.append((asked, asked_from))
        return "Stand-in", "default"

    monkeypatch.setattr(mouth, "resolve_device", stand_in)
    x = _box.read(deliver(rig), rig.d)
    assert (x["device"], x["device_from"]) == ("Stand-in", "default")
    assert seen == [(None, "mail")]
    assert mouth.say("hi", d=rig.d, spawn=False)["device"] == "Stand-in"


# -- say, play, again: nothing set is the system default, no longer a refusal ----

def test_say_with_nothing_set_takes_the_system_default(rig):
    item = mouth.say("hello", d=rig.d, spawn=False)
    assert (item["device"], item["device_from"]) == ("default", "default")
    (x,) = _box.Queue(rig.d).lines()
    assert (x["device"], x["device_from"]) == ("default", "default")


def test_say_names_each_rung_on_the_line(rig, monkeypatch):
    assert mouth.say("a", device="null", d=rig.d, spawn=False)["device_from"] == "flag"
    rig.settings.write_text(json.dumps({"output": "null"}))
    assert mouth.say("b", d=rig.d, spawn=False)["device_from"] == "settings"
    monkeypatch.setenv("MOUTH_DEVICE", "null")
    assert mouth.say("c", d=rig.d, spawn=False)["device_from"] == "env"
    got = {x["text"]: x.get("device_from") for x in _box.Queue(rig.d).lines()}
    # past the caller's own choice the rung rides the mail; a named line's mail is as it was
    assert got == {"a": None, "b": "settings", "c": None}


def test_a_named_line_is_the_mail_it_was(rig, monkeypatch):
    """No X-Mouth-Extra for --device or $MOUTH_DEVICE (test_mouth_golden.py); one for the default."""
    import email

    monkeypatch.setenv("MOUTH_DEVICE", "null")
    mouth.say("named", d=rig.d, spawn=False)
    env_fence.delenv(monkeypatch, "MOUTH_DEVICE")
    mouth.say("unnamed", d=rig.d, spawn=False)
    got = {}
    for f in (rig.mail / "new").iterdir():
        m = email.message_from_bytes(f.read_bytes())
        got[m.get_payload().strip()] = (m["X-Mouth-Device"], m["X-Mouth-Extra"])
    assert got == {"named": ("null", None), "unnamed": ("default", '{"device_from":"default"}')}


def test_play_with_nothing_set_takes_the_system_default(rig, tmp_path):
    snd = tmp_path / "ding.wav"
    snd.write_bytes(b"RIFF")
    item = mouth.play(str(snd), d=rig.d, spawn=False)
    assert (item["device"], item["device_from"]) == ("default", "default")


# -- a mailed line: the same order -----------------------------------------------

def test_a_mailed_line_with_nothing_set_takes_the_system_default(rig):
    """The bug: this read device None, and the player filed it device-absent."""
    x = _box.read(deliver(rig), rig.d)
    assert x["device"] == "default"
    assert x["device_from"] == "default"


def test_a_mailed_line_takes_the_settings_output(rig):
    rig.settings.write_text(json.dumps({"output": "MacBook Pro Speakers"}))
    x = _box.read(deliver(rig), rig.d)
    assert (x["device"], x["device_from"]) == ("MacBook Pro Speakers", "settings")


def test_a_mailed_line_takes_the_env_over_the_settings(rig, monkeypatch):
    rig.settings.write_text(json.dumps({"output": "MacBook Pro Speakers"}))
    monkeypatch.setenv("MOUTH_DEVICE", "null")
    x = _box.read(deliver(rig), rig.d)
    assert (x["device"], x["device_from"]) == ("null", "env")


def test_a_mailed_device_outranks_the_rest(rig, monkeypatch):
    rig.settings.write_text(json.dumps({"output": "MacBook Pro Speakers"}))
    monkeypatch.setenv("MOUTH_DEVICE", "AirPods")
    x = _box.read(deliver(rig, X_Mouth_Device="null"), rig.d)
    assert (x["device"], x["device_from"]) == ("null", "mail")
    y = _box.read(deliver(rig, X_Mouth_Extra=json.dumps({"device": "null"})), rig.d)
    assert (y["device"], y["device_from"]) == ("null", "mail")


def test_a_mail_cannot_forge_its_rung(rig):
    x = _box.read(deliver(rig, X_Mouth_Extra=json.dumps({"device_from": "flag"})), rig.d)
    assert (x["device"], x["device_from"]) == ("default", "default")


def test_a_raw_line_keeps_the_rung_say_chose(rig):
    rig.settings.write_text(json.dumps({"output": "null"}))
    mouth.say("hello", d=rig.d, spawn=False)
    (f,) = (rig.mail / "new").iterdir()
    x = _box.read(f, rig.d)
    assert x["_raw"] and (x["device"], x["device_from"]) == ("null", "settings")


# -- at play: the default plays, and only no default at all is device-absent -----

class FakeSd:
    """A fake PortAudio: two outputs, a default (index 1, or none), streams that pull."""

    def __init__(self, default_out=1):
        self.default = SimpleNamespace(device=(0, default_out))
        self.opened = []

    def _terminate(self):
        pass

    def _initialize(self):
        pass

    def query_devices(self, i=None):
        devs = [{"name": "MacBook Pro Microphone", "max_output_channels": 0},
                {"name": "MacBook Pro Speakers", "max_output_channels": 2}]
        return devs if i is None else devs[i]

    def OutputStream(self, *, device, samplerate, channels, dtype, callback):
        import numpy as np

        self.opened.append(device)

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


def test_a_mailed_line_with_nothing_set_plays_on_the_system_default(rig, monkeypatch):
    sd = FakeSd()
    monkeypatch.setattr(output, "_sd", lambda: sd)
    deliver(rig, "hello there")
    run_player(rig.d)
    (said,) = [r for r in lines(rig.logs) if r["kind"] == "said"]
    assert said["reason"] == "done", said
    assert said["device"] == "MacBook Pro Speakers"   # the default's own name
    (saying,) = [r for r in lines(rig.logs) if r["kind"] == "saying"]
    assert said["device_from"] == saying["device_from"] == "default"
    assert sd.opened == [None]   # PortAudio's default output, opened as the default


def test_no_default_output_at_all_is_device_absent_and_says_so(rig, monkeypatch):
    monkeypatch.setattr(output, "_sd", lambda: FakeSd(default_out=-1))
    deliver(rig)
    mouth.say("from say", d=rig.d, spawn=False)
    run_player(rig.d)
    said = [r for r in lines(rig.logs) if r["kind"] == "said"]
    assert len(said) == 2
    for r in said:
        assert (r["reason"], r["played_s"]) == ("device-absent", 0.0)
        assert "no default output device" in r["detail"]
        assert r["device_from"] == "default"


def test_a_named_device_that_is_absent_never_falls_back_to_the_default(rig, monkeypatch):
    sd = FakeSd()
    monkeypatch.setattr(output, "_sd", lambda: sd)
    deliver(rig, X_Mouth_Device="AirPods")
    run_player(rig.d)
    (said,) = [r for r in lines(rig.logs) if r["kind"] == "said"]
    assert (said["reason"], said["device"], said["device_from"]) == ("device-absent", "AirPods", "mail")
    assert sd.opened == []
