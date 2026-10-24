"""EARS-291 do-002: the speech server in `ears services install` / `uninstall`.

- the dry run prints which of design.md's steps 1-4 it took, and what it would build and
  write, and changes nothing;
- never two servers, at the install level: an occupied :8890 or a loaded mlx_audio.server job
  means no plist written, no bootstrap and no venv built;
- step 4 writes and loads a plist labelled dev.sessionkit.voice.mlx-audio and nothing labelled
  com.voicemode.*; uninstall removes only ours;
- when no STT will answer, install says what to run;
- the seed of ``final`` is not probed while the server this install loads is still starting.

Everything runs inside test_services_install_checks' fence: a temp HOME, launchctl faked, the
speech probes faked (nothing asks this host's live :8890), and no venv built or fetched."""
import os
import plistlib
import subprocess
from importlib.resources import files
from pathlib import Path

import pytest

from ears import services, speech
from ears.home import SETTINGS
from ears.speech import Holder, Job, Probe
from test_services_install_checks import _store, fence  # noqa: F401  (a fixture)
from test_speech import Stub, WELCOME                     # the stub HTTP server on 127.0.0.1:0
from test_services_label_move import ours_plist, print_out

REAL_PROBES = {n: getattr(speech, n) for n in ("probe", "port_holder", "connects", "identify")}

REPO = Path(__file__).resolve().parents[1]
UID = os.getuid()
OURS = "dev.sessionkit.voice.mlx-audio"
VM = Holder(pid=2000, command="/x/mlx-audio/bin/python /x/mlx_audio.server --host 127.0.0.1 --port 8890",
            label="com.voicemode.mlx-audio")
FOREIGN = Holder(pid=3000, command="/usr/bin/python3 -m http.server 8890")
# mlx-audio 0.5.8's GET / (mlx_audio/server.py:897-900 in mlx_audio-0.5.8-py3-none-any.whl,
# sha256 cf2eaef3d5d965d3a0a5c98ab68d4867db51daace190a77b3b9576577a7d2eab, read Wed
# 2030-06-09): the pin this install ships, byte for byte (decompose-001 review note 2)
WELCOME_058 = "Welcome to the MLX Audio API server! Visit https://localhost:3000 for the UI."


@pytest.fixture
def sx(fence, monkeypatch):
    """The fence, plus: `launchctl list` answers from ``listed`` ({label: pid or "-"}), and
    ``state["probe"]`` (a Probe) is what the speech probes saw (default: nothing on the port)."""
    listed, calls = {}, fence["calls"]
    state = {"probe": Probe()}

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

    def probe(agents, got, **kw):
        calls["speech"].append(("probe", str(agents), dict(got)))
        p = state["probe"]
        jobs = p.jobs or speech.loaded_jobs(agents, got)      # a plist a test wrote is scanned for real
        return Probe(host=p.host, port=kw.get("port", p.port), holder=p.holder, connects=p.connects,
                     identity=p.identity, why=p.why, jobs=jobs)

    monkeypatch.setattr(services, "_launchctl", launchctl)
    monkeypatch.setattr(speech, "probe", probe)
    fence.update(listed=listed, state=state, home_speech=fence["home"] / ".local/share/ears/speech")
    return fence


def _ours():
    return services.AGENTS / f"{OURS}.plist"


def _touched(fx, label):
    """launchctl calls that change LABEL's job (print and list only look)."""
    return [a for a in fx["calls"]["launchctl"] if a[0] not in ("print", "list") and label in " ".join(a)]


def _vm_plist(port=8890):
    p = services.AGENTS / "com.voicemode.mlx-audio.plist"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(plistlib.dumps({"Label": "com.voicemode.mlx-audio", "ProgramArguments": [
        "/bin/sh", "-c", f"exec ~/.local/bin/mlx_audio.server --host 127.0.0.1 --port {port}"]}))
    return p


def _nothing_changed(fx):
    assert not _ours().exists()
    assert not any(a[0] in ("bootstrap", "bootout", "kickstart") for a in fx["calls"]["launchctl"])
    assert not fx["home_speech"].exists()                    # no venv, no home
    assert not (fx["home"] / SETTINGS / "ears.json").exists()


# ---- the service: its label, its place in the lists ----------------------------------------

def test_speech_is_a_service_in_the_bare_install_and_only_leaves_it_out():
    assert "speech" in services.ALL and services.DEFAULT[0] == "speech"
    assert services._label("speech") == OURS == speech.LABEL
    assert services._label("mouth") == "dev.sessionkit.voice.ears-mouth"


def test_only_leaves_speech_out(monkeypatch):
    got = {}
    monkeypatch.setattr(services, "install", lambda names, dry=False, **kw: got.update(names=names) or 0)
    services.main(["install", "--only", "mouth,keeper"])
    assert got["names"] == ["mouth", "keeper"]
    services.main(["install", "--only", "speech"])
    assert got["names"] == ["speech"]


def test_the_plist_runs_mlx_audio_server_on_127_0_0_1_8890_from_our_venv(sx):
    d = services.spec("speech")
    py = os.environ["EARS_SPEECH_PY"]                        # the fence's given python
    assert d["Label"] == OURS
    assert d["ProgramArguments"][:7] == [py, "-m", "mlx_audio.server", "--host", "127.0.0.1", "--port", "8890"]
    assert d["ProgramArguments"][7:] == ["--log-dir", str(services.LOGS / "mlx-audio")]
    assert "EARS_PY" not in d["EnvironmentVariables"]
    assert d["EnvironmentVariables"]["PATH"].startswith(str(Path(py).parent) + ":")
    assert d["WorkingDirectory"] == str(services.speech_home()) and d["KeepAlive"] is True
    assert "com.voicemode" not in plistlib.dumps(d).decode()


def test_our_plist_is_one_the_never_two_scan_sees(sx, tmp_path):
    """Our own job, loaded and down, also blocks a second install (the scan finds it)."""
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / f"{OURS}.plist").write_bytes(plistlib.dumps(services.spec("speech")))
    jobs = speech.loaded_jobs(agents, {OURS: "-"})
    assert [j.label for j in jobs] == [OURS] and jobs[0].loaded


def test_the_venv_python_is_ours_unless_given(fence, monkeypatch):
    monkeypatch.delenv("EARS_SPEECH_PY")
    assert services.speech_py() == fence["home"] / ".local/share/ears/speech/venv/bin/python"
    monkeypatch.setenv("EARS_SPEECH_HOME", str(fence["tmp"] / "sp"))
    assert services.speech_py() == fence["tmp"] / "sp/venv/bin/python"
    monkeypatch.setenv("EARS_SPEECH_PY", "/opt/py")
    assert services.speech_py() == Path("/opt/py")


# ---- the dry run: which step, what it would build and write, nothing changed ---------------

@pytest.mark.parametrize("probe,url,step", [
    (Probe(), "http://127.0.0.1:2023/v1", "step 1, opt-out"),
    (Probe(holder=VM, connects=True, identity="mlx-audio"), None, "step 2, use"),
    (Probe(jobs=(Job("com.voicemode.mlx-audio", Path("/x.plist"), "-"),)), None, "step 3, refuse-loaded-down"),
])
def test_dry_run_names_the_step_and_changes_nothing(sx, capsys, monkeypatch, probe, url, step):
    if url:
        monkeypatch.setenv("EARS_STT_URL", url)
    sx["state"]["probe"] = probe
    assert services.install(["speech"], dry_run=True) == 0
    out = capsys.readouterr().out
    assert step in out and "nothing built or written" in out
    assert OURS + ".plist" not in out                       # no plist printed: none would be written
    _nothing_changed(sx)


def test_dry_run_step_2_a_foreign_holder_is_refused_and_says_what_to_run(sx, capsys):
    sx["state"]["probe"] = Probe(holder=FOREIGN, connects=True, identity="foreign", why="GET / answered 404")
    assert services.install(["speech"], dry_run=True) == 2
    err = capsys.readouterr().err
    assert "REFUSED: speech step 2, refuse-foreign" in err and "pid 3000" in err
    assert "what to run" in err
    _nothing_changed(sx)


def test_dry_run_step_4_says_what_it_would_build_and_write(sx, capsys, monkeypatch):
    monkeypatch.delenv("EARS_SPEECH_PY")
    monkeypatch.setattr(services, "_uv", lambda: "/fake/uv")
    assert services.install(["speech"], dry_run=True) == 0
    out = capsys.readouterr().out
    venv = sx["home_speech"] / "venv"
    lock = services._speech_file("requirements.lock")
    assert "speech step 4, install: nothing on :8890 and no mlx-audio job loaded; install " + OURS in out
    assert f"would build {venv} (python 3.12, mlx-audio==0.5.8" in out and str(lock) in out
    assert f"--- {_ours()}" in out and f"<string>{OURS}</string>" in out     # the plist it would write
    assert "com.voicemode" not in out.split(f"--- {_ours()}")[1]
    _nothing_changed(sx)


def test_dry_run_of_a_bare_install_runs_the_speech_step_first(sx, capsys):
    _store(sx["home"])
    assert services.main(["install", "--no-agent", "--dry-run"]) == 0   # EARS-421: else REFUSED, rc 2
    out = capsys.readouterr().out
    assert out.index("speech step 4") < out.index("final would be set")
    assert "(not probed: it is the speech server this install loads)" in out
    assert sx["calls"]["probe"] == []                        # no POST to a server not yet loaded
    _nothing_changed(sx)


# ---- never two: occupied or loaded => no plist, no bootstrap, no venv ----------------------

OCCUPIED = {
    "mlx-audio (VoiceMode's)": Probe(holder=VM, connects=True, identity="mlx-audio"),
    "mlx-audio, lsof blind": Probe(holder=None, connects=True, identity="mlx-audio"),
    "foreign": Probe(holder=FOREIGN, connects=True, identity="foreign"),
    "wedged": Probe(holder=VM, connects=True, identity="no-answer"),
    "a listener the connect misses": Probe(holder=VM, connects=False, identity="no-answer"),
    "a loaded job, port closed": Probe(jobs=(Job("com.voicemode.mlx-audio", Path("/x.plist"), "-"),)),
    "a loaded system daemon": Probe(jobs=(Job("org.site.mlx", Path("/Library/LaunchDaemons/x.plist"), "-"),)),
}


@pytest.mark.parametrize("name", OCCUPIED)
@pytest.mark.parametrize("names", [["speech"], ["speech", "mouth", "keeper", "mail"]])
def test_never_two_an_occupied_port_or_loaded_job_installs_nothing(sx, capsys, monkeypatch, name, names):
    _store(sx["home"])
    monkeypatch.delenv("EARS_SPEECH_PY")                    # a build would run _speech_sh: the fence fails it
    monkeypatch.setattr(services, "_uv", lambda: "/fake/uv")
    sx["state"]["probe"] = OCCUPIED[name]
    services.install(names)
    capsys.readouterr()
    assert not _ours().exists()
    assert not _touched(sx, OURS)                            # no bootout, no bootstrap of ours
    assert not sx["home_speech"].exists()                    # no venv built
    assert not (services.AGENTS / "dev.sessionkit.voice.ears-speech.plist").exists()


def test_never_two_a_real_voicemode_plist_loaded_and_down_blocks_install(sx, capsys, monkeypatch):
    """Through the real plist scan: VoiceMode's job between restarts (KeepAlive, port closed)."""
    vm = _vm_plist()
    before = vm.read_bytes()
    sx["listed"]["com.voicemode.mlx-audio"] = "-"
    monkeypatch.setattr(services, "_speech_sh", lambda argv: pytest.fail(f"built {argv}"))
    assert services.install(["speech"]) == 0
    out = capsys.readouterr().out
    assert "step 3, refuse-loaded-down: com.voicemode.mlx-audio is loaded" in out
    assert not _ours().exists() and not _touched(sx, OURS)
    assert vm.read_bytes() == before and not _touched(sx, "com.voicemode.mlx-audio")   # theirs untouched


def test_a_voicemode_plist_that_is_not_loaded_does_not_block(sx, capsys):
    _vm_plist()                                              # on disk, launchd has not loaded it
    assert services.install(["speech"]) == 0
    assert _ours().is_file()


# ---- step 4: ours, written and loaded ------------------------------------------------------

def test_step_4_writes_and_loads_ours_and_nothing_voicemode(sx, capsys):
    assert services.install(["speech"]) == 0
    out = capsys.readouterr().out
    d = plistlib.loads(_ours().read_bytes())
    assert d["Label"] == OURS and "com.voicemode" not in _ours().read_text()
    assert ("bootstrap", f"gui/{UID}", str(_ours())) in sx["calls"]["launchctl"]
    assert ("bootout", f"gui/{UID}/{OURS}") in sx["calls"]["launchctl"]            # a re-install replaces
    assert f"speech loaded  {_ours()}" in out
    assert sorted(p.name for p in services.AGENTS.iterdir()) == [f"{OURS}.plist"]
    assert not any("com.voicemode" in " ".join(a) for a in _touched(sx, "com."))
    assert sx["home_speech"].is_dir()                        # its WorkingDirectory exists
    assert ("import", os.environ["EARS_SPEECH_PY"]) in sx["calls"]["speech"]       # modules checked


def test_step_4_builds_our_venv_with_uv_from_the_shipped_lock(sx, capsys, monkeypatch):
    monkeypatch.delenv("EARS_SPEECH_PY")
    sh = []
    py = sx["home_speech"] / "venv/bin/python"

    def run(argv):
        sh.append([str(a) for a in argv])
        if argv[1] == "venv":
            py.parent.mkdir(parents=True)
            py.write_text("")
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(services, "_uv", lambda: "/fake/uv")
    monkeypatch.setattr(services, "_speech_sh", run)
    assert services.install(["speech"]) == 0
    lock = str(services._speech_file("requirements.lock"))
    build = str(services._speech_file("build-constraints.txt"))   # EARS-339: the sdists' backend
    assert sh == [["/fake/uv", "venv", "--python", "3.12", str(py.parent.parent)],
                  ["/fake/uv", "pip", "install", "--python", str(py), "--require-hashes",
                   "-b", build, "-r", lock]]
    assert not any("tool" in a for a in sh)                  # never `uv tool install mlx-audio`
    assert plistlib.loads(_ours().read_bytes())["ProgramArguments"][0] == str(py)
    assert "111 packages pinned (mlx-audio==0.5.8)" in capsys.readouterr().out   # 110 pins + the URL


def test_a_failed_build_is_refused_before_any_plist(sx, capsys, monkeypatch):
    monkeypatch.delenv("EARS_SPEECH_PY")
    monkeypatch.setattr(services, "_uv", lambda: "/fake/uv")
    monkeypatch.setattr(services, "_speech_sh", lambda argv: subprocess.CompletedProcess(argv, 1, "", "no network"))
    assert services.install(["speech", "mouth", "keeper"]) == 2
    err = capsys.readouterr().err
    assert "REFUSED: speech: uv venv" in err and "no network" in err and "what to run" in err
    assert not services.AGENTS.exists() or not list(services.AGENTS.iterdir())


def test_no_uv_and_no_venv_is_refused(sx, capsys, monkeypatch):
    monkeypatch.delenv("EARS_SPEECH_PY")
    monkeypatch.setattr(services, "_uv", lambda: None)
    assert services.install(["speech"]) == 2
    assert "no uv on PATH" in capsys.readouterr().err and not _ours().exists()


def test_a_missing_module_is_named(sx, capsys, monkeypatch):
    monkeypatch.setattr(services, "speech_missing_module", lambda py: "misaki.en")
    assert services.install(["speech"]) == 2
    err = capsys.readouterr().err
    assert "has no misaki.en" in err and "requirements.lock" in err and not _ours().exists()


def test_the_seed_is_not_probed_while_our_server_starts(sx, capsys):
    """Build plan "Ordering" / decompose review note 3: the new final names the server this
    install loads, which cannot answer yet, so seed_stt is told no_probe."""
    _store(sx["home"])
    assert services.install(["speech", "mouth", "keeper", "mail"]) == 0
    out = capsys.readouterr().out
    assert "final set: {'url': 'http://127.0.0.1:8890/v1'" in out
    assert "(not probed: it is the speech server this install loads)" in out
    assert sx["calls"]["probe"] == []


def test_the_seed_is_probed_when_the_server_is_someone_elses(sx, capsys):
    _store(sx["home"])
    sx["state"]["probe"] = Probe(holder=VM, connects=True, identity="mlx-audio")
    assert services.install(["speech", "mouth", "keeper", "mail"]) == 0
    assert "step 2, use: using mlx-audio on :8890 (pid 2000, launchd com.voicemode.mlx-audio)" in capsys.readouterr().out
    assert sx["calls"]["probe"] == [("http://127.0.0.1:8890/v1", "mlx-community/parakeet-tdt-0.6b-v3")]
    assert not _ours().exists()


# ---- no STT: say what to run ---------------------------------------------------------------

def test_a_foreign_holder_refuses_the_whole_install_and_says_what_to_run(sx, capsys):
    sx["state"]["probe"] = Probe(holder=FOREIGN, connects=True, identity="foreign", why="GET / answered 404")
    assert services.install(["speech", "mouth", "keeper", "mail"]) == 2
    err = capsys.readouterr().err
    assert "REFUSED" in err and "what to run" in err
    assert "ears services install --only speech" in err and "EARS_STT_URL=https://HOST/v1" in err
    assert "ears install --apple" in err and "stt.key" in err
    assert not services.AGENTS.exists() or not list(services.AGENTS.iterdir())    # no plist at all


def test_not_apple_silicon_refuses_with_what_to_run(sx, capsys, monkeypatch):
    monkeypatch.setattr(services, "_mac", lambda: ("x86_64", "13.6"))
    monkeypatch.setattr(services, "_speech_platform",
                        lambda: services._mlx_platform("the speech server", "mlx-audio's runtime"))
    assert services.install(["speech"]) == 2
    err = capsys.readouterr().err
    assert "REFUSED: speech: the speech server needs an Apple-silicon Mac (arm64)" in err
    assert "x86_64" in err and "what to run" in err and not _ours().exists()


def test_when_only_the_mouth_wants_the_port_a_refusal_is_a_warning(sx, capsys, monkeypatch):
    monkeypatch.setenv("EARS_STT_URL", "https://stt.example/v1")
    monkeypatch.setattr(services, "_mac", lambda: ("x86_64", "13.6"))
    monkeypatch.setattr(services, "_speech_platform",
                        lambda: services._mlx_platform("the speech server", "mlx-audio's runtime"))
    _store(sx["home"])
    assert services.install(["speech", "mouth", "keeper", "mail"]) == 0
    out = capsys.readouterr().out
    assert "WARNING: speech SKIPPED" in out and "falls back to `say`" in out
    assert not _ours().exists()
    assert (services.AGENTS / "dev.sessionkit.voice.ears-mouth.plist").is_file()        # the rest installs


def test_seed_says_what_to_run_when_neither_engine_answers(fence, monkeypatch):
    monkeypatch.setattr(services, "probe_stt", lambda url, model=None, timeout=30.0: "no answer: refused")
    said = services.seed_stt(no_probe=None)
    assert "did not answer either" in said and "what to run" in said and "ears services install --only speech" in said


# ---- uninstall: ours only ------------------------------------------------------------------

def test_uninstall_removes_only_ours(sx, capsys):
    vm = _vm_plist()
    services.AGENTS.mkdir(parents=True, exist_ok=True)
    ours_plist(_ours())                          # as install wrote it: its MARKER
    services.uninstall(["speech"])
    assert not _ours().exists() and vm.is_file()
    assert ("bootout", f"gui/{UID}/{OURS}") in sx["calls"]["launchctl"]
    assert not any("com.voicemode.mlx-audio" in " ".join(a) for a in sx["calls"]["launchctl"])
    assert "speech removed (dev.sessionkit.voice.mlx-audio; its venv stays in" in capsys.readouterr().out


def test_uninstall_all_includes_ours_and_never_a_voicemode_ears_speech(sx):
    services.uninstall(list(services.ALL))
    booted = [a[1] for a in sx["calls"]["launchctl"] if a[0] == "bootout"]
    assert f"gui/{UID}/{OURS}" in booted
    assert f"gui/{UID}/dev.sessionkit.voice.ears-speech" not in booted
    ours = [f"gui/{UID}/{l}" for l in (OURS, *speech.OLD_LABELS)]     # ours, new and old
    assert f"gui/{UID}/com.sessionvoice.mlx-audio" in booted
    assert not any("mlx-audio" in b and b not in ours for b in booted)


def test_status_has_one_speech_row_and_no_ears_speech_label(sx, capsys):
    services.status()
    out = capsys.readouterr().out
    rows = [l for l in out.splitlines() if l.startswith("speech ")]
    assert rows == ["speech none on :8890: no listener, no mlx-audio job loaded"]
    assert "dev.sessionkit.voice.ears-speech" not in out


# ---- what ships: the lock, the pin, the welcome line ---------------------------------------

def test_the_lock_is_compiled_pinned_and_says_where_it_runs():
    text = services._speech_file("requirements.lock").read_text()
    head = "\n".join(l for l in text.splitlines()[:10] if l.startswith("#"))
    assert "arm64" in head and "macOS 14" in head and "REFUSES" in head and OURS in head
    reqs = [l.removesuffix(" \\") for l in text.splitlines() if l[:1].isalnum()]   # its hashes follow
    assert "mlx-audio==0.5.8" in reqs and services.SPEECH_PIN == "mlx-audio==0.5.8" == services.SPEAKER_PIN
    for name in ("fastapi", "uvicorn", "python-multipart", "webrtcvad", "misaki", "spacy"):
        assert any(l.startswith(name + "==") for l in reqs), name
    urls = [l for l in reqs if "://" in l]
    assert urls == ["en-core-web-sm @ https://github.com/explosion/spacy-models/releases/download/"
                    "en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"]   # one release wheel
    assert all("==" in l for l in reqs if l not in urls)    # compiled: every other package pinned
    assert "git+" not in text and "tool install" not in text


def test_the_package_ships_the_lock_as_the_checkout_has_it():
    for name in ("requirements.lock", "build-constraints.txt"):
        assert (files("ears") / "_speech" / name).read_bytes() == (REPO / "speech" / name).read_bytes()
    assert sorted(p.name for p in (REPO / "src/ears/_speech").iterdir()) == \
        ["build-constraints.txt", "requirements.lock"]


def test_the_0_5_8_welcome_line_is_what_identify_matches():
    """decompose-001 review note 2: design.md measured 0.4.3 to 0.5.7; 0.5.8 is the pin."""
    assert WELCOME_058.startswith(speech.WELCOME)
    lock = services._speech_file("requirements.lock").read_text()
    assert "\nmlx-audio==0.5.8 \\\n" in lock                  # the version the line was read from


# ---- the port is a parameter: the verify demo runs install against a stub ------------------

def test_ears_speech_port_moves_the_probe_and_the_plist(sx, capsys, monkeypatch):
    monkeypatch.setenv("EARS_SPEECH_PORT", "51234")
    assert services.install(["speech"], dry_run=True) == 0
    out = capsys.readouterr().out
    assert "step 4, install: nothing on :51234" in out
    assert [c for c in sx["calls"]["speech"] if c[0] == "probe"]
    assert services.spec("speech")["ProgramArguments"][5:7] == ["--port", "51234"]


@pytest.fixture
def real(sx, monkeypatch):
    """The real probes (lsof, a connect, the two GETs), but only ever at a stub's port."""
    for n, f in REAL_PROBES.items():
        monkeypatch.setattr(speech, n, f)
    made = []

    def at(*a, **k):
        s = Stub(*a, **k)
        made.append(s)
        monkeypatch.setenv("EARS_SPEECH_PORT", str(s.port))
        return s
    yield at
    for s in made:
        s.close()
        assert all(m == "GET" and p in ("/", "/openapi.json") for m, p in s.seen), s.seen


def test_stub_mlx_audio_is_used_and_nothing_is_started(sx, real, capsys):
    s = real(WELCOME)
    assert services.install(["speech"], dry_run=True) == 0
    assert f"step 2, use: using mlx-audio on :{s.port}" in capsys.readouterr().out
    assert services.install(["speech"]) == 0
    capsys.readouterr()
    _nothing_changed(sx)


def test_stub_foreign_server_is_refused(sx, real, capsys):
    s = real({"detail": "Not Found"}, root_status=404)
    assert services.install(["speech"], dry_run=True) == 2
    err = capsys.readouterr().err
    assert f"step 2, refuse-foreign: :{s.port} is held by something that is not mlx-audio" in err
    _nothing_changed(sx)


def test_stub_closed_port_takes_step_4(sx, real, capsys, monkeypatch):
    s = real(WELCOME)
    s.close()                                                # bound once, now nothing listens
    assert services.install(["speech"], dry_run=True) == 0
    out = capsys.readouterr().out
    assert f"step 4, install: nothing on :{s.port}" in out and f"--- {_ours()}" in out
    _nothing_changed(sx)
