"""EARS-319: with ducking ON or unset, his voice never CUTS a played music file.

His voice never cuts music, unless he says a command for it to stop.
EARS-295 pinned the
``music_duck`` false half (test_mouth_bed.py, test_mouth_duck.py T4). This file
pins the other half: with ``music_duck`` true, or unset (the built-in on), his
voice DIPS a playing bed and it plays on to its end. A barge-in on a SPOKEN
line still cuts that line; the bed under it plays on. The finding behind each
case, with file:line for every path, is the task's
``findings/f01-duck-on-cut.md``.

Each path his voice has to a playing bed, and the case that pins it:

- P1, the bed's own floor poll (``hold.speaking_over`` -> ``Ducker``): DIP.
  ``test_his_voice_dips_the_bed_and_it_plays_on``.
- P2, a spoken line's word-gated barge-in (a general ``barge-in`` stop with
  flush): cuts the line, and never the bed (``Bed.hit_by``), nor music queued
  behind it (``_handle_stop``). ``test_a_barge_on_a_spoken_line_cuts_the_line_and_the_bed_plays_on``
  and ``test_no_stop_his_voice_writes_ever_hits_a_bed``.
- P3, a spoken line's onset duck (``onset.BargeDuck``) on a device with a
  callback: it scales the WHOLE mix, the bed with it, and at
  ``barge_duck_level`` 0 that dip is silent. It is still a dip: the bed keeps
  its place and comes back at the line's end, cut or done.
  ``test_the_onset_dip_over_a_bed_lets_go_when_his_words_cut_the_line``.

No real audio device is ever opened (a host may have AirPods; EARS-320): ``null``,
``dev-*`` recording outputs, and a fake PortAudio (``PullingSd``) only. The
``no_real_device`` fixture fences ``output._sd`` for every test here.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from itertools import count

import pytest

import mouth
from ears import heard
from mouth import bed, hold, output, player

from test_mouth import PullingSd
from test_mouth_bed import env, kind, play, rows, run, say, started, wait_for  # noqa: F401 - fixture

#: The duck settings ruling 5950 asks about, and the ``by`` each one answers with.
#: ``setting``: ``music_duck: true`` in the mouth's settings file; ``unset``: no
#: layer sets it, so the built-in on (a live run says "by builtin");
#: ``flag``: a per-call ``--duck``, also on.
ON_MODES = {"setting": "settings", "unset": "builtin", "flag": "argument"}

#: What he says over the music. A stop word on its own and inside a phrase
#: ("unless I say a command for it to stop": saying it is not a command, the
#: command is ``mouth stop``), and plain content words that would cut a line.
SAID = ("stop", "hang on, wait", "tell me about the bridge works tomorrow")

_seq = count(5000)


@pytest.fixture(autouse=True)
def no_real_device(monkeypatch):
    """Every PortAudio look in this file is a fake one: nothing reaches a device."""
    sd = PullingSd()
    monkeypatch.setattr(output, "_sd", lambda: sd)
    return sd


def on(env, m: str, **extra) -> dict:
    """Ducking on, the ``m`` way: the settings it writes, and the play() keywords."""
    cfg = {"music_duck_restore_s": 0.3, **extra}
    if m == "setting":
        cfg["music_duck"] = True
    env.settings.write_text(json.dumps(cfg))
    return {"duck": True} if m == "flag" else {}


def heard_row(kind_: str, text: str = "", source: str = "mic") -> None:
    """A row in the ears' own log: a partial or a turn end, his words."""
    t = datetime.now().astimezone().isoformat(timespec="milliseconds")
    with open(heard.log_path(), "a") as f:
        f.write(json.dumps({"ts": t, "seq": next(_seq), "kind": kind_, "text": text,
                            "source": source}) + "\n")
    for c in hold._caches.values():
        c.update(t=0.0, v=None)


def vad() -> None:
    """A voice onset, as the ears write it (what ``onset.BargeDuck`` reads)."""
    heard.append("event", event=heard.EV_VAD, speech=True, at=1.0, source="mic")


def events(env, name: str) -> list:
    return [r for r in rows(env) if r["kind"] == "event" and r.get("event") == name]


@pytest.fixture
def mixers(monkeypatch):
    """The mixer each bed lane opened on, by device name (its gain trace)."""
    seen: dict = {}
    real = output.Mixer.bed_lane

    def spy(self, pan=None, volume=None, meta=None, utt=None):
        seen.setdefault(self.name, self)
        return real(self, pan, volume, meta, utt)
    monkeypatch.setattr(output.Mixer, "bed_lane", spy)
    return seen


def bed_said_whole(said: dict, utt: str, seconds: float) -> None:
    """The music played to its end: never cut, every second of it played."""
    m = said[utt]
    assert (m["reason"], m["cut"]) == ("done", False), f"his voice cut the music: {m}"
    assert m["played_s"] == pytest.approx(seconds, abs=0.06)


# -- P1: his voice over the bed alone: a dip, and it plays on ---------------------

@pytest.mark.parametrize("words", SAID)
@pytest.mark.parametrize("m", ON_MODES)
@pytest.mark.needs_ffmpeg
def test_his_voice_dips_the_bed_and_it_plays_on(env, mixers, monkeypatch, m, words):
    monkeypatch.setenv("MOUTH_BARGE_DEVICES", "null")   # his voice counts here (Q1)
    f = play(env, seconds=2.0, **on(env, m))
    th = run(env, idle=0.5)
    assert wait_for(lambda: started(env, f["utt"]))
    time.sleep(0.3)
    heard_row("partial", words)
    assert wait_for(lambda: events(env, "duck"), 2.0)
    assert wait_for(lambda: [(x["ducked"], x["duck_by"]) for x in bed.read(env.d)]
                    == [(True, ON_MODES[m])], 1.0), bed.read(env.d)   # bed.json, as read live
    assert [x["ducked"] for x in mouth._music(env.d)] == [True]       # and mouth status
    time.sleep(0.3)
    heard_row("turn", words)
    assert wait_for(lambda: events(env, "unduck"), 2.0)
    th.join(12)
    assert not th.is_alive()
    said = kind(env, "said")
    bed_said_whole(said, f["utt"], 2.0)
    dip = events(env, "duck")
    assert len(dip) == 1
    assert (dip[0]["utt"], dip[0]["why"], dip[0]["by"], dip[0]["level"]) == \
        (f["utt"], "barge", ON_MODES[m], 20)
    assert said[f["utt"]]["ducks"] == 1 and said[f["utt"]]["ducked_s"] > 0
    gains = [g for _, g in mixers["null"].trace]
    assert min(gains) == pytest.approx(0.2), "a dip goes to the duck level, never to silence"
    assert gains[-1] == pytest.approx(1.0), "the music came back up"
    assert not (env.d / "stop").exists(), "his voice over the music wrote a stop"


# -- P2: his words cut a SPOKEN line; the bed under it plays on --------------------

@pytest.mark.parametrize("line_on", ["null", "dev-b"])   # over the bed, or on another device
@pytest.mark.parametrize("m", ON_MODES)
@pytest.mark.needs_ffmpeg
def test_a_barge_on_a_spoken_line_cuts_the_line_and_the_bed_plays_on(env, monkeypatch, m, line_on):
    monkeypatch.setenv("MOUTH_BARGE_DEVICES", "null|dev-b")
    kw = on(env, m)
    f = play(env, seconds=3.0, **kw)
    nxt = play(env, seconds=0.3, name="next.wav", **kw)   # music queued behind: never flushed
    line = say(env, "word " * 12, device=line_on)            # ~4 s: his words cut it mid-way
    th = run(env, idle=0.5)
    assert wait_for(lambda: started(env, line["utt"]) and started(env, f["utt"]))
    time.sleep(0.3)
    heard_row("partial", "tell me about the bridge works tomorrow")
    assert wait_for(lambda: line["utt"] in kind(env, "said"), 2.0)
    time.sleep(0.2)
    heard_row("turn", "tell me about the bridge works tomorrow")
    th.join(15)
    assert not th.is_alive()
    said = kind(env, "said")
    cut = said[line["utt"]]
    assert (cut["reason"], cut["cut"]) == ("barge-in", True)   # the spoken line may be cut
    assert cut.get("over") == (f["utt"] if line_on == "null" else None)
    bed_said_whole(said, f["utt"], 3.0)
    assert said[nxt["utt"]]["reason"] == "done", "the barge-in flush dropped the next song"
    assert said[f["utt"]]["ducks"] >= 1                        # it dipped instead
    assert not [r for r in said.values() if r.get("lane") == "file" and r.get("cut")]


def test_no_stop_his_voice_writes_ever_hits_a_bed(env):
    """``Bed.hit_by``, the one test every stop meets (the bed's poll and
    ``_handle_stop``): the general ``barge-in`` stop his words write misses a
    bed, whenever it was written and whatever it flushes; an aimed stop or a
    general ``mouth stop`` still cuts it (a command, never his voice)."""
    item = {"utt": "song1", "requested_t": 100.0, "device": "null", "text": "[sound x]"}
    b = bed.Bed(bed.Beds(env.d), item, None, "null")
    for t in (99.0, 100.0, 200.0):
        for flush in (True, False):
            assert not b.hit_by({"t": t, "reason": "barge-in", "flush": flush}), (t, flush)
    assert not b.hit_by({"t": 200.0, "reason": "barge-in", "utt": "line9"})   # another's
    assert not b.hit_by(None) and not b.hit_by({})
    assert b.hit_by({"t": 200.0, "reason": "stop", "flush": True})       # mouth stop
    assert b.hit_by({"t": 200.0, "flush": True})                         # its reason defaults to stop
    assert not b.hit_by({"t": 99.0, "reason": "stop"})                   # a stop before it was asked
    assert b.hit_by({"t": 200.0, "reason": "retracted", "utt": "song1"})   # mouth retract
    b.on_stop({"t": 200.0, "reason": "barge-in", "flush": True})
    assert b._why is None, "on_stop let a barge-in cut the bed"


# -- P3: the onset dip of a line over the bed, at its deepest ----------------------

@pytest.mark.parametrize("m", ("setting", "unset"))
@pytest.mark.needs_ffmpeg
def test_the_onset_dip_over_a_bed_lets_go_when_his_words_cut_the_line(env, monkeypatch, m):
    """On a device with a callback the onset duck scales the whole mix, so the
    bed dips with the line. ``barge_duck_level`` 0 makes that dip silent, but
    the bed keeps its place: the line's cut lets the duck go and the music
    plays to its end. A fake PortAudio pulls in real time; no device opens."""
    dev = "MacBook Pro Speakers"
    monkeypatch.setenv("MOUTH_BARGE_DEVICES", "speakers")   # as if headphones
    kw = on(env, m, barge_duck_level=0, barge_duck_recover_s=30)
    ducks: list = []
    real = player._onset.for_line

    def spy(*a, **k):
        bd = real(*a, **k)
        ducks.append(bd)
        return bd
    monkeypatch.setattr(player._onset, "for_line", spy)
    f = play(env, seconds=3.0, device=dev, **kw)
    line = say(env, "word " * 12, device=dev)
    th = run(env, idle=0.5)
    assert wait_for(lambda: ducks and ducks[0] is not None, 3.0)
    duck = ducks[0].duck
    vad()
    assert wait_for(lambda: duck.target == 0.0, 1.0)            # the whole mix, the bed too
    heard_row("partial", "tell me about the bridge works tomorrow")
    assert wait_for(lambda: line["utt"] in kind(env, "said"), 3.0)
    assert duck.target == 1.0, "the line's cut left the device ducked"
    th.join(15)
    assert not th.is_alive()
    said = kind(env, "said")
    assert (said[line["utt"]]["reason"], said[line["utt"]]["over"]) == ("barge-in", f["utt"])
    bed_said_whole(said, f["utt"], 3.0)
