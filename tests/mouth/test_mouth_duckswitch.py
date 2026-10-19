"""EARS-295 do-001: ducking's switch (design.md s2-s4). Pure: no audio, no player.

T3: a timed override, driven by a fake clock, expires at ``until``; the
setting answers again with nothing restarted, and ``state()`` is None after.
T2: ``decide()``'s order: argument > override > the music_duck cascade
(session, seat, agent, settings) > built-in on, each answer naming its ``by``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from mouth import duckswitch
from tests import env_fence


@pytest.fixture
def d(tmp_path, monkeypatch) -> Path:
    """A mouth dir whose settings file is its own ``settings.json``."""
    m = tmp_path / "mouth"
    m.mkdir()
    env_fence.delenv(monkeypatch, "MOUTH_SETTINGS")
    return m


def host(d: Path, **kw) -> None:
    (d / "settings.json").write_text(json.dumps(kw))


def item(on, frm):
    """A played file's item, its duck stamped as enqueue stamps it."""
    return {"utt": "u1", "file": "/x.mp3", "duck": {"on": on, "level": 20, "restore_s": 1.0, "from": frm}}


# -- T3: the timed override ----------------------------------------------------

def test_t3_override_expires_at_until_and_the_setting_answers_again(d):
    it = item(True, "settings")
    rec = duckswitch.set(d, on=False, for_s=1800, now=1000.0)
    assert rec == {"on": False, "set_t": 1000.0, "until": 2800.0}
    assert duckswitch.decide(it, d, now=1000.0) == (False, "override")
    assert duckswitch.decide(it, d, now=2799.999) == (False, "override")
    assert duckswitch.state(d, now=2799.999)["until"] == 2800.0
    # at until, it is over: the stamped setting answers, and nothing was rewritten
    assert duckswitch.decide(it, d, now=2800.0) == (True, "settings")
    assert duckswitch.state(d, now=2800.0) is None
    assert duckswitch.state(d, now=99999.0) is None
    # the expired record is still readable (the readout's "expired_at")
    assert duckswitch.read(d)["until"] == 2800.0


def test_t3_the_module_clock_is_the_fake_clock(d, monkeypatch):
    t = [5000.0]
    monkeypatch.setattr(duckswitch, "clock", lambda: t[0])
    duckswitch.set(d, on=False, for_s=60)
    it = item(True, "builtin")
    assert duckswitch.decide(it, d) == (False, "override")
    t[0] = 5059.0
    assert duckswitch.state(d) is not None
    t[0] = 5060.0
    assert duckswitch.state(d) is None
    assert duckswitch.decide(it, d) == (True, "builtin")


def test_t3_no_for_lasts_the_default_hour(d):
    rec = duckswitch.set(d, on=False, now=100.0)
    assert duckswitch.OVERRIDE_DEFAULT_S == 3600.0
    assert rec["until"] == 3700.0
    assert duckswitch.state(d, now=3699.0) is not None
    assert duckswitch.state(d, now=3700.0) is None


def test_t3_reset_drops_it_at_once(d):
    duckswitch.set(d, on=False, for_s=600, now=0.0)
    assert duckswitch.reset(d) is True
    assert duckswitch.state(d, now=1.0) is None
    assert duckswitch.decide(item(True, "settings"), d, now=1.0) == (True, "settings")
    assert duckswitch.reset(d) is False


def test_t3_a_new_override_replaces_the_old_one_at_once(d):
    """The cached read sees a rewrite, even one in the same instant at the same size."""
    duckswitch.set(d, on=False, for_s=600, now=0.0)
    assert duckswitch.state(d, now=1.0)["on"] is False
    duckswitch.set(d, on=True, for_s=600, now=0.0)
    assert duckswitch.state(d, now=1.0)["on"] is True
    assert duckswitch.decide(item(False, "settings"), d, now=1.0) == (True, "override")
    # the same size, back to back: only the inode tells them apart
    before = (d / "duck.json").stat().st_size
    duckswitch.set(d, on=True, for_s=700, now=0.0)
    assert (d / "duck.json").stat().st_size == before
    assert duckswitch.read(d)["until"] == 700.0


def test_t3_the_write_is_atomic_and_carries_by(d):
    duckswitch.set(d, on=False, for_s=10, now=0.0, by="sess-1")
    assert json.loads((d / "duck.json").read_text())["by"] == "sess-1"
    assert [p.name for p in d.iterdir() if p.name.endswith(".part")] == []


@pytest.mark.parametrize("for_s", [0, -1, float("nan"), float("inf"), True, "30m"])
def test_t3_a_bad_length_is_refused_before_anything_is_written(d, for_s):
    with pytest.raises(ValueError):
        duckswitch.set(d, on=False, for_s=for_s, now=0.0)
    assert not (d / "duck.json").exists()


@pytest.mark.parametrize("on", [None, 0, 1, "off"])
def test_t3_a_bad_on_is_refused(d, on):
    with pytest.raises(ValueError):
        duckswitch.set(d, on=on, for_s=10, now=0.0)


@pytest.mark.parametrize("text", ["", "not json", "[]", '{"on": "off", "until": 9e9, "set_t": 0}',
                                  '{"on": false, "until": "later", "set_t": 0}',
                                  '{"on": false, "set_t": 0}', '{"on": false, "until": NaN, "set_t": 0}'])
def test_a_bad_file_is_no_override_and_never_raises(d, text):
    (d / "duck.json").write_text(text)
    assert duckswitch.read(d) is None
    assert duckswitch.state(d, now=0.0) is None
    assert duckswitch.decide(item(True, "settings"), d, now=0.0) == (True, "settings")


def test_a_removed_file_is_seen(d):
    duckswitch.set(d, on=False, for_s=60, now=0.0)
    assert duckswitch.state(d, now=1.0) is not None
    os.unlink(d / "duck.json")
    assert duckswitch.state(d, now=1.0) is None


# -- T2: the order --------------------------------------------------------------

LAYERS = ("session", "seat", "agent", "settings", "builtin")


@pytest.mark.parametrize("arg", [True, False])
@pytest.mark.parametrize("override", [True, False, None])
def test_t2_the_argument_beats_the_override_and_every_layer(d, arg, override):
    if override is not None:
        duckswitch.set(d, on=override, for_s=60, now=0.0)
    assert duckswitch.decide(item(arg, "flag"), d, now=1.0) == (arg, "argument")


@pytest.mark.parametrize("layer", LAYERS)
@pytest.mark.parametrize("stamped", [True, False])
@pytest.mark.parametrize("override", [True, False])
def test_t2_the_override_beats_every_cascade_layer(d, layer, stamped, override):
    duckswitch.set(d, on=override, for_s=60, now=0.0)
    assert duckswitch.decide(item(stamped, layer), d, now=1.0) == (override, "override")


@pytest.mark.parametrize("layer", LAYERS)
@pytest.mark.parametrize("stamped", [True, False])
def test_t2_with_no_override_the_stamped_layer_answers(d, layer, stamped):
    assert duckswitch.decide(item(stamped, layer), d, now=1.0) == (stamped, layer)


def test_t2_a_file_with_no_duck_stamped_is_the_builtin_on(d):
    duckswitch.set(d, on=False, for_s=60, now=0.0)
    assert duckswitch.decide({"utt": "u", "file": "/x"}, d, now=1.0) == (False, "override")
    assert duckswitch.decide({"utt": "u", "file": "/x"}, d, now=61.0) == (True, "builtin")


def test_t2_no_item_reads_the_host_setting_under_the_override(d):
    """The DJ dip at a spoken line (design.md s3): override, then the host's music_duck."""
    assert duckswitch.decide(None, d, now=1.0) == (True, "builtin")
    host(d, music_duck=False)
    assert duckswitch.decide(None, d, now=1.0) == (False, "settings")
    assert duckswitch.decide(None, d, now=1.0, cascaded={"music_duck": (True, "seat")}) == (True, "seat")
    duckswitch.set(d, on=True, for_s=60, now=0.0)
    assert duckswitch.decide(None, d, now=1.0) == (True, "override")
    assert duckswitch.decide(None, d, now=61.0) == (False, "settings")
