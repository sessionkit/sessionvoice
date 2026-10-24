"""EARS-429: `ears services install --only sounds` from the wheel, no checkout.

sounds is one service of two launch agents: the ears sounds (keeper/ears-sounds.py, label
dev.sessionkit.voice.ears-sounds) and the mouth sounds (keeper/mouth-sounds.py, label
dev.sessionkit.voice.mouth-sounds). Each runs on the agents' python, its script found in the
checkout's keeper/ or the wheel's ears/_keeper/, its settings, log and STOP file on the kit's
paths, never beside the script (inside the package, from a wheel). Opt-in: a bare install
never loads them. --dry-run prints both plists and writes nothing; status has a row for each;
uninstall --only sounds removes both.

Every test runs under a temp HOME with launchctl faked (test_services_speakers.sp): nothing
here loads a launch agent, reads the real HOME, or plays a sound."""
import ast
import os
import plistlib
import re
import sys
from importlib.resources import files
from pathlib import Path

import pytest

from ears import services
from test_services_install_checks import fence  # noqa: F401  (a fixture)
from test_services_speakers import sp  # noqa: F401  (a fixture)

REPO = Path(__file__).resolve().parents[1]
UID = os.getuid()
LOOK = ("print", "list")
JOBS = ("ears-sounds", "mouth-sounds")
LABELS = {"ears-sounds": "dev.sessionkit.voice.ears-sounds", "mouth-sounds": "dev.sessionkit.voice.mouth-sounds"}


@pytest.fixture
def snd(sp, monkeypatch):  # noqa: F811
    for k in ("EARS_RUN", "EARS_PY", "EARS_SOUNDS_SETTINGS", "MOUTH_SOUNDS_SETTINGS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(services, "weights_status", lambda: [])
    return sp


def _changing(sp):
    """launchctl calls that change launchd (print and list only look)."""
    return [a for a in sp["calls"]["launchctl"] if a[0] not in LOOK]


# ---- the service: in ALL, never in DEFAULT, two jobs -------------------------------------------

def test_sounds_is_a_service_of_two_jobs_and_opt_in():
    assert "sounds" in services.ALL and "sounds" not in services.DEFAULT
    assert services.jobs(["sounds"]) == list(JOBS)
    assert services.jobs(["mouth", "sounds", "ears-sounds"]) == ["mouth", *JOBS]      # once each
    for j in JOBS:
        assert services._label(j) == LABELS[j]
        assert services._old_labels(j) == []          # install never made them under another label
        assert services._labels(j) == [LABELS[j]]


def test_only_help_lists_sounds(capsys):
    with pytest.raises(SystemExit):
        services.main(["--help"])
    out = re.sub(r"-\s+", "-", " ".join(capsys.readouterr().out.split()))   # argparse wraps at a hyphen
    assert "speakers,sounds" in out and "sounds: ears-sounds and mouth-sounds" in out


def test_a_bare_install_never_loads_the_sounds(monkeypatch):
    got = {}
    monkeypatch.setattr(services, "install", lambda names, dry=False, **kw: got.update(install=names) or 0)
    monkeypatch.setattr(services, "uninstall", lambda names, dry=False: got.update(uninstall=names) or 0)
    services.main(["install", "--no-agent"])
    assert not set(JOBS) & set(got["install"])
    services.main(["install", "--only", "sounds"])
    assert got["install"] == list(JOBS)
    services.main(["uninstall"])                      # uninstall takes every service, sounds too
    assert set(JOBS) <= set(got["uninstall"])


def test_an_unknown_name_is_still_refused(capsys):
    with pytest.raises(SystemExit) as e:
        services.main(["install", "--only", "sound"])
    assert e.value.code == 2 and "unknown service ['sound']" in capsys.readouterr().err


# ---- spec(): the plists ---------------------------------------------------------------------

def test_the_ears_sounds_agent(snd):
    home = snd["home"]
    d = services.spec("ears-sounds")
    assert d["Label"] == LABELS["ears-sounds"]
    py = sys.executable
    assert d["ProgramArguments"] == [
        py, str(REPO / "keeper/ears-sounds.py"),
        "--settings", str(home / ".sessionkit/voice/settings/ears-sounds.json"),
        "--sound-log", str(services.LOGS / "ears-sounds.jsonl"),
        "--stop-file", str(home / ".sessionkit/voice/STOP-ears-sounds")]
    assert d["StandardOutPath"] == d["StandardErrorPath"] == str(services.LOGS / "ears-sounds.log")
    assert d["ThrottleInterval"] == 10 and d["RunAtLoad"] is True
    assert d["KeepAlive"] == {"SuccessfulExit": False}               # a STOP exits 0 and stays down
    assert d["WorkingDirectory"] == str(home)
    env = d["EnvironmentVariables"]
    assert env[services.MARKER[0]] == services.MARKER[1]
    assert "PATH" in env


def test_the_mouth_sounds_agent(snd):
    home = snd["home"]
    d = services.spec("mouth-sounds")
    assert d["Label"] == LABELS["mouth-sounds"]
    assert d["ProgramArguments"] == [sys.executable, str(REPO / "keeper/mouth-sounds.py")]
    assert d["StandardOutPath"] == d["StandardErrorPath"] == str(services.LOGS / "mouth-sounds.log")
    assert d["ThrottleInterval"] == 30 and d["KeepAlive"] == {"SuccessfulExit": False}
    env = d["EnvironmentVariables"]
    assert env[services.MARKER[0]] == services.MARKER[1]
    assert env["MOUTH_SOUNDS_SETTINGS"] == str(home / ".sessionkit/voice/settings/mouth-sounds.json")
    assert env["MOUTH_SOUNDS_LOG"] == str(services.LOGS / "mouth-sounds.jsonl")
    assert env["MOUTH_SOUNDS_STOP"] == str(home / ".sessionkit/voice/STOP-mouth-sounds")
    # where their writers put them (skills/ears/scripts/ears-mid-turn, ears-mouth-tail)
    assert env["MOUTH_SOUNDS_FIRED"] == str(home / ".cache/ears/run/mid-turn.fired")
    assert env["MOUTH_SOUNDS_CANNED"] == str(home / ".cache/ears/run/auto-opener.utts")


def test_the_marker_and_canned_follow_ears_run(snd, monkeypatch, tmp_path):
    monkeypatch.setenv("EARS_RUN", str(tmp_path / "run"))
    env = services.spec("mouth-sounds")["EnvironmentVariables"]
    assert env["MOUTH_SOUNDS_FIRED"] == str(tmp_path / "run/mid-turn.fired")
    assert env["MOUTH_SOUNDS_CANNED"] == str(tmp_path / "run/auto-opener.utts")


def test_the_writers_put_the_marker_and_canned_where_the_agent_looks():
    """The service's FIRED and CANNED are the keeper RUN dir's: the same default the scripts
    that write them use, so the agent hears what they write."""
    mid = (REPO / "skills/ears/scripts/ears-mid-turn").read_text()
    tail = (REPO / "skills/ears/scripts/ears-mouth-tail").read_text()
    assert '"~/.cache/ears/run", "mid-turn.fired"' in mid
    assert '"~/.cache/ears/run", "auto-opener.utts"' in tail
    assert 'os.environ.get("EARS_RUN")' in mid and 'os.environ.get("EARS_RUN")' in tail   # as keeper_run()


@pytest.mark.parametrize("job", JOBS)
def test_every_path_is_under_home_and_none_in_the_package(snd, job, monkeypatch, tmp_path):
    """From a wheel, a STOP file or a settings file beside the script would be inside the
    package (site-packages); every path the plist names but the script and python is HOME's."""
    monkeypatch.setattr(services, "ROOT", tmp_path / "no-checkout")
    d = services.spec(job)
    script = Path(d["ProgramArguments"][1])
    assert script.parent == Path(str(files("ears"))) / "_keeper"
    env = d["EnvironmentVariables"]
    paths = [a for a in d["ProgramArguments"][2:] if a.startswith("/")] + [
        v for k, v in env.items() if k.startswith("MOUTH_SOUNDS_")] + [d["StandardOutPath"], d["WorkingDirectory"]]
    assert paths
    for p in paths:
        assert p.startswith(str(snd["home"]) + "/") or p == str(snd["home"]), p
        assert not p.startswith(str(script.parent)), p


@pytest.mark.parametrize("job", JOBS)
def test_from_a_wheel_the_job_runs_the_shipped_script(snd, job, monkeypatch, tmp_path):
    monkeypatch.setattr(services, "ROOT", tmp_path / "no-checkout")
    got = Path(services.spec(job)["ProgramArguments"][1])
    assert got == Path(str(files("ears") / "_keeper" / f"{job}.py"))
    assert got.read_bytes() == (REPO / "keeper" / f"{job}.py").read_bytes()
    if job == "ears-sounds":
        assert (got.parent / "held_stream.py").is_file()


def test_no_held_stream_beside_ears_sounds_refuses_and_writes_nothing(snd, monkeypatch, tmp_path, capsys):
    shipped = tmp_path / "pkg/_keeper"
    shipped.mkdir(parents=True)
    for name in ("ears-sounds.py", "mouth-sounds.py"):       # held_stream.py missing
        (shipped / name).write_text("")
    import importlib.resources
    monkeypatch.setattr(services, "ROOT", tmp_path / "no-checkout")
    monkeypatch.setattr(importlib.resources, "files", lambda pkg: tmp_path / "pkg")
    for dry in (True, False):
        assert services.install(["sounds"], dry_run=dry) == 2
        err = capsys.readouterr().err
        assert "REFUSED" in err and "held_stream.py" in err
    assert not services.AGENTS.exists() and not services.LOGS.exists()
    assert _changing(snd) == []


# ---- the hand templates: only the paths, the marker and the restart rule differ ----------------

def _template(job) -> dict:
    vals = {"HOME": "/Users/x", "EARS_REPO": "/r", "PYTHON": "/p/python"}
    body = re.sub(r"<!--.*?-->", "", (REPO / f"launchd/{LABELS[job]}.plist.in").read_text(), flags=re.S)
    return plistlib.loads(re.sub(r"@([A-Z_]+)@", lambda m: vals[m.group(1)], body).encode())


@pytest.mark.parametrize("job", JOBS)
def test_template_parity(snd, job):
    t, d = _template(job), services.spec(job)
    assert t["Label"] == d["Label"] == LABELS[job]            # one label per job, whoever installs it
    assert d["ThrottleInterval"] == t["ThrottleInterval"] and d["RunAtLoad"] == t["RunAtLoad"]
    assert Path(t["ProgramArguments"][1]).name == Path(d["ProgramArguments"][1]).name == f"{job}.py"
    for k in ("StandardOutPath", "StandardErrorPath"):
        assert Path(d[k]).name == Path(t[k]).name == f"{job}.log"
    assert services.MARKER[0] in d["EnvironmentVariables"] and services.MARKER[0] not in t["EnvironmentVariables"]


# ---- what the scripts need: shipped beside them, and the python's modules ----------------------

@pytest.mark.parametrize("script", ["ears-sounds.py", "mouth-sounds.py"])
def test_every_module_a_sounds_script_imports_from_beside_itself_ships(script):
    """A module the script imports that lives in keeper/ must ship in ears/_keeper/ (from a
    wheel the script's dir is the only place it looks): held_stream.py for ears-sounds."""
    tree = ast.parse((REPO / "keeper" / script).read_text())
    mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    beside = {m for m in mods if (REPO / "keeper" / f"{m}.py").is_file()}
    if script == "ears-sounds.py":
        assert beside == {"held_stream"}
    for m in beside:
        assert (files("ears") / "_keeper" / f"{m}.py").is_file(), m
        assert f"{m}.py" in services.SOUNDS_FILES[script.removesuffix(".py")]


def test_the_agents_python_has_what_the_sounds_import():
    """Both run on the agents' python (the kit's venv): numpy and sounddevice must be BASE
    dependencies, not an extra, or the agent dies on import."""
    tomllib = pytest.importorskip("tomllib")
    deps = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["dependencies"]
    assert {"numpy", "sounddevice"} <= {re.split(r"[<>=!~\[ ;]", d)[0] for d in deps}


# ---- install, uninstall, status ---------------------------------------------------------------

def _tree(root):
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None) for p in sorted(root.rglob("*"))}


def test_install_dry_run_prints_both_plists_and_writes_nothing(snd, capsys, tmp_path):
    before = _tree(tmp_path)
    assert services.main(["install", "--only", "sounds", "--dry-run"]) == 0
    out = capsys.readouterr().out
    for job in JOBS:
        path = services.AGENTS / f"{LABELS[job]}.plist"
        assert f"--- {path}" in out
        assert f"<string>{LABELS[job]}</string>" in out
    assert "STOP-ears-sounds" in out and "STOP-mouth-sounds" in out
    assert _changing(snd) == []
    assert _tree(tmp_path) == before


def test_install_writes_and_loads_both(snd, capsys):
    assert services.main(["install", "--only", "sounds"]) == 0
    out = capsys.readouterr().out
    for job in JOBS:
        path = services.AGENTS / f"{LABELS[job]}.plist"
        assert services._ours(path)
        assert plistlib.loads(path.read_bytes()) == services.spec(job)
        assert ("bootstrap", f"gui/{UID}", str(path)) in snd["calls"]["launchctl"]
        assert re.search(rf"^{job}\s+loaded\s+{re.escape(str(path))}$", out, re.M), out
    # the folder mouth-sounds writes its default settings to: it fails without it
    assert (snd["home"] / ".sessionkit/voice/settings").is_dir()
    assert services.LOGS.is_dir()
    # nothing else is installed
    assert {p.name for p in services.AGENTS.iterdir()} == {f"{LABELS[j]}.plist" for j in JOBS}


def test_a_rerun_replaces_ours_with_the_same_job(snd, capsys):
    services.main(["install", "--only", "sounds"])
    first = {j: (services.AGENTS / f"{LABELS[j]}.plist").read_bytes() for j in JOBS}
    services.main(["install", "--only", "sounds"])
    assert "SKIPPED" not in capsys.readouterr().out
    assert {j: (services.AGENTS / f"{LABELS[j]}.plist").read_bytes() for j in JOBS} == first


@pytest.mark.parametrize("job", JOBS)
def test_a_hand_render_at_our_label_is_left_and_ours_skipped(snd, capsys, job):
    services.AGENTS.mkdir(parents=True)
    hand = services.AGENTS / f"{LABELS[job]}.plist"
    hand.write_text("<plist>hand</plist>")
    snd["listed"][LABELS[job]] = "42"
    assert services.install(["sounds"]) == 0
    out = capsys.readouterr().out
    assert f"{job} SKIPPED" in out and "would play twice" in out
    assert hand.read_text() == "<plist>hand</plist>"
    assert not [a for a in _changing(snd) if LABELS[job] in " ".join(a)]
    other = next(j for j in JOBS if j != job)
    assert services._ours(services.AGENTS / f"{LABELS[other]}.plist")   # the other one installs
    # the hint's command is one --only accepts: the service, never the job (--only ears-sounds
    # is refused)
    hinted = re.findall(r"`ears services install --only (\S+)`", out)
    assert hinted == ["sounds"], out


def test_every_install_hint_names_a_service_only_accepts():
    for j in JOBS:
        assert services._service(j) == "sounds"
    for n in ("stt", "speech", "mouth", "keeper", "mail", "speakers"):
        assert services._service(n) == n
    assert all(services._service(j) in services.ALL for j in services.jobs(services.ALL))


def test_uninstall_removes_both(snd, capsys):
    services.main(["install", "--only", "sounds"])
    capsys.readouterr()
    assert services.main(["uninstall", "--only", "sounds"]) == 0
    out = capsys.readouterr().out
    for job in JOBS:
        assert ("bootout", f"gui/{UID}/{LABELS[job]}") in snd["calls"]["launchctl"]
        assert not (services.AGENTS / f"{LABELS[job]}.plist").exists()
        assert f"{job} removed" in out


def test_uninstall_dry_run_names_both_and_changes_nothing(snd, capsys, tmp_path):
    services.main(["install", "--only", "sounds"])
    capsys.readouterr()
    calls = len(snd["calls"]["launchctl"])
    before = _tree(tmp_path)
    assert services.main(["uninstall", "--only", "sounds", "--dry-run"]) == 0
    out = capsys.readouterr().out
    for job in JOBS:
        assert f"would boot out gui/{UID}/{LABELS[job]}" in out
        assert f"would delete {services.AGENTS / (LABELS[job] + '.plist')}" in out
    assert snd["calls"]["launchctl"][calls:] == []
    assert _tree(tmp_path) == before


def test_status_reports_both(snd, capsys):
    services.status()
    out = capsys.readouterr().out
    for job in JOBS:
        assert re.search(rf"^{job}\s+not installed$", out, re.M), out
    services.main(["install", "--only", "sounds"])
    snd["listed"].update({LABELS["ears-sounds"]: "101", LABELS["mouth-sounds"]: "-"})
    capsys.readouterr()
    services.status()
    out = capsys.readouterr().out
    assert re.search(r"^ears-sounds\s+running, pid 101$", out, re.M), out
    assert re.search(r"^mouth-sounds\s+loaded, not running$", out, re.M), out
