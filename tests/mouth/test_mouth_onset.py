"""EARS-238: a line dips the moment he starts talking over it, and his words cut it.

The ears' ``vad`` onset rows are faked into a scratch heard log; the device is
a fake PortAudio whose callback is called by hand (or pulled in real time by
``PullingSd``). No sound, no live ears, no live mouth.
"""
from __future__ import annotations

import json
import time
from datetime import datetime

import numpy as np
import pytest

from ears import heard
from mouth import hold, onset, output

from test_mouth import FakeSd, PullingSd, say  # noqa: F401
from test_mouth_inbox import box, lines, run_player  # noqa: F401  (fixture)
from tests import env_fence

RATE = 24000
BLOCK = int(RATE * 0.05)


@pytest.fixture
def ears(tmp_path, monkeypatch):
    """vad(speech=True, source="mic") / partial(text): rows in a scratch heard log."""
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm"))
    monkeypatch.setenv("MOUTH_BARGE", "1")
    monkeypatch.setenv("MOUTH_SETTINGS", str(tmp_path / "mouth.json"))
    env_fence.delenv(monkeypatch, "MOUTH_BARGE_WORDS")
    heard.log_dir().mkdir(parents=True, exist_ok=True)

    class Ears:
        settings = tmp_path / "mouth.json"

        @staticmethod
        def vad(speech=True, source="mic"):
            return heard.append("event", event=heard.EV_VAD, speech=speech, at=1.0, source=source)

        @staticmethod
        def partial(text, source="mic"):
            rec = heard.append("partial", text=text, source=source)
            for c in hold._caches.values():
                c.update(t=0.0, v=None)
            return rec
    return Ears


# -- the gain, in the callback ---------------------------------------------------

def test_the_line_duck_ramps_down_fast_and_back_up_slowly():
    d = output.LineDuck(RATE)
    buf = np.ones((BLOCK, 2), dtype=np.float32)
    d.apply(buf)
    assert (buf == 1.0).all()                               # no duck: untouched
    d.set(0.25)
    assert d.ducked
    buf = np.ones((BLOCK, 2), dtype=np.float32)
    d.apply(buf)
    assert buf[0, 0] < 1.0 and buf[-1, 0] == pytest.approx(0.25)    # 50 ms down, full depth
    assert (buf[:, 0] == buf[:, 1]).all()                   # every channel alike
    buf = np.ones((BLOCK, 1), dtype=np.float32)
    d.apply(buf)
    assert np.allclose(buf, 0.25)                           # held
    d.set(1.0)
    buf = np.ones((BLOCK, 1), dtype=np.float32)
    d.apply(buf)
    assert buf[-1, 0] == pytest.approx(0.25 + 0.75 * 0.05 / output.LINE_DUCK_UP_S, abs=1e-4)
    assert len(d.trace) == 1 and d.trace[0][1] >= d.trace[0][0]


class ManualSd(FakeSd):
    """A stream that never pulls by itself: the test calls the callback."""

    def OutputStream(self, *, device, samplerate, channels, dtype, callback):
        class S:
            latency = 0.0

            def start(s):
                pass

            def abort(s):
                pass

            def close(s):
                pass
        return S()


def test_the_duck_reaches_audio_already_queued(monkeypatch):
    """The point of putting it in the callback: the 200 ms already queued at
    full level is ducked as the device pulls it, not played out first."""
    monkeypatch.setattr(output, "_sd", lambda: ManualSd())
    out = output.DeviceOut("MacBook Pro Speakers", RATE)
    for _ in range(out.queue_blocks):
        out.write(np.full(BLOCK, 0.5, dtype=np.float32))  # written BEFORE the duck
    out.duck.set(0.25)
    buf = np.zeros((BLOCK, 1), dtype=np.float32)
    out._callback(buf, BLOCK, None, None)
    assert buf[0, 0] > 0.45 and buf[-1, 0] == pytest.approx(0.125)   # the first queued block, ducked
    assert out.frames_played == BLOCK
    out.abort()


def test_a_line_over_music_shares_the_devices_duck(monkeypatch):
    """The Mixer path: the line is a MixLane; its duck is the device's, after
    the lane's queue and the device's."""
    monkeypatch.setattr(output, "_sd", lambda: ManualSd())
    mix = output.Mixer("MacBook Pro Speakers", RATE)
    bed_lane = mix.bed_lane()
    line = mix.speech_lane(RATE)
    assert isinstance(line.duck, output.LineDuck) and line.duck is mix.out.duck is bed_lane.duck
    line.abort()
    bed_lane.abort()


def test_no_duck_on_the_null_device():
    assert getattr(output.NullOut(RATE), "duck", None) is None
    assert onset.for_line(output.NullOut(RATE), "airpods", settings={}) is None


# -- the onsets ------------------------------------------------------------------

def test_the_tail_reads_only_new_voice_onsets_from_its_own_ears(ears):
    ears.vad()                                              # before the line: not ours
    tail = onset.OnsetTail(hold.MIC)
    assert tail.poll() == []
    ears.partial("hello there")
    ears.vad(speech=False)
    ears.vad(source="call")                                 # another source's ears
    assert tail.poll() == []
    rec = ears.vad()
    got = tail.poll()
    assert [r["seq"] for r in got] == [rec["seq"]] and tail.poll() == []
    with open(heard.log_path(), "ab") as f:                 # a write still in flight
        f.write(b'{"kind": "event", "event": "vad", "speech": true, "source": "mic"')
    assert tail.poll() == []
    with open(heard.log_path(), "ab") as f:
        f.write(b', "seq": 99}\n')
    assert [r["seq"] for r in tail.poll()] == [99]


def test_onset_ducks_the_line(ears):
    d = output.LineDuck(RATE)
    bd = onset.BargeDuck(d, hold.MIC, 0.25, 2.5)
    bd.step(now=10.0)
    assert not d.ducked                                     # nobody talking
    ears.vad()
    bd.step(now=10.01)
    assert d.target == 0.25 and bd.onsets == 1


def test_a_false_onset_only_dips_the_line_and_it_comes_back(ears):
    """Road noise: an onset, no words. The line stays down while the voice is
    on, and comes back up recover_s after its OFFSET (EARS-315 R12); a new
    burst before then puts it down again and cancels the recovery."""
    d = output.LineDuck(RATE)
    bd = onset.BargeDuck(d, hold.MIC, 0.25, 2.5)
    ears.vad()
    bd.step(now=10.0)
    bd.step(now=13.0)
    assert d.ducked                                         # 3 s on: still down (no offset yet)
    ears.vad(speech=False)
    bd.step(now=13.1)
    ears.vad()                                              # another burst: still him?
    bd.step(now=14.0)
    ears.vad(speech=False)
    bd.step(now=14.5)
    bd.step(now=16.9)
    assert d.ducked                                         # 2.4 s after the last offset
    bd.step(now=17.0)
    assert not d.ducked and d.target == 1.0 and bd.recovered == 1
    assert (bd.onsets, bd.offsets, bd.capped) == (2, 2, False)
    bd.stop()
    assert d.target == 1.0


def test_onset_then_his_words_confirm_the_cut_while_ducked(ears):
    line = "the weather on the coast and the tide tonight"
    t0 = time.time() - 0.5
    d = output.LineDuck(RATE)
    bd = onset.BargeDuck(d, hold.MIC, 0.25, 2.5)
    ears.vad()
    bd.step(now=0.0)
    ears.partial("tell me")
    assert hold.barge(t0, line, words=3) is None            # one word: ducked, not cut
    bd.step(now=0.5)
    assert d.ducked
    ears.partial("tell me about the bridge works tomorrow")
    got = hold.barge(t0, line, words=3)
    assert got is not None and got["text"].endswith("tomorrow")
    bd.step(now=1.3)
    assert d.ducked                                         # cut confirmed before recover
    bd.stop()                                               # the line ends: no duck left behind
    assert d.target == 1.0


def test_the_settings(ears, monkeypatch):
    assert (hold.barge_duck_level({}), hold.barge_duck_recover_s({}), hold.barge_words(None, {})) \
        == (25, 2.5, 2)
    s = {"barge_duck_level": 40, "barge_duck_recover_s": 1.5, "barge_words": 3}
    assert (hold.barge_duck_level(s), hold.barge_duck_recover_s(s), hold.barge_words("airpods", s)) \
        == (40, 1.5, 3)
    bad = {"barge_duck_level": 101, "barge_duck_recover_s": -1, "barge_words": 0}
    assert (hold.barge_duck_level(bad), hold.barge_duck_recover_s(bad), hold.barge_words(None, bad)) \
        == (25, 2.5, 2)
    assert hold.barge_words(None, {"barge_words": 2.5}) == 2 and hold.barge_words(None, {"barge_words": True}) == 2
    assert hold.barge_words("call:1", s) == 3               # a call takes the user's too (EARS-315)
    ears.settings.write_text(json.dumps({"barge_words": 5, "barge_duck_level": 100}))
    assert hold.barge_words("airpods") == 5                 # read from the mouth's settings file
    monkeypatch.setenv("MOUTH_BARGE_WORDS", "4")
    assert hold.barge_words("airpods") == 5                 # the setting beats the env (EARS-315)
    d = output.LineDuck(RATE)

    class Out:
        duck = d
    assert onset.for_line(Out(), "airpods") is None         # 100: the duck is off


# -- the player ------------------------------------------------------------------

@pytest.fixture
def played(box, ears, monkeypatch):
    """A line on a fake device that pulls in real time; ``.ducks`` the
    BargeDuck each line started."""
    sd = PullingSd()
    monkeypatch.setattr(output, "_sd", lambda: sd)
    monkeypatch.setenv("MOUTH_BARGE_DEVICES", "speakers")   # as if headphones
    monkeypatch.setenv("MOUTH_RESUME_S", "0")   # a cut line's rest is not queued again
    ducks = []
    real = onset.for_line

    def spy(*a, **kw):
        bd = real(*a, **kw)
        ducks.append(bd)
        return bd
    monkeypatch.setattr(onset, "for_line", spy)
    box.ducks, box.ears = ducks, ears
    return box


def _wait(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.005)
    return False


def test_the_player_ducks_at_onset_and_recovers_when_no_cut_follows(played):
    played.ears.settings.write_text(json.dumps({"barge_duck_recover_s": 0.3}))
    item = say("a line long enough to talk over, for a while", device="MacBook Pro Speakers")
    th = run_player(played.d)
    assert _wait(lambda: played.ducks)
    bd = played.ducks[0]
    duck = bd.duck
    t = time.monotonic()
    played.ears.vad()
    assert _wait(lambda: duck.trace, 1.0)                   # the callback applied it
    asked, applied, ahead = duck.trace[0]
    assert applied - t < 0.1 and duck.target == 0.25
    assert ahead == pytest.approx(0.01)                     # PullingSd's stream latency: past the duck
    played.ears.vad(speech=False)                           # his voice stops (EARS-315 R12)
    assert _wait(lambda: not duck.ducked, 1.0)              # no words: it came back
    th.join(8)
    assert not th.is_alive()
    said = next(r for r in lines(played.logs) if r["kind"] == "said" and r["utt"] == item["utt"])
    assert said["reason"] == "done" and bd.recovered == 1


def test_the_player_ducks_at_onset_and_his_words_cut(played):
    item = say("a line long enough to talk over, for a while longer than that",
               device="MacBook Pro Speakers")
    th = run_player(played.d)
    assert _wait(lambda: played.ducks)
    duck = played.ducks[0].duck
    played.ears.vad()
    assert _wait(lambda: duck.ducked, 1.0)
    played.ears.partial("tell me about the bridge works tomorrow")
    th.join(8)
    assert not th.is_alive()
    said = next(r for r in lines(played.logs) if r["kind"] == "said" and r["utt"] == item["utt"])
    assert said["reason"] == "barge-in" and said["cut"] is True
    assert duck.target == 1.0                               # stopped with the line


# -- the duck record (EARS-270 design.md s2) ---------------------------------------

def _vad_row(seq: int, ts: str, speech: bool = True) -> dict:
    """An onset row (an offset with ``speech=False``) with a chosen ``seq`` and
    ``ts``, as the ears write it."""
    rec = {"ts": ts, "seq": seq, "kind": heard.EVENT, "event": heard.EV_VAD, "speech": speech, "source": "mic"}
    with open(heard.log_path(), "a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def test_the_duck_record_dips_wall_to_wall(ears):
    """dip_ms is the wall time of the first dip minus the onset row's ts, both
    wall clocks: 08:00:39.186 (the 08:00 onset, seq 448237) to .204 is 18 ms.
    The recover runs on the injected monotonic clock, which never enters it."""
    onset_ts = "2030-01-07T08:00:39.186+11:00"
    walls = iter([datetime.fromisoformat(onset_ts).timestamp() + 0.018, 9e9])
    d = output.LineDuck(RATE)
    bd = onset.BargeDuck(d, hold.MIC, 0.25, 2.5, clock=lambda: 1000.0, wall=lambda: next(walls))
    _vad_row(448237, onset_ts)
    bd.step(now=10.0)                                       # the first dip: wall stamped once
    _vad_row(448238, "2030-01-07T08:00:40.000+11:00")
    bd.step(now=11.0)                                       # a second onset: no new stamp
    _vad_row(448239, "2030-01-07T08:00:41.000+11:00", speech=False)
    bd.step(now=11.5)                                       # his offset: the recover clock starts
    bd.step(now=14.0)                                       # 2.5 s after the offset: back up
    d.trace.extend([(9.0, 9.01, 0.2),                       # an earlier line's dip on this device
                    (10.001, 10.032, 0.2004)])              # this line's: asked at/after 10.0
    bd.stop()
    rec = bd.record(utt="42ec8fb04957", device="Theo's AirPods", cut=False, reason="done")
    assert rec == {"kind": "duck", "utt": "42ec8fb04957", "device": "Theo's AirPods",
                   "level": 0.25, "recover_s": 2.5, "max_s": 4.0, "onsets": 2, "offsets": 1,
                   "started_ducked": False,
                   "first_onset_seq": 448237, "first_onset_ts": onset_ts,
                   "ducked_ts": rec["ducked_ts"], "dip_ms": 18, "applied_ms": 31, "ahead_ms": 200,
                   "recovered": 1, "capped": False, "capped_words": None, "cut": False, "reason": "done"}
    assert datetime.fromisoformat(rec["ducked_ts"]) == datetime.fromisoformat("2030-01-07T08:00:39.204+11:00")


def test_a_duck_nobody_set_off_records_zero_onsets(ears):
    bd = onset.BargeDuck(output.LineDuck(RATE), hold.MIC, 0.25, 2.5)
    bd.step(now=10.0)
    rec = bd.record(utt="u", device="d", cut=True, reason="stop")
    assert (rec["onsets"], rec["first_onset_seq"], rec["first_onset_ts"], rec["ducked_ts"],
            rec["dip_ms"], rec["applied_ms"], rec["ahead_ms"], rec["recovered"]) \
        == (0, None, None, None, None, None, None, 0)
    assert (rec["cut"], rec["reason"]) == (True, "stop")


def test_a_record_that_cannot_be_built_returns_what_it_has(ears):
    bd = onset.BargeDuck(output.LineDuck(RATE), hold.MIC, 0.25, 2.5)
    bd.seen.append((10.0, "not a row"))                    # a row it cannot read
    rec = bd.record(utt="u", device="d", cut=False, reason="done")
    assert rec["kind"] == "duck" and rec["onsets"] == 0 and "dip_ms" not in rec
    assert list(rec)[-2:] == ["cut", "reason"] and (rec["cut"], rec["reason"]) == (False, "done")


def _printed(text: str, kind: str) -> list:
    out = []
    for raw in text.splitlines():
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("kind") == kind:
            out.append(rec)
    return out


def test_the_player_prints_one_duck_record_for_a_line_that_recovered(played, capsys):
    played.ears.settings.write_text(json.dumps({"barge_duck_recover_s": 0.3}))
    item = say("a line long enough to talk over, for a while", device="MacBook Pro Speakers")
    th = run_player(played.d)
    assert _wait(lambda: played.ducks)
    duck = played.ducks[0].duck
    row = played.ears.vad()
    assert _wait(lambda: duck.trace, 1.0)
    played.ears.vad(speech=False)                           # his voice stops (EARS-315 R12)
    assert _wait(lambda: not duck.ducked, 1.0)              # no words: it came back
    th.join(8)
    assert not th.is_alive()
    said = next(r for r in lines(played.logs) if r["kind"] == "said" and r["utt"] == item["utt"])
    assert said["reason"] == "done"
    recs = _printed(capsys.readouterr().out, "duck")
    assert len(recs) == 1                                   # one per line
    rec = recs[0]
    assert (rec["utt"], rec["device"], rec["level"], rec["recover_s"]) == (
        item["utt"], "MacBook Pro Speakers", 0.25, 0.3)
    assert (rec["onsets"], rec["first_onset_seq"], rec["first_onset_ts"]) == (1, row["seq"], row["ts"])
    assert (rec["offsets"], rec["started_ducked"], rec["max_s"]) == (1, False, 4.0)
    assert isinstance(rec["dip_ms"], int) and 0 <= rec["dip_ms"] < 1000
    assert datetime.fromisoformat(rec["ducked_ts"]) >= datetime.fromisoformat(row["ts"])
    assert isinstance(rec["applied_ms"], int) and rec["applied_ms"] >= 0
    assert (rec["recovered"], rec["capped"], rec["capped_words"]) == (1, False, None)
    assert (rec["cut"], rec["reason"]) == (False, "done")


def test_the_player_prints_the_duck_record_of_a_line_his_words_cut(played, capsys):
    item = say("a line long enough to talk over, for a while longer than that",
               device="MacBook Pro Speakers")
    th = run_player(played.d)
    assert _wait(lambda: played.ducks)
    duck = played.ducks[0].duck
    row = played.ears.vad()
    assert _wait(lambda: duck.ducked, 1.0)
    played.ears.partial("tell me about the bridge works tomorrow")
    th.join(8)
    assert not th.is_alive()
    recs = _printed(capsys.readouterr().out, "duck")
    assert [(r["utt"], r["onsets"], r["first_onset_seq"], r["recovered"], r["cut"], r["reason"])
            for r in recs] == [(item["utt"], 1, row["seq"], 0, True, "barge-in")]
    assert recs[0]["dip_ms"] is not None and recs[0]["dip_ms"] >= 0


@pytest.mark.parametrize("why", ["level 100", "not a barge device"])
def test_a_line_with_no_duck_prints_no_duck_record(played, monkeypatch, capsys, why):
    if why == "level 100":
        played.ears.settings.write_text(json.dumps({"barge_duck_level": 100}))
    else:
        monkeypatch.setenv("MOUTH_BARGE_DEVICES", "headphones")
    item = say("a short line", device="MacBook Pro Speakers")
    th = run_player(played.d)
    assert _wait(lambda: any(r["kind"] == "saying" and r["utt"] == item["utt"] for r in lines(played.logs)))
    played.ears.vad()                                       # an onset it would have recorded
    th.join(8)
    assert not th.is_alive()
    assert played.ducks == ([None] if why == "level 100" else [])   # for_line said no / was never asked
    assert next(r for r in lines(played.logs) if r["kind"] == "said" and r["utt"] == item["utt"])
    assert _printed(capsys.readouterr().out, "duck") == []


def test_a_record_that_raises_never_stops_the_line(played, monkeypatch, capsys):
    def boom(self, **kw):
        raise RuntimeError("no record")

    monkeypatch.setattr(onset.BargeDuck, "record", boom)
    played.ears.settings.write_text(json.dumps({"barge_duck_recover_s": 0.3}))
    item = say("a line long enough to talk over, for a while", device="MacBook Pro Speakers")
    th = run_player(played.d)
    assert _wait(lambda: played.ducks)
    played.ears.vad()
    th.join(8)
    assert not th.is_alive()
    said = next(r for r in lines(played.logs) if r["kind"] == "said" and r["utt"] == item["utt"])
    assert (said["reason"], said["cut"]) == ("done", False)  # it ends done, its said row written
    assert "no record" not in str(said.get("detail"))
    assert _printed(capsys.readouterr().out, "duck") == []
