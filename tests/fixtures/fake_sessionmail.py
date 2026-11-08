"""A fake sessionmail ``message`` for the tests (SM 0.3.3, the mail root move): the few
verbs sessionvoice asks, answered from a mail root in tmp, so no test spells the root.
(``fake_message.py`` beside it is the ears keeper's fake ``message send``: another thing.)

    python -I fake_sessionmail.py [ARG...]

The root is ``$FAKE_MESSAGE_ROOT``, else ``$HOME/.mail``. The boxes folder is
sessionmail's own rule: ``<root>/agents`` while that is a directory and
``<root>/stores`` is not, else ``<root>/stores``. A test lays boxes out by hand under
the fenced HOME and this answers for them, the way the real ``message`` would.

Verbs (stdout is ONE path or the rows, and nothing else; stderr says why not):

- ``path NAME`` / ``box path NAME``: ``<boxes>/NAME`` when its ``new/`` is a directory
  (or it is a store's folder, carrying ``.tm-store``); ``<boxes>/STORE/NAME`` for
  ``STORE/NAME``, or for a bare NAME nested under the first store that holds it; exit 2
  when none.
- ``path --root`` | ``--boxes``: the folder, exit 0. ``--generator``:
  ``<root>/.postfix/vmailbox-gen`` when it exists, else exit 2. ``--notmuch-config``:
  ``<root>/settings/notmuch/sessionkit/config`` when it exists, else exit 2.
- ``setup --box NAME``: makes ``<boxes>/NAME/{tmp,new,cur}`` 0700 (its parents too),
  prints a plan and an ``applying:`` row as sessionmail does, exit 0.
- ``box list [-n N] [--all]``: ``store NAME`` for each dir carrying ``.tm-store``,
  ``project STORE/NAME`` for each box under one, ``seat NAME`` for the rest; a
  session-id box only with ``--all``; a trailing ``-- N shown`` line.

Every call is appended as one JSON line to ``$FAKE_MESSAGE_LOG`` when that is set.
Stdlib only. Anything else is usage: exit 1.
"""
import json
import os
import re
import sys
from pathlib import Path

SUBDIRS = ("tmp", "new", "cur")
_SESSION = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def root() -> Path:
    return Path(os.environ.get("FAKE_MESSAGE_ROOT") or os.path.join(os.environ.get("HOME", ""), ".mail"))


def boxes_dir() -> Path:
    r = root()
    if (r / "agents").is_dir() and not (r / "stores").is_dir():
        return r / "agents"
    return r / "stores"


def stores() -> list:
    b = boxes_dir()
    return sorted(d for d in b.iterdir() if d.is_dir() and (d / ".tm-store").is_file()) if b.is_dir() else []


def is_box(p: Path) -> bool:
    """A maildir (its new/ is a directory), or a store box's folder (it carries
    .tm-store: sessionmail resolves a store by name whether or not it has mail)."""
    return (p / "new").is_dir() or (p / ".tm-store").is_file()


def resolve(name: str):
    b = boxes_dir()
    if "/" in name:
        p = b / name
        return p if is_box(p) else None
    p = b / name
    if is_box(p):
        return p
    for s in stores():
        if is_box(s / name):
            return s / name
    return None


def log(argv):
    f = os.environ.get("FAKE_MESSAGE_LOG")
    if f:
        with open(f, "a") as fh:
            fh.write(json.dumps({"argv": argv, "cwd": os.getcwd()}) + "\n")


def cmd_path(args) -> int:
    flags = [a for a in args if a.startswith("--") and a != "--why"]
    names = [a for a in args if not a.startswith("--")]
    if flags and names or len(flags) > 1 or len(names) > 1:
        print("Usage: message path [BOX] [--root|--boxes|--generator|--notmuch-config]", file=sys.stderr)
        return 1
    if flags:
        flag = flags[0]
        if flag == "--root":
            print(root()); return 0
        if flag == "--boxes":
            print(boxes_dir()); return 0
        if flag == "--generator":
            g = root() / ".postfix" / "vmailbox-gen"
            if g.is_file():
                print(g); return 0
            print(f"message path: no vmailbox-gen on this host (expected {g})", file=sys.stderr); return 2
        if flag == "--notmuch-config":
            c = root() / "settings" / "notmuch" / "sessionkit" / "config"
            if c.is_file():
                print(c); return 0
            print(f"message path: no notmuch profile yet (tried {c})", file=sys.stderr); return 2
        print(f"Usage: message path: no such option {flag}", file=sys.stderr); return 1
    if not names:
        print("Usage: message path BOX", file=sys.stderr); return 1
    found = resolve(names[0])
    if found is None:
        print(f"message path: no box '{names[0]}' under {boxes_dir()}", file=sys.stderr)
        return 2
    print(found)
    return 0


def cmd_setup(args) -> int:
    names = [args[i + 1] for i, a in enumerate(args) if a == "--box" and i + 1 < len(args)]
    dry = "--dry-run" in args
    b = boxes_dir()
    plan, applied = [f"  mail-root        {'DONE' if b.is_dir() else 'TODO'}     {b}"], []
    for n in names:
        p = b / n
        plan.append(f"  box:{n:<12} {'DONE' if is_box(p) else 'TODO'}     {p}")
        if not is_box(p) and not dry:
            for d in (p, *(p / s for s in SUBDIRS)):
                d.mkdir(parents=True, exist_ok=True, mode=0o700)
                d.chmod(0o700)
            applied.append(f"  box:{n:<12} made {p}")
    plan.append("  carrier          DONE     the mail drop (fake)")
    out = "steps:\n" + "\n".join(plan) + "\n"
    if dry:
        out += "dry run: nothing applied\n"
    elif applied:
        out += "applying:\n" + "\n".join(applied) + "\n"
    sys.stdout.write(out)
    return 0


def cmd_box_list(args) -> int:
    show_all = "--all" in args
    rows = []
    b = boxes_dir()
    if b.is_dir():
        for d in sorted(b.iterdir()):
            if not d.is_dir():
                continue
            if (d / ".tm-store").is_file():
                rows.append(f"store   {d.name}")
                for p in sorted(d.iterdir()):
                    if p.is_dir() and is_box(p):
                        rows.append(f"project {d.name}/{p.name}")
            elif _SESSION.match(d.name):
                if show_all:
                    rows.append(f"session {d.name}")
            elif is_box(d):
                rows.append(f"seat    {d.name}")
    pattern = next((a for a in args if not a.startswith("-") and a not in ("list",)
                    and not a.isdigit()), None)
    if pattern:
        rows = [r for r in rows if pattern.lower().strip("*") in r.lower()]
    sys.stdout.write("".join(r + "\n" for r in rows) + f"-- {len(rows)} shown\n")
    return 0


def main(argv) -> int:
    log(argv)
    if not argv:
        print("Usage: message <verb>", file=sys.stderr); return 1
    verb, rest = argv[0], argv[1:]
    if verb == "path":
        return cmd_path(rest)
    if verb == "box" and rest[:1] == ["path"]:
        return cmd_path(rest[1:])
    if verb == "box" and rest[:1] == ["list"]:
        return cmd_box_list(rest[1:])
    if verb == "setup":
        return cmd_setup(rest)
    print(f"fake sessionmail: verb not faked: {' '.join(argv)}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
