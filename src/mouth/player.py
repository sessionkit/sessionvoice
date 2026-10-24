"""The player: one resident process, one utterance at a time, in playlist order.

``say`` drops a mail into the queue maildir's ``new/`` (box.py) and makes
sure a player is running (the ``player.lock`` flock is held by whoever
plays); it never waits for the audio. Mail to mouth@<host> lands in the same
``new/``. The player takes the first line in play order (priority, then
arrival) to ``cur/``, synthesises, flags it when done (S spoken, T not, P
passed), and writes to the heard log:

- ``saying`` at the FIRST audio frame handed to the device:
  ``utt text voice backend device requested_ts gen_s``
- ``said`` when playback ends:
  ``utt played_s dur_s cut cut_at_s text_played_est underrun_s underruns
  device_gaps device_gap_s device_gap_max_s reason`` (``underrun_s``: silence
  the device got mid-line because synthesis fell behind real time; the
  callback ran on time and found the queue empty. ``device_gap*``: silence the
  DEVICE inserted because the mouth's callback ran late, the host starved,
  measured from the device clock. ``device_gaps`` counts episodes, late
  ~50 ms samples in a row merged into one, ``device_gap_s`` totals their lost
  time and ``device_gap_max_s`` is the longest episode: ~50 ms resolution,
  not the longest single silence. On ``null`` and ``call:`` all three are 0,
  meaning "no device callback", and the row's ``device`` tells which. Played
  rows always carry them; a line that never played has none), where reason
  is ``done``, ``stop``, ``barge-in``, ``device-absent`` (not there at the
  start), ``device-lost`` (stopped taking audio mid-play) or ``error``.

``text_played_est`` is an ESTIMATE: the text cut at the played fraction of
the audio, back to a word boundary, until the backends give word timings.
``dur_s`` is absent when a cut came before synthesis finished.

A stop is a file, ``stop``: ``{"t": epoch, "reason": ..., "flush": bool}``.
It cuts every utterance requested at or before ``t`` (the one playing within
50 ms, and, with flush, the queued ones: each gets a ``said`` with
``played_s`` 0 and no ``saying``, so every requested utterance is accounted
for). A stop never touches an utterance requested after it.
"""

from __future__ import annotations

import fcntl
import functools
import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from ears import envnames, heard

from . import audio as _audio
from . import backends as _backends
from . import bed as _bed
from . import box as _box
from . import maillog
from . import output as _output
from . import hold as _hold
from . import onset as _onset
from . import level as _level
from . import mpv as _mpv
from . import voices as _voices
from .paths import mouth_dir


_SYNTH_KEYS = ("text", "voice", "speed", "backend", "file", "start", "end")


_duck: dict = {"prev": None, "ctl": None, "holders": [], "skipped": False}
_duck_lock = threading.RLock()   # a bed's first frame and a line's can race for the one DJ


def _dj_gate(item: Optional[dict], d: Path) -> tuple:
    """``duckswitch.decide`` for the DJ: ``(dip?, by)``. A gate that cannot answer
    leaves today's DJ dip in charge (a file's own stamp, else on), never a lost line."""
    from . import duckswitch

    try:
        return duckswitch.decide(item, d)
    except Exception:  # noqa: BLE001 - a duck that cannot decide never stops a line
        return (_bed.ducks(item), _bed._from(item)) if item is not None else (True, "builtin")


def _dj_skipped(item: Optional[dict], why: str, by: str) -> None:
    """The DJ's ``event: duck-skipped`` row: ``lane: dj``,
    beside the bed's rows and through the same ``heard.event``. Never raises."""
    if item is None:
        return
    try:
        rec = heard.event("duck-skipped", utt=item.get("utt"), device=item.get("device"),
                          source="mouth", directory=_log_dir(item), session=item.get("session"),
                          agent=item.get("agent"), lane="dj", why=why, by=by)
    except (OSError, ValueError):
        return
    maillog.entry(item, "duck-skipped", rec)


def _duck_on(item: Optional[dict] = None, d: Optional[Path] = None, *,
             line: Optional[dict] = None) -> None:
    """Lower the DJ's music while the mouth speaks ($MOUTH_DUCK, a volume 0-100).

    mpv stays its own stream: no mixing, just its volume, through its IPC
    socket (mpv.py). Once per run of lines; _duck_off restores it when the queue
    is empty, so stacked lines don't pump the music between them.

    The gate first: ``item`` is a played
    file's own item, so its ``--duck``/``--no-duck`` beats a ``mouth duck``
    override; None is a spoken line (``line``, named on the row), which the
    override and the host's ``music_duck`` answer. Gate no: the DJ is not
    lowered, and ONE ``duck-skipped`` row (``lane: dj``, ``why`` play or say,
    ``by``) is written per run, latched by ``skipped`` (cleared by _duck_off,
    and by a dip, so it never blocks one).

    Who wins the one DJ volume (a test pins it): its HOLDERS. Every caller whose
    gate said yes while the DJ was down holds it (the one that lowered it, and
    any that joined after; lines hold it as one, None). A no lifts a lowered DJ
    only if no holder would dip it now. So a ``--duck`` file keeps the DJ down
    under ``mouth duck off`` while lines play over it, whether it lowered the
    DJ or joined a dip lines made; a DJ that lines lowered comes up at the next
    line once the override says off; and a ``--no-duck`` file never raises a DJ
    that lines lowered. Where the DJ stays down for a holder, the no writes no
    row: nothing was skipped. Holders last the run, as the dip does.
    """
    level = envnames.get("MOUTH_DUCK")
    if not level:
        return
    d = Path(d) if d is not None else mouth_dir()
    on, by = _dj_gate(item, d)
    why = "say" if item is None else "play"
    with _duck_lock:
        if not on:
            if _duck["prev"] is not None:
                if any(_dj_gate(h, d)[0] for h in _duck["holders"]):
                    return                     # a holder still dips it: down it stays
                _duck_off()                    # the override reached it: up, a skipped dip
            elif _duck["skipped"] or not _dj_would_dip(int(level)):
                return
            if not _duck["skipped"]:
                _duck["skipped"] = True
                _dj_skipped(item if item is not None else line, why, by)
            return
        _duck["skipped"] = False
        if _duck["prev"] is not None:
            if not any(h is item for h in _duck["holders"]):
                _duck["holders"].append(item)  # it joins the dip: a --duck file holds it too
            return
        try:
            ctl = _mpv.Mpv()
            prev = ctl.volume()
            if prev is not None and prev > int(level):
                ctl.volume(int(level))
                _duck.update(prev=prev, ctl=ctl, holders=[item])
        except Exception:  # noqa: BLE001 - no DJ, no ducking; never a reason not to speak
            pass


def _dj_would_dip(level: int) -> bool:
    """Whether a dip the gate stopped was a real one: a DJ is playing above
    ``level`` (no DJ, nothing skipped; no row)."""
    try:
        prev = _mpv.Mpv().volume()
        return prev is not None and prev > level
    except Exception:  # noqa: BLE001 - no DJ: nothing to skip
        return False


def _duck_off() -> None:
    with _duck_lock:
        _duck["skipped"] = False
        if _duck["prev"] is None:
            return
        try:
            _duck["ctl"].volume(_duck["prev"])
        except Exception:  # noqa: BLE001
            pass
        finally:
            _duck.update(prev=None, ctl=None, holders=[])


class _Cut(Exception):
    pass


def _abort(out) -> None:
    """Abort without trusting the device: a dead one may hang abort() too."""
    import threading

    t = threading.Thread(target=out.abort, daemon=True)
    t.start()
    t.join(1.0)


def _read_stop(d: Path) -> Optional[dict]:
    try:
        return json.loads((d / "stop").read_text())
    except (FileNotFoundError, ValueError):
        return None


def _log_dir(item: dict) -> Optional[Path]:
    return Path(item["log_dir"]) if item.get("log_dir") else None


def _common(item: dict) -> dict:
    return {"utt": item["utt"], "voice": item.get("voice"), "backend": item.get("backend"),
            "backend_asked": item.get("backend_asked"), "pan": item.get("pan"),
            # the rung that chose the device (mouth.resolve_device): flag, mail, call,
            # env, settings or default; the device itself is the caller's
            "device_from": item.get("device_from"),
            "session": item.get("session"), "agent": item.get("agent"),
            "requested_ts": item.get("requested_ts"), "directory": _log_dir(item),
            "file": item.get("file"), "start": item.get("start"), "end": item.get("end"),
            "mail_id": item.get("mail_id"), "hold": item.get("hold"),
            # room: held by the room default (hold.ROOM_HOLD), not by its sender
            "hold_from": item.get("hold_from"),
            "interest": item.get("interest"), "resume_of": item.get("resume_of"),
            # a say-again replay names the line it replays, so the keeper
            # follows it back to the original; its text is the original's own
            "again_of": item.get("again_of"),
            "expires_t": item.get("expires_t"),
            # the level it played at; the player fills it at take
            "volume": item.get("volume"), "volume_from": item.get("volume_from"),
            # the line's thread on its saying/said, so a past line says which
            # turn it answered and which call it is in; None is dropped
            "in_reply_to": item.get("in_reply_to"), "call_id": item.get("call_id")}


def _gain(item: dict) -> Optional[float]:
    """The line's volume as a linear gain (``volume / 100``), or
    None at 100 (or no level), where nothing is multiplied: today's samples,
    byte for byte. A bad level plays at 100 rather than not at all."""
    v = _voices._pct(item.get("volume"))
    return None if v is None or v == 100 else v / 100.0


def text_at(text: str, fraction: float) -> str:
    """The text cut at ``fraction`` of its length, back to a word boundary."""
    if fraction >= 1.0:
        return text
    if fraction <= 0.0:
        return ""
    cut = int(len(text) * fraction)
    head = text[:cut]
    if cut < len(text) and not text[cut].isspace() and " " in head:
        head = head[: head.rfind(" ")]
    return head.rstrip()


def _said(item: dict, **fields) -> dict:
    if fields.get("cut") and fields.get("reason") == "barge-in" and "underruns" in fields:
        # the user's words cut a PLAYING line (play_one writes underruns, said_unplayed never
        # does): decide the replay NOW, so its notice says what the player will do
        fields.update(_plan(item, fields))
    replayed = item.get("resume_of") and not fields.get("cut") and fields.get("reason") == "done"
    if item.get("again_of"):
        # a say-again replay: ONE note on every said row,
        # played, cut or dropped, in place of the drop notice; it rides, never wakes
        from . import notice
        fields["notice"] = notice.again(item, fields)
    elif fields.get("cut") or replayed:  # a drop, or a replay heard in full
        from . import notice
        fields["notice"] = notice.dropped(item, fields)
    rec = heard.said(**fields)
    maillog.entry(item, "said", rec)
    return rec


def _log(rec: dict) -> None:
    """One JSON line on the player's stdout, which is ``player.log``: the
    barge and duck records. Never raises; a record that
    cannot be written is dropped and the line plays on."""
    try:
        print(json.dumps(rec), flush=True)
    except Exception:  # noqa: BLE001 - logging never stops a line
        pass


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t).astimezone().isoformat(timespec="milliseconds")


class BargeLog:
    """One line's barge decision records: `emit` turns
    a `hold.barge` trace into ``kind: barge`` records on player.log.

    `barge` re-weighs every partial since the line began on every poll, so a
    partial is printed the first time it is weighed and again ONLY when its
    classification changes (a label landing, ``near()`` moving): the
    signature is (skip, speaker, dropped, counted, asks, verdict), never the
    running count. ``ignore`` names the list in force: ``default`` or
    ``setting`` (`hold.barge_ignore_words`)."""

    def __init__(self, utt: Optional[str], ignore: str = "default", log: Optional[Callable] = None) -> None:
        self.utt, self.ignore, self._log = utt, ignore, log
        self._seen: dict = {}

    @staticmethod
    def _key(e: dict):
        seq = e.get("seq")
        return seq if isinstance(seq, int) else (e.get("ts"), e.get("text"))

    @staticmethod
    def _signature(e: dict) -> tuple:
        dropped = e.get("dropped") or {}
        return (e.get("skip"), e.get("speaker"), tuple((k, tuple(v)) for k, v in sorted(dropped.items())),
                tuple(e.get("counted") or ()), tuple(e.get("asks") or ()), e.get("verdict"))

    def emit(self, trace: list) -> list:
        """Print each entry of ``trace`` that is new or changed; return them."""
        out = []
        at = None
        for e in trace:
            key, sig = self._key(e), self._signature(e)
            if self._seen.get(key) == sig:
                continue
            self._seen[key] = sig
            at = at or _iso(time.time())
            rec = {"kind": "barge", "utt": self.utt, **e, "ignore": self.ignore, "at": at}
            (_log if self._log is None else self._log)(rec)
            out.append(rec)
        return out


def _log_duck(item: dict, out, bduck: "_onset.BargeDuck", reason: str) -> None:
    """The line's duck record on player.log: once, at
    its end, done or cut, for a line that had a BargeDuck, a call line too.
    A line with none (a speaker, the null device,
    ``barge_duck_level`` 100) never gets here."""
    _log(bduck.record(utt=item.get("utt"), device=getattr(out, "name", item.get("device")),
                      cut=reason != "done", reason=reason))


def _saying(item: dict, text: str, **fields) -> dict:
    rec = heard.saying(text, **fields)
    maillog.entry(item, "saying", rec)
    return rec


def said_unplayed(item: dict, reason: str, device: Optional[str] = None, detail: Optional[str] = None,
                  cut_named: Optional[dict] = None) -> dict:
    """The said row of a line that never played. ``cut_named``: the `CUT_KEYS`
    fields of the barge-in stop that flushed it (`_stop_cut_fields`), so a
    flushed line names the words that cut it."""
    return _said(item, **_common(item), device=device or item.get("device"), played_s=0.0,
                 cut=True, cut_at_s=0.0, text_played_est="", reason=reason, detail=detail,
                 **(cut_named or {}))


#: What names a barge-in cut: the words that cut it,
#: how (``words`` or ``stop-ask``), and the N in force with its layer. On the
#: cut line's said row, on the stop record, and from it on every line it flushes.
CUT_KEYS = ("cut_by", "cut_how", "barge_words", "barge_words_from")


def _cut_fields(trace: list, policy: dict) -> dict:
    """The `CUT_KEYS` of the poll that cut a line (``trace``, `hold.cut_words`)
    under its ``policy`` (`hold.barge_policy`); {} when the trace holds no cut.
    ``barge_words`` is None under ``--no-barge`` (a stop ask still cuts), and a
    None is dropped from the row. Never raises."""
    try:
        words, how = _hold.cut_words(trace)
        if how is None:
            return {}
        return {"cut_by": words, "cut_how": how, "barge_words": policy.get("words"),
                "barge_words_from": policy.get("words_from")}
    except Exception:  # noqa: BLE001 - naming the words never changes the cut
        return {}


def _stop_cut_fields(stop: Optional[dict]) -> dict:
    """The `CUT_KEYS` a barge-in stop record carries; {} for any other stop."""
    if not stop or stop.get("reason") != "barge-in":
        return {}
    return {k: stop[k] for k in CUT_KEYS if stop.get(k) is not None}


class Synth:
    """Synthesis in its own thread, running ahead of playback into a buffer.

    Playback reads the buffer, so synthesis never waits on the device. When
    one utterance's synthesis finishes, the player starts the NEXT one while
    this one is still playing (stacked lines had 0.3 s of dead
    air between them, the next item's synthesis). One at a time on the TTS
    server, never two at once.
    """

    def __init__(self, item: dict, on_done=None) -> None:
        self.item = item
        self.utt = item["utt"]
        if item.get("file"):
            self.backend, self.voice = _backends.FileSound(item["file"], item.get("start"),
                                                           item.get("end")), ""
        else:
            self.backend, self.voice = _backends.resolve(item.get("backend") or "auto",
                                                         item.get("voice") or "af_sky")
        self.sample_rate = self.backend.sample_rate
        self.frames = 0
        self.t0 = time.monotonic()
        self.t_first: Optional[float] = None
        self.done = False
        self.finished = False  # every block synthesised: not cancelled first, no error
        self.error: Optional[BaseException] = None
        self._blocks: list = []
        self._cond = threading.Condition()
        self._cancel = threading.Event()
        self._on_done = on_done
        threading.Thread(target=self._run, daemon=True, name=f"synth-{self.utt}").start()

    def _run(self) -> None:
        gen = None
        try:
            gen = self.backend.stream(self.item["text"], self.voice, self.item.get("speed"))
            for block in gen:
                if self._cancel.is_set():
                    break
                with self._cond:
                    if self.t_first is None:
                        self.t_first = time.monotonic()
                    self._blocks.append(block)
                    self.frames += len(block)
                    self._cond.notify_all()
            else:
                self.finished = True
        except BaseException as e:  # noqa: BLE001 - surfaced to the player through blocks()
            self.error = e
        finally:
            if gen is not None:
                try:
                    gen.close()  # a cancel must not leave the request open
                except Exception:  # noqa: BLE001
                    pass
            with self._cond:
                self.done = True
                self._cond.notify_all()
        if self.finished and self._on_done and not self._cancel.is_set():
            try:
                self._on_done(self)
            except Exception:  # noqa: BLE001
                pass

    @property
    def gen_s(self) -> Optional[float]:
        return round(self.t_first - self.t0, 3) if self.t_first is not None else None

    def cancel(self) -> None:
        self._cancel.set()

    def wait_for(self, frames: int, tick: float = _backends.BLOCK_S) -> bool:
        """True once ``frames`` of audio are buffered or the synthesis has ended
        (done, cut or failed); else wait up to ``tick`` and say whether it is now."""
        with self._cond:
            if self.frames < frames and not self.done:
                self._cond.wait(tick)
            return self.frames >= frames or self.done

    def snapshot(self) -> list:
        """The blocks synthesised so far (all of them once ``finished``)."""
        with self._cond:
            return list(self._blocks)

    def blocks(self, tick: float = _backends.BLOCK_S):
        """Yield audio blocks as they arrive; ``None`` every ``tick`` while waiting,
        so the player can look for a stop during a slow synthesis."""
        i = 0
        while True:
            with self._cond:
                if i >= len(self._blocks) and not self.done:
                    self._cond.wait(tick)
                if i < len(self._blocks):
                    block = self._blocks[i]
                    i += 1
                elif self.done:
                    if self.error is not None:
                        raise self.error
                    return
                else:
                    block = None
            yield block


def _cut_by_mail(q: "_box.Queue", item: dict, same=None) -> Optional[str]:
    """Why a mail that landed meanwhile cuts this line, if one does: a retract
    naming it (``retracted``), or a ``now`` line queued after it
    (``interrupted``). ``say --now`` and ``mouth retract`` cut at once instead.

    ``same`` narrows the ``now`` look to the lines it accepts: a bed passes
    "lines for my own device", so a ``now`` to another device
    never cuts the music."""
    for e in q.entries():
        if e["_retract"] and e["_sup"] == item.get("mail_id") and e["_allowed"] and e["_sup_trust"]:  # trusted Supersedes
            return "retracted"
    for x in q.lines():
        if x.get("priority") == "now" and x["requested_t"] > item["requested_t"] and x["utt"] != item["utt"] \
                and (same is None or same(x)):
            return "interrupted"
    return None


def _superseded(q: "_box.Queue", item: dict) -> bool:
    """A mail that supersedes this line landed after the player took it:
    ``mouth amend`` of a line taken but not yet heard, or a ``Supersedes:`` mail
    from an allowed sender. A retract is :func:`_cut_by_mail`'s. Only asked
    before the line's first frame: a line already heard is never cut for an
    amend, and its correction plays after it, as news.

    The cut is gated as a queued line's replacement is: the mail's
    ``Supersedes`` must be trusted (``_sup_trust``: a local drop, which is what
    ``mouth amend`` writes, or a v2 signature covering it), or come From the
    line's own sender (:func:`box._same_from`). Anything else may not
    cut another sender's line before its first frame, silently: the line plays,
    and the mail plays after it, as news (it names no queued line). A mail
    refused whole (``_untrusted``: a file outside the sounds dir, a call it may
    not place) cuts nothing either, trusted or not: it never plays in the line's
    place, so the line plays, as a queued line's target is left untouched."""
    mid = item.get("mail_id")
    if not mid:
        return False
    own = {"_from": (item.get("mail_from") or "")[:200]}   # the line's From, as read() keeps it
    return any(e["_sup"] == mid and not e["_retract"] and e["_allowed"]
               # a mail Queue.lines() never plays (refused whole, a notice reply, the user's own
               # turn) never cuts: as for a queued line, its target is untouched
               and not (e.get("_untrusted") or e.get("_notice_reply") or e.get("_heard"))
               and (e["_sup_trust"] or _box._same_from(e, own))   # trusted, or the same sender
               for e in q.entries())


def _mark_heard(d: Path, item: dict, t: float) -> None:
    """``playing.json`` says the line is AUDIBLE from now: ``first_frame_t``, the
    wall time of its first frame. Until then the line is taken but not
    yet heard (synthesis, the lead hold, opening the device), and ``mouth amend``
    may still replace it; once it is set, amend refuses. Never raises: a line
    whose mark cannot be written plays on."""
    p = d / "playing.json"
    try:
        cur = json.loads(p.read_text())
        if not isinstance(cur, dict) or cur.get("utt") != item.get("utt"):
            return
        tmp = d / ".playing.json.part"
        tmp.write_text(json.dumps({**cur, "first_frame_t": t}))
        tmp.rename(p)
    except (OSError, ValueError):
        pass


def play_one(item: dict, d: Path, synth: Optional[Synth] = None, prefetched: bool = False,
             beds: "Optional[_bed.Beds]" = None, on_poll: Optional[Callable[[], object]] = None) -> dict:
    """Play one queued utterance; return its ``said`` record.

    ``synth`` is its synthesis if already started; ``prefetched`` says it was
    started while the previous utterance played (logged on ``saying``).
    ``beds`` hears the line's first frame: every bed weighs its dip now.
    ``on_poll`` runs every 100 ms while the line plays and is not being cut
    (``serve`` starts files for idle devices there); it never
    runs inside ``open_output`` or the final ``drain``, and its failure never
    stops the line.
    """
    text, speed = item["text"], item.get("speed")
    if synth is None:
        try:
            synth = Synth(item)
        except Exception as e:  # noqa: BLE001 - a bad voice must still close the utterance
            return said_unplayed(item, "error", detail=str(e)[:300])
    backend = synth.backend
    if backend.name != item.get("backend"):  # log what ran; keep what was asked (auto)
        item = {**item, "backend": backend.name, "backend_asked": item.get("backend")}
    try:
        out = _output.open_output(item["device"], backend.sample_rate, item.get("pan"),
                                  meta={"text": text, "who": item.get("agent")})
    except Exception as e:  # noqa: BLE001 - absent, or PortAudio refusing to open it
        synth.cancel()
        detail = str(e) if isinstance(e, _output.DeviceAbsent) else f"{type(e).__name__}: {e}"
        return said_unplayed(item, "device-absent", detail=detail[:300])

    sr = backend.sample_rate
    step = int(sr * _backends.BLOCK_S)
    written = 0
    reason, detail = "done", None
    started = False
    gain = _gain(item)   # applied where the samples are written: every output, one place
    # the voice's own gain and limiter, before the volume: read once a line
    lev, level_rec = _level.for_line(item, backend.name, sr, d=d)

    first_frame_t = None
    barge_here = _hold.barge_device(getattr(out, "name", item["device"]))
    barge_who = _hold.barge_in(item["device"])   # read once a line: a change applies to the next
    # the ONE barge policy: N, the duck and the ignore list, each from its
    # layer (the line's own, the mouth barge override, the user's setting, the default);
    # likewise read once a line, the override from this mouth dir
    policy = _hold.barge_policy(item["device"], item=item, d=d)
    barge_need = _hold.need_of(policy)   # math.inf: --no-barge (a stop ask still cuts)
    barge_ignore = policy["ignore"]   # the record names it as it always has
    blog = BargeLog(item["utt"], "setting" if policy["ignore_from"] == "user" else "default")
    barge_trace: list = []
    counted = [[]]   # the user's words counted toward the cut, as of the last poll that weighed any
    cut_named: dict = {}   # the CUT_KEYS of the barge-in stop that cut it
    bduck: "Optional[_onset.BargeDuck]" = None   # dips the line at the user's voice onset
    nq = _box.Queue(d)
    next_look = [0.0]

    def check_stop() -> None:
        nonlocal reason
        due = time.monotonic() >= next_look[0]
        if due:
            next_look[0] = time.monotonic() + 0.1
            why = None if (d / "stop").exists() else _cut_by_mail(nq, item)
            if why:
                _write_stop(d, {"t": time.time(), "reason": why, "flush": False, "utt": item["utt"]})
        if first_frame_t is None and _superseded(nq, item):
            # amended before it was heard: it is closed unspoken, and the
            # amend, which carries its utt, plays next in its place
            reason = "amended"
            raise _Cut
        if first_frame_t is not None and barge_here:
            est = max(synth.frames / sr, len(text) / 15.0 / (speed or 1.0))
            words = _hold.barge(first_frame_t, _hold.near(text, written / sr, est),
                                sources=_hold.barge_sources_for(item["device"]),
                                words=barge_need, who=barge_who, ignore=barge_ignore,
                                trace=barge_trace)
            if barge_trace:   # every partial since the first frame, re-weighed: the whole count
                counted[0] = list(dict.fromkeys(w for e in barge_trace for w in e.get("counted") or ()))
            try:
                blog.emit(barge_trace)   # before the stop: the cut record precedes it
            except Exception:  # noqa: BLE001 - a record that cannot be written never changes the line
                pass
            if words is not None:
                # The user's words cut the line, and every line queued before them
                # (stale now); an opener queued after them survives.
                # the stop names the words that cut it, for this line and each it flushes
                # ``t`` stays the partial's ARRIVAL: a line queued
                # before the system KNEW the user's words could not have answered them. `barge`
                # counts only fresh partials, so arrival is within BARGE_FRESH_S of audio.
                _write_stop(d, {"t": _hold._ts(words) or time.time(), "reason": "barge-in",
                                "flush": True, "heard": str(words.get("text") or "")[:120],
                                **_cut_fields(barge_trace, policy)})
        stop = _read_stop(d)
        if stop:
            # A stop aimed at one line (retract, --now) cuts only that line;
            # a general stop cuts every line requested at or before it.
            hit = stop["utt"] == item["utt"] if stop.get("utt") else stop.get("t", 0) >= item["requested_t"]
            if hit:
                reason = stop.get("reason") or "stop"
                cut_named.update(_stop_cut_fields(stop))
                raise _Cut
        if due and on_poll is not None:
            try:
                on_poll()
            except Exception:  # noqa: BLE001 - a file that cannot start never stops the line
                pass

    lead, lead_from = _voices.lead_setting(item=item, d=d)   # 0 unless asked
    held = None   # wall seconds the lead held the first frame (saying.held_s)
    try:
        if lead > 0:
            # a clone renders barely faster than it plays, so a line
            # asked to hold waits for ``lead`` seconds of audio (or the whole
            # line, if shorter): a stall or a slow stretch then eats into the
            # lead, not into the line. Stops still cut it while it holds.
            hold_t0 = time.monotonic()
            need = int(round(lead * sr))
            while not synth.wait_for(need):
                check_stop()
            check_stop()
            first = synth.t_first if synth.t_first is not None else hold_t0
            held = round(max(0.0, time.monotonic() - max(hold_t0, first)), 3)
        for block in synth.blocks():
            if block is None:
                check_stop()
                continue
            for i in range(0, len(block), step):
                check_stop()
                if not started:
                    first_frame_t = time.time()
                    _mark_heard(d, item, first_frame_t)   # amend refuses it from now
                    _duck_on(None, d, line=item)   # a spoken line: the override and the host answer
                    if barge_here:
                        try:
                            bduck = _onset.for_line(out, item["device"], policy=policy,
                                                    words=lambda: counted[0])   # a capped duck's record
                        except Exception:  # noqa: BLE001 - no duck is today's line, never a lost one
                            bduck = None
                    if beds is not None:
                        beds.line_started()
                    _saying(item, text, **_common(item), device=getattr(out, "name", item["device"]),
                                 speed=speed, gen_s=synth.gen_s, prefetched=prefetched or None,
                                 lead_s=lead or None, lead_from=lead_from if lead else None, held_s=held,
                                 pan_ignored=getattr(out, "pan_ignored", None), level=level_rec)
                    started = True
                sub = block[i:i + step]
                if lev is not None:
                    sub = lev.apply(sub)
                out.write(sub if gain is None else (sub * gain).astype(sub.dtype, copy=False))
                written += len(sub)
        out.drain()
    except _Cut:
        synth.cancel()
        _abort(out)
    except Exception as e:  # noqa: BLE001
        synth.cancel()
        try:
            _abort(out)
        except Exception:  # noqa: BLE001
            pass
        name = type(e).__name__
        lost = isinstance(e, _output.DeviceLost) or "PortAudio" in name
        reason = "device-lost" if lost else "error"
        detail = f"{name}: {e}"[:300]
    finally:
        if bduck is not None:   # after the drain or the abort: a bed on the device comes back
            bduck.stop()

    cut = reason != "done"
    if bduck is not None:   # its duck record, after stop(): the counters are final
        try:
            _log_duck(item, out, bduck, reason)
        except Exception:  # noqa: BLE001 - a record that cannot be written never changes the line
            pass
    played = getattr(out, "frames_played", written) / sr
    if cut:
        played = max(0.0, played - getattr(out, "latency", 0.0))
    dur = synth.frames / sr if synth.finished else None
    est_dur = dur or max(synth.frames / sr, len(text) / 15.0 / (speed or 1.0))
    # The mouth keeps what it says (audio.py): the whole line, once its
    # synthesis finished, even if the playback was cut.
    kept = _audio.keep(item, synth.snapshot(), sr, backend.name) if synth.finished else None
    if kept:
        _level.after_kept(item, backend.name, level_rec, d=d)   # an unmeasured voice: measure it now
    if lev is not None and level_rec is not None:
        level_rec = {**level_rec, "limited": lev.limited}
    return _said(
        item, **_common(item), device=getattr(out, "name", item["device"]),
        audio=str(kept) if kept else None,
        played_s=round(played, 3), dur_s=round(dur, 3) if dur is not None else None,
        cut=cut, cut_at_s=round(played, 3) if cut else None,
        text_played_est=text if not cut else text_at(text, played / est_dur if est_dur else 0.0),
        underrun_s=round(getattr(out, "underrun_frames", 0) / sr, 3),
        underruns=getattr(out, "underruns", 0),
        # the device's own gaps (its callback ran late), beside the underruns
        device_gaps=getattr(out, "device_gaps", 0),
        device_gap_s=round(getattr(out, "device_gap_s", 0.0), 3),
        device_gap_max_s=round(getattr(out, "device_gap_max_s", 0.0), 3),
        reason=reason, detail=detail,
        # mixed over a bed: the music it played over; None is dropped
        over=out.over if isinstance(out, _output.MixLane) else None,
        level=level_rec,   # the voice's levelling; None (a file, silence) is dropped
        **(cut_named if reason == "barge-in" else {}))   # the words that cut it


def _next(d: Path, q: "_box.Queue", beds: "Optional[_bed.Beds]" = None) -> tuple[Optional[dict], bool]:
    """The first line that may play now, and whether any line is held back.

    A held line (hold.py) keeps its place and is skipped while the user talks; a
    line past its expiry is filed P with ``said reason=expired``, and a held
    line queued before the user's turn began is filed P with ``reason=stale`` (kept,
    never spoken). Lines with neither just play.

    A file whose device has a live bed (bed.py) keeps its place
    the same way and is skipped (``held``): a second file waits for its
    device's bed. Lines and files for other devices behind it still
    play. A line to a bed's device plays at once, duck on or off:
    ``open_output`` mixes it over the bed.

    Before it hands back a line, every eligible file queued behind it starts
    its bed (:func:`_leapfrog`): a file for an idle device never waits
    behind lines for another.
    """
    held = False
    playing = _read_playing(d)
    for x in q.lines(act=True, playing=[playing, *beds.items()] if beds and beds.any() else playing):
        if x.get("hold") or x.get("expires_t") is not None:
            verdict = _hold.check(x)
            if verdict in ("expire", "stale"):
                if q.drop_line(x, "P"):
                    item = _box.public(x)
                    _done(d, item, said_unplayed(item, "expired" if verdict == "expire" else "stale",
                                                 detail=getattr(verdict, "detail", None)))
                continue
            if verdict != "play":
                held = True
                continue
        bed = beds.on(x) if beds else None
        if bed is None:
            if not _bed.is_file(x):
                _leapfrog(d, q, beds, x)   # files for idle devices first
            return x, held
        if not _bed.is_file(x):
            if bed.ready.is_set():   # its mixer is open: open_output mixes the line in
                _leapfrog(d, q, beds, x)
                return x, held
            held = True              # milliseconds: never a second output beside it
            continue
        held = True
    return None, held


def _playable(x: dict, beds: "Optional[_bed.Beds]") -> bool:
    """``_next``'s own test, read-only: would it hand ``x`` out now? Not a line
    held for the user's turn (or past its moment: ``_next`` files that one), and not one
    waiting on its device's bed (a file, or a line whose bed's mixer is not
    open yet). Nothing is filed here."""
    if (x.get("hold") or x.get("expires_t") is not None) and _hold.check(x) != "play":
        return False
    bed = beds.on(x) if beds else None
    if bed is None:
        return True
    return bool(not _bed.is_file(x) and bed.ready.is_set())


def _leapfrog(d: Path, q: "_box.Queue", beds: "Optional[_bed.Beds]", playing: Optional[dict]) -> list:
    """Start a bed for every ELIGIBLE queued file, in playlist order: no device
    waits on another.
    The queue is one playlist across all devices, so without this a
    laptop Tink queued behind call lines waits for every one of them.

    ``playing`` is the line playing now (``play_one``'s poll), or the one
    ``_next`` is about to hand back. A file is eligible when its device

    1. has no live bed (one bed per device: a second file waits);
    2. is not ``playing``'s device (a line that has opened its own output takes
       no mixer mid-line); and
    3. has no PLAYABLE line queued ahead of it (:func:`_playable`, ``_next``'s
       own test): same-device order is kept, and a held line keeps its place
       and is skipped, as ``_next`` skips it.

    A file that is itself held (or past its moment) is left to ``_next``. Its
    ``saying`` carries ``leapfrog: true`` when it jumped a playable line queued
    ahead of it. Returns the beds started."""
    if beds is None:
        return []
    queued = q.lines()    # read-only: the player's own _next acts (files, drops)
    if not any(_bed.is_file(x) for x in queued):
        # the common case, every 100 ms on the line's write path: nothing to
        # start, so no device look-up (a "call"/"default" key can probe)
        return []
    busy = {_bed.device_key(playing.get("device"))} if playing else set()
    jumped = False
    out = []
    for x in queued:
        key = _bed.device_key(x.get("device"))
        if not _bed.is_file(x):
            if _playable(x, beds):
                busy.add(key)
                jumped = True
            continue
        if (x.get("hold") or x.get("expires_t") is not None) and _hold.check(x) != "play":
            continue
        if key in busy or beds.on(x) is not None:
            busy.add(key)
            continue
        item = _voices.fill_item(_box.public(x), d)
        q.ack(x)            # a mail from outside: its queued root, as _next's act gives it
        taken = q.take(x)
        if taken is None:   # retracted or taken meanwhile
            continue
        out.append(beds.start(item, taken, leapfrog=jumped))
        busy.add(key)
    return out


def _read_playing(d: Path) -> Optional[dict]:
    try:
        return json.loads((d / "playing.json").read_text())
    except (FileNotFoundError, ValueError):
        return None


def _flags(rec: dict) -> str:
    """S if any of it was heard; P if it passed its moment; else T."""
    if (rec.get("played_s") or 0) > 0:
        return "S"
    return "P" if rec.get("reason") in ("expired", "stale") else "T"


#: ``resume_why`` for a replay cut again: the once-only rule, in the notice's words.
NEVER_TWICE = "a replay is never replayed"
#: ``resume_why`` for a say-again replay the user's words cut: the user heard it again,
#: and a cut one stays cut. Asked BEFORE the played-file check, which its ``file`` would
#: otherwise answer first as "it is a played file".
AGAIN_NOT_RESUMED = "a say-again replay is not resumed"
#: How much of the cut line's text its replay carries, for the replay's notices.
RESUME_TEXT_MAX = 200


def _plan(item: dict, rec: dict) -> dict:
    """The replay decision for a line the user's words cut, made ONCE, before its notice:
    ``_said`` writes it on the said row and the notice is written from
    it, so the words cannot disagree with the act; ``_resume`` then acts on it and
    never decides again. ``{"resume": "will", "resume_utt": <the replay's utt,
    minted now>, "resume_s": s}`` or ``{"resume": "won't", "resume_why": why}``.
    Never raises: a decision that cannot be made is a ``won't``."""
    try:
        if _hold.resume_s() <= 0:
            return {"resume": "won't", "resume_why": "replay is off (resume_s is 0)"}
        if item.get("again_of"):
            return {"resume": "won't", "resume_why": AGAIN_NOT_RESUMED}
        if item.get("backend") == "file" or item.get("file"):
            return {"resume": "won't", "resume_why": "it is a played file"}
        if item.get("resume_of"):
            # once: a resumed line the user cuts again is dropped, never resumed again
            # (else one long sentence restarts from its start after each of the user's
            # cuts)
            return {"resume": "won't", "resume_why": NEVER_TWICE}
        since = time.time() - float(rec.get("played_s") or 0) - 0.5     # the line's first frame
        if _hold.asked_for_quiet(since, str(item.get("text") or ""),
                                 sources=_hold.barge_sources_for(item.get("device"))):
            # the user asked for quiet, not a pause: the cut stands
            return {"resume": "won't", "resume_why": _hold.QUIET}
        if not _hold.rest_of(str(item.get("text") or ""), rec.get("text_played_est") or ""):
            return {"resume": "won't", "resume_why": "nothing of it was left unsaid"}
        import uuid

        return {"resume": "will", "resume_utt": uuid.uuid4().hex[:12], "resume_s": _hold.resume_s()}
    except Exception as e:  # noqa: BLE001 - a decision must never stop the player
        return {"resume": "won't", "resume_why": f"the mouth could not decide ({type(e).__name__})"}


def _resume(d: Path, item: dict, rec: dict) -> None:
    """The fast follow: a line the user's words cut is
    queued again from the start of the sentence it was cut in, HELD for the
    end of the user's turn and expiring in resume_s(). A short turn then hears it
    with no agent round trip; its agent may amend or retract it meanwhile
    (``resume_of`` names the cut line).

    It ACTS on the decision ``_said`` wrote on the row (:func:`_plan`),
    under the utt the notice named; a row with no decision (``rec`` not from
    ``_said``) is decided here, once."""
    plan = rec if rec.get("resume") else _plan(item, rec)
    if plan.get("resume") != "will":
        return   # once only: a replay's plan is always won't (NEVER_TWICE)
    rest = _hold.rest_of(item["text"], rec.get("text_played_est") or "")
    from . import say

    # the level it was cut at comes back with it. Given, say() reads
    # no cascade, so the player runs no `session settings` (under ITS session) here.
    level = _voices._pct(item.get("volume"))
    carry = {"volume_from": item.get("volume_from") or "flag"} if level is not None else {}
    try:
        say(rest, voice=item.get("voice"), speed=item.get("speed"),
            backend=item.get("backend_asked") or item.get("backend"), device=item.get("device"),
            pan=item.get("pan"), log_dir=Path(item["log_dir"]) if item.get("log_dir") else None,
            d=d, spawn=False, hold="turn-end", expires_s=plan.get("resume_s") or _hold.resume_s(),
            volume=level,
            extra={"agent": item.get("agent"), "session": item.get("session"),
                   # the utt its notice named, and the cut line it names back
                   "utt": plan["resume_utt"], "resume_of": item["utt"],
                   "resume_text": str(item.get("text") or "")[:RESUME_TEXT_MAX], **carry,
                   # how much of it played: hold's echo test reads the user's turn against
                   # only what the mic could have heard (hold._heard_window)
                   **({"resume_played": str(rec.get("text_played_est") or ""),
                       "resume_played_s": rec["played_s"]}
                      if rec.get("played_s") is not None else {}),
                   # its own barge count (--barge-words/--no-barge), and its device's rung
                   **{k: item[k] for k in ("barge_words", "barge_words_from", "device_from")
                      if k in item},
                   # its levelling as its caller answered it; else the host's, at take
                   **{k: item[k] for k in ("normalize", "normalize_from", "normalize_lufs",
                                           "normalize_lufs_from") if k in item},
                   # a reply's rest stays in its thread: no second lookup
                   **{k: item[k] for k in _box.REPLY if item.get(k)}})
    except (ValueError, OSError) as e:  # a resume must never stall the player
        print(json.dumps({"resume_failed": item["utt"], "error": str(e)[:200]}), flush=True)
        # its notice said WILL: the second notice says it was dropped
        from . import notice

        notice.dropped({**item, "utt": plan["resume_utt"], "text": rest, "wait": False,
                        "resume_of": item["utt"], "resume_text": str(item.get("text") or "")},
                       {"reason": "resume-failed", "cut": True, "played_s": 0.0,
                        "detail": str(e)[:200]})


def _write_stop(d: Path, rec: dict) -> None:
    """The player's own stop (barge-in): the same file `mouth stop` writes.

    The part file is per THREAD: a bed's barge test and a line's over it can
    write at the same moment, and a shared name loses one rename."""
    part = d / f".stop.player.{os.getpid()}.{threading.get_ident()}.part"
    part.write_text(json.dumps(rec))
    part.rename(d / "stop")


def _handle_stop(d: Path, q: "Optional[_box.Queue]" = None, beds: "Optional[_bed.Beds]" = None) -> None:
    """After a cut or while idle: flush what the stop covers, then retire it.

    Every live bed hears the stop BEFORE it is unlinked (``mouth stop`` stops
    the music too); a bed's own poll also reads it meanwhile."""
    stop = _read_stop(d)
    if not stop:
        return
    if beds is not None:
        beds.on_stop(stop)
    if stop.get("flush", True) and not stop.get("utt"):
        q = q or _box.Queue(d)
        barge = stop.get("reason") == "barge-in"
        for x in q.lines():
            if x.get("requested_t", 0) > stop.get("t", 0):
                continue
            if barge and _is_music(x):
                continue   # music, not stale words: the user's words never drop it
            if q.drop_line(x, "T"):
                item = _box.public(x)
                _done(d, item, said_unplayed(item, stop.get("reason") or "stop",
                                             cut_named=_stop_cut_fields(stop)))   # the words that cut it
    (d / "stop").unlink(missing_ok=True)


def _is_music(x: dict) -> bool:
    """A queued played file: a barge-in flush spares it, duck on or off
    (every played file, not only a duck-on one)."""
    return _bed.is_file(_box.public(x))


def _done(d: Path, item: dict, rec: dict) -> None:
    if item.get("wait"):
        p = d / "done" / f"{item['utt']}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(rec))
        tmp.rename(p)


#: The said detail of a line recovered from a dead player.
LOST_DETAIL = ("the player stopped before it filed this line (crash, kill or reboot); "
               "how much was heard is unknown")
#: A ``done/<utt>.wait`` older than this and held by no one is a killed waiter's:
#: ``mouth.say``'s default wait timeout.
STALE_WAIT_S = 300.0


def recover(d: Path, q: "_box.Queue") -> int:
    """Lines a dead player took (``cur/NAME:2,``, never filed): each filed T and
    said ``lost``. Returns how many.

    ``Queue.lines`` calls it once per Queue, on the first ``act=True``, which
    only the lock holder makes, before it takes anything: so every unflagged
    line in ``cur/`` is a dead player's, and ``playing.json`` is stale. A live
    waiter gets its done file; a young line's sender is mailed (``notice``
    decides by age). ``bed.json`` is the bed's and is left alone. Each file
    is handled in its own ``try``: recovery never raises."""
    n = 0
    try:
        names = sorted(os.listdir(q.bx / "cur"))
    except OSError:
        names = []
    for name in names:
        if not name.endswith(":2,"):
            continue
        path = q.bx / "cur" / name
        try:
            try:
                x = _box.read(path, d)
            except Exception as e:  # noqa: BLE001 - X-Mouth-Requested: nan raises today
                if _box.file_to(path, "T"):
                    print(json.dumps({"recover_failed": name, "error": f"{type(e).__name__}: {e}"[:200]}),
                          flush=True)
                continue
            if x is None or _box.file_to(path, "T") is None:
                continue
            item = _box.public(x)
            _done(d, item, said_unplayed(item, "lost", detail=LOST_DETAIL))
            n += 1
        except Exception as e:  # noqa: BLE001 - recovery must never stop the player
            print(json.dumps({"recover_failed": name, "error": f"{type(e).__name__}: {e}"[:200]}), flush=True)
    try:
        (d / "playing.json").unlink(missing_ok=True)
    except OSError:
        pass
    _sweep_waits(d)
    return n


def _sweep_waits(d: Path) -> None:
    """Unlink ``done/*.wait`` files left by killed waiters: older than
    ``STALE_WAIT_S`` AND not locked. A held one is a live waiter's, whatever its age."""
    import stat  # here: the module's top-level imports stay as they are

    try:
        names = os.listdir(d / "done")
    except OSError:
        return
    cutoff = time.time() - STALE_WAIT_S
    for name in names:
        if not name.endswith(".wait"):
            continue
        p = d / "done" / name
        try:
            fd = os.open(str(p), os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        except OSError:
            continue
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_mtime > cutoff:
                continue
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                continue  # held: a waiter is live
            p.unlink(missing_ok=True)
        except OSError:
            pass
        finally:
            os.close(fd)


_warned = False


def _warn_retired() -> None:
    """Once per process, when it first holds the player lock: a ``kind: warn``
    record on player.log for each barge warning (``hold.barge_warnings``:
    a $MOUTH_BARGE_WORDS that is not a count). It never stops the mouth."""
    global _warned
    if _warned:
        return
    _warned = True
    try:
        for rec in _hold.barge_warnings():
            _log(rec)
    except Exception:  # noqa: BLE001 - a warning never stops the player
        pass


def serve(d: Optional[Path] = None, idle_exit_s: Optional[float] = None, poll_s: float = 0.02) -> int:
    """Play the queue until it has been empty ``idle_exit_s``. 0 if another player holds the lock."""
    d = d or mouth_dir()
    d.mkdir(parents=True, exist_ok=True)
    q = _box.Queue(d)
    _box.ensure(q.bx)
    pq = _box.Queue(d)  # prefetch's own view (it runs on synth threads too)
    beds: Optional[_bed.Beds] = None   # made once this player holds the lock (below)
    if idle_exit_s is None:
        idle_exit_s = float(envnames.get("MOUTH_IDLE_S", "600"))
    while True:
        lock_fd = os.open(str(d / "player.lock"), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(lock_fd)
            return 0
        (d / "player.pid").write_text(str(os.getpid()))
        _warn_retired()
        if beds is None:
            # played files, one per device, each on its own thread. Only
            # the lock holder may clear bed.json: a second serve that loses the race
            # must leave the live player's beds alone.
            beds = _bed.Beds(d)
        idle_since = time.monotonic()
        ahead: dict = {"synth": None, "playing": None}
        guard = threading.Lock()

        def prefetch(_finished: Optional[Synth] = None) -> None:
            """Start synthesising the next queued item, one ahead, never the one playing."""
            with guard:
                if ahead["synth"] is not None:
                    return
                for x in pq.lines():
                    item = _box.public(x)
                    if item.get("utt") == ahead["playing"] or _bed.is_file(item):
                        continue   # a bed decodes its own file, ~2 s ahead
                    try:
                        ahead["synth"] = Synth(item, on_done=prefetch)
                    except Exception:  # noqa: BLE001 - play_one reports it when its turn comes
                        pass
                    return

        try:
            while True:
                f, held = _next(d, q, beds)
                if f is None:
                    beds.lines_done()   # nothing playable: the beds' say cause lets go
                if f is None and held:
                    # Lines wait for the end of the user's turn (or for a bed on their
                    # device): keep their synthesis warm, honour a stop, and do
                    # not idle out.
                    _handle_stop(d, q, beds)
                    prefetch()
                    idle_since = time.monotonic()
                    time.sleep(poll_s)
                    continue
                if f is None:
                    if not beds.any():   # a bed playing keeps the DJ down, as a file did
                        _duck_off()
                    with guard:
                        if ahead["synth"] is not None:  # its item was flushed by a stop
                            ahead["synth"].cancel()
                            ahead["synth"] = None
                    _handle_stop(d, q, beds)
                    if beds.any():
                        idle_since = time.monotonic()   # never idle out under the music
                    elif time.monotonic() - idle_since > idle_exit_s:
                        break
                    time.sleep(poll_s)
                    continue
                # a mail from outside has no caller to read a cascade for: its level
                # (and a file's duck) come from the host file now
                item = _voices.fill_item(_box.public(f), d)
                if _bed.is_file(item):
                    # A played file (any duck setting) leaves the speech queue as a
                    # bed on its device, and the loop goes straight back to the
                    # queue: nothing for another device waits behind it.
                    taken = q.take(f)
                    if taken is not None:
                        beds.start(item, taken)
                    idle_since = time.monotonic()
                    continue
                with guard:
                    synth, ahead["synth"] = ahead["synth"], None
                    ahead["playing"] = item.get("utt")
                if synth is not None and (synth.utt != item.get("utt") or any(
                        synth.item.get(k) != item.get(k) for k in _SYNTH_KEYS)):  # amended
                    synth.cancel()
                    synth = None
                playing = d / "playing.json"
                taken = q.take(f)
                if taken is None:  # retracted or flushed meanwhile
                    if synth is not None:
                        synth.cancel()
                    continue
                tmp = d / ".playing.json.part"
                tmp.write_text(json.dumps({**item, "box_file": str(taken)}))
                tmp.rename(playing)
                if synth is None:
                    try:
                        synth = Synth(item, on_done=prefetch)
                    except Exception:  # noqa: BLE001 - play_one makes the said line
                        synth = None
                    prefetched = False
                else:
                    prefetched = True
                if synth is not None and synth.finished:
                    prefetch()
                try:
                    # files for idle devices start while the line plays
                    rec = play_one(item, d, synth, prefetched, beds=beds,
                                   on_poll=functools.partial(_leapfrog, d, q, beds, item))
                except Exception as e:  # noqa: BLE001 - one bad utterance must not stall the queue
                    rec = said_unplayed(item, "error", detail=f"{type(e).__name__}: {e}"[:300])
                _box.file_to(taken, _flags(rec))
                playing.unlink(missing_ok=True)
                _done(d, item, rec)
                if rec.get("cut"):
                    _handle_stop(d, q, beds)
                if rec.get("reason") == "barge-in":
                    _resume(d, item, rec)
                print(json.dumps({k: rec.get(k) for k in ("seq", "utt", "reason", "played_s", "dur_s")}),
                      flush=True)
                idle_since = time.monotonic()
        finally:
            (d / "player.pid").unlink(missing_ok=True)
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        # A say that landed while we were letting go saw the lock held and did
        # not start a player: look once more before leaving.
        if not q.lines():
            return 0


if __name__ == "__main__":
    sys.exit(serve())
