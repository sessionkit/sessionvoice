"""Transcription for ``listen``, behind one small interface.

``STT.transcribe(audio) -> str`` takes 16 kHz mono int16 and returns text. An
engine may also have ``transcribe_final(audio)``: the call for a turn's FINAL
text (the re-decode), which is the only request the seat-name hint rides on.
An engine without it is asked ``transcribe`` for both.
Whisper (today's local server) is the only backend now; Apple on-device
speech slots in beside it. ``FollowingSTT`` wraps it
to follow ears.json's ``final`` live: a change of engine lands on the next
chunk, with no restart.

ears.json names two engines (Nemotron for the
partials, Parakeet for the final, say): ``final``, the URL engine that writes the kept
line (and the partials too, chunk by chunk, unless told otherwise), and ``partials``:
``"final"`` (that same engine per chunk, the default) or ``"nemotron"`` (streaming,
:mod:`ears.listen.streaming`). ``final`` was called ``stt`` until then; ``stt`` is
still read when ``final`` is absent.
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import threading
import time
import wave
from typing import Protocol

import numpy as np

from .. import envnames
from ..home import settings_dir
from ..keys import stt_key
from .sources import SAMPLE_RATE

def conf_path() -> str:
    """The ears' host settings file (the keeper reads the same one every loop)."""
    return os.environ.get("EARS_CONF") or str(settings_dir() / "ears.json")


def final_of(d) -> dict:
    """The ``final`` engine of a parsed ears.json - ``{"url": ..., "model": ...}`` - read
    from ``final``, else from its old name ``stt``; {} when neither is a dict."""
    if not isinstance(d, dict):
        return {}
    s = d.get("final") or d.get("stt") or {}
    return s if isinstance(s, dict) else {}


PARTIALS = ("final", "nemotron")


def partials_of(d) -> str:
    """The ``partials`` engine of a parsed ears.json: one of :data:`PARTIALS`; ``final``
    (chunks through the final engine, as before) when absent or not one of them."""
    p = d.get("partials") if isinstance(d, dict) else None
    return p if p in PARTIALS else "final"


def _conf() -> dict:
    try:
        with open(conf_path()) as f:
            return json.load(f)
    except Exception:
        return {}


def conf_stt() -> dict:
    """The ``final`` engine from ears.json (``stt`` when ``final`` is absent), or {} when
    neither is there or the file is unreadable.

    The one place both ears (room and call) take their engine from, so a change there
    (by hand, or from the phone's settings app) reaches both: each reads this
    one file, ears.json (``conf_path()``)."""
    return final_of(_conf())


conf_final = conf_stt


def conf_partials() -> str:
    """``partials`` from ears.json: ``final`` (the default) or ``nemotron``."""
    return partials_of(_conf())


# Parakeet on the mlx-audio server on :8890 (found or installed by `ears services install`), the
# same default whether or not ears.json exists - what it seeds
FALLBACK_URL = "http://127.0.0.1:8890/v1"
FALLBACK_MODEL = "mlx-community/parakeet-tdt-0.6b-v3"
# the model a URL named without one is asked for: Apple's server and whisper.cpp ignore it,
# and Parakeet's name is only right on mlx-audio
URL_ONLY_MODEL = "whisper-1"


def model_for(url, model=None) -> str:
    """The model to ask URL for: the one named, else Parakeet on the fallback URL, else whisper-1."""
    if model:
        return model
    return FALLBACK_MODEL if str(url).rstrip("/") == FALLBACK_URL else URL_ONLY_MODEL


# An env var (the session layer) wins over ears.json (the host layer), which wins over the default.
DEFAULT_WHISPER_URL = envnames.get("EARS_LISTEN_STT_URL") or conf_stt().get("url") or FALLBACK_URL
DEFAULT_WHISPER_MODEL = model_for(DEFAULT_WHISPER_URL,
                                  envnames.get("EARS_LISTEN_STT_MODEL") or conf_stt().get("model"))


def stt_pinned() -> bool:
    """True when the session layer names the engine (EARS_LISTEN_STT_URL/_MODEL), so a
    capture keeps it rather than following ears.json."""
    return bool(envnames.get("EARS_LISTEN_STT_URL") or envnames.get("EARS_LISTEN_STT_MODEL"))


class STTError(RuntimeError):
    pass


# whisper's non-speech annotations: "[BLANK_AUDIO]", "(static)", "[BEEP]",
# "(upbeat music)", "*sighs*". Heard from a noise
# tail in ~/.<agent>/apple-fm/scratch/vad/fixture.wav; left in, they would turn
# a quiet room's hiss into partials and turns.
_ANNOTATION = re.compile(r"\[[^\]]*\]|\([^)]*\)|\*[^*]*\*")


def strip_annotations(text: str) -> str:
    """Remove bracketed non-speech tags; collapse the whitespace left behind."""
    return " ".join(_ANNOTATION.sub(" ", text).split())


class STT(Protocol):
    name: str

    def transcribe(self, audio: np.ndarray) -> str:
        ...


def final_transcriber(stt):
    """STT's call for a turn's final text: ``transcribe_final`` when it has one, else
    ``transcribe`` (fakes and engines with no final-only behaviour)."""
    return getattr(stt, "transcribe_final", None) or stt.transcribe


def _words(text: str) -> list[str]:
    """TEXT's words, case-blind, punctuation stripped: "Hey, Dave!" -> ["hey", "dave"]."""
    return re.findall(r"[^\W_]+", str(text).lower())


def echoes_prompt(text: str, prompt: str) -> bool:
    """True when every word of TEXT is a word of PROMPT (case-blind, punctuation
    stripped): whisper reading its prompt back on silence or noise ("Dave, Bob." for
    a cough). Never true of a call: a call needs "hey", which the opener-free prompt
    does not hold (measured). No words is False."""
    words, said = _words(text), set(_words(prompt))
    return bool(words) and bool(said) and all(w in said for w in words)


def to_wav_bytes(audio: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(np.asarray(audio, dtype=np.int16).tobytes())
    return buf.getvalue()


class WhisperSTT:
    """OpenAI-compatible ``POST {base_url}/audio/transcriptions`` (whisper.cpp on :2022)."""

    name = "whisper"

    def __init__(
        self,
        base_url: str = DEFAULT_WHISPER_URL,
        *,
        model: str | None = None,
        timeout: float = 30.0,
        language: str | None = None,
        drop_annotations: bool = True,
        connect_retries: int = 1,
        retry_delay_s: float = 0.3,
        prompt=None,
        final_prompt=None,
    ) -> None:
        import httpx

        self.base_url = base_url.rstrip("/")
        # no model named: the default URL's (env, ears.json, else Parakeet), else model_for(URL),
        # so a client made for whisper on :2022 is not asked for Parakeet's name
        self.model = model or (DEFAULT_WHISPER_MODEL if self.base_url == DEFAULT_WHISPER_URL.rstrip("/")
                               else model_for(self.base_url))
        self.language = language
        self.drop_annotations = drop_annotations
        self.connect_retries = connect_retries
        self.retry_delay_s = retry_delay_s
        # the STT hint: a str, or a zero-arg callable asked once per request.
        # ``prompt`` rides every request (the bench's raw path); ``final_prompt`` only
        # transcribe_final, the turn's re-decode. make_stt sets final_prompt alone, so
        # partials, many short chunks often led by silence, never carry it.
        self.prompt = prompt
        self.final_prompt = final_prompt
        # A hosted STT (OpenAI's own, or any keyed server) needs a key; a local one doesn't.
        # EARS_LISTEN_STT_KEY wins, else the settings folder's stt.key (the launch agent never
        # sees a shell's export), else OPENAI_API_KEY for api.openai.com only, so a
        # user's OpenAI key is never sent to some other URL.
        # Read when the client is built: at start,
        # and when ears.json changes the engine (FollowingSTT).
        # A key a header cannot carry is refused at transcribe, BEFORE any request, by the
        # name of its source, never the key: httpx would quote the whole header in its error.
        # Not here: a raise at build would escape make_stt (outside main's try) as a bare
        # traceback, or leave FollowingSTT on its old engine with its stamp already moved.
        self._refused: str | None = None
        try:
            key = stt_key(self.base_url)
        except ValueError as exc:
            key, self._refused = None, str(exc)
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        self._client = httpx.Client(timeout=httpx.Timeout(timeout, connect=5.0), headers=headers)

    def transcribe(self, audio: np.ndarray) -> str:
        return self._request(audio, self.prompt)

    def transcribe_final(self, audio: np.ndarray) -> str:
        """A turn's final text: as transcribe, carrying final_prompt (else prompt)."""
        return self._request(audio, self.final_prompt if self.final_prompt is not None else self.prompt)

    def _request(self, audio: np.ndarray, prompt) -> str:
        data = {"model": self.model, "response_format": "json"}
        if self.language:
            data["language"] = self.language
        hint = self._prompt(prompt)
        if hint:
            # every engine takes it: whisper.cpp uses it, mlx-audio and ears serve answer 200
            # and drop it (measured), so it is never gated
            data["prompt"] = hint
        if self._refused:
            raise STTError(f"whisper at {self.base_url}: {self._refused}")
        import httpx

        wav = to_wav_bytes(audio)
        for attempt in range(1 + self.connect_retries):
            try:
                resp = self._client.post(
                    f"{self.base_url}/audio/transcriptions",
                    files={"file": ("chunk.wav", wav, "audio/wav")},
                    data=data,
                )
                resp.raise_for_status()
                text = str(resp.json().get("text", "")).strip()
                break
            except httpx.ConnectError as exc:
                # A refused connection is retried: seen once
                # mid-listen, and it cost that chunk's words.
                if attempt < self.connect_retries:
                    time.sleep(self.retry_delay_s)
                    continue
                raise STTError(f"whisper at {self.base_url}: {exc}") from exc
            except Exception as exc:
                raise STTError(f"whisper at {self.base_url}: {exc}") from exc
        text = strip_annotations(text) if self.drop_annotations else text
        # the drop-equals-prompt guard: a transcript that is only the
        # prompt's words is whisper echoing it, not speech
        return "" if hint and echoes_prompt(text, hint) else text

    @staticmethod
    def _prompt(p) -> str:
        """This request's prompt from P (a str, or a zero-arg callable): '' when there is
        none, and when the callable fails (a hint must never cost a turn)."""
        try:
            p = p() if callable(p) else p
        except Exception:
            return ""
        return str(p).strip() if p else ""

    def close(self) -> None:
        self._client.close()


class FollowingSTT:
    """A ``WhisperSTT`` that follows ears.json's ``final`` (or ``stt``) live, with no restart.

    Before every ``transcribe`` (each partial, each turn) it stats ``conf_path()``. When the
    file has changed it re-reads ``final``, and when the URL or model differ it builds a new
    client: the next chunk goes to the new engine. Restarting the capture for that cost a
    ~4 s gap in what the room heard; a stat on every call is a far faster
    change.

    The old client is not closed on a swap - an executor call may still be in flight on it -
    only at ``close()``. A file that will not parse (caught mid-write) keeps the current
    engine and is read again on the next call. A missing file, or one without ``final`` or
    ``stt``, means the fallback, as at startup.
    """

    def __init__(self, *, make=None, log=None, **whisper_kw) -> None:
        self._make = make or (lambda url, model: WhisperSTT(url, model=model, **whisper_kw))
        self._log = log if log is not None else (lambda msg: print(msg, file=sys.stderr, flush=True))
        self._lock = threading.Lock()
        self._retired: list = []
        self._stamp = self._stat()
        want = self._want()
        self._key = want if want is not None else (FALLBACK_URL, FALLBACK_MODEL)
        self._inner = self._make(*self._key)

    @staticmethod
    def _stat():
        try:
            st = os.stat(conf_path())
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size, st.st_ino)

    @staticmethod
    def _want():
        """(url, model) from ears.json; None when the file is there but will not parse."""
        try:
            with open(conf_path()) as f:
                d = json.load(f)
        except FileNotFoundError:
            d = {}
        except Exception:
            return None
        s = final_of(d)
        url = str(s.get("url") or "").strip().rstrip("/") or FALLBACK_URL
        model = model_for(url, str(s.get("model") or "").strip())
        return (url, model)

    def current(self):
        """The client for this call: the same one unless ears.json changed its engine."""
        stamp = self._stat()
        if stamp == self._stamp:
            return self._inner
        with self._lock:
            if stamp == self._stamp:
                return self._inner
            want = self._want()
            if want is None:      # mid-write: keep this engine, look again next call
                return self._inner
            self._stamp = stamp
            if want != self._key:
                self._log(f"ears: stt {self._key[0]} {self._key[1]} -> {want[0]} {want[1]} (ears.json)")
                self._retired.append(self._inner)
                self._inner = self._make(*want)
                self._key = want
            return self._inner

    @property
    def name(self) -> str:
        return getattr(self._inner, "name", type(self._inner).__name__)

    @property
    def base_url(self) -> str:
        return self._key[0]

    @property
    def model(self) -> str:
        return self._key[1]

    def transcribe(self, audio: np.ndarray) -> str:
        return self.current().transcribe(audio)

    def transcribe_final(self, audio: np.ndarray) -> str:
        return final_transcriber(self.current())(audio)

    def close(self) -> None:
        with self._lock:
            clients, self._retired = [*self._retired, self._inner], []
        for c in clients:
            c.close()


# -- the STT hint: seat names as whisper's prompt -------------------------

HINT_ENV = "EARS_STT_HINT"
# THE hint's default, when neither EARS_STT_HINT nor ears.json's stt_hint says: the one
# line to flip. OFF pending the owner's answer on the near-word trade (with it on, a real
# "hey <near-word>" can wake a seat, as measured);
# settings/ears.schema.json's stt_hint default mirrors it, held to it by a test.
HINT_DEFAULT = False
_OFF = {"0", "false", "no", "off"}


def hint_on_env():
    """True/False when EARS_STT_HINT (the session layer) says; None when unset or empty."""
    v = (os.getenv(HINT_ENV) or "").strip().lower()
    return None if not v else v not in _OFF


def hint_on_conf(d) -> bool:
    """ears.json's ``stt_hint`` (the host layer): HINT_DEFAULT when absent or null; off when
    false (or "0"/"false"/"no"/"off"); on otherwise."""
    v = d.get("stt_hint") if isinstance(d, dict) else None
    if v is None or (isinstance(v, str) and not v.strip()):
        return HINT_DEFAULT
    return not (v is False or (isinstance(v, str) and v.strip().lower() in _OFF))


class SttHint:
    """The prompt ``WhisperSTT`` sends: the wake file's seat names (``ears.wake.WakeHint``),
    unless switched off. A zero-arg callable, asked once per request.

    The switch is EARS_STT_HINT (``0``/``false``/``off`` is off, anything else on), which
    wins, else ears.json's ``stt_hint``, else HINT_DEFAULT. Both are read per call
    (ears.json by stat, as ``FollowingSTT`` reads it), so turning the hint off is a
    rollback with no restart. An ears.json caught mid-write keeps the last setting.
    """

    def __init__(self, hint=None, *, log=None) -> None:
        if hint is None:
            from ..wake import WakeHint
            hint = WakeHint()
        self._hint = hint
        self._log = log if log is not None else (lambda msg: print(msg, file=sys.stderr, flush=True))
        self._stamp = object()
        self._conf_on = HINT_DEFAULT
        self._said = None

    def on(self) -> bool:
        env = hint_on_env()
        if env is not None:
            return env
        stamp = FollowingSTT._stat()
        if stamp != self._stamp:
            try:
                with open(conf_path()) as f:
                    d = json.load(f)
            except FileNotFoundError:
                d = {}
            except Exception:      # mid-write: keep the last setting, look again next call
                return self._conf_on
            self._stamp, self._conf_on = stamp, hint_on_conf(d)
        return self._conf_on

    def __call__(self) -> str:
        on = self.on()
        text = self._hint() if on else ""
        if (on, text) != self._said:       # say it once per change, as FollowingSTT does
            self._said = (on, text)
            self._log(f"ears: stt hint {text!r} (wake file)" if text else
                      "ears: stt hint none: no seats in the wake file" if on else
                      f"ears: stt hint off ({HINT_ENV}, ears.json stt_hint, else the default)")
        return text
