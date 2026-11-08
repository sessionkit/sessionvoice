"""The mail fence holds (EARS-506): no test reaches the live mail index or runs a child with
the live HOME. tests/mail_fence.py says what it does and why.

Every probe here is hermetic: :func:`fence_tmp` moves the fence's idea of "the temp dir" to a
dir inside this test's tmp_path, so a "real" program outside it is a fake this test wrote,
which leaves a mark if it ever runs. Nothing here runs, or looks for, the host's own notmuch.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests import child_pytest, env_fence, mail_fence
from tests.mail_fence import RealMailIndex, expect_refusal

ROOT = Path(__file__).resolve().parent.parent


def _script(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(0o755)
    return path


@pytest.fixture
def fence_tmp(tmp_path, monkeypatch):
    """The fence, with "the temp dir" moved to ``tmp_path/tmp`` and its own refusing bin there.
    Returns (tmp, outside, marker): a program under ``outside`` stands in for a real one, and
    every fake there touches ``marker`` if it ever runs."""
    tmp, outside = tmp_path / "tmp", tmp_path / "outside"
    tmp.mkdir()
    outside.mkdir()
    marker = tmp_path / "ran"
    for name in mail_fence.REFUSED:
        _script(outside / name, f"touch {marker}")
    monkeypatch.setattr(mail_fence, "TMP", str(tmp.resolve()))
    monkeypatch.setattr(mail_fence, "_leak_cache", {})
    monkeypatch.setitem(mail_fence._session, "dir", str(tmp / "fence"))
    monkeypatch.setitem(mail_fence._session, "bins", {})
    monkeypatch.setenv("PATH", mail_fence.fenced_path(os.environ["PATH"]))   # the session's bin out, ours in
    return tmp, outside, marker


# -- the session's fence ------------------------------------------------------------------------

def test_the_fence_is_on_for_every_test():
    assert mail_fence.on()
    assert os.path.realpath(os.environ["HOME"]) != mail_fence.LIVE_HOME
    assert mail_fence.under_tmp(os.environ["HOME"])
    assert mail_fence.leaks(os.environ["PATH"]) == []


def test_the_mail_env_is_pinned_under_a_path_that_never_exists():
    for name in env_fence.MAIL_FENCED:
        want = env_fence.FENCE[name]
        assert os.environ.get(name) == want, name
        if want is not None:
            assert want.startswith(env_fence.MAIL_FENCE_ROOT + "/")
    assert not os.path.exists(env_fence.MAIL_FENCE_ROOT)
    others = [n for n in os.environ if n.startswith(mail_fence.PREFIXES) and n not in mail_fence.PINNED]
    assert others == [], "a runner's sessionmail variable survived the fence"


_WHICH = ("import shutil, os\n"
          "print(os.environ['HOME'])\n"
          "for n in ('notmuch', 'message', 'memory', 'pager', 'sendmail'):\n"
          "    print(shutil.which(n))\n")


def test_a_python_child_finds_the_refusers_first_on_path(fence_tmp):
    tmp, outside, marker = fence_tmp
    env = {**os.environ, "PATH": f"{outside}{os.pathsep}/usr/bin{os.pathsep}/bin"}
    home, *found = subprocess.run([sys.executable, "-c", _WHICH], env=env, capture_output=True,
                                  text=True, timeout=60, check=True).stdout.splitlines()
    assert home == os.environ["HOME"]
    every = mail_fence.bin_for(mail_fence.REFUSED)
    assert found == [os.path.join(every, n) for n in mail_fence.REFUSED]


def test_the_sessions_own_children_find_no_real_mail_program():
    found = subprocess.run([sys.executable, "-c", _WHICH], capture_output=True, text=True,
                           timeout=60, check=True).stdout.splitlines()[1:]
    assert all(f == "None" or mail_fence.under_tmp(f) for f in found), found


def test_a_name_the_path_would_not_find_stays_unfound(fence_tmp):
    """A launchd PATH holds /usr/sbin/sendmail and no message: the child's sendmail is
    refused, and its message is still missing, so a test of the HOME fallback still holds."""
    tmp, outside, marker = fence_tmp
    only = tmp.parent / "only-sendmail"
    _script(only / "sendmail", f"touch {marker}")
    env = {**os.environ, "PATH": f"{only}{os.pathsep}/nonexistent-bin"}
    found = subprocess.run([sys.executable, "-c", _WHICH], env=env, capture_output=True, text=True,
                           timeout=60, check=True).stdout.splitlines()[1:]
    assert found == ["None"] * 4 + [os.path.join(mail_fence.bin_for(["sendmail"]), "sendmail")]


# -- the audit hook: the program ----------------------------------------------------------------

@pytest.mark.parametrize("name", mail_fence.REFUSED)
def test_a_real_mail_program_is_refused_before_it_starts(name, fence_tmp):
    tmp, outside, marker = fence_tmp
    with expect_refusal() as got:
        with pytest.raises(RealMailIndex, match=f"runs the real {name}"):
            subprocess.run([str(outside / name), "new"], timeout=30)
    assert not marker.exists(), "the program ran"
    assert [(r["kind"], r["where"]) for r in got] == [("refused", "pytest")]
    assert got[0]["test"].startswith(os.environ["PYTEST_CURRENT_TEST"].split(" (")[0])


def test_a_real_one_found_on_a_tests_own_path_is_refused(fence_tmp):
    tmp, outside, marker = fence_tmp
    env = {**os.environ, "PATH": f"{outside}{os.pathsep}/usr/bin{os.pathsep}/bin"}
    with expect_refusal(), pytest.raises(RealMailIndex):
        subprocess.run(["notmuch", "new"], env=env, timeout=30)
    assert not marker.exists()


def test_a_symlink_into_tmp_that_lands_outside_is_refused(fence_tmp):
    """The realpath decides, not the name a test gave it."""
    tmp, outside, marker = fence_tmp
    link = tmp / "links" / "notmuch"
    link.parent.mkdir()
    link.symlink_to(outside / "notmuch")
    with expect_refusal(), pytest.raises(RealMailIndex):
        subprocess.run([str(link), "new"], timeout=30)
    assert not marker.exists()


def test_a_grandchild_on_a_tmp_symlink_to_a_real_one_meets_the_refuser(fence_tmp):
    """A PATH dir in tmp whose notmuch links to a real one is no test's fake: the refuser goes
    before it, so a shell that looks notmuch up never runs the real one. Its own fake beside
    the link still wins."""
    tmp, outside, marker = fence_tmp
    links = tmp / "links"
    links.mkdir()
    (links / "notmuch").symlink_to(outside / "notmuch")
    mine = tmp / "my-mark"
    _script(links / "memory", f"touch {mine}")
    env = {**os.environ, "PATH": f"{links}{os.pathsep}/usr/bin{os.pathsep}/bin"}
    with expect_refusal() as got:
        out = subprocess.run(["sh", "-c", "notmuch new </dev/null; echo $?; memory ls"], env=env,
                             capture_output=True, text=True, timeout=60)
    assert out.stdout.split()[0] == str(mail_fence.EXIT) and not marker.exists(), out.stdout + out.stderr
    assert mine.exists(), "the test's own fake beside the link did not run"
    assert [r["kind"] for r in got] == ["fenced", "refused"]
    assert (got[1]["where"], got[1]["prog"]) == ("child", "notmuch")


def test_a_fake_under_tmp_runs(fence_tmp):
    tmp, outside, marker = fence_tmp
    mine = tmp / "my-mark"
    fake = _script(tmp / "fakes" / "notmuch", f"touch {mine}")
    env = {**os.environ, "PATH": f"{fake.parent}{os.pathsep}{os.environ['PATH']}"}
    subprocess.run(["notmuch", "new"], env=env, timeout=30, check=True)
    assert mine.exists() and not marker.exists()


def test_the_refusal_is_not_an_oserror():
    assert not issubclass(RealMailIndex, OSError)


def test_posix_spawn_is_refused(fence_tmp):
    tmp, outside, marker = fence_tmp
    with expect_refusal(), pytest.raises(RealMailIndex):
        os.posix_spawn(str(outside / "message"), ["message", "send"], dict(os.environ))
    assert not marker.exists()


@pytest.mark.parametrize("event,args", [
    ("os.exec", lambda o: (str(o / "notmuch"), ["notmuch", "new"], None)),
    ("os.spawn", lambda o: (os.P_WAIT, str(o / "pager"), ["pager", "listen"], None)),
])
def test_exec_and_spawn_are_judged_alike(event, args, fence_tmp):
    """Judged directly: an un-refused exec would replace this pytest."""
    tmp, outside, marker = fence_tmp
    with expect_refusal(), pytest.raises(RealMailIndex):
        mail_fence.judge(event, args(outside))


def test_a_refusal_the_code_swallowed_is_still_recorded(fence_tmp):
    tmp, outside, marker = fence_tmp
    with expect_refusal() as got:
        try:
            subprocess.run([str(outside / "notmuch"), "new"], timeout=30)
        except Exception:  # noqa: BLE001 - the code under test's broad except
            pass
    assert [r["kind"] for r in got] == ["refused"]


# -- the audit hook: the env a child gets -----------------------------------------------------------

def _home_of(env) -> str:
    return subprocess.run([sys.executable, "-c", "import os; print(os.environ.get('HOME'))"], env=env,
                          capture_output=True, text=True, timeout=60, check=True).stdout.strip()


@pytest.mark.parametrize("home", ["live", "live-trailing-slash", "unset"])
def test_a_child_given_the_live_home_or_none_is_refused_before_it_starts(home, tmp_path):
    """env= names the live HOME (any spelling of it), or none: Python, bash and notmuch then
    take the account's home from the password database. Refused, loud, naming the test."""
    env = {k: v for k, v in os.environ.items() if k != "HOME"}
    if home != "unset":
        env["HOME"] = mail_fence.LIVE_HOME + ("/" if home.endswith("slash") else "")
    mark = tmp_path / "ran"
    with expect_refusal() as got, pytest.raises(RealMailIndex, match="HOME is") as err:
        subprocess.run([sys.executable, "-c", f"open({str(mark)!r}, 'w')"], env=env, timeout=60)
    assert not mark.exists(), "the child ran"
    assert os.environ["PYTEST_CURRENT_TEST"].split(" (")[0] in str(err.value)
    assert [(r["kind"], r["event"]) for r in got] == [("refused", "subprocess.Popen")]
    assert got[0]["argv"][:2] == [sys.executable, "-c"]


def test_a_bytes_env_with_the_live_home_is_refused():
    env = {os.fsencode(k): os.fsencode(v) for k, v in os.environ.items()}
    env[b"HOME"] = os.fsencode(mail_fence.LIVE_HOME)
    with expect_refusal(), pytest.raises(RealMailIndex):
        _home_of(env)


def test_a_child_inheriting_the_sessions_home_runs():
    assert _home_of(None) == mail_fence._session["home"]


def test_a_tests_own_home_in_tmp_is_kept(tmp_path):
    env = {**os.environ, "HOME": str(tmp_path)}
    assert _home_of(env) == str(tmp_path)


def test_a_child_on_a_path_without_the_bin_gets_it_before_the_real_one(fence_tmp):
    tmp, outside, marker = fence_tmp
    mine = _script(tmp / "fakes" / "memory", "exit 0").parent
    env = {**os.environ, "PATH": os.pathsep.join([str(mine), str(outside), "/usr/bin", "/bin"])}
    code = "import shutil; print(shutil.which('memory')); print(shutil.which('notmuch'))"
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                         timeout=60, check=True).stdout.split()
    assert out == [str(mine / "memory"),
                   os.path.join(mail_fence.bin_for(["notmuch", "message", "pager", "sendmail"]), "notmuch")]


@pytest.mark.parametrize("name", ["NOTMUCH_CONFIG", "NOTMUCH_DATABASE", "SESSIONMAIL_ROOT", "MAIL_ROOT"])
def test_a_childs_mail_variable_outside_tmp_gets_the_pin_back(name, tmp_path):
    env = {**os.environ, name: "/somewhere/live/.mail"}
    code = f"import os; print(os.environ.get({name!r}))"
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                         timeout=60, check=True).stdout.strip()
    assert out == env_fence.FENCE[name]
    env[name] = str(tmp_path / "mine")                 # a test's own, in tmp: kept
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                         timeout=60, check=True).stdout.strip()
    assert out == str(tmp_path / "mine")


def test_a_popen_child_inheriting_a_launchd_path_gets_a_fixed_copy(monkeypatch, fence_tmp):
    """A test that set a launchd PATH (a real sendmail on it) in os.environ: a Popen child gets
    a fenced copy, and os.environ is left as the test set it."""
    tmp, outside, marker = fence_tmp
    monkeypatch.setenv("PATH", f"{outside}{os.pathsep}/usr/bin{os.pathsep}/bin")
    before = dict(os.environ)
    code = "import os, shutil; print(os.environ['HOME']); print(shutil.which('notmuch'))"
    with expect_refusal() as got:
        home, notmuch = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                                       timeout=60, check=True).stdout.split()
    assert home == before["HOME"]
    assert notmuch == os.path.join(mail_fence.bin_for(mail_fence.REFUSED), "notmuch")
    assert [r["kind"] for r in got] == ["fenced"]
    assert dict(os.environ) == before


@pytest.mark.parametrize("home", ["live", "unset"])
def test_a_child_inheriting_the_live_home_from_os_environ_is_refused(home, monkeypatch):
    """A test that put the live HOME back in os.environ (or removed HOME): every way it can
    start a child is refused."""
    if home == "live":
        monkeypatch.setenv("HOME", mail_fence.LIVE_HOME)
    else:
        monkeypatch.delenv("HOME")
    with expect_refusal() as got:
        with pytest.raises(RealMailIndex):
            subprocess.run(["true"], timeout=60)
        with pytest.raises(RealMailIndex):
            os.system("true")
        with pytest.raises(RealMailIndex):
            os.posix_spawn("/usr/bin/true", ["true"], dict(os.environ))
    assert [r["event"] for r in got] == ["subprocess.Popen", "os.system", "os.posix_spawn"]


@pytest.mark.parametrize("change", ["home", "path"])
def test_a_shell_or_spawn_inheriting_a_changed_os_environ_is_refused(change, monkeypatch, fence_tmp):
    """os.system and os.posix_spawn hand the child an env this fence cannot fix: refused, by name."""
    tmp, outside, marker = fence_tmp
    if change == "home":
        monkeypatch.setenv("HOME", mail_fence.LIVE_HOME)
    else:
        monkeypatch.setenv("PATH", f"{outside}{os.pathsep}/usr/bin{os.pathsep}/bin")
    with expect_refusal() as got, pytest.raises(RealMailIndex):
        os.system("true")
    with expect_refusal(), pytest.raises(RealMailIndex):
        os.posix_spawn("/usr/bin/true", ["true"], dict(os.environ))
    assert got and got[0]["kind"] == "refused"


def test_popen_keeps_the_audio_fences_mark():
    assert subprocess.Popen.__init__.mail_fence and subprocess.Popen.__init__.audio_fence


def test_the_live_home_fallback_cannot_reach_a_real_message(tmp_path, monkeypatch, fence_tmp):
    """ears.message_bin.find falls back to ~/.local/bin/message under the HOME it is given. A
    child handed the live HOME is refused before it can look; one on the session's HOME falls
    back onto a refuser that names the test."""
    live = tmp_path / "live"
    _script(live / ".local" / "bin" / "message", f"touch {tmp_path / 'ran'}")
    monkeypatch.setattr(mail_fence, "LIVE_HOME", str(live.resolve()))
    code = "import os; from ears import message_bin; print(message_bin.find(os.environ))"
    with expect_refusal(), pytest.raises(RealMailIndex):
        subprocess.run([sys.executable, "-c", code], env={**os.environ, "HOME": str(live)}, cwd=ROOT,
                       timeout=60)
    env = {**os.environ, "PATH": "/usr/bin:/bin"}               # the session's HOME
    out = subprocess.run([sys.executable, "-c", code], env=env, cwd=ROOT, capture_output=True,
                         text=True, timeout=60, check=True).stdout.strip()
    assert out == os.path.join(mail_fence._session["home"], ".local", "bin", "message")
    with expect_refusal() as got:                       # ...which is a refuser that names the test
        ran = subprocess.run(["sh", "-c", f"'{out}' send --to x"], capture_output=True, timeout=60)
    assert ran.returncode == mail_fence.EXIT and not (tmp_path / "ran").exists()
    assert [(r["where"], r["prog"]) for r in got] == [("child", "message")]


@pytest.mark.parametrize("rel", mail_fence.HOME_BINS)
def test_the_session_home_holds_a_refuser_where_the_fallback_looks(rel):
    for name in ("notmuch", "message", "memory", "pager"):
        path = Path(mail_fence._session["home"], rel, name)
        assert os.access(path, os.X_OK) and "refuse_mail.py" in path.read_text()


# -- the refuser and the log ----------------------------------------------------------------------

def test_the_refuser_writes_nothing_logs_the_test_and_exits_75():
    notmuch = os.path.join(mail_fence.bin_for(["notmuch"]), "notmuch")
    with expect_refusal() as got:
        out = subprocess.run([notmuch, "insert", "--folder=x"], input=b"x" * 200000,
                             capture_output=True, timeout=60)
    assert out.returncode == mail_fence.EXIT
    assert b"mail fence (EARS-506): refused notmuch insert" in out.stderr
    assert [(r["kind"], r["where"], r["prog"], r["argv"]) for r in got] == \
        [("refused", "child", "notmuch", ["insert", "--folder=x"])]
    assert got[0]["test"].split(" (")[0] == os.environ["PYTEST_CURRENT_TEST"].split(" (")[0]
    assert Path(notmuch).read_text().startswith("#!/bin/sh\n")


def test_a_grandchild_meets_the_refuser(fence_tmp):
    """A shell the test starts looks message up on PATH: the hook never sees it."""
    tmp, outside, marker = fence_tmp
    env = {**os.environ, "PATH": f"{outside}{os.pathsep}/usr/bin{os.pathsep}/bin"}
    with expect_refusal() as got:
        out = subprocess.run(["sh", "-c", "message send --to x </dev/null; echo $?"], env=env,
                             capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "75" and not marker.exists()
    assert [r["kind"] for r in got] == ["fenced", "refused"]       # the PATH, then the shell's message
    assert (got[1]["where"], got[1]["prog"]) == ("child", "message")


def test_fenced_path_keeps_a_tests_fake_first(tmp_path, monkeypatch):
    bin_dir = str(tmp_path / "bin")
    fake = str(tmp_path / "fake")
    assert mail_fence.fenced_path(f"{fake}:/usr/bin:{bin_dir}", bin_dir) == f"{fake}:{bin_dir}:/usr/bin"
    assert mail_fence.fenced_path(":/usr/bin", bin_dir) == f"{bin_dir}::/usr/bin"
    assert mail_fence.fenced_path(None, bin_dir).split(os.pathsep)[0] == bin_dir
    other = mail_fence.make_bin(str(tmp_path / "parent"), str(tmp_path / "parent.log"))
    assert mail_fence.fenced_path(f"{other}:/nonexistent-bin") == "/nonexistent-bin"     # nothing real: no bin
    assert mail_fence.fenced_path(f"{other}:{fake}:/usr/bin", bin_dir) == f"{fake}:{bin_dir}:/usr/bin"
    # a dir in tmp is a test's fake only if what it holds lands in tmp: a link out is real
    real = tmp_path.parent / f"{tmp_path.name}-outside"
    real.mkdir()
    (real / "notmuch").write_text("")
    (real / "notmuch").chmod(0o755)
    links = tmp_path / "links"
    links.mkdir()
    (links / "notmuch").symlink_to(real / "notmuch")
    monkeypatch.setattr(mail_fence, "TMP", str(tmp_path.resolve()))
    assert mail_fence.fenced_path(f"{fake}:{links}:/usr/bin", bin_dir) == f"{fake}:{bin_dir}:{links}:/usr/bin"


def test_unfenced_is_the_path_the_code_gave(tmp_path):
    ours = mail_fence.make_bin(str(tmp_path / "b"), str(tmp_path / "log"), ["notmuch"])
    assert mail_fence.unfenced(f"{ours}:/opt/x/bin:/usr/bin") == "/opt/x/bin:/usr/bin"
    assert mail_fence.unfenced("/opt/x/bin:/usr/bin") == "/opt/x/bin:/usr/bin"


def _child_pytest(tmp_path, files: dict):
    child = tmp_path / "child"
    child.mkdir()
    for name, text in files.items():
        (child / name).write_text(text)
    env = {**os.environ, "HOME": str(tmp_path / "home"), "PYTHONPATH": str(ROOT)}
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                           "-p", "tests.mail_fence", child_pytest.basetemp(tmp_path), str(child)],
                          cwd=child, env=env, capture_output=True, text=True, timeout=120)


def test_a_test_whose_child_was_refused_fails_at_teardown_naming_it(tmp_path):
    out = _child_pytest(tmp_path, {"test_escape.py": (
        "import subprocess\n"
        "from tests import mail_fence\n"
        "def test_escapes():\n"
        "    b = mail_fence.bin_for(['notmuch'])\n"
        "    subprocess.run(['sh', '-c', b + '/notmuch new'])\n"
        "def test_clean():\n"
        "    pass\n")})
    assert out.returncode == 1, out.stdout + out.stderr
    assert "2 passed, 1 error" in out.stdout, out.stdout
    assert re.search(r"reached for the live mail index \(EARS-506: fake it\): \S*test_escape\.py::"
                     r"test_escapes \(call\): child pid \d+ notmuch new", out.stdout), out.stdout
    assert re.search(r"^  REFUSED \S*test_escape\.py::test_escapes", out.stdout, re.M), out.stdout


def test_a_refusal_after_every_test_fails_the_session(tmp_path):
    out = _child_pytest(tmp_path, {
        "test_detached.py": (
            "import subprocess\n"
            "from tests import mail_fence\n"
            "def test_detaches():\n"
            "    b = mail_fence.bin_for(['pager'])\n"
            "    subprocess.Popen(['sh', '-c', 'sleep 0.5; ' + b + '/pager listen'], start_new_session=True,\n"
            "                     stderr=subprocess.DEVNULL)\n"),
        "conftest.py": (
            "import os, time, pytest\n"
            "@pytest.hookimpl(tryfirst=True)\n"
            "def pytest_sessionfinish(session):\n"
            "    end = time.time() + 30\n"
            "    while time.time() < end and not os.path.getsize(os.environ['MAIL_FENCE_LOG']):\n"
            "        time.sleep(0.05)\n")})
    assert out.returncode == 1, out.stdout + out.stderr
    assert "1 passed" in out.stdout
    assert re.search(r"^mail fence: reached for the live mail index outside a test: "
                     r"\S*test_detached\.py::test_detaches \(call\): child", out.stdout, re.M), out.stdout


def test_the_receipt_is_kept_when_asked(tmp_path, monkeypatch):
    receipt = tmp_path / "receipt.jsonl"
    monkeypatch.setenv(mail_fence.RECEIPT_ENV, str(receipt))
    out = _child_pytest(tmp_path, {"test_escape.py": (
        "import os\nfrom tests import mail_fence\n"
        "def test_escapes():\n    os.system(mail_fence.bin_for(['memory']) + '/memory ls')\n")})
    assert out.returncode == 1
    recs = [json.loads(x) for x in receipt.read_text().splitlines()]
    assert [(r["prog"], r["argv"]) for r in recs] == [("memory", ["ls"])]
