"""EARS-313: a tests/mouth file run after a tests/-root file keeps tests/mouth's fence.

pytest 9.1 bound tests/mouth/conftest.py's fixtures to the first tests/mouth node only,
and a tests/-root file between two mouth files builds a second one. The resolver tests
then ran with the live HOME and wrote ~/.voicemode/voicemode.env. Each order runs here in
a child pytest whose HOME is tmp: a fence that fails again lands there, not in the
live HOME.
"""
from __future__ import annotations

import itertools
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APPLE = "tests/mouth/test_mouth_apple.py"
SCHEMA = "tests/test_ears_settings_schema.py"
RESOLVER = "tests/mouth/test_mouth_resolver.py"


@pytest.mark.parametrize("order", [
    (APPLE, SCHEMA, RESOLVER),                                         # the order that failed
    (RESOLVER, SCHEMA, APPLE),                                         # reversed, same shape
    (APPLE, SCHEMA, RESOLVER + "::test_the_process_env_beats_voicemode_env"),  # a node id
], ids=["reported", "reversed", "node-id"])
def test_a_mouth_file_after_a_root_file_is_still_fenced(order, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home)}
    out = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *order],
                         cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    tail = out.stdout[-3000:] + out.stderr[-2000:]
    assert out.returncode == 0, tail
    summary = out.stdout.strip().splitlines()[-1]
    assert re.search(r"\d+ passed", summary) and not re.search(r"failed|error", summary), tail
    assert not (home / ".voicemode").exists(), "a mouth test reached the unfenced HOME"


FENCE = {"heard_box", "fresh_voices", "barge_traced", "mouth_fenced"}
_PLANNED = re.compile(r"^\s+(tests/\S+?) \(fixtures used: ([^)]*)\)$", re.M)


@pytest.mark.parametrize("order", [
    *itertools.permutations((APPLE, SCHEMA, RESOLVER)),
    (APPLE, SCHEMA, "tests/mouth"),                                    # a directory after a root file (pytest folds the file into it)
    ("tests/mouth", SCHEMA, RESOLVER),                                 # a directory, then a file in it
    (SCHEMA, RESOLVER, "tests/test_ears_mail.py", APPLE, SCHEMA),      # root files on both sides, twice
    (RESOLVER + "::test_the_process_env_beats_voicemode_env", SCHEMA, APPLE + "::test_auto_picks_apple_on_a_bare_mac"),
], ids=lambda o: "+".join(Path(p.split("::")[0]).stem.removeprefix("test_") + ("::1" if "::" in p else "") for p in o))
def test_every_mouth_test_is_fenced_in_any_order(order, tmp_path):
    """The fence holds whatever the order (the super's ask): pytest's setup plan, no test body
    runs, lists the whole fence for every tests/mouth item and none of it for a root one."""
    env = {**os.environ, "HOME": str(tmp_path)}
    out = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--setup-plan", *order],
                         cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stdout[-3000:] + out.stderr[-2000:]
    planned = _PLANNED.findall(out.stdout)
    mouth = [(item, set(used.split(", "))) for item, used in planned if item.startswith("tests/mouth/")]
    root = [(item, set(used.split(", "))) for item, used in planned if not item.startswith("tests/mouth/")]
    assert mouth and root, out.stdout[-3000:]
    assert [item for item, used in mouth if not FENCE <= used] == []
    assert [item for item, used in root if used & {"heard_box", "fresh_voices", "barge_traced"}] == []
