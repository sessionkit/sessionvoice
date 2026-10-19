"""The keeper runs the call ears as its child (merged:
one ears job). These run the keeper's calls functions alone, on a fake
call-ears (a sleeping script), so no test opens a call; and call-ears' captured() on fake
ps output."""
import importlib.machinery
import importlib.util
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
KEEPER = ROOT / "keeper" / "ears-keeper.sh"


def _calls_block() -> str:
    s = KEEPER.read_text()
    return s[s.index("# --- calls:"):s.index("# --- end calls ---")]


def _bash(script: str, env: dict, timeout=30) -> str:
    pre = 'note() { echo "NOTE $*"; }\n'
    out = subprocess.run(["bash", "-c", pre + _calls_block() + "\n" + script], env=dict(os.environ, **env),
                         capture_output=True, text=True, timeout=timeout)
    assert out.returncode == 0, out.stderr
    return out.stdout


@pytest.fixture
def env(tmp_path):
    tag = f"earstest{uuid.uuid4().hex[:8]}"
    fake = tmp_path / tag / "call-ears"
    fake.parent.mkdir()
    fake.write_text("import time\ntime.sleep(float(__import__('os').environ.get('FAKE_LIFE', '30')))\n")
    return {"PY": sys.executable, "EARS_CALL_EARS": str(fake), "LOGDIR": str(tmp_path),
            "CALLS_PIDF": str(tmp_path / "call-ears.pid"), "CALL_EARS_DIR": str(tmp_path / "dir"),
            "EARS_CALL_EARS_PAT": rf"[Pp]ython[0-9.]* [^ ]*{tag}/call-ears( |$)", "_tag": tag}


def _kill_all(tag):
    subprocess.run(["pkill", "-f", tag])


def test_starts_one_call_ears_and_keeps_it(env):
    try:
        out = _bash('export LOGDIR CALLS_PIDF PY; calls_up; p1=$calls_pid; sleep 0.5; calls_up; '
                    'echo "SAME $([ "$p1" = "$calls_pid" ] && echo yes)"; '
                    'echo "PIDF $(cat "$CALLS_PIDF")=$calls_pid"; calls_down; sleep 0.3; '
                    'kill -0 "$p1" 2>/dev/null && echo ALIVE || echo GONE', env)
        assert "NOTE call ears: up, pid" in out and "SAME yes" in out and "GONE" in out
        pidf = next(l for l in out.splitlines() if l.startswith("PIDF ")).split()[1]
        a, b = pidf.split("="); assert a == b
    finally:
        _kill_all(env["_tag"])


def test_restarts_after_it_dies_with_backoff(env):
    try:
        out = _bash('calls_up; p1=$calls_pid; sleep 1; calls_up; echo "WAIT $calls_wait"; '
                    'calls_next=0; calls_up; echo "NEW $([ -n "$calls_pid" ] && [ "$calls_pid" != "$p1" ] && echo yes)"; '
                    'calls_down', dict(env, FAKE_LIFE="0.2"))
        assert "ended (exit 0); again in 4s" in out and "WAIT 4" in out and "NEW yes" in out
    finally:
        _kill_all(env["_tag"])


def test_backoff_doubles_to_five_minutes_and_resets_after_a_good_run(env):
    out = _bash('for a in "5 2" "5 4" "5 200" "5 300" "60 300" "3600 8"; do calls_next_wait $a; done', env)
    assert out.split() == ["4", "8", "300", "300", "2", "2"]


def test_stop_file_holds_it_down(env, tmp_path):
    (tmp_path / "dir").mkdir()
    (tmp_path / "dir" / "STOP").touch()
    out = _bash('calls_up; echo "PID [$calls_pid]"', env)
    assert "PID []" in out


def test_off_with_ears_calls_0(env):
    assert "PID []" in _bash('calls_up; echo "PID [$calls_pid]"', dict(env, EARS_CALLS="0"))


def test_stands_aside_for_another_jobs_call_ears(env):
    other = subprocess.Popen([sys.executable, env["EARS_CALL_EARS"]], env=dict(os.environ, FAKE_LIFE="30"))
    try:
        time.sleep(0.3)
        out = _bash('calls_up; echo "PID [$calls_pid]"', env)
        assert "PID []" in out and f"another job runs them, so not starting ours: {other.pid}:" in out
        other.kill(); other.wait()
        out = _bash('calls_other=x; calls_up; echo "PID [$calls_pid]"; calls_down', env)
        assert "the other job's are gone; starting ours" in out and "PID []" not in out
    finally:
        other.kill()
        _kill_all(env["_tag"])


def test_default_pattern_is_a_python_running_call_ears_only(tmp_path):
    """Not a capture (`--session call-ears-5`), not a pager on the file."""
    tag = f"earstest{uuid.uuid4().hex[:8]}"
    d = tmp_path / tag
    d.mkdir()
    f = d / "call-ears"
    f.write_text("import time; time.sleep(30)\n")
    ears = subprocess.Popen([sys.executable, str(f)])
    cap = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", f"{tag}.listen", "capture",
                            "--source", "call:5", "--session", "call-ears-5"])
    pager = subprocess.Popen(["/usr/bin/tail", "-f", str(f)], stdout=subprocess.DEVNULL)
    try:
        time.sleep(0.3)
        out = _bash("other_call_ears", {"PY": sys.executable})
        pids = {int(l.split(":")[0]) for l in out.splitlines()}   # argv is cut at 160: match pids
        assert pids & {ears.pid, cap.pid, pager.pid} == {ears.pid}
    finally:
        for p in (ears, cap, pager):
            p.kill(); p.wait()


def _call_ears():
    loader = importlib.machinery.SourceFileLoader("call_ears", str(ROOT / "keeper" / "call-ears"))
    spec = importlib.util.spec_from_loader("call_ears", loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def test_captured_names_the_calls_that_have_an_ear(monkeypatch):
    m = _call_ears()
    ps = "\n".join([
        "/x/.venv/bin/python -m ears.listen capture --source call:54681 --heartbeat 15 --session call-ears-54681",
        "/x/.venv/bin/python -m ears.listen capture --source mic --device default",
        "/x/.venv/bin/python /x/keeper/call-ears",
        "/x/python -m voice_mode.listen capture --source call:7",
    ])
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, ps, ""))
    assert m.captured() == {54681, 7}


def test_call_ears_dir_follows_the_keeper(monkeypatch, tmp_path):
    monkeypatch.setenv("CALL_EARS_DIR", str(tmp_path / "ce"))
    assert _call_ears().DIR == tmp_path / "ce"


def test_publish_writes_the_live_calls_whole(monkeypatch, tmp_path):
    monkeypatch.setenv("CALL_EARS_DIR", str(tmp_path))
    m = _call_ears()
    m.publish({3081, 12})
    assert (tmp_path / "live").read_text() == "call:12,call:3081\n"
    m.publish(set())
    assert (tmp_path / "live").read_text() == "\n"
    assert not (tmp_path / "live.tmp").exists()


class _Popen:
    def __init__(self, cmd, **kw):
        self.cmd, self.kw, self.pid = cmd, kw, 4242


@pytest.mark.parametrize("code", ["", "/somewhere/voicemode"])
def test_call_ears_code_is_ignored_and_said_once(monkeypatch, tmp_path, capsys, code):
    """EARS-294 T3 (RT-10): the CALL_EARS_CODE branch that ran voice_mode.listen is gone. A call
    ear is ears.listen with the env as it is, whatever CALL_EARS_CODE says; set, main() says once
    that it is ignored."""
    monkeypatch.setenv("CALL_EARS_DIR", str(tmp_path))
    monkeypatch.setenv("CALL_EARS_CODE", code)
    m = _call_ears()
    started = []
    monkeypatch.setattr(m.subprocess, "Popen", lambda cmd, **kw: started.append(_Popen(cmd, **kw)) or started[-1])
    m.start(7)
    (p,) = started
    assert p.cmd[1:3] == ["-m", "ears.listen"] and p.cmd[3] == "capture"
    assert p.kw["env"].get("PYTHONPATH") == os.environ.get("PYTHONPATH")
    monkeypatch.setattr(m, "socks", lambda: [])
    monkeypatch.setattr(m, "captured", lambda: set())
    monkeypatch.setattr(m, "live_calls", lambda: m.STOP.touch() or set())   # one look, then stop
    m.main()
    said = [ln for ln in capsys.readouterr().out.splitlines() if "CALL_EARS_CODE" in ln]
    assert len(said) == (1 if code else 0), said
    assert not said or said[0].endswith(f"CALL_EARS_CODE is set but ignored: the call ears are ears.listen, run by {m.PY}")
