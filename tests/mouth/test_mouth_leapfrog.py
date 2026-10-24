"""EARS-152 do-006: a file for an IDLE device leapfrogs lines (Q10, fix (a)).

design.md s2 "Files to an idle device leapfrog lines" (super.sessionvoice:
"no device waits on another"). The player takes items in playlist
order across ALL devices, so without this a laptop Tink queued behind call
lines waited for every one of them. Now the player starts a bed for every
ELIGIBLE queued file, in playlist order, at two points: in ``_next``, before it
hands back a line, and in ``play_one``'s 100 ms ``check_stop`` poll (a
callback ``serve`` passes in). A file is eligible when its device:

1. has no live bed;
2. is not the device of the line playing now;
3. has no PLAYABLE line (``_next``'s own test: a held line does not count)
   queued ahead of it.

Its ``saying`` carries ``leapfrog: true`` when it jumped a playable line that
was queued ahead of it.

Seam: file-leapfrogs-lines, under both duck settings, with its negative cases
(same-device order is kept) and the held-line positive case. Plus the start
bound while a CALL line is still opening (review 1118 input 4): the poll
cannot run inside ``open_output``, so a file queued then waits for the open.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import tempfile
import threading
import time
from datetime import datetime

import pytest

import mouth
from ears import heard
from mouth import bed, hold, player

from test_mouth_bed import (env, kind, play, rows, run, say,  # noqa: F401 - fixtures
                            started, tone, wait_for)

DUCK = pytest.mark.parametrize("duck", [True, False], ids=["duck-on", "duck-off"])
LONG = "word " * 12          # ~4 s on the silence backend: a line that is still playing


def ts(r: dict) -> float:
    return datetime.fromisoformat(r["ts"]).timestamp()


def order(env) -> dict:
    return {(r["kind"], r["utt"]): i for i, r in enumerate(sorted(rows(env), key=lambda r: r["seq"]))}


def floor_row(k: str = "partial") -> None:
    """His words on the room mic (a partial holds the floor; a turn ends it)."""
    t = datetime.now().astimezone().isoformat(timespec="milliseconds")
    with open(heard.log_path(), "a") as f:
        f.write(json.dumps({"ts": t, "seq": 1, "kind": k, "text": "mm", "source": "mic",
                            "final": k == "turn"}) + "\n")
    for c in hold._caches.values():
        c.update(t=0.0, v=None)


def setting(env, duck: bool) -> None:
    env.settings.write_text(json.dumps({"music_duck": duck}))


class Points:
    """Which point started each file, in POLL UNITS, not wall clock (EARS-254).

    Wraps ``player.play_one`` (to count the playing line's ``check_stop``
    polls: one call of the ``on_poll`` that ``serve`` passes in is one poll)
    and ``player._leapfrog`` (to see which call started which bed).
    ``at[file utt] == (line utt, n)``: the file's bed was started for that
    line at its poll ``n``, or at ``_next``'s point before the line opened
    (``n == 0``).

    ``pause_in`` (a line's utt): hold that line inside its first poll after
    its ``saying`` row (``paused`` is set, ``self.poll`` is that poll's
    number) until the test sets ``go``. A file queued then is queued strictly between two polls, so
    the next poll, ``self.poll + 1``, is the one that must start it, however
    loaded the box is."""

    def __init__(self, env, monkeypatch) -> None:
        self.at: dict = {}
        self.pause_in = ""
        self.poll = 0
        self.paused, self.go = threading.Event(), threading.Event()
        self._line: list = [None, 0]                 # the line inside play_one, its polls so far
        real_play, real_leapfrog = player.play_one, player._leapfrog

        def leapfrog(d, q, beds, playing):
            got = real_leapfrog(d, q, beds, playing)
            for b in got:   # outside play_one, _line is [None, 0]: _next's point
                self.at.setdefault(b.utt, (playing["utt"] if playing else None, self._line[1]))
            return got

        def play_one(item, d, *a, on_poll=None, **kw):
            if on_poll is None:
                return real_play(item, d, *a, **kw)

            def counted():
                self._line[1] += 1
                try:
                    return on_poll()
                finally:
                    if (item["utt"] == self.pause_in and not self.paused.is_set()
                            and started(env, item["utt"])):
                        self.poll = self._line[1]
                        self.paused.set()
                        self.go.wait(10)

            self._line[:] = [item["utt"], 0]
            try:
                return real_play(item, d, *a, on_poll=counted, **kw)
            finally:
                self._line[:] = [None, 0]

        monkeypatch.setattr(player, "_leapfrog", leapfrog)
        monkeypatch.setattr(player, "play_one", play_one)


# -- file-leapfrogs-lines: the positive cases ------------------------------------

@DUCK
@pytest.mark.needs_ffmpeg
def test_a_tink_behind_two_call_lines_starts_before_the_first_ones_said(env, duck, monkeypatch):
    """README / Q10: the laptop Tink is queued behind two lines for another
    device. It starts at once, before the first line's ``said``, at ``_next``'s
    point (it jumps both lines): before A's first line opens, not at a poll."""
    setting(env, duck)
    pts = Points(env, monkeypatch)
    a1 = say(env, LONG)                                          # device A: null
    a2 = say(env, "the second line for A")
    tink = play(env, seconds=0.3, name="tink.wav", device="dev-b")
    run(env).join(15)
    saying, said, pos = kind(env, "saying"), kind(env, "said"), order(env)
    assert saying[tink["utt"]]["leapfrog"] is True
    assert pts.at.get(tink["utt"]) == (a1["utt"], 0), \
        f"started at {pts.at.get(tink['utt'])}, not _next's point for a1"
    assert pos[("said", tink["utt"])] < pos[("said", a1["utt"])], "the Tink waited for A's line"
    assert [said[x["utt"]]["reason"] for x in (a1, a2, tink)] == ["done"] * 3
    assert pos[("saying", a2["utt"])] > pos[("said", a1["utt"])]    # speech is still one lane


@DUCK
@pytest.mark.needs_ffmpeg
def test_a_tink_queued_while_a_line_plays_starts_within_a_poll(env, duck, monkeypatch):
    """The ``check_stop`` point: the Tink lands while A's first line plays and its
    second is queued. It starts at the line's NEXT poll, not after A's lines.

    Measured in polls, not seconds (EARS-254: a 0.2 s wall-clock bound flaked
    under suite load). A's line is held inside one poll while the Tink is
    queued, so the poll after it is the first to see the Tink, and must start it."""
    setting(env, duck)
    pts = Points(env, monkeypatch)
    a1 = say(env, LONG)
    a2 = say(env, "the second line for A")
    pts.pause_in = a1["utt"]
    th = run(env)
    try:
        assert pts.paused.wait(10), "A's line never polled while it played"
        tink = play(env, seconds=0.3, name="tink.wav", device="dev-b")
    finally:
        pts.go.set()
    th.join(15)
    saying, pos = kind(env, "saying"), order(env)
    assert pts.at.get(tink["utt"]) == (a1["utt"], pts.poll + 1), \
        f"queued after a1's poll {pts.poll}, started at {pts.at.get(tink['utt'])}"
    assert saying[tink["utt"]]["leapfrog"] is True              # it jumped a2, still queued
    assert pos[("said", tink["utt"])] < pos[("said", a1["utt"])]
    assert pos[("saying", a2["utt"])] > pos[("said", a1["utt"])]


@pytest.mark.needs_ffmpeg
def test_a_file_with_nothing_queued_ahead_starts_without_the_leapfrog_flag(env):
    """Queued while a line plays, with no line queued ahead of it: it starts at
    the poll, beside the line, and jumps nothing (``leapfrog`` is absent)."""
    a1 = say(env, LONG)
    th = run(env)
    assert wait_for(lambda: started(env, a1["utt"]))
    tink = play(env, seconds=0.3, name="tink.wav", device="dev-b")
    assert wait_for(lambda: started(env, tink["utt"]), 2.0)
    th.join(15)
    pos = order(env)
    assert "leapfrog" not in kind(env, "saying")[tink["utt"]]
    assert pos[("said", tink["utt"])] < pos[("said", a1["utt"])]


@DUCK
@pytest.mark.needs_ffmpeg
def test_a_held_line_to_its_own_device_does_not_stop_it(env, duck):
    """Rule 3 counts only PLAYABLE lines: a held line for B keeps its place and
    is skipped, as ``_next`` skips it, so the Tink for B still leapfrogs A."""
    setting(env, duck)
    floor_row("partial")                                         # he is mid-turn
    a1 = say(env, LONG, hold="none")    # plays over him: the room default would hold it (EARS-434)
    held = say(env, "after your turn", device="dev-b", hold="turn-end")
    tink = play(env, seconds=0.3, name="tink.wav", device="dev-b")
    th = run(env)
    assert wait_for(lambda: started(env, tink["utt"]), 2.0), "a held line stopped the Tink"
    assert kind(env, "saying")[tink["utt"]]["leapfrog"] is True  # it jumped a1
    assert not started(env, held["utt"])
    floor_row("turn")                                            # his turn ends: the held line goes
    th.join(15)
    said = kind(env, "said")
    assert said[held["utt"]]["reason"] == "done" and said[tink["utt"]]["reason"] == "done"
    assert order(env)[("said", tink["utt"])] < order(env)[("said", a1["utt"])]


@pytest.mark.needs_ffmpeg
def test_files_leapfrog_in_playlist_order_one_bed_per_device(env):
    """Two files for B behind A's line: the first takes B's bed, the second
    waits for it (one bed per device); a file for C starts beside them."""
    a1 = say(env, LONG)
    b1 = play(env, seconds=0.5, name="b1.wav", device="dev-b")
    b2 = play(env, seconds=0.3, name="b2.wav", device="dev-b")
    c1 = play(env, seconds=0.3, name="c1.wav", device="dev-c")
    run(env).join(15)
    saying, pos = kind(env, "saying"), order(env)
    assert saying[b1["utt"]]["leapfrog"] is True and saying[c1["utt"]]["leapfrog"] is True
    assert pos[("saying", b2["utt"])] > pos[("said", b1["utt"])]   # Q3: same device waits
    assert pos[("said", b2["utt"])] < pos[("said", a1["utt"])]     # ...for the bed, not the line


# -- file-leapfrogs-lines: same-device order is kept (negative cases) -----------

@DUCK
@pytest.mark.needs_ffmpeg
def test_a_file_behind_a_line_for_its_own_device_waits_for_that_line(env, duck):
    setting(env, duck)
    a1 = say(env, LONG)
    b_line = say(env, "a line for B", device="dev-b")
    tink = play(env, seconds=0.3, name="tink.wav", device="dev-b")
    run(env).join(20)
    pos, said = order(env), kind(env, "said")
    assert pos[("saying", tink["utt"])] > pos[("said", b_line["utt"])], "the Tink jumped its own device's line"
    assert pos[("saying", b_line["utt"])] > pos[("said", a1["utt"])]
    assert [said[x["utt"]]["reason"] for x in (a1, b_line, tink)] == ["done"] * 3


@DUCK
@pytest.mark.needs_ffmpeg
def test_a_file_for_the_playing_lines_device_waits_for_its_lines(env, duck):
    """Rule 2 (and 3): a file for A waits for A's line playing, and for A's line
    queued ahead of it."""
    setting(env, duck)
    a1 = say(env, "word " * 6)
    a2 = say(env, "the second line for A")
    fa = play(env, seconds=0.3, name="a.wav")                    # device A: null
    th = run(env)
    assert wait_for(lambda: started(env, a1["utt"]))
    th.join(20)
    pos = order(env)
    assert pos[("saying", fa["utt"])] > pos[("said", a2["utt"])]
    assert "leapfrog" not in kind(env, "saying")[fa["utt"]]


@pytest.mark.needs_ffmpeg
def test_a_file_queued_while_a_line_plays_on_its_device_waits(env):
    """The poll's rule 2: queued while A's line plays, a file for A waits for it."""
    a1 = say(env, "word " * 6)
    th = run(env)
    assert wait_for(lambda: started(env, a1["utt"]))
    fa = play(env, seconds=0.3, name="a.wav")
    th.join(15)
    assert order(env)[("saying", fa["utt"])] > order(env)[("said", a1["utt"])]


def test_eligibility_is_read_only_and_follows_the_three_rules(env):
    """``_leapfrog`` itself: it starts B's file and leaves A's (rule 2) and C's
    (rule 3: a playable line for C ahead of it) queued; it files nothing else."""
    a1 = say(env, LONG)
    c_line = say(env, "a line for C", device="dev-c")
    fb = play(env, seconds=0.3, name="b.wav", device="dev-b")
    fa = play(env, seconds=0.3, name="a.wav")
    fc = play(env, seconds=0.3, name="c.wav", device="dev-c")
    q = player._box.Queue(env.d)
    beds = bed.Beds(env.d)
    line = player._box.public(next(x for x in q.lines() if x["utt"] == a1["utt"]))
    got = player._leapfrog(env.d, q, beds, line)
    assert [b.utt for b in got] == [fb["utt"]] and got[0].leapfrog
    left = [x["utt"] for x in q.lines()]
    assert left == [a1["utt"], c_line["utt"], fa["utt"], fc["utt"]]
    assert beds.wait(5)


# -- the start bound while a call line is OPENING (review 1118 input 4) ----------

class SlowBot:
    """A mouth leg that queues a laptop Tink the moment a line's header arrives,
    and answers it ``stall`` seconds later: a file queued while the call line
    is still opening (inside ``open_output``, where no poll runs)."""

    def __init__(self, on_header, stall: float, call: int = 5531) -> None:
        self.dir = tempfile.mkdtemp(prefix="lf-", dir="/tmp")    # AF_UNIX paths are short
        self.path = os.path.join(self.dir, "call-mouth.sock")
        self.call, self.stall, self.on_header = call, stall, on_header
        self.t_header: list = []
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(self.path)
        self.srv.listen(8)
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._one, args=(c,), daemon=True).start()

    def _one(self, c) -> None:
        f = c.makefile("rb")
        hdr = json.loads(f.readline())
        if hdr.get("probe"):
            c.sendall((json.dumps({"ok": True, "agent": "ivy", "calls": [self.call]}) + "\n").encode())
            c.close()
            return
        if not self.t_header:
            self.t_header.append(time.time())
            self.on_header()
            time.sleep(self.stall)
        c.sendall(b'{"ok": true}\n')
        ended = "eof"
        while True:
            head = f.read(5)
            if len(head) < 5:
                break
            k, n = head[:1], struct.unpack(">I", head[1:])[0]
            if n:
                f.read(n)
            if k != b"A":
                ended = {b"D": "done", b"C": "cut"}[k]
                break
        try:
            c.sendall(json.dumps({"ok": ended == "done", "ended": ended}).encode() + b"\n")
        except OSError:
            pass
        c.close()

    def close(self) -> None:
        self.srv.close()


@pytest.mark.parametrize("stall", [0.0, 0.5], ids=["healthy-bot", "slow-bot"])
@pytest.mark.needs_ffmpeg
def test_the_start_bound_while_a_call_line_is_opening(env, monkeypatch, stall):
    """MEASURED (do-006, 2030-06-09): a Tink queued while a call line is
    opening starts when the open returns, at the line's first poll. From the
    line's header reaching the bot to the Tink's ``saying``: 0.025 s with a
    bot that answers at once (inside the ~0.1 s poll bound), 0.542 s with a
    bot that answers 0.5 s late (its delay, plus ~0.04 s, mostly the bed's
    decode starting). The poll cannot run inside ``open_output``, so the bound
    there is the open's own: up to ``CALL_LOOK_S`` (1 s) per socket probe plus
    1 s for the header (design.md s2, Known limits).

    The upper bound is in POLL UNITS, not wall clock (EARS-323, the shape
    EARS-254 took out of the two tests above): the Tink is queued inside
    ``open_output``, where no poll runs, so the line's FIRST poll after the
    open returns (``Points`` poll 1) is the one that must start it, however
    loaded the box is. The lower bound stays in seconds: the open cannot
    return before the bot answers, so ``stall <= took`` is a causal floor,
    not a race."""
    tink: dict = {}
    path = tone(env, "tink.wav", 0.3)

    def queue_tink():
        tink.update(mouth.play(str(path), spawn=False, d=env.d, device="null"))

    pts = Points(env, monkeypatch)
    b = SlowBot(queue_tink, stall)
    monkeypatch.setenv("MOUTH_CALL_SOCKS", b.path)
    try:
        line = say(env, "word " * 6, device="call:5531")
        th = run(env)
        assert wait_for(lambda: bool(tink) and started(env, tink["utt"]), 5.0)   # a hang guard only
        th.join(15)
    finally:
        b.close()
    saying, pos = kind(env, "saying"), order(env)
    took = ts(saying[tink["utt"]]) - b.t_header[0]
    print(f"\nleapfrog start while a call line opens (stall {stall:.1f}s): {took:.3f}s, "
          f"at {pts.at.get(tink['utt'])}")
    assert pts.at.get(tink["utt"]) == (line["utt"], 1), \
        f"queued inside the open, started at {pts.at.get(tink['utt'])}, not the line's first poll"
    assert stall <= took, f"{took:.3f}s: the Tink started before the open returned"
    assert pos[("said", tink["utt"])] < pos[("said", line["utt"])]


# -- review 2030-06-09T1845 (worker 832bd610): the poll's own costs and receipts ----

@pytest.mark.needs_ffmpeg
def test_a_mailed_file_started_by_the_poll_keeps_its_queued_root(env, monkeypatch, tmp_path):
    """The poll reads the queue read-only, so before it takes a mailed file it
    acks it the way ``lines(act=True)`` would: the file's maillog thread opens
    with ``queued``, then ``saying`` and ``said``, all under the request mail."""
    import email
    import email.policy

    from test_mouth_bed import deliver

    log = tmp_path / "mlog"
    for s in ("new", "cur", "tmp"):
        (log / s).mkdir(parents=True)
    monkeypatch.setenv("MOUTH_MAIL_LOG", str(log))
    a1 = say(env, LONG)
    th = run(env)
    assert wait_for(lambda: started(env, a1["utt"]))
    tink_wav = tone(env, "tink.wav", 0.3)
    monkeypatch.setenv("MOUTH_SOUNDS_DIR", str(tink_wav.parent))   # a mail's sound (EARS-335)
    mid = deliver(env, "", X_Mouth_File=str(tink_wav), X_Mouth_Device="dev-b")
    th.join(15)
    mine = []
    for f in sorted((log / "cur").iterdir()):
        m = email.message_from_bytes(f.read_bytes(), policy=email.policy.default)
        if m.get("X-Mouth-Log") and m["In-Reply-To"] == mid:
            mine.append(m["X-Mouth-Log"])
    assert [k for k in mine if k not in ("duck", "unduck")] == ["queued", "saying", "said"]
    tink = next(r["utt"] for r in rows(env) if r["kind"] == "saying" and r.get("device") == "dev-b")
    pos = order(env)
    assert pos[("said", tink)] < pos[("said", a1["utt"])], "the mailed Tink waited for A's line"


def test_the_poll_looks_up_no_device_when_no_file_is_queued(env, monkeypatch):
    """The poll runs on the line's write path every 100 ms. With no file queued
    it has nothing to start, so it must not pay ``device_key`` (a ``call`` or
    ``default`` key can probe a socket or CoreAudio on a cache miss)."""
    say(env, LONG)
    say(env, "a line for C", device="dev-c")
    q = player._box.Queue(env.d)
    beds = bed.Beds(env.d)
    calls: list = []
    monkeypatch.setattr(bed, "device_key", lambda raw: calls.append(raw) or raw)
    playing = player._box.public(q.lines()[0])
    assert player._leapfrog(env.d, q, beds, playing) == []
    assert calls == []
