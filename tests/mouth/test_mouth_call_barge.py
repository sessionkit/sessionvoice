"""EARS-315 do-001: one barge policy, on calls too.

On a call, any sound DUCKS the line and two
confirmed content words STOP it, as off a call. That supersedes the earlier one word
on calls, which stood in for a call duck that did not
exist: a call line is paced into the bot's socket and has no device callback.
The duck is now a ``LineDuck`` applied in ``CallOut.write`` (design.md s3.1).

A fake bot (test_mouth_call.FakeBot) records the PCM the mouth sends; the
call ears' ``vad`` and ``partial`` rows are faked into a scratch heard log
(source ``call``). No sound, no live ears, no live bot, no live mouth.
"""
from __future__ import annotations

import json
import time

import numpy as np
import pytest

import mouth
from mouth import backends, hold, onset, output, player

from test_mouth import lines, run_player, say  # noqa: F401
from test_mouth_call import bots  # noqa: F401  (fixture)
from test_mouth_inbox import box  # noqa: F401  (fixture)
from test_mouth_onset import _printed, _wait, ears  # noqa: F401  (fixture)
from tests import env_fence

RATE = 24000
BLOCK = int(RATE * 0.05)
CALL = "call:5531"
LEVEL = 0.5          # the tone's level: the sent PCM's peak is LEVEL * 32767 unducked


class Tone:
    """A steady tone of ``seconds``, in 50 ms blocks, so a dip shows in the PCM."""
    name, sample_rate = "tone", RATE

    def __init__(self, seconds: float) -> None:
        self.n = int(round(seconds / 0.05))

    def stream(self, text, voice, speed):
        for _ in range(self.n):
            yield np.full(BLOCK, LEVEL, dtype=np.float32)


@pytest.fixture
def on_call(box, ears, bots, monkeypatch):  # noqa: F811
    """A live fake call 5531, a line of ``tone(seconds)``, and ``.ducks``: the
    BargeDuck each line started."""
    bot = bots([5531])
    monkeypatch.setenv("MOUTH_RESUME_S", "0")   # a cut line's rest is not queued again
    env_fence.delenv(monkeypatch, "MOUTH_BARGE_WORDS_CALL")
    ducks = []
    real = onset.for_line

    def spy(*a, **kw):
        bd = real(*a, **kw)
        ducks.append(bd)
        return bd
    monkeypatch.setattr(onset, "for_line", spy)

    def tone(seconds):
        monkeypatch.setattr(backends, "resolve", lambda b, v: (Tone(seconds), v))
    box.bot, box.ducks, box.ears, box.tone = bot, ducks, ears, tone
    return box


def _peaks(bot_line: dict, ms: int = 50) -> np.ndarray:
    """The sent PCM's peak per ``ms`` window, 0-1 of full scale (48 kHz int16)."""
    pcm = np.concatenate(bot_line["pcm"]).astype(np.float32) / 32767.0 if bot_line["pcm"] else np.zeros(0)
    n = 48000 * ms // 1000
    return np.array([np.abs(pcm[i:i + n]).max() for i in range(0, len(pcm) - n + 1, n)])


def _said(box, item):  # noqa: F811
    return next(r for r in lines(box.logs) if r["kind"] == "said" and r["utt"] == item["utt"])


# -- the gain, in CallOut.write ------------------------------------------------------

def test_a_call_has_a_duck_applied_in_write_on_a_copy(bots):  # noqa: F811
    bot = bots([5531])
    out = output.open_output(CALL, RATE)
    assert isinstance(out.duck, output.LineDuck)
    first = np.full(BLOCK, LEVEL, dtype=np.float32)
    out.write(first)                                        # no duck: today's bytes
    out.duck.set(0.25)
    mine = np.full(BLOCK, LEVEL, dtype=np.float32)
    for _ in range(3):
        out.write(mine)
    assert (mine == LEVEL).all()                            # the caller's block (the synth's) untouched
    out.duck.set(1.0)
    for _ in range(12):                                     # 0.6 s: over the 0.5 s ramp up
        out.write(mine)
    out.drain()
    pcm = np.concatenate(bot.lines[0]["pcm"]).astype(np.float32) / 32767.0
    assert np.abs(pcm[:2000]).max() == pytest.approx(LEVEL, abs=1e-3)       # before the duck
    ducked = pcm[2400 * 2:2400 * 4]                         # the 2nd and 3rd ducked blocks, at 48 kHz
    assert np.abs(ducked).max() == pytest.approx(LEVEL * 0.25, abs=2e-3)    # down in 50 ms, held
    assert np.abs(pcm[-2000:]).max() == pytest.approx(LEVEL, abs=1e-3)      # sprung back
    asked, applied, ahead = out.duck.trace[0]
    assert applied >= asked
    # what was paced out already when it bit: the lead, plus up to one block
    assert 0.0 < ahead <= output.CallOut.latency + 0.05 + 0.02


def test_an_untouched_call_line_sends_the_same_bytes(bots):  # noqa: F811
    """A line nobody barges in on costs one comparison: no copy, no change."""
    bot = bots([5531])
    out = output.open_output(CALL, RATE)
    ramp = np.linspace(-0.9, 0.9, BLOCK * 4, dtype=np.float32)
    for i in range(0, len(ramp), BLOCK):
        out.write(ramp[i:i + BLOCK])
    out.drain()
    sent = np.concatenate(bot.lines[0]["pcm"])
    ref = output.CallOut.__new__(output.CallOut)
    ref.sample_rate, ref._last, ref._pos = RATE, np.zeros(0, dtype=np.float32), 0.0
    want = np.concatenate([ref._resample(ramp[i:i + BLOCK]) for i in range(0, len(ramp), BLOCK)])
    want = (np.clip(want, -1.0, 1.0) * 32767.0).astype("<i2")
    assert np.array_equal(sent, want)
    assert not out.duck.trace


def test_for_line_finds_a_calls_duck_by_out_duck_and_listens_to_the_call(bots, ears):  # noqa: F811
    bots([5531])
    out = output.open_output(CALL, RATE)
    bd = onset.for_line(out, CALL, settings={})
    try:
        assert bd is not None and bd.duck is out.duck
        assert bd.tail.sources == hold.barge_sources_for(CALL) == ("call",)
        assert bd.level == hold.BARGE_DUCK_LEVEL / 100.0
    finally:
        bd.stop()
        out.abort()
    out = output.open_output(CALL, RATE)
    try:
        assert onset.for_line(out, CALL, settings={"barge_duck_level": 100}) is None   # 100: off, as off a call
    finally:
        out.abort()


def test_the_room_mic_never_ducks_a_call_line(bots, ears):  # noqa: F811
    """As it never cuts one: only the call's own voice dips it."""
    bots([5531])
    out = output.open_output(CALL, RATE)
    bd = onset.BargeDuck(out.duck, hold.barge_sources_for(CALL), 0.25, 2.5)
    ears.vad(source="mic")
    bd.step(now=1.0)
    assert not out.duck.ducked and bd.onsets == 0
    ears.vad(source="call")
    bd.step(now=1.1)
    assert out.duck.ducked and bd.onsets == 1
    bd.stop()
    out.abort()


def test_a_call_line_over_music_shares_the_calls_duck(bots):  # noqa: F811
    """A call over a bed is a MixLane, whose duck is the CallOut's: the mixer
    writes each summed block through CallOut.write, so bed and line dip together."""
    bots([5531])
    mix = output.Mixer(CALL, RATE)
    bed_lane = mix.bed_lane()
    line = mix.speech_lane(RATE)
    try:
        assert isinstance(mix.out, output.CallOut)
        assert line.duck is mix.out.duck is bed_lane.duck
    finally:
        line.abort()
        bed_lane.abort()


# -- the player: a sound ducks a call line, and it springs back with no cut ---------

def test_an_onset_on_a_call_dips_the_sent_pcm_and_it_recovers_with_no_cut(on_call, capsys):
    on_call.ears.settings.write_text(json.dumps({"barge_duck_recover_s": 0.3}))
    on_call.tone(2.5)
    item = say("a call line long enough to talk over, for a while", device=CALL)
    th = run_player(on_call.d)
    assert _wait(lambda: on_call.ducks and on_call.ducks[0] is not None)
    bd = on_call.ducks[0]
    assert bd.duck is not None and bd.tail.sources == ("call",)
    time.sleep(0.4)                                         # some line at full level first
    row = on_call.ears.vad(source="call")
    assert _wait(lambda: bd.duck.trace, 1.0)                # the write applied it
    on_call.ears.vad(speech=False, source="call")           # his voice stops (EARS-315 R12)
    assert _wait(lambda: not bd.duck.ducked, 1.5)           # no words: it came back
    th.join(10)
    assert not th.is_alive()
    said = _said(on_call, item)
    assert (said["reason"], said["cut"], said["device"]) == ("done", False, CALL)
    peaks = _peaks(on_call.bot.lines[0])
    full = LEVEL * 0.99
    first_dip = int(np.argmax(peaks < full))
    assert first_dip >= 4 and peaks[:first_dip].min() > full   # full level until the onset
    assert peaks.min() == pytest.approx(LEVEL * 0.25, abs=0.01)   # dipped to the duck level
    assert peaks[-4:].min() > full                          # and back up: the line played on
    assert bd.recovered == 1
    # R5: the call's duck record (kind: duck), as off a call, with ahead_ms
    recs = _printed(capsys.readouterr().out, "duck")
    assert len(recs) == 1
    rec = recs[0]
    assert (rec["utt"], rec["device"], rec["onsets"], rec["recovered"]) == (item["utt"], CALL, 1, 1)
    assert (rec["first_onset_seq"], rec["first_onset_ts"]) == (row["seq"], row["ts"])
    assert isinstance(rec["applied_ms"], int) and rec["applied_ms"] >= 0
    assert isinstance(rec["ahead_ms"], int) and 0 < rec["ahead_ms"] <= 300   # the paced lead
    assert (rec["cut"], rec["reason"]) == (False, "done")


# -- the player: N words on a call, as off it ---------------------------------------

def test_one_content_word_does_not_cut_a_call_line_two_do(on_call):
    on_call.tone(4.0)
    item = say("I can check the train times for you and send the platform when it is posted.",
               device=CALL)
    th = run_player(on_call.d)
    assert _wait(lambda: on_call.ducks)
    time.sleep(0.2)
    on_call.ears.vad(source="call")
    on_call.ears.partial("Tuesday", source="call")
    time.sleep(0.6)                                         # several polls of the barge test
    assert th.is_alive() and not [r for r in lines(on_call.logs) if r["kind"] == "said"]
    assert on_call.ducks[0].duck.ducked                     # one word: ducked, not cut
    on_call.ears.partial("Tuesday evening", source="call")
    th.join(10)
    assert not th.is_alive()
    said = _said(on_call, item)
    assert (said["reason"], said["cut"]) == ("barge-in", True)
    assert said["played_s"] < 3.0
    assert on_call.bot.lines[0]["ended"] == "cut"


@pytest.mark.parametrize("ask", ["Hang on.", "actually, wait", "stop"])
def test_a_stop_ask_cuts_a_call_line_at_once(on_call, ask):
    on_call.tone(4.0)
    item = say("I can check the train times for you and send the platform when it is posted.",
               device=CALL)
    th = run_player(on_call.d)
    assert _wait(lambda: on_call.ducks)
    time.sleep(0.2)
    on_call.ears.partial(ask, source="call")
    th.join(10)
    assert not th.is_alive()
    said = _said(on_call, item)
    assert (said["reason"], said["cut"]) == ("barge-in", True) and said["played_s"] < 2.0


def test_phantom_partials_never_cut_a_call_line(on_call):
    """R3, the s1.2 table: the phantoms on call:74702. "." has no word,
    "MM." "Yes," and "oh yeah" are filler, and "Shell." is one word however
    often it comes (the count is a set). At N=2 none of them cuts."""
    on_call.tone(2.0)
    item = say("I can check the train times for you and send the platform when it is posted.",
               device=CALL)
    th = run_player(on_call.d)
    assert _wait(lambda: on_call.ducks)
    on_call.ears.vad(source="call")
    for text in (".", "MM.", "Shell.", "Yes,", "Shell.", "oh yeah", "Shell.", "."):
        on_call.ears.partial(text, source="call")
        time.sleep(0.1)
    th.join(10)
    assert not th.is_alive()
    said = _said(on_call, item)
    assert (said["reason"], said["cut"]) == ("done", False)


LINE = "I can check the train times for you and send the platform when it is posted."


@pytest.mark.parametrize("partials,cuts", [
    (["."], False),
    (["MM."], False),
    (["Shell."], False),
    (["Yes,"], False),
    (["oh yeah"], False),
    (["Shell.", "Shell.", "Shell."], False),
    (["Shell.", "MM.", "Yes,", "."], False),
    (["Shell. Shell."], False),
    (["the platform"], False),                           # the line's own words
    (["Shell.", "Hello."], True),                        # two different phantom words still cut (s3.7)
    (["Tuesday", "evening"], True),
    (["Hang on."], True),
    (["actually, wait"], True),
])
def test_the_s1_2_table_on_a_call_at_the_common_n(ears, monkeypatch, partials, cuts):
    """design.md s1.2, the N=2 column, through the real ``barge()`` with the
    call's own sources and the call's N from the common resolver."""
    env_fence.delenv(monkeypatch, "MOUTH_BARGE_WORDS_CALL")
    n = hold.barge_words("call:74702")
    assert n == 2
    t0 = time.time() - 1
    for p in partials:
        ears.partial(p, source="call")
    got = hold.barge(t0, LINE, sources=hold.barge_sources_for("call:74702"), words=n)
    assert (got is not None) is cuts


# -- the retired variable: not honoured, warned once, listed by status -------------

def test_the_call_variable_is_not_honoured(monkeypatch):
    env_fence.delenv(monkeypatch, "MOUTH_BARGE_WORDS")
    monkeypatch.setenv("MOUTH_SETTINGS", "/nonexistent/mouth.json")
    monkeypatch.setenv("MOUTH_BARGE_WORDS_CALL", "1")
    assert hold.barge_words("call:74702") == hold.barge_words("airpods") == 2
    assert hold.barge_words("call:74702", {"barge_words": 3}) == 3     # the user's N reaches a call


def test_the_call_variable_is_ignored_without_a_warning(monkeypatch):
    assert hold.barge_warnings({}) == []
    assert hold.barge_warnings({"MOUTH_BARGE_WORDS_CALL": "1", "MOUTH_BARGE_WORDS": "3"}) == []


def test_a_stale_barge_words_warns_with_a_neutral_detail():
    w = hold.barge_warnings({"MOUTH_BARGE_WORDS_CALL": "1", "MOUTH_BARGE_WORDS": "two"})
    assert len(w) == 1
    assert {k: w[0][k] for k in ("kind", "var", "value")} == \
        {"kind": "warn", "var": "MOUTH_BARGE_WORDS", "value": "two"}
    assert w[0]["detail"] == "not a whole number of 1 or more: ignored"
    json.dumps(w)                                           # a player.log line


def test_the_player_warns_once_and_status_lists_it(box, monkeypatch, capsys):  # noqa: F811
    monkeypatch.setenv("MOUTH_BARGE_WORDS_CALL", "1")       # ignored: never warned about
    monkeypatch.setenv("MOUTH_BARGE_WORDS", "two")
    monkeypatch.setattr(player, "_warned", False)
    say("one")
    run_player(box.d).join(5)
    say("two")
    run_player(box.d).join(5)                               # a second start in the same process
    warns = _printed(capsys.readouterr().out, "warn")
    assert [w["var"] for w in warns] == ["MOUTH_BARGE_WORDS"]
    st = mouth.status(box.d)
    assert [w["var"] for w in st["barge"]["warnings"]] == ["MOUTH_BARGE_WORDS"]
    env_fence.delenv(monkeypatch, "MOUTH_BARGE_WORDS")
    assert mouth.status(box.d)["barge"]["warnings"] == []
