"""No test reaches the live mail index, or runs a child with the live HOME (EARS-506).

A test run on a mail host indexed the live notmuch database: a ``notmuch`` process
descended from the suite's pytest. ``tests/setup_fence.py`` refuses a real
``message setup`` and nothing else, and a child the suite started ran with the live
HOME, where sessionmail's ``message`` sits in ``~/.local/bin`` (``ears.message_bin``
falls back to it with no PATH entry) and every send runs the MTA, then ``notmuch
insert`` or ``notmuch new``. So the fence closes every path, for the whole session
(``tests/conftest.py`` imports this module before any test module):

- **HOME is a session temp dir**, in this process and so in every child that inherits
  it. A child whose effective HOME is the live one (its ``env=`` HOME, else
  ``os.environ``'s), or unset, is REFUSED before it starts, by the audit hook below: a
  test that builds a child's env gives it a tmp HOME. Where a program falls back to HOME for
  sessionmail's CLIs (``~/.local/bin/message``, uv's tool dir: ``ears.message_bin``,
  ``keeper/ears-mail.py``, ``keeper/ears-keeper.sh``), the session HOME holds a refuser:
  that fallback, the way a child that ran with the live HOME reached the real
  ``message``, is refused and names its test instead of passing in silence.
- **The mail env is pinned** (``env_fence.FENCE``, under every alias): notmuch's
  ``NOTMUCH_CONFIG`` and ``NOTMUCH_DATABASE``, and sessionmail's roots, home, notmuch
  and carrier, under ``env_fence.MAIL_FENCE_ROOT``, a path that never exists;
  ``NOTMUCH_PROFILE`` and the XDG homes notmuch reads are popped. Every other
  ``SESSIONMAIL_*`` / ``SESSION_MAIL_*`` the runner exports (its subscription, its
  page rules, its transport) is popped here. A child's explicit env that sets one of
  the pinned names outside tmp gets the pin back.
- **A refusing bin is first on PATH**, before every PATH entry outside tmp (a test's
  own fake in tmp still wins): for each of ``notmuch``, ``message``, ``memory``, ``pager``
  and ``sendmail`` that the PATH would find for real, a program that runs
  ``tests/fixtures/refuse_mail.py``, which writes nothing but its log line and exits 75.
  A name the PATH would not find stays unfound (:func:`fenced_path`). A child's explicit
  PATH is fenced the same way. That reaches grandchildren, which no audit hook does: a shell
  or a keeper script that looks a mail program up on PATH meets the refuser.
- **An audit hook in this process** refuses, before the child starts, on
  ``subprocess.Popen``, ``os.posix_spawn``, ``os.exec*`` and ``os.spawn*``: a program
  named ``notmuch``, ``message``, ``memory``, ``pager`` or ``sendmail`` whose realpath is
  not under the temp dir; any child whose effective HOME is the live one or unset; and
  a child that would inherit an env this module cannot fix (``os.environ`` itself, or
  an exec's env) carrying a PATH on which a real mail program wins, or a mail variable
  unpinned. An explicit ``Popen`` env has those two fixed in place (logged ``fenced``,
  not a failure). It raises
  :class:`RealMailIndex`, not an OSError, so no ``except OSError`` swallows it.
  ``os.system`` runs a shell that inherits ``os.environ``: that env is judged. A
  ``subprocess.Popen`` that would inherit an ``os.environ`` a test made unsafe (a launchd
  PATH, with ``/usr/sbin/sendmail`` on it) is handed a copy, fixed like an explicit env:
  ``Popen.__init__`` is wrapped for that alone, outside ``tests/audio_fence.py``'s wrapper.

``session`` (sessiontools) is NOT refused: it runs no notmuch, and its one mail call
(``message box path``) looks ``message`` up on PATH, where it meets the refuser.

**Loudly.** Every refusal, in this process or a child, is a JSON line in the session's
log naming the test (``PYTEST_CURRENT_TEST``). A test fails at teardown if anything was
refused for it, even if its code caught the error; a refusal no test was running for
fails the session; and the session prints every refusal and every fenced child at the
end: that list is the receipt naming the escaping test. ``$MAIL_FENCE_RECEIPT``, when
set, is a path the log is copied to at the end of the session.

A test that means to be refused (the fence's own) wraps it in :func:`expect_refusal`.
"""
from __future__ import annotations

import contextlib
import inspect
import json
import os
import pwd
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

from ears import envnames
from tests import env_fence

#: The programs no test may run for real: notmuch itself, sessionmail's CLIs, and the MTA
#: whose delivery runs `notmuch insert`.
REFUSED = ("notmuch", "message", "memory", "pager", "sendmail")
#: Where a program that falls back to HOME finds sessionmail's CLIs (``ears.message_bin.KNOWN``,
#: keeper/ears-mail.py's copy): the session HOME holds a refuser at each.
HOME_BINS = (".local/bin", ".local/share/uv/tools/sessionmail/bin")
#: sessionmail's variable prefixes: every one the runner exports is popped at session start.
PREFIXES = ("SESSIONMAIL_", "SESSION_MAIL_")
LIVE_HOME = os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)
TMP = os.path.realpath(tempfile.gettempdir())
REFUSER = str(Path(__file__).resolve().parent / "fixtures" / "refuse_mail.py")
LOG_ENV = "MAIL_FENCE_LOG"
RECEIPT_ENV = "MAIL_FENCE_RECEIPT"
#: Every name the mail fence pins or pops, each with every alias it has.
PINNED = {n: env_fence.FENCE[name] for name in env_fence.MAIL_FENCED for n in envnames.group(name)}
EXIT = 75
#: The file that marks a dir as a fence's bin.
MARK = ".mail-fence-bin"

_EVENTS = frozenset({"subprocess.Popen", "os.posix_spawn", "os.exec", "os.spawn", "os.system"})


class RealMailIndex(RuntimeError):
    """A test tried to reach the live mail index, or run a child with the live HOME.
    Not an OSError, so nothing that catches a missing program catches it."""


_session: dict = {}       # dir, bin, home, log: this session's fence
_expected: set = set()    # records a test asked for (expect_refusal)
_reported: set = set()    # records a test, or the session, already failed for
_busy = threading.local()
_leak_cache: dict = {}


def under_tmp(path) -> bool:
    p = os.path.realpath(os.fsdecode(path))
    return p == TMP or p.startswith(TMP + os.sep)


def leaks(path_value: str) -> list:
    """The real mail programs a lookup on PATH_VALUE would find before any refuser, as
    (name, path): each REFUSED name that resolves to a file outside the temp dir."""
    got = _leak_cache.get(path_value)
    if got is None:
        got = []
        for name in REFUSED:
            found = shutil.which(name, path=path_value)
            if found and not under_tmp(found):
                got.append((name, found))
        if len(_leak_cache) > 256:
            _leak_cache.clear()
        _leak_cache[path_value] = got
    return got


def _a_fence_bin(path: str) -> bool:
    """PATH is a fence's bin: this session's, or another's (a parent pytest's, when this one
    is its child, whose refusers log to that session and must not answer for this one)."""
    return bool(path) and os.path.isfile(os.path.join(path, MARK))


def unfenced(path_value: str) -> str:
    """PATH_VALUE without the fence's bins: the PATH the code under test handed its child,
    for a test that asserts on it (the fence puts its bin first only where a real mail
    program would win)."""
    return os.pathsep.join(p for p in path_value.split(os.pathsep) if not _a_fence_bin(p))


def bin_for(names) -> str:
    """This session's refusing bin for exactly NAMES, made once under the session dir."""
    names = tuple(n for n in REFUSED if n in names)
    bins = _session.setdefault("bins", {})
    if names not in bins:
        bins[names] = make_bin(os.path.join(_session["dir"], "bin-" + "-".join(names)),
                               _session["log"], names)
    return bins[names]


def _holds_a_real_one(entry: str, names) -> bool:
    """ENTRY, a PATH dir in tmp, holds one of NAMES whose realpath is outside tmp: a symlink
    to the real program, not a test's fake."""
    return any((found := shutil.which(n, path=entry)) and not under_tmp(found) for n in names)


def fenced_path(path_value: str | None, bin_dir: str | None = None) -> str:
    """PATH_VALUE with no fence bin of its own, then, if a REFUSED program on it would be a
    real one, a bin refusing exactly those, before its first entry outside tmp (an empty
    entry, the cwd, counts as outside) or its first entry in tmp that links to a real one.
    A test's fake in tmp stays first, and a name the PATH would not find stays unfound: a
    test of a launchd PATH that holds no `message` still finds none, though
    /usr/sbin/sendmail on it is fenced. BIN_DIR forces the bin."""
    parts = [p for p in (os.defpath if path_value is None else path_value).split(os.pathsep)
             if p != bin_dir and not _a_fence_bin(p)]
    names = REFUSED
    if bin_dir is None:
        real = leaks(os.pathsep.join(parts))
        if not real:
            return os.pathsep.join(parts)
        names = [name for name, _ in real]
        bin_dir = bin_for(names)
    at = next((i for i, p in enumerate(parts)
               if not (p and under_tmp(p)) or _holds_a_real_one(p, names)), len(parts))
    return os.pathsep.join(parts[:at] + [bin_dir] + parts[at:])


# -- the env a child gets ------------------------------------------------------------------

def _is_bytes(env) -> bool:
    return any(isinstance(k, bytes) for k in env)


def _get(env, name):
    if _is_bytes(env):
        v = env.get(os.fsencode(name))
        return None if v is None else os.fsdecode(v)
    return env.get(name)


def _set(env, name, value) -> None:
    key = os.fsencode(name) if _is_bytes(env) else name
    if value is None:
        env.pop(key, None)
    else:
        env[key] = os.fsencode(value) if _is_bytes(env) else value


def env_faults(env) -> list:
    """What is wrong with ENV for a child, as (name, value it must have, why) triples:
    the live HOME (or none), a PATH a real mail program wins on, a mail variable unpinned."""
    out = []
    home = _get(env, "HOME")
    if not home or os.path.realpath(home) == LIVE_HOME:
        out.append(("HOME", _session["home"], f"HOME is {'unset' if not home else 'the live one'}"))
    path = _get(env, "PATH")
    real = leaks(os.defpath if path is None else path)
    if real:
        out.append(("PATH", fenced_path(path), f"PATH finds the real {', '.join(p for _, p in real)}"))
    for name, want in PINNED.items():
        have = _get(env, name)
        if have is None or have == want:
            continue                     # unset follows the fenced HOME; set must be the pin
        if have is not None and under_tmp(have):
            continue                     # a test's own fake, in tmp
        out.append((name, want, f"{name}={have!r} is not the fence's"))
    return out


# -- the audit hook ---------------------------------------------------------------------------

def _record(rec: dict) -> dict:
    rec.setdefault("test", os.environ.get("PYTEST_CURRENT_TEST"))
    rec.setdefault("pid", os.getpid())
    log = _session.get("log")
    if log:
        with contextlib.suppress(OSError):
            fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, (json.dumps(rec, default=str) + "\n").encode())
            finally:
                os.close(fd)
    return rec


def _argv(args) -> list:
    if isinstance(args, (str, bytes, os.PathLike)):
        return [os.fsdecode(args)]
    try:
        return [os.fsdecode(a) if isinstance(a, (bytes, os.PathLike)) else str(a) for a in args][:12]
    except TypeError:
        return [repr(args)]


def _child(event: str, args: tuple):
    """(program, argv, env, env may be fixed in place) for one spawn event. env None means
    the child inherits ``os.environ``."""
    if event == "subprocess.Popen":
        exe, argv, _cwd, env = args
        return exe, _argv(argv), env, isinstance(env, dict)
    if event in ("os.posix_spawn", "os.exec"):
        return args[0], _argv(args[1]), args[2], False
    if event == "os.spawn":
        return args[1], _argv(args[2]), args[3], False
    return None, _argv(args[0]), None, False          # os.system: a shell on os.environ


def judge(event: str, args: tuple) -> None:
    """Refuse (raise RealMailIndex) or fence (fix the child's env in place) one spawn. A child
    whose effective HOME is the live one (its ``env=`` HOME, else ``os.environ``'s), or unset
    (Python, bash and notmuch then take the account's home from the password database), is
    always refused: HOME is never fixed for it. A PATH or mail variable in an explicit env is
    fixed in place."""
    prog, argv, env, mutable = _child(event, args)
    why = []
    if prog is not None:
        p = os.fsdecode(prog)
        if os.path.basename(p) in REFUSED:
            if os.sep in p:
                found = p if os.path.exists(p) else None
            else:
                path = _get(env, "PATH") if env is not None else os.environ.get("PATH")
                found = shutil.which(p, path=path)
            if found and not under_tmp(found):
                why.append(f"runs the real {os.path.basename(p)} ({found})")
    faults = env_faults(os.environ if env is None else env)
    why += [w for name, _, w in faults if name == "HOME"]        # HOME is refused, never fixed
    faults = [f for f in faults if f[0] != "HOME"]
    if faults and mutable and not why:
        for name, value, _ in faults:
            _set(env, name, value)
        test = os.environ.get("PYTEST_CURRENT_TEST")
        if test is not None and _get(env, "PYTEST_CURRENT_TEST") is None:
            _set(env, "PYTEST_CURRENT_TEST", test)
        _record({"kind": "fenced", "where": "pytest", "event": event, "argv": argv,
                 "why": [w for _, _, w in faults]})
        return
    why += [w for _, _, w in faults]
    if why:
        rec = _record({"kind": "refused", "where": "pytest", "event": event, "argv": argv, "why": why})
        raise RealMailIndex(f"mail fence (EARS-506): {rec['test'] or 'no test'} {event} {argv[:4]}: "
                            f"{'; '.join(why)} (tests/mail_fence.py: fake it, fence its env)")


def _hook(event: str, args: tuple) -> None:
    if event not in _EVENTS or not _session.get("on") or getattr(_busy, "now", False):
        return
    _busy.now = True
    try:
        judge(event, args)
    finally:
        _busy.now = False


# -- the session -------------------------------------------------------------------------------

def _script(name: str, log: str) -> str:
    return ("#!/bin/sh\n"
            f"# EARS-506: no test may run the real {name} (tests/mail_fence.py)\n"
            f"exec {shlex.quote(sys.executable)} -I {shlex.quote(REFUSER)} {shlex.quote(log)} "
            '"$0" "$@"\n')


def make_bin(bin_dir: str, log: str, names=REFUSED) -> str:
    """BIN_DIR, marked as a fence's bin, holding a refuser for each of NAMES that logs to LOG."""
    os.makedirs(bin_dir, exist_ok=True)
    open(os.path.join(bin_dir, MARK), "w").close()
    for name in names:
        path = os.path.join(bin_dir, name)
        with open(path, "w") as f:
            f.write(_script(name, log))
        os.chmod(path, 0o755)
    return bin_dir


_popen_init = subprocess.Popen.__init__
_popen_sig = inspect.signature(_popen_init)


def _fenced_popen_init(self, *args, **kwargs):
    """A child that would inherit an ``os.environ`` the fence must fix (a test set a launchd
    PATH, with /usr/sbin/sendmail on it) is handed a copy instead, which the hook fixes."""
    if _session.get("on"):
        bound = _popen_sig.bind(self, *args, **kwargs)
        if bound.arguments.get("env") is None and env_faults(os.environ):
            bound.arguments["env"] = dict(os.environ)
            _popen_init(*bound.args, **bound.kwargs)
            return
    _popen_init(self, *args, **kwargs)


_fenced_popen_init.__wrapped__ = _popen_init
_fenced_popen_init.mail_fence = True
# audio_fence's wrapper is inside this one: its mark is kept, so its own check still holds.
_fenced_popen_init.audio_fence = getattr(_popen_init, "audio_fence", False)


def install() -> None:
    """Fence this process and every child it starts (idempotent): a fresh session dir in tmp
    holding the HOME, the refusing bin and the log."""
    if _session:
        return
    d = os.path.realpath(tempfile.mkdtemp(prefix="mail-fence-"))
    log = os.path.join(d, "refusals.jsonl")
    open(log, "a").close()
    home = os.path.join(d, "home")
    os.makedirs(home)
    for rel in HOME_BINS:                # the HOME fallback meets a refuser, loudly, by name
        make_bin(os.path.join(home, rel), log, ("notmuch", "message", "memory", "pager"))
    _session.update(dir=d, log=log, home=home, bins={})
    for name in [n for n in os.environ if n.startswith(PREFIXES) and n not in PINNED]:
        del os.environ[name]
    os.environ["HOME"] = home
    os.environ["PATH"] = fenced_path(os.environ.get("PATH"))
    os.environ[LOG_ENV] = log
    sys.addaudithook(_hook)
    if not getattr(subprocess.Popen.__init__, "mail_fence", False):
        subprocess.Popen.__init__ = _fenced_popen_init
    _session["on"] = True


def on() -> bool:
    """The fence holds in this process now."""
    return bool(_session.get("on")) and not env_faults(os.environ)


install()      # at import: before any test module is collected


# -- reading the log ----------------------------------------------------------------------------

def _key(rec: dict) -> str:
    return json.dumps(rec, sort_keys=True, default=str)


def records(start: int = 0) -> tuple[list, int]:
    """The records logged from byte ``start`` on, and the offset after them."""
    try:
        with open(_session["log"], "rb") as f:
            f.seek(start)
            data = f.read()
    except OSError:
        return [], start
    end = data.rfind(b"\n") + 1                    # a line still being written waits for next time
    recs = []
    for line in data[:end].splitlines():
        with contextlib.suppress(ValueError):
            recs.append(json.loads(line))
    return recs, start + end


def _size() -> int:
    try:
        return os.path.getsize(_session["log"])
    except OSError:
        return 0


@contextlib.contextmanager
def expect_refusal():
    """For the fence's own tests: refusals inside the block are wanted, not a failure.
    Yields the list, filled at exit, of every record logged inside the block (children too)."""
    got: list = []
    start = _size()
    try:
        yield got
    finally:
        recs, _ = records(start)
        _expected.update(_key(r) for r in recs if r.get("kind") == "refused")
        got.extend(recs)


def _text(r: dict) -> str:
    where = f"{r.get('where')} pid {r.get('pid')}"
    return (f"{r.get('test') or '(no test)'}: {where} {r.get('prog') or r.get('event') or ''} "
            f"{' '.join(map(str, r.get('argv') or []))[:200]}"
            + (f" [{'; '.join(r['why'])}]" if r.get("why") else ""))


def _mine(rec: dict, nodeid: str) -> bool:
    test = rec.get("test")
    return test is None or test.split(" (")[0] == nodeid


@pytest.fixture(autouse=True)
def no_live_mail_index(request):
    """Fails the test at teardown if anything during it, in this process or a child, was
    refused by the mail fence, even if the error was swallowed."""
    start = _size()
    yield
    recs, _ = records(start)
    bad = [r for r in recs if r.get("kind") == "refused" and _mine(r, request.node.nodeid)
           and _key(r) not in _expected and _key(r) not in _reported]
    _reported.update(_key(r) for r in bad)
    if bad:
        pytest.fail("reached for the live mail index (EARS-506: fake it): "
                    + "; ".join(_text(r) for r in bad))


def summary_lines() -> list:
    """The receipt: every refusal this session, then each test whose child the fence had to
    fence (its env named the live HOME, a real mail program, or an unpinned mail variable)."""
    recs, _ = records(0)
    out = [f"REFUSED {_text(r)}" for r in recs if r.get("kind") == "refused" and _key(r) not in _expected]
    fenced: dict = {}
    for r in recs:
        if r.get("kind") == "fenced":
            k = ((r.get("test") or "(no test)").split(" (")[0], "; ".join(r.get("why") or []))
            fenced[k] = fenced.get(k, 0) + 1
    out += [f"fenced {n}x {t}: {w}" for (t, w), n in sorted(fenced.items())]
    return out


def pytest_sessionfinish(session):
    """Print the receipt; a refusal outside every test's teardown fails the run; keep the
    log at $MAIL_FENCE_RECEIPT if asked; then drop the session dir."""
    recs, _ = records(0)
    late = [r for r in recs if r.get("kind") == "refused"
            and _key(r) not in _expected and _key(r) not in _reported]
    lines = summary_lines()
    tr = session.config.pluginmanager.get_plugin("terminalreporter")

    def say(msg, **kw):
        if tr is not None:
            tr.write_line(msg, **kw)
        else:
            print(msg, file=sys.stderr)

    if lines:
        if tr is not None:
            tr.write("\n")
        say("mail fence (EARS-506): " + ("refused or fenced:" if lines else ""), yellow=True)
        for line in lines:
            say("  " + line, red=line.startswith("REFUSED"))
    if late:
        _reported.update(_key(r) for r in late)
        say("mail fence: reached for the live mail index outside a test: "
            + "; ".join(_text(r) for r in late) + " (EARS-506)", red=True)
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
    receipt = os.environ.get(RECEIPT_ENV)
    if receipt:
        with contextlib.suppress(OSError):
            shutil.copyfile(_session["log"], receipt)
    _session["on"] = False
    shutil.rmtree(_session["dir"], ignore_errors=True)
