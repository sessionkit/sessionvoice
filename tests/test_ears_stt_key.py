"""EARS-314: the ears' STT key, the two gaps EARS-310 left (repro-001).

(a) R7. The installed ears service is a launch agent; its environment never holds
    a shell-exported ``$EARS_LISTEN_STT_KEY``. The mouth reads a 0600 key file
    for that (``~/.sessionkit/voice/settings/tts.key``, EARS-310 R7); the ears read the env
    only, so a key in ``~/.sessionkit/voice/settings/`` is never sent, by the capture
    (``WhisperSTT``) or the installer's probe (``services.probe_stt``).

(b) Echo. A key with a control character inside (a newline) reaches the HTTP
    library, which refuses the header and quotes it: httpx raises
    ``LocalProtocolError("Illegal header value b'Bearer <key>'")``, http.client a
    ``ValueError("Invalid header value b'Bearer <key>'")``. That text becomes
    WhisperSTT's ``STTError`` and probe_stt's ``no answer: ...``, so the key is
    printed. It must be refused by its SOURCE NAME before any request.

No request leaves this host. HOME is a tmp dir. The capture is pointed at a
loopback socket that listens and never answers (httpx checks the header only
after it connects); the probe's http.client checks it before it dials, and
``socket.create_connection`` is replaced so a dial would fail the test.
"""

from __future__ import annotations

import os
import re
import socket
from pathlib import Path

import pytest

from ears import envnames, keys, services
from ears.home import SETTINGS
from tests import env_fence

#: Fake, and shaped like no provider's key (release-scan P0 stays unchanged).
FILE_KEY = "test-key-ears314-file-0123456789abcdef"
#: The same, with a newline inside: what a mis-pasted export holds.
BAD_KEY = "test-key-ears314-ab\ncd-fedcba9876"
ENV = "EARS_LISTEN_STT_KEY"
#: The ears' key file, ``ears.keys.stt_key_path()`` (named by rca-001; the mouth's is tts.key).
KEY_FILE = Path(SETTINGS) / "stt.key"
URL_LOCAL = "http://127.0.0.1:2022/v1"


@pytest.fixture(autouse=True)
def fenced(monkeypatch, tmp_path):
    """HOME in tmp, and no key from the runner's environment."""
    for k in ("OPENAI_API_KEY", *envnames.group(ENV), "MOUTH_TTS_KEY"):
        env_fence.delenv(monkeypatch, k)   # both names (EARS-294)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert Path.home() == tmp_path
    return tmp_path


def _key_file(home: Path, text: str, mode: int = 0o600) -> Path:
    path = home / KEY_FILE
    path.parent.mkdir(parents=True)
    path.write_text(text + "\n")
    path.chmod(mode)
    return path


def _capture_auth(url: str):
    stt = pytest.importorskip("ears.listen.stt")
    pytest.importorskip("httpx")
    c = stt.WhisperSTT(url, model="whisper-1")   # the client is built; no request is made
    try:
        return c._client.headers.get("Authorization")
    finally:
        c._client.close()


@pytest.fixture
def probed(monkeypatch):
    """urlopen replaced: each Request is recorded and refused, so no socket is opened."""
    import urllib.request

    got: list = []

    def urlopen(req, timeout=None):
        got.append(req)
        raise OSError("not dialled in tests")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return got


# -- (a) R7: the ears key file -------------------------------------------------------

def test_capture_sends_the_key_files_key(fenced):
    _key_file(fenced, FILE_KEY)
    assert _capture_auth(URL_LOCAL) == f"Bearer {FILE_KEY}"


def test_probe_sends_the_key_files_key(fenced, probed):
    _key_file(fenced, FILE_KEY)
    services.probe_stt(URL_LOCAL, "whisper-1")
    [req] = probed
    assert req.get_header("Authorization") == f"Bearer {FILE_KEY}"


# -- (b) a malformed key is refused by name, never echoed ----------------------------

@pytest.fixture
def mute_port():
    """A loopback port that accepts and never answers: no STT server, no network."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    try:
        yield srv.getsockname()[1]
    finally:
        srv.close()


@pytest.fixture
def no_dial(monkeypatch):
    def dial(*a, **k):
        raise AssertionError(f"a request was dialled: {a!r}")

    monkeypatch.setattr(socket, "create_connection", dial)


def test_capture_never_echoes_a_malformed_key(monkeypatch, mute_port):
    stt = pytest.importorskip("ears.listen.stt")
    np = pytest.importorskip("numpy")
    pytest.importorskip("httpx")
    monkeypatch.setenv(ENV, BAD_KEY)
    with pytest.raises(Exception) as got:   # refused at build or at transcribe: either is fine
        c = stt.WhisperSTT(f"http://127.0.0.1:{mute_port}/v1", model="whisper-1",
                           connect_retries=0)
        try:
            c.transcribe(np.zeros(1600, dtype=np.int16))
        finally:
            c.close()
    text = str(got.value)
    for part in BAD_KEY.split("\n"):
        assert part not in text, f"the key is in the capture's error: {text!r}"
    assert ENV in text, f"the error does not name the key's source: {text!r}"


def test_probe_never_echoes_a_malformed_key(monkeypatch, no_dial):
    monkeypatch.setenv(ENV, BAD_KEY)
    out = services.probe_stt(URL_LOCAL, "whisper-1")
    assert out is not None
    for part in BAD_KEY.split("\n"):
        assert part not in out, f"the key is in the probe's answer: {out!r}"
    assert ENV in out, f"the probe's answer does not name the key's source: {out!r}"


# ====================================================================================
# fix-001: every branch of the ears' key (ears.keys.stt_key), and the shared copy.
# HOME is the autouse `fenced` tmp dir throughout; no request leaves this host.
# ====================================================================================

OTHER_KEY = "test-key-ears314-openai-00112233445566"
URL_OPENAI = "https://api.openai.com/v1"
URL_ELSEWHERE = "https://stt.example.com/v1"


@pytest.fixture(autouse=True)
def fresh_warnings(monkeypatch):
    """Each test sees its own first WARNING (one is printed per state of the file)."""
    monkeypatch.setattr(keys, "_KEY_FILE_WARNED", set())


def _write(home: Path, content, mode: int = 0o600) -> Path:
    path = home / KEY_FILE
    assert path == keys.stt_key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content)
    path.chmod(mode)
    return path


def _warnings(err: str) -> list:
    return [x for x in err.splitlines() if "WARNING" in x]


def _assert_refused(capfd, why: str):
    """No key; ONE WARNING, starting ``ears:``, naming the path and why, never the key."""
    io = capfd.readouterr()
    [w] = _warnings(io.err)
    assert w.startswith(f"ears: WARNING: the key file {keys.stt_key_path()} ") and why in w, w
    assert w.endswith("it is not used, and no key from it is sent")
    for part in (FILE_KEY, FILE_KEY[:12], FILE_KEY[-12:]):
        assert part not in io.out + io.err


# -- the path and the order -----------------------------------------------------------

def test_the_key_file_is_a_fixed_path_under_settings_ears(fenced, monkeypatch):
    """Beside ears.json, but not moved with it: $EARS_CONF moves ears.json only."""
    assert keys.stt_key_path() == fenced / SETTINGS / "stt.key"
    monkeypatch.setenv("EARS_CONF", str(fenced / "elsewhere" / "ears.json"))
    assert keys.stt_key_path() == fenced / SETTINGS / "stt.key"


def test_the_file_key_is_stripped(fenced, capfd):
    _write(fenced, f"  {FILE_KEY}\n")          # an editor's newline
    assert keys.stt_key(URL_LOCAL) == FILE_KEY
    assert capfd.readouterr().err == ""


def test_the_env_key_wins_and_the_file_is_not_even_opened(fenced, monkeypatch, capfd):
    """A refused file says nothing while the env key is in use: it is never opened."""
    monkeypatch.setenv(ENV, f" {OTHER_KEY} ")
    _write(fenced, FILE_KEY, 0o644)
    opened = []
    real_open = os.open
    monkeypatch.setattr(os, "open", lambda p, *a, **k: opened.append(str(p)) or real_open(p, *a, **k))
    assert keys.stt_key(URL_LOCAL) == OTHER_KEY
    assert str(keys.stt_key_path()) not in opened
    assert capfd.readouterr().err == ""


@pytest.mark.parametrize("url", [URL_OPENAI, URL_LOCAL, URL_ELSEWHERE, "https://api.openai.com.evil.example/v1"])
def test_the_file_key_goes_to_any_url_and_wins_over_openai(fenced, monkeypatch, url):
    monkeypatch.setenv("OPENAI_API_KEY", OTHER_KEY)
    _write(fenced, FILE_KEY)
    assert keys.stt_key(url) == FILE_KEY


def test_no_file_is_no_key_silently_then_the_openai_rule(fenced, monkeypatch, capfd):
    assert keys.stt_key(URL_LOCAL) is None
    monkeypatch.setenv("OPENAI_API_KEY", OTHER_KEY)
    assert keys.stt_key(URL_OPENAI) == OTHER_KEY
    assert keys.stt_key(URL_ELSEWHERE) is None
    assert capfd.readouterr().err == ""
    assert not (fenced / Path(SETTINGS).parts[0]).exists()   # nothing made, not even the directory


@pytest.mark.parametrize("content", ["", "   ", "\n", " \n\t\n"])
def test_an_empty_key_file_is_no_key(fenced, monkeypatch, capfd, content):
    _write(fenced, content)
    assert keys.stt_key(URL_LOCAL) is None
    monkeypatch.setenv("OPENAI_API_KEY", OTHER_KEY)
    assert keys.stt_key(URL_OPENAI) == OTHER_KEY
    assert capfd.readouterr().err == ""


@pytest.mark.parametrize("value", ["", "   ", "\n"])
def test_a_blank_env_key_falls_through_to_the_file(fenced, monkeypatch, value):
    monkeypatch.setenv(ENV, value)
    _write(fenced, FILE_KEY)
    assert keys.stt_key(URL_LOCAL) == FILE_KEY


# -- refused files: no key, one path-only warning ---------------------------------------

@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o660, 0o601, 0o610, 0o704])
def test_a_key_file_others_may_read_is_refused(fenced, monkeypatch, capfd, mode):
    _write(fenced, FILE_KEY, mode)
    monkeypatch.setenv("OPENAI_API_KEY", OTHER_KEY)
    assert keys.stt_key(URL_LOCAL) is None
    _assert_refused(capfd, f"has mode {mode:o}")
    assert keys.stt_key(URL_OPENAI) == OTHER_KEY          # refused = no key, as with no file


def test_a_key_file_owned_by_someone_else_is_refused(fenced, monkeypatch, capfd):
    """st_uid != os.getuid(), measured on the open file (a mocked fstat: no chown here)."""
    path = _write(fenced, FILE_KEY)
    real = os.fstat

    def other_owner(fd):
        st = real(fd)
        if os.path.samestat(st, os.stat(path)):
            return os.stat_result((st.st_mode, st.st_ino, st.st_dev, st.st_nlink, os.getuid() + 1,
                                   st.st_gid, st.st_size, int(st.st_atime), int(st.st_mtime),
                                   int(st.st_ctime)))
        return st

    monkeypatch.setattr(os, "fstat", other_owner)
    assert keys.stt_key(URL_LOCAL) is None
    _assert_refused(capfd, f"is owned by uid {os.getuid() + 1}, not by you (uid {os.getuid()})")


def test_a_key_file_that_is_a_directory_is_refused(fenced, capfd):
    p = keys.stt_key_path()
    p.mkdir(parents=True)
    p.chmod(0o700)
    assert keys.stt_key(URL_LOCAL) is None
    _assert_refused(capfd, "is not a regular file")


@pytest.mark.skipif(os.getuid() == 0, reason="root reads a mode-000 file")
def test_a_key_file_that_cannot_be_opened_is_refused(fenced, capfd):
    p = _write(fenced, FILE_KEY, 0o000)
    try:
        assert keys.stt_key(URL_LOCAL) is None
        _assert_refused(capfd, "cannot be opened (Permission denied)")
    finally:
        p.chmod(0o600)


def test_a_key_file_that_cannot_be_read_is_refused(fenced, monkeypatch, capfd):
    _write(fenced, FILE_KEY)

    def broken(fd, n):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(os, "read", broken)
    assert keys.stt_key(URL_LOCAL) is None
    _assert_refused(capfd, "cannot be read (Input/output error)")


def test_a_huge_key_file_is_refused_unread(fenced, capfd):
    _write(fenced, FILE_KEY + "x" * keys.KEY_FILE_MAX)
    assert keys.stt_key(URL_LOCAL) is None
    _assert_refused(capfd, "too big to be a key")


def test_a_key_file_that_is_not_text_is_refused(fenced, capfd):
    _write(fenced, FILE_KEY.encode() + b"\xff\xfe")
    assert keys.stt_key(URL_LOCAL) is None
    _assert_refused(capfd, "is not UTF-8 text")


def test_a_refused_file_warns_once_until_it_changes(fenced, capfd):
    p = _write(fenced, FILE_KEY, 0o644)
    for _ in range(3):
        assert keys.stt_key(URL_LOCAL) is None
    assert len(_warnings(capfd.readouterr().err)) == 1
    p.chmod(0o600)
    assert keys.stt_key(URL_LOCAL) == FILE_KEY
    p.chmod(0o640)
    assert keys.stt_key(URL_LOCAL) is None
    [w] = _warnings(capfd.readouterr().err)
    assert "has mode 640" in w


def test_the_key_file_is_never_written(fenced):
    """Read only: its bytes, mode and mtime stay as the user made them."""
    p = _write(fenced, FILE_KEY)
    before = os.stat(p)
    assert keys.stt_key(URL_LOCAL) == FILE_KEY
    assert _capture_auth(URL_LOCAL) == f"Bearer {FILE_KEY}"
    after = os.stat(p)
    assert (after.st_mode, after.st_mtime_ns, p.read_text()) == (before.st_mode, before.st_mtime_ns, FILE_KEY)


# -- a key a header cannot carry: refused by its SOURCE, never echoed --------------------

BAD = [f"{FILE_KEY[:12]}\n{FILE_KEY[12:]}", f"{FILE_KEY[:12]}\t{FILE_KEY[12:]}",
       f"{FILE_KEY[:12]} {FILE_KEY[12:]}", f"{FILE_KEY}é"]


@pytest.mark.parametrize("bad", BAD)
def test_stt_key_refuses_a_bad_file_key_by_path(fenced, bad):
    _write(fenced, bad)
    with pytest.raises(ValueError) as e:
        keys.stt_key(URL_LOCAL)
    msg = str(e.value)
    assert msg.startswith(f"{keys.stt_key_path()} holds a space or a character")
    assert FILE_KEY[:12] not in msg and FILE_KEY[12:] not in msg


@pytest.mark.parametrize("bad", BAD)
def test_stt_key_refuses_a_bad_openai_key_by_name(monkeypatch, bad):
    monkeypatch.setenv("OPENAI_API_KEY", bad)
    with pytest.raises(ValueError, match=r"^\$OPENAI_API_KEY holds a space") as e:
        keys.stt_key(URL_OPENAI)
    assert FILE_KEY[:12] not in str(e.value)
    assert keys.stt_key(URL_LOCAL) is None        # not OpenAI's host: never read, never refused


@pytest.mark.parametrize("bad", BAD)
def test_probe_refuses_a_bad_file_key_by_path_undialled(fenced, no_dial, bad):
    _write(fenced, bad)
    out = services.probe_stt(URL_LOCAL, "whisper-1")
    assert out.startswith(f"no key sent: {keys.stt_key_path()} holds a space"), out
    assert FILE_KEY[:12] not in out and FILE_KEY[12:] not in out


def test_probe_refuses_a_bad_openai_key_by_name_undialled(monkeypatch, no_dial):
    monkeypatch.setenv("OPENAI_API_KEY", BAD_KEY)
    out = services.probe_stt(URL_OPENAI, "whisper-1")
    assert out.startswith("no key sent: $OPENAI_API_KEY holds a space"), out
    for part in BAD_KEY.split("\n"):
        assert part not in out


def _transcribe_refused(url: str):
    """Build (it must not raise), then transcribe: the STTError, with no request made."""
    stt = pytest.importorskip("ears.listen.stt")
    np = pytest.importorskip("numpy")
    pytest.importorskip("httpx")
    c = stt.WhisperSTT(url, model="whisper-1", connect_retries=0)
    try:
        assert c._client.headers.get("Authorization") is None     # the bad key is in no header
        with pytest.raises(stt.STTError) as e:
            c.transcribe(np.zeros(1600, dtype=np.int16))
        return str(e.value)
    finally:
        c.close()


@pytest.mark.parametrize("bad", BAD)
def test_capture_refuses_a_bad_file_key_by_path_at_transcribe(fenced, no_dial, bad):
    _write(fenced, bad)
    msg = _transcribe_refused(URL_LOCAL)
    assert msg.startswith(f"whisper at {URL_LOCAL}: {keys.stt_key_path()} holds a space"), msg
    assert FILE_KEY[:12] not in msg and FILE_KEY[12:] not in msg


def test_capture_refuses_a_bad_env_key_by_name_at_transcribe(monkeypatch, no_dial):
    monkeypatch.setenv(ENV, BAD_KEY)
    msg = _transcribe_refused(URL_LOCAL)
    assert msg.startswith(f"whisper at {URL_LOCAL}: ${ENV} holds a space"), msg
    for part in BAD_KEY.split("\n"):
        assert part not in msg


def test_a_following_capture_builds_with_a_bad_key_and_refuses_at_transcribe(fenced, monkeypatch, no_dial):
    """Why the refusal waits for transcribe: make_stt runs outside main's try, so a raise
    at build would be a bare traceback; here the capture starts and its STT says why."""
    stt = pytest.importorskip("ears.listen.stt")
    np = pytest.importorskip("numpy")
    pytest.importorskip("httpx")
    monkeypatch.setenv("EARS_CONF", str(fenced / "no-ears.json"))
    monkeypatch.setenv(ENV, BAD_KEY)
    f = stt.FollowingSTT(connect_retries=0)
    try:
        with pytest.raises(stt.STTError, match=f"\\${ENV} holds a space"):
            f.transcribe(np.zeros(1600, dtype=np.int16))
    finally:
        f.close()


# -- one copy: the mouth delegates to ears.keys ----------------------------------------

def test_one_copy_of_the_key_file_reader_and_the_header_check():
    """The reader, its checks and the visible-ASCII test live in ears/keys.py only."""
    src = Path(__file__).resolve().parents[1] / "src"
    hits = [f"{p.relative_to(src)}:{n}" for p in src.rglob("*.py")
            for n, line in enumerate(p.read_text().splitlines(), 1)
            if re.search(r"""WARNING: the key file|st_mode & 0o077|["']!["'] <= c <= ["']~["']""", line)]
    assert hits and all(h.startswith("ears/keys.py:") for h in hits), hits


def test_the_mouth_warns_with_its_own_word_through_the_shared_reader(fenced, capfd):
    from mouth import backends

    p = backends.tts_key_path()
    p.parent.mkdir(parents=True)
    p.write_text(FILE_KEY)
    p.chmod(0o644)
    assert backends.tts_key("http://127.0.0.1:8880/v1") is None
    [w] = _warnings(capfd.readouterr().err)
    assert w.startswith(f"mouth: WARNING: the key file {p} has mode 644"), w
