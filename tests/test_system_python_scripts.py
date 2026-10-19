"""The scripts that run on PATH's python3 still RUN on the system python below the 3.10 floor.

Nine shipped scripts start on whatever python3 PATH holds: by their `#!/usr/bin/env python3`,
or as `python3 FILE`. On a clean Mac that is Apple's /usr/bin/python3, 3.9.6. They are
stdlib-only and work there today, by care rather than by any check (EARS-402). This guard
runs each one on that python, by its cheapest real invocation. It fails on a Traceback (a
TypeError, SyntaxError or ImportError) and on an unexpected exit code.

Why it runs them and does not just parse them: the bug this guards against, an `int | None`
annotation, PARSES under the 3.9 grammar and fails only when it is evaluated. A
`def f(x: int | None)` raises when the def runs, so a run that reaches the def catches it.

Plain paths only. HOME is a temp dir holding an empty heard log for today. PATH is a stub
dir (python3 -> the system python, and a `tail` that exits 0 for heard-tail's follow), then
/usr/bin:/bin:/usr/sbin:/sbin. Nothing under the real home is read or written. With no
system python below the floor (none at all, macOS's install stub, or one at 3.10 or later),
every case SKIPS and says why.

THE LIMIT, stated rather than hidden: a run only evaluates what it reaches. Every module-level
def, class and signature is reached, except in ears-mid-turn: `--help` exits at its
parse_args (line 37), before its two unannotated defs `path()` and `ts()`. A real run of it
needs a writer appending rows, which is not cheap. A 3.10-only call deep inside a function
body that these runs never call is not caught either. The other scripts that need numpy, or
that start on the venv's python, are out of scope: they never start on PATH's python3.
"""

import os
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from tests.system_python import below_floor

ROOT = Path(__file__).resolve().parents[1]

# (name, argv relative to the checkout, expected exit code, a fragment of the output).
# "./x" runs by the script's own shebang; "python3 x" is a 100644 file run the way a person
# runs it. The build-architecture-page case runs a COPY, so it writes only under tmp.
CASES = [
    ("build-architecture-page", ["COPY/bin/build-architecture-page"], 0, "architecture.html"),
    ("ears-turn", ["./bin/ears-turn", "1", "--wait", "0"], 124, "(no turn in 0 s"),
    ("heard-first", ["./bin/heard-first", "--rule", "turn", "--timeout", "0.2"], 124, '"timeout": true'),
    ("render-plist", ["./bin/render-plist", "launchd/dev.sessionkit.voice.ears-keeper.plist.in"], 0, "</plist>"),
    ("heard-tail.py", ["./keeper/heard-tail.py"], 0, ""),
    ("heard-tail.sh", ["./keeper/heard-tail.sh"], 0, ""),
    ("partial-wake.py", ["python3", "keeper/partial-wake.py", "--help"], 0, "--match"),
    ("ears-mid-turn", ["./skills/ears/scripts/ears-mid-turn", "--help"], 0, "--source"),
    ("ears-mouth-tail", ["./skills/ears/scripts/ears-mouth-tail", "--help"], 0, "--heartbeats"),
]


@pytest.mark.parametrize("name,argv,rc,want", CASES, ids=[c[0] for c in CASES])
def test_the_script_runs_on_the_system_python(tmp_path, name, argv, rc, want):
    system, why = below_floor()
    if system is None:
        pytest.skip(f"EARS-402 system-python guard not run: {why}")
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "python3").symlink_to(system)
    (stub / "tail").write_text("#!/bin/sh\nexit 0\n")
    (stub / "tail").chmod(0o755)
    home = tmp_path / "home"
    conv = home / ".sessionvoice" / "logs" / "conversations"
    conv.mkdir(parents=True)
    (conv / f"heard_{date.today():%Y-%m-%d}.jsonl").write_text("")
    if argv[0].startswith("COPY/"):
        copy = tmp_path / "copy"
        (copy / "bin").mkdir(parents=True)
        (copy / "docs").mkdir()
        shutil.copy2(ROOT / "bin" / "build-architecture-page", copy / "bin")
        shutil.copy2(ROOT / "docs" / "architecture.md", copy / "docs")
        argv = [str(copy / argv[0][len("COPY/"):]), *argv[1:]]
    env = {"PATH": f"{stub}:/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(home), "LANG": "C.UTF-8"}
    p = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    out = p.stdout + p.stderr
    assert "Traceback" not in out, f"{name} on {system} (python3 {below_floor()[1]}):\n{out[-3000:]}"
    assert p.returncode == rc, (name, p.returncode, out[-3000:])
    assert want in out, (name, want, out[-2000:])


def test_every_case_is_one_of_the_path_python3_scripts():
    """The nine are the shipped scripts that start on PATH's python3 (EARS-402 repro-001):
    each one exists, and each executable one names python3 in its shebang."""
    for name, argv, _, _ in CASES:
        rel = argv[1] if argv[0] == "python3" else argv[0].removeprefix("./").replace("COPY/", "")
        path = ROOT / rel
        assert path.is_file(), (name, path)
        first = path.read_text().splitlines()[0]
        if argv[0] != "python3":
            assert os.access(path, os.X_OK), (name, path)
            assert first.startswith("#!") and ("python3" in first or path.suffix == ".sh"), (name, first)
