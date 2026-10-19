"""A pytest plugin that puts a gap between collection and the run (EARS-297).

A full ``make test`` under load takes about 600 s, so a test file collected at the
start may run many minutes after its module was imported. A clock read at import
(``NOW = time.time()``) is then stale by that long. This plugin makes that gap
happen on demand without waiting for it: while pytest collects, ``time.time``
reads ``MOUTH_AGED_CLOCK_S`` seconds (default 520) behind the real clock, so
anything a module reads at import is that old by the time its tests run.

    PYTHONPATH=tests/mouth/fixtures pytest -p aged_clock tests/mouth/test_mouth_timeline_cli.py

The run itself reads the real clock, in this process and in every child process
alike. Only ``time.time`` moves; the monotonic and perf counters are left alone.
The terminal summary says how far collection was aged, so a run can show the
plugin was in force.
"""

from __future__ import annotations

import os
import time

import pytest

_REAL = time.time
AGE_S = float(os.environ.get("MOUTH_AGED_CLOCK_S", "520"))


def _aged() -> float:
    return _REAL() - AGE_S


@pytest.hookimpl(hookwrapper=True)
def pytest_collection(session):
    time.time = _aged
    try:
        yield
    finally:
        time.time = _REAL


def pytest_terminal_summary(terminalreporter):
    terminalreporter.write_line(f"aged_clock: collection read time.time {AGE_S:g} s behind")
