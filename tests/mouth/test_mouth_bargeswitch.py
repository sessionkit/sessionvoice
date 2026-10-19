"""EARS-315 do-003: ``mouth barge``, the model's timed override (design.md s3.3).

R7 (timed): ``mouth barge N|off [--duck L|off] [--duck-max S] [--ignore W,..]
[--for T]`` sets the model layer for a while, in duckswitch's shape:
``barge.json`` by rename, expiry worked out by the reader, ``parse_for``, a
readout, and the PRECEDENCE line said once. It beats the user and the default;
a line's own ``--barge-words`` beats it (N only). R10: ``--ignore`` only ADDS.
Q12 (the foreman): ``--duck-max`` is the cap's model layer. R8: ``mouth status``
shows the override and its ``remaining_s``.

No sound, no live ears, no live bot, no live mouth: a temp mouth dir and a fake
``bargeswitch.clock``; the two player tests run a fake call (test_mouth_call).
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime
from pathlib import Path

import pytest

import mouth
from mouth import __main__ as cli
from mouth import bargeswitch, hold, onset

from test_mouth import lines, run_player, say  # noqa: F401
from test_mouth_call import bots  # noqa: F401  (fixture)
from test_mouth_call_barge import CALL, LINE, _said, on_call  # noqa: F401  (fixture)
from test_mouth_inbox import box  # noqa: F401  (fixture)
from test_mouth_onset import _wait, ears  # noqa: F401  (fixture)
from tests import env_fence
from tests._anchor import ANCHOR_SUMMER

ROOT = Path(__file__).resolve().parents[2]
SID = "barge-cli-session"
T0 = ANCHOR_SUMMER.timestamp()


@pytest.fixture
def d(tmp_path, monkeypatch) -> Path:
    """A temp mouth dir (never the live one), with no settings file and no env N."""
    m = tmp_path / "mouth"
    m.mkdir()
    monkeypatch.setenv("MOUTH_DIR", str(m))
    monkeypatch.setenv("MOUTH_SETTINGS", str(tmp_path / "mouth.json"))
    monkeypatch.setenv("MOUTH_SESSION_BIN", "")
    monkeypatch.setenv("VOICEMODE_SESSION_ID", SID)
    env_fence.delenv(monkeypatch, "MOUTH_BARGE_WORDS")
    env_fence.delenv(monkeypatch, "MOUTH_BARGE_WORDS_CALL")
    return m


@pytest.fixture
def clock(monkeypatch) -> list:
    t = [T0]
    monkeypatch.setattr(bargeswitch, "clock", lambda: t[0])
    return t


def user(d: Path, **kw) -> None:
    """The user's layer: the settings file (``$MOUTH_SETTINGS``)."""
    (d.parent / "mouth.json").write_text(json.dumps(kw))


def local(t: float) -> str:
    return datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds")


def barge(capsys, *args) -> dict:
    """Run ``mouth barge ARGS``; it exits 0 and prints one JSON readout."""
    assert cli.main(["barge", *args]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1
    return json.loads(out[0])


def refused(capsys, *args) -> str:
    assert cli.main(["barge", *args]) == 2
    return capsys.readouterr().err


# -- --for: duckswitch's parser, our verb's name -----------------------------------

@pytest.mark.parametrize("text,s", [("90s", 90.0), ("30m", 1800.0), ("1h", 3600.0), ("45", 45.0)])
def test_for_takes_seconds_minutes_hours_or_plain_seconds(text, s):
    assert bargeswitch.parse_for(text) == s


@pytest.mark.parametrize("text", ["", "0", "forever", "-5m", "1d", "inf"])
def test_for_refuses_anything_else_and_there_is_no_forever(text):
    with pytest.raises(ValueError, match=r"^mouth barge: --for .* 90s, 30m, 1h"):
        bargeswitch.parse_for(text)


# -- set, expire, reset, readout (pure, on a fake clock) ------------------------------

def test_set_writes_one_file_by_rename_and_state_reads_it_back(d, clock):
    rec = bargeswitch.set(d, words=3, duck_level="off", duck_max_s="5s", ignore="Shell, uh-huh",
                          for_s=1800, by="s1")
    assert rec == {"words": 3, "duck_level": 100, "duck_max_s": 5.0, "ignore": ["shell", "uh", "huh"],
                   "set_t": T0, "until": T0 + 1800, "by": "s1"}
    assert sorted(p.name for p in d.iterdir()) == ["barge.json"]        # no .part left behind
    assert json.loads((d / "barge.json").read_text()) == rec
    assert bargeswitch.state(d) == rec


def test_without_for_it_lasts_an_hour(d, clock):
    assert bargeswitch.set(d, words="off")["until"] == T0 + bargeswitch.OVERRIDE_DEFAULT_S == T0 + 3600


def test_the_reader_works_out_expiry_and_the_layers_answer_again(d, clock):
    user(d, barge_words=4)
    bargeswitch.set(d, words=3, for_s=60)
    clock[0] = T0 + 59.5
    pol = hold.barge_policy(d=d)
    assert (pol["words"], pol["words_from"], pol["words_by"]) == (3, "model", "override")
    assert bargeswitch.shown(d)["remaining_s"] == 1                       # ceil of what is left
    clock[0] = T0 + 60                                                     # now >= until: gone
    assert bargeswitch.state(d) is None
    pol = hold.barge_policy(d=d)
    assert (pol["words"], pol["words_from"], pol["words_by"]) == (4, "user", "setting")
    assert bargeswitch.shown(d) == {"words": 3, "expired_at": local(T0 + 60)}   # the file stays
    assert (d / "barge.json").exists()


def test_reset_drops_it_at_once(d, clock):
    bargeswitch.set(d, duck_level=40)
    assert bargeswitch.reset(d) is True
    assert bargeswitch.reset(d) is False                                   # nothing to drop
    assert bargeswitch.state(d) is None and hold.barge_policy(d=d)["duck_level_from"] == "default"


def test_each_set_replaces_the_override(d, clock):
    bargeswitch.set(d, words=3)
    bargeswitch.set(d, ignore="shell")
    rec = bargeswitch.state(d)
    assert "words" not in rec and rec["ignore"] == ["shell"]


@pytest.mark.parametrize("kw", [{}, {"words": 0}, {"words": "two"}, {"words": 2.5}, {"duck_level": 101},
                                {"duck_level": "loud"}, {"duck_level": 25.5}, {"duck_max_s": -1},
                                {"duck_max_s": "forever"}, {"ignore": ""}, {"ignore": " , "},
                                {"ignore": [3]}, {"words": 2, "for_s": 0}, {"words": 2, "for_s": math.inf}])
def test_set_refuses_a_bad_value_before_anything_is_written(d, clock, kw):
    with pytest.raises(ValueError, match="^mouth barge: "):
        bargeswitch.set(d, **kw)
    assert not (d / "barge.json").exists()


@pytest.mark.parametrize("text", ["", "{", "[]", '{"words": 3}', '{"set_t": 1, "until": 2}',
                                  '{"words": 0, "set_t": 1, "until": 9e99}',
                                  '{"ignore": "shell", "set_t": 1, "until": 9e99}',
                                  '{"words": 3, "set_t": 1, "until": "later"}'])
def test_a_bad_file_is_as_if_there_were_none(d, clock, text):
    (d / "barge.json").write_text(text)
    assert bargeswitch.read(d) is None and bargeswitch.shown(d) is None
    pol = hold.barge_policy(d=d)
    assert (pol["words"], pol["words_from"]) == (2, "default")             # never raises, never cuts


def test_the_readout_with_no_override_is_the_policy(d, clock):
    out = mouth.barge_state(d)
    assert out["override"] is None
    assert (out["words"], out["words_from"], out["words_by"]) == (2, "default", "builtin")
    assert "ignore" not in out
    json.dumps(out)


# -- the layers: the override beats the user and the default; the line beats it -----------

def test_the_override_beats_the_user_and_the_default(d, clock, monkeypatch):
    monkeypatch.setenv("MOUTH_BARGE_WORDS", "5")
    user(d, barge_words=4, barge_duck_level=30, barge_duck_recover_s=1.5, barge_duck_max_s=6)
    bargeswitch.set(d, words=3, duck_level=40, duck_max_s=2.5)
    pol = hold.barge_policy(d=d)
    assert (pol["words"], pol["duck_level"], pol["duck_max_s"]) == (3, 40, 2.5)
    for name in ("words", "duck_level", "duck_max"):
        assert (pol[name + "_from"], pol[name + "_by"]) == ("model", "override")
    assert (pol["recover_s"], pol["recover_from"]) == (1.5, "user")       # no model surface (Q12)


def test_a_partial_override_leaves_the_rest_to_the_lower_layers(d, clock):
    user(d, barge_words=4)
    bargeswitch.set(d, duck_level=40)
    pol = hold.barge_policy(d=d)
    assert (pol["words"], pol["words_from"]) == (4, "user")
    assert (pol["duck_level"], pol["duck_level_by"]) == (40, "override")
    assert (pol["duck_max_s"], pol["duck_max_from"]) == (4.0, "default")


def test_off_is_the_overrides_and_needs_infinite_words(d, clock):
    bargeswitch.set(d, words="off")
    pol = hold.barge_policy(d=d)
    assert (pol["words"], pol["words_by"]) == (None, "override")
    assert hold.need_of(pol) == math.inf
    assert pol["duck_level"] == hold.BARGE_DUCK_LEVEL                      # his voice still ducks


def test_a_lines_argument_beats_the_override_but_only_for_n(d, clock):
    bargeswitch.set(d, words=3, duck_level=40, ignore="shell")
    pol = hold.barge_policy(item={"barge_words": 2}, d=d)
    assert (pol["words"], pol["words_from"], pol["words_by"]) == (2, "model", "argument")
    assert (pol["duck_level"], pol["duck_level_by"]) == (40, "override")  # the line sets N only
    assert "shell" in pol["ignore"]
    pol = hold.barge_policy(item={"barge_words": "off"}, d=d)
    assert (pol["words"], pol["words_by"]) == (None, "argument")
    pol = hold.barge_policy(item={"barge_words": "two"}, d=d)              # not a count: next layer
    assert (pol["words"], pol["words_by"]) == (3, "override")


def test_duck_max_is_the_caps_model_layer(d, clock):
    user(d, barge_duck_max_s=6)
    bargeswitch.set(d, duck_max_s=0)
    pol = hold.barge_policy(d=d)
    assert (pol["duck_max_s"], pol["duck_max_from"], pol["duck_max_by"]) == (0.0, "model", "override")
    assert hold.barge_duck_max_s() == 0.0                                  # the reader do-005 uses
    bargeswitch.reset(d)
    assert (hold.barge_duck_max_s(), hold.barge_policy(d=d)["duck_max_from"]) == (6.0, "user")


def test_duck_off_is_level_100_and_the_line_gets_no_dip(d, clock):
    bargeswitch.set(d, duck_level="off")
    pol = hold.barge_policy(d=d)
    assert pol["duck_level"] == 100

    class Out:
        duck = object()
    assert onset.for_line(Out(), "airpods", policy=pol) is None


# -- R10: --ignore is a union; it adds and never removes ---------------------------------

def test_ignore_adds_to_the_default_list_and_removes_nothing(d, clock):
    bargeswitch.set(d, ignore="shell,Battery")
    pol = hold.barge_policy(d=d)
    assert pol["ignore"] == hold.STOP_WORDS | {"shell", "battery"}
    assert hold.STOP_WORDS <= pol["ignore"]                                # every backchannel still ignored
    assert (pol["ignore_from"], pol["ignore_by"]) == ("default", "builtin")
    assert (pol["ignore_added"], pol["ignore_added_by"]) == (["shell", "battery"], "override")


def test_ignore_adds_to_the_users_list_too(d, clock):
    user(d, barge_ignore_words=["uh-huh"])
    bargeswitch.set(d, ignore=["shell"])
    pol = hold.barge_policy(d=d)
    assert pol["ignore"] == {"uh", "huh", "shell"}
    assert (pol["ignore_from"], pol["ignore_added"]) == ("user", ["shell"])
    user(d, barge_ignore_words=[])                                         # the user counts every word
    assert hold.barge_policy(d=d)["ignore"] == {"shell"}


def test_an_ignored_word_no_longer_counts_toward_n(d, clock):
    """The policy's ignore is what ``barge`` counts against: ``shell`` stops counting."""
    bargeswitch.set(d, ignore="shell")
    pol = hold.barge_policy(d=d)
    assert "shell" in pol["ignore"] and "shell" not in hold.STOP_WORDS
    assert not (set(hold._words("Shell. Shell.")) - pol["ignore"])


def test_the_policy_is_still_json_but_for_the_ignore_set(d, clock):
    bargeswitch.set(d, words="off", duck_level=40, duck_max_s=3, ignore="shell")
    pol = hold.barge_policy(d=d)
    json.dumps({k: v for k, v in pol.items() if k != "ignore"})
    assert isinstance(pol["ignore"], frozenset)


# -- the CLI: set, readout, reset ----------------------------------------------------

def test_cli_set_prints_the_policy_and_the_override(d, clock, capsys):
    out = barge(capsys, "3", "--duck", "off", "--duck-max", "5", "--ignore", "shell", "--for", "40m")
    assert (out["words"], out["words_from"], out["words_by"]) == (3, "model", "override")
    assert (out["duck_level"], out["duck_max_s"], out["ignore_added"]) == (100, 5.0, ["shell"])
    assert out["override"] == {"words": 3, "duck_level": 100, "duck_max_s": 5.0, "ignore": ["shell"],
                               "until": local(T0 + 2400), "remaining_s": 2400, "by": SID}
    clock[0] = T0 + 1220
    assert barge(capsys)["override"]["remaining_s"] == 1180                # the readout, no argument
    assert barge(capsys, "reset")["override"] is None
    assert not (d / "barge.json").exists()


def test_cli_ignore_alone_and_off(d, clock, capsys):
    out = barge(capsys, "--ignore", "shell", "--for", "1h")
    assert out["override"]["ignore"] == ["shell"] and out["words_from"] == "default"
    out = barge(capsys, "off")
    assert (out["words"], out["words_by"]) == (None, "override")
    assert out["override"]["words"] == "off" and "ignore" not in out["override"]   # replaced


@pytest.mark.parametrize("args,why", [(["0"], "N is a whole number"), (["two"], "N is a whole number"),
                                      (["--duck", "101"], "--duck is a level"),
                                      (["--duck-max", "-1"], "--duck-max is seconds"),
                                      (["--ignore", ","], "--ignore names no word"),
                                      (["3", "--for", "forever"], "--for 'forever' is not a time"),
                                      (["--for", "30m"], "--for goes with"),
                                      (["reset", "--duck", "40"], "reset takes nothing else")])
def test_cli_refuses_and_writes_nothing(d, clock, capsys, args, why):
    assert why in refused(capsys, *args)
    assert not (d / "barge.json").exists()


def test_status_shows_the_override_and_its_time_left(box, clock, monkeypatch):  # noqa: F811
    env_fence.delenv(monkeypatch, "MOUTH_BARGE_WORDS")
    monkeypatch.setenv("MOUTH_SETTINGS", "/nonexistent/mouth.json")
    st = mouth.status(box.d)["barge"]
    assert st["override"] is None
    bargeswitch.set(box.d, words=3, for_s=1800, by="s9")
    clock[0] = T0 + 620
    st = mouth.status(box.d)["barge"]
    assert (st["words"], st["words_from"], st["words_by"]) == (3, "model", "override")
    assert st["override"] == {"words": 3, "until": local(T0 + 1800), "remaining_s": 1180, "by": "s9"}
    json.dumps(st)


# -- PRECEDENCE: said once, held in two places -------------------------------------------

def test_the_schema_states_the_precedence_line():
    props = json.loads((ROOT / "settings/mouth.schema.json").read_text())["properties"]
    assert bargeswitch.PRECEDENCE in props["barge_words"]["description"]
    assert "mouth barge --duck L|off" in props["barge_duck_level"]["description"]
    assert "mouth barge --duck-max S" in props["barge_duck_max_s"]["description"]
    assert "never removes one" in props["barge_ignore_words"]["description"]


def test_mouth_barge_help_states_the_same_precedence_line(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["barge", "--help"])
    assert e.value.code == 0
    assert "precedence: " + bargeswitch.PRECEDENCE in capsys.readouterr().out   # one line, never re-wrapped


# -- F4 (do-002's review): an item's own barge_words beats say()'s argument -----------------

def test_an_items_own_count_beats_the_say_argument_and_the_override(box, clock):  # noqa: F811
    """Decided here: ``extra={"barge_words": ...}`` (a resume's carried value, or a
    mail's X-Mouth-Extra) wins over ``say(barge_words=)``, as for ``lead_s`` and
    ``normalize``: the item is what the line carries. Either way it is the line's
    own argument, so it beats the timed override."""
    item = say("both given", barge_words=3, extra={"barge_words": 4, "barge_words_from": "flag"})
    assert (item["barge_words"], item["barge_words_from"]) == (4, "flag")
    bargeswitch.set(box.d, words=2)
    pol = hold.barge_policy(item=item, d=box.d)
    assert (pol["words"], pol["words_by"]) == (4, "argument")


# -- the player reads the override, from its own mouth dir, once a line ----------------------

def test_the_override_reaches_a_call_line(on_call, monkeypatch):  # noqa: F811
    on_call.ears.settings.write_text(json.dumps({"barge_words": 2}))
    bargeswitch.set(on_call.d, words=3, duck_level=40)
    reads = []
    real = hold.barge_policy

    def spy(*a, **kw):
        if kw.get("item"):
            reads.append(kw.get("d"))
        return real(*a, **kw)
    monkeypatch.setattr(hold, "barge_policy", spy)
    on_call.tone(4.0)
    item = say(LINE, device=CALL)
    th = run_player(on_call.d)
    assert _wait(lambda: on_call.ducks and on_call.ducks[0] is not None)
    assert on_call.ducks[0].level == 0.4                                   # the override's duck
    time.sleep(0.2)
    on_call.ears.vad(source="call")
    on_call.ears.partial("Tuesday evening", source="call")                # 2: the user's N, not 3
    time.sleep(0.6)
    assert th.is_alive() and not [r for r in lines(on_call.logs) if r["kind"] == "said"]
    on_call.ears.partial("Tuesday evening, at nine", source="call")
    th.join(10)
    assert not th.is_alive()
    assert (_said(on_call, item)["reason"], _said(on_call, item)["cut"]) == ("barge-in", True)
    assert reads == [on_call.d]                                            # once a line, its own dir


def test_a_lines_own_count_beats_the_override_on_a_call(on_call):  # noqa: F811
    bargeswitch.set(on_call.d, words=4)
    on_call.tone(4.0)
    item = say(LINE, device=CALL, barge_words=2)
    th = run_player(on_call.d)
    assert _wait(lambda: on_call.ducks)
    time.sleep(0.2)
    on_call.ears.vad(source="call")
    on_call.ears.partial("Tuesday evening", source="call")                # 2: the line's own N
    th.join(10)
    assert not th.is_alive()
    assert (_said(on_call, item)["reason"], _said(on_call, item)["cut"]) == ("barge-in", True)
