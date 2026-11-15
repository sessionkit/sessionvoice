"""The settings a ``mouth play`` of a remote URL reads (``voices.play_url_settings``).

Four keys in ``mouth.json``:

- ``play_remote`` and ``play_cache`` (boolean, on): the caller's cascade (session,
  seat, agent), then the host file, then on. A host lock (``_locked`` in the host
  file, or the lock the cascade reports) makes the host file answer over the layers,
  so a locked-down machine cannot be beaten from below. No env var and no flag.
- ``play_cache_dir`` (absolute or ``~/...``; else ``<mouth dir>/play-cache``) and
  ``play_cache_max_age_s`` (seconds over 0; else None, kept forever): the host file
  alone, whatever the layers say.

A bad value is skipped and the next layer answers; nothing raises. The cascade is a
fake ``session settings --json`` (``MOUTH_SESSION_BIN``), as in
``test_mouth_instrumental_setting.py``: nothing here reads this machine's seats.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from mouth import fetch, voices

SID = "test-sid-play-url"


@pytest.fixture
def home(tmp_path, monkeypatch):
    d = tmp_path / "mouth"
    d.mkdir()
    host = tmp_path / "mouth.json"
    monkeypatch.setenv("MOUTH_DIR", str(d))
    monkeypatch.setenv("MOUTH_SETTINGS", str(host))
    monkeypatch.setenv("MOUTH_SESSION_BIN", "")   # the cascade off unless a test fakes it
    return {"d": d, "host": host, "tmp": tmp_path}


def host(home, **kw):
    home["host"].write_text(json.dumps(kw))


def fake_session(home, monkeypatch, layer="session", locked=(), **values):
    """A fake ``session settings --json``: ``values`` set in ``layer``; each key in
    ``locked`` locked by the host layer (sessiontools' ``locked``: key -> the path of
    the layer that locks it)."""
    path = f"/fake/{layer}/mouth.json"
    hpath = str(home["host"])
    out = {"tools": {"mouth": {
        "values": values, "origins": {k: path for k in values},
        "locked": {k: hpath for k in locked},
        "sources": [{"layer": layer, "path": path}, {"layer": "host", "path": hpath}]}}}
    p = home["tmp"] / f"fake-session-{layer}"
    p.write_text(f"#!/bin/sh\ncat <<'EOF'\n{json.dumps(out)}\nEOF\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("MOUTH_SESSION_BIN", str(p))


def read(home) -> dict:
    """What a remote play reads: ONE cascade run for the two keys, then the reader."""
    return voices.play_url_settings(cascaded=voices.cascade(SID, voices.PLAY_URL_KEYS),
                                    d=home["d"])


# -- the built-ins ------------------------------------------------------------------------

def test_nothing_set_is_remote_on_cache_on_the_default_dir_kept_forever(home):
    got = read(home)
    assert got == {"remote": (True, "builtin"), "cache": (True, "builtin"),
                   "dir": home["d"] / "play-cache", "max_age_s": None}


def test_the_default_dir_is_fetchs_cache_dir_under_the_mouth_dir(home):
    assert read(home)["dir"] == fetch.cache_dir(home["d"])


def test_with_no_d_the_default_dir_is_under_mouth_dir(home):
    """``play()`` passes its ``d``; with none, ``$MOUTH_DIR`` is the base."""
    assert voices.play_url_settings()["dir"] == home["d"] / "play-cache"


def test_no_cascade_given_is_the_host_file_then_the_built_in(home):
    host(home, play_remote=False)
    assert voices.play_url_settings(d=home["d"])["remote"] == (False, "settings")


# -- play_remote and play_cache: each layer answers ---------------------------------------

@pytest.mark.parametrize("key, name", [("play_remote", "remote"), ("play_cache", "cache")])
@pytest.mark.parametrize("layer", ["session", "seat", "agent"])
def test_each_cascade_layer_answers_over_the_host_file(home, monkeypatch, key, name, layer):
    host(home, **{key: True})
    fake_session(home, monkeypatch, layer, **{key: False})
    assert read(home)[name] == (False, layer)


@pytest.mark.parametrize("key, name", [("play_remote", "remote"), ("play_cache", "cache")])
def test_the_host_file_answers_when_no_layer_sets_it(home, monkeypatch, key, name):
    host(home, **{key: False})
    fake_session(home, monkeypatch, "session", volume=40)    # a layer that sets another key
    assert read(home)[name] == (False, "settings")


def test_the_two_switches_answer_each_on_its_own(home, monkeypatch):
    host(home, play_cache=False)
    fake_session(home, monkeypatch, "seat", play_remote=False)
    got = read(home)
    assert got["remote"] == (False, "seat") and got["cache"] == (False, "settings")


@pytest.mark.parametrize("bad", ["off", "false", 0, 1, None, [], {}])
@pytest.mark.parametrize("key, name", [("play_remote", "remote"), ("play_cache", "cache")])
def test_a_bad_value_in_a_layer_is_skipped_and_the_host_file_answers(home, monkeypatch, key, name, bad):
    host(home, **{key: False})
    fake_session(home, monkeypatch, "session", **{key: bad})
    assert read(home)[name] == (False, "settings")


@pytest.mark.parametrize("bad", ["off", 0, None, "no"])
def test_a_bad_value_in_the_host_file_is_skipped_and_on_answers(home, bad):
    host(home, play_remote=bad, play_cache=bad)
    got = read(home)
    assert got["remote"] == (True, "builtin") and got["cache"] == (True, "builtin")


def test_an_unreadable_host_file_is_the_built_in(home):
    home["host"].write_text("{not json")
    assert read(home)["remote"] == (True, "builtin")


# -- the host lock: a locked-down machine wins ---------------------------------------------

@pytest.mark.parametrize("key, name", [("play_remote", "remote"), ("play_cache", "cache")])
def test_a_lock_in_the_host_file_beats_a_session_true(home, monkeypatch, key, name):
    host(home, **{key: False, "_locked": [key]})
    fake_session(home, monkeypatch, "session", **{key: True})
    assert read(home)[name] == (False, "settings")


@pytest.mark.parametrize("key, name", [("play_remote", "remote"), ("play_cache", "cache")])
def test_a_lock_the_cascade_reports_beats_a_session_true(home, monkeypatch, key, name):
    """sessiontools names the host layer as the locker; the host file's own ``_locked``
    may not say it (a template lock, or a lock written elsewhere in the host layer)."""
    host(home, **{key: False})
    fake_session(home, monkeypatch, "session", locked=[key], **{key: True})
    assert read(home)[name] == (False, "settings")


def test_a_locked_key_the_host_file_does_not_set_is_the_built_in(home, monkeypatch):
    host(home, _locked=["play_cache"])
    fake_session(home, monkeypatch, "agent", play_cache=False)
    assert read(home)["cache"] == (True, "builtin")


def test_a_lock_on_one_key_leaves_the_other_to_the_layers(home, monkeypatch):
    host(home, play_remote=False, play_cache=True, _locked=["play_remote"])
    fake_session(home, monkeypatch, "session", play_remote=True, play_cache=False)
    got = read(home)
    assert got["remote"] == (False, "settings") and got["cache"] == (False, "session")


@pytest.mark.parametrize("locks", ["play_remote", {"play_remote": True}, ["play_remote", 7], 1])
def test_a_malformed_lock_is_no_lock(home, monkeypatch, locks):
    """``host_locks``: only a list of strings locks (sessiontools refuses other shapes)."""
    host(home, play_remote=False, _locked=locks)
    fake_session(home, monkeypatch, "session", play_remote=True)
    assert read(home)["remote"] == (True, "session")


def test_a_lock_on_voice_does_not_lock_play_remote(home, monkeypatch):
    host(home, play_remote=False, _locked=["voice"])
    fake_session(home, monkeypatch, "session", play_remote=True)
    assert read(home)["remote"] == (True, "session")


# -- no env var, no flag (need 5) ---------------------------------------------------------

@pytest.mark.parametrize("var", ["MOUTH_PLAY_REMOTE", "MOUTH_PLAY_CACHE", "MOUTH_PLAY_CACHE_DIR",
                                 "MOUTH_PLAY_CACHE_MAX_AGE_S"])
def test_no_environment_variable_answers(home, monkeypatch, var):
    host(home, play_remote=False, play_cache=False, _locked=["play_remote"])
    monkeypatch.setenv(var, "on")
    got = read(home)
    assert got["remote"] == (False, "settings") and got["cache"] == (False, "settings")
    assert got["dir"] == home["d"] / "play-cache" and got["max_age_s"] is None


def test_the_reader_takes_no_flag():
    import inspect

    assert set(inspect.signature(voices.play_url_settings).parameters) == {"cascaded", "d"}


# -- play_cache_dir: the host file only ----------------------------------------------------

def test_an_absolute_dir_in_the_host_file_answers(home, tmp_path):
    host(home, play_cache_dir=str(tmp_path / "elsewhere"))
    assert read(home)["dir"] == tmp_path / "elsewhere"


def test_a_home_relative_dir_is_under_home(home, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    host(home, play_cache_dir="~/cache/plays")
    assert read(home)["dir"] == tmp_path / "h" / "cache" / "plays"


@pytest.mark.parametrize("bad", ["relative/dir", "", "   ", "~other/x", 7, True, None, ["/a"]])
def test_a_bad_dir_is_skipped_and_the_default_answers(home, bad):
    host(home, play_cache_dir=bad)
    assert read(home)["dir"] == home["d"] / "play-cache"


def test_the_layers_never_move_the_dir(home, monkeypatch, tmp_path):
    """Every caller on the host shares one cache: a session cannot split it."""
    fake_session(home, monkeypatch, "session", play_cache_dir=str(tmp_path / "mine"))
    assert read(home)["dir"] == home["d"] / "play-cache"
    assert "play_cache_dir" not in voices.cascade(SID, voices.PLAY_URL_KEYS)


# -- play_cache_max_age_s: the host file only ----------------------------------------------

@pytest.mark.parametrize("v, want", [(60, 60.0), (0.5, 0.5), (86400 * 30, 2592000.0)])
def test_a_positive_age_answers_in_seconds(home, v, want):
    host(home, play_cache_max_age_s=v)
    assert read(home)["max_age_s"] == want


@pytest.mark.parametrize("bad", [0, -1, -0.5, None, True, False, "60", float("inf"), [60]])
def test_a_bad_or_absent_age_is_kept_forever(home, bad):
    """0 is not "always re-fetch": ``play_cache: false`` is that. inf is written as
    JSON's ``Infinity``, which Python reads; it is not finite, so it is skipped."""
    host(home, play_cache_max_age_s=bad)
    assert read(home)["max_age_s"] is None


def test_the_layers_never_set_the_age(home, monkeypatch):
    fake_session(home, monkeypatch, "seat", play_cache_max_age_s=5)
    assert read(home)["max_age_s"] is None


# -- the keys, the schema and the line ----------------------------------------------------

def test_the_two_layered_keys_are_not_line_keys():
    """A path or file:// play reads none of them: they are not in a line's cascade run."""
    assert set(voices.PLAY_URL_KEYS) == {"play_remote", "play_cache"}
    assert not set(voices.PLAY_URL_KEYS) & set(voices.LINE_KEYS)


def test_the_schema_declares_the_four_keys_with_the_codes_defaults():
    schema = json.loads((Path(__file__).resolve().parents[2] / "settings" /
                         "mouth.schema.json").read_text())["properties"]
    for key, on in voices.BUILTIN_PLAY.items():
        assert schema[key]["type"] == "boolean" and schema[key]["default"] is on, key
    assert schema["play_cache_dir"]["type"] == "string" and "default" not in schema["play_cache_dir"]
    age = schema["play_cache_max_age_s"]
    assert age["type"] == ["number", "null"] and age["exclusiveMinimum"] == 0
    assert age["default"] is None
