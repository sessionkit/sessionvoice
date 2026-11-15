"""A fake finder for ``mouth play --find``: speaks the finder contract with the
standard library only, and searches nothing.

    python fake_finder.py [OPTIONS] -- WORDS...

A test writes ``[<python>, tests/fixtures/fake_finder.py, OPTIONS..., "--"]`` as a
finder's ``argv`` in the test host's ``mouth.json``; the mouth appends the words
after the ``--``. Its own options, each before the ``--``:

- ``--print LINE``: print LINE on stdout (repeatable, in order).
- ``--stderr TEXT``: print TEXT on stderr.
- ``--exit N``: exit with N (default 0).
- ``--sleep S``: sleep S seconds after printing, before exiting.
- ``--fork``: leave a child sleeping in the finder's own process group (as a
  shell-script finder leaves its ``mdfind``), holding stdout open.
- ``--escape``: leave a child that starts a session of its own (``setsid``) and
  sleeps holding stdout open: the group kill cannot reach it. The test kills it by
  its logged pid.
- ``--log PATH``: append one JSON line: ``words`` (the arguments after ``--``,
  exactly as received), ``pid``, and ``child`` (the forked pid, or null).

The child of ``--fork`` / ``--escape`` sleeps 60 s, so a test that forgets it
does not leave it for long.
"""
from __future__ import annotations

import json
import os
import sys
import time

CHILD_SLEEP_S = 60


def main(argv: list[str]) -> int:
    opts, words = argv, []
    if "--" in argv:
        i = argv.index("--")
        opts, words = argv[:i], argv[i + 1:]
    prints, stderr, code, sleep, fork, escape, log = [], [], 0, 0.0, False, False, None
    it = iter(opts)
    for o in it:
        if o == "--print":
            prints.append(next(it))
        elif o == "--stderr":
            stderr.append(next(it))
        elif o == "--exit":
            code = int(next(it))
        elif o == "--sleep":
            sleep = float(next(it))
        elif o == "--fork":
            fork = True
        elif o == "--escape":
            escape = True
        elif o == "--log":
            log = next(it)
        else:
            print(f"fake_finder: unknown option {o}", file=sys.stderr)
            return 64
    child = None
    if fork or escape:
        child = os.fork()
        if child == 0:
            if escape:
                os.setsid()
            time.sleep(CHILD_SLEEP_S)
            os._exit(0)
    if log:
        with open(log, "a") as f:
            f.write(json.dumps({"words": words, "pid": os.getpid(), "child": child}) + "\n")
    for line in prints:
        sys.stdout.write(line + "\n")
    sys.stdout.flush()
    for line in stderr:
        sys.stderr.write(line + "\n")
    sys.stderr.flush()
    if sleep:
        time.sleep(sleep)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
