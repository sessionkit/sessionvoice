"""Backends: text in, a stream of mono float32 audio blocks out.

A backend is anything with ``name``, ``sample_rate`` and
``stream(text, voice, speed) -> Iterator[np.ndarray]``. The first block
coming back is what ``gen_s`` measures. Three ship:

- ``kokoro``: an OpenAI-compatible ``/audio/speech`` server with native
  voice names (``af_sky``); ``MOUTH_KOKORO_URL``, else mouth.json's
  ``tts`` (``speech_endpoint``), default mlx-audio on 8890 with Kokoro-82M.
- ``clone``: the same endpoint with ``ref_audio``/``ref_text`` for the
  voices under ``~/.voicemode/voices`` (``alice``, ``bob``), resolved by
  ``resolver.resolve_voice``, the mouth's copy of VoiceMode's resolver.
- ``apple``: macOS's own speech (``/usr/bin/say``), on-device, with nothing
  to install. The voice is ``$MOUTH_APPLE_VOICE``, else the asked
  voice if it names an installed Apple voice, else the system voice.
- ``silence``: no server, no sound; paced blocks of zeros, one second per
  15 characters. For tests and dry runs.

``auto`` picks ``clone`` when the voice resolves to a clone, else ``kokoro``.
On a Mac with no speech server answering (a bare install), ``auto`` picks
``apple`` instead, so the mouth speaks with nothing installed.

``FileSound`` is not chosen by name: a queued item with ``file`` plays that
file, or ``start``..``end`` seconds of it, decoded by ffmpeg (files only).
"""

from __future__ import annotations

import os
from typing import Iterator, Optional, Protocol

import numpy as np

from ears import envnames

SAMPLE_RATE = 24000
BLOCK_S = 0.05  # 50 ms: how often playback looks for a stop


class Backend(Protocol):
    name: str
    sample_rate: int

    def stream(self, text: str, voice: str, speed: Optional[float]) -> Iterator[np.ndarray]: ...


class OpenAISpeech:
    """POST {base_url}/audio/speech, response_format pcm (s16le mono), streamed."""

    def __init__(self, name: str, base_url: str, model: str, *,
                 extra_body: Optional[dict] = None, sample_rate: int = SAMPLE_RATE,
                 api_key: Optional[str] = None, timeout: float = 60.0) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.extra_body = extra_body or {}
        self.sample_rate = sample_rate
        self.api_key = api_key
        self.timeout = timeout

    def stream(self, text: str, voice: str, speed: Optional[float]) -> Iterator[np.ndarray]:
        import httpx

        body = {"model": self.model, "input": text, "voice": voice, "response_format": "pcm",
                **self.extra_body}
        if speed is not None:
            body["speed"] = speed
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        carry = b""
        with httpx.stream("POST", f"{self.base_url}/audio/speech", json=body,
                          headers=headers, timeout=self.timeout) as r:
            if r.status_code != 200:
                r.read()
                raise RuntimeError(refused(self.name, r.status_code, r.text, self.api_key))
            for chunk in r.iter_bytes():
                data = carry + chunk
                cut = len(data) - (len(data) % 2)  # s16: never split a sample
                carry = data[cut:]
                if cut:
                    yield np.frombuffer(data[:cut], dtype="<i2").astype(np.float32) / 32768.0


SAY = "/usr/bin/say"


def apple_available() -> bool:
    return os.path.exists(SAY) and os.access(SAY, os.X_OK)


def apple_voices() -> dict:
    """{lower-cased name: name} for the installed Apple voices; {} if say can't list them."""
    import subprocess

    try:
        out = subprocess.run([SAY, "-v", "?"], capture_output=True, text=True, timeout=10,
                             check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}
    names = {}
    for line in out.splitlines():
        # "Samantha            en_US    # Hello! My name is Samantha." - a name may hold spaces
        head = line.split("#", 1)[0].rstrip()
        parts = head.rsplit(None, 1)
        if len(parts) == 2:
            names[parts[0].strip().lower()] = parts[0].strip()
    return names


def server_answers(base_url: str, timeout: float = 0.3) -> bool:
    """Does anything accept a TCP connection at base_url's host and port? Cheap; no request."""
    import socket
    from urllib.parse import urlparse

    u = urlparse(base_url)
    port = u.port or (443 if u.scheme == "https" else 80)
    try:
        with socket.create_connection((u.hostname or "127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


class AppleSay:
    """macOS speech: ``say`` renders the line to a 24 kHz WAV, then it plays in blocks.

    The text goes in on stdin, never argv, so a line that starts with ``-``
    is spoken, not parsed as an option. A sentence renders in about 0.6 s
    (one Mac, 2030-06-09); say cannot stream, so gen_s is the whole render.
    """

    name = "apple"
    sample_rate = SAMPLE_RATE

    def __init__(self, timeout: float = 60.0) -> None:
        self.timeout = timeout

    def _voice(self, voice: Optional[str]) -> Optional[str]:
        forced = envnames.get("MOUTH_APPLE_VOICE")
        if forced:
            return forced
        return apple_voices().get((voice or "").strip().lower()) if voice else None

    def stream(self, text: str, voice: str, speed: Optional[float]) -> Iterator[np.ndarray]:
        import subprocess
        import tempfile
        import wave

        with tempfile.TemporaryDirectory(prefix="mouth-say-") as d:
            out = os.path.join(d, "line.wav")
            cmd = [SAY, "-f", "-", "-o", out, f"--data-format=LEI16@{self.sample_rate}"]
            v = self._voice(voice)
            if v:
                cmd += ["-v", v]
            if speed:
                cmd += ["-r", str(int(round(175 * speed)))]  # say's words per minute; 175 is its normal
            r = subprocess.run(cmd, input=text, capture_output=True, text=True,
                               timeout=self.timeout, check=False)
            if r.returncode != 0:
                raise RuntimeError(f"apple: say exited {r.returncode}: {(r.stderr or '').strip()[:200]}")
            with wave.open(out, "rb") as w:
                if w.getnchannels() != 1 or w.getsampwidth() != 2:
                    raise RuntimeError(f"apple: say wrote {w.getnchannels()} ch, {8 * w.getsampwidth()} bit")
                data = w.readframes(w.getnframes())
        pcm = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
        step = int(self.sample_rate * BLOCK_S)
        for i in range(0, len(pcm), step):
            yield pcm[i:i + step]


class Silence:
    """A backend with no server and no sound: zeros, 15 characters a second."""

    name = "silence"
    sample_rate = SAMPLE_RATE

    def __init__(self, chars_per_s: float = 15.0, gen_s: float = 0.0) -> None:
        self.chars_per_s = chars_per_s
        self.gen_s = gen_s

    def stream(self, text: str, voice: str, speed: Optional[float]) -> Iterator[np.ndarray]:
        import time

        if self.gen_s:
            time.sleep(self.gen_s)
        total = int(max(len(text), 1) / self.chars_per_s / (speed or 1.0) * self.sample_rate)
        step = int(self.sample_rate * BLOCK_S)
        for i in range(0, total, step):
            yield np.zeros(min(step, total - i), dtype=np.float32)


def resolve(backend: str, voice: str) -> tuple[Backend, str]:
    """(backend, the voice name to send it). Raises ValueError, loudly, on a bad pair."""
    if backend == "silence":
        return Silence(), voice
    if backend == "apple":
        if not apple_available():
            raise ValueError(f"the apple backend needs macOS's {SAY}")
        return AppleSay(), voice
    kokoro_url, kokoro_model = speech_endpoint()
    if backend == "auto" and apple_available() and not server_answers(kokoro_url):
        # A bare Mac: no speech server (kokoro and clones both ride it; `ears services
        # install` finds or installs one). Speak with the Mac's own voice, not fail.
        return AppleSay(), voice
    if backend in ("auto", "clone"):
        from . import resolver as _resolver  # the clone dirs, read directly

        try:
            res = _resolver.resolve_voice(voice)
        except _resolver.VoiceResolutionError as e:
            # Only a voice the mouth's own Kokoro speaks may fall through to
            # it (bf_isabella: the resolver does not know it). Anything
            # else is refused, loudly, with the resolver's "did you mean":
            # a silent Kokoro fallback is an error: once a typo or a short-form
            # reference (clone-a/02-line.wav for demo/clone-a/02-line.wav) went
            # to Kokoro without a word.
            from .voices import KOKORO
            # A remote tts takes its own names; its 400 is loud (is_remote).
            if backend == "clone" or (voice not in KOKORO and not is_remote(kokoro_url)):
                raise ValueError(str(e)) from e
            res = None
        if res is not None and res.profile is not None:
            p = res.profile
            return OpenAISpeech("clone", p.base_url, p.model, extra_body={
                "ref_audio": p.ref_audio, "ref_text": p.ref_text, "stream": True,
                "streaming_interval": 0.3, "lang_code": "auto"}), voice
        if backend == "clone":
            raise ValueError(f"voice {voice!r} is not a clone voice")
        if res is not None:
            voice = res.resolved
    if backend in ("auto", "kokoro"):
        return OpenAISpeech(  # the key goes to the tts endpoint only, never a clone's
            "kokoro",
            kokoro_url,
            kokoro_model,
            api_key=tts_key(kokoro_url)), voice
    raise ValueError(f"unknown backend {backend!r}: auto, kokoro, clone, apple or silence")


class FileSound:
    """A sound file, or a section of one, decoded by ffmpeg to the mouth's format.

    ffmpeg reads only files (``-protocol_whitelist file``, an input option, so it comes
    before ``-i``): whoever asked, a path is never fetched as a URL.

    It plays a sound file, or a section of one (``start``..``end``
    seconds).

    PATH may be a list of files, the stems of one song (``mouth play --stem``): ONE
    ffmpeg decodes them all, so the mix is sample-locked. ``-protocol_whitelist file``
    and ``-ss`` are per-input options, so each goes before EACH ``-i``. The inputs are
    summed by ``amix=inputs=N:duration=longest,volume=N``: plain ``amix`` averages
    them, and ``volume=N`` undoes that on every ffmpeg (``normalize=0`` needs 4.4 or
    later). A list of one plays as a plain file.
    """

    name = "file"
    sample_rate = SAMPLE_RATE

    def __init__(self, path, start: Optional[float] = None, end: Optional[float] = None) -> None:
        paths = [path] if isinstance(path, (str, os.PathLike)) else list(path)
        if not paths:
            raise ValueError("FileSound: no file to play")
        self.paths = [str(p) for p in paths]
        self.path, self.start, self.end = self.paths[0], start, end

    def command(self) -> list:
        """The one ffmpeg's argv."""
        cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error"]
        for p in self.paths:
            cmd += ["-protocol_whitelist", "file"]
            if self.start:
                cmd += ["-ss", f"{self.start:.3f}"]
            cmd += ["-i", p]
        n = len(self.paths)
        if n > 1:
            ins = "".join(f"[{i}:a]" for i in range(n))
            cmd += ["-filter_complex", f"{ins}amix=inputs={n}:duration=longest,volume={n}"]
        if self.end is not None:
            cmd += ["-t", f"{self.end - (self.start or 0.0):.3f}"]
        cmd += ["-f", "s16le", "-ac", "1", "-ar", str(self.sample_rate), "-"]
        return cmd

    def stream(self, text: str, voice: str, speed: Optional[float]) -> Iterator[np.ndarray]:
        import subprocess

        cmd = self.command()
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        got = 0
        try:
            carry = b""
            while True:
                chunk = proc.stdout.read(int(self.sample_rate * 0.1) * 2)
                if not chunk:
                    break
                data = carry + chunk
                cut = len(data) - (len(data) % 2)
                carry = data[cut:]
                if cut:
                    got += cut
                    yield np.frombuffer(data[:cut], dtype="<i2").astype(np.float32) / 32768.0
            if proc.wait() != 0 and not got:
                raise RuntimeError(f"ffmpeg: {proc.stderr.read().decode(errors='replace').strip()[:200]}")
        finally:
            if proc.poll() is None:  # a cut: stop decoding now
                proc.kill()
                proc.wait()


# kept at the end so the backends.py lines README.md and
# docs/troubleshooting.md cite stay where they are.
KOKORO_URL = "http://127.0.0.1:8890/v1"     # mlx-audio: `ears services install` finds or adds it
KOKORO_MODEL = "mlx-community/Kokoro-82M-bf16"


def speech_endpoint(settings: Optional[dict] = None) -> tuple[str, str]:
    """(url, model) the ``kokoro`` backend (and ``auto``) speaks through.

    Each field on its own, as the ears resolve theirs (``ears/listen/stt.py:100-102``):
    the env (the session layer) wins over ``tts`` in mouth.json (the host layer),
    which wins over the built-in mlx-audio on 8890 with Kokoro-82M.

    - url = ``$MOUTH_KOKORO_URL``, else ``tts.url``, else ``KOKORO_URL``;
    - model = ``$MOUTH_KOKORO_MODEL``, else ``tts.model``, else ``KOKORO_MODEL``.

    A blank or non-string value counts as unset, and so does a ``tts`` that is
    not an object. Unlike the ears, the model's default does not depend on the
    URL: a URL alone keeps asking for Kokoro, as ``$MOUTH_KOKORO_URL``
    alone always has. The file is read on every call (once a line), so a change
    applies to the next line. Nothing else in ``tts`` is read: never a key.
    """
    if settings is None:
        from .voices import settings as _settings

        settings = _settings()
    tts = settings.get("tts") if isinstance(settings, dict) else None
    tts = tts if isinstance(tts, dict) else {}

    def field(env: str, key: str, builtin: str) -> str:
        for v in (envnames.get(env), tts.get(key)):
            if isinstance(v, str) and v.strip():
                return v.strip()
        return builtin

    return (field("MOUTH_KOKORO_URL", "url", KOKORO_URL),
            field("MOUTH_KOKORO_MODEL", "model", KOKORO_MODEL))


# -- the key: also at the end, for the same reason ---------------

TTS_KEY_ENV = "MOUTH_TTS_KEY"


def tts_key(url: str) -> Optional[str]:
    """The key for the ``tts`` endpoint at ``url``, else None.

    The first of these that holds one:

    1. ``$MOUTH_TTS_KEY``, to whatever URL is configured (the mouth's
       mirror of the ears' ``$EARS_LISTEN_STT_KEY``);
    2. the key file, ``~/.sessionkit/voice/settings/tts.key`` (``tts_key_path``), to the
       same URL: the installed player is a launch agent whose environment never
       holds the key, so this is how it reaches it (``tts_key_file``);
    3. ``$OPENAI_API_KEY``, but only for ``https://api.openai.com``
       (``ears.keys``, the one host rule the ears use too).

    Outer whitespace is stripped, so a key pasted with a newline works; blank
    is no key. Read in the process that speaks (the player), once a line:
    never from mouth.json, never put on the queued line.

    A key holding a control character (a newline inside it) is refused with a
    ValueError naming where it came from, never the key: httpx would otherwise
    fail with ``Illegal header value b'Bearer <the key>'`` and that text would
    become the line's error, its heard-log row and its sender's notice.

    The order, the file's checks and the character check are ONE copy, shared
    with the ears' STT key: ``ears.keys.resolve_key``.
    """
    from ears.keys import resolve_key

    return resolve_key(TTS_KEY_ENV, tts_key_path(), "mouth", url)


#: The mouth's key file, in the voice settings folder (beside mouth.json).
TTS_KEY_FILE = "tts.key"


def tts_key_path() -> Path:
    """``~/.sessionkit/voice/settings/tts.key`` (``ears.home.settings_dir``): a fixed
    path, beside mouth.json.

    Fixed on purpose: no variable moves it, so there is one file to protect.
    ``~`` is ``$HOME``, read on every call."""
    from ears.home import settings_dir

    return settings_dir() / TTS_KEY_FILE


def tts_key_file() -> Optional[str]:
    """The key in ``tts_key_path()``, else None. Read only; nothing writes it.

    The user makes it (``install -m 600 /dev/null ~/.sessionkit/voice/settings/tts.key``,
    then edits it); neither the mouth nor the installer creates it. Missing is
    no key, without a word. Not the user's own, any group or other mode bit,
    or not a regular file, and it is refused: no key, and ONE ``mouth:``
    WARNING on stderr (the player's log) naming the path and why, never the
    key. The reader is ``ears.keys.key_file``, shared with the ears.
    """
    from ears.keys import key_file

    return key_file(tts_key_path(), "mouth")


def refused(name: str, status: int, body: str, key: Optional[str]) -> str:
    """The error text for a speech server's non-200 answer: never the key.

    That text becomes the ``said`` row's detail, so it reaches the heard log,
    the sender's drop notice and player.log. A server that echoes the request
    (a debug proxy, some gateways) would put the key in it. So on 401/403 the
    body is left out; on any other status the key, if there is one, is
    replaced by ``[key]`` BEFORE the body is cut to 200 characters (cut first,
    and a key across the cut would leave its head behind). The key is replaced
    in every spelling an echo may give it (``_key_spellings``), not only as typed.
    """
    if status in (401, 403):
        if key:
            return f"{name}: HTTP {status}: the server refused the key"
        hint = f" (${TTS_KEY_ENV})" if name == "kokoro" else ""
        return f"{name}: HTTP {status}: the server wants a key, and none was sent{hint}"
    text = body
    for form in _key_spellings(key):
        text = text.replace(form, "[key]")
    return f"{name}: HTTP {status}: {text[:200]}"


def _key_spellings(key: Optional[str]) -> list:
    """The key as typed and as an echoing server may spell it, longest first.

    A key holds ``/``, ``+``, ``=`` and the like (base64). A JSON echo escapes
    ``"`` and ``\\`` and may escape ``/`` as ``\\/`` (PHP's default); a query or
    form echo percent-encodes; an HTML page entity-escapes. Replacing only the
    typed form left each of those spellings whole in the
    error."""
    if not key:
        return []
    import html
    import json
    import re
    from urllib.parse import quote, quote_plus

    j = json.dumps(key)[1:-1]
    forms = {key, j, j.replace("/", "\\/"), html.escape(key), html.escape(key, quote=False)}
    for pct in (quote(key, safe=""), quote_plus(key)):
        # hex digits are case-blind (RFC 3986 2.1) and some encoders write %2f, not %2F
        forms |= {pct, re.sub(r"%[0-9A-F]{2}", lambda m: m.group().lower(), pct)}
    return sorted(forms, key=len, reverse=True)


def _speech_repr(self: OpenAISpeech) -> str:
    """Names the endpoint; the key only as set or none, so a ``{be!r}`` in a log stays safe."""
    return (f"OpenAISpeech(name={self.name!r}, base_url={self.base_url!r}, model={self.model!r}, "
            f"api_key={'set' if self.api_key else 'none'})")


OpenAISpeech.__repr__ = _speech_repr  # here, not in the class: the cited lines above stay put


# -- a hosted endpoint takes its own voice names: also at the end ------

def is_remote(url: str) -> bool:
    """True when ``url``'s host is not this machine: a hosted ``tts`` endpoint.

    Under ``auto``, a voice the resolver cannot resolve goes as it is to a
    remote ``tts`` endpoint (OpenAI's ``coral``, any provider's own names), and
    the provider answers a typo with a loud 400. A local server is refused as
    before: it is most likely another mlx-audio or Kokoro-FastAPI, which may
    answer an unknown voice with its own default, the silent fallback the
    refusal exists to stop. With nothing configured the URL is the loopback
    default, so every refusal stays as it is.

    Not remote: ``localhost``, an address in ``127.0.0.0/8``, ``::1`` (decided
    with ``ipaddress``, so ``127.0.0.2``, ``[::1]`` and ``[::ffff:127.0.0.1]``
    count too), and the unspecified ``0.0.0.0``/``::``, which a client reaches
    as this host. A URL with no host, or one that will not parse, is not remote
    either: when in doubt, the refusal stays.
    """
    import ipaddress
    from urllib.parse import urlparse

    try:
        host = urlparse(url).hostname
    except ValueError:  # a malformed IPv6 literal (http://[::1/v1)
        return False
    if not host:
        return False
    if host == "localhost":  # hostname is lower-cased
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True  # a name, not an address: a hosted endpoint
    mapped = getattr(ip, "ipv4_mapped", None)
    ip = mapped or ip
    return not (ip.is_loopback or ip.is_unspecified)
