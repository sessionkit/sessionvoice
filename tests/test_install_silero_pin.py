"""`ears install` fetches Silero VAD pinned and checked (EARS-300): one fixed upstream commit,
a SHA-256 compared before the file lands, a temp file beside the model path that is moved
onto it only on a match, and a model already on disk left alone but checked against the pin.

No network: every test stubs the opener with fixture bytes, and a real urlopen fails loudly.
HOME is a temp dir; EARS_HOME and EARS_SILERO_ONNX are unset."""
import hashlib
import io
import os
import re
import urllib.error
import urllib.request

import pytest

from ears import cli, vad

GOOD = b"not really an onnx model, just the fixture bytes for EARS-300\n" * 100
GOOD_SHA = hashlib.sha256(GOOD).hexdigest()


@pytest.fixture(autouse=True)
def fenced(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("EARS_HOME", raising=False)
    monkeypatch.delenv("EARS_SILERO_ONNX", raising=False)

    def no_network(*a, **kw):
        raise AssertionError("a test reached the real urlopen")
    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    monkeypatch.setattr(urllib.request, "urlretrieve", no_network)
    # the apple helper is opt-in and never built here; keep install off subprocess entirely
    monkeypatch.setattr(cli.subprocess, "run", no_network)
    return tmp_path / "home"


def model(home):
    return home / ".cache" / "ears" / "silero_vad.onnx"


def leftovers(home):
    d = home / ".cache" / "ears"
    return sorted(p.name for p in d.iterdir() if p.name.startswith(".silero_vad.")) if d.exists() else []


class Opener:
    """urlopen's shape: a context manager with read(n); records the URLs it was asked for."""

    def __init__(self, body=GOOD, error=None, fail_after=None):
        self.body, self.error, self.fail_after, self.urls = body, error, fail_after, []

    def __call__(self, url, timeout=None):
        self.urls.append(url)
        if self.error is not None:
            raise self.error
        buf, fail_after = io.BytesIO(self.body), self.fail_after

        class Resp:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def read(s, n=-1):
                if fail_after is not None and buf.tell() >= fail_after:
                    raise ConnectionResetError("connection reset mid-body")
                return buf.read(n)
        return Resp()


def run_install(monkeypatch, opener, sha=GOOD_SHA):
    """`ears install` with the pinned fetch's opener stubbed and the expected sum set to `sha`."""
    real = vad.fetch_silero
    monkeypatch.setattr(cli, "fetch_silero", lambda dst, **kw: real(dst, sha256=sha, opener=opener))
    return cli.main(["install"])


# --- the pin itself ---------------------------------------------------------------------

def test_the_pin_names_one_commit_and_its_sum():
    assert re.fullmatch(r"[0-9a-f]{40}", vad.SILERO_COMMIT)
    assert re.fullmatch(r"[0-9a-f]{64}", vad.SILERO_SHA256)
    assert re.fullmatch(r"v\d+(\.\d+)*", vad.SILERO_TAG)
    assert vad.SILERO_URL.startswith("https://github.com/snakers4/silero-vad/raw/")
    assert f"/raw/{vad.SILERO_COMMIT}/" in vad.SILERO_URL
    assert "/master/" not in vad.SILERO_URL


def test_cli_uses_the_one_pin_not_its_own_copy():
    with open(cli.__file__, encoding="utf-8") as f:
        src = f.read()
    assert "snakers4" not in src and "urlretrieve" not in src
    assert cli.SILERO_URL is vad.SILERO_URL and cli.SILERO_TAG is vad.SILERO_TAG


def test_install_fetches_the_pinned_url_by_default(fenced, monkeypatch, capsys):
    opener = Opener(body=b"x")  # the real pin's sum will not match these bytes
    real = vad.fetch_silero
    monkeypatch.setattr(cli, "fetch_silero", lambda dst, **kw: real(dst, opener=opener))
    assert cli.main(["install"]) == 1
    assert opener.urls == [vad.SILERO_URL]
    out = capsys.readouterr()
    assert vad.SILERO_URL in out.out and vad.SILERO_TAG in out.out   # the URL is printed before the fetch
    assert f"expected sha256 {vad.SILERO_SHA256[:12]}" in out.err


# --- a fresh install --------------------------------------------------------------------

def test_good_hash_lands_the_model(fenced, monkeypatch, capsys):
    opener = Opener()
    assert run_install(monkeypatch, opener) == 0
    assert model(fenced).read_bytes() == GOOD
    assert leftovers(fenced) == []
    out = capsys.readouterr()
    assert out.err == ""
    assert out.out.index(vad.SILERO_URL) < out.out.index("done")


def test_bad_hash_writes_nothing_and_fails(fenced, monkeypatch, capsys):
    assert run_install(monkeypatch, Opener(body=GOOD + b"tampered")) == 1
    assert not model(fenced).exists()
    assert leftovers(fenced) == []
    err = capsys.readouterr().err
    assert len(err.splitlines()) == 1
    assert "checksum mismatch" in err and vad.SILERO_URL in err
    assert f"expected sha256 {GOOD_SHA[:12]}" in err
    assert f"got {hashlib.sha256(GOOD + b'tampered').hexdigest()[:12]}" in err


@pytest.mark.parametrize("opener", [
    Opener(error=urllib.error.HTTPError(vad.SILERO_URL, 404, "Not Found", {}, None)),
    Opener(error=OSError("Network is unreachable\nsecond line")),
    Opener(fail_after=1),   # dies mid-body, after the first chunk is already on disk
], ids=["http-404", "no-network", "mid-body"])
def test_fetch_error_writes_nothing_and_fails(fenced, monkeypatch, capsys, opener):
    assert run_install(monkeypatch, opener) == 1
    assert not model(fenced).exists()
    assert leftovers(fenced) == []
    err = capsys.readouterr().err
    assert len(err.splitlines()) == 1
    assert err.startswith("ears install: could not fetch ") and vad.SILERO_URL in err
    assert "Traceback" not in err


def test_a_failed_fetch_leaves_an_old_file_at_dst_untouched(tmp_path):
    # fetch_silero on its own: a mismatch never overwrites what is at dst
    dst = tmp_path / "m" / "silero_vad.onnx"
    dst.parent.mkdir(); dst.write_bytes(b"old")
    with pytest.raises(vad.SileroFetchError):
        vad.fetch_silero(str(dst), sha256=GOOD_SHA, opener=Opener(body=b"other"))
    assert dst.read_bytes() == b"old"
    assert [p.name for p in dst.parent.iterdir()] == ["silero_vad.onnx"]


def test_the_temp_file_is_beside_the_model(tmp_path, monkeypatch):
    import tempfile
    seen, orig = [], tempfile.mkstemp

    def spy(*a, **kw):
        fd, p = orig(*a, **kw); seen.append(p); return fd, p
    monkeypatch.setattr(tempfile, "mkstemp", spy)
    dst = tmp_path / "deep" / "silero_vad.onnx"
    vad.fetch_silero(str(dst), sha256=GOOD_SHA, opener=Opener())
    assert len(seen) == 1 and os.path.dirname(seen[0]) == str(dst.parent)
    assert dst.read_bytes() == GOOD and not os.path.exists(seen[0])


# --- a model already on disk ------------------------------------------------------------

def _present(path, body):
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(body)


def test_present_and_matching_is_left_alone(fenced, monkeypatch, capsys):
    _present(model(fenced), GOOD)
    monkeypatch.setattr(cli, "silero_pin_check", lambda p: vad.silero_pin_check(p, sha256=GOOD_SHA))
    opener = Opener()
    assert run_install(monkeypatch, opener) == 0
    assert opener.urls == []
    assert model(fenced).read_bytes() == GOOD
    out = capsys.readouterr().out
    assert f"{model(fenced)} matches the pin ({vad.SILERO_TAG}" in out


def test_present_and_different_is_left_alone_and_said(fenced, monkeypatch, capsys):
    _present(model(fenced), b"someone's own model")
    opener = Opener()
    assert run_install(monkeypatch, opener) == 0
    assert opener.urls == []
    assert model(fenced).read_bytes() == b"someone's own model"
    out = capsys.readouterr().out
    line = [l for l in out.splitlines() if l.startswith("silero:")]
    assert len(line) == 1 and "differs from the pin" in line[0] and "not replaced" in line[0]
    assert "remove it and re-run `ears install`" in line[0]


def test_present_via_ears_silero_onnx_is_checked_not_fetched(fenced, monkeypatch, tmp_path, capsys):
    own = tmp_path / "mine" / "silero_vad.onnx"
    _present(own, b"mine")
    monkeypatch.setenv("EARS_SILERO_ONNX", str(own))
    opener = Opener()
    assert run_install(monkeypatch, opener) == 0
    assert opener.urls == [] and not model(fenced).exists()
    assert f"silero: {own} differs from the pin" in capsys.readouterr().out


@pytest.mark.parametrize("kind", ["absent", "directory"])
def test_present_but_unreadable_does_not_crash(fenced, monkeypatch, tmp_path, capsys, kind):
    p = tmp_path / "x" / "silero_vad.onnx"
    if kind == "directory":
        p.mkdir(parents=True)
    monkeypatch.setattr(cli, "silero_model_path", lambda: str(p))
    opener = Opener()
    assert run_install(monkeypatch, opener) == 0
    assert opener.urls == []
    out = capsys.readouterr().out
    assert f"silero: {p} is present but could not be read" in out and "left as it is" in out


# --- the file that lands, and a model dir that cannot be written (EARS-300 review) ---------

def test_the_landed_model_has_a_plain_download_mode(tmp_path):
    # mkstemp's temp is 0600; the model must land as urlretrieve left it (0666 less umask)
    umask = os.umask(0o022)
    try:
        dst = tmp_path / "silero_vad.onnx"
        vad.fetch_silero(str(dst), sha256=GOOD_SHA, opener=Opener())
        assert (os.stat(dst).st_mode & 0o777) == 0o644
    finally:
        os.umask(umask)


def test_a_model_dir_that_cannot_be_made_is_one_line_not_a_traceback(tmp_path):
    # a disk-side failure (not only a fetch) is a SileroFetchError with one line, so install()
    # prints one stderr line and exits non-zero rather than a traceback
    (tmp_path / "ears").write_bytes(b"a file where the model dir should be")
    dst, opener = tmp_path / "ears" / "m" / "silero_vad.onnx", Opener()
    with pytest.raises(vad.SileroFetchError) as e:
        vad.fetch_silero(str(dst), sha256=GOOD_SHA, opener=opener)
    msg = str(e.value)
    assert opener.urls == [] and len(msg.splitlines()) == 1
    assert msg.startswith("could not write in ") and vad.SILERO_URL in msg and "nothing written at" in msg


def test_a_failed_replace_leaves_no_temp_and_says_so(tmp_path, monkeypatch):
    def refuse(src, dst):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(vad.os, "replace", refuse)
    dst = tmp_path / "silero_vad.onnx"
    with pytest.raises(vad.SileroFetchError, match="could not write .*nothing written at"):
        vad.fetch_silero(str(dst), sha256=GOOD_SHA, opener=Opener())
    assert list(tmp_path.iterdir()) == []
