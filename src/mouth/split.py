"""split - ``mouth split FILE``: make a song's four stems and its instrumental in its sidecar.

``mouth split FILE...`` runs in the foreground. For each file it makes the sidecar
:mod:`mouth.sidecar` reads first, ``<dir>/.<name>.d/``: ``stems/{vocals,drums,bass,other}``,
``mixes/instrumental`` and the manifest ``split.json``. It prints one JSON line per file.
``mouth play`` starts it detached for the instrumental it needs; the user may run it by
hand. ``--force`` splits a file whose outputs are current.

**One split, step by step.**

1. **Refused before anything** (exit 2): a URL, a file that is missing or unreadable, no
   separator (no ``$MOUTH_SEPARATOR`` and no install stamp: the line names
   ``mouth split --install``), a folder the sidecar cannot be written in, a file whose
   duration ``ffmpeg -i`` cannot read.
2. **The file's lock** (``<mouth dir>/splits/<key>.lock``, held for the whole split):
   a second ``mouth split`` of the same file waits on it, then finds the outputs made and
   makes nothing. Under it, outputs that are current (the manifest's source matches, and
   every output it names is there) are reported ``present`` and nothing runs.
3. **The record** ``<mouth dir>/splits/<key>.json`` (``key``: a hash of the real path)
   reads ``running`` (``pid``, ``started_t``, ``eta_s``, ``deadline_t``), then ``done``
   or ``failed`` (``detail``). Every record carries the source's ``size`` and
   ``mtime_ns``, so a failure is known to be about these bytes. It is written only under
   its own short lock (``<key>.json.lock``, :func:`record_lock`), never the file's split
   lock: ``mouth play`` reads it, and writes the first ``running`` record itself, under
   that lock. The child keeps a ``running`` record that already names its pid.
4. **The host's lock** (``<mouth dir>/splits/.one.lock``): one separation at a time.
5. **Decode and separate.** The mouth's own ffmpeg decodes the file (files only,
   ``-protocol_whitelist file``) to 44.1 kHz stereo s16le, piped to the separator's
   stdin. The separator is ``$MOUTH_SEPARATOR`` (a command, split as a shell would) or
   the installed runner (the venv's python, ``run.py``), given the runner's arguments
   (``--out``, ``--model``, ``--sha256``, ``--threads``: the core count minus two). Both
   processes run at ``nice`` :data:`NICE`. The separator writes into
   ``.<name>.d/.split-<pid>/`` and prints one JSON line. Past ``deadline_t``
   (``max(5 x eta, 600 s)``) both are killed and the split has failed.
6. **Finish.** The speech check (:func:`mouth.sidecar.speech_verdict`) gives the
   ``verdict``. ``split.json`` is written in the temp folder, then the outputs and the
   manifest are renamed into place: any old ones are moved aside first (the manifest
   before the outputs), then removed. The host's seconds per second of audio go into
   ``<mouth dir>/splits/speed.json``, a running mean, for the next ETA (:func:`eta_s`).
   The record becomes ``done``.

A failure removes the temp folder, and the sidecar folder too when the split made it and
it is empty: it never leaves a partial output. The record becomes ``failed``.

**Exit codes**, for the whole run: 2 if any file was refused, else 1 if any split failed,
else 0 (each file made, or already there).

**JSON lines**, one per file: ``file`` (the real path), ``state`` (``made``, ``present``,
``refused`` or ``failed``), ``sidecar``, and ``verdict``, ``split_s`` and ``outputs``
when made, or ``detail`` when it was not.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

from . import separator, sidecar
from .instrumental import probe_duration
from ears.home import log_dir

from .paths import mouth_dir

#: Names a command that speaks the runner's protocol, in place of the installed runner.
SEPARATOR_ENV = "MOUTH_SEPARATOR"

#: The folder under the mouth directory that holds the records, the locks and the speed.
SPLITS = "splits"
ONE_LOCK = ".one.lock"
SPEED = "speed.json"
LOG = "split.log"   # in the voice log folder (ears.home.log_dir), not in SPLITS
RECORD_SCHEMA = "mouth.split/1"

#: The separation's priority: below the audio threads and the speech server.
NICE = 10

#: Seconds of split per second of audio before the host has measured its own: errs long.
DEFAULT_SPEED = 0.4

#: The watchdog: a split is killed past ``max(DEADLINE_FACTOR x eta, DEADLINE_MIN_S)``.
DEADLINE_FACTOR = 5.0
DEADLINE_MIN_S = 600.0

#: The model the manifest names, for the installed runner.
MODEL_NAME = "htdemucs"

#: The decoded PCM the runner reads: s16le, 44.1 kHz, 2 channels.
RATE, CHANNELS = 44100, 2

#: The five outputs, by the names the runner reports them under.
OUTPUT_NAMES = (*sidecar.STEMS, "instrumental")

#: The folders a split writes in the sidecar, beside the manifest.
OUTPUT_DIRS = ("stems", "mixes")

EXIT_OK, EXIT_FAILED, EXIT_REFUSED = 0, 1, 2

RUNNING, DONE, FAILED = "running", "done", "failed"


class Refused(Exception):
    """Refused before any separation ran: exit 2."""


class Failed(Exception):
    """The split ran and failed: exit 1."""


# ---- where things live ---------------------------------------------------------------------

def splits_dir(d: Optional[Path] = None) -> Path:
    return (d or mouth_dir()) / SPLITS


def key(source) -> str:
    """The file's key: a hash of its real path."""
    return hashlib.sha256(os.path.realpath(source).encode()).hexdigest()[:32]


def record_path(source, d: Optional[Path] = None) -> Path:
    return splits_dir(d) / f"{key(source)}.json"


def _split_lock_path(source, d: Optional[Path] = None) -> Path:
    return splits_dir(d) / f"{key(source)}.lock"


def _record_lock_path(source, d: Optional[Path] = None) -> Path:
    return splits_dir(d) / f"{key(source)}.json.lock"


@contextlib.contextmanager
def _flock(path: Path):
    """Hold an exclusive lock on PATH (made if missing) until the block ends."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)                  # closing the fd drops the lock


# ---- the record ------------------------------------------------------------------------------

def record_lock(source, d: Optional[Path] = None):
    """The record's own short lock. Hold it across a read and a write of the record; never
    across a split (that is the file's split lock, held by the child)."""
    return _flock(_record_lock_path(source, d))


def read_record(source, d: Optional[Path] = None) -> Optional[dict]:
    """The file's record, or None (none, or not a record of ours)."""
    try:
        rec = json.loads(record_path(source, d).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict) or rec.get("schema") != RECORD_SCHEMA:
        return None
    return rec


def write_record(source, rec: dict, d: Optional[Path] = None) -> dict:
    """Write REC as the file's record, atomically. The caller holds :func:`record_lock`."""
    rec = {"schema": RECORD_SCHEMA, **rec}
    _write_json(record_path(source, d), rec)
    return rec


def pid_alive(pid) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def record_state(rec: Optional[dict], now: Optional[float] = None) -> Optional[str]:
    """What REC says now: ``running``, ``done``, ``failed``, or None (no record).

    A ``running`` record whose pid is dead, or that is past its ``deadline_t``, counts as
    ``failed``."""
    if not isinstance(rec, dict):
        return None
    state = rec.get("state")
    if state == RUNNING:
        if not pid_alive(rec.get("pid")):
            return FAILED
        deadline = rec.get("deadline_t")
        if isinstance(deadline, (int, float)) and (now or time.time()) > deadline:
            return FAILED
        return RUNNING
    return state if state in (DONE, FAILED) else None


def _source_stat(path: Path) -> dict:
    st = path.stat()
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}


# ---- the ETA ---------------------------------------------------------------------------------

def deadline_for(eta: float) -> float:
    """How long a split with this ETA may run before it is killed (seconds)."""
    return max(DEADLINE_FACTOR * eta, DEADLINE_MIN_S)


def read_speed(d: Optional[Path] = None) -> float:
    """This host's measured seconds of split per second of audio, else DEFAULT_SPEED."""
    try:
        rec = json.loads((splits_dir(d) / SPEED).read_text())
        v = float(rec["s_per_s"])
    except (OSError, ValueError, KeyError, TypeError):
        return DEFAULT_SPEED
    return v if v > 0 else DEFAULT_SPEED


def record_speed(s_per_s: float, d: Optional[Path] = None) -> dict:
    """Fold one split's seconds per second into the host's running mean."""
    path = splits_dir(d) / SPEED
    try:
        rec = json.loads(path.read_text())
        n, mean = int(rec["n"]), float(rec["s_per_s"])
    except (OSError, ValueError, KeyError, TypeError):
        n, mean = 0, 0.0
    n += 1
    mean += (s_per_s - mean) / n
    rec = {"s_per_s": round(mean, 4), "n": n, "updated_t": round(time.time(), 3)}
    _write_json(path, rec)
    return rec


def eta_s(duration_s: float, d: Optional[Path] = None) -> float:
    """How long a split of DURATION_S seconds of audio should take on this host."""
    return round(max(0.0, duration_s) * read_speed(d), 1)


# ---- the separator ---------------------------------------------------------------------------

def threads() -> int:
    """The separator's threads: the core count minus two, at least one."""
    return max(1, (os.cpu_count() or 1) - 2)


def separator_present() -> bool:
    """Whether a split can run: ``$MOUTH_SEPARATOR`` set, or the install's stamp present."""
    return bool(os.environ.get(SEPARATOR_ENV, "").strip()) or separator.installed()


def spawn(source, d: Optional[Path] = None) -> int:
    """Start ``mouth split SOURCE`` detached, the way ``ensure_player`` starts the player:
    a new session, stdin closed, its output in ``split.log`` in the voice log folder
    (``ears.home.log_dir``; it was ``<mouth dir>/splits/split.log`` before 0.3.2). Returns
    the child's pid; OSError if it cannot start. The one seam ``mouth play`` spawns
    through (``mouth.autosplit``), so a test spies on it and nothing splits for real."""
    d = d or mouth_dir()
    folder = splits_dir(d)
    folder.mkdir(parents=True, exist_ok=True)
    logs = log_dir()
    logs.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "MOUTH_DIR": str(d)}
    with open(logs / LOG, "ab") as log:
        child = subprocess.Popen([sys.executable, "-m", "mouth", "split", str(source)], env=env,
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                 start_new_session=True)
    return child.pid


def separator_argv(out_dir: Path) -> list[str]:
    """The separator's command line for a split into OUT_DIR. No separator: Refused."""
    cmd = os.environ.get(SEPARATOR_ENV, "").strip()
    stamp = separator.read_stamp()
    if cmd:
        try:
            prefix = shlex.split(cmd)
        except ValueError as e:
            raise Refused(f"${SEPARATOR_ENV} is not a command line: {e}") from e
        if stamp:
            model, sha = stamp["model"]["path"], stamp["model"]["sha256"]
        else:
            model, sha = str(separator.model_path()), separator.MODEL.sha256
    elif stamp:
        try:
            runner = separator.separator_file("run.py")
        except separator.NoSeparatorFile as e:
            raise Refused(str(e)) from e
        prefix = [stamp["python"], str(runner)]
        model, sha = stamp["model"]["path"], stamp["model"]["sha256"]
    else:
        raise Refused(f"no separator: run `{separator.INSTALL}` first")
    return [*prefix, "--out", str(out_dir), "--model", str(model), "--sha256", str(sha),
            "--threads", str(threads())]


def decode_argv(path: Path) -> list[str]:
    """ffmpeg, files only, to the runner's PCM on stdout."""
    return ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
            "-protocol_whitelist", "file", "-i", str(path), "-vn",
            "-f", "s16le", "-ar", str(RATE), "-ac", str(CHANNELS), "-"]


def _lower() -> None:
    """In the child, before exec: run below the audio threads."""
    os.nice(NICE)


def _separate(path: Path, out_dir: Path, timeout: float) -> dict:
    """Decode PATH into the separator, which writes into OUT_DIR. Its JSON line, or Failed."""
    argv = separator_argv(out_dir)
    env = {**os.environ, "HF_HUB_OFFLINE": "1"}       # a split never fetches
    with tempfile.TemporaryFile() as dec_err:
        try:
            dec = subprocess.Popen(decode_argv(path), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=dec_err, preexec_fn=_lower)
        except OSError as e:
            raise Failed(f"cannot run ffmpeg: {e}") from e
        try:
            sep = subprocess.Popen(argv, stdin=dec.stdout, stdout=subprocess.PIPE, env=env,
                                   preexec_fn=_lower)
        except OSError as e:
            dec.kill()
            dec.wait()
            raise Failed(f"cannot run the separator {argv[0]}: {e}") from e
        finally:
            dec.stdout.close()                         # the separator holds it now
        try:
            out, _ = sep.communicate(timeout=max(1.0, timeout))
        except subprocess.TimeoutExpired:
            for p in (sep, dec):
                p.kill()
                p.wait()
            raise Failed(f"the split ran past its deadline ({timeout:.0f} s) and was stopped")
        dec_rc = dec.wait()
        dec_err.seek(0)
        dec_text = dec_err.read().decode(errors="replace").strip()
    line = _last_json(out.decode(errors="replace"))
    if sep.returncode != 0 or not (line or {}).get("ok"):
        detail = (line or {}).get("detail") or f"exit {sep.returncode}, no report"
        if dec_rc != 0 and dec_text:
            detail = f"{detail} (ffmpeg: {dec_text.splitlines()[-1][:200]})"
        raise Failed(f"the separator failed: {detail}")
    if dec_rc != 0:
        tail = dec_text.splitlines()[-1][:200] if dec_text else f"exit {dec_rc}"
        raise Failed(f"ffmpeg could not decode the whole file: {tail}")
    return line


def _last_json(text: str) -> Optional[dict]:
    for raw in reversed(text.strip().splitlines()):
        try:
            v = json.loads(raw)
        except ValueError:
            continue
        return v if isinstance(v, dict) else None
    return None


def _outputs(line: dict, tmp: Path) -> dict:
    """The runner's outputs, each a file inside TMP under stems/ or mixes/. Else Failed."""
    outputs = line.get("outputs")
    if not isinstance(outputs, dict) or set(OUTPUT_NAMES) - set(outputs):
        raise Failed(f"the separator reported outputs {outputs!r}, not {', '.join(OUTPUT_NAMES)}")
    out = {}
    root = tmp.resolve()
    for name in OUTPUT_NAMES:
        rel = outputs[name]
        if not isinstance(rel, str):
            raise Failed(f"the separator reported {name} as {rel!r}")
        p = (tmp / rel).resolve()
        if p.parent.parent != root or p.parent.name not in OUTPUT_DIRS or not p.is_file():
            raise Failed(f"the separator's {name}, {rel}, is not a file under stems/ or mixes/")
        out[name] = p.relative_to(root).as_posix()
    return out


# ---- the sidecar -------------------------------------------------------------------------------

def current(path: Path) -> Optional[dict]:
    """The canonical sidecar's manifest when its outputs are current: the manifest fits the
    source (:func:`mouth.sidecar.is_stale`) and every output it names is there. Else None."""
    folder = sidecar.canonical_dir(path)
    m = sidecar.read_manifest(folder)
    if not m or sidecar.is_stale(m, path, probe_duration):
        return None
    outputs = m.get("outputs")
    if not isinstance(outputs, dict) or set(OUTPUT_NAMES) - set(outputs):
        return None
    for rel in outputs.values():
        if not isinstance(rel, str) or not (folder / rel).is_file():
            return None
    return m


def _writable(path: Path) -> None:
    folder = sidecar.canonical_dir(path)
    where = folder if folder.exists() else path.parent
    if not os.access(where, os.W_OK | os.X_OK):
        raise Refused(f"cannot write the sidecar: {where} is not writable")


def _clear_leftovers(folder: Path) -> None:
    """Temp and swapped-aside folders of splits that died. Under the file's lock, every one
    there is a leftover: no other split of this file runs."""
    try:
        names = [p for p in folder.iterdir() if p.name.startswith((".split-", ".old-"))]
    except OSError:
        return
    for p in names:
        shutil.rmtree(p, ignore_errors=True)


def _hand_filled(folder: Path) -> bool:
    """A sidecar with outputs and no manifest: someone else's, not replaced unless forced."""
    if (folder / sidecar.MANIFEST).exists():
        return False
    return any((folder / n).exists() for n in OUTPUT_DIRS)


def _install(tmp: Path, folder: Path) -> None:
    """Rename TMP's outputs and manifest into FOLDER. Old ones go aside first, the manifest
    before the outputs, so a reader never sees an old manifest over new outputs, then are
    removed. A rename that fails puts the old ones back."""
    aside = folder / f".old-{os.getpid()}"
    moved, placed = [], []
    try:
        for name in (sidecar.MANIFEST, *OUTPUT_DIRS):
            p = folder / name
            if p.exists() or p.is_symlink():
                aside.mkdir(exist_ok=True)
                os.rename(p, aside / name)
                moved.append(name)
        for name in (*OUTPUT_DIRS, sidecar.MANIFEST):
            os.rename(tmp / name, folder / name)
            placed.append(name)
    except OSError:
        for name in placed:
            with contextlib.suppress(OSError):
                os.rename(folder / name, tmp / name)
        for name in moved:
            with contextlib.suppress(OSError):
                os.rename(aside / name, folder / name)
        raise
    finally:
        if aside.exists():
            shutil.rmtree(aside, ignore_errors=True)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


# ---- one file -------------------------------------------------------------------------------

def split_file(source, force: bool = False, d: Optional[Path] = None) -> tuple[int, dict]:
    """Split SOURCE into its sidecar. Returns the exit code and its JSON line."""
    d = d or mouth_dir()
    raw = str(source)
    if "://" in raw:
        return EXIT_REFUSED, {"file": raw, "state": "refused",
                              "detail": "a URL is never split: name a file"}
    path = Path(os.path.expanduser(raw)).resolve()
    folder = sidecar.canonical_dir(path)
    line = {"file": str(path), "sidecar": str(folder)}
    me = os.getpid()
    rec_base: dict = {"source": str(path), "pid": me}

    def finish(state: str, own_only: bool, **extra) -> None:
        """The record's last write, under its own lock. OWN_ONLY: only replace a record
        that is this process's running one (a refusal that is not about the file)."""
        with record_lock(path, d):
            rec = read_record(path, d)
            ours = isinstance(rec, dict) and rec.get("state") == RUNNING and rec.get("pid") == me
            if own_only and not ours:
                return
            write_record(path, {**(rec if ours else {}), **rec_base, "state": state,
                                "finished_t": round(time.time(), 3), **extra}, d)

    try:
        if not path.is_file():
            raise Refused(f"no such file: {path}")
        if not os.access(path, os.R_OK):
            raise Refused(f"cannot read {path}")
        rec_base.update(_source_stat(path))      # every record names the bytes it is about
    except (Refused, OSError) as e:
        return EXIT_REFUSED, {**line, "state": "refused", "detail": str(e)}
    if not separator_present():
        detail = f"no separator: run `{separator.INSTALL}` first"
        finish(FAILED, own_only=True, detail=detail)
        return EXIT_REFUSED, {**line, "state": "refused", "detail": detail}
    try:
        _writable(path)
    except Refused as e:
        finish(FAILED, own_only=False, detail=str(e))
        return EXIT_REFUSED, {**line, "state": "refused", "detail": str(e)}

    with _flock(_split_lock_path(path, d)):
        if folder.is_dir():
            _clear_leftovers(folder)
        made = None if force else current(path)
        if made is not None:
            finish(DONE, own_only=True, verdict=sidecar.manifest_verdict(made))
            return EXIT_OK, {**line, "state": "present",
                             "verdict": sidecar.manifest_verdict(made)}
        try:
            duration = probe_duration(path)
            if duration is None:
                raise Refused(f"ffmpeg -i gives no duration for {path}: not a sound file "
                              "ffmpeg can read")
            if not force and folder.is_dir() and _hand_filled(folder):
                raise Refused(f"{folder} holds stems/ or mixes/ with no {sidecar.MANIFEST}: "
                              "not made by mouth split; --force replaces them")
        except Refused as e:
            finish(FAILED, own_only=False, detail=str(e))
            return EXIT_REFUSED, {**line, "state": "refused", "detail": str(e)}

        eta = eta_s(duration, d)
        with record_lock(path, d):
            rec = read_record(path, d)
            if not (isinstance(rec, dict) and rec.get("state") == RUNNING and rec.get("pid") == me):
                now = time.time()
                write_record(path, {**rec_base, "state": RUNNING, "started_t": round(now, 3),
                                    "eta_s": eta, "deadline_t": round(now + deadline_for(eta), 3),
                                    "duration_s": duration}, d)

        made_folder = not folder.exists()
        tmp = folder / f".split-{me}"
        try:
            with _flock(splits_dir(d) / ONE_LOCK):
                t0 = time.monotonic()
                with record_lock(path, d):
                    rec = read_record(path, d) or {}
                    deadline = rec.get("deadline_t")
                    if not isinstance(deadline, (int, float)) or deadline < time.time():
                        deadline = time.time() + deadline_for(eta)   # waited past it in the queue
                        write_record(path, {**rec, "deadline_t": round(deadline, 3)}, d)
                tmp.mkdir(parents=True)
                report = _separate(path, tmp, deadline - time.time())
                outputs = _outputs(report, tmp)
                levels = report.get("levels_db") if isinstance(report.get("levels_db"), dict) else {}
                verdict = sidecar.speech_verdict(levels.get("mix"), levels.get("instrumental"))
                split_s = round(time.monotonic() - t0, 3)
                manifest = {
                    "version": sidecar.MANIFEST_VERSION,
                    "separator": report.get("separator") or separator.SEPARATOR,
                    "model": MODEL_NAME,
                    "signature": report.get("signature"),
                    "versions": report.get("versions"),
                    "device": report.get("device") or "cpu",
                    "split_s": split_s,
                    "made_t": round(time.time(), 3),
                    "source": {"name": path.name, "size": rec_base["size"],
                               "mtime_ns": rec_base["mtime_ns"], "duration_s": duration},
                    "outputs": outputs,
                    "levels_db": levels,
                    "peak_scaled_db": report.get("peak_scaled_db"),
                    "verdict": verdict,
                }
                _write_json(tmp / sidecar.MANIFEST, manifest)
                try:
                    _install(tmp, folder)
                except OSError as e:
                    raise Failed(f"cannot move the outputs into {folder}: {e}") from e
                if duration > 0:
                    with contextlib.suppress(OSError):   # the ETA's input, never the split's
                        record_speed(split_s / duration, d)
        except (Failed, Refused, OSError) as e:
            code = EXIT_REFUSED if isinstance(e, Refused) else EXIT_FAILED
            failure = str(e) if isinstance(e, (Failed, Refused)) else f"{type(e).__name__}: {e}"
        else:
            failure = None
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            if made_folder and folder.is_dir():
                with contextlib.suppress(OSError):
                    folder.rmdir()                   # only when the split left it empty
        if failure is not None:                      # recorded once nothing partial is left
            finish(FAILED, own_only=False, detail=failure)
            return code, {**line, "state": "refused" if code == EXIT_REFUSED else "failed",
                          "detail": failure}
        finish(DONE, own_only=False, verdict=verdict, split_s=split_s)
        return EXIT_OK, {**line, "state": "made", "verdict": verdict, "split_s": split_s,
                         "outputs": outputs}


# ---- mouth split FILE... --------------------------------------------------------------------

def main(files, force: bool = False, out=None, err=None) -> int:
    """``mouth split FILE... [--force]``: one JSON line per file on OUT; a refusal or a
    failure is one line on ERR as well. The exit code is the worst: 2, then 1, then 0."""
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr
    worst = EXIT_OK
    for f in files:
        code, line = split_file(f, force=force)
        print(json.dumps(line, ensure_ascii=False), file=out, flush=True)
        if code != EXIT_OK:
            word = "REFUSED" if code == EXIT_REFUSED else "FAILED"
            print(f"mouth split: {word}: {line.get('file')}: {line.get('detail')}", file=err,
                  flush=True)
        worst = max(worst, code)                     # EXIT_REFUSED > EXIT_FAILED > EXIT_OK
    return worst
