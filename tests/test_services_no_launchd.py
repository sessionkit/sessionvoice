"""EARS-522: on a host with no launchctl (Linux), `ears services status|install|uninstall` and
`ears speakers`' restart refuse in one plain line and exit 2, never a launchctl traceback.

The Doctor's Fedora dry run hit `FileNotFoundError: 'launchctl'` from services._launchctl, which
ran launchctl with no lookup first. The missing host is faked here, never needed: shutil.which
answers None for launchctl (the lookup services.has_launchd makes), and _launchctl raises what a
real Linux host raises, so a verb that still reached it would fail the test, not pass it.
weights and schema never reach launchctl, so they keep working there."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ears import cli, services, speakers
from test_services_install_checks import fence  # noqa: F401  (a fixture)

LINE = "ears services: macOS only (launchd)"


@pytest.fixture
def linux(fence, monkeypatch):
    """The fence, then no launchctl: the lookup misses it, and running it raises as on Linux."""
    real = shutil.which
    monkeypatch.setattr(shutil, "which", lambda cmd, *a, **k: None if cmd == "launchctl" else real(cmd, *a, **k))

    def launchctl(*a):
        fence["calls"]["launchctl"].append(a)
        raise FileNotFoundError(2, "No such file or directory", "launchctl")
    monkeypatch.setattr(services, "_launchctl", launchctl)
    return fence


def test_the_lookup_is_what_decides(linux):
    assert services.has_launchd() is False


@pytest.mark.parametrize("argv", [
    ["status"],
    ["install"],
    ["install", "--dry-run"],
    ["install", "--only", "mouth", "--no-agent"],
    ["uninstall"],
    ["uninstall", "--dry-run"],
], ids=lambda a: " ".join(a))
def test_each_launchd_verb_refuses_in_one_line(linux, capsys, argv):
    assert cli.main(["services", *argv]) == 2
    out, err = capsys.readouterr()
    assert err.strip().splitlines() == [LINE]
    assert "Traceback" not in out + err
    assert linux["calls"]["launchctl"] == []          # refused before any launchctl call
    assert not services.AGENTS.exists() or not any(services.AGENTS.iterdir())   # nothing written


@pytest.mark.parametrize("argv", [["weights"], ["weights", "--json"], ["schema", "--dry-run"]],
                         ids=lambda a: " ".join(a))
def test_weights_and_schema_still_work_without_launchd(linux, capsys, argv):
    assert services.main(argv) == 0
    out, err = capsys.readouterr()
    assert LINE not in err and "Traceback" not in out + err
    assert linux["calls"]["launchctl"] == []


def test_help_still_answers_without_launchd(linux, capsys):
    with pytest.raises(SystemExit) as e:
        services.main(["--help"])
    assert e.value.code == 0
    assert "ears services" in capsys.readouterr().out


def test_speakers_restart_refuses_in_one_line(linux, monkeypatch, capsys):
    """`ears speakers name` ends by restarting the job (services._launchctl kickstart): on a
    host without launchd it says so, through speakers.main's REFUSED line, and exits 2."""
    monkeypatch.setattr(speakers, "name", lambda d, specs: speakers._restart())
    assert cli.main(["speakers", "name", "somewhere", "L0=alice"]) == 2
    out, err = capsys.readouterr()
    assert err.strip().splitlines() == [
        "ears speakers: REFUSED: macOS only (launchd): no job to restart on the new references"]
    assert "Traceback" not in out + err
    assert linux["calls"]["launchctl"] == []


def test_macos_unchanged_status_still_asks_launchctl(fence, capsys):
    """With launchctl present (the fence fakes it), status runs as before: it lists the jobs."""
    if not services.has_launchd():
        pytest.skip("this host has no launchctl")
    services.status()
    assert ("list",) in fence["calls"]["launchctl"]
    assert LINE not in capsys.readouterr().err


def test_a_real_process_with_no_launchctl_on_path_refuses(tmp_path):
    """End to end, no patching: a child whose PATH holds no launchctl prints the line, exits 2."""
    empty = tmp_path / "bin"
    empty.mkdir()
    env = {**os.environ, "PATH": str(empty), "HOME": str(tmp_path / "home")}
    r = subprocess.run([sys.executable, "-c", "import sys; from ears import cli; "
                        "sys.exit(cli.main(['services', 'status']))"],
                       capture_output=True, text=True, env=env, cwd=Path(__file__).resolve().parents[1],
                       timeout=60)
    assert r.returncode == 2, r.stderr
    assert r.stderr.strip().splitlines()[-1] == LINE
    assert "Traceback" not in r.stderr
