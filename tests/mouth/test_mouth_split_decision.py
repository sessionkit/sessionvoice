"""The caller's decision in ``play()``: does this play start a split of its file?
(design.md sections 3, 4 and 5; ``mouth.autosplit``).

The detached ``mouth split`` is ONE seam, ``mouth.split.spawn``, spied on here: nothing in
this file splits, decodes or runs a separator. The duration probe
(``mouth.instrumental.probe_duration``) is stubbed per test, and counted, so a test can
say that a play ran no probe at all.

- A split starts only for a span of 30 s or more, the whole file or its ``--start``/
  ``--end`` section, from a file of 10 min or less.
- A play on a call device never starts one, nor waits on one.
- A file the probe cannot read never starts one.
- An eligible play spawns once; the first ``running`` record, naming the child's pid, is
  written before the play is queued; the play is stamped ``split_wait`` and
  ``split_eta_s``. Two plays racing spawn once, and the second waits on the same record.
- A ``running`` record whose pid lives is joined, never read as a failure, inside the
  player's grace past its first deadline (a split queued on the one-split lock); past
  the grace it is a failure.
- With no separator, the original plays and the one install command is printed on the
  caller's stderr, on every such play; nothing is spoken.
- ``--instrumental`` lifts the other limits, and is refused with no separator.
  ``--no-instrumental`` never spawns.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

import mouth
from ears.home import log_dir
from mouth import __main__ as cli
from mouth import autosplit, box, splitwait
from mouth import instrumental as _instrumental
from mouth import split as _split
from tests import env_fence

SID = "test-sid-split-decision"
REAL_PROBE = _instrumental.probe_duration
SONG_S = 200.0          # an eligible song: over the 30 s floor, under the 10 min ceiling


@pytest.fixture
def home(tmp_path, monkeypatch):
    d = tmp_path / "mouth"
    d.mkdir()
    monkeypatch.setenv("MOUTH_DIR", str(d))
    monkeypatch.setenv("MOUTH_SETTINGS", str(tmp_path / "mouth.json"))
    monkeypatch.setenv("MOUTH_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MOUTH_DEVICE", "null")
    monkeypatch.setenv("MOUTH_BACKEND", "silence")
    monkeypatch.setenv("MOUTH_SESSION_BIN", "")
    monkeypatch.setenv("VOICEMODE_SESSION_ID", SID)
    env_fence.delenv(monkeypatch, "MOUTH_MUSIC_INSTRUMENTAL")
    music = tmp_path / "music"
    music.mkdir()
    song = music / "song.m4a"
    song.write_bytes(b"not decoded here")
    h = {"d": d, "tmp": tmp_path, "song": song, "durations": {}, "probes": [], "spawns": [],
         "spawn_hook": None}

    def probe(path):
        h["probes"].append(str(path))
        return h["durations"].get(str(path), SONG_S)

    def spawn(source, d=None):
        h["spawns"].append(str(source))
        if h["spawn_hook"]:
            h["spawn_hook"](source)
        return os.getpid()          # alive: the record reads ``running``

    monkeypatch.setattr(_instrumental, "probe_duration", probe)
    monkeypatch.setattr(_split, "spawn", spawn)
    return h


@pytest.fixture
def separator(monkeypatch):
    """A separator present: ``$MOUTH_SEPARATOR`` set. Never run: the spawn is spied."""
    monkeypatch.setenv(_split.SEPARATOR_ENV, f"{sys.executable} -c pass")


def play(h, path=None, **kw):
    return mouth.play(str(path or h["song"]), spawn=False, d=h["d"], **kw)


def queued(h) -> list:
    return box.Queue(h["d"]).lines()


def record(h, path=None):
    return _split.read_record(path or h["song"], h["d"])


def running_record(h, path=None, pid=None, eta=50.0):
    path = Path(path or h["song"])
    st = path.stat()
    now = time.time()
    return _split.write_record(path, {"source": str(path), "pid": pid or os.getpid(),
                                      "size": st.st_size, "mtime_ns": st.st_mtime_ns,
                                      "state": "running", "started_t": round(now, 3),
                                      "eta_s": eta, "deadline_t": now + 600.0,
                                      "duration_s": SONG_S}, h["d"])


def failed_record(h, path=None, detail="the separator failed: exit 1, no report"):
    path = Path(path or h["song"])
    st = path.stat()
    return _split.write_record(path, {"source": str(path), "pid": 1, "size": st.st_size,
                                      "mtime_ns": st.st_mtime_ns, "state": "failed",
                                      "detail": detail}, h["d"])


def instrumental_in_sidecar(h, path=None):
    path = Path(path or h["song"])
    mixes = path.parent / f".{path.name}.d" / "mixes"
    mixes.mkdir(parents=True)
    (mixes / "instrumental.flac").write_bytes(b"fLaC")


# ---- the floor and the ceiling (R16) ---------------------------------------------------------

def test_a_short_file_never_spawns(home, separator, capsys):
    home["durations"][str(home["song"])] = 12.0
    item = play(home)
    assert home["spawns"] == []
    assert "split_wait" not in item and "split_eta_s" not in item
    assert record(home) is None
    assert capsys.readouterr().err == ""          # a chime says nothing


def test_just_under_the_floor_never_spawns_and_the_floor_itself_does(home, separator):
    home["durations"][str(home["song"])] = autosplit.FLOOR_S - 0.01
    play(home)
    assert home["spawns"] == []
    home["durations"][str(home["song"])] = autosplit.FLOOR_S
    play(home)
    assert len(home["spawns"]) == 1


def test_a_short_section_of_an_eligible_file_never_spawns_and_runs_no_probe(home, separator,
                                                                            capsys):
    item = play(home, start=10.0, end=25.0)       # 15 s of a 200 s song
    assert home["spawns"] == [] and home["probes"] == []
    assert "split_wait" not in item
    assert capsys.readouterr().err == ""


def test_a_short_tail_section_never_spawns(home, separator):
    play(home, start=SONG_S - 10.0)               # from 190 s to the end: 10 s
    assert home["spawns"] == []
    assert home["probes"] == [str(home["song"])]  # no --end: the span needs the duration
    play(home, start=150.0, end=500.0)            # 500 s is past the end: 50 s plays, but
    assert len(home["spawns"]) == 1               # min(end, duration) - start is the span


def test_a_long_section_of_an_eligible_file_spawns(home, separator):
    play(home, start=30.0, end=90.0)
    assert len(home["spawns"]) == 1


def test_a_file_over_the_ceiling_plays_as_it_is_and_names_the_flag(home, separator, capsys):
    home["durations"][str(home["song"])] = autosplit.CEILING_S + 1
    item = play(home)
    assert home["spawns"] == [] and "split_wait" not in item
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "--instrumental" in err and "song.m4a" in err


def test_the_ceiling_is_10_min():
    """The ceiling bounds the separator's memory: a 20-minute split peaked at about 7.3 GB
    of footprint even with the runner's post-processing in place, so 10 min it is."""
    assert autosplit.CEILING_S == 10 * 60


def test_a_file_of_exactly_the_ceiling_spawns(home, separator):
    home["durations"][str(home["song"])] = 10 * 60.0
    play(home)
    assert len(home["spawns"]) == 1


def test_a_file_just_over_10_min_plays_as_it_is_and_says_10_min(home, separator, capsys):
    home["durations"][str(home["song"])] = 10 * 60.0 + 0.5
    item = play(home)
    assert home["spawns"] == [] and "split_wait" not in item
    assert "is over 10 min" in capsys.readouterr().err


# ---- a call device (R11) -----------------------------------------------------------------------

CALLS = ("call", "call:3", "CALL:7", "Call")


@pytest.mark.parametrize("device", CALLS)
def test_a_play_on_a_call_device_never_spawns(home, separator, capsys, device):
    item = play(home, device=device)
    assert home["spawns"] == [] and home["probes"] == []    # a call costs nothing
    assert "split_wait" not in item
    assert capsys.readouterr().err == ""


def test_a_call_device_from_the_env_never_spawns(home, separator, monkeypatch):
    monkeypatch.setenv("MOUTH_DEVICE", "call:2")
    item = play(home)
    assert item["device"] == "call:2"
    assert home["spawns"] == [] and home["probes"] == []


def test_a_call_with_no_separator_prints_nothing(home, capsys):
    play(home, device="call:1")
    assert capsys.readouterr().err == ""          # not the install line: a call never splits


@pytest.mark.parametrize("device", ("call", "call:3"))
def test_instrumental_on_a_call_with_no_instrumental_is_refused(home, separator, device):
    with pytest.raises(ValueError) as e:
        play(home, device=device, instrumental=True)
    msg = str(e.value)
    assert "mouth split " in msg and str(home["song"]) in msg
    assert home["spawns"] == [] and queued(home) == []


def test_instrumental_on_a_call_with_no_separator_names_the_install(home):
    with pytest.raises(ValueError, match="mouth split --install"):
        play(home, device="call", instrumental=True)
    assert home["spawns"] == [] and queued(home) == []


def test_instrumental_on_a_call_with_an_instrumental_plays_it(home, separator):
    instrumental_in_sidecar(home)
    item = play(home, device="call:4", instrumental=True)
    assert home["spawns"] == [] and "split_wait" not in item
    assert len(queued(home)) == 1                 # the player swaps it in at take time


def test_a_call_play_while_the_file_splits_never_waits(home, separator):
    running_record(home)
    before = record(home)
    item = play(home, device="call:1")
    assert home["spawns"] == []
    assert "split_wait" not in item               # the original plays at once
    assert record(home) == before


# ---- the probe fails soft ----------------------------------------------------------------------

def test_a_file_the_probe_cannot_read_never_spawns(home, separator, capsys):
    home["durations"][str(home["song"])] = None   # Duration: N/A, or no ffmpeg
    item = play(home)
    assert home["spawns"] == [] and "split_wait" not in item
    assert capsys.readouterr().err == ""


def test_with_no_ffmpeg_on_path_the_real_probe_fails_soft(home, separator, monkeypatch, capsys):
    monkeypatch.setattr(_instrumental, "probe_duration", REAL_PROBE)
    empty = home["tmp"] / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))        # no ffmpeg to run
    item = play(home)
    assert home["spawns"] == [] and "split_wait" not in item
    assert capsys.readouterr().err == ""


@pytest.mark.needs_ffmpeg
def test_a_file_ffmpeg_cannot_read_never_spawns(home, separator, monkeypatch, capsys):
    monkeypatch.setattr(_instrumental, "probe_duration", REAL_PROBE)
    item = play(home)                             # the song's bytes are not audio
    assert home["spawns"] == [] and "split_wait" not in item
    assert capsys.readouterr().err == ""


def test_instrumental_on_a_file_with_no_duration_is_refused(home, separator):
    home["durations"][str(home["song"])] = None
    with pytest.raises(ValueError, match="no duration"):
        play(home, instrumental=True)
    assert home["spawns"] == [] and queued(home) == []


# ---- an eligible play ----------------------------------------------------------------------------

def test_an_eligible_play_spawns_once_with_its_record_before_the_queue(home, separator,
                                                                      monkeypatch, capsys):
    seen = {}
    real_drop = box.drop

    def drop(bx, item, **kw):
        seen["record"] = record(home)
        seen["queued_before"] = len(queued(home))
        return real_drop(bx, item, **kw)

    monkeypatch.setattr(box, "drop", drop)
    t0 = time.time()
    item = play(home)
    assert home["spawns"] == [str(home["song"])]
    rec = seen["record"]
    assert rec is not None and seen["queued_before"] == 0     # written before the play's mail
    assert rec["state"] == "running" and rec["pid"] == os.getpid()
    assert rec["source"] == str(home["song"]) and rec["duration_s"] == SONG_S
    st = home["song"].stat()
    assert (rec["size"], rec["mtime_ns"]) == (st.st_size, st.st_mtime_ns)
    eta = round(SONG_S * _split.DEFAULT_SPEED, 1)
    assert rec["eta_s"] == eta
    assert rec["deadline_t"] >= t0 + _split.deadline_for(eta) - 1
    assert item["split_wait"] is True and item["split_eta_s"] == eta
    q = queued(home)
    assert len(q) == 1 and q[0]["split_wait"] is True and q[0]["split_eta_s"] == eta
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "making the instrumental" in err and "song.m4a" in err and f"{round(eta)} s" in err
    assert "--no-instrumental" in err


def test_the_eta_counts_the_splits_already_queued(home, separator, tmp_path):
    other = home["song"].parent / "other.flac"
    other.write_bytes(b"x")
    running_record(home, path=other, eta=100.0)
    item = play(home)
    own = SONG_S * _split.DEFAULT_SPEED
    assert item["split_eta_s"] == pytest.approx(own + 100.0, abs=1.0)
    assert record(home)["deadline_t"] >= time.time() + _split.deadline_for(own + 99.0) - 1


def test_a_second_play_during_the_split_spawns_nothing_and_waits_on_the_same_record(
        home, separator, capsys):
    first = play(home)
    rec = record(home)
    capsys.readouterr()
    second = play(home)
    assert len(home["spawns"]) == 1
    assert record(home) == rec                    # the live record is never overwritten
    assert second["split_wait"] is True
    assert 0 < second["split_eta_s"] <= first["split_eta_s"]
    assert "making the instrumental" in capsys.readouterr().err
    assert len(queued(home)) == 2


def test_two_plays_racing_spawn_once(home, separator):
    """N1: the record's lock is held across the read, the spawn and the first write, so a
    second play that reads the record while the first is spawning waits for it."""
    gate = threading.Event()

    def slow(source):
        gate.set()
        time.sleep(0.3)                           # the window a race would fall into

    home["spawn_hook"] = slow
    out, errs = {}, []

    def run(name):
        try:
            out[name] = play(home)
        except Exception as e:                    # pragma: no cover - a failure to report
            errs.append(e)

    a = threading.Thread(target=run, args=("a",))
    a.start()
    assert gate.wait(5)
    b = threading.Thread(target=run, args=("b",))
    b.start()
    a.join(10)
    b.join(10)
    assert errs == []
    assert len(home["spawns"]) == 1
    assert out["a"]["split_wait"] and out["b"]["split_wait"]
    assert record(home)["pid"] == os.getpid()


def test_a_dead_running_record_is_a_failure_and_never_joined(home, separator, capsys):
    running_record(home, pid=2 ** 22 + 12345)     # no such process
    item = play(home)
    assert home["spawns"] == [] and "split_wait" not in item
    err = capsys.readouterr().err
    assert "split failed (it stopped before it finished)" in err
    assert "--instrumental tries again" in err


def test_a_live_running_record_past_its_first_deadline_is_joined_not_failed(home, separator,
                                                                              capsys):
    """A split queued behind another's on the one-split lock: its first deadline has
    passed, its pid lives. It is no recorded failure: the play joins it, and a --wait
    is not timed out at the passed deadline but at the player's own bound (the grace)."""
    rec = running_record(home)
    past = round(time.time() - 5, 3)
    _split.write_record(home["song"], {**rec, "deadline_t": past}, home["d"])
    plan = autosplit.decide(home["song"], on=True, src="setting", device="null", d=home["d"])
    assert plan.action == "joined" and plan.stamp["split_wait"] is True
    assert plan.deadline_t == pytest.approx(past + splitwait.GRACE_S)
    item = play(home)
    assert home["spawns"] == [] and item["split_wait"] is True
    err = capsys.readouterr().err
    assert "making the instrumental" in err and "--instrumental tries again" not in err


def test_a_live_running_record_past_the_grace_is_a_failure(home, separator, capsys):
    rec = running_record(home)
    _split.write_record(home["song"], {**rec, "deadline_t": time.time() - splitwait.GRACE_S - 5},
                        home["d"])
    item = play(home)
    assert home["spawns"] == [] and "split_wait" not in item
    err = capsys.readouterr().err
    # its process still runs: overdue, never worded as a split that stopped or failed
    assert "still running, past its deadline" in err and "--instrumental tries again" in err
    assert "failed" not in err and "stopped" not in err


def test_an_existing_instrumental_is_the_players_to_play(home, separator, capsys):
    instrumental_in_sidecar(home)
    item = play(home)
    assert home["spawns"] == [] and home["probes"] == [] and "split_wait" not in item
    assert capsys.readouterr().err == ""


# ---- a recorded failure ----------------------------------------------------------------------------

def test_a_recorded_failure_is_not_retried_and_its_reason_is_repeated(home, separator, capsys):
    failed_record(home, detail="the separator failed: out of memory")
    item = play(home)
    assert home["spawns"] == [] and home["probes"] == [] and "split_wait" not in item
    err = capsys.readouterr().err
    assert "out of memory" in err and "--instrumental tries again" in err


def test_a_short_section_of_a_failed_file_says_nothing(home, separator, capsys):
    failed_record(home)
    play(home, start=0.0, end=5.0)
    assert home["spawns"] == [] and capsys.readouterr().err == ""


def test_a_failure_for_other_bytes_does_not_count(home, separator):
    failed_record(home)
    home["song"].write_bytes(b"new bytes, a new song")
    play(home)
    assert len(home["spawns"]) == 1


# ---- no separator (R5, R14) -----------------------------------------------------------------------

def test_a_missing_separator_plays_the_original_with_the_install_line_every_time(home, capsys):
    for n in (1, 2):
        item = play(home)
        assert "split_wait" not in item
        err = capsys.readouterr().err
        assert err.count("\n") == 1, n
        assert "mouth split --install" in err and "song.m4a" in err
    assert home["spawns"] == [] and record(home) is None
    q = queued(home)
    assert len(q) == 2                            # nothing else queued: nothing is spoken
    assert all(x["file"] == str(home["song"]) and x["text"].startswith("[sound ") for x in q)


def test_a_missing_separator_says_nothing_for_a_short_file(home, capsys):
    home["durations"][str(home["song"])] = 3.0
    play(home)
    assert capsys.readouterr().err == ""          # the separator is checked last


def test_the_setting_off_silences_the_install_line(home, monkeypatch, capsys):
    monkeypatch.setenv("MOUTH_MUSIC_INSTRUMENTAL", "off")
    play(home)
    assert capsys.readouterr().err == "" and home["probes"] == []


# ---- the flags -------------------------------------------------------------------------------------

def test_instrumental_lifts_the_floor_the_ceiling_and_a_failure(home, separator):
    short = home["song"].parent / "chime.wav"
    short.write_bytes(b"c")
    home["durations"][str(short)] = 2.0
    long = home["song"].parent / "mix.mp3"
    long.write_bytes(b"m")
    home["durations"][str(long)] = autosplit.CEILING_S * 3
    failed_record(home)
    for p in (short, long, home["song"]):
        item = play(home, path=p, instrumental=True)
        assert item["split_wait"] is True, p
    assert home["spawns"] == [str(short), str(long), str(home["song"])]


def test_instrumental_lifts_a_short_section(home, separator):
    item = play(home, start=1.0, end=4.0, instrumental=True)
    assert item["split_wait"] is True and len(home["spawns"]) == 1


def test_instrumental_with_no_separator_is_refused_before_anything_is_queued(home):
    with pytest.raises(ValueError, match="mouth split --install"):
        play(home, instrumental=True)
    assert home["spawns"] == [] and queued(home) == [] and record(home) is None


def test_instrumental_with_no_separator_and_an_instrumental_plays_it(home):
    instrumental_in_sidecar(home)
    item = play(home, instrumental=True)
    assert "split_wait" not in item and len(queued(home)) == 1


def test_no_instrumental_never_spawns(home, separator, capsys):
    item = play(home, instrumental=False)
    assert home["spawns"] == [] and home["probes"] == [] and "split_wait" not in item
    assert capsys.readouterr().err == ""


def test_the_cli_refuses_instrumental_with_no_separator(home, capsys):
    rc = cli.main(["play", str(home["song"]), "--instrumental"])
    assert rc == 2
    assert "mouth split --install" in capsys.readouterr().err
    assert queued(home) == []


def test_the_cli_prints_the_split_line_on_stderr(home, separator, capsys, monkeypatch):
    monkeypatch.setattr(mouth, "ensure_player", lambda d=None: False)
    rc = cli.main(["play", str(home["song"])])
    assert rc == 0
    out, err = capsys.readouterr()
    assert "making the instrumental" in err and "making" not in out


# ---- --wait ------------------------------------------------------------------------------------------

def test_wait_is_extended_by_the_splits_deadline_and_names_the_eta(home, separator):
    """A waited play is not timed out by its own split: its timeout grows by the split's
    deadline, and the said record it returns carries the ETA."""
    done = home["d"] / "done"

    def finish_later():
        for _ in range(200):
            q = queued(home)
            if q:
                break
            time.sleep(0.01)
        time.sleep(0.4)                           # past the 0.1 s timeout asked for
        done.mkdir(exist_ok=True)
        (done / f"{q[0]['utt']}.json").write_text(json.dumps({"utt": q[0]["utt"],
                                                              "reason": "done"}))

    t = threading.Thread(target=finish_later)
    t.start()
    rec = play(home, wait=True, timeout=0.1)
    t.join(5)
    assert rec["reason"] == "done"
    assert rec["split"] == {"eta_s": round(SONG_S * _split.DEFAULT_SPEED, 1)}


def test_wait_with_no_split_keeps_its_timeout(home):
    with pytest.raises(TimeoutError):
        play(home, wait=True, timeout=0.1, instrumental=False)


# ---- the seam ----------------------------------------------------------------------------------------

def test_spawn_starts_mouth_split_detached(tmp_path, monkeypatch):
    calls = []

    class Child:
        pid = 4242

    def popen(argv, **kw):
        calls.append((argv, kw))
        return Child()

    monkeypatch.setattr(_split.subprocess, "Popen", popen)
    d = tmp_path / "mouth"
    song = tmp_path / "s.flac"
    assert _split.spawn(song, d) == 4242
    (argv, kw), = calls
    assert argv == [sys.executable, "-m", "mouth", "split", str(song)]
    assert kw["start_new_session"] is True
    assert kw["stdin"] is _split.subprocess.DEVNULL
    assert kw["env"]["MOUTH_DIR"] == str(d)
    assert (log_dir() / "split.log").exists()           # the voice log folder (EARS-483)
    assert not (d / "splits" / "split.log").exists()    # ...never the mouth dir
    assert (d / "splits").is_dir()                       # which still holds the records


def test_a_spawn_that_cannot_start_plays_the_original(home, separator, monkeypatch, capsys):
    def boom(source, d=None):
        raise OSError("no such interpreter")

    monkeypatch.setattr(_split, "spawn", boom)
    item = play(home)
    assert "split_wait" not in item and record(home) is None
    assert "cannot start mouth split" in capsys.readouterr().err
    assert len(queued(home)) == 1
