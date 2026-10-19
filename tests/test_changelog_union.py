"""EARS-356: CHANGELOG.md merges with ``merge=union``, and a guard catches the
damage union can do.

Every branch adds a bullet under ``## [Unreleased]``, so before this change
every branch that landed after another one conflicted in CHANGELOG.md. One
night gave four such clashes (EARS-352, 341, 342 and 354). ``.gitattributes``
now says ``CHANGELOG.md merge=union``: on a conflict, git keeps both sides'
lines instead of stopping.

Two kinds of test pin that:

* REPLAY. The four real clashes are merged again with ``git merge-tree
  --write-tree`` in a throwaway bare clone: once with union off, once on.
  Off, CHANGELOG.md conflicts; on, it is clean, holds no conflict marker,
  holds every line either side added and has no duplicated bullet. 342 also
  conflicted in ``docs/logs.md``, which union does not touch, so the tests
  assert on CHANGELOG.md only. The attributes are set from outside: an
  empty ``attr.tree`` hides every in-tree ``.gitattributes``, and
  ``core.attributesFile`` turns union on. The checkout under test is never
  edited. If the four pairs are not in the repo (a shallow clone, say), the
  replay skips and says why.

* GUARD. Union cannot conflict, so a bad merge lands silently. The guard
  reads CHANGELOG.md and fails if the same bullet's first line appears twice
  (whitespace normalised). It passes on master and catches the synthetic
  case below: two branches each reword the same two landed bullets, union
  keeps both versions of the pair and writes the unchanged bullet between
  them twice.

What the guard does NOT catch (measured with git 2.56, see
``test_guard_blind_spots``): git keeps a line two sides share out of the
conflict, so a bullet's unchanged first line is written once. When one
bullet is edited on both sides, union keeps both versions, but they differ,
so the guard sees no duplicate. When an edit sits next to an insert, the
stale text comes back as a continuation line, which the guard does not
read. Hence the rule in AGENTS.md: never rewrite a bullet that has landed
on master; add a new one.
"""
from __future__ import annotations

import os
import re
import subprocess
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = "CHANGELOG.md"

# (task, master before the land, the branch that clashed with it)
PAIRS = [
    ("EARS-352", "d20f401", "9e3b842"),
    ("EARS-341", "dedba21", "cdb065c"),
    ("EARS-342", "bf5660b", "1bd2089"),
    ("EARS-354", "0815a68", "4d634bb"),
]

MARKER = re.compile(r"^(<{7}|={7}|>{7})( |$)", re.M)
BULLET = re.compile(r"^\s*- (.*)$")
FENCE = re.compile(r"^\s*(```|~~~)")


def duplicated_bullets(text: str) -> list[str]:
    """First lines of ``- `` bullets that appear more than once.

    Whitespace is normalised. Nested bullets count; fenced code is skipped.
    """
    seen: Counter[str] = Counter()
    in_fence = False
    for line in text.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = BULLET.match(line)
        if m:
            seen[" ".join(m.group(1).split())] += 1
    return sorted(k for k, n in seen.items() if n > 1)


# --- a hermetic git -----------------------------------------------------------


def git_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@example.invalid",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@example.invalid",
    )
    return env


def git(repo: Path, *args: str, check: bool = True, stdin: str | None = None):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=git_env(),
        check=check,
    )


def merge(repo: Path, ours: str, theirs: str, union_file: Path | None):
    """``git merge-tree --write-tree``, with in-tree attributes hidden.

    Returns (exit code, conflicted paths, merged CHANGELOG.md text).
    """
    empty = git(repo, "hash-object", "-w", "-t", "tree", "--stdin", stdin="").stdout.strip()
    attrs = str(union_file) if union_file else os.devnull
    r = git(
        repo,
        "-c", f"attr.tree={empty}",
        "-c", f"core.attributesFile={attrs}",
        "merge-tree", "--write-tree", "--name-only", "--no-messages",
        ours, theirs,
        check=False,
    )
    assert r.returncode in (0, 1), r.stderr
    tree, *conflicted = [line for line in r.stdout.splitlines() if line]
    text = git(repo, "cat-file", "-p", f"{tree}:{CHANGELOG}").stdout
    return r.returncode, set(conflicted), text


def added_lines(repo: Path, base: str, tip: str) -> list[str]:
    diff = git(repo, "diff", "--unified=0", base, tip, "--", CHANGELOG).stdout
    return [
        line[1:]
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]


@pytest.fixture
def union_file(tmp_path: Path) -> Path:
    f = tmp_path / "union-attributes"
    f.write_text(f"{CHANGELOG} merge=union\n")
    return f


# --- the line itself -----------------------------------------------------------


def is_checkout_top() -> bool:
    """True when ROOT is the top of a git checkout. A release archive (or an unpacked
    sdist) is not one: it ships .gitattributes but has no repo for git to ask."""
    top = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, env=git_env())
    return top.returncode == 0 and Path(top.stdout.strip()).resolve() == ROOT


def test_gitattributes_sets_union_for_changelog():
    lines = (ROOT / ".gitattributes").read_text().splitlines()
    assert f"{CHANGELOG} merge=union" in lines
    if not is_checkout_top():
        pytest.skip("not a git checkout of this repo (the release archive, a wheel or sdist): "
                    "the .gitattributes line is checked, `git check-attr` has no repo to ask")
    r = subprocess.run(
        ["git", "-C", str(ROOT), "check-attr", "merge", "--", CHANGELOG],
        capture_output=True, text=True, check=True,
    )
    assert r.stdout.strip() == f"{CHANGELOG}: merge: union"


# --- REPLAY ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def replay_repo(tmp_path_factory) -> Path:
    """A bare, shared clone: its objects are borrowed, its writes its own."""
    dst = tmp_path_factory.mktemp("replay") / "repo.git"
    r = subprocess.run(
        ["git", "clone", "--quiet", "--bare", "--shared", str(ROOT), str(dst)],
        capture_output=True, text=True, env=git_env(),
    )
    if r.returncode:
        pytest.skip(f"cannot clone the checkout for a replay: {r.stderr.strip()}")
    missing = [
        sha
        for _, *shas in PAIRS
        for sha in shas
        if git(dst, "cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode
    ]
    if missing:
        pytest.skip(
            "the EARS-356 replay needs the four real clash pairs, and this repo "
            f"lacks {', '.join(missing)} (a shallow clone?)"
        )
    return dst


@pytest.mark.parametrize("task,ours,theirs", PAIRS, ids=[p[0] for p in PAIRS])
def test_replay_conflicts_without_union(replay_repo, task, ours, theirs):
    code, conflicted, _ = merge(replay_repo, ours, theirs, union_file=None)
    assert code == 1
    assert CHANGELOG in conflicted


@pytest.mark.parametrize("task,ours,theirs", PAIRS, ids=[p[0] for p in PAIRS])
def test_replay_is_clean_with_union(replay_repo, union_file, task, ours, theirs):
    _, conflicted, text = merge(replay_repo, ours, theirs, union_file)
    assert CHANGELOG not in conflicted  # EARS-342 still conflicts in docs/logs.md
    assert not MARKER.search(text)
    base = git(replay_repo, "merge-base", ours, theirs).stdout.strip()
    merged = set(text.splitlines())
    for tip in (ours, theirs):
        lost = [line for line in added_lines(replay_repo, base, tip) if line not in merged]
        assert not lost, f"{task}: lines {tip} added are missing: {lost}"
    assert duplicated_bullets(text) == []


# --- GUARD ----------------------------------------------------------------------


def test_guard_passes_on_master():
    assert duplicated_bullets((ROOT / CHANGELOG).read_text()) == []


BASE = """\
## [Unreleased]

### Fixed

- Alpha fix (EARS-1).
- Beta fix (EARS-2). It
  reads the log.
- Gamma fix (EARS-3).

## [0.1.0]
"""


def union_merge(tmp_path: Path, ours: str, theirs: str) -> str:
    """Merge two edits of BASE with union on, as a real merge does."""
    repo = tmp_path / "synthetic"
    repo.mkdir()
    git(repo, "init", "--quiet", "--initial-branch=main")
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "attributes").write_text(f"{CHANGELOG} merge=union\n")
    path = repo / CHANGELOG
    path.write_text(BASE)
    git(repo, "add", CHANGELOG)
    git(repo, "commit", "--quiet", "-m", "base")
    git(repo, "switch", "--quiet", "-c", "side")
    path.write_text(theirs)
    git(repo, "commit", "--quiet", "-am", "theirs")
    git(repo, "switch", "--quiet", "main")
    path.write_text(ours)
    git(repo, "commit", "--quiet", "-am", "ours")
    r = git(repo, "merge-tree", "--write-tree", "--name-only", "--no-messages",
            "main", "side", check=False)
    assert r.returncode == 0, "union should leave nothing to resolve"
    tree = r.stdout.splitlines()[0]
    return git(repo, "cat-file", "-p", f"{tree}:{CHANGELOG}").stdout


def test_guard_catches_edit_both(tmp_path):
    """Both branches reword the landed Alpha and Gamma bullets.

    Without union this conflicts. With it, the merge is clean, both versions
    of each bullet stay, and the Beta bullet between them is written twice.
    """
    ours = BASE.replace("Alpha fix", "Alpha fix, ours").replace("Gamma fix", "Gamma fix, ours")
    theirs = BASE.replace("Alpha fix", "Alpha fix, theirs").replace(
        "Gamma fix", "Gamma fix, theirs"
    )
    text = union_merge(tmp_path, ours, theirs)
    assert "Alpha fix, ours" in text and "Alpha fix, theirs" in text
    assert duplicated_bullets(text) == ["Beta fix (EARS-2). It"]


@pytest.mark.parametrize(
    "case,ours,theirs,silent",
    [
        (
            "one bullet edited on both sides",
            BASE.replace("Alpha fix", "Alpha fix, ours"),
            BASE.replace("Alpha fix", "Alpha fix, theirs"),
            ["- Alpha fix, ours (EARS-1).", "- Alpha fix, theirs (EARS-1)."],
        ),
        (
            "a continuation line edited on both sides",
            BASE.replace("reads the log.", "reads the log twice."),
            BASE.replace("reads the log.", "reads the heard log."),
            ["  reads the log twice.", "  reads the heard log."],
        ),
        (
            "an edit next to an insert brings the stale text back",
            BASE.replace("reads the log.", "reads the heard log."),
            BASE.replace("reads the log.\n", "reads the log.\n- Delta fix (EARS-4).\n"),
            ["  reads the heard log.", "  reads the log.", "- Delta fix (EARS-4)."],
        ),
    ],
    ids=["edit-both-one-bullet", "edit-both-continuation", "stale-next-to-insert"],
)
def test_guard_blind_spots(tmp_path, case, ours, theirs, silent):
    """What union damages and the guard cannot see: the merge is clean, the
    damage is in the file, and no bullet's first line repeats. The AGENTS.md
    rule (never rewrite a landed bullet) is the defence here, not the guard.
    """
    text = union_merge(tmp_path, ours, theirs)
    lines = text.splitlines()
    assert all(line in lines for line in silent), case
    assert duplicated_bullets(text) == [], case
