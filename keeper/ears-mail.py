#!/usr/bin/env python3
"""ears-mail - the keeper mails every finished turn of the user to the room's address.

The keeper half of the pager genie: the keeper writes, and sessionmail's
pager reads. What it reads is the finished turns.
By default (--to device) each FINISHED turn is ONE
mail To and From heard+room@<host>, whatever input device the capture is
following, and it lands in the one `heard` box. A session hears the room, on
every device, by subscribing to that one address; the pager decides wake or ride
from the headers. The device is named by the Ears-Device header, never by the
address. While --device-copy is on (the default) the same send, one message with
one Message-ID, also goes Cc heard+<device>@<host>, so a session subscribed to a
device's address still hears that device's turns. Every finished turn goes,
addressed or not. The headers carry no X-: Ears-Kind, Ears-Rule, Ears-Seq,
Ears-Start, Ears-End, Ears-Source, Ears-Device, Ears-Speaker. Ears-Speaker is
the turn's own `speaker` label as <label>@<host> when the capture named the turn
(a split, or its partials' labels; speaker_of), else --speaker, unknown@<host> by default.
After an echo cut that left words, a label naming a seat whose line was cut is
dropped (heard_rec): the words left are the user's, so they get the default. Ears-Rule is keyed
by ADDRESS: alice@<host>=call for "hey Alice", everyone=everyone for "hey
everyone"; reply, light and first entries ride (only call and everyone wake).
Ears-Audio names the turn's kept WAV when the record has `audio` (recording
on); with no `audio` there is no Ears-Audio header.
<device> is device_slug(): airpods, macbook (the built-in mic), else the name
in lower-case words joined by "-". It names the Cc, and it is the gate: a turn
with no slug is not mailed at all. --room names the room (default
$EARS_MAIL_ROOM, else room); a device whose slug is the room's gets no Cc. NOT mailed: drafts and partials, the mouth's
echo, empty turns, and a BlackHole device's turns (the call tap).
A FINISHED turn of a Delta call (source call, device call:N) is mailed To
calls+<bot>-<N>@<host>, the call's own box, with the headers Ears-Kind: turn,
Ears-Seq, Call-Id and Call-Bot, never to any heard+ address and with no Cc; the box carries both
directions. The bot is learned from the call's call-ears.sock probe (a call whose
bot no leg names is skipped, {"skip": "call-bot"}, said on stderr). --calls off
skips call turns again ({"skip": "call"}); --calls-from sets the From.
Web legs (<sessionphone root>/web) are left to sessionphone web. --to seats is
the pre-genie behaviour below (each matched turn To the seats it is for), kept
until the genie is live.
NEVER run --to seats and --to device for the same seat: a call would reach it
twice. The box's name is --sender ($EARS_MAIL_FROM, default
heard): `heard`, because ears+<device> would fall into the ears project's
own box (the project's, not the device's). The room is its plus tag.
sessionmail's reader trusts only its keeper's From (_voice.KEEPER), so
the two names move together.

Pre-genie: the keeper matches every seat's wake words ONCE and mails the seat.

The ears send a mail when there is something for a seat,
and the seat's own pager carries it. The keeper sends the notifications,
so there is one gate that controls who is able to listen
to what.

Follows the heard log (today's and yesterday's, re-listed every poll so
midnight's new file is read) from a saved place. For each FINISHED turn of the user
it asks every seat's rules, and mails the turn to every seat it is for, one
send per seat (one dead box must not silence the rest), From ears.
The seat's ordinary pager wakes on it; no heard-first arm, no ears-count, no
catchup. The heard log stays the record.

Every mail goes by sessionmail's `message send`, never straight to an
MTA, so a Mac with no postfix delivers through sessionmail's mail drop
(SESSIONMAIL_SENDMAIL=drop). There is no fallback. $EARS_MAIL_MESSAGE names the
command (tests and rigs point it at a fake), else `message` on PATH, resolved
once at start; while it is missing it is looked for again at each send, so a
`message` installed later is found with no restart. The send runs as the seat (SENDER, or SENDER+ROOM), display
name Ears, with every inherited CLAUDE_SESSION_* and must-sign dropped: unsigned
until a plus seat can hold a key; $EARS_MAIL_SIGNER_SOCK and _PUB, both set, sign.
The send mints the Message-ID, and the fire log keeps the one it returned.

A seat's rules (defaults, then its own entry in the rules file):
  call      \\b(OPENERS)[,]? +(NAMES)\\b        "hey Alice"
  everyone  \\b(OPENERS)[,]? +(EVERYONE)\\b     "hey everyone": every seat, one verdict
  first     ^\\W*(NAMES)\\b  if name_first        "Alice, ..."
  reply     an unaddressed turn answers the line that most recently FINISHED
            playing before it began, within reply_window s (default 120); a
            line not yet played can't be answered, nor
            can a turn in flight; one reply wake per line. A
            line the user's turn CUT (a barge-in inside the turn) is the one it
            answers, first
  light     for LIGHT seconds after the seat's line was said, every turn the user
            finishes goes to it (the stairwell light, 4389353)
  cut       the seat's own line was cut: it is told, with the reason, unless the
            mouth's own notice reached it (the said row's notice is sent/waiter)
  film      while Kodi plays a video (--kodi URL), reply and light wake nobody:
            the film is talking, not the user. A turn that names a seat still wakes it
  again     a turn that is WHOLLY a say-again phrase ("say again", "pardon", ...;
            names, openers and "sorry"/"please" stripped from its ends only) asks
            for the newest heard line of the seat it names, else of whoever spoke
            last, within again_window s (default 120). It is checked FIRST and is
            KEY@host=again, which calls nobody; it marks nothing answered.
            The keeper then runs `MOUTH again UTT` (--mouth, 5 s) to replay the line;
            if that fails the entry is KEY@host=call instead, so the seat says it
            again. Under --to seats an again mails no turn
Echo of the mouth (its line heard back through the mic) is never the user's: the same
rule as heard-first and the mouth's barge guard (hold.py is_echo).

  ears-mail.py [--rules FILE] [--seats alice,bob] [--dry-run] [--from-start]
               [--once] [--state FILE] [--fire-log FILE] [--kodi URL] [--mouth CMD]

--dry-run prints each mail it would send as one JSON line and sends nothing; the
`mouth again` it would run goes to stderr, and is not run.
--once reads to the end of the log and exits (tests, replays).
Stdlib only: it runs on the system python, like the rest of keeper/.

"""
import argparse, difflib, glob, json, os, re, shlex, socket, subprocess, sys, time
import urllib.request
from datetime import datetime

# phone_roots.py sits beside this file (keeper/, or ears/_keeper/ in a wheel): where a Delta
# bot keeps its call sockets, the rule keeper/call-ears shares. A loader that runs this
# without its dir on sys.path (the tests) still finds it.
if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import phone_roots  # noqa: E402  (keeper/ is not a package)

DEFAULTS = {
    "openers": "hey|hay",            # whisper hears "hey" as hey or hay; "a" dropped
                                     # (finding 3.6: "a Bob thing" is no call;
                                     # a seat can still add it in the rules file)
    "everyone": "everyone|everybody",  # 2647da3, like @here in Slack
    "reply": True,                   # 38249a2
    "name_first": False,
    "light": 0,                      # seconds; 0 = off
    # a turn that is WHOLLY one of these, once normalised (again_match), asks
    # the mouth to replay the last line; [] or false turns it off for a seat
    # ("say that again" included)
    "again": ["say again", "repeat that", "pardon", "come again", "say that again"],
    "again_window": 120,             # seconds back a say-again looks, like reply_window
}
SUBJECT_MAX = 69
#: The room's plus tag: every finished mic turn goes To and From SENDER+ROOM,
#: whatever input device the capture follows (--room, $EARS_MAIL_ROOM).
ROOM = "room"
ROOM_VAR = "EARS_MAIL_ROOM"
#: One plus tag: lower-case letters, digits and "-", never "+", "/" or "@".
_TAG = re.compile(r"[a-z0-9-]+")

# -- the send: sessionmail's `message send`, never an MTA ----------
#: The command turns go through: it takes `message send`'s arguments. Tests and
#: rigs point it at a fake; unset, sessionmail's `message` on PATH.
MESSAGE_VAR = "EARS_MAIL_MESSAGE"
DEFAULT_MESSAGE = "message"
SEND_TIMEOUT_S = 30
FROM_NAME = "Ears"
#: The keeper's own key, when it holds one: both set, the send signs
#: with them and must sign. Unset, the turn goes unsigned: a plus seat
#: (heard+room) cannot hold a key yet.
SIGNER_VARS = {"EARS_MAIL_SIGNER_SOCK": "CLAUDE_SESSION_AGENT_SOCK",
               "EARS_MAIL_SIGNER_PUB": "CLAUDE_SESSION_PUB"}
#: Both spellings sessionmail reads for must-sign: a caller's value is dropped.
MUST_SIGN_VARS = ("SESSIONMAIL_REQUIRE_SIGNATURE", "SESSION_MAIL_REQUIRE_SIGNATURE")
#: A caller's session: its key and id never reach the send.
STRIPPED_PREFIX = "CLAUDE_SESSION_"
STRIPPED_VARS = ("CLAUDE_CODE_SESSION_ID",)


def load_rules(path, only):
    """{seat: rules} for EVERY seat in the rules file, defaults filled in. --seats
    narrows who is mailed (main), never who can be addressed: with only one
    seat's names loaded, "Hey Ada, ..." looked unaddressed and woke that seat
    as a reply."""
    conf = {}
    if path and os.path.exists(path):
        with open(path) as f:
            conf = json.load(f)
    base = dict(DEFAULTS, **conf.get("defaults", {}))
    seats = conf.get("seats", {})
    for s in only:
        seats.setdefault(s, {})
    out = {}
    for seat, own in seats.items():
        r = dict(base, **(own or {}))
        r.setdefault("names", seat)
        names = r["names"]
        r["_call"] = re.compile(rf"\b({r['openers']})[,]? +({names})\b", re.I)
        r["_everyone"] = (re.compile(rf"\b({r['openers']})[,]? +({r['everyone']})\b", re.I)
                          if r.get("everyone") else None)
        r["_first"] = re.compile(rf"^\W*({names})\b", re.I) if r.get("name_first") else None
        r["_named"] = re.compile(rf"\b({names})\b", re.I)
        # each say-again phrase as a word tuple, by the keeper's one tokeniser
        phrases = r.get("again") or ()
        if isinstance(phrases, str):
            phrases = [phrases]
        elif not isinstance(phrases, (list, tuple)):
            phrases = ()                    # true, a number: no phrase list, so off
        r["_again"] = frozenset(tuple(_words(str(x))) for x in phrases if _words(str(x)))
        out[seat] = r
    return out


# -- time -------------------------------------------------------------------

def _t(rec):
    try:
        return datetime.fromisoformat(rec["ts"]).timestamp()
    except (KeyError, TypeError, ValueError):
        return None


def _start(rec):
    """When the user's words began: ts is when the turn ENDED; t0..t1 is its span."""
    t = _t(rec)
    try:
        span = float(rec["t1"]) - float(rec["t0"])
    except (KeyError, TypeError, ValueError):
        return t
    return t - span if t is not None and span > 0 else t


# -- echo: the mouth heard through the mic is not the user -------------------------
TAIL_S, ECHO_RUN = 3.0, 0.7
# The user's words may come ahead of a line's echo up to
# LEAD_WORDS of slack (3: case 1 had 3, at 0.6 s) plus LEAD_WPS words per second the
# line began after the user's turn did (5: above the fastest measured, 4.7 w/s).
LEAD_WORDS, LEAD_WPS = 3, 5.0
# A cut needs COVER of the line's played words, and the turn open by OPEN_S after the
# line began unless the turn is mostly the line (measured: the user's quotes start 2.8-20 s
# into a line and cover at most 57% of it; echoes cover 59-100%). ALIGN_SLACK: a
# word-for-word copy has as many words as the line, give or take this ("Lumen").
COVER, OPEN_S, ALIGN_SLACK = 0.5, 2.0, 2
LAG_MAX_S = 15.0    # a turn row lands at most this long after the user's last word (7.9 seen)
# no acoustic path from these to the mic: mouth hold.py BARGE_DEVICES, kept by hand
HEADPHONES = re.compile(r"airpods|headphone|headset|buds|beats", re.I)


def _words(text):
    return [w for w in "".join(c.lower() if c.isalnum() else " " for c in text).split() if w]


def is_echo(heard, line):
    p, l = _words(heard), _words(line)
    if not p or not l:
        return False
    best = 0
    for i in range(len(l)):
        for j in range(len(p)):
            k = 0
            while i + k < len(l) and j + k < len(p) and l[i + k] == p[j + k]:
                k += 1
            best = max(best, k)
    return best == 1 if len(p) == 1 else (best >= 2 and best / len(p) >= ECHO_RUN)


def _anchored(blocks, i):
    """May an echo span begin at matching block I? Yes if it is 2 words or more, or if
    it reaches the next such block by one-for-one swaps only (a misheard word for each
    of the line's words). A lone matched word followed by more of the user's words than the
    line has there are the user's, not the line's opening (measured: a turn that shared one
    word with a line whose opening whisper missed keeps all of the user's words, where
    the ratio alone would have cut the user's words from that shared word on)."""
    for x, y in zip(blocks[i:], blocks[i + 1:] + [None]):
        if x.size >= 2:
            return True
        if y is not None and y.a - x.a != y.b - x.b:
            return False
    return True


def echo_span(p, l, ahead=2):
    """(lead, k, matched): words P[lead:k] (a word list) are line L's words coming back
    through the mic, MATCHED of them exactly; (0, 0, 0) if none. The echo sits anywhere
    (mid-turn, seq 158169), beginning within the user's first AHEAD words: words before
    lead are the user's (seq 59692: a short acknowledgement, then a mouth line), and the mic joins the
    speakers' copy of a line to the user's words after it (seq 60043: a mouth line, then
    the user's question). The span is ECHO_RUN matched over
    the words it spans (whisper hears Kodi as Cody), or is a word-for-word copy, COVER
    exact ("Lumen" for "Luhmann", seq 152161); 3 matched words at least. It begins only
    where _anchored says, so a word of the user's that is also the line's ("the", "is") never
    pulls the user's words into a clipped echo. A clipped word after it that sounds like the
    line's next word is echo too (seq 59682: "current" for "correction", cut there)."""
    blocks = [b for b in difflib.SequenceMatcher(None, p, l, autojunk=False).get_matching_blocks()
              if b.size]
    lead = k = matched = at = 0
    for i, first in enumerate(blocks):
        if first.a > ahead:
            break
        if not _anchored(blocks, i):
            continue
        got = 0
        for b in blocks[i:]:
            got += b.size
            n = b.a + b.size - first.a
            if got >= 3 and n > k - lead and (got / n >= ECHO_RUN or (
                    abs(n - (b.b + b.size - first.b)) <= ALIGN_SLACK and got / n >= COVER)):
                lead, k, matched, at = first.a, b.a + b.size, got, b.b + b.size
    while (k and k < len(p) and at < len(l) and len(p[k]) >= 4
           and difflib.SequenceMatcher(None, p[k], l[at]).ratio() >= 0.5):
        k, at = k + 1, at + 1
    return lead, k, matched


def echo_suffix(heard, line):
    """How many of HEARD's last words are LINE's first words coming back: a line
    that began playing while the user was still being heard (seq 59682:
    "...And a current" was the next mouth line, "And a correction: ..."). Compared
    by characters, since whisper mishears a clipped word; 0 if none."""
    p, l = _words(heard), _words(line)
    best = 0
    for n in range(1, min(len(p), 15) + 1):
        tail = " ".join(p[-n:])
        if len(tail) < 6:
            continue
        for m in (n, n + 1):
            head = " ".join(l[:m])
            if head and difflib.SequenceMatcher(None, tail, head, autojunk=False).ratio() >= 0.75:
                best = n
                break
    return best


def cut_words(text, cut):
    """TEXT without the words whose indices are in CUT (words as _words counts them),
    each run of them cut out with the punctuation that led into the next word."""
    spans = [m.span() for m in re.finditer(r"[^\W_]+", text)]
    idx = sorted(i for i in set(cut) if i < len(spans))
    runs = []
    for i in idx:
        if runs and runs[-1][1] == i:
            runs[-1][1] = i + 1
        else:
            runs.append([i, i + 1])
    for a, b in reversed(runs):     # right to left: the spans before a stay true
        head = text[: spans[a][0]].rstrip() if a else ""
        text = (head + " " + text[spans[b - 1][1]:].lstrip(" \t.,;:!?-'\"")).strip()
    return text


class Room:
    """What the mouth has said lately: for echo, reply and light."""

    def __init__(self):
        self.lines = {}      # utt -> [queued_t, end_t|None, text, agent, interest,
                             #         played_s|None, device, text_played_est] (note())
        self.finished = []   # [(end_t, agent, utt, interest)], oldest first
        self.answered = set()  # utts a reply has already been mailed for
        self.barged = set()    # utts the user's barge-in cut (said: cut, reason barge-in)
        self.echoed = set()    # utts whose echo trim_echo() cut from the turn it last trimmed
        self.said = {}       # agent -> when its newest line finished
        self.held = None     # why verdicts() just woke nobody: a film was playing
        self.again = None    # the say-again verdicts() just gave: {seat, utt, line, ...}
        self.meta = {}       # utt -> {audio, lane, again_of, resume_of, voice, volume, session,
                             #         played_s}: what a say-again needs (note())
        self.streams = {}    # (source, session, device) -> when its stream began

    def note(self, rec):
        k, utt = rec.get("kind"), rec.get("utt")
        if k == "event" and rec.get("event") in ("listen-started", "heartbeat"):
            t, key = _t(rec), (rec.get("source"), rec.get("session"), rec.get("device"))
            try:
                self.streams[key] = t - float(rec.get("stream_s") or 0)
            except (TypeError, ValueError):
                pass
        elif k == "saying" and utt:
            # [queued, said, text, agent, interest, played_s, device, played text]
            self.lines[utt] = [_t(rec), None, str(rec.get("text") or ""), rec.get("agent"),
                               rec.get("interest") or "normal", None, rec.get("device"), None]
            self._meta(utt, rec)
            if len(self.lines) > 200:
                for old in list(self.lines)[:100]:
                    del self.lines[old]
                    self.meta.pop(old, None)
        elif k == "said" and utt:
            end = _t(rec)
            known = self.lines.get(utt)
            self._meta(utt, rec)
            agent = rec.get("agent") or (known and known[3])
            interest = rec.get("interest") or (known and known[4]) or "normal"
            if known:
                known[1] = end
                try:
                    played = float(rec.get("played_s") or 0) or None
                except (TypeError, ValueError):
                    played = None
                known[5:8] = (played, rec.get("device") or known[6], rec.get("text_played_est"))
            if end is not None and agent:
                self.said[agent] = end
                self.finished.append((end, agent, utt, interest))
                del self.finished[:-50]
                # a barge-in counts only when the mouth said how much it PLAYED: without
                # played_s the echo trim can't tell the line's own words coming back
                # (which can trip the barge guard: seq 59682) from the user's; and a line cut
                # before it played a word (played_s 0: the queue dropped behind the
                # cut one, seq 394829) was never heard, so it can't be answered
                if (rec.get("cut") and (rec.get("reason") or rec.get("cut_reason")) == "barge-in"
                        and (known and known[5] or 0) > 0):
                    self.barged.add(utt)
                self.barged &= {f[2] for f in self.finished}

    META = ("audio", "lane", "again_of", "resume_of", "voice", "volume", "session", "played_s")

    def _meta(self, utt, rec):
        """A line's say-again facts from its saying and said rows: a side map,
        so the 8-slot `lines` and every unpacking of it stay as they are. A said row's
        value wins over its saying row's; an absent one keeps what was known."""
        m = self.meta.setdefault(utt, {})
        for key in self.META:
            if rec.get(key) is not None:
                m[key] = rec[key]
        if len(self.meta) > 400:            # a said row with no saying row is noted too
            for old in [u for u in list(self.meta)[:200] if u not in self.lines]:
                del self.meta[old]

    def answer(self, utt):
        """Mark UTT answered; a say-again replay's original with it:
        one content, never answered twice as two lines."""
        self.answered.add(utt)
        of = (self.meta.get(utt) or {}).get("again_of")
        if of:
            self.answered.add(of)

    def answered_line(self, start, window, until=None):
        """The line the user's turn answers: the one that most recently FINISHED before
        the turn began (a line not yet played can't be answered),
        within WINDOW seconds, not already answered. None if none.
        A line the USER's turn cut first: a barge-in cut between START and UNTIL (the turn's
        end) is the line the user answered, ahead of any that finished before they began
        (else a turn that cut a line 2.7 s
        into the turn woke nobody). A line still
        playing under the user's turn and NOT cut stays unanswerable."""
        if until is not None:
            # not a line whose own echo was in this turn: the echo may be what tripped
            # the barge guard, so the user's words are judged by the rule below, as before
            cut = [f for f in self.finished
                   if f[2] in self.barged and f[2] not in self.answered | self.echoed
                   and start < f[0] <= until]
            if cut:
                return max(cut)
        best = None
        for end, agent, utt, interest in self.finished:
            if end <= start and (best is None or end > best[0]):
                best = (end, agent, utt, interest)
        if best is None or best[2] in self.answered or start - best[0] > window:
            return None
        return best

    def span(self, rec):
        """(start, stop, real) of the user's turn: the REAL span, stream start (heartbeat ts -
        stream_s, or listen-started) + t0..t1, when the stream is known and agrees with
        the row (the row lands up to 7.9 s after the user's last word); else today's
        estimate, ts - (t1 - t0) .. ts."""
        t = _t(rec)
        s = self.streams.get((rec.get("source", "mic"), rec.get("session"), rec.get("device")))
        try:
            a, b = s + float(rec["t0"]), s + float(rec["t1"])
            if t is not None and t - LAG_MAX_S <= b <= t + 1.0 and a <= b:
                return a, b, True
        except (KeyError, TypeError, ValueError):
            pass
        return _start(rec), t, False

    def trim_echo(self, rec):
        """(the user's words, when they began, cuts): the turn with every mouth line's echo cut
        out WHEREVER it sits, cuts = [(agent, utt, words cut)] in turn order.
        A line is a candidate while its PLAY span (said - played_s .. said) meets the
        turn's span; a headphone line has no acoustic path and is never cut (152937).
        The best-matched line is cut first and the rest re-matched on what is left. A
        turn that is all echo gives ("", None, cuts). The user's words begin when a leading
        echo's line finished, so a reply is judged against the room the user answered.
        It reads and changes words only, never REC's `speaker`; heard_rec() drops a label
        that names a seat whose line was cut."""
        text, began, stop = str(rec.get("text") or ""), _start(rec), _t(rec)
        self.echoed = set()
        start, end_r, real = self.span(rec)
        if start is None:
            return text, began, []
        p = _words(text)
        alive, cut = list(range(len(p))), {}       # cut: utt -> [word indices]
        pending = {}
        for utt, (q, end, line, _, _, played, dev, heard) in self.lines.items():
            a = end - played if end is not None and played else q
            if (a is None or HEADPHONES.search(str(dev or "")) or a > end_r + 0.5
                    or (end is not None and end + TAIL_S < start)):
                continue
            pending[utt] = (a, end, _words(heard or line))
        while pending:
            sub, best = [p[i] for i in alive], None
            for utt, (a, end, l) in pending.items():
                ld, k, n = echo_span(sub, l, LEAD_WORDS + LEAD_WPS * max(0.0, a - start))
                if (k and n >= COVER * len(l) and (best is None or n > best[3])
                        and (not real or start <= a + OPEN_S or n >= len(sub) / 2)):
                    best = (utt, ld, k, n)
            if best is None:
                break
            utt, ld, k = best[:3]
            cut[utt] = alive[ld:k]
            if cut[utt][0] == 0:      # the user's words came after the echo: they answered what it said
                began = max(began, pending[utt][1] if pending[utt][1] is not None else stop or began)
            del alive[ld:k], pending[utt]
        for utt, (a, end, l) in pending.items():   # a line that began mid-turn, at its end
            if start < a <= end_r + 0.5 and alive:
                k = echo_suffix(" ".join(p[i] for i in alive), " ".join(l))
                if k:
                    cut[utt], alive = alive[-k:], alive[:-k]
        self.echoed = set(cut)
        cuts = [(self.lines[u][3] or "mouth", u, len(w))
                for u, w in sorted(cut.items(), key=lambda x: x[1][0])]
        # one word BETWEEN two echoes is the seam misheard ("86%" for a line's
        # "eighty-six", seq 59682), not the user; a lone word before or after both
        # ("<line> <line> Yes.") is the user's answer and is kept
        gone = {i for w in cut.values() for i in w}
        if not alive or (len(alive) == 1 and {alive[0] - 1, alive[0] + 1} <= gone):
            return "", None, cuts
        return cut_words(text, gone), began, cuts

    def echoes(self, rec):
        """True if the turn is a mouth line's echo, to be dropped WHOLE. A headphone
        line has no acoustic path to the mic, so nothing it says comes back: it is
        skipped here as trim_echo() skips it (seq 169079, the user's one word
        after an AirPods line holding that word, was dropped)."""
        t = _t(rec)
        if t is None:
            return False
        text = str(rec.get("text") or "")
        for start, end, line, _, _, _, dev, _ in self.lines.values():
            if start is None or t < start - 0.5 or HEADPHONES.search(str(dev or "")):
                continue
            if end is not None and t > end + TAIL_S:
                continue
            if is_echo(text, line):
                return True
        return False


# -- twins: one utterance, two ears ------------------------------------------
# On a call on speakers the call leg and the room mic each write a turn for
# the same words: seq 61416 (call) and 61417 (mic), 0.43 s apart,
# and both were mailed to one seat. The first copy mailed wins; the
# other SOURCE's copy, inside TWIN_S and alike by TWIN_SIM (word Jaccard), is
# not mailed again to a seat that already has it. The same source twice is
# the user repeating themselves, and is mailed.
TWIN_S, TWIN_SIM = 10.0, 0.5


class Twins:
    def __init__(self):
        self.recent = []   # (t, source, words, seq, seats)

    def split(self, rec, text, seats):
        """(seats still to mail, seq of the twin or None)."""
        t = _t(rec) or time.time()
        self.recent = [r for r in self.recent if t - r[0] <= TWIN_S]
        src, w = rec.get("source", "mic"), set(_words(text))
        keep, twin = list(seats), None
        for t0, s0, w0, seq0, seats0 in self.recent:
            if s0 == src or not w or not w0:
                continue
            if len(w & w0) / len(w | w0) >= TWIN_SIM:
                if any(s in seats0 for s in keep):
                    keep, twin = [s for s in keep if s not in seats0], seq0
        return keep, twin

    def add(self, rec, text, seats):
        self.recent.append((_t(rec) or time.time(), rec.get("source", "mic"),
                            set(_words(text)), rec.get("seq"), set(seats)))


# -- a film is playing: only a turn that names a seat wakes it -----------------
# An unaddressed turn inside 120 s of a seat's line is its reply. With a film
# on, that turn is the film: two lines put into the film's own log (the
# MacBook mic, a film on the TV) were
# "answered" by its dialogue (seq 60116) and by a "17." from it
# (60762). kodi-hush pauses the film before a seat speaks, so a film playing
# again means the user pressed play: they are watching, not answering. While a video
# plays, reply and light wake nobody and the line stays answerable once the user
# pauses; "hey Alice" still wakes Alice. Kodi unreachable = today's behaviour.
class Kodi:
    def __init__(self, url, timeout=1.0):
        self.url, self.timeout = url, timeout   # asked only for an unaddressed verdict
        self.down = False

    def _rpc(self, method, **params):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                           **({"params": params} if params else {})}).encode()
        req = urllib.request.Request(self.url, body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.load(r).get("result")

    def playing(self):
        """'kodi playing at H:MM:SS' while a video plays; None when paused,
        stopped or unreachable (said once on stderr, then quietly)."""
        state = None
        try:
            players = self._rpc("Player.GetActivePlayers") or []
            vid = [x["playerid"] for x in players if x.get("type") == "video"]
            if vid:
                r = self._rpc("Player.GetProperties", playerid=vid[0], properties=["speed", "time"]) or {}
                if r.get("speed"):
                    t = r.get("time") or {}
                    state = "kodi playing at %d:%02d:%02d" % (
                        t.get("hours", 0), t.get("minutes", 0), t.get("seconds", 0))
            self.down = False
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            if not self.down:
                print(json.dumps({"kodi": "unreachable", "err": str(e)[:200]}), file=sys.stderr, flush=True)
            self.down = True
        return state


def _film(playing, room, out):
    """True when a film plays: OUT (unaddressed verdicts) is the film, not the user."""
    state = playing() if playing and out else None
    if state:
        room.held = {"media": state, "seats": sorted(out), "rules": sorted(set(out.values()))}
    return bool(state)


# -- say again (designs/say-again.md sections 1-2) ----------------------
# Stripped from the two ENDS of a turn only, with any seat's names and openers.
AGAIN_COURTESY = ("sorry", "please", "um", "uh", "er", "oh", "ok", "okay", "could you", "can you")
# A line with a saying row and no said row is in flight; one this old is a lost row,
# not a line still playing, and no longer holds a say-again off.
IN_FLIGHT_MAX_S = 600.0


def _strip_end(s, alt, front):
    """S without one ALT (a regex alternation over words) at its FRONT or back, or None."""
    m = (re.match(rf"(?:{alt})(?: |$)", s, re.I) if front
         else re.search(rf"(?:^| )(?:{alt})$", s, re.I))
    if not m or not m.group(0).strip():
        return None
    return (s[m.end():] if front else s[:m.start()]).strip()


def again_match(text, rules):
    """(the turn's words left once its ends are stripped, {seats named}), section 1.

    The words are the keeper's `_words()`; from the two ends only, repeatedly, it
    strips every seat's names (recording whose), the openers and AGAIN_COURTESY.
    Words in the middle are never stripped: that is the whole-turn guarantee. The
    caller compares what is left, whole, with a seat's phrases (`r["_again"]`)."""
    s = " ".join(_words(text or ""))
    named = set()
    strips = [(seat, r["names"]) for seat, r in rules.items()]
    strips += [(None, alt) for alt in sorted({r["openers"] for r in rules.values()})]
    strips.append((None, "|".join(w.replace(" ", " +") for w in AGAIN_COURTESY)))
    changed = True
    while s and changed:
        changed = False
        for seat, alt in strips:
            for front in (True, False):
                try:
                    left = _strip_end(s, alt, front)
                except re.error:
                    left = None
                if left is not None and left != s:
                    s, changed = left, True
                    if seat:
                        named.add(seat)
    return tuple(s.split()), named


def again_target(room, rules, his, start, until, own, playing=None):
    """(seat, utt) the say-again asks for, or None for today's verdict (section 2).

    HIS must be WHOLLY a say-again phrase (`again_match`). Named, by one seat (a
    name stripped, or its call): that seat's newest heard line. Two named: none.
    Unnamed: the newest heard line from any agent. A heard line is a speech line
    (not a bed, lane file), played_s > 0, that ended by the turn's end (UNTIL; a
    line the user's words cut counts) and within the seat's again_window of the turn's
    START. `answered` is ignored. A replay (again_of) or a resume (resume_of)
    resolves to its original. Guards: a line still in flight, the seat's own voice
    (OWN), a film playing (PLAYING()) under an unnamed turn, and `again` off.
    On a target, room.again holds it with what the mouth needs."""
    words, named = again_match(his, rules)
    if not words or not any(words in r.get("_again", ()) for r in rules.values()):
        return None
    named |= {seat for seat, r in rules.items() if r["_call"].search(his or "")}
    if len(named) > 1:
        return None
    if until is None:
        return None
    for utt, ln in room.lines.items():       # a line in flight: the newest finished is older
        if (ln[1] is None and ln[0] is not None and until - IN_FLIGHT_MAX_S <= ln[0] <= until
                and (room.meta.get(utt) or {}).get("lane") != "file"):   # a bed is no line
            return None
    best = None
    for end, agent, utt, _ in room.finished:
        if named and agent not in named:
            continue
        m = room.meta.get(utt) or {}
        if m.get("lane") == "file":
            continue                         # a bed: music or a sound, never a line
        known = room.lines.get(utt)
        try:
            played = float((known and known[5]) or m.get("played_s") or 0)
        except (TypeError, ValueError):
            played = 0.0
        if played <= 0 or end > until:
            continue                         # never heard (or amended unheard), or after the turn
        if best is None or end > best[0]:
            best = (end, agent, utt)
    if best is None:
        return None
    end, seat, utt = best
    r = rules.get(seat)
    if (not r or words not in r.get("_again", ())
            or start - end > float(r.get("again_window", 120)) or own == _local(seat)):
        return None
    if not named and playing and playing():
        return None                          # the film may be the one saying "pardon?"
    line, seen = utt, set()
    while utt not in seen:                   # a replay or a resume: its original
        seen.add(utt)
        m = room.meta.get(utt) or {}
        nxt = m.get("again_of") or m.get("resume_of")
        if not nxt:
            break
        utt = nxt
    orig, heard = room.meta.get(utt) or {}, room.meta.get(line) or {}
    known = room.lines.get(utt) or room.lines.get(line)

    def fact(key):                           # the original's own row first, else the line heard
        return orig.get(key) if orig.get(key) is not None else heard.get(key)
    room.again = {"seat": seat, "utt": utt, "line": line, "volume": fact("volume"),
                  "voice": fact("voice"), "session": fact("session"),
                  "text": known[2] if known else None, "named": sorted(named)}
    return seat, utt


def verdicts(rec, rules, room, text=None, start=None, playing=None):
    """{seat: rule} for one finished turn of the user. A turn that names a seat (or
    everyone) is for those seats; only an unaddressed turn can be a reply.
    A reply that NAMES the seat it answers is a call to it (a turn that
    thanked a seat by name right after its line was <seat>=reply, which
    the pager rides, and the seat slept 5 minutes; seq 160899).
    A bare name with no line to answer is still no call.
    TEXT/START override the record's: the user's words with any echo cut off.
    PLAYING() says whether a film plays; if so an unaddressed turn wakes nobody.
    A turn the capture labels as a seat (its `speaker` is that seat's name) is
    that seat's own voice, so it is never that seat's reply or light: a seat
    does not answer itself (a seat's own line on the laptop speakers,
    labelled with its name, came back as a reply and woke it). The dropped reply
    leaves the line answerable, for the user's real reply. Calls stand: they come from
    words that name the seat, and a label that is wrong must not lose a call.
    The keeper passes REC as heard_rec() made it: once an echo cut has taken a seat's
    line out of the turn, a label naming that seat is gone, so the user's words left
    over are his, and can be a reply to that seat."""
    text = str(rec.get("text") or "") if text is None else text
    own = speaker_label(rec)
    out = {}
    room.held = None
    room.again = None
    start = (_start(rec) if start is None else start) or 0
    # a say-again first: a command for the mouth, not words for a seat. It
    # marks nothing answered and lights no seat; no match leaves today's verdict
    target = again_target(room, rules, text, start, _t(rec), own, playing)
    if target:
        return {target[0]: "again"}
    for seat, r in rules.items():
        if r["_call"].search(text):
            out[seat] = "call"
        elif r["_everyone"] is not None and r["_everyone"].search(text):
            out[seat] = "everyone"
        elif r["_first"] is not None and r["_first"].search(text):
            out[seat] = "first"
    if out:
        return out
    windows = [float(r.get("reply_window", 120)) for r in rules.values()] or [120]
    line = room.answered_line(start, max(windows), until=_t(rec))
    if line:
        _, agent, utt, interest = line
        r = rules.get(agent)
        if (r and r.get("reply") and interest != "zero"
                and start - line[0] <= float(r.get("reply_window", 120))):
            if r["_named"].search(text):  # the user's answer, by name: a call, film or not
                room.answer(utt)
                return {agent: "call"}
            if own != _local(agent):
                if _film(playing, room, {agent: "reply"}):
                    return {}            # the line stays answerable
                room.answer(utt)         # one reply wake per line spoken
                return {agent: "reply"}
            # its own voice: no reply, and the line stays answerable
    for seat, r in rules.items():
        if own == _local(seat):
            continue                 # no light on its own voice either
        if r.get("light") and room.said.get(seat) and 0 <= start - room.said[seat] <= float(r["light"]):
            out[seat] = "light"
    if _film(playing, room, out):
        return {}
    return out


# -- say again, the action ---------
# The keeper waits for `mouth again`: a local drop, about a millisecond plus python's
# start-up, but the loop blocks on it, so this bounds the wait.
MOUTH_TIMEOUT_S = 5.0


def default_mouth():
    """The mouth CLI the keeper runs: $EARS_MOUTH, else this python's `-m mouth`. The
    installed agent runs the keeper on the python that runs `-m mouth mail`."""
    return os.environ.get("EARS_MOUTH") or shlex.join([sys.executable, "-m", "mouth"])


def again_argv(mouth, again, rec, his):
    """The argv of `mouth again` for ROOM.again (again_target's AGAIN): the utt, the
    turn's seq and words, the level the original played at, and the saying row's text,
    voice, agent and session for the mouth's text fallback (case 1). Every value goes
    as --flag=VALUE, so words that begin with "-" stay a value."""
    argv = shlex.split(mouth) + ["again", str(again["utt"])]
    seq = rec.get("seq")
    if isinstance(seq, int) and not isinstance(seq, bool):
        argv.append(f"--seq={seq}")
    if his:
        argv.append(f"--heard={his}")
    for flag, key in (("volume", "volume"), ("text", "text"), ("voice", "voice"),
                      ("agent", "seat"), ("session", "session")):
        v = again.get(key)
        if v is not None and str(v).strip():
            argv.append(f"--{flag}={v}")
    return argv


def run_again(argv, timeout=None):
    """(ok, facts) for one `mouth again`. Only exit 0 is ok: a mouth that will not run,
    times out, refuses (exit 2) or has nothing to replay (exit 3) is a failure, and the
    caller falls back to a call (section 5, cases 1 and 2). TIMEOUT: MOUTH_TIMEOUT_S."""
    timeout = MOUTH_TIMEOUT_S if timeout is None else timeout
    try:
        p = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, {"cause": "timeout", "timeout_s": timeout}
    except (OSError, ValueError) as e:
        return False, {"cause": "not run", "err": str(e)[-300:]}
    if p.returncode:
        return False, {"cause": f"exit {p.returncode}", "err": (p.stderr or "").strip()[-300:]}
    out = (p.stdout or "").strip().splitlines()
    try:
        said = json.loads(out[-1]) if out else {}
    except ValueError:
        said = {"out": out[-1][-300:]}
    return True, {"mouth": said}


def device_slug(name):
    """The plus part of a device's address, heard+<slug>@<host> (the Cc a
    room turn carries while --device-copy is on), or None for a device the
    keeper does not mail at all: None is the gate. The keeper names the device:
    airpods for any AirPods input, macbook for the built-in mic, else the
    name's lower-case words joined by "-". A BlackHole input is the call tap,
    and a Delta call is delta+call<N>, not built here: None."""
    n = str(name or "").lower()
    if not n or "blackhole" in n:
        return None
    if "airpods" in n:
        return "airpods"
    if "macbook" in n:
        return "macbook"
    return re.sub(r"[^a-z0-9]+", "-", n).strip("-") or None


# -- whose turn: one speaker per turn --------------------------------
# The capture closes a turn where another named speaker takes over, and writes
# the turn's side-car label as `speaker` (alice, bob, carol, ...); it also
# names an unsplit turn from its partials' labels (speaker_from: labels). Labels that name
# nobody never become a turn's speaker; ears.listen.speakers.NEUTRAL, kept in step
# by hand because keeper/ is stdlib-only and imports nothing from src/.
NEUTRAL_SPEAKERS = frozenset({"other", "mixed", "silent", "unheard"})


def _local(name):
    """NAME as one address local part: lower-cased, anything outside
    [a-z0-9._-] made "-", "" when nothing is left."""
    return re.sub(r"[^a-z0-9._-]+", "-", str(name).strip().lower()).strip("-.")


def speaker_label(rec):
    """The turn's `speaker` label as a local part (alice, mary-jane), or None
    for a turn with no label, or a label that is not a string, is empty or
    names nobody (NEUTRAL_SPEAKERS). Compared with a seat's key, it says the
    turn is that seat's own voice (verdicts())."""
    label = rec.get("speaker")
    if not isinstance(label, str):
        return None
    local = _local(label)
    return local if local and local not in NEUTRAL_SPEAKERS else None


def speaker_of(rec, default, domain):
    """Ears-Speaker for one turn. Mapping: the turn's `speaker` label becomes the
    --speaker address form, <label>@<domain> (alice -> alice@host1, bob -> bob@host1),
    the label lower-cased and anything outside [a-z0-9._-] made "-" so it is one
    local part. A turn with no `speaker` (neutral-only or unlabelled), or a label that is
    empty, not a string or names nobody (NEUTRAL_SPEAKERS), keeps DEFAULT:
    --speaker / $EARS_SPEAKER / unknown@<domain> (never a person's address by default).
    The keeper passes REC as heard_rec() made it, so the words an echo cut left are
    never mailed under the seat whose line was cut out of them."""
    local = speaker_label(rec)
    return f"{local}@{domain}" if local else default


def heard_rec(rec, his, cuts):
    """REC as the words HIS (what trim_echo left) are heard: who said them. A turn
    the user spoke over a seat's line is labelled with that seat (the capture names a
    turn once, and the line's voice filled most of its audio), but the cut took the
    seat's words out: what is left is the user's. So when a cut LEFT words and the
    label names a seat whose line was cut (CUTS, [(agent, utt, n)]; compared as
    speaker_label does, so " Vera " is vera), this is a shallow copy without
    `speaker`: the remainder is the default speaker (speaker_of) and is not the
    seat's own voice (verdicts()). Any other turn is REC itself: nothing cut, all
    echo, or a label naming someone else (a person's label over the echo is kept)."""
    own = speaker_label(rec)
    if not his or not own or own not in {_local(agent) for agent, _, _ in cuts}:
        return rec
    return {k: v for k, v in rec.items() if k != "speaker"}


def rule_header(hit, domain):
    """Ears-Rule for the device mail: comma-separated key=rule entries keyed by
    ADDRESS (the pager compares each entry whole). "hey everyone" is the
    one entry everyone=everyone, not an entry per seat. Empty: nobody's.
    An entry calls its seat only when its rule is exactly `call` (sessionmail
    src/sessionmail/_parsers.py:107-108 at ee5ce67, Entry.calls; read, never
    imported): `again`, like reply, light and first, calls nobody."""
    out = {"everyone=everyone" if r == "everyone" else f"{s}@{domain}={r}"
           for s, r in hit.items()}
    return ", ".join(sorted(out))


#: Folds unicodedata leaves alone: typographic quotes, dashes and the ellipsis
#: (a copy of mouth.notice._FOLD: keeper/ imports nothing from src/).
_FOLD = {"\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u2032": "'",
         "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"', "\u2033": '"',
         "\u00ab": '"', "\u00bb": '"', "\u2013": "-", "\u2014": "-", "\u2212": "-",
         "\u2026": "...", "\u00a0": " "}


def _ascii(text):
    """TEXT as ASCII for a subject: `message send` refuses any other,
    and a heard turn is STT text, with curly quotes and accents in it. Typographic
    marks become their plain forms, an accent is dropped from its letter, and
    anything left becomes "?". The body keeps the words as heard."""
    import unicodedata
    t = "".join(_FOLD.get(c, c) for c in " ".join(str(text or "").split()))
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return "".join(c if " " <= c <= "~" else "?" for c in t)


def clean_subject(text):
    """At most SUBJECT_MAX ASCII characters that cannot execute: a backtick becomes
    ', and $( becomes $ ( (`message send` refuses either), cut at a word with ..."""
    s = _ascii(text).replace("`", "'").replace("$(", "$ (").strip()
    if len(s) <= SUBJECT_MAX:
        return s
    cut = s[: SUBJECT_MAX - 3]
    return (cut.rsplit(" ", 1)[0] if " " in cut else cut) + "..."


def subject_of(text):
    return clean_subject(text) or "(the user spoke)"


def mail_for(rec, to, rules_hit, kind="spoken", his=None, rule=None, frm=None, speaker=None,
             whole=True, cuts=(), cc=()):
    """HIS is the turn with the mouth's echo cut off; the whole turn follows it.
    TO is local parts; FRM the sender's local part (None: the keeper's own).
    CC is local parts copied on the SAME send (a room turn's device address):
    one message, one Message-ID, never a second send. Only a mail with a Cc
    carries the "cc" key, so every other mail is unchanged.
    RULE is the Ears-Rule value; None keys RULES_HIT by seat (--to seats).
    WHOLE=False drops the "heard whole" copy: a device mail rides WHOLE into
    every subscriber, so the copy would double the carry.
    CUTS, [(seat, utt, n)] from trim_echo, is Ears-Echo: ONE header, a comma list
    with an entry per line cut, '<seat>/<utt> <n> words cut'."""
    whole_text = str(rec.get("text") or "")
    his = whole_text if his is None else his
    body = his + "\n" + (f"\n-- heard whole, echo included --\n{whole_text}\n"
                         if whole and his != whole_text else "")
    if rule is None:
        rule = ", ".join(f"{s}={r}" for s, r in sorted(rules_hit.items()))
    return {
        "to": sorted(to),
        **({"cc": sorted(cc)} if cc else {}),
        "from": frm,
        "subject": subject_of(his) if kind == "spoken"
        else clean_subject(f"your line was cut: {rec.get('reason') or rec.get('cut_reason') or 'cut'}"),
        "body": body if kind == "spoken" else json.dumps(rec) + "\n",
        "headers": {                 # no X- headers
            "Ears-Kind": kind,
            "Ears-Rule": rule,
            "Ears-Seq": str(rec.get("seq", "")),
            "Ears-Start": datetime.fromtimestamp(_start(rec)).astimezone().isoformat(timespec="milliseconds")
            if _start(rec) else "",
            "Ears-End": str(rec.get("ts", "")),
            "Ears-Source": str(rec.get("source", "")),
            "Ears-Device": str(rec.get("device", "")),
            "Ears-Speaker": speaker or "",
            **({"Ears-Echo": ", ".join(f"{s}/{u} {n} words cut" for s, u, n in cuts)}
               if cuts else {}),
            # The turn's kept audio, when recording was on and it was written
            **({"Ears-Audio": str(rec["audio"])} if rec.get("audio") else {}),
        },
    }


def build(m, sender, domain):
    """``message send``'s arguments for the ONE recipient in m["to"], the
    body on stdin. From is not an argument: it is the seat in child_env, which is
    m["from"] (the room's address) or SENDER. Each of m["cc"] is a --cc on
    this same send, not another recipient: one send is one message with one
    Message-ID. The keeper mints no Message-ID:
    the send mints one, and --json returns it. Every non-empty Ears-*
    is a --header; the fleet's senders carry only X-Task/X-Project, so the keeper
    writes its own."""
    if len(m["to"]) != 1:
        raise ValueError(f"one recipient per send, got {m['to']!r}")
    a = ["send", f"{m['to'][0]}@{domain}"]
    for c in m.get("cc", ()):
        a += ["--cc", f"{c}@{domain}"]
    a += ["-s", clean_subject(m["subject"]), "-f", "-", "--raw", "--no-shorten", "--json"]
    for k, v in m["headers"].items():
        v = " ".join(str(v or "").split())     # one line per header: send refuses a break
        if v:
            a += ["--header", f"{k}: {v}"]
    return a


def child_env(seat, environ=None):
    """The environment ``message send`` runs with (as the mouth's
    notice.child_env). Every CLAUDE_SESSION_* (and the session id) and
    both must-sign spellings are dropped: a keeper started from a seat's shell must
    never sign, or answer, as that seat. The seat is SEAT (TM_AGENT_ADDRESS and
    EMAIL, so an inherited EMAIL never wins), its name Ears. $EARS_MAIL_SIGNER_SOCK
    and $EARS_MAIL_SIGNER_PUB, both set, become the send's key and must-sign turns
    on; unset, the turn goes unsigned, as it did by postfix. From is left
    unqualified: sessionmail qualifies it, never the keeper."""
    e = os.environ if environ is None else environ
    env = {k: v for k, v in e.items()
           if not k.startswith(STRIPPED_PREFIX) and k not in STRIPPED_VARS
           and k not in MUST_SIGN_VARS}
    env["TM_AGENT_ADDRESS"] = env["EMAIL"] = seat
    env["SESSIONMAIL_FROM_NAME"] = FROM_NAME
    keys = {var: e.get(name) for name, var in SIGNER_VARS.items()}
    if all(keys.values()):
        env.update({var: os.path.expanduser(v) for var, v in keys.items()})
        env[MUST_SIGN_VARS[0]] = "1"
    return env


#: Where uv installs sessionmail's `message`, relative to HOME, looked in after PATH:
#: a copy of ears.message_bin.KNOWN (tests/test_message_bin.py holds it). A launchd
#: PATH holds neither, so a lookup on PATH alone lost every send.
KNOWN_MESSAGE = (".local/bin/message", ".local/share/uv/tools/sessionmail/bin/message")


def resolve_message(environ=None):
    """(the send command as an argv with an absolute program, "") or (None, why not).
    $EARS_MAIL_MESSAGE (shlex-split) is honoured in any process, looked up on PATH
    alone: tests and rigs point it at a fake, and what was named is what runs. Else
    sessionmail's `message`: on PATH, else each of KNOWN_MESSAGE under HOME (a copy
    of ears.message_bin.find). HOME and PATH are ENVIRON's, never the process's
    alone. Never postfix's binary: there is no fallback to it."""
    import shutil
    e = os.environ if environ is None else environ
    explicit = e.get(MESSAGE_VAR, "").strip()
    where = f"${MESSAGE_VAR}: " if explicit else ""
    try:
        cmd = shlex.split(explicit) if explicit else [DEFAULT_MESSAGE]
    except ValueError as err:              # unbalanced quotes: said, never a crash
        return None, f"no message binary ({where}{err})"
    found = cmd and shutil.which(os.path.expanduser(cmd[0]), path=e.get("PATH"))
    home = e.get("HOME") or ""
    known = [os.path.join(home, rel) for rel in KNOWN_MESSAGE] if home and not explicit else []
    if not found:
        found = next((k for k in known if os.path.isfile(k) and os.access(k, os.X_OK)), None)
    if not found:
        name = cmd[0] if cmd else "(empty)"
        if explicit:
            return None, (f"no message binary ({where}{name} is not an executable on PATH; "
                          f"it is sessionmail's)")
        looked = ", ".join(["PATH", *("~/" + rel for rel in KNOWN_MESSAGE)]) if known else "PATH (no HOME)"
        return None, f"no message binary ({name} is not an executable on {looked}; it is sessionmail's)"
    return [os.path.abspath(found), *cmd[1:]], ""


_MESSAGE = None          # (cmd, why): resolved once (main), or at the first send; a
                         # missing one is looked for again at each send


def _answer(stdout):
    """--json's one object (the last line that parses), or {}."""
    for line in reversed((stdout or b"").decode("utf-8", "replace").splitlines()):
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return {}


def send(m, sender, domain):
    """Hand ONE mail (one recipient) to sessionmail's `message send`; (code, id, err).
    ID is the one --json returned (None when nothing was sent); ERR is "kind: why"
    from --json, else send's stderr. A missing binary, a timeout or an OSError is a
    nonzero code with ERR saying so: the keeper keeps running, and never falls back
    to an MTA."""
    global _MESSAGE
    if _MESSAGE is None or _MESSAGE[0] is None:     # missing: looked for again, so a
        _MESSAGE = resolve_message()                # `message` installed later is found
    cmd, why = _MESSAGE
    if cmd is None:
        return 127, None, why
    try:
        p = subprocess.run([*cmd, *build(m, sender, domain)], input=m["body"].encode("utf-8"),
                           env=child_env(m.get("from") or sender), capture_output=True,
                           timeout=SEND_TIMEOUT_S, close_fds=True, check=False)
    except subprocess.TimeoutExpired:
        return 124, None, f"message send timed out after {SEND_TIMEOUT_S:g}s"
    except OSError as e:
        return 126, None, f"message send: {type(e).__name__}: {e}"
    got = _answer(p.stdout)
    if p.returncode == 0 and got.get("sent", "yes") == "yes":
        return 0, got.get("id"), ""
    said = " ".join(str(got.get("message") or p.stderr.decode("utf-8", "replace")).split())
    return (p.returncode or 1), None, (f"{got['kind']}: {said}" if got.get("kind") else said)


#: The voice settings folder under $HOME: a copy of ears.home.SETTINGS (keeper/ imports
#: nothing from src/; tests/test_settings_dir.py holds every copy to it).
VOICE_SETTINGS = ".sessionkit/voice/settings"


def sessionvoice_home():
    """sessionvoice's home: a copy of ears.home.sessionvoice_home (tests/test_home.py holds
    it). $SESSIONVOICE_HOME, $VOICEMODE_BASE_DIR, ~/.sessionvoice or ~/.voicemode if it
    holds the heard log, else ~/.sessionvoice."""
    import glob, os
    for name in ("SESSIONVOICE_HOME", "VOICEMODE_BASE_DIR"):
        if os.environ.get(name):
            return os.path.expanduser(os.path.expandvars(os.environ[name]))
    for d in ("~/.sessionvoice", "~/.voicemode"):
        c = os.path.expanduser(d + "/logs/conversations")
        if os.path.exists(c + "/heard.lock") or glob.glob(glob.escape(c) + "/heard_*.jsonl"):
            return os.path.expanduser(d)
    return os.path.expanduser("~/.sessionvoice")


# -- a Delta call's turns: To its call box, calls+<bot>-<N> --------------------
# A FINAL turn of a Delta call (source call, device call:N) goes To the call's box,
# calls+<bot>-<N>@<domain>, the box the mouth reads (src/mouth/box.py call_box):
# the caller's turns come in here, the seats' replies go To the same box. The bot is
# learned from the call's own leg: its call-ears.sock answers a probe with
# {"agent", "profile", "calls": [N...]}.
CALL_DEVICE = re.compile(r"^call:(\d+)$")
CALL_SUBJECT_MAX = 56
CALL_BOTS_KEPT = 256          # the newest {N: bot} entries kept in --state
PROBE_TIMEOUT_S = 1.0         # as keeper/call-ears: a leg that never answers stalls no mail


def call_socks():
    """Every Delta bot's ear-leg socket: the keeper's twin of ears.listen.sources.call_socks
    (keeper/ imports nothing from src/; tests/test_call_sock_search.py holds the two
    equal). $EARS_CALL_SOCKS if it is set at all (colon-separated), else its old name;
    else call-ears.sock in every bot root (phone_roots.bot_roots: sessionphone's root,
    $SESSIONPHONE_ROOT alone or the new and old roots; $DC_ROOT; each ~/.<agent>/delta-chat),
    each [/<profile>]."""
    env = os.environ.get("EARS_CALL_SOCKS", os.environ.get("VOICEMODE_CALL_EARS_SOCKS"))
    if env:
        return [p for p in env.split(":") if p]
    return phone_roots.bot_socks("call-ears.sock")


def call_roots():
    """Every bot root, in search order (phone_roots.bot_roots, as sources.bot_roots)."""
    return phone_roots.bot_roots()


def web_leg(path):
    """True for sessionphone web's leg, <phone root>/web/call-ears.sock at any of
    sessionphone's roots: it names no Delta bot, and its own server already mails each
    web turn to its seat."""
    return phone_roots.web_leg(path)


def bot_of(reply):
    """The bot a probe reply names: agent, or <agent>-<profile> when profile is set, as
    one local part; None when it names no agent."""
    if not isinstance(reply, dict):
        return None
    agent = _local(reply.get("agent") or "")
    if not agent:
        return None
    profile = _local(reply.get("profile") or "")
    return f"{agent}-{profile}" if profile else agent


PROBE_MAX_BYTES = 65536       # a probe reply is one short JSON line


def probe(path, timeout=PROBE_TIMEOUT_S):
    """One leg's probe reply, a dict; {} when it cannot be read in TIMEOUT, in all: a
    leg that trickles bytes and never ends its line stalls no mail either."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    deadline = time.monotonic() + timeout
    try:
        s.settimeout(timeout)
        s.connect(path)
        s.sendall(b'{"probe": true}\n')
        buf = b""
        while b"\n" not in buf:
            left = deadline - time.monotonic()
            if left <= 0 or len(buf) > PROBE_MAX_BYTES:
                return {}
            s.settimeout(left)
            c = s.recv(4096)
            if not c:
                break
            buf += c
        got = json.loads(buf.split(b"\n")[0] or b"{}")
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError, RecursionError):   # RecursionError: a reply nested
        return {}                                   # thousands deep, still under the cap
    finally:
        s.close()


class CallBots:
    """{call id: bot}, learned by probing every leg. A call is learned the first time
    any row names its call:N (a partial is written while the call is live), probed
    again for a turn whose call is unknown, and KEPT after hang-up, so the last turn
    still finds its box; --state keeps the newest CALL_BOTS_KEPT across restarts.
    A capture's listen-started forgets the call and probes afresh: a call id is the
    bot's message id, so another bot's later call can reuse a kept one.
    When two legs list one call, the first in sorted order wins (as CallSource)."""

    def __init__(self, saved=None, socks=call_socks, probe=probe):
        self.bots, self.seen = {}, set()
        self._socks, self._probe = socks, probe
        for k, v in (saved if isinstance(saved, dict) else {}).items():
            try:
                if isinstance(v, str) and v:
                    self.bots[int(k)] = v
            except (TypeError, ValueError):
                continue
        self._trim()

    def _trim(self):
        while len(self.bots) > CALL_BOTS_KEPT:
            del self.bots[next(iter(self.bots))]

    def learn(self):
        """Probe every leg (web legs excepted) and note each call it lists."""
        found = {}
        for path in self._socks():
            if web_leg(path):
                continue
            reply = self._probe(path)
            bot = bot_of(reply)
            calls = reply.get("calls") if bot else None
            if not isinstance(calls, list):     # a leg's bad reply names no call, and
                continue                        # never stops the device mail
            for n in calls:
                if isinstance(n, int) and not isinstance(n, bool):
                    found.setdefault(n, bot)
        for n, bot in found.items():
            self.bots.pop(n, None)              # re-learned: the newest again
            self.bots[n] = bot
        self._trim()

    def saw(self, n, started=False):
        """A row named call:N: probe the first time this process sees it, and again at
        each capture's start (STARTED), with the kept bot forgotten: if the probe then
        misses it, the turn probes again, and never goes to an earlier call's bot."""
        if started:
            self.bots.pop(n, None)
            self.seen.discard(n)
        if n not in self.seen:
            self.seen.add(n)
            if len(self.seen) > 4 * CALL_BOTS_KEPT:
                self.seen = {n}
            self.learn()

    def bot(self, n):
        """The bot of call N, probing again if it is unknown; None if no leg names it."""
        if n not in self.bots:
            self.learn()
        return self.bots.get(n)

    def state(self):
        return {str(n): b for n, b in self.bots.items()}


def call_of(rec):
    """N for a row of a Delta call's capture (device call:N), else None."""
    m = CALL_DEVICE.match(str(rec.get("device") or ""))
    return int(m.group(1)) if m else None


def call_subject(rec):
    """<speaker>: <text>, cut to CALL_SUBJECT_MAX, made safe; "a turn" when empty."""
    text = " ".join(str(rec.get("text") or "").split())
    who = rec.get("speaker")
    s = (f"{who.strip()}: " if isinstance(who, str) and who.strip() else "") + text
    return clean_subject(s[:CALL_SUBJECT_MAX]) or "a turn"


def call_mail(rec, bot, n, frm):
    """The call box's turn mail: To calls+<bot>-<N>, From FRM, exactly four headers
    (Ears-Kind: turn, Ears-Seq when the seq is an int, Call-Id, Call-Bot), the whole
    row as one JSON line for its body. No Ears-Rule: a call-box turn wakes no rule."""
    seq = rec.get("seq")
    headers = {"Ears-Kind": "turn"}
    if isinstance(seq, int) and not isinstance(seq, bool):
        headers["Ears-Seq"] = str(seq)
    headers.update({"Call-Id": str(n), "Call-Bot": bot})
    return {"to": [f"calls+{bot}-{n}"], "from": frm, "subject": call_subject(rec),
            "body": json.dumps(rec, ensure_ascii=False) + "\n", "headers": headers}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rules", default=os.path.expanduser(f"~/{VOICE_SETTINGS}/wake.json"))
    ap.add_argument("--to", choices=("device", "seats"), default="device",
                    help="device (default): every finished turn To and From SENDER+ROOM@DOMAIN, "
                         "Cc SENDER+<device>@DOMAIN while --device-copy is on: "
                         "the genie contract; seats: the pre-genie mail To each seat it is for. "
                         "Never both for one seat: it would get each call twice")
    ap.add_argument("--speaker", default=os.environ.get("EARS_SPEAKER", ""),
                    help="Ears-Speaker of a turn with no speaker label: whose turns the mic hears "
                         "(default $EARS_SPEAKER, else unknown@DOMAIN). A turn the capture split at a "
                         "speaker change carries its label, mailed as LABEL@DOMAIN")
    ap.add_argument("--seats", default="", help="comma list; only these seats are mailed: the turns "
                    "under --to seats, the cut notices under either (side-by-side tests)")
    ap.add_argument("--dry-run", action="store_true", help="print the mails, send nothing")
    ap.add_argument("--from-start", action="store_true", help="read the logs from the start, not the end")
    ap.add_argument("--once", action="store_true", help="read to the end and exit")
    ap.add_argument("--state", help="keeps the place across restarts (no gaps, no replays)")
    ap.add_argument("--fire-log", help="append one JSON line per mail sent (who woke, on what)")
    ap.add_argument("--sender", default=os.environ.get("EARS_MAIL_FROM", "heard"),
                    help="the keeper's box: a room turn is To and From SENDER+ROOM@DOMAIN "
                         "(default $EARS_MAIL_FROM, else heard)")
    ap.add_argument("--room", default=os.environ.get(ROOM_VAR) or ROOM, metavar="TAG",
                    help="the room's plus tag: every finished mic turn, on any input device, is "
                         "To and From SENDER+TAG@DOMAIN (default $EARS_MAIL_ROOM, else room). "
                         "One tag: lower-case letters, digits and -")
    ap.add_argument("--device-copy", choices=("on", "off"), default="on",
                    help="on (default): the same send also goes Cc SENDER+<device>@DOMAIN, so a "
                         "session subscribed to a device's address keeps hearing that device; "
                         "off: no Cc, the room's address only")
    ap.add_argument("--domain", default=os.environ.get("EARS_MAIL_DOMAIN", socket.gethostname().split(".")[0]),
                    help="the seats' doorbells are SEAT@DOMAIN (default: this host's short name)")
    ap.add_argument("--logdir", default=os.environ.get("EARS_HEARD_DIR"),
                    help="where the heard log lives: configuration, not a constant, so it can "
                         "move where seats can't read it (least privilege: a seat need "
                         "not read the whole log)")
    ap.add_argument("--source", default="mic,call")
    ap.add_argument("--poll", type=float, default=0.1)
    ap.add_argument("--kodi", default=os.environ.get("KODI_RPC", ""),
                    help="Kodi's JSON-RPC URL: while a video plays there, only a turn that names "
                         "a seat wakes it (default $KODI_RPC; empty = off)")
    ap.add_argument("--mouth", default=default_mouth(), metavar="CMD",
                    help="the mouth CLI an again verdict runs as CMD again UTT (default "
                         "$EARS_MOUTH, else this python's -m mouth). It has 5 s; if it fails the "
                         "turn is a call instead. Under --dry-run it is printed, never run")
    ap.add_argument("--calls", choices=("on", "off"), default="on",
                    help="--to device: each finished turn of a Delta call (device call:N) To its "
                         "call box, calls+<bot>-<N>@DOMAIN, the bot named by the call's own "
                         "call-ears.sock (default on); off: skipped, as before")
    ap.add_argument("--calls-from", default="ears", metavar="LOCAL",
                    help="the call-box turn mail's From, LOCAL@DOMAIN (default ears)")
    a = ap.parse_args(argv)
    if not _TAG.fullmatch(a.room):
        ap.error(f"--room {a.room!r}: one plus tag, lower-case letters, digits and - only")
    speaker = a.speaker or f"unknown@{a.domain}"  # an unplaced voice is not the operator
    only = [s.strip() for s in a.seats.split(",") if s.strip()]
    rules = load_rules(a.rules, only)
    if not rules:
        # a fresh install ships no seats (keeper/defaults/wake.json). Device mail
        # still goes for every finished turn, just with no call marks; seats mail has no one.
        if a.to == "seats":
            ap.error("no seats: give --seats or a rules file with seats")
        print(f"ears-mail: no seats in {a.rules}: no turn is a call; add one with: "
              f"ears wake add <seat>", file=sys.stderr, flush=True)
    mailto = set(only) or set(rules)
    sources = {x.strip() for x in a.source.split(",") if x.strip()}
    base = sessionvoice_home()
    logdir = os.path.expanduser(a.logdir or os.path.join(base, "logs", "conversations"))

    def files():
        return sorted(glob.glob(os.path.join(logdir, "heard_????-??-??.jsonl")))[-2:]

    offsets, room, twins = {}, Room(), Twins()
    kodi = Kodi(a.kodi) if a.kodi else None
    saved, saved_bots = {}, {}
    if a.state and os.path.exists(a.state):
        try:
            st = json.load(open(a.state))
            saved, saved_bots = st.get("offsets", {}), st.get("call_bots") or {}
        except (ValueError, AttributeError):
            saved, saved_bots = {}, {}
    calls = CallBots(saved_bots) if a.to == "device" and a.calls == "on" else None
    for p in files():
        size = os.path.getsize(p)
        if a.from_start:
            offsets[p] = 0
            continue
        with open(p, "rb") as f:  # learn the lines already playing (last 64 KB)
            f.seek(max(0, size - 65536))
            for raw in f.read().split(b"\n")[(1 if size > 65536 else 0):-1]:
                try:
                    room.note(json.loads(raw))
                except ValueError:
                    pass
        offsets[p] = saved[p] if (p in saved and saved[p] <= size) else size

    def save():
        if a.state:
            tmp = a.state + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"offsets": offsets,
                           **({"call_bots": calls.state()} if calls is not None else {})}, f)
            os.replace(tmp, a.state)

    def deliver(m, rec):
        """ONE send per recipient: `message send` refuses a whole mail
        for one dead box, and one bad seat must not silence the rest. Each copy keeps
        the one Ears-Rule, so it tells the whole verdict. One fire-log row per send,
        with the id the send returned; --dry-run prints the same rows."""
        for to in m["to"]:
            one = dict(m, to=[to])
            out = {"at": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                   "seq": rec.get("seq"), "to": one["to"],
                   **({"cc": m["cc"]} if m.get("cc") else {}),   # the same send's copies
                   "rule": m["headers"].get("Ears-Rule"), "subject": m["subject"]}
            if a.dry_run:
                out["dry_run"] = True
                out["mail"] = one
            else:
                code, mid, err = send(one, a.sender, a.domain)
                out.update(exit=code, id=mid, **({"err": err[-300:]} if code else {}))
                if code:            # loud in the service log, not only in the fire row
                    said = " ".join(str(err or "").split())[-300:]
                    print(f"ears-mail: ERROR: send to {to} failed, exit {code}"
                          f"{': ' + said if said else ''}", file=sys.stderr, flush=True)
            line = json.dumps(out)
            print(line, flush=True)
            if a.fire_log:
                with open(os.path.expanduser(a.fire_log), "a") as f:
                    f.write(line + "\n")

    room_to = (f"every finished turn To and From {a.sender}+{a.room}@{a.domain}"
               + (f", Cc {a.sender}+<device>@{a.domain}" if a.device_copy == "on" else ""))
    print(f"ears-mail: {room_to if a.to == 'device' else 'seats ' + ', '.join(sorted(mailto))}"
          f" (names known: {', '.join(sorted(rules))}); logs {logdir}; "
          f"{'DRY RUN' if a.dry_run else 'From ' + a.sender}", file=sys.stderr, flush=True)
    if not a.dry_run:           # resolved once, to an absolute path
        global _MESSAGE
        _MESSAGE = resolve_message()
        print(f"ears-mail: sends by {' '.join(_MESSAGE[0])}" if _MESSAGE[0] else
              f"ears-mail: {_MESSAGE[1]}: every turn fails until it is installed",
              file=sys.stderr, flush=True)

    def skip(why, rec, **kw):
        print(json.dumps({"skip": why, "seq": rec.get("seq"), **kw}), file=sys.stderr, flush=True)

    def act(hit, rec, his):
        """HIT with its again entry acted on: `mouth again` replays the line,
        and the entry stays again, which calls nobody. If the mouth fails, the entry is a
        call, so the seat is woken to say it again. --dry-run prints the command only."""
        seat = next((s for s, r in hit.items() if r == "again"), None)
        if seat is None or not room.again:
            return hit
        argv = again_argv(a.mouth, room.again, rec, his)
        said = {"seq": rec.get("seq"), "seat": seat, "utt": room.again["utt"]}
        if a.dry_run:
            print(json.dumps({"again": "dry run", **said, "cmd": argv}), file=sys.stderr, flush=True)
            return hit
        ok, facts = run_again(argv)
        if ok:
            print(json.dumps({"again": "replayed", **said, **facts}), file=sys.stderr, flush=True)
            return hit
        skip("again-failed", rec, seat=seat, utt=room.again["utt"], fallback="call", **facts)
        return {**hit, seat: "call"}
    while True:
        for p in files():
            try:
                with open(p, "rb") as f:
                    f.seek(offsets.get(p, 0))
                    chunk = f.read()
            except OSError:
                continue
            whole = chunk[: chunk.rfind(b"\n") + 1]
            at = offsets.get(p, 0)
            for raw in whole.split(b"\n")[:-1]:
                at += len(raw) + 1
                offsets[p] = at
                try:
                    rec = json.loads(raw)
                except ValueError:
                    continue
                room.note(rec)
                kind = rec.get("kind")
                n = call_of(rec) if calls is not None and rec.get("source") == "call" else None
                if n is not None:       # learned while the call is live: its first row
                    calls.saw(n, kind == "event" and rec.get("event") == "listen-started")
                # the mouth tells a cut line's sender itself: stand down only
                # for a notice that reached it, so a failed: or none: row is still told
                if kind == "said" and rec.get("cut") and rec.get("agent") in mailto \
                        and not str(rec.get("notice") or "").startswith(("sent ", "waiter")):
                    deliver(mail_for(rec, [rec["agent"]], {rec["agent"]: "cut"}, kind="cut"), rec)
                    continue
                if kind != "turn" or rec.get("source", "mic") not in sources:
                    continue
                if not str(rec.get("text") or "").strip() or room.echoes(rec):
                    continue
                slug = None
                if a.to == "device":    # calls first: verdicts() marks a line answered
                    if n is not None:   # a Delta call's turn: To its call box, never heard+
                        bot = calls.bot(n)
                        if bot:
                            deliver(call_mail(rec, bot, n, a.calls_from), rec)
                        else:
                            skip("call-bot", rec, call=n)
                        continue
                    if rec.get("source", "mic") != "mic":
                        skip("call", rec, source=rec.get("source"))
                        continue
                    slug = device_slug(rec.get("device"))
                    if not slug:
                        skip("device", rec, device=rec.get("device"))
                        continue
                his, began, cuts = room.trim_echo(rec)
                if not his:
                    continue
                # who said HIS: never the seat whose echo was just cut out (heard_rec)
                his_rec = heard_rec(rec, his, cuts)
                if slug:                # EVERY finished turn of the user, To and From the room
                    hit = act(verdicts(his_rec, rules, room, his, began, kodi and kodi.playing),
                              rec, his)
                    if room.held:
                        skip("film", rec, seats=room.held["seats"], rules=room.held["rules"],
                             media=room.held["media"], mailed="without those rules")
                    # one address for the room, whatever device the capture follows; the
                    # device is Ears-Device, and (while --device-copy is on) a Cc on
                    # the same send, so its own address still hears it
                    addr = f"{a.sender}+{a.room}"   # the box's name is configuration
                    copy = f"{a.sender}+{slug}"
                    cc = [copy] if a.device_copy == "on" and copy != addr else []
                    deliver(mail_for(rec, [addr], hit, his=his, rule=rule_header(hit, a.domain),
                                     frm=addr, speaker=speaker_of(his_rec, speaker, a.domain),
                                     whole=False, cuts=cuts, cc=cc), rec)
                    continue
                hit = {s: r for s, r in verdicts(his_rec, rules, room, his, began,
                                                 kodi and kodi.playing).items() if s in mailto}
                held = room.held and [s for s in room.held["seats"] if s in mailto]
                if held:
                    print(json.dumps({"skip": "film", "seq": rec.get("seq"), "seats": held,
                                      "rules": room.held["rules"], "media": room.held["media"]}),
                          file=sys.stderr, flush=True)
                if hit:
                    keep, twin = twins.split(rec, his, hit)
                    if twin is not None:
                        print(json.dumps({"skip": "twin", "seq": rec.get("seq"), "twin_of": twin,
                                          "seats": sorted(set(hit) - set(keep))}),
                              file=sys.stderr, flush=True)
                    hit = {k: hit[k] for k in keep}
                if hit:
                    twins.add(rec, his, hit)    # a twin of a say-again replays nothing twice
                    # an again entry mails no turn: To the seat, it would match the pager's
                    # `wake to:$me` and wake it (section 4b); a failed one is now a call
                    hit = {s: r for s, r in act(hit, rec, his).items() if r != "again"}
                if hit:
                    deliver(mail_for(rec, hit.keys(), hit, his=his, cuts=cuts), rec)
            save()
        if a.once:
            return 0
        time.sleep(a.poll)


if __name__ == "__main__":
    sys.exit(main())
