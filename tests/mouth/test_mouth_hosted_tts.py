"""EARS-310 R1/R4: the mouth's speech endpoint can come from mouth.json, and unset is today's.

``speech_endpoint()`` (``src/mouth/backends.py``) resolves the ``kokoro``
backend's URL and model field by field, as the ears resolve theirs
(``src/ears/listen/stt.py:100-102``): the env (the session layer) wins over
``tts`` in mouth.json (the host layer), which wins over the built-in mlx-audio
on 8890 with Kokoro-82M.

R2/R6 (do-002): the key. ``tts_key()`` is ``$MOUTH_TTS_KEY``, else
``$OPENAI_API_KEY`` for ``https://api.openai.com`` only (``ears.keys``); it goes
to the ``kokoro`` endpoint only; and it never reaches an error, a log line, the
heard log, a notice or a mail, even from a server that echoes it back.

R7 (do-004): with no ``$MOUTH_TTS_KEY``, the key is the user's own
read-only key file ``~/.sessionkit/voice/settings/tts.key`` (``tts_key_file``), refused with
a path-only WARNING when it is not the user's or has a group/other mode bit.
``HOME`` is the conftest's tmp fence, so every key file here is in tmp.

R3 (do-003): under ``auto``, a voice the resolver cannot resolve goes as it is
to a REMOTE ``tts`` url (``is_remote``: not localhost, 127.0.0.0/8 or ::1), and
is refused as today with none or a loopback one (SC2).

Every request here goes to a stub HTTP server on 127.0.0.1 at an ephemeral
port. No test opens a connection to a hosted endpoint, or to a live :8890,
:2022 or :8880: ``resolve("auto", ...)`` always runs with ``server_answers``
and ``apple_available`` stubbed (as ``test_mouth_apple.py`` does).
"""

from __future__ import annotations

import html
import json
import os
import re
import socket
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from ears import keys as ears_keys
from ears.home import SETTINGS
from mouth import backends
from mouth import resolver as vp
from tests import env_fence

ROOT = Path(__file__).resolve().parents[2]
MOUTH_SCHEMA = json.loads((ROOT / "settings" / "mouth.schema.json").read_text())

BUILTIN_URL = "http://127.0.0.1:8890/v1"
BUILTIN_MODEL = "mlx-community/Kokoro-82M-bf16"
#: Fake, and shaped like no provider's key (release-scan P0 stays unchanged).
FAKE_KEY = "test-key-ears310-0123456789abcdef"


# -- fences -----------------------------------------------------------------------

@pytest.fixture(autouse=True)
def fence(tmp_path, monkeypatch):
    """No endpoint or key from the runner's env; mouth.json is a tmp file this test writes."""
    for k in ("MOUTH_KOKORO_URL", "MOUTH_KOKORO_MODEL",
              "MOUTH_TTS_KEY", "OPENAI_API_KEY"):
        env_fence.delenv(monkeypatch, k)   # both names (EARS-294)
    monkeypatch.setenv("MOUTH_SETTINGS", str(tmp_path / "mouth.json"))


@pytest.fixture
def mouth_json(tmp_path):
    """Write mouth.json (a dict as JSON, a str as raw bytes)."""
    def write(content):
        p = tmp_path / "mouth.json"
        p.write_text(content if isinstance(content, str) else json.dumps(content))
        return p
    return write


@pytest.fixture
def no_live_probe(monkeypatch):
    """`auto` on a Mac with a server that answers; the probe's URLs are recorded, never dialled."""
    probed: list = []

    def answers(url, timeout=0.3):
        probed.append(url)
        return True

    monkeypatch.setattr(backends, "apple_available", lambda: True)
    monkeypatch.setattr(backends, "server_answers", answers)
    return probed


@pytest.fixture
def stub():
    """An OpenAI-compatible /v1/audio/speech on 127.0.0.1:<ephemeral>.

    POST /v1/audio/speech answers 200 with 480 s16 zero samples (20 ms at 24 kHz),
    and records (path, headers, JSON body) for each request. Set ``.status`` to
    401, 403, 500 ... and it answers that instead, with a body that ECHOES the
    request's Authorization header: the worst case for the key leak tests (R2)."""
    got: list = []
    ns = SimpleNamespace(url=None, requests=got, status=200, echo=lambda v: v)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 (http.server's name)
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            got.append(SimpleNamespace(path=self.path, headers=dict(self.headers), body=body))
            if self.path != "/v1/audio/speech":
                self.send_response(404)
                self.end_headers()
                return
            if ns.status != 200:
                echo = f"bad request; you sent Authorization: {ns.echo(self.headers.get('Authorization'))}"
                raw = echo.encode()
                self.send_response(ns.status)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
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
    try:
        ns.url = f"http://127.0.0.1:{srv.server_address[1]}/v1"
        yield ns
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=5)


def speak(be, voice="af_sky"):
    blocks = list(be.stream("hello", voice, None))
    return np.concatenate(blocks) if blocks else np.zeros(0, dtype=np.float32)


# -- R1: url and model from mouth.json ---------------------------------------------

def test_tts_in_mouth_json_is_the_url_and_model_the_line_goes_to(stub, mouth_json):
    mouth_json({"tts": {"url": stub.url, "model": "example-tts-model"}})
    be, voice = backends.resolve("kokoro", "af_sky")
    assert (be.name, be.base_url, be.model, voice) == ("kokoro", stub.url, "example-tts-model", "af_sky")
    audio = speak(be)
    assert len(audio) == 480 and not audio.any()
    [req] = stub.requests
    assert req.path == "/v1/audio/speech"
    assert req.body == {"model": "example-tts-model", "input": "hello", "voice": "af_sky",
                        "response_format": "pcm"}


def test_a_trailing_slash_on_tts_url_is_dropped(stub, mouth_json):
    mouth_json({"tts": {"url": stub.url + "/", "model": "m"}})
    be, _ = backends.resolve("kokoro", "af_sky")
    assert be.base_url == stub.url
    speak(be)
    assert [r.path for r in stub.requests] == ["/v1/audio/speech"]


def test_tts_url_alone_still_asks_for_kokoro(mouth_json):
    """Unlike the ears' model_for(): a URL alone keeps the Kokoro model, as the env URL alone always has."""
    mouth_json({"tts": {"url": "https://speech.example.invalid/v1"}})
    assert backends.speech_endpoint() == ("https://speech.example.invalid/v1", BUILTIN_MODEL)


def test_tts_model_alone_keeps_the_builtin_url(mouth_json):
    mouth_json({"tts": {"model": "other-model"}})
    assert backends.speech_endpoint() == (BUILTIN_URL, "other-model")


# -- R1: precedence, field by field, as stt.py:100-102 ------------------------------

def test_env_url_wins_over_tts_url(stub, mouth_json, monkeypatch):
    mouth_json({"tts": {"url": "https://speech.example.invalid/v1", "model": "file-model"}})
    monkeypatch.setenv("MOUTH_KOKORO_URL", stub.url)
    be, _ = backends.resolve("kokoro", "af_sky")
    # env url + file model combine, each field on its own (as stt.py:100-102)
    assert (be.base_url, be.model) == (stub.url, "file-model")
    speak(be)
    assert [r.body["model"] for r in stub.requests] == ["file-model"]


def test_env_model_wins_over_tts_model(mouth_json, monkeypatch):
    mouth_json({"tts": {"url": "https://speech.example.invalid/v1", "model": "file-model"}})
    monkeypatch.setenv("MOUTH_KOKORO_MODEL", "env-model")
    # file url + env model combine
    assert backends.speech_endpoint() == ("https://speech.example.invalid/v1", "env-model")


def test_env_wins_over_both_fields(mouth_json, monkeypatch):
    mouth_json({"tts": {"url": "https://speech.example.invalid/v1", "model": "file-model"}})
    monkeypatch.setenv("MOUTH_KOKORO_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("MOUTH_KOKORO_MODEL", "env-model")
    assert backends.speech_endpoint() == ("http://127.0.0.1:9/v1", "env-model")


def test_env_alone_is_as_today(monkeypatch):
    """No mouth.json: the env layer works exactly as it did before EARS-310."""
    monkeypatch.setenv("MOUTH_KOKORO_URL", "http://127.0.0.1:9/v1")
    assert backends.speech_endpoint() == ("http://127.0.0.1:9/v1", BUILTIN_MODEL)
    monkeypatch.setenv("MOUTH_KOKORO_MODEL", "env-model")
    assert backends.speech_endpoint() == ("http://127.0.0.1:9/v1", "env-model")


def test_a_blank_env_value_is_unset(mouth_json, monkeypatch):
    """As the ears' `os.getenv(...) or conf.get(...)`: an empty env var falls to the file."""
    mouth_json({"tts": {"url": "https://speech.example.invalid/v1", "model": "file-model"}})
    monkeypatch.setenv("MOUTH_KOKORO_URL", "")
    monkeypatch.setenv("MOUTH_KOKORO_MODEL", "  ")
    assert backends.speech_endpoint() == ("https://speech.example.invalid/v1", "file-model")


@pytest.mark.parametrize("tts", [
    None, "https://speech.example.invalid/v1", 42, [], ["https://speech.example.invalid/v1"], {},
    {"url": ""}, {"url": "   "}, {"url": 7, "model": 8}, {"url": None, "model": None},
    {"model": ""},
])
def test_a_blank_or_malformed_tts_is_unset(mouth_json, tts):
    mouth_json({"tts": tts})
    assert backends.speech_endpoint() == (BUILTIN_URL, BUILTIN_MODEL)


@pytest.mark.parametrize("raw", ["", "{not json", "[1, 2]", "\"a string\"", "null"])
def test_a_mouth_json_that_will_not_parse_or_is_not_an_object_is_unset(mouth_json, raw):
    mouth_json(raw)
    assert backends.speech_endpoint() == (BUILTIN_URL, BUILTIN_MODEL)


def test_a_settings_dict_can_be_passed_in(monkeypatch):
    assert backends.speech_endpoint({"tts": {"url": "http://a.invalid/v1", "model": "m"}}) == (
        "http://a.invalid/v1", "m")
    assert backends.speech_endpoint({}) == (BUILTIN_URL, BUILTIN_MODEL)
    monkeypatch.setenv("MOUTH_KOKORO_URL", "http://b.invalid/v1")
    assert backends.speech_endpoint({"tts": {"url": "http://a.invalid/v1"}})[0] == "http://b.invalid/v1"


def test_mouth_json_is_read_per_line(stub, mouth_json):
    """A change applies to the next line resolved, with no restart (voices.py's rule)."""
    mouth_json({"tts": {"url": stub.url, "model": "first"}})
    assert backends.resolve("kokoro", "af_sky")[0].model == "first"
    mouth_json({"tts": {"url": stub.url, "model": "second"}})
    assert backends.resolve("kokoro", "af_sky")[0].model == "second"
    mouth_json({})
    be, _ = backends.resolve("kokoro", "af_sky")
    assert (be.base_url, be.model) == (BUILTIN_URL, BUILTIN_MODEL)


def test_no_key_is_read_from_mouth_json(stub, mouth_json):
    """`tts` holds a url and a model; anything else in it is not looked at, a key least of all."""
    mouth_json({"tts": {"url": stub.url, "model": "m", "key": FAKE_KEY, "api_key": FAKE_KEY}})
    be, _ = backends.resolve("kokoro", "af_sky")
    assert be.api_key is None
    speak(be)
    [req] = stub.requests
    assert "authorization" not in {k.lower() for k in req.headers}
    assert FAKE_KEY not in json.dumps(req.headers) + json.dumps(req.body)


# -- the auto probe uses the same endpoint (backends.py: server_answers) -------------

def test_auto_probes_the_tts_url(mouth_json, no_live_probe):
    mouth_json({"tts": {"url": "https://speech.example.invalid/v1", "model": "m"}})
    be, voice = backends.resolve("auto", "af_sky")
    assert no_live_probe == ["https://speech.example.invalid/v1"]
    assert (be.name, be.base_url, be.model, voice) == (
        "kokoro", "https://speech.example.invalid/v1", "m", "af_sky")


def test_auto_probes_the_env_url_over_the_file(mouth_json, no_live_probe, monkeypatch):
    mouth_json({"tts": {"url": "https://speech.example.invalid/v1"}})
    monkeypatch.setenv("MOUTH_KOKORO_URL", "http://127.0.0.1:9/v1")
    backends.resolve("auto", "af_sky")
    assert no_live_probe == ["http://127.0.0.1:9/v1"]


def test_auto_falls_back_to_apple_when_the_tts_url_does_not_answer(mouth_json, monkeypatch):
    mouth_json({"tts": {"url": "https://speech.example.invalid/v1"}})
    probed: list = []
    monkeypatch.setattr(backends, "apple_available", lambda: True)
    monkeypatch.setattr(backends, "server_answers", lambda url, timeout=0.3: probed.append(url) or False)
    be, _ = backends.resolve("auto", "af_sky")
    assert be.name == "apple" and probed == ["https://speech.example.invalid/v1"]


def test_a_clone_keeps_its_own_url_not_tts(mouth_json, no_live_probe, monkeypatch):
    """A clone's URL is its profile's (VOICEMODE_MLX_AUDIO_BASE_URL), never tts.url."""
    ivy = SimpleNamespace(base_url="http://clone.invalid/v1", model="clone-model",
                          ref_audio="/voices/ivy/default.wav", ref_text="hello")
    monkeypatch.setattr(vp, "resolve_voice",
                        lambda expr: SimpleNamespace(kind="clone", resolved="ivy", profile=ivy))
    mouth_json({"tts": {"url": "https://speech.example.invalid/v1", "model": "m"}})
    be, _ = backends.resolve("auto", "ivy")
    assert (be.name, be.base_url, be.model) == ("clone", "http://clone.invalid/v1", "clone-model")


# -- R4 / SC3: unconfigured is byte-identical to today ------------------------------

def test_unconfigured_is_the_builtin_with_no_key(monkeypatch):
    """No tts, no env: the local mlx-audio on 8890 with Kokoro-82M, and no key, even with
    OPENAI_API_KEY in the env (it is never sent to a non-OpenAI host)."""
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    assert backends.speech_endpoint() == (BUILTIN_URL, BUILTIN_MODEL)
    be, voice = backends.resolve("kokoro", "af_sky")
    assert (be.name, be.base_url, be.model, be.api_key, be.extra_body, voice) == (
        "kokoro", BUILTIN_URL, BUILTIN_MODEL, None, {}, "af_sky")


def test_unconfigured_auto_probes_the_builtin_url_only(no_live_probe):
    be, _ = backends.resolve("auto", "af_sky")
    assert no_live_probe == [BUILTIN_URL]
    assert (be.base_url, be.model, be.api_key) == (BUILTIN_URL, BUILTIN_MODEL, None)


def test_unconfigured_with_an_empty_mouth_json(mouth_json, no_live_probe):
    mouth_json({})
    be, _ = backends.resolve("auto", "af_sky")
    assert (no_live_probe, be.base_url, be.model, be.api_key) == (
        [BUILTIN_URL], BUILTIN_URL, BUILTIN_MODEL, None)


def test_the_request_is_todays_no_key_header(stub, monkeypatch):
    """The wire request with no tts and no key is today's: the same body, no Authorization.
    (Pointed at the stub by today's env var, since the built-in URL is the live :8890.)"""
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("MOUTH_KOKORO_URL", stub.url)
    be, _ = backends.resolve("kokoro", "af_sky")
    speak(be)
    [req] = stub.requests
    assert "authorization" not in {k.lower() for k in req.headers}
    assert req.body == {"model": BUILTIN_MODEL, "input": "hello", "voice": "af_sky",
                        "response_format": "pcm"}


# -- the schema: one additive property ------------------------------------------------

def _valid_tts(value) -> bool:
    """Just enough of JSON Schema for the `tts` property (jsonschema is not a dependency)."""
    s = MOUTH_SCHEMA["properties"]["tts"]
    if not isinstance(value, dict):
        return False
    if any(k not in value for k in s["required"]):
        return False
    if s["additionalProperties"] is False and set(value) - set(s["properties"]):
        return False
    for k, v in value.items():
        p = s["properties"][k]
        if p["type"] == "string" and not isinstance(v, str):
            return False
        if "pattern" in p and not re.search(p["pattern"], v):
            return False
        if len(v) < p.get("minLength", 0):
            return False
    return True


def test_schema_has_tts_as_an_object_of_url_and_model():
    p = MOUTH_SCHEMA["properties"]
    t = p["tts"]
    assert t["title"] and t["description"] and t["type"] == "object"
    assert set(t["properties"]) == {"url", "model"}
    assert t["required"] == ["url"] and t["additionalProperties"] is False
    assert t["properties"]["url"]["pattern"] == "^https?://"
    assert "default" not in json.dumps(t)          # the built-in is the code's, stated in the description
    assert list(p).index("tts") == list(p).index("voice") + 1
    for word in ("MOUTH_KOKORO_URL", "MOUTH_KOKORO_MODEL", "8890", "key"):
        assert word in t["description"], word


def test_schema_accepts_the_docs_example_and_refuses_a_key():
    assert _valid_tts({"url": "https://speech.example.com/v1", "model": "example-tts-model"})
    assert _valid_tts({"url": "http://127.0.0.1:8880/v1"})
    assert not _valid_tts({"url": "https://speech.example.com/v1", "key": FAKE_KEY})
    assert not _valid_tts({"url": "ftp://speech.example.com/v1"})
    assert not _valid_tts({"model": "m"})
    assert not _valid_tts({"url": "https://speech.example.com/v1", "model": ""})
    assert not _valid_tts("https://speech.example.com/v1")


def test_schema_examples_resolve_as_the_code_reads_them(mouth_json):
    """What the schema accepts, speech_endpoint() reads as written."""
    ex = {"url": "https://speech.example.com/v1", "model": "example-tts-model"}
    assert _valid_tts(ex)
    mouth_json({"tts": ex})
    assert backends.speech_endpoint() == (ex["url"], ex["model"])


# -- R2: the key, from the env only, to the tts endpoint only ------------------------

OPENAI_URL = "https://api.openai.com/v1"
LOOK_ALIKES = [
    "https://api.openai.com.evil.example/v1",
    "http://evil.example/v1?x=api.openai.com",
    "https://evil.example/api.openai.com/v1",
    "https://api.openai.com@evil.example/v1",
    "http://api.openai.com/v1",
]
OTHER_KEY = "test-key-ears310-openai-fedcba9876543210"


def auth(req):
    return {k.lower(): v for k, v in req.headers.items()}.get("authorization")


def test_tts_key_is_sent_as_bearer_to_the_tts_url(stub, mouth_json, monkeypatch):
    mouth_json({"tts": {"url": stub.url, "model": "example-tts-model"}})
    monkeypatch.setenv("MOUTH_TTS_KEY", FAKE_KEY)
    be, _ = backends.resolve("kokoro", "af_sky")
    assert be.api_key == FAKE_KEY
    speak(be)
    [req] = stub.requests
    assert auth(req) == f"Bearer {FAKE_KEY}"
    assert FAKE_KEY not in json.dumps(req.body)        # in the header, nowhere else


def test_tts_key_goes_with_an_env_url_too(stub, monkeypatch):
    monkeypatch.setenv("MOUTH_KOKORO_URL", stub.url)
    monkeypatch.setenv("MOUTH_TTS_KEY", f"  {FAKE_KEY}\n")   # pasted with a newline
    be, _ = backends.resolve("kokoro", "af_sky")
    speak(be)
    assert [auth(r) for r in stub.requests] == [f"Bearer {FAKE_KEY}"]


def test_openai_key_is_not_sent_to_a_non_openai_tts_url(stub, mouth_json, monkeypatch):
    mouth_json({"tts": {"url": stub.url, "model": "m"}})
    monkeypatch.setenv("OPENAI_API_KEY", OTHER_KEY)
    be, _ = backends.resolve("kokoro", "af_sky")
    assert be.api_key is None
    speak(be)
    [req] = stub.requests
    assert auth(req) is None and OTHER_KEY not in json.dumps(req.headers)


def test_the_file_key_is_ignored_and_the_env_key_is_sent(stub, mouth_json, monkeypatch):
    mouth_json({"tts": {"url": stub.url, "model": "m", "key": OTHER_KEY, "api_key": OTHER_KEY}})
    monkeypatch.setenv("MOUTH_TTS_KEY", FAKE_KEY)
    speak(backends.resolve("kokoro", "af_sky")[0])
    [req] = stub.requests
    assert auth(req) == f"Bearer {FAKE_KEY}"
    assert OTHER_KEY not in json.dumps(req.headers) + json.dumps(req.body)


def test_tts_key_unit_openai_host_gets_openai_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", OTHER_KEY)
    assert backends.tts_key(OPENAI_URL) == OTHER_KEY
    assert backends.tts_key("https://API.OpenAI.com:443/v1") == OTHER_KEY


@pytest.mark.parametrize("url", LOOK_ALIKES + [BUILTIN_URL, "https://speech.example.com/v1", ""])
def test_tts_key_unit_no_openai_key_for_any_other_url(url, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", OTHER_KEY)
    assert backends.tts_key(url) is None


@pytest.mark.parametrize("url", [OPENAI_URL] + LOOK_ALIKES + [BUILTIN_URL])
def test_tts_key_unit_own_key_wins_and_goes_to_any_url(url, monkeypatch):
    """$MOUTH_TTS_KEY is for whatever URL is configured, OpenAI's included."""
    monkeypatch.setenv("OPENAI_API_KEY", OTHER_KEY)
    monkeypatch.setenv("MOUTH_TTS_KEY", FAKE_KEY)
    assert backends.tts_key(url) == FAKE_KEY


@pytest.mark.parametrize("value", ["", "   ", "\n"])
def test_tts_key_unit_blank_is_no_key(value, monkeypatch):
    monkeypatch.setenv("MOUTH_TTS_KEY", value)
    assert backends.tts_key(BUILTIN_URL) is None
    monkeypatch.setenv("OPENAI_API_KEY", OTHER_KEY)    # a blank own key falls to the OpenAI rule
    assert backends.tts_key(OPENAI_URL) == OTHER_KEY


def test_resolve_hands_openai_key_only_to_openais_host(mouth_json, monkeypatch):
    """resolve() builds the backend; it makes no request (no hosted endpoint is dialled)."""
    monkeypatch.setenv("OPENAI_API_KEY", OTHER_KEY)
    mouth_json({"tts": {"url": OPENAI_URL, "model": "example-tts-model"}})
    assert backends.resolve("kokoro", "alloy")[0].api_key == OTHER_KEY
    for url in LOOK_ALIKES:
        mouth_json({"tts": {"url": url, "model": "example-tts-model"}})
        assert backends.resolve("kokoro", "alloy")[0].api_key is None, url


@pytest.mark.parametrize("bad", [f"{FAKE_KEY[:8]}\n{FAKE_KEY[8:]}", f"{FAKE_KEY[:8]}\t{FAKE_KEY[8:]}",
                                 f"{FAKE_KEY[:8]} {FAKE_KEY[8:]}", f"{FAKE_KEY}é"])
def test_a_key_a_header_cannot_carry_is_refused_by_name_never_echoed(bad, monkeypatch):
    """httpx would raise ``Illegal header value b'Bearer <the key>'``: the key in the error.
    So the mouth refuses it first, naming the variable and never the key."""
    monkeypatch.setenv("MOUTH_KOKORO_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("MOUTH_TTS_KEY", bad)
    with pytest.raises(ValueError) as e:
        backends.resolve("kokoro", "af_sky")
    assert "$MOUTH_TTS_KEY" in str(e.value)
    assert FAKE_KEY[:8] not in str(e.value) and FAKE_KEY[8:] not in str(e.value)


def test_a_bad_openai_key_is_refused_by_name(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", f"{OTHER_KEY[:8]}\n{OTHER_KEY[8:]}")
    with pytest.raises(ValueError, match=r"\$OPENAI_API_KEY") as e:
        backends.tts_key(OPENAI_URL)
    assert OTHER_KEY[8:] not in str(e.value)


def test_a_clone_never_gets_the_key(mouth_json, no_live_probe, monkeypatch):
    ivy = SimpleNamespace(base_url="http://clone.invalid/v1", model="clone-model",
                          ref_audio="/voices/ivy/default.wav", ref_text="hello")
    monkeypatch.setattr(vp, "resolve_voice",
                        lambda expr: SimpleNamespace(kind="clone", resolved="ivy", profile=ivy))
    monkeypatch.setenv("MOUTH_TTS_KEY", FAKE_KEY)
    monkeypatch.setenv("OPENAI_API_KEY", OTHER_KEY)
    for backend in ("auto", "clone"):
        be, _ = backends.resolve(backend, "ivy")
        assert (be.name, be.api_key) == ("clone", None)


# -- R2: the key never leaks: errors, repr, CLI output, heard log, notice, mail -------

@pytest.mark.parametrize("status", [401, 403])
def test_refused_key_error_leaves_the_echoing_body_out(stub, status, monkeypatch):
    monkeypatch.setenv("MOUTH_KOKORO_URL", stub.url)
    monkeypatch.setenv("MOUTH_TTS_KEY", FAKE_KEY)
    stub.status = status
    with pytest.raises(RuntimeError) as e:
        speak(backends.resolve("kokoro", "af_sky")[0])
    assert str(e.value) == f"kokoro: HTTP {status}: the server refused the key"
    assert auth(stub.requests[0]) == f"Bearer {FAKE_KEY}"   # it was sent, and echoed back


def test_a_401_with_no_key_says_none_was_sent(stub, monkeypatch):
    monkeypatch.setenv("MOUTH_KOKORO_URL", stub.url)
    stub.status = 401
    with pytest.raises(RuntimeError) as e:
        speak(backends.resolve("kokoro", "af_sky")[0])
    assert str(e.value) == ("kokoro: HTTP 401: the server wants a key, and none was sent "
                            "($MOUTH_TTS_KEY)")


def test_any_other_status_keeps_the_body_with_the_key_replaced(stub, monkeypatch):
    monkeypatch.setenv("MOUTH_KOKORO_URL", stub.url)
    monkeypatch.setenv("MOUTH_TTS_KEY", FAKE_KEY)
    stub.status = 500
    with pytest.raises(RuntimeError) as e:
        speak(backends.resolve("kokoro", "af_sky")[0])
    assert str(e.value) == "kokoro: HTTP 500: bad request; you sent Authorization: Bearer [key]"


def test_redaction_comes_before_the_200_character_cut():
    """Cut first and a key across the cut would leave its head in the message."""
    body = "x" * 190 + FAKE_KEY + " trailing"
    msg = backends.refused("kokoro", 500, body, FAKE_KEY)
    assert FAKE_KEY[:10] not in msg and msg.endswith("x" * 10 + "[key]" + " trailing"[:5])
    assert backends.refused("kokoro", 500, "plain", None) == "kokoro: HTTP 500: plain"
    assert backends.refused("clone", 401, "body", None) == (
        "clone: HTTP 401: the server wants a key, and none was sent")


def _lowhex(pct: str) -> str:
    """A percent-encoding with lower-case hex digits, as some encoders write it."""
    return re.sub(r"%[0-9A-F]{2}", lambda m: m.group().lower(), pct)


#: Fake, and shaped like no provider's key, but holding every character an echo re-spells.
ODD_KEY = 'test-key/ears310+0123"&<abc>\\='


@pytest.mark.parametrize("spell", [
    lambda k: k,                                               # as typed
    lambda k: json.dumps(k)[1:-1],                             # a JSON echo (Python, Go)
    lambda k: json.dumps(k)[1:-1].replace("/", "\\/"),         # a JSON echo that escapes / (PHP)
    lambda k: urllib.parse.quote(k, safe=""),                  # a query echo
    lambda k: urllib.parse.quote_plus(k),                      # a form echo
    lambda k: _lowhex(urllib.parse.quote(k, safe="")),         # %2f: hex is case-blind (.NET)
    lambda k: _lowhex(urllib.parse.quote_plus(k)),
    lambda k: html.escape(k),                                  # an HTML error page
    lambda k: html.escape(k, quote=False),
], ids=["raw", "json", "json-slash", "quote", "quote-plus", "quote-lowhex", "quote-plus-lowhex",
        "html", "html-noquote"])
def test_redaction_catches_every_spelling_an_echo_gives_the_key(spell):
    """do-002 review: only the typed key was replaced, so an escaped or encoded
    echo of it went into the said row whole. Every spelling is now [key]."""
    echoed = spell(ODD_KEY)
    msg = backends.refused("kokoro", 400, f"you sent: Bearer {echoed} (bad voice)", ODD_KEY)
    assert msg == "kokoro: HTTP 400: you sent: Bearer [key] (bad voice)"
    assert "ears310" not in msg


def test_a_json_escaped_echo_from_the_stub_is_redacted(stub, monkeypatch):
    """The same, end to end through OpenAISpeech.stream: the stub echoes the header
    as PHP's json_encode would, with every / escaped."""
    key = "test-key/ears310+0123456789abcdef=="
    monkeypatch.setenv("MOUTH_KOKORO_URL", stub.url)
    monkeypatch.setenv("MOUTH_TTS_KEY", key)
    stub.status = 500
    stub.echo = lambda v: json.dumps(v)[1:-1].replace("/", "\\/")
    with pytest.raises(RuntimeError) as e:
        speak(backends.resolve("kokoro", "af_sky")[0])
    assert str(e.value) == "kokoro: HTTP 500: bad request; you sent Authorization: Bearer [key]"
    assert auth(stub.requests[0]) == f"Bearer {key}"   # it was sent, and echoed back escaped


def test_a_connect_error_carries_no_key(monkeypatch):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()                                          # a closed ephemeral port: refused
    monkeypatch.setenv("MOUTH_KOKORO_URL", f"http://127.0.0.1:{port}/v1")
    monkeypatch.setenv("MOUTH_TTS_KEY", FAKE_KEY)
    with pytest.raises(Exception) as e:
        speak(backends.resolve("kokoro", "af_sky")[0])
    assert FAKE_KEY not in str(e.value) and FAKE_KEY not in repr(e.value)


def test_repr_never_shows_the_key():
    be = backends.OpenAISpeech("kokoro", "https://speech.example.com/v1/", "m", api_key=FAKE_KEY)
    assert repr(be) == ("OpenAISpeech(name='kokoro', base_url='https://speech.example.com/v1', "
                        "model='m', api_key=set)")
    assert FAKE_KEY not in repr(be) and FAKE_KEY not in f"{be!r} {be}"
    assert repr(backends.OpenAISpeech("kokoro", "http://x/v1", "m")).endswith("api_key=none)")


def _cli(args, env_extra, tmp_path):
    env = {**os.environ, **env_extra}
    return subprocess.run([sys.executable, "-m", "mouth", *args], capture_output=True, text=True,
                          env=env, cwd=tmp_path, timeout=60, check=False)


def test_mouth_resolve_output_has_no_key(stub, tmp_path):
    r = _cli(["resolve", "--backend", "kokoro", "af_sky"],
             {"MOUTH_KOKORO_URL": stub.url, "MOUTH_TTS_KEY": FAKE_KEY}, tmp_path)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["backend"] == "kokoro"
    assert FAKE_KEY not in r.stdout + r.stderr
    assert stub.requests == []                         # resolve makes no request


def test_mouth_render_on_a_401_has_no_key(stub, tmp_path):
    stub.status = 401
    out = tmp_path / "r.wav"
    r = _cli(["render", "--backend", "kokoro", "--voice", "af_sky", "--out", str(out), "hello"],
             {"MOUTH_KOKORO_URL": stub.url, "MOUTH_TTS_KEY": FAKE_KEY}, tmp_path)
    assert r.returncode != 0 and not out.exists()
    assert "HTTP 401: the server refused the key" in r.stderr
    assert FAKE_KEY not in r.stdout + r.stderr
    assert [auth(q) for q in stub.requests] == [f"Bearer {FAKE_KEY}"]


@pytest.fixture
def player_env(tmp_path, monkeypatch, heard_box):
    """A CLI caller (agent ivy, session s-test, whose box exists) and a player on a null device."""
    import mouth  # noqa: F401  (the package the player serves)

    d, logs, q = tmp_path / "mouthdir", tmp_path / "logs", tmp_path / "queue"
    for sub in ("new", "cur", "tmp"):
        (q / sub).mkdir(parents=True)
    for k, v in {"MOUTH_DIR": d, "MOUTH_MAILBOX": q,
                 "MOUTH_LOG_DIR": logs, "MOUTH_DEVICE": "null",
                 "MOUTH_IDLE_S": "1", "MOUTH_RESUME_S": "0",
                 "VOICEMODE_AGENT": "ivy", "VOICEMODE_SESSION_ID": "s-test"}.items():
        monkeypatch.setenv(k, str(v))
    for k in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID", "MOUTH_MAIL_ALLOW"):
        env_fence.delenv(monkeypatch, k)   # both names (EARS-294)
    agents = Path(os.environ["MOUTH_FAKE_MAIL_ROOT"])
    box = agents / "s-test"
    for sub in ("new", "cur", "tmp"):
        (box / sub).mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(d=d, logs=logs, q=q, box=box, roots=[tmp_path, heard_box.parent])


def _every_file_bytes(roots, but=()) -> bytes:
    """Every file under ``roots``, but the ones in ``but`` (the key file a test made)."""
    skip = {Path(b).resolve() for b in but}
    return b"\n".join(p.read_bytes() for r in roots for p in Path(r).rglob("*")
                      if p.is_file() and p.resolve() not in skip)


@pytest.mark.parametrize("source", ["env", "file"])
@pytest.mark.parametrize("status,said_as", [(401, "the server refused the key"),
                                            (500, "Authorization: Bearer [key]")])
def test_play_one_end_to_end_a_refused_line_never_echoes_the_key(stub, player_env, status, said_as,
                                                                  source, monkeypatch, capfd):
    """One line through the player against a server that echoes the key: the said row,
    the drop notice, player.log (stdout) and every file written carry no key. The key
    comes from the env, or (do-004) from the key file, the installed player's way."""
    import email
    import email.policy

    import mouth
    from mouth import player

    monkeypatch.setenv("MOUTH_KOKORO_URL", stub.url)
    if source == "env":
        monkeypatch.setenv("MOUTH_TTS_KEY", FAKE_KEY)
    else:
        _write_key_file(FAKE_KEY)
    stub.status = status
    item = mouth.say("hello there", device="null", backend="kokoro", spawn=False)
    queued = b"".join(p.read_bytes() for p in (player_env.q / "new").iterdir())
    assert queued and FAKE_KEY.encode() not in queued        # the key is never on the line
    t = threading.Thread(target=player.serve, kwargs={"d": player_env.d, "idle_exit_s": 0.3},
                         daemon=True)
    t.start()
    t.join(15)
    assert not t.is_alive()

    assert [auth(r) for r in stub.requests] == [f"Bearer {FAKE_KEY}"]   # sent, and echoed back
    rows = [json.loads(x) for f in sorted(player_env.logs.glob("heard_*.jsonl"))
            for x in f.read_text().splitlines()]
    [said] = [r for r in rows if r["kind"] == "said" and r["utt"] == item["utt"]]
    assert said["reason"] == "error" and said_as in said["detail"]
    assert said["notice"].startswith("sent s-test@")
    [m] = [email.message_from_bytes(p.read_bytes(), policy=email.policy.default)
           for p in (player_env.box / "new").iterdir()]
    assert said_as in m.get_content()
    io = capfd.readouterr()
    assert FAKE_KEY not in io.out + io.err
    assert FAKE_KEY.encode() not in _every_file_bytes(player_env.roots, but=[backends.tts_key_path()])


# -- R3 / SC2: a hosted endpoint takes its own voice names (do-003) -----------------
#
# Under auto, a voice the resolver cannot resolve goes as it is to a REMOTE tts
# url (backends.is_remote), and is refused as today with none or a loopback one.
# Remote urls here are .invalid / example names and TEST-NET addresses: resolve()
# makes no request, and every probe is stubbed (no_live_probe).

REMOTE = "https://tts.example.invalid/v1"

LOOPBACK_URLS = [
    BUILTIN_URL,
    "http://localhost:8880/v1",
    "http://LOCALHOST:8880/v1",
    "http://127.0.0.2/v1",
    "http://127.255.255.254:9000/v1",
    "http://[::1]:8880/v1",
    "http://[::ffff:127.0.0.1]:8880/v1",
    "http://0.0.0.0:8880/v1",
    "http://[::]:8880/v1",
]


@pytest.mark.parametrize("url", [
    REMOTE,
    "https://speech.example.com/v1",
    "https://api.openai.com/v1",
    "http://192.0.2.10:8880/v1",          # TEST-NET-1: another machine, so remote
    "https://[2001:db8::1]/v1",
    "https://localhost.example.invalid/v1",   # a name that only starts with localhost
    "http://127.0.0.1.example.invalid/v1",    # ... or with a loopback address
])
def test_is_remote_for_a_host_that_is_not_this_machine(url):
    assert backends.is_remote(url) is True


@pytest.mark.parametrize("url", LOOPBACK_URLS + ["", "not a url", "http:///v1", "http://[::1/v1"])
def test_is_remote_is_false_for_loopback_and_for_no_host(url):
    """Loopback (ipaddress-decided), unspecified, no host, unparseable: the refusal stays."""
    assert backends.is_remote(url) is False


def test_sc2_a_hosted_voice_is_accepted_with_a_remote_tts_url(mouth_json, no_live_probe):
    mouth_json({"tts": {"url": REMOTE, "model": "example-tts-model"}})
    be, voice = backends.resolve("auto", "coral")
    assert (be.name, be.base_url, be.model, voice) == ("kokoro", REMOTE, "example-tts-model", "coral")
    assert no_live_probe == [REMOTE]


@pytest.mark.parametrize("name", ["coral", "ash", "marin", "Some-Provider-Voice_2"])
def test_sc2_any_unresolvable_name_goes_as_it_is_to_a_remote_url(name, mouth_json, no_live_probe):
    mouth_json({"tts": {"url": REMOTE}})
    be, voice = backends.resolve("auto", name)
    assert (be.name, voice) == ("kokoro", name)


def test_sc2_a_remote_env_url_passes_the_voice_through_too(no_live_probe, monkeypatch):
    """design Q5: the env url is the same endpoint, so the same rule holds for it."""
    monkeypatch.setenv("MOUTH_KOKORO_URL", "https://speech.example.invalid/v1")
    be, voice = backends.resolve("auto", "coral")
    assert (be.name, be.base_url, voice) == ("kokoro", "https://speech.example.invalid/v1", "coral")


def test_sc2_the_passed_through_name_is_the_one_on_the_wire(stub, mouth_json, no_live_probe,
                                                             monkeypatch):
    """The stub is loopback, so is_remote is stubbed True for it: the body carries 'coral' as asked."""
    monkeypatch.setattr(backends, "is_remote", lambda url: url == stub.url)
    mouth_json({"tts": {"url": stub.url, "model": "example-tts-model"}})
    be, voice = backends.resolve("auto", "coral")
    speak(be, voice)
    [req] = stub.requests
    assert req.body == {"model": "example-tts-model", "input": "hello", "voice": "coral",
                        "response_format": "pcm"}


def test_sc2_a_typo_on_a_remote_url_is_still_loud(stub, mouth_json, no_live_probe, monkeypatch):
    """No silent fall to another voice: the provider's 400 is the line's error."""
    monkeypatch.setattr(backends, "is_remote", lambda url: url == stub.url)
    mouth_json({"tts": {"url": stub.url}})
    stub.status = 400
    be, voice = backends.resolve("auto", "corall")
    with pytest.raises(RuntimeError, match="kokoro: HTTP 400"):
        speak(be, voice)


def test_sc2_refused_as_today_with_no_tts_configured(no_live_probe):
    with pytest.raises(ValueError, match="Unresolvable voice 'coral'"):
        backends.resolve("auto", "coral")
    assert no_live_probe == [BUILTIN_URL]


@pytest.mark.parametrize("url", LOOPBACK_URLS)
def test_sc2_refused_as_today_with_a_loopback_tts_url(url, mouth_json, no_live_probe):
    mouth_json({"tts": {"url": url}})
    with pytest.raises(ValueError, match="Unresolvable voice 'coral'"):
        backends.resolve("auto", "coral")


def test_sc2_refused_as_today_with_a_loopback_env_url(no_live_probe, monkeypatch):
    monkeypatch.setenv("MOUTH_KOKORO_URL", "http://127.0.0.1:8880/v1")
    with pytest.raises(ValueError, match="Unresolvable voice 'coral'"):
        backends.resolve("auto", "coral")


@pytest.mark.parametrize("configured", [False, True])
def test_sc2_a_kokoro_voice_still_falls_through_either_way(configured, mouth_json, no_live_probe):
    """bf_isabella: the resolver does not know it, Kokoro does. Unchanged, remote or not."""
    if configured:
        mouth_json({"tts": {"url": REMOTE}})
    be, voice = backends.resolve("auto", "bf_isabella")
    assert (be.name, voice) == ("kokoro", "bf_isabella")


@pytest.mark.parametrize("name", ["alloy", "nova", "af_sky"])
def test_sc2_a_provider_native_name_resolves_as_today_with_a_remote_url(name, mouth_json,
                                                                        no_live_probe):
    mouth_json({"tts": {"url": REMOTE}})
    be, voice = backends.resolve("auto", name)
    assert (be.name, voice) == ("kokoro", name)


def test_sc2_backend_clone_still_refuses_an_unknown_voice_with_a_remote_url(mouth_json,
                                                                           no_live_probe):
    mouth_json({"tts": {"url": REMOTE}})
    with pytest.raises(ValueError, match="Unresolvable voice 'coral'"):
        backends.resolve("clone", "coral")


def test_sc2_a_clone_voice_still_goes_to_its_clone_with_a_remote_url(mouth_json, no_live_probe,
                                                                    monkeypatch):
    ivy = SimpleNamespace(base_url="http://clone.invalid/v1", model="clone-model",
                          ref_audio="/voices/ivy/default.wav", ref_text="hello")
    monkeypatch.setattr(vp, "resolve_voice",
                        lambda expr: SimpleNamespace(kind="clone", resolved="ivy", profile=ivy))
    mouth_json({"tts": {"url": REMOTE}})
    be, _ = backends.resolve("auto", "ivy")
    assert (be.name, be.base_url) == ("clone", "http://clone.invalid/v1")


def test_sc2_backend_kokoro_is_unchanged_it_never_asked_the_resolver(no_live_probe):
    """--backend kokoro already sent any name to any url, as before."""
    be, voice = backends.resolve("kokoro", "coral")
    assert (be.name, be.base_url, voice) == ("kokoro", BUILTIN_URL, "coral")


def test_sc2_a_remote_url_that_does_not_answer_on_a_mac_is_apple(mouth_json, monkeypatch):
    """The fallback runs before voice resolution, as today: a hosted voice name is spoken by say."""
    mouth_json({"tts": {"url": REMOTE}})
    monkeypatch.setattr(backends, "apple_available", lambda: True)
    monkeypatch.setattr(backends, "server_answers", lambda url, timeout=0.3: False)
    be, voice = backends.resolve("auto", "coral")
    assert (be.name, voice) == ("apple", "coral")


# -- R5: the docs (do-003): placeholders only, and the lines they cite stay true -----

def _hosted_section() -> str:
    m = re.search(r"^### A hosted speech endpoint instead\n(.*?)(?=^## |\Z)",
                  (ROOT / "docs" / "models.md").read_text(), re.S | re.M)
    assert m, "docs/models.md has no '### A hosted speech endpoint instead'"
    return m.group(1)


def test_the_speech_server_page_points_a_stranger_at_the_hosted_section():
    # EARS-447: the README's Requirements moved to docs/dependencies.md, and the speech
    # server's detail, this pointer included, to docs/speech-server.md, which it links.
    page = (ROOT / "docs" / "speech-server.md").read_text()
    assert "](speech-server.md)" in (ROOT / "docs" / "dependencies.md").read_text()
    assert "models.md#a-hosted-speech-endpoint-instead" in page
    for word in ("`tts`", f"~/{SETTINGS}/mouth.json", "MOUTH_TTS_KEY"):
        assert word in page, word
    _hosted_section()


def test_the_speech_server_page_names_the_kokoro_default_the_code_holds():
    """The page says the mouth's Kokoro voices are on 127.0.0.1:8890, and KOKORO_URL is that
    server. No file:line in the public text (EARS-447: receipts live in the review)."""
    page = (ROOT / "docs" / "speech-server.md").read_text()
    assert "127.0.0.1:8890" in page and "the mouth's Kokoro voices" in page
    assert backends.KOKORO_URL == "http://127.0.0.1:8890/v1"


def test_the_docs_example_is_a_placeholder_the_schema_and_code_accept(mouth_json):
    s = _hosted_section()
    [block] = re.findall(r"```json\n(.*?)```", s, re.S)
    ex = json.loads(block)
    assert ex["tts"] == {"url": "https://speech.example.com/v1", "model": "example-tts-model"}
    assert _valid_tts(ex["tts"])
    mouth_json(ex)
    assert backends.speech_endpoint() == ("https://speech.example.com/v1", "example-tts-model")
    assert backends.is_remote(ex["tts"]["url"])


def test_the_docs_hold_no_real_key_or_account():
    """Placeholders only: no provider-shaped key, the key as <your-key>, example.com hosts."""
    s = _hosted_section()
    readme = (ROOT / "README.md").read_text()
    page = (ROOT / "docs" / "speech-server.md").read_text()
    for text in (s, readme, page):
        assert not re.search(r"\bsk-[A-Za-z0-9_-]{8,}", text)
    assert "MOUTH_TTS_KEY=<your-key>" in s
    assert "launchctl setenv MOUTH_TTS_KEY '<your-key>'" in s
    for host in re.findall(r"https?://([^/\s`\"']+)", s):
        assert host in ("speech.example.com", "api.openai.com", "127.0.0.1:8890"), host


def test_the_docs_name_the_mouth_agent_launchd_runs():
    from ears import services

    s = _hosted_section()
    label = services.PREFIX + "mouth"
    assert f'launchctl kickstart -k "gui/$(id -u)/{label}"' in s
    assert services.spec("mouth")["ProgramArguments"][-2:] == ["mouth", "mail"]
    # no key among them: PATH, EARS_PY and install's marker (EARS-407), nothing from the shell
    assert set(services.spec("mouth")["EnvironmentVariables"]) == {"PATH", "EARS_PY", services.MARKER[0]}


# -- R7: the key file (do-004) -----------------------------------------------------
#
# $MOUTH_TTS_KEY, else ~/.sessionkit/voice/settings/tts.key, else the OpenAI rule.
# HOME is the conftest's tmp fence (heard_box), so the real ~/.sessionkit/voice/settings/ is
# never read or written: _write_key_file asserts its path is under that HOME.

@pytest.fixture(autouse=True)
def fresh_key_warnings(monkeypatch):
    """Each test sees its own first WARNING (the mouth prints one per state of the file)."""
    monkeypatch.setattr(ears_keys, "_KEY_FILE_WARNED", set())   # ONE set, shared with the ears (EARS-314)


def _write_key_file(content, mode=0o600) -> Path:
    path = backends.tts_key_path()
    home = Path(os.environ["HOME"]).resolve()
    assert "fence" in str(home) and path.resolve().is_relative_to(home), path   # tmp, never the real one
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content)
    os.chmod(path, mode)
    return path


def _warnings(err: str) -> list:
    return [x for x in err.splitlines() if "WARNING" in x]


def test_the_key_file_is_a_fixed_path_under_settings_mouth():
    assert backends.tts_key_path() == Path(os.environ["HOME"]) / SETTINGS / "tts.key"


def test_the_key_file_is_used_and_sent_as_bearer(stub, mouth_json, capfd):
    mouth_json({"tts": {"url": stub.url, "model": "example-tts-model"}})
    _write_key_file(f"  {FAKE_KEY}\n")                     # an editor's newline, stripped
    be, _ = backends.resolve("kokoro", "af_sky")
    assert be.api_key == FAKE_KEY
    speak(be)
    [req] = stub.requests
    assert auth(req) == f"Bearer {FAKE_KEY}"
    assert FAKE_KEY not in json.dumps(req.body)
    io = capfd.readouterr()
    assert io.err == "" and FAKE_KEY not in io.out


def test_the_env_key_wins_over_the_file(stub, monkeypatch):
    monkeypatch.setenv("MOUTH_KOKORO_URL", stub.url)
    monkeypatch.setenv("MOUTH_TTS_KEY", FAKE_KEY)
    _write_key_file(OTHER_KEY)
    speak(backends.resolve("kokoro", "af_sky")[0])
    assert [auth(r) for r in stub.requests] == [f"Bearer {FAKE_KEY}"]


def test_with_the_env_key_set_the_file_is_not_even_read(monkeypatch, capfd):
    """A refused file says nothing while the env key is in use: it is never opened."""
    monkeypatch.setenv("MOUTH_TTS_KEY", FAKE_KEY)
    _write_key_file(OTHER_KEY, 0o644)
    opened = []
    real_open = os.open
    monkeypatch.setattr(os, "open", lambda p, *a, **k: opened.append(str(p)) or real_open(p, *a, **k))
    assert backends.tts_key(BUILTIN_URL) == FAKE_KEY
    assert str(backends.tts_key_path()) not in opened
    assert capfd.readouterr().err == ""


@pytest.mark.parametrize("url", [OPENAI_URL] + LOOK_ALIKES + [BUILTIN_URL])
def test_the_file_key_is_the_tts_key_and_goes_to_any_url(url, monkeypatch):
    """The same rule as the env key: to whatever URL is configured, and it wins over
    $OPENAI_API_KEY even for OpenAI's own host."""
    monkeypatch.setenv("OPENAI_API_KEY", OTHER_KEY)
    _write_key_file(FAKE_KEY)
    assert backends.tts_key(url) == FAKE_KEY


def test_no_key_file_is_no_key_and_says_nothing(capfd, monkeypatch):
    assert not backends.tts_key_path().exists()
    assert backends.tts_key(BUILTIN_URL) is None
    monkeypatch.setenv("OPENAI_API_KEY", OTHER_KEY)       # as before the file: the OpenAI rule
    assert backends.tts_key(OPENAI_URL) == OTHER_KEY
    assert backends.tts_key("https://speech.example.com/v1") is None
    assert capfd.readouterr().err == ""


@pytest.mark.parametrize("content", ["", "   ", "\n", " \n\t\n"])
def test_an_empty_key_file_is_no_key(content, capfd, monkeypatch):
    _write_key_file(content)
    assert backends.tts_key(BUILTIN_URL) is None
    assert backends.resolve("kokoro", "af_sky")[0].api_key is None
    monkeypatch.setenv("OPENAI_API_KEY", OTHER_KEY)
    assert backends.tts_key(OPENAI_URL) == OTHER_KEY
    assert capfd.readouterr().err == ""


def _assert_refused(capfd, why: str):
    """No key; ONE WARNING, which names the path and why and holds no part of the key."""
    path = str(backends.tts_key_path())
    io = capfd.readouterr()
    [w] = _warnings(io.err)
    assert w.startswith(f"mouth: WARNING: the key file {path} ") and why in w, w
    assert w.endswith("it is not used, and no key from it is sent")
    for part in (FAKE_KEY, FAKE_KEY[:12], FAKE_KEY[-12:]):
        assert part not in io.out + io.err


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o660, 0o606, 0o601, 0o610, 0o704])
def test_a_key_file_others_may_read_is_refused_with_a_path_only_warning(mode, capfd, monkeypatch):
    _write_key_file(FAKE_KEY, mode)
    monkeypatch.setenv("OPENAI_API_KEY", OTHER_KEY)
    assert backends.tts_key(BUILTIN_URL) is None
    assert backends.tts_key("https://speech.example.com/v1") is None
    _assert_refused(capfd, f"has mode {mode:o}")
    assert backends.tts_key(OPENAI_URL) == OTHER_KEY      # refused = no key, exactly as before


def test_a_key_file_owned_by_someone_else_is_refused(capfd, monkeypatch):
    """st_uid != os.getuid(), measured on the open file (a mocked fstat: no chown here)."""
    path = _write_key_file(FAKE_KEY)
    real = os.fstat

    def other_owner(fd):
        st = real(fd)
        if os.path.samestat(st, os.stat(path)):
            return os.stat_result((st.st_mode, st.st_ino, st.st_dev, st.st_nlink, os.getuid() + 1,
                                   st.st_gid, st.st_size, int(st.st_atime), int(st.st_mtime),
                                   int(st.st_ctime)))
        return st

    monkeypatch.setattr(os, "fstat", other_owner)
    assert backends.tts_key(BUILTIN_URL) is None
    _assert_refused(capfd, f"is owned by uid {os.getuid() + 1}, not by you (uid {os.getuid()})")


def test_a_key_file_that_is_a_directory_is_refused(capfd):
    p = backends.tts_key_path()
    p.mkdir(parents=True)
    os.chmod(p, 0o700)
    assert backends.tts_key(BUILTIN_URL) is None
    _assert_refused(capfd, "is not a regular file")


@pytest.mark.skipif(os.getuid() == 0, reason="root reads a mode-000 file")
def test_a_key_file_that_cannot_be_read_is_refused(capfd):
    _write_key_file(FAKE_KEY, 0o000)
    try:
        assert backends.tts_key(BUILTIN_URL) is None
        _assert_refused(capfd, "cannot be opened (Permission denied)")
    finally:
        os.chmod(backends.tts_key_path(), 0o600)


def test_a_huge_key_file_is_refused_unread(capfd):
    _write_key_file(FAKE_KEY + "x" * (64 * 1024))
    assert backends.tts_key(BUILTIN_URL) is None
    _assert_refused(capfd, "too big to be a key")


def test_a_key_file_that_is_not_text_is_refused(capfd):
    _write_key_file(FAKE_KEY.encode() + b"\xff\xfe")
    assert backends.tts_key(BUILTIN_URL) is None
    _assert_refused(capfd, "is not UTF-8 text")


def test_a_refused_file_warns_once_until_it_changes(capfd):
    """Read once a line: the player's log gets one WARNING, not one a line. A
    fix applies at once, and breaking it again warns again."""
    p = _write_key_file(FAKE_KEY, 0o644)
    for _ in range(3):
        assert backends.tts_key(BUILTIN_URL) is None
    assert len(_warnings(capfd.readouterr().err)) == 1
    os.chmod(p, 0o600)
    assert backends.tts_key(BUILTIN_URL) == FAKE_KEY
    os.chmod(p, 0o640)
    assert backends.tts_key(BUILTIN_URL) is None
    [w] = _warnings(capfd.readouterr().err)
    assert "has mode 640" in w


@pytest.mark.parametrize("bad", [f"{FAKE_KEY[:8]}\n{FAKE_KEY[8:]}", f"{FAKE_KEY[:8]} {FAKE_KEY[8:]}",
                                 f"{FAKE_KEY}é"])
def test_a_file_key_a_header_cannot_carry_is_refused_by_path_never_echoed(bad, capfd):
    _write_key_file(bad)
    with pytest.raises(ValueError) as e:
        backends.resolve("kokoro", "af_sky")
    msg = str(e.value)
    assert msg.startswith(f"{backends.tts_key_path()} holds a space or a character")
    assert FAKE_KEY[:8] not in msg and FAKE_KEY[8:] not in msg
    io = capfd.readouterr()
    assert FAKE_KEY[8:] not in io.out + io.err


def test_the_key_file_is_never_written_by_the_mouth(stub, mouth_json):
    """Read only: reading it leaves its bytes, mode and mtime as the user made them,
    and a missing one stays missing (nor is its directory made)."""
    mouth_json({"tts": {"url": stub.url, "model": "m"}})
    speak(backends.resolve("kokoro", "af_sky")[0])
    assert not backends.tts_key_path().parent.exists()
    p = _write_key_file(FAKE_KEY)
    before = os.stat(p)
    speak(backends.resolve("kokoro", "af_sky")[0])
    after = os.stat(p)
    assert (after.st_mode, after.st_mtime_ns, p.read_text()) == (before.st_mode, before.st_mtime_ns, FAKE_KEY)


def test_mouth_render_with_the_key_file_on_a_401_has_no_key(stub, tmp_path):
    """The CLI (its own process, the same fenced HOME): sent from the file, never printed."""
    _write_key_file(FAKE_KEY)
    stub.status = 401
    out = tmp_path / "r.wav"
    r = _cli(["render", "--backend", "kokoro", "--voice", "af_sky", "--out", str(out), "hello"],
             {"MOUTH_KOKORO_URL": stub.url}, tmp_path)
    assert r.returncode != 0 and not out.exists()
    assert "HTTP 401: the server refused the key" in r.stderr
    assert FAKE_KEY not in r.stdout + r.stderr
    assert [auth(q) for q in stub.requests] == [f"Bearer {FAKE_KEY}"]


def test_mouth_render_with_a_refused_key_file_warns_by_path_and_sends_no_key(stub, tmp_path):
    _write_key_file(FAKE_KEY, 0o644)
    out = tmp_path / "r.wav"
    r = _cli(["render", "--backend", "kokoro", "--voice", "af_sky", "--out", str(out), "hello"],
             {"MOUTH_KOKORO_URL": stub.url}, tmp_path)
    assert r.returncode == 0, r.stderr
    [w] = _warnings(r.stderr)
    assert str(backends.tts_key_path()) in w and "has mode 644" in w
    assert FAKE_KEY not in r.stdout + r.stderr
    assert [auth(q) for q in stub.requests] == [None]


def test_the_docs_show_the_key_file_made_private_first():
    s = _hosted_section()
    assert f"install -m 600 /dev/null ~/{SETTINGS}/tts.key" in s
    assert "launchctl setenv MOUTH_TTS_KEY '<your-key>'" in s and "hand test" in s
    assert f"~/{SETTINGS}/tts.key" in (ROOT / "docs" / "speech-server.md").read_text()
    assert f"~/{SETTINGS}/tts.key" in MOUTH_SCHEMA["properties"]["tts"]["description"]
    assert "EARS-310 do-004" not in s                       # do-003's placeholders are filled
