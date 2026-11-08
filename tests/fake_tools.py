"""Fake sessiontools, for the tests that ask tools where seat homes are (EARS-492 design.md D5).

voice asks tools, never derives tools' folders: (a) ``sessiontools.settings``, imported; (b)
``session path agent`` / ``session path sessions``; (c) tools' OLD folders only when neither
answers. A real ``session`` on the runner's PATH may answer (b) for real, so every test that
reaches the ladder puts one of these first:

- :func:`session_bin` writes a fake ``session`` into a bin folder: ``path agent`` and ``path
  sessions`` print the roots it is given, or, given none, answer as a tools with no ``path``
  verb does (``unknown verb 'path'``, exit 2). Every other verb exits 2 too. It records each
  argv in ``<bin>/session.calls``.
- :func:`sessiontools_pkg` writes a fake ``sessiontools`` package, for ``sys.path`` or
  ``PYTHONPATH``: ``settings.agent_home``, ``agent_config`` and ``session_dir`` under a root.
- :func:`no_tools` (in process) hides any importable sessiontools and puts a fake ``session``
  with no ``path`` verb first on PATH: the ladder reaches rung (c).

Nothing here writes outside the folders it is given.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def session_bin(bindir: Path, agent: Path | None = None, sessions: Path | None = None) -> Path:
    """A fake ``session`` in BINDIR: ``path agent`` prints AGENT, ``path sessions`` prints
    SESSIONS; a root not given, and any other verb, is an older tools' "unknown verb"."""
    bindir.mkdir(parents=True, exist_ok=True)
    s = bindir / "session"
    calls = bindir / "session.calls"
    lines = ["#!/bin/sh", f'echo "$*" >> "{calls}"']
    if agent is not None:
        lines.append(f'[ "$1 $2" = "path agent" ] && {{ echo "{agent}"; exit 0; }}')
    if sessions is not None:
        lines.append(f'[ "$1 $2" = "path sessions" ] && {{ echo "{sessions}"; exit 0; }}')
    lines += ['echo "session: unknown verb \'$1\'" >&2', "exit 2", ""]
    s.write_text("\n".join(lines))
    s.chmod(0o755)
    return s


def calls(bindir: Path) -> list[str]:
    """The argv lines the fake ``session`` in BINDIR recorded."""
    f = bindir / "session.calls"
    return f.read_text().splitlines() if f.exists() else []


SETTINGS = '''"""A FAKE sessiontools.settings (tests/fake_tools.py): every folder under ROOT."""
from pathlib import Path

ROOT = Path({root!r})


def session_root(env=None):
    return ROOT


def agent_home(agent, env=None):
    return ROOT / "agent" / "home" / agent


def agent_config(agent, env=None):
    config = ROOT / "agent" / "config"
    if (config / agent).is_dir():
        return config / agent / f"{{agent}}.md"
    return config / f"{{agent}}.md"


def session_dir(sid, env=None):
    return ROOT / "sessions" / sid
'''


def sessiontools_pkg(where: Path, root: Path) -> Path:
    """A fake ``sessiontools`` package under WHERE (put WHERE on ``sys.path`` or
    ``PYTHONPATH``) whose folders are all under ROOT, as the real one's are under its
    session root. Returns WHERE."""
    pkg = where / "sessiontools"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text('"""A FAKE sessiontools (tests/fake_tools.py)."""\n')
    (pkg / "settings.py").write_text(SETTINGS.format(root=str(root)))
    return where


def no_tools(tmp_path: Path, monkeypatch) -> Path:
    """In this process: no importable sessiontools, and a fake ``session`` with no ``path``
    verb first on PATH. Returns the fake's bin folder."""
    for name in [m for m in sys.modules if m == "sessiontools" or m.startswith("sessiontools.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "sessiontools", None)
    bindir = tmp_path / "no-tools-bin"
    session_bin(bindir)
    monkeypatch.setenv("PATH", os.pathsep.join([str(bindir), os.environ.get("PATH", "")]))
    return bindir


def with_sessiontools(tmp_path: Path, monkeypatch, root: Path) -> None:
    """In this process: a fake sessiontools importable, its folders under ROOT. The fake
    leaves ``sys.modules`` at teardown (monkeypatch restores both names as they were)."""
    import importlib
    where = sessiontools_pkg(tmp_path / "fake-sessiontools", root)
    for name in ("sessiontools", "sessiontools.settings"):
        monkeypatch.setitem(sys.modules, name, None)     # records the name's old state
        del sys.modules[name]
    monkeypatch.syspath_prepend(str(where))
    importlib.import_module("sessiontools.settings")
