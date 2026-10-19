"""Markdown link extraction shared by the docs tests (EARS-266, EARS-382).

tests/test_docs_layout.py checks that each relative link in the checkout resolves;
tests/test_export_ignore.py checks that no page in `git archive HEAD` links an
export-ignored note. Both read links with these expressions, so the two can never
disagree about what a link is.
"""
from __future__ import annotations

import re

FENCE = re.compile(r"^(```|~~~).*?^\1[^\n]*$", re.S | re.M)
INLINE_CODE = re.compile(r"`[^`\n]*`")
INLINE_LINK = re.compile(r"!?\[[^\]\n]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
# [ \t], not \s: a \s in the title part crosses the newline and swallows the next
# definition, so every other line of a block of `[ref]: target` lines went unchecked.
REF_DEF = re.compile(r"^ {0,3}\[[^\]\n]+\]:[ \t]*<?(\S+?)>?(?:[ \t]+.*)?$", re.M)
WIKI_LINK = re.compile(r"\[\[[^\]\n]+\]\]")


def prose(text: str) -> str:
    """The text with code fences and inline code removed: a path in code is not a link."""
    return INLINE_CODE.sub("", FENCE.sub("", text))


def links(text: str) -> list[str]:
    """Every inline link target and reference-definition target in TEXT's prose."""
    body = prose(text)
    return INLINE_LINK.findall(body) + REF_DEF.findall(body)


def is_local(target: str) -> bool:
    """A target in this tree: not an in-page anchor and not a scheme (https:, mailto:)."""
    return not (target.startswith("#") or re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I))
