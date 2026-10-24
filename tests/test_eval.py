"""ears eval: the scorer, the call cutter, and the report - no engine runs here."""
import json, wave
from pathlib import Path

import pytest

from ears import eval as ev


def test_norm_drops_punctuation_fillers_and_brackets():
    assert ev.norm("Hey, Ivy. Um, I'm having a look [INAUDIBLE].") == ["hey", "ivy", "i'm", "having", "a", "look"]


def test_edits_counts_substitutions_deletions_insertions():
    assert ev.edits("a b c d".split(), "a x c".split()) == (1, 1, 0)
    assert ev.edits([], "oh my god".split()) == (0, 0, 3)
    assert ev.edits("a b".split(), "a b".split()) == (0, 0, 0)


def _wav(path, seconds, rate=16000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"\0\0" * int(seconds * rate))


START = 1921356800.2


def _call_dir(tmp_path, stem="caller-call", seconds=30, start=START, name="20300609-080000-chat12-msg1317"):
    """A Delta call dir as the bot leaves it: STEM.wav + STEM.json (caller-call since PHONE-127,
    mike-call before). Another stem in the same dir is added by calling again. NAME: a web
    call's dir is its bare id (EARS-316)."""
    call = tmp_path / name; call.mkdir(exist_ok=True)
    _wav(call / f"{stem}.wav", seconds)
    (call / f"{stem}.json").write_text(json.dumps({"start_epoch": start, "end_epoch": start + seconds}))
    return call


def test_call_clips_cut_each_heard_turn_at_t0_plus_offset(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm"))
    from ears import heard
    call = _call_dir(tmp_path)
    start = START
    log = heard.log_path(__import__("datetime").datetime.fromtimestamp(start).date())
    log.parent.mkdir(parents=True)
    rows = [dict(seq=2, kind="turn", device="call:1317", t0=10.0, t1=12.0, text="two"),
            dict(seq=1, kind="turn", device="call:1317", t0=2.0, t1=3.0, text="one"),
            dict(seq=3, kind="partial", device="call:1317", t0=2.0, t1=3.0, text="not a turn"),
            dict(seq=4, kind="turn", device="call:999", t0=2.0, t1=3.0, text="another call")]
    log.write_text("".join(json.dumps(r) + "\n" for r in rows))
    out = tmp_path / "out"; (out / "clips").mkdir(parents=True)
    cid, clips = ev.call_clips(call, out, offset=0.3, limit=None)
    assert cid == "1317"
    assert [c["id"] for c in clips] == ["1", "2"]                     # turns only, this call, by time
    assert [c["heard"] for c in clips] == ["one", "two"]
    assert abs(clips[0]["dur"] - (1.0 + sum(ev.PAD))) < 1e-3          # t1-t0 plus both pads


@pytest.mark.parametrize("stems,want", [
    (["caller-call"], "caller-call"),                     # a call since PHONE-127
    (["mike-call"], "mike-call"),                         # an older call dir keeps its name
    (["mike-call", "caller-call"], "caller-call"),        # both: the new name wins
])
def test_call_clips_read_caller_call_else_mike_call(tmp_path, monkeypatch, stems, want):
    """EARS-298: PHONE-127 renamed mike-call.wav/.json -> caller-call.wav/.json. The loser
    of 'both' is a 1 s wav a day earlier: read, it would cut no clip at all."""
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm"))
    from ears import heard
    for stem in stems:
        if stem == want:
            call = _call_dir(tmp_path, stem)
        else:
            call = _call_dir(tmp_path, stem, seconds=1, start=START - 86400)
    log = heard.log_path(__import__("datetime").datetime.fromtimestamp(START).date())
    log.parent.mkdir(parents=True)
    log.write_text(json.dumps(dict(seq=7, kind="turn", device="call:1317", t0=10.0, t1=12.0, text="hi")) + "\n")
    out = tmp_path / "out"; (out / "clips").mkdir(parents=True)
    cid, clips = ev.call_clips(call, out, offset=0.3, limit=None)
    assert (cid, [c["id"] for c in clips]) == ("1317", ["7"])
    assert abs(clips[0]["dur"] - (2.0 + sum(ev.PAD))) < 1e-3


@pytest.mark.parametrize("name,cid", [
    ("20300609-080000-chat12-msg1317", "1317"),           # a Delta bot's call dir
    ("1921357125", "1921357125"),                         # a sessionphone web call's (EARS-316)
])
def test_call_clips_read_the_call_id_from_a_delta_or_a_web_call_dir(tmp_path, monkeypatch, name, cid):
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm"))
    from ears import heard
    call = _call_dir(tmp_path, name=name)
    log = heard.log_path(__import__("datetime").datetime.fromtimestamp(START).date())
    log.parent.mkdir(parents=True)
    log.write_text("".join(json.dumps(dict(seq=s, kind="turn", device=f"call:{n}", t0=10.0, t1=12.0, text="hi")) + "\n"
                           for s, n in ((7, cid), (8, "999"))))
    out = tmp_path / "out"; (out / "clips").mkdir(parents=True)
    got, clips = ev.call_clips(call, out, offset=0.659, limit=None)
    assert (got, [c["id"] for c in clips]) == (cid, ["7"])     # this call's turns only


@pytest.mark.parametrize("name", ["notes", "1921357125-old", "msg"])
def test_call_clips_refuse_a_dir_with_no_call_id(tmp_path, name):
    call = _call_dir(tmp_path, name=name)
    with pytest.raises(SystemExit, match="cannot read the call id"):
        ev.call_clips(call, tmp_path / "out", offset=0.3, limit=None)


def test_call_clips_with_no_recording_names_both_files(tmp_path):
    call = tmp_path / "20300609-080000-chat12-msg1317"; call.mkdir()
    with pytest.raises(SystemExit) as e:
        ev.call_clips(call, tmp_path / "out", offset=0.3, limit=None)
    assert "caller-call.json" in str(e.value) and "mike-call.json" in str(e.value)


def test_report_scores_only_referenced_clips_and_counts_hallucinations():
    clips = [dict(id="a", label="a", dur=1.0), dict(id="b", label="b", dur=1.0, heard="Oh, my God.")]
    results = {"x": {"a": {"text": "hello there", "proc_s": 0.1}, "b": {"text": "Oh, my God.", "proc_s": 0.1}}}
    lines = []
    ev.report(clips, results, {"a": "hello there", "b": ""}, ["x"], lines.append)
    text = "\n".join(lines)
    assert "[  0%] hello there" in text
    assert "[+3 ins] Oh, my God." in text
    assert "S/D/I 0/0/3" in text                                     # 3 errors over 2 reference words


def _fresh_eval():
    """ears.eval read again, so its module-level paths see this test's env."""
    import importlib
    import sys
    sys.modules.pop("ears.eval", None)
    return importlib.import_module("ears.eval")


def test_harness_default_is_ears_cache_never_vera(tmp_path, monkeypatch):
    """EARS-202 G24: unset (or empty) EARS_EVAL_HARNESS is ~/.cache/ears/eval, beside
    ears' other downloads; it was under an agent's home. No engine is run or probed."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("EARS_EVAL_MLX_PY", raising=False)
    try:
        for val in (None, ""):
            if val is None:
                monkeypatch.delenv("EARS_EVAL_HARNESS", raising=False)
            else:
                monkeypatch.setenv("EARS_EVAL_HARNESS", val)
            m = _fresh_eval()
            assert m.HARNESS == tmp_path / ".cache/ears/eval"
            assert m.MLX_PY == str(tmp_path / ".cache/ears/eval/venv-mlx054/bin/python")
            assert ".vera" not in str(m.ENGINES["whisper-turbo"]["model"])
        monkeypatch.setenv("EARS_EVAL_HARNESS", str(tmp_path / "h"))
        assert _fresh_eval().HARNESS == tmp_path / "h"
    finally:
        monkeypatch.undo()
        _fresh_eval()
