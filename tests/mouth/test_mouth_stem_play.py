"""``mouth play --stem NAME[,NAME]``: one stem of a song, or several mixed in ONE ffmpeg.

The sidecar here is made by hand (``stems/<name>.wav`` in ``.<name>.d/``): nothing
splits, nothing separates. The caller checks the names and that every stem is there;
the line carries the names only, and the player finds the files at take time.

Three decoders stand in or play for real:

- ``Rec`` (most tests): notes what each bed was handed to decode, a path or a list;
- a fake ``ffmpeg`` on PATH: notes the argv of every run, so a test can say there was
  ONE and what it was told;
- the real ffmpeg (``needs_ffmpeg``): two equal stems summed come out 6 dB above one.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import wave
from pathlib import Path

import numpy as np
import pytest
from test_mouth_bed import env, kind, run, tone  # noqa: F401  (fixture)
from test_mouth_instrumental_play import Rec, mailed, make_instrumental, said_for, song

import mouth
from mouth import __main__ as cli
from mouth import autosplit, backends, bed, box, sidecar, stems
from tests import env_fence


@pytest.fixture
def rec(env, monkeypatch):
    """``env`` with the recording decoder in for ffmpeg, and no separator."""
    Rec.paths, Rec.seconds = [], 0.3
    monkeypatch.setattr(backends, "FileSound", Rec)
    env_fence.delenv(monkeypatch, "MOUTH_MUSIC_INSTRUMENTAL")
    env_fence.delenv(monkeypatch, "MOUTH_SOUNDS_DIR")
    return env


def make_stem(src: Path, name: str, seconds=0.5, amp=0.1, folder=None, freq=220.0) -> Path:
    """SRC's stem NAME, a mono 24 kHz tone, in its dot folder (or FOLDER)."""
    out = (folder or sidecar.canonical_dir(src)) / "stems" / f"{name}.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    rate = 24000
    x = (amp * np.sin(2 * np.pi * freq * np.arange(int(seconds * rate)) / rate) * 32767).astype("<i2")
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(x.tobytes())
    return out


def queued(env) -> list:
    return [p for sub in ("new", "cur") for p in (env.mail / sub).iterdir()]


# -- the names ---------------------------------------------------------------

def test_names_take_a_comma_list_drop_duplicates_and_keep_the_order():
    assert stems.names("drums") == ["drums"]
    assert stems.names("bass, Drums,bass,,") == ["bass", "drums"]
    assert stems.names(["other", "vocals", "other"]) == ["other", "vocals"]


@pytest.mark.parametrize("bad", ["", " , ", "guitar", "drums,piano", ["drums", 3], None, 7])
def test_an_unknown_or_empty_name_is_refused_listing_the_four(bad):
    with pytest.raises(ValueError) as e:
        stems.names(bad)
    assert "vocals, drums, bass, other" in str(e.value)


def test_the_cli_refuses_an_unknown_name_and_queues_nothing(rec, capsys):
    src = song(rec)
    make_stem(src, "drums")
    assert cli.main(["play", str(src), "--stem", "drums,guitar"]) == 2
    err = capsys.readouterr().err
    assert "'guitar'" in err and "vocals, drums, bass, other" in err
    assert queued(rec) == []


# -- the exclusions --------------------------------------------------------------

@pytest.mark.parametrize("flag", ["--instrumental", "--no-instrumental"])
def test_stem_is_exclusive_with_the_instrumental_flags(rec, flag, capsys):
    src = song(rec)
    make_stem(src, "drums")
    with pytest.raises(SystemExit) as e:
        cli.main(["play", str(src), "--stem", "drums", flag])
    assert e.value.code == 2
    assert "not allowed with" in capsys.readouterr().err
    assert queued(rec) == []


@pytest.mark.parametrize("instrumental", [True, False])
def test_play_refuses_stem_with_instrumental(rec, instrumental):
    src = song(rec)
    make_stem(src, "drums")
    with pytest.raises(ValueError, match="--stem plays stems"):
        mouth.play(str(src), spawn=False, d=rec.d, stem="drums", instrumental=instrumental)
    assert queued(rec) == []


def test_stem_ignores_the_setting_and_never_splits(rec, monkeypatch):
    src = song(rec)
    make_instrumental(src)                       # an instrumental exists, and the setting is on
    drums = make_stem(src, "drums")
    calls = []
    monkeypatch.setattr(autosplit, "decide", lambda *a, **k: calls.append(a))
    rec.settings.write_text(json.dumps({"music_instrumental": True}))
    f = mouth.play(str(src), spawn=False, d=rec.d, device="null", stem="drums")
    assert "instrumental" not in f and "split_wait" not in f
    run(rec).join(8)
    assert Rec.paths == [[str(drums)]]           # the stem, never the instrumental
    assert calls == []                           # no split decided, none started
    said = said_for(rec, f["utt"])
    assert said["reason"] == "done" and said["stem"] == ["drums"]
    assert "instrumental_from" not in said


# -- the caller's refusal: a missing stem, with the ONE next command ----------------

def test_a_missing_stem_is_refused_with_the_install_when_no_separator(rec):
    src = song(rec)                               # no sidecar at all; the suite has no separator
    with pytest.raises(ValueError) as e:
        mouth.play(str(src), spawn=False, d=rec.d, stem="vocals")
    msg = str(e.value)
    assert "vocals stem" in msg and "`mouth split --install`" in msg
    assert queued(rec) == []
    assert not sidecar.canonical_dir(src).exists()   # never made silently


def test_a_missing_stem_names_mouth_split_file_when_a_separator_is_present(rec, monkeypatch):
    src = song(rec)
    make_stem(src, "drums")
    monkeypatch.setenv("MOUTH_SEPARATOR", "/nowhere/fake-separator")
    with pytest.raises(ValueError) as e:
        mouth.play(str(src), spawn=False, d=rec.d, stem="drums,bass,other")
    msg = str(e.value)
    assert "bass and other stems" in msg and "drums" not in msg.split("run")[0]
    assert f"`mouth split {src}`" in msg
    assert queued(rec) == []


def test_the_cli_exits_2_naming_the_command(rec, capsys):
    src = song(rec)
    assert cli.main(["play", str(src), "--stem", "bass"]) == 2
    err = capsys.readouterr().err
    assert "`mouth split --install`" in err
    assert queued(rec) == []


# -- the player: names, never paths ---------------------------------------------

def test_the_line_carries_the_names_and_the_player_finds_the_files(rec):
    src = song(rec)
    drums, bass = make_stem(src, "drums"), make_stem(src, "bass")
    f = mouth.play(str(src), spawn=False, d=rec.d, device="null", stem="bass,drums")
    assert f["stem"] == ["bass", "drums"] and "stem_files" not in f
    assert f["file"] == str(src)                 # what was asked for, until the player takes it
    run(rec).join(8)
    assert Rec.paths == [[str(bass), str(drums)]]   # ONE decoder, both stems, in the order asked
    saying, said = kind(rec, "saying")[f["utt"]], said_for(rec, f["utt"])
    for row in (saying, said):
        assert row["stem"] == ["bass", "drums"]
        assert row["file"] == str(bass) and row["source_file"] == str(src)
    assert saying["text"] == "[sound song.wav]"  # the label names the song
    assert said["reason"] == "done"


def test_a_stem_missing_at_play_time_is_an_error_naming_the_command(rec):
    src = song(rec)
    drums = make_stem(src, "drums")
    f = mouth.play(str(src), spawn=False, d=rec.d, device="null", stem="drums")
    drums.unlink()                                # gone between the call and the take
    run(rec).join(8)
    said = said_for(rec, f["utt"])
    assert said["reason"] == "error" and Rec.paths == []
    assert "drums stem" in said["detail"] and "`mouth split --install`" in said["detail"]


def test_volume_and_duck_ride_as_for_any_played_file(rec):
    src = song(rec)
    make_stem(src, "vocals")
    f = mouth.play(str(src), spawn=False, d=rec.d, device="null", stem="vocals",
                   volume=40, duck=False)
    run(rec).join(8)
    said = said_for(rec, f["utt"])
    assert (said["volume"], said["volume_from"]) == (40, "flag")
    assert said["reason"] == "done"


def test_mouth_status_music_row_names_the_stems(rec):
    from test_mouth_bed import wait_for

    Rec.seconds = 3.0
    src = song(rec)
    drums, bass = make_stem(src, "drums"), make_stem(src, "bass")
    f = mouth.play(str(src), spawn=False, d=rec.d, device="null", stem="drums,bass")
    th = run(rec)
    assert wait_for(lambda: bed.read(rec.d) and bed.read(rec.d)[0].get("started_t"))
    (row,) = mouth._music(rec.d)
    assert (row["stem"], row["file"], row["source_file"]) == (["drums", "bass"], str(drums), str(src))
    mouth.retract(f["utt"], d=rec.d)
    th.join(8)


# -- a mail from outside: a mailed path is ignored ----------------------------------

def test_a_mailed_path_is_ignored_and_the_names_are_found_in_the_sidecar(rec, monkeypatch):
    sounds = rec.tmp / "sounds"
    src = song(rec, folder=sounds)
    drums = make_stem(src, "drums")
    evil = rec.tmp / "elsewhere" / "secret.wav"
    evil.parent.mkdir()
    evil.write_bytes(b"not for you")
    monkeypatch.setenv("MOUTH_SOUNDS_DIR", str(sounds))
    mid = mailed(rec, src, X_Mouth_Extra=json.dumps({
        "stem": ["drums"], "stem_files": [str(evil)], "stem_error": None}))
    run(rec).join(8)
    said = said_for(rec)
    assert said["mail_id"] == mid and said["reason"] == "done"
    assert Rec.paths == [[os.path.realpath(drums)]]   # the sidecar's, never the mailed path
    assert said["source_file"] == os.path.realpath(src)


def test_mailed_stem_files_without_a_stem_are_dropped(rec, monkeypatch):
    sounds = rec.tmp / "sounds"
    src = song(rec, folder=sounds)
    evil = rec.tmp / "elsewhere" / "secret.wav"
    evil.parent.mkdir()
    evil.write_bytes(b"x")
    monkeypatch.setenv("MOUTH_SOUNDS_DIR", str(sounds))
    mailed(rec, src, X_Mouth_Extra=json.dumps({"stem_files": [str(evil)]}))
    run(rec).join(8)
    assert Rec.paths == [os.path.realpath(src)]       # the song itself
    assert said_for(rec)["reason"] == "done"


def test_a_mailed_name_that_is_a_path_is_refused(rec, monkeypatch):
    sounds = rec.tmp / "sounds"
    src = song(rec, folder=sounds)
    monkeypatch.setenv("MOUTH_SOUNDS_DIR", str(sounds))
    mailed(rec, src, X_Mouth_Extra=json.dumps({"stem": ["../../elsewhere/secret"]}))
    run(rec).join(8)
    said = said_for(rec)
    assert said["reason"] == "error" and Rec.paths == []
    assert "vocals, drums, bass, other" in said["detail"]


def test_a_mailed_stem_never_follows_a_sidecar_link_out_of_the_sounds_dir(rec, monkeypatch):
    sounds = rec.tmp / "sounds"
    src = song(rec, folder=sounds)
    outside = make_stem(src, "vocals", folder=rec.tmp / "elsewhere")
    link = sidecar.canonical_dir(src) / "stems" / "vocals.wav"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)
    monkeypatch.setenv("MOUTH_SOUNDS_DIR", str(sounds))
    mailed(rec, src, X_Mouth_Extra=json.dumps({"stem": ["vocals"]}))
    run(rec).join(8)
    said = said_for(rec)
    assert said["reason"] == "error" and Rec.paths == []
    assert "outside the sounds directory" in said["detail"]


def test_resolve_rebuilds_from_names_and_leaves_the_rest(tmp_path):
    src = tmp_path / "a.flac"
    src.write_bytes(b"x")
    drums = make_stem(src, "drums")
    item = {"utt": "u1", "backend": "file", "file": str(src), "stem": ["drums"],
            "stem_files": ["/etc/hosts"], "start": 1.0, "end": 2.0, "volume": 40}
    out = stems.resolve(item, local=True)
    assert out == {**item, "stem_files": [str(drums)], "file": str(drums), "source_file": str(src)}
    spoken = {"utt": "u2", "text": "hi", "backend": "silence"}
    assert stems.resolve(spoken, local=True) is spoken
    assert stems.resolve({**spoken, "stem_files": ["/etc/hosts"]}, local=True) == spoken


# -- the ONE ffmpeg: its argv, by a fake ffmpeg on PATH ------------------------------

FAKE_FFMPEG = """#!{py}
import json, os, sys
with open(os.environ["FAKE_FFMPEG_LOG"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
sys.stdout.buffer.write(b"\\0\\0" * 2400)   # 0.1 s of silence, the mouth's s16le mono
"""


@pytest.fixture
def fake_ffmpeg(env, monkeypatch):
    """A fake ``ffmpeg`` first on PATH; returns a function reading the argv of each run."""
    env_fence.delenv(monkeypatch, "MOUTH_MUSIC_INSTRUMENTAL")
    bindir = env.tmp / "fakebin"
    bindir.mkdir()
    exe = bindir / "ffmpeg"
    exe.write_text(FAKE_FFMPEG.format(py=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    log = env.tmp / "ffmpeg.argv"
    monkeypatch.setenv("FAKE_FFMPEG_LOG", str(log))
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    return lambda: [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []


def _inputs(argv: list) -> list:
    """Each ``-i``'s options, as (the options since the last input, the path)."""
    out, opts = [], []
    it = iter(argv)
    for a in it:
        if a == "-i":
            out.append((opts, next(it)))
            opts = []
        else:
            opts.append(a)
    return out


def test_one_ffmpeg_mixes_two_stems_with_start_and_end_on_each(env, fake_ffmpeg):
    src = tone(env, "song.wav", 0.5)
    drums, bass = make_stem(src, "drums"), make_stem(src, "bass")
    f = mouth.play(str(src), spawn=False, d=env.d, device="null", stem="drums,bass",
                   start=1.5, end=4.0)
    run(env).join(8)
    assert kind(env, "said")[f["utt"]]["reason"] == "done"
    (argv,) = fake_ffmpeg()                       # ONE ffmpeg for the whole mix
    ins = _inputs(argv)
    assert [p for _, p in ins] == [str(drums), str(bass)]
    for opts, _ in ins:                           # both are per-input: before EACH -i
        tail = opts[opts.index("-protocol_whitelist"):]
        assert tail == ["-protocol_whitelist", "file", "-ss", "1.500"]
    fc = argv[argv.index("-filter_complex") + 1]
    assert fc == "[0:a][1:a]amix=inputs=2:duration=longest,volume=2"
    assert argv[argv.index("-t") + 1] == "2.500"  # the span, an output option after the mix
    assert argv.index("-t") > argv.index("-filter_complex")
    assert argv[-7:] == ["-f", "s16le", "-ac", "1", "-ar", str(backends.SAMPLE_RATE), "-"]


def test_one_stem_plays_as_a_plain_file(env, fake_ffmpeg):
    src = tone(env, "song.wav", 0.5)
    other = make_stem(src, "other")
    mouth.play(str(src), spawn=False, d=env.d, device="null", stem="other")
    run(env).join(8)
    (argv,) = fake_ffmpeg()
    assert "-filter_complex" not in argv and "-ss" not in argv
    assert _inputs(argv) == [(["-nostdin", "-hide_banner", "-loglevel", "error",
                               "-protocol_whitelist", "file"], str(other))]


def test_four_stems_are_one_ffmpeg_with_volume_4(env, fake_ffmpeg):
    src = tone(env, "song.wav", 0.5)
    for n in sidecar.STEMS:
        make_stem(src, n)
    mouth.play(str(src), spawn=False, d=env.d, device="null", stem="vocals,drums,bass,other")
    run(env).join(8)
    (argv,) = fake_ffmpeg()
    assert len(_inputs(argv)) == 4 and argv.count("-protocol_whitelist") == 4
    assert argv[argv.index("-filter_complex") + 1].endswith("amix=inputs=4:duration=longest,volume=4")


def test_filesound_takes_one_path_as_before():
    fs = backends.FileSound("/x/a.wav", 2.0, 3.0)
    assert fs.path == "/x/a.wav" and fs.paths == ["/x/a.wav"]
    assert fs.command() == ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                            "-protocol_whitelist", "file", "-ss", "2.000", "-i", "/x/a.wav",
                            "-t", "1.000", "-f", "s16le", "-ac", "1", "-ar",
                            str(backends.SAMPLE_RATE), "-"]
    with pytest.raises(ValueError):
        backends.FileSound([])


# -- for real: two equal stems sum, +6 dB ---------------------------------------------

@pytest.mark.needs_ffmpeg
def test_a_real_two_stem_mix_sums(env, monkeypatch):
    env_fence.delenv(monkeypatch, "MOUTH_MUSIC_INSTRUMENTAL")
    src = tone(env, "song.wav", 1.0)
    make_stem(src, "drums", seconds=1.0, amp=0.1)
    make_stem(src, "bass", seconds=1.0, amp=0.1)     # the same signal: the sum is twice it
    one = mouth.play(str(src), spawn=False, d=env.d, device="dev-a", volume=100, stem="drums")
    run(env).join(8)
    two = mouth.play(str(src), spawn=False, d=env.d, device="dev-b", volume=100, stem="drums,bass")
    run(env).join(8)
    saids = kind(env, "said")
    assert saids[one["utt"]]["reason"] == "done" == saids[two["utt"]]["reason"]

    def rms_db(dev):
        x = np.concatenate([blk for o in env.outs[dev] for blk in o.blocks if len(blk)])
        x = x[len(x) // 10: -len(x) // 10]           # the body of the tone, not its edges
        return 20 * np.log10(np.sqrt(np.mean(x.astype(np.float64) ** 2)))

    assert rms_db("dev-b") - rms_db("dev-a") == pytest.approx(6.02, abs=0.3)   # summed, not averaged


@pytest.mark.parametrize("bad", [[], None, "", "piano"])
def test_a_mailed_empty_or_bad_stem_is_an_error_never_the_song(rec, monkeypatch, bad):
    sounds = rec.tmp / "sounds"
    src = song(rec, folder=sounds)
    make_instrumental(src)
    monkeypatch.setenv("MOUTH_SOUNDS_DIR", str(sounds))
    mailed(rec, src, X_Mouth_Extra=json.dumps({"stem": bad}))
    run(rec).join(8)
    said = said_for(rec)
    assert said["reason"] == "error" and Rec.paths == []   # neither the song nor its instrumental
