"""One ears job: the keeper runs the room and the call ears (merged: they need not be
separate)."""
import os
import subprocess

from ears import services


def _verbs(monkeypatch, argv):
    got = {}
    monkeypatch.setattr(services, "install", lambda names, dry=False, **kw: got.update(install=names) or 0)
    monkeypatch.setattr(services, "uninstall", lambda names, dry=False: got.update(uninstall=names) or 0)
    services.main(argv)
    return got


def test_call_and_room_name_the_keeper(monkeypatch):
    assert _verbs(monkeypatch, ["install", "--only", "call,room"])["install"] == ["keeper"]
    assert _verbs(monkeypatch, ["install", "--only", "mouth,room"])["install"] == ["mouth", "keeper"]


def test_the_keeper_is_one_agent_that_keeps_its_captures():
    d = services.spec("keeper")
    assert d["Label"] == "dev.sessionkit.voice.ears-keeper"
    assert d["ProgramArguments"][-2].endswith("keeper/ears-keeper.sh") and d["ProgramArguments"][-1] == "default"
    assert d["AbandonProcessGroup"] is True
    assert "call" not in services.ALL and "room" not in services.ALL


def _fake_launchctl(monkeypatch, tmp_path, loaded):
    calls = []
    def fake(*a):
        calls.append(a)
        rc = 0 if a[0] == "print" and any(a[1].endswith(o) for o in loaded) else (113 if a[0] == "print" else 0)
        out = "".join(f"1\t0\tcom.voicemode.{o}\n" for o in loaded) if a[0] == "list" else ""
        return subprocess.CompletedProcess(a, rc, out, "")
    monkeypatch.setattr(services, "_launchctl", fake)
    monkeypatch.setattr(services, "AGENTS", tmp_path)
    return calls


def test_retire_boots_out_and_removes_both_old_agents(monkeypatch, tmp_path):
    calls = _fake_launchctl(monkeypatch, tmp_path, loaded=("ears-call",))
    (tmp_path / "com.voicemode.ears-room.plist").write_text("x")      # on disk, not loaded
    (tmp_path / "com.voicemode.ears-call.plist").write_text("x")
    out = services._retire_old(501)
    assert ("bootout", "gui/501/com.voicemode.ears-call") in calls
    assert ("bootout", "gui/501/com.voicemode.ears-room") not in calls   # not loaded: no bootout
    assert not list(tmp_path.glob("*.plist"))
    assert "call, room removed" in out


def test_retire_with_nothing_old_says_none(monkeypatch, tmp_path):
    _fake_launchctl(monkeypatch, tmp_path, loaded=())
    assert services._retire_old(501).endswith("none")


def test_install_keeper_retires_the_old_first(monkeypatch, tmp_path):
    calls = _fake_launchctl(monkeypatch, tmp_path, loaded=("ears-call", "ears-room"))
    monkeypatch.setattr(services, "LOGS", tmp_path / "logs")
    monkeypatch.setenv("SESSIONVOICE_HOME", str(tmp_path / "sessionvoice"))   # the install log's root
    monkeypatch.setattr(services, "seed_stt", lambda **kw: "stt kept")
    monkeypatch.setattr(services, "silero", lambda dry_run=False: (True, "silero present"))
    monkeypatch.setattr(services, "install_schema", lambda: "schema")
    monkeypatch.setattr("time.sleep", lambda s: None)
    services.install(["keeper"])
    boots = [a for a in calls if a[0] in ("bootout", "bootstrap")]
    u = os.getuid()
    assert boots[:2] == [("bootout", f"gui/{u}/com.voicemode.ears-call"), ("bootout", f"gui/{u}/com.voicemode.ears-room")]
    assert boots[-1][0] == "bootstrap" and boots[-1][2].endswith("dev.sessionkit.voice.ears-keeper.plist")


def test_uninstall_keeper_takes_the_old_too(monkeypatch, tmp_path):
    calls = _fake_launchctl(monkeypatch, tmp_path, loaded=())
    services.uninstall(["keeper"])
    outs = {a[1] for a in calls if a[0] == "bootout"}
    u = os.getuid()   # the keeper under every label (EARS-294, EARS-407); call and room only old ones
    assert outs == {f"gui/{u}/dev.sessionkit.voice.ears-keeper"} | {
        f"gui/{u}/{p}{n}" for p in services.OLD_PREFIXES for n in ("keeper", "call", "room")}
