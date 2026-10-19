"""EARS-280 item 2, round 3: the missed-reply clip is OPT-IN, and the keeper's room capture
(the one capture on HIS mic) is what opts in: 30 s, EARS_MISSED_REPLY_S overrides, 0 is off.

These run the keeper's own start_capture with its python as a stub that records its argv,
in tmp dirs: no device, no capture, no audio. The argv is parsed with the capture's own
parser, so a flag the capture would refuse fails here, not on the user's machine."""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ears.listen.__main__ import _missed_reply, build_parser
from ears.listen.missed import MissedReply

KEEPER = Path(__file__).resolve().parents[1] / "keeper" / "ears-keeper.sh"


def _functions() -> str:
    s = KEEPER.read_text()
    a = s.index("missed_reply_window() {")
    return s[a:s.index("stop_capture() {")]


@pytest.fixture
def keeper(tmp_path):
    argv = tmp_path / "py-argv"
    py = tmp_path / "python"
    # `-m ears.recording status --line` (the start note, EARS-387) answers like the real one,
    # through the real module; only the capture's argv is recorded
    py.write_text(f'#!/bin/sh\nif [ "$2" = ears.recording ]; then shift 2; exec {sys.executable} -m ears.recording "$@"; fi\n'
                  f'for a in "$@"; do printf "%s\\n" "$a"; done > "{argv}.tmp"\n'
                  f'mv "{argv}.tmp" "{argv}"\n')
    py.chmod(0o755)
    (tmp_path / "silero.onnx").write_bytes(b"onnx")   # start_capture holds without one (EARS-222)
    stubs = ('note() { echo "$*"; }\nmail_me() { :; }\ncapture_running() { return 0; }\n'
             'sleep() { :; }\n')

    def run(**env):
        e = {k: v for k, v in os.environ.items() if k not in ("EARS_MISSED_REPLY_S", "EARS_RECORD")}
        e.update(W=str(tmp_path), PY=str(py), MOD="ears.listen", RUN=str(tmp_path), LOGDIR=str(tmp_path),
                 SILERO=str(tmp_path / "silero.onnx"), **env)
        script = ('set -uo pipefail\nW="$W"; PY="$PY"; MOD="$MOD"; RUN="$RUN"; LOGDIR="$LOGDIR"; '
                  'SILERO="$SILERO"\n' + stubs + _functions() + '\nstart_capture airpods\n')
        out = subprocess.run(["bash", "-c", script], env=e, capture_output=True, text=True,
                             timeout=30, check=True)
        for _ in range(100):  # the capture is started in the background
            if argv.exists():
                break
            time.sleep(0.05)
        lines = argv.read_text().splitlines()
        return out.stdout, lines[lines.index("capture"):]

    return run


def test_the_room_capture_opts_in_at_30s(keeper):
    log, args = keeper()
    assert args[args.index("--missed-reply-window") + 1] == "30"
    a = build_parser().parse_args(args)
    assert a.source == "mic" and a.device == "airpods"
    mr = _missed_reply(a)
    assert isinstance(mr, MissedReply) and mr.window_s == 30.0
    assert "missed-reply clips: window 30s" in log


@pytest.mark.parametrize("value,window", [("12", 12.0), ("7.5", 7.5)])
def test_one_env_var_sets_the_window(keeper, value, window):
    _log, args = keeper(EARS_MISSED_REPLY_S=value)
    assert _missed_reply(build_parser().parse_args(args)).window_s == window


def test_zero_turns_it_off(keeper):
    _log, args = keeper(EARS_MISSED_REPLY_S="0")
    assert args[args.index("--missed-reply-window") + 1] == "0"
    assert _missed_reply(build_parser().parse_args(args)) is None


@pytest.mark.parametrize("bad", ["abc", "-5", "30s", "1e3", "3 0"])
def test_a_bad_value_is_off_and_said_never_a_dead_capture(keeper, bad):
    log, args = keeper(EARS_MISSED_REPLY_S=bad)
    a = build_parser().parse_args(args)  # the capture still parses: it would start
    assert _missed_reply(a) is None
    assert "is not seconds: missed-reply clips off" in log


def test_the_start_note_says_recording_is_off_by_default_and_why(keeper):
    """EARS-387: the window is passed as before, but the start note says whether anything is
    KEPT, and which layer decided. The keeper passes no --record: the capture reads the env."""
    log, args = keeper()
    assert "--record" not in args and "--no-record" not in args
    assert "recording: off (default), retention 7 days (default)" in log


def test_the_start_note_names_the_env_that_turned_it_on(keeper):
    log, args = keeper(EARS_RECORD="1")
    assert "--record" not in args                     # never mapped onto the flag
    assert "recording: on (env EARS_RECORD=1)" in log
