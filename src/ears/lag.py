"""lag - when the audio behind a heard record was SPOKEN, and how late it was written.

A heard record's ``ts`` is the wall time it was WRITTEN. Its ``t0``/``t1`` are
STREAM seconds, counted from the capture's first frame. When the recogniser
falls behind, the two come apart: on 10-07 partials were written 64-108 s after
their audio, and the mouth's barge-in took them for fresh talk-over.

So the capture stamps each ``partial``, ``turn``, ``turn-discarded``, ``vad``
and ``heartbeat`` it writes with two fields:

- ``audio_ts``: the wall time of the END of the record's audio, an ISO string in
  the same format as ``ts``. It is ``wall0 + t1`` for a partial or turn,
  ``wall0 + at`` for a ``vad`` edge and ``wall0 + stream_s`` for a heartbeat.
  ``wall0`` is the wall time of the capture's ``listen-started``.
- ``lag_s``: the write's wall time minus ``audio_ts``, in seconds. A partial's is the
  recogniser's lag (plus the chunker's ~0.35 s quiet cut). A turn's also includes the
  detector's ``silence_s`` (2 s) wait for quiet after the last word, by construction, so
  a lag alarm reads partials and heartbeats, never turns.

This is the MINIMAL clock: ``listen-started`` plus stream
time. Audio the device dropped does not advance stream time, so after a drop
``audio_ts`` reads EARLY and ``lag_s`` reads LARGE (toward stale, never toward
fresh). The mic counts its drops on every heartbeat (``input_overflows``), so a
lag figure taken after one can be distrusted.

Records without the fields (old ears, a non-real-time source, another writer)
read as None here. A reader then falls back to what it did before.

``LAG_WARN_S`` is the recogniser-lag alarm threshold. It lives here, in one
place, because both sides read it: the ears raise the alarm (:class:`LagAlarm`:
a ``recogniser-lag`` event once per episode, which the keeper mails),
and the mouth's barge-in freshness bound (``hold.BARGE_FRESH_S``) must
sit above it, so that every word barge-in drops for lateness comes after a
visible alarm.

Standard library only, like :mod:`ears.heard`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from . import envnames

AUDIO_TS = "audio_ts"
LAG_S = "lag_s"

# Above the 1.2 s healthy
# ceiling measured on 10-07, below the 4 s barge-in freshness bound.
LAG_WARN_S = 2.0


def lag_warn_s() -> float:
    """The alarm threshold: ``$EARS_LAG_WARN_S``, else :data:`LAG_WARN_S`."""
    try:
        return float(envnames.get("EARS_LAG_WARN_S", LAG_WARN_S))
    except ValueError:
        return LAG_WARN_S


def iso(t: float) -> str:
    """Epoch seconds as ``heard`` writes ``ts``: local time, milliseconds, with offset."""
    return datetime.fromtimestamp(t).astimezone().isoformat(timespec="milliseconds")


def stamp(spoken_t: float, now: float) -> dict:
    """The two fields for a record whose audio ended at ``spoken_t``, written at ``now``."""
    return {AUDIO_TS: iso(spoken_t), LAG_S: round(now - spoken_t, 3)}


def audio_t(rec: dict) -> Optional[float]:
    """The record's ``audio_ts`` as epoch seconds; None when it has none or it is bad."""
    try:
        return datetime.fromisoformat(rec[AUDIO_TS]).timestamp()
    except (KeyError, TypeError, ValueError):
        return None


def lag_of(rec: dict) -> Optional[float]:
    """The record's ``lag_s``; None when it has none or it is not a number."""
    v = rec.get(LAG_S)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


# -- the recogniser-lag alarm ------------

LAG_ALARM = "recogniser-lag"      # the capture fell behind: one per episode
LAG_ALARM_OK = "recogniser-lag-ok"  # it caught up again: the episode's end
# Hysteresis: the alarm clears only once every reading is this far under the threshold,
# so a lag that hovers around it is one episode, not an alarm per heartbeat.
LAG_HYSTERESIS_S = 0.5


def lag_clear_s(threshold_s: float) -> float:
    """The level every reading must be under for the alarm to clear: the threshold less
    :data:`LAG_HYSTERESIS_S`, or less half of it when the threshold is that small."""
    return threshold_s - min(LAG_HYSTERESIS_S, threshold_s / 2)


class LagAlarm:
    """When the recogniser is behind its audio, say so ONCE per episode.

    Two readings feed it, each the newest of its kind (a turn's ``lag_s`` never does:
    it includes the detector's wait for quiet, by construction):

    - ``partial``: a partial's ``lag_s``, how late the recogniser wrote words;
    - ``heartbeat``: the larger of a heartbeat's ``lag_s`` (the frame loop) and its
      ``stt_backlog_s`` (the oldest chunk waiting on the STT). It sees a backlog while
      the user is silent, which no partial can.

    The alarm is RAISED when either reading is over the threshold, and CLEARS when the
    newest reading of each kind is under :func:`lag_clear_s`. So a sustained lag is one
    ``recogniser-lag`` event and, when it ends, one ``recogniser-lag-ok``: never one per
    record. A late partial stays the newest partial reading until the next partial, so
    the alarm can outlast the lag until the user speaks again, unless a heartbeat says no
    words are waiting (``words_waiting=False``, chunk mode with nothing pending): then
    the stale partial reading is dropped. A streaming engine's own queue cannot be seen
    from a heartbeat, so there only a fresh partial clears it.

    Pure: no I/O, no clock of its own. Each call returns None, or ``(event, fields)``
    for the caller to write.
    """

    def __init__(self, threshold_s: Optional[float] = None) -> None:
        self.threshold_s = lag_warn_s() if threshold_s is None else float(threshold_s)
        self.clear_s = lag_clear_s(self.threshold_s)
        self._latest: dict[str, float] = {}
        self._backlog: Optional[float] = None
        self._since: Optional[float] = None  # wall time the open episode was raised
        self._worst = 0.0

    @property
    def raised(self) -> bool:
        return self._since is not None

    def partial(self, lag_s: Optional[float], now: float) -> Optional[tuple[str, dict]]:
        """A partial was written ``lag_s`` after its audio ended."""
        if lag_s is None:
            return None
        return self._observe("partial", lag_s, now)

    def heartbeat(self, lag_s: Optional[float], stt_backlog_s: Optional[float], now: float,
                  *, words_waiting: bool = True) -> Optional[tuple[str, dict]]:
        """A heartbeat's loop lag and STT backlog. ``words_waiting=False`` says no words can
        still be on their way (chunk mode, nothing pending): an old partial reading goes."""
        vals = [v for v in (lag_s, stt_backlog_s) if v is not None]
        if not vals:
            return None
        self._backlog = stt_backlog_s
        if not words_waiting:
            self._latest.pop("partial", None)
        return self._observe("heartbeat", max(vals), now)

    def _observe(self, on: str, value: float, now: float) -> Optional[tuple[str, dict]]:
        self._latest[on] = value
        if self._since is None:
            if value > self.threshold_s:
                self._since, self._worst = now, value
                fields = {"lag_s": round(value, 3)}
                if self._backlog is not None:
                    fields["stt_backlog_s"] = round(self._backlog, 3)
                return LAG_ALARM, {
                    **fields,
                    "threshold_s": self.threshold_s,
                    "since": iso(now),
                    "on": on,
                    "text": f"recogniser {value:.1f} s behind its audio (alarm over {self.threshold_s:g} s)",
                }
            return None
        self._worst = max(self._worst, value)
        if all(v < self.clear_s for v in self._latest.values()):
            since, worst = self._since, self._worst
            self._since, self._worst = None, 0.0
            return LAG_ALARM_OK, {
                "lag_s": round(value, 3),
                "max_lag_s": round(worst, 3),
                "threshold_s": self.threshold_s,
                "since": iso(since),
                "lasted_s": round(now - since, 3),
                "on": on,
                "text": f"recogniser caught up after {now - since:.0f} s (worst {worst:.1f} s behind)",
            }
        return None
