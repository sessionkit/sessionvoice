"""bin/mouth-timeline runs the checkout's OWN python, never PATH's python3 (EARS-402).

PATH's python3 on a clean Mac is Apple's 3.9.6, below the package's 3.10 floor: importing
ears/events.py (`int | None`) raised TypeError and every notifier call failed. The wrapper
now runs $MOUTH_PYTHON when that is set, else <repo>/.venv/bin/python, else the installed uv
tool's python (<tools>/sessionkit, then <tools>/sessionvoice: EARS-440, a plugin's copy of bin/
has no .venv), and with none of them it says so in one line and exits 2.

Every test but the last is portable: a scratch tree holds a COPY of the wrapper (a symlink
would resolve back to this repo), src/ linked to this repo's, and .venv/bin/python linked to
the python running the tests. A fake python3 first on PATH leaves a marker and exits 97 if
anything runs it. HOME, UV_TOOL_DIR and XDG_DATA_HOME are always scratch (or absent), so a
host that really has the kit installed answers nothing here. The last test is the real
stranger's case and needs a system python below the floor; without one it skips, saying why.
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


@pytest.mark.parametrize("decoy", ["none", "uv-tool-dir-wins-over-home"])
def test_with_no_venv_and_no_tool_it_refuses_in_one_line_naming_an_install_fix(scratch, tmp_path, decoy):
    tree, env, marker = scratch
    if decoy != "none":     # a kit under the HOME default does not count once UV_TOOL_DIR is set
        _recorder(Path(env["HOME"]) / ".local/share/uv/tools/sessionkit/bin/python", tmp_path / "decoy")
        env = {**env, "UV_TOOL_DIR": str(tmp_path / "empty-tools")}
    p = _run(tree / "bin" / "mouth-timeline", env)
    assert p.returncode == 2, p.stderr
    assert p.stdout == ""
    lines = p.stderr.splitlines()
    assert len(lines) == 1, p.stderr
    for part in ("mouth-timeline: no python found", "3.10", "uv tool install --python 3.14 sessionkit",
                 "uv sync", "MOUTH_PYTHON"):
        assert part in lines[0], (part, lines[0])
    # the fix that applies to a stranger's install comes first; `uv sync` is the checkout's
    assert lines[0].index("uv tool install") < lines[0].index("uv sync"), lines[0]
    assert "Traceback" not in p.stderr
    assert not marker.exists(), "it fell back to PATH's python3"
    assert not (tmp_path / "decoy").exists()


def _recorder(path: Path, log: Path) -> Path:
    """A fake python at `path` that writes its argv and PYTHONPATH to `log`, then runs the real
    interpreter with the same argv, so the reader still answers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'''#!/bin/sh\nprintf '%s\\n' "$@" "PYTHONPATH=$PYTHONPATH" > '{log}'\nexec '{sys.executable}' "$@"\n''')
    path.chmod(0o755)
    return path


def _tool_env(env: dict, tmp_path: Path, where: str) -> tuple[dict, Path]:
    """(env, tools): the env that makes uv's tool dir `tools`, by the variable named `where`."""
    if where == "UV_TOOL_DIR":
        tools = tmp_path / "uvtools"
        return {**env, "UV_TOOL_DIR": str(tools)}, tools
    if where == "XDG_DATA_HOME":
        return {**env, "XDG_DATA_HOME": str(tmp_path / "xdg")}, tmp_path / "xdg" / "uv" / "tools"
    return env, Path(env["HOME"]) / ".local" / "share" / "uv" / "tools"     # the HOME default


@pytest.mark.parametrize("where,tool", [("UV_TOOL_DIR", "sessionkit"), ("UV_TOOL_DIR", "sessionvoice"),
                                        ("XDG_DATA_HOME", "sessionkit"), ("HOME", "sessionkit")])
def test_with_no_venv_it_runs_the_installed_tools_python_on_its_own_src(scratch, tmp_path, where, tool):
    """The stranger's install (EARS-440): a plugin copy with no .venv, and the kit's uv tool."""
    tree, env, marker = scratch
    env, tools = _tool_env(env, tmp_path, where)
    log = tmp_path / "tool-python-ran"
    _recorder(tools / tool / "bin" / "python", log)
    p = _run(tree / "bin" / "mouth-timeline", env)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["v"] == 1
    argv = log.read_text().splitlines()
    assert argv[:3] == ["-S", "-m", "mouth.timeline"], argv          # still no site-packages
    first = argv[-1].removeprefix("PYTHONPATH=").split(":")[0]     # the suite's fences follow it
    assert first == f"{tree}/bin/../src", argv                         # the copy's own src/
    assert not marker.exists()


def test_the_kit_tool_wins_over_a_lone_sessionvoice_tool(scratch, tmp_path):
    tree, env, marker = scratch
    env, tools = _tool_env(env, tmp_path, "UV_TOOL_DIR")
    _recorder(tools / "sessionkit" / "bin" / "python", tmp_path / "kit")
    _recorder(tools / "sessionvoice" / "bin" / "python", tmp_path / "voice")
    p = _run(tree / "bin" / "mouth-timeline", env)
    assert p.returncode == 0, p.stderr
    assert (tmp_path / "kit").exists() and not (tmp_path / "voice").exists()


def test_a_checkout_venv_wins_over_the_installed_tool(scratch, tmp_path):
    tree, env, marker = scratch
    env, tools = _tool_env(env, tmp_path, "UV_TOOL_DIR")
    _recorder(tree / ".venv" / "bin" / "python", tmp_path / "venv")
    _recorder(tools / "sessionkit" / "bin" / "python", tmp_path / "kit")
    p = _run(tree / "bin" / "mouth-timeline", env)
    assert p.returncode == 0, p.stderr
    assert (tmp_path / "venv").exists() and not (tmp_path / "kit").exists()


def test_mouth_python_beats_the_venv_and_the_tool(scratch, tmp_path):
    tree, env, marker = scratch
    env, tools = _tool_env(env, tmp_path, "UV_TOOL_DIR")
    _recorder(tree / ".venv" / "bin" / "python", tmp_path / "venv")
    _recorder(tools / "sessionkit" / "bin" / "python", tmp_path / "kit")
    mine = _recorder(tmp_path / "mine" / "python", tmp_path / "mine-ran")
    p = _run(tree / "bin" / "mouth-timeline", {**env, "MOUTH_PYTHON": str(mine)})
    assert p.returncode == 0, p.stderr
    assert (tmp_path / "mine-ran").exists()
    assert not (tmp_path / "venv").exists() and not (tmp_path / "kit").exists()


def test_a_missing_mouth_python_is_refused_never_fallen_through(scratch, tmp_path):
    tree, env, marker = scratch
    env, tools = _tool_env(env, tmp_path, "UV_TOOL_DIR")
    _recorder(tools / "sessionkit" / "bin" / "python", tmp_path / "kit")
    gone = tmp_path / "no-such-python"
    p = _run(tree / "bin" / "mouth-timeline", {**env, "MOUTH_PYTHON": str(gone)})
    assert p.returncode == 2, p.stderr
    assert p.stdout == ""
    lines = p.stderr.splitlines()
    assert len(lines) == 1, p.stderr
    assert f"MOUTH_PYTHON={gone}" in lines[0] and "3.10" in lines[0], lines[0]
    assert not (tmp_path / "kit").exists(), "an explicit MOUTH_PYTHON fell through to the tool"


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
