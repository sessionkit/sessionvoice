"""EARS-144: a bare `mouth say` speaks the caller's settings-cascade voice.

The order is pinned here:
the line's own --voice, then the caller's session, seat and agent layers (as
`session settings` merges them), then the caller's $MOUTH_VOICE
unless the host locks `voice` (EARS-329), then the host file, af_sky. The voice
is resolved in the CALLER and rides on the queued job.
"""

import json
import shutil
import stat
import subprocess

import pytest

from ears.home import SETTINGS
import mouth
from mouth import voices
from tests import env_fence

SID = "test-sid-ears144"
SEAT = "doc.ears144"
AGENT = "doc144"
SESSION = shutil.which("session")


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A whole HOME with all five layers set, each to its own voice."""
    h = tmp_path / "home"
    d = h / ".voicemode" / "mouth"
    d.mkdir(parents=True)
    host = h / SETTINGS / "mouth.json"
    host.parent.mkdir(parents=True)
    host.write_text(json.dumps({"voice": "host_voice"}))
    sdir = h / ".session" / "sessions" / SID / "settings"
    sdir.mkdir(parents=True)
    (sdir / "session.json").write_text(json.dumps({"agent": AGENT, "seat": SEAT}))
    (sdir / "mouth.json").write_text(json.dumps({"voice": "session_voice"}))
    seat = h / ".session" / "seats" / SEAT / "settings"
    seat.mkdir(parents=True)
    (seat / "mouth.json").write_text(json.dumps({"voice": "seat_voice"}))
    agent = h / ".session" / "agent" / "config"
    agent.mkdir(parents=True)
    (agent / f"{AGENT}.md").write_text(
        f"---\nname: {AGENT}\nsettings:\n  mouth.voice: agent_voice\n---\n\nA test agent.\n")
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv("SESSIONS_DIR", raising=False)
    monkeypatch.setenv("MOUTH_DIR", str(d))
    monkeypatch.setenv("MOUTH_SETTINGS", str(host))  # the live host file is this one, linked
    monkeypatch.setenv("MOUTH_LOG_DIR", str(tmp_path / "logs"))
    env_fence.delenv(monkeypatch, "MOUTH_VOICE")
    env_fence.delenv(monkeypatch, "MOUTH_MAIL_LOG")
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.setenv("VOICEMODE_SESSION_ID", SID)
    return {"d": d, "host": host, "session": sdir / "mouth.json",
            "seat": seat / "mouth.json", "agent": agent / f"{AGENT}.md"}


def queued(d, **kw):
    return mouth.say("hello", device="null", spawn=False, d=d, **kw)["voice"]


@pytest.mark.skipif(SESSION is None, reason="no `session` command (sessiontools) on PATH")
def test_the_order_flag_session_seat_agent_env_host_default(home, monkeypatch):
    # every layer set, then peeled one at a time: each beats the one under it (EARS-329 SC1)
    monkeypatch.setenv("MOUTH_SESSION_BIN", SESSION)
    monkeypatch.setenv("MOUTH_VOICE", "env_voice")
    d = home["d"]

    def caller():
        return voices.default_voice(d, SID, caller=True)

    assert queued(d, voice="flag_voice") == "flag_voice"
    assert queued(d) == "session_voice"
    assert caller() == ("session_voice", "session")
    home["session"].unlink()
    assert queued(d) == "seat_voice"
    assert caller() == ("seat_voice", "seat")
    assert queued(d, voice="flag_voice") == "flag_voice"
    home["seat"].unlink()
    assert queued(d) == "agent_voice"
    assert caller() == ("agent_voice", "agent")
    home["agent"].write_text(f"---\nname: {AGENT}\n---\n\nNo settings.\n")
    assert queued(d) == "env_voice"
    assert caller() == ("env_voice", "env")
    assert voices.default_voice(d, SID) == ("host_voice", "settings")   # the box's order
    env_fence.delenv(monkeypatch, "MOUTH_VOICE")
    assert queued(d) == "host_voice"
    assert caller() == ("host_voice", "settings")
    home["host"].unlink()
    assert queued(d) == "af_sky"
    assert caller() == ("af_sky", "builtin")


@pytest.mark.skipif(SESSION is None, reason="no `session` command (sessiontools) on PATH")
def test_a_host_lock_pins_the_voice_over_the_seat(home, monkeypatch):
    monkeypatch.setenv("MOUTH_SESSION_BIN", SESSION)
    monkeypatch.setenv("MOUTH_VOICE", "env_voice")   # nor the env (EARS-329)
    home["host"].write_text(json.dumps({"voice": "host_voice", "_locked": ["voice"]}))
    home["session"].unlink()
    assert queued(home["d"]) == "host_voice"
    assert voices.voices(home["d"], SID)["from"] == "settings"


@pytest.mark.skipif(SESSION is None, reason="no `session` command (sessiontools) on PATH")
def test_a_seat_that_sets_its_voice_gets_it_and_one_that_does_not_gets_the_host(home, monkeypatch):
    # Success Criteria 2: one queued say with no --voice, seat set, then unset
    monkeypatch.setenv("MOUTH_SESSION_BIN", SESSION)
    home["session"].unlink()
    home["agent"].write_text(f"---\nname: {AGENT}\n---\n\nNo settings.\n")
    assert queued(home["d"]) == "seat_voice"
    home["seat"].unlink()
    assert queued(home["d"]) == "host_voice"


@pytest.mark.skipif(SESSION is None, reason="no `session` command (sessiontools) on PATH")
def test_mouth_voices_reports_env_under_a_session(home, monkeypatch):
    monkeypatch.setenv("MOUTH_SESSION_BIN", SESSION)
    monkeypatch.setenv("MOUTH_VOICE", "env_voice")
    for k in ("session", "seat"):
        home[k].unlink()
    home["agent"].write_text(f"---\nname: {AGENT}\n---\n\nNo settings.\n")
    v = voices.voices(home["d"], SID)
    assert (v["default"], v["from"]) == ("env_voice", "env")


@pytest.mark.skipif(SESSION is None, reason="no `session` command (sessiontools) on PATH")
def test_the_voice_rides_on_the_queued_job(home, monkeypatch):
    monkeypatch.setenv("MOUTH_SESSION_BIN", SESSION)
    home["session"].unlink()
    item = mouth.say("hello", device="null", spawn=False, d=home["d"])
    assert item["voice"] == "seat_voice" and item["session"] == SID


@pytest.mark.skipif(SESSION is None, reason="no `session` command (sessiontools) on PATH")
def test_mouth_voices_reports_the_callers_layer(home, monkeypatch):
    monkeypatch.setenv("MOUTH_SESSION_BIN", SESSION)
    v = voices.voices(home["d"], SID)
    assert (v["default"], v["from"]) == ("session_voice", "session")


def test_no_session_id_reads_only_the_host(home, monkeypatch):
    monkeypatch.setenv("MOUTH_SESSION_BIN", SESSION or "/no/such/session")
    monkeypatch.delenv("VOICEMODE_SESSION_ID")
    assert queued(home["d"]) == "host_voice"


def test_the_cascade_off_reads_only_the_host(home, monkeypatch):
    monkeypatch.setenv("MOUTH_SESSION_BIN", "")
    assert queued(home["d"]) == "host_voice"


def fake(tmp_path, body):
    p = tmp_path / "fake-session"
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return str(p)


@pytest.mark.parametrize("body", [
    "exit 1",
    "echo not json",
    "echo '{}'",
    "echo '{\"tools\": {}}'",
    "echo '{\"tools\": {\"mouth\": {\"values\": {\"voice\": 7}, \"origins\": {\"voice\": \"/s\"}, \"sources\": [{\"layer\": \"seat\", \"path\": \"/s\"}]}}}'",
    "sleep 10",
])
def test_a_broken_session_command_falls_back_to_the_host(home, monkeypatch, tmp_path, body):
    monkeypatch.setattr(subprocess, "run", _short(subprocess.run))
    monkeypatch.setenv("MOUTH_SESSION_BIN", fake(tmp_path, body))
    assert queued(home["d"]) == "host_voice"


def test_a_missing_session_command_falls_back_to_the_host(home, monkeypatch):
    monkeypatch.setenv("MOUTH_SESSION_BIN", "/no/such/session")
    assert queued(home["d"]) == "host_voice"


def test_a_value_from_the_host_layer_is_left_to_the_host_file(home, monkeypatch, tmp_path):
    out = {"tools": {"mouth": {"values": {"voice": "x"}, "origins": {"voice": "/h"},
                               "sources": [{"layer": "host", "path": "/h"}]}}}
    monkeypatch.setenv("MOUTH_SESSION_BIN", fake(tmp_path, f"echo '{json.dumps(out)}'"))
    assert queued(home["d"]) == "host_voice"


def locked_by(tmp_path, layer):
    """A fake `session` whose cascade has no voice above the host, and ``voice``
    locked by a file of LAYER."""
    out = {"tools": {"mouth": {"values": {}, "origins": {}, "locked": {"voice": "/h"},
                               "sources": [{"layer": layer, "path": "/h"}]}}}
    return fake(tmp_path, f"echo '{json.dumps(out)}'")


@pytest.mark.parametrize("layer", ["host", "template"])
def test_a_lock_in_the_cascade_beats_the_callers_env(home, monkeypatch, tmp_path, layer):
    # source (ii): the mouth's own file has no _locked; the lock is the cascade's, whose
    # host layer may be another file than $MOUTH_SETTINGS
    monkeypatch.setenv("MOUTH_SESSION_BIN", locked_by(tmp_path, layer))
    monkeypatch.setenv("MOUTH_VOICE", "env_voice")
    assert voices.cascade(SID, voices.LINE_KEYS)[voices.HOST_LOCKS] == (("voice",), "host")
    assert queued(home["d"]) == "host_voice"
    assert voices.default_voice(home["d"], SID, caller=True) == ("host_voice", "settings")
    assert voices.voices(home["d"], SID)["from"] == "settings"
    home["host"].write_text(json.dumps({"output": "default"}))   # a lock with no host voice
    assert queued(home["d"]) == "af_sky"


@pytest.mark.parametrize("layer", ["seat", "agent", "session"])
def test_a_lock_above_the_host_does_not_pin_the_host(home, monkeypatch, tmp_path, layer):
    monkeypatch.setenv("MOUTH_SESSION_BIN", locked_by(tmp_path, layer))
    monkeypatch.setenv("MOUTH_VOICE", "env_voice")
    assert voices.HOST_LOCKS not in voices.cascade(SID, voices.LINE_KEYS)
    assert queued(home["d"]) == "env_voice"


def test_the_lock_entry_is_invisible_to_the_level_readers(home, monkeypatch, tmp_path):
    # every reader looks the cascade up by key: the reserved entry changes nothing
    monkeypatch.setenv("MOUTH_SESSION_BIN", locked_by(tmp_path, "host"))
    item = mouth.say("hello", device="null", spawn=False, d=home["d"])
    assert item["voice"] == "host_voice"
    assert "_locked" not in item and "volume" not in item


def _short(run):
    def r(*a, **kw):
        kw["timeout"] = 0.5
        return run(*a, **kw)
    return r
