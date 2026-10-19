"""EARS-10: the side-car has a name. Activity Monitor showed plain "Python" (pid 70809);
ps -c and Activity Monitor read the kernel's p_comm, which setproctitle never reaches on
macOS. speakers/run.sh runs a copy of the interpreter's own binary, named ears-speakers, from
the venv's bin/. No model, no audio: run.sh runs from a scratch copy of speakers/ whose
speaker-sidecar2.py is a stub that imports a module only the venv has, writes its pid, and
waits for STOP. The name is MEASURED, three ways, on the live process."""
import ctypes
import os
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "speakers" / "run.sh"
NAME = "ears-speakers"

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="p_comm via libproc: macOS")

STUB = '''import os, sys, time
import only_in_the_venv  # the venv's site-packages loaded: real deps would too
open("pid.tmp", "w").write(f"{os.getpid()}\\n{only_in_the_venv.OK}\\n{' '.join(sys.argv[1:])}\\n")
os.replace("pid.tmp", "pid")
t = time.time() + 30
while not os.path.exists("STOP") and time.time() < t: time.sleep(0.05)
'''


def bases():
    """The interpreters a side-car venv is built on: the one running the tests (uv's, a
    plain binary), and a Homebrew framework python if this Mac has one (its bin/python3.12
    is a stub that re-execs Python.app/Contents/MacOS/Python: the "Python" it showed)."""
    out = [pytest.param(Path(getattr(sys, "_base_executable", sys.executable)).resolve(), id="uv")]
    fw = Path("/opt/homebrew/opt/python@3.12/bin/python3.12")
    out.append(pytest.param(fw, id="framework", marks=pytest.mark.skipif(
        not fw.exists(), reason="no Homebrew framework python here")))
    return out


def p_comm(pid):
    """What Activity Monitor and ps -c read: the kernel's own name for the process."""
    buf = ctypes.create_string_buffer(64)
    ctypes.CDLL("/usr/lib/libproc.dylib").proc_name(pid, buf, 64)
    return buf.value.decode()


def ps(pid, *opts):
    return subprocess.run(["ps", *opts, "-p", str(pid)], capture_output=True, text=True, check=False).stdout.strip()


def scratch(tmp_path, base):
    """speakers/ with run.sh, the stub side-car, one reference; and a venv on base."""
    sp = tmp_path / "speakers"
    (sp / "refs").mkdir(parents=True)
    shutil.copy(RUN, sp / "run.sh")
    (sp / "speaker-sidecar2.py").write_text(STUB)
    with wave.open(str(sp / "refs" / "theo.wav"), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\0\0" * 160)
    venv = tmp_path / "venv"
    subprocess.run([str(base), "-m", "venv", "--without-pip", str(venv)], check=True, timeout=120)
    site = subprocess.run([str(venv / "bin" / "python"), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                          capture_output=True, text=True, check=True).stdout.strip()
    Path(site, "only_in_the_venv.py").write_text("OK = 'venv'\n")
    # launchd's environment (the plist): a bare PATH, no venv on it
    env = {"HOME": os.environ["HOME"], "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
           "SPEAKER_PY": str(venv / "bin" / "python"), "SPEAKER_DEVICE": "follow"}
    return sp, venv, env


def wait_pid(sp, proc=None):
    t = time.time() + 30
    while not (sp / "pid").exists():
        if proc is not None and proc.poll() is not None:
            pytest.fail(f"run.sh exited {proc.returncode}: {proc.stderr.read()}")
        assert time.time() < t, "the stub side-car never started"
        time.sleep(0.05)
    pid, ok, argv = (sp / "pid").read_text().splitlines()
    return int(pid), ok, argv


def check_named(pid, ok, argv, venv):
    assert ok == "venv"
    assert "--device follow" in argv and "--enroll theo=refs/theo.wav" in argv
    assert ps(pid, "-c", "-o", "comm=") == NAME          # ps -c
    assert ps(pid, "-o", "comm=") == NAME                # ps -o comm= (argv[0])
    assert p_comm(pid) == NAME                           # the kernel: Activity Monitor
    assert (venv / "bin" / NAME).is_file()
    # pgrep -f speaker-sidecar2.py, and anyone reading ps args, still finds it
    assert "speaker-sidecar2.py --device follow" in ps(pid, "-o", "args=")


@pytest.mark.parametrize("base", bases())
def test_run_sh_names_the_side_car(tmp_path, base):
    """The launchd path: plist -> /bin/bash run.sh, in the foreground (exec)."""
    sp, venv, env = scratch(tmp_path, base)
    p = subprocess.Popen(["/bin/bash", str(sp / "run.sh")], cwd=sp, env=env, text=True,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        pid, ok, argv = wait_pid(sp, p)
        assert pid == p.pid  # run.sh exec'd: launchd's pid IS the side-car
        check_named(pid, ok, argv, venv)
    finally:
        (sp / "STOP").touch()
        p.wait(timeout=30)
    assert p.returncode == 0, p.stderr.read()


@pytest.mark.parametrize("base", bases())
def test_run_sh_detach_names_the_side_car(tmp_path, base):
    sp, venv, env = scratch(tmp_path, base)
    p = subprocess.run(["/bin/bash", str(sp / "run.sh"), "--detach"], cwd=sp, env=env,
                       capture_output=True, text=True, timeout=60, check=False)
    assert p.returncode == 0, p.stderr
    pid, ok, argv = wait_pid(sp)
    try:
        check_named(pid, ok, argv, venv)
    finally:
        (sp / "STOP").touch()
        t = time.time() + 30
        while ps(pid, "-o", "pid=") and time.time() < t: time.sleep(0.05)


def test_run_sh_refreshes_a_stale_copy(tmp_path):
    """A python upgrade leaves the old copy behind (linked to a framework path that is
    gone): run.sh replaces any ears-speakers that differs from the real binary."""
    sp, venv, env = scratch(tmp_path, Path(getattr(sys, "_base_executable", sys.executable)).resolve())
    (venv / "bin" / NAME).write_text("#!/bin/sh\nexit 3\n"); (venv / "bin" / NAME).chmod(0o755)
    p = subprocess.Popen(["/bin/bash", str(sp / "run.sh")], cwd=sp, env=env, text=True,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        pid, ok, argv = wait_pid(sp, p)
        check_named(pid, ok, argv, venv)
    finally:
        (sp / "STOP").touch()
        p.wait(timeout=30)
    assert p.returncode == 0


def test_run_sh_not_a_venv_still_runs_and_says_so(tmp_path):
    """A bare interpreter has no venv bin/ to put the copy in: run.sh runs it as is and
    says why it is not named, rather than writing into the interpreter's own install."""
    base = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
    sp, _venv, env = scratch(tmp_path, base)
    stub = (sp / "speaker-sidecar2.py").read_text().replace("import only_in_the_venv", "only_in_the_venv = type('m', (), {'OK': 'bare'})")
    (sp / "speaker-sidecar2.py").write_text(stub)
    env["SPEAKER_PY"] = str(base)
    p = subprocess.Popen(["/bin/bash", str(sp / "run.sh")], cwd=sp, env=env, text=True,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        _pid, ok, _argv = wait_pid(sp, p)
        assert ok == "bare"
        assert not (base.parent / NAME).exists()
    finally:
        (sp / "STOP").touch()
        p.wait(timeout=30)
    assert p.returncode == 0
    assert "not a venv's python" in p.stderr.read()
