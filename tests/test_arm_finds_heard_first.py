"""EARS-422: ears-arm finds heard-first WITHOUT the plugin's bin/ on PATH.

Claude Code puts a plugin's bin/ on the Bash tool's PATH only for a plugin enabled when
the session started. After a mid-session install, ears-arm's bare `heard-first` was not
found (`exec: heard-first: not found`, rc 127) until Claude Code restarted. Now it looks,
in order: (1) the plugin root's bin/ from its own location, symlinks resolved; (2)
$CLAUDE_PLUGIN_ROOT/bin; (3) PATH. All three missing is ONE plain stderr line and exit
127 (never 2, which means a waiter is already up).

Every run is fenced: HOME and XDG_STATE_HOME are under tmp_path; EARS_HOME and the
runner's CLAUDE_PLUGIN_ROOT are dropped; PATH is /usr/bin:/bin plus only what a test
names. ears-arm runs from a COPY of the plugin layout (tests/plugin_copy.py), so its
own location is a stub, or nothing; every heard-first that can run is a stub that
records its own path, and each test asserts WHICH stub ran. Nothing here reaches the
checkout's bin/heard-first, a real ~/.ears or launchctl.

The seat is fenced too (EARS-432): ears-count counts waiters HOST-WIDE by `cut:<seat>`
in ps, so each test arms its own probe seat (``fence.seat``, from
plugin_copy.probe_seat). A fixed seat let a concurrent run's stub, briefly in ps,
count as a waiter here: exit 2, not the stub's 99."""
import os
import re
import subprocess
from pathlib import Path

import pytest

from tests import plugin_copy

ROOT = Path(__file__).resolve().parents[1]
REAL_HF = ROOT / "bin" / "heard-first"
PROBE = "ears422probe"          # the prefix of each test's own seat; see fence.seat
RC = plugin_copy.STUB_RC


@pytest.fixture
def fence(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    base = {k: v for k, v in os.environ.items() if k not in plugin_copy.STRIP}
    base.update(HOME=str(home), XDG_STATE_HOME=str(tmp_path / "state"))

    def run(argv, *front, **extra):
        env = {**base, "PATH": os.pathsep.join([*map(str, front), "/usr/bin", "/bin"]),
               **extra}
        return subprocess.run(list(map(str, argv)), capture_output=True, text=True,
                              env=env, timeout=30)

    def ran(path):
        """(exe, argv) the stub recorded at ``path``."""
        exe, argv = Path(path).read_text().splitlines()
        assert Path(exe).resolve() != REAL_HF.resolve(), "the REAL heard-first ran"
        return exe, argv

    run.tmp, run.home, run.ran = tmp_path, home, ran
    # This test's own seat: no real seat and no concurrent run holds it, so the
    # host-wide ears-count finds no waiter but the ones this test makes.
    run.seat = plugin_copy.probe_seat(PROBE)
    return run


def arm_of(plugin):
    return plugin / "skills" / "ears" / "scripts" / "ears-arm"


# -- route 1: the plugin root's bin/, from ears-arm's own location -----------------

def test_own_location_arms_with_no_plugin_bin_on_path(fence):
    """The user's case: PATH without the plugin's bin/, no CLAUDE_PLUGIN_ROOT. It arms."""
    plugin = plugin_copy.make(fence.tmp)
    r = fence([arm_of(plugin), fence.seat, "talk"])
    assert r.returncode == RC, r.stderr
    exe, argv = fence.ran(fence.tmp / "plugin-ran")
    assert exe == f"{plugin}/bin/heard-first"
    assert f"cut:{fence.seat}" in argv and f"--state {fence.home}/.ears/heard.state" in argv


def test_own_location_wins_over_plugin_root_and_path(fence):
    plugin = plugin_copy.make(fence.tmp)
    other = fence.tmp / "other-root"
    plugin_copy.stub_heard_first(other / "bin", fence.tmp / "other-ran")
    stubs = fence.tmp / "stubs"
    plugin_copy.stub_heard_first(stubs, fence.tmp / "path-ran")
    r = fence([arm_of(plugin), fence.seat, "talk"], stubs, CLAUDE_PLUGIN_ROOT=str(other))
    assert r.returncode == RC, r.stderr
    assert fence.ran(fence.tmp / "plugin-ran")[0] == f"{plugin}/bin/heard-first"
    assert not (fence.tmp / "other-ran").exists()
    assert not (fence.tmp / "path-ran").exists()


def test_own_location_through_the_bin_shim(fence):
    plugin = plugin_copy.make(fence.tmp)
    r = fence([plugin / "bin" / "ears-arm", fence.seat, "alone"])
    assert r.returncode == RC, r.stderr
    assert fence.ran(fence.tmp / "plugin-ran")[0] == f"{plugin}/bin/heard-first"


def test_own_location_through_a_symlink_elsewhere(fence):
    """A symlink to the script in another directory resolves with readlink -f, so the
    lookup is from the script's real place, not the symlink's."""
    plugin = plugin_copy.make(fence.tmp)
    elsewhere = fence.tmp / "elsewhere" / "a" / "b"; elsewhere.mkdir(parents=True)
    (elsewhere / "ears-arm").symlink_to(arm_of(plugin))
    r = fence([elsewhere / "ears-arm", fence.seat, "talk"])
    assert r.returncode == RC, r.stderr
    assert fence.ran(fence.tmp / "plugin-ran")[0] == f"{plugin}/bin/heard-first"


# -- route 2: $CLAUDE_PLUGIN_ROOT/bin ----------------------------------------------

def test_plugin_root_when_own_location_misses(fence):
    plugin = plugin_copy.make(fence.tmp, heard_first=False)
    other = fence.tmp / "other-root"
    plugin_copy.stub_heard_first(other / "bin", fence.tmp / "other-ran")
    stubs = fence.tmp / "stubs"
    plugin_copy.stub_heard_first(stubs, fence.tmp / "path-ran")
    r = fence([arm_of(plugin), fence.seat, "talk"], stubs, CLAUDE_PLUGIN_ROOT=str(other))
    assert r.returncode == RC, r.stderr
    assert fence.ran(fence.tmp / "other-ran")[0] == f"{other}/bin/heard-first"
    assert not (fence.tmp / "path-ran").exists()


def test_plugin_root_without_an_executable_heard_first_falls_to_path(fence):
    plugin = plugin_copy.make(fence.tmp, heard_first=False)
    other = fence.tmp / "other-root"
    hf = plugin_copy.stub_heard_first(other / "bin", fence.tmp / "other-ran")
    hf.chmod(0o644)                                  # there, but not executable
    stubs = fence.tmp / "stubs"
    plugin_copy.stub_heard_first(stubs, fence.tmp / "path-ran")
    r = fence([arm_of(plugin), fence.seat, "talk"], stubs, CLAUDE_PLUGIN_ROOT=str(other))
    assert r.returncode == RC, r.stderr
    assert fence.ran(fence.tmp / "path-ran")[0] == f"{stubs}/heard-first"


# -- route 3: PATH -----------------------------------------------------------------

def test_path_when_both_miss(fence):
    plugin = plugin_copy.make(fence.tmp, heard_first=False)
    stubs = fence.tmp / "stubs"
    plugin_copy.stub_heard_first(stubs, fence.tmp / "path-ran")
    r = fence([arm_of(plugin), fence.seat, "room"], stubs,
              CLAUDE_PLUGIN_ROOT=str(fence.tmp / "no-such-root"))
    assert r.returncode == RC, r.stderr
    assert fence.ran(fence.tmp / "path-ran")[0] == f"{stubs}/heard-first"


# -- all three miss ----------------------------------------------------------------

@pytest.mark.parametrize("extra", [[], ["--dry-run"]])
def test_all_miss_is_one_plain_line_and_not_exit_2(fence, extra):
    plugin = plugin_copy.make(fence.tmp, heard_first=False)
    r = fence([arm_of(plugin), fence.seat, "talk", *extra],
              CLAUDE_PLUGIN_ROOT=str(fence.tmp / "no-such-root"))
    assert r.returncode not in (0, 2), r.returncode
    assert r.returncode == 127
    assert r.stdout == ""
    lines = r.stderr.splitlines()
    assert len(lines) == 1, r.stderr
    assert lines[0].startswith("ears-arm: heard-first was not found")
    assert "restarting Claude Code puts the plugin's bin/ on PATH" in lines[0]


# -- --dry-run prints the resolved command -----------------------------------------

def test_dry_run_prints_the_resolved_path_and_runs_nothing(fence):
    plugin = plugin_copy.make(fence.tmp)
    r = fence([arm_of(plugin), fence.seat, "talk", "--dry-run"])
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith(f"{plugin}/bin/heard-first --source mic")
    assert not (fence.tmp / "plugin-ran").exists()
    assert not (fence.home / ".ears").exists()


def test_dry_run_prints_the_plugin_root_path(fence):
    plugin = plugin_copy.make(fence.tmp, heard_first=False)
    other = fence.tmp / "other-root"
    plugin_copy.stub_heard_first(other / "bin", fence.tmp / "other-ran")
    r = fence([arm_of(plugin), fence.seat, "--dry-run"], CLAUDE_PLUGIN_ROOT=str(other))
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith(f"{other}/bin/heard-first --source mic")
    assert not (fence.tmp / "other-ran").exists()


# -- ears-count still counts the waiter the resolved path makes --------------------

def test_ears_count_matches_the_resolved_argv(fence):
    """ears-count greps ps for `bin/heard-first .*cut:<seat>`. The resolved full path
    keeps `.../bin/heard-first` in argv; a python script started through
    `/usr/bin/env python3` shows as `python3 <path> <args>`. A fake ps prints that line
    for the argv the stub recorded, and ears-count counts it."""
    plugin = plugin_copy.make(fence.tmp)
    r = fence([arm_of(plugin), fence.seat, "talk"])
    assert r.returncode == RC, r.stderr
    exe, argv = fence.ran(fence.tmp / "plugin-ran")
    line = f"python3 {exe} {argv}"
    assert re.search(rf"bin/heard-first .*cut:{fence.seat}( |$)", line), line
    stubs = fence.tmp / "stubs"; stubs.mkdir()
    ps = stubs / "ps"
    ps.write_text(f"#!/bin/sh\necho '  4242 00:01 {line}'\n"); ps.chmod(0o755)
    c = fence([plugin / "skills" / "ears" / "scripts" / "ears-count", fence.seat], stubs)
    assert c.returncode == 0, c.stderr
    assert c.stdout == "1\n"


# -- the seat fence (EARS-432) -----------------------------------------------------

def test_probe_seat_is_unique_alphanumeric_and_keeps_its_prefix():
    seats = {plugin_copy.probe_seat(PROBE) for _ in range(200)}
    assert len(seats) == 200
    for seat in seats:
        assert seat.startswith(PROBE) and len(seat) == len(PROBE) + 12
        assert re.fullmatch(r"[a-z0-9]+", seat), seat     # safe unescaped in the regex


def test_a_concurrent_waiter_on_another_probe_seat_is_not_counted(fence):
    """The hazard, live: a process in ps that looks like a heard-first waiter on
    ANOTHER probe seat (a concurrent run's stub) is counted host-wide for that seat,
    and is not counted for this test's seat, so the arm still reaches the stub."""
    other = plugin_copy.probe_seat(PROBE)
    assert other != fence.seat
    fake = fence.tmp / "neighbour" / "bin"; fake.mkdir(parents=True)
    hf = fake / "heard-first"
    hf.write_text("#!/bin/sh\nsleep 30\n"); hf.chmod(0o755)
    neighbour = subprocess.Popen(["/bin/sh", str(hf), "--rule", f"cut:{other}"],
                                 start_new_session=True)
    try:
        plugin = plugin_copy.make(fence.tmp)
        ears_count = plugin / "skills" / "ears" / "scripts" / "ears-count"
        for _ in range(50):                      # until ps shows the neighbour
            if fence([ears_count, other]).stdout == "1\n":
                break
        else:
            pytest.fail("the neighbour waiter never showed in ps")
        assert fence([ears_count, fence.seat]).stdout == "0\n"
        r = fence([arm_of(plugin), fence.seat, "talk"])
        assert r.returncode == RC, r.stderr
        assert "already up" not in r.stderr
    finally:
        neighbour.kill(); neighbour.wait(timeout=10)
