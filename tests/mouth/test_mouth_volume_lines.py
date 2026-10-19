"""EARS-152 do-002: a spoken line plays at its own volume (design.md s7).

mouth say and mouth play each have a control for the
volume.

The gain is ``volume / 100``, applied in ``play_one`` just before each block
is written, so every output sees it. At 100 nothing is multiplied: today's
samples, byte for byte. What the mouth keeps (``audio.keep``) stays at full
scale. A line his words cut comes back at the level it was cut at.

Seams: volume-per-item (lines), volume-unchanged-at-100, volume-resume. A
level is measured on what reached the output, not read off a field.
"""
from __future__ import annotations

import json
import threading

import numpy as np
import pytest
from test_mouth_inbox import box, lines, run_player  # noqa: F401  (fixture)

import mouth
from mouth import box as _box
from mouth import player, voices

SR = 24000
TONE = (0.5 * np.sin(np.arange(SR // 2) * 2 * np.pi * 440 / SR)).astype(np.float32)   # 0.5 s


class Rec:
    """An output that keeps every block written to it."""

    name = "null"
    latency = 0.0
    underrun_frames = 0
    underruns = 0
    pan_ignored = None

    def __init__(self):
        self.blocks = []
        self.frames_played = 0

    def write(self, block):
        self.blocks.append(block)
        self.frames_played += len(block)

    def drain(self):
        pass

    def abort(self):
        pass

    @property
    def audio(self):
        return np.concatenate(self.blocks)


class Tone:
    """A finished synthesis of TONE, in two blocks (so a line spans blocks)."""

    def __init__(self, item):
        self.item, self.utt = item, item["utt"]
        self.backend = type("B", (), {"name": "silence", "sample_rate": SR})()
        self.sample_rate, self.frames, self.finished, self.gen_s = SR, len(TONE), True, 0.0
        self._blocks = [TONE[: SR // 4], TONE[SR // 4:]]

    def blocks(self, tick=0.05):
        yield from self._blocks

    def snapshot(self):
        return list(self._blocks)

    def cancel(self):
        pass


@pytest.fixture
def rig(box, monkeypatch):
    """play_one over the Tone, into Rec; `kept` is what audio.keep was handed."""
    outs, kept = [], []
    monkeypatch.setenv("MOUTH_SESSION_BIN", "")
    monkeypatch.setenv("MOUTH_SETTINGS", str(box.d.parent / "mouth.json"))

    def open_output(device, rate, pan=None, meta=None):
        outs.append(Rec())
        return outs[-1]

    monkeypatch.setattr(player._output, "open_output", open_output)
    monkeypatch.setattr(player._audio, "keep", lambda item, blocks, *a, **kw: kept.append(list(blocks)))

    def play(volume=None, **fields):
        item = {"utt": f"u{len(outs)}", "text": "hello there", "device": "null", "backend": "silence",
                "requested_t": 0.0, "log_dir": str(box.logs), **fields}
        if volume is not None:
            item.update(volume=volume, volume_from=fields.get("volume_from", "flag"))
        rec = player.play_one(item, box.d, Tone(item))
        return outs[-1], rec

    return type("Rig", (), {"play": staticmethod(play), "outs": outs, "kept": kept})


def peak(out):
    return float(np.max(np.abs(out.audio)))


# -- volume-unchanged-at-100 -------------------------------------------------

@pytest.mark.parametrize("volume", [None, 100])
def test_at_100_or_no_level_the_blocks_are_todays_byte_for_byte(rig, volume):
    out, rec = rig.play(volume)
    assert rec["reason"] == "done"
    assert out.audio.dtype == np.float32 and out.audio.tobytes() == TONE.tobytes()
    # nothing was multiplied: every block written is a view of the synthesis itself
    assert all(np.shares_memory(b, TONE) for b in out.blocks)


@pytest.mark.parametrize("bad", [150, -5, "loud", 50.5, True])
def test_a_bad_level_on_the_item_plays_at_100_not_at_all(rig, bad):
    out, rec = rig.play(bad)
    assert rec["reason"] == "done" and out.audio.tobytes() == TONE.tobytes()


# -- volume-per-item (lines) -------------------------------------------------

def test_each_line_plays_at_its_own_volume(rig):
    quiet, _ = rig.play(30)
    plain, _ = rig.play()
    full, _ = rig.play(100)
    assert peak(quiet) == pytest.approx(0.3 * peak(full), rel=1e-5)
    assert peak(plain) == peak(full)
    assert quiet.audio.dtype == np.float32
    assert np.allclose(quiet.audio, TONE * 0.3, atol=1e-7)   # a gain, not a clip or a shape change
    assert len(quiet.audio) == len(TONE)


def test_volume_zero_is_silence_and_still_plays_through(rig):
    out, rec = rig.play(0)
    assert rec["reason"] == "done" and rec["cut"] is False and not out.audio.any()


def test_what_the_mouth_keeps_is_full_scale(rig):
    rig.play(20)
    (blocks,) = rig.kept
    assert np.concatenate(blocks).tobytes() == TONE.tobytes()


def test_saying_and_said_carry_the_level(rig, box):
    rig.play(30, volume_from="session")
    rows = [x for x in lines(box.logs) if x["kind"] in ("saying", "said")]
    assert [(x["kind"], x["volume"], x["volume_from"]) for x in rows] == [
        ("saying", 30, "session"), ("said", 30, "session")]


def test_through_the_player_a_flag_and_the_cascade_default(box, monkeypatch):
    """End to end on the real player (silence backend, null device): the rows
    say what each line played at, and playing.json / status carry it."""
    host = box.d.parent / "mouth.json"
    host.write_text(json.dumps({"volume": 40}))
    monkeypatch.setenv("MOUTH_SETTINGS", str(host))
    monkeypatch.setenv("MOUTH_SESSION_BIN", "")
    a = mouth.say("at thirty", volume=30, d=box.d, spawn=False)
    b = mouth.say("the host default", d=box.d, spawn=False)
    host.write_text("{}")
    c = mouth.say("nothing set", d=box.d, spawn=False)
    run_player(box.d).join(10)
    saying = {x["utt"]: x for x in lines(box.logs) if x["kind"] == "saying"}
    assert (saying[a["utt"]]["volume"], saying[a["utt"]]["volume_from"]) == (30, "flag")
    assert (saying[b["utt"]]["volume"], saying[b["utt"]]["volume_from"]) == (40, "settings")
    assert (saying[c["utt"]]["volume"], saying[c["utt"]]["volume_from"]) == (100, "builtin")


def test_status_shows_the_playing_lines_volume(box):
    box.d.mkdir(parents=True, exist_ok=True)
    (box.d / "playing.json").write_text(json.dumps({"utt": "u1", "text": "hi", "volume": 30,
                                                    "volume_from": "flag"}))
    assert mouth.status(box.d)["playing"] == {"utt": "u1", "text": "hi", "volume": 30,
                                              "volume_from": "flag"}
    (box.d / "playing.json").write_text(json.dumps({"utt": "u2", "text": "hi"}))
    assert mouth.status(box.d)["playing"] == {"utt": "u2", "text": "hi"}


# -- volume-resume -------------------------------------------------------------

@pytest.fixture
def resumable(box, monkeypatch):
    monkeypatch.setenv("MOUTH_SESSION_BIN", "")
    monkeypatch.setenv("MOUTH_SETTINGS", str(box.d.parent / "mouth.json"))
    monkeypatch.setattr(player._hold, "resume_s", lambda: 30.0)
    monkeypatch.setattr(player._hold, "asked_for_quiet", lambda *a, **kw: False)
    box.d.mkdir(parents=True, exist_ok=True)

    def cut(item):
        rec = {"played_s": 1.0, "text_played_est": "First sentence."}
        player._resume(box.d, {"utt": "cut1", "text": "First sentence. Second one here.",
                               "voice": "af_sky", "device": "null", "backend": "silence",
                               "agent": "ivy", **item}, rec)
        (x,) = [_box.public(e) for e in _box.Queue(box.d).lines()]
        return x
    return cut


def test_a_line_cut_at_30_comes_back_at_30(resumable):
    x = resumable({"volume": 30, "volume_from": "session"})
    assert x["resume_of"] == "cut1" and x["text"] == "Second one here."
    assert (x["volume"], x["volume_from"]) == (30, "session")


def test_a_builtin_level_comes_back_as_it_was(resumable):
    x = resumable({"volume": 100, "volume_from": "builtin"})
    assert (x["volume"], x["volume_from"]) == (100, "builtin")


def test_a_resume_reads_no_cascade_in_the_player(resumable, monkeypatch):
    """The player's own session is whoever spawned it, not the cut line's
    caller: a resume must not read that session's settings (do-001 review)."""
    runs = []
    monkeypatch.setattr(voices, "cascade", lambda sid, keys: runs.append(sid) or {})
    monkeypatch.setenv("VOICEMODE_SESSION_ID", "the-spawner")
    resumable({"volume": 30, "volume_from": "flag"})
    assert runs == []


def test_a_resume_through_the_player_keeps_its_level(box, monkeypatch):
    """serve -> play_one cut by barge-in -> _resume: the resumed line carries the
    level the cut line played at (play_one and the barge are faked)."""
    monkeypatch.setenv("MOUTH_SESSION_BIN", "")
    monkeypatch.setenv("MOUTH_SETTINGS", str(box.d.parent / "mouth.json"))
    monkeypatch.setattr(player._hold, "resume_s", lambda: 30.0)
    monkeypatch.setattr(player._hold, "asked_for_quiet", lambda *a, **kw: False)
    seen = []

    def fake_play_one(item, d, synth=None, prefetched=False, **kw):   # kw: beds (do-005)
        if synth is not None:
            synth.cancel()
        seen.append(item)
        if item.get("resume_of"):
            return {"utt": item["utt"], "reason": "done", "cut": False, "played_s": 0.5}
        return {"utt": item["utt"], "reason": "barge-in", "cut": True, "played_s": 1.0,
                "text_played_est": "First sentence."}

    monkeypatch.setattr(player, "play_one", fake_play_one)
    monkeypatch.setattr(player._hold, "check", lambda x: "play")
    mouth.say("First sentence. Second one here.", volume=25, d=box.d, spawn=False)
    t = threading.Thread(target=player.serve, kwargs={"d": box.d, "idle_exit_s": 0.5}, daemon=True)
    t.start()
    t.join(10)
    assert [x.get("resume_of") is not None for x in seen] == [False, True]
    assert (seen[1]["volume"], seen[1]["volume_from"]) == (25, "flag")
