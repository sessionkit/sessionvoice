#!/usr/bin/env python3
"""smart-turn-shadow: Smart Turn v3 in SHADOW mode beside the ears. It logs, never acts.

Detect mode, not act mode: the logs show when this turn detector fired
against when the ears' own did, so the two can be compared.
Mode names:
off | shadow | live. This process is `shadow` by construction: nothing reads
its verdicts to end a turn.

What it does: opens the same mic as the ears (a second reader, the ears are
untouched), runs Silero VAD with its debounced edges (ears.vad.VadEdges:
an offset after 0.2 s of quiet), and at every offset asks Smart Turn "does
this sound finished?" of the last 8 s. One JSON line per offset:

  voice_end   wall clock of the user's last voiced frame
  p           Smart Turn's P(turn complete); complete = p > 0.5
  would_close when the `shorten` rule (from the eval) would end the turn:
              voice_end + 1.0 s if complete, else + 2.0 s
  infer_ms    the model call

Compare with the ears' TURN line, which lands after 2.0 s of silence
(EARS_LISTEN_SILENCE_S default) plus the redecode. ears-mouth-tail shows
both as SMART and TURN.

Reuses this package's ears (src/ears: SileroVAD, VadEdges, SmartTurnModel,
the turn layer of 0c1049b). Run with the package's venv:
  .venv/bin/python shadow/shadow.py [--device airpods]
Stops on --stop-file or a kill. Log: smart_<date>.jsonl in --log-dir, default
$EARS_SHADOW_LOG_DIR, else log/ beside this file (score and ears-mouth-tail read the same).
"""
import argparse, collections, datetime, json, os, queue, sys, time
from pathlib import Path

import numpy as np
import sounddevice as sd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))  # this package's src/ears
from ears.smartturn import SmartTurnModel  # noqa: E402
from ears.vad import SileroVAD, VadEdges  # noqa: E402
from ears.home import sessionvoice_home  # noqa: E402

HERE = Path(__file__).resolve().parent
SR, BLOCK, WINDOW_S = 16000, 512, 8.0
WAIT_COMPLETE, WAIT_INCOMPLETE, THRESHOLD = 1.0, 2.0, 0.5

ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
ap.add_argument("--device", default="follow",
                help="input device, case-insensitive substring; 'follow' (default) = whatever mic the ears are on, "
                     "re-checked every 3 s (a pinned device went deaf when the AirPods came out)")
ap.add_argument("--log-dir", default=os.environ.get("EARS_SHADOW_LOG_DIR") or str(HERE / "log"))
ap.add_argument("--stop-file", default=str(HERE / "STOP-shadow"))
a = ap.parse_args()


def find_input(name, fatal=True):
    """(index, info) of the first input whose name contains NAME. PortAudio caches its
    device list at start, so a miss re-initialises it once: AirPods put back in
    were invisible to a list taken while they were out, and the shadow exited."""
    for attempt in (0, 1):
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0 and name.lower() in d["name"].lower():
                return i, d
        if attempt == 0:
            sd._terminate()
            sd._initialize()
    msg = (f"smart-turn-shadow: no input device matching {name!r}: "
           f"{[d['name'] for d in sd.query_devices() if d['max_input_channels'] > 0]}")
    if fatal:
        sys.exit(msg)
    print(msg + "; keeping the old mic, retrying in 3 s", flush=True)
    return None


def log_path():
    Path(a.log_dir).mkdir(parents=True, exist_ok=True)
    return Path(a.log_dir) / f"smart_{datetime.date.today():%Y-%m-%d}.jsonl"


def stamp(t):
    return datetime.datetime.fromtimestamp(t).astimezone().isoformat(timespec="milliseconds")


def write(rec):
    with open(log_path(), "a") as f:
        f.write(json.dumps(rec) + "\n")


HEARD = sessionvoice_home() / "logs/conversations"


def ears_mic(default="airpods"):
    """The device on the ears' newest mic line (partial/turn/event), else default."""
    p = HEARD / f"heard_{datetime.date.today():%Y-%m-%d}.jsonl"
    try:
        with open(p, "rb") as f:
            f.seek(max(0, os.path.getsize(p) - 20000))
            for l in reversed(f.read().decode(errors="replace").splitlines()[1:]):
                try:
                    r = json.loads(l)
                except ValueError:
                    continue
                if r.get("source") == "mic" and r.get("device"):
                    return r["device"]
    except OSError:
        pass
    return default


want = ears_mic() if a.device == "follow" else a.device
dev_i, dev = find_input(want)
rate = int(dev["default_samplerate"])
print(f"smart-turn-shadow: loading Smart Turn ...", flush=True)
model = SmartTurnModel()
vad = SileroVAD(SR)
edges = VadEdges(vad)
ring = collections.deque(maxlen=int(WINDOW_S * SR))
q = queue.Queue()


def cb(indata, frames, t, status):
    q.put(indata[:, 0].copy())


def open_stream(i, r):
    s_ = sd.InputStream(device=i, channels=1, samplerate=r, blocksize=max(1, int(round(BLOCK * r / SR))),
                        dtype="float32", callback=cb)
    s_.start()
    return s_


stream = open_stream(dev_i, rate)
next_check = time.time() + 3
write({"ts": stamp(time.time()), "kind": "start", "mode": "shadow", "device": dev["name"], "rate": rate,
       "model": getattr(model, "model_id", "?"), "backend": model.backend})
print(f"smart-turn-shadow: mode shadow, mic {dev['name']!r} at {rate} Hz, model {model.backend}, "
      f"log {log_path()}, stop-file {a.stop_file}", flush=True)

pending = np.zeros(0, np.float32)
while True:
    if os.path.exists(a.stop_file):
        write({"ts": stamp(time.time()), "kind": "stop", "mode": "shadow", "reason": "stop-file"})
        print("smart-turn-shadow: stop file seen, exiting", flush=True)
        break
    if a.device == "follow" and time.time() >= next_check:
        next_check = time.time() + 3
        now_want = ears_mic(want)
        if now_want.lower() != want.lower():
            found = find_input(now_want, fatal=False)  # find the new mic BEFORE letting go of the old one
            if found is None:
                continue
            try:
                stream.close()
            except Exception:
                pass
            want = now_want
            dev_i, dev = found
            rate = int(dev["default_samplerate"])
            stream = open_stream(dev_i, rate)
            vad, pending = SileroVAD(SR), np.zeros(0, np.float32)
            edges = VadEdges(vad)
            ring.clear()
            write({"ts": stamp(time.time()), "kind": "device", "mode": "shadow", "device": dev["name"], "rate": rate})
            print(f"smart-turn-shadow: followed the ears to {dev['name']!r} at {rate} Hz", flush=True)
    try:
        x = q.get(timeout=0.5)
    except queue.Empty:
        continue
    now = time.time()
    if rate != SR:  # linear resample to 16 kHz; fine for VAD and Smart Turn
        n = int(round(len(x) * SR / rate))
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)
    pending = np.concatenate([pending, x])
    while len(pending) >= BLOCK:
        w, pending = pending[:BLOCK], pending[BLOCK:]
        ring.extend(w)
        pcm = (np.clip(w, -1, 1) * 32767).astype("<i2").tobytes()
        for ev in edges.feed(pcm):
            if ev["speech"]:  # an onset: logged so a DONE can be scored (did the user resume before it would close?)
                write({"ts": stamp(time.time()), "kind": "onset", "mode": "shadow",
                       "voice_start": stamp(now - (vad.t - ev["at"]))})
                continue
            voice_end = now - (vad.t - (vad.last_voice or vad.t))
            t0 = time.perf_counter()
            p = round(float(model.probability(np.asarray(ring, np.float32))), 3)
            ms = round((time.perf_counter() - t0) * 1000, 1)
            done = p > THRESHOLD
            close = voice_end + (WAIT_COMPLETE if done else WAIT_INCOMPLETE)
            write({"ts": stamp(time.time()), "kind": "offset", "mode": "shadow", "p": p, "complete": done,
                   "voice_end": stamp(voice_end), "would_close": stamp(close), "infer_ms": ms,
                   "device": dev["name"]})
