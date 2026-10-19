"""EARS-334: a partial stamped in the same millisecond as the line's first frame counts.

The ears stamp a record's ``ts`` (and ``audio_ts``) to the millisecond, floored
(``heard._now_iso``: ``isoformat(timespec="milliseconds")`` truncates). A line's
first frame is ``time.time()``, a full float. Barge compared the two as they
stood (``t <= since_t`` drops), so a partial written in the same millisecond as
the first frame, and after it, stamped ``.859`` against ``.859115`` and was
dropped as said before the line. EARS-330 measured exactly that (``ts`` .859000,
first frame .859115) as a load flake, and waited it out in one test.

The fix compares both ends at heard's resolution (``hold._ms``): a stamp in the
first frame's own millisecond counts, one in the millisecond before does not.
The four that put a partial in the first frame's millisecond fail on dedba21;
the arrival-gate control and the `_ms` check pass there too, and pin the bound.
"""
from __future__ import annotations

import math
import time

from mouth import hold

from test_mouth_stale_barge import LINE, ears, _iso  # noqa: F401  (fixture)

#: The first frame's offset into its millisecond: EARS-330's measured .859115.
FRAME_US = 115e-6
#: When the partial is written: after the frame, inside the same millisecond.
WRITE_US = 600e-6


def _the_race(ears, text: str) -> tuple:
    """The measured shape: a line's first frame at ``ms + 115 us``, then his
    partial written (and heard, ``audio_ts``) at ``ms + 600 us``: after the frame,
    in its millisecond. Returns ``(since_t, partial)``."""
    ms = math.floor(time.time() * 1000) - 50
    since_t = ms / 1000 + FRAME_US
    ts = ms / 1000 + WRITE_US
    p = ears.partial(text, ts, ts, stamp=ts)
    assert ts > since_t                                   # written after the frame,
    assert hold._ts(p) < since_t                          # yet its stamp reads as before it
    assert hold._ms(hold._ts(p)) == hold._ms(since_t) == ms
    return since_t, p


def test_a_same_ms_partial_cuts_the_line(ears):
    since_t, p = _the_race(ears, "okay trying barge happens")
    trace: list = []
    got = hold.barge(since_t, LINE, now=since_t + 0.05, trace=trace)
    assert got is not None and got["seq"] == p["seq"], (
        f"a partial written {(WRITE_US - FRAME_US) * 1e6:.0f} us after the first frame, "
        f"in its millisecond, did not cut: trace {trace}")


def test_a_same_ms_partial_is_weighed_fresh(ears):
    """`_partials_since`: written after (arrival) and said after (``audio_ts``),
    both at heard's resolution, so it is not skipped ``stale``."""
    since_t, p = _the_race(ears, "okay trying")
    assert [(q["seq"], stale) for q, stale in hold._partials_since(since_t)] == [(p["seq"], None)]


def test_a_same_ms_quiet_ask_is_heard(ears):
    """`asked_for_quiet` goes through the same compare."""
    since_t, p = _the_race(ears, "please be quiet")
    got = hold.asked_for_quiet(since_t)
    assert got is not None and got["seq"] == p["seq"]


def test_the_millisecond_before_the_frame_still_does_not_count(ears):
    """The control: the bound moved by the frame's sub-millisecond part, no
    further. A partial stamped in the millisecond before the frame's is before
    the line."""
    since_t, p = _the_race(ears, "okay trying")
    early = ears.partial("said before it", since_t - 0.001, since_t - 0.001,
                         stamp=since_t - 0.001)
    assert hold._ms(hold._ts(early)) == hold._ms(since_t) - 1
    assert [q["seq"] for q, _ in hold._partials_since(since_t)] == [p["seq"]]


def test_a_newest_partial_from_the_ms_before_does_not_open_the_gate(ears):
    """The control on barge's arrival gate: the newest record is from the
    millisecond before the first frame, so nothing is new."""
    since_t = (math.floor(time.time() * 1000) - 50) / 1000 + FRAME_US
    ears.partial("okay trying barge happens", since_t - 0.001, since_t - 0.001,
                 stamp=since_t - 0.001)
    assert hold.barge(since_t, LINE, now=since_t + 0.05) is None


def test_ms_reads_a_parsed_stamp_as_its_own_millisecond():
    """A parsed stamp is a float a hair under its millisecond (``.859`` is
    ``.85899999...``); `_ms` must not floor it to the one below. Every ms of a
    whole second, and a float first frame inside it."""
    base = math.floor(time.time())
    for k in range(1000):
        t = base + k / 1000
        assert hold._ms(hold._ts({"ts": _iso(t)})) == base * 1000 + k, _iso(t)
        assert hold._ms(t + FRAME_US) == base * 1000 + k
