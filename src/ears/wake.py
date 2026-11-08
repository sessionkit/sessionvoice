"""ears wake: register a seat, and the words that call it, in the USER wake file.

A "hey <agent-name>" wakes only a seat the wake file names.
The shipped keeper/defaults/wake.json carries no seats, so a fresh install
calls nobody by name. A seat is added here instead, to the user file ears-mail reads
by default (~/.sessionkit/voice/settings/wake.json):

  ears wake add dave --names 'dav|days'    # STT hears Dave as "Dav" and "Days"
  ears wake add dave --hint David          # the spoken name STT is primed with
  ears wake add mini-me --names 'minnie me'  # STT writes "Minnie Me" for mini-me
  ears wake add --self                      # this seat, from its environment
  ears wake list                            # who "hey <name>" wakes, and by which names

SEAT is the seat's address's local part, WHOLE (dave, sessionmail+engineer): the device
mail's Ears-Rule writes SEAT@host=call and the pager matches it exactly.
Its names always include the key itself, first; --names adds sound-alikes and
mishearings. A key of two words or more (mini-me) is also called by its spoken form
with no --names: its words joined by spaces, hyphens or nothing ("mini me", "minime"),
which ears-mail derives from the key; "spoken": false on the seat turns it off. Any
other mishearing is one --names alternative, matched as written: 'minnie me' is
"Minnie Me", not "Minnie-Me". The names
also make the room mail's Ears-Addressed list SEAT@host whenever a turn names the seat,
anywhere in it, so a sound-alike should not be an everyday word. Re-adding
a seat merges its names and keeps the rest of its entry, and an add that changes
nothing writes nothing. The file is --file, else
$EARS_WAKE, else ~/.sessionkit/voice/settings/wake.json; the repo's shipped default is never written.
Adds are serialised by a lock file beside it (.wake.json.lock), so seats added at
once never lose each other.

--self takes SEAT from this shell's environment, as the pager names the seat: a plus
address on this host in $EMAIL, then $TM_AGENT_ADDRESS (its local part), then
$CLAUDE_CODE_AGENT. When $CLAUDE_CODE_AGENT is set and the address names another seat,
it writes nothing and exits 1: the address is the caller's, not the agent launched.
An agent file runs it at every `session start NAME`, so the seat is a wake seat from
its first launch, with no hand edit:

  start:
    context:
      system-prompt:
        - ears wake add --self

The seats' names also prime the STT: whisper is sent a prompt such as
"Dave, Bob." so it hears the name it was told, not "Davis" or "caves". The prompt is
built from each seat's KEY (or its --hint), never from --names: those are the
mishearings, and priming with them would bias STT toward the very errors.
The --hint is one of the seat's names too, added literally after the others: a
transcript primed to say "David" must call dave. A seat re-added picks up the hint
it already has; --hint '' drops the hint, never a name it added.
"""
import argparse, contextlib, fcntl, fnmatch, json, os, re, socket, sys, tempfile
from pathlib import Path

from .home import SETTINGS, refuse_stranded

#: The user wake file, as shown to the user (``~`` is expanded at each read).
USER_FILE = f"~/{SETTINGS}/wake.json"
# src/ears/wake.py -> the repo's keeper/defaults: the shipped files ears wake refuses
SHIPPED_DIR = Path(__file__).resolve().parent.parent.parent / "keeper" / "defaults"
SHIPPED_GLOB = "wake*.json"


class WakeError(Exception):
    """A refusal: the message says why."""


def target(path=None):
    """The wake file: PATH, else $EARS_WAKE, else the user file."""
    return Path(os.path.expanduser(path or os.environ.get("EARS_WAKE") or USER_FILE))


def is_shipped(path):
    """True if PATH resolves to one of the repo's keeper/defaults/wake*.json."""
    real = Path(os.path.realpath(path))
    return (real.parent == Path(os.path.realpath(SHIPPED_DIR))
            and fnmatch.fnmatch(real.name, SHIPPED_GLOB))


def seat_key(seat):
    key = str(seat or "").strip().lower()
    if not key:
        raise WakeError("no seat: give the seat's address local part, e.g. dave")
    if "@" in key:
        raise WakeError(f"seat {seat!r} has an '@': give the local part only, e.g. "
                        f"{key.split('@')[0]} (the host is added when a turn is mailed)")
    if any(c.isspace() for c in key):
        raise WakeError(f"seat {seat!r} has a space: an address local part has none")
    return key


#: The variables a seat declares its mail address in, first wins (sessionmail's
#: ADDRESS_VARS); only a PLUS address on this host is a declaration.
ADDRESS_VARS = ("EMAIL", "TM_AGENT_ADDRESS")
#: The one identity variable `session start NAME` sets from its command line.
AGENT_VAR = "CLAUDE_CODE_AGENT"
#: sessionmail's host variables, the new spelling first (its _host.VAR, OLD_VAR).
HOST_VARS = ("SESSIONMAIL_HOST", "SESSION_MAIL_HOST")
_UNSAFE_ADDRESS = re.compile(r"[^a-z0-9._+-]+")


def fold(value):
    """The local part of VALUE as sessionmail folds it (_seat.fold_address): lower case,
    a run of characters outside [a-z0-9._+-] is one '-', the '+' kept."""
    local = str(value or "").strip().lower().split("@", 1)[0]
    return _UNSAFE_ADDRESS.sub("-", local).strip("-")


def this_hosts(environ=None):
    """The names this host goes by: sessionmail's host variables (whole) and the live
    hostname's first label, lower case. Approximates sessionmail's own rule, which also
    reads its settings file."""
    env = os.environ if environ is None else environ
    names = {(env.get(v) or "").strip().lower() for v in HOST_VARS}
    names.add(socket.gethostname().split(".")[0].strip().lower())
    return {n for n in names if n}


def _on_this_host(value, environ=None):
    """True when VALUE's domain is absent or names this host (alpha, alpha.<anything>)."""
    _, at, domain = str(value).strip().lower().partition("@")
    if not at:
        return True
    return any(domain == h or domain.startswith(h + ".") for h in this_hosts(environ))


def _is_plus(local):
    base, plus, ext = local.partition("+")
    return bool(plus and base and ext)


def self_key(environ=None):
    """(key, variable): the seat this environment is, in the pager's order. A PLUS
    address on this host in $EMAIL, then $TM_AGENT_ADDRESS; else $TM_AGENT_ADDRESS's
    local part; else $CLAUDE_CODE_AGENT; each folded as sessionmail folds a local part.
    Refuses (WakeError, one line) when none is set, and when $CLAUDE_CODE_AGENT is set
    and the address names another seat: a `session start` generator inherits the
    CALLER's EMAIL, so registering it would make the caller a wake seat in the
    launched agent's place."""
    env = os.environ if environ is None else environ
    key = var = None
    for v in ADDRESS_VARS:
        value = env.get(v) or ""
        local = fold(value)
        if _is_plus(local) and _on_this_host(value, env):
            key, var = local, v
            break
    if key is None and fold(env.get("TM_AGENT_ADDRESS")):
        key, var = fold(env.get("TM_AGENT_ADDRESS")), "TM_AGENT_ADDRESS"
    agent = fold(env.get(AGENT_VAR))
    if key is None:
        if not agent:
            raise WakeError(f"no seat in this environment ({', '.join(ADDRESS_VARS)}, "
                            f"{AGENT_VAR}): give it as ears wake add SEAT")
        return agent, AGENT_VAR
    if agent and key != agent:
        raise WakeError(f"{var} names this seat {key}, but the launch is {agent} "
                        f"({AGENT_VAR}): not guessing; give the key: ears wake add {agent}")
    return key, var


def _alts(names):
    """NAMES split on each '|' that is not escaped: re.escape writes a literal pipe as
    a backslash and '|', and a stored hint must come back whole on the next add."""
    out, cur, s, i = [], "", str(names or ""), 0
    while i < len(s):
        if s[i] == "\\":
            cur += s[i:i + 2]               # an escape and what it escapes, as one
            i += 2
            continue
        if s[i] == "|":
            out.append(cur)
            cur = ""
        else:
            cur += s[i]
        i += 1
    out.append(cur)
    return [n.strip() for n in out if n.strip()]


def merge_names(key, *names, hint=None):
    """KEY first, then every alternative of each NAMES in order, then HINT, de-duped
    (case-blind). The key and the hint go in escaped, the hint as ONE alternative:
    sessionmail+engineer is a literal, not "l, once or more", and a spoken name is
    said, never a pattern."""
    out, seen = [], set()
    tail = [re.escape(hint)] if hint else []
    for n in [re.escape(key)] + [a for ns in names for a in _alts(ns)] + tail:
        if n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    joined = "|".join(out)
    try:
        re.compile(rf"\b({joined})\b", re.I)
    except re.error as e:
        raise WakeError(f"names {joined!r} do not compile as a regex: {e}") from None
    return joined


def load(path):
    p = Path(path)
    if not p.exists():
        return {}
    try:
        conf = json.loads(p.read_text())
    except (ValueError, RecursionError) as e:
        raise WakeError(f"{p} is not JSON ({e}): fix or move it, nothing written") from None
    if not isinstance(conf, dict) or not isinstance(conf.get("seats", {}), dict):
        raise WakeError(f"{p} has no seats object: fix or move it, nothing written")
    return conf


def write(path, conf):
    """Atomic: a temp file beside PATH, then rename over it."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=p.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(conf, f, indent=2)
            f.write("\n")
        os.replace(tmp, p)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def lock_path(path):
    """The lock file beside the wake file: .wake.json.lock. Never removed."""
    p = Path(path)
    return p.with_name(f".{p.name}.lock")


@contextlib.contextmanager
def locked(path):
    """An exclusive flock on lock_path(PATH), held for the block: concurrent adds take
    turns at load, merge and write. ears-mail reads without it (the rename keeps every
    read whole). Closing the descriptor releases the lock."""
    lp = lock_path(path)
    lp.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lp, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def add(seat, names=None, path=None, hint=None):
    """Add SEAT (or merge NAMES into it) in the wake file; returns (file, key, names).
    HINT, when given, sets the seat's spoken name for the STT prompt; '' removes it.
    The seat's hint (HINT, else the one already stored) is merged into its names too,
    so a transcript that says it is a call. Names only grow here: '' drops the hint,
    never a name it added.
    A STRANDED user file (``ears.home.stranded``) is refused: nothing is written.
    Load, merge and write hold the lock (``locked``), taken after every refusal, so a
    refused add leaves no lock file; an add that changes nothing writes nothing (the
    file keeps its inode and mtime, and ears-mail reloads nothing)."""
    p = target(path)
    if is_shipped(p):
        raise WakeError(f"{p} is the shipped default (keeper/defaults): never written; "
                        f"seats go in the user file {USER_FILE}")
    why = refuse_stranded(p, "wake.json", "ears wake")   # a 0.1.0 file not moved yet
    if why:
        raise WakeError(why)
    key = seat_key(seat)
    given = " ".join(str(hint).split()) if hint else None
    merge_names(key, names, hint=given)       # a bad --names is refused before the lock
    with locked(p):
        conf = load(p)
        seats = conf.setdefault("seats", {})
        before = json.dumps(seats.get(key), sort_keys=True)
        entry = dict(seats.get(key) or {})
        if hint is not None:
            h = " ".join(str(hint).split())
            if h:
                entry["hint"] = h
            else:
                entry.pop("hint", None)
        h = entry.get("hint")
        h = " ".join(h.split()) if isinstance(h, str) else None
        entry["names"] = merge_names(key, entry.get("names"), names, hint=h)
        seats[key] = entry
        if not (p.exists() and json.dumps(entry, sort_keys=True) == before):
            write(p, conf)
    return p, key, entry["names"]


def spoken_on(conf, key):
    """Whether ears-mail derives KEY's spoken forms: the seat's "spoken", else the
    file's defaults', else on (the keeper's rule)."""
    defaults = conf.get("defaults") if isinstance(conf.get("defaults"), dict) else {}
    entry = (conf.get("seats") or {}).get(key)
    entry = entry if isinstance(entry, dict) else {}
    return bool(entry.get("spoken", defaults.get("spoken", True)))


def self_line(key, names, path):
    """The one line `ears wake add --self` prints, for the seat's system prompt."""
    try:
        on = spoken_on(load(path), key)
    except WakeError:
        on = True
    said = spoken(key) if on else key
    return (f'ears wake: this seat is a wake seat, {key}: "hey {said}" calls it '
            f'(names {names}; {path})')


def seats(path=None):
    """{seat: names} in the wake file (names default to the key, as ears-mail's)."""
    conf = load(target(path))
    return {k: (v or {}).get("names") or k for k, v in conf.get("seats", {}).items()}


# -- the STT hint ---------------------------------------------------
# whisper takes at most 224 prompt tokens and, past that, silently keeps the LAST ones
# (measured); a char budget well under it drops whole trailing
# seats instead, so which seats are hinted never depends on a tokenizer.
HINT_MAX_CHARS = 400


def spoken(key):
    """A seat key as said aloud: its words (split on anything not a letter or digit),
    title-cased: dave -> Dave, sessionmail+engineer -> Sessionmail Engineer.
    This is the one place the casing is set."""
    return " ".join(w.capitalize() for w in re.split(r"[^0-9A-Za-z]+", str(key)) if w)


def hint_words(path=None):
    """One spoken name per seat, in file order: the seat's "hint" when it is a non-empty
    string, else spoken(key). Never the names field's sound-alikes. De-duped (case-blind).
    Raises WakeError on a bad file, as seats() does; a missing file is []."""
    conf = load(target(path))
    out, seen = [], set()
    for key, entry in conf.get("seats", {}).items():
        h = entry.get("hint") if isinstance(entry, dict) else None
        name = " ".join(h.split()) if isinstance(h, str) and h.strip() else spoken(key)
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out


def hint_prompt(words, limit=HINT_MAX_CHARS):
    """The prompt text for WORDS: a bare comma list, "Dave, Bob, Carol.", with no opener.
    Measured: the "Hey X." sentence shape echoed on
    silence and swallowed the last seat; the bare list did neither. Whole seats past
    LIMIT chars are dropped; no words is ''."""
    kept = []
    for w in words:
        if len(", ".join([*kept, w]) + ".") > limit:
            break
        kept.append(w)
    return ", ".join(kept) + "." if kept else ""


class WakeHint:
    """The STT prompt from the wake file, following it live: a zero-arg callable that
    stats the file on every call and rebuilds the prompt only when it changed, so
    `ears wake add` reaches the next chunk with no restart. A missing, unreadable or
    unparseable file is '' (the request goes out as it did before the hint), never an
    error. PATH as target(): PATH, else $EARS_WAKE, else the user file, resolved per call."""

    def __init__(self, path=None):
        self._path = path
        self._stamp = self._text = None

    def _stat(self, p):
        try:
            st = os.stat(p)
        except OSError:
            return None
        return (str(p), st.st_mtime_ns, st.st_size, st.st_ino)

    def __call__(self):
        p = target(self._path)
        stamp = self._stat(p)
        if stamp != self._stamp or self._text is None:
            text = ""
            if stamp is not None:
                try:
                    text = hint_prompt(hint_words(p))
                except (WakeError, OSError, AttributeError, TypeError):
                    text = ""
            self._stamp, self._text = stamp, text
        return self._text


def _hint_note(key, path=None):
    """'  hint: David' when SEAT has its own hint; '' when the key is the hint."""
    entry = load(target(path)).get("seats", {}).get(key)
    h = entry.get("hint") if isinstance(entry, dict) else None
    return f"  hint: {h}" if isinstance(h, str) and h.strip() else ""


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ears wake", description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("\n", 2)[2])
    sub = ap.add_subparsers(dest="verb", required=True)
    a_ = sub.add_parser("add", help="add a seat (or more names for one) to the user wake file")
    a_.add_argument("seat", nargs="?",
                    help="the seat's address local part, whole (dave, sessionmail+engineer)")
    a_.add_argument("--self", dest="self_", action="store_true",
                    help="this seat, from the environment: a plus address on this host in "
                         "$EMAIL, then $TM_AGENT_ADDRESS, then $CLAUDE_CODE_AGENT; refused "
                         "when $CLAUDE_CODE_AGENT names another seat. Not with SEAT")
    a_.add_argument("--names", default="", help="sound-alikes and mishearings, a|b|c; a mishearing of more than "
                         "one word is one alternative, as --names 'minnie me' "
                         "(the key is always one)")
    a_.add_argument("--hint", default=None,
                    help="the name as spoken, to prime STT with (default: the key, e.g. Dave); "
                         "it is added to the seat's names too. '' removes the hint, not a "
                         "name it added. Never a sound-alike: those are --names")
    l_ = sub.add_parser("list", help="the seats the wake file wakes, and their names")
    for s in (a_, l_):
        s.add_argument("--file", help=f"the wake file (default $EARS_WAKE, else {USER_FILE})")
    a = ap.parse_args(argv)
    try:
        if a.verb == "add":
            if a.self_ == (a.seat is not None):
                a_.error("give SEAT or --self, not both" if a.self_
                         else "give SEAT, or --self for this seat")
            if a.self_:
                p, key, names = add(self_key()[0], a.names, a.file, hint=a.hint)
                print(self_line(key, names, p))
                return 0
            p, key, names = add(a.seat, a.names, a.file, hint=a.hint)
            print(f"{key}: {names}{_hint_note(key, a.file)}  ({p})")
            return 0
        p = target(a.file)
        found = seats(a.file)
        if not found:
            print(f"no seats in {p}: no turn is a call; add one with: ears wake add <seat>",
                  file=sys.stderr)
            return 0
        for key, names in sorted(found.items()):
            print(f"{key}: {names}{_hint_note(key, a.file)}")
        return 0
    except WakeError as e:
        print(f"ears wake: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
