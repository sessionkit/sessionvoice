#!/usr/bin/env python3
"""partial-wake: wake a seat MID-SPEECH, from the heard log's partials.

An experiment: heard_wait can't interrupt the user, because
it wakes only after 8 s of quiet, and the
hook shows the user's words only while the seat is running tools.

Follows today's heard log from its current end. Exits (so a Bash
run_in_background wakes the agent) on the FIRST of:
  - a partial whose text matches --match REGEX (case-insensitive), or
  - --every SECONDS of this session's partials since arming (a digest).
Prints one JSON line: reason, the matched partial, and every partial seen
since arming, in order. Re-arm it every time, like heard_wait.

Not in the spike. A side-car that reads the log, nothing more.
"""
import argparse, json, os, re, sys, time
from datetime import date
from pathlib import Path
HERE = Path(__file__).resolve().parent


def voice_home():
    """ears/home.py, the ONE place voice names its state root and its log folder.
    keeper/ imports nothing from the ears package, so it loads home.py
    (standard library only) by its path, once: the checkout's src/ears/home.py (keeper/ ships in the
    checkout and the plugin only).
    Not there: exit 2, naming where it looked; there is no copy of the rule to fall back
    on."""
    mod = getattr(voice_home, "mod", None)
    if mod is None:
        import importlib.util, sys
        tried = [HERE.parent / "src/ears/home.py"]
        f = next((f for f in tried if f.is_file()), None)
        if f is None:
            print(f"partial-wake: no ears/home.py at {' nor '.join(map(str, tried))}: it names "
                  "voice's state root. Run it from a sessionvoice checkout, the plugin or the "
                  "package", file=sys.stderr)
            sys.exit(2)
        spec = importlib.util.spec_from_file_location("_voice_home", f)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        voice_home.mod = mod
    return mod


def voice_state_dir() -> Path:
    """voice's state root, as the PACKAGE answers it (ears.home.asked_state_dir, design.md
    D4.1): asked once per process, and exit 2, naming both answers, when the package cannot
    be asked or disagrees with this checkout's home.py."""
    return voice_home().asked_state_dir("partial-wake")


ap = argparse.ArgumentParser()
ap.add_argument("--match", help="regex that wakes on a partial (case-insensitive)")
ap.add_argument("--every", type=float, default=0, help="wake after this many seconds of partials (0 = never)")
ap.add_argument("--session", default=os.environ.get("CLAUDE_CODE_SESSION_ID"))
ap.add_argument("--log", default=str(Path(voice_state_dir()) / "logs/conversations" / f"heard_{date.today():%Y-%m-%d}.jsonl"))
a = ap.parse_args()
if not a.match and not a.every:
    sys.exit("partial-wake: give --match REGEX, --every SECONDS, or both")
rx = re.compile(a.match, re.I) if a.match else None

f = open(a.log)
f.seek(0, os.SEEK_END)
print(f"partial-wake: session {a.session}, log {a.log}, match {a.match!r}, every {a.every}", file=sys.stderr, flush=True)
seen, first = [], None
while True:
    line = f.readline()
    if not line:
        time.sleep(0.2)
        continue
    try:
        e = json.loads(line)
    except ValueError:
        continue
    # Delta call lines (the Delta chat) carry no session; count them as mine.
    if e.get("kind") != "partial" or e.get("session") not in (a.session, None):
        continue
    text = e.get("text", "")
    seen.append(f"[{e['ts'][11:19]}] {text}")
    first = first or time.monotonic()
    hit = rx.search(text) if rx else None
    if hit or (a.every and time.monotonic() - first >= a.every):
        print(json.dumps({"reason": "match" if hit else "every",
                          "matched": hit.group(0) if hit else None,
                          "seq": e.get("seq"), "partials": seen}), flush=True)
        sys.exit(0)
