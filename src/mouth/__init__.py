"""mouth - the speaking half of the ears: ``mouth say TEXT`` returns at once.

The complement to ``ears.listen`` (the ears), built new and small: a
little thing to speak back with, a stack of lines and a pluggable
backend.

It replaces nothing yet; converse still speaks.

- ``say`` queues and returns; ONE resident player speaks, in playlist
  order, so stacked openers come for free. The queue is a maildir (box.py):
  a line is a mail in ``new/``, done is ``cur/`` with a flag.
- It speaks to ONE device, exact name, by ONE order (``resolve_device``) for
  ``say``/``play``/``again`` and a mailed line alike: the asked device
  (``--device``, a mail's ``X-Mouth-Device``, or its call), else
  ``$MOUTH_DEVICE``, else ``output`` in the host settings file
  (``settings_device``), else ``default``, the system default output. The
  line's ``device_from`` names the rung that chose it. A device that is
  absent at play, a ``default`` on a machine with no default output
  included, gets a ``said reason=device-absent``; ``null`` plays nothing.
- It writes ``saying`` (first audio frame) and ``said`` (end, with the cut
  point) into the heard log, on the ears' timeline, through ``heard.append``.
- ``stop`` cuts within 50 ms; ``reason`` says why (``stop``, ``barge-in``).
- ``amend``/``retract`` change a line before it is spoken; ``say --next``
  jumps the queue and ``--now`` interrupts. See docs/mouth.md.
- ``again(utt)`` queues a say-again replay of a kept line: its WAV, as a
  spoken line, never a bed, else its text again; it replaces a resume of the
  same line.
- ``play FILE [--start S] [--end S]`` queues a sound; ``$MOUTH_DUCK``
  lowers the DJ's music while the mouth speaks.
- ``hold="turn-end"`` waits for the end of the user's turn; ``expires_s`` drops a
  line that lost its moment (hold.py: stacked openers).
- ``pan`` puts it in one ear: -1 left, 1 right (``--channel left|right``).
- Backends are pluggable (``backends.py``): kokoro, clone, silence.

    python -m mouth say --device airpods --voice alice "Hello."
    python -m mouth stop --reason barge-in
"""

from __future__ import annotations

import fcntl
import json
import math
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from ears import envnames, heard

from . import sender as _sender
from . import voices as _voices
from .paths import mouth_dir
from .voices import default_voice


def _player_running(d: Path) -> bool:
    fd = os.open(str(d / "player.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def ensure_player(d: Optional[Path] = None) -> bool:
    """Start a player unless one holds the lock. True if one was started. Its stdout and
    stderr go to ``player.log`` in the voice log folder (``ears.home.log_dir``)."""
    d = d or mouth_dir()
    d.mkdir(parents=True, exist_ok=True)
    if _player_running(d):
        return False
    import subprocess  # here, not at the top: the timeline reader never spawns

    env = {**os.environ, "MOUTH_DIR": str(d)}
    from ears.home import log_dir   # player.log in the voice log folder, never in the mouth dir
    logs = log_dir()
    logs.mkdir(parents=True, exist_ok=True)
    with open(logs / "player.log", "ab") as log:
        subprocess.Popen([sys.executable, "-m", "mouth", "serve"], env=env,
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         start_new_session=True)
    return True


PAN_NAMES = {"left": -1.0, "right": 1.0, "both": None}

#: How much the speaker wants the user's reply (speaking subscribes the agent, at a
#: level of interest):
#: high - wake me on the user's partials as they pile up;
#: normal - wake me on the user's turn; low - signed off; zero - speak and leave.
#: Carried on saying/said so a waiter (heard-first reply:AGENT) can read it.
INTERESTS = ("high", "normal", "low", "zero")


def pan_value(pan) -> Optional[float]:
    """A pan as given (-1..1, 'left', 'right', 'both'); None asks $MOUTH_PAN."""
    if pan is None:
        env = envnames.get("MOUTH_PAN")
        return pan_value(env) if env else None
    if isinstance(pan, str):
        if pan in PAN_NAMES:
            return PAN_NAMES[pan]
        pan = float(pan)
    if not -1.0 <= float(pan) <= 1.0:
        raise ValueError("mouth: pan is -1 (left) .. 1 (right)")
    return float(pan)


def settings_device(d: Optional[Path] = None) -> Optional[str]:
    """The host settings file's ``output``: a line's device when
    neither the line (``--device``, the mail) nor ``$MOUTH_DEVICE`` names one.

    The file is ``voices.settings_path(d)``. An absent file, an absent key, or
    an empty or non-string value is unset: ``None``, and ``resolve_device``
    goes on to its last rung, the system default output. An ``output`` of
    ``"default"`` or ``"null"`` written in the file is honoured as written."""
    out = _voices.settings(d).get("output")
    return out if isinstance(out, str) and out.strip() else None


#: The rungs ``resolve_device`` names, first first: the line's ``device_from``.
#: ``flag`` is ``--device`` (or ``say(device=)``); ``mail`` a mail's
#: ``X-Mouth-Device`` (or its ``X-Mouth-Extra`` device); ``call`` a mail To a
#: call's box.
DEVICE_RUNGS = ("flag", "mail", "call", "env", "settings", "default")
#: The rungs that ride a ``say`` line's mail (``X-Mouth-Extra``): the ones past the
#: caller's own choice. A line its caller named (``flag``, ``env``) is the mail
#: it was, byte for byte (tests/mouth/test_mouth_golden.py), and reads back from
#: the box with no ``device_from``.
DEVICE_RUNGS_RIDE = ("settings", "default")


def resolve_device(asked: Optional[str] = None, asked_from: str = "flag",
                   d: Optional[Path] = None) -> tuple[str, str]:
    """ONE device order for every line, ``say``/``play``/``again`` and a
    mailed line (``box.read``) alike: ``(device, rung)``.

    ASKED (as ASKED_FROM says: ``flag``, ``mail`` or ``call``), else
    ``$MOUTH_DEVICE`` (``env``), else ``output`` in the host settings file
    (``settings``, ``settings_device``), else ``"default"`` (``default``): the
    system default output, by NAME. Nothing asks PortAudio here: ``default`` is
    resolved at play (``output.resolve``), and a machine with no default output
    at all gets ``said reason=device-absent``, "no default output device".
    ``null`` at any rung is honoured: it plays nothing."""
    if asked_from not in DEVICE_RUNGS[:3]:
        raise ValueError(f"mouth: a device is asked by {', '.join(DEVICE_RUNGS[:3])}")
    if asked:
        return asked, asked_from
    env = envnames.get("MOUTH_DEVICE")
    if env:
        return env, "env"
    out = settings_device(d)
    if out:
        return out, "settings"
    return "default", "default"


def say(text: str, *, voice: Optional[str] = None, speed: Optional[float] = None,
        backend: Optional[str] = None, device: Optional[str] = None,
        pan=None, log_dir: Optional[Path] = None, wait: bool = False, timeout: float = 300.0,
        priority: Optional[str] = None, d: Optional[Path] = None, spawn: bool = True,
        extra: Optional[dict] = None, hold: Optional[str] = None,
        expires_s: Optional[float] = None, interest: Optional[str] = None,
        in_reply_to: Optional[str] = None, volume=None, duck: Optional[bool] = None,
        duck_level=None, normalize: Optional[bool] = None, lead_s=None,
        barge_words=None, instrumental: Optional[bool] = None, cap: bool = False,
        cut_bed: Optional[str] = None, cascaded: Optional[dict] = None,
        alert: bool = False) -> dict:
    """Queue ``text``; return the queued item (``utt``), or with ``wait`` its ``said`` record.

    ``priority`` (a playlist, lower plays sooner): None (50, the back of the
    list), a number (10, 20, 30...), ``next`` (0, the front), or ``now`` (-1,
    the front, and the line playing now is cut with ``reason=interrupted``;
    only that line, never the queue behind it).

    ``in_reply_to``: the Message-ID of the heard turn this line answers,
    ``<id>`` or bare. The mail carries In-Reply-To, References (the parent's root,
    then the ID) and the parent's Call-ID, looked up in the heard box
    (``mouth.reply``). A bad ID is refused before anything is written. A parent
    not found still threads on the ID; ``_parent_found`` on the item says which.

    ``volume``: this line's level, 0-100 (100 is today's), else the
    caller's cascade (``volume``, or ``play_volume`` for a played file), the
    host file, 100. ``duck``/``duck_level``: a played file's own duck setting
    (``mouth play --duck/--no-duck --duck-level N``), else the same layers.
    A bad one is refused before anything is written.

    ``normalize``: this line's own ``--normalize/--no-normalize``,
    else ``normalize`` in the caller's cascade, the host file, on: the voice is
    levelled to ``normalize_lufs`` by its per-voice gain (``mouth.level``).
    Only what a layer above the host answered rides on the line.

    ``lead_s``: seconds of this line's audio the player buffers
    before its first frame (``mouth say --lead S``), else ``lead_s`` in the
    caller's cascade, the host file, then the built-in: 3 for a clone voice
    (one rendering below real time), 0 (play as it streams) for any other.
    An explicit 0 is 0, for a clone too. A bad one is refused before anything
    is written.

    ``instrumental``: a played file's own ``--instrumental/--no-instrumental``,
    else ``$MOUTH_MUSIC_INSTRUMENTAL`` (the caller's, now), the caller's cascade
    (``music_instrumental``), the host file, on (``voices.instrumental_setting``).
    Only what a layer above the built-in answered rides on the line, as
    ``instrumental``/``instrumental_from``. A spoken line takes none: asking for one
    is refused, as is a value that is not on or off, before anything is written.
    For a played file with no instrumental yet, the caller decides here, before the
    drop, whether to start (or join) its split (``mouth.autosplit``): the play is then
    stamped ``split_wait``/``split_eta_s``, a ``wait`` timeout grows by the split's
    deadline, and the decision's lines go to stderr, never spoken. An
    ``--instrumental`` that cannot be met is refused, before anything is written.

    ``barge_words``: this line's own barge count, the model's
    layer (``mouth say --barge-words N|off``, ``--no-barge``): a whole number of
    1 or more, or ``off`` (no cut on a word count; the user's voice still ducks it and
    a stop ask still cuts it). It beats the user's ``barge_words`` setting and
    the default (``hold.barge_policy``). It rides the line as ``barge_words``
    and ``barge_words_from: flag``, and a resume carries it. A bad one is
    refused before anything is written.

    ``cap``: refuse a line longer than ``say_max_chars`` characters (``len(text)``)
    in the caller's cascade, the host file, else 300; 0 or ``off`` is no cap
    (``voices.say_max_chars_setting``). ``mouth say`` sets it unless ``--long``;
    nothing else does, so a resume, a replay or a notice is never refused. The
    refusal is one ValueError line, ``mouth: over 300 characters: split it, or
    mail it (or --long)``, raised before anything is queued.

    ``cut_bed``: the utt of a playing bed to stop, aimed at it alone (``reason=
    interrupted``), right after this line is dropped and before any wait: how
    ``mouth play --now`` stops the song on its device (:func:`play_many`).

    ``cascaded``: the caller's cascade already read (a ``voices.cascade()`` answer
    that asked for at least ``voices.LINE_KEYS``), so this line runs no
    ``session settings`` of its own: how a play's one run serves its mode and its
    line (:func:`play_many`). None reads it here, when the line needs it.

    ``log_dir``: where this line's heard rows go. It rides the mail as
    ``X-Mouth-Log-Dir``, and the player honours it only under its log root
    (``box.heard_root``), so one outside the root is refused here, before anything
    is written, rather than ignored at play.

    ``alert``: the host's own line, never a caller's (``mouth say --alert``):
    what a part of sessionvoice says when its mail fails. Whoever runs it, its
    ``agent``, ``session`` and ``mail_from`` are None, so no seat is named on
    it or told of it; and, as for a resume, no caller's cascade is read (the
    host's voice and level). Its interest is ``zero`` (any other
    is refused), its priority ``next`` unless one is given, and it is never
    refused for length. It rides the line as ``alert: true``, onto its saying
    and said rows, and no drop notice is ever mailed about it
    (``notice._decide``).
    """
    priority = _priority(priority)
    if alert:
        if interest not in (None, "zero"):
            raise ValueError("mouth: an alert line's interest is zero")
        interest = "zero"
        priority = "next" if priority is None else priority
        cap = False
    from .hold import HOLDS, NO_HOLD

    if hold not in (None, *HOLDS, NO_HOLD):
        raise ValueError("mouth: hold is " + " or ".join((*HOLDS, NO_HOLD)))
    if expires_s is not None and expires_s <= 0:
        raise ValueError("mouth: expires is seconds from now, above 0")
    if interest not in (None, *INTERESTS):
        raise ValueError("mouth: interest is " + ", ".join(INTERESTS))
    volume = _voices.pct(volume, "volume")
    duck_level = _voices.pct(duck_level, "duck level")
    if duck is not None and not isinstance(duck, bool):
        raise ValueError("mouth: duck is on or off")
    if normalize is not None and not isinstance(normalize, bool):
        raise ValueError("mouth: normalize is on or off")
    if instrumental is not None:
        if not isinstance(instrumental, bool):
            raise ValueError("mouth: instrumental is on or off")
        if backend != "file":
            raise ValueError("mouth: instrumental is for a played file (mouth play)")
    lead_s = _voices.secs(lead_s, "lead")
    from .hold import check_barge_words

    barge_words = check_barge_words(barge_words)
    # the player would ignore it; say so now, not at play. The env's own
    # value is the root itself, as box.read() takes it (a resume re-drops it).
    if log_dir and str(log_dir) != envnames.get("MOUTH_LOG_DIR"):
        from .box import confined, heard_root

        root = heard_root()
        if confined(str(log_dir), root) is None:
            raise ValueError(f"mouth: log dir {log_dir} is not under the mouth's log root {root} "
                             "($MOUTH_LOG_DIR, else <sessionvoice home>/logs/conversations)")
    from . import reply

    rid = reply.norm_id(in_reply_to) if in_reply_to is not None else None
    device, device_from = resolve_device(device, "flag", d)
    if not text.strip():
        raise ValueError("mouth: nothing to say")
    d = d or mouth_dir()
    if rid:  # the parent is read before anything is written; never an error
        parent = reply.find_parent(rid)
        hdrs = reply.reply_headers(rid, parent)
    d.mkdir(parents=True, exist_ok=True)
    now = time.time()
    sid = None if alert else heard.caller_session()   # an alert line names no caller
    is_file = backend == "file"
    # ONE `session settings` run serves the voice, the level and the duck
    # A resume (the player's own, ``resume_of``) reads none: the player's session is
    # whoever spawned it, and the cut line carries what its caller answered.
    # A say-again replay (``again_of``) is the same: its caller is the keeper.
    resuming = bool((extra or {}).get("resume_of") or (extra or {}).get("again_of"))
    # an alert line is the host's own too: no caller's cascade, voice or address
    hosts = resuming or alert
    need = not hosts and (voice is None or volume is None or is_file or normalize is None
                          or lead_s is None or cap)
    if not need:
        cascaded = {}
    elif cascaded is None:
        cascaded = _voices.cascade(sid, _voices.LINE_KEYS)
    if cap:   # mouth say's own line cap: refused before anything is queued
        limit, _ = _voices.say_max_chars_setting(cascaded=cascaded, d=d)
        if limit and len(text) > limit:
            raise ValueError(f"mouth: over {limit} characters: split it, or mail it (or --long)")
    level, level_from = _voices.volume_setting(file=is_file, flag=volume, cascaded=cascaded, d=d)
    item = {
        "utt": uuid.uuid4().hex[:12],
        "text": text,
        # the caller's cascade and env; a resume is the player's own, so
        # it takes the box's order: the player's env never beats the host file
        "voice": voice or default_voice(d, sid, cascaded, caller=not hosts)[0],
        "speed": speed,
        "backend": backend or envnames.get("MOUTH_BACKEND") or "auto",
        "device": device,
        # the rung that chose it, when it rides (a resume carries its own, as an extra)
        **({"device_from": device_from} if device_from in DEVICE_RUNGS_RIDE else {}),
        "pan": pan_value(pan),
        "requested_t": now,
        "requested_ts": datetime.fromtimestamp(now).astimezone().isoformat(timespec="milliseconds"),
        "agent": None if alert else heard.caller_agent(),
        "session": sid,
        # who ran it, as `message send` would name them (mouth.sender); the drop's
        # From. A resume or a say-again replay carries its line's own, as an extra.
        # An alert line has none: the box writes the mouth's own From for it.
        "mail_from": None if hosts else _sender.address(),
        "log_dir": str(log_dir) if log_dir else envnames.get("MOUTH_LOG_DIR"),
        "wait": wait,
        "priority": priority,
        # ``none``: the caller's own no-hold, past the room default, as the mail says it
        "hold": "now" if hold == NO_HOLD else hold,
        "interest": interest,
        "expires_s": expires_s,
        "expires_t": (now + expires_s) if expires_s is not None else None,
        **(extra or {}),
    }
    if alert:   # only an alert line carries the key: every other line's rows stay as they were
        item["alert"] = True
    # Only what a layer above the built-in answered rides on the line: with no
    # flag and no setting, the mail is byte-for-byte today's, and the player
    # fills in the built-in when it takes the line (voices.fill_item).
    if level_from != "builtin":
        item.setdefault("volume", level)
        item.setdefault("volume_from", level_from)
    if not is_file:   # levelling and the lead: only a flag or the cascade rides
        for key, flag in (("normalize", normalize), ("normalize_lufs", None), ("lead_s", lead_s)):
            if flag is not None:
                item.setdefault(key, flag)
                item.setdefault(key + "_from", "flag")
            elif key in cascaded and _voices._CHECK[key](cascaded[key][0]) is not None:
                item.setdefault(key, cascaded[key][0])
                item.setdefault(key + "_from", cascaded[key][1])
    if barge_words is not None and not is_file:   # the line's own: the model's layer
        item.setdefault("barge_words", barge_words)
        item.setdefault("barge_words_from", "flag")
    if is_file and "duck" not in item:
        dk = _voices.duck_setting(on=duck, level=duck_level, cascaded=cascaded, d=d,
                                  none_if_builtin=True)
        if dk is not None:
            item["duck"] = dk
    # the caller's env is read here, at call time; a resume's is the player's, so it
    # keeps what its first line carried, or the player fills it in (voices.fill_item)
    plan = None
    # a --stem line ignores the setting: no instrumental rides, no split starts (mouth.stems)
    if is_file and not resuming and "instrumental" not in item and "stem" not in item:
        on, src = _voices.instrumental_setting(flag=instrumental, cascaded=cascaded, d=d)
        if src != "builtin":
            item["instrumental"], item["instrumental_from"] = on, src
        if item.get("file"):
            # the caller's half: start (or join) the split of a file with no instrumental
            # yet, its first record written before the play is queued (mouth.autosplit).
            # Refuses an --instrumental that cannot be met, before anything is queued.
            from . import autosplit

            plan = autosplit.decide(item["file"], on=on, src=src, device=device,
                                    start=item.get("start"), end=item.get("end"), d=d)
            item.update(plan.stamp)
            if plan.deadline_t is not None:   # a waited play is not timed out by its own split
                timeout += max(0.0, plan.deadline_t - time.time())
    if rid:
        from .box import REPLY

        for k in REPLY:
            item.pop(k, None)
        item.update({k: hdrs[h] for k, h in REPLY.items() if h in hdrs},
                    _parent_found=parent is not None)
    from . import maillog

    item["mail_log"] = maillog.box_from_env()
    if item["mail_log"]:
        # The thread root: the request mail if it came by mail, else this entry.
        queued = maillog.entry({**item, "log_root": item.get("mail_id")}, "queued",
                               {k: item.get(k) for k in ("utt", "text", "voice", "device", "pan",
                                                         "priority", "hold", "expires_t",
                                                         "agent", "session",
                                                         "requested_ts", "file", "mail_id")}
                               | {"device_from": item.get("device_from") or device_from})
        item["log_root"] = item.get("mail_id") or queued
    from .box import box_dir, drop

    # a waiter holds done/<utt>.wait (LOCK_EX) from before the drop to
    # its exit. A drop finds it held and leaves the notice to us (stderr);
    # once released, the drop is mailed instead. The fd is not inherited (PEP 446),
    # so the player spawned below never holds it.
    lock = _wait_lock(d, item["utt"]) if wait else None
    try:
        drop(box_dir(d), item)
        if priority == "now":
            cur = _playing(d)
            if cur:
                _stop_file(d, {"t": time.time(), "reason": "interrupted", "flush": False, "utt": cur["utt"]})
        if cut_bed:
            _stop_file(d, {"t": time.time(), "reason": "interrupted", "flush": False, "utt": cut_bed})
        if spawn:
            ensure_player(d)
        item.setdefault("device_from", device_from)   # the caller is told, whether it rode or not
        if plan is not None:
            from . import autosplit

            autosplit.tell(plan)     # the caller's stderr, never spoken
        if not wait:
            return item
        done = d / "done" / f"{item['utt']}.json"
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                rec = _take_done(done)
                if rec is not None:
                    if item.get("split_wait"):
                        rec.setdefault("split", {"eta_s": item.get("split_eta_s")})
                    return rec
                time.sleep(0.02)
            raise TimeoutError(f"mouth: no said for {item['utt']} within {timeout:.0f}s")
        except (TimeoutError, KeyboardInterrupt):
            # Leaving early: release first, so a drop from now on
            # is mailed; then look once more, for a drop that found us still here.
            _wait_unlock(lock)
            lock = None
            grace = time.monotonic() + WAIT_GRACE_S
            while time.monotonic() < grace:
                rec = _take_done(done)
                if rec is not None:
                    return rec
                time.sleep(0.02)
            raise
    finally:
        _wait_unlock(lock)


#: How long a waiter that gives up still looks for its said record.
WAIT_GRACE_S = 0.5


def _wait_lock(d: Path, utt: str) -> tuple[int, Path]:
    """Create and LOCK_EX ``<d>/done/<utt>.wait``: a live waiter, for the drop notice's probe."""
    p = d / "done" / f"{utt}.wait"
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
    except BaseException:
        os.close(fd)
        raise
    return fd, p


def _wait_unlock(lock: Optional[tuple[int, Path]]) -> None:
    """Unlink, then release (close). Safe to call twice: the second gets None."""
    if lock is None:
        return
    fd, p = lock
    try:
        p.unlink(missing_ok=True)
    except OSError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass


def _take_done(done: Path) -> Optional[dict]:
    if not done.exists():
        return None
    rec = json.loads(done.read_text())
    done.unlink(missing_ok=True)
    return rec


def _stop_file(d: Path, rec: dict) -> dict:
    d.mkdir(parents=True, exist_ok=True)
    part = d / f".stop.{os.getpid()}.part"
    part.write_text(json.dumps(rec))
    part.rename(d / "stop")
    return rec


def _playing(d: Path) -> Optional[dict]:
    try:
        return json.loads((d / "playing.json").read_text())
    except (FileNotFoundError, ValueError):
        return None


def _priority(p):
    """None, ``next``, ``now``, or a number (a numeric string counts); else ValueError."""
    if p is None or p in ("next", "now"):
        return p
    try:
        f = float(p)
    except (TypeError, ValueError):
        raise ValueError("mouth: priority is next, now, or a number (lower plays sooner)") from None
    return int(f) if f == int(f) else f


def sound_label(source: str, start: Optional[float] = None, end: Optional[float] = None) -> str:
    """A sound's text: ``[sound x.wav 0-5s]``. The said record and a drop notice name it so."""
    span = f" {start or 0:g}-{end:g}s" if end is not None else (f" from {start:g}s" if start else "")
    return f"[sound {Path(source).name}{span}]"


def _fetched(url: str, d: Optional[Path]) -> Path:
    """A remote SOURCE as a local file, before anything is queued (cache or spool).

    The caller's ``play_remote`` and ``play_cache`` (one cascade run of their own,
    lock-aware: ``voices.play_url_settings``), then the fetch: into the play cache, or
    with the cache off into the spool, which this play sweeps first. One line on the
    caller's stderr on a miss (``mouth: fetching URL``), none on a hit.

    ValueError, naming the URL without its credentials: remote plays switched off
    (naming the setting and the layer), a failed fetch (``fetch``'s own text), or the
    cache or spool that could not be written (an OSError: a full disk, an unwritable
    dir). Nothing is queued either way."""
    from . import fetch

    d = d or mouth_dir()
    s = _voices.play_url_settings(
        cascaded=_voices.cascade(heard.caller_session(), _voices.PLAY_URL_KEYS), d=d)
    on, src = s["remote"]
    if not on:
        raise ValueError(f"mouth: a remote URL is not played: play_remote is off ({src}); "
                         "a path or a file:// URL still plays")

    def tell(line: str) -> None:   # the caller's stderr, never spoken (autosplit.tell)
        print(line, file=sys.stderr, flush=True)

    try:
        fetch.sweep_spool(d)
        if s["cache"][0]:
            return fetch.cached(url, s["dir"], max_age_s=s["max_age_s"], tell=tell)
        return fetch.spooled(url, fetch.spool_dir(d), tell=tell)
    except OSError as e:
        why = e.strerror or type(e).__name__
        if e.filename:
            why = f"{why}: {e.filename}"
        raise ValueError(f"mouth: could not fetch {fetch.shown(url)}: "
                         f"could not write it ({why})") from None


def play(source: str, *, start: Optional[float] = None, end: Optional[float] = None,
         extra_tag: Optional[dict] = None, instrumental: Optional[bool] = None,
         stem=None, queue: Optional[str] = None, **kw) -> dict:
    """Queue a sound file (or ``start``..``end`` seconds of it). Same queue, stop, log.

    ``source`` is a path, or a URL (``mouth.fetch.classify``):

    - ``file:///abs/song.m4a`` plays the file it names, percent-decoded, in place: it
      is never fetched or copied, and the remote switch does not refuse it. A
      ``file://`` URL naming another host is refused.
    - ``http://`` and ``https://`` are fetched first, into the play cache (a second
      play is a hit, with no request), and the CACHED file is queued as ``file``.
      The line carries ``source_url``, the URL without its credentials. The
      settings ``play_remote`` (off: refused) and ``play_cache`` (off: fetched for
      this play only) answer from the caller's layers; a host lock wins.
    - Any other scheme is refused.

    A URL source (``file://`` or http(s)) is registered (``mouth.sourcekey``): its
    sidecar is ``<mouth dir>/url-sidecars/<sha256 of its bytes>/``, never beside it.
    A plain path already registered is re-checked, so a replaced file gets its new
    bytes' folder. An unreadable source is refused.

    Every refusal, and a failed fetch, is a ValueError before anything is queued.
    The decoder still reads local files only (``-protocol_whitelist file``): it
    never sees the URL.

    ``stem``: play the file's stems instead (``mouth play --stem NAME[,NAME]``), one or a
    comma list (or a list) of ``vocals``, ``drums``, ``bass``, ``other``, mixed in ONE
    ffmpeg (``mouth.stems``). It is exclusive with ``instrumental`` and ignores the
    setting. Every stem must be in the file's sidecar already: a missing one is refused,
    before anything is queued, naming the one command that makes it (``mouth split``).
    The line carries the names only; the player finds the files at take time.

    ``queue``: where the play goes in the queue (:data:`QUEUE_MODES`): ``last``, the
    end (today's plain play); ``next``, the front (rank 0), so it plays when the song
    on its device ends; ``now``, which stops the song playing on THAT device only and
    plays at once (:func:`play_many`). None reads ``play_queue`` from the caller's
    layers and the host file, else ``last``. Speech mixes over a song in every mode:
    ``now`` never cuts a spoken line. ``priority`` alone keeps its raw meaning
    (``say``); both together are refused.

    ``instrumental``: True or False is this play's own answer to whether it plays
    the file's instrumental (``--instrumental``/``--no-instrumental``); None reads
    the setting's layers (``say``). With no instrumental yet, a play of a song may
    start its split and wait for it in the queue (``mouth.autosplit``)."""
    return play_many([source], start=start, end=end, extra_tag=extra_tag,
                     instrumental=instrumental, stem=stem, queue=queue, **kw)[0]


def _prepare(source: str, *, start: Optional[float] = None, end: Optional[float] = None,
             extra_tag: Optional[dict] = None, instrumental: Optional[bool] = None,
             stem=None, d: Optional[Path] = None) -> tuple[str, dict]:
    """One play's source made ready to drop, before anything is queued: its scheme
    checked, a URL fetched, a URL source registered, its stems checked. Returns
    ``(text, extra)`` for ``say``. Every refusal is a ValueError (:func:`play`)."""
    from . import fetch

    kind, value = fetch.classify(source)   # a refused scheme or file:// host raises here
    if start is not None and end is not None and end <= start:
        raise ValueError("mouth: --end must be after --start")   # before any download
    origin = {}
    if kind == "remote":
        value = str(_fetched(value, d))
        origin = {"source_url": fetch.shown(source)}
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"mouth: no such file {value if kind == 'file' and value else source}")
    source = str(path)
    # A URL source's sidecar is url-sidecars/<sha256>/, never beside it (design.md §11):
    # registered before anything reads it. A plain path registers nothing new, but one
    # already registered is refreshed, so a replaced file finds its new bytes' folder.
    from . import sourcekey

    if kind != "path" or sourcekey.lookup(path, d) is not None:
        sourcekey.register(path, d)   # unreadable: refused, nothing queued
    extra = {"file": source, "start": start, "end": end, **origin, **(extra_tag or {})}
    if stem is not None:
        from . import stems

        if instrumental is not None:
            raise ValueError("mouth: --stem plays stems, not the instrumental: "
                             "drop --instrumental/--no-instrumental")
        names = stems.names(stem)
        stems.check(source, names)       # a missing stem: refused with the command that makes it
        extra["stem"] = names
    return sound_label(source, start, end), extra


#: ``mouth play``'s queue modes (``play_queue``, ``--now``/``--next``/``--last``).
QUEUE_MODES = _voices.QUEUE_MODES
#: The top of a ``now`` play's rank band, (-1, -0.5]: ahead of every ``next`` (0) and
#: every plain play (50), behind a spoken ``now`` line (-1), which mixes over it anyway.
NOW_SONG_RANK = -0.5
#: The lowest rank a ``now`` play takes: just above -1, so a spoken ``now`` line
#: still sorts first. A float halving towards -1 reaches -1.0 exactly after ~53
#: stacked ``now`` runs; past this floor a new one ties with the lowest, FIFO.
NOW_RANK_FLOOR = math.nextafter(-1.0, 0.0)


def now_rank(lines, key: Optional[str]) -> float:
    """The rank of a ``now`` play on device KEY, given the queued LINES
    (``box.Queue(d).lines()``): strictly below every queued played file for the
    same device whose rank is in (-1, -0.5], so it plays before an earlier ``now``
    run's remainder, which stays queued behind it; else :data:`NOW_SONG_RANK`.
    It halves towards -1 and never reaches it (:data:`NOW_RANK_FLOOR`)."""
    from . import bed
    from .box import DEFAULT_RANK

    ranks = [float(x["_rank"]) for x in lines
             if x.get("file") and -1.0 < float(x.get("_rank", DEFAULT_RANK)) <= NOW_SONG_RANK
             and bed.device_key(x.get("device")) == key]
    if not ranks:
        return NOW_SONG_RANK
    r = min(ranks)
    return max(r - (r + 1) / 2, NOW_RANK_FLOOR)


def _bed_playing(d: Path, key: Optional[str]) -> Optional[str]:
    """The utt of the song playing on device KEY, as ``bed.json`` names it (its own
    ``key``, which the player computed), else None. Only while a player runs:
    ``bed.json`` outlives a player that died."""
    from . import bed

    if not d.exists() or not _player_running(d):
        return None
    for b in bed.read(d):
        if b.get("key") == key and b.get("utt"):
            return b["utt"]
    return None


def queue_mode(queue: Optional[str] = None, *, d: Optional[Path] = None,
               cascaded: Optional[dict] = None) -> tuple[str, str]:
    """``(mode, from)`` for a ``mouth play``: QUEUE as given (``flag``), else
    ``play_queue`` in the caller's layers and the host file (read only when no flag
    answers), else ``last`` (``builtin``). A bad QUEUE is refused.

    ``cascaded``: the play's own cascade, already read with ``play_queue`` among its
    keys (``voices.play_keys``), so no run is made here; None makes one."""
    if queue is not None:
        q = queue.strip().lower() if isinstance(queue, str) else None
        if q not in QUEUE_MODES:
            raise ValueError("mouth: queue is " + ", ".join(QUEUE_MODES))
        return q, "flag"
    if cascaded is None:
        cascaded = _voices.cascade(heard.caller_session(), _voices.PLAY_MODE_KEYS)
    return _voices.play_mode_settings(cascaded, d)["play_queue"]


#: Which of a ``mouth play --find``'s matches it plays (``play_find_matches``,
#: ``--matches``).
FIND_MATCHES = _voices.FIND_MATCHES


def find_matches(matches: Optional[str] = None, *, d: Optional[Path] = None,
                 cascaded: Optional[dict] = None) -> tuple[str, str]:
    """``(mode, from)`` for a ``mouth play --find``: MATCHES as given (``flag``), else
    ``play_find_matches`` in the caller's layers and the host file (read only when
    no flag answers), else ``first`` (``builtin``). A bad MATCHES is refused.
    ``first`` plays the first match, ``list`` plays none and lists them, ``all``
    plays every one as one run (:func:`play_many`).

    ``cascaded``: the play's own cascade, read up front with ``play_find_matches``
    among its keys (``voices.play_keys``), so no run is made here; None makes one."""
    if matches is not None:
        m = matches.strip().lower() if isinstance(matches, str) else None
        if m not in FIND_MATCHES:
            raise ValueError("mouth: matches is " + ", ".join(FIND_MATCHES))
        return m, "flag"
    if cascaded is None:
        cascaded = _voices.cascade(heard.caller_session(), _voices.PLAY_MODE_KEYS)
    return _voices.play_mode_settings(cascaded, d)["play_find_matches"]


def play_many(sources, *, start: Optional[float] = None, end: Optional[float] = None,
              extra_tag: Optional[dict] = None, instrumental: Optional[bool] = None,
              stem=None, queue: Optional[str] = None, skip=None, **kw) -> list[dict]:
    """Queue SOURCES as ONE run, in order, placed by one queue mode; return the
    queued items (each as :func:`play` returns it), in order.

    The caller's cascade is read ONCE (``cascaded``, else here: one ``session
    settings`` run, :func:`voices.play_keys`), and it serves the mode and every
    line of the run: the voice, the level, the duck and the instrumental.
    The mode is resolved once (:func:`queue_mode`). Every source is prepared first
    (fetched, registered, checked: :func:`_prepare`), then each is dropped in order
    at the mode's one rank, so equal ranks keep the run FIFO and it plays as one
    block: ``last`` at the end, ``next`` at the front, ``now`` ahead of both
    (:func:`now_rank`). Under ``now`` the song playing on the run's device is read
    BEFORE the drop, so a run never cuts its own first song, and ONE stop aimed at
    it is written after the last drop, so no other queued song takes the freed
    device in between. Nothing else is cut: not a spoken line, not another device.

    ``skip``: None, and a source that cannot be prepared raises before anything is
    queued. Else a callable ``skip(source, error)``: that source is handed to it and
    left out, and the run goes on; with none left, a ValueError. ``wait`` plays one
    source only. ``priority`` alone keeps its raw meaning (``say``); with ``queue``
    it is refused. Every other keyword is ``say``'s."""
    sources = list(sources)
    if not sources:
        raise ValueError("mouth: nothing to play")
    if kw.get("wait") and len(sources) > 1:
        raise ValueError("mouth: --wait plays one")
    d = kw.get("d")
    priority = kw.pop("priority", None)
    if priority is not None and queue is not None:
        raise ValueError("mouth: a play takes a queue mode or a priority, not both")
    cascaded = kw.pop("cascaded", None)
    if cascaded is None:   # the one run: the mode (if no flag answers) and the lines
        cascaded = _voices.cascade(heard.caller_session(), _voices.play_keys(
            queue=priority is None and queue is None))
    mode = mode_from = None
    if priority is None:
        mode, mode_from = queue_mode(queue, d=d, cascaded=cascaded)
        priority = {"next": "next", "last": None}.get(mode)
    prepared = []
    for src in sources:
        try:
            prepared.append(_prepare(src, start=start, end=end, extra_tag=extra_tag,
                                     instrumental=instrumental, stem=stem, d=d))
        except ValueError as e:
            if skip is None:
                raise
            skip(src, e)
    if not prepared:
        raise ValueError("mouth: nothing to play")
    cut = None
    if mode == "now":
        from . import bed
        from .box import Queue

        dd = d or mouth_dir()
        key = bed.device_key(resolve_device(kw.get("device"), "flag", d)[0])
        cut = _bed_playing(dd, key)   # read before the drop: a run never cuts its own song
        priority = now_rank(Queue(dd).lines(), key) if dd.exists() else NOW_SONG_RANK
    out = []
    for i, (text, extra) in enumerate(prepared):
        if mode is not None and mode_from != "builtin":   # only what a layer answered rides
            extra = {**extra, "queue": mode, "queue_from": mode_from}
        last = i == len(prepared) - 1
        out.append(say(text, backend="file", instrumental=instrumental, extra=extra,
                       priority=priority, cut_bed=cut if last else None,
                       cascaded=cascaded, **kw))
    return out


#: How long a say-again replay waits for the end of the user's turn before it is dropped.
AGAIN_EXPIRES_S = 60.0


class NoAudio(ValueError):
    """``mouth again`` has nothing to replay: no kept WAV, and no text to say
    instead. The CLI exits 3, and the keeper falls
    back to waking the seat."""


def again(utt: str, *, seq=None, heard_text: Optional[str] = None, volume=None,
          text: Optional[str] = None, voice: Optional[str] = None,
          agent: Optional[str] = None, session: Optional[str] = None,
          device: Optional[str] = None, d: Optional[Path] = None, spawn: bool = True,
          root: Optional[Path] = None) -> dict:
    """Queue a say-again replay of line UTT: its kept WAV, as a SPEECH line.

    Not ``play``: every played file is a bed, which the user's voice never cuts and
    lines mix over. The replay names the line (``again_of``), so it stays in
    the speech lane (``voices.played_file``), is levelled as its voice is, and
    takes ``volume``, not ``play_volume``. It carries:

    - the original text (the saying row, the echo trim and the barge guard
      see the real words), voice, backend, speed, agent and session, from
      the line's kept note (``audio.find``); the backend rides as
      ``backend_asked`` too, since the box makes a ``file`` line the ``file``
      backend, and the replay is levelled under the backend that made it;
    - ``file`` = the kept WAV, which the speech lane plays (``FileSound``);
    - ``hold=turn-end``, ``priority=next`` and ``expires_s`` 60;
    - ``again_seq`` and ``again_heard``: the heard turn that asked for it;
    - ``again_from``: ``audio``, or ``text`` for the fallback below;
    - ``volume``: the level the original played at (the keeper passes its
      said row's ``volume``), else the host's;
    - no ``notice_to``: its note goes to the line's own seat.

    **When the WAV is gone** (rung 2) the replay
    re-synthesises the line instead, still with no model turn: the note's text,
    voice, backend, agent and session, or, when the note is gone too, TEXT and
    VOICE (and AGENT and SESSION) as the keeper read them off the line's saying
    row. It is a plain TTS line with ``again_of`` and ``again_from: text``.
    With neither it raises :class:`NoAudio` naming why (``mouth again`` exits 3).

    **It replaces a resume of the same line**: a queued
    ``resume_of`` UTT is filed T with ``reason=again``, and a playing one gets a
    stop aimed at its utt only, reason ``again``. Both are quiet: the notice is
    ``none: replaced by a say-again``. This happens only once the replay is
    queued, so a replay that fails leaves the resume as it was. The returned
    item names them in ``replaced``.

    The device is ``say()``'s own resolution (``resolve_device``), as for a new line. Raises
    ValueError for an utt that is not a plain id, and for anything ``say()``
    refuses. Reads no caller's cascade: the caller is the keeper, never the
    line's seat.
    """
    from . import audio as _audio

    note = _audio.find(utt, root)   # ValueError for an utt that is not a plain id
    noted = str((note or {}).get("text") or "")
    given = str(text or "")
    if note is None:
        why = "no kept line has that utt"
    elif not note["wav_exists"]:
        why = f"its WAV is gone ({note['wav']})"
    elif not (noted.strip() or given.strip()):
        why = "its note has no text"
    else:
        why = None
    extra: dict = {"again_of": utt}
    if seq is not None:
        extra["again_seq"] = seq
    if heard_text:
        extra["again_heard"] = heard_text
    if why is None:   # rung 1: the kept WAV itself
        extra.update({"file": note["wav"], "again_from": "audio",
                      "agent": note.get("agent"), "session": note.get("session"),
                      "mail_from": note.get("mail_from")})
        if note.get("backend"):
            # The backend that made the WAV. The box makes any line with a ``file`` the
            # ``file`` backend (box.read), so without this the player would level the
            # replay under ``file``, the decoder, and the rows would lose the original's.
            extra["backend_asked"] = note["backend"]
        say_text, say_voice = noted if noted.strip() else given, note.get("voice") or voice
        speed, backend = note.get("speed"), note.get("backend")
    elif noted.strip():   # rung 2: the note's text, in the note's voice
        extra.update({"again_from": "text", "again_why": why,
                      "agent": note.get("agent"), "session": note.get("session"),
                      "mail_from": note.get("mail_from")})
        say_text, say_voice = noted, note.get("voice") or voice
        speed, backend = note.get("speed"), note.get("backend")
    elif given.strip():   # rung 2, the note gone too: what the keeper read off the saying row
        extra.update({"again_from": "text", "again_why": why, "agent": agent, "session": session})
        say_text, say_voice, speed, backend = given, voice, None, None
    else:
        raise NoAudio(f"mouth: no audio for {utt}: {why}, and no text to say instead")
    item = say(say_text.strip(), voice=say_voice, speed=speed, backend=backend,
               device=device, d=d, spawn=spawn, hold="turn-end", priority="next",
               expires_s=AGAIN_EXPIRES_S, volume=volume, extra=extra)
    return {**item, "replaced": _replace_resume(utt, d or mouth_dir())}


def _replace_resume(utt: str, d: Path) -> list:
    """A say-again replaces the resume of the line it replays: the whole line
    plays instead of its rest. A queued resume is
    filed T with ``said reason=again``; a playing one gets a stop aimed at its utt
    alone, so no line of any other utt is cut. Both are quiet (``notice._decide``).
    Returns ``[{"utt", "as": "retracted"|"stopped"}]``."""
    from .box import Queue, public
    from .player import _done, said_unplayed

    out = []
    q = Queue(d)
    for x in q.lines():
        if x.get("resume_of") == utt and q.drop_line(x, "T"):
            item = public(x)
            _done(d, item, said_unplayed(item, "again"))
            out.append({"utt": item["utt"], "as": "retracted"})
    cur = _playing(d)
    if cur and cur.get("resume_of") == utt and cur.get("utt"):
        _stop_file(d, {"t": time.time(), "reason": "again", "flush": False, "utt": cur["utt"]})
        out.append({"utt": cur["utt"], "as": "stopped"})
    return out


def stop(reason: str = "stop", *, flush: bool = True, d: Optional[Path] = None) -> dict:
    """Cut what is playing (and, with flush, what is queued) within 50 ms."""
    return _stop_file(d or mouth_dir(), {"t": time.time(), "reason": reason, "flush": flush})


def _rank_of(p) -> float:
    """A line's priority as the queue ranks it (``box.RANKS``; lower plays sooner)."""
    from .box import DEFAULT_RANK, RANKS

    if p is None:
        return DEFAULT_RANK
    if p in RANKS:
        return RANKS[p]
    try:
        return float(p)
    except (TypeError, ValueError):
        return DEFAULT_RANK


def amend(utt: str, text: str, *, d: Optional[Path] = None) -> dict:
    """Rewrite a line not yet heard, keeping its place (the queue's ``Supersedes:``).

    A queued line, held for the user's turn or not, is replaced where it stands. A line
    the player has taken but not yet made audible (its synthesis, the lead hold,
    the device opening: ``playing.json`` with no ``first_frame_t``) is
    cut unspoken (``said reason=amended``) and the new text plays next, in its
    place. Refuses a line being heard (retract it instead) or already gone.
    """
    d = d or mouth_dir()
    if not text.strip():
        raise ValueError("mouth: nothing to say; retract it instead")
    from .box import Queue, asked_no_hold, drop, public

    q = Queue(d)
    x = q.find(utt)
    taken = False
    if x is None:
        cur = _playing(d)
        if not (cur and cur.get("utt") == utt and cur.get("mail_id")):
            raise ValueError(f"mouth: no queued line {utt}")
        if cur.get("first_frame_t") is not None:
            raise ValueError(f"mouth: {utt} is being heard now; retract it and say again")
        x, taken = cur, True
    # A new mail that supersedes the line: it takes the line's place and utt.
    # If the player makes the old one audible first, this plays after it, as news.
    item = public(x)
    # a taken line is playing.json's, public keys only: its mail in cur/ says it
    unheld = x.get("_unheld") or (taken and asked_no_hold(x.get("box_file")))
    if unheld and not item.get("hold"):
        item["hold"] = "now"   # its sender's --hold none / X-Mouth-When: now rides the new mail
    target = item.pop("mail_id")   # the line's mail_from stays: an amend keeps its From
    if taken:
        item.pop("box_file", None)   # playing.json's own fact, not the line's
        if _rank_of(item.get("priority")) > 0:
            item["priority"] = "next"   # its place was the front: a plain line queued since waits;
            # a --next or --now line queued while it rendered still goes first
    item["amended_from"] = x.get("amended_from") or x["text"]
    item["text"] = text
    drop(q.bx, item, supersedes=target)
    return item


def retract(utt: str, *, d: Optional[Path] = None) -> dict:
    """Drop a queued line, or cut it if it is playing. Its ``said`` says ``retracted``."""
    d = d or mouth_dir()
    from .box import Queue, public
    from .player import _done, said_unplayed

    q = Queue(d)
    x = q.find(utt)
    if x is not None and q.drop_line(x, "T"):
        # a retract by the line's own session is quiet (notice.py);
        # this caller gets the said record on stdout
        item = {**public(x), "retracted_by": heard.caller_session()}
        rec = said_unplayed(item, "retracted")
        _done(d, item, rec)
        return rec
    cur = _playing(d)
    if cur and cur.get("utt") == utt:
        return _stop_file(d, {"t": time.time(), "reason": "retracted", "flush": False, "utt": utt})
    from . import bed

    if any(b.get("utt") == utt for b in bed.read(d)):   # a played file, on its own lane
        return _stop_file(d, {"t": time.time(), "reason": "retracted", "flush": False, "utt": utt})
    raise ValueError(f"mouth: no queued or playing line {utt}")


def status(d: Optional[Path] = None) -> dict:
    d = d or mouth_dir()
    playing = None
    try:
        p = json.loads((d / "playing.json").read_text())
        playing = {"utt": p["utt"], "text": p["text"][:80]}
        if p.get("volume") is not None:  # the level it plays at
            playing.update(volume=p["volume"], volume_from=p.get("volume_from"))
        from .hold import line_barge_words

        n, given = line_barge_words(p)
        if given:  # its own --barge-words/--no-barge: the model's layer
            playing.update(barge_words="off" if n is None else n, barge_words_from="model")
    except (FileNotFoundError, ValueError):
        pass
    pid = (d / "player.pid").read_text().strip() if (d / "player.pid").exists() else None
    from .box import Queue

    q = Queue(d)
    running = _player_running(d) if d.exists() else False
    from . import duckswitch, hold, level, splitwait

    dk = duckswitch.readout(d)
    queued = q.lines()
    return {"dir": str(d), "box": str(q.bx), "player": running,
            "pid": pid, "queued": len(queued), "playing": playing,
            "music": _music(d) if running else [],
            # the splits running on this host, and the plays that wait on each
            "splits": splitwait.running(d, queued),
            "duck": {k: dk[k] for k in ("effective", "by", "override")},
            # the host's barge policy, each value with its layer and source
            "barge": hold.barge_status(d),
            # false: no ffmpeg, so no voice can be measured and levelling is off
            "level": {"ffmpeg": level.have_ffmpeg()}}


def duck_state(d: Optional[Path] = None, now: Optional[float] = None) -> dict:
    """``mouth duck``'s readout: the duck now and who set
    it, the override and its time left, the setting under it, and each playing
    bed's own answer (a ``--duck``/``--no-duck`` file keeps its argument)."""
    from . import bed, duckswitch

    d = d or mouth_dir()
    out = duckswitch.readout(d, now)
    rows = []
    if d.exists() and _player_running(d):     # bed.json outlives a player that died
        for b in bed.read(d):
            rows.append({"utt": b.get("utt"), "device": b.get("device"),
                         "duck_from": bed._from(b), "duck_by": b.get("duck_by"),
                         "dipping": bool(b.get("ducked"))})
    out["beds"] = rows
    return out


def barge_state(d: Optional[Path] = None, now: Optional[float] = None) -> dict:
    """``mouth barge``'s readout: the host's barge
    policy now, each value with its ``from`` and ``by``, and the override with
    its ``remaining_s``: the same as ``mouth status``'s ``barge`` key. A line's
    own ``--barge-words`` still beats the override's N; ``mouth status`` shows
    it on ``playing``."""
    from . import hold

    return hold.barge_status(d or mouth_dir(), now)


def _music(d: Path) -> list:
    """What each bed plays: one row per device."""
    from . import bed

    now, out = time.time(), []
    for b in bed.read(d):
        row = {"device": b.get("device"), "utt": b.get("utt"), "file": b.get("file"),
               "text": str(b.get("text") or "")[:80],
               # file is what plays: the instrumental, when source_file names the original
               "source_file": b.get("source_file"), "instrumental_from": b.get("instrumental_from"),
               "stem": b.get("stem"),   # --stem: the stems that play, mixed in one ffmpeg
               "played_s": round(now - b["started_t"], 1) if b.get("started_t") else None,
               "volume": b.get("volume"), "volume_from": b.get("volume_from")}
        dk = b.get("duck")
        if isinstance(dk, dict):
            row["duck"] = {"on": dk.get("on"), "level": dk.get("level")}
        row["ducked"] = bool(b.get("ducked"))   # dipped right now, under a line or the user's voice
        out.append(row)
    return out
