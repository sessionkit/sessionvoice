"""EARS-226 item 6: a keeper restart adopts only a capture that still beats; no launcher shell
outlives a capture's launch; one keeper loop at a time.

Sun 2030-06-09: a kickstarted keeper adopted a capture whose heartbeat had been dead for
4h40m, and an "old keeper shell" (ppid 1) sat beside it: the capture's launcher subshell
(design B6). These run the keeper's own regions alone: ``note``, ``mail_me``, ``$HEARD``,
``capture_arg`` and ``stop_capture`` are stubs where named; the heard log is a tmp dir; the
"captures" and "keepers" are fake processes in tmp_path, started through a shell that exits at
once (so they are reaped, as launchd reaps). No real capture or keeper is signalled, and no STOP
file is written.
"""
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
KEEPER = ROOT / "keeper" / "ears-keeper.sh"

ROOM, ROOM_SESSION, CALL_SESSION = 4242, "room-s", "call-ears-3"


def _region(name: str) -> str:
    s = KEEPER.read_text()
    return s[s.index(f"# --- {name}:"):s.index(f"# --- end {name} ---")]


def _adopt_regions() -> str:
    return "".join(_region(n) for n in ("deaf", "heard", "lid", "zeros", "beat", "adopt"))


STUBS = r'''
note() { echo "NOTE $*"; }
mail_me() { echo "MAIL $*"; }
heard_stub() { shift 2; echo "HEARD $*" >&3; }
HEARD=heard_stub; exec 3>&1
capture_arg() { case "$1" in --session) echo "$ROOM_SESSION" ;; --device) echo "MacBook" ;;
  --stop-file) echo "/r/STOP-capture-keeper-174406" ;; esac; }
stop_capture() { echo "STOP"; }
'''


def _bash(script: str, heard_dir: Path, **env) -> str:
    out = subprocess.run(["bash", "-c", "set -uo pipefail\n" + _adopt_regions() + STUBS + script],
                         env=dict(os.environ, EARS_HEARD_DIR=str(heard_dir), ROOM_SESSION=ROOM_SESSION, **env),
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return out.stdout


def _ts(ago_s: float) -> str:
    return (datetime.now().astimezone() - timedelta(seconds=ago_s)).isoformat(timespec="milliseconds")


def beat(ago_s, pid=ROOM, session=ROOM_SESSION, source="mic", device="AirPods Pro") -> dict:
    row = {"ts": _ts(ago_s), "seq": 1, "v": 1, "kind": "event", "event": "heartbeat", "source": source,
           "device": device, "session": session, "period": 15.0, "peak": 37}
    if pid is not None:
        row["pid"] = pid
    return row


def _write(d: Path, *rows: dict, day: date | None = None) -> None:
    f = d / f"heard_{(day or date.today()):%Y-%m-%d}.jsonl"
    with f.open("a") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _mails(out: str) -> list[str]:
    return [l[5:] for l in out.splitlines() if l.startswith("MAIL ")]


def _stops(out: str) -> int:
    return out.splitlines().count("STOP")


# -- adopt only a capture that still beats ---------------------------------------------------

def test_a_fresh_beat_is_adopted_with_no_mail(tmp_path):
    _write(tmp_path, beat(400), beat(10))
    out = _bash(f"proc_age() {{ echo 99999; }}; adopt_capture {ROOM}; echo rc=$? pid=$beat_pid", tmp_path)
    assert "rc=0" in out and f"pid={ROOM}" in out
    assert "MAIL" not in out and _stops(out) == 0
    assert f"NOTE adopting running capture {ROOM}: device 'AirPods Pro'" in out


def test_a_wedged_capture_is_not_adopted_one_mail_and_a_hard_stop(tmp_path):
    """The capture: alive, its last beat 280 minutes old."""
    _write(tmp_path, beat(280 * 60))
    out = _bash(f"proc_age() {{ echo 99999; }}; adopt_capture {ROOM}; echo rc=$?", tmp_path)
    assert "rc=1" in out and _stops(out) == 1
    assert f"NOTE not adopting wedged capture {ROOM} (last heartbeat " in out
    mails = _mails(out)
    assert len(mails) == 1 and mails[0].startswith("ears: DEAF, no heartbeat since ")
    assert mails[0].endswith(" on AirPods Pro EOM") and len(mails[0]) <= 64


def test_a_beat_between_stale_and_kill_is_adopted_and_reported_on_the_first_loop(tmp_path):
    """Provisional: the tracker's clock starts at the real last beat, so it reports at once
    and does not stop; the next beat is the recovery."""
    _write(tmp_path, beat(200))
    out = _bash(f"proc_age() {{ echo 99999; }}; adopt_capture {ROOM}; echo rc=$?; echo LOOP; beat_check; beat_check; echo BEAT; "
                f"beat_seen {ROOM} {ROOM_SESSION} mic 37 'AirPods Pro'", tmp_path)
    at_adopt, rest = out.split("LOOP")
    in_loop, recovered = rest.split("BEAT")
    assert "rc=0" in at_adopt and "MAIL" not in at_adopt
    assert len(_mails(in_loop)) == 1 and _stops(in_loop) == 0
    assert _mails(recovered)[0].startswith("ears: hearing again on AirPods Pro at ")


def test_after_a_wedge_the_fresh_captures_first_beat_is_one_recovery_mail(tmp_path):
    _write(tmp_path, beat(3600))
    out = _bash(f"proc_age() {{ echo 99999; }}; adopt_capture {ROOM}; beat_track ''; beat_track 5151; echo NEW; "
                f"beat_seen 5151 {ROOM_SESSION} mic 37 'AirPods Pro'; beat_seen 5151 {ROOM_SESSION} mic 37 x", tmp_path)
    before, after = out.split("NEW")
    assert len(_mails(before)) == 1
    assert len(_mails(after)) == 1 and _mails(after)[0].startswith("ears: hearing again on AirPods Pro")


# -- whose beat (G-R2) ------------------------------------------------------------------------

def test_a_fresh_beat_with_no_pid_and_the_room_session_is_adopted(tmp_path):
    """G-R2: a capture started before beats carried a pid, adopted across the upgrade."""
    _write(tmp_path, beat(10, pid=None))
    out = _bash(f"proc_age() {{ echo 99999; }}; adopt_capture {ROOM}; echo rc=$?", tmp_path)
    assert "rc=0" in out and "MAIL" not in out and _stops(out) == 0


@pytest.mark.parametrize("row", [
    beat(10, pid=None, session=CALL_SESSION),               # no pid, a call leg's session
    beat(10, pid=None, source="call"),                      # no pid, the room's session, a call's source
    beat(10, pid=7777, source="call", session=CALL_SESSION),  # a call leg with a pid
    beat(10, pid=7777),                                     # another capture's pid
], ids=["call-session", "call-source", "call-pid", "other-pid"])
def test_a_fresh_beat_that_is_not_the_room_captures_does_not_save_a_wedged_one(tmp_path, row):
    _write(tmp_path, beat(3600), row)
    out = _bash(f"proc_age() {{ echo 99999; }}; adopt_capture {ROOM}; echo rc=$?", tmp_path)
    assert "rc=1" in out and _stops(out) == 1 and len(_mails(out)) == 1


def test_a_beat_from_before_the_capture_started_is_not_its_own(tmp_path):
    """Review fix (do-004): the capture before an upgrade beat with no pid, in the room's session,
    and its last beat is an hour old. A capture 10 s old has not beaten yet: that old beat is not
    its own, so it is adopted, not condemned (a false DEAF mail and a hard stop)."""
    _write(tmp_path, beat(3600, pid=None))
    out = _bash(f"proc_age() {{ echo 10; }}; adopt_capture {ROOM}; echo rc=$?", tmp_path)
    assert "rc=0" in out and "MAIL" not in out and _stops(out) == 0
    assert "not its own" in out and "no heartbeat since it started at" in out
    # the same beat, for a capture older than it, still condemns it
    old = _bash(f"proc_age() {{ echo 7200; }}; adopt_capture {ROOM}; echo rc=$?", tmp_path)
    assert "rc=1" in old and _stops(old) == 1 and len(_mails(old)) == 1


def test_no_beat_young_capture_is_adopted_old_one_is_wedged(tmp_path):
    young = _bash(f"proc_age() {{ echo 30; }}; adopt_capture {ROOM}; echo rc=$?", tmp_path)
    assert "rc=0" in young and "MAIL" not in young
    old = _bash(f"proc_age() {{ echo 400; }}; adopt_capture {ROOM}; echo rc=$?", tmp_path)
    assert "rc=1" in old and _stops(old) == 1 and "no heartbeat since it started at" in old
    assert len(_mails(old)) == 1


def test_just_after_midnight_yesterdays_log_is_read(tmp_path):
    _write(tmp_path, beat(20), day=date.today() - timedelta(days=1))
    out = _bash(f"proc_age() {{ echo 99999; }}; adopt_capture {ROOM}; echo rc=$?", tmp_path)
    assert "rc=0" in out and "MAIL" not in out


def test_last_beat_reads_only_the_tail_and_skips_a_cut_line(tmp_path):
    rows = [beat(5000 - i, device=f"old{i}") for i in range(50)] + [beat(30, device="newest")]
    _write(tmp_path, *rows)
    out = _bash(f"last_beat {ROOM} {ROOM_SESSION}", tmp_path, EARS_BEAT_TAIL="700").strip()
    epoch, dev = out.split("\t")
    assert dev == "newest" and abs(int(epoch) - (time.time() - 30)) < 5
    none = _bash(f"last_beat 999 nobody", tmp_path).strip()
    assert none == ""


@pytest.mark.parametrize("etime,secs", [("00:07", 7), ("08:09", 489), ("01:02:03", 3723),
                                        ("2-03:04:05", 2 * 86400 + 3 * 3600 + 4 * 60 + 5)])
def test_proc_age_reads_ps_etime(tmp_path, etime, secs):
    assert _bash(f"ps() {{ echo '   {etime}'; }}; proc_age 1", tmp_path).strip() == str(secs)


def test_proc_age_of_a_real_process(tmp_path):
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        time.sleep(1.2)
        age = int(_bash(f"proc_age {p.pid}", tmp_path).strip())
        assert 1 <= age <= 10
    finally:
        p.kill(); p.wait()
    assert _bash("proc_age 999999", tmp_path).strip() == ""


# -- no launcher shell outlives a launch (design B6) -------------------------------------------

FAKE_PY = """#!/bin/sh
exec "{py}" -c 'import os,sys,time; open(sys.argv[1],"w").write(str(os.getpid())); time.sleep(60)' "{pidf}"
"""


def _args(pid: int) -> str:
    return subprocess.run(["ps", "-ww", "-o", "args=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()


def _ppid(pid: int) -> int:
    return int(subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True).stdout.strip())


def _launch(tmp_path: Path, region: str) -> tuple[int, Path]:
    """Run launch_capture from a script named like the keeper, as launchd runs it; return the
    fake capture's pid once the script has exited."""
    pidf = tmp_path / "capture.pid"
    py = tmp_path / "fakepy"
    py.write_text(FAKE_PY.format(py=sys.executable, pidf=pidf)); py.chmod(0o755)
    script = tmp_path / "keeperdir" / "ears-keeper.sh"
    script.parent.mkdir()
    script.write_text(region + '\nlaunch_capture "AirPods Pro" "$T/STOP-capture-keeper-1" ""\n')
    env = dict(os.environ, T=str(tmp_path), W=str(tmp_path), PY=str(py), MOD="ears.listen",
               LOGDIR=str(tmp_path), SILERO=str(tmp_path / "silero.onnx"))
    subprocess.run(["/bin/bash", str(script), "default"], env=env, check=True, timeout=30)
    for _ in range(50):
        if pidf.exists() and pidf.read_text():
            break
        time.sleep(0.1)
    return int(pidf.read_text()), script


def _survivors(script: Path) -> list[int]:
    ps = subprocess.run(["ps", "-axww", "-o", "pid=,args="], capture_output=True, text=True).stdout
    return [int(l.split(None, 1)[0]) for l in ps.splitlines() if str(script) in l]


def _kill(*pids: int) -> None:
    for p in pids:
        try:
            os.kill(p, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_the_launch_leaves_no_shell_with_the_keepers_argv(tmp_path):
    cap, script = _launch(tmp_path, _region("launch"))
    try:
        time.sleep(0.3)
        assert str(script) not in _args(_ppid(cap))   # the capture's parent is not a keeper bash
        assert _survivors(script) == []
    finally:
        _kill(cap)


def test_without_exec_a_keeper_shell_would_outlive_the_launch(tmp_path):
    """The detector above is real: the old form (no exec) leaves exactly the B6 orphan."""
    old = _region("launch").replace("&& exec nohup", "&& nohup")
    assert old != _region("launch")
    cap, script = _launch(tmp_path, old)
    left = _survivors(script)
    try:
        assert left and _ppid(cap) in left
    finally:
        _kill(cap, *left)


def test_start_capture_launches_through_launch_capture():
    s = KEEPER.read_text()
    body = s[s.index("start_capture() {"):s.index("# --- stop:")]
    assert 'launch_capture "$1" "$STOP" "$missed"' in body and "nohup" not in body
    code = [l for l in s.splitlines() if "nohup" in l and not l.lstrip().startswith("#")]
    assert len(code) == 1 and '( cd "$W" && exec nohup "$PY"' in code[0]


# -- one keeper loop ----------------------------------------------------------------------------

KEEPER_FAKE = "trap '' TERM\nwhile :; do sleep 0.2; done\n"      # a loop that ignores SIGTERM
KEEPER_FAKE_TERM = "while :; do sleep 0.2; done\n"                # dies on SIGTERM


def _spawn(*argv: str) -> int:
    out = subprocess.run(["/bin/sh", "-c", '"$@" </dev/null >/dev/null 2>&1 & echo $!', "sh", *argv],
                         capture_output=True, text=True, check=True)
    return int(out.stdout.strip())


def _alive(pid: int) -> bool:
    st = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(st) and not st.startswith("Z")


def _claim(tmp_path: Path, pidfile_text: str | None) -> tuple[str, str]:
    run = tmp_path / "run"; run.mkdir(exist_ok=True)
    pf = run / "ears-keeper.pid"
    if pidfile_text is not None:
        pf.write_text(pidfile_text)
    script = 'note() { echo "NOTE $*"; }\n' + _region("pidfile") + "\nkeeper_claim; echo ME=$$\n"
    out = subprocess.run(["bash", "-c", script], env=dict(os.environ, RUN=str(run), EARS_KEEPER_TERM_WAIT="1"),
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    me = out.stdout.strip().splitlines()[-1].split("=")[1]
    assert pf.read_text().strip() == me
    return out.stdout, me


@pytest.mark.parametrize("body,signal_used", [(KEEPER_FAKE_TERM, "SIGTERM"), (KEEPER_FAKE, "SIGKILL")])
def test_a_keeper_loop_the_pidfile_names_is_ended(tmp_path, body, signal_used):
    k = tmp_path / "keeper" / "ears-keeper.sh"
    k.parent.mkdir(); k.write_text(body)
    old = _spawn("/bin/bash", str(k), "default")
    try:
        time.sleep(0.4)
        out, _ = _claim(tmp_path, str(old))
        time.sleep(0.3)
        assert not _alive(old)
        assert re.search(rf"another keeper loop runs, pid {old}\b", out), out  # not pid {old}7
        assert ("SIGKILL" in out) == (signal_used == "SIGKILL")
    finally:
        _kill(old)


def test_a_pidfile_naming_something_else_is_left_alone(tmp_path):
    other = _spawn(sys.executable, "-c", "import time; time.sleep(30)", "/x/keeper/ears-keeper.sh")
    try:
        time.sleep(0.4)
        out, _ = _claim(tmp_path, str(other))
        assert _alive(other) and "another keeper" not in out
    finally:
        _kill(other)


@pytest.mark.parametrize("text", ["", "garbage", "999999"])
def test_a_stale_or_bad_pidfile_is_just_replaced(tmp_path, text):
    out, _ = _claim(tmp_path, text)
    assert "another keeper" not in out


def test_no_pidfile_is_written_fresh(tmp_path):
    _claim(tmp_path, None)


def test_release_removes_only_our_own(tmp_path):
    pf = tmp_path / "ears-keeper.pid"
    script = _region("pidfile") + '\necho $$ >"$KEEPER_PIDF"; keeper_release; [ -e "$KEEPER_PIDF" ] || echo GONE; ' \
        'echo 1 >"$KEEPER_PIDF"; keeper_release; cat "$KEEPER_PIDF"'
    out = subprocess.run(["bash", "-c", script], env=dict(os.environ, RUN=str(tmp_path)),
                         capture_output=True, text=True, timeout=30).stdout.split()
    assert out == ["GONE", "1"] and pf.read_text().strip() == "1"


def test_startup_claims_first_reads_then_adopts_only_fresh():
    s = KEEPER.read_text()
    start = s[s.index("# --- end pidfile ---"):s.index("while :; do")]
    assert start.index("keeper_claim") < start.index('if [ -s "$WATCH_PIDF" ]')
    assert start.index("heard_start") < start.index('adopt_capture "$adopted"')
    assert "trap 'watch_down; calls_down; keeper_release; exit 0' TERM INT" in start
    assert "trap 'watch_down; calls_down; keeper_release' EXIT" in start
