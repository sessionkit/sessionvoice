#!/usr/bin/env python3
"""heard-tail: follow today's heard log, one readable line per event (for the operator's screen)."""
import json, subprocess, sys
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
            print(f"heard-tail: no ears/home.py at {' nor '.join(map(str, tried))}: it names "
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
    return voice_home().asked_state_dir("heard-tail")


log = Path(voice_state_dir()) / "logs/conversations" / f"heard_{date.today():%Y-%m-%d}.jsonl"
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
