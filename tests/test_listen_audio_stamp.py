"""EARS-192 fix-001: the ears stamp each record with its AUDIO wall time and its lag.

On 10-07 partials were written 64-108 s after the audio they describe, and the
mouth, which reads only ``ts`` (the write time), took them for fresh talk-over.
Each partial, turn, turn-discarded, vad and heartbeat now carries ``audio_ts``
(``listen-started`` + stream time) and ``lag_s`` (write time minus that). A
heartbeat adds ``stt_backlog_s`` and the mic's ``input_overflows``.

Synthetic audio, the energy classifier and a fake wall clock, so the lag is exact.
"""
from __future__ import annotations

import re
from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from ears import envnames, heard, lag
from tests._anchor import ANCHOR_SUMMER
from ears.listen import FRAME_S, capture, sources
from ears.listen.loop import _Capture

from listen_helpers import CountingSource, FakeSTT, RecordingSink, energy_detector, read_lines, synth

BASE = ANCHOR_SUMMER.timestamp()  # the test anchor, in daylight saving; any fixed value would do
STAMPED = ("partial", "turn")


class LiveSource(CountingSource):
    """Unpaced (the test runs at once) but claims real time, as the mic and a call do."""

    def __init__(self, samples, **kw):
        super().__init__(samples, **kw)
        self.realtime = True

    def frames(self):  # ArraySource paces when realtime is set: these frames are never paced
        self.realtime = False
        try:
            yield from super().frames()
        finally:
            self.realtime = True


class Clock:
    """Wall time = BASE + the frames the source has yielded + ``behind`` (a stalled loop)."""

    def __init__(self, src: CountingSource, behind=lambda stream_s: 0.0):
        self.src, self.behind = src, behind

    def __call__(self) -> float:
        stream_s = self.src.yielded * FRAME_S
        return BASE + stream_s + self.behind(stream_s)


def run(tmp_path, segments, *, behind=lambda s: 0.0, texts=None, heartbeat=0.0, realtime=True):
    audio = synth(segments)
    src = LiveSource(audio) if realtime else CountingSource(audio)
    sink = RecordingSink(tmp_path, src)
    capture(src, FakeSTT(texts), sink, heartbeat=heartbeat, detector=energy_detector(), stt_workers=0,
            clock=Clock(src, behind))
    return read_lines(tmp_path)


def spoken(rec: dict) -> float:
    """The stream second the record's audio ended at (``t1``, ``at`` or ``stream_s``)."""
    if rec["kind"] in STAMPED or rec.get("event") == "turn-discarded":
        return rec["t1"]
    return rec["at"] if rec.get("event") == heard.EV_VAD else rec["stream_s"]


# -- the capture -------------------------------------------------------------


def test_every_partial_turn_vad_and_heartbeat_carries_audio_ts_and_lag(tmp_path):
    lines = run(tmp_path, [("silence", 1.0), ("speech", 1.5), ("silence", 3.0)], heartbeat=1.0)
    kinds = {r["kind"] if r["kind"] != "event" else r["event"] for r in lines if lag.AUDIO_TS in r}
    assert {"partial", "turn", heard.EV_VAD, heard.EV_HEARTBEAT} <= kinds
    for r in lines:
        if r["kind"] == "partial" or r.get("event") in (heard.EV_VAD, heard.EV_HEARTBEAT):
            assert lag.audio_t(r) == pytest.approx(BASE + spoken(r), abs=0.001), r
            # the loop is on time: written within the chunker's quiet cut (0.35 s) of the audio's end
            assert 0.0 <= r["lag_s"] < 0.5, r
    # a turn ends on silence_s (2 s) of quiet AFTER its last word, so even on time its lag is
    # that wait: an alarm on lag reads partials and heartbeats, never turns
    turn = next(r for r in lines if r["kind"] == "turn")
    assert lag.audio_t(turn) == pytest.approx(BASE + turn["t1"], abs=0.001)
    assert 2.0 <= turn["lag_s"] < 2.1
    # the start, the stop and other events are not about audio: no stamp
    assert all(lag.AUDIO_TS not in r for r in lines
               if r.get("event") in (heard.EV_LISTEN_STARTED, heard.EV_LISTEN_STOPPED))


def test_a_stalled_recogniser_shows_its_lag_and_the_audio_time_stays_true(tmp_path):
    """The 10-07 shape: the loop falls 90 s behind its audio. ``ts`` says now; the
    stamp says when he spoke, and how late the record is."""
    lines = run(tmp_path, [("silence", 1.0), ("speech", 1.5), ("silence", 3.0)],
                behind=lambda s: 90.0 if s > 1.2 else 0.0)
    partials = [r for r in lines if r["kind"] == "partial"]
    turn = next(r for r in lines if r["kind"] == "turn")
    assert partials
    for r in partials + [turn]:
        assert lag.audio_t(r) == pytest.approx(BASE + r["t1"], abs=0.001)
    for r in partials:
        assert 90.0 <= lag.lag_of(r) < 90.5  # 90 s behind, plus at most the quiet cut
    assert 92.0 <= lag.lag_of(turn) < 92.1   # 90 s behind, plus the turn's 2 s of quiet


def test_the_stamp_is_the_same_iso_shape_as_ts():
    shape = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}[+-]\d\d:\d\d$")
    assert shape.match(heard._now_iso())
    assert shape.match(lag.iso(BASE + 0.25))
    assert lag.audio_t({"audio_ts": lag.iso(BASE + 0.25)}) == pytest.approx(BASE + 0.25, abs=0.001)


def test_a_turn_discarded_is_stamped_too(tmp_path):
    lines = run(tmp_path, [("silence", 1.0), ("speech", 1.0), ("silence", 3.0)], texts=["", "", ""])
    gone = next(r for r in lines if r.get("event") == "turn-discarded")
    assert lag.audio_t(gone) == pytest.approx(BASE + gone["t1"], abs=0.001)
    assert gone["lag_s"] >= 0.0


def test_an_unpaced_file_is_not_stamped(tmp_path):
    """Its stream time is not wall time, so an audio wall time would be a lie."""
    lines = run(tmp_path, [("silence", 1.0), ("speech", 1.5), ("silence", 3.0)], heartbeat=1.0,
                realtime=False)
    assert any(r["kind"] == "partial" for r in lines)
    for r in lines:
        assert not ({lag.AUDIO_TS, lag.LAG_S, "stt_backlog_s"} & r.keys()), r


def test_the_live_sources_say_they_run_in_real_time():
    assert sources.MicSource.realtime is True
    assert sources.CallSource.realtime is True


# -- the heartbeat: what its own lag cannot see ---------------------------------


def _bare_capture(source, clock) -> _Capture:
    return _Capture(source, sink=SimpleNamespace(write=lambda line: 1), stt=FakeSTT(), heartbeat=1.0,
                    ceiling=None, detector=energy_detector(), chunker=SimpleNamespace(quiet_s=0.35),
                    stop=lambda: False, session=None, agent=None, stt_workers=0, max_stt_failures=3,
                    clock=clock)


def test_the_heartbeat_names_the_oldest_chunk_waiting_on_the_stt():
    """In chunk mode the frame loop (and so the heartbeat) can be on time while partials
    wait in the STT's queue: the backlog is the age of the oldest waiting chunk's audio."""
    cap = _bare_capture(SimpleNamespace(name="mic", device="d", realtime=True), clock=lambda: BASE + 70.0)
    cap._wall0 = BASE
    assert cap._backlog_fields() == {"stt_backlog_s": 0.0}
    cap._pending.append((Future(), SimpleNamespace(t0=3.0, t1=5.0)))
    cap._pending.append((Future(), SimpleNamespace(t0=5.0, t1=7.0)))
    assert cap._backlog_fields() == {"stt_backlog_s": 65.0}


def test_the_heartbeat_carries_the_mics_dropped_blocks():
    src = SimpleNamespace(name="mic", device="d", realtime=True, input_overflows=3)
    cap = _bare_capture(src, clock=lambda: BASE)
    cap._wall0 = BASE
    assert cap._backlog_fields()["input_overflows"] == 3


def test_a_heartbeat_through_the_loop_carries_its_backlog(tmp_path):
    lines = run(tmp_path, [("silence", 2.5)], heartbeat=1.0)
    beats = [r for r in lines if r.get("event") == heard.EV_HEARTBEAT]
    assert beats and all(r["stt_backlog_s"] == 0.0 for r in beats)
    assert all("input_overflows" not in r for r in beats)  # a file source counts no drops


def test_the_mic_counts_input_overflows(monkeypatch):
    seen = {}

    class Stream:
        def __init__(self, **kw):
            seen["callback"] = kw["callback"]

        def start(self):
            pass

    fake = SimpleNamespace(query_devices=lambda *a, **k: {"default_samplerate": 16000}, InputStream=Stream)
    monkeypatch.setattr(sources, "_sd", lambda: fake)
    monkeypatch.setattr(sources, "resolve_input_device", lambda device=None: (None, "fake mic"))
    mic = sources.MicSource()
    mic._open()
    import numpy as np
    block = np.zeros((480, 1), dtype=np.int16)
    seen["callback"](block, 480, None, SimpleNamespace(input_overflow=False))
    seen["callback"](block, 480, None, SimpleNamespace(input_overflow=True))
    seen["callback"](block, 480, None, None)
    assert mic.input_overflows == 1
    assert mic._q.qsize() == 3  # a dropped block's neighbours are still delivered


# -- the reader: old records degrade safely -------------------------------------


def test_a_record_without_the_stamp_reads_as_none():
    old = {"ts": ANCHOR_SUMMER.astimezone(timezone(timedelta(hours=11))).isoformat(timespec="milliseconds"), "kind": "partial", "text": "stop", "t0": 1.0, "t1": 2.0}
    assert lag.audio_t(old) is None
    assert lag.lag_of(old) is None


@pytest.mark.parametrize("bad", [None, "", "not a time", 12, ["x"]])
def test_a_bad_stamp_reads_as_none(bad):
    assert lag.audio_t({"audio_ts": bad}) is None


@pytest.mark.parametrize("bad", [None, "1.2", True, [1]])
def test_a_bad_lag_reads_as_none(bad):
    assert lag.lag_of({"lag_s": bad}) is None


def test_lag_of_reads_a_number():
    assert lag.lag_of({"lag_s": 95}) == 95.0
    assert lag.lag_of({"lag_s": 0.412}) == 0.412


def test_a_stamped_record_round_trips_through_heard(tmp_path):
    rec = heard.append("partial", text="w1", directory=tmp_path, t0=1.0, t1=2.0, **lag.stamp(BASE + 2.0, BASE + 2.4))
    back = read_lines(tmp_path)[-1]
    assert back["seq"] == rec["seq"]
    assert lag.audio_t(back) == pytest.approx(BASE + 2.0, abs=0.001)
    assert back["lag_s"] == 0.4
    assert datetime.fromisoformat(back["ts"])  # ts is untouched: still the write time


# -- the alarm threshold, one place both sides read --------------------------------


def test_lag_warn_s_defaults_and_follows_its_env(monkeypatch):
    for k in envnames.group("EARS_LAG_WARN_S"):
        monkeypatch.delenv(k, raising=False)
    assert lag.LAG_WARN_S == 2.0
    assert lag.lag_warn_s() == 2.0
    monkeypatch.setenv("EARS_LAG_WARN_S", "3.5")
    assert lag.lag_warn_s() == 3.5
    monkeypatch.setenv("EARS_LAG_WARN_S", "soon")
    assert lag.lag_warn_s() == 2.0


# -- fix-003: the recogniser-lag alarm, once per episode ------------------------------


def _alarms(lines):
    return [r for r in lines if r.get("event") in (lag.LAG_ALARM, lag.LAG_ALARM_OK)]


def test_a_sustained_lag_is_one_alarm_and_one_all_clear(tmp_path, monkeypatch):
    """The 10-07 shape again, with heartbeats: every partial and every heartbeat in the
    episode is 90 s late, and the log says so ONCE; when the loop catches up, once more."""
    for k in envnames.group("EARS_LAG_WARN_S"):
        monkeypatch.delenv(k, raising=False)
    lines = run(tmp_path, [("silence", 1.0), ("speech", 1.5), ("silence", 3.0), ("speech", 1.5), ("silence", 4.0)],
                heartbeat=1.0, behind=lambda s: 90.0 if 1.2 < s <= 6.0 else 0.0)
    late = [r for r in lines if (r["kind"] == "partial" or r.get("event") == heard.EV_HEARTBEAT)
            and r.get("lag_s", 0) > lag.LAG_WARN_S]
    assert len(late) >= 5  # the flood the alarm must not echo
    said = _alarms(lines)
    assert [r["event"] for r in said] == [lag.LAG_ALARM, lag.LAG_ALARM_OK]
    alarm, ok = said
    assert alarm["kind"] == "event" and alarm["threshold_s"] == lag.LAG_WARN_S
    assert 90.0 <= alarm["lag_s"] < 90.5 and alarm["on"] in ("partial", "heartbeat")
    assert alarm["seq"] > late[0]["seq"]  # written after the late record that raised it
    assert "behind" in alarm["text"] and alarm["source"] == "file" and alarm["device"] == "synthetic"
    assert ok["since"] == alarm["since"] and ok["max_lag_s"] >= 90.0 and ok["lag_s"] < lag.lag_clear_s(2.0)
    assert "lasted_s" in ok  # its value here is the fake clock stepping back 90 s, not a duration


def test_on_time_there_is_no_alarm(tmp_path):
    lines = run(tmp_path, [("silence", 1.0), ("speech", 1.5), ("silence", 3.0)], heartbeat=1.0)
    assert _alarms(lines) == []


def test_the_threshold_follows_its_env(tmp_path, monkeypatch):
    """1 s behind is healthy at the default 2 s, an alarm at 0.5 s."""
    segs = [("silence", 1.0), ("speech", 1.5), ("silence", 3.0)]
    behind = lambda s: 1.0 if s > 0.5 else 0.0  # noqa: E731 - wall0 is taken on time, then it slips
    for k in envnames.group("EARS_LAG_WARN_S"):
        monkeypatch.delenv(k, raising=False)
    quiet = run(tmp_path / "a", segs, heartbeat=1.0, behind=behind)
    assert any(r.get("lag_s", 0) >= 1.0 for r in quiet) and _alarms(quiet) == []
    monkeypatch.setenv("EARS_LAG_WARN_S", "0.5")
    said = _alarms(run(tmp_path / "b", segs, heartbeat=1.0, behind=behind))
    assert [r["event"] for r in said] == [lag.LAG_ALARM] and said[0]["threshold_s"] == 0.5


def test_an_unpaced_file_never_alarms(tmp_path):
    """No wall clock, no lag, no alarm: a test file run at full speed is not 'behind'."""
    lines = run(tmp_path, [("silence", 1.0), ("speech", 1.5), ("silence", 3.0)], heartbeat=1.0,
                behind=lambda s: 90.0, realtime=False)
    assert _alarms(lines) == []


def test_a_backlog_while_he_is_silent_raises_it_from_the_heartbeat():
    """Chunk mode: the loop is on time but a chunk waits 65 s on the STT. No partial can
    say so; the heartbeat's stt_backlog_s does."""
    a = lag.LagAlarm(2.0)
    ev, f = a.heartbeat(0.02, 65.0, BASE)
    assert ev == lag.LAG_ALARM and f["lag_s"] == 65.0 and f["stt_backlog_s"] == 65.0 and f["on"] == "heartbeat"
    assert a.heartbeat(0.02, 80.0, BASE + 15) is None          # still behind: no second alarm
    ev, f = a.heartbeat(0.02, 0.0, BASE + 30, words_waiting=False)
    assert ev == lag.LAG_ALARM_OK and f["lasted_s"] == 30.0 and f["max_lag_s"] == 80.0


# -- the alarm's rule, alone ------------------------------------------------------------


def test_hovering_at_the_threshold_is_one_episode():
    """Hysteresis: under 2.0 but over 1.5 does not clear it, so 2.1, 1.9, 2.1 ... is one alarm."""
    a = lag.LagAlarm(2.0)
    assert lag.lag_clear_s(2.0) == 1.5
    said = [a.partial(v, BASE + i) for i, v in enumerate([2.1, 1.9, 2.2, 1.6, 2.4, 1.8, 2.05])]
    assert [s[0] for s in said if s] == [lag.LAG_ALARM]
    assert a.raised
    assert a.partial(1.4, BASE + 10)[0] == lag.LAG_ALARM_OK
    assert a.partial(2.5, BASE + 11)[0] == lag.LAG_ALARM  # a NEW episode is a new alarm


def test_exactly_the_threshold_is_not_over_it():
    a = lag.LagAlarm(2.0)
    assert a.partial(2.0, BASE) is None and not a.raised


def test_it_clears_only_when_every_reading_is_under():
    """A heartbeat on time does not clear a late partial: the stream may still be behind."""
    a = lag.LagAlarm(2.0)
    assert a.partial(70.0, BASE)[0] == lag.LAG_ALARM
    assert a.heartbeat(0.01, 0.0, BASE + 15) is None             # words may still be waiting
    assert a.partial(0.4, BASE + 20)[0] == lag.LAG_ALARM_OK      # a fresh word: caught up


def test_with_no_words_waiting_a_stale_partial_reading_is_dropped():
    a = lag.LagAlarm(2.0)
    a.partial(70.0, BASE)
    assert a.heartbeat(0.01, 0.0, BASE + 15, words_waiting=False)[0] == lag.LAG_ALARM_OK


def test_a_missing_reading_is_no_reading():
    a = lag.LagAlarm(2.0)
    assert a.partial(None, BASE) is None
    assert a.heartbeat(None, None, BASE) is None
    ev, f = a.heartbeat(None, 9.0, BASE)
    assert ev == lag.LAG_ALARM and f["lag_s"] == 9.0


def test_the_alarm_reads_lag_warn_s_by_default(monkeypatch):
    monkeypatch.setenv("EARS_LAG_WARN_S", "3")
    assert lag.LagAlarm().threshold_s == 3.0
    assert lag.lag_clear_s(0.4) == 0.2  # a tiny threshold keeps half of itself as margin
