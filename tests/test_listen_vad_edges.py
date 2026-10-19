"""EARS-238: capture writes a ``vad`` event at each voice onset and offset,
so the mouth can duck a line before a word of his is decoded.

Synthetic audio and the energy classifier, so what counts as speech is exact.
"""
from __future__ import annotations

import pytest

from ears import heard
from ears.listen import FRAME_S, capture
from ears.listen.loop import _Edges

from listen_helpers import CountingSource, FakeSTT, RecordingSink, energy_detector, read_lines, synth


def vads(directory):
    return [r for r in read_lines(directory) if r["kind"] == "event" and r["event"] == heard.EV_VAD]


def test_each_burst_of_voice_is_an_onset_even_inside_one_turn(tmp_path):
    """A turn stays open for 2 s of quiet; an onset needs only a short one.
    Three bursts, 0.5 s apart, make ONE turn and THREE onsets (he barges in a
    beat after his last turn, while the detector still holds it open)."""
    audio = synth([("silence", 1.0), ("speech", 0.6), ("silence", 0.5), ("speech", 0.6),
                   ("silence", 0.5), ("speech", 0.6), ("silence", 3.0)])
    src = CountingSource(audio)
    sink = RecordingSink(tmp_path, src)
    capture(src, FakeSTT(), sink, heartbeat=0, detector=energy_detector(), stt_workers=0)
    got = vads(tmp_path)
    assert [r["speech"] for r in got] == [True, False, True, False, True, False]
    assert [r["at"] for r in got if r["speech"]] == pytest.approx([1.0, 2.1, 3.2], abs=FRAME_S)
    assert [r["at"] for r in got if not r["speech"]] == pytest.approx([1.6, 2.7, 3.8], abs=FRAME_S)
    assert all(r["source"] == "file" and r["by"] == "vad-silence" for r in got)
    assert len([r for r in read_lines(tmp_path) if r["kind"] == "turn"]) == 1


def test_the_onset_is_written_about_60_ms_into_his_voice_and_before_his_words(tmp_path):
    audio = synth([("silence", 1.0), ("speech", 1.0), ("silence", 3.0)])
    src = CountingSource(audio)
    sink = RecordingSink(tmp_path, src)
    capture(src, FakeSTT(), sink, heartbeat=0, detector=energy_detector(), stt_workers=0)
    lines = read_lines(tmp_path)
    onset = next(r for r in lines if r.get("event") == heard.EV_VAD and r["speech"])
    first_partial = next(r for r in lines if r["kind"] == "partial")
    written_at = sink.at_frames[onset["seq"]] * FRAME_S       # stream seconds when written
    assert written_at - onset["at"] == pytest.approx(0.06, abs=0.001)    # 2 frames of 30 ms
    assert onset["seq"] < first_partial["seq"]


def test_a_click_is_not_an_onset_and_a_short_pause_is_not_an_offset():
    e = _Edges()
    assert (e.on_frames, e.off_frames) == (2, 7)
    t = iter(i * FRAME_S for i in range(100))
    assert e.feed(True, next(t)) is None                  # one frame: a click
    assert e.feed(False, next(t)) is None
    assert e.feed(True, next(t)) is None
    on = e.feed(True, next(t))
    assert on == (True, pytest.approx(2 * FRAME_S))       # where the voiced run began
    for _ in range(6):
        assert e.feed(False, next(t)) is None             # 180 ms of quiet: still him
    assert e.feed(True, next(t)) is None
    for _ in range(6):
        assert e.feed(False, next(t)) is None
    off = e.feed(False, next(t))
    assert off == (False, pytest.approx(11 * FRAME_S))    # the end of his last voiced frame


def test_vad_rows_stay_out_of_an_agents_hook():
    assert heard.EV_VAD in heard.QUIET_EVENTS and heard.EV_VAD not in heard.SHOWN_EVENTS
