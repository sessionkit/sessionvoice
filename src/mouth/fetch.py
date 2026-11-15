"""fetch - turn ``mouth play``'s SOURCE into a local file: a path, ``file://``, or http(s).

``play()`` asks this module before anything is queued, so the decoder, the
instrumental probe and the splitter only ever see a local file (each still runs
ffmpeg with ``-protocol_whitelist file``).

- :func:`classify` says what SOURCE is: ``("path", source)``, ``("file", path)``
  for a ``file://`` URL (decoded; never fetched or copied), or ``("remote", url)``
  for http(s). Any other scheme, or a ``file://`` URL naming another host, is
  refused with a ValueError.
- :func:`cached` fetches a remote URL into the play cache, or finds it there. The
  cache keeps one folder per URL, so the played file keeps a readable name::

      <cache dir>/
        .tmp/                partial downloads, on the same filesystem
        <key>/               sha256 of the URL (no fragment), 24 hex characters
          .entry.json        url, final_url, fetched_t, size, content_type, name
          <name>             the played file, and nothing else

  Its sidecar is not beside it: ``play()`` registers every URL source, and its
  stems go in ``<mouth dir>/url-sidecars/<sha256 of the bytes>/``
  (:mod:`mouth.sourcekey`, ``design.md`` section 11). A hit makes no request and
  never touches the file: the registry memoises the hash by size + mtime + inode,
  so a touch would only cost a re-hash.
- :func:`spooled` fetches with the cache off, into a spool folder of its own, and
  :func:`sweep_spool` clears spool folders a day old that no queued, playing or
  bedded line names, and forgets their files' registry entries (their
  ``url-sidecars/`` folders are kept). The player never deletes a fetched file: a
  cut bed can resume.

Settings arrive as plain arguments (the cache dir, the age); the mouth's settings
layers are read by the caller. A failed fetch is a ValueError naming the URL, with
its credentials dropped (:func:`shown`), and leaves no partial file.

The user docs: ``docs/mouth.md`` ("Play a URL") and ``docs/sidecar.md``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import sys
import time
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import unquote, urlsplit

#: A scheme at the START of SOURCE. A ``://`` later on is part of a file name.
_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*)://")
REMOTE_SCHEMES = ("http", "https")
#: A URL's scheme and its authority (up to the first ``/``, ``?`` or ``#``): ``shown``.
_AUTHORITY = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*://)([^/?#]*)")
#: Redirects ``fetch`` follows itself, checking each hop's scheme before it is sent.
MAX_REDIRECTS = 10
#: Never more than this is downloaded: an internet radio URL never ends.
MAX_FETCH_BYTES = 2 * 1000 ** 3
#: The same numbers as the separator's download (``separator.py``).
CONNECT_S, READ_S = 15.0, 60.0
#: A spool folder this old, that no live line names, is swept.
SPOOL_KEEP_S = 24 * 3600.0
#: Where the cache and the spool live by default, under the mouth dir.
CACHE_DIR_NAME, SPOOL_DIR_NAME = "play-cache", "play-spool"
ENTRY = ".entry.json"
TMP = ".tmp"
KEY_LEN = 24
#: A cached name's length in UTF-8 BYTES (a file system counts bytes, not characters).
NAME_MAX = 120
#: A name for a file whose URL gives none.
FALLBACK_NAME = "audio"
#: Content types that are never a sound file: a web page, an API's answer.
_TEXT_TYPES = ("application/json", "application/xml")
#: An extension for a URL that names none, from its Content-Type.
EXT_BY_TYPE = {
    "audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/mp4": ".m4a", "audio/x-m4a": ".m4a",
    "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/wave": ".wav", "audio/flac": ".flac",
    "audio/x-flac": ".flac", "audio/ogg": ".ogg", "audio/aac": ".aac", "audio/webm": ".webm",
    "audio/opus": ".opus",
}

_SUPPORTED = "mouth play takes a path, file://, http:// or https://"


# -- what SOURCE is -----------------------------------------------------------------------

def classify(source: str) -> tuple[str, str]:
    """``("path", source)``, ``("file", path)`` or ``("remote", url)``; ValueError for a
    scheme the mouth does not play, or a ``file://`` URL naming another host.

    ``file://``'s path is percent-decoded (``My%20Song.m4a`` names ``My Song.m4a``);
    its query and fragment are ignored."""
    m = _SCHEME.match(source)
    if not m:
        return "path", source
    scheme = m.group(1).lower()
    if scheme == "file":
        parts = urlsplit(source)
        host = parts.hostname or ""
        if parts.netloc and host.lower() not in ("", "localhost"):
            raise ValueError(f"mouth: a file:// URL names this host only "
                             f"(file:///path), not {parts.netloc!r}")
        return "file", unquote(parts.path)
    if scheme in REMOTE_SCHEMES:
        try:
            host = urlsplit(source).hostname
        except ValueError:   # e.g. an unclosed IPv6 bracket
            host = None
        if not host:
            raise ValueError(f"mouth: not a usable {scheme}:// URL (no host): {shown(source)}")
        return "remote", source
    raise ValueError(f"mouth: a {scheme}:// URL is not played: {_SUPPORTED}")


def shown(url: str) -> str:
    """URL as it may be shown (stderr, an error, a said record): no ``user:pass@``.
    The query string stays: it can name the file.

    Read from the text, not ``urlsplit``: a URL it cannot parse (an unclosed IPv6
    bracket) is shown in a refusal too, and must not show its password there."""
    m = _AUTHORITY.match(url)
    if not m or "@" not in m.group(2):
        return url
    return m.group(1) + m.group(2).rsplit("@", 1)[1] + url[m.end():]


def key(url: str) -> str:
    """The cache folder's name: sha256 of URL without its fragment (never sent), 24 hex."""
    bare = url.split("#", 1)[0]
    return hashlib.sha256(bare.encode("utf-8", "surrogatepass")).hexdigest()[:KEY_LEN]


def cache_dir(d: Path) -> Path:
    """The built-in cache dir: ``<mouth dir>/play-cache``."""
    return Path(d) / CACHE_DIR_NAME


def spool_dir(d: Path) -> Path:
    """Where a fetch with the cache off goes: ``<mouth dir>/play-spool``."""
    return Path(d) / SPOOL_DIR_NAME


# -- the file's name ----------------------------------------------------------------------

def _segment(url: str) -> str:
    """URL's last path segment, percent-decoded and cleaned; "" when none is usable."""
    try:
        path = urlsplit(url).path
    except ValueError:
        return ""
    name = unquote(path.rsplit("/", 1)[-1])
    # Dots and blanks together: " .mp3" must not end as a hidden ".mp3", nor " .." as "..".
    name = re.sub(r"^[.\s]+", "", re.sub(r"[/\\\x00]", "_", name)).rstrip()
    if not name:
        return ""
    stem, ext = os.path.splitext(name)
    return _fit(stem, ext)


def _utf8(s: str) -> int:
    return len(s.encode("utf-8", "surrogatepass"))


def _fit(stem: str, ext: str) -> str:
    """``stem + ext`` cut to ``NAME_MAX`` UTF-8 bytes, keeping the extension and
    cutting the stem on a character boundary (never half of a multi-byte one). An
    extension as long as half the limit is no extension: the whole name is cut."""
    if _utf8(stem + ext) <= NAME_MAX:
        return stem + ext
    if _utf8(ext) >= NAME_MAX // 2:
        stem, ext = stem + ext, ""
    room = NAME_MAX - _utf8(ext)
    cut = stem.encode("utf-8", "surrogatepass")[:room].decode("utf-8", "ignore")
    return cut + ext


def name_for(url: str, final_url: str, content_type: str) -> str:
    """The played file's name: URL's last segment when it has an extension; else the
    redirect target's; else URL's (or ``audio``) with the Content-Type's extension;
    else URL's segment, or ``audio``, as it is."""
    given = _segment(url)
    if os.path.splitext(given)[1]:
        return given
    target = _segment(final_url) if final_url != url else ""
    if os.path.splitext(target)[1]:
        return target
    base = given or FALLBACK_NAME
    ext = EXT_BY_TYPE.get(content_type, "")
    if ext:
        return _fit(base, ext)
    return base


# -- the fetch ----------------------------------------------------------------------------

def client():
    """The one httpx client factory. Tests replace it with a MockTransport client."""
    import httpx

    return httpx.Client(follow_redirects=False,
                        timeout=httpx.Timeout(READ_S, connect=CONNECT_S))


def _fail(url: str, why: str) -> ValueError:
    return ValueError(f"mouth: could not fetch {shown(url)}: {why}")


def _scrub(text: str, url: str) -> str:
    """An error text with URL's credentials dropped, wherever httpx put the URL."""
    return text.replace(url, shown(url)) if shown(url) != url else text


def _media_type(headers) -> str:
    return (headers.get("content-type") or "").split(";", 1)[0].strip().lower()


def _download(url: str, tmp_root: Path, *, http=None, tell: Optional[Callable] = None,
              max_bytes: int = MAX_FETCH_BYTES) -> tuple[Path, dict]:
    """Fetch URL into a fresh part file under TMP_ROOT: ``(part, meta)``. Redirects are
    followed here, at most ``MAX_REDIRECTS``, and a hop that is not http(s) is refused
    BEFORE it is sent. Any failure raises the plain ValueError and leaves no part file."""
    import httpx

    own = http is None
    http = client() if own else http
    tmp_root.mkdir(parents=True, exist_ok=True)
    part = tmp_root / f"{key(url)}-{os.getpid()}-{secrets.token_hex(4)}.part"
    done = False
    if tell:
        tell(f"mouth: fetching {shown(url)}")
    try:
        req = http.build_request("GET", url)
        if req.url.scheme not in REMOTE_SCHEMES or not req.url.host:
            raise _fail(url, "not an http:// or https:// URL with a host")
        for _ in range(MAX_REDIRECTS + 1):
            r = http.send(req, stream=True, follow_redirects=False)   # every hop is ours to check
            try:
                if r.is_redirect:
                    nxt = r.next_request
                    if nxt is None:
                        raise _fail(url, f"HTTP {r.status_code} with no Location")
                    if nxt.url.scheme not in REMOTE_SCHEMES:
                        raise _fail(url, f"redirected to a {nxt.url.scheme}:// URL")
                    req = nxt
                    continue
                if not 200 <= r.status_code < 300:
                    raise _fail(url, f"HTTP {r.status_code}")
                ctype = _media_type(r.headers)
                if ctype.startswith("text/") or ctype in _TEXT_TYPES:
                    raise _fail(url, f"not a sound file ({ctype})")
                limit = f"larger than {max_bytes / 1000 ** 3:g} GB"
                try:
                    length = int(r.headers.get("content-length", ""))
                except ValueError:
                    length = None
                if length is not None and length > max_bytes:
                    raise _fail(url, limit)
                size = 0
                fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
                with os.fdopen(fd, "wb") as f:
                    for chunk in r.iter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            raise _fail(url, limit)
                        f.write(chunk)
                meta = {"url": url, "final_url": str(r.url), "fetched_t": time.time(),
                        "size": size, "content_type": ctype}
                done = True
                return part, meta
            finally:
                r.close()
        raise _fail(url, "too many redirects")
    except httpx.InvalidURL as e:   # not an httpx.HTTPError: it would reach the CLI raw
        raise _fail(url, f"not a valid URL ({_scrub(str(e), url)})") from None
    except httpx.TimeoutException:
        raise _fail(url, "timed out") from None
    except httpx.ConnectError as e:
        raise _fail(url, f"connection failed ({_scrub(str(e), url) or type(e).__name__})") from None
    except httpx.HTTPError as e:
        raise _fail(url, f"{type(e).__name__} ({_scrub(str(e), url)})") from None
    finally:
        if not done:
            part.unlink(missing_ok=True)
        if own:
            http.close()


def _write_json(path: Path, data: dict, tmp_root: Path) -> None:
    """DATA to PATH atomically: a tmp file under TMP_ROOT, then ``os.replace``."""
    tmp_root.mkdir(parents=True, exist_ok=True)
    tmp = tmp_root / f"entry-{os.getpid()}-{secrets.token_hex(4)}.json"
    try:
        tmp.write_text(json.dumps(data))
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _hit(folder: Path, max_age_s: Optional[float], now: float) -> Optional[Path]:
    """The cached file in FOLDER when its entry is whole, its size matches, and it is
    younger than MAX_AGE_S (None: forever); else None. Reads only: never touches it."""
    try:
        entry = json.loads((folder / ENTRY).read_text())
        name, size, fetched = entry["name"], entry["size"], float(entry["fetched_t"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(name, str) or not name or "/" in name or name.startswith("."):
        return None
    f = folder / name
    try:
        if not f.is_file() or f.stat().st_size != size:
            return None
    except OSError:
        return None
    if max_age_s is not None and now - fetched >= max_age_s:
        return None
    return f


def cached(url: str, cache_root: Path, *, max_age_s: Optional[float] = None, http=None,
           tell: Optional[Callable] = None, max_bytes: int = MAX_FETCH_BYTES,
           now: Optional[float] = None) -> Path:
    """URL's file in the play cache at CACHE_ROOT: a hit as it is, else fetched now.

    ``max_age_s``: an entry this old or older is a miss and is fetched again (None:
    kept forever). ``tell``: called with one line on a miss (the CLI's stderr).
    ``http``: an httpx client (else :func:`client`)."""
    cache_root = Path(cache_root)
    folder = cache_root / key(url)
    hit = _hit(folder, max_age_s, time.time() if now is None else now)
    if hit is not None:
        return hit
    tmp_root = cache_root / TMP
    part, meta = _download(url, tmp_root, http=http, tell=tell, max_bytes=max_bytes)
    try:
        name = name_for(url, meta["final_url"], meta["content_type"])
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / name
        os.replace(part, dest)
    finally:
        part.unlink(missing_ok=True)
    _write_json(folder / ENTRY, {**meta, "name": name}, tmp_root)   # LAST: a crash is a miss
    for p in folder.iterdir():     # an older fetch under another name: one file per URL
        if p.name != name and not p.name.startswith(".") and p.is_file() and not p.is_symlink():
            p.unlink(missing_ok=True)
    return dest


def spooled(url: str, spool_root: Path, *, http=None, tell: Optional[Callable] = None,
            max_bytes: int = MAX_FETCH_BYTES) -> Path:
    """URL fetched with the cache off: into a folder of its own under SPOOL_ROOT, never
    into the cache dir. Every call fetches. :func:`sweep_spool` clears it later."""
    spool_root = Path(spool_root)
    part, meta = _download(url, spool_root / TMP, http=http, tell=tell, max_bytes=max_bytes)
    try:
        folder = spool_root / f"{key(url)}-{secrets.token_hex(4)}"
        folder.mkdir(parents=True)
        dest = folder / name_for(url, meta["final_url"], meta["content_type"])
        os.replace(part, dest)
    finally:
        part.unlink(missing_ok=True)
    return dest


# -- the spool's sweep --------------------------------------------------------------------

def live_files(d: Path) -> set:
    """Every file a live line names, as real paths: each queued line's ``file``, the
    line in ``playing.json``, and each live bed (``bed.json``, beside it, never in it)."""
    from . import bed, box

    names = []
    try:
        names += [x.get("file") for x in box.Queue(d).lines()]
    except Exception:  # a queue that cannot be read: sweep nothing it might name
        return {None}
    try:
        playing = json.loads((Path(d) / "playing.json").read_text())
        names.append(playing.get("file") if isinstance(playing, dict) else None)
    except FileNotFoundError:
        pass
    except ValueError:
        return {None}
    names += [b.get("file") for b in bed.read(d) if isinstance(b, dict)]
    return {os.path.realpath(n) for n in names if isinstance(n, str) and n}


def sweep_spool(d: Path, *, keep_s: float = SPOOL_KEEP_S, now: Optional[float] = None,
                spool_root: Optional[Path] = None) -> list:
    """Remove each spool folder (and part file) older than KEEP_S that no live line
    names (:func:`live_files`), and forget each removed file's registry entry
    (:func:`mouth.sourcekey.forget`; its ``url-sidecars/`` folder is kept). Returns
    what it removed. Never raises."""
    from . import sourcekey

    root = Path(spool_root) if spool_root is not None else spool_dir(d)
    try:
        entries = list(root.iterdir())
    except OSError:
        return []
    now = time.time() if now is None else now
    old = []
    for p in entries:
        try:
            if now - p.stat().st_mtime >= keep_s:
                old.append(p)
        except OSError:
            continue
    tmp = root / TMP
    try:
        old += [p for p in tmp.iterdir() if now - p.stat().st_mtime >= keep_s]
    except OSError:
        pass
    old = [p for p in old if p != tmp]
    if not old:
        return []
    live = live_files(d)
    if None in live:
        return []
    gone = []
    for p in old:
        real = os.path.realpath(p)
        if any(f == real or f.startswith(real + os.sep) for f in live):
            continue
        try:
            if p.is_dir() and not p.is_symlink():
                files = [os.path.realpath(f) for f in p.rglob("*") if f.is_file()]
                shutil.rmtree(p)
            else:
                files = [real]
                p.unlink()
            gone.append(p)
        except OSError as e:
            print(json.dumps({"spool_sweep": str(p), "error": str(e)}), file=sys.stderr)
            continue
        for f in files:
            sourcekey.forget(f, d)
    return gone
