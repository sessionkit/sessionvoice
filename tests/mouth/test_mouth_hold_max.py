"""A turn-end hold has a ceiling, ``hold_max_s`` (EARS-531).

A seat queued ``mouth say --hold turn-end --expires 300`` in a busy room: two
people talked for 193 s with no pause the turn stage counted as an end, and the
line played 168 s after it was queued. Now a held line waits at most
``hold_max_s`` (12 s) from when it was queued; past it, it plays at the next gap
in the user's words (no new partial for ``HOLD_GAP_S``), or once
``HOLD_GAP_WINDOW_S`` more has passed with no gap.

The order: $MOUTH_HOLD_MAX_S > ``hold_max_s`` in the mouth's settings > 12 s,
as ``room_hold``'s. 0 (or ``off`` in the env) is no ceiling.

The ears are faked by writing partial/turn records into a scratch heard log
(the ``ears`` fixture); a record with no audio time is aged by its ``ts``.
"""
from __future__ import annotations

import json
import math
import threading
import time
from datetime import datetime

import pytest

from ears import heard
import mouth
from mouth import hold

from test_mouth_hold import ears, _saying, _wait_for  # noqa: F401  (fixture)
from test_mouth_inbox import box, lines, run_player  # noqa: F401  (fixture)
from tests import env_fence

CEIL = hold.HOLD_MAX_S
GAP = hold.HOLD_GAP_S
WINDOW = hold.HOLD_GAP_WINDOW_S


@pytest.fixture(autouse=True)
def settings(tmp_path, monkeypatch):
    """The mouth's settings file, in tmp, empty; and no runner's $MOUTH_HOLD_MAX_S."""
    path = tmp_path / "mouth.json"
    monkeypatch.setenv("MOUTH_SETTINGS", str(path))
    env_fence.delenv(monkeypatch, "MOUTH_HOLD_MAX_S")

    def write(**keys):
        path.write_text(json.dumps(keys))
    return write


def long_turn(now: float, span_s: float = 200.0, every_s: float = 1.0, newest_age_s: float = 0.0):
    """One unbroken turn: a worded partial every ``every_s`` from ``span_s`` ago up
    to ``newest_age_s`` ago, and no ``turn`` row. Written oldest first, in one go."""
    with open(heard.log_path(), "a") as f:
        t = now - span_s
        while t <= now - newest_age_s + 1e-9:
            ts = datetime.fromtimestamp(t).astimezone().isoformat(timespec="milliseconds")
            f.write(json.dumps({"ts": ts, "seq": 1, "kind": "partial", "text": "talk",
                                "source": "mic"}) + "\n")
            t += every_s
    for c in hold._caches.values():
        c.update(t=0.0, v=None)


def sender(age_s: float, now: float, **extra) -> dict:
    return {"hold": "turn-end", "requested_t": now - age_s, **extra}


def room(age_s: float, now: float, **extra) -> dict:
    return {"hold": "turn-end", "hold_from": hold.ROOM, "requested_t": now - age_s, **extra}


# -- the default ceiling -------------------------------------------------------------

def test_the_default_is_twelve_seconds():
    assert CEIL == 12.0 and hold.hold_max_s({}) == (12.0, "default")
    assert 0 < GAP < hold.IDLE_S and 0 < WINDOW < hold.IDLE_S


def test_the_measured_case_a_168_s_hold_now_plays(ears):
    """The repro: one 193 s turn, a line queued 168 s ago with --expires 300.
    It waited the whole turn; now it plays at the first gap past the ceiling,
    and with no gap at all once the window has passed."""
    now = time.time()
    long_turn(now, span_s=193, every_s=1.5, newest_age_s=GAP)            # a gap right now
    for make in (sender, room):
        assert hold.check(make(168, now, expires_s=300), now=now) == "play"
    hold._cache.update(t=0.0, v=None)
    now2 = time.time()
    long_turn(now2, span_s=1, every_s=0.5)                               # words now: no gap
    for make in (sender, room):
        assert hold.check(make(168, now2, expires_s=300), now=now2) == "play"


@pytest.mark.parametrize("make", [sender, room], ids=["sender-held", "room-held"])
def test_before_the_ceiling_a_long_turn_still_waits(ears, make):
    now = time.time()
    long_turn(now, newest_age_s=GAP)                                     # even in a gap
    assert hold.check(make(1, now), now=now) == "wait"
    assert hold.check(make(CEIL - 0.1, now), now=now) == "wait"


@pytest.mark.parametrize("make", [sender, room], ids=["sender-held", "room-held"])
def test_past_the_ceiling_it_plays_at_the_next_gap(ears, make):
    """Words still flowing (the newest partial younger than HOLD_GAP_S): it waits.
    The newest partial HOLD_GAP_S old (a gap): it plays."""
    now = time.time()
    long_turn(now)                                                       # newest: now
    item = make(CEIL + 0.5, now)
    assert hold.floor(now=now) == "speaking"
    assert hold.check(item, now=now) == "wait"
    assert hold.check(item, now=now + GAP - 0.1) == "wait"
    later = now + GAP                                                    # no partial since
    assert hold.floor(now=later) == "speaking", "a gap is shorter than idle_s: still mid-turn"
    assert hold.check(item, now=later) == "play"


@pytest.mark.parametrize("make", [sender, room], ids=["sender-held", "room-held"])
def test_past_the_ceiling_with_no_gap_it_plays_after_the_window(ears, make):
    now = time.time()
    long_turn(now, every_s=0.5)                                          # newest: now
    assert hold.check(make(CEIL + WINDOW - 0.1, now), now=now) == "wait"
    assert hold.check(make(CEIL + WINDOW, now), now=now) == "play"
    assert hold.check(make(CEIL + WINDOW + 60, now), now=now) == "play"


def test_a_short_turn_still_releases_at_its_end(ears):
    now = time.time()
    ears("partial", age_s=2)
    ears("partial")
    assert hold.check(sender(1, now), now=now) == "wait"
    assert hold.check(room(1, now), now=now) == "wait"
    ears("turn")
    assert hold.check(sender(1, time.time())) == "play"
    assert hold.check(room(1, time.time())) == "play"


def test_a_sender_opener_from_before_the_turn_is_still_stale_past_the_ceiling(ears):
    """The stale rule is unchanged: a sender opener queued before the user's
    turn BEGAN is old news, however long it has waited. A room-held line is
    not an opener: past the ceiling it plays."""
    ears("turn", age_s=60)
    now = time.time()
    long_turn(now, span_s=40, newest_age_s=GAP)                          # began 40 s ago
    assert hold.check(sender(50, now), now=now) == "stale"
    assert hold.check(room(50, now), now=now) == "play"


def test_a_line_with_no_requested_t_waits_as_before(ears):
    ears("partial")
    assert hold.check({"hold": "turn-end"}) == "wait"
    assert hold.past_hold_max(None, time.time()) is False


def test_expires_still_counts_from_the_end_of_the_turn(ears):
    """A line the ceiling releases mid-turn is not expired by an --expires that
    has run since it was queued: a held line's clock starts at the turn's end."""
    now = time.time()
    long_turn(now, newest_age_s=GAP)
    assert hold.check(sender(CEIL + 1, now, expires_s=5), now=now) == "play"


def test_an_unheld_line_is_untouched(ears):
    now = time.time()
    long_turn(now)
    assert hold.check({"requested_t": now - 600}, now=now) == "play"


# -- the layers: env > settings > default --------------------------------------------

def test_the_setting_overrides_the_default(ears, settings):
    settings(hold_max_s=30)
    assert hold.hold_max_s() == (30.0, "settings")
    now = time.time()
    long_turn(now, newest_age_s=GAP)
    assert hold.check(sender(20, now), now=now) == "wait"
    assert hold.check(sender(31, now), now=now) == "play"
    settings(hold_max_s=2.5)
    assert hold.check(sender(3, now), now=now) == "play"


def test_the_env_overrides_the_default(ears, monkeypatch):
    monkeypatch.setenv("MOUTH_HOLD_MAX_S", "4")
    assert hold.hold_max_s() == (4.0, "env")
    now = time.time()
    long_turn(now, newest_age_s=GAP)
    assert hold.check(sender(3, now), now=now) == "wait"
    assert hold.check(sender(4.5, now), now=now) == "play"


def test_the_env_wins_over_the_setting(ears, settings, monkeypatch):
    settings(hold_max_s=60)
    monkeypatch.setenv("MOUTH_HOLD_MAX_S", "4")
    assert hold.hold_max_s() == (4.0, "env")
    now = time.time()
    long_turn(now, newest_age_s=GAP)
    assert hold.check(sender(5, now), now=now) == "play"
    monkeypatch.setenv("MOUTH_HOLD_MAX_S", "")                        # empty: as if unset
    assert hold.hold_max_s() == (60.0, "settings")
    assert hold.check(sender(5, now), now=now) == "wait"


@pytest.mark.parametrize("env", ["off", "0", "no", "false", "OFF", " 0 "])
def test_the_env_can_turn_the_ceiling_off(ears, settings, monkeypatch, env):
    settings(hold_max_s=4)
    monkeypatch.setenv("MOUTH_HOLD_MAX_S", env)
    assert hold.hold_max_s() == (math.inf, "env")
    now = time.time()
    long_turn(now, every_s=0.5)                                          # began 200 s ago
    assert hold.check(sender(150, now), now=now) == "wait"
    assert hold.check(room(600, now), now=now) == "wait"


def test_a_zero_setting_is_no_ceiling(ears, settings):
    settings(hold_max_s=0)
    assert hold.hold_max_s() == (math.inf, "settings")
    now = time.time()
    long_turn(now, every_s=0.5)
    assert hold.check(room(600, now), now=now) == "wait"


@pytest.mark.parametrize("bad", [-1, "12", True, None, [12]])
def test_an_unreadable_setting_is_as_if_unset(settings, bad):
    settings(hold_max_s=bad)
    assert hold.hold_max_s() == (CEIL, "default")


@pytest.mark.parametrize("bad", ["soon", "-3", "nan"])
def test_an_unreadable_env_falls_through(settings, monkeypatch, bad):
    settings(hold_max_s=20)
    monkeypatch.setenv("MOUTH_HOLD_MAX_S", bad)
    assert hold.hold_max_s() == (20.0, "settings")


# -- the player ---------------------------------------------------------------------

def test_the_player_speaks_a_held_line_inside_a_turn_that_never_ends(box, ears, monkeypatch):
    """End to end: the user talks on and on (a partial every 0.3 s, no turn row).
    The held line plays inside hold_max_s plus one window, while the turn goes on."""
    monkeypatch.setenv("MOUTH_HOLD_MAX_S", "0.5")
    stop = threading.Event()

    def talk():
        while not stop.is_set():
            ears("partial", text="and another thing")
            time.sleep(0.3)
    feeder = threading.Thread(target=talk, daemon=True)
    feeder.start()
    try:
        time.sleep(0.1)
        t0 = time.time()
        mouth.say("Still with you", hold="turn-end", d=box.d, spawn=False)
        th = run_player(box.d, idle=0.5)
        assert _wait_for(lambda: _saying(box, "Still with you"), s=0.5 + WINDOW + 3), \
            "the held line never played inside a turn that did not end"
        waited = _saying(box, "Still with you")[0]
        started = datetime.fromisoformat(waited["ts"]).timestamp()
        assert started - t0 >= 0.5, "it played before the ceiling"
        assert not [x for x in lines(box.logs) if x.get("kind") == "turn"]
    finally:
        stop.set()
        feeder.join(2)
    th.join(10)


# -- a resume is not capped ----------------------------------------------------------

def test_a_resume_waits_for_the_end_of_the_turn_however_long(ears):
    """A resume (``resume_of``) is exempt from the ceiling: past it, mid-turn, in a
    gap or not, it still waits; at the turn's end it plays."""
    now = time.time()
    long_turn(now, newest_age_s=GAP)                                     # a gap right now
    item = sender(CEIL + WINDOW + 60, now, resume_of="u1", text="the rest of it.")
    assert hold.check(item, now=now) == "wait"
    assert hold.check({k: v for k, v in item.items() if k != "resume_of"}, now=now) == "play"


def test_on_a_barge_device_a_cut_ceiling_release_is_not_lost(box, ears, monkeypatch):
    """Headphones, a turn that never ends: the ceiling releases the held line, the
    user's ongoing words cut it, and its resume waits through the rest of the turn
    (no second ceiling release, which would be cut again and then lost: a replay is
    never replayed) and plays when the turn ends."""
    monkeypatch.setenv("MOUTH_BARGE_DEVICES", "null")                    # null = headphones
    monkeypatch.setenv("MOUTH_HOLD_MAX_S", "0.5")
    text = "This part you heard. " + "one two three four five six seven eight nine ten " * 2
    stop = threading.Event()

    def talk():
        while not stop.is_set():
            ears("partial", text="and another thing")
            time.sleep(0.3)
    feeder = threading.Thread(target=talk, daemon=True)
    feeder.start()
    try:
        time.sleep(0.1)
        mouth.say(text, hold="turn-end", d=box.d, spawn=False)
        th = run_player(box.d, idle=0.5)
        cut = lambda: [x for x in lines(box.logs)                         # noqa: E731
                       if x["kind"] == "said" and x.get("reason") == "barge-in"]
        assert _wait_for(cut, s=0.5 + WINDOW + 3), "the ceiling never released the line"
        time.sleep(0.5 + WINDOW + 1.5)            # past a second ceiling and its window
        assert len(_saying(box)) == 1, "the resume was ceiling-released into the ongoing talk"
    finally:
        stop.set()
        feeder.join(2)
    ears("turn", text="and another thing")
    th.join(20)
    saying = _saying(box)
    assert len(saying) == 2 and saying[1].get("resume_of"), \
        f"the cut line was lost: {[(x.get('text', '')[:30], x.get('resume_of')) for x in saying]}"
    said = {x["utt"]: x for x in lines(box.logs) if x["kind"] == "said"}
    assert said[saying[1]["utt"]]["reason"] == "done", "the resume did not play whole"
