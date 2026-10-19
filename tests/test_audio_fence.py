"""tests/audio_fence.py: no test reaches a real audio device (EARS-320).

Nothing here can play: the "real" sounddevice is a stand-in written to tmp under a
``site-packages`` directory (that is what makes it real to the fence, as an installed
one is), and the stand-in leaves a mark if any of its code ever runs. Both venv shapes
are covered: the dev venv, where sounddevice is absent, and a capture venv, where it
is installed (the stand-in).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests import audio_fence
from tests.audio_fence import RealAudioDevice, expect_refusal
from ears import envnames

ROOT = Path(__file__).resolve().parents[1]
FAKE_SD = ROOT / "tests" / "fixtures" / "fake_sd"
ME = re.escape("tests/test_audio_fence.py::")


@pytest.fixture
def no_sd(monkeypatch):
    """Whatever an earlier test left in sys.modules, the next import looks again."""
    for name in ("sounddevice", "_sounddevice"):
        monkeypatch.delitem(sys.modules, name, raising=False)


@pytest.fixture
def installed(tmp_path) -> tuple[Path, Path]:
    """A capture venv's sounddevice, stood in: (its site-packages dir, the mark it leaves if run)."""
    site = tmp_path / "venv" / "lib" / "python3" / "site-packages"
    site.mkdir(parents=True)
    mark = tmp_path / "REAL-SOUNDDEVICE-RAN"
    (site / "sounddevice.py").write_text(f"open({str(mark)!r}, 'w').write('ran')\n"
                                         "def query_devices(*a, **k):\n    return [{'name': 'airpods'}]\n")
    return site, mark


def test_the_fence_is_on_for_every_test():
    assert audio_fence.on()
    assert type(sys.meta_path[0]).__name__ == "Fence"
    assert subprocess.Popen.__init__.audio_fence
    assert os.environ["PYTHONPATH"].split(os.pathsep)[0] == audio_fence.FENCE_DIR
    assert os.path.isfile(os.environ[audio_fence.LOG_ENV])
    assert not envnames.isset("MOUTH_DEVICE")   # the host's device choice (E2), either name


# -- in the pytest process ----------------------------------------------------------------------

def test_a_bare_import_is_refused_loudly_naming_the_test(no_sd):
    with expect_refusal() as got, pytest.raises(RealAudioDevice, match=ME + "test_a_bare_import"):
        import sounddevice  # noqa: F401
    (rec,) = got
    assert rec["module"] == "sounddevice" and rec["pid"] == os.getpid()
    assert rec["test"].startswith("tests/test_audio_fence.py::test_a_bare_import_is_refused")
    # this venv: absent (dev group), or installed (capture extra): refused either way
    assert rec["found"] == "not installed" or "site-packages" in rec["found"]


def test_an_installed_sounddevice_is_refused_before_any_of_it_runs(no_sd, installed, monkeypatch):
    site, mark = installed
    monkeypatch.syspath_prepend(str(site))
    with expect_refusal() as got, pytest.raises(RealAudioDevice, match="tried to import the real sounddevice"):
        import sounddevice  # noqa: F401
    assert got[0]["found"] == str(site / "sounddevice.py")
    assert not mark.exists(), "the installed module's code ran"
    assert "sounddevice" not in sys.modules


def test_the_refusal_is_not_an_importerror(no_sd):
    """``except ImportError`` and importorskip would read it as "not installed" and carry on."""
    assert not issubclass(RealAudioDevice, ImportError)
    with expect_refusal(), pytest.raises(RealAudioDevice):
        pytest.importorskip("sounddevice")


def test_a_fake_is_allowed(no_sd, tmp_path, monkeypatch):
    fake = tmp_path / "fake"
    fake.mkdir()
    (fake / "sounddevice.py").write_text("FAKE = True\n")
    monkeypatch.syspath_prepend(str(fake))
    with expect_refusal() as got:
        import sounddevice
    assert sounddevice.FAKE and got == []


def test_a_module_a_test_put_in_sys_modules_is_allowed(no_sd, monkeypatch):
    fake = type(sys)("sounddevice")
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    import sounddevice
    assert sounddevice is fake


@pytest.mark.parametrize("reach", [
    lambda: __import__("mouth.output", fromlist=["x"]).open_output("airpods", 24000),
    lambda: __import__("mouth.output", fromlist=["x"]).open_output("default", 24000),
    lambda: __import__("mouth.output", fromlist=["x"]).resolve("default"),
    lambda: __import__("mouth.output", fromlist=["x"]).output_names(),
    lambda: __import__("ears.listen.sources", fromlist=["x"]).resolve_input_device(),
    lambda: __import__("ears.listen.sources", fromlist=["x"]).MicSource()._open(),
], ids=["open_output-airpods", "open_output-default", "resolve-default", "output_names",
        "resolve_input_device", "MicSource"])
def test_every_unfaked_device_seam_is_refused(no_sd, reach):
    """The seams the audit found (A1, E4): no fake, so the fence stops them, and not as DeviceAbsent."""
    with expect_refusal() as got, pytest.raises(RealAudioDevice):
        reach()
    assert len(got) >= 1


def test_a_refusal_the_code_swallowed_is_still_recorded(no_sd):
    """heard.input_device_name() catches everything and returns None: the fence still saw it."""
    from ears import heard

    with expect_refusal() as got:
        assert heard.input_device_name() is None
    assert [r["module"] for r in got] == ["sounddevice"]


def test_a_test_that_swallowed_a_refusal_fails_at_teardown(tmp_path):
    """The fence as a plugin in a child pytest: the test catches the refusal, and fails anyway."""
    child = tmp_path / "child"
    child.mkdir()
    (child / "test_swallow.py").write_text(
        "def test_swallows():\n"
        "    try:\n"
        "        import sounddevice\n"
        "    except Exception:\n"
        "        pass\n"
        "def test_clean():\n"
        "    pass\n")
    env = {**os.environ, "HOME": str(tmp_path / "home"), "PYTHONPATH": str(ROOT)}
    out = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                          "-p", "tests.audio_fence", str(child)],
                         cwd=child, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 1, out.stdout + out.stderr
    assert "2 passed, 1 error" in out.stdout, out.stdout
    assert "ERROR at teardown of test_swallows" in out.stdout
    assert re.search(r"reached for a real audio device: \S*test_swallow\.py::test_swallows \(call\) "
                     r"imported the real sounddevice", out.stdout), out.stdout


def test_a_fake_does_not_outlive_its_test(tmp_path):
    """A fake one test imported is gone from sys.modules for the next: that test's unfaked
    import reaches the fence and is refused, not handed the stale fake (which would fail
    quietly, as a missing module once passed for absent)."""
    child = tmp_path / "child"
    child.mkdir()
    (child / "test_leak.py").write_text(
        "import sys\n"
        "from tests.audio_fence import RealAudioDevice, expect_refusal\n"
        "def test_a_imports_a_fake(tmp_path, monkeypatch):\n"
        "    fake = tmp_path / 'fake'\n"
        "    fake.mkdir()\n"
        "    (fake / 'sounddevice.py').write_text('FAKE = True\\n')\n"
        "    monkeypatch.syspath_prepend(str(fake))\n"
        "    monkeypatch.delitem(sys.modules, 'sounddevice', raising=False)   # absent: records nothing to undo\n"
        "    import sounddevice\n"
        "    assert sounddevice.FAKE\n"
        "def test_b_an_unfaked_import_after_it_is_refused():\n"
        "    assert 'sounddevice' not in sys.modules\n"
        "    with expect_refusal() as got:\n"
        "        try:\n"
        "            import sounddevice\n"
        "        except RealAudioDevice:\n"
        "            pass\n"
        "        else:\n"
        "            raise AssertionError(f'got the stale fake {sounddevice.__file__}')\n"
        "    assert [r['module'] for r in got] == ['sounddevice']\n")
    env = {**os.environ, "HOME": str(tmp_path / "home"), "PYTHONPATH": str(ROOT)}
    out = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                          "-p", "tests.audio_fence", str(child)],
                         cwd=child, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "2 passed" in out.stdout, out.stdout


def test_a_refusal_after_every_test_fails_the_session(tmp_path):
    """A detached child (a `mouth serve`) that reaches for a device after its test ended."""
    child = tmp_path / "child"
    child.mkdir()
    (child / "test_detached.py").write_text(
        "import subprocess, sys\n"
        "def test_detaches(tmp_path):\n"
        "    subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(0.5); import sounddevice'],\n"
        "                     start_new_session=True, stderr=subprocess.DEVNULL)\n")
    (child / "conftest.py").write_text(
        "import os, time, pytest\n"
        "@pytest.hookimpl(tryfirst=True)\n"
        "def pytest_sessionfinish(session):\n"
        "    end = time.time() + 30\n"
        "    while time.time() < end and not os.path.getsize(os.environ['AUDIO_FENCE_LOG']):\n"
        "        time.sleep(0.05)\n")
    env = {**os.environ, "HOME": str(tmp_path / "home"), "PYTHONPATH": str(ROOT)}
    out = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                          "-p", "tests.audio_fence", str(child)],
                         cwd=child, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 1, out.stdout + out.stderr
    assert "1 passed" in out.stdout
    assert re.search(r"^audio fence: reached for a real audio device outside a test: "
                     r"\S*test_detached\.py::test_detaches \(call\)", out.stdout, re.M), out.stdout


# -- in child processes, whatever env the test gives them ----------------------------------------

def _child(code: str, env):
    return subprocess.run([sys.executable, "-c", code], env=env, cwd=ROOT,
                          capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize("shape", ["inherited", "pythonpath-replaced", "env-replaced"])
def test_a_child_cannot_import_the_real_sounddevice(shape, tmp_path):
    env = {"inherited": None,
           "pythonpath-replaced": {**os.environ, "PYTHONPATH": str(tmp_path)},
           "env-replaced": {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}}[shape]
    with expect_refusal() as got:
        r = _child("import sounddevice", env)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "RealAudioDevice" in r.stderr and f"test_a_child_cannot_import_the_real_sounddevice[{shape}]" in r.stderr
    (rec,) = got
    assert rec["pid"] != os.getpid() and rec["module"] == "sounddevice"


@pytest.mark.parametrize("change", ["pythonpath-set", "pythonpath-removed", "log-removed"])
def test_a_child_is_fenced_when_the_test_changed_os_environ(change, monkeypatch, tmp_path):
    """No ``env=``: the child inherits ``os.environ``, which the test itself changed. Unfenced,
    the dev venv's child got a ModuleNotFoundError (absent, silently) and a capture venv's
    child the real PortAudio."""
    {"pythonpath-set": lambda: monkeypatch.setenv("PYTHONPATH", str(tmp_path)),
     "pythonpath-removed": lambda: monkeypatch.delenv("PYTHONPATH"),
     "log-removed": lambda: monkeypatch.delenv(audio_fence.LOG_ENV)}[change]()
    with expect_refusal() as got:
        r = _child("import sounddevice", None)
    assert r.returncode == 1 and "RealAudioDevice" in r.stderr, r.stderr
    assert f"test_a_child_is_fenced_when_the_test_changed_os_environ[{change}]" in r.stderr
    (rec,) = got                                 # in this session's log, even when the test dropped it
    assert rec["pid"] != os.getpid() and rec["module"] == "sounddevice"


def test_a_child_with_an_installed_sounddevice_is_refused_before_it_runs(installed):
    site, mark = installed
    with expect_refusal() as got:
        r = _child("import sounddevice", {**os.environ, "PYTHONPATH": str(site)})
    assert r.returncode == 1 and "RealAudioDevice" in r.stderr, r.stderr
    assert got[0]["found"] == str(site / "sounddevice.py")
    assert not mark.exists(), "the installed module's code ran in the child"


def test_a_child_with_the_fake_on_its_path_gets_the_fake(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(FAKE_SD), "FAKE_SD_LOG": str(tmp_path / "sd.log")}
    with expect_refusal() as got:
        r = _child("import sounddevice as sd; print([d['name'] for d in sd.query_devices()])", env)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "['Fake Speakers']" and got == []


def test_a_child_keeps_its_own_sitecustomize(tmp_path):
    """The fence's sitecustomize runs first, then the one the child would have had."""
    own = tmp_path / "own"
    own.mkdir()
    (own / "sitecustomize.py").write_text("import builtins\nbuiltins.OWN_SITECUSTOMIZE = True\n")
    r = _child("import builtins, sys; print(getattr(builtins, 'OWN_SITECUSTOMIZE', False), "
               "type(sys.meta_path[0]).__name__)", {**os.environ, "PYTHONPATH": str(own)})
    assert r.stdout.split() == ["True", "Fence"], r.stderr


def test_a_refusal_after_the_session_leaves_no_stray_log(tmp_path):
    """A detached child refusing after pytest_sessionfinish removed the log: still refused,
    and the log is not made again as a stray tmp file."""
    gone = tmp_path / "audio-fence-gone.jsonl"
    r = _child("import sounddevice", {**os.environ, audio_fence.LOG_ENV: str(gone)})
    assert r.returncode == 1 and "RealAudioDevice" in r.stderr, r.stderr
    assert not gone.exists()


def test_an_explicit_env_is_copied_not_changed():
    env = {"PATH": "/usr/bin", "PYTHONPATH": "/x"}
    got = audio_fence.fenced_env(env)
    assert env == {"PATH": "/usr/bin", "PYTHONPATH": "/x"}
    assert got["PYTHONPATH"] == os.pathsep.join([audio_fence.FENCE_DIR, "/x"])
    assert got[audio_fence.LOG_ENV] == os.environ[audio_fence.LOG_ENV]
    assert audio_fence.fenced_env(None) is None
    assert audio_fence.fenced_env(got)["PYTHONPATH"] == got["PYTHONPATH"]   # once, not twice


# -- in any order: every test, root or tests/mouth, has the fence ---------------------------------

_PLANNED = re.compile(r"^\s+(tests/\S+?) \(fixtures used: ([^)]*)\)$", re.M)


@pytest.mark.parametrize("order", [
    ("tests/mouth/test_mouth_device_gaps.py", "tests/test_ears_settings_schema.py", "tests/mouth/test_mouth_apple.py"),
    ("tests/test_ears_sounds_call.py", "tests/mouth", "tests/test_mouth_sounds_scoop_by.py"),
], ids=["mouth-root-mouth", "root-dir-root"])
def test_every_test_is_fenced_in_any_order(order, tmp_path):
    env = {**os.environ, "HOME": str(tmp_path)}
    out = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--setup-plan", *order],
                         cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stdout[-3000:] + out.stderr[-2000:]
    planned = _PLANNED.findall(out.stdout)
    assert {item.split("::")[0] for item, _ in planned} >= {o for o in order if o.endswith(".py")}
    assert [item for item, used in planned if "no_real_audio_device" not in used.split(", ")] == []
