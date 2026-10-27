"""Device gaps in the ``said`` record (EARS-286): time the device played that the
mouth's callback never filled, because the callback ran late (a starved host).

Measured from the clock, off the callback (design.md s2): the callback only adds
``frames_pulled``; the writer's ``_gap_sample`` compares wall time with audio
pulled. No PortAudio here: fake streams, and a fake clock for the sampler.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from mouth import output
from tests import child_pytest
from tests.audio_fence import expect_refusal
from test_mouth import FakeSd, PullingSd, _slow_on_device, box, lines, run_player, say  # noqa: F401  (fixture)
from test_mouth_hold import _wait_for

RATE = 24000
FIELDS = ("device_gaps", "device_gap_s", "device_gap_max_s")


class GappySd(PullingSd):
    """A PullingSd with a CoreAudio-sized buffer (10 ms), paced on an absolute
    schedule, that can MISS cycles: ``gap(s)`` stops calling back for ``s``
    seconds, then resumes at normal pace with no catch-up burst (what the HAL
    did on one host, design.md s0). ``start_delay`` holds the first callback back."""

    block = 240          # 10 ms at 24 kHz: the healthy slip stays well under the 20 ms floor

    def __init__(self, start_delay: float = 0.0):
        super().__init__()
        self.start_delay = start_delay
        self._gap = 0.0
        self.pulled = 0

    def gap(self, s: float) -> None:
        self._gap = s

    def OutputStream(self, *, device, samplerate, channels, dtype, callback):
        fake = self

        class S:
            latency = 0.01

            def __init__(s):
                s.run = True
                fake.streams.append(s)

            def start(s):
                def loop():
                    buf = np.zeros((fake.block, channels), dtype="float32")
                    period = fake.block / samplerate
                    time.sleep(fake.start_delay)
                    nxt = time.monotonic()
                    while s.run:
                        if fake._gap:
                            time.sleep(fake._gap)
                            fake._gap = 0.0
                            nxt = time.monotonic()       # resume: no catch-up burst
                        callback(buf, fake.block, None, None)
                        fake.pulled += fake.block
                        nxt += period
                        time.sleep(max(0.0, nxt - time.monotonic()))
                threading.Thread(target=loop, daemon=True).start()

            def abort(s):
                s.run = False

            def close(s):
                s.run = False

        return S()


@pytest.fixture
def gappy(monkeypatch):
    sd = GappySd()
    monkeypatch.setattr(output, "_sd", lambda: sd)
    monkeypatch.setattr(output.DeviceOut, "lost_after_s", 0.3)
    return sd


@pytest.fixture
def fake_device(monkeypatch):
    sd = PullingSd()
    monkeypatch.setattr(output, "_sd", lambda: sd)
    monkeypatch.setattr(output.DeviceOut, "lost_after_s", 0.3)
    return sd


def _said(logs, utt=None) -> dict:
    return next(r for r in lines(logs) if r["kind"] == "said" and (utt is None or r["utt"] == utt))


def _wait_playing(fake, timeout=3.0) -> None:
    end = time.monotonic() + timeout
    while fake.pulled == 0 and time.monotonic() < end:
        time.sleep(0.005)


# -- s4 test 1: forcing a gap -----------------------------------------------

def test_two_missed_stretches_are_two_device_gaps(box, gappy):
    item = say("x" * 36, device="MacBook Pro Speakers")      # 2.4 s of silence at 15 chars/s
    th = run_player(box.d)
    _wait_playing(gappy)
    time.sleep(0.4)
    gappy.gap(0.15)
    time.sleep(0.8)
    gappy.gap(0.1)
    th.join(8)
    said = _said(box.logs, item["utt"])
    assert said["reason"] == "done"
    assert said["device_gaps"] == 2
    assert said["device_gap_s"] == pytest.approx(0.25, abs=0.06)
    assert said["device_gap_max_s"] == pytest.approx(0.15, abs=0.06)
    assert said["underruns"] == 0                 # the queue never ran dry: not an underrun


def test_a_healthy_line_has_no_device_gap_and_says_so(box, gappy):
    say("x" * 15, device="MacBook Pro Speakers")
    run_player(box.d).join(5)
    said = _said(box.logs)
    assert said["reason"] == "done"
    assert all(k in said for k in FIELDS)         # present, never absent, on a played row
    assert (said["device_gaps"], said["device_gap_s"], said["device_gap_max_s"]) == (0, 0.0, 0.0)


def test_a_lost_device_says_how_long_it_was_gone(box, fake_device):
    """design.md s2: the abort's sample charges the dead time (decompose N2)."""
    lost = say("word " * 20, device="MacBook Pro Speakers")
    th = run_player(box.d)
    time.sleep(0.4)
    fake_device.stall.set()
    th.join(5)
    said = _said(box.logs, lost["utt"])
    assert said["reason"] == "device-lost"
    assert said["device_gaps"] == 1 and said["device_gap_s"] > 0.2


# -- s4 test 2: kept apart from underruns ------------------------------------

def test_a_slow_synthesis_is_an_underrun_not_a_device_gap(box, fake_device, monkeypatch):
    said = _slow_on_device(box, monkeypatch)
    assert said["reason"] == "done" and said["underruns"] >= 5
    assert said["device_gaps"] == 0 and said["device_gap_s"] == 0.0


# -- s4 test 3: the sampler, on a fake clock ---------------------------------

class StillSd(FakeSd):
    """A stream that never runs: the test calls the callback by hand."""

    def OutputStream(self, *, device, samplerate, channels, dtype, callback):
        return type("S", (), {"latency": 0.01, "start": lambda s: None,
                              "abort": lambda s: None, "close": lambda s: None})()


@pytest.fixture
def still(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(output, "_sd", lambda: StillSd())
    monkeypatch.setattr(output.time, "monotonic", lambda: clock[0])
    out = output.DeviceOut("MacBook Pro Speakers", RATE)
    block = np.zeros(1200, dtype=np.float32)
    buf = np.zeros((240, 1), dtype=np.float32)

    def step(wall_s, callbacks=5, frames=240):
        """``wall_s`` passes, the device calls back ``callbacks`` times, then a write samples."""
        clock[0] += wall_s
        for _ in range(callbacks):
            out._callback(buf if frames == 240 else np.zeros((frames, 1), np.float32), frames, None, None)
        out.write(block)

    out.write(block)                  # before any callback: no baseline yet
    return out, step, clock


def test_the_sampler_merges_late_samples_into_one_episode(still):
    out, step, _ = still
    step(5.0, callbacks=1)                       # the device starts late: still no gap (F1)
    assert out._gap_t is not None and out.device_gap_s == 0.0
    for _ in range(3):
        step(0.05)                               # healthy: 50 ms wall, 50 ms pulled
    assert (out.device_gaps, out.device_gap_s) == (0, 0.0)
    step(0.15)                                   # 100 ms the callback never ran
    step(0.10)                                   # and 50 ms more: the same episode
    assert out.device_gaps == 0 and out._gap_ep == pytest.approx(0.15)   # still open
    step(0.05)                                   # healthy again: closed
    assert out.device_gaps == 1
    assert out.device_gap_max_s == pytest.approx(0.15) and out._gap_last == pytest.approx(0.15)
    step(0.065)                                  # 15 ms: under the 20 ms floor, not a gap
    assert out.device_gaps == 1 and out.device_gap_s == pytest.approx(0.15)


def test_the_sampler_closes_an_open_episode_at_the_end(still):
    out, step, clock = still
    step(0.01, callbacks=1)                      # baseline
    step(0.05)
    step(0.30)                                   # 250 ms gone, still open ...
    assert out.device_gaps == 0 and out._gap_ep == pytest.approx(0.25)
    out.abort()                                  # ... the line's end closes it
    assert (out.device_gaps, out.device_gap_max_s) == (1, pytest.approx(0.25))
    clock[0] += 5.0
    out._gap_sample()                            # nothing after the end counts
    assert out.device_gap_s == pytest.approx(0.25)


def test_the_threshold_follows_the_buffer(still):
    out, step, _ = still
    step(0.05, callbacks=1, frames=1200)         # a 50 ms host buffer: baseline, B learned
    step(0.10, callbacks=1, frames=1200)         # 50 ms short: one buffer of jitter, not a gap
    step(0.00, callbacks=1, frames=1200)
    assert out.device_gap_s == 0.0
    step(0.20, callbacks=1, frames=1200)         # 150 ms short: past 1.5 buffers
    assert out._gap_ep == pytest.approx(0.15)


def test_a_device_that_never_calls_back_has_no_gap(still):
    out, _, clock = still
    clock[0] += 2.0
    out.abort()
    assert (out.device_gaps, out.device_gap_s) == (0, 0.0)


# -- s4 test 4: the cost limit ------------------------------------------------

def test_the_callback_reads_no_clock(monkeypatch):
    monkeypatch.setattr(output, "_sd", lambda: StillSd())
    out = output.DeviceOut("MacBook Pro Speakers", RATE)
    out.write(np.full(1200, 0.5, dtype=np.float32))

    def boom():
        raise AssertionError("the callback read the clock")
    monkeypatch.setattr(output.time, "monotonic", boom)
    buf = np.zeros((480, 1), dtype=np.float32)
    out._callback(buf, 480, None, None)          # healthy
    out.rebuffer_s, out._hold = 1.0, True
    out._callback(buf, 480, None, None)          # during a hold: the early return
    assert out.frames_pulled == 960 and out.frames_played == 480
    assert (out.device_gaps, out.device_gap_s, out._gap_ep) == (0, 0.0, 0.0)   # untouched here
    monkeypatch.undo()


# -- s4 test 6: null and call: say 0, present ----------------------------------

def test_a_null_line_carries_zeros(box):
    say("Hello Theo.")
    run_player(box.d).join(5)
    said = _said(box.logs)
    assert said["device"] == "null"
    assert (said["device_gaps"], said["device_gap_s"], said["device_gap_max_s"]) == (0, 0.0, 0.0)


def test_null_and_call_outputs_have_no_device_clock():
    for cls in (output.NullOut, output.CallOut):
        assert (cls.device_gaps, cls.device_gap_s, cls.device_gap_max_s) == (0, 0.0, 0.0)


def test_a_line_that_never_played_has_no_device_gap_fields(box, monkeypatch):
    """EARS-320: "absent" comes from this test's own device list, never the host's. On one host
    the host HAD airpods and the line played to them; in a venv without sounddevice a
    missing module passed for absent. The fake lists no `airpods` (only the aggregate
    that must never match it), so the player meets a real DeviceAbsent, and says so."""
    sd = PullingSd()
    monkeypatch.setattr(output, "_sd", lambda: sd)
    say("hello", device="airpods")               # not in the fake's list: device-absent
    th = run_player(box.d)
    # EARS-367: a slow host fails here, with a message, not as next()'s bare StopIteration
    assert _wait_for(lambda: any(r["kind"] == "said" for r in lines(box.logs))), "no said row in 5 s"
    th.join(5)
    assert not th.is_alive()                     # the player exited before the fake is undone
    said = _said(box.logs)
    assert said["reason"] == "device-absent"
    assert "output device 'airpods' is not present" in said["detail"]   # a DeviceAbsent, not a missing module
    assert sd.streams == []                      # nothing was opened
    assert not any(k in said for k in FIELDS)


HOST_WITH_AIRPODS = '''\
"""A host's sounddevice that HAS airpods (EARS-320 2b): a recording fake, no PortAudio."""
import os


def _note(what):
    with open(os.environ["HOST_SD_LOG"], "a") as f:
        f.write(what + "\\n")


class _Default:
    device = [0, 1]


default = _Default()
_DEVICES = [{"name": "MacBook Pro Microphone", "max_input_channels": 1, "max_output_channels": 0},
            {"name": "airpods", "max_input_channels": 1, "max_output_channels": 2},
            {"name": "MacBook Pro Speakers", "max_input_channels": 0, "max_output_channels": 2}]


def query_devices(device=None, kind=None):
    _note(f"query:{device}:{kind}")
    return list(_DEVICES) if device is None and kind is None else _DEVICES[device if device is not None else 1]


def _terminate():
    _note("terminate")


def _initialize():
    _note("initialize")


class OutputStream:
    def __init__(self, device=None, **kw):
        _note(f"OPEN:{device}")
        raise RuntimeError("the host's airpods were opened")
'''


def test_absent_comes_from_the_fake_even_on_a_host_with_airpods(tmp_path):
    """2b: the test above, run as its own pytest on a "host" whose sounddevice lists airpods
    (the shape of the host that played). It passes, and the host's device list is never read: the
    test's own fake decides absent. Unfaked, the same line opened the host's airpods."""
    root = Path(__file__).resolve().parents[2]
    host = tmp_path / "host"
    host.mkdir()
    (host / "sounddevice.py").write_text(HOST_WITH_AIRPODS)
    log = tmp_path / "host-sd.log"
    env = {**os.environ, "HOME": str(tmp_path / "home"), "HOST_SD_LOG": str(log),
           "PYTHONPATH": os.pathsep.join([str(host), os.environ.get("PYTHONPATH", "")])}
    node = "tests/mouth/test_mouth_device_gaps.py::test_a_line_that_never_played_has_no_device_gap_fields"
    out = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                          child_pytest.basetemp(tmp_path), node],
                         cwd=root, env=env, capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stdout[-3000:] + out.stderr[-2000:]
    assert "1 passed" in out.stdout
    assert not log.exists(), log.read_text()     # the host's sounddevice was never asked anything


def test_unfaked_the_line_reaches_the_hosts_airpods(box, monkeypatch, tmp_path):
    """The control for 2b: with the same host and NO fake of the test's own, the player
    asks the host's list, finds airpods and opens them (what played on that host)."""
    host = tmp_path / "host"
    host.mkdir()
    (host / "sounddevice.py").write_text(HOST_WITH_AIRPODS)
    log = tmp_path / "host-sd.log"
    monkeypatch.setenv("HOST_SD_LOG", str(log))
    monkeypatch.syspath_prepend(str(host))
    monkeypatch.delitem(sys.modules, "sounddevice", raising=False)
    say("hello", device="airpods")
    run_player(box.d).join(5)
    said = _said(box.logs)
    # the host fake's OutputStream raises: a device that is there and refused to
    # open, so open-failed (EARS-489), not device-absent
    assert said["reason"] == "open-failed" and "the host's airpods were opened" in said["detail"]
    assert "OPEN:1" in log.read_text().splitlines()   # index 1: airpods


def test_unfaked_the_fence_refuses_the_line_and_names_the_test(box, monkeypatch):
    """The other half of EARS-320: no fake and no host fake, the real sounddevice is what
    the player would reach. The fence refuses it, installed or not; the player's broad
    except still writes the line as unplayed (``open-failed`` since EARS-489: the
    refusal is no DeviceAbsent), but the refusal is recorded, so a test that forgot
    its fake fails at teardown instead of passing as "absent"."""
    monkeypatch.delitem(sys.modules, "sounddevice", raising=False)
    with expect_refusal() as got:
        say("hello", device="airpods")
        run_player(box.d).join(5)
    said = _said(box.logs)
    assert said["reason"] == "open-failed" and said["detail"].startswith("RealAudioDevice: ")
    assert got and got[0]["test"].startswith(
        "tests/mouth/test_mouth_device_gaps.py::test_unfaked_the_fence_refuses_the_line_and_names_the_test")


# -- s4 test 7: a slow device start is not a gap (design review F1) ------------

def test_a_slow_device_start_is_not_a_gap(box, monkeypatch):
    sd = GappySd(start_delay=0.1)
    monkeypatch.setattr(output, "_sd", lambda: sd)
    say("x" * 15, device="MacBook Pro Speakers")  # silence: written at once, like a file bed
    run_player(box.d).join(5)
    said = _said(box.logs)
    assert said["reason"] == "done"
    assert (said["device_gaps"], said["device_gap_s"]) == (0, 0.0)


def test_a_stall_during_drain_polls_is_a_gap(still):
    """drain() samples every 10 ms, under the 20 ms floor: a sample where the
    device took nothing keeps the baseline, so the stall is not sliced away."""
    out, step, clock = still
    step(0.01, callbacks=1)                      # baseline
    step(0.05)
    for _ in range(20):                          # the device stalls 200 ms while drain() polls
        clock[0] += 0.01
        out._gap_sample()
    assert out._gap_ep > 0.15                    # open, not sliced into nothing
    step(0.05)                                   # back: closed
    assert out.device_gaps == 1
    assert out.device_gap_s == pytest.approx(0.2, abs=0.03)


def test_quick_samples_between_callbacks_are_not_gaps(still):
    """Writes into a queue with room come faster than the callbacks: no slip."""
    out, step, clock = still
    step(0.01, callbacks=1)
    for _ in range(10):
        for _ in range(4):                       # four quick samples, the device not due yet
            clock[0] += 0.002
            out._gap_sample()
        step(0.042)                              # then 50 ms of wall time has gone, 50 ms pulled
    assert (out.device_gaps, out.device_gap_s, out._gap_ep) == (0, 0.0, 0.0)
