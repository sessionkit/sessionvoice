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
  ``PYTHONPATH``: ``settings.agent_home``, ``agent_config`` and ``session_dir`` under a root,
  and ``paths.agent_report`` (the launcher's view of an agent, from an agents folder), or an
  older tools' ``paths`` with no ``agent_report``, or one whose ``agent_report`` raises.
- :func:`with_kit_tools` (in process) is the kit's tools on an upgraded host: a fake
  sessiontools whose launcher still reads the pre-4.2 agents folder under HOME.
- :func:`no_tools` (in process) hides any importable sessiontools and puts a fake ``session``
  with no ``path`` verb first on PATH: the ladder reaches rung (c).
- :func:`hidden_sessiontools` is the same hiding for a child process, by ``PYTHONPATH``: a
  ``sessiontools`` whose import fails, so the runner's real one cannot answer rung (a).

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


PATHS = '''"""A FAKE sessiontools.paths (tests/fake_tools.py): agent_report shaped as the real one
is, its launcher reading the agents folder AGENTS (a directory agent, a flat agent, or a
refusal, as sessiontools.start.agents.resolve_agent answers)."""
from pathlib import Path

ROOT = Path({root!r})
AGENTS = Path({agents!r})
REPORT = {report!r}


def _config(name):
    folder, flat = AGENTS / name, AGENTS / f"{{name}}.md"
    config = {{"path": "", "source": "fake", "file": "", "builtin": False}}
    if folder.is_dir():
        if (folder / f"{{name}}.md").is_file():
            config.update(path=str(folder), file=str(folder / f"{{name}}.md"))
        else:
            config["refused"] = f"agent directory {{folder}} exists but {{name}}.md is missing"
    elif flat.is_file():
        config["file"] = str(flat)
    else:
        config["refused"] = f"agent {{name!r}} not found"
    return config


if REPORT == "yes":
    def agent_report(name, env=None):
        return {{"version": "0.0.0+fake",
                "root": {{"path": str(ROOT / "agent"), "source": "fake"}},
                "items": {{"home": {{"path": str(ROOT / "agent" / "home" / name), "source": "fake"}},
                          "agents": {{"path": str(AGENTS), "source": "fake", "rung": "fake"}},
                          "config": _config(name)}}}}
elif REPORT == "raise":
    def agent_report(name, env=None):
        raise RuntimeError("a fake tools refusal")
'''

#: What the fake ``paths`` module offers: ``yes`` (``agent_report``), ``none`` (an older tools:
#: a ``paths`` module with no ``agent_report``), ``raise`` (``agent_report`` raises).
REPORTS = ("yes", "none", "raise")


def sessiontools_pkg(where: Path, root: Path, agents: Path | None = None,
                     report: str = "yes") -> Path:
    """A fake ``sessiontools`` package under WHERE (put WHERE on ``sys.path`` or
    ``PYTHONPATH``) whose folders are all under ROOT, as the real one's are under its
    session root. Its launcher reads AGENTS (default ``ROOT/agent/config``, tools' own
    folder); REPORT is one of :data:`REPORTS`. Returns WHERE."""
    assert report in REPORTS, report
    agents = root / "agent" / "config" if agents is None else agents
    pkg = where / "sessiontools"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text('"""A FAKE sessiontools (tests/fake_tools.py)."""\n')
    (pkg / "settings.py").write_text(SETTINGS.format(root=str(root)))
    (pkg / "paths.py").write_text(PATHS.format(root=str(root), agents=str(agents), report=report))
    return where


HIDDEN = '''"""A sessiontools that HIDES any other (tests/fake_tools.py): importing it fails, as an
uninstalled one does, so a child process reaches rung (b) whatever the runner has installed."""
raise ImportError("sessiontools hidden by tests/fake_tools.py")
'''


def hidden_sessiontools(where: Path) -> Path:
    """For a CHILD process, what :func:`no_tools` is in this one: a ``sessiontools`` package
    under WHERE whose import raises ImportError. Put WHERE first on the child's ``PYTHONPATH``
    and a real sessiontools in its site-packages is never imported (a child runs with
    ``sys.executable``, so it sees the runner's installed packages). Returns WHERE."""
    pkg = where / "sessiontools"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(HIDDEN)
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


def with_sessiontools(tmp_path: Path, monkeypatch, root: Path, agents: Path | None = None,
                      report: str = "yes") -> None:
    """In this process: a fake sessiontools importable, its folders under ROOT, its launcher
    reading AGENTS, REPORT as :func:`sessiontools_pkg` takes it. The fake leaves
    ``sys.modules`` at teardown (monkeypatch restores each name as it was), so a real
    sessiontools on the runner's path is never the one asked."""
    import importlib
    where = sessiontools_pkg(tmp_path / "fake-sessiontools", root, agents, report)
    for name in ("sessiontools", "sessiontools.settings", "sessiontools.paths"):
        monkeypatch.setitem(sys.modules, name, None)     # records the name's old state
        del sys.modules[name]
    monkeypatch.syspath_prepend(str(where))
    importlib.import_module("sessiontools.settings")
    importlib.import_module("sessiontools.paths")


def with_kit_tools(tmp_path: Path, monkeypatch, home: Path) -> Path:
    """In this process: the kit's tools on a host upgraded from before 4.2, whatever the
    runner has installed. A fake sessiontools with its folders under ``HOME/.sessionkit/
    tools/state`` and its launcher still reading the pre-4.2 agents folder,
    ``HOME/.session/agents``. Returns that agents folder: a face planted at
    ``<it>/<name>/avatar.jpg`` is served through ``paths.agent_report``."""
    agents = home / ".session" / "agents"
    with_sessiontools(tmp_path, monkeypatch, home / ".sessionkit" / "tools" / "state", agents)
    return agents
