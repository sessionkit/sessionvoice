"""EARS-361: ``say`` with no device falls back to the host settings file's ``output``.

The order is ``--device`` > ``$MOUTH_DEVICE`` > ``output`` in the host
settings file (``voices.settings_path``) > refuse. An ``output`` of ``"default"``
written in the file is honoured as written; an absent file, an absent key, or an
empty or non-string value is unset, and the line is refused with a message that
names all three fixes. The schema's documented default is never applied.

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
def test_an_unset_output_still_refuses_naming_every_fix(rig, content):
    d, settings = rig
    if content is not None:
        settings.write_text(content)
    with pytest.raises(ValueError) as e:
        say(d)
    msg = str(e.value)
    assert "no device" in msg
    assert "--device" in msg and "MOUTH_DEVICE" in msg
    assert "`output`" in msg and str(settings) in msg
    assert queued(d) == []


def test_the_schema_default_is_never_applied(rig):
    """The schema documents ``"default": "default"``; nothing writes it, so a file
    without the key must refuse, never play on the system default by accident."""
    d, settings = rig
    settings.write_text(json.dumps({"voice": "af_sky"}))
    with pytest.raises(ValueError, match="no device"):
        say(d)


def test_settings_device_reads_only_a_set_string(rig):
    d, settings = rig
    assert mouth.settings_device(d) is None
    settings.write_text(json.dumps({"output": " AirPods Pro "}))
    assert mouth.settings_device(d) == " AirPods Pro "   # exact, as written
