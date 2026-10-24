"""tests/child_pytest.py: a child pytest gets its own basetemp (EARS-436).

The children here run in an ISOLATED temp root (``TMPDIR`` in tmp_path), never the
host's shared ``pytest-of-<user>``. A "finished suite" is planted there: a numbered dir
with no ``.lock`` and three newer dirs, as a busy host's root holds one whenever a suite
has just ended.
"""
from __future__ import annotations

import ast
import getpass
import os
import subprocess
import sys
from pathlib import Path

from tests import child_pytest

ROOT = Path(__file__).resolve().parents[1]
CHILD_TEST = ("import os\n"
              "def test_uses_tmp_path(tmp_path):\n"
              "    (tmp_path / 'made').write_text('ok')\n"
              "    open(os.environ['CHILD_TMP_PATH_FILE'], 'w').write(str(tmp_path))\n")


def _planted(tmp_path: Path) -> tuple[Path, Path, Path]:
    """(the isolated temp root, its pytest-of-<user>, the finished suite's dir in it)."""
    root = tmp_path / "root"
    shared = root / f"pytest-of-{getpass.getuser()}"
    finished = shared / "pytest-1"
    for i in range(20):
        (finished / f"test_{i}0").mkdir(parents=True)
        (finished / f"test_{i}0" / "f").write_text("a finished suite's file\n")
    for n in (2, 3, 4):                         # newer sessions, still running: locked
        (shared / f"pytest-{n}").mkdir()
        (shared / f"pytest-{n}" / ".lock").write_text("")
    return root, shared, finished


def _run_child(tmp_path: Path, root: Path, *extra: str) -> tuple[subprocess.CompletedProcess, Path]:
    child = tmp_path / "child"
    child.mkdir()
    (child / "test_child.py").write_text(CHILD_TEST)
    report = tmp_path / "child-tmp-path.txt"
    env = {**os.environ, "HOME": str(tmp_path / "home"), "PYTHONPATH": str(ROOT), "TMPDIR": str(root),
           "CHILD_TMP_PATH_FILE": str(report)}
    env.pop("PYTEST_DEBUG_TEMPROOT", None)      # it would outrank TMPDIR
    out = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *extra, str(child)],
                         cwd=child, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr
    return out, Path(report.read_text())


def test_a_child_without_its_own_basetemp_deletes_a_finished_suites_dir(tmp_path):
    """The mechanism, on the code as pytest ships it: a bare child pytest's sessionfinish
    deletes the planted suite's dir in the root it shares. On a busy host that dir was
    tens of thousands of files, and the child deleted them before it could exit."""
    root, shared, finished = _planted(tmp_path)
    _, child_tmp = _run_child(tmp_path, root)
    assert not finished.exists(), "pytest's cleanup did not run: this test no longer shows the hazard"
    assert child_tmp.resolve().is_relative_to(shared.resolve())


def test_a_child_with_its_own_basetemp_leaves_the_shared_root_alone(tmp_path):
    root, shared, finished = _planted(tmp_path)
    before = sorted(str(p.relative_to(shared)) for p in shared.rglob("*"))
    _, child_tmp = _run_child(tmp_path, root, child_pytest.basetemp(tmp_path))
    assert sorted(str(p.relative_to(shared)) for p in shared.rglob("*")) == before   # not one file gone, none added
    assert child_tmp.resolve().is_relative_to((tmp_path / child_pytest.DIRNAME).resolve())
    assert (child_tmp / "made").read_text() == "ok"


def test_basetemp_is_inside_the_owning_tests_tmp_path(tmp_path):
    arg = child_pytest.basetemp(tmp_path)
    assert arg == f"--basetemp={tmp_path / 'bt'}"
    assert child_pytest.basetemp(str(tmp_path)) == arg


# -- every child pytest in tests/ ----------------------------------------------------------------

#: Spawners that mean to run without it, and why. Only the control above, whose child
#: runs in an isolated root.
EXEMPT = {("tests/test_child_pytest.py", "_run_child")}


def _spawners():
    """(file, enclosing function, argv source) for every argv literal holding ``"-m", "pytest"``."""
    for path in sorted((ROOT / "tests").rglob("*.py")):
        src = path.read_text()
        tree = ast.parse(src)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if not isinstance(node, (ast.List, ast.Tuple)):
                    continue
                vals = [e.value if isinstance(e, ast.Constant) else None for e in node.elts]
                if any(a == "-m" and b == "pytest" for a, b in zip(vals, vals[1:])):
                    yield str(path.relative_to(ROOT)), fn.name, ast.get_source_segment(src, node)


def test_every_child_pytest_has_its_own_basetemp():
    found = list(_spawners())
    # not vacuous: the spawners the triage listed (EARS-436), and the two that hung
    assert {f for f, _, _ in found} >= {
        "tests/test_audio_fence.py", "tests/test_live_home_guard.py", "tests/test_conftest_ffmpeg.py",
        "tests/test_conftest_order.py", "tests/test_scanlist.py",
        "tests/mouth/test_mouth_timeline_cli_aged.py", "tests/mouth/test_mouth_device_gaps.py"}
    missing = [f"{f}::{fn}" for f, fn, seg in found
               if (f, fn) not in EXEMPT and "child_pytest.basetemp(" not in seg]
    assert missing == [], ("a child pytest with no basetemp of its own deletes other sessions' "
                           f"files before it exits (EARS-436): pass child_pytest.basetemp(tmp_path) in {missing}")
    assert EXEMPT <= {(f, fn) for f, fn, _ in found}, "an exemption names a spawner that is gone"
