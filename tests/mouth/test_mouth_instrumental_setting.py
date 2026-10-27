"""The instrumental setting and its flags: whether a played file plays its
instrumental (design.md section 2).

``music_instrumental`` in ``mouth.json`` (boolean, on by default), the caller's
``$MOUTH_MUSIC_INSTRUMENTAL``, and ``mouth play --instrumental`` /
``--no-instrumental`` (``mouth.play(instrumental=)``). The order, highest first:

    the flag > $MOUTH_MUSIC_INSTRUMENTAL > session > seat > agent > the host file > on

The env is read by the caller, at call time, as ``$MOUTH_ROOM_HOLD`` is: ``off``,
``0``, ``no``, ``false`` turn it off, any other non-empty value turns it on, and
empty counts as unset. A value that is not valid in a layer is skipped and the
next layer answers; a bad flag is refused before anything is written.

The answer rides the line (``instrumental``, ``instrumental_from``) only when a
layer above the built-in gave it, so a plain ``mouth play`` writes today's mail,
byte for byte (``golden_play.json``, captured before this change). The player
fills in the host file, then the built-in, when it takes a line that carries none
(``voices.fill_item``); the player's own environment never answers there.

What plays (the swap) is not decided here: nothing in this file changes playback.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from test_mouth_golden import _mail, _new, pinned  # noqa: F401  (fixture)

import mouth
from mouth import __main__ as cli
from mouth import box, voices
from tests import env_fence

SID = "test-sid-instrumental"
ENV = "MOUTH_MUSIC_INSTRUMENTAL"
GOLDEN = Path(__file__).parent / "fixtures" / "golden_play.json"


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
    env_fence.delenv(monkeypatch, ENV)
    song = tmp_path / "song.wav"
    song.write_bytes(b"RIFF")   # play() checks the path is a file; nothing decodes it here
    # ...and it has an instrumental, so an --instrumental play is met with no separator:
    # the caller refuses one that would need a split it cannot start (autosplit). The
    # split decision itself is test_mouth_split_decision.py's.
    mixes = tmp_path / ".song.wav.d" / "mixes"
    mixes.mkdir(parents=True)
    (mixes / "instrumental.flac").write_bytes(b"fLaC")
    return {"d": d, "host": host, "tmp": tmp_path, "song": song}


def host(home, **kw):
    home["host"].write_text(json.dumps(kw))


def cascade(home, monkeypatch, layer="session", **values):
    """A fake ``session settings --json``: ``values`` set in ``layer``. sessiontools
    merges the layers; the mouth sees one value per key and the layer it came from."""
    path = f"/fake/{layer}/mouth.json"
    out = {"tools": {"mouth": {
        "values": values, "origins": {k: path for k in values},
        "sources": [{"layer": layer, "path": path}, {"layer": "host", "path": str(home["host"])}]}}}
    p = home["tmp"] / f"fake-session-{layer}"
    p.write_text(f"#!/bin/sh\ncat <<'EOF'\n{json.dumps(out)}\nEOF\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("MOUTH_SESSION_BIN", str(p))


def played(home, **kw) -> dict:
    return mouth.play(str(home["song"]), spawn=False, d=home["d"], **kw)


def queued(home) -> list:
    return [box.public(x) for x in box.Queue(home["d"]).lines()]


def answer(item: dict) -> tuple:
    """What rode on the line: ``(instrumental, instrumental_from)``, or None."""
    if "instrumental" not in item and "instrumental_from" not in item:
        return None
    return item["instrumental"], item["instrumental_from"]


def taken(home, item: dict) -> tuple:
    """What the player resolves for ITEM as it takes it: its mail read back, filled."""
    (x,) = [x for x in box.Queue(home["d"]).lines() if x["utt"] == item["utt"]]
    f = voices.fill_item(box.public(x), home["d"])
    return f["instrumental"], f["instrumental_from"]


# -- the order: flag > env > session > seat > agent > file > built-in, each both ways ---

LAYERS = ("flag", "env", "session", "seat", "agent", "settings")


def _stack(home, monkeypatch, top: str, value: bool) -> dict:
    """Set ``top`` to VALUE and every layer under it to the opposite; return the
    keyword arguments for play(). Layers above ``top`` say nothing."""
    other = not value
    below = LAYERS[LAYERS.index(top) + 1:]
    kw = {}
    if "settings" in below or top == "settings":
        host(home, music_instrumental=value if top == "settings" else other)
    cascaded = [x for x in ("session", "seat", "agent") if x == top or x in below]
    if cascaded:
        # the merge is sessiontools': only the highest cascade layer that sets it is seen
        layer = cascaded[0]
        cascade(home, monkeypatch, layer, music_instrumental=value if layer == top else other)
    if top == "env":
        monkeypatch.setenv(ENV, "on" if value else "off")
    if top == "flag":
        monkeypatch.setenv(ENV, "off" if value else "on")
        kw["instrumental"] = value
    return kw


@pytest.mark.parametrize("value", [True, False])
@pytest.mark.parametrize("top", LAYERS)
def test_each_layer_beats_every_layer_under_it_both_ways(home, monkeypatch, top, value):
    kw = _stack(home, monkeypatch, top, value)
    item = played(home, **kw)
    assert answer(item) == (value, top)
    assert taken(home, item) == (value, top)   # what rode is what the player takes
    assert voices.instrumental_setting(
        flag=kw.get("instrumental"), cascaded=voices.cascade(SID, voices.LINE_KEYS),
        d=home["d"]) == (value, top)


def test_the_built_in_is_on_and_is_left_to_the_player(home):
    item = played(home)
    assert answer(item) is None
    assert taken(home, item) == (True, "builtin")
    assert voices.instrumental_setting(d=home["d"]) == (True, "builtin")


@pytest.mark.parametrize("value", [True, False])
@pytest.mark.parametrize("layer", ["session", "seat", "agent"])
def test_each_cascade_layer_is_named_and_beats_the_file(home, monkeypatch, layer, value):
    host(home, music_instrumental=not value)
    cascade(home, monkeypatch, layer, music_instrumental=value)
    assert answer(played(home)) == (value, layer)


@pytest.mark.parametrize("value", [True, False])
def test_the_flag_beats_the_env_and_the_env_beats_the_cascade(home, monkeypatch, value):
    host(home, music_instrumental=not value)
    cascade(home, monkeypatch, "session", music_instrumental=not value)
    monkeypatch.setenv(ENV, "on" if value else "off")
    assert answer(played(home)) == (value, "env")
    assert answer(played(home, instrumental=not value)) == (not value, "flag")


# -- the env: read like $MOUTH_ROOM_HOLD, by the caller, at call time -------------

@pytest.mark.parametrize("word", ["off", "0", "no", "false", "OFF", " False ", "No"])
def test_an_off_word_turns_it_off(home, monkeypatch, word):
    host(home, music_instrumental=True)
    monkeypatch.setenv(ENV, word)
    assert answer(played(home)) == (False, "env")


@pytest.mark.parametrize("word", ["on", "1", "yes", "true", "anything", " ON "])
def test_any_other_word_turns_it_on(home, monkeypatch, word):
    host(home, music_instrumental=False)
    monkeypatch.setenv(ENV, word)
    assert answer(played(home)) == (True, "env")


@pytest.mark.parametrize("empty", ["", "   "])
def test_an_empty_env_is_as_if_unset(home, monkeypatch, empty):
    host(home, music_instrumental=False)
    monkeypatch.setenv(ENV, empty)
    assert answer(played(home)) == (False, "settings")


def test_the_env_is_read_at_call_time(home, monkeypatch):
    monkeypatch.setenv(ENV, "off")
    assert answer(played(home)) == (False, "env")
    monkeypatch.setenv(ENV, "on")
    assert answer(played(home)) == (True, "env")
    monkeypatch.delenv(ENV)
    assert answer(played(home)) is None


def test_the_players_own_env_never_answers(home, monkeypatch):
    """A line that carries none is filled from the host file, then the built-in:
    the environment the player inherited is some seat's, not the line's caller's."""
    item = played(home)
    monkeypatch.setenv(ENV, "off")   # as the player would see it, when it takes the line
    assert taken(home, item) == (True, "builtin")
    host(home, music_instrumental=False)
    assert taken(home, item) == (False, "settings")


def test_a_resume_reads_no_env_and_keeps_what_its_line_carried(home, monkeypatch):
    """A resume is the player's own: its env is not the caller's. It keeps what its
    first line rode with (an extra), or carries none and is filled at take."""
    monkeypatch.setenv(ENV, "off")
    a = played(home, extra_tag={"resume_of": "abc123abc123"})
    assert answer(a) is None
    b = played(home, extra_tag={"resume_of": "abc123abc123", "instrumental": False,
                                "instrumental_from": "session"})
    assert answer(b) == (False, "session")


# -- bad values are skipped; a bad flag is refused --------------------------------

@pytest.mark.parametrize("bad", ["yes", "true", 1, 0, None, [True], {"on": True}])
def test_a_bad_value_in_a_layer_falls_through_and_never_raises(home, monkeypatch, bad):
    host(home, music_instrumental=False)
    cascade(home, monkeypatch, "session", music_instrumental=bad)
    assert answer(played(home)) == (False, "settings")
    host(home, music_instrumental=bad)
    item = played(home)
    assert answer(item) is None                    # the built-in, left to the player
    assert taken(home, item) == (True, "builtin")


@pytest.mark.parametrize("bad", ["yes", 1, 0, "off", [True]])
def test_a_bad_flag_is_refused_before_anything_is_written(home, bad):
    with pytest.raises(ValueError, match="instrumental is on or off"):
        played(home, instrumental=bad)
    assert queued(home) == []


def test_a_spoken_line_takes_no_instrumental(home, monkeypatch):
    """It is a played file's setting: a spoken line never carries it, and asking a
    spoken line for one is refused."""
    monkeypatch.setenv(ENV, "off")
    cascade(home, monkeypatch, "session", music_instrumental=False)
    line = mouth.say("hello", spawn=False, d=home["d"])
    assert answer(line) is None
    with pytest.raises(ValueError, match="instrumental is for a played file"):
        mouth.say("hello", spawn=False, d=home["d"], instrumental=True)


# -- what rides, and what the player fills ---------------------------------------

def test_a_layer_above_the_built_in_rides_the_mail(home, monkeypatch):
    host(home, music_instrumental=False)
    item = played(home)
    assert answer(item) == (False, "settings")
    (f,) = (box.box_dir(home["d"]) / "new").iterdir()
    extra = json.loads(dict(_mail(f)["headers"])["X-Mouth-Extra"])
    assert (extra["instrumental"], extra["instrumental_from"]) == (False, "settings")


def test_one_session_run_serves_the_voice_the_level_the_duck_and_the_instrumental(home, monkeypatch):
    import subprocess
    cascade(home, monkeypatch, "session", voice="session_voice", volume=70, music_duck=False,
            music_instrumental=False)
    runs = []
    real = subprocess.run
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: runs.append(a) or real(*a, **kw))
    item = played(home)
    assert len(runs) == 1
    assert item["voice"] == "session_voice" and item["duck"]["on"] is False
    assert answer(item) == (False, "session")


PLAYED = {"utt": "f1", "text": "[sound a.wav]", "backend": "file", "file": "/x/a.wav"}


@pytest.mark.parametrize("src", ["flag", "env", "session", "seat", "agent", "settings", "mail"])
def test_fill_item_keeps_a_good_answer_that_rode(home, src):
    host(home, music_instrumental=True)
    f = voices.fill_item({**PLAYED, "instrumental": False, "instrumental_from": src}, home["d"])
    assert (f["instrumental"], f["instrumental_from"]) == (False, src)


@pytest.mark.parametrize("src", [None, "", "builtin", "nonsense", 3])
def test_a_bool_that_rode_with_no_good_layer_is_the_mails_own(home, src):
    """Like a mail's X-Mouth-Volume alone: an ``instrumental`` in a mail's extras with
    no layer that a caller would name is the line's own ask, ``mail``."""
    item = {**PLAYED, "instrumental": False}
    if src is not None:
        item["instrumental_from"] = src
    f = voices.fill_item(item, home["d"])
    assert (f["instrumental"], f["instrumental_from"]) == (False, "mail")


@pytest.mark.parametrize("bad", ["off", 0, 1, None, [False]])
def test_a_bad_value_that_rode_is_replaced_by_the_host_then_the_built_in(home, bad):
    f = voices.fill_item({**PLAYED, "instrumental": bad, "instrumental_from": "flag"}, home["d"])
    assert (f["instrumental"], f["instrumental_from"]) == (True, "builtin")
    host(home, music_instrumental=False)
    f = voices.fill_item({**PLAYED, "instrumental": bad, "instrumental_from": "flag"}, home["d"])
    assert (f["instrumental"], f["instrumental_from"]) == (False, "settings")


def test_fill_item_leaves_a_spoken_line_and_a_replay_alone(home):
    host(home, music_instrumental=False)
    line = voices.fill_item({"utt": "l1", "text": "hi", "backend": "kokoro"}, home["d"])
    replay = voices.fill_item({**PLAYED, "again_of": "l0"}, home["d"])
    assert "instrumental" not in line and "instrumental" not in replay


# -- the CLI: --instrumental / --no-instrumental ----------------------------------

def test_the_cli_flags_reach_the_item(home, monkeypatch):
    monkeypatch.setattr(mouth, "ensure_player", lambda d=None: False)
    monkeypatch.setenv(ENV, "off")
    assert cli.main(["play", str(home["song"]), "--instrumental"]) == 0
    assert cli.main(["play", str(home["song"]), "--no-instrumental"]) == 0
    monkeypatch.setenv(ENV, "on")
    assert cli.main(["play", str(home["song"]), "--no-instrumental"]) == 0
    assert cli.main(["play", str(home["song"])]) == 0
    got = sorted((x["requested_t"], answer(x)) for x in queued(home))
    assert [a for _, a in got] == [(True, "flag"), (False, "flag"), (False, "flag"), (True, "env")]


def test_the_two_flags_are_exclusive(home, capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["play", str(home["song"]), "--instrumental", "--no-instrumental"])
    assert e.value.code == 2
    assert "not allowed with" in capsys.readouterr().err
    assert queued(home) == []


def test_say_has_no_instrumental_flag(home, capsys):
    with pytest.raises(SystemExit):
        cli.main(["say", "--instrumental", "hi"])
    capsys.readouterr()


# -- a plain play's mail, byte for byte as before -----------------------------------

def _play_cases(d: Path, song: Path) -> dict:
    out = {}
    mouth.play(str(song), d=d, spawn=False)
    (f,) = _new(d)
    out["play"] = _mail(f)
    f.unlink()
    mouth.play(str(song), start=1.5, end=4, volume=30, duck=False, duck_level=10, pan="right",
               priority="next", d=d, spawn=False)
    (f,) = _new(d)
    out["play_options"] = _mail(f)
    return out


def _neutral(cases: dict, tmp: Path) -> dict:
    """The song's tmp path, which differs every run, as ``<TMP>``."""
    text = json.dumps(cases)
    for p in sorted({str(tmp.resolve()), str(tmp)}, key=len, reverse=True):
        text = text.replace(p, "<TMP>")
    return json.loads(text)


def test_golden_a_plain_play_is_the_mail_it_was(pinned, tmp_path):
    """Captured at the base commit, before the setting existed, with this test's
    capture switch (``MOUTH_GOLDEN_CAPTURE=1``). Never re-capture to make it pass."""
    song = tmp_path / "golden-song.wav"
    song.write_bytes(b"RIFF")
    got = _neutral(_play_cases(pinned.d, song), tmp_path)
    if os.environ.get("MOUTH_GOLDEN_CAPTURE") == "1":
        GOLDEN.write_text(json.dumps(got, indent=1, ensure_ascii=False) + "\n")
        pytest.skip(f"captured {GOLDEN.name}")
    want = json.loads(GOLDEN.read_text())
    assert set(got) == set(want)
    for case in want:
        assert got[case]["headers"] == want[case]["headers"], f"{case}: the header list changed"
        assert got[case]["body"] == want[case]["body"], f"{case}: the body changed"


def test_the_schema_offers_the_built_in(home):
    schema = json.loads((Path(__file__).resolve().parents[2] / "settings" / "mouth.schema.json").read_text())
    p = schema["properties"]["music_instrumental"]
    assert p["type"] == "boolean" and p["title"] and p["description"]
    assert p["default"] is voices.BUILTIN_INSTRUMENTAL["music_instrumental"]
    assert voices.instrumental_setting(d=home["d"]) == (p["default"], "builtin")
