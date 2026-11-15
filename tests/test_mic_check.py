"""The install mic check's core (``ears.mic_check``): the bound, the verdict, the wait.

Fakes only: heard rows are written by the real ``heard`` writer (or the real capture loop
on a synthetic source) into a tmp dir, the clock is a fake that each ``sleep`` advances,
and ``ps``/``system_profiler`` are stand-ins. No microphone is opened, here or by the code.
"""

from __future__ import annotations

import ast
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ears import heard, mic_check as mc
from ears.listen import ArraySource, capture
from tests.listen_helpers import FakeSTT, RecordingSink, energy_detector, synth

ROOT = Path(__file__).resolve().parent.parent


# -- fakes --------------------------------------------------------------------------


class Clock:
    """A fake monotonic clock; ``sleep`` advances it."""

    def __init__(self):
        self.t = 100.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        assert s >= 0
        self.sleeps.append(s)
        self.t += s


def beat(peak, *, source="mic", session="ears-keeper", device="MacBook Pro Microphone", pid=4242, **kw):
    rec = {"kind": "event", "event": "heartbeat", "source": source, "session": session,
           "device": device, "pid": pid, **kw}
    if peak is not None:
        rec["peak"] = peak
    return rec


def room_event(name, **kw):
    return {"kind": "event", "event": name, "source": "mic", "session": "ears-keeper",
            "device": "MacBook Pro Microphone", **kw}


def keeper_event(name, text, device="none"):
    """A row the keeper writes itself (``$HEARD write event``): no source or session."""
    return {"kind": "event", "event": name, "text": text, "device": device}


def _append_in(directory: Path, kind: str, fields: dict) -> None:
    sink = RecordingSink(directory)
    sink.write({"kind": kind, **fields})


# -- the acceptance test: each fake source gets its verdict within the bound ---------------

SOURCES = {
    # what the keeper's room capture writes, per fake source, while the check waits
    "zeros": ([beat(0), beat(0), beat(0)], mc.ZEROS),
    "signal": ([beat(0), beat(1834)], mc.HEARD),
    "error": ([room_event("listen-started"),
               room_event("listen-stopped", reason="error", error="cannot open the input")], mc.ERROR),
    "nothing": ([], mc.NOTHING),
}


class TimedReader:
    """``heard.read_after`` on a real dir, where the rows appear one per poll, as beats do."""

    def __init__(self, directory: Path, rows, clock: Clock):
        self.directory, self.rows, self.clock = directory, list(rows), clock
        self.calls = 0
        self.hints: list = []

    def __call__(self, seq, *, hint_file=None, hint_offset=0, directory=None):
        assert directory == self.directory
        self.calls += 1
        self.hints.append((hint_file, hint_offset))
        if self.rows:
            _append_in(self.directory, "event", {k: v for k, v in self.rows.pop(0).items() if k != "kind"})
        return heard.read_after(seq, hint_file=hint_file, hint_offset=hint_offset, directory=directory)


@pytest.mark.parametrize("name", list(SOURCES))
def test_each_fake_source_gets_its_verdict_within_the_bound(tmp_path, name):
    rows, want = SOURCES[name]
    d = tmp_path / "conversations"
    _append_in(d, "event", {"event": "heartbeat", "source": "mic", "session": "ears-keeper", "peak": 999,
                            "device": "old"})   # before the keeper loaded: never counts
    start = mc.start_seq(d)
    clock = Clock()
    read = TimedReader(d, rows, clock)
    v = mc.wait(start, 45.0, read=read, clock=clock, sleep=clock.sleep, directory=d)
    assert v.kind == want
    waited = clock.t - 100.0
    assert waited <= 45.0 + mc.POLL_S
    if want == mc.HEARD:
        assert v.peak == 1834 and v.device == "MacBook Pro Microphone" and v.pid == 4242
        assert waited < 45.0, "heard is said at once, not at the bound"
    else:
        assert waited >= 45.0, "NOT HEARD is judged at the bound: the user may still click Allow"
    if want == mc.ZEROS:
        assert v.peaks == (0, 0, 0)
    if want == mc.ERROR:
        assert v.error == "cannot open the input"


# -- verdict(): pure ------------------------------------------------------------------


def test_a_mic_beat_with_sound_is_heard_at_once():
    v = mc.verdict([beat(1200)], 0.0, 45.0)
    assert v.kind == mc.HEARD and v.peak == 1200 and not v.failed


def test_beats_of_zero_wait_then_give_zeros_at_the_bound():
    assert mc.verdict([beat(0), beat(0)], 44.9, 45.0) is None
    v = mc.verdict([beat(0), beat(0)], 45.0, 45.0)
    assert v.kind == mc.ZEROS and v.failed and v.peaks == (0, 0)


def test_a_beat_after_zeros_is_heard_the_allow_click_inside_the_bound():
    assert mc.verdict([beat(0), beat(0), beat(7)], 30.0, 45.0).kind == mc.HEARD


def test_listen_stopped_with_error_gives_error_at_the_bound():
    rows = [room_event("listen-started"), room_event("listen-stopped", reason="error", error="boom")]
    assert mc.verdict(rows, 10.0, 45.0) is None
    v = mc.verdict(rows, 45.0, 45.0)
    assert v.kind == mc.ERROR and v.error == "boom"


def test_a_listen_stopped_for_another_reason_is_not_an_error():
    assert mc.verdict([room_event("listen-stopped", reason="stop")], 45.0, 45.0).kind == mc.NOTHING


def test_capture_wedged_gives_error():
    v = mc.verdict([room_event("capture-wedged", why="no frames for 90s", pid=77)], 45.0, 45.0)
    assert v.kind == mc.ERROR and v.pid == 77 and v.error == "no frames for 90s"


def test_a_call_legs_wedge_is_not_the_rooms_error():
    rows = [{**room_event("capture-wedged", why="x"), "session": "call:3"},
            {**room_event("listen-stopped", reason="error"), "source": "call"}]
    assert mc.verdict(rows, 45.0, 45.0).kind == mc.NOTHING


def test_no_rows_give_nothing_at_the_bound_and_none_before():
    assert mc.verdict([], 0.0, 45.0) is None
    assert mc.verdict([], 45.0, 45.0).kind == mc.NOTHING


def test_a_shut_lid_is_said_at_once():
    v = mc.verdict([keeper_event("ears-deaf", "room: lid: shut", "MacBook Pro Microphone")], 1.0, 45.0)
    assert v.kind == mc.LID and v.failed


def test_a_deaf_row_for_another_cause_is_not_the_lid():
    assert mc.verdict([keeper_event("ears-deaf", "room: zeros")], 1.0, 45.0) is None


def test_room_off_is_a_live_call_at_once_and_not_a_failure():
    v = mc.verdict([keeper_event("room-off", "call live: the room mic is off until hang-up", "call:3")], 1.0, 45.0)
    assert v.kind == mc.CALL and not v.failed


@pytest.mark.parametrize("other", [
    beat(5000, session="call:3"),
    beat(5000, session="some-agent-session"),
    beat(5000, source="call"),
    beat(None),                 # a beat with no peak says nothing either way
    beat(True),                 # a bool is not a peak
    beat("5000"),
    {"kind": "turn", "text": "hello", "source": "mic", "session": "ears-keeper", "peak": 5000},
])
def test_rows_that_are_not_the_room_captures_beat_are_ignored(other):
    assert mc.verdict([other], 1.0, 45.0) is None
    assert mc.verdict([other], 45.0, 45.0).kind == mc.NOTHING


def test_verdict_takes_the_readers_records_as_well_as_dicts(tmp_path):
    d = tmp_path / "c"
    _append_in(d, "event", {k: v for k, v in beat(3).items() if k != "kind"})
    recs, _ = heard.read_after(0, directory=d)
    assert mc.verdict(recs, 0.0, 45.0).kind == mc.HEARD


# -- bound() ----------------------------------------------------------------------------


@pytest.mark.parametrize("raw, want", [
    (None, (mc.BOUND_S, "")),
    ("", (mc.BOUND_S, "")),
    ("45", (45.0, "")),
    ("0.5", (0.5, "")),
    ("600", (600.0, "")),
    ("0", (0.0, "")),
    ("0.0", (0.0, "")),
    ("-1", (mc.BOUND_S, "(EARS_MIC_CHECK_S=-1 is not seconds: 60 s)")),
    ("abc", (mc.BOUND_S, "(EARS_MIC_CHECK_S=abc is not seconds: 60 s)")),
    ("601", (mc.BOUND_S, "(EARS_MIC_CHECK_S=601 is not seconds: 60 s)")),
    ("nan", (mc.BOUND_S, "(EARS_MIC_CHECK_S=nan is not seconds: 60 s)")),
    ("inf", (mc.BOUND_S, "(EARS_MIC_CHECK_S=inf is not seconds: 60 s)")),
])
def test_bound(raw, want):
    env = {} if raw is None else {"EARS_MIC_CHECK_S": raw}
    assert mc.bound(env) == want


def test_the_default_bound_is_one_constant_of_60_s():
    assert mc.BOUND_S == 60.0 and mc.bound({}) == (60.0, "")


def test_bound_reads_the_process_environment_by_default(monkeypatch):
    monkeypatch.setenv("EARS_MIC_CHECK_S", "12")
    assert mc.bound() == (12.0, "")


# -- heard_dir() and start_seq() ---------------------------------------------------------


@pytest.mark.parametrize("root", ["SESSIONVOICE_HOME", "VOICEMODE_BASE_DIR"])
def test_heard_dir_is_the_keepers_not_this_shells_override(monkeypatch, tmp_path, root):
    monkeypatch.setenv(root, str(tmp_path / "override"))
    d = mc.heard_dir()
    assert d.parts[-2:] == ("logs", "conversations")
    assert str(tmp_path / "override") not in str(d)


def test_start_seq_is_the_logs_last_seq(tmp_path):
    d = tmp_path / "c"
    assert mc.start_seq(d) == 0
    for _ in range(3):
        _append_in(d, "event", {"event": "heartbeat"})
    assert mc.start_seq(d) == heard.last_seq(d) == 3


# -- inputs(): opens nothing ------------------------------------------------------------


def _devices(tmp_path, inputs, pid=4321, version=1) -> Path:
    p = tmp_path / "audio-devices.json"
    p.write_text(json.dumps({"version": version, "writer": "input-watch", "pid": pid, "seq": 1,
                             "inputs": inputs, "outputs": []}))
    return p


PROFILER = """Audio:

    Devices:

        MacBook Pro Microphone:

          Default Input Device: Yes
          Input Channels: 1
          Manufacturer: Apple Inc.

        MacBook Pro Speakers:

          Default Output Device: Yes
          Output Channels: 2

        External USB Mic:

          Input Channels: 2
"""


def _no_profiler(_timeout):
    raise AssertionError("system_profiler ran while the devices file was fresh")


def test_a_fresh_devices_file_with_no_inputs_is_no_microphone(tmp_path):
    assert mc.inputs(path=_devices(tmp_path, []), alive=lambda pid: True, profiler=_no_profiler) == []


def test_a_fresh_devices_file_names_its_inputs(tmp_path):
    p = _devices(tmp_path, [{"name": "MacBook Pro Microphone", "uid": "x", "transport": "builtin"}])
    assert mc.inputs(path=p, alive=lambda pid: True, profiler=_no_profiler) == ["MacBook Pro Microphone"]


@pytest.mark.parametrize("make, alive", [
    (lambda t: _devices(t, []), lambda pid: False),                      # a dead writer
    (lambda t: _devices(t, [], version=2), lambda pid: True),            # a version it does not know
    (lambda t: _devices(t, [{"uid": "no name"}]), lambda pid: True),     # a device with no name
    (lambda t: _devices(t, "MacBook"), lambda pid: True),                # inputs not a list
    (lambda t: _devices(t, [], pid=True), lambda pid: True),             # a pid that is not one
    (lambda t: t / "missing.json", lambda pid: True),
])
def test_a_stale_devices_file_falls_back_to_system_profiler(tmp_path, make, alive):
    got = mc.inputs(path=make(tmp_path), alive=alive, profiler=lambda timeout: PROFILER)
    assert got == ["External USB Mic", "MacBook Pro Microphone"]


def test_system_profiler_timing_out_is_unknown(tmp_path, monkeypatch):
    def run(argv, **kw):
        assert argv == ["system_profiler", "SPAudioDataType"] and kw["timeout"] == mc.PROFILER_TIMEOUT_S
        raise subprocess.TimeoutExpired(argv, kw["timeout"])
    monkeypatch.setattr(mc.subprocess, "run", run)
    assert mc.inputs(path=tmp_path / "missing.json") is None


def test_system_profiler_failing_is_unknown(tmp_path):
    assert mc.inputs(path=tmp_path / "missing.json", profiler=lambda timeout: None) is None


def test_parse_profiler_reads_as_the_keeper_does():
    assert mc.parse_profiler(PROFILER) == ["External USB Mic", "MacBook Pro Microphone"]
    assert mc.parse_profiler("") == []


def test_the_devices_file_is_the_keepers_not_this_shells_override(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIO_DEVICES_FILE", str(tmp_path / "mine.json"))
    assert mc.devices_file() != tmp_path / "mine.json"
    assert mc.devices_file().name == "audio-devices.json"


# -- app(): the name macOS asks about ------------------------------------------------------


def test_app_names_a_uv_python_by_its_file():
    real = "/opt/uv/python/cpython-3.13.1-macos-aarch64-none/bin/python3.13"
    name, path = mc.app(4242, "/venv/bin/python", comm=lambda pid: "/venv/bin/python3",
                        realpath=lambda p: real if p == "/venv/bin/python3" else p)
    assert (name, path) == ("python3.13", real)


def test_app_names_a_framework_python_by_its_app():
    fw = "/Library/Frameworks/Python.framework/Versions/3.13/Resources/Python.app/Contents/MacOS/Python"
    assert mc.app(4242, "/x", comm=lambda pid: fw, realpath=lambda p: p) == ("Python", fw)


def test_a_dead_pid_falls_back_to_ears_py():
    assert mc.app(999999, "/opt/py/bin/python3.12", comm=lambda pid: None,
                  realpath=lambda p: p) == ("python3.12", "/opt/py/bin/python3.12")


def test_no_pid_falls_back_to_ears_py_without_running_ps():
    def comm(pid):
        raise AssertionError("ps ran with no pid")
    assert mc.app(None, "/opt/py/bin/python3.12", comm=comm, realpath=lambda p: p)[0] == "python3.12"


def test_ps_is_bounded(monkeypatch):
    seen = {}

    def run(argv, **kw):
        seen.update(argv=argv, **kw)
        raise subprocess.TimeoutExpired(argv, kw["timeout"])
    monkeypatch.setattr(mc.subprocess, "run", run)
    assert mc._ps_comm(4242) is None
    assert seen["argv"] == ["ps", "-o", "comm=", "-p", "4242"] and seen["timeout"] == mc.PS_TIMEOUT_S


# -- the lines ------------------------------------------------------------------------------


def test_the_lines():
    assert mc.row(mc.NC_LISTEN_OFF) == "mic    not checked: listening is off; install with --listen to check now"
    assert mc.row(mc.NC_OFF) == "mic    not checked: EARS_MIC_CHECK_S=0"
    assert mc.row(mc.NC_NO_INPUT) == "mic    not checked: this Mac has no microphone (no audio input device)"
    assert mc.row(mc.NC_HAND_MADE.format(label="dev.x")) == (
        "mic    not checked: the keeper here is hand-made (dev.x); install did not load it")
    assert mc.waiting(60.0) == ("mic    listening up to 60 s for the keeper's first sound (EARS_MIC_CHECK_S); "
                                "if macOS asks to use the microphone, click Allow")
    assert "(EARS_MIC_CHECK_S=abc is not seconds: 60 s)" in mc.waiting(60.0, mc.bound({"EARS_MIC_CHECK_S": "abc"})[1])
    assert mc.row(mc.WOULD_WAIT.format(bound="60")).startswith("mic    would wait up to 60 s")
    heard_v = mc.Verdict(mc.HEARD, 18.2, device="MacBook Pro Microphone", peak=1834)
    assert mc.line(heard_v, 60.0) == "mic    heard: MacBook Pro Microphone (peak 1834), 18 s after the keeper loaded"
    assert mc.line(mc.Verdict(mc.ZEROS, 60.0), 60.0, app_label="python3.13") == (
        "ears services install: mic NOT HEARD in 60 s (only silence): open System Settings > Privacy & Security > "
        "Microphone and turn on python3.13 (the ears keeper's capture), then run: ears services install --only keeper")
    assert "(no sound arrived)" in mc.line(mc.Verdict(mc.NOTHING), 60.0, app_label="P")
    assert "(the capture failed)" in mc.line(mc.Verdict(mc.ERROR), 60.0, app_label="P")
    assert mc.line(mc.Verdict(mc.LID), 60.0) == (
        "ears services install: mic NOT HEARD: the lid is shut, so the built-in mic is off; "
        "open the lid or plug in a mic, then run: ears services install --only keeper")
    assert mc.line(mc.Verdict(mc.CALL), 60.0) == "mic    not checked: a call is live, so the room mic is off until it ends"


# -- the wait: bounded, the cursor, and never on a human (R5) ----------------------------


class RaisingStdin(io.TextIOBase):
    def __init__(self, tty):
        self.tty = tty

    def isatty(self):
        return self.tty

    def read(self, *a):
        raise AssertionError("the check read stdin")

    readline = read

    def readlines(self, *a):
        raise AssertionError("the check read stdin")

    def fileno(self):
        raise AssertionError("the check touched stdin's fd")


@pytest.mark.parametrize("tty", [True, False])
def test_the_wait_never_reads_stdin_and_is_the_same_with_or_without_a_tty(tmp_path, monkeypatch, tty, capsys):
    monkeypatch.setattr(sys, "stdin", RaisingStdin(tty))
    clock = Clock()
    d = tmp_path / "c"
    calls = []

    def read(seq, **kw):
        calls.append(clock.t)
        return [], None
    v = mc.wait(0, 45.0, read=read, clock=clock, sleep=clock.sleep, directory=d)
    assert v.kind == mc.NOTHING
    assert 45.0 <= clock.t - 100.0 <= 45.0 + mc.POLL_S
    assert all(s <= mc.POLL_S for s in clock.sleeps)
    assert len(calls) == 46   # one read a second, the last at the bound
    assert capsys.readouterr() == ("", ""), "the wait prints nothing: install prints its lines"


def test_the_worst_case_stays_bounded_with_slow_reads(tmp_path):
    # each read costs 0.4 s, and app()'s ps its 2 s: still bound + poll + one read + ps
    clock = Clock()

    def read(seq, **kw):
        clock.t += 0.4
        return [], None
    v = mc.wait(0, 45.0, read=read, clock=clock, sleep=clock.sleep, directory=tmp_path)

    def comm(pid):
        clock.t += mc.PS_TIMEOUT_S
        return None
    mc.app(v.pid or 1, "/py/python3", comm=comm, realpath=lambda p: p)
    assert clock.t - 100.0 <= 45.0 + mc.POLL_S + 0.4 + mc.PS_TIMEOUT_S


def test_a_failing_read_is_a_poll_with_no_rows(tmp_path):
    clock = Clock()

    def read(seq, **kw):
        raise OSError("gone")
    assert mc.wait(0, 3.0, read=read, clock=clock, sleep=clock.sleep, directory=tmp_path).kind == mc.NOTHING


def test_the_wait_polls_from_the_cursor_the_last_read_returned(tmp_path):
    d = tmp_path / "c"
    for _ in range(5):
        _append_in(d, "event", {"event": "heartbeat", "source": "mic", "session": "other", "peak": 9})
    start = mc.start_seq(d)
    clock = Clock()
    read = TimedReader(d, [beat(0), beat(0), beat(0), beat(55)], clock)
    v = mc.wait(start, 45.0, read=read, clock=clock, sleep=clock.sleep, directory=d)
    assert v.kind == mc.HEARD and v.peaks == (0, 0, 0, 55)
    assert read.hints[0] == (None, 0)
    name = read.hints[1][0]
    assert name == heard.log_files(d)[-1].name
    offsets = [off for _f, off in read.hints[1:]]
    assert offsets == sorted(offsets) and offsets[0] > 0, "each poll starts where the last stopped"


def test_rows_before_the_start_never_count(tmp_path):
    d = tmp_path / "c"
    _append_in(d, "event", {k: v for k, v in beat(4000).items() if k != "kind"})
    clock = Clock()
    v = mc.wait(mc.start_seq(d), 5.0, clock=clock, sleep=clock.sleep, directory=d)
    assert v.kind == mc.NOTHING


def test_the_wait_reads_the_real_heard_log_by_default(tmp_path):
    d = tmp_path / "c"
    start = mc.start_seq(d)
    _append_in(d, "event", {k: v for k, v in beat(321).items() if k != "kind"})
    clock = Clock()
    v = mc.wait(start, 45.0, clock=clock, sleep=clock.sleep, directory=d)
    assert v.kind == mc.HEARD and v.peak == 321 and clock.t == 100.0


# -- C2: the real beat writer, read by the new reader --------------------------------------


def _room_capture(directory: Path, monkeypatch, tmp_path, segments):
    """The real capture loop on a synthetic source named as the keeper's room capture is."""
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm"))
    source = ArraySource(synth(segments), name="mic", device="MacBook Pro Microphone", realtime=False,
                         tail_silence_s=0)
    capture(source, FakeSTT(), RecordingSink(directory, None), detector=energy_detector(), stt_workers=0,
            heartbeat=1.0, session="ears-keeper")


def test_the_real_writers_zero_beats_read_as_zeros(tmp_path, monkeypatch):
    d = tmp_path / "c"
    start = mc.start_seq(d)
    _room_capture(d, monkeypatch, tmp_path, [("silence", 3.0)])
    clock = Clock()
    v = mc.wait(start, 45.0, clock=clock, sleep=clock.sleep, directory=d)
    assert v.kind == mc.ZEROS and len(v.peaks) >= 2 and set(v.peaks) == {0}
    assert v.pid == os.getpid(), "the room capture's beats carry its pid: app() starts from it"


def test_the_real_writers_beats_with_sound_read_as_heard(tmp_path, monkeypatch):
    d = tmp_path / "c"
    start = mc.start_seq(d)
    _room_capture(d, monkeypatch, tmp_path, [("silence", 2.5), ("speech", 1.5), ("silence", 1.0)])
    clock = Clock()
    v = mc.wait(start, 45.0, clock=clock, sleep=clock.sleep, directory=d)
    assert v.kind == mc.HEARD and v.peak > 0 and v.peaks[0] == 0, "zeros first, then sound"
    assert v.device == "MacBook Pro Microphone" and v.pid == os.getpid()
    assert clock.t == 100.0, "heard at once, on the first read"


def test_the_keepers_job_sets_no_session_so_its_capture_is_ears_keeper(monkeypatch):
    # the capture's --session is ${EARS_SESSION:-${CLAUDE_CODE_SESSION_ID:-ears-keeper}}; the
    # job must carry neither, or the room capture's rows stop being ROOM_SESSION's
    from ears import services
    monkeypatch.setenv("EARS_SESSION", "leak")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "leak")
    env = services.spec("keeper")["EnvironmentVariables"]
    assert "EARS_SESSION" not in env and "CLAUDE_CODE_SESSION_ID" not in env
    script = (ROOT / "keeper" / "ears-keeper.sh").read_text()
    assert '--source mic' in script
    assert '--session "${EARS_SESSION:-${CLAUDE_CODE_SESSION_ID:-' + mc.ROOM_SESSION + '}}"' in script


# -- R8: the check opens no microphone, by construction ----------------------------------------

def _imports(path: Path) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            out.add(mod)
            out |= {f"{mod}.{a.name}" for a in node.names}
    return out


@pytest.mark.parametrize("rel", ["src/ears/mic_check.py", "src/ears/services.py"])
def test_no_mic_opener_is_imported(rel):
    got = _imports(ROOT / rel)
    assert not {m for m in got if m == "sounddevice" or m.startswith("sounddevice.")}
    assert not {m for m in got if m == "ears.listen.sources" or m.startswith("ears.listen.sources.")}
    if rel.endswith("mic_check.py"):
        assert not {m for m in got if m.startswith("ears.listen")}, "the check never pulls in the capture"


def test_importing_the_check_loads_no_audio_module():
    code = ("import sys; import ears.mic_check; "
            "bad = [m for m in sys.modules if m == 'sounddevice' or m.startswith('ears.listen')]; "
            "print(bad); sys.exit(1 if bad else 0)")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stdout + r.stderr


# -- the event names, pinned to their writers (a rename must not move a verdict) ---------------


def test_the_wedge_event_is_the_watchdogs():
    from ears.listen import watchdog
    assert mc.EV_WEDGED == watchdog.ROW_EVENT


def test_the_keeper_still_writes_room_off_and_the_rooms_deaf_rows():
    script = (ROOT / "keeper" / "ears-keeper.sh").read_text()
    assert "$HEARD write event " + mc.EV_ROOM_OFF + " " in script
    # deaf_set writes ears-deaf with text "$ch: $cause...": the room's lid is "room: lid..."
    assert 'deaf_event() {  # $1 event, $2 device, $3 text' in script
    assert 'deaf_event ' + mc.EV_DEAF + ' "$dev" "$ch: $cause' in script
    assert "deaf_set room lid" in script or 'deaf_set room "lid"' in script


def test_the_heartbeat_and_listen_stopped_names_are_heards():
    assert heard.EV_HEARTBEAT == "heartbeat" and heard.EV_LISTEN_STOPPED == "listen-stopped"


# -- ears.json, read as the keeper reads it ----------------------------------------------------


@pytest.mark.parametrize("text, on", [
    (None, True),                       # missing: the keeper hears
    ("{}", True),                       # no listen key: on
    ('{"listen": true}', True),
    ('{"listen": false}', False),
    ('{"listen": 0}', True),            # only false is off
    ("[1]", True),                      # not an object: as unreadable
    ("not json", True),
])
def test_listening_is_the_keepers_rule(tmp_path, text, on):
    p = tmp_path / "ears.json"
    if text is not None:
        p.write_text(text)
    assert mc.listening(mc.keeper_conf(p)) is on


@pytest.mark.parametrize("conf, want", [
    ({}, None), ({"input": "default"}, None), ({"input": ""}, None), ({"input": "  "}, None),
    ({"input": None}, None), ({"input": " USB Mic "}, "USB Mic"),
])
def test_pinned(conf, want):
    assert mc.pinned(conf) == want


def test_pinned_absent_matches_as_the_keeper_does():
    conf = {"input": "USB Mic"}
    assert mc.pinned_absent(conf, ["MacBook Pro Microphone"]) == "USB Mic"
    assert mc.pinned_absent(conf, ["usb mic"]) is None            # any case
    assert mc.pinned_absent(conf, ["USB Mic 2"]) == "USB Mic"     # exact, not a substring
    assert mc.pinned_absent(conf, None) is None                   # unknown: wait as usual
    assert mc.pinned_absent({}, []) is None                       # nothing pinned


def test_the_pinned_line_names_the_input_and_the_fix():
    line = mc.fail(mc.PINNED_LINE.format(name="Yeti"))
    assert line == ("ears services install: mic NOT HEARD: ears.json pins the input to Yeti, which is not "
                    "connected; plug in Yeti, or remove the pin from ears.json, then run: "
                    "ears services install --only keeper")


def test_the_evidence_record():
    v = mc.Verdict(mc.ZEROS, 60.4, pid=7, peaks=(0, 0), error="x")
    assert mc.evidence(v, 60.0, app_path="/p/python3.13") == (
        "verdict zeros; waited 60.4 s of 60 s; beats 2; peaks 0,0; pid 7; path /p/python3.13; error x")
    assert mc.evidence(mc.Verdict(mc.NOTHING, 60.0), 60.0) == "verdict nothing; waited 60.0 s of 60 s; beats 0"
