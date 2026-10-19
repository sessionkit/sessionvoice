"""EARS-315 verify-001: ``bench/duck_latency.py``'s ``callout`` mode times the BUILD.

The bench's ``call`` mode is the design's model of the call duck (a fake
writer). ``callout`` drives a real :class:`mouth.output.CallOut` into a fake
bot on a Unix socket, with the duck ``onset.for_line`` builds for a
``call:N`` line. These pin that it measures what it says: the duck bites in
``CallOut.write``, the bot receives the scaled PCM, the record carries the
paced lead as ``ahead_ms``, and each run's line starts with no duck. No timing is
asserted beyond a loose ceiling: the figures are the bench's job, not a test's.
"""
from __future__ import annotations

import pytest

from bench import duck_latency as dl
from tests import env_fence


@pytest.fixture
def bot(tmp_path, monkeypatch):
    monkeypatch.setenv("MOUTH_DIR", str(tmp_path / "mouth"))
    monkeypatch.setenv("MOUTH_SETTINGS", str(tmp_path / "no-settings.json"))
    for k in ("MOUTH_BARGE", "MOUTH_BARGE_WORDS",
              "MOUTH_BARGE_WORDS_CALL"):
        env_fence.delenv(monkeypatch, k)   # both names (EARS-294)
    b = dl.FakeBot()
    monkeypatch.setenv("MOUTH_CALL_SOCKS", b.path)
    yield b
    b.close()


def test_a_callout_run_ducks_the_pcm_the_bot_receives(tmp_path, bot):
    r = dl.one_callout_run(tmp_path, bot, 24000)
    assert r["ok"], r
    assert r["level"] == 0.25                       # the built-in barge_duck_level, 25
    assert r["started_ducked"] is False
    # the duck bit in CallOut.write, and the bot got a scaled frame from it
    assert r["bot_ms"] is not None and r["bot_ms"] >= r["total_ms"] - 1
    assert r["total_ms"] < 1000
    # the paced lead past the duck: up to CallOut.latency (0.2 s), never more than a block over
    assert 0 <= r["ahead_ms"] <= 1000 * (dl.output.CallOut.latency + dl.backends.BLOCK_S)
    assert r["head_ms"] == pytest.approx(r["total_ms"] + r["ahead_ms"], abs=0.01)
    # the frames before the row went out at full level: nothing ducked them
    full = int(dl.LEVEL * 32767)
    assert any(peak >= full - 2 for _, peak in bot.frames)


def test_each_callout_run_is_a_fresh_line_not_started_ducked(tmp_path, bot):
    runs = [dl.one_callout_run(tmp_path, bot, 24000) for _ in range(2)]
    assert all(r["ok"] for r in runs), runs
    assert [r["started_ducked"] for r in runs] == [False, False]   # the offset row closed run 1


def test_the_summary_reports_the_call_fields():
    runs = [{"ok": True, "dip_ms": 7, "applied_ms": 20, "ahead_ms": 190, "total_ms": 27.0,
             "bot_ms": 27.1, "head_ms": 217.0}, {"ok": False}]
    s = dl.summary(runs)
    assert s["runs"] == 2 and s["ok"] == 1
    assert s["head_ms"]["p50"] == 217.0 and s["bot_ms"]["max"] == 27.1 and s["ahead_ms"]["p90"] == 190
