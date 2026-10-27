"""sessionvoice-web: Talk as a room in the kit's web page.

  sessionvoice-web web-room
      print this package's room descriptor, one line of JSON, and exit 0

The kit's web front door finds a package's room by one function, `room()`,
reached two ways:

1. In the front door's own environment, through the `sessionkit.web`
   entry-point group (`talk = "talk.web:room"`). The front door loads it and
   calls it; nothing is run.
2. From another environment, through this command: `sessionvoice-web
   web-room` prints `json.dumps(room())`.

Both give the same dict: a name, a title, the package, and the command the
front door runs to start the room, `talk serve --kit` on the unix socket it
names. The command starts with the interpreter of the process that called
`room()`, read at that moment, so it runs in the environment that can import
`talk`, and nothing looks `talk` up on PATH. It runs that interpreter with
`-I` (isolated): the front door's working directory, the user site and the
PYTHON* variables are left out, so a `talk.py` lying in that directory is
never what is served. `talk` must be installed in the interpreter's own
environment (a venv, `uv tool` or `pipx`).

Stdlib only, and nothing else of talk is imported: this answers well within
the front door's 2 s, with no audio or model code loaded.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

NAME, TITLE, PACKAGE = "talk", "Talk", "sessionvoice"
#: The module the room runs. A rename of the module is this line.
MODULE = "talk"
#: The room's command after the interpreter, defined once. "{socket}" is the
#: front door's placeholder for the unix socket it fills in.
SERVE = ("-I", "-m", MODULE, "serve", "--kit", "--unix", "{socket}")
PROG = "sessionvoice-web"


def room() -> dict:
    """This package's room descriptor. The interpreter is read now, in the
    process that calls this, never at import and never resolved through a
    symlink (a venv's python finds its venv by its own path)."""
    exe = sys.executable
    if not exe or not os.path.isabs(exe):
        raise RuntimeError("talk.web.room: sys.executable is not an absolute path "
                           f"({exe!r}), so the room's command could not run")
    return {"name": NAME, "title": TITLE, "package": PACKAGE, "command": [exe, *SERVE]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=PROG, description="Talk as a room in the kit's web page.")
    verbs = parser.add_subparsers(dest="verb", metavar="web-room")
    verbs.add_parser("web-room", help="print the room descriptor as one line of JSON",
                     description="Print the room descriptor as one line of JSON.")
    args = parser.parse_args(argv)
    if args.verb is None:
        parser.print_usage(sys.stderr)
        print(f"{PROG}: error: a verb is required (web-room)", file=sys.stderr)
        return 2
    try:
        desc = room()
    except RuntimeError as e:
        print(f"{PROG}: {e}", file=sys.stderr)
        return 1
    print(json.dumps(desc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
