"""EARS-258 do-001: the speaker side-car's scripts run from a package, not only a checkout.

- src/ears/_speakers/ holds symlinks into speakers/ (the EARS-202 _keeper pattern; the build
  copies the files once pyproject names them, do-005), and services._speakers_file() finds
  each one in the checkout first, then the package, else refuses (NoSpeakers).
- run.sh reads its data (refs/, refs-call/, STOP) from $SPEAKER_HOME when set, runs the
  side-car from beside itself, and passes --logdir only when $SPEAKER_LOGDIR is set. Both
  unset: the argv is the one test_speakers_parity pins.
- The side-car's call-leg helper is found one up from ears/_speakers/ (ears/callrec.py) too.

No model, no mic, no launchd: the stub side-car of test_speakers_parity, lifted functions."""
import importlib
import json
import shutil
import sys
from importlib.resources import files
from pathlib import Path

import pytest

from ears import services
from test_speakers_follow import lift
from test_speakers_parity import FAKE, RUN, SC, reap, start  # noqa: F401  (reap: a fixture)

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = ("run.sh", "speaker-sidecar2.py", "cluster-room.py", "make-refs.py",
           "requirements.lock")   # the service venv's pins, which install reads (do-004)

# the parity stub, also saying which file it was run as (argv[0]: run.sh names it by path)
FAKE0 = FAKE.replace('{"pid": os.getpid(),', '{"argv0": sys.argv[0], "pid": os.getpid(),')
assert FAKE0 != FAKE


# ---- ears/_speakers/ and services._speakers_file() ----

@pytest.mark.parametrize("name", SCRIPTS)
def test_the_package_holds_each_speakers_script_as_a_link_into_speakers(name):
    link = REPO / "src" / "ears" / "_speakers" / name
    assert link.is_symlink(), f"src/ears/_speakers/{name} is not a symlink"
    assert link.resolve() == (REPO / "speakers" / name).resolve()
    got = files("ears") / "_speakers" / name
    assert got.is_file() and got.read_bytes() == (REPO / "speakers" / name).read_bytes()


@pytest.mark.parametrize("name", SCRIPTS)
def test_a_checkout_runs_its_own_speakers_scripts(name):
    assert services._speakers_file(name) == REPO / "speakers" / name


@pytest.mark.parametrize("name", SCRIPTS)
def test_from_a_wheel_the_speakers_scripts_are_the_shipped_ones(monkeypatch, tmp_path, name):
    # installed, ROOT is <venv>/lib/python3.X: no speakers/ there
    monkeypatch.setattr(services, "ROOT", tmp_path)
    got = services._speakers_file(name)
    assert got.is_file() and got.name == name
    assert got.parent.name == "_speakers" and got.parent.parent.name == "ears"


def test_no_speakers_script_anywhere_refuses_naming_it(monkeypatch, tmp_path):
    import importlib.resources
    monkeypatch.setattr(services, "ROOT", tmp_path / "root")
    monkeypatch.setattr(importlib.resources, "files", lambda pkg: tmp_path / "pkg")
    with pytest.raises(services.NoSpeakers) as e:
        services._speakers_file("run.sh")
    msg = str(e.value)
    assert "run.sh" in msg and str(tmp_path / "root" / "speakers" / "run.sh") in msg
    assert "ears/_speakers/" in msg
    # a NoKeeper, so install's existing refusal path (no plist written) catches it too
    assert issubclass(services.NoSpeakers, services.NoKeeper)


def test_the_keeper_refusal_still_names_the_keeper_package(monkeypatch, tmp_path):
    """_keeper_file is now _shipped_file("keeper", "_keeper", ...): its words are unchanged."""
    import importlib.resources
    monkeypatch.setattr(services, "ROOT", tmp_path / "root")
    monkeypatch.setattr(importlib.resources, "files", lambda pkg: tmp_path / "pkg")
    with pytest.raises(services.NoKeeper) as e:
        services.keeper_script()
    assert str(e.value) == (
        f"no ears-keeper.sh: not at {tmp_path / 'root' / 'keeper' / 'ears-keeper.sh'} (a checkout) "
        f"nor at {tmp_path / 'pkg' / '_keeper' / 'ears-keeper.sh'} (the package). Reinstall "
        "sessionvoice from a wheel that ships ears/_keeper/, or run `ears services install` "
        "from a git checkout (uv run ears ...)")


# ---- run.sh: SPEAKER_HOME and SPEAKER_LOGDIR ----

def split(tmp_path, call=True):
    """The scripts in one dir (as in a package: run.sh + the stub side-car, no data) and the
    data in another (the speakers home: refs/, refs-call/). launchd's env, as the parity
    tests give it."""
    scripts, home = tmp_path / "pkg" / "_speakers", tmp_path / "home"
    scripts.mkdir(parents=True)
    (home / "refs").mkdir(parents=True)
    shutil.copy(RUN, scripts / "run.sh")
    (scripts / "speaker-sidecar2.py").write_text(FAKE0)
    for n in ("alice", "theo", "dave"):
        (home / "refs" / f"{n}.wav").write_bytes(b"")
    if call:
        (home / "refs-call").mkdir()
        (home / "refs-call" / "theo.wav").write_bytes(b"")
    py = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
           "SPEAKER_PY": str(py), "SPEAKER_DEVICE": "follow", "SPEAKER_AGENT_REFS": "dave"}
    return scripts, home, env


def started_in(d, p):
    import time
    t = time.time() + 30
    while not (d / "argv.json").exists():
        if p.poll() is not None:
            pytest.fail(f"run.sh exited {p.returncode}: {p.stderr.read()}")
        assert time.time() < t, "the stub side-car never started"
        time.sleep(0.05)
    return json.loads((d / "argv.json").read_text())


PARITY_ARGV = ["--device", "follow",
               "--enroll", "alice=refs/alice.wav", "--enroll", "dave=refs/dave.wav",
               "--enroll", "theo=refs/theo.wav",
               "--call-enroll", "theo=refs-call/theo.wav",
               "--agent-refs", "dave", "--enroll-s", "12", "--ring", "300",
               "--stop-file", "STOP"]


def test_speaker_home_holds_the_data_and_the_side_car_runs_from_beside_run_sh(reap, tmp_path):
    """SPEAKER_HOME set: run.sh works in the home (refs, refs-call, a stale STOP cleared, the
    STOP that ends it), and runs the side-car from its own dir by absolute path. The argv is
    the parity one: refs paths stay relative to the cwd, which is the home."""
    scripts, home, env = split(tmp_path)
    (home / "STOP").touch()                       # stale: cleared at start, as without a home
    p = start(scripts, env, reap, SPEAKER_HOME=str(home))
    got = started_in(home, p)
    assert got["pid"] == p.pid, "run.sh did not exec the side-car"
    assert got["argv0"] == str(scripts.resolve() / "speaker-sidecar2.py")
    assert got["argv"] == PARITY_ARGV
    assert got["stop_at_start"] is False
    assert not (scripts / "argv.json").exists(), "it ran in the scripts' dir, not the home"
    (home / "STOP").touch()
    assert p.wait(timeout=15) == 0


def test_with_no_refs_in_the_home_run_sh_refuses_before_python(reap, tmp_path):
    """The refs it counts are the home's: refs beside the scripts do not count."""
    scripts, home, env = split(tmp_path, call=False)
    shutil.rmtree(home / "refs")
    (scripts / "refs").mkdir()
    (scripts / "refs" / "alice.wav").write_bytes(b"")
    p = start(scripts, env, reap, SPEAKER_HOME=str(home))
    assert p.wait(timeout=15) == 1
    assert "no refs/*.wav" in p.stderr.read()
    assert not (home / "argv.json").exists() and not (scripts / "argv.json").exists()


def test_speaker_logdir_is_passed_as_logdir_only_when_set(reap, tmp_path):
    """SPEAKER_LOGDIR (the ears' heard.log_dir()): --logdir <it>, one argument even with a
    space, after the parity argv. Unset (the next test): no --logdir at all."""
    scripts, home, env = split(tmp_path)
    logdir = tmp_path / "base dir" / "logs" / "conversations"
    p = start(scripts, env, reap, SPEAKER_HOME=str(home), SPEAKER_LOGDIR=str(logdir))
    got = started_in(home, p)
    assert got["argv"] == PARITY_ARGV + ["--logdir", str(logdir)]
    (home / "STOP").touch()
    assert p.wait(timeout=15) == 0


@pytest.mark.parametrize("logdir", [None, ""])
def test_unset_or_empty_speaker_logdir_passes_no_logdir(reap, tmp_path, logdir):
    scripts, home, env = split(tmp_path)
    kw = {"SPEAKER_HOME": str(home)}
    if logdir is not None:
        kw["SPEAKER_LOGDIR"] = logdir
    p = start(scripts, env, reap, **kw)
    got = started_in(home, p)
    assert "--logdir" not in got["argv"] and got["argv"] == PARITY_ARGV
    (home / "STOP").touch()
    assert p.wait(timeout=15) == 0


def test_with_neither_set_run_sh_runs_in_its_own_dir_as_before(reap, tmp_path):
    """The hand setup: no SPEAKER_HOME, no SPEAKER_LOGDIR. The data sit beside run.sh, the
    side-car is the one beside it, and the argv is the parity one."""
    scripts, home, env = split(tmp_path)
    shutil.move(str(home / "refs"), str(scripts / "refs"))
    shutil.move(str(home / "refs-call"), str(scripts / "refs-call"))
    p = start(scripts, env, reap)
    got = started_in(scripts, p)
    assert got["argv0"] == str(scripts.resolve() / "speaker-sidecar2.py")
    assert got["argv"] == PARITY_ARGV
    (scripts / "STOP").touch()
    assert p.wait(timeout=15) == 0


# ---- the side-car's call-leg helper from a package ----

def test_from_a_wheel_the_side_car_finds_callrec_one_up_from_speakers(tmp_path):
    """In a wheel the side-car is site-packages/ears/_speakers/speaker-sidecar2.py and the
    helper is site-packages/ears/callrec.py: the second candidate."""
    ears = tmp_path / "site-packages" / "ears"
    (ears / "_speakers").mkdir(parents=True)
    shutil.copy(SC, ears / "_speakers" / "speaker-sidecar2.py")
    shutil.copy(REPO / "src" / "ears" / "callrec.py", ears / "callrec.py")
    ns = lift("_load_callrec", "callrec", __file__=str(ears / "_speakers" / "speaker-sidecar2.py"),
              importlib=importlib, Path=Path, CALL={"names": ["theo"]}, sys=sys)
    assert Path(ns["callrec"].__file__) == ears / "callrec.py"
    assert ns["callrec"].__name__ == "callrec"


def test_in_a_checkout_the_package_link_still_loads_src_ears_callrec(tmp_path):
    """Run as src/ears/_speakers/speaker-sidecar2.py (the link), resolve() lands in speakers/,
    so the first candidate, src/ears/callrec.py, still wins."""
    link = REPO / "src" / "ears" / "_speakers" / "speaker-sidecar2.py"
    ns = lift("_load_callrec", "callrec", __file__=str(link), importlib=importlib, Path=Path,
              CALL={"names": ["theo"]}, sys=sys)
    assert Path(ns["callrec"].__file__).resolve() == (REPO / "src" / "ears" / "callrec.py").resolve()


def test_with_neither_candidate_a_call_leg_refuses_naming_both(tmp_path):
    copy = tmp_path / "speaker-sidecar" / "speaker-sidecar2.py"
    with pytest.raises(SystemExit) as e:
        lift("_load_callrec", "callrec", __file__=str(copy), importlib=importlib, Path=Path,
             CALL={"names": ["theo"]}, sys=sys)
    msg = str(e.value)
    assert str(tmp_path / "src" / "ears" / "callrec.py") in msg
    assert str(tmp_path / "callrec.py") in msg


# ---- speakers/README.md leads with the service path (EARS-258 do-005, SC11) ----

def test_the_readme_leads_with_the_service_path_and_no_git_url():
    text = (REPO / "speakers" / "README.md").read_text()
    sections = [l for l in text.splitlines() if l.startswith("## ")]
    assert sections[0].startswith("## Install, enrol, status, uninstall"), sections[0]
    lead = text.split(sections[1], 1)[0]                  # everything before the second section
    for cmd in ("ears services install --only speakers", "ears speakers enrol",
                "ears services status", "ears services uninstall --only speakers"):
        assert cmd in lead, cmd
    assert "git+" not in text                             # the git-built hand venv is not documented
    assert "-r requirements.lock" in text                 # the hand path uses the service's own pins
