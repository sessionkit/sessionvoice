"""ears speakers: a guided enrol for the speaker side-car.

    ears speakers enrol [--seconds 180]       a person at a terminal: all of the below, prompted
    ears speakers record [--seconds 180]      step 1: record the room's heard lines -> prints DIR
    ears speakers clusters DIR                step 2: who said what, cluster by cluster
    ears speakers name DIR L0=alice L1=bob    steps 3-5: make, check and commit the references

The split exists because the installer on a kit is often an agent with no terminal: it runs
`record`, reads each cluster's words to the user, and runs `name` with the names the USER
gives. A name is never inferred: it comes from a person who read what the cluster said.

- record: refuses unless the service python imports the diarizer and the model is cached
  (so nobody talks for minutes only to be refused at `clusters`), and unless the ears
  keeper's capture runs (--record-only follows its heard log and its device). It runs
  speaker-sidecar2.py --record-only <home>/enrol/<stamp> and stops it at --seconds, or at
  Enter on a terminal, by touching its stop file: never a kill.
- clusters: cluster-room.py DIR, the recorded lines diarised together; each line printed
  with its cluster (`L 0`) and its words.
- name: LABEL=NAME, LABEL as `L 0`, `L0` or `0`, NAME [a-z][a-z0-9_-]*; on a terminal with
  none given, one prompt per cluster, blank skips it. A named cluster needs 3 recorded lines.
  make-refs.py writes the clips into <home>/refs.new (0700); it refuses a cluster that is not
  one voice. Then the service's own start check (the side-car enrolls the new clips beside
  every kept one, and relabels an empty dir) runs before anything is committed. Commit: each
  NAME.wav/.txt moves into <home>/refs/ (0600 in 0700), replacing that name only. The
  recording is deleted after a good commit and kept on a failure; the job is restarted
  (kickstart -k) only if it is loaded; the status row is printed.

Every model step runs with HF_HUB_OFFLINE=1: enrol never downloads. The one download is
`ears services install --only speakers`, and with no cached model each step says so.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import services

FLOOR = 3            # recorded lines a named cluster needs; make-refs warns under 5 s
NAME = re.compile(r"[a-z][a-z0-9_-]*")                 # make-refs.py's rule for a NAME
LABEL = re.compile(r"\s*L?\s*(\d+)\s*")                # `L 0` (as cluster-room prints it), L0, 0
INSTALL = "ears services install --only speakers"

# The ears keeper's capture, as speaker-sidecar2.py's keeper_device() finds it in `ps` (its
# CAPTURE and KEEPER_SESSION): --record-only follows that capture, so without one it hears
# nothing. A test holds the two to the same answers.
CAPTURE = re.compile(r"(?:voice_mode|ears)\.listen capture\b")
KEEPER_SESSION = "ears-keeper"


class Refused(Exception):
    """One line saying why; ``code`` is the exit status (2: this host, 1: the recording)."""

    def __init__(self, msg: str, code: int = 2):
        super().__init__(msg)
        self.code = code


def _say(msg: str) -> None:
    print(msg, flush=True)


def _py() -> Path:
    return services.speaker_py()


def _script(name: str) -> str:
    return str(services._speakers_file(name))


_popen = subprocess.Popen     # the recorder's start: a seam, so a test never opens a mic


def _run(argv, **kw) -> subprocess.CompletedProcess:
    """A speakers step on the service python, offline (HF_HUB_OFFLINE=1): never a download."""
    return subprocess.run([str(a) for a in argv], env=services._offline_env(), check=False, **kw)


def enrol_root() -> Path:
    return services.speakers_home() / "enrol"


def _private_dir(d: Path) -> Path:
    """D made (with its parents) and chmod'd 0700: mkdir's mode is umask'd, and only on creation."""
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o700)
    return d


# ---- before anyone talks: the python, the model, the keeper --------------------------------

def preflight() -> None:
    """Refuse, in one line, when a later step would: no service python, no diarizer in it,
    or no model cached. Run before recording, so a refusal never costs anyone their minutes."""
    py = _py()
    if not py.exists():
        raise Refused(f"no speaker python at {py}: run `{INSTALL}` (it builds it), or set "
                      "EARS_SPEAKER_PY to a python with mlx-audio")
    missing = services.speakers_missing_module(py)
    if missing:
        raise Refused(f"{py} has no {missing}: the speaker side-car needs it (mlx-audio>=0.5.6 "
                      f"for the diarizer): run `{INSTALL}`")
    if not services.speakers_model_cached(py):
        raise Refused(f"the speaker model {services.SPEAKER_MODEL} is not cached for {py}: run "
                      f"`{INSTALL}` (the one download, about 190 MB). Enrol never downloads")


def keeper_device(ps_text: str) -> str | None:
    """The --device of the ears keeper's capture in `ps -axo command` text, else None; a
    BlackHole capture is a call tap, not a room mic (speaker-sidecar2.py keeper_device)."""
    for line in ps_text.splitlines():
        if not CAPTURE.search(line) or f"--session {KEEPER_SESSION}" not in line:
            continue
        m = re.search(r"--device (.+?)(?= --[a-z]|$)", line)
        if m and "blackhole" not in m.group(1).lower():
            return m.group(1).strip()
    return None


def _ps() -> str:
    try:
        return subprocess.run(["ps", "-axo", "command"], capture_output=True, text=True, timeout=10, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


# ---- 1. record -----------------------------------------------------------------------------

def _lines(d: Path) -> list[Path]:
    return sorted(p for p in d.glob("*.json") if p.stem.isdigit())


def _enter_pressed(timeout: float) -> bool:
    """True when the person at the terminal pressed Enter within TIMEOUT seconds."""
    if not sys.stdin.isatty():
        time.sleep(timeout)
        return False
    r, _, _ = select.select([sys.stdin], [], [], timeout)
    if r:
        sys.stdin.readline()
        return True
    return False


def record(seconds: float) -> Path:
    """Record the room's heard lines into a new <home>/enrol/<stamp>, until SECONDS pass or
    Enter. Returns the dir. Stops the recorder by its stop file, never by a signal."""
    preflight()
    dev = keeper_device(_ps())
    if dev is None:
        raise Refused("no ears keeper capture is running: record follows its heard log and its "
                      "mic. Start it (`ears services install`, or `ears services status`), then record")
    _private_dir(enrol_root())
    d = _private_dir(enrol_root() / time.strftime("%Y%m%d-%H%M%S"))
    stop = d / "STOP"
    from .heard import log_dir
    argv = [_py(), _script("speaker-sidecar2.py"), "--record-only", d, "--stop-file", stop,
            "--logdir", log_dir()]
    p = _popen([str(a) for a in argv], env=services._offline_env())
    _say(f"recording on {dev} into {d} for up to {seconds:.0f}s"
         + (": talk in turns, each person saying a few lines; Enter stops it" if sys.stdin.isatty() else ""))
    end = time.monotonic() + seconds
    while p.poll() is None and time.monotonic() < end:
        if _enter_pressed(min(0.5, max(0.0, end - time.monotonic()))):
            break
    stop.touch()                                   # the recorder's own stop: never a kill
    try:
        p.wait(timeout=15)
    except subprocess.TimeoutExpired:
        _say(f"WARNING: the recorder (pid {p.pid}) has not stopped yet; its stop file {stop} is in "
             "place and it exits at its next line. Nothing was killed")
    else:
        stop.unlink(missing_ok=True)
    n = len(_lines(d))
    _say(f"recorded {n} line{'s' * (n != 1)} into {d}")
    if n < FLOOR:
        raise Refused(f"only {n} recorded line{'s' * (n != 1)} in {d}: a person needs {FLOOR} to be "
                      "named. Record again, with each person saying more", code=1)
    return d


# ---- 2. clusters ---------------------------------------------------------------------------

def clusters(d: Path) -> list[dict]:
    """cluster-room.py D: diarise the recorded lines together and print each with its cluster.
    Writes and returns D/clusters.json."""
    d = Path(d)
    if not _lines(d):
        raise Refused(f"no recorded lines in {d}: run `ears speakers record` first", code=1)
    preflight()
    r = _run([_py(), _script("cluster-room.py"), d], capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    if r.returncode != 0:
        raise Refused(f"cluster-room.py failed on {d}: {_last(r.stderr)}", code=1)
    return json.loads((d / "clusters.json").read_text())


def _last(text: str) -> str:
    lines = [l for l in (text or "").strip().splitlines() if l.strip()]
    return lines[-1] if lines else "(no message)"


def _by_label(rows: list[dict]) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for r in rows:
        if r.get("label", -1) >= 0:             # -1: too little speech to say whose
            out.setdefault(int(r["label"]), []).append(r)
    return dict(sorted(out.items()))


# ---- 3. name -------------------------------------------------------------------------------

def parse_specs(specs) -> list[tuple[int, str]]:
    """LABEL=NAME pairs, LABEL as `L 0`, `L0` or `0`. Refuses a bad one, a name twice or a
    cluster twice: make-refs.py's rules, said before anything runs."""
    out = []
    for spec in specs:
        lab, sep, name = spec.partition("=")
        m = LABEL.fullmatch(lab)
        if not sep or not m:
            raise Refused(f"{spec!r} is not LABEL=NAME (L0=alice, 'L 0=alice' or 0=alice)", code=1)
        if not NAME.fullmatch(name):
            raise Refused(f"{name!r} is not a name: lower case, starting with a letter "
                          "([a-z][a-z0-9_-]*)", code=1)
        out.append((int(m[1]), name))
    if len({n for _, n in out}) < len(out):
        raise Refused("a name is given twice", code=1)
    if len({l for l, _ in out}) < len(out):
        raise Refused("a cluster is named twice", code=1)
    return out


def ask_names(rows: list[dict], ask=None) -> list[tuple[int, str]]:
    """One prompt per cluster, showing its lines: the person names it from what was said, or
    leaves it blank to skip it. Never a guess: an empty answer names nobody."""
    ask = ask or input
    out, used = [], set()
    for lab, lines in _by_label(rows).items():
        _say(f"\ncluster L{lab}: {len(lines)} line{'s' * (len(lines) != 1)}")
        for r in lines:
            _say(f"  {r.get('ts', '')}  {r.get('text', '')}")
        while True:
            name = ask(f"who said these? a name for L{lab} (blank skips it): ").strip().lower()
            if not name:
                break
            if not NAME.fullmatch(name):
                _say("  a name is lower case and starts with a letter ([a-z][a-z0-9_-]*)")
            elif name in used:
                _say(f"  {name} is already named above")
            else:
                out.append((lab, name)); used.add(name)
                break
    return out


def name(d: Path, specs: list[tuple[int, str]]) -> int:
    """Make the references for SPECS from recording D, check them as the service will start
    them, and commit them into <home>/refs. Nothing is committed unless every step passes."""
    d = Path(d)
    cj = d / "clusters.json"
    if not cj.exists():
        raise Refused(f"no {cj}: run `ears speakers clusters {d}` first", code=1)
    if not specs:
        raise Refused("no cluster named: nothing enrolled", code=1)
    groups = _by_label(json.loads(cj.read_text()))
    for lab, who in specs:
        n = len(groups.get(lab, []))
        if n < FLOOR:
            raise Refused(f"cluster L{lab} ({who}) has {n} recorded line{'s' * (n != 1)}; a person "
                          f"needs {FLOOR}: record again, with {who} saying more", code=1)
    preflight()
    home = _private_dir(services.speakers_home())
    refs, staged = home / "refs", home / "refs.new"
    shutil.rmtree(staged, ignore_errors=True)
    _private_dir(staged)
    try:
        r = _run([_py(), _script("make-refs.py"), d, *[f"L{l}={n}" for l, n in specs], "--out", staged],
                 capture_output=True, text=True)
        sys.stdout.write(r.stdout); sys.stderr.write(r.stderr)
        if r.returncode != 0:
            raise Refused(f"make-refs.py refused: {_last(r.stderr or r.stdout)}. Nothing was enrolled; "
                          f"the recording stays in {d}", code=1)
        start_check(refs, staged)
        commit(refs, staged)
    finally:
        shutil.rmtree(staged, ignore_errors=True)
    for _, who in specs:
        _say(f"enrolled {who}: {refs / (who + '.wav')}")
    if d.resolve().parent == enrol_root().resolve():   # only a recording enrol made: never a dir of yours
        shutil.rmtree(d, ignore_errors=True)
        _say(f"recording {d} deleted (voices are kept only as references)")
    _restart()
    return 0


def start_check(refs: Path, staged: Path) -> None:
    """The service's own start, before anything is committed: the side-car enrolls every
    reference it would start with (the staged ones replacing a kept one of the same name, plus
    refs-call/) and relabels an empty dir. A clip that is not one voice, or two clips that come
    back as one label, would crash-loop the service: here it refuses instead."""
    clips = {p.stem: p for p in sorted(refs.glob("*.wav"))}
    clips.update({p.stem: p for p in sorted(staged.glob("*.wav"))})
    argv = [_py(), _script("speaker-sidecar2.py"), "--enroll-s", "12"]   # what run.sh passes
    for n in sorted(clips):
        argv += ["--enroll", f"{n}={clips[n]}"]
    for p in sorted((refs.parent / "refs-call").glob("*.wav")):
        argv += ["--call-enroll", f"{p.stem}={p}"]
    with tempfile.TemporaryDirectory() as empty, tempfile.TemporaryDirectory() as logs:
        r = _run([*argv, "--relabel", empty, "--logdir", logs], capture_output=True, text=True)
    if r.returncode != 0:
        raise Refused(f"the side-car would not start with these references: "
                      f"{_last(r.stderr or r.stdout)}. Nothing was enrolled", code=1)


def commit(refs: Path, staged: Path) -> None:
    """Move each staged NAME.wav/.txt into REFS: 0600 files in a 0700 dir, a name replacing
    only itself (re-enrolling merges by name; nobody else's reference is touched)."""
    _private_dir(refs)
    for p in sorted(staged.iterdir()):
        if p.suffix in (".wav", ".txt"):
            p.chmod(0o600)
            os.replace(p, refs / p.name)


def _restart() -> None:
    """Restart the loaded job on the new references (no hand launchctl), then say its state.
    The job is under whichever label is loaded, the new one first: a host not yet moved off
    an old label (com.sessionvoice.ears-speakers, com.voicemode.ears-speakers) still runs it
    under that."""
    uid = os.getuid()
    label = services.loaded_label(uid, "speakers")
    if label:
        services._launchctl("kickstart", "-k", f"gui/{uid}/{label}")
        _say(f"restarted {label} on the new references")
    else:
        _say(f"{services.PREFIX}speakers is not loaded: `{INSTALL}` loads it")
    _say(f"{'speakers':6s} " + services._speakers_row(
        services._listed(services._launchctl("list").stdout.splitlines())))


# ---- enrol: all of it, for a person at a terminal ------------------------------------------

def enrol(seconds: float, ask=None) -> int:
    if not sys.stdin.isatty():
        raise Refused("enrol asks a person at a terminal for each name, and there is none here. "
                      "An agent runs the steps: `ears speakers record`, then `clusters DIR`, reads "
                      "each cluster's words to the person, and runs `name DIR L0=NAME ...` with "
                      "the names they give")
    d = record(seconds)
    rows = clusters(d)
    specs = ask_names(rows, ask)
    if not specs:
        raise Refused(f"no cluster named: nothing enrolled. The recording stays in {d}; "
                      f"`ears speakers name {d}` asks again", code=1)
    return name(d, specs)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="ears speakers", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="verb", required=True)
    for verb in ("enrol", "record"):
        s = sub.add_parser(verb)
        s.add_argument("--seconds", type=float, default=180.0, help="the longest it records (Enter stops it sooner)")
    sub.add_parser("clusters").add_argument("dir")
    s = sub.add_parser("name")
    s.add_argument("dir")
    s.add_argument("specs", nargs="*", metavar="LABEL=NAME",
                   help="L0=alice ('L 0' and 0 work too); none, on a terminal: asked per cluster")
    a = p.parse_args(argv)
    try:
        if a.verb == "enrol":
            return enrol(a.seconds)
        if a.verb == "record":
            d = record(a.seconds)
            _say(f"next: ears speakers clusters {d}")
            return 0
        if a.verb == "clusters":
            clusters(Path(a.dir))
            _say(f"next: ears speakers name {a.dir} L0=NAME ... (names from what each cluster said)")
            return 0
        specs = parse_specs(a.specs)
        if not specs and sys.stdin.isatty():
            cj = Path(a.dir) / "clusters.json"
            specs = ask_names(json.loads(cj.read_text())) if cj.exists() else []
        return name(Path(a.dir), specs)
    except Refused as e:
        print(f"ears speakers: REFUSED: {e}", file=sys.stderr)
        return e.code
    except services.NoKeeper as e:
        print(f"ears speakers: REFUSED: {e}", file=sys.stderr)
        return 2
