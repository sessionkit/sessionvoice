"""talk's per-install token: the secret a browser must show to read the room.

The file is ``<home>/talk/token``, where ``<home>`` is sessionvoice's home
(``ears.heard.base_dir()``, the rule the heard log follows: ``ears.home``). It holds
``secrets.token_urlsafe(32)``, 43 characters of ``[A-Za-z0-9_-]``, and one newline.
The file is 0600 and its directory 0700, both owned by the uid that runs talk.

- :func:`load_or_create` makes the token if it is missing. It writes a temporary
  file in the same directory (``O_EXCL|O_NOFOLLOW``, 0600), fsyncs it, then
  ``os.link``s it to ``token``. ``link`` fails if ``token`` exists, so the file
  appears whole or not at all, and of two creators racing, one wins and the other
  reads the winner's complete file.
- :func:`load` reads it and never creates it.
- :func:`rotate` writes a fresh token and ``os.replace``s it over the old one.
- :func:`matches` answers whether a value is the current token. It ``stat``s the
  file (and its directory) on every call and reads it again only when either has
  changed, so a rotation takes effect with no restart. A missing or refused file
  matches nothing: deleting the token never lets anyone in, and it is never quietly
  re-created at run time.

Refused, never repaired: a file or directory not owned by the effective uid; any
group or other bit on either; a token that is a symlink or not a regular file;
content that is not 43 or more urlsafe characters. A wider mode is not silently
tightened, because a token that was readable may already be known: rotate it.
Each refusal is a :class:`TokenError` (a ``ValueError``, like talk's other start-up
refusals) whose message names the path and the fix, and never the token.

The token never goes to argv, a URL query, a log line, stdout or stderr from here.
Stdlib only, plus the kit's own ``ears.heard`` (itself stdlib only) for the base.

Named ``token_store``, not ``token`` (the design's name): ``python3 src/talk/server.py``
puts ``src/talk`` first on ``sys.path``, where a ``token.py`` hides the stdlib ``token``
module and the server dies on import (dataclasses, inspect, tokenize, token).
"""
import errno, hmac, os, re, secrets, stat, threading
from pathlib import Path

from ears import heard

DIR_NAME = "talk"
FILE_NAME = "token"
NBYTES = 32                                  # secrets.token_urlsafe(32): 43 characters, 256 bits
TOKEN = re.compile(r"[A-Za-z0-9_-]{43,}")   # what a token file must hold (one trailing newline allowed)
MAX_BYTES = 4096                             # a token file larger than this is not a token
ROTATE = "talk token --rotate"


class TokenError(ValueError):
    """The token file or its directory is refused. The message names the path and the fix,
    never the token."""


def path() -> Path:
    """The token file, read at call time: ``<sessionvoice home>/talk/token``."""
    return heard.base_dir() / DIR_NAME / FILE_NAME


def _new() -> str:
    return secrets.token_urlsafe(NBYTES)


def _mode(st) -> str:
    return f"{stat.S_IMODE(st.st_mode):04o}"


def _check_dir(d: Path, what: str = "token directory") -> None:
    """Refuse a token directory that is not ours alone (0700, owned by the euid, not a symlink)."""
    try:
        fd = os.open(str(d), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as e:
        if e.errno in (errno.ELOOP, errno.ENOTDIR):
            raise TokenError(f"{what} {d} is not a directory (a symlink or a file); "
                             f"remove it and run `{ROTATE}`") from None
        raise
    try:
        st = os.fstat(fd)
    finally:
        os.close(fd)
    if st.st_uid != os.geteuid():
        raise TokenError(f"{what} {d} is owned by uid {st.st_uid}, not this user (uid "
                         f"{os.geteuid()}); remove it and run `{ROTATE}`")
    if stat.S_IMODE(st.st_mode) & 0o077:
        raise TokenError(f"{what} {d} is mode {_mode(st)}; it must have no group or other "
                         f"bits: chmod 700 {d}, then run `{ROTATE}` (a token there may be known)")


def private_dir(d, what: str = "token directory") -> None:
    """Make `d` 0700 if it is missing, then refuse it unless it is ours alone: owned by the
    euid, no group or other bits, not a symlink (TokenError). talk's unix socket lives in one
    by default: the token's own directory."""
    _ensure_dir(Path(d), what)


def _ensure_dir(d: Path, what: str = "token directory") -> None:
    """Make the token directory 0700 if it is missing, then check it."""
    d.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.mkdir(str(d), 0o700)
    except FileExistsError:
        pass
    else:
        os.chmod(str(d), 0o700)              # the umask may have taken bits; it never adds any
    _check_dir(d, what)


def _read(p: Path) -> str:
    """Read and check the token file. Raises FileNotFoundError if it is missing,
    TokenError if it is refused."""
    try:
        fd = os.open(str(p), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise TokenError(f"token file {p} is a symlink; remove it and run `{ROTATE}`") from None
        raise
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise TokenError(f"token file {p} is not a regular file; remove it and run `{ROTATE}`")
        if st.st_uid != os.geteuid():
            raise TokenError(f"token file {p} is owned by uid {st.st_uid}, not this user (uid "
                             f"{os.geteuid()}); remove it and run `{ROTATE}`")
        if stat.S_IMODE(st.st_mode) & 0o077:
            raise TokenError(f"token file {p} is mode {_mode(st)}; it must be 0600: chmod 600 {p}, "
                             f"then run `{ROTATE}` (a token that was readable may be known)")
        raw = os.read(fd, MAX_BYTES + 1)
    finally:
        os.close(fd)
    text = raw.decode("ascii", "replace")
    if text.endswith("\n"):
        text = text[:-1]
    if len(raw) > MAX_BYTES or not TOKEN.fullmatch(text):
        raise TokenError(f"token file {p} does not hold a talk token (43 or more of A-Z a-z 0-9 _ -); "
                         f"run `{ROTATE}`")
    return text


def _write_temp(d: Path, token: str) -> Path:
    """Write ``token`` to a fresh 0600 temporary file in ``d`` and fsync it; return its path."""
    tmp = d / f".{FILE_NAME}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)                 # exactly 0600, whatever the umask
        data = (token + "\n").encode("ascii")
        while data:
            data = data[os.write(fd, data):]
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        _unlink(tmp)
        raise
    os.close(fd)
    return tmp


def _unlink(p: Path) -> None:
    try:
        os.unlink(str(p))
    except FileNotFoundError:
        pass


def _sync_dir(d: Path) -> None:
    fd = os.open(str(d), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    except OSError:                          # not every filesystem syncs a directory; the data is synced
        pass
    finally:
        os.close(fd)


def load() -> str:
    """The current token. Never creates it: FileNotFoundError if it is missing,
    TokenError if the file or its directory is refused."""
    p = path()
    _check_dir(p.parent)
    return _read(p)


def load_or_create() -> str:
    """The token, made on first use. talk serve calls this at start, before anything binds."""
    p = path()
    _ensure_dir(p.parent)
    try:
        return _read(p)
    except FileNotFoundError:
        pass
    tmp = _write_temp(p.parent, _new())
    try:
        os.link(str(tmp), str(p))            # fails if another creator won: then read theirs
    except FileExistsError:
        pass
    finally:
        _unlink(tmp)
    _sync_dir(p.parent)
    return _read(p)


def rotate() -> Path:
    """Replace the token with a fresh one, atomically, and return the file's path (never the
    token). A running server sees the new one at its next check; every paired browser must pair
    again. Also the remedy for a missing, wide or unreadable token file."""
    p = path()
    _ensure_dir(p.parent)
    tmp = _write_temp(p.parent, _new())
    try:
        os.replace(str(tmp), str(p))
    except BaseException:
        _unlink(tmp)
        raise
    _sync_dir(p.parent)
    return p


# -- the run-time check: stat on every call, read again only on a change

_lock = threading.Lock()
_cache = {"key": None, "token": None}


def _key(p: Path):
    """What identifies this version of the token: the file's and the directory's lstat.
    A chmod or chown changes ctime, so a mode widened at run time is read (and refused) again."""
    def one(x):
        st = os.lstat(str(x))
        return (st.st_dev, st.st_ino, st.st_mode, st.st_uid, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
    return (str(p), one(p.parent), one(p))


def current():
    """The token as it stands now, or None if it is missing or refused (fail closed).
    Read from the file only when it, or its directory, has changed since the last call."""
    p = path()
    try:
        key = _key(p)
    except OSError:
        with _lock:
            _cache["key"], _cache["token"] = None, None
        return None
    with _lock:
        if _cache["key"] == key:
            return _cache["token"]
        try:
            token = load()
        except (OSError, TokenError):
            token = None
        _cache["key"], _cache["token"] = key, token
        return token


def matches(value) -> bool:
    """True if ``value`` is the current token (compared in constant time). A missing or refused
    token file matches nothing."""
    if not isinstance(value, str) or not value:
        return False
    token = current()
    if token is None:
        return False
    return hmac.compare_digest(value.encode("utf-8"), token.encode("ascii"))
