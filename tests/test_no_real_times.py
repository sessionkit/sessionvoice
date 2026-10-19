"""EARS-397 (SK-108): every date and time typed into the tree this repo exports is
visibly synthetic, which here means it falls in 2030 or later.

EARS-393 kept a map of the synthetic values a real-looking time could take, keyed by
path and count. EARS-397 moved every typed date and time into 2030, which made the map
moot, so the guard is now one rule: a value that reads as a date or an epoch and falls
before 2030 is a hit. The sibling test_no_time_of_day_stamps.py (EARS-389) still catches
a clock time beside a weekday and a date, in any year.

The forms read, each decoded to its year:

- a date, YYYY-MM-DD, alone or inside an ISO time, a file name or a path;
- a year and month, YYYY-MM, with no day after it ("served in YYYY-MM");
- a date written with slashes, YYYY/MM/DD (a path or a URL included);
- a compact stamp, a date and a clock with no separators (YYYYMMDD-HHMMSS and kin);
- a 10-digit epoch second (with or without a fraction) and a 13-digit ms epoch;
- a 19-digit ns epoch, matched without a word boundary at its start so the ns id
  inside a Message-ID (``<NNN...NNN.utt@host.mouth>``) is caught too;
- the same epochs written digit-grouped, as a Python literal with underscores;
- a month by name beside a year (an RFC 2822 mail Date, "October YYYY", "Oct 1, YYYY");
- a year, month and day typed as a tuple, as ``datetime(YYYY, M, D, ...)`` or a
  ``mktime((YYYY, M, D, ...))`` takes them.

A day and month with no year ("19 Sep") has no net: it names no year to decode, and a
net for it would also read sizes, ids and versions. Such days were dropped from the
comments by hand instead.

The tree is `git archive HEAD`, the tree a release exports, so export-ignored paths do
not count and uncommitted edits are not read. Four things are carved out: uv.lock, whose
times are PyPI's public upload times; the root LICENSE, the license's own text with its
own dates; the CHANGELOG's release headings (``## [x.y.z] - YYYY-MM-DD``), the dates
each version shipped; and the JSON Schema dialect id, read only as the dialect: its
``json-schema.org/draft/YYYY-MM/`` URL and the prose "JSON Schema YYYY-MM". The same
year and month anywhere else is a hit. Every other CHANGELOG line is read. Binary files are skipped (a NUL
in the first 8 KiB). A hit fails the test, naming its path, its count and its line
numbers, never the matched text.

A test that needs a fixed moment takes it from tests/_anchor.py (ANCHOR, or a named
variant of it); a family of related values moves by one whole-week shift, so its
weekdays, its order and its gaps survive. A test that needs "today" is written relative
to the real now. Neither is a fake clock: the code keeps reading the real one.

Offline and fast: one local `git archive`, no network. Outside a git checkout (an
installed wheel or an unpacked sdist) there is no HEAD to read, and the scan skips.

This file must never hold a hit itself: the nets are written with character classes,
and every sample before 2030 is built at run time from its parts.
"""
import io
import os
import re
import shutil
import subprocess
import tarfile
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

import pytest

from tests._anchor import ANCHOR, ANCHOR_ISO, ANCHOR_SUMMER

ROOT = Path(__file__).resolve().parent.parent
FLOOR = 2030                 # the first year a typed value may name
SKIP = {"uv.lock",           # PyPI's public upload times
        "LICENSE"}           # the license's own text
RELEASE_HEADING = re.compile(r"^## \[[^\]]+\] - [0-9]{4}-[0-9]{2}-[0-9]{2}\s*$")

_YEAR = r"((?:19|20)[0-9]{2})"
DATE = re.compile(r"(?<![0-9])" + _YEAR + r"-[01][0-9]-[0-3][0-9](?![0-9])")
COMPACT = re.compile(r"(?<![0-9])" + _YEAR + r"(?:0[1-9]|1[0-2])(?:0[1-9]|[12][0-9]|3[01])"
                     r"[-T_ ]?(?:[01][0-9]|2[0-3])[0-5][0-9](?:[0-5][0-9])?(?![0-9])")
EPOCH = re.compile(r"\b(1[0-9]{9})(?:\.[0-9]+)?\b")
EPOCH_MS = re.compile(r"\b(1[0-9]{12})\b")
EPOCH_NS = re.compile(r"(?<![0-9])(1[0-9]{18})(?![0-9])")
GROUPED = re.compile(r"(?<![0-9_])([0-9]{1,3}(?:_[0-9]{3})+)(?:\.[0-9]+)?(?![0-9_])")
_MONTH = (r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?"
          r"|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)")
MONTH_YEAR = re.compile(r"\b" + _MONTH + r"\.?(?: [0-3]?[0-9](?:st|nd|rd|th)?,?)? "
                        + _YEAR + r"\b")
YEAR_MONTH = re.compile(r"(?<![0-9])" + _YEAR + r"-(?:0[1-9]|1[0-2])(?![0-9A-Za-z]|-[0-9])")
SLASH_DATE = re.compile(r"(?<![0-9])" + _YEAR
                        + r"/(?:0[1-9]|1[0-2])/(?:0[1-9]|[12][0-9]|3[01])(?![0-9])")
# The JSON Schema dialect this repo's schemas declare, as its URL and as its prose name.
# Only these two forms are let by; the year is split so this file holds no hit.
_DIALECT = "20" + "20-12"
DIALECT = re.compile(re.escape("json-schema.org/draft/" + _DIALECT + "/")
                     + "|" + re.escape("JSON Schema " + _DIALECT))
TUPLE = re.compile(r"\(\s*" + _YEAR
                   + r"\s*,\s*(?:1[0-2]|0?[1-9])\s*,\s*(?:3[01]|[12][0-9]|0?[1-9])\s*[,)]")


def _epoch_year(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).year


def _grouped_year(literal):
    """The year of a digit-grouped epoch (s, ms or ns), or None when it is not one."""
    digits = literal.replace("_", "")
    if digits[0] != "1" or len(digits) not in (10, 13, 19):
        return None
    return _epoch_year(int(digits[:10]))


# name -> (net, its matched group -> the year it names, or None)
NETS = {
    "date": (DATE, int),
    "year and month": (YEAR_MONTH, int),
    "slashed date": (SLASH_DATE, int),
    "compact stamp": (COMPACT, int),
    "epoch second": (EPOCH, lambda g: _epoch_year(int(g))),
    "ms epoch": (EPOCH_MS, lambda g: _epoch_year(int(g[:10]))),
    "ns epoch": (EPOCH_NS, lambda g: _epoch_year(int(g[:10]))),
    "digit-grouped epoch": (GROUPED, _grouped_year),
    "month and year": (MONTH_YEAR, int),
    "date tuple": (TUPLE, int),
}


def real_time_hits(text, path=""):
    """Each value in `text` that names a year before FLOOR, as (line number, matched
    text). In a CHANGELOG, a release heading is not read; anywhere, the JSON Schema
    dialect id is not read as a year and month."""
    changelog = Path(path).name == "CHANGELOG.md"
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        if changelog and RELEASE_HEADING.match(line):
            continue
        for rx, year_of in NETS.values():
            subject = DIALECT.sub(" ", line) if rx is YEAR_MONTH else line
            for m in rx.finditer(subject):
                year = year_of(m.group(1))
                if year is not None and year < FLOOR:
                    hits.append((n, m.group(0)))
    return hits


def _git_env():
    # A hook or a `git -C` caller can export GIT_DIR and friends; read ROOT's repo only.
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _archive_head(root=ROOT):
    """HEAD's `git archive` as bytes, or None when `root` is not the top of a git checkout."""
    if shutil.which("git") is None:
        return None
    top = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, env=_git_env())
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != Path(root).resolve():
        return None
    arc = subprocess.run(["git", "-C", str(root), "archive", "HEAD"],
                         capture_output=True, env=_git_env())
    if arc.returncode != 0:   # e.g. a fresh repo with no commit yet
        return None
    return arc.stdout


def exported_hits(data):
    """path -> [(line, text)] for every file of the archive `data` that holds a hit."""
    found = {}
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for member in tar.getmembers():
            if not member.isfile() or member.name in SKIP:
                continue
            body = tar.extractfile(member).read()
            if b"\0" in body[:8192]:
                continue
            hits = real_time_hits(body.decode("utf-8", "replace"), member.name)
            if hits:
                found[member.name] = hits
    return found


def assert_no_real_time(found):
    """Fail, naming each path, its count and its lines (never the text), if `found` holds a hit."""
    bad = {path: [n for n, _ in hits] for path, hits in found.items()}   # no text past here
    report = "\n".join(f"  {len(lines):3} {path}  (line {', '.join(map(str, lines))})"
                       for path, lines in sorted(bad.items()))
    total = sum(len(v) for v in bad.values())
    assert not bad, (
        f"{total} typed date(s) or epoch(s) before {FLOOR} in {len(found)} file(s) of "
        f"`git archive HEAD` (EARS-397): take a fixed moment from tests/_anchor.py, or move "
        f"a family of related values into {FLOOR} by one whole-week shift.\n{report}")


def test_the_exported_tree_holds_no_real_time():
    data = _archive_head()
    if data is None:
        pytest.skip("not a git checkout of this repo (a wheel or sdist install): no HEAD to read")
    assert_no_real_time(exported_hits(data))


# A planted real date is caught, end to end: a scratch repo, a commit, its archive.
def _git(repo, *args):
    env = dict(_git_env(), GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t",
                    "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
                    "-c", "core.hooksPath=" + os.devnull, *args],
                   check=True, capture_output=True, env=env)


def _scratch_repo(tmp_path, files):
    if shutil.which("git") is None:
        pytest.skip("no git")
    repo = tmp_path / "repo"
    repo.mkdir()
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text, encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "scratch")
    return repo


def _real_iso():
    return "20" + "26-10-18T" + "14:05:09.123+11:00"      # a real-looking stamp, from its parts


def test_a_planted_real_date_fails_the_guard(tmp_path):
    repo = _scratch_repo(tmp_path, {
        "README.md": "clean: " + ANCHOR_ISO + "\n",
        "tests/test_x.py": "first\nTS = '" + _real_iso() + "'\n",
    })
    found = exported_hits(_archive_head(repo))
    assert {p: [n for n, _ in h] for p, h in found.items()} == {"tests/test_x.py": [2]}
    with pytest.raises(AssertionError) as caught:
        assert_no_real_time(found)
    assert "tests/test_x.py  (line 2)" in str(caught.value)
    assert _real_iso() not in str(caught.value)          # the path and line, never the text


def test_a_planted_grouped_epoch_fails_the_guard(tmp_path):
    repo = _scratch_repo(tmp_path, {"t.py": "T0 = " + "1_7" + "00_000_000.0\n"})
    with pytest.raises(AssertionError):
        assert_no_real_time(exported_hits(_archive_head(repo)))


def test_a_planted_mail_date_fails_the_guard(tmp_path):
    mail_date = "Date: Fri, 16 Oct " + "20" + "26 09:00:00 +" + "1100\n"
    repo = _scratch_repo(tmp_path, {"m.eml": mail_date})
    with pytest.raises(AssertionError):
        assert_no_real_time(exported_hits(_archive_head(repo)))


def test_a_planted_date_tuple_fails_the_guard(tmp_path):
    repo = _scratch_repo(tmp_path, {"t.py": "T0 = datetime(" + "20" + "26, 9, 24, 4, 0)\n"})
    with pytest.raises(AssertionError):
        assert_no_real_time(exported_hits(_archive_head(repo)))


def test_a_planted_year_and_month_fails_the_guard(tmp_path):
    repo = _scratch_repo(tmp_path, {"src/m.py": "# the file served in " + "20" + "26-10.\n"})
    found = exported_hits(_archive_head(repo))
    assert {p: [n for n, _ in h] for p, h in found.items()} == {"src/m.py": [1]}
    with pytest.raises(AssertionError):
        assert_no_real_time(found)


def test_a_planted_slashed_date_fails_the_guard(tmp_path):
    repo = _scratch_repo(tmp_path, {"docs/a.md": "logs/" + "20" + "26/10/18/heard.jsonl\n"})
    found = exported_hits(_archive_head(repo))
    assert {p: [n for n, _ in h] for p, h in found.items()} == {"docs/a.md": [1]}
    with pytest.raises(AssertionError):
        assert_no_real_time(found)


def test_the_json_schema_dialect_is_let_by_and_only_as_the_dialect(tmp_path):
    url = "https://json-schema.org/draft/" + _DIALECT + "/schema"
    repo = _scratch_repo(tmp_path, {
        "s.schema.json": '{\n  "$schema": "' + url + '"\n}\n',
        "docs/s.md": "The schema is written in (JSON Schema " + _DIALECT + ").\n",
        "tests/test_s.py": 'assert S["$schema"] == "' + url + '"\n',
        "notes.md": "ok\nshipped in " + _DIALECT + ", the bare year and month\n",
    })
    # The URL and the prose name are the dialect; the same value bare is a hit.
    assert {p: [n for n, _ in h] for p, h in exported_hits(_archive_head(repo)).items()} == {
        "notes.md": [2]}


def test_the_carve_outs_hold_in_an_archive(tmp_path):
    heading = "## [0.1.0] - " + "20" + "26-10-18"
    repo = _scratch_repo(tmp_path, {
        "uv.lock": 'upload-time = "' + "20" + '25-01-02"\n',
        "LICENSE": "Version 3, 29 June " + "20" + "07\n",
        "docs/LICENSE": "Version 3, 29 June " + "20" + "07\n",
        "CHANGELOG.md": "# Changelog\n\n" + heading + "\n\n- landed on " + "20" + "26-10-18\n",
    })
    # Let by: uv.lock and the root LICENSE (not read) and the release heading. Caught: a
    # LICENSE anywhere else, and the bullet below the heading.
    assert {p: [n for n, _ in h] for p, h in exported_hits(_archive_head(repo)).items()} == {
        "docs/LICENSE": [1],
        "CHANGELOG.md": [5]}


# The matcher itself. Each sample before FLOOR is built from its parts, so this file holds no hit.
def _iso(date="20" + "26-10-01", clock="08:00:00", frac=".123", off="+10:00", sep="T"):
    return date + sep + clock + frac + off


def _epoch(head="17", tail="23456789", frac=""):
    return head + tail + frac


def _grouped(head="1_7", tail="00_000_000", frac=""):
    return head + tail + frac


@pytest.mark.parametrize("sample", [
    _iso(),                                               # a heard-log ts
    _iso(off="+11:00"),                                   # daylight time
    _iso(frac="", off="Z"),                               # UTC, whole seconds
    _iso(sep=" "),                                        # a space for the T
    "20" + "26-10-01",                                    # a date alone
    "heard_" + "20" + "26-10-01.jsonl",                   # a date in a file name
    "19" + "99-12-31",                                    # last century too
    "20" + "261013-110000",                               # a compact stamp
    "20" + "261014T023000Z",                              # a compact ISO stamp
    _epoch(),                                             # an epoch second
    _epoch(frac=".2"),                                    # with a fraction
    _epoch(head="16"),                                    # the 16xx decade
    _epoch(head="18", tail="00000000"),                   # 2027: not yet the floor
    _epoch(head="10", tail="00000000"),                   # 2001
    _epoch(tail="23456789012"),                           # a ms epoch
    _epoch(tail="23456789012345678"),                     # an ns epoch
    _grouped(),                                           # a digit-grouped epoch second
    _grouped(frac=".123"),                                # with a fraction
    _grouped(tail="00_000_000_123_456_789"),              # a digit-grouped ns epoch
    _grouped(head="1_8"),                                 # 2027, grouped
    "Thu, 15 Oct " + "20" + "26 20:00:00 +" + "1100",     # an RFC 2822 mail Date
    "measured in October " + "20" + "26",                 # a month and year in prose
    "Oct 15, " + "20" + "26",
    "datetime(" + "20" + "26, 9, 24, 4, 0)",              # a typed tuple
    "mktime((" + "20" + "26, 10, 17, 12, 0, 0, 0, 0, -1))",
    "date(" + "20" + "26,9,24)",
    "served in " + "20" + "26-10.",                       # a year and month
    "heard_" + "20" + "26-10.jsonl",                      # a year and month in a file name
    _DIALECT,                                             # the dialect's value, bare
    "json-schema.org/draft/" + _DIALECT,                  # a URL that is not the dialect's
    "JSON Schema draft " + _DIALECT,                      # prose that is not its name
    "20" + "26/10/18",                                    # a slashed date
    "logs/" + "20" + "26/10/18/x",                        # in a path
])
def test_the_matcher_catches_each_form(sample):
    assert len(real_time_hits("noted " + sample + " here")) == 1


def test_an_ns_id_inside_a_message_id_is_caught():
    mid = "<" + _epoch(tail="23456789012345678") + ".eb1da083a8bf@host.mouth>"
    assert [t for _, t in real_time_hits("Message-ID: " + mid)] == [mid[1:20]]
    glued = "x" + _epoch(tail="23456789012345678")        # a letter, not a digit, before it
    assert len(real_time_hits(glued)) == 1


@pytest.mark.parametrize("sample", [
    ANCHOR_ISO,                                           # the anchor
    ANCHOR.isoformat(timespec="milliseconds"),
    ANCHOR_SUMMER.astimezone().isoformat(),
    ANCHOR.strftime("%Y%m%d-%H%M%S"),                     # its compact stamp
    str(ANCHOR.timestamp()),                              # its epoch second
    str(int(ANCHOR.timestamp() * 1000)),                  # its ms epoch
    str(int(ANCHOR.timestamp()) * 10**9),                 # its ns epoch
    f"{int(ANCHOR.timestamp()):_}",                       # grouped
    "2031-01-01",                                         # a future value stays
    _epoch(head="19", tail="90000000"),                   # 2033: past the floor stays
    _epoch(tail="2345678"),                               # 9 digits
    _epoch(tail="234567890"),                             # 11 digits
    _epoch(tail="2345678901234567"),                      # 18 digits
    _epoch(tail="234567890123456789"),                    # 20 digits
    "ab" + _epoch() + "cd",                               # inside a hex hash
    "1_000",                                              # a grouped count, not an epoch
    "60_000_000",
    "20" + "26",                                          # a year alone
    "20" + "261013",                                      # a compact date with no clock
    "v" + "20" + "26.10.1",                               # a version
    format_datetime(ANCHOR),                              # the anchor as a mail Date
    "June " + str(ANCHOR.year),
    "Decimal " + "20" + "00",                             # a word, not a month
    "(" + "20" + "26, 13, 1)",                            # no month 13: not a date
    "(" + "20" + "26, 48000)",                            # a pair, not a date
    "json-schema.org/draft/" + _DIALECT + "/schema",      # the dialect's URL
    "JSON Schema " + _DIALECT,                            # the dialect's prose name
    "20" + "26-13",                                       # no month 13
    "a1b2c3d4-" + "20" + "19-12ab-89cd",                  # a uuid group, not a month
    "20" + "26-1234",                                     # more digits: not a month
    "20" + "26/10",                                       # a year and month by slash: not read
    "fsspec==" + "20" + "26.9.0",                         # a CalVer pin
])
def test_the_matcher_lets_other_forms_by(sample):
    assert real_time_hits("noted " + sample + " here") == []


def test_a_release_heading_is_let_by_only_in_the_changelog():
    heading = "## [1.2.3] - " + "20" + "26-10-01"
    assert real_time_hits(heading, "CHANGELOG.md") == []
    assert real_time_hits(heading + "\n- a bullet, " + "20" + "26-10-01", "CHANGELOG.md") == [
        (2, "20" + "26-10-01")]
    assert len(real_time_hits(heading, "docs/notes.md")) == 1
    assert len(real_time_hits("see " + heading, "CHANGELOG.md")) == 1  # not a heading line


def test_hits_carry_their_line_numbers():
    text = "first\n" + _iso() + " and " + _epoch() + "\n" + "third " + _epoch(frac=".5") + "\n"
    assert [n for n, _ in real_time_hits(text)] == [2, 2, 3]


def test_this_file_holds_no_hit():
    assert real_time_hits(Path(__file__).read_text(encoding="utf-8")) == []
