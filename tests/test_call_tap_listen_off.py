"""EARS-364, the super's ruling (c): `listen: false` pauses call-tap's far-end EAR, and its sox
passthrough (the user's own voice OUT to the call app) keeps running. `down` still ends both.

The keeper pauses the ear by touching $RUN/LISTEN-OFF, then STOP-ear (the capture's own stop
file, apart from `down`'s STOP). call-tap holds the ear off while LISTEN-OFF is there and starts
it again when it clears.

FAKES only: `sox` on PATH is a stub that sleeps, EARS_PY is a stub "python" that logs each start
and runs until its --stop-file appears, a Silero model file exists, and HOME and XDG_STATE_HOME
are under tmp_path. Nothing here opens an audio device or runs the real call-tap legs. Every
test runs under bash 5 (`bash`) AND macOS's /bin/bash 3.2.
"""
import os
import signal
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

CALL_TAP = Path(__file__).resolve().parents[1] / "bin" / "call-tap"
shells = pytest.mark.parametrize("sh", [pytest.param(s, id=s) for s in ("bash", "/bin/bash")])


def _alive(pid: int) -> bool:
    st = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(st) and not st.startswith("Z")


def _wait_for(cond, secs=6.0):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.1)
    return cond()


@pytest.fixture
def tap(tmp_path):
    if not Path("/bin/bash").exists():
        pytest.skip("no /bin/bash")
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    starts = tmp_path / "starts"
    (stubs / "sox").write_text("#!/bin/sh\nexec sleep 120\n")
    # FAKE_DIE: the capture ends by itself after a moment, as one does when its device goes
    (stubs / "python").write_text(
        '#!/bin/sh\necho start >>"%s"\n'
        '[ -n "${FAKE_DIE:-}" ] && { sleep 0.5; exit 3; }\n'
        's=""; prev=""\nfor a in "$@"; do [ "$prev" = "--stop-file" ] && s="$a"; prev="$a"; done\n'
        'while [ ! -e "$s" ]; do sleep 0.1; done\n' % starts)
    for f in ("sox", "python"):
        (stubs / f).chmod(0o755)
    model = tmp_path / "vad.onnx"
    model.write_bytes(b"onnx")
    state = tmp_path / "state"
    env = {k: v for k, v in os.environ.items() if not k.startswith("CALL_TAP_")}
    env.update(HOME=str(tmp_path), XDG_STATE_HOME=str(state), EARS_PY=str(stubs / "python"),
               EARS_SILERO_MODEL=str(model), PATH=f"{stubs}:/usr/bin:/bin")
    run_dir = state / "ears" / "call-tap"
    ups = []

    def up(sh, **extra):
        p = subprocess.Popen([sh, str(CALL_TAP), "up"], env=dict(env, **extra), stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, start_new_session=True)
        ups.append(p)
        assert _wait_for(lambda: (run_dir / "sox.pid").exists())
        return p

    def cmd(sh, *args):
        return subprocess.run([sh, str(CALL_TAP), *args], env=env, capture_output=True, text=True, timeout=30)

    def n_starts():
        return starts.read_text().count("start") if starts.exists() else 0

    def pid(name):
        return int((run_dir / f"{name}.pid").read_text())

    def pause():  # what the keeper's tap_off does, in its order
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "LISTEN-OFF").touch()
        (run_dir / "STOP-ear").touch()

    yield SimpleNamespace(up=up, cmd=cmd, n_starts=n_starts, pid=pid, pause=pause, dir=run_dir)
    for p in ups:   # each `up` leads its own session: its sox and capture die with it
        if p.poll() is None:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait()


@shells
def test_off_stops_the_ear_and_keeps_the_passthrough_and_on_brings_it_back(tap, sh):
    p = tap.up(sh)
    assert _wait_for(lambda: tap.n_starts() == 1 and (tap.dir / "capture.pid").exists())
    sox, cap = tap.pid("sox"), tap.pid("capture")
    tap.pause()
    assert _wait_for(lambda: not _alive(cap)), "the far-end capture stops"
    time.sleep(1.5)
    assert _alive(sox) and p.poll() is None, "the passthrough and the tap run on"
    assert tap.n_starts() == 1, "no capture starts while LISTEN-OFF is there"
    st = tap.cmd(sh, "status").stdout
    assert f"sox: running pid {sox}" in st and "capture: paused (listening OFF by ears.json)" in st
    (tap.dir / "LISTEN-OFF").unlink()
    assert _wait_for(lambda: tap.n_starts() == 2), "listen:true brings the capture back"
    assert _wait_for(lambda: (tap.dir / "capture.pid").exists() and _alive(tap.pid("capture")))
    assert not (tap.dir / "STOP-ear").exists()
    assert "capture: running pid" in tap.cmd(sh, "status").stdout
    assert tap.cmd(sh, "down").returncode == 0
    p.wait(timeout=10)
    assert not _alive(sox)
    out = p.stdout.read()
    assert out.count("far-end ear paused, passthrough running") == 1
    assert "listening is ON again" in out and "passthrough stopped" in out


@shells
def test_down_while_paused_ends_both(tap, sh):
    p = tap.up(sh)
    assert _wait_for(lambda: tap.n_starts() == 1)
    sox = tap.pid("sox")
    tap.pause()
    time.sleep(1)
    assert tap.cmd(sh, "down").returncode == 0
    p.wait(timeout=10)
    assert not _alive(sox) and tap.n_starts() == 1


@shells
def test_up_while_off_starts_the_passthrough_and_no_ear_until_it_clears(tap, sh):
    tap.dir.mkdir(parents=True)
    (tap.dir / "LISTEN-OFF").touch()
    p = tap.up(sh)
    time.sleep(1.5)
    sox = tap.pid("sox")
    assert _alive(sox) and tap.n_starts() == 0 and p.poll() is None
    (tap.dir / "LISTEN-OFF").unlink()
    assert _wait_for(lambda: tap.n_starts() == 1)
    tap.cmd(sh, "down")
    p.wait(timeout=10)
    assert not _alive(sox)


@shells
def test_down_ends_both_as_before(tap, sh):
    p = tap.up(sh)
    assert _wait_for(lambda: tap.n_starts() == 1 and (tap.dir / "capture.pid").exists())
    sox, cap = tap.pid("sox"), tap.pid("capture")
    tap.cmd(sh, "down")
    p.wait(timeout=10)
    assert not _alive(sox) and not _alive(cap) and tap.n_starts() == 1
    assert (tap.dir / "STOP").exists() and (tap.dir / "STOP-ear").exists()


@shells
def test_a_capture_that_ends_by_itself_still_ends_the_tap(tap, sh):
    """Not a pause: no LISTEN-OFF, no STOP-ear. The tap ends and takes the passthrough, as before."""
    p = tap.up(sh, FAKE_DIE="1")
    sox = tap.pid("sox")
    p.wait(timeout=10)
    assert not _alive(sox) and tap.n_starts() == 1
    assert "capture exited 3" in p.stdout.read()


@shells
def test_status_says_not_running_when_no_tap_runs_even_if_off(tap, sh):
    tap.dir.mkdir(parents=True)
    (tap.dir / "LISTEN-OFF").touch()
    st = tap.cmd(sh, "status").stdout
    assert "sox: not running" in st and "capture: not running" in st
