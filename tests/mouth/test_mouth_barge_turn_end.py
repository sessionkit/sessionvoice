"""A review of ready/feat/mouth-barge-who (4feeda6),
kept as written, plus two guards on the fix: an unnamed partial waits LABEL_WAIT_S,
but barge() bails when the newest record is not a partial. If his short
utterance ENDS (a turn record) before the wait is up, does it ever cut?"""
import json, time
from datetime import datetime
from ears import heard
from mouth import hold
from test_mouth_barge_in import room, LINE  # noqa: F401  (fixture)


def _turn(age_s, text):
    ts = datetime.fromtimestamp(time.time() - age_s).astimezone().isoformat(timespec="milliseconds")
    with open(heard.log_path(), "a") as f:
        f.write(json.dumps({"ts": ts, "seq": 999, "kind": "turn", "text": text, "source": "mic"}) + "\n")
    for c in hold._caches.values():
        c.update(t=0.0, v=None)


def test_short_stop_side_car_down_then_turn_ends(room):
    t0 = time.time() - 10
    room("stop", age_s=3.0)          # his "stop", 3 s ago, never named (side-car down)
    _turn(2.2, "stop")              # his turn closed 0.8 s after it
    who = frozenset({"theo"})
    print("who=theo ->", hold.barge(t0, LINE, who=who))
    print("anyone   ->", hold.barge(t0, LINE))
    assert hold.barge(t0, LINE, who=who) is not None, "the unnamed stop never cuts once his turn has ended"


def test_short_stop_named_late_after_turn_end(room):
    t0 = time.time() - 10
    seq = room("stop", age_s=1.5)    # side-car UP but slow (p99 1.35 s)
    _turn(0.7, "stop")
    room.label(seq, "theo")         # named after the turn record landed
    assert hold.barge(t0, LINE, who=frozenset({"theo"})) is not None, "named theo, but the turn already ended"


def test_named_someone_else_still_never_cuts_after_the_turn(room):
    t0 = time.time() - 10
    seq = room("stop", age_s=1.5)
    _turn(0.7, "stop")
    room.label(seq, "other")        # the window re-weighs; the gate still holds
    assert hold.barge(t0, LINE, who=frozenset({"theo"})) is None


def test_an_old_turn_stays_cheap(room):
    t0 = time.time() - 10
    room("stop", age_s=6.0)
    _turn(5.0, "stop")              # past the wait plus the slack: nothing left to weigh
    assert hold.barge(t0, LINE, who=frozenset({"theo"})) is None
