"""Who may barge in: ``barge_in`` in the mouth's settings: anything, or only
a voice identified as the user's. Identified only means a television does
not barge in.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from itertools import count

import pytest

from ears import heard
from mouth import hold
from tests import env_fence

LINE = "the echo canceller is how FaceTime keeps your own voice out"


@pytest.fixture
def room(tmp_path, monkeypatch):
    """write(text, speaker=None, age_s=0, source="mic"): a heard partial, and
    the side-car's row for it when ``speaker`` is given."""
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm"))
    monkeypatch.setenv("MOUTH_BARGE", "1")
    env_fence.delenv(monkeypatch, "MOUTH_BARGE_WORDS")
    env_fence.delenv(monkeypatch, "MOUTH_LABEL_WAIT_S")
    heard.log_dir().mkdir(parents=True, exist_ok=True)
    seqs = count(100)

    def write(text, speaker=None, age_s=0.0, source="mic"):
        seq = next(seqs)
        ts = datetime.fromtimestamp(time.time() - age_s).astimezone().isoformat(timespec="milliseconds")
        with open(heard.log_path(), "a") as f:
            f.write(json.dumps({"ts": ts, "seq": seq, "kind": "partial", "text": text,
                                "source": source}) + "\n")
        if speaker is not None:
            label(seq, speaker)
        for c in hold._caches.values():
            c.update(t=0.0, v=None)
        return seq

    def label(seq, speaker):
        with open(heard.speakers_path(heard.log_path()), "a") as f:
            f.write(json.dumps({"seq": seq, "kind": "partial", "speaker": speaker,
                                "by": "speaker-sidecar2"}) + "\n")

    write.label = label
    return write


def test_the_setting():
    assert hold.barge_in(settings={}) is None                          # default: anyone
    assert hold.barge_in(settings={"barge_in": "anyone"}) is None
    assert hold.barge_in(settings={"barge_in": ["Theo"]}) == {"theo"}
    assert hold.barge_in(settings={"barge_in": "theo"}) == {"theo"}
    assert hold.barge_in(settings={"barge_in": []}) == frozenset()      # nobody
    assert hold.barge_in(settings={"barge_in": 3}) is None             # unreadable: as before
    per = {"barge_in": {"room": ["theo"]}}
    assert hold.barge_in("airpods", per) == {"theo"}
    assert hold.barge_in("call:54541", per) is None                    # left out: anyone
    per = {"barge_in": {"room": "anyone", "call": ["theo", "alice"]}}
    assert hold.barge_in("call:54541", per) == {"theo", "alice"}
    assert hold.barge_in(None, per) is None


def test_the_setting_is_read_from_the_mouth_settings_file(tmp_path, monkeypatch):
    f = tmp_path / "mouth.json"
    f.write_text(json.dumps({"barge_in": ["theo"]}))
    monkeypatch.setenv("MOUTH_SETTINGS", str(f))
    assert hold.barge_in("airpods") == {"theo"}
    f.write_text("not json")
    assert hold.barge_in("airpods") is None


def test_the_television_does_not_cut(room):
    t0 = time.time() - 5
    room("and in other news tonight the markets fell sharply", speaker="other")
    assert hold.barge(t0, LINE, who=frozenset({"theo"})) is None
    assert hold.barge(t0, LINE) is not None                            # anyone: it would


def test_his_voice_cuts(room):
    t0 = time.time() - 5
    room("actually I need the other one", speaker="theo")
    assert hold.barge(t0, LINE, who=frozenset({"theo"})) is not None


def test_a_stop_word_in_someone_elses_voice_does_not_cut(room):
    t0 = time.time() - 5
    room("wait", speaker="other")
    assert hold.barge(t0, LINE, who=frozenset({"theo"})) is None
    room("hang on", speaker="theo")
    assert hold.barge(t0, LINE, who=frozenset({"theo"}))["text"] == "hang on"


def test_silent_and_mixed_never_cut(room):
    t0 = time.time() - 5
    room("and some road", speaker="silent")                           # road noise with words
    room("over the top of each other here", speaker="mixed")
    assert hold.barge(t0, LINE, who=frozenset({"theo"})) is None


def test_only_his_words_count_toward_the_two(room):
    """BARGE_WORDS is 2 (EARS-274; it was 3): the other speaker's three words
    do not make up his count."""
    t0 = time.time() - 5
    room("I'm actually", speaker="theo")                              # 1 word of his
    room("weather forecast tomorrow", speaker="other")                # 3 that are not
    assert hold.barge(t0, LINE, who=frozenset({"theo"})) is None
    room("on a call now", speaker="theo")                             # his 2nd
    assert hold.barge(t0, LINE, who=frozenset({"theo"})) is not None


def test_an_unnamed_partial_waits_for_its_name(room):
    t0 = time.time() - 5
    seq = room("actually I need the other one")                       # no row yet
    assert hold.barge(t0, LINE, who=frozenset({"theo"})) is None
    room.label(seq, "theo")                                           # 0.4 s later
    assert hold.barge(t0, LINE, who=frozenset({"theo"})) is not None


def test_a_partial_nobody_names_cuts_as_before(room):
    """The side-car is down, or it does not hear the call: a partial still
    unnamed after LABEL_WAIT_S cuts, so a dead side-car cannot take his
    barge-in away."""
    t0 = time.time() - 10
    room("actually I need the other one", age_s=hold.LABEL_WAIT_S + 0.5, source="call")
    got = hold.barge(t0, LINE, sources=("call",), words=1, who=frozenset({"theo"}))
    assert got is not None


def test_nobody_cuts_with_an_empty_list(room):
    t0 = time.time() - 5
    room("hang on hang on", speaker="theo")
    assert hold.barge(t0, LINE, who=frozenset()) is None


def test_echo_is_still_not_a_barge(room):
    t0 = time.time() - 5
    room("how FaceTime keeps your own voice out", speaker="theo")      # the line, through the mic
    assert hold.barge(t0, LINE, who=frozenset({"theo"})) is None
