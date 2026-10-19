"""EARS-192: barge-in must not cut a line on words the recogniser hears late.

Measured Sun 2030-06-09 (heard_2030-06-09.jsonl, one AirPods capture of
929 s): a partial's emit ``ts`` minus its audio time grew from 0.2-1.0 s to
64-80 s (857 s into the capture) and 102-108 s (48 s later). The recogniser
had fallen behind and flushed its backlog in one burst, 11 partials in 22 ms,
while a mouth line was playing and the user was silent. Barge-in timed each partial
by its ARRIVAL ``ts`` (hold.py ``_ts``), so words he said a minute and a half
earlier cut four lines while he was not
speaking.

How a record's audio maps to wall time on master 138484c (no new field yet):
``t0``/``t1`` on a partial or turn are seconds of STREAM audio, counted from
the capture's first frame (loop.py ``t_frame = n * FRAME_S``). The capture
writes ``listen-started`` before it opens the device and reads the first frame,
so a record's audio wall time is the ``ts`` of the newest ``listen-started`` of
the same source before it, plus ``t1``. Its recogniser lag is its ``ts`` minus
that. The fixture below writes records in exactly that shape, the shape of the
10-07 log, which is the log the replay criterion names.

These tests replay that shape. The stale burst must cut NOTHING (it fails on
master 138484c); the control, fresh words inside the line, must still cut.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Optional

import pytest

from ears import heard, lag
import mouth
from mouth import hold

from test_mouth_inbox import box, lines, run_player  # noqa: F401  (fixture)
from tests import env_fence

#: The lags of the 10-07 burst, seconds from the audio to the record's ts:
#: 64-80 s at 857 s into the capture, 102-108 s 48 s later (11 partials, as measured).
STALE_LAGS = (64.0, 67.5, 71.0, 74.0, 77.5, 80.0, 102.0, 103.5, 105.0, 106.5, 108.0)
#: The burst's spread: 11 partials landed inside 22 ms.
BURST_S = 0.022
#: How long the capture had been running when the burst landed (~857 s, as
#: measured); only needs to be longer than the biggest lag.
CAPTURE_AGE_S = 857.0
#: Words he said earlier: content words, none of them the line's, and one stop
#: ask - each would cut a line on its own were it fresh.
STALE_TEXTS = ("so the build", "failed again on", "the second runner", "check the",
               "logs for me", "and then", "hang on", "push the fix", "to the branch",
               "once it", "passes cleanly")

LINE = "the echo canceller is how FaceTime keeps your own voice out of the call"
MID = "one two three four five six seven eight nine ten " * 2    # ~7 s on the silence backend


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t).astimezone().isoformat(timespec="milliseconds")


class Ears:
    """Writes heard records as the ears do on master: ``ts`` is when the record
    was written; ``t0``/``t1`` are seconds of audio since the capture began,
    whose wall start is its ``listen-started`` ``ts``."""

    def __init__(self) -> None:
        self.seq = 0
        self.start_t = None

    def _put(self, rec: dict) -> dict:
        self.seq += 1
        rec = {"seq": self.seq, "source": "mic", "device": "airpods", **rec}
        with open(heard.log_path(), "a") as f:
            f.write(json.dumps(rec) + "\n")
        for c in hold._caches.values():
            c.update(t=0.0, v=None)
        return rec

    def started(self, at: float) -> dict:
        self.start_t = at
        return self._put({"ts": _iso(at), "kind": "event", "event": heard.EV_LISTEN_STARTED,
                          "detector": "silero", "stt": "nemotron", "partials": "nemotron"})

    def partial(self, text: str, ts: float, audio_t1: float, dur_s: float = 0.6,
                stamp: Optional[float] = None) -> dict:
        """A partial written at wall ``ts`` whose audio ended at wall ``audio_t1``.
        ``stamp``: also write fix-001's ``audio_ts``/``lag_s`` for audio ending at
        that wall time (which may disagree with ``t1``, to show which one wins)."""
        t1 = audio_t1 - self.start_t
        extra = lag.stamp(stamp, ts) if stamp is not None else {}
        return self._put({"ts": _iso(ts), "kind": "partial", "text": text, "final": False,
                          "t0": round(max(0.0, t1 - dur_s), 3), "t1": round(t1, 3),
                          "engine": "nemotron", **extra})

    def turn(self, text: str, ts: float) -> dict:
        return self._put({"ts": _iso(ts), "kind": "turn", "text": text, "final": True})


@pytest.fixture
def ears(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm"))
    env_fence.delenv(monkeypatch, "MOUTH_BARGE")
    env_fence.delenv(monkeypatch, "MOUTH_BARGE_WORDS")
    heard.log_dir().mkdir(parents=True, exist_ok=True)
    for c in hold._caches.values():
        c.update(t=0.0, v=None)
    e = Ears()
    e.started(time.time() - CAPTURE_AGE_S)
    return e


#: The same burst with no stop ask: only the word count could cut on it.
STALE_WORDS_ONLY = tuple("and so on" if t == "hang on" else t for t in STALE_TEXTS)


def _stale_burst(e: Ears, now: float, texts: tuple = STALE_TEXTS) -> list:
    """The 10-07 backlog: 11 partials written inside 22 ms ending at ``now``,
    each heard ``STALE_LAGS`` seconds before it was written."""
    step = BURST_S / (len(texts) - 1)
    out = []
    for i, (text, lag) in enumerate(zip(texts, STALE_LAGS)):
        ts = now - BURST_S + i * step
        out.append(e.partial(text, ts, ts - lag))
    return out


def _audio_wall(e: Ears, rec: dict) -> float:
    return e.start_t + rec["t1"]


@pytest.mark.parametrize("texts", [STALE_TEXTS, STALE_WORDS_ONLY], ids=["stop-ask", "words-only"])
@pytest.mark.parametrize("line_age_s", [3.0, 90.0], ids=["line-3s", "line-90s"])
def test_a_stale_burst_does_not_cut_the_line(ears, line_age_s, texts):
    """The replay: the user silent, a line playing, the recogniser's backlog lands
    in one burst. ZERO cuts. ``line-3s``: every word was said before the line
    began. ``line-90s``: a long line, so the 64-80 s words fall inside it and
    the 102-108 s ones before it, and only a freshness bound tells them apart.
    ``stop-ask`` carries a stale "hang on" (cuts at once); ``words-only`` has
    none, so only the word count could cut."""
    now = time.time()
    line_start = now - line_age_s - BURST_S
    burst = _stale_burst(ears, now, texts)
    lags = [hold._ts(p) - _audio_wall(ears, p) for p in burst]
    assert min(lags) >= 63.9 and max(lags) <= 108.1, lags            # the fixture is the measured shape
    assert all(hold._ts(p) > line_start for p in burst)              # they ARRIVE during the line
    trace: list = []
    got = hold.barge(line_start, LINE, now=now, trace=trace)
    assert got is None, (
        f"barge-in cut a {line_age_s:.0f} s old line on words heard "
        f"{min(lags):.0f}-{max(lags):.0f} s before they arrived: {got.get('text')!r}; "
        f"trace verdicts {[(e.get('text'), e.get('verdict')) for e in trace]}")


def test_a_fresh_interruption_still_cuts(ears):
    """The control: his words heard inside the line, with the recogniser keeping
    up (0.2-1.0 s behind, as 275-455 s into the capture), cut it."""
    now = time.time()
    line_start = now - 3.0
    ears.partial("hang on a", now - 0.4, now - 0.9)
    ears.partial("second there", now, now - 0.3)
    got = hold.barge(line_start, LINE, now=now)
    assert got is not None and got["text"] in ("hang on a", "second there")


def test_fresh_words_after_a_stale_burst_still_cut(ears):
    """The backlog must not blind barge-in: once the stale burst is in the log,
    fresh content words of his inside the line still cut, and the cut names
    the FRESH partial. (On master 138484c this fails too: the stale "hang on"
    cuts first.)"""
    now = time.time()
    line_start = now - 5.0
    _stale_burst(ears, now - 1.0)
    ears.partial("I need you", now - 0.3, now - 0.8)
    ears.partial("to listen now", now, now - 0.2)
    got = hold.barge(line_start, LINE, now=now)
    assert got is not None, "fresh words after a stale burst did not cut"
    assert got["text"] == "to listen now", f"the cut was on a stale partial: {got['text']!r}"


@pytest.fixture
def headphones(monkeypatch):
    monkeypatch.setenv("MOUTH_BARGE_DEVICES", "null")   # the null device stands in
    # a cut line mails its caller a notice, and the caller is this test's
    # session: keep a test run from mailing a live seat's box
    monkeypatch.setenv("MOUTH_NOTICE", "off")


def _wait_for(pred, s=5.0):
    end = time.time() + s
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_the_player_is_not_cut_by_a_stale_burst(box, ears, headphones):
    """End to end through the player's barge poll: a line is playing, the burst
    lands, and the line plays to its end."""
    mouth.say(MID, d=box.d, spawn=False)
    th = run_player(box.d, idle=0.3)
    assert _wait_for(lambda: any(x["kind"] == "saying" for x in lines(box.logs))), "the line never started"
    time.sleep(0.3)
    _stale_burst(ears, time.time())
    th.join(15)
    said = [(x.get("reason"), x.get("heard")) for x in lines(box.logs) if x["kind"] == "said"]
    assert [r for r, _ in said] == ["done"], f"the line was cut by a stale burst: {said}"


# -- fix-002: the bound, the fallbacks, the trace, and the held line ---------
# (rca-001 section 7, as ruled in findings/2030-06-09-foreman-rulings.md)


@pytest.mark.parametrize("stamped", [False, True], ids=["listen-started", "audio_ts"])
@pytest.mark.parametrize("late_s,cuts", [(1.2, True), (4.5, False)], ids=["lag-1.2s", "lag-4.5s"])
def test_the_freshness_bound_at_both_edges(ears, late_s, cuts, stamped):
    """BARGE_FRESH_S = 4.0: a stop ask said inside the line and delivered 1.2 s
    late (the healthy ceiling) still cuts; one delivered 4.5 s late does not.
    The same through either source of audio time: the capture's
    ``listen-started`` plus ``t1`` (old ears), or fix-001's own stamp."""
    now = time.time()
    line_start = now - 10.0
    said = now - late_s
    ears.partial("hang on", now, said, stamp=said if stamped else None)
    got = hold.barge(line_start, LINE, now=now)
    assert (got is not None) is cuts, (late_s, got)


def test_the_records_own_stamp_wins_over_listen_started(ears):
    """fix-001's ``audio_ts`` is the record's own word on when it was said:
    ``t1`` says fresh, the stamp says 90 s late, and the stamp decides."""
    now = time.time()
    ears.partial("hang on", now, now - 0.3, stamp=now - 90.0)
    assert hold.barge(now - 10.0, LINE, now=now) is None
    assert hold._heard_t(json.loads(heard.log_path().read_text().splitlines()[-1])) \
        == pytest.approx(now - 90.0, abs=0.01)


def test_said_before_the_line_does_not_cut_even_when_delivered_in_time(ears):
    """Both terms: a partial delivered 1 s late, but said before the line's first
    frame, is not talk-over."""
    now = time.time()
    line_start = now - 0.5
    ears.partial("hang on", now, now - 1.0)
    assert hold.barge(line_start, LINE, now=now) is None


def test_no_listen_started_in_the_tail_falls_back_to_arrival(ears):
    """Safe degrade (Q6): an old-ears record with no stamp whose capture start is
    not in the tail has no audio time, so it is judged by arrival, today's
    behaviour. The 10-07 burst then still cuts: never worse than before."""
    heard.log_path().write_text("")                   # the listen-started is gone
    now = time.time()
    _stale_burst(ears, now)
    assert hold.barge(now - 3.0, LINE, now=now) is not None


def test_a_listen_started_for_another_device_is_not_this_records_start(ears):
    """The join is source AND device: a start on another device never times
    this record (it falls back to arrival)."""
    heard.log_path().write_text("")
    now = time.time()
    with open(heard.log_path(), "a") as f:
        f.write(json.dumps({"seq": 99, "ts": _iso(now - 200.0), "kind": "event",
                            "event": heard.EV_LISTEN_STARTED, "source": "mic",
                            "device": "macbook"}) + "\n")
    ears.partial("hang on", now, now - 100.0)
    assert hold._partials_since(now - 3.0)[0][1] is None, "judged by another capture's start"


def test_the_trace_says_stale_with_the_lag(ears):
    """Every dropped partial gets a trace entry ``skip: stale`` with its
    ``lag_s`` (EARS-270's ``kind: barge`` record then says why the burst did not
    count), and no verdict cuts."""
    now = time.time()
    burst = _stale_burst(ears, now)
    trace: list = []
    assert hold.barge(now - 3.0, LINE, now=now, trace=trace) is None
    assert [e.get("skip") for e in trace] == ["stale"] * len(burst)
    assert [e["seq"] for e in trace] == [p["seq"] for p in burst]
    for e, want in zip(trace, STALE_LAGS):
        assert e["lag_s"] == pytest.approx(want, abs=0.01)
        assert e["counted"] == [] and e["verdict"] == "wait"
    assert hold.cut_words(trace) == ([], None)


def test_the_bound_is_a_setting(ears, monkeypatch):
    """BARGE_FRESH_S (4.0, above the ears' 2 s lag alarm) is read through
    $MOUTH_BARGE_FRESH_S; a bad value keeps the default. Raised past
    the burst's 108 s, the burst inside a 90 s line's span counts again."""
    assert hold.BARGE_FRESH_S == 4.0 and hold.barge_fresh_s() == 4.0
    assert hold.BARGE_FRESH_S > lag.LAG_WARN_S        # every dropped word follows an alarm
    monkeypatch.setenv("MOUTH_BARGE_FRESH_S", "not a number")
    assert hold.barge_fresh_s() == 4.0
    monkeypatch.setenv("MOUTH_BARGE_FRESH_S", "200")
    now = time.time()
    _stale_burst(ears, now)
    assert hold.barge(now - 90.0, LINE, now=now) is not None


def test_a_stale_quiet_ask_does_not_expire_a_resume(ears):
    """rca path 5 follows path 2: a stale "quiet" is not his ask."""
    now = time.time()
    ears.partial("be quiet", now, now - 80.0)
    assert hold.asked_for_quiet(now - 3.0) is None
    ears.partial("be quiet", now + 0.01, now - 0.3)
    assert hold.asked_for_quiet(now - 3.0) is not None


# rca path 6: _scan / floor / check judge his turn by audio time too.


def test_a_stale_burst_does_not_read_as_speaking(ears):
    """The burst's newest words were said 64 s ago: older than idle_s (8 s), so
    the floor is free, and the bed is not ducked under it."""
    now = time.time()
    _stale_burst(ears, now)
    assert hold.floor(now=now, max_age_s=0.0) == "free"
    assert hold.speaking_over("airpods", now=now, who=None) is None


def test_a_stale_burst_does_not_file_a_held_line_stale(ears):
    """His last real turn ended, an agent queued a turn-end opener, then the
    recogniser's backlog (words said BEFORE the opener) landed. On master the
    burst read as a new turn begun after the opener, so it was filed ``stale``
    and never spoken. Now it plays."""
    now = time.time()
    ears.partial("what do you think", now - 120.0, now - 120.5)
    ears.turn("what do you think", now - 118.0)
    item = {"hold": "turn-end", "device": "airpods", "requested_t": now - 30.0}
    _stale_burst(ears, now)
    assert hold.check(item, now=now) == "play"


def test_a_late_turn_begins_when_he_spoke_not_when_it_arrived(ears):
    """Chronology by audio: he began 5 s ago, the opener was queued 2 s ago, his
    words arrived just now (5 s late). He began BEFORE the opener, so it is not
    stale: it waits for his turn to end."""
    now = time.time()
    ears.turn("earlier", now - 60.0)
    item = {"hold": "turn-end", "device": "airpods", "requested_t": now - 2.0}
    ears.partial("so what about", now, now - 5.0)
    assert hold.floor(now=now, max_age_s=0.0) == "speaking"
    assert hold.check(item, now=now) == "wait"


def test_a_held_line_queued_before_he_really_began_is_still_stale(ears):
    """The control: fresh words he began AFTER the opener was queued still make
    it old news (chronology is important)."""
    now = time.time()
    ears.turn("earlier", now - 60.0)
    item = {"hold": "turn-end", "device": "airpods", "requested_t": now - 5.0}
    ears.partial("actually wait", now, now - 0.4)
    assert hold.check(item, now=now) == "stale"
