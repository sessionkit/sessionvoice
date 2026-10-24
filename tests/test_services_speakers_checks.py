"""EARS-258 do-004: what `ears services install --only speakers` checks before any plist (design
s2, s5; super ruling 2030-06-09: a separate service venv under HOME, mlx-audio==0.5.8 from
the compiled lock the package ships, no change to the ears' own dependencies).

- the host: arm64 on macOS 14+, else one REFUSED line (mlx has no wheel elsewhere);
- the python: the venv <speakers home>/venv, built by uv from requirements.lock; no uv to build
  it with, refused; EARS_SPEAKER_PY skips the build, never the check;
- the modules, each imported on its own: the first one missing is named, exit 2, no plist (SC6);
- the model: cached, or fetched at install and said so ("about 190 MB, once"); never by the job;
- --dry-run says "would build" / "would download" and changes nothing.

No network and no real venv: uv and the download go through services._speakers_sh, faked. The
module check is real where it matters (SC6): a python whose PYTHONPATH puts first a 0.5.5
stand-in mlx_audio (vad/models/ but no nemotron_diarization) and a stub sounddevice."""
import os
import subprocess
import sys

import pytest

from ears import services
from test_services_install_checks import fence  # noqa: F401  (a fixture)
from test_services_speakers import OURS, UID, _hand_plist, _plist, sp  # noqa: F401  (sp: a fixture)

REAL_MISSING = services.speakers_missing_module      # before sp fakes it
NEMO = "mlx_audio.vad.models.nemotron_diarization"


def _err_lines(capsys):
    out = capsys.readouterr()
    return out.out, [l for l in out.err.splitlines() if l.strip()]


def _nothing_written(sp):
    assert not _plist().exists()
    assert not [a for a in sp["calls"]["launchctl"] if a[0] in ("bootstrap", "bootout", "kickstart")]


def _stand_in(root, nemotron: bool, sounddevice: bool):
    """A site dir to put first on PYTHONPATH: mlx_audio as 0.5.5 shipped it (vad/models/, no
    nemotron_diarization) or as 0.5.6+ (with it), and sounddevice present or ABSENT (a stub that
    raises ImportError, so a dev env that has the real one still sees it missing)."""
    models = root / "mlx_audio/vad/models"
    models.mkdir(parents=True)
    for d in (root / "mlx_audio", root / "mlx_audio/vad", models):
        (d / "__init__.py").write_text("")
    if nemotron:
        (models / "nemotron_diarization").mkdir()
        (models / "nemotron_diarization/__init__.py").write_text("")
    (root / "sounddevice.py").write_text("" if sounddevice else
                                        "raise ImportError('stand-in: no sounddevice here')\n")
    return root


@pytest.fixture
def real_check(sp, monkeypatch, tmp_path):
    """The REAL module check, on this test's own python (EARS_SPEAKER_PY), with a stand-in
    site dir first on its PYTHONPATH: returns a function that builds that site dir."""
    monkeypatch.setattr(services, "speakers_missing_module", REAL_MISSING)
    monkeypatch.setenv("EARS_SPEAKER_PY", sys.executable)

    def site(nemotron=True, sounddevice=True):
        root = _stand_in(tmp_path / "site", nemotron, sounddevice)
        monkeypatch.setenv("PYTHONPATH", str(root))
        return root
    return site


# ---- SC6: the module check ------------------------------------------------------------------

def test_a_0_5_5_mlx_audio_is_refused_in_one_line_naming_the_module(sp, real_check, capsys):
    real_check(nemotron=False, sounddevice=True)
    assert services.install(["speakers"]) == 2
    out, err = _err_lines(capsys)
    assert len(err) == 1, err
    assert "REFUSED" in err[0] and NEMO in err[0] and "mlx-audio>=0.5.6" in err[0] and "0.5.8" in err[0]
    _nothing_written(sp)
    assert not sp["sh"]                                   # EARS_SPEAKER_PY: nothing built, nothing fetched


def test_the_module_present_and_no_sounddevice_names_sounddevice(sp, real_check, capsys):
    real_check(nemotron=True, sounddevice=False)
    assert services.install(["speakers"]) == 2
    _, err = _err_lines(capsys)
    assert len(err) == 1 and "REFUSED" in err[0] and "has no sounddevice" in err[0]
    assert NEMO not in err[0]
    _nothing_written(sp)


def test_every_module_present_passes_and_the_plist_is_written(sp, real_check, capsys):
    real_check(nemotron=True, sounddevice=True)
    assert services.install(["speakers"]) == 0
    out, err = _err_lines(capsys)
    assert not err and f"python {sys.executable} (EARS_SPEAKER_PY: no venv built)" in out
    assert _plist().exists()


def test_the_check_names_the_first_missing_and_imports_each_on_its_own(monkeypatch):
    seen = []

    def run(argv, **kw):
        seen.append(argv[2])
        return subprocess.CompletedProcess(argv, 0 if argv[2] != "import numpy" else 1, "", "")
    monkeypatch.setattr(subprocess, "run", run)
    assert REAL_MISSING("/x/python") == "numpy"
    assert seen == [f"import {NEMO}", "import numpy"]     # one per python, stopped at the first missing


def test_a_python_that_does_not_run_is_its_first_module_missing(tmp_path):
    assert REAL_MISSING(tmp_path / "no-such-python") == NEMO


def test_ears_speaker_py_skips_the_build_but_never_the_check(sp, monkeypatch, tmp_path, capsys):
    py = tmp_path / "py"; py.write_text("")
    monkeypatch.setenv("EARS_SPEAKER_PY", str(py))
    checked = []
    monkeypatch.setattr(services, "speakers_missing_module", lambda p: checked.append(p) or NEMO)
    assert services.install(["speakers"]) == 2
    assert checked == [py] and not sp["sh"]
    _nothing_written(sp)


def test_ears_speaker_py_that_does_not_exist_is_refused(sp, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("EARS_SPEAKER_PY", str(tmp_path / "nope"))
    assert services.install(["speakers"]) == 2
    _, err = _err_lines(capsys)
    assert len(err) == 1 and "EARS_SPEAKER_PY=" in err[0] and "no such python" in err[0]
    _nothing_written(sp)


def test_ears_speaker_py_needs_no_uv(sp, monkeypatch, tmp_path):
    py = tmp_path / "py"; py.write_text("")
    monkeypatch.setenv("EARS_SPEAKER_PY", str(py))
    monkeypatch.setattr(services, "_uv", lambda: None)
    assert services.install(["speakers"]) == 0 and _plist().exists()


# ---- the host: arm64, macOS 14+ --------------------------------------------------------------

@pytest.mark.parametrize("machine, ver", [("x86_64", "14.5"), ("arm64", "13.6.1"), ("arm64", ""),
                                          ("aarch64", ""), ("arm64", "weird")])
def test_a_host_mlx_cannot_run_on_is_refused_before_anything(sp, monkeypatch, capsys, machine, ver):
    monkeypatch.setattr(services, "_mac", lambda: (machine, ver))
    monkeypatch.setattr(services, "speakers_missing_module", lambda p: pytest.fail("checked a module"))
    assert services.install(["speakers"]) == 2
    _, err = _err_lines(capsys)
    assert len(err) == 1 and "REFUSED" in err[0] and "arm64" in err[0] and "macOS 14" in err[0]
    _nothing_written(sp)
    assert not sp["sh"] and not sp["speakers"].exists()


@pytest.mark.parametrize("ver", ["14", "14.0", "14.7.2", "15.1", "26.0", "27"])
def test_macos_14_and_later_on_arm64_pass(monkeypatch, ver):
    monkeypatch.setattr(services, "_mac", lambda: ("arm64", ver))
    assert services._speakers_platform() is None


def test_the_host_seam_reads_this_host():
    machine, ver = services._mac()
    assert isinstance(machine, str) and isinstance(ver, str)


def test_a_refusal_writes_no_other_service_either(sp, monkeypatch, capsys):
    monkeypatch.setattr(services, "_mac", lambda: ("x86_64", "14.5"))
    assert services.install(["mouth", "speakers"]) == 2
    assert not (services.AGENTS.exists() and list(services.AGENTS.glob("*.plist")))
    assert not [a for a in sp["calls"]["launchctl"] if a[0] == "bootstrap"]


# ---- the venv: built by uv from the shipped lock ------------------------------------------------

def test_no_venv_is_built_with_uv_from_the_shipped_lock(sp, capsys):
    assert services.install(["speakers"]) == 0
    venv = sp["speakers"] / "venv"
    lock = services._speakers_file("requirements.lock")
    assert sp["sh"] == [["/fake/uv", "venv", "--python", "3.12", venv],
                        ["/fake/uv", "pip", "install", "--python", venv / "bin/python",
                         "--require-hashes", "-r", lock]]
    out = capsys.readouterr().out
    assert f"building {venv} (python 3.12, mlx-audio==0.5.8; network)" in out
    assert "35 packages pinned (mlx-audio==0.5.8)" in out     # hash lines are not counted as pins
    assert (sp["speakers"].stat().st_mode & 0o777) == 0o700        # the venv's home is private
    assert _plist().exists()


def test_a_present_venv_is_not_rebuilt_and_gets_the_pins_re_applied(sp, capsys):
    py = sp["speakers"] / "venv/bin/python"
    py.parent.mkdir(parents=True); py.write_text("")
    assert services.install(["speakers"]) == 0
    assert [a[1] for a in sp["sh"]] == ["pip"]                     # no `uv venv`: an upgrade keeps it
    assert sp["sh"][0][-2:] == ["-r", services._speakers_file("requirements.lock")]


def test_a_venv_dir_with_no_python_is_rebuilt_over_with_clear(sp, capsys):
    """Its base python removed (a dangling bin/python) or a build cut short: uv 0.11 will not
    build over an existing venv without --clear, so install would be a dead end (review do-004)."""
    venv = sp["speakers"] / "venv"
    (venv / "bin").mkdir(parents=True); (venv / "pyvenv.cfg").write_text("")
    (venv / "bin/python").symlink_to(venv / "gone/python3.12")     # dangling: exists() is False
    assert services.install(["speakers"]) == 0
    assert sp["sh"][0] == ["/fake/uv", "venv", "--python", "3.12", "--clear", venv]
    assert sp["sh"][1][:2] == ["/fake/uv", "pip"]


def test_no_uv_and_no_venv_is_refused_naming_uv(sp, monkeypatch, capsys):
    monkeypatch.setattr(services, "_uv", lambda: None)
    assert services.install(["speakers"]) == 2
    _, err = _err_lines(capsys)
    assert len(err) == 1 and "REFUSED" in err[0] and "no uv on PATH" in err[0] and "EARS_SPEAKER_PY" in err[0]
    _nothing_written(sp)
    assert not sp["speakers"].exists()


def test_no_uv_beside_a_present_venv_uses_it_as_it_is(sp, monkeypatch, capsys):
    py = sp["speakers"] / "venv/bin/python"
    py.parent.mkdir(parents=True); py.write_text("")
    monkeypatch.setattr(services, "_uv", lambda: None)
    assert services.install(["speakers"]) == 0
    assert "pins not re-applied" in capsys.readouterr().out and not sp["sh"]


@pytest.mark.parametrize("step", ["venv", "pip"])
def test_a_failed_build_is_refused_with_uvs_last_line(sp, monkeypatch, capsys, step):
    def sh(argv):
        sp["sh"].append(argv)
        bad = argv[1] == step
        return subprocess.CompletedProcess(argv, 1 if bad else 0, "", "resolving\nerror: no network" if bad else "")
    monkeypatch.setattr(services, "_speakers_sh", sh)
    assert services.install(["speakers"]) == 2
    _, err = _err_lines(capsys)
    assert len(err) == 1 and "REFUSED" in err[0] and err[0].endswith("error: no network")
    _nothing_written(sp)


def test_no_shipped_lock_is_refused(sp, monkeypatch, capsys):
    real = services._speakers_file

    def find(name):
        if name == "requirements.lock":
            raise services.NoSpeakers("no requirements.lock")
        return real(name)
    monkeypatch.setattr(services, "_speakers_file", find)
    assert services.install(["speakers"]) == 2
    assert "no requirements.lock" in capsys.readouterr().err
    _nothing_written(sp)


def test_the_network_steps_never_run_offline(monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    r = services._speakers_sh([sys.executable, "-c", "import os; print(os.environ.get('HF_HUB_OFFLINE'))"])
    assert r.returncode == 0 and r.stdout.strip() == "None"


# ---- the model: fetched at install, said so; the job never fetches -----------------------------

def test_a_cached_model_is_not_fetched(sp, capsys):
    assert services.install(["speakers"]) == 0
    assert "model mlx-community/Nemotron-3-Diarization cached" in capsys.readouterr().out
    assert not [a for a in sp["sh"] if "snapshot_download" in " ".join(map(str, a))]


def test_an_uncached_model_is_downloaded_at_install_and_said_so(sp, monkeypatch, capsys):
    monkeypatch.setattr(services, "speakers_model_cached", lambda py: False)
    assert services.install(["speakers"]) == 0
    out = capsys.readouterr().out
    assert "downloading mlx-community/Nemotron-3-Diarization (about 190 MB, once)" in out
    py = sp["speakers"] / "venv/bin/python"
    fetch = [a for a in sp["sh"] if "snapshot_download" in " ".join(map(str, a))]
    assert len(fetch) == 1 and fetch[0][0] == py and "local_files_only" not in fetch[0][2]
    assert f"revision={services.SPEAKER_REVISION!r}" in fetch[0][2]   # the pinned snapshot, not main
    assert services.spec("speakers")["EnvironmentVariables"]["HF_HUB_OFFLINE"] == "1"   # the job never does


def test_a_failed_download_is_refused(sp, monkeypatch, capsys):
    monkeypatch.setattr(services, "speakers_model_cached", lambda py: False)

    def sh(argv):
        ok = "snapshot_download" not in " ".join(map(str, argv))
        return subprocess.CompletedProcess(argv, 0 if ok else 1, "", "" if ok else "OSError: offline")
    monkeypatch.setattr(services, "_speakers_sh", sh)
    assert services.install(["speakers"]) == 2
    _, err = _err_lines(capsys)
    assert len(err) == 1 and "download" in err[0] and "OSError: offline" in err[0]
    _nothing_written(sp)


# ---- --dry-run: says what it would do, does nothing --------------------------------------------

def test_dry_run_with_no_venv_says_would_build_and_would_download(sp, capsys):
    assert services.install(["speakers"], dry_run=True) == 0
    out = capsys.readouterr().out
    venv = sp["speakers"] / "venv"
    assert f"would build {venv} (python 3.12, mlx-audio==0.5.8, 35 packages pinned in" in out
    assert "would download mlx-community/Nemotron-3-Diarization (about 190 MB, once)" in out
    assert not sp["sh"] and not sp["speakers"].exists()
    _nothing_written(sp)


def test_dry_run_with_a_venv_checks_it_and_says_would_download(sp, monkeypatch, capsys):
    py = sp["speakers"] / "venv/bin/python"
    py.parent.mkdir(parents=True); py.write_text("")
    checked = []
    monkeypatch.setattr(services, "speakers_missing_module", lambda p: checked.append(p))
    monkeypatch.setattr(services, "speakers_model_cached", lambda p: False)
    assert services.install(["speakers"], dry_run=True) == 0
    out = capsys.readouterr().out
    assert f"venv present: {py}" in out and "would download" in out
    assert checked == [py] and not sp["sh"]
    _nothing_written(sp)


def test_dry_run_refused_exits_2_and_changes_nothing(sp, monkeypatch, capsys):
    monkeypatch.setattr(services, "_mac", lambda: ("arm64", "13.0"))
    assert services.install(["speakers"], dry_run=True) == 2
    assert "REFUSED" in capsys.readouterr().err
    assert not sp["sh"] and not sp["speakers"].exists()
    _nothing_written(sp)


# ---- order: never two sidecars comes first -------------------------------------------------------

def test_a_hand_made_sidecar_skips_the_checks_too(sp, monkeypatch, capsys):
    _hand_plist("com.failmode.ears-speakers")
    monkeypatch.setattr(services, "_mac", lambda: pytest.fail("checked a skipped service"))
    assert services.install(["speakers"]) == 0
    assert not sp["sh"] and not _plist().exists()


def test_a_bare_install_runs_no_speakers_check(sp, monkeypatch, capsys):
    from test_services_install_checks import _store
    _store(sp["home"])
    monkeypatch.setattr(services, "_mac", lambda: pytest.fail("checked speakers on a bare install"))
    assert services.main(["install", "--no-agent", "--dry-run"]) == 0   # EARS-421: else REFUSED, rc 2


# ---- the shipped lock (super ruling Q1) ---------------------------------------------------------

def test_the_lock_is_compiled_pinned_and_says_where_it_runs():
    text = services._speakers_file("requirements.lock").read_text()
    head = "\n".join(l for l in text.splitlines()[:10] if l.startswith("#"))
    assert "arm64" in head and "macOS 14" in head and "REFUSES" in head
    reqs = [l.removesuffix(" \\") for l in text.splitlines() if l[:1].isalnum()]   # its hashes follow
    assert "mlx-audio==0.5.8" in reqs and len(reqs) == 35
    assert all("==" in l for l in reqs)                      # compiled: every package pinned
    assert any(l.startswith("sounddevice==") for l in reqs) and any(l.startswith("numpy==") for l in reqs)
    assert "git+" not in text and "://" not in "\n".join(reqs)   # no git URL anywhere


def test_the_lock_s_source_pins_the_same_mlx_audio():
    src = (services.ROOT / "speakers/requirements.in").read_text().splitlines()
    assert "mlx-audio==0.5.8" in src and services.SPEAKER_PIN == "mlx-audio==0.5.8"
