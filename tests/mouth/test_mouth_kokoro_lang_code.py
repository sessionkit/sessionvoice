"""EARS-543: the kokoro request carries ``lang_code`` from the voice's prefix.

A request with no ``lang_code`` gets mlx-audio's default, ``a``, so a British
voice (``bf_lily``) was phonemised as American. ``resolve()`` now derives the
code from the FINAL voice's first letter, against Kokoro's own table
(``backends.KOKORO_LANG_CODES``, vendored from mlx-audio 0.5.9's
``LANG_CODES``). Any other voice sends no ``lang_code``: the body it always was.
The clone path keeps ``lang_code: auto``.

Every request goes to a stub HTTP server on 127.0.0.1 at an ephemeral port;
none reaches a live :8890. ``resolve("auto", ...)`` runs with
``server_answers`` and ``apple_available`` stubbed.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from mouth import backends
from mouth import resolver as vp
from tests import env_fence

MODEL = "mlx-community/Kokoro-82M-bf16"


@pytest.fixture(autouse=True)
def fence(tmp_path, monkeypatch):
    """No endpoint or key from the runner's env; no mouth.json; never a live probe."""
    for k in ("MOUTH_KOKORO_URL", "MOUTH_KOKORO_MODEL", "MOUTH_TTS_KEY", "OPENAI_API_KEY"):
        env_fence.delenv(monkeypatch, k)
    monkeypatch.setenv("MOUTH_SETTINGS", str(tmp_path / "mouth.json"))
    monkeypatch.setattr(backends, "apple_available", lambda: True)
    monkeypatch.setattr(backends, "server_answers", lambda url, timeout=0.3: True)


@pytest.fixture
def stub(monkeypatch):
    """/v1/audio/speech on 127.0.0.1:<ephemeral>, recording each JSON body; the
    mouth's kokoro URL points at it."""
    got: list = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 (http.server's name)
            n = int(self.headers.get("Content-Length") or 0)
            got.append(json.loads(self.rfile.read(n) or b"{}"))
            pcm = b"\x00\x00" * 480
            self.send_response(200)
            self.send_header("Content-Type", "audio/pcm")
            self.send_header("Content-Length", str(len(pcm)))
            self.end_headers()
            self.wfile.write(pcm)

        def log_message(self, *a):  # quiet
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/v1"
    monkeypatch.setenv("MOUTH_KOKORO_URL", url)
    try:
        yield SimpleNamespace(url=url, bodies=got)
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=5)


def said(backend, voice, stub):
    """Resolve, speak one line, and return (backend name, the one body on the wire)."""
    be, v = backends.resolve(backend, voice)
    list(be.stream("tomato, schedule, water", v, None))
    [body] = stub.bodies
    return be.name, body


def body(voice, **extra):
    return {"model": MODEL, "input": "tomato, schedule, water", "voice": voice,
            "response_format": "pcm", **extra}


# -- the table ----------------------------------------------------------------------

def test_the_table_is_kokoros_own_nine_codes():
    """mlx-audio 0.5.9 mlx_audio/tts/models/kokoro/pipeline.py:77-91 LANG_CODES."""
    assert sorted(backends.KOKORO_LANG_CODES) == sorted("abefhipjz")


@pytest.mark.parametrize("voice, code", [
    ("bf_lily", "b"), ("bm_george", "b"), ("af_heart", "a"), ("am_adam", "a"),
    ("ef_dora", "e"), ("ff_siwis", "f"), ("hf_alpha", "h"), ("if_sara", "i"),
    ("pf_dora", "p"), ("jf_alpha", "j"), ("zf_xiaobei", "z"),
])
def test_every_known_prefix_maps_to_its_letter(voice, code):
    assert backends.kokoro_lang_code(voice) == code


@pytest.mark.parametrize("voice", [
    "nonsense_voice", "alloy", "coral", "xf_nobody", "cf_nobody", "bx_lily", "bf_",
    "bf-lily", "BF_LILY", "bf_lily+af_sky", "ivy", "demo/clone-a", "",
])
def test_anything_else_has_no_code(voice):
    assert backends.kokoro_lang_code(voice) is None


# -- the wire -----------------------------------------------------------------------

def test_bf_lily_asks_for_the_british_pipeline(stub):
    assert said("kokoro", "bf_lily", stub) == ("kokoro", body("bf_lily", lang_code="b"))


def test_an_american_voice_asks_for_a(stub):
    assert said("kokoro", "af_heart", stub) == ("kokoro", body("af_heart", lang_code="a"))


@pytest.mark.parametrize("voice, code", [("ff_siwis", "f"), ("jf_alpha", "j")])
def test_a_non_english_voice_asks_for_its_language(stub, voice, code):
    assert said("kokoro", voice, stub) == ("kokoro", body(voice, lang_code=code))


@pytest.mark.parametrize("voice", ["nonsense_voice", "xf_nobody"])
def test_an_unknown_prefix_sends_the_body_it_always_did(stub, voice):
    name, sent = said("kokoro", voice, stub)
    assert "lang_code" not in sent
    assert (name, sent) == ("kokoro", body(voice))


def test_auto_derives_the_code_from_the_resolved_voice(stub, monkeypatch):
    """The code comes from the FINAL voice: a name the resolver maps to bf_lily is British."""
    def fake(expr):
        if expr == "lily":
            return SimpleNamespace(kind="provider", resolved="bf_lily", profile=None)
        raise vp.Unresolvable(expr, f"no voice named {expr!r}", [])
    monkeypatch.setattr(vp, "resolve_voice", fake)
    assert said("auto", "lily", stub) == ("kokoro", body("bf_lily", lang_code="b"))


# -- the clone path is untouched ----------------------------------------------------

@pytest.mark.parametrize("voice", ["ivy", "bf_twin"])
def test_a_clone_still_sends_auto(stub, monkeypatch, voice):
    """Even a clone named like a Kokoro voice keeps lang_code auto: the clone branch
    is decided before the kokoro one."""
    profile = SimpleNamespace(base_url=stub.url, model="clone-model",
                              ref_audio="/voices/x/default.wav", ref_text="hello")
    monkeypatch.setattr(vp, "resolve_voice", lambda expr: SimpleNamespace(
        kind="clone", resolved=expr, profile=profile))
    name, sent = said("auto", voice, stub)
    assert name == "clone"
    assert sent == {"model": "clone-model", "input": "tomato, schedule, water", "voice": voice,
                    "response_format": "pcm", "ref_audio": "/voices/x/default.wav",
                    "ref_text": "hello", "stream": True, "streaming_interval": 0.3,
                    "lang_code": "auto"}
