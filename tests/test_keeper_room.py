"""The keeper's room capture is the one a keeper started (its STOP-capture-keeper- stop file).

A call-tap capture on "BlackHole 2ch" was stopped by the keeper within a second
(measured): FOLLOW mode took any capture for the room's. These run the
keeper's own functions against fake captures (a sleeping python with a capture argv),
under a pattern no live capture matches (EARS_CAPTURE_PAT).
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest

KEEPER = Path(__file__).resolve().parents[1] / "keeper" / "ears-keeper.sh"


def _functions() -> str:
    s = KEEPER.read_text()
    return s[s.index("CAPTURE_PAT="):s.index("start_capture() {")]


@pytest.fixture
def fake():
    tag = f"earstest{uuid.uuid4().hex[:8]}"
    procs = []

    def spawn(*argv, exe=sys.executable, code="import time; time.sleep(60)"):
        p = subprocess.Popen([exe, "-c", code, f"{tag}.listen", "capture", *argv])
        procs.append(p)
        return p.pid

    spawn.pat = rf"{tag}\.listen capture"
    yield spawn
    for p in procs:
        p.kill()
        p.wait()


def _run(pat: str, call: str) -> str:
    time.sleep(0.3)  # let ps see the argv
    env = dict(os.environ, EARS_CAPTURE_PAT=pat)
    out = subprocess.run(["bash", "-c", _functions() + "\n" + call], env=env,
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def _pids(lines: str) -> set:
    """The pids of foreign_captures' "pid: argv" lines. Compare these, never the text: the
    argv holds paths, and a path may hold a pid's digits (a checkout under .../135ed4ea-...)."""
    return {int(line.split(":", 1)[0]) for line in lines.splitlines()}


@pytest.fixture
def short_dir():
    """A dir under /tmp, not tmp_path: foreign_captures cuts each argv at 160 characters, and
    a long basetemp or TMPDIR (184 characters, measured) pushed a pid in tmp_path past the cut."""
    d = Path(tempfile.mkdtemp(dir="/tmp"))
    yield d
    shutil.rmtree(d)


def test_room_capture_is_the_keepers_not_call_tap_nor_call_leg(fake, short_dir):
    room = fake("--source", "mic", "--device", "airpods", "--stop-file", "/r/STOP-capture-keeper-203400")
    # The tap's argv holds the room's pid digits, as a checkout path can. They go in the
    # tap's first ARGUMENT: the code it runs opens with them, a bare expression that does
    # nothing. argv[0] is no place for them: a macOS framework python (Homebrew's) re-execs
    # into its Python.app, so ps shows that path, not the one it was started by. Started
    # through a short symlink, argv[0] stays short on the other builds, and the digits sit
    # well inside foreign_captures' cut at 160 on all of them.
    py = short_dir / "python"
    py.symlink_to(sys.executable)
    tap = fake("--source", "mic", "--device", "BlackHole 2ch", "--stop-file", "/r/STOP-call-tap",
               exe=str(py), code=f"{room}; import time; time.sleep(60)")
    leg = fake("--source", "call:5", "--stop-file", "/r/STOP-capture-keeper-000001")
    assert _run(fake.pat, "capture_pid") == str(room)
    assert _run(fake.pat, "capture_arg --device") == "airpods"
    foreign = _run(fake.pat, "foreign_captures")
    assert str(room) in foreign  # the digits are there: a substring test would fail here
    assert _pids(foreign) == {tap, leg}


def test_pids_reads_the_pid_field_not_the_argv():
    lines = "41357: /x135x/.venv/bin/python -c ... --stop-file /r/135/STOP-call-tap\n" \
            "7: /usr/bin/python3 -c ... --source call:5"
    assert _pids(lines) == {41357, 7}
    assert 135 not in _pids(lines) and 1357 not in _pids(lines)
    assert _pids("") == set()


def test_call_tap_alone_is_no_room_capture(fake):
    """So FOLLOW mode never touches its stop file: capture_running is false."""
    fake("--source", "mic", "--device", "BlackHole 2ch", "--stop-file", "/r/STOP-call-tap")
    assert _run(fake.pat, "capture_pid") == ""
    assert _run(fake.pat, "capture_running && echo yes || echo no") == "no"


def test_old_keepers_capture_is_still_adopted(fake):
    """The live capture today was started by the old keeper, stop file in another dir."""
    room = fake("--source", "mic", "--device", "airpods",
                "--stop-file", "/x/.agent/moonshot-ambient-listen/STOP-capture-keeper-174406")
    assert _run(fake.pat, "capture_pid") == str(room)


# -- the room is off while a call is live (when he leaves the computer, somebody else
#    at the computer must not be able to do prompt injection) --

def _run_conf(pat: str, call: str, conf: Path) -> str:
    time.sleep(0.3)
    # call-ears' published calls: a dir of the test's own, never a live call-ears dir
    env = dict(os.environ, EARS_CAPTURE_PAT=pat, EARS_CONF=str(conf), CALL_EARS_DIR=str(conf.parent / "ce"))
    out = subprocess.run(["bash", "-c", _functions() + "\n" + call], env=env,
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def test_no_call_no_gate(fake, tmp_path):
    fake("--source", "mic", "--device", "airpods", "--stop-file", "/r/STOP-capture-keeper-203400")
    assert _run_conf(fake.pat, "call_live", tmp_path / "none.json") == ""


def test_a_call_capture_is_a_live_call(fake, tmp_path):
    fake("--source", "mic", "--device", "airpods", "--stop-file", "/r/STOP-capture-keeper-203400")
    fake("--source", "call:54681", "--heartbeat", "15")
    assert _run_conf(fake.pat, "call_live", tmp_path / "none.json") == "call:54681"


def test_two_calls_are_both_named(fake, tmp_path):
    fake("--source", "call:5")
    fake("--source", "call:7")
    assert sorted(_run_conf(fake.pat, "call_live", tmp_path / "none.json").split(",")) == ["call:5", "call:7"]


def test_a_call_tap_on_a_mic_is_not_a_call(fake, tmp_path):
    """A call-tap is `--source mic` on BlackHole: not a call, so it never silences the room."""
    fake("--source", "mic", "--device", "BlackHole 2ch", "--stop-file", "/r/STOP-call-tap")
    assert _run_conf(fake.pat, "call_live", tmp_path / "none.json") == ""


def test_room_on_call_true_keeps_both(fake, tmp_path):
    conf = tmp_path / "ears.json"
    conf.write_text('{"listen": true, "room_on_call": true}')
    fake("--source", "call:5")
    assert _run_conf(fake.pat, "call_live", conf) == ""


@pytest.mark.parametrize("text", ['{"room_on_call": false}', '{"room_on_call": "yes"}', "not json", "[]"])
def test_anything_but_true_keeps_the_gate(fake, tmp_path, text):
    """Fail closed: only an explicit JSON true turns the gate off."""
    conf = tmp_path / "ears.json"
    conf.write_text(text)
    fake("--source", "call:5")
    assert _run_conf(fake.pat, "call_live", conf) == "call:5"


# -- a live call holds the room off even with no capture running (call 3081, Sun
#    2030-06-09: the ear leg sent no audio, each capture died after 10 s, and in each gap the
#    room mic came back on) --

def _publish(tmp_path, text, age=0.0):
    d = tmp_path / "ce"
    d.mkdir(exist_ok=True)
    f = d / "live"
    f.write_text(text)
    if age:
        t = time.time() - age
        os.utime(f, (t, t))


def test_a_published_call_is_live_with_no_capture(fake, tmp_path):
    fake("--source", "mic", "--device", "airpods", "--stop-file", "/r/STOP-capture-keeper-203400")
    _publish(tmp_path, "call:3081\n")
    assert _run_conf(fake.pat, "call_live", tmp_path / "none.json") == "call:3081"


def test_published_and_captured_merge_sorted_once(fake, tmp_path):
    fake("--source", "call:7")
    fake("--source", "call:3081")
    _publish(tmp_path, "call:3081,call:12\n")
    assert _run_conf(fake.pat, "call_live", tmp_path / "none.json") == "call:12,call:3081,call:7"


def test_a_stale_publish_is_ignored(fake, tmp_path):
    """call-ears down: its last word is not a live call; the captures decide."""
    _publish(tmp_path, "call:3081\n", age=30)
    assert _run_conf(fake.pat, "call_live", tmp_path / "none.json") == ""


def test_an_empty_publish_is_no_call(fake, tmp_path):
    _publish(tmp_path, "\n")
    assert _run_conf(fake.pat, "call_live", tmp_path / "none.json") == ""


def test_room_on_call_true_ignores_a_published_call(fake, tmp_path):
    conf = tmp_path / "ears.json"
    conf.write_text('{"room_on_call": true}')
    _publish(tmp_path, "call:3081\n")
    assert _run_conf(fake.pat, "call_live", conf) == ""
