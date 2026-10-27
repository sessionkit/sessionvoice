"""Playing an existing instrumental: the swap at take time (design.md sections 2 and 3).

When ``music_instrumental`` resolves on, the player takes a played file and
decodes its sidecar's instrumental in its place, on any device, a call's
included: a call never STARTS a split, but it uses an instrumental that exists.
The rows and ``mouth status``'s music row say what played (``file``), what was
asked for (``source_file``) and which layer answered (``instrumental_from``).

The sidecar here is made by hand (``mixes/instrumental.wav`` in ``.<name>.d/``,
or another layout the reader takes): nothing splits, nothing separates.

Most tests stand a recording decoder in for ffmpeg (``Rec``): it notes the path
each bed decodes and gives a short silence, so they run on any host. One test
decodes for real (``needs_ffmpeg``) and measures the level that reached the
device: the instrumental is made quieter than the song, so the level says which
one played.
"""

from __future__ import annotations

import json
import os
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from maildir_put import put
from test_mouth_bed import HOST, env, kind, run, tone, wait_for  # noqa: F401  (fixture)
from test_mouth_call_sound import sbots  # noqa: F401  (fixture)

import mouth
from mouth import __main__ as cli
from mouth import backends, bed, box, instrumental, player, sidecar
from tests import env_fence


class Rec:
    """A decoder that records the path it was handed and gives SECONDS of silence."""

    paths: list = []
    seconds = 0.3

    def __init__(self, path, start=None, end=None):
        self.path, self.start, self.end = path, start, end
        Rec.paths.append(path)

    name = "file"
    sample_rate = backends.SAMPLE_RATE

    def stream(self, text, voice, speed):
        n = int(self.sample_rate * 0.1)
        for _ in range(max(1, round(Rec.seconds / 0.1))):
            yield np.zeros(n, dtype=np.float32)


@pytest.fixture
def rec(env, monkeypatch):
    """``env`` with the recording decoder in for ffmpeg."""
    Rec.paths, Rec.seconds = [], 0.3
    monkeypatch.setattr(backends, "FileSound", Rec)
    env_fence.delenv(monkeypatch, "MOUTH_MUSIC_INSTRUMENTAL")
    env_fence.delenv(monkeypatch, "MOUTH_SOUNDS_DIR")
    return env


def song(env, name="song.wav", folder=None, seconds=0.5, amp=0.3) -> Path:
    path = tone(env, name, seconds, amp)
    if folder is not None:
        folder.mkdir(parents=True, exist_ok=True)
        path = path.rename(folder / name)
    return path


def make_instrumental(src: Path, rel="mixes/instrumental.wav", folder=None, seconds=0.5, amp=0.05) -> Path:
    """A hand-made sidecar: SRC's instrumental at REL in its dot folder (or FOLDER)."""
    import wave

    out = (folder or sidecar.canonical_dir(src)) / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    rate = 24000
    x = (amp * np.sin(2 * np.pi * 220 * np.arange(int(seconds * rate)) / rate) * 32767).astype("<i2")
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(x.tobytes())
    return out


def manifest(src: Path, verdict: str) -> None:
    st = src.stat()
    (sidecar.canonical_dir(src) / sidecar.MANIFEST).write_text(json.dumps({
        "version": 1, "separator": "demucs", "verdict": verdict,
        "source": {"name": src.name, "size": st.st_size, "mtime_ns": st.st_mtime_ns,
                   "duration_s": 0.5}}))


def mailed(env, path: Path, **headers) -> str:
    """A mail from outside asking the mouth to play PATH."""
    env.n[0] += 1
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = f"vera@{HOST}.example.ts.net", f"mouth@{HOST}", "a sound"
    mid = f"<{env.n[0]}.inst@{HOST}.session-mail>"
    m["Message-ID"] = mid
    m["X-Mouth-File"] = str(path)
    m["X-Mouth-Device"] = "null"
    for k, v in headers.items():
        m[k.replace("_", "-")] = v
    m.set_content("")
    put(env.mail, f"{os.urandom(4).hex()}.{env.n[0]}.{HOST}", bytes(m))
    return mid


def said_for(env, utt=None) -> dict:
    rows = kind(env, "said")
    if utt is None:
        (only,) = rows.values()
        return only
    return rows[utt]


# -- the swap: a local play ----------------------------------------------------

def test_the_bed_decodes_the_instrumental_when_the_sidecar_has_one(rec):
    src = song(rec)
    inst = make_instrumental(src)
    f = mouth.play(str(src), spawn=False, d=rec.d, device="null")
    run(rec).join(8)
    assert Rec.paths == [str(inst)]                      # the instrumental is what decodes
    saying, said = kind(rec, "saying")[f["utt"]], said_for(rec, f["utt"])
    for row in (saying, said):
        assert row["file"] == str(inst)                  # what played
        assert row["source_file"] == str(src)            # what was asked for
        assert row["instrumental_from"] == "builtin"     # the setting's built-in, on
    assert saying["text"] == "[sound song.wav]"          # the label names the song
    assert said["reason"] == "done"


def test_off_plays_the_original_by_the_host_file_and_by_the_flag(rec):
    src = song(rec)
    make_instrumental(src)
    rec.settings.write_text(json.dumps({"music_instrumental": False}))
    a = mouth.play(str(src), spawn=False, d=rec.d, device="null")
    run(rec).join(8)
    rec.settings.write_text("{}")
    b = mouth.play(str(src), spawn=False, d=rec.d, device="null", instrumental=False)
    run(rec).join(8)
    assert Rec.paths == [str(src), str(src)]
    for utt, src_from in ((a["utt"], "settings"), (b["utt"], "flag")):
        said = said_for(rec, utt)
        assert said["file"] == str(src) and "source_file" not in said
        assert said["instrumental_from"] == src_from


def test_off_by_the_callers_env_plays_the_original(rec, monkeypatch):
    src = song(rec)
    make_instrumental(src)
    monkeypatch.setenv("MOUTH_MUSIC_INSTRUMENTAL", "off")
    f = mouth.play(str(src), spawn=False, d=rec.d, device="null")
    monkeypatch.delenv("MOUTH_MUSIC_INSTRUMENTAL")   # the player's own env never answers
    run(rec).join(8)
    said = said_for(rec, f["utt"])
    assert Rec.paths == [str(src)] and said["instrumental_from"] == "env"


def test_no_instrumental_plays_the_original_and_never_splits(rec):
    src = song(rec)
    f = mouth.play(str(src), spawn=False, d=rec.d, device="null")
    run(rec).join(8)
    said = said_for(rec, f["utt"])
    assert Rec.paths == [str(src)] and "source_file" not in said
    assert said["instrumental_from"] == "builtin"
    assert not sidecar.canonical_dir(src).exists()       # nothing made, nothing written


def test_the_reader_takes_the_other_layouts_too(rec):
    src = song(rec)
    # audio-tools' folder, no dot: the one kept mix already on disk is of this shape
    inst = make_instrumental(src, folder=src.parent / f"{src.name}.d")
    mouth.play(str(src), spawn=False, d=rec.d, device="null")
    run(rec).join(8)
    assert Rec.paths == [str(inst)]


def test_a_section_plays_the_same_section_of_the_instrumental(rec):
    src = song(rec, seconds=2.0)
    inst = make_instrumental(src, seconds=2.0)
    f = mouth.play(str(src), spawn=False, d=rec.d, device="null", start=0.5, end=1.5)
    run(rec).join(8)
    said = said_for(rec, f["utt"])
    assert Rec.paths == [str(inst)]
    assert (said["file"], said["start"], said["end"]) == (str(inst), 0.5, 1.5)


def test_a_speech_verdict_plays_the_original_unless_the_line_asks(rec):
    src = song(rec)
    inst = make_instrumental(src)
    manifest(src, "speech")
    a = mouth.play(str(src), spawn=False, d=rec.d, device="null")
    run(rec).join(8)
    b = mouth.play(str(src), spawn=False, d=rec.d, device="null", instrumental=True)
    run(rec).join(8)
    assert Rec.paths == [str(src), str(inst)]            # --instrumental still plays it
    assert "source_file" not in said_for(rec, a["utt"])
    assert said_for(rec, b["utt"])["source_file"] == str(src)


def test_a_music_verdict_plays_the_instrumental(rec):
    src = song(rec)
    inst = make_instrumental(src)
    manifest(src, "music")
    mouth.play(str(src), spawn=False, d=rec.d, device="null")
    run(rec).join(8)
    assert Rec.paths == [str(inst)]


# -- a mail from outside ------------------------------------------------------

def test_a_mailed_play_uses_an_existing_instrumental(rec, monkeypatch):
    sounds = rec.tmp / "sounds"
    src = song(rec, folder=sounds)
    inst = make_instrumental(src)
    monkeypatch.setenv("MOUTH_SOUNDS_DIR", str(sounds))
    mid = mailed(rec, src)
    run(rec).join(8)
    said = said_for(rec)
    assert said["mail_id"] == mid and said["reason"] == "done"
    assert Rec.paths == [os.path.realpath(inst)]
    assert said["source_file"] == os.path.realpath(src)
    assert said["instrumental_from"] == "builtin"        # no caller: the host file, then on


def test_a_mailed_play_follows_the_host_file_off(rec, monkeypatch):
    sounds = rec.tmp / "sounds"
    src = song(rec, folder=sounds)
    make_instrumental(src)
    monkeypatch.setenv("MOUTH_SOUNDS_DIR", str(sounds))
    rec.settings.write_text(json.dumps({"music_instrumental": False}))
    mailed(rec, src)
    run(rec).join(8)
    assert Rec.paths == [os.path.realpath(src)]
    assert said_for(rec)["instrumental_from"] == "settings"


def test_a_mailed_play_never_follows_a_sidecar_link_out_of_the_sounds_dir(rec, monkeypatch):
    sounds = rec.tmp / "sounds"
    src = song(rec, folder=sounds)
    outside = make_instrumental(src, folder=rec.tmp / "elsewhere")
    link = sidecar.canonical_dir(src) / "mixes" / "instrumental.wav"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)
    monkeypatch.setenv("MOUTH_SOUNDS_DIR", str(sounds))
    mailed(rec, src)
    run(rec).join(8)
    said = said_for(rec)
    assert Rec.paths == [os.path.realpath(src)]          # the original, never the link's target
    assert "source_file" not in said and said["reason"] == "done"


def test_a_local_play_may_follow_a_link_in_its_sidecar(rec):
    src = song(rec)
    outside = make_instrumental(src, folder=rec.tmp / "elsewhere")
    link = sidecar.canonical_dir(src) / "mixes" / "instrumental.wav"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)
    mouth.play(str(src), spawn=False, d=rec.d, device="null")
    run(rec).join(8)
    assert Rec.paths == [str(link)]                      # the user's own line: any file


# -- a call device -------------------------------------------------------------

def test_an_item_for_a_call_gets_the_same_swap(rec, sbots):
    bot = sbots([5531])
    src = song(rec)
    inst = make_instrumental(src)
    f = mouth.play(str(src), spawn=False, d=rec.d, device="call:5531")
    run(rec).join(8)
    said = said_for(rec, f["utt"])
    assert Rec.paths == [str(inst)]
    assert said["device"] == "call:5531" and said["reason"] == "done"
    assert (said["file"], said["source_file"]) == (str(inst), str(src))
    (line,) = bot.lines                                  # the bed's mixer, on the call
    assert line["header"]["text"] == "[sound song.wav]" and line["ended"] == "done"
    assert line["samples"] == pytest.approx(0.3 * 48000, abs=4800)


def test_a_mailed_call_item_gets_the_same_swap(rec, sbots, monkeypatch):
    sbots([5531])
    sounds = rec.tmp / "sounds"
    src = song(rec, folder=sounds)
    inst = make_instrumental(src)
    monkeypatch.setenv("MOUTH_SOUNDS_DIR", str(sounds))
    item = {"utt": "u1", "backend": "file", "file": os.path.realpath(src), "device": "call:7",
            "instrumental": True, "instrumental_from": "settings"}
    out = instrumental.swap(item, local=False, d=rec.d)
    assert out["file"] == os.path.realpath(inst) and out["device"] == "call:7"


# -- the readout: mouth status, the cut notice ---------------------------------

def test_mouth_status_music_row_names_both_files(rec):
    Rec.seconds = 3.0
    src = song(rec)
    inst = make_instrumental(src)
    f = mouth.play(str(src), spawn=False, d=rec.d, device="null")
    th = run(rec)
    assert wait_for(lambda: bed.read(rec.d) and bed.read(rec.d)[0].get("started_t"))
    (row,) = mouth._music(rec.d)
    assert (row["file"], row["source_file"], row["instrumental_from"]) == (str(inst), str(src), "builtin")
    mouth.retract(f["utt"], d=rec.d)
    th.join(8)


def test_the_cut_notice_names_the_song_not_its_instrumental(rec, monkeypatch, capsys):
    src = song(rec)
    inst = make_instrumental(src)
    cut = {"utt": "u1", "cut": True, "reason": "stop", "played_s": 0.2, "cut_at_s": 0.2,
           "file": str(inst), "source_file": str(src), "start": None, "end": None}
    monkeypatch.setattr(cli, "play", lambda *a, **k: cut)
    assert cli.main(["play", str(src), "--wait"]) == 1
    err = capsys.readouterr().err
    assert "song.wav" in err and "instrumental.wav" not in err


# -- the swap itself -----------------------------------------------------------

def _item(path, **kw) -> dict:
    return {"utt": "u1", "backend": "file", "file": str(path), "instrumental": True,
            "instrumental_from": "builtin", **kw}


def test_swap_leaves_everything_but_the_file(tmp_path):
    src = tmp_path / "a.flac"
    src.write_bytes(b"x")
    inst = make_instrumental(src)
    it = _item(src, start=1.0, end=2.0, volume=40, duck={"on": True}, device="dev-x")
    out = instrumental.swap(it, local=True)
    assert out == {**it, "file": str(inst), "source_file": str(src)}
    assert it["file"] == str(src)                         # the item itself is never changed
    assert instrumental.swap(out, local=True) is out      # swapped once, never twice


def test_swap_passes_what_is_not_a_played_file_or_not_on(tmp_path):
    src = tmp_path / "a.flac"
    src.write_bytes(b"x")
    make_instrumental(src)
    spoken = {"utt": "u1", "text": "hello", "backend": "silence"}
    again = {**_item(src), "again_of": "u0"}               # a say-again replay is a spoken line
    off = _item(src, instrumental=False)
    unfilled = {k: v for k, v in _item(src).items() if k != "instrumental"}
    for it in (spoken, again, off, unfilled):
        assert instrumental.swap(it, local=True) is it


def test_swap_mail_from_outside_needs_the_sounds_dir(tmp_path, monkeypatch):
    sounds = tmp_path / "sounds"
    sounds.mkdir()
    src = sounds / "a.flac"
    src.write_bytes(b"x")
    inst = make_instrumental(src)
    env_fence.delenv(monkeypatch, "MOUTH_SOUNDS_DIR")
    elsewhere = tmp_path / "mouth"
    assert instrumental.swap(_item(src), local=False, d=elsewhere)["file"] == str(src)   # no sounds dir
    monkeypatch.setenv("MOUTH_SOUNDS_DIR", str(sounds))
    assert instrumental.swap(_item(src), local=False)["file"] == os.path.realpath(inst)


def test_swap_mail_ask_plays_a_speech_verdict(tmp_path):
    src = tmp_path / "a.flac"
    src.write_bytes(b"x")
    inst = make_instrumental(src)
    manifest(src, "speech")
    assert instrumental.swap(_item(src), local=True)["file"] == str(src)
    for asked in instrumental.ASKED:
        assert instrumental.swap(_item(src, instrumental_from=asked), local=True)["file"] == str(inst)


def test_swap_a_stale_sidecar_plays_the_original(tmp_path):
    src = tmp_path / "a.flac"
    src.write_bytes(b"x")
    make_instrumental(src)
    manifest(src, "music")
    src.write_bytes(b"a longer file now")                 # size changed since the split
    calls = []

    def probe(p):
        calls.append(p)
        return 99.0                                       # and its length moved far past 1 s

    assert instrumental.swap(_item(src), local=True, probe=probe)["file"] == str(src)
    assert calls == [str(src)]


def test_probe_fails_soft_with_no_ffmpeg(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(instrumental.subprocess, "run", boom)
    assert instrumental.probe_duration("/nowhere.wav") is None


def test_probe_reads_ffmpegs_duration_line(monkeypatch):
    out = SimpleNamespace(stderr=b"Input #0, wav\n  Duration: 00:01:35.00, bitrate: 384 kb/s\n")
    monkeypatch.setattr(instrumental.subprocess, "run", lambda *a, **k: out)
    assert instrumental.probe_duration("/x.wav") == 95.0


# -- for real: the level at the device says which file played ------------------

@pytest.mark.needs_ffmpeg
def test_the_instrumental_is_what_reaches_the_device(env, monkeypatch):
    env_fence.delenv(monkeypatch, "MOUTH_MUSIC_INSTRUMENTAL")
    src = tone(env, "loud.wav", 1.0, amp=0.5)
    make_instrumental(src, seconds=1.0, amp=0.05)
    a = mouth.play(str(src), spawn=False, d=env.d, device="dev-a", volume=100)
    run(env).join(8)
    b = mouth.play(str(src), spawn=False, d=env.d, device="dev-b", volume=100, instrumental=False)
    run(env).join(8)
    assert kind(env, "said")[a["utt"]]["reason"] == "done" == kind(env, "said")[b["utt"]]["reason"]

    def peak(dev):
        return max(float(np.abs(blk).max()) for o in env.outs[dev] for blk in o.blocks if len(blk))

    assert peak("dev-a") == pytest.approx(0.05, abs=0.01)   # the quiet instrumental
    assert peak("dev-b") == pytest.approx(0.5, abs=0.02)    # the song, as it is
