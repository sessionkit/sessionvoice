"""Device gaps on a shared device (EARS-286 design.md s3, s4 test 5).

With an on-bed, ONE device output serves the bed lane and the line's lane, so
each said row's ``device_gap*`` is an attribution: the mixer charges the
device's gaps to every lane live when they happened. No PortAudio: a recording
null output whose gap counters the test moves, like DeviceOut's sampler would.
"""

from __future__ import annotations

import threading

import pytest

import test_mouth_bed
from mouth import output
from test_mouth_bed import RecOut, env, kind, play, run, say, started, wait_for  # noqa: F401 - fixtures
from test_mouth_mixer import RATE, Rig

FIELDS = ("device_gaps", "device_gap_s", "device_gap_max_s")


class GapRec(RecOut):
    """A recording null output with DeviceOut's gap counters. ``gap(s)`` makes
    the next write report ``s`` of lost time (an open episode), and the write
    after it close the episode: one sample per write, as DeviceOut does."""

    def __init__(self, name, sample_rate, pan=None) -> None:
        super().__init__(name, sample_rate, pan)
        self.device_gaps, self.device_gap_s, self.device_gap_max_s = 0, 0.0, 0.0
        self._gap_ep, self._gap_last = 0.0, 0.0
        self._pending = 0.0
        self.done = threading.Event()
        self.done.set()

    def gap(self, s: float) -> None:
        self.done.clear()
        self._pending = s

    def write(self, block) -> None:
        super().write(block)
        if self._pending:
            self._gap_ep += self._pending
            self.device_gap_s += self._pending
            self._pending = 0.0
        elif self._gap_ep:
            self.device_gaps += 1
            self.device_gap_max_s = max(self.device_gap_max_s, self._gap_ep)
            self._gap_last, self._gap_ep = self._gap_ep, 0.0
            self.done.set()


def _charge(mix, out, gaps=None, gap_s=None) -> None:
    """Move the device's counters by hand, then the mixer's per-block charge."""
    if gap_s is not None:
        out.device_gap_s = gap_s
    if gaps is not None:
        out.device_gaps = gaps
    with mix._lock:
        mix._charge_gaps()


def test_the_mixer_charges_device_gaps_to_the_lanes_live_when_they_happened(monkeypatch):
    rig = Rig(monkeypatch, out_cls=GapRec)
    mix = rig.mix
    bed_lane = mix.bed_lane()
    out = rig.outs[0]
    _charge(mix, out, gap_s=0.10)                  # an episode opens under the bed alone
    assert (bed_lane.device_gaps, bed_lane._gap_ep) == (0, pytest.approx(0.10))
    _charge(mix, out, gaps=1)                      # ... and closes
    line = mix.speech_lane(RATE)                   # the line joins after it
    _charge(mix, out, gap_s=0.25)                  # a second episode: both lanes hear it
    _charge(mix, out, gaps=2)
    assert (line.device_gaps, line.device_gap_s, line.device_gap_max_s) == (1, pytest.approx(0.15),
                                                                            pytest.approx(0.15))
    assert (bed_lane.device_gaps, bed_lane.device_gap_s, bed_lane.device_gap_max_s) == (
        2, pytest.approx(0.25), pytest.approx(0.15))
    _charge(mix, out, gap_s=0.30)                  # a third opens, then the line is cut in it
    line.abort()
    assert (line.device_gaps, line.device_gap_s) == (2, pytest.approx(0.20))   # closed as it left
    _charge(mix, out, gap_s=0.40)                  # later lost time is the bed's alone
    _charge(mix, out, gaps=3)
    assert (line.device_gaps, line.device_gap_s) == (2, pytest.approx(0.20))
    assert (bed_lane.device_gaps, bed_lane.device_gap_s, bed_lane.device_gap_max_s) == (
        3, pytest.approx(0.40), pytest.approx(0.15))
    bed_lane.abort()


def test_a_lane_that_heard_no_gap_carries_zeros(monkeypatch):
    rig = Rig(monkeypatch, out_cls=GapRec)
    bed_lane = rig.mix.bed_lane()
    with rig.mix._lock:
        rig.mix._charge_gaps()                     # nothing moved: nothing charged
    assert (bed_lane.device_gaps, bed_lane.device_gap_s, bed_lane.device_gap_max_s) == (0, 0.0, 0.0)
    bed_lane.abort()


# -- s4 test 5: through the player ---------------------------------------------------

@pytest.mark.needs_ffmpeg
def test_a_gap_while_a_line_plays_over_the_bed_is_on_both_rows(env, monkeypatch):
    monkeypatch.setattr(test_mouth_bed, "RecOut", GapRec)      # env's dev-* outputs report gaps
    f = play(env, seconds=3.0, device="dev-a")
    th = run(env)
    assert wait_for(lambda: started(env, f["utt"]))
    out = env.outs["dev-a"][0]
    out.gap(0.10)                                  # before the line: the bed's alone
    assert out.done.wait(2)
    line = say(env, "over the music " * 2, device="dev-a")
    assert wait_for(lambda: started(env, line["utt"]))
    out.gap(0.20)                                  # while the line plays over the bed
    assert out.done.wait(2)
    th.join(10)
    said = kind(env, "said")
    s_bed, s_line = said[f["utt"]], said[line["utt"]]
    assert s_bed["reason"] == "done" and s_line["reason"] == "done"
    assert len(env.outs["dev-a"]) == 1, "the line opened a second output"
    assert all(k in s_bed and k in s_line for k in FIELDS)
    assert (s_line["device_gaps"], s_line["device_gap_s"], s_line["device_gap_max_s"]) == (1, 0.2, 0.2)
    assert (s_bed["device_gaps"], s_bed["device_gap_s"], s_bed["device_gap_max_s"]) == (2, 0.3, 0.2)


@pytest.mark.needs_ffmpeg
def test_an_off_bed_row_carries_its_devices_gap_fields(env):
    f = play(env, seconds=0.4, device="dev-a", duck=False)   # an off-bed: its own plain output
    run(env).join(5)
    s = kind(env, "said")[f["utt"]]
    assert s["reason"] == "done"
    assert (s["device_gaps"], s["device_gap_s"], s["device_gap_max_s"]) == (0, 0.0, 0.0)
