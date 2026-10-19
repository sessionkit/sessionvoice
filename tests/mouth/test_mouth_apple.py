"""The apple backend: a bare Mac speaks with its own voice when no speech server answers."""
import numpy as np
import pytest

from mouth import backends


def test_auto_picks_apple_on_a_bare_mac(monkeypatch):
    monkeypatch.setattr(backends, "apple_available", lambda: True)
    monkeypatch.setattr(backends, "server_answers", lambda url, timeout=0.3: False)
    b, v = backends.resolve("auto", "af_sky")
    assert b.name == "apple" and v == "af_sky"


def test_auto_keeps_kokoro_when_the_server_answers(monkeypatch):
    monkeypatch.setattr(backends, "apple_available", lambda: True)
    monkeypatch.setattr(backends, "server_answers", lambda url, timeout=0.3: True)
    b, _ = backends.resolve("kokoro", "af_sky")
    assert b.name == "kokoro"
    b, _ = backends.resolve("auto", "af_sky")
    assert b.name in ("kokoro", "clone")


def test_auto_without_say_does_not_pick_apple(monkeypatch):
    monkeypatch.setattr(backends, "apple_available", lambda: False)
    monkeypatch.setattr(backends, "server_answers", lambda url, timeout=0.3: False)
    b, _ = backends.resolve("auto", "af_sky")
    assert b.name != "apple"


def test_apple_asked_for_without_say_is_refused(monkeypatch):
    monkeypatch.setattr(backends, "apple_available", lambda: False)
    with pytest.raises(ValueError, match="say"):
        backends.resolve("apple", "x")


def test_server_answers_is_false_for_a_closed_port():
    assert backends.server_answers("http://127.0.0.1:9/v1", timeout=0.2) is False


@pytest.mark.skipif(not backends.apple_available(), reason="needs macOS say")
def test_apple_renders_speech_even_from_a_dash_line():
    blocks = list(backends.AppleSay().stream("-v not an option. Hello.", "af_sky", None))
    pcm = np.concatenate(blocks)
    assert blocks and all(len(b) <= int(backends.SAMPLE_RATE * backends.BLOCK_S) for b in blocks)
    assert len(pcm) > backends.SAMPLE_RATE * 0.5          # over half a second of speech
    assert float(np.abs(pcm).max()) > 0.05                # and not silence
