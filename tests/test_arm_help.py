"""EARS-282: `ears-arm --help` took "--help" as a seat name, armed a real heard-first
waiter on ~/.ears and moved ~/.ears/heard.state to EOF (Sun 2030-06-09).

Now -h/--help prints the header and exits 0, an unknown '-' arg is refused, and
neither makes a home dir or writes a state file. `call-tap --help` is the same fix.

Every run is fenced: HOME and XDG_STATE_HOME are under tmp_path, EARS_HOME is unset,
and `heard-first` on PATH is a stub that records that it ran and exits 99, so nothing
here can arm a real waiter or touch a real ~/.ears."""
import os, subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EARS_ARM = ROOT / "skills" / "ears" / "scripts" / "ears-arm"
CALL_TAP = ROOT / "bin" / "call-tap"
# A seat name no real seat holds, so ears-count (a read-only ps scan) finds no waiter.
SEAT = "ears282probe"


@pytest.fixture
def fence(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    state = tmp_path / "state"
    stubs = tmp_path / "stubs"; stubs.mkdir()
    ran = tmp_path / "heard-first-ran"
    hf = stubs / "heard-first"
    hf.write_text(f'#!/bin/sh\necho "STUB heard-first EXEC'
                  f'D: $*" >&2\nprintf "%s\\n" "$*" > "{ran}"\nexit 99\n')
    hf.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k not in ("EARS_HOME", "EARS_SPEAKERS")}
    env.update(HOME=str(home), XDG_STATE_HOME=str(state),
               PATH=f"{stubs}:/usr/bin:/bin")

    def run(script, *args):
        return subprocess.run(["bash", str(script), *args], capture_output=True,
                              text=True, env=env, timeout=30)

    def created():
        """Everything a run left under the fenced HOME and state dir."""
        found = sorted(str(p.relative_to(tmp_path)) for d in (home, state) if d.exists()
                       for p in d.rglob("*"))
        return found + ([str(state.relative_to(tmp_path))] if state.exists() else [])

    run.home, run.ran, run.created = home, ran, created
    return run


# -- ears-arm ------------------------------------------------------------------

@pytest.mark.parametrize("args", [
    ["--help"], ["-h"],
    [SEAT, "--help"],             # after a seat: still help, still nothing armed
    [SEAT, "room", "-h"],
])
def test_ears_arm_help_prints_usage_and_arms_nothing(fence, args):
    r = fence(EARS_ARM, *args)
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
    r = fence(EARS_ARM, *args)
    assert r.returncode == 1
    assert "ears-arm: unknown option" in r.stderr
    assert r.stdout == ""
    assert not fence.ran.exists(), "heard-first was exec'd"
    assert fence.created() == []


def test_ears_arm_no_seat_is_a_usage_error(fence):
    r = fence(EARS_ARM)
    assert r.returncode != 0
    assert "usage: ears-arm <seat>" in r.stderr
    assert fence.created() == []


@pytest.mark.parametrize("args", [
    [SEAT, "--dry-run"],
    [SEAT, "talk", "--dry-run"],
    ["--dry-run", SEAT, "alone"],
])
def test_ears_arm_dry_run_anywhere_prints_and_creates_nothing(fence, args):
    r = fence(EARS_ARM, *args)
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("heard-first --source mic")   # printf %q writes mic\,call
    assert f"cut:{SEAT}" in r.stdout
    assert not fence.ran.exists()
    assert fence.created() == []


def test_ears_arm_dry_run_keeps_positional_names(fence):
    r = fence(EARS_ARM, SEAT, "alone", "ivy|dave", "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "partial:ivy\\|dave" in r.stdout
    assert "turn" in r.stdout


def test_the_fence_holds_a_real_arm_reaches_the_stub(fence):
    """The control: without a flag, ears-arm makes its home and execs heard-first,
    which here is the stub. So the empty results above are not a dead fence."""
    r = fence(EARS_ARM, SEAT, "talk")
    assert r.returncode == 99, r.stderr
    assert "STUB heard-first EXECD" in r.stderr
    assert f"--state {fence.home}/.ears/heard.state" in fence.ran.read_text()
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
