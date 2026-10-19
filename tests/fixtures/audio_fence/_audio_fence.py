"""The audio fence's import hook (EARS-320): no test reaches a REAL sounddevice.

A mouth test played to the user's AirPods: nothing stopped a test from loading the
real ``sounddevice`` (PortAudio) and opening whatever output it found. This hook sits
first on ``sys.meta_path``. An import of ``sounddevice`` (or its ``_sounddevice``
binding) is refused unless the module found is a FAKE, so a test cannot open, query
or even load the real PortAudio, whether the venv has it installed or not:

- **installed** (a venv with the capture extra): the module found is in site-packages.
  Refused, and none of its code runs.
- **absent** (the dev-group venv): nothing is found. Refused as well, so a test that
  forgot its fake fails here instead of passing because the import failed
  (``player.py``'s broad except used to write a missing module as ``device-absent``).
- **a fake** (``tests/fixtures/fake_sd``, a stub a test writes in tmp, or a module a
  test put in ``sys.modules``): allowed. Anything outside site-packages and the
  interpreter's prefixes is a fake, because the real one is only ever installed there.

A refusal raises :class:`RealAudioDevice` (a RuntimeError, so ``except ImportError``
and ``pytest.importorskip`` do not mistake it for "not installed"), naming the test
from ``PYTEST_CURRENT_TEST``. It is also written, one JSON line, to the file named by
``AUDIO_FENCE_LOG``, so the test fails at teardown even if its code (or a child
process's) swallowed the error: see ``tests/audio_fence.py``.

This module is loaded in the pytest process by ``tests/audio_fence.py`` and in every
child Python by ``sitecustomize.py`` beside it (that directory is put first on each
child's PYTHONPATH). It imports nothing beyond what every interpreter has loaded at
startup, so a child's import-time measurements do not move.
"""
import _thread
import os
import sys

REFUSED = ("sounddevice", "_sounddevice")
LOG_ENV = "AUDIO_FENCE_LOG"
_SITE_NAMES = ("site-packages", "dist-packages")


class RealAudioDevice(RuntimeError):
    """A test reached for the real sounddevice. Not an ImportError: nothing may read it as absent."""


def _real_roots() -> list:
    roots = {sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix}
    try:
        import site
        roots.update(site.getsitepackages())
        roots.add(site.getusersitepackages())
    except Exception:  # noqa: BLE001 - a -S child or an odd build: the prefixes still hold
        pass
    return sorted({os.path.realpath(r) for r in roots if r})


def is_real(origin) -> bool:
    """True if ``origin`` is an installed module: in site-packages, or under the interpreter's prefixes."""
    if not origin or not os.path.isabs(str(origin)):
        return True          # built in, frozen or a namespace: never a test's fake
    path = os.path.realpath(str(origin))
    if any(part in _SITE_NAMES for part in path.split(os.sep)):
        return True
    return any(path == r or path.startswith(r + os.sep) for r in _real_roots())


hits: list = []              # every refusal in this process, newest last


def _record(rec: dict) -> None:
    hits.append(rec)
    log = os.environ.get(LOG_ENV)
    if not log:
        return
    import json
    line = (json.dumps(rec) + "\n").encode()
    try:
        fd = os.open(log, os.O_WRONLY | os.O_APPEND)   # never O_CREAT: the session made it; gone = session over
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    except OSError:
        pass                 # the raise below still stops it


def refuse(name: str, origin) -> None:
    test = os.environ.get("PYTEST_CURRENT_TEST") or "(no test running)"
    found = "not installed" if origin is None else str(origin)
    rec = {"test": test, "module": name, "found": found, "pid": os.getpid(), "argv": sys.argv[:3]}
    _record(rec)
    raise RealAudioDevice(
        f"{test} tried to import the real {name} ({found}) in pid {os.getpid()}: no test may reach a "
        f"real audio device. Fake it: tests/fixtures/fake_sd first on PYTHONPATH, or monkeypatch "
        f"mouth.output._sd / ears.listen.sources._sd (EARS-320)")


class Fence:
    """A ``sys.meta_path`` finder that lets a fake sounddevice load and refuses the real one."""

    def __init__(self) -> None:
        self._looking: set = set()   # thread idents mid-lookup: our own search must not find us again

    def find_spec(self, fullname, path=None, target=None):
        me = _thread.get_ident()
        if fullname.partition(".")[0] not in REFUSED or me in self._looking:
            return None
        self._looking.add(me)
        try:
            spec = None
            for finder in list(sys.meta_path):
                if finder is self or isinstance(finder, Fence) or not hasattr(finder, "find_spec"):
                    continue
                spec = finder.find_spec(fullname, path, target)
                if spec is not None:
                    break
        finally:
            self._looking.discard(me)
        origin = None if spec is None else (spec.origin or None)
        if spec is not None and not is_real(origin):
            return spec      # a fake: load it as found
        refuse(fullname, origin)


def installed() -> bool:
    return any(isinstance(f, Fence) for f in sys.meta_path)


def install() -> None:
    """Put the fence first on ``sys.meta_path`` (once per process)."""
    if not installed():
        sys.meta_path.insert(0, Fence())
