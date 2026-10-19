"""EARS-226 item 5: a room capture whose heartbeat stopped is DEAF, and then wedged.

Sun 2030-06-09: a room capture was alive for 18 h, but its last heartbeat was 4h40m old,
and nothing said so. The keeper now judges its room capture by its heartbeats, with two
thresholds: EARS_BEAT_STALE (120 s) reports DEAF through the one deaf path, and EARS_BEAT_KILL
(300 s) stops the capture hard. These run the keeper's deaf, heard, lag and beat regions alone.
The heard log is a tmp file; ``note``, ``mail_me``, ``$HEARD`` and ``stop_capture`` are stubs;
the room capture is a pid in a shell variable (``capture_pid``/``capture_arg`` stubs); the clock
is ``EARS_NOW``. Nothing is mailed, signalled or started.
"""
import json
import os
import subprocess
from datetime import date, timedelta, timezone
from pathlib import Path

from tests._anchor import ANCHOR_SUMMER

# every row's stamp: the summer anchor at the +11:00 it was written with (nothing compares it)
ROW_TS = ANCHOR_SUMMER.astimezone(timezone(timedelta(hours=11))).isoformat(timespec="milliseconds")

ROOT = Path(__file__).resolve().parents[1]
KEEPER = ROOT / "keeper" / "ears-keeper.sh"

ROOM, ROOM_SESSION, CALL_SESSION = 4242, "room-s", "call-s"


def _regions() -> str:
    s = KEEPER.read_text()
    deaf = s[s.index("# --- deaf:"):s.index("# --- end deaf ---")]
    rest = s[s.index("# --- heard:"):s.index("# --- end beat ---")]   # heard, lag, beat
    return deaf + rest


# The room capture is $ROOM_PID. stop_capture, as the keeper's would, ends it; the loop's next
# start_capture is $NEXT_PID (empty: none starts). step T is one keeper loop at time T.
PRE = r'''
note() { echo "NOTE $*"; }
mail_me() { echo "MAIL $*"; }
heard_stub() { shift 2; echo "HEARD $*" >&3; }
HEARD=heard_stub; exec 3>&1
ROOM_PID=""; NEXT_PID=""
capture_pid() { echo "$ROOM_PID"; }
capture_arg() { [ -n "$ROOM_PID" ] || return 0
  case "$1" in --session) echo "$ROOM_SESSION" ;; --device) echo "AirPods" ;; esac; }
stop_capture() { echo "STOP $ROOM_PID"; ROOM_PID="$NEXT_PID"; }
step() { EARS_NOW="$1"; beat_track "$(capture_pid)"; heard_watch; beat_check; }
'''


def _bash(script: str, heard_dir: Path, **env) -> str:
    out = subprocess.run(["bash", "-c", "set -uo pipefail\n" + _regions() + PRE + script],
                         env=dict(os.environ, EARS_HEARD_DIR=str(heard_dir), ROOM_SESSION=ROOM_SESSION, **env),
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return out.stdout


def _log(d: Path) -> Path:
    return d / f"heard_{date.today():%Y-%m-%d}.jsonl"


def beat(pid=ROOM, session=ROOM_SESSION, source="mic", device="AirPods Pro", peak=37) -> dict:
    row = {"kind": "event", "event": "heartbeat", "source": source, "device": device, "session": session,
           "period": 15.0, "peak": peak}
    if pid is not None:
        row["pid"] = pid
    return row


def _add(d: Path, *recs: dict) -> str:
    """Append rows as heard writes them; the bash line that does it."""
    lines = "".join(json.dumps({"ts": ROW_TS, "seq": 1, "v": 1, **r}) + "\n"
                    for r in recs)
    f = d / f"add{len(list(d.glob('add*')))}.txt"
    f.write_text(lines)
    return f'cat "{f}" >>"{_log(d)}"'


def _lines(out: str, prefix: str) -> list[str]:
    return [l[len(prefix) + 1:] for l in out.splitlines() if l.startswith(prefix + " ")]


def _between(out: str, a: str, b: str) -> str:
    return out.split(a, 1)[1].split(b, 1)[0]


# -- the criterion: one DEAF mail at 120 s, a stop at 300 s, one recovery mail ------------------

def test_a_stopped_heartbeat_is_one_deaf_mail_at_stale_a_stop_at_kill_and_one_recovery(tmp_path):
    out = _bash(f"ROOM_PID={ROOM}; NEXT_PID=5151; heard_start; step 1000; {_add(tmp_path, beat())}; step 1000; "
                "echo T90; step 1090; echo T121; step 1121; echo T200; step 1200; echo T301; step 1301; "
                f"echo T305; step 1305; {_add(tmp_path, beat(pid=5151))}; echo T320; step 1320; "
                "echo T400; step 1400", tmp_path)
    mails = _lines(out, "MAIL")
    assert len(mails) == 2, out
    assert mails[0].startswith("ears: DEAF, no heartbeat since ") and mails[0].endswith(" on AirPods Pro EOM")
    assert len(mails[0]) <= 64
    assert mails[1].startswith("ears: hearing again on AirPods Pro at ")
    assert _lines(out, "STOP") == [str(ROOM)]                            # one hard stop, of the room
    assert "MAIL" not in _between(out, "T90", "T121")                    # 90 s: a slow turn end
    assert "MAIL ears: DEAF" in _between(out, "T121", "T200")            # 121 s: report ...
    assert "STOP" not in out.split("T301")[0]                            # ... and no stop yet
    assert "STOP" in _between(out, "T301", "T305") and "MAIL" not in _between(out, "T200", "T320")
    assert "MAIL ears: hearing again" in _between(out, "T320", "T400")   # the fresh capture's first beat
    assert [h.split()[0] for h in _lines(out, "HEARD")] == ["ears-deaf", "ears-hearing"]


def test_a_beat_after_stale_recovers_without_a_stop(tmp_path):
    out = _bash(f"ROOM_PID={ROOM}; heard_start; step 1000; {_add(tmp_path, beat())}; step 1000; step 1121; "
                f"{_add(tmp_path, beat())}; step 1130; step 1200", tmp_path)
    mails = _lines(out, "MAIL")
    assert len(mails) == 2 and mails[0].startswith("ears: DEAF, no heartbeat since ")
    assert mails[1].startswith("ears: hearing again on AirPods Pro at ")
    assert "STOP" not in out


def test_steady_beats_say_nothing(tmp_path):
    script = f"ROOM_PID={ROOM}; heard_start; step 1000; "
    script += "".join(f"{_add(tmp_path, beat())}; step {1000 + 15 * i}; " for i in range(1, 30))
    out = _bash(script, tmp_path)
    assert "MAIL" not in out and "STOP" not in out and "HEARD" not in out


def test_kill_with_no_report_before_it_is_still_one_mail(tmp_path):
    """A loop that first looks after KILL (the keeper was busy): one DEAF mail, then the stop."""
    out = _bash(f"ROOM_PID={ROOM}; heard_start; step 1000; step 1400; step 1401", tmp_path)
    assert len(_lines(out, "MAIL")) == 1 and _lines(out, "STOP") == [str(ROOM)]


def test_a_young_capture_has_the_full_grace_before_its_first_beat(tmp_path):
    out = _bash(f"ROOM_PID={ROOM}; heard_start; step 1000; step 1119; echo LATE; step 1121", tmp_path)
    assert "MAIL" not in out.split("LATE")[0] and "MAIL ears: DEAF" in out.split("LATE")[1]


def test_no_room_capture_is_never_judged(tmp_path):
    out = _bash(f"heard_start; step 1000; {_add(tmp_path, beat())}; step 1500; step 9999", tmp_path)
    assert "MAIL" not in out and "STOP" not in out


def test_a_new_capture_restarts_the_clock(tmp_path):
    """The FOLLOW swap gives a new pid: its grace starts when the keeper first sees it."""
    out = _bash(f"ROOM_PID={ROOM}; heard_start; step 1000; step 1100; ROOM_PID=6000; step 1110; step 1200", tmp_path)
    assert "MAIL" not in out


# -- whose beats count (G-R2) ------------------------------------------------------------------

def test_a_beat_with_no_pid_and_the_room_session_counts(tmp_path):
    """G-R2: a capture started before beats carried a pid, adopted across the upgrade."""
    out = _bash(f"ROOM_PID={ROOM}; heard_start; step 1000; {_add(tmp_path, beat(pid=None))}; step 1100; step 1219",
                tmp_path)
    assert "MAIL" not in out


def test_beats_that_are_not_the_room_captures_do_not_count(tmp_path):
    others = [beat(pid=None, session=CALL_SESSION),            # no pid, another session
              beat(pid=None, source="call:3"),                  # no pid, the room's session, a call leg's source
              beat(pid=7777, source="call:3", session=CALL_SESSION),   # a call leg with a pid
              beat(pid=7777)]                                   # another mic capture's pid
    for i, row in enumerate(others):
        d = tmp_path / f"case{i}"
        d.mkdir()
        out = _bash(f"ROOM_PID={ROOM}; heard_start; step 1000; {_add(d, row)}; step 1100; step 1121", d)
        assert "MAIL ears: DEAF, no heartbeat" in out, row


def test_a_beat_from_the_capture_before_a_swap_does_not_count(tmp_path):
    out = _bash(f"ROOM_PID={ROOM}; heard_start; step 1000; ROOM_PID=6000; step 1001; "
                f"{_add(tmp_path, beat(pid=ROOM))}; step 1100; step 1122", tmp_path)
    assert "MAIL ears: DEAF" in out


def test_a_beat_that_also_carries_a_lag_alarm_line_keeps_both(tmp_path):
    """One reader for both: a lag alarm in the same read is still mailed, and the beat still counts."""
    alarm = {"kind": "event", "event": "recogniser-lag", "lag_s": 64.2, "device": "AirPods Pro", "source": "mic"}
    out = _bash(f"ROOM_PID={ROOM}; heard_start; step 1000; {_add(tmp_path, beat(), alarm)}; step 1100; step 1219",
                tmp_path)
    assert _lines(out, "MAIL")[0].startswith("ears: recogniser 64 s behind on AirPods Pro at ")
    assert len(_lines(out, "MAIL")) == 1


# -- thresholds are env seams; the loop wires it ------------------------------------------------

def test_the_thresholds_are_env_seams(tmp_path):
    out = _bash(f"ROOM_PID={ROOM}; heard_start; step 1000; step 1011; echo =MARK=; step 1021", tmp_path,
                EARS_BEAT_STALE="10", EARS_BEAT_KILL="20")
    before, after = out.split("=MARK=")
    assert "MAIL ears: DEAF" in before and "STOP" not in before
    assert _lines(after, "STOP") == [str(ROOM)]


def test_the_loop_tracks_reads_and_judges_before_anything_can_skip_it():
    s = KEEPER.read_text()
    loop = s[s.index("while :; do"):]
    i_track, i_read, i_judge = loop.index("beat_track"), loop.index("heard_watch"), loop.index("beat_check")
    assert i_track < i_read < i_judge < loop.index("continue")
    assert "heard_start" in s[:s.index("while :; do")]
