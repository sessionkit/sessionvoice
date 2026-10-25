"""The ears' engine is a setting: ``final`` (old name ``stt``) in ears.json, followed by
every capture; ears services seeds it and keeps it out of the launch agents."""
import json, os, subprocess, sys
from pathlib import Path

import pytest

from ears import envnames, services
from ears.listen import stt

SRC = str(Path(__file__).resolve().parents[1] / "src")
# the default engine: Parakeet on VoiceMode's mlx-audio, the mouth's service
PARAKEET = {"url": "http://127.0.0.1:8890/v1", "model": "mlx-community/parakeet-tdt-0.6b-v3"}


@pytest.fixture(autouse=True)
def _no_live_stt(monkeypatch, tmp_path):
    # seed_stt probes a new final (EARS-222 item 7): never this host's live servers here;
    # the probe itself is tested on a stub server (test_services_install_checks.py)
    monkeypatch.setattr(services, "probe_stt", lambda url, model=None, timeout=30.0: None)
    # ...and reads (and may write) its fallback record under sessionvoice's home: never the live one
    monkeypatch.setenv("SESSIONVOICE_HOME", str(tmp_path / "sessionvoice"))


def test_agents_carry_no_engine(monkeypatch):
    monkeypatch.setenv("EARS_STT_URL", "http://127.0.0.1:8890/v1")
    monkeypatch.setenv("EARS_STT_MODEL", "mlx-community/parakeet-tdt-0.6b-v3")
    for name in ("keeper", "stt", "mouth"):
        env = services.spec(name)["EnvironmentVariables"]
        for k in (*envnames.group("EARS_LISTEN_STT_URL"), *envnames.group("EARS_LISTEN_STT_MODEL")):
            assert k not in env


def test_seed_writes_the_default_only_when_absent(tmp_path, monkeypatch):
    conf = tmp_path / "ears.json"
    conf.write_text(json.dumps({"listen": True, "input": "default"}))
    monkeypatch.setenv("EARS_CONF", str(conf))
    monkeypatch.delenv("EARS_STT_URL", raising=False); monkeypatch.delenv("EARS_STT_MODEL", raising=False)
    services.seed_stt()
    d = json.loads(conf.read_text())
    assert d["final"] == PARAKEET and d["listen"] is True and d["input"] == "default" and "stt" not in d
    d["final"] = {"url": "http://127.0.0.1:8890/v1", "model": "p"}; conf.write_text(json.dumps(d))
    services.seed_stt()                                                # a choice already made is kept
    assert json.loads(conf.read_text())["final"] == {"url": "http://127.0.0.1:8890/v1", "model": "p"}


def test_seed_keeps_an_old_stt_as_the_choice(tmp_path, monkeypatch):
    conf = tmp_path / "ears.json"                 # kept, under its new name: the schema no longer draws stt
    conf.write_text(json.dumps({"stt": {"url": "http://127.0.0.1:2023/v1"}, "listen": True}))
    monkeypatch.setenv("EARS_CONF", str(conf))
    monkeypatch.delenv("EARS_STT_URL", raising=False); monkeypatch.delenv("EARS_STT_MODEL", raising=False)
    assert "renamed from stt" in services.seed_stt()
    assert json.loads(conf.read_text()) == {"final": {"url": "http://127.0.0.1:2023/v1"}, "listen": True}
    assert services.seed_stt() == "final kept: {'url': 'http://127.0.0.1:2023/v1'}"   # a second run leaves it be



def test_seed_drops_a_dead_stt_beside_final(tmp_path, monkeypatch):
    conf = tmp_path / "ears.json"                 # final wins in every reader; stt would fail the schema
    final = {"url": "http://127.0.0.1:8890/v1", "model": "mlx-community/parakeet-tdt-0.6b-v3"}
    conf.write_text(json.dumps({"final": final, "stt": {"url": "http://127.0.0.1:2023/v1"}}))
    monkeypatch.setenv("EARS_CONF", str(conf))
    monkeypatch.delenv("EARS_STT_URL", raising=False); monkeypatch.delenv("EARS_STT_MODEL", raising=False)
    assert "dropped stt, final wins" in services.seed_stt()
    assert json.loads(conf.read_text()) == {"final": final}

def test_seed_given_at_install_replaces(tmp_path, monkeypatch):
    conf = tmp_path / "ears.json"
    conf.write_text(json.dumps({"stt": {"url": "http://127.0.0.1:2023/v1"}, "input": "airpods"}))
    monkeypatch.setenv("EARS_CONF", str(conf))
    monkeypatch.setenv("EARS_STT_URL", "http://127.0.0.1:8890/v1")
    monkeypatch.setenv("EARS_STT_MODEL", "mlx-community/parakeet-tdt-0.6b-v3")
    services.seed_stt()
    d = json.loads(conf.read_text())
    assert d["final"] == {"url": "http://127.0.0.1:8890/v1", "model": "mlx-community/parakeet-tdt-0.6b-v3"}
    assert d["input"] == "airpods" and "stt" not in d                  # never two engines named


def test_a_url_given_alone_names_no_model(tmp_path, monkeypatch):
    conf = tmp_path / "ears.json"
    monkeypatch.setenv("EARS_CONF", str(conf))
    monkeypatch.setenv("EARS_STT_URL", "http://127.0.0.1:2023/v1")    # Apple: no Parakeet model on it
    monkeypatch.delenv("EARS_STT_MODEL", raising=False)
    services.seed_stt()
    assert json.loads(conf.read_text())["final"] == {"url": "http://127.0.0.1:2023/v1"}


def test_conf_stt_tolerates_a_missing_or_bad_file(tmp_path, monkeypatch):
    monkeypatch.setenv("EARS_CONF", str(tmp_path / "nope.json"))
    assert stt.conf_stt() == {}
    (tmp_path / "bad.json").write_text("{not json")
    monkeypatch.setenv("EARS_CONF", str(tmp_path / "bad.json"))
    assert stt.conf_stt() == {}


def _defaults(env):
    code = "import ears.listen.stt as s; print(s.DEFAULT_WHISPER_URL, s.DEFAULT_WHISPER_MODEL)"
    base = {k: v for k, v in os.environ.items() if not k.startswith(("VOICEMODE_LISTEN_STT", "EARS_CONF"))}
    return subprocess.run([sys.executable, "-c", code], env={**base, "PYTHONPATH": SRC, **env},
                          capture_output=True, text=True, check=True).stdout.split()


def test_capture_takes_env_then_file_then_default(tmp_path):
    conf = tmp_path / "ears.json"
    conf.write_text(json.dumps({"stt": {"url": "http://f:1/v1", "model": "file-model"}}))
    assert _defaults({"EARS_CONF": str(conf)}) == ["http://f:1/v1", "file-model"]
    assert _defaults({"EARS_CONF": str(conf), "EARS_LISTEN_STT_URL": "http://e:2/v1"}) == ["http://e:2/v1", "file-model"]
    none = str(tmp_path / "none.json")                  # no file: Parakeet, as install seeds (/RULINGS 4860)
    assert _defaults({"EARS_CONF": none}) == [PARAKEET["url"], PARAKEET["model"]]
    # a URL with no model is not asked for Parakeet's name (Apple's server, whisper.cpp)
    assert _defaults({"EARS_CONF": none, "EARS_LISTEN_STT_URL": "http://127.0.0.1:2023/v1"}) == [
        "http://127.0.0.1:2023/v1", "whisper-1"]


def test_one_default_for_install_and_capture():
    assert {"url": services.DEFAULT_STT_URL, "model": services.DEFAULT_STT_MODEL} == PARAKEET
    assert (stt.FALLBACK_URL, stt.FALLBACK_MODEL) == (PARAKEET["url"], PARAKEET["model"])


def _verbs(monkeypatch, argv):
    got = {}
    monkeypatch.setattr(services, "install", lambda names, dry=False, **kw: got.update(install=names) or 0)
    monkeypatch.setattr(services, "uninstall", lambda names, dry=False: got.update(uninstall=names) or 0)
    services.main(argv)
    return got


def test_the_apple_stt_agent_installs_only_with_apple(monkeypatch):
    # without the swift helper it answers 500 to every request (measured)
    # --no-agent: a bare install with mail and no wake seat refuses before install() (EARS-421)
    assert _verbs(monkeypatch, ["install", "--no-agent"])["install"] == ["speech", "mouth", "keeper", "mail"]   # EARS-291
    assert "stt" in _verbs(monkeypatch, ["install", "--no-agent", "--apple"])["install"]
    assert _verbs(monkeypatch, ["install", "--only", "stt"])["install"] == ["stt"]
    assert _verbs(monkeypatch, ["uninstall"])["uninstall"] == services.jobs(services.ALL)   # an old stt agent goes


def test_ears_mail_as_their_seat(monkeypatch):
    monkeypatch.delenv("EARS_AGENT", raising=False)
    env = services.spec("keeper")["EnvironmentVariables"]
    assert env["EARS_AGENT"] == env["TM_AGENT_ADDRESS"] == "ears"
    assert "TM_AGENT_ADDRESS" not in services.spec("stt")["EnvironmentVariables"]


def test_path_reaches_the_uv_tools():
    for name in ("keeper",):
        assert str(Path.home() / ".local/bin") in services.spec(name)["EnvironmentVariables"]["PATH"].split(":")
