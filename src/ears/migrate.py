"""migrate - ``ears home migrate``: move voice's state into the kit's state root, once per host.

A host whose heard log is still in an old root (0.3.2's own, or VoiceMode's) keeps using it in
place (:mod:`ears.home`). This verb is the one step that moves it, by hand, in a stop window:

    ears home migrate              preflight, copy, verify, publish, swap, sweep, report
    ears home migrate --dry-run    the preflight and the plan only; writes nothing
    ears home migrate --json       the plan (with --dry-run) or the result, as JSON

It ACTS by default. On a host where voice runs, a bare run refuses at the preflight (our launch
agents are loaded), prints the plan and writes nothing: only a host whose stop window is open
moves. Exit: 0 done, or nothing to do; 1 refused, nothing written; 2 usage; 3 stopped part-way,
in a safe state the output names (a re-run finishes it).

What moves (design.md D7.2): on 0.3.2's own root, the whole tree as one item; on VoiceMode's
root, only voice's own entries (each heard file, each turn-audio day folder, ``mouth/``,
``talk/``, ``state/``), never VoiceMode's files nor the folders the two share; on both, every
stray (:data:`ears.home.ITEMS`) whose old path is REAL, the speaker enrolment entry by entry
(its ``venv/`` stays). Sockets and other special files are skipped and named.

How (design.md D7.4, D7.5): every lock it must hold is held for the whole run (``heard.lock``,
``mouth/player.lock``, every session lock), and every lock file is HARD-LINKED into the copy
(one inode, so one lock on both sides). Everything else is copied byte for byte into a staging
folder beside the new root, verified (size, sha256, mode), and published by one ``rename``.
Each original is then swapped for a link to its new place in ONE exchange (``renamex_np``,
``RENAME_SWAP``), so the old path never stops existing; the original stays beside it as
``<path>.pre-sessionkit``. A sweep carries what arrived during the run, and never overwrites.

It never deletes (no unlink, rmtree or rmdir here), never touches launchd (``launchctl print``
only, a read), and never writes in a seat home or in VoiceMode's files. After it: re-arm the
readers that hold the heard file open, then ``ears services install`` before any writer starts
(the report says so, in order).
"""
from __future__ import annotations

import ctypes
import ctypes.util
import errno
import fcntl
import fnmatch
import hashlib
import json
import os
import re
import resource
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import home as H

#: The manifest the publish writes at the top of the new root: each item's source, and the
#: files copied with their digests. A new root that holds one was made by this verb.
MANIFEST = ".migrated-from.json"
#: The staging folder, beside the new root (one filesystem, so the publish is one rename).
STAGING = ".state.migrating"
#: Where a staging folder left by a stop goes, by time: never deleted.
FAILED = STAGING + ".failed-"
#: The name an original keeps beside the link that replaced it (the super's ruling (b)).
SUFFIX = ".pre-sessionkit"
#: Free space the copy needs beyond its own bytes.
MARGIN = 64 << 20
#: How long a busy session lock is retried before the run refuses (refusal 9).
LOCK_RETRY_S = 0.5
#: Open files to keep spare beyond the session locks held.
NOFILE_SPARE = 256

#: Every lock file under the root, by pattern relative to the root, and what the run does with
#: it (design.md D7.4). All are HARD-LINKED into the copy, never copied: a copied lock file is
#: a second lock. ``hold``: taken for the whole run. ``link``: only linked, so a waiter's hold
#: stays one lock in the new root. ``tests/test_migrate.py`` fails on a lock site under the
#: root that this table does not name.
LOCKS = {
    "logs/conversations/heard.lock": "hold",       # every heard writer; the watchdog probes it
    "state/heard-cursor/*.lock": "hold",           # each agent's ride-along hook, per session
    "mouth/player.lock": "hold",                   # the mouth player; ensure_player probes it
    "mouth/done/*.wait": "link",                   # a `mouth say` that waits for its line
    "mouth/splits/*.lock": "link",                 # <key>.lock, <key>.json.lock, .one.lock
}
#: One turn-audio day folder, under ``<root>/audio/`` (VoiceMode's own are ``YYYY/MM/``).
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: Voice's own writers, by what their argv runs (refusal 2).
WRITERS = (
    ("the keeper", r"(^|/)ears-keeper\.sh(\s|$)"),
    ("the capture", r"-m\s+ears\.listen(\s|$)"),
    ("call ears", r"(^|[\s/])call-ears(\s|$)"),
    ("call-tap", r"(^|[\s/])call-tap(\s|$)"),
    ("the speaker side-car", r"(^|/)speaker-sidecar2\.py(\s|$)"),
    ("the mouth player", r"(-m\s+mouth|(^|[\s/])mouth)\s+(serve|mail|watch)(\s|$)"),
    ("ears-mail", r"(^|/)ears-mail\.py(\s|$)"),
    ("talk", r"(-m\s+talk|(^|[\s/])talk)\s+serve(\s|$)"),
)
#: VoiceMode's own heard writers, by argv (refusal 10, on VoiceMode's root only). Its MCP
#: server alone is not one: whether converse is in use is the per-host measure.
GUESTS = (
    ("VoiceMode's capture loop", r"-m\s+voice_mode\.listen(\s|$)"),
    ("VoiceMode's mouth player", r"-m\s+voice_mode\.mouth\s+(serve|mail|watch)(\s|$)"),
)
#: sessionphone, by argv, and its launch agents' label prefixes (refusal 10).
PHONE = r"(^|[\s/])sessionphone([\s.]|$)"
PHONE_LABELS = ("dev.sessionkit.phone.bot.", "com.sessionphone.bot.")
#: VoiceMode's heard ride-along: the marker in a hook command, and its file under its root.
HOOK_MARKER = "voicemode-heard-hook"
#: The live readers of the heard log the run lists after it (design.md D7.7 item 5), by argv,
#: and whether each must be re-armed: ``now`` (it holds the file open), ``midnight`` (it globs
#: the folder it resolved: on VoiceMode's root only), ``no`` (it follows the name).
READERS = (
    ("heard-first", r"(^|/)heard-first(\s|$)", "midnight"),
    ("partial-wake.py", r"(^|/)partial-wake\.py(\s|$)", "now"),
    ("ears-mid-turn", r"(^|/)ears-mid-turn(\s|$)", "now"),
    ("ears-mouth-tail", r"(^|/)ears-mouth-tail(\s|$)", "now"),
    ("heard-tail.py", r"(^|/)heard-tail\.py(\s|$)", "no"),
)

#: The two kinds of host, by the old root that holds the heard log.
OURS, VOICEMODE = "sessionvoice", "voicemode"

RENAME_SWAP = 0x2
ATTR_BIT_MAP_COUNT = 5
ATTR_VOL_INFO = 0x80000000
ATTR_VOL_CAPABILITIES = 0x00020000
VOL_CAPABILITIES_INTERFACES = 1
VOL_CAP_INT_RENAME_SWAP = 0x00040000


# --- the seams: what the run asks the machine (tests give each a fake) -------------------------

def _launchctl(*args: str) -> tuple[int, str]:
    """``launchctl print ...``: the only launchctl verb this module runs (a read)."""
    if args[:1] != ("print",):
        raise AssertionError(f"ears home migrate asks launchctl print only, not {args[:1]}")
    try:
        r = subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""
    return r.returncode, r.stdout or ""


def _processes() -> list[tuple[int, str]]:
    """Every process on the host, as ``(pid, argv)``, but this one."""
    try:
        r = subprocess.run(["ps", "-axww", "-o", "pid=,args="], capture_output=True, text=True,
                           timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return []
    out = []
    for line in (r.stdout or "").splitlines():
        pid, _, args = line.strip().partition(" ")
        if pid.isdigit() and int(pid) != os.getpid():
            out.append((int(pid), args.strip()))
    return out


_LIBC = None


def _libc():
    global _LIBC
    if _LIBC is None:
        _LIBC = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    return _LIBC


class _AttrList(ctypes.Structure):
    _fields_ = [("bitmapcount", ctypes.c_ushort), ("reserved", ctypes.c_uint16),
                ("commonattr", ctypes.c_uint32), ("volattr", ctypes.c_uint32),
                ("dirattr", ctypes.c_uint32), ("fileattr", ctypes.c_uint32),
                ("forkattr", ctypes.c_uint32)]


class _VolCaps(ctypes.Structure):
    _fields_ = [("length", ctypes.c_uint32), ("caps", ctypes.c_uint32 * 4),
                ("valid", ctypes.c_uint32 * 4)]


def _can_swap(path) -> bool:
    """The volume PATH is on reports ``VOL_CAP_INT_RENAME_SWAP`` (``getattrlist(2)``, a read)."""
    try:
        al = _AttrList(ATTR_BIT_MAP_COUNT, 0, 0, ATTR_VOL_INFO | ATTR_VOL_CAPABILITIES, 0, 0, 0)
        buf = _VolCaps()
        if _libc().getattrlist(os.fsencode(str(path)), ctypes.byref(al), ctypes.byref(buf),
                               ctypes.sizeof(buf), 0) != 0:
            return False
    except (OSError, AttributeError, TypeError):
        return False
    i = VOL_CAPABILITIES_INTERFACES
    return bool(buf.valid[i] & VOL_CAP_INT_RENAME_SWAP and buf.caps[i] & VOL_CAP_INT_RENAME_SWAP)


def _swap(a, b) -> None:
    """Exchange A and B in one step (``renamex_np(A, B, RENAME_SWAP)``): each name now holds
    what the other held, and neither is ever missing."""
    sys.audit("os.rename", str(a), str(b))   # the same audit event a rename raises
    try:
        fn = _libc().renamex_np
    except AttributeError:
        raise OSError(errno.ENOTSUP, "renamex_np is not available", str(a)) from None
    fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    if fn(os.fsencode(str(a)), os.fsencode(str(b)), RENAME_SWAP) != 0:
        e = ctypes.get_errno()
        raise OSError(e, os.strerror(e), str(a), None, str(b))


def _raise_nofile(n: int) -> bool:
    """Raise this process's soft open-file limit to at least N; False when it cannot be."""
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft == resource.RLIM_INFINITY or soft >= n:
        return True
    if hard != resource.RLIM_INFINITY and n > hard:
        return False
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (n, hard))
    except (ValueError, OSError):
        return False
    return True


def _nofile_ok(n: int) -> bool:
    """Could the soft open-file limit be raised to N? A look, for the dry run."""
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    return soft == resource.RLIM_INFINITY or soft >= n or hard == resource.RLIM_INFINITY or n <= hard


@dataclass
class Host:
    """What the run reads and does, as seams: the HOME, the environment, and each call that
    asks the machine. The defaults are the real ones; the tests give fakes."""
    home: Path
    environ: dict
    uid: int = field(default_factory=os.getuid)
    launchctl: Callable = _launchctl
    processes: Callable = _processes
    dev: Callable = lambda p: os.stat(p).st_dev
    can_swap: Callable = _can_swap
    free: Callable = lambda p: shutil.disk_usage(p).free
    raise_nofile: Callable = _raise_nofile
    nofile_ok: Callable = _nofile_ok
    swap: Callable = _swap
    #: Called at each stage (``copied``, ``published``, ``linked``, ``swapped``) with the
    #: entry, if any: a test's fault injection. Does nothing by default.
    pause: Callable = lambda stage, entry=None: None
    #: The installed-plist check after the swaps (services.installed_state_lines by default).
    installed_lines: Optional[Callable] = None

    @property
    def agents(self) -> Path:
        return self.home / "Library" / "LaunchAgents"

    def loaded(self, label: str) -> bool:
        return self.launchctl("print", f"gui/{self.uid}/{label}")[0] == 0


def host(environ=None, home=None) -> Host:
    """The real host: ``$HOME`` and ``os.environ``."""
    env = dict(os.environ if environ is None else environ)
    return Host(home=Path(home) if home is not None else Path(os.path.expanduser("~")),
                environ=env)


# --- the plan ----------------------------------------------------------------------------------

@dataclass
class Entry:
    """One item the run moves: its old path, its new place, where the original goes."""
    src: Path
    dst: Path
    pre: Path
    rel: str                     # the new place under the new root ("" for the whole root)
    files: dict = field(default_factory=dict)   # rel -> record, filled by the copy
    bytes: int = 0
    mode: Optional[int] = None

    def to_json(self) -> dict:
        return {"src": str(self.src), "dst": str(self.dst), "pre": str(self.pre),
                "rel": self.rel, "files": self.files}

    @classmethod
    def from_json(cls, d: dict) -> "Entry":
        return cls(Path(d["src"]), Path(d["dst"]), Path(d["pre"]), d["rel"], dict(d["files"]))


@dataclass
class Plan:
    home: Path
    new: Path
    kind: Optional[str] = None   # OURS, VOICEMODE, or None (strays only, or nothing)
    old: Optional[Path] = None   # the old root that moves
    entries: list = field(default_factory=list)
    dirs: dict = field(default_factory=dict)   # rel -> mode, the parents made in the copy
    resume: bool = False
    refusals: list = field(default_factory=list)   # (number, why)
    warnings: list = field(default_factory=list)
    probes: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    @property
    def kit(self) -> Path:
        return self.new.parent

    @property
    def staging(self) -> Path:
        return self.kit / STAGING

    def refuse(self, n: int, why: str) -> None:
        self.refusals.append((n, why))


def _pre(path: Path) -> Path:
    return path.with_name(path.name + SUFFIX)


def _join(a: str, b: str) -> str:
    return f"{a}/{b}" if a and b else (a or b)


def _is_lock(rel: str) -> bool:
    return any(fnmatch.fnmatchcase(rel, pat) for pat in LOCKS)


def _same(a, b) -> bool:
    try:
        return os.path.samefile(a, b)
    except (OSError, TypeError, ValueError):
        return False


def _ls(d: Path) -> list[Path]:
    try:
        return sorted(d.iterdir()) if d.is_dir() and not d.is_symlink() else []
    except OSError:
        return []


def _kind(st, rel: str) -> str:
    if stat.S_ISLNK(st.st_mode):
        return "symlink"
    if stat.S_ISDIR(st.st_mode):
        return "dir"
    if stat.S_ISREG(st.st_mode):
        return "lock" if _is_lock(rel) else "file"
    return "special"


def _walk(top: Path, rel: str):
    """``(path, rel, kind, lstat)`` for TOP and everything under it, parents first. A symlink
    is not followed."""
    st = os.lstat(top)
    kind = _kind(st, rel)
    yield top, rel, kind, st
    if kind == "dir":
        for name in sorted(os.listdir(top)):
            yield from _walk(top / name, _join(rel, name))


def _voicemode_entries(vm: Path, new: Path) -> list[Entry]:
    """Voice's own entries in VoiceMode's root, each its own item (design.md D7.2)."""
    out = []
    for c in _ls(vm / "logs" / "conversations"):
        if (c.name == "heard.lock" or fnmatch.fnmatchcase(c.name, "heard_*.jsonl")
                or fnmatch.fnmatchcase(c.name, "speakers_*.jsonl")) \
                and c.is_file() and not c.is_symlink():
            rel = f"logs/conversations/{c.name}"
            out.append(Entry(c, new / rel, _pre(c), rel))
    for c in _ls(vm / "audio"):
        if DAY.match(c.name) and c.is_dir() and not c.is_symlink():
            rel = f"audio/{c.name}"
            out.append(Entry(c, new / rel, _pre(c), rel))
    for name in ("mouth", "talk", "state", *(it.name for it in H.ITEMS.values())):
        c = vm / name
        if os.path.lexists(c) and not c.is_symlink():
            out.append(Entry(c, new / name, _pre(c), name))
    return out


def _stray_entries(h: Host, p: Plan) -> list[Entry]:
    """Every stray whose OLD path is REAL (ears.home.ITEMS), to ``<new root>/<name>``."""
    out = []
    for name, it in H.ITEMS.items():
        if it.env and h.environ.get(it.env):
            p.skipped.append(f"{name}: ${it.env} names it ({h.environ[it.env]}): not moved")
            continue
        old = H.item_old(name, h.environ, h.home)
        place = p.new / it.name
        if not H._real_old(it, old, place):
            continue
        if p.old is not None and os.path.lexists(p.old / it.name):
            p.refuse(5, f"the stray {name} is in two places, {old} and {p.old / it.name}: "
                        "neither is a copy of the other, so a human decides")
            continue
        if it.kind == "speakers":
            aside = _pre(old)
            for c in _ls(old):
                if c.name == "venv" or c.is_symlink():
                    continue
                rel = f"{it.name}/{c.name}"
                out.append(Entry(c, p.new / rel, aside / c.name, rel))
        else:
            out.append(Entry(old, place, _pre(old), it.name))
    return out


def _read_manifest(new: Path) -> Optional[dict]:
    try:
        doc = json.loads((new / MANIFEST).read_text())
        return doc if isinstance(doc, dict) and isinstance(doc.get("entries"), list) else None
    except (OSError, ValueError):
        return None


def plan(h: Host) -> Plan:
    """What a run on host H would move, and the refusals that need no look at the machine."""
    new = h.home / H.NEW
    p = Plan(home=h.home, new=new)
    for n in H.ENV:
        if h.environ.get(n):
            p.refuse(1, f"a root override is set (${n}={h.environ[n]}): the root was set by "
                        "hand, so it is moved by hand")
    if p.refusals:
        return p
    man = _read_manifest(new)
    if man is not None:
        p.resume, p.kind = True, man.get("kind")
        p.old = Path(man["old"]) if man.get("old") else None
        p.entries = [Entry.from_json(e) for e in man["entries"]]
        p.dirs = dict(man.get("dirs") or {})
        return p
    root, source = H.state_source(h.environ, h.home)
    sv, vm = (h.home / r for r in H.OLD_ROOTS)
    if source == "old":
        p.kind, p.old = (OURS if root == sv else VOICEMODE), root
    if p.kind == OURS:
        p.entries.append(Entry(sv, new, _pre(sv), ""))
    elif p.kind == VOICEMODE:
        p.entries += _voicemode_entries(vm, new)
    p.entries += _stray_entries(h, p)
    for e in p.entries:
        _size(e)
    return p


def _size(e: Entry) -> None:
    """The entry's bytes (regular files, not lock files) and its mode."""
    try:
        e.mode = stat.S_IMODE(os.lstat(e.src).st_mode)
        e.bytes = sum(st.st_size for _, _, k, st in _walk(e.src, e.rel) if k == "file")
    except OSError:
        pass


def _linked_logs(p: Plan) -> list[str]:
    """An old root whose ``logs/conversations`` is a folder link into the new root's (a hand
    move): the layout refusal 4 names (design.md D7.10)."""
    out = []
    new_lc = p.new / "logs" / "conversations"
    for r in (p.home / n for n in H.OLD_ROOTS):
        lc = r / "logs" / "conversations"
        if r.is_dir() and not r.is_symlink() and lc.is_symlink() and _same(lc, new_lc):
            out.append(f"{lc} is a folder link into {new_lc}")
    return out


# --- the preflight -----------------------------------------------------------------------------

def _our_labels() -> list[str]:
    """Every launchd label a voice writer of ours runs under: each job's, new and old, its
    hand-made names, and the mailer's LEGACY render."""
    from . import services as S
    out: list[str] = []
    for job in [*S.jobs(S.ALL), *S.OLD]:
        out += S._labels(job) + list(S.HAND.get(job, ()))
    out += list(S.LEGACY.get("mail", ()))
    return list(dict.fromkeys(out))


def _claude_dir(h: Host) -> Path:
    raw = h.environ.get("CLAUDE_CONFIG_DIR")
    return Path(os.path.expanduser(raw)) if raw else h.home / ".claude"


def _skew(h: Host, p: Plan) -> None:
    """Refusal 3: every installed copy of the plugin must ask the package (design.md D4.1),
    read from Claude Code's install record (read, never written)."""
    rec = _claude_dir(h) / "plugins" / "installed_plugins.json"
    if not rec.exists():
        p.notes.append(f"no plugin install record ({rec}): no plugin to disagree with the package")
        return
    try:
        plugins = json.loads(rec.read_text())["plugins"]
        if not isinstance(plugins, dict):
            raise TypeError
    except (OSError, ValueError, KeyError, TypeError):
        p.refuse(3, f"the plugin install record {rec} cannot be parsed, so whether an installed "
                    "plugin answers the old root cannot be told")
        return
    for key, entries in plugins.items():
        if not str(key).startswith("sessionvoice@"):
            continue
        for e in entries if isinstance(entries, list) else [entries]:
            ip = e.get("installPath") if isinstance(e, dict) else None
            hp = Path(os.path.expanduser(str(ip))) / "src" / "ears" / "home.py" if ip else None
            if hp is None or not hp.is_file():
                p.refuse(3, f"the plugin copy {key} ({ip}) has no src/ears/home.py, so whether "
                            "it asks the package cannot be told")
            elif not H.asks_package(hp):
                p.refuse(3, f"the plugin copy {key} at {ip} does not ask the package for the "
                            "state root (0.3.2 or older): after the move it would answer the old "
                            "root and split the heard log. Upgrade the plugin and the package "
                            "together")


def _settings_hooks(h: Host) -> tuple[list[Path], list[str]]:
    """The Claude settings files that register VoiceMode's heard hook, and the ones that
    could not be read. Only the user scope is listed from HOME (VoiceMode's own file, and
    ``$CLAUDE_CONFIG_DIR``'s when set)."""
    files = [h.home / ".claude" / "settings.json"]
    if h.environ.get("CLAUDE_CONFIG_DIR"):
        files.append(_claude_dir(h) / "settings.json")
    found, unread = [], []
    for f in dict.fromkeys(files):
        if not f.exists():
            continue
        try:
            hooks = json.loads(f.read_text()).get("hooks")
        except (OSError, ValueError, AttributeError):
            unread.append(f"{f} could not be read as JSON, so a heard hook registered in it "
                          "was not checked")
            continue
        if HOOK_MARKER in json.dumps(hooks):
            found.append(f)
    return found, unread


def _guests(h: Host, p: Plan, procs) -> None:
    """Refusal 10, on VoiceMode's root: a guest copy of the heard rule is wired."""
    from . import services as S
    vm = h.home / H.OLD_ROOTS[1]
    for pid, args in procs:
        for what, rx in GUESTS:
            if re.search(rx, args):
                p.refuse(10, f"{what} runs (pid {pid}: {args}): it writes the heard log by "
                             f"VoiceMode's own rule, in {vm}, after the move. Stop it, and use "
                             "it no more, or take the FIX instead of the migrate")
    for label in S.LEGACY.get("mouth", ()):
        plist = h.agents / f"{label}.plist"
        loaded, there = h.loaded(label), plist.exists()
        if loaded or there:
            what = ("loaded" if loaded else "") + (" and " if loaded and there else "") + \
                   (f"its plist is in {h.agents}" if there else "")
            p.refuse(10, f"VoiceMode's mouth player job {label} ({what}): launchd starts it "
                         "again, and it writes the heard log in the old folder. `ears services "
                         "install mouth` boots it out and moves its plist aside; by hand, boot it "
                         f"out AND move {plist} out of {h.agents}")
    regs, unread = _settings_hooks(h)
    for f in regs:
        p.refuse(10, f"VoiceMode's heard hook is registered in {f}: it reads the old folder, "
                     "and goes deaf from the next midnight. Run `voicemode claude hooks remove "
                     "heard` once each seat has voice's own (`ears heard hook`)")
    p.warnings += unread
    hook = vm / "bin" / HOOK_MARKER
    if hook.exists() and not regs:
        p.warnings.append(f"{hook} exists, and no registration of it was seen in the user's "
                          "settings. Project-scope settings (.claude/settings.json and "
                          "settings.local.json in each project) cannot be listed from HOME and "
                          "were not checked: run `voicemode claude hooks list -s all` in each "
                          "project that may have added it")
    rc, out = h.launchctl("print", f"gui/{h.uid}")
    for label in sorted(set(re.findall(r"(?:%s)\S+" % "|".join(map(re.escape, PHONE_LABELS)),
                                       out if rc == 0 else ""))):
        p.refuse(10, f"sessionphone's job {label} is loaded: its sink writes the heard log in "
                     "the old folder. Stop phone for the stop window, and start it again with "
                     "DC_HEARD_PY at the package's heard.py and EARS_HEARD_DIR at the new root's "
                     "logs/conversations")
    for pid, args in procs:
        if re.search(PHONE, args):
            p.refuse(10, f"sessionphone runs (pid {pid}: {args}): its sink writes the heard log "
                         "in the old folder. Stop it for the stop window (and start it again "
                         "with DC_HEARD_PY and EARS_HEARD_DIR set to voice's)")


def _ancestor(p: Path) -> Path:
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def _lock_targets(p: Plan) -> tuple[list[Path], list[Path]]:
    """The lock files the run holds: (held at once, session locks retried). Under the old
    root and, on a re-run, the new one too (one inode each, deduped at the take)."""
    once, sessions = [], []
    roots = [r for r in (p.old, p.new) if r is not None and r.is_dir()]
    for r in roots:
        lc, mouth = r / "logs" / "conversations", r / "mouth"
        if lc.is_dir():
            once.append(lc / "heard.lock")
        if mouth.is_dir():
            once.append(mouth / "player.lock")
        sessions += sorted((r / "state" / "heard-cursor").glob("*.lock"))
    return once, sessions


def preflight(h: Host, p: Plan, dry_run: bool) -> None:
    """Every refusal of design.md D7.3 that needs a look at the machine, and the warnings. A
    look only: nothing is written. Refusal 9 (the holds) is the run's; the dry run reports it
    as a probe."""
    procs = h.processes()
    for label in _our_labels():
        if h.loaded(label):
            p.refuse(2, f"a launch agent of ours is loaded ({label}): stop voice's jobs for the "
                        "stop window (`ears services uninstall`, or boot each one out)")
    for pid, args in procs:
        for what, rx in WRITERS:
            if re.search(rx, args):
                p.refuse(2, f"{what} runs (pid {pid}: {args}): stop voice's writers for the "
                            "stop window")
    _skew(h, p)
    if not p.resume and os.path.lexists(p.new):
        try:
            busy = p.new.is_symlink() or any(p.new.iterdir())
        except OSError:
            busy = True
        if busy:
            why = (" It holds a heard log: that state is stranded there." if H.populated(p.new)
                   else "")
            links = _linked_logs(p)
            p.refuse(4, f"the new root {p.new} is not empty and has no {MANIFEST}: something "
                        f"this migrate did not put there lives in it.{why}"
                        + (f" {'; '.join(links)}." if links else "") + " A human decides")
    kit = _ancestor(p.kit)
    kit_dev = h.dev(kit)
    devs = {}
    for e in p.entries:
        d = h.dev(e.src)
        if d != kit_dev:
            p.refuse(6, f"{e.src} is on another filesystem from {kit}: its lock files cannot be "
                        "hard-linked across, and the publish is one rename")
        devs.setdefault(d, e.src.parent)
    devs.setdefault(kit_dev, kit)
    for d, where in devs.items():
        if not h.can_swap(where):
            p.refuse(7, f"the volume of {where} cannot swap two names in one step "
                        "(VOL_CAP_INT_RENAME_SWAP): the old paths could never be swapped in")
    if not p.resume:
        need = sum(e.bytes for e in p.entries) + MARGIN
        free = h.free(kit)
        if free < need:
            p.refuse(8, f"not enough free space under {kit}: the copy needs {need} bytes "
                        f"(with a margin of {MARGIN}), and {free} are free")
    if p.kind == VOICEMODE:
        _guests(h, p, procs)
    if dry_run:
        once, sessions = _lock_targets(p)
        for f in [*once, *sessions]:
            if _busy(f):
                p.probes.append(f"{f} is busy now: the run would wait for it, then refuse")
        if not h.nofile_ok(len(sessions) + NOFILE_SPARE):
            p.probes.append(f"the open-file limit cannot be raised to hold {len(sessions)} "
                            "session locks: the run would refuse")
    _installed_warnings(h, p)


def _busy(f: Path) -> bool:
    """A probe: is the lock file F held now? Opened read-only, never made, never kept."""
    try:
        fd = os.open(str(f), os.O_RDONLY)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except OSError:
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def _installed_warnings(h: Host, p: Plan) -> None:
    """The plan warns while an installed plist of ours names an old path this run moves: that
    job would bring it back (design.md D7.7 item 3). Plists only; launchd is not asked."""
    from . import services as S
    moved = {os.path.normpath(str(e.src)) for e in p.entries}
    moved |= {os.path.normpath(str(e.src.parent)) for e in p.entries
              if e.rel.startswith(H.ITEMS["speakers"].name + "/")}
    for job in S.STATE_JOBS:
        for label in S._labels(job):
            plist = h.agents / f"{label}.plist"
            if not plist.exists() or not S._ours(plist):
                continue
            named = S._installed_state(job, plist)
            if named and os.path.normpath(os.path.expanduser(named)) in moved:
                p.warnings.append(f"the installed job {plist} names {named}, which this run "
                                  "moves: run `ears services install` after it, before any "
                                  "writer starts and before the next login")


# --- the run -----------------------------------------------------------------------------------

class Holds:
    """The locks the run holds, one fd per inode (two opens of one file would lock each
    other out)."""

    def __init__(self):
        self.fds: list[int] = []
        self.seen: set = set()

    def take(self, path: Path, create: bool, retry: float = 0.0) -> bool:
        try:
            fd = os.open(str(path), os.O_RDWR | (os.O_CREAT if create else 0), 0o600)
        except FileNotFoundError:
            return True       # gone since it was listed: nothing to hold
        st = os.fstat(fd)
        if (st.st_dev, st.st_ino) in self.seen:
            os.close(fd)
            return True
        deadline = time.monotonic() + retry
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    return False
                time.sleep(0.02)
        self.seen.add((st.st_dev, st.st_ino))
        self.fds.append(fd)
        return True

    def release(self) -> None:
        for fd in self.fds:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
        self.fds, self.seen = [], set()


def _hold(h: Host, p: Plan) -> Optional[Holds]:
    """Take every hold of design.md D7.4, or refuse (9) and hold nothing."""
    once, sessions = _lock_targets(p)
    if not h.raise_nofile(len(sessions) + NOFILE_SPARE):
        p.refuse(9, f"the open-file limit cannot be raised to hold {len(sessions)} session locks")
        return None
    holds = Holds()
    for f in once:
        if not holds.take(f, create=True):
            p.refuse(9, f"{f} is busy: a writer holds it. Stop it and run again")
            holds.release()
            return None
    for f in sessions:
        if not holds.take(f, create=False, retry=LOCK_RETRY_S):
            p.refuse(9, f"the session lock {f} stayed busy past {LOCK_RETRY_S:g} s")
            holds.release()
            return None
    return holds


def _made_by_the_hold(p: Plan) -> None:
    """On VoiceMode's root each heard file is its own item, listed before the holds. A
    ``heard.lock`` that was not there then was made by the hold (opened ``O_CREAT``, as every
    heard writer opens it): it is voice's, and it moves too. Left behind, a lone real
    ``heard.lock`` keeps the old root POPULATED (a false split), and the new root has none, so
    a writer there would lock another inode than the one the run holds."""
    if p.kind != VOICEMODE or p.old is None:
        return
    rel = "logs/conversations/heard.lock"
    lock = p.old / rel
    if any(e.rel == rel for e in p.entries) or not lock.is_file() or lock.is_symlink():
        return
    e = Entry(lock, p.new / rel, _pre(lock), rel)
    _size(e)
    p.entries.insert(0, e)


def _sha(path: Path) -> str:
    d = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            d.update(chunk)
    return d.hexdigest()


def _copy_file(src: Path, dst: Path) -> dict:
    d, n = hashlib.sha256(), 0
    with open(src, "rb") as fi, open(dst, "xb") as fo:
        for chunk in iter(lambda: fi.read(1 << 20), b""):
            d.update(chunk)
            fo.write(chunk)
            n += len(chunk)
    shutil.copystat(src, dst, follow_symlinks=False)
    return {"kind": "file", "size": n, "sha256": d.hexdigest(),
            "mode": stat.S_IMODE(os.lstat(src).st_mode)}


def _place(staging: Path, rel: str) -> Path:
    return staging / rel if rel else staging


def _copy_one(path: Path, dst: Path, kind: str, st) -> dict:
    """Copy one walked node to DST; its manifest record."""
    if kind == "dir":
        if not dst.exists():
            os.mkdir(dst, 0o700)
        return {"kind": "dir", "mode": stat.S_IMODE(st.st_mode)}
    if kind == "lock":
        os.link(path, dst)
        return {"kind": "lock"}
    if kind == "symlink":
        target = os.readlink(path)
        os.symlink(target, dst)
        return {"kind": "symlink", "target": target}
    return _copy_file(path, dst)


def _copy(p: Plan, h: Host) -> None:
    """Copy every entry into the staging folder, parents made with their source's mode."""
    root_mode = stat.S_IMODE(os.stat(p.old).st_mode) if p.old is not None else 0o700
    p.kit.mkdir(parents=True, exist_ok=True)
    os.mkdir(p.staging, 0o700)
    later = [(p.staging, root_mode, None)]
    for e in p.entries:
        parts = e.rel.split("/")[:-1] if e.rel else []
        for i in range(len(parts)):
            rel = "/".join(parts[:i + 1])
            src_dir = e.src.parents[len(parts) - 1 - i]
            dst = p.staging / rel
            if rel not in p.dirs:
                p.dirs[rel] = stat.S_IMODE(os.stat(src_dir).st_mode)
                if not dst.exists():
                    os.mkdir(dst, 0o700)
                later.append((dst, p.dirs[rel], None))
        for path, rel, kind, st in _walk(e.src, e.rel):
            if kind == "special":
                p.skipped.append(f"{path}: not a file, folder or link (a socket is bound again by "
                                 "its server at its next start)")
                continue
            e.files[rel] = _copy_one(path, _place(p.staging, rel), kind, st)
            if kind == "dir":
                later.append((_place(p.staging, rel), stat.S_IMODE(st.st_mode), st))
    for d, mode, st in reversed(later):
        os.chmod(d, mode)
        if st is not None:
            os.utime(d, ns=(st.st_atime_ns, st.st_mtime_ns))
    h.pause("copied")


def _src_of(e: Entry, rel: str, base: Optional[Path] = None) -> Path:
    base = e.src if base is None else base
    sub = rel[len(e.rel):].lstrip("/") if e.rel else rel
    return base / sub if sub else base


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def _verify(p: Plan) -> list[str]:
    """Every copied node against its source as it is now (design.md D7.5 step 3)."""
    bad = []
    for e in p.entries:
        for rel, rec in e.files.items():
            src, cp = _src_of(e, rel), _place(p.staging, rel)
            try:
                k = rec["kind"]
                if k == "file":
                    if os.path.getsize(src) != rec["size"] or _sha(src) != rec["sha256"]:
                        bad.append(f"{src} changed at the source after it was copied")
                    elif os.path.getsize(cp) != rec["size"] or _sha(cp) != rec["sha256"]:
                        bad.append(f"{cp} differs from its source {src}")
                    elif _mode(cp) != rec["mode"] or _mode(src) != rec["mode"]:
                        bad.append(f"{cp} has mode {_mode(cp):04o}, its source {src} "
                                   f"{_mode(src):04o}")
                elif k == "dir":
                    if _mode(cp) != rec["mode"] or _mode(src) != rec["mode"]:
                        bad.append(f"the folder {cp} has mode {_mode(cp):04o}, its source "
                                   f"{_mode(src):04o}")
                elif k == "lock" and not _same(src, cp):
                    bad.append(f"the lock file {cp} is not the same file as {src}")
                elif k == "symlink" and os.readlink(cp) != rec["target"]:
                    bad.append(f"the link {cp} does not name {rec['target']}")
            except OSError as err:
                bad.append(f"{src}: {err.strerror or err}")
    for rel, mode in p.dirs.items():
        if _mode(p.staging / rel) != mode:
            bad.append(f"the folder {p.staging / rel} has mode {_mode(p.staging / rel):04o}, "
                       f"its source {mode:04o}")
    return bad


def _publish(p: Plan, h: Host) -> None:
    doc = {"version": 1, "kind": p.kind, "old": str(p.old) if p.old else None,
           "made": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "entries": [e.to_json() for e in p.entries], "dirs": p.dirs}
    with open(p.staging / MANIFEST, "x") as f:
        json.dump(doc, f, indent=1)
    os.rename(p.staging, p.new)
    h.pause("published")


def _swapped(e: Entry) -> bool:
    return os.path.islink(e.src) and _same(e.src, e.dst)


def _unchanged(e: Entry) -> list[str]:
    """For a re-run's swap: the entry's source against the manifest. A file in it that changed
    or is gone is a difference; a file only ADDED is not (the sweep carries it)."""
    bad = []
    for rel, rec in e.files.items():
        src = _src_of(e, rel)
        if not os.path.lexists(src):
            bad.append(f"{src} is gone")
        elif rec["kind"] == "file":
            try:
                if _sha(src) != rec["sha256"]:
                    bad.append(f"{src} changed")
            except OSError as err:
                bad.append(f"{src}: {err.strerror or err}")
    return bad


def _swap_entry(e: Entry, h: Host) -> Optional[str]:
    """Swap the original for a link to its new place, in ONE exchange (design.md D7.5 step 5).
    None when swapped; else why not (the item is left real)."""
    if _swapped(e):
        return None
    if os.path.lexists(e.pre):
        if not (os.path.islink(e.pre) and os.readlink(e.pre) == str(e.dst)):
            return f"{e.pre} is there and is not a link to {e.dst}: a human decides"
    else:
        if not e.pre.parent.exists():
            os.mkdir(e.pre.parent, 0o700)
        os.symlink(str(e.dst), e.pre)
    h.pause("linked", e)
    try:
        h.swap(e.pre, e.src)
    except OSError as err:
        return f"the exchange of {e.src} failed ({err.strerror or err})"
    h.pause("swapped", e)
    return None


def _same_content(a: Path, b: Path, kind: str) -> bool:
    try:
        if kind == "file":
            return os.path.isfile(b) and not os.path.islink(b) and _sha(a) == _sha(b)
        if kind == "lock":
            return _same(a, b)
        if kind == "dir":
            return os.path.isdir(b) and not os.path.islink(b)
        if kind == "symlink":
            return os.path.islink(b) and os.readlink(a) == os.readlink(b)
    except OSError:
        return False
    return False


@dataclass
class Sweep:
    carried: list = field(default_factory=list)
    left: list = field(default_factory=list)
    changed: list = field(default_factory=list)
    todo: list = field(default_factory=list)    # (src, dst, kind, st): what a carry would copy


def _sweep(p: Plan, carry: bool) -> Sweep:
    """Each swapped item's original (now at its ``.pre-sessionkit`` name) against the
    manifest (design.md D7.5 step 6). What was added during the run goes to its new place
    when that is empty, and is named; a new place already taken keeps both. Nothing is ever
    overwritten. CARRY False only looks."""
    s = Sweep()
    for e in p.entries:
        if not _swapped(e) or not os.path.lexists(e.pre):
            continue
        seen = set()
        for path, rel, kind, st in _walk(e.pre, e.rel):
            seen.add(rel)
            if kind == "special":
                continue
            rec = e.files.get(rel)
            if rec is not None:
                if rec["kind"] == "file" and _sha(path) != rec["sha256"]:
                    s.changed.append(f"{path} changed after the verify; the new place "
                                     f"{_place(p.new, rel)} is the live one, and both are kept")
                continue
            dst = _place(p.new, rel)
            if os.path.lexists(dst):
                if not _same_content(path, dst, kind):
                    s.left.append(f"{path} arrived during the run, and {dst} holds another: "
                                  "both are left")
                continue
            s.todo.append((path, dst, kind, st, rel))
        for rel in e.files:
            if rel not in seen:
                s.changed.append(f"{_src_of(e, rel, e.pre)} is gone since the verify")
    if carry:
        for path, dst, kind, st, rel in s.todo:
            rec = _copy_one(path, dst, kind, st)
            if kind == "dir":
                os.chmod(dst, stat.S_IMODE(st.st_mode))
            if kind == "file" and _sha(dst) != rec["sha256"]:
                s.left.append(f"{path} was carried to {dst}, and the copy does not verify")
            else:
                s.carried.append(f"{path} -> {dst}" + (" (hard-linked)" if kind == "lock" else ""))
    return s


def _readers(h: Host, p: Plan) -> list[str]:
    """The live readers of the heard log, by pid and argv, each with its re-arm line."""
    out = []
    for pid, args in h.processes():
        for what, rx, when in READERS:
            if not re.search(rx, args):
                continue
            if when == "now":
                line = "re-arm it NOW: it holds the heard file open, and hears nothing since the swap"
            elif when == "midnight" and p.kind == VOICEMODE:
                line = ("re-arm it before the next local midnight: it globs the old folder, and "
                        "hears nothing from the next day on (it resumes at its place)")
            elif when == "midnight":
                line = "need not be re-armed: it opens the heard log by its path, through the link"
            else:
                line = "need not be re-armed: it follows the name"
            out.append(f"{what} (pid {pid}: {args}): {line}")
    return out


def _report(h: Host, p: Plan, result: dict, sweep: Optional[Sweep]) -> None:
    """The result's after-the-run list, in the order it must be done (design.md D7.7)."""
    after = result.setdefault("after", [])
    after.append("1. Re-arm the readers that hold the heard file open, right away: "
                 "partial-wake.py, ears-mid-turn, ears-mouth-tail (listed below by pid)")
    if p.kind == VOICEMODE:
        after.append("2. Re-arm every heard-first waiter before the next local midnight: each "
                     "resumes at its place")
    after.append("3. Then run `ears services install`, before any writer starts again and "
                 "before the next login: it re-renders every job to the new paths. A job "
                 "rendered by hand from launchd/ is rendered again with bin/render-plist and "
                 "installed by hand, as its header says")
    lines = (h.installed_lines or _services_installed_lines)()
    after += [f"   {line}" for line in lines]
    after.append("4. Check the heartbeat's rms_dbfs once the keeper is back")
    readers = _readers(h, p)
    after += ["5. The live readers:"] + [f"   {r}" for r in readers] if readers else \
        ["5. No live reader of the heard log runs"]
    if sweep is not None:
        result["carried"], result["left"], result["changed"] = \
            sweep.carried, sweep.left, sweep.changed
        swept = sweep.carried + sweep.left + sweep.changed + p.skipped
        if swept:
            after.append("6. What the sweep carried or left, and what was skipped:")
            after += [f"   {x}" for x in swept]
    shared = h.home / H.SHARED_OLD
    if shared.is_dir() and not shared.is_symlink():
        after.append(f"7. {shared} is real: still the shared seat home of the seats that have no "
                     "home of their own, and not moved")
    originals = []
    for e in p.entries:
        if os.path.lexists(e.pre) and not os.path.islink(e.pre):
            originals.append({"path": str(e.pre), "bytes": e.bytes or _bytes(e.pre)})
    result["originals"] = originals
    if originals:
        after.append("8. The originals stay beside the links, untouched (the rollback, by hand, "
                     "by the same exchange back). Remove them by hand when satisfied:")
        after += [f"   {o['path']} ({o['bytes']} bytes)" for o in originals]
    if p.kind == VOICEMODE:
        hook = h.home / H.OLD_ROOTS[1] / "bin" / HOOK_MARKER
        after.append("9. VoiceMode's root: its capture loop, its mouth player (and its LEGACY "
                     "job) and converse stay unused; its heard hook stays removed; phone starts "
                     "again only with DC_HEARD_PY and EARS_HEARD_DIR set to voice's"
                     + (f". {hook} is still there; project-scope settings were not checked"
                        if hook.exists() else ""))


def _bytes(path: Path) -> int:
    try:
        return sum(st.st_size for _, _, k, st in _walk(path, "") if k in ("file",))
    except OSError:
        return 0


def _services_installed_lines() -> list[str]:
    from . import services as S
    return S.installed_state_lines()


def _items(p: Plan) -> list[dict]:
    out = []
    for e in p.entries:
        how = "tree: copy, lock files hard-linked" if not e.rel else \
            ("hard link" if _is_lock(e.rel) else "copy")
        out.append({"from": str(e.src), "to": str(e.dst), "bytes": e.bytes,
                    "mode": f"{e.mode:04o}" if e.mode is not None else None, "how": how})
    return out


def _doc(p: Plan, dry_run: bool) -> dict:
    return {"kind": p.kind, "from": str(p.old) if p.old else None, "to": str(p.new),
            "dry_run": dry_run, "resume": p.resume, "items": _items(p),
            "refusals": [{"n": n, "why": why} for n, why in p.refusals],
            "warnings": p.warnings, "probes": p.probes, "skipped": p.skipped, "notes": p.notes}


def _aside(p: Plan) -> Optional[str]:
    """A staging folder a stop left (before the publish): renamed aside, never deleted."""
    if not os.path.lexists(p.staging):
        return None
    aside = p.kit / f"{FAILED}{time.strftime('%Y%m%d-%H%M%S')}"
    n = 1
    while os.path.lexists(aside):
        n += 1
        aside = p.kit / f"{FAILED}{time.strftime('%Y%m%d-%H%M%S')}-{n}"
    os.rename(p.staging, aside)
    return f"a staging folder left by a stop was renamed aside to {aside}"


def run(h: Host, dry_run: bool = False) -> tuple[int, dict]:
    """``ears home migrate`` on host H: (exit code, the plan and result as one document)."""
    p = plan(h)
    doc = lambda: _doc(p, dry_run)   # noqa: E731
    if p.refusals:
        return 1, doc()
    if os.path.lexists(p.staging):
        if dry_run:
            p.notes.append(f"a staging folder left by a stop, {p.staging}, would be renamed "
                           "aside and the run started again")
        else:
            p.notes.append(_aside(p))
    if p.resume:
        return _resume(h, p, dry_run)
    links = _linked_logs(p)
    if not p.entries and not links:
        p.notes.append("nothing to do: no old root holds the heard log, and no stray is at its "
                       "old path")
        return 0, doc()
    preflight(h, p, dry_run)
    if p.refusals:
        return 1, doc()
    if dry_run:
        return 0, doc()
    holds = _hold(h, p)
    if holds is None:
        return 1, doc()
    _made_by_the_hold(p)
    result: dict = {"published": False, "swapped": [], "failed": []}
    sweep = None
    try:
        try:
            _copy(p, h)
        except OSError as err:
            result["stopped"] = (f"the copy stopped ({err}): nothing is published, and the "
                                 f"staging folder {p.staging} is left for a re-run")
            return 3, {**doc(), "result": result}
        bad = _verify(p)
        if bad:
            result["stopped"] = (f"the verify failed, so nothing is published; the staging "
                                 f"folder {p.staging} is left in place, and a re-run starts again")
            result["mismatches"] = bad
            return 3, {**doc(), "result": result}
        _publish(p, h)
        result["published"] = True
        for e in p.entries:
            why = _swap_entry(e, h)
            (result["failed"].append(f"{e.src}: {why}") if why else
             result["swapped"].append(f"{e.src} -> {e.dst}"))
        sweep = _sweep(p, carry=True)
    finally:
        holds.release()
    _report(h, p, result, sweep)
    code = 3 if result["failed"] or (sweep and sweep.changed) else 0
    return code, {**doc(), "result": result}


def _resume(h: Host, p: Plan, dry_run: bool) -> tuple[int, dict]:
    """A host with a published root (design.md D7.8): swap what is still real, when it still
    matches the manifest, and sweep."""
    doc = lambda: _doc(p, dry_run)   # noqa: E731
    pending = [e for e in p.entries if not _swapped(e)]
    look = _sweep(p, carry=False)
    if not pending and not look.todo:
        result = {"published": True, "swapped": [], "failed": [], "changed": look.changed}
        if look.changed:
            result["stopped"] = "a swapped original changed after the verify (named again; "\
                                "nothing is changed): a human compares the two"
            return 3, {**doc(), "result": result}
        p.notes.append(f"nothing to do: every item is in {p.new}, and each old path is a link "
                       "into it")
        return 0, doc()
    preflight(h, p, dry_run)
    if p.refusals:
        return 1, doc()
    for e in pending:
        diff = _unchanged(e)
        if diff:
            p.warnings.append(f"{e.src} differs from the manifest ({'; '.join(diff)}): a writer "
                              "ran since, so it is not swapped. A human decides")
    if dry_run:
        return 0, doc()
    holds = _hold(h, p)
    if holds is None:
        return 1, doc()
    result: dict = {"published": True, "swapped": [], "failed": []}
    try:
        for e in pending:
            if _unchanged(e):
                result["failed"].append(f"{e.src}: differs from the manifest")
                continue
            why = _swap_entry(e, h)
            (result["failed"].append(f"{e.src}: {why}") if why else
             result["swapped"].append(f"{e.src} -> {e.dst}"))
        sweep = _sweep(p, carry=True)
    finally:
        holds.release()
    _report(h, p, result, sweep)
    code = 3 if result["failed"] or sweep.changed else 0
    return code, {**doc(), "result": result}


# --- the CLI -----------------------------------------------------------------------------------

HELP = """\
usage: {prog} [--dry-run] [--json]

Move voice's state from its old root (0.3.2's own, or VoiceMode's) and the old paths of its
strays into the kit's state root, once per host, in a stop window. It acts by default; a host
where voice runs is refused at the preflight, and nothing is written.

  --dry-run   the preflight and the plan only: writes nothing
  --json      the plan (with --dry-run) or the result, as JSON

It copies (lock files are hard-linked), verifies, publishes, swaps each original for a link
in one exchange, keeps the original as <path>{suffix}, and never deletes. Then, in this
order: re-arm the readers that hold the heard file open, and run `ears services install`
before any writer starts and before the next login (a job rendered by hand from launchd/ is
rendered again with bin/render-plist and installed by hand). That install is this host's
own step: no merge runs it. `ears services status` names an old path until it is done.

exit: 0 done, or nothing to do; 1 refused, nothing written; 2 usage; 3 stopped part-way, in a
safe state the output names (run it again to finish)."""


def _text(code: int, doc: dict, prog: str) -> str:
    lines = []
    what = {OURS: "an old root of 0.3.2's", VOICEMODE: "VoiceMode's root"}.get(doc["kind"])
    lines.append(f"{prog}: " + (f"moves {doc['from']} ({what}) into {doc['to']}" if what
                                else f"the state root is {doc['to']}")
                 + (" (a re-run)" if doc["resume"] else "") + (" -- dry run" if doc["dry_run"] else ""))
    if doc["items"]:
        lines.append("plan:")
        lines += [f"  {i['from']} -> {i['to']}  ({i['bytes']} bytes, mode {i['mode']}, {i['how']})"
                  for i in doc["items"]]
    for key, head in (("notes", "note"), ("skipped", "skip"), ("probes", "probe"),
                      ("warnings", "WARNING")):
        lines += [f"{head}: {x}" for x in doc[key]]
    if doc["refusals"]:
        lines.append("REFUSED, nothing was written:")
        lines += [f"  refusal {r['n']}: {r['why']}" for r in doc["refusals"]]
    res = doc.get("result")
    if res:
        if res.get("stopped"):
            lines.append(f"STOPPED: {res['stopped']}")
        lines += [f"  {m}" for m in res.get("mismatches", [])]
        lines += [f"swapped: {x}" for x in res.get("swapped", [])]
        lines += [f"NOT SWAPPED: {x}" for x in res.get("failed", [])]
        lines += [f"carried: {x}" for x in res.get("carried", [])]
        lines += [f"left: {x}" for x in res.get("left", [])]
        lines += [f"CHANGED: {x}" for x in res.get("changed", [])]
        if res.get("after"):
            lines.append("after the run, in this order:")
            lines += [f"  {x}" for x in res["after"]]
    lines.append(f"exit {code}")
    return "\n".join(lines)


def main(argv=None, prog: str = "ears home migrate", h: Optional[Host] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if any(a in ("-h", "--help") for a in args):
        print(HELP.format(prog=prog, suffix=SUFFIX))
        return 0
    bad = [a for a in args if a not in ("--dry-run", "--json")]
    if bad:
        print(HELP.format(prog=prog, suffix=SUFFIX).splitlines()[0], file=sys.stderr)
        print(f"{prog}: unknown argument {bad[0]!r}", file=sys.stderr)
        return 2
    try:
        code, doc = run(h or host(), dry_run="--dry-run" in args)
    except OSError as err:
        print(f"{prog}: STOPPED part-way: {err}. Each item answers one place (the old path is "
              "never missing); run it again to finish", file=sys.stderr)
        return 3
    print(json.dumps({**doc, "exit": code}, indent=2) if "--json" in args else _text(code, doc, prog))
    return code
