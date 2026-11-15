"""mic_check - did the ears keeper's room capture hear anything, within a bound?

``ears services install`` asks this after it loads the keeper, while the user is
still at the Mac. macOS asks for the microphone on behalf of the capture's own
interpreter, not the terminal, so the only proof of the grant that will be used
is the keeper's capture hearing something. The check therefore opens no
microphone itself: it waits, bounded, for the room capture's first heartbeat
with ``peak > 0`` in the heard log the keeper writes.

- **heard**: a room-capture heartbeat with sound. Said at once.
- **lid**: the keeper wrote ``ears-deaf`` ``room: lid ...``: the built-in mic is off.
- **call**: the keeper wrote ``room-off``: a call is live, the room mic is off.
- at the bound, **zeros** (beats of only silence), **error** (the capture stopped on an
  error, or wedged) or **nothing** (no beat at all). Each is NOT HEARD.

"The room capture's" rows are ``source == "mic"`` and ``session == "ears-keeper"``:
what the keeper starts under launchd (``keeper/ears-keeper.sh``, ``launch_capture``).
A call leg's rows (``call:N``) and any other session's never count.

Everything outside this module is a seam: the heard reader, the clock and the
sleep, the devices file, ``system_profiler`` and ``ps``. It reads no stdin and
behaves the same with or without a tty. It imports neither ``sounddevice`` nor
``ears.listen.sources`` (a test walks this file's imports to hold that).
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Mapping, Optional

from ears import heard
from ears.home import item_source, state_dir

# --- the bound ---------------------------------------------------------------------

BOUND_ENV = "EARS_MIC_CHECK_S"
#: Seconds to wait for the first beat with sound. A beat comes every 15 s of stream
#: time and the first about 20 s after the keeper loads, so 60 s holds three beats
#: and leaves room for an Allow click.
BOUND_S = 60.0
#: The most a bound may be: seconds, not minutes.
BOUND_MAX_S = 600.0
#: How often the heard log is read while waiting.
POLL_S = 1.0
#: ``system_profiler``'s limit, when the devices file is stale. Counts toward the bound.
PROFILER_TIMEOUT_S = 5.0
#: ``ps``'s limit, naming the capture's interpreter.
PS_TIMEOUT_S = 2.0

# --- the room capture, as the keeper starts it ----------------------------------------

ROOM_SOURCE = "mic"
ROOM_SESSION = "ears-keeper"
EV_DEAF = "ears-deaf"
EV_ROOM_OFF = "room-off"
EV_WEDGED = "capture-wedged"   # ears.listen.watchdog.ROW_EVENT, not imported: it pulls in the capture
LID_PREFIX = "room: lid"

# --- verdicts ---------------------------------------------------------------------

HEARD = "heard"
LID = "lid"
CALL = "call"
ZEROS = "zeros"
ERROR = "error"
NOTHING = "nothing"
#: The verdicts that fail install (exit 2).
NOT_HEARD = (ZEROS, ERROR, NOTHING, LID)

# --- the lines install prints -----------------------------------------------------------
#
# The row label is install's ``{name:6s} text`` form; failure lines go to stderr with
# install's own prefix. Placeholders are filled by :func:`line`.

LABEL = "mic"
FAIL_PREFIX = "ears services install: "

WOULD_WAIT = "would wait up to {bound} s for the keeper's first heartbeat with sound; nothing is opened now"
WAITING = ("listening up to {bound} s for the keeper's first sound ({env}){note}; "
           "if macOS asks to use the microphone, click Allow")
HEARD_LINE = "heard: {device} (peak {peak}), {after} s after the keeper loaded"

NOT_CHECKED = "not checked: "
NC_ONLY_NO_KEEPER = NOT_CHECKED + "--only has no keeper"
#: the keeper was asked for, and a refusal above dropped it
NC_NO_KEEPER = NOT_CHECKED + "no keeper in this run"
NC_LISTEN_OFF = NOT_CHECKED + "listening is off; install with --listen to check now"
NC_KEEPER_FAILED = NOT_CHECKED + "the keeper did not load (above)"
NC_HAND_MADE = NOT_CHECKED + "the keeper here is hand-made ({label}); install did not load it"
NC_OFF = NOT_CHECKED + BOUND_ENV + "=0"
NC_NO_INPUT = NOT_CHECKED + "this Mac has no microphone (no audio input device)"
NC_CALL = NOT_CHECKED + "a call is live, so the room mic is off until it ends"

REMEDY = "then run: ears services install --only keeper"
NOT_HEARD_LINE = ("mic NOT HEARD in {bound} s ({why}): open System Settings > Privacy & Security > "
                  "Microphone and turn on {app} (the ears keeper's capture), " + REMEDY)
LID_LINE = ("mic NOT HEARD: the lid is shut, so the built-in mic is off; "
            "open the lid or plug in a mic, " + REMEDY)
#: ears.json pins an input that is not connected: the keeper starts no capture on anything else
PINNED_LINE = ("mic NOT HEARD: ears.json pins the input to {name}, which is not connected; "
               "plug in {name}, or remove the pin from ears.json, " + REMEDY)
#: The parenthesis of NOT_HEARD_LINE, per verdict.
WHY = {ZEROS: "only silence", NOTHING: "no sound arrived", ERROR: "the capture failed"}


def secs(s: float) -> str:
    """Seconds as the lines print them: ``60``, ``0.5``."""
    return f"{s:g}"


def row(text: str) -> str:
    """One install row: the label padded to six, then the text."""
    return f"{LABEL:6s} {text}"


def fail(text: str) -> str:
    """One install failure line (stderr): install's prefix, then the text."""
    return FAIL_PREFIX + text


# --- the bound, from the environment ---------------------------------------------------


def bound(environ: Optional[Mapping[str, str]] = None) -> tuple[float, str]:
    """``(seconds, note)`` from ``EARS_MIC_CHECK_S``.

    - a number with 0 < n <= 600 is used, with no note;
    - ``0`` means the check is off: ``(0.0, "")``;
    - unset or empty is the default, with no note;
    - anything else is the default, with a note for the wait line:
      ``(EARS_MIC_CHECK_S=x is not seconds: 60 s)``.
    """
    env = os.environ if environ is None else environ
    raw = (env.get(BOUND_ENV) or "").strip()
    if not raw:
        return BOUND_S, ""
    try:
        n = float(raw)
    except ValueError:
        n = math.nan
    if n == 0:
        return 0.0, ""
    if math.isfinite(n) and 0 < n <= BOUND_MAX_S:
        return n, ""
    return BOUND_S, f"({BOUND_ENV}={raw} is not seconds: {secs(BOUND_S)} s)"


# --- where the keeper writes, and where the wait starts --------------------------------


def heard_dir() -> Path:
    """The heard log the KEEPER writes: the state root with the installer's root overrides
    left out, as the keeper's own job has it (the rule ``SPEAKER_LOGDIR`` follows).
    Never ``heard.log_dir()``, which follows this shell's overrides into a folder no job writes."""
    return state_dir(environ={}) / "logs" / "conversations"


def start_seq(directory: Optional[Path] = None) -> int:
    """The heard log's last ``seq`` now: take it just before the keeper loads, so every
    row the new keeper writes is after it."""
    return heard.last_seq(directory if directory is not None else heard_dir())


# --- inputs: is there a microphone at all? (opens nothing) ----------------------------


def devices_file() -> Path:
    """The audio devices file as the keeper's job finds it (no override in its env)."""
    return item_source("audio-devices", environ={})[0]


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def fresh_inputs(path: Path, *, alive: Callable[[int], bool] = _alive) -> Optional[list[str]]:
    """The input names in the audio devices file while it is FRESH (docs/audio-devices.md:
    an object, ``version`` 1, a live ``pid``); None when it is stale or missing. Stricter
    than the rule, never looser: ``inputs`` must be a list of devices with a string name."""
    try:
        d = json.loads(Path(path).read_text())
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(d, dict) or type(d.get("version")) is not int or d["version"] != 1:
        return None
    pid = d.get("pid")
    if type(pid) is not int or pid <= 0 or not alive(pid):
        return None
    ins = d.get("inputs")
    if not isinstance(ins, list):
        return None
    names = []
    for x in ins:
        if not isinstance(x, dict) or not isinstance(x.get("name"), str) or not x["name"]:
            return None
        names.append(x["name"])
    return names


def parse_profiler(text: str) -> list[str]:
    """``system_profiler SPAudioDataType``'s input devices, as the keeper's ``inputs()``
    reads them: a device is a line indented eight spaces ending ``:``; it is an input
    when an ``Input Channels`` line follows before the next device. Sorted, unique."""
    out, name = set(), ""
    for ln in text.splitlines():
        if ln.startswith(" " * 8) and len(ln) > 8 and ln[8] != " " and ln.endswith(":"):
            name = ln[8:-1]
            continue
        if "Input Channels" in ln and name:
            out.add(name)
            name = ""
    return sorted(out)


def _profiler(timeout: float) -> Optional[str]:
    try:
        r = subprocess.run(["system_profiler", "SPAudioDataType"], capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def inputs(*, path: Optional[Path] = None, alive: Callable[[int], bool] = _alive,
           profiler: Callable[[float], Optional[str]] = _profiler,
           timeout: float = PROFILER_TIMEOUT_S) -> Optional[list[str]]:
    """The Mac's audio input names: ``[]`` is none, None is unknown.

    The audio devices file's ``inputs`` while it is fresh, else the keeper's own
    fallback, ``system_profiler SPAudioDataType`` (``timeout`` seconds, then unknown).
    Neither needs a microphone grant, and neither opens a device."""
    got = fresh_inputs(path if path is not None else devices_file(), alive=alive)
    if got is not None:
        return got
    text = profiler(timeout)
    return None if text is None else parse_profiler(text)


# --- ears.json, read as the keeper reads it ----------------------------------------------


def keeper_conf(path) -> dict:
    """ears.json as the keeper's ``conf()`` reads it: the file's object, or ``{}`` when it is
    missing, unreadable or not an object (the keeper then hears, on the default input)."""
    try:
        d = json.loads(Path(path).read_text())
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    return d if isinstance(d, dict) else {}


def listening(conf: Mapping) -> bool:
    """The keeper's rule: the room's ear is off only for ``"listen": false``; a missing key is on."""
    return conf.get("listen") is not False


def pinned(conf: Mapping) -> Optional[str]:
    """The input ears.json pins the room to, or None for ``default`` (the keeper's rule:
    empty or missing is ``default``)."""
    want = str(conf.get("input") or "default").strip() or "default"
    return None if want == "default" else want


def pinned_absent(conf: Mapping, names: Optional[list]) -> Optional[str]:
    """The pinned input when it is not among NAMES (exact, any case: the keeper's
    ``grep -qxiF``), so the keeper starts no capture; None when nothing is pinned, the pin is
    connected, or the inputs are unknown."""
    want = pinned(conf)
    if want is None or names is None:
        return None
    return None if want.lower() in {n.lower() for n in names} else want


# --- the verdict: pure ---------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    """What the wait found. ``kind`` is one of HEARD, LID, CALL, ZEROS, ERROR, NOTHING.
    ``device``/``peak``: the beat that heard. ``pid``: the room capture's, if a row named
    one. ``peaks``: every room beat's peak seen. ``error``: the capture's own words."""
    kind: str
    elapsed: float = 0.0
    device: Optional[str] = None
    peak: Optional[int] = None
    pid: Optional[int] = None
    peaks: tuple = field(default_factory=tuple)
    error: Optional[str] = None

    @property
    def failed(self) -> bool:
        return self.kind in NOT_HEARD


def _rec(r) -> dict:
    """A heard record, from a dict or a reader's record (``heard.read_after``'s)."""
    rec = getattr(r, "rec", r)
    return rec if isinstance(rec, dict) else {}


def is_room(rec: Mapping) -> bool:
    """The row is the keeper's room capture's."""
    return rec.get("source") == ROOM_SOURCE and rec.get("session") == ROOM_SESSION


def _int(v) -> Optional[int]:
    return v if type(v) is int else None   # a bool is not a peak


def verdict(rows: Iterable, elapsed: float, bound_s: float) -> Optional[Verdict]:
    """The verdict on the rows written since the keeper loaded, or None: keep waiting.

    At once: a room-capture heartbeat with an int ``peak > 0`` (heard), an ``ears-deaf``
    whose text starts ``room: lid`` (lid), a ``room-off`` (call), the first in log order.
    At ``elapsed >= bound_s``: any room beat with ``peak == 0`` (zeros), else a room
    ``listen-stopped`` with reason ``error`` or a room ``capture-wedged`` (error), else
    nothing. A beat with no ``peak`` says nothing either way."""
    peaks: list[int] = []
    pid: Optional[int] = None
    error: Optional[str] = None
    errored = False
    for r in rows:
        rec = _rec(r)
        ev = rec.get("event") if rec.get("kind") == heard.EVENT else None
        if ev is None:
            continue
        if ev == EV_DEAF and str(rec.get("text") or "").startswith(LID_PREFIX):
            return Verdict(LID, elapsed, device=rec.get("device"), pid=pid, peaks=tuple(peaks))
        if ev == EV_ROOM_OFF:
            return Verdict(CALL, elapsed, device=rec.get("device"), pid=pid, peaks=tuple(peaks))
        if not is_room(rec):
            continue
        pid = _int(rec.get("pid")) or pid
        if ev == heard.EV_HEARTBEAT:
            peak = _int(rec.get("peak"))
            if peak is None:
                continue
            if peak > 0:
                return Verdict(HEARD, elapsed, device=rec.get("device"), peak=peak, pid=pid,
                               peaks=tuple(peaks + [peak]))
            peaks.append(peak)
        elif ev == heard.EV_LISTEN_STOPPED and rec.get("reason") == "error":
            errored, error = True, rec.get("error") or error
        elif ev == EV_WEDGED:
            errored, error = True, rec.get("why") or error
    if elapsed < bound_s:
        return None
    if peaks:
        return Verdict(ZEROS, elapsed, pid=pid, peaks=tuple(peaks), error=error)
    if errored:
        return Verdict(ERROR, elapsed, pid=pid, error=error)
    return Verdict(NOTHING, elapsed, pid=pid)


# --- the wait: bounded, never on a human ----------------------------------------------


def wait(start: int, bound_s: float, *, read=heard.read_after, clock: Callable[[], float] = time.monotonic,
         sleep: Callable[[float], None] = time.sleep, directory: Optional[Path] = None,
         poll_s: float = POLL_S) -> Verdict:
    """Poll the heard log for rows after ``start`` until :func:`verdict` gives one.

    Each poll is one read from the cursor the last one returned (``(file, offset)``), so a
    poll reads only what is new. It sleeps at most ``poll_s`` and never past the bound, so
    it returns by ``bound_s`` plus one poll plus one read. A read that fails is a poll
    with no rows."""
    d = directory if directory is not None else heard_dir()
    t0 = clock()
    rows: list[dict] = []
    seen, hint, offset = start, None, 0
    while True:
        try:
            recs, tail = read(seen, hint_file=hint, hint_offset=offset, directory=d)
        except OSError:
            recs, tail = [], None
        for r in recs:
            rec = _rec(r)
            if type(rec.get("seq")) is int and rec["seq"] > seen:
                rows.append(rec)
                seen = rec["seq"]
        if tail is not None:
            hint, offset = Path(tail[0]).name, tail[1]
        elapsed = clock() - t0
        v = verdict(rows, elapsed, bound_s)
        if v is not None:
            return v
        sleep(max(0.0, min(poll_s, bound_s - elapsed)))


# --- the app macOS asks about ----------------------------------------------------------


def _ps_comm(pid: int) -> Optional[str]:
    try:
        r = subprocess.run(["ps", "-o", "comm=", "-p", str(pid)], capture_output=True, text=True,
                           timeout=PS_TIMEOUT_S, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    out = r.stdout.strip()
    return out if r.returncode == 0 and out else None


def app_name(path: str) -> str:
    """The name the Privacy pane shows for an executable: ``X`` inside
    ``X.app/Contents/MacOS/``, else the file's own name."""
    parts = Path(path).parts
    for i in range(len(parts) - 3, -1, -1):
        if parts[i].endswith(".app") and parts[i + 1:i + 3] == ("Contents", "MacOS"):
            return parts[i][:-4] or Path(path).name
    return Path(path).name


def app(pid: Optional[int], py: str, *, comm: Callable[[int], Optional[str]] = _ps_comm,
        realpath: Callable[[str], str] = os.path.realpath) -> tuple[str, str]:
    """``(name, path)`` of the interpreter the room capture runs as: what macOS asks about.

    The capture's pid (from its rows) through ``ps -o comm=``; else ``py``, the
    ``EARS_PY`` install wrote. Resolved with ``realpath``, then named by :func:`app_name`."""
    path = comm(pid) if pid else None
    if not path or not os.path.isabs(path):
        path = py
    real = realpath(path)
    return app_name(real), real


# --- the lines, filled -----------------------------------------------------------------


def line(v: Verdict, bound_s: float, *, app_label: str = "", after: Optional[float] = None) -> str:
    """The one line a verdict prints: a ``row`` for heard and call, a ``fail`` line for
    the rest. ``app_label`` names the app (NOT HEARD); ``after`` is seconds since the
    keeper loaded (heard)."""
    if v.kind == HEARD:
        return row(HEARD_LINE.format(device=v.device or "the input", peak=v.peak,
                                     after=secs(round(v.elapsed if after is None else after))))
    if v.kind == CALL:
        return row(NC_CALL)
    if v.kind == LID:
        return fail(LID_LINE)
    return fail(NOT_HEARD_LINE.format(bound=secs(bound_s), why=WHY[v.kind], app=app_label))


def evidence(v: Verdict, bound_s: float, *, app_path: str = "") -> str:
    """The install log's ``mic`` record: what the screen's one line leaves out."""
    parts = [f"verdict {v.kind}", f"waited {v.elapsed:.1f} s of {secs(bound_s)} s",
             f"beats {len(v.peaks)}"]
    if v.peaks:
        parts.append("peaks " + ",".join(str(p) for p in v.peaks))
    if v.device:
        parts.append(f"device {v.device}")
    if v.pid:
        parts.append(f"pid {v.pid}")
    if app_path:
        parts.append(f"path {app_path}")
    if v.error:
        parts.append(f"error {v.error}")
    return "; ".join(parts)


def waiting(bound_s: float, note: str = "") -> str:
    """The line before the wait (install flushes it)."""
    return row(WAITING.format(bound=secs(bound_s), env=BOUND_ENV, note=f" {note}" if note else ""))
