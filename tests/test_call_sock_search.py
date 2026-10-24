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


def _ears_mail():
    """keeper/ears-mail.py, loaded: it carries a stdlib copy of the search (EARS-431)."""
    import importlib.util
    from pathlib import Path
    script = Path(__file__).resolve().parent.parent / "keeper" / "ears-mail.py"
    spec = importlib.util.spec_from_file_location("ears_mail_socks", script)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_ears_mails_copy_of_the_search_equals_sources(tmp_path, monkeypatch):
    """ears-mail finds the same legs as sources.call_socks(), in the same order: the
    sessionphone root (its own and a profile's), every agent's own bot, the override
    and its old name, presence-read."""
    em = _ears_mail()
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("SESSIONPHONE_ROOT", raising=False)
    for k in envnames.group("EARS_CALL_SOCKS"):
        monkeypatch.delenv(k, raising=False)
    assert em.call_socks() == sources.call_socks() == []
    _touch(str(home / ".sessionphone" / "call-ears.sock"))
    _touch(str(home / ".sessionphone" / "web" / "call-ears.sock"))
    _touch(str(home / ".alice" / "delta-chat" / "public" / "call-ears.sock"))
    _touch(str(home / ".bob" / "delta-chat" / "call-ears.sock"))
    _touch(str(home / ".bob" / "delta-chat" / "call-mouth.sock"))
    _touch(str(home / ".carol" / "other" / "call-ears.sock"))       # not a delta-chat dir
    assert em.call_socks() == sources.call_socks()
    assert len(em.call_socks()) == 4
    monkeypatch.setenv("SESSIONPHONE_ROOT", str(tmp_path / "elsewhere"))
    _touch(str(tmp_path / "elsewhere" / "p1" / "call-ears.sock"))
    assert em.call_socks() == sources.call_socks()
    assert em.web_leg(str(tmp_path / "elsewhere" / "web" / "call-ears.sock"))
    assert not em.web_leg(str(tmp_path / "elsewhere" / "p1" / "call-ears.sock"))
    monkeypatch.setenv("VOICEMODE_CALL_EARS_SOCKS", "/old.sock")
    assert em.call_socks() == sources.call_socks() == ["/old.sock"]
    monkeypatch.setenv("EARS_CALL_SOCKS", "/a.sock:/b.sock")
    assert em.call_socks() == sources.call_socks() == ["/a.sock", "/b.sock"]
    monkeypatch.setenv("EARS_CALL_SOCKS", "")                       # set: the old name is not read
    assert em.call_socks() == sources.call_socks()
    assert "/old.sock" not in em.call_socks()
