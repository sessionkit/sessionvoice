"""EARS-315 do-005: the duck follows his voice (R12, R11; design.md s3.5 as
amended by s9a).

Down at his voice onset and down while it is on; ``barge_duck_recover_s``
after the ears' OFFSET row it springs back and the line plays on. The
recovery's built-in goes by the ears' ``partials`` mode: 1.0 s on streaming
(nemotron), 2.5 s on chunked (final, the code default). With no offset for
``barge_duck_max_s`` (4 s) the line comes back anyway, ``capped: true``, with
the words counted toward a cut at that moment. A line that starts while he
is mid-voice starts ducked, and its cap counts from its first frame.

Rows are faked into a scratch heard log (`test_mouth_onset.ears`); the clock
is injected where the timing is the point. No sound, no live ears or mouth.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from ears import heard
from mouth import hold, onset, output

from test_mouth import say  # noqa: F401
from test_mouth_call import bots  # noqa: F401  (fixture)
from test_mouth_call_barge import CALL, LEVEL, _peaks, _said, on_call  # noqa: F401  (fixture)
from test_mouth_inbox import box, lines, run_player  # noqa: F401  (fixture)
from test_mouth_onset import RATE, _printed, _wait, ears  # noqa: F401  (fixture)


def _row(speech: bool, age_s: float = 0.0, source: str = "mic") -> dict:
    """A vad edge row stamped ``age_s`` seconds ago (wall), as the ears write it."""
    ts = (datetime.now().astimezone() - timedelta(seconds=age_s)).isoformat(timespec="milliseconds")
    rec = {"ts": ts, "seq": 7, "kind": heard.EVENT, "event": heard.EV_VAD, "speech": speech, "source": source}
    with open(heard.log_path(), "a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


@pytest.fixture
def partials(tmp_path, monkeypatch):
    """partials("nemotron"): a temp ears.json naming the mode, as a host's would."""
    def set_mode(mode):
        conf = tmp_path / "ears.json"
        conf.write_text(json.dumps({"partials": mode}))
        monkeypatch.setenv("EARS_CONF", str(conf))
    return set_mode


# -- the tail: both edges ----------------------------------------------------------

def test_the_tail_returns_both_edges_in_order_and_poll_keeps_onsets(ears):
    tail = onset.OnsetTail(hold.MIC)
    on, off = ears.vad(), ears.vad(speech=False)
    ears.vad(source="call")                                 # another source's ears
    with open(heard.log_path(), "a") as f:                  # speech that is not a bool: not an edge
        f.write(json.dumps({"kind": heard.EVENT, "event": heard.EV_VAD, "speech": 1, "source": "mic"}) + "\n")
    got = tail.edges()
    assert [(r["seq"], r["speech"]) for r in got] == [(on["seq"], True), (off["seq"], False)]
    ears.vad(speech=False)
    on2 = ears.vad()
    assert [r["seq"] for r in tail.poll()] == [on2["seq"]]  # onsets only, as before


# -- stays down while the voice is on; up after the offset ---------------------------

def test_the_duck_stays_down_while_the_voice_is_on(ears):
    """Unbroken voice of 3.9 s: no flat recovery from the onset any more."""
    d = output.LineDuck(RATE)
    bd = onset.BargeDuck(d, hold.MIC, 0.25, 1.0)
    ears.vad()
    bd.step(now=0.0)
    for t in (1.0, 2.0, 2.5, 3.0, 3.9):
        bd.step(now=t)
        assert d.ducked and d.target == 0.25, t
    ears.vad(speech=False)
    bd.step(now=3.95)
    bd.step(now=4.9)
    assert d.ducked                                         # 0.95 s after the offset
    bd.step(now=4.95)
    assert not d.ducked and (bd.recovered, bd.capped) == (1, False)


def test_an_offset_with_no_voice_on_does_nothing(ears):
    """An offset of a voice that began before the line (and was not taken as
    mid-voice) never dips or springs it; it is counted."""
    d = output.LineDuck(RATE)
    bd = onset.BargeDuck(d, hold.MIC, 0.25, 1.0)
    ears.vad(speech=False)
    bd.step(now=0.0)
    bd.step(now=5.0)
    assert not d.ducked and (bd.offsets, bd.recovered) == (1, 0)


def test_an_onset_and_its_offset_in_one_look_recover_after_the_offset(ears):
    d = output.LineDuck(RATE)
    bd = onset.BargeDuck(d, hold.MIC, 0.25, 1.0)
    ears.vad()
    ears.vad(speech=False)                                  # a blip, both rows before one poll
    bd.step(now=0.0)
    assert d.ducked and not bd.speaking
    bd.step(now=0.99)
    assert d.ducked
    bd.step(now=1.0)
    assert not d.ducked and bd.recovered == 1


def test_recover_zero_springs_back_at_the_offset(ears):
    d = output.LineDuck(RATE)
    bd = onset.BargeDuck(d, hold.MIC, 0.25, 0.0)
    ears.vad()
    bd.step(now=0.0)
    ears.vad(speech=False)
    bd.step(now=0.5)
    assert not d.ducked and bd.recovered == 1


@pytest.mark.parametrize("mode,want", [(None, 2.5), ("final", 2.5), ("nemotron", 1.0), ("whisper", 2.5)])
def test_the_recovery_default_follows_the_partials_mode(ears, partials, mode, want, monkeypatch):
    """s9a (Q9): 1.0 s after the offset with streaming partials (nemotron),
    2.5 s with chunked (final, the code default; a mode it does not know is
    final, as the ears read it).

    The test steps the duck by hand on its own clock (0.0, 0.2, ...), so the
    duck ``for_line`` hands back must not also be started: its poll thread
    steps the same duck every ``POLL_S`` on ``time.monotonic``, hours past
    0.0, and one such step taking the offset (or landing after the onset)
    recovered the line early or never (EARS-453 do-004)."""
    monkeypatch.setattr(onset.BargeDuck, "start", lambda self: self)
    if mode is not None:
        partials(mode)
    pol = hold.barge_policy()
    assert (pol["recover_s"], pol["recover_from"], pol["recover_by"]) == (want, "default", "builtin")
    assert pol["partials"] == ("final" if mode in (None, "whisper") else mode)
    assert hold.barge_duck_recover_s({}) == want == hold.barge_duck_recover_default(pol["partials"])
    d = output.LineDuck(RATE)

    class Out:
        duck = d
    bd = onset.for_line(Out(), "airpods", settings={})
    try:
        assert bd.recover_s == want and bd._thread is None    # stepped by this test alone
        ears.vad()
        bd.step(now=0.0)
        ears.vad(speech=False)
        bd.step(now=0.2)
        bd.step(now=0.2 + want - 0.01)
        assert d.ducked
        bd.step(now=0.2 + want)
        assert not d.ducked
    finally:
        bd.stop()


def test_the_users_recover_beats_either_mode(ears, partials):
    partials("nemotron")
    pol = hold.barge_policy(settings={"barge_duck_recover_s": 1.7})
    assert (pol["recover_s"], pol["recover_from"], pol["recover_by"]) == (1.7, "user", "setting")


def test_an_unreadable_ears_file_is_final(ears, tmp_path, monkeypatch):
    bad = tmp_path / "ears.json"
    bad.write_text("{not json")
    monkeypatch.setenv("EARS_CONF", str(bad))
    assert hold.ears_partials() == "final" and hold.barge_duck_recover_s({}) == 2.5


def test_the_schema_names_both_recovery_values():
    """The Settings app's text says the delay counts from the offset and names
    the default for each partials mode; its ``default`` is the code default's
    (final), which the schema pins in test_ears_settings_schema.py hold."""
    schema = json.loads((Path(__file__).resolve().parents[2] / "settings" / "mouth.schema.json").read_text())
    p = schema["properties"]["barge_duck_recover_s"]
    text = p["description"]
    assert "1.0" in text and "nemotron" in text and "2.5" in text and "final" in text
    assert "stop talking" in text
    assert p["default"] == hold.barge_duck_recover_default("final") == 2.5
    assert hold.barge_duck_recover_default("nemotron") == 1.0
    assert schema["properties"]["barge_duck_max_s"]["default"] == hold.BARGE_DUCK_MAX_S


# -- the cap: a lost offset ----------------------------------------------------------

def test_a_lost_offset_recovers_at_the_cap_with_capped_and_the_counted_words(ears):
    counted = [["tuesday"]]
    d = output.LineDuck(RATE)
    bd = onset.BargeDuck(d, hold.MIC, 0.25, 1.0, words=lambda: counted[0])
    assert bd.max_s == hold.BARGE_DUCK_MAX_S == 4.0
    ears.vad()
    bd.step(now=10.0)
    bd.step(now=13.99)
    assert d.ducked and not bd.capped
    bd.step(now=14.0)                                       # 4 s on, no offset: taken as lost
    assert not d.ducked and bd.capped and bd.capped_words == ["tuesday"] and bd.recovered == 1
    counted[0] = ["tuesday", "evening"]                     # later words never rewrite the moment
    ears.vad(speech=False)                                  # the late offset: nothing to recover
    bd.step(now=14.5)
    bd.step(now=20.0)
    assert bd.recovered == 1 and bd.capped_words == ["tuesday"]
    bd.stop()
    rec = bd.record(utt="u", device="d", cut=False, reason="done")
    assert (rec["max_s"], rec["onsets"], rec["offsets"], rec["recovered"], rec["capped"],
            rec["capped_words"]) == (4.0, 1, 1, 1, True, ["tuesday"])
    assert list(rec)[-2:] == ["cut", "reason"]


def test_a_capped_duck_with_no_words_records_an_empty_list(ears):
    """Noise or filler: the Q11 count can tell it from talk."""
    d = output.LineDuck(RATE)
    bd = onset.BargeDuck(d, hold.MIC, 0.25, 1.0, max_s=2.0)
    ears.vad()
    bd.step(now=0.0)
    bd.step(now=2.0)
    assert bd.capped and bd.capped_words == []
    assert bd.record()["capped_words"] == []


def test_each_onset_restarts_the_cap(ears):
    d = output.LineDuck(RATE)
    bd = onset.BargeDuck(d, hold.MIC, 0.25, 1.0)
    ears.vad()
    bd.step(now=0.0)
    ears.vad()                                              # no offset between: still a new onset
    bd.step(now=3.0)
    bd.step(now=6.9)
    assert d.ducked and not bd.capped
    bd.step(now=7.0)
    assert not d.ducked and bd.capped


def test_the_cap_comes_from_the_policy(ears, monkeypatch):
    """The cap reaches the duck through the line's policy, the user's setting
    or the model's override (do-003's --duck-max), read once a line."""
    d = output.LineDuck(RATE)

    class Out:
        duck = d
    bd = onset.for_line(Out(), "airpods", settings={"barge_duck_max_s": 1.5})
    try:
        assert bd.max_s == 1.5
    finally:
        bd.stop()
    pol = dict(hold.barge_policy(settings={}), duck_max_s=2.5)
    bd = onset.for_line(Out(), "airpods", policy=pol)
    try:
        assert bd.max_s == 2.5
    finally:
        bd.stop()


# -- start ducked when he is mid-voice ------------------------------------------------

def test_a_line_that_starts_mid_voice_starts_ducked_and_caps_from_its_first_frame(ears):
    """The onset is 3.9 s old at the line's first frame; the cap is 4 s from
    the FIRST FRAME (the super), not from the onset."""
    _row(True, age_s=3.9)
    clock = [100.0]
    d = output.LineDuck(RATE)
    bd = onset.BargeDuck(d, hold.MIC, 0.25, 1.0, clock=lambda: clock[0], words=lambda: [])
    assert bd.started_ducked and bd.speaking and d.ducked and d.target == 0.25
    assert bd.onsets == 0 and bd.ducked_wall is None        # no onset seen during the line
    bd.step(now=103.99)
    assert d.ducked
    bd.step(now=104.0)
    assert not d.ducked and bd.capped and bd.capped_words == []
    rec = bd.record(cut=False, reason="done")
    assert (rec["started_ducked"], rec["onsets"], rec["capped"], rec["dip_ms"]) == (True, 0, True, None)


def test_a_line_that_starts_mid_voice_recovers_after_his_offset(ears):
    _row(True, age_s=0.5)
    d = output.LineDuck(RATE)
    bd = onset.BargeDuck(d, hold.MIC, 0.25, 1.0, clock=lambda: 50.0)
    assert bd.started_ducked
    ears.vad(speech=False)
    bd.step(now=50.3)
    bd.step(now=51.3)
    assert not d.ducked and not bd.capped and (bd.offsets, bd.recovered) == (1, 1)


@pytest.mark.parametrize("before", [
    "nothing",
    "an onset then its offset",
    "an onset older than the cap",
    "an onset from another source",
])
def test_a_line_starts_at_full_level_unless_he_is_mid_voice(ears, before):
    if before == "an onset then its offset":
        _row(True, age_s=1.0)
        _row(False, age_s=0.8)
    elif before == "an onset older than the cap":
        _row(True, age_s=4.5)
    elif before == "an onset from another source":
        _row(True, age_s=0.5, source="call")
    d = output.LineDuck(RATE)
    bd = onset.BargeDuck(d, hold.MIC, 0.25, 1.0)
    assert not bd.started_ducked and not d.ducked


def test_start_ducked_looks_back_only_so_far(ears):
    """The look back is the log's last 64 KB, as hold's scans: an onset
    buried under more than that is not found (and the tail never returns it)."""
    _row(True, age_s=0.5)
    with open(heard.log_path(), "a") as f:
        for i in range(800):
            f.write(json.dumps({"kind": "partial", "text": "x" * 80, "seq": i}) + "\n")
    bd = onset.BargeDuck(output.LineDuck(RATE), hold.MIC, 0.25, 1.0)
    assert not bd.started_ducked and bd.tail.edges() == []


# -- the player: springback (R11), start ducked, the cap's words ----------------------

def test_springback_with_no_cut_the_call_line_plays_on(on_call, capsys):  # noqa: F811
    """R11: his voice dips the line, it stops, no words: the line ramps back
    up over LINE_DUCK_UP_S and plays on to its end, uncut."""
    on_call.ears.settings.write_text(json.dumps({"barge_duck_recover_s": 0.2}))
    on_call.tone(2.5)
    item = say("a call line long enough to talk over, for a while", device=CALL)
    th = run_player(on_call.d)
    assert _wait(lambda: on_call.ducks and on_call.ducks[0] is not None)
    bd = on_call.ducks[0]
    time.sleep(0.3)
    on_call.ears.vad(source="call")
    assert _wait(lambda: bd.duck.trace, 1.0)
    time.sleep(0.4)                                         # his voice on: down, and staying down
    assert bd.duck.ducked
    on_call.ears.vad(speech=False, source="call")
    assert _wait(lambda: not bd.duck.ducked, 1.0)
    th.join(10)
    assert not th.is_alive()
    assert (_said(on_call, item)["reason"], _said(on_call, item)["cut"]) == ("done", False)
    peaks = _peaks(on_call.bot.lines[0])
    low = int(np.argmin(peaks))
    assert peaks[low] == pytest.approx(LEVEL * 0.25, abs=0.01)
    rising = peaks[low:]
    assert np.all(np.diff(rising[: int(np.argmax(rising > LEVEL * 0.99))]) >= -1e-3)   # a ramp, not a jump
    assert peaks[-4:].min() > LEVEL * 0.99                  # back to full: it played on
    rec = _printed(capsys.readouterr().out, "duck")[0]
    assert (rec["onsets"], rec["offsets"], rec["recovered"], rec["capped"], rec["recover_s"]) \
        == (1, 1, 1, False, 0.2)


def test_the_player_starts_a_line_ducked_when_he_is_mid_voice(on_call, capsys):  # noqa: F811
    on_call.ears.settings.write_text(json.dumps({"barge_duck_recover_s": 0.2}))
    on_call.tone(2.0)
    on_call.ears.vad(source="call")                         # he is talking as the line is asked
    item = say("a call line asked while he talks", device=CALL)
    th = run_player(on_call.d)
    assert _wait(lambda: on_call.ducks and on_call.ducks[0] is not None)
    bd = on_call.ducks[0]
    assert bd.started_ducked
    time.sleep(0.3)
    on_call.ears.vad(speech=False, source="call")
    th.join(10)
    assert not th.is_alive()
    assert _said(on_call, item)["reason"] == "done"
    peaks = _peaks(on_call.bot.lines[0])
    assert peaks[1] == pytest.approx(LEVEL * 0.25, abs=0.02)   # ducked from (nearly) the first block
    assert peaks[-4:].min() > LEVEL * 0.99                  # and up after his offset
    rec = _printed(capsys.readouterr().out, "duck")[0]
    assert (rec["started_ducked"], rec["offsets"], rec["recovered"], rec["capped"]) == (True, 1, 1, False)


def test_the_player_caps_a_lost_offset_and_records_the_counted_words(on_call, capsys):  # noqa: F811
    """One content word (N=2: no cut) and no offset: the cap brings the line
    back, and the record carries the word counted at that moment."""
    on_call.ears.settings.write_text(json.dumps({"barge_duck_max_s": 0.8}))
    on_call.tone(2.5)
    item = say("I can check the train times for you and send the platform when it is posted.",
               device=CALL)
    th = run_player(on_call.d)
    assert _wait(lambda: on_call.ducks and on_call.ducks[0] is not None)
    bd = on_call.ducks[0]
    assert bd.max_s == 0.8
    time.sleep(0.2)
    on_call.ears.vad(source="call")
    on_call.ears.partial("Tuesday", source="call")
    assert _wait(lambda: bd.capped, 2.0)
    assert not bd.duck.ducked
    th.join(10)
    assert not th.is_alive()
    assert (_said(on_call, item)["reason"], _said(on_call, item)["cut"]) == ("done", False)
    rec = _printed(capsys.readouterr().out, "duck")[0]
    assert (rec["capped"], rec["capped_words"], rec["max_s"], rec["offsets"], rec["cut"]) \
        == (True, ["tuesday"], 0.8, 0, False)
