"""Put the fake sessionmail ``message`` (``tests/fixtures/fake_sessionmail.py``) first on
a test's PATH, so every box sessionvoice asks for is answered from the fenced HOME's
mail root and no test spells the root (SM 0.3.3, the mail root move).

The mail fence (``tests/mail_fence.py``) refuses the real ``message``; a fake in tmp,
earlier on PATH, wins. :func:`install` writes a ``message`` program into DIRECTORY (a
shell line running the fixture with this interpreter, ``-I``), puts DIRECTORY first on
PATH, points ``ears.boxes`` at a fresh process (``forget``), and returns the path the
fake logs its calls to (``$FAKE_MESSAGE_LOG``), one JSON line per call.
"""
from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "fake_sessionmail.py"


def install(directory: Path, monkeypatch, root: Path | None = None) -> Path:
    """The fake `message` first on PATH, from DIRECTORY; ROOT is its mail root (else the
    HOME's ``.mail``). Returns its call log's path."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    log = directory / "message.calls"
    log.write_text("")                       # this test's calls alone
    prog = directory / "message"
    prog.write_text("#!/bin/sh\nexec " + " ".join(shlex.quote(x) for x in (sys.executable, "-I", str(FIXTURE)))
                    + ' "$@"\n')
    prog.chmod(0o755)
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_MESSAGE_LOG", str(log))
    if root is not None:
        monkeypatch.setenv("FAKE_MESSAGE_ROOT", str(root))
    else:
        monkeypatch.delenv("FAKE_MESSAGE_ROOT", raising=False)
    from ears import boxes
    boxes.forget()
    return log


def calls(log: Path) -> list:
    """Every argv the fake was run with, in order."""
    if not log.exists():
        return []
    return [json.loads(l)["argv"] for l in log.read_text().splitlines() if l.strip()]
