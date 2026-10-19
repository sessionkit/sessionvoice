"""ears-serve's command line (EARS-198): --help/-h print usage and exit 0, a bad port is a
usage error (exit 2, no traceback), and no args / an explicit port bind exactly as before.
The server is never started: ThreadingHTTPServer is swapped for a recorder."""
import os
import subprocess
import sys
from pathlib import Path
from typing import ClassVar

import pytest

from ears import serve

ROOT = Path(__file__).resolve().parents[1]


class FakeServer:
    bound: ClassVar[list] = []

    def __init__(self, addr, handler):
        FakeServer.bound.append(addr)
        assert handler is serve.H

    def serve_forever(self):
        return None


@pytest.fixture
def fake_server(monkeypatch):
    FakeServer.bound = []
    monkeypatch.setattr(serve, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(serve, "helper_path", lambda: "/fake/helper")
    return FakeServer


def run_main(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["ears-serve", *args])
    serve.main()


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_help_prints_usage_and_exits_0(monkeypatch, capsys, fake_server, flag):
    with pytest.raises(SystemExit) as e:
        run_main(monkeypatch, flag)
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "usage:" in out and "PORT" in out and "2023" in out and "EARS_SERVE_HOST" in out
    assert fake_server.bound == []                   # help never binds


def test_bad_port_is_a_usage_error_exit_2(monkeypatch, capsys, fake_server):
    with pytest.raises(SystemExit) as e:
        run_main(monkeypatch, "abc")
    assert e.value.code == 2
    cap = capsys.readouterr()
    assert "usage:" in cap.err and "abc" in cap.err
    assert "Traceback" not in cap.err + cap.out
    assert fake_server.bound == []


def test_no_args_binds_loopback_2023(monkeypatch, capsys, fake_server):
    monkeypatch.delenv("EARS_SERVE_HOST", raising=False)
    run_main(monkeypatch)
    assert fake_server.bound == [("127.0.0.1", 2023)]
    assert capsys.readouterr().out == (
        "ears serve: http://127.0.0.1:2023/v1/audio/transcriptions (helper /fake/helper)\n")


def test_no_args_honours_ears_serve_host(monkeypatch, fake_server):
    monkeypatch.setenv("EARS_SERVE_HOST", "0.0.0.0")
    run_main(monkeypatch)
    assert fake_server.bound == [("0.0.0.0", 2023)]


def test_explicit_port_binds_it(monkeypatch, capsys, fake_server):
    monkeypatch.delenv("EARS_SERVE_HOST", raising=False)
    run_main(monkeypatch, "2024")
    assert fake_server.bound == [("127.0.0.1", 2024)]
    assert "http://127.0.0.1:2024/" in capsys.readouterr().out


def test_module_help_exits_0_in_a_subprocess():
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    r = subprocess.run([sys.executable, "-m", "ears.serve", "--help"], capture_output=True, text=True,
                       env=env, timeout=60, check=False)
    assert r.returncode == 0, r.stderr
    assert "EARS_SERVE_HOST" in r.stdout and "Traceback" not in r.stderr
