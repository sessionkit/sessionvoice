"""EARS-366 (SK-97): the tree this repo exports holds no work-hour stamp.

A work-hour stamp is a time of day written beside a weekday and a date, as in
"HH:MM Day YYYY-MM-DD", where the day is Mon to Fri and the start time is from 09:00
up to (not including) 17:00. The pattern is SK-42's STAMP_RE, ported verbatim from the
SK-97 measure. A range ("HH:MM-HH:MM Day ...") counts by its start; seconds are allowed.

The tree is `git archive HEAD`, the tree a release exports, so export-ignored paths do
not count and uncommitted edits are not read. Binary files are skipped as the measure
skips them (a NUL in the first 8 KiB). A hit fails the test, naming each path, its count
and its line numbers, never the matched text. The fix is to drop the time of day and
keep the weekday and date.

Offline and fast: one local `git archive`, no network. Outside a git checkout (an
installed wheel or an unpacked sdist) there is no HEAD to read, and the scan skips.

This file must never hold a stamp itself: the pattern has no literal one, and the
matcher's samples are built at run time from their parts.
"""
import io
import os
import re
import shutil
import subprocess
import tarfile
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# SK-42's STAMP_RE, verbatim. Written with character classes so that no stamp appears in
# this file's own text.
STAMP_RE = re.compile(r"[0-9]{1,2}:[0-9]{2}(:[0-9]{2})?(-[0-9]{1,2}:[0-9]{2})?"
                      r" (Mon|Tue|Wed|Thu|Fri) 20[0-9]{2}-[0-9]{2}-[0-9]{2}")
WORK_START = 9 * 60    # 09:00, inclusive
WORK_END = 17 * 60     # 17:00, exclusive


def _start_minute(match):
    """The start time of a stamp in minutes after midnight (a range counts by its start)."""
    hh, mm = match.group(0).split(" ", 1)[0].split("-", 1)[0].split(":")[:2]
    return int(hh) * 60 + int(mm)


def work_hour_stamps(text):
    """Each work-hour stamp in `text` as (line number, matched text)."""
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        for m in STAMP_RE.finditer(line):
            if WORK_START <= _start_minute(m) < WORK_END:
                hits.append((n, m.group(0)))
    return hits


def _git_env():
    # A hook or a `git -C` caller can export GIT_DIR and friends; read ROOT's repo only.
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _archive_head():
    """HEAD's `git archive` as bytes, or None when ROOT is not the top of a git checkout."""
    if shutil.which("git") is None:
        return None
    top = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, env=_git_env())
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != ROOT:
        return None
    arc = subprocess.run(["git", "-C", str(ROOT), "archive", "HEAD"],
                         capture_output=True, env=_git_env())
    if arc.returncode != 0:   # e.g. a fresh repo with no commit yet
        return None
    return arc.stdout


def test_the_exported_tree_has_no_work_hour_stamp():
    data = _archive_head()
    if data is None:
        pytest.skip("not a git checkout of this repo (a wheel or sdist install): no HEAD to read")
    found = {}
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            body = tar.extractfile(member).read()
            if b"\0" in body[:8192]:
                continue
            hits = work_hour_stamps(body.decode("utf-8", "replace"))
            if hits:
                found[member.name] = [n for n, _ in hits]
    report = "\n".join(f"  {len(lines):3} {path}  (line {', '.join(map(str, lines))})"
                       for path, lines in sorted(found.items()))
    total = sum(len(v) for v in found.values())
    assert not found, (
        f"{total} work-hour stamp(s) in {len(found)} file(s) of `git archive HEAD` "
        f"(EARS-366): drop the time of day, keep the weekday and date.\n{report}")


# The matcher itself. Each sample is built from its parts, so this file holds no stamp.
def _stamp(clock, day="Thu", date="2030-10-03"):
    return clock + " " + day + " " + date


def _clock(h, m, s=None):
    return f"{h:02d}:{m:02d}" + ("" if s is None else f":{s:02d}")


@pytest.mark.parametrize("clock,day,hit", [
    (_clock(9, 0), "Mon", True),                          # the first minute counts
    (_clock(16, 59), "Fri", True),                        # the last minute counts
    (_clock(8, 59), "Tue", False),                        # before hours
    (_clock(17, 0), "Wed", False),                        # 17:00 is out
    (_clock(12, 0), "Sat", False),                        # the weekend never counts
    (_clock(12, 0), "Sun", False),
    (_clock(10, 30, 15), "Thu", True),                    # seconds
    (_clock(16, 13) + "-" + _clock(17, 30), "Thu", True),  # a range counts by its start
    (_clock(8, 30) + "-" + _clock(9, 30), "Thu", False),
    ("9" + ":" + "05", "Thu", True),                      # a one-digit hour
])
def test_the_matcher_follows_sk42(clock, day, hit):
    line = "noted " + _stamp(clock, day) + ": a ruling"
    assert bool(work_hour_stamps(line)) is hit


def test_a_stripped_stamp_is_not_a_hit():
    assert work_hour_stamps("noted " + _stamp("").strip() + ": a ruling") == []
    assert work_hour_stamps("an ISO time 2030-10-03T" + _clock(10, 30) + ":00+10:00") == []


def test_hits_carry_their_line_numbers():
    text = "first\n" + _stamp(_clock(11, 0)) + " and " + _stamp(_clock(14, 0), "Fri") + "\n"
    assert [n for n, _ in work_hour_stamps(text)] == [2, 2]


def test_this_file_holds_no_stamp():
    assert work_hour_stamps(Path(__file__).read_text(encoding="utf-8")) == []
