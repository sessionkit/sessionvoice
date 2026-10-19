"""clone_live_ab.py --json OUT.jsonl --voice NAME --text short|long --lead off|on [--burners N] [--check] - ONE live clone line.

The live A/B. It says ONE real clone line (a clone voice
on the resident mlx-audio server, :8890) through the worktree's own player,
from a private mouth box, to a named output device. ``--lead on`` says it
with ``lead_s=2.5`` (``mouth say --lead 2.5``); ``off`` with no lead, the
mouth's default. It can also put the
machine under short, capped CPU load while it does. It records the line's
``saying``/``said`` fields (``gen_s``, ``held_s``, ``underruns``,
``underrun_s``, ``played_s``, ``dur_s``), the chunk arrival times (from which
it computes ``rtf`` and the lead the line NEEDED), the 1-minute load before
and after, and whether a burner outlived the run.

Its safety terms are enforced here, not left to the caller:

- the clone model must already be resident (``GET /v1/models``); nothing is
  loaded. If it is missing, the run refuses.
- at most ``--max-burners`` (9: half of an 18-core host), each one ``timeout 20
  yes``, so no burner can outlive 20 s even if this process dies;
- no run starts while the 1-minute load is above 16;
- at most 20 lines, within 20 minutes of the FIRST request, counted from
  ``OUT.jsonl``;
- after each run, ``pgrep -x yes`` and ``pgrep -x stress-ng`` must come back
  empty. A ``pgrep -fl 'yes|stress-ng'`` would also match ssh's
  ``-o BatchMode=yes`` on a host, so it is never empty;
- it looks read-only at the live heard log for another seat's ``saying``
  during the run (``other_speech``), so a run that overlapped one can be paused
  and repeated.

The clone voice resolves through the mouth's own resolver (``src/mouth/resolver.py``),
so no ``voice_mode`` is needed, e.g.:

    uv run --locked \\
        python bench/clone_live_ab.py --json live.jsonl --voice NAME --text short --lead on --burners 6
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

TEXTS = {
    # ~3 s and ~8 s of speech, the offline scenarios' two lengths
    "short": "The kettle is on, and the tea will be ready soon.",
    "long": ("This is a longer line, the kind a seat says when it reports what it found: "
             "three tests passed, one failed, and the fix is on its way to review now."),
}
MAX_LINES, WINDOW_S, MAX_LOAD = 20, 20 * 60, 16.0
BURN_S = 20
LEAD_ON_S = 2.5   # --lead on: a 2-3 s sizing, as a seat that wants no chop passes it


def resident(base_url: str) -> list[str]:
    with urllib.request.urlopen(f"{base_url}/models", timeout=5) as r:
        return [m.get("id") for m in json.load(r).get("data", [])]


def pgrep(name: str) -> list[str]:
    r = subprocess.run(["pgrep", "-x", name], capture_output=True, text=True)
    return r.stdout.split()


def prior(out: Path) -> list[dict]:
    if not out.exists():
        return []
    return [json.loads(x) for x in out.read_text().splitlines() if x.strip()]


def live_sayings(t0: float, t1: float) -> list[dict]:
    """Another seat's ``saying`` in the live heard log between t0 and t1 (read only)."""
    from ears import heard

    path = heard.log_path(directory=heard.log_dir())
    if not path.exists():
        return []
    got = []
    with path.open() as f:
        f.seek(max(0, path.stat().st_size - 512 * 1024))
        for x in f:
            try:
                r = json.loads(x)
            except ValueError:
                continue
            if r.get("kind") != "saying":
                continue
            ts = r.get("ts")
            try:
                from datetime import datetime
                t = datetime.fromisoformat(ts).timestamp()
            except (TypeError, ValueError):
                continue
            if t0 <= t <= t1:
                got.append({"ts": ts, "agent": r.get("agent"), "voice": r.get("voice"),
                            "backend": r.get("backend"), "device": r.get("device")})
    return got


class Recorder:
    """Wraps the real clone backend and times each chunk as it lands."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.name, self.sample_rate = inner.name, inner.sample_rate
        self.arrivals: list[tuple[float, float]] = []   # (wall s from request, audio s at chunk start)

    def stream(self, text, voice, speed):
        t0, frames = time.monotonic(), 0
        for block in self.inner.stream(text, voice, speed):
            self.arrivals.append((time.monotonic() - t0, frames / self.sample_rate))
            frames += len(block)
            yield block
        self.frames = frames

    def stats(self) -> dict:
        if not self.arrivals:
            return {}
        first = self.arrivals[0][0]
        last_t = self.arrivals[-1][0]
        audio = getattr(self, "frames", 0) / self.sample_rate
        # render time over audio time, after the first chunk (the steady rate)
        rtf = (last_t - first) / self.arrivals[-1][1] if self.arrivals[-1][1] > 0 else None
        need = max(0.0, max(a - at for a, at in self.arrivals) - first)
        return {"chunks": len(self.arrivals), "audio_s": round(audio, 3),
                "first_chunk_s": round(first, 3), "render_s": round(last_t, 3),
                "rtf": round(rtf, 3) if rtf else None, "lead_needed_s": round(need, 3)}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", required=True, type=Path)
    ap.add_argument("--text", choices=sorted(TEXTS), default="short")
    ap.add_argument("--lead", choices=("off", "on"), default="on")
    ap.add_argument("--check", action="store_true",
                    help="check the terms (resident model, device, load, caps) and exit: no clone request")
    ap.add_argument("--burners", type=int, default=0)
    ap.add_argument("--max-burners", type=int, default=9)
    ap.add_argument("--voice", required=True,
                    help="the clone voice to say the line in: clone voices are per host, so there is no default")
    ap.add_argument("--device", default="DELL U2421E")
    ap.add_argument("--label", default="")
    a = ap.parse_args(argv)
    lead = LEAD_ON_S if a.lead == "on" else None

    if a.burners < 0 or a.burners > a.max_burners:
        sys.exit(f"refused: {a.burners} burners; the cap is {a.max_burners}")
    if "blackhole" in a.device.lower():
        sys.exit("refused: never a BlackHole device")
    rows = prior(a.json)
    if len(rows) >= MAX_LINES:
        sys.exit(f"refused: {len(rows)} lines already, the cap is {MAX_LINES}")
    if rows and time.time() - rows[0]["t_request"] > WINDOW_S:
        sys.exit("refused: the 20-minute window is over")
    load0 = os.getloadavg()
    if load0[0] > MAX_LOAD:
        sys.exit(f"refused: 1-minute load {load0[0]:.2f} > {MAX_LOAD}")
    if pgrep("yes") or pgrep("stress-ng"):
        sys.exit("refused: a burner is already running")

    import mouth
    from mouth import backends, output, player

    real, voice = backends.resolve("clone", a.voice)
    models = resident(real.base_url)
    if real.model not in models:
        sys.exit(f"refused: {real.model} is not resident on {real.base_url} ({models}); load it there first")
    names = output.output_names()
    if a.device not in names:
        sys.exit(f"refused: no output {a.device!r} ({names})")
    if a.check:
        print(json.dumps({"ok": True, "model": real.model, "resident": models, "device": a.device,
                          "lead_s": lead, "load": [round(x, 2) for x in load0],
                          "lines_so_far": len(rows)}))
        return 0
    rec = Recorder(real)

    work = Path(tempfile.mkdtemp(prefix="ears285-live-"))
    d, logs = work / "mouth", work / "logs"
    env = {"MOUTH_DIR": str(d), "MOUTH_LOG_DIR": str(logs),
           "MOUTH_SETTINGS": str(work / "mouth.json"), "MOUTH_SESSION_BIN": "",
           "MOUTH_RESUME_S": "0", "VOICEMODE_AGENT": "ears285-ab",
           "VOICEMODE_SESSION_ID": "s-ears285-ab", "MOUTH_BARGE_DEVICES": "",
           "SESSIONVOICE_HOME": str(work / "vm"), "MOUTH_NOTICE": "off"}
    from ears import envnames
    for k in ("MOUTH_MAILBOX", "MOUTH_MAIL_LOG", "MOUTH_DUCK"):
        for n in envnames.group(k):   # and its old VOICEMODE_MOUTH_ name
            os.environ.pop(n, None)
    os.environ.update(env)
    backends.resolve = lambda b, v: (rec, voice)

    burners = []
    t_start = time.time()
    try:
        for _ in range(a.burners):
            burners.append(subprocess.Popen(["timeout", str(BURN_S), "yes"],
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        if burners:
            time.sleep(1.0)   # let the load bite before the request
        t_request = time.time()
        item = mouth.say(TEXTS[a.text], spawn=False, d=d, device=a.device, backend="clone",
                         voice=a.voice, lead_s=lead)
        th = threading.Thread(target=player.serve, kwargs={"d": d, "idle_exit_s": 0.3}, daemon=True)
        with contextlib.redirect_stdout(io.StringIO()):
            th.start()
            th.join(BURN_S + 40)
        t_end = time.time()
        starved = th.is_alive()
    finally:
        for b in burners:
            b.terminate()
        for b in burners:
            try:
                b.wait(5)
            except subprocess.TimeoutExpired:
                b.kill()
    load1 = os.getloadavg()
    left = {"yes": pgrep("yes"), "stress-ng": pgrep("stress-ng")}
    recs = [json.loads(x) for f in sorted(logs.glob("heard_*.jsonl")) for x in f.read_text().splitlines()]
    saying = next((r for r in recs if r.get("kind") == "saying" and r.get("utt") == item["utt"]), {})
    said = next((r for r in recs if r.get("kind") == "said" and r.get("utt") == item["utt"]), {})
    gen, held = saying.get("gen_s"), saying.get("held_s")
    row = {
        "n": len(rows) + 1, "label": a.label, "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "t_request": t_request, "text": a.text, "lead": a.lead, "burners": a.burners,
        "voice": a.voice, "model": real.model, "device": said.get("device") or a.device,
        "load_before": [round(x, 2) for x in load0], "load_after": [round(x, 2) for x in load1],
        "burn_s": BURN_S if burners else 0, "run_wall_s": round(t_end - t_start, 2),
        "reason": said.get("reason"), "gen_s": gen, "lead_s": lead, "held_s": held,
        "ttfa_s": round((gen or 0) + (held or 0), 3) if gen is not None else None,
        "played_s": said.get("played_s"), "dur_s": said.get("dur_s"),
        "underruns": said.get("underruns"), "underrun_s": said.get("underrun_s"),
        **rec.stats(), "starved": starved, "pgrep_after": left,
        "other_speech": live_sayings(t_start, t_end),
    }
    a.json.parent.mkdir(parents=True, exist_ok=True)
    with a.json.open("a") as f:
        f.write(json.dumps(row) + "\n")
    print(json.dumps(row))
    return 0 if not any(left.values()) else 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
