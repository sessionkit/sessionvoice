"""The settings-schema copies follow an upgrade, not only an install.

install_schema() was the copies' only writer, and only `ears services install` ran it, so an
upgrade (a new wheel, a pull) left each copy holding the old schema, and a settings screen
drawn from it refused the keys the new version writes. Now the copy is compared by its BYTES
with the shipped schema: a different one is written whole, an equal one is left alone (never
by mtime). `ears services schema` runs it with no reinstall; the keeper runs it, --quiet, at
its start. A stranded settings file's schema is still never written.

Every test runs under a fake HOME in tmp_path, with a fake shipped settings/ (services.ROOT
moved): no launchctl, no network, nothing of the real home.
"""
import importlib.resources
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ears import home, services
from tests import env_fence

REPO = Path(__file__).resolve().parents[1]
KEEPER = REPO / "keeper" / "ears-keeper.sh"
DOTS = [f".{n}" for n in services.SCHEMAS]
HOUR_NS = 3600 * 10**9


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    for n in ("EARS_CONF", "EARS_WAKE", "EARS_RECORD", "EARS_RECORD_DAYS"):
        monkeypatch.delenv(n, raising=False)
    env_fence.delenv(monkeypatch, "MOUTH_SETTINGS")
    monkeypatch.setattr(home, "_said", set())   # a fresh "process" for the once-per-process rule
    return h


@pytest.fixture
def shipped(tmp_path, monkeypatch):
    """A checkout's settings/, as the package ships it: each schema copied from the repo.
    ``ship(name, extra)`` stands in for an upgrade that changes one schema."""
    root = tmp_path / "root"
    (root / "settings").mkdir(parents=True)
    for n in services.SCHEMAS:
        (root / "settings" / n).write_bytes((REPO / "settings" / n).read_bytes())
    monkeypatch.setattr(services, "ROOT", root)

    def ship(name, extra="added_by_the_upgrade"):
        p = root / "settings" / name
        d = json.loads(p.read_text())
        d.setdefault("properties", {})[extra] = {"type": "boolean"}
        p.write_text(json.dumps(d, indent=2) + "\n")
        return p
    ship.dir = root / "settings"
    return ship


def _copies(h):
    return {n: home.settings_dir(h) / n for n in DOTS}


def _stat(p):
    s = p.stat()
    return s.st_ino, s.st_mtime_ns


def test_a_stale_copy_is_brought_up_to_date(fake_home, shipped):
    services.install_schema()                         # the install
    new = shipped("ears.schema.json")                 # the upgrade changes one schema
    c = _copies(fake_home)
    assert c[".ears.schema.json"].read_bytes() != new.read_bytes()   # stale
    line = services.install_schema()
    assert c[".ears.schema.json"].read_bytes() == new.read_bytes()
    assert line == (f"schema: .ears.schema.json in {home.settings_dir(fake_home)}; current, "
                    "nothing written: .ears-sounds.schema.json, .mouth.schema.json")
    assert not list(home.settings_dir(fake_home).glob("*.tmp"))


def test_an_equal_copy_is_not_written(fake_home, shipped):
    services.install_schema()
    before = {n: _stat(p) for n, p in _copies(fake_home).items()}
    line = services.install_schema()
    assert {n: _stat(p) for n, p in _copies(fake_home).items()} == before   # same inode, mtime
    assert line == (f"schema: none in {home.settings_dir(fake_home)}; current, nothing written: "
                    + ", ".join(DOTS))


def test_bytes_decide_never_mtime(fake_home, shipped):
    services.install_schema()
    c = _copies(fake_home)
    old = c[".ears-sounds.schema.json"]
    hour_ago = old.stat().st_mtime_ns - HOUR_NS       # moments taken from the copy's own stat
    os.utime(old, ns=(hour_ago, hour_ago))            # an old mtime, the same bytes: current
    assert ".ears-sounds.schema.json" not in services.install_schema().split(";")[0]
    assert old.stat().st_mtime_ns == hour_ago         # left alone, not even touched
    new = shipped("mouth.schema.json")                # different bytes...
    older = c[".mouth.schema.json"].stat().st_mtime_ns - HOUR_NS
    os.utime(new, ns=(older, older))                  # ...in a source OLDER than the copy: written
    assert new.stat().st_mtime_ns < c[".mouth.schema.json"].stat().st_mtime_ns
    assert services.install_schema().startswith("schema: .mouth.schema.json in ")
    assert c[".mouth.schema.json"].read_bytes() == new.read_bytes()


def test_an_unreadable_copy_is_written_again(fake_home, shipped, monkeypatch):
    services.install_schema()
    real = Path.read_bytes

    def deny(self):
        if self.name == ".ears.schema.json":
            raise PermissionError(self)
        return real(self)
    monkeypatch.setattr(Path, "read_bytes", deny)
    assert services.install_schema().startswith("schema: .ears.schema.json in ")


def test_a_stranded_file_s_schema_is_neither_compared_nor_written(fake_home, shipped):
    # ears.json stranded (its 0.1.0 copy there, nothing in the settings folder): its schema is
    # held, the others are brought up to date beside it
    old = home.legacy_path("ears.json", fake_home)
    old.parent.mkdir(parents=True)
    old.write_text('{"record": true}')
    shipped("ears-sounds.schema.json")
    line = services.install_schema()
    d = home.settings_dir(fake_home)
    assert not (d / ".ears.schema.json").exists() and not (d / "ears.json").exists()
    assert (d / ".ears-sounds.schema.json").read_bytes() == \
        (shipped.dir / "ears-sounds.schema.json").read_bytes()
    assert line.endswith("; NOT copied (stranded, nothing written): ears.schema.json")


def test_a_host_with_every_file_stranded_gets_nothing(fake_home, shipped, capsys):
    for name in ("ears.json", "ears-sounds.json", "mouth.json"):
        old = home.legacy_path(name, fake_home)
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text("{}")
    assert services.main(["schema"]) == 0
    out, err = capsys.readouterr()
    assert "NOT copied (stranded, nothing written)" in out and "is missing" in err
    assert not home.settings_dir(fake_home).exists()


# --- the verb: ears services schema ------------------------------------------------------


def test_the_verb_writes_and_says_so(fake_home, shipped, capsys):
    assert services.main(["schema"]) == 0
    out, err = capsys.readouterr()
    assert out == f"schema: {', '.join(DOTS)} in {home.settings_dir(fake_home)}\n" and err == ""
    assert all(p.is_file() for p in _copies(fake_home).values())


def test_a_dry_run_writes_nothing(fake_home, shipped, capsys):
    assert services.main(["schema", "--dry-run"]) == 0
    assert capsys.readouterr().out.startswith(f"schema (dry run): would write {', '.join(DOTS)} in ")
    assert not home.settings_dir(fake_home).exists()
    services.install_schema()
    shipped("mouth.schema.json")
    stale = {n: _stat(p) for n, p in _copies(fake_home).items()}
    assert services.main(["schema", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("schema (dry run): would write .mouth.schema.json in ")
    assert {n: _stat(p) for n, p in _copies(fake_home).items()} == stale


def test_quiet_says_only_what_it_wrote(fake_home, shipped, capsys):
    assert services.main(["schema", "--quiet"]) == 0
    assert capsys.readouterr().out.startswith(f"schema: {', '.join(DOTS)} in ")
    assert services.main(["schema", "--quiet"]) == 0          # all current: silent
    assert capsys.readouterr() == ("", "")
    shipped("ears.schema.json")
    assert services.main(["schema", "--quiet"]) == 0
    assert capsys.readouterr() == (f"schema: .ears.schema.json in {home.settings_dir(fake_home)}\n", "")


def test_quiet_says_nothing_of_a_stranded_file(fake_home, shipped, capsys):
    old = home.legacy_path("mouth.json", fake_home)
    old.parent.mkdir(parents=True)
    old.write_text("{}")
    services.install_schema()                          # the other two, written
    capsys.readouterr()
    home._said.clear()                                 # a fresh process: the keeper's start
    assert services.main(["schema", "--quiet"]) == 0
    assert capsys.readouterr() == ("", "")
    assert not (home.settings_dir(fake_home) / ".mouth.schema.json").exists()


def test_no_shipped_schema_refuses_and_writes_nothing(fake_home, shipped, capsys, tmp_path,
                                                       monkeypatch):
    (shipped.dir / "mouth.schema.json").unlink()      # not in the checkout...
    pkg = tmp_path / "site-packages" / "ears"
    (pkg / "_settings").mkdir(parents=True)           # ...nor in the package
    real = importlib.resources.files
    monkeypatch.setattr(importlib.resources, "files", lambda p: pkg if p == "ears" else real(p))
    assert services.main(["schema"]) == 2
    out, err = capsys.readouterr()
    assert out == "" and err.startswith("ears services schema: REFUSED: no mouth.schema.json")
    assert services.main(["schema", "--quiet"]) == 2
    assert capsys.readouterr() == ("", "")
    assert not home.settings_dir(fake_home).exists()


def test_the_fresh_install_line_is_unchanged(fake_home, shipped):
    assert services.install_schema() == f"schema: {', '.join(DOTS)} in {home.settings_dir(fake_home)}"


# --- the keeper ---------------------------------------------------------------------------


def test_the_keeper_runs_it_quietly_once_before_its_loop():
    s = KEEPER.read_text()
    call = '"$PY" -m ears.services schema --quiet 2>/dev/null || true'
    assert s.count("ears.services schema") == 1 and call in s
    assert s.index(call) < s.index("\nwhile :; do")
    assert s.index('[ -x "$PY" ]') < s.index(call)   # after the python is checked


def test_the_module_runs_as_the_keeper_runs_it(tmp_path):
    # the keeper's own line, in a real process: python -m ears.services schema --quiet, in a
    # temp HOME and with none of the settings variables (launchd gives the keeper none)
    h = tmp_path / "home"
    h.mkdir()
    env = {k: v for k, v in os.environ.items()
           if k not in ("EARS_CONF", "MOUTH_SETTINGS", "VOICEMODE_MOUTH_SETTINGS")}
    env["HOME"] = str(h)

    def run():
        return subprocess.run([sys.executable, "-m", "ears.services", "schema", "--quiet"],
                              env=env, capture_output=True, text=True, timeout=60, cwd=tmp_path)
    first = run()
    d = home.settings_dir(h)
    assert first.returncode == 0 and first.stderr == ""
    assert first.stdout == f"schema: {', '.join(DOTS)} in {d}\n"
    for n in services.SCHEMAS:
        assert (d / f".{n}").read_bytes() == (REPO / "settings" / n).read_bytes()
    second = run()
    assert (second.returncode, second.stdout, second.stderr) == (0, "", "")
