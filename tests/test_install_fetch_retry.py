"""A model fetch is tried again on a server error or a connection error (EARS-524).

One transient HTTP 503 on silero_vad.onnx stopped a whole kit install; the same URL served 200
forty seconds later. `vad.fetch_silero` now tries a 5xx or a connection error again after each
wait in FETCH_DELAYS (three tries in all), from an empty temp file each time. A 4xx, a checksum
mismatch and a disk error are final at once. The last failure is the same one line as before,
nothing is written at the model path, and `ears install` exits non-zero, which is what the
kit's install.sh turns into "run this again and it picks up from here".

No network: every opener here is a fake, and the waits are recorded, never slept."""
import errno
import hashlib
import http.client
import io
import socket
import urllib.error
import urllib.request

import pytest

from ears import cli, vad

GOOD = b"fixture bytes standing in for the onnx model, EARS-524\n" * 200
GOOD_SHA = hashlib.sha256(GOOD).hexdigest()
URL = vad.SILERO_URL


def http_error(code, msg):
    return urllib.error.HTTPError(URL, code, msg, {}, None)


def e503():
    return http_error(503, "Service Unavailable")


class Cut:
    """A response that serves the first `n` bytes of the good body, then the connection dies."""

    def __init__(self, n):
        self.n = n


class Seq:
    """urlopen's shape, one outcome per call: an exception is raised, bytes are served, a Cut
    serves part of the body and then resets. Records each call."""

    def __init__(self, *outcomes):
        self.outcomes, self.calls = list(outcomes), 0

    def __call__(self, url, timeout=None):
        assert url == URL
        out = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(out, BaseException):
            raise out
        body, cut = (GOOD, out.n) if isinstance(out, Cut) else (out, None)
        buf = io.BytesIO(body)

        class Resp:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def read(s, n=-1):
                if cut is not None and buf.tell() >= cut:
                    raise ConnectionResetError(errno.ECONNRESET, "Connection reset by peer")
                return buf.read(n if cut is None else min(n, cut - buf.tell()))
        return Resp()


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def no_network(*a, **kw):
        raise AssertionError("a test reached the real urlopen")
    monkeypatch.setattr(urllib.request, "urlopen", no_network)


@pytest.fixture
def dst(tmp_path):
    return tmp_path / "models" / "silero_vad.onnx"


def fetch(dst, opener, slept, delays=(0.5, 1.5)):
    vad.fetch_silero(str(dst), sha256=GOOD_SHA, opener=opener, delays=delays, sleep=slept.append)


def left(dst):
    return sorted(p.name for p in dst.parent.iterdir()) if dst.parent.exists() else []


# --- tried again, and lands --------------------------------------------------------------

def test_a_503_then_a_200_lands_the_model(dst):
    opener, slept = Seq(e503(), GOOD), []
    fetch(dst, opener, slept)
    assert dst.read_bytes() == GOOD and left(dst) == ["silero_vad.onnx"]
    assert opener.calls == 2 and slept == [0.5]


@pytest.mark.parametrize("error", [
    urllib.error.URLError(ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused")),
    urllib.error.URLError(socket.gaierror(8, "nodename nor servname provided, or not known")),
    TimeoutError("The read operation timed out"),
    socket.timeout("timed out"),
    ConnectionResetError(errno.ECONNRESET, "Connection reset by peer"),
    http.client.RemoteDisconnected("Remote end closed connection without response"),
    http.client.IncompleteRead(b"x", 10),
    http_error(500, "Internal Server Error"),
    http_error(502, "Bad Gateway"),
], ids=["refused", "dns", "timeout", "socket-timeout", "reset", "remote-closed", "incomplete",
        "http-500", "http-502"])
def test_a_transient_error_then_a_200_lands_the_model(dst, error):
    opener, slept = Seq(error, GOOD), []
    fetch(dst, opener, slept)
    assert dst.read_bytes() == GOOD and opener.calls == 2 and slept == [0.5]


def test_two_failures_then_a_200_uses_every_wait_in_order(dst):
    opener, slept = Seq(e503(), urllib.error.URLError("timed out"), GOOD), []
    fetch(dst, opener, slept)
    assert dst.read_bytes() == GOOD and opener.calls == 3 and slept == [0.5, 1.5]


def test_a_body_cut_short_never_counts_toward_the_sum(dst):
    # the first try writes part of the body to the temp file, then dies; the second try starts
    # the temp from empty, so the sum is of the second body alone and matches
    opener, slept = Seq(Cut(len(GOOD) // 2), GOOD), []
    fetch(dst, opener, slept)
    assert dst.read_bytes() == GOOD and left(dst) == ["silero_vad.onnx"]
    assert opener.calls == 2


# --- tried three times, then fails cleanly ------------------------------------------------

def test_three_503s_fail_cleanly_and_write_nothing(dst):
    opener, slept = Seq(e503(), e503(), e503()), []
    with pytest.raises(vad.SileroFetchError) as e:
        fetch(dst, opener, slept)
    assert opener.calls == 3 and slept == [0.5, 1.5]
    assert str(e.value) == f"could not fetch {URL}: HTTP Error 503: Service Unavailable; nothing written at {dst}"
    assert left(dst) == []      # no model, no .part


def test_three_cut_bodies_leave_no_part_file(dst):
    opener, slept = Seq(Cut(100), Cut(100), Cut(100)), []
    with pytest.raises(vad.SileroFetchError, match=r"^could not fetch .*Connection reset by peer; nothing written at"):
        fetch(dst, opener, slept)
    assert opener.calls == 3 and left(dst) == []


def test_the_last_error_is_the_one_said(dst):
    opener, slept = Seq(e503(), e503(), urllib.error.URLError("Network is unreachable")), []
    with pytest.raises(vad.SileroFetchError, match=r"<urlopen error Network is unreachable>; nothing written"):
        fetch(dst, opener, slept)


# --- final at once ------------------------------------------------------------------------

@pytest.mark.parametrize("code,msg", [(404, "Not Found"), (403, "Forbidden"), (410, "Gone")])
def test_a_4xx_is_not_tried_again(dst, code, msg):
    opener, slept = Seq(http_error(code, msg), GOOD), []
    with pytest.raises(vad.SileroFetchError) as e:
        fetch(dst, opener, slept)
    assert opener.calls == 1 and slept == []
    assert str(e.value) == f"could not fetch {URL}: HTTP Error {code}: {msg}; nothing written at {dst}"
    assert left(dst) == []


def test_a_5xx_then_a_404_stops_at_the_404(dst):
    opener, slept = Seq(e503(), http_error(404, "Not Found"), GOOD), []
    with pytest.raises(vad.SileroFetchError, match="HTTP Error 404"):
        fetch(dst, opener, slept)
    assert opener.calls == 2 and slept == [0.5]


def test_a_checksum_mismatch_is_not_tried_again(dst):
    opener, slept = Seq(GOOD + b"tampered", GOOD), []
    with pytest.raises(vad.SileroFetchError, match="^checksum mismatch"):
        fetch(dst, opener, slept)
    assert opener.calls == 1 and slept == [] and left(dst) == []


def test_a_disk_error_while_writing_is_not_tried_again(dst, monkeypatch):
    # a full disk is not the network's fault: another try would only fill it again
    real_open = open

    class Full(io.RawIOBase):
        def __init__(self, path):
            self.f = real_open(path, "wb")
        def write(self, b):
            raise OSError(errno.ENOSPC, "No space left on device")
        def close(self):
            self.f.close(); super().close()

    monkeypatch.setattr(vad, "open", lambda path, mode="r", *a, **kw: Full(path), raising=False)
    opener, slept = Seq(GOOD, GOOD), []
    with pytest.raises(vad.SileroFetchError, match="No space left on device; nothing written at"):
        fetch(dst, opener, slept)
    assert opener.calls == 1 and slept == []
    monkeypatch.undo()
    assert left(dst) == []


def test_no_waits_means_one_try(dst):
    opener, slept = Seq(e503(), GOOD), []
    with pytest.raises(vad.SileroFetchError, match="HTTP Error 503"):
        fetch(dst, opener, slept, delays=())
    assert opener.calls == 1 and slept == []


# --- the defaults -------------------------------------------------------------------------

def test_the_default_is_three_tries_with_short_waits():
    assert len(vad.FETCH_DELAYS) == 2
    assert all(0 < w <= 10 for w in vad.FETCH_DELAYS) and sum(vad.FETCH_DELAYS) <= 15


def test_with_no_delays_given_the_module_waits_are_read_at_call_time(dst, monkeypatch):
    monkeypatch.setattr(vad, "FETCH_DELAYS", (0.25,))
    opener, slept = Seq(e503(), GOOD), []
    vad.fetch_silero(str(dst), sha256=GOOD_SHA, opener=opener, sleep=slept.append)
    assert opener.calls == 2 and slept == [0.25]


def test_with_no_sleep_given_it_is_time_sleep(dst, monkeypatch):
    import time
    slept = []
    monkeypatch.setattr(time, "sleep", slept.append)
    vad.fetch_silero(str(dst), sha256=GOOD_SHA, opener=Seq(e503(), GOOD), delays=(0.75,))
    assert slept == [0.75] and dst.read_bytes() == GOOD


# --- through `ears install` ---------------------------------------------------------------

@pytest.fixture
def install(tmp_path, monkeypatch):
    """`ears install` with HOME in a temp dir, the fetch's opener and sleep stubbed."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("EARS_HOME", raising=False)
    monkeypatch.delenv("EARS_SILERO_ONNX", raising=False)
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **kw: pytest.fail("install ran a subprocess"))
    slept, real = [], vad.fetch_silero

    def run(opener):
        monkeypatch.setattr(cli, "fetch_silero",
                            lambda d, **kw: real(d, sha256=GOOD_SHA, opener=opener, sleep=slept.append))
        return cli.main(["install"])
    run.model, run.slept = home / ".cache" / "ears" / "silero_vad.onnx", slept
    return run


def test_install_rides_out_one_503(install, capsys):
    assert install(Seq(e503(), GOOD)) == 0
    assert install.model.read_bytes() == GOOD and install.slept == list(vad.FETCH_DELAYS[:1])
    out = capsys.readouterr()
    assert out.err == "" and out.out.rstrip().endswith("done")


def test_install_fails_non_zero_with_one_line_after_three_503s(install, capsys):
    # the non-zero exit is what the kit's install.sh turns into "run this again"
    assert install(Seq(e503(), e503(), e503())) == 1
    assert not install.model.exists() and install.slept == list(vad.FETCH_DELAYS)
    assert [p.name for p in install.model.parent.iterdir() if p.name.startswith((".silero", "silero"))] == []
    err = capsys.readouterr().err
    assert err == (f"ears install: could not fetch {URL}: HTTP Error 503: Service Unavailable; "
                   f"nothing written at {install.model}\n")
