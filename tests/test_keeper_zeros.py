"""EARS-341 (EARS-226 item 3): a mic that gives only zeros is DEAF, not a quiet room.

A lid-shut built-in mic, a mic with no TCC grant or a stream that stopped delivering sound all
hand the capture frames of zeros. Frames arrive, so the capture never says "no audio": it beats
with ``peak: 0``, and the keeper used to count it as hearing. Now EARS_ZERO_BEATS (4) of the
room capture's beats in a row with peak 0 is ONE ``ears: DEAF, mic gives only zeros`` mail
through the deaf path, then ONE reopen (a stop, and the loop starts a fresh capture at once,
not held by the restart backoff), then a hold: no second reopen, no restart loop. A beat with
peak > 0 is ONE recovery mail. A quiet room (a low peak, never 0) says nothing.

These run the keeper's deaf, heard, lag, stt, zeros and beat regions alone. The heard log is a
tmp file; ``note``, ``mail_me``, ``$HEARD``, ``capture_pid``/``capture_arg`` and
``stop_capture`` are stubs; the clock is ``EARS_NOW``. ``step T`` is one keeper loop at time T,
in the real loop's order: beat_track, heard_watch, beat_check, zeros_check, then (no capture
running) ``restart_due`` and a start. The stub ``stop_capture`` writes the stop row the capture
writes when it honours its STOP file (``reason: stop``). Nothing is mailed, signalled or started.
"""
import json
import os
import re
import subprocess
from datetime import date, timedelta, timezone
from pathlib import Path

from tests._anchor import ANCHOR_SUMMER

# every row's stamp: the summer anchor at the +11:00 it was written with (nothing compares it)
ROW_TS = ANCHOR_SUMMER.astimezone(timezone(timedelta(hours=11))).isoformat(timespec="milliseconds")

ROOT = Path(__file__).resolve().parents[1]
KEEPER = ROOT / "keeper" / "ears-keeper.sh"

ROOM, ROOM_SESSION, CALL_SESSION = 4242, "room-s", "call-ears-3"
FRESH = (5151, 6161, 7171)   # the pids the loop's starts give, in turn


def _region(name: str) -> str:
    s = KEEPER.read_text()
    return s[s.index(f"# --- {name}:"):s.index(f"# --- end {name} ---")]


def _regions() -> str:
    s = KEEPER.read_text()
    return _region("deaf") + s[s.index("# --- heard:"):s.index("# --- end beat ---")]   # heard .. beat


PRE = r'''
note() { echo "NOTE $*"; }
mail_me() { echo "MAIL $*"; }
heard_stub() { shift 2; echo "HEARD $*" >&3; }
HEARD=heard_stub; exec 3>&1
ROOM_PID=""; FRESH="5151 6161 7171"
capture_pid() { echo "$ROOM_PID"; }
capture_arg() { [ -n "$ROOM_PID" ] || return 0
  # as the keeper's own reads argv back: split on spaces, so a device is its FIRST word only
  case "$1" in --session) echo "$ROOM_SESSION" ;; --device) d="${DEV:-AirPods Pro}"; echo "${d%% *}" ;; esac; }
stop_capture() { echo "STOP $ROOM_PID"; [ -n "${STOP_ROW:-}" ] && cat "$STOP_ROW" >>"$LOG"; ROOM_PID=""; }
step() { EARS_NOW="$1"; beat_track "$(capture_pid)"; heard_watch; beat_check; zeros_check
  if [ -z "$ROOM_PID" ] && restart_due; then
    set -- $FRESH; ROOM_PID="$1"; shift; FRESH="$*"; echo "START $ROOM_PID"
    mail_unless_deaf "ears: capture restarted on AirPods Pro at 10:00 EOM"; fi; }
'''


def _log(d: Path) -> Path:
    return d / f"heard_{date.today():%Y-%m-%d}.jsonl"


def _row(r: dict) -> str:
    return json.dumps({"ts": ROW_TS, "seq": 1, "v": 1, **r}) + "\n"


def _bash(script: str, d: Path, stop_row: bool = True, **env) -> str:
    stop = d / "stop-row.txt"
    stop.write_text(_row({"kind": "event", "event": "listen-stopped", "reason": "stop", "source": "mic",
                          "device": "AirPods Pro", "session": ROOM_SESSION, "turns": 0}))
    e = dict(os.environ, EARS_HEARD_DIR=str(d), EARS_SESSION=ROOM_SESSION, ROOM_SESSION=ROOM_SESSION,
             LOG=str(_log(d)), **env)
    if stop_row:
        e["STOP_ROW"] = str(stop)
    out = subprocess.run(["bash", "-c", "set -uo pipefail\n" + _regions() + PRE + script],
                         env=e, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return out.stdout


def beat(pid=ROOM, peak=0, session=ROOM_SESSION, source="mic", device="AirPods Pro", rms=0.0) -> dict:
    row = {"kind": "event", "event": "heartbeat", "source": source, "device": device, "session": session,
           "period": 15.0, "rms": rms, "rms_dbfs": -120.0 if rms == 0 else -70.0}
    if pid is not None:
        row["pid"] = pid
    if peak is not None:
        row["peak"] = peak
    return row


def _add(d: Path, *recs: dict) -> str:
    """Append rows as heard writes them; the bash line that does it."""
    f = d / f"add{len(list(d.glob('add*')))}.txt"
    f.write_text("".join(_row(r) for r in recs))
    return f'cat "{f}" >>"{_log(d)}"'


def _beats(d: Path, t0: int, n: int, **kw) -> str:
    """n loops 15 s apart from t0, a room beat read on each; the bash for them."""
    return "".join(f"{_add(d, beat(**kw))}; step {t0 + 15 * i}; " for i in range(n))


def _lines(out: str, prefix: str) -> list[str]:
    return [l[len(prefix) + 1:] for l in out.splitlines() if l.startswith(prefix + " ")]


def _deaf_mails(out: str) -> list[str]:
    return [m for m in _lines(out, "MAIL") if "DEAF" in m]


START = f"ROOM_PID={ROOM}; heard_start; step 1000; "


# -- the criterion: one DEAF mail, ONE reopen, a hold, one recovery mail -----------------------

def test_zero_beats_are_one_deaf_mail_one_reopen_a_hold_and_one_recovery(tmp_path):
    script = (START + _beats(tmp_path, 1015, 3) + "echo =FOURTH=; " + _beats(tmp_path, 1060, 1)
              + "echo =FRESH=; " + _beats(tmp_path, 1075, 12, pid=FRESH[0])
              + "echo =SOUND=; " + _beats(tmp_path, 1300, 2, pid=FRESH[0], peak=212, rms=3.1))
    out = _bash(script, tmp_path)
    three, rest = out.split("=FOURTH=")
    fourth, rest = rest.split("=FRESH=")
    fresh, sound = rest.split("=SOUND=")
    assert "MAIL" not in three and "STOP" not in three                 # 3 beats: not yet
    deaf = _deaf_mails(out)
    assert len(deaf) == 1, out
    assert re.fullmatch(r"ears: DEAF, mic gives only zeros on AirPods Pro at \d\d:\d\d EOM", deaf[0]), deaf
    assert len(deaf[0]) <= 64
    # the 4th beat: DEAF, the ONE reopen, and the fresh capture starts in the same loop
    assert _deaf_mails(fourth) == deaf
    assert _lines(fourth, "STOP") == [str(ROOM)] and _lines(fourth, "START") == [str(FRESH[0])]
    assert "NOTE not mailed, the room is DEAF (zeros): ears: capture restarted" in fourth
    # the fresh capture gives only zeros too: held. No mail, no second reopen, said once
    assert "MAIL" not in fresh and "STOP" not in fresh and "START" not in fresh, fresh
    assert fresh.count("still only zeros after the reopen: holding") == 1
    assert "next start in" not in out                                   # the reopen armed no backoff
    # a beat with sound: ONE recovery mail
    mails = _lines(sound, "MAIL")
    assert len(mails) == 1 and mails[0].startswith("ears: hearing again on AirPods Pro at "), out
    assert len(_lines(out, "MAIL")) == 2 and _lines(out, "STOP") == [str(ROOM)]
    heard = _lines(out, "HEARD")
    assert [h.split()[0] for h in heard] == ["ears-deaf", "ears-hearing"]
    assert heard[0] == "ears-deaf --device AirPods Pro --text room: zeros: 4 beats of peak 0"
    assert "room: hearing again, was zeros for " in heard[1]


def test_a_quiet_room_is_not_deaf(tmp_path):
    """A low peak and a low level, for many minutes: still hearing. Only peak == 0 counts."""
    out = _bash(START + _beats(tmp_path, 1015, 40, peak=1, rms=0.2) + _beats(tmp_path, 1700, 10, peak=7, rms=0.9),
                tmp_path)
    assert "MAIL" not in out and "STOP" not in out and "HEARD" not in out


def test_a_beat_with_sound_breaks_the_run(tmp_path):
    script = START
    for k in range(5):
        t = 1015 + 60 * k
        script += _beats(tmp_path, t, 3) + _beats(tmp_path, t + 45, 1, peak=3)
    out = _bash(script, tmp_path)
    assert "MAIL" not in out and "STOP" not in out


def test_beats_that_are_not_the_room_captures_never_count(tmp_path):
    others = [dict(pid=7777, source="call:3", session=CALL_SESSION),   # a call leg
              dict(pid=None, source="call:3"),                          # no pid, a call leg's source
              dict(pid=None, session=CALL_SESSION),                     # no pid, another session
              dict(pid=7777)]                                           # another mic capture
    for i, kw in enumerate(others):
        d = tmp_path / f"case{i}"
        d.mkdir()
        out = _bash(START + "".join(f"{_add(d, beat(**kw), beat(peak=9))}; step {1015 + 15 * j}; "
                                    for j in range(8)), d)
        assert "MAIL" not in out and "STOP" not in out, kw


def test_a_beat_with_no_pid_from_the_room_session_counts(tmp_path):
    """G-R2: a capture adopted across the upgrade has no pid on its beats; its zeros count."""
    out = _bash(START + _beats(tmp_path, 1015, 4, pid=None), tmp_path)
    assert len(_deaf_mails(out)) == 1 and _lines(out, "STOP") == [str(ROOM)]


def test_a_beat_with_no_peak_says_nothing_either_way(tmp_path):
    out = _bash(START + _beats(tmp_path, 1015, 2) + _beats(tmp_path, 1045, 3, peak=None) + _beats(tmp_path, 1090, 1),
                tmp_path)
    assert "MAIL" not in out                                    # 2 + 1 zeros, the no-peak beats between
    d = tmp_path / "two"; d.mkdir()
    out = _bash(START + _beats(d, 1015, 3) + _beats(d, 1060, 2, peak=None) + _beats(d, 1090, 1), d)
    assert len(_deaf_mails(out)) == 1                           # nor do they break a run


# -- the ONE reopen ------------------------------------------------------------------------------

def test_the_reopen_is_not_held_by_a_backoff_armed_before_it(tmp_path):
    """A capture that ended by itself and heard nothing armed a wait (30 s); the next one ran, and
    gave only zeros. Its reopen starts the fresh capture at once and arms no wait of its own."""
    eof = {"kind": "event", "event": "listen-stopped", "reason": "eof", "source": "mic",
           "device": "AirPods Pro", "session": ROOM_SESSION}
    script = (f"ROOM_PID={ROOM}; heard_start; step 900; {_add(tmp_path, eof)}; ROOM_PID=''; step 901; step 931; echo =RUN=; "
              + _beats(tmp_path, 1000, 4, pid=FRESH[0]) + "echo =AFTER=; " + _beats(tmp_path, 1060, 3, pid=FRESH[1]))
    out = _bash(script, tmp_path)
    before, rest = out.split("=RUN=")
    run, after = rest.split("=AFTER=")
    assert "next start in 30s" in before and _lines(before, "START") == [str(FRESH[0])]
    assert _lines(run, "STOP") == [str(FRESH[0])] and _lines(run, "START") == [str(FRESH[1])]
    assert "next start in" not in run + after


def test_a_reopen_that_needed_a_kill_is_not_held_either(tmp_path):
    """A capture stopped hard (SIGKILL) writes no stop row: the loop still starts one at once."""
    out = _bash(START + _beats(tmp_path, 1015, 4) + _beats(tmp_path, 1075, 2, pid=FRESH[0]), tmp_path, stop_row=False)
    assert _lines(out, "STOP") == [str(ROOM)] and _lines(out, "START") == [str(FRESH[0])]


def test_a_capture_gone_before_its_reopen_is_not_stopped_again(tmp_path):
    """Gone between its 4th beat and the check (it died, or the beat region stopped it hard):
    the loop's next start is the reopen, and nothing is stopped."""
    out = _bash(f"ROOM_PID={ROOM}; heard_start; beat_track {ROOM}; "
                f"for i in 1 2 3 4; do zeros_seen {ROOM} 0 'AirPods Pro'; done; ROOM_PID=''; zeros_check", tmp_path)
    assert "STOP" not in out and "it is gone already: the next start is the reopen" in out
    assert len(_deaf_mails(out)) == 1


def test_a_new_episode_may_reopen_once_again(tmp_path):
    script = (START + _beats(tmp_path, 1015, 4) + _beats(tmp_path, 1075, 1, pid=FRESH[0], peak=50)
              + "echo =TWO=; " + _beats(tmp_path, 1200, 4, pid=FRESH[0]))
    out = _bash(script, tmp_path)
    one, two = out.split("=TWO=")
    assert _lines(one, "STOP") == [str(ROOM)] and _lines(two, "STOP") == [str(FRESH[0])]
    assert len(_deaf_mails(one)) == 1 and len(_deaf_mails(two)) == 1
    assert _lines(one, "MAIL")[-1].startswith("ears: hearing again on AirPods Pro at ")


# -- the lid's hook (do-007), the seam, the subject, the wiring -----------------------------------

def test_the_lid_hook_takes_the_cause_and_this_region_does_nothing(tmp_path):
    """do-007 wires zeros_lid to the lid probe: when it says the lid is shut on the built-in,
    the cause is `lid`, not zeros, and the lid region reports and holds."""
    out = _bash("zeros_lid() { [ \"$1\" = MacBook ]; }; " + START   # capture_arg's first word
                + _beats(tmp_path, 1015, 6, device="MacBook Pro Microphone"), tmp_path, DEV="MacBook Pro Microphone")
    assert "MAIL" not in out and "STOP" not in out and "HEARD" not in out
    assert "beats of peak 0, and the lid is shut: the lid's" in out


def test_the_hook_asks_the_lid_region():
    """do-007 wired it: the built-in (by transport) with the lid shut; tests/test_keeper_lid.py runs it."""
    hook = _region("zeros").split("zeros_lid() {", 1)[1].split("\n}", 1)[0]
    assert 'lid_shut "$1"' in hook


def test_the_threshold_is_an_env_seam(tmp_path):
    out = _bash(START + _beats(tmp_path, 1015, 2), tmp_path, EARS_ZERO_BEATS="2")
    assert len(_deaf_mails(out)) == 1
    d = tmp_path / "bad"; d.mkdir()
    out = _bash(START + _beats(d, 1015, 3) + "echo =FOUR=; " + _beats(d, 1060, 1), d, EARS_ZERO_BEATS="x")
    assert "MAIL" not in out.split("=FOUR=")[0] and len(_deaf_mails(out)) == 1   # not a number: 4


def test_the_zeros_subject_fits_64_for_any_device():
    devs = ["AirPods Pro", "MacBook Pro Microphone", "Somebody with a very long device name indeed", ""]
    script = "note() { :; }\n" + _region("deaf") + "\n" + "".join(f"deaf_subject room zeros '4 beats of peak 0' '{d}'\n"
                                                               for d in devs)
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30, check=True).stdout
    lines = out.splitlines()
    assert len(lines) == 4
    for s in lines:
        assert len(s) <= 64 and s.startswith("ears: DEAF, mic gives only zeros") and s.endswith(" EOM"), s
    assert re.fullmatch(r"ears: DEAF, mic gives only zeros on MacBook Pro Mic at \d\d:\d\d EOM", lines[1])
    assert re.fullmatch(r"ears: DEAF, mic gives only zeros at \d\d:\d\d EOM", lines[3])


def test_zeros_ranks_last_so_any_other_cause_names_the_episode(tmp_path):
    out = _bash("deaf_set room zeros x Mic; deaf_set room stt 500 Mic; echo CAUSE=$(deaf_cause room)", tmp_path)
    assert "CAUSE=stt" in out and len(_lines(out, "MAIL")) == 1


def test_the_loop_reopens_after_judging_the_beats_and_before_anything_can_skip_it():
    s = KEEPER.read_text()
    loop = s[s.index("while :; do"):]
    assert loop.index("heard_watch") < loop.index("beat_check") < loop.index("zeros_check") < loop.index("continue")
    assert s.index("# --- end stt ---") < s.index("# --- zeros:") < s.index("# --- end zeros ---") < s.index("# --- beat:")
    seen = s[s.index("beat_seen() {"):s.index("beat_check() {")]
    assert 'zeros_seen "$beat_pid" "$4" "$5"' in seen
    assert "zeros" in s[s.index("DEAF_ORDER="):].split("\n", 1)[0]
