"""envnames - sessionvoice's environment names, and the old names they replace.

sessionvoice began inside VoiceMode, and some of its settings still carried
VoiceMode's ``VOICEMODE_`` prefix. They are renamed to sessionvoice's own.
A host that still sets an old name keeps working: every read goes
through this table.

The rules (one for every name in :data:`ALIASES`):

- **New wins.** The NEW name is read if it is set, else the OLD name, else
  the default. When both are set, the NEW one is used.
- **Presence, not truthiness.** A name set to the empty string is SET, and it
  wins. Some settings use ``""`` as an off switch, so an empty NEW name must
  not fall through to an OLD one.
- **No warning** on an old name. The old names are listed once in the docs as
  deprecated aliases (``docs/ears.md`` and ``docs/mouth.md``, "Old environment
  names"); removing them is a later task.

Everything sessionvoice writes (code, docs, help and error text, the keeper's
exports, the shell scripts) uses the NEW name.

Two files keep their own copy of a pair because they import nothing from this
package: ``src/ears/heard.py`` (it also runs as a plain script, standard
library only) and ``keeper/call-ears`` (run by any ``python3``). The shell
scripts read a pair as ``${NEW-${OLD-default}}``. ``tests/test_envnames.py``
holds each of those to this table, and fails on a ``VOICEMODE_`` name that is
read anywhere else and is not listed here.
"""
from __future__ import annotations

import os
from typing import Mapping, Optional

#: NEW -> OLD. The NEW name is read first (presence-based); the OLD one is the
#: deprecated alias, read only when NEW is not set at all.
ALIASES: dict[str, str] = {
    # the capture's speech-to-text engine and turn detector (ears.listen). Not
    # EARS_STT_URL / EARS_STT_MODEL: those are `ears services install` knobs,
    # with another meaning (src/ears/services.py).
    "EARS_LISTEN_STT_URL": "VOICEMODE_LISTEN_STT_URL",
    "EARS_LISTEN_STT_MODEL": "VOICEMODE_LISTEN_STT_MODEL",
    "EARS_LISTEN_STT_KEY": "VOICEMODE_LISTEN_STT_KEY",
    "EARS_LISTEN_SILENCE_S": "VOICEMODE_LISTEN_SILENCE_S",
    "EARS_LISTEN_VAD_AGGRESSIVENESS": "VOICEMODE_LISTEN_VAD_AGGRESSIVENESS",
    # the Silero VAD model file. Not EARS_SILERO_ONNX, which bin/call-tap reads
    # after it, as before.
    "EARS_SILERO_MODEL": "VOICEMODE_SILERO_MODEL",
    # every Delta bot's ear-leg socket (ears.listen.sources, keeper/call-ears)
    "EARS_CALL_SOCKS": "VOICEMODE_CALL_EARS_SOCKS",
    # the recogniser-lag alarm threshold (ears.lag)
    "EARS_LAG_WARN_S": "VOICEMODE_EARS_LAG_WARN_S",
    # who moved a heard cursor (ears.heard)
    "EARS_HEARD_EVENT": "VOICEMODE_HEARD_EVENT",
    "EARS_HEARD_CALLER": "VOICEMODE_HEARD_CALLER",
    # the heard ride-along hook (ears heard hook): its off switch, budget and line cap
    "EARS_HEARD_HOOK": "VOICEMODE_HEARD_HOOK",
    "EARS_HEARD_BUDGET": "VOICEMODE_HEARD_BUDGET",
    "EARS_HEARD_MAX_LINES": "VOICEMODE_HEARD_MAX_LINES",
    # the mouth's own settings: VOICEMODE_MOUTH_<X> -> MOUTH_<X>.
    # VOICEMODE_MOUTH_SESSION_BIN and _VERIFY_BIN set to "" are off switches, and
    # stay so under either name (presence, above).
    "MOUTH_APPLE_VOICE": "VOICEMODE_MOUTH_APPLE_VOICE",
    "MOUTH_AUDIO_DAYS": "VOICEMODE_MOUTH_AUDIO_DAYS",
    "MOUTH_AUDIO_DIR": "VOICEMODE_MOUTH_AUDIO_DIR",
    "MOUTH_BACKEND": "VOICEMODE_MOUTH_BACKEND",
    "MOUTH_BARGE": "VOICEMODE_MOUTH_BARGE",
    "MOUTH_BARGE_DEVICES": "VOICEMODE_MOUTH_BARGE_DEVICES",
    "MOUTH_BARGE_FRESH_S": "VOICEMODE_MOUTH_BARGE_FRESH_S",
    "MOUTH_BARGE_WORDS": "VOICEMODE_MOUTH_BARGE_WORDS",
    "MOUTH_BARGE_WORDS_CALL": "VOICEMODE_MOUTH_BARGE_WORDS_CALL",
    "MOUTH_CALL_SOCKS": "VOICEMODE_MOUTH_CALL_SOCKS",
    "MOUTH_DEVICE": "VOICEMODE_MOUTH_DEVICE",
    "MOUTH_DIR": "VOICEMODE_MOUTH_DIR",
    "MOUTH_DUCK": "VOICEMODE_MOUTH_DUCK",
    "MOUTH_HEARD_BOX": "VOICEMODE_MOUTH_HEARD_BOX",
    "MOUTH_HOLD_GRACE_S": "VOICEMODE_MOUTH_HOLD_GRACE_S",
    "MOUTH_HOLD_IDLE_S": "VOICEMODE_MOUTH_HOLD_IDLE_S",
    "MOUTH_IDLE_S": "VOICEMODE_MOUTH_IDLE_S",
    "MOUTH_KEEP_AUDIO": "VOICEMODE_MOUTH_KEEP_AUDIO",
    "MOUTH_KOKORO_MODEL": "VOICEMODE_MOUTH_KOKORO_MODEL",
    "MOUTH_KOKORO_URL": "VOICEMODE_MOUTH_KOKORO_URL",
    "MOUTH_LABEL_WAIT_S": "VOICEMODE_MOUTH_LABEL_WAIT_S",
    "MOUTH_LOG_DIR": "VOICEMODE_MOUTH_LOG_DIR",
    "MOUTH_MAIL_ALLOW": "VOICEMODE_MOUTH_MAIL_ALLOW",
    "MOUTH_MAIL_LOG": "VOICEMODE_MOUTH_MAIL_LOG",
    "MOUTH_MAILBOX": "VOICEMODE_MOUTH_MAILBOX",
    "MOUTH_MPV_SOCKET": "VOICEMODE_MOUTH_MPV_SOCKET",
    "MOUTH_NOTICE": "VOICEMODE_MOUTH_NOTICE",
    "MOUTH_PAN": "VOICEMODE_MOUTH_PAN",
    "MOUTH_REBUFFER_S": "VOICEMODE_MOUTH_REBUFFER_S",
    "MOUTH_RESUME_ANSWER_QUESTION": "VOICEMODE_MOUTH_RESUME_ANSWER_QUESTION",
    "MOUTH_RESUME_ANSWER_WORDS": "VOICEMODE_MOUTH_RESUME_ANSWER_WORDS",
    "MOUTH_RESUME_S": "VOICEMODE_MOUTH_RESUME_S",
    "MOUTH_SENDMAIL": "VOICEMODE_MOUTH_SENDMAIL",
    "MOUTH_SESSION_BIN": "VOICEMODE_MOUTH_SESSION_BIN",
    "MOUTH_SETTINGS": "VOICEMODE_MOUTH_SETTINGS",
    "MOUTH_SOUNDS_DIR": "VOICEMODE_MOUTH_SOUNDS_DIR",
    "MOUTH_TTS_KEY": "VOICEMODE_MOUTH_TTS_KEY",
    "MOUTH_VERIFY_BIN": "VOICEMODE_MOUTH_VERIFY_BIN",
    "MOUTH_VOICE": "VOICEMODE_MOUTH_VOICE",
}

#: The ``VOICEMODE_`` names sessionvoice still reads under that name, on
#: purpose, and gives no new name.
UNTOUCHED: frozenset[str] = frozenset({
    # shared with VoiceMode
    "VOICEMODE_AGENT",
    # VoiceMode core's session id
    "VOICEMODE_SESSION_ID",
    # the home's old name: read by ears.home after SESSIONVOICE_HOME, never through
    # this table, because the home is more than a name
    "VOICEMODE_BASE_DIR",
    # VoiceMode's own clone config, which the mouth's resolver reads (from the
    # env, then ~/.voicemode/voicemode.env) and never writes
    "VOICEMODE_VOICES_DIR",
    "VOICEMODE_REMOTE_VOICES_DIR",
    "VOICEMODE_MLX_AUDIO_BASE_URL",
    "VOICEMODE_IMPRESSIONS_MODEL",
    "VOICEMODE_CAST_UNDECLARED_SEVERITY",
})

_OLD_TO_NEW: dict[str, str] = {old: new for new, old in ALIASES.items()}


def old(new: str) -> Optional[str]:
    """The deprecated alias of ``new``, or None when it has none."""
    return ALIASES.get(new)


def group(name: str) -> tuple[str, ...]:
    """Every name that sets the same thing as ``name``, NEW first.

    ``group("EARS_CALL_SOCKS")`` and ``group("VOICEMODE_CALL_EARS_SOCKS")`` are both
    ``("EARS_CALL_SOCKS", "VOICEMODE_CALL_EARS_SOCKS")``; a name with no alias is
    ``(name,)``. A fence that pops or sets a setting does it to the whole group.
    """
    new = _OLD_TO_NEW.get(name, name)
    o = ALIASES.get(new)
    return (new, o) if o else (new,)


def source(new: str, environ: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """The name :func:`get` would read ``new`` from: ``new`` if it is set, else its
    old name if that is set, else None."""
    env = os.environ if environ is None else environ
    for name in group(new):
        if name in env:
            return name
    return None


def isset(new: str, environ: Optional[Mapping[str, str]] = None) -> bool:
    """True when ``new`` or its old name is set, even to ``""``."""
    return source(new, environ) is not None


def get(new: str, default: Optional[str] = None,
        environ: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """``$new`` if it is set (``""`` counts), else its old name's value if that
    is set, else ``default``. A name with no alias reads just itself."""
    env = os.environ if environ is None else environ
    name = source(new, env)
    return default if name is None else env[name]
