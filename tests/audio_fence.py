"""No test reaches a real audio device (EARS-320).

``tests/mouth/test_mouth_device_gaps.py`` played to the user's AirPods on a laptop
(once): the test named ``airpods`` with no fake, the host had them, and the
player opened them. Nothing in the suite stopped it; the dev-group venv, which has
no sounddevice, had been standing in for a fence.

The fence (loaded for every test by ``tests/conftest.py``; ``-p tests.audio_fence``
loads it alone):

- **In the pytest process**, ``fixtures/audio_fence/_audio_fence.py`` sits first on
  ``sys.meta_path`` and refuses the real ``sounddevice``, installed or not. That covers
  ``mouth.output._sd``, ``ears.listen.sources._sd``, ``ears.heard`` and any bare
  ``import sounddevice``. A test's own fake still wins: a monkeypatched ``_sd``
  never imports, and a fake module (``fixtures/fake_sd``, a stub in tmp) is allowed.
- **In every child process.** ``subprocess.Popen`` is wrapped for the session: each
  child's PYTHONPATH gets ``fixtures/audio_fence`` first, whose ``sitecustomize``
  installs the same hook, even when the test REPLACED PYTHONPATH or the whole env,
  in the child's ``env=`` or in ``os.environ`` itself (``monkeypatch.setenv`` / ``delenv``).
  ``os.environ`` carries it too, for children started another way (a fork, a
  detached ``mouth serve``). A child run with ``-S``, ``-I`` or ``-E`` skips
  ``sitecustomize``; no test that can reach a player does that.
- **Loudly.** A refusal raises ``RealAudioDevice`` naming the test, and is written to
  ``$AUDIO_FENCE_LOG``. The test fails at teardown if anything (its own code, a
  player's broad except, a child process) was refused during it, even if the error
  was swallowed. A refusal no test was running for (a detached child, late) fails
  the session.
- ``MOUTH_DEVICE`` is cleared, so the host's choice of device never
  reaches a test (``tests/conftest.py``).

A test that means to be refused (the fence's own) wraps it in :func:`expect_refusal`.
"""
from __future__ import annotations

import contextlib
import importlib.util
import inspect
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

FENCE_DIR = str(Path(__file__).resolve().parent / "fixtures" / "audio_fence")


def _load():
    mod = sys.modules.get("_audio_fence")         # a child pytest's sitecustomize got here first
    if mod is None:
        spec = importlib.util.spec_from_file_location("_audio_fence", os.path.join(FENCE_DIR, "_audio_fence.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_audio_fence"] = mod
        spec.loader.exec_module(mod)
    return mod


hook = _load()
RealAudioDevice = hook.RealAudioDevice
LOG_ENV = hook.LOG_ENV

_expected: set = set()     # records a test asked for (expect_refusal)
_reported: set = set()     # records a test already failed for


def _key(rec: dict) -> str:
    return json.dumps(rec, sort_keys=True)


def log_path() -> str:
    return os.environ.get(LOG_ENV) or _session["log"]


def records(start: int = 0) -> tuple[list, int]:
    """The refusals logged from byte ``start`` on, and the offset after them."""
    try:
        with open(log_path(), "rb") as f:
            f.seek(start)
            data = f.read()
    except OSError:
        return [], start
    end = data.rfind(b"\n") + 1                    # a line still being written waits for next time
    recs = [json.loads(x) for x in data[:end].splitlines() if x.strip()]
    return recs, start + end


def _size() -> int:
    try:
        return os.path.getsize(log_path())
    except OSError:
        return 0


@contextlib.contextmanager
def expect_refusal():
    """For the fence's own tests: refusals inside the block are wanted, not a failure.
    Yields the list, filled at exit, of every refusal logged inside the block (children too)."""
    got: list = []
    start = _size()
    try:
        yield got
    finally:
        recs, _ = records(start)
        _expected.update(_key(r) for r in recs)
        got.extend(recs)


# -- children: every subprocess the session starts carries the fence -------------------------

def _with_fence(pythonpath) -> str:
    parts = [p for p in (pythonpath or "").split(os.pathsep) if p]
    if parts[:1] == [FENCE_DIR]:
        return os.pathsep.join(parts)
    return os.pathsep.join([FENCE_DIR] + [p for p in parts if p != FENCE_DIR])


_session: dict = {}        # this session's own log path, kept in case a test removes it from os.environ


def _carried() -> dict:
    """What every child inherits from the fence: the log (this session's, even if a test
    dropped it from ``os.environ``) and the test it runs for."""
    got = {k: os.environ[k] for k in (LOG_ENV, "PYTEST_CURRENT_TEST") if k in os.environ}
    if LOG_ENV not in got and _session.get("log"):
        got[LOG_ENV] = _session["log"]
    return got


def fenced_env(env):
    """A copy of a child's explicit env, with the fence first on PYTHONPATH (None stays None:
    the child inherits ``os.environ``, which carries it)."""
    if env is None:
        return None
    env = dict(env)
    if any(isinstance(k, bytes) for k in env):
        enc = {os.fsencode(k): os.fsencode(v) for k, v in env.items()}
        enc[b"PYTHONPATH"] = os.fsencode(_with_fence(os.fsdecode(enc.get(b"PYTHONPATH", b""))))
        for k, v in _carried().items():
            enc.setdefault(os.fsencode(k), os.fsencode(v))
        return enc
    env["PYTHONPATH"] = _with_fence(env.get("PYTHONPATH"))
    for k, v in _carried().items():
        env.setdefault(k, v)
    return env


def _environ_fenced() -> bool:
    """``os.environ`` still carries the fence. A test that set or removed PYTHONPATH (or the
    log) there with ``monkeypatch.setenv`` / ``delenv`` took it away from every child that
    inherits the env, so those children get an explicit, fenced copy instead."""
    return os.environ.get("PYTHONPATH", "").split(os.pathsep)[:1] == [FENCE_DIR] and LOG_ENV in os.environ


_popen_init = subprocess.Popen.__init__
_popen_sig = inspect.signature(_popen_init)


def _fenced_popen_init(self, *args, **kwargs):
    bound = _popen_sig.bind(self, *args, **kwargs)
    if bound.arguments.get("env") is not None:
        bound.arguments["env"] = fenced_env(bound.arguments["env"])
    elif not _environ_fenced():                  # inherits an os.environ a test changed
        bound.arguments["env"] = fenced_env(os.environ)
    _popen_init(*bound.args, **bound.kwargs)


_fenced_popen_init.__wrapped__ = _popen_init
_fenced_popen_init.audio_fence = True


def install() -> None:
    """Fence this process and every child it starts (idempotent). A fresh log per session."""
    already = sys.modules.get("sounddevice")
    if already is not None and hook.is_real(getattr(already, "__file__", None)):
        raise RuntimeError(f"the real sounddevice was imported before the audio fence ({already.__file__}): "
                           "nothing may load it in a test run (EARS-320)")
    fd, path = tempfile.mkstemp(prefix="audio-fence-", suffix=".jsonl")
    os.close(fd)
    os.environ[LOG_ENV] = _session["log"] = path
    os.environ["PYTHONPATH"] = _with_fence(os.environ.get("PYTHONPATH"))
    hook.install()
    if not getattr(subprocess.Popen.__init__, "audio_fence", False):
        subprocess.Popen.__init__ = _fenced_popen_init


def on() -> bool:
    return hook.installed() and getattr(subprocess.Popen.__init__, "audio_fence", False) \
        and os.environ.get("PYTHONPATH", "").split(os.pathsep)[:1] == [FENCE_DIR]


install()      # at import: before any test module is collected, so before anything can import sounddevice


# -- as a pytest plugin: tests/conftest.py imports these; `-p tests.audio_fence` loads them alone

def _fail_text(recs: list) -> str:
    return "; ".join(f"{r['test']} imported the real {r['module']} ({r['found']}) in pid {r['pid']} "
                     f"argv {r['argv']}" for r in recs)


def _sd_modules() -> dict:
    return {k: v for k, v in sys.modules.items() if k.partition(".")[0] in hook.REFUSED}


def _restore_sd_modules(before: dict) -> None:
    """Put ``sys.modules``' sounddevice entries back as they were before the test. A fake a
    test imported (from a tmp dir on a monkeypatched sys.path, say) must not outlive it: the
    next test's unfaked import would get that stale fake from ``sys.modules`` and never reach
    the hook, so a missing fake would fail quietly instead of being refused."""
    for k in [k for k in _sd_modules() if k not in before]:
        del sys.modules[k]
    sys.modules.update(before)


@pytest.fixture(autouse=True)
def no_real_audio_device():
    """Fails the test at teardown if anything during it reached for a real audio device,
    in this process or a child, even if the error was caught. A fake sounddevice the test
    left in ``sys.modules`` is dropped, so the fence holds in any order."""
    start = _size()
    mods = _sd_modules()
    yield
    _restore_sd_modules(mods)
    recs, _ = records(start)
    bad = [r for r in recs if _key(r) not in _expected and _key(r) not in _reported]
    _reported.update(_key(r) for r in bad)
    if bad:
        pytest.fail(f"reached for a real audio device: {_fail_text(bad)} (EARS-320: fake the device)")


def pytest_sessionfinish(session):
    """A refusal outside every test's teardown (a detached child, after its test) fails the run."""
    recs, _ = records(0)
    late = [r for r in recs if _key(r) not in _expected and _key(r) not in _reported]
    if late:
        _reported.update(_key(r) for r in late)
        tr = session.config.pluginmanager.get_plugin("terminalreporter")
        msg = f"audio fence: reached for a real audio device outside a test: {_fail_text(late)} (EARS-320)"
        if tr is not None:
            tr.write("\n")                 # after the progress dots, on a line of its own
            tr.write_line(msg, red=True)
        else:
            print(msg, file=sys.stderr)
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
    with contextlib.suppress(OSError):
        os.unlink(log_path())
