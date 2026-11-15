"""EARS-441: a missing ffmpeg leaves voices unlevelled, and the mouth says so.

With no ffmpeg no voice can be measured (``level.lufs`` is None, the same None
as too little audio), so every unmeasured voice plays unlevelled. The mouth
writes ONE ``kind: warn`` row on player.log per process, on the first line
that wants levelling; that line's ``level`` record carries ``ffmpeg: false``;
``mouth status`` has ``level.ffmpeg``. The line still plays. Fakes only:
``level.shutil.which`` is patched, the host's ffmpeg is never touched.
"""
from __future__ import annotations

import json
import threading

import numpy as np
import pytest
from test_mouth_normalize import SR, TONE, hand, host, mdir, rig  # noqa: F401  (fixtures)

import mouth
from mouth import level, player


@pytest.fixture
def no_ffmpeg(monkeypatch):
    monkeypatch.setattr(level.shutil, "which", lambda name, *a, **k: None)


@pytest.fixture
def warns(monkeypatch):
    """Every row the player writes on player.log, kept."""
    rows = []
    monkeypatch.setattr(player, "_log", rows.append)
    return lambda: [r for r in rows if r.get("warn") == "no-ffmpeg"]


def test_have_ffmpeg_is_the_same_lookup_lufs_makes(monkeypatch):
    monkeypatch.setattr(level.shutil, "which", lambda name, *a, **k: None)
    assert level.have_ffmpeg() is False
    assert level.lufs(b"\0\0" * SR, SR) is None            # its None contract is unchanged
    monkeypatch.setattr(level.shutil, "which", lambda name, *a, **k: "/fake/ffmpeg")
    assert level.have_ffmpeg() is True


def test_the_notice_goes_out_once_across_many_lines(rig, mdir, no_ffmpeg, warns, monkeypatch):
    started = []
    monkeypatch.setattr(level.threading, "Thread", lambda *a, **k: started.append(k) or _NoThread())
    for voice in ("af_new", "af_new", "af_other"):
        out, rec = rig.play(voice=voice)
        level.after_kept({"voice": voice}, "kokoro", rec["level"], d=mdir)
    assert warns() == [level.NO_FFMPEG]
    assert "ffmpeg" in warns()[0]["detail"] and "levelling is off" in warns()[0]["detail"]


def test_once_holds_across_for_line_and_after_kept_and_threads(mdir, no_ffmpeg):
    """for_line on many threads at once, and the measure after_kept starts:
    one row, by whichever line came first."""
    got = []
    go = threading.Barrier(8)

    def line():
        go.wait()
        _, rec = level.for_line({"voice": "af_new"}, "kokoro", SR, d=mdir, log=got.append)
        level.after_kept({"voice": "af_new"}, "kokoro", rec, d=mdir)

    ts = [threading.Thread(target=line) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    for t in threading.enumerate():                         # the measures after_kept started
        if t.name == "mouth-level-measure":
            t.join(5)
    assert got == [level.NO_FFMPEG]


def test_the_line_still_plays_unlevelled_and_its_record_says_no_ffmpeg(rig, mdir, no_ffmpeg, warns):
    out, rec = rig.play(voice="af_new")
    assert out.audio.tobytes() == TONE.tobytes()            # played, as today, byte for byte
    assert rec["level"] == {"on": True, "on_from": "builtin", "lufs": -16.0, "gain_db": None,
                            "from": "unmeasured", "ffmpeg": False}
    lev, r = level.for_line({"voice": "af_new"}, "kokoro", SR, d=mdir)
    assert lev is None and r["ffmpeg"] is False and r["from"] == "unmeasured"


def test_normalize_off_says_nothing(rig, mdir, no_ffmpeg, warns):
    out, rec = rig.play(voice="af_new", normalize=False, normalize_from="flag")
    host(mdir, normalize=False)
    out2, rec2 = rig.play(voice="af_new")
    assert warns() == []
    assert "ffmpeg" not in rec["level"] and "ffmpeg" not in rec2["level"]
    assert out.audio.tobytes() == out2.audio.tobytes() == TONE.tobytes()


def test_a_voice_with_a_gain_needs_no_ffmpeg_and_says_nothing(rig, mdir, no_ffmpeg, warns):
    hand(mdir, "af_test", -6)
    out, rec = rig.play()
    assert np.allclose(out.audio, TONE * 10 ** (-6 / 20), atol=1e-6)
    assert rec["level"] == {"on": True, "on_from": "builtin", "lufs": -16.0, "gain_db": -6.0,
                            "from": "hand", "limited": 0}
    assert warns() == []


@pytest.mark.parametrize("text", [json.dumps({"gain_from": "hand"}), "{not json"])
def test_a_hand_file_with_no_gain_stays_hand_and_says_nothing(rig, mdir, no_ffmpeg, warns, text):
    p = mdir / "voices" / "af_test" / "settings.json"
    p.parent.mkdir(parents=True)
    p.write_text(text)
    _, rec = rig.play()
    assert rec["level"]["from"] == "hand" and "ffmpeg" not in rec["level"]
    assert warns() == []


def test_with_ffmpeg_present_nothing_changes(rig, mdir, warns, monkeypatch):
    """ffmpeg 'present' (a fake path, lufs faked): no notice, no ffmpeg key, and
    the voice is measured and levelled as before."""
    monkeypatch.setattr(level.shutil, "which", lambda name, *a, **k: "/fake/ffmpeg")
    monkeypatch.setattr(level, "measure", lambda *a, **k: (-22.0, 10.0))
    out, rec = rig.play(voice="af_new")
    assert rec["level"] == {"on": True, "on_from": "builtin", "lufs": -16.0, "gain_db": 6.0,
                            "from": "measured", "limited": rec["level"]["limited"]}
    assert warns() == []
    monkeypatch.setattr(level, "measure", lambda *a, **k: None)   # too little audio: still no notice
    _, rec = rig.play(voice="af_short")
    assert rec["level"]["from"] == "unmeasured" and "ffmpeg" not in rec["level"]
    assert warns() == []


def test_with_no_log_given_it_is_one_json_line_on_stdout(mdir, no_ffmpeg, capsys):
    for _ in range(3):
        level.for_line({"voice": "af_new"}, "kokoro", SR, d=mdir)
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.strip()]
    assert lines == [level.NO_FFMPEG]


def test_a_log_that_raises_never_stops_the_line(mdir, no_ffmpeg):
    def boom(rec):
        raise OSError("player.log is gone")

    lev, rec = level.for_line({"voice": "af_new"}, "kokoro", SR, d=mdir, log=boom)
    assert lev is None and rec["ffmpeg"] is False


def test_forget_tries_lets_the_notice_go_out_again(mdir, no_ffmpeg):
    got = []
    level.for_line({"voice": "af_new"}, "kokoro", SR, d=mdir, log=got.append)
    level.forget_tries()
    level.for_line({"voice": "af_new"}, "kokoro", SR, d=mdir, log=got.append)
    assert got == [level.NO_FFMPEG, level.NO_FFMPEG]


def test_mouth_status_says_whether_levelling_can_measure(mdir, monkeypatch):
    monkeypatch.setattr(level.shutil, "which", lambda name, *a, **k: None)
    assert mouth.status(mdir)["level"] == {"ffmpeg": False}
    monkeypatch.setattr(level.shutil, "which", lambda name, *a, **k: "/fake/ffmpeg")
    assert mouth.status(mdir)["level"] == {"ffmpeg": True}


class _NoThread:
    def start(self):
        pass
