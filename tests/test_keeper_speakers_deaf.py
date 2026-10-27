"""EARS-341 (EARS-226 item 7, the report half): the speaker side-car hears nothing, and the keeper says so.

The side-car's mic stream can open, stay active and deliver nothing: every heard line then gets a
row `why: "no audio from 'DEV' (0% of the line covered)"` in speakers_DAY.jsonl, and 27k of them
went unread (design B7). The keeper's ONE heard python now reads that file too, and
EARS_SPEAKERS_DEAF_LINES (20) mic rows in a row with 0% covered is ONE `ears: speakers DEAF` mail;
a covered row is ONE recovery. Muted while the room is DEAF or no room capture runs.

These run the keeper's deaf region and its heard .. beat span (heard, lag, stt, lid, zeros,
speakers, beat) as the keeper has them. Stubbed: note, mail_me, $HEARD, capture_pid and capture_arg
(a fake room capture). The rows are written as speaker-sidecar2.py's record() writes them. Nothing
under speakers/ is run or edited.
"""
import json
import os
import re
import subprocess
from datetime import date, timedelta, timezone
from pathlib import Path

import pytest

from tests._anchor import ANCHOR, ANCHOR_SUMMER

ROOT = Path(__file__).resolve().parents[1]
KEEPER = ROOT / "keeper" / "ears-keeper.sh"
DEV = "AirPods Pro"
ZERO_WHY = f"no audio from {DEV!r} (0% of the line covered)"
ZEROS_WHY = "no signal: the device gave only zeros (100% of the line covered)"


def _region(name: str) -> str:
    s = KEEPER.read_text()
    return s[s.index(f"# --- {name}:"):s.index(f"# --- end {name} ---")]


def _program() -> str:
    s = KEEPER.read_text()
    return _region("deaf") + s[s.index("# --- heard:"):s.index("# --- end beat ---")]


PRE = r'''
note() { echo "NOTE $*"; }
mail_me() { echo "MAIL $*"; }
heard_stub() { shift 2; echo "HEARD $*" >&3; }
HEARD=heard_stub; exec 3>&1
python3() { echo x >>"$T/py"; command python3 "$@"; }   # counts the reader's python runs
ROOM_PID=4242
capture_pid() { echo "$ROOM_PID"; }
capture_arg() { [ -n "$ROOM_PID" ] || return 0
  case "$1" in --session) echo room-s ;; --device) echo AirPods ;; esac; }
step() { beat_track "$(capture_pid)"; heard_watch; }
heard_start
'''


def _bash(script: str, tmp: Path, **env) -> str:
    e = dict(os.environ, EARS_HEARD_DIR=str(tmp), T=str(tmp), EARS_SESSION="room-s")
    e.pop("EARS_SPEAKERS_DEAF_LINES", None); e.pop("EARS_SPEAKERS_SILENT_TURNS", None)
    e.update(env)
    out = subprocess.run(["bash", "-c", "set -uo pipefail\n" + _program() + PRE + script],
                         env=e, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "command not found" not in out.stderr, out.stderr
    return out.stdout


def _spk(tmp: Path) -> Path:
    return tmp / f"speakers_{date.today():%Y-%m-%d}.jsonl"


_n = [0]


# every row's stamp: the summer anchor at the +11:00 it was written with (nothing compares it)
ROW_AT = ANCHOR_SUMMER.astimezone(timezone(timedelta(hours=11))).isoformat(timespec="milliseconds")


def _rows(tmp: Path, n: int, why: str | None = ZERO_WHY, **over) -> str:
    """A bash line that appends n rows as speaker-sidecar2.py's record() writes them."""
    lines = ""
    for _ in range(n):
        _n[0] += 1
        r = {"seq": _n[0], "kind": "partial", "speaker": "unheard" if why else "theo", "fracs": {},
             "speech_s": 0.0, "session": "room-s", "device": DEV, "t0": 1.0, "t1": 2.0, "ms": 0.0,
             "lag_s": 0.4, "refs": ["theo"], "by": "speaker-sidecar2", "at": ROW_AT,
             "v": 2, **({"why": why} if why else {}), **over}
        lines += json.dumps(r) + "\n"
    f = tmp / f"rows{_n[0]}.txt"
    f.write_text(lines)
    return f'cat "{f}" >>"{_spk(tmp)}"; '


def _mails(out: str) -> list[str]:
    return [l[5:] for l in out.splitlines() if l.startswith("MAIL ")]


def _spk_mails(out: str) -> list[str]:
    return [m for m in _mails(out) if m.startswith("ears: speakers")]


# -- the criterion: ONE mail at the threshold, ONE on recovery ----------------------------------

def test_twenty_rows_with_none_covered_are_one_speakers_deaf_mail(tmp_path):
    out = _bash(_rows(tmp_path, 19) + "step; echo =AT19=; " + _rows(tmp_path, 1) + "step; "
                + _rows(tmp_path, 25) + "step; " + _rows(tmp_path, 5) + "step", tmp_path)
    before, after = out.split("=AT19=")
    assert not _mails(before)
    assert _mails(after) == [f"ears: speakers DEAF, 0% of 20 lines on {DEV} EOM"], out
    assert "HEARD ears-deaf --device AirPods Pro --text speakers: cover: 20" in after


def test_a_covered_row_is_one_recovery_mail(tmp_path):
    out = _bash(_rows(tmp_path, 20) + "step; echo =BACK=; " + _rows(tmp_path, 3, why=None) + "step; "
                + _rows(tmp_path, 5) + "step; " + _rows(tmp_path, 2, why=None) + "step", tmp_path)
    back = out.split("=BACK=")[1]
    mails = _mails(back)
    assert len(mails) == 1 and re.fullmatch(r"ears: speakers hearing again at \d\d:\d\d EOM", mails[0]), out
    assert "speakers: hearing again, was cover for" in back


def test_the_room_is_not_made_deaf_by_the_speakers(tmp_path):
    out = _bash(_rows(tmp_path, 20) + "step; deaf_room || echo ROOM-HEARS; deaf_on speakers && echo SPK-DEAF", tmp_path)
    assert "ROOM-HEARS" in out and "SPK-DEAF" in out


# -- muted: the room DEAF, or no room capture ---------------------------------------------------

def test_muted_while_the_room_is_deaf(tmp_path):
    out = _bash("deaf_set room stt 500 AirPods; " + _rows(tmp_path, 30) + "step; deaf_on speakers || echo SPK-OK",
                tmp_path)
    assert not _spk_mails(out) and "SPK-OK" in out, out
    assert len(_mails(out)) == 1   # the room's own stt mail only


def test_muted_while_no_room_capture_runs(tmp_path):
    out = _bash("ROOM_PID=''; " + _rows(tmp_path, 30) + "step; deaf_on speakers || echo SPK-OK", tmp_path)
    assert not _mails(out) and "SPK-OK" in out, out


def test_a_mute_starts_the_run_again(tmp_path):
    """15 rows, then 10 while no capture runs (muted), then 10: never 20 in a row unmuted."""
    out = _bash(_rows(tmp_path, 15) + "step; ROOM_PID=''; " + _rows(tmp_path, 10) + "step; ROOM_PID=4343; "
                + _rows(tmp_path, 10) + "step", tmp_path)
    assert not _mails(out), out


def test_unmuted_again_it_counts_afresh_and_reports(tmp_path):
    out = _bash("deaf_set room stt 500 AirPods; " + _rows(tmp_path, 10) + "step; deaf_clear room stt AirPods; "
                + _rows(tmp_path, 20) + "step", tmp_path)
    assert _spk_mails(out) == [f"ears: speakers DEAF, 0% of 20 lines on {DEV} EOM"], out


# -- which rows count ------------------------------------------------------------------------

def test_a_zeros_row_is_not_zero_coverage(tmp_path):
    """'no signal: the device gave only zeros' is covered: it breaks a run, and it ends an episode."""
    out = _bash(_rows(tmp_path, 19) + _rows(tmp_path, 1, why=ZEROS_WHY) + _rows(tmp_path, 19) + "step; "
                "echo =EP=; " + _rows(tmp_path, 20) + "step; " + _rows(tmp_path, 1, why=ZEROS_WHY) + "step", tmp_path)
    before, ep = out.split("=EP=")
    assert not _mails(before), out
    mails = _spk_mails(ep)
    assert len(mails) == 2 and mails[0].startswith("ears: speakers DEAF") and "hearing again" in mails[1], out


def test_partly_covered_is_covered(tmp_path):
    out = _bash(_rows(tmp_path, 19) + _rows(tmp_path, 1, why=f"no audio from {DEV!r} (30% of the line covered)")
                + _rows(tmp_path, 19) + "step", tmp_path)
    assert not _mails(out), out


def test_the_cover_field_wins_when_the_side_car_writes_it(tmp_path):
    """EARS-342 adds `cover`; until then the why's words decide. cover 0 counts, cover > 0 clears."""
    out = _bash(_rows(tmp_path, 20, why="no audio", cover=0) + "step; echo =BACK=; "
                + _rows(tmp_path, 1, why=ZERO_WHY, cover=0.4) + "step", tmp_path)
    held, back = out.split("=BACK=")
    assert len(_spk_mails(held)) == 1 and len(_spk_mails(back)) == 1 and "hearing again" in _spk_mails(back)[0], out


def test_a_calls_rows_are_not_the_rooms(tmp_path):
    out = _bash(_rows(tmp_path, 10) + _rows(tmp_path, 30, leg="call", device="call:7") + _rows(tmp_path, 9) + "step",
                tmp_path)
    assert not _mails(out), out
    d = tmp_path / "covered"; d.mkdir()
    out = _bash(_rows(d, 10) + _rows(d, 5, why=None, leg="call") + _rows(d, 10) + "step", d)
    assert len(_spk_mails(out)) == 1, out   # a call's covered rows do not break the room's run either


def test_a_row_that_says_no_coverage_neither_counts_nor_breaks(tmp_path):
    why = "the line's window is in the future: its stream start is wrong"
    out = _bash(_rows(tmp_path, 10) + _rows(tmp_path, 5, why=why) + _rows(tmp_path, 10) + "step", tmp_path)
    assert len(_spk_mails(out)) == 1, out


def test_bad_lines_are_skipped(tmp_path):
    bad = tmp_path / "bad.txt"
    bad.write_text('not json\n[1,2]\n{"why": 5}\n')
    out = _bash(_rows(tmp_path, 10) + f'cat "{bad}" >>"{_spk(tmp_path)}"; ' + _rows(tmp_path, 10) + "step", tmp_path)
    assert len(_spk_mails(out)) == 1, out


# -- the reader: one python per loop, offsets, the day ------------------------------------------

def test_rows_from_before_the_keeper_started_are_not_news(tmp_path):
    out = _bash(_rows(tmp_path, 30) + "heard_start; step; " + _rows(tmp_path, 19) + "step", tmp_path)
    assert not _mails(out), out


def test_rows_already_read_are_not_read_again(tmp_path):
    out = _bash(_rows(tmp_path, 10) + "step; step; step; " + _rows(tmp_path, 9) + "step; step", tmp_path)
    assert not _mails(out), out   # 19, not 10 * 3 + 9


def test_a_half_written_row_waits_for_its_newline(tmp_path):
    full = json.dumps({"seq": 1, "kind": "turn", "device": DEV, "why": ZERO_WHY})
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text(full[:30])
    b.write_text(full[30:] + "\n")
    out = _bash(_rows(tmp_path, 19) + f'cat "{a}" >>"{_spk(tmp_path)}"; step; echo =REST=; '
                f'cat "{b}" >>"{_spk(tmp_path)}"; step', tmp_path)
    before, rest = out.split("=REST=")
    assert not _mails(before) and len(_spk_mails(rest)) == 1, out


def test_a_speakers_log_cut_short_is_read_again(tmp_path):
    out = _bash(_rows(tmp_path, 15) + f'step; : >"{_spk(tmp_path)}"; ' + _rows(tmp_path, 20) + "step", tmp_path)
    assert len(_spk_mails(out)) == 1, out


def test_one_python_per_loop_and_none_when_neither_log_grew(tmp_path):
    heard = tmp_path / f"heard_{date.today():%Y-%m-%d}.jsonl"
    beat = json.dumps({"kind": "event", "event": "heartbeat", "pid": 4242, "peak": 300, "source": "mic",
                       "session": "room-s", "device": DEV}) + "\n"
    hb = tmp_path / "hb.txt"
    hb.write_text(beat)
    _bash(f'step; step; {_rows(tmp_path, 3)}step; cat "{hb}" >>"{heard}"; {_rows(tmp_path, 3)}step; '
          f'cat "{hb}" >>"{heard}"; step; step', tmp_path)
    runs = (tmp_path / "py").read_text().splitlines() if (tmp_path / "py").exists() else []
    assert len(runs) == 3, runs   # speakers grew; both grew (ONE run); the heard log grew; never one idle


def test_a_new_day_reads_its_speakers_log_from_the_top(tmp_path):
    """The last file read was yesterday's, far into it: today's is read from byte 0."""
    out = _bash(f"spk_file=/old/speakers_{ANCHOR:%Y-%m-%d}.jsonl; spk_off=999999; " + _rows(tmp_path, 20) + "step", tmp_path)
    assert len(_spk_mails(out)) == 1, out


# -- the seam and the subject -------------------------------------------------------------------

def test_EARS_SPEAKERS_DEAF_LINES_is_a_seam(tmp_path):
    out = _bash(_rows(tmp_path, 5) + "step", tmp_path, EARS_SPEAKERS_DEAF_LINES="5")
    assert _spk_mails(out) == [f"ears: speakers DEAF, 0% of 5 lines on {DEV} EOM"], out
    for bad in ("0", "x", "1000", "-3"):
        d = tmp_path / f"b{bad}"; d.mkdir()
        out = _bash(_rows(d, 19) + "step; echo LINES=$SPK_DEAF_LINES", d, EARS_SPEAKERS_DEAF_LINES=bad)
        assert "LINES=20" in out and not _mails(out), (bad, out)


def test_the_speakers_subjects_fit():
    script = "note() { :; }\n" + _region("deaf") + (
        "\ndeaf_subject speakers cover 999 'Microsoft Teams Audio Device'"
        "\ndeaf_recovered_subject speakers 'Microsoft Teams Audio Device'")
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30, check=True).stdout
    deaf, back = out.strip().splitlines()
    assert deaf == "ears: speakers DEAF, 0% of 999 lines on Microsoft Teams Audi EOM" and len(deaf) <= 64
    assert re.fullmatch(r"ears: speakers hearing again at \d\d:\d\d EOM", back)


# -- the keeper wires it ------------------------------------------------------------------------

def test_the_heard_reader_hands_speakers_rows_to_this_region():
    s = KEEPER.read_text()
    watch = s[s.index("heard_watch() {"):s.index("# --- end heard ---")]
    assert 'spk) spk_seen "$a" "$b" ;;' in watch and 'spkoff) spk_off="$a" ;;' in watch
    assert 'heard_events "$f" "$heard_off" "$s" "$spk_off"' in watch
    assert s.index("# --- end zeros ---") < s.index("# --- speakers:") < s.index("# --- beat:")


# -- covered and still deaf: words in the line, no speech in the side-car's audio ----------------

SILENT_WHY = "words in the line, no speech found in its audio (100% of the line covered)"
SPOKEN = "Oh okay, something short."


def _silent(tmp: Path, n: int, **over) -> str:
    """n covered turns the capture heard words in and the side-car found no speech in."""
    return _rows(tmp, n, why=SILENT_WHY, kind="turn", text=SPOKEN, cover=1.0, **over)


def _heard(tmp: Path, n: int) -> str:
    return _rows(tmp, n, why=None, kind="turn", text=SPOKEN, cover=1.0)


def test_six_covered_turns_with_no_speech_are_one_speakers_deaf_mail(tmp_path):
    out = _bash(_silent(tmp_path, 5) + "step; echo =AT5=; " + _silent(tmp_path, 1) + "step; "
                + _silent(tmp_path, 10) + "step", tmp_path)
    before, after = out.split("=AT5=")
    assert not _mails(before), out
    assert _mails(after) == [f"ears: speakers DEAF, no speech in 6 turns on {DEV} EOM"], out
    assert "HEARD ears-deaf --device AirPods Pro --text speakers: speech: 6" in after


def test_a_heard_turn_ends_it_and_only_a_heard_turn(tmp_path):
    """A 0% row, or a covered row that is no evidence, leaves the episode open; a row where
    the side-car named someone is the ONE recovery mail."""
    out = _bash(_silent(tmp_path, 6) + "step; echo =HELD=; " + _rows(tmp_path, 3)
                + _rows(tmp_path, 3, why=ZEROS_WHY) + "step; echo =BACK=; " + _heard(tmp_path, 1) + "step", tmp_path)
    held, back = out.split("=HELD=")[1].split("=BACK=")
    assert not _spk_mails(held), out
    mails = _spk_mails(back)
    assert len(mails) == 1 and re.fullmatch(r"ears: speakers hearing again at \d\d:\d\d EOM", mails[0]), out
    assert "speakers: hearing again, was speech for" in back


def test_0pc_rows_do_not_reset_the_silent_run_nor_silent_rows_hide_a_0pc_run(tmp_path):
    """The two causes cannot hide each other: silent turns between 0% rows still add up to 6,
    and a heard turn starts the silent run again."""
    out = _bash(_silent(tmp_path, 3) + _rows(tmp_path, 4) + _silent(tmp_path, 3) + "step", tmp_path)
    assert _spk_mails(out) == [f"ears: speakers DEAF, no speech in 6 turns on {DEV} EOM"], out
    d = tmp_path / "reset"; d.mkdir()
    out = _bash(_silent(d, 5) + _heard(d, 1) + _silent(d, 5) + "step", d)
    assert not _mails(out), out


@pytest.mark.parametrize("over", [
    {"text": "", "why": None, "speaker": "silent"},                      # a quiet room: no words
    {"text": "Short."},                                                 # one word
    {"kind": "partial"},                                                # partials repeat and flap
    {"mouth_utt": "u-7"},                                               # the mouth's own words
], ids=["quiet-room", "one-word", "partial", "mouth"])
def test_no_evidence_never_reports(tmp_path, over):
    base = {"why": SILENT_WHY, "kind": "turn", "text": SPOKEN, "cover": 1.0}
    base.update(over)
    why = base.pop("why")
    out = _bash(_rows(tmp_path, 30, why=why, **base) + "step", tmp_path)
    assert not _mails(out), out


def test_silent_turns_are_muted_while_the_room_is_deaf(tmp_path):
    out = _bash("deaf_set room stt 500 AirPods; " + _silent(tmp_path, 10) + "step; deaf_on speakers || echo SPK-OK",
                tmp_path)
    assert not _spk_mails(out) and "SPK-OK" in out, out


def test_EARS_SPEAKERS_SILENT_TURNS_is_a_seam(tmp_path):
    out = _bash(_silent(tmp_path, 2) + "step", tmp_path, EARS_SPEAKERS_SILENT_TURNS="2")
    assert _spk_mails(out) == [f"ears: speakers DEAF, no speech in 2 turns on {DEV} EOM"], out
    for bad in ("0", "x", "100", "-3"):
        d = tmp_path / f"b{bad}"; d.mkdir()
        out = _bash(_silent(d, 5) + "step; echo TURNS=$SPK_SILENT_TURNS", d, EARS_SPEAKERS_SILENT_TURNS=bad)
        assert "TURNS=6" in out and not _mails(out), (bad, out)


def test_the_speech_subject_fits():
    script = "note() { :; }\n" + _region("deaf") + "\ndeaf_subject speakers speech 99 'Microsoft Teams Audio Device'"
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30, check=True).stdout.strip()
    assert out == "ears: speakers DEAF, no speech in 99 turns on Microsoft Team EOM" and len(out) == 64
