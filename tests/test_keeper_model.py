"""EARS-222 item 5: with no Silero model the keeper says so ONCE and holds, instead of
starting a capture that dies "Silero model not found" every 4 s forever (seen on a new host's install,
Sun 2030-06-09). These run the keeper's model gate alone, as test_keeper_calls.py does
its calls functions. EARS-226: the gate reports through the keeper's ONE deaf path (deaf_set /
deaf_clear), so the deaf block runs with it."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEEPER = ROOT / "keeper" / "ears-keeper.sh"


def _model_block() -> str:
    s = KEEPER.read_text()
    return s[s.index("# --- deaf:"):s.index("# --- end deaf ---")] + s[s.index("# --- model:"):s.index("# --- end model ---")]


def _bash(script: str, env: dict) -> str:
    pre = ('note() { echo "NOTE $*"; }\nmail_me() { echo "MAIL $*"; }\n'
           'heard_stub() { shift 2; echo "HEARD $*" >&3; }\nHEARD=heard_stub\nexec 3>&1\n')
    out = subprocess.run(["bash", "-c", "set -uo pipefail\n" + pre + _model_block() + "\n" + script],
                         env=dict(os.environ, **env), capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_present_model_starts_quietly(tmp_path):
    m = tmp_path / "silero_vad.onnx"; m.write_bytes(b"onnx")
    out = _bash('SILERO="$M"; model_ready && echo READY', {"M": str(m)})
    assert out.strip() == "READY"


def test_missing_model_is_said_once_and_held(tmp_path):
    out = _bash('SILERO="$M"; for i in 1 2 3 4 5; do model_ready Mic && echo READY || echo HELD; done',
                {"M": str(tmp_path / "none.onnx"), "EARS_MODEL_RECHECK": "0"})
    lines = out.splitlines()
    assert lines.count("HELD") == 5 and "READY" not in lines
    assert sum(l.startswith("NOTE Silero model not found") for l in lines) == 1
    assert [l for l in lines if l.startswith("MAIL ")] == ["MAIL ears: DEAF, no Silero model: run ears install EOM"]
    assert [l for l in lines if l.startswith("HEARD ")] == [
        f"HEARD ears-deaf --device Mic --text room: model: {tmp_path / 'none.onnx'}"]


def test_the_model_arriving_lets_the_capture_start(tmp_path):
    m = tmp_path / "silero_vad.onnx"
    out = _bash('SILERO="$M"; model_ready "AirPods Pro" || echo HELD; : >"$M"; model_ready || echo STILL; '
                'echo onnx >"$M"; model_ready "AirPods Pro" && echo READY; deaf_room && echo STILL-DEAF; '
                'model_started "AirPods Pro"; deaf_room || echo HEARING',
                {"M": str(m), "EARS_MODEL_RECHECK": "0"})
    lines = out.splitlines()
    assert lines[-1] == "HEARING" and "STILL" in lines                 # an empty file is no model
    # the model is there, but the episode ends only once a capture is seen running
    assert "READY" in lines and "STILL-DEAF" in lines
    mails = [l for l in lines if l.startswith("MAIL ")]
    assert "NOTE Silero model found" in out and len(mails) == 2
    assert mails[1].startswith("MAIL ears: hearing again on AirPods Pro at ") and mails[1].endswith(" EOM")
    assert any(l.startswith("HEARD ears-hearing --device AirPods Pro --text room: hearing again, was model for ")
               for l in lines)


def test_the_model_arriving_is_one_mail_not_two(tmp_path):
    """EARS-226 do-002 M1: the model arriving used to mail `hearing again` AND `capture restarted`.
    The start that ends a model DEAF episode is a note; the recovery mail is the one mail. This
    runs the keeper's own start_capture, with the launch, the wait and the capture stubbed."""
    s = KEEPER.read_text()
    start = s[s.index("start_capture() {"):s.index("# --- stop:")]
    m = tmp_path / "silero_vad.onnx"
    out = _bash('SILERO="$M"; missed_reply_window() { echo "${EARS_MISSED_REPLY_S:-30}"; }; RUN="$T"; launch_capture() { :; }; sleep() { :; }; '
                'capture_running() { return 0; }\n' + start +
                '\nstart_capture Mic || echo HELD; echo =ARRIVES=; echo onnx >"$M"; start_capture "AirPods Pro"; '
                'deaf_room || echo HEARING',
                {"M": str(m), "T": str(tmp_path), "EARS_MODEL_RECHECK": "0"})
    before, after = out.split("=ARRIVES=")
    assert "HELD" in before and [l for l in before.splitlines() if l.startswith("MAIL ")] == [
        "MAIL ears: DEAF, no Silero model: run ears install EOM"]
    mails = [l for l in after.splitlines() if l.startswith("MAIL ")]
    assert len(mails) == 1 and mails[0].startswith("MAIL ears: hearing again on AirPods Pro at "), out
    assert "NOTE not mailed, the room is DEAF (model): ears: capture restarted on AirPods Pro" in after
    assert after.rstrip().endswith("HEARING")


def test_a_start_with_no_model_episode_still_mails_its_restart(tmp_path):
    s = KEEPER.read_text()
    start = s[s.index("start_capture() {"):s.index("# --- stop:")]
    m = tmp_path / "silero_vad.onnx"; m.write_bytes(b"onnx")
    out = _bash('SILERO="$M"; missed_reply_window() { echo "${EARS_MISSED_REPLY_S:-30}"; }; RUN="$T"; launch_capture() { :; }; sleep() { :; }; '
                'capture_running() { return 0; }\n' + start + '\nstart_capture Mic',
                {"M": str(m), "T": str(tmp_path)})
    mails = [l for l in out.splitlines() if l.startswith("MAIL ")]
    assert len(mails) == 1 and mails[0].startswith("MAIL ears: capture restarted on Mic at ")


def test_held_it_looks_again_only_slowly(tmp_path):
    m = tmp_path / "silero_vad.onnx"
    out = _bash('SILERO="$M"; model_ready; echo onnx >"$M"; model_ready && echo READY || echo WAITING',
                {"M": str(m), "EARS_MODEL_RECHECK": "300"})
    assert out.splitlines()[-1] == "WAITING"                        # not re-checked inside 300 s


def test_no_capture_starts_without_the_model():
    s = KEEPER.read_text()
    body = s[s.index("start_capture() {"):]
    assert body.splitlines()[1].strip().startswith('model_ready "$1" || return 1')
    assert s.index("# --- end deaf ---") < s.index("# --- model:") < s.index("start_capture() {")


def test_held_the_room_is_deaf_so_restart_mails_are_notes(tmp_path):
    out = _bash('SILERO="$M"; model_ready Mic; mail_unless_deaf "ears: capture restarted on Mic at 10:00 EOM"',
                {"M": str(tmp_path / "none.onnx")})
    assert out.count("MAIL ") == 1 and "NOTE not mailed, the room is DEAF (model)" in out
