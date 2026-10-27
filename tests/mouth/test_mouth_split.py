"""`mouth split FILE`: a song's stems and instrumental, made into its sidecar.

- the outputs land in `.<name>.d/` with the manifest, `split.json`, and the record says
  `done`; a second run makes nothing;
- a failing separator leaves no partial output, and a `failed` record that carries the
  source's size and mtime_ns;
- two concurrent splits of one file run the separator once;
- around those: the speech check, `--force`, the refusals (exit 2), the record's states,
  the watchdog, `nice`, the speed record, the JSON lines and exit codes, the installed
  runner's command line, and the env fence (no separator in any test unless it sets one).

The separator is always tests/fixtures/fake_separator.py (it speaks the runner's
protocol, stdlib only): no model, no torch. ffmpeg decodes, as in a real split, so the
tests that split are marked needs_ffmpeg."""
from __future__ import annotations

import json
import math
import os
import shlex
import struct
import subprocess
import sys
import time
import wave
from pathlib import Path

import pytest

from mouth import __main__ as cli
from mouth import separator, sidecar
from mouth import split
from tests import env_fence

FAKE = Path(__file__).resolve().parents[1] / "fixtures" / "fake_separator.py"
ROOT = Path(__file__).resolve().parents[2]


def tone(path: Path, seconds: float = 2.0, rate: int = 44100) -> Path:
    """A stereo 16-bit WAV: a 440 Hz tone at half scale."""
    n = int(seconds * rate)
    frames = bytearray()
    for i in range(n):
        v = int(16000 * math.sin(2 * math.pi * 440 * i / rate))
        frames += struct.pack("<hh", v, v)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return path


@pytest.fixture
def rig(monkeypatch, tmp_path):
    """A mouth dir in tmp, the fake separator as $MOUTH_SEPARATOR, its run log, a song."""
    d = tmp_path / "mouth"
    monkeypatch.setenv("MOUTH_DIR", str(d))
    log = tmp_path / "runs.jsonl"
    monkeypatch.setenv("MOUTH_SEPARATOR", f"{shlex.quote(sys.executable)} {shlex.quote(str(FAKE))}")
    monkeypatch.setenv("MOUTH_FAKE_SEPARATOR_LOG", str(log))
    song = tone(tmp_path / "music" / "01 song.wav")
    return {"d": d, "log": log, "song": song, "tmp": tmp_path,
            "runs": lambda: log.read_text().splitlines() if log.exists() else []}


def outputs_of(folder: Path) -> dict:
    return {n: folder / rel for n, rel in json.loads((folder / "split.json").read_text())["outputs"].items()}


# ---- the acceptance test ----------------------------------------------------------------

@pytest.mark.needs_ffmpeg
def test_outputs_land_in_the_dot_sidecar_with_the_manifest(rig):
    song = rig["song"]
    code, line = split.split_file(song)
    assert code == 0, line
    folder = song.parent / ".01 song.wav.d"
    assert line == {**line, "file": str(song.resolve()), "sidecar": str(folder), "state": "made",
                    "verdict": "music"}
    m = json.loads((folder / "split.json").read_text())
    assert m["version"] == sidecar.MANIFEST_VERSION
    assert m["separator"] == "fake" and m["model"] == split.MODEL_NAME
    st = song.stat()
    assert m["source"] == {"name": "01 song.wav", "size": st.st_size, "mtime_ns": st.st_mtime_ns,
                           "duration_s": pytest.approx(2.0, abs=0.05)}
    assert m["outputs"] == {"vocals": "stems/vocals.wav", "drums": "stems/drums.wav",
                            "bass": "stems/bass.wav", "other": "stems/other.wav",
                            "instrumental": "mixes/instrumental.wav"}
    assert set(m["levels_db"]) == {"mix", "vocals", "instrumental"}
    assert m["verdict"] == "music" and m["device"] == "cpu" and m["split_s"] >= 0
    for p in outputs_of(folder).values():
        assert p.is_file() and p.stat().st_size > 44
    # nothing else is left in the sidecar: no temp folder, nothing set aside
    assert sorted(p.name for p in folder.iterdir()) == ["mixes", "split.json", "stems"]
    # the reader finds what was written, from the written layout
    hit = sidecar.instrumental(song)
    assert hit.layout == "dot" and hit.path == folder / "mixes/instrumental.wav"
    assert hit.verdict == "music"
    assert sidecar.stem(song, "drums").path == folder / "stems/drums.wav"
    rec = split.read_record(song)
    assert rec["state"] == "done" and split.record_state(rec) == "done"
    assert rec["size"] == st.st_size and rec["mtime_ns"] == st.st_mtime_ns
    assert len(rig["runs"]()) == 1


@pytest.mark.needs_ffmpeg
def test_a_second_run_makes_nothing(rig):
    song = rig["song"]
    assert split.split_file(song)[0] == 0
    folder = sidecar.canonical_dir(song)
    before = {p: p.stat().st_mtime_ns for p in folder.rglob("*")}
    code, line = split.split_file(song)
    assert code == 0 and line["state"] == "present" and line["verdict"] == "music"
    assert len(rig["runs"]()) == 1                       # the separator did not run again
    assert {p: p.stat().st_mtime_ns for p in folder.rglob("*")} == before


@pytest.mark.needs_ffmpeg
def test_a_failing_separator_leaves_no_partial_output_and_a_failed_record(rig, monkeypatch):
    monkeypatch.setenv("MOUTH_FAKE_SEPARATOR_FAIL", "1")   # writes the vocals, then fails
    song = rig["song"]
    code, line = split.split_file(song)
    assert code == 1 and line["state"] == "failed"
    assert "the fake separator was told to fail" in line["detail"]
    assert len(rig["runs"]()) == 1
    assert not sidecar.canonical_dir(song).exists()        # made by the split, removed again
    assert sorted(p.name for p in song.parent.iterdir()) == ["01 song.wav"]
    rec = split.read_record(song)
    st = song.stat()
    assert rec["state"] == "failed" and split.record_state(rec) == "failed"
    assert rec["size"] == st.st_size and rec["mtime_ns"] == st.st_mtime_ns
    assert "told to fail" in rec["detail"] and rec["source"] == str(song.resolve())


@pytest.mark.needs_ffmpeg
def test_a_failure_keeps_an_existing_sidecar_and_its_other_contents(rig, monkeypatch):
    """A sidecar the split did not make (audio-tools' analysis beside it) is left, emptied
    of nothing; only the temp folder goes."""
    song = rig["song"]
    folder = sidecar.canonical_dir(song)
    (folder / "analysis").mkdir(parents=True)
    (folder / "analysis/bpm.txt").write_text("120\n")
    monkeypatch.setenv("MOUTH_FAKE_SEPARATOR_FAIL", "1")
    assert split.split_file(song)[0] == 1
    assert sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*")) == [
        "analysis", "analysis/bpm.txt"]


@pytest.mark.needs_ffmpeg
def test_two_concurrent_splits_of_one_file_run_once(rig, monkeypatch):
    monkeypatch.setenv("MOUTH_FAKE_SEPARATOR_SLEEP", "1.5")
    song = rig["song"]
    argv = [sys.executable, "-m", "mouth", "split", str(song)]
    procs = [subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                              cwd=ROOT) for _ in range(2)]
    results = [p.communicate(timeout=60) for p in procs]
    assert [p.returncode for p in procs] == [0, 0], results
    states = sorted(json.loads(out)["state"] for out, _ in results)
    assert states == ["made", "present"]
    assert len(rig["runs"]()) == 1
    assert split.read_record(song)["state"] == "done"


# ---- what a split writes ----------------------------------------------------------------

@pytest.mark.needs_ffmpeg
def test_a_speech_file_is_recorded_as_speech(rig, monkeypatch):
    monkeypatch.setenv("MOUTH_FAKE_SEPARATOR_SPEECH", "1")
    song = rig["song"]
    code, line = split.split_file(song)
    assert code == 0 and line["verdict"] == "speech"
    m = sidecar.read_manifest(sidecar.canonical_dir(song))
    assert m["verdict"] == "speech"
    assert m["levels_db"]["mix"] - m["levels_db"]["instrumental"] >= sidecar.SPEECH_GAP_DB
    assert split.read_record(song)["verdict"] == "speech"
    assert sidecar.instrumental(song).verdict == "speech"


@pytest.mark.needs_ffmpeg
def test_force_splits_again_and_replaces_the_outputs(rig):
    song = rig["song"]
    assert split.split_file(song)[0] == 0
    folder = sidecar.canonical_dir(song)
    first = json.loads((folder / "split.json").read_text())["made_t"]
    (folder / "stems/stray.wav").write_bytes(b"old")      # in the old stems/, swapped out whole
    time.sleep(0.01)
    code, line = split.split_file(song, force=True)
    assert code == 0 and line["state"] == "made"
    assert len(rig["runs"]()) == 2
    assert json.loads((folder / "split.json").read_text())["made_t"] > first
    assert not (folder / "stems/stray.wav").exists()
    assert sorted(p.name for p in folder.iterdir()) == ["mixes", "split.json", "stems"]


@pytest.mark.needs_ffmpeg
def test_a_changed_source_is_split_again(rig):
    song = rig["song"]
    assert split.split_file(song)[0] == 0
    tone(song, seconds=4.0)                               # a new song in its place
    code, line = split.split_file(song)
    assert code == 0 and line["state"] == "made"
    assert len(rig["runs"]()) == 2
    m = sidecar.read_manifest(sidecar.canonical_dir(song))
    assert m["source"]["duration_s"] == pytest.approx(4.0, abs=0.05)


@pytest.mark.needs_ffmpeg
def test_a_missing_output_is_split_again(rig):
    song = rig["song"]
    assert split.split_file(song)[0] == 0
    (sidecar.canonical_dir(song) / "stems/bass.wav").unlink()
    assert split.split_file(song)[1]["state"] == "made"
    assert len(rig["runs"]()) == 2


@pytest.mark.needs_ffmpeg
def test_a_hand_filled_sidecar_is_not_replaced_unless_forced(rig):
    song = rig["song"]
    folder = sidecar.canonical_dir(song)
    (folder / "mixes").mkdir(parents=True)
    (folder / "mixes/instrumental.wav").write_bytes(b"hand made")
    code, line = split.split_file(song)
    assert code == 2 and "--force replaces them" in line["detail"]
    assert (folder / "mixes/instrumental.wav").read_bytes() == b"hand made"
    assert rig["runs"]() == []
    assert split.split_file(song, force=True)[0] == 0
    assert (folder / "split.json").is_file()


@pytest.mark.needs_ffmpeg
def test_leftovers_of_a_dead_split_are_cleared(rig):
    song = rig["song"]
    folder = sidecar.canonical_dir(song)
    (folder / ".split-99999/stems").mkdir(parents=True)
    (folder / ".old-99998").mkdir()
    assert split.split_file(song)[0] == 0
    assert sorted(p.name for p in folder.iterdir()) == ["mixes", "split.json", "stems"]


@pytest.mark.needs_ffmpeg
def test_the_separator_runs_niced_with_the_protocols_arguments(rig):
    song = rig["song"]
    base = os.nice(0)
    assert split.split_file(song)[0] == 0
    run = json.loads(rig["runs"]()[0])
    assert run["nice"] == min(19, base + split.NICE)
    argv = run["argv"]
    out = argv[argv.index("--out") + 1]
    assert Path(out).name == f".split-{os.getpid()}"
    assert Path(out).parent == sidecar.canonical_dir(song)
    assert argv[argv.index("--threads") + 1] == str(split.threads())
    assert argv[argv.index("--model") + 1] == str(separator.model_path())
    assert argv[argv.index("--sha256") + 1] == separator.MODEL.sha256
    assert run["bytes"] == pytest.approx(2.0 * 44100 * 4, rel=0.01)   # s16le, 44.1 kHz, stereo


@pytest.mark.needs_ffmpeg
def test_the_speed_is_recorded_as_a_running_mean(rig):
    song = rig["song"]
    assert split.read_speed() == split.DEFAULT_SPEED
    assert split.split_file(song)[0] == 0
    rec = json.loads((rig["d"] / "splits/speed.json").read_text())
    assert rec["n"] == 1 and rec["s_per_s"] > 0
    assert split.read_speed() == rec["s_per_s"]
    assert split.eta_s(100) == round(100 * rec["s_per_s"], 1)
    split.split_file(song, force=True)
    assert json.loads((rig["d"] / "splits/speed.json").read_text())["n"] == 2


def test_the_running_mean_and_the_default_eta(rig):
    assert split.eta_s(200) == 80.0                      # 0.4 s per s before any split
    split.record_speed(0.2)
    split.record_speed(0.4)
    assert split.read_speed() == pytest.approx(0.3)
    assert split.deadline_for(10) == split.DEADLINE_MIN_S
    assert split.deadline_for(1000) == 5000


@pytest.mark.needs_ffmpeg
def test_a_split_past_its_deadline_is_stopped_and_failed(rig, monkeypatch):
    monkeypatch.setattr(split, "DEADLINE_MIN_S", 1.0)
    monkeypatch.setattr(split, "DEFAULT_SPEED", 0.01)
    monkeypatch.setenv("MOUTH_FAKE_SEPARATOR_SLEEP", "30")
    song = rig["song"]
    t0 = time.monotonic()
    code, line = split.split_file(song)
    assert time.monotonic() - t0 < 15
    assert code == 1 and "past its deadline" in line["detail"]
    assert split.read_record(song)["state"] == "failed"
    assert not sidecar.canonical_dir(song).exists()


@pytest.mark.needs_ffmpeg
def test_a_running_record_the_caller_wrote_is_kept_and_finished(rig):
    """`mouth play` writes the first record, naming the child's pid, before it queues the
    play: the child keeps it (its eta and deadline) and finishes it."""
    song = rig["song"]
    with split.record_lock(song):
        split.write_record(song, {"state": "running", "pid": os.getpid(), "source": str(song),
                                  "started_t": time.time(), "eta_s": 42.0,
                                  "deadline_t": time.time() + 900})
    assert split.split_file(song)[0] == 0
    rec = split.read_record(song)
    assert rec["state"] == "done" and rec["eta_s"] == 42.0


# ---- refusals: exit 2 -------------------------------------------------------------------

def test_no_separator_is_refused_naming_the_install(rig, monkeypatch):
    monkeypatch.delenv("MOUTH_SEPARATOR")
    code, line = split.split_file(rig["song"])
    assert code == 2 and line["state"] == "refused"
    assert "mouth split --install" in line["detail"]
    assert split.read_record(rig["song"]) is None       # not about the file: nothing recorded
    assert rig["runs"]() == []


def test_no_separator_finishes_a_record_the_caller_wrote_for_it(rig, monkeypatch):
    monkeypatch.delenv("MOUTH_SEPARATOR")
    song = rig["song"]
    with split.record_lock(song):
        split.write_record(song, {"state": "running", "pid": os.getpid()})
    assert split.split_file(song)[0] == 2
    rec = split.read_record(song)
    assert rec["state"] == "failed" and "mouth split --install" in rec["detail"]
    assert rec["size"] == song.stat().st_size and rec["mtime_ns"] == song.stat().st_mtime_ns


@pytest.mark.parametrize("what", ["url", "missing", "directory"])
def test_a_url_or_a_missing_file_is_refused(rig, what):
    target = {"url": "https://example.com/song.mp3", "missing": str(rig["tmp"] / "nope.wav"),
              "directory": str(rig["tmp"])}[what]
    code, line = split.split_file(target)
    assert code == 2 and line["state"] == "refused"
    assert rig["runs"]() == []


def test_an_unwritable_folder_is_refused_and_recorded_failed(rig):
    song = rig["song"]
    os.chmod(song.parent, 0o555)
    try:
        code, line = split.split_file(song)
    finally:
        os.chmod(song.parent, 0o755)
    assert code == 2 and "not writable" in line["detail"]
    rec = split.read_record(song)
    assert rec["state"] == "failed" and rec["size"] == song.stat().st_size
    assert rig["runs"]() == []


@pytest.mark.needs_ffmpeg
def test_a_file_ffmpeg_cannot_read_is_refused(rig):
    junk = rig["tmp"] / "music" / "notes.txt"
    junk.write_text("not a sound\n")
    code, line = split.split_file(junk)
    assert code == 2 and "no duration" in line["detail"]
    assert split.read_record(junk)["state"] == "failed"
    assert rig["runs"]() == []
    assert not sidecar.canonical_dir(junk).exists()


# ---- the record ----------------------------------------------------------------------------

def test_a_running_record_with_a_dead_pid_or_past_its_deadline_counts_as_failed():
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    now = time.time()
    assert split.record_state(None) is None
    assert split.record_state({"state": "running", "pid": os.getpid(), "deadline_t": now + 60}) == "running"
    assert split.record_state({"state": "running", "pid": dead.pid, "deadline_t": now + 60}) == "failed"
    assert split.record_state({"state": "running", "pid": os.getpid(), "deadline_t": now - 1}) == "failed"
    assert split.record_state({"state": "done"}) == "done"
    assert split.record_state({"state": "odd"}) is None


def test_the_key_is_the_real_paths(tmp_path):
    song = tone(tmp_path / "a.wav", seconds=0.01)
    link = tmp_path / "b.wav"
    link.symlink_to(song)
    assert split.key(link) == split.key(song)
    assert split.record_path(link).name == f"{split.key(song)}.json"


# ---- the command ---------------------------------------------------------------------------

@pytest.mark.needs_ffmpeg
def test_the_cli_prints_one_json_line_per_file_and_the_worst_exit(rig, capsys):
    song = rig["song"]
    missing = rig["tmp"] / "nope.wav"
    assert cli.main(["split", str(song)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 1 and json.loads(out[0])["state"] == "made"
    assert cli.main(["split", str(song), str(missing)]) == 2
    cap = capsys.readouterr()
    lines = [json.loads(x) for x in cap.out.splitlines()]
    assert [x["state"] for x in lines] == ["present", "refused"]
    assert cap.err.count("\n") == 1 and "REFUSED" in cap.err


def test_the_cli_exit_is_1_when_a_split_fails_and_none_is_refused(rig, monkeypatch, capsys):
    calls = iter([(0, {"state": "made"}), (1, {"state": "failed", "detail": "x"})])
    monkeypatch.setattr(split, "split_file", lambda f, force=False: next(calls))
    assert split.main(["a", "b"]) == 1
    assert "FAILED" in capsys.readouterr().err


@pytest.mark.parametrize("argv, words", [
    (["split"], "name a FILE"),
    (["split", "--install", "--force"], "no --force"),
])
def test_the_cli_refuses_a_bad_combination(argv, words, capsys):
    assert cli.main(argv) == 2
    assert words in capsys.readouterr().err


def test_force_is_passed_through(rig, monkeypatch):
    seen = []
    monkeypatch.setattr(split, "split_file", lambda f, force=False: seen.append(force) or (0, {}))
    assert cli.main(["split", "--force", str(rig["song"])]) == 0
    assert seen == [True]


# ---- the installed runner, and the fence ---------------------------------------------------

def test_the_installed_runner_is_the_venvs_python_on_run_py(monkeypatch, tmp_path):
    home = tmp_path / "sephome"
    monkeypatch.setenv("MOUTH_SEPARATOR_HOME", str(home))
    monkeypatch.delenv("MOUTH_SEPARATOR", raising=False)
    assert not split.separator_present()
    with pytest.raises(split.Refused, match="mouth split --install"):
        split.separator_argv(tmp_path / "out")
    py = home / "venv/bin/python"
    py.parent.mkdir(parents=True)
    py.write_text("")
    model = home / "models" / separator.MODEL.file
    model.parent.mkdir()
    model.write_bytes(b"x" * 10)
    (home / "installed.json").write_text(json.dumps({
        "schema": separator.STAMP_SCHEMA, "python": str(py),
        "model": {"path": str(model), "size": 10, "sha256": "ab" * 32}}))
    assert split.separator_present()
    argv = split.separator_argv(tmp_path / "out")
    assert argv[:2] == [str(py), str(separator.separator_file("run.py"))]
    assert argv[2:] == ["--out", str(tmp_path / "out"), "--model", str(model),
                        "--sha256", "ab" * 32, "--threads", str(split.threads())]


def test_the_env_fence_gives_every_test_no_separator():
    assert "MOUTH_SEPARATOR" not in os.environ
    assert os.environ["MOUTH_SEPARATOR_HOME"] == env_fence.NO_SEPARATOR_HOME
    assert not Path(env_fence.NO_SEPARATOR_HOME).exists()
    assert not split.separator_present()
    for knob in ("LOG", "FAIL", "SLEEP", "SPEECH"):
        assert f"MOUTH_FAKE_SEPARATOR_{knob}" not in os.environ


def test_a_blank_separator_command_is_no_separator(monkeypatch):
    monkeypatch.setenv("MOUTH_SEPARATOR", "   ")
    assert not split.separator_present()
