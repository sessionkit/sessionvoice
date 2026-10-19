#!/usr/bin/env python3
"""heard-tail: follow today's heard log, one readable line per event (for the operator's screen)."""
import json, subprocess, sys
from datetime import date
from pathlib import Path


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


log = Path(sessionvoice_home()) / "logs/conversations" / f"heard_{date.today():%Y-%m-%d}.jsonl"
p = subprocess.Popen(["tail", "-n", "15", "-F", str(log)], stdout=subprocess.PIPE, text=True)
for line in p.stdout:
    try:
        d = json.loads(line)
    except ValueError:
        continue
    kind = d.get("kind", "?")
    text = (d.get("text") or d.get("event") or "").strip()
    src = d.get("device") or d.get("source") or ""
    mark = {"turn": "TURN", "partial": " .. "}.get(kind, kind[:4].upper())
    print(f"{d.get('ts', '')[11:19]}  {mark:4}  {src:14.14}  {text}", flush=True)
