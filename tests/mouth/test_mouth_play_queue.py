"""``mouth play``'s queue modes: ``play_queue`` = now | next | last, and the flags.

design.md 2.3 (queue modes), 2.1 (the setting), 4 (this file's rows). Each mode
is checked against the player's own records: the heard log's ``saying`` and
``said`` rows, ordered by their ``seq``, never by the clock. A song that must
still be playing when the next play arrives is held at its end
(``held_at_the_end``) until the test has looked, so a slow host stretches a
step without breaking the check.

- ``last`` (the default): the end of the queue, today's plain play.
- ``next``: the front of the queue (rank 0); plays when the song on its device ends.
- ``now``: the song playing on THAT device only is stopped (a stop aimed at its
  utt, ``reason=interrupted``) and this plays at once. A spoken line is never cut.
- A run (``play_many``) is placed as one block, in order, by one mode.
"""

from __future__ import annotations

import json
import math

import pytest

import mouth
from mouth import __main__ as cli
from mouth import bed, voices
from mouth.box import Queue

from test_mouth_bed import (env, held_at_the_end, kind, run, say, seq_order,  # noqa: F401 - fixtures
                            started, tone, wait_for)


def song(env, name, seconds=0.3, **kw) -> dict:
    """Queue a short tone on dev-a (unless a device is named), as ``mouth play`` would."""
    kw.setdefault("device", "dev-a")
    return mouth.play(str(tone(env, name, seconds)), spawn=False, d=env.d, **kw)


def run_of(env, names, **kw) -> list:
    kw.setdefault("device", "dev-a")
    return mouth.play_many([str(tone(env, n, 0.3)) for n in names], spawn=False, d=env.d, **kw)


def on_bed(env, utt) -> bool:
    """The song is in ``bed.json``: the player's own record that it is playing."""
    return any(b.get("utt") == utt for b in bed.read(env.d))


def playing(env, x) -> bool:
    return started(env, x["utt"]) and on_bed(env, x["utt"])


@pytest.fixture
def stops(monkeypatch):
    """Every stop record written, in order (the player still reads each one)."""
    seen, real = [], mouth._stop_file

    def rec(d, r):
        seen.append(dict(r))
        return real(d, r)

    monkeypatch.setattr(mouth, "_stop_file", rec)
    return seen


def said(env, x) -> dict:
    return kind(env, "said")[x["utt"]]


def ends(env, x) -> tuple:
    r = said(env, x)
    return r["reason"], r["cut"]


# -- last: the end of the queue (the default, and --last) ------------------------------

@pytest.mark.parametrize("flag", [None, "last"], ids=["default", "last"])
def test_last_joins_the_end_of_the_queue(env, flag):
    a = song(env, "a.wav")
    x = song(env, "x.wav")
    b = song(env, "b.wav", queue=flag)
    run(env).join(10)
    pos = seq_order(env)
    assert pos[("said", a["utt"])] < pos[("saying", x["utt"])] < pos[("said", x["utt"])] \
        < pos[("saying", b["utt"])]
    assert all(ends(env, s) == ("done", False) for s in (a, x, b))
    assert "queue" not in a and b.get("queue") == flag   # the built-in rides on nothing


# -- next: the front of the queue ------------------------------------------------------

def test_next_plays_when_the_song_on_its_device_ends(env, monkeypatch):
    release = held_at_the_end(monkeypatch)
    a = song(env, "a.wav")
    th = run(env)
    try:
        assert wait_for(lambda: playing(env, a))
        x = song(env, "x.wav")
        b = song(env, "b.wav", queue="next")
        assert a["utt"] not in kind(env, "said"), "a ended before b was queued: the check proved nothing"
    finally:
        release.set()
    th.join(10)
    pos = seq_order(env)
    assert pos[("said", a["utt"])] < pos[("saying", b["utt"])] < pos[("saying", x["utt"])]
    assert ends(env, a) == ("done", False)               # next never cuts the song playing
    assert (b["priority"], b["queue"], b["queue_from"]) == ("next", "next", "flag")


# -- now: stop the song on THAT device, play at once -----------------------------------

def test_now_stops_the_song_on_its_device_and_plays_at_once(env, monkeypatch, stops):
    release = held_at_the_end(monkeypatch)
    a = song(env, "a.wav")
    x = song(env, "x.wav")                                # a plain play, queued behind a
    th = run(env)
    try:
        assert wait_for(lambda: playing(env, a))
        b = song(env, "b.wav", queue="now")
        assert wait_for(lambda: started(env, b["utt"]), 10), "the now play never started"
    finally:
        release.set()
    th.join(10)
    assert ends(env, a) == ("interrupted", True)
    pos = seq_order(env)
    assert pos[("said", a["utt"])] < pos[("saying", b["utt"])] < pos[("saying", x["utt"])]
    assert ends(env, b) == ends(env, x) == ("done", False)
    assert stops == [{"t": stops[0]["t"], "reason": "interrupted", "flush": False, "utt": a["utt"]}]
    assert -1.0 < b["priority"] <= mouth.NOW_SONG_RANK and b["queue"] == "now"


def test_now_leaves_the_song_on_another_device_alone(env, monkeypatch, stops):
    release = held_at_the_end(monkeypatch)
    c = song(env, "c.wav", device="dev-b")
    a = song(env, "a.wav")
    th = run(env)
    try:
        assert wait_for(lambda: playing(env, a) and playing(env, c))
        b = song(env, "b.wav", queue="now")
        assert wait_for(lambda: started(env, b["utt"]), 10)
        assert c["utt"] not in kind(env, "said"), "c ended before the now: the check proved nothing"
    finally:
        release.set()
    th.join(10)
    assert ends(env, a) == ("interrupted", True)
    assert ends(env, c) == ("done", False)
    assert [s["utt"] for s in stops] == [a["utt"]]


def test_now_never_cuts_a_spoken_line(env, stops):
    """Speech mixes over songs in every mode: a ``now`` play no longer cuts the
    line playing, as ``mouth play --now`` once did (``priority=now``)."""
    line = say(env, "word " * 12, device="dev-a")          # ~4 s on the silence backend
    th = run(env)
    assert wait_for(lambda: started(env, line["utt"]))
    b = song(env, "b.wav", queue="now")
    cur = mouth._playing(env.d)
    assert cur and cur["utt"] == line["utt"], "the line ended before the now: the check proved nothing"
    th.join(15)
    assert ends(env, line) == ("done", False)
    assert ends(env, b) == ("done", False)
    assert stops == []                                     # no song played, so nothing was stopped


def test_now_with_nothing_playing_plays_and_stops_nothing(env, stops):
    th = run(env)
    b = song(env, "b.wav", queue="now")
    th.join(10)
    assert ends(env, b) == ("done", False)
    assert stops == []


def test_now_with_no_player_reads_no_stale_bed(env, stops):
    """``bed.json`` outlives a player that died: with none running, no stop is aimed."""
    env.d.mkdir(parents=True, exist_ok=True)
    (env.d / bed.BED_JSON).write_text(json.dumps([{"utt": "deadbeef0000", "key": "dev-a",
                                                   "device": "dev-a"}]))
    song(env, "b.wav", queue="now")
    assert stops == []


# -- runs: one block, in order, by one mode --------------------------------------------

def test_a_run_under_now_cuts_the_song_once_and_plays_in_order(env, monkeypatch, stops):
    release = held_at_the_end(monkeypatch)
    a = song(env, "a.wav")
    th = run(env)
    try:
        assert wait_for(lambda: playing(env, a))
        m1, m2, m3 = run_of(env, ["m1.wav", "m2.wav", "m3.wav"], queue="now")
        assert wait_for(lambda: started(env, m1["utt"]), 10)
    finally:
        release.set()
    th.join(15)
    assert [s["utt"] for s in stops] == [a["utt"]]       # ONE stop, aimed at a
    assert ends(env, a) == ("interrupted", True)
    pos = seq_order(env)
    assert pos[("said", a["utt"])] < pos[("saying", m1["utt"])] < pos[("saying", m2["utt"])] \
        < pos[("saying", m3["utt"])]
    assert all(ends(env, m) == ("done", False) for m in (m1, m2, m3))   # never cut by its own run
    assert m1["priority"] == m2["priority"] == m3["priority"]          # one rank: FIFO


def test_a_second_now_plays_before_an_earlier_now_runs_remainder(env, monkeypatch, stops):
    """The older run's queued songs stay queued and play after the new one, in order."""
    release = held_at_the_end(monkeypatch)
    th = run(env)
    try:
        m1, m2, m3 = run_of(env, ["m1.wav", "m2.wav", "m3.wav"], queue="now")
        assert wait_for(lambda: playing(env, m1))
        x = song(env, "x.wav", queue="now")
        assert wait_for(lambda: started(env, x["utt"]), 10)
    finally:
        release.set()
    th.join(15)
    assert ends(env, m1) == ("interrupted", True)
    assert [s["utt"] for s in stops] == [m1["utt"]]
    pos = seq_order(env)
    assert pos[("said", m1["utt"])] < pos[("saying", x["utt"])] < pos[("saying", m2["utt"])] \
        < pos[("saying", m3["utt"])]
    assert x["priority"] < m2["priority"]
    assert all(ends(env, s) == ("done", False) for s in (x, m2, m3))


def test_a_run_under_next_sits_ahead_of_an_earlier_plain_play(env, monkeypatch):
    release = held_at_the_end(monkeypatch)
    a = song(env, "a.wav")
    th = run(env)
    try:
        assert wait_for(lambda: playing(env, a))
        x = song(env, "x.wav")
        n1, n2 = run_of(env, ["n1.wav", "n2.wav"], queue="next")
    finally:
        release.set()
    th.join(15)
    pos = seq_order(env)
    assert pos[("said", a["utt"])] < pos[("saying", n1["utt"])] < pos[("saying", n2["utt"])] \
        < pos[("saying", x["utt"])]
    assert ends(env, a) == ("done", False)


def test_a_next_plays_after_a_now_runs_remainder(env, monkeypatch):
    """A ``now`` run is one block: a ``--next`` dropped while its remainder is
    queued plays after it, never between its songs. Two nexts stay FIFO."""
    release = held_at_the_end(monkeypatch)
    th = run(env)
    try:
        m1, m2, m3 = run_of(env, ["m1.wav", "m2.wav", "m3.wav"], queue="now")
        assert wait_for(lambda: playing(env, m1))
        x1 = song(env, "x1.wav", queue="next")
        x2 = song(env, "x2.wav", queue="next")
        assert m1["utt"] not in kind(env, "said"), "m1 ended early: the check proved nothing"
    finally:
        release.set()
    th.join(15)
    pos = seq_order(env)
    assert pos[("saying", m1["utt"])] < pos[("saying", m2["utt"])] < pos[("saying", m3["utt"])] \
        < pos[("saying", x1["utt"])] < pos[("saying", x2["utt"])]
    assert ends(env, m1) == ("done", False)               # a next never cuts


def test_a_run_skips_a_source_that_cannot_be_prepared(env):
    seen = []
    good = str(tone(env, "g.wav", 0.3))
    out = mouth.play_many([str(env.tmp / "gone.wav"), good], spawn=False, d=env.d,
                          device="dev-a", skip=lambda src, e: seen.append((src, str(e))))
    assert [x["file"] for x in out] == [good]
    assert seen == [(str(env.tmp / "gone.wav"), f"mouth: no such file {env.tmp / 'gone.wav'}")]
    with pytest.raises(ValueError, match="nothing to play"):
        mouth.play_many([str(env.tmp / "gone.wav")], spawn=False, d=env.d, skip=lambda *a: None)
    assert len(Queue(env.d).lines()) == 1


def nothing_queued(env) -> bool:
    return not env.d.exists() or Queue(env.d).lines() == []


def test_a_run_is_prepared_whole_before_anything_is_queued(env):
    """With no ``skip``, a bad source is refused before its good neighbours drop."""
    with pytest.raises(ValueError, match="no such file"):
        mouth.play_many([str(tone(env, "ok.wav", 0.3)), str(env.tmp / "gone.wav")],
                        spawn=False, d=env.d)
    assert nothing_queued(env)


def test_wait_plays_one(env):
    with pytest.raises(ValueError, match="--wait plays one"):
        run_of(env, ["a.wav", "b.wav"], wait=True)
    assert nothing_queued(env)


# -- the rank of a now play ------------------------------------------------------------

def line(rank, device="dev-a", file="/x.wav"):
    return {"_rank": rank, "device": device, **({"file": file} if file else {})}


def test_the_now_rank_is_below_every_queued_now_band_rank_on_its_device():
    assert mouth.now_rank([], "dev-a") == mouth.NOW_SONG_RANK == -0.5
    assert mouth.now_rank([line(-0.5)], "dev-a") == -0.75
    assert mouth.now_rank([line(-0.5), line(-0.75)], "dev-a") == -0.875
    # outside the band, another device, or a spoken line: not counted
    assert mouth.now_rank([line(-1.0), line(0.0), line(50.0), line(-0.75, "dev-b"),
                           line(-0.75, file=None)], "dev-a") == -0.5
    assert mouth.now_rank([line(-0.6, "DEV-A")], "dev-a") == pytest.approx(-0.8)   # keyed as the player keys


def test_the_now_rank_never_reaches_minus_one():
    r, lines = mouth.NOW_SONG_RANK, []
    for _ in range(80):
        lines.append(line(r))
        nxt = mouth.now_rank(lines, "dev-a")
        assert -1.0 < nxt <= r
        r = nxt
    assert r == mouth.NOW_RANK_FLOOR == math.nextafter(-1.0, 0.0)


def test_the_now_rank_round_trips_through_the_box(env):
    """The rank rides ``X-Mouth-Priority`` as a number and the queue reads it back."""
    one = song(env, "one.wav", queue="now")
    two = song(env, "two.wav", queue="now")
    other = song(env, "other.wav", queue="now", device="dev-b")
    ranks = {x["utt"]: x["_rank"] for x in Queue(env.d).lines()}
    assert ranks == {one["utt"]: -0.5, two["utt"]: -0.75, other["utt"]: -0.5}
    assert [x["utt"] for x in Queue(env.d).lines()][:2] == [two["utt"], one["utt"]]


# -- the setting, the flag, the refusals -----------------------------------------------

def host(env, **kw):
    env.settings.write_text(json.dumps(kw))


def test_the_setting_now_acts_as_now(env, monkeypatch, stops):
    host(env, play_queue="now")
    release = held_at_the_end(monkeypatch)
    a = song(env, "a.wav", queue="last")                  # the flag beats the setting
    th = run(env)
    try:
        assert wait_for(lambda: playing(env, a))
        b = song(env, "b.wav")
        assert wait_for(lambda: started(env, b["utt"]), 10)
    finally:
        release.set()
    th.join(10)
    assert ends(env, a) == ("interrupted", True)
    assert (a["queue"], a["queue_from"], a["priority"]) == ("last", "flag", None)
    assert (b["queue"], b["queue_from"]) == ("now", "settings")


def test_a_flag_beats_the_setting(env):
    host(env, play_queue="next")
    assert song(env, "a.wav")["priority"] == "next"
    b = song(env, "b.wav", queue="last")
    assert (b["priority"], b["queue_from"]) == (None, "flag")


@pytest.mark.parametrize("given, want", [
    ({}, ("last", "builtin")),
    ({"play_queue": "next"}, ("next", "settings")),
    ({"play_queue": " NOW "}, ("now", "settings")),
    ({"play_queue": "replace"}, ("last", "builtin")),     # a bad value is skipped
    ({"play_queue": True}, ("last", "builtin")),
])
def test_the_play_queue_setting(env, given, want):
    host(env, **given)
    assert voices.play_mode_settings({}, env.d)["play_queue"] == want


def test_the_play_queue_setting_is_layered_and_lockable(env):
    host(env, play_queue="last")
    seat = {"play_queue": ("now", "seat")}
    assert voices.play_mode_settings(seat, env.d)["play_queue"] == ("now", "seat")
    assert voices.play_mode_settings({"play_queue": ("bad", "seat")}, env.d)["play_queue"] \
        == ("last", "settings")
    host(env, play_queue="last", _locked=["play_queue"])
    assert voices.play_mode_settings(seat, env.d)["play_queue"] == ("last", "settings")
    host(env)
    locked = {**seat, voices.HOST_LOCKS: (("play_queue",), "host")}
    assert voices.play_mode_settings(locked, env.d)["play_queue"] == ("last", "builtin")


def test_the_cascade_is_read_only_when_no_flag_answers(env, monkeypatch):
    """``play_queue`` joins the line's ONE cascade read, and only when no flag answers."""
    asked = []
    monkeypatch.setattr(voices, "cascade", lambda sid, keys: asked.append(tuple(keys)) or {})
    song(env, "a.wav", queue="next")
    assert asked == [voices.LINE_KEYS]
    asked.clear()
    song(env, "b.wav")
    assert asked == [voices.LINE_KEYS + ("play_queue",)]


def test_a_run_reads_the_cascade_once_for_its_mode_and_every_line(env, monkeypatch):
    asked = []
    monkeypatch.setattr(voices, "cascade", lambda sid, keys: asked.append(tuple(keys)) or {
        "play_queue": ("next", "seat"), "play_volume": (40, "seat")})
    out = run_of(env, ["a.wav", "b.wav", "c.wav"])
    assert asked == [voices.play_keys(queue=True)]
    assert [(x["queue"], x["queue_from"], x["volume"], x["volume_from"]) for x in out] \
        == [("next", "seat", 40, "seat")] * 3


def test_queue_and_priority_together_are_refused(env):
    with pytest.raises(ValueError, match="not both"):
        song(env, "a.wav", queue="now", priority="next")
    with pytest.raises(ValueError, match="queue is now, next, last"):
        song(env, "a.wav", queue="soon")
    assert nothing_queued(env)


def test_priority_alone_keeps_its_raw_meaning(env):
    a = song(env, "a.wav", priority=10)
    assert a["priority"] == 10 and "queue" not in a


# -- the CLI: --now, --next, --last ----------------------------------------------------

@pytest.mark.parametrize("argv, want", [
    ([], None), (["--now"], "now"), (["--next"], "next"), (["--last"], "last"),
])
def test_the_cli_flags_set_the_queue_mode(env, monkeypatch, capsys, argv, want):
    got = {}
    monkeypatch.setattr(cli, "_can_play", lambda device: True)
    monkeypatch.setattr(cli, "play", lambda source, **kw: got.update(kw) or {"utt": "u1"})
    assert cli.main(["play", "song.wav", *argv]) == 0
    assert got["queue"] == want and "priority" not in got
    assert capsys.readouterr().out.strip() == "u1"


@pytest.mark.parametrize("pair", [["--now", "--next"], ["--next", "--last"], ["--now", "--last"]])
def test_the_cli_queue_flags_are_one_or_another(env, capsys, pair):
    with pytest.raises(SystemExit) as e:
        cli.main(["play", "song.wav", *pair])
    assert e.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err
