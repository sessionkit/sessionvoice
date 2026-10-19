"""EARS-152 do-005: the duck. An on-bed ducks under speech, both ways.

design.md s4 and s6. A played file with ducking on (an ON-bed, the default) has a
``Ducker`` with two causes:

- ``say``: any line playing in this player, from its first frame until the
  player finds nothing playable, so stacked lines give ONE duck (no pumping);
- ``barge``: he has the floor on the bed's device (``hold.speaking_over``),
  only where ``hold.barge_device`` is true (no barge-duck on laptop speakers, Q1).

The mixer ramps the bed to ``level/100`` of its own volume, per sample (down
150 ms, up 500 ms once both causes have been clear for ``restore_s``). Rows:
``event: duck`` / ``event: unduck``; the music's ``said`` counts ``ducks`` and
``ducked_s``; a line over the music carries ``over: <music utt>``.

Seams: duck-on-barge, duck-under-say, spoken-still-cut, volume-bed-times-duck,
and review 1546's F1 (a line never opens beside a closing mixer).
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from itertools import count

import numpy as np
import pytest

import mouth
from ears import heard
from mouth import bed, hold, output

from test_mouth_bed import (RecOut, env, kind, play, rows, run, say,  # noqa: F401 - fixtures
                            started, wait_for)
from test_mouth_mixer import Rig, SlowDrain

RATE = 24000
BLOCK = int(RATE * output.MIX_BLOCK_S)
_seq = count(1000)


def ts(r: dict) -> float:
    return datetime.fromisoformat(r["ts"]).timestamp()


def order(env) -> dict:
    return {(r["kind"], r["utt"]): i for i, r in enumerate(sorted(rows(env), key=lambda r: r["seq"]))}


def events(env, name: str) -> list:
    return [r for r in rows(env) if r["kind"] == "event" and r.get("event") == name]


def heard_row(kind_: str, text: str = "", source: str = "mic", speaker=None, age_s: float = 0.0) -> int:
    """A row in the ears' own log (a partial or a turn), and its side-car label."""
    seq = next(_seq)
    t = datetime.fromtimestamp(time.time() - age_s).astimezone().isoformat(timespec="milliseconds")
    with open(heard.log_path(), "a") as f:
        f.write(json.dumps({"ts": t, "seq": seq, "kind": kind_, "text": text, "source": source}) + "\n")
    if speaker is not None:
        with open(heard.speakers_path(heard.log_path()), "a") as f:
            f.write(json.dumps({"seq": seq, "kind": kind_, "speaker": speaker}) + "\n")
    for c in hold._caches.values():
        c.update(t=0.0, v=None)
    return seq


def partial(text="hang on a second", **kw) -> int:
    return heard_row("partial", text, **kw)


def turn(text="hang on a second", **kw) -> int:
    return heard_row("turn", text, **kw)


def settings(env, **kw) -> None:
    env.settings.write_text(json.dumps(kw))


@pytest.fixture
def mixers(monkeypatch):
    """The first mixer a bed lane opened on, by device name (its gain trace)."""
    seen: dict = {}
    real = output.Mixer.bed_lane

    def spy(self, pan=None, volume=None, meta=None, utt=None):
        seen.setdefault(self.name, self)
        return real(self, pan, volume, meta, utt)
    monkeypatch.setattr(output.Mixer, "bed_lane", spy)
    return seen


# -- hold.speaking_over: the barge cause ------------------------------------------

def test_speaking_over_is_the_floor_on_the_devices_barge_sources(env):
    assert hold.speaking_over("null", who=None) is None
    partial("hang on")
    assert hold.speaking_over("null", who=None)["text"] == "hang on"
    turn("hang on")
    assert hold.speaking_over("null", who=None) is None              # his turn ended
    partial("an old one", age_s=hold.idle_s() + 1)
    assert hold.speaking_over("null", who=None) is None              # the floor went idle
    partial("from the room")
    assert hold.speaking_over("call:7", who=None) is None            # a call hears only the call
    partial("on the phone", source="call")
    assert hold.speaking_over("call:7", who=None)["text"] == "on the phone"


def test_speaking_over_keeps_the_barge_in_who_filter(env, monkeypatch):
    monkeypatch.setenv("MOUTH_LABEL_WAIT_S", "0.3")
    theo = frozenset({"theo"})
    partial("the radio says", speaker="other", age_s=0.6)
    assert hold.speaking_over("null", who=theo) is None              # named, and not him
    partial("hang on", speaker="theo", age_s=0.5)
    assert hold.speaking_over("null", who=theo)["text"] == "hang on"
    turn(age_s=0.4)
    partial("who is this")
    assert hold.speaking_over("null", who=theo) is None              # not named yet: wait for it
    time.sleep(0.35)
    for c in hold._caches.values():
        c.update(t=0.0, v=None)
    assert hold.speaking_over("null", who=theo)["text"] == "who is this"   # never named: counts
    settings(env, barge_in=["theo"])
    turn(age_s=0.0)
    time.sleep(0.01)
    partial("the radio again", speaker="other")
    assert hold.speaking_over("null") is None                        # read from the settings


# -- the mixer's ramp: per sample, down 150 ms, up 500 ms -----------------------------

def test_the_duck_ramps_per_sample_down_150_ms_and_up_500_ms(monkeypatch):
    rig = Rig(monkeypatch)
    lane = rig.mix.bed_lane(volume=0.5)
    ones = np.ones(BLOCK, dtype=np.float32)
    stop = threading.Event()

    def feed():
        while not stop.is_set():
            lane.write(ones)
    th = threading.Thread(target=feed, daemon=True)
    th.start()
    assert wait_for(lambda: len(rig.outs[0].blocks) >= 4, 2.0)
    rig.mix.set_duck(0.2)
    assert wait_for(lambda: rig.mix.duck_ramp == 0.2, 2.0)
    rig.mix.set_duck(1.0)
    assert wait_for(lambda: rig.mix.duck_ramp == 1.0, 2.0)
    time.sleep(0.1)
    stop.set()
    th.join(2.0)
    lane.drain()
    w = rig.written
    down = np.flatnonzero(w < 0.5 - 1e-6)
    assert len(down), "it never ducked"
    first, low = down[0], np.flatnonzero(np.isclose(w, 0.1, atol=1e-6))
    # 1.0 -> 0.2 over 150 ms (bed at 0.5: 0.5 -> 0.1), sample by sample: no step bigger than one sample's
    assert (low[0] - first) / RATE == pytest.approx(output.DUCK_DOWN_S, abs=2 / RATE)
    seg = w[first - 1:low[0] + 1]
    assert np.all(np.diff(seg) <= 0) and np.abs(np.diff(seg)).max() < 0.4 / (output.DUCK_DOWN_S * RATE) * 1.01
    back = np.flatnonzero(w[low[-1]:] >= 0.5 - 1e-6)[0]
    assert back / RATE == pytest.approx(output.DUCK_UP_S, abs=2 / RATE)
    assert w[-1] == pytest.approx(0.5)


# -- duck-on-barge ------------------------------------------------------------------

@pytest.mark.needs_ffmpeg
def test_his_words_duck_the_music_and_it_comes_back(env, mixers, monkeypatch):
    monkeypatch.setenv("MOUTH_BARGE_DEVICES", "null")
    settings(env, music_duck_restore_s=0.3)
    f = play(env, seconds=3.0)
    stale = play(env, seconds=0.3, name="next.wav")      # the next song: his words never drop it
    th = run(env, idle=0.5)
    assert wait_for(lambda: started(env, f["utt"]))
    time.sleep(0.3)
    partial("hang on, wait")                             # a stop word, even: it ducks, never cuts
    assert wait_for(lambda: events(env, "duck"), 2.0)
    assert wait_for(lambda: [m["ducked"] for m in mouth._music(env.d)] == [True], 1.0)   # mouth status
    time.sleep(0.4)
    turn("hang on, wait")
    assert wait_for(lambda: events(env, "unduck"), 2.0)
    assert wait_for(lambda: [m["ducked"] for m in mouth._music(env.d)] == [False], 1.0)
    th.join(12)
    said = kind(env, "said")
    d, u = events(env, "duck"), events(env, "unduck")
    assert len(d) == 1 and len(u) == 1
    assert (d[0]["utt"], d[0]["level"], d[0]["why"], d[0]["causes"], d[0]["device"], d[0]["source"]) == \
        (f["utt"], 20, "barge", ["barge"], "null", "mouth")
    assert (u[0]["utt"], u[0]["device"], u[0]["source"]) == (f["utt"], "null", "mouth")
    assert 0.6 < u[0]["ducked_s"] < 1.6                  # 0.4 s of his words, the turn, restore 0.3 s
    assert ts(u[0]) - ts(d[0]) == pytest.approx(u[0]["ducked_s"], abs=0.15)
    m = said[f["utt"]]
    assert (m["reason"], m["cut"], m["ducks"]) == ("done", False, 1)
    assert m["ducked_s"] == pytest.approx(u[0]["ducked_s"], abs=0.01)
    assert m["played_s"] == pytest.approx(3.0, abs=0.06)
    assert said[stale["utt"]]["reason"] == "done", "the barge-in flush dropped the next song"
    g = [x for _, x in mixers["null"].trace]
    assert min(g) == pytest.approx(0.2) and g[-1] == pytest.approx(1.0)
    assert not any(r.get("reason") == "barge-in" for r in said.values())


@pytest.mark.needs_ffmpeg
def test_no_barge_duck_where_barge_in_does_not_count(env, mixers):
    """Q1: on the laptop speakers the mic hears the song; his floor never ducks it."""
    f = play(env, seconds=1.5, device="dev-a")                # not a barge device
    th = run(env)
    assert wait_for(lambda: started(env, f["utt"]))
    partial("la la la the lyrics")
    th.join(8)
    assert not events(env, "duck")
    assert kind(env, "said")[f["utt"]]["ducks"] == 0
    assert {g for _, g in mixers["dev-a"].trace} == {1.0}


# -- duck-under-say ------------------------------------------------------------------

@pytest.mark.needs_ffmpeg
def test_a_line_plays_over_the_ducked_music_and_two_stacked_lines_are_one_duck(env, mixers):
    settings(env, music_duck_restore_s=0.3)
    # 5 s: the lines end ~3.1 s in, then restore_s, a poll and the 500 ms ramp up
    # must all fit inside the song for the trace to reach 1.0 (4 s did not, 1 in 6)
    f = play(env, seconds=5.0, device="dev-a")
    one = say(env, "the first line", device="dev-a")
    two = say(env, "and a second one right behind it", device="dev-a")
    run(env, idle=0.5).join(15)
    saying, said = kind(env, "saying"), kind(env, "said")
    pos = order(env)
    assert ts(saying[one["utt"]]) - ts(saying[f["utt"]]) < 0.5, "the line waited for the song"
    assert pos[("said", two["utt"])] < pos[("said", f["utt"])]
    d, u = events(env, "duck"), events(env, "unduck")
    assert len(d) == 1 and len(u) == 1, "stacked lines pumped the music"
    assert (d[0]["why"], d[0]["level"], d[0]["utt"]) == ("say", 20, f["utt"])
    # with the line (or with the music's first frame, if ffmpeg was slower), not a poll later
    assert ts(d[0]) - max(ts(saying[one["utt"]]), ts(saying[f["utt"]])) < 0.15
    assert ts(u[0]) > ts(said[two["utt"]])                  # back up after the LAST line
    assert ts(u[0]) - ts(said[two["utt"]]) == pytest.approx(0.3, abs=0.25)
    assert said[one["utt"]]["over"] == f["utt"] and said[two["utt"]]["over"] == f["utt"]
    m = said[f["utt"]]
    assert (m["reason"], m["cut"], m["ducks"]) == ("done", False, 1)
    g = [x for _, x in mixers["dev-a"].trace]
    assert min(g) == pytest.approx(0.2) and g[-1] == pytest.approx(1.0)


@pytest.mark.needs_ffmpeg
def test_a_slow_hand_off_between_stacked_lines_is_still_one_duck(env, mixers, monkeypatch):
    """No pumping, pinned: the second line starts 0.6 s after the first ends
    (late synthesis), longer than restore_s. The say cause holds because the
    player found a playable line, not because the gap was short (review 1626:
    the test above passes 2 runs in 3 with the cause let go after every line)."""
    from mouth import player
    settings(env, music_duck_restore_s=0.2)
    real, n = player.play_one, [0]

    def late(item, d, *a, **kw):
        n[0] += 1
        if n[0] == 2:
            time.sleep(0.6)
        return real(item, d, *a, **kw)
    monkeypatch.setattr(player, "play_one", late)
    f = play(env, seconds=4.0, device="dev-a")
    say(env, "the first line", device="dev-a")
    say(env, "and a second one", device="dev-a")
    run(env, idle=0.5).join(15)
    assert len(events(env, "duck")) == 1 and len(events(env, "unduck")) == 1, "stacked lines pumped"
    assert kind(env, "said")[f["utt"]]["ducks"] == 1


@pytest.mark.needs_ffmpeg
def test_a_line_on_another_device_ducks_the_music_too_and_is_not_over_it(env, mixers):
    """Q6: the mouth is one voice; the line is not mixed over the bed, so no ``over``."""
    settings(env, music_duck_restore_s=0.2)
    f = play(env, seconds=3.0, device="dev-a")
    line = say(env, "on the other device", device="dev-b")
    run(env, idle=0.5).join(12)
    said = kind(env, "said")
    assert [e["why"] for e in events(env, "duck")] == ["say"] and len(events(env, "unduck")) == 1
    assert "over" not in said[line["utt"]]
    assert said[f["utt"]]["ducks"] == 1


@pytest.mark.needs_ffmpeg
def test_with_ducking_off_nothing_ducks(env):
    f = play(env, seconds=1.0, duck=False)
    line = say(env, "elsewhere", device="dev-b")
    run(env).join(8)
    said = kind(env, "said")
    assert not events(env, "duck") and not events(env, "unduck")
    assert said[f["utt"]]["ducks"] == 0 and said[f["utt"]]["ducked_s"] == 0.0
    assert "over" not in said[line["utt"]]


# -- spoken-still-cut ------------------------------------------------------------------

@pytest.mark.needs_ffmpeg
def test_a_line_over_the_music_is_still_cut_by_his_words_and_the_music_plays_on(env, monkeypatch):
    monkeypatch.setenv("MOUTH_BARGE_DEVICES", "null")
    monkeypatch.setenv("MOUTH_RESUME_S", "20")
    settings(env, music_duck_restore_s=0.3)
    f = play(env, seconds=4.0)
    line = say(env, "word " * 12)                              # ~4 s over the music
    stale = say(env, "queued before he spoke")
    th = run(env, idle=0.5)
    assert wait_for(lambda: started(env, line["utt"]))
    time.sleep(0.4)
    partial("hang on, wait")
    assert wait_for(lambda: line["utt"] in kind(env, "said"), 2.0)
    time.sleep(0.3)
    turn("hang on, wait")
    th.join(15)
    said = kind(env, "said")
    cut = said[line["utt"]]
    assert (cut["reason"], cut["cut"], cut["over"]) == ("barge-in", True, f["utt"])
    assert said[stale["utt"]]["reason"] == "barge-in"        # today's flush: stale words go
    resumed = [r for r in said.values() if r.get("resume_of") == line["utt"]]
    assert resumed and resumed[0]["reason"] == "done"        # the fast follow, as today
    m = said[f["utt"]]
    assert (m["reason"], m["cut"]) == ("done", False)
    assert m["played_s"] == pytest.approx(4.0, abs=0.06)
    assert m["ducks"] >= 1 and any(r.get("why") in ("say", "barge") for r in events(env, "duck"))


# -- duck-off: off means "don't dip", never "cut" (EARS-295 T4 + T9, super ruling Q1) --

@pytest.mark.parametrize("how", ["setting", "flag"])
@pytest.mark.needs_ffmpeg
def test_t4_with_ducking_off_his_singing_neither_dips_nor_cuts_the_music(env, mixers, monkeypatch, how):
    """Karaoke: he sings over the music. It stays at its own level, plays to the
    end, a line queued meanwhile mixes over it, and the next song survives."""
    monkeypatch.setenv("MOUTH_BARGE_DEVICES", "null")
    if how == "setting":
        settings(env, music_duck=False, music_duck_restore_s=0.3)
    else:
        settings(env, music_duck_restore_s=0.3)
    kw = {"duck": False} if how == "flag" else {}
    f = play(env, seconds=3.0, **kw)
    nxt = play(env, seconds=0.3, name="next.wav", **kw)
    th = run(env, idle=0.5)
    assert wait_for(lambda: started(env, f["utt"]))
    time.sleep(0.3)
    partial("la la la, hang on, wait")                # a stop word, even: off never cuts
    time.sleep(0.4)
    line = say(env, "a line while he sings")
    assert wait_for(lambda: line["utt"] in kind(env, "said"), 3.0)
    assert not (env.d / "stop").exists()               # no barge-in flush was written
    turn("la la la, hang on, wait")
    th.join(12)
    said = kind(env, "said")
    m = said[f["utt"]]
    assert (m["reason"], m["cut"], m["ducks"], m["ducked_s"]) == ("done", False, 0, 0.0)
    assert m["played_s"] == pytest.approx(3.0, abs=0.06)
    assert (said[line["utt"]]["reason"], said[line["utt"]]["over"]) == ("done", f["utt"])
    assert said[nxt["utt"]]["reason"] == "done", "the next song was dropped"
    assert not events(env, "duck") and not events(env, "unduck")
    assert {g for _, g in mixers["null"].trace} == {1.0}
    assert not any(r.get("reason") == "barge-in" for r in said.values())


@pytest.mark.needs_ffmpeg
def test_t9_with_ducking_off_a_spoken_line_is_still_cut_by_his_words(env, mixers, monkeypatch):
    """The super's Q1 condition: only the music is no longer cut. His words cut
    a spoken line over a duck-off bed, the flush drops stale words, and the
    music and the next song play on."""
    monkeypatch.setenv("MOUTH_BARGE_DEVICES", "null")
    monkeypatch.setenv("MOUTH_RESUME_S", "20")
    settings(env, music_duck=False, music_duck_restore_s=0.3)
    f = play(env, seconds=4.0)
    nxt = play(env, seconds=0.3, name="next.wav")
    line = say(env, "word " * 12)                              # ~4 s over the music
    stale = say(env, "queued before he spoke")
    th = run(env, idle=0.5)
    assert wait_for(lambda: started(env, line["utt"]))
    time.sleep(0.4)
    partial("hang on, wait")
    assert wait_for(lambda: line["utt"] in kind(env, "said"), 2.0)
    time.sleep(0.3)
    turn("hang on, wait")
    th.join(15)
    said = kind(env, "said")
    cut = said[line["utt"]]
    assert (cut["reason"], cut["cut"], cut["over"]) == ("barge-in", True, f["utt"])
    assert said[stale["utt"]]["reason"] == "barge-in"        # the flush: stale words go
    assert said[nxt["utt"]]["reason"] == "done", "the flush dropped the next song"
    m = said[f["utt"]]
    assert (m["reason"], m["cut"], m["ducks"]) == ("done", False, 0)
    assert m["played_s"] == pytest.approx(4.0, abs=0.06)
    assert {g for _, g in mixers["null"].trace} == {1.0}


# -- the gate: the Ducker asks duckswitch.decide on every weigh (EARS-295 do-003) --

class FakeMix:
    """A mixer that only records the duck gains it was sent."""

    def __init__(self) -> None:
        self.gains: list = []

    def set_duck(self, g: float) -> None:
        self.gains.append(g)


def a_ducker(env, gate, restore_s=0.3, **kw) -> "bed.Ducker":
    """A Ducker on a real (unplayed) Bed, driven by hand: ``_weigh(causes, now)``."""
    play(env, seconds=0.5, **kw)
    item = mouth.box.public(mouth.box.Queue(env.d).lines()[0])
    b = bed.Bed(bed.Beds(env.d), item, None, bed.device_key(item["device"]))
    return bed.Ducker(b, FakeMix(), 20, restore_s, None, "null", gate=gate)


@pytest.mark.parametrize("on", [False, True])
@pytest.mark.needs_ffmpeg
def test_t1_the_setting_decides_whether_a_line_over_the_music_ducks_it(env, mixers, on):
    """SC1: setting off, the line mixes over the music and it stays at 1.0 (one
    duck-skipped row, by: settings); setting on, it ducks (by: settings)."""
    settings(env, music_duck=on, music_duck_restore_s=0.2)
    f = play(env, seconds=3.0)
    line = say(env, "a line over the music")
    run(env, idle=0.5).join(12)
    said = kind(env, "said")
    assert said[line["utt"]]["over"] == f["utt"] and said[line["utt"]]["reason"] == "done"
    m = said[f["utt"]]
    g = {x for _, x in mixers["null"].trace}
    if on:
        assert [(e["why"], e["by"]) for e in events(env, "duck")] == [("say", "settings")]
        assert events(env, "unduck")[0]["by"] == "settings"
        assert (m["ducks"], m["ducks_skipped"]) == (1, 0) and min(g) == pytest.approx(0.2)
        assert not events(env, "duck-skipped")
    else:
        sk = events(env, "duck-skipped")
        assert [(e["why"], e["by"], e["lane"], e["utt"], e["device"]) for e in sk] == \
            [("say", "settings", "bed", f["utt"], "null")]
        assert (m["ducks"], m["ducks_skipped"]) == (0, 1) and g == {1.0}
        assert not events(env, "duck") and not events(env, "unduck")


@pytest.mark.needs_ffmpeg
def test_t5_an_override_set_mid_bed_lifts_the_dip_and_it_returns_when_it_expires(env, mixers, monkeypatch):
    """SC5: the same Bed and mixer lane, no restart. The override lifts the dip
    within one poll; once the fake clock passes ``until`` it dips again."""
    from mouth import duckswitch
    clock = [time.time()]
    monkeypatch.setattr(duckswitch, "clock", lambda: clock[0])
    settings(env, music_duck_restore_s=0.3)
    f = play(env, seconds=6.0)
    line = say(env, "word " * 14)                                 # ~4.5 s: the say cause holds
    th = run(env, idle=0.5)
    assert wait_for(lambda: events(env, "duck"), 3.0)
    first = bed.read(env.d)[0]
    assert first["duck_by"] == "builtin" and first["ducked"]
    # the trace waits on the mixer, not the rows: under load the pacing thread can
    # stall, so the ramp down (or back up) may not be drawn yet when the row is
    # written; without these waits the trace showed one low run (3 in 23 at load 14-26)
    low = lambda: mixers["null"].trace and mixers["null"].trace[-1][1] < 0.21
    up = lambda: mixers["null"].trace and mixers["null"].trace[-1][1] > 0.5
    assert wait_for(low, 1.0)
    duckswitch.set(env.d, on=False, for_s=600)
    t0 = time.monotonic()
    assert wait_for(lambda: events(env, "unduck"), 1.0)
    assert time.monotonic() - t0 < 0.35                           # one poll (100 ms), and slack
    assert wait_for(lambda: bed.read(env.d)[0].get("duck_by") == "override", 1.0)
    assert not bed.read(env.d)[0]["ducked"]
    assert wait_for(up, 1.0)
    clock[0] += 601                                               # the override has expired
    assert wait_for(lambda: len(events(env, "duck")) == 2, 1.0)
    th.join(15)
    said = kind(env, "said")
    d, u = events(env, "duck"), events(env, "unduck")
    assert [x["by"] for x in d] == ["builtin", "builtin"]
    assert [x["by"] for x in u] == ["override", "builtin"]
    assert not events(env, "duck-skipped")                        # the lifted dip is not a second row
    assert len([r for r in rows(env) if r["kind"] == "saying" and r["utt"] == f["utt"]]) == 1
    m = said[f["utt"]]
    assert (m["reason"], m["cut"], m["ducks"], m["ducks_skipped"]) == ("done", False, 2, 0)
    assert m["played_s"] == pytest.approx(6.0, abs=0.06)          # one bed, never restarted
    assert said[line["utt"]]["reason"] == "done"
    g = [x for _, x in mixers["null"].trace]                      # ONE mixer: down, up, down, up
    lows = [i for i, x in enumerate(g) if x < 0.21]
    runs = 1 + sum(1 for a, b in zip(lows, lows[1:]) if b != a + 1) if lows else 0
    assert runs == 2 and g[-1] == pytest.approx(1.0)


def test_t6_one_skipped_row_per_would_be_dip_and_the_latch_never_blocks_a_dip(env):
    """SC6: a cause flapping inside ``restore_s`` is one would-be dip, one row; a
    quiet gap re-arms it. Gate turning yes with a cause live dips at once."""
    gate = [(False, "override")]
    dk = a_ducker(env, lambda: gate[0])
    for t, c in [(0.0, ["barge"]), (0.1, []), (0.25, ["barge"]), (0.4, []), (0.5, ["say"])]:
        dk._weigh(c, t)
    assert dk.skipped == 1 and dk.skipping
    dk._weigh([], 0.81)                                           # quiet for restore_s: re-armed
    assert not dk.skipping
    dk._weigh(["say"], 1.0)                                       # a second would-be dip
    assert dk.skipped == 2
    gate[0] = (True, "settings")                                  # mouth duck reset, cause live
    assert dk._weigh(["say"], 1.1) is True
    assert dk.down and dk.mixer.gains == [0.2] and not dk.skipping
    gate[0] = (False, "override")                                 # mouth duck off, mid-dip
    assert dk._weigh(["say"], 1.2) is True
    assert not dk.down and dk.mixer.gains == [0.2, 1.0]
    assert dk.skipped == 2                                        # the lifted dip: no second row
    sk = events(env, "duck-skipped")
    assert [(e["why"], e["by"], e["lane"]) for e in sk] == [("barge", "override", "bed"), ("say", "override", "bed")]
    assert sk[0]["causes"] == ["barge"]
    assert [(e["event"], e["by"]) for e in events(env, "duck") + events(env, "unduck")] == \
        [("duck", "settings"), ("unduck", "override")]


def test_t6_a_gate_that_cannot_answer_leaves_the_stamped_duck_in_charge(env):
    def broken():
        raise OSError("duck.json unreadable")
    dk = a_ducker(env, broken)
    assert dk._weigh(["say"], 0.0) is True and dk.down              # the item's duck: on
    assert dk.by == "builtin"


@pytest.mark.parametrize("flag", [True, False])
def test_t6_a_gate_that_cannot_answer_names_a_per_call_flag_argument(env, flag):
    """The fallback speaks decide()'s words: a ``--duck``/``--no-duck`` item is
    ``by: argument`` (design.md s6), never the stamp's raw ``flag``."""
    def broken():
        raise OSError("duck.json unreadable")
    dk = a_ducker(env, broken, duck=flag)
    dk._weigh(["say"], 0.0)
    assert (dk.down, dk.by) == (flag, "argument")


@pytest.mark.parametrize("flag,override", [(True, False), (False, True)])
@pytest.mark.needs_ffmpeg
def test_t2_the_per_call_argument_beats_the_override_end_to_end(env, mixers, flag, override):
    """SC2: ``--duck`` still dips under ``mouth duck off``; ``--no-duck`` stays up
    under ``mouth duck on`` (with the setting off, so only the override says dip)."""
    from mouth import duckswitch
    settings(env, music_duck=not override, music_duck_restore_s=0.2)
    duckswitch.set(env.d, on=override, for_s=600)
    f = play(env, seconds=2.5, duck=flag)
    say(env, "a line over the music")
    run(env, idle=0.5).join(12)
    m = kind(env, "said")[f["utt"]]
    g = {x for _, x in mixers["null"].trace}
    if flag:
        assert [x["by"] for x in events(env, "duck")] == ["argument"] and m["ducks"] == 1
        assert min(g) == pytest.approx(0.2)
    else:
        assert [(x["by"], x["why"]) for x in events(env, "duck-skipped")] == [("argument", "say")]
        assert (m["ducks"], m["ducks_skipped"]) == (0, 1) and g == {1.0}


@pytest.mark.needs_ffmpeg
def test_t4_with_ducking_off_by_override_his_singing_neither_dips_nor_cuts_the_music(env, mixers, monkeypatch):
    """SC4, the override half: the setting is on, ``mouth duck off`` is live."""
    from mouth import duckswitch
    monkeypatch.setenv("MOUTH_BARGE_DEVICES", "null")
    settings(env, music_duck=True, music_duck_restore_s=0.3)
    duckswitch.set(env.d, on=False, for_s=600)
    f = play(env, seconds=3.0)
    nxt = play(env, seconds=0.3, name="next.wav")
    th = run(env, idle=0.5)
    assert wait_for(lambda: started(env, f["utt"]))
    time.sleep(0.3)
    partial("la la la, hang on, wait")
    assert wait_for(lambda: events(env, "duck-skipped"), 2.0)
    time.sleep(0.4)
    assert not (env.d / "stop").exists()
    turn("la la la, hang on, wait")
    th.join(12)
    said = kind(env, "said")
    m = said[f["utt"]]
    assert (m["reason"], m["cut"], m["ducks"], m["ducks_skipped"]) == ("done", False, 0, 1)
    assert m["played_s"] == pytest.approx(3.0, abs=0.06)
    assert said[nxt["utt"]]["reason"] == "done"
    assert [(x["why"], x["by"]) for x in events(env, "duck-skipped")] == [("barge", "override")]
    assert not events(env, "duck") and {g for _, g in mixers["null"].trace} == {1.0}


# -- volume-bed-times-duck -------------------------------------------------------------

@pytest.mark.needs_ffmpeg
def test_a_bed_at_50_ducks_to_10_and_comes_back_to_50(env, mixers):
    settings(env, music_duck_restore_s=0.2)
    f = play(env, seconds=3.0, device="dev-a", volume=50)
    line = say(env, "a short line", device="dev-a")
    run(env, idle=0.5).join(12)
    said = kind(env, "said")
    assert said[line["utt"]]["over"] == f["utt"] and said[f["utt"]]["ducks"] == 1
    g = [x for _, x in mixers["dev-a"].trace]
    # no g[0]: a line whose first frame beats the bed's first block ducks it before
    # the trace starts (the bed is ticked at once), so the trace may open mid-ramp
    assert min(g) == pytest.approx(0.10) and max(g) == pytest.approx(0.5)
    assert g[-1] == pytest.approx(0.5), "it came back to 1.0, not to its own level"


# -- review 1546 F1: never two outputs beside a closing mixer ----------------------------

def test_a_line_whose_mixer_closes_under_it_waits_for_the_close(monkeypatch):
    """The bed drains between ``mixer_for()`` and ``speech_lane()``: the line's
    fallback opens its own output only once the mixer's is closed (on a call,
    never two line connections at once)."""
    rig = Rig(monkeypatch, out_cls=SlowDrain)
    bed._MIX["dev-x"] = rig.mix
    real = output.Mixer.speech_lane
    try:
        lane = rig.mix.bed_lane()
        lane.write(np.zeros(BLOCK, dtype=np.float32))

        def preempted(self, *a, **kw):
            # the player thread loses the GIL here, and the bed's song ends
            threading.Thread(target=lane.drain, daemon=True).start()
            assert wait_for(lambda: self.closing, 2.0)
            return real(self, *a, **kw)
        monkeypatch.setattr(output.Mixer, "speech_lane", preempted)
        out = output.open_output("dev-x", RATE)
        assert type(out) is not output.MixLane
        assert rig.mix.wait_closed(0), "the line opened its own output while the mixer's was open"
        assert len(rig.outs) == 2
    finally:
        bed._MIX.pop("dev-x", None)


def test_the_player_lets_the_say_cause_go_only_when_nothing_is_playable(env):
    b = bed.Beds(env.d)
    b.line_started()
    assert b.saying
    b.lines_done()
    assert not b.saying
