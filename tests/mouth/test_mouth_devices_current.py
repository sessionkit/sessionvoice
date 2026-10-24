"""EARS-430: ``mouth devices`` marks the current output, with its volume, and names the input.

The device query is stubbed throughout: ``mouth.coreaudio``'s four functions,
``output.output_names`` and ``output.live_calls``. No test here opens PortAudio;
the one darwin smoke test reads CoreAudio's properties and asserts types only.
"""

from __future__ import annotations

import ctypes
import json
import sys

import pytest

from mouth import coreaudio, devices, output
from mouth.__main__ import main

OUTPUTS = ["HP Z27i", "Mac Studio Speakers", "External Headphones"]


@pytest.fixture
def machine(monkeypatch):
    """A fake machine: three outputs, no call, a current output, input and volume."""
    state = {"outputs": list(OUTPUTS), "calls": [], "output": "Mac Studio Speakers",
             "input": "USB Microphone", "volume": 0.62, "muted": False, "portaudio": None}
    monkeypatch.setattr(output, "output_names", lambda rescan=True: list(state["outputs"]))
    monkeypatch.setattr(output, "live_calls", lambda: list(state["calls"]))
    monkeypatch.setattr(coreaudio, "default_output_name", lambda: state["output"])
    monkeypatch.setattr(coreaudio, "default_input_name", lambda: state["input"])
    monkeypatch.setattr(coreaudio, "output_volume", lambda: state["volume"])
    monkeypatch.setattr(coreaudio, "output_muted", lambda: state["muted"])
    monkeypatch.setattr(devices, "_portaudio_default", lambda: state["portaudio"])
    return state


def run(capsys, *args) -> str:
    assert main(["devices", *args]) == 0
    return capsys.readouterr().out


def test_the_current_output_is_marked_with_its_volume(machine, capsys):
    lines = run(capsys).splitlines()
    assert lines == ["HP Z27i",
                     "Mac Studio Speakers\t# current output, volume 62%",
                     "External Headphones",
                     "# current input: USB Microphone"]


def test_a_reader_that_cuts_at_the_tab_still_gets_the_names(machine, capsys):
    names = [ln.split("\t")[0] for ln in run(capsys).splitlines() if not ln.startswith("#")]
    assert names == OUTPUTS


def test_the_match_is_case_insensitive_and_marks_one_line(machine, capsys):
    machine["outputs"] = ["Speakers", "speakers"]
    machine["output"] = "SPEAKERS"
    lines = run(capsys).splitlines()
    assert lines[:2] == ["Speakers\t# current output, volume 62%", "speakers"]


def test_muted_says_so_beside_the_volume(machine, capsys):
    machine["muted"] = True
    assert "Mac Studio Speakers\t# current output, volume 62% (muted)" in run(capsys).splitlines()


def test_an_unknown_volume_is_left_out_never_invented(machine, capsys):
    machine["volume"] = None
    machine["muted"] = None
    assert "Mac Studio Speakers\t# current output" in run(capsys).splitlines()
    machine["muted"] = True
    assert "Mac Studio Speakers\t# current output, muted" in run(capsys).splitlines()


def test_volume_rounds_to_a_whole_percent(machine, capsys):
    machine["volume"] = 0.7098
    assert "Mac Studio Speakers\t# current output, volume 71%" in run(capsys).splitlines()
    machine["volume"] = 0.0
    assert "Mac Studio Speakers\t# current output, volume 0%" in run(capsys).splitlines()


def test_no_listed_match_means_no_marker(machine, capsys):
    machine["output"] = "Some Aggregate"
    out = run(capsys)
    assert "current output" not in out
    assert out.splitlines()[:3] == OUTPUTS


def test_the_input_line_comes_last_after_the_calls(machine, capsys):
    machine["calls"] = [(3, "sessionvoice+super", "/tmp/s")]
    lines = run(capsys).splitlines()
    assert lines[-2:] == ["call:3\t# live call, sessionvoice+super", "# current input: USB Microphone"]


def test_no_input_no_input_line(machine, capsys):
    machine["input"] = None
    assert "current input" not in run(capsys)


def test_portaudio_names_the_output_when_coreaudio_cannot(machine, capsys):
    machine["output"] = None
    machine["portaudio"] = "External Headphones"
    assert "External Headphones\t# current output, volume 62%" in run(capsys).splitlines()


def test_json_carries_every_field(machine, capsys):
    machine["calls"] = [(3, "sessionvoice+super", "/tmp/s")]
    machine["muted"] = True
    d = json.loads(run(capsys, "--json"))
    assert d == {"outputs": OUTPUTS,
                 "calls": [{"name": "call:3", "who": "sessionvoice+super"}],
                 "current_output": "Mac Studio Speakers",
                 "current_input": "USB Microphone",
                 "output_volume": 0.62,
                 "output_muted": True}


def test_off_darwin_everything_unknown_is_null_and_left_out(machine, capsys):
    machine.update(output=None, input=None, volume=None, muted=None, portaudio=None)
    assert run(capsys).splitlines() == OUTPUTS
    d = json.loads(run(capsys, "--json"))
    assert (d["current_output"], d["current_input"], d["output_volume"], d["output_muted"]) == (None,) * 4
    assert d["outputs"] == OUTPUTS


# --- mouth.coreaudio: never raises, None off darwin or on failure ---------------------


@pytest.fixture
def fresh_libs(monkeypatch):
    monkeypatch.setattr(coreaudio, "_libs", None)


QUERIES = (coreaudio.default_output_name, coreaudio.default_input_name,
           coreaudio.output_volume, coreaudio.output_muted)


def test_off_darwin_every_query_is_none(monkeypatch, fresh_libs):
    monkeypatch.setattr(sys, "platform", "linux")
    def no(*a, **k):
        raise AssertionError("no framework is loaded off darwin")
    monkeypatch.setattr(ctypes, "CDLL", no)
    assert [q() for q in QUERIES] == [None] * 4


def test_a_framework_that_will_not_load_is_none_not_an_error(monkeypatch, fresh_libs):
    monkeypatch.setattr(sys, "platform", "darwin")
    def boom(*a, **k):
        raise OSError("no such framework")
    monkeypatch.setattr(ctypes, "CDLL", boom)
    assert [q() for q in QUERIES] == [None] * 4


class _FakeFn:
    def __init__(self, fn):
        self.fn, self.argtypes, self.restype = fn, None, None

    def __call__(self, *a):
        return self.fn(*a)


class _FakeLib:
    """CoreAudio, AudioToolbox and CoreFoundation, all in one, from a property table."""

    def __init__(self, props, status=0):
        self.props, self.status = props, status
        self.AudioObjectHasProperty = _FakeFn(self._has)
        self.AudioObjectGetPropertyData = _FakeFn(self._get)
        self.AudioHardwareServiceHasProperty = _FakeFn(lambda o, a: 0)
        self.AudioHardwareServiceGetPropertyData = _FakeFn(lambda *a: -1)
        self.CFStringGetCString = _FakeFn(self._cstr)
        self.CFRelease = _FakeFn(lambda ref: None)
        self.strings = {}

    def _key(self, obj, addr):
        a = addr._obj
        return (obj, a.mSelector, a.mScope, a.mElement)

    def _has(self, obj, addr):
        return 1 if self._key(obj, addr) in self.props else 0

    def _get(self, obj, addr, qs, q, size, out):
        if self.status:
            return self.status
        val = self.props[self._key(obj, addr)]
        target = out._obj
        if isinstance(val, str):                    # a CFString: hand back a handle
            handle = 0x1000 + len(self.strings)
            self.strings[handle] = val
            target.value = handle
        else:
            target.value = val
        return 0

    def _cstr(self, ref, buf, n, enc):
        s = self.strings[ref.value if hasattr(ref, "value") else ref].encode()
        ctypes.memmove(buf, s + b"\0", len(s) + 1)
        return True


C = coreaudio
G, O, M = C.SCOPE_GLOBAL, C.SCOPE_OUTPUT, C.ELEMENT_MAIN


def _fake(monkeypatch, props, status=0):
    lib = _FakeLib(props, status)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(coreaudio, "_libs", (lib, lib, lib))
    return lib


def test_names_volume_and_mute_from_the_properties(monkeypatch):
    _fake(monkeypatch, {(1, C.DEFAULT_OUTPUT, G, M): 40, (1, C.DEFAULT_INPUT, G, M): 41,
                        (40, C.NAME, G, M): "Speakers", (41, C.NAME, G, M): "Mic",
                        (40, C.VOLUME_SCALAR, O, M): 0.5, (40, C.MUTE, O, M): 1})
    assert [q() for q in QUERIES] == ["Speakers", "Mic", 0.5, True]


def test_volume_falls_back_to_the_mean_of_channels_one_and_two(monkeypatch):
    _fake(monkeypatch, {(1, C.DEFAULT_OUTPUT, G, M): 40,
                        (40, C.VOLUME_SCALAR, O, 1): 0.25, (40, C.VOLUME_SCALAR, O, 2): 0.75})
    assert coreaudio.output_volume() == pytest.approx(0.5)
    assert coreaudio.output_muted() is None        # no mute control: unknown


def test_a_device_with_no_volume_control_is_unknown(monkeypatch):
    _fake(monkeypatch, {(1, C.DEFAULT_OUTPUT, G, M): 40, (40, C.NAME, G, M): "HDMI"})
    assert coreaudio.output_volume() is None


def test_no_default_device_is_none(monkeypatch):
    _fake(monkeypatch, {(1, C.DEFAULT_OUTPUT, G, M): 0, (1, C.DEFAULT_INPUT, G, M): 0})
    assert [q() for q in QUERIES] == [None] * 4


def test_a_failing_osstatus_is_none(monkeypatch):
    _fake(monkeypatch, {(1, C.DEFAULT_OUTPUT, G, M): 40, (40, C.NAME, G, M): "Speakers"},
          status=-50)
    assert [q() for q in QUERIES] == [None] * 4


@pytest.mark.skipif(sys.platform != "darwin", reason="CoreAudio is darwin's")
def test_the_real_coreaudio_answers_with_the_right_types(fresh_libs):
    """Reads properties only; opens no stream, plays nothing."""
    for q, t in zip(QUERIES, (str, str, float, bool)):
        v = q()
        assert v is None or isinstance(v, t)
    v = coreaudio.output_volume()
    assert v is None or 0.0 <= v <= 1.0
