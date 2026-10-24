"""speakers/speaker-sidecar2.py: mouth naming (a line this machine's mouth is playing is its
agent's) and naming labels by reference (the order may differ). No model: the side-car's
functions are lifted by name, and the label map runs against a stand-in diarizer."""
import ast, datetime as dt, json, os, subprocess, sys, wave
from pathlib import Path

import numpy as np
import pytest

SC = Path(__file__).resolve().parents[1] / "speakers" / "speaker-sidecar2.py"


def lift(*names):
    ns = {"dt": dt}
    exec("def epoch(ts): return dt.datetime.fromisoformat(ts).timestamp()", ns)
    for node in ast.parse(SC.read_text()).body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            exec(compile(ast.Module([node], []), "sidecar", "exec"), ns)
    return ns


T0 = "2030-06-09T12:00:00"    # synthetic; no offset, so fromisoformat reads it on 3.10
LINE = ("It's ears-tail. Type ears, then tab. The speaker names already have a plan to become "
        "a service inside ears, and that plan waits on your yes.")


@pytest.fixture
def ns():
    ns = lift("words", "note_mouth", "mouth_name", "named_by_mouth")
    ns["mouth"] = {}
    s0 = ns["epoch"](T0)
    ns["note_mouth"]({"kind": "saying", "utt": "u1", "agent": "ivy", "ts": T0, "text": LINE})
    ns["note_mouth"]({"kind": "said", "utt": "u1", "agent": "ivy",
                      "ts": dt.datetime.fromtimestamp(s0 + 20).astimezone().isoformat()})
    ns["s0"] = s0
    return ns


@pytest.mark.parametrize("who,fr,text,at,want", [
    ("other", {"other": 1.0}, "Type ears, then tab.", 4, "ivy"),             # its own line, heard back
    ("other", {"other": 1.0}, "a service inside years and that plan", 12, "ivy"),  # whisper's retyping
    ("dave", {"dave": 1.0}, "the speaker names already", 8, "ivy"),           # an agent ref yields
    ("theo", {"theo": 1.0}, "Type ears, then tab.", 4, "theo"),               # a person talking over it
    ("other", {"other": 1.0}, "Did you get the Pro?", 6, "other"),            # someone else, mid-line
    ("other", {"other": 1.0}, "Type ears, then tab.", 40, "other"),           # long after it ended
    ("mixed", {"theo": 0.65, "other": 0.35}, "It's ears-tail.", 1, "mixed"),  # stays mixed
])
def test_mouth_name(ns, who, fr, text, at, want):
    w0 = ns["s0"] + at
    got, fr2, utt = ns["mouth_name"](who, fr, text, w0, w0 + 1.5, ns["mouth"], 1.5, frozenset({"dave"}))
    assert got == want
    # EARS-360: speaker_by "mouth" exactly when the mouth renamed the label to its agent
    assert ns["named_by_mouth"](who, utt, frozenset({"dave"})) == (want == "ivy")
    if want == "mixed":
        assert fr2 == {"theo": 0.65, "ivy": 0.35}  # the lead still counts for a speaker gate


STUB = '''
import numpy as np
TABLE = {200: 0, 400: 1, 600: 2, 800: 3, 1000: int(__import__("os").environ["STUB_LAST"])}
class R:
    def __init__(self, p): self.speaker_probs = p
class M:
    def set_streaming_config(self, _): pass
    def generate(self, x):
        _trace("generate")
        n = len(x) // 160; p = np.zeros((n, 8))
        for i in range(n):
            fr = x[i * 160:(i + 1) * 160]
            if np.abs(fr).max() < 1e-3: continue
            zc = np.count_nonzero(np.diff(np.signbit(fr))) * 100 / 2
            p[i, TABLE[min(TABLE, key=lambda k: abs(k - zc))]] = 1.0
        return R(p)
def load(*a, **k): return M()
def _trace(what):  # STUB_TRACE=FILE: one line per model call, in order
    if __import__("os").environ.get("STUB_TRACE"):
        with open(__import__("os").environ["STUB_TRACE"], "a") as fh: fh.write(what + "\\n")
'''

# mlx.core as the side-car uses it: only clear_cache (EARS-140), traced like generate.
MX_STUB = '''
import os
def clear_cache():
    if os.environ.get("STUB_TRACE"):
        with open(os.environ["STUB_TRACE"], "a") as fh: fh.write("clear_cache\\n")
'''


def stub_diarizer(tmp_path):
    """A stand-in mlx_audio (the diarizer) and mlx.core on a PYTHONPATH dir; returns the dir."""
    d = tmp_path / "stub"
    for pkg, mod, src in (("mlx_audio", "vad", STUB), ("mlx", "core", MX_STUB)):
        (d / pkg).mkdir(parents=True, exist_ok=True)
        (d / pkg / "__init__.py").write_text("")
        (d / pkg / f"{mod}.py").write_text(src)
    return d


def _wav(p, f, s):
    t = np.arange(int(16000 * s)) / 16000
    with wave.open(str(p), "wb") as h:
        h.setnchannels(1); h.setsampwidth(2); h.setframerate(16000)
        h.writeframes((0.5 * np.sin(2 * np.pi * f * t) * 32767).astype(np.int16).tobytes())


@pytest.mark.parametrize("last,ok", [(4, True), (5, True), (3, False)])
def test_labels_by_reference(tmp_path, last, ok):
    """DAVE: five references came back as labels 0,1,2,3,5 and the side-car refused.
    Out of order is fine; two references on one label is not."""
    stub_diarizer(tmp_path)
    names = ["vera", "alice", "theo", "ivy", "dave"]
    for n, f in zip(names, [200, 400, 600, 800, 1000]):
        _wav(tmp_path / f"{n}.wav", f, 3)
    rec = tmp_path / "rec"; rec.mkdir(); logs = tmp_path / "logs"; logs.mkdir()
    for seq, f in [(1, 1000), (2, 800), (3, 600)]:
        _wav(rec / f"{seq}.wav", f, 2)
        (rec / f"{seq}.json").write_text(json.dumps(
            {"seq": seq, "kind": "turn", "t0": 0, "t1": 2, "text": "x", "ts": T0}))
    args = [sys.executable, str(SC), "--enroll-s", "3", "--relabel", str(rec), "--logdir", str(logs)]
    for n in names:
        args += ["--enroll", f"{n}={tmp_path / (n + '.wav')}"]
    env = dict(os.environ, PYTHONPATH=str(tmp_path / "stub"), STUB_LAST=str(last))
    p = subprocess.run(args, capture_output=True, text=True, env=env, timeout=60)
    if not ok:
        assert p.returncode != 0 and "one label" in (p.stderr + p.stdout)
        return
    assert p.returncode == 0, p.stderr
    got = {r["seq"]: r["speaker"] for r in map(json.loads, (logs / "speakers_2030-06-09.jsonl").open())}
    assert got == {1: "dave", 2: "ivy", 3: "theo"}


def test_clear_cache_after_every_generate(tmp_path):
    """EARS-140: MLX keeps every freed Metal buffer, and each heard line has a new length, so
    the side-car grew ~5 GB/hour. mx.clear_cache() must follow EVERY model.generate: the
    references' checks (room and call) as much as each line, with no generate left uncleared."""
    stub = stub_diarizer(tmp_path)
    for n, f in (("theo", 600), ("alice", 400), ("theocall", 1000)):
        _wav(tmp_path / f"{n}.wav", f, 3)
    rec = tmp_path / "rec"; rec.mkdir(); logs = tmp_path / "logs"; logs.mkdir()
    for seq, f, s in [(1, 600, 2), (2, 400, 1.3), (3, 600, 0.7)]:  # three lengths, as live lines are
        _wav(rec / f"{seq}.wav", f, s)
        (rec / f"{seq}.json").write_text(json.dumps(
            {"seq": seq, "kind": "turn", "t0": 0, "t1": s, "text": "x", "ts": T0}))
    trace = tmp_path / "trace"
    args = [sys.executable, str(SC), "--enroll-s", "3", "--relabel", str(rec), "--logdir", str(logs),
            "--enroll", f"theo={tmp_path / 'theo.wav'}", "--enroll", f"alice={tmp_path / 'alice.wav'}",
            "--call-enroll", f"theo={tmp_path / 'theocall.wav'}"]
    env = dict(os.environ, PYTHONPATH=str(stub), STUB_LAST="4", STUB_TRACE=str(trace))
    p = subprocess.run(args, capture_output=True, text=True, env=env, timeout=60)
    assert p.returncode == 0, p.stderr
    calls = trace.read_text().split()
    # room: 2 references + their prefix; call: 1 reference + its prefix; then 3 lines
    assert calls.count("generate") == 2 + 1 + 1 + 1 + 3
    assert calls == ["generate", "clear_cache"] * (len(calls) // 2)
    got = {r["seq"]: r["speaker"] for r in map(json.loads, (logs / "speakers_2030-06-09.jsonl").open())}
    assert got == {1: "theo", 2: "alice", 3: "theo"}  # the labels are what they were


def test_record_only_needs_no_mlx(tmp_path):
    """--record-only loads no model, so it must not need mlx either (the import sits with the
    model's): a machine picking reference clips has no diarizer venv yet."""
    src = SC.read_text()
    model_block = src[src.index("if not a.record_only:"):src.index("def probs(x):")]
    assert "import mlx.core as mx" in model_block
    assert src.count("import mlx.core") == 1
