"""EARS-409: no shipped file names a tracker id (a card like ``ABC-123``).

A tracker id in a shipped comment, doc or runtime string points at a board the reader
cannot see. Say what happened instead: "a launchd PATH holds neither", "the label
rename", "the package's 3.10 floor". This pins the release gate's grep G in the repo:

    G  = \\b[A-Z]{2,5}-[0-9]+\\b
    GV = (UTF|SHA|ISO|GPL|AGPL|LGPL|RFC|CVE|TLS|MD|PEP)-   (standard names, not ids)

The tree is `git archive HEAD`, the tree a release exports, so export-ignored notes do
not count and uncommitted edits are not read. As the gate does, it skips every file named
CHANGELOG.md (its landed bullets are history and are never rewritten) and every file
under a directory named ``tests`` (tests may name the card they pin), and binary files (a
NUL in the first 8 KiB).

It is at least as strict as the gate. The gate drops a whole LINE that holds a GV name;
here only the GV match itself is let by, so an id that shares a line with ``UTF-8`` is
still a hit. The other carve-outs are listed below, each with its reason:

- LICENCES: SPDX licence names whose text happens to fit G. Each is matched whole, so a
  bare ``BSD-4`` is still a hit.
- SYNTHETIC: obviously made-up example ids a shipped doc may show. Empty today; an entry
  needs its reason, and it is matched as the whole id.

Offline and fast: one local `git archive`, no network. Outside a git checkout (an
installed wheel or an unpacked sdist) there is no HEAD to read, and the scan skips.
The planted controls build their ids from parts, so this file's own samples are not
typed whole.
"""
import io
import os
import re
import shutil
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

import pytest

ROOT = Path(__file__).resolve().parent.parent

# The gate's G, verbatim. ASCII word boundaries, as /usr/bin/grep -E reads them.
TRACKER_ID = re.compile(r"\b[A-Z]{2,5}-[0-9]+\b", re.ASCII)
# The gate's GV prefixes: standard names (UTF-8, SHA-256, ISO-8601, RFC-2822, ...).
STANDARD = frozenset({"UTF", "SHA", "ISO", "GPL", "AGPL", "LGPL", "RFC", "CVE", "TLS", "MD",
                      "PEP"})

# name -> reason. Removed from a line, whole, before it is read.
LICENCES = {
    "CC-BY-4.0": "licence name: Creative Commons Attribution 4.0 (docs/models.md, a model's licence)",
    "BSD-2-Clause": "licence name: the SPDX id of the 2-clause BSD licence (docs/models.md)",
    "BSD-3-Clause": "licence name: the SPDX id of the 3-clause BSD licence (docs/models.md)",
}
# id -> reason. An obviously synthetic example id a shipped file may show. None today.
SYNTHETIC: dict[str, str] = {}

SKIP_NAMES = {"CHANGELOG.md"}   # any file of this name: its landed bullets are history
SKIP_DIRS = {"tests"}           # any file under a directory of this name: tests may cite ids


def tracker_ids(text):
    """Each tracker id in `text` as (line number, id), the carve-outs let by."""
    found = []
    for n, line in enumerate(text.splitlines(), 1):
        for name in LICENCES:
            line = line.replace(name, " ")
        for m in TRACKER_ID.finditer(line):
            token = m.group(0)
            if token.split("-", 1)[0] in STANDARD or token in SYNTHETIC:
                continue
            found.append((n, token))
    return found


def skipped(path):
    """True when the gate does not read `path` (a CHANGELOG.md, or anything under tests/)."""
    p = PurePosixPath(path)
    return p.name in SKIP_NAMES or bool(SKIP_DIRS.intersection(p.parts[:-1]))


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


def exported_ids(data):
    """path -> [(line, id)] for every file of the archive `data` that names a tracker id."""
    found = {}
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for member in tar.getmembers():
            if not member.isfile() or skipped(member.name):
                continue
            body = tar.extractfile(member).read()
            if b"\0" in body[:8192]:
                continue
            hits = tracker_ids(body.decode("utf-8", "replace"))
            if hits:
                found[member.name] = hits
    return found


def assert_no_tracker_id(found):
    """Fail, naming each path, line and id, if `found` holds a hit."""
    report = "\n".join(f"  {path}:{n}: {token}"
                       for path, hits in sorted(found.items()) for n, token in hits)
    total = sum(len(v) for v in found.values())
    assert not found, (
        f"{total} tracker id(s) in {len(found)} shipped file(s) of `git archive HEAD`: drop "
        f"the id and keep the meaning (say what happened, or name the change in words). An "
        f"obviously made-up example goes in SYNTHETIC, with its reason.\n{report}")


def test_the_exported_tree_names_no_tracker_id():
    data = _archive_head()
    if data is None:
        pytest.skip("not a git checkout of this repo (a wheel or sdist install): no HEAD to read")
    assert_no_tracker_id(exported_ids(data))


def test_every_carve_out_has_its_reason():
    for name, why in {**LICENCES, **SYNTHETIC}.items():
        assert why.strip(), f"{name} is let by with no reason"
    for name in SYNTHETIC:
        assert TRACKER_ID.fullmatch(name), f"SYNTHETIC holds {name!r}, which is not an id"


# The controls: a planted id is caught, end to end (a scratch repo, a commit, its archive).
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


def _id(code="ABC", number="123"):
    return code + "-" + number           # built from parts: no sample is typed whole


def test_a_planted_id_fails_the_guard(tmp_path):
    repo = _scratch_repo(tmp_path, {
        "README.md": "clean\n",
        "src/pkg/m.py": "x = 1\n# fixed in " + _id() + "\n",
    })
    found = exported_ids(_archive_head(repo))
    assert found == {"src/pkg/m.py": [(2, _id())]}
    with pytest.raises(AssertionError) as caught:
        assert_no_tracker_id(found)
    assert "src/pkg/m.py:2: " + _id() in str(caught.value)


def test_the_gate_carve_outs_hold_in_an_archive(tmp_path):
    planted = "see " + _id() + "\n"
    repo = _scratch_repo(tmp_path, {
        ".gitattributes": "/notes.md export-ignore\n",
        "notes.md": planted,                          # export-ignored: not shipped
        "CHANGELOG.md": planted,                      # history
        "docs/CHANGELOG.md": planted,                 # any CHANGELOG.md, as the gate's --exclude
        "tests/test_x.py": planted,                   # tests may cite the card they pin
        "pkg/tests/data.txt": planted,                # any tests/ dir, as the gate's --exclude-dir
        "bin.dat": "\0" + planted,                    # binary
        "docs/a.md": "ok\n" + planted,                # the one shipped hit
        "docs/b.md": "a tests word and a CHANGELOG.md word: " + _id("XY", "9") + "\n",
    })
    assert {p: [n for n, _ in h] for p, h in exported_ids(_archive_head(repo)).items()} == {
        "docs/a.md": [2], "docs/b.md": [1]}


@pytest.mark.parametrize("sample", [
    _id(),                                               # the plain form
    _id("AB", "1"),                                      # two letters, one digit
    _id("ABCDE", "123456"),                              # five letters
    "(" + _id() + ")",                                   # in parentheses
    "pre-" + _id() + ".",                                # after a hyphenated word
    _id() + ": a fix",
    "UTF-8 text, then " + _id(),                         # a GV name on the line lets only itself by
    "BSD-" + "4",                                        # not one of the licence names
    "CC-BY-" + "3.0",
    _id("XSHA", "1"),                                    # a GV name inside a longer code is not GV
])
def test_the_matcher_catches_each_form(sample):
    assert len(tracker_ids("noted " + sample + " here")) == 1


@pytest.mark.parametrize("sample", [
    "UTF-" + "8", "SHA-" + "256", "ISO-" + "8601", "GPL-" + "3", "AGPL-" + "3", "LGPL-" + "2",
    "RFC-" + "2822", "CVE-" + "1", "TLS-" + "1", "MD-" + "5", "PEP-" + "8",   # GV names
    "CC-BY-" + "4.0", "BSD-" + "2-Clause", "BSD-" + "3-Clause",                  # licences
    "A-" + "1",                                          # one letter
    "ABCDEF-" + "1",                                     # six letters
    "abc-" + "123",                                      # lower case
    "Abc-" + "123",                                      # mixed case: the code is caps only
    "ABC" + "123",                                       # no hyphen
    "ABC-" + "x1",                                       # no digits after the hyphen
    "xABC-" + "1",                                       # a letter glued before it
    "ABC-" + "12a",                                      # a letter after the digits
    "x-axis", "PR #12", "py3.10",
])
def test_the_matcher_lets_other_forms_by(sample):
    assert tracker_ids("noted " + sample + " here") == []


def test_a_synthetic_id_is_let_by_only_whole(monkeypatch):
    monkeypatch.setitem(SYNTHETIC, _id("XYZ", "1"), "a made-up example in a doc")
    assert tracker_ids("e.g. " + _id("XYZ", "1")) == []
    assert tracker_ids("e.g. " + _id("XYZ", "12")) == [(1, _id("XYZ", "12"))]


def test_hits_carry_their_line_numbers():
    text = "first\n" + _id() + " and " + _id("DE", "4") + "\nthird " + _id("FG", "5") + "\n"
    assert tracker_ids(text) == [(2, _id()), (2, _id("DE", "4")), (3, _id("FG", "5"))]
