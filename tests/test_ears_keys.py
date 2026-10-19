"""EARS-310 R6: ONE rule for which URL gets ``$OPENAI_API_KEY`` (``src/ears/keys.py``).

Before it, the ears tested ``"api.openai.com" in url`` (a substring) in two
places, ``listen/stt.py`` (the capture) and ``services.py`` (the installer's
probe), and so handed the key to any URL that merely held the name. Now both,
and the mouth (``mouth.backends.tts_key``), ask ``ears.keys.openai_key``: the
scheme is https AND ``urlparse().hostname`` is exactly api.openai.com.

No request leaves this host: the capture's client is only built, never used,
and the probe's ``urlopen`` is replaced by a recorder.
"""

from __future__ import annotations

import re
import urllib.request
from pathlib import Path

import pytest

from ears import envnames, keys, services
from tests import env_fence

SRC = Path(__file__).resolve().parents[1] / "src"
#: Fake, and shaped like no provider's key (release-scan P0 stays unchanged).
FAKE_KEY = "test-key-ears310-0123456789abcdef"
STT_KEY = "test-key-ears310-listen-fedcba9876543210"

LOOK_ALIKES = [
    "https://api.openai.com.evil.example/v1",    # a host that only starts with the name
    "http://evil.example/v1?x=api.openai.com",   # the name in the query
    "https://evil.example/api.openai.com/v1",    # the name in the path
    "https://api.openai.com@evil.example/v1",    # the name as userinfo
]
NOT_OPENAI = LOOK_ALIKES + [
    "http://api.openai.com/v1",                  # plain http: a key never goes out in clear
    "https://evil.example#@api.openai.com/v1",   # the name after a fragment
    "https://api.openai.com./v1",                # trailing-dot FQDN: conservative, no key
    "https://[::1/v1",                           # malformed: urlparse raises ValueError
    "api.openai.com/v1",                         # no scheme
    "",
    "http://127.0.0.1:2022/v1",
    "https://speech.example.com/v1",
]
OPENAI = [
    "https://api.openai.com/v1",
    "https://api.openai.com:443/v1",
    "https://API.OpenAI.com/v1",                 # hostname is lower-cased
    "https://api.openai.com",
]


@pytest.fixture(autouse=True)
def _no_runner_keys(monkeypatch, tmp_path):
    """No key from the runner: not its env, and not its ~/.sessionkit/voice/settings/stt.key (the
    ears read that file too since EARS-314), so HOME is a tmp dir."""
    for k in ("OPENAI_API_KEY", *envnames.group("EARS_LISTEN_STT_KEY"), "MOUTH_TTS_KEY"):
        env_fence.delenv(monkeypatch, k)   # both names (EARS-294)
    monkeypatch.setenv("HOME", str(tmp_path))


# -- the helper ---------------------------------------------------------------------

@pytest.mark.parametrize("url", NOT_OPENAI)
def test_openai_key_is_none_for_anything_but_openais_https_host(url, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    assert keys.is_openai(url) is False
    assert keys.openai_key(url) is None


@pytest.mark.parametrize("url", OPENAI)
def test_openai_key_is_the_key_for_openais_own_host(url, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    assert keys.is_openai(url) is True
    assert keys.openai_key(url) == FAKE_KEY


@pytest.mark.parametrize("value", ["", "   ", "\n"])
def test_a_blank_openai_key_is_no_key(value, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", value)
    assert keys.openai_key("https://api.openai.com/v1") is None


def test_unset_openai_key_is_no_key():
    assert keys.openai_key("https://api.openai.com/v1") is None


def test_outer_whitespace_is_stripped(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", f"  {FAKE_KEY}\n")
    assert keys.openai_key("https://api.openai.com/v1") == FAKE_KEY


def test_not_a_string_is_not_openai():
    assert keys.is_openai(None) is False
    assert keys.is_openai(b"https://api.openai.com/v1") is False


def test_the_helper_and_httpx_agree_on_the_host():
    """The rule is safe only if the host the helper approves is the host httpx dials
    (design.md s3a). Pinned with httpx alone: the probe's urllib does not split
    userinfo, and fails to resolve both of these before any header is sent."""
    httpx = pytest.importorskip("httpx")
    # the name as userinfo: helper no, and httpx dials evil.example. No key, rightly.
    u = "https://api.openai.com@evil.example/v1"
    assert keys.is_openai(u) is False and httpx.URL(u).host == "evil.example"
    # a backslash before the @: helper yes, and httpx dials api.openai.com. The key
    # goes only to OpenAI, so nothing leaks.
    u = "https://evil.example\\@api.openai.com/v1"
    assert keys.is_openai(u) is True and httpx.URL(u).host == "api.openai.com"


def test_keys_is_stdlib_only():
    """services.py, the installer, imports it, and the installer carries no httpx."""
    import sys

    text = (SRC / "ears" / "keys.py").read_text()
    # every import, the ones inside a function too (key_file imports stat, the warning sys)
    imports = set(re.findall(r"^\s*(?:from|import) ([\w.]+)", text, re.M))
    assert {"os", "pathlib", "urllib.parse"} <= imports, imports   # the pattern still sees them
    # its two siblings, the env-name table and the home (for the settings folder), are
    # stdlib only too
    assert re.findall(r"^\s*from (\.[\w.]*) import (.+)$", text, re.M) == [
        (".", "envnames"), (".home", "settings_dir")]
    imports -= {".", ".home"}
    for sibling in ("envnames.py", "home.py"):
        imports |= set(re.findall(r"^\s*(?:from|import) ([\w.]+)",
                                  (SRC / "ears" / sibling).read_text(), re.M))
    outside = {m for m in imports if m.split(".")[0] not in sys.stdlib_module_names}
    assert not outside, f"keys.py imports beyond the stdlib: {outside}"


def test_one_copy_of_the_host_rule():
    """No substring test on the name is left anywhere under src/ (R6: one helper)."""
    hits = [f"{p.relative_to(SRC)}:{n}" for p in SRC.rglob("*.py")
            for n, line in enumerate(p.read_text().splitlines(), 1)
            if re.search(r"""["']api\.openai\.com["']\s+in\b""", line)]
    assert hits == []


# -- the ears' capture (listen/stt.py: WhisperSTT) -----------------------------------

def _auth(url):
    stt = pytest.importorskip("ears.listen.stt")
    pytest.importorskip("httpx")
    c = stt.WhisperSTT(url, model="whisper-1")   # the client is built; no request is made
    try:
        return c._client.headers.get("Authorization")
    finally:
        c._client.close()


@pytest.mark.parametrize("url", LOOK_ALIKES + ["http://api.openai.com/v1", "http://127.0.0.1:2022/v1"])
def test_capture_sends_no_openai_key_to_a_look_alike(url, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    assert _auth(url) is None


def test_capture_sends_the_openai_key_to_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    assert _auth("https://api.openai.com/v1") == f"Bearer {FAKE_KEY}"


@pytest.mark.parametrize("url", ["https://api.openai.com/v1", "http://127.0.0.1:2022/v1",
                                 "https://api.openai.com.evil.example/v1"])
def test_capture_stt_key_still_goes_to_any_url(url, monkeypatch):
    """EARS_LISTEN_STT_KEY is the user's choice of key for the URL they set: unchanged."""
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("EARS_LISTEN_STT_KEY", STT_KEY)
    assert _auth(url) == f"Bearer {STT_KEY}"


def test_capture_sends_no_header_with_no_key():
    assert _auth("https://api.openai.com/v1") is None


# -- the installer's probe (services.probe_stt) ---------------------------------------

@pytest.fixture
def probed(monkeypatch):
    """urlopen replaced: each Request is recorded and refused, so no socket is opened."""
    got: list = []

    def urlopen(req, timeout=None):
        got.append(req)
        raise OSError("not dialled in tests")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return got


@pytest.mark.parametrize("url", LOOK_ALIKES + ["http://api.openai.com/v1"])
def test_probe_sends_no_openai_key_to_a_look_alike(url, probed, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    out = services.probe_stt(url, "whisper-1")
    [req] = probed
    assert req.get_header("Authorization") is None
    assert out.startswith("no answer: ") and FAKE_KEY not in out


def test_probe_sends_the_openai_key_to_openai(probed, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    services.probe_stt("https://api.openai.com/v1", "whisper-1")
    [req] = probed
    assert req.full_url == "https://api.openai.com/v1/audio/transcriptions"
    assert req.get_header("Authorization") == f"Bearer {FAKE_KEY}"


def test_probe_stt_key_still_goes_to_any_url(probed, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("EARS_LISTEN_STT_KEY", STT_KEY)
    services.probe_stt("https://api.openai.com.evil.example/v1", "whisper-1")
    [req] = probed
    assert req.get_header("Authorization") == f"Bearer {STT_KEY}"
