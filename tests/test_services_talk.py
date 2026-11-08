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
    vals = {"HOME": "/Users/x", "TALK": "/x/venv/bin/talk", "EARS_LOG_DIR": "/Users/x/logs"}
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


# ---- --allow-host: the Host a phone behind tailscale serve sends ----------------------------------
# Behind `tailscale serve` the browser's Host is the tailnet name, and talk answers only
# 127.0.0.1:8780 and localhost:8780 unless TALK_ALLOW_HOSTS names it. Install wrote no way to set
# it, so it was added to the plist by hand, and the next install would have dropped it.

TAILNET = "machine.example.ts.net"
HOSTS_NONE = ("talk   answers Host 127.0.0.1:8780, localhost:8780 only. Behind `tailscale serve` a "
              "phone gets a 403 until install is run with --allow-host NAME (the tailnet name)")


def _env():
    return plistlib.loads((services.AGENTS / f"{LABEL}.plist").read_bytes())["EnvironmentVariables"]


def test_allow_host_is_written_to_the_job(tk, capsys):
    assert services.main(["install", "--only", "talk", "--allow-host", TAILNET]) == 0
    out = capsys.readouterr().out
    assert _env()["TALK_ALLOW_HOSTS"] == TAILNET
    assert (f"talk   answers Host 127.0.0.1:8780, localhost:8780, {TAILNET} (TALK_ALLOW_HOSTS)\n"
            in out)
    assert out.index("talk   answers Host") < out.index("talk   loaded")   # said before it loads


def test_allow_host_repeats_and_is_checked_as_talk_serve_checks_it(tk):
    assert services.main(["install", "--only", "talk", "--allow-host", "B.example.ts.net.",
                          "--allow-host", "a.example:8443"]) == 0
    assert _env()["TALK_ALLOW_HOSTS"] == "a.example:8443,b.example.ts.net"   # lower, sorted
    from talk.server import check_allow_hosts
    assert check_allow_hosts(_env()["TALK_ALLOW_HOSTS"]) == {"a.example:8443", "b.example.ts.net"}


@pytest.mark.parametrize("bad, why", [("https://" + TAILNET, "a scheme"), (TAILNET + "/talk", "a path"),
                                      ("*.ts.net", "no wildcards"), (TAILNET + ":0", "a bad port")])
def test_a_bad_allow_host_is_refused_and_nothing_written(tk, capsys, tmp_path, bad, why):
    before = _tree(tmp_path)
    with pytest.raises(SystemExit) as e:
        services.main(["install", "--only", "talk", "--allow-host", bad])
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert f"--allow-host: allowed host {bad!r} refused: {why}" in err
    assert _tree(tmp_path) == before and _changing(tk) == []


@pytest.mark.parametrize("argv", [["install", "--allow-host", TAILNET],
                                  ["install", "--only", "mouth", "--allow-host", TAILNET],
                                  ["uninstall", "--only", "talk", "--allow-host", TAILNET]])
def test_allow_host_without_a_talk_install_is_refused(tk, capsys, tmp_path, argv):
    before = _tree(tmp_path)
    with pytest.raises(SystemExit) as e:
        services.main(argv)
    assert e.value.code == 2
    assert "--allow-host is talk's: install it with --only talk" in capsys.readouterr().err
    assert _tree(tmp_path) == before and _changing(tk) == []


def test_without_allow_host_install_says_how_a_phone_gets_in(tk, capsys):
    assert services.main(["install", "--only", "talk"]) == 0
    assert HOSTS_NONE + "\n" in capsys.readouterr().out
    assert not any(k.startswith("TALK_") for k in _env())


def test_a_rerun_keeps_the_talk_settings_of_our_plist(tk, capsys):
    """An upgrade runs install again with no flags: the names, and a setting added to our plist
    by hand (the template's TALK_SETTINGS_URL), stay."""
    services.main(["install", "--only", "talk", "--allow-host", TAILNET])
    path = services.AGENTS / f"{LABEL}.plist"
    d = plistlib.loads(path.read_bytes())
    d["EnvironmentVariables"]["TALK_SETTINGS_URL"] = f"https://{TAILNET}/settings/"
    d["EnvironmentVariables"]["NOT_TALKS"] = "x"
    path.write_bytes(plistlib.dumps(d))
    capsys.readouterr()
    assert services.main(["install", "--only", "talk"]) == 0
    out = capsys.readouterr().out
    env = _env()
    assert env["TALK_ALLOW_HOSTS"] == TAILNET
    assert env["TALK_SETTINGS_URL"] == f"https://{TAILNET}/settings/"
    assert "NOT_TALKS" not in env                     # only talk's own settings are kept
    assert ("; kept from the plist it replaces: TALK_ALLOW_HOSTS, TALK_SETTINGS_URL\n") in out


def test_allow_host_replaces_the_names_and_keeps_the_rest(tk, capsys):
    services.main(["install", "--only", "talk", "--allow-host", TAILNET])
    path = services.AGENTS / f"{LABEL}.plist"
    d = plistlib.loads(path.read_bytes())
    d["EnvironmentVariables"]["TALK_PERSON"] = "Ivy"
    path.write_bytes(plistlib.dumps(d))
    capsys.readouterr()
    assert services.main(["install", "--only", "talk", "--allow-host", "other.example.ts.net"]) == 0
    out = capsys.readouterr().out
    assert _env()["TALK_ALLOW_HOSTS"] == "other.example.ts.net"
    assert _env()["TALK_PERSON"] == "Ivy"
    assert out.count("kept from the plist it replaces: TALK_PERSON\n") == 1   # not the names


def test_uninstall_then_install_starts_clean(tk, capsys):
    services.main(["install", "--only", "talk", "--allow-host", TAILNET])
    services.main(["uninstall", "--only", "talk"])
    capsys.readouterr()
    services.main(["install", "--only", "talk"])
    assert not any(k.startswith("TALK_") for k in _env())
    assert "kept from" not in capsys.readouterr().out


def test_a_hand_plist_is_never_read_for_settings(tk):
    """No MARKER: not ours, so nothing in it is carried into ours."""
    services.AGENTS.mkdir(parents=True)
    hand = _template()
    hand["EnvironmentVariables"]["TALK_ALLOW_HOSTS"] = TAILNET
    (services.AGENTS / f"{LABEL}.plist").write_bytes(plistlib.dumps(hand))
    assert services.talk_settings(None) == ({}, [])


def test_the_dry_run_shows_the_names_and_writes_nothing(tk, capsys, tmp_path):
    before = _tree(tmp_path)
    assert services.main(["install", "--only", "talk", "--allow-host", TAILNET, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert f"<key>TALK_ALLOW_HOSTS</key>\n\t\t<string>{TAILNET}</string>" in out
    assert f"localhost:8780, {TAILNET} (TALK_ALLOW_HOSTS)" in out
    assert _tree(tmp_path) == before and _changing(tk) == []


# ---- the closing lines say how a browser pairs ---------------------------------------------------

def test_a_talk_install_ends_by_saying_how_to_pair(tk, capsys):
    assert services.main(["install", "--only", "talk"]) == 0
    out = capsys.readouterr().out
    assert services.TALK_PAIR + "\n" in out
    assert out.index("talk   loaded") < out.index(services.TALK_PAIR)
    assert "`talk pair`" in services.TALK_PAIR and "`talk pair --url`" in services.TALK_PAIR
    assert "reload a Talk tab that was already open" in services.TALK_PAIR


def test_no_pair_line_for_a_dry_run_or_a_talk_that_did_not_load(tk, capsys, monkeypatch):
    services.main(["install", "--only", "talk", "--dry-run"])
    assert "pair   " not in capsys.readouterr().out
    inner = services._launchctl

    def launchctl(*a):
        if a[0] == "bootstrap" and a[2].endswith(f"{LABEL}.plist"):
            return services.subprocess.CompletedProcess(a, 5, "", "Bootstrap failed: 5: Input/output error\n")
        return inner(*a)
    monkeypatch.setattr(services, "_launchctl", launchctl)
    assert services.main(["install", "--only", "talk"]) == 2
    assert "pair   " not in capsys.readouterr().out


def test_no_pair_line_when_a_hand_talk_is_left(tk, capsys):
    services.AGENTS.mkdir(parents=True)
    (services.AGENTS / f"{LABEL}.plist").write_bytes(plistlib.dumps(_template()))
    services.main(["install", "--only", "talk"])
    out = capsys.readouterr().out
    assert "pair   " not in out and "talk   answers Host" not in out
