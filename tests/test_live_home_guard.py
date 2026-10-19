"""tests/home_guard.py: no test writes under the live HOME (EARS-313).

Every write here goes to a stand-in "live home" in tmp (``LIVE_HOME`` patched), so a
guard that fails these tests can never reach the real one.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests import home_guard
from tests.home_guard import LiveHomeWrite

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def live(tmp_path, monkeypatch) -> Path:
    """A stand-in live home with a file and a dir already in it, and a checkout inside it."""
    home = tmp_path / "live"
    (home / "repo").mkdir(parents=True)
    (home / ".voicemode").mkdir()
    (home / ".voicemode" / "voicemode.env").write_text("KEEP=1\n")
    (home / "old").mkdir()
    monkeypatch.setattr(home_guard, "LIVE_HOME", str(home.resolve()))
    monkeypatch.setattr(home_guard, "REPO", str((home / "repo").resolve()))
    monkeypatch.setattr(home_guard, "hits", [])
    return home


def test_the_guard_is_on_for_every_test():
    assert home_guard.on and home_guard._installed
    assert home_guard.LIVE_HOME == os.path.realpath(os.path.expanduser("~" + os.environ.get("USER", ""))) \
        or os.path.isdir(home_guard.LIVE_HOME)


@pytest.mark.parametrize("write", [
    lambda h, out: (h / ".voicemode" / "voicemode.env").write_text("clobbered\n"),
    lambda h, out: open(h / ".voicemode" / "voicemode.env", "a").close(),
    lambda h, out: open(h / ".voicemode" / "voicemode.env", "r+").close(),
    lambda h, out: os.close(os.open(h / "new.env", os.O_CREAT | os.O_WRONLY)),
    lambda h, out: (h / "newdir").mkdir(),
    lambda h, out: os.replace(out, h / "moved-in"),
    lambda h, out: os.replace(h / ".voicemode" / "voicemode.env", out.parent / "moved-out"),
    lambda h, out: (h / ".voicemode" / "voicemode.env").unlink(),
    lambda h, out: (h / "old").rmdir(),
    lambda h, out: os.symlink(out, h / "link"),
    lambda h, out: shutil.rmtree(h / "old"),
    lambda h, out: shutil.copyfile(out, h / "copied"),
], ids=["write_text", "append", "r+", "os.open-creat", "mkdir", "rename-in", "rename-out",
        "unlink", "rmdir", "symlink", "rmtree", "copyfile"])
def test_a_write_under_the_live_home_is_refused_before_it_happens(live, tmp_path, write):
    out = tmp_path / "outside.txt"
    out.write_text("x")
    before = sorted(str(p) for p in live.rglob("*"))
    with pytest.raises(LiveHomeWrite, match="live HOME"):
        write(live, out)
    assert sorted(str(p) for p in live.rglob("*")) == before
    assert (live / ".voicemode" / "voicemode.env").read_text() == "KEEP=1\n"
    assert len(home_guard.hits) == 1


def test_reads_the_checkout_and_everywhere_else_are_allowed(live, tmp_path):
    assert (live / ".voicemode" / "voicemode.env").read_text() == "KEEP=1\n"   # reading is fine
    os.close(os.open(live / ".voicemode" / "voicemode.env", os.O_RDONLY))
    (live / "repo" / "built.pyc").write_bytes(b"")                            # the checkout is exempt
    (live / "repo" / "cache").mkdir()
    (tmp_path / "elsewhere.txt").write_text("fine")                            # tmp is not the home
    assert home_guard.hits == []


def test_the_refusal_is_not_an_oserror(live):
    """Product code catches OSError around its file writes; the refusal must get past that."""
    assert not issubclass(LiveHomeWrite, OSError)
    with pytest.raises(LiveHomeWrite):
        try:
            (live / "x").write_text("y")
        except OSError:
            pass


def test_a_write_the_test_swallowed_still_fails_it(tmp_path):
    """The guard as a plugin in a child pytest: the test catches the refusal, and fails at teardown."""
    child = tmp_path / "child"
    child.mkdir()
    (child / "test_swallow.py").write_text(
        "from tests import home_guard\n"
        "def test_swallows(tmp_path, monkeypatch):\n"
        "    monkeypatch.setattr(home_guard, 'LIVE_HOME', str(tmp_path.resolve()))\n"
        "    try:\n"
        "        (tmp_path / 'voicemode.env').write_text('clobbered')\n"
        "    except Exception:\n"
        "        pass\n"
        "def test_clean(tmp_path):\n"
        "    (tmp_path / 'fine').write_text('ok')\n")
    env = {**os.environ, "HOME": str(tmp_path / "home"), "PYTHONPATH": str(ROOT)}
    out = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                          "-p", "tests.home_guard", str(child)],
                         cwd=child, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 1, out.stdout + out.stderr
    assert "2 passed, 1 error" in out.stdout, out.stdout
    assert "ERROR at teardown of test_swallows" in out.stdout and "wrote under the live HOME" in out.stdout
    assert not (tmp_path / "voicemode.env").exists()


def test_the_interpreters_own_trees_are_exempt_and_the_rest_of_home_is_not(live, monkeypatch):
    """A uv CPython lives under the home; its first run writes the stdlib's .pyc there (EARS-327)."""
    base = os.path.realpath(sys.base_prefix)
    assert base in home_guard.PYTHON and os.path.realpath(sys.prefix) in home_guard.PYTHON
    # The real interpreter, judged as if the live home held it: its tree passes, its neighbours do not.
    monkeypatch.setattr(home_guard, "LIVE_HOME", os.path.dirname(base))
    assert not home_guard.refused(os.path.join(base, "lib", "__pycache__", "os.cpython.pyc"))
    assert home_guard.refused(os.path.join(os.path.dirname(base), "not-the-interpreter", "x"))
    assert home_guard.refused(base + "-sibling")                   # a prefix match is not a tree match
    # And a real write, in the stand-in home: under the interpreter it lands, elsewhere it is refused.
    py = live / ".local" / "share" / "uv" / "python" / "cpython"
    py.mkdir(parents=True)                       # the home is still the real interpreter's parent here
    monkeypatch.setattr(home_guard, "LIVE_HOME", str(live.resolve()))
    monkeypatch.setattr(home_guard, "PYTHON", (str(py.resolve()),))
    (py / "__pycache__").mkdir()
    (py / "__pycache__" / "os.cpython.pyc").write_bytes(b"")
    assert home_guard.hits == []
    with pytest.raises(LiveHomeWrite):
        (live / ".voicemode" / "voicemode.env").write_text("clobbered\n")
    assert (live / ".voicemode" / "voicemode.env").read_text() == "KEEP=1\n"
    assert len(home_guard.hits) == 1
