"""hold - a line that waits for the end of the user's turn (stacked openers).

The mouth fills while the user talks and speaks when they finish: an agent
queues an opener while the user is still
talking, supersedes it as its answer improves, and the mouth speaks the
latest version the moment the user's turn ends.

A line with ``hold="turn-end"`` stays in the queue, keeps its place, and is
synthesised ahead so it starts at once, until the floor is free:

- **speaking:** the newest mic record in the ears' heard log is a
  ``partial`` with words in it (the user is mid-turn).
- **free:** the newest mic record is a ``turn`` (the user's turn has ended), a
  ``turn-discarded`` or ``listen-stopped`` event (speech ended with no words,
  or the ears stopped), or
  there has been no mic record for ``idle_s`` (the ears died, or nobody is
  talking), or there is no heard log at all.

Measured over 1,004 turns (two days of heard logs): the ``turn`` record
lands 1.46 s after the last ``partial`` at p50, 3.72 s at p99, 7.8 s at
worst; partials inside a turn are 3.39 s apart at p99. So the ``turn``
record is the release, and ``idle_s`` (8 s) is only the safety net.

An opener that arrives after the user's turn has ended plays once ``grace_s``
has passed since it ended (0 by default: the ears' 2 s silence
rule is the beat; $MOUTH_HOLD_GRACE_S sets one); a ``now`` line
skips it. Lines queued behind a held one are never blocked.
``barge()`` is the other half: the user's words during a line cut it. One that arrives while the user talks again waits for that turn.
``expires_s`` drops a line that has lost its moment: its ``said`` says
``expired`` and it is never spoken. For a held line the clock starts when
the user's turn ENDS, not when the line was queued (an opener queued 30 s into a
90 s turn must not die before the user stops).

**Stale**: a held line queued
before the user's current turn BEGAN is old news once they start talking again.
It is kept (filed P) and never spoken, its
``said`` says ``stale``.

**Audio time, not arrival**: a record's ``ts`` is when the ears
WROTE it. When the recogniser falls behind, words arrive late (64-108 s on
10-07) and a backlog lands in one burst. So what is about when the user SPOKE reads
the record's audio time (`_heard_t`): barge-in counts a partial only if the user said
it after the line began AND the recogniser delivered it within
:data:`BARGE_FRESH_S`; the user's turn begins, and the floor ages, by audio time. What
is about when the system KNEW (the label waits, the newest-record gate, the
grace beat, the stop's ``t``) stays on arrival.
"""

from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from ears import envnames, heard, lag, operator_config

HOLDS = ("turn-end",)
IDLE_S = 8.0
GRACE_S = 0.0   # the ears' 2 s silence rule already IS the beat;
                # 0.7 s on top measured +0.7 s per opener
_TAIL = 64 * 1024
# Events that end speech without a turn record: a blip with no words
# ("no text recognised", ~0.3 s; 46 in those two days), or the ears stopping.
_ENDS = ("turn-discarded", "listen-stopped")
_cache: dict = {"t": 0.0, "v": None}  # v = (newest floor record, turn start t, last end t)
_caches: dict = {("mic",): _cache}     # one per set of ears (sources_for)

MIC = ("mic",)


def sources_for(device: Optional[str]) -> tuple:
    """Whose ears decide the floor for a line on ``device``. A line into a call
    (``call:<N>``) listens to the call as well as the room: on the phone, the user's
    words arrive as ``source: "call"`` (from the Delta bot). Every other device keeps the
    room mic alone, as before - someone else's call must not hold the user's AirPods."""
    d = str(device or "")
    return ("mic", "call") if d == "call" or d.startswith("call:") else MIC


def barge_sources_for(device: Optional[str]) -> tuple:
    """Whose words may CUT a line on ``device``. A call line is cut only by the
    call: the phone cancels our echo there, but the room mic hears the call
    played aloud (Delta open on the Mac) and writes our own line down as the user's.
    Measured: the mic's copy of a call line cut that line
    13.7 s in. Every other device: the room mic, as before."""
    d = str(device or "")
    return ("call",) if d == "call" or d.startswith("call:") else MIC


def idle_s() -> float:
    return float(envnames.get("MOUTH_HOLD_IDLE_S", IDLE_S))


def _newest_mic(directory: Optional[Path] = None, sources: tuple = MIC) -> Optional[dict]:
    """The newest mic record that moves the floor, today's log then yesterday's."""
    return _scan(directory, sources)[0]


def _is_end(rec: dict) -> bool:
    return rec.get("kind") == "turn" or (rec.get("kind") == "event" and rec.get("event") in _ENDS)


def _scan(directory: Optional[Path] = None, sources: tuple = MIC) -> tuple:
    """(the newest floor-moving mic record, when the user's current turn began, when
    the user's last turn ended, when the newest record was SAID). Begin is the first
    worded partial after the last end, and only while the newest record is a
    partial; any of the times may be None.

    Begin and the newest record's time are AUDIO times (`_heard_t`), so a
    backlog the recogniser flushes late neither reads as the user
    speaking now nor files a held line ``stale`` against a turn the user began a
    minute ago. They fall back to arrival (``ts``) for a record with no audio
    time. The end, and the pause test inside the beat, stay on arrival: they
    are about when the system KNEW the user's turn was over."""
    newest, start, end_t = None, None, None          # start: (rec, lines, index)
    newest_at: Optional[tuple] = None
    for path in reversed(heard.log_files(directory)[-2:]):
        try:
            with open(path, "rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - _TAIL))
                lines = f.read().split(b"\n")
        except OSError:
            continue
        for i in range(len(lines) - 2, -1, -1):  # the last piece is empty or a write in flight
            try:
                rec = json.loads(lines[i])
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(rec, dict) or rec.get("source", "mic") not in sources:
                continue
            if rec.get("kind") == "partial" and str(rec.get("text") or "").strip():
                if newest is None:
                    newest, newest_at = rec, (lines, i)
                start = (rec, lines, i) if _ts(rec) is not None else start
                continue
            if _is_end(rec):
                if newest is None:
                    return rec, None, _ts(rec), _ts(rec)
                end_t = _ts(rec) if end_t is None else end_t
                # A pause inside the beat is the same turn (the user went on before
                # a held line could speak), so the turn began before it.
                start_arr = _ts(start[0]) if start is not None else None
                gap = (start_arr - _ts(rec)) if start_arr is not None and _ts(rec) is not None else None
                if gap is not None and gap < grace_s():
                    continue
                return _scanned(newest, newest_at, start, end_t)
    return _scanned(newest, newest_at, start, end_t)


def _scanned(newest: Optional[dict], newest_at: Optional[tuple], start: Optional[tuple],
             end_t: Optional[float]) -> tuple:
    """`_scan`'s answer: the begin and the newest record resolved to audio time."""
    def said(rec: dict, lines: list, i: int) -> Optional[float]:
        key = _start_key(rec)          # an unstamped record: look up its capture's start
        starts = {} if lag.audio_t(rec) is not None else {key: _start_before(lines, i, key)}
        t = _heard_t(rec, starts)
        return t if t is not None else _ts(rec)
    start_t = said(*start) if start is not None else None
    newest_t = said(newest, *newest_at) if newest is not None and newest_at is not None else None
    return newest, start_t, end_t, newest_t


def _ts(rec: dict) -> Optional[float]:
    try:
        return datetime.fromisoformat(rec["ts"]).timestamp()
    except (KeyError, TypeError, ValueError):
        return None


def _ms(t: float) -> int:
    """``t`` at heard's resolution: whole epoch milliseconds, floored.

    The ears stamp ``ts`` and ``audio_ts`` to the millisecond, floored
    (``isoformat(timespec="milliseconds")`` truncates), but a line's first frame
    is ``time.time()``, a full float. Compared as they stand, a partial written in
    the same millisecond as the first frame, and after it, stamps ``.859`` against
    ``.859115`` and reads as said before the line (measured exactly
    that). So every compare against ``since_t`` floors BOTH ends to the ms and
    counts an equal one: a partial in the first frame's own millisecond counts,
    which also counts one written up to 1 ms before the frame (the stamp cannot
    tell them apart, and 1 ms is far below anything a person says). The inner
    ``round`` drops the float error of a parsed stamp (``.859`` parses to
    ``.85899999...``), which would otherwise floor to the ms below."""
    return math.floor(round(t * 1000, 3))


# -- audio time: when the user SAID it, not when the ears wrote it ------------------
#: The freshness bound on barge-in:
#: a partial counts toward a cut only if the
#: recogniser wrote it within this many seconds of its audio (``ts`` minus its
#: audio time, the lag AT THE WRITE, fixed per record, so every poll weighs it
#: the same). Healthy lag is 0.05-1.2 s (10-07) and the barge-in budget is
#: about 4 s from the user's first word to the cut, so a partial later than this could
#: never have cut in time anyway; 10-07's backlog was 64-108 s late. It sits
#: above the ears' lag alarm (`ears.lag.LAG_WARN_S`, 2 s), so every word dropped
#: here for lateness comes after a visible alarm. $MOUTH_BARGE_FRESH_S
#: sets it.
BARGE_FRESH_S = 4.0
_STARTED = heard.EV_LISTEN_STARTED.encode()


def barge_fresh_s() -> float:
    """$MOUTH_BARGE_FRESH_S, else :data:`BARGE_FRESH_S`."""
    try:
        return float(envnames.get("MOUTH_BARGE_FRESH_S", BARGE_FRESH_S))
    except ValueError:
        return BARGE_FRESH_S


def _start_key(rec: dict) -> tuple:
    """Which capture a record came from: its ``source`` and ``device``. Only
    ``listen-started`` carries a pid, so the join is the newest start of the same
    key before the record in the log."""
    return rec.get("source", "mic"), rec.get("device")


def _is_started(rec: dict) -> bool:
    return rec.get("kind") == "event" and rec.get("event") == heard.EV_LISTEN_STARTED


def _heard_t(rec: dict, starts: Optional[dict] = None) -> Optional[float]:
    """When the END of the record's audio was spoken, as epoch seconds, or None.

    1. its own ``audio_ts``, which newer ears stamp;
    2. else ``start + t1``: ``start`` the ``ts`` of the newest ``listen-started``
       of the same capture (`_start_key`) before it, from ``starts``, and ``t1``
       its stream seconds (old ears, as on 10-07);
    3. else None, and the caller falls back to arrival (``ts``): today's
       behaviour, never worse. That is the case for an old-ears record whose
       ``listen-started`` lies beyond the 64 KB tail (for the floor, `_scan`,
       beyond today's tail: a capture begun yesterday). Accepted: every record
       the ears write now carries its own stamp, case 1.

    The END (``t1``) and not the start: it errs toward "after the line began"
    and toward "fresh", the side that never loses a real barge-in."""
    t = lag.audio_t(rec)
    if t is not None:
        return t
    start = (starts or {}).get(_start_key(rec))
    t1 = rec.get("t1")
    if start is None or isinstance(t1, bool) or not isinstance(t1, (int, float)):
        return None
    return start + float(t1)


def _start_before(lines: list, i: int, key: tuple) -> Optional[float]:
    """The ``ts`` of the newest ``listen-started`` of capture ``key`` above
    ``lines[i]``, or None. Parses only the lines that name the event."""
    for j in range(i - 1, -1, -1):
        if _STARTED not in lines[j]:
            continue
        try:
            rec = json.loads(lines[j])
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(rec, dict) and _is_started(rec) and _start_key(rec) == key:
            return _ts(rec)
    return None


def _look(directory: Optional[Path] = None, now: Optional[float] = None,
          max_age_s: float = 0.1, sources: tuple = MIC) -> tuple[Optional[dict], float]:
    """The newest floor-moving mic record, and ``now``; cached for ``max_age_s``."""
    rec, _, _, _, now = _look_floor(directory, now, max_age_s, sources)
    return rec, now


def _look_floor(directory: Optional[Path] = None, now: Optional[float] = None,
                max_age_s: float = 0.1, sources: tuple = MIC) -> tuple:
    """(newest floor record, turn start t, last end t, newest record's audio t,
    now): `_scan`, cached for ``max_age_s``."""
    now = time.time() if now is None else now
    cache = _caches.setdefault(tuple(sources), {"t": 0.0, "v": None})
    if directory is None and cache["v"] is not None and now - cache["t"] < max_age_s:
        return (*cache["v"], now)
    v = _scan(directory, tuple(sources))
    if directory is None:
        cache.update(t=now, v=v)
    return (*v, now)


def floor(directory: Optional[Path] = None, now: Optional[float] = None,
          max_age_s: float = 0.1, sources: tuple = MIC) -> str:
    """``speaking`` or ``free`` (see the module doc). Cached for ``max_age_s``.
    The newest partial's age is from when the user SAID it (`_scan`), so a backlog
    flushed late does not hold the floor for ``idle_s``."""
    rec, _, _, t, now = _look_floor(directory, now, max_age_s, sources)
    if rec is not None and rec.get("kind") == "partial":
        age = now - t if t is not None else 0.0
        return "speaking" if age < idle_s() else "free"
    return "free"


def grace_s() -> float:
    """The beat a held line waits after the user's turn ends. If they start again
    inside it, the line keeps holding."""
    return float(envnames.get("MOUTH_HOLD_GRACE_S", GRACE_S))


#: The ``detail`` of a resume's expiry because the user asked for quiet.
QUIET = "the user asked for quiet"
#: The ``detail`` of a resume's expiry because the user's turn answered the cut line.
ANSWERED = "the user answered it"


class Verdict(str):
    """A :func:`check` verdict that says why: it equals its plain word, so
    every ``== "expire"`` reads as before; ``detail`` is the said row's detail."""
    detail: Optional[str] = None


def _why(word: str, detail: str) -> Verdict:
    v = Verdict(word)
    v.detail = detail
    return v


def check(item: dict, now: Optional[float] = None, directory: Optional[Path] = None) -> str:
    """What the player should do with a queued item: ``play``, ``wait``, ``expire`` or ``stale``.
    A resume the user asked to be quiet expires with ``detail`` :data:`QUIET`; one their turn
    answered (:func:`answered`) with ``detail`` :data:`ANSWERED`."""
    now = time.time() if now is None else now
    ears = sources_for(item.get("device"))
    held = item.get("hold") == "turn-end"
    exp_s = item.get("expires_s")
    exp = item.get("expires_t")
    if (not held or exp_s is None) and exp is not None and now >= float(exp):
        return "expire"
    if held:
        # start_t is when the user BEGAN by audio time (`_scan`): a stale burst does not
        # make a line queued before it, but after the user's last real turn, ``stale``.
        _, start_t, end_t, _, _ = _look_floor(directory, now, sources=ears)
        if floor(directory, now, sources=ears) == "speaking":
            asked = item.get("requested_t")
            # an opener its sender held is old news once the user starts again; a line
            # held only by the room default (ROOM_HOLD) is not an opener, so it waits
            if asked is not None and start_t is not None and float(asked) < start_t \
                    and item.get("hold_from") != ROOM:
                return "stale"
            return "wait"
        if exp_s is not None:
            base = max(float(item.get("requested_t") or now), end_t or 0.0)
            if now >= base + float(exp_s):
                return "expire"
        rec, _ = _look(directory, now, sources=ears)
        t = _ts(rec) if rec is not None else None    # arrival: the end is when the system KNEW
        # The beat: a turn that ended less than grace_s ago might not be over.
        # ``now`` priority skips it (urgent lines do not wait a beat).
        if t is not None and rec.get("kind") != "partial" and item.get("priority") != "now" \
                and now - t < grace_s():
            return "wait"
        if item.get("resume_of") and item.get("requested_t") is not None and asked_for_quiet(
                float(item["requested_t"]), str(item.get("text") or ""), directory,
                barge_sources_for(item.get("device"))):
            return _why("expire", QUIET)     # a resume the user told to be quiet is not spoken
        if item.get("resume_of") and answered(item, directory, now) is not None:
            return _why("expire", ANSWERED)  # the user's turn was the answer: do not ask again
    return "play"


# -- a cut question the user answered is not replayed -------------------------------
# The user's words cut a line that asked them something, and those words WERE the
# answer; the replay then asked them the answered question again when their turn
# ended. The rule, N=2: at release, judged
# from the ``turn`` record the floor is freed by (the text of the user's whole turn), a
# resume is dropped when the sentence it was cut in ends in "?" and the user's turn has
# N or more content words. A stop ask, a filler-only reply, a blip (no turn
# text) and the user's echo of the line are not an answer, so those still resume.
#: Content words of the user's turn (not STOP_WORDS, not STOP_ASKS) that answer a cut
#: question. 2, the BARGE_WORDS floor: a turn that could cut a line can answer
#: it. ``resume_answer_words`` in the mouth's settings, else
#: $MOUTH_RESUME_ANSWER_WORDS (`resume_answer_words`).
RESUME_ANSWER_WORDS = 2
#: Only a cut QUESTION is dropped when the user answers it. Off
#: (``resume_answer_question: false``, or $MOUTH_RESUME_ANSWER_QUESTION
#: off) drops any cut line the user's turn answered (`resume_answer_question`).
RESUME_ANSWER_QUESTION = True
_OFF = ("off", "0", "no", "false")


def resume_answer_words(settings: Optional[dict] = None) -> int:
    """How many content words of the user's turn answer a cut line: the user's
    ``resume_answer_words`` setting (a whole number, 1 or more), else
    $MOUTH_RESUME_ANSWER_WORDS (the same), else
    :data:`RESUME_ANSWER_WORDS`. The same layering as ``barge_words``; an
    unreadable value is as if unset."""
    n = _setting_num(settings, "resume_answer_words", 1, whole=True)
    if n is not None:
        return int(n)
    try:
        n = int(str(envnames.get("MOUTH_RESUME_ANSWER_WORDS")).strip())
    except (TypeError, ValueError):
        return RESUME_ANSWER_WORDS
    return n if n >= 1 else RESUME_ANSWER_WORDS


#: A line on a ROOM device (any device that is not ``call`` or ``call:<N>``) that
#: asks no hold waits for the end of the user's turn, as ``--hold turn-end`` does:
#: a seat that forgets the flag never talks over the user. ``--now``, ``--hold none``
#: (``X-Mouth-When: now``) and a played file (``mouth play``) still play at once.
#: Off: ``room_hold: false`` in the mouth's settings, or $MOUTH_ROOM_HOLD=off.
ROOM_HOLD = True
#: The ``hold_from`` of a line held by :data:`ROOM_HOLD`, not by its sender.
ROOM = "room"
#: ``say(hold=...)`` / ``mouth say --hold none``: no hold, even on a room device.
#: It rides the mail as ``X-Mouth-When: now``, the word a call line already reads.
NO_HOLD = "none"


def room_hold(settings: Optional[dict] = None) -> tuple:
    """Whether a room line that asks no hold is held ``turn-end``, and the layer
    that said so: ``(on, "env"|"settings"|"default")``. The line's own ask (its
    ``--hold``, ``--now``, ``X-Mouth-When``) is the layer above all of these
    and is read by ``box.read``.

    $MOUTH_ROOM_HOLD (``off``, ``0``, ``no``, ``false`` turn it off; any other
    non-empty value is on), else ``room_hold`` (true or false) in the mouth's
    settings, else :data:`ROOM_HOLD`. An empty env or a non-boolean setting is
    as if unset."""
    env = envnames.get("MOUTH_ROOM_HOLD")
    if env is not None and env.strip():
        return env.strip().lower() not in _OFF, "env"
    v = _settings(settings).get("room_hold")
    if isinstance(v, bool):
        return v, "settings"
    return ROOM_HOLD, "default"


def resume_answer_question(settings: Optional[dict] = None) -> bool:
    """Whether only a cut QUESTION is dropped when the user's turn answers it: the
    user's ``resume_answer_question`` setting (true or false), else
    $MOUTH_RESUME_ANSWER_QUESTION (``off``, ``0``, ``no``, ``false``
    turn it off; anything else is on), else :data:`RESUME_ANSWER_QUESTION`."""
    v = _settings(settings).get("resume_answer_question")
    if isinstance(v, bool):
        return v
    env = envnames.get("MOUTH_RESUME_ANSWER_QUESTION")
    if env is not None:
        return env.strip().lower() not in _OFF
    return RESUME_ANSWER_QUESTION


#: A sentence's end: its stops, any closing quote or bracket, then a space or the
#: end. So the dots inside "0.2.0" or "1.0" do not end a sentence.
_SENTENCE_END = r"[.!?]+[\"')\]]*(?=\s|$)"


def cut_is_question(rest: str) -> bool:
    """Whether the sentence a line was cut in ends in "?". ``rest`` is a resume's
    text: the unsaid rest from the start of the cut sentence (`rest_of`), so the
    cut sentence is its FIRST. A rest with no sentence end is not a question."""
    import re
    m = re.search(_SENTENCE_END, rest or "")
    return m is not None and "?" in m.group(0)


def _could_be_heard(line: str, rest: str) -> str:
    """The part of a cut line the mic could have heard: the line up to the END of
    the sentence it was cut in (the rest's first). The sentences after it were
    never played, so the user's words that match them are the user's own, not echo: an answer
    that repeats words of a question's later sentence is theirs when the line was cut in its first
    sentence. ``line`` is the item's ``resume_text`` (the first 200 characters);
    if the cut sentence is not found in it, all of it counts, and with no line,
    the cut sentence alone."""
    import re
    m = re.search(_SENTENCE_END, rest or "")
    first = (rest[:m.end()] if m else rest or "").strip()
    if not line:
        return first
    j = line.rfind(first) if first else -1
    return line[:j + len(first)] if j >= 0 else line


#: Characters a second of speech, for a cut too early to measure its own rate:
#: the player's floor on a line's estimated length (``len(text) / 15.0``).
_CHARS_PER_S = 15.0
#: How far past a CUT line's played estimate the mic may have heard, in seconds:
#: the estimate's slack (it is a proportion of the audio). After a cut the audio
#: has stopped and ``played_s`` already has the device latency taken off, so this
#: is a word or so, not `AHEAD_S` (3 s), the margin for a line still PLAYING,
#: placed from frames written and an estimated length. 3 s reaches about 42
#: characters: from a cut two words into a question it would call the user's
#: answer, which repeated the question's words, the line's own echo (measured).
CUT_ECHO_AHEAD_S = 0.5


def _heard_window(played: str, played_s, rest: str) -> Optional[str]:
    """What the mic could have heard of a cut line, from its PLAYED estimate: the
    said row's ``text_played_est`` (``played``), and the next
    :data:`CUT_ECHO_AHEAD_S` of the line after it: the estimate is a proportion
    of the audio, and the playhead may be a little ahead of it (the forward
    margin of the barge echo window, `near`'s `AHEAD_S`, sized for a stopped
    line). The margin's characters are that at the played part's own rate
    (``len(played) / played_s``, the rate `near` uses), else at
    :data:`_CHARS_PER_S`, rounded out to a whole word. Everything before the
    playhead counts (no `BACK_S`): the record of the user's turn spans their whole turn.

    The unsaid continuation is read from ``rest`` (the resume's text, from the
    cut sentence's start, never truncated): the played part of that sentence
    is ``played`` after its last sentence end, as `rest_of` cut it. None when
    the estimate does not line up with the rest (the caller falls back to
    `_could_be_heard`)."""
    try:
        secs = float(played_s)
    except (TypeError, ValueError):
        return None
    played = str(played or "")
    start = max(played.rfind(c) for c in ".!?")
    tail = played[start + 1:].strip()
    rest = str(rest or "")
    if not rest.startswith(tail):
        return None
    ahead = rest[len(tail):]
    rate = len(played) / secs if played and secs > 0 else _CHARS_PER_S
    n = int(CUT_ECHO_AHEAD_S * rate)
    while n < len(ahead) and not ahead[n].isspace():   # out to the end of the word
        n += 1
    # cut on a sentence's end: the rest starts at the next one, with no space
    sep = " " if played and not tail and ahead[:1] and not ahead[:1].isspace() else ""
    return (played + sep + ahead[:n]).strip()


def answered(item: dict, directory: Optional[Path] = None,
             now: Optional[float] = None, settings: Optional[dict] = None) -> Optional[dict]:
    """The user's ``turn`` record that answered a resume's cut line, or None.

    The record is the one the floor is freed by: the newest floor-moving record
    of the ears whose words may cut the line (`barge_sources_for`), when it is a
    ``turn``. A blip (``turn-discarded``), the ears stopping, or an idle release
    on an old partial carry no turn text, and are no answer. It answers when the
    cut sentence is a question (`cut_is_question`; not asked when
    `resume_answer_question` is off), the turn is not the line's own echo
    (`is_echo`, against only the part the mic could have heard: the played
    estimate the item carries, ``resume_played`` and ``resume_played_s``, plus
    :data:`CUT_ECHO_AHEAD_S` (`_heard_window`); an item without them, queued before they were
    carried, falls back to the line up to the end of the cut sentence,
    `_could_be_heard`), and it holds `resume_answer_words` distinct content
    words: not STOP_WORDS, not STOP_ASKS."""
    if not item.get("resume_of"):
        return None
    rest = str(item.get("text") or "")
    s = _settings(settings)
    if resume_answer_question(s) and not cut_is_question(rest):
        return None
    rec, _ = _look(directory, now, sources=barge_sources_for(item.get("device")))
    if rec is None or rec.get("kind") != "turn":
        return None
    text = str(rec.get("text") or "")
    line = str(item.get("resume_text") or "")
    heard = None
    if "resume_played" in item:
        heard = _heard_window(item.get("resume_played"), item.get("resume_played_s"), rest)
    if heard is None:
        heard = _could_be_heard(line, rest)
    if is_echo(text, heard):
        return None
    content = {w for w in _words(text) if w not in STOP_WORDS and w not in STOP_ASKS}
    return rec if len(content) >= resume_answer_words(s) else None


def asked_for_quiet(since_t: float, line_text: str = "", directory: Optional[Path] = None,
                    sources: tuple = MIC) -> Optional[dict]:
    """The user's partial since ``since_t`` that asks for quiet (QUIET_ASKS), not the line's own
    echo, or None."""
    for p in _mic_partials_since(since_t, directory, sources):
        text = str(p.get("text") or "")
        if not is_echo(text, line_text) and QUIET_ASKS & set(_words(text)):
            return p
    return None


# -- barge-in on words --------------------------------------------------------
# Stop the mouth when the ears log a PARTIAL - whisper decoded words - not on
# voice activity, so road noise that trips the detector never cuts a line. A
# partial that is the line itself coming back through the mic is not a barge.
ECHO_RUN = 0.7     # share of a partial that must be ONE run of the line's words
BACK_S, AHEAD_S = 8.0, 3.0   # the stretch of the line the mic could be hearing now


def barge_on() -> bool:
    return envnames.get("MOUTH_BARGE", "partial").lower() not in ("off", "0", "no")


#: Barge-in only where the mic cannot hear the mouth: on the
#: MacBook speakers the mic hears the line GARBLED ("the echo canceler faced--
#: time use" for "the echo canceller FaceTime uses"), no word match survives
#: that, and two lines cut themselves. Headphones only, until a real echo
#: canceller shares the output as its reference.
#: $MOUTH_BARGE_DEVICES is a regex over the output device's name.
BARGE_DEVICES = r"airpods|headphone|headset|buds|beats"


def barge_device(name: Optional[str]) -> bool:
    """Whether a line on output NAME may be cut by the user's words. A call
    (``call:<N>``) always may: the phone cancels our echo, and
    `barge_sources_for` lets only the call's own words cut it, on the same
    word count as every line (`barge_words`). The device regex
    never matches a call's name, so without this a call line could
    not be cut."""
    import re
    if name is not None and (str(name) == "call" or str(name).startswith("call:")):
        return True
    pat = envnames.get("MOUTH_BARGE_DEVICES", BARGE_DEVICES)
    return bool(name) and re.search(pat, str(name), re.I) is not None


def _words(text: str) -> list:
    return [w for w in "".join(c.lower() if c.isalnum() else " " for c in text).split() if w]


def near(text: str, played_s: float, est_dur_s: float) -> str:
    """The part of a line around the playhead: what the mic could be hearing."""
    if not est_dur_s or est_dur_s <= 0:
        return text
    n = len(text)
    a = int(n * max(0.0, (played_s - BACK_S) / est_dur_s))
    b = int(n * min(1.0, (played_s + AHEAD_S) / est_dur_s))
    return text[a:max(b, a + 1)]


def _echo_words(text: str) -> list:
    """Words for echo matching: lowercase, punctuation gone, hyphens joined
    ("side-car" and whisper's "sidecar" are one word)."""
    return _words(str(text).replace("-", ""))


def _edit1(a: str, b: str) -> bool:
    """At most one letter inserted, deleted or changed ("named" / "name")."""
    if len(a) > len(b):
        a, b = b, a
    if len(b) - len(a) > 1:
        return False
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i + 1:] == b[i + 1:] if len(a) == len(b) else a[i:] == b[i + 1:]


def _in_line(w: str, vocab: set) -> bool:
    if w in vocab:
        return True
    return len(w) >= 4 and any(abs(len(v) - len(w)) <= 1 and _edit1(w, v) for v in vocab)


#: The bag rule: whisper retypes a
#: few words of a long line coming back ("named dave," -> "name, Dave."), which
#: breaks the run (62% for a 56-word turn) though 96% of its words are the
#: line's. So a record is ALSO echo when ECHO_BAG of its CONTENT words (not
#: STOP_WORDS) are the line's, one edit allowed per word - but only with
#: BAG_MIN content words, so a short reply ("yes, the side-car") is judged by
#: the run alone, and common words never count (see ``is_echo``).
ECHO_BAG = 0.85
BAG_MIN = 4


def is_echo(partial: str, line: str) -> bool:
    """The mic heard the mouth: most of the partial is ONE run of the line's
    words, or (with BAG_MIN content words) ECHO_BAG of its content words are
    the line's.

    Not a bag of ALL words (measured): against a whole 36 s
    line every common word is "in" it, so a turn of common words scored 3/3 as echo
    and the user's barge-in took 7.9 s. A run keeps the words' order, ``near()`` keeps
    it to what is being played now, and the bag rule counts content words only.
    heard-first (a separate tool) carries a copy: change both or neither.
    """
    p, l = _echo_words(partial), _echo_words(line)
    if not p or not l:
        return False
    best = 0
    for i in range(len(l)):
        for j in range(len(p)):
            k = 0
            while i + k < len(l) and j + k < len(p) and l[i + k] == p[j + k]:
                k += 1
            best = max(best, k)
    if len(p) == 1:
        return best == 1
    if best >= 2 and best / len(p) >= ECHO_RUN:
        return True
    content = [w for w in p if w not in STOP_WORDS]
    if len(content) < BAG_MIN:
        return False
    vocab = set(l)
    return sum(1 for w in content if _in_line(w, vocab)) / len(content) >= ECHO_BAG


#: The default for the ``barge_ignore_words`` setting (`barge_ignore_words()`,
#: the words barge-in never counts. ``is_echo``'s bag rule above
#: keeps using this constant directly; the setting never reaches the echo test.
#: Stop words are removed before the echo and barge counts, so only content
#: words decide: enough words that are not in the set.
#: Backchannels are in the set: a backchannel is not a stop (a
#: one-word backchannel once cut a line at 1.45 s).
STOP_WORDS = frozenset("""
a about above after again all am an and any are as at be been being below both
but by can could did do does doing down during each few for from further had
has have having he her here hers him his how i if in into is it its itself just
let me more most my myself no nor not now of off on once only or other our out
over own same she should so some such than that the their them then there these
they this those through to too under until up very was we were what when where
which while who whom why will with would you your yours yourself d ll m re s t ve
yeah yes yep yup ok okay mm mmm mhm hmm uh um ah oh huh right sure cool nice great
fantastic brilliant awesome lovely good wow thanks thank really totally exactly
indeed got see well like
""".split())
#: Words that ask the mouth to stop, whatever else was said. The match is exact
#: on whole words, so an ask's inflections are listed, not stemmed (unlisted,
#: an "interrupting" was no stop ask, and the line ran ~3 s on). Only
#: "interrupt" is inflected: it means the conversation
#: in every form, while "holding", "waiting", "hanging" or "stopped" are
#: ordinary speech ("I'm holding the baby") and would cut lines the user never meant
#: to. A stem or prefix rule would also catch "waiter", "holder", "hangar".
STOP_ASKS = frozenset("stop wait hang hold pause shush quiet enough interrupt "
                      "interrupts interrupting interrupted".split())
#: Of those, the words that end a line, not pause it: a line cut by them is not resumed.
#: "hang on" and "wait" pause - the rest comes back after the user's turn (the fast follow) - but
#: "can you please be quiet" ends it (otherwise a cut greeting comes back whole
#: after the user asked for quiet).
QUIET_ASKS = frozenset("quiet shush hush shut stop silence".split())
#: The user's content words (not STOP_WORDS, not the line's own) that cut a line, on
#: a call or off it: the user can say a little without the line stopping.
#:
#: It was 3; now 2, with STOP_WORDS unchanged. At 3, two measured
#: barge-ins on AirPods took 5.3 s and 2.5 s from the user's voice onset to the
#: counting partial; at 2 they take 3.0 s and 1.4 s.
#:
#: Over 12 days of lines (5,525 replayed) 2 words cuts 119 more lines
#: than 3, about 17 of them unwanted by hand reading, 1 line in ~325: the
#: fewest of the options measured. `barge_words` in the mouth's settings
#: or $MOUTH_BARGE_WORDS tunes it (`barge_words()`).
BARGE_WORDS = 2
#: On calls too: one policy
#: everywhere, a sound ducks, N words cut. It supersedes an older one-word
#: call rule, which
#: stood in for a call duck that did not exist: three content words of mostly
#: filler took 6.3 s and 9.6 s to cut a call line, and nothing answered the user's
#: voice sooner. A call line now ducks at the call's own onsets (CallOut.duck,
#: onset.for_line), and at one word the STT's phantoms cut it ("Shell." three
#: times on one call). Only the call's own words cut a call line
#: (`barge_sources_for`). $MOUTH_BARGE_WORDS_CALL is retired and ignored:
#: it is not honoured, and nothing warns about it any more.
#: RETIRED_ENV maps a retired barge variable to the warning `barge_warnings`
#: gives when it is set. It is empty today: the runtime-neutral cleanup
#: dropped the CALL one's warning, whose text carried a tracker id and a date.
RETIRED_ENV: dict = {}
RESUME_S = 20.0   # a cut line's unplayed rest waits this long for the user's turn to end

# -- who may barge in ----------------------------------------------------------
# Whose words may cut a line: anyone's, or only named speakers' (so a
# television need not).
# Road noise has cut call lines: Silero scored it
# .95-.98 and the STT gave it words, so no VAD threshold can tell it from the user.
#
# ``barge_in`` in the mouth's settings (``~/.sessionkit/voice/settings/mouth.json``):
#
# - ``"anyone"`` (the default with no operator configured, see below, and
#   anything unreadable): any words that are not the line's own may cut it.
# - a list of names, ``["alice"]``: a partial cuts only once the speaker
#   side-car (ears ``speakers/``) has named it as one of them. ``other``,
#   ``silent`` and ``mixed`` never cut, and nor does a stop word in them.
#   ``unheard`` (too little audio to score: a quiet voice) is nobody's name
#   and counts as an unnamed one does, below. ``[]`` lets nobody cut.
# - per place, ``{"room": ["alice"], "call": "anyone"}``: ``room`` is every
#   output that is not a call; a place left out is ``anyone``.
#
# The side-car names a partial 0.43 s after it at p50, 1.35 s at p99
# (15,914 partials over two days). A partial still unnamed after LABEL_WAIT_S is
# one nobody is naming (the side-car is down, or it does not hear that
# source: today it labels the room mic only, never a call). It cuts as if the
# setting were ``anyone``, so a dead side-car cannot take the user's barge-in away.
# A partial the side-car labels ``unheard`` is the same case, told at once: it
# counts as if ``anyone`` without the wait, so a quiet voice cannot take the
# user's barge-in away either. Its trace entry keeps ``speaker: unheard``.
#
# The default when the key is absent: when
# ``session operator`` names an operator, it is ``{"room": [<mailbox>]}``, the
# operator's MAILBOX read as a speaker name, so enroll under your mailbox name.
# Room only: a call is left out, so it stays ``anyone`` and never waits
# LABEL_WAIT_S for a label the side-car does not give calls. Only the
# settings-only ``mailbox`` field counts, never ``operator`` (which
# AGENT_OPERATOR_ADDR may move): an env var never picks who may cut a line.
# Unconfigured (no operator block, no ``session``, the reader off) it is
# ``anyone``, as before. The key present, with ANY value, is explicit and wins,
# ``"anyone"`` included: that is how a configured user keeps today's behaviour.
ANYONE = "anyone"
UNHEARD = "unheard"   # the side-car's label for a partial it could not score
LABEL_WAIT_S = 2.0
_SPEAKERS_TAIL = 128 * 1024


def _who_list(v) -> Optional[frozenset]:
    if isinstance(v, str):
        v = v.strip().lower()
        return None if v in ("", ANYONE, "*") else frozenset({v})
    if isinstance(v, list) and all(isinstance(x, str) for x in v):
        return frozenset(x.strip().lower() for x in v if x.strip())
    return None


def _barge_in_value(settings: Optional[dict]) -> tuple:
    """``(value, by)``: the raw ``barge_in`` and where it came from, ``setting``
    (the key present, any value), ``operator`` (absent, the operator's mailbox)
    or ``builtin`` (absent, unconfigured: anyone)."""
    if settings is None:
        from . import voices
        settings = voices.settings()
    s = settings or {}
    if "barge_in" in s:
        return s["barge_in"], "setting"              # explicit wins, any value, "anyone" too
    mb = operator_config.current().mailbox           # settings-only field, never `operator`
    if mb:
        return {"room": [mb]}, "operator"            # room only: a call is left out, so anyone
    return ANYONE, "builtin"


def _barge_in_place(v, device: Optional[str]) -> Optional[frozenset]:
    if isinstance(v, dict):
        d = str(device or "")
        v = v.get("call" if d == "call" or d.startswith("call:") else "room", ANYONE)
    return _who_list(v)


def barge_in(device: Optional[str] = None, settings: Optional[dict] = None) -> Optional[frozenset]:
    """The names that may cut a line on ``device``, or None for anyone."""
    return _barge_in_place(_barge_in_value(settings)[0], device)


def barge_in_shown(settings: Optional[dict] = None) -> dict:
    """Who may cut a line in each place, JSON-safe: ``{"room": {"who": ...,
    "from": ..., "by": ...}, "call": {...}}``. ``who`` is ``"anyone"`` or the
    sorted names (``[]``: nobody); ``from``/``by`` are ``user``/``setting``
    (the ``barge_in`` key), ``default``/``operator`` (the operator's mailbox)
    or ``default``/``builtin`` (unconfigured). The same answer `barge_in`
    gives; ``mouth status`` and ``mouth barge`` print it."""
    v, by = _barge_in_value(settings)
    out: dict = {}
    for place, device in (("room", None), ("call", "call")):
        who = _barge_in_place(v, device)
        out[place] = {"who": ANYONE if who is None else sorted(who),
                      "from": "user" if by == "setting" else "default", "by": by}
    return out


LABEL_LATE_S = 1.0   # after the user's turn ends: the slack for the poll that sees a partial's wait run out


def label_wait_s() -> float:
    return float(envnames.get("MOUTH_LABEL_WAIT_S", LABEL_WAIT_S))


def _speaker_labels(directory: Optional[Path] = None, min_seq: int = 0) -> dict:
    """seq -> the side-car's raw label (``alice``, ``other``, ``mixed``...) for
    seqs >= ``min_seq``, from the tail of today's and yesterday's
    speakers_YYYY-MM-DD.jsonl. Never raises; a missing file names nobody."""
    out: dict = {}
    for hf in heard.log_files(directory)[-2:]:
        try:
            with open(heard.speakers_path(hf), "rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - _SPEAKERS_TAIL))
                raw = f.read()
        except OSError:
            continue
        for line in raw.split(b"\n"):
            try:
                r = json.loads(line)
                seq, who = int(r["seq"]), r.get("speaker")
            except (ValueError, KeyError, TypeError, UnicodeDecodeError):
                continue
            if seq >= min_seq and isinstance(who, str):
                out[seq] = who.lower()
    return out


def _settings(settings: Optional[dict]) -> dict:
    if settings is None:
        from . import voices
        settings = voices.settings()
    return settings if isinstance(settings, dict) else {}


def _setting_num(settings: Optional[dict], key: str, lo: float, hi: Optional[float] = None,
                 whole: bool = False) -> Optional[float]:
    """``key`` from the mouth's settings when it is a number in [lo, hi] (a
    whole one if ``whole``), else None: an unreadable value is as if unset."""
    v = _settings(settings).get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
        return None
    if v < lo or (hi is not None and v > hi) or (whole and v != int(v)):
        return None
    return v


def barge_ignore_words(settings: Optional[dict] = None) -> tuple:
    """The words barge-in never counts as the user's, and where they came from:
    ``(words, "setting")`` from ``barge_ignore_words`` in the mouth's settings
    (a list of strings, each split by `_words` as the user's partials are, so
    "uh-huh" is ``uh`` and ``huh``; ``[]`` counts every word), else
    ``(STOP_WORDS, "default")``. An unreadable value (not a list, or a
    non-string entry) is as if unset, so deleting the key restores the
    default. One list for every place, room and call. It reaches the barge
    count only: ``is_echo`` keeps STOP_WORDS."""
    v = _settings(settings).get("barge_ignore_words")
    if isinstance(v, list) and all(isinstance(x, str) for x in v):
        return frozenset(w for x in v for w in _words(x)), "setting"
    return STOP_WORDS, "default"


def barge_words(device: Optional[str] = None, settings: Optional[dict] = None) -> int:
    """How many of the user's content words cut a line, on any ``device``, a call
    too (one policy): the host's answer of :func:`barge_policy`
    (the user's ``barge_words`` setting, else $MOUTH_BARGE_WORDS,
    else BARGE_WORDS). A thin wrapper, so no caller breaks; ``math.inf`` if
    a layer turned the count off. ``device`` no longer changes the answer;
    it stays for the callers."""
    return need_of(barge_policy(device, settings=settings))


#: The env var of the default layer (N only). Below the user's setting: a
#: variable left in a launchd plist must never beat what the user picks
#: in the Settings app, invisibly.
BARGE_WORDS_ENV = "MOUTH_BARGE_WORDS"


def _env_words(env: Optional[dict] = None) -> Optional[int]:
    """$MOUTH_BARGE_WORDS as a whole number of 1 or more, else None.
    Not one (``0``, ``two``) is as if unset, and `barge_warnings` says so: a
    stale variable never stops the mouth, and never cuts on every partial."""
    v = envnames.get(BARGE_WORDS_ENV, None, env)
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        return None
    return n if n >= 1 else None


def line_barge_words(item: Optional[dict]):
    """A line's own barge count (the model layer's ``argument``):
    ``(N, True)`` for a whole number of 1 or more, ``(None, True)`` for
    ``off`` (``--no-barge``, ``--barge-words off``: no cut on a word count; the
    duck and the stop asks stay), ``(None, False)`` when the line carries none
    or one that is not either (as if unset: the next layer answers). It rides
    the item as ``barge_words``, by ``mouth say``, ``mouth.say(barge_words=)``
    or a mail's ``X-Mouth-Extra``; a resume carries its cut line's."""
    v = (item or {}).get("barge_words")
    if v is None:
        return None, False
    if v is False or (isinstance(v, str) and v.strip().lower() == "off"):
        return None, True
    if isinstance(v, bool) or isinstance(v, float) and not v.is_integer():   # True, 2.5, inf, nan
        return None, False
    try:
        n = int(v.strip()) if isinstance(v, str) else int(v)
    except (TypeError, ValueError, OverflowError):     # "two", inf, a list
        return None, False
    return (n, True) if n >= 1 else (None, False)


def check_barge_words(v):
    """``mouth say --barge-words``'s value, checked before anything is written:
    None, a whole number of 1 or more, or ``off`` (returned as ``"off"``).
    Anything else raises ValueError."""
    if v is None:
        return None
    n, given = line_barge_words({"barge_words": v})
    if not given:
        raise ValueError("mouth: barge words is a whole number, 1 or more, or off")
    return "off" if n is None else n


def _layer_num(settings: dict, key: str, builtin: float, lo: float, hi: Optional[float] = None,
               whole: bool = False) -> tuple:
    """(value, from, by) for a duck value: the user's setting, else the built-in."""
    v = _setting_num(settings, key, lo, hi, whole)
    if v is None:
        return builtin, "default", "builtin"
    return (int(v) if whole else float(v)), "user", "setting"


def barge_policy(device: Optional[str] = None, item: Optional[dict] = None,
                 settings: Optional[dict] = None, d: Optional[Path] = None,
                 now: Optional[float] = None) -> dict:
    """The ONE barge policy for a line, on a call or off it: each value with
    the layer that set it (``from``:
    ``default``, ``user`` or ``model``) and its source (``by``: ``builtin``,
    ``env``, ``setting``, ``override`` or ``argument``). Three layers, highest
    wins (the order said once: `bargeswitch.PRECEDENCE`):

    - the model: first the line's own ``barge_words`` (``item``;
      `line_barge_words`, ``by: argument``), N only; then the timed override,
      ``mouth barge`` (`bargeswitch`, ``<d>/barge.json`` until it expires at
      ``now``, ``by: override``): N, ``duck_level``, ``duck_max_s``, and words
      added to the ignore list (a union: ``ignore_added``, never a removal).
      ``None`` for N is off: no cut on a word count (``need`` is ``math.inf``
      in `barge`, see `need_of`); the duck and the stop asks stay;
    - the user: ``barge_words``, ``barge_duck_level``, ``barge_duck_recover_s``,
      ``barge_duck_max_s`` and ``barge_ignore_words`` in the mouth's settings
      (``~/.sessionkit/voice/settings/mouth.json``, `voices.settings`);
    - the default: $MOUTH_BARGE_WORDS (N only, ``by: env``), else the
      built-ins (BARGE_WORDS, BARGE_DUCK_LEVEL, BARGE_DUCK_MAX_S, STOP_WORDS,
      and the recover time by the ears' ``partials`` mode,
      `barge_duck_recover_default`: 1.0 s on ``nemotron``, 2.5 s on ``final``;
      ``partials`` names the mode read).

    Read ONCE a line by ``play_one``, which hands it to ``onset.for_line``; a
    change applies to the next line. With no ``item`` it is the host's answer
    (``mouth status``, ``mouth barge``). ``device`` does not change it (one
    policy). ``d`` is the mouth dir: its settings file (when ``settings`` is
    None) and its override; with no ``d`` the override is the live mouth
    dir's. ``now`` (epoch seconds) is when the override is read. Never raises:
    a value that cannot be read is as if unset. ``ignore`` is a frozenset (not
    JSON); every other value is."""
    from . import bargeswitch
    from .paths import mouth_dir

    if settings is None and d is not None:
        from . import voices
        settings = voices.settings(d)
    s = _settings(settings)
    ov = bargeswitch.state(mouth_dir() if d is None else d, now) or {}
    out: dict = {}
    n, given = line_barge_words(item)
    env_n = _env_words()
    user_n = _setting_num(s, "barge_words", 1, whole=True)
    if given:
        out.update(words=n, words_from="model", words_by="argument")
    elif "words" in ov:
        on, _ = line_barge_words({"barge_words": ov["words"]})
        out.update(words=on, words_from="model", words_by="override")
    elif user_n is not None:
        out.update(words=int(user_n), words_from="user", words_by="setting")
    elif env_n is not None:
        out.update(words=env_n, words_from="default", words_by="env")
    else:
        out.update(words=BARGE_WORDS, words_from="default", words_by="builtin")
    partials = ears_partials()      # the recover built-in goes by it
    out["partials"] = partials
    for key, name, builtin, lo, hi, whole in (
            ("barge_duck_level", "duck_level", BARGE_DUCK_LEVEL, 0, 100, True),
            ("barge_duck_recover_s", "recover", barge_duck_recover_default(partials), 0, None, False),
            ("barge_duck_max_s", "duck_max", BARGE_DUCK_MAX_S, 0, None, False)):
        vk = name if name == "duck_level" else name + "_s"
        if vk in ov:    # the override's duck level and cap (`mouth barge --duck`, `--duck-max`)
            v, frm, by = (int(ov[vk]) if whole else float(ov[vk])), "model", "override"
        else:
            v, frm, by = _layer_num(s, key, builtin, lo, hi, whole)
        out.update({vk: v, name + "_from": frm, name + "_by": by})
    ignore, src = barge_ignore_words(s)
    added = list(ov.get("ignore") or ())     # a union, so a backchannel can never be made to count
    out.update(ignore=ignore | frozenset(added) if added else ignore,
               ignore_from="user" if src == "setting" else "default",
               ignore_by="setting" if src == "setting" else "builtin",
               ignore_added=added, ignore_added_by="override" if added else None)
    out["warnings"] = barge_warnings()
    return out


def need_of(policy: dict):
    """The word count `barge` needs under ``policy``: its ``words``, or
    ``math.inf`` when a layer turned the count off (never a cut on words; a
    stop ask still cuts). A record writes it as ``need: null`` (`_weighed`),
    never ``Infinity``: that is not JSON, and ``jq 'fromjson?'`` drops it."""
    import math
    w = policy.get("words")
    return math.inf if w is None else w


def barge_status(d: Optional[Path] = None, now: Optional[float] = None) -> dict:
    """``mouth status``'s ``barge`` key: the host's
    `barge_policy`, every value with its ``from`` and ``by``, JSON-safe (the
    ignore set itself is left out; ``ignore_from`` names the list), and the
    ``override`` (``mouth barge``, `bargeswitch.shown`: its values, ``until``
    and ``remaining_s``, ``by``; None when there is none), and ``barge_in``,
    who may cut in each place (`barge_in_shown`). ``mouth barge``
    with no argument prints the same."""
    from . import bargeswitch
    from .paths import mouth_dir

    out = {k: v for k, v in barge_policy(d=d, now=now).items() if k != "ignore"}
    out["override"] = bargeswitch.shown(mouth_dir() if d is None else d, now)
    from . import voices
    out["barge_in"] = barge_in_shown(voices.settings(d) if d is not None else None)
    return out


def barge_warnings(env: Optional[dict] = None) -> list:
    """One ``kind: warn`` record per retired barge variable set in ``env``
    (the process's, by default; :data:`RETIRED_ENV`, empty today) and for
    $MOUTH_BARGE_WORDS set to something that is not a count (ignored,
    `_env_words`). A stale variable never stops the mouth: the player prints
    these once at start, and ``mouth status`` lists them."""
    env = os.environ if env is None else env
    out = []
    for k, why in RETIRED_ENV.items():
        name = envnames.source(k, env)   # the name it is set under, new or old
        if name is not None:
            out.append({"kind": "warn", "var": name, "value": env[name], "detail": why})
    name = envnames.source(BARGE_WORDS_ENV, env)
    if name is not None and _env_words(env) is None:
        out.append({"kind": "warn", "var": name, "value": env[name],
                    "detail": "not a whole number of 1 or more: ignored"})
    return out


# -- the duck at the user's voice onset (mouth/onset.py) ------------------------------
# The user's words cut a line 1.3-2 s after the user's first word (measured); their VOICE
# reaches the mouth ~0.1 s after it, as a ``vad`` onset. So the line dips at
# the onset and the word count above confirms the cut. A false onset (road
# noise, the TV) only dips it. The duck follows the user's
# voice: down from the user's onset while it lasts, up the recover time after their
# offset, and never longer than the cap with no offset.
#: The line's level while ducked, 0-100 of its own (25 is about -12 dB: out of
#: the user's way at once, still there if it was noise). 100 turns the duck off.
BARGE_DUCK_LEVEL = 25
#: Seconds after the user's voice OFFSET (the ears' ``vad`` row with ``speech:
#: false``, 210 ms after the user's last voiced frame) with no cut before the line
#: comes back up. The built-in goes by
#: the ears' ``partials`` mode (`barge_duck_recover_default`): a word of a
#: short barge-in lands about 0.1 s after the offset on streaming Nemotron
#: partials, so 1.0 s; chunk partials (``final``, the code default) carry no
#: such evidence and keep 2.5 s.
BARGE_DUCK_RECOVER_S = 2.5
#: The recover built-in for each ears ``partials`` mode. A mode not named here
#: is ``final``.
BARGE_DUCK_RECOVER_BY_PARTIALS = {"final": BARGE_DUCK_RECOVER_S, "nemotron": 1.0}
#: The longest a line stays ducked under unbroken voice with no offset row, in
#: seconds (4 s, a setting through the layers): the offset is taken as lost
#: (the ears died,
#: or noise that never pauses 210 ms) and the line comes back up, its duck
#: record saying ``capped: true``. Each new onset restarts it; a line that
#: starts ducked counts it from its first frame (`onset.BargeDuck`).
BARGE_DUCK_MAX_S = 4.0


def ears_partials() -> str:
    """The ears' ``partials`` mode on this host, ``final`` or ``nemotron``,
    from ears.json (`ears.listen.stt.conf_partials`, the file both ears
    follow). ``final`` when it cannot be read. Never raises."""
    try:
        from ears.listen.stt import conf_partials
        return conf_partials()
    except Exception:  # noqa: BLE001 - a default that cannot be read is the code's own
        return "final"


def barge_duck_recover_default(partials: Optional[str] = None) -> float:
    """The built-in ``barge_duck_recover_s`` for the ears' ``partials`` mode
    (`ears_partials` when None): 1.0 s on ``nemotron``, 2.5 s on ``final``."""
    mode = ears_partials() if partials is None else partials
    return BARGE_DUCK_RECOVER_BY_PARTIALS.get(mode, BARGE_DUCK_RECOVER_S)


def barge_duck_level(settings: Optional[dict] = None) -> int:
    """``barge_duck_level`` in the mouth's settings (0-100), else BARGE_DUCK_LEVEL
    (a thin wrapper over :func:`barge_policy`)."""
    return barge_policy(settings=settings)["duck_level"]


def barge_duck_recover_s(settings: Optional[dict] = None) -> float:
    """``barge_duck_recover_s`` in the mouth's settings (seconds after the user's
    voice offset, 0 or more), else the built-in for the ears' ``partials``
    mode, `barge_duck_recover_default` (a thin wrapper over :func:`barge_policy`)."""
    return barge_policy(settings=settings)["recover_s"]


def barge_duck_max_s(settings: Optional[dict] = None) -> float:
    """``barge_duck_max_s`` in the mouth's settings (seconds, 0 or more), else
    BARGE_DUCK_MAX_S (a thin wrapper over :func:`barge_policy`): the cap
    `onset.BargeDuck` applies."""
    return barge_policy(settings=settings)["duck_max_s"]


def resume_s() -> float:
    return float(envnames.get("MOUTH_RESUME_S", RESUME_S))


def rest_of(text: str, played: str) -> str:
    """What was not heard, from the start of the sentence it was cut in."""
    cut = len(played or "")
    start = max(text.rfind(c, 0, cut) for c in ".!?")
    return text[start + 1:].strip() if start >= 0 else text.strip()


def _mic_partials_since(since_t: float, directory: Optional[Path] = None,
                        sources: tuple = MIC) -> list:
    """The user's worded partials said after ``since_t`` and delivered fresh, or with
    no audio time written after it (`_partials_since`), oldest first."""
    return [p for p, stale_lag in _partials_since(since_t, directory, sources) if stale_lag is None]


def _partials_since(since_t: float, directory: Optional[Path] = None,
                    sources: tuple = MIC) -> list:
    """Every worded partial WRITTEN after ``since_t``, oldest first, as
    ``(partial, stale_lag)``: ``stale_lag`` is None when it counts, else its lag in
    seconds (``ts`` minus its audio time, `_heard_t`) and it does not.

    It counts iff the user SAID it after ``since_t`` and the recogniser wrote it within
    `barge_fresh_s` of its audio (the lag at the write). A partial with no
    audio time counts by arrival, as before.
    "After" is at heard's resolution (`_ms`): a stamp in ``since_t``'s
    own millisecond is after it.

    Written after ``since_t`` is the first test, on arrival, and it is a correct
    necessary condition: audio is never later than its write (lag >= 0), so a
    partial written by then was said by then. Old ears' records find their
    capture's start in the same forward pass, so it costs no extra read."""
    out, starts, bound, since_ms = [], {}, barge_fresh_s(), _ms(since_t)
    for path in heard.log_files(directory)[-2:]:
        try:
            with open(path, "rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - _TAIL))
                lines = f.read().split(b"\n")
        except OSError:
            continue
        for raw in lines[:-1]:
            try:
                rec = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(rec, dict) or rec.get("source", "mic") not in sources:
                continue
            if _is_started(rec):
                starts[_start_key(rec)] = _ts(rec)
                continue
            if rec.get("kind") == "partial" and str(rec.get("text") or "").strip():
                t = _ts(rec)
                if t is None or _ms(t) < since_ms:
                    continue
                said = _heard_t(rec, starts)
                if said is None or (_ms(said) >= since_ms and t - said <= bound):
                    out.append((rec, None))
                else:
                    out.append((rec, round(t - said, 3)))
    return out


def barge(since_t: float, line_text: str, directory: Optional[Path] = None,
          now: Optional[float] = None, sources: tuple = MIC,
          words: Optional[int] = None, who: Optional[frozenset] = None,
          ignore: Optional[frozenset] = None, trace: Optional[list] = None) -> Optional[dict]:
    """The user's words since ``since_t`` (the line's first frame) that ask for the
    floor, or None. Echo partials are ignored, and so, when ``who`` names
    people (`barge_in`), are partials the side-car named as anyone else; then
    an explicit stop word cuts at once, and otherwise WORDS (default:
    BARGE_WORDS) content words of the user that are not the line's own, counted
    across their partials so far. ``words`` may be ``math.inf``: the count is
    off (``--no-barge``, `need_of`), and only a stop ask cuts. A content word
    is one not in ``ignore`` (default: STOP_WORDS; the caller reads the
    policy's ``ignore`` once a line, `barge_policy`).

    Only partials the user said after ``since_t`` and the recogniser delivered in
    time count (`_partials_since`); the rest are skipped ``stale``.

    ``trace``, a list, is cleared on entry and gets one entry per partial
    weighed in this poll (`_weighed`; a ``stale`` one also carries its
    ``lag_s``): what was dropped and why, what counted,
    the running count, and a verdict. A poll that weighs nothing leaves it
    empty. It never changes what is returned."""
    if trace is not None:
        trace.clear()
    ignored = STOP_WORDS if ignore is None else ignore
    if not barge_on():
        return None
    need = barge_words() if words is None else words     # before the loop's own `words`
    rec, now = _look(directory, now, sources=sources)
    if rec is None:
        return None
    # The gate stays on ARRIVAL: audio is never later than
    # its write, so nothing written by since_t was said after it, and this only
    # ever returns early. Freshness is judged per partial, in `_partials_since`.
    # At heard's resolution, as there (`_ms`).
    t = _ts(rec)
    if t is None or _ms(t) < _ms(since_t):
        return None
    if rec.get("kind") != "partial":
        # Cheap: nothing new from the user - every partial was weighed while it was
        # the newest. Except under `who`: a partial still waiting on its name
        # was skipped, and a short turn ENDS inside that wait, so its turn
        # record became the newest and "stop" was never counted (measured:
        # 84 of 87 one- or two-word turns close in
        # under 2 s). So keep weighing for the wait, plus a poll's slack, after
        # the turn record; its partials are older than it, so past that window
        # every one of them has been named or has counted unnamed.
        # Arrival here too: the wait is for the side-car, from the write.
        if who is None or now - t > label_wait_s() + LABEL_LATE_S:
            return None
    weighed = _partials_since(since_t, directory, sources)
    partials = [p for p, stale_lag in weighed if stale_lag is None]
    labels: dict = {}
    if who is not None and partials:
        seqs = [p["seq"] for p in partials if isinstance(p.get("seq"), int)]
        labels = _speaker_labels(directory, min(seqs)) if seqs else {}
    have, novel, last = set(_words(line_text)), set(), None
    last_entry = None                                # the trace entry of `last`
    for p, stale_lag in weighed:
        text = str(p.get("text") or "")
        name = None
        if stale_lag is not None:                    # said before the line, or delivered late
            if trace is not None:
                trace.append({**_weighed(p, text, need, len(novel), skip="stale"), "lag_s": stale_lag})
            continue
        if is_echo(text, line_text):
            if trace is not None:
                trace.append(_weighed(p, text, need, len(novel), skip="echo"))
            continue
        if who is not None:
            name = labels.get(p.get("seq"))
            if name is None:
                # Arrival: the side-car names a partial after it is WRITTEN.
                pt = _ts(p)
                if pt is not None and now - pt < label_wait_s():
                    if trace is not None:
                        trace.append(_weighed(p, text, need, len(novel), skip="unnamed"))
                    continue                         # not named yet: wait for it
            elif name != UNHEARD and name not in who:  # unheard: as if anyone
                if trace is not None:
                    trace.append(_weighed(p, text, need, len(novel), skip="speaker", speaker=name))
                continue                             # named, and not one who may cut
        words = _words(text)
        if STOP_ASKS & set(words):
            if trace is not None:
                trace.append(_weighed(p, text, need, len(novel), speaker=name, ignored=ignored,
                                      have=have, novel=novel, verdict="stop-ask"))
            return p
        novel |= {w for w in words if w not in ignored and w not in have}
        last = p
        if trace is not None:
            trace.append(_weighed(p, text, need, len(novel), speaker=name, ignored=ignored, have=have))
            last_entry = trace[-1]
    got = last if len(novel) >= need else None
    if got is not None and last_entry is not None:
        last_entry["verdict"] = "cut"
    return got


def _weighed(p: dict, text: str, need: int, count: int, skip: Optional[str] = None,
             speaker: Optional[str] = None, ignored: frozenset = frozenset(), have: set = frozenset(),
             novel: Optional[set] = None, verdict: str = "wait") -> dict:
    """One `barge` trace entry: the partial (seq, ts, text), why it was skipped
    or how its words split (``dropped`` by reason, in the partial's word order:
    ``ignored`` before ``own``, as `barge` tests them; the rest ``counted``),
    the running ``count`` against ``need``, and the ``verdict`` (``wait``,
    ``cut`` or ``stop-ask``). With ``novel`` (a stop-ask, which returns before
    counting) the count includes this partial's words. Builds a dict, no I/O."""
    words = _words(text)
    entry: dict = {"seq": p.get("seq"), "ts": p.get("ts"), "text": text}
    if skip is not None:
        entry["skip"] = skip
    if speaker is not None:
        entry["speaker"] = speaker
    dropped: dict = {}
    counted: list = []
    if skip is not None:
        if words:
            dropped[skip] = words
    else:
        for w in words:
            if w in ignored:
                dropped.setdefault("ignored", []).append(w)
            elif w in have:
                dropped.setdefault("own", []).append(w)
            else:
                counted.append(w)
        asks = [w for w in dict.fromkeys(words) if w in STOP_ASKS]
        if asks:
            entry["asks"] = asks
        if novel is not None:
            count = len(novel | set(counted))
    entry.update(dropped=dropped, counted=counted, count=count,
                 need=None if need == float("inf") else need,   # off: null, never Infinity
                 verdict=verdict)
    return entry


def cut_words(trace: Optional[list]) -> tuple:
    """The words that cut a line, from the trace of the poll that cut it
    (`barge`'s ``trace=``): ``(words, how)``.

    - a stop ask: its ask words, ``how`` ``"stop-ask"`` (``["stop"]``);
    - a cut on words: every word counted in the poll, deduplicated in order,
      ``how`` ``"words"``. `barge` re-weighs every partial since the line
      began on each poll, so these are exactly its ``novel`` set, the words
      that reached N;
    - no cut in the trace (it waited, or nothing was weighed): ``([], None)``.

    The verdict is looked for on every entry, not the last: `barge` marks
    ``cut`` on the last partial that COUNTED, and a skipped one (an echo, an
    unnamed or another speaker's partial) may be weighed after it. A stop ask
    returns at once, so it is always the last. Builds a tuple, no I/O."""
    trace = [e for e in (trace or ()) if isinstance(e, dict)]
    verdicts = {e.get("verdict") for e in trace}
    if "stop-ask" in verdicts:
        ask = next(e for e in reversed(trace) if e.get("verdict") == "stop-ask")
        return [str(w) for w in ask.get("asks") or ()], "stop-ask"
    if "cut" not in verdicts:
        return [], None
    return [str(w) for w in dict.fromkeys(w for e in trace for w in e.get("counted") or ())], "words"


_WHO_UNSET = object()


def speaking_over(device: Optional[str], directory: Optional[Path] = None,
                  now: Optional[float] = None, who=_WHO_UNSET) -> Optional[dict]:
    """The user's newest partial if they have the floor on ``device``, else None: what
    ducks a bed under the user's voice (the ``barge`` cause).

    The floor is :func:`floor`'s, read on :func:`barge_sources_for` (on a call,
    the call's own words only), so it holds from the user's first worded partial to
    the end of their turn. ``who`` is the :func:`barge_in` filter (read here when not
    given): a partial the side-car named as anyone else does not count, and
    one not named yet counts once its label wait has run out, as in
    :func:`barge`. Unlike :func:`barge` no word count and no echo test: a
    duck is cheap and comes back up, and a bed has no words of its own to echo.
    The caller decides WHERE it counts (``barge_device``)."""
    sources = barge_sources_for(device)
    rec, start_t, _, t, now = _look_floor(directory, now, sources=sources)
    if rec is None or rec.get("kind") != "partial":
        return None
    if t is not None and now - t >= idle_s():      # aged by audio time, as `floor`
        return None
    if who is _WHO_UNSET:
        who = barge_in(device)
    if who is None:
        return rec
    # The turn's own first partial counts: `_partials_since` counts a stamp in
    # ``since``'s own millisecond.
    since = start_t if start_t is not None else (t if t is not None else now)
    partials = _mic_partials_since(since, directory, sources)
    seqs = [p["seq"] for p in partials if isinstance(p.get("seq"), int)]
    labels = _speaker_labels(directory, min(seqs)) if seqs else {}
    for p in reversed(partials):                 # newest first
        name = labels.get(p.get("seq"))
        if name is None:
            pt = _ts(p)                          # arrival: the side-car names it after the write
            if pt is not None and now - pt < label_wait_s():
                continue                         # not named yet: wait for it
            return p
        if name in who or name == UNHEARD:       # unheard: as if anyone, as in `barge`
            return p
    return None
