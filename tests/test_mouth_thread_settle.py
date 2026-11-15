"""EARS-550: a thread a mouth test leaves running never reaches sessionmail in a later test.

A player outlived its test's ``join(5)``: ``test_mouth.py``'s stop test barged a line, and
the line resumed about 7 s on (its fixture now turns resume off). After the test ended
the player dropped a line and asked sessionmail for the sender's box, ``message path
ivy``, while a later test was still in its setup: HOME was the session's again and the
test's fake ``message`` was off PATH. The mail fence refused it, and the refusal named
the innocent test and failed the run. ``tests/mouth/conftest.py``'s ``threads_settled``
now waits, at the end of every mouth test's teardown and before its env is put back, for
each thread the test started.

Each case runs a child pytest from the repo root, with HOME in tmp: the mouth test in
``tests/mouth/fixtures/child_left_running.py``, then the root test in
``tests/fixtures/child_the_next.py``.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from tests import child_pytest

ROOT = Path(__file__).resolve().parent.parent
LEFT = "tests/mouth/fixtures/child_left_running.py"
NEXT = "tests/fixtures/child_the_next.py::test_the_next_test"


def _child(tmp_path, left: str) -> subprocess.CompletedProcess:
    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "CHILD_LATE_DONE": str(tmp_path / "late-done")}
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                           child_pytest.basetemp(tmp_path), f"{LEFT}::{left}", NEXT],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)


def test_a_player_left_running_finishes_inside_its_own_test(tmp_path):
    out = _child(tmp_path, "test_a_player_left_running")
    tail = out.stdout[-4000:] + out.stderr[-2000:]
    assert out.returncode == 0, tail
    assert re.search(r"^2 passed", out.stdout.strip().splitlines()[-1]), tail
    assert "REFUSED" not in out.stdout and "reached for the live mail index" not in out.stdout, tail
    assert (tmp_path / "late-done").exists(), "the late drop never ran: this case shows nothing"


def test_the_control_an_unsettled_thread_is_blamed_on_the_next_test(tmp_path):
    """The hazard, on a thread the fence cannot see: the late drop's ``message path ivy``
    meets the mail fence's refuser and fails the NEXT test, as the seeded run did."""
    out = _child(tmp_path, "test_a_raw_thread_left_running")
    tail = out.stdout[-4000:] + out.stderr[-2000:]
    assert out.returncode == 1, tail
    assert re.search(r"reached for the live mail index \(EARS-506: fake it\): \S*child_the_next\.py::"
                     r"test_the_next_test \(call\): child pid \d+ message path ivy", out.stdout), tail
