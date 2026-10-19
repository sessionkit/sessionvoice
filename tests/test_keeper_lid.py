"""EARS-341 (EARS-226 item 1): lid shut, the keeper HOLDS instead of failing over to a deaf mic.

The AirPods left with the lid shut, macOS made the built-in mic the default, and FOLLOW moved the
room capture onto it: a mic off in hardware that still hands over frames of zeros. The keeper
mailed `mic ->` and `capture restarted`, and the ears were deaf. Now the lid is probed while the
target is the built-in (found by transport), and lid shut is ONE `ears: DEAF, lid shut, built-in
mic off, holding` mail, no capture on the built-in, and a hold. The `mic ->` change is a note
(design G-R1: the lid decision runs before the FOLLOW mail). The external input returning is ONE
mail, the recovery, once its capture beats with sound.

These run the keeper's regions (deaf, decisions, model, heard .. beat, which holds lag, stt, lid
and zeros) and the keeper's OWN loop tail, from `lid_watch "$t"` to the start, and its own
start_capture. Stubbed: note, mail_me, $HEARD, the launch (a fake pid), capture_running,
capture_pid and capture_arg (the fake pid's argv), and stop_capture (it writes the stop row a
capture writes when it honours its STOP file). The lid is EARS_LID_PROBE, a stub that prints
a file. The clock is EARS_NOW. Nothing is probed, launched, stopped or mailed for real.
"""
import json
import os
import re
import stat
import subprocess
from datetime import date, timedelta, timezone
from pathlib import Path

from tests._anchor import ANCHOR_SUMMER

ROOT = Path(__file__).resolve().parents[1]
KEEPER = ROOT / "keeper" / "ears-keeper.sh"

AIR = "MacBook Air Microphone"   # an Air: the old literal ("MacBook Pro Microphone") named nothing here
BUDS = "AirPods Pro"
SESSION = "room-s"


def _region(name: str) -> str:
    s = KEEPER.read_text()
    return s[s.index(f"# --- {name}:"):s.index(f"# --- end {name} ---")]


def _loop_tail() -> str:
    """The keeper's loop from the lid decision to the start, run once per step."""
    s = KEEPER.read_text()
    loop = s[s.index("while :; do"):]
    return loop[loop.index('  lid_watch "$t"'):loop.rindex("\ndone")]


def _program() -> str:
    s = KEEPER.read_text()
    return (_region("deaf") + s[s.index("# --- decisions"):s.index("# --- end decisions")]
            + _region("model") + s[s.index("start_capture() {"):s.index("# --- stop:")]
            + s[s.index("# --- heard:"):s.index("# --- end beat ---")])


# every row's stamp: the summer anchor at the +11:00 it was written with (nothing compares it)
ROW_TS = ANCHOR_SUMMER.astimezone(timezone(timedelta(hours=11))).isoformat(timespec="seconds")
PRE = r'''
note() { echo "NOTE $*"; }
mail_me() { echo "MAIL $*"; }
heard_stub() { shift 2; echo "HEARD $*" >&3; }
HEARD=heard_stub; exec 3>&1
sleep() { :; }; missed_reply_window() { echo "${EARS_MISSED_REPLY_S:-30}"; }; RUN="$T"; SILERO="$T/silero_vad.onnx"; echo onnx >"$SILERO"
ROOM_PID=""; ROOM_DEV=""; NEXT=5000; SEQ=0
launch_capture() { NEXT=$((NEXT + 1)); ROOM_PID=$NEXT; ROOM_DEV="$1"; echo "START $1"; }
capture_running() { [ -n "$ROOM_PID" ]; }
capture_pid() { echo "$ROOM_PID"; }
capture_arg() { [ -n "$ROOM_PID" ] || return 0
  # as the keeper's own reads argv back: split on spaces, so a device is its FIRST word only
  case "$1" in --session) echo "$EARS_SESSION" ;; --device) echo "${ROOM_DEV%% *}" ;; esac; }
row() { SEQ=$((SEQ + 1)); printf '{"ts":"''' + ROW_TS + r'''","seq":%s,"v":1,%s}\n' "$SEQ" "$1" >>"$LOG"; }
stop_capture() { echo "STOP $ROOM_DEV"
  row '"kind":"event","event":"listen-stopped","reason":"stop","source":"mic","session":"'"$EARS_SESSION"'","device":"'"$ROOM_DEV"'"'
  ROOM_PID=""; }
died() {  # the capture ends by itself: $1 = the error (it heard nothing)
  row '"kind":"event","event":"listen-stopped","reason":"error","error":"'"$1"'","source":"mic","session":"'"$EARS_SESSION"'","device":"'"$ROOM_DEV"'"'
  ROOM_PID=""; }
beat() {  # one heartbeat of the running capture: $1 = peak
  row '"kind":"event","event":"heartbeat","pid":'"$ROOM_PID"',"peak":'"$1"',"rms":3.0,"source":"mic","session":"'"$EARS_SESSION"'","device":"'"$ROOM_DEV"'"'; }
lid() { echo "$1" >"$LIDF"; }
audio_builtin_inputs() { printf '%s\n' "$BUILTIN_TRANSPORT"; }
FOLLOW=1; STABLE=1; seen=0; last_t="$BUDS"; now="$BUDS"
heard_start
step() {  # $1 = time, $2 = the target (the Mac's default input), $3 = every input
  EARS_NOW="$1"; t="$2"; now="$3"
  [ "$now" = "$builtin_for" ] || builtin_refresh "$now"
  beat_track "$(capture_pid)"; heard_watch; beat_check; zeros_check
  for _once in 1; do
'''
POST = r'''
  done
}
'''


def _bash(script: str, tmp: Path, **env) -> str:
    log = tmp / f"heard_{date.today():%Y-%m-%d}.jsonl"
    log.touch()
    lidf = tmp / "lid"
    lidf.write_text("open\n")
    probe = tmp / "lid-probe"
    probe.write_text(f'#!/bin/sh\necho x >>"{tmp}/probes"\ncat "{lidf}"\n')
    probe.chmod(probe.stat().st_mode | stat.S_IXUSR)
    e = dict(os.environ, EARS_HEARD_DIR=str(tmp), EARS_SESSION=SESSION, T=str(tmp), LOG=str(log), LIDF=str(lidf),
             EARS_LID_PROBE=str(probe), BUDS=BUDS, AIR=AIR, BUILTIN_TRANSPORT=AIR, EARS_LID_EVERY="5")
    e.pop("EARS_BUILTIN_MIC", None)
    e.update(env)
    prog = "set -uo pipefail\n" + _program() + PRE + _loop_tail() + POST + script
    out = subprocess.run(["bash", "-c", prog], env=e, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "command not found" not in out.stderr, out.stderr
    return out.stdout


def _lines(out: str, prefix: str) -> list[str]:
    return [l[len(prefix) + 1:] for l in out.splitlines() if l.startswith(prefix + " ")]


def _probes(tmp: Path) -> int:
    p = tmp / "probes"
    return len(p.read_text().splitlines()) if p.exists() else 0


BOTH = f"$BUDS\n$AIR"   # the inputs while the AirPods are here (a bash string)

# On the AirPods, hearing: a start, then beats with sound.
HEARING = f'step 0 "$BUDS" "{BOTH}"; beat 300; step 15 "$BUDS" "{BOTH}"; '
# The lid is shut, and the AirPods leave: macOS makes the built-in the default.
FAILOVER = 'lid closed; echo =FAILOVER=; step 20 "$AIR" "$AIR"; step 21 "$AIR" "$AIR"; step 40 "$AIR" "$AIR"; '
# They come back.
RETURN = (f'echo =RETURN=; step 100 "$BUDS" "{BOTH}"; beat 250; step 115 "$BUDS" "{BOTH}"; '
          f'beat 280; step 130 "$BUDS" "{BOTH}"; ')


# -- the criterion: ONE mail each way, no capture on the built-in -------------------------

def test_lid_shut_failover_is_one_mail_and_the_return_is_one_mail(tmp_path):
    out = _bash(HEARING + FAILOVER + RETURN, tmp_path)
    before, rest = out.split("=FAILOVER=")
    failover, back = rest.split("=RETURN=")
    assert _lines(before, "START") == [BUDS] and not _lines(before, "MAIL")[1:], before   # one start mail
    # failover: exactly ONE mail, the DEAF one; no `mic ->`
    mails = _lines(failover, "MAIL")
    assert len(mails) == 1 and re.fullmatch(r"ears: DEAF, lid shut, built-in mic off, holding at \d\d:\d\d EOM", mails[0]), out
    assert f"NOTE not mailed, the room is DEAF (lid): ears: mic -> {AIR}" in failover
    # no capture on the built-in: the one on the departed AirPods is stopped, none started
    assert _lines(failover, "STOP") == [BUDS] and not _lines(failover, "START"), failover
    # the return: exactly ONE mail, the recovery, once the capture beats with sound
    mails = _lines(back, "MAIL")
    assert len(mails) == 1 and mails[0].startswith(f"ears: hearing again on {BUDS} at "), out
    assert f"NOTE not mailed, the room is DEAF (lid): ears: mic -> {BUDS}" in back
    assert f"NOTE not mailed, the room is DEAF (lid): ears: capture restarted on {BUDS}" in back
    assert _lines(back, "START") == [BUDS]
    assert AIR not in _lines(out, "START")
    heard = _lines(out, "HEARD")
    assert [h.split()[0] for h in heard] == ["ears-deaf", "ears-hearing"], heard
    assert heard[0] == f"ears-deaf --device {AIR} --text room: lid: shut"
    assert "room: hearing again, was lid for " in heard[1]


def test_the_return_starts_at_once_with_no_wait_inherited(tmp_path):
    """The capture on the departed AirPods stalled out (`no audio`, it heard nothing) AFTER the
    keeper saw the move: its row armed a wait on the AirPods. The hold starts nothing, so it adds
    no wait; the AirPods returning start at once (foreman, do-007)."""
    out = _bash(f'step 0 "$BUDS" "{BOTH}"; lid closed; step 20 "$AIR" "$AIR"; '
                "died 'SourceError: no audio for 5s'; step 25 \"$AIR\" \"$AIR\"; step 60 \"$AIR\" \"$AIR\"; "
                f'echo =RETURN=; step 61 "$BUDS" "{BOTH}"', tmp_path)
    hold, back = out.split("=RETURN=")
    assert _lines(back, "START") == [BUDS], out     # at 61: not held to 25 + 30
    assert _lines(hold, "START") == [BUDS]          # step 0's only: none on the built-in


def test_a_one_loop_flap_to_the_built_in_does_not_cut_a_capture_that_hears(tmp_path):
    """do-007 review: lid shut, the AirPods hearing, and macOS flips the default input to the
    built-in for ONE loop. The FOLLOW swap waits STABLE loops, so it never cut the AirPods
    capture for that; the hold must not either (design C3 stops a capture ON the built-in)."""
    out = _bash(f'STABLE=2; step 0 "$BUDS" "{BOTH}"; step 1 "$BUDS" "{BOTH}"; beat 300; step 15 "$BUDS" "{BOTH}"; '
                f'lid closed; echo =FLAP=; step 16 "$AIR" "{BOTH}"; beat 290; step 17 "$BUDS" "{BOTH}"; '
                f'step 18 "$BUDS" "{BOTH}"; beat 280; step 30 "$BUDS" "{BOTH}"', tmp_path)
    flap = out.split("=FLAP=")[1]
    assert not _lines(flap, "STOP") and not _lines(flap, "START"), flap   # the AirPods capture ran on
    assert len(_lines(flap, "MAIL")) == 2   # the DEAF mail at the flip (G-R1: before any mic ->), the recovery


def test_a_capture_on_the_input_the_mac_left_stops_once_the_move_holds_stable_loops(tmp_path):
    out = _bash(f'STABLE=2; step 0 "$BUDS" "{BOTH}"; step 1 "$BUDS" "{BOTH}"; beat 300; step 15 "$BUDS" "{BOTH}"; '
                'lid closed; echo =ONE=; step 16 "$AIR" "$AIR"; echo =TWO=; step 17 "$AIR" "$AIR"; step 18 "$AIR" "$AIR"',
                tmp_path)
    one, two = out.split("=ONE=")[1].split("=TWO=")
    assert len(_lines(one, "MAIL")) == 1 and not _lines(one, "STOP")
    assert _lines(two, "STOP") == [BUDS] and not _lines(two, "START") and not _lines(two, "MAIL"), out


def test_a_capture_on_the_built_in_stops_at_once_whatever_stable(tmp_path):
    """The capture runs on the built-in; the default flaps to the AirPods and back (seen starts
    again), and the lid shuts: that capture is stopped on the first held loop, not STABLE later.
    capture_arg reads its device back as the first word only ("MacBook"), as the keeper's does."""
    out = _bash(f'STABLE=5; ' + "".join(f'step {i} "$AIR" "{BOTH}"; ' for i in range(0, 6))
                + f'step 6 "$BUDS" "{BOTH}"; lid closed; echo =SHUT=; step 20 "$AIR" "{BOTH}"', tmp_path)   # past the lid cache
    before, shut = out.split("=SHUT=")
    assert _lines(before, "START") == [AIR] and not _lines(before, "STOP"), before
    assert _lines(shut, "STOP") == [AIR] and not _lines(shut, "START"), out


def test_a_hold_arms_no_wait_and_its_stop_is_never_held(tmp_path):
    """The lid shuts under a capture on the built-in: stopped (reason stop), so no wait; and the
    lid opening starts it again at once."""
    out = _bash(f'step 0 "$AIR" "$AIR"; beat 120; step 15 "$AIR" "$AIR"; lid closed; step 21 "$AIR" "$AIR"; '
                'step 22 "$AIR" "$AIR"; step 50 "$AIR" "$AIR"; lid open; echo =OPEN=; step 55 "$AIR" "$AIR"', tmp_path)
    held, opened = out.split("=OPEN=")
    assert _lines(held, "STOP") == [AIR] and _lines(held, "START") == [AIR]   # the start at 0 only
    assert "next start in" not in out
    assert _lines(opened, "START") == [AIR], out


def test_the_lid_opening_is_one_recovery_mail_once_the_capture_hears(tmp_path):
    """Not the bare opening: that would mail `hearing again`, then `capture restarted`."""
    out = _bash('lid closed; step 0 "$AIR" "$AIR"; step 10 "$AIR" "$AIR"; lid open; echo =OPEN=; '
                'step 20 "$AIR" "$AIR"; beat 90; step 35 "$AIR" "$AIR"; beat 95; step 50 "$AIR" "$AIR"', tmp_path)
    held, opened = out.split("=OPEN=")
    assert len(_lines(held, "MAIL")) == 1 and "lid shut" in _lines(held, "MAIL")[0]
    mails = _lines(opened, "MAIL")
    # the device as the beat row names it: the heard reader cuts it to 20, as deaf_recovered_subject does
    assert len(mails) == 1 and mails[0].startswith(f"ears: hearing again on {AIR[:20]} at "), out
    assert f"NOTE not mailed, the room is DEAF (lid): ears: capture restarted on {AIR}" in opened


def test_beats_with_no_sound_after_the_return_do_not_end_it(tmp_path):
    out = _bash(f'step 0 "$BUDS" "{BOTH}"; lid closed; step 20 "$AIR" "$AIR"; lid open; '
                f'step 100 "$BUDS" "{BOTH}"; beat 0; step 115 "$BUDS" "{BOTH}"; deaf_room && echo STILL-DEAF', tmp_path)
    assert "STILL-DEAF" in out and len([m for m in _lines(out, "MAIL") if "hearing again" in m]) == 0


def test_a_stale_beat_with_sound_from_before_the_hold_does_not_end_it(tmp_path):
    """The capture beat with sound, then the lid shut before that row was read."""
    out = _bash(f'step 0 "$AIR" "$AIR"; lid closed; beat 140; step 6 "$AIR" "$AIR"; step 7 "$AIR" "$AIR"; '
                'deaf_room && echo STILL-DEAF', tmp_path)
    assert "STILL-DEAF" in out and not [m for m in _lines(out, "MAIL") if "hearing again" in m], out


# -- the probe: a seam, cached, only while the target is the built-in; unknown is open -----------

def test_the_probe_runs_only_while_the_target_is_the_builtin(tmp_path):
    _bash(f'step 0 "$BUDS" "{BOTH}"; ' + "".join(f'step {i} "$BUDS" "{BOTH}"; ' for i in range(1, 30)), tmp_path)
    assert _probes(tmp_path) == 0


def test_the_probe_is_cached_for_EARS_LID_EVERY(tmp_path):
    _bash("".join(f'step {i} "$AIR" "$AIR"; ' for i in range(0, 12)), tmp_path)
    assert _probes(tmp_path) == 3                   # at 0, 5 and 10
    d = tmp_path / "every2"; d.mkdir()
    _bash("".join(f'step {i} "$AIR" "$AIR"; ' for i in range(0, 12)), d, EARS_LID_EVERY="2")
    assert _probes(d) == 6


def test_a_new_lid_state_is_seen_within_EARS_LID_EVERY(tmp_path):
    out = _bash('step 0 "$AIR" "$AIR"; lid closed; step 3 "$AIR" "$AIR"; echo =AT5=; step 5 "$AIR" "$AIR"', tmp_path)
    early, at5 = out.split("=AT5=")
    assert "lid shut" not in early and "DEAF, lid shut" in at5


def test_unknown_is_open(tmp_path):
    for state in ("unknown", "", "garbage", "Closed?"):
        d = tmp_path / (state or "empty"); d.mkdir()
        out = _bash(f'lid "{state}"; step 0 "$AIR" "$AIR"', d)
        assert _lines(out, "START") == [AIR] and "DEAF" not in out, (state, out)
        assert "NOTE lid: ? -> unknown" in out


def test_a_mac_with_no_builtin_never_probes_or_holds(tmp_path):
    """A desktop: no `builtin` transport and no name that fits the pattern."""
    out = _bash('lid closed; step 0 "USB Mic" "USB Mic"', tmp_path, BUILTIN_TRANSPORT="")
    assert _probes(tmp_path) == 0 and _lines(out, "START") == ["USB Mic"] and "DEAF" not in out


def test_the_builtin_is_known_by_transport_whatever_its_name(tmp_path):
    out = _bash('lid closed; step 0 "Micro interne" "Micro interne"', tmp_path, BUILTIN_TRANSPORT="Micro interne")
    assert not _lines(out, "START") and "DEAF, lid shut" in out


def test_with_devices_json_stale_the_name_pattern_finds_an_air(tmp_path):
    out = _bash('lid closed; step 0 "$AIR" "$AIR"', tmp_path, BUILTIN_TRANSPORT="")
    assert not _lines(out, "START") and "DEAF, lid shut" in out


# -- a headset on the jack is a `builtin` input too (EARS-341 do-010, do-007 round 3 F1) --------
# input-watch sorts inputs by name, so "External Microphone" comes before "MacBook Pro
# Microphone". That the jack's input reports transport `builtin` is UNMEASURED on real hardware
# (it is the Mac's own codec); either way the keeper must take the MacBook mic for the built-in.

JACK = "External Microphone"
MBP = "MacBook Pro Microphone"
TWO_BUILTINS = f"{JACK}\n{MBP}"


def test_with_a_headset_on_the_jack_the_builtin_is_the_macbook_mic(tmp_path):
    out = _bash(f'step 0 "{JACK}" "{JACK}\n{MBP}"; echo "BUILTIN=$builtin_mic"', tmp_path,
                BUILTIN_TRANSPORT=TWO_BUILTINS)
    assert f"BUILTIN={MBP}" in out, out


def test_lid_shut_with_the_headset_as_the_target_is_followed_never_held(tmp_path):
    """The lid does not shut a headset's mic: it is the setup a lid-shut user is told to use."""
    out = _bash(f'lid closed; step 0 "{JACK}" "{JACK}\n{MBP}"; step 6 "{JACK}" "{JACK}\n{MBP}"; '
                'deaf_room || echo HEARING', tmp_path, BUILTIN_TRANSPORT=TWO_BUILTINS)
    assert _lines(out, "START") == [JACK] and "DEAF" not in out and "HEARING" in out, out
    assert _probes(tmp_path) == 0   # the target is not the built-in: no probe


def test_headset_in_lid_shut_the_airpods_leave_onto_the_macbook_mic_holds_with_one_mail(tmp_path):
    """Item 1's failover, with a headset on the jack: the Mac picks the MacBook mic, and the
    keeper holds with ONE lid mail instead of moving onto it."""
    here, left = f"$BUDS\n{JACK}\n{MBP}", f"{JACK}\n{MBP}"
    out = _bash(f'step 0 "$BUDS" "{here}"; beat 300; step 15 "$BUDS" "{here}"; lid closed; echo =FAILOVER=; '
                f'step 20 "{MBP}" "{left}"; step 21 "{MBP}" "{left}"; step 40 "{MBP}" "{left}"', tmp_path,
                BUILTIN_TRANSPORT=TWO_BUILTINS)
    failover = out.split("=FAILOVER=")[1]
    mails = _lines(failover, "MAIL")
    assert len(mails) == 1 and re.fullmatch(r"ears: DEAF, lid shut, built-in mic off, holding at \d\d:\d\d EOM", mails[0]), out
    assert not _lines(failover, "START") and MBP not in _lines(out, "START"), out


def _fake_ioreg(tmp: Path, body: str) -> dict:
    bin_ = tmp / "bin"
    bin_.mkdir(exist_ok=True)
    f = bin_ / "ioreg"
    f.write_text("#!/bin/sh\ncat <<'EOF'\n" + body + "EOF\n")
    f.chmod(0o755)
    return {"PATH": f"{bin_}:{os.environ['PATH']}"}


IOREG = '''+-o AppleARMPE  <class AppleARMPE, id 0x100000213, registered, matched, active, busy 0 (5 ms), retain 46>
    {
      "AppleClamshellCausesSleep" = Yes
      "AppleClamshellState" = {state}
      "IOPlatformThermalProfile" = "J413"
    }
'''


def test_the_default_probe_reads_ioreg(tmp_path):
    for state, want in (("Yes", "closed"), ("No", "open")):
        d = tmp_path / state; d.mkdir()
        env = _fake_ioreg(d, IOREG.replace("{state}", state))
        script = "\n".join(["note() { :; }", _region("deaf"), _region("lid"), "lid_look; echo $lid_now"])
        e = dict(os.environ, **env)
        e.pop("EARS_LID_PROBE", None)
        out = subprocess.run(["bash", "-c", "set -uo pipefail\n" + script], env=e, capture_output=True, text=True,
                             timeout=30)
        assert out.stdout.strip() == want, (state, out.stdout, out.stderr)


def test_a_desktop_has_no_clamshell_key_and_is_unknown(tmp_path):
    env = _fake_ioreg(tmp_path, "+-o J375AP  <class IOPlatformExpertDevice>\n")
    e = dict(os.environ, **env)
    e.pop("EARS_LID_PROBE", None)
    script = "\n".join(["note() { :; }", _region("deaf"), _region("lid"), "lid_look; echo $lid_now"])
    out = subprocess.run(["bash", "-c", "set -uo pipefail\n" + script], env=e, capture_output=True, text=True, timeout=30)
    assert out.stdout.strip() == "unknown"


# -- the lid wins over zeros (do-006's hook), and the precedence -----------------------------

def test_zeros_on_the_builtin_with_the_lid_shut_are_the_lids(tmp_path):
    """The capture on the built-in gives zeros (the lid shut under it): one lid mail, not zeros.
    The hook probes afresh: here the cached state (open, at 0) is still within EARS_LID_EVERY."""
    out = _bash('step 0 "$AIR" "$AIR"; lid closed; beat 0; beat 0; beat 0; beat 0; step 1 "$AIR" "$AIR"; '
                'step 5 "$AIR" "$AIR"; step 6 "$AIR" "$AIR"', tmp_path, EARS_LID_EVERY="5")
    mails = _lines(out, "MAIL")
    assert [m for m in mails if "DEAF" in m] == [m for m in mails if "lid shut" in m] and len(
        [m for m in mails if "DEAF" in m]) == 1, out
    assert "mic gives only zeros" not in out and "the lid is shut: the lid's" in out
    assert _lines(out, "STOP") == [AIR]
    assert _probes(tmp_path) == 3                   # 0; the hook's fresh one at 1 (cached to 6); 6


USB = "Microphone USB"   # reads back as "Microphone": a word inside "MacBook Air Microphone"


def test_zeros_on_an_external_mic_whose_first_word_is_in_the_builtins_name_are_zeros(tmp_path):
    """do-007 r2 review: the capture's device reads back as its first word, and a substring test
    took "Microphone" (of "Microphone USB") for the built-in. With the lid shut, its zeros were
    the lid's, and the lid region holds only for the built-in target: nobody reported them."""
    both = f"{USB}\n$AIR"
    out = _bash(f'step 0 "{USB}" "{both}"; lid closed; beat 0; beat 0; beat 0; beat 0; step 1 "{USB}" "{both}"; '
                'deaf_room && echo DEAF-ROOM', tmp_path)
    deaf = [m for m in _lines(out, "MAIL") if "DEAF" in m]
    assert len(deaf) == 1 and deaf[0].startswith(f"ears: DEAF, mic gives only zeros on {USB} at "), out
    assert "the lid's" not in out and "DEAF-ROOM" in out


def test_a_flap_does_not_cut_an_external_mic_whose_first_word_is_in_the_builtins_name(tmp_path):
    both = f"{USB}\n$AIR"
    out = _bash(f'STABLE=2; step 0 "{USB}" "{both}"; step 1 "{USB}" "{both}"; lid closed; echo =FLAP=; '
                f'step 16 "$AIR" "{both}"; step 17 "{USB}" "{both}"', tmp_path)
    assert not _lines(out.split("=FLAP=")[1], "STOP"), out


def test_the_capture_device_matches_the_builtins_first_word_whole():
    script = "\n".join(["note() { :; }", _region("deaf"), _region("lid"), f'builtin_mic="{AIR}"',
                        *[f'lid_capture_builtin "{w}" && echo "Y {w}" || echo "N {w}"'
                          for w in ("MacBook", "macbook", "Microphone", "Mac", "Air", "AirPods", "")]])
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30, check=True).stdout
    assert out.split("\n")[:-1] == ["Y MacBook", "Y macbook", "N Microphone", "N Mac", "N Air", "N AirPods", "N "]


def test_lid_ranks_after_model_and_before_stt_beat_zeros(tmp_path):
    script = "\n".join(["note() { :; }", _region("deaf"),
                        'echo "$(deaf_name "zeros beat stt lid") $(deaf_name "lid model")"'])
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30, check=True).stdout
    assert out.split() == ["lid", "model"]


def test_the_lid_subject_fits():
    script = "note() { :; }\n" + _region("deaf") + f"\ndeaf_subject room lid shut '{AIR}'"
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30, check=True).stdout.strip()
    assert re.fullmatch(r"ears: DEAF, lid shut, built-in mic off, holding at \d\d:\d\d EOM", out) and len(out) <= 64


# -- pinned (not FOLLOW): the built-in pinned with the lid shut holds too ---------------------

def test_a_pinned_builtin_with_the_lid_shut_holds(tmp_path):
    out = _bash('FOLLOW=0; last_t="$AIR"; lid closed; step 0 "$AIR" "$AIR"; step 9 "$AIR" "$AIR"', tmp_path)
    assert not _lines(out, "START") and len(_lines(out, "MAIL")) == 1 and "mic ->" not in out


# -- the keeper wires it ------------------------------------------------------------------------

def test_the_lid_decision_runs_before_the_follow_mail_and_the_hold_before_any_start():
    s = KEEPER.read_text()
    loop = s[s.index("while :; do"):]
    assert loop.index('lid_watch "$t"') < loop.index('mail_me "ears: mic ->') < loop.index('if [ "$lid_on" = 1 ]')
    assert loop.index('if [ "$lid_on" = 1 ]') < loop.index("start_capture")
    assert loop.index("heard_watch") < loop.index('lid_watch "$t"')


def test_the_builtin_is_found_at_start_and_again_when_the_inputs_change():
    s = KEEPER.read_text()
    assert 'prev="$(inputs)"; builtin_refresh "$prev";' in s
    loop = s[s.index("while :; do"):]
    assert '[ "$now" = "$builtin_for" ] || builtin_refresh "$now"; t="$(target)"' in loop
    assert 'pick_input default "$(default_input)" "$builtin_mic"' in s


def test_beats_reach_the_lid_after_zeros():
    beat = _region("beat")
    assert beat.index('zeros_seen "$beat_pid"') < beat.index('lid_beat "$4" "$5"')
    s = KEEPER.read_text()
    assert s.index("# --- end stt ---") < s.index("# --- lid:") < s.index("# --- zeros:")
