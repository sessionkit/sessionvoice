"""Tests never read this machine's ears settings: ears.listen.stt takes the engine from
~/.sessionkit/voice/settings/ears.json at import, so a host that has chosen Parakeet would change
the defaults the tests assert. Set before any test module imports ears."""
import os

os.environ["EARS_CONF"] = os.path.join(os.path.dirname(__file__), "no-such-ears.json")

# ...nor reaches a real audio device, in this process or any child (EARS-320): a mouth
# test played to the user's AirPods. tests/audio_fence.py refuses the real sounddevice,
# installed or not, from here on, before any test module is imported; a test's fake
# still wins. The host's choice of output device never reaches a test either.
from tests import audio_fence  # noqa: E402
from tests.audio_fence import no_real_audio_device  # noqa: E402,F401

# The test-time anchor (EARS-397), re-exported; defined once, in tests/_anchor.py.
from tests._anchor import ANCHOR, ANCHOR_ISO, ANCHOR_SUMMER, ANCHOR_SUMMER_ISO  # noqa: E402,F401

# ...nor the runner's own environment: the mouth's device, settings, voice, settings
# cascade, mail verifier and sendmail, and talk's socket mode, each popped or set to a
# fake under every name it has (tests/env_fence.py holds the table, and why each one).
from tests import env_fence  # noqa: E402
from tests import _scanlist  # noqa: E402  (the private-word guards' list, EARS-406)

env_fence.apply()

import sys as _sys  # noqa: E402

# ...and every conftest under tests/ fences every test beneath it, whatever the command
# line's order (EARS-313). pytest 9.1 binds a conftest's fixtures to the FIRST Directory
# node of its path (_pytest/fixtures.py:1800, `_pending_conftests.pop`). A file argument
# re-collects its parent (main.py, `handle_dupes`), so `tests/mouth/a.py tests/b.py
# tests/mouth/c.py` builds a second tests/mouth node, and c.py ran with none of
# tests/mouth/conftest.py's fixtures: HOME unfenced, the resolver's cache never reset,
# and test_mouth_resolver's envfile() wrote the live ~/.voicemode/voicemode.env.
# This binds the conftest to each later node of the same directory as well.
from pathlib import Path as _Path

import pytest

_FIRST_DIR = pytest.StashKey[dict]()


@pytest.hookimpl(wrapper=True)
def pytest_make_collect_report(collector):
    rep = yield
    if isinstance(collector, pytest.Directory):
        first = collector.session.stash.setdefault(_FIRST_DIR, {}).setdefault(collector.path, collector)
        if first is not collector:
            conftest = collector.config.pluginmanager.get_plugin(str(collector.path / "conftest.py"))
            if conftest is not None:
                collector.session._fixturemanager.parsefactories(holder=conftest, node=collector)
    return rep


_MOUTH = _Path(__file__).parent / "mouth"


@pytest.fixture(autouse=True)
def mouth_fenced(request):
    """A tests/mouth test runs only inside its fence, with HOME in tmp (EARS-313). Without
    the fence it fails at setup, before its body could touch the live HOME."""
    if _MOUTH not in request.path.parents:
        return
    if "heard_box" not in request.fixturenames:
        pytest.fail("tests/mouth/conftest.py's fence is missing: HOME would be the live one (EARS-313)")
    request.getfixturevalue("heard_box")
    home = os.path.realpath(os.environ["HOME"])
    if home_guard.refused(home):
        pytest.fail(f"tests/mouth's HOME is the live one ({home}), not tmp (EARS-313)")


# ...and nothing writes under the live HOME, in any test (EARS-313, the super's guard):
# tests/home_guard.py refuses the write as it happens, and the test fails at teardown even
# if its code caught the error.
from tests import home_guard  # noqa: E402
from tests.home_guard import live_home_untouched, pytest_sessionstart  # noqa: E402,F401


def pytest_sessionfinish(session):
    home_guard.pytest_sessionfinish(session)
    audio_fence.pytest_sessionfinish(session)   # a refusal no test was running for fails the run


# ...nor runs sessionmail's real `message setup`, the dry run included: install asks it what
# carries the mail and makes the heard box with it (tests/setup_fence.py holds both fences).
from tests import setup_fence  # noqa: E402,F401
from tests.setup_fence import no_real_message_setup  # noqa: E402,F401


# ...and a host with no ffmpeg on PATH says so ONCE, in red, by name (EARS-327 fix 4, ruled
# (b2) by the super; narrowed by EARS-377). Without ffmpeg, mouth's file player
# (backends.FileSound.stream) cannot decode a bed or a tink. The decode thread swallows the
# ENOENT and ends the bed, and the test then fails on what it saw: a wait_for timeout, a
# KeyError, an 'error' outcome. So a test that needs ffmpeg DECLARES it, and only while
# ffmpeg is missing:
#   - a test marked @pytest.mark.needs_ffmpeg skips at SETUP, before any fixture runs, with a
#     reason that starts "no ffmpeg on PATH". Its body never runs, so it can hide nothing.
#   - an UNDECLARED test is relabelled SKIPPED "no ffmpeg on PATH: ..." only when its failure
#     IS the missing binary: a FileNotFoundError (ENOENT) naming ffmpeg or ffprobe is in the
#     exception's chain (__cause__, __context__; an exception group only when ALL its members
#     are the missing binary, so a real sibling failure is never hidden). Every other failure
#     stays FAILED with its own traceback, even one that tried ffmpeg on another thread
#     (EARS-377: the old rule relabelled ANY failure of a test that tried ffmpeg, and a real
#     bed/line race hid behind it). Such a report carries an "ffmpeg" section: the test tried
#     ffmpeg, and if it needs it, it should be marked.
#   - ONE collected item, tests/conftest.py::ffmpeg_on_path (the same node id every run), runs
#     last and always SKIPS: docs/dependencies.md names what uses ffmpeg, so its absence
#     is a loud skip, never a failure (EARS-402, the GC's strict-shape ruling). Whenever a test
#     in this run TRIED ffmpeg (the interpreter's "subprocess.Popen" audit event, from the main
#     thread or from a thread started DURING that test; a thread leaked from an earlier test
#     never marks the current one), was declared, or skipped itself for ffmpeg (at setup or in
#     its body), the skip reason names ffmpeg, docs/dependencies.md and true counts, and the
#     terminal summary repeats it in its own "ffmpeg" section, so it shows without -rs. A run
#     with none of these skips it as "no test in this run needed it", with no summary line.
#     What stays FAILED is unchanged: every failure that is not the missing binary (above),
#     a test that tried ffmpeg and failed for another reason included; the skip reason lists those.
# With ffmpeg on PATH none of this is installed: no item, no audit hook, no skip, every test as
# before. An audit hook, not a Popen wrapper: audio_fence owns subprocess.Popen.__init__.
import errno as _errno  # noqa: E402
import shutil as _shutil  # noqa: E402
import threading as _threading  # noqa: E402

FFMPEG_ITEM = "ffmpeg_on_path"
FFMPEG_MARK = "needs_ffmpeg"
_FFMPEG_EXES = ("ffmpeg", "ffprobe")
_ffmpeg = {"id": f"tests/conftest.py::{FFMPEG_ITEM}", "hooked": False, "test": None,
           "threads": frozenset(), "tried": set(), "relabelled": [], "failed": [],
           "declared": [], "own": [], "summary": None}


def pytest_configure(config):
    config.addinivalue_line(
        "markers", f"{FFMPEG_MARK}: the test needs ffmpeg/ffprobe on PATH; on a host without them it "
                   "skips at setup, 'no ffmpeg on PATH' (tests/conftest.py, EARS-377)")


def _ffmpeg_audit(event, args):
    if event == "subprocess.Popen" and _ffmpeg["test"] is not None:
        me = _threading.get_ident()
        if me != _threading.main_thread().ident and me in _ffmpeg["threads"]:
            return                     # a thread that outlived an earlier test: not this test's
        try:
            exe = os.fsdecode(args[0])   # the executable: argv[0] unless executable= named one
        except TypeError:
            return
        if os.path.basename(exe) in _FFMPEG_EXES:
            _ffmpeg["tried"].add(_ffmpeg["test"])


def _missing_ffmpeg(exc, _seen=frozenset()) -> bool:
    """True if the failure IS a missing ffmpeg/ffprobe: ``exc`` is a FileNotFoundError/ENOENT
    naming one, or its __cause__ or __context__ is (followed in turn), or it is an exception
    group whose members ALL are. A group with any other member is not relabelled: that member
    is a real failure the missing binary does not explain (EARS-377 fix-001 review).
    The chain is walked with a stack, not by recursion: pytest reports a chain thousands of
    links long, and a recursive walk would raise RecursionError inside this hook and abort the
    whole run. Only a group's members recurse, one level per level of nesting."""
    seen, stack = set(_seen), [exc]
    while stack:
        e = stack.pop()
        if e is None or id(e) in seen:
            continue
        seen.add(id(e))
        if isinstance(e, OSError) and e.errno == _errno.ENOENT and e.filename is not None:
            try:
                name = os.path.basename(os.fsdecode(e.filename))
            except TypeError:
                name = ""
            if name in _FFMPEG_EXES:
                return True
        members = getattr(e, "exceptions", None)
        if isinstance(members, tuple) and members:
            done = frozenset(seen)     # each member judged afresh, a repeated member included
            if all(_missing_ffmpeg(m, done) for m in members):
                return True
        stack += (e.__context__, e.__cause__)      # __cause__ first, as raised
    return False


def _ffmpeg_reason(nodeid: str) -> str:
    """The item's skip reason, built from this run's counts (each counted once)."""
    f = _ffmpeg
    relabelled, failed = len(set(f["relabelled"])), len(f["failed"])
    tried = len(f["tried"] | set(f["relabelled"]))   # a relabel tried it, whatever the audit saw
    declared, own = len(f["declared"]), len(set(f["own"]))
    if not (tried or relabelled or declared or own):
        return ""
    msg = (f"ffmpeg is not on PATH (docs/dependencies.md says what uses it): "
           f"{tried} test(s) tried to launch it "
           f"({relabelled} skipped here as 'no ffmpeg on PATH: see {nodeid}' because the "
           f"missing binary failed them, {failed} FAILED for another reason, "
           f"{tried - relabelled - failed} passed or skipped anyway); {declared} marked "
           f"{FFMPEG_MARK} skipped at setup; {own} skip themselves for ffmpeg")
    if failed:
        msg += ". Tried ffmpeg and FAILED (each may need @pytest.mark.needs_ffmpeg): " + \
               ", ".join(f["failed"])
    return msg


class FfmpegOnPath(pytest.Item):
    """Stands in for every test this run that needed ffmpeg and could not have it: a LOUD
    skip naming ffmpeg and docs/dependencies.md, never a failure (EARS-402)."""

    def runtest(self):
        msg = _ffmpeg_reason(self.nodeid)
        if not msg:
            pytest.skip("no ffmpeg on PATH, and no test in this run needed it")
        _ffmpeg["summary"] = msg
        pytest.skip(msg)

    def reportinfo(self):
        return self.path, None, self.name


@pytest.hookimpl(trylast=True)   # after -k/-m/--lf have chosen: the item always runs, and last
def pytest_collection_modifyitems(session, config, items):
    if _shutil.which("ffmpeg") is not None:
        return
    try:
        _ffmpeg["id"] = f"{_Path(__file__).relative_to(config.rootpath).as_posix()}::{FFMPEG_ITEM}"
    except ValueError:
        pass
    declared = pytest.mark.skip(reason=f"no ffmpeg on PATH: marked {FFMPEG_MARK}; see {_ffmpeg['id']}")
    for item in items:
        if item.get_closest_marker(FFMPEG_MARK) is not None:
            item.add_marker(declared)      # the skipping plugin acts on it at setup
    if not _ffmpeg["hooked"]:      # an audit hook cannot be removed: it does nothing between tests
        _sys.addaudithook(_ffmpeg_audit)
        _ffmpeg["hooked"] = True
    items.append(FfmpegOnPath.from_parent(session, name=FFMPEG_ITEM, nodeid=_ffmpeg["id"],
                                          path=_Path(__file__)))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(item, nextitem):
    if _ffmpeg["hooked"]:
        _ffmpeg["threads"] = frozenset(t.ident for t in _threading.enumerate())   # alive before it
    _ffmpeg["test"] = item.nodeid if _ffmpeg["hooked"] else None
    try:
        return (yield)
    finally:
        _ffmpeg["test"] = None


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    rep = yield
    if not _ffmpeg["hooked"] or isinstance(item, FfmpegOnPath) or call.when == "teardown":
        return rep
    if rep.failed and call.excinfo is not None and _missing_ffmpeg(call.excinfo.value):
        rep.outcome = "skipped"
        line = item.location[1]
        rep.longrepr = (str(item.path), None if line is None else line + 1,
                        f"Skipped: no ffmpeg on PATH: see {_ffmpeg['id']}")
        _ffmpeg["relabelled"].append(item.nodeid)
    elif rep.failed and item.nodeid in _ffmpeg["tried"]:
        rep.sections.append(("ffmpeg (EARS-377)", (
            "this test tried to launch ffmpeg/ffprobe, which is not on PATH, and FAILED for another "
            "reason: it stays FAILED. If it needs ffmpeg, mark it @pytest.mark.needs_ffmpeg.\n")))
        if item.nodeid not in _ffmpeg["failed"]:
            _ffmpeg["failed"].append(item.nodeid)
    elif rep.skipped and isinstance(rep.longrepr, tuple) and "ffmpeg" in str(rep.longrepr[2]).lower():
        if call.when == "setup" and item.get_closest_marker(FFMPEG_MARK) is not None:
            _ffmpeg["declared"].append(item.nodeid)
        else:                                  # its own skipif at setup, or pytest.skip in its body
            _ffmpeg["own"].append(item.nodeid)
    return rep


def pytest_terminal_summary(terminalreporter):
    """The loud half of the ffmpeg skip: its reason again, in its own section, whenever a test
    in this run needed the missing ffmpeg. A bare `pytest -q` shows it without -rs."""
    if _ffmpeg["summary"]:
        terminalreporter.write_sep("-", "ffmpeg: not on PATH", yellow=True)
        terminalreporter.write_line(_ffmpeg["summary"], yellow=True)
    # ...and the private-word guards' skip, counted, when the private scan list is not set
    # (EARS-406, tests/_scanlist.py): a guard that cannot run says so, never passes quietly.
    _scanlist.summary(terminalreporter)
