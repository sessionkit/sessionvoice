"""finder - run the host's configured finders for ``mouth play --find``, and parse
what they print.

Its only job is to find: it never plays and never queues, so it can be tested on
its own. Only the caller's process runs a finder, never the player, so a mailed
play can never reach one.

The finders are ``play_finders`` in the HOST FILE ALONE
(:func:`mouth.voices.finder_settings`): an ordered list of ``{"name", "argv"}``.

- The words are split on whitespace (``str.split()``, no shlex) and appended to a
  finder's ``argv`` as separate arguments. No shell, ever.
- Finders are asked in order, and the first one that finds anything wins; the rest
  are not run. ``name=`` asks that one finder only; an unknown name is refused.
- The contract: one match per line, tab-separated. The LAST field is a URL,
  ``file:///abs`` (three slashes) or ``http(s)://``; the earlier fields are the
  label, joined with `` | `` (the URL itself when there are none).
- Exit 0 with at least one line: found. Exit 1: none, whatever it printed, and the
  next finder is asked. Anything else, a timeout (``play_finder_timeout_s``), exit
  0 with nothing printed, or one line with no URL, is a :class:`FinderError` that
  names the finder and STOPS the chain: a broken finder never hands over silently.

A finder runs in a process group of its own. On a timeout the whole group is
killed (TERM, then KILL) and reaped, and its pipes are closed without being read to
the end, so neither a forked child nor one that left the group can hold the mouth.
"""
from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import voices

#: Seconds a timed-out finder's group has to go after TERM, before KILL.
KILL_GRACE_S = 1.0

#: The longest stderr tail a FinderError carries.
STDERR_TAIL = 200

_URL_PREFIXES = ("file:///", "http://", "https://")


class NoFinder(ValueError):
    """No ``play_finders`` entry is set in the host file (the CLI exits 2)."""


class FinderError(ValueError):
    """A finder failed: an exit other than 0 or 1, a timeout, no output on exit 0,
    a line with no URL, or it could not be run. Names the finder (the CLI exits 2)."""


class NotFound(ValueError):
    """Every finder asked answered none. Names the words and the finders asked (the
    CLI exits 1)."""


@dataclass
class Match:
    label: str
    url: str


@dataclass
class Found:
    finder: str
    matches: list[Match] = field(default_factory=list)
    asked: list[str] = field(default_factory=list)


def is_url(text: str) -> bool:
    """Whether TEXT is a URL the contract accepts: ``file:///`` (an absolute path),
    ``http://`` or ``https://``, the scheme in any case. ``mouth.play`` still checks
    it again (``fetch.classify``) when it plays."""
    return text.lower().startswith(_URL_PREFIXES)


def parse(name: str, out: str) -> list[Match]:
    """The matches in a finder's exit-0 output, [] when it printed none. Blank lines
    are skipped and a trailing CR is dropped. One line with no URL refuses the whole
    answer, naming the finder and the line."""
    matches = []
    for n, line in enumerate(out.split("\n"), 1):
        line = line.rstrip("\r")
        if not line.strip():
            continue
        fields = line.split("\t")
        url = fields[-1].strip()
        if not is_url(url):
            raise FinderError(f"mouth: finder {name} failed: printed a line with no URL (line {n})")
        label = " | ".join(f.strip() for f in fields[:-1] if f.strip())
        matches.append(Match(label=label or url, url=url))
    return matches


def _tail(err: bytes) -> str:
    """The last non-blank line of a finder's stderr, capped, or ""."""
    lines = [ln.strip() for ln in err.decode("utf-8", errors="replace").splitlines() if ln.strip()]
    return lines[-1][:STDERR_TAIL] if lines else ""


def _signal_group(proc: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _kill_group(proc: subprocess.Popen) -> None:
    """Kill PROC's whole process group, TERM then KILL, and reap PROC. Its pipes are
    closed unread: a grandchild that left the group may still hold them open."""
    _signal_group(proc, signal.SIGTERM)
    try:
        proc.wait(timeout=KILL_GRACE_S)
    except subprocess.TimeoutExpired:
        pass
    _signal_group(proc, signal.SIGKILL)     # any of the group that ignored TERM
    proc.wait()
    for pipe in (proc.stdout, proc.stderr):
        if pipe is not None:
            pipe.close()


def ask(name: str, args: list[str], timeout_s: float) -> list[Match]:
    """Run one finder (ARGS, no shell) and return its matches: [] for exit 1.
    Raises FinderError for anything else (see the module doc)."""
    try:
        proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, shell=False, start_new_session=True)
    except OSError as e:
        why = e.strerror or type(e).__name__
        raise FinderError(f"mouth: finder {name} failed: could not run: {args[0]} ({why})") from None
    try:
        out, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        raise FinderError(f"mouth: finder {name} failed: timed out after {timeout_s:g}s") from None
    rc = proc.returncode
    if rc == 1:
        return []
    tail = _tail(err)
    if rc == 0:
        matches = parse(name, out.decode("utf-8", errors="replace"))
        if matches:
            return matches
        why = "exited 0 but printed no match"
    else:
        why = f"exit {rc}" if rc >= 0 else f"killed by signal {-rc}"
    raise FinderError(f"mouth: finder {name} failed: {why}" + (f": {tail}" if tail else ""))


def find(words: str, *, name: Optional[str] = None, d: Optional[Path] = None) -> Found:
    """Ask the host's finders for WORDS, in order, and return the first answer.

    Raises ValueError for empty words or an unknown ``name``, :class:`NoFinder`
    when the host file sets none, :class:`FinderError` when one fails (the chain
    stops there), and :class:`NotFound` when every finder asked answers none."""
    terms = words.split() if isinstance(words, str) else []
    if not terms:
        raise ValueError("mouth: --find needs words")
    s = voices.finder_settings(d)
    finders = s["finders"]
    if not finders:
        raise NoFinder(f"mouth: no finder: set play_finders in the host's mouth.json ({s['path']})")
    if name is not None:
        chosen = [f for f in finders if f[0] == name]
        if not chosen:
            have = ", ".join(n for n, _ in finders)
            raise ValueError(f"mouth: no finder named {name} (have: {have})")
        finders = chosen
    asked = []
    for fname, argv in finders:
        asked.append(fname)
        matches = ask(fname, [*argv, *terms], s["timeout_s"])
        if matches:
            return Found(finder=fname, matches=matches, asked=asked)
    raise NotFound(f"mouth: not found: {' '.join(terms)} (asked: {', '.join(asked)})")
