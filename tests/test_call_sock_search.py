"""The ear and the mouth find a Delta bot's call sockets in sessionphone's root
and in an agent's own ~/.<agent>/delta-chat, so both halves of a call pair up."""
import os

from ears import envnames
from ears.listen import sources
from mouth import output
from tests import env_fence


def _touch(p):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").close()
    return p


def test_both_halves_find_sessionphone_and_agent_bots(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("SESSIONPHONE_ROOT", raising=False)
    for k in envnames.group("EARS_CALL_SOCKS"):
        monkeypatch.delenv(k, raising=False)
    env_fence.delenv(monkeypatch, "MOUTH_CALL_SOCKS")
    sp_ear = _touch(str(home / ".sessionphone" / "call-ears.sock"))
    sp_mouth = _touch(str(home / ".sessionphone" / "call-mouth.sock"))
    agent_ear = _touch(str(home / ".alice" / "delta-chat" / "public" / "call-ears.sock"))
    agent_mouth = _touch(str(home / ".alice" / "delta-chat" / "call-mouth.sock"))
    assert sources.call_socks() == sorted([sp_ear, agent_ear])
    assert output.call_socks() == sorted([sp_mouth, agent_mouth])


def test_sessionphone_root_is_honoured(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SESSIONPHONE_ROOT", str(tmp_path / "elsewhere"))
    for k in envnames.group("EARS_CALL_SOCKS"):
        monkeypatch.delenv(k, raising=False)
    env_fence.delenv(monkeypatch, "MOUTH_CALL_SOCKS")
    ear = _touch(str(tmp_path / "elsewhere" / "call-ears.sock"))
    mouth = _touch(str(tmp_path / "elsewhere" / "call-mouth.sock"))
    assert sources.call_socks() == [ear]
    assert output.call_socks() == [mouth]


def test_the_env_override_still_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("EARS_CALL_SOCKS", "/a.sock:/b.sock")
    assert sources.call_socks() == ["/a.sock", "/b.sock"]
