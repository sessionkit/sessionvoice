"""No shipped file links this repo by full URL, or a kit repo's file by a branch name.

Two rules, over every file the repo ships but CHANGELOG.md (its landed bullets are
history and are never rewritten):

(self)   No full github.com/sessionkit/sessionvoice/(blob|tree|raw)/ URL. A link to
         one of this repo's own files is relative, so it works in a checkout,
         offline (tests/test_readme_links.py checks that each one resolves).
(branch) A link to another kit repo's file names no branch. It names `HEAD`, which
         GitHub resolves to the repo's default branch whatever that is called, or a
         release tag (`v1.2.3`):
         https://github.com/sessionkit/sessionkit/blob/HEAD/docs/settings.md
         A branch name (main, master or any other) breaks when the branch is renamed;
         a link onto `main` already 404s, as the public repos carry no such branch.
         raw.githubusercontent.com/sessionkit/<repo>/<ref>/ is held to the same rule.

A repo's home page, https://github.com/sessionkit/sessionvoice, names no file and stays.
The controls below build their bad links from pieces, so this file holds none.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXEMPT = {"CHANGELOG.md"}
PRUNE = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", "build", "dist"}

NAME = r"[A-Za-z0-9_.-]+"
# A file or folder URL into a kit repo: its repo and the ref (branch, tag or HEAD) it names.
KIT_FILE = re.compile(
    rf"(?:github\.com/sessionkit/({NAME})/(?:blob|tree|raw)|raw\.githubusercontent\.com/sessionkit/({NAME}))"
    rf"/({NAME})",
    re.I,   # github.com takes the owner and repo in any case; the ref is matched as written
)
SELF = "sessionvoice"
# The refs a link may name: HEAD, or a release tag.
ALLOWED_REF = re.compile(r"HEAD|v[0-9][A-Za-z0-9_.-]*")


def _git_env():
    # A hook or a `git -C` caller can export GIT_DIR and friends; read ROOT's repo only.
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def shipped_files() -> list[str]:
    """Every file the repo ships, relative: git's list in a checkout, else a walk (an archive)."""
    if shutil.which("git"):
        top = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, env=_git_env())
        if top.returncode == 0 and Path(top.stdout.strip()).resolve() == ROOT:
            r = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"],
                               capture_output=True, text=True, env=_git_env(), timeout=60)
            if r.returncode == 0 and r.stdout:
                return sorted(f for f in r.stdout.split("\0") if f and (ROOT / f).is_file())
    out = []
    for top, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in PRUNE]
        out += [(Path(top) / f).relative_to(ROOT).as_posix() for f in files]
    return sorted(out)


def bad_links(text: str) -> list[tuple[int, str, str]]:
    """(line number, rule, link) of every kit file URL in TEXT that breaks a rule."""
    found = []
    for n, line in enumerate(text.splitlines(), 1):
        for m in KIT_FILE.finditer(line):
            repo, ref = (m.group(1) or m.group(2)).lower(), m.group(3)
            if repo == SELF:
                found.append((n, "self", m.group(0)))
            elif not ALLOWED_REF.fullmatch(ref):
                found.append((n, "branch", m.group(0)))
    return found


def hits(files: list[str]) -> list[str]:
    """`path:line: rule: link` for every bad link in FILES, the exempt files skipped."""
    found = []
    for rel in files:
        if rel in EXEMPT:
            continue
        path = ROOT / rel
        if path.is_symlink():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        found += [f"{rel}:{n}: {rule}: {link}" for n, rule, link in bad_links(text)]
    return found


# The controls: each rule catches each planted form, and the allowed forms pass.
# Bad links are built from pieces, so this file holds none of its own.

GH = "https://github.com/"
KIT = GH + "sessionkit/"
RAW = "https://raw.githubusercontent.com/sessionkit/"
MAIN, MASTER = "ma" + "in", "mas" + "ter"
ME = "session" + "voice"


def rules(text: str) -> list[tuple[int, str]]:
    return [(n, rule) for n, rule, _ in bad_links(text)]


def test_a_branch_name_is_caught_main_master_or_any_other():
    text = (
        f"[a]({KIT}sessionkit/blob/{MAIN}/docs/a.md)\n"
        f"[b]({KIT}sessionmail/blob/{MASTER}/docs/settings.md#the-cascade)\n"
        f"[c]({KIT}sessiontools/tree/dev/docs)\n"
        f"[d]({KIT}sessionphone/raw/{MAIN}line/install.sh)\n"
        f"{RAW}sessionkit/{MASTER}/README.md\n"
        f"https://GitHub.com/SessionKit/SessionKit/blob/{MAIN.title()}/README.md\n"
    )
    assert rules(text) == [(1, "branch"), (2, "branch"), (3, "branch"), (4, "branch"),
                           (5, "branch"), (6, "branch")]


def test_head_and_a_release_tag_are_allowed():
    text = (
        f"[kit]({KIT}sessionkit/blob/HEAD/docs/settings.md#the-cascade)\n"
        f"{KIT}sessionmail/tree/HEAD/docs {KIT}sessiontools/raw/HEAD/x.sh\n"
        f"{KIT}sessionkit/blob/v1.2.3/docs/settings.md {RAW}sessionkit/v0.2.0/README.md\n"
    )
    assert bad_links(text) == []


def test_a_full_url_into_this_repo_is_caught_whatever_it_names():
    text = (
        f"[a]({KIT}{ME}/blob/HEAD/docs/a.md)\n"
        f"[b]({KIT}{ME}/tree/{MASTER}/speakers)\n"
        f"[c]({KIT}{ME}/raw/v1.2.3/install.sh)\n"
        f"{RAW}{ME}/{MAIN}/README.md\n"
        f"https://GitHub.com/SessionKit/{ME.title()}/blob/HEAD/docs/a.md\n"
    )
    assert rules(text) == [(1, "self"), (2, "self"), (3, "self"), (4, "self"), (5, "self")]


def test_repo_roots_relative_links_and_other_owners_pass():
    text = (
        f'Homepage = "{KIT}{ME}"  [repo]({KIT}{ME})  {KIT}{ME}.git  {KIT}sessionkit\n'
        "[a](docs/a.md) [b](../README.md) [s](speakers/)\n"
        f"{GH}someone-else/repo/blob/{MAIN}/x.md\n"           # not the kit's organisation
        f"git checkout {MAIN}  refs/{MAIN}  def {MAIN}():\n"
    )
    assert bad_links(text) == []


def test_a_planted_file_is_caught_and_the_changelog_is_not(tmp_path, monkeypatch):
    bad = f"see {KIT}sessionkit/blob/{MAIN}/README.md and {KIT}{ME}/blob/HEAD/docs/x.md\n"
    for rel in ("docs/x.md", "CHANGELOG.md"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(bad)
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    assert hits(["docs/x.md", "CHANGELOG.md"]) == [
        f"docs/x.md:1: branch: github.com/sessionkit/sessionkit/blob/{MAIN}",
        f"docs/x.md:1: self: github.com/sessionkit/{ME}/blob/HEAD"]


# The gate, over the repo itself.

def test_no_shipped_file_breaks_a_link_rule():
    files = shipped_files()
    assert "README.md" in files, f"found no README.md under {ROOT}: the file list is wrong"
    found = hits(files)
    assert not found, (
        "link this repo's files by relative path (self), and another kit repo's file by "
        f"HEAD or a release tag, never a branch name (branch): {found}"
    )
