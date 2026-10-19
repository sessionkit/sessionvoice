"""EARS-258 do-002: the speaker side-car as an opt-in `ears services` service.

- spec("speakers") is the hand template's job (launchd/dev.sessionkit.voice.ears-speakers.plist.in)
  under our label, its run.sh found in the checkout or the package, its data in the
  per-user speakers home;
- a bare install never installs it (SC2); uninstall removes ours and only ours (SC3);
- a hand-made sidecar on the host means ours is skipped, from either order (SC4);
- its status row is exactly one state, and install --dry-run prints it first (SC5);
- a stray speaker-sidecar2.py that is not our job is warned about, never killed (Q9).

Every test runs under a temp HOME (test_services_install_checks' fence), with launchctl and
pgrep faked: nothing here loads a launch agent, reads the real HOME, or looks at this host's
processes."""
import os
import plistlib
import re
import subprocess
from importlib.resources import files
from pathlib import Path

import pytest

from ears import heard, services
from test_services_install_checks import fence  # noqa: F401  (a fixture)
from test_services_label_move import ours_plist, print_out

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "launchd" / "dev.sessionkit.voice.ears-speakers.plist.in"
OURS = "dev.sessionkit.voice.ears-speakers"
UID = os.getuid()


@pytest.fixture
def sp(fence, monkeypatch):
    """The fence, plus: `launchctl list` answers from ``listed`` ({label: pid or "-"}), print
    answers for ``loaded`` or a listed label, and pgrep from ``procs``. The speakers home is
    the default one, under the temp HOME."""
    for k in ("EARS_SPEAKERS_HOME", "EARS_SPEAKER_PY", "EARS_SPEAKER_AGENT_REFS", "VOICEMODE_BASE_DIR"):
        monkeypatch.delenv(k, raising=False)
    listed, procs, calls = {}, [], fence["calls"]

    def launchctl(*a):
        calls["launchctl"].append(a)
        if a[0] == "print":
            label = a[1].split("/")[-1]
            if not (label in fence["loaded"] or label in listed):
                return subprocess.CompletedProcess(a, 113, "", "")
            return subprocess.CompletedProcess(a, 0, print_out(label, fence["env"].get(label, {})), "")
        if a[0] == "list":
            out = "PID\tStatus\tLabel\n" + "".join(f"{pid}\t0\t{label}\n" for label, pid in listed.items())
            return subprocess.CompletedProcess(a, 0, out, "")
        return subprocess.CompletedProcess(a, 0, "", "")

    monkeypatch.setattr(services, "_launchctl", launchctl)
    monkeypatch.setattr(services, "_pgrep", lambda pattern: list(procs))
    # do-004's install checks, passing: an arm64 macOS 14+ host whose venv build and model
    # download are faked (test_services_speakers_checks covers each refusal)
    sh = []
    monkeypatch.setattr(services, "_mac", lambda: ("arm64", "15.0"))
    monkeypatch.setattr(services, "_uv", lambda: "/fake/uv")
    monkeypatch.setattr(services, "_speakers_sh", lambda argv: sh.append(argv) or subprocess.CompletedProcess(argv, 0, "", ""))
    monkeypatch.setattr(services, "speakers_missing_module", lambda py: None)
    monkeypatch.setattr(services, "speakers_model_cached", lambda py: True)
    fence.update(listed=listed, procs=procs, sh=sh, speakers=fence["home"] / ".local/share/ears/speakers")
    return fence


def _plist():
    return services.AGENTS / f"{OURS}.plist"


def _enrol(sp, *names):
    refs = sp["speakers"] / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    for n in names:
        (refs / f"{n}.wav").write_bytes(b"RIFF")


def _touched(sp, label):
    """launchctl calls that change LABEL's job (print and list only look)."""
    return [a for a in sp["calls"]["launchctl"] if a[0] not in ("print", "list") and label in " ".join(a)]


def _hand_plist(label, text="HAND"):
    p = services.AGENTS / f"{label}.plist"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


# ---- spec("speakers"): design s4 -----------------------------------------------------------

def test_speakers_is_a_service_and_opt_in():
    assert "speakers" in services.ALL
    assert "speakers" not in services.DEFAULT                          # a bare install: never speakers
    assert services.DEFAULT == ("speech", "mouth", "keeper", "mail")   # speech since EARS-291


def test_the_speakers_agent_is_the_template_job_under_our_label(sp):
    home = sp["speakers"]
    d = services.spec("speakers")
    assert d["Label"] == OURS
    assert d["ProgramArguments"] == ["/bin/bash", str(services._speakers_file("run.sh"))]
    assert d["WorkingDirectory"] == str(home)
    assert d["KeepAlive"] == {"SuccessfulExit": False}                # touch STOP keeps it down
    assert d["ThrottleInterval"] == 30 and d["RunAtLoad"] is True
    assert d["StandardOutPath"] == d["StandardErrorPath"] == str(services.LOGS / "ears-speakers.log")
    env = d["EnvironmentVariables"]
    assert env["SPEAKER_PY"] == str(home / "venv/bin/python")
    assert env["SPEAKER_HOME"] == str(home)
    assert env["SPEAKER_DEVICE"] == "follow" and env["HF_HUB_OFFLINE"] == "1"
    assert env["SPEAKER_AGENT_REFS"] == ""
    assert "EARS_PY" not in env                                        # it runs on SPEAKER_PY
    assert "PATH" in env


def test_the_overrides(sp, monkeypatch, tmp_path):
    monkeypatch.setenv("EARS_SPEAKERS_HOME", str(tmp_path / "elsewhere"))
    monkeypatch.setenv("EARS_SPEAKER_PY", str(tmp_path / "py/bin/python"))
    monkeypatch.setenv("EARS_SPEAKER_AGENT_REFS", "dave,vera")
    d = services.spec("speakers")
    env = d["EnvironmentVariables"]
    assert d["WorkingDirectory"] == env["SPEAKER_HOME"] == str(tmp_path / "elsewhere")
    assert env["SPEAKER_PY"] == str(tmp_path / "py/bin/python")
    assert env["SPEAKER_AGENT_REFS"] == "dave,vera"


def test_without_an_override_the_python_is_the_service_venv_in_the_home(sp, monkeypatch, tmp_path):
    monkeypatch.setenv("EARS_SPEAKERS_HOME", str(tmp_path / "h"))
    assert services.speaker_py() == tmp_path / "h/venv/bin/python"


def test_from_a_package_the_job_runs_the_shipped_run_sh(sp, monkeypatch, tmp_path):
    # SC1's program path: no checkout above the module, so run.sh is the package's copy
    monkeypatch.setattr(services, "ROOT", tmp_path / "no-checkout")
    run = services.spec("speakers")["ProgramArguments"][1]
    assert run == str(files("ears") / "_speakers" / "run.sh")
    assert Path(run).parent == Path(str(files("ears"))) / "_speakers"


def test_data_paths_lie_under_home(sp):
    d = services.spec("speakers")
    home = str(sp["home"])
    for path in (d["WorkingDirectory"], d["EnvironmentVariables"]["SPEAKER_HOME"],
                 d["EnvironmentVariables"]["SPEAKER_LOGDIR"], d["StandardOutPath"]):
        assert path.startswith(home + "/"), path


def _template(tmp_path) -> dict:
    """The hand template rendered with dummy values (bin/render-plist fills only @NAME@)."""
    vals = {"HOME": "/Users/x", "EARS_REPO": "/r", "SPEAKER_PY": "/p/python", "AGENT_REFS": ""}
    body = re.sub(r"<!--.*?-->", "", TEMPLATE.read_text(), flags=re.S)
    return plistlib.loads(re.sub(r"@([A-Z_]+)@", lambda m: vals[m.group(1)], body).encode())


def test_template_parity(sp, tmp_path):
    # only the paths and install's MARKER may differ from the hand template; the label is one
    t, d = _template(tmp_path), services.spec("speakers")
    for k in ("KeepAlive", "ThrottleInterval", "RunAtLoad"):
        assert d[k] == t[k], k
    for k in ("StandardOutPath", "StandardErrorPath"):
        assert Path(d[k]).name == Path(t[k]).name == "ears-speakers.log"
    for k in ("SPEAKER_DEVICE", "HF_HUB_OFFLINE"):
        assert d["EnvironmentVariables"][k] == t["EnvironmentVariables"][k], k
    assert set(t["EnvironmentVariables"]) <= set(d["EnvironmentVariables"])   # every key it sets
    assert t["ProgramArguments"][0] == d["ProgramArguments"][0] == "/bin/bash"
    assert Path(t["ProgramArguments"][1]).name == Path(d["ProgramArguments"][1]).name == "run.sh"
    # ONE label per job, whoever installs it (EARS-407): told apart by content, never by name
    assert t["Label"] == d["Label"] == "dev.sessionkit.voice.ears-speakers"
    assert services.MARKER[0] in d["EnvironmentVariables"] and services.MARKER[0] not in t["EnvironmentVariables"]


def test_home_and_the_heard_log_are_pinned_from_install(sp):
    env = services.spec("speakers")["EnvironmentVariables"]
    assert env["HOME"] == str(sp["home"])
    # a fresh home: sessionvoice's own (EARS-294 T7)
    assert env["SPEAKER_LOGDIR"] == str(sp["home"] / ".sessionvoice/logs/conversations") == str(heard.log_dir())
    # a host whose heard log is in ~/.voicemode keeps it there: adopt in place, nothing moved
    old = sp["home"] / ".voicemode/logs/conversations"
    old.mkdir(parents=True)
    (old / "heard.lock").write_text("")
    env = services.spec("speakers")["EnvironmentVariables"]
    assert env["SPEAKER_LOGDIR"] == str(old) == str(heard.log_dir())


def test_the_heard_log_follows_voicemode_base_dir(sp, monkeypatch, tmp_path):
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm"))
    env = services.spec("speakers")["EnvironmentVariables"]
    assert env["SPEAKER_LOGDIR"] == str(tmp_path / "vm/logs/conversations")
    assert env["HOME"] == str(sp["home"])


# ---- SC2: a bare install has no speakers agent -------------------------------------------

def _verbs(monkeypatch, argv):
    got = {}
    monkeypatch.setattr(services, "install", lambda names, dry=False, **kw: got.update(install=names) or 0)
    monkeypatch.setattr(services, "uninstall", lambda names, dry=False: got.update(uninstall=names) or 0)
    services.main(argv)
    return got


def test_a_bare_install_names_no_speakers_and_a_bare_uninstall_does(monkeypatch):
    assert "speakers" not in _verbs(monkeypatch, ["install"])["install"]
    assert "speakers" not in _verbs(monkeypatch, ["install", "--dry-run"])["install"]
    assert "speakers" not in _verbs(monkeypatch, ["install", "--apple"])["install"]
    assert _verbs(monkeypatch, ["install", "--only", "speakers"])["install"] == ["speakers"]
    assert "speakers" in _verbs(monkeypatch, ["uninstall"])["uninstall"]


@pytest.mark.parametrize("dry_run", [False, True])
def test_a_bare_install_writes_and_prints_no_speakers_agent(sp, capsys, dry_run):
    from test_services_install_checks import _store
    _store(sp["home"])
    assert services.main(["install"] + (["--dry-run"] if dry_run else [])) == 0
    out = capsys.readouterr().out
    assert "ears-speakers" not in out and "SPEAKER_" not in out            # no plist printed
    assert not any(l.startswith("speakers") for l in out.splitlines())      # and no row
    assert not _plist().exists() and not sp["speakers"].exists()
    assert not [a for a in sp["calls"]["launchctl"] if "ears-speakers" in " ".join(a)]


# ---- install --only speakers ---------------------------------------------------------------

def test_install_only_speakers_writes_loads_and_makes_a_private_home(sp, capsys):
    assert services.main(["install", "--only", "speakers"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("speakers not installed")                   # the row, first
    assert plistlib.loads(_plist().read_bytes()) == services.spec("speakers")
    assert ("bootstrap", f"gui/{UID}", str(_plist())) in sp["calls"]["launchctl"]
    assert sp["speakers"].is_dir() and (sp["speakers"].stat().st_mode & 0o777) == 0o700
    assert any(l.startswith("speakers loaded") for l in out)


def test_an_existing_home_is_made_private_and_its_refs_kept(sp):
    _enrol(sp, "theo")
    sp["speakers"].chmod(0o755)
    assert services.install(["speakers"]) == 0
    assert (sp["speakers"].stat().st_mode & 0o777) == 0o700
    assert (sp["speakers"] / "refs/theo.wav").read_bytes() == b"RIFF"


def test_dry_run_changes_nothing_and_prints_the_plist(sp, capsys):
    assert services.install(["speakers"], dry_run=True) == 0
    out = capsys.readouterr().out
    assert f"--- {_plist()}" in out and OURS in out
    assert not _plist().exists() and not sp["speakers"].exists()
    assert not _touched(sp, OURS)


def _tree(root: Path) -> list[tuple[str, int]]:
    return sorted((str(p.relative_to(root)), p.stat().st_mode) for p in root.rglob("*"))


@pytest.mark.parametrize("argv", [["install", "--only", "speakers", "--dry-run"], ["install", "--dry-run"]])
def test_a_dry_run_leaves_the_whole_home_as_it_was(sp, capsys, argv):
    """SC1 "changes nothing", measured over the whole HOME: the do-006 proof run found a dry
    run making an empty ~/Library/Logs/ears (install's LOGS.mkdir ran before any dry_run test)."""
    before = _tree(sp["home"])
    services.main(argv)
    assert _tree(sp["home"]) == before
    assert not services.LOGS.exists()


def test_no_run_sh_anywhere_refuses_and_writes_nothing(sp, monkeypatch, capsys):
    def none(name):
        raise services.NoSpeakers("no run.sh")
    monkeypatch.setattr(services, "_speakers_file", none)
    assert services.install(["speakers"]) == 2
    assert "REFUSED" in capsys.readouterr().err
    assert not _plist().exists() and not sp["calls"]["launchctl"]


# ---- SC3: uninstall ------------------------------------------------------------------------

@pytest.mark.parametrize("argv", [["uninstall", "--only", "speakers"], ["uninstall"]])
def test_uninstall_removes_ours_keeps_refs_and_never_touches_a_hand_one(sp, capsys, argv):
    assert services.install(["speakers"]) == 0
    _enrol(sp, "theo")
    hands = [_hand_plist(label) for label in services.HAND_SPEAKERS]
    sp["calls"]["launchctl"].clear()
    assert services.main(argv) == 0
    assert ("bootout", f"gui/{UID}/{OURS}") in sp["calls"]["launchctl"]
    assert not _plist().exists()
    assert (sp["speakers"] / "refs/theo.wav").read_bytes() == b"RIFF"    # references survive
    assert all(h.read_text() == "HAND" for h in hands)                    # theirs untouched
    for label in services.HAND_SPEAKERS:
        assert not _touched(sp, label)
    assert "speakers removed" in capsys.readouterr().out


# ---- SC4: never two sidecars ---------------------------------------------------------------

@pytest.mark.parametrize("label", services.HAND_SPEAKERS)
@pytest.mark.parametrize("how", ["plist", "loaded"])
def test_a_hand_made_sidecar_means_ours_is_skipped(sp, capsys, label, how):
    hand = _hand_plist(label) if how == "plist" else None
    if how == "loaded":
        sp["loaded"].add(label)
    assert services.install(["speakers"]) == 0
    out = capsys.readouterr().out.splitlines()
    named = [l for l in out if label in l]
    assert len(named) == 1 and "speakers SKIPPED" in named[0]            # one line, naming it
    assert not _plist().exists()
    if hand:
        assert hand.read_text() == "HAND"
    assert not _touched(sp, "ears-speakers") and not _touched(sp, label)


@pytest.mark.parametrize("label", services.HAND_SPEAKERS)
@pytest.mark.parametrize("how", ["plist", "loaded"])
def test_ours_installed_before_a_hand_made_one_goes(sp, capsys, label, how):
    hand = _hand_plist(label)
    if how == "plist":
        ours_plist(_plist())                     # as install wrote it: its MARKER
    else:
        sp["loaded"].add(OURS)                   # loaded by install: its environment carries MARKER
        sp["env"][OURS] = dict([services.MARKER])
    assert services.install(["speakers"]) == 0
    out = capsys.readouterr().out
    assert "speakers SKIPPED" in out and "speakers ours REMOVED" in out
    assert not _plist().exists() and hand.read_text() == "HAND"          # exactly one: theirs
    booted = [a for a in sp["calls"]["launchctl"] if a[0] == "bootout"]
    assert booted == ([("bootout", f"gui/{UID}/{OURS}")] if how == "loaded" else [])
    assert not _touched(sp, label)
    assert not [a for a in sp["calls"]["launchctl"] if a[0] == "bootstrap"]


def test_dry_run_only_says_ours_would_go(sp, capsys):
    _hand_plist("com.failmode.speaker-sidecar")
    body = ours_plist(_plist()).read_bytes()
    assert services.install(["speakers"], dry_run=True) == 0
    assert "speakers ours would be REMOVED" in capsys.readouterr().out
    assert _plist().read_bytes() == body
    assert not [a for a in sp["calls"]["launchctl"] if a[0] not in ("print", "list")]


def test_the_first_hand_label_found_is_named(sp):
    sp["loaded"].add("com.failmode.speaker-sidecar")
    assert services._hand_made(UID, services.HAND_SPEAKERS) == (
        "com.failmode.speaker-sidecar", services.AGENTS / "com.failmode.speaker-sidecar.plist")
    assert services._hand_made(UID, (services.HAND_MAIL,)) is None


# ---- SC5: the status row -------------------------------------------------------------------

def _row(capsys):
    out = capsys.readouterr().out.splitlines()
    rows = [l for l in out if l.startswith("speakers ")]
    assert len(rows) == 1, out
    return rows[0][len("speakers "):], out


def _state(sp, state):
    """Put the host in STATE (our job's half of it); refs only where the state says so."""
    if state == "not installed":
        return
    _plist().parent.mkdir(parents=True, exist_ok=True)
    _plist().write_bytes(b"plist")
    if state == "installed, not enrolled":
        return
    if state == "not enrolled, loaded":
        sp["listed"][OURS] = "777"                                       # running, but no refs
        return
    _enrol(sp, "theo")
    sp["listed"][OURS] = {"running, pid 777": "777", "loaded, not running": "-"}[state]


STATES = {
    "not installed": "not installed",
    "installed, not enrolled": "installed, not enrolled",
    "not enrolled, loaded": "installed, not enrolled",                   # it wins, loaded or not
    "running, pid 777": "running, pid 777",
    "loaded, not running": "loaded, not running",
}


@pytest.mark.parametrize("state", STATES)
def test_status_gives_exactly_one_state(sp, capsys, state):
    _state(sp, state)
    services.status()
    row, _ = _row(capsys)
    assert row.startswith(STATES[state])
    others = set(STATES.values()) - {STATES[state]}
    assert not any(row.startswith(o) for o in others)


@pytest.mark.parametrize("state", STATES)
def test_install_dry_run_prints_the_same_row_first(sp, capsys, state):
    _state(sp, state)
    services.status()
    row, _ = _row(capsys)
    services.install(["speakers"], dry_run=True)
    assert capsys.readouterr().out.splitlines()[0] == "speakers " + row


def test_not_enrolled_says_how(sp, capsys):
    _state(sp, "installed, not enrolled")
    services.status()
    row, _ = _row(capsys)
    assert str(sp["speakers"] / "refs") in row and "ears speakers enrol" in row


@pytest.mark.parametrize("label", services.HAND_SPEAKERS)
@pytest.mark.parametrize("how,said,way", [
    ("running", "running, pid 4242", "launchctl bootout "),
    ("loaded", "loaded, not running", "launchctl bootout "),
    # a plist that is not loaded is DORMANT (launchd loads it at the next login): moved aside,
    # nothing to boot out (super 20:24)
    ("plist", f"plist present, NOT loaded ({services.DORMANT})", "move ")])
def test_status_names_a_hand_made_sidecar_beside_the_row(sp, capsys, label, how, said, way):
    if how == "plist":
        _hand_plist(label)
    else:
        sp["listed"][label] = "4242" if how == "running" else "-"
    services.status()
    row, out = _row(capsys)
    assert row == "not installed"
    i = out.index("speakers not installed")
    assert out[i + 1].startswith(f"       {label} (hand-made): {said}; install skips speakers while it is here "
                                 f"(ours is NOT loaded). To run ours instead: {way}")
    assert out[i + 1].endswith("then `ears services install --only speakers`")
    if how == "plist":
        assert "bootout" not in out[i + 1] and "(it is not loaded: nothing to boot out)" in out[i + 1]


def test_status_with_no_hand_sidecar_prints_no_hand_line(sp, capsys):
    services.status()
    assert "hand-made" not in capsys.readouterr().out


# ---- Q9: a stray side-car is warned about, never killed ------------------------------------

def test_a_stray_sidecar_is_a_warning_only(sp, capsys):
    sp["procs"].extend([4242, 5151])
    assert services.install(["speakers"]) == 0                           # never a refusal
    out = capsys.readouterr().out
    warn = [l for l in out.splitlines() if "speaker-sidecar2.py" in l]
    assert len(warn) == 1 and warn[0].startswith("WARNING") and "4242, 5151" in warn[0]
    assert _plist().exists()                                             # installed all the same


def test_our_own_running_job_is_not_a_stray(sp, capsys):
    _state(sp, "running, pid 777")
    sp["procs"].append(777)
    assert services.install(["speakers"]) == 0
    assert "speaker-sidecar2.py" not in capsys.readouterr().out


def test_pgrep_is_read_only(monkeypatch):
    ran = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: ran.append(argv) or
                        subprocess.CompletedProcess(argv, 0, "12\n34\n", ""))
    assert services._pgrep("speaker-sidecar2.py") == [12, 34]
    assert ran == [["pgrep", "-f", "speaker-sidecar2.py"]]


def test_no_pgrep_is_no_stray(monkeypatch):
    def boom(*a, **kw):
        raise FileNotFoundError("pgrep")
    monkeypatch.setattr(subprocess, "run", boom)
    assert services._pgrep("speaker-sidecar2.py") == []
