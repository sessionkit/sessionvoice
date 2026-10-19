"""tests/_scanlist.py, the private scan list's reader for the guard tests (EARS-406), on a
FAKE list of placeholder words: these run with or without the real list."""
import re
import subprocess
import sys

import pytest

from tests import _scanlist

FAKE = "\n".join([
    "# a comment, then a blank line",
    "",
    "vera\ts\tP0\t\\bVera\\b",
    "ivy-any-case\ti\tP0\tivy",
    "theo-dir\ts\truling\t~/\\.(theo|dave)\\b",
    "carol-tab\ts\truling\tcarol\\tbob",          # everything after the third TAB is the regex
    "  padded  \ti\truling\t  alice  ",
])


@pytest.fixture
def listfile(tmp_path, monkeypatch):
    p = tmp_path / "SCRUB-RULES"
    p.write_text(FAKE + "\n")
    monkeypatch.setenv(_scanlist.ENV, str(p))
    _scanlist._cached.cache_clear()
    yield p
    _scanlist._cached.cache_clear()


def test_load_reads_the_four_fields(listfile):
    rows = _scanlist.load(listfile)
    assert [(r.name, r.tier, r.lineno) for r in rows] == [
        ("vera", "P0", 3), ("ivy-any-case", "P0", 4), ("theo-dir", "ruling", 5),
        ("carol-tab", "ruling", 6), ("padded", "ruling", 7)]
    assert rows[3].pattern.pattern == "carol\\tbob"
    assert rows[4].pattern.pattern == "alice"     # fields are stripped, as release-scan does


def test_the_case_flag_is_honoured(listfile):
    rows = _scanlist.rows()
    assert _scanlist.hits("Vera", rows) == ["vera"]
    assert _scanlist.hits("vera", rows) == []                  # s: case kept
    assert _scanlist.hits("IVY", rows) == ["ivy-any-case"]     # i: case ignored


def test_hits_are_names_matched_line_by_line(listfile):
    rows = _scanlist.rows()
    assert _scanlist.hits("x ~/.theo/log\nVera\nnothing\ncarol\tbob", rows) == \
        ["carol-tab", "theo-dir", "vera"]
    assert _scanlist.hits("carol\nbob", rows) == []            # no match across a line break
    assert _scanlist.hits("", rows) == []


def test_rows_picks_a_tier(listfile):
    assert [r.name for r in _scanlist.rows("P0")] == ["vera", "ivy-any-case"]
    assert len(_scanlist.rows("P0", "ruling")) == 5
    with pytest.raises(ValueError):
        _scanlist.rows("P1")


def test_rows_leaves_out_the_rows_a_sample_matches(listfile):
    """legit holds neutral text the subject carries; exactly the rows matching it go."""
    assert [r.name for r in _scanlist.rows(legit=("Vera", "ALICE"))] == \
        ["ivy-any-case", "theo-dir", "carol-tab"]               # padded is case-blind
    assert [r.name for r in _scanlist.rows("P0", legit=("Vera",))] == ["ivy-any-case"]
    assert [r.name for r in _scanlist.rows(legit=("Vera at ~/.theo/log",))] == \
        ["ivy-any-case", "carol-tab", "padded"]                 # one sample, two rows
    assert len(_scanlist.rows()) == 5                           # no samples, every row
    with pytest.raises(TypeError):
        _scanlist.rows(legit="Vera")                           # one string is not a list of samples
    with pytest.raises(pytest.fail.Exception):
        _scanlist.rows("P0", legit=("Vera", "ivy"))            # nothing left to scan with


def test_a_row_name_is_not_a_sample(listfile):
    """There is no leave-out by NAME: the old keyword is gone, and a row's name handed over as
    a sample matches no row, so it fails as stale rather than leaving that row out."""
    with pytest.raises(TypeError):
        _scanlist.rows(but=("vera",))
    with pytest.raises(pytest.fail.Exception) as e:
        _scanlist.rows(legit=("theo-dir",))
    assert "theo-dir" in str(e.value) and "match no row" in str(e.value)


def test_a_stale_sample_fails_loudly_naming_it_never_a_pattern(listfile):
    """A sample that matches no row leaves nothing out: a defect, never a silent no-op, even
    beside a sample that does match. The failure names the sample (the subject's own text)
    and never a row's pattern."""
    for legit in [("no such text",), ("Vera", "no such text")]:
        with pytest.raises(pytest.fail.Exception) as e:
            _scanlist.rows(legit=legit)
        msg = str(e.value)
        assert "'no such text'" in msg and "match no row" in msg
        assert not any(r.pattern.pattern in msg for r in _scanlist.load(listfile))
    with pytest.raises(pytest.fail.Exception):
        _scanlist.rows("P0", legit=("~/.theo",))               # it matches a row, of another tier


def test_split_maps_each_left_out_row_to_its_samples():
    rows = [_scanlist.Row(n, "P0", re.compile(rx), 0) for n, rx in
            [("a", r"\bVera\b"), ("b", "ivy"), ("c", r"~/\.theo\b")]]
    kept, left_out = _scanlist.split(rows, ("Vera and ivy", "ivy league"))
    assert [r.name for r in kept] == ["c"]
    assert left_out == {"a": ("Vera and ivy",), "b": ("Vera and ivy", "ivy league")}


# -- rename invariance: the leave-outs follow the patterns, never the names ------------------

# The new names are words joined by an underscore, never <word>-<number>: a placeholder name
# must not be able to collide with a real list's row name.
RENAME = {"vera": "renamed_one", "ivy-any-case": "renamed_two", "theo-dir": "renamed_three",
          "carol-tab": "renamed_four", "padded": "renamed_five"}
RENAMED = "\n".join(
    "\t".join([RENAME[line.split("\t")[0].strip()]] + line.split("\t")[1:])
    if line and not line.startswith("#") else line for line in FAKE.split("\n"))

# A placeholder subject that carries two words of the fake list by design, and the samples
# its guard names for them.
SUBJECT = "Ask alice at the desk.\nIts log is ~/.theo/log, kept a week.\n"
SUBJECT_LEGIT = ("alice", "~/.theo/log")


def guard(subject):
    """A guard as the real ones are written: rows by sample, hits() outside the assert."""
    return _scanlist.hits(subject, _scanlist.rows(legit=SUBJECT_LEGIT))


def use(path, text, monkeypatch):
    path.write_text(text + "\n")
    monkeypatch.setenv(_scanlist.ENV, str(path))
    _scanlist._cached.cache_clear()


def test_renamed_rows_leave_the_same_rows_out(tmp_path, monkeypatch):
    """Rename every row of the list, keep the patterns: the guard keeps and leaves out the
    same rows, and gives the same result on its subject, clean and planted."""
    got = {}
    for label, text in [("named", FAKE), ("renamed", RENAMED)]:
        use(tmp_path / label, text, monkeypatch)
        kept = _scanlist.rows(legit=SUBJECT_LEGIT)
        got[label] = ([r.pattern.pattern for r in kept], [r.name for r in kept],
                      guard(SUBJECT), guard(SUBJECT + "Signed, Vera.\n"))
    _scanlist._cached.cache_clear()
    named, renamed = got["named"], got["renamed"]
    assert named[0] == renamed[0]                               # the same patterns kept
    assert [RENAME[n] for n in named[1]] == renamed[1]          # the same rows, renamed
    assert named[2] == renamed[2] == []                         # the subject is clean both ways
    assert named[3] == ["vera"] and renamed[3] == ["renamed_one"]  # the plant is caught both ways


@pytest.mark.parametrize("text", [FAKE, RENAMED], ids=["named", "renamed"])
def test_a_planted_private_word_still_fails_the_guard(tmp_path, monkeypatch, text):
    """The positive control: leaving rows out for the subject's samples does not blind the
    guard to the rest: every word synthesised from a kept row, planted in the subject, is a
    hit, and only the rows the samples match are gone."""
    use(tmp_path / "list", text, monkeypatch)
    kept = _scanlist.rows(legit=SUBJECT_LEGIT)
    assert len(kept) == 3                                       # 5 rows, 2 matched a sample
    missed = [name for name, word in _scanlist.controls(kept)
              if name not in guard(f"{SUBJECT}x {word} x\n")]
    assert missed == []
    _scanlist._cached.cache_clear()


GUARD_SUITE = '''
import pathlib
from tests import _scanlist

LEGIT = ("alice", "~/.theo/log")

def test_guard():
    rows = _scanlist.rows(legit=LEGIT)
    found = _scanlist.hits(pathlib.Path("subject.txt").read_text(), rows)
    assert found == [], f"subject.txt holds a word of row(s) {found}"
'''


def test_a_red_guard_prints_row_names_only(tmp_path):
    """Run a guard, on the renamed placeholder list, against a subject with a planted word:
    it fails, naming the row, and its output holds neither the word nor any row's pattern."""
    listfile = tmp_path / "list"
    listfile.write_text(RENAMED + "\n")
    (tmp_path / "test_guard.py").write_text(GUARD_SUITE)
    (tmp_path / "subject.txt").write_text(SUBJECT + "Signed, IVY.\n")
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(_scanlist.__file__).rsplit("/tests/", 1)[0],
           _scanlist.ENV: str(listfile)}
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                        str(tmp_path / "test_guard.py")],
                       capture_output=True, text=True, env=env, cwd=tmp_path, timeout=60)
    out = p.stdout + p.stderr
    assert "1 failed" in out, out
    assert "['renamed_two']" in out, out                           # the row, by its name
    for secret in ["IVY", "ivy"] + [r.pattern.pattern for r in _scanlist.load(listfile)]:
        assert secret not in out, secret


def test_unset_skips_loudly_naming_the_variable(monkeypatch):
    monkeypatch.delenv(_scanlist.ENV, raising=False)
    with pytest.raises(pytest.skip.Exception) as e:
        _scanlist.rows()
    assert str(e.value).startswith(_scanlist.SKIP_PREFIX)
    assert _scanlist.ENV in str(e.value)
    monkeypatch.setenv(_scanlist.ENV, "")                       # empty is unset
    with pytest.raises(pytest.skip.Exception):
        _scanlist.rows()


def test_set_but_missing_fails(tmp_path, monkeypatch):
    monkeypatch.setenv(_scanlist.ENV, str(tmp_path / "nowhere"))
    with pytest.raises(pytest.fail.Exception) as e:
        _scanlist.rows()
    assert "cannot read" in str(e.value)


@pytest.mark.parametrize("line, why", [
    ("vera\ts\tP0", "want 4"),
    ("vera\tx\tP0\tVera", "case must be"),
    ("vera\ts\tP9\tVera", "tier must be"),
    ("\ts\tP0\tVera", "empty name"),
    ("vera\ts\tP0\t(Vera", "bad regex"),
])
def test_a_bad_row_is_refused_by_line_never_by_its_text(tmp_path, line, why):
    p = tmp_path / "list"
    p.write_text(f"# ok\n{line}\n")
    with pytest.raises(_scanlist.ListError) as e:
        _scanlist.load(p)
    assert why in str(e.value) and f"{p}:2" in str(e.value)
    assert "Vera" not in str(e.value)                          # the regex is never echoed


def test_an_empty_list_is_refused(tmp_path):
    p = tmp_path / "list"
    p.write_text("# only comments\n\n")
    with pytest.raises(_scanlist.ListError, match="no rows"):
        _scanlist.load(p)


@pytest.mark.parametrize("regex, flags", [
    (r"\bVera\b", ""),
    (r"ivy", "i"),
    (r"(?<![0-9a-f]{4})dave|dave(?![0-9a-f]{4})", "i"),        # lookarounds, an alternation
    (r"~/\.(theo|dave)\b", ""),
    (r"\b(10\.[0-9]{1,3}\.[0-9]{1,3}|192\.168\.[0-9]{1,3})\b", ""),
    (r"(\+99[ 0-9]|\b04[0-9]{2}[ -]?[0-9]{3}\b)", ""),
    (r"\b([01][0-9]|2[0-3]):[0-5][0-9](:[0-5][0-9])? (Mon|Tue)\b", ""),
    (r"\b[a-z][a-z0-9-]*\+([a-z]+-[0-9]+\.)?(carol|bob)\b", "i"),
    (r"bob('s)?\b", ""),
    (r"[^x]vera\s+\d\W\S", ""),
    (r"theo.?ivy", ""),
])
def test_a_sample_is_synthesised_and_matches(regex, flags):
    row = _scanlist.Row("r", "P0", re.compile(regex, re.M | (re.I if flags else 0)), 1)
    word = _scanlist.sample(row)
    assert word and row.pattern.search(f" {word} ")


@pytest.mark.parametrize("regex", [r"(a)\1", r"^$", r"\b"])
def test_no_sample_when_the_regex_is_beyond_the_generator(regex):
    assert _scanlist.sample(_scanlist.Row("r", "P0", re.compile(regex), 1)) is None


def test_controls_cover_every_fake_row(listfile):
    rows = _scanlist.rows()
    got = _scanlist.controls(rows)
    assert [n for n, _ in got] == [r.name for r in rows]
    for name, word in got:
        assert name in _scanlist.hits(f"x {word} x", rows)


def test_controls_fail_when_nothing_can_be_planted():
    with pytest.raises(pytest.fail.Exception):
        _scanlist.controls([_scanlist.Row("r", "P0", re.compile(r"(a)\1"), 1)])


SUMMARY_SUITE = '''
import pytest
from tests import _scanlist

def test_a():
    _scanlist.rows()

def test_b():
    _scanlist.rows("P0")

def test_c():
    pytest.skip("another reason")
'''


@pytest.mark.parametrize("listed", [False, True])
def test_the_terminal_summary_counts_the_skips(tmp_path, listfile, listed):
    """Without the list, a bare run (no -rs) says how many guards skipped and names the
    variable; with it, the guards run and the section is absent."""
    (tmp_path / "test_guards.py").write_text(SUMMARY_SUITE)
    conftest = tmp_path / "conftest.py"
    conftest.write_text("from tests import _scanlist\n\n"
                        "def pytest_terminal_summary(terminalreporter):\n"
                        "    _scanlist.summary(terminalreporter)\n")
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(_scanlist.__file__).rsplit("/tests/", 1)[0]}
    if listed:
        env[_scanlist.ENV] = str(listfile)
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                        str(tmp_path / "test_guards.py")],
                       capture_output=True, text=True, env=env, cwd=tmp_path, timeout=60)
    if listed:
        assert "2 passed, 1 skipped" in p.stdout, p.stdout
        assert "private scan list" not in p.stdout
    else:
        assert "3 skipped" in p.stdout, p.stdout
        assert "private scan list: 2 guard tests skipped" in p.stdout
        assert f"export {_scanlist.ENV}=" in p.stdout
