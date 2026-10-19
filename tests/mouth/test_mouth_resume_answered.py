"""A cut question his turn answered is not replayed (EARS-365).

His words cut a line that asked him something, and those words WERE the
answer. The mouth then replayed the unsaid rest of the question when his turn
ended, so he heard an answered question again (two cases on AirPods: a
question about which release goes to the new org, and the line after it).

The rule (rule (b), N=2): at release, a held resume is dropped as ``expired``,
detail :data:`hold.ANSWERED`, when BOTH the sentence the line was cut in ends
in "?" and his turn has N or more content words (not STOP_WORDS, not
STOP_ASKS, not the line's own echo). A cut statement, a stop ask, a blip and
an answer below N all still resume. N (``resume_answer_words``) and the "?"
test (``resume_answer_question``) are mouth settings.

Fakes only: a heard log in a tmp dir, a settings file in a tmp dir, the
``silence`` backend and the ``null`` device for the player. No real audio,
mouth, keeper or mail.
"""
from __future__ import annotations

import json
import time
from datetime import datetime

import pytest

from ears import heard
from mouth import hold, notice
from tests import env_fence

#: Case 1: the question his "yes ... the new sessionkit org" answered.
Q1 = "Is that the 0.2.0 publish to the new sessionkit org? Or something later, like 1.0?"
A1 = "Yes, yeah the new sessionkit org"
#: Case 2's shape: the next line, a question too, cut by his answer.
Q2 = "Shall I write the release notes from the changelog, or draft them fresh?"
A2 = "From the changelog, keep them short"


@pytest.fixture
def ears(tmp_path, monkeypatch):
    """A heard log and a mouth settings file in tmp; returns ``turn(text, age_s,
    source, kind)`` which writes one floor record (a ``turn`` by default)."""
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm"))
    monkeypatch.setenv("MOUTH_SETTINGS", str(tmp_path / "mouth.json"))
    for var in ("MOUTH_RESUME_ANSWER_WORDS", "MOUTH_RESUME_ANSWER_QUESTION",
                "MOUTH_HOLD_GRACE_S", "MOUTH_HOLD_IDLE_S"):
        env_fence.delenv(monkeypatch, var)   # both names (EARS-294)
    heard.log_dir().mkdir(parents=True, exist_ok=True)

    def write(text="", age_s=0.5, source="mic", kind="turn", event=None):
        ts = datetime.fromtimestamp(time.time() - age_s).astimezone().isoformat(timespec="milliseconds")
        rec = {"ts": ts, "seq": 999, "kind": kind, "source": source}
        if kind == "event":
            rec["event"] = event
        else:
            rec["text"] = text
        with open(heard.log_path(), "a") as f:
            f.write(json.dumps(rec) + "\n")
        for c in hold._caches.values():
            c.update(t=0.0, v=None)

    write.settings = lambda **kw: (tmp_path / "mouth.json").write_text(json.dumps(kw))
    return write


def resume(line: str, played: str = "", device=None, **kw) -> dict:
    """A queued resume as ``player._resume`` makes it: the rest from the start of
    the cut sentence (``hold.rest_of``), the cut line as ``resume_text``."""
    return {"hold": "turn-end", "resume_of": "c51c87801f2f", "requested_t": time.time() - 3,
            "expires_s": 20.0, "text": hold.rest_of(line, played), "resume_text": line[:200],
            "device": device, **kw}


def _check(item: dict):
    v = hold.check(item)
    return str(v), getattr(v, "detail", None)


ANSWERED = ("expire", "the user answered it")
PLAYS = ("play", None)


# -- the two cases: no replay ----------------------------------------------------

def test_case_1_his_answer_cut_the_question(ears):
    ears("yes yeah", kind="partial", age_s=2.5)          # his words during the line
    ears(A1)                                             # the turn the mouth releases on
    assert _check(resume(Q1, played="Is that")) == ANSWERED


def test_case_1_cut_in_the_second_question(ears):
    ears(A1)
    item = resume(Q1, played="Is that the 0.2.0 publish to the new sessionkit org? Or some")
    assert item["text"] == "Or something later, like 1.0?"
    assert _check(item) == ANSWERED


def test_case_2_shape(ears):
    ears(A2)
    assert _check(resume(Q2, played="Shall I write the")) == ANSWERED


def test_the_constant_and_its_word(ears):
    assert hold.ANSWERED == "the user answered it"
    ears(A1)
    v = hold.check(resume(Q1, played="Is that"))
    assert v == "expire" and v.detail == hold.ANSWERED          # still the plain word


# -- still resumes ----------------------------------------------------------------

def test_a_cut_statement_with_a_long_turn_still_resumes(ears):
    ears("Great, now publish it to the sessionkit org please, and tag it")
    line = "The build is green and the tag is minted on master. It is ready to publish."
    assert _check(resume(line, played="The build is")) == PLAYS


@pytest.mark.parametrize("words", ["hang on", "wait", "hang on, wait", "hold on, hold on"])
def test_a_stop_ask_still_resumes(ears, words):
    ears(words)
    assert _check(resume(Q1, played="Is that")) == PLAYS


@pytest.mark.parametrize("words", ["yes", "Yes, sessionkit", "yeah okay sure"])
def test_an_answer_below_n_still_resumes(ears, words):
    ears(words)
    assert _check(resume(Q1, played="Is that")) == PLAYS


def test_a_blip_with_no_turn_text_still_resumes(ears):
    ears(kind="event", event="turn-discarded")
    assert _check(resume(Q1, played="Is that")) == PLAYS


def test_an_idle_release_on_an_old_partial_still_resumes(ears):
    ears("yes the new sessionkit org", kind="partial", age_s=30)  # older than idle_s: free
    assert _check(resume(Q1, played="Is that")) == PLAYS


def test_no_heard_log_still_resumes(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "none"))
    for c in hold._caches.values():
        c.update(t=0.0, v=None)
    assert _check(resume(Q1, played="Is that")) == PLAYS


def test_his_turn_that_is_the_line_coming_back_is_echo(ears):
    ears("is that the 0.2.0 publish to the new sessionkit org")
    assert _check(resume(Q1, played="Is that")) == PLAYS


@pytest.mark.parametrize("words", ["Something later, 1.0", "Something later, like 1.0, later"])
def test_his_answer_in_the_words_of_an_unplayed_sentence_is_no_echo(ears, words):
    """Cut in its first sentence, the line's second ("Or something later, like
    1.0?") was never played, so the mic could not hear it: his "something later,
    1.0" is the answer, not echo. Measured on b48ac4f: echo, and replayed."""
    ears(words)
    item = resume(Q1, played="Is that")
    assert hold.is_echo(words, item["text"])               # the whole rest would call it echo
    assert _check(item) == ANSWERED


def test_his_echo_of_the_cut_sentence_still_resumes(ears):
    """The cut sentence itself may have been playing: its words coming back with a
    stop ask are echo, so the line resumes."""
    ears("is that the 0.2.0 publish to the new hang on")
    assert _check(resume(Q1, played="Is that the 0.2.0 publish to the")) == PLAYS


@pytest.mark.parametrize("line, rest, want", [
    (Q1, "Is that the 0.2.0 publish to the new sessionkit org? Or something later, like 1.0?",
     "Is that the 0.2.0 publish to the new sessionkit org?"),
    (Q1, "Or something later, like 1.0?", Q1),
    ("", "Is it done? Or not?", "Is it done?"),
    ("A long line the first 200 characters of", "Not in it at all. Later.",
     "A long line the first 200 characters of"),
])
def test_could_be_heard(line, rest, want):
    assert hold._could_be_heard(line, rest) == want


# -- the played estimate: echo is only what the mic could have heard ------------

def played(line: str, est: str, played_s: float, **kw) -> dict:
    """A resume carrying the cut line's played estimate, as ``player._resume``
    queues it: the said row's ``text_played_est`` and ``played_s``."""
    return resume(line, played=est, resume_played=est, resume_played_s=played_s, **kw)


#: The turns of the margin table (the foreman's ruling on the rework, 0.5 s): an
#: answer in the cut sentence's own words, twice, and a true echo of the played
#: words with a stop ask.
TURN_A = "Yeah, the new sessionkit org"
TURN_B = "Yes, yeah the new sessionkit org"
TURN_C = "is that the 0.2.0 publish to the new hang on"


def cut_at(line: str, cut_s: float) -> dict:
    """A resume of ``line`` cut ``cut_s`` in, its estimate made as the player makes
    it: `player.text_at` at the played fraction of the floor-rate length."""
    from mouth import player
    est = player.text_at(line, cut_s / (len(line) / 15.0))
    return played(line, est, cut_s)


def test_the_margin_is_half_a_second():
    assert hold.CUT_ECHO_AHEAD_S == 0.5 and hold.AHEAD_S == 3.0     # not near()'s, for a stopped line


@pytest.mark.parametrize("cut_s", [0.5, 1.0, 2.0])
@pytest.mark.parametrize("words", [TURN_A, TURN_B])
def test_his_answer_in_the_cut_sentences_own_words_drops(ears, words, cut_s):
    """Cut early in its first sentence, the line had not reached "the new
    sessionkit org", so his words are the answer, not echo. With AHEAD_S (3 s)
    as the margin, A at a 0.5 s cut was echo and replayed."""
    ears(words)
    assert _check(cut_at(Q1, cut_s)) == ANSWERED


def test_a_true_echo_of_the_played_words_still_resumes(ears):
    """The words it had played (2 s in: "Is that the 0.2.0 publish to"), coming
    back through the mic with a stop ask, are echo: the line resumes."""
    ears(TURN_C)
    item = cut_at(Q1, 2.0)
    assert item["resume_played"] == "Is that the 0.2.0 publish to"
    assert _check(item) == PLAYS


def test_at_a_three_second_cut_the_answer_still_drops(ears):
    """3 s in, "the new" had played and "sessionkit" lies inside the margin: A is
    still his answer (not one run of 70% of its words)."""
    ears(TURN_A)
    assert _check(cut_at(Q1, 3.0)) == ANSWERED


def test_once_the_window_reaches_org_an_answer_reads_as_echo(ears):
    """The known limit of judging on text: from about 3.2 s in, the window reaches
    "org" (played, or within the margin), so A is one run of the line's words and
    reads as echo, as C does: it resumes. B's extra "yes" keeps it his."""
    ears(TURN_A)
    assert _check(cut_at(Q1, 3.4)) == PLAYS
    ears(TURN_B, age_s=0.2)
    assert _check(cut_at(Q1, 3.4)) == ANSWERED


def test_the_margin_covers_a_playhead_just_past_the_estimate(ears):
    """The estimate is a proportion of the audio: the word just past it
    ("publish") may have played. It coming back is echo; without the margin it
    would be read as his answer."""
    ears("the 0.2.0 publish hang on")
    assert _check(played(Q1, "Is that the 0.2.0", 1.2)) == PLAYS
    assert not hold.is_echo("the 0.2.0 publish hang on", "Is that the 0.2.0")


def test_an_item_without_the_estimate_falls_back_to_the_cut_sentence(ears):
    """A resume queued before the estimate was carried: echo is judged against
    the line up to the end of the cut sentence (`_could_be_heard`), as e184eff."""
    ears(TURN_A)
    assert _check(resume(Q1, played="Is that")) == PLAYS              # echo of the cut sentence
    ears("Something later, 1.0", age_s=0.2)
    assert _check(resume(Q1, played="Is that")) == ANSWERED          # never played: his words


def test_an_estimate_that_does_not_line_up_falls_back(ears):
    """An estimate that is not the start of the rest, or an unreadable
    played_s, is no window: the fallback judges it."""
    assert hold._heard_window("Something else entirely", 1.0, "Is that it?") is None
    assert hold._heard_window("Is that", "soon", "Is that the one?") is None
    assert hold._heard_window("Is that", None, "Is that the one?") is None
    ears(TURN_A)
    assert _check(played(Q1, "Is that", None)) == PLAYS               # fallback: echo


@pytest.mark.parametrize("est, secs, rest, want", [
    # 7 chars in 0.5 s = 14 c/s, so 0.5 s ahead is 7 chars, out to the word end
    ("Is that", 0.5, "Is that the 0.2.0 publish to the new sessionkit org? Or later?",
     "Is that the 0.2.0"),
    # nothing played yet: the player's floor rate, 15 c/s
    ("", 0.1, "Is that the 0.2.0 publish to the new sessionkit org? Or later?", "Is that"),
    # cut in the second sentence: all the played text, then the margin
    ("First one. Is it", 1.0, "Is it on? Or not at all, ever, in any way whatsoever?",
     "First one. Is it on? Or not"),
    ("Is that it", 0.8, "Is that it", "Is that it"),                 # nothing left ahead
    # cut on a sentence's end: the next sentence follows after a space
    ("Is it on?", 0.6, "Or not at all?", "Is it on? Or not at"),
])
def test_heard_window(est, secs, rest, want):
    assert hold._heard_window(est, secs, rest) == want


def test_a_statement_cut_before_its_question_reads_the_cut_sentence(ears):
    """The cut sentence is the rest's FIRST: a line that ends in "?" but was cut
    in a statement still resumes, and one cut in a question that ends on a
    statement is dropped (the full line is not the test)."""
    ears(A2)
    statement_first = "I pushed the tag to the new org. Shall I publish it now?"
    assert _check(resume(statement_first, played="I pushed")) == PLAYS
    question_first = "Shall I publish it now? I pushed the tag to the new org."
    assert _check(resume(question_first, played="Shall I")) == ANSWERED


def test_a_line_that_is_not_a_resume_is_untouched(ears):
    ears(A1)
    opener = {"hold": "turn-end", "requested_t": time.time() - 3, "text": Q1}
    assert _check(opener) == PLAYS


def test_a_say_again_replay_is_untouched(ears):
    """EARS-22: a say-again replay carries ``again_of``, never ``resume_of``."""
    ears(A1)
    again = {"hold": "turn-end", "again_of": "c51c87801f2f", "requested_t": time.time() - 3,
             "text": Q1}
    assert _check(again) == PLAYS


def test_on_a_call_only_the_call_answers(ears):
    ears(A1, source="mic")
    assert _check(resume(Q1, played="Is that", device="call:74702")) == PLAYS
    ears(A1, source="call", age_s=0.2)
    assert _check(resume(Q1, played="Is that", device="call:74702")) == ANSWERED


def test_quiet_keeps_its_own_detail(ears, monkeypatch):
    monkeypatch.setattr(hold, "asked_for_quiet", lambda *a, **kw: {"text": "please be quiet"})
    ears(A1)
    assert _check(resume(Q1, played="Is that")) == ("expire", hold.QUIET)


# -- the settings -----------------------------------------------------------------

def test_the_defaults():
    assert hold.RESUME_ANSWER_WORDS == 2
    assert hold.resume_answer_words(settings={}) == 2
    assert hold.resume_answer_question(settings={}) is True


def test_n_from_the_setting(ears):
    ears("yes the new org")                              # 2 content words: new, org
    ears.settings(resume_answer_words=3)
    assert _check(resume(Q1, played="Is that")) == PLAYS
    ears.settings(resume_answer_words=2)
    assert _check(resume(Q1, played="Is that")) == ANSWERED


def test_n_from_the_env_and_the_setting_beats_it(ears, monkeypatch):
    ears("Yes, sessionkit")                              # 1 content word
    monkeypatch.setenv("MOUTH_RESUME_ANSWER_WORDS", "1")
    assert _check(resume(Q1, played="Is that")) == ANSWERED
    ears.settings(resume_answer_words=4)
    assert hold.resume_answer_words() == 4
    assert _check(resume(Q1, played="Is that")) == PLAYS


@pytest.mark.parametrize("bad", [0, -1, 1.5, "two", True, None])
def test_an_unreadable_n_is_as_if_unset(bad):
    assert hold.resume_answer_words(settings={"resume_answer_words": bad}) == 2


def test_an_unreadable_n_in_the_env_is_as_if_unset(monkeypatch):
    monkeypatch.setenv("MOUTH_RESUME_ANSWER_WORDS", "0")
    assert hold.resume_answer_words(settings={}) == 2


def test_the_question_test_off_drops_any_answered_line(ears):
    ears("Great, now publish it to the sessionkit org please")
    line = "The build is green and the tag is minted on master."
    assert _check(resume(line, played="The build")) == PLAYS
    ears.settings(resume_answer_question=False)
    assert _check(resume(line, played="The build")) == ANSWERED


def test_the_question_test_off_still_keeps_a_stop_ask_and_a_short_answer(ears):
    ears.settings(resume_answer_question=False)
    ears("hang on")
    line = "The build is green and the tag is minted on master."
    assert _check(resume(line, played="The build")) == PLAYS


@pytest.mark.parametrize("raw, want", [("off", False), ("0", False), ("false", False), ("no", False),
                                       ("on", True), ("1", True), ("true", True), ("maybe", True)])
def test_the_question_test_from_the_env(monkeypatch, raw, want):
    monkeypatch.setenv("MOUTH_RESUME_ANSWER_QUESTION", raw)
    assert hold.resume_answer_question(settings={}) is want


def test_the_question_setting_beats_the_env(monkeypatch):
    monkeypatch.setenv("MOUTH_RESUME_ANSWER_QUESTION", "off")
    assert hold.resume_answer_question(settings={"resume_answer_question": True}) is True
    assert hold.resume_answer_question(settings={"resume_answer_question": "yes"}) is False  # not a bool: unset


# -- the cut sentence -------------------------------------------------------------

@pytest.mark.parametrize("rest, want", [
    ("Is that the 0.2.0 publish to the new sessionkit org? Or something later, like 1.0?", True),
    ("Or something later, like 1.0?", True),
    ("Version 0.2.0 is out. Want 1.0?", False),        # the cut sentence is the statement
    ("Shall I tag it (now)?", True),
    ("No punctuation at all", False),
    ("", False),
])
def test_is_question(rest, want):
    assert hold.cut_is_question(rest) is want


# -- the second notice says why ---------------------------------------------------

def test_the_notice_says_he_answered_it():
    item = {"utt": "c26611b4a7a0", "text": "Or something later, like 1.0?", "resume_of": "c51c87801f2f",
            "resume_text": Q1, "expires_s": 20.0, "requested_t": time.time() - 30}
    body = notice.body(item, {"reason": "expired", "played_s": 0.0, "detail": hold.ANSWERED})
    assert "Dropped: the user answered it, so it is not replayed." in body
    assert "ran past" not in body and "asked for quiet" not in body
