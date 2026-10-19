"""The private scan list, for the tests that guard against private words (EARS-406).

This repo is public, and the words it must never ship are private: `make scan` reads them
from a list kept outside the repo. A guard test that spelt those words out would ship the
very list it guards. So each such guard reads the list at run time from the file that
SESSIONKIT_SCRUB_RULES names, and skips LOUDLY when it is not set: the skip reason names the
variable, and the terminal summary repeats it with a count (summary(), from conftest.py).
There is no default location and no fallback: unset means skip.

The list's format is release-scan's: `#` comments and blank lines are skipped; each row is
four TAB-separated fields, name<TAB>case<TAB>tier<TAB>regex. case `i` ignores case, `s` keeps
it. tier is `P0` (blocks a release) or `ruling`. Everything after the third TAB is the regex,
a Python `re` pattern matched line by line.

Nothing here prints a row's regex, a matched line or a planted word, in a failure message or
anywhere else: a red guard's output may sit in a public CI log. A guard reports a row by its
NAME column only. hits() returns names, and controls() hands back each planted word with its
row's name, so a guard can assert on the names alone. A guard calls hits() OUTSIDE its assert
statement: pytest's assertion rewriting prints the arguments of a call made inside one.

A guard whose subject carries some text the list matches by design (a vendor command its help
rightly names) leaves rows out by WHAT THEY MATCH: rows(legit=...) takes that text as neutral
sample strings and drops exactly the rows whose pattern matches one. Never by a row's NAME:
the names are the list's to change, and a guard pinned to one goes red, or quiet, when the
list renames it.

A guard keeps its power with a POSITIVE CONTROL: controls() synthesises, from each loaded
row's own regex, a word that row matches; the guard plants it into its subject and asserts
it fires. The planted word comes from the list at run time, never from the repo.
"""
import functools
import os
import re
from dataclasses import dataclass, field

import pytest

try:                                   # Python 3.11+
    import re._parser as _sre_parse
    import re._constants as _sre_c
except ImportError:                    # Python 3.10
    import sre_parse as _sre_parse
    import sre_constants as _sre_c

ENV = "SESSIONKIT_SCRUB_RULES"
SKIP_PREFIX = "private scan list not set"
SKIP = (f"{SKIP_PREFIX}: export {ENV}=<the word list `make scan` uses> to run this guard; "
        "it is private, so it is never in this repo")
TIERS = ("P0", "ruling")


@dataclass(frozen=True)
class Row:
    name: str
    tier: str
    pattern: re.Pattern = field(repr=False)     # never in a repr: a failure would print it
    lineno: int = 0


class ListError(ValueError):
    """The list could not be read or parsed. The message names the file and line, never a
    row's text beyond its name."""


def load(path):
    """Every row of the list at path, in file order."""
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().split("\n")
    except OSError as exc:
        raise ListError(f"{ENV}={path}: cannot read it: {exc.strerror}") from None
    rows = []
    for lineno, line in enumerate(lines, 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = line.split("\t", 3)
        if len(fields) != 4:
            raise ListError(f"{path}:{lineno}: want 4 TAB-separated fields, got {len(fields)}")
        name, case, tier, regex = (f.strip() for f in fields)
        if not name or not regex:
            raise ListError(f"{path}:{lineno}: empty name or regex")
        if case not in ("i", "s"):
            raise ListError(f"{path}:{lineno} ({name}): case must be i or s")
        if tier not in TIERS:
            raise ListError(f"{path}:{lineno} ({name}): tier must be one of {TIERS}")
        try:
            pattern = re.compile(regex, re.M | (re.I if case == "i" else 0))
        except re.error:
            raise ListError(f"{path}:{lineno} ({name}): bad regex") from None
        rows.append(Row(name, tier, pattern, lineno))
    if not rows:
        raise ListError(f"{path}: no rows")
    return rows


@functools.lru_cache(maxsize=None)
def _cached(path):
    return tuple(load(path))


def rows(*tiers, legit=()):
    """The rows of the list SESSIONKIT_SCRUB_RULES names, only those of the given tiers if
    any are given. Unset (or empty): the calling test SKIPS, naming the variable. Set but
    unreadable or malformed: the test FAILS, since a list that was asked for and is not there
    is a broken run, not a missing one.

    legit leaves rows out by WHAT THEY MATCH, never by their NAME: it holds neutral sample
    strings, the text a guard's subject carries by design (a vendor command its help rightly
    names, say), and every row whose pattern matches one of them is left out. The names are
    the list's to change; which rows go depends on the patterns alone, so a list that renames
    every row leaves the same rows out. A sample that matches no row FAILS the test: it leaves
    nothing out, so it is stale, and a stale sample is a defect, not a silent no-op."""
    path = os.environ.get(ENV, "")
    if not path:
        pytest.skip(SKIP)
    try:
        loaded = _cached(path)
    except ListError as exc:
        pytest.fail(str(exc), pytrace=False)
    for t in tiers:
        if t not in TIERS:
            raise ValueError(f"no tier {t!r}; the tiers are {TIERS}")
    picked = [r for r in loaded if not tiers or r.tier in tiers]
    if not picked:
        pytest.fail(f"{ENV}: the list holds no row of tier {', '.join(tiers)}", pytrace=False)
    kept, _left_out = split(picked, legit)
    return kept


def split(rows, samples):
    """(kept, left_out) for a subject that carries the neutral sample strings by design: a
    row whose pattern matches any sample is left out, every other row is kept. left_out maps
    each left-out row's NAME to the samples it matched. Fails the test when a sample matches
    no row (stale: it leaves nothing out) or when every row is left out (nothing left to scan
    with). A failure names the stale sample, which is the subject's own neutral text, never a
    row's pattern."""
    if isinstance(samples, str):
        raise TypeError("samples is a sequence of strings, not one string")
    kept, left_out, used = [], {}, set()
    for r in rows:
        matched = tuple(s for s in samples if hits(s, [r]))
        if matched:
            left_out[r.name] = matched
            used.update(matched)
        else:
            kept.append(r)
    stale = [s for s in samples if s not in used]
    if stale:
        pytest.fail(f"{ENV}: the sample(s) {stale} match no row of the list, so they leave "
                    "nothing out: drop them, or name the text the subject carries now",
                    pytrace=False)
    if not kept:
        pytest.fail(f"{ENV}: every row matches one of the {len(samples)} samples; nothing is "
                    "left to scan with", pytrace=False)
    return kept, left_out


def hits(text, rows):
    """The NAMES of the rows that match any line of text, sorted. Never the matched text."""
    lines = text.splitlines()
    return sorted({r.name for r in rows if any(r.pattern.search(line) for line in lines)})


# -- the positive control: a word each row matches, synthesised from its own regex ----------

_CATEGORY = {
    _sre_c.CATEGORY_DIGIT: "0", _sre_c.CATEGORY_NOT_DIGIT: "a",
    _sre_c.CATEGORY_WORD: "a", _sre_c.CATEGORY_NOT_WORD: " ",
    _sre_c.CATEGORY_SPACE: " ", _sre_c.CATEGORY_NOT_SPACE: "a",
}


class _Cannot(Exception):
    pass


def _one_char(items):
    """A character the set [items] matches: the first literal, range start or category."""
    if items and items[0][0] is _sre_c.NEGATE:
        for c in "aA0 -_.":                  # a negated set: the first of these it lets through
            if re.fullmatch(_set_regex(items), c):
                return c
        raise _Cannot
    for op, av in items:
        if op is _sre_c.LITERAL:
            return chr(av)
        if op is _sre_c.RANGE:
            return chr(av[0])
        if op is _sre_c.CATEGORY and av in _CATEGORY:
            return _CATEGORY[av]
    raise _Cannot


def _set_regex(items):
    """Rebuild a one-character class from its parsed items, to test a candidate against."""
    out = []
    for op, av in items:
        if op is _sre_c.NEGATE:
            out.append("^")
        elif op is _sre_c.LITERAL:
            out.append(re.escape(chr(av)))
        elif op is _sre_c.RANGE:
            out.append(f"{re.escape(chr(av[0]))}-{re.escape(chr(av[1]))}")
        elif op is _sre_c.CATEGORY:
            out.append({_sre_c.CATEGORY_DIGIT: r"\d", _sre_c.CATEGORY_NOT_DIGIT: r"\D",
                        _sre_c.CATEGORY_WORD: r"\w", _sre_c.CATEGORY_NOT_WORD: r"\W",
                        _sre_c.CATEGORY_SPACE: r"\s", _sre_c.CATEGORY_NOT_SPACE: r"\S"}[av])
        else:
            raise _Cannot
    return f"[{''.join(out)}]"


def _emit(parsed):
    out = []
    for op, av in parsed:
        if op is _sre_c.LITERAL:
            out.append(chr(av))
        elif op is _sre_c.NOT_LITERAL:
            out.append("a" if chr(av) != "a" else "b")
        elif op is _sre_c.ANY:
            out.append("a")
        elif op is _sre_c.IN:
            out.append(_one_char(av))
        elif op in (_sre_c.MAX_REPEAT, _sre_c.MIN_REPEAT, getattr(_sre_c, "POSSESSIVE_REPEAT", None)):
            lo, _hi, sub = av
            out.append(_emit(sub) * lo)
        elif op is _sre_c.SUBPATTERN:
            out.append(_emit(av[-1]))
        elif op is getattr(_sre_c, "ATOMIC_GROUP", None):
            out.append(_emit(av))
        elif op is _sre_c.BRANCH:
            out.append(_emit(av[1][0]))      # the first alternative
        elif op in (_sre_c.AT, _sre_c.ASSERT, _sre_c.ASSERT_NOT):
            pass                             # zero width: the verify step below judges it
        elif op is _sre_c.CATEGORY:
            out.append(_CATEGORY.get(av, "a"))
        else:                                # a back-reference, a conditional: no sample
            raise _Cannot
    return "".join(out)


def sample(row):
    """A word row matches when it stands alone on a line between spaces, built from the
    row's own regex; None when its regex is beyond this small generator. Verified: the
    returned word, between spaces, is a hit for row."""
    try:
        word = _emit(_sre_parse.parse(row.pattern.pattern, row.pattern.flags))
    except (_Cannot, re.error, KeyError):
        return None
    if not word.strip() or not row.pattern.search(f" {word} "):
        return None
    return word


def controls(rows):
    """[(row name, planted word)] for every row a word could be synthesised for. Fails the
    test when there is none: a positive control that plants nothing proves nothing."""
    out = [(r.name, w) for r in rows if (w := sample(r)) is not None]
    if not out:
        pytest.fail(f"{ENV}: no row of the list yielded a word to plant", pytrace=False)
    return out


# -- the loud half of the skip --------------------------------------------------------------

def summary(terminalreporter):
    """A section in the terminal summary whenever a guard skipped for want of the list: how
    many, and the variable to set. It shows without -rs."""
    n = sum(1 for rep in terminalreporter.stats.get("skipped", [])
            if isinstance(rep.longrepr, tuple) and SKIP_PREFIX in str(rep.longrepr[2]))
    if n:
        terminalreporter.write_sep("-", f"private scan list: {n} guard tests skipped", yellow=True)
        terminalreporter.write_line(f"{n} skipped: {SKIP}", yellow=True)
