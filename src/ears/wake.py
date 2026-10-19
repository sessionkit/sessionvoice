"""ears wake: register a seat, and the words that call it, in the USER wake file.

A "hey <agent-name>" wakes only a seat the wake file names.
The shipped keeper/defaults/wake.json carries no seats, so a fresh install
calls nobody by name. A seat is added here instead, to the user file ears-mail reads
by default (~/.sessionkit/voice/settings/wake.json):

  ears wake add dave --names 'dav|days'    # STT hears Dave as "Dav" and "Days"
  ears wake add dave --hint David          # the spoken name STT is primed with
  ears wake list                            # who "hey <name>" wakes, and by which names

SEAT is the seat's address's local part, WHOLE (dave, sessionmail+engineer): the device
mail's Ears-Rule writes SEAT@host=call and the pager matches it exactly.
Its names always include the key itself, first; --names adds sound-alikes. Re-adding
a seat merges its names and keeps the rest of its entry. The file is --file, else
$EARS_WAKE, else ~/.sessionkit/voice/settings/wake.json; the repo's shipped default is never written.

The seats' names also prime the STT: whisper is sent a prompt such as
"Dave, Bob." so it hears the name it was told, not "Davis" or "caves". The prompt is
built from each seat's KEY (or its --hint), never from --names: those are the
mishearings, and priming with them would bias STT toward the very errors.
"""
import argparse, fnmatch, json, os, re, sys, tempfile
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


def _alts(names):
    return [n.strip() for n in str(names or "").split("|") if n.strip()]


def merge_names(key, *names):
    """KEY first, then every alternative of each NAMES in order, de-duped (case-blind).
    The key goes in escaped: sessionmail+engineer is a literal, not "l, once or more"."""
    out, seen = [], set()
    for n in [re.escape(key)] + [a for ns in names for a in _alts(ns)]:
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
    except ValueError as e:
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


def add(seat, names=None, path=None, hint=None):
    """Add SEAT (or merge NAMES into it) in the wake file; returns (file, key, names).
    HINT, when given, sets the seat's spoken name for the STT prompt; '' removes it.
    A STRANDED user file (``ears.home.stranded``) is refused: nothing is written."""
    p = target(path)
    if is_shipped(p):
        raise WakeError(f"{p} is the shipped default (keeper/defaults): never written; "
                        f"seats go in the user file {USER_FILE}")
    why = refuse_stranded(p, "wake.json", "ears wake")   # a 0.1.0 file not moved yet
    if why:
        raise WakeError(why)
    key = seat_key(seat)
    conf = load(p)
    seats = conf.setdefault("seats", {})
    entry = seats.get(key) or {}
    entry["names"] = merge_names(key, entry.get("names"), names)
    if hint is not None:
        h = " ".join(str(hint).split())
        if h:
            entry["hint"] = h
        else:
            entry.pop("hint", None)
    seats[key] = entry
    write(p, conf)
    return p, key, entry["names"]


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
    a_.add_argument("seat", help="the seat's address local part, whole (dave, sessionmail+engineer)")
    a_.add_argument("--names", default="", help="sound-alikes, a|b|c (the seat's key is always one)")
    a_.add_argument("--hint", default=None,
                    help="the name as spoken, to prime STT with (default: the key, e.g. Dave); "
                         "'' removes it. Never a sound-alike: those are --names")
    l_ = sub.add_parser("list", help="the seats the wake file wakes, and their names")
    for s in (a_, l_):
        s.add_argument("--file", help=f"the wake file (default $EARS_WAKE, else {USER_FILE})")
    a = ap.parse_args(argv)
    try:
        if a.verb == "add":
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
