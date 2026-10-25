#!/usr/bin/env python3
"""timeline_bench.py - time `bin/mouth-timeline --future --json` as a cold process.

A notifier runs that command after every tool batch, so the budget is p95 <= 100 ms
wall from a cold process, interpreter start included. This times the WRAPPER, not
the function: each run is a fresh `bin/mouth-timeline` (bash, then the checkout's own
.venv/bin/python, the interpreter the wrapper pins for the package's 3.10 floor). With no
.venv the wrapper runs the installed uv tool's python instead (sessionkit's, else
sessionvoice's), on the same -S and the same src/; this bench times the checkout path, so
the python it reports is .venv's.

Everything it runs against is scratch, built fresh in a temp dir:

- a scratch SESSIONVOICE_HOME holding a COPY of a real heard day (the source is
  only read, never opened for writing), so the backwards scan for the current
  call costs what it costs live;
- a scratch HOME, so the mouth dir is the "live-shaped" default
  (<base>/mouth, queue maildir ~/.mail/agents/mouth) and both are scratch;
- 0 queued lines, then 20 (queued through `mouth.say(spawn=False)`, so no player
  starts; a mix of agents, holds and priorities, like a busy ride-along).

    python3 bench/timeline_bench.py                 # the newest big real heard day
    python3 bench/timeline_bench.py --heard FILE --runs 50
    python3 bench/timeline_bench.py --json          # one JSON line with the numbers

Prints p50/p95/min/max in ms for each queue depth; exit 1 if a p95 is over budget.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "bin" / "mouth-timeline"
# The interpreter the wrapper runs: MOUTH_* is stripped from its env below, so it is always
# the pinned default, never PATH's python3 (bin/mouth-timeline: the package's 3.10 floor).
WRAPPER_PY = ROOT / ".venv" / "bin" / "python"
CMD = ["--future", "--json"]


def real_heard_day() -> Path:
    """The biggest of the newest three real heard logs: a busy day, read only."""
    from ears.home import sessionvoice_home

    base = sessionvoice_home()
    files = sorted((base / "logs" / "conversations").glob("heard_????-??-??.jsonl"))[-3:]
    if not files:
        sys.exit(f"timeline_bench: no heard logs under {base}; pass --heard FILE")
    return max(files, key=lambda p: p.stat().st_size)


def pct(xs: list, p: float) -> float:
    """Nearest-rank percentile: the smallest value with at least p% at or below it."""
    s = sorted(xs)
    k = max(0, min(len(s) - 1, -(-len(s) * p // 100) - 1))
    return s[int(k)]


def queue_lines(n: int, env: dict) -> None:
    """Queue n lines into the scratch mouth, in-process, with no player."""
    saved = dict(os.environ)
    os.environ.clear()
    os.environ.update(env)
    try:
        sys.path.insert(0, str(ROOT / "src"))
        import mouth

        for i in range(n):
            os.environ["VOICEMODE_AGENT"] = ("alice", "bob", "carol")[i % 3]
            mouth.say(f"queued line {i}: a sentence of ordinary length for the ride-along",
                      d=Path(env["SESSIONVOICE_HOME"]) / "mouth", spawn=False, device="airpods",
                      hold="turn-end" if i % 4 == 0 else None,
                      priority="next" if i % 7 == 3 else None,
                      expires_s=120.0 if i % 5 == 1 else None)
    finally:
        os.environ.clear()
        os.environ.update(saved)


def time_runs(runs: int, warm: int, env: dict, want_rows: int) -> list:
    out = []
    for i in range(warm + runs):
        t = time.perf_counter()
        p = subprocess.run([str(WRAPPER), *CMD], env=env, capture_output=True)
        ms = (time.perf_counter() - t) * 1000.0
        if p.returncode != 0:
            sys.exit(f"timeline_bench: exit {p.returncode}: {p.stderr.decode()[-400:]}")
        rows = json.loads(p.stdout)["rows"]
        if len(rows) != want_rows:
            sys.exit(f"timeline_bench: {len(rows)} rows, wanted {want_rows}")
        if i >= warm:
            out.append(ms)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--heard", type=Path, help="the real heard day to copy (default: the "
                    "biggest of the newest three under sessionvoice's home, ears.home)")
    ap.add_argument("--runs", type=int, default=30, help="timed runs per queue depth (default 30)")
    ap.add_argument("--warm", type=int, default=2, help="untimed runs first (default 2)")
    ap.add_argument("--queued", default="0,20", help="queue depths, comma separated (default 0,20)")
    ap.add_argument("--budget-ms", type=float, default=100.0, help="the p95 budget (default 100)")
    ap.add_argument("--json", action="store_true", help="one JSON line, not a table")
    a = ap.parse_args(argv)

    src = a.heard or real_heard_day()
    depths = sorted(int(x) for x in a.queued.split(","))
    scratch = Path(tempfile.mkdtemp(prefix="timeline-bench-"))
    try:
        base, home = scratch / "base", scratch / "home"
        conv = base / "logs" / "conversations"
        conv.mkdir(parents=True)
        home.mkdir()
        shutil.copyfile(src, conv / src.name)  # opens src for reading only
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("MOUTH_", "VOICEMODE_MOUTH_"))
               and k not in ("PYTHONPATH", "VOICEMODE_BASE_DIR")}
        env.update(SESSIONVOICE_HOME=str(base), HOME=str(home))

        res = {"cmd": " ".join(["bin/mouth-timeline", *CMD]), "heard": src.name,
               "heard_mb": round(src.stat().st_size / 1e6, 1), "runs": a.runs,
               "python": str(WRAPPER_PY),
               "budget_ms": a.budget_ms, "depths": {}}
        have = 0
        for n in depths:
            queue_lines(n - have, env)
            have = n
            ms = time_runs(a.runs, a.warm, env, n)
            res["depths"][str(n)] = {"p50": round(pct(ms, 50), 1), "p95": round(pct(ms, 95), 1),
                                     "min": round(min(ms), 1), "max": round(max(ms), 1)}
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    ok = all(v["p95"] <= a.budget_ms for v in res["depths"].values())
    res["ok"] = ok
    if a.json:
        print(json.dumps(res))
    else:
        print(f"{res['cmd']}  cold process  heard copy {res['heard']} ({res['heard_mb']} MB)  "
              f"{a.runs} runs  python {res['python']}")
        print(f"{'queued':>6}  {'p50':>7}  {'p95':>7}  {'min':>7}  {'max':>7}   (ms)")
        for n, v in res["depths"].items():
            print(f"{n:>6}  {v['p50']:7.1f}  {v['p95']:7.1f}  {v['min']:7.1f}  {v['max']:7.1f}")
        print(f"budget p95 <= {a.budget_ms:g} ms: {'PASS' if ok else 'OVER'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
