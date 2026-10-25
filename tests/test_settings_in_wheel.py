"""The settings schemas ship in the wheel, and install refuses without them (EARS-160).

From the kit (a wheel in a venv), install_schema() globbed <venv>/lib/python3.X/settings/,
found nothing, printed `schema:  in ...` and succeeded: Settings had no schema to draw. The
wheel now ships settings/*.schema.json as package data, ears/_settings/ (symlinks into
settings/ in the checkout; the build copies the files), found as the keeper is: the checkout
first, then the package. Neither: NoSchema, and `ears services install` refuses before it
writes anything.
"""
import contextlib
import glob
import importlib.resources
import io
import json
import os
import shutil
import subprocess
from importlib.resources import files
from pathlib import Path

import pytest

from ears import services

REPO = Path(__file__).resolve().parents[1]


def test_schemas_is_every_schema_in_settings():
    # a new settings/NAME.schema.json must be added to SCHEMAS, or a wheel install would drop it
    assert sorted(services.SCHEMAS) == sorted(p.name for p in (REPO / "settings").glob("*.schema.json"))


def test_the_settings_glob_ships_exactly_the_schemas():
    # what setuptools copies: the glob under src/ears/, each the same bytes as settings/'s
    pkg = REPO / "src" / "ears"
    matched = sorted(Path(m).name for m in glob.glob(os.path.join(pkg, "_settings/*.schema.json"))
                     if os.path.isfile(m))
    assert matched == sorted(services.SCHEMAS)
    for name in services.SCHEMAS:
        got = files("ears") / "_settings" / name
        assert got.is_file(), f"ears/_settings/{name} does not resolve to a file"
        assert got.read_bytes() == (REPO / "settings" / name).read_bytes()


def test_a_checkout_copies_its_own_settings():
    assert services.schema_files() == [REPO / "settings" / n for n in services.SCHEMAS]


def _wheel_layout(monkeypatch, tmp_path, with_schemas=True):
    """ROOT as an installed wheel has it (<venv>/lib/python3.X: no settings/), and an ears
    package whose _settings/ holds real files, as the build copies them, or nothing at all."""
    monkeypatch.setattr(services, "ROOT", tmp_path / "root")
    pkg = tmp_path / "site-packages" / "ears"
    (pkg / "_settings").mkdir(parents=True)
    if with_schemas:
        for name in services.SCHEMAS:
            shutil.copyfile(REPO / "settings" / name, pkg / "_settings" / name)
    monkeypatch.setattr(importlib.resources, "files", lambda p: pkg if p == "ears" else files(p))
    return pkg


def _settings_env(monkeypatch, tmp_path):
    ears_dir, mouth_dir = tmp_path / "home" / "ears", tmp_path / "home" / "mouth"
    monkeypatch.setenv("EARS_CONF", str(ears_dir / "ears.json"))
    monkeypatch.setenv("MOUTH_SETTINGS", str(mouth_dir / "mouth.json"))
    return ears_dir, mouth_dir


def test_from_a_wheel_install_copies_the_three_schemas_from_the_package(monkeypatch, tmp_path):
    pkg = _wheel_layout(monkeypatch, tmp_path)
    assert services.schema_files() == [pkg / "_settings" / n for n in services.SCHEMAS]
    ears_dir, mouth_dir = _settings_env(monkeypatch, tmp_path)
    out = services.install_schema()
    for name, d in (("ears", ears_dir), ("ears-sounds", ears_dir), ("mouth", mouth_dir)):
        assert json.loads((d / f".{name}.schema.json").read_text()) == \
            json.loads((REPO / "settings" / f"{name}.schema.json").read_text())
    assert out == f"schema: .ears-sounds.schema.json, .ears.schema.json, .mouth.schema.json in {ears_dir}"


def test_no_schema_anywhere_refuses_and_copies_nothing(monkeypatch, tmp_path):
    _wheel_layout(monkeypatch, tmp_path, with_schemas=False)
    ears_dir, mouth_dir = _settings_env(monkeypatch, tmp_path)
    with pytest.raises(services.NoSchema, match=r"no ears-sounds\.schema\.json: .*ears/_settings/"):
        services.install_schema()
    assert not ears_dir.exists() and not mouth_dir.exists()


def test_one_schema_missing_refuses_before_copying_the_others(monkeypatch, tmp_path):
    pkg = _wheel_layout(monkeypatch, tmp_path)
    (pkg / "_settings" / "mouth.schema.json").unlink()
    ears_dir, mouth_dir = _settings_env(monkeypatch, tmp_path)
    with pytest.raises(services.NoSchema, match=r"no mouth\.schema\.json"):
        services.install_schema()
    assert not ears_dir.exists() and not mouth_dir.exists()


def _look(*a):
    """launchctl, read-only and with nothing loaded: it may only look (list, print), and never
    the real one."""
    if a[0] not in ("list", "print"):
        raise AssertionError(f"launchctl called: {a}")
    return subprocess.CompletedProcess(a, 113 if a[0] == "print" else 0, "", "")


def test_services_install_refuses_without_schemas_and_writes_nothing(monkeypatch, tmp_path):
    _wheel_layout(monkeypatch, tmp_path, with_schemas=False)
    monkeypatch.setattr(services, "keeper_script", lambda: tmp_path / "ears-keeper.sh")   # the keeper is found
    monkeypatch.setattr(services, "AGENTS", tmp_path / "agents")
    monkeypatch.setattr(services, "LOGS", tmp_path / "logs")

    def boom(*a, **kw):
        raise AssertionError(f"install went past the refusal: {a}")

    def look(*a):
        """A dry run goes on listing past the refusal (EARS-449): launchctl may only look."""
        if a[0] not in ("list", "print"):
            boom(*a)
        return subprocess.CompletedProcess(a, 113 if a[0] == "print" else 0, "", "")
    monkeypatch.setattr(services, "_launchctl", look)
    for name in ("silero", "seed_stt", "install_schema"):
        monkeypatch.setattr(services, name, boom)
    for dry in (True, False):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            assert services.install(["mouth", "keeper"], dry_run=dry) == 2
        assert "REFUSED" in err.getvalue() and "ears-sounds.schema.json" in err.getvalue()
        assert "schema:" not in out.getvalue()                    # never the empty schema line
    assert not (tmp_path / "agents").exists() and not (tmp_path / "logs").exists()


def test_the_schema_refusal_spares_an_install_without_the_keeper(monkeypatch, tmp_path, capsys):
    _wheel_layout(monkeypatch, tmp_path, with_schemas=False)
    monkeypatch.setattr(services, "LOGS", tmp_path / "logs")
    monkeypatch.setattr(services, "_launchctl", _look)
    assert services.install(["mouth"], dry_run=True) == 0
    assert "dev.sessionkit.voice.ears-mouth" in capsys.readouterr().out
