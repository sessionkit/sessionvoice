"""EARS-152 do-004: the mixer. One output per device while an on-bed plays.

design.md s3. A played file with ducking on (an ON-bed, the default) owns a
``Mixer`` on its device, and a line spoken to that device is a ``MixLane`` of
it, mixed over the music, instead of waiting behind the song or opening a
second output. On a call the mixer re-segments its one line connection.

Seams: mixer-lane-contract, mixer-headroom, call-segments, now-spares-music,
volume-bed-times-duck (the bed_volume half), volume-per-item (file, duck on).
The duck itself (an on-bed ducks instead of being cut) is test_mouth_duck.py's.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import tempfile
import threading
import time
from datetime import datetime

import numpy as np
import pytest

import mouth
from mouth import bed, output

from test_mouth_bed import (RecOut, deliver, env, kind, play, rows, run, say,  # noqa: F401 - fixtures
                            started, wait_for)

RATE = 24000
BLOCK = int(RATE * output.MIX_BLOCK_S)


def ts(r: dict) -> float:
    return datetime.fromisoformat(r["ts"]).timestamp()


def order(env) -> dict:
    return {(r["kind"], r["utt"]): i for i, r in enumerate(sorted(rows(env), key=lambda r: r["seq"]))}


# -- no bed, no mixer ------------------------------------------------------------

def test_with_no_bed_open_output_is_todays_and_looks_nothing_up(monkeypatch):
    monkeypatch.setattr(bed, "device_key", lambda raw: pytest.fail("looked a device up"))
    out = output.open_output("null", RATE)
    assert type(out) is output.NullOut


# -- mixer-headroom --------------------------------------------------------------

def test_the_limiter_is_identity_below_the_knee_and_never_passes_full_scale():
    x = np.linspace(-0.9, 0.9, 1001, dtype=np.float32)
    assert output.soft_limit(x) is x                        # below the knee: untouched
    big = np.linspace(-3.0, 3.0, 6001, dtype=np.float32)
    y = output.soft_limit(big)
    assert np.abs(y).max() <= 1.0 and np.all(np.diff(y) >= 0)  # monotonic, never past 1.0
    assert output.soft_limit(np.array([1.2], dtype=np.float32))[0] < 1.0     # 1.2: a line over a 20 % bed
    k, e = 0.9, 1e-4                                        # slope 1 on both sides of the knee
    s = (output.soft_limit(np.array([k + e], dtype=np.float32))[0] - k) / e
    assert s == pytest.approx(1.0, abs=1e-2)


class Rig:
    """A mixer on a recording null output, built by hand (no player)."""

    def __init__(self, monkeypatch, out_cls=None, name="dev-x"):
        self.outs: list = []

        def plain(n, rate, pan=None, meta=None):
            o = (out_cls or RecOut)(n, rate, pan)
            self.outs.append(o)
            return o
        monkeypatch.setattr(output, "_plain_output", plain)
        self.mix = output.Mixer(name, RATE, meta={"text": "[sound x]", "who": "ivy"})

    @property
    def written(self) -> np.ndarray:
        return np.concatenate([np.asarray(b) for b in self.outs[0].blocks])


def test_a_full_scale_line_over_a_full_scale_bed_never_writes_past_full_scale(monkeypatch):
    rig = Rig(monkeypatch)
    bed_lane = rig.mix.bed_lane()
    line = rig.mix.speech_lane(RATE)
    ones = np.ones(BLOCK, dtype=np.float32)
    feed = threading.Thread(target=lambda: [bed_lane.write(ones) for _ in range(6)])
    feed.start()
    for _ in range(4):
        line.write(-ones if _ % 2 else ones)
    line.drain()
    feed.join()
    bed_lane.drain()
    w = rig.written
    assert np.abs(w).max() <= 1.0, "a mixed block passed full scale"
    assert w.max() > 0.95 and np.any(np.isclose(w, 0.0, atol=1e-6))   # 2.0 limited; 0.0 is 1 - 1


def test_a_bed_alone_passes_through_the_mixer_unchanged(monkeypatch):
    rig = Rig(monkeypatch)
    lane = rig.mix.bed_lane()
    x = (0.95 * np.sin(np.arange(BLOCK * 5) / 7.0)).astype(np.float32)   # above the knee, alone
    for i in range(0, len(x), BLOCK):
        lane.write(x[i:i + BLOCK])
    lane.drain()
    assert np.array_equal(rig.written, x)


# -- mixer-lane-contract -----------------------------------------------------------

def test_a_lane_counts_what_the_mixer_took_and_its_underruns(monkeypatch):
    rig = Rig(monkeypatch)
    bed_lane = rig.mix.bed_lane()
    stop = threading.Event()

    def music():
        while not stop.is_set():
            bed_lane.write(np.zeros(BLOCK, dtype=np.float32))
    feeder = threading.Thread(target=music, daemon=True)
    feeder.start()
    line = rig.mix.speech_lane(RATE)
    assert (line.name, line.frames_played, line.underruns) == ("dev-x", 0, 0)
    line.write(np.full(BLOCK, 0.1, dtype=np.float32))
    time.sleep(0.25)                                   # dry mid-line: synthesis fell behind
    line.write(np.full(BLOCK, 0.1, dtype=np.float32))
    line.drain()
    assert line.frames_played == line.frames_queued == 2 * BLOCK
    assert line.underruns == 1 and line.underrun_frames >= BLOCK
    assert line.latency == pytest.approx(output.NullOut.latency)
    stop.set()
    feeder.join()
    bed_lane.drain()


def test_abort_drops_the_lanes_fifo(monkeypatch):
    rig = Rig(monkeypatch)
    bed_lane = rig.mix.bed_lane()
    stop = threading.Event()

    def music():
        while not stop.is_set():
            bed_lane.write(np.zeros(BLOCK, dtype=np.float32))
    threading.Thread(target=music, daemon=True).start()
    line = rig.mix.speech_lane(RATE)
    for _ in range(4):
        line.write(np.full(BLOCK, 0.5, dtype=np.float32))
    line.abort()
    took = line.frames_played
    assert took < line.frames_queued, "nothing was left to drop"
    time.sleep(0.2)
    assert line.frames_played == took
    assert rig.mix.speech is None and rig.mix.takes_speech()   # the bed plays on, a line may join
    heard = int((np.abs(rig.written) > 0.25).sum())
    assert heard <= took
    stop.set()
    bed_lane.drain()


class Unpaced(RecOut):
    """A device that takes blocks as fast as the mixer makes them, keeping none."""

    def write(self, block) -> None:
        self.frames_played += len(block)


def test_aborting_a_line_while_the_mixer_pulls_it_never_kills_the_music(monkeypatch):
    """Review 1525: ``abort`` (play_one's ``_abort`` thread) dropped ``_cur``
    while the mixer's thread was inside ``_pull``, a TypeError in the pacing
    thread: the mixer died and the bed with it, on a barge-in over music. It
    hit within ~1000 aborts at a 1 us switch interval; now none in 1.5 s."""
    import sys

    rig = Rig(monkeypatch, out_cls=Unpaced)
    bed_lane = rig.mix.bed_lane()
    stop = threading.Event()

    def music():
        try:
            while not stop.is_set():
                bed_lane.write(np.zeros(BLOCK, dtype=np.float32))
        except output.DeviceLost:
            pass
    feeder = threading.Thread(target=music, daemon=True)
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        feeder.start()
        t0, n = time.monotonic(), 0
        while time.monotonic() - t0 < 1.5 and rig.mix.error is None:
            line = rig.mix.speech_lane(RATE)
            if line is None:
                continue
            try:
                for _ in range(3):
                    line.write(np.full(BLOCK // 7, 0.1, dtype=np.float32))
            except output.DeviceLost:
                break                     # the mixer died: the assert below says why
            line.abort()
            n += 1
    finally:
        sys.setswitchinterval(old)
        stop.set()
    assert rig.mix.error is None, f"the mixer died after {n} aborts: {rig.mix.error!r}"
    assert n > 100
    feeder.join(2)
    bed_lane.abort()


class Stalled(RecOut):
    """A device that stops pulling: write blocks until released."""

    go = threading.Event()

    def write(self, block) -> None:
        self.go.wait(5)


def test_a_lane_is_device_lost_when_the_mixer_takes_nothing_for_a_second(monkeypatch):
    Stalled.go.clear()
    rig = Rig(monkeypatch, out_cls=Stalled)
    lane = rig.mix.bed_lane()
    t0 = time.monotonic()
    with pytest.raises(output.DeviceLost, match="took no audio for 1.0s"):
        for _ in range(20):
            lane.write(np.zeros(BLOCK, dtype=np.float32))
    assert 0.9 < time.monotonic() - t0 < 2.0
    Stalled.go.set()
    lane.abort()


def test_lanes_pan_on_a_stereo_device_and_a_call_stays_mono(monkeypatch):
    class Stereo(RecOut):
        def __init__(self, name, rate, pan=None):
            super().__init__(name, rate, pan)
            self.gains = np.array([0.7, 0.7], dtype=np.float32)   # what DeviceOut sets on 2 channels

    rig = Rig(monkeypatch, out_cls=Stereo)
    assert rig.mix.stereo
    bed_lane = rig.mix.bed_lane()                          # no pan: today's mono on both
    line = rig.mix.speech_lane(RATE, pan=-1.0)             # the left ear only
    assert line.pan_ignored is None
    bed_lane.write(np.full(BLOCK, 0.2, dtype=np.float32))
    line.write(np.full(BLOCK, 0.4, dtype=np.float32))
    line.drain()
    bed_lane.drain()
    w = rig.written.reshape(-1, 2)
    assert np.allclose(w[:BLOCK], [0.6, 0.2], atol=1e-6)   # (bed + line, bed)

    mono = output.Mixer.__new__(output.Mixer)
    mono.stereo, mono.out = False, type("C", (), {"pan_ignored": "a call is mono"})()
    assert mono._lane_gains(-1.0) == (None, "a call is mono")
    assert mono._lane_gains(None) == (None, None)


# -- through the player: lines mix over an on-bed --------------------------------

@pytest.mark.needs_ffmpeg
def test_a_line_to_an_on_beds_device_plays_over_the_music_on_one_output(env):
    f = play(env, seconds=2.0, device="dev-a")
    line = say(env, "over the music", device="dev-a")
    run(env).join(10)
    saying, said = kind(env, "saying"), kind(env, "said")
    pos = order(env)
    assert pos[("said", line["utt"])] < pos[("said", f["utt"])], "the line waited for the song"
    assert ts(saying[line["utt"]]) - ts(saying[f["utt"]]) < 0.5
    assert said[f["utt"]]["reason"] == "done" and not said[f["utt"]]["cut"]
    assert said[line["utt"]]["reason"] == "done" and said[line["utt"]]["device"] == "dev-a"
    assert len(env.outs["dev-a"]) == 1, "the line opened a second output on the device"
    assert said[f["utt"]]["played_s"] == pytest.approx(2.0, abs=0.06)
    assert not bed._MIX, "the mixer outlived its lanes"


@pytest.mark.needs_ffmpeg
def test_a_line_that_outlasts_the_bed_finishes_on_the_same_output(env):
    f = play(env, seconds=0.4, device="dev-a")
    line = say(env, "word " * 6, device="dev-a")             # ~2 s, the song ends under it
    run(env).join(10)
    said = kind(env, "said")
    assert said[f["utt"]]["reason"] == "done" and said[line["utt"]]["reason"] == "done"
    assert said[line["utt"]]["played_s"] == pytest.approx(said[line["utt"]]["dur_s"], abs=0.06)
    assert len(env.outs["dev-a"]) == 1
    assert not bed._MIX


@pytest.mark.needs_ffmpeg
def test_a_second_line_after_the_first_mixes_too_and_lines_elsewhere_open_their_own(env):
    f = play(env, seconds=3.0, device="dev-a")
    one = say(env, "first", device="dev-a")
    other = say(env, "elsewhere", device="dev-b")
    two = say(env, "second", device="dev-a")
    run(env).join(10)
    said = kind(env, "said")
    pos = order(env)
    for x in (one, other, two):
        assert said[x["utt"]]["reason"] == "done"
        assert pos[("said", x["utt"])] < pos[("said", f["utt"])]
    assert len(env.outs["dev-a"]) == 1 and len(env.outs["dev-b"]) == 1


# -- now-spares-music ----------------------------------------------------------------

@pytest.mark.parametrize("how", ["cli", "mail"])
@pytest.mark.needs_ffmpeg
def test_a_now_line_cuts_the_line_over_the_music_never_the_music(env, how, signed):
    f = play(env, seconds=3.0, device="dev-a")
    line = say(env, "word " * 12, device="dev-a")
    th = run(env)
    assert wait_for(lambda: started(env, f["utt"]) and started(env, line["utt"]))
    time.sleep(0.2)
    if how == "cli":
        now = say(env, "right now", priority="now", device="dev-a")["utt"]
    else:
        deliver(env, "right now", X_Mouth_Priority="now", X_Mouth_Device="dev-a",
                X_Session_Signature=signed("2", "x-mouth-priority", "x-mouth-device"))  # EARS-335
        now = None
    th.join(10)
    said, saying = kind(env, "said"), kind(env, "saying")
    hit = now or next(u for u, r in saying.items() if r["text"] == "right now")
    assert said[line["utt"]]["reason"] == "interrupted"
    assert said[hit]["reason"] == "done"
    assert (said[f["utt"]]["reason"], said[f["utt"]]["cut"]) == ("done", False)
    assert order(env)[("said", hit)] < order(env)[("said", f["utt"])], "the now line waited for the song"


@pytest.mark.needs_ffmpeg
def test_a_now_line_to_the_beds_device_while_a_line_plays_elsewhere_spares_the_music(env):
    f = play(env, seconds=3.0, device="dev-a")
    other = say(env, "word " * 12, device="dev-b")            # ~4 s on another device
    th = run(env)
    assert wait_for(lambda: started(env, f["utt"]) and started(env, other["utt"]))
    time.sleep(0.2)
    now = say(env, "right now", priority="now", device="dev-a")
    th.join(10)
    said = kind(env, "said")
    assert said[other["utt"]]["reason"] == "interrupted"     # say()'s stop, today's
    assert said[now["utt"]]["reason"] == "done"
    assert said[f["utt"]]["reason"] == "done"
    assert order(env)[("said", now["utt"])] < order(env)[("said", f["utt"])]
    assert len(env.outs["dev-a"]) == 1                        # it mixed over the music


def test_an_on_beds_now_look_never_cuts_it_but_a_retract_does(env, signed):
    f = play(env, seconds=1.0)
    item = mouth.box.public(mouth.box.Queue(env.d).lines()[0])
    b = bed.Bed(bed.Beds(env.d), item, None, bed.device_key(item["device"]))
    assert b.ducks
    say(env, "here now", priority="now")
    b._poll()
    assert b._why is None
    deliver(env, "", Supersedes=f["mail_id"], X_Session_Signature=signed("2"))  # rule (a), EARS-335
    signed.settle(env.mail)   # its verdict has landed: a bed's poll never waits for one
    b._next_look = 0.0
    with pytest.raises(bed._Cut):
        b._poll()
    assert b._why == "retracted"


# -- volume: the bed's is the mixer's bed_volume -------------------------------------

@pytest.mark.needs_ffmpeg
def test_an_on_beds_volume_is_the_mixers_bed_volume(env):
    seen = {}
    real = output.Mixer.bed_lane

    def spy(self, pan=None, volume=None, meta=None, utt=None):
        seen[self.name] = self
        return real(self, pan, volume, meta, utt)

    import _pytest.monkeypatch as mp
    m = mp.MonkeyPatch()
    m.setattr(output.Mixer, "bed_lane", spy)
    try:
        half = play(env, seconds=0.6, device="dev-a", volume=50)
        full = play(env, seconds=0.6, name="b.wav", device="dev-b")
        quiet = play(env, seconds=0.6, name="c.wav", device="dev-c", volume=30)
        run(env).join(8)
    finally:
        m.undo()

    def peak(dev):
        return max(float(np.abs(b).max()) for b in env.outs[dev][0].blocks)

    assert peak("dev-a") == pytest.approx(0.5 * peak("dev-b"), rel=0.01)
    assert peak("dev-c") == pytest.approx(0.3 * peak("dev-b"), rel=0.01)
    assert {g for _, g in seen["dev-a"].trace} == {0.5}        # bed_volume x duck_ramp (no line, no duck)
    assert {g for _, g in seen["dev-b"].trace} == {1.0}
    said = kind(env, "said")
    assert [said[x["utt"]]["volume"] for x in (half, full, quiet)] == [50, 100, 30]


# -- call-segments: a fake mouth leg -------------------------------------------------

class SegBot:
    """A mouth leg that stamps every connection's header and its end."""

    def __init__(self, call=5531):
        self.dir = tempfile.mkdtemp(prefix="mx-", dir="/tmp")
        self.path = os.path.join(self.dir, "call-mouth.sock")
        self.call = call
        self.conns: list[dict] = []
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(self.path)
        self.srv.listen(8)
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._one, args=(c,), daemon=True).start()

    def _one(self, c):
        f = c.makefile("rb")
        hdr = json.loads(f.readline())
        if hdr.get("probe"):
            c.sendall((json.dumps({"ok": True, "agent": "ivy", "calls": [self.call]}) + "\n").encode())
            c.close()
            return
        conn = {"text": hdr.get("text"), "t_open": time.monotonic(), "t_end": None, "ended": "eof", "samples": 0}
        self.conns.append(conn)
        c.sendall(b'{"ok": true}\n')
        while True:
            head = f.read(5)
            if len(head) < 5:
                break
            kind_, n = head[:1], struct.unpack(">I", head[1:])[0]
            payload = f.read(n) if n else b""
            if kind_ == b"A":
                conn["samples"] += len(payload) // 2
                continue
            conn["ended"] = {b"D": "done", b"C": "cut"}[kind_]
            break
        conn["t_end"] = time.monotonic()
        try:
            c.sendall(json.dumps({"ok": conn["ended"] == "done", "ended": conn["ended"]}).encode() + b"\n")
        except OSError:
            pass
        c.close()

    def close(self):
        self.srv.close()


@pytest.fixture
def segbot(env, monkeypatch):
    b = SegBot()
    monkeypatch.setenv("MOUTH_CALL_SOCKS", b.path)
    yield b
    b.close()


class MixBlocks:
    """Each block the mixer's pacing thread wrote, as ``[bed frames, line frames]``:
    what its lanes' ``_pull`` gave it (a spy on :meth:`output.MixLane._pull`)."""

    def __init__(self, monkeypatch):
        self.rows: list = []
        real = output.MixLane._pull

        def pull(lane, n):
            x, filled = real(lane, n)
            if lane.kind == "bed" or lane.mixer.bed is None or not self.rows:
                self.rows.append([0, 0])   # the bed's pull opens a block; a line alone opens its own
            self.rows[-1][0 if lane.kind == "bed" else 1] = filled
            return x, filled
        monkeypatch.setattr(output.MixLane, "_pull", pull)


def never_two_at_once(conns: list) -> None:
    for a, b in zip(conns, conns[1:]):
        assert a["t_end"] is not None and a["t_end"] <= b["t_open"], f"{b['text']!r} opened before {a['text']!r} ended"


@pytest.mark.needs_ffmpeg
def test_a_line_over_music_on_a_call_resegments_and_back(env, segbot, monkeypatch):
    blocks = MixBlocks(monkeypatch)
    f = play(env, seconds=2.5, device="call:5531")
    line = say(env, "over the call music", device="call:5531")
    run(env).join(12)
    said = kind(env, "said")
    assert said[line["utt"]]["reason"] == "done" and said[line["utt"]]["device"] == "call:5531"
    assert said[f["utt"]]["reason"] == "done"
    texts = [c["text"] for c in segbot.conns]
    assert texts == ["[sound song.wav]", "over the call music", "[sound song.wav]"]
    assert [c["ended"] for c in segbot.conns] == ["done", "done", "done"]
    never_two_at_once(segbot.conns)
    # The bed once, with the line inside it (not after it). A line joins once the bed's
    # mixer is open, not at the song's first decoded block (player._next), so it can
    # play alone first, for as long as ffmpeg takes to start: scheduler time, unbounded
    # under load (EARS-453). Count the mixer's blocks instead of bounding wall-clock.
    rows = blocks.rows
    bed = [i for i, (b, _) in enumerate(rows) if b]
    assert sum(b for b, _ in rows) == int(2.5 * RATE), "the bed played other than once, whole"
    assert any(rows[0]), "the mixer started on silence, before either lane wrote"
    assert not any(s for _, s in rows[bed[-1] + 1:]), "the line played on after the bed"
    assert any(b and s for b, s in rows), "the line never played over the bed"
    total = sum(c["samples"] for c in segbot.conns) / output.CALL_RATE
    # the call heard the mixer's blocks, each once: the bed's 2.5 s plus the line alone before it
    assert total == pytest.approx(len(rows) * output.MIX_BLOCK_S, abs=2 / output.CALL_RATE)


@pytest.mark.needs_ffmpeg
def test_a_cut_line_over_music_on_a_call_is_c_then_a_new_bed_segment(env, segbot):
    f = play(env, seconds=3.0, device="call:5531")
    line = say(env, "word " * 12, device="call:5531")
    th = run(env)
    assert wait_for(lambda: started(env, line["utt"]))
    time.sleep(0.3)
    mouth.retract(line["utt"], d=env.d)
    th.join(12)
    said = kind(env, "said")
    assert said[line["utt"]]["reason"] == "retracted" and said[f["utt"]]["reason"] == "done"
    assert [(c["text"], c["ended"]) for c in segbot.conns] == [
        ("[sound song.wav]", "done"), ("word " * 12, "cut"), ("[sound song.wav]", "done")]
    never_two_at_once(segbot.conns)


@pytest.mark.parametrize("duck", [True, False], ids=["duck-on", "duck-off"])
@pytest.mark.needs_ffmpeg
def test_a_laptop_sound_plays_at_once_while_a_file_plays_on_a_call(env, segbot, duck):
    """README success criterion 4, on a call device (verify-001): a song on
    ``call:<N>`` and a Tink for the laptop speakers. The Tink starts with the
    song, and ends before it, on its own output, under either duck setting
    (Q9). The call never hears it."""
    env.settings.write_text(json.dumps({"music_duck": duck}))
    song = play(env, seconds=2.5, device="call:5531")
    tink = play(env, seconds=0.3, name="tink.wav", device="dev-b")
    run(env).join(12)
    saying, said, pos = kind(env, "saying"), kind(env, "said"), order(env)
    assert said[song["utt"]]["reason"] == "done" and said[song["utt"]]["device"] == "call:5531"
    assert said[tink["utt"]]["reason"] == "done" and said[tink["utt"]]["device"] == "dev-b"
    assert pos[("said", tink["utt"])] < pos[("said", song["utt"])], "the Tink waited for the call's song"
    assert abs(ts(saying[tink["utt"]]) - ts(saying[song["utt"]])) < 0.5
    assert len(env.outs["dev-b"]) == 1
    assert [c["text"] for c in segbot.conns] == ["[sound song.wav]"], "the Tink reached the call"


def test_segment_waits_for_the_bots_reply_and_keeps_the_clock(segbot):
    out = output.CallOut("call:5531", RATE, meta={"text": "a", "who": "ivy"})
    out.write(np.zeros(BLOCK, dtype=np.float32))
    clock = out._clock
    out.segment("b", "ivy")
    assert out.result == {"ok": True, "ended": "done"} and out._clock == clock
    out.write(np.zeros(BLOCK, dtype=np.float32))
    out.cut_segment("c", "ivy")
    out.drain()
    assert [(c["text"], c["ended"]) for c in segbot.conns] == [("a", "done"), ("b", "cut"), ("c", "done")]
    never_two_at_once(segbot.conns)


def test_a_new_segments_header_waits_as_long_as_the_first_ones(segbot, monkeypatch):
    """EARS-156 made a line's header wait CALL_SLOW_S (a slow bot is not an absent
    one). A re-segment is a header too: at CALL_LOOK_S a bot slow by 1 s would
    turn a duck into DeviceLost (merge-order line, EARS-152 do-007)."""
    asked: list = []
    real = output._ask

    def spy(path, header, timeout=None):
        if not header.get("probe"):     # find_call's look keeps its own bounds
            asked.append((header["text"], timeout))
        return real(path, header, timeout)
    monkeypatch.setattr(output, "_ask", spy)
    out = output.CallOut("call:5531", RATE, meta={"text": "a", "who": "ivy"})
    out.write(np.zeros(BLOCK, dtype=np.float32))
    out.segment("b", "ivy")
    out.write(np.zeros(BLOCK, dtype=np.float32))
    out.cut_segment("c", "ivy")
    out.drain()
    assert asked == [(t, output.CALL_SLOW_S) for t in ("a", "b", "c")]


def test_two_threads_writing_the_stop_at_once_both_land(env):
    """A bed's barge test and the line's over it fire on the same words: one
    shared part-file name lost a rename (FileNotFoundError, the line said error)."""
    from mouth import player

    env.d.mkdir(parents=True, exist_ok=True)
    errs: list = []
    go = threading.Event()

    def write():
        go.wait()
        try:
            for _ in range(200):
                player._write_stop(env.d, {"t": time.time(), "reason": "barge-in", "flush": True})
        except OSError as e:
            errs.append(e)
    ts_ = [threading.Thread(target=write) for _ in range(4)]
    for t in ts_:
        t.start()
    go.set()
    for t in ts_:
        t.join()
    assert not errs and player._read_stop(env.d)["reason"] == "barge-in"


# -- the two edges of a bed: a line never opens a second output beside its mixer ----

@pytest.mark.needs_ffmpeg
def test_a_line_waits_for_a_starting_beds_mixer_never_opening_its_own(env, monkeypatch):
    real = output.Mixer.__init__

    def slow(self, *a, **kw):
        time.sleep(0.3)                    # a call's probe and header take this long
        real(self, *a, **kw)
    monkeypatch.setattr(output.Mixer, "__init__", slow)
    f = play(env, seconds=1.5, device="dev-a")
    line = say(env, "right behind it", device="dev-a")
    run(env).join(10)
    said = kind(env, "said")
    assert said[line["utt"]]["reason"] == "done" and said[f["utt"]]["reason"] == "done"
    assert len(env.outs["dev-a"]) == 1, "the line opened its own output beside the starting mixer"
    assert order(env)[("said", line["utt"])] < order(env)[("said", f["utt"])]


class SlowDrain(RecOut):
    def drain(self) -> None:
        time.sleep(0.3)


def test_a_line_to_a_closing_mixers_device_waits_for_it_to_close(monkeypatch):
    rig = Rig(monkeypatch, out_cls=SlowDrain)
    bed._MIX["dev-x"] = rig.mix
    try:
        lane = rig.mix.bed_lane()
        lane.write(np.zeros(BLOCK, dtype=np.float32))
        threading.Thread(target=lane.drain, daemon=True).start()
        assert wait_for(lambda: rig.mix.closing, 2.0)
        assert bed.mixer_for("dev-x") is None
        assert rig.mix.wait_closed(0), "mixer_for returned while the mixer's output was still open"
    finally:
        bed._MIX.pop("dev-x", None)
