"""Tests for talk.token_store, the per-install token store (EARS-338, slice S1).

Run: make test ARGS=tests/test_talk_token.py

Every test runs in a temp HOME and VOICEMODE_BASE_DIR (the ``vm`` fixture), so
nothing here reads or writes ~/.voicemode. A foreign owner is faked by wrapping
os.fstat; no second account is needed. No test prints a token, and the ones that
provoke a refusal check that its message names the path and never the token.
"""
import os, stat, subprocess, sys, threading, types
from pathlib import Path
from unittest import mock

import pytest

from talk import token_store as tk


@pytest.fixture
def vm(tmp_path, monkeypatch):
    """A fenced HOME and VOICEMODE_BASE_DIR; the run-time cache starts empty."""
    home, base = tmp_path / "home", tmp_path / "vm"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(base))
    monkeypatch.setattr(tk, "_cache", {"key": None, "token": None})
    return base


def mode(p):
    return stat.S_IMODE(os.lstat(p).st_mode)


def refused(match_path, secret=None):
    """Assert a TokenError whose message names ``match_path`` and never ``secret``."""
    class Check:
        def __enter__(self):
            self.cm = pytest.raises(tk.TokenError)
            self.info = self.cm.__enter__()
            return self.info

        def __exit__(self, *exc):
            out = self.cm.__exit__(*exc)
            msg = str(self.info.value)
            assert str(match_path) in msg, msg
            if secret:
                assert secret not in msg
            return out
    return Check()


def write_token(p, text, m=0o600):
    p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(p.parent, 0o700)
    p.write_text(text)
    os.chmod(p, m)


def foreign_fstat(only_regular):
    """os.fstat, but owned by another uid: every fd, or (only_regular) only a regular file's."""
    real = os.fstat

    def fake(fd):
        st = real(fd)
        if only_regular and not stat.S_ISREG(st.st_mode):
            return st
        return types.SimpleNamespace(st_mode=st.st_mode, st_uid=os.geteuid() + 1, st_ino=st.st_ino)
    return mock.patch.object(tk.os, "fstat", fake)


# -- where it lives, and what it holds

def test_path_is_under_voicemode_base_dir_read_at_call_time(vm, tmp_path, monkeypatch):
    assert tk.path() == vm / "talk" / "token"
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "other"))
    assert tk.path() == tmp_path / "other" / "talk" / "token"


def test_creates_a_43_char_token_0600_in_a_0700_dir(vm):
    t = tk.load_or_create()
    assert len(t) == 43 and tk.TOKEN.fullmatch(t)
    p = tk.path()
    assert p.read_text() == t + "\n"
    assert mode(p) == 0o600 and stat.S_ISREG(os.lstat(p).st_mode)
    assert mode(p.parent) == 0o700
    assert os.lstat(p).st_uid == os.geteuid()
    assert sorted(os.listdir(p.parent)) == ["token"]      # no temporary file left behind


def test_second_call_returns_the_same_token(vm):
    t = tk.load_or_create()
    assert tk.load_or_create() == t
    assert tk.load() == t
    assert sorted(os.listdir(tk.path().parent)) == ["token"]


def test_modes_are_exact_whatever_the_umask(vm):
    old = os.umask(0)
    try:
        tk.load_or_create()
    finally:
        os.umask(old)
    assert mode(tk.path()) == 0o600 and mode(tk.path().parent) == 0o700


def test_each_install_gets_its_own_token(vm, tmp_path, monkeypatch):
    a = tk.load_or_create()
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm2"))
    assert tk.load_or_create() != a


def test_load_never_creates(vm):
    with pytest.raises(FileNotFoundError):
        tk.load()
    assert not tk.path().exists()


# -- refusals: each names the path, never the token, and changes nothing

def test_a_0644_file_is_refused_and_not_tightened(vm):
    t = tk.load_or_create()
    os.chmod(tk.path(), 0o644)
    for call in (tk.load_or_create, tk.load):
        with refused(tk.path(), t) as e:
            call()
        assert "0644" in str(e.value) and "chmod 600" in str(e.value)
    assert mode(tk.path()) == 0o644                      # refused, never silently tightened
    assert tk.path().read_text() == t + "\n"


@pytest.mark.parametrize("m", [0o640, 0o604, 0o660, 0o700])
def test_any_group_or_other_bit_on_the_file_is_refused(vm, m):
    t = tk.load_or_create()
    os.chmod(tk.path(), m)
    if m & 0o077:
        with refused(tk.path(), t):
            tk.load()
    else:                                                # 0700: no group or other bit, so allowed
        assert tk.load() == t


def test_a_0755_dir_is_refused(vm):
    t = tk.load_or_create()
    os.chmod(tk.path().parent, 0o755)
    for call in (tk.load_or_create, tk.load, tk.rotate):
        with refused(tk.path().parent, t) as e:
            call()
        assert "0755" in str(e.value) and "chmod 700" in str(e.value)
    assert mode(tk.path().parent) == 0o755
    assert tk.path().read_text() == t + "\n"             # rotate refused too: nothing was written


def test_a_symlinked_token_file_is_refused(vm, tmp_path):
    real = tmp_path / "elsewhere" / "token"
    write_token(real, "A" * 43 + "\n")
    tk.path().parent.mkdir(mode=0o700, parents=True)
    os.symlink(real, tk.path())
    for call in (tk.load_or_create, tk.load):
        with refused(tk.path(), "A" * 43) as e:
            call()
        assert "symlink" in str(e.value)
    assert os.path.islink(tk.path())                     # left exactly as found


def test_a_symlinked_token_dir_is_refused(vm, tmp_path):
    real = tmp_path / "elsewhere"
    real.mkdir(mode=0o700)
    vm.mkdir()
    os.symlink(real, vm / "talk")
    for call in (tk.load_or_create, tk.load, tk.rotate):
        with refused(vm / "talk"):
            call()
    assert os.listdir(real) == []


def test_a_token_that_is_not_a_regular_file_is_refused(vm):
    tk.path().parent.mkdir(mode=0o700, parents=True)
    os.mkdir(tk.path(), 0o700)
    with refused(tk.path()) as e:
        tk.load_or_create()
    assert "not a regular file" in str(e.value)
    os.rmdir(tk.path())
    os.mkfifo(tk.path(), 0o600)                          # never blocks on a FIFO
    with refused(tk.path()):
        tk.load()


def test_a_foreign_owned_file_is_refused(vm):
    t = tk.load_or_create()
    with foreign_fstat(only_regular=True):
        with refused(tk.path(), t) as e:
            tk.load()
    assert f"uid {os.geteuid() + 1}" in str(e.value)


def test_a_foreign_owned_dir_is_refused(vm):
    t = tk.load_or_create()
    with foreign_fstat(only_regular=False):
        for call in (tk.load_or_create, tk.load, tk.rotate):
            with refused(tk.path().parent, t):
                call()
    assert tk.path().read_text() == t + "\n"


@pytest.mark.parametrize("content", [
    "", "\n", "SHORTSECRETxyz\n", "B" * 42 + "\n",           # too short
    "C" * 40 + "+/=\n",                                      # not urlsafe
    "D" * 43 + "\n\n", " " + "E" * 43 + "\n",               # stray whitespace
    "F" * 5000,                                              # far too long to be a token
])
def test_content_that_is_not_a_token_is_refused(vm, content):
    write_token(tk.path(), content)
    with refused(tk.path(), content.strip()[:20] or None) as e:
        tk.load_or_create()
    assert "talk token --rotate" in str(e.value)
    assert tk.path().read_text() == content               # left for the user to rotate


def test_a_longer_urlsafe_token_is_accepted(vm):
    write_token(tk.path(), "G" * 64 + "\n")
    assert tk.load() == "G" * 64
    write_token(tk.path(), "H" * 43)                       # the trailing newline is optional
    assert tk.load() == "H" * 43


def test_refusals_print_nothing(vm, capsys):
    t = tk.load_or_create()
    os.chmod(tk.path(), 0o644)
    with pytest.raises(tk.TokenError):
        tk.load()
    assert tk.matches(t) is False
    tk.rotate()
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


def test_token_error_is_a_value_error():
    assert issubclass(tk.TokenError, ValueError)          # talk's start-up refusals are ValueErrors


# -- racing creators agree, and no one reads a partial file (the link step)

def test_a_creator_mid_write_is_never_read_partially(vm, monkeypatch):
    """The first creator writes its temporary file one byte at a time. Between bytes, a
    second creator runs to completion: it never sees a partial token (only whole files are
    linked to ``token``), it wins the link, and the first then reads the winner's token."""
    real_write = os.write
    seen, second, started = [], [], []

    def slow_write(fd, data):
        n = real_write(fd, data[:1])
        if os.path.exists(tk.path()):
            seen.append(tk.path().read_text())
        if not started:                                   # the second creator runs once, mid-write
            started.append(True)
            second.append(tk.load_or_create())
        return n

    monkeypatch.setattr(tk.os, "write", slow_write)
    first = tk.load_or_create()
    assert second and first == second[0]
    assert all(s == first + "\n" for s in seen), "a partial token was visible"
    assert tk.path().read_text() == first + "\n"
    assert sorted(os.listdir(tk.path().parent)) == ["token"]


def test_threads_racing_agree(vm):
    n = 16
    gate = threading.Barrier(n)
    got, errors = [], []

    def run():
        try:
            gate.wait()
            got.append(tk.load_or_create())
        except Exception as e:                            # pragma: no cover - reported below
            errors.append(e)

    threads = [threading.Thread(target=run) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not errors and len(got) == n
    assert len(set(got)) == 1 and got[0] == tk.load()
    assert sorted(os.listdir(tk.path().parent)) == ["token"]


def test_processes_racing_agree(vm, tmp_path):
    """Separate processes, started together: one wins, all agree. Each prints a digest of the
    token, never the token. The children carry the fenced HOME and base dir in their env."""
    go = tmp_path / "go"
    code = (
        "import hashlib, os, sys, time\n"
        "from talk import token_store as token\n"
        f"while not os.path.exists({str(go)!r}): time.sleep(0.005)\n"
        "print(hashlib.sha256(token.load_or_create().encode()).hexdigest())\n"
    )
    env = dict(os.environ)
    procs = [subprocess.Popen([sys.executable, "-c", code], env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) for _ in range(6)]
    go.touch()
    outs = [p.communicate(timeout=60) for p in procs]
    assert all(p.returncode == 0 for p in procs), [o[1] for o in outs]
    digests = {o[0].strip() for o in outs}
    assert len(digests) == 1
    import hashlib
    assert digests == {hashlib.sha256(tk.load().encode()).hexdigest()}
    assert sorted(os.listdir(tk.path().parent)) == ["token"]


def test_a_failed_link_leaves_no_temporary_file(vm, monkeypatch):
    def boom(src, dst):
        raise OSError(5, "simulated")
    monkeypatch.setattr(tk.os, "link", boom)
    with pytest.raises(OSError):
        tk.load_or_create()
    assert os.listdir(tk.path().parent) == []


# -- rotate

def test_after_rotate_the_old_token_no_longer_matches(vm):
    old = tk.load_or_create()
    assert tk.matches(old)
    p = tk.rotate()
    assert p == tk.path()
    new = tk.load()
    assert new != old and len(new) == 43
    assert not tk.matches(old)
    assert tk.matches(new)
    assert mode(p) == 0o600 and mode(p.parent) == 0o700
    assert sorted(os.listdir(p.parent)) == ["token"]


def test_rotate_creates_a_missing_token(vm):
    p = tk.rotate()
    assert len(tk.load()) == 43 and mode(p) == 0o600 and mode(p.parent) == 0o700


def test_rotate_replaces_a_wide_or_bad_file_with_a_good_one(vm):
    t = tk.load_or_create()
    os.chmod(tk.path(), 0o644)
    tk.rotate()                                           # the remedy every refusal names
    assert mode(tk.path()) == 0o600 and tk.load() != t
    write_token(tk.path(), "short")
    tk.rotate()
    assert len(tk.load()) == 43


def test_rotate_replaces_a_symlink_never_writes_through_it(vm, tmp_path):
    target = tmp_path / "elsewhere" / "token"
    write_token(target, "I" * 43 + "\n")
    tk.path().parent.mkdir(mode=0o700, parents=True)
    os.symlink(target, tk.path())
    tk.rotate()
    assert not os.path.islink(tk.path()) and len(tk.load()) == 43
    assert target.read_text() == "I" * 43 + "\n"


# -- matches and the stat cache

def test_matches_only_the_token(vm):
    t = tk.load_or_create()
    assert tk.matches(t)
    for bad in (None, "", t[:-1], t + "x", t.upper() if t.upper() != t else t + "y", "é" * 43, 42, b"x"):
        assert tk.matches(bad) is False


def test_a_missing_file_matches_nothing_and_is_not_recreated(vm):
    t = tk.load_or_create()
    assert tk.matches(t)
    os.unlink(tk.path())
    assert tk.matches(t) is False
    assert not tk.path().exists()                         # deleting it never lets anyone in
    os.rmdir(tk.path().parent)
    assert tk.matches(t) is False
    assert not tk.path().parent.exists()


def test_a_mode_widened_at_run_time_matches_nothing(vm):
    t = tk.load_or_create()
    assert tk.matches(t)
    os.chmod(tk.path(), 0o644)
    assert tk.matches(t) is False
    os.chmod(tk.path(), 0o600)
    assert tk.matches(t)


def test_a_dir_widened_at_run_time_matches_nothing(vm):
    t = tk.load_or_create()
    assert tk.matches(t)
    os.chmod(tk.path().parent, 0o755)
    assert tk.matches(t) is False


def test_the_file_is_read_again_only_when_it_changes(vm, monkeypatch):
    t = tk.load_or_create()
    reads = []
    real = tk._read
    monkeypatch.setattr(tk, "_read", lambda p: reads.append(p) or real(p))
    for _ in range(5):
        assert tk.matches(t)
    assert len(reads) == 1                                # one read, then stat only
    tk.rotate()
    assert not tk.matches(t)
    assert tk.matches(tk.load())
    assert len(reads) == 3                                # the rotation read once more (+ load())


def test_rotation_by_another_process_is_seen_with_no_restart(vm):
    old = tk.load_or_create()
    assert tk.matches(old)
    subprocess.run([sys.executable, "-c", "from talk import token_store; token_store.rotate()"],
                   env=dict(os.environ), check=True, capture_output=True)
    assert not tk.matches(old)
    assert tk.matches(tk.load())


# -- no module in src/talk shadows the stdlib (why this file is not talk/token.py)

def test_no_talk_module_shadows_a_stdlib_module():
    """`python3 src/talk/server.py` puts src/talk first on sys.path. A talk/token.py there
    hid the stdlib `token`, and the server died on import (dataclasses -> inspect -> tokenize
    -> token). So no file in src/talk may take a stdlib module's name."""
    here = Path(tk.__file__).parent
    names = {p.stem for p in here.glob("*.py")} - {"__init__", "__main__"}
    assert not names & set(sys.stdlib_module_names)


def test_server_imports_as_a_script(tmp_path):
    code = "import sys; sys.path.insert(0, sys.argv[1]); import server; print('ok')"
    r = subprocess.run([sys.executable, "-c", code, str(Path(tk.__file__).parent)],
                       capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, HOME=str(tmp_path)))
    assert r.returncode == 0 and r.stdout.strip() == "ok", r.stderr[-800:]


# -- the module stays stdlib only (talk's rule), plus ears.heard

def test_stdlib_only():
    src = Path(tk.__file__).read_text()
    imports = [ln for ln in src.splitlines() if ln.startswith(("import ", "from "))]
    assert imports == [
        "import errno, hmac, os, re, secrets, stat, threading",
        "from pathlib import Path",
        "from ears import heard",
    ]
