"""EARS-301: `call-tap up --help` started both call legs. EARS-282 checked -h/--help
in $1 only, so a subcommand followed by a help flag ran the subcommand.

Now -h/--help anywhere in argv prints the header and exits 0 before the run dir is
made: no leg starts, no STOP is touched, no pid file is written. An unknown
subcommand and a bare `call-tap` still print usage and exit 1.

Every run is fenced: HOME and XDG_STATE_HOME are under tmp_path, EARS_HOME is unset,
`sox` on PATH and EARS_PY are stubs that record that they ran and exit, and a Silero
model file exists, so a regression would reach the stubs (and be DETECTED) instead
of stopping at the missing-model check. Nothing here can open an audio device."""
import os, subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CALL_TAP = ROOT / "bin" / "call-tap"


@pytest.fixture
def fence(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    state = tmp_path / "state"
    stubs = tmp_path / "stubs"; stubs.mkdir()
    ran = tmp_path / "ran"; ran.mkdir()
    model = tmp_path / "silero_vad.onnx"; model.write_bytes(b"onnx")
    # `up` backgrounds sox, waits on the capture, then kills sox: the capture stub
    # waits (5s cap) for sox's record, or a fast capture kills sox before it writes.
    wait_sox = (f'i=0; while [ ! -e "{ran / "sox"}" ] && [ $i -lt 50 ]; do '
                f'sleep 0.1; i=$((i+1)); done\n')
    for name, pre in (("sox", ""), ("python", wait_sox)):
        stub = stubs / name
        stub.write_text(f'#!/bin/sh\n{pre}printf "%s\\n" "$*" > "{ran / name}"\nexit 0\n')
        stub.chmod(0o755)
    env = {k: v for k, v in os.environ.items()
           if k not in ("EARS_HOME", "EARS_SPEAKERS", "EARS_SILERO_ONNX",
                        "CALL_TAP_MIC", "CALL_TAP_FAR", "CALL_TAP_NEAR")}
    env.update(HOME=str(home), XDG_STATE_HOME=str(state), EARS_PY=str(stubs / "python"),
               EARS_SILERO_MODEL=str(model), PATH=f"{stubs}:/usr/bin:/bin")

    def run(*args):
        return subprocess.run(["bash", str(CALL_TAP), *args], capture_output=True,
                              text=True, env=env, timeout=30)

    def stubs_ran():
        return sorted(p.name for p in ran.iterdir())

    def created():
        """Everything a run left under the fenced HOME and state dir."""
        found = sorted(str(p.relative_to(tmp_path)) for d in (home, state) if d.exists()
                       for p in d.rglob("*"))
        return found + ([str(state.relative_to(tmp_path))] if state.exists() else [])

    run.state, run.stubs_ran, run.created = state, stubs_ran, created
    return run


def assert_usage(out):
    assert "call-tap up" in out and "call-tap down" in out and "call-tap status" in out
    assert "Env: CALL_TAP_MIC" in out
    assert "set -u" not in out                 # the header only, not the code


@pytest.mark.parametrize("args", [
    ["--help"], ["-h"],
    ["up", "--help"], ["up", "-h"],
    ["down", "--help"], ["down", "-h"],
    ["status", "-h"], ["status", "--help"],
    ["up", "extra", "--help"],                 # not only $2: anywhere
    ["bogus", "--help"],                       # help wins over an unknown command
])
def test_help_anywhere_prints_usage_and_starts_nothing(fence, args):
    r = fence(*args)
    assert r.returncode == 0, r.stderr
    assert_usage(r.stdout)
    assert r.stderr == ""
    assert fence.stubs_ran() == [], "a leg was started"
    assert fence.created() == []               # no run dir, no STOP, no pid file


def test_unknown_subcommand_still_exits_one(fence):
    r = fence("bogus")
    assert r.returncode == 1
    assert_usage(r.stdout)
    assert fence.stubs_ran() == []


def test_bare_call_tap_still_exits_one(fence):
    r = fence()
    assert r.returncode == 1
    assert_usage(r.stdout)
    assert fence.stubs_ran() == []


def test_the_fence_holds_a_real_up_reaches_the_stubs(fence):
    """The control: without a help flag, `up` makes its run dir and starts both
    legs, which here are the stubs. So the empty results above are not a dead fence."""
    r = fence("up")
    assert r.returncode == 0, r.stderr
    assert fence.stubs_ran() == ["python", "sox"]
    run_dir = fence.state / "ears" / "call-tap"
    assert (run_dir / "sox.pid").is_file() and (run_dir / "capture.pid").is_file()


def test_the_fence_holds_a_real_down_touches_stop(fence):
    r = fence("down")
    assert r.returncode == 0, r.stderr
    assert (fence.state / "ears" / "call-tap" / "STOP").exists()
