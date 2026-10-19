"""EARS-152 do-001: a line's level and a played file's duck, as settings, flags
and per-item fields (design.md s1 + s7). Nothing here changes playback.

Ducking is on by default, and `mouth say`/`mouth play`
each have a control for the
volume.

The order, highest first: the line's own (a flag, or a mail's X-Mouth-Volume),
the caller's cascade (session, seat, agent), the host file, the built-in.
Seams: cascade-duck, volume-cascade-default, volume-refused, volume-mail
(schema-covers-duck-volume is in tests/test_ears_settings_schema.py).
"""

import json
import socket
import stat
import threading
import time
from email.message import EmailMessage

import pytest

import mouth
from mouth import __main__ as cli
from mouth import box, player, voices

from maildir_put import put
from tests import env_fence

SID = "test-sid-ears152"
HOST = socket.gethostname().split(".")[0].lower()


@pytest.fixture
def home(tmp_path, monkeypatch):
    d = tmp_path / "mouth"
    d.mkdir()
    host = tmp_path / "mouth.json"
    monkeypatch.setenv("MOUTH_DIR", str(d))
    monkeypatch.setenv("MOUTH_SETTINGS", str(host))
    monkeypatch.setenv("MOUTH_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MOUTH_DEVICE", "null")
    monkeypatch.setenv("MOUTH_BACKEND", "silence")
    monkeypatch.setenv("MOUTH_SESSION_BIN", "")   # the cascade off unless a test fakes it
    monkeypatch.setenv("VOICEMODE_SESSION_ID", SID)
    env_fence.delenv(monkeypatch, "MOUTH_VOICE")
    return {"d": d, "host": host, "tmp": tmp_path}


def host(home, **kw):
    home["host"].write_text(json.dumps(kw))


def cascade(home, monkeypatch, layer="session", **values):
    """A fake `session settings --json`: ``values`` set in ``layer``."""
    path = f"/fake/{layer}/mouth.json"
    out = {"tools": {"mouth": {
        "values": values, "origins": {k: path for k in values},
        "sources": [{"layer": layer, "path": path}, {"layer": "host", "path": str(home["host"])}]}}}
    p = home["tmp"] / f"fake-session-{layer}"
    p.write_text(f"#!/bin/sh\ncat <<'EOF'\n{json.dumps(out)}\nEOF\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("MOUTH_SESSION_BIN", str(p))


def said(home, **kw):
    return mouth.say("hello", spawn=False, d=home["d"], **kw)


def played(home, **kw):
    wav = home["tmp"] / "song.wav"
    wav.write_bytes(b"RIFF")  # play() checks the path is a file; nothing decodes it here
    return mouth.play(str(wav), spawn=False, d=home["d"], **kw)


def queued(home) -> list:
    return [box.public(x) for x in box.Queue(home["d"]).lines()]


# -- volume-cascade-default --------------------------------------------------

def test_no_flag_no_setting_is_todays_item_and_the_player_fills_the_builtin(home, monkeypatch):
    """With no flag and no setting the queued line and its mail are today's
    (test_mouth_golden guards the bytes); the player fills in the built-in."""
    line, f = said(home), played(home)
    for item in (line, f):
        assert "volume" not in item and "volume_from" not in item and "duck" not in item
    a, b = taken(home, monkeypatch)
    assert (a["volume"], a["volume_from"]) == (100, "builtin") and "duck" not in a
    assert (b["volume"], b["volume_from"]) == (100, "builtin")
    assert b["duck"] == {"on": True, "level": 20, "restore_s": 1.0, "from": "builtin"}


def test_the_host_file_sets_the_level(home):
    host(home, volume=40)
    item = said(home)
    assert (item["volume"], item["volume_from"]) == (40, "settings")


def test_a_session_layer_beats_the_host_and_a_flag_beats_both(home, monkeypatch):
    host(home, volume=40)
    cascade(home, monkeypatch, "session", volume=70)
    assert (said(home)["volume"], said(home)["volume_from"]) == (70, "session")
    assert (said(home, volume=30)["volume"], said(home, volume=30)["volume_from"]) == (30, "flag")


@pytest.mark.parametrize("layer", ["seat", "agent"])
def test_each_cascade_layer_is_named(home, monkeypatch, layer):
    cascade(home, monkeypatch, layer, volume=55)
    item = said(home)
    assert (item["volume"], item["volume_from"]) == (55, layer)


def test_play_volume_beats_volume_for_a_file_and_is_ignored_for_a_line(home, monkeypatch):
    host(home, volume=40, play_volume=60)
    assert (played(home)["volume"], played(home)["volume_from"]) == (60, "settings")
    assert said(home)["volume"] == 40
    cascade(home, monkeypatch, "seat", play_volume=25)
    assert (played(home)["volume"], played(home)["volume_from"]) == (25, "seat")
    assert said(home)["volume"] == 40


def test_play_volume_unset_follows_volume(home, monkeypatch):
    host(home, volume=40)
    assert (played(home)["volume"], played(home)["volume_from"]) == (40, "settings")
    cascade(home, monkeypatch, "session", volume=70)
    assert (played(home)["volume"], played(home)["volume_from"]) == (70, "session")
    assert played(home, volume=10)["volume_from"] == "flag"


@pytest.mark.parametrize("bad", [150, -1, "loud", 50.5, True, None, [50]])
def test_a_bad_value_in_a_layer_falls_through_and_never_raises(home, monkeypatch, bad):
    host(home, volume=40)
    cascade(home, monkeypatch, "session", volume=bad)
    assert (said(home)["volume"], said(home)["volume_from"]) == (40, "settings")
    host(home, volume=bad)
    assert "volume" not in said(home)                       # the built-in, left to the player
    assert voices.volume_setting(file=False, cascaded=voices.cascade(SID, ("volume",))) == (100, "builtin")


def test_one_session_run_serves_voice_level_and_duck(home, monkeypatch):
    """A say costs ONE `session settings` run, as before EARS-152 (design.md s1)."""
    import subprocess
    cascade(home, monkeypatch, "session", voice="session_voice", volume=70, music_duck=False)
    runs = []
    real = subprocess.run
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: runs.append(a) or real(*a, **kw))
    item = played(home)
    assert len(runs) == 1
    assert item["voice"] == "session_voice" and item["volume"] == 70 and item["duck"]["on"] is False
    runs.clear()
    item = said(home)
    assert len(runs) == 1 and item["voice"] == "session_voice"


def test_the_voice_cascade_still_answers_through_the_wrapper(home, monkeypatch):
    cascade(home, monkeypatch, "seat", voice="seat_voice")
    assert voices.cascade_voice(SID) == ("seat_voice", "seat")
    assert voices.cascade(SID, ("voice", "volume")) == {"voice": ("seat_voice", "seat")}
    assert voices.cascade(None, ("voice",)) == {}


# -- volume-refused ------------------------------------------------------------

@pytest.mark.parametrize("bad", [101, -1, "abc", "", 50.5, True, "1e9"])
def test_a_bad_volume_flag_is_refused_before_anything_is_written(home, bad):
    with pytest.raises(ValueError, match="volume is 0-100"):
        said(home, volume=bad)
    with pytest.raises(ValueError, match="volume is 0-100"):
        played(home, volume=bad)
    assert queued(home) == []


@pytest.mark.parametrize("good,n", [(0, 0), ("30", 30), (100, 100), (60.0, 60), (" 7 ", 7)])
def test_a_good_volume_flag(home, good, n):
    assert said(home, volume=good)["volume"] == n


def test_a_bad_duck_level_is_refused(home):
    with pytest.raises(ValueError, match="duck level is 0-100"):
        played(home, duck_level=101)
    with pytest.raises(ValueError, match="duck is on or off"):
        played(home, duck="yes")
    assert queued(home) == []


@pytest.mark.parametrize("argv", [["say", "--volume", "101", "hi"], ["say", "--volume", "abc", "hi"],
                                  ["play", "SONG", "--volume", "-1"], ["play", "SONG", "--duck-level", "x"]])
def test_the_cli_refuses_a_bad_level(home, capsys, argv):
    song = home["tmp"] / "song.wav"
    song.write_bytes(b"RIFF")
    argv = [str(song) if a == "SONG" else a for a in argv]
    assert cli.main(argv) == 2
    assert "0-100" in capsys.readouterr().err
    assert queued(home) == []


def test_the_cli_flags_reach_the_item(home, monkeypatch):
    monkeypatch.setattr(mouth, "ensure_player", lambda d=None: False)
    song = home["tmp"] / "song.wav"
    song.write_bytes(b"RIFF")
    assert cli.main(["say", "--volume", "30", "hi"]) == 0
    assert cli.main(["play", str(song), "--volume", "40", "--no-duck", "--duck-level", "10"]) == 0
    assert cli.main(["play", str(song), "--duck"]) == 0
    line, f1, f2 = queued(home)
    assert (line["volume"], line["volume_from"]) == (30, "flag") and "duck" not in line
    assert (f1["volume"], f1["volume_from"]) == (40, "flag")
    assert f1["duck"] == {"on": False, "level": 10, "restore_s": 1.0, "from": "flag"}
    assert f2["duck"]["on"] is True and f2["duck"]["from"] == "flag" and f2.get("volume") is None


# -- cascade-duck ----------------------------------------------------------------

def test_a_file_ducks_by_default(home, monkeypatch):
    assert "duck" not in played(home)                       # the built-in: filled when taken
    (x,) = taken(home, monkeypatch)
    assert x["duck"] == {"on": True, "level": 20, "restore_s": 1.0, "from": "builtin"}


def test_any_layer_above_the_builtin_rides_the_whole_duck(home):
    assert played(home, duck_level=30)["duck"] == {"on": True, "level": 30, "restore_s": 1.0,
                                                   "from": "builtin"}
    host(home, music_duck_restore_s=3)
    assert played(home)["duck"] == {"on": True, "level": 20, "restore_s": 3.0, "from": "builtin"}


def test_the_host_file_turns_it_off_and_sets_the_level(home):
    host(home, music_duck=False, music_duck_level=35, music_duck_restore_s=2.5)
    assert played(home)["duck"] == {"on": False, "level": 35, "restore_s": 2.5, "from": "settings"}


def test_a_session_layer_off_beats_the_hosts_on(home, monkeypatch):
    host(home, music_duck=True)
    cascade(home, monkeypatch, "session", music_duck=False, music_duck_level=5)
    assert played(home)["duck"] == {"on": False, "level": 5, "restore_s": 1.0, "from": "session"}
    assert played(home, duck=True)["duck"]["from"] == "flag"


@pytest.mark.parametrize("bad", ["off", 0, None, "true"])
def test_a_bad_duck_setting_falls_through(home, monkeypatch, bad):
    host(home, music_duck=False, music_duck_level=200, music_duck_restore_s=-1)
    cascade(home, monkeypatch, "session", music_duck=bad)
    assert played(home)["duck"] == {"on": False, "level": 20, "restore_s": 1.0, "from": "settings"}


def test_the_duck_and_level_ride_the_mail_and_come_back(home):
    host(home, music_duck=False)
    item = played(home, volume=45)
    back = queued(home)[0]
    assert back["duck"] == item["duck"] and back["volume"] == 45 and back["volume_from"] == "flag"
    raw = next((home["d"] / "box" / "new").iterdir()).read_text()
    assert "X-Mouth-Volume: 45" in raw


def test_amend_keeps_the_level(home):
    item = said(home, volume=30)
    mouth.amend(item["utt"], "changed", d=home["d"])
    (x,) = queued(home)
    assert x["text"] == "changed" and (x["volume"], x["volume_from"]) == (30, "flag")


# -- volume-mail -------------------------------------------------------------------

def deliver(home, n, body="hello", **headers):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = f"vera@{HOST}", f"mouth@{HOST}", "hi"
    m["Message-ID"] = f"<{n}.test@{HOST}.session-mail>"
    for k, v in headers.items():
        m[k.replace("_", "-")] = v
    m.set_content(body)
    bx = box.ensure(box.box_dir(home["d"]))
    put(bx, f"{time.time_ns()}.{n}.{HOST}", bytes(m))


def test_x_mouth_volume_parses(home):
    deliver(home, 1, X_Mouth_Volume="25")
    deliver(home, 2, X_Mouth_Volume="loud")
    deliver(home, 3)
    a, b, c = queued(home)
    assert a["volume"] == 25 and b["volume"] is None and c["volume"] is None


def taken(home, monkeypatch) -> list:
    """Run the player over the queue; return each item as the player handed it
    on: a line to play_one, a played file to its bed (do-003: files are beds)."""
    seen = []

    def fake_play_one(item, d, synth=None, prefetched=False, **kw):   # kw: beds (do-005)
        if synth is not None:
            synth.cancel()
        seen.append(item)
        return {"utt": item["utt"], "reason": "done", "cut": False, "played_s": 0.1}

    def fake_bed(self, item, taken):
        seen.append(item)

    monkeypatch.setattr(player, "play_one", fake_play_one)
    monkeypatch.setattr(player._bed.Beds, "start", fake_bed)
    t = threading.Thread(target=player.serve, kwargs={"d": home["d"], "idle_exit_s": 0.3}, daemon=True)
    t.start()
    t.join(10)
    return seen


def test_a_mail_without_a_level_reads_the_host_when_taken(home, monkeypatch):
    host(home, volume=40, play_volume=70, music_duck=False)
    deliver(home, 1, X_Mouth_Volume="25")
    deliver(home, 2)
    wav = home["tmp"] / "chime.wav"
    wav.write_bytes(b"RIFF")
    monkeypatch.setenv("MOUTH_SOUNDS_DIR", str(home["tmp"]))   # a mail's sound (EARS-335)
    deliver(home, 3, body="", X_Mouth_File=str(wav))
    a, b, c = taken(home, monkeypatch)
    assert (a["volume"], a["volume_from"]) == (25, "mail")
    assert (b["volume"], b["volume_from"]) == (40, "settings") and "duck" not in b
    assert (c["volume"], c["volume_from"]) == (70, "settings")
    assert c["duck"] == {"on": False, "level": 20, "restore_s": 1.0, "from": "settings"}


def test_a_said_line_keeps_what_its_caller_resolved(home, monkeypatch):
    item = said(home, volume=30)
    host(home, volume=90)                       # changed after it was queued
    (x,) = taken(home, monkeypatch)
    assert x["utt"] == item["utt"] and (x["volume"], x["volume_from"]) == (30, "flag")
