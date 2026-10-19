"""A say-again replay is a speech line (EARS-22, design.md S1).

``mouth.again(utt)`` queues a kept line's WAV again. It is NOT ``mouth play``:
every played file is a bed, which his voice never cuts and lines mix over. The
replay names its line (``again_of``), so it stays in the speech lane, is
levelled as its voice is, takes ``volume`` (not ``play_volume``), gets no duck
and holds a line's lead.

No sound, no ffmpeg, no server: the ``null`` device, a fake decoder standing
in for ``backends.FileSound``, and a fenced HOME and mouth dir.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import mouth
from mouth import audio, backends, bed, level, notice, player, voices
from mouth import box as _box
from tests import env_fence

SR = 24000
TEXT = "The build is green, and the second option is the safer one."


class Decoded:
    """Stands in for ``backends.FileSound`` (ffmpeg): the file's audio, as blocks."""

    name, sample_rate = "file", SR
    opened: list = []

    def __init__(self, path, start=None, end=None):
        Decoded.opened.append(path)

    def stream(self, text, voice, speed):
        for _ in range(3):
            yield np.full(1200, 0.25, dtype=np.float32)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    d, logs, aud = tmp_path / "mouth", tmp_path / "logs", tmp_path / "audio"
    monkeypatch.setenv("MOUTH_DIR", str(d))
    monkeypatch.setenv("MOUTH_LOG_DIR", str(logs))
    monkeypatch.setenv("MOUTH_AUDIO_DIR", str(aud))
    monkeypatch.setenv("MOUTH_DEVICE", "null")
    monkeypatch.setenv("MOUTH_SETTINGS", str(tmp_path / "mouth.json"))
    monkeypatch.setenv("MOUTH_SESSION_BIN", "")
    monkeypatch.setenv("VOICEMODE_VOICES_DIR", str(tmp_path / "voices"))
    monkeypatch.setenv("VOICEMODE_AGENT", "keeper")          # the caller is the keeper,
    monkeypatch.setenv("VOICEMODE_SESSION_ID", "s-keeper")   # never the line's seat
    env_fence.delenv(monkeypatch, "MOUTH_KEEP_AUDIO")
    env_fence.delenv(monkeypatch, "MOUTH_BACKEND")
    monkeypatch.setitem(audio._last_prune, "t", 0.0)
    level.forget_tries()
    Decoded.opened = []
    return SimpleNamespace(d=d, logs=logs, audio=aud, settings=tmp_path / "mouth.json")


def keep_line(root: Path, utt: str = "3f9c1a2b7e40", day: str = "2030-10-16", *,
              text: str = TEXT, make_wav: bool = True, **meta) -> Path:
    """A kept line as audio.keep leaves it: <day>/<utt>.wav and <utt>.json."""
    folder = root / day
    folder.mkdir(parents=True, exist_ok=True)
    w = folder / f"{utt}.wav"
    if make_wav:
        audio.write_wav(w, audio.pcm16([np.full(2400, 0.25, dtype=np.float32)]), SR)
    note = {"utt": utt, "voice": "af_sky", "backend": "kokoro", "speed": None, "agent": "ivy",
            "session": "s-ivy", "text": text, "sample_rate": SR, "dur_s": 0.1, "wav": str(w), **meta}
    (folder / f"{utt}.json").write_text(json.dumps(note))
    return w


def queued(d: Path) -> list[dict]:
    return [_box.public(x) for x in _box.Queue(d).lines()]


def rows(logs: Path) -> list[dict]:
    return [json.loads(x) for f in sorted(logs.glob("heard_*.jsonl")) for x in f.read_text().splitlines()]


# -- audio.find -------------------------------------------------------------

def test_find_follows_the_day_folders_newest_first(rig):
    keep_line(rig.audio, "aaa111", "2030-10-14", text="older")
    keep_line(rig.audio, "aaa111", "2030-10-16", text="newer")
    keep_line(rig.audio, "bbb222", "2030-10-14", text="only the old day has it")
    assert audio.find("aaa111")["text"] == "newer"
    got = audio.find("bbb222")
    assert got["text"] == "only the old day has it"
    assert got["wav"] == str(rig.audio / "2030-10-14" / "bbb222.wav") and got["wav_exists"]


def test_find_says_none_when_nothing_is_kept(rig):
    assert audio.find("abc123") is None             # no audio dir at all
    keep_line(rig.audio, "other1")
    assert audio.find("abc123") is None             # a dir, but not this line


@pytest.mark.parametrize("bad", ["", "../etc/passwd", "a/b", "abc.wav", "x" * 33, "abc 123", "*"])
def test_find_refuses_anything_that_is_not_a_plain_utt(rig, bad):
    keep_line(rig.audio)
    with pytest.raises(ValueError, match="not a line's utt"):
        audio.find(bad)


def test_find_reads_the_wav_beside_the_note_never_the_path_the_note_names(rig, tmp_path):
    elsewhere = tmp_path / "secret.wav"
    elsewhere.write_bytes(b"RIFF")
    keep_line(rig.audio, "abc123", make_wav=False, wav=str(elsewhere))
    got = audio.find("abc123")
    assert got["wav"] == str(rig.audio / "2030-10-16" / "abc123.wav")
    assert got["wav_exists"] is False


# -- mouth.again: the queued item ---------------------------------------------

def test_again_queues_the_original_as_a_held_next_line(rig):
    wav = keep_line(rig.audio)
    item = mouth.again("3f9c1a2b7e40", seq=161204, heard_text="say again", volume=40,
                       d=rig.d, spawn=False)
    [x] = queued(rig.d)
    for got in (item, x):
        assert got["text"] == TEXT
        assert got["file"] == str(wav) and got["again_of"] == "3f9c1a2b7e40"
        assert got["hold"] == "turn-end" and got["priority"] == "next"
        assert got["expires_s"] == mouth.AGAIN_EXPIRES_S == 60.0
        assert (got["agent"], got["session"], got["voice"]) == ("ivy", "s-ivy", "af_sky")
        assert (got["again_seq"], got["again_heard"]) == (161204, "say again")
        assert (got["volume"], got["volume_from"]) == (40, "flag")
        assert got["backend_asked"] == "kokoro"       # the backend that made the WAV
        assert got["utt"] != "3f9c1a2b7e40"          # a new line, naming the old one
        assert "notice_to" not in got                 # its note goes to the seat, never a sender


def test_again_reads_no_cascade_its_caller_is_the_keeper(rig, monkeypatch):
    keep_line(rig.audio)
    called = []
    monkeypatch.setattr(voices, "cascade", lambda *a, **k: called.append(a) or {})
    mouth.again("3f9c1a2b7e40", d=rig.d, spawn=False)
    assert called == []


def test_again_with_no_level_given_takes_a_lines_volume_never_play_volume(rig):
    rig.settings.write_text(json.dumps({"volume": 70, "play_volume": 20}))
    keep_line(rig.audio)
    item = mouth.again("3f9c1a2b7e40", d=rig.d, spawn=False)
    assert (item["volume"], item["volume_from"]) == (70, "settings")


def test_a_note_with_no_text_and_none_given_raises_naming_why(rig):
    keep_line(rig.audio, text="  ")
    with pytest.raises(mouth.NoAudio, match="its note has no text"):
        mouth.again("3f9c1a2b7e40", d=rig.d, spawn=False)
    assert queued(rig.d) == []


def test_a_note_with_no_text_plays_its_wav_under_the_text_given(rig):
    wav = keep_line(rig.audio, text="")
    item = mouth.again("3f9c1a2b7e40", text="the words off the saying row", d=rig.d, spawn=False)
    assert (item["file"], item["text"], item["again_from"]) == (str(wav), "the words off the saying row", "audio")


def test_a_bad_utt_is_refused_before_anything_is_queued(rig):
    with pytest.raises(ValueError, match="not a line's utt"):
        mouth.again("../../x", d=rig.d, spawn=False)
    assert not rig.d.exists() or queued(rig.d) == []


# -- one predicate: a played file is `file` without `again_of` -----------------

PLAYED = {"utt": "p1", "text": "[sound x.wav]", "backend": "file", "file": "/s/x.wav"}
REPLAY = {"utt": "r1", "text": TEXT, "backend": "kokoro", "file": "/a/r.wav", "again_of": "3f9c1a2b7e40"}


def test_the_predicate_is_one_and_bed_asks_it():
    assert voices.played_file(PLAYED) and bed.is_file(PLAYED)
    assert voices.played_file({"utt": "p2", "file": "/s/y.wav"})
    assert not voices.played_file(REPLAY) and not bed.is_file(REPLAY)
    assert not voices.played_file({**REPLAY, "backend": "file"})   # the decoder's name, after take
    assert not voices.played_file({"utt": "l1", "text": "hi", "backend": "kokoro"})


def test_a_replay_is_not_kept_again():
    assert audio.wanted(REPLAY, "kokoro") is False
    assert audio.wanted(REPLAY, "file") is False


def test_a_replay_takes_volume_and_no_duck_where_a_file_takes_play_volume_and_a_duck(rig):
    rig.settings.write_text(json.dumps({"volume": 70, "play_volume": 20}))
    r = voices.fill_item(dict(REPLAY), rig.d)
    f = voices.fill_item(dict(PLAYED), rig.d)
    assert (r["volume"], r["volume_from"]) == (70, "settings") and "duck" not in r
    assert f["volume"] == 20 and isinstance(f["duck"], dict)


def test_a_replay_holds_a_lines_lead_where_a_file_never_holds(rig):
    rig.settings.write_text(json.dumps({"lead_s": 1.5}))
    assert voices.lead_setting(item=dict(REPLAY), d=rig.d) == (1.5, "settings")
    assert voices.lead_setting(item=dict(PLAYED), d=rig.d) == (0.0, "file")


def write_vs(d: Path, voice: str, body: dict) -> None:
    p = d / "voices" / voice / "settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(body))


def test_a_replay_is_levelled_as_its_voice_under_the_backend_that_made_it(rig, monkeypatch):
    write_vs(rig.d, "af_sky", {"gain_from": "hand", "gain_db": 6})
    line = {"utt": "l1", "text": TEXT, "voice": "af_sky", "backend": "kokoro"}
    # what play_one hands for_line: the decoder's name, the asked backend kept
    replay = {**REPLAY, "voice": "af_sky", "backend": "file", "backend_asked": "kokoro"}
    want = level.for_line(line, "kokoro", SR, d=rig.d)
    got = level.for_line(replay, "file", SR, d=rig.d)
    assert want[1] is not None and got[1] == want[1] and got[0].gain_db == want[0].gain_db == 6.0
    assert level.for_line({**PLAYED, "voice": "af_sky"}, "file", SR, d=rig.d) == (None, None)
    measured = []
    monkeypatch.setattr(level, "measure", lambda voice, backend, root=None: measured.append(backend))
    write_vs(rig.d, "af_new", {})
    level.for_line({**replay, "voice": "af_new"}, "file", SR, d=rig.d)
    assert measured == ["kokoro"]                 # never measured as the `file` decoder


def test_a_replay_of_a_silence_line_is_not_levelled(rig):
    write_vs(rig.d, "af_sky", {"gain_from": "hand", "gain_db": 6})
    replay = {**REPLAY, "voice": "af_sky", "backend": "file", "backend_asked": "silence"}
    assert level.for_line(replay, "file", SR, d=rig.d) == (None, None)


# -- barge-in: a cut replay is not resumed, and says why ----------------------

def test_a_replay_cut_by_barge_in_plans_wont_with_the_again_reason(monkeypatch):
    monkeypatch.setattr(player._hold, "resume_s", lambda: 20.0)
    monkeypatch.setattr(player._hold, "asked_for_quiet", lambda *a, **k: False)
    cut = {"reason": "barge-in", "cut": True, "played_s": 1.2, "text_played_est": "The build"}
    assert player._plan(dict(REPLAY), cut) == {"resume": "won't",
                                               "resume_why": player.AGAIN_NOT_RESUMED}
    assert player.AGAIN_NOT_RESUMED == "a say-again replay is not resumed"
    assert player._plan(dict(PLAYED), cut)["resume_why"] == "it is a played file"
    line = {"utt": "l1", "text": TEXT, "backend": "kokoro"}
    assert player._plan(line, cut)["resume"] == "will"   # a plain line still resumes


# -- through the player: the speech lane, the saying row ----------------------

def test_a_kept_line_replays_through_the_speech_lane(rig, monkeypatch):
    wav = keep_line(rig.audio)
    monkeypatch.setattr(backends, "FileSound", Decoded)
    monkeypatch.setattr(player._hold, "check", lambda x: "play")
    started = []
    monkeypatch.setattr(player._bed.Beds, "start", lambda self, item, taken: started.append(item))
    item = mouth.again("3f9c1a2b7e40", volume=40, d=rig.d, spawn=False)
    t = threading.Thread(target=player.serve, kwargs={"d": rig.d, "idle_exit_s": 0.3}, daemon=True)
    t.start()
    t.join(10)
    assert not t.is_alive()
    assert started == []                          # never handed to the bed lane
    assert Decoded.opened == [str(wav)]           # the speech lane decoded the kept WAV
    saying = [r for r in rows(rig.logs) if r["kind"] == "saying"]
    said = [r for r in rows(rig.logs) if r["kind"] == "said"]
    assert [r["utt"] for r in saying] == [item["utt"]] == [r["utt"] for r in said]
    for r in (saying[0], said[0]):
        assert r["again_of"] == "3f9c1a2b7e40" and r["file"] == str(wav)
        assert (r["agent"], r["session"], r["volume"]) == ("ivy", "s-ivy", 40)
        assert (r["backend"], r["backend_asked"]) == ("file", "kokoro")
        assert "lane" not in r
    assert saying[0]["text"] == TEXT              # the real words, for the echo trim
    assert said[0]["reason"] == "done" and not said[0].get("audio")   # played, not kept again
    assert sorted(p.name for p in (rig.audio / "2030-10-16").iterdir()) == \
        ["3f9c1a2b7e40.json", "3f9c1a2b7e40.wav"]
    assert len([p for p in rig.audio.rglob("*.json")]) == 1


def test_through_the_player_a_replay_is_levelled_under_the_backend_that_made_it(rig, monkeypatch):
    """The real path, not a hand-built item: the box makes any ``file`` line the
    ``file`` backend, so the replay must carry the original's as ``backend_asked``
    (review fix). An unmeasured voice is measured under it, never under ``file``."""
    keep_line(rig.audio)
    write_vs(rig.d, "af_sky", {})                 # unmeasured: for_line measures it
    monkeypatch.setattr(backends, "FileSound", Decoded)
    monkeypatch.setattr(player._hold, "check", lambda x: "play")
    measured = []
    monkeypatch.setattr(level, "measure", lambda voice, backend, root=None: measured.append(backend))
    mouth.again("3f9c1a2b7e40", d=rig.d, spawn=False)
    [x] = queued(rig.d)
    assert (x["backend"], x["backend_asked"]) == ("file", "kokoro")
    t = threading.Thread(target=player.serve, kwargs={"d": rig.d, "idle_exit_s": 0.3}, daemon=True)
    t.start()
    t.join(10)
    assert not t.is_alive()
    assert measured == ["kokoro"]
    [saying] = [r for r in rows(rig.logs) if r["kind"] == "saying"]
    assert saying["level"]["from"] == "unmeasured"   # levelled as its voice is: not None, as a file's


def test_through_the_player_a_replay_gets_its_voices_gain(rig, monkeypatch):
    keep_line(rig.audio)
    write_vs(rig.d, "af_sky", {"gain_from": "hand", "gain_db": 6})
    monkeypatch.setattr(backends, "FileSound", Decoded)
    monkeypatch.setattr(player._hold, "check", lambda x: "play")
    mouth.again("3f9c1a2b7e40", d=rig.d, spawn=False)
    t = threading.Thread(target=player.serve, kwargs={"d": rig.d, "idle_exit_s": 0.3}, daemon=True)
    t.start()
    t.join(10)
    [saying] = [r for r in rows(rig.logs) if r["kind"] == "saying"]
    assert (saying["level"]["gain_db"], saying["level"]["from"]) == (6.0, "hand")



# == S2: the `mouth again` verb, the text fallback and the resume handover ========

UTT = "3f9c1a2b7e40"


def test_no_wav_re_synthesises_the_notes_text_in_its_voice(rig):
    """Case 1, rung 2: the WAV is gone (pruned, keep-audio off, cut before its
    synthesis finished), so the note's text is said again: still no model turn."""
    keep_line(rig.audio, make_wav=False, voice="bf_emma", speed=1.1)
    item = mouth.again(UTT, seq=7, d=rig.d, spawn=False)
    [x] = queued(rig.d)
    for got in (item, x):
        assert "file" not in got or got["file"] is None            # a TTS line, not a played file
        assert (got["text"], got["voice"], got["speed"]) == (TEXT, "bf_emma", 1.1)
        assert (got["backend"], got["agent"], got["session"]) == ("kokoro", "ivy", "s-ivy")
        assert (got["again_of"], got["again_from"], got["again_seq"]) == (UTT, "text", 7)
        assert got["again_why"].startswith("its WAV is gone")
        assert (got["hold"], got["priority"]) == ("turn-end", "next")
    assert not voices.played_file(x) and not bed.is_file(x)


def test_no_note_says_the_text_the_keeper_passed(rig):
    item = mouth.again(UTT, text="  The build is green.  ", voice="am_adam", agent="vera",
                       session="s-vera", d=rig.d, spawn=False)
    [x] = queued(rig.d)
    for got in (item, x):
        assert (got["text"], got["voice"], got["agent"], got["session"]) == \
            ("The build is green.", "am_adam", "vera", "s-vera")
        assert (got["again_of"], got["again_from"]) == (UTT, "text")
        assert got["again_why"] == "no kept line has that utt"


def test_the_notes_text_beats_the_text_passed(rig):
    keep_line(rig.audio, make_wav=False)
    item = mouth.again(UTT, text="what the keeper read", voice="am_adam", d=rig.d, spawn=False)
    assert (item["text"], item["voice"]) == (TEXT, "af_sky")


def test_neither_audio_nor_text_raises_no_audio(rig):
    with pytest.raises(mouth.NoAudio, match=f"no audio for {UTT}: no kept line has that utt, "
                                           "and no text to say instead"):
        mouth.again(UTT, text="   ", d=rig.d, spawn=False)
    assert queued(rig.d) == []


def test_a_re_synthesised_replay_is_levelled_under_the_backend_that_ran(rig, monkeypatch):
    """Only a replay of the kept WAV is measured under ``backend_asked``; a TTS
    replay's ``backend_asked`` is what it asked (``auto``), not what made it."""
    measured = []
    monkeypatch.setattr(level, "measure", lambda voice, backend, root=None: measured.append(backend))
    write_vs(rig.d, "af_new", {})
    tts = {"utt": "t1", "text": TEXT, "voice": "af_new", "again_of": UTT,
           "backend": "kokoro", "backend_asked": "auto"}
    level.for_line(tts, "kokoro", SR, d=rig.d)
    assert measured == ["kokoro"]


# -- the resume handover (cases 4 and 5) -------------------------------------------

def queue_resume(rig, of: str = UTT, utt: str = "r0e5u1e00001") -> dict:
    """A resume as the player queues one (player._resume): held, naming the cut line."""
    return mouth.say("and the second option is the safer one.", voice="af_sky", d=rig.d,
                     spawn=False, hold="turn-end", expires_s=20,
                     extra={"agent": "ivy", "session": "s-ivy", "utt": utt, "resume_of": of})


def test_a_queued_resume_of_the_line_is_filed_t_with_reason_again_and_a_quiet_notice(rig):
    keep_line(rig.audio)
    queue_resume(rig)
    queue_resume(rig, of="0ther1ine000", utt="r0e5u1e00002")      # another line's resume
    item = mouth.again(UTT, d=rig.d, spawn=False)
    assert item["replaced"] == [{"utt": "r0e5u1e00001", "as": "retracted"}]
    left = sorted(x["utt"] for x in queued(rig.d))
    assert left == sorted([item["utt"], "r0e5u1e00002"])           # the other resume stays
    [row] = [r for r in rows(rig.logs) if r["kind"] == "said"]
    assert (row["utt"], row["reason"], row["resume_of"]) == ("r0e5u1e00001", "again", UTT)
    assert row["notice"] == notice.AGAIN_REPLACED == "none: replaced by a say-again"
    assert not (rig.d / "stop").exists()


def test_a_playing_resume_of_the_line_gets_a_stop_aimed_at_its_utt_only(rig):
    keep_line(rig.audio)
    rig.d.mkdir(parents=True, exist_ok=True)
    (rig.d / "playing.json").write_text(json.dumps({"utt": "r0e5u1e00001", "resume_of": UTT,
                                                    "text": "and the rest"}))
    item = mouth.again(UTT, d=rig.d, spawn=False)
    assert item["replaced"] == [{"utt": "r0e5u1e00001", "as": "stopped"}]
    stop = json.loads((rig.d / "stop").read_text())
    assert (stop["reason"], stop["utt"], stop["flush"]) == ("again", "r0e5u1e00001", False)


@pytest.mark.parametrize("playing", [
    {"utt": "l1n3o7h3r000", "text": "another seat's line"},                     # a plain line
    {"utt": "r0e5u1e00009", "resume_of": "0ther1ine000", "text": "another's rest"},
], ids=["another line", "another line's resume"])
def test_a_playing_line_of_another_utt_is_never_cut(rig, playing):
    keep_line(rig.audio)
    rig.d.mkdir(parents=True, exist_ok=True)
    (rig.d / "playing.json").write_text(json.dumps(playing))
    item = mouth.again(UTT, d=rig.d, spawn=False)
    assert item["replaced"] == [] and not (rig.d / "stop").exists()


def test_a_replay_that_cannot_be_queued_leaves_the_resume_alone(rig, monkeypatch):
    keep_line(rig.audio)
    queue_resume(rig)
    env_fence.delenv(monkeypatch, "MOUTH_DEVICE")
    with pytest.raises(ValueError, match="no device"):
        mouth.again(UTT, d=rig.d, spawn=False)
    assert [x["utt"] for x in queued(rig.d)] == ["r0e5u1e00001"]
    assert not [r for r in rows(rig.logs) if r["kind"] == "said"]


def test_a_finished_resume_changes_nothing_the_whole_line_plays(rig):
    """Case 6: the resume played out; there is nothing to replace."""
    keep_line(rig.audio)
    item = mouth.again(UTT, d=rig.d, spawn=False)
    assert item["replaced"] == [] and [x["utt"] for x in queued(rig.d)] == [item["utt"]]


def test_the_notice_for_a_replaced_resume_is_quiet():
    item = {"utt": "r1", "text": "the rest", "resume_of": UTT, "session": "s-ivy", "agent": "ivy"}
    assert notice._decide(item, {"reason": "again", "cut": True}) == (None, notice.AGAIN_REPLACED)


def test_a_cut_with_reason_again_is_never_resumed(rig, monkeypatch):
    """The player resumes only a barge-in cut; a resume a say-again stopped stays stopped."""
    resumed = []
    monkeypatch.setattr(player, "_resume", lambda *a, **k: resumed.append(a))
    rec = player._said({"utt": "r1", "text": "the rest", "resume_of": UTT, "log_dir": str(rig.logs)},
                       utt="r1", cut=True, reason="again", played_s=0.4, underruns=0)
    assert rec["notice"] == notice.AGAIN_REPLACED and "resume" not in rec


# -- the CLI: exit 0, 2 for usage, 3 for nothing to replay ---------------------------

def test_cli_again_exits_0_and_prints_what_it_queued(rig, monkeypatch, capsys):
    from mouth import __main__ as cli

    monkeypatch.setattr(mouth, "ensure_player", lambda d=None: True)   # never a live player
    keep_line(rig.audio)
    queue_resume(rig)
    assert cli.main(["again", UTT, "--seq", "161204", "--heard", "say again", "--volume", "40"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert (out["again_of"], out["again_from"]) == (UTT, "audio")
    assert out["replaced"] == [{"utt": "r0e5u1e00001", "as": "retracted"}]
    [x] = queued(rig.d)
    assert (x["utt"], x["again_seq"], x["again_heard"], x["volume"]) == (out["utt"], 161204, "say again", 40)


def test_cli_again_falls_back_to_the_text_passed(rig, monkeypatch, capsys):
    from mouth import __main__ as cli

    monkeypatch.setattr(mouth, "ensure_player", lambda d=None: True)
    assert cli.main(["again", UTT, "--text", "The build is green.", "--voice", "af_sky",
                     "--agent", "vera", "--session", "s-vera"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["again_from"] == "text" and out["again_why"] == "no kept line has that utt"
    [x] = queued(rig.d)
    assert (x["agent"], x["session"]) == ("vera", "s-vera")


def test_cli_again_exits_3_when_there_is_nothing_to_replay(rig, capsys):
    from mouth import __main__ as cli

    assert cli.main(["again", UTT]) == 3
    assert f"no audio for {UTT}" in capsys.readouterr().err
    assert not rig.d.exists() or queued(rig.d) == []


@pytest.mark.parametrize("argv", [["again", "../etc/passwd"], ["again", UTT, "--volume", "loud"]],
                         ids=["not an utt", "a bad volume"])
def test_cli_again_exits_2_for_usage(rig, capsys, argv):
    from mouth import __main__ as cli

    keep_line(rig.audio)
    assert cli.main(argv) == 2
    assert capsys.readouterr().err.strip()


def test_cli_again_with_no_utt_is_argparse_usage(rig):
    from mouth import __main__ as cli

    with pytest.raises(SystemExit) as e:
        cli.main(["again"])
    assert e.value.code == 2
