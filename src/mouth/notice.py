"""A line the mouth drops always reaches its sender.

A drop is a ``said`` row with ``cut: true``: any line not played to its end.
``player._said`` writes every said row, and for a drop it first calls
``dropped(item, fields)``. That tells the line's sender and returns a short
string, which ``_said`` writes onto the row as ``notice``:

- ``sent <addr>``: ``message send`` took the notice mail (exit 0);
- ``failed: <why>``: there is no ``message`` binary, or ``message send``
  refused or failed, timed out, was fenced (this process is not the mouth
  service), or the burst budget was spent;
- ``none: <why>``: nothing reaches this sender unasked (a refused sender, no
  box, or ``$MOUTH_NOTICE=off``).

The carrier is chosen by route, never by reason:

1. a refused line is a record only: nothing in a refused mail names a
   recipient;
2. a line whose ``--wait`` caller is still waiting is not mailed: the
   waiter reads the said record, prints the drop on stderr and exits 1. The
   row says ``waiter``;
3. a mail from outside gets a reply To its From (``notice_to``);
4. a CLI line (or one of case 2 whose waiter has gone) gets mail To its session's
   own sessionmail box, else To its agent's box, if that box exists under
   ``~/.mail/agents``;
5. else the row records ``none:``.

Some drops are quiet by rule. A line retracted by its own sender is not told:
``retracted_by`` must be non-empty and equal the line's ``session`` (a CLI
retract) or its ``mail_from`` (a mailed retract); the retractor already has
the said record (``none: retracted by its sender``). A retract of a PLAYING
line is not quiet: that retractor got only the stop record. And a line the
player recovers as ``lost`` more than ``RECOVER_MAIL_S`` after it was
asked keeps its row but is not mailed. A line cut before it
was heard because an amend replaced it (``reason=amended``) is not
mailed either: its new text plays in its place.

**The waiter probe.** ``mouth.say(wait=True)`` holds ``LOCK_EX`` on
``<mouth dir>/done/<utt>.wait`` from before it drops the line until it
leaves. The probe derives that path itself, from the mouth dir and a
checked utt, and never from the item (``X-Mouth-Extra`` can set any key).
It opens with ``O_NONBLOCK|O_NOFOLLOW``, locks only a regular file, and
tries ``LOCK_SH|LOCK_NB``: refused means a waiter is live. Anything else
(no file, a FIFO, a symlink, an unlocked file left by a killed waiter)
means no waiter, and the line is mailed. The probe runs before ``_done``
writes the done file, so a waiter that is found cannot miss its record.

Every recipient passes the same guards: it is one plain addr-spec, the mouth
would take a line from it (``box.allowed``), and it is neither the mouth's own
box (``mouth``, ``mouth+...``) nor a call's box, where a notice would be
spoken.

**Notices go by sessionmail's ``message send``**, never straight to
an MTA, so a Mac with no postfix delivers them through sessionmail's mail drop
(``SESSIONMAIL_SENDMAIL=drop``). There is no fallback to postfix's binary. The
send runs as the seat ``mouth``, display name ``Mouth``, in an environment
``child_env`` builds: every inherited ``CLAUDE_SESSION_*`` (and the session id)
and both must-sign switches are dropped, so a player a CLI ``say`` spawned
never signs, or answers, as its caller. Notices go unsigned until the mouth
holds a seat key: ``$MOUTH_SIGNER_SOCK`` and ``$MOUTH_SIGNER_PUB``, both set,
become the key the send signs with, and must-sign turns on.
``SESSIONMAIL_HOST`` is pinned to the host the id is minted at.

Every notice carries a ``Message-ID`` of the form
``<ns.uid.mouth-notice@<host>>`` (``--message-id``); ``box.read`` files a reply
to one unspoken, and the notice says so. A reply to a notice id of the older
form (``<ns.uid@<host>.mouth-notice>``) is filed unspoken too. The envelope
sender is the seat now, not NULL, so a bounce of a notice comes back to the
mouth's box: ``box.read`` files a delivery report unspoken. The subject is
ASCII, with a backtick or ``$(`` defused, because ``message send`` refuses
anything else; the body keeps the line as it was.

**The player's environment governs notices.** ``$MOUTH_NOTICE``
(``off`` stops them) and ``$MOUTH_SENDMAIL`` (the command notices go
through, split with ``shlex``; it takes ``message send``'s arguments) are read
on every call, in the process that writes the said
row: the player. A player that a CLI ``say`` spawned inherits that caller's
environment (``ensure_player``), so one caller's ``NOTICE=off`` silences
every sender for that player's life, up to ``$MOUTH_IDLE_S``
(600 s). On a host where ``mouth mail`` is resident, it holds the player
lock and its own environment governs.

**The default ``message`` is the service's alone.** With no
``$MOUTH_SENDMAIL``, a notice goes through ``message`` (on PATH, else where uv
installs it: ``ears.message_bin``), resolved to an absolute path, only in a
process that a service entry point marked: ``mouth
serve`` and ``mouth mail`` call ``mark_service`` before the player starts, and
``mouth retract`` and ``mouth again`` before they write a said row. Any other
process (a script, a review rig, a library caller that imports the mouth) is
refused with ``failed: message send fenced: ...``, written on the said row and
logged. An explicit ``$MOUTH_SENDMAIL`` is honoured in any process.
A rig that starts a player as a process (``mouth say``, ``mouth serve``), or
runs ``mouth retract`` or ``mouth again``, runs the marked service, so it must
name its own stub.

Sends are synchronous and share ONE budget per burst: a
decision within ``BURST_GAP_S`` of the previous one is in the same burst; a
burst gets ``BURST_BUDGET_S`` of send time in all, and lasts at most
``BURST_MAX_S``, so a sustained flood cannot keep a spent budget spent.

**A say-again replay's note.** A replay (an item with ``again_of``)
gets ``again(item, fields)`` in place of ``dropped``, on EVERY said row, played
in full or not: one line To the line's own seat (never a ``notice_to``),
``Priority: non-urgent``, ``Mouth-Notice: again``. Its quiet rules, guards,
budget and fence are ``dropped``'s own.

``dropped`` and ``again`` never raise.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from ears import envnames, message_bin

NOTICE_VAR = "MOUTH_NOTICE"
#: The command notices go through (its name and meaning kept):
#: it takes ``message send``'s arguments. Tests and rigs point it at a fake.
SENDMAIL_VAR = "MOUTH_SENDMAIL"
#: The default command: sessionmail's ``message``, resolved on PATH to an absolute
#: path at use. Only the marked service may use it.
DEFAULT_SENDMAIL = "message"

#: The seat notices go From (``TM_AGENT_ADDRESS`` and ``EMAIL``), and its name.
SEAT = "mouth"
FROM_NAME = "Mouth"
#: The mouth's own key, when it holds one: both set, the send signs
#: with them and must sign. Unset, the notice goes unsigned, as it did by postfix.
SIGNER_VARS = {"MOUTH_SIGNER_SOCK": "CLAUDE_SESSION_AGENT_SOCK",
               "MOUTH_SIGNER_PUB": "CLAUDE_SESSION_PUB"}
#: Both spellings sessionmail reads for must-sign: a caller's value is dropped.
MUST_SIGN_VARS = ("SESSIONMAIL_REQUIRE_SIGNATURE", "SESSION_MAIL_REQUIRE_SIGNATURE")
#: A caller's session: its key and id never reach the send (sessiontools' id chain).
STRIPPED_PREFIX = "CLAUDE_SESSION_"
STRIPPED_VARS = ("CLAUDE_CODE_SESSION_ID",)

SEND_TIMEOUT_S = 5.0     # one send, at most
BURST_GAP_S = 1.0        # a decision this soon after the last is in the same burst
#: Send time a burst may spend in all. A `message send` takes about 1.2 s
#: (measured), so a burst of 6 notices needs about 7.2 s: 15 s sends them all, with room.
#: A notice dropped for budget is worse than a slow one; BURST_MAX_S still bounds a flood.
BURST_BUDGET_S = 15.0
BURST_MIN_S = 0.1        # less than this left: no send is tried
BURST_MAX_S = 30.0       # a burst this old starts afresh, however close its decisions

SUBJECT_MAX = 69         # sessionmail's rule
CLI_TEXT_MAX = 300
#: The tail of a notice id of the older form, ``<ns.uid@<host>.mouth-notice>``:
#: still matched by ``box._notice_reply``, never minted (``message send`` refuses it).
NOTICE_SUFFIX = ".mouth-notice>"
#: What a notice id carries now: ``<ns.uid.mouth-notice@<host>>``.
NOTICE_TAG = ".mouth-notice@"
RECOVER_MAIL_S = 3600.0  # a recovered (lost) line older than this is recorded, not mailed

#: A session or agent name that may name a box: flat, no ``+`` or ``@``.
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
#: One plain addr-spec: no quotes, comma, whitespace, angle brackets or leading ``-``.
_ADDR = re.compile(r"[A-Za-z0-9!#$%&'*/=?^_`{|}~+][A-Za-z0-9!#$%&'*/=?^_`{|}~+.-]{0,127}"
                   r"@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,252}[A-Za-z0-9])?")
_UTT = re.compile(r"[A-Za-z0-9]{1,32}")
_MSGID = re.compile(r"<[^<>\s\"]{1,250}>")

#: What each reason means, and what to do about it: one plain line in the body.
_ADVICE = {
    "device-absent": "The device was not there when the line's turn came. "
                     "`mouth devices` lists what is there.",
    "open-failed": "The device was there but would not open for this line; why says "
                   "what the audio system answered.",
    "device-lost": "The device stopped taking audio part way through the line.",
    "error": "The mouth could not synthesise or play it.",
    "stop": "A stop cut it (`mouth stop`).",
    "interrupted": "A line sent with --now cut it.",
    "retracted": "It was retracted.",
    "expired": "It waited past its expiry and was dropped unsaid.",
    "stale": "It was held for the end of the user's turn, and was queued before that "
             "turn began, so it went stale.",
    "lost": "The player stopped before it finished this line.",
    "resume-failed": "The mouth could not queue the replay.",
}
#: The quiet outcome of a resume a say-again replaced: no mail.
AGAIN_REPLACED = "none: replaced by a say-again"
_NO_REPLY = "Replies to this mail are not spoken. To say it again, send a new line."


# -- what a notice says ---------------------------------------------------------

def describe(text: str, rec: dict) -> dict:
    """WHY and WHAT: ``why`` (the reason, with its detail), ``heard`` and ``unsaid``.

    ``heard`` is the said row's ``text_played_est``, an estimate; for a line
    that never started it is empty and ``unsaid`` is the whole text."""
    text = str(text or "")
    reason = str(rec.get("reason") or "dropped")
    detail = rec.get("detail")
    why = f"{reason} ({detail})" if detail else reason
    heard = ""
    if (rec.get("played_s") or 0) > 0:
        heard = str(rec.get("text_played_est") or "")
    if heard and text.startswith(heard):
        at = len(heard)
        if 0 < at < len(text) and not text[at - 1].isspace() and not text[at].isspace():
            at = text.rfind(" ", 0, at) + 1   # cut inside a word: the unsaid part starts with it
        unsaid = text[at:].strip()
    else:
        unsaid = text.strip()
    return {"reason": reason, "why": why, "heard": heard, "unsaid": unsaid}


def _clip(text: str, n: int) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= n else t[: max(0, n - 1)] + "…"


#: Folds ``unicodedata`` leaves alone: typographic quotes, dashes and the ellipsis.
_FOLD = {"\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u2032": "'",
         "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"', "\u2033": '"',
         "\u00ab": '"', "\u00bb": '"', "\u2013": "-", "\u2014": "-", "\u2212": "-",
         "\u2026": "...", "\u00a0": " "}


def _ascii(text) -> str:
    """TEXT as ASCII for a subject: ``message send`` refuses any other.
    Typographic marks become their plain forms, an accent is dropped from its
    letter, and anything left becomes ``?``. A backtick becomes ``'`` and ``$(``
    becomes ``$ (``: ``message send`` refuses a subject that could execute."""
    t = "".join(_FOLD.get(c, c) for c in re.sub(r"\s+", " ", str(text or "")))
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = "".join(c if " " <= c <= "~" else "?" for c in t)
    return t.replace("`", "'").replace("$(", "$ (")


def _sclip(text, n: int) -> str:
    """``_clip`` for a subject: ASCII, cut with ``...``."""
    t = _ascii(text).strip()
    return t if len(t) <= n else t[: max(0, n - 3)] + "..."


def cli_line(text: str, rec: dict) -> str:
    """The one stderr line for a ``--wait`` drop; the same words as the mail."""
    w = describe(text, rec)
    if w["heard"]:
        head = f"mouth: cut at {float(rec.get('cut_at_s') or rec.get('played_s') or 0):.1f}s, {w['why']}"
        return f'{head}: not said: "{_clip(w["unsaid"], CLI_TEXT_MAX)}"'
    return f'mouth: not said, {w["why"]}: "{_clip(text, CLI_TEXT_MAX)}"'


def subject(text: str, rec: dict) -> str:
    """At most 69 ASCII characters, the first words quoted (``_ascii``)."""
    w = describe(text, rec)
    if w["reason"] == "done":   # a replay heard in full
        head = "replayed in full: "
        return f'{head}"{_sclip(text, max(SUBJECT_MAX - len(head) - 2, 1))}"'[:SUBJECT_MAX]
    if w["heard"]:
        head = _ascii(f"cut at {float(rec.get('cut_at_s') or rec.get('played_s') or 0):.1f}s, "
                      f"{w['reason']}: ")
        room = SUBJECT_MAX - len(head) - 5
        return f'{head}"...{_sclip(w["unsaid"], max(room, 1))}"'[:SUBJECT_MAX]
    head = _ascii(f"not said, {w['reason']}: ")
    room = SUBJECT_MAX - len(head) - 2
    return f'{head}"{_sclip(text, max(room, 1))}"'[:SUBJECT_MAX]


def _resumable(item: dict, rec: dict) -> bool:
    """Will the mouth replay the rest? The player's own decision, never a guess:
    ``player._plan`` writes it on the row (``resume: will``) before this notice,
    and ``_resume`` acts on that same decision."""
    return rec.get("resume") == "will" and bool(rec.get("resume_utt"))


def _cut_line(item: dict) -> str:
    """The line a replay re-offered, by utt and text."""
    said = item.get("resume_text")
    return f"line {item['resume_of']}" + (f' ("{_clip(said, 200)}")' if said else "")


def _hm(ts) -> str:
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts).strftime("%H:%M:%S")
        except ValueError:
            return "?"
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(ts).strftime("%H:%M:%S")
        except (OverflowError, OSError, ValueError):
            return "?"
    return "?"


#: Who set the live N, in the notice's words: ``barge_words_from``.
_LAYER = {"default": "the default", "user": "the user's setting", "model": "the model"}


def cut_by(rec: dict) -> str:
    """The ``cut by:`` line's text for a barge-in cut, from the said row's
    ``cut_by``, ``cut_how``, ``barge_words`` and
    ``barge_words_from`` (``player.CUT_KEYS``); "" when the row names no words
    (a hand ``mouth stop --reason barge-in``, or an older row).

    - words: ``"advice battery" (2 words; N=2, set by the user's setting)``
    - a stop ask: ``"stop" (a stop ask: it cuts at any N)``"""
    words = [str(w) for w in rec.get("cut_by") or () if str(w)]
    if not words:
        return ""
    said = '"' + _clip(" ".join(words), 120) + '"'
    if rec.get("cut_how") == "stop-ask":
        return f"{said} (a stop ask: it cuts at any N)"
    n = len(words)
    count = f"{n} word{'s' if n != 1 else ''}"
    need = rec.get("barge_words")
    if need is None:
        return f"{said} ({count})"
    layer = _LAYER.get(str(rec.get("barge_words_from")))
    return f"{said} ({count}; N={need}" + (f", set by {layer})" if layer else ")")


def body(item: dict, rec: dict) -> str:
    text = str(item.get("text") or "")
    w = describe(text, rec)
    reason = w["reason"]
    full = reason == "done" and bool(item.get("resume_of"))   # a replay heard in full
    out = []
    if full:   # the loop closes
        out.append(f"The replay of {_cut_line(item)} was played in full.")
    elif w["heard"]:
        at = float(rec.get("cut_at_s") or rec.get("played_s") or 0)
        out.append(f"Your line was cut at {at:.1f}s: {reason}.")
    elif reason == "lost":  # the dead player recorded nothing of how far it got
        out.append(f"How much of your line was said is unknown: {reason}.")
    else:
        out.append(f"Your line was not said: {reason}.")
    out.append(f"why:    {w['why']}")
    if reason == "barge-in" and cut_by(rec):
        out.append(f"cut by: {cut_by(rec)}")
    out.append(f"what:   {text}")
    if full:
        out.append("heard:  all of it")
    elif w["heard"]:
        out.append(f"heard:  {w['heard']}  (an estimate)")
        out.append(f"unsaid: {w['unsaid']}")
    elif reason == "lost":
        out.append("heard:  unknown (none, some or all of it)")
    else:
        out.append("heard:  nothing (it never started)")
    if item.get("resume_of") and not full:   # a replay's own end
        out.append(f"This was the replay of {_cut_line(item)}; it is not replayed again.")
    if reason == "barge-in":
        if _resumable(item, rec):
            out.append(f"The user's words cut it. The mouth WILL replay the unsaid rest when "
                       f"the user's turn ends (utt {rec['resume_utt']}). Do not re-say it. If "
                       f"the user asks for quiet, or answers it, "
                       f"or their turn runs past {float(rec.get('resume_s') or 0):g} s, it is "
                       f"dropped and you get a second notice. A replay is never replayed.")
        elif item.get("resume_of"):
            out.append("The user's words cut the replay too. It will not come back: a replay "
                       "is never replayed. Re-say it only if it still matters.")
        elif "underruns" in rec:
            why = rec.get("resume_why") or "the mouth did not decide to replay it"
            out.append(f"The user's words cut it. It will not come back: {why}. "
                       "Re-say it only if it still matters.")
        else:
            out.append("The user's words came before it played, so it was flushed. "
                       "It will not come back. Re-say it only if it still matters.")
    elif reason == "expired" and item.get("resume_of"):
        from .hold import ANSWERED, QUIET

        if rec.get("detail") == QUIET:
            out.append("Dropped: the user asked for quiet. Re-say it only if it still matters.")
        elif rec.get("detail") == ANSWERED:      # the user's turn was the answer
            out.append("Dropped: the user answered it, so it is not replayed. Their turn "
                       "is the answer: read it, and do not ask again.")
        else:
            out.append(f"Dropped: the user's turn ran past "
                       f"{float(item.get('expires_s') or 0):g} s, "
                       "so it expired. Re-say it only if it still matters.")
    elif reason in _ADVICE:
        out.append(_ADVICE[reason])
    if item.get("amended_from"):
        out.append(f"(amended from: {_clip(item['amended_from'], 200)})")
    dev = rec.get("device") or item.get("device") or "?"
    out.append(f"utt {item.get('utt') or '?'} · device {dev} · queued "
               f"{_hm(item.get('requested_ts') or item.get('requested_t'))} · "
               f"{'played' if full else 'dropped'} "
               f"{_hm(time.time())}")
    out.append("")
    out.append(_NO_REPLY)
    return "\n".join(out) + "\n"


def _non_urgent(item: dict, rec: dict) -> bool:
    """Rides, never wakes: a barge-in, and every notice about a replay, whose cut
    line's notice already went. A say-again replay's note rides too: the user asked for
    the line, not for the seat."""
    return (rec.get("reason") == "barge-in" or bool(item.get("resume_of"))
            or bool(item.get("again_of")))


# -- the say-again replay's note ------------------------

def _again_how(item: dict, rec: dict) -> tuple[str, str]:
    """(what became of the replay, in the note's words; the short form for the subject)."""
    reason = str(rec.get("reason") or "dropped")
    played = float(rec.get("played_s") or 0) > 0
    at = float(rec.get("cut_at_s") or rec.get("played_s") or 0)
    if not rec.get("cut") and reason == "done":
        return "in full", "replayed in full"
    if reason == "lost":
        return ("the player stopped part way, so how much the user heard is unknown",
                "replay lost")
    if played:
        by = " by the user's words" if reason == "barge-in" else f": {describe('', rec)['why']}"
        return f"cut at {at:.1f} s{by}", f"replay cut at {at:.1f}s"
    if reason == "stale":
        return "dropped unheard: the user started talking again", "replay dropped unheard"
    if reason == "barge-in":
        return "dropped unheard: the user's words flushed it", "replay dropped unheard"
    return f"dropped unheard: {describe('', rec)['why']}", "replay dropped unheard"


def again_body(item: dict, rec: dict) -> str:
    """ONE line: who asked, which line, from what, and what became of it."""
    heard = " ".join(str(item.get("again_heard") or "").split())
    seq = item.get("again_seq")
    asked = f'The user said "{_clip(heard, 80)}"' if heard else "The user asked to hear it again"
    if seq is not None and str(seq).strip():
        asked += f" (heard seq {_clip(seq, 32)})"
    of = _safe_id(item.get("again_of"))
    src = ("from its text: the audio was gone" if item.get("again_from") == "text"
           else "from its audio")
    how, _ = _again_how(item, rec)
    done = not rec.get("cut") and rec.get("reason") == "done"
    heard_some = done or rec.get("reason") == "lost" or float(rec.get("played_s") or 0) > 0
    did = "replayed your line" if heard_some else "queued a replay of your line"
    tail = "No reply needed." if done else "Re-say it only if it still matters."
    return f"{asked}: the mouth {did} {of} {src}, {how}. {tail}\n"


def again_subject(item: dict, rec: dict) -> str:
    """At most 69 characters: what became of the replay, the first words quoted."""
    _, head = _again_how(item, rec)
    head = _ascii(head + ": ")
    return f'{head}"{_sclip(item.get("text"), max(SUBJECT_MAX - len(head) - 2, 1))}"'[:SUBJECT_MAX]


def again_message(item: dict, rec: dict, to: str, *, reply: bool) -> "Notice":
    """The replay's note: ``Mouth-Notice: again``, always ``Priority: non-urgent``,
    and the ``Mouth-Utt`` of the replay (its ``again_of`` is in the body)."""
    m = message(item, rec, to, reply=reply)
    m.subject = again_subject(item, rec)
    m.headers["Mouth-Notice"] = "again"
    m.headers.setdefault("Priority", "non-urgent")
    m.body = again_body(item, rec)
    return m


def _host() -> str:
    from .box import _host as host

    return host()


def _safe_id(utt) -> str:
    u = str(utt or "")
    return u if _UTT.fullmatch(u) else hashlib.sha1(u.encode()).hexdigest()[:12]


@dataclass
class Notice:
    """One notice, as ``message send`` takes it (``args``), its body on stdin.

    From, Date and To are the send's own (the seat is ``child_env``'s). The id
    goes by ``--message-id``; the thread by ``-r``, which writes In-Reply-To and
    References (``--header`` may not). Every other header is a ``--header``."""
    to: str
    subject: str
    body: str
    msgid: str
    in_reply_to: Optional[str] = None
    headers: dict = field(default_factory=dict)

    def args(self) -> list:
        a = ["send", self.to, "-s", self.subject, "-f", "-", "--raw", "--no-shorten", "--json",
             "--message-id", self.msgid]
        if self.in_reply_to:
            a += ["-r", self.in_reply_to]
        for k, v in self.headers.items():
            a += ["--header", f"{k}: {' '.join(str(v).split())}"]
        return a


def message(item: dict, rec: dict, to: str, *, reply: bool) -> Notice:
    uid = _safe_id(item.get("utt"))
    mid = item.get("mail_id")
    irt = mid.strip() if isinstance(mid, str) and _MSGID.fullmatch(mid.strip()) else None
    headers = {"Auto-Submitted": "auto-replied" if reply else "auto-generated"}
    if _non_urgent(item, rec):
        headers["Priority"] = "non-urgent"
    headers["Mouth-Notice"] = _ascii(rec.get("reason") or "dropped")
    headers["Mouth-Utt"] = uid
    return Notice(to=to, subject=subject(str(item.get("text") or ""), rec), body=body(item, rec),
                  msgid=f"<{time.time_ns()}.{uid}{NOTICE_TAG}{_host()}>", in_reply_to=irt,
                  headers=headers)


# -- who hears it --------------------------------------------------------------

def _refuse(addr: str) -> Optional[str]:
    """Why ADDR must not be mailed, or None if it may."""
    from .box import allowed, call_box

    if not _ADDR.fullmatch(addr):
        return "not a plain address"
    local = addr.split("@", 1)[0].lower()
    if local == "mouth" or local.startswith("mouth+"):
        return "the mouth's own box"
    if call_box(addr):
        return "a call's box"
    if not allowed(addr):
        return "not an address the mouth takes lines from"
    return None


def _box_exists(name) -> bool:
    if not isinstance(name, str) or not _NAME.fullmatch(name) or name.lower() == "mouth":
        return False
    return (Path.home() / ".mail" / "agents" / name / "new").is_dir()


def recipient(item: dict) -> tuple[Optional[str], bool, str]:
    """(address, is a reply, why not) for a dropped line's sender."""
    why = []
    to = item.get("notice_to")
    if to:
        bad = _refuse(to) if isinstance(to, str) else "not a plain address"
        if bad is None:
            return to, True, ""
        why.append(f"From {_clip(to, 80)} is {bad}")
    host = _host()
    for kind in ("session", "agent"):
        name = item.get(kind)
        if _box_exists(name):
            addr = f"{name}@{host}"
            bad = _refuse(addr)
            if bad is None:
                return addr, False, ""
            why.append(f"{kind} {name}: {bad}")
        else:
            why.append(f"no box for {kind} {_clip(name, 64) if name else '(none)'}")
    return None, False, "; ".join(why)


# -- the live --wait caller -------------------------------------------------

def _mouth_dir() -> Path:
    from .paths import mouth_dir

    return mouth_dir()


def waiter(item: dict) -> bool:
    """Is this line's ``--wait`` caller still waiting? Never blocks, never raises.

    The path is DERIVED: ``<mouth dir>/done/<utt>.wait``, the utt checked, the
    same directory ``_done`` writes the record to (``ensure_player`` passes the
    caller's mouth dir to the player as ``$MOUTH_DIR``)."""
    if not item.get("wait"):
        return False
    utt = item.get("utt")
    if not isinstance(utt, str) or not _UTT.fullmatch(utt):
        return False
    try:
        p = _mouth_dir() / "done" / f"{utt}.wait"
        fd = os.open(str(p), os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    except (OSError, ValueError):
        return False
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError:
        return False
    finally:
        os.close(fd)


# -- delivery: one budget per burst --------------------------------------------

class _Budget:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.start = float("-inf")
        self.last = float("-inf")
        self.spent = 0.0

    def _roll(self, now: float) -> None:
        if now - self.last > BURST_GAP_S or now - self.start > BURST_MAX_S:
            self.start, self.spent = now, 0.0

    def decided(self) -> None:
        """Every decision extends the burst: sent, failed or skipped."""
        with self.lock:
            now = time.monotonic()
            self._roll(now)
            self.last = now

    def send(self, addr: str, n: Notice) -> str:
        with self.lock:
            now = time.monotonic()
            self._roll(now)
            left = BURST_BUDGET_S - self.spent
            if left < BURST_MIN_S:
                self.last = time.monotonic()
                return "failed: send budget spent"
            t0 = time.monotonic()
            try:
                return _sendmail(addr, n, min(SEND_TIMEOUT_S, left))
            finally:
                t1 = time.monotonic()
                self.spent += t1 - t0
                self.last = t1


_BUDGET = _Budget()


#: Set by the service's entry points (``mouth serve``, ``mouth mail``, ``mouth
#: retract``, ``mouth again``), never by an import: only the mouth service may use
#: ``DEFAULT_SENDMAIL``.
_SERVICE: Optional[str] = None


def mark_service(verb: str) -> None:
    """Mark this process as the mouth service, which may use the default ``message``.

    ``mouth.__main__`` calls it for its ``SERVICE_VERBS`` only. It is a flag in this
    process, not an environment variable, so no child inherits it and no
    environment can fake it."""
    global _SERVICE
    _SERVICE = verb


def _resolve(cmd: list) -> tuple[Optional[list], str]:
    """CMD with its program as an absolute path, or (None, why there is none)."""
    if not cmd:
        return None, "the command is empty"
    found = shutil.which(os.path.expanduser(cmd[0]))
    if not found:
        return None, f"{cmd[0]} is not an executable on PATH"
    return [os.path.abspath(found), *cmd[1:]], ""


def _sendmail_cmd() -> tuple[Optional[list], str]:
    """(the send command, "") or (None, why there is none: fenced, or no binary).

    An explicit ``$MOUTH_SENDMAIL`` is honoured in any process. The default,
    sessionmail's ``message``, is the marked service's alone: a script, a review
    rig or a library caller that imports the mouth must name its own. Either is
    resolved to an absolute path: the explicit one on PATH alone, the default on PATH,
    then where uv installs it (``ears.message_bin``: a launchd PATH holds neither).
    One that is not there is ``no message binary``."""
    explicit = envnames.get(SENDMAIL_VAR)
    if explicit:
        cmd, why = _resolve(shlex.split(explicit))
        return cmd, "" if cmd else f"no message binary (${SENDMAIL_VAR}: {why})"
    if _SERVICE is None:
        return None, (f"message send fenced: not the mouth service, and no ${SENDMAIL_VAR} "
                      f"(only `mouth serve`, `mouth mail`, `mouth retract` or `mouth again` "
                      f"uses the default `{DEFAULT_SENDMAIL}`)")
    cmd = shlex.split(DEFAULT_SENDMAIL)
    if cmd and cmd[0] == message_bin.NAME:      # the default `message`: PATH, then where
        found = message_bin.find()                # uv installs it (a launchd PATH is short)
        if found:
            return [found, *cmd[1:]], ""
        return None, (f"no message binary ({cmd[0]} is not an executable on "
                      f"{message_bin.where_looked()}; it is sessionmail's)")
    cmd, why = _resolve(cmd)
    return cmd, "" if cmd else f"no message binary ({why}; it is sessionmail's)"


def child_env(environ=None) -> dict:
    """The environment ``message send`` runs with.

    - every ``CLAUDE_SESSION_*``, and the session id, is dropped: a player a CLI
      ``say`` spawned inherits its caller's, and must never sign, or answer, as it;
    - both must-sign spellings are dropped;
    - the seat is ``mouth`` (``TM_AGENT_ADDRESS`` and ``EMAIL``, so an inherited
      ``EMAIL`` never wins), its name ``Mouth``;
    - ``SESSIONMAIL_HOST`` is pinned to the host the notice id is minted
      at, or ``--message-id`` would be refused;
    - ``$MOUTH_SIGNER_SOCK`` and ``$MOUTH_SIGNER_PUB``, both set, become the
      send's key, and must-sign turns on: a mouth with a key never sends unsigned."""
    e = os.environ if environ is None else environ
    env = {k: v for k, v in e.items()
           if not k.startswith(STRIPPED_PREFIX) and k not in STRIPPED_VARS
           and k not in MUST_SIGN_VARS}
    env["TM_AGENT_ADDRESS"] = env["EMAIL"] = SEAT
    env["SESSIONMAIL_FROM_NAME"] = FROM_NAME
    env["SESSIONMAIL_HOST"] = _host()
    keys = {var: e.get(name) for name, var in SIGNER_VARS.items()}
    if all(keys.values()):
        env.update({var: os.path.expanduser(v) for var, v in keys.items()})
        env[MUST_SIGN_VARS[0]] = "1"
    return env


def _answer(stdout: bytes) -> dict:
    """``--json``'s one object (the last line that parses), or {}."""
    for line in reversed((stdout or b"").decode("utf-8", "replace").splitlines()):
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return {}


def _sendmail(addr: str, n: Notice, timeout: float) -> str:
    """Send N by ``message send``; ``sent <addr>`` or ``failed: ...``. Never falls back.

    A failed send also writes one ERROR line to stderr (the service's log), so a
    notice that never left is loud there, not only in the decision's outcome."""
    out = _send_once(addr, n, timeout)
    if not out.startswith("sent "):
        try:
            print(f"mouth: ERROR: notice to {addr} {' '.join(out.split())}", file=sys.stderr,
                  flush=True)
        except Exception:  # noqa: BLE001 - a log line must never stop the player
            pass
    return out


def _send_once(addr: str, n: Notice, timeout: float) -> str:
    """The one ``message send`` behind ``_sendmail``: ``sent <addr>`` or ``failed: ...``."""
    cmd, why = _sendmail_cmd()
    if cmd is None:
        return f"failed: {why}"[:300]
    try:
        p = subprocess.run([*cmd, *n.args()], input=n.body.encode("utf-8"), env=child_env(),
                           capture_output=True, timeout=timeout, close_fds=True, check=False)
    except subprocess.TimeoutExpired:
        return f"failed: message send timed out after {timeout:.1f}s"
    except OSError as e:
        return f"failed: message send: {type(e).__name__}: {e}"[:200]
    got = _answer(p.stdout)
    if p.returncode == 0 and got.get("sent", "yes") == "yes":
        return f"sent {addr}"
    said = " ".join(str(got.get("message") or (p.stderr or b"").decode("utf-8", "replace")).split())
    kind = f" kind {got['kind']}" if got.get("kind") else ""
    out = f"failed: message send exit {p.returncode}{kind}"
    return (out + (f": {said[-160:]}" if said else ""))[:300]


def deliver(item: dict, rec: dict, to: str, *, reply: bool, build=None) -> str:
    return _BUDGET.send(to, (build or message)(item, rec, to, reply=reply))


# -- the hook ------------------------------------------------------------------

def _log(item: dict, rec: dict, to: Optional[str], outcome: str) -> None:
    """One log line per decision, on stderr: a CLI's stdout is its JSON."""
    try:
        print(json.dumps({"notice": item.get("utt"), "reason": rec.get("reason"), "to": to,
                          "outcome": outcome[:200]}), file=sys.stderr, flush=True)
    except Exception:  # noqa: BLE001
        pass


def _self_retract(item: dict) -> bool:
    """Retracted by the line's own sender? Two unset values are not a match."""
    by = str(item.get("retracted_by") or "")
    return bool(by) and by in (str(item.get("session") or ""), str(item.get("mail_from") or ""))


def _age(item: dict) -> Optional[float]:
    try:
        t = float(item.get("requested_t"))
    except (TypeError, ValueError):
        return None
    age = time.time() - t
    return age if math.isfinite(age) else None  # NaN or inf is no age


def _ago(age: Optional[float]) -> str:
    if age is None:
        return "an unknown time"
    if age < 7200:
        return f"{age / 60:.0f} min"
    if age < 172800:
        return f"{age / 3600:.0f} h"
    return f"{age / 86400:.0f} d"


def _decide(item: dict, rec: dict, build=None) -> tuple[Optional[str], str]:
    if rec.get("reason") == "refused":
        # a record only. The refused mail's From, X-Session-From,
        # X-Mouth-Agent and X-Mouth-Extra are the claims of the sender the mouth
        # just refused; none of them is read.
        return None, "none: refused sender, not mailed"
    if waiter(item):
        return None, "waiter"
    if _self_retract(item):
        return None, "none: retracted by its sender"
    if rec.get("reason") == "amended":
        # cut before it was heard because an amend replaced it: not a
        # drop, its new text plays in its place, and that line's said reports it
        return None, "none: amended before it was heard"
    if rec.get("reason") == "again":
        # a resume a say-again replaced: the
        # whole line plays instead of its rest, and the replay's own note reports it
        return None, AGAIN_REPLACED
    if rec.get("reason") == "lost":
        age = _age(item)
        if age is None or age >= RECOVER_MAIL_S:
            return None, f"none: recovered {_ago(age)} after it was asked, too old to mail"
    if (envnames.get(NOTICE_VAR) or "").strip().lower() in ("off", "0", "no", "false"):
        return None, "none: off"
    to, reply, why = recipient(item)
    if to is None:
        return None, f"none: {why}"[:300]
    return to, deliver(item, rec, to, reply=reply, build=build)


def dropped(item: dict, fields: dict) -> str:
    """Tell the sender of a dropped line; return where the notice went. Never raises."""
    return _notify(item, fields)


def again(item: dict, fields: dict) -> str:
    """Tell the seat what became of its say-again replay.

    ``player._said`` calls it for EVERY said row of an item with ``again_of``,
    played in full, cut or dropped, in place of ``dropped``: exactly one note
    per replay. The note is one line, ``Priority: non-urgent`` (it rides, never
    wakes), ``Mouth-Notice: again``. It goes To the line's own seat: its session
    box, else its agent box. A ``notice_to`` is never read here: the replay is
    the keeper's, never an outside sender's. The quiet rules, ``off``, the guards,
    the burst budget and the send fence are ``dropped``'s own. Returns where
    the note went, for the row's ``notice``. Never raises."""
    seat = {k: v for k, v in item.items() if k != "notice_to"}
    return _notify(seat, fields, build=again_message)


def _notify(item: dict, fields: dict, build=None) -> str:
    to = None
    try:
        to, outcome = _decide(item, fields, build)
    except Exception as e:  # noqa: BLE001 - a notice must never stop the player
        outcome = f"failed: {type(e).__name__}: {e}"[:200]
    try:
        _BUDGET.decided()  # every decision extends the burst, not only a send
    except Exception:  # noqa: BLE001
        pass
    _log(item, fields, to, outcome)
    return outcome
