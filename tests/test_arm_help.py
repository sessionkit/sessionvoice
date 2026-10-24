"""EARS-282: `ears-arm --help` took "--help" as a seat name, armed a real heard-first
waiter on ~/.ears and moved ~/.ears/heard.state to EOF (Sun 2030-06-09).

Now -h/--help prints the header and exits 0, an unknown '-' arg is refused, and
neither makes a home dir or writes a state file. `call-tap --help` is the same fix.

Every run is fenced: HOME and XDG_STATE_HOME are under tmp_path, EARS_HOME and
CLAUDE_PLUGIN_ROOT are unset, and ears-arm runs from a COPY of the plugin layout
(tests/plugin_copy.py) whose bin/heard-first is a stub that records that it ran and
exits 99. ears-arm finds heard-first from its own location first, so a stub earlier
on PATH would not fence the checkout's real one; the copy's own location IS the
stub. Nothing here can arm a real waiter or touch a real ~/.ears.

The seat is fenced too (EARS-432): ears-count counts waiters HOST-WIDE by `cut:<seat>`
in ps, so a fixed seat let a concurrent run's stub count as a waiter here. Each test
has its own probe seat (``fence.seat``); an arg equal to SEAT is swapped for it."""
import os, subprocess
from pathlib import Path

import pytest

from tests import plugin_copy

ROOT = Path(__file__).resolve().parents[1]
CALL_TAP = ROOT / "bin" / "call-tap"
# A placeholder: the fence swaps it for this test's own probe seat, a name no real
# seat and no concurrent run holds, so ears-count (a host-wide ps scan) finds no waiter.
SEAT = "<seat>"
PROBE = "ears282probe"


@pytest.fixture
def fence(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    state = tmp_path / "state"
    plugin = plugin_copy.make(tmp_path)
    ran = tmp_path / "plugin-ran"
    env = {k: v for k, v in os.environ.items() if k not in plugin_copy.STRIP}
    env.update(HOME=str(home), XDG_STATE_HOME=str(state), PATH="/usr/bin:/bin")

    seat = plugin_copy.probe_seat(PROBE)

    def run(script, *args):
        args = [seat if a == SEAT else a for a in args]
        return subprocess.run(["bash", str(script), *args], capture_output=True,
                              text=True, env=env, timeout=30)

    def created():
        """Everything a run left under the fenced HOME and state dir."""
        found = sorted(str(p.relative_to(tmp_path)) for d in (home, state) if d.exists()
                       for p in d.rglob("*"))
        return found + ([str(state.relative_to(tmp_path))] if state.exists() else [])

    run.home, run.ran, run.created, run.plugin, run.seat = home, ran, created, plugin, seat
    run.arm = plugin / "skills" / "ears" / "scripts" / "ears-arm"
    return run


# -- ears-arm ------------------------------------------------------------------

@pytest.mark.parametrize("args", [
    ["--help"], ["-h"],
    [SEAT, "--help"],             # after a seat: still help, still nothing armed
    [SEAT, "room", "-h"],
])
def test_ears_arm_help_prints_usage_and_arms_nothing(fence, args):
    r = fence(fence.arm, *args)
    assert r.returncode == 0, r.stderr
    assert "ears-arm <seat> [talk|alone|room]" in r.stdout
    assert "talk  :" in r.stdout and "room  :" in r.stdout
    assert "set -euo" not in r.stdout          # the header only, not the code
    assert r.stderr == ""
    assert not fence.ran.exists(), "heard-first was exec'd"
    assert fence.created() == []               # no ~/.ears, no heard.state


@pytest.mark.parametrize("args", [
    ["-x"], ["--bogus"],
    [SEAT, "-x"],                 # in the mode slot
    [SEAT, "talk", "--names"],    # in the names slot
    ["-x", "--help"],             # refused before help is reached
])
def test_ears_arm_unknown_option_is_refused(fence, args):
    r = fence(fence.arm, *args)
    assert r.returncode == 1
    assert "ears-arm: unknown option" in r.stderr
    assert r.stdout == ""
    assert not fence.ran.exists(), "heard-first was exec'd"
    assert fence.created() == []


def test_ears_arm_no_seat_is_a_usage_error(fence):
    r = fence(fence.arm)
    assert r.returncode != 0
    assert "usage: ears-arm <seat>" in r.stderr
    assert fence.created() == []


@pytest.mark.parametrize("args", [
    [SEAT, "--dry-run"],
    [SEAT, "talk", "--dry-run"],
    ["--dry-run", SEAT, "alone"],
])
def test_ears_arm_dry_run_anywhere_prints_and_creates_nothing(fence, args):
    r = fence(fence.arm, *args)
    assert r.returncode == 0, r.stderr
    # The resolved path, the copy's stub: printf %q writes mic\,call
    assert r.stdout.startswith(f"{fence.plugin}/bin/heard-first --source mic")
    assert f"cut:{fence.seat}" in r.stdout
    assert not fence.ran.exists()
    assert fence.created() == []


def test_ears_arm_dry_run_keeps_positional_names(fence):
    r = fence(fence.arm, SEAT, "alone", "ivy|dave", "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "partial:ivy\\|dave" in r.stdout
    assert "turn" in r.stdout


def test_the_fence_holds_a_real_arm_reaches_the_stub(fence):
    """The control: without a flag, ears-arm makes its home and execs heard-first,
    which here is the copy's stub. So the empty results above are not a dead fence."""
    r = fence(fence.arm, SEAT, "talk")
    assert r.returncode == 99, r.stderr
    assert "STUB heard-first EXECD" in r.stderr
    exe, argv = fence.ran.read_text().splitlines()
    assert exe == f"{fence.plugin}/bin/heard-first"
    assert f"--state {fence.home}/.ears/heard.state" in argv
    assert (fence.home / ".ears").is_dir()


# -- call-tap ------------------------------------------------------------------

@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_call_tap_help_exits_zero_and_creates_nothing(fence, flag):
    r = fence(CALL_TAP, flag)
    assert r.returncode == 0, r.stderr
    assert "call-tap up" in r.stdout and "call-tap status" in r.stdout
    assert "Env: CALL_TAP_MIC" in r.stdout
    assert "set -u" not in r.stdout
    assert fence.created() == []               # no $XDG_STATE_HOME/ears/call-tap


def test_call_tap_unknown_subcommand_still_exits_one(fence):
    r = fence(CALL_TAP, "bogus")
    assert r.returncode == 1
    assert "call-tap up" in r.stdout
