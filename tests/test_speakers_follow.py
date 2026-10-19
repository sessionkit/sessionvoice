"""EARS-10: speaker ID follows the capture across device changes, and never goes deaf
without saying so. At Thu 2030-10-03 the AirPods came out, the keeper moved to the
MacBook mic, and a side-car pinned to --device airpods went deaf; seq 157865 had words and
came out "silent". No model: the side-car's functions and Ring are lifted by name, and the
relabel run uses the stand-in diarizer from test_speakers."""
import ast, collections, json, os, plistlib, re, subprocess, sys, threading, wave
import xml.dom.minidom
from pathlib import Path

import numpy as np
import pytest

from test_speakers import T0, _wav, stub_diarizer

ROOT = Path(__file__).resolve().parents[1]
SC = ROOT / "speakers" / "speaker-sidecar2.py"
PLIST = ROOT / "launchd" / "dev.sessionkit.voice.ears-speakers.plist.in"
SR = 16000


def lift(*names, **given):
    """The side-car's top-level defs and assignments NAMED, executed in file order into one
    namespace; GIVEN is in it first (the module globals a lifted assignment needs)."""
    ns = {"np": np, "re": re, "collections": collections, "threading": threading, "SR": SR, **given}
    for node in ast.parse(SC.read_text()).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names:
            exec(compile(ast.Module([node], []), "sidecar", "exec"), ns)
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") in names for t in node.targets):
            exec(compile(ast.Module([node], []), "sidecar", "exec"), ns)
    return ns


KEEPER = ("/x/ears/.venv/bin/python -m ears.listen capture --source mic --device {} "
          "--heartbeat 15 --agent ears --session ears-keeper --stop-file /x/STOP-capture-keeper-1")


@pytest.mark.parametrize("ps,want", [
    (KEEPER.format("airpods"), "airpods"),
    # a name with spaces ends at the next option
    (KEEPER.format("MacBook Pro Microphone"), "MacBook Pro Microphone"),
    # the old module name still counts
    (KEEPER.format("airpods").replace("ears.listen", "voice_mode.listen"), "airpods"),
    # a call tap is never a room mic; the keeper's real capture after it still is
    (KEEPER.format("BlackHole 16ch") + "\n" + KEEPER.format("airpods"), "airpods"),
    (KEEPER.format("BlackHole 16ch"), None),
    # another session's capture is not the keeper's
    (KEEPER.format("airpods").replace("ears-keeper", "call-ears"), None),
    # the last option on the line
    ("python -m ears.listen capture --session ears-keeper --device airpods", "airpods"),
    ("", None),
    ("/usr/bin/vim ears.listen capture notes --session ears-keeper", None),
])
def test_keeper_device(ps, want):
    ns = lift("KEEPER_SESSION", "CAPTURE", "virtual", "keeper_device")
    assert ns["keeper_device"](ps) == want


@pytest.mark.parametrize("x,y,want", [
    ("airpods", "airpods", True),
    ("airpods", "Theo's AirPods Pro", True),
    ("MacBook Pro Microphone", "macbook pro microphone", True),
    ("airpods", "MacBook Pro Microphone", False),
    ("", "airpods", False), (None, "airpods", False),
])
def test_same_dev(x, y, want):
    assert lift("same_dev")["same_dev"](x, y) is want


def block(v, s=0.02):
    return np.full(int(SR * s), v, np.int16)


def test_ring_cuts_only_the_lines_own_device():
    """AirPods 0-1 s, the MacBook mic 1-2 s, the AirPods again 2-3 s: an AirPods line over
    0.5-2.5 s gets only AirPods audio, sliced on each block's own stamp across the gap."""
    ns = lift("same_dev", "Ring")
    ring = ns["Ring"](300)
    for i in range(150):
        t_end = (i + 1) * 0.02
        ring.add(t_end, block(100 if t_end <= 1 or t_end > 2 else 200),
                 "airpods" if t_end <= 1 or t_end > 2 else "MacBook Pro Microphone")
    seg, cover = ring.cut(0.5, 2.5, "airpods")
    assert cover == pytest.approx(0.5, abs=0.02)
    assert len(seg) == pytest.approx(SR * 1.0, abs=SR * 0.04)
    assert set(np.round(seg * 32768).astype(int)) == {100}
    mac, cover = ring.cut(0.5, 2.5, "macbook pro microphone")
    assert cover == pytest.approx(0.5, abs=0.02) and set(np.round(mac * 32768).astype(int)) == {200}
    assert ring.span("airpods") == pytest.approx((0.0, 3.0))
    assert ring.span("MacBook Pro Microphone") == pytest.approx((1.0, 2.0))
    nothing, cover = ring.cut(0.5, 2.5, "BlackHole 16ch")
    assert cover == 0.0 and len(nothing) == 0
    assert ring.span("BlackHole 16ch") == (None, None)


def test_ring_drops_audio_older_than_keep():
    ns = lift("same_dev", "Ring")
    ring = ns["Ring"](1.0)
    for i in range(150):
        ring.add((i + 1) * 0.02, block(1), "airpods")
    r0, r1 = ring.span("airpods")
    assert r1 == pytest.approx(3.0) and r0 >= 2.0 - 0.02


@pytest.mark.parametrize("who,text,seg,want", [
    # the AirPods dying: zeros, whatever the diarizer said
    ("silent", "Okay.", np.zeros(1600, np.float32), ("unheard", "no signal")),
    ("theo", "Okay.", np.zeros(1600, np.float32), ("unheard", "no signal")),
    # words, no speech found: the diarizer missed him; never "silent"
    ("silent", "Um.", np.full(1600, 1e-4, np.float32), ("unheard", "words in the line")),
    # no words and no speech: silent stands
    ("silent", "", np.full(1600, 1e-4, np.float32), ("silent", None)),
    ("silent", " ... ", np.full(1600, 1e-4, np.float32), ("silent", None)),
    ("theo", "Hello there.", np.full(1600, 0.3, np.float32), ("theo", None)),
])
def test_unheard_why(who, text, seg, want):
    got = lift("words", "unheard_why")["unheard_why"](who, text, seg)
    assert got[0] == want[0]
    assert (got[1] is None) if want[1] is None else want[1] in got[1]


def test_relabel_never_calls_a_spoken_line_silent(tmp_path):
    """End to end on the stand-in diarizer: zeros and near-silence with words are unheard,
    with a why; near-silence with no words stays silent; a voice is named."""
    stub_diarizer(tmp_path)
    for n, f in [("theo", 600), ("alice", 400)]:
        _wav(tmp_path / f"{n}.wav", f, 3)
    rec = tmp_path / "rec"; rec.mkdir(); logs = tmp_path / "logs"; logs.mkdir()

    def raw(p, x):
        with wave.open(str(p), "wb") as h:
            h.setnchannels(1); h.setsampwidth(2); h.setframerate(SR)
            h.writeframes(x.astype(np.int16).tobytes())
    raw(rec / "1.wav", np.zeros(SR * 2))                    # the AirPods dying
    raw(rec / "2.wav", np.full(SR * 2, 2))                  # near silence, words
    raw(rec / "3.wav", np.full(SR * 2, 2))                  # near silence, no words
    _wav(rec / "4.wav", 600, 2)                             # theo
    for seq, text in [(1, "Okay."), (2, "Um."), (3, ""), (4, "Hello.")]:
        (rec / f"{seq}.json").write_text(json.dumps(
            {"seq": seq, "kind": "turn", "t0": 0, "t1": 2, "text": text, "ts": T0}))
    args = [sys.executable, str(SC), "--enroll-s", "3", "--relabel", str(rec), "--logdir", str(logs),
            "--enroll", f"theo={tmp_path / 'theo.wav'}", "--enroll", f"alice={tmp_path / 'alice.wav'}"]
    env = dict(os.environ, PYTHONPATH=str(tmp_path / "stub"), STUB_LAST="1")
    p = subprocess.run(args, capture_output=True, text=True, env=env, timeout=60)
    assert p.returncode == 0, p.stderr
    rows = {r["seq"]: r for r in map(json.loads, (logs / "speakers_2030-09-29.jsonl").open())}
    assert rows[1]["speaker"] == "unheard" and "no signal" in rows[1]["why"]
    assert rows[2]["speaker"] == "unheard" and "words" in rows[2]["why"]
    assert rows[3]["speaker"] == "silent" and "why" not in rows[3]
    assert rows[4]["speaker"] == "theo" and "why" not in rows[4]


def test_device_defaults_to_follow():
    src = SC.read_text()
    assert re.search(r'add_argument\("--device", default="follow"', src)
    assert 'dev=${SPEAKER_DEVICE:-follow}' in (ROOT / "speakers" / "run.sh").read_text()


def test_plist_renders_and_stays_down_on_stop(tmp_path):
    """Strict XML (plutil -lint passes a comment holding a double hyphen; minidom does not),
    every placeholder filled, and KeepAlive restarts a crash but not a STOP (exit 0)."""
    text = PLIST.read_text()
    for k, v in {"@HOME@": "/Users/x", "@EARS_REPO@": "/Users/x/ears",
                 "@SPEAKER_PY@": "/Users/x/venv/bin/python", "@AGENT_REFS@": "dave"}.items():
        text = text.replace(k, v)
    assert "@" not in re.sub(r"<!--.*?-->", "", text, flags=re.S)
    xml.dom.minidom.parseString(text.encode())
    pl = plistlib.loads(text.encode())
    assert pl["Label"] == "dev.sessionkit.voice.ears-speakers"
    assert pl["ProgramArguments"] == ["/bin/bash", "/Users/x/ears/speakers/run.sh"]
    assert pl["KeepAlive"] == {"SuccessfulExit": False} and pl["RunAtLoad"] is True
    env = pl["EnvironmentVariables"]
    assert env["SPEAKER_DEVICE"] == "follow" and env["HF_HUB_OFFLINE"] == "1"
    assert env["SPEAKER_PY"] == "/Users/x/venv/bin/python" and env["SPEAKER_AGENT_REFS"] == "dave"


class FakeStream:
    def __init__(self, dev, fail_stop=False):
        self.dev, self.active, self.fail_stop, self.closed = dev, False, fail_stop, False
    def start(self): self.active = True
    def stop(self):
        if self.fail_stop: raise RuntimeError("PortAudio -9986: the device left")
    def close(self): self.closed = True


class FakeSD:
    """PortAudio as sounddevice shows it: the device list is the one read at the last
    _initialize, so a device that arrives later is invisible until a rescan."""
    def __init__(self, names, fail_stop=False):
        self.live, self.names, self.fail_stop, self.opened, self.rescans = list(names), list(names), fail_stop, [], 0
    def _terminate(self): pass
    def _initialize(self): self.names = list(self.live); self.rescans += 1
    def query_devices(self, idx=None, kind=None):
        if idx is None:
            return [{"name": n, "max_input_channels": 1} for n in self.names]
        return {"default_samplerate": 48000.0}
    def InputStream(self, device, **kw):
        if self.names[device] not in self.live:
            raise RuntimeError("PortAudio -9986: stale device list")
        s = FakeStream(self.names[device], self.fail_stop); self.opened.append(s); return s


def mic_ns(target, said, deaf_lines=10):
    ns = lift("same_dev", "RESCAN_S", "REOPEN_S", "deaf", "deaf_lock", "note_cover", "deaf_due",
              "deaf_reset", "close_stream", "rescan", "try_open", "mic_step",
              a=type("A", (), {"deaf_lines": deaf_lines})())
    ns.update(SR=SR, time=__import__("time"), ring=None, target_device=lambda: target[0],
              say=said.append)
    return ns


def test_mic_step_follows_the_capture_and_survives_a_dying_device():
    """AirPods -> the MacBook mic -> the AirPods, with no restart: each move closes the old
    stream, and the real close_stream still closes it when stop() throws (a vanished
    device); a stream that dies under a steady target is reopened; no capture closes it."""
    said, target = [], ["airpods"]
    ns = mic_ns(target, said)
    sd = FakeSD(["Theo's AirPods Pro", "MacBook Pro Microphone"], fail_stop=True)
    st = {"stream": None, "dev": None}
    ns["mic_step"](sd, st, 0.0)
    assert st["dev"] == "airpods" and sd.opened[-1].dev == "Theo's AirPods Pro"
    target[0] = "MacBook Pro Microphone"
    ns["mic_step"](sd, st, 1.0)
    assert sd.opened[0].closed, "stop() raised and the stream leaked"
    assert st["dev"] == "MacBook Pro Microphone" and sd.opened[-1].dev == "MacBook Pro Microphone"
    target[0] = "airpods"
    ns["mic_step"](sd, st, 2.0)
    assert st["dev"] == "airpods" and len(sd.opened) == 3 and sd.opened[1].closed
    st["stream"].active = False                      # died under a steady target
    ns["mic_step"](sd, st, 3.0)
    assert st["stream"] is sd.opened[-1] and len(sd.opened) == 4 and st["stream"].active
    target[0] = None                                 # the keeper's capture stopped
    ns["mic_step"](sd, st, 4.0)
    assert st["stream"] is None and len(sd.opened) == 4
    assert sd.rescans == 0                           # every device was in the list


def test_mic_step_rescans_for_airpods_put_in_after_it_started():
    """Started at login with the AirPods out: PortAudio's list has no AirPods. They go in
    and the keeper moves to them: one rescan finds them, and they open (review finding 1)."""
    said, target = [], ["MacBook Pro Microphone"]
    ns = mic_ns(target, said)
    sd = FakeSD(["MacBook Pro Microphone"])
    st = {"stream": None, "dev": None}
    ns["mic_step"](sd, st, 0.0)
    assert st["dev"] == "MacBook Pro Microphone" and sd.rescans == 0
    sd.live.append("Theo's AirPods Pro")             # in, but not in PortAudio's list
    target[0] = "airpods"
    ns["mic_step"](sd, st, 10.0)
    assert sd.rescans == 1 and st["dev"] == "airpods" and sd.opened[-1].dev == "Theo's AirPods Pro"


def test_mic_step_rescans_a_stale_entry_and_rate_limits():
    """A reconnect leaves a stale entry that fails to open (-9986): rescan once, then open.
    A device that is truly gone rescans at most once per RESCAN_S, not every second."""
    said, target = [], ["airpods"]
    ns = mic_ns(target, said)
    sd = FakeSD(["Theo's AirPods Pro"])
    sd.live = []                                      # gone, but still listed
    st = {"stream": None, "dev": None}
    ns["mic_step"](sd, st, 100.0)
    assert st["stream"] is None and sd.rescans == 1
    for t in (101.0, 102.0, 103.0, 104.0):
        ns["mic_step"](sd, st, t)
    assert sd.rescans == 1
    sd.live = ["Theo's AirPods Pro"]                  # back
    ns["mic_step"](sd, st, 105.0)
    assert sd.rescans == 2 and st["dev"] == "airpods"


def test_mic_thread_survives_an_error_in_a_look():
    said, calls = [], []
    ns = lift("mic_thread")

    def step(sd, st, now):
        calls.append(now)
        if len(calls) == 1:
            st["stream"], st["dev"] = FakeStream("x"), "x"
            raise RuntimeError("boom")
        if len(calls) == 3:
            raise SystemExit   # end the test's loop

    closed = []
    ns.update(mic_step=step, say=said.append, close_stream=lambda st, why: closed.append(why) or st.update(stream=None),
              time=type("T", (), {"monotonic": staticmethod(lambda: 0.0), "sleep": staticmethod(lambda s: None)}))
    sys.modules.setdefault("sounddevice", type(sys)("sounddevice"))
    with pytest.raises(SystemExit):
        ns["mic_thread"]()
    assert len(calls) == 3 and closed == ["after an error"] and any("boom" in m for m in said)


# -- the heard-log follower: take() queues or answers, drain() labels or says why --

def follower(device="follow", cover=0.5):
    rows = []
    ns = lift("virtual", "same_dev", "Ring", "take", "drain")
    ns.update(a=type("A", (), {"device": device, "cover": cover})(), starts={}, backlog=collections.deque(),
              CALL=None,  # no --call-enroll: call lines get no row, as before
              note_start=lambda r: False, note_mouth=lambda r: False, time=__import__("time"),
              record=lambda r, seg, w0, w1, day, cover=None, why=None:
                  rows.append((r["seq"], None if seg is None else len(seg), cover, why)))
    ns["ring"] = ns["Ring"](300)
    return ns, rows


def line(seq, dev="airpods", t0=10.0, t1=12.0, session="ears-keeper", source="mic"):
    return {"seq": seq, "kind": "turn", "source": source, "device": dev, "session": session,
            "t0": t0, "t1": t1, "text": "hello", "ts": T0}


def test_a_line_with_no_stream_start_gets_its_row_at_once():
    ns, rows = follower()
    ns["take"](line(1), "2030-10-03")
    assert rows == [(1, None, None, "no stream start for its capture yet (no listen-started or heartbeat)")]
    assert not ns["backlog"]


def test_call_taps_and_pinned_other_devices_get_no_row():
    ns, rows = follower()
    ns["take"](line(1, dev="BlackHole 16ch"), "d")
    ns["take"](line(2, source="call"), "d")
    assert rows == [] and not ns["backlog"]
    ns, rows = follower(device="MacBook Pro Microphone")
    ns["take"](line(3, dev="airpods"), "d")
    assert rows == [] and not ns["backlog"]


def test_drain_waits_for_audio_then_labels_or_says_why():
    ns, rows = follower()
    ns["starts"][("ears-keeper", "airpods")] = 1000.0
    ns["take"](line(1, t0=10.0, t1=12.0), "d")          # window 1010..1012
    ns["drain"](1012.5, "d")
    assert rows == [] and len(ns["backlog"]) == 1        # no audio yet, 0.5 s after: wait
    for i in range(175):                                 # AirPods audio 1009..1012.5
        ns["ring"].add(1009.0 + (i + 1) * 0.02, block(100), "airpods")
    ns["drain"](1012.5, "d")
    assert rows == [(1, SR * 2, pytest.approx(1.0), None)]


def test_drain_under_cover_is_unheard_and_a_dead_device_stops_waiting():
    ns, rows = follower()
    ns["starts"][("ears-keeper", "airpods")] = 1000.0
    for i in range(50):                                  # opened late: 1011.5..1012.5
        ns["ring"].add(1011.5 + (i + 1) * 0.02, block(100), "airpods")
    ns["take"](line(1, t0=10.0, t1=12.0), "d")
    ns["drain"](1013.0, "d")
    assert rows == [(1, None, pytest.approx(0.25), None)]
    ns["take"](line(2, t0=20.0, t1=22.0), "d")          # its device never comes back
    ns["drain"](1023.0, "d")
    assert len(rows) == 1                                 # still within 5 s: wait
    ns["drain"](1027.5, "d")
    assert rows[1][:2] == (2, None) and rows[1][2] == 0.0


def test_a_line_from_the_future_is_answered_and_blocks_nobody():
    ns, rows = follower()
    ns["starts"][("ears-keeper", "airpods")] = 5000.0    # a wrong start
    ns["starts"][("ears-keeper", "MacBook Pro Microphone")] = 1000.0
    for i in range(175):
        ns["ring"].add(1009.0 + (i + 1) * 0.02, block(100), "MacBook Pro Microphone")
    ns["take"](line(1, t0=10.0, t1=12.0), "d")                                  # 5010: future
    ns["take"](line(2, dev="MacBook Pro Microphone", t0=10.0, t1=12.0), "d")    # 1010: fine
    ns["drain"](1013.0, "d")
    assert [r[0] for r in rows] == [1, 2]
    assert "in the future" in rows[0][3] and rows[1][1] == SR * 2


# -- EARS-342 (EARS-226 item 7): a capture stream that opens, stays active, never calls back --

def sidecar(argv, **given):
    """The side-car's top level with its I/O left out: every import, def, class and plain
    assignment in file order, plus the argparse lines, parsed with ARGV. Left out: the model
    and relabel blocks, the mic thread's start, and open_today() (it waits for a heard log).
    Unlike lift() it names nothing, so state a later change adds at top level comes too."""
    ns = {"__name__": "sidecar", "__file__": str(SC), **given}
    for node in ast.parse(SC.read_text()).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "a" for t in node.targets):
            ns["a"] = ns["ap"].parse_args(argv); continue
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Tuple) \
                and "f" in [getattr(e, "id", "") for e in node.targets[0].elts]:
            continue                                     # day, f = open_today()
        if isinstance(node, ast.Expr):
            fn = getattr(node.value, "func", None)
            if not (isinstance(fn, ast.Attribute) and getattr(fn.value, "id", "") == "ap"):
                continue                                 # the docstring, the thread's start
        elif not isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef,
                                   ast.Assign, ast.AnnAssign)):
            continue                                     # if (model, relabel), while (follow)
        exec(compile(ast.Module([node], []), "sidecar", "exec"), ns)
    return ns


class Clock:
    """time as the side-car reads it, moved by the test: no real sleeps."""
    def __init__(self, t): self.t = t
    def time(self): return self.t
    def monotonic(self): return self.t
    def perf_counter(self): return self.t
    def sleep(self, s): self.t += s
    def strftime(self, f): return "00:00:00"


class StaleSD(FakeSD):
    """EARS-226 item 7: PortAudio's cached entry for the device opens and reports active,
    and never calls back. A rescan (the kickstart, in the incident) gives a live entry whose
    stream does call back. Each stream keeps its callback; the test plays 1 s per tick."""
    def InputStream(self, device, callback=None, **kw):
        s = super().InputStream(device, **kw)
        s.callback, s.deaf = callback, self.rescans == 0
        return s

    def play(self, seconds):
        for s in self.opened:
            if s.active and not s.closed and not s.deaf:
                s.callback(np.full((int(SR * seconds), 1), 300, np.int16), int(SR * seconds), None, None)


class DeafSD(StaleSD):
    """A device that stays deaf: every stream, before a rescan or after, never calls back
    (the reopen does not help; the keeper's count of 20 is the backstop, EARS-341)."""
    def InputStream(self, device, callback=None, **kw):
        s = super().InputStream(device, callback=callback, **kw)
        s.deaf = True
        return s


def run_deaf(tmp_path, ticks=60, sd=None, argv=()):
    """The side-car's two loops, interleaved on one fake clock: each tick is one mic_step
    (the mic thread's 1 s look), 1 s of audio from every stream that calls back, and the
    follow loop, which takes a 2 s keeper line every other tick and drains. Returns the
    rows written, what it said (tick, line), and the fake PortAudio."""
    said, clock = [], Clock(1_000_000.0)
    ns = sidecar(["--logdir", str(tmp_path), *argv])
    start = clock.t
    ns.update(say=lambda m: said.append((round(clock.t - start), m)), time=clock,
              target_device=lambda: "airpods",
              label=lambda seg, refset=None: ("theo", {"theo": 1.0}, 100, 0.0))  # no diarizer
    sd = sd or StaleSD(["Theo's AirPods Pro"])
    st, day = {"stream": None, "dev": None}, "2030-10-17"
    ns["starts"][("ears-keeper", "airpods")] = start
    out = tmp_path / f"speakers_{day}.jsonl"
    for tick in range(1, ticks + 1):
        clock.t = start + tick
        ns["mic_step"](sd, st, clock.t)
        sd.play(1.0)
        if tick % 2 == 0:
            r = line(tick // 2, dev="airpods", t0=tick - 2.0, t1=float(tick))
            ns["take"](r, day)
        ns["drain"](clock.t, day)
    rows = [json.loads(x) for x in out.read_text().splitlines()] if out.exists() else []
    return rows, said, sd


DEAF_WHY = "no audio from 'airpods' (0% of the line covered)"


def test_a_stream_that_never_calls_back_is_reopened_after_deaf_lines(tmp_path):
    """The incident (EARS-226 item 7, 27k rows in 3h19m): the capture's stream opens on a
    stale PortAudio entry, stays active and never calls back, so every line is cut from an
    empty ring and gets 0%. mic_step reopens only on a new device name or an inactive
    stream, and rescans only when an open FAILS, so at HEAD it never reacts. After
    --deaf-lines (10) such rows it must close, rescan and reopen once, and hear again."""
    rows, said, sd = run_deaf(tmp_path)
    assert len(rows) >= 20, f"too few rows to judge: {len(rows)}"
    # the symptom, at HEAD and after the fix alike: the first ten lines are 0%
    assert [r.get("why") for r in rows[:10]] == [DEAF_WHY] * 10
    deaf = sum(r.get("why") == DEAF_WHY for r in rows)
    state = (f"{deaf}/{len(rows)} rows at 0%, {len(sd.opened)} stream(s) opened, "
             f"{sd.rescans} rescan(s), closed: {[s.closed for s in sd.opened]}")
    # the remedy: one close + rescan + reopen, on the same device name, the stream 'active'
    assert sd.rescans == 1 and len(sd.opened) == 2 and sd.opened[0].closed, state
    assert [m for _, m in said if "0% coverage" in m and "reopening" in m] == \
        ["mic: 10 lines at 0% coverage: reopening 'airpods'"], state
    # and the side-car hears again: once the lines in flight at the reopen are answered,
    # every line is named, none at 0%
    assert all(r["speaker"] == "theo" and "why" not in r for r in rows[-10:]), state
    assert deaf <= 12, state


def test_rows_carry_a_numeric_cover(tmp_path):
    """Every row with a window carries cover, a number rounded to 2 places, not a bool (the
    keeper's contract, EARS-341: cover <= 0 is 0%); the why text is unchanged beside it."""
    rows, _, _ = run_deaf(tmp_path)
    assert all(type(r["cover"]) is float and 0.0 <= r["cover"] <= 1.0 for r in rows)
    assert all(r["cover"] == 0.0 for r in rows if r.get("why") == DEAF_WHY)
    assert [r["cover"] for r in rows[-10:]] == [1.0] * 10
    assert all(r["v"] == 2 for r in rows)


def test_a_row_with_no_window_has_no_cover(tmp_path):
    """No stream start, a call-leg row with no window, a relabelled line: cover is unknown,
    so the field is absent, and none of them counts toward a reopen."""
    ns = sidecar(["--logdir", str(tmp_path)])
    ns.update(say=lambda m: None, label=lambda seg, refset=None: ("theo", {"theo": 1.0}, 100, 0.0),
              mouth_name=lambda who, fr, *x: (who, fr, None))
    r = line(1)
    ns["take"](r, "2030-10-17")                                   # no stream start
    ns["record"](line(2), None, 0.0, 0.0, "2030-10-17", why="a call line with no t0/t1", leg="call")
    ns["record"](line(3), np.full(SR, 0.3, np.float32), 0.0, 1.0, "2030-10-17")   # relabel
    rows = [json.loads(x) for x in (tmp_path / "speakers_2030-10-17.jsonl").read_text().splitlines()]
    assert [r["seq"] for r in rows] == [1, 2, 3] and not any("cover" in r for r in rows)
    assert ns["deaf"]["n"] == 0


def count_ns(deaf_lines=10):
    ns = lift("same_dev", "deaf", "deaf_lock", "note_cover", "deaf_due", "deaf_reset",
              a=type("A", (), {"deaf_lines": deaf_lines})())
    return ns


def test_a_covered_line_or_a_new_device_resets_the_count():
    """Nine at 0%, then one covered: no reopen. A share that rounds to 0 counts as 0 (the
    row's field says 0.0); one that rounds up to 0.01 does not. Nine at 0% on the AirPods,
    then one on the MacBook mic: a new count."""
    ns = count_ns()
    note, due = ns["note_cover"], ns["deaf_due"]
    for _ in range(9): note("airpods", 0.0)
    note("airpods", 0.25)
    for _ in range(9): note("airpods", 0.004)                     # rounds to 0.0: counts
    assert ns["deaf"]["n"] == 9 and due("airpods") == 0
    note("airpods", 0.006)                                        # rounds to 0.01: covered
    assert ns["deaf"]["n"] == 0
    for _ in range(9): note("airpods", 0.0)
    note("MacBook Pro Microphone", 0.0)
    assert ns["deaf"]["n"] == 1 and due("MacBook Pro Microphone") == 0
    for _ in range(9): note("MacBook Pro Microphone", 0.0)
    assert due("airpods") == 0                                    # not the stream that is deaf
    assert due("MacBook Pro Microphone") == 10 and ns["deaf"]["n"] == 0
    assert due("MacBook Pro Microphone") == 0                     # asked once, cleared
    ns = count_ns(deaf_lines=0)                                   # 0 turns it off
    for _ in range(100): ns["note_cover"]("airpods", 0.0)
    assert ns["deaf_due"]("airpods") == 0


def test_a_forced_reopen_is_rate_limited_to_once_per_60_s():
    """mic_step with a reopen due: close + rescan + reopen on the same name, the stream still
    'active'. Due again 30 s later: nothing until 60 s after the first, then once more."""
    said, target = [], ["airpods"]
    ns = mic_ns(target, said)
    sd = FakeSD(["Theo's AirPods Pro"])
    st = {"stream": None, "dev": None}
    ns["mic_step"](sd, st, 1000.0)
    assert len(sd.opened) == 1 and sd.rescans == 0

    def deafen():
        for _ in range(10): ns["note_cover"]("airpods", 0.0)
    deafen()
    ns["mic_step"](sd, st, 1001.0)
    assert len(sd.opened) == 2 and sd.opened[0].closed and sd.rescans == 1
    assert st["stream"] is sd.opened[1] and st["dev"] == "airpods"
    assert said.count("mic: 10 lines at 0% coverage: reopening 'airpods'") == 1
    deafen()
    for t in (1031.0, 1060.0):                                    # inside the 60 s
        ns["mic_step"](sd, st, t)
    assert len(sd.opened) == 2 and sd.rescans == 1
    for _ in range(3): ns["note_cover"]("airpods", 0.0)           # still due, still counting
    ns["mic_step"](sd, st, 1061.0)
    assert len(sd.opened) == 3 and sd.rescans == 2 and sd.opened[1].closed
    assert said[-4:] == ["mic: 13 lines at 0% coverage: reopening 'airpods'",
                         "mic: closed 'airpods' - 13 lines at 0% coverage",
                         "mic: rescanning the device list", "mic: open 'airpods' at 16000 Hz"]
    ns["mic_step"](sd, st, 1062.0)                                # nothing due: nothing done
    assert len(sd.opened) == 3 and sd.rescans == 2


def test_a_device_that_stays_deaf_is_reopened_once_a_minute_not_every_ten_lines(tmp_path):
    """The reopen does not help (every stream stays deaf): over 200 s the side-car reopens
    at the 10th 0% line, then once per 60 s, never more often; every row says 0%."""
    rows, said, sd = run_deaf(tmp_path, ticks=200, sd=DeafSD(["Theo's AirPods Pro"]))
    when = [t for t, m in said if "lines at 0% coverage: reopening" in m]
    assert when == [26, 86, 146], when   # line 10 is answered at 25 (ends at 20, +5 s)
    assert all(b - a >= 60 for a, b in zip(when, when[1:])), when
    assert sd.rescans == 3 and len(sd.opened) == 4 and all(s.closed for s in sd.opened[:3])
    assert all(r["cover"] == 0.0 and r.get("why") == DEAF_WHY for r in rows)


def test_deaf_lines_is_a_flag():
    ns = sidecar(["--deaf-lines", "4"])
    assert ns["a"].deaf_lines == 4 and sidecar([])["a"].deaf_lines == 10
