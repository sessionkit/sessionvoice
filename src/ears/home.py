"""home - where sessionvoice keeps its state: :func:`sessionvoice_home`.

sessionvoice began inside VoiceMode and kept its state under ``~/.voicemode``. Its own
root is ``~/.sessionvoice``. Nothing is moved: a host whose heard log is in
``~/.voicemode`` keeps using it there ("adopt in place"). Moving it is a separate,
hand-run step.

The root is the first of:

1. ``$SESSIONVOICE_HOME``, if it is set;
2. ``$VOICEMODE_BASE_DIR``, if it is set (the old name, still read);
3. ``~/.sessionvoice``, if it is POPULATED;
4. ``~/.voicemode``, if it is POPULATED;
5. otherwise ``~/.sessionvoice``.

A variable set to the empty string counts as not set, as ``VOICEMODE_BASE_DIR`` always
did.

POPULATED means ``<root>/logs/conversations`` holds a ``heard.lock`` or a
``heard_*.jsonl``. That directory is where the heard log lives, and a ``seq`` names one
line across days only while every writer uses the same one. A directory that exists
but holds no heard log was made by hand or by a stray ``mkdir``, and it never decides.

When BOTH are populated the heard log is split in two. The resolver picks
``~/.sessionvoice`` and ``ears services status`` says so loudly (:func:`split`).

On a FRESH host that also runs VoiceMode's ``converse`` as a heard guest (it writes
``~/.voicemode`` and reads only ``VOICEMODE_BASE_DIR``), set
``SESSIONVOICE_HOME=~/.voicemode`` (or ``VOICEMODE_BASE_DIR``) so that both write one
log. Otherwise whichever writes first decides: converse first, and sessionvoice adopts
``~/.voicemode``; sessionvoice first, and converse's lines split off there. The split
warning names this fix (:data:`FIX`). Nothing here detects VoiceMode.

Under the root nothing changes: ``logs/conversations/heard_*.jsonl``, ``heard.lock``,
``speakers_*``, ``logs/install.log``, ``mouth/``, ``talk/`` and ``state/``. VoiceMode's own files in
``~/.voicemode`` (``voicemode.env``, ``voices/``, ``services/``, ``exchanges_*``) are
VoiceMode's, and this module never names them.

Standard library only. Several scripts import nothing from this package (they run under
any ``python3``, or as shell), so each keeps a short copy of this rule:
``src/ears/heard.py``, the scripts in ``keeper/``, ``bin/``, ``view/``, ``shadow/``,
``speakers/`` and ``skills/ears/scripts/``, and ``keeper/ears-keeper.sh``.
``tests/test_home.py`` holds every copy to this module under fake HOMEs.

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
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping, Optional

#: The variables that name the root, read in this order.
ENV = ("SESSIONVOICE_HOME", "VOICEMODE_BASE_DIR")
NEW = ".sessionvoice"     # the new default, under $HOME
OLD = ".voicemode"        # where the state has lived until now, under $HOME
#: The fix the split warning names: a host that also runs
#: VoiceMode's converse as a heard guest points sessionvoice at converse's folder by hand.
#: Nothing here looks for VoiceMode.
FIX = "SESSIONVOICE_HOME=~/.voicemode (or VOICEMODE_BASE_DIR), so that both write one log"
#: The package's settings folder, under $HOME: the kit's settings home, package first.
#: The ONE place this repo's Python names it; the copies are held to it by tests.
SETTINGS = ".sessionkit/voice/settings"
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


def populated(root) -> bool:
    """True when ``<root>/logs/conversations`` holds a ``heard.lock`` or a ``heard_*.jsonl``."""
    d = Path(root) / "logs" / "conversations"
    try:
        return (d / "heard.lock").exists() or any(d.glob("heard_*.jsonl"))
    except OSError:
        return False


def _home(home) -> Path:
    return Path(home) if home is not None else Path(os.path.expanduser("~"))


def settings_dir(home=None) -> Path:
    """``~/.sessionkit/voice/settings``: the folder every voice settings file sits in.
    ``home`` defaults to the user's home (``$HOME``, read on every call)."""
    return _home(home) / SETTINGS


def legacy_path(name: str, home=None) -> Path:
    """Where the voice settings file NAME sat under 0.1.0 (:data:`LEGACY_FOLDER`)."""
    return _home(home) / LEGACY_SETTINGS[LEGACY_FOLDER[name]] / name


def stranded(name: str, home=None) -> Optional[tuple[Path, Path]]:
    """``(new, old)`` when the voice settings file NAME is STRANDED: nothing is at its path
    in the settings folder (not even a dangling link) and its 0.1.0 copy exists; else None.
    A stat, never a read: a refusal, not a fallback."""
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


def sessionvoice_home(environ: Optional[Mapping[str, str]] = None, home=None) -> Path:
    """sessionvoice's state root, by the order in this module's docstring.

    ``environ`` defaults to ``os.environ`` and ``home`` to the user's home (``$HOME``).
    """
    env = os.environ if environ is None else environ
    for name in ENV:
        raw = env.get(name)
        if raw:
            return Path(os.path.expanduser(os.path.expandvars(raw)))
    h = _home(home)
    if populated(h / NEW):
        return h / NEW
    if populated(h / OLD):
        return h / OLD
    return h / NEW


def split(home=None) -> Optional[tuple[Path, Path]]:
    """``(~/.sessionvoice, ~/.voicemode)`` when BOTH hold a heard log, else None.

    The env is not consulted: a root set by a variable decides for this process, but a
    writer started without it still picks between these two."""
    h = _home(home)
    if populated(h / NEW) and populated(h / OLD):
        return h / NEW, h / OLD
    return None


def status_lines(environ: Optional[Mapping[str, str]] = None, home=None) -> list[str]:
    """``ears services status``'s home rows: the root and why, and a LOUD warning on a split."""
    env = os.environ if environ is None else environ
    root = sessionvoice_home(env, home)
    why = next((f"${n}" for n in ENV if env.get(n)), None)
    if why is None:
        why = "has the heard log" if populated(root) else "the default; no heard log yet"
    rows = [f"{'home':6s} {root} ({why})"]
    both = split(home)
    if both:
        new, old = both
        rows += [
            f"{'home':6s} WARNING: THE HEARD LOG IS SPLIT. Both {new} and {old} hold one.",
            f"{'':6s} sessionvoice reads and writes {new}; a writer still on {old} (an older",
            f"{'':6s} checkout, or VoiceMode's own) keeps its own seq there, so a cursor or",
            f"{'':6s} a seq no longer names one line. Stop the writers before joining them.",
            f"{'':6s} FIX, where VoiceMode's converse also writes the heard log: set",
            f"{'':6s} {FIX}.",
        ]
    return rows
