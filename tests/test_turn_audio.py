"""Kept turn audio (EARS-387 do-002): one WAV per final turn, only while recording is on.

The capture runs on a synthetic source with the energy classifier, so what is speech
is exact; the heard log and the audio dir live under tmp_path.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np
import pytest

from ears import heard, recording
from ears.listen import FRAME_S, SAMPLE_RATE, capture
from ears.listen import turnaudio
from ears.listen.turnaudio import CAP_S, PRUNE_EVERY_S, TurnAudio, default_dir, prune
from tests._anchor import ANCHOR
from tests.listen_helpers import CountingSource, FakeSTT, RecordingSink, energy_detector, read_lines

FRAME = int(round(FRAME_S * SAMPLE_RATE))
ROOT = Path(__file__).resolve().parent.parent
MAIL = ROOT / "keeper" / "ears-mail.py"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm"))
    for name in (recording.ENV_RECORD, recording.ENV_DAYS):
        monkeypatch.delenv(name, raising=False)


def quiet(seconds: float, seed: int = 7) -> np.ndarray:
    n = int(round(seconds * SAMPLE_RATE))
    return np.random.default_rng(seed).integers(-99, 100, n).astype(np.int16)


def tone(seconds: float, seed: int = 3) -> np.ndarray:
    """Loud (speech to the energy classifier) and different every sample, so a WAV
    can be matched to its place in the stream."""
    n = int(round(seconds * SAMPLE_RATE))
    t = np.arange(n) / SAMPLE_RATE
    jitter = np.random.default_rng(seed).integers(-50, 51, n)
    return (4000 * np.sin(2 * np.pi * 220 * t) + jitter).astype(np.int16)


class HookSource(CountingSource):
    """Calls ``hooks[k]()`` just before frame ``k`` is yielded."""

    def __init__(self, samples, hooks=None):
        super().__init__(samples)
        self.hooks = hooks or {}

    def frames(self):
        for frame in super().frames():
            hook = self.hooks.get(self.yielded - 1)
            if hook is not None:
                hook()
            yield frame


def run(tmp_path, samples, *, switch=None, keep=None, stt=None, hooks=None):
    logs = tmp_path / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    source = HookSource(samples, hooks)
    kw = {}
    if switch is not None:
        kw["record"] = switch
    if keep is not None:
        kw["turn_audio"] = keep
    result = capture(source, stt or FakeSTT(), RecordingSink(logs, source), detector=energy_detector(),
                     stt_workers=0, heartbeat=0, **kw)
    return result, read_lines(logs)


def turns(lines):
    return [ln for ln in lines if ln["kind"] == "turn"]


def wav(path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, SAMPLE_RATE)
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


TWO_TURNS = np.concatenate([quiet(1.0), tone(1.0), quiet(3.0, 8), tone(1.0, 4), quiet(3.0, 9)])


# -- off (the default) ----------------------------------------------------------------

def test_off_by_default_holds_nothing_makes_no_dir_and_rows_carry_no_audio(tmp_path):
    seen = []
    keep = TurnAudio(recording.Switch(), directory=tmp_path / "vm" / "audio")
    # mid-turn (frame 70 is inside the first tone): nothing is held while off
    hooks = {70: lambda: seen.append((len(keep._pre), keep._turn))}
    _, lines = run(tmp_path, TWO_TURNS, keep=keep, hooks=hooks)
    assert len(turns(lines)) == 2
    assert all("audio" not in t and "audio_s" not in t for t in turns(lines))
    assert seen == [(0, None)]
    assert not (tmp_path / "vm" / "audio").exists()
    started = next(ln for ln in lines if ln.get("event") == heard.EV_LISTEN_STARTED)
    assert started["record"] is False and started["record_from"] == "default"


def test_the_default_capture_keeps_under_the_heard_base_only_when_on(tmp_path):
    assert default_dir() == tmp_path / "vm" / "audio"
    _, lines = run(tmp_path / "a", TWO_TURNS)  # no switch, no keeper given: the capture's own
    assert all("audio" not in t for t in turns(lines))
    assert not default_dir().exists()
    _, lines = run(tmp_path / "b", TWO_TURNS, switch=recording.Fixed(True))
    assert len(turns(lines)) == 2
    assert all(Path(t["audio"]).parent.parent == default_dir() for t in turns(lines))


# -- on ---------------------------------------------------------------------------------

def test_on_each_turn_names_an_0600_wav_of_its_frames_from_t0_to_the_vad_close(tmp_path):
    d = tmp_path / "vm" / "audio"
    _, lines = run(tmp_path, TWO_TURNS, keep=TurnAudio(recording.Fixed(True), directory=d))
    rows = turns(lines)
    assert len(rows) == 2
    for row in rows:
        p = Path(row["audio"])
        assert p.is_file() and mode(p) == 0o600
        assert mode(p.parent) == 0o700 and mode(d) == 0o700
        assert p.parent.name == time.strftime("%Y-%m-%d")
        assert turnaudio.OURS_RE.fullmatch(p.name) and p.name.endswith("-1.wav")
        got = wav(p)
        assert row["audio_s"] == pytest.approx(len(got) / SAMPLE_RATE)
        assert "audio_truncated" not in row
        # it starts at the turn's t0 (pre-roll included) and is the very stream audio
        start = int(round(row["t0"] / FRAME_S)) * FRAME
        assert np.array_equal(got, TWO_TURNS[start:start + len(got)])
        # it runs from t0 to the VAD close: past t1, by the detector's silence
        assert row["t0"] + row["audio_s"] >= row["t1"]
    assert not list(d.rglob(".*"))  # no temp file left behind


def test_the_wav_is_written_before_the_row(tmp_path, monkeypatch):
    d = tmp_path / "vm" / "audio"
    order = []
    real_append = heard.append

    def append(kind, **kw):
        if kind == "turn":
            order.append(("row", Path(kw["audio"]).is_file()))
        return real_append(kind, **kw)

    monkeypatch.setattr(heard, "append", append)
    run(tmp_path, TWO_TURNS, keep=TurnAudio(recording.Fixed(True), directory=d))
    assert order == [("row", True), ("row", True)]


def test_a_long_turn_keeps_its_first_cap_seconds_and_says_truncated(tmp_path):
    d = tmp_path / "vm" / "audio"
    audio = np.concatenate([quiet(1.0), tone(3.0), quiet(3.0, 8)])
    _, lines = run(tmp_path, audio, keep=TurnAudio(recording.Fixed(True), directory=d, cap_s=1.0))
    [row] = turns(lines)
    got = wav(row["audio"])
    cap = int(round(1.0 / FRAME_S))
    assert row["audio_truncated"] is True and row["audio_s"] == pytest.approx(cap * FRAME_S)
    assert len(got) == cap * FRAME
    start = int(round(row["t0"] / FRAME_S)) * FRAME
    assert np.array_equal(got, audio[start:start + len(got)])  # the FIRST second


def test_the_default_cap_is_120_seconds():
    assert CAP_S == 120.0


def test_a_discarded_turn_keeps_no_audio(tmp_path):
    d = tmp_path / "vm" / "audio"
    _, lines = run(tmp_path, TWO_TURNS, keep=TurnAudio(recording.Fixed(True), directory=d),
                   stt=FakeSTT(texts=[""] * 20))
    assert turns(lines) == []
    assert len([ln for ln in lines if ln.get("event") == "turn-discarded"]) == 2
    assert not list(d.rglob("*.wav"))


class Flip:
    """A switch whose answer the test sets; ``days`` as Fixed."""

    def __init__(self, on: bool, days: int = 7):
        self.on, self._days = on, days

    def record(self):
        return recording.Record(self.on, "file")

    def days(self):
        return recording.Days(self._days, "file")

    def __call__(self):
        return self.on


def test_rule_8_off_at_write_time_means_no_file_and_no_audio_field(tmp_path):
    d = tmp_path / "vm" / "audio"
    sw = Flip(True)
    keep = TurnAudio(sw, directory=d)
    real_take = keep.take

    def take():
        out = real_take()
        sw.on = False  # flipped between the turn closing and the write
        return out

    keep.take = take
    _, lines = run(tmp_path, TWO_TURNS, keep=keep)
    assert all("audio" not in t for t in turns(lines))
    assert not d.exists()


@pytest.mark.parametrize("first,second", [(False, True), (True, False)])
def test_flipping_ears_json_mid_run_changes_the_next_turn(tmp_path, first, second):
    conf = tmp_path / "ears.json"
    conf.write_text(json.dumps({"record": first, "other": 1}))
    d = tmp_path / "vm" / "audio"
    switch = recording.Switch(env={}, path=str(conf), log=lambda m: None)

    def flip():
        conf.write_text(json.dumps({"record": second, "other": 1, "pad": "x" * 10}))

    # frame 150 (3.0 s) is in the quiet between the turns
    _, lines = run(tmp_path, TWO_TURNS, keep=TurnAudio(switch, directory=d), hooks={150: flip})
    rows = turns(lines)
    assert len(rows) == 2
    assert [("audio" in r) for r in rows] == [first, second]
    assert len(list(d.rglob("*.wav"))) == 1


def test_a_turn_that_opened_while_off_keeps_nothing_even_if_on_at_close(tmp_path):
    d = tmp_path / "vm" / "audio"
    sw = Flip(False)
    audio = np.concatenate([quiet(1.0), tone(1.0), quiet(3.0, 8)])
    _, lines = run(tmp_path, audio, keep=TurnAudio(sw, directory=d), hooks={70: lambda: setattr(sw, "on", True)})
    [row] = turns(lines)
    assert "audio" not in row  # it was not held from t0; the next turn would be


def test_a_write_error_is_said_once_and_the_turns_still_land(tmp_path):
    blocker = tmp_path / "vm"
    blocker.mkdir()
    (blocker / "audio").write_text("a file where the dir would go")
    _, lines = run(tmp_path, TWO_TURNS, keep=TurnAudio(recording.Fixed(True), directory=blocker / "audio"))
    assert len(turns(lines)) == 2 and all("audio" not in t for t in turns(lines))
    errs = [ln for ln in lines if ln.get("event") == "turn-audio-error"]
    assert len(errs) == 1 and "Error" in errs[0]["detail"]


def test_a_taken_name_gets_the_next_number_and_never_overwrites(tmp_path):
    d = tmp_path / "audio"
    clock = lambda: ANCHOR.timestamp() + 0.123  # noqa: E731 - one fixed instant
    keep = TurnAudio(recording.Fixed(True), directory=d, clock=clock)
    frames = [np.full(FRAME, 7, dtype=np.int16)]
    a = keep.write(frames, False)["audio"]
    b = keep.write([np.full(FRAME, 9, dtype=np.int16)], False)["audio"]
    assert a.endswith("-1.wav") and b.endswith("-2.wav")
    assert wav(a)[0] == 7 and wav(b)[0] == 9


def test_cut_and_resume_split_the_held_frames_at_a_speaker_change():
    keep = TurnAudio(recording.Fixed(True), directory=Path("/nonexistent"))
    keep._holding = True
    for i in range(5):
        keep.frame(np.full(FRAME, i, dtype=np.int16), i == 0, 1)
    rest = keep.cut(2)
    first, trunc = keep.take()
    assert [int(f[0]) for f in first] == [0, 1] and trunc is False
    keep.resume(rest)
    keep.frame(np.full(FRAME, 5, dtype=np.int16), False, 0)
    second, _ = keep.take()
    assert [int(f[0]) for f in second] == [2, 3, 4, 5]


# -- the heard mail -----------------------------------------------------------------------

def test_ears_mail_dry_run_prints_ears_audio_for_a_kept_turn(tmp_path):
    d = tmp_path / "vm" / "audio"
    _, lines = run(tmp_path, TWO_TURNS[:int(5 * SAMPLE_RATE)],
                   keep=TurnAudio(recording.Fixed(True), directory=d),
                   stt=FakeSTT(texts=["hey pip what time is it"] + [""] * 20))
    [row] = turns(lines)
    base = tmp_path / "mail"
    logs = base / "logs" / "conversations"
    logs.mkdir(parents=True)
    row = dict(row, source="mic", device="airpods")  # the keeper mails the mic's turns
    plain = dict(row, seq=row["seq"] + 1, text="hey pip and this one kept nothing")
    plain.pop("audio")
    (logs / f"heard_{time.strftime('%Y-%m-%d')}.jsonl").write_text(
        json.dumps(row) + "\n" + json.dumps(plain) + "\n")
    rules = tmp_path / "wake.json"
    rules.write_text(json.dumps({"defaults": {"openers": "hey"}, "seats": {"pip": {"names": "pip"}}}))
    p = subprocess.run([sys.executable, str(MAIL), "--rules", str(rules), "--dry-run", "--once",
                        "--from-start", "--domain", "example.test", "--to", "seats", "--seats", "pip"],
                       capture_output=True, text=True, timeout=60, env=dict(os.environ, VOICEMODE_BASE_DIR=str(base)))
    assert p.returncode == 0, p.stderr
    mails = [json.loads(ln) for ln in p.stdout.splitlines() if ln.strip()]
    assert len(mails) == 2, (p.stdout, p.stderr)
    assert mails[0]["mail"]["headers"]["Ears-Audio"] == row["audio"]
    assert "Ears-Audio" not in mails[1]["mail"]["headers"]


# -- retention ----------------------------------------------------------------------------

DAY = 86400.0
NOW = time.mktime((2030, 10, 19, 12, 0, 0, 0, 0, -1))


def _kept(d: Path, day: str, name: str = "120000-000-1.wav") -> Path:
    p = d / day / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"RIFF")
    return p


def test_prune_deletes_date_dirs_older_than_record_days_and_keeps_the_rest(tmp_path):
    d = tmp_path / "audio"
    old = _kept(d, "2030-10-11")         # 8 days old: gone at 7
    edge = _kept(d, "2030-10-12")        # exactly 7 days: kept
    new = _kept(d, "2030-10-19")
    assert prune(d, NOW, 7) == 1
    assert not old.parent.exists() and edge.exists() and new.exists()


def test_record_days_0_never_prunes(tmp_path):
    d = tmp_path / "audio"
    ancient = _kept(d, ANCHOR.strftime("%Y-%m-%d"))
    assert prune(d, NOW, 0) == 0
    assert ancient.exists()
    sw = recording.Switch(env={recording.ENV_DAYS: "0"}, path=str(tmp_path / "none.json"), log=lambda m: None)
    TurnAudio(sw, directory=d, clock=lambda: NOW).open()
    assert ancient.exists()


def test_prune_touches_only_what_it_wrote(tmp_path):
    """The scoped-prune rule: plant everything that is NOT ours, old,
    and prove the pruner leaves it all alone."""
    base = tmp_path / "vm"
    d = base / "audio"
    ours = _kept(d, "2030-01-03")
    ours_tmp = _kept(d, "2030-01-04", ".120000-000-4242.tmp.wav")
    planted = [
        base / "sibling" / "2030-01-03" / "120000-000-1.wav",            # a sibling dir of audio/
        base / "mouth" / "audio" / "2030-01-03" / "u1.wav",               # a mouth-like audio dir
        base / "mouth" / "audio" / "2030-01-03" / "120000-000-1.wav",
        base / "state" / "missed-reply" / "missed-reply_20300103-120000-000_u.wav",
        d / "2030-01-03" / "notes.txt",                                   # a non-wav in an old date dir
        d / "2030-01-03" / "someone-elses.wav",                           # a wav it did not name
        d / "calls" / "120000-000-1.wav",                                 # a non-date dir inside audio/
        d / "2030-1-3" / "120000-000-1.wav",                              # not a date pattern
        d / "2030-01-07" / "sub" / "120000-000-1.wav",                    # a nested tree in a date dir
    ]
    for p in planted:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    # a symlinked date dir pointing at someone else's files
    elsewhere = base / "elsewhere"
    target = elsewhere / "120000-000-1.wav"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x")
    (d / "2030-01-05").symlink_to(elsewhere, target_is_directory=True)
    for p in [*planted, target, ours, ours_tmp]:
        os.utime(p, (NOW - 400 * DAY, NOW - 400 * DAY))

    assert prune(d, NOW, 7) == 2
    assert not ours.exists() and not ours_tmp.exists()
    assert not ours_tmp.parent.exists()          # emptied, so removed
    assert ours.parent.exists()                  # still holds files it did not write
    for p in planted:
        assert p.exists(), p
    assert target.exists() and (d / "2030-01-05").is_symlink()


def test_prune_runs_at_capture_start_and_then_at_most_hourly(tmp_path):
    d = tmp_path / "audio"
    t = [NOW]
    keep = TurnAudio(recording.Fixed(True), directory=d, clock=lambda: t[0])
    _kept(d, "2030-10-03")
    assert keep.open() == 1                       # at start (recording on)
    _kept(d, "2030-10-04")
    t[0] += PRUNE_EVERY_S - 1
    assert keep.poll() == 0                       # not yet an hour
    t[0] += 2
    assert keep.poll() == 1
    _kept(d, "2030-10-05")
    t[0] += 60
    assert keep.poll() == 0                       # the next one waits another hour


def test_prune_with_no_audio_dir_makes_none(tmp_path):
    d = tmp_path / "audio"
    TurnAudio(recording.Fixed(True), directory=d, clock=lambda: NOW).open()
    assert not d.exists()


def test_the_capture_prunes_at_start(tmp_path):
    d = tmp_path / "vm" / "audio"
    # the capture prunes on the REAL clock, so its old date dir is written relative to now
    old = _kept(d, time.strftime("%Y-%m-%d", time.localtime(time.time() - 400 * DAY)))
    run(tmp_path, quiet(0.5), keep=TurnAudio(recording.Fixed(True), directory=d))
    assert not old.exists()


def test_recording_off_prunes_nothing(tmp_path):
    """do-003 (2): the prune runs only while recording is on, read at prune time."""
    d = tmp_path / "vm" / "audio"
    old = _kept(d, ANCHOR.strftime("%Y-%m-%d"))
    keep = TurnAudio(recording.Fixed(False), directory=d, clock=lambda: NOW)
    run(tmp_path, quiet(0.5), keep=keep)
    assert keep.open() == 0 and keep._prune() == 0
    assert old.exists()
