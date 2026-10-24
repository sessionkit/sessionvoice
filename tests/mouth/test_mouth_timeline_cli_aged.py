"""The timeline CLI tests survive a slow suite (EARS-297).

test_mouth_timeline_cli.py once took its clock at import, which is collection.
Its rows sat about 100 s before that clock, and timeline ends a call after
LINGER_S = 600 s with no record, so when a full suite under load reached the
file more than ~500 s after collecting it, four tests found the current call
already over. fixtures/aged_clock.py puts that gap in on demand: collection reads
the clock 520 s behind, and the run reads it true. On the base the fix came from
(c02871a), 520 s fails those four and 450 s passes; these tests hold the fix.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

from tests import child_pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
CLI_TESTS = Path(__file__).resolve().parent / "test_mouth_timeline_cli.py"
AGE_S = 520

#: The four that failed on a slow suite (EARS-297's README).
THE_FOUR = (
    "test_every_flag_combination_leaves_the_trees_byte_and_mtime_identical",
    "test_the_current_call_in_order",
    "test_last_counts_past_rows_and_never_caps_the_future",
    "test_the_human_form",
)


def run_aged(args, cwd, tmp_path, age=AGE_S) -> subprocess.CompletedProcess:
    """pytest ARGS in a child, with the aged_clock plugin rewinding collection, in its own
    basetemp under ``tmp_path`` (tests/child_pytest.py, EARS-436)."""
    env = {**os.environ, "MOUTH_AGED_CLOCK_S": str(age),
           "PYTHONPATH": os.pathsep.join(
               [str(FIXTURES), *filter(None, [os.environ.get("PYTHONPATH")])])}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "aged_clock", "-p", "no:cacheprovider",
         "-q", "-rA", child_pytest.basetemp(tmp_path), *args],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=600)


def test_the_plugin_rewinds_collection_and_leaves_the_run_true(tmp_path):
    """The reproducer bites: a clock read at import is AGE_S stale at run time,
    and the run itself (this process and a child) reads the real clock."""
    probe = tmp_path / "test_probe.py"
    probe.write_text(
        "import subprocess, sys, time\n"
        "AT_IMPORT = time.time()\n"
        "REAL_AT_IMPORT = time.clock_gettime(time.CLOCK_REALTIME)\n"
        "def test_import_was_aged():\n"
        f"    assert {AGE_S} - 5 < REAL_AT_IMPORT - AT_IMPORT < {AGE_S} + 5\n"
        f"    assert time.time() - AT_IMPORT >= {AGE_S}\n"
        "def test_the_run_reads_true():\n"
        "    assert abs(time.time() - time.clock_gettime(time.CLOCK_REALTIME)) < 5\n"
        "    child = float(subprocess.check_output(\n"
        "        [sys.executable, '-c', 'import time; print(time.time())']))\n"
        "    assert abs(child - time.time()) < 30\n")
    p = run_aged([str(probe)], cwd=tmp_path, tmp_path=tmp_path)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "2 passed" in p.stdout, p.stdout
    assert f"aged_clock: collection read time.time {AGE_S} s behind" in p.stdout


def test_every_timeline_cli_test_passes_520_s_after_collection(tmp_path):
    p = run_aged([str(CLI_TESTS)], cwd=ROOT, tmp_path=tmp_path)
    out = p.stdout + p.stderr
    assert f"aged_clock: collection read time.time {AGE_S} s behind" in p.stdout, out
    assert p.returncode == 0, out
    assert not re.search(r"\b(failed|error)", p.stdout.splitlines()[-1]), out
    for name in THE_FOUR:
        assert f"PASSED tests/mouth/test_mouth_timeline_cli.py::{name}" in p.stdout, (name, out)
