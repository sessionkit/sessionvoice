"""mouth - the speaking half of the ears: ``mouth say TEXT`` returns at once.

The complement to ``ears.listen`` (the ears), built new and small: a
little thing to speak back with, a stack of lines and a pluggable
backend.

It replaces nothing yet; converse still speaks.

- ``say`` queues and returns; ONE resident player speaks, in playlist
  order, so stacked openers come for free. The queue is a maildir (box.py):
  a line is a mail in ``new/``, done is ``cur/`` with a flag.
- It speaks to ONE named device, exact name: ``--device``, else
  ``$MOUTH_DEVICE``, else ``output`` in the host settings file
  (``settings_device``); with none of them, ``say`` refuses the line. A named
  device that is absent gets a ``said reason=device-absent``. Never the
  default by accident: ``default`` plays only when one of the three says it.
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
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from ears import envnames, heard

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
    """Start a player unless one holds the lock. True if one was started."""
    d = d or mouth_dir()
    d.mkdir(parents=True, exist_ok=True)
    if _player_running(d):
        return False
    import subprocess  # here, not at the top: the timeline reader never spawns

    env = {**os.environ, "MOUTH_DIR": str(d)}
    with open(d / "player.log", "ab") as log:
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
    """The host settings file's ``output``: ``say``'s device when
    neither ``--device`` nor ``$MOUTH_DEVICE`` names one.

    The file is ``voices.settings_path(d)``. An absent file, an absent key, or
    an empty or non-string value is unset: ``None``, so ``say`` still refuses.
    The schema's documented ``default`` is never applied here; an ``output``
    of ``"default"`` written in the file is honoured as written."""
    out = _voices.settings(d).get("output")
    return out if isinstance(out, str) and out.strip() else None


def say(text: str, *, voice: Optional[str] = None, speed: Optional[float] = None,
        backend: Optional[str] = None, device: Optional[str] = None,
        pan=None, log_dir: Optional[Path] = None, wait: bool = False, timeout: float = 300.0,
        priority: Optional[str] = None, d: Optional[Path] = None, spawn: bool = True,
        extra: Optional[dict] = None, hold: Optional[str] = None,
        expires_s: Optional[float] = None, interest: Optional[str] = None,
        in_reply_to: Optional[str] = None, volume=None, duck: Optional[bool] = None,
        duck_level=None, normalize: Optional[bool] = None, lead_s=None,
        barge_words=None) -> dict:
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
    caller's cascade, the host file, 0: play as it streams. 2.5 rides out a
    clone rendering below real time. A bad one is refused before anything is
    written.

    ``barge_words``: this line's own barge count, the model's
    layer (``mouth say --barge-words N|off``, ``--no-barge``): a whole number of
    1 or more, or ``off`` (no cut on a word count; the user's voice still ducks it and
    a stop ask still cuts it). It beats the user's ``barge_words`` setting and
    the default (``hold.barge_policy``). It rides the line as ``barge_words``
    and ``barge_words_from: flag``, and a resume carries it. A bad one is
    refused before anything is written.

    ``log_dir``: where this line's heard rows go. It rides the mail as
    ``X-Mouth-Log-Dir``, and the player honours it only under its log root
    (``box.heard_root``), so one outside the root is refused here, before anything
    is written, rather than ignored at play.
    """
    priority = _priority(priority)
    from .hold import HOLDS

    if hold not in (None, *HOLDS):
        raise ValueError("mouth: hold is " + " or ".join(HOLDS))
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
    device = device or envnames.get("MOUTH_DEVICE") or settings_device(d)
    if not device:
        raise ValueError("mouth: no device. Pass --device NAME, set MOUTH_DEVICE, or set "
                         f"`output` in {_voices.settings_path(d)} ('null' plays nothing; 'default' "
                         "is the system default, by name only)")
    if not text.strip():
        raise ValueError("mouth: nothing to say")
    d = d or mouth_dir()
    if rid:  # the parent is read before anything is written; never an error
        parent = reply.find_parent(rid)
        hdrs = reply.reply_headers(rid, parent)
    d.mkdir(parents=True, exist_ok=True)
    now = time.time()
    sid = heard.caller_session()
    is_file = backend == "file"
    # ONE `session settings` run serves the voice, the level and the duck
    # A resume (the player's own, ``resume_of``) reads none: the player's session is
    # whoever spawned it, and the cut line carries what its caller answered.
    # A say-again replay (``again_of``) is the same: its caller is the keeper.
    resuming = bool((extra or {}).get("resume_of") or (extra or {}).get("again_of"))
    need = not resuming and (voice is None or volume is None or is_file or normalize is None
                             or lead_s is None)
    cascaded = _voices.cascade(sid, _voices.LINE_KEYS) if need else {}
    level, level_from = _voices.volume_setting(file=is_file, flag=volume, cascaded=cascaded, d=d)
    item = {
        "utt": uuid.uuid4().hex[:12],
        "text": text,
        # the caller's cascade and env; a resume is the player's own, so
        # it takes the box's order: the player's env never beats the host file
        "voice": voice or default_voice(d, sid, cascaded, caller=not resuming)[0],
        "speed": speed,
        "backend": backend or envnames.get("MOUTH_BACKEND") or "auto",
        "device": device,
        "pan": pan_value(pan),
        "requested_t": now,
        "requested_ts": datetime.fromtimestamp(now).astimezone().isoformat(timespec="milliseconds"),
        "agent": heard.caller_agent(),
        "session": sid,
        "log_dir": str(log_dir) if log_dir else envnames.get("MOUTH_LOG_DIR"),
        "wait": wait,
        "priority": priority,
        "hold": hold,
        "interest": interest,
        "expires_s": expires_s,
        "expires_t": (now + expires_s) if expires_s is not None else None,
        **(extra or {}),
    }
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
                                                         "requested_ts", "file", "mail_id")})
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
        if spawn:
            ensure_player(d)
        if not wait:
            return item
        done = d / "done" / f"{item['utt']}.json"
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                rec = _take_done(done)
                if rec is not None:
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


def play(source: str, *, start: Optional[float] = None, end: Optional[float] = None,
         extra_tag: Optional[dict] = None, **kw) -> dict:
    """Queue a sound file (or ``start``..``end`` seconds of it). Same queue, stop, log.

    A URL is refused before anything is queued: the decoder reads files only
    (``-protocol_whitelist file``)."""
    if "://" in source:
        raise ValueError("mouth: a URL is not played; download it, then mouth play the file")
    path = Path(source).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"mouth: no such file {source}")
    source = str(path)
    if start is not None and end is not None and end <= start:
        raise ValueError("mouth: --end must be after --start")
    return say(sound_label(source, start, end), backend="file",
               extra={"file": source, "start": start, "end": end, **(extra_tag or {})}, **kw)


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

    The device is ``say()``'s own resolution, as for a new line. Raises
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
                      "agent": note.get("agent"), "session": note.get("session")})
        if note.get("backend"):
            # The backend that made the WAV. The box makes any line with a ``file`` the
            # ``file`` backend (box.read), so without this the player would level the
            # replay under ``file``, the decoder, and the rows would lose the original's.
            extra["backend_asked"] = note["backend"]
        say_text, say_voice = noted if noted.strip() else given, note.get("voice") or voice
        speed, backend = note.get("speed"), note.get("backend")
    elif noted.strip():   # rung 2: the note's text, in the note's voice
        extra.update({"again_from": "text", "again_why": why,
                      "agent": note.get("agent"), "session": note.get("session")})
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
    from .box import Queue, drop, public

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
    target = item.pop("mail_id")
    item.pop("mail_from", None)
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
    from . import duckswitch, hold

    dk = duckswitch.readout(d)
    return {"dir": str(d), "box": str(q.bx), "player": running,
            "pid": pid, "queued": len(q.lines()), "playing": playing,
            "music": _music(d) if running else [],
            "duck": {k: dk[k] for k in ("effective", "by", "override")},
            # the host's barge policy, each value with its layer and source
            "barge": hold.barge_status(d)}


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
               "played_s": round(now - b["started_t"], 1) if b.get("started_t") else None,
               "volume": b.get("volume"), "volume_from": b.get("volume_from")}
        dk = b.get("duck")
        if isinstance(dk, dict):
            row["duck"] = {"on": dk.get("on"), "level": dk.get("level")}
        row["ducked"] = bool(b.get("ducked"))   # dipped right now, under a line or the user's voice
        out.append(row)
    return out
