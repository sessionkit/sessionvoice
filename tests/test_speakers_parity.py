"""EARS-10: run.sh + launchd/dev.sessionkit.voice.ears-speakers.plist.in do everything the interim
follow-capture does (device follow, a stop file that keeps it down, restart on a crash or an
exit), so follow-capture can retire. The parity table is in speakers/README.md; each row
names its test, here or in test_speakers_follow.py.

launchd itself is not run (live jobs stay untouched). What launchd decides from is tested:
the plist says KeepAlive {SuccessfulExit: false} (test_speakers_follow), run.sh execs the
side-car so the job's exit status IS the side-car's (here), and the side-car exits 0 only on
its stop file (here). No model, no audio: a stand-in diarizer and a stand-in sounddevice
with no devices; run.sh is run from a scratch copy of speakers/ with a stub side-car."""
import json
import os
import plistlib
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest
from test_speakers import _wav, stub_diarizer

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "speakers" / "run.sh"
SC = ROOT / "speakers" / "speaker-sidecar2.py"

# The side-car run.sh starts, as a stub: records its pid and argv, then does what
# FAKE_DO says: "stop" waits for STOP (as the real one does) and exits 0; a number exits
# with it; "hang" waits to be killed.
FAKE = '''import json, os, sys, time
json.dump({"pid": os.getpid(), "argv": sys.argv[1:], "stop_at_start": os.path.exists("STOP")},
          open("argv.tmp", "w"))
os.replace("argv.tmp", "argv.json")
do = os.environ.get("FAKE_DO", "stop")
if do not in ("stop", "hang"):
    sys.exit(int(do))
t = time.time() + 30
while time.time() < t:
    if do == "stop" and os.path.exists("STOP"):
        sys.exit(0)
    time.sleep(0.05)
sys.exit(9)  # the test timed out
'''


def scratch(tmp_path, call=True):
    """speakers/ holding run.sh, the stub side-car, refs/ (and refs-call/), in launchd's env.
    SPEAKER_PY is the base interpreter, not a venv: run.sh runs it as itself (no copy made
    anywhere outside tmp_path)."""
    sp = tmp_path / "speakers"
    (sp / "refs").mkdir(parents=True)
    shutil.copy(RUN, sp / "run.sh")
    (sp / "speaker-sidecar2.py").write_text(FAKE)
    for n in ("alice", "theo", "dave"):
        (sp / "refs" / f"{n}.wav").write_bytes(b"")
    if call:
        (sp / "refs-call").mkdir()
        (sp / "refs-call" / "theo.wav").write_bytes(b"")
    py = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
           "SPEAKER_PY": str(py), "SPEAKER_DEVICE": "follow", "SPEAKER_AGENT_REFS": "dave"}
    return sp, env


@pytest.fixture(autouse=True)
def reap():
    """Every child a test starts is killed at teardown, so a failing test leaves nothing
    waiting for a STOP that never comes."""
    procs = []
    yield procs
    for p in procs:
        if p.poll() is None:
            p.kill()
            p.wait(timeout=10)


def start(sp, env, procs, **kw):
    p = subprocess.Popen(["/bin/bash", str(sp / "run.sh")], cwd=sp, env=dict(env, **kw),
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    procs.append(p)
    return p


def started(sp, p):
    t = time.time() + 30
    while not (sp / "argv.json").exists():
        if p.poll() is not None:
            pytest.fail(f"run.sh exited {p.returncode}: {p.stderr.read()}")
        assert time.time() < t, "the stub side-car never started"
        time.sleep(0.05)
    return json.loads((sp / "argv.json").read_text())


def test_run_sh_gives_the_side_car_what_follow_capture_gave_it(reap, tmp_path):
    """follow-capture ran: --device <the keeper's> --enroll alice=... dave=... theo=...
    --agent-refs dave --enroll-s 12 --ring 300 --stop-file STOP-follow-HHMMSS. run.sh: the
    same, with --device follow (the move happens in-process), every refs/*.wav in name
    order, and every refs-call/*.wav as --call-enroll. It is exec'd: the job's pid is the
    side-car's, so launchd sees the side-car's own exit."""
    sp, env = scratch(tmp_path)
    p = start(sp, env, reap)
    got = started(sp, p)
    assert got["pid"] == p.pid, "run.sh did not exec the side-car"
    assert got["argv"] == ["--device", "follow",
                           "--enroll", "alice=refs/alice.wav", "--enroll", "dave=refs/dave.wav",
                           "--enroll", "theo=refs/theo.wav",
                           "--call-enroll", "theo=refs-call/theo.wav",
                           "--agent-refs", "dave", "--enroll-s", "12", "--ring", "300",
                           "--stop-file", "STOP"]
    (sp / "STOP").touch()
    assert p.wait(timeout=15) == 0


def test_run_sh_pins_a_named_device_as_one_argument(reap, tmp_path):
    """SPEAKER_DEVICE pins it, as follow-capture's --device did: a name with spaces stays
    one argument. No refs-call/: no --call-enroll (follow-capture passed none)."""
    sp, env = scratch(tmp_path, call=False)
    p = start(sp, env, reap, SPEAKER_DEVICE="MacBook Pro Microphone")
    got = started(sp, p)
    assert got["argv"][:2] == ["--device", "MacBook Pro Microphone"]
    assert "--call-enroll" not in got["argv"]
    (sp / "STOP").touch()
    assert p.wait(timeout=15) == 0


def test_run_sh_clears_a_stale_stop_so_the_next_load_comes_back(reap, tmp_path):
    """follow-capture's `rm -f STOP-FOLLOW` at start: a STOP keeps it down until the next
    load or login, never for good. run.sh removes STOP before the side-car starts, and the
    side-car then runs until STOP is touched again."""
    sp, env = scratch(tmp_path)
    (sp / "STOP").touch()
    p = start(sp, env, reap)
    got = started(sp, p)
    assert got["stop_at_start"] is False and not (sp / "STOP").exists()
    time.sleep(0.5)
    assert p.poll() is None, "it stopped on the STOP left from last time"
    (sp / "STOP").touch()
    assert p.wait(timeout=15) == 0


@pytest.mark.parametrize("do,want", [("0", 0), ("1", 1), ("3", 3)])
def test_run_sh_passes_the_side_cars_exit_status_to_launchd(reap, tmp_path, do, want):
    """KeepAlive {SuccessfulExit: false} restarts any exit but a clean 0. run.sh must not
    turn a crash into 0 (it would stay down) or a STOP into non-zero (it would come back)."""
    sp, env = scratch(tmp_path)
    p = start(sp, env, reap, FAKE_DO=do)
    assert p.wait(timeout=30) == want


@pytest.mark.parametrize("sig", [signal.SIGKILL, signal.SIGTERM])
def test_run_sh_a_killed_side_car_is_not_a_clean_exit(reap, tmp_path, sig):
    """Killed (a crash, an OOM kill): launchd sees the signal, not bash's 0, and restarts it.
    follow-capture restarted a dead side-car itself after 5 s; launchd does it here."""
    sp, env = scratch(tmp_path)
    p = start(sp, env, reap, FAKE_DO="hang")
    got = started(sp, p)
    os.kill(got["pid"], sig)
    assert p.wait(timeout=15) == -sig


def test_run_sh_refuses_without_refs_and_launchd_retries(reap, tmp_path):
    """No references (or no python): exit 1, never 0, so launchd tries again (ThrottleInterval
    apart) rather than staying down with no word."""
    sp, env = scratch(tmp_path)
    for w in (sp / "refs").iterdir():
        w.unlink()
    p = start(sp, env, reap)
    assert p.wait(timeout=30) == 1 and "no refs" in p.stderr.read()


# -- the real side-car, live (not --relabel): its stop file, and a kill --

SD_STUB = '''# sounddevice with no devices: no stream ever opens, so no real audio is read.
def query_devices(idx=None, kind=None): return [] if idx is None else {"default_samplerate": 16000.0}
def _terminate(): pass
def _initialize(): pass
def InputStream(**kw): raise RuntimeError("no devices in the test")
'''


def live(tmp_path, procs):
    """The side-car following today's (empty) heard log in tmp_path, labelling against one
    stand-in reference, with --device follow and its stop file."""
    stub = stub_diarizer(tmp_path)
    (stub / "sounddevice.py").write_text(SD_STUB)
    _wav(tmp_path / "theo.wav", 600, 3)
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / f"heard_{datetime.now().astimezone().date().isoformat()}.jsonl").write_text("")
    out = (tmp_path / "out.log").open("w")
    args = [sys.executable, str(SC), "--device", "follow", "--enroll", f"theo={tmp_path / 'theo.wav'}",
            "--enroll-s", "3", "--logdir", str(logs), "--stop-file", str(tmp_path / "STOP")]
    env = dict(os.environ, PYTHONPATH=str(stub), STUB_LAST="4", HOME=str(tmp_path))
    p = subprocess.Popen(args, stdout=out, stderr=subprocess.STDOUT, env=env, cwd=tmp_path)
    procs.append(p)
    t = time.time() + 60
    while not re.search(r"following \S*heard_", (tmp_path / "out.log").read_text()):
        if p.poll() is not None:
            pytest.fail(f"side-car exited {p.returncode}: {(tmp_path / 'out.log').read_text()}")
        assert time.time() < t, "the side-car never reached its loop"
        time.sleep(0.1)
    return p


def test_side_car_exits_0_on_its_stop_file_while_following(reap, tmp_path):
    """The STOP-FOLLOW equivalent: touch STOP and the live side-car exits 0 within a second
    (it looks every 0.2 s while the heard log is quiet), so KeepAlive {SuccessfulExit: false}
    leaves it down."""
    p = live(tmp_path, reap)
    time.sleep(0.5)
    assert p.poll() is None, "it exited with no STOP"
    (tmp_path / "STOP").touch()
    assert p.wait(timeout=10) == 0


def test_side_car_killed_is_not_a_clean_exit(reap, tmp_path):
    """A TERM (launchctl kill, or anyone's kill) ends it by the signal, not with 0: launchd
    restarts it. Only STOP keeps it down (follow-capture trapped TERM and exited 0; see the
    README's parity table for why that one is not kept)."""
    p = live(tmp_path, reap)
    p.send_signal(signal.SIGTERM)
    assert p.wait(timeout=10) == -signal.SIGTERM


def test_plist_runs_from_the_repo_with_its_own_log(tmp_path):
    """follow-capture's job ran from an agent's home and logged to speaker-sidecar.log, ten
    seconds between restarts. ears-speakers runs the repo's own speakers/, logs to
    ears-speakers.log, and spaces a crash loop 30 s apart (a refused reference reloads the
    model every start). RunAtLoad brings it back after a reboot or a login."""
    text = (ROOT / "launchd" / "dev.sessionkit.voice.ears-speakers.plist.in").read_text()
    for k, v in {"@HOME@": "/Users/x", "@EARS_REPO@": "/Users/x/ears",
                 "@SPEAKER_PY@": "/Users/x/venv/bin/python", "@AGENT_REFS@": "dave",
                 "@EARS_LOG_DIR@": "/Users/x/.sessionkit/voice/log"}.items():
        text = text.replace(k, v)
    pl = plistlib.loads(text.encode())
    assert pl["WorkingDirectory"] == "/Users/x/ears/speakers"
    assert pl["StandardOutPath"] == pl["StandardErrorPath"] == "/Users/x/.sessionkit/voice/log/ears-speakers.log"
    assert pl["ThrottleInterval"] == 30 and pl["RunAtLoad"] is True
    assert "follow-capture" in text and "Replacing follow-capture" in text, \
        "the plist must point at the job it replaces (its old label is named in speakers/README.md only)"
