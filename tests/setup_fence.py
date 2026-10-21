"""No test runs sessionmail's real `message setup`.

`ears services install` asks sessionmail what carries the launch agents' mail with
`message setup --box heard --dry-run`, and on a mail-drop host it makes the heard box with
`message setup --box heard`. A real run, even the dry one, reads the live host's settings, and
the applying one makes boxes and a notmuch profile, and on a postfix host rebuilds the maps.

Two fences, installed for the whole session by tests/conftest.py:

- ``no_real_message_setup``: an autouse fixture. When ``ears.services`` is loaded, its
  ``_message_setup`` answers as sessionmail would on a postfix host (an MTA carries the
  mail, nothing is made), so an install test written before the question was asked keeps
  its postfix answer. A test that wants another answer patches ``_message_setup`` itself.
- an audit hook. It refuses, before the child starts, any ``message setup`` whose program is
  not under the temp dir (a test's own stub), whatever imported ``ears.services`` and
  whenever. It raises :class:`RealMessageSetup`, which is not an OSError, so
  ``_message_setup``'s ``except`` lets it through and the test fails.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

#: sessionmail's answer on a postfix host, as its `message setup --dry-run` prints it.
MTA_PLAN = ("steps:\n"
            "  carrier          DONE     the MTA at /opt/x/sendmail "
            "(from ~/.sessionkit/mail/settings/mail.json)\n")

_TMP = os.path.realpath(tempfile.gettempdir())


class RealMessageSetup(RuntimeError):
    """A test tried to run the real `message setup`. Not an OSError, so nothing catches it."""


def _is_message_setup(args) -> str | None:
    """The program, when ARGS run `message setup`; else None."""
    if isinstance(args, (str, bytes, os.PathLike)) or not args:
        return None
    argv = [os.fsdecode(a) if isinstance(a, (bytes, os.PathLike)) else str(a) for a in args]
    if len(argv) >= 2 and os.path.basename(argv[0]) == "message" and argv[1] == "setup":
        return argv[0]
    return None


def _hook(event: str, args: tuple) -> None:
    if event != "subprocess.Popen":
        return
    prog = _is_message_setup(args[1])
    if prog is None:
        return
    real = os.path.realpath(prog)
    if not (real == _TMP or real.startswith(_TMP + os.sep)):
        raise RealMessageSetup(f"a test ran the real `message setup` ({prog}): stub it "
                               "(tests/setup_fence.py)")


sys.addaudithook(_hook)


@pytest.fixture(autouse=True)
def no_real_message_setup(monkeypatch):
    services = sys.modules.get("ears.services")
    if services is not None:
        monkeypatch.setattr(services, "_message_setup",
                            lambda argv, runs=None: (4, MTA_PLAN, ""))
