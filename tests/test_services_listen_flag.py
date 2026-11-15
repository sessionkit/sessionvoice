"""`ears services install --listen`: typing the flag is the user's yes to the room's ear.

It writes ``"listen": true`` into ears.json BEFORE the keeper's plist is loaded, so the keeper's
first read already hears (and macOS asks for the microphone while the user is there). Without
it a fresh install still writes ``"listen": false``; an existing file is never turned off.

Everything runs inside test_services_install_checks' fence: a temp HOME, launchctl faked,
nothing probed, no microphone. The keeper is never really loaded."""
import json
import os
import stat

import pytest

from ears import services
from ears.home import SETTINGS
from test_services_install_checks import _real_probe, fence  # noqa: F401  (a fixture)


def _conf(fence):
    return fence["home"] / SETTINGS / "ears.json"


def _read(fence):
    return json.loads(_conf(fence).read_text())


@pytest.fixture
def at_load(fence, monkeypatch):
    """What ears.json said at each `launchctl bootstrap`, keyed by the plist's file name."""
    seen = {}
    inner = services._launchctl

    def launchctl(*a):
        if a and a[0] == "bootstrap":
            conf = _conf(fence)
            seen[os.path.basename(a[-1])] = json.loads(conf.read_text()) if conf.exists() else None
        return inner(*a)
    monkeypatch.setattr(services, "_launchctl", launchctl)
    return seen


KEEPER_PLIST = f"{services._label('keeper')}.plist"


def test_listen_is_written_before_the_keeper_loads(fence, at_load, capsys):
    assert services.main(["install", "--only", "keeper", "--listen"]) == 0
    assert at_load[KEEPER_PLIST]["listen"] is True, "the keeper's first read must already hear"
    assert _read(fence)["listen"] is True
    assert "final" in _read(fence), "every other key is kept"
    out = capsys.readouterr().out
    assert f"listen {services.LISTEN_ON}" in out.splitlines()
    assert "listen off" not in out, "seed_stt's off sentence would contradict the flag"


def test_the_listen_line_comes_before_the_keepers_load_line(fence, at_load, capsys):
    services.main(["install", "--only", "keeper", "--listen"])
    out = capsys.readouterr().out.splitlines()
    on = out.index(f"listen {services.LISTEN_ON}")
    loaded = next(i for i, ln in enumerate(out) if ln.startswith("keeper loaded"))
    assert on < loaded


def test_without_the_flag_a_fresh_install_still_writes_listen_off(fence, at_load, capsys):
    assert services.main(["install", "--only", "keeper"]) == 0
    assert at_load[KEEPER_PLIST]["listen"] is False and _read(fence)["listen"] is False
    out = capsys.readouterr().out
    assert "listen off: a fresh install hears nothing" in out and services.LISTEN_ON not in out


def test_without_the_flag_an_existing_file_with_no_listen_is_left_as_it_is(fence, at_load):
    conf = _conf(fence)
    conf.parent.mkdir(parents=True)
    conf.write_text(json.dumps({"input": "default"}))
    assert services.main(["install", "--only", "keeper"]) == 0
    assert "listen" not in _read(fence), "the keeper reads a missing listen as on: never flipped off"


@pytest.mark.parametrize("before", [False, True, None])
def test_the_flag_turns_an_existing_file_on_and_keeps_its_keys_and_mode(fence, at_load, before):
    conf = _conf(fence)
    conf.parent.mkdir(parents=True)
    d = {"input": "USB Mic", "record": True, "final": {"url": "http://127.0.0.1:8890/v1"}}
    if before is not None:
        d["listen"] = before
    conf.write_text(json.dumps(d))
    conf.chmod(0o640)
    fence["mic"]["inputs"] = ["USB Mic"]   # the pinned input is connected: the check hears
    assert services.main(["install", "--only", "keeper", "--listen"]) == 0
    got = _read(fence)
    assert got["listen"] is True and at_load[KEEPER_PLIST]["listen"] is True
    assert {k: got[k] for k in ("input", "record", "final")} == {k: d[k] for k in ("input", "record", "final")}
    assert stat.S_IMODE(conf.stat().st_mode) == 0o640
    assert not [p for p in conf.parent.iterdir() if p.name.endswith(".tmp")]


def test_a_bare_install_takes_the_flag(fence, at_load, monkeypatch):
    assert services.main(["install", "--agent", "dave", "--listen"]) == 0
    assert at_load[KEEPER_PLIST]["listen"] is True


def test_dry_run_with_the_flag_writes_nothing_and_says_what_it_would(fence, at_load, capsys):
    assert services.main(["install", "--only", "keeper", "--listen", "--dry-run"]) == 0
    assert not _conf(fence).exists()
    assert not [c for c in fence["calls"]["launchctl"] if c and c[0] in ("bootstrap", "bootout")]
    out = capsys.readouterr().out
    assert f"listen would be {services.LISTEN_ON}" in out.splitlines()
    assert "listen would be off" not in out


def test_dry_run_with_the_flag_leaves_an_existing_file_untouched(fence, at_load, capsys):
    conf = _conf(fence)
    conf.parent.mkdir(parents=True)
    conf.write_text(json.dumps({"listen": False}))
    before = conf.read_bytes()
    services.main(["install", "--only", "keeper", "--listen", "--dry-run"])
    assert conf.read_bytes() == before
    assert f"listen would be {services.LISTEN_ON}" in capsys.readouterr().out.splitlines()


@pytest.mark.parametrize("args", [
    ["install", "--only", "mail", "--listen"],
    ["install", "--only", "mouth,talk", "--listen"],
    ["uninstall", "--listen"],
    ["status", "--listen"],
])
def test_the_flag_without_the_keeper_is_a_usage_error(fence, at_load, capsys, args):
    with pytest.raises(SystemExit) as e:
        services.main(args)
    assert e.value.code == 2
    assert ("--listen is the keeper's: install it with the keeper (a bare install, or --only naming keeper)"
            in capsys.readouterr().err)
    assert not _conf(fence).exists() and at_load == {}


@pytest.mark.parametrize("only", ["keeper", "room", "call", "mouth,keeper"])
def test_any_list_naming_the_keeper_takes_the_flag(fence, at_load, only):
    assert services.main(["install", "--only", only, "--listen"]) == 0
    assert at_load[KEEPER_PLIST]["listen"] is True


def test_a_file_that_is_not_an_object_is_refused_and_left(fence, at_load, monkeypatch, capsys):
    conf = _conf(fence)
    conf.parent.mkdir(parents=True)
    conf.write_text("[1, 2]")
    _real_probe(monkeypatch, {})
    monkeypatch.setattr(services, "seed_stt", lambda **kw: "final kept: (stand-in)")
    services.main(["install", "--only", "keeper", "--listen"])
    assert conf.read_text() == "[1, 2]"
    out = capsys.readouterr().out
    assert any(ln.startswith("listen NOT set: ") and "not a JSON object" in ln for ln in out.splitlines())
    assert services.LISTEN_ON not in out


def test_a_stranded_file_is_refused_and_says_listen_not_set(fence, monkeypatch):
    from ears import recording
    monkeypatch.setattr(recording, "refuse_stranded", lambda p, what, who: f"{p} is stranded ({who})")
    said = services.set_listen()
    assert said.startswith("listen NOT set: ") and "(ears services)" in said
    assert not _conf(fence).exists()
    assert services.set_listen(dry_run=True).startswith("listen would NOT be set: ")


def test_seed_stt_leaves_out_its_off_sentence_only_when_listening(fence, monkeypatch):
    _real_probe(monkeypatch, {})
    out = services.seed_stt(listening=True)
    assert "listen off" not in out
    assert _read(fence)["listen"] is False, "seed_stt still creates the file off: --listen's write turns it on"
