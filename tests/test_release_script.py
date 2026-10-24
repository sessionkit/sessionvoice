"""scripts/release.py cuts a release in the family's shape, and the Makefile drives it.

Every case runs the real script, by path, in a throwaway git repo under tmp_path: a
minimal pyproject.toml named sessionvoice, its uv.lock, COPIES of this checkout's two
plugin manifests (so the mirror is tested on their real layout), a CHANGELOG.md with an
``[Unreleased]`` section, and a bare ``origin`` the script must never push to.

The child is hermetic: git reads no system or global config, uv runs offline with its
cache, HOME and Python all pinned (UV_NO_SYNC keeps it from building a venv), so nothing
under the real home is read or written and nothing reaches the network.

Pinned here:

- ``--dry-run`` says what it would do and changes nothing: no file, no commit, no tag.
- A release sets ONE version in pyproject.toml through uv and mirrors it, textually,
  into plugin.json and marketplace.json; uv.lock follows. The version-agreement guard
  (tests/test_version_sync.py) passes on the result.
- CHANGELOG.md's ``[Unreleased]`` becomes ``[X.Y.Z] - today`` with a fresh, empty
  ``[Unreleased]`` above it.
- One commit, ``chore: release vX.Y.Z``, holding exactly the release files; an annotated
  tag ``vX.Y.Z`` on it; the push line printed and nothing pushed.
- The refusals: a dirty tree, a missing CHANGELOG.md, a CHANGELOG.md with no
  ``[Unreleased]``, a tag that already exists, a spec that is not patch|minor|major or
  X.Y.Z. Each one exits non-zero, names its reason and leaves the repo as it found it.
- The Makefile's release, push, publish and build targets, each with a ``##`` help line;
  publish takes the token from the password store or the environment, never argv.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from tests import test_version_sync as version_guard
from tests._anchor import ANCHOR

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release.py"
LANDED = f"{ANCHOR:%Y-%m-%d}"  # the synthetic date of the fixture's landed release

needs_uv = pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not on PATH")

PYPROJECT = """\
[project]
name = "sessionvoice"
version = "0.2.1"
requires-python = ">=3.10"
dependencies = []
"""

CHANGELOG = f"""\
# Changelog

All notable changes are documented here.

## [Unreleased]

### Added

- A line the next release ships.

## [0.2.1] - {LANDED}

### Added

- A line that already shipped.
"""


# --- a hermetic repo ------------------------------------------------------------


def child_env(home: Path) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not (k.startswith(("GIT_", "UV_")) or k in ("VIRTUAL_ENV", "PYTHONPATH"))
    }
    env.update(
        HOME=str(home),
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@example.invalid",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@example.invalid",
        UV_CACHE_DIR=str(home / "uv-cache"),
        UV_OFFLINE="1",
        UV_NO_SYNC="1",
        UV_NO_CONFIG="1",
        UV_PYTHON=sys.executable,
        UV_PYTHON_DOWNLOADS="never",
    )
    return env


class Repo:
    def __init__(self, path: Path, env: dict[str, str]):
        self.path, self.env = path, env

    def run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        out = subprocess.run(args, cwd=self.path, env=self.env, capture_output=True, text=True, timeout=120)
        if check and out.returncode != 0:
            raise AssertionError(f"{' '.join(args)} exited {out.returncode}:\n{out.stdout}\n{out.stderr}")
        return out

    def git(self, *args: str, check: bool = True) -> str:
        return self.run("git", *args, check=check).stdout.strip()

    def release(self, *args: str) -> subprocess.CompletedProcess:
        return self.run(sys.executable, str(SCRIPT), *args, check=False)

    def read(self, name: str) -> str:
        return (self.path / name).read_text()

    def commit_all(self, message: str) -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def snapshot(self) -> dict:
        """Everything a release could change: each file's bytes (tracked or not, .git
        aside), the status, HEAD, the tags and what origin holds."""
        files = {
            str(p.relative_to(self.path)): p.read_bytes()
            for p in sorted(self.path.rglob("*"))
            if p.is_file() and ".git" not in p.relative_to(self.path).parts
        }
        return {
            "files": files,
            "status": self.git("status", "--porcelain"),
            "head": self.git("rev-parse", "HEAD"),
            "tags": self.git("tag", "-l"),
            "origin": self.git("ls-remote", "origin"),
        }


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    home = tmp_path / "home"
    home.mkdir()
    work = tmp_path / "work"
    (work / ".claude-plugin").mkdir(parents=True)
    r = Repo(work, child_env(home))
    (work / "pyproject.toml").write_text(PYPROJECT)
    (work / "CHANGELOG.md").write_text(CHANGELOG)
    (work / ".gitignore").write_text(".venv/\n")
    for name in ("plugin.json", "marketplace.json"):
        text = (ROOT / ".claude-plugin" / name).read_text()
        current = version_guard.pyproject_version()
        assert f'"{current}"' in text, f"{name} does not state {current}; the fixture expects it to"
        (work / ".claude-plugin" / name).write_text(text.replace(f'"{current}"', '"0.2.1"'))
    r.run("uv", "lock")
    r.git("init", "-q", "-b", "master")
    r.commit_all("init")
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], env=r.env, check=True, capture_output=True)
    r.git("remote", "add", "origin", str(bare))
    return r


def today_window(run) -> tuple[set[str], subprocess.CompletedProcess]:
    """The local date before and after ``run()``, so a run across midnight still matches."""
    before = datetime.now().astimezone().date().isoformat()
    out = run()
    after = datetime.now().astimezone().date().isoformat()
    return {before, after}, out


def versions(r: Repo) -> dict[str, str]:
    """The version-agreement guard's own readers, pointed at the temp repo."""
    plugin = json.loads(r.read(".claude-plugin/plugin.json"))["version"]
    return {
        "pyproject.toml": version_guard.tomllib.loads(r.read("pyproject.toml"))["project"]["version"],
        "plugin.json": plugin,
        **{k.split(" ", 1)[1]: v for k, v in _market(r).items()},
        "uv.lock": next(
            p["version"]
            for p in version_guard.tomllib.loads(r.read("uv.lock"))["package"]
            if p["name"] == "sessionvoice"
        ),
    }


def _market(r: Repo) -> dict[str, str]:
    data = json.loads(r.read(".claude-plugin/marketplace.json"))
    found = {"marketplace.json metadata.version": data["metadata"]["version"]}
    for entry in data["plugins"]:
        if entry.get("name") == "sessionvoice" and "version" in entry:
            found["marketplace.json plugins[sessionvoice].version"] = entry["version"]
    return found


# --- dry run ----------------------------------------------------------------------


@needs_uv
@pytest.mark.parametrize("spec, want", [("0.3.0", "0.3.0"), ("minor", "0.3.0"), ("patch", "0.2.2")])
def test_dry_run_says_what_it_would_do_and_changes_nothing(repo, spec, want):
    before = repo.snapshot()
    out = repo.release(spec, "--dry-run")
    assert out.returncode == 0, out.stderr
    assert f"would release v{want}. Push is yours:" in out.stdout
    assert f".claude-plugin/plugin.json: version -> {want}" in out.stdout
    assert f".claude-plugin/marketplace.json: version -> {want}" in out.stdout
    assert f"CHANGELOG.md: [Unreleased] -> [{want}]" in out.stdout
    assert f"$ git tag -a v{want} -m sessionvoice v{want}" in out.stdout
    assert repo.snapshot() == before


@needs_uv
def test_current_prints_the_pyproject_version(repo):
    out = repo.release("--current")
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1] == "0.2.1"


# --- a release --------------------------------------------------------------------


@needs_uv
def test_release_sets_one_version_and_mirrors_it(repo, monkeypatch):
    plugin_before = repo.read(".claude-plugin/plugin.json")
    market_before = repo.read(".claude-plugin/marketplace.json")
    days, out = today_window(lambda: repo.release("0.3.0"))
    assert out.returncode == 0, out.stderr

    assert set(versions(repo).values()) == {"0.3.0"}, versions(repo)
    # the mirror is textual: the version is the one thing that changed in each manifest
    assert repo.read(".claude-plugin/plugin.json") == plugin_before.replace('"0.2.1"', '"0.3.0"')
    assert repo.read(".claude-plugin/marketplace.json") == market_before.replace('"0.2.1"', '"0.3.0"')
    # the version-agreement guard itself, run over the released repo
    monkeypatch.setattr(version_guard, "ROOT", repo.path)
    version_guard.test_all_manifests_state_one_version()

    log = repo.read("CHANGELOG.md")
    assert any(
        f"## [Unreleased]\n\n## [0.3.0] - {day}\n\n### Added\n\n- A line the next release ships.\n" in log
        for day in days
    ), log
    assert log.count("## [Unreleased]") == 1
    assert f"## [0.2.1] - {LANDED}" in log


@needs_uv
def test_release_commits_the_files_by_name_tags_and_never_pushes(repo):
    (repo.path / "notes.txt").write_text("tracked, and no part of a release\n")
    repo.commit_all("notes")
    out = repo.release("0.3.0")
    assert out.returncode == 0, out.stderr

    assert repo.git("status", "--porcelain") == ""
    assert repo.git("log", "-1", "--format=%s") == "chore: release v0.3.0"
    changed = set(repo.git("show", "--name-only", "--format=", "HEAD").splitlines())
    assert changed == {
        "pyproject.toml",
        "uv.lock",
        "CHANGELOG.md",
        ".claude-plugin/plugin.json",
        ".claude-plugin/marketplace.json",
    }
    assert repo.git("cat-file", "-t", "v0.3.0") == "tag"  # annotated, not lightweight
    assert repo.git("rev-parse", "v0.3.0^{commit}") == repo.git("rev-parse", "HEAD")
    assert repo.git("tag", "-l", "--format=%(contents:subject)", "v0.3.0") == "sessionvoice v0.3.0"

    assert out.stdout.rstrip().endswith("released v0.3.0. Push is yours:\n  git push && git push --tags")
    assert repo.git("ls-remote", "origin") == ""


@needs_uv
def test_a_bump_moves_from_the_current_version(repo):
    out = repo.release("patch")
    assert out.returncode == 0, out.stderr
    assert set(versions(repo).values()) == {"0.2.2"}
    assert repo.git("cat-file", "-t", "v0.2.2") == "tag"


# --- the refusals -----------------------------------------------------------------


def _untracked(r: Repo) -> None:
    (r.path / "stray.txt").write_text("not committed\n")


def _modified(r: Repo) -> None:
    (r.path / "CHANGELOG.md").write_text(r.read("CHANGELOG.md") + "\n- an edit not committed\n")


def _no_changelog(r: Repo) -> None:
    r.git("rm", "-q", "CHANGELOG.md")
    r.git("commit", "-q", "-m", "drop the changelog")


def _no_unreleased(r: Repo) -> None:
    (r.path / "CHANGELOG.md").write_text(r.read("CHANGELOG.md").replace("## [Unreleased]", "## [Next]"))
    r.commit_all("no unreleased section")


def _tag_exists(r: Repo) -> None:
    r.git("tag", "-a", "v0.3.0", "-m", "already cut")


REFUSALS = [
    pytest.param(_untracked, "0.3.0", "the working tree is not clean", id="dirty-untracked"),
    pytest.param(_modified, "0.3.0", "the working tree is not clean", id="dirty-modified"),
    pytest.param(_no_changelog, "0.3.0", "CHANGELOG.md not found", id="no-changelog"),
    pytest.param(_no_unreleased, "0.3.0", "has no '## [Unreleased]' section", id="no-unreleased"),
    pytest.param(_tag_exists, "0.3.0", "tag v0.3.0 already exists", id="tag-exists"),
    pytest.param(_tag_exists, "minor", "tag v0.3.0 already exists", id="tag-exists-by-bump"),
    pytest.param(None, "0.3", "is not patch|minor|major or X.Y.Z", id="bad-spec"),
]


@needs_uv
@pytest.mark.parametrize("dry", [False, True], ids=["real", "dry-run"])
@pytest.mark.parametrize("setup, spec, reason", REFUSALS)
def test_each_refusal_names_its_reason_and_writes_nothing(repo, setup, spec, reason, dry):
    if setup:
        setup(repo)
    before = repo.snapshot()
    out = repo.release(spec, *(["--dry-run"] if dry else []))
    assert out.returncode != 0
    assert reason in out.stderr
    assert repo.snapshot() == before


@needs_uv
def test_a_manifest_with_no_version_field_is_refused_before_any_write(repo):
    path = repo.path / ".claude-plugin" / "plugin.json"
    data = json.loads(path.read_text())
    del data["version"]
    path.write_text(json.dumps(data, indent=2) + "\n")
    repo.commit_all("no version in the plugin manifest")
    before = repo.snapshot()
    out = repo.release("0.3.0")
    assert out.returncode != 0
    assert "no version field in .claude-plugin/plugin.json" in out.stderr
    assert repo.snapshot() == before


# --- the Makefile -----------------------------------------------------------------

needs_make = pytest.mark.skipif(shutil.which("make") is None, reason="make is not on PATH")


def make_n(*args: str) -> str:
    out = subprocess.run(["make", "-n", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_each_release_target_has_a_help_line():
    makefile = (ROOT / "Makefile").read_text().splitlines()
    for target in ("build", "release", "push", "publish"):
        lines = [line for line in makefile if line.startswith(f"{target}:")]
        assert len(lines) == 1, f"Makefile has {len(lines)} '{target}:' rules"
        assert "  ## " in lines[0], f"'{target}:' has no '## ' help line: {lines[0]}"


@needs_make
@pytest.mark.parametrize(
    "args, want",
    [((), "scripts/release.py patch"), (("BUMP=minor",), "scripts/release.py minor"),
     (("VERSION=0.3.0",), "scripts/release.py 0.3.0"), (("BUMP=minor", "VERSION=0.3.0"), "scripts/release.py 0.3.0")],
)
def test_make_release_passes_bump_or_version(args, want):
    assert make_n("release", *args).strip() == f"uv run python {want}"


@needs_make
def test_make_push_pushes_the_branch_and_the_tags():
    assert make_n("push").strip() == "git push origin HEAD && git push origin --tags"


@needs_make
def test_make_publish_keeps_the_token_off_every_command_line():
    recipe = make_n("publish")
    assert "uv build" in recipe
    uploads = [part for part in recipe.split(";") if "uv publish" in part]
    assert uploads, recipe
    for part in uploads:
        # the token travels in uv's environment, never as a flag or an argument
        assert "--token" not in part and "-t " not in part and "--password" not in part, part
        assert 'uv publish "$dist"*' in part, part
    assert 'UV_PUBLISH_TOKEN="$(pass show "$PYPI_TOKEN_PASS_PATH" | head -n1)" uv publish' in recipe
    assert "dist/$(echo 'sessionvoice' | tr '-' '_')-$(uv version --short)" in recipe
