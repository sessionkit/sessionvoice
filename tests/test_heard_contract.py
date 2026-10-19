"""EARS-294 (CS-03): the heard log's format is sessionvoice's contract.

docs/logs.md's "Who owns this format" section states the version, and
``ears.heard.SCHEMA`` is the ``v`` every writer stamps. This holds the two to each
other, so a bump in one without the other fails here. It reads the doc and imports
the module, nothing else: no log is written.
"""
from __future__ import annotations

import re
from pathlib import Path

from ears import heard

ROOT = Path(__file__).resolve().parents[1]
LOGS_MD = ROOT / "docs" / "logs.md"
HEADING = "### Who owns this format"


def owner_section() -> str:
    text = LOGS_MD.read_text(encoding="utf-8")
    assert HEADING in text, f"docs/logs.md has no {HEADING!r} section"
    body = text.split(HEADING, 1)[1]
    end = re.search(r"^#{1,3} ", body, re.M)
    return body[: end.start()] if end else body


def test_the_documented_version_is_heard_schema():
    section = owner_section()
    named = {int(n) for n in re.findall(r"heard-log v(\d+)", section)}
    stamped = {int(n) for n in re.findall(r'`"v": (\d+)`', section)}
    assert named, "the section names no heard-log version"
    assert stamped, 'the section gives no `"v": N` value'
    assert named == stamped == {heard.SCHEMA}


def test_the_section_names_sessionvoice_the_owner_and_the_contract():
    section = owner_section()
    assert "sessionvoice owns" in section
    assert "logs/conversations/heard_YYYY-MM-DD.jsonl" in section
    assert f"`{heard.LOCK_NAME}`" in section
    assert "guests" in section


def test_the_module_names_sessionvoice_the_owner():
    assert "sessionvoice owns" in (heard.__doc__ or "")
