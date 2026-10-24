"""EARS-234: a fresh install ships no seats; `ears wake add` registers one in the user file.

"Hey AGENT" is a call only when ears-mail knows the seat's names. The shipped
keeper/defaults/wake.json carries none (ours moved to wake.example.json), ears-mail
still mails every finished turn without a seat, and `ears wake add SEAT --names`
writes the seat and its STT sound-alikes ("Dav", "Days" for DAVE) to the user file.
The ears-mail runs reuse test_ears_mail's synthetic heard log (--dry-run --once).
"""
import json, os, subprocess, sys
from pathlib import Path

import pytest

from ears import wake
from ears.cli import main as ears_main
from ears.home import SETTINGS
from tests import _scanlist

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "keeper" / "ears-mail.py"
SHIPPED = REPO / "keeper" / "defaults" / "wake.json"
EXAMPLE = REPO / "keeper" / "defaults" / "wake.example.json"


def turn(seq, text):
    return dict(ts=f"2030-06-09T01:00:{5 * seq:06.3f}+10:00", kind="turn", source="mic",
                device="airpods", text=text, t0=100.0, t1=102.0, seq=seq)


def ears_mail(tmp_path, rules, texts, to="device"):
    """(mails, stderr, returncode) for TEXTS as finished turns, --rules RULES."""
    logs = tmp_path / "logs" / "conversations"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "heard_2030-06-09.jsonl").write_text(
        "".join(json.dumps(turn(i + 1, t)) + "\n" for i, t in enumerate(texts)))
    p = subprocess.run([sys.executable, str(SCRIPT), "--rules", str(rules), "--dry-run", "--once",
                        "--from-start", "--domain", "host1", "--to", to],
                       capture_output=True, text=True, timeout=30,
                       env=dict(os.environ, VOICEMODE_BASE_DIR=str(tmp_path)))
    return [json.loads(l) for l in p.stdout.splitlines() if l.strip()], p.stderr, p.returncode


def rules_of(mails):
    return [(m["seq"], m["to"], m["rule"]) for m in mails]


TEXTS = ["Hey DAVE, what's on?", "Hey Dav, what's on?", "Hey Ivy, what's the latency?"]


# -- 1. the shipped default carries no seats ------------------------------------

def test_shipped_default_has_no_seats_and_keeps_its_defaults():
    conf = json.loads(SHIPPED.read_text())
    assert conf["seats"] == {}
    assert conf["defaults"]["openers"] == "hey|hay"
    assert "ears wake add" in conf["_doc"] and f"~/{SETTINGS}/wake.json" in conf["_doc"]
    assert "WHOLE" in conf["_doc"]            # the key is the address's local part (S2)


def test_example_carries_three_placeholder_seats():
    """EARS-304 R5: the example's seats are placeholders, one per option (sound-alikes,
    name_first + light, own openers), and none of them is a seat of ours."""
    conf = json.loads(EXAMPLE.read_text())
    assert conf["seats"] == {
        "assistant": {"names": "assistant|assistants|assistance"},
        "helper": {"names": "helper", "name_first": True, "light": 300},
        "reviewer": {"names": "reviewer", "openers": "hey|hay"},
    }
    assert conf["defaults"] == json.loads(SHIPPED.read_text())["defaults"]


def test_example_doc_reads_true_as_the_example_and_as_a_copied_wake_json():
    """EARS-362: the example is meant to be copied to the live wake.json, _doc and all, so
    _doc must not say "ears-mail never reads this copy" (false once copied). It names the
    file ears-mail does read, and says the packaged example is not it."""
    doc = json.loads(EXAMPLE.read_text())["_doc"]
    assert "never reads this copy" not in doc and "this copy" not in doc
    assert f"~/{SETTINGS}/wake.json" in doc
    assert "never the example that ships in the package" in doc
    assert "ears wake add" in doc


# The text wake.example.json carries by design that the private scan list matches: the
# placeholder seat address its _doc spells out. Its guard leaves out exactly the rows this
# text matches, whatever the list calls them.
EXAMPLE_LEGIT = ("assistant+engineer",)


def test_example_names_no_private_word():
    """EARS-406: the example's text holds no word of the private scan list, and the same scan
    fires on a word planted from that list as a seat. Every row but those that match
    EXAMPLE_LEGIT, the placeholder seat address its _doc rightly spells out (left out by what
    they match, never by row name): the agents' names are ruling rows, not P0, so a P0-only
    scan would miss the very names this guard is for. The seats themselves are pinned to the
    three placeholders above, so no other name can hide there."""
    text = EXAMPLE.read_text()
    assert all(s in text for s in EXAMPLE_LEGIT), "a sample is no longer the example's text"
    rows = _scanlist.rows(legit=EXAMPLE_LEGIT)
    found = _scanlist.hits(text, rows)
    assert found == [], f"wake.example.json holds a word of row(s) {found}"
    conf = json.loads(text)
    missed = []
    for name, word in _scanlist.controls(rows):
        planted = dict(conf, seats={**conf["seats"], word: {"names": word}})
        if name not in _scanlist.hits(json.dumps(planted, indent=2), rows):
            missed.append(name)
    assert missed == [], f"the scan missed a word planted from row(s) {missed}"


def test_example_calls_its_seat(tmp_path):
    """The example is a working rules file: "hey <seat>" (or a sound-alike) is a call."""
    texts = TEXTS[:2] + ["Hey assistant, what's the latency?", "Hey assistance, are you there?"]
    m, _, rc = ears_mail(tmp_path, EXAMPLE, texts)
    assert rc == 0
    assert rules_of(m) == [(1, ["heard+airpods"], ""), (2, ["heard+airpods"], ""),
                           (3, ["heard+airpods"], "assistant@host1=call"),
                           (4, ["heard+airpods"], "assistant@host1=call")]


@pytest.mark.parametrize("rules", ["missing", "shipped"])
def test_no_seats_still_mails_every_turn_with_no_call(tmp_path, rules):
    path = tmp_path / "nowhere" / "wake.json" if rules == "missing" else SHIPPED
    m, err, rc = ears_mail(tmp_path, path, TEXTS)
    assert rc == 0, err
    assert rules_of(m) == [(1, ["heard+airpods"], ""), (2, ["heard+airpods"], ""),
                           (3, ["heard+airpods"], "")]
    assert f"no seats in {path}: no turn is a call; add one with: ears wake add <seat>" in err
    assert err.count("no seats in") == 1


def test_no_seats_in_seats_mode_is_still_an_error(tmp_path):
    _, err, rc = ears_mail(tmp_path, tmp_path / "missing.json", TEXTS, to="seats")
    assert rc == 2 and "no seats: give --seats or a rules file with seats" in err


# -- 2. ears wake add writes the user file ---------------------------------------

def test_add_dave_with_sound_alikes_makes_hey_dav_a_call(tmp_path):
    f = tmp_path / "home" / SETTINGS / "wake.json"   # missing, parents too
    assert ears_main(["wake", "add", "dave", "--names", "dav|days", "--file", str(f)]) == 0
    assert json.loads(f.read_text()) == {"seats": {"dave": {"names": "dave|dav|days"}}}
    m, _, rc = ears_mail(tmp_path, f, TEXTS + ["Hey Days, and hey Ivy."])
    assert rc == 0
    assert rules_of(m) == [(1, ["heard+airpods"], "dave@host1=call"),
                           (2, ["heard+airpods"], "dave@host1=call"),
                           (3, ["heard+airpods"], ""),          # Ivy is not on this host
                           (4, ["heard+airpods"], "dave@host1=call")]


@pytest.mark.parametrize("names", ["dav|days", "dave|dav|days", "dav|days|DAV|Dave", " dav | days "])
def test_names_are_key_first_deduped_in_order(tmp_path, names):
    f = tmp_path / "wake.json"
    wake.add("dave", names, f)
    assert wake.seats(f) == {"dave": "dave|dav|days"}   # de-duped case-blind, first kept


def test_no_names_means_the_key(tmp_path):
    f = tmp_path / "wake.json"
    wake.add("DAVE", None, f)
    assert wake.seats(f) == {"dave": "dave"}


def test_a_plus_key_is_a_literal_name(tmp_path):
    f = tmp_path / "wake.json"
    wake.add("sessionmail+engineer", "", f)
    assert json.loads(f.read_text())["seats"] == {
        "sessionmail+engineer": {"names": r"sessionmail\+engineer"}}


def test_re_adding_merges_and_keeps_the_rest(tmp_path):
    f = tmp_path / "wake.json"
    f.write_text(json.dumps({
        "_doc": "mine", "defaults": {"openers": "hey|hay|a", "light": 0},
        "seats": {"vera": {"names": "vera|vira", "name_first": True, "light": 300},
                  "dave": {"names": "dave|dav", "light": 60, "openers": "hey|ok"}}}))
    wake.add("dave", "days|dav", f)
    conf = json.loads(f.read_text())
    assert conf["_doc"] == "mine"
    assert conf["defaults"] == {"openers": "hey|hay|a", "light": 0}
    assert conf["seats"]["vera"] == {"names": "vera|vira", "name_first": True, "light": 300}
    assert conf["seats"]["dave"] == {"names": "dave|dav|days", "light": 60, "openers": "hey|ok"}
    assert list(conf) == ["_doc", "defaults", "seats"]


def test_ears_wake_env_names_the_file(tmp_path, monkeypatch):
    f = tmp_path / "env-wake.json"
    monkeypatch.setenv("EARS_WAKE", str(f))
    wake.add("dave", "dav", None)
    assert wake.seats(None) == {"dave": "dave|dav"}
    monkeypatch.delenv("EARS_WAKE")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert wake.target() == tmp_path / "home" / SETTINGS / "wake.json"


def test_write_is_atomic_no_temp_left(tmp_path):
    f = tmp_path / "wake.json"
    wake.add("dave", "dav", f)
    wake.add("ivy", "ivey", f)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["wake.json"]


@pytest.mark.parametrize("path", [SHIPPED, EXAMPLE, SHIPPED.parent / "wake.local.json"])
def test_refuses_the_shipped_default(tmp_path, path, capsys):
    before = SHIPPED.read_bytes(), EXAMPLE.read_bytes()
    assert ears_main(["wake", "add", "dave", "--file", str(path)]) == 1
    assert "shipped default" in capsys.readouterr().err
    assert (SHIPPED.read_bytes(), EXAMPLE.read_bytes()) == before
    assert not (SHIPPED.parent / "wake.local.json").exists()


def test_refuses_the_shipped_default_through_a_symlink(tmp_path):
    link = tmp_path / "wake.json"
    link.symlink_to(SHIPPED)
    with pytest.raises(wake.WakeError, match="shipped default"):
        wake.add("dave", "dav", link)


def test_refuses_a_bad_regex_and_writes_nothing(tmp_path, capsys):
    f = tmp_path / "wake.json"
    assert ears_main(["wake", "add", "dave", "--names", "dav(", "--file", str(f)]) == 1
    assert "do not compile as a regex" in capsys.readouterr().err
    assert not f.exists()


@pytest.mark.parametrize("seat", ["dave@host1", "", "two words"])
def test_refuses_a_seat_that_is_not_a_local_part(tmp_path, seat):
    with pytest.raises(wake.WakeError):
        wake.add(seat, "", tmp_path / "wake.json")
    assert not (tmp_path / "wake.json").exists()


def test_refuses_a_file_that_is_not_json(tmp_path):
    f = tmp_path / "wake.json"
    f.write_text("{not json")
    with pytest.raises(wake.WakeError, match="not JSON"):
        wake.add("dave", "", f)
    assert f.read_text() == "{not json"


def test_list_prints_each_seat_and_its_names(tmp_path, capsys):
    f = tmp_path / "wake.json"
    assert ears_main(["wake", "list", "--file", str(f)]) == 0
    assert "no seats in" in capsys.readouterr().err
    wake.add("dave", "dav|days", f)
    wake.add("ivy", "ivey", f)
    assert ears_main(["wake", "list", "--file", str(f)]) == 0
    assert capsys.readouterr().out == "dave: dave|dav|days\nivy: ivy|ivey\n"
