"""EARS-353: every file that states sessionvoice's version states the same one.

A release bumps four places: pyproject.toml, the plugin manifest, the marketplace
manifest and uv.lock's own package entry. Nothing tied them together, so one could
be bumped alone. This test reads all four and fails naming each file and its version.
"""
import json
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    tomllib = pytest.importorskip("tomli")

ROOT = Path(__file__).resolve().parents[1]
NAME = "sessionvoice"


def pyproject_version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


def plugin_version() -> str:
    return json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())["version"]


def marketplace_versions() -> dict:
    """metadata.version, plus a version on the sessionvoice plugin entry if it has one."""
    data = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    found = {".claude-plugin/marketplace.json metadata.version": data["metadata"]["version"]}
    entries = [p for p in data["plugins"] if p.get("name") == NAME]
    assert entries, f"marketplace.json lists no plugin named {NAME!r}"
    for entry in entries:
        if "version" in entry:
            found[".claude-plugin/marketplace.json plugins[sessionvoice].version"] = entry["version"]
    return found


def uv_lock_version() -> str:
    packages = tomllib.loads((ROOT / "uv.lock").read_text())["package"]
    own = [p for p in packages if p["name"] == NAME]
    assert len(own) == 1, f"uv.lock has {len(own)} [[package]] entries named {NAME!r}, want 1"
    return own[0]["version"]


def test_all_manifests_state_one_version():
    versions = {
        "pyproject.toml [project].version": pyproject_version(),
        ".claude-plugin/plugin.json version": plugin_version(),
        **marketplace_versions(),
        "uv.lock [[package]] sessionvoice version": uv_lock_version(),
    }
    table = "\n".join(f"  {where}: {version}" for where, version in versions.items())
    assert len(set(versions.values())) == 1, f"version drift, bump them together:\n{table}"
