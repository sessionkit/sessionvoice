"""EARS-388: the mouth's notice sender and mail verifier find ``message`` where uv
installs it, under a launchd PATH, and the ``_SERVICE`` fence still holds.

HOME is the fence's tmp HOME (``tests/mouth/conftest.py``), so nothing here can find
this machine's real ``message``. The rule itself is tested in
``tests/test_message_bin.py``; the notice's launchd-PATH regression in
``tests/mouth/test_mouth_sendmail_fence.py``.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from ears import message_bin
from mouth import box, notice
from tests import env_fence

FAKE = Path(__file__).parent / "fixtures" / "fake_sendmail.py"
STUB = " ".join(shlex.quote(x) for x in (sys.executable, str(FAKE)))
LAUNCHD_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
REAL_RUN = subprocess.run          # before any test patches notice.subprocess.run


def install(script: str) -> Path:
    """``message`` as uv lays it out under the fenced HOME: the program in uv's tool
    dir running SCRIPT, linked into ~/.local/bin. The link is what is found."""
    home = Path(os.environ["HOME"])
    prog = home / message_bin.KNOWN[1]
    prog.parent.mkdir(parents=True)
    prog.write_text(script)
    prog.chmod(0o755)
    (home / message_bin.KNOWN[0]).parent.mkdir(parents=True, exist_ok=True)
    (home / message_bin.KNOWN[0]).symlink_to(prog)
    return home / message_bin.KNOWN[0]


@pytest.fixture
def item():
    return {"utt": "u1", "text": "x", "notice_to": "ivy@" + notice._host()}


@pytest.fixture
def service(monkeypatch, heard_box):
    """The marked service with the default `message` and a launchd PATH."""
    env_fence.delenv(monkeypatch, "MOUTH_SENDMAIL")
    monkeypatch.setattr(notice, "DEFAULT_SENDMAIL", "message")
    monkeypatch.setattr(notice, "_SERVICE", "mouth serve")
    monkeypatch.setenv("PATH", LAUNCHD_PATH)


# -- the notice sender --------------------------------------------------------------

def test_unmarked_the_default_is_still_fenced_with_message_installed(monkeypatch, heard_box, item):
    """The fallback is the marked service's: an imported mouth is refused the
    default `message` however it could be found."""
    install("#!/bin/sh\nexit 0\n")
    env_fence.delenv(monkeypatch, "MOUTH_SENDMAIL")
    monkeypatch.setattr(notice, "DEFAULT_SENDMAIL", "message")
    monkeypatch.setattr(notice, "_SERVICE", None)
    monkeypatch.setenv("PATH", LAUNCHD_PATH)
    monkeypatch.setattr(notice.subprocess, "run", lambda *a, **k: 1 / 0)
    assert notice.dropped(item, {"reason": "error", "cut": True}).startswith(
        "failed: message send fenced: not the mouth service")


def test_an_explicit_sendmail_still_wins_and_is_never_swapped(service, monkeypatch, item):
    """$MOUTH_SENDMAIL is what runs; named but not there, it is refused, never
    replaced by the installed `message`."""
    install("#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("MOUTH_SENDMAIL", "no-such-sender")
    monkeypatch.setattr(notice.subprocess, "run", lambda *a, **k: 1 / 0)
    assert notice.dropped(item, {"reason": "error", "cut": True}) == (
        "failed: no message binary ($MOUTH_SENDMAIL: no-such-sender is not an executable on PATH)")
    monkeypatch.setenv("MOUTH_SENDMAIL", STUB)          # a command that is there: it runs
    ran = []
    monkeypatch.setattr(notice.subprocess, "run",
                        lambda argv, *a, **k: ran.append(argv) or REAL_RUN(argv, *a, **k))
    assert notice.dropped(item, {"reason": "error", "cut": True}) == f"sent ivy@{notice._host()}"
    assert ran[0][:2] == [sys.executable, str(FAKE)]


def test_no_message_anywhere_names_every_place_looked(service, item):
    assert notice.dropped(item, {"reason": "error", "cut": True}) == (
        f"failed: no message binary (message is not an executable on PATH, "
        f"~/{message_bin.KNOWN[0]}, ~/{message_bin.KNOWN[1]}; it is sessionmail's)")


# -- the box's verifier ---------------------------------------------------------------

def test_the_verifier_runs_message_where_uv_installs_it(monkeypatch, heard_box, tmp_path):
    """At d4dc5e5 ``shutil.which("message")`` on a launchd PATH found nothing, and
    every mail was graded unsigned without a word."""
    argv = tmp_path / "argv.txt"
    exe = install(f"#!/bin/sh\necho \"$0 $@\" >> {shlex.quote(str(argv))}\nexit 1\n")
    monkeypatch.setenv("PATH", LAUNCHD_PATH)
    env_fence.delenv(monkeypatch, box.VERIFY_VAR)
    mail = tmp_path / "m"
    mail.write_text("From: a@b\n\nx\n")
    assert box.verify_signed(mail) is None               # exit 1: unsigned, but it ran
    assert argv.read_text().split() == [str(exe), "verify", "--json", "--trust", "--quiet",
                                        str(mail)]


def test_the_verifier_switch_still_wins_over_an_installed_message(monkeypatch, heard_box, tmp_path):
    argv = tmp_path / "argv.txt"
    install(f"#!/bin/sh\necho \"$@\" >> {shlex.quote(str(argv))}\nexit 0\n")
    monkeypatch.setenv("PATH", LAUNCHD_PATH)
    monkeypatch.setenv(box.VERIFY_VAR, "")
    assert box.verify_signed(tmp_path / "m") is None
    assert not argv.exists()


# -- a failed notice is loud in the service log --------------------------------------
# The decision's JSON line names the outcome; a failed send also writes one ERROR line,
# so a notice that never left is plain in the mouth's launchd log.

def _errors(err: str) -> list:
    return [l for l in err.splitlines() if l.startswith("mouth: ERROR:")]


def test_a_send_that_exits_nonzero_writes_one_error_line(service, item, capsys):
    install("#!/bin/sh\necho 'no such box' >&2\nexit 3\n")
    out = notice.dropped(item, {"reason": "error", "cut": True})
    assert out == "failed: message send exit 3: no such box"
    assert _errors(capsys.readouterr().err) == [
        f"mouth: ERROR: notice to ivy@{notice._host()} failed: message send exit 3: no such box"]


def test_a_missing_message_writes_one_error_line_per_notice(service, item, capsys):
    for _ in range(2):
        assert notice.dropped(item, {"reason": "error", "cut": True}).startswith(
            "failed: no message binary")
    errs = _errors(capsys.readouterr().err)
    assert len(errs) == 2
    for e in errs:
        assert e.startswith(f"mouth: ERROR: notice to ivy@{notice._host()} failed: no message binary")
        assert "EARS-" not in e and " he " not in f" {e} "     # B2: no tracker id, no he


def test_a_sent_notice_writes_no_error_line(service, item, monkeypatch, capsys):
    monkeypatch.setenv("MOUTH_SENDMAIL", STUB)
    assert notice.dropped(item, {"reason": "error", "cut": True}) == f"sent ivy@{notice._host()}"
    assert _errors(capsys.readouterr().err) == []
