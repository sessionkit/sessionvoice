"""The ears' recording switch (EARS-387): off by default; CLI, then env, then ears.json.

The resolver is held to the shared fixture table (tests/fixtures/recording-switch-cases.json,
"EARS-387 recording switch contract, rules 1-11"), which the call recorder runs
too. Then the capture: with nothing set, a missed reply writes no WAV anywhere; each layer
beats the one below it; a live flip of ears.json changes the next clip. Then the CLI,
`ears recording status|on|off`. Synthetic audio only, every path in tmp_path.
"""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

import numpy as np
import pytest

from ears import heard, recording
from ears.home import SETTINGS
from ears.listen import FRAME_S, SAMPLE_RATE, capture
from ears.listen.__main__ import _missed_reply, _switch, build_parser
from ears.listen.missed import MissedReply
from tests.listen_helpers import CountingSource, FakeSTT, RecordingSink, energy_detector, read_lines

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "tests" / "fixtures" / "recording-switch-cases.json").read_text())
SCHEMA = json.loads((ROOT / "settings" / "ears.schema.json").read_text())
FRAME = int(round(FRAME_S * SAMPLE_RATE))
ENVS = (recording.ENV_RECORD, recording.ENV_DAYS, recording.ENV_NOTICE, recording.ENV_SPOKEN)


@pytest.fixture
def conf(tmp_path, monkeypatch):
    """ears.json in tmp, named by $EARS_CONF; the three env names unset."""
    p = tmp_path / "settings" / "ears" / "ears.json"
    monkeypatch.setenv("EARS_CONF", str(p))
    for name in ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm"))
    return p


def _put(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj))


# -- the shared table ---------------------------------------------------------------------


def test_the_fixture_is_the_contract_table():
    assert CASES["contract"] == "EARS-387 recording switch contract, rules 1-11"
    assert len(CASES["cases"]) >= 59
    for case in CASES["cases"]:
        assert set(case["expect"]) == {"record", "record_days", "record_notice", "record_spoken_notice"}, case["name"]


@pytest.mark.parametrize("case", CASES["cases"], ids=[c["name"] for c in CASES["cases"]])
def test_resolve_matches_every_case_of_the_shared_table(case, conf, monkeypatch):
    inp = case["input"]
    if inp["file"] == "unreadable" and os.geteuid() == 0:
        pytest.skip("root reads a mode-000 file")
    for name in ENVS:
        v = inp.get(f"env_{name}")
        if v is not None:
            monkeypatch.setenv(name, v)
    f = inp["file"]
    if f != "absent":
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_text("{not json" if f == "invalid-json" else "{}" if f == "unreadable" else f["raw"])
        if f == "unreadable":
            conf.chmod(0)
    try:
        got = recording.resolve_all(inp["cli"]).as_dict()
        on, source = recording.resolve(inp["cli"])
        via_switch = recording.Switch(inp["cli"], log=lambda _m: None)
        switched = (via_switch.record().as_dict(), via_switch.days().as_dict())
    finally:
        if conf.exists():
            conf.chmod(0o600)
    assert got == case["expect"]
    assert (on, source) == (case["expect"]["record"]["on"], case["expect"]["record"]["source"])
    assert switched == (case["expect"]["record"], case["expect"]["record_days"])


def test_an_invalid_value_is_named_in_a_warning(conf, monkeypatch):
    monkeypatch.setenv("EARS_RECORD", "yes")
    _put(conf, {"record_days": -2, "record_notice": "off", "record_spoken_notice": 0})
    st = recording.resolve_all()
    text = "\n".join(st.warnings)
    assert "EARS_RECORD='yes'" in text and "fails closed" in text
    assert '"record_days": -2' in text and '"record_notice": "off"' in text and "fails to on" in text
    assert '"record_spoken_notice": 0' in text
    assert st.record_spoken_notice.as_dict() == {"on": True, "source": "file", "invalid": True}


def test_the_ears_json_path_is_the_capture_s(conf, monkeypatch):
    from ears.listen.stt import conf_path
    assert recording.conf_path() == conf_path() == str(conf)
    monkeypatch.setenv("EARS_CONF", "")  # set but empty: the default path
    assert recording.conf_path() == conf_path() == os.path.expanduser(f"~/{SETTINGS}/ears.json")


# -- the switch a capture holds -----------------------------------------------------------


def test_the_last_flag_given_wins():
    p = build_parser()
    assert p.parse_args(["capture"]).record is None
    assert p.parse_args(["capture", "--record"]).record is True
    assert p.parse_args(["capture", "--no-record"]).record is False
    assert p.parse_args(["capture", "--record", "--no-record"]).record is False
    assert p.parse_args(["capture", "--no-record", "--record"]).record is True


def test_env_is_fixed_for_the_life_of_the_switch(conf, monkeypatch):
    monkeypatch.setenv("EARS_RECORD", "1")
    sw = recording.Switch(log=lambda _m: None)
    monkeypatch.setenv("EARS_RECORD", "0")
    assert sw.record().as_dict() == {"on": True, "source": "env", "invalid": False}


def test_the_switch_follows_the_file_and_reads_it_only_when_it_changes(conf, monkeypatch):
    _put(conf, {"record": True})
    sw = recording.Switch(log=lambda _m: None)
    assert sw() is True
    reads = []
    real = recording.read_file
    monkeypatch.setattr(recording, "read_file", lambda p=None: reads.append(p) or real(p))
    for _ in range(5):
        assert sw() is True
    assert reads == []  # stat unchanged: not read again
    _put(conf, {"record": False, "listen": True})
    assert sw.record().as_dict() == {"on": False, "source": "file", "invalid": False}
    assert len(reads) == 1
    conf.unlink()
    assert sw.record().source == "default"


def test_a_warning_is_said_once(conf, monkeypatch):
    _put(conf, {"record": "on"})
    said = []
    sw = recording.Switch(log=said.append)
    for _ in range(3):
        assert sw.record().as_dict() == {"on": False, "source": "file", "invalid": True}
    _put(conf, {"record": "on", "listen": True})  # changed, read again: the same warning
    sw.record()
    assert len(said) == 1 and '"record": "on"' in said[0]


def test_rule_2_a_bad_file_sets_nothing_and_is_said_once(conf):
    conf.parent.mkdir(parents=True)
    conf.write_text("{not json")
    said = []
    sw = recording.Switch(log=said.append)
    for _ in range(3):
        assert sw.record().source == "default" and sw.days().source == "default"
    assert len(said) == 1 and "not JSON" in said[0] and "the file sets nothing" in said[0]


# -- the capture: nothing kept unless the switch is on ------------------------------------


def _quiet(seconds: float, seed: int = 7) -> np.ndarray:
    n = int(round(seconds * SAMPLE_RATE))
    return np.random.default_rng(seed).integers(-99, 100, n).astype(np.int16)


class _MouthSource(CountingSource):
    """Writes a mouth ``said`` into the heard log before the frames in ``said_at``, and
    calls ``at[k]()`` before frame k (a live change to ears.json)."""

    def __init__(self, samples, logs: Path, said_at=(), at=None):
        super().__init__(samples)
        self.logs, self.said_at, self.at = logs, set(said_at), at or {}

    def frames(self):
        for frame in super().frames():
            k = self.yielded - 1
            if k in self.at:
                self.at[k]()
            if k in self.said_at:
                heard.said(directory=self.logs, utt=f"u{k}", played_s=2.0)
            yield frame


def _capture(tmp_path, argv, seconds, said_at, at=None):
    """The capture as the CLI builds it from ``argv`` (its parser, its switch, its
    missed-reply clip), on synthetic audio. Returns the heard lines."""
    logs = tmp_path / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    args = build_parser().parse_args(["capture", "--log-dir", str(logs), *argv])
    switch = _switch(args)
    mr = _missed_reply(args, switch)
    mr.home = tmp_path / "home"
    source = _MouthSource(_quiet(seconds), logs, said_at, at)
    capture(source, FakeSTT(), RecordingSink(logs, source), detector=energy_detector(),
            stt_workers=0, heartbeat=0, missed_reply=mr, record=switch)
    return read_lines(logs)


def _events(lines, name):
    return [ln for ln in lines if ln["kind"] == "event" and ln.get("event") == name]


def _wavs(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.suffix.lower() == ".wav")


def test_default_off_a_missed_reply_with_a_30s_window_writes_no_wav_anywhere(tmp_path, conf):
    """THE pin (EARS-387 R1): no flag, no env, no key => no audio file at all."""
    assert not conf.exists() and not any(os.environ.get(n) for n in ENVS)
    lines = _capture(tmp_path, ["--missed-reply-window", "30"], 32.0, [5])
    assert _wavs(tmp_path) == []
    assert _events(lines, heard.EV_MISSED_REPLY) == []
    [off] = _events(lines, "missed-reply-off")
    assert off["why"] == "recording off" and off["record_from"] == "default"
    [started] = _events(lines, heard.EV_LISTEN_STARTED)
    assert started["record"] is False and started["record_from"] == "default"
    assert started["missed_reply_s"] == 30.0  # the window is still given: only the keeping is off


def test_the_same_run_with_record_on_keeps_one_clip(tmp_path, conf):
    """The pin's mirror: the run above would see a clip if one were written."""
    lines = _capture(tmp_path, ["--missed-reply-window", "30", "--record"], 32.0, [5])
    [wav] = _wavs(tmp_path)
    [ev] = _events(lines, heard.EV_MISSED_REPLY)
    assert ev["file"] == str(wav) and stat.S_IMODE(wav.stat().st_mode) == 0o600
    assert _events(lines, "missed-reply-off") == []
    [started] = _events(lines, heard.EV_LISTEN_STARTED)
    assert started["record"] is True and started["record_from"] == "cli"


@pytest.mark.parametrize("flag,env,file,kept,source", [
    (["--record"], "0", None, True, "cli"),           # cli > env
    (["--no-record"], "1", {"record": True}, False, "cli"),  # --no-record beats EARS_RECORD=1
    ([], "1", {"record": False}, True, "env"),        # env > file
    ([], "0", {"record": True}, False, "env"),
    ([], None, {"record": True}, True, "file"),       # file > default
    ([], "yes", {"record": True}, False, "env"),      # an invalid env fails closed
], ids=["cli>env", "no-record>env1", "env>file", "env0>file", "file>default", "invalid-env"])
def test_each_layer_beats_the_one_below_it_in_a_capture(tmp_path, conf, monkeypatch, flag, env, file, kept, source):
    if env is not None:
        monkeypatch.setenv("EARS_RECORD", env)
    if file is not None:
        _put(conf, file)
    lines = _capture(tmp_path, ["--missed-reply-window", "1", *flag], 3.0, [5])
    assert len(_wavs(tmp_path / "vm")) == (1 if kept else 0)
    [started] = _events(lines, heard.EV_LISTEN_STARTED)
    assert started["record"] is kept and started["record_from"] == source


def test_a_live_flip_of_ears_json_changes_the_next_clip(tmp_path, conf):
    """Three windows of 1 s, the file flipped between them, never a restart:
    on (a clip), off (none, one missed-reply-off), on again (a clip)."""
    _put(conf, {"record": True})
    flips = {60: lambda: _put(conf, {"record": False, "listen": True}),
             120: lambda: _put(conf, {"record": True})}
    lines = _capture(tmp_path, ["--missed-reply-window", "1"], 6.0, [5, 65, 125], at=flips)
    clips = _events(lines, heard.EV_MISSED_REPLY)
    assert [c["utt"] for c in clips] == ["u5", "u125"]
    assert len(_wavs(tmp_path / "vm")) == 2
    [off] = _events(lines, "missed-reply-off")
    assert off["why"] == "recording off" and off["record_from"] == "file"


def test_off_is_said_once_per_change_not_once_per_missed_window(tmp_path, conf):
    lines = _capture(tmp_path, ["--missed-reply-window", "1"], 8.0, [5, 60, 120, 180])
    assert len(_events(lines, "missed-reply-off")) == 1 and _wavs(tmp_path) == []


def test_off_at_write_time_drops_a_window_that_opened_while_on(tmp_path):
    """Rule 8: the switch is read as the clip is about to be written, and audio held
    while it was on is not written after it turned off."""
    state = {"on": True}

    class Flip:
        def record(self):
            return recording.Record(state["on"], "file")

        def days(self):
            return recording.Days(recording.DEFAULT_DAYS, "default")

    said = [{"seq": 1, "kind": "said", "utt": "u", "played_s": 1.0, "ts": heard._now_iso()}]
    mr = MissedReply(lambda: [said.pop()] if said else [], tmp_path / "c", window_s=0.3,
                     home=tmp_path / "home", switch=Flip())
    assert mr.open() == []
    mr.poll(0.0)
    assert mr.pending
    out = []
    for i in range(10):
        if i == 5:
            state["on"] = False
        out += mr.frame(np.zeros(FRAME, dtype=np.int16), None, False)
    assert out == [("missed-reply-off", {"why": "recording off", "record_from": "file"})]
    assert not (tmp_path / "c").exists() or _wavs(tmp_path / "c") == []


def test_a_library_capture_with_no_switch_keeps_nothing(tmp_path, conf):
    """A MissedReply made with no switch reads this process's own: off by default."""
    logs = tmp_path / "logs"
    logs.mkdir()
    source = _MouthSource(_quiet(3.0), logs, [5])
    mr = MissedReply(lambda: [], tmp_path / "c", window_s=1.0, home=tmp_path / "home")
    capture(source, FakeSTT(), RecordingSink(logs, source), detector=energy_detector(),
            stt_workers=0, heartbeat=0, missed_reply=mr)
    lines = read_lines(logs)
    [started] = _events(lines, heard.EV_LISTEN_STARTED)
    assert started["record"] is False and started["record_from"] == "default"
    assert _wavs(tmp_path) == []


# -- `ears recording` ---------------------------------------------------------------------


def _run(argv, capsys, ps=lambda: "", **env):
    from ears.cli import main as ears_main
    for k, v in env.items():
        os.environ[k] = v
    try:
        if ps is None:
            rc = ears_main(["recording", *argv])
        else:
            rc = recording.main(argv, ps=ps)
    finally:
        for k in env:
            os.environ.pop(k, None)
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_status_says_off_by_default_with_its_source_and_retention(conf, capsys):
    rc, out, _err = _run(["status"], capsys)
    assert rc == 0
    assert "recording: off  (source: default)" in out
    assert "retention: 7 days  (source: default)" in out
    assert "call notice: on  (source: default)" in out
    assert "spoken call notice: on  (source: default)" in out
    assert recording.ALL_VOICES not in out


def test_status_json_names_each_source(conf, capsys, monkeypatch):
    _put(conf, {"record": True, "record_days": 14})
    monkeypatch.setenv("EARS_RECORD_NOTICE", "off")
    monkeypatch.setenv("EARS_RECORD_SPOKEN_NOTICE", "on")
    rc, out, _err = _run(["status", "--json"], capsys)
    d = json.loads(out)
    assert rc == 0
    assert d["record"] == {"on": True, "source": "file", "invalid": False}
    assert d["record_days"] == {"value": 14, "source": "file", "invalid": False}
    assert d["record_notice"] == {"on": False, "source": "env", "invalid": False}
    assert d["record_spoken_notice"] == {"on": True, "source": "env", "invalid": False}
    assert d["file"] == str(conf) and d["captures"] == []


def test_status_on_says_every_voice_is_kept(conf, capsys, monkeypatch):
    monkeypatch.setenv("EARS_RECORD", "1")
    rc, out, _err = _run(["status"], capsys)
    assert rc == 0 and "recording: on  (source: env EARS_RECORD=1)" in out
    assert recording.ALL_VOICES in out


def test_the_ears_command_dispatches_recording(conf, capsys):
    rc, out, _err = _run(["status", "--json"], capsys, ps=None)
    assert rc == 0 and json.loads(out)["record"]["source"] == "default"


def _schema_valid(d: dict) -> None:
    """The writer's output against settings/ears.schema.json, for the keys it writes and
    every key's name (no validator is a dependency: these are the rules that matter here)."""
    props = SCHEMA["properties"]
    for k in d:
        assert k in props or k.startswith("_"), k
    assert isinstance(d["record"], bool) and props["record"]["type"] == "boolean"


def test_on_writes_the_key_atomically_keeping_the_others_and_warns_of_all_voices(conf, capsys):
    _put(conf, {"listen": True, "final": {"url": "http://127.0.0.1:2022/v1"}, "_why": "a note"})
    conf.chmod(0o640)
    rc, out, _err = _run(["on"], capsys)
    assert rc == 0
    d = json.loads(conf.read_text())
    assert d == {"listen": True, "final": {"url": "http://127.0.0.1:2022/v1"}, "_why": "a note", "record": True}
    _schema_valid(d)
    assert stat.S_IMODE(conf.stat().st_mode) == 0o640           # the file's mode is kept
    assert [p.name for p in conf.parent.iterdir()] == ["ears.json"]  # no temp file left
    assert f"wrote record: true to {conf}" in out
    assert recording.ALL_VOICES in out and "7 days" in out
    assert recording.resolve() == (True, "file")


def test_off_on_a_new_host_makes_the_file(conf, capsys):
    rc, out, _err = _run(["off"], capsys)
    assert rc == 0 and json.loads(conf.read_text()) == {"record": False}
    assert stat.S_IMODE(conf.stat().st_mode) == 0o600
    assert "Recording is OFF" in out


@pytest.mark.parametrize("cmd", ["on", "off"])
def test_on_off_never_vouch_for_a_capture_s_own_env(conf, capsys, cmd):
    """Rule 9: a capture started with EARS_RECORD set (the keeper's environment, which the
    CHANGELOG suggests) is not this shell's, so off never says "no audio is kept" outright."""
    rc, out, _err = _run([cmd], capsys)
    assert rc == 0
    assert "no audio is kept" not in out
    assert "sees only this shell's environment" in out and "EARS_RECORD set keeps to that" in out
    assert "keeper log's start note" in out


def test_off_says_the_env_wins_names_its_value_and_exits_0(conf, capsys):
    """Rule 9: nobody turns recording off, sees it accepted, and is still recorded."""
    rc, out, _err = _run(["off"], capsys, EARS_RECORD="1")
    assert rc == 0 and json.loads(conf.read_text())["record"] is False
    assert "but EARS_RECORD=1 in this environment wins: recording stays ON" in out
    assert "Recording is OFF" not in out


def test_on_says_an_invalid_env_wins_closed(conf, capsys):
    rc, out, _err = _run(["on"], capsys, EARS_RECORD="yes")
    assert rc == 0
    assert "but EARS_RECORD=yes (not a valid value, so it fails closed) in this environment wins: recording stays OFF" in out


def test_off_names_a_running_capture_whose_flag_wins(conf, capsys):
    ps = lambda: ("  101 /venv/bin/python -m ears.listen capture --source mic --record\n"
                  "  102 /venv/bin/python -m ears.listen capture --source call:1\n"
                  "  103 /usr/bin/vim notes --record\n")
    rc, out, _err = _run(["off"], capsys, ps=ps)
    assert rc == 0
    assert "but capture 101 runs with --record, which wins: recording stays ON for it" in out
    # Whole tokens, never a bare "102": out echoes the settings path, which holds
    # pytest's basetemp (pytest-102/...), so a bare-digit check is host-flaky.
    assert not re.search(r"\bcapture 10[23]\b", out)


def test_running_flags_takes_the_last_flag():
    ps = lambda: "7 ears capture --record --no-record\n8 python -m ears.listen capture --no-record --record\n"
    assert recording.running_flags(ps) == [(7, False), (8, True)]


def test_a_file_that_is_not_an_object_is_refused_and_left_alone(conf, capsys):
    conf.parent.mkdir(parents=True)
    conf.write_text("{not json")
    rc, _out, err = _run(["on"], capsys)
    assert rc == 1 and "not JSON" in err and "nothing written" in err
    assert conf.read_text() == "{not json"


def test_the_keeper_line(conf, monkeypatch):
    assert recording.line() == "off (default), retention 7 days (default)"
    monkeypatch.setenv("EARS_RECORD_DAYS", "0")
    _put(conf, {"record": True})
    assert recording.line() == f"on (file {conf}), retention forever (env EARS_RECORD_DAYS=0)"


# -- the schema ---------------------------------------------------------------------------


def test_the_schema_draws_the_three_settings():
    p = SCHEMA["properties"]
    assert p["record"]["type"] == "boolean" and p["record"]["default"] is False and p["record"]["title"]
    assert p["record_days"]["type"] == "integer" and p["record_days"]["minimum"] == 0
    assert p["record_days"]["default"] == recording.DEFAULT_DAYS
    assert p["record_notice"]["type"] == "boolean" and p["record_notice"]["default"] is True
    assert p["record_spoken_notice"]["type"] == "boolean" and p["record_spoken_notice"]["default"] is True
    assert "guests and TV included" in p["record"]["description"]


def test_status_says_kept_forever_for_zero_days_never_0_days(conf, capsys):
    _put(conf, {"record": True, "record_days": 0})
    rc, out, _err = _run(["status"], capsys)
    assert rc == 0 and "retention: kept forever  (source: file" in out and "0 days" not in out


def test_on_days_writes_both_keys_in_one_write(conf, capsys, monkeypatch):
    """The deploy step is one command: `ears recording on --days 0`."""
    _put(conf, {"listen": True, "record_days": 7})
    writes = []
    real = os.replace
    monkeypatch.setattr(recording.os, "replace", lambda a, b: writes.append(b) or real(a, b))
    rc, out, _err = _run(["on", "--days", "0"], capsys)
    assert rc == 0 and len(writes) == 1
    d = json.loads(conf.read_text())
    assert d == {"listen": True, "record": True, "record_days": 0}
    _schema_valid(d)
    assert "wrote record: true and record_days: 0" in out and "kept forever" in out and "0 days" not in out
    st = recording.resolve_all()
    assert (st.record.on, st.record_days.value, st.record_days.source) == (True, 0, "file")


def test_on_without_days_leaves_record_days_alone(conf, capsys):
    _put(conf, {"record_days": 30})
    _run(["on"], capsys)
    assert json.loads(conf.read_text()) == {"record_days": 30, "record": True}


@pytest.mark.parametrize("bad", ["-1", "1.5", "week", ""])
def test_on_refuses_a_bad_days_and_writes_nothing(conf, capsys, bad):
    with pytest.raises(SystemExit) as e:
        recording.main(["on", "--days", bad], ps=lambda: "")
    assert e.value.code == 2 and not conf.exists()


def test_on_days_says_the_env_days_wins(conf, capsys):
    rc, out, _err = _run(["on", "--days", "0"], capsys, EARS_RECORD_DAYS="3")
    assert rc == 0 and "but EARS_RECORD_DAYS=3 in this environment wins: retention stays 3 days" in out


@pytest.mark.parametrize("raw", ["7\n", "0\n", " 7", "7 ", "+7", "٣"])
def test_days_env_is_the_exact_digits_or_it_is_skipped(conf, raw):
    """Rule 5, and the table's "whitespace included": "0\\n" must not slip through as
    forever (a regex "$" matches before a trailing newline)."""
    _put(conf, {"record_days": 14})
    st = recording.resolve_all(env={"EARS_RECORD_DAYS": raw})
    assert st.record_days.as_dict() == {"value": 14, "source": "file", "invalid": False}
    assert any("EARS_RECORD_DAYS" in w for w in st.warnings)


def test_on_names_a_running_capture_whose_flag_agrees(conf, capsys):
    """Rule 9: a higher layer that is set is named even when it agrees with the file."""
    ps = lambda: "  101 /venv/bin/python -m ears.listen capture --source mic --record\n"
    rc, out, _err = _run(["on"], capsys, ps=ps)
    assert rc == 0
    assert "note: capture 101 runs with --record, which decides for it, not the file; it agrees" in out
    assert "but capture" not in out
