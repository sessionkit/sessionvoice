"""No test writes under the live HOME (EARS-313).

An unfenced tests/mouth run wrote the live ``~/.voicemode/voicemode.env``. This audit
hook is installed for the whole pytest session (tests/conftest.py). Any in-process
write under the account's home (from ``pwd``, not ``$HOME``, which a test may move)
raises :class:`LiveHomeWrite` before it happens, and is recorded so the test fails
at teardown even if its code swallowed the error. Covered: opening for writing,
mkdir, rename/replace (both ends), remove/unlink, rmdir, link, symlink, truncate,
rmtree. The checkout itself is exempt: its ``.pyc`` files and ``.pytest_cache`` sit
under the home on a dev machine. So are the interpreter's trees (``sys.base_prefix``,
``sys.prefix``): a uv-managed CPython sits under the home too. Subprocesses carry
no audit hook; a test that spawns one sets that child's HOME itself.
"""
from __future__ import annotations

import os
import pwd
import sys
import threading

import pytest

LIVE_HOME = os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)
REPO = os.path.realpath(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# The interpreter's trees are not user data. A uv-managed CPython lives under the
# home (~/.local/share/uv/python/...), and its first run writes the stdlib's .pyc
# there; refusing that killed collection (EARS-327). sys.prefix is the venv.
PYTHON = tuple(dict.fromkeys(os.path.realpath(p) for p in (sys.base_prefix, sys.prefix)))

on = False          # set by the session; the hook is a no-op until then
hits: list = []     # every refused write: (event, path)

_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
_FIRST = {"os.mkdir", "os.remove", "os.rmdir", "os.truncate", "shutil.rmtree"}   # args[0] is written
_BOTH = {"os.rename"}                                                            # src goes, dst comes
_SECOND = {"os.link", "os.symlink"}                                              # args[1] is made
_busy = threading.local()


class LiveHomeWrite(RuntimeError):
    """A test tried to write under the live HOME. Not an OSError, so ``except OSError`` misses it."""


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root + os.sep)


def _paths(event: str, args: tuple) -> tuple:
    if event == "open":
        path, mode, flags = args[0], args[1], args[2]
        writes = bool(flags & _WRITE_FLAGS) if mode is None else any(c in mode for c in "wax+")
        return (path,) if writes else ()
    if event in _FIRST:
        return args[:1]
    if event in _BOTH:
        return args[:2]
    if event in _SECOND:
        return args[1:2]
    return ()


def refused(path) -> bool:
    """True if a write to ``path`` would land under the live HOME and outside the checkout
    and the interpreter's own trees."""
    try:
        p = os.path.realpath(os.fsdecode(os.fspath(path)))
    except TypeError:                  # a file descriptor: no path to judge
        return False
    return _under(p, LIVE_HOME) and not any(_under(p, root) for root in (REPO, *PYTHON))


def audit(event: str, args: tuple) -> None:
    if not on or getattr(_busy, "now", False):
        return
    paths = _paths(event, args)
    if not paths:
        return
    _busy.now = True
    try:
        for path in paths:
            if path is not None and refused(path):
                hits.append((event, os.fsdecode(os.fspath(path))))
                raise LiveHomeWrite(f"a test tried to write under the live HOME {LIVE_HOME}: "
                                    f"{event} {path!r} (EARS-313: fence HOME first)")
    finally:
        _busy.now = False


_installed = False


def install() -> None:
    """Add the audit hook once per process (a hook cannot be removed; ``on`` gates it)."""
    global _installed
    if not _installed:
        sys.addaudithook(audit)
        _installed = True


# -- as a pytest plugin: tests/conftest.py imports these; `-p tests.home_guard` loads them alone

def pytest_sessionstart(session):
    global on
    install()
    on = True


def pytest_sessionfinish(session):
    global on
    on = False


@pytest.fixture(autouse=True)
def live_home_untouched():
    """Fails the test at teardown if it wrote under the live HOME, even if its code caught it."""
    before = len(hits)
    yield
    if len(hits) > before:
        pytest.fail(f"wrote under the live HOME: {hits[before:]} (EARS-313)")
