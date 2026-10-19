"""clone_underrun.py [--json OUT.jsonl] [--lead S] [SCENARIO ...] - a clone line slower than real time, OFFLINE.

A repro. The mouth plays a clone (Qwen3-TTS on mlx-audio, ``stream``
True, ``streaming_interval`` 0.3, src/mouth/backends.py) WHILE it streams, so
the player is never more than one chunk ahead; when the render drops below
real time, playback runs dry. This drives the REAL player (``player.serve``,
``play_one``, the real outputs) with :class:`CloneLike`, a fake backend that
yields audio the way the clone server does: a first chunk after
``ttfa_s``, then one ``interval_s`` chunk every ``interval_s * rtf`` seconds,
with optional stalls. No server, no sound card, no model.

Three paths, the three a clone line can take today:

- ``null``: :class:`output.NullOut` (real-time pacing, one-block buffer).
- ``device``: :class:`output.DeviceOut` on a fake PortAudio that pulls in real
  time (the callback, the 4-block queue, the underrun counter as on a Mac).
- ``bed``: the line as a :class:`output.MixLane` over a ducking bed (a file
  playing on the same device), the mixer on a recording null output.

Each run prints the line's own ``saying``/``said`` fields (``gen_s``,
``underruns``, ``underrun_s``, ``played_s``, ``dur_s``), ``held_s`` (the
player's lead hold, ``saying.held_s``; empty with ``--lead 0``, the fix OFF)
and ``lead_s``: the
lead buffer this line would have needed, from the chunk arrival times the
backend measured (max over chunks of arrival - its play time, minus the first
arrival). A lead buffer of ``lead_s`` before the first frame would have played
the line with no dry-out.

Each line is said with ``--lead S`` (default 2.5, as ``mouth say --lead 2.5``);
the mouth's own default is 0, the fix OFF.

    uv run --locked python bench/clone_underrun.py            # every scenario, every path
    uv run --locked python bench/clone_underrun.py loaded-1.25 --json out.jsonl
    uv run --locked python bench/clone_underrun.py --lead 0   # the fix OFF
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator, Optional

import numpy as np

SAMPLE_RATE = 24000


class CloneLike:
    """The clone server's stream, faked: ``dur_s`` of audio in ``interval_s``
    chunks; chunk k arrives at ``ttfa_s + k * interval_s * rtf`` (plus every
    stall before it). ``rtf`` is render time over audio time: below 1 is faster
    than real time (an idle laptop measured 0.93-0.96), above 1 is slower.
    ``stalls`` is ``[(at_audio_s, stall_s), ...]``: the render stops for
    ``stall_s`` before the chunk that starts at ``at_audio_s``.

    Scheduled on absolute times, so sleep jitter never accumulates.
    ``arrivals`` holds each chunk's (wall time from the request, audio start s)."""

    name = "clone"
    sample_rate = SAMPLE_RATE

    def __init__(self, dur_s: float = 3.0, rtf: float = 0.95, interval_s: float = 0.3,
                 ttfa_s: Optional[float] = None, stalls=()) -> None:
        self.dur_s, self.rtf, self.interval_s = dur_s, rtf, interval_s
        self.ttfa_s = interval_s * rtf if ttfa_s is None else ttfa_s
        self.stalls = sorted(stalls)
        self.arrivals: list[tuple[float, float]] = []

    def stream(self, text: str, voice: str, speed: Optional[float]) -> Iterator[np.ndarray]:
        total = int(round(self.dur_s * self.sample_rate))
        step = int(round(self.interval_s * self.sample_rate))
        t0 = time.monotonic()
        k = 0
        for start in range(0, total, step):
            at = start / self.sample_rate
            due = self.ttfa_s + k * self.interval_s * self.rtf
            due += sum(s for a, s in self.stalls if a <= at + 1e-9)
            time.sleep(max(0.0, t0 + due - time.monotonic()))
            n = min(step, total - start)
            self.arrivals.append((time.monotonic() - t0, at))
            # a quiet tone, not zeros: the level stage and the limiter see audio
            t = (np.arange(n) + start) / self.sample_rate
            yield (0.1 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
            k += 1

    def lead_s(self) -> Optional[float]:
        """The lead buffer this line needed: how long after its first chunk
        playback must start so no chunk arrives after its play time."""
        if not self.arrivals:
            return None
        first = self.arrivals[0][0]
        return round(max(0.0, max(a - at for a, at in self.arrivals) - first), 3)


#: name -> CloneLike kwargs. Sized against measured evidence (README
#: Log): clone RTF 0.93-0.96 idle, so any stall chops; under load (load
#: 58) the render drops below real time.
SCENARIOS = {
    "healthy-0.5":   dict(dur_s=3.0, rtf=0.5),                  # control: render 2x real time
    "idle-0.95":     dict(dur_s=3.0, rtf=0.95),                 # an idle laptop, no stall
    "stall-1s":      dict(dur_s=3.0, rtf=0.95, stalls=[(1.5, 1.0)]),   # idle + one 1 s stall
    "loaded-1.25":   dict(dur_s=3.0, rtf=1.25),                 # render at 0.8x real time
    "loaded-1.25-8s": dict(dur_s=8.0, rtf=1.25),                # a long line under the same load
}
PATHS = ("null", "device", "bed")


class _PullingSd:
    """A fake PortAudio whose stream pulls 50 ms blocks in real time (a Mac's
    callback, without a sound card)."""

    default = SimpleNamespace(device=(0, 1))

    def _terminate(self):
        pass

    def _initialize(self):
        pass

    def query_devices(self, i=None):
        devs = [{"name": "Fake Speakers", "max_output_channels": 2}]
        return devs if i is None else devs[i]

    def OutputStream(self, *, device, samplerate, channels, dtype, callback):
        class S:
            latency = 0.01

            def __init__(s):
                s.run = True

            def start(s):
                def loop():
                    buf = np.zeros((1200, channels), dtype="float32")
                    nxt = time.monotonic()
                    while s.run:
                        callback(buf, 1200, None, None)
                        nxt += 1200 / samplerate
                        time.sleep(max(0.0, nxt - time.monotonic()))
                threading.Thread(target=loop, daemon=True).start()

            def abort(s):
                s.run = False

            def close(s):
                s.run = False

        return S()


def _tone(path: Path, seconds: float) -> Path:
    x = (0.2 * np.sin(2 * np.pi * 440 * np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE)
         * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(x.tobytes())
    return path


def run_line(scenario: dict, path: str = "null", workdir: Optional[Path] = None,
             lead_s: Optional[float] = None) -> dict:
    """One clone line through the real player on ``path``; its saying/said fields.
    ``lead_s`` is the line's own lead (``mouth say --lead``); None, the mouth's default.

    Sets the mouth's env for a private box under ``workdir`` (a temp dir if
    None) and patches ``backends.resolve`` and the output layer for the run,
    restoring both after."""
    import mouth
    from mouth import backends, output, player

    work = Path(workdir or tempfile.mkdtemp(prefix="ears285-"))
    d, logs, mail = work / "mouth", work / "logs", work / "mail"
    for sub in ("tmp", "new", "cur"):
        (mail / sub).mkdir(parents=True, exist_ok=True)
    env = {
        "MOUTH_DIR": str(d), "MOUTH_LOG_DIR": str(logs),
        "MOUTH_MAILBOX": str(mail), "MOUTH_SETTINGS": str(work / "mouth.json"),
        "MOUTH_SESSION_BIN": "", "MOUTH_RESUME_S": "0",
        "VOICEMODE_AGENT": "ears285", "VOICEMODE_SESSION_ID": "s-ears285",
        "SESSIONVOICE_HOME": str(work / "vm"), "MOUTH_DEVICE": "null",
        "MOUTH_BARGE_DEVICES": "",
    }
    saved_env = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    fake = CloneLike(**scenario)
    saved = (backends.resolve, output._sd, output._plain_output)
    real_plain = output._plain_output

    def plain(name, sample_rate, pan=None, meta=None):
        if str(name).startswith("dev-"):   # the bed's device: a named null output
            o = output.NullOut(sample_rate, pan)
            o.name = name
            return o
        return real_plain(name, sample_rate, pan, meta)

    backends.resolve = lambda b, v: (fake, v)
    output._sd = lambda: _PullingSd()
    output._plain_output = plain
    try:
        device = {"null": "null", "device": "Fake Speakers", "bed": "dev-a"}[path]
        t_req = time.monotonic()
        if path == "bed":   # a ducking bed on the device, long enough to lie under the line
            song = _tone(work / "song.wav", scenario.get("dur_s", 3.0) * scenario.get("rtf", 1.0) + 4.0)
            mouth.play(str(song), spawn=False, d=d, device=device)
        item = mouth.say("a clone line", spawn=False, d=d, device=device, backend="clone",
                         lead_s=lead_s)
        th = threading.Thread(target=player.serve, kwargs={"d": d, "idle_exit_s": 0.3}, daemon=True)
        with contextlib.redirect_stdout(io.StringIO()):   # serve prints each said; the table is ours
            th.start()
            th.join(scenario.get("dur_s", 3.0) * max(1.0, scenario.get("rtf", 1.0)) + 30)
        wall = time.monotonic() - t_req
        rows = [json.loads(x) for f in sorted(logs.glob("heard_*.jsonl")) for x in f.read_text().splitlines()]
        saying = next((r for r in rows if r.get("kind") == "saying" and r.get("utt") == item["utt"]), {})
        said = next((r for r in rows if r.get("kind") == "said" and r.get("utt") == item["utt"]), {})
        return {
            "path": path, "device": said.get("device"), "reason": said.get("reason"),
            "gen_s": saying.get("gen_s"), "played_s": said.get("played_s"), "dur_s": said.get("dur_s"),
            "underruns": said.get("underruns"), "underrun_s": said.get("underrun_s"),
            "over": said.get("over"), "lead_s": fake.lead_s(), "held_s": saying.get("held_s"),
            "wall_s": round(wall, 3),
            "chunks": len(fake.arrivals),
        }
    finally:
        backends.resolve, output._sd, output._plain_output = saved
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def main(argv: list[str]) -> int:
    out, lead = None, 2.5
    if "--lead" in argv:
        i = argv.index("--lead")
        lead = float(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    if "--json" in argv:
        i = argv.index("--json")
        out = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    names = argv or list(SCENARIOS)
    f = open(out, "w") if out else None
    print(f"{'scenario':16} {'path':6} {'reason':6} {'gen_s':>6} {'dur_s':>6} {'played':>6} "
          f"{'unders':>6} {'under_s':>7} {'lead_s':>6} {'held_s':>6} {'wall_s':>6}")
    for name in names:
        for path in PATHS:
            r = {"scenario": name, **SCENARIOS[name], "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 **run_line(SCENARIOS[name], path, lead_s=lead)}
            print(f"{name:16} {path:6} {str(r['reason']):6} {r['gen_s'] or 0:6.3f} {r['dur_s'] or 0:6.3f} "
                  f"{r['played_s'] or 0:6.3f} {r['underruns'] or 0:6d} {r['underrun_s'] or 0:7.3f} "
                  f"{r['lead_s'] or 0:6.3f} {r['held_s'] or 0:6.3f} {r['wall_s']:6.2f}", flush=True)
            if f:
                f.write(json.dumps(r) + "\n")
                f.flush()
    if f:
        f.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
