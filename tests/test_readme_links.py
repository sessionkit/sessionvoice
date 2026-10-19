"""README.md and docs/ link into this repo by relative path, and every such link resolves.

The pages are read in a checkout as often as on GitHub: in an editor, offline, with no
browser to open. So a link to a file of this repo is relative to the page it is on:

    from README.md:   docs/models.md   docs/models.md#an-anchor   speakers/
    from docs/*.md:   models.md        ../README.md               ../speakers/README.md

never a full https://github.com/sessionkit/sessionvoice/... URL. Rendering README.md
for the package page is the kit's release step, done at publish time; the repo carries
no workaround for it. A pure `#anchor`, a `mailto:` and any other scheme stay as they
are, and a link to another kit repo stays a full URL onto its `HEAD`
(tests/test_github_links.py holds that rule).

The same holds for the repo's owner: every github.com URL that names this repo or
one of its kit siblings names the `sessionkit` organisation, as pyproject's
Homepage and Repository do.

A relative link is still dead if its file or folder is missing, export-ignored (the
public repo is the export, so the file is not there), outside the repo, or its
`#anchor` names no heading. Those are checked against `git archive HEAD` and skip
outside a git checkout.
"""
from __future__ import annotations

import io
import posixpath
import re
import tarfile
from pathlib import Path
from urllib.parse import unquote

import pytest

from tests.md_links import FENCE, is_local, links, prose
from tests.test_export_ignore import _archive_head

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"

ORG = "sessionkit"
REPO = "sessionvoice"
KIT = {"sessionkit", "sessionmail", "sessionphone", "sessiontools", "sessionview", "sessionvoice"}
BASE = f"https://github.com/{ORG}/{REPO}/"
# A full URL onto one of this repo's files or folders: it must be a relative link.
SELF_URL = re.compile(rf"^https?://(?:www\.)?github\.com/{ORG}/{REPO}/(?:blob|tree|raw)/", re.I)

# Raw HTML a README may carry: <a href="..."> and <img src="...">.
HTML_ATTR = re.compile(r"""<[a-zA-Z][^>]*?\b(?:href|src)\s*=\s*["']([^"']+)["']""")
# An autolink, <https://...>.
AUTOLINK = re.compile(r"<(https?://[^>\s]+)>")
# Any github.com/<owner>/<repo> in the prose, linked or bare.
GITHUB = re.compile(r"https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")


def targets(text: str) -> list[str]:
    """Every link target in TEXT: inline, reference definitions, HTML href/src, autolinks."""
    body = prose(text)
    return links(text) + HTML_ATTR.findall(body) + AUTOLINK.findall(body)


def self_urls(text: str) -> list[str]:
    """The targets that name one of this repo's files by full URL: each must be relative."""
    return [t for t in targets(text) if SELF_URL.match(t)]


def wrong_owner(text: str) -> list[str]:
    """github.com URLs naming a kit repo under any owner but the kit's organisation."""
    bad = []
    for owner, repo in GITHUB.findall(prose(text)):
        name = repo[:-len(".git")] if repo.endswith(".git") else repo
        if name.lower() in KIT and owner.lower() != ORG:
            bad.append(f"github.com/{owner}/{repo}")
    return bad


def slug(heading: str) -> str:
    """The anchor GitHub gives a heading: lower case, punctuation dropped, spaces to dashes."""
    text = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return text.replace(" ", "-")


def anchors(markdown: str) -> set[str]:
    """Every heading anchor in MARKDOWN, duplicates numbered as GitHub numbers them.

    Only fenced code is dropped: a heading's inline code is part of its anchor
    (`## The mouth (`src/mouth`)` gives #the-mouth-srcmouth).
    """
    seen: dict[str, int] = {}
    out = set()
    for line in FENCE.sub("", markdown).splitlines():
        m = re.match(r"^ {0,3}#{1,6}\s+(.*?)\s*#*\s*$", line)
        if not m:
            continue
        base = slug(m.group(1))
        n = seen.get(base, 0)
        seen[base] = n + 1
        out.add(base if n == 0 else f"{base}-{n}")
    return out


def dead_links(page: str, text: str, files: dict[str, bytes]) -> list[str]:
    """PAGE's links (TEXT) into the repo whose file, folder or anchor is not in FILES.

    PAGE is the page's path in FILES (the archive); a relative target resolves against
    its folder, and a pure #anchor against PAGE itself.
    """
    folders = set()
    for p in files:
        d = posixpath.dirname(p)
        while d and d not in folders:
            folders.add(d)
            d = posixpath.dirname(d)
    dead = []
    for target in targets(text):
        if target.startswith("#"):
            if page.endswith(".md") and unquote(target[1:]) not in anchors(text):
                dead.append(f"{target}: no heading gives it")
            continue
        if not is_local(target):
            continue
        part, _, anchor = target.split("?", 1)[0].partition("#")
        path = posixpath.normpath(posixpath.join(posixpath.dirname(page), unquote(part)))
        if path == ".." or path.startswith("../"):
            dead.append(f"{target}: outside the repo")
        elif part.endswith("/") or path in folders:
            if path not in folders:
                dead.append(f"{target}: no such folder in the archive")
        elif path not in files:
            dead.append(f"{target}: no such file in the archive")
        elif anchor and path.endswith(".md"):
            if unquote(anchor) not in anchors(files[path].decode("utf-8", "replace")):
                dead.append(f"{target}: no heading gives #{anchor}")
    return dead


# The checkers, on text built here.

def test_a_full_url_into_the_repo_is_caught_in_every_form():
    text = (
        f"[a]({BASE}blob/HEAD/docs/a.md) [b]({BASE}tree/master/speakers)\n"
        f"[ref]: {BASE}raw/master/install.sh\n"
        f'<a href="{BASE}blob/master/x.md">x</a> <{BASE}blob/master/y.md>\n'
    )
    assert self_urls(text) == [f"{BASE}blob/HEAD/docs/a.md", f"{BASE}tree/master/speakers",
                               f"{BASE}raw/master/install.sh", f"{BASE}blob/master/x.md",
                               f"{BASE}blob/master/y.md"]


def test_relative_anchor_scheme_and_other_repo_links_pass():
    text = (
        "[a](docs/a.md) [b](docs/b.md#part) [s](speakers/) [here](#install)\n"
        "[mail](mailto:x@example.com) <https://docs.astral.sh/uv/>\n"
        f"[home]({BASE[:-1]}) [kit](https://github.com/{ORG}/sessionkit/blob/HEAD/docs/x.md)\n"
        f"`{BASE}blob/master/in-code.md`\n"
        f"```\n[fenced]({BASE}blob/master/fenced.md)\n```\n"
    )
    assert self_urls(text) == []


def test_a_kit_repo_under_another_owner_is_caught():
    text = (
        "https://github.com/someone-else/sessionvoice and "
        "[m](https://github.com/someone-else/sessionmail.git) and "
        f"{BASE} and https://github.com/astral-sh/uv"
    )
    assert wrong_owner(text) == ["github.com/someone-else/sessionvoice",
                                 "github.com/someone-else/sessionmail.git"]


def test_slugs_follow_github():
    assert slug("A hosted speech endpoint instead") == "a-hosted-speech-endpoint-instead"
    assert slug("sessionvoice's home") == "sessionvoices-home"
    assert slug("The `ears` CLI: run it") == "the-ears-cli-run-it"
    assert anchors("# Same\n## Same\n### Other #\n") == {"same", "same-1", "other"}
    assert anchors("## The mouth (`src/mouth`)\n### `turn`: a finished turn\n```\n# no\n```\n") == {
        "the-mouth-srcmouth", "turn-a-finished-turn"}


def test_a_dead_relative_link_is_caught():
    files = {"README.md": b"# R\n", "docs/a.md": b"# Title\n\n## Part one\n",
             "speakers/README.md": b"# S\n"}
    readme = (
        "# R\n"
        "[ok](docs/a.md#part-one) [ok](speakers/) [ok](speakers) [ok](#r)\n"
        "[gone](docs/gone.md) [nope](nope/) [bad](docs/a.md#part-two) [lost](#nowhere)\n"
    )
    page = (
        "[ok](../README.md) [ok](a.md) [ok](../speakers/README.md)\n"
        "[up](../../x.md) [gone](../docs/gone.md)\n"
    )
    dead = dead_links("README.md", readme, files)
    assert len(dead) == 4, dead
    assert ("docs/gone.md" in dead[0] and "nope/" in dead[1] and "#part-two" in dead[2]
            and "#nowhere" in dead[3])
    dead = dead_links("docs/a.md", page, files)
    assert len(dead) == 2, dead
    assert "outside the repo" in dead[0] and "no such file" in dead[1]


# The pages themselves.

def pages() -> list[Path]:
    return [README, *sorted((ROOT / "docs").glob("*.md"))]


@pytest.mark.parametrize("path", pages(), ids=lambda p: str(p.relative_to(ROOT)))
def test_pages_link_into_the_repo_relatively(path):
    found = self_urls(path.read_text(encoding="utf-8"))
    rel = path.relative_to(ROOT).as_posix()
    assert not found, (
        f"{rel}: link this repo's files by relative path, as the page is read in a "
        f"checkout too (from README.md: docs/x.md; from docs/: x.md, ../README.md): {found}"
    )


def test_readme_names_the_kit_organisation():
    found = wrong_owner(README.read_text())
    assert not found, f"README.md: kit repos live under github.com/{ORG}/: {found}"


@pytest.fixture(scope="module")
def archive() -> dict[str, bytes]:
    data = _archive_head()
    if data is None:
        pytest.skip("not a git checkout of this repo (a wheel, sdist or the archive): no HEAD")
    files = {}
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for member in tar.getmembers():
            if member.isfile():
                files[member.name] = tar.extractfile(member).read()
    return files


def test_shipped_pages_links_resolve(archive):
    """Read from the archive, so the links checked are the ones that ship."""
    shipped = ["README.md"] + sorted(p for p in archive
                                     if posixpath.dirname(p) == "docs" and p.endswith(".md"))
    assert len(shipped) > 1, "found no docs/*.md in the archive: the page list is wrong"
    dead = [f"{page}: {d}" for page in shipped
            for d in dead_links(page, archive[page].decode("utf-8"), archive)]
    assert not dead, f"links that are dead in the public repo: {dead}"
