"""The cut names its words (EARS-315 R4, R8 for the notice; design.md s3.4).

Calls stop on 2 real words, and the cut
notice names the words. A line his words cut says which words cut it: the
said row (``cut_by``, ``cut_how``, ``barge_words``, ``barge_words_from``),
the barge-in stop record (so each line it flushes says the same), the notice's
``cut by:`` line with N and the layer that set it, and the ``mouth timeline``
row's ``cut_by`` key and human row.

Nothing here reaches the MTA (the conftest fence), a live mouth or a live
port: the player runs on a scratch mouth dir, a scratch heard log, the
``silence`` backend and a fake device that pulls in real time.
"""
from __future__ import annotations

import json
import time

import pytest

import mouth
from mouth import hold, notice, player, timeline

from test_mouth import PullingSd, say  # noqa: F401
from test_mouth_barge_in import room  # noqa: F401  (fixture)
from test_mouth_inbox import box, lines, run_player  # noqa: F401  (fixture)
from test_mouth_notice import mails, mkbox
from test_mouth_onset import ears, played, _wait  # noqa: F401  (fixtures)
from test_mouth_timeline_past import Log, d, doc, h, past  # noqa: F401  (fixtures)

LINE = "the echo canceller is how FaceTime keeps your own voice out"
LONG = "a line long enough to talk over, for a while longer than that"


def _barge(*a, **kw):
    """The real `hold.barge`, under the conftest's shadow."""
    return getattr(hold.barge, "shadows", hold.barge)(*a, **kw)


def _trace(t0, line=LINE, **kw):
    trace: list = []
    got = _barge(t0, line, trace=trace, **kw)
    return got, trace


# -- hold.cut_words: the words of the poll that cut ----------------------------------

def test_a_word_cut_names_the_words_counted_in_order_once_each(room):
    t0 = time.time() - 5
    room("okay trying")                     # "okay" ignored: 1 word, waits
    room("trying barge FaceTime")           # "trying" again, "facetime" the line's own
    got, trace = _trace(t0, words=2)
    assert got is not None and got["text"] == "trying barge FaceTime"
    assert hold.cut_words(trace) == (["trying", "barge"], "words")


def test_the_words_are_barges_whole_count_not_the_last_partial(room):
    t0 = time.time() - 5
    room("trying")
    room("barge")
    room("happens")
    got, trace = _trace(t0, words=3)
    assert got["text"] == "happens"
    assert hold.cut_words(trace) == (["trying", "barge", "happens"], "words")


def test_a_stop_ask_names_the_ask(room):
    t0 = time.time() - 5
    room("trying")
    room("hang on a second")
    got, trace = _trace(t0, words=2)
    assert got["text"] == "hang on a second"
    assert hold.cut_words(trace) == (["hang"], "stop-ask")


def test_no_cut_names_nothing(room):
    t0 = time.time() - 5
    room("trying")                          # one word, N=2: waits
    got, trace = _trace(t0, words=2)
    assert got is None and trace
    assert hold.cut_words(trace) == ([], None)
    assert hold.cut_words([]) == ([], None)
    assert hold.cut_words(None) == ([], None)


def test_a_skipped_partial_after_the_cut_does_not_hide_it():
    """`barge` marks ``cut`` on the last partial that COUNTED; an echo weighed
    after it is still in the trace."""
    trace = [{"counted": ["advice"], "verdict": "wait"},
             {"counted": ["battery", "advice"], "verdict": "cut"},
             {"skip": "echo", "counted": [], "verdict": "wait"}]
    assert hold.cut_words(trace) == (["advice", "battery"], "words")


def test_the_phantoms_alone_never_name_a_cut(room):
    """R3: ".", "MM." and "Shell." (three times) reach no cut at N=2, so name nothing."""
    t0 = time.time() - 5
    for text in (".", "MM.", "Shell.", "Shell.", "Shell."):
        room(text)
    got, trace = _trace(t0, words=2)
    assert got is None and hold.cut_words(trace) == ([], None)


# -- the notice's "cut by:" line -------------------------------------------------------

CUT = {"reason": "barge-in", "cut": True, "played_s": 1.0, "cut_at_s": 1.0,
       "text_played_est": "a line long", "underruns": 0, "resume": "won't", "resume_why": "x"}


@pytest.mark.parametrize("frm, says", [("default", "set by the default"),
                                       ("user", "set by the user's setting"),
                                       ("model", "set by the model")])
def test_the_notice_names_the_words_n_and_its_layer(frm, says):
    rec = {**CUT, "cut_by": ["advice", "battery"], "cut_how": "words",
           "barge_words": 2, "barge_words_from": frm}
    body = notice.body({"utt": "u1", "text": LONG}, rec)
    assert f'cut by: "advice battery" (2 words; N=2, {says})' in body
    lines_ = body.splitlines()
    assert lines_[lines_.index(next(x for x in lines_ if x.startswith("why:"))) + 1].startswith("cut by:")


def test_the_notice_names_a_stop_ask():
    rec = {**CUT, "cut_by": ["stop"], "cut_how": "stop-ask", "barge_words": 2, "barge_words_from": "user"}
    body = notice.body({"utt": "u1", "text": LONG}, rec)
    assert 'cut by: "stop" (a stop ask: it cuts at any N)' in body


def test_a_stop_ask_under_no_barge_names_the_ask_and_no_n():
    rec = {**CUT, "cut_by": ["wait"], "cut_how": "stop-ask", "barge_words_from": "model"}
    assert 'cut by: "wait" (a stop ask: it cuts at any N)' in notice.body({"utt": "u1", "text": LONG}, rec)


def test_a_cut_with_more_words_than_n_counts_them_all():
    rec = {**CUT, "cut_by": ["tell", "bridge", "works", "tomorrow"], "cut_how": "words",
           "barge_words": 2, "barge_words_from": "default"}
    assert 'cut by: "tell bridge works tomorrow" (4 words; N=2, set by the default)' in \
        notice.body({"utt": "u1", "text": LONG}, rec)


def test_a_flushed_line_names_them_too():
    rec = {"reason": "barge-in", "cut": True, "played_s": 0.0, "cut_by": ["advice", "battery"],
           "cut_how": "words", "barge_words": 2, "barge_words_from": "user"}
    body = notice.body({"utt": "u2", "text": "queued behind it"}, rec)
    assert 'cut by: "advice battery" (2 words; N=2, set by the user\'s setting)' in body
    assert "flushed" in body


@pytest.mark.parametrize("rec", [
    {"reason": "barge-in", "played_s": 1.0, "text_played_est": "a line", "underruns": 0},   # a hand stop
    {"reason": "stop", "played_s": 1.0, "cut_by": ["advice"], "cut_how": "words"},          # not a barge-in
])
def test_no_cut_by_line_without_his_words(rec):
    assert "cut by:" not in notice.body({"utt": "u1", "text": LONG}, rec)


def test_the_subject_is_unchanged():
    rec = {**CUT, "cut_by": ["advice", "battery"], "cut_how": "words", "barge_words": 2,
           "barge_words_from": "user"}
    plain = {k: v for k, v in rec.items() if k not in player.CUT_KEYS}
    assert notice.subject(LONG, rec) == notice.subject(LONG, plain)
    assert len(notice.subject(LONG, rec)) <= notice.SUBJECT_MAX


# -- the timeline row -------------------------------------------------------------------

def test_row_keys_end_with_cut_by():
    assert timeline.ROW_KEYS[-1] == "cut_by"
    assert timeline.row(kind="line")["cut_by"] is None


def test_a_cut_line_names_its_words_in_the_timeline(d, h):
    log = Log(h)
    log.line(10.0, reason="barge-in", cut_by=["advice", "battery"], cut_how="words",
             barge_words=2, barge_words_from="user")
    log.line(20.0)
    cut, done = past(doc(d, h, at=100.0))
    assert cut["cut_by"] == ["advice", "battery"] and cut["state"] == "barge-in"
    assert done["cut_by"] is None
    text = timeline.human(doc(d, h, at=100.0), which="past")
    rows = [x for x in text.splitlines() if cut["utt"] in x or done["utt"] in x]
    assert rows[0].endswith("cut by: advice battery")
    assert "cut by" not in rows[1]


def test_a_flushed_line_names_them_in_the_timeline(d, h):
    Log(h).unplayed(10.0, utt="flushed00001", reason="barge-in", cut_by=["stop"], cut_how="stop-ask")
    (r,) = past(doc(d, h, at=100.0))
    assert r["cut_by"] == ["stop"]


# -- the player: real play_one / check_stop / _handle_stop on a fake device ----------

@pytest.fixture
def told(played, monkeypatch):
    """The notices the caller's session box got, by utt (the conftest's fake sendmail)."""
    import os
    from pathlib import Path
    from types import SimpleNamespace

    monkeypatch.setenv("VOICEMODE_AGENT", "ivy")
    monkeypatch.setenv("VOICEMODE_SESSION_ID", "s-cut")
    for k in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID"):
        monkeypatch.delenv(k, raising=False)
    env = SimpleNamespace(agents=Path(os.environ["MOUTH_FAKE_MAIL_ROOT"]))
    mkbox(env, "s-cut")

    def get() -> dict:
        return {m["Mouth-Utt"]: m for m in mails(env, "s-cut")}
    return get


def _said(played, utt):
    return next(r for r in lines(played.logs) if r["kind"] == "said" and r["utt"] == utt)


def _cut(played, *partials, queued=False):
    """A line plays; his partials cut it. With ``queued`` a second line waits behind it."""
    played.ears.settings.write_text(json.dumps({"barge_duck_level": 100, **played.settings}))
    a = say(LONG, device="MacBook Pro Speakers")
    b = say("queued behind it", device="MacBook Pro Speakers") if queued else None
    th = run_player(played.d)
    assert _wait(lambda: any(r["kind"] == "saying" and r["utt"] == a["utt"]
                             for r in lines(played.logs)))       # a hang guard only
    for p in partials:
        played.ears.partial(p)
        time.sleep(0.25)
    th.join(10)
    assert not th.is_alive()
    return a, b


@pytest.fixture
def cut(played):
    played.settings = {}
    return played


def test_the_cut_lines_said_row_names_the_counted_words(cut, told):
    a, _ = _cut(cut, "okay trying", "barge happens")
    row = _said(cut, a["utt"])
    assert (row["reason"], row["cut"]) == ("barge-in", True)
    assert row["cut_by"] == ["trying", "barge", "happens"] and row["cut_how"] == "words"
    assert (row["barge_words"], row["barge_words_from"]) == (2, "default")
    body = told()[a["utt"]].get_content()
    assert 'cut by: "trying barge happens" (3 words; N=2, set by the default)' in body


def test_the_users_n_is_named_with_its_layer(cut, told):
    cut.settings = {"barge_words": 3}
    a, _ = _cut(cut, "trying barge", "happens now")
    row = _said(cut, a["utt"])
    assert row["cut_by"] == ["trying", "barge", "happens"]
    assert (row["barge_words"], row["barge_words_from"]) == (3, "user")
    assert "N=3, set by the user's setting" in told()[a["utt"]].get_content()


def test_a_stop_ask_cut_names_the_ask_on_its_row_and_notice(cut, told):
    a, _ = _cut(cut, "hang on")
    row = _said(cut, a["utt"])
    assert (row["cut_by"], row["cut_how"]) == (["hang"], "stop-ask")
    assert 'cut by: "hang" (a stop ask: it cuts at any N)' in told()[a["utt"]].get_content()


def test_a_flushed_line_names_the_words_that_cut_the_line_before_it(cut, told):
    a, b = _cut(cut, "trying barge", queued=True)
    cut_row, flushed = _said(cut, a["utt"]), _said(cut, b["utt"])
    assert flushed["played_s"] == 0.0 and flushed["reason"] == "barge-in"
    for k in player.CUT_KEYS:
        assert flushed[k] == cut_row[k], k
    assert flushed["cut_by"] == ["trying", "barge"]
    body = told()[b["utt"]].get_content()
    assert "flushed" in body and 'cut by: "trying barge" (2 words; N=2, set by the default)' in body


def test_the_cut_line_is_named_in_the_timeline(cut, told):
    a, b = _cut(cut, "trying barge", queued=True)
    got = timeline.document(cut.d, cut.logs, which="past")    # the player's heard dir
    rows = {r["utt"]: r for r in got["rows"] if r["kind"] == "line"}
    assert rows[a["utt"]]["cut_by"] == ["trying", "barge"]
    assert rows[b["utt"]]["cut_by"] == ["trying", "barge"]      # the line it flushed
    text = timeline.human(got, which="past")
    assert any(a["utt"] in x and x.endswith("cut by: trying barge") for x in text.splitlines())


def test_a_hand_barge_in_stop_names_no_words(cut, told):
    cut.ears.settings.write_text(json.dumps({"barge_duck_level": 100}))
    a = say(LONG, device="MacBook Pro Speakers")
    th = run_player(cut.d)
    assert _wait(lambda: any(r["kind"] == "saying" and r["utt"] == a["utt"] for r in lines(cut.logs)))
    mouth.stop("barge-in", flush=True)
    th.join(10)
    row = _said(cut, a["utt"])
    assert row["reason"] == "barge-in"
    assert not any(k in row for k in player.CUT_KEYS)
    assert "cut by:" not in told()[a["utt"]].get_content()


def test_a_line_that_ends_uncut_names_nothing(cut, told):
    cut.ears.settings.write_text(json.dumps({"barge_duck_level": 100}))
    a = say("short", device="MacBook Pro Speakers")
    run_player(cut.d).join(10)
    row = _said(cut, a["utt"])
    assert row["reason"] == "done" and not any(k in row for k in player.CUT_KEYS)
