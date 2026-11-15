"""Where a Delta bot keeps its call sockets: the keeper's stdlib copy of the rule.

keeper/call-ears and keeper/ears-mail.py import this from beside themselves (keeper/
imports nothing from src/). Its twin is ears.listen.sources (phone_roots, bot_roots,
bot_socks), which the mouth's call_socks uses too; tests/test_call_sock_search.py holds
the two equal.

The rule, as sessionphone moves its root under ~/.sessionkit:

- sessionphone's root: $SESSIONPHONE_ROOT when it is set, alone. Else BOTH
  ~/.sessionkit/phone/state (the new root) and ~/.sessionphone (the old one), the new
  first. For one release the old is a symlink to the new, so the roots are deduped by
  realpath and a socket in it is counted once.
- $DC_ROOT, when it is set: the root a bot run with it keeps (sessionphone dc_env).
- every agent's own bot, ~/.<agent>/delta-chat: the glob stays, phone's move moves none.
- ~/.sessionkit/phone/run: sessionphone publishes every bot's sockets there as
  run/<bot>[.<profile>]/<name>, each a symlink to the socket the bot bound, so a bot at
  any root is reached.
- the fallback guess until every bot runs a phone that publishes: every agent's
  ~/.<agent>/phone (sessionphone's own ~/.sessionkit/phone is not one).

Each root holds <name> itself and in a profile dir, [/<profile>]/<name>. A socket reached
by two roots (a run/ symlink and its target) is counted once, named by the earlier root.
"""
import glob
import os

NEW_ROOT = os.path.join(".sessionkit", "phone", "state")   # under ~
OLD_ROOT = ".sessionphone"                                 # under ~
RUN_ROOT = os.path.join(".sessionkit", "phone", "run")     # under ~: phone publishes here
PHONE_HOME = os.path.join(".sessionkit", "phone")          # under ~: not an agent's phone


def _dedupe(paths):
    """PATHS in order, each realpath once: the first spelling of a place wins."""
    seen, out = set(), []
    for p in paths:
        real = os.path.realpath(p)
        if real not in seen:
            seen.add(real)
            out.append(p)
    return out


def phone_roots():
    """sessionphone's root(s): [$SESSIONPHONE_ROOT] when it is set, else the new root
    then the old, deduped by realpath."""
    explicit = os.environ.get("SESSIONPHONE_ROOT")
    if explicit:
        return [os.path.expanduser(explicit)]
    home = os.path.expanduser("~")
    return _dedupe([os.path.join(home, NEW_ROOT), os.path.join(home, OLD_ROOT)])


def bot_roots():
    """Every dir a Delta bot keeps its call sockets in: sessionphone's root(s), then
    $DC_ROOT when it is set, then each agent's own ~/.<agent>/delta-chat, then
    ~/.sessionkit/phone/run (phone's published symlinks), then the fallback guess
    ~/.<agent>/phone (not ~/.sessionkit/phone); no place twice."""
    home = os.path.expanduser("~")
    roots = phone_roots()
    dc = os.environ.get("DC_ROOT")
    if dc:
        roots.append(os.path.expanduser(dc))
    roots += sorted(glob.glob(os.path.join(glob.escape(home), ".*", "delta-chat")))
    roots.append(os.path.join(home, RUN_ROOT))
    phone_home = os.path.realpath(os.path.join(home, PHONE_HOME))
    roots += sorted(p for p in glob.glob(os.path.join(glob.escape(home), ".*", "phone"))
                    if os.path.realpath(p) != phone_home)
    return _dedupe(roots)


def bot_socks(name):
    """NAME in every bot root and every profile dir under one, sorted; a socket reached
    by two spellings (the old root a symlink to the new) is counted once."""
    found = []
    for root in bot_roots():
        found += sorted(glob.glob(os.path.join(glob.escape(root), name)))
        found += sorted(glob.glob(os.path.join(glob.escape(root), "*", name)))
    return sorted(_dedupe(found))


def web_leg(path):
    """True for sessionphone web's leg, <phone root>/web/call-ears.sock, at any phone
    root (new, old or explicit): it names no Delta bot."""
    here = os.path.realpath(os.path.dirname(path))
    return any(here == os.path.realpath(os.path.join(r, "web")) for r in phone_roots())
