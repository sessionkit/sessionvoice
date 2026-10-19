"""Speaker labels for the capture's own partials: split a turn where the speaker changes.

When a speaker name changes, the turn of the one before ends, for any
named person, not just one.

The speaker side-car (ears ``speakers/``) writes ``speakers_YYYY-MM-DD.jsonl`` beside the
heard log: one row per heard line, keyed by ``seq``, about half a second after the line.
The capture reads the rows for its OWN open partials and, when another named person holds
K of them in a row, closes the turn before the first of those (see :func:`first_cut`).
A turn no split named is named by the same rule from its own partials (see
:func:`turn_speaker`): the split alone left nearly every turn with no speaker.

Read-only, and it never breaks the capture: a missing file, a half-written last line, a
row that is not JSON or not a label, a file that was cut short, all read as "no label",
which leaves the turn exactly as it is without speaker names.
"""
from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path
from typing import Callable, Iterator, NamedTuple, Optional, Sequence

from mouth.hold import is_echo

from .. import heard
from .missed import HeardTail

# Labels that name nobody: they never set a turn's speaker, never count toward a change,
# and never reset one. "other" is any unenrolled voice (it may be several people).
NEUTRAL = frozenset({"other", "mixed", "silent", "unheard"})
DEFAULT_K = 2  # no split on a single label


def _holders(labels: Sequence[Optional[str]], k: int, agent: Optional[Sequence[bool]] = None,
             echo: Optional[Sequence[bool]] = None, held: Optional[str] = None) -> Iterator[tuple[int, str]]:
    """(index, name) each time a name takes the turn: the split's one rule, shared by
    :func:`first_cut` (the 2nd holder is a change) and :func:`turn_speaker` (the 1st is
    whose turn it is).

    A turn's speaker is set once K labels of one name come in a row (neutral labels and
    unlabelled partials are skipped, not counted, and never reset a run). After that, K
    labels in a row of ANOTHER name take it over, from the first of them.

    The one exception: the mouth's agent yields to ONE named person. ``agent[j]``
    is True when the side-car gave label j by mouth attribution (the line was inside a
    playing mouth line and its words were mostly that line's), not by voice. While the
    holder is such an agent label, ONE partial labelled with a named person (a name, not
    neutral, not by mouth) takes the turn over (K=1), unless ``echo[j]`` says that
    partial's text is the line's own echo (:func:`mouth.hold.is_echo`; the voice matcher
    scores the line's last word as the person, 3 times in 12 lines on 2030-06-09). Every
    other edge keeps K: the first holder, person to agent, person to person. K=2 guards
    against a stray diarizer label flapping between two enrolled VOICES; an agent label is
    not a diarizer guess. ``agent`` and ``echo`` None: no label is the mouth's, as before.

    ``held``: the turn already has a holder from index 0 (the speaker a split opened this
    turn with, yielded first). Without it a single person partial opening the remainder
    of a cut, followed by two agent labels, is taken back into the agent's turn.
    """
    if k < 1:
        return
    cur: Optional[str] = None
    by_mouth = False  # the holder's latest label was the mouth's agent
    if held is not None and held not in NEUTRAL:
        cur = held
        yield 0, held
    run: Optional[tuple[str, int, int]] = None  # (name, first index, count)
    for j, s in enumerate(labels):
        if s is None or s in NEUTRAL:
            continue
        mouth_j = bool(agent is not None and agent[j])
        if s == cur:
            run, by_mouth = None, mouth_j
            continue
        if by_mouth and not mouth_j and not (echo is not None and echo[j]):
            yield j, s
            cur, by_mouth, run = s, False, None
            continue
        run = (s, run[1], run[2] + 1) if run and run[0] == s else (s, j, 1)
        if run[2] >= k:
            yield run[1], s
            cur, by_mouth, run = s, mouth_j, None


def first_cut(labels: Sequence[Optional[str]], k: int = DEFAULT_K, *, agent: Optional[Sequence[bool]] = None,
              echo: Optional[Sequence[bool]] = None,
              held: Optional[str] = None) -> Optional[tuple[int, str, str]]:
    """(index, before, after) of the first speaker change in one open turn, or None.

    A turn's speaker is set once K labels of one name come in a row (neutral labels and
    unlabelled partials are skipped, not counted). After that, K labels in a row of
    ANOTHER name are a change, cut at the first of them. So a turn that opens with one
    stray label and then is all one name's is that name's, with no cut. Replay of 2030-06-09 at
    K=2: 779 turns, 16 split, 29 cuts (agent->user 9 of them: an echo opening the user's turn).

    ``agent``, ``echo`` and ``held``: see :func:`_holders`. A change at index 0
    (only a ``held`` holder the labels contradict) is no cut: there is nothing before it.
    """
    held_by = _holders(labels, k, agent, echo, held)
    first = next(held_by, None)
    change = next(held_by, None)
    if first is None or change is None or change[0] < 1:
        return None
    return change[0], first[1], change[1]


def turn_speaker(labels: Sequence[Optional[str]], k: int = DEFAULT_K, *, agent: Optional[Sequence[bool]] = None,
                 echo: Optional[Sequence[bool]] = None) -> Optional[str]:
    """Whose turn this is, from its partials' labels, or None.

    The split writes a ``speaker`` only on a turn it splits, so an unsplit turn (nearly every
    turn) carried none and ears-mail mailed it as its default speaker. This names the
    unsplit turn by the same rule (:func:`_holders`, with its ``agent``/``echo``):

    - one name took the turn (K in a row) and nobody took it over: that name. A stray
      label of another name does not change it, exactly as it does not split it.
    - a turn too short to reach K AT ALL (fewer than K partials: a 1-partial turn at
      K=2): its named labels, if they are all ONE name. The label is still the side-car's
      own word for that voice and nothing contradicts it; a single ``bob`` partial is far
      better evidence than ears-mail's blanket default. Two names: a conflict, None.
    - a turn of K or more partials that no name held K in a row: None, however its
      labels fall. As there is no split on a single label, a long,
      mostly-neutral turn (the TV) is not named by one label in it.
      10-03 seq 189201 ``osoooomooosoo`` was named by the old
      any-length fallback; it is None.
    - two names each took the turn (a change the split had not cut when the turn
      closed, the mouth's agent then one person included): None. Naming either would
      put one speaker's name on the other's words.
    - only neutral labels (other/mixed/silent/unheard: the TV, an unknown voice) or no
      labels at all: None. A neutral label never names a turn.
    """
    if k < 1:
        return None
    held = list(_holders(labels, k, agent, echo))
    if len(held) == 1:
        return held[0][1]
    if held or len(labels) >= k:
        return None
    named = {s for s in labels if s is not None and s not in NEUTRAL}
    return named.pop() if len(named) == 1 else None


class Label(NamedTuple):
    """One side-car row as the split rule reads it: the name, whether the side-car gave
    it by mouth attribution (``speaker_by: "mouth"``), and the mouth line the
    row's words fell in (``mouth_utt``), if any."""
    speaker: str
    by_mouth: bool = False
    mouth_utt: Optional[str] = None


class LineTexts:
    """utt -> the text of the mouth line, from the ``saying`` rows the mouth writes into
    the heard log the capture writes. Read incrementally
    (:class:`~ears.listen.missed.HeardTail`), never the whole file again; ``keep`` bounds
    the map (oldest lines go first). A line that started before the capture is unknown."""

    def __init__(self, path: Callable[[], Path], keep: int = 256) -> None:
        self._tail, self.keep = HeardTail(path, heard.SAYING), keep
        self._text: OrderedDict[str, str] = OrderedDict()

    def __call__(self, utt: str) -> Optional[str]:
        for rec in self._tail():
            u, text = rec.get("utt"), rec.get("text")
            if isinstance(u, str) and u and isinstance(text, str):
                self._text[u] = text
                self._text.move_to_end(u)
        while len(self._text) > self.keep:
            self._text.popitem(last=False)
        return self._text.get(utt)


def guard_flags(rows: Sequence[Optional[Label]], texts: Sequence[str],
                line_text: Callable[[str], Optional[str]]) -> tuple[list[bool], list[bool]]:
    """(agent, echo) per partial, for :func:`first_cut` / :func:`turn_speaker`.

    ``agent[j]``: the side-car named partial j by mouth attribution. ``echo[j]``, worked
    out only for a NAMED PERSON's partial (the only one the K=1 edge can take): its text
    is the echo of the line its words fell in, ``is_echo(text, line)`` (imported from
    :mod:`mouth.hold`, never copied). No ``mouth_utt``: not echo. A ``mouth_utt`` whose
    line text is unknown (spoken before the capture started, or aged out): echo, so the
    turn keeps today's K, never a cut it cannot check.
    """
    agent = [bool(r is not None and r.by_mouth) for r in rows]
    echo = [False] * len(rows)
    for j, r in enumerate(rows):
        if r is None or r.by_mouth or r.speaker in NEUTRAL or r.mouth_utt is None:
            continue
        line = line_text(r.mouth_utt)
        echo[j] = True if line is None else is_echo(texts[j] if j < len(texts) else "", line)
    return agent, echo


def read_turn(tail, seqs: Sequence[int], texts: Sequence[str]) -> tuple[list[Optional[str]], dict]:
    """The labels for one turn's partials (``seqs``, their ``texts``), and the mouth
    keyword arguments for :func:`first_cut` / :func:`turn_speaker` (``agent``, ``echo``).
    A reader with no :meth:`LabelTail.read` (labels only): no keywords, today's rule."""
    read = getattr(tail, "read", None)
    if read is None:
        return tail.labels(seqs), {}
    rows = read(seqs)
    agent, echo = guard_flags(rows, texts, getattr(tail, "line_text", lambda utt: None))
    return [r.speaker if r is not None else None for r in rows], {"agent": agent, "echo": echo}


class LabelTail:
    """seq -> speaker, from the side-car's file for today, read incrementally.

    With each name it keeps the row's mouth marker (``speaker_by: "mouth"``) and
    ``mouth_utt`` (:meth:`read`), and ``lines`` (a :class:`LineTexts`, or any
    ``utt -> text or None``) gives the mouth line's text for the echo guard
    (:meth:`line_text`); no ``lines``: every line is unknown.

    ``path`` is called on every read (the day rolls over at midnight). Only whole lines
    are taken, so a row being written is read next time. Only a file that SHRANK, or a
    new path, is read again from the start: a file replaced by a longer one is not
    detected, and its rows up to the old offset are missed (fails safe as "no label";
    the side-car only appends). ``keep`` bounds the map (oldest seqs go first).
    """

    def __init__(self, path: Callable[[], Path], keep: int = 4096,
                 lines: Optional[Callable[[str], Optional[str]]] = None) -> None:
        self.path, self.keep, self.lines = path, keep, lines
        self._file: Optional[Path] = None
        self._offset = 0
        self._seen: OrderedDict[int, Label] = OrderedDict()

    def _read(self) -> None:
        p = self.path()
        if p != self._file:
            self._file, self._offset = p, 0
        try:
            size = p.stat().st_size
        except OSError:
            return  # no side-car (yet): no labels
        if size < self._offset:
            self._offset = 0  # rewritten or truncated
        if size == self._offset:
            return
        with open(p, "rb") as f:
            f.seek(self._offset)
            data = f.read(size - self._offset)
        end = data.rfind(b"\n")
        if end < 0:
            return  # only a partial line so far
        self._offset += end + 1
        for raw in data[: end + 1].splitlines():
            try:
                row = json.loads(raw)
                seq, who = row["seq"], row["speaker"]
            except (ValueError, TypeError, KeyError):
                continue
            if isinstance(seq, int) and not isinstance(seq, bool) and isinstance(who, str) and who:
                utt = row.get("mouth_utt")
                self._seen[seq] = Label(who, row.get("speaker_by") == "mouth",
                                        utt if isinstance(utt, str) and utt else None)
                self._seen.move_to_end(seq)
        while len(self._seen) > self.keep:
            self._seen.popitem(last=False)

    def read(self, seqs: Sequence[int]) -> list[Optional[Label]]:
        self._read()
        return [self._seen.get(s) for s in seqs]

    def labels(self, seqs: Sequence[int]) -> list[Optional[str]]:
        return [r.speaker if r is not None else None for r in self.read(seqs)]

    def line_text(self, utt: str) -> Optional[str]:
        return self.lines(utt) if self.lines is not None else None
