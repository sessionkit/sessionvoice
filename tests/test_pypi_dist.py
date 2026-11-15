"""`make pypi-dist` builds what `make publish` uploads, with no relative link (EARS-534).

The repo's README links into the repo by relative path (tests/test_readme_links.py), and
PyPI renders the README with no repo, so each such link 404s on the package page. The fix
is in the release path, not the README: `make pypi-dist` archives the release TAG into a
stage, has the kit's ONE scripts/pypi_readme.py (sessionkit SK-140) pin the stage's links
to the public tag, builds there, then checks both dists. `make publish` depends on it.

Every test here runs OFFLINE. The repo is cloned into tmp and tagged there; PUBLIC_REMOTE
is a local bare repo holding that tag, so `git ls-remote` never leaves the machine. uv
builds with no build isolation, on this interpreter's setuptools (the dev group), so
nothing is fetched. The Makefile under test is the checkout's (`make -f`), run in the clone.

- The wiring tests need no kit: PYPI_README is a stub that records its argv. Its
  `tag-check` passes, or refuses when STUB_TAG_CHECK_FAIL holds a message.
- The refusals: no SESSIONKIT_DIR, or one without the script, is exit 2 naming the
  variable; a tag missing here is exit 1; a refusing tag-check is exit 1 and stops the
  build before the rewrite; none of them builds. (make itself exits 2 for any failed
  recipe; the recipe's code is in its `Error N` line.)
- The tag-check tests run the kit's REAL `tag-check` (the stub hands that verb to the
  script named by STUB_KIT, and stubs the rest, so nothing is built): a tag moved here
  after PUBLIC_REMOTE got it is refused, naming both shas; a tag missing there is
  refused; the same commit passes, lightweight or annotated. They skip unless
  SESSIONKIT_DIR names a sessionkit checkout whose script has `tag-check`.
- test_real_script_leaves_no_relative_link runs the kit's real script and reads both
  dists; it skips unless SESSIONKIT_DIR names a sessionkit checkout that has it.
"""
from __future__ import annotations

import email.parser
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from tests.md_links import is_local, links

ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = ROOT / "Makefile"
SCRIPT = Path("scripts") / "pypi_readme.py"
# Set by a runner that has the kit; the fence leaves it alone. Read once, at import.
KIT = os.environ.get("SESSIONKIT_DIR", "")
KIT_SCRIPT = Path(KIT) / SCRIPT if KIT else None
HAS_TAG_CHECK = bool(KIT_SCRIPT and KIT_SCRIPT.is_file()
                     and "tag-check" in KIT_SCRIPT.read_text(encoding="utf-8"))

STUB = '''\
import json, os, subprocess, sys
argv = sys.argv[1:]
row = {"argv": argv}
if argv[:1] == ["tag-check"]:
    with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\\n")
    if os.environ.get("STUB_KIT"):
        sys.exit(subprocess.run([sys.executable, os.environ["STUB_KIT"], *argv]).returncode)
    if os.environ.get("STUB_TAG_CHECK_FAIL"):
        print(os.environ["STUB_TAG_CHECK_FAIL"], file=sys.stderr)
        sys.exit(1)
    sys.exit(0)
if argv[:1] == ["rewrite"]:
    readme = argv[-1]
    row["readme"] = open(readme, encoding="utf-8").read()
    row["root_files"] = sorted(os.listdir(argv[argv.index("--root") + 1]))
else:
    row["exists"] = [os.path.isfile(p) for p in argv[1:]]
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps(row) + "\\n")
'''

MARK = "UNTAGGED-EDIT-EARS-534"


def _git(*args, cwd, check=True):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True,
                          check=check)


@pytest.fixture(scope="module")
def rig(tmp_path_factory):
    """A clone of the repo with tag v<version>, a bare PUBLIC_REMOTE holding the tag, and
    one commit on top of the tag that pypi-dist must NOT build (it builds the tag)."""
    for tool in ("git", "make", "uv"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} is not on PATH")
    top = _git("rev-parse", "--show-toplevel", cwd=ROOT, check=False)
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != ROOT:
        pytest.skip("not a git checkout")
    base = tmp_path_factory.mktemp("pypi-dist")
    clone, public = base / "clone", base / "public.git"
    _git("clone", "-q", "--no-tags", str(ROOT), str(clone), cwd=base)
    version = re.search(r'^version = "([^"]+)"', (clone / "pyproject.toml").read_text(),
                        re.M).group(1)
    tag = f"v{version}"
    _git("tag", tag, cwd=clone)
    _git("init", "-q", "--bare", str(public), cwd=base)
    _git("push", "-q", str(public), f"refs/tags/{tag}", cwd=clone)
    # The checkout moves on past the tag: what is built must still be the tag's README.
    with open(clone / "README.md", "a", encoding="utf-8") as f:
        f.write(f"\n{MARK}\n")
    _git("-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "commit", "-q", "-am", "after the tag", cwd=clone)
    return {"clone": clone, "public": public, "version": version, "tag": tag, "base": base}


def _make(rig, *args, extra=None, drop=()):
    """`make -f <checkout Makefile> ARGS` in the clone, offline. dist/ starts empty."""
    shutil.rmtree(rig["clone"] / "dist", ignore_errors=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "MAKE", "MFLAGS"))}
    for k in ("SESSIONKIT_DIR", "PYPI_README", "PUBLIC_REMOTE", "UV_PUBLISH_TOKEN",
              "PYPI_TOKEN_PASS_PATH", "STUB_KIT", "STUB_TAG_CHECK_FAIL", *drop):
        env.pop(k, None)
    env.update(UV_OFFLINE="1", UV_NO_BUILD_ISOLATION="1", UV_PYTHON=sys.executable,
               UV_CACHE_DIR=str(rig["base"] / "uv-cache"), STUB_LOG=str(rig["base"] / "stub.log"))
    env.update(extra or {})
    return subprocess.run(["make", "-s", "-f", str(MAKEFILE), *args], cwd=rig["clone"],
                          env=env, capture_output=True, text=True, timeout=600)


def _stub(rig, tmp_path) -> str:
    stub = tmp_path / "stub_pypi_readme.py"
    stub.write_text(STUB)
    log = rig["base"] / "stub.log"
    log.unlink(missing_ok=True)
    return f"{sys.executable} {stub}"


def _rows(rig) -> list[dict]:
    log = rig["base"] / "stub.log"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines()]


def _refused(p, code: int) -> bool:
    """make exits 2 whenever a recipe fails; the recipe's own exit is in its Error line."""
    return p.returncode == 2 and f"[pypi-dist] Error {code}" in p.stderr


def _dists(rig) -> tuple[Path, Path]:
    dist, stem = rig["clone"] / "dist", f"sessionvoice-{rig['version']}"
    return dist / f"{stem}-py3-none-any.whl", dist / f"{stem}.tar.gz"


# --- the wiring, with no kit -------------------------------------------------------------

def test_pypi_dist_rewrites_the_tags_readme_then_checks_both_dists(rig, tmp_path):
    p = _make(rig, "pypi-dist", f"PUBLIC_REMOTE={rig['public']}",
              f"PYPI_README={_stub(rig, tmp_path)}")
    assert p.returncode == 0, p.stdout + p.stderr
    tag_check, rewrite, check = _rows(rig)
    tag = rig["tag"]

    # tag-check first: this version's tag, against PUBLIC_REMOTE, before anything builds.
    assert tag_check["argv"] == ["tag-check", "--tag", tag, "--remote", str(rig["public"])]

    # rewrite: this repo, the tag, the stage's README, in place.
    argv = rewrite["argv"]
    assert argv[:5] == ["rewrite", "--repo", "sessionkit/sessionvoice", "--ref", tag], argv
    assert argv[5] == "--root" and argv[7] == str(Path(argv[6]) / "README.md"), argv
    stage = Path(argv[6])
    assert not str(stage).startswith(str(rig["clone"])), "the stage is the checkout"
    assert "pyproject.toml" in rewrite["root_files"], rewrite["root_files"]
    # The stage is the TAG's tree: the commit made after the tag is not in it.
    assert MARK not in rewrite["readme"]
    assert MARK in (rig["clone"] / "README.md").read_text()
    # ...and it is gone once the target ends.
    assert not stage.exists()

    # check: this version's wheel and sdist, both built.
    whl, sdist = _dists(rig)
    assert check["argv"] == ["check", f"dist/{whl.name}", f"dist/{sdist.name}"], check["argv"]
    assert check["exists"] == [True, True]
    assert whl.is_file() and sdist.is_file()


def test_publish_builds_through_pypi_dist(rig):
    """publish's prerequisite is pypi-dist: with no SESSIONKIT_DIR it refuses there, exit 2,
    before any upload. (No token is set either, so nothing could reach PyPI.)"""
    p = _make(rig, "publish", f"PUBLIC_REMOTE={rig['public']}")
    assert _refused(p, 2), p.stdout + p.stderr
    assert "pypi-dist: set SESSIONKIT_DIR" in p.stderr
    assert "publish:" not in p.stderr
    assert not (rig["clone"] / "dist").exists()


def test_build_is_not_what_publish_uploads():
    """`make build` stays the plain checkout build; publish must not depend on it."""
    text = MAKEFILE.read_text()
    assert re.search(r"^publish: pypi-dist\b", text, re.M), "publish must depend on pypi-dist"
    assert not re.search(r"^publish:[^#\n]*\bbuild\b", text, re.M)
    assert "PUBLIC_REMOTE ?= https://github.com/sessionkit/sessionvoice.git" in text


# --- the refusals -------------------------------------------------------------------------

def test_no_sessionkit_dir_refuses_naming_it(rig):
    p = _make(rig, "pypi-dist", f"PUBLIC_REMOTE={rig['public']}")
    assert _refused(p, 2), p.stdout + p.stderr
    assert "set SESSIONKIT_DIR" in p.stderr
    assert not (rig["clone"] / "dist").exists()


def test_sessionkit_dir_without_the_script_refuses(rig, tmp_path):
    p = _make(rig, "pypi-dist", f"PUBLIC_REMOTE={rig['public']}",
              extra={"SESSIONKIT_DIR": str(tmp_path)})
    assert _refused(p, 2), p.stdout + p.stderr
    assert f"SESSIONKIT_DIR={tmp_path} has no scripts/pypi_readme.py" in p.stderr
    assert not (rig["clone"] / "dist").exists()


def test_sessionkit_dir_names_the_kits_script(rig, tmp_path):
    """SESSIONKIT_DIR is all a release needs: PYPI_README defaults to its script."""
    kit = tmp_path / "kit"
    (kit / "scripts").mkdir(parents=True)
    (kit / SCRIPT).write_text(STUB)
    _stub(rig, tmp_path)   # clears the log
    p = _make(rig, "pypi-dist", f"PUBLIC_REMOTE={rig['public']}",
              extra={"SESSIONKIT_DIR": str(kit)})
    assert p.returncode == 0, p.stdout + p.stderr
    assert [r["argv"][0] for r in _rows(rig)] == ["tag-check", "rewrite", "check"]


def test_a_refusing_tag_check_stops_the_build(rig, tmp_path):
    """tag-check's exit is the recipe's: a refusal is exit 1, its words reach the user,
    and nothing after it runs (no rewrite, no build, no check)."""
    why = "stub tag-check: tag differs: local aaaa, public bbbb"
    p = _make(rig, "pypi-dist", f"PUBLIC_REMOTE={rig['public']}",
              f"PYPI_README={_stub(rig, tmp_path)}", extra={"STUB_TAG_CHECK_FAIL": why})
    assert _refused(p, 1), p.stdout + p.stderr
    assert why in p.stderr
    assert [r["argv"][0] for r in _rows(rig)] == ["tag-check"]
    assert not (rig["clone"] / "dist").exists()


def test_the_bare_existence_check_is_gone():
    """The recipe no longer asks `git ls-remote` only whether the tag exists: that passed
    a tag moved here after the public repo got it. tag-check compares the commits."""
    text = MAKEFILE.read_text()
    assert "ls-remote" not in text
    assert re.search(r'\$\(PYPI_README\) tag-check --tag "\$\$v" --remote "\$\(PUBLIC_REMOTE\)"',
                     text)


def test_tag_missing_here_refuses(rig, tmp_path):
    clone, tag = rig["clone"], rig["tag"]
    sha = _git("rev-parse", tag, cwd=clone).stdout.strip()
    _git("tag", "-d", tag, cwd=clone)
    try:
        p = _make(rig, "pypi-dist", f"PUBLIC_REMOTE={rig['public']}",
                  f"PYPI_README={_stub(rig, tmp_path)}")
    finally:
        _git("tag", tag, sha, cwd=clone)
    assert _refused(p, 1), p.stdout + p.stderr
    assert f"no tag {tag} here" in p.stderr
    assert _rows(rig) == [] and not (clone / "dist").exists()


# --- the kit's real tag-check --------------------------------------------------------------

needs_tag_check = pytest.mark.skipif(
    not HAS_TAG_CHECK,
    reason="set SESSIONKIT_DIR to a sessionkit checkout whose scripts/pypi_readme.py has tag-check")


def _kit_make(rig, tmp_path, public):
    """pypi-dist with the kit's real tag-check and the stub for the rest (nothing builds)."""
    return _make(rig, "pypi-dist", f"PUBLIC_REMOTE={public}",
                 f"PYPI_README={_stub(rig, tmp_path)}", extra={"STUB_KIT": str(KIT_SCRIPT)})


@needs_tag_check
def test_tag_moved_here_refuses_naming_both_shas(rig, tmp_path):
    """The public repo got the tag; then it was moved here to another commit. The name
    exists on both sides, which is all the old check asked: tag-check refuses."""
    clone, tag = rig["clone"], rig["tag"]
    public_sha = _git("rev-parse", f"{tag}^{{commit}}", cwd=clone).stdout.strip()
    moved_sha = _git("rev-parse", "HEAD", cwd=clone).stdout.strip()
    assert moved_sha != public_sha
    _git("tag", "-f", tag, moved_sha, cwd=clone)
    try:
        p = _kit_make(rig, tmp_path, rig["public"])
    finally:
        _git("tag", "-f", tag, public_sha, cwd=clone)
    assert _refused(p, 1), p.stdout + p.stderr
    assert f"tag {tag} differs" in p.stderr
    assert moved_sha in p.stderr and public_sha in p.stderr, p.stderr
    assert [r["argv"][0] for r in _rows(rig)] == ["tag-check"]
    assert not (clone / "dist").exists()


@needs_tag_check
def test_tag_missing_on_public_remote_refuses(rig, tmp_path):
    empty = tmp_path / "empty.git"
    _git("init", "-q", "--bare", str(empty), cwd=tmp_path)
    p = _kit_make(rig, tmp_path, empty)
    assert _refused(p, 1), p.stdout + p.stderr
    assert f"tag {rig['tag']} is not on {empty}" in p.stderr
    assert [r["argv"][0] for r in _rows(rig)] == ["tag-check"]
    assert not (rig["clone"] / "dist").exists()


@needs_tag_check
def test_same_commit_passes_the_gate(rig, tmp_path):
    p = _kit_make(rig, tmp_path, rig["public"])
    assert p.returncode == 0, p.stdout + p.stderr
    sha = _git("rev-parse", f"{rig['tag']}^{{commit}}", cwd=rig["clone"]).stdout.strip()
    assert f"{rig['tag']} is {sha} here and on {rig['public']}" in p.stdout
    assert [r["argv"][0] for r in _rows(rig)] == ["tag-check", "rewrite", "check"]


@needs_tag_check
def test_annotated_tag_at_the_same_commit_passes_the_gate(rig, tmp_path):
    """`make release` makes an annotated tag: the public side lists the tag object and
    its peeled commit. Same commit both sides is a pass, not a mismatch."""
    clone, tag = rig["clone"], rig["tag"]
    sha = _git("rev-parse", f"{tag}^{{commit}}", cwd=clone).stdout.strip()
    public = tmp_path / "annotated.git"
    _git("init", "-q", "--bare", str(public), cwd=tmp_path)
    _git("-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "tag", "-f", "-a", tag, sha, "-m", f"sessionvoice {tag}", cwd=clone)
    try:
        _git("push", "-q", str(public), f"refs/tags/{tag}", cwd=clone)
        p = _kit_make(rig, tmp_path, public)
    finally:
        _git("tag", "-f", tag, sha, cwd=clone)
    assert p.returncode == 0, p.stdout + p.stderr
    assert f"{tag} is {sha} here and on {public}" in p.stdout


# --- the kit's real script ----------------------------------------------------------------

def _description(dist: Path) -> str:
    if dist.suffix == ".whl":
        with zipfile.ZipFile(dist) as z:
            raw = z.read(next(n for n in z.namelist() if n.endswith(".dist-info/METADATA")))
    else:
        with tarfile.open(dist) as t:
            raw = t.extractfile(next(m for m in t.getmembers()
                                     if m.name.count("/") == 1
                                     and m.name.endswith("/PKG-INFO"))).read()
    return email.parser.Parser().parsestr(raw.decode("utf-8")).get_payload()


@pytest.mark.skipif(not (KIT and (Path(KIT) / SCRIPT).is_file()),
                    reason="set SESSIONKIT_DIR to a sessionkit checkout with scripts/pypi_readme.py")
def test_real_script_leaves_no_relative_link(rig):
    p = _make(rig, "pypi-dist", f"PUBLIC_REMOTE={rig['public']}",
              extra={"SESSIONKIT_DIR": KIT})
    assert p.returncode == 0, p.stdout + p.stderr
    if HAS_TAG_CHECK:   # the real tag-check ran first, and passed
        assert f"tag-check: {rig['tag']} is " in p.stdout, p.stdout
    tagged = _git("show", f"{rig['tag']}:README.md", cwd=rig["clone"]).stdout
    relative = [t for t in links(tagged) if is_local(t)]
    assert relative, "the tag's README has no relative link: this test would prove nothing"
    pinned = re.compile(r"https://(?:github\.com/sessionkit/sessionvoice/(?:blob|tree)"
                        r"|raw\.githubusercontent\.com/sessionkit/sessionvoice)/"
                        + re.escape(rig["tag"]) + "/")
    for dist in _dists(rig):
        text = _description(dist)
        assert MARK not in text, f"{dist.name}: built from the checkout, not the tag"
        left = [t for t in links(text) if is_local(t)]
        assert left == [], f"{dist.name}: relative links left: {left}"
        pins = [t for t in links(text) if pinned.match(t)]
        assert len(pins) >= len(relative), (dist.name, len(pins), len(relative))
    print(f"\n{len(relative)} relative links in {rig['tag']}'s README; "
          f"0 left in either dist; all pinned to {rig['tag']}")
