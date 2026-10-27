"""EARS-212 do-004: the separator's runner, separator/run.py, and its venv's lock.

The runner runs in its own venv (demucs 4.1.0 on torch 2.14.1), never the mouth's. Here it is
driven with a FAKE model object, numpy only: the suite never imports torch, demucs or
soundfile, and never loads or fetches a real model. What is held:

- the protocol: s16le stereo 44.1 kHz PCM on stdin, five outputs under --out, ONE JSON line,
  exit 0 / 1 / 2, a model file refused unless its full SHA-256 matches;
- the outputs: the four stems and the instrumental, which is drums + bass + other;
- the clip scaling: ONE gain for all five, so the stems still sum to the instrumental;
- the levels: taken before that scaling, so the speech check reads the true ratio;
- R12: torch and demucs are in no pyproject.toml dependency list and no wheel requirement;
- the lock: hash-complete (tests/test_service_hash_pins.py lists it in LOCKS), demucs 4.1.0,
  the speech venv's torch, no torchaudio, nothing built from an sdist.

A real install and one real split are verify-001's hand receipt, outside `make test`."""
from __future__ import annotations

import importlib.util
import io
import json
import math
import re
import sys
import types
import wave
from importlib.resources import files
from pathlib import Path

import numpy as np
import pytest

from mouth import separator, sidecar
from tests.test_service_build_constraints import sdist_only
from tests.test_service_hash_pins import entries

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    tomllib = pytest.importorskip("tomli")

REPO = Path(__file__).resolve().parents[1]
RUNNER = REPO / "separator" / "run.py"
HEAVY = ("torch", "demucs", "soundfile", "torchaudio")


def _load_runner():
    before = set(sys.modules)
    spec = importlib.util.spec_from_file_location("separator_run_under_test", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, set(sys.modules) - before


run, IMPORTED = _load_runner()


# ---- fakes ---------------------------------------------------------------------------------

class FakeModel:
    """A model whose sources are fixed multiples of its (normalised) input, in Demucs's order.

    GAINS maps a source to its multiple. It records what apply() was given."""
    sources = ["drums", "bass", "other", "vocals"]

    def __init__(self, **gains):
        self.gains = {s: gains.get(s, 0.25) for s in self.sources}
        self.seen = []

    def apply(self, x):
        self.seen.append(x.copy())
        return np.stack([self.gains[s] * x for s in self.sources])


class Recorder:
    """A writer that keeps what it was handed, by path under the output folder."""

    def __init__(self, root):
        self.root, self.got = Path(root), {}

    def __call__(self, path, data, rate):
        assert rate == run.RATE
        self.got[Path(path).relative_to(self.root).as_posix()] = np.array(data, copy=True)
        Path(path).write_bytes(b"")


def wav_writer(path, data, rate):
    """16-bit WAV with stdlib wave: what the FLAC writer does, without soundfile."""
    pcm = np.clip(np.round(data.T * 32768.0), -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(data.shape[0])
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def wav_read(path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        raw = w.readframes(w.getnframes())
        ch = w.getnchannels()
    return (np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0).reshape(-1, ch).T


def tone(seconds=0.5, amp=0.5, dc=0.0, rate=44100) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    left = amp * np.sin(2 * np.pi * 220 * t) + dc
    right = amp * np.sin(2 * np.pi * 330 * t) + dc
    return np.stack([left, right]).astype(np.float32)


def pcm_bytes(mix: np.ndarray) -> bytes:
    return np.clip(np.round(mix.T * 32768.0), -32768, 32767).astype("<i2").tobytes()


@pytest.fixture
def model_file(tmp_path):
    path = tmp_path / "model" / "955717e8.safetensors"
    path.parent.mkdir()
    path.write_bytes(b"not a real model: the loader is faked")
    return path, run.sha256_of(path)


def fake_loader(model, calls=None):
    def load(path, threads):
        if calls is not None:
            calls.append((path, threads))
        return model, {"demucs": "4.1.0", "torch": "2.14.1"}
    return load


# ---- the module stays light ------------------------------------------------------------------

def test_loading_the_runner_imports_no_torch_demucs_or_soundfile():
    assert not {m for m in IMPORTED if m.split(".")[0] in HEAVY}


def test_the_runner_never_uses_torchaudio_the_demucs_cli_or_its_file_io():
    text = RUNNER.read_text()
    code = text.split('"""', 2)[2]                       # past the docstring, which names them
    assert not re.search(r"^\s*(import|from)\s+torchaudio\b", code, re.M)
    assert "demucs.api" not in code and "demucs.separate" not in code
    assert "subprocess" not in code                      # no demucs command, no ffmpeg
    assert "hf_hub_download" not in code and "get_hf_model" not in code   # never the hub


def test_the_runner_writes_the_sidecars_layout():
    """The paths mouth.sidecar reads, under the folder the caller renames into place."""
    assert run.STEMS == sidecar.STEMS
    assert set(run.OUTPUTS) == {*sidecar.STEMS, "instrumental"}
    for stem in sidecar.STEMS:
        assert run.OUTPUTS[stem] == f"stems/{stem}.flac"
    assert run.OUTPUTS["instrumental"] == sidecar.INSTRUMENTAL_NAMES[0] + ".flac"
    assert all(p.endswith("." + sidecar.EXTENSIONS[0]) for p in run.OUTPUTS.values())


# ---- separate and write: the outputs, the sum ------------------------------------------------

def test_the_five_outputs_are_written(tmp_path):
    rec = Recorder(tmp_path)
    report = run.separate_and_write(tone(), FakeModel(), tmp_path, writer=rec)
    assert sorted(rec.got) == sorted(run.OUTPUTS.values())
    for rel in run.OUTPUTS.values():
        assert (tmp_path / rel).is_file()
        assert rec.got[rel].shape == tone().shape and rec.got[rel].dtype == np.float32
    assert report["outputs"] == run.OUTPUTS


def test_the_instrumental_is_drums_plus_bass_plus_other(tmp_path):
    rec = Recorder(tmp_path)
    run.separate_and_write(tone(), FakeModel(drums=0.1, bass=0.2, other=0.3, vocals=0.4),
                           tmp_path, writer=rec)
    o = {name: rec.got[rel] for name, rel in run.OUTPUTS.items()}
    np.testing.assert_allclose(o["instrumental"], o["drums"] + o["bass"] + o["other"], atol=1e-6)
    assert not np.allclose(o["instrumental"], o["vocals"])   # vocals are not in it


def test_each_stem_is_the_models_source_of_that_name():
    """The model's own order (drums, bass, other, vocals) is mapped by name, not position."""
    mix = tone()
    stems = run.separate(mix, FakeModel(drums=0.0, bass=0.0, other=0.0, vocals=1.0))
    np.testing.assert_allclose(stems["vocals"], mix, atol=1e-5)


def test_the_mix_is_normalised_for_the_model_and_restored_after():
    """As Demucs's own separate: the mono reference's mean off and its std divided out."""
    mix = tone(amp=0.3, dc=0.1)
    model = FakeModel(drums=0.0, bass=0.0, other=0.0, vocals=1.0)
    stems = run.separate(mix, model)
    x, = model.seen
    assert abs(float(x.mean(axis=0).mean())) < 1e-5
    assert abs(float(x.mean(axis=0).std()) - 1.0) < 1e-3
    np.testing.assert_allclose(stems["vocals"], mix, atol=1e-5)      # mean and std put back


def test_silence_separates_without_dividing_by_zero(tmp_path):
    rec = Recorder(tmp_path)
    report = run.separate_and_write(np.zeros((2, 4410), np.float32), FakeModel(), tmp_path,
                                    writer=rec)
    assert all(np.isfinite(a).all() for a in rec.got.values())
    assert report["levels_db"] == {"mix": run.LEVEL_FLOOR_DB, "vocals": run.LEVEL_FLOOR_DB,
                                   "instrumental": run.LEVEL_FLOOR_DB}
    assert report["peak_scaled_db"] is None


def test_a_model_missing_a_stem_fails(tmp_path):
    class ThreeStems(FakeModel):
        sources = ["drums", "bass", "vocals"]
    with pytest.raises(run.Failed, match="no other source"):
        run.separate_and_write(tone(), ThreeStems(), tmp_path, writer=Recorder(tmp_path))


def test_a_model_returning_the_wrong_shape_fails(tmp_path):
    class Short(FakeModel):
        def apply(self, x):
            return super().apply(x)[:, :, :-1]
    with pytest.raises(run.Failed, match="shape"):
        run.separate(tone(), Short())


# ---- the clip scaling: one gain for all five --------------------------------------------------

def test_nothing_that_fits_is_scaled(tmp_path):
    rec = Recorder(tmp_path)
    mix = tone(amp=0.2)
    report = run.separate_and_write(mix, FakeModel(), tmp_path, writer=rec)
    assert report["peak_scaled_db"] is None
    unscaled = run.outputs_of(run.separate(mix, FakeModel()))
    for name, rel in run.OUTPUTS.items():
        np.testing.assert_array_equal(rec.got[rel], unscaled[name])


def test_a_clipping_instrumental_scales_all_five_by_one_gain(tmp_path):
    """Each stem fits, but their sum does not: every output takes the same gain, so the
    written stems still sum to the written instrumental, and their balance holds."""
    mix = tone(amp=0.9)
    model = FakeModel(drums=0.5, bass=0.5, other=0.5, vocals=0.2)
    unscaled = run.outputs_of(run.separate(mix, FakeModel(drums=0.5, bass=0.5, other=0.5,
                                                          vocals=0.2)))
    assert max(float(np.abs(unscaled[s]).max()) for s in run.STEMS) <= run.CLIP_LIMIT
    peak = float(np.abs(unscaled["instrumental"]).max())
    assert peak > 1.0
    rec = Recorder(tmp_path)
    report = run.separate_and_write(mix, model, tmp_path, writer=rec)
    o = {name: rec.got[rel] for name, rel in run.OUTPUTS.items()}
    gain = run.CLIP_LIMIT / peak
    for name in run.OUTPUTS:                                   # one gain, all five
        np.testing.assert_allclose(o[name], unscaled[name] * gain, atol=1e-6)
    assert max(float(np.abs(a).max()) for a in o.values()) <= run.CLIP_LIMIT + 1e-7
    np.testing.assert_allclose(o["instrumental"], o["drums"] + o["bass"] + o["other"], atol=1e-6)
    assert report["peak_scaled_db"] == round(20 * math.log10(gain), 2) < 0


def test_a_clipping_stem_scales_the_instrumental_too(tmp_path):
    mix = tone(amp=0.9)
    model = FakeModel(drums=0.0, bass=0.0, other=0.0, vocals=1.5)
    rec = Recorder(tmp_path)
    report = run.separate_and_write(mix, model, tmp_path, writer=rec)
    unscaled = run.outputs_of(run.separate(mix, FakeModel(drums=0.0, bass=0.0, other=0.0,
                                                          vocals=1.5)))
    gain = run.CLIP_LIMIT / float(np.abs(unscaled["vocals"]).max())
    np.testing.assert_allclose(rec.got["mixes/instrumental.flac"],
                               unscaled["instrumental"] * gain, atol=1e-6)
    assert report["peak_scaled_db"] < 0


def test_the_levels_are_taken_before_the_scaling(tmp_path):
    mix = tone(amp=0.9)
    model = FakeModel(drums=0.5, bass=0.5, other=0.5, vocals=0.2)
    unscaled = run.outputs_of(run.separate(mix, FakeModel(drums=0.5, bass=0.5, other=0.5,
                                                          vocals=0.2)))
    rec = Recorder(tmp_path)
    report = run.separate_and_write(mix, model, tmp_path, writer=rec)
    assert report["peak_scaled_db"] is not None
    levels = report["levels_db"]
    assert levels["mix"] == run.level_db(mix)
    assert levels["vocals"] == run.level_db(unscaled["vocals"])
    assert levels["instrumental"] == run.level_db(unscaled["instrumental"])
    assert levels["instrumental"] != run.level_db(rec.got["mixes/instrumental.flac"])


def test_the_speech_check_reads_the_runners_levels():
    """A quiet instrumental under a loud mix is speech, by mouth.sidecar's own check."""
    mix = tone(amp=0.5)
    speech = run.outputs_of(run.separate(mix, FakeModel(drums=0.01, bass=0.01, other=0.01,
                                                        vocals=1.0)))
    music = run.outputs_of(run.separate(mix, FakeModel()))
    assert sidecar.speech_verdict(run.level_db(mix), run.level_db(speech["instrumental"])) == "speech"
    assert sidecar.speech_verdict(run.level_db(mix), run.level_db(music["instrumental"])) == "music"


def test_a_level_is_rms_in_dbfs():
    full = np.ones((2, 100), np.float32)
    assert run.level_db(full) == 0.0
    assert run.level_db(full * 0.1) == pytest.approx(-20.0, abs=0.01)
    assert run.level_db(np.zeros((2, 0), np.float32)) == run.LEVEL_FLOOR_DB
    assert run.level_db(np.full((2, 10), 1e-9, np.float32)) == run.LEVEL_FLOOR_DB


# ---- in place: the same outputs, without the copies ---------------------------------------
#
# The post-processing after the model works in place (the sources are four times the mix, so
# each copy of them is a 20-minute song's 1.7 GB). The functions below are the runner's code
# from before that change, kept word for word as the reference: the outputs must not move.

def _separate_before(mix, model):
    ref = mix.mean(axis=0)
    mean, std = float(ref.mean()), float(ref.std()) + 1e-8
    out = np.asarray(model.apply((mix - mean) / std), dtype=np.float32)
    sources = list(model.sources)
    if out.shape != (len(sources), *mix.shape):
        raise run.Failed(f"the model returned shape {out.shape}, not {(len(sources), *mix.shape)}")
    missing = [s for s in run.STEMS if s not in sources]
    if missing:
        raise run.Failed(f"the model has no {', '.join(missing)} source (it has {', '.join(sources)})")
    out = out * std + mean
    return {name: out[sources.index(name)] for name in run.STEMS}


def _outputs_of_before(stems):
    inst = sum(stems[p] for p in run.INSTRUMENTAL_PARTS)
    return {**{s: stems[s] for s in run.STEMS}, "instrumental": inst.astype(np.float32)}


def _separate_and_write_before(mix, model, out_dir, writer, rate=run.RATE):
    out_dir = Path(out_dir)
    outputs = _outputs_of_before(_separate_before(mix, model))
    levels = {"mix": run.level_db(mix), "vocals": run.level_db(outputs["vocals"]),
              "instrumental": run.level_db(outputs["instrumental"])}
    gain = run.clip_gain(outputs)
    for name, rel in run.OUTPUTS.items():
        path = out_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        data = outputs[name] if gain == 1.0 else outputs[name] * np.float32(gain)
        writer(path, data, rate)
    return {"outputs": dict(run.OUTPUTS), "levels_db": levels,
            "peak_scaled_db": None if gain == 1.0 else round(20.0 * math.log10(gain), 2)}


def _noise(seconds=2.0, amp=0.3, dc=0.0, seed=7):
    """Stereo noise over a tone: every sample a different value, so a reordered sum shows."""
    rng = np.random.default_rng(seed)
    n = int(seconds * run.RATE)
    return (tone(seconds, amp) + rng.normal(0.0, amp / 3, (2, n)) + dc).astype(np.float32)


IN_PLACE_CASES = {
    "a tone": (lambda: tone(seconds=1.0, amp=0.4), {}),
    "noise with an offset": (lambda: _noise(dc=0.05),
                             {"drums": 0.31, "bass": 0.17, "other": 0.43, "vocals": 0.09}),
    "an instrumental that clips": (lambda: _noise(amp=0.6),
                                   {"drums": 0.7, "bass": 0.6, "other": 0.5, "vocals": 0.2}),
    "a vocal that clips": (lambda: tone(amp=0.9), {"drums": 0.0, "bass": 0.0, "other": 0.0,
                                                   "vocals": 1.5}),
    "silence": (lambda: np.zeros((2, 4410), np.float32), {}),
}


@pytest.mark.parametrize("case", IN_PLACE_CASES)
def test_in_place_post_processing_writes_the_same_outputs_as_before(tmp_path, case):
    """Same arrays handed to the writer, byte-equal written files, the same report."""
    make_mix, gains = IN_PLACE_CASES[case]
    runs = {}
    for side, fn in (("before", _separate_and_write_before), ("after", run.separate_and_write)):
        rec = Recorder(tmp_path / side)
        report = fn(make_mix(), FakeModel(**gains), tmp_path / side, writer=rec)
        wav_dir = tmp_path / f"{side}-wav"
        fn(make_mix(), FakeModel(**gains), wav_dir, writer=wav_writer)
        files_ = {rel: (wav_dir / rel).read_bytes() for rel in run.OUTPUTS.values()}
        runs[side] = rec.got, files_, report
    (got_b, files_b, report_b), (got_a, files_a, report_a) = runs["before"], runs["after"]
    assert sorted(got_a) == sorted(got_b) == sorted(run.OUTPUTS.values())
    for rel in run.OUTPUTS.values():
        assert got_a[rel].dtype == got_b[rel].dtype == np.float32
        np.testing.assert_array_equal(got_a[rel], got_b[rel], err_msg=rel)   # exact, not close
        assert files_a[rel] == files_b[rel], rel                             # byte for byte
    assert report_a == report_b
    if "clips" in case:
        assert report_a["peak_scaled_db"] is not None                       # the gain path ran


def test_in_place_post_processing_leaves_the_mix_untouched(tmp_path):
    mix = _noise(dc=0.05)
    kept = mix.copy()
    run.separate_and_write(mix, FakeModel(drums=0.7, bass=0.6, other=0.5), tmp_path,
                           writer=Recorder(tmp_path))
    np.testing.assert_array_equal(mix, kept)


def test_a_model_array_that_is_read_only_or_a_broadcast_is_never_scaled_in_place():
    """In place is only ever in an array the model handed over whole: a read-only result
    is copied first and left as it was; a broadcast (one sample seen four times) is copied
    first, so no sample is scaled four times over."""
    mix = _noise()
    kept = mix.copy()

    class ReadOnly(FakeModel):
        def apply(self, x):
            self.result = super().apply(x)
            self.result.flags.writeable = False
            return self.result

    class Echo(FakeModel):                          # four writeable views of what it is given
        def apply(self, x):
            return np.lib.stride_tricks.as_strided(x, (4, *x.shape), (0, *x.strides),
                                                   writeable=True)

    read_only = ReadOnly(drums=0.2, bass=0.3, other=0.1, vocals=0.4)
    stems = run.separate(mix, read_only)
    assert not read_only.result.flags.writeable
    np.testing.assert_array_equal(
        read_only.result, ReadOnly(drums=0.2, bass=0.3, other=0.1, vocals=0.4).apply(
            (mix - float(mix.mean(axis=0).mean())) / (float(mix.mean(axis=0).std()) + 1e-8)))
    expected = _separate_before(mix, ReadOnly(drums=0.2, bass=0.3, other=0.1, vocals=0.4))
    for name in run.STEMS:
        np.testing.assert_array_equal(stems[name], expected[name])
    echo = run.separate(mix, Echo())
    np.testing.assert_allclose(echo["vocals"], mix, atol=1e-5)
    np.testing.assert_array_equal(mix, kept)


def test_the_instrumental_is_one_new_array_and_the_stems_are_views():
    """The memory shape the change is for: each stem is a view into the model's one array,
    and the instrumental is one array of its own, never a view of a stem."""
    model = FakeModel()
    stems = run.separate(tone(), model)
    bases = {id(s.base) for s in stems.values()}
    assert all(s.base is not None for s in stems.values()) and len(bases) == 1
    outputs = run.outputs_of(stems)
    assert outputs["instrumental"].dtype == np.float32
    assert not any(np.shares_memory(outputs["instrumental"], stems[s]) for s in run.STEMS)
    for s in run.STEMS:
        assert outputs[s] is stems[s]


# ---- the protocol: stdin, the JSON line, exit codes --------------------------------------------

def test_stdin_pcm_is_interleaved_stereo_and_a_partial_frame_is_dropped():
    frames = np.array([[1000, -1000], [2000, -2000], [3000, -3000]], "<i2").tobytes()
    mix = run.read_pcm(io.BytesIO(frames + b"\x01\x02\x03"))
    assert mix.shape == (2, 3) and mix.dtype == np.float32
    np.testing.assert_allclose(mix[0], np.array([1000, 2000, 3000]) / 32768.0)
    np.testing.assert_allclose(mix[1], np.array([-1000, -2000, -3000]) / 32768.0)


def _main(tmp_path, model_file, stdin: bytes, loader, *extra, writer=wav_writer):
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    path, sha = model_file
    stdout = io.StringIO()
    argv = ["--out", str(out), "--model", str(path), "--sha256", sha, *extra]
    code = run.main(argv, stdin=io.BytesIO(stdin), stdout=stdout, loader=loader, writer=writer)
    lines = stdout.getvalue().splitlines()
    assert len(lines) == 1, lines                                  # ONE JSON line, always
    return code, json.loads(lines[0]), out


def test_a_split_writes_the_files_and_prints_one_json_line(tmp_path, model_file):
    mix = tone(seconds=1.0, amp=0.4)
    calls = []
    code, line, out = _main(tmp_path, model_file, pcm_bytes(mix), fake_loader(FakeModel(), calls),
                            "--threads", "3")
    assert code == 0 and line["ok"] is True
    assert calls == [(model_file[0], 3)]
    assert line["outputs"] == run.OUTPUTS
    assert line["separator"] == "demucs" and line["signature"] == "955717e8"
    assert line["versions"] == {"demucs": "4.1.0", "torch": "2.14.1"}
    assert line["device"] == "cpu" and line["threads"] == 3
    assert (line["rate"], line["channels"]) == (44100, 2)
    assert line["duration_s"] == 1.0 and line["split_s"] >= 0
    assert set(line["levels_db"]) == {"mix", "vocals", "instrumental"}
    assert line["peak_scaled_db"] is None
    got = {name: wav_read(out / rel) for name, rel in run.OUTPUTS.items()}
    assert all(a.shape == mix.shape for a in got.values())
    np.testing.assert_allclose(got["instrumental"], got["drums"] + got["bass"] + got["other"],
                               atol=4 / 32768)                   # three roundings of 16 bits


def test_the_default_threads_leave_two_cores(monkeypatch, tmp_path, model_file):
    monkeypatch.setattr(run.os, "cpu_count", lambda: 10)
    calls = []
    code, line, _ = _main(tmp_path, model_file, pcm_bytes(tone()), fake_loader(FakeModel(), calls))
    assert code == 0 and calls[0][1] == 8 and line["threads"] == 8
    monkeypatch.setattr(run.os, "cpu_count", lambda: 2)
    assert run.default_threads() == 1
    monkeypatch.setattr(run.os, "cpu_count", lambda: None)
    assert run.default_threads() == 1


def test_a_model_file_that_is_not_the_pinned_bytes_is_refused_unloaded(tmp_path, model_file):
    path, sha = model_file
    calls = []
    wrong = ("0" if sha[0] != "0" else "1") + sha[1:]
    code, line, out = _main(tmp_path, (path, wrong), pcm_bytes(tone()),
                            fake_loader(FakeModel(), calls))
    assert code == 2 and line["ok"] is False and "sha256" in line["detail"]
    assert calls == [] and list(out.iterdir()) == []


@pytest.mark.parametrize("why, argv_patch, detail", [
    ("no model file", {"--model": "missing.safetensors"}, "no model file"),
    ("not a model kind", {"--model": "model.pkl"}, ".safetensors or a .th"),
    ("a short sha", {"--sha256": "8726e21a"}, "64 hex digits"),
    ("no output folder", {"--out": "nowhere"}, "no output folder"),
    ("no threads", {"--threads": "0"}, "--threads"),
])
def test_bad_arguments_are_refused_before_any_load(tmp_path, model_file, why, argv_patch, detail):
    out = tmp_path / "out"
    out.mkdir()
    path, sha = model_file
    args = {"--out": str(out), "--model": str(path), "--sha256": sha}
    for k, v in argv_patch.items():
        args[k] = v if k in ("--sha256", "--threads") else str(tmp_path / v)
    argv = [x for kv in args.items() for x in kv]
    calls, stdout = [], io.StringIO()
    code = run.main(argv, stdin=io.BytesIO(pcm_bytes(tone())), stdout=stdout,
                    loader=fake_loader(FakeModel(), calls), writer=wav_writer)
    line = json.loads(stdout.getvalue())
    assert code == 2 and line["ok"] is False and detail in line["detail"], why
    assert calls == []


def test_a_missing_argument_is_refused_as_one_json_line(tmp_path):
    stdout = io.StringIO()
    code = run.main(["--out", str(tmp_path)], stdin=io.BytesIO(b""), stdout=stdout,
                    loader=fake_loader(FakeModel()), writer=wav_writer)
    assert code == 2 and json.loads(stdout.getvalue())["ok"] is False


def test_no_audio_on_stdin_is_refused(tmp_path, model_file):
    calls = []
    code, line, _ = _main(tmp_path, model_file, b"\x00", fake_loader(FakeModel(), calls))
    assert code == 2 and line["detail"] == "no audio on stdin" and calls == []


def test_a_model_that_cannot_load_fails_with_exit_1(tmp_path, model_file):
    def broken(path, threads):
        raise RuntimeError("bad checkpoint")
    code, line, _ = _main(tmp_path, model_file, pcm_bytes(tone()), broken)
    assert code == 1 and line == {"ok": False, "detail": "RuntimeError: bad checkpoint"}


def test_a_write_that_fails_fails_with_exit_1(tmp_path, model_file):
    def full(path, data, rate):
        raise OSError(28, "No space left on device")
    code, line, _ = _main(tmp_path, model_file, pcm_bytes(tone()), fake_loader(FakeModel()),
                          writer=full)
    assert code == 1 and "No space left" in line["detail"]


# ---- the model loader, with torch and demucs faked ------------------------------------------

@pytest.fixture
def fake_demucs(monkeypatch):
    """Stand-in torch and demucs modules: what load_demucs calls, recorded."""
    seen = {}

    class Model:
        sources = FakeModel.sources
        samplerate, audio_channels = 44100, 2

        def eval(self):
            seen["eval"] = True
            return self

    torch = types.ModuleType("torch")
    torch.__version__ = "2.14.1"
    torch.set_num_threads = lambda n: seen.__setitem__("threads", n)
    demucs = types.ModuleType("demucs")
    demucs.__version__ = "4.1.0"
    hf = types.ModuleType("demucs.hf")
    hf.load_safetensors_model = lambda path: (seen.__setitem__("safetensors", path), Model())[1]
    pretrained = types.ModuleType("demucs.pretrained")
    pretrained.get_model = lambda name, repo=None: (seen.__setitem__("get_model", (name, repo)),
                                                    Model())[1]
    for name, mod in {"torch": torch, "demucs": demucs, "demucs.hf": hf,
                      "demucs.pretrained": pretrained}.items():
        monkeypatch.setitem(sys.modules, name, mod)
    seen["Model"] = Model
    return seen


def test_a_safetensors_model_loads_by_its_path(fake_demucs, tmp_path):
    path = tmp_path / "955717e8.safetensors"
    model, versions = run.load_demucs(path, 6)
    assert fake_demucs["safetensors"] == path and "get_model" not in fake_demucs
    assert fake_demucs["threads"] == 6 and fake_demucs["eval"]
    assert model.sources == FakeModel.sources
    assert versions == {"demucs": "4.1.0", "torch": "2.14.1"}


def test_a_th_model_loads_from_its_own_folder_never_the_hub(fake_demucs, tmp_path):
    path = tmp_path / "955717e8-8726e21a.th"
    run.load_demucs(path, 2)
    assert fake_demucs["get_model"] == ("955717e8", tmp_path)       # a local repo, by path


def test_a_model_of_another_rate_fails(fake_demucs, tmp_path):
    fake_demucs["Model"].samplerate = 48000
    with pytest.raises(run.Failed, match="48000 Hz"):
        run.load_demucs(tmp_path / "955717e8.safetensors", 2)


# ---- R12: never a dependency of the wheel --------------------------------------------------

def _pyproject_requirements() -> dict[str, list[str]]:
    conf = tomllib.loads((REPO / "pyproject.toml").read_text())
    lists = {"dependencies": conf["project"]["dependencies"],
             "build-system": conf["build-system"]["requires"]}
    for extra, reqs in conf["project"].get("optional-dependencies", {}).items():
        lists[f"extra {extra}"] = reqs
    for group, reqs in conf.get("dependency-groups", {}).items():
        lists[f"group {group}"] = [r for r in reqs if isinstance(r, str)]
    return lists


def _name(req: str) -> str:
    return re.sub(r"[-_.]+", "-", re.match(r"[A-Za-z0-9._-]+", req.strip()).group(0)).lower()


def test_torch_and_demucs_are_in_no_pyproject_dependency_list():
    lists = _pyproject_requirements()
    assert lists["dependencies"]                                     # the reader found them
    found = {where: sorted({_name(r) for r in reqs} & {"torch", "demucs", "torchaudio"})
             for where, reqs in lists.items()}
    assert {w: f for w, f in found.items() if f} == {}


def test_uv_lock_installs_no_torch_or_demucs():
    names = set(re.findall(r'^name = "([^"]+)"$', (REPO / "uv.lock").read_text(), re.M))
    assert "numpy" in names and not names & {"torch", "demucs", "torchaudio"}


def test_the_runner_ships_as_package_data_not_a_module():
    conf = tomllib.loads((REPO / "pyproject.toml").read_text())
    assert conf["tool"]["setuptools"]["package-data"]["mouth"] == ["_separator/*"]
    assert not (REPO / "src/mouth/_separator/__init__.py").exists()


# ---- the shipped files and the lock ---------------------------------------------------------

@pytest.mark.parametrize("name", separator.SHIPPED)
def test_the_package_holds_the_checkouts_bytes(name):
    got = files("mouth") / "_separator" / name
    assert got.is_file() and got.read_bytes() == (REPO / "separator" / name).read_bytes()


def test_the_package_ships_exactly_the_runner_and_the_lock():
    pkg = REPO / "src/mouth/_separator"
    assert sorted(p.name for p in pkg.iterdir() if p.is_file()) == sorted(separator.SHIPPED)


def test_a_checkout_finds_its_own_files():
    assert separator.separator_file("run.py") == RUNNER


def test_from_a_wheel_the_files_are_the_shipped_ones(monkeypatch, tmp_path):
    monkeypatch.setattr(separator, "ROOT", tmp_path)                # installed: no separator/
    got = separator.separator_file("requirements.lock")
    assert got.parent.name == "_separator" and got.parent.parent.name == "mouth"
    with pytest.raises(separator.NoSeparatorFile, match="no nothing.txt"):
        separator.separator_file("nothing.txt")


def _pins(lock: str) -> dict[str, str]:
    """name -> version, for each index pin (a direct-URL pin has no `==`)."""
    return dict(r.split()[0].split("==", 1) for r in entries(lock) if "==" in r.split()[0])


def test_the_lock_pins_demucs_4_1_0_on_the_speech_venvs_torch():
    pins = _pins(separator.separator_file("requirements.lock").read_text())
    speech = _pins((REPO / "speech" / "requirements.lock").read_text())
    assert pins["demucs"] == "4.1.0"
    assert pins["torch"] == "2.14.1" == speech["torch"]
    assert {"numpy", "soundfile", "safetensors", "pyyaml"} <= set(pins)
    assert "torchaudio" not in pins and "openunmix" not in pins and "dora-search" not in pins


def test_the_lock_builds_nothing():
    """Every pin is a wheel, so no build backend is fetched and none is pinned."""
    lock = separator.separator_file("requirements.lock").read_text()
    assert sdist_only(lock) == set()
    assert all("://" not in r.split(" --hash")[0] for r in entries(lock))
    assert not (REPO / "separator" / "build-constraints.txt").exists()


def test_the_source_lists_what_the_runner_needs():
    src = (REPO / "separator" / "requirements.in").read_text()
    reqs = [l.strip() for l in src.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    assert reqs == ["demucs==4.1.0", "torch==2.14.1", "numpy", "soundfile"]
    assert "--python-version 3.12" in src and "aarch64-apple-darwin" in src
