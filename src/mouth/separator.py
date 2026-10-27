"""separator - the separator's shipped files, its home, and ``mouth split --install``.

The separator is the Demucs runner ``mouth split`` starts in its own venv. Its files live
in ``separator/`` in a checkout and ship in the wheel as package data,
``mouth/_separator/`` (symlinks into ``separator/`` in the checkout; the build copies the
files). They are never imported by the mouth: ``run.py`` runs in the separator venv, and
``requirements.lock`` is what that venv is built from. torch and demucs are never a
dependency of this package.

**The home** is ``~/.local/share/ears/separator``, or ``$MOUTH_SEPARATOR_HOME``::

    <home>/venv/                       the separator venv (python 3.12, from the lock)
    <home>/models/955717e8.safetensors the model, checked against its full SHA-256
    <home>/installed.json              the stamp: written last, only when both are in

**The install** (:func:`install`), only when asked, never by ``ears services install``:

1. the host: an Apple-silicon Mac (arm64) on macOS 14 or later, else refused;
2. the venv, built with uv from the shipped lock, ``--require-hashes --no-build``: every
   artifact checked against the lock's hashes, and nothing built from source;
3. the model: :data:`MODEL`, one file at a pinned Hugging Face revision, its size and
   its full SHA-256 checked before it is moved into place;
4. the stamp, ``installed.json``. The play path reads it (:func:`read_stamp`) and never
   imports torch.

``--dry-run`` checks the host and says what it would build and fetch, with the sizes, and
builds, fetches and writes nothing. A refusal is one line on stderr, exit 2; a step that
failed (uv, the network) is one line on stderr, exit 1. Any stamp from before is removed
when a real install starts, so a half-done install never reads as installed.

**The model's source.** Model ``955717e8`` (Demucs ``htdemucs``) as
``955717e8.safetensors`` from ``adefossez/HTDemucs``, at a pinned revision. The hub
publishes that file's full SHA-256 (its LFS digest), so the pin can be checked against a
source other than our own download. The legacy ``.th`` is a pickle, and its publisher
gives only an 8-hex prefix of its SHA-256. A safetensors file is never unpickled, but
demucs's loader imports and calls the class its metadata names: so only the full SHA-256
pin makes it safe, and the class it names is checked as well.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # the package checkout (src/mouth/ -> root)

#: What the wheel ships under mouth/_separator/: the runner and the venv's lock.
SHIPPED = ("run.py", "requirements.lock")


class NoSeparatorFile(Exception):
    """Neither the checkout nor the package holds the named separator file."""


def separator_file(name: str) -> Path:
    """separator/NAME: the checkout's when run from one, else the wheel's mouth/_separator/
    copy. Neither: NoSeparatorFile, naming both places."""
    checkout = ROOT / "separator" / name
    if checkout.is_file():
        return checkout
    shipped = Path(__file__).resolve().parent / "_separator" / name
    if shipped.is_file():
        return shipped
    raise NoSeparatorFile(f"no {name}: not at {checkout} (a checkout) nor at {shipped} (the "
                          "package). Reinstall sessionvoice from a wheel that ships "
                          "mouth/_separator/")


# ---- what is installed, and where -------------------------------------------------------

HOME_ENV = "MOUTH_SEPARATOR_HOME"
PYTHON = "3.12"                       # the lock is compiled for it
MACOS = (14, 0)                       # the oldest macOS, as for the speech venv
SEPARATOR = "demucs"
DEMUCS_VERSION = "4.1.0"              # the lock's pin; the install checks the venv has it
TORCH_VERSION = "2.14.1"
VENV_SIZE = "about 1 GB, mostly torch"
STAMP = "installed.json"
STAMP_SCHEMA = "mouth.separator/1"
HUB = "https://huggingface.co"
INSTALL = "mouth split --install"     # the one command that makes a separator


@dataclass(frozen=True)
class Model:
    """One model file, pinned: where it comes from and the bytes it must be."""
    signature: str                    # the Demucs model's id
    file: str                         # its name, on the hub and in <home>/models/
    repo: str                         # the Hugging Face repo
    revision: str                     # the repo's commit, never a branch
    sha256: str                       # the file's full SHA-256 (the hub's LFS digest)
    size: int                         # its bytes
    klass: str                        # the class its safetensors metadata names

    @property
    def url(self) -> str:
        return f"{HUB}/{self.repo}/resolve/{self.revision}/{self.file}"

    @property
    def source(self) -> str:
        return f"{HUB}/{self.repo} at revision {self.revision}"


#: Demucs ``htdemucs`` (model ``955717e8``): what ``mouth split`` separates with.
MODEL = Model(
    signature="955717e8",
    file="955717e8.safetensors",
    repo="adefossez/HTDemucs",
    revision="cbc8a9b1a87023b7fd74e7b3412e6321c0eab003",
    sha256="d9fa14133cfcc034a6758923bb3a8ca9f8dfd0b582134643bbf83f72c17576dd",
    size=84_025_440,
    klass="demucs.htdemucs.HTDemucs",
)


def separator_home() -> Path:
    """The separator home: ``$MOUTH_SEPARATOR_HOME``, else ``~/.local/share/ears/separator``.
    Per user, outside the package, so an upgrade keeps the venv and the model."""
    raw = os.environ.get(HOME_ENV)
    return Path(os.path.expanduser(raw)) if raw else Path.home() / ".local/share/ears/separator"


def separator_py(home: Path | None = None) -> Path:
    """The python the runner runs on: the separator venv's."""
    return (home or separator_home()) / "venv/bin/python"


def model_path(home: Path | None = None) -> Path:
    """Where the model lives once installed: <home>/models/<file>."""
    return (home or separator_home()) / "models" / MODEL.file


def stamp_path(home: Path | None = None) -> Path:
    return (home or separator_home()) / STAMP


def read_stamp(home: Path | None = None) -> dict | None:
    """The install's stamp, when it says a separator is here: its schema is ours, and its
    python and its model file exist (the model's size matching). Else None. Cheap: it
    hashes nothing and imports nothing, so the play path may call it on every play."""
    try:
        stamp = json.loads(stamp_path(home).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(stamp, dict) or stamp.get("schema") != STAMP_SCHEMA:
        return None
    model = stamp.get("model")
    if not isinstance(model, dict) or not isinstance(stamp.get("python"), str):
        return None
    try:
        if not Path(stamp["python"]).exists():
            return None
        if Path(model["path"]).stat().st_size != model["size"]:
            return None
    except (KeyError, TypeError, OSError):
        return None
    return stamp


def installed(home: Path | None = None) -> bool:
    return read_stamp(home) is not None


# ---- the seams: tests fake the host, uv, the commands and the download ------------------

def _mac() -> tuple[str, str]:
    """(machine, macOS version; "" off macOS) of this host."""
    import platform
    return platform.machine(), platform.mac_ver()[0]


def _uv() -> str | None:
    """The uv that builds the venv, or None."""
    import shutil
    return shutil.which("uv")


def _sh(argv) -> subprocess.CompletedProcess:
    """One step of the install (uv venv, uv pip install, the venv's check). Never
    HF_HUB_OFFLINE: these are the explicit steps that may fetch."""
    env = {k: v for k, v in os.environ.items() if k != "HF_HUB_OFFLINE"}
    return subprocess.run([str(a) for a in argv], capture_output=True, text=True, env=env)


class FetchFailed(Exception):
    """The download did not complete."""


def _fetch(url: str, dest: Path, limit: int, client=None) -> None:
    """Download URL into DEST, following redirects; stop past LIMIT bytes (a file larger
    than the pin is refused by the caller anyway). Any failure: FetchFailed."""
    import httpx
    own = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0),
                                    follow_redirects=True)
    got = 0
    try:
        with client.stream("GET", url) as r:
            if r.status_code != 200:
                raise FetchFailed(f"HTTP {r.status_code} from {url}")
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    got += len(chunk)
                    f.write(chunk)
                    if got > limit:
                        break
    except httpx.HTTPError as e:
        raise FetchFailed(f"{type(e).__name__}: {e}") from e
    finally:
        if own:
            client.close()


# ---- the checks ---------------------------------------------------------------------------

def platform_refusal() -> str | None:
    """Why this host cannot run the separator (not arm64, not macOS 14+), or None."""
    machine, ver = _mac()
    try:
        parts = (tuple(int(x) for x in ver.split(".")[:2]) + (0,))[:2] if ver else ()
    except ValueError:
        parts = ()
    if machine == "arm64" and parts and parts >= MACOS:
        return None
    return (f"the separator needs an Apple-silicon Mac (arm64) on macOS {MACOS[0]} or later; "
            f"this host is {machine or 'unknown'} on {'macOS ' + ver if ver else 'no macOS'} "
            f"(its lock is built for that host only)")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def safetensors_klass(path: Path) -> str | None:
    """The class a safetensors file's metadata names (demucs's ``klass``), or None when its
    header cannot be read. Reads the header only (stdlib): nothing is loaded."""
    try:
        with open(path, "rb") as f:
            n = struct.unpack("<Q", f.read(8))[0]
            if n > 100 * 1024 * 1024:
                return None
            header = json.loads(f.read(n))
    except (OSError, ValueError, struct.error):
        return None
    meta = header.get("__metadata__") if isinstance(header, dict) else None
    klass = meta.get("klass") if isinstance(meta, dict) else None
    return klass if isinstance(klass, str) else None


def model_problem(path: Path) -> str | None:
    """Why PATH is not the pinned model (missing, size, SHA-256, class), or None."""
    try:
        size = path.stat().st_size
    except OSError:
        return f"no model file at {path}"
    if size != MODEL.size:
        return f"{path.name} is {size:,} bytes, not the pinned {MODEL.size:,}"
    got = sha256_of(path)
    if got != MODEL.sha256:
        return f"{path.name}: sha256 {got} is not the pinned {MODEL.sha256}"
    klass = safetensors_klass(path)
    if klass != MODEL.klass:
        return f"{path.name} names the class {klass!r}, not {MODEL.klass!r}"
    return None


def _mb(n: int) -> str:
    return f"{n / 1_000_000:.1f} MB"


def _pins(lock: Path) -> int:
    return sum(1 for line in lock.read_text().splitlines() if line[:1].isalnum())


def _last_line(r: subprocess.CompletedProcess) -> str:
    out = (r.stderr or r.stdout or "").strip().splitlines()
    return out[-1] if out else f"exit {r.returncode}"


#: What the venv's check prints: the versions the stamp records.
CHECK = ("import json, demucs, numpy, soundfile, torch; "
         "print(json.dumps({'demucs': demucs.__version__, 'torch': torch.__version__, "
         "'numpy': numpy.__version__, 'soundfile': soundfile.__version__}))")


# ---- mouth split --install ------------------------------------------------------------------

EXIT_FAILED, EXIT_REFUSED = 1, 2


def install(dry_run: bool = False, out=None, err=None) -> int:
    """``mouth split --install [--dry-run]``: build the separator venv and fetch the model,
    then write the stamp. Returns the exit code: 0 installed (or, dry, would install),
    2 refused, 1 a step failed. See the module's docstring for the steps."""
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr

    def say(msg: str) -> None:
        print(msg, file=out, flush=True)

    def refuse(msg: str) -> int:
        print(f"{INSTALL}: REFUSED: {msg}", file=err, flush=True)
        return EXIT_REFUSED

    def failed(msg: str) -> int:
        print(f"{INSTALL}: FAILED: {msg}", file=err, flush=True)
        return EXIT_FAILED

    bad = platform_refusal()
    if bad:
        return refuse(bad)
    uv = _uv()
    if not uv:
        return refuse("no uv on PATH: the separator's venv is built with uv. Install uv "
                      "(https://docs.astral.sh/uv/), then run it again")
    try:
        lock = separator_file("requirements.lock")
    except NoSeparatorFile as e:
        return refuse(str(e))
    pins = _pins(lock)
    home = separator_home()
    venv = home / "venv"
    py = separator_py(home)
    model = model_path(home)
    stamp = stamp_path(home)
    what = f"python {PYTHON}, demucs {DEMUCS_VERSION}, torch {TORCH_VERSION}"
    model_ok = model.exists() and model_problem(model) is None

    # the plan, sizes first
    say(f"separator home {home} (${HOME_ENV} moves it)")
    if py.exists():
        say(f"  venv {venv}: present; {pins} pins from {lock} re-applied, hash-checked")
    else:
        say(f"  venv {venv}: built with uv ({what}; {pins} packages pinned in {lock}, "
            f"hash-checked; {VENV_SIZE}; network)")
    if model_ok:
        say(f"  model {MODEL.signature}: present at {model}, its sha256 checked")
    else:
        say(f"  model {MODEL.signature}: {MODEL.file}, {_mb(MODEL.size)} ({MODEL.size:,} bytes), "
            f"from {MODEL.source}; checked against its full sha256 {MODEL.sha256}; network")
    say(f"  stamp {stamp}: written when both are in")
    if dry_run:
        say("dry run: nothing built, fetched or written")
        return 0

    try:
        stamp.unlink()                # a half-done install never reads as installed
    except FileNotFoundError:
        pass
    except OSError as e:
        return failed(f"cannot remove the old stamp {stamp}: {e}")
    try:
        home.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return refuse(f"cannot make {home}: {e}")

    # 1. the venv
    if not py.exists():
        say(f"building {venv} ({what}; network)")
        # a venv dir with no python in it (a build cut short): uv builds over it only with
        # --clear. It is ours: it is under the separator home.
        clear = ["--clear"] if venv.exists() else []
        r = _sh([uv, "venv", "--python", PYTHON, *clear, venv])
        if r.returncode != 0:
            return failed(f"uv venv {venv} failed: {_last_line(r)}")
    say(f"installing {pins} pinned packages into {venv} (hash-checked; network)")
    r = _sh([uv, "pip", "install", "--python", py, "--require-hashes", "--no-build", "-r", lock])
    if r.returncode != 0:
        return failed(f"uv pip install --require-hashes -r {lock} into {venv} failed: "
                      f"{_last_line(r)}")
    r = _sh([py, "-c", CHECK])
    try:
        versions = json.loads((r.stdout or "").strip().splitlines()[-1]) if r.returncode == 0 else None
    except (ValueError, IndexError):
        versions = None
    if not isinstance(versions, dict):
        return failed(f"{py} cannot import the separator's modules: {_last_line(r)}")
    if versions.get("demucs") != DEMUCS_VERSION:
        return refuse(f"{py} has demucs {versions.get('demucs')}, not the pinned {DEMUCS_VERSION}")

    # 2. the model
    if model_ok:
        say(f"model {MODEL.signature} present: {model}")
    else:
        if model.exists():
            say(f"model at {model} is not the pinned file ({model_problem(model)}); "
                "fetching it again")
        say(f"downloading {MODEL.file} ({_mb(MODEL.size)}) from {MODEL.source}")
        model.parent.mkdir(parents=True, exist_ok=True)
        part = model.with_name(f".{model.name}.part")
        try:
            _fetch(MODEL.url, part, MODEL.size)
        except FetchFailed as e:
            part.unlink(missing_ok=True)
            return failed(f"the download of {MODEL.file} failed: {e}")
        problem = model_problem(part)
        if problem:
            part.unlink(missing_ok=True)
            return refuse(f"the downloaded model was refused, and removed: {problem}")
        os.replace(part, model)
        say(f"model {MODEL.signature}: {model}, sha256 {MODEL.sha256} checked")

    # 3. the stamp, last
    record = {
        "schema": STAMP_SCHEMA,
        "separator": SEPARATOR,
        "python": str(py),
        "versions": versions,
        "lock": {"path": str(lock), "sha256": sha256_of(lock), "pins": pins},
        "model": {"signature": MODEL.signature, "path": str(model), "sha256": MODEL.sha256,
                  "size": MODEL.size, "repo": MODEL.repo, "revision": MODEL.revision},
        "installed_t": round(time.time(), 3),
    }
    tmp = stamp.with_name(f".{stamp.name}.{os.getpid()}")
    tmp.write_text(json.dumps(record, indent=2) + "\n")
    os.replace(tmp, stamp)
    say(f"separator installed: {stamp}")
    return 0
