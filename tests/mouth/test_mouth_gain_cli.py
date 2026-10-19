"""EARS-214 do-002: ``mouth gain VOICE [DB|reset]`` (design.md (b)).

No audio, no player: a temp mouth dir and voices dir. Bare shows a voice's
gain and writes nothing (SC4); a number sets a hand gain (SC5); a bad call
exits 2 and changes nothing (SC6); reset goes back to measured (SC7); the
help names all three (SC9).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mouth import __main__ as cli
from mouth import level, voices
from tests._anchor import ANCHOR_ISO

SR = 24000


@pytest.fixture
def d(tmp_path, monkeypatch) -> Path:
    """A temp mouth dir, voices dir and host settings file; no session cascade."""
    m = tmp_path / "mouth"
    m.mkdir()
    monkeypatch.setenv("MOUTH_DIR", str(m))
    monkeypatch.setenv("VOICEMODE_VOICES_DIR", str(tmp_path / "voices"))
    monkeypatch.setenv("MOUTH_AUDIO_DIR", str(tmp_path / "audio"))
    monkeypatch.setenv("MOUTH_SETTINGS", str(m / "settings.json"))
    monkeypatch.setenv("MOUTH_SESSION_BIN", "")
    level.forget_tries()
    return m


def vfile(d, voice="af_test") -> Path:
    return d / "voices" / voice / "settings.json"


def write(d, body, voice="af_test") -> Path:
    p = vfile(d, voice)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body if isinstance(body, str) else json.dumps(body))
    return p


def run(capsys, *args):
    code = cli.main(["gain", *args])
    out, err = capsys.readouterr()
    return code, (json.loads(out) if out.strip() else None), err


def tree(root: Path) -> dict:
    return {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in sorted(root.rglob("*")) if p.is_file()}


MEASURED = {"gain_db": 12.0, "measured_lufs": -28.0, "measured_at": ANCHOR_ISO,
            "measured_s": 30.0, "gain_from": "measured"}


# -- SC4: show ------------------------------------------------------------------

def test_sc4_show_a_measured_voice_echoes_the_file_and_writes_nothing(d, capsys):
    p = write(d, MEASURED)
    before = tree(d)
    code, out, err = run(capsys, "af_test")
    assert code == 0 and err == ""
    assert {k: out[k] for k in ("gain_db", "measured_lufs", "measured_at", "gain_from")} == \
        {k: MEASURED[k] for k in ("gain_db", "measured_lufs", "measured_at", "gain_from")}
    assert out["voice"] == "af_test" and out["settings"] == str(p) and out["file"] == "ok"
    assert out["level"] == {"on": True, "on_from": "builtin", "lufs": -16.0, "gain_db": 12.0, "from": "measured"}
    assert tree(d) == before


def test_sc4_show_a_voice_with_no_file_is_null_and_unmeasured(d, capsys):
    code, out, _ = run(capsys, "af_new")
    assert code == 0
    assert out["file"] == "none" and out["settings"] == str(vfile(d, "af_new"))
    assert [out[k] for k in ("gain_db", "measured_lufs", "measured_at", "gain_from")] == [None] * 4
    assert out["level"]["from"] == "unmeasured" and out["level"]["gain_db"] is None
    assert not vfile(d, "af_new").exists()


def test_show_reads_the_target_and_switch_from_the_callers_cascade(d, capsys, monkeypatch):
    write(d, MEASURED)
    monkeypatch.setattr(voices, "cascade", lambda sid, keys: {"normalize_lufs": (-20, "session")})
    assert run(capsys, "af_test")[1]["level"]["lufs"] == -20.0
    assert run(capsys, "af_test")[1]["level"]["gain_db"] == 8.0
    monkeypatch.setattr(voices, "cascade", lambda sid, keys: {"normalize": (False, "seat")})
    assert run(capsys, "af_test")[1]["level"] == {"on": False, "on_from": "seat", "lufs": -16.0}


def test_show_a_broken_file_exits_0_and_says_so(d, capsys):
    p = write(d, '{"gain_db": 6,}\n')
    code, out, _ = run(capsys, "af_test")
    assert code == 0 and out["file"] == "broken" and out["gain_db"] is None
    assert out["level"]["from"] == "hand" and p.read_text() == '{"gain_db": 6,}\n'


# -- SC5: set -------------------------------------------------------------------

def test_sc5_set_with_no_file_creates_a_hand_gain(d, capsys):
    code, out, err = run(capsys, "af_test", "3.5")
    assert code == 0 and err == ""
    assert json.loads(vfile(d).read_text()) == {"gain_db": 3.5, "gain_from": "hand"}
    assert out["gain_db"] == 3.5 and out["gain_from"] == "hand" and out["level"]["from"] == "hand"
    lev, rec = level.for_line({"voice": "af_test"}, "kokoro", SR, d=d)
    assert lev.gain_db == 3.5 and rec["from"] == "hand" and rec["gain_db"] == 3.5


def test_sc5_set_over_a_measured_file_keeps_its_other_keys(d, capsys):
    write(d, {**MEASURED, "note": "mine"})
    assert run(capsys, "af_test", "3.5")[0] == 0
    assert json.loads(vfile(d).read_text()) == {**MEASURED, "note": "mine", "gain_db": 3.5, "gain_from": "hand"}
    assert level.gain_db(level.voice_settings("af_test", d), -16.0) == (3.5, "hand")


def test_sc5_a_clone_writes_its_own_settings_json(d, tmp_path, capsys):
    clone = tmp_path / "voices" / "ivy"
    clone.mkdir(parents=True)
    code, out, _ = run(capsys, "ivy", "-6")                 # a negative value is a value, not a flag
    assert code == 0 and out["settings"] == str(clone / "settings.json")
    assert json.loads((clone / "settings.json").read_text()) == {"gain_db": -6.0, "gain_from": "hand"}
    assert not (d / "voices" / "ivy").exists()


@pytest.mark.parametrize("text, want", [("24", 24.0), ("-24", -24.0), ("+1.25", 1.25), ("0", 0.0)])
def test_the_range_is_inclusive_and_the_value_kept_as_given(d, capsys, text, want):
    assert run(capsys, "af_test", text)[0] == 0
    assert json.loads(vfile(d).read_text())["gain_db"] == want


# -- SC6: refusals --------------------------------------------------------------

@pytest.mark.parametrize("args, msg", [
    (["af_test", "loud"], "a number of dB"),
    (["af_test", "nan"], "a number of dB"),
    (["af_test", "inf"], "a number of dB"),
    (["af_test", "24.5"], "-24 to 24"),
    (["af_test", "-30"], "-24 to 24"),
    (["af_test", "3dB"], "a number of dB"),
    (["/abs/a.wav", "3"], "names no voice directory"),
    (["alice[2]", "3"], "names no voice directory"),
    (["..", "3"], "names no voice directory"),
    (["alice[2]"], "names no voice directory"),           # show too: no file to show
    (["alice[2]", "reset"], "names no voice directory"),
])
def test_sc6_a_bad_call_exits_2_with_the_reason_and_changes_nothing(d, capsys, args, msg):
    write(d, MEASURED)
    before = tree(d.parent)
    code, out, err = run(capsys, *args)
    assert code == 2 and out is None and msg in err
    assert tree(d.parent) == before


def test_sc6_minus_inf_is_refused_by_argparse(d, capsys):
    before = tree(d.parent)
    with pytest.raises(SystemExit) as e:
        cli.main(["gain", "af_test", "-inf"])
    assert e.value.code == 2 and tree(d.parent) == before


@pytest.mark.parametrize("verb", ["3", "reset"])
@pytest.mark.parametrize("text", ['{"gain_from": "hand", "gain_db": 6.0,}\n', '["hand", 6.0]\n'])
def test_sc6_a_file_a_hand_broke_is_left_byte_for_byte(d, capsys, verb, text):
    p = write(d, text)
    before = tree(d.parent)
    code, out, err = run(capsys, "af_test", verb)
    assert code == 2 and str(p) in err and "not a JSON object" in err
    assert tree(d.parent) == before


# -- SC7: reset -----------------------------------------------------------------

def test_sc7_reset_with_a_kept_measure_goes_back_to_it(d, capsys):
    write(d, {**MEASURED, "gain_db": 3.5, "gain_from": "hand"})
    code, out, _ = run(capsys, "af_test", "reset")
    vs = json.loads(vfile(d).read_text())
    assert code == 0 and vs == {**MEASURED, "gain_db": 12.0, "gain_from": "measured"}
    assert level.gain_db(vs, -16.0) == (pytest.approx(-16.0 - MEASURED["measured_lufs"]), "measured")
    assert out["gain_from"] == "measured" and out["level"]["from"] == "measured"


def test_sc7_reset_of_a_bare_gain_with_a_measure_is_measured_again(d, capsys):
    write(d, {"gain_db": 6, "measured_lufs": -28.0})       # a hand's (do-001): no gain_from
    assert run(capsys, "af_test", "reset")[0] == 0
    assert level.gain_db(level.voice_settings("af_test", d), -16.0) == (pytest.approx(12.0), "measured")


def test_sc7_reset_with_no_measure_measures_on_the_next_line(d, capsys, monkeypatch):
    p = write(d, {"gain_db": 3.5, "gain_from": "hand", "note": "mine"})
    assert run(capsys, "af_test", "reset")[0] == 0
    vs = json.loads(p.read_text())
    assert vs == {"note": "mine"} and not level._settled(p, vs, -16.0)
    calls = []
    monkeypatch.setattr(level, "measure", lambda *a, **k: calls.append(a) or (-23.0, 10.0))
    assert level.ensure_measured("af_test", -16.0, "kokoro", d=d)["gain_from"] == "measured"
    assert len(calls) == 1


def test_sc7_reset_uses_the_callers_target(d, capsys, monkeypatch):
    write(d, {**MEASURED, "gain_db": 3.5, "gain_from": "hand"})
    monkeypatch.setattr(voices, "cascade", lambda sid, keys: {"normalize_lufs": (-20, "session")})
    assert run(capsys, "af_test", "reset")[0] == 0
    assert json.loads(vfile(d).read_text())["gain_db"] == 8.0


@pytest.mark.parametrize("body", [None, MEASURED, {"note": "mine"}])
def test_sc7_reset_of_a_voice_not_hand_set_writes_nothing(d, capsys, body):
    if body is not None:
        write(d, body)
    before = tree(d.parent)
    assert run(capsys, "af_test", "reset")[0] == 0
    assert tree(d.parent) == before and (body is not None or not vfile(d).exists())


# -- SC9: help ------------------------------------------------------------------

def test_sc9_mouth_help_lists_gain(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--help"])
    assert e.value.code == 0 and "gain" in capsys.readouterr().out


def test_sc9_mouth_gain_help_names_show_set_and_reset(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["gain", "--help"])
    out = capsys.readouterr().out
    assert e.value.code == 0
    for word in ("show", "sets a hand gain", "reset", "dB", "-24 to 24"):
        assert word in out
