"""The missed-reply clip (EARS-280 item 2).

A mouth ``said`` with no turn inside the window keeps that window's audio as one WAV
(0600, in a 0700 dir under the ears' state dir, never synced, kept for record_days) and
one ``missed-reply`` event names it. Synthetic audio only (a tone and quiet noise from a
fixed seed), unpaced sources, a fake STT, every path in tmp_path: no mic, no device, no
real audio of anyone, nothing under ~/.voicemode.
"""

from __future__ import annotations

import os
import stat
import wave
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pytest

from ears import heard, recording
from ears.listen import FRAME_S, SAMPLE_RATE, capture
from ears.listen.__main__ import _missed_reply, build_parser
from ears.listen.missed import (
    DEFAULT_WINDOW_S,
    PRUNE_EVERY_S,
    MissedReply,
    SaidTail,
    default_dir,
    prune,
    synced_reason,
)
from ears.listen.silero import SileroClassifier
from tests.listen_helpers import (
    CountingSource,
    FakeSTT,
    RecordingSink,
    energy_detector,
    read_lines,
)

FRAME = int(round(FRAME_S * SAMPLE_RATE))  # 480 samples
# EARS-387: a clip is written only while the recording switch is on (off by default).
# These tests are about the clip itself, so they hold the switch on; the switch's own
# gate is tested at the bottom of this file and in tests/test_recording.py.
ON = recording.Fixed(True)
# The old fixed two days, kept here as test data: the switch above keeps 7 by default.
KEEP_S = 2 * 24 * 3600.0
ON_2 = recording.Fixed(True, days=2)  # record_days 2: the old two-day tests, unchanged


@pytest.fixture(autouse=True)
def _no_real_voicemode_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm"))


def quiet(seconds: float, seed: int = 7) -> np.ndarray:
    """Low noise (|x| < 100): under the energy classifier's 500, so it is never speech,
    and every sample differs, so a clip can be matched to its place in the stream."""
    n = int(round(seconds * SAMPLE_RATE))
    return np.random.default_rng(seed).integers(-99, 100, n).astype(np.int16)


def tone(seconds: float) -> np.ndarray:
    t = np.arange(int(round(seconds * SAMPLE_RATE))) / SAMPLE_RATE
    return (4000 * np.sin(2 * np.pi * 220 * t)).astype(np.int16)


class MouthSource(CountingSource):
    """Writes a mouth ``said`` into the heard log just before frame ``k`` is yielded,
    as the mouth would when a line ends at that point of the stream."""

    def __init__(self, samples, logs: Path, said_at: dict[int, dict]):
        super().__init__(samples)
        self.logs, self.said_at = logs, said_at

    def frames(self):
        for frame in super().frames():
            fields = self.said_at.get(self.yielded - 1)
            if fields is not None:
                heard.said(directory=self.logs, **fields)
            yield frame


def run(tmp_path, samples, said_at=None, *, window_s=1.0, stt=None, clip_dir=None, clock=None,
        home=None, missed=True, detector=None, switch=ON):
    logs = tmp_path / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    said_at = {k: {"utt": f"u{k}", "played_s": 2.0, **v} for k, v in (said_at or {}).items()}
    source = MouthSource(samples, logs, said_at)
    sink = RecordingSink(logs, source)
    clips = clip_dir or tmp_path / "vm" / "state" / "missed-reply"
    mr = None
    if missed:
        kw = {"clock": clock} if clock else {}
        mr = MissedReply(SaidTail(lambda: heard.log_path(directory=logs)), clips, window_s=window_s,
                         home=home or tmp_path / "home", switch=switch, **kw)
    result = capture(source, stt or FakeSTT(), sink, detector=detector or energy_detector(),
                     stt_workers=0, heartbeat=0, missed_reply=mr)
    return result, read_lines(logs), clips


def events(lines, name):
    return [ln for ln in lines if ln["kind"] == "event" and ln.get("event") == name]


def wav_samples(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, SAMPLE_RATE)
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


# -- the trigger --------------------------------------------------------------------


def test_a_said_with_no_turn_writes_exactly_one_wav_of_that_window(tmp_path):
    audio = quiet(3.0)
    result, lines, clips = run(tmp_path, audio, {20: {}}, window_s=1.0)

    assert result.reason == "eof"
    files = sorted(clips.glob("*.wav"))
    assert len(files) == 1
    [ev] = events(lines, heard.EV_MISSED_REPLY)
    assert ev["file"] == str(files[0]) and ev["utt"] == "u20" and ev["window_s"] == 1.0
    said = next(ln for ln in lines if ln["kind"] == "said")
    assert ev["said_seq"] == said["seq"]
    # the window is the line's end (frame 20) on, the very frames the capture took in
    got = wav_samples(files[0])
    n = int(round(1.0 / FRAME_S))
    assert ev["t0"] == pytest.approx(20 * FRAME_S) and ev["audio_s"] == pytest.approx(n * FRAME_S)
    assert np.array_equal(got, audio[20 * FRAME:(20 + n) * FRAME])
    assert "speech_prob_max" not in ev  # the energy classifier gives no probability


def test_a_turn_inside_the_window_writes_nothing(tmp_path):
    # the line ends at 0.6 s; he answers at 1.0 s; the turn lands 2 s after he stops
    audio = np.concatenate([quiet(1.0), tone(1.0), quiet(4.0, seed=8)])
    _result, lines, clips = run(tmp_path, audio, {20: {}}, window_s=5.0)
    assert [ln["text"] for ln in lines if ln["kind"] == "turn"] == ["w1"]
    assert events(lines, heard.EV_MISSED_REPLY) == []
    assert not clips.exists() or list(clips.iterdir()) == []


def test_no_said_writes_nothing_and_makes_no_directory(tmp_path):
    result, lines, clips = run(tmp_path, quiet(3.0), {}, window_s=1.0)
    assert result.reason == "eof"
    assert events(lines, heard.EV_MISSED_REPLY) == []
    assert not clips.exists()


def test_a_turn_open_when_the_window_fills_cancels_it_when_it_lands(tmp_path):
    # window 1 s from 0.6 s; he starts at 1.2 s, still talking at 1.6 s; the turn lands later
    audio = np.concatenate([quiet(1.2), tone(1.0), quiet(4.0, seed=8)])
    _, lines, clips = run(tmp_path, audio, {20: {}}, window_s=1.0)
    assert [ln["kind"] for ln in lines if ln["kind"] == "turn"] == ["turn"]
    assert events(lines, heard.EV_MISSED_REPLY) == []
    assert not clips.exists()


def test_a_discarded_turn_is_no_reply_the_window_is_kept(tmp_path):
    # the same, but the STT hears no words in it: turn-discarded, and the window is written
    audio = np.concatenate([quiet(1.2), tone(1.0), quiet(4.0, seed=8)])
    _, lines, _clips = run(tmp_path, audio, {20: {}}, window_s=1.0, stt=FakeSTT(texts=[""] * 10))
    assert [ln for ln in lines if ln["kind"] == "turn"] == []
    assert len(events(lines, "turn-discarded")) == 1
    [ev] = events(lines, heard.EV_MISSED_REPLY)
    assert len(wav_samples(Path(ev["file"]))) == int(round(1.0 / FRAME_S)) * FRAME  # still one window


def test_a_said_before_the_capture_started_or_never_played_opens_nothing(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir(parents=True)
    heard.said(utt="before", played_s=3.0, directory=logs)  # an old line: owed nothing
    _, lines, clips = run(tmp_path, quiet(3.0), {20: {"played_s": 0.0}}, window_s=1.0)
    assert events(lines, heard.EV_MISSED_REPLY) == []
    assert not clips.exists()


def test_a_second_line_restarts_the_window_one_clip_for_the_latest(tmp_path):
    audio = quiet(4.0)
    _, lines, clips = run(tmp_path, audio, {20: {}, 40: {}}, window_s=1.0)
    [ev] = events(lines, heard.EV_MISSED_REPLY)
    assert ev["utt"] == "u40" and ev["t0"] == pytest.approx(40 * FRAME_S)
    assert len(list(clips.glob("*.wav"))) == 1


def test_a_window_still_open_at_the_end_of_the_capture_writes_nothing(tmp_path):
    _, lines, clips = run(tmp_path, quiet(1.0), {20: {}}, window_s=5.0)
    assert events(lines, heard.EV_MISSED_REPLY) == []
    assert not clips.exists()


def test_no_missed_reply_object_is_todays_capture_exactly(tmp_path):
    def strip(lines):
        return [{k: v for k, v in ln.items() if k not in ("ts", "pid", "wall_s")} for ln in lines
                if ln["kind"] != "said"]

    audio = np.concatenate([quiet(1.0), tone(1.0), quiet(3.0, seed=8)])
    _, off, _ = run(tmp_path / "a", audio, {20: {}}, missed=False)
    _, on, _ = run(tmp_path / "b", audio, {20: {}}, window_s=1.0)  # he answers: no clip
    assert strip(on) == [
        {**ln, "missed_reply_s": 1.0} if ln.get("event") == heard.EV_LISTEN_STARTED else ln
        for ln in strip(off)]


# -- mode, place --------------------------------------------------------------------


def test_the_clip_is_0600_in_a_0700_dir_whatever_the_umask_or_an_old_dir(tmp_path):
    clips = tmp_path / "vm" / "state" / "missed-reply"
    clips.mkdir(parents=True, mode=0o755)
    os.chmod(clips, 0o755)
    old = os.umask(0o000)
    try:
        _, _lines, _ = run(tmp_path, quiet(2.0), {20: {}}, window_s=1.0)
    finally:
        os.umask(old)
    [f] = clips.glob("*.wav")
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert stat.S_IMODE(clips.stat().st_mode) == 0o700


def test_the_default_dir_is_the_ears_state_dir(tmp_path):
    assert default_dir() == tmp_path / "vm" / "state" / "missed-reply"
    mr = MissedReply(list, home=tmp_path / "home", switch=ON)
    assert mr.open() == [] and mr.directory == default_dir()


@pytest.mark.parametrize("where", [
    "Library/Mobile Documents/com~apple~CloudDocs/clips",
    "Library/CloudStorage/Dropbox/clips",
    "Desktop/clips",
    "Documents/clips",
    "Dropbox/clips",
    "OneDrive/x/clips",
])
def test_a_synced_dir_is_refused_with_one_warning_and_no_clip(tmp_path, where):
    home = tmp_path / "home"
    clips = home / where
    _, lines, _ = run(tmp_path, quiet(2.0), {20: {}}, window_s=1.0, clip_dir=clips, home=home)
    [off] = events(lines, "missed-reply-off")
    assert "synced location" in off["why"] and off["dir"] == str(clips)
    assert events(lines, heard.EV_MISSED_REPLY) == []
    assert not clips.exists()


def test_a_symlink_into_icloud_is_refused(tmp_path):
    home = tmp_path / "home"
    cloud = home / "Library" / "Mobile Documents" / "x"
    cloud.mkdir(parents=True)
    link = tmp_path / "state-link"
    link.symlink_to(cloud)
    assert "iCloud" in synced_reason(link / "missed-reply", home)


def test_a_sync_marker_file_is_refused(tmp_path):
    (tmp_path / "share").mkdir()
    (tmp_path / "share" / ".stfolder").mkdir()
    assert "synced folder" in synced_reason(tmp_path / "share" / "a" / "clips", tmp_path / "home")


def test_a_git_tree_with_a_remote_is_refused_and_one_without_is_local(tmp_path):
    pushed, local = tmp_path / "store", tmp_path / "vmdir"
    for repo in (pushed, local):
        (repo / ".git").mkdir(parents=True)
        (repo / ".git" / "config").write_text("[core]\n\tbare = false\n")
    (pushed / ".git" / "config").write_text('[core]\n\tbare = false\n[remote "origin"]\n\turl = host3:x.git\n')
    assert "git work tree with a remote" in synced_reason(pushed / "state" / "missed-reply", tmp_path / "h")
    assert synced_reason(local / "state" / "missed-reply", tmp_path / "h") is None


def test_a_git_worktree_file_is_followed_to_its_common_config(tmp_path):
    main = tmp_path / "main"
    (main / ".git" / "worktrees" / "wt").mkdir(parents=True)
    (main / ".git" / "config").write_text('[remote "origin"]\n\turl = x\n')
    (main / ".git" / "worktrees" / "wt" / "commondir").write_text("../..\n")
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {main / '.git' / 'worktrees' / 'wt'}\n")
    assert "remote" in synced_reason(wt / "clips", tmp_path / "h")


def test_a_plain_local_dir_is_not_synced(tmp_path):
    assert synced_reason(tmp_path / "vm" / "state" / "missed-reply", tmp_path / "home") is None


# -- unreadable is not absent (from Python 3.14, Path.exists/is_dir/is_file read it as False) --

needs_modes = pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                                 reason="root ignores mode 000")


@contextmanager
def unsearchable(d: Path):
    """Mode 000 on ``d`` for the block, restored after it whatever happens."""
    os.chmod(d, 0)
    try:
        yield
    finally:
        os.chmod(d, 0o755)


def _pushed_repo(repo: Path) -> Path:
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "config").write_text('[remote "origin"]\n\turl = x\n')
    (repo / "clips").mkdir()
    return repo / "clips"


@needs_modes
def test_a_folder_that_cannot_be_searched_is_refused_not_read_as_absent(tmp_path):
    """A pushed repo whose folder is mode 000: 3.13's exists() raised out of synced_reason
    (a crash in open()), 3.14's said absent (allowed as local). Both now refuse."""
    clips = _pushed_repo(tmp_path / "store")
    assert "git work tree with a remote" in synced_reason(clips, tmp_path / "h")
    with unsearchable(tmp_path / "store"):
        why = synced_reason(clips, tmp_path / "h")
    assert why is not None and "cannot be read" in why and "Permission denied" in why


@needs_modes
def test_a_dot_git_that_cannot_be_looked_up_is_refused(tmp_path):
    """The markers beside it read fine; the .git itself (a link into a mode-000 folder)
    cannot be looked up: refused, never taken for a folder with no repo."""
    hidden = tmp_path / "locked"
    _pushed_repo(hidden / "real")
    tree = tmp_path / "tree"
    (tree / "clips").mkdir(parents=True)
    (tree / ".git").symlink_to(hidden / "real" / ".git")
    assert "git work tree with a remote" in synced_reason(tree / "clips", tmp_path / "h")
    with unsearchable(hidden):
        why = synced_reason(tree / "clips", tmp_path / "h")
    assert why is not None and "config cannot be read" in why and "Permission denied" in why


@needs_modes
def test_an_unsearchable_clip_folder_turns_clips_off_and_the_capture_goes_on(tmp_path):
    clips = _pushed_repo(tmp_path / "store") / "missed-reply"
    with unsearchable(tmp_path / "store"):
        result, lines, _ = run(tmp_path, quiet(2.0), {20: {}}, window_s=1.0, clip_dir=clips,
                               home=tmp_path / "home")
    [off] = events(lines, "missed-reply-off")
    assert "synced location" in off["why"] and "cannot be read" in off["why"]
    assert events(lines, heard.EV_MISSED_REPLY) == []
    assert not clips.exists()


def test_a_dangling_dot_git_or_marker_link_is_absent_as_before(tmp_path):
    """Absent stays absent: a link to nothing is no marker and no repo."""
    d = tmp_path / "d"
    (d / "clips").mkdir(parents=True)
    (d / ".git").symlink_to(tmp_path / "gone")
    (d / ".stfolder").symlink_to(tmp_path / "gone-too")
    assert synced_reason(d / "clips", tmp_path / "h") is None


def test_no_path_predicate_decides_absent_in_the_synced_check():
    """synced_reason and _git_config tell absent from unreadable by os.stat, never by a
    Path predicate (which reads unreadable as False from Python 3.14)."""
    import inspect
    import re as _re

    from ears.listen import missed
    for fn in (missed.synced_reason, missed._git_config):
        src = inspect.getsource(fn).split('"""', 2)[2]   # past the docstring
        assert not _re.search(r"\.(exists|is_dir|is_file)\(", src), fn.__name__


# -- record_days, then gone (EARS-387 do-003: was a fixed two days) ----------------------


def _clip(d: Path, name: str, mtime: float) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_bytes(b"RIFF")
    os.utime(p, (mtime, mtime))
    return p


def test_clips_older_than_record_days_are_deleted_at_start_and_at_each_new_clip(tmp_path):
    now = 2_000_000_000.0
    clock = [now]
    clips = tmp_path / "vm" / "state" / "missed-reply"
    old = _clip(clips, "missed-reply_old.wav", now - KEEP_S - 60)
    fresh = _clip(clips, "missed-reply_fresh.wav", now - KEEP_S + 3600)
    other = _clip(clips, "not-ours.wav", now - 10 * KEEP_S)
    lines = [{"seq": 9, "kind": "said", "utt": "u", "played_s": 1.0, "ts": heard._now_iso()}]
    said = lambda: [lines.pop()] if lines else []

    mr = MissedReply(said, clips, window_s=0.3, clock=lambda: clock[0], home=tmp_path / "home", switch=ON_2)
    assert mr.open() == []  # at capture start
    assert not old.exists() and fresh.exists() and other.exists()

    clock[0] = now + 7200  # two hours on: fresh is now past two days, and the next clip prunes it
    mr.poll(0.0)
    out = []
    for _ in range(10):
        out += mr.frame(np.zeros(FRAME, dtype=np.int16), None, False)
    [(name, ev)] = out
    assert name == heard.EV_MISSED_REPLY and ev["pruned"] == 1
    assert not fresh.exists() and other.exists() and Path(ev["file"]).exists()
    assert os.path.basename(ev["file"]).startswith("missed-reply_")


def test_a_long_capture_prunes_once_an_hour_with_no_new_clip(tmp_path):
    now = [1_000_000.0]
    clips = tmp_path / "c"
    mr = MissedReply(list, clips, clock=lambda: now[0], home=tmp_path / "home", switch=ON_2)
    mr.open()
    c = _clip(clips, "missed-reply_a.wav", now[0])
    now[0] += KEEP_S + 1
    mr.poll(PRUNE_EVERY_S - 1)
    assert c.exists()
    mr.poll(PRUNE_EVERY_S)
    assert not c.exists()


def test_prune_keeps_exactly_record_days(tmp_path):
    now = 5_000_000.0
    a = _clip(tmp_path, "missed-reply_a.wav", now - KEEP_S)        # exactly two days: kept
    b = _clip(tmp_path, "missed-reply_b.wav", now - KEEP_S - 1)    # a second past: gone
    assert prune(tmp_path, now, 2) == 1
    assert a.exists() and not b.exists()


def test_retention_follows_record_days_not_a_fixed_two_days(tmp_path):
    """do-003 (1): the clip's retention is record_days, read from the switch; a clip
    five days old stays at the default 7, and goes at record_days 3."""
    now = 5_000_000.0
    c = _clip(tmp_path / "c", "missed-reply_a.wav", now - 5 * 86400)
    MissedReply(list, tmp_path / "c", clock=lambda: now, home=tmp_path / "home", switch=ON).open()
    assert c.exists()  # 5 days < 7: kept (the old fixed two days would have deleted it)
    sw = recording.Switch(env={recording.ENV_RECORD: "1", recording.ENV_DAYS: "3"},
                          path=str(tmp_path / "none.json"), log=lambda m: None)
    MissedReply(list, tmp_path / "c", clock=lambda: now, home=tmp_path / "home", switch=sw).open()
    assert not c.exists()


def test_prune_0_days_never_prunes(tmp_path):
    now = 5_000_000.0
    a = _clip(tmp_path, "missed-reply_a.wav", now - 400 * 86400)
    assert prune(tmp_path, now, 0) == 0
    assert a.exists()


def test_no_fixed_two_day_constant_is_left_on_the_prune_path():
    from ears.listen import missed
    assert not hasattr(missed, "KEEP_S")
    import inspect
    assert "keep_s" not in inspect.signature(MissedReply).parameters
    assert "days" in inspect.signature(prune).parameters
    assert inspect.signature(prune).parameters["days"].default is inspect.Parameter.empty


# -- one event line, errors never stop the capture ----------------------------------------


def test_one_event_per_clip_names_the_file_and_the_window_max_probability(tmp_path):
    class Probs:
        """The energy call, and a silero-like per-frame max: 0.4 in the window's 3rd frame."""

        def __init__(self):
            self.inner, self.n, self.frame_prob_max = energy_detector().classifier, 0, None

        def __call__(self, frame):
            self.n += 1
            self.frame_prob_max = 0.4 if self.n == 23 else 0.05
            return self.inner(frame)

    det = energy_detector()
    det.classifier = Probs()
    _, lines, clips = run(tmp_path, quiet(3.0), {20: {}}, window_s=1.0, detector=det)
    [ev] = events(lines, heard.EV_MISSED_REPLY)
    assert ev["speech_prob_max"] == 0.4 and Path(ev["file"]).parent == clips


def test_silero_frame_prob_max_is_the_max_of_that_frames_windows_only():
    probs = iter([0.3, 0.8, 0.1])

    class Sess:
        def run(self, _o, feeds):
            return np.array([[next(probs)]], dtype=np.float32), feeds["state"]

    c = SileroClassifier(session=Sess())
    c(np.zeros(1024, dtype=np.int16))  # two windows in one frame
    assert c.frame_prob_max == pytest.approx(0.8)
    c(np.zeros(100, dtype=np.int16))   # no window: None, not the last frame's
    assert c.frame_prob_max is None
    c(np.zeros(500, dtype=np.int16))
    assert c.frame_prob_max == pytest.approx(0.1)
    assert c.take_prob_max() == pytest.approx(0.8)  # the heartbeat's own max is untouched


def test_a_broken_said_reader_is_said_once_and_the_capture_goes_on(tmp_path):
    def boom():
        raise OSError("disk gone")

    logs = tmp_path / "logs"
    logs.mkdir()
    audio = np.concatenate([quiet(1.0), tone(1.0), quiet(3.0, seed=8)])
    source = CountingSource(audio)
    mr = MissedReply(boom, tmp_path / "c", window_s=1.0, home=tmp_path / "home", switch=ON)
    result = capture(source, FakeSTT(), RecordingSink(logs, source), detector=energy_detector(),
                     stt_workers=0, heartbeat=0, missed_reply=mr)
    lines = read_lines(logs)
    assert result.reason == "eof" and [ln["text"] for ln in lines if ln["kind"] == "turn"] == ["w1"]
    [err] = events(lines, "missed-reply-error")
    assert "disk gone" in err["detail"]


def test_a_clip_that_cannot_be_written_never_stops_the_capture(tmp_path):
    blocker = tmp_path / "vm" / "state"
    blocker.parent.mkdir(parents=True)
    blocker.write_text("a file where the dir should be")
    audio = np.concatenate([quiet(1.0), tone(1.0), quiet(3.0, seed=8)])
    result, lines, _ = run(tmp_path, audio, {5: {}, 140: {}}, window_s=0.5)
    assert result.reason == "eof"
    assert [ln["text"] for ln in lines if ln["kind"] == "turn"] == ["w1"]
    assert len(events(lines, "missed-reply-error")) == 1  # two windows failed, said once
    assert events(lines, heard.EV_MISSED_REPLY) == []


def test_a_failed_start_turns_clips_off_and_the_capture_goes_on(tmp_path, monkeypatch):
    from ears.listen import missed

    def bad(*_a, **_k):
        raise PermissionError("no")

    monkeypatch.setattr(missed, "synced_reason", bad)
    result, lines, clips = run(tmp_path, quiet(2.0), {20: {}}, window_s=1.0)
    assert result.reason == "eof"
    assert len(events(lines, "missed-reply-error")) == 1
    assert events(lines, heard.EV_MISSED_REPLY) == [] and not clips.exists()


# -- the tail, the CLI ---------------------------------------------------------------


def test_said_tail_starts_at_the_end_reads_whole_lines_and_follows_the_day(tmp_path):
    a, b = tmp_path / "heard_a.jsonl", tmp_path / "heard_b.jsonl"
    a.write_text('{"seq": 1, "kind": "said", "utt": "old"}\n')
    cur = [a]
    tail = SaidTail(lambda: cur[0])
    assert tail() == []
    with open(a, "a") as f:
        f.write('{"seq": 2, "kind": "event", "event": "heartbeat"}\n{"seq": 3, "kind": "said", "utt": "x"}\n'
                'not json "said"\n{"seq": 4, "kind": "said", "utt": "half')
    assert [r["utt"] for r in tail()] == ["x"]
    with open(a, "a") as f:
        f.write('"}\n')
    assert [r["utt"] for r in tail()] == ["half"]
    b.write_text('{"seq": 5, "kind": "said", "utt": "new day"}\n')
    cur[0] = b
    assert [r["utt"] for r in tail()] == ["new day"]


def test_cli_window_is_opt_in_and_0_is_off(tmp_path):
    # Round 3 (FOREMAN): OPT-IN. The flag turns clips on; nothing else does.
    p = build_parser()
    args = p.parse_args(["capture", "--log-dir", str(tmp_path)])
    assert args.source == "mic" and args.missed_reply_window == 0
    assert _missed_reply(args) is None
    mr = _missed_reply(p.parse_args(["capture", "--log-dir", str(tmp_path),
                                     "--missed-reply-window", str(DEFAULT_WINDOW_S)]))
    assert isinstance(mr, MissedReply) and mr.window_s == 30.0 and mr.directory is None
    assert _missed_reply(p.parse_args(["capture", "--missed-reply-window", "0"])) is None
    d = _missed_reply(p.parse_args(["capture", "--missed-reply-window", "5",
                                    "--missed-reply-dir", str(tmp_path / "c")])).directory
    assert d == tmp_path / "c"


@pytest.mark.parametrize("source", ["mic", "call", "call:2", "file:x.wav"])
def test_cli_keeps_no_clips_on_any_source_unless_asked(source):
    # The clip is for his mic audio; a default that is on leaked through call:N and
    # call-tap's far-end mic leg. Off by default everywhere; the flag is the one way in.
    p = build_parser()
    assert _missed_reply(p.parse_args(["capture", "--source", source])) is None
    mr = _missed_reply(p.parse_args(["capture", "--source", source, "--missed-reply-window", "12"]))
    assert isinstance(mr, MissedReply) and mr.window_s == 12.0


def test_window_must_be_positive():
    with pytest.raises(ValueError):
        MissedReply(list, window_s=0)


def _said_ago(seconds: float) -> dict:
    from datetime import datetime, timedelta
    ts = (datetime.now().astimezone() - timedelta(seconds=seconds)).isoformat(timespec="milliseconds")
    return {"seq": 3, "kind": "said", "utt": "late", "played_s": 1.0, "ts": ts}


def test_the_pre_roll_covers_a_said_read_late_and_a_stale_one_opens_nothing(tmp_path):
    recs: list = []
    mr = MissedReply(lambda: [recs.pop()] if recs else [], tmp_path / "c", window_s=0.6,
                     home=tmp_path / "home", switch=ON)
    mr.open()
    for i in range(40):  # frame i is all i: where the clip starts can be read off it
        mr.frame(np.full(FRAME, i, dtype=np.int16), None, False)
    recs.append(_said_ago(0.3))  # the line ended 0.3 s (10 frames) before this read
    mr.poll(40 * FRAME_S)
    out = []
    for i in range(40, 60):
        out += mr.frame(np.full(FRAME, i, dtype=np.int16), None, False)
    [(_, ev)] = out
    got = wav_samples(Path(ev["file"]))
    assert got[0] in (29, 30, 31) and len(got) == 20 * FRAME  # ~10 frames back, one window long
    assert ev["lag_s"] == pytest.approx(0.3, abs=0.05)

    recs.append(_said_ago(5.0))  # older than the window: not the window it names
    mr.poll(60 * FRAME_S)
    assert not mr.pending
