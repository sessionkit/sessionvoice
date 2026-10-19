"""EARS-192 fix-003: the keeper mails the capture's recogniser-lag alarm, once.

The capture writes ONE ``recogniser-lag`` event per episode into the heard log (src/ears/lag.py
LagAlarm); the keeper is the ears' mail path (mail_me), so it reads the log's new lines each
loop and mails the alarm, never more than one per EARS_LAG_MAIL_GAP seconds. These run the
keeper's lag block alone, as test_keeper_model.py does its model gate. The heard log is a tmp
file and mail_me is a stub: nothing is mailed and the real log is never read.
"""
import json
import os
import subprocess
from datetime import date, timedelta, timezone
from pathlib import Path

from tests._anchor import ANCHOR_SUMMER

ROOT = Path(__file__).resolve().parents[1]
KEEPER = ROOT / "keeper" / "ears-keeper.sh"


def _lag_block() -> str:
    """The heard reader (EARS-226: one reader for lag and heartbeats) and the lag mail."""
    s = KEEPER.read_text()
    return s[s.index("# --- heard:"):s.index("# --- end lag ---")]


def _bash(script: str, heard_dir: Path, **env) -> str:
    # beat_seen is the beat region's (test_keeper_beat.py), stt_heard and stt_stopped the stt
    # region's (test_keeper_stt_deaf.py): here those rows are only read past
    pre = ('note() { echo "NOTE $*"; }\nmail_me() { echo "MAIL $*"; }\nbeat_seen() { :; }\n'
           'stt_heard() { :; }\nstt_stopped() { :; }\n')
    out = subprocess.run(["bash", "-c", "set -uo pipefail\n" + pre + _lag_block() + "\n" + script],
                         env=dict(os.environ, EARS_HEARD_DIR=str(heard_dir), **env),
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return out.stdout


# every row's stamp: the summer anchor at the +11:00 it was written with (nothing compares it)
ROW_TS = ANCHOR_SUMMER.astimezone(timezone(timedelta(hours=11))).isoformat(timespec="milliseconds")


def _log(d: Path) -> Path:
    return d / f"heard_{date.today():%Y-%m-%d}.jsonl"


def _add(d: Path, *recs: dict) -> str:
    """Append records as heard writes them (json.dumps, one per line); the bash line that does it."""
    lines = "".join(json.dumps({"ts": ROW_TS, "seq": 1, "source": "mic",
                                "device": "AirPods Pro", "v": 1, **r}) + "\n" for r in recs)
    f = d / f"add{len(list(d.glob('add*')))}.txt"
    f.write_text(lines)
    return f'cat "{f}" >>"{_log(d)}"'


ALARM = {"kind": "event", "event": "recogniser-lag", "lag_s": 64.2, "threshold_s": 2.0}
OK = {"kind": "event", "event": "recogniser-lag-ok", "lag_s": 0.4, "lasted_s": 125.0}
BEAT = {"kind": "event", "event": "heartbeat", "lag_s": 64.0}
PART = {"kind": "partial", "text": "hang on recogniser-lag", "lag_s": 70.0}


def test_an_alarm_is_mailed_once(tmp_path):
    out = _bash(f"heard_start; {_add(tmp_path, BEAT, ALARM, BEAT, PART)}; heard_watch; heard_watch; heard_watch",
                tmp_path)
    mails = [l for l in out.splitlines() if l.startswith("MAIL ")]
    assert len(mails) == 1
    assert mails[0].startswith("MAIL ears: recogniser 64 s behind on AirPods Pro at ") and mails[0].endswith(" EOM")
    assert len(mails[0]) - len("MAIL ") <= 69  # a mail subject over 69 chars is refused


def test_alarms_from_before_the_keeper_started_are_not_news(tmp_path):
    out = _bash(f"{_add(tmp_path, ALARM)}; heard_start; heard_watch; {_add(tmp_path, BEAT)}; heard_watch", tmp_path)
    assert "MAIL" not in out


def test_a_log_that_appears_after_start_is_read_from_its_top(tmp_path):
    out = _bash(f"heard_start; heard_watch; {_add(tmp_path, ALARM)}; heard_watch", tmp_path)
    assert out.count("MAIL ") == 1


def test_a_flapping_lag_is_one_mail_per_gap(tmp_path):
    """Three episodes inside the gap: one mail, and the log notes the two it held back."""
    out = _bash(f"heard_start; {_add(tmp_path, ALARM, OK)}; heard_watch; {_add(tmp_path, ALARM, OK)}; heard_watch; "
                f"{_add(tmp_path, ALARM)}; heard_watch", tmp_path)
    assert out.count("MAIL ") == 1
    assert out.count("not mailed, one was sent") == 2
    assert out.count("NOTE recogniser caught up on AirPods Pro after 125 s") == 2


def test_after_the_gap_a_new_episode_mails_again(tmp_path):
    out = _bash(f"heard_start; {_add(tmp_path, ALARM)}; heard_watch; {_add(tmp_path, ALARM)}; heard_watch",
                tmp_path, EARS_LAG_MAIL_GAP="0")
    assert out.count("MAIL ") == 2


def test_a_line_still_being_written_waits_for_its_newline(tmp_path):
    whole = json.dumps({"seq": 1, **ALARM, "device": "mic"})
    half = tmp_path / "half.txt"
    half.write_text(whole[:30])
    rest = tmp_path / "rest.txt"
    rest.write_text(whole[30:] + "\n")
    log = _log(tmp_path)
    out = _bash(f'heard_start; cat "{half}" >>"{log}"; heard_watch; echo "OFF $heard_off"; '
                f'cat "{rest}" >>"{log}"; heard_watch', tmp_path)
    assert "OFF 0" in out and out.count("MAIL ") == 1


def test_only_the_alarm_event_mails(tmp_path):
    """A word that says "recogniser-lag", an ok, a bad line: none is an alarm."""
    out = _bash(f"heard_start; {_add(tmp_path, PART, OK, BEAT)}; echo 'not json recogniser-lag' >>\"{_log(tmp_path)}\"; "
                "heard_watch", tmp_path)
    assert "MAIL" not in out and "caught up" in out


def test_a_log_cut_short_is_read_again(tmp_path):
    out = _bash(f'heard_start; {_add(tmp_path, BEAT, BEAT, BEAT)}; heard_watch; : >"{_log(tmp_path)}"; '
                f"{_add(tmp_path, ALARM)}; heard_watch", tmp_path)
    assert out.count("MAIL ") == 1


def test_the_loop_watches_before_anything_can_skip_it():
    """heard_watch (was lag_watch) runs at the top of every loop, before the `continue`s for
    listening off and a live call: a call leg's capture can fall behind while the room mic is
    off."""
    s = KEEPER.read_text()
    loop = s[s.index("while :; do"):]
    assert loop.index("heard_watch") < loop.index("continue")
    assert "heard_start" in s[:s.index("while :; do")]


def test_a_long_device_and_a_long_lag_still_fit_a_subject(tmp_path):
    out = _bash(f"heard_start; {_add(tmp_path, {**ALARM, 'lag_s': 12345.6, 'device': 'Microsoft Teams Audio Device Long'})}; "
                "heard_watch", tmp_path)
    mail = next(l for l in out.splitlines() if l.startswith("MAIL "))
    assert "12346 s behind on Microsoft Teams Audi at " in mail
    assert len(mail) - len("MAIL ") <= 69
