"""EARS-208: voices levelled to one loudness, by a gain kept with the voice.

Voices sat about 14 dB
under his music. A mouth setting ``normalize`` (default on) and
``mouth say --normalize/--no-normalize``; a target ``normalize_lufs`` (-16);
a per-voice ``settings.json`` (gain_db, measured_lufs, measured_at,
gain_from measured|hand), measured lazily from what the voice has said and
written once, a hand gain never re-measured; the gain on each block as it
streams, with a light limiter.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime

import numpy as np
import pytest
from test_mouth_volume_settings import cascade, home, played, queued, said  # noqa: F401  (fixture)
from test_mouth_volume_settings import host as vhost

from mouth import audio, level, player, voices

SR = 24000
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg measures loudness")


def tone(amp: float, secs: float = 0.5, hz: float = 1000.0) -> np.ndarray:
    return (amp * np.sin(np.arange(int(SR * secs)) * 2 * np.pi * hz / SR)).astype(np.float32)


@pytest.fixture
def mdir(tmp_path, monkeypatch):
    """A fenced mouth dir, voices dir, kept-audio dir and host settings file."""
    d = tmp_path / "mouth"
    d.mkdir()
    monkeypatch.setenv("MOUTH_DIR", str(d))
    monkeypatch.setenv("VOICEMODE_VOICES_DIR", str(tmp_path / "voices"))
    monkeypatch.setenv("MOUTH_AUDIO_DIR", str(tmp_path / "audio"))
    monkeypatch.setenv("MOUTH_SETTINGS", str(d / "settings.json"))
    monkeypatch.setenv("MOUTH_SESSION_BIN", "")
    level.forget_tries()
    return d


def host(d, **kw):
    (d / "settings.json").write_text(json.dumps(kw))


# -- the switch and the target -------------------------------------------------

def test_on_at_minus_16_when_nothing_sets_them(mdir):
    assert voices.normalize_setting(d=mdir) == (True, "builtin")
    assert voices.lufs_setting(d=mdir) == (-16.0, "builtin")


def test_the_host_file_turns_it_off_and_moves_the_target(mdir):
    host(mdir, normalize=False, normalize_lufs=-20)
    assert voices.normalize_setting(d=mdir) == (False, "settings")
    assert voices.lufs_setting(d=mdir) == (-20.0, "settings")


def test_the_lines_own_flag_wins_over_every_layer(mdir):
    host(mdir, normalize=False)
    item = {"normalize": False, "normalize_from": "session"}
    assert voices.normalize_setting(flag=True, item=item, d=mdir) == (True, "flag")
    assert voices.normalize_setting(item=item, d=mdir) == (False, "session")
    assert voices.normalize_setting(item={"normalize": True, "normalize_from": "flag"}, d=mdir) == (True, "flag")


@pytest.mark.parametrize("bad", ["yes", 1, None])
def test_a_bad_switch_falls_to_the_next_layer(mdir, bad):
    host(mdir, normalize=bad)
    assert voices.normalize_setting(item={"normalize": bad, "normalize_from": "seat"}, d=mdir) == (True, "builtin")


@pytest.mark.parametrize("bad", [0, -60, "loud", True, float("nan")])
def test_a_bad_target_falls_to_minus_16(mdir, bad):
    host(mdir, normalize_lufs=bad)
    assert voices.lufs_setting(d=mdir) == (-16.0, "builtin")


def test_a_flag_that_is_not_a_bool_is_refused():
    with pytest.raises(ValueError):
        voices.normalize_setting(flag="on")


# -- where a voice's settings live --------------------------------------------

def test_a_clone_keeps_its_settings_in_its_own_dir(mdir, tmp_path):
    (tmp_path / "voices" / "ivy").mkdir(parents=True)
    assert level.settings_path("ivy") == tmp_path / "voices" / "ivy" / "settings.json"


def test_any_other_voice_keeps_them_under_the_mouth_dir(mdir, tmp_path):
    assert level.settings_path("af_sky") == mdir / "voices" / "af_sky" / "settings.json"
    assert not (tmp_path / "voices" / "af_sky").exists()   # never a dir that would read as a clone


@pytest.mark.parametrize("bad", ["", "/abs/voice.wav", "../x", "ivy[2]", "a/../b", None, 3])
def test_a_voice_that_names_no_directory_has_no_settings(mdir, bad):
    assert level.settings_path(bad) is None
    assert level.voice_settings(bad) == {}


def test_the_gain_is_target_minus_measured_and_a_hand_gain_wins():
    assert level.gain_db({"measured_lufs": -27.3}, -16.0) == (pytest.approx(11.3), "measured")
    assert level.gain_db({"measured_lufs": -27.3}, -20.0) == (pytest.approx(7.3), "measured")
    assert level.gain_db({"measured_lufs": -27.3, "gain_db": 4, "gain_from": "hand"}, -16) == (4.0, "hand")
    assert level.gain_db({"measured_lufs": -80.0}, -16.0) == (level.GAIN_LIMIT_DB, "measured")
    assert level.gain_db({}, -16.0) is None
    assert level.gain_db({"gain_from": "hand", "gain_db": "x"}, -16.0) is None


# -- the leveller --------------------------------------------------------------

def test_a_gain_with_headroom_is_a_plain_multiply():
    x = tone(0.1)
    y = level.Leveler(6.0, SR).apply(x)
    assert y.dtype == np.float32
    assert np.allclose(y, x * 10 ** (6 / 20), atol=1e-6)


def test_the_limiter_keeps_every_sample_under_the_ceiling():
    lev = level.Leveler(12.0, SR)   # a 0.5 peak at +12 dB would be 2.0
    out = np.concatenate([lev.apply(b) for b in np.array_split(tone(0.5, 1.0), 20)])
    assert float(np.max(np.abs(out))) <= level.CEILING + 1e-6
    assert lev.limited >= 1
    assert float(np.max(np.abs(out[SR // 2:]))) >= level.KNEE * 0.95   # held near the knee, not crushed


@pytest.mark.parametrize("blocks", [1, 7, 20, 200])
def test_full_scale_at_the_largest_gain_never_reaches_0_dbfs(blocks):
    """SC4, the worst case: a full-scale voice at +GAIN_LIMIT_DB, in any block size."""
    lev = level.Leveler(level.GAIN_LIMIT_DB, SR)
    x = np.concatenate([tone(1.0, 0.5), -np.ones(SR // 10, dtype=np.float32), tone(0.355, 0.5)])
    out = np.concatenate([lev.apply(b) for b in np.array_split(x, blocks)])
    assert float(np.max(np.abs(out))) <= level.CEILING + 1e-6 < 1.0


def test_the_limiter_lets_go_after_a_loud_passage():
    lev = level.Leveler(12.0, SR)
    for b in np.array_split(tone(0.5, 0.2), 4):
        lev.apply(b)
    assert lev.r < 0.6
    quiet = tone(0.01, 3.0)
    out = np.concatenate([lev.apply(b) for b in np.array_split(quiet, 60)])
    assert lev.r == 1.0
    assert np.allclose(out[-1200:], quiet[-1200:] * 10 ** (12 / 20), atol=1e-6)


# -- measuring -----------------------------------------------------------------

@needs_ffmpeg
def test_lufs_of_a_full_scale_1k_sine_is_minus_3():
    pcm = audio.pcm16([tone(1.0 - 1e-4, 5.0)])
    assert level.lufs(pcm, SR) == pytest.approx(-3.0, abs=0.3)
    assert level.lufs(audio.pcm16([tone(0.1, 5.0)]), SR) == pytest.approx(-23.0, abs=0.3)


@needs_ffmpeg
def test_silence_is_not_a_loudness():
    assert level.lufs(audio.pcm16([np.zeros(SR * 5, dtype=np.float32)]), SR) is None


def keep_lines(voice, amp, n, secs=2.0, backend="kokoro"):
    for i in range(n):
        audio.keep({"utt": f"{voice}{amp}{i}", "voice": voice, "text": "x"}, [tone(amp, secs)], SR,
                   backend, now=1_917_852_800.0 + i)


@needs_ffmpeg
def test_a_voice_is_measured_from_what_it_has_said(mdir):
    keep_lines("af_test", 0.1, 3)
    m, secs = level.measure("af_test", "kokoro")
    assert m == pytest.approx(-23.0, abs=0.3) and secs == pytest.approx(6.0)
    assert level.measure("af_test", "clone") is None      # another backend's lines don't count
    keep_lines("af_short", 0.1, 1)
    assert level.measure("af_short") is None              # under MEASURE_MIN_S


@needs_ffmpeg
def test_measured_once_and_written_beside_the_voice(mdir):
    keep_lines("af_test", 0.1, 3)
    vs = level.ensure_measured("af_test", -16.0, "kokoro", now=1_917_852_900.0)
    p = mdir / "voices" / "af_test" / "settings.json"
    assert json.loads(p.read_text()) == vs
    assert vs["gain_from"] == "measured" and vs["measured_lufs"] == pytest.approx(-23.0, abs=0.3)
    assert vs["gain_db"] == pytest.approx(7.0, abs=0.3) and datetime.fromisoformat(vs["measured_at"]).timestamp() == 1_917_852_900.0
    keep_lines("af_test", 0.5, 30)                        # louder lines later: never re-measured
    assert level.ensure_measured("af_test", -16.0, "kokoro") == vs


@needs_ffmpeg
def test_a_hand_gain_is_never_measured_over(mdir):
    p = mdir / "voices" / "af_test" / "settings.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"gain_db": 3, "gain_from": "hand", "note": "Theo's ear"}))
    keep_lines("af_test", 0.1, 3)
    assert level.ensure_measured("af_test", -16.0, "kokoro")["gain_db"] == 3
    assert json.loads(p.read_text()) == {"gain_db": 3, "gain_from": "hand", "note": "Theo's ear"}


@needs_ffmpeg
@pytest.mark.parametrize("body", [{"gain_from": "hand"}, {"gain_from": "hand", "gain_db": "loud"},
                                  {"gain_from": "hand", "gain_db": None, "measured_lufs": -30}])
def test_a_hand_file_with_no_usable_gain_is_still_never_measured_over(mdir, body):
    """SC3: ``gain_from: hand`` is never re-measured or overwritten, even when its
    gain is missing or bad (the voice plays unlevelled until the hand fixes it)."""
    p = mdir / "voices" / "af_test" / "settings.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps(body))
    keep_lines("af_test", 0.1, 3)
    assert level.ensure_measured("af_test", -16.0, "kokoro") == body
    assert json.loads(p.read_text()) == body


BROKEN = ['{"gain_from": "hand", "gain_db": 6.0,}\n', '["hand", 6.0]\n', '"hand"\n']


@pytest.mark.parametrize("text", BROKEN)
def test_a_file_a_hand_broke_is_never_measured_or_written_over(mdir, monkeypatch, text):
    """SC3: a settings.json that is not a JSON object (a typo in a hand edit; the
    mouth only writes whole objects) is never measured or written over."""
    p = mdir / "voices" / "af_test" / "settings.json"
    p.parent.mkdir(parents=True)
    p.write_text(text)
    calls = []
    monkeypatch.setattr(level, "measure", lambda *a, **k: calls.append(a) or (-23.0, 10.0))
    assert level.ensure_measured("af_test", -16.0, "kokoro") == {}
    assert p.read_text() == text and calls == []


def test_a_hand_that_breaks_the_file_mid_measure_wins(mdir, monkeypatch):
    p = mdir / "voices" / "af_test" / "settings.json"

    def measure(*a, **k):    # the hand saves a typo while the voice is being measured
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(BROKEN[0])
        return -23.0, 10.0
    monkeypatch.setattr(level, "measure", measure)
    level.ensure_measured("af_test", -16.0, "kokoro")
    assert p.read_text() == BROKEN[0]


def test_an_unmeasurable_voice_is_not_looked_at_every_line(mdir, monkeypatch):
    calls = []
    monkeypatch.setattr(level, "measure", lambda *a, **k: calls.append(a) or None)
    for t in (0.0, 10.0, 59.0):
        level.ensure_measured("af_new", -16.0, "kokoro", now=1000.0 + t)
    assert len(calls) == 1
    level.ensure_measured("af_new", -16.0, "kokoro", now=1061.0)
    assert len(calls) == 2


# -- through play_one ----------------------------------------------------------

class Rec:
    name, latency, underrun_frames, underruns, pan_ignored = "null", 0.0, 0, 0, None

    def __init__(self):
        self.blocks, self.frames_played = [], 0

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


TONE = tone(0.2)


class Synth:
    def __init__(self, item, backend="kokoro"):
        self.item, self.utt = item, item["utt"]
        self.backend = type("B", (), {"name": backend, "sample_rate": SR})()
        self.sample_rate, self.frames, self.finished, self.gen_s = SR, len(TONE), True, 0.0
        self._blocks = [TONE[: SR // 4], TONE[SR // 4:]]

    def blocks(self, tick=0.05):
        yield from self._blocks

    def snapshot(self):
        return list(self._blocks)

    def cancel(self):
        pass


@pytest.fixture
def rig(mdir, tmp_path, monkeypatch):
    outs, rows = [], []
    monkeypatch.setattr(player._output, "open_output", lambda *a, **k: outs.append(Rec()) or outs[-1])
    monkeypatch.setattr(player._audio, "keep", lambda *a, **k: None)
    monkeypatch.setattr(player, "_said", lambda item, **f: rows.append(("said", f)) or f)
    monkeypatch.setattr(player, "_saying", lambda item, text, **f: rows.append(("saying", f)) or f)

    def play(voice="af_test", backend="kokoro", **fields):
        item = {"utt": f"u{len(outs)}", "text": "hello there", "device": "null", "backend": backend,
                "voice": voice, "requested_t": 0.0, **fields}
        rec = player.play_one(item, mdir, Synth(item, backend))
        return outs[-1], rec

    return type("Rig", (), {"play": staticmethod(play), "rows": rows})


def hand(mdir, voice, db):
    p = mdir / "voices" / voice / "settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"gain_db": db, "gain_from": "hand"}))


def test_a_voice_plays_at_its_own_gain(rig, mdir):
    hand(mdir, "af_test", -6)
    out, rec = rig.play()
    assert np.allclose(out.audio, TONE * 10 ** (-6 / 20), atol=1e-6)
    assert rec["level"] == {"on": True, "on_from": "builtin", "lufs": -16.0, "gain_db": -6.0,
                            "from": "hand", "limited": 0}
    assert dict(rig.rows)["saying"]["level"]["gain_db"] == -6.0


def test_the_gain_comes_before_the_volume(rig, mdir):
    hand(mdir, "af_test", 12)            # 0.2 * 4 = 0.8: over the knee, so limited
    out, rec = rig.play(volume=50, volume_from="flag")
    assert float(np.max(np.abs(out.audio))) <= level.CEILING * 0.5 + 1e-6
    assert rec["level"]["limited"] >= 1


def test_off_by_the_flag_or_the_file_is_todays_samples(rig, mdir):
    hand(mdir, "af_test", 6)
    out, rec = rig.play(normalize=False, normalize_from="flag")
    assert out.audio.tobytes() == TONE.tobytes() and all(np.shares_memory(b, TONE) for b in out.blocks)
    assert rec["level"] == {"on": False, "on_from": "flag", "lufs": -16.0}
    host(mdir, normalize=False)
    out, _ = rig.play()
    assert out.audio.tobytes() == TONE.tobytes()


def test_a_flip_in_the_host_file_applies_to_the_next_line(rig, mdir):
    """R1: the settings app's flip is read per line, no restart: on, off, on."""
    hand(mdir, "af_test", 6)
    on = TONE * np.float32(10 ** (6 / 20))
    for want_on in (True, False, True):
        host(mdir, normalize=want_on)
        out, rec = rig.play()
        assert rec["level"]["on"] is want_on and rec["level"]["on_from"] == "settings"
        assert np.allclose(out.audio, on if want_on else TONE, atol=1e-6)
    host(mdir, normalize_lufs=-20)
    assert rig.play()[1]["level"]["lufs"] == -20.0


@pytest.mark.parametrize("text", [json.dumps({"gain_from": "hand"}), *BROKEN])
def test_a_hand_file_with_no_gain_plays_as_today_and_says_hand(rig, mdir, monkeypatch, text):
    p = mdir / "voices" / "af_test" / "settings.json"
    p.parent.mkdir(parents=True)
    p.write_text(text)
    started = []
    monkeypatch.setattr(level.threading, "Thread", lambda *a, **k: started.append(k) or None)
    out, rec = rig.play()
    assert out.audio.tobytes() == TONE.tobytes()
    assert rec["level"]["from"] == "hand" and rec["level"]["gain_db"] is None
    # the rig keeps nothing, so play_one never reaches after_kept: ask it directly
    level.after_kept({"voice": "af_test"}, "kokoro", rec["level"], d=mdir)
    assert started == []                                   # no measure queued after it is kept
    assert p.read_text() == text


def test_an_unmeasured_voice_plays_as_today(rig, mdir):
    out, rec = rig.play(voice="af_new")
    assert out.audio.tobytes() == TONE.tobytes()
    assert rec["level"]["from"] == "unmeasured" and rec["level"]["gain_db"] is None


@pytest.mark.parametrize("fields", [{"backend": "silence"}, {"file": "/x.wav"}, {"voice": "/abs/v.wav"}])
def test_never_levelled_silence_a_file_or_a_path_voice(rig, mdir, fields):
    hand(mdir, "af_test", 6)
    out, rec = rig.play(**fields)
    assert out.audio.tobytes() == TONE.tobytes()
    assert rec.get("level") is None


# -- the caller: what rides on the line (say) ---------------------------------


def test_with_no_flag_and_no_cascade_nothing_rides(home):
    vhost(home, normalize=False)            # the host file is the player's to read, at take
    item = said(home)
    assert not {"normalize", "normalize_from", "normalize_lufs", "normalize_lufs_from"} & set(item)


def test_the_flag_rides_and_survives_the_queue(home):
    said(home, normalize=False)
    (q,) = queued(home)
    assert (q["normalize"], q["normalize_from"]) == (False, "flag")


def test_the_cascade_rides_with_its_layer(home, monkeypatch):
    cascade(home, monkeypatch, "seat", normalize=False, normalize_lufs=-20)
    item = said(home)
    assert (item["normalize"], item["normalize_from"]) == (False, "seat")
    assert (item["normalize_lufs"], item["normalize_lufs_from"]) == (-20, "seat")
    assert said(home, normalize=True)["normalize_from"] == "flag"


def test_a_played_file_carries_no_levelling(home, monkeypatch):
    cascade(home, monkeypatch, "session", normalize=False)
    assert "normalize" not in played(home)


def test_a_bad_flag_is_refused_before_anything_is_written(home):
    with pytest.raises(ValueError):
        said(home, normalize="off")
    assert queued(home) == []


def test_the_cli_takes_normalize_and_no_normalize(home, monkeypatch):
    import mouth
    from mouth.__main__ import main

    monkeypatch.setattr(mouth, "ensure_player", lambda d=None: False)
    assert main(["say", "--no-normalize", "hello"]) == 0
    assert main(["say", "--normalize", "hello"]) == 0
    assert [q.get("normalize") for q in queued(home)] == [False, True]


def test_a_resume_keeps_the_cut_lines_levelling_and_reads_no_cascade(home, monkeypatch):
    runs = []
    monkeypatch.setattr(voices, "cascade", lambda sid, keys: runs.append(sid) or {})
    item = {"utt": "cut1", "text": "First one. Second one here.", "voice": "af_sky", "device": "null",
            "backend": "silence", "volume": 100, "volume_from": "builtin",
            "normalize": False, "normalize_from": "seat"}
    monkeypatch.setattr(player._hold, "resume_s", lambda: 30.0)
    monkeypatch.setattr(player._hold, "asked_for_quiet", lambda *a, **k: False)
    player._resume(home["d"], item, {"played_s": 1.0, "text_played_est": "First one."})
    (q,) = queued(home)
    assert runs == [] and q["resume_of"] == "cut1"
    assert (q["normalize"], q["normalize_from"]) == (False, "seat")


# -- EARS-214 (c): a gain_db the mouth did not write is a hand's ---------------
# The mouth only ever writes gain_db with gain_from: measured, so a bare
# {"gain_db": 6} (or one beside a null or unknown gain_from) is a hand's tuning.

def write_vs(mdir, voice, body):
    p = mdir / "voices" / voice / "settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body if isinstance(body, str) else json.dumps(body))
    return p


def a_measure_that_would_write(monkeypatch):
    """A measure that always succeeds: if ensure_measured got that far, it would write."""
    calls = []
    monkeypatch.setattr(level, "measure", lambda *a, **k: calls.append(a) or (-23.0, 10.0))
    return calls


BARE = [{"gain_db": 6}, {"gain_db": 6, "gain_from": None}, {"gain_db": 6, "gain_from": "manual"}]


@pytest.mark.parametrize("body", BARE)
def test_sc1_a_bare_gain_db_is_a_hand_gain(mdir, monkeypatch, body):
    assert level.gain_db(body, -16.0) == (6.0, "hand")
    p = write_vs(mdir, "af_test", body)
    assert level._settled(p, body, -16.0)
    before, mtime = p.read_bytes(), p.stat().st_mtime_ns
    calls = a_measure_that_would_write(monkeypatch)
    assert level.ensure_measured("af_test", -16.0, "kokoro") == body
    assert p.read_bytes() == before and p.stat().st_mtime_ns == mtime and calls == []
    assert not any(k.startswith("measured_") for k in json.loads(p.read_text()))


def test_sc1_a_bare_gain_is_clamped_like_any_hand_gain():
    assert level.gain_db({"gain_db": 40}, -16.0) == (level.GAIN_LIMIT_DB, "hand")
    assert level.gain_db({"gain_db": -40, "measured_lufs": -28.0}, -16.0) == (-level.GAIN_LIMIT_DB, "hand")


@pytest.mark.parametrize("body", BARE)
def test_sc1_a_bare_gain_plays_at_that_gain_and_says_hand(rig, mdir, monkeypatch, body):
    write_vs(mdir, "af_test", body)
    a_measure_that_would_write(monkeypatch)
    lev, rec = level.for_line({"voice": "af_test"}, "kokoro", SR, d=mdir)
    assert lev.gain_db == 6.0
    assert rec == {"on": True, "on_from": "builtin", "lufs": -16.0, "gain_db": 6.0, "from": "hand"}
    out, rec = rig.play()
    assert np.allclose(out.audio, TONE * 10 ** (6 / 20), atol=1e-6)
    assert rec["level"]["from"] == "hand" and rec["level"]["gain_db"] == 6.0


@pytest.mark.parametrize("text", ['{"gain_db": "loud"}', '{"gain_db": null}', '{"gain_db": NaN}',
                                  '{"gain_db": true, "measured_lufs": -28.0}'])
def test_sc2_a_bare_bad_gain_plays_unlevelled_says_hand_and_is_never_measured(rig, mdir, monkeypatch, text):
    p = write_vs(mdir, "af_test", text)
    calls = a_measure_that_would_write(monkeypatch)
    started = []
    monkeypatch.setattr(level.threading, "Thread", lambda *a, **k: started.append(k) or None)
    lev, rec = level.for_line({"voice": "af_test"}, "kokoro", SR, d=mdir)
    assert lev is None and rec["from"] == "hand" and rec["gain_db"] is None
    out, rec = rig.play()
    assert out.audio.tobytes() == TONE.tobytes()
    assert rec["level"]["from"] == "hand" and rec["level"]["gain_db"] is None
    level.after_kept({"voice": "af_test"}, "kokoro", rec["level"], d=mdir)
    assert started == [] and calls == [] and p.read_text() == text


def test_sc3_a_measured_file_takes_target_minus_measured_not_its_stale_gain():
    vs = {"gain_db": 2.0, "measured_lufs": -28.0, "gain_from": "measured"}
    assert level.gain_db(vs, -16.0) == (pytest.approx(12.0), "measured")
    assert level.gain_db(vs, -20.0) == (pytest.approx(8.0), "measured")


@pytest.mark.parametrize("body", [None, {}, {"note": "mine"}, {"gain_from": "manual"}])
def test_sc3_a_file_with_no_gain_db_is_still_measured_lazily(mdir, monkeypatch, body):
    p = mdir / "voices" / "af_test" / "settings.json"
    if body is not None:
        write_vs(mdir, "af_test", body)
    calls = a_measure_that_would_write(monkeypatch)
    vs = level.ensure_measured("af_test", -16.0, "kokoro", now=1_917_852_900.0)
    assert len(calls) == 1 and vs["gain_from"] == "measured" and vs["measured_lufs"] == -23.0
    assert vs["gain_db"] == 7.0 and json.loads(p.read_text()) == vs
    assert {k: v for k, v in (body or {}).items() if k != "gain_from"}.items() <= vs.items()


# -- EARS-214 (a): mouth render never levels -----------------------------------
# design.md (a): the Settings audition compares a raw clip with its raw clone.

class ToneBackend:
    """A local fake backend for audio.render: TONE in two blocks, at SR."""
    name, model, sample_rate, extra_body = "tone", "tone-1", SR, {}

    def stream(self, text, sends, speed):
        yield from (TONE[: SR // 4], TONE[SR // 4:])


def test_sc8_render_never_levels_whatever_the_voices_gain(mdir, tmp_path, monkeypatch):
    from mouth import backends
    monkeypatch.setattr(backends, "resolve", lambda b, v: (ToneBackend(), v))
    raw = audio.render("af_test", "hello", tmp_path / "raw.wav")
    write_vs(mdir, "af_test", {"gain_from": "hand", "gain_db": 12})
    assert level.for_line({"voice": "af_test"}, "tone", SR, d=mdir)[0].gain_db == 12.0  # say would level it
    loud = audio.render("af_test", "hello", tmp_path / "hand.wav")
    assert raw["made"] and loud["made"] and "level" not in loud
    assert (tmp_path / "hand.wav").read_bytes() == (tmp_path / "raw.wav").read_bytes()
    assert (tmp_path / "raw.wav").read_bytes()[44:] == audio.pcm16([TONE])
