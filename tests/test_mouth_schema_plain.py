"""settings/mouth.schema.json goes into a stranger's home: every description reads
plainly, with no person's name, no date or clock time, no ticket ID, and the user
addressed as "you", never "he/his/him". The barge_ignore_words default list is
data, not a description, and is not checked."""
import json
import re
from pathlib import Path

import pytest

from tests import _scanlist

SCHEMA = Path(__file__).resolve().parents[1] / "settings" / "mouth.schema.json"

BANNED = {
    # a name, capitalised as in prose, from the placeholder cast; real names are on the
    # private scan list, which test_no_description_holds_a_private_word reads (EARS-406)
    "a person's name": r"\b(Alice|Bob|Carol|Dave|Ivy|Theo|Vera)\b",
    "a date": r"\b\d{4}-\d{2}-\d{2}\b",
    "a clock time": r"\b\d{1,2}:\d{2}\b",
    "a task ID": r"\b[A-Z]{2,5}-\d+\b",
    "he/his/him for the user": r"\b(he|his|him)\b",
}


def descriptions(node, path="$"):
    """Every (json path, description string) in the schema, nested ones too."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "description" and isinstance(v, str):
                yield path, v
            else:
                yield from descriptions(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from descriptions(v, f"{path}[{i}]")


def offences(schema):
    return [(path, what, m.group(0))
            for path, text in descriptions(schema)
            for what, pat in BANNED.items()
            for m in re.finditer(pat, text, re.IGNORECASE if what.startswith("he/") else 0)]


def test_the_walk_finds_the_nested_descriptions():
    paths = [p for p, _ in descriptions(json.loads(SCHEMA.read_text()))]
    assert "$" in paths                                   # the top-level one
    assert "$.properties.barge_in.anyOf[1].properties.room" in paths
    assert "$.$defs.who.anyOf[1]" in paths
    assert len(paths) >= 20


def test_every_description_reads_plainly():
    assert offences(json.loads(SCHEMA.read_text())) == []


@pytest.mark.parametrize("text, what", [
    ("Vera, voice Sun 2030-06-09: louder.", "a person's name"),
    ("Set on 2030-06-09.", "a date"),
    ("Changed at 12:34.", "a clock time"),
    ("EARS-238: a cut came late.", "a task ID"),
    ("How many of his own words cut a line he talks over.", "he/his/him for the user"),
    ("He talks over it.", "he/his/him for the user"),
])
def test_each_rule_catches_its_kind(text, what):
    assert what in {w for _, w, _ in offences({"description": text})}


@pytest.mark.parametrize("text", [
    "How many of your own words cut a line you talk over.",
    "50 is about -6 dB; 0.93-0.96x real time; EBU R128; the theme, then here.",
])
def test_plain_words_pass(text):
    assert offences({"description": text}) == []


def test_no_description_holds_a_private_word():
    """EARS-406: no description holds a word of the private scan list (every row), and the
    same scan fires on each word planted from that list into the top-level description."""
    rows = _scanlist.rows()
    schema = json.loads(SCHEMA.read_text())

    def text(node):
        return "\n".join(t for _, t in descriptions(node))

    found = _scanlist.hits(text(schema), rows)
    assert found == [], f"a description holds a word of row(s) {found}"
    missed = [name for name, word in _scanlist.controls(rows)
              if name not in _scanlist.hits(text(dict(schema, description=f"{schema['description']} {word}.")), rows)]
    assert missed == [], f"the scan missed a word planted from row(s) {missed}"


def test_the_default_word_list_is_not_checked():
    p = json.loads(SCHEMA.read_text())["properties"]["barge_ignore_words"]
    assert {"he", "his", "him"} <= set(p["default"])      # data, legitimately; the walk skips it
    assert offences({"default": p["default"]}) == []
