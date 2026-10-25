"""EARS-447: the dependency docs, to the family standard.

The README's `## Requirements` is only the operating systems and a link to
docs/dependencies.md. That page has one entry per dependency, opens with the
required list, the optional list, the four pending a ruling and the upgrade
lines, and gives the held four no class and no install step. The Dependabot
file is the standard one: uv, the root, daily, and no github-actions entry
while the repo has no workflows. Every Python dependency pyproject.toml names
is on the page, so a new one fails here until the page follows. The page is
not the full list: its first screen links uv.lock, the full resolved set, and
the speech server's entry links that venv's own lock.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
DEPS = (ROOT / "docs" / "dependencies.md").read_text(encoding="utf-8")
HELD = ("Homebrew", "tmux", "notmuch", "ffmpeg")


def _requirements() -> str:
    m = re.search(r"^## Requirements\n(.*?)^## ", README, re.S | re.M)
    assert m, "README has no '## Requirements' section"
    return m.group(1)


def _held(name: str) -> str:
    m = re.search(rf"^### {re.escape(name)}\n(.*?)(?=^##)", DEPS + "\n## end", re.S | re.M)
    assert m, f"docs/dependencies.md has no '### {name}' entry"
    return m.group(1)


def test_requirements_is_the_operating_systems_and_a_link():
    req = _requirements()
    boxes = re.findall(r"^- \[[ x]\] (\S+)", req, re.M)
    assert boxes == ["macOS", "Linux", "WSL", "Windows"], boxes
    assert "](docs/dependencies.md)" in req
    assert "**Required:**" not in req and "**Optional:**" not in req
    lines = [ln for ln in req.splitlines() if ln.strip() and not ln.startswith("- [")]
    assert len(lines) <= 2, f"Requirements carries more than the link line: {lines}"


UV_LOCK_SENTENCE = (
    "The full resolved set, every package at the version this repo is tested with, "
    "is in [`uv.lock`][uv-lock]."
)


def test_the_first_screen_links_the_full_set_in_uv_lock():
    # The page lists the direct dependencies; uv.lock holds every package, pinned.
    head = " ".join(DEPS.split("\n## ", 1)[0].split())
    assert UV_LOCK_SENTENCE in head
    assert re.search(r"^\[uv-lock\]: \.\./uv\.lock$", DEPS, re.M)
    assert (ROOT / "uv.lock").is_file()


def test_the_page_is_not_called_the_full_list():
    for text in (DEPS, _requirements()):
        flat = " ".join(text.split()).lower()
        for claim in ("the full list", "complete list", "everything sessionvoice depends on"):
            assert claim not in flat, claim


def test_the_speech_server_entry_links_its_own_lock():
    row = next(ln for ln in DEPS.splitlines() if ln.startswith("| [The speech server]"))
    assert "](../speech/requirements.lock)" in row
    assert (ROOT / "speech" / "requirements.lock").is_file()


def test_the_first_screen_is_the_lists_and_the_upgrade():
    head = DEPS.split("\n## ", 1)[0]
    for label in ("**Required:**", "**Optional:**", "**Pending a ruling:**", "**Upgrade:**"):
        assert label in head, label
    assert "|" not in head, "tables come after the first screen"
    for name in HELD:
        assert name in head.split("**Pending a ruling:**", 1)[1], name


@pytest.mark.parametrize("name", HELD)
def test_a_held_tool_has_no_class_and_no_install_step(name):
    entry = _held(name)
    assert "Status: pending a ruling" in entry
    for word in ("brew install", "Required", "Optional", "required", "optional"):
        assert word not in entry, (name, word)
    for part in ("**What uses it:**", "**Without it:**", "**Where it comes from today:**"):
        assert part in entry, (name, part)


def test_ffmpeg_names_every_use_the_code_has():
    entry = _held("ffmpeg")
    for use in ("mouth play", "mouth render", "loudness", "ears file", "ears eval"):
        assert use in entry, use


def test_the_speech_server_upgrade_names_the_first_installs_flag():
    """A bare re-run is refused when the wake file names no seat, and a re-install while
    our own server runs only uses it: the upgrade line says both."""
    for text in (DEPS, (ROOT / "docs" / "speech-server.md").read_text(encoding="utf-8")):
        assert "ears services uninstall --only speech" in text
        assert "--agent NAME" in text and "--no-agent" in text


def _names(reqs: list[str]) -> set[str]:
    return {re.split(r"[<>=!~\[ ;]", r, maxsplit=1)[0] for r in reqs}


def test_every_python_dependency_is_on_the_page():
    tomllib = pytest.importorskip("tomllib")       # 3.11+; the suite also runs on 3.10
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    names = _names(project["dependencies"])
    for extra, reqs in project["optional-dependencies"].items():
        if extra != "test":
            names |= _names(reqs)
            assert f"`{extra}`" in DEPS, f"extra {extra} is not on docs/dependencies.md"
    missing = sorted(n for n in names if n not in DEPS)
    assert not missing, f"docs/dependencies.md does not name {missing}"
    lo, hi = re.fullmatch(r">=3\.(\d+),<3\.(\d+)", project["requires-python"]).groups()
    assert f"Python 3.{lo} to 3.{int(hi) - 1}" in DEPS


def test_dependabot_is_the_standard_file():
    conf = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text())
    ecosystems = {u["package-ecosystem"]: u for u in conf["updates"]}
    assert conf["version"] == 2
    assert ecosystems["uv"]["directory"] == "/" and ecosystems["uv"]["schedule"]["interval"] == "daily"
    assert (ROOT / "uv.lock").is_file()
    workflows = (ROOT / ".github" / "workflows").is_dir()
    assert ("github-actions" in ecosystems) == workflows
    assert set(ecosystems) <= {"uv", "github-actions"}
