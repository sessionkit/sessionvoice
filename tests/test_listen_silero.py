"""Silero VAD behind the end-of-turn interface (spec 3.4, VM-2274 do-009).

The Silero model is replaced by a fake ONNX session that scores a window by its
loudness, so these tests need neither onnxruntime nor the model file. The real
model is measured in findings/m3.4-false-turns.md. No device, no sound.
"""

from __future__ import annotations

import wave

import numpy as np
import pytest

from ears.listen import capture
from ears import envnames
from ears.listen.__main__ import build_parser, main
from ears.listen.detector import EnergyClassifier, SileroTurnDetector, SilenceTurnDetector, make_detector
from ears.listen.silero import CONTEXT, WINDOW, SileroClassifier, SileroError, model_path
from tests.listen_helpers import CountingSource, FakeSTT, RecordingSink, read_lines, synth


class FakeSession:
    """Silero's ONNX signature; the 'probability' is a function of the window's loudness."""

    def __init__(self, prob=None):
        self.calls = []
        self.prob = prob or (lambda x: 0.9 if np.abs(x).mean() > 0.05 else 0.02)

    def run(self, _outputs, feeds):
        x = feeds["input"]
        assert feeds["state"].shape == (2, 1, 128) and int(feeds["sr"]) == 16000
        self.calls.append(x.shape)
        return np.array([[self.prob(x[0, CONTEXT:])]], dtype=np.float32), feeds["state"] + 1


def frames(samples):
    return [samples[i:i + 480] for i in range(0, len(samples) - 479, 480)]


# -- the classifier ---------------------------------------------------------------


def test_frames_are_rebuffered_into_512_sample_windows_with_64_of_context():
    sess = FakeSession()
    c = SileroClassifier(session=sess)
    for f in frames(np.zeros(16000, dtype=np.int16)):  # 33 frames of 480 = 15840 samples
        c(f)
    assert len(sess.calls) == 15840 // WINDOW
    assert set(sess.calls) == {(1, WINDOW + CONTEXT)}


def test_hysteresis_speech_starts_at_threshold_and_holds_until_it_falls_015_below():
    probs = iter([0.4, 0.55, 0.45, 0.36, 0.34, 0.49, 0.51])
    c = SileroClassifier(session=FakeSession(prob=lambda x: next(probs)), threshold=0.5)
    one = np.zeros(WINDOW, dtype=np.int16)  # exactly one window per call
    got = []
    for _ in range(7):
        c._buf = np.zeros(0, dtype=np.float32)
        got.append(c(one))
    assert got == [False, True, True, True, False, False, True]


def test_no_model_is_a_clear_error(monkeypatch, tmp_path):
    for k in envnames.group("EARS_SILERO_MODEL"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(SileroError, match="model"):
        model_path(None)
    with pytest.raises(SileroError, match="not found"):
        model_path(str(tmp_path / "nope.onnx"))


def test_prob_max_is_the_highest_window_since_the_last_take_not_the_latest():
    # EARS-280: a 30 ms frame can run two windows; the earlier one must count too
    probs = iter([0.2, 0.8, 0.3, 0.6, 0.1])
    c = SileroClassifier(session=FakeSession(prob=lambda x: next(probs)))
    assert c.take_prob_max() is None, "no window scored yet: no number, never a fake 0"
    c(np.zeros(2 * WINDOW, dtype=np.int16))  # two windows in one call: 0.2, 0.8
    c(np.zeros(WINDOW, dtype=np.int16))      # 0.3
    assert c.prob == pytest.approx(0.3)
    assert c.take_prob_max() == pytest.approx(0.8)
    assert c.take_prob_max() is None, "taking resets"
    c(np.zeros(2 * WINDOW, dtype=np.int16))  # 0.6, 0.1
    assert c.take_prob_max() == pytest.approx(0.6)


def test_a_frame_short_of_a_window_scores_nothing_toward_the_max():
    c = SileroClassifier(session=FakeSession(prob=lambda x: 0.7))
    c(np.zeros(480, dtype=np.int16))  # 480 < 512: no window yet
    assert c.take_prob_max() is None
    c(np.zeros(480, dtype=np.int16))  # 960: one window
    assert c.take_prob_max() == pytest.approx(0.7)


# -- behind the same end-of-turn interface ------------------------------------------


def test_silero_detector_is_the_silence_rule_with_a_different_speech_call():
    d = make_detector("silero", classifier=SileroClassifier(session=FakeSession()))
    assert isinstance(d, SilenceTurnDetector) and d.name == "silero" and d.silence_s == 2.0


def test_turn_lines_name_the_silero_detector(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm"))
    source = CountingSource(synth([("silence", 0.5), ("speech", 1.5)]), tail_silence_s=3.0)
    det = SileroTurnDetector(classifier=SileroClassifier(session=FakeSession()))
    capture(source, FakeSTT(), RecordingSink(tmp_path / "logs", source), detector=det, stt_workers=0, heartbeat=0)
    turns = [ln for ln in read_lines(tmp_path / "logs") if ln["kind"] == "turn"]
    assert len(turns) == 1 and turns[0]["detector"] == "silero"


def test_a_loud_non_speech_burst_that_silero_rejects_makes_no_turn(tmp_path, monkeypatch):
    # the room case: loud enough for webrtcvad/energy, but the classifier says not speech
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm"))
    source = CountingSource(synth([("silence", 0.5), ("speech", 1.0)]), tail_silence_s=3.0)
    det = SileroTurnDetector(classifier=SileroClassifier(session=FakeSession(prob=lambda x: 0.1)))
    stt = FakeSTT(texts=["Bye."])
    capture(source, stt, RecordingSink(tmp_path / "logs", source), detector=det, stt_workers=0, heartbeat=0)
    lines = read_lines(tmp_path / "logs")
    assert not [ln for ln in lines if ln["kind"] in ("turn", "partial")]
    assert stt.calls == 0, "nothing reached whisper, so nothing to hallucinate"


# -- the flag: default off -----------------------------------------------------------


def test_the_default_detector_is_still_vad_silence():
    assert build_parser().parse_args(["capture"]).detector == "vad-silence"
    assert build_parser().parse_args(["capture", "--detector", "silero"]).detector == "silero"


def test_cli_with_silero_and_no_model_fails_before_listening(tmp_path, monkeypatch, capsys):
    for k in envnames.group("EARS_SILERO_MODEL"):
        monkeypatch.delenv(k, raising=False)
    pytest.importorskip("onnxruntime")
    wav = tmp_path / "s.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000)
        w.writeframes(np.zeros(1600, dtype=np.int16).tobytes())
    rc = main(["capture", "--source", f"file:{wav}", "--detector", "silero", "--log-dir", str(tmp_path / "logs")])
    out = capsys.readouterr().out
    assert rc == 1 and '"reason": "error"' in out and "model" in out
    assert not (tmp_path / "logs").exists() or not list((tmp_path / "logs").glob("heard_*.jsonl"))


# -- the heartbeat carries silero's max speech probability (EARS-280) ----------------


def _beats(tmp_path, monkeypatch, detector, segments, heartbeat=1.0):
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm"))
    source = CountingSource(synth(segments))
    capture(source, FakeSTT(), RecordingSink(tmp_path / "logs", source), detector=detector, stt_workers=0,
            heartbeat=heartbeat)
    return [ln for ln in read_lines(tmp_path / "logs") if ln.get("event") == "heartbeat"]


def test_each_silero_heartbeat_carries_the_max_over_its_period_reset_each_beat(tmp_path, monkeypatch):
    # speech in the first period only: 0.9 there; the later periods are all silence (0.02).
    # Without the reset at each beat the 0.9 would carry into every later heartbeat.
    det = SileroTurnDetector(classifier=SileroClassifier(session=FakeSession()))
    beats = _beats(tmp_path, monkeypatch, det, [("speech", 0.9), ("silence", 2.1)])
    assert [b["speech_prob_max"] for b in beats] == [0.9, 0.02, 0.02]
    assert all({"peak", "rms"} <= b.keys() for b in beats), "beside the level, not instead of it"


def test_the_max_is_rounded_to_three_places_and_takes_every_window_of_the_period(tmp_path, monkeypatch):
    # one window in the period scores 0.61234; every other one 0.1; the latest window
    # before the beat is a 0.1, so a field read off the latest probability would say 0.1
    calls = iter(range(10**6))
    det = SileroTurnDetector(classifier=SileroClassifier(
        session=FakeSession(prob=lambda x: 0.61234 if next(calls) == 7 else 0.1)))
    beats = _beats(tmp_path, monkeypatch, det, [("silence", 2.0)])
    assert [b["speech_prob_max"] for b in beats] == [0.612, 0.1]


def test_without_a_probability_the_field_is_omitted_never_a_fake_zero(tmp_path, monkeypatch):
    # webrtc (the default detector) and the energy classifier score no probability
    for i, det in enumerate((SilenceTurnDetector(), SilenceTurnDetector(classifier=EnergyClassifier()))):
        beats = _beats(tmp_path / str(i), monkeypatch, det, [("speech", 0.9), ("silence", 1.2)])
        assert len(beats) == 2
        assert not [b for b in beats if "speech_prob_max" in b]
