"""The speech server on :8890: find the mlx-audio server the ears and the mouth use, or say
why there is none to use.

The ears' STT default and the mouth's Kokoro both point at ``http://127.0.0.1:8890/v1``.
Before ``ears services install`` installs a speech server of its own, it must know which of
these is true:

1. ``opt-out``: ``final.url`` names another engine and the mouth is not being installed, so
   :8890 is not ours to manage;
2. something holds :8890 (``lsof`` sees a listener on any address, or a connect succeeds):
   - it says it is mlx-audio (``GET /`` returns the welcome line, or ``/openapi.json`` lists
     mlx-audio's three audio routes): ``use`` it, and start nothing;
   - anything else (another body, not JSON, a timeout, a reset): ``refuse-foreign``. Start
     nothing, and name the holder;
3. nothing holds :8890, but a loaded launch agent runs ``mlx_audio.server`` (any label, so
   VoiceMode's job between restarts counts): ``refuse-loaded-down``. Never a second server;
4. nothing holds :8890 and no such job is loaded: ``install``.

Before steps 2 and 3: a server that is OURS under its old label (``OLD_LABELS``,
com.sessionvoice.mlx-audio), holding the port or loaded and down, is ``install`` too: install
moves it to ``LABEL`` as it moves every service, the old job booted out first. Only when no
other job runs ``mlx_audio.server`` and no unknown holder has the port.

The probes are small functions with no state: ``port_holder`` (lsof, then ps and the
launchd label), ``connects`` (a TCP connect), ``identify`` (``GET /``, then
``GET /openapi.json``) and ``loaded_jobs`` (a plist scan of ~/Library/LaunchAgents,
/Library/LaunchAgents and /Library/LaunchDaemons). ``probe`` runs them and
``decide`` is pure, so a test feeds it any host it likes. Host and port are parameters, so
a test points them at a stub on an ephemeral port and never at the live :8890.

Read-only, all of it: no POST, never ``GET /v1/audio/voices`` (it downloads a model), never
a bind, a start or a stop. Only ``ears services install`` and ``status`` run it; the capture
loop never does.
"""
from __future__ import annotations

import json
import os
import plistlib
import re
import socket
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

HOST = "127.0.0.1"
PORT = 8890
LABEL = "dev.sessionkit.voice.mlx-audio"     # our own launch agent's label; never com.voicemode.*
# our own agent's labels before the label rename: ours, so install MOVES a host off them (decide)
OLD_LABELS = ("com.sessionvoice.mlx-audio",)
SERVER = "mlx_audio.server"                  # what an mlx-audio job's ProgramArguments run
# mlx-audio's GET / (server.py, the same bytes 0.4.3 to upstream main)
WELCOME = "Welcome to the MLX Audio API server"
# /openapi.json's fallback: the OpenAI pair alone is any OpenAI-compatible server's;
# /v1/audio/separations is mlx-audio's own
ROUTES = ("/v1/audio/transcriptions", "/v1/audio/speech", "/v1/audio/separations")
ID_TIMEOUT = 2.0                             # GET /: a wedged server is "occupied, unknown"

KINDS = ("opt-out", "use", "refuse-foreign", "refuse-loaded-down", "install")
# which of the steps 1-4 above each kind is: what `ears services install --dry-run` names
STEP = {"opt-out": 1, "use": 2, "refuse-foreign": 2, "refuse-loaded-down": 3, "install": 4}


def default_url(host: str = HOST, port: int = PORT) -> str:
    return f"http://{host}:{port}/v1"


@dataclass(frozen=True)
class Holder:
    """Who listens on the port, as lsof and ps see it. Any field may be unknown."""
    pid: int | None = None
    command: str | None = None
    label: str | None = None

    def describe(self) -> str:
        """``pid 2000, launchd com.voicemode.mlx-audio``, or the command when no label runs it."""
        if self.pid is None:
            return "holder unknown: lsof does not see it (another user's, or no lsof)"
        if self.label:
            return f"pid {self.pid}, launchd {self.label}"
        return f"pid {self.pid}, {self.command or 'command unknown'}"


@dataclass(frozen=True)
class Job:
    """A launch agent whose plist runs mlx_audio.server. ``pid`` is launchctl list's: a pid,
    ``-`` (loaded, not running), or None (not loaded)."""
    label: str
    plist: Path
    pid: str | None = None

    @property
    def loaded(self) -> bool:
        return self.pid is not None


@dataclass(frozen=True)
class Probe:
    """What the probes saw. ``identity`` is ``mlx-audio``, ``foreign`` or ``no-answer`` (None
    when nothing holds the port, so nothing was asked); ``why`` says how it was told."""
    host: str = HOST
    port: int = PORT
    holder: Holder | None = None
    connects: bool = False
    identity: str | None = None
    why: str = ""
    jobs: tuple[Job, ...] = field(default_factory=tuple)

    @property
    def held(self) -> bool:
        return self.holder is not None or self.connects


@dataclass(frozen=True)
class Decision:
    """``kind`` is one of KINDS; ``line`` is what install prints; ``fatal`` means the ears
    would be deaf, so install exits non-zero."""
    kind: str
    line: str
    fatal: bool = False

    @property
    def installs(self) -> bool:
        return self.kind == "install"


# --- the probes: each small, read-only, and replaceable in a test ---------------------------

def _run(argv: list[str], timeout: float = 10.0) -> subprocess.CompletedProcess | None:
    """Run a read-only command; None when it is not on this host or does not finish."""
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def port_holder(port: int = PORT, listed: dict[str, str] | None = None) -> Holder | None:
    """The process listening on PORT on ANY address (``*``, 127.0.0.1, ``::1``), named by its
    command and, when launchd runs it, its label. None: lsof sees no listener, or there is no
    lsof (the connect check still runs). Not root, lsof sees only this user's processes."""
    r = _run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fp"])
    if r is None or r.returncode != 0:
        return None
    pids = [int(l[1:]) for l in r.stdout.splitlines() if l.startswith("p") and l[1:].isdigit()]
    if not pids:
        return None
    pid = pids[0]
    ps = _run(["ps", "-o", "command=", "-p", str(pid)])
    command = (ps.stdout.strip() or None) if ps is not None and ps.returncode == 0 else None
    label = next((lab for lab, p in (listed or {}).items() if p == str(pid)), None)
    return Holder(pid=pid, command=command, label=label)


def connects(host: str = HOST, port: int = PORT, timeout: float = 0.3) -> bool:
    """Does anything accept a TCP connection at HOST:PORT? No request is sent."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _get(url: str, timeout: float) -> tuple[int, bytes]:
    """One GET, with no proxy (a localhost probe must never leave the host)."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(urllib.request.Request(url, method="GET"), timeout=timeout) as r:
            return r.status, r.read(65536)
    except urllib.error.HTTPError as e:
        return e.code, e.read(65536)


def identify(host: str = HOST, port: int = PORT, timeout: float = ID_TIMEOUT) -> tuple[str, str]:
    """(identity, why): ask the server on HOST:PORT what it is. ``mlx-audio`` when ``GET /``
    returns the welcome line, or ``/openapi.json`` lists all of ROUTES; ``foreign`` when it
    answers anything else; ``no-answer`` when it does not answer in time (a wedged server),
    resets, or is not there. Only these two GETs, ever."""
    base = f"http://{host}:{port}"
    try:
        status, body = _get(base + "/", timeout)
    except Exception as e:                  # timeout, reset, refused: occupied, unknown
        return "no-answer", f"GET / did not answer in {timeout:g}s ({type(e).__name__}: {e})"
    try:
        msg = json.loads(body).get("message", "")
    except Exception:
        msg = ""
    if status == 200 and isinstance(msg, str) and msg.startswith(WELCOME):
        return "mlx-audio", "GET / returns the mlx-audio welcome line"
    said = f"GET / answered {status} {body[:60]!r}"
    try:
        status, body = _get(base + "/openapi.json", timeout)
        paths = json.loads(body).get("paths", {}) if status == 200 else {}
    except Exception:
        paths = {}
    if isinstance(paths, dict) and all(r in paths for r in ROUTES):
        return "mlx-audio", "/openapi.json lists mlx-audio's audio routes"
    return "foreign", f"{said}, not the mlx-audio welcome line, and /openapi.json lacks its routes"


_PORT_ARG = re.compile(r"--port[=\s]+(\d+)")
_PID = re.compile(r"^\s*pid = (\d+)", re.M)
SYSTEM_AGENTS = Path("/Library/LaunchAgents")      # loaded in the user's gui domain: launchctl list
SYSTEM_DAEMONS = Path("/Library/LaunchDaemons")    # loaded in the system domain: launchctl print


def _servers(d: Path, port: int):
    """(label, plist) for each plist in D whose program runs mlx_audio.server on PORT. A job
    that names another ``--port`` is not this port's; one that names none counts (fail safe).
    VoiceMode's hides the server in a ``/bin/sh -c`` string, so the joined args are searched.
    Unreadable plists are skipped."""
    d = Path(d)
    for path in sorted(d.glob("*.plist")) if d.is_dir() else []:
        try:
            with open(path, "rb") as f:
                data = plistlib.load(f)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        args = data.get("ProgramArguments") or []
        joined = " ".join(str(a) for a in ([data.get("Program")] if data.get("Program") else []) +
                          (list(args) if isinstance(args, list) else []))
        if SERVER not in joined:
            continue
        ports = {int(p) for p in _PORT_ARG.findall(joined)}
        if ports and port not in ports:
            continue
        yield str(data.get("Label") or path.stem), path


def _system_pid(label: str) -> str | None:
    """A system-domain job's state, as launchctl list gives a user job's: its pid, ``-`` when
    loaded and not running, None when ``launchctl print system/LABEL`` does not know it."""
    r = _run(["launchctl", "print", f"system/{label}"])
    if r is None or r.returncode != 0:
        return None
    m = _PID.search(r.stdout)
    return m.group(1) if m else "-"


def loaded_jobs(agents: Path, listed: dict[str, str], port: int = PORT,
                system_agents: Path | None = None, daemons: Path | None = None) -> tuple[Job, ...]:
    """The launch jobs that run mlx_audio.server on PORT, under ANY label, with their state:
    the user's AGENTS and /Library/LaunchAgents (their pid from ``listed``, launchctl list), and
    /Library/LaunchDaemons (from ``launchctl print system/LABEL``). A system job is invisible
    to a non-root lsof, so a loaded one that is down must still stop an install. Read only."""
    jobs = []
    for d in (agents, SYSTEM_AGENTS if system_agents is None else system_agents):
        for label, path in _servers(d, port):
            jobs.append(Job(label=label, plist=path, pid=listed.get(label)))
    for label, path in _servers(SYSTEM_DAEMONS if daemons is None else daemons, port):
        jobs.append(Job(label=label, plist=path, pid=_system_pid(label)))
    return tuple(jobs)


def probe(agents: Path, listed: dict[str, str], host: str = HOST, port: int = PORT,
          system_agents: Path | None = None, daemons: Path | None = None) -> Probe:
    """Run the probes for HOST:PORT. ``listed`` is ``launchctl list`` as {label: pid}, from
    the caller (services has it already). The GETs run only when something holds the port."""
    holder = port_holder(port, listed)
    conn = connects(host, port)
    identity, why = (None, "")
    if holder is not None or conn:
        identity, why = identify(host, port)
    return Probe(host=host, port=port, holder=holder, connects=conn, identity=identity, why=why,
                 jobs=loaded_jobs(agents, listed, port, system_agents, daemons))


# --- the decision: pure ---------------------------------------------------------------------

def _same_server(url: str, host: str, port: int) -> bool:
    """Does URL name HOST:PORT (localhost and 127.0.0.1 are one host)? The path does not count."""
    from urllib.parse import urlparse
    u = urlparse(str(url).strip())
    loop = {"127.0.0.1", "localhost"}
    h = u.hostname or ""
    same_host = h == host or (h in loop and host in loop)
    try:
        p = u.port or (443 if u.scheme == "https" else 80)
    except ValueError:
        return False
    return same_host and p == port


def ears_here(final_url: str | None, host: str = HOST, port: int = PORT) -> bool:
    """Do the ears hear through HOST:PORT? FINAL_URL None is the default, which is this port."""
    return final_url is None or _same_server(final_url, host, port)


def decide(p: Probe, final_url: str | None = None, mouth: bool = False) -> Decision:
    """The steps 1-4 above, top to bottom. FINAL_URL is the ears' engine (None: the default,
    this port); MOUTH is True when the mouth is being installed, whose Kokoro always rides
    this port. Only step 4 installs: an empty port on every address AND no loaded job that
    runs mlx_audio.server."""
    at = f":{p.port}"
    here = ears_here(final_url, p.host, p.port)
    # 1. the setting says not ours to manage (unless the mouth, which only speaks here, comes too)
    if not here and not mouth:
        return Decision("opt-out", f"speech: final.url is {final_url}; not managing {at}")
    # ours under an old label (OLD_LABELS), holding the port or loaded and down: install MOVES
    # it, as every service is moved (the old job booted out first, ours loaded once it has gone).
    # Only when no other job runs mlx_audio.server here and no unknown holder has the port: those
    # are the steps below
    mine = (*OLD_LABELS, LABEL)
    old = next((l for l in ([p.holder.label] if p.holder else []) + [j.label for j in p.jobs if j.loaded]
                if l in OLD_LABELS), None)
    others = [j for j in p.jobs if j.loaded and j.label not in mine]
    if old and not others and (not p.held or (p.holder is not None and p.holder.label in mine)):
        return Decision("install", f"speech: {old} is ours under its old label; install {LABEL} in its place")
    # 2. something holds the port: use it only if it says it is mlx-audio
    if p.held:
        who = p.holder.describe() if p.holder else "holder unknown: lsof does not see it"
        if p.identity == "mlx-audio":
            return Decision("use", f"speech: using mlx-audio on {at} ({who})")
        if p.identity == "no-answer":
            what = f"{at} is held but does not answer ({p.why})"
        else:
            what = f"{at} is held by something that is not mlx-audio ({p.why})"
        fix = (f"free {at}, or point final.url in ears.json at a speech server"
               if here else f"free {at}; the mouth falls back to `say` until then")
        return Decision("refuse-foreign",
                        f"speech: REFUSED: {what}; holder {who}. Installing nothing, starting nothing: {fix}",
                        fatal=here)
    # 3. a loaded job that runs mlx_audio.server, down or restarting: never a second server
    loaded = [j for j in p.jobs if j.loaded]
    if loaded:
        j = loaded[0]
        return Decision("refuse-loaded-down",
                        f"speech: {j.label} is loaded but {at} is not answering; not installing a second server")
    # 4. nothing there at all
    return Decision("install", f"speech: nothing on {at} and no mlx-audio job loaded; install {LABEL}")


def status_row(p: Probe) -> str:
    """The ``speech`` row of ``ears services status``: who serves the port, or none."""
    at = f":{p.port}"
    if p.held:
        who = p.holder.describe() if p.holder else "holder unknown: lsof does not see it"
        ours = " (ours)" if p.holder and p.holder.label == LABEL else ""
        if p.identity == "mlx-audio":
            return f"mlx-audio on {at}, {who}{ours}"
        if p.identity == "no-answer":
            return f"{at} held, not answering: {who}"
        return f"{at} held by something that is NOT mlx-audio: {who}; the ears cannot hear through it"
    loaded = [j for j in p.jobs if j.loaded]
    if loaded:
        return f"none answering on {at}; {loaded[0].label} is loaded" + (
            ", not running" if loaded[0].pid == "-" else f", pid {loaded[0].pid}")
    return f"none on {at}: no listener, no mlx-audio job loaded"


def final_url() -> str | None:
    """The ears' engine URL as install will leave it: EARS_STT_URL when given (install writes
    it), else ears.json's ``final.url``, else None (the default, this port)."""
    if os.environ.get("EARS_STT_URL"):
        return os.environ["EARS_STT_URL"]
    from .listen.stt import conf_path, final_of
    try:
        d = json.loads(Path(conf_path()).read_text())
    except Exception:
        return None
    url = final_of(d).get("url")
    return str(url) if url else None
