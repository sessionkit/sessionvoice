"""bin/mouth-timeline runs the checkout's OWN python, never PATH's python3 (EARS-402).

PATH's python3 on a clean Mac is Apple's 3.9.6, below the package's 3.10 floor: importing
ears/events.py (`int | None`) raised TypeError and every notifier call failed. The wrapper
now runs <repo>/.venv/bin/python, or $MOUTH_PYTHON when that is set, and with neither it
says so in one line and exits 2.

The first three tests are portable: a scratch tree holds a COPY of the wrapper (a symlink
would resolve back to this repo), src/ linked to this repo's, and .venv/bin/python linked to
the python running the tests. A fake python3 first on PATH leaves a marker and exits 97 if
anything runs it. The last test is the real stranger's case and needs a system python below
the floor; without one it skips, saying why.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.system_python import below_floor

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "bin" / "mouth-timeline"


@pytest.fixture
def scratch(tmp_path):
    """(tree, env, marker): a wrapper tree with no .venv yet, and an env whose PATH puts a fake
    python3 first. Only named variables, nothing copied from the live environment."""
    tree = tmp_path / "checkout"
    (tree / "bin").mkdir(parents=True)
    shutil.copy2(WRAPPER, tree / "bin" / "mouth-timeline")
    (tree / "src").symlink_to(ROOT / "src")
    fake = tmp_path / "fakebin"
    fake.mkdir()
    marker = tmp_path / "path-python3-ran"
    (fake / "python3").write_text(f"#!/bin/sh\necho ran > '{marker}'\nexit 97\n")
    (fake / "python3").chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    env = {"PATH": f"{fake}:/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(home), "LANG": "C.UTF-8",
           "VOICEMODE_BASE_DIR": str(tmp_path / "base"), "MOUTH_DIR": str(tmp_path / "mouth")}
    return tree, env, marker


def _venv(tree: Path, python: str) -> None:
    py = tree / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.symlink_to(python)


def _run(cmd, env):
    return subprocess.run([str(cmd), "--future", "--json"], env=env, cwd="/",
                          capture_output=True, text=True, timeout=30)


def test_the_wrapper_runs_its_own_venv_python_never_paths_python3(scratch, tmp_path):
    tree, env, marker = scratch
    _venv(tree, sys.executable)
    link = tmp_path / "elsewhere" / "mouth-timeline"     # and through a symlink, as a plugin's bin/
    link.parent.mkdir()
    link.symlink_to(tree / "bin" / "mouth-timeline")
    for cmd in (tree / "bin" / "mouth-timeline", link):
        p = _run(cmd, env)
        assert p.returncode == 0, (cmd, p.stderr)
        assert json.loads(p.stdout)["v"] == 1
        assert not marker.exists(), f"{cmd} ran PATH's python3"


def test_with_no_venv_it_refuses_in_one_line_naming_the_floor(scratch):
    tree, env, marker = scratch
    p = _run(tree / "bin" / "mouth-timeline", env)
    assert p.returncode == 2, p.stderr
    assert p.stdout == ""
    lines = p.stderr.splitlines()
    assert len(lines) == 1, p.stderr
    for part in ("mouth-timeline: no python at", "3.10", "uv sync", "MOUTH_PYTHON"):
        assert part in lines[0], (part, lines[0])
    assert "Traceback" not in p.stderr
    assert not marker.exists(), "it fell back to PATH's python3"


def test_mouth_python_is_an_explicit_override(scratch):
    tree, env, marker = scratch
    p = _run(tree / "bin" / "mouth-timeline", {**env, "MOUTH_PYTHON": sys.executable})
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["v"] == 1
    assert not marker.exists()


def test_the_real_wrapper_works_with_the_system_python_first_on_path(tmp_path):
    """The stranger's Mac: PATH's python3 is Apple's 3.9.6, and the wrapper still works."""
    system, why = below_floor()
    if system is None:
        pytest.skip(f"EARS-402 pin, real-host case not run: {why}")
    own = ROOT / ".venv" / "bin" / "python"
    if not os.access(own, os.X_OK):
        pytest.skip(f"EARS-402 pin, real-host case not run: no {own} (run `uv sync` here); "
                    "the scratch-tree tests above still prove the pin")
    first = tmp_path / "first"
    first.mkdir()
    (first / "python3").symlink_to(system)
    home = tmp_path / "home"
    home.mkdir()
    env = {"PATH": f"{first}:/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(home), "LANG": "C.UTF-8",
           "VOICEMODE_BASE_DIR": str(tmp_path / "base"), "MOUTH_DIR": str(tmp_path / "mouth")}
    on_path = subprocess.run(["/bin/sh", "-c", "python3 -c 'import sys; print(sys.version_info[:2] < (3, 10))'"],
                             env=env, capture_output=True, text=True, timeout=30)
    assert on_path.stdout.strip() == "True", on_path      # the setup really is below the floor
    p = _run(WRAPPER, env)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["v"] == 1
