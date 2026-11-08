"""boxes - where a mail box is: ask sessionmail, never spell the mail root.

sessionvoice keeps no copy of the mail root. Every box it reads or writes (the
mouth's queue, the ``heard`` box, a seat's box a notice goes to) is asked of
sessionmail's ``message``, the one program that knows where mail lives on this host:

- ``message path NAME`` prints the maildir NAME resolves to, exit 0; exit 2 when
  there is no such box. :func:`path`.
- ``message setup --box NAME`` makes NAME on this host (and the mail root, the
  first time). :func:`make` asks ``path`` first, then ``setup``, then ``path`` again.
- ``message box list`` lists the boxes here, one row each, ``<kind> <name>``:
  :func:`stores` reads the ``store`` rows.
- ``message path --root`` prints the mail root and ``--boxes`` the folder the boxes
  live in (sessionmail 0.3.3; an older one exits 1 or 2). :func:`root`, :func:`boxes_dir`.

``message`` is found as :mod:`ears.message_bin` finds it (PATH, then where uv puts
it under HOME), in the environment given. With none, :class:`NoMessage` says so:
nothing here ever makes a folder under the mail root itself, so a host without
sessionmail gets no box, not a guessed one (SM 0.3.3, the mail root move).

A box found is kept for the process (:data:`_known`): a box does not move while the
mouth runs. A ``no`` is never kept: a box may be made between two asks.
:func:`forget` drops what is kept (tests).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, Optional

from . import message_bin
# subprocess is imported where it is used: the mouth's wrapper imports this module and
# must stay cheap (tests/mouth/test_mouth_timeline_cli.py).

#: Seconds a `message path` or `box list` may take; `setup` makes things, so longer.
PATH_TIMEOUT_S = 15.0
SETUP_TIMEOUT_S = 120.0
#: What a host without sessionmail is told.
NO_MESSAGE = ("no `message` on PATH (install sessionmail: it says where mail lives, and "
              "sessionvoice never guesses)")

_known: dict[str, Path] = {}


class NoMessage(RuntimeError):
    """sessionmail's `message` is not on this host, so no box can be asked for."""


class NoBox(RuntimeError):
    """`message` is here, but the box could not be found or made."""


def forget() -> None:
    """Drop every box kept: the next ask runs `message` again."""
    _known.clear()


def exe(environ: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """sessionmail's `message`, as :mod:`ears.message_bin` finds it in ENVIRON; None."""
    return message_bin.find(environ)


def _run(argv: list, environ: Optional[Mapping[str, str]], timeout: float):
    """`message ARGV` in ENVIRON: (exit, stdout, stderr), or None when it could not run.
    Raises :class:`NoMessage` when there is no `message` to run."""
    import subprocess

    msg = exe(environ)
    if not msg:
        raise NoMessage(NO_MESSAGE)
    env = dict(os.environ if environ is None else environ)
    try:
        r = subprocess.run([msg, *argv], capture_output=True, text=True, timeout=timeout,
                           env=env, stdin=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.returncode, r.stdout, r.stderr


def _last_line(out: str) -> str:
    lines = [l.strip() for l in (out or "").splitlines() if l.strip()]
    return lines[-1] if lines else ""


def path(name: str, environ: Optional[Mapping[str, str]] = None) -> Optional[Path]:
    """The maildir NAME resolves to (`message path NAME`, exit 0 and a path), or None
    when sessionmail says there is no such box (exit 2) or could not answer. A found
    box is kept for the process. Raises :class:`NoMessage` with no `message`."""
    had = _known.get(name)
    if had is not None:
        return had
    got = _run(["path", name], environ, PATH_TIMEOUT_S)
    if got is None or got[0] != 0:
        return None
    line = _last_line(got[1])
    if not line:
        return None
    found = Path(line).expanduser()
    _known[name] = found
    return found


def make(name: str, environ: Optional[Mapping[str, str]] = None) -> Path:
    """NAME's maildir, made by sessionmail if it is not there yet: `message path NAME`,
    else `message setup --box NAME` and `path` again. Raises :class:`NoMessage` with no
    `message`, :class:`NoBox` when setup did not make it (its last stderr line is quoted)."""
    found = path(name, environ)
    if found is not None:
        return found
    got = _run(["setup", "--box", name], environ, SETUP_TIMEOUT_S)
    found = path(name, environ)
    if found is not None:
        return found
    why = (_last_line(got[2]) or _last_line(got[1]) or f"exit {got[0]}") if got else "it could not run"
    raise NoBox(f"no box `{name}`: `message setup --box {name}` did not make it: {why}")


def _one_path(flag: str, environ: Optional[Mapping[str, str]]) -> Optional[Path]:
    """`message path FLAG` (sessionmail 0.3.3: one path on stdout, exit 0), or None when
    this sessionmail has no such flag (exit 1), has no such thing (exit 2) or could not
    answer. Raises :class:`NoMessage` with no `message`."""
    got = _run(["path", flag], environ, PATH_TIMEOUT_S)
    if got is None or got[0] != 0:
        return None
    line = _last_line(got[1])
    return Path(line).expanduser() if line else None


def root(environ: Optional[Mapping[str, str]] = None) -> Optional[Path]:
    """The mail root, `message path --root`, or None (see :func:`_one_path`)."""
    return _one_path("--root", environ)


def boxes_dir(environ: Optional[Mapping[str, str]] = None) -> Optional[Path]:
    """The folder the boxes live in, `message path --boxes`, or None."""
    return _one_path("--boxes", environ)


def generator(environ: Optional[Mapping[str, str]] = None) -> Optional[Path]:
    """This host's `vmailbox-gen`, `message path --generator` (sessionmail's own order:
    its pin, PATH, the copy under the root, the packaged one), or None when there is none
    or this sessionmail cannot say."""
    return _one_path("--generator", environ)


def notmuch_config(environ: Optional[Mapping[str, str]] = None) -> Optional[Path]:
    """The FILE of sessionmail's own notmuch profile (`notmuch --config=FILE`),
    `message path --notmuch-config`, or None before `message setup` wrote one or when
    this sessionmail cannot say."""
    return _one_path("--notmuch-config", environ)


def stores(environ: Optional[Mapping[str, str]] = None) -> list:
    """The store boxes on this host, by name, sorted: the `store NAME` rows of
    `message box list -n 0`. Empty when `message` could not answer. Raises
    :class:`NoMessage` with no `message`."""
    got = _run(["box", "list", "-n", "0"], environ, PATH_TIMEOUT_S)
    if got is None or got[0] != 0:
        return []
    names = []
    for line in (got[1] or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "store":
            names.append(parts[1])
    return sorted(set(names))
