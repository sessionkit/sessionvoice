"""The keeper ships in the wheel (EARS-202 G28).

From the kit (a wheel in a venv), `ears services install` wrote a keeper agent pointing at
<venv>/lib/python3.X/keeper/ears-keeper.sh, which no install made: services.ROOT assumed a
git checkout.
The wheel now ships what the keeper agent runs as package data, ears/_keeper/ (symlinks into
keeper/ in the checkout; the build copies the files), and services resolves the keeper from
the checkout first, then the package, and refuses when neither has it. The speaker side-car
ships the same way, as ears/_speakers/ (EARS-258).
"""
import io
import subprocess
import contextlib
from importlib.resources import files
from pathlib import Path

import pytest

from ears import services

REPO = Path(__file__).resolve().parents[1]
SHIPPED = ("ears-keeper.sh", "call-ears", "input-watch.swift", "ears-mail.py",   # ears-mail: EARS-222
           "ears-sounds.py", "mouth-sounds.py", "held_stream.py")                # the sounds: EARS-429


def _package_data() -> list[str]:
    tomllib = pytest.importorskip("tomllib")
    conf = tomllib.loads((REPO / "pyproject.toml").read_text())
    return conf["tool"]["setuptools"]["package-data"]["ears"]


def test_the_wheel_lists_what_the_keeper_runs():
    # the keeper's files by name, and the speaker side-car's directory (EARS-258), the speech
    # server's lock (EARS-291) and the settings schemas (EARS-160) by one glob each
    assert sorted(_package_data()) == sorted([*(f"_keeper/{f}" for f in SHIPPED), "_speakers/*", "_speech/*",
                                              "_settings/*.schema.json"])


def test_the_speakers_glob_ships_exactly_what_the_side_car_needs():
    """EARS-258 do-005: `_speakers/*` is a glob, so what it matches is pinned here: every file
    the speakers service runs or reads (test_speakers_in_package.SCRIPTS, each the same bytes as
    speakers/'s), and nothing else. setuptools globs the pattern under the package dir and keeps
    the files (a symlink to a file is a file, so the build copies it)."""
    import glob
    import os
    from test_speakers_in_package import SCRIPTS
    pkg = REPO / "src" / "ears"
    matched = sorted(Path(m).name for pat in _package_data() if pat.startswith("_speakers/")
                     for m in glob.glob(os.path.join(pkg, pat)) if os.path.isfile(m))
    assert matched == sorted(SCRIPTS)
    for name in SCRIPTS:
        assert (files("ears") / "_speakers" / name).read_bytes() == (REPO / "speakers" / name).read_bytes()


@pytest.mark.parametrize("name", SHIPPED)
def test_the_package_resources_hold_the_keeper_files(name):
    # what the wheel copies: each must resolve to a real file, the same bytes as keeper/'s
    got = files("ears") / "_keeper" / name
    assert got.is_file(), f"ears/_keeper/{name} does not resolve to a file"
    assert got.read_bytes() == (REPO / "keeper" / name).read_bytes()


def test_the_keeper_runs_its_children_from_beside_itself():
    # the shipped set is what ears-keeper.sh reaches by its own directory; a new one must ship
    sh = (REPO / "keeper/ears-keeper.sh").read_text()
    assert '"$(dirname "$0")" && pwd)/call-ears' in sh
    assert '"$(dirname "$0")" && pwd)/input-watch.swift' in sh


def test_a_checkout_runs_its_own_keeper():
    assert services.keeper_script() == REPO / "keeper/ears-keeper.sh"
    assert services.spec("keeper")["ProgramArguments"][1] == str(REPO / "keeper/ears-keeper.sh")


def test_from_a_wheel_the_keeper_is_the_shipped_one(monkeypatch, tmp_path):
    # installed, ROOT is <venv>/lib/python3.X: no keeper/ there
    monkeypatch.setattr(services, "ROOT", tmp_path)
    got = services.keeper_script()
    assert got.is_file() and got.parent.name == "_keeper" and got.parent.parent.name == "ears"
    assert services.spec("keeper")["ProgramArguments"][1] == str(got)


def _no_keeper_anywhere(monkeypatch, tmp_path):
    import importlib.resources
    monkeypatch.setattr(services, "ROOT", tmp_path / "root")
    monkeypatch.setattr(importlib.resources, "files", lambda pkg: tmp_path / "pkg")


def _look(*a):
    """launchctl, read-only and with nothing loaded: it may only look (list, print), and never
    the real one."""
    if a[0] not in ("list", "print"):
        raise AssertionError(f"launchctl called: {a}")
    return subprocess.CompletedProcess(a, 113 if a[0] == "print" else 0, "", "")


def test_no_keeper_anywhere_refuses_and_writes_nothing(monkeypatch, tmp_path):
    _no_keeper_anywhere(monkeypatch, tmp_path)
    monkeypatch.setattr(services, "AGENTS", tmp_path / "agents")
    monkeypatch.setattr(services, "LOGS", tmp_path / "logs")
    # a dry run goes on listing past the refusal: launchctl may only look
    monkeypatch.setattr(services, "_launchctl", _look)
    for dry in (True, False):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            assert services.install(["mouth", "keeper"], dry_run=dry) == 2
        assert "REFUSED" in err.getvalue() and "ears-keeper.sh" in err.getvalue()
    assert not (tmp_path / "agents").exists() and not (tmp_path / "logs").exists()


def test_no_keeper_refusal_spares_an_install_without_the_keeper(monkeypatch, tmp_path, capsys):
    _no_keeper_anywhere(monkeypatch, tmp_path)
    monkeypatch.setattr(services, "LOGS", tmp_path / "logs")
    monkeypatch.setattr(services, "_launchctl", _look)
    assert services.install(["mouth"], dry_run=True) == 0
    assert "dev.sessionkit.voice.ears-mouth" in capsys.readouterr().out


def test_call_ears_without_a_venv_beside_it_runs_captures_on_its_own_python():
    # shipped, call-ears has no <root>/.venv: it must not name a python that is not there
    src = (REPO / "keeper/call-ears").read_text()
    assert 'else sys.executable' in src
