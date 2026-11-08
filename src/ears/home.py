"""home - where sessionvoice keeps its state: :func:`state_dir`.

sessionvoice began inside VoiceMode and kept its state under ``~/.voicemode``; 0.3.2 and
older made ``~/.sessionvoice``. From 0.3.3 the state lives in the kit, package first:
``~/.sessionkit/voice/state`` (:data:`NEW`). Nothing is moved here: a host whose heard log
is already in an old root keeps using it there ("adopt in place"). Moving it is a separate,
hand-run step per host (``ears home migrate``, wave 4).

The root is the first of:

1. ``$SESSIONVOICE_HOME``, if it is set;                                       (override)
2. ``$VOICEMODE_BASE_DIR``, if it is set (the old name, still read);           (override)
3. ``~/.sessionvoice``, if it is POPULATED and REAL;                           (old)
4. ``~/.sessionkit/voice/state``, if it is POPULATED;                          (new)
5. ``~/.voicemode``, if it is POPULATED and REAL;                              (old)
6. otherwise ``~/.sessionkit/voice/state``.                                    (fresh-new)

The words in brackets are the SOURCE (:func:`state_source`), tools' words, as
``python -m ears.home state-dir --json`` prints them. A variable set to the empty string
counts as not set, as ``VOICEMODE_BASE_DIR`` always did. The resolver makes nothing.

POPULATED means ``<root>/logs/conversations`` holds a ``heard.lock`` or a
``heard_*.jsonl`` that is a REAL file: a symlink (into another root, say) is not that
root's own log. That directory is where the heard log lives, and a ``seq`` names one line
across days only while every writer uses the same one. A directory that exists but holds
no heard log was made by hand, by an installer or by a stray ``mkdir``, and it never
decides: not even for the new root, so a pre-made ``~/.sessionkit/voice/state`` cannot pull
a live ``~/.voicemode`` host out of its root.

REAL means the old folder is not the new root itself (``os.path.samefile``): once a migrate
has left ``~/.sessionvoice`` as a symlink to the new root, the old path is never handed out
(talk refuses a symlinked token folder).

Our own old root (3) wins over the new one while it is real: only 0.3.2 or older makes it.
``~/.voicemode`` (5) never outranks a populated new root: VoiceMode's ``converse`` can fill
it at any time, and that must not pull a host out of the kit.

When two or more of the three hold a heard log the log is split. The resolver picks the
first in the order above and ``ears services status`` says so loudly (:func:`split`).

On a FRESH host that also runs VoiceMode's ``converse`` as a heard guest (it writes
``~/.voicemode`` and reads only ``VOICEMODE_BASE_DIR``), set
``SESSIONVOICE_HOME=~/.voicemode`` (or ``VOICEMODE_BASE_DIR``) so that both write one
log. Otherwise whichever writes first decides: converse first, and sessionvoice adopts
``~/.voicemode``; sessionvoice first, and it stays in the kit while converse's lines split
off there. The split warning names this fix (:data:`FIX`). Nothing here detects VoiceMode.

Under the root nothing changes: ``logs/conversations/heard_*.jsonl``, ``heard.lock``,
``speakers_*``, ``audio/``, ``mouth/``, ``talk/`` and ``state/`` (``logs/install.log`` was
there before 0.3.2; it is in the log folder now, below). VoiceMode's own files in
``~/.voicemode`` (``voicemode.env``, ``voices/``, ``services/``, ``exchanges_*``) are
VoiceMode's, and this module never names them.

The ITEMS are the state 0.3.2 kept outside the root (:data:`ITEMS`, design.md D3): call-tap's
and call-ears' folders, the audio devices file, the speaker enrolment and ears-mail's
``--state``. Each is its own variable, else its OLD path while that is REAL, else
``<root>/<name>`` (:func:`state_path`), the root taken with the two root overrides left out,
because the launchd jobs that use the items carry none. The speaker side-car's venv stays
outside (:data:`SPEAKERS_VENV`).

A SEAT's home (its heard-first ``heard.state``) is :func:`seat_home` (design.md D5):
``$EARS_HOME``; else the seat's home in tools, ASKED of tools (``sessiontools.settings``, else
``session path agent``), never derived; else ``~/.<seat>``; else ``~/.ears`` while it is real
(0.3.2's shared home, read only); else voice's own ``<root>/seats/<seat>/`` (the root with the
root overrides left out), which ears-arm makes. A fresh host makes no ``~/.ears``. Tools'
old folders are read only when tools cannot be asked at all (:data:`TOOLS_OLD`).
``seat_names()`` lists the seats with a ``heard.state``, and ``avatar()`` finds a face.

Shell and peers ask: ``python -m ears.home state-dir [ITEM] [--json]`` (also run as a file,
``python src/ears/home.py``, and as ``ears home state-dir``). ``--json`` prints the contract
the kit's peers parse: ``{"version", "root": {"path", "source"}, "items": {name:
{"path", "source"}}}``. ``version`` is this package's (:func:`version`): a peer may cache
the answer on it.

Standard library only. Several scripts import nothing from this package (they run under
any ``python3``, or as shell), and none of them keeps a copy of this rule:
the scripts in ``keeper/``, ``bin/``, ``view/``, ``shadow/``, ``speakers/`` and
``skills/ears/scripts/`` load this file by its path and call :func:`asked_state_dir`, and
the keeper's shell asks ``python -m ears.home state-dir``. ONE copy is left, in
``src/ears/heard.py``: it runs standalone under any ``python3`` (a guest's hook, and
sessionphone loads a copy of it by path), and ``tests/test_home.py`` holds it to this module
under fake HOMEs and scans for any other.

A plugin script ASKS THE PACKAGE (design.md D4.1, open question 7's ruling):
:func:`asked_state_dir` returns the package's answer when it agrees with this file's, and
otherwise says why on stderr, naming both, and exits 2. When this file is the package's
own (an installed wheel, or the file the running python imports) no process is started;
else it runs ``ears home state-dir --json`` once per process (``$EARS_BIN``, the checkout's
``.venv/bin/ears``, ``ears`` on PATH, uv's tool installs). Shell reaches it as ``python3
home.py asked-state-dir --as PROG``.

The SETTINGS are not under the root. They live in the kit's settings home, one folder
for the whole package (the ears and the mouth alike), package first:
``~/.sessionkit/voice/settings/`` (:data:`SETTINGS`, :func:`settings_dir`). A file per
command sits in it under its own name: ``ears.json``, ``wake.json``, ``ears-sounds.json``,
``stt.key``, ``mouth.json``, ``mouth-sounds.json``, ``tts.key``. It is the kit's map,
``<kit root>/<package>/settings`` with the ears and the mouth both in ``voice``. No
variable moves the folder; the variables that name one file each (``$EARS_CONF`` and its
kin) still do. The stdlib-only scripts keep a copy of :data:`SETTINGS`, and
``tests/test_settings_dir.py`` holds every copy to it.

Version 0.1.0 kept these files in two other folders (:data:`LEGACY_SETTINGS`, by file
:data:`LEGACY_FOLDER`). A file that is absent from the settings folder while its 0.1.0
copy is still there is STRANDED (:func:`stranded`): the host step that moves the 0.1.0
settings into the new folder has not run. That is not a fallback. The old file is never
opened; its existence alone (a stat) is checked. Readers use their defaults, as for a
missing file, and say so in ONE line naming both paths (:func:`warn_stranded`, once per
process, on stderr, from the ears, mouth and talk commands). Writers write NOTHING to a
stranded file (:func:`refuse_stranded`), so the move finds no second copy in its way.
The stdlib-only scripts carry no copy of the old folders and say nothing.

The LOGS are not under the root either. Every voice log a writer keeps by default (the
launch agents' stdout and stderr, the keeper's own logs, the sounds' logs, ``install.log``,
the mouth's ``player.log`` and ``split.log``) goes in ONE folder, the kit's log home beside
the settings: ``~/.sessionkit/voice/log/`` (:data:`LOG`, :func:`log_dir`). ``$EARS_LOG_DIR``
moves the folder, and each variable that names one log file (``$EARS_SOUNDS_LOG``,
``$MOUTH_SOUNDS_LOG``) still moves that file. This module is the ONE place the folder is
named. There are no copies: a script that imports nothing from this package loads this
file by its path and calls :func:`log_dir` (it is standard library only), the keeper's
shell asks ``python -m ears.home log-dir``, and ``bin/render-plist`` fills the templates'
``@EARS_LOG_DIR@`` from it. ``tests/test_log_dir.py`` scans for any other.

NOT logs, and not in that folder: the heard log and ``heard.lock`` (``<root>/logs/
conversations``, ``ears.heard.log_dir``, a different folder), the speakers' files, state,
audio, caches and venvs. Before 0.3.2 the logs went to ``~/Library/Logs/ears`` (and a hand
render's to ``~/Library/Logs/failmode``, :data:`LEGACY_LOGS`). Nothing moves or deletes
them. ``ears services status`` says in one line where the logs are now while an old folder
still holds a file (:func:`log_status_lines`).
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Mapping, NamedTuple, Optional

#: The variables that name the root, read in this order.
ENV = ("SESSIONVOICE_HOME", "VOICEMODE_BASE_DIR")
#: The state root, under $HOME: the kit's state home, package first. The fresh
#: default, and step 4 of the order once it holds a heard log.
NEW = ".sessionkit/voice/state"
#: The old roots, under $HOME, in the order they are adopted: our own (0.3.2 and older, step
#: 3) and VoiceMode's (step 5). Each is adopted in place while POPULATED and REAL.
OLD_ROOTS = (".sessionvoice", ".voicemode")
#: The source words :func:`state_source` returns, tools' words (``session path --json``).
SOURCES = ("override", "old", "new", "fresh-new")
#: The fix the split warning names: a host that also runs
#: VoiceMode's converse as a heard guest points sessionvoice at converse's folder by hand.
#: Nothing here looks for VoiceMode.
FIX = "SESSIONVOICE_HOME=~/.voicemode (or VOICEMODE_BASE_DIR), so that both write one log"
#: The package's settings folder, under $HOME: the kit's settings home, package first.
#: The ONE place this repo's Python names it; the copies are held to it by tests.
SETTINGS = ".sessionkit/voice/settings"
#: The package's log folder, under $HOME: the kit's log home, beside :data:`SETTINGS`.
#: The ONE place this repo names it; nothing copies it (:func:`log_dir`).
LOG = ".sessionkit/voice/log"
#: The variable that moves the log folder.
LOG_ENV = "EARS_LOG_DIR"
#: Where the logs went before 0.3.2, under $HOME. Only ever LOOKED at, for the pointer in
#: ``ears services status``: nothing here writes, moves or deletes a file in them.
LEGACY_LOGS = ("Library/Logs/ears", "Library/Logs/failmode")
#: Where version 0.1.0 kept the voice settings files, under $HOME, by folder. The ONE place
#: this repo names them. Used ONLY to build the stranded line: never opened, never written,
#: never a fallback. The stdlib-only scripts carry no copy.
LEGACY_SETTINGS = {"ears": ".settings/ears", "mouth": ".settings/mouth"}
#: Each voice settings file, and the :data:`LEGACY_SETTINGS` folder it sat in under 0.1.0.
LEGACY_FOLDER = {
    "ears.json": "ears", "wake.json": "ears", "ears-sounds.json": "ears", "stt.key": "ears",
    "mouth.json": "mouth", "mouth-sounds.json": "mouth", "tts.key": "mouth",
}
#: The stranded line, after ``<command>: ``. Formatted with {new} and {old}.
STRANDED = ("{new} is missing and {old} still exists, so nothing is read from it and nothing "
            "is written: the defaults apply until the host step that moves the 0.1.0 settings "
            "into the new folder has run.")
#: What the line means, for each command's --help.
STRANDED_HELP = ("A line '<new file> is missing and <old file> still exists' on stderr means a "
                 "settings file from version 0.1.0 has not been moved: it is not read, nothing is "
                 "written in its place, and the defaults apply until the host step that moves the "
                 "0.1.0 settings has run (docs/settings.md, upgrading from 0.1.0).")


def _real_file(p: Path) -> bool:
    """A regular file that is not a symlink: a link into another root is not this root's log."""
    try:
        return p.is_file() and not p.is_symlink()
    except OSError:
        return False


def populated(root) -> bool:
    """True when ``<root>/logs/conversations`` holds a ``heard.lock`` or a ``heard_*.jsonl``
    that is a REAL file (not a symlink). A look, never a write."""
    d = Path(root) / "logs" / "conversations"
    try:
        return _real_file(d / "heard.lock") or any(_real_file(p) for p in d.glob("heard_*.jsonl"))
    except OSError:
        return False


def _same(a, b) -> bool:
    """``a`` and ``b`` are one folder (``os.path.samefile``); False when either is missing."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _home(home) -> Path:
    return Path(home) if home is not None else Path(os.path.expanduser("~"))


def settings_dir(home=None) -> Path:
    """``~/.sessionkit/voice/settings``: the folder every voice settings file sits in.
    ``home`` defaults to the user's home (``$HOME``, read on every call)."""
    return _home(home) / SETTINGS


def log_dir(environ: Optional[Mapping[str, str]] = None, home=None) -> Path:
    """The folder every voice log goes in by default: ``$EARS_LOG_DIR`` when it is set
    (and not empty), else ``~/.sessionkit/voice/log``. ``environ`` defaults to
    ``os.environ`` and ``home`` to the user's home (``$HOME``), both read on every call.
    Nothing is made: a writer makes the folder when it writes."""
    env = os.environ if environ is None else environ
    raw = env.get(LOG_ENV)
    if raw:
        return Path(os.path.expanduser(os.path.expandvars(raw)))
    return _home(home) / LOG


def old_logs(home=None) -> list[Path]:
    """Each :data:`LEGACY_LOGS` folder that still holds a file (at any depth). A look,
    never a write: the old logs stay where they are."""
    h, found = _home(home), []
    for rel in LEGACY_LOGS:
        d = h / rel
        try:
            if d.is_dir() and any(p.is_file() for p in d.rglob("*")):
                found.append(d)
        except OSError:
            continue
    return found


def log_status_lines(environ: Optional[Mapping[str, str]] = None, home=None) -> list[str]:
    """``ears services status``'s log row, and ONE pointer line while an old log folder
    still holds files: they are left in place, and new logs go to :func:`log_dir`."""
    env = os.environ if environ is None else environ
    new = log_dir(env, home)
    why = f"${LOG_ENV}" if env.get(LOG_ENV) else "the default"
    rows = [f"{'logs':6s} {new} ({why})"]
    old = old_logs(home)
    if old:
        rows.append(f"{'':6s} the logs are now in {new}; older ones stay in "
                    f"{' and '.join(map(str, old))} (left in place, never deleted)")
    return rows


def legacy_path(name: str, home=None) -> Path:
    """Where the voice settings file NAME sat under 0.1.0 (:data:`LEGACY_FOLDER`)."""
    return _home(home) / LEGACY_SETTINGS[LEGACY_FOLDER[name]] / name


def stranded(name: str, home=None) -> Optional[tuple[Path, Path]]:
    """``(new, old)`` when the voice settings file NAME is STRANDED: nothing is at its path
    in the settings folder (not even a dangling link) and its 0.1.0 copy exists; else None.
    A stat, never a read: a refusal, not a fallback. A file 0.1.0 never had (talk.json)
    has no 0.1.0 copy, so it is never stranded."""
    if name not in LEGACY_FOLDER:
        return None
    new, old = settings_dir(home) / name, legacy_path(name, home)
    if not os.path.lexists(new) and old.exists():
        return new, old
    return None


def stranded_line(name: str, home=None) -> Optional[str]:
    """The stranded line for NAME, without the command's name, or None."""
    both = stranded(name, home)
    return STRANDED.format(new=both[0], old=both[1]) if both else None


#: The stranded pairs this process has already named: the line is said once per process.
_said: set[tuple[str, str]] = set()


def _say(prog: str, name: str, home=None, file=None) -> Optional[str]:
    both = stranded(name, home)
    if not both:
        return None
    key = (str(both[0]), str(both[1]))
    if key in _said:
        return None
    _said.add(key)
    line = f"{prog}: {STRANDED.format(new=both[0], old=both[1])}"
    print(line, file=sys.stderr if file is None else file, flush=True)
    return line


def warn_stranded(prog: str, home=None, file=None) -> list[str]:
    """Say the stranded line, on stderr, for every stranded voice settings file this
    process has not named yet. Returns the lines said. Nothing is read or written."""
    return [line for name in LEGACY_FOLDER for line in [_say(prog, name, home, file)] if line]


def refuse_stranded(path, name: str, prog: str = "ears", home=None) -> Optional[str]:
    """For a WRITER of the voice settings file NAME at PATH. When PATH is NAME's own path in
    the settings folder and the file is stranded: say the stranded line (once per process)
    and return the refusal to raise; the writer writes nothing. Else None: a file a
    variable names elsewhere, or one that is not stranded, is written as before."""
    new = settings_dir(home) / name
    if Path(os.path.expanduser(str(path))) != new or not stranded(name, home):
        return None
    _say(prog, name, home)
    return f"{new} is stranded (the line above): nothing written"


def state_source(environ: Optional[Mapping[str, str]] = None,
                 home=None) -> tuple[Path, str]:
    """sessionvoice's state root and its SOURCE (``override``, ``old``, ``new`` or
    ``fresh-new``), by the order in this module's docstring. Nothing is made.

    ``environ`` defaults to ``os.environ`` and ``home`` to the user's home (``$HOME``), both
    read on every call. ``environ={}`` leaves the two root overrides out."""
    env = os.environ if environ is None else environ
    for name in ENV:
        raw = env.get(name)
        if raw:
            return Path(os.path.expanduser(os.path.expandvars(raw))), "override"
    h = _home(home)
    new = h / NEW
    sv, vm = (h / r for r in OLD_ROOTS)
    if populated(sv) and not _same(sv, new):
        return sv, "old"
    if populated(new):
        return new, "new"
    if populated(vm) and not _same(vm, new):
        return vm, "old"
    return new, "fresh-new"


def state_dir(environ: Optional[Mapping[str, str]] = None, home=None) -> Path:
    """sessionvoice's state root (:func:`state_source`, without the source)."""
    return state_source(environ, home)[0]


def sessionvoice_home(environ: Optional[Mapping[str, str]] = None, home=None) -> Path:
    """The old name of :func:`state_dir`, kept for one release: tests and outside importers
    call it."""
    return state_dir(environ, home)


# --- the items: state 0.3.2 kept outside the root (the strays, design.md D3) -------------------
#
# Each item has ONE rule: its own override, else its OLD path while that path is REAL, else
# <root>/<name>. The root here is state_dir(environ={}): it leaves the two ROOT overrides
# out in every caller, because a launchd job uses each item and no job of ours carries one
# (design.md D3, D8). The item's own override, and XDG_STATE_HOME (which only LOCATES an
# old path), are still read from the caller's environment. A live host keeps each item where
# it is; a fresh host writes none of them outside the kit. Nothing is made here.


class Item(NamedTuple):
    #: The variable that names the item's path (it wins), or None: ears-mail's state has
    #: none of its own (its ``--state`` flag is the override, and the jobs pass it).
    env: Optional[str]
    #: Where its OLD path is: under ``$XDG_STATE_HOME`` (else ``~/.local/state``) when
    #: ``xdg``, else under $HOME.
    xdg: bool
    #: The OLD path, under that folder. Only ever LOOKED at: adopted in place while REAL.
    old: str
    #: Its name under the root.
    name: str
    #: ``dir`` or ``file``: what a REAL old path must be.
    kind: str


#: The item table: the ONE place each stray's name, override and old path are written down.
#: ``tests/test_state_items.py`` scans for any old path written anywhere else.
ITEMS = {
    # bin/call-tap's run folder (STOP, STOP-ear, LISTEN-OFF, capture.pid); the keeper touches
    # LISTEN-OFF in it (TAP_RUN)
    "call-tap": Item("EARS_CALL_TAP_RUN", True, "ears/call-tap", "call-tap", "dir"),
    # keeper/call-ears' folder (STOP, live, capture-N.log); the keeper exports it
    "call-ears": Item("CALL_EARS_DIR", True, "ears/call-ears", "call-ears", "dir"),
    # the audio devices file the keeper's input-watch writes (docs/audio-devices.md). Not
    # <root>/audio/: that folder is turn audio (design.md D2)
    "audio-devices": Item("AUDIO_DEVICES_FILE", True, "audio/devices.json",
                          "audio-devices.json", "file"),
    # the speaker enrolment: refs/, refs-call/, enrol/, STOP and the side-car's log. Private
    # (install and enrol make it 0700). The side-car's venv is NOT in it: SPEAKERS_VENV
    "speakers": Item("EARS_SPEAKERS_HOME", False, ".local/share/ears/speakers", "speakers",
                     "speakers"),
    # ears-mail's place in the heard log (its --state); ears-mail makes no folder
    "ears-mail-state": Item(None, False, ".cache/ears/ears-mail.state.json",
                            "ears-mail.state.json", "file"),
}
#: The speaker side-car's venv, under $HOME. It STAYS here, beside the speech and separator
#: venvs (venvs stay by ruling): it never follows the enrolment into the kit (design.md D3,
#: the speakers split). Named beside the table because it shares the old speakers folder.
SPEAKERS_VENV = ".local/share/ears/speakers/venv"


def _item(name: str) -> Item:
    try:
        return ITEMS[name]
    except KeyError:
        raise KeyError(f"no state item {name!r} (items: {', '.join(sorted(ITEMS))})") from None


def item_old(name: str, environ: Optional[Mapping[str, str]] = None, home=None) -> Path:
    """Where item NAME lived before 0.3.3: under ``$XDG_STATE_HOME`` (else
    ``~/.local/state``) or $HOME. A path, never a look."""
    it, env = _item(name), os.environ if environ is None else environ
    h = _home(home)
    if it.xdg:
        base = Path(os.path.expanduser(env.get("XDG_STATE_HOME") or str(h / ".local" / "state")))
    else:
        base = h
    return base / it.old


def _real_old(it: Item, old: Path, new: Path) -> bool:
    """The old path is REAL: it is what the item is (a folder, or a file), and it is not the
    item's new path itself (a symlink into the new place, as a migrate leaves it, is not).
    The speakers folder is real only while it holds an entry that is not a link, other than
    ``venv/``: a fresh host's venv, or a migrated folder's links, never adopt it."""
    try:
        if _same(old, new):
            return False
        if it.kind == "file":
            return old.is_file()
        if not old.is_dir():
            return False
        if it.kind == "speakers":
            return any(p.name != "venv" and not p.is_symlink() for p in old.iterdir())
        return True
    except OSError:
        return False


def item_source(name: str, environ: Optional[Mapping[str, str]] = None,
                home=None) -> tuple[Path, str]:
    """Item NAME's path and its SOURCE, by the rule above: ``override`` (its own variable),
    ``old`` (its old path, adopted while REAL; or its place under an adopted old root),
    ``new`` (its place under the kit's root, already there) or ``fresh-new`` (that place,
    not made yet). ``environ`` gives the item's own variable and the XDG folder (item_old); the root
    always leaves the two root overrides out. Nothing is made."""
    it, env = _item(name), os.environ if environ is None else environ
    raw = env.get(it.env) if it.env else None
    if raw:
        return Path(os.path.expanduser(raw)), "override"
    root, root_src = state_source({}, home)
    new = root / it.name
    old = item_old(name, env, home)
    if _real_old(it, old, new):
        return old, "old"
    if root_src == "old":
        return new, "old"
    return new, ("new" if os.path.lexists(new) else "fresh-new")


def state_path(name: str, environ: Optional[Mapping[str, str]] = None, home=None) -> Path:
    """Item NAME's path (:func:`item_source`, without the source)."""
    return item_source(name, environ, home)[0]


def state_items(environ: Optional[Mapping[str, str]] = None,
                home=None) -> dict[str, tuple[Path, str]]:
    """Each item (:data:`ITEMS`), as ``{name: (path, source)}``: the ``items`` of the
    ``--json`` contract."""
    return {n: item_source(n, environ, home) for n in ITEMS}


def asked_state_path(name: str, prog: str, environ: Optional[Mapping[str, str]] = None,
                     home=None) -> Path:
    """Item NAME's path for a plugin script named PROG. Its own variable names it for both
    sides, so nothing is asked then. Else the package is asked for the item's root (the root
    overrides left out, as the item's rule has it) and this file's answer is used only when
    the two agree: else ONE line on stderr and exit 2 (:func:`asked_state_dir`)."""
    it, env = _item(name), dict(os.environ if environ is None else environ)
    if it.env and env.get(it.env):
        return state_path(name, env, home)
    asked_state_dir(prog, {k: v for k, v in env.items() if k not in ENV}, home)
    return state_path(name, env, home)


def version() -> str:
    """This package's version, for the ``--json`` contract (a peer caches on it). A checkout
    answers from its own ``pyproject.toml`` (the code that is answering), an installed
    wheel from its metadata; ``unknown`` when neither can be read."""
    pp = Path(__file__).resolve().parents[2] / "pyproject.toml"
    try:
        text = pp.read_text()
        if re.search(r'^name\s*=\s*"sessionvoice"', text, re.M):
            m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
            if m:
                return m.group(1)
    except (OSError, UnicodeDecodeError):
        pass
    try:
        from importlib.metadata import version as _dist_version
        return _dist_version("sessionvoice")
    except Exception:
        return "unknown"


def state_json(environ: Optional[Mapping[str, str]] = None, home=None) -> dict:
    """The ``--json`` contract the kit's peers parse::

        {"version": "0.3.3", "root": {"path": "...", "source": "fresh-new"},
         "items": {"<name>": {"path": "...", "source": "..."}}}
    """
    root, source = state_source(environ, home)
    return {
        "version": version(),
        "root": {"path": str(root), "source": source},
        "items": {n: {"path": str(p), "source": src}
                  for n, (p, src) in state_items(environ, home).items()},
    }


def split(home=None) -> Optional[tuple[Path, ...]]:
    """The roots that each hold a heard log, in the order of the rule, when there are two or
    more different folders of the three (``~/.sessionvoice``, ``~/.sessionkit/voice/state``,
    ``~/.voicemode``); else None. The first is the one the rule picks.

    The env is not consulted: a root set by a variable decides for this process, but a
    writer started without it still picks between these. A folder that is the same as one
    already counted (an old root left as a symlink to the new one) is no second root, and
    neither is a root whose ``logs/conversations`` IS one already counted's (a folder link
    between the two, made by hand): one folder, one ``heard.lock``, one log (design.md
    D7.10). The root the rule picks does not change."""
    h = _home(home)
    found: list[Path] = []
    for r in (h / OLD_ROOTS[0], h / NEW, h / OLD_ROOTS[1]):
        if populated(r) and not any(_same(r, f) or _same(_heard(r), _heard(f)) for f in found):
            found.append(r)
    return tuple(found) if len(found) >= 2 else None


def _heard(root) -> Path:
    """The folder under ROOT that holds the heard log."""
    return Path(root) / "logs" / "conversations"


def saved_offset(saved: Mapping, path, size: int) -> int:
    """Where a reader of the heard file PATH (``size`` bytes now) resumes, from the offsets
    it SAVED, keyed by path (heard-first's ``heard.state``, ears-mail's ``--state``). Its
    own key first; on a miss, the key that is the same file (``os.path.samefile``): after
    ``ears home migrate`` the old path still reaches the file through the link it left, so
    a re-armed reader resumes where it was and nothing is rewritten (design.md D7.6). A key
    whose path is gone is skipped. No offset, or one past the end: ``size``, the end (only
    what is heard from now on)."""
    off = saved.get(str(path)) if isinstance(saved, Mapping) else None
    if off is None and isinstance(saved, Mapping):
        for key, val in saved.items():
            try:
                if os.path.samefile(key, path):
                    off = val
                    break
            except (OSError, TypeError, ValueError):
                continue
    try:
        return off if off is not None and 0 <= off <= size else size
    except TypeError:
        return size


def _and(paths) -> str:
    names = [str(p) for p in paths]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


#: Why the root is the root, for the ``state`` row, by source (an override names its variable).
WHY = {"old": "has the heard log; an old root, adopted in place",
       "new": "has the heard log",
       "fresh-new": "the default; no heard log yet"}


def still_old(environ: Optional[Mapping[str, str]] = None, home=None) -> list[Path]:
    """What ``ears home migrate`` would move on this host, as paths: the root when it is an old
    one adopted in place, then each item whose OLD path is still real (the strays a host keeps
    until its wave-4 step). Empty on a fresh host, after a migrate, and when a root override
    is set (the root was set by hand, and the migrate refuses it). A look, never a write."""
    env = os.environ if environ is None else environ
    root, source = state_source(env, home)
    if source == "override":
        return []
    out = [root] if source == "old" else []
    for name in ITEMS:
        path, src = item_source(name, env, home)
        if src == "old" and path == item_old(name, env, home):
            out.append(path)
    return out


def status_lines(environ: Optional[Mapping[str, str]] = None, home=None) -> list[str]:
    """``ears services status``'s state rows: the root and why, a LOUD warning on a split, and
    last the row that names ``ears home migrate`` while an old path still holds the state
    (:func:`still_old`). The rows are labelled ``state``, by what they show (``home`` in
    0.3.2)."""
    env = os.environ if environ is None else environ
    root, source = state_source(env, home)
    why = next((f"${n}" for n in ENV if env.get(n)), None) if source == "override" else WHY[source]
    rows = [f"{'state':6s} {root} ({why})"]
    roots = split(home)
    if roots:
        first, rest = roots[0], roots[1:]
        lead = "Both" if len(roots) == 2 else "All of"
        rows += [
            f"{'state':6s} WARNING: THE HEARD LOG IS SPLIT. {lead} {_and(roots)} hold one.",
            f"{'':6s} sessionvoice reads and writes {first}; a writer still on {_and(rest)} (an",
            f"{'':6s} older checkout, or VoiceMode's own) keeps its own seq there, so a cursor or",
            f"{'':6s} a seq no longer names one line. Stop the writers before joining them.",
            f"{'':6s} FIX, where VoiceMode's converse also writes the heard log: set",
            f"{'':6s} {FIX}.",
        ]
    old = still_old(env, home)
    if old:
        rows.append(f"{'state':6s} the state is in {_and(old)}; `ears home migrate` moves it to "
                    f"{_home(home) / NEW} (this host's wave-4 step, then `ears services install`)")
    return rows


# --- seat homes, and tools' folders asked of tools ---------------------------------------------
#
# A seat's home holds its heard-first state (heard.state): ears-arm arms there, talk reads
# the receipts from there, heard-first lists the seats that have one. Tools (sessiontools)
# owns the agent homes, so voice ASKS tools where they are and never derives them:
#   (a) sessiontools.settings, imported lazily (the kit installs both in one environment);
#   (b) else `session path agent`, one absolute path: tools' agent folder;
#   (c) only when NEITHER answers (no importable sessiontools, and a `session` with no
#       `path` verb): tools' OLD folders, read only, in tools' own order (TOOLS_OLD). This
#       keeps a host on old tools unchanged; it only checks whether a folder exists, so it
#       creates nothing. Accepted by the kit's super; it is deleted once every host's
#       tools has `session path`.

#: Tools' OLD folders, under $HOME: the ONE place voice names them (the scan in
#: tests/test_seat_home.py holds every other literal out of voice's code). Read ONLY at rung
#: (c), when tools cannot be asked at all; never written. They go
#: once every host's tools has `session path`.
TOOLS_OLD = {
    "agent": ".session/agent",          # tools' agent folder: home/<seat>/, config/<name>/
    "agents": ".session/agents",        # the pre-4.2 agent folder: home/<seat>/, <name>/
    "sessions": ".session/sessions",    # tools' session folders, <sid>/ (talk's receipts)
}
#: How long `session path` gets to answer, in seconds (talk's own ask uses the same).
TOOLS_TIMEOUT = 10.0
#: The variable that names a seat's home outright (every seat's, when set).
SEAT_ENV = "EARS_HOME"
#: The shared seat home 0.3.2 and older made under $HOME when a seat had no home of its
#: own. Only ever READ now, while it is a real folder (seat_home step 4): nothing makes it.
SHARED_OLD = ".ears"
#: Where voice keeps a seat's home of its own, under the state root: ``seats/<seat>/``, made
#: by ears-arm only (seat_home step 5, the fallback that replaces ~/.ears).
SEATS = "seats"
#: A seat name: what may be a folder's name and a heard-first rule's word.
SEAT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class Tools(NamedTuple):
    """Where tools keeps agent folders, as tools answered (or, at rung c, its old folders).
    ``how`` is the rung: ``sessiontools``, ``session path`` or ``old``."""
    how: str
    #: Each folder whose children are agent homes (``<agent folder>/home``), in order.
    homes: tuple
    #: For rung (a): sessiontools.settings itself, asked per seat and per face.
    mod: object = None
    #: The environment tools was asked with.
    env: Optional[Mapping[str, str]] = None

    def seat_homes(self, seat: str) -> list[Path]:
        """SEAT's tools home candidates, in order (each may not exist)."""
        if self.mod is not None:
            return [Path(self.mod.agent_home(seat, env=self.env))]
        return [d / seat for d in self.homes]

    def avatars(self, name: str) -> list[Path]:
        """NAME's face candidates (``avatar.jpg``), in order (each may not exist): tools'
        config folder for the agent, and at rung (c) the pre-4.2 ``agents/<name>/``."""
        if self.mod is not None and hasattr(self.mod, "agent_config"):
            md = Path(self.mod.agent_config(name, env=self.env))
            return [md.parent / "avatar.jpg"] if md.parent.name == name else []
        if self.how == "old":       # agent/config/<name>/, then the pre-4.2 agents/<name>/
            agent, agents = (d.parent for d in self.homes)
            return [agent / "config" / name / "avatar.jpg", agents / name / "avatar.jpg"]
        return [d.parent / "config" / name / "avatar.jpg" for d in self.homes]


def _tools_env(environ, home) -> dict:
    env = dict(os.environ if environ is None else environ)
    if home is not None:
        env["HOME"] = str(home)
    return env


def _cli_path(kind: str, env: Mapping[str, str]) -> Optional[Path]:
    """``session path KIND``: one absolute path, or None when there is no ``session``, no
    ``path`` verb (an older tools says "unknown verb 'path'" and exits non-zero), it times
    out, or it prints anything but one absolute path. Quiet: None means the next rung."""
    import subprocess
    try:
        p = subprocess.run(["session", "path", kind], env=dict(env), stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=TOOLS_TIMEOUT)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    out = (p.stdout or "").strip() if p.returncode == 0 else ""
    if not out or "\n" in out or not os.path.isabs(out):
        return None
    return Path(out)


def tools_old(kind: str, home=None) -> Path:
    """Tools' OLD folder KIND (:data:`TOOLS_OLD`), under $HOME: rung (c) only."""
    return _home(home) / TOOLS_OLD[kind]


def tools_agent(environ: Optional[Mapping[str, str]] = None, home=None) -> Tools:
    """Where tools keeps agent folders, asked of tools: rung (a), (b), else (c) above. Ask it
    ONCE per pass (talk) or per process (a script) and hand it to :func:`seat_home`; each
    call may start one ``session`` process. Nothing is made."""
    env = _tools_env(environ, home)
    try:
        from sessiontools import settings as mod
        homes = Path(mod.agent_home("_", env=env)).parent      # <agent folder>/home
        return Tools("sessiontools", (homes,), mod, env)
    except Exception:       # no sessiontools, or its own refusal (SettingsError): next rung
        pass
    leaf = _cli_path("agent", env)
    if leaf is not None:
        return Tools("session path", (leaf / "home",), None, env)
    h = _home(env.get("HOME") or None)
    return Tools("old", tuple(tools_old(k, h) / "home" for k in ("agent", "agents")), None, env)


def _shared_old(home) -> Optional[Path]:
    """~/.ears while it is a REAL folder: a directory that is not inside voice's state root
    (a migrate that left it as a link into the kit leaves no second home). Else None."""
    d = _home(home) / SHARED_OLD
    try:
        if not d.is_dir():
            return None
        root = os.path.realpath(state_dir({}, home))
        real = os.path.realpath(d)
        return None if real == root or real.startswith(root + os.sep) else d
    except OSError:
        return None


def shared_seat_home(environ: Optional[Mapping[str, str]] = None, home=None) -> Optional[Path]:
    """The home every seat shares, when there is one: ``$EARS_HOME``, else ~/.ears while it
    is real; else None (a fresh host has none). talk labels a heard.state there that no named
    seat resolved to. Nothing is made."""
    env = os.environ if environ is None else environ
    raw = env.get(SEAT_ENV)
    if raw:
        return Path(os.path.expanduser(raw))
    return _shared_old(home)


def seat_source(seat: str, environ: Optional[Mapping[str, str]] = None, home=None,
                tools: Optional[Tools] = None) -> tuple[Path, str]:
    """SEAT's home and where it came from, the first of (design.md D5):

    1. ``$EARS_HOME``, set and not empty                                (``override``)
    2. tools' home for SEAT, when it is a directory (asked of tools)     (``tools``)
    3. ``~/.<seat>``, when it is a directory                             (``own``)
    4. ``~/.ears``, while it is a real directory (the old shared home)    (``old``)
    5. ``<state root>/seats/<seat>/``, the root with the root overrides left out, as the
       items' is (talk runs as a launchd job, ears-arm from a shell: both name one folder)
                                                                         (``fresh``)

    Steps 2-4 are returned only when they exist, so the one folder a caller may make is
    step 1's or step 5's (ears-arm makes it when it arms). ``tools`` is
    :func:`tools_agent`'s answer, asked here when not given. A name that is not a seat
    name (:data:`SEAT_NAME`) is a ValueError. Nothing is made."""
    if not SEAT_NAME.match(seat or ""):
        raise ValueError(f"not a seat name: {seat!r}")
    env = os.environ if environ is None else environ
    raw = env.get(SEAT_ENV)
    if raw:
        return Path(os.path.expanduser(raw)), "override"
    tools = tools_agent(env, home) if tools is None else tools
    try:
        for d in tools.seat_homes(seat):
            if d.is_dir():
                return d, "tools"
    except Exception:       # tools' own refusal for this name: the next step
        pass
    own = _home(home) / f".{seat}"
    if own.is_dir():
        return own, "own"
    old = _shared_old(home)
    if old is not None:
        return old, "old"
    return state_dir({}, home) / SEATS / seat, "fresh"


def seat_home(seat: str, environ: Optional[Mapping[str, str]] = None, home=None,
              tools: Optional[Tools] = None) -> Path:
    """SEAT's home (:func:`seat_source`, without the source)."""
    return seat_source(seat, environ, home, tools)[0]


def seat_names(environ: Optional[Mapping[str, str]] = None, home=None,
               tools: Optional[Tools] = None) -> list[str]:
    """The names of the seats whose home holds a ``heard.state``: tools' agent homes (asked of
    tools, :func:`tools_agent`) and voice's own ``<state root>/seats/*``. Sorted; a look,
    never a write. ``~/.<seat>``, ``$EARS_HOME`` and ~/.ears name no seat, so they add none."""
    tools = tools_agent(environ, home) if tools is None else tools
    dirs = list(tools.homes) + [state_dir({}, home) / SEATS]
    names: set[str] = set()
    for d in dirs:
        try:
            names |= {p.name for p in d.iterdir()
                      if SEAT_NAME.match(p.name) and (p / "heard.state").is_file()}
        except OSError:
            continue
    return sorted(names)


def avatar(name: str, environ: Optional[Mapping[str, str]] = None, home=None,
           tools: Optional[Tools] = None) -> Optional[Path]:
    """NAME's face (``avatar.jpg``) where tools keeps it, asked of tools; None when it has
    none. A look, never a write."""
    if not SEAT_NAME.match(name or ""):
        return None
    tools = tools_agent(environ, home) if tools is None else tools
    try:
        return next((p for p in tools.avatars(name) if p.is_file()), None)
    except Exception:
        return None


# --- asking the package ------------------------------------------------------------------------
#
# A script that ships in the PLUGIN (the repo: bin/, keeper/, view/, shadow/, speakers/,
# skills/ears/scripts/) loads this file by its path, but the writers run the PACKAGE. When a
# host upgrades one and not the other, the two can name two roots, and the heard log splits.
# So a plugin script never answers from this file alone: it asks the package and refuses,
# naming both paths, when the package cannot be asked or says something else.

#: The marker :func:`asks_package` looks for: a home.py that carries it asks the package.
ASKS_THE_PACKAGE = 1
#: The variable that names the package's ``ears`` command to ask (alone, when set).
ASK_ENV = "EARS_BIN"
#: How long the package gets to answer, in seconds.
ASK_TIMEOUT = 10.0
#: One answer per process, keyed by the root overrides and $HOME.
_asked: dict = {}


def _is_the_package() -> bool:
    """True when this file IS the package, so its answer is the package's (rung 1): an
    installed wheel's ``ears/home.py`` (a ``sessionvoice-*.dist-info`` beside ``ears/``), or
    the file the running python imports as ``ears.home``. No process, no import of the
    package's ``__init__`` (``find_spec`` of a top-level package only locates it)."""
    me = Path(__file__)
    try:
        if me.parent.name == "ears" and any(me.parent.parent.glob("sessionvoice-*.dist-info")):
            return True
    except OSError:
        pass
    try:
        import importlib.util
        spec = importlib.util.find_spec("ears")
        locs = list(spec.submodule_search_locations or []) if spec else []
    except (ImportError, ValueError, AttributeError):
        return False
    return any(_same(Path(d) / "home.py", me) for d in locs)


def _checkout() -> Optional[Path]:
    """The repo this file is the ``src/ears/home.py`` of, when it is a sessionvoice checkout."""
    me = Path(__file__).resolve()
    repo = me.parents[2] if len(me.parents) > 2 else None
    if repo is None or me.parent.name != "ears" or me.parent.parent.name != "src":
        return None
    try:
        if re.search(r'^name\s*=\s*"sessionvoice"', (repo / "pyproject.toml").read_text(), re.M):
            return repo
    except (OSError, UnicodeDecodeError):
        pass
    return None


def _candidates(env: Mapping[str, str], home=None) -> list[str]:
    """The ``ears`` commands to ask, in order (rung 2): $EARS_BIN alone; else the checkout's
    own venv; each ``ears`` on PATH but this checkout's own ``bin/ears`` shim (its last resort
    builds a venv); uv's tool installs, where the shim looks."""
    named = env.get(ASK_ENV)
    if named:
        return [os.path.expanduser(named)]
    found: list[str] = []
    repo = _checkout()
    if repo is not None:
        found.append(str(repo / ".venv" / "bin" / "ears"))
    shim = repo / "bin" / "ears" if repo is not None else None
    for d in (env.get("PATH") or "").split(os.pathsep):
        p = Path(d or ".") / "ears"
        if shim is not None and _same(p, shim):
            continue
        found.append(str(p))
    h = _home(home)
    tools = env.get("UV_TOOL_DIR") or os.path.join(
        env.get("XDG_DATA_HOME") or str(h / ".local" / "share"), "uv", "tools")
    found += [os.path.join(tools, t, "bin", "ears") for t in ("sessionkit", "sessionvoice")]
    out: list[str] = []
    for c in found:
        if c not in out and os.path.isfile(c) and os.access(c, os.X_OK):
            out.append(c)
    return out


def ask_package(environ: Optional[Mapping[str, str]] = None,
                home=None) -> tuple[Optional[Path], str]:
    """The package's answer for the state root, checked against this file's: ``(root,
    how)`` when they agree, ``(None, refusal)`` when the package cannot be asked or says
    something else (design.md D4.1). ``how`` names the rung that answered. The refusal is the
    line to print, without the command's name. Cached once per process."""
    env = dict(os.environ if environ is None else environ)
    if home is not None:
        env["HOME"] = str(home)
    home = env.get("HOME") or None
    key = (tuple(env.get(n) or "" for n in ENV), str(_home(home)))
    if key in _asked:
        return _asked[key]
    own = state_dir(env, home)
    me = f"this plugin's home.py ({Path(__file__)}, version {version()}) says {own}"
    if _is_the_package():
        got = (own, "this home.py is the package's own")
        _asked[key] = got
        return got
    cands = _candidates(env, home)
    if not cands:
        got = (None, f"no sessionvoice package to ask for the state root, and {me}. Nothing is "
                     f"read or written: a guess is how the heard log splits. Install the package "
                     f"(README, Install), or set {ASK_ENV} to its ears.")
        _asked[key] = got
        return got
    ears = cands[0]
    import subprocess
    why = None
    try:
        p = subprocess.run([ears, "home", "state-dir", "--json"], env=env, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=ASK_TIMEOUT)
    except subprocess.TimeoutExpired:
        why = f"it did not answer in {ASK_TIMEOUT:g} s"
    except OSError as e:
        why = f"it could not be run ({e.strerror or e})"
    else:
        if p.returncode != 0:
            tail = (p.stderr.strip().splitlines() or [""])[-1][:200]
            why = (f"it exited {p.returncode} (a package older than 0.3.3 has no `ears home "
                   f"state-dir`; upgrade the plugin and the package together)"
                   + (f": {tail}" if tail else ""))
        else:
            try:
                doc = json.loads(p.stdout)
                pkg, pv = doc["root"]["path"], doc.get("version", "unknown")
                if not isinstance(pkg, str) or not pkg:
                    raise TypeError
            except (ValueError, KeyError, TypeError):
                why = "its answer is not the state-dir --json contract"
    if why:
        got = (None, f"could not ask the sessionvoice package ({ears} home state-dir --json) for "
                     f"the state root: {why}; {me}. Nothing is read or written.")
    elif os.path.realpath(pkg) != os.path.realpath(own):
        got = (None, f"the sessionvoice package ({ears}, version {pv}) says the state root is "
                     f"{pkg}, but {me}: they disagree, so nothing is read or written. Upgrade "
                     f"the plugin and the package together.")
    else:
        got = (Path(pkg), f"asked {ears}")
    _asked[key] = got
    return got


def asked_state_dir(prog: str, environ: Optional[Mapping[str, str]] = None, home=None) -> Path:
    """The state root for a plugin script named PROG: the package's answer, when it agrees
    with this file's (:func:`ask_package`). Else ONE line on stderr, ``PROG: <refusal>``,
    and exit 2, before the script reads or writes anything."""
    root, how = ask_package(environ, home)
    if root is None:
        print(f"{prog}: {how}", file=sys.stderr, flush=True)
        sys.exit(2)
    return root


def asks_package(path) -> bool:
    """True when the home.py at PATH carries the mechanism (:data:`ASKS_THE_PACKAGE`), so the
    plugin it belongs to asks the package: the predicate ``ears home migrate`` checks
    (design.md D7.3, refusal 3). A read, never a run; False when it cannot be read."""
    try:
        return re.search(r"^ASKS_THE_PACKAGE = 1$", Path(path).read_text(), re.M) is not None
    except (OSError, UnicodeDecodeError):
        return False


#: The CLI's usage line, for both of its names.
USAGE = ("usage: {prog} log-dir | state-dir [ITEM] [--json] | asked-state-dir [--as PROG]"
         " | seat-home SEAT")


def main(argv=None, prog: str = "python -m ears.home") -> int:
    """``python -m ears.home log-dir``: print :func:`log_dir`, for a writer in shell.
    ``python -m ears.home state-dir [ITEM] [--json]``: print :func:`state_dir` (or ITEM's
    path), or with ``--json`` the contract (:func:`state_json`; an ITEM's ``{"path",
    "source"}``). ``python -m ears.home seat-home SEAT``: print :func:`seat_home`, for
    ears-arm. ``ears home ...`` is this same function. Exit 2 on a usage error, an unknown
    ITEM or a name that is not a seat name, with nothing on stdout."""
    args = sys.argv[1:] if argv is None else list(argv)
    if args == ["log-dir"]:
        print(log_dir())
        return 0
    if args[:1] == ["state-dir"]:
        rest = args[1:]
        as_json = "--json" in rest
        rest = [a for a in rest if a != "--json"]
        if len(rest) <= 1 and not any(a.startswith("-") for a in rest):
            if not rest:
                if as_json:
                    print(json.dumps(state_json(), indent=2))
                else:
                    print(state_dir())
                return 0
            items = state_items()
            if rest[0] in items:
                path, source = items[rest[0]]
                print(json.dumps({"path": str(path), "source": source}, indent=2) if as_json
                      else path)
                return 0
            known = ", ".join(sorted(items)) or "none yet"
            print(f"{prog} state-dir: no item {rest[0]!r} (items: {known})", file=sys.stderr)
            return 2
    if len(args) == 2 and args[0] == "seat-home":
        if not SEAT_NAME.match(args[1]):
            print(f"{prog} seat-home: not a seat name: {args[1]!r} (letters, digits, '_' and "
                  f"'-', not first)", file=sys.stderr)
            return 2
        print(seat_home(args[1]))
        return 0
    if args[:1] == ["asked-state-dir"]:
        rest = args[1:]
        if not rest or (len(rest) == 2 and rest[0] == "--as" and rest[1]):
            print(asked_state_dir(rest[1] if rest else prog))
            return 0
    print(USAGE.format(prog=prog), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
