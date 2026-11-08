"""EARS-504 R1 + R2: a seat key of two words or more is called by its spoken forms.

The keeper derives one more alternative from every seat KEY of two words or more: its
words joined by spaces, hyphens or nothing (`mini-me` gives `mini[\\s-]*me`). It is
matched beside the file's `names` by `_call`, `_first`, `_named` and the say-again
strip, whether `names` is unset or holds what `ears wake add` writes (`mini\\-me`).
`"spoken": false` turns it off. A mishearing ("Minnie Me") is one `--names` alternative.
Design: the EARS-504 task's design.md, sections R1, R2 and the test plan (SC1, SC2).
Every keeper run is --dry-run --once over a synthetic heard log, as in test_ears_mail.
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ears import wake
from ears.cli import main as ears_main

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "keeper" / "ears-mail.py"
SHIPPED = REPO / "keeper" / "defaults" / "wake.json"
EXAMPLE = REPO / "keeper" / "defaults" / "wake.example.json"


def _em():
    spec = importlib.util.spec_from_file_location("ears_mail_spoken", SCRIPT)
    em = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(em)
    return em


em = _em()


def _rules(tmp_path, conf):
    p = tmp_path / "rules.json"
    p.write_text(json.dumps(conf))
    return em.load_rules(str(p), [])


def _added(tmp_path, seat="mini-me", **kw):
    """The wake file `ears wake add SEAT` writes (the normal path: names is set)."""
    f = tmp_path / "wake.json"
    wake.add(seat, path=f, **kw)
    return f


# The two ways a seat reaches the keeper: hand-written with no names, and by ears wake add.
@pytest.fixture(params=["no-names", "wake-add"])
def mini_me(request, tmp_path):
    """mini-me's rules, with name_first on, from a file of either kind."""
    if request.param == "no-names":
        conf = {"seats": {"mini-me": {"name_first": True}}}
    else:
        conf = json.loads(_added(tmp_path).read_text())
        assert conf["seats"]["mini-me"]["names"] == r"mini\-me"     # F1: names is set
        conf["seats"]["mini-me"]["name_first"] = True
    return _rules(tmp_path, conf)["mini-me"]


# -- SC1: the spoken forms, in _call, _first and _named --------------------------------

@pytest.mark.parametrize("text", ["hey mini me", "hey minime", "Hey Mini-Me, what's on?",
                                  "Hey, Mini Me.", "HEY MINI  ME"])
def test_a_spoken_form_is_a_call_and_names_the_seat(mini_me, text):
    assert mini_me["_call"].search(text), text
    assert mini_me["_named"].search(text), text


def test_name_first_makes_a_spoken_form_a_first_call(mini_me):
    assert mini_me["_first"].search("Mini me, what time is it")
    assert mini_me["_first"].search("minime, what time is it")


@pytest.mark.parametrize("text", ["minimal", "the minimes", "a mini, me", "I want a mini. Me too",
                                  "hey mini", "hey me"])
def test_words_that_are_not_the_key_side_by_side_miss(mini_me, text):
    """F2: whole words at both ends, in key order, with only spaces or hyphens between."""
    assert not mini_me["_named"].search(text), text
    assert not mini_me["_call"].search(text), text
    assert not mini_me["_first"].search(text), text


def test_names_keeps_its_value_and_the_spoken_form_is_beside_it(tmp_path):
    """r["names"] is today's value; r["_names"] adds the spoken form after it."""
    r = _rules(tmp_path, {"seats": {"mini-me": {}, "ivy": {"names": "ivy|ivey"}}})
    assert r["mini-me"]["names"] == r"mini\-me"
    assert r["mini-me"]["_names"] == r"mini\-me|mini[\s-]*me"
    assert r["ivy"]["names"] == r["ivy"]["_names"] == "ivy|ivey"


@pytest.mark.parametrize("key", ["dave", "vera", "r2d2"])
def test_a_one_word_key_gains_nothing(tmp_path, key):
    r = _rules(tmp_path, {"seats": {key: {}}})[key]
    assert r["_names"] == r["names"] == key
    assert em.spoken_names(key) is None


def test_a_plus_key_is_called_by_its_words(tmp_path):
    r = _rules(tmp_path, {"seats": {"sessionmail+engineer": {}}})["sessionmail+engineer"]
    assert r["_call"].search("hey sessionmail engineer, the build")
    assert r["_call"].search("hey sessionmail+engineer")              # the key itself still
    assert not r["_named"].search("sessionmailengineering")


# -- spoken: false ---------------------------------------------------------------------

def test_spoken_false_on_the_seat_turns_the_forms_off(tmp_path):
    r = _rules(tmp_path, {"seats": {"mini-me": {"spoken": False, "name_first": True}}})["mini-me"]
    assert r["_names"] == r["names"] == r"mini\-me"
    for text in ("hey mini me", "hey minime"):
        assert not r["_call"].search(text) and not r["_named"].search(text), text
    assert not r["_first"].search("Mini me, what time is it")
    assert r["_call"].search("Hey Mini-Me, hi")                       # the key still calls


def test_spoken_false_in_defaults_turns_them_off_for_every_seat(tmp_path):
    r = _rules(tmp_path, {"defaults": {"spoken": False},
                          "seats": {"mini-me": {}, "data-set": {}, "a-team": {"spoken": True}}})
    assert not r["mini-me"]["_named"].search("hey mini me")
    assert not r["data-set"]["_named"].search("the data set is big")
    assert r["a-team"]["_named"].search("we need a team")              # a seat turns it back on


def test_spoken_is_on_in_the_defaults_and_in_both_shipped_files():
    assert em.DEFAULTS["spoken"] is True
    for f in (SHIPPED, EXAMPLE):
        assert json.loads(f.read_text())["defaults"]["spoken"] is True, f
    assert em.load_rules(None, ["mini-me"])["mini-me"]["_named"].search("hey mini me")


def test_both_shipped_docs_say_what_spoken_does():
    for f in (SHIPPED, EXAMPLE):
        doc = json.loads(f.read_text())["_doc"]
        assert "'spoken': false" in doc and "mini me" in doc, f
        assert "ears wake add mini-me --names 'minnie me'" in doc, f


# -- the parity of the twins: the keeper's split is wake.spoken()'s ----------------------

@pytest.mark.parametrize("key", ["mini-me", "sessionmail+engineer", "r2-d2", "foo_bar.baz",
                                 "dave", "a--b", "-lead"])
def test_the_keepers_split_is_wake_spokens(key):
    """The STT hint primes whisper with wake.spoken(key), so the keeper must accept what
    it primes. keeper/ imports nothing from src/: the two splits are twins."""
    assert " ".join(em.spoken_words(key)) == wake.spoken(key).lower()


# -- R7: light is in seconds -------------------------------------------------------------

def test_the_example_says_light_is_in_seconds():
    doc = json.loads(EXAMPLE.read_text())["_doc"]
    assert "300 ms" not in doc and "300 s" in doc
    assert json.loads(EXAMPLE.read_text())["seats"]["helper"]["light"] == 300


# -- through the keeper: Ears-Rule and Ears-Addressed ------------------------------------

def turn(seq, text):
    return dict(ts=f"2030-06-09T01:00:{5 * seq:06.3f}+10:00", kind="turn", source="mic",
                device="airpods", text=text, t0=100.0, t1=102.0, seq=seq)


def ears_mail(tmp_path, rules, records):
    """The device mails (--to device) for RECORDS, --rules RULES."""
    logs = tmp_path / "logs" / "conversations"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "heard_2030-06-09.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    p = subprocess.run([sys.executable, str(SCRIPT), "--rules", str(rules), "--dry-run", "--once",
                        "--from-start", "--domain", "host1", "--to", "device"],
                       capture_output=True, text=True, timeout=30,
                       env=dict(os.environ, VOICEMODE_BASE_DIR=str(tmp_path)))
    assert p.returncode == 0, p.stderr
    return [m for m in map(json.loads, filter(str.strip, p.stdout.splitlines())) if m.get("seq")]


def rule_and_addressed(mails):
    return [(m["rule"], m["mail"]["headers"].get("Ears-Addressed")) for m in mails]


@pytest.mark.parametrize("kind", ["no-names", "wake-add"])
def test_through_the_keeper_a_spoken_form_is_a_call_and_is_addressed(tmp_path, kind):
    """SC1 end to end, --to device: mini-me@host1=call and Ears-Addressed mini-me@host1."""
    if kind == "no-names":
        rules = tmp_path / "rules.json"
        rules.write_text(json.dumps({"seats": {"mini-me": {}}}))
    else:
        rules = _added(tmp_path)
    m = ears_mail(tmp_path, rules, [turn(1, "Hey mini me, what time is it?"),
                                    turn(2, "hey minime, the build"),
                                    turn(3, "That was minimal.")])
    assert rule_and_addressed(m) == [("mini-me@host1=call", "mini-me@host1"),
                                     ("mini-me@host1=call", "mini-me@host1"),
                                     ("", None)]


def test_through_the_keeper_spoken_false_is_no_call(tmp_path):
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({"seats": {"mini-me": {"spoken": False}}}))
    m = ears_mail(tmp_path, rules, [turn(1, "Hey mini me, what time is it?")])
    assert [(r, a) for r, a in rule_and_addressed(m)] == [("", None)]


def test_mini_me_say_again_names_mini_me(tmp_path):
    """F4: "Mini-Me, say again" is mini-me's say-again. The strip reads _names: names
    alone (mini\\-me) never matched the words "mini me" that _words() leaves."""
    r = _rules(tmp_path, json.loads(_added(tmp_path).read_text()))
    assert em.again_match("Mini-Me, say again", r) == (("say", "again"), {"mini-me"})
    assert em.again_match("hey minime, pardon", r) == (("pardon",), {"mini-me"})
    off = _rules(tmp_path, {"seats": {"mini-me": {"spoken": False}}})
    assert em.again_match("Mini-Me, say again", off)[1] == set()       # today's miss, opted out

    line = "The build is green and the second option is safer."
    recs = [dict(ts="2030-06-09T01:00:07.000+10:00", kind="saying", agent="mini-me", text=line,
                 utt="m1n1me000001"),
            dict(ts="2030-06-09T01:00:10.000+10:00", kind="said", agent="mini-me",
                 utt="m1n1me000001", played_s=3.0),
            dict(turn(3, "Mini-Me, say again."), ts="2030-06-09T01:00:15.000+10:00")]
    m = ears_mail(tmp_path, _added(tmp_path), recs)
    assert rule_and_addressed(m) == [("mini-me@host1=again", "mini-me@host1")]


# -- SC2: one documented command for a mishearing ----------------------------------------

def test_a_mishearing_added_by_ears_wake_add_is_a_call(tmp_path, capsys):
    """After `ears wake add mini-me --names 'minnie me'`, "Hey Minnie Me, what time is it"
    is mini-me@host1=call and lists mini-me@host1."""
    f = tmp_path / "wake.json"
    assert ears_main(["wake", "add", "mini-me", "--names", "minnie me", "--file", str(f)]) in (0, None)
    assert "mini-me" in capsys.readouterr().out
    assert json.loads(f.read_text())["seats"]["mini-me"]["names"] == r"mini\-me|minnie me"
    m = ears_mail(tmp_path, f, [turn(1, "Hey Minnie Me, what time is it"),
                                turn(2, "Hey mini me, and you")])
    assert rule_and_addressed(m) == [("mini-me@host1=call", "mini-me@host1"),
                                     ("mini-me@host1=call", "mini-me@host1")]


def test_the_wake_help_gives_the_mishearing_step(capsys):
    with pytest.raises(SystemExit):
        ears_main(["wake", "--help"])
    assert "ears wake add mini-me --names 'minnie me'" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        ears_main(["wake", "add", "--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert "mishearings" in out and "--names 'minnie me'" in out, out
