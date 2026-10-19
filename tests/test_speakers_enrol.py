"""EARS-258 do-003: `ears speakers enrol|record|clusters|name`, the guided enrol (design s6).

No mic and no model. The service python is this test's python, with a stand-in diarizer on
PYTHONPATH (a stand-in mlx_audio that labels a sine by its pitch, as tests/test_speakers.py's
does), a stand-in huggingface_hub whose cache answers from STUB_MODEL_CACHED, and a stand-in
sounddevice. The stand-in's load() refuses unless HF_HUB_OFFLINE=1, so every real model step
below also proves enrol never downloads; it and the cache check also refuse any revision but
services.SPEAKER_REVISION, so every step also proves it reads the pinned snapshot. The recorder (--record-only) is faked at its Popen;
cluster-room.py, make-refs.py and the side-car's start check run for real on the stand-in.
Everything runs under test_services_install_checks' fence: a temp HOME, launchctl faked."""
import ast
import io
import json
import os
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

from ears import cli, services, speakers
from test_services_install_checks import fence  # noqa: F401  (a fixture)
from tests.audio_fence import expect_refusal

SIDECAR = Path(__file__).resolve().parents[1] / "speakers" / "speaker-sidecar2.py"
OURS = "dev.sessionkit.voice.ears-speakers"
UID = os.getuid()

# A sine's pitch is its speaker. 1200 Hz is two voices at once: its frames go to labels 4 and
# 5, 7 to 3, so its cluster is L4 by share (0.7) and make-refs' one-voice check refuses it.
VAD = '''
import os
import numpy as np
TABLE = {200: 0, 400: 1, 800: 2, 1200: -1}
class R:
    def __init__(self, p): self.speaker_probs = p
class M:
    def set_streaming_config(self, _): pass
    def generate(self, x):
        n = len(x) // 160; p = np.zeros((n, 8))
        for i in range(n):
            fr = x[i * 160:(i + 1) * 160]
            if np.abs(fr).max() < 1e-3: continue
            zc = np.count_nonzero(np.diff(np.signbit(fr))) * 100 / 2
            lab = TABLE[min(TABLE, key=lambda k: abs(k - zc))]
            p[i, lab if lab >= 0 else (4 if i % 10 < 7 else 5)] = 1.0
        return R(p)
def load(*a, **k):
    if os.environ.get("HF_HUB_OFFLINE") != "1":
        raise SystemExit("stub: a model load without HF_HUB_OFFLINE=1 would download")
    if k.get("revision") != "@REVISION@":
        raise SystemExit(f"stub: a model load not at the pinned revision: {k.get('revision')!r}")
    return M()
'''
HUB = '''
import os
def snapshot_download(repo, local_files_only=False, **kw):
    if os.environ.get("HF_HUB_OFFLINE") != "1" or not local_files_only:
        raise SystemExit("stub: a cache check that could download")
    if kw.get("revision") != "@REVISION@":
        raise SystemExit(f"stub: a cache check not at the pinned revision: {kw.get('revision')!r}")
    if os.environ.get("STUB_MODEL_CACHED") != "1":
        raise FileNotFoundError(repo + " is not in the cache")
    return "/cache/" + repo
'''
MX = "def clear_cache(): pass\n"


def stub(root: Path, nemotron=True, sounddevice=True) -> Path:
    files = {"mlx_audio/__init__.py": "", "mlx_audio/vad/__init__.py": VAD,
             "mlx_audio/vad/models/__init__.py": "", "mlx/__init__.py": "", "mlx/core.py": MX,
             "huggingface_hub/__init__.py": HUB}
    if nemotron:
        files["mlx_audio/vad/models/nemotron_diarization/__init__.py"] = ""
    if sounddevice:
        files["sounddevice.py"] = ""
    for rel, src in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        # the stand-ins refuse a load or a cache check at any other snapshot (SK-80 S3): a
        # script that drops revision= fails here, not only in a grep
        (root / rel).write_text(src.replace("@REVISION@", services.SPEAKER_REVISION))
    return root


def wav(p: Path, hz: float, s: float) -> Path:
    t = np.arange(int(16000 * s)) / 16000
    with wave.open(str(p), "wb") as h:
        h.setnchannels(1); h.setsampwidth(2); h.setframerate(16000)
        h.writeframes((0.5 * np.sin(2 * np.pi * hz * t) * 32767).astype(np.int16).tobytes())
    return p


ROOM = [(200, "alice"), (400, "theo"), (800, "someone")]     # three voices, three lines each


def recording(d: Path, voices=ROOM, per=3, seconds=2.0) -> Path:
    """SEQ.wav + SEQ.json as --record-only writes them: each voice says PER lines."""
    d.mkdir(parents=True, exist_ok=True)
    seq = 100
    for i in range(per):
        for hz, who in voices:
            seq += 1
            wav(d / f"{seq}.wav", hz, seconds)
            (d / f"{seq}.json").write_text(json.dumps(
                {"seq": seq, "kind": "partial", "speaker": None, "t0": 0, "t1": seconds,
                 "text": f"{who} says line {i}", "ts": f"2030-10-15T11:00:{seq - 100:02d}+11:00"}))
    return d


@pytest.fixture
def en(fence, monkeypatch, tmp_path):
    """The fence, plus: the service python is this one, with the stand-ins first on its path,
    and the model cached. The speakers home is the default one, under the temp HOME."""
    for k in ("EARS_SPEAKERS_HOME", "EARS_SPEAKER_AGENT_REFS", "VOICEMODE_BASE_DIR"):
        monkeypatch.delenv(k, raising=False)
    root = stub(tmp_path / "stub")
    monkeypatch.setenv("EARS_SPEAKER_PY", sys.executable)
    monkeypatch.setenv("PYTHONPATH", str(root))
    monkeypatch.setenv("STUB_MODEL_CACHED", "1")
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)      # enrol must set it, not inherit it
    # never this host's processes, and never a real recorder (it would open a mic): a test
    # that records says so with the `rec` fixture
    monkeypatch.setattr(speakers, "_ps", lambda: "")
    monkeypatch.setattr(speakers, "_popen", lambda *a, **k: pytest.fail("a real recorder was started"))
    home = fence["home"] / ".local/share/ears/speakers"
    fence.update(speakers=home, refs=home / "refs", stub=root)
    return fence


def mine(en, name="20301015-110000") -> Path:
    """A recording where enrol makes one: under <home>/enrol/."""
    return recording(en["speakers"] / "enrol" / name)


def clustered(en, d=None) -> Path:
    d = d or mine(en)
    speakers.clusters(d)
    return d


def refs(en) -> dict:
    r = en["refs"]
    return {p.name: p.stat().st_mode & 0o777 for p in sorted(r.iterdir())} if r.exists() else {}


class Tty(io.StringIO):
    def isatty(self):
        return True


# ---- clusters: who said what ---------------------------------------------------------------

def test_clusters_shows_each_cluster_and_its_words(en, capsys):
    d = mine(en)
    rows = speakers.clusters(d)
    out = capsys.readouterr().out
    assert "alice says line 0" in out and "L 0" in out and "L 1" in out and "L 2" in out
    assert {r["label"] for r in rows} == {0, 1, 2} and (d / "clusters.json").exists()
    assert {r["label"] for r in rows if r["text"].startswith("theo")} == {1}


def test_clusters_with_no_recording_refuses(en, tmp_path, capsys):
    (tmp_path / "empty").mkdir()
    assert speakers.main(["clusters", str(tmp_path / "empty")]) == 1
    assert "ears speakers record" in capsys.readouterr().err


# ---- name: SC7 -----------------------------------------------------------------------------

def test_two_named_one_skipped_makes_exactly_two_private_refs(en, capsys):
    d = clustered(en)
    assert speakers.main(["name", str(d), "L0=alice", "L 1=theo"]) == 0          # L2 skipped
    assert refs(en) == {"alice.txt": 0o600, "alice.wav": 0o600, "theo.txt": 0o600, "theo.wav": 0o600}
    assert en["refs"].stat().st_mode & 0o777 == 0o700
    assert en["speakers"].stat().st_mode & 0o777 == 0o700
    assert not (en["speakers"] / "refs.new").exists()
    assert not d.exists()                                    # a good commit deletes the recording (Q7)
    out = capsys.readouterr().out
    assert "enrolled alice" in out and "enrolled theo" in out
    assert out.rstrip().splitlines()[-1].startswith("speakers not installed")   # the status row


def test_a_recording_not_made_by_enrol_is_never_deleted(en, tmp_path):
    d = clustered(en, recording(tmp_path / "theirs"))
    assert speakers.main(["name", str(d), "0=alice"]) == 0
    assert d.is_dir() and (d / "clusters.json").exists()


def test_tty_prompts_name_each_cluster_and_blank_skips(en, monkeypatch, capsys):
    d = clustered(en)
    capsys.readouterr()
    # L0: a bad name, then alice; L1: alice again (taken), then theo; L2: blank, skipped
    monkeypatch.setattr(sys, "stdin", Tty("Alice!\nalice\nalice\ntheo\n\n"))
    assert speakers.main(["name", str(d)]) == 0
    assert set(refs(en)) == {"alice.wav", "alice.txt", "theo.wav", "theo.txt"}
    out = capsys.readouterr().out
    assert "cluster L0: 3 lines" in out and "alice says line 2" in out    # its words, before the ask
    assert "a name is lower case" in out and "alice is already named" in out


def test_no_name_given_off_a_terminal_enrols_nobody(en, capsys):
    d = clustered(en)
    assert speakers.main(["name", str(d)]) == 1
    assert "no cluster named" in capsys.readouterr().err and not en["refs"].exists()


@pytest.mark.parametrize("spec,want", [("L 0=alice", (0, "alice")), ("L0=alice", (0, "alice")),
                                       ("0=alice", (0, "alice")), ("L12=bo_b-2", (12, "bo_b-2"))])
def test_a_label_as_cluster_room_prints_it_or_short(spec, want):
    assert speakers.parse_specs([spec]) == [want]


@pytest.mark.parametrize("specs,said", [(["L0=Alice"], "not a name"), (["L0=1x"], "not a name"),
                                        (["alice"], "not LABEL=NAME"), (["Lx=alice"], "not LABEL=NAME"),
                                        (["L0=a", "L1=a"], "name is given twice"),
                                        (["L0=a", "0=b"], "cluster is named twice")])
def test_a_bad_spec_is_refused_before_anything_runs(specs, said):
    with pytest.raises(speakers.Refused, match=said):
        speakers.parse_specs(specs)


def test_a_cluster_that_is_not_one_voice_is_refused_and_nothing_committed(en, capsys):
    d = clustered(en, recording(en["speakers"] / "enrol" / "two", voices=[(1200, "both"), (200, "alice")]))
    rows = json.loads((d / "clusters.json").read_text())
    assert {r["label"] for r in rows if r["text"].startswith("both")} == {4}
    assert speakers.main(["name", str(d), "L4=both", "L0=alice"]) == 1
    err = capsys.readouterr().err
    assert "does not come back as one speaker" in err and "Nothing was enrolled" in err
    assert refs(en) == {} and not (en["speakers"] / "refs.new").exists()
    assert d.is_dir()                                        # kept on a failure (Q7)


def test_the_start_check_failing_commits_nothing(en, capsys):
    en["refs"].mkdir(parents=True)
    zed = wav(en["refs"] / "zed.wav", 400, 3)               # a kept voice the side-car hears as theo's
    before = zed.read_bytes()
    d = clustered(en)
    assert speakers.main(["name", str(d), "L1=theo"]) == 1
    err = capsys.readouterr().err
    assert "would not start" in err and "one label" in err
    assert sorted(p.name for p in en["refs"].iterdir()) == ["zed.wav"] and zed.read_bytes() == before
    assert not (en["speakers"] / "refs.new").exists() and d.is_dir()


def test_re_enrol_merges_by_name(en):
    en["refs"].mkdir(parents=True)
    bob = wav(en["refs"] / "bob.wav", 800, 3); bob_bytes = bob.read_bytes()
    old = wav(en["refs"] / "alice.wav", 200, 1); old_bytes = old.read_bytes()
    d = clustered(en, recording(en["speakers"] / "enrol" / "again", voices=ROOM[:2]))
    assert speakers.main(["name", str(d), "L0=alice"]) == 0
    assert bob.read_bytes() == bob_bytes                    # nobody else's reference is touched
    assert (en["refs"] / "alice.wav").read_bytes() != old_bytes and refs(en)["alice.wav"] == 0o600


def test_a_cluster_needs_three_recorded_lines(en, capsys):
    d = en["speakers"] / "enrol" / "short"
    d.mkdir(parents=True)
    (d / "clusters.json").write_text(json.dumps(
        [{"seq": 1, "label": 0, "share": 1.0, "text": "a", "ts": "11:00:01"},
         {"seq": 2, "label": 0, "share": 1.0, "text": "b", "ts": "11:00:02"},
         {"seq": 3, "label": 1, "share": 1.0, "text": "c", "ts": "11:00:03"}]))
    assert speakers.main(["name", str(d), "L0=alice"]) == 1
    err = capsys.readouterr().err
    assert "L0 (alice) has 2 recorded lines" in err and "needs 3" in err
    assert not en["speakers"].joinpath("refs.new").exists() and not en["refs"].exists()


def test_name_before_clusters_says_so(en, capsys):
    d = mine(en)
    assert cli.main(["speakers", "name", str(d), "L0=alice"]) == 1          # dispatched by ears
    assert f"ears speakers clusters {d}" in capsys.readouterr().err


# ---- the job: restarted only when loaded ---------------------------------------------------

@pytest.mark.parametrize("loaded", [True, False])
def test_the_job_is_restarted_only_if_loaded(en, loaded):
    if loaded:
        en["loaded"].add(OURS)
    d = clustered(en)
    assert speakers.main(["name", str(d), "L0=alice"]) == 0
    kick = [a for a in en["calls"]["launchctl"] if a[0] == "kickstart"]
    assert kick == ([("kickstart", "-k", f"gui/{UID}/{OURS}")] if loaded else [])


# ---- never a silent download (criterion 5) and fail before anyone talks (criterion 6) -----

def test_every_model_step_runs_offline(en, monkeypatch):
    seen, real = [], subprocess.run

    def run(argv, *a, **kw):
        seen.append((list(map(str, argv)), (kw.get("env") or os.environ).get("HF_HUB_OFFLINE")))
        return real(argv, *a, **kw)
    monkeypatch.setattr(subprocess, "run", run)
    d = clustered(en)
    assert speakers.main(["name", str(d), "L0=alice"]) == 0
    py = [(argv, off) for argv, off in seen if argv[0] == sys.executable]
    steps = {Path(argv[1]).name for argv, _ in py if not argv[1].startswith("-")}
    assert {"cluster-room.py", "make-refs.py", "speaker-sidecar2.py"} <= steps
    assert any(argv[1] == "-c" and "snapshot_download" in argv[2] for argv, _ in py)   # the cache check
    assert all(off == "1" for _, off in py), py


@pytest.mark.parametrize("argv", [["record"], ["clusters", "{d}"], ["name", "{d}", "L0=alice"]])
def test_no_cached_model_refuses_in_one_line_naming_install(en, monkeypatch, capsys, argv):
    d = clustered(en)
    capsys.readouterr()
    monkeypatch.setenv("STUB_MODEL_CACHED", "0")
    before = sorted(p.name for p in d.iterdir())
    assert speakers.main([a.replace("{d}", str(d)) for a in argv]) == 2
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1 and "not cached" in err[0] and "ears services install --only speakers" in err[0]
    assert not en["refs"].exists() and not (en["speakers"] / "refs.new").exists()
    assert sorted(p.name for p in d.iterdir()) == before


def test_enrol_with_no_cached_model_refuses_before_recording(en, monkeypatch, capsys):
    monkeypatch.setenv("STUB_MODEL_CACHED", "0")
    monkeypatch.setattr(sys, "stdin", Tty(""))
    monkeypatch.setattr(speakers, "_popen", lambda *a, **k: pytest.fail("recorded before the checks"))
    assert speakers.main(["enrol", "--seconds", "0"]) == 2
    assert "not cached" in capsys.readouterr().err
    assert not (en["speakers"] / "enrol").exists()          # no record dir made


@pytest.mark.parametrize("nemotron,sd,named", [(False, True, "mlx_audio.vad.models.nemotron_diarization"),
                                                (True, False, "sounddevice")])
def test_a_python_missing_a_module_is_named(en, monkeypatch, tmp_path, capsys, nemotron, sd, named):
    monkeypatch.setenv("PYTHONPATH", str(stub(tmp_path / "old", nemotron=nemotron, sounddevice=sd)))
    monkeypatch.setattr(sys, "stdin", Tty(""))
    # With no stand-in sounddevice, the probe's child (`python -c "import sounddevice"`) is
    # refused by the audio fence (EARS-320): the refusal is this test's point, so it is
    # declared, and only that one is. Its child still exits non-zero, as a missing module does.
    with expect_refusal() as refused:
        assert speakers.main(["enrol", "--seconds", "0"]) == 2
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1 and f"has no {named}:" in err[0]
    assert not (en["speakers"] / "enrol").exists()
    want = [("sounddevice", ["-c"])] if named == "sounddevice" else []
    assert [(r["module"], r["argv"]) for r in refused] == want, refused


def test_no_service_python_says_install(en, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("EARS_SPEAKER_PY", str(tmp_path / "nope/bin/python"))
    assert speakers.main(["record", "--seconds", "0"]) == 2
    assert "ears services install --only speakers" in capsys.readouterr().err


# ---- record --------------------------------------------------------------------------------

KEEPER_PS = ("/usr/bin/python -m ears.listen capture --session ears-keeper --device MacBook Pro "
             "Microphone --vad silero\n/bin/zsh\n")


class Recorder:
    """--record-only as a Popen: it 'records' LINES of the fixture room into its dir, and runs
    until its stop file appears. A signal is a failure: enrol never kills it."""
    made = []

    def __init__(self, argv, env=None, lines=9, **kw):
        self.argv, self.env, self.pid = argv, env, 4242
        self.dir = Path(argv[argv.index("--record-only") + 1])
        self.stop = Path(argv[argv.index("--stop-file") + 1])
        src = recording(self.dir.parent / ".src")
        for p in sorted(src.glob("*.json"))[:lines]:
            for q in (p, p.with_suffix(".wav")):
                (self.dir / q.name).write_bytes(q.read_bytes())
        for p in src.iterdir():
            p.unlink()
        src.rmdir()
        Recorder.made.append(self)

    def poll(self):
        return 0 if self.stop.exists() else None

    def wait(self, timeout=None):
        assert self.stop.exists(), "waited on a recorder nobody asked to stop"
        return 0

    def kill(self):
        pytest.fail("the recorder was killed")

    terminate = send_signal = kill


@pytest.fixture
def rec(en, monkeypatch):
    Recorder.made = []
    monkeypatch.setattr(speakers, "_ps", lambda: KEEPER_PS)
    monkeypatch.setattr(speakers, "_popen", Recorder)
    return en


def test_record_runs_the_recorder_and_stops_it_by_its_stop_file(rec, capsys):
    assert speakers.main(["record", "--seconds", "0"]) == 0
    (r,) = Recorder.made
    d = r.dir
    assert d.parent == rec["speakers"] / "enrol"
    assert r.argv == [sys.executable, str(services._speakers_file("speaker-sidecar2.py")), "--record-only",
                      str(d), "--stop-file", str(d / "STOP"), "--logdir",
                      str(rec["home"] / ".sessionvoice/logs/conversations")]   # a fresh home (EARS-294 T7)
    assert r.env["HF_HUB_OFFLINE"] == "1"
    assert not r.stop.exists()                               # touched to stop it, then tidied
    assert d.stat().st_mode & 0o777 == 0o700 and d.parent.stat().st_mode & 0o777 == 0o700
    out = capsys.readouterr().out
    assert f"recorded 9 lines into {d}" in out and f"next: ears speakers clusters {d}" in out


def test_record_refuses_with_no_keeper_capture(rec, monkeypatch, capsys):
    monkeypatch.setattr(speakers, "_ps", lambda: "/bin/zsh\n/usr/bin/python -m ears.listen capture "
                        "--session call-1 --device BlackHole 2ch\n")
    assert speakers.main(["record", "--seconds", "0"]) == 2
    assert "no ears keeper capture" in capsys.readouterr().err
    assert not Recorder.made and not (rec["speakers"] / "enrol").exists()


def test_too_few_lines_recorded_is_refused_and_kept(rec, monkeypatch, capsys):
    monkeypatch.setattr(speakers, "_popen", lambda argv, **kw: Recorder(argv, lines=2, **kw))
    assert speakers.main(["record", "--seconds", "0"]) == 1
    assert "only 2 recorded lines" in capsys.readouterr().err
    (d,) = (rec["speakers"] / "enrol").iterdir()
    assert len(list(d.glob("*.json"))) == 2


def test_the_keeper_check_is_the_sidecars(rec):
    """speakers.keeper_device answers as speaker-sidecar2.py's own keeper_device does."""
    src = SIDECAR.read_text()
    ns = {"re": __import__("re")}
    exec(src[src.index("KEEPER_SESSION = "):src.index("def same_dev")], ns)
    for ps in (KEEPER_PS, "/bin/zsh\n", "x -m voice_mode.listen capture --session ears-keeper --device airpods\n",
               "x -m ears.listen capture --session ears-keeper --device BlackHole 2ch --vad silero\n",
               "x -m ears.listen capture --session room-2 --device airpods\n"):
        assert speakers.keeper_device(ps) == ns["keeper_device"](ps), ps


# ---- enrol: all of it ----------------------------------------------------------------------

def test_enrol_at_a_terminal_records_clusters_asks_and_commits(rec, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", Tty("\n"))       # Enter in the record step is not read: --seconds 0
    answers = iter(["alice", "theo", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert speakers.main(["enrol", "--seconds", "0"]) == 0
    assert set(refs(rec)) == {"alice.wav", "alice.txt", "theo.wav", "theo.txt"}
    assert not list((rec["speakers"] / "enrol").iterdir())                    # the recording is gone
    out = capsys.readouterr().out
    assert "cluster L1: 3 lines" in out and "theo says line 0" in out


def test_enrol_with_nobody_named_keeps_the_recording(rec, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", Tty(""))
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    assert speakers.main(["enrol", "--seconds", "0"]) == 1
    assert "nothing enrolled" in capsys.readouterr().err and not rec["refs"].exists()
    (d,) = (rec["speakers"] / "enrol").iterdir()
    assert (d / "clusters.json").exists()


def test_enrol_off_a_terminal_says_how_an_agent_does_it(rec, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert speakers.main(["enrol"]) == 2
    err = capsys.readouterr().err
    assert "ears speakers record" in err and "name DIR L0=NAME" in err
    assert not Recorder.made


def test_the_enrol_verbs_never_read_the_real_home(en):
    assert str(speakers.enrol_root()).startswith(str(en["home"]) + "/")
    assert str(services.speaker_py()) == sys.executable


def test_the_side_car_scripts_enrol_runs_are_in_the_package():
    for name in ("speaker-sidecar2.py", "cluster-room.py", "make-refs.py"):
        assert services._speakers_file(name).name == name
    assert ast.parse((Path(speakers.__file__)).read_text())                  # imports cleanly


def test_the_recorder_honours_its_stop_file_before_the_days_first_line(en, tmp_path):
    """--record-only waits for today's heard log; a STOP touched while it waits must end it,
    or `record` (which never kills) would leave it running for good."""
    d, logs = tmp_path / "rec", tmp_path / "logs"
    d.mkdir(); logs.mkdir()
    (d / "STOP").touch()
    p = subprocess.run([sys.executable, str(SIDECAR), "--record-only", str(d), "--stop-file", str(d / "STOP"),
                        "--logdir", str(logs)], capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, HF_HUB_OFFLINE="1"))
    assert p.returncode == 0 and "stop file: exiting" in p.stdout, p.stderr
