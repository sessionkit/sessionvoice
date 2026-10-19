"""EARS-285: a clone line rendered slower than real time underruns (repro).

The clone backend streams 0.3 s chunks (``streaming_interval`` 0.3) and the
player plays each as it lands, so it is never more than a chunk ahead. These
drive the REAL player with ``bench/clone_underrun.py``'s :class:`CloneLike`,
a fake of the clone server's pacing, on each path a clone line takes: a null
output, a device (fake PortAudio pulling in real time) and a lane over a bed.

These were the bug, measured, as ``xfail(strict=True)`` (repro-001): without a
lead the said record showed ``(9, 0.68)`` and ``(1, 0.93)`` of
``underruns``/``underrun_s``. fix-001 held a clone line's first frame for a
2.5 s lead; fix-002 made the lead a setting, default 0. So these say each
line with ``lead_s=2.5`` (``mouth say --lead
2.5``), and they pass. The last test is the default, no lead: the bug is
still there without it, so these tests can still see it.

EARS-374: the ``bed`` case needs ffmpeg. Its bed is a file, and the mouth decodes
every file with ffmpeg (``backends.FileSound``). On a host without ffmpeg that bed
died within milliseconds, and whether the line caught it alive was a race: the
case passed without ever running ffmpeg, or failed and was reported as an ffmpeg
skip by ``tests/conftest.py``. So it is skipped there, before it runs.

The pacing asserts allow ``JITTER_S`` of dry-out in all: a loaded host wakes the
paced writer late by a few ms, and each late wake counts as an underrun. The bug
these guard against is ~0.7 s; ``test_without_the_lead...`` still sees it.
"""
from __future__ import annotations

import shutil

import pytest

from bench.clone_underrun import SCENARIOS, CloneLike, run_line

LEAD = 2.5   # a 2-3 s sizing: what a seat that wants no chop passes
JITTER_S = 0.1   # a loaded host's late wakes, in all: under a third of a 0.3 s chunk, a
                 # seventh of the ~0.7 s dry-out a slow line has without the lead (EARS-374)
NEEDS_FFMPEG = pytest.mark.skipif(shutil.which("ffmpeg") is None,
                                  reason="no ffmpeg: the bed is a file, and files decode with ffmpeg")


def no_dry_out(r):
    """The line never ran dry, beyond a loaded host's scheduling jitter (EARS-374)."""
    return r["underrun_s"] is not None and r["underrun_s"] <= JITTER_S


def test_the_fake_clone_paces_chunks_like_the_server():
    fake = CloneLike(dur_s=0.9, rtf=1.0, interval_s=0.3, stalls=[(0.6, 0.2)])
    blocks = list(fake.stream("x", "v", None))
    assert [len(b) for b in blocks] == [7200, 7200, 7200]
    t = [a for a, _ in fake.arrivals]
    assert t[0] == pytest.approx(0.3, abs=0.05)
    assert t[1] - t[0] == pytest.approx(0.3, abs=0.05)
    assert t[2] - t[1] == pytest.approx(0.5, abs=0.05)    # the 0.2 s stall before chunk 2
    assert fake.lead_s() == pytest.approx(0.2, abs=0.05)  # what a lead buffer had to cover


def test_a_clone_line_faster_than_real_time_never_underruns(tmp_path):
    r = run_line(SCENARIOS["healthy-0.5"], "null", tmp_path)
    assert r["reason"] == "done" and r["played_s"] == pytest.approx(3.0, abs=0.06)
    assert r["underruns"] == 0 and r["underrun_s"] == 0


@pytest.mark.parametrize("path", ["null", "device", pytest.param("bed", marks=NEEDS_FFMPEG)])
def test_a_clone_line_below_real_time_does_not_underrun(tmp_path, path):
    """Render at 0.8x real time (rtf 1.25), 3 s of audio: without a lead 9
    dry-outs, ~0.7 s of silence mid-line, on every path. The 2.5 s lead is
    the whole 3 s line here (9 of its 10 chunks), held ~3.0 s past the first."""
    r = run_line(SCENARIOS["loaded-1.25"], path, tmp_path, lead_s=LEAD)
    assert r["reason"] == "done" and r["played_s"] == pytest.approx(3.0, abs=0.06)
    if path == "bed":
        assert r["over"], "the line did not mix over the bed"
    assert no_dry_out(r), r
    assert r["held_s"] == pytest.approx(3.0, abs=0.2), r


def test_a_one_second_stall_in_a_clone_line_does_not_underrun(tmp_path):
    """An idle laptop (rtf 0.95) with one 1 s stall: without a lead one
    ~0.9 s gap. The stall lands in the hold, so the hold is ~1 s longer."""
    r = run_line(SCENARIOS["stall-1s"], "null", tmp_path, lead_s=LEAD)
    assert r["reason"] == "done"
    assert no_dry_out(r), r
    assert r["held_s"] == pytest.approx(3.28, abs=0.2), r


def test_without_the_lead_the_slow_clone_line_still_underruns(tmp_path):
    """The default, no lead asked: repro-001's numbers come back, so the
    tests above are not passing by accident."""
    r = run_line(SCENARIOS["loaded-1.25"], "null", tmp_path)
    assert r["held_s"] is None
    assert r["underruns"] >= 5 and r["underrun_s"] == pytest.approx(0.68, abs=0.15), r
    assert not no_dry_out(r), r   # JITTER_S never lets the bug itself through (EARS-374)
