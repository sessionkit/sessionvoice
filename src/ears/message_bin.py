"""Where sessionmail's ``message`` CLI is: PATH first, then where uv installs it.

``message`` is a uv tool. uv puts the program in its tool dir
(``~/.local/share/uv/tools/sessionmail/bin/message``) and links it into
``~/.local/bin``. A launchd job's PATH is short (``/usr/bin:/bin:/usr/sbin:/sbin``
when a plist names none), and neither directory is on it, so a lookup on PATH
alone lost every send on a host whose plist predates the wider PATH.

:func:`find` is that lookup: ``shutil.which`` on PATH, then each of
:data:`KNOWN` under HOME. HOME and PATH come from the environment it is GIVEN,
never from the process alone: a test that hands it a fake HOME must never find
this machine's real ``message``.

It resolves the DEFAULT name only. A command the operator named
(``$EARS_MAIL_MESSAGE``, ``$MOUTH_SENDMAIL``) is looked up on PATH and nothing
else: what was named is what runs, or nothing does.

``keeper/ears-mail.py`` (stdlib only, run by the system python) keeps its own
copy of the rule, and ``keeper/ears-keeper.sh`` a shell one;
``tests/test_message_bin.py`` holds both to this module.
"""
from __future__ import annotations

import os
import shutil
from typing import Mapping, Optional

#: The default program name: sessionmail's mail CLI.
NAME = "message"

#: Where uv installs ``message``, relative to HOME, in the order looked.
KNOWN = (".local/bin/message", ".local/share/uv/tools/sessionmail/bin/message")


def known(environ: Optional[Mapping[str, str]] = None) -> list:
    """The absolute paths of :data:`KNOWN` under ENVIRON's HOME; [] with no HOME."""
    e = os.environ if environ is None else environ
    home = e.get("HOME") or ""
    return [os.path.join(home, rel) for rel in KNOWN] if home else []


def where_looked(environ: Optional[Mapping[str, str]] = None) -> str:
    """Every place :func:`find` looks, for a ``why not`` that names them: HOME is
    written ``~`` (short enough for a said row's 200 characters)."""
    e = os.environ if environ is None else environ
    return ", ".join(["PATH", *("~/" + rel for rel in KNOWN)]) if e.get("HOME") else "PATH (no HOME)"


def find(environ: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """The absolute path of sessionmail's ``message``, or None: on ENVIRON's PATH,
    else the first of :data:`KNOWN` under its HOME that is an executable file.
    ENVIRON defaults to the process environment."""
    # with no ENVIRON, the one-argument call (a test fakes shutil.which by name alone)
    found = (shutil.which(NAME) if environ is None
             else shutil.which(NAME, path=environ.get("PATH")))
    if found:
        return os.path.abspath(found)
    for path in known(environ):
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None
