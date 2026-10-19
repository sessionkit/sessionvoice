"""EARS-266: docs/ is one flat folder of Markdown, its links resolve, and each
page makes its point up front.

The convention (sessionkit): every doc is ``docs/<name>.md``,
with no subfolders; links are plain Markdown links, never wiki links; and the
point of a page is in its first 40 lines and 400 tokens. ``docs/internal/`` is
the one exception: it is the dev diary, held apart until the owner rules on it, so
it is allowed as a folder and left unchecked here.

A moved page breaks links silently, so this pins them: every relative link in
docs/*.md, README.md and skills/ must name a file that exists.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.md_links import WIKI_LINK, is_local, links, prose

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
ALLOWED_SUBDIRS = {"internal"}  # D7: the dev diary, not moved until ruled


def top_docs() -> list[Path]:
    return sorted(DOCS.glob("*.md"))


def checked_files() -> list[Path]:
    return [*top_docs(), ROOT / "README.md", *sorted((ROOT / "skills").rglob("*.md"))]


def test_docs_has_no_subfolder_but_internal():
    subdirs = {p.name for p in DOCS.iterdir() if p.is_dir()}
    assert subdirs <= ALLOWED_SUBDIRS, f"docs/ must be flat; found {sorted(subdirs - ALLOWED_SUBDIRS)}"


def test_the_moved_pages_are_where_the_links_say():
    # One more page moved here too, but it is export-ignored (EARS-382): no link names it.
    for name in ("architecture.md", "architecture.html"):
        assert (DOCS / name).is_file(), f"docs/{name} is missing"
    assert not (DOCS / "architecture").exists() and not (DOCS / "design").exists()


@pytest.mark.parametrize("path", checked_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_every_relative_link_resolves(path: Path):
    broken = []
    for target in links(path.read_text()):
        if not is_local(target):
            continue
        file_part = target.split("#", 1)[0]
        if not (path.parent / file_part).exists():
            broken.append(target)
    assert not broken, f"{path.relative_to(ROOT)}: links to missing files: {broken}"


@pytest.mark.parametrize("path", checked_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_no_wiki_links(path: Path):
    found = WIKI_LINK.findall(prose(path.read_text()))
    assert not found, f"{path.relative_to(ROOT)}: wiki links, use [text](file.md): {found}"


@pytest.mark.parametrize("path", top_docs(), ids=lambda p: p.name)
def test_each_doc_leads_with_its_point(path: Path):
    """A title, then a paragraph of prose saying what the page is, inside the
    first 40 lines and 400 tokens (about 1600 characters)."""
    lines = path.read_text().splitlines()
    assert lines and lines[0].startswith("# "), f"{path.name}: no '# ' title on line 1"
    i = 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    first = lines[i] if i < len(lines) else ""
    assert first and not re.match(r"^(#|```|~~~|\||[-*+] |\d+\. |>)", first), (
        f"{path.name}: line {i + 1} should open a lead paragraph, not {first!r}"
    )
    j = i
    while j < len(lines) and lines[j].strip():
        j += 1
    lead = "\n".join(lines[i:j])
    assert j <= 40, f"{path.name}: the lead paragraph runs past line 40"
    assert len("\n".join(lines[:j])) <= 1600, f"{path.name}: the lead is past ~400 tokens"
    assert len(lead.split()) >= 12, f"{path.name}: the lead is too short to make a point: {lead!r}"


def test_architecture_page_is_built_from_its_source():
    """docs/architecture.html embeds docs/architecture.md; rebuild with
    bin/build-architecture-page whenever the Markdown changes."""
    page = (DOCS / "architecture.html").read_text()
    m = re.search(r"^ const md = (\".*\");$", page, re.M)
    assert m, "docs/architecture.html has no embedded Markdown"
    assert json.loads(m.group(1)) == (DOCS / "architecture.md").read_text(), (
        "docs/architecture.html is stale: run bin/build-architecture-page"
    )


def test_the_builder_lives_in_bin_and_names_the_flat_paths():
    builder = ROOT / "bin" / "build-architecture-page"
    assert builder.is_file() and builder.stat().st_mode & 0o111, "builder missing or not executable"
    src = builder.read_text()
    assert '"architecture.md"' in src and '"architecture.html"' in src
