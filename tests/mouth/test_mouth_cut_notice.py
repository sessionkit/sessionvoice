"""A barge-in cut notice says definitely WILL or WON'T replay, and a second
notice closes the loop (EARS-306).

On a Delta call the user heard one answer twice: the
mouth replayed the unsaid rest of a cut line, and the seat, told the mouth "may"
re-offer it, re-said it as well. The player now decides the replay ONCE, before
the cut line's notice (``player._plan``), writes it on the said row, and the
notice is written from it; ``_resume`` acts on that same decision under the utt
the notice named. Every way a replay ends gets a second notice, riding.

Nothing here reaches the MTA (the conftest fence) or a live mouth: the env
fixture is test_mouth_notice's, a tmp mouth dir with the ``silence`` backend
and the ``null`` device.
"""

from __future__ import annotations

import re
import time

import pytest

import mouth
from mouth import box as _box
from mouth import hold, notice, player
from test_mouth_notice import env, mails, run_player, said, say, told  # noqa: F401  (fixtures)

#: Short enough to replay quickly on ``silence`` (len/15 s), long enough to cut at 0.5 s.
LINE = "one two three four five six seven eight nine ten eleven twelve"
CUT = {"reason": "barge-in", "cut": True, "played_s": 1.0, "cut_at_s": 1.0,
       "text_played_est": "one two", "underruns": 0}


@pytest.fixture
def resume_on(monkeypatch):
    """Replay on (5 s), and no partial of his asks for quiet."""
    monkeypatch.setenv("MOUTH_RESUME_S", "5")
    monkeypatch.setattr(hold, "asked_for_quiet", lambda *a, **kw: None)


def _quiet(monkeypatch):
    monkeypatch.setattr(hold, "asked_for_quiet", lambda *a, **kw: {"text": "please be quiet"})


def _no_may(body: str) -> None:
    assert not re.search(r"\bmay\b", body, re.I), body


def _cut_and_told(env, told) -> tuple[dict, dict, dict]:
    """A line plays, his words cut it at ~0.5 s, the player runs on; (said rows, notices)."""
    a = say(LINE)
    th = run_player(env.d, idle=0.5)
    time.sleep(0.5)
    mouth.stop("barge-in", flush=True)
    th.join(15)
    assert not th.is_alive()
    return a, said(env.logs), told()


# -- R1 / SC1: the first notice is the decision ------------------------------------

def test_plan_will_mints_the_replays_utt(resume_on):
    plan = player._plan({"utt": "u1", "text": LINE, "backend": "silence"}, CUT)
    assert plan["resume"] == "will" and plan["resume_s"] == 5.0
    assert re.fullmatch(r"[0-9a-f]{12}", plan["resume_utt"])


def test_a_will_notice_names_the_replay_and_says_do_not_re_say_it(resume_on):
    item = {"utt": "u1", "text": LINE, "backend": "silence"}
    rec = {**CUT, **player._plan(item, CUT)}
    body = notice.body(item, rec)
    assert (f"The mouth WILL replay the unsaid rest when the user's turn ends (utt {rec['resume_utt']})."
            in body)
    assert "Do not re-say it." in body
    assert "If the user asks for quiet, or answers it, or their turn runs past 5 s, it is dropped " \
           "and you get a second notice." in body
    assert "A replay is never replayed." in body
    _no_may(body)


@pytest.mark.parametrize("case, item, why", [
    ("resume off", {"backend": "silence"}, "replay is off (resume_s is 0)"),
    ("a file", {"backend": "file", "file": "/x.wav"}, "it is a played file"),
    ("he asked for quiet", {"backend": "silence"}, hold.QUIET),
    ("nothing left", {"backend": "silence", "text": "one two."}, "nothing of it was left unsaid"),
])
def test_each_wont_names_why_and_says_re_say_only_if_it_matters(monkeypatch, case, item, why):
    monkeypatch.setenv("MOUTH_RESUME_S", "0" if case == "resume off" else "5")
    if case == "he asked for quiet":
        _quiet(monkeypatch)
    else:
        monkeypatch.setattr(hold, "asked_for_quiet", lambda *a, **kw: None)
    item = {"utt": "u1", "text": LINE, **item}
    cut = {**CUT, "text_played_est": "one two."} if case == "nothing left" else CUT
    rec = {**cut, **player._plan(item, cut)}
    assert rec["resume"] == "won't" and rec["resume_why"] == why
    body = notice.body(item, rec)
    assert f"The user's words cut it. It will not come back: {why}. Re-say it only if it still matters." in body
    assert "WILL" not in body
    _no_may(body)


def test_a_replay_cut_again_is_a_wont_never_twice(resume_on):
    item = {"utt": "r1", "text": "five six", "backend": "silence", "resume_of": "u1",
            "resume_text": LINE}
    plan = player._plan(item, CUT)
    assert plan == {"resume": "won't", "resume_why": player.NEVER_TWICE}


def test_a_flushed_line_says_re_say_only_if_it_matters():
    body = notice.body({"utt": "u2", "text": "queued"}, {"reason": "barge-in", "played_s": 0.0})
    assert "It will not come back. Re-say it only if it still matters." in body
    _no_may(body)


def test_no_notice_ever_says_may(resume_on):
    """SC1: every reason, cut or never started, a replay or not, and each second notice."""
    item = {"utt": "u1", "text": LINE, "backend": "silence", "requested_t": time.time()}
    replay = {**item, "utt": "r1", "resume_of": "u0", "resume_text": "the cut line", "expires_s": 5}
    recs = [{"reason": r, "played_s": p, "text_played_est": "one two" if p else "", "underruns": 0}
            for r in [*notice._ADVICE, "barge-in", "refused", "expired"] for p in (0.0, 1.0)]
    recs += [{**CUT, **player._plan(item, CUT)}, {**CUT, "resume": "won't", "resume_why": "x"},
             {"reason": "done", "played_s": 2.0, "text_played_est": LINE},
             {"reason": "expired", "played_s": 0.0, "detail": hold.QUIET}]
    for it in (item, replay):
        for rec in recs:
            _no_may(notice.body(it, rec))
            _no_may(notice.subject(it["text"], rec))


# -- the decision is made once, and acted on as written ----------------------------

def test_a_resumable_cut_end_to_end(env, told, resume_on):
    """The cut row says will + the replay's utt; the notice names that utt; the replay
    is queued under it, plays in full, and its own notice closes the loop."""
    a, rows, got = _cut_and_told(env, told)
    cut = rows[a["utt"]]
    assert cut["reason"] == "barge-in" and cut["resume"] == "will"
    r = cut["resume_utt"]
    first = got[a["utt"]]
    assert f"(utt {r})" in first.get_content() and "Do not re-say it." in first.get_content()
    assert first["Priority"] == "non-urgent"
    # the replay: the very utt the notice named, played in full (silence never barges)
    assert rows[r]["resume_of"] == a["utt"] and rows[r]["reason"] == "done"
    second = got[r]
    assert second["Mouth-Notice"] == "done" and second["Priority"] == "non-urgent"
    assert second["Subject"].startswith("replayed in full: ")
    body = second.get_content()
    assert f'The replay of line {a["utt"]} ("{LINE}") was played in full.' in body
    assert "heard:  all of it" in body
    _no_may(body)
    assert rows[r]["notice"].startswith("sent ")


def test_resume_acts_on_the_rows_decision_and_never_decides_again(env, monkeypatch):
    """A row that says won't queues nothing, even with replay on and rest left; a row
    that says will queues the rest under the planned utt, whatever resume_s is now."""
    monkeypatch.setenv("MOUTH_RESUME_S", "5")
    monkeypatch.setattr(hold, "asked_for_quiet", lambda *a, **kw: None)
    item = {"utt": "u1", "text": "First part. Second part here.", "device": "null",
            "backend": "silence", "agent": "ivy", "session": "s-test"}
    env.d.mkdir(parents=True, exist_ok=True)
    player._resume(env.d, item, {**CUT, "text_played_est": "First part. Sec",
                                 "resume": "won't", "resume_why": "x"})
    assert _box.Queue(env.d).lines() == []
    monkeypatch.setenv("MOUTH_RESUME_S", "0")   # a later re-decision would say won't
    player._resume(env.d, item, {**CUT, "text_played_est": "First part. Sec",
                                 "resume": "will", "resume_utt": "abcdef012345", "resume_s": 5.0})
    (x,) = [_box.public(e) for e in _box.Queue(env.d).lines()]
    assert x["utt"] == "abcdef012345" and x["resume_of"] == "u1"
    assert x["text"] == "Second part here." and x["resume_text"] == item["text"]
    assert x["hold"] == "turn-end" and float(x["expires_s"]) == 5.0
    # how much played rides with it, for hold's echo test (EARS-365)
    assert x["resume_played"] == "First part. Sec" and float(x["resume_played_s"]) == 1.0


def test_a_failed_replay_after_a_will_notice_is_told_dropped(env, told, monkeypatch):
    """say() fails after the notice said WILL: the sender gets the second notice."""
    def refuse(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(mouth, "say", refuse)
    item = {"utt": "u1", "text": "First part. Second part here.", "device": "null",
            "backend": "silence", "agent": "ivy", "session": "s-test"}
    env.d.mkdir(parents=True, exist_ok=True)
    player._resume(env.d, item, {**CUT, "text_played_est": "First part. Sec",
                                 "resume": "will", "resume_utt": "abcdef012345", "resume_s": 5.0})
    m = told()["abcdef012345"]
    assert m["Mouth-Notice"] == "resume-failed" and m["Priority"] == "non-urgent"
    body = m.get_content()
    assert 'This was the replay of line u1 ("First part. Second part here.")' in body
    assert "The mouth could not queue the replay." in body and "disk full" in body
    _no_may(body)


# -- R2 / SC2: a second notice for each way a replay ends --------------------------

def _replay(**kw):
    return say("five six seven", extra={"resume_of": "abc123def456", "resume_text": LINE}, **kw)


def test_second_notice_expired(env, told):
    item = _replay(hold="turn-end", expires_s=0.01)
    time.sleep(0.05)
    run_player(env.d).join(5)
    row = said(env.logs)[item["utt"]]
    assert row["reason"] == "expired" and "detail" not in row
    m = told()[item["utt"]]
    assert m["Priority"] == "non-urgent"
    body = m.get_content()
    assert f'This was the replay of line abc123def456 ("{LINE}"); it is not replayed again.' in body
    assert "Dropped: the user's turn ran past 0.01 s, so it expired." in body
    _no_may(body)


def test_second_notice_asked_for_quiet(env, told, monkeypatch):
    _quiet(monkeypatch)
    item = _replay(hold="turn-end", expires_s=30)
    run_player(env.d).join(5)
    row = said(env.logs)[item["utt"]]
    assert row["reason"] == "expired" and row["detail"] == hold.QUIET
    m = told()[item["utt"]]
    assert m["Priority"] == "non-urgent"
    body = m.get_content()
    assert "line abc123def456" in body and "Dropped: the user asked for quiet." in body
    assert "ran past" not in body
    _no_may(body)


def test_second_notice_he_answered_it(env, told, monkeypatch, tmp_path):
    """EARS-365: his turn answered the cut question, so the replay is dropped and
    its second notice says so; the player runs end to end on fakes."""
    import json
    from datetime import datetime

    from ears import heard

    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm"))
    heard.log_dir().mkdir(parents=True, exist_ok=True)
    ts = datetime.now().astimezone().isoformat(timespec="milliseconds")
    with open(heard.log_path(), "a") as f:
        f.write(json.dumps({"ts": ts, "seq": 7, "kind": "turn", "source": "mic",
                            "text": "Yes, yeah the new sessionkit org"}) + "\n")
    for c in hold._caches.values():
        c.update(t=0.0, v=None)
    q = "Is that the 0.2.0 publish to the new sessionkit org? Or something later, like 1.0?"
    item = say(q, hold="turn-end", expires_s=30,
               extra={"resume_of": "abc123def456", "resume_text": q})
    run_player(env.d).join(5)
    row = said(env.logs)[item["utt"]]
    assert row["reason"] == "expired" and row["detail"] == hold.ANSWERED
    body = told()[item["utt"]].get_content()
    assert "line abc123def456" in body and "Dropped: the user answered it" in body
    assert "ran past" not in body and "asked for quiet" not in body
    _no_may(body)


def test_hold_check_says_why_a_quiet_resume_expired(monkeypatch, tmp_path):
    _quiet(monkeypatch)
    item = {"hold": "turn-end", "resume_of": "u1", "requested_t": time.time() - 2, "text": "x"}
    v = hold.check(item, directory=tmp_path)
    assert v == "expire" and v.detail == hold.QUIET            # still the plain word
    timed_out = {**item, "expires_s": 1.0}
    monkeypatch.setattr(hold, "asked_for_quiet", lambda *a, **kw: None)
    v = hold.check(timed_out, directory=tmp_path)
    assert v == "expire" and getattr(v, "detail", None) is None


def test_second_notice_replayed_in_full(env, told):
    item = _replay()
    run_player(env.d).join(5)
    row = said(env.logs)[item["utt"]]
    assert row["reason"] == "done" and row["notice"].startswith("sent ")
    m = told()[item["utt"]]
    assert m["Priority"] == "non-urgent" and m["Subject"] == 'replayed in full: "five six seven"'
    assert f'The replay of line abc123def456 ("{LINE}") was played in full.' in m.get_content()


def test_a_plain_line_played_in_full_still_sends_nothing(env, told):
    item = say("all of it")
    run_player(env.d).join(5)
    assert "notice" not in said(env.logs)[item["utt"]] and told() == {}


# -- R3 / SC3: once only -----------------------------------------------------------

def test_a_replay_cut_again_is_never_queued_again(env, told, resume_on):
    """player._plan returns won't for a line with resume_of (player.py, NEVER_TWICE),
    so _resume queues nothing: the queue is empty after the cut, and the notice says
    it was the replay and a replay is never replayed."""
    item = say(LINE, extra={"resume_of": "abc123def456", "resume_text": "the cut line"})
    th = run_player(env.d, idle=0.5)
    time.sleep(0.5)
    mouth.stop("barge-in", flush=True)
    th.join(10)
    rows = said(env.logs)
    assert rows[item["utt"]]["reason"] == "barge-in"
    assert rows[item["utt"]]["resume"] == "won't"
    assert rows[item["utt"]]["resume_why"] == player.NEVER_TWICE
    assert list(rows) == [item["utt"]]                        # nothing else was ever said
    assert _box.Queue(env.d).lines() == []                    # and nothing is queued
    m = told()[item["utt"]]
    assert m["Priority"] == "non-urgent"
    body = m.get_content()
    assert 'This was the replay of line abc123def456 ("the cut line"); it is not replayed again.' in body
    assert "The user's words cut the replay too. It will not come back: a replay is never replayed. " \
           "Re-say it only if it still matters." in body
    _no_may(body)


def test_resume_of_a_replay_queues_nothing_even_undecided(env, resume_on):
    """The once-only path with no decision on the row: _resume decides (once) and stops."""
    env.d.mkdir(parents=True, exist_ok=True)
    player._resume(env.d, {"utt": "r1", "text": "First part. Second part.", "device": "null",
                           "backend": "silence", "resume_of": "u1"},
                   {**CUT, "text_played_est": "First part. Sec"})
    assert _box.Queue(env.d).lines() == []
