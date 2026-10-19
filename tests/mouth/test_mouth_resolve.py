"""A voice the mouth cannot resolve is refused, loudly; it no longer falls to Kokoro.

The silent Kokoro fallback is an error. The one
fall-through kept is a voice the mouth's own Kokoro speaks that the resolver
does not know (bf_isabella). The resolver is the mouth's own copy (EARS-292), so
this file runs on every install: no VoiceMode, no skip (TS-01).
"""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

import mouth
from mouth import backends, player
from mouth import resolver as vp
from mouth.__main__ import main

IVY = SimpleNamespace(base_url="http://clone.test/v1", model="clone-model",
                      ref_audio="/voices/ivy/default.wav", ref_text="hello")
CLONE_A_2 = SimpleNamespace(base_url="http://clone.test/v1", model="clone-model",
                            ref_audio="/voices/demo/clone-a/02-line.wav", ref_text="then I'll face it")


def fake_resolve(expr):
    if expr == "ivy":
        return SimpleNamespace(kind="clone", resolved="ivy", profile=IVY)
    if expr == "demo/clone-a/02-line.wav":
        return SimpleNamespace(kind="clone", resolved="demo/clone-a", profile=CLONE_A_2)
    if expr in ("af_sky", "am_santa"):
        return SimpleNamespace(kind="provider", resolved=expr, profile=None)
    raise vp.Unresolvable(expr, f"no voice named {expr!r}", ["ivy"] if expr.startswith("iv") else [])


@pytest.fixture(autouse=True)
def resolver(monkeypatch):
    monkeypatch.setattr(vp, "resolve_voice", fake_resolve)
    # The premise of every test here: a speech server answers. Without this the
    # "auto" path probes the host's live port, and a Mac with `say` and no
    # server there picks apple (EARS-327); test_mouth_apple.py covers that path.
    monkeypatch.setattr(backends, "server_answers", lambda url, timeout=0.3: True)


def test_a_kokoro_voice_goes_to_kokoro():
    be, voice = backends.resolve("auto", "af_sky")
    assert (be.name, voice) == ("kokoro", "af_sky")


def test_a_clone_and_a_chosen_reference_clip_go_to_the_clone_server():
    be, _ = backends.resolve("auto", "ivy")
    assert be.name == "clone" and be.extra_body["ref_audio"] == "/voices/ivy/default.wav"
    be, _ = backends.resolve("auto", "demo/clone-a/02-line.wav")
    assert be.extra_body["ref_audio"] == "/voices/demo/clone-a/02-line.wav"


def test_the_mouths_own_kokoro_voice_still_falls_through():
    be, voice = backends.resolve("auto", "bf_isabella")  # in KOKORO, unknown to the resolver
    assert (be.name, voice) == ("kokoro", "bf_isabella")


@pytest.mark.parametrize("expr", ["ivyy", "clone-a/02-line.wav", "nobody"])
def test_anything_else_is_refused_with_the_resolvers_reason(expr):
    with pytest.raises(ValueError, match="Unresolvable voice"):
        backends.resolve("auto", expr)


def test_the_did_you_mean_survives():
    with pytest.raises(ValueError, match="Did you mean: ivy"):
        backends.resolve("auto", "ivyy")


def test_an_explicit_kokoro_backend_is_not_second_guessed():
    be, voice = backends.resolve("kokoro", "whatever_the_server_has")
    assert (be.name, voice) == ("kokoro", "whatever_the_server_has")


def test_the_player_closes_a_bad_voice_as_an_error_and_says_nothing(tmp_path, monkeypatch):
    d, logs = tmp_path / "mouth", tmp_path / "logs"
    monkeypatch.setenv("MOUTH_DIR", str(d))
    monkeypatch.setenv("MOUTH_LOG_DIR", str(logs))
    item = mouth.say("hello", voice="ivyy", device="null", spawn=False)
    t = threading.Thread(target=player.serve, kwargs={"d": d, "idle_exit_s": 0.3}, daemon=True)
    t.start()
    t.join(10)
    recs = [json.loads(x) for f in sorted(logs.glob("heard_*.jsonl")) for x in f.read_text().splitlines()]
    assert not [r for r in recs if r["kind"] == "saying"]
    said = next(r for r in recs if r["kind"] == "said" and r["utt"] == item["utt"])
    assert said["reason"] == "error" and "Did you mean: ivy" in said["detail"]


def test_mouth_resolve_prints_the_clip_or_exits_2(capsys):
    assert main(["resolve", "demo/clone-a/02-line.wav"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["backend"] == "clone" and out["ref_audio"] == "/voices/demo/clone-a/02-line.wav"
    assert main(["resolve", "clone-a/02-line.wav"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("mouth resolve: Unresolvable voice 'clone-a/02-line.wav'")
