"""EARS-389 (SK-108): the tree this repo exports holds no time-of-day stamp, at any hour.

A time-of-day stamp is a clock time written beside a weekday and a date, as in
"HH:MM Day YYYY-MM-DD". Any hour counts and any day counts, Saturday and Sunday too.
Seconds ("HH:MM:SS Day ...") and ranges ("HH:MM-HH:MM Day ...") count as well. The
sibling test_no_work_hour_stamps.py (EARS-366, SK-97) counts only 09:00-17:00 on
weekdays, so it passed a tree that still shipped 23 evening, night and weekend stamps.

The tree is `git archive HEAD`, the tree a release exports, so export-ignored paths do
not count and uncommitted edits are not read. Binary files are skipped (a NUL in the
first 8 KiB). A hit fails the test, naming each path, its count and its line numbers,
never the matched text. The fix is to drop the time of day and keep the weekday and date.

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
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# A clock time, then a weekday, then a date. Written with character classes so that no
# stamp appears in this file's own text. The end of a range ("-HH:MM") is itself a clock
# time beside the day, so a range is caught without a rule of its own.
STAMP_RE = re.compile(r"[0-9]{1,2}:[0-9]{2}(:[0-9]{2})?"
                      r" (Mon|Tue|Wed|Thu|Fri|Sat|Sun) [0-9]{4}-[0-9]{2}-[0-9]{2}")


def time_of_day_stamps(text):
    """Each time-of-day stamp in `text` as (line number, matched text)."""
    return [(n, m.group(0))
            for n, line in enumerate(text.splitlines(), 1)
            for m in STAMP_RE.finditer(line)]


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


def test_the_exported_tree_has_no_time_of_day_stamp():
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
            hits = time_of_day_stamps(body.decode("utf-8", "replace"))
            if hits:
                found[member.name] = [n for n, _ in hits]
    report = "\n".join(f"  {len(lines):3} {path}  (line {', '.join(map(str, lines))})"
                       for path, lines in sorted(found.items()))
    total = sum(len(v) for v in found.values())
    assert not found, (
        f"{total} time-of-day stamp(s) in {len(found)} file(s) of `git archive HEAD` "
        f"(EARS-389): drop the time of day, keep the weekday and date.\n{report}")


# The matcher itself. Each sample is built from its parts, so this file holds no stamp.
def _stamp(clock, day="Thu", date="2030-10-03"):
    return clock + " " + day + " " + date


def _clock(h, m, s=None):
    return f"{h:02d}:{m:02d}" + ("" if s is None else f":{s:02d}")


@pytest.mark.parametrize("clock,day", [
    (_clock(0, 0), "Mon"),                                # midnight
    (_clock(2, 45), "Sun"),                               # the small hours, a weekend
    (_clock(9, 0), "Tue"),                                # work hours count too
    (_clock(17, 0), "Wed"),                               # the hour SK-97's gate let by
    (_clock(21, 56), "Sat"),
    (_clock(23, 59), "Fri"),
    (_clock(18, 47, 23), "Sat"),                          # seconds
    (_clock(1, 50) + "-" + _clock(2, 41), "Sun"),         # a range
    ("9" + ":" + "05", "Thu"),                            # a one-digit hour
])
def test_the_matcher_catches_any_hour_and_any_day(clock, day):
    assert len(time_of_day_stamps("noted " + _stamp(clock, day) + ": a ruling")) == 1


def test_a_stripped_stamp_is_not_a_hit():
    assert time_of_day_stamps("noted " + _stamp("").strip() + ": a ruling") == []
    assert time_of_day_stamps("At " + _stamp("").strip() + " the AirPods left") == []
    assert time_of_day_stamps("an ISO time 2030-10-03T" + _clock(21, 30) + ":00+10:00") == []
    assert time_of_day_stamps("a clock alone: " + _clock(21, 30) + ", no day") == []


def test_hits_carry_their_line_numbers():
    text = "first\n" + _stamp(_clock(1, 0), "Sun") + " and " + _stamp(_clock(22, 0), "Sat") + "\n"
    assert [n for n, _ in time_of_day_stamps(text)] == [2, 2]


def test_this_file_holds_no_stamp():
    assert time_of_day_stamps(Path(__file__).read_text(encoding="utf-8")) == []
