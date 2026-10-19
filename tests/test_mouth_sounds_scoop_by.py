"""mouth-sounds: every scoop line names who moved the heard cursor (EARS-148).

Two different tools can scoop, and the log says which one
scooped. The keeper is run as launchd runs it
(`python keeper/mouth-sounds.py`), DRY, over temp cursor/heard/log/settings/stop
paths (the pattern of test_ears_sounds_call.py): seed the cursors, wait for the
banner (the first snapshot is taken before it prints), move them, and read the
scoop lines. A cursor's `by` is copied; a cursor with none logs `"by": null`,
never a guess; `seq` is `[old, new]`, null inside the pair when not an int.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import timedelta, timezone
from pathlib import Path

import pytest

from tests._anchor import ANCHOR, ANCHOR_SUMMER

pytest.importorskip("numpy")
# keeper/mouth-sounds.py imports sounddevice at the top. It gets a one-line stub first on
# its PYTHONPATH (as test_mouth_sounds_wake.py does), never the real PortAudio, and DRY
# keeps it from asking for a device at all (EARS-320 B1-B2: this file used to import the
# real module into pytest, and the keeper loaded it too).
SD_STUB = "class PortAudioError(Exception):\n    pass\n"

SCRIPT = Path(__file__).resolve().parents[1] / "keeper" / "mouth-sounds.py"
LOG_NAME = f"heard_{ANCHOR:%Y-%m-%d}.jsonl"

BY = {"event": "PostToolBatch", "caller": "/x/heard-partials-hook", "pid": 41234,
      "at": ANCHOR_SUMMER.astimezone(timezone(timedelta(hours=11))).isoformat(timespec="milliseconds"), "heard": "/x/src/ears/heard.py",
      "session": "c100ef85-9fe3-59e6-d137-8452abe2fd1c"}


def _heard_log(path: Path) -> list[int]:
    """Write a heard log: 2 rows, then 3 of Theo's, then 1 agent row. End offsets."""
    recs = [{"seq": 1, "kind": "said"}, {"seq": 2, "kind": "turn", "source": "agent"},
            {"seq": 3, "kind": "partial"}, {"seq": 4, "kind": "partial", "source": "mic"},
            {"seq": 5, "kind": "turn", "source": "call"}, {"seq": 6, "kind": "said"}]
    ends, pos = [], 0
    with open(path, "wb") as f:
        for r in recs:
            b = (json.dumps(r) + "\n").encode()
            f.write(b)
            pos += len(b)
            ends.append(pos)
    return ends


def _put(cdir: Path, sess: str, cur: dict) -> None:
    tmp = cdir / f"{sess}.tmp{os.getpid()}"            # as save_cursor: tmp, then replace
    tmp.write_text(json.dumps(cur) + "\n")
    os.replace(tmp, cdir / sess)


def _run(tmp_path: Path, seeds: dict, moves: dict, wait_for: int) -> tuple[list[dict], Path]:
    """Run the keeper DRY; seed `seeds`, then after the banner write `moves`.
    Returns the scoop lines once `wait_for` have landed (or after 8 s)."""
    cdir, hdir = tmp_path / "cursors", tmp_path / "heard"
    for d in (cdir, hdir, tmp_path / "md" / "new", tmp_path / "md" / "cur"):
        d.mkdir(parents=True, exist_ok=True)
    for sess, cur in seeds.items():
        _put(cdir, sess, cur)
    log, stop, settings = tmp_path / "sounds.jsonl", tmp_path / "STOP", tmp_path / "settings.json"
    stub = tmp_path / "stub"
    stub.mkdir(exist_ok=True)
    (stub / "sounddevice.py").write_text(SD_STUB)
    env = dict(os.environ, PYTHONPATH=str(stub), MOUTH_SOUNDS_DRY="1", MOUTH_SOUNDS_CURSORS=str(cdir),
               MOUTH_SOUNDS_HEARD_DIR=str(hdir), MOUTH_SOUNDS_LOG=str(log),
               MOUTH_SOUNDS_SETTINGS=str(settings), MOUTH_SOUNDS_STOP=str(stop),
               MOUTH_MAILDIR=str(tmp_path / "md"),
               MOUTH_SOUNDS_FIRED=str(tmp_path / "fired"), MOUTH_SOUNDS_CANNED=str(tmp_path / "canned"))
    keeper = subprocess.Popen([sys.executable, str(SCRIPT)], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lines: list[dict] = []
    try:
        banner = keeper.stdout.readline()
        assert banner.startswith("mouth-sounds: watching"), banner
        for sess, cur in moves.items():
            _put(cdir, sess, cur)
        deadline = time.time() + 8
        while time.time() < deadline:
            time.sleep(0.1)
            if log.exists():
                lines = [json.loads(x) for x in log.read_text().splitlines()]
                lines = [x for x in lines if x.get("sound") == "scoop"]
                if len(lines) >= wait_for:
                    break
        time.sleep(0.4)                                  # a stray extra line would land now
        if log.exists():
            lines = [x for x in map(json.loads, log.read_text().splitlines()) if x.get("sound") == "scoop"]
    finally:
        stop.touch()
        try:
            keeper.wait(timeout=10)
        except subprocess.TimeoutExpired:
            keeper.kill()
    assert keeper.returncode == 0, keeper.stdout.read()
    return lines, settings


@pytest.fixture(scope="module")
def one_run(tmp_path_factory):
    """One keeper run over five cursors, each moved past Theo's 3 rows but one."""
    tmp = tmp_path_factory.mktemp("mouth-scoop")
    (tmp / "heard").mkdir()
    ends = _heard_log(tmp / "heard" / LOG_NAME)
    seed = {"file": LOG_NAME, "offset": ends[1]}        # after seq 2, before Theo's rows
    moved = {"file": LOG_NAME, "offset": ends[5]}       # past them all
    seeds = {
        "aaaaaaaa-with-by": {"seq": 2, **seed, "by": {**BY, "pid": 1}},   # the previous writer
        "bbbbbbbb-legacy": {"seq": 2, **seed},
        "cccccccc-junk-by": {"seq": 2, **seed},
        "dddddddd-no-seq": {**seed},
        "eeeeeeee-no-theo": {"seq": 5, "file": LOG_NAME, "offset": ends[4]},
    }
    moves = {
        "aaaaaaaa-with-by": {"seq": 6, **moved, "by": BY},
        "bbbbbbbb-legacy": {"seq": 6, **moved},                      # an old writer: no by
        "cccccccc-junk-by": {"seq": "6", **moved, "by": "PostToolBatch"},   # non-dict by, str seq
        "dddddddd-no-seq": {**moved, "by": None},
        "eeeeeeee-no-theo": {"seq": 6, **moved, "by": BY},          # past an agent row only
    }
    lines, settings = _run(tmp, seeds, moves, wait_for=4)
    return {x["session"]: x for x in lines}, settings


def test_a_cursor_written_with_by_scoops_under_that_by(one_run):
    by_sess, _ = one_run
    line = by_sess["aaaaaaaa"]
    assert line["by"] == BY                              # the NEW writer's, not the seed's pid 1
    assert line["seq"] == [2, 6]
    assert line["rows"] == 3


def test_a_legacy_cursor_with_no_by_logs_null_and_still_scoops(one_run):
    by_sess, _ = one_run
    line = by_sess["bbbbbbbb"]
    assert "by" in line and line["by"] is None
    assert line["seq"] == [2, 6] and line["rows"] == 3


def test_a_non_dict_by_is_null_and_a_non_int_seq_is_null_in_the_pair(one_run):
    by_sess, _ = one_run
    line = by_sess["cccccccc"]
    assert line["by"] is None
    assert line["seq"] == [2, None] and line["rows"] == 3


def test_a_cursor_with_no_seq_still_scoops_with_a_null_pair(one_run):
    by_sess, _ = one_run
    line = by_sess["dddddddd"]
    assert line["by"] is None and line["seq"] == [None, None] and line["rows"] == 3


def test_a_move_past_no_theo_row_makes_no_scoop(one_run):
    by_sess, _ = one_run
    assert "eeeeeeee" not in by_sess
    assert sorted(by_sess) == ["aaaaaaaa", "bbbbbbbb", "cccccccc", "dddddddd"]


def test_every_scoop_line_carries_by_and_seq_keys(one_run):
    by_sess, settings = one_run
    for line in by_sess.values():
        assert {"ts", "sound", "session", "rows", "seq", "by", "device", "error"} <= set(line)
        assert line["device"] is None and line["error"] is None         # DRY: no audio device
    assert settings.exists()                             # the temp settings, not the real settings folder


def test_a_bool_seq_is_not_an_int(tmp_path):
    (tmp_path / "heard").mkdir()
    ends = _heard_log(tmp_path / "heard" / LOG_NAME)
    lines, _ = _run(tmp_path, {"ffffffff-bool": {"seq": True, "file": LOG_NAME, "offset": ends[1]}},
                    {"ffffffff-bool": {"seq": 6, "file": LOG_NAME, "offset": ends[5], "by": BY}},
                    wait_for=1)
    (line,) = lines
    assert line["seq"] == [None, 6] and line["by"] == BY
