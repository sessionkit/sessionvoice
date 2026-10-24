"""EARS-361: ``say`` with no device falls back to the host settings file's ``output``.

The order is ``--device`` > ``$MOUTH_DEVICE`` > ``output`` in the host
settings file (``voices.settings_path``) > the system default output
(EARS-368, ``mouth.resolve_device``; a mailed line takes the same order:
``test_mouth_default_device.py``). An ``output`` of ``"default"`` written in
the file is honoured as written; an absent file, an absent key, or an empty or
non-string value is unset, and the line takes the last rung, ``default``,
with ``device_from: default``. Until EARS-368 that last rung was a refusal.

Fakes only: a tmp mouth dir, a tmp settings file, ``spawn=False`` (no player),
the ``silence`` backend, and the cascade off (``tests/conftest.py``).
"""

from __future__ import annotations

import json

import pytest

import mouth
from mouth import box as _box
from tests import env_fence


@pytest.fixture
def rig(tmp_path, monkeypatch):
    d = tmp_path / "mouth"
    settings = tmp_path / "mouth.json"
    monkeypatch.setenv("MOUTH_DIR", str(d))
    monkeypatch.setenv("MOUTH_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MOUTH_SETTINGS", str(settings))
    monkeypatch.setenv("MOUTH_SESSION_BIN", "")
    env_fence.delenv(monkeypatch, "MOUTH_DEVICE")
    return d, settings


def queued(d) -> list[dict]:
    return [_box.public(x) for x in _box.Queue(d).lines()]


def say(d, **kw):
    return mouth.say("hello", backend="silence", d=d, spawn=False, **kw)


def test_no_device_and_no_env_takes_the_settings_output(rig):
    d, settings = rig
    settings.write_text(json.dumps({"output": "MacBook Pro Speakers"}))
    item = say(d)
    assert item["device"] == "MacBook Pro Speakers"
    assert [x["device"] for x in queued(d)] == ["MacBook Pro Speakers"]


def test_an_explicit_default_is_honoured_as_written(rig):
    d, settings = rig
    settings.write_text(json.dumps({"output": "default"}))
    say(d)
    assert [x["device"] for x in queued(d)] == ["default"]


def test_the_env_outranks_the_settings_output(rig, monkeypatch):
    d, settings = rig
    settings.write_text(json.dumps({"output": "MacBook Pro Speakers"}))
    monkeypatch.setenv("MOUTH_DEVICE", "null")
    say(d)
    assert [x["device"] for x in queued(d)] == ["null"]


def test_the_flag_outranks_the_env_and_the_settings_output(rig, monkeypatch):
    d, settings = rig
    settings.write_text(json.dumps({"output": "MacBook Pro Speakers"}))
    monkeypatch.setenv("MOUTH_DEVICE", "default")
    say(d, device="null")
    assert [x["device"] for x in queued(d)] == ["null"]


def test_play_takes_the_settings_output_too(rig, tmp_path):
    d, settings = rig
    settings.write_text(json.dumps({"output": "null"}))
    snd = tmp_path / "ding.wav"
    snd.write_bytes(b"RIFF")
    mouth.play(str(snd), d=d, spawn=False)
    assert [x["device"] for x in queued(d)] == ["null"]


@pytest.mark.parametrize("content", [
    None,                                  # no file
    "{}",                                  # no `output` key
    json.dumps({"output": ""}),            # empty
    json.dumps({"output": "   "}),         # blank
    json.dumps({"output": None}),          # null
    json.dumps({"output": 3}),             # not a string
    json.dumps({"output": ["airpods"]}),   # not a string
    "not json",                            # unreadable
    json.dumps(["output", "airpods"]),     # not an object
], ids=["no-file", "no-key", "empty", "blank", "null", "number", "list", "bad-json", "not-object"])
def test_an_unset_output_takes_the_system_default(rig, content):
    """EARS-368 changed this on purpose: it was ``..._still_refuses_naming_every_fix``."""
    d, settings = rig
    if content is not None:
        settings.write_text(content)
    item = say(d)
    assert (item["device"], item["device_from"]) == ("default", "default")
    assert [(x["device"], x["device_from"]) for x in queued(d)] == [("default", "default")]


def test_the_schema_default_is_not_the_settings_rung(rig):
    """The schema documents ``"default": "default"``; nothing writes it, so a file
    without the key is unset: the line reaches the last rung, ``default``, and says
    so, never ``settings``. EARS-368 changed this on purpose: it was
    ``test_the_schema_default_is_never_applied``, a refusal."""
    d, settings = rig
    settings.write_text(json.dumps({"voice": "af_sky"}))
    assert say(d)["device_from"] == "default"
    settings.write_text(json.dumps({"output": "default"}))
    assert say(d)["device_from"] == "settings"   # written in the file: the settings rung


def test_settings_device_reads_only_a_set_string(rig):
    d, settings = rig
    assert mouth.settings_device(d) is None
    settings.write_text(json.dumps({"output": " AirPods Pro "}))
    assert mouth.settings_device(d) == " AirPods Pro "   # exact, as written
