"""Which key goes to which URL, and where it is read from. Stdlib only: services.py
(the installer) imports it.

ONE rule for the ears and the mouth: ``$OPENAI_API_KEY`` goes to
OpenAI's own host and nowhere else. ONE order for both:
the component's own variable, else its own read-only key file, else that OpenAI
rule (``resolve_key``). The component passes its variable, its file and the word
its warning starts with, so there is one copy of the reader, the checks and the
host test, and no helper that asks "which component am I". The ears' is
``stt_key`` (here); the mouth's is ``mouth.backends.tts_key``, which delegates.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from . import envnames
from .home import settings_dir

OPENAI_HOST = "api.openai.com"


def is_openai(url: str) -> bool:
    """True only for ``https://api.openai.com[:port]/...``.

    A substring test is not enough: ``https://api.openai.com.evil.example/v1``,
    ``http://evil.example/v1?x=api.openai.com``, ``https://evil.example/api.openai.com/v1``
    and ``https://api.openai.com@evil.example/v1`` all contain the name, and the
    old test, a substring match on it, handed each of them the key.
    ``urlparse().hostname`` is lower-cased and drops the port and any userinfo.
    Plain ``http`` is refused: OpenAI's API is HTTPS-only, and a key never goes
    out in clear text.
    """
    if not isinstance(url, str):
        return False
    try:
        u = urlparse(url)
        return u.scheme == "https" and u.hostname == OPENAI_HOST
    except ValueError:   # a malformed IPv6 literal (https://[::1/v1) or port: not OpenAI's
        return False


def openai_key(url: str) -> Optional[str]:
    """``$OPENAI_API_KEY``, but only for OpenAI's own host (``is_openai``); else None.

    A blank or whitespace-only value is no key: never a ``Bearer`` with nothing
    after it."""
    if not is_openai(url):
        return None
    return (os.environ.get("OPENAI_API_KEY") or "").strip() or None


# -- the key file and the order ---------------

#: Bigger than any key: a file this size is not one, and is not read whole.
KEY_FILE_MAX = 64 * 1024
#: The refusals already printed, so a refused file warns once, not once a line.
_KEY_FILE_WARNED: set = set()


def key_file(path: Path, who: str) -> Optional[str]:
    """The key in ``path``, else None. Read only; nothing writes it.

    The user makes it (``install -m 600 /dev/null <path>``, then edits it);
    neither the ears, the mouth nor the installer creates it. Missing is no
    key, without a word. A file that is not the user's own
    (``st_uid != os.getuid()``), that has any group or other mode bit set, or
    that is not a regular file, is refused: no key, and ONE WARNING on stderr,
    starting ``who:``, that names the path and why, never the key. The checks
    are made on the open file (``fstat``), so a file swapped between the check
    and the read is not read. Outer whitespace is stripped; an empty file is
    no key.
    """
    import stat

    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return None
    except OSError as e:
        return _key_file_refused(path, f"cannot be opened ({e.strerror or type(e).__name__})", who=who)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return _key_file_refused(path, "is not a regular file", st, who=who)
        if st.st_uid != os.getuid():
            return _key_file_refused(
                path, f"is owned by uid {st.st_uid}, not by you (uid {os.getuid()})", st, who=who)
        if st.st_mode & 0o077:
            return _key_file_refused(
                path, f"has mode {stat.S_IMODE(st.st_mode):o}, so others may read it (chmod 600 it)",
                st, who=who)
        if st.st_size > KEY_FILE_MAX:
            return _key_file_refused(path, f"is {st.st_size} bytes, too big to be a key", st, who=who)
        raw = os.read(fd, KEY_FILE_MAX + 1)
    except OSError as e:
        return _key_file_refused(path, f"cannot be read ({e.strerror or type(e).__name__})", who=who)
    finally:
        os.close(fd)
    try:
        return raw.decode("utf-8").strip() or None
    except UnicodeDecodeError:
        return _key_file_refused(path, "is not UTF-8 text", who=who)


def _key_file_refused(path: Path, why: str, st: Optional[os.stat_result] = None, *, who: str) -> None:
    """Print ONE refusal for this state of the file: the path and why, never its contents."""
    import sys

    sig = (str(path), why, st and (st.st_mode, st.st_uid, st.st_mtime_ns, st.st_size))
    if sig not in _KEY_FILE_WARNED:
        _KEY_FILE_WARNED.add(sig)
        print(f"{who}: WARNING: the key file {path} {why}; it is not used, "
              "and no key from it is sent", file=sys.stderr, flush=True)
    return None


def checked(key: str, where: str) -> str:
    """``key``, if every character is visible ASCII (what a Bearer token holds).

    Else a ValueError naming ``where`` the key came from (a variable or a path),
    never the key: an HTTP library refuses such a header and quotes it whole
    (httpx ``Illegal header value b'Bearer <the key>'``, http.client ``Invalid
    header value ...``), and that text would become the caller's error.
    """
    if not all("!" <= c <= "~" for c in key):
        raise ValueError(f"{where} holds a space or a character "
                         "an HTTP header cannot carry; the key is not sent")
    return key


def resolve_key(env: str, path: Path, who: str, url: str) -> Optional[str]:
    """The key for ``url``, else None: the first of these that holds one.

    1. ``$env`` (or its old name, ``envnames``), to whatever URL is configured;
    2. the key file ``path`` (``key_file``), to the same URL: an installed
       service is a launch agent whose environment never holds the key;
       with ``$env`` set it is never opened;
    3. ``$OPENAI_API_KEY``, but only for ``https://api.openai.com`` (``openai_key``).

    Outer whitespace is stripped; blank is no key. A key that ``checked``
    refuses raises its ValueError, naming ``$env``, the path or
    ``$OPENAI_API_KEY``, never the key.
    """
    key = (envnames.get(env) or "").strip()
    where = f"${envnames.source(env) or env}"
    if not key:
        key, where = key_file(path, who) or "", str(path)
    if not key:
        key, where = openai_key(url) or "", "$OPENAI_API_KEY"
    return checked(key, where) if key else None


# -- the ears' key --------------------------------------------------------

STT_KEY_ENV = "EARS_LISTEN_STT_KEY"
#: The ears' key file, in the voice settings folder (beside ears.json).
STT_KEY_FILE = "stt.key"


def stt_key_path() -> Path:
    """``~/.sessionkit/voice/settings/stt.key`` (``ears.home.settings_dir``): a fixed path.

    Fixed on purpose: no variable moves it (``$EARS_CONF`` moves ears.json,
    not this), so there is one file to protect. ``~`` is ``$HOME``, read on
    every call."""
    return settings_dir() / STT_KEY_FILE


def stt_key(url: str) -> Optional[str]:
    """The key for the STT endpoint at ``url``, else None (``resolve_key``):
    ``$EARS_LISTEN_STT_KEY``, else ``~/.sessionkit/voice/settings/stt.key``, else
    ``$OPENAI_API_KEY`` for api.openai.com only. ValueError, by source name,
    for a key a header cannot carry. Used by the capture (``WhisperSTT``) and
    the installer's probe (``services.probe_stt``)."""
    return resolve_key(STT_KEY_ENV, stt_key_path(), "ears", url)
