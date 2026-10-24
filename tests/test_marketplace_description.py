"""The marketplace listing describes the plugin in the plugin manifest's own words.

.claude-plugin/marketplace.json keeps its own copy of the plugin description, a short
listing blurb. It drifted once: the plugin manifest's description changed and the
listing kept the old text. The rule that ties them is one line: the listing's
description is the plugin manifest's description up to the end of its first sentence.
This test reads both repo files and fails, quoting both texts, when they disagree.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
NAME = "sessionvoice"


def first_sentence(text: str) -> str:
    """Text up to and including the first full stop that ends the text or meets a space."""
    m = re.match(r"(.*?\.)(?:\s|$)", text, re.S)
    assert m, f"no full stop in {text!r}"
    return m.group(1)


def plugin_description() -> str:
    return json.loads(PLUGIN.read_text())["description"]


def marketplace_description() -> str:
    data = json.loads(MARKETPLACE.read_text())
    entries = [p for p in data["plugins"] if p.get("name") == NAME]
    assert len(entries) == 1, f"marketplace.json lists {len(entries)} plugins named {NAME!r}"
    return entries[0]["description"]


def test_first_sentence_stops_at_the_first_full_stop():
    assert first_sentence("One, two. Three at 127.0.0.1. Four.") == "One, two."
    assert first_sentence("Only one.") == "Only one."


def test_marketplace_description_is_the_plugin_descriptions_first_sentence():
    want = first_sentence(plugin_description())
    got = marketplace_description()
    assert got == want, (
        "marketplace.json plugins[sessionvoice].description must equal the first sentence "
        f"of plugin.json's description.\n  plugin.json first sentence: {want!r}\n"
        f"  marketplace.json:           {got!r}")
