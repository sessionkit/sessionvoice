"""sounds_wake.py SCRIPT [--idle S] [--n N] - idle CPU and enqueue->log latency of
keeper/mouth-sounds.py, over a FAKE maildir in a temp dir. Nothing live is touched:
MOUTH_MAILDIR, _FIRED, _CANNED, _CURSORS, _HEARD_DIR, _SETTINGS, _LOG and _STOP
all sit in the temp dir, MOUTH_SOUNDS_DRY=1, and sounddevice is a stub (never opens audio).

Run it with a python that has numpy:  uv run --locked python bench/sounds_wake.py keeper/mouth-sounds.py
Compare two revisions by pointing SCRIPT at each (git show REV:keeper/mouth-sounds.py > /x/old.py).

Prints, as JSON: idle CPU % of one core (the process's cputime delta from `ps`, over --idle
seconds with the same fake load: --cursors cursor files (default 400, as live), an empty maildir), and latency in ms for
N maildir lines, each as (a) a canned utt (is_canned answers at once:
this is the wake latency) and (b) an ordinary utt (is_canned waits up to 6 x 50 ms for the
auto-opener to record it, which is in the old code too).
"""
import argparse, json, os, statistics, subprocess, sys, tempfile, time
from datetime import datetime
from pathlib import Path

STUB = "class PortAudioError(Exception):\n    pass\n"


def cputime(pid):
    out = subprocess.run(["ps", "-o", "time=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    m, s = out.split(":")
    return int(m) * 60 + float(s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("script")
    ap.add_argument("--idle", type=float, default=30.0)
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--cursors", type=int, default=400, help="cursor files in the fake heard-cursor dir (live: ~400)")
    a = ap.parse_args()
    with tempfile.TemporaryDirectory() as d:
        t = Path(d)
        for p in ("stub", "md/new", "md/cur", "cursors", "heard", "run"):
            (t / p).mkdir(parents=True)
        (t / "stub/sounddevice.py").write_text(STUB)
        for i in range(a.cursors):
            (t / f"cursors/sess{i}").write_text(json.dumps({"file": "h.jsonl", "offset": 0, "seq": 1}))
        (t / "heard/h.jsonl").write_text("")
        (t / "settings.json").write_text(json.dumps({"sounds": {"drop": {"on": True, "notes": [[1568, 5]], "volume": 0.1},
                                                                "canned": {"on": True, "notes": [[784, 5]], "volume": 0.1}}}))
        env = dict(os.environ, PYTHONPATH=str(t / "stub"), MOUTH_SOUNDS_DRY="1",
                   MOUTH_MAILDIR=str(t / "md"), EARS_RUN=str(t / "run"),
                   MOUTH_SOUNDS_FIRED=str(t / "run/mid-turn.fired"), MOUTH_SOUNDS_CANNED=str(t / "run/canned"),
                   MOUTH_SOUNDS_SETTINGS=str(t / "settings.json"), MOUTH_SOUNDS_LOG=str(t / "sounds.jsonl"),
                   MOUTH_SOUNDS_STOP=str(t / "STOP"), MOUTH_SOUNDS_CURSORS=str(t / "cursors"),
                   MOUTH_SOUNDS_HEARD_DIR=str(t / "heard"))
        p = subprocess.Popen([sys.executable, a.script], env=env, stdout=subprocess.PIPE, text=True)
        p.stdout.readline()                      # the banner
        time.sleep(1.0)
        c0, w0 = cputime(p.pid), time.time()
        time.sleep(a.idle)
        c1, w1 = cputime(p.pid), time.time()
        res = {"script": a.script, "idle_s": round(w1 - w0, 1), "idle_cpu_pct": round(100 * (c1 - c0) / (w1 - w0), 2)}
        log = t / "sounds.jsonl"

        def lat(kind, canned):
            (t / "run/canned").write_text("")
            marks = {}
            for i in range(a.n):
                utt = f"{kind}{'c' if canned else 'o'}{i:03d}"
                if canned:
                    with open(t / "run/canned", "a") as f:
                        f.write(utt + "\n")
                time.sleep(0.37)                 # an arbitrary phase against any poll/backstop
                marks[utt] = time.time()
                tmp = t / "md" / f"{utt}.tmp"
                tmp.write_text(f"X-Mouth-Utt: {utt}\n\nhi\n")
                marks[utt] = time.time()
                os.rename(tmp, t / "md/new" / f"m.{utt}.host")
            time.sleep(1.0)
            got = {}
            for line in log.read_text().splitlines():
                r = json.loads(line)
                if r.get("utt") in marks:
                    got[r["utt"]] = (datetime.fromisoformat(r["ts"]).timestamp() - marks[r["utt"]]) * 1000
            ms = sorted(got.values())
            return {"n": len(ms), "of": a.n, "min": round(ms[0]), "median": round(statistics.median(ms)),
                    "p95": round(ms[int(len(ms) * 0.95) - 1]), "max": round(ms[-1])} if ms else {"n": 0, "of": a.n}
        # the old queue folder is no longer watched: maildir lines only
        res["latency_ms"] = {"maildir_canned": lat("m", True), "maildir_ordinary": lat("m", False)}
        (t / "STOP").touch()
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
        res["exit"] = p.returncode
        print(json.dumps(res, indent=1))


main()
