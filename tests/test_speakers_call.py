"""speakers/speaker-sidecar2.py, the call leg: a call line (source "call", device call:N) is cut
from the bot's caller-call.wav (mike-call.wav in a call dir from before PHONE-127) at
wav_s + t0 .. wav_s + t1, wav_s from ear-taps.jsonl, and named
against the CALL references (barge in only on a voice identified as the
user's). No model: functions are lifted by name, as in test_speakers_follow."""
import collections, datetime as dt, importlib, json, os, re, subprocess, sys, time, wave
from pathlib import Path

import numpy as np
import pytest

from test_speakers import T0, _wav, stub_diarizer
from test_speakers_follow import SC, SR, lift
from test_speakers_parity import SD_STUB

T0_EPOCH = dt.datetime.fromisoformat(T0).timestamp()


def growing_wav(path, samples):
    """A wav as the bot leaves it mid-call: the header still says 0 frames."""
    with open(path, "wb") as f:
        f.write(b"RIFF" + (0).to_bytes(4, "little") + b"WAVEfmt " + (16).to_bytes(4, "little")
                + (1).to_bytes(2, "little") + (1).to_bytes(2, "little") + SR.to_bytes(4, "little")
                + (SR * 2).to_bytes(4, "little") + (2).to_bytes(2, "little") + (16).to_bytes(2, "little")
                + b"data" + (0).to_bytes(4, "little"))
        f.write(np.asarray(samples, np.int16).tobytes())


def more(path, samples):
    with open(path, "ab") as f:
        f.write(np.asarray(samples, np.int16).tobytes())


def ramp(s0, s1):
    """Samples whose value is their own index mod 30000: a cut says where it came from."""
    return (np.arange(int(s0 * SR), int(s1 * SR)) % 30000).astype(np.int16)


def test_tap_offset_prefers_the_tap_nearest_listen_started():
    ns = lift("tap_offset")
    rows = [{"t": 100.0, "call": 5, "wav_s": 2.0}, {"t": 100.5, "call": 5, "end": "x", "dropped": 0},
            {"t": 300.0, "call": 5, "wav_s": 40.25}]
    assert ns["tap_offset"](rows, 301.0, 400.0) == 40.25      # the capture that started at 301
    assert ns["tap_offset"](rows, 99.0, 400.0) == 2.0
    assert ns["tap_offset"](rows, None, 200.0) == 2.0         # no listen-started: newest before
    assert ns["tap_offset"](rows, None, 350.0) == 40.25
    assert ns["tap_offset"](rows, None, 50.0) is None
    assert ns["tap_offset"]([], 300.0, 400.0) is None


def test_call_dir_is_the_newest_dir_for_that_call(tmp_path):
    ns = lift("call_dir"); ns["Path"] = Path
    for d in ("20300107-080000-chat12-msg54541", "20300107-090000-chat12-msg54541",
              "20300107-090000-chat12-msg545410"):
        (tmp_path / "a" / d).mkdir(parents=True)
    got = ns["call_dir"]("54541", [str(tmp_path / "a"), str(tmp_path / "nowhere")])
    assert got.name == "20300107-090000-chat12-msg54541"
    assert ns["call_dir"]("1", [str(tmp_path / "a")]) is None


def test_cut_wav_reads_a_wav_still_being_written(tmp_path):
    ns = lift("wav_data_at", "cut_wav")
    w = tmp_path / "caller-call.wav"
    growing_wav(w, ramp(0, 3))
    seg, cover = ns["cut_wav"](w, 1.0, 2.0)
    assert cover == 1.0 and len(seg) == SR and int(round(seg[0] * 32768)) == SR % 30000
    seg, cover = ns["cut_wav"](w, 2.5, 3.5)                    # half of it is on disk
    assert cover == pytest.approx(0.5) and len(seg) == SR // 2


def leg(tmp_path, cover=0.5, taps=({"t": T0_EPOCH - 60, "call": 54541, "wav_s": 1.5},),
        name="20300107-080000-chat12-msg54541"):
    rows = []
    calls = tmp_path / "calls"; d = calls / name; d.mkdir(parents=True)
    if taps is not None:
        (d / "ear-taps.jsonl").write_text("".join(json.dumps(r) + "\n" for r in taps))
    ns = lift("_load_callrec", "callrec", "call_dir", "tap_offset", "read_taps", "wav_data_at", "cut_wav",
              "take_call", "drain_call", "CALL_ID", __file__=str(SC), importlib=importlib, Path=Path,
              CALL={"names": ["theo"]}, sys=sys)
    ns.update(a=type("A", (), {"cover": cover, "call_dirs": f"{tmp_path / 'none'}:{calls}"})(),
              listen_t={}, call_backlog=collections.deque(), time=__import__("time"), json=json,
              epoch=lambda ts: dt.datetime.fromisoformat(ts).timestamp(),
              record=lambda r, seg, w0, w1, day, cover=None, why=None, leg=None:
                  rows.append((r["seq"], None if seg is None else seg, cover, why, leg)))
    return ns, rows, d


def call_line(seq, t0=10.0, t1=12.0, dev="call:54541"):
    return {"seq": seq, "kind": "partial", "source": "call", "device": dev, "session": "call-ears-54541",
            "t0": t0, "t1": t1, "text": "hey court", "ts": T0}


def test_a_call_line_waits_for_its_audio_then_is_cut_at_wav_s_plus_t0(tmp_path):
    ns, rows, d = leg(tmp_path)
    growing_wav(d / "caller-call.wav", ramp(0, 12))            # wav_s 1.5 + 10..12 = 11.5..13.5
    ns["take_call"](call_line(1), "d")
    queued = ns["call_backlog"][0][4]
    ns["drain_call"](queued + 1, "d")
    assert rows == [] and len(ns["call_backlog"]) == 1         # 12 s on disk, needs 13.5: wait
    more(d / "caller-call.wav", ramp(12, 14))
    ns["drain_call"](queued + 1.2, "d")
    (seq, seg, cover, why, legname), = rows
    assert (seq, cover, why, legname) == (1, 1.0, None, "call") and len(seg) == 2 * SR
    assert int(round(seg[0] * 32768)) == int(11.5 * SR) % 30000


def test_a_call_line_whose_audio_never_comes_is_answered_after_5_s(tmp_path):
    ns, rows, d = leg(tmp_path)
    growing_wav(d / "caller-call.wav", ramp(0, 12.0))         # 11.5..12: a quarter
    ns["take_call"](call_line(1), "d")
    queued = ns["call_backlog"][0][4]
    ns["drain_call"](queued + 5.1, "d")
    assert rows[0][0] == 1 and rows[0][1] is None and rows[0][2] == pytest.approx(0.25)


def test_listen_started_picks_the_capture_s_own_tap(tmp_path):
    taps = ({"t": T0_EPOCH - 600, "call": 54541, "wav_s": 0.0}, {"t": T0_EPOCH - 60, "call": 54541, "wav_s": 5.0})
    ns, rows, d = leg(tmp_path, taps=taps)
    ns["listen_t"][("call-ears-54541", "call:54541")] = T0_EPOCH - 599.5
    growing_wav(d / "caller-call.wav", ramp(0, 30))
    ns["take_call"](call_line(1), "d")
    assert ns["call_backlog"][0][2:4] == (10.0, 12.0)          # wav_s 0, not the newer tap's 5


@pytest.mark.parametrize("have,want", [
    (["caller-call.wav"], "caller-call.wav"),                  # a call since PHONE-127
    (["mike-call.wav"], "mike-call.wav"),                      # an older call dir keeps its name
    (["mike-call.wav", "caller-call.wav"], "caller-call.wav"), # both: the new name wins
])
def test_a_call_line_is_cut_from_caller_call_wav_else_mike_call_wav(tmp_path, have, want):
    """EARS-298: PHONE-127 renamed the bot's recording mike-call.wav -> caller-call.wav."""
    ns, rows, d = leg(tmp_path)
    for n in have:
        growing_wav(d / n, ramp(0, 30))
    ns["take_call"](call_line(1), "d")
    assert rows == [], rows
    (r, wav, s0, s1, _), = ns["call_backlog"]
    assert wav == d / want and (s0, s1) == (11.5, 13.5)


def test_the_side_car_loads_the_one_name_helper_from_the_repo_not_from_ears():
    """Its venv has no ears: callrec.py is loaded by path from src/ears, the file ears eval imports."""
    ns = lift("_load_callrec", "callrec", __file__=str(SC), importlib=importlib, Path=Path,
              CALL={"names": ["theo"]}, sys=sys)
    from ears import callrec
    assert Path(ns["callrec"].__file__).resolve() == Path(callrec.__file__).resolve()
    assert ns["callrec"].__name__ != "ears.callrec"            # its own module: no ears package run
    assert not re.search(r"^\s*(from|import)\s+ears\b", SC.read_text(), re.M)


def test_a_copy_of_the_side_car_outside_the_repo_runs_without_the_helper_until_it_has_a_call_leg(tmp_path):
    """A side-car copied out of the repo (~/.<agent>/speaker-sidecar) has no ../src/ears/callrec.py.
    Room only (no --call-enroll): it never looks for it. With a call leg: it refuses at start,
    naming the file, instead of dying on the first call line."""
    copy = tmp_path / "speaker-sidecar" / "speaker-sidecar2.py"
    room = lift("_load_callrec", "callrec", __file__=str(copy), importlib=importlib, Path=Path, CALL=None, sys=sys)
    assert room["callrec"] is None
    with pytest.raises(SystemExit, match=r"callrec\.py"):
        lift("_load_callrec", "callrec", __file__=str(copy), importlib=importlib, Path=Path,
             CALL={"names": ["theo"]}, sys=sys)


@pytest.mark.parametrize("make,why", [
    (lambda d: None, "no caller-call.wav or mike-call.wav for call 54541 in "),   # both names, and where
    (lambda d: growing_wav(d / "caller-call.wav", ramp(0, 1)), "no ear-taps.jsonl row"),
])
def test_a_call_line_that_cannot_be_placed_gets_its_row_now(tmp_path, make, why):
    ns, rows, d = leg(tmp_path, taps=None)
    make(d)
    ns["take_call"](call_line(1), "d")
    assert rows[0][0] == 1 and rows[0][1] is None and why in rows[0][3] and rows[0][4] == "call"
    assert not ns["call_backlog"]


def test_take_hands_call_lines_to_the_call_leg_only_with_call_refs():
    taken = []
    ns = lift("virtual", "same_dev", "take")
    ns.update(a=type("A", (), {"device": "follow"})(), starts={}, backlog=collections.deque(),
              note_start=lambda r: False, note_mouth=lambda r: False,
              take_call=lambda r, day: taken.append(r["seq"]), record=lambda *x, **k: None)
    ns["CALL"] = None
    ns["take"](call_line(1), "d")
    ns["CALL"] = {"names": ["theo"]}
    ns["take"](call_line(2), "d")
    assert taken == [2]


def test_room_and_call_references_are_enrolled_apart(tmp_path):
    """--call-enroll is its own prefix: a room line is never named by a call clip."""
    stub_diarizer(tmp_path)
    for n, f in (("theo", 600), ("alice", 400), ("theocall", 1000)):
        _wav(tmp_path / f"{n}.wav", f, 3)
    rec = tmp_path / "rec"; rec.mkdir(); logs = tmp_path / "logs"; logs.mkdir()
    _wav(rec / "1.wav", 600, 2)
    (rec / "1.json").write_text(json.dumps({"seq": 1, "kind": "turn", "t0": 0, "t1": 2, "text": "x", "ts": T0}))
    args = [sys.executable, str(SC), "--enroll-s", "3", "--relabel", str(rec), "--logdir", str(logs),
            "--enroll", f"theo={tmp_path / 'theo.wav'}", "--enroll", f"alice={tmp_path / 'alice.wav'}",
            "--call-enroll", f"theo={tmp_path / 'theocall.wav'}"]
    env = dict(os.environ, PYTHONPATH=str(tmp_path / "stub"), STUB_LAST="4")
    p = subprocess.run(args, capture_output=True, text=True, env=env, timeout=60)
    assert p.returncode == 0, p.stderr
    assert "room reference theo" in p.stdout and "call reference theo" in p.stdout
    row, = map(json.loads, (logs / "speakers_2030-09-29.jsonl").open())
    assert row["speaker"] == "theo" and row["refs"] == ["theo", "alice"] and "leg" not in row


def test_call_dirs_default_names_no_agent_and_finds_every_bot(tmp_path, monkeypatch):
    """EARS-202 fix-003: the default was two of our agents' homes; it is now every
    ~/.<agent>/delta-chat/calls, as the ears and the mouth find a bot's sockets."""
    monkeypatch.delenv("SESSIONPHONE_ROOT", raising=False)
    ns = lift("call_dir", "CALL_DIRS", os=os); ns["Path"] = Path
    assert ".ivy" not in ns["CALL_DIRS"] and ".vera" not in ns["CALL_DIRS"]
    home = tmp_path / "home"; monkeypatch.setenv("HOME", str(home))
    old = home / ".alice" / "delta-chat" / "calls" / "20300107-080000-chat12-msg54541"
    new = home / ".bob" / "delta-chat" / "calls" / "20300107-090000-chat3-msg54541"
    for d in (old, new): d.mkdir(parents=True)
    assert ns["call_dir"]("54541", ns["CALL_DIRS"].split(":")) == new   # the newest, any agent
    assert ns["call_dir"]("7", ns["CALL_DIRS"].split(":")) is None


def test_call_dir_skips_an_empty_root_never_the_cwd(tmp_path, monkeypatch):
    ns = lift("call_dir"); ns["Path"] = Path
    (tmp_path / "20300107-080000-chat12-msg9").mkdir()
    monkeypatch.chdir(tmp_path)
    assert ns["call_dir"]("9", ["", str(tmp_path / "nowhere")]) is None


# ---- EARS-316: a sessionphone web call. Its dir is <root>/web/calls/<N>/ (N the 10-digit epoch
# second the call began), holding the same caller-call.wav and ear-taps.jsonl a Delta dir does.
# On one host's call:1921357125 every line got "unheard": call_dir() looked only for *-msg<N> under
# ~/.*/delta-chat/calls (findings/rca-001.md). ----

WEB = 1921357125


def test_call_dir_finds_a_web_call_s_dir_named_by_its_id(tmp_path):
    ns = lift("call_dir"); ns["Path"] = Path
    delta, web = tmp_path / "delta" / "calls", tmp_path / "sp" / "web" / "calls"
    for d in (delta / "20300107-080000-chat12-msg54541", delta / "20300107-090000-chat12-msg54541",
              web / str(WEB), web / f"{WEB}0", web / "54541x"):
        d.mkdir(parents=True)
    (web / "1921357799").write_text("")                        # a file named like a call: not a dir
    roots = [str(delta), str(web)]
    assert ns["call_dir"](str(WEB), roots) == web / str(WEB)
    assert ns["call_dir"]("54541", roots).name == "20300107-090000-chat12-msg54541"   # still the newest
    assert ns["call_dir"]("1921357799", roots) is None
    assert ns["call_dir"]("170000032", roots) is None          # a prefix of an id is not the id


def test_call_dirs_default_covers_sessionphone_s_web_calls(tmp_path, monkeypatch):
    """The default reaches sessionphone's web calls as the ears reach its sockets (bot_roots):
    $SESSIONPHONE_ROOT, else ~/.sessionphone. Beside every agent's Delta bot, not instead."""
    home = tmp_path / "home"; monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("SESSIONPHONE_ROOT", raising=False)
    ns = lift("call_dir", "CALL_DIRS", os=os); ns["Path"] = Path
    web = home / ".sessionphone" / "web" / "calls" / str(WEB)
    delta = home / ".alice" / "delta-chat" / "calls" / "20300107-080000-chat12-msg54541"
    for d in (web, delta): d.mkdir(parents=True)
    roots = ns["CALL_DIRS"].split(":")
    assert ns["call_dir"](str(WEB), roots) == web and ns["call_dir"]("54541", roots) == delta

    sp = tmp_path / "elsewhere"; (sp / "web" / "calls" / str(WEB)).mkdir(parents=True)
    monkeypatch.setenv("SESSIONPHONE_ROOT", str(sp))
    ns = lift("call_dir", "CALL_DIRS", os=os); ns["Path"] = Path
    roots = ns["CALL_DIRS"].split(":")
    assert ns["call_dir"](str(WEB), roots) == sp / "web" / "calls" / str(WEB)
    assert ns["call_dir"]("54541", roots) == delta              # the Delta bots are still searched


def test_a_web_call_line_is_cut_from_its_caller_call_wav_at_wav_s_plus_t0(tmp_path):
    """A web call tapped at wav_s 0.659 (a recorded run's output): the cut starts there, as a
    Delta call's does, and the row carries leg: call."""
    ns, rows, d = leg(tmp_path, taps=({"t": T0_EPOCH - 60, "call": WEB, "wav_s": 0.659},), name=str(WEB))
    growing_wav(d / "caller-call.wav", ramp(0, 14))
    ns["take_call"](call_line(1, dev=f"call:{WEB}"), "d")
    (r, wav, s0, s1, queued), = ns["call_backlog"]
    assert wav == d / "caller-call.wav" and (s0, s1) == pytest.approx((10.659, 12.659))
    ns["drain_call"](queued + 0.1, "d")
    (seq, seg, cover, why, legname), = rows
    assert (seq, cover, why, legname) == (1, 1.0, None, "call") and len(seg) == 2 * SR
    assert int(round(seg[0] * 32768)) == int(round(10.659 * SR)) % 30000


def test_a_web_call_with_taps_and_no_wav_still_gets_its_unheard_row(tmp_path):
    """Until sessionphone writes the web call's recording, its lines keep today's row and why:
    found, not silently skipped."""
    ns, rows, d = leg(tmp_path, taps=({"t": T0_EPOCH - 60, "call": WEB, "wav_s": 0.659},), name=str(WEB))
    ns["take_call"](call_line(1, dev=f"call:{WEB}"), "d")
    assert rows == [(1, None, None, f"no caller-call.wav or mike-call.wav for call {WEB} in {d}", "call")]
    assert not ns["call_backlog"]


def test_a_call_line_with_no_call_dir_names_every_root_it_searched(tmp_path, monkeypatch):
    """The super (EARS-316): a no-wav why NAMES WHERE IT LOOKED. With no dir for the
    call at all, that is both forms it looked for and every root, ~ expanded; an empty root is
    not named."""
    home = tmp_path / "home"; monkeypatch.setenv("HOME", str(home))
    ns, rows, _ = leg(tmp_path, taps=None)
    ns["a"].call_dirs = f"~/.*/delta-chat/calls::{tmp_path / 'calls'}"
    ns["take_call"](call_line(1, dev=f"call:{WEB}"), "d")
    (seq, seg, _, why, legname), = rows
    assert (seq, seg, legname) == (1, None, "call")
    assert why == (f"no caller-call.wav or mike-call.wav for call {WEB} (no dir *-msg{WEB} or {WEB}/ "
                   f"under {home}/.*/delta-chat/calls:{tmp_path / 'calls'})")
    assert not ns["call_backlog"]


def _tone(f, s0, s1):
    t = np.arange(int(round(s0 * SR)), int(round(s1 * SR))) / SR
    return (0.5 * np.sin(2 * np.pi * f * t) * 32767).astype(np.int16)


def test_end_to_end_a_web_call_and_a_delta_call_both_label_theo(tmp_path):
    """The success criterion, on the REAL side-car following a heard log (stand-in diarizer,
    no mic, no model, no port). Default --call-dirs under a scratch HOME: a Delta call and a
    web call in the same run each give a theo row with leg: call, and a web call with no wav
    gives its unheard row. Each recording is 600 Hz (theo) only inside wav_s+t0..wav_s+t1 and
    800 Hz (no reference) elsewhere, so a cut that dropped the web tap's 0.659 s would be a
    third 800 Hz: 'mixed', not theo."""
    stub = stub_diarizer(tmp_path); (stub / "sounddevice.py").write_text(SD_STUB)
    _wav(tmp_path / "room-theo.wav", 600, 3); _wav(tmp_path / "call-theo.wav", 600, 3)
    now = time.time()
    iso = lambda off=0.0: dt.datetime.fromtimestamp(now + off).astimezone().isoformat(timespec="milliseconds")
    delta = tmp_path / ".alice" / "delta-chat" / "calls" / "20300107-080000-chat12-msg74702"
    calls = tmp_path / ".sessionphone" / "web" / "calls"
    for d, n, wav_s in ((delta, 74702, 1.5), (calls / str(WEB), WEB, 0.659), (calls / str(WEB + 1), WEB + 1, 0.659)):
        d.mkdir(parents=True)
        (d / "ear-taps.jsonl").write_text(json.dumps({"t": round(now - 60, 3), "call": n, "wav_s": wav_s}) + "\n")
        if n != WEB + 1:                                       # WEB + 1: a web call with no recording
            growing_wav(d / "caller-call.wav", np.concatenate(
                [_tone(800, 0, wav_s + 10), _tone(600, wav_s + 10, wav_s + 12), _tone(800, wav_s + 12, 30)]))
    logs = tmp_path / "logs"; logs.mkdir()
    day = dt.date.today().isoformat(); heard = logs / f"heard_{day}.jsonl"; heard.write_text("")
    out = tmp_path / "out.log"
    env = {k: v for k, v in os.environ.items() if k != "SESSIONPHONE_ROOT"}
    env.update(PYTHONPATH=str(stub), STUB_LAST="4", HOME=str(tmp_path))
    args = [sys.executable, str(SC), "--enroll-s", "3", "--logdir", str(logs), "--stop-file", str(tmp_path / "STOP"),
            "--enroll", f"theo={tmp_path / 'room-theo.wav'}", "--call-enroll", f"theo={tmp_path / 'call-theo.wav'}"]
    p = subprocess.Popen(args, stdout=out.open("w"), stderr=subprocess.STDOUT, env=env, cwd=tmp_path)
    try:
        deadline = time.time() + 60
        while not re.search(r"following \S*heard_", out.read_text()):
            assert p.poll() is None, out.read_text()
            assert time.time() < deadline, "the side-car never reached its loop"
            time.sleep(0.1)
        lines = []
        for seq, n in ((1, 74702), (2, WEB), (3, WEB + 1)):
            lines += [{"event": "listen-started", "session": f"call-ears-{n}", "device": f"call:{n}", "ts": iso(-55)},
                      {"seq": seq, "kind": "turn", "source": "call", "device": f"call:{n}", "session": f"call-ears-{n}",
                       "t0": 10.0, "t1": 12.0, "text": "hey can you hear me", "ts": iso()}]
        with heard.open("a") as f:
            f.write("".join(json.dumps(r) + "\n" for r in lines))
        sp = logs / f"speakers_{day}.jsonl"
        deadline = time.time() + 30
        while not (sp.exists() and len(sp.read_text().splitlines()) >= 3):
            assert p.poll() is None, out.read_text()
            assert time.time() < deadline, out.read_text()
            time.sleep(0.1)
    finally:
        (tmp_path / "STOP").touch()
        with heard.open("a") as f: f.write(json.dumps({"event": "noop", "ts": iso()}) + "\n")
        try: p.wait(timeout=10)
        except subprocess.TimeoutExpired: p.kill(); p.wait()
    rows = {r["seq"]: r for r in map(json.loads, sp.read_text().splitlines())}
    assert rows[1]["speaker"] == "theo" and rows[1]["leg"] == "call", rows[1]          # Delta, still
    assert rows[2]["speaker"] == "theo" and rows[2]["leg"] == "call", rows[2]          # the web call
    assert rows[2]["device"] == f"call:{WEB}" and rows[2]["refs"] == ["theo"] and "why" not in rows[2]
    assert rows[3]["speaker"] == "unheard" and rows[3]["leg"] == "call"
    assert rows[3]["why"] == f"no caller-call.wav or mike-call.wav for call {WEB + 1} in {calls / str(WEB + 1)}"
