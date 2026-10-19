"""Every import an optional extra brings fails naming that extra (EARS-160).

A bare wheel has the base dependencies only. Each lazy import of an extra's module goes
through ``ears.extras.need``; these tests take the module away (``None`` in
``sys.modules``) and read the message at every call site. The fallbacks that catch an
ImportError (``open_vad("auto")``, ``default_classifier``) must still fall back.
"""
from __future__ import annotations

import os
import subprocess
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from ears.extras import MissingExtra, need

EVAL_MLX = Path(__file__).resolve().parent.parent / "src" / "ears" / "eval_mlx.py"


def block(monkeypatch, *names):
    """Make each module (and any submodule already loaded) unimportable for one test."""
    for name in names:
        for loaded in [m for m in sys.modules if m == name or m.startswith(name + ".")]:
            monkeypatch.setitem(sys.modules, loaded, None)
        monkeypatch.setitem(sys.modules, name, None)


def install_line(extra):
    return f"pip install 'sessionvoice[{extra}]'"


# -- the helper ------------------------------------------------------------------------


def test_need_returns_the_module_when_it_is_there():
    import json

    assert need("json", "capture") is json


def test_need_names_the_extra_and_is_an_import_error(monkeypatch):
    block(monkeypatch, "onnxruntime")
    with pytest.raises(MissingExtra) as e:
        need("onnxruntime", "silero", "Silero VAD")
    assert isinstance(e.value, ImportError)
    assert str(e.value) == f"Silero VAD needs onnxruntime, which is not installed: {install_line('silero')}"
    assert (e.value.name, e.value.extra, e.value.install) == ("onnxruntime", "silero", install_line("silero"))


def test_need_without_a_purpose_still_names_the_extra(monkeypatch):
    block(monkeypatch, "scipy")
    with pytest.raises(MissingExtra, match=r"^scipy is needed, which is not installed: pip install 'sessionvoice\[capture\]'$"):
        need("scipy.signal", "capture")


def test_need_leaves_a_broken_install_alone(monkeypatch, tmp_path):
    """The module is there but one of ITS imports is not: the extra would not fix that."""
    pkg = tmp_path / "ears160_present"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("import ears160_absent_dep\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    with pytest.raises(ModuleNotFoundError) as e:
        need("ears160_present", "capture")
    assert not isinstance(e.value, MissingExtra)
    assert e.value.name == "ears160_absent_dep"


# -- every call site -------------------------------------------------------------------


def test_resample_names_capture(monkeypatch):
    from ears.listen.sources import _resample

    block(monkeypatch, "scipy")
    with pytest.raises(MissingExtra, match=r"Resampling to 16 kHz needs scipy.*" + r"\[capture\]"):
        _resample(np.zeros(480, np.int16), 48000)


def test_smart_turn_onnx_names_smart_turn_for_onnxruntime(monkeypatch):
    from ears.smartturn import SmartTurnModel

    block(monkeypatch, "onnxruntime")
    with pytest.raises(MissingExtra, match=r"Smart Turn \(onnx\) needs onnxruntime.*\[smart-turn\]"):
        SmartTurnModel(backend="onnx")


def test_smart_turn_onnx_names_smart_turn_for_transformers(monkeypatch):
    from ears.smartturn import SmartTurnModel

    monkeypatch.setitem(sys.modules, "onnxruntime", types.ModuleType("onnxruntime"))
    block(monkeypatch, "transformers")
    with pytest.raises(MissingExtra, match=r"Smart Turn \(onnx\) needs transformers.*\[smart-turn\]"):
        SmartTurnModel(backend="onnx")


def test_silero_vad_names_silero(monkeypatch):
    from ears.vad import SileroVAD

    block(monkeypatch, "onnxruntime")
    with pytest.raises(MissingExtra, match=r"Silero VAD needs onnxruntime.*\[silero\]"):
        SileroVAD()


def test_open_vad_auto_still_falls_back_and_silero_still_raises(monkeypatch):
    from ears.vad import EnergyVAD, open_vad

    block(monkeypatch, "onnxruntime")
    assert isinstance(open_vad("auto"), EnergyVAD)
    with pytest.raises(MissingExtra, match=r"\[silero\]"):
        open_vad("silero")


def test_listen_silero_classifier_names_silero(monkeypatch):
    from ears.listen.silero import SileroClassifier

    block(monkeypatch, "onnxruntime")
    with pytest.raises(MissingExtra, match=r"Silero VAD needs onnxruntime.*\[silero\]"):
        SileroClassifier()


def test_webrtc_classifier_names_capture_and_the_default_still_falls_back(monkeypatch):
    from ears.listen.detector import EnergyClassifier, WebRtcClassifier, default_classifier

    block(monkeypatch, "webrtcvad")
    with pytest.raises(MissingExtra, match=r"The webrtc VAD needs webrtcvad.*\[capture\]"):
        WebRtcClassifier()
    assert isinstance(default_classifier(), EnergyClassifier)   # silent, by design


def test_redecoder_names_nemotron(monkeypatch):
    from ears.redecode import Redecoder

    block(monkeypatch, "mlx", "mlx_audio")
    with pytest.raises(MissingExtra, match=r"The Parakeet re-decode needs mlx.*\[nemotron\]"):
        Redecoder()


# -- eval_mlx: a FOREIGN interpreter's needs, not an extra -------------------------------


@pytest.fixture
def no_soundfile(tmp_path):
    """A PYTHONPATH on which ``import soundfile`` fails as if it were not installed."""
    d = tmp_path / "nosf"
    d.mkdir()
    (d / "soundfile.py").write_text(
        "raise ModuleNotFoundError(\"No module named 'soundfile'\", name='soundfile')\n")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(filter(None, [str(d), os.environ.get("PYTHONPATH")])))
    return env


def test_eval_mlx_says_what_its_interpreter_needs(no_soundfile, tmp_path):
    p = subprocess.run([sys.executable, str(EVAL_MLX), "parakeet", "m", str(tmp_path / "l.tsv"),
                        str(tmp_path / "o.jsonl")], capture_output=True, text=True, env=no_soundfile)
    assert p.returncode == 1
    last = p.stderr.strip().splitlines()[-1]
    assert last == (f"eval_mlx: {sys.executable} (EARS_EVAL_MLX_PY) lacks soundfile; it needs "
                    f"numpy soundfile mlx-audio: {sys.executable} -m pip install numpy soundfile mlx-audio")


def test_ears_eval_reports_that_line_as_each_clips_error(no_soundfile, tmp_path, monkeypatch):
    from ears import eval as ev

    monkeypatch.setattr(ev, "MLX_PY", sys.executable)
    monkeypatch.setenv("PYTHONPATH", no_soundfile["PYTHONPATH"])
    res = ev.run_engine("parakeet-mlx", [{"id": "c1"}, {"id": "c2"}], tmp_path)
    assert set(res) == {"c1", "c2"}
    for r in res.values():
        assert r["text"] == ""
        assert "(EARS_EVAL_MLX_PY) lacks soundfile" in r["error"]
        assert "-m pip install numpy soundfile mlx-audio" in r["error"]
