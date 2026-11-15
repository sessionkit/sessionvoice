"""The code stamp (ears.codestamp): one function the capture and the keeper both call.

A keeper restart reloads the keeper but not the room capture, a separate process. The capture
writes this stamp on its heartbeats, and the keeper compares it with the stamp of the code it
would start. These hold the stamp to its rules: the same source gives the same stamp (from a
checkout and from a wheel's copied files alike), any source change moves it, package data and
bytecode do not, and the keeper's way of asking (``$PY -m ears.codestamp`` from ``$W``) answers
what the capture's process computed. Every tree here is a copy under tmp_path.
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from ears import codestamp

ROOT = Path(__file__).resolve().parents[1]
KEEPER = ROOT / "keeper" / "ears-keeper.sh"


def _tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


SRC = {"__init__.py": "x = 1\n", "listen/loop.py": "def beat(): pass\n", "heard.py": "EV = 'heartbeat'\n"}


def test_the_stamp_is_the_version_and_a_short_source_hash():
    s = codestamp.stamp()
    assert re.fullmatch(r"[^\s+]+\+[0-9a-f]{12}", s), s
    assert s.split("+")[0] == codestamp._version()
    assert s == codestamp.compute()      # the package's own source


def test_the_same_source_gives_the_same_stamp(tmp_path):
    a = _tree(tmp_path / "a", SRC)
    b = _tree(tmp_path / "b", dict(reversed(list(SRC.items()))))   # written in another order
    assert codestamp.compute(a, "1.0") == codestamp.compute(b, "1.0")


def test_any_source_change_moves_the_stamp(tmp_path):
    base = codestamp.compute(_tree(tmp_path / "a", SRC), "1.0")
    edited = codestamp.compute(_tree(tmp_path / "b", SRC | {"listen/loop.py": "def beat(): return 1\n"}), "1.0")
    added = codestamp.compute(_tree(tmp_path / "c", SRC | {"listen/watchdog.py": "WEDGE_S = 90\n"}), "1.0")
    moved = codestamp.compute(_tree(tmp_path / "d", {("x" + k if k == "heard.py" else k): v
                                                     for k, v in SRC.items()}), "1.0")
    assert len({base, edited, added, moved}) == 4
    assert codestamp.compute(_tree(tmp_path / "e", SRC), "1.1") != base   # and the version


def test_package_data_and_bytecode_are_not_the_code(tmp_path):
    base = codestamp.compute(_tree(tmp_path / "a", SRC), "1.0")
    extra = _tree(tmp_path / "b", SRC | {"_keeper/ears-mail.py": "print(1)\n", "_speakers/side.py": "y\n",
                                         "__pycache__/loop.cpython-312.py": "z\n", "notes.txt": "n\n"})
    assert codestamp.compute(extra, "1.0") == base


def test_a_checkout_and_a_wheel_of_the_same_source_agree(tmp_path):
    """The checkout's ears/_keeper holds symlinks into keeper/; the wheel holds copies. Neither
    is code, and every .py is read by its bytes, so the two layouts give one stamp."""
    pkg = ROOT / "src" / "ears"
    checkout = tmp_path / "checkout"
    shutil.copytree(pkg, checkout, symlinks=True, ignore=shutil.ignore_patterns("__pycache__"))
    wheel = tmp_path / "wheel"
    shutil.copytree(pkg, wheel, symlinks=False, ignore=shutil.ignore_patterns("__pycache__"))
    assert any(p.is_symlink() for p in (checkout / "_keeper").iterdir())
    assert not any(p.is_symlink() for p in wheel.rglob("*"))
    assert codestamp.compute(checkout, "1.0") == codestamp.compute(wheel, "1.0") == codestamp.compute(pkg, "1.0")


def test_with_no_source_it_is_the_version_alone(tmp_path):
    assert codestamp.compute(tmp_path, "1.0") == "1.0"


def test_it_is_computed_once_per_process(monkeypatch):
    """A capture's stamp is the code it loaded at its start, not what a later deploy put on disk."""
    first = codestamp.stamp()
    monkeypatch.setattr(codestamp, "compute", lambda *a, **k: "9.9+ffffffffffff")
    assert codestamp.stamp() == first


def test_a_failure_is_no_stamp_not_a_crash(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("unreadable")
    monkeypatch.setattr(codestamp, "compute", boom)
    codestamp.stamp.cache_clear()
    try:
        assert codestamp.stamp() == ""
        assert codestamp.main() == 1
    finally:
        codestamp.stamp.cache_clear()


def test_the_command_prints_the_stamp():
    out = subprocess.run([sys.executable, "-m", "ears.codestamp"], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and out.stdout.strip() == codestamp.stamp()


def test_the_keeper_asks_the_same_package_the_capture_runs():
    """The keeper's code_stamp, as it ships (the same $PY and $W as launch_capture), answers what
    the capture computes in its own process: they agree by construction."""
    s = KEEPER.read_text()
    fn = next(l for l in s.splitlines() if l.startswith("code_stamp() {"))
    assert '"${W:?}"' in fn and '"${PY:?}" -m ears.codestamp' in fn
    launch = s[s.index("launch_capture() {"):s.index("# --- end launch ---")]
    assert '( cd "$W" && exec nohup "$PY" -m "$MOD" capture' in launch
    out = subprocess.run(["bash", "-c", f"set -uo pipefail\n{fn}\ncode_stamp"], capture_output=True, text=True,
                         env=dict(os.environ, W=str(ROOT), PY=sys.executable), timeout=60)
    assert out.returncode == 0 and out.stdout.strip() == codestamp.stamp()
    # and with no python to ask, it is empty (cannot tell), never an error
    none = subprocess.run(["bash", "-c", f"set -uo pipefail\n{fn}\ncode_stamp; echo rc=$?"], capture_output=True,
                          text=True, env=dict(os.environ, W=str(ROOT), PY="/nonexistent/python"), timeout=60)
    assert none.stdout.startswith("rc=") and none.stdout.strip() != "rc=0"
