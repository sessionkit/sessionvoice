"""Retention for BOTH kinds of kept audio (EARS-387 do-003).

- The missed-reply clip's prune follows ``record_days``, as kept turn audio does; 0 keeps
  it forever. No fixed two days is left.
- Each prune (turn audio and missed-reply) runs ONLY when the recording switch reads ON
  and ``record_days`` is more than 0, both read at prune time. With recording off nothing
  is pruned, whatever is on disk.
- Each prune deletes only the files the ears wrote, in their own dirs.

A real capture runs on a synthetic source (quiet noise, no speech) with both keepers
attached and one :class:`ears.recording.Switch` reading an ears.json under tmp_path.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import numpy as np
import pytest

from ears import heard, recording
from ears.listen import FRAME_S, SAMPLE_RATE, capture
from ears.listen.missed import PRUNE_EVERY_S as MR_PRUNE_EVERY_S, MissedReply
from ears.listen.turnaudio import PRUNE_EVERY_S as TA_PRUNE_EVERY_S, TurnAudio
from tests.listen_helpers import CountingSource, FakeSTT, RecordingSink, energy_detector, read_lines

DAY = 86400.0
NOW = time.mktime((2030, 10, 19, 12, 0, 0, 0, 0, -1))
OLD = NOW - 400 * DAY


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm"))
    for name in (recording.ENV_RECORD, recording.ENV_DAYS):
        monkeypatch.delenv(name, raising=False)


def quiet(seconds: float) -> np.ndarray:
    n = int(round(seconds * SAMPLE_RATE))
    return np.random.default_rng(7).integers(-99, 100, n).astype(np.int16)


def _plant(p: Path, mtime: float = OLD) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"RIFF")
    os.utime(p, (mtime, mtime))
    return p


class Ears:
    """The ears' two dirs under one base, with an old file of each kind planted."""

    def __init__(self, base: Path):
        self.base = base
        self.audio = base / "audio"
        self.clips = base / "state" / "missed-reply"
        self.turn_wav = _plant(self.audio / "2030-01-07" / "120000-000-1.wav")
        self.clip_wav = _plant(self.clips / "missed-reply_20300107-120000-000_u.wav")

    def keepers(self, sw, clock=lambda: NOW):
        ta = TurnAudio(sw, directory=self.audio, clock=clock)
        mr = MissedReply(list, self.clips, window_s=1.0, clock=clock, home=self.base.parent / "home", switch=sw)
        return ta, mr


def _switch(tmp_path: Path, conf: dict | None, env: dict | None = None) -> tuple[recording.Switch, Path]:
    path = tmp_path / "ears.json"
    if conf is not None:
        path.write_text(json.dumps(conf))
    return recording.Switch(env=env or {}, path=str(path), log=lambda m: None), path


def _capture(tmp_path: Path, ta: TurnAudio, mr: MissedReply) -> list:
    logs = tmp_path / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    source = CountingSource(quiet(1.0))
    capture(source, FakeSTT(), RecordingSink(logs, source), detector=energy_detector(),
            stt_workers=0, heartbeat=0, turn_audio=ta, missed_reply=mr)
    return read_lines(logs)


# -- criterion 1: record OFF, nothing is pruned -------------------------------------------


@pytest.mark.parametrize("conf, env", [
    (None, None),                                   # the default: off
    ({"record": False, "record_days": 1}, None),    # off by the file, with a short retention
    ({"record": True, "record_days": 1}, {recording.ENV_RECORD: "0"}),  # off by the env
    ({"record": "on", "record_days": 1}, None),     # invalid: fails closed, so off
], ids=["default", "file-off", "env-off", "invalid"])
def test_record_off_prunes_neither_kind(tmp_path, conf, env):
    ears = Ears(tmp_path / "vm")
    sw, _ = _switch(tmp_path, conf, env)
    assert not sw()
    ta, mr = ears.keepers(sw)
    _capture(tmp_path, ta, mr)                           # the capture's start prune
    assert ta.open() == 0 and ta._prune() == 0           # and again, by hand
    mr.open()
    assert mr._prune() == 0
    assert ears.turn_wav.exists() and ears.clip_wav.exists()


# -- criterion 2: record ON, days 1: the ears' old files go, nothing else does -------------


def test_record_on_prunes_only_the_ears_own_files_of_both_kinds(tmp_path):
    base = tmp_path / "vm"
    ears = Ears(base)
    outside = [
        _plant(base / "mouth" / "audio" / "2030-01-07" / "u1.wav"),             # the mouth's audio
        _plant(base / "mouth" / "audio" / "2030-01-07" / "120000-000-1.wav"),
        _plant(base / "calls" / "2030-01-07" / "caller-call.wav"),             # a call dir
        _plant(base / "calls" / "missed-reply_20300107-120000-000_u.wav"),
        _plant(base / "state" / "other" / "missed-reply_20300107-120000-000_u.wav"),
        _plant(ears.clips / "sub" / "missed-reply_20300107-120000-000_u.wav"),  # nested: not its dir
        _plant(ears.clips / "someone-elses.wav"),                                # its dir, not its prefix
        _plant(ears.audio / "2030-01-07" / "missed-reply_x.wav"),               # not a turn-audio name
        _plant(ears.audio / "calls" / "120000-000-1.wav"),                      # not a date dir
    ]
    sw, _ = _switch(tmp_path, {"record": True, "record_days": 1})
    ta, mr = ears.keepers(sw)
    _capture(tmp_path, ta, mr)
    assert not ears.turn_wav.exists() and not ears.clip_wav.exists()
    for p in outside:
        assert p.exists(), p


def test_record_on_keeps_what_is_younger_than_record_days(tmp_path):
    ears = Ears(tmp_path / "vm")
    young_clip = _plant(ears.clips / "missed-reply_young.wav", NOW - 0.5 * DAY)
    today = time.strftime("%Y-%m-%d", time.localtime(NOW))
    young_turn = _plant(ears.audio / today / "110000-000-1.wav", NOW - 3600)
    sw, _ = _switch(tmp_path, {"record": True, "record_days": 1})
    ta, mr = ears.keepers(sw)
    ta.open()
    mr.open()
    assert not ears.turn_wav.exists() and not ears.clip_wav.exists()
    assert young_clip.exists() and young_turn.exists()


# -- criterion 3: record ON, days 0: kept forever, both kinds -------------------------------


@pytest.mark.parametrize("conf, env", [
    ({"record": True, "record_days": 0}, None),
    ({"record": True}, {recording.ENV_DAYS: "0"}),
], ids=["file-days-0", "env-days-0"])
def test_record_on_days_0_prunes_nothing_missed_reply_included(tmp_path, conf, env):
    ears = Ears(tmp_path / "vm")
    sw, _ = _switch(tmp_path, conf, env)
    assert sw() and sw.days().value == 0
    ta, mr = ears.keepers(sw)
    _capture(tmp_path, ta, mr)
    assert ta._prune() == 0 and mr._prune() == 0
    assert ears.turn_wav.exists() and ears.clip_wav.exists()


# -- checked at prune time, not only at start -----------------------------------------------


def test_the_switch_is_read_at_each_prune_not_only_at_start(tmp_path):
    """Off at start: nothing goes. Turned on in ears.json mid-run: the next hourly prune
    deletes. Turned off again: the next hourly prune deletes nothing."""
    t = [NOW]
    ears = Ears(tmp_path / "vm")
    sw, path = _switch(tmp_path, {"record": False, "record_days": 1})
    ta, mr = ears.keepers(sw, clock=lambda: t[0])
    assert ta.open() == 0
    mr.open()
    assert ears.turn_wav.exists() and ears.clip_wav.exists()

    path.write_text(json.dumps({"record": True, "record_days": 1, "pad": "x"}))  # size changes
    t[0] += max(TA_PRUNE_EVERY_S, MR_PRUNE_EVERY_S) + 1
    assert ta.poll() == 1
    mr.poll(MR_PRUNE_EVERY_S)
    assert not ears.turn_wav.exists() and not ears.clip_wav.exists()

    path.write_text(json.dumps({"record": False, "record_days": 1}))
    again = Ears(tmp_path / "vm")
    t[0] += max(TA_PRUNE_EVERY_S, MR_PRUNE_EVERY_S) + 1
    assert ta.poll() == 0
    mr.poll(2 * MR_PRUNE_EVERY_S + 1)
    assert again.turn_wav.exists() and again.clip_wav.exists()


def test_a_clip_write_prunes_with_record_days_from_the_switch(tmp_path):
    """The prune at each new clip reads record_days too (here 1), not two days."""
    ears = Ears(tmp_path / "vm")
    two_days = _plant(ears.clips / "missed-reply_two.wav", NOW - 1.5 * DAY)  # kept under 2 days, gone at 1
    sw, _ = _switch(tmp_path, {"record": True, "record_days": 1})
    said = [{"seq": 1, "kind": "said", "utt": "u", "played_s": 1.0, "ts": heard._now_iso()}]
    mr = MissedReply(lambda: [said.pop()] if said else [], ears.clips, window_s=0.3,
                     clock=lambda: NOW, home=tmp_path / "home", switch=sw)
    mr.poll(0.0)  # no open(): only the write's own prune runs
    frame = np.zeros(int(round(FRAME_S * SAMPLE_RATE)), dtype=np.int16)
    out = []
    for _ in range(10):
        out += mr.frame(frame, None, False)
    [(_name, ev)] = out
    assert ev["pruned"] == 2
    assert not two_days.exists() and not ears.clip_wav.exists() and Path(ev["file"]).exists()
