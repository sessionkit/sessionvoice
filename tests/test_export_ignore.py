"""EARS-382: internal notes that live beside shipped code stay out of the release archive.

keeper/QUESTIONS.md and keeper/ROADMAP.md came in with the keeper scripts, and talk/ holds
its design notes as Markdown. Nothing checked the archive against the ruled list, so they
shipped. `.gitattributes` now export-ignores each one by an anchored, per-file line; this
pins it.

The tree is `git archive HEAD`, the tree a release exports. Outside a git checkout (an
installed wheel, an unpacked sdist, or the archive itself) there is no HEAD to read, and
these tests skip, so the suite still runs from the archive.
"""
import io
import os
import posixpath
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from tests.md_links import is_local, links

ROOT = Path(__file__).resolve().parent.parent

# Whole-file internal notes. Each has its own anchored export-ignore line.
IGNORED = [
    "keeper/QUESTIONS.md",
    "keeper/ROADMAP.md",
    "src/mouth/DESIGN.md",
    "docs/phone-voice.md",
    "talk/README.md",
    "talk/ask-4890.1.md",
    "talk/ruling-question-wake.md",
    "talk/pager-files-design.md",
    "talk/wake-settings-design.md",
    "talk/features/200.md",
    "talk/features/200.1.md",
    "talk/features/200.2.md",
    "talk/features/60.2.1.md",
    "talk/features/70.1.md",
    "talk/features/70.2.md",
]

# The code beside those notes still ships: the ignore lines name files, never a folder.
STILL_SHIPPED = [
    "keeper/ears-keeper.sh",
    "keeper/ears-mail.py",
    "keeper/defaults/wake.json",
    "src/mouth/__init__.py",
    "src/talk/server.py",
    "docs/mouth.md",
    "docs/ears.md",
]


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


# Shipped files that name an ignored note on purpose: they keep it out, they never send a
# reader to it. Every other shipped file that cites one fails
# test_no_shipped_file_cites_an_ignored_note.
NAMES_THEM_ON_PURPOSE = {
    ".gitattributes": "the export-ignore lines themselves",
    "tests/test_export_ignore.py": "this test, which lists the notes it keeps out",
    "CHANGELOG.md": "landed bullets name the notes as history, and AGENTS.md forbids"
                    " rewriting a landed bullet",
}

# A file name ending in .md, as a comment or docstring writes it: `talk/README.md`,
# `src/mouth/DESIGN.md`, "the mouth's DESIGN.md", "(DESIGN.md 8)".
MD_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]*\.md\b")


def cited_notes(path, text, ignored, shipped):
    """The ignored notes that `text`, the body of shipped file `path`, cites.

    A name counts when it is an ignored path from the repo root, or from the file's
    own folder, or a bare file name (no folder) that only an ignored note has: no
    shipped file shares it. So a bare README.md is never read as talk/README.md, but
    a bare DESIGN.md is src/mouth/DESIGN.md. An all-caps note is cited by its stem
    too, "ROADMAP", when no shipped file has that stem: README never counts.
    """
    shipped_names = {posixpath.basename(p) for p in shipped}
    only_ignored = {posixpath.basename(p): p for p in ignored
                    if posixpath.basename(p) not in shipped_names}
    shipped_stems = {n.split(".", 1)[0] for n in shipped_names}
    caps_stems = {}
    for name, note in only_ignored.items():
        stem = name[:-len(".md")]
        if stem.isupper() and stem not in shipped_stems:
            caps_stems[stem] = note
    found = []
    for name in MD_NAME.findall(text):
        name = name[2:] if name.startswith("./") else name
        here = posixpath.normpath(posixpath.join(posixpath.dirname(path), name))
        if name in ignored:
            hit = name
        elif here in ignored:
            hit = here
        elif "/" not in name and name in only_ignored:
            hit = only_ignored[name]
        else:
            continue
        if hit not in found:
            found.append(hit)
    for stem, note in caps_stems.items():
        if re.search(rf"\b{re.escape(stem)}\b", text) and note not in found:
            found.append(note)
    return found


def test_cited_notes_reads_paths_folders_and_bare_names():
    ignored = {"talk/README.md", "src/mouth/DESIGN.md"}
    shipped = {"README.md", "src/mouth/x.py", "launchd/t.plist.in"}
    assert cited_notes("launchd/t.plist.in", "(talk/README.md, \"Pairing\")",
                       ignored, shipped) == ["talk/README.md"]
    assert cited_notes("src/mouth/x.py", "See DESIGN.md.", ignored, shipped) == [
        "src/mouth/DESIGN.md"]
    assert cited_notes("bench/b.py", "as news (DESIGN.md 8)", ignored, shipped) == [
        "src/mouth/DESIGN.md"]
    # A name is read from the file's own folder too: talk/'s README.md is the ignored one.
    assert cited_notes("talk/x.py", "See ./README.md.", ignored, shipped) == ["talk/README.md"]
    assert cited_notes("src/talk/x.py", "See README.md.", ignored, shipped) == []
    # A bare README.md is the shipped README, never the ignored talk/README.md.
    assert cited_notes("bin/x.sh", "see README.md", ignored, shipped) == []
    assert cited_notes("bin/x.sh", "see docs/talk.md", ignored, shipped) == []
    # An all-caps note's stem alone is a cite, unless a shipped file has that stem.
    ignored = ignored | {"keeper/ROADMAP.md"}
    assert cited_notes("src/x.py", "the design split, ROADMAP", ignored, shipped) == [
        "keeper/ROADMAP.md"]
    assert cited_notes("src/x.py", "see the DESIGN notes", ignored, shipped) == [
        "src/mouth/DESIGN.md"]
    assert cited_notes("src/x.py", "the roadmap, a design", ignored, shipped) == []
    assert cited_notes("bin/x.sh", "see the README", ignored, shipped) == []


@pytest.fixture(scope="module")
def archive():
    """{path: bytes} for every file in `git archive HEAD`."""
    data = _archive_head()
    if data is None:
        pytest.skip("not a git checkout of this repo (a wheel, sdist or the archive): no HEAD")
    files = {}
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for member in tar.getmembers():
            if member.isfile():
                files[member.name] = tar.extractfile(member).read()
    return files


@pytest.mark.parametrize("path", IGNORED)
def test_the_internal_note_is_not_in_the_archive(archive, path):
    assert path not in archive, f"{path} ships in `git archive HEAD`: export-ignore it"


def test_no_talk_note_is_in_the_archive(archive):
    """Every Markdown file under talk/ is a design note, so a new one is caught too."""
    shipped = sorted(p for p in archive if p.startswith("talk/") and p.endswith(".md"))
    assert not shipped, f"talk/ notes ship in the archive: {shipped}"


@pytest.mark.parametrize("path", STILL_SHIPPED)
def test_the_code_beside_the_notes_still_ships(archive, path):
    assert path in archive, f"{path} is missing from `git archive HEAD`: an ignore line is too wide"


def test_no_shipped_page_links_an_ignored_note(archive):
    """A link to a file the archive leaves out is a dead link for everyone who installs it."""
    ignored = set(IGNORED)
    dead = []
    for path, body in sorted(archive.items()):
        if not path.endswith(".md"):
            continue
        for target in links(body.decode("utf-8", "replace")):
            if not is_local(target):
                continue
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path),
                                                         target.split("#", 1)[0]))
            if resolved in ignored:
                dead.append(f"{path} -> {target}")
    assert not dead, f"shipped pages link export-ignored notes: {dead}"


def test_no_shipped_file_cites_an_ignored_note(archive):
    """A plist template, a docstring, a comment, a script or a page that sends its reader
    to an export-ignored note sends everyone who installs it to a file they do not have.

    Markdown pages are read here too, prose and backticks as well as links; the
    CHANGELOG is allowlisted, since its landed bullets name the notes as history.
    """
    ignored = set(IGNORED)
    shipped = set(archive)
    cites = []
    for path, body in sorted(archive.items()):
        if path in NAMES_THEM_ON_PURPOSE:
            continue
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:   # a binary file: a WAV, an image
            continue
        for note in cited_notes(path, text, ignored, shipped):
            cites.append(f"{path} -> {note}")
    assert not cites, f"shipped files cite export-ignored notes: {cites}"


@pytest.mark.parametrize("path", sorted(NAMES_THEM_ON_PURPOSE))
def test_each_file_that_names_the_notes_on_purpose_still_ships(archive, path):
    """An allowlist entry for a file that is gone would hide nothing; keep the list true."""
    assert path in archive, f"{path} no longer ships: drop it from NAMES_THEM_ON_PURPOSE"
