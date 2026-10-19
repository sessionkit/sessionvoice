"""EARS-315 do-002: the barge policy's layers, and the source shown.

R7: three layers, highest wins: the built-in default (with
$MOUTH_BARGE_WORDS now IN the default layer, below the user), the
user's setting (``~/.sessionkit/voice/settings/mouth.json``), and the model's own line
(``mouth say --barge-words N|off``, ``--no-barge``). R8: ``mouth status``
names the layer (``from``) and the source (``by``) of each value. Off means
no cut on a word count: his voice still ducks the line, and a stop ask still
cuts it (design.md s3.3, s3.6).

The model's timed override (``mouth barge``) is do-003's; the cut notice's
layer is do-004's. No sound, no live ears, no live bot, no live mouth.
"""
from __future__ import annotations

import json
import math
import time

import pytest

import mouth
from mouth import __main__ as cli
from mouth import box as _box
from mouth import hold, player

from test_mouth import lines, run_player, say  # noqa: F401
from test_mouth_call import bots  # noqa: F401  (fixture)
from test_mouth_call_barge import CALL, LINE, _said, on_call  # noqa: F401  (fixture)
from test_mouth_inbox import box  # noqa: F401  (fixture)
from test_mouth_onset import _printed, _wait, ears  # noqa: F401  (fixture)
from tests import env_fence

ENV = "MOUTH_BARGE_WORDS"
SRC = ("words", "duck_level", "recover", "duck_max")     # the values, by their _from/_by prefix


@pytest.fixture
def clean(monkeypatch):
    env_fence.delenv(monkeypatch, ENV)
    env_fence.delenv(monkeypatch, "MOUTH_BARGE_WORDS_CALL")
    monkeypatch.setenv("MOUTH_SETTINGS", "/nonexistent/mouth.json")


def _src(pol: dict, name: str) -> tuple:
    return pol[name + "_from"], pol[name + "_by"]


# -- the resolver: each layer, and the higher wins --------------------------------

def test_the_default_layer_is_the_built_ins(clean):
    pol = hold.barge_policy(settings={})
    assert (pol["words"], pol["duck_level"], pol["recover_s"], pol["duck_max_s"]) == (2, 25, 2.5, 4.0)
    assert all(_src(pol, n) == ("default", "builtin") for n in SRC)
    assert pol["ignore"] is hold.STOP_WORDS
    assert (pol["ignore_from"], pol["ignore_by"], pol["ignore_added"], pol["ignore_added_by"]) == \
        ("default", "builtin", [], None)
    assert pol["warnings"] == []
    assert hold.barge_policy() == pol                       # no settings file: the same


def test_the_user_layer_sets_n_and_the_duck(clean):
    s = {"barge_words": 3, "barge_duck_level": 40, "barge_duck_recover_s": 1.5,
         "barge_duck_max_s": 6, "barge_ignore_words": ["shell", "uh-huh"]}
    pol = hold.barge_policy(settings=s)
    assert (pol["words"], pol["duck_level"], pol["recover_s"], pol["duck_max_s"]) == (3, 40, 1.5, 6.0)
    assert all(_src(pol, n) == ("user", "setting") for n in SRC)
    assert pol["ignore"] == {"shell", "uh", "huh"}
    assert (pol["ignore_from"], pol["ignore_by"]) == ("user", "setting")
    # the old readers are thin wrappers: they agree with the policy
    assert (hold.barge_words(None, s), hold.barge_duck_level(s), hold.barge_duck_recover_s(s),
            hold.barge_duck_max_s(s)) == (3, 40, 1.5, 6.0)


def test_a_user_value_out_of_range_is_as_if_unset(clean):
    bad = {"barge_words": 0, "barge_duck_level": 101, "barge_duck_recover_s": -1,
           "barge_duck_max_s": "four", "barge_ignore_words": "shell"}
    pol = hold.barge_policy(settings=bad)
    assert (pol["words"], pol["duck_level"], pol["recover_s"], pol["duck_max_s"]) == (2, 25, 2.5, 4.0)
    assert all(_src(pol, n) == ("default", "builtin") for n in SRC)
    assert pol["ignore_from"] == "default"


def test_the_env_is_the_defaults_and_sits_below_the_user(clean, monkeypatch):
    monkeypatch.setenv(ENV, "3")
    pol = hold.barge_policy(settings={})
    assert (pol["words"], *_src(pol, "words")) == (3, "default", "env")
    assert _src(pol, "duck_level") == ("default", "builtin")     # the env is N only
    pol = hold.barge_policy(settings={"barge_words": 4})
    assert (pol["words"], *_src(pol, "words")) == (4, "user", "setting")   # it won until EARS-315


@pytest.mark.parametrize("bad", ["0", "-2", "two", "", "2.5"])
def test_an_env_that_is_not_a_count_is_ignored_and_warned(clean, monkeypatch, bad):
    monkeypatch.setenv(ENV, bad)
    pol = hold.barge_policy(settings={})
    assert (pol["words"], *_src(pol, "words")) == (2, "default", "builtin")   # never 0: every partial
    assert [(w["var"], w["value"]) for w in pol["warnings"]] == [(ENV, bad)]
    assert hold.barge_words() == 2                          # barge()'s own default read never raises


def test_the_line_beats_the_user_and_the_env(clean, monkeypatch):
    monkeypatch.setenv(ENV, "5")
    s = {"barge_words": 3, "barge_duck_level": 40}
    pol = hold.barge_policy("airpods", item={"barge_words": 4, "barge_words_from": "flag"}, settings=s)
    assert (pol["words"], *_src(pol, "words")) == (4, "model", "argument")
    assert (pol["duck_level"], *_src(pol, "duck_level")) == (40, "user", "setting")   # a line sets N only
    pol = hold.barge_policy(CALL, item={"barge_words": 1}, settings=s)
    assert (pol["words"], *_src(pol, "words")) == (1, "model", "argument")   # a call line too


@pytest.mark.parametrize("v", ["off", "OFF", False])
def test_off_is_the_models_and_needs_infinite_words(clean, v):
    pol = hold.barge_policy(item={"barge_words": v}, settings={"barge_words": 3})
    assert (pol["words"], *_src(pol, "words")) == (None, "model", "argument")
    assert hold.need_of(pol) == math.inf
    assert hold.need_of(hold.barge_policy(settings={})) == 2


@pytest.mark.parametrize("v", ["two", 0, -1, True, 2.5, [3], {"n": 3}, float("inf"), float("nan")])
def test_a_line_value_that_is_not_a_count_falls_to_the_next_layer(clean, v):
    pol = hold.barge_policy(item={"barge_words": v}, settings={"barge_words": 3})
    assert (pol["words"], *_src(pol, "words")) == (3, "user", "setting")


def test_the_policy_is_json_but_for_the_ignore_set(clean, monkeypatch):
    monkeypatch.setenv("MOUTH_BARGE_WORDS_CALL", "1")
    pol = hold.barge_policy(item={"barge_words": "off"})
    json.dumps({k: v for k, v in pol.items() if k != "ignore"})
    assert isinstance(pol["ignore"], frozenset)
    assert pol["warnings"] == []                    # the retired CALL variable draws no warning


def test_check_barge_words():
    assert [hold.check_barge_words(v) for v in (None, 1, "3", " 4 ", "off", "Off", False)] == \
        [None, 1, 3, 4, "off", "off", "off"]
    for bad in (0, -1, "two", 2.5, True, ""):
        with pytest.raises(ValueError, match="barge words"):
            hold.check_barge_words(bad)


# -- off: no cut on words; a stop ask still cuts (R14 under --no-barge) -----------

def test_off_never_cuts_on_words_and_a_stop_ask_still_does(ears):  # noqa: F811
    t0 = time.time() - 1
    for p in ("Tuesday", "evening", "platform nine", "the eastern suburbs", "orange juice"):
        ears.partial(p)
    trace: list = []
    assert hold.barge(t0, LINE, words=math.inf, trace=trace) is None
    assert trace and all(e["need"] is None for e in trace)  # null, never Infinity
    json.loads(json.dumps(trace[-1], allow_nan=False))
    assert hold.barge(t0, LINE, words=2) is not None        # the same words cut at N=2
    ears.partial("stop")
    got = hold.barge(t0, LINE, words=math.inf, trace=trace)
    assert got is not None and got["text"] == "stop"
    assert trace[-1]["verdict"] == "stop-ask" and trace[-1]["need"] is None


# -- the surfaces: say(), the CLI, the item key, the resume ----------------------

def test_say_carries_the_lines_own_count(box):  # noqa: F811
    item = say("read the whole document", barge_words=4)
    assert (item["barge_words"], item["barge_words_from"]) == (4, "flag")
    assert say("an alarm", barge_words="off")["barge_words"] == "off"
    plain = say("as today")
    assert "barge_words" not in plain and "barge_words_from" not in plain   # the mail is today's
    assert "barge_words" not in say("a file", backend="file")   # a played file has no barge test


def test_say_refuses_a_bad_count_before_anything_is_written(box):  # noqa: F811
    with pytest.raises(ValueError, match="barge words"):
        say("never queued", barge_words=0)
    assert _box.Queue(box.d).lines() == []


@pytest.mark.parametrize("args,want", [
    (["--barge-words", "3"], "3"),                       # say() checks it (check_barge_words)
    (["--barge-words", "off"], "off"),
    (["--no-barge"], "off"),
    ([], None),
])
def test_the_cli_flags(monkeypatch, capsys, args, want):
    got = {}

    def fake(text, **kw):
        got.update(kw)
        return {"utt": "u1"}
    monkeypatch.setattr(cli, "say", fake)
    assert cli.main(["say", "hello", "--device", "null", *args]) == 0
    assert got["barge_words"] == want


def test_the_cli_refuses_both_flags_and_a_bad_count(box, capsys):  # noqa: F811
    with pytest.raises(SystemExit):
        cli.main(["say", "hi", "--barge-words", "3", "--no-barge"])
    assert cli.main(["say", "hi", "--barge-words", "lots"]) == 2
    assert "barge words" in capsys.readouterr().err
    assert _box.Queue(box.d).lines() == []


def test_a_mail_extra_carries_the_count(box, clean):  # noqa: F811
    """By mail the item key rides X-Mouth-Extra (box.py carries any key)."""
    item = say("by mail", barge_words=3)
    taken = [e for e in _box.Queue(box.d).lines() if e["utt"] == item["utt"]]
    assert taken and (taken[0]["barge_words"], taken[0]["barge_words_from"]) == (3, "flag")
    pol = hold.barge_policy(taken[0]["device"], item=taken[0], settings={"barge_words": 2})
    assert (pol["words"], pol["words_from"]) == (3, "model")


def test_a_resume_carries_its_cut_lines_count(box, monkeypatch):  # noqa: F811
    monkeypatch.setenv("MOUTH_RESUME_S", "20")
    item = say("The first sentence is heard. The second one is long and he cut it.",
               barge_words="off")
    rec = {"resume": "will", "resume_utt": "a1b2c3d4e5f6", "resume_s": 20.0,
           "text_played_est": "The first sentence is heard. The sec"}
    player._resume(box.d, item, rec)
    queued = [e for e in _box.Queue(box.d).lines() if e["utt"] == "a1b2c3d4e5f6"]
    assert queued, "the resume was not queued"
    r = queued[0]
    assert (r["resume_of"], r["barge_words"], r["barge_words_from"]) == (item["utt"], "off", "flag")
    plain = say("Another sentence. And its rest.")
    player._resume(box.d, plain, {**rec, "resume_utt": "f6e5d4c3b2a1", "text_played_est": "Another sen"})
    r = next(e for e in _box.Queue(box.d).lines() if e["utt"] == "f6e5d4c3b2a1")
    assert "barge_words" not in r                           # none given, none carried


# -- the player reads the policy once a line ----------------------------------------

def test_a_lines_own_count_beats_the_users_on_a_call(on_call):  # noqa: F811
    on_call.ears.settings.write_text(json.dumps({"barge_words": 2}))
    on_call.tone(4.0)
    item = say(LINE, device=CALL, barge_words=3)
    th = run_player(on_call.d)
    assert _wait(lambda: on_call.ducks)
    time.sleep(0.2)
    on_call.ears.vad(source="call")
    on_call.ears.partial("Tuesday evening", source="call")    # 2: the user's N, not the line's
    time.sleep(0.6)
    assert th.is_alive() and not [r for r in lines(on_call.logs) if r["kind"] == "said"]
    on_call.ears.partial("Tuesday evening, at nine", source="call")
    th.join(10)
    assert not th.is_alive()
    said = _said(on_call, item)
    assert (said["reason"], said["cut"]) == ("barge-in", True)


def test_no_barge_still_ducks_and_a_stop_ask_still_cuts(on_call, capsys):  # noqa: F811
    on_call.tone(3.0)
    item = say(LINE, device=CALL, barge_words="off")
    th = run_player(on_call.d)
    assert _wait(lambda: on_call.ducks and on_call.ducks[0] is not None)
    bd = on_call.ducks[0]
    time.sleep(0.2)
    on_call.ears.vad(source="call")
    assert _wait(lambda: bd.duck.trace, 1.0)                # his voice still dips it
    for p in ("Tuesday", "Tuesday evening", "Tuesday evening at nine, platform four"):
        on_call.ears.partial(p, source="call")
        time.sleep(0.15)
    time.sleep(0.3)
    assert th.is_alive() and not [r for r in lines(on_call.logs) if r["kind"] == "said"]
    on_call.ears.partial("stop", source="call")
    th.join(10)
    assert not th.is_alive()
    said = _said(on_call, item)
    assert (said["reason"], said["cut"]) == ("barge-in", True) and said["played_s"] < 2.5
    recs = _printed(capsys.readouterr().out, "barge")
    assert recs and all(r["need"] is None for r in recs)    # need: null on player.log
    assert recs[-1]["verdict"] == "stop-ask"


def test_the_users_duck_reaches_the_line_through_the_policy(on_call, monkeypatch):  # noqa: F811
    on_call.ears.settings.write_text(json.dumps({"barge_duck_level": 40, "barge_duck_recover_s": 0.7}))
    reads = []
    real = hold.barge_policy

    def spy(*a, **kw):
        reads.append(kw.get("item", {}).get("utt") if kw.get("item") else None)
        return real(*a, **kw)
    monkeypatch.setattr(hold, "barge_policy", spy)
    on_call.tone(1.0)
    item = say(LINE, device=CALL)
    th = run_player(on_call.d)
    th.join(10)
    assert not th.is_alive()
    bd = on_call.ducks[0]
    assert (bd.level, bd.recover_s) == (0.4, 0.7)
    assert reads.count(item["utt"]) == 1                    # once a line, handed to for_line


# -- R8: mouth status names the layer and the source -----------------------------------

def test_status_names_the_layer_and_the_source_of_each_value(box, clean, monkeypatch, tmp_path):  # noqa: F811
    st = mouth.status(box.d)["barge"]
    assert "ignore" not in st                               # JSON: the list is named, not dumped
    json.dumps(st)
    assert all(_src(st, n) == ("default", "builtin") for n in SRC)
    assert (st["words"], st["duck_level"], st["recover_s"], st["duck_max_s"]) == (2, 25, 2.5, 4.0)
    monkeypatch.setenv(ENV, "3")
    st = mouth.status(box.d)["barge"]
    assert (st["words"], *_src(st, "words")) == (3, "default", "env")
    s = tmp_path / "mouth.json"
    s.write_text(json.dumps({"barge_words": 4, "barge_duck_level": 30, "barge_ignore_words": ["shell"]}))
    monkeypatch.setenv("MOUTH_SETTINGS", str(s))
    st = mouth.status(box.d)["barge"]
    assert (st["words"], *_src(st, "words")) == (4, "user", "setting")
    assert (st["duck_level"], *_src(st, "duck_level")) == (30, "user", "setting")
    assert (st["recover_s"], *_src(st, "recover")) == (2.5, "default", "builtin")
    assert (st["ignore_from"], st["ignore_by"]) == ("user", "setting")


def test_status_shows_a_playing_lines_own_count(box, clean):  # noqa: F811
    box.d.mkdir(parents=True, exist_ok=True)
    (box.d / "playing.json").write_text(json.dumps({"utt": "u1", "text": "x", "barge_words": 3,
                                                    "barge_words_from": "flag"}))
    assert mouth.status(box.d)["playing"] == {"utt": "u1", "text": "x", "barge_words": 3,
                                              "barge_words_from": "model"}
    (box.d / "playing.json").write_text(json.dumps({"utt": "u2", "text": "y", "barge_words": "off"}))
    p = mouth.status(box.d)["playing"]
    assert (p["barge_words"], p["barge_words_from"]) == ("off", "model")
    (box.d / "playing.json").write_text(json.dumps({"utt": "u3", "text": "z"}))
    assert mouth.status(box.d)["playing"] == {"utt": "u3", "text": "z"}   # none: as today
