"""EARS-429: `ears services install --only talk` from the wheel, no checkout.

talk is one launch agent, dev.sessionkit.voice.talk, running `talk serve` on 127.0.0.1 at talk's
default port, as launchd/dev.sessionkit.voice.talk.plist.in does. It runs the INSTALLED talk
console script, found beside the agents' python (else sys.prefix's bin), never the first
`talk` on PATH: macOS ships an unrelated /usr/bin/talk. Opt-in: a bare install never loads it.
--dry-run prints the plist and writes nothing; status has a row; uninstall --only talk removes
it.

Every test runs under a temp HOME with launchctl faked (test_services_speakers.sp): nothing
here loads a launch agent, reads the real HOME, or binds a port."""
import os
import plistlib
import re
import sys
from pathlib import Path

import pytest

from ears import services
from test_services_install_checks import fence  # noqa: F401  (a fixture)
from test_services_speakers import sp  # noqa: F401  (a fixture)

REPO = Path(__file__).resolve().parents[1]
UID = os.getuid()
LOOK = ("print", "list")
LABEL = "dev.sessionkit.voice.talk"
#: what uv and pip write for talk's entry point (the [project.scripts] line)
ENTRY = "#!/x/venv/bin/python\nimport sys\nfrom talk.cli import main\nsys.exit(main())\n"


def _script(path: Path, body: str = ENTRY) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)
    return path


@pytest.fixture
def venv(tmp_path, monkeypatch):
    """A kit-like venv: bin/python (EARS_PY, the agents' python) with talk's console script
    beside it."""
    bindir = tmp_path / "venv/bin"
    _script(bindir / "python", "#!/bin/sh\n")
    talk = _script(bindir / "talk")
    monkeypatch.setenv("EARS_PY", str(bindir / "python"))
    return talk


@pytest.fixture
def tk(sp, venv, monkeypatch):  # noqa: F811
    monkeypatch.setattr(services, "weights_status", lambda: [])
    sp["talk"] = venv
    return sp


def _changing(sp):
    """launchctl calls that change launchd (print and list only look)."""
    return [a for a in sp["calls"]["launchctl"] if a[0] not in LOOK]


def _tree(root):
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None) for p in sorted(root.rglob("*"))}


# ---- the service: in ALL, never in DEFAULT, one job under the template's label ------------------

def test_talk_is_a_service_and_opt_in():
    assert "talk" in services.ALL and "talk" not in services.DEFAULT
    assert services.jobs(["talk"]) == ["talk"]
    assert services._label("talk") == LABEL
    assert services._old_labels("talk") == []         # install never made it under another label
    assert services._labels("talk") == [LABEL]
    assert services._service("talk") == "talk"        # every install hint names --only talk


def test_only_help_lists_talk(capsys):
    with pytest.raises(SystemExit):
        services.main(["--help"])
    out = re.sub(r"-\s+", "-", " ".join(capsys.readouterr().out.split()))
    assert "sounds,talk" in out


def test_a_bare_install_never_loads_talk(monkeypatch):
    got = {}
    monkeypatch.setattr(services, "install", lambda names, dry=False, **kw: got.update(install=names) or 0)
    monkeypatch.setattr(services, "uninstall", lambda names, dry=False: got.update(uninstall=names) or 0)
    services.main(["install", "--no-agent"])
    assert "talk" not in got["install"]
    services.main(["install", "--only", "talk"])
    assert got["install"] == ["talk"]
    services.main(["uninstall"])                      # uninstall takes every service, talk too
    assert "talk" in got["uninstall"]


# ---- talk_bin(): the installed console script, never PATH's -----------------------------------

def test_talk_bin_is_the_one_beside_the_agents_python(venv):
    assert services.talk_bin() == venv


def test_from_the_test_venv_talk_bin_is_its_console_script(monkeypatch):
    """No EARS_PY: the python running ears is the venv's, and its talk is beside it."""
    monkeypatch.delenv("EARS_PY", raising=False)
    got = services.talk_bin()
    assert got.parent == Path(sys.executable).parent
    assert b"talk.cli" in got.read_bytes()


def test_talk_bin_falls_back_to_sys_prefix_bin(tmp_path, monkeypatch):
    _script(tmp_path / "py/bin/python", "#!/bin/sh\n")
    monkeypatch.setenv("EARS_PY", str(tmp_path / "py/bin/python"))      # no talk beside it
    talk = _script(tmp_path / "prefix/bin/talk")
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "prefix"))
    assert services.talk_bin() == talk


def test_talk_bin_never_takes_path_or_a_talk_that_is_not_ours(tmp_path, monkeypatch):
    """/usr/bin/talk on macOS is the old chat program: a file named talk that is not talk's
    entry point is not it, and a good talk only on PATH is never found (launchd does not search
    PATH)."""
    bindir = tmp_path / "py/bin"
    _script(bindir / "python", "#!/bin/sh\n")
    _script(bindir / "talk", "\xcf\xfa\xed\xfe a Mach-O talk(1)")          # not ours
    monkeypatch.setenv("EARS_PY", str(bindir / "python"))
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "empty-prefix"))
    _script(tmp_path / "onpath/talk")
    monkeypatch.setenv("PATH", f"{tmp_path / 'onpath'}:{os.environ['PATH']}")
    with pytest.raises(services.NoTalk) as e:
        services.talk_bin()
    assert str(bindir / "talk") in str(e.value) and str(tmp_path / "empty-prefix/bin/talk") in str(e.value)
    assert "onpath" not in str(e.value)


def test_a_talk_script_that_cannot_run_is_not_found(tmp_path, monkeypatch):
    bindir = tmp_path / "py/bin"
    _script(bindir / "python", "#!/bin/sh\n")
    _script(bindir / "talk").chmod(0o644)                                 # not executable
    monkeypatch.setenv("EARS_PY", str(bindir / "python"))
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "empty-prefix"))
    with pytest.raises(services.NoTalk):
        services.talk_bin()


# ---- spec(): the plist ------------------------------------------------------------------------

def test_the_talk_agent(tk):
    home = tk["home"]
    d = services.spec("talk")
    from talk.server import PORT
    assert PORT == 8780                               # the template's default
    assert d["Label"] == LABEL
    assert d["ProgramArguments"] == [str(tk["talk"]), "serve", "--host", "127.0.0.1", "--port", str(PORT)]
    assert d["StandardOutPath"] == d["StandardErrorPath"] == str(services.LOGS / "talk.log")
    assert d["KeepAlive"] is True and d["RunAtLoad"] is True and d["ThrottleInterval"] == 10
    assert d["WorkingDirectory"] == str(home)
    env = d["EnvironmentVariables"]
    assert env[services.MARKER[0]] == services.MARKER[1]
    assert env["HOME"] == str(home) and env["PYTHONUNBUFFERED"] == "1"
    assert "EARS_PY" not in env                       # it runs on its console script's python
    assert env["PATH"].split(":")[0] == str(tk["talk"].parent)
    assert not any(k.startswith("TALK_") for k in env)   # every talk setting stays off by default


def _template() -> dict:
    vals = {"HOME": "/Users/x", "TALK": "/x/venv/bin/talk"}
    body = re.sub(r"<!--.*?-->", "", (REPO / f"launchd/{LABEL}.plist.in").read_text(), flags=re.S)
    return plistlib.loads(re.sub(r"@([A-Z_]+)@", lambda m: vals[m.group(1)], body).encode())


def test_template_parity(tk):
    """Only the paths and the marker differ from the hand template."""
    t, d = _template(), services.spec("talk")
    assert t["Label"] == d["Label"] == LABEL                # one label, whoever installs it
    assert t["ProgramArguments"][1:] == d["ProgramArguments"][1:]
    for k in ("RunAtLoad", "KeepAlive", "ThrottleInterval"):
        assert d[k] == t[k], k
    for k in ("StandardOutPath", "StandardErrorPath"):
        assert Path(d[k]).name == Path(t[k]).name == "talk.log"
    assert set(t["EnvironmentVariables"]) | {services.MARKER[0]} == set(d["EnvironmentVariables"])
    assert services.MARKER[0] not in t["EnvironmentVariables"]


def test_the_template_header_no_longer_says_install_is_not_built():
    text = (REPO / f"launchd/{LABEL}.plist.in").read_text()
    assert "not built yet" not in text
    assert "`ears services install`, asked for talk" in text


# ---- install, uninstall, status ---------------------------------------------------------------

def test_no_talk_refuses_and_writes_nothing(tk, monkeypatch, tmp_path, capsys):
    tk["talk"].unlink()
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "empty-prefix"))
    before = _tree(tmp_path)
    for dry in (True, False):
        assert services.install(["talk"], dry_run=dry) == 2
        err = capsys.readouterr().err
        assert "REFUSED" in err and "no installed talk console script" in err
    assert _tree(tmp_path) == before
    assert _changing(tk) == []


def test_install_dry_run_prints_the_plist_and_writes_nothing(tk, capsys, tmp_path):
    before = _tree(tmp_path)
    assert services.main(["install", "--only", "talk", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert f"--- {services.AGENTS / (LABEL + '.plist')}" in out
    assert f"<string>{LABEL}</string>" in out and f"<string>{tk['talk']}</string>" in out
    assert _changing(tk) == []
    assert _tree(tmp_path) == before


def test_install_writes_and_loads_it(tk, capsys):
    assert services.main(["install", "--only", "talk"]) == 0
    out = capsys.readouterr().out
    path = services.AGENTS / f"{LABEL}.plist"
    assert services._ours(path)
    assert plistlib.loads(path.read_bytes()) == services.spec("talk")
    assert ("bootstrap", f"gui/{UID}", str(path)) in tk["calls"]["launchctl"]
    assert re.search(rf"^talk\s+loaded\s+{re.escape(str(path))}$", out, re.M), out
    assert services.LOGS.is_dir()
    assert {p.name for p in services.AGENTS.iterdir()} == {f"{LABEL}.plist"}   # nothing else


def test_a_rerun_replaces_ours_with_the_same_job(tk, capsys):
    services.main(["install", "--only", "talk"])
    first = (services.AGENTS / f"{LABEL}.plist").read_bytes()
    services.main(["install", "--only", "talk"])
    assert "SKIPPED" not in capsys.readouterr().out
    assert (services.AGENTS / f"{LABEL}.plist").read_bytes() == first


def test_a_hand_render_at_our_label_is_left_and_ours_skipped(tk, capsys):
    services.AGENTS.mkdir(parents=True)
    hand = services.AGENTS / f"{LABEL}.plist"
    hand.write_text("<plist>hand</plist>")
    tk["listed"][LABEL] = "42"
    assert services.install(["talk"]) == 0
    out = capsys.readouterr().out
    assert "talk SKIPPED" in out and "fight over talk's port" in out
    assert hand.read_text() == "<plist>hand</plist>"
    assert _changing(tk) == []
    assert re.findall(r"`ears services install --only (\S+)`", out) == ["talk"], out


@pytest.mark.parametrize("old", ["dev.sessionvoice.talk", "dev.voicemode.talk"])
def test_a_hand_talk_under_an_old_name_means_ours_is_skipped(tk, capsys, old):
    """Before, install could not run talk, so these were only named. Now ours beside one would
    be two talks on one port: install skips ours, and status says hand-made."""
    assert old in services.HAND["talk"]
    services.AGENTS.mkdir(parents=True)
    (services.AGENTS / f"{old}.plist").write_bytes(plistlib.dumps({"Label": old, "ProgramArguments": ["/x/talk"]}))
    tk["listed"][old] = "7"
    assert services.install(["talk"]) == 0
    out = capsys.readouterr().out
    assert f"talk SKIPPED: the hand-made {old}" in out
    assert not (services.AGENTS / f"{LABEL}.plist").exists() and _changing(tk) == []
    services.status()
    assert re.search(rf"{re.escape(old)} \(hand-made\): running", capsys.readouterr().out)


def test_uninstall_removes_it(tk, capsys):
    services.main(["install", "--only", "talk"])
    capsys.readouterr()
    assert services.main(["uninstall", "--only", "talk"]) == 0
    out = capsys.readouterr().out
    assert ("bootout", f"gui/{UID}/{LABEL}") in tk["calls"]["launchctl"]
    assert not (services.AGENTS / f"{LABEL}.plist").exists()
    assert re.search(r"^talk\s+removed$", out, re.M), out


def test_uninstall_dry_run_names_it_and_changes_nothing(tk, capsys, tmp_path):
    services.main(["install", "--only", "talk"])
    capsys.readouterr()
    calls = len(tk["calls"]["launchctl"])
    before = _tree(tmp_path)
    assert services.main(["uninstall", "--only", "talk", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert f"would boot out gui/{UID}/{LABEL}" in out
    assert f"would delete {services.AGENTS / (LABEL + '.plist')}" in out
    assert tk["calls"]["launchctl"][calls:] == []
    assert _tree(tmp_path) == before


def test_status_reports_it(tk, capsys):
    services.status()
    assert re.search(r"^talk\s+not installed$", capsys.readouterr().out, re.M)
    services.main(["install", "--only", "talk"])
    tk["listed"][LABEL] = "808"
    capsys.readouterr()
    services.status()
    out = capsys.readouterr().out
    assert re.search(r"^talk\s+running, pid 808$", out, re.M), out
