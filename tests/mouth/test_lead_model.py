"""EARS-285 rca-001: the paper model of the lead-buffer designs.

``bench/lead_model.py`` is what rca-001's design choice rests on. These pin
that it agrees with the real player's measured underruns (repro-001), and
the properties the design leans on.
"""
from __future__ import annotations

import pytest

from bench.lead_model import arrivals, play, start_index


@pytest.mark.parametrize("dur_s, rtf, stalls, under_s", [
    (3.0, 1.25, (), 0.68),          # repro-001 loaded-1.25: player 0.68-0.70
    (8.0, 1.25, (), 1.95),          # loaded-1.25-8s: player 1.95
    (3.0, 0.95, ((1.5, 1.0),), 0.93),   # stall-1s: player 0.90-0.95
    (3.0, 0.95, (), 0.0),           # idle-0.95: player 0
])
def test_no_lead_matches_what_the_player_measured(dur_s, rtf, stalls, under_s):
    assert play("none", dur_s, rtf, stalls)["under_s"] == pytest.approx(under_s, abs=0.03)


def test_arrivals_follow_the_clone_bench():
    assert arrivals(0.9, 1.0, ((0.6, 0.2),)) == pytest.approx([0.3, 0.6, 1.1])


def test_a_fixed_lead_holds_for_its_audio_and_never_past_the_line():
    arr = arrivals(6.0, 0.95)
    assert start_index("fixed:2.5", arr, 6.0) == 8     # 9 chunks = 2.7 s >= 2.5 s
    assert start_index("fixed:2.5", arrivals(1.5, 0.95), 1.5) == 4   # a short line: the whole of it
    r = play("fixed:2.5", 6.0, 0.95)
    assert r["added_s"] == pytest.approx(8 * 0.3 * 0.95) and r["under_s"] == 0


def test_a_fixed_lead_covers_a_stall_shorter_than_itself_after_the_start():
    assert play("fixed:2.5", 6.0, 0.95, ((2.7, 2.0),))["under_s"] == 0
    assert play("fixed:2.5", 6.0, 0.95, ((2.7, 3.0),))["under_s"] > 0


def test_the_adaptive_lead_waits_longer_only_when_the_render_is_below_real_time():
    for d in (3.0, 8.0):
        assert play("adapt:2.5", d, 0.95)["added_s"] == play("fixed:2.5", d, 0.95)["added_s"]
    long_loaded = 15.0, 1.25
    assert play("adapt:2.5", *long_loaded)["under_s"] < play("fixed:2.5", *long_loaded)["under_s"]
