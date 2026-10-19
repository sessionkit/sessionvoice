"""EARS-136: `ears install --apple` reports a failed Apple asset step as ONE plain line, not a
traceback. The asset step used `check=True` with no `try`, so a helper that exited 1 became a
CalledProcessError traceback (or, on the VM, a bare exit 1), and Silero was never reached.

Nothing here builds Swift or downloads assets: cli.subprocess.run is stubbed and answers for
the helper the way the new helper (one `apple-speech: ...` line), an old helper (progress plus
a raw NSError on stderr) or a silent one would.
"""
import subprocess

import pytest

from ears import cli

NEW = "apple-speech: locale xx-XX is not supported by SpeechTranscriber on this Mac (`apple-speech locales` lists the supported ones)\n"
OLD = ("downloading assets for de-DE...\n"
       "error: Error Domain=SFSpeechErrorDomain Code=4 \"Some modules are configured with an unsupported configuration.\"\n")


def _install(monkeypatch, tmp_path, helper, build=None, locale="xx-XX"):
    """Run `ears install --apple --locale L` with every subprocess faked. `helper` is what the
    `apple-speech install` argv does: (rc, stderr), or an exception to raise. `build` likewise
    for `swift build`. Returns (rc, calls, silero_fetched)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    calls, fetched = [], []

    def run(cmd, **kw):
        calls.append((cmd, kw))
        if cmd[:2] == ["swift", "build"]:
            if isinstance(build, BaseException):
                raise build
            return subprocess.CompletedProcess(cmd, 0)
        assert cmd[-1] == "install" and kw["env"]["APPLE_SPEECH_LOCALE"] == locale
        if isinstance(helper, BaseException):
            raise helper
        rc, err = helper
        return subprocess.CompletedProcess(cmd, rc, None, err)

    monkeypatch.setattr(cli.subprocess, "run", run)
    monkeypatch.setattr(cli.shutil, "copy2", lambda *a, **kw: None)
    monkeypatch.setattr(cli.sys, "platform", "darwin")
    monkeypatch.setattr(cli, "silero_model_path", lambda: None)
    monkeypatch.setattr(cli, "fetch_silero", lambda dst: fetched.append(dst))
    rc = cli.main(["install", "--apple", "--locale", locale])
    return rc, calls, fetched


def _one_line(capsys):
    err = capsys.readouterr().err
    assert "Traceback" not in err
    lines = err.splitlines()
    assert len(lines) == 1, err
    return lines[0]


def test_unsupported_locale_is_one_line_with_the_helpers_reason(monkeypatch, tmp_path, capsys):
    rc, _, fetched = _install(monkeypatch, tmp_path, (2, NEW))
    assert rc == 1
    line = _one_line(capsys)
    assert line == "ears install: Apple speech assets for xx-XX not installed: " + NEW.strip()
    assert fetched == []                    # stops before the Silero step, like Silero's own failure


def test_the_helpers_stderr_is_captured_not_passed_through(monkeypatch, tmp_path, capsys):
    _, calls, _ = _install(monkeypatch, tmp_path, (2, NEW))
    (cmd, kw), = [c for c in calls if c[0][-1] == "install"]
    assert kw.get("stderr") == subprocess.PIPE and kw.get("text")
    assert not kw.get("check")              # a non-zero exit is ours to report, not a raise
    capsys.readouterr()


def test_an_old_helper_still_gives_one_line(monkeypatch, tmp_path, capsys):
    # a helper already in ~/.cache/ears/bin from before the fix: progress and a raw NSError
    rc, _, _ = _install(monkeypatch, tmp_path, (1, OLD), locale="de-DE")
    assert rc == 1
    line = _one_line(capsys)
    assert line.startswith("ears install: Apple speech assets for de-DE not installed: error: Error Domain=SFSpeechErrorDomain Code=4")
    assert "downloading" not in line


@pytest.mark.parametrize("stderr", ["", None, "\n  \n"])
def test_a_silent_failure_is_named(monkeypatch, tmp_path, capsys, stderr):
    # the VM's "exit 1, no message" (G9)
    rc, _, _ = _install(monkeypatch, tmp_path, (1, stderr))
    assert rc == 1
    assert _one_line(capsys) == "ears install: Apple speech assets for xx-XX not installed: apple-speech install exited 1 with no message"


def test_a_helper_that_cannot_run_is_one_line(monkeypatch, tmp_path, capsys):
    rc, _, _ = _install(monkeypatch, tmp_path, PermissionError(13, "Permission denied"))
    assert rc == 1
    line = _one_line(capsys)
    assert line.startswith("ears install: Apple speech assets for xx-XX not installed: could not run ")
    assert "Permission denied" in line


def test_assets_in_goes_on_to_silero(monkeypatch, tmp_path, capsys):
    rc, _, fetched = _install(monkeypatch, tmp_path, (0, ""))
    assert rc == 0
    assert len(fetched) == 1
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("exc", [subprocess.CalledProcessError(1, ["swift", "build"]),
                                 FileNotFoundError(2, "No such file or directory", "swift")])
def test_a_failed_swift_build_is_one_line(monkeypatch, tmp_path, capsys, exc):
    rc, calls, fetched = _install(monkeypatch, tmp_path, (0, ""), build=exc)
    assert rc == 1
    assert _one_line(capsys).startswith("ears install: apple-speech helper did not build: ")
    assert [c for c, _ in calls if c[-1] == "install"] == []   # never ran the helper
    assert fetched == []
