"""EARS-363: `ears-arm` and `ears-count` are on PATH by bare name, through the plugin's
bin/. Each is a shim in the bin/ears pattern that execs the one implementation in
skills/ears/scripts/, which stays where it is and still works by its old path.

Every run is fenced: HOME and XDG_STATE_HOME are under tmp_path, EARS_HOME is unset,
and PATH holds only the directories a test names plus /usr/bin:/bin. A test that
could reach the arm puts a stub heard-first ahead of bin/, so nothing here can arm a
real waiter or touch a real ~/.ears. ears-count is a read-only ps scan; its seat is a
name no real seat holds, or a fake `ps` answers it."""
import os, shutil, subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
SCRIPTS = ROOT / "skills" / "ears" / "scripts"
SHIMS = ("ears-arm", "ears-count")
# A seat name no real seat holds, so a real ps scan finds no waiter.
SEAT = "ears363probe"


@pytest.fixture
def fence(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    state = tmp_path / "state"
    base = {k: v for k, v in os.environ.items() if k not in ("EARS_HOME", "EARS_SPEAKERS")}
    base.update(HOME=str(home), XDG_STATE_HOME=str(state))

    def env(*front):
        return {**base, "PATH": os.pathsep.join([*map(str, front), "/usr/bin", "/bin"])}

    def run(argv, *front):
        return subprocess.run(argv, capture_output=True, text=True, env=env(*front),
                              timeout=30)

    def created():
        """Everything a run left under the fenced HOME and state dir."""
        return sorted(str(p.relative_to(tmp_path)) for d in (home, state) if d.exists()
                      for p in d.rglob("*")) + ([str(state)] if state.exists() else [])

    run.env, run.home, run.created, run.tmp = env, home, created, tmp_path
    return run


def stub(dirpath, name, body):
    dirpath.mkdir(exist_ok=True)
    p = dirpath / name
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(0o755)
    return p


# -- the shims themselves ----------------------------------------------------------

@pytest.mark.parametrize("name", SHIMS)
def test_shim_exists_is_executable_and_is_not_a_symlink(name):
    shim = BIN / name
    assert shim.is_file(), f"bin/{name} is missing"
    assert not shim.is_symlink(), f"bin/{name} must be a shim, not a symlink"
    assert os.access(shim, os.X_OK), f"bin/{name} is not executable"
    assert (SCRIPTS / name).is_file() and os.access(SCRIPTS / name, os.X_OK), \
        f"skills/ears/scripts/{name} must stay in place"


@pytest.mark.parametrize("name", SHIMS)
def test_shim_follows_the_bin_ears_pattern_and_execs_the_script(name):
    """The bin/ears pattern, and one implementation: the shim execs the skill's script
    and carries none of its body."""
    text = (BIN / name).read_text()
    lines = text.splitlines()
    assert lines[0] == "#!/usr/bin/env bash"
    assert lines[1].startswith(f"# bin/{name} - ")
    assert "added to the Bash tool's PATH" in " ".join(l.lstrip("# ") for l in lines)
    assert "set -euo pipefail" in lines
    assert 'here="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"' in lines
    assert 'root="$(cd "$here/.." && pwd)"' in lines
    assert lines[-1] == f'exec "$root/skills/ears/scripts/{name}" "$@"'
    # None of the script's code is in the shim (a copy would drift).
    code = [l for l in (SCRIPTS / name).read_text().splitlines()
            if l.strip() and not l.lstrip().startswith("#") and l != "set -euo pipefail"]
    assert not [l for l in code if l in lines], "the shim copies the script's code"
    assert len(lines) < 15


# -- by bare name, from the plugin's bin/ on PATH ----------------------------------

@pytest.mark.parametrize("name", SHIMS)
def test_shim_resolves_by_bare_name_from_bin(fence, name):
    found = shutil.which(name, path=fence.env(BIN)["PATH"])
    assert found is not None, f"{name} is not on PATH from bin/"
    assert Path(found) == BIN / name


def test_ears_arm_help_by_bare_name(fence):
    r = fence(["ears-arm", "--help"], BIN)
    assert r.returncode == 0, r.stderr
    assert "ears-arm <seat> [talk|alone|room]" in r.stdout
    assert "talk  :" in r.stdout and "room  :" in r.stdout
    assert "set -euo" not in r.stdout          # the script's header, not the shim's
    assert "bin/ears-arm - " not in r.stdout   # $0 is the script, not the shim
    assert r.stderr == ""
    assert fence.created() == []


def test_ears_count_by_bare_name_prints_zero(fence):
    r = fence(["ears-count", SEAT], BIN)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "0\n"
    assert fence.created() == []


def test_ears_arm_dry_run_by_bare_name_creates_nothing(fence):
    r = fence(["ears-arm", SEAT, "talk", "--dry-run"], BIN)
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("heard-first --source mic")
    assert f"cut:{SEAT}" in r.stdout
    assert fence.created() == []


def test_ears_arm_through_the_shim_still_finds_ears_count_beside_it(fence):
    """ears-arm runs `$(dirname "$0")/ears-count`. Through the shim's exec, $0 is the
    script's own path, so that lookup holds. A fake ps reports one waiter for SEAT:
    ears-arm refuses to arm a second one (exit 2), which it can only do if it found
    ears-count and ears-count read the fake. The stub heard-first, ahead of bin/,
    proves no arm ran."""
    stubs = fence.tmp / "stubs"
    stub(stubs, "ps", f'echo "  4242 00:01 /x/bin/heard-first --rule cut:{SEAT} --rule turn"\n')
    ran = fence.tmp / "heard-first-ran"
    stub(stubs, "heard-first", f'printf "%s\\n" "$*" > "{ran}"\nexit 99\n')
    r = fence(["ears-arm", SEAT, "talk"], stubs, BIN)
    assert r.returncode == 2, (r.returncode, r.stderr)
    assert f"1 waiter(s) already up for {SEAT}" in r.stderr
    assert not ran.exists(), "heard-first was exec'd"


def test_ears_arm_through_the_shim_reaches_the_arm(fence):
    """The control: with no waiter up, the shim's ears-arm makes its home and execs
    heard-first, here the stub ahead of bin/. So the empty results above are not a
    dead fence."""
    stubs = fence.tmp / "stubs"
    ran = fence.tmp / "heard-first-ran"
    stub(stubs, "heard-first", f'printf "%s\\n" "$*" > "{ran}"\nexit 99\n')
    r = fence(["ears-arm", SEAT, "talk"], stubs, BIN)
    assert r.returncode == 99, r.stderr
    assert f"--state {fence.home}/.ears/heard.state" in ran.read_text()


# -- through a symlink elsewhere (readlink -f) -------------------------------------

@pytest.mark.parametrize("name,args,want", [
    ("ears-arm", ["--help"], "ears-arm <seat> [talk|alone|room]"),
    ("ears-count", [SEAT], "0\n"),
])
def test_shim_through_a_symlink_elsewhere_still_finds_the_script(fence, name, args, want):
    """A symlink to the shim in another directory (a user's ~/bin, say) resolves the
    shim's real place with readlink -f, so the script is still found."""
    elsewhere = fence.tmp / "elsewhere"; elsewhere.mkdir()
    (elsewhere / name).symlink_to(BIN / name)
    assert shutil.which(name, path=fence.env(elsewhere)["PATH"]) == str(elsewhere / name)
    r = fence([name, *args], elsewhere)
    assert r.returncode == 0, r.stderr
    assert want in r.stdout
    assert fence.created() == []


# -- the old path still works ------------------------------------------------------

@pytest.mark.parametrize("name,args,want", [
    ("ears-arm", ["--help"], "ears-arm <seat> [talk|alone|room]"),
    ("ears-count", [SEAT], "0\n"),
])
def test_old_skills_path_still_works(fence, name, args, want):
    r = fence([str(SCRIPTS / name), *args])
    assert r.returncode == 0, r.stderr
    assert want in r.stdout
    assert fence.created() == []
