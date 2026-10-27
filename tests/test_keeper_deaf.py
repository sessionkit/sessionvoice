"""EARS-226: the keeper's ONE deaf/recover report path.

A capture can look alive and hear nothing: no Silero model, a stopped heartbeat, STT failing, a
lid-shut built-in mic. Every cause reports through ``deaf_set`` / ``deaf_clear``: one mail and one
``ears-deaf`` heard event when a channel's episode opens, one mail and one ``ears-hearing`` event
when it closes, and nothing in between. These run the keeper's deaf block alone, as
test_keeper_model.py does its model gate: ``note``, ``mail_me`` and ``$HEARD`` are stubs, the
clock is ``EARS_NOW``, and nothing is mailed or written.
"""
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEEPER = ROOT / "keeper" / "ears-keeper.sh"

LONG_DEV = "Somebody's Pixel Buds Pro with a very long name indeed"


def _deaf_block() -> str:
    s = KEEPER.read_text()
    return s[s.index("# --- deaf:"):s.index("# --- end deaf ---")]


def _bash(script: str, **env) -> str:
    pre = ('note() { echo "NOTE $*"; }\nmail_me() { echo "MAIL $*"; }\n'
           'heard_stub() { shift 2; echo "HEARD $*" >&3; }\nHEARD=heard_stub\nexec 3>&1\n')  # past >/dev/null
    out = subprocess.run(["bash", "-c", "set -uo pipefail\n" + pre + _deaf_block() + "\n" + script],
                         env=dict(os.environ, **env), capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return out.stdout


def _kind(out: str, prefix: str) -> list[str]:
    return [l[len(prefix) + 1:] for l in out.splitlines() if l.startswith(prefix + " ")]


def _subject_ok(s: str) -> None:
    assert len(s) <= 64 and s.endswith(" EOM") and s.startswith("ears: "), s


# -- one mail per episode ------------------------------------------------------------------

def test_first_set_opens_an_episode_one_mail_one_event():
    # `probe`: a cause with no subject line of its own, so it gets the generic, fitted one
    out = _bash("deaf_set room probe 'no audio since 17:02' 'MacBook Pro Microphone'")
    mails, heard = _kind(out, "MAIL"), _kind(out, "HEARD")
    assert len(mails) == 1 and len(heard) == 1
    _subject_ok(mails[0])
    assert mails[0].startswith("ears: DEAF, probe: no audio") and " on MacBook Pro Micropho at " in mails[0]
    assert heard[0] == "ears-deaf --device MacBook Pro Microphone --text room: probe: no audio since 17:02"
    assert "NOTE DEAF (room): probe: no audio since 17:02" in out


def test_a_repeat_of_the_same_cause_says_nothing():
    out = _bash("deaf_set room beat '' Mic; echo ---; for i in 1 2 3 4 5; do deaf_set room beat '' Mic; done")
    after = out.split("---", 1)[1]
    assert after.strip() == ""                                    # no note, no event, no mail


def test_a_new_cause_mid_episode_is_an_event_and_a_note_never_a_mail():
    out = _bash("deaf_set room beat '' Mic; echo ---; deaf_set room stt 500 Mic; deaf_set room stt 500 Mic; "
                "echo CAUSE=$(deaf_cause room)")
    after = out.split("---", 1)[1]
    assert "MAIL" not in after
    assert _kind(after, "HEARD") == ["ears-deaf --device Mic --text room: stt: 500"]
    assert "not mailed, one was sent" in after
    assert "CAUSE=stt" in after                                    # stt outranks beat (DEAF_ORDER)


def test_a_lesser_cause_does_not_rename_the_episode():
    out = _bash("deaf_set room model x Mic; deaf_set room zeros '' Mic; echo CAUSE=$(deaf_cause room)")
    assert "CAUSE=model" in out and out.count("MAIL ") == 1


def test_clear_closes_the_episode_one_mail_naming_cause_and_duration():
    out = _bash("EARS_NOW=1000; deaf_set room beat '' 'AirPods Pro'; echo ---; "
                "EARS_NOW=$((1000 + 4*3600 + 40*60)); deaf_clear room beat; deaf_room && echo STILL || echo HEARING")
    after = out.split("---", 1)[1]
    mails = _kind(after, "MAIL")
    assert len(mails) == 1 and mails[0].startswith("ears: hearing again on AirPods Pro at ")
    _subject_ok(mails[0])
    assert _kind(after, "HEARD") == ["ears-hearing --device AirPods Pro --text room: hearing again, was beat for 4h40m"]
    assert "HEARING" in after


def test_clear_while_hearing_or_for_another_cause_says_nothing():
    out = _bash("deaf_clear room beat Mic; deaf_set room model x Mic; echo ---; deaf_clear room beat Mic")
    assert out.split("---")[0].count("NOTE") == 1                   # only deaf_set's note
    assert out.split("---", 1)[1].strip() == ""


def test_the_episode_closes_only_when_its_last_cause_clears():
    out = _bash("deaf_set room beat '' Mic; deaf_set room stt 500 Mic; echo ---; deaf_clear room stt Mic; "
                "echo ===; deaf_clear room beat Mic")
    mid, end = out.split("---", 1)[1].split("===")
    assert "MAIL" not in mid and "HEARD" not in mid and "still DEAF: beat" in mid
    assert len(_kind(end, "MAIL")) == 1 and "was stt for" in end   # named by the episode's top cause


def test_a_new_episode_after_recovery_mails_again():
    out = _bash("for i in 1 2 3; do deaf_set room model x Mic; deaf_clear room model Mic; done")
    assert out.count("MAIL ears: DEAF") == 3 and out.count("MAIL ears: hearing again") == 3


def test_channels_are_separate_episodes():
    out = _bash("deaf_set speakers cover '0% of 20 lines' Mic; deaf_room && echo ROOM || echo ROOMOK; "
                "deaf_set room model x Mic; deaf_clear speakers cover; deaf_on room && echo ROOMDEAF")
    mails = _kind(out, "MAIL")
    assert len(mails) == 3
    assert mails[0].startswith("ears: speakers DEAF, ") and mails[2].startswith("ears: speakers hearing again at ")
    assert "ROOMOK" in out and "ROOMDEAF" in out


def test_a_bad_channel_or_cause_is_refused_and_noted():
    out = _bash("deaf_set 'room x' beat; echo rc=$?; deaf_set room 'a;b'; echo rc=$?; deaf_clear Room beat; echo rc=$?")
    assert out.count("rc=1") == 3 and "MAIL" not in out and out.count("bad channel or cause") == 3


def test_the_device_is_remembered_for_the_recovery():
    out = _bash("deaf_set room model x 'USB Mic'; deaf_clear room model")
    assert "MAIL ears: hearing again on USB Mic at " in out


# -- subjects ------------------------------------------------------------------------------

def test_every_subject_fits_64_and_ends_eom():
    causes = ["model", "lid", "stt", "beat", "zeros", "cover", "speech", "averyveryverylongcausename"]
    script = "".join(
        f"deaf_subject {ch} {c} 'a long detail string that keeps on going and going' \"$D\"; "
        f"deaf_subject {ch} {c} '' ''; "
        for ch in ("room", "speakers", "averyverylongchannel") for c in causes)
    script += 'deaf_recovered_subject room "$D"; deaf_recovered_subject room ""; deaf_recovered_subject speakers "$D"'
    lines = _bash(script, D=LONG_DEV).splitlines()
    assert len(lines) == 3 * len(causes) * 2 + 3
    for s in lines:
        _subject_ok(s)


def test_a_cut_detail_leaves_no_dangling_colon():
    s = _bash('deaf_subject speakers zeros "long detail" "$D"', D=LONG_DEV).strip()
    assert s.startswith("ears: speakers DEAF, zeros on ") and ": on" not in s


def test_the_model_subject_is_the_designed_one():
    assert _bash("deaf_subject room model /x/silero_vad.onnx Mic").strip() == \
        "ears: DEAF, no Silero model: run ears install EOM"


def test_a_generic_subject_cuts_the_device_to_20():
    s = _bash('deaf_subject room probe "" "$D"', D=LONG_DEV).strip()
    assert f" on {LONG_DEV[:20]} at " in s and re.search(r" at \d\d:\d\d EOM$", s)


def test_the_beat_subject_is_the_designed_one():
    """EARS-226 do-003, design C1: "ears: DEAF, no heartbeat since HH:MM on <dev20> EOM", <= 64."""
    s = _bash('deaf_subject room beat "since 17:02" "$D"', D=LONG_DEV).strip()
    assert s == f"ears: DEAF, no heartbeat since 17:02 on {LONG_DEV[:20]} EOM" and len(s) == 64
    assert _bash("deaf_subject room beat '' ''").strip() == "ears: DEAF, no heartbeat since ? EOM"


# -- restart mails are notes while the room is DEAF -------------------------------------------

def test_restart_mails_are_notes_while_the_room_is_deaf():
    out = _bash("mail_unless_deaf 'ears: capture restarted on Mic at 10:00 EOM'; "
                "deaf_set room stt 500 Mic; echo ---; "
                "mail_unless_deaf 'ears: capture restart on Mic FAILED at 10:01 EOM'; "
                "deaf_set speakers cover x; deaf_clear room stt; echo ===; "
                "mail_unless_deaf 'ears: capture restarted on Mic at 10:02 EOM'")
    before, rest = out.split("---", 1)
    mid, end = rest.split("===")
    assert "MAIL ears: capture restarted on Mic at 10:00 EOM" in before
    assert "capture restart" not in " ".join(_kind(mid, "MAIL"))
    assert "NOTE not mailed, the room is DEAF (stt): ears: capture restart on Mic FAILED" in mid
    assert "MAIL ears: capture restarted on Mic at 10:02 EOM" in end   # a deaf SPEAKERS channel does not mute it


def test_start_capture_mails_through_mail_unless_deaf():
    s = KEEPER.read_text()
    body = s[s.index("start_capture() {"):s.index("stop_capture() {")]
    assert "mail_me" not in body and body.count("mail_unless_deaf") == 2


# -- the backoff, a pure function ------------------------------------------------------------

def test_backoff_doubles_from_30_to_300_and_resets_after_a_capture_that_heard():
    out = _bash("w=0; for i in 1 2 3 4 5 6 7; do w=$(restart_backoff 0 $w); echo $w; done; "
                "restart_backoff 1 300; restart_backoff 0 0")
    assert out.split() == ["30", "60", "120", "240", "300", "300", "300", "0", "30"]


def test_backoff_bounds_are_env_seams_and_bad_input_is_no_wait():
    out = _bash("restart_backoff 0 0; restart_backoff 0 8; restart_backoff 0 x; restart_backoff 0 -5",
                EARS_BACKOFF_MIN="5", EARS_BACKOFF_MAX="12")
    assert out.split() == ["5", "12", "5", "5"]


def test_duration_reads_as_seconds_minutes_or_hours():
    out = _bash("for s in 0 45 89 90 3599 3600 16800 x; do deaf_dur $s; done")
    assert out.split() == ["0s", "45s", "89s", "1m", "59m", "1h0m", "4h40m", "0s"]


# -- one path -------------------------------------------------------------------------------

def test_nothing_outside_the_deaf_region_mails_about_deafness():
    s = KEEPER.read_text()
    outside = s[:s.index("# --- deaf:")] + s[s.index("# --- end deaf ---"):]
    assert not re.search(r"mail_me[^\n]*(DEAF|Silero)", outside)
    assert s.index("# --- end deaf ---") < s.index("# --- model:")   # model_ready reports via deaf_set
