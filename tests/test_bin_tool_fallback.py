"""bin/ears and bin/mouth run the installed uv tool's verb when there is no checkout .venv (EARS-440).

A plugin's copy of bin/ (the plugin cache, or a clone loaded in place) has no .venv. Before,
`uv run --project <copy>` built a second venv inside it, with the capture extras, on the
network; the kit's uv tool already holds the same package. Now, with no .venv, the wrapper
execs <tools>/sessionkit/bin/<verb>, else <tools>/sessionvoice/bin/<verb>, where <tools> is
uv's tool dir (${UV_TOOL_DIR:-${XDG_DATA_HOME:-~/.local/share}/uv/tools}). A checkout keeps
`uv run`, and so does a copy with no tool installed.

All fakes: a scratch tree holds a COPY of the wrapper (a symlink would resolve back to this
repo), a fake `uv` first on PATH and a fake tool console script each write their argv to a
file. HOME, UV_TOOL_DIR and XDG_DATA_HOME are always scratch (or absent), so a host that really
has the kit installed answers nothing here.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VERBS = ("ears", "mouth")


def _stub(path: Path, tag: str, log: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\nprintf '%s\\n' '{tag}' \"$@\" > '{log}'\n")
    path.chmod(0o755)
    return path


@pytest.fixture
def scratch(tmp_path):
    """(tmp, env, uvlog): a copy tree for both verbs with no .venv, and an env whose PATH
    holds a fake uv. Only named variables, nothing copied from the live environment."""
    tree = tmp_path / "copy"
    (tree / "bin").mkdir(parents=True)
    for verb in VERBS:
        shutil.copy2(ROOT / "bin" / verb, tree / "bin" / verb)
    uvlog = tmp_path / "uv-ran"
    _stub(tmp_path / "fakebin" / "uv", "uv", uvlog)
    home = tmp_path / "home"
    home.mkdir()
    env = {"PATH": f"{tmp_path / 'fakebin'}:/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(home),
           "LANG": "C.UTF-8"}
    return tree, env, uvlog


def _tools(env: dict, tmp_path: Path, where: str) -> tuple[dict, Path]:
    """(env, tools): the env that makes uv's tool dir `tools`, by the variable named `where`."""
    if where == "UV_TOOL_DIR":
        return {**env, "UV_TOOL_DIR": str(tmp_path / "uvtools")}, tmp_path / "uvtools"
    if where == "XDG_DATA_HOME":
        return {**env, "XDG_DATA_HOME": str(tmp_path / "xdg")}, tmp_path / "xdg" / "uv" / "tools"
    return env, Path(env["HOME"]) / ".local" / "share" / "uv" / "tools"     # the HOME default


def _run(cmd: Path, env: dict):
    return subprocess.run([str(cmd), "say", "two words"], env=env, cwd="/",
                          capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("verb", VERBS)
@pytest.mark.parametrize("where,tool", [("UV_TOOL_DIR", "sessionkit"), ("UV_TOOL_DIR", "sessionvoice"),
                                        ("XDG_DATA_HOME", "sessionkit"), ("HOME", "sessionkit")])
def test_with_no_venv_it_execs_the_installed_tools_verb(scratch, tmp_path, verb, where, tool):
    tree, env, uvlog = scratch
    env, tools = _tools(env, tmp_path, where)
    log = tmp_path / "tool-ran"
    _stub(tools / tool / "bin" / verb, "tool", log)
    p = _run(tree / "bin" / verb, env)
    assert p.returncode == 0, p.stderr
    assert log.read_text().splitlines() == ["tool", "say", "two words"]   # argv passed through whole
    assert not uvlog.exists(), "it built a venv in the copy with uv run"


@pytest.mark.parametrize("verb", VERBS)
def test_the_kit_tool_wins_over_a_lone_sessionvoice_tool(scratch, tmp_path, verb):
    tree, env, uvlog = scratch
    env, tools = _tools(env, tmp_path, "UV_TOOL_DIR")
    _stub(tools / "sessionkit" / "bin" / verb, "kit", tmp_path / "kit")
    _stub(tools / "sessionvoice" / "bin" / verb, "voice", tmp_path / "voice")
    assert _run(tree / "bin" / verb, env).returncode == 0
    assert (tmp_path / "kit").exists() and not (tmp_path / "voice").exists()


@pytest.mark.parametrize("verb", VERBS)
def test_a_checkout_venv_keeps_uv_run_even_with_a_tool_installed(scratch, tmp_path, verb):
    tree, env, uvlog = scratch
    (tree / ".venv").mkdir()
    env, tools = _tools(env, tmp_path, "UV_TOOL_DIR")
    _stub(tools / "sessionkit" / "bin" / verb, "tool", tmp_path / "tool")
    p = _run(tree / "bin" / verb, env)
    assert p.returncode == 0, p.stderr
    assert uvlog.read_text().splitlines() == ["uv", "run", "--quiet", "--project", str(tree),
                                              "--extra", "capture", verb, "say", "two words"]
    assert not (tmp_path / "tool").exists()


@pytest.mark.parametrize("verb", VERBS)
def test_with_neither_it_runs_uv_as_before(scratch, verb):
    tree, env, uvlog = scratch
    p = _run(tree / "bin" / verb, env)
    assert p.returncode == 0, p.stderr
    assert uvlog.read_text().splitlines()[:2] == ["uv", "run"]


@pytest.mark.parametrize("verb", VERBS)
def test_the_tool_needs_no_uv_on_path(scratch, tmp_path, verb):
    tree, env, uvlog = scratch
    env, tools = _tools(env, tmp_path, "UV_TOOL_DIR")
    _stub(tools / "sessionkit" / "bin" / verb, "tool", tmp_path / "tool")
    p = _run(tree / "bin" / verb, {**env, "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"})
    assert p.returncode == 0, p.stderr
    assert (tmp_path / "tool").exists()


@pytest.mark.parametrize("verb", VERBS)
def test_with_neither_and_no_uv_it_refuses_naming_uv(scratch, verb):
    tree, env, uvlog = scratch
    p = _run(tree / "bin" / verb, {**env, "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"})
    assert p.returncode == 2, p.stderr
    assert f"{verb}: uv is not on the PATH" in p.stderr


@pytest.mark.parametrize("verb", VERBS)
def test_a_tool_entry_that_is_this_wrapper_is_never_execd(scratch, tmp_path, verb):
    """No loop: a tool dir whose <verb> links back to this script falls through to uv."""
    tree, env, uvlog = scratch
    env, tools = _tools(env, tmp_path, "UV_TOOL_DIR")
    (tools / "sessionkit" / "bin").mkdir(parents=True)
    (tools / "sessionkit" / "bin" / verb).symlink_to(tree / "bin" / verb)
    p = _run(tree / "bin" / verb, env)
    assert p.returncode == 0, p.stderr
    assert uvlog.read_text().splitlines()[:2] == ["uv", "run"]
