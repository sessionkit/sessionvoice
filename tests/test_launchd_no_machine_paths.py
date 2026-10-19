"""EARS-218: the launch agents in launchd/ are TEMPLATES. No machine's home directory lives in
the repo: each file is a *.plist.in whose paths are @PLACEHOLDERS@, filled at install time
(the sed line in each file's header). A raw *.plist with a real home path is what this
catches. launchd itself is not run; the rendered text is only parsed."""
import plistlib
import re
from pathlib import Path

import pytest

LAUNCHD = Path(__file__).resolve().parent.parent / "launchd"
TEMPLATES = sorted(LAUNCHD.glob("*.plist.in"))
PLACEHOLDER = re.compile(r"@([A-Z_]+)@")


def test_every_launch_agent_is_a_template():
    files = sorted(p.name for p in LAUNCHD.iterdir() if p.is_file())
    assert files, "launchd/ is empty: a broken checkout, not a clean one"
    assert [f for f in files if not f.endswith(".plist.in")] == []


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_no_home_directory_in_a_template(path):
    hits = [n for n, line in enumerate(path.read_text().splitlines(), 1)
            if re.search(r"/Users/[^/\s<]", line)]
    assert hits == [], f"{path.name}: a machine home path on line(s) {hits}"


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_a_template_renders_to_a_plist(path):
    text = path.read_text()
    assert PLACEHOLDER.search(text), f"{path.name} has no placeholder to fill"
    rendered = PLACEHOLDER.sub(lambda m: f"/opt/example/{m.group(1).lower()}", text)
    # Header comments may hold "--" (strict XML refuses it; launchd's parser, like plutil,
    # takes it). Only the plist body is under test here.
    plist = plistlib.loads(re.sub(r"<!--.*?-->", "", rendered, flags=re.S).encode())
    assert plist["Label"] == path.name.removesuffix(".plist.in")
    body = rendered[rendered.index("<dict>"):]
    assert not PLACEHOLDER.search(body), "a placeholder survived the render"
