"""`barge_ignore_words`: the words barge-in never counts, as a mouth setting
(EARS-270 design.md s4).

The words are configurable, so a test can try different selections.
Absent, the
setting is today's STOP_WORDS, the same object, so nothing changes until it is
set; deleting the key brings the default back. It reaches the barge count
only, never `is_echo`.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from mouth import hold

from test_mouth import PullingSd, say  # noqa: F401
from test_mouth_inbox import box, lines, run_player  # noqa: F401  (fixture)
from test_mouth_onset import ears, played, _wait  # noqa: F401  (fixtures)

SCHEMA = Path(__file__).resolve().parents[2] / "settings" / "mouth.schema.json"
LINE = "the echo canceller is how FaceTime keeps your own voice out"


def test_absent_it_is_stop_words_itself():
    words, src = hold.barge_ignore_words({})
    assert words is hold.STOP_WORDS and src == "default"
    assert len(words) == 159


def test_a_list_is_split_as_his_partials_are():
    words, src = hold.barge_ignore_words({"barge_ignore_words": ["Uh-huh", "I'm", " OK ", "um"]})
    assert src == "setting"
    assert words == frozenset({"uh", "huh", "i", "m", "ok", "um"})


def test_an_empty_list_ignores_nothing():
    assert hold.barge_ignore_words({"barge_ignore_words": []}) == (frozenset(), "setting")


def test_an_unreadable_value_is_as_if_unset():
    for bad in ("a b", [1], ["ok", 2], {"room": ["ok"]}, None, True, 3):
        words, src = hold.barge_ignore_words({"barge_ignore_words": bad})
        assert words is hold.STOP_WORDS and src == "default", bad


def test_the_settings_file_sets_it_and_deleting_the_key_restores_the_default(ears):
    assert hold.barge_ignore_words() == (hold.STOP_WORDS, "default")      # no file
    ears.settings.write_text(json.dumps({"barge_words": 2, "barge_ignore_words": ["um", "uh"]}))
    assert hold.barge_ignore_words() == (frozenset({"um", "uh"}), "setting")
    ears.settings.write_text(json.dumps({"barge_words": 2}))              # the key deleted
    words, src = hold.barge_ignore_words()
    assert words is hold.STOP_WORDS and src == "default"


@pytest.mark.parametrize("need", [2, 3])
def test_barge_counts_against_the_list_it_is_given(ears, monkeypatch, need):
    """barge_words is pinned here, not read from the default (EARS-274 moved
    it 3 -> 2): the list decides which words count, the need how many."""
    monkeypatch.setenv("MOUTH_BARGE_WORDS", str(need))
    said = ["okay", "so", "well", "now"]                 # four ignored words, none the line's
    t0 = time.time() - 1
    ears.partial(" ".join(said))
    assert hold.barge(t0, LINE) is None                  # today: nothing of his counts
    assert hold.barge(t0, LINE, ignore=hold.STOP_WORDS) is None
    assert hold.barge(t0, LINE, ignore=frozenset()) is not None              # count every word: 4 >= need
    short = frozenset(said[:len(said) - need + 1])                           # leaves need - 1 to count
    enough = frozenset(said[:len(said) - need])                              # leaves need to count
    assert hold.barge(t0, LINE, ignore=short) is None
    assert hold.barge(t0, LINE, ignore=enough) is not None
    assert hold.barge(t0, LINE, ignore=short, words=need - 1) is not None    # words= still wins


def test_the_default_is_byte_identical_to_no_ignore(ears):
    t0 = time.time() - 1
    default = hold.barge_ignore_words({})[0]
    steps = ["Fantastic!", "I'm actually", "on a call now", "with my AI", "hang on"]
    for text in steps:
        ears.partial(text)
        assert hold.barge(t0, LINE) == hold.barge(t0, LINE, ignore=default)


def test_the_line_own_words_still_never_count(ears):
    t0 = time.time() - 1
    ears.partial("canceller FaceTime voice")             # all the line's own
    assert hold.barge(t0, LINE, ignore=frozenset()) is None


def test_is_echo_is_untouched_by_the_setting(ears):
    """The bag rule keeps STOP_WORDS: is_echo takes no setting at all, and with
    the setting in force it answers exactly as without it."""
    cases = [("so the echo canceller keeps your voice", LINE),
             ("named dave and the voice is out of the canceller now", LINE),
             ("tell me about the bridge works tomorrow", LINE)]
    before = [hold.is_echo(p, l) for p, l in cases]
    ears.settings.write_text(json.dumps({"barge_ignore_words": []}))
    assert hold.barge_ignore_words()[1] == "setting"
    assert [hold.is_echo(p, l) for p, l in cases] == before


def test_the_schema_default_is_stop_words():
    p = json.loads(SCHEMA.read_text())["properties"]["barge_ignore_words"]
    assert p["type"] == "array" and p["items"] == {"type": "string"} and p["uniqueItems"] is True
    assert p["default"] == sorted(hold.STOP_WORDS)


def test_the_player_reads_it_for_the_line(played):
    """An empty list in the settings file: four filler words of his cut a line
    that today's list would let play on."""
    played.ears.settings.write_text(json.dumps({"barge_ignore_words": [], "barge_duck_level": 100}))
    item = say("a line long enough to talk over, for a while longer than that",
               device="MacBook Pro Speakers")
    th = run_player(played.d)
    assert _wait(lambda: any(r["kind"] == "saying" and r["utt"] == item["utt"]
                             for r in lines(played.logs)))
    played.ears.partial("okay so well now")
    th.join(8)
    assert not th.is_alive()
    said = next(r for r in lines(played.logs) if r["kind"] == "said" and r["utt"] == item["utt"])
    assert said["reason"] == "barge-in" and said["cut"] is True
