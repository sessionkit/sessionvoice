"""EARS-202 G34: the heard ride-along hook is an install step of its own, and the docs say so.

The plugin ships no hooks (a plugin hook would run in every seat that has the plugin, and
the ride-along is opt-in per seat), so the README's Install section carries the exact
settings.local.json snippet, and the plugin description no longer says "nothing to install".
These tests keep the three in step: the snippet parses and names both events, the command
it gives clamps every failure to exit 1, and the plugin has no hooks file.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
PLUGIN = ROOT / ".claude-plugin" / "plugin.json"
COMMAND = "ears heard hook || exit 1"


def install_section() -> str:
    text = README.read_text()
    m = re.search(r"^## Install\n(.*?)^## ", text, re.S | re.M)
    assert m, "README has no '## Install' section"
    return m.group(1)


def snippet() -> dict:
    """The indented JSON block in README's Install section."""
    lines = install_section().splitlines()
    start = next(i for i, l in enumerate(lines) if l.strip() == "{" and l.startswith("    "))
    block = []
    for l in lines[start:]:
        if l and not l.startswith("    "):
            break
        block.append(l[4:])
        if l == "    }":
            break
    return json.loads("\n".join(block))


def test_the_install_section_carries_a_settings_snippet_for_both_events():
    hooks = snippet()["hooks"]
    assert set(hooks) == {"PostToolUse", "PostToolBatch"}
    for event, entries in hooks.items():
        cmds = [h["command"] for e in entries for h in e["hooks"]]
        assert cmds == [COMMAND], event
        assert all(h["type"] == "command" for e in entries for h in e["hooks"])


def test_the_snippet_names_no_machine_path():
    sec = install_section()
    assert "settings.local.json" in sec
    assert "/Users/" not in json.dumps(snippet()) and "~" not in json.dumps(snippet())


def test_the_plugin_description_no_longer_says_nothing_to_install():
    meta = json.loads(PLUGIN.read_text())
    assert "nothing to install" not in meta["description"].lower()
    assert "hook" in meta["description"].lower()


def test_the_plugin_description_says_what_readme_says_about_stt():
    # EARS-289 (SK-46 G34): README's Requirements make a speech server on :8890 a
    # prerequisite and Apple an opt-in backend; the description said "no STT server to run".
    desc = json.loads(PLUGIN.read_text())["description"]
    req = re.search(r"^## Requirements\n(.*?)^## ", README.read_text(), re.S | re.M).group(1)
    assert "no stt server" not in desc.lower()
    assert "127.0.0.1:8890" in desc and "127.0.0.1:8890" in req
    assert "Parakeet" in desc and "Parakeet" in req
    assert "`ears install --apple`" in desc and "--apple" in req
    assert "OpenAI-compatible STT" in desc


def test_the_plugin_ships_no_hooks_so_the_ride_along_stays_opt_in():
    meta = json.loads(PLUGIN.read_text())
    assert "hooks" not in meta
    assert not (ROOT / "hooks" / "hooks.json").exists()


def test_the_command_clamps_a_missing_ears_to_exit_1(tmp_path):
    # 127 (command not found) would show as an error too, but `|| exit 1` makes every
    # failure a 1, never the 2 that stops the agent's turn.
    env = {"PATH": str(tmp_path), "HOME": str(tmp_path)}
    r = subprocess.run(["/bin/sh", "-c", COMMAND], env=env, capture_output=True, text=True)
    assert r.returncode == 1


def test_the_command_runs_the_hook_verb(tmp_path):
    ears = Path(sys.executable).parent / "ears"
    if not ears.exists():
        pytest.skip("no `ears` script beside this interpreter")
    # VOICEMODE_HEARD_HOOK=off: the verb is routed and returns before reading any log,
    # so nothing under the real ~/.voicemode is read or written.
    env = {"PATH": f"{ears.parent}:/usr/bin:/bin", "HOME": str(tmp_path),
           "VOICEMODE_HEARD_HOOK": "off"}
    r = subprocess.run(["/bin/sh", "-c", COMMAND], env=env, input="{}",
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout == ""
