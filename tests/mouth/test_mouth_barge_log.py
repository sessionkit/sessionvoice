"""The barge decision log (EARS-270 design.md s3): one ``kind: barge`` record
on player.log per partial `hold.barge` weighs while a line plays. Each record
says what was dropped and why, what counted, the count against the need, and
the verdict.

The log shows, from the words, what was heard, what was decided, and what
each word was checked against.

"""
from __future__ import annotations

import json
import time
from datetime import datetime

import pytest

from mouth import hold, player

from test_mouth import PullingSd, say  # noqa: F401
from test_mouth_barge_in import room  # noqa: F401  (fixture)
from test_mouth_inbox import box, lines, run_player  # noqa: F401  (fixture)
from test_mouth_onset import ears, played, _wait  # noqa: F401  (fixtures)

LINE = "the echo canceller is how FaceTime keeps your own voice out"
THEO = frozenset({"theo"})


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t).astimezone().isoformat(timespec="milliseconds")


def _barge(*a, **kw):
    """The real `hold.barge`, under the conftest's shadow."""
    return getattr(hold.barge, "shadows", hold.barge)(*a, **kw)


class Printed(list):
    """A BargeLog sink: what would go to player.log."""

    def __call__(self, rec):
        self.append(json.loads(json.dumps(rec)))


# -- barge() returns the same with and without trace= -------------------------

def test_every_barge_scenario_is_checked_with_and_without_a_trace():
    """conftest's `barge_traced` runs every hold.barge call that a mouth test
    makes, without and with trace=, and asserts the two agree. This covers
    every existing scenario in test_mouth_hold, test_mouth_barge_in,
    test_mouth_barge_turn_end, test_mouth_barge_ignore and test_mouth_onset."""
    assert getattr(hold.barge, "shadows", None) is not None


def test_tracing_never_changes_what_barge_returns(room):
    t0 = time.time() - 5
    cases = []
    for text, speaker in [("so the echo canceller keeps your own voice", None),   # echo
                          ("okay well", "theo"), ("FaceTime voice", None),        # ignored, own
                          ("trying", "other"), ("barge", None),                   # speaker, unnamed
                          ("happens now", "theo"), ("hang on", "other"), ("hang on", "theo")]:
        room(text, speaker=speaker)
        for kw in ({}, {"who": THEO}, {"words": 1}, {"ignore": frozenset()}, {"who": frozenset()}):
            now = time.time()
            got = _barge(t0, LINE, now=now, **kw)
            trace: list = []
            assert _barge(t0, LINE, now=now, trace=trace, **kw) == got, (text, kw)
            cases.append(got is not None)
    assert any(cases) and not all(cases)         # both outcomes were exercised


# -- one entry per branch ---------------------------------------------------------

def _trace(t0, line=LINE, **kw):
    trace: list = []
    got = _barge(t0, line, trace=trace, **kw)
    return got, trace


@pytest.mark.parametrize("need", [2, 3])
def test_each_word_is_counted_or_dropped_with_its_reason(room, monkeypatch, need):
    monkeypatch.setenv("MOUTH_BARGE_WORDS", str(need))   # pinned: not the default's
    t0 = time.time() - 5
    room("okay FaceTime trying")                         # ignored, own, counted
    got, trace = _trace(t0)
    assert got is None and len(trace) == 1
    e = trace[0]
    assert (e["text"], e["dropped"], e["counted"]) == ("okay FaceTime trying",
                                                      {"ignored": ["okay"], "own": ["facetime"]}, ["trying"])
    assert (e["count"], e["need"], e["verdict"]) == (1, need, "wait")
    assert isinstance(e["seq"], int) and e["ts"] and "skip" not in e and "speaker" not in e


def test_a_word_both_ignored_and_the_lines_own_is_ignored(room):
    t0 = time.time() - 5
    room("your keeps")                                   # "your": a stop word AND the line's
    _, trace = _trace(t0)
    assert trace[0]["dropped"] == {"ignored": ["your"], "own": ["keeps"]}


def test_the_cut_is_the_returned_partial_with_the_count_reaching_the_need(room):
    t0 = time.time() - 5
    room("trying to")
    room("barge in")
    room("happens and")
    got, trace = _trace(t0)
    assert got["text"] == "happens and"
    assert [(e["text"], e["count"], e["verdict"]) for e in trace] == [
        ("trying to", 1, "wait"), ("barge in", 2, "wait"), ("happens and", 3, "cut")]


def test_an_echo_partial_drops_every_word_as_echo(room):
    t0 = time.time() - 5
    room("the echo canceller keeps your own voice")
    _, trace = _trace(t0)
    assert trace[0]["skip"] == "echo"
    assert trace[0]["dropped"] == {"echo": ["the", "echo", "canceller", "keeps", "your", "own", "voice"]}
    assert trace[0]["counted"] == [] and trace[0]["verdict"] == "wait"


def test_under_who_an_unnamed_partial_waits_and_another_voice_is_dropped(room):
    t0 = time.time() - 5
    room("television words here", speaker="other")
    room("actually I need")                              # no label yet, inside the wait
    room("trying barge", speaker="theo")
    _, trace = _trace(t0, who=THEO)
    assert [e.get("skip") for e in trace] == ["speaker", "unnamed", None]
    assert trace[0]["speaker"] == "other" and trace[0]["dropped"] == {"speaker": ["television", "words", "here"]}
    assert trace[1]["dropped"] == {"unnamed": ["actually", "i", "need"]} and "speaker" not in trace[1]
    assert trace[2]["speaker"] == "theo" and trace[2]["counted"] == ["trying", "barge"]
    assert [e["count"] for e in trace] == [0, 0, 2]


def test_a_stop_ask_is_its_own_verdict(room):
    t0 = time.time() - 5
    room("trying")
    room("okay hang on")
    got, trace = _trace(t0)
    assert got["text"] == "okay hang on"
    e = trace[-1]
    assert e["verdict"] == "stop-ask" and e["asks"] == ["hang"]
    assert e["dropped"] == {"ignored": ["okay", "on"]} and e["counted"] == ["hang"] and e["count"] == 2
    assert trace[0]["verdict"] == "wait"


def test_the_ignore_list_given_is_the_one_reported(room):
    t0 = time.time() - 5
    room("okay well now")
    _, trace = _trace(t0, ignore=frozenset({"okay"}))
    assert trace[0]["dropped"] == {"ignored": ["okay"]} and trace[0]["counted"] == ["well", "now"]


def test_a_silent_poll_weighs_nothing_and_clears_the_trace(room, monkeypatch):
    t0 = time.time() - 5
    trace = [{"stale": True}]
    assert _barge(time.time() + 1, LINE, trace=trace) is None and trace == []     # nothing newer
    room("trying barge")
    with open(hold.heard.log_path(), "a") as f:                                   # his turn ended
        f.write(json.dumps({"ts": _iso(time.time()), "seq": 999, "kind": "turn",
                            "text": "trying barge", "source": "mic"}) + "\n")
    for c in hold._caches.values():
        c.update(t=0.0, v=None)
    trace.append({"stale": True})
    assert _barge(t0, LINE, trace=trace) is None and trace == []                  # the newest is no partial
    monkeypatch.setenv("MOUTH_BARGE", "0")
    trace.append({"stale": True})
    assert _barge(t0, LINE, trace=trace) is None and trace == []                  # barge off


# -- dedup (BargeLog) -----------------------------------------------------------------

def test_a_partial_weighed_on_many_polls_prints_once(room):
    t0 = time.time() - 5
    out = Printed()
    blog = player.BargeLog("u1", "default", log=out)
    room("okay trying")
    for _ in range(4):
        _, trace = _trace(t0)
        blog.emit(trace)
    room("barge now")
    for _ in range(3):
        _, trace = _trace(t0)
        blog.emit(trace)
    assert [r["text"] for r in out] == ["okay trying", "barge now"]
    assert all(r["kind"] == "barge" and r["utt"] == "u1" and r["ignore"] == "default" and r["at"]
               for r in out)
    assert out[1]["count"] == 2


def test_a_partial_that_gets_its_name_prints_again(room):
    """The label lands: the record changes from unnamed to counted, so it is
    printed a second time. This is by design (review A-5)."""
    t0 = time.time() - 5
    out = Printed()
    blog = player.BargeLog("u1", "setting", log=out)
    seq = room("actually I need the other one")
    blog.emit(_trace(t0, who=THEO)[1])
    blog.emit(_trace(t0, who=THEO)[1])
    room.label(seq, "theo")
    blog.emit(_trace(t0, who=THEO)[1])
    blog.emit(_trace(t0, who=THEO)[1])
    assert [(r["seq"], r.get("skip"), r.get("speaker")) for r in out] == [(seq, "unnamed", None),
                                                                            (seq, None, "theo")]
    assert out[1]["counted"] == ["actually", "need", "one"] and out[1]["ignore"] == "setting"


def test_a_moving_line_window_reprints_a_partial_it_reclassifies(room):
    t0 = time.time() - 5
    out = Printed()
    blog = player.BargeLog("u1", log=out)
    room("canceller trying")
    blog.emit(_trace(t0, line="the weather on the coast")[1])        # canceller counts
    blog.emit(_trace(t0, line=LINE)[1])                               # near() moved: now the line's
    assert [(r["dropped"], r["counted"]) for r in out] == [
        ({}, ["canceller", "trying"]), ({"own": ["canceller"]}, ["trying"])]


def test_a_record_without_seq_is_keyed_by_ts_and_text():
    out = Printed()
    blog = player.BargeLog("u1", log=out)
    e = {"seq": None, "ts": "t", "text": "a", "dropped": {}, "counted": ["a"], "count": 1, "need": 3,
         "verdict": "wait"}
    blog.emit([e])
    blog.emit([dict(e)])
    blog.emit([{**e, "text": "b"}])
    assert [r["text"] for r in out] == ["a", "b"]


# -- the 08:00 replay (EARS-274 findings, copied in as literals) ------------------

# The two lines played on 2030-06-09, and his partials over each. These are
# from EARS-274 findings/2030-06-09-line-texts.md and its word-path findings
# (seq, ts, text), with every ts moved by ONE constant onto a synthetic
# 2030-12-08 08:00 base (EARS-393): each delta, the ms and the order as recorded.
L1 = ("Go for it. I'll keep talking for a while so you've got something to cut into. Here's some "
      "filler: the little man the magpie came by today, or maybe he didn't, I wouldn't know, I'm on "
      "a laptop under a table at eight in the morning, and nobody tells me anything about "
      "birds. Kuka the kookaburra would have something to say about that. Anyway, any time now, just "
      "start talking over me and see how fast I duck.")
L2 = L1[L1.index("Here's"):]
LINE1 = ("2030-12-08T08:00:36.478+11:00", L1, [
    (448238, "2030-12-08T08:00:40.606+11:00", "Okay well"),
    (448239, "2030-12-08T08:00:41.147+11:00", "I'm just trying"),
    (448240, "2030-12-08T08:00:41.686+11:00", "to"),
    (448241, "2030-12-08T08:00:42.226+11:00", "barge in now"),
    (448242, "2030-12-08T08:00:42.786+11:00", "so we'll see"),
    (448243, "2030-12-08T08:00:43.367+11:00", "what"),
    (448244, "2030-12-08T08:00:44.467+11:00", "happens and I'm"),
])
LINE2 = ("2030-12-08T08:00:57.289+11:00", L2, [
    (448259, "2030-12-08T08:01:01.186+11:00", "Okay, so now"),
    (448260, "2030-12-08T08:01:01.246+11:00", "I'm"),
    (448261, "2030-12-08T08:01:01.666+11:00", "saying some more"),
    (448263, "2030-12-08T08:01:02.266+11:00", "words and"),
    (448264, "2030-12-08T08:01:02.806+11:00", "I'm"),
    (448265, "2030-12-08T08:01:03.366+11:00", "speaking"),
])


def _replay(tmp_path, line):
    """Write his partials one at a time into a scratch heard log, poll barge()
    after each as the player does (0.18 s later, as measured), and run every
    trace through one BargeLog. Returns (the poll that cut, the records)."""
    start, text, partials = line
    d = tmp_path / "replay"
    d.mkdir()
    log = d / "heard_2030-12-08.jsonl"
    since = datetime.fromisoformat(start).timestamp()
    out = Printed()
    blog = player.BargeLog("replay", "default", log=out)
    for seq, ts, said in partials:
        with open(log, "a") as f:
            f.write(json.dumps({"ts": ts, "seq": seq, "kind": "partial", "text": said,
                                "source": "mic"}) + "\n")
        now = datetime.fromisoformat(ts).timestamp() + 0.18
        trace: list = []
        # The whole line text stands in for near(): EARS-274's replay found no
        # line-own words in either cut, so the window cannot change the result.
        got = _barge(since, text, directory=d, now=now, words=3, trace=trace)
        blog.emit(trace)
        if got is not None:
            return got, out
    return None, out


@pytest.mark.parametrize("line, words, cut_on", [
    (LINE1, ["trying", "barge", "happens"], "happens and I'm"),
    (LINE2, ["saying", "words", "speaking"], "speaking"),
])
def test_the_0800_replay_explains_the_cut(ears, tmp_path, line, words, cut_on):
    """The records explain both cuts. The assertions are on the counted words,
    the verdicts and the count. "to" and "I'm" are dropped as echo here (one
    word, and it is in the line), and the test does not assert on those skips
    (review A-4): the cut does not depend on them."""
    got, out = _replay(tmp_path, line)
    assert got is not None and got["text"] == cut_on
    counted = [w for r in out for w in r["counted"]]
    assert counted == words
    assert [r["verdict"] for r in out if r["verdict"] != "wait"] == ["cut"]
    cut = out[-1]
    assert (cut["text"], cut["verdict"], cut["count"], cut["need"]) == (cut_on, "cut", 3, 3)
    assert len({r["seq"] for r in out}) == len(out)               # no partial printed twice
    assert [r["seq"] for r in out] == [s for s, _, _ in line[2]]  # every partial weighed, once
    for r in out:
        assert set(r["counted"]) <= set(words)
        assert r["dropped"].keys() <= {"ignored", "echo"}          # never the line's own


def test_the_0800_line_one_records_read_as_the_replay_did(ears, tmp_path):
    _, out = _replay(tmp_path, LINE1)
    by = {r["text"]: r for r in out}
    assert by["Okay well"]["dropped"] == {"ignored": ["okay", "well"]} and by["Okay well"]["count"] == 0
    assert by["I'm just trying"]["dropped"] == {"ignored": ["i", "m", "just"]}
    assert by["barge in now"]["dropped"] == {"ignored": ["in", "now"]} and by["barge in now"]["count"] == 2
    assert by["so we'll see"]["dropped"] == {"ignored": ["so", "we", "ll", "see"]}
    assert by["happens and I'm"]["dropped"] == {"ignored": ["and", "i", "m"]}


# -- the player: real play_one / check_stop on a fake device ----------------------

def _records(text: str) -> list:
    out = []
    for raw in text.splitlines():
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _cut_line(played, monkeypatch):
    """A line cut by three words of his. The stop is marked on the same
    stdout, so the order of the cut record and the stop can be read."""
    real = player._write_stop

    def write_stop(d, rec):
        player._log({"test_stop_written": rec.get("reason")})
        return real(d, rec)

    monkeypatch.setattr(player, "_write_stop", write_stop)
    played.ears.settings.write_text(json.dumps({"barge_duck_level": 100}))
    item = say("a line long enough to talk over, for a while longer than that",
               device="MacBook Pro Speakers")
    th = run_player(played.d)
    assert _wait(lambda: any(r["kind"] == "saying" and r["utt"] == item["utt"]
                             for r in lines(played.logs)))
    played.ears.partial("okay trying")
    time.sleep(0.3)
    played.ears.partial("barge happens")
    th.join(8)
    assert not th.is_alive()
    said = next(r for r in lines(played.logs) if r["kind"] == "said" and r["utt"] == item["utt"])
    return item, said


@pytest.mark.parametrize("need", [2, 3])
def test_the_player_prints_the_records_and_the_cut_before_the_stop(played, monkeypatch, capsys, need):
    monkeypatch.setenv("MOUTH_BARGE_WORDS", str(need))   # pinned: not the default's
    item, said = _cut_line(played, monkeypatch)
    assert (said["reason"], said["cut"]) == ("barge-in", True)
    recs = _records(capsys.readouterr().out)
    barge = [r for r in recs if r.get("kind") == "barge"]
    assert [r["text"] for r in barge] == ["okay trying", "barge happens"]
    assert all(r["utt"] == item["utt"] and r["ignore"] == "default" for r in barge)
    assert barge[0]["counted"] == ["trying"] and barge[0]["verdict"] == "wait"
    assert (barge[-1]["verdict"], barge[-1]["count"], barge[-1]["need"]) == ("cut", 3, need)
    cut_i = recs.index(barge[-1])
    stop_i = next(i for i, r in enumerate(recs) if r.get("test_stop_written") == "barge-in")
    assert cut_i < stop_i                                         # the cut record precedes the stop


def test_a_failing_log_never_changes_the_cut(played, monkeypatch, capsys):
    def boom(self, trace):
        raise RuntimeError("player.log is gone")

    monkeypatch.setattr(player.BargeLog, "emit", boom)
    item, said = _cut_line(played, monkeypatch)
    assert (said["reason"], said["cut"]) == ("barge-in", True)    # exactly as today
    assert "player.log is gone" not in str(said.get("detail"))
    assert not [r for r in _records(capsys.readouterr().out) if r.get("kind") == "barge"]


def test_a_setting_in_force_is_named_in_the_records(played, monkeypatch, capsys):
    played.ears.settings.write_text(json.dumps({"barge_duck_level": 100, "barge_ignore_words": ["okay"]}))
    item = say("a line long enough to talk over, for a while longer than that",
               device="MacBook Pro Speakers")
    th = run_player(played.d)
    assert _wait(lambda: any(r["kind"] == "saying" and r["utt"] == item["utt"]
                             for r in lines(played.logs)))
    played.ears.partial("okay so well now")           # with only "okay" ignored: 3 words count
    th.join(8)
    barge = [r for r in _records(capsys.readouterr().out) if r.get("kind") == "barge"]
    assert barge and barge[-1]["ignore"] == "setting" and barge[-1]["verdict"] == "cut"
    assert barge[-1]["dropped"] == {"ignored": ["okay"]} and barge[-1]["counted"] == ["so", "well", "now"]
