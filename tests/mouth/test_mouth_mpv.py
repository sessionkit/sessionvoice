"""EARS-293: the mouth ducks the DJ through mpv's JSON IPC socket, not ``voice_mode``.

``FakeMpv`` is a Unix-socket server in a temp dir that speaks mpv's own
protocol (https://mpv.io/manual/stable/#json-ipc), so a wrong command name, a
wrong property or a malformed request FAILS here as it would against mpv:

- requests are newline-terminated JSON, ``{"command": [...], "request_id": N}``;
- replies are ``{"data": ..., "error": "success", "request_id": N}``
  (``request_id`` echoed, 0 when the request sent none; no ``data`` on a set);
- an unknown command is ``"error": "invalid parameter"``, an unknown property
  ``"error": "property not found"``;
- the connection stays open after a reply, as mpv's does, and ``events=True``
  puts an event line before each reply, as mpv may.

It never touches the live DJ's socket: the conftest fences
``$MOUTH_MPV_SOCKET`` to nothing, and these tests name their own.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest
from test_mouth import box, lines, run_player, say  # noqa: F401 - fixtures

from mouth import mpv
from tests import env_fence
from ears import envnames

SRC = Path(__file__).resolve().parents[2] / "src"


class FakeMpv:
    """mpv's IPC server, its ``volume`` property, and every request it got."""

    COMMANDS = ("get_property", "set_property")

    def __init__(self, volume=70.0, *, events=False, reply=True, raw=None):
        self.dir = tempfile.mkdtemp(prefix="mpv-", dir="/tmp")   # AF_UNIX paths are short
        self.path = os.path.join(self.dir, "mpv.sock")
        self.props = {"volume": volume}
        self.events, self.reply, self.raw = events, reply, raw
        self.requests: list = []
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(self.path)
        self.srv.listen(8)
        self.conns: list = []
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            self.conns.append(c)
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def _serve(self, c):
        buf = b""
        while True:
            try:
                chunk = c.recv(4096)
            except OSError:
                return
            if not chunk:
                return
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not self.reply:
                    continue                          # a hung DJ: it reads, never answers
                if self.raw is not None:
                    c.sendall(self.raw)
                    continue
                if self.events:
                    c.sendall(b'{"event":"property-change","id":1,"name":"volume","data":5}\n')
                c.sendall(json.dumps(self._answer(line)).encode() + b"\n")

    def _answer(self, line: bytes) -> dict:
        try:
            req = json.loads(line)
        except ValueError:
            return {"error": "invalid parameter"}
        self.requests.append(req)
        rid = req.get("request_id", 0)
        cmd = req.get("command")
        if not isinstance(cmd, list) or not cmd or cmd[0] not in self.COMMANDS:
            return {"request_id": rid, "error": "invalid parameter"}
        if cmd[0] == "get_property" and len(cmd) == 2:
            if self.props.get(cmd[1]) is None:
                return {"request_id": rid, "error": "property not found"}
            return {"data": self.props[cmd[1]], "request_id": rid, "error": "success"}
        if cmd[0] == "set_property" and len(cmd) == 3:
            if cmd[1] not in self.props:
                return {"request_id": rid, "error": "property not found"}
            if not isinstance(cmd[2], (int, float)) or isinstance(cmd[2], bool):
                return {"request_id": rid, "error": "unsupported format for accessing property"}
            self.props[cmd[1]] = float(cmd[2])
            return {"request_id": rid, "error": "success"}
        return {"request_id": rid, "error": "invalid parameter"}

    def sets(self) -> list:
        return [r["command"][2] for r in self.requests if r["command"][0] == "set_property"]

    def close(self):
        self.srv.close()
        for c in self.conns:
            c.close()
        shutil.rmtree(self.dir, ignore_errors=True)


@pytest.fixture
def fake_mpv():
    made: list = []

    def make(*a, **kw) -> FakeMpv:
        made.append(FakeMpv(*a, **kw))
        return made[-1]

    yield make
    for f in made:
        f.close()


# -- the client against a fake mpv ---------------------------------------------------

def test_volume_reads_mpvs_volume_property(fake_mpv):
    f = fake_mpv(volume=70.0)
    assert mpv.Mpv(f.path).volume() == 70
    assert f.requests == [{"command": ["get_property", "volume"], "request_id": 1}]


def test_volume_n_sets_mpvs_volume_property_and_reads_it_back(fake_mpv):
    f = fake_mpv(volume=70.0)
    assert mpv.Mpv(f.path).volume(20) == 20
    assert f.requests[0] == {"command": ["set_property", "volume", 20], "request_id": 1}
    assert f.props["volume"] == 20.0 and f.sets() == [20]


def test_a_fractional_volume_reads_as_an_int(fake_mpv):
    assert mpv.Mpv(fake_mpv(volume=42.7).path).volume() == 42


def test_an_event_line_before_the_reply_is_skipped(fake_mpv):
    f = fake_mpv(volume=64.0, events=True)
    assert mpv.Mpv(f.path).volume() == 64


def test_a_wrong_command_name_fails_like_mpv(fake_mpv):
    f = fake_mpv()
    assert mpv.Mpv(f.path).command("get_propertee", "volume") is None
    assert mpv.Mpv(f.path).command("get_property", "volume") == 70.0


def test_an_mpv_error_is_no_volume(fake_mpv):
    assert mpv.Mpv(fake_mpv(volume=None).path).volume() is None   # "property not found"


@pytest.mark.parametrize("raw", [b"not json\n", b"5\n", b'{"request_id":1,"error":"success","data":"loud"}\n'])
def test_a_junk_reply_is_no_volume(fake_mpv, raw):
    assert mpv.Mpv(fake_mpv(raw=raw).path).volume() is None


def test_no_socket_is_no_volume_and_a_set_does_nothing(tmp_path):
    m = mpv.Mpv(str(tmp_path / "none.sock"))
    assert m.volume() is None and m.volume(20) is None


def test_a_refused_socket_is_no_volume():
    d = tempfile.mkdtemp(prefix="mpv-", dir="/tmp")
    try:
        path = os.path.join(d, "dead.sock")
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(path)                                  # a stale socket file, no listener
        s.close()
        assert mpv.Mpv(path).volume() is None
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_a_hung_dj_never_stalls_the_mouth(fake_mpv):
    f = fake_mpv(reply=False)
    t0 = time.monotonic()
    assert mpv.Mpv(f.path).volume() is None
    assert time.monotonic() - t0 < 2 * mpv.TIMEOUT_S + 0.2
    assert mpv.TIMEOUT_S <= 0.5                       # never VoiceMode's 5 s


def test_the_socket_is_the_djs_unless_the_env_names_another(monkeypatch):
    env_fence.delenv(monkeypatch, "MOUTH_MPV_SOCKET")
    assert mpv.Mpv().path == mpv.SOCKET == "/tmp/voicemode-mpv.sock"   # read, never dialled
    monkeypatch.setenv("MOUTH_MPV_SOCKET", "/tmp/elsewhere.sock")
    assert mpv.Mpv().path == "/tmp/elsewhere.sock"
    assert mpv.Mpv("/x.sock").path == "/x.sock"


def test_the_fence_points_mouth_tests_at_no_dj():
    assert os.environ["MOUTH_MPV_SOCKET"] != mpv.SOCKET


# -- the player ducks through it -----------------------------------------------------

def test_the_player_ducks_a_real_socket_once_per_run_and_restores_it(box, fake_mpv, monkeypatch):
    f = fake_mpv(volume=70.0)
    monkeypatch.setenv("MOUTH_MPV_SOCKET", f.path)
    monkeypatch.setenv("MOUTH_DUCK", "20")
    say("one"), say("two")
    run_player(box.d, idle=0.5).join(8)
    assert f.sets() == [20, 70]                       # down once for both lines, back up once
    assert f.props["volume"] == 70.0
    assert all(r["reason"] == "done" for r in lines(box.logs) if r["kind"] == "said")


def test_a_dead_dj_never_stops_a_line(box, monkeypatch):
    monkeypatch.setenv("MOUTH_DUCK", "20")      # the fence: no mpv behind the socket
    say("one")
    run_player(box.d).join(5)
    assert next(r for r in lines(box.logs) if r["kind"] == "said")["reason"] == "done"


# -- the guard: the duck path imports no voice_mode ----------------------------------

def test_the_duck_path_imports_no_voice_mode():
    for f in ("player.py", "mpv.py"):
        tree = ast.parse((SRC / "mouth" / f).read_text())
        names = [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        names += [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        assert not [m for m in names if m.split(".")[0] == "voice_mode"], f


def test_ducking_never_even_tries_voice_mode(tmp_path):
    """A fresh interpreter whose import system refuses and records any
    ``voice_mode``: a duck and its restore through a fake mpv ask for none."""
    f = FakeMpv(volume=70.0)
    try:
        code = (
            "import sys\n"
            "asked = []\n"
            "class No:\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            "        if name.split('.')[0] == 'voice_mode':\n"
            "            asked.append(name)\n"
            "            raise ImportError(name)\n"
            "sys.meta_path.insert(0, No())\n"
            "from mouth import player\n"
            f"player._duck_on(None, {str(tmp_path / 'mouth')!r})\n"
            "assert player._duck['prev'] == 70, player._duck\n"
            "player._duck_off()\n"
            "print(asked, sorted(m for m in sys.modules if m.split('.')[0] == 'voice_mode'))\n")
        env = {k: v for k, v in os.environ.items() if not k.startswith("VOICEMODE_") and k not in envnames.ALIASES}
        env.update(HOME=str(tmp_path), PYTHONPATH=str(SRC), MOUTH_DUCK="20",
                   MOUTH_MPV_SOCKET=f.path)
        out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                             text=True, timeout=60)
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "[] []"
        assert f.sets() == [20, 70]
    finally:
        f.close()
