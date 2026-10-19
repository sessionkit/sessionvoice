"""EARS-295 do-005, T7: the DJ (mpv) dip asks the same gate as the bed.

design.md s3, s6 and super ruling Q2: ``music_duck`` and a ``mouth duck``
override gate the DJ dip too. ``_duck_on(item)`` asks ``duckswitch.decide``:
the bed's first frame passes its own item (its ``--duck``/``--no-duck`` beats
the override), a line's first frame passes None (the override, then the host).
A dip the gate stops writes ONE ``duck-skipped`` row, ``lane: dj``, per run.

The DJ volume is ONE shared state with two kinds of caller (design-001 review
build note). Pinned here: the dip's HOLDERS win. Every caller that lowered the
DJ or joined its dip holds it; a no lifts the DJ only if no holder would dip
it now.

A fake ``mouth.mpv.Mpv`` stands in for the DJ's mpv client (EARS-293), and
never touches a live mpv.
"""

from __future__ import annotations

import json
from typing import ClassVar, Optional

import pytest
from test_mouth_bed import env, play, rows, run, say, started, wait_for  # noqa: F401 - fixtures

import mouth
from mouth import duckswitch, mpv, player


class FakeDJ:
    """The DJ's mpv: a volume, and every level it was set to."""
    vol: Optional[int] = 70
    calls: ClassVar[list] = []

    def volume(self, level=None):
        if level is not None:
            FakeDJ.vol = level
            FakeDJ.calls.append(level)
        return FakeDJ.vol


@pytest.fixture
def dj(env, monkeypatch):
    """A fake DJ at 70, ducking to 20, and a fresh duck state."""
    monkeypatch.setattr(mpv, "Mpv", FakeDJ)
    monkeypatch.setattr(FakeDJ, "vol", 70)
    monkeypatch.setattr(FakeDJ, "calls", [])
    monkeypatch.setattr(player, "_duck", {"prev": None, "ctl": None, "holders": [], "skipped": False})
    monkeypatch.setenv("MOUTH_DUCK", "20")
    return FakeDJ


def settings(env, **kw) -> None:
    env.settings.write_text(json.dumps(kw))


def skips(env) -> list:
    return [(r["lane"], r["why"], r["by"]) for r in rows(env)
            if r["kind"] == "event" and r.get("event") == "duck-skipped" and r.get("lane") == "dj"]


def queued(env, n: int = 1) -> list:
    """The last N queued items, as the player sees them."""
    return [mouth.box.public(x) for x in mouth.box.Queue(env.d).lines()[-n:]]


# -- the line's first frame: the override, then the host ----------------------------

def test_t7_setting_on_lowers_the_dj_once_per_run_and_restores_it(env, dj):
    settings(env, music_duck=True)
    say(env, "one"), say(env, "two")
    run(env, idle=0.5).join(8)
    assert dj.calls == [20, 70]                      # down once for both lines, back up once
    assert skips(env) == []


@pytest.mark.parametrize("how", ["setting", "override"])
def test_t7_ducking_off_never_lowers_the_dj_and_one_row_per_run_names_by(env, dj, how):
    settings(env, music_duck=how == "override")          # the override beats a setting of on
    if how == "override":
        duckswitch.set(env.d, on=False, for_s=600)
    say(env, "one"), say(env, "two"), say(env, "three")
    run(env, idle=0.5).join(8)
    assert dj.calls == []
    assert skips(env) == [("dj", "say", "settings" if how == "setting" else "override")]


def test_t7_one_row_per_run_a_second_run_writes_another(env, dj):
    settings(env, music_duck=False)
    say(env, "one")
    run(env, idle=0.3).join(8)
    say(env, "two")
    run(env, idle=0.3).join(8)
    assert skips(env) == [("dj", "say", "settings")] * 2


def test_t7_no_dj_playing_is_no_skipped_dip(env, dj, monkeypatch):
    monkeypatch.setattr(FakeDJ, "vol", None)                   # no mpv: nothing to dip
    settings(env, music_duck=False)
    say(env, "one")
    run(env, idle=0.3).join(8)
    assert dj.calls == [] and skips(env) == []


def test_t7_an_override_set_while_lines_run_lifts_the_dj_at_the_next_line(env, dj):
    settings(env, music_duck=True)
    say(env, "one"), say(env, "two")
    one, two = queued(env, 2)
    player._duck_on(None, env.d, line=one)
    assert dj.calls == [20]
    duckswitch.set(env.d, on=False, for_s=600)
    player._duck_on(None, env.d, line=two)
    assert dj.calls == [20, 70]                             # up at the next first frame
    player._duck_on(None, env.d, line=two)
    assert skips(env) == [("dj", "say", "override")]         # the lifted dip: one row


# -- the bed's first frame: its own argument beats the override ---------------------

def test_t7_a_no_duck_file_leaves_the_dj_alone_with_the_setting_on(env, dj):
    settings(env, music_duck=True)
    play(env, seconds=0.3, duck=False)
    (f,) = queued(env)
    player._duck_on(f, env.d)
    assert dj.calls == []
    assert skips(env) == [("dj", "play", "argument")]


def test_t7_a_duck_file_lowers_the_dj_under_an_override_of_off(env, dj):
    duckswitch.set(env.d, on=False, for_s=600)
    play(env, seconds=0.3, duck=True)
    (f,) = queued(env)
    player._duck_on(f, env.d)
    assert dj.calls == [20] and skips(env) == []


def test_t7_the_latch_never_blocks_a_dip_the_gate_allows(env, dj):
    settings(env, music_duck=False)
    say(env, "one")
    play(env, seconds=0.3, duck=True)
    line, f = queued(env, 2)
    player._duck_on(None, env.d, line=line)                 # skipped: one row
    player._duck_on(f, env.d)                               # --duck: it dips anyway
    assert dj.calls == [20]
    assert skips(env) == [("dj", "say", "settings")]


# -- PINNED: who wins the one DJ volume when a bed and a line disagree ---------------

def test_t7_pinned_a_duck_files_dip_holds_while_lines_under_the_override_play(env, dj):
    """A holder wins. A ``--duck`` file lowered the DJ under ``mouth duck off``;
    a spoken line (gate no) does not raise it, and writes no row: nothing was
    skipped, the DJ is down. The queue going quiet restores it, as today."""
    duckswitch.set(env.d, on=False, for_s=600)
    play(env, seconds=0.3, duck=True)
    say(env, "over the music")
    f, line = queued(env, 2)
    player._duck_on(f, env.d)
    player._duck_on(None, env.d, line=line)
    assert dj.calls == [20] and skips(env) == []
    player._duck_off()
    assert dj.calls == [20, 70]


def test_t7_pinned_a_no_duck_file_never_raises_a_dj_that_lines_lowered(env, dj):
    settings(env, music_duck=True)
    say(env, "a line")
    play(env, seconds=0.3, duck=False)
    line, f = queued(env, 2)
    player._duck_on(None, env.d, line=line)
    player._duck_on(f, env.d)
    assert dj.calls == [20] and skips(env) == []


def test_t7_pinned_a_cascade_files_dip_is_lifted_by_the_override(env, dj):
    """A holder that no longer dips lets it go: a file stamped from the setting
    lowered the DJ; once ``mouth duck off`` is live, the next line lifts it."""
    settings(env, music_duck=True)
    play(env, seconds=0.3)
    say(env, "a line")
    f, line = queued(env, 2)
    player._duck_on(f, env.d)
    duckswitch.set(env.d, on=False, for_s=600)
    player._duck_on(None, env.d, line=line)
    assert dj.calls == [20, 70]
    assert skips(env) == [("dj", "say", "override")]


@pytest.mark.needs_ffmpeg
def test_t7_end_to_end_a_duck_file_holds_the_dj_down_under_a_line(env, dj):
    """Both call sites wired: the bed's first frame passes its item, the line's
    passes None. ``--duck`` under the override: down at the music, kept under
    the line, up once the player is quiet."""
    duckswitch.set(env.d, on=False, for_s=600)
    f = play(env, seconds=1.5, duck=True)
    th = run(env, idle=0.5)
    assert wait_for(lambda: started(env, f["utt"]))
    say(env, "a line over it")
    th.join(10)
    assert dj.calls == [20, 70]
    assert skips(env) == []


def test_t7_a_gate_that_cannot_answer_leaves_todays_dj_dip(env, dj, monkeypatch):
    def broken(*a, **k):
        raise OSError("duck.json unreadable")
    monkeypatch.setattr(duckswitch, "decide", broken)
    say(env, "one")
    play(env, seconds=0.3, duck=False)
    line, f = queued(env, 2)
    player._duck_on(f, env.d)                               # its stamp: no
    assert dj.calls == [] and skips(env) == [("dj", "play", "argument")]
    player._duck_on(None, env.d, line=line)                 # a line: today's dip
    assert dj.calls == [20]


def test_t7_pinned_a_duck_file_that_joins_a_lines_dip_holds_it_under_the_override(env, dj):
    """The order flipped: a LINE lowered the DJ first, then a ``--duck`` file
    started while it was down. Its ``--duck`` still beats the override (design
    s3: argument > override on every path), so ``mouth duck off`` and the next
    line do not raise the DJ under it."""
    settings(env, music_duck=True)
    say(env, "first")
    play(env, seconds=0.3, duck=True)
    say(env, "second")
    one, f, two = queued(env, 3)
    player._duck_on(None, env.d, line=one)                  # the line lowers it
    player._duck_on(f, env.d)                               # --duck joins a dip already down
    duckswitch.set(env.d, on=False, for_s=600)
    player._duck_on(None, env.d, line=two)
    assert dj.calls == [20] and skips(env) == []


def test_t7_pinned_a_cascade_file_that_joins_a_lines_dip_lets_the_override_lift_it(env, dj):
    """Joining is not a veto: a file stamped from the setting joins the lines'
    dip, and both follow ``mouth duck off``, so the next line lifts the DJ."""
    settings(env, music_duck=True)
    say(env, "first")
    play(env, seconds=0.3)
    say(env, "second")
    one, f, two = queued(env, 3)
    player._duck_on(None, env.d, line=one)
    player._duck_on(f, env.d)
    duckswitch.set(env.d, on=False, for_s=600)
    player._duck_on(None, env.d, line=two)
    assert dj.calls == [20, 70]
    assert skips(env) == [("dj", "say", "override")]
