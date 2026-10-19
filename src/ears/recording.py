"""recording - the ears' one recording switch: is captured audio kept on disk?

Off by default. With nothing set, the ears write no audio file: no missed-reply
clip and no kept turn audio. The paths a person runs by hand (``ears speakers
record``, ``ears eval``) do not read it: typing the command is the consent.

Two settings, each read from the first layer that is SET:

=================  ==========================  ======================  ======================  =======
setting            CLI (``ears capture``)      environment             ears.json key           default
=================  ==========================  ======================  ======================  =======
``record``         ``--record``/``--no-record``  ``EARS_RECORD``        ``"record"``            off
``record_days``    (none)                      ``EARS_RECORD_DAYS``    ``"record_days"``       7
=================  ==========================  ======================  ======================  =======

ears.json is ``$EARS_CONF`` when it is set and not empty, else
``~/.sessionkit/voice/settings/ears.json`` (:func:`conf_path`, the same file
:mod:`ears.listen.stt` reads).

Accepted values. ``EARS_RECORD``: ``1``, ``true``, ``on`` or ``0``, ``false``,
``off``, any case, whitespace trimmed. The file's ``record``: a JSON boolean.
``EARS_RECORD_DAYS``: a decimal integer, 0 or more. The file's ``record_days``:
a JSON integer, 0 or more (not a boolean). 0 days means never prune.

The edge rules (shared with the call recorder, which reads the same settings):

1. An env var that is unset or empty is NOT SET.
2. No file, an unreadable file, bad JSON, or a top-level value that is not an
   object: the file layer is NOT SET for both keys (said once).
3. A key missing from the file is NOT SET.
4. An invalid ``record`` at any layer FAILS CLOSED: that layer wins, with OFF,
   and a warning names the layer and the value. A garbled switch never records.
5. An invalid ``record_days`` at a layer is skipped with a warning; the next
   layer (or 7) is used. Retention never becomes "forever" by accident.
6. The CLI flag is the process's own argument; the last one given wins.
7. The CLI and env are fixed for the life of the process. The file is followed:
   :class:`Switch` re-reads it when its stat (mtime, size, inode) changes.
8. The switch is checked when a unit of audio is about to be written, never once
   at start-up. Off then means no file at all.

The winning source is reported as one of ``cli``, ``env``, ``file`` or
``default``. :func:`resolve` is the switch alone; :class:`Switch` is what a
long-running capture holds.

``ears recording status [--json]`` prints what this process would resolve (no
CLI layer: that belongs to a capture). ``ears recording on|off`` writes the
file key, atomically, keeping every other key, and says so when a higher layer
wins over what it wrote.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Optional

from .home import refuse_stranded, settings_dir

ENV_RECORD = "EARS_RECORD"
ENV_DAYS = "EARS_RECORD_DAYS"
ENV_NOTICE = "EARS_RECORD_NOTICE"
ENV_SPOKEN = "EARS_RECORD_SPOKEN_NOTICE"
KEY_RECORD = "record"
KEY_DAYS = "record_days"
KEY_NOTICE = "record_notice"
KEY_SPOKEN = "record_spoken_notice"
DEFAULT_RECORD = False
DEFAULT_DAYS = 7
DEFAULT_NOTICE = True
DEFAULT_SPOKEN = True

ON_WORDS = frozenset({"1", "true", "on"})
OFF_WORDS = frozenset({"0", "false", "off"})

SOURCES = ("cli", "env", "file", "default")

#: What `on` says, and what the docs say: the switch keeps every voice.
ALL_VOICES = "every voice the mic hears is kept, guests and TV included"

_DAYS_RE = re.compile(r"[0-9]+")  # fullmatch only: "$" would let a trailing newline through


def conf_path() -> str:
    """ears.json: ``$EARS_CONF`` if set and not empty, else ``ears.json`` in
    ``ears.home.settings_dir()``.
    The same answer as :func:`ears.listen.stt.conf_path`, without importing the capture."""
    return os.environ.get("EARS_CONF") or str(settings_dir() / "ears.json")


@dataclass(frozen=True)
class Record:
    """The ``record`` switch: ``on``, the layer that set it, and whether that layer's
    value was invalid (then ``on`` is False: rule 4). ``value`` is the raw value the
    winning layer held (None for the default)."""

    on: bool
    source: str
    invalid: bool = False
    value: object = None

    def as_dict(self) -> dict:
        return {"on": self.on, "source": self.source, "invalid": self.invalid}

    @property
    def word(self) -> str:
        return "on" if self.on else "off"


@dataclass(frozen=True)
class Days:
    """``record_days``: the retention in days (0 = never prune) and the layer that set it.
    ``invalid`` is always False: an invalid layer is skipped (rule 5)."""

    value: int
    source: str
    invalid: bool = False

    def as_dict(self) -> dict:
        return {"value": self.value, "source": self.source, "invalid": self.invalid}

    @property
    def words(self) -> str:
        if self.value == 0:
            return "kept forever"
        return f"{self.value} day{'' if self.value == 1 else 's'}"


@dataclass(frozen=True)
class FileLayer:
    """What ears.json gave: ``data`` is its object, or None when the file layer is NOT SET
    for both keys (rule 2), with ``why`` saying which case it was."""

    path: str
    data: Optional[dict]
    why: Optional[str] = None
    stamp: Optional[tuple] = None


def _stat(path: str) -> Optional[tuple]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size, st.st_ino)


def read_file(path: Optional[str] = None) -> FileLayer:
    """ears.json as a :class:`FileLayer`. Never raises."""
    p = path or conf_path()
    stamp = _stat(p)
    try:
        with open(p, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return FileLayer(p, None, "no file", stamp)
    except (OSError, UnicodeDecodeError) as exc:
        return FileLayer(p, None, f"unreadable ({type(exc).__name__}: {exc})", stamp)
    try:
        d = json.loads(text)
    except ValueError as exc:
        return FileLayer(p, None, f"not JSON ({exc})", stamp)
    if not isinstance(d, dict):
        return FileLayer(p, None, f"top level is a {type(d).__name__}, not an object", stamp)
    return FileLayer(p, d, None, stamp)


def _env_value(env: Mapping[str, str], name: str) -> Optional[str]:
    v = env.get(name)
    return v if v else None  # rule 1: unset or empty is NOT SET


def _switch_from(cli: Optional[bool], env: Mapping[str, str], file: FileLayer, warn: Callable[[str], None],
                 *, env_name: str, key: str, default: bool, fails_to: bool, what: str) -> Record:
    """A boolean setting from its layers: the CLI, then ``env_name``, then ``key`` in the
    file, then ``default``. An invalid value wins at its layer with ``fails_to``."""
    said = f"{what} {'ON' if fails_to else 'OFF'} ({'fails to on' if fails_to else 'fails closed'})"
    if cli is not None:
        return Record(bool(cli), "cli", False, bool(cli))
    raw = _env_value(env, env_name)
    if raw is not None:
        word = raw.strip().lower()
        if word in ON_WORDS:
            return Record(True, "env", False, raw)
        if word in OFF_WORDS:
            return Record(False, "env", False, raw)
        warn(f"{env_name}={raw!r} is not one of 1/true/on or 0/false/off: {said}")
        return Record(fails_to, "env", True, raw)
    if file.data is not None and key in file.data:
        v = file.data[key]
        if isinstance(v, bool):
            return Record(v, "file", False, v)
        warn(f"{file.path}: \"{key}\": {json.dumps(v)} is not true or false: {said}")
        return Record(fails_to, "file", True, v)
    return Record(default, "default", False, None)


def record_from(cli: Optional[bool], env: Mapping[str, str], file: FileLayer,
                warn: Callable[[str], None]) -> Record:
    """The ``record`` switch from its layers (rules 1-4): an invalid value fails closed."""
    return _switch_from(cli, env, file, warn, env_name=ENV_RECORD, key=KEY_RECORD,
                        default=DEFAULT_RECORD, fails_to=False, what="recording")


def notice_from(env: Mapping[str, str], file: FileLayer, warn: Callable[[str], None]) -> Record:
    """``record_notice`` (rule 10): whether a call states its recording status in its chat.
    No CLI layer; default ON; an invalid value FAILS TO ON. The ears only report it: the
    call bot is what acts on it."""
    return _switch_from(None, env, file, warn, env_name=ENV_NOTICE, key=KEY_NOTICE,
                        default=DEFAULT_NOTICE, fails_to=True, what="the call's recording notice")


def spoken_from(env: Mapping[str, str], file: FileLayer, warn: Callable[[str], None]) -> Record:
    """``record_spoken_notice`` (rule 11): whether a call that is being recorded starts with
    a spoken notice saying so. Like ``record_notice`` and independent of it: no CLI layer,
    default ON, an invalid value FAILS TO ON. The ears only report it."""
    return _switch_from(None, env, file, warn, env_name=ENV_SPOKEN, key=KEY_SPOKEN,
                        default=DEFAULT_SPOKEN, fails_to=True, what="the call's spoken recording notice")


def days_from(env: Mapping[str, str], file: FileLayer, warn: Callable[[str], None]) -> Days:
    """``record_days`` from its layers (rules 1-3, 5). There is no CLI layer."""
    raw = _env_value(env, ENV_DAYS)
    if raw is not None:
        if _DAYS_RE.fullmatch(raw):
            return Days(int(raw), "env")
        warn(f"{ENV_DAYS}={raw!r} is not a whole number of days (0 or more): ignored")
    if file.data is not None and KEY_DAYS in file.data:
        v = file.data[KEY_DAYS]
        if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
            return Days(v, "file")
        warn(f"{file.path}: \"record_days\": {json.dumps(v)} is not a whole number of days (0 or more): ignored")
    return Days(DEFAULT_DAYS, "default")


def _collect() -> tuple[list[str], Callable[[str], None]]:
    out: list[str] = []
    return out, out.append


def _file_warning(file: FileLayer) -> Optional[str]:
    if file.why is None:
        return None
    return f"{file.path}: {file.why}: the file sets nothing"


def resolve(cli: Optional[bool] = None, *, env: Optional[Mapping[str, str]] = None,
            path: Optional[str] = None) -> tuple[bool, str]:
    """``(on, source)`` for the ``record`` switch, read now. ``cli``: True for
    ``--record``, False for ``--no-record``, None for no flag."""
    r = record_from(cli, os.environ if env is None else env, read_file(path), lambda _m: None)
    return r.on, r.source


@dataclass(frozen=True)
class Settings:
    """Every setting, read at one moment, and every warning the read raised."""

    record: Record
    record_days: Days
    record_notice: Record
    record_spoken_notice: Record
    warnings: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"record": self.record.as_dict(), "record_days": self.record_days.as_dict(),
                "record_notice": self.record_notice.as_dict(),
                "record_spoken_notice": self.record_spoken_notice.as_dict()}


def resolve_all(cli: Optional[bool] = None, *, env: Optional[Mapping[str, str]] = None,
                path: Optional[str] = None) -> Settings:
    """Every setting, read now (one read of the file), with its warnings."""
    env = os.environ if env is None else env
    file = read_file(path)
    warnings, warn = _collect()
    fw = _file_warning(file)
    if fw is not None:
        warnings.append(fw)
    return Settings(record_from(cli, env, file, warn), days_from(env, file, warn),
                    notice_from(env, file, warn), spoken_from(env, file, warn), warnings)


class Switch:
    """The switch a long-running process holds (rule 7).

    The CLI flag and the env are taken once, here. ears.json is followed: each
    :meth:`record` or :meth:`days` stats it, and re-reads it only when its mtime, size
    or inode changed. Each distinct warning goes to ``log`` once.
    """

    def __init__(self, cli: Optional[bool] = None, *, env: Optional[Mapping[str, str]] = None,
                 path: Optional[str] = None, log: Optional[Callable[[str], None]] = None) -> None:
        src = os.environ if env is None else env
        self.cli = cli
        self._env = {k: src[k] for k in (ENV_RECORD, ENV_DAYS, ENV_NOTICE, ENV_SPOKEN) if k in src}
        self._path = path
        self._log = log if log is not None else (lambda m: print(f"ears recording: {m}", file=sys.stderr, flush=True))
        self._said: set[str] = set()
        self._stamp: object = object()  # never equal to a stat: the first call reads
        self._record: Optional[Record] = None
        self._days: Optional[Days] = None

    @property
    def path(self) -> str:
        return self._path or conf_path()

    def _warn(self, msg: str) -> None:
        if msg not in self._said:
            self._said.add(msg)
            self._log(msg)

    def _refresh(self) -> None:
        p = self.path
        stamp = (p, _stat(p))
        if stamp == self._stamp and self._record is not None:
            return
        file = read_file(p)
        fw = _file_warning(file)
        if fw is not None:
            self._warn(fw)  # rule 2: said once, then the file layer is NOT SET
        self._record = record_from(self.cli, self._env, file, self._warn)
        self._days = days_from(self._env, file, self._warn)
        self._stamp = stamp

    def record(self) -> Record:
        """The switch now (the file re-read if it changed)."""
        self._refresh()
        return self._record  # type: ignore[return-value]

    def days(self) -> Days:
        """The retention now (the file re-read if it changed)."""
        self._refresh()
        return self._days  # type: ignore[return-value]

    def __call__(self) -> bool:
        return self.record().on


class Fixed:
    """A switch that never changes: for a library caller or a test that decides itself."""

    def __init__(self, on: bool, days: int = DEFAULT_DAYS) -> None:
        self._record = Record(bool(on), "cli", False, bool(on))
        self._days = Days(days, "default")
        self.cli = bool(on)

    def record(self) -> Record:
        return self._record

    def days(self) -> Days:
        return self._days

    def __call__(self) -> bool:
        return self._record.on


# -- the capture's CLI flag, as a running capture shows it --------------------------------


def _ps() -> str:
    try:
        r = subprocess.run(["ps", "-axww", "-o", "pid=,command="], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout if r.returncode == 0 else ""


def running_flags(ps: Callable[[], str] = _ps) -> list[tuple[int, bool]]:
    """``(pid, on)`` for each running ``ears.listen capture`` started with ``--record`` or
    ``--no-record`` (the last flag given, rule 6). Best effort: [] if ``ps`` fails."""
    out = []
    me = os.getpid()
    for line in ps().splitlines():
        parts = line.split()
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        pid, argv = int(parts[0]), parts[1:]
        if pid == me or "capture" not in argv:
            continue
        if not any(a == "ears.listen" or a.endswith("/ears") or a == "ears" for a in argv[: argv.index("capture")]):
            continue
        flag = None
        for a in argv[argv.index("capture") + 1:]:
            if a == "--record":
                flag = True
            elif a == "--no-record":
                flag = False
        if flag is not None:
            out.append((pid, flag))
    return out


# -- writing the file key -----------------------------------------------------------------


class WriteRefused(Exception):
    """ears.json is there but cannot be kept as it is: nothing was written."""


def write_record(on: bool, path: Optional[str] = None, days: Optional[int] = None) -> str:
    """Set ``"record": on`` (and ``"record_days": days`` when given) in ears.json, keeping
    every other key, in ONE atomic write (a temp file beside it, then a rename). A file
    that is not a JSON object is refused, never replaced. A new file is made 0600 in its
    directory; an existing one keeps its mode. A STRANDED file (``ears.home.stranded``) is
    refused: nothing is written. Returns the path."""
    if days is not None and (isinstance(days, bool) or not isinstance(days, int) or days < 0):
        raise WriteRefused(f"record_days must be a whole number of days, 0 or more, not {days!r}: nothing written")
    p = Path(path or conf_path())
    why = refuse_stranded(p, "ears.json", "ears recording")   # a 0.1.0 file not moved yet
    if why:
        raise WriteRefused(why)
    mode = None
    if p.exists():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            raise WriteRefused(f"{p} cannot be read ({exc}): fix or move it, nothing written") from None
        except ValueError as exc:
            raise WriteRefused(f"{p} is not JSON ({exc}): fix or move it, nothing written") from None
        if not isinstance(d, dict):
            raise WriteRefused(f"{p} is not a JSON object: fix or move it, nothing written")
        mode = p.stat().st_mode & 0o777
    else:
        d = {}
    d[KEY_RECORD] = bool(on)
    if days is not None:
        d[KEY_DAYS] = days
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=p.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2, ensure_ascii=False)
            f.write("\n")
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, p)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return str(p)


# -- `ears recording` -----------------------------------------------------------------------


def status_dict(cli: Optional[bool] = None, *, env: Optional[Mapping[str, str]] = None,
                path: Optional[str] = None, ps: Optional[Callable[[], str]] = None) -> dict:
    """What ``ears recording status --json`` prints: ``record``, ``record_days`` and
    ``record_notice`` (each with its ``source``), ``file``, ``warnings``, and with ``ps``
    the running captures that carry a flag (``captures``)."""
    st = resolve_all(cli, env=env, path=path)
    d = {**st.as_dict(), "file": path or conf_path(), "warnings": list(st.warnings)}
    if ps is not None:
        d["captures"] = [{"pid": pid, "flag": "--record" if on else "--no-record"} for pid, on in running_flags(ps)]
    return d


_ENV_OF = {KEY_RECORD: ENV_RECORD, KEY_DAYS: ENV_DAYS, KEY_NOTICE: ENV_NOTICE, KEY_SPOKEN: ENV_SPOKEN}


def _from(key: str, setting, env: Mapping[str, str], path: str) -> str:
    s = setting.source
    if s == "env":
        return f"env {_ENV_OF[key]}={env.get(_ENV_OF[key], '')}"
    if s == "file":
        return f"file {path}"
    return s


def line(env: Optional[Mapping[str, str]] = None, path: Optional[str] = None) -> str:
    """One line for the keeper's start note: ``off (default), retention 7 days (default)``."""
    env = os.environ if env is None else env
    p = path or conf_path()
    st = resolve_all(None, env=env, path=p)
    bad = ": INVALID, fails closed" if st.record.invalid else ""
    keep = "forever" if st.record_days.value == 0 else st.record_days.words
    return (f"{st.record.word} ({_from(KEY_RECORD, st.record, env, p)}{bad}), "
            f"retention {keep} ({_from(KEY_DAYS, st.record_days, env, p)})")


def _status(args, env, path, ps) -> int:
    if args.json:
        print(json.dumps(status_dict(env=env, path=path, ps=ps), ensure_ascii=False))
        return 0
    if args.line:
        print(line(env, path))
        return 0
    st = resolve_all(None, env=env, path=path)
    for w in st.warnings:
        print(f"warning: {w}", file=sys.stderr)
    rec, days, notice = st.record, st.record_days, st.record_notice
    bad = "  INVALID: fails closed, nothing is kept" if rec.invalid else ""
    print(f"recording: {rec.word}  (source: {_from(KEY_RECORD, rec, env, path)}){bad}")
    print(f"retention: {days.words}  (source: {_from(KEY_DAYS, days, env, path)})")
    nbad = "  INVALID: fails to on" if notice.invalid else ""
    print(f"call notice: {notice.word}  (source: {_from(KEY_NOTICE, notice, env, path)}){nbad}")
    spoken = st.record_spoken_notice
    sbad = "  INVALID: fails to on" if spoken.invalid else ""
    print(f"spoken call notice: {spoken.word}  (source: {_from(KEY_SPOKEN, spoken, env, path)}){sbad}")
    print(f"file: {path}")
    for pid, on in running_flags(ps):
        print(f"capture {pid} runs with {'--record' if on else '--no-record'}: recording is "
              f"{'ON' if on else 'OFF'} for it, whatever the env or the file say")
    if rec.on:
        print(f"While recording is on, {ALL_VOICES}.")
    return 0


def _set(on: bool, env, path, ps, days: Optional[int] = None) -> int:
    """``ears recording on|off``: write the file key, then say plainly whether it decides
    (rule 9). A higher layer that wins is named with its value; the exit is still 0."""
    try:
        wrote = write_record(on, path, days)
    except (WriteRefused, OSError) as exc:
        print(f"ears recording {'on' if on else 'off'}: {exc}", file=sys.stderr)
        return 1
    extra = f" and record_days: {days}" if days is not None else ""
    print(f"wrote record: {'true' if on else 'false'}{extra} to {wrote}")
    st = resolve_all(None, env=env, path=wrote)
    if days is not None and st.record_days.source == "env" and st.record_days.value != days:
        print(f"but {ENV_DAYS}={env.get(ENV_DAYS, '')} in this environment wins: retention stays "
              f"{st.record_days.words}")
    rec = st.record
    overridden = False
    if rec.source == "env":  # a higher layer is set: say so whether or not it agrees
        overridden = rec.on != on
        val = env.get(ENV_RECORD, "")
        if overridden:
            bad = " (not a valid value, so it fails closed)" if rec.invalid else ""
            print(f"but {ENV_RECORD}={val}{bad} in this environment wins: recording stays {rec.word.upper()}")
        else:
            print(f"note: {ENV_RECORD}={val} in this environment decides, not the file; it agrees: "
                  f"recording is {rec.word.upper()}")
    for pid, flag in running_flags(ps):
        if flag != on:
            overridden = True
            print(f"but capture {pid} runs with {'--record' if flag else '--no-record'}, which wins: "
                  f"recording stays {'ON' if flag else 'OFF'} for it until it is restarted without the flag")
        else:  # rule 9: a higher layer is set; say so even when it agrees, as for the env
            print(f"note: capture {pid} runs with {'--record' if flag else '--no-record'}, which decides "
                  f"for it, not the file; it agrees: recording is {'ON' if flag else 'OFF'} for it")
    if on and rec.on:
        keep = ("Kept audio is kept forever (record_days 0)." if st.record_days.value == 0
                else f"Kept audio is deleted after {st.record_days.words}.")
        print(f"Recording is ON: {ALL_VOICES}. {keep}")
    elif on:
        print(f"When the file decides, recording is on, and {ALL_VOICES}.")
    elif not overridden:
        print("Recording is OFF where the file decides: a running capture keeps no audio from its next clip.")
    # Rule 9's point: never say "off" to someone who is still recorded. This command sees only
    # its own environment; a capture's own EARS_RECORD (the keeper's, say) outranks the file.
    print(f"This command sees only this shell's environment. A capture started with {ENV_RECORD} set "
          f"keeps to that, not the file: the keeper log's start note (\"recording: ...\") shows which.")
    return 0


def main(argv: Optional[list[str]] = None, *, env: Optional[Mapping[str, str]] = None,
         ps: Callable[[], str] = _ps) -> int:
    ap = argparse.ArgumentParser(
        prog="ears recording",
        description="The ears' recording switch: whether captured audio is kept. Off by default. "
                    "Order: the capture's --record/--no-record, then $EARS_RECORD, then \"record\" in "
                    f"ears.json ({conf_path()}), then off.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("status", help="on or off and which layer decided, the retention, and the call notice")
    st.add_argument("--json", action="store_true",
                    help="one JSON object: record, record_days, record_notice (each with its source), "
                         "file, warnings, captures")
    st.add_argument("--line", action="store_true", help=argparse.SUPPRESS)  # the keeper's start note
    on = sub.add_parser("on", help=f"set \"record\": true in ears.json ({ALL_VOICES})")
    on.add_argument("--days", type=_days_arg, default=None, metavar="N",
                    help="also set \"record_days\": N in the same write: how long kept audio stays, "
                         "in whole days; 0 keeps it forever (default: leave record_days as it is)")
    sub.add_parser("off", help="set \"record\": false in ears.json")
    a = ap.parse_args(argv)
    env = os.environ if env is None else env
    path = conf_path()
    if a.cmd == "status":
        return _status(a, env, path, ps)
    return _set(a.cmd == "on", env, path, ps, getattr(a, "days", None))


def _days_arg(text: str) -> int:
    if not _DAYS_RE.fullmatch(text):
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number of days, 0 or more")
    return int(text)


if __name__ == "__main__":
    sys.exit(main())
