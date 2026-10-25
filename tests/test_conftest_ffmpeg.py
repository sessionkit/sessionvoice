"""EARS-377: tests/conftest.py's no-ffmpeg rule skips only what the missing binary explains.

The old rule (EARS-327 fix 4) relabelled ANY failure of a test that tried ffmpeg as a skip, so
a real bed/line race hid behind "no ffmpeg on PATH" on a host without ffmpeg. Each case here
runs in a child pytest that loads tests/conftest.py as a plugin. The child's PATH is an empty
directory, so ffmpeg is missing there whatever this host has: these tests mean the same on a
host with no ffmpeg and on a Mac with brew's ffmpeg. One child runs every case (``whole``);
the counts that need a run of their own get one.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from tests import child_pytest

ROOT = Path(__file__).resolve().parents[1]
ITEM = "ffmpeg_on_path"

INNER = textwrap.dedent('''
    import subprocess
    import threading

    import pytest

    try:
        ExceptionGroup
    except NameError:                      # Python 3.10: pytest's own dependency backports it
        from exceptiongroup import ExceptionGroup


    def _launch(exe="ffmpeg"):
        subprocess.Popen([exe, "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


    def test_touches_ffmpeg_then_fails_for_another_reason():
        try:
            _launch()
        except FileNotFoundError:
            pass
        assert 1 + 1 == 3, "a real failure that has nothing to do with ffmpeg"


    def test_a_thread_swallows_the_missing_ffmpeg_then_the_test_fails():
        """The bed's shape: a decode thread swallows the ENOENT; the test fails on what it saw."""
        def decode():
            try:
                _launch()
            except FileNotFoundError:
                pass
        t = threading.Thread(target=decode)
        t.start()
        t.join(5)
        assert False, "the bed never ducked"


    def test_fails_only_because_ffmpeg_is_missing():
        _launch()


    def test_fails_on_missing_ffprobe_through_its_cause():
        try:
            _launch("ffprobe")
        except FileNotFoundError as e:
            raise RuntimeError("could not measure the file") from e


    def test_a_group_hides_no_real_sibling_failure():
        """A missing ffmpeg beside a real failure in one exception group: the real one FAILS."""
        errors = []
        try:
            _launch()
        except FileNotFoundError as e:
            errors.append(e)
        errors.append(AssertionError("a real failure beside the missing ffmpeg"))
        raise ExceptionGroup("two failures", errors)


    def test_a_group_of_only_missing_binaries_skips():
        errors = []
        for exe in ("ffmpeg", "ffprobe"):
            try:
                _launch(exe)
            except FileNotFoundError as e:
                errors.append(e)
        raise ExceptionGroup("both binaries missing", errors)


    def _long_chain(root, links=3000):
        """``links`` errors, each the __context__ of the next: far past the recursion limit."""
        exc = root
        for i in range(links):
            nxt = ValueError(f"link {i}")
            nxt.__context__ = exc
            exc = nxt
        return exc


    def test_a_long_chain_that_is_not_ffmpeg_fails():
        raise _long_chain(KeyError("a real failure at the root of a long chain"))


    def test_a_long_chain_ending_in_the_missing_ffmpeg_skips():
        try:
            _launch()
        except FileNotFoundError as e:
            root = e
        raise _long_chain(root)


    def test_tries_ffmpeg_and_passes():
        try:
            _launch()
        except FileNotFoundError:
            pass


    def test_fails_on_some_other_missing_file():
        open("/no/such/dir/ffmpeg-settings.json")


    @pytest.mark.needs_ffmpeg
    def test_declares_ffmpeg(tmp_path):
        raise AssertionError("a declared test's body must not run without ffmpeg")


    def test_skips_itself_in_its_body():
        pytest.skip("no ffmpeg")


    def test_never_touches_ffmpeg():
        assert True
''')


def run_inner(tmp_path: Path, *names: str) -> dict[str, tuple[str, str]]:
    """Run INNER's tests ``names`` (all if none) in a child pytest with no ffmpeg on PATH:
    {test name: (outcome, message)}, ffmpeg_on_path included, and the child's stdout under
    the key ""."""
    bare = tmp_path / "bin"
    bare.mkdir()
    assert shutil.which("ffmpeg", path=str(bare)) is None and shutil.which("ffprobe", path=str(bare)) is None
    home = tmp_path / "home"
    home.mkdir()
    inner = tmp_path / "test_inner.py"
    inner.write_text(INNER)
    xml = tmp_path / "junit.xml"
    env = {**os.environ, "PATH": str(bare), "HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"}
    args = [f"{inner}::{n}" for n in names] or [str(inner)]
    out = subprocess.run([sys.executable, "-m", "pytest", "-q", "-rA", "--tb=short", "-p", "no:cacheprovider",
                          "-p", "tests.conftest", "--rootdir", str(ROOT), "-c", str(ROOT / "pyproject.toml"),
                          f"--junitxml={xml}", child_pytest.basetemp(tmp_path), *args],
                         cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    assert out.returncode in (0, 1) and xml.exists(), out.stdout[-3000:] + out.stderr[-2000:]
    got: dict[str, tuple[str, str]] = {"": ("stdout", out.stdout)}
    for case in ET.parse(xml).getroot().iter("testcase"):
        outcome, message = "passed", ""
        for tag in ("failure", "error", "skipped"):
            el = case.find(tag)
            if el is not None:
                outcome, message = tag, (el.get("message") or "") + "\n" + (el.text or "")
                break
        got[case.get("name")] = (outcome, message)
    return got


@pytest.fixture(scope="module")
def whole(tmp_path_factory):
    return run_inner(tmp_path_factory.mktemp("whole"))


# (a) touches ffmpeg, fails for another reason: FAILED, with its own traceback --------------

def test_a_test_that_touches_ffmpeg_and_fails_for_another_reason_fails(whole):
    outcome, message = whole["test_touches_ffmpeg_then_fails_for_another_reason"]
    assert outcome == "failure", whole
    assert "a real failure that has nothing to do with ffmpeg" in message
    report = whole[""][1].split("test_touches_ffmpeg_then_fails_for_another_reason", 1)[1]
    assert "ffmpeg (EARS-377)" in report and "needs_ffmpeg" in report        # the hint, in its report
    assert "test_touches_ffmpeg_then_fails_for_another_reason" in whole[ITEM][1]   # named by the item


def test_a_failure_after_a_thread_swallowed_the_missing_ffmpeg_stays_failed(whole):
    """The hole the card is about: ffmpeg was tried (and failed) on another thread, and the test
    failed on what it saw. Nothing in its own failure is the missing binary, so it FAILS."""
    outcome, message = whole["test_a_thread_swallows_the_missing_ffmpeg_then_the_test_fails"]
    assert outcome == "failure" and "the bed never ducked" in message, whole
    assert "test_a_thread_swallows_the_missing_ffmpeg_then_the_test_fails" in whole[ITEM][1]


def test_an_exception_group_with_a_real_member_stays_failed(whole):
    """fix-001 review: a group is the missing binary only if EVERY member is."""
    outcome, message = whole["test_a_group_hides_no_real_sibling_failure"]
    assert outcome == "failure", whole
    assert "test_a_group_hides_no_real_sibling_failure" in whole[ITEM][1]


def test_a_long_chain_neither_aborts_the_run_nor_hides_a_failure(whole):
    """fix-001 review 2: a chain longer than the recursion limit once raised RecursionError in
    the hook, an INTERNALERROR that ended the whole run. It is walked with a stack now."""
    assert "INTERNALERROR" not in whole[""][1], whole[""][1][-3000:]
    outcome, message = whole["test_a_long_chain_that_is_not_ffmpeg_fails"]
    assert outcome == "failure" and "link 2999" in message, whole


def test_a_missing_file_that_is_not_ffmpeg_stays_failed(whole):
    assert whole["test_fails_on_some_other_missing_file"][0] == "failure", whole


# (b) fails only on the missing binary: SKIPPED 'no ffmpeg' ---------------------------------

@pytest.mark.parametrize("name", ["test_fails_only_because_ffmpeg_is_missing",
                                  "test_fails_on_missing_ffprobe_through_its_cause",
                                  "test_a_group_of_only_missing_binaries_skips",
                                  "test_a_long_chain_ending_in_the_missing_ffmpeg_skips"])
def test_a_test_that_fails_only_on_the_missing_binary_skips_no_ffmpeg(whole, name):
    outcome, message = whole[name]
    assert outcome == "skipped", whole
    assert message.startswith("no ffmpeg on PATH: see tests/conftest.py::ffmpeg_on_path"), message


def test_a_declared_test_skips_at_setup_and_its_body_never_runs(whole):
    outcome, message = whole["test_declares_ffmpeg"]
    assert outcome == "skipped" and message.startswith("no ffmpeg on PATH: marked needs_ffmpeg"), whole
    assert "body must not run" not in whole[""][1]


# (c) tries ffmpeg and passes: ffmpeg_on_path never says "no test needed it" ----------------

def test_a_test_that_tries_ffmpeg_and_passes_is_counted(tmp_path):
    got = run_inner(tmp_path, "test_tries_ffmpeg_and_passes")
    assert got["test_tries_ffmpeg_and_passes"][0] == "passed", got
    outcome, message = got[ITEM]
    assert outcome == "skipped", got[ITEM]          # a missing ffmpeg: a loud SKIP (EARS-402)
    assert "no test in this run needed it" not in message
    assert "1 test(s) tried to launch it" in message and "1 passed or skipped anyway" in message
    assert "docs/dependencies.md" in message and "brew install" not in message, message


def test_a_run_that_never_needs_ffmpeg_skips_the_item(tmp_path):
    got = run_inner(tmp_path, "test_never_touches_ffmpeg", "test_fails_on_some_other_missing_file")
    assert got["test_never_touches_ffmpeg"][0] == "passed"
    assert got["test_fails_on_some_other_missing_file"][0] == "failure"
    assert got[ITEM][0] == "skipped" and "no test in this run needed it" in got[ITEM][1], got[ITEM]
    assert "ffmpeg: not on PATH" not in got[""][1]   # no summary line


def test_the_terminal_summary_repeats_the_skip_without_rs(whole):
    """The loud half (EARS-402): the run's own summary carries the reason in an "ffmpeg"
    section, so a run without -rs still says ffmpeg is missing."""
    out = whole[""][1]
    assert "ffmpeg: not on PATH" in out, out[-2000:]
    section = out.split("ffmpeg: not on PATH", 1)[1]
    assert "docs/dependencies.md" in section and "8 test(s) tried to launch it" in section, section[:1000]


def test_the_whole_run_gives_true_counts(whole):
    """Each count once, a call-phase pytest.skip("no ffmpeg") among a test's own skips."""
    assert {n: o for n, (o, _) in whole.items() if n not in ("", ITEM)} == {
        "test_touches_ffmpeg_then_fails_for_another_reason": "failure",
        "test_a_thread_swallows_the_missing_ffmpeg_then_the_test_fails": "failure",
        "test_fails_only_because_ffmpeg_is_missing": "skipped",
        "test_fails_on_missing_ffprobe_through_its_cause": "skipped",
        "test_a_group_hides_no_real_sibling_failure": "failure",
        "test_a_group_of_only_missing_binaries_skips": "skipped",
        "test_a_long_chain_that_is_not_ffmpeg_fails": "failure",
        "test_a_long_chain_ending_in_the_missing_ffmpeg_skips": "skipped",
        "test_tries_ffmpeg_and_passes": "passed",
        "test_fails_on_some_other_missing_file": "failure",
        "test_declares_ffmpeg": "skipped",
        "test_skips_itself_in_its_body": "skipped",
        "test_never_touches_ffmpeg": "passed",
    }
    outcome, message = whole[ITEM]
    assert outcome == "skipped", whole[ITEM]        # never a failure, even with 3 FAILED (EARS-402)
    assert "ffmpeg is not on PATH (docs/dependencies.md says what uses it)" in message, message
    assert "prerequisite" not in message and "brew install" not in message, message
    for part in ("8 test(s) tried to launch it", "4 skipped here as", "3 FAILED for another reason",
                 "1 passed or skipped anyway", "1 marked needs_ffmpeg skipped at setup",
                 "1 skip themselves for ffmpeg"):
        assert part in message, (part, message)
