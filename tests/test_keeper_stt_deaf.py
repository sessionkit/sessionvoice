"""EARS-341 (EARS-226 item 4): failing speech to text is DEAF, and room restarts back off.

A capture whose STT fails 3 times in a row stops itself (`listen-stopped reason=error`,
`error="RuntimeError: STT failed 3 times in a row: ... 500 ..."`). The keeper used to start
another one at once, which failed as soon as three chunks of speech reached the broken server:
a restart every ~30 s, each a `capture restarted` mail, and nothing ever said "STT failing".
Now EARS_STT_DEAF (2) such stops in a row is ONE `ears: DEAF, STT failing (500)` mail through
the deaf path, the restarts back off 30 s doubling to 5 min (none after a capture that heard),
and the first heard line (a partial or turn with text) is ONE recovery mail.

These run the keeper's deaf, heard, lag and stt regions alone. The heard log is a tmp file;
``note``, ``mail_me``, ``$HEARD`` and ``beat_seen`` are stubs; the clock is ``EARS_NOW``. The
room capture is a shell variable: ``step T`` is one keeper loop at time T, which reads the
heard log and then, with no capture running, starts one when ``restart_due`` lets it (the
keeper loop's own gate, ``restart_due && start_capture``). Nothing is mailed or started.
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

ROOM_SESSION = "room-s"
STT_500 = ("RuntimeError: STT failed 3 times in a row: whisper at http://127.0.0.1:2022: "
           "Server error '500 Internal Server Error'")


def _region(name: str) -> str:
    s = KEEPER.read_text()
    return s[s.index(f"# --- {name}:"):s.index(f"# --- end {name} ---")]


def _regions() -> str:
    s = KEEPER.read_text()
    return _region("deaf") + s[s.index("# --- heard:"):s.index("# --- end stt ---")]   # heard, lag, stt


# The room capture runs while UP=1. A start is the keeper's: a restart mail, through the deaf
# path's mail_unless_deaf as start_capture sends it. INPUT, when set, is the input the loop would
# start on (its `restart_due "$t"`); a start records it as start_capture does (room_input).
PRE = r'''
note() { echo "NOTE $*"; }
mail_me() { echo "MAIL $*"; }
heard_stub() { shift 2; echo "HEARD $*" >&3; }
HEARD=heard_stub; exec 3>&1
beat_seen() { :; }
UP=0
step() { EARS_NOW="$1"; heard_watch
  if [ "$UP" = 0 ] && restart_due "${INPUT:-}"; then UP=1; room_input="${INPUT:-}"; echo "START $1"
    mail_unless_deaf "ears: capture restarted on AirPods Pro at 10:00 EOM"; fi; }
'''


def _bash(script: str, heard_dir: Path, **env) -> str:
    e = dict(os.environ, EARS_HEARD_DIR=str(heard_dir), EARS_SESSION=ROOM_SESSION, **env)
    out = subprocess.run(["bash", "-c", "set -uo pipefail\n" + _regions() + PRE + script],
                         env=e, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return out.stdout


def _log(d: Path) -> Path:
    return d / f"heard_{date.today():%Y-%m-%d}.jsonl"


def stopped(error=STT_500, reason="error", session=ROOM_SESSION, source="mic") -> dict:
    row = {"kind": "event", "event": "listen-stopped", "reason": reason, "source": source,
           "device": "AirPods Pro", "session": session, "stream_s": 40.0, "wall_s": 40.1, "turns": 0}
    if error is not None:
        row["error"] = error
    return row


def said(text="hello there", kind="turn", session=ROOM_SESSION, source="mic") -> dict:
    return {"kind": kind, "text": text, "source": source, "device": "AirPods Pro", "session": session,
            "final": kind == "turn", "t0": 0.3, "t1": 1.5}


def _add(d: Path, *recs: dict) -> str:
    """Append rows as heard writes them, and the capture is gone; the bash line that does it."""
    lines = "".join(json.dumps({"ts": ROW_TS, "seq": 1, "v": 1, **r}) + "\n"
                    for r in recs)
    f = d / f"add{len(list(d.glob('add*')))}.txt"
    f.write_text(lines)
    gone = any(r.get("event") == "listen-stopped" and r.get("session") == ROOM_SESSION
               and r.get("source") == "mic" for r in recs)
    return f'cat "{f}" >>"{_log(d)}"' + ("; UP=0" if gone else "")


def _lines(out: str, prefix: str) -> list[str]:
    return [l[len(prefix) + 1:] for l in out.splitlines() if l.startswith(prefix + " ")]


def _starts(out: str) -> list[int]:
    return [int(t) for t in _lines(out, "START")]


def _deaf_mails(out: str) -> list[str]:
    return [m for m in _lines(out, "MAIL") if "DEAF" in m]


# -- the criterion: one DEAF mail, backed-off restarts, one recovery mail ----------------------

def test_stt_500_stops_are_one_deaf_mail_backed_off_restarts_and_one_recovery(tmp_path):
    # The keeper looks every 10 s here (the real loop: every second). Each 50th second, a
    # capture that is up dies of STT 500s, and that loop reads its listen-stopped.
    script = "heard_start; step 0; "
    for t in range(10, 1310, 10):
        if t % 50 == 0:
            script += f'if [ "$UP" = 1 ]; then {_add(tmp_path, stopped())}; echo "STOPPED {t}"; fi; '
        script += f"step {t}; "
    script += f"echo =GOOD=; step 2000; {_add(tmp_path, said(kind='partial'), said(), said())}; step 2001; step 2002"
    out = _bash(script, tmp_path)
    before, after = out.split("=GOOD=")
    assert [int(x) for x in _lines(before, "STOPPED")] == [50, 100, 200, 350, 600, 950, 1300], out
    stops = [int(x) for x in _lines(before, "STOPPED")]
    starts = _starts(before)
    assert starts[0] == 0
    # 30 s doubling to 300 after each stop, and never sooner
    assert [nxt - stp for stp, nxt in zip(stops, starts[1:])] == [30, 60, 120, 240, 300, 300], (stops, starts)
    deaf = _deaf_mails(out)
    assert len(deaf) == 1 and re.fullmatch(r"ears: DEAF, STT failing \(500\) at \d\d:\d\d EOM", deaf[0]), out
    assert len(deaf[0]) <= 64
    # one restart mail before the room was DEAF (the second stop opens it), then only notes
    assert _lines(before, "MAIL") == ["ears: capture restarted on AirPods Pro at 10:00 EOM"] * 2 + deaf
    assert before.count("NOTE not mailed, the room is DEAF (stt): ears: capture restarted") == 5
    mails = _lines(after, "MAIL")
    assert len(mails) == 1 and mails[0].startswith("ears: hearing again on AirPods Pro at "), out
    heard = _lines(out, "HEARD")
    assert [h.split()[0] for h in heard] == ["ears-deaf", "ears-hearing"]
    assert heard[0] == "ears-deaf --device AirPods Pro --text room: stt: 500"
    assert "room: hearing again, was stt for " in heard[1]


def test_one_stop_is_not_deaf_and_further_stops_send_no_second_mail(tmp_path):
    out = _bash(f"heard_start; step 0; {_add(tmp_path, stopped())}; step 1; echo =ONE=; "
                f"step 100; {_add(tmp_path, stopped())}; step 101; echo =TWO=; "
                f"step 300; {_add(tmp_path, stopped())}; step 301; "
                f"step 900; {_add(tmp_path, stopped())}; step 901", tmp_path)
    one, rest = out.split("=ONE=")
    two, more = rest.split("=TWO=")
    assert not _deaf_mails(one) and "NOTE capture stopped: STT failing (500), 1 in a row" in one
    assert len(_deaf_mails(two)) == 1
    assert not _lines(more, "MAIL") and "STT failing (500), 4 in a row" in more


def test_a_heard_line_between_two_stt_stops_breaks_the_run(tmp_path):
    out = _bash(f"heard_start; step 0; {_add(tmp_path, stopped())}; step 1; step 40; "
                f"{_add(tmp_path, said(), stopped())}; step 41; step 90", tmp_path)
    assert not _deaf_mails(out)
    assert _starts(out) == [0, 40, 41]          # a capture that heard: restarted with no wait


def test_a_non_stt_stop_does_not_count_and_breaks_the_run(tmp_path):
    rows = [stopped(error="RuntimeError: no audio for 5s"),     # the device went: not STT
            stopped(error=None, reason="eof"),
            stopped(error=None, reason="stop")]                 # the keeper's own STOP file
    for i, row in enumerate(rows):
        d = tmp_path / f"case{i}"
        d.mkdir()
        out = _bash(f"heard_start; step 0; {_add(d, stopped())}; step 1; step 40; {_add(d, row)}; step 41; "
                    f"step 200; {_add(d, stopped())}; step 201; step 400", d)
        assert not _deaf_mails(out), row


def test_a_call_leg_or_another_session_never_counts(tmp_path):
    others = [stopped(source="call:3", session="call-ears-3"), stopped(source="call:3"),
              stopped(session="somebody-else")]
    script = "heard_start; step 0; " + "".join(f"{_add(tmp_path, r)}; step {i + 1}; " for i, r in enumerate(others * 3))
    out = _bash(script, tmp_path)
    assert not _deaf_mails(out) and "STT failing" not in out
    assert _starts(out) == [0]                  # the room capture still runs: no restart, no wait


def test_a_heard_line_from_another_capture_does_not_clear(tmp_path):
    out = _bash(f"heard_start; step 0; {_add(tmp_path, stopped())}; step 30; {_add(tmp_path, stopped())}; "
                f"step 90; {_add(tmp_path, said(session='call-ears-3', source='call:3'))}; step 91; "
                f"{_add(tmp_path, said(text='   '), said(kind='partial', text=''))}; step 92; "
                "deaf_room && echo STILL-DEAF", tmp_path)
    assert "STILL-DEAF" in out and len(_lines(out, "MAIL")) == 2   # a restart, then the DEAF mail


def test_no_code_in_the_error_is_a_question_mark(tmp_path):
    err = "RuntimeError: STT failed 3 times in a row: whisper at http://127.0.0.1:2022: ConnectError"
    out = _bash(f"heard_start; step 0; {_add(tmp_path, stopped(error=err))}; step 30; "
                f"{_add(tmp_path, stopped(error=err))}; step 90", tmp_path)
    deaf = _deaf_mails(out)
    assert len(deaf) == 1 and deaf[0].startswith("ears: DEAF, STT failing (?) at "), out


def test_the_code_is_the_http_status_and_never_a_port_in_a_url(tmp_path):
    """do-005 review m1: the first 3-digit 4xx/5xx in the error was taken, so a base URL's
    :443 read as the code. URLs are cut out of the error first."""
    err = ("RuntimeError: STT failed 3 times in a row: whisper at https://stt.example.com:443: "
           "Server error '503 Service Unavailable' for url 'https://stt.example.com:443/v1/audio/transcriptions'")
    out = _bash(f"heard_start; step 0; {_add(tmp_path, stopped(error=err))}; step 30; "
                f"{_add(tmp_path, stopped(error=err))}; step 90", tmp_path)
    deaf = _deaf_mails(out)
    assert len(deaf) == 1 and deaf[0].startswith("ears: DEAF, STT failing (503) at "), out
    assert "HEARD ears-deaf --device AirPods Pro --text room: stt: 503" in out


def test_a_port_in_a_url_with_no_status_is_no_code(tmp_path):
    err = "RuntimeError: STT failed 3 times in a row: whisper at http://10.0.0.2:443/v1: ReadTimeout"
    out = _bash(f"heard_start; step 0; {_add(tmp_path, stopped(error=err))}; step 30; "
                f"{_add(tmp_path, stopped(error=err))}; step 90", tmp_path)
    deaf = _deaf_mails(out)
    assert len(deaf) == 1 and deaf[0].startswith("ears: DEAF, STT failing (?) at "), out


def test_the_stt_subject_fits_for_any_detail():
    out = subprocess.run(["bash", "-c", "note() { :; }\n" + _region("deaf") +
                          "\nfor c in 500 503 404 '?' 'a-very-long-detail-indeed'; do deaf_subject room stt \"$c\" "
                          "'Somebody with a very long device name indeed'; done"],
                         capture_output=True, text=True, timeout=30, check=True).stdout.splitlines()
    assert len(out) == 5
    for s in out:
        assert len(s) <= 64 and s.startswith("ears: DEAF, STT failing (") and s.endswith(" EOM"), s


# -- the backoff is wired into the restart path ------------------------------------------------

def test_a_capture_that_heard_resets_the_backoff(tmp_path):
    # the wait runs from the loop that read the stop
    out = _bash(f"heard_start; step 0; {_add(tmp_path, stopped())}; step 1; step 30; step 31; "
                f"{_add(tmp_path, stopped())}; step 32; step 91; step 92; "
                f"{_add(tmp_path, said(), stopped(error=None, reason='eof'))}; step 93; "
                f"step 94; {_add(tmp_path, stopped())}; step 95; step 124; step 125", tmp_path)
    assert _starts(out) == [0, 31, 92, 93, 125]   # 30, 60; heard: none; then 30 again, not 120


def test_a_stop_the_keeper_asked_for_is_never_held(tmp_path):
    """reason `stop`: the keeper's STOP file (listen off, a call, the FOLLOW swap): start at once."""
    out = _bash(f"heard_start; step 0; {_add(tmp_path, stopped(error=None, reason='stop'))}; step 1", tmp_path)
    assert _starts(out) == [0, 1] and "next start in" not in out


def test_a_waiting_restart_is_noted_once_not_every_loop(tmp_path):
    out = _bash(f"heard_start; step 0; {_add(tmp_path, stopped())}; " + "".join(f"step {i}; " for i in range(1, 32)),
                tmp_path)
    assert out.count("next start in 30s") == 1 and _starts(out) == [0, 31]


def test_a_new_follow_target_drops_the_wait(tmp_path):
    """do-005 review m2 (foreman's ruling): the backoff is for a loop on the SAME broken thing.
    A capture lost with its device in a quiet room heard nothing and armed a wait; the input
    FOLLOW moves to is a new device, so its start is not held for 30 s..5 min."""
    out = _bash(f"heard_start; step 0; {_add(tmp_path, stopped(error=None, reason='eof'))}; step 1; step 2; "
                "restart_reset 'USB Mic'; step 3", tmp_path)
    assert _starts(out) == [0, 3], out
    assert "NOTE input is now USB Mic: the restart wait (30s) is dropped" in out
    # and the wait after it starts again from the bottom, not where it was
    d = tmp_path / "again"; d.mkdir()
    out = _bash(f"heard_start; step 0; {_add(d, stopped())}; step 1; step 31; {_add(d, stopped())}; step 32; "
                f"restart_reset 'USB Mic'; step 33; {_add(d, stopped())}; step 34; step 63; step 64", d)
    assert _starts(out) == [0, 31, 33, 64], out   # 30, 60 dropped, then 30 again (not 120)


def test_a_reset_with_no_wait_pending_says_nothing(tmp_path):
    out = _bash("heard_start; step 0; restart_reset 'USB Mic'; step 1", tmp_path)
    assert "restart wait" not in out and _starts(out) == [0]


def test_the_old_captures_stop_row_read_after_the_move_does_not_hold_the_new_input(tmp_path):
    """do-006 review: the buds go, the keeper sees the move (restart_reset) first, and the old
    capture stalls out (`no audio for 5s`, a quiet room: heard nothing) before the loop starts on
    the new input. Its stop row arms a wait for the OLD input, which must not hold the new one."""
    lost = stopped(error="SourceError: input device 'AirPods Pro' delivered no audio for 5s")
    out = _bash(f"INPUT='AirPods Pro'; heard_start; step 0; INPUT='USB Mic'; restart_reset 'USB Mic'; step 1; "
                f"{_add(tmp_path, lost)}; step 5; step 6", tmp_path)
    assert _starts(out) == [0, 5], out


def test_a_wait_still_holds_a_restart_on_the_same_input(tmp_path):
    out = _bash(f"INPUT='AirPods Pro'; heard_start; step 0; {_add(tmp_path, stopped())}; step 1; step 30; step 31",
                tmp_path)
    assert _starts(out) == [0, 31], out


def test_on_another_input_the_doubling_starts_again(tmp_path):
    # no restart_reset here: the move is only seen through the input the loop would start on
    out = _bash(f"INPUT=A; heard_start; step 0; {_add(tmp_path, stopped())}; step 1; step 31; "
                f"{_add(tmp_path, stopped())}; step 32; INPUT=B; step 33; {_add(tmp_path, stopped())}; step 34; "
                "step 63; step 64", tmp_path)
    assert _starts(out) == [0, 31, 33, 64], out   # 30, 60 on A; B at once, then 30 (not 120)


def test_start_capture_records_the_input_a_wait_belongs_to():
    s = KEEPER.read_text()
    start = s[s.index("start_capture() {"):s.index("# --- stop:")]
    assert 'launch_capture "$1" "$STOP" "$missed"; room_input="$1"' in start


def test_the_loop_drops_the_wait_when_the_follow_target_changes():
    s = KEEPER.read_text()
    loop = s[s.index("while :; do"):]
    block = loop[loop.index('if [ "$FOLLOW" = 1 ] && [ "$t" != "$last_t" ]; then'):]
    block = block[:block.index("\n  fi\n")]
    assert 'restart_reset "$t"' in block, block


def test_the_threshold_is_an_env_seam(tmp_path):
    script = "heard_start; step 0; " + "".join(f"{_add(tmp_path, stopped())}; step {400 * i}; " for i in range(1, 4))
    assert len(_deaf_mails(_bash(script, tmp_path, EARS_STT_DEAF="3"))) == 1
    d = tmp_path / "two"; d.mkdir()
    script = "heard_start; step 0; " + "".join(f"{_add(d, stopped())}; step {400 * i}; " for i in range(1, 3))
    assert not _deaf_mails(_bash(script, d, EARS_STT_DEAF="3"))


# -- the keeper wires it ------------------------------------------------------------------------

def test_the_loop_gates_its_start_on_restart_due():
    s = KEEPER.read_text()
    loop = s[s.index("while :; do"):]
    assert 'restart_due "$t" && start_capture "$t"' in loop
    assert loop.index("heard_watch") < loop.index("restart_due")
    # the FOLLOW swap is the keeper's own stop: never held
    assert "stop_capture && start_capture \"$t\"" in loop


def test_room_session_is_the_session_the_capture_is_launched_with():
    s = KEEPER.read_text()
    expr = "${EARS_SESSION:-${CLAUDE_CODE_SESSION_ID:-ears-keeper}}"
    assert f'room_session() {{ echo "{expr}"; }}' in _region("stt")
    assert f'--session "{expr}"' in _region("launch")
    env = {k: v for k, v in os.environ.items() if k not in ("EARS_SESSION", "CLAUDE_CODE_SESSION_ID")}
    out = subprocess.run(["bash", "-c", "note() { :; }\n" + _region("deaf") + _region("stt") + "\nroom_session"],
                         env=env, capture_output=True, text=True, timeout=30, check=True).stdout
    assert out.strip() == "ears-keeper"


def test_the_reader_hands_stops_and_heard_lines_to_the_stt_region():
    w = _region("heard").split("heard_watch() {", 1)[1]
    assert 'stopped) stt_stopped "$a" "$b" "$c" "$d" "$e" "$g"' in w
    assert 'heard) stt_heard "$a" "$b" "$c" "$d"' in w
    s = KEEPER.read_text()
    assert s.index("# --- end lag ---") < s.index("# --- stt:") < s.index("# --- beat:")
