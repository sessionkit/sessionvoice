"""EARS-226 item 6: stopping the room capture escalates, STOP file, then SIGTERM, then SIGKILL.

Sun 2030-06-09: a wedged capture ignored its STOP file AND SIGTERM, and needed SIGKILL by
hand. The keeper used to touch the STOP file, wait 15 s and give up. These run the keeper's own
stop_capture against FAKE captures: python processes with a capture argv, under a pattern no
live capture matches (EARS_CAPTURE_PAT), whose STOP files are in tmp_path. Each fake is started
through a shell that exits at once, so it is reaped when it dies, as a keeper-started one is.
No real capture is ever signalled or sent a STOP file.
"""
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

KEEPER = Path(__file__).resolve().parents[1] / "keeper" / "ears-keeper.sh"

# exits once its --stop-file exists, as a healthy capture does (src/ears/listen/loop.py)
HONOURS_STOP = ("import os,sys,time;s=sys.argv[sys.argv.index('--stop-file')+1];"
                "[time.sleep(0.05) for _ in iter(lambda: os.path.exists(s), True)]")
# a wedged frame loop: never looks at its STOP file, and SIGTERM sets nothing it will ever read
IGNORES_STOP_AND_TERM = "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(120)"
# never looks at its STOP file, but the default SIGTERM still ends it
IGNORES_STOP = "import time;time.sleep(120)"


def _functions() -> str:
    s = KEEPER.read_text()
    return (s[s.index("note() {"):s.index("mail_me() {")]
            + s[s.index("CAPTURE_PAT="):s.index("start_capture() {")]
            + s[s.index("# --- stop:"):s.index("# --- end stop ---")])


def _alive(pid: int) -> bool:
    st = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(st) and not st.startswith("Z")


@pytest.fixture
def fake(tmp_path):
    tag = f"earstest{uuid.uuid4().hex[:8]}"
    pids = []

    def spawn(code, *argv):
        out = subprocess.run(["/bin/sh", "-c", '"$@" </dev/null >/dev/null 2>&1 & echo $!', "sh",
                              sys.executable, "-c", code, f"{tag}.listen", "capture", *argv],
                             capture_output=True, text=True, check=True)
        pid = int(out.stdout.strip())
        pids.append(pid)
        return pid

    spawn.pat = rf"{tag}\.listen capture"
    spawn.stop = lambda name: str(tmp_path / name)
    yield spawn
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _stop(pat: str, **env) -> tuple[int, str, float]:
    time.sleep(0.5)  # let ps see the argv, and the fake install its signal handler
    t0 = time.monotonic()
    out = subprocess.run(["bash", "-c", _functions() + "\nstop_capture; echo rc=$?"],
                         env=dict(os.environ, EARS_CAPTURE_PAT=pat, EARS_STOP_WAIT="1", EARS_TERM_WAIT="1", **env),
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    rc = int(out.stdout.strip().splitlines()[-1].split("=")[1])
    return rc, out.stdout, time.monotonic() - t0


def _room(fake, code, name="STOP-capture-keeper-203400"):
    return fake(code, "--source", "mic", "--device", "airpods", "--stop-file", fake.stop(name))


def test_a_healthy_capture_stops_on_its_stop_file_and_is_never_signalled(fake):
    pid = _room(fake, HONOURS_STOP)
    rc, out, took = _stop(fake.pat)
    assert rc == 0 and not _alive(pid)
    assert "stopping capture" in out and "SIGTERM" not in out and "SIGKILL" not in out
    assert Path(fake.stop("STOP-capture-keeper-203400")).exists()
    assert took < 1.5


def test_a_capture_that_ignores_its_stop_file_gets_sigterm(fake):
    pid = _room(fake, IGNORES_STOP)
    rc, out, _ = _stop(fake.pat)
    assert rc == 0 and not _alive(pid)
    assert "after its stop file: SIGTERM" in out and "SIGKILL" not in out


def test_a_wedged_capture_that_ignores_stop_and_sigterm_gets_sigkill(fake):
    """The capture: STOP file, then SIGTERM, then SIGKILL, each step noted."""
    pid = _room(fake, IGNORES_STOP_AND_TERM)
    rc, out, took = _stop(fake.pat)
    assert rc == 0 and not _alive(pid)
    notes = out.splitlines()
    i_stop = next(i for i, l in enumerate(notes) if "stopping capture" in l)
    i_term = next(i for i, l in enumerate(notes) if "after its stop file: SIGTERM" in l)
    i_kill = next(i for i, l in enumerate(notes) if "after SIGTERM: SIGKILL" in l)
    assert i_stop < i_term < i_kill
    assert 2 <= took < 6   # EARS_STOP_WAIT=1 + EARS_TERM_WAIT=1, then the kill


def test_a_call_leg_and_a_foreign_capture_are_never_touched(fake):
    leg = fake(IGNORES_STOP, "--source", "call:5", "--stop-file", fake.stop("STOP-capture-keeper-000001"))
    tap = fake(IGNORES_STOP, "--source", "mic", "--device", "BlackHole", "--stop-file", fake.stop("STOP-call-tap"))
    room = _room(fake, IGNORES_STOP_AND_TERM)
    rc, _, _ = _stop(fake.pat)
    assert rc == 0 and not _alive(room)
    assert _alive(leg) and _alive(tap)
    assert not Path(fake.stop("STOP-capture-keeper-000001")).exists() and not Path(fake.stop("STOP-call-tap")).exists()


def test_no_room_capture_is_nothing_to_stop(fake):
    tap = fake(IGNORES_STOP, "--source", "mic", "--device", "BlackHole", "--stop-file", fake.stop("STOP-call-tap"))
    rc, out, _ = _stop(fake.pat)
    assert rc == 0 and out.strip() == "rc=0" and _alive(tap)


def test_every_room_stop_in_the_keeper_goes_through_stop_capture():
    """No other path touches a capture's STOP file or signals it: one escalation for all."""
    s = KEEPER.read_text()
    outside = s[:s.index("# --- stop:")] + s[s.index("# --- end stop ---"):]
    assert 'touch "$stop"' not in outside
    loop = s[s.index("while :; do"):]
    assert loop.count("stop_capture") >= 3   # a call live, the lid, the FOLLOW swap (listening off: ears_off)
