"""EARS-295 do-004: the ``mouth duck`` verb and ``mouth status``'s ``duck`` key
(design.md s4, super ruling Q3). No audio, no player: a temp mouth dir and a
fake ``duckswitch.clock``.

T3, the CLI half: ``mouth duck off --for 30m``, then ``mouth duck`` shows
``effective: false``, ``by: override`` and ``remaining_s``; ``reset`` gives the
setting back. Without ``--for`` the override lasts an hour (Q3).
"""

from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime
from pathlib import Path

import pytest

import mouth
from mouth import __main__ as cli
from mouth import duckswitch
from tests import env_fence
from tests._anchor import ANCHOR_SUMMER

SID = "duck-cli-session"
T0 = ANCHOR_SUMMER.timestamp()


@pytest.fixture
def d(tmp_path, monkeypatch) -> Path:
    """A temp mouth dir (never the live one), its settings file its own."""
    m = tmp_path / "mouth"
    m.mkdir()
    monkeypatch.setenv("MOUTH_DIR", str(m))
    env_fence.delenv(monkeypatch, "MOUTH_SETTINGS")
    monkeypatch.setenv("MOUTH_SESSION_BIN", "")
    monkeypatch.setenv("VOICEMODE_SESSION_ID", SID)
    return m


@pytest.fixture
def clock(monkeypatch) -> list:
    t = [T0]
    monkeypatch.setattr(duckswitch, "clock", lambda: t[0])
    return t


def host(d: Path, **kw) -> None:
    (d / "settings.json").write_text(json.dumps(kw))


def local(t: float) -> str:
    return datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds")


def duck(capsys, *args) -> dict:
    """Run ``mouth duck ARGS``; it exits 0 and prints one JSON readout."""
    assert cli.main(["duck", *args]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1
    return json.loads(out[0])


# -- --for ----------------------------------------------------------------------

@pytest.mark.parametrize("text,s", [("90s", 90.0), ("30m", 1800.0), ("1h", 3600.0), ("45", 45.0),
                                    ("1.5h", 5400.0), (" 2M ", 120.0), (".5m", 30.0)])
def test_for_takes_seconds_minutes_hours_or_plain_seconds(text, s):
    assert duckswitch.parse_for(text) == s


@pytest.mark.parametrize("text", ["", "0", "0m", "forever", "-5m", "1d", "inf", "nan", "1h30m",
                                  "m", "30 minutes"])
def test_for_refuses_anything_else_and_there_is_no_forever(text):
    with pytest.raises(ValueError, match="90s, 30m, 1h"):
        duckswitch.parse_for(text)


# -- the readout, pure, on a fake clock -------------------------------------------

def test_readout_with_no_override_is_the_setting(d):
    host(d, music_duck=False)
    assert duckswitch.readout(d, now=T0) == {
        "effective": False, "by": "settings", "override": None,
        "setting": {"on": False, "from": "settings"}}


def test_readout_shows_remaining_s_then_expired_at(d):
    host(d, music_duck=True)
    duckswitch.set(d, on=False, for_s=1800, now=T0, by="ivy")
    r = duckswitch.readout(d, now=T0 + 6)
    assert r == {"effective": False, "by": "override",
                 "override": {"on": False, "until": local(T0 + 1800), "remaining_s": 1794, "by": "ivy"},
                 "setting": {"on": True, "from": "settings"}}
    # a part second left still says a second, never 0 while it is live
    assert duckswitch.readout(d, now=T0 + 1799.5)["override"]["remaining_s"] == 1
    # at until it has run out: the setting answers, the readout says when it ended
    r = duckswitch.readout(d, now=T0 + 1800)
    assert (r["effective"], r["by"]) == (True, "settings")
    assert r["override"] == {"on": False, "expired_at": local(T0 + 1800), "by": "ivy"}


# -- T3, the CLI half ---------------------------------------------------------------

def test_t3_duck_off_for_30m_reads_back_and_reset_gives_the_setting_back(d, clock, capsys):
    host(d, music_duck=True)
    assert duck(capsys) == {"effective": True, "by": "settings", "override": None,
                            "setting": {"on": True, "from": "settings"}, "beds": []}
    # off: the verb prints the readout it wrote, with until, so the agent can say when it ends
    r = duck(capsys, "off", "--for", "30m")
    assert (r["effective"], r["by"]) == (False, "override")
    assert r["override"] == {"on": False, "until": local(T0 + 1800), "remaining_s": 1800, "by": SID}
    clock[0] += 60
    r = duck(capsys)
    assert (r["effective"], r["by"], r["override"]["remaining_s"]) == (False, "override", 1740)
    assert r["setting"] == {"on": True, "from": "settings"}       # the setting is untouched under it
    assert json.loads((d / "duck.json").read_text())["until"] == T0 + 1800
    # reset: the setting again, now, and the file is gone
    r = duck(capsys, "reset")
    assert (r["effective"], r["by"], r["override"]) == (True, "settings", None)
    assert not (d / "duck.json").exists()


def test_t3_without_for_the_override_lasts_an_hour(d, clock, capsys):
    host(d, music_duck=False)
    r = duck(capsys, "on")
    assert (r["effective"], r["by"]) == (True, "override")        # on beats the setting's off
    assert r["override"]["remaining_s"] == 3600 == duckswitch.OVERRIDE_DEFAULT_S
    assert r["override"]["until"] == local(T0 + 3600)


def test_t3_an_expired_override_reads_back_as_the_setting(d, clock, capsys):
    host(d, music_duck=True)
    duck(capsys, "off", "--for", "90s")
    clock[0] += 90
    r = duck(capsys)
    assert (r["effective"], r["by"]) == (True, "settings")
    assert r["override"] == {"on": False, "expired_at": local(T0 + 90), "by": SID}
    # a new override replaces the expired one
    r = duck(capsys, "off", "--for", "10m")
    assert r["override"]["remaining_s"] == 600


def test_duck_reset_with_nothing_set_is_quiet(d, clock, capsys):
    host(d, music_duck=True)
    assert duck(capsys, "reset")["override"] is None


def test_the_override_names_the_agent_when_there_is_no_session(d, clock, capsys, monkeypatch):
    for k in ("VOICEMODE_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("VOICEMODE_AGENT", "ivy")
    assert duck(capsys, "off")["override"]["by"] == "ivy"


@pytest.mark.parametrize("args,msg", [(["--for", "30m"], "--for goes with off or on"),
                                      (["reset", "--for", "30m"], "--for goes with off or on"),
                                      (["off", "--for", "forever"], "is not a time"),
                                      (["on", "--for", "0"], "is not a time")])
def test_a_bad_call_exits_2_and_writes_nothing(d, clock, capsys, args, msg):
    duckswitch.set(d, on=True, for_s=60)
    before = (d / "duck.json").read_bytes()
    assert cli.main(["duck", *args]) == 2
    err = capsys.readouterr()
    assert msg in err.err and err.out == ""
    assert (d / "duck.json").read_bytes() == before


def test_an_unknown_state_is_a_usage_error(d, capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["duck", "maybe"])
    assert e.value.code == 2


# -- mouth status's duck key, and the beds in the readout ----------------------------

def test_status_gains_a_duck_key_with_the_readouts_fields(d, clock, capsys):
    host(d, music_duck=True)
    assert mouth.status(d)["duck"] == {"effective": True, "by": "settings", "override": None}
    duck(capsys, "off", "--for", "30m")
    clock[0] += 30
    assert cli.main(["status"]) == 0
    st = json.loads(capsys.readouterr().out)
    assert st["duck"] == {"effective": False, "by": "override",
                          "override": {"on": False, "until": local(T0 + 1800),
                                       "remaining_s": 1770, "by": SID}}


def bed_json(d: Path) -> None:
    (d / "bed.json").write_text(json.dumps({
        "null": {"utt": "u1", "device": "null", "duck": {"on": False, "level": 20, "from": "flag"},
                 "ducked": False, "duck_by": "argument"},
        "spk": {"utt": "u2", "device": "spk", "duck": {"on": True, "level": 20, "from": "settings"},
                "ducked": True, "duck_by": "settings"}}))


def test_the_readout_shows_each_playing_beds_own_answer(d, clock):
    host(d, music_duck=True)
    bed_json(d)
    fd = os.open(str(d / "player.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)                                 # a player holds its lock
    try:
        beds = mouth.duck_state(d)["beds"]
    finally:
        os.close(fd)
    assert beds == [
        {"utt": "u1", "device": "null", "duck_from": "argument", "duck_by": "argument", "dipping": False},
        {"utt": "u2", "device": "spk", "duck_from": "settings", "duck_by": "settings", "dipping": True}]


def test_a_bed_json_left_by_a_dead_player_shows_no_beds(d, clock):
    host(d, music_duck=True)
    bed_json(d)
    assert mouth.duck_state(d)["beds"] == []


# -- T8: the precedence, said the same in every place it is said (do-006) ----------

ROOT = Path(__file__).resolve().parents[2]


def test_t8_the_schema_states_the_precedence_line():
    desc = json.loads((ROOT / "settings/mouth.schema.json").read_text())["properties"]["music_duck"]["description"]
    assert duckswitch.PRECEDENCE in desc
    assert "Off: it plays on at its own level" in desc and "today's behaviour" not in desc


def test_t8_mouth_duck_help_states_the_same_precedence_line(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["duck", "--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "precedence: " + duckswitch.PRECEDENCE in out              # one line, never re-wrapped


def test_t8_the_play_flags_say_the_argument_beats_the_override(capsys):
    with pytest.raises(SystemExit):
        cli.main(["play", "--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert "never dip this file (beats the setting and any override)" in out
    assert "a barge-in cuts it" not in out                            # the OFF-bed is gone

