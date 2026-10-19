"""CallSource: a live Delta call read as a mic, from a fake ear leg that speaks
a Delta bot's ear-leg protocol (probe; {"call": N} -> header, A frames, E)."""
import json
import os
import socket
import struct
import tempfile
import threading

import numpy as np
import pytest

from ears.listen.sources import FRAME_SAMPLES, CallSource, SourceError, open_source


def _serve(path, calls, frames, end=b"connection closed", rate=16000):
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(4)
    got = []

    def one(c):
        f = c.makefile("rb")
        hdr = json.loads(f.readline())
        got.append(hdr)
        if hdr.get("probe"):
            c.sendall((json.dumps({"ok": True, "calls": calls, "rate": rate}) + "\n").encode())
        elif hdr.get("call") in calls:
            c.sendall((json.dumps({"ok": True, "call": hdr["call"], "rate": rate}) + "\n").encode())
            for pcm in frames:
                c.sendall(b"A" + struct.pack(">I", len(pcm)) + pcm)
            if end is not None:
                c.sendall(b"E" + struct.pack(">I", len(end)) + end)
        else:
            c.sendall((json.dumps({"ok": False, "error": "not live", "calls": calls}) + "\n").encode())
        c.close()

    def loop():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            one(c)

    threading.Thread(target=loop, daemon=True).start()
    return srv, got


@pytest.fixture
def sock():
    d = tempfile.mkdtemp(prefix="cs-")
    p = os.path.join(d, "call-ears.sock")
    yield p
    if os.path.exists(p):
        os.unlink(p)


def pcm(value, samples=320):  # the bot's frames: 20 ms at 16 kHz
    return (np.ones(samples, dtype="<i2") * value).tobytes()


def test_reads_his_audio_as_mic_frames_then_ends(sock):
    srv, got = _serve(sock, [1139], [pcm(i) for i in range(1, 7)])  # 6 x 320 = 1920 = 4 x 480
    try:
        src = CallSource(None, socks=[sock])
        assert (src.name, src.device, src.call) == ("call", "call:1139", 1139)
        out = list(src.frames())
        assert [len(f) for f in out] == [FRAME_SAMPLES] * 4
        assert int(out[0][0]) == 1 and int(out[-1][-1]) == 6   # in order, re-framed 20 ms -> 30 ms
        assert src.ended == "connection closed"
        assert got[0] == {"probe": True} and got[1] == {"call": 1139}
    finally:
        srv.close()


def test_call_n_must_be_live(sock):
    srv, _ = _serve(sock, [1139], [])
    try:
        with pytest.raises(SourceError, match="call 77"):
            CallSource(77, socks=[sock])
    finally:
        srv.close()


def test_no_ear_leg_is_a_clear_error():
    with pytest.raises(SourceError, match="no ear leg"):
        CallSource(None, socks=[])


def test_open_source_parses_call_specs(sock, monkeypatch):
    srv, _ = _serve(sock, [1120, 1139], [])
    monkeypatch.setenv("EARS_CALL_SOCKS", sock)
    try:
        assert open_source("call").device == "call:1139"      # the newest
        assert open_source("call:1120").device == "call:1120"
        with pytest.raises(SourceError, match="bad call spec"):
            open_source("call:x")
    finally:
        srv.close()


def test_a_dropped_connection_ends_the_source(sock):
    srv, _ = _serve(sock, [5], [pcm(1), pcm(2), pcm(3)], end=None)
    try:
        src = CallSource(5, socks=[sock])
        out = list(src.frames())
        assert len(out) == 2 and src.ended == "eof"           # 960 samples = 2 frames; the rest is < 1 frame
    finally:
        srv.close()
