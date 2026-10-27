"""The player's hold, end to end (design.md section 5; ``mouth.splitwait``).

A ``mouth play`` of a song with no instrumental starts its split and waits for it in the
queue, not in a bed. Here the split is real (``mouth.split.split_file``: the ffmpeg decode,
the locks, the record, the atomic rename) and the separator is the fake one
(``tests/fixtures/fake_separator.py``), so these need ffmpeg. The detached spawn is the one
seam (``mouth.split.spawn``): it runs the split on a thread of this process, behind a gate
the test opens, and returns this process's pid, alive, as a child's would be. The player
decodes with a recording stand-in for ffmpeg (``Rec``), so what it played is a path.

The floor (30 s) is lowered to 1 s, so a 2 s tone is an eligible song and the split takes
a moment.

- The first play splits, waits, then plays the instrumental; the second reuses it, with
  no split.
- A mailed play of an eligible file with no instrumental plays the original, and splits
  nothing; a mailed ``split_wait`` never holds it.
- A failed split, a dead one, and one with no record play the original, with the reason.
- A split past its first deadline while it waits its turn still holds its play, and a
  second play then joins it, never playing the original.
- A retract during the split drops the play; the split finishes.
- A spoken line to the same device is not held; a later file for that device is.
- A line for another device plays first, and never leapfrogs the waiting song onto its
  idle device before its split is done.
- ``mouth status`` lists the running split and the play that waits on it.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import threading
import time
from email.message import EmailMessage
from pathlib import Path

import pytest
from maildir_put import put
from test_mouth_bed import HOST, env, kind, rows, run, say, started, wait_for  # noqa: F401  (fixture)
from test_mouth_instrumental_play import Rec, make_instrumental, rec, said_for, song  # noqa: F401  (fixture)

import mouth
from mouth import autosplit, box, instrumental, player, sidecar, splitwait
from mouth import split as _split

pytestmark = pytest.mark.needs_ffmpeg

FAKE = Path(__file__).resolve().parents[1] / "fixtures" / "fake_separator.py"


@pytest.fixture
def rig(rec, monkeypatch):
    """``rec`` (the recording decoder, the setting's built-in on) with the fake separator
    present, a 1 s floor, and the spawn seam running the real split on a thread behind
    ``rig.gate``."""
    monkeypatch.setattr(autosplit, "FLOOR_S", 1.0)
    log = rec.tmp / "separator.jsonl"
    monkeypatch.setenv(_split.SEPARATOR_ENV, f"{shlex.quote(sys.executable)} {shlex.quote(str(FAKE))}")
    monkeypatch.setenv("MOUTH_FAKE_SEPARATOR_LOG", str(log))
    gate = threading.Event()
    gate.set()
    spawns: list = []
    threads: list = []

    def spawn(source, d=None):
        spawns.append(str(source))

        def child():
            gate.wait(30)
            _split.split_file(source, d=d)

        t = threading.Thread(target=child, daemon=True)
        t.start()
        threads.append(t)
        return os.getpid()          # alive, and the split names it: the caller's record stands

    monkeypatch.setattr(_split, "spawn", spawn)
    rec.gate, rec.spawns, rec.threads, rec.seplog = gate, spawns, threads, log
    yield rec
    gate.set()
    for t in threads:
        t.join(30)


def separations(rig) -> int:
    """How many times the separator ran."""
    return len(rig.seplog.read_text().splitlines()) if rig.seplog.exists() else 0


def a_song(rig, name="song.wav", folder=None) -> Path:
    return song(rig, name=name, folder=folder, seconds=2.0)


def play(rig, path: Path, **kw) -> dict:
    kw.setdefault("device", "null")
    return mouth.play(str(path), spawn=False, d=rig.d, **kw)


def the_instrumental(src: Path) -> Path:
    return sidecar.canonical_dir(src) / "mixes" / "instrumental.wav"


def held_for(rig, utt, s=0.6) -> bool:
    """True if UTT has not started in S seconds (a player is running)."""
    return not wait_for(lambda: started(rig, utt), s)


def mailed(rig, path: Path, extra=None) -> str:
    """A mail from outside asking the mouth to play PATH, with X-Mouth-Extra EXTRA."""
    rig.n[0] += 1
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = f"vera@{HOST}.example.ts.net", f"mouth@{HOST}", "a sound"
    mid = f"<{rig.n[0]}.e2e@{HOST}.session-mail>"
    m["Message-ID"] = mid
    m["X-Mouth-File"] = str(path)
    m["X-Mouth-Device"] = "null"
    if extra is not None:
        m["X-Mouth-Extra"] = json.dumps(extra)
    m.set_content("")
    put(rig.mail, f"{os.urandom(4).hex()}.{rig.n[0]}.{HOST}", bytes(m))
    return mid


def dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def rewrite(rig, src: Path, **changes) -> None:
    with _split.record_lock(src, rig.d):
        rec = _split.read_record(src, rig.d)
        _split.write_record(src, {**rec, **changes}, rig.d)


# -- the first play splits, then plays the instrumental; the second reuses it ----------------

def test_the_first_play_splits_then_plays_the_instrumental_and_the_second_reuses_it(rig, capsys):
    src = a_song(rig)
    rig.gate.clear()                                  # the split waits until we say
    first = play(rig, src)
    assert "making the instrumental" in capsys.readouterr().err
    assert first["split_wait"] is True and rig.spawns == [str(src)]
    th = run(rig)
    assert held_for(rig, first["utt"]), "the play did not wait for its split"
    st = mouth.status(rig.d)
    (row,) = st["splits"]
    assert row["file"] == str(src) and row["pid"] == os.getpid()
    assert row["plays"] == [first["utt"]] and isinstance(row["left_s"], float)
    rig.gate.set()
    th.join(15)
    assert not th.is_alive()
    inst = the_instrumental(src)
    assert inst.is_file()
    assert Rec.paths == [str(inst)]                   # the instrumental, never the song
    said = said_for(rig, first["utt"])
    assert said["reason"] == "done" and said["source_file"] == str(src)
    assert said["split"]["state"] == "done" and "detail" not in said["split"]
    assert said["split"]["eta_s"] == first["split_eta_s"]
    assert mouth.status(rig.d)["splits"] == []        # nothing runs now
    assert separations(rig) == 1

    second = play(rig, src)
    assert "split_wait" not in second
    run(rig).join(8)
    assert rig.spawns == [str(src)] and separations(rig) == 1    # no second split
    assert Rec.paths == [str(inst), str(inst)]
    said2 = said_for(rig, second["utt"])
    assert said2["source_file"] == str(src) and "split" not in said2


def test_a_second_play_during_the_split_waits_on_it_and_both_play_the_instrumental(rig):
    src = a_song(rig)
    rig.gate.clear()
    one = play(rig, src)
    two = play(rig, src)
    assert rig.spawns == [str(src)] and two["split_wait"] is True
    th = run(rig)
    assert held_for(rig, one["utt"]) and not started(rig, two["utt"])
    assert mouth.status(rig.d)["splits"][0]["plays"] == [one["utt"], two["utt"]]
    rig.gate.set()
    th.join(15)
    inst = str(the_instrumental(src))
    assert Rec.paths == [inst, inst] and separations(rig) == 1
    order = sorted(rows(rig), key=lambda r: r["seq"])
    pos = {(r["kind"], r["utt"]): i for i, r in enumerate(order)}
    assert pos[("said", one["utt"])] < pos[("saying", two["utt"])]   # in order, one bed


# -- a mail from outside never splits, and its stamp never holds -----------------------------

def test_a_mailed_play_of_an_eligible_file_plays_the_original_and_splits_nothing(rig, monkeypatch):
    sounds = rig.tmp / "sounds"
    src = a_song(rig, folder=sounds)
    monkeypatch.setenv("MOUTH_SOUNDS_DIR", str(sounds))
    mid = mailed(rig, src)
    run(rig).join(8)
    said = said_for(rig)
    assert said["mail_id"] == mid and said["reason"] == "done"
    assert Rec.paths == [os.path.realpath(src)]
    assert rig.spawns == [] and separations(rig) == 0           # call count 0
    assert "source_file" not in said and "split" not in said
    assert not sidecar.canonical_dir(src).exists()


@pytest.mark.parametrize("live", [False, True], ids=["no-record", "a-local-split-running"])
def test_a_mailed_split_wait_never_holds_the_play(rig, monkeypatch, live):
    sounds = rig.tmp / "sounds"
    src = a_song(rig, folder=sounds)
    monkeypatch.setenv("MOUTH_SOUNDS_DIR", str(sounds))
    if live:                                          # the user's own play started a split
        rig.gate.clear()                              # (on another device: a waiting play
        local = play(rig, src, device="dev-b")        # holds later files for its own)
        assert local["split_wait"] is True
    mid = mailed(rig, src, extra={"split_wait": True, "split_eta_s": 40})
    th = run(rig)
    assert wait_for(lambda: any(r.get("mail_id") == mid for r in kind(rig, "said").values()), 5)
    said = next(r for r in kind(rig, "said").values() if r.get("mail_id") == mid)
    assert said["reason"] == "done" and "split" not in said
    assert Rec.paths[0] == os.path.realpath(src)      # at once, as it is
    rig.gate.set()
    th.join(15)
    assert len(rig.spawns) == (1 if live else 0)


def test_a_mailed_source_file_is_ignored_and_the_swap_names_the_real_one(rig, monkeypatch):
    sounds = rig.tmp / "sounds"
    src = a_song(rig, folder=sounds)
    inst = make_instrumental(src)
    other = a_song(rig, name="other.wav", folder=sounds)
    monkeypatch.setenv("MOUTH_SOUNDS_DIR", str(sounds))
    mailed(rig, src, extra={"source_file": str(other)})
    mailed(rig, other, extra={"source_file": str(src)})   # no instrumental: no swap to claim
    run(rig).join(8)
    by_file = {Path(r["file"]).name: r for r in kind(rig, "said").values()}
    assert Rec.paths == [os.path.realpath(inst), os.path.realpath(other)]
    assert by_file["instrumental.wav"]["source_file"] == os.path.realpath(src)
    assert "source_file" not in by_file["other.wav"]


# -- the split fails, dies or has no record: the original, with the reason --------------------

def test_a_failed_split_plays_the_original_with_the_reason(rig, monkeypatch):
    monkeypatch.setenv("MOUTH_FAKE_SEPARATOR_FAIL", "1")
    src = a_song(rig)
    f = play(rig, src)
    run(rig).join(15)
    assert Rec.paths == [str(src)]
    said = said_for(rig, f["utt"])
    assert said["reason"] == "done" and "source_file" not in said
    assert said["split"]["state"] == "failed" and said["split"]["detail"]
    assert _split.read_record(src, rig.d)["state"] == "failed"
    assert not the_instrumental(src).exists()


def test_a_dead_split_plays_the_original_with_the_reason(rig):
    src = a_song(rig)
    rig.gate.clear()
    f = play(rig, src)
    pid = dead_pid()
    rewrite(rig, src, pid=pid)                        # the child died before it finished
    run(rig).join(8)
    assert Rec.paths == [str(src)]
    split = said_for(rig, f["utt"])["split"]
    assert split["state"] == "dead" and str(pid) in split["detail"]


def test_a_stamped_play_whose_record_is_gone_plays_at_once(rig):
    src = a_song(rig)
    rig.gate.clear()
    f = play(rig, src)
    _split.record_path(src, rig.d).unlink()           # the stamp alone never holds a play
    run(rig).join(8)
    assert Rec.paths == [str(src)]
    assert said_for(rig, f["utt"])["split"]["state"] == "missing"


def test_a_split_past_its_first_deadline_while_queued_still_holds_its_play(rig):
    """A split queued behind another's: its first deadline passes while it waits its turn,
    its pid alive. The play holds, and plays the instrumental when it is made."""
    src = a_song(rig)
    rig.gate.clear()
    f = play(rig, src)
    rewrite(rig, src, deadline_t=round(time.time() - 5, 3))
    assert _split.record_state(_split.read_record(src, rig.d)) == "failed"   # what it reads
    th = run(rig)
    assert held_for(rig, f["utt"]), "a live split past its first deadline let its play go"
    rig.gate.set()                                    # its turn: a new deadline, then the split
    th.join(15)
    assert Rec.paths == [str(the_instrumental(src))]
    assert said_for(rig, f["utt"])["split"]["state"] == "done"


def test_a_second_play_during_a_queued_split_past_its_deadline_joins_it(rig, capsys):
    """The caller's half of the same grace: a second play while the split waits its turn,
    past its first deadline, joins it. It neither spawns again nor reads the live split as
    a failure and plays the original, with its vocals, before the first play."""
    src = a_song(rig)
    rig.gate.clear()
    one = play(rig, src)
    rewrite(rig, src, deadline_t=round(time.time() - 5, 3))
    capsys.readouterr()
    two = play(rig, src)
    err = capsys.readouterr().err
    assert rig.spawns == [str(src)] and two["split_wait"] is True
    assert "making the instrumental" in err and "plays as it is" not in err
    th = run(rig)
    assert held_for(rig, two["utt"]), "the second play did not wait for the split it joined"
    rig.gate.set()
    th.join(15)
    inst = str(the_instrumental(src))
    assert Rec.paths == [inst, inst] and separations(rig) == 1      # never the original
    assert said_for(rig, two["utt"])["split"]["state"] == "done"


def test_a_split_past_its_deadline_and_the_grace_lets_its_play_go(rig):
    src = a_song(rig)
    rig.gate.clear()
    f = play(rig, src)
    rewrite(rig, src, deadline_t=round(time.time() - splitwait.GRACE_S - 5, 3))
    run(rig).join(8)
    assert Rec.paths == [str(src)]
    assert said_for(rig, f["utt"])["split"]["state"] == "overdue"


# -- a retract drops the play, never the job ---------------------------------------------------

def test_a_retract_during_the_split_drops_the_play_and_the_split_finishes(rig):
    src = a_song(rig)
    rig.gate.clear()
    f = play(rig, src)
    th = run(rig)
    assert held_for(rig, f["utt"], 0.3)
    mouth.retract(f["utt"], d=rig.d)
    assert wait_for(lambda: f["utt"] in kind(rig, "said"), 3)
    assert kind(rig, "said")[f["utt"]]["reason"] == "retracted"
    rig.gate.set()
    for t in rig.threads:
        t.join(15)
    th.join(8)
    assert Rec.paths == []                            # it never played
    assert the_instrumental(src).is_file()            # but the split made its outputs
    assert _split.read_record(src, rig.d)["state"] == "done"


# -- what is held with it, and what is not -----------------------------------------------------

def test_a_spoken_line_to_the_same_device_is_not_held(rig):
    src = a_song(rig)
    rig.gate.clear()
    f = play(rig, src)
    line = say(rig, "a line while the song waits")     # the same device, null
    th = run(rig)
    assert wait_for(lambda: line["utt"] in kind(rig, "said"), 5), "the line waited for the split"
    assert kind(rig, "said")[line["utt"]]["reason"] == "done"
    assert not started(rig, f["utt"])
    rig.gate.set()
    th.join(15)
    assert Rec.paths == [str(the_instrumental(src))]


def test_a_later_file_for_the_same_device_waits_and_other_devices_do_not(rig):
    src = a_song(rig)
    rig.gate.clear()
    f = play(rig, src)
    after = play(rig, song(rig, name="after.wav", seconds=0.3))        # under the floor
    elsewhere = play(rig, song(rig, name="tink.wav", seconds=0.3), device="dev-b")
    th = run(rig)
    assert wait_for(lambda: elsewhere["utt"] in kind(rig, "said"), 5), "another device waited"
    assert not started(rig, after["utt"]), "a later file for its device jumped the waiting play"
    rig.gate.set()
    th.join(15)
    order = sorted(rows(rig), key=lambda r: r["seq"])
    pos = {(r["kind"], r["utt"]): i for i, r in enumerate(order)}
    assert pos[("saying", f["utt"])] < pos[("saying", after["utt"])]
    assert all(kind(rig, "said")[x["utt"]]["reason"] == "done" for x in (f, after, elsewhere))


# -- the leapfrog take site: a file started while a spoken line plays --------------------------

def test_a_file_leapfrogged_to_an_idle_device_plays_its_instrumental(rig):
    """The bed a queued file gets ahead of a line (``player._leapfrog``, ``_bed_item``)
    swaps in the instrumental as the plain take does."""
    src = song(rig)
    inst = make_instrumental(src)
    line = say(rig, "word " * 20)                     # on null, queued first
    f = play(rig, src, device="dev-b")                # an idle device
    run(rig).join(10)
    saying = kind(rig, "saying")[f["utt"]]
    assert saying.get("leapfrog") is True             # it started through _leapfrog
    assert Rec.paths == [str(inst)]
    said = said_for(rig, f["utt"])
    assert said["source_file"] == str(src) and said["file"] == str(inst)
    assert kind(rig, "said")[line["utt"]]["reason"] == "done"


def test_a_waiting_file_on_an_idle_device_is_not_leapfrogged_while_a_line_plays(rig):
    """``_leapfrog`` holds a waiting play as ``_next`` does: a line for another device
    plays first, and the song on its idle device still waits for its split, then plays
    the instrumental. Without the hold there, the song would start as it is, beside the
    line, before its split finished."""
    src = a_song(rig)
    rig.gate.clear()
    line = say(rig, "word " * 20)                     # on null, queued first
    f = play(rig, src, device="dev-b")                # an idle device, its split held
    assert f["split_wait"] is True
    th = run(rig)
    assert wait_for(lambda: line["utt"] in kind(rig, "said"), 8), "the line waited"
    assert not started(rig, f["utt"]), "the waiting song was leapfrogged as it is"
    assert Rec.paths == []
    rig.gate.set()
    th.join(15)
    assert Rec.paths == [str(the_instrumental(src))]
    said = said_for(rig, f["utt"])
    assert said["source_file"] == str(src) and said["split"]["state"] == "done"
    assert kind(rig, "saying")[f["utt"]].get("leapfrog") is not True


def test_a_waiting_file_behind_a_line_is_settled_at_the_leapfrog_take_site(rig):
    """``_bed_item`` (the leapfrog take) carries what the split came to, as the plain
    take does."""
    src = a_song(rig)
    rig.gate.clear()
    f = play(rig, src)
    (x,) = [e for e in box.Queue(rig.d).lines() if e["utt"] == f["utt"]]
    assert player._playable(x, None, rig.d) is False  # held: _leapfrog never starts it
    rewrite(rig, src, state="failed", detail="the separator failed: exit 1")
    assert player._playable(x, None, rig.d) is True
    item = player._bed_item(x, rig.d)
    assert item["split"] == {"state": "failed", "eta_s": f["split_eta_s"],
                             "detail": "the separator failed: exit 1"}


# -- the pieces --------------------------------------------------------------------------------

def _x(**kw) -> dict:
    return {"utt": "u1", "file": "/music/song.wav", "backend": "file", "device": "null",
            "split_wait": True, "_raw": True, **kw}


def test_stamped_is_a_local_played_file_off_a_call():
    assert splitwait.stamped(_x())
    assert not splitwait.stamped(_x(_raw=False))      # a mail from outside
    assert not splitwait.stamped(_x(split_wait="yes"))
    assert not splitwait.stamped(_x(split_wait=None))
    for dev in ("call", "call:3", "CALL:3"):
        assert not splitwait.stamped(_x(device=dev))
    assert not splitwait.stamped(_x(again_of="u0"))   # a say-again replay is a spoken line


def test_live_holds_a_running_record_with_its_pid_alive_up_to_the_grace():
    now = time.time()
    rec = {"state": "running", "pid": os.getpid(), "deadline_t": now + 10}
    assert splitwait.live(rec, now)
    assert splitwait.live({**rec, "deadline_t": now - 1}, now)                     # in the grace
    assert not splitwait.live({**rec, "deadline_t": now - splitwait.GRACE_S - 1}, now)
    assert not splitwait.live({**rec, "pid": dead_pid()}, now)
    assert not splitwait.live({**rec, "state": "done"}, now)
    assert not splitwait.live(None, now)


def test_outcome_names_each_way_a_split_ends():
    now = time.time()
    assert splitwait.outcome(None, now)[0] == "missing"
    assert splitwait.outcome({"state": "done"}, now) == ("done", None)
    assert splitwait.outcome({"state": "failed", "detail": "x"}, now) == ("failed", "x")
    assert splitwait.outcome({"state": "running", "pid": dead_pid()}, now)[0] == "dead"
    assert splitwait.outcome({"state": "running", "pid": os.getpid()}, now)[0] == "overdue"


def test_a_mailed_source_file_on_a_stem_line_is_dropped_too(tmp_path):
    """``stems.resolve`` names a stem line's ``source_file``; a mail's own never rides."""
    from mouth import stems

    src = tmp_path / "song.wav"
    src.write_bytes(b"x")
    line = {"utt": "u1", "file": str(src), "backend": "file", "stem": ["drums"],
            "source_file": "/elsewhere/other.wav"}
    mailed_out = stems.resolve(line, local=False, d=tmp_path)
    assert "stem_error" in mailed_out and "source_file" not in mailed_out   # no drums: refused
    assert instrumental.swap(mailed_out, local=False, d=tmp_path) == mailed_out
    assert stems.resolve(line, local=True, d=tmp_path)["source_file"] == "/elsewhere/other.wav"
