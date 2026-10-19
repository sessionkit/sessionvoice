"""duck_latency.py [--runs N] [--json OUT.jsonl] - the barge duck's latency, OFFLINE.

A BENCH figure, never a live one: no sound card, no ears, no player, no
network. It times the part of the chain the mouth owns, from the ears' ``vad``
onset row to the first output buffer the duck scaled, with the REAL classes:

1. a synthetic ``vad`` onset row (``speech: true``) is appended to a heard log
   in a temp dir by ``ears.heard.event``, as capture writes it
   (``listen/loop.py`` ``_Edges``);
2. :class:`mouth.onset.OnsetTail` finds it (one ``stat`` per 10 ms poll) and
   :class:`mouth.onset.BargeDuck` sets the line's duck (``dip``);
3. :class:`mouth.output.LineDuck` scales the next buffer that a FAKE consumer
   pulls in real time (``applied``).

Three consumers, the two places a duck is applied:

- ``device``: a device callback every ``--frames`` / ``--rate`` seconds (512
  frames at 48 kHz, 10.7 ms, by default), as ``DeviceOut._callback`` applies
  the duck AFTER its 200 ms queue;
- ``call``: a fake writer that applies it once per 50 ms block, paced in real
  time: the design's model of ``CallOut.write``, kept so the design
  figure can be re-run beside the build's;
- ``callout``: the BUILD. A real :class:`mouth.output.CallOut`
  speaks to a fake bot on a Unix socket in a temp dir (the mouth-leg protocol,
  as tests/mouth/test_mouth_call.py's ``FakeBot``; no live call, no port),
  ``play_one``'s 50 ms blocks (``backends.BLOCK_S``) at ``--call-rate`` go
  through ``CallOut.write``, and the duck is the one ``onset.for_line`` builds
  for a ``call:N`` line from ``hold.barge_policy`` (built-in defaults: the
  temp dir holds no settings and no override). Besides the record's
  ``dip_ms``, ``applied_ms`` and ``ahead_ms`` it prints ``bot_ms`` (row
  written to the first scaled PCM frame the bot received, monotonic) and
  ``head_ms`` (``total_ms + ahead_ms``: when that frame reaches the head of
  the bot's queue, the design's target).

Per run it prints ``dip_ms`` (the duck record's own: dip wall time minus the
row's ``ts``), ``applied_ms`` (the record's own: the dip's ask to the first
scaled buffer, monotonic) and ``total_ms`` (row written to first scaled
buffer, monotonic). The summary gives p50/p90/max of each. Each run is one
line from its first frame: the voice ends with an offset row, or the next
line would start ducked.

What a bench CANNOT see, so never quote it as voice-to-ear:

- the ~60 ms of the user's voice before the onset row (2 Silero frames of 30 ms,
  ``_Edges`` MIN_SPEECH), and the capture's own input buffering before that;
- after the scaled buffer: the device's output buffer (Bluetooth, ~0.2 s on
  AirPods), or on a call everything after the write:
  ``CallOut.latency`` (0.2 s paced ahead, which a write-side duck cannot
  reach), the bot's queue, Opus and the network to the user's phone; and on a call
  the uplink before the row (the user's phone to the bot's ear leg);
- a starved host: the bench runs at whatever load the machine has (printed).

Run from the repo root: ``python3 bench/duck_latency.py --runs 200`` (or
``uv run python bench/duck_latency.py``; ``--modes callout`` for the build's
call path alone). It writes only under a temp dir.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import socket
import statistics
import struct
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np  # noqa: E402

from ears import envnames, heard  # noqa: E402
from mouth import backends, hold, onset, output  # noqa: E402


class Consumer:
    """A fake device: pulls ``frames`` at ``rate`` every ``frames / rate`` s, in
    real time (a drifting-free schedule), and lets the duck scale each buffer."""

    def __init__(self, duck: output.LineDuck, frames: int, rate: int) -> None:
        self.duck, self.period = duck, frames / rate
        self.buf = np.zeros((frames, 1), dtype=np.float32)
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name="fake-device")

    def _run(self) -> None:
        nxt = time.monotonic()
        while not self._stop.is_set():
            nxt += self.period
            delay = nxt - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self.buf[:] = 1.0
            self.duck.apply(self.buf)

    def start(self) -> "Consumer":
        self.thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self.thread.join(1.0)


def _wall(ts: str) -> float:
    return datetime.fromisoformat(ts).timestamp()


def one_run(d: Path, frames: int, rate: int, timeout: float = 2.0) -> dict:
    duck = output.LineDuck(rate)
    dev = Consumer(duck, frames, rate).start()
    bd = onset.BargeDuck(duck, ("mic",), 0.25, 60.0, directory=d).start()
    try:
        time.sleep(random.uniform(0.02, 0.07))       # a random phase against both polls
        rec = heard.event(heard.EV_VAD, speech=True, at=0.0, by="bench", directory=d, agent="bench")
        t_row = time.monotonic()
        deadline = t_row + timeout
        hit = None
        while hit is None and time.monotonic() < deadline:
            for t in list(duck.trace):              # (asked, applied, ahead)
                asked, applied = t[0], t[1]
                if asked >= t_row - 0.001:
                    hit = (asked, applied)
                    break
            time.sleep(0.001)
        dr = bd.record()
    finally:
        bd.stop()
        dev.stop()
        # the user's voice ends, as the ears would say: without an offset the next run's
        # line would start ducked and never dip again
        heard.event(heard.EV_VAD, speech=False, at=0.0, by="bench", directory=d, agent="bench")
    if hit is None or bd.ducked_wall is None:
        return {"ok": False}
    return {"ok": True,
            "dip_ms": dr.get("dip_ms"),
            "applied_ms": dr.get("applied_ms"),
            "total_ms": round((hit[1] - t_row) * 1000, 2),
            "row_ts": rec["ts"]}


class FakeBot:
    """The bot's side of the mouth leg, enough for one :class:`output.CallOut`
    at a time: answers the probe and the line's header, then notes, per ``A``
    frame, when it arrived (monotonic) and its peak. As
    tests/mouth/test_mouth_call.py's ``FakeBot``; the bot's own mouth leg is
    the real one. The socket lives in its own temp dir (an AF_UNIX path is short)."""

    CALL = 315

    def __init__(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="dl-", dir="/tmp")
        self.path = os.path.join(self.dir, "call-mouth.sock")
        self.frames: list = []                   # (monotonic rx, peak) of the live line
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(self.path)
        self.srv.listen(4)
        threading.Thread(target=self._serve, daemon=True, name="fake-bot").start()

    def _serve(self) -> None:
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._one, args=(c,), daemon=True).start()

    def _one(self, c: socket.socket) -> None:
        with c:
            f = c.makefile("rb")
            hdr = json.loads(f.readline() or b"{}")
            if hdr.get("probe"):
                c.sendall((json.dumps({"ok": True, "agent": "bench", "profile": "",
                                       "calls": [self.CALL]}) + "\n").encode())
                return
            frames: list = []
            self.frames = frames
            c.sendall(json.dumps({"ok": True, "call": self.CALL}).encode() + b"\n")
            while True:
                head = f.read(5)
                if len(head) < 5:
                    return
                kind, n = head[:1], struct.unpack(">I", head[1:])[0]
                payload = f.read(n) if n else b""
                if kind == b"A":
                    a = np.frombuffer(payload, dtype="<i2")
                    frames.append((time.monotonic(), int(np.abs(a).max()) if len(a) else 0))
                    continue
                try:
                    c.sendall(json.dumps({"ok": kind == b"D",
                                          "ended": "done" if kind == b"D" else "cut"}).encode() + b"\n")
                except OSError:
                    pass
                return

    def close(self) -> None:
        self.srv.close()
        try:
            os.unlink(self.path)
            os.rmdir(self.dir)
        except OSError:
            pass


LEVEL = 0.5     # the callout writer's signal: below full scale, so a dip shows in the int16


def one_callout_run(d: Path, bot: FakeBot, rate: int, timeout: float = 2.0) -> dict:
    """One call line through the BUILD's path: ``CallOut.write`` paced, the duck
    from ``onset.for_line`` on ``call:N`` with ``hold.barge_policy``'s values."""
    dev = f"call:{bot.CALL}"
    out = output.CallOut(dev, rate)
    policy = hold.barge_policy(dev)
    step = int(rate * backends.BLOCK_S)
    block = np.full(step, LEVEL, dtype=np.float32)
    stop = threading.Event()
    err: list = []

    def writer() -> None:
        try:
            while not stop.is_set():
                out.write(block)
        except Exception as e:  # noqa: BLE001 - reported, the run is lost
            err.append(repr(e))

    bd = onset.for_line(out, dev, directory=d, policy=policy)
    w = threading.Thread(target=writer, daemon=True, name="callout-writer")
    w.start()
    try:
        # past the paced lead, so the bot's queue is full as on a line in flight,
        # then a random phase against the tail's poll and the 50 ms write
        time.sleep(out.latency + random.uniform(0.1, 0.2))
        rec = heard.event(heard.EV_VAD, speech=True, at=0.0, by="bench", directory=d,
                          agent="bench", source="call", device=dev)
        t_row = time.monotonic()
        deadline = t_row + timeout
        hit = None
        while hit is None and time.monotonic() < deadline:
            for t in list(out.duck.trace):
                if t[0] >= t_row - 0.001:
                    hit = (t[0], t[1])
                    break
            time.sleep(0.001)
        # the bot's side: the first frame after the row that the duck scaled
        full = int(LEVEL * 32767)
        bot_t = None
        end = time.monotonic() + 0.3
        while bot_t is None and time.monotonic() < end:
            bot_t = next((rx for rx, peak in list(bot.frames) if rx >= t_row and peak < full - 2), None)
            time.sleep(0.002)
        dr = bd.record(device=dev) if bd is not None else {}
    finally:
        stop.set()
        w.join(1.0)
        if bd is not None:
            bd.stop()
        out.abort()
        heard.event(heard.EV_VAD, speech=False, at=0.0, by="bench", directory=d,
                    agent="bench", source="call", device=dev)
    if hit is None or bd is None or bd.ducked_wall is None or err:
        return {"ok": False, "error": err[0] if err else None}
    total = round((hit[1] - t_row) * 1000, 2)
    ahead = dr.get("ahead_ms")
    return {"ok": True,
            "dip_ms": dr.get("dip_ms"),
            "applied_ms": dr.get("applied_ms"),
            "ahead_ms": ahead,
            "total_ms": total,
            "head_ms": None if ahead is None else round(total + ahead, 2),
            "bot_ms": None if bot_t is None else round((bot_t - t_row) * 1000, 2),
            "level": dr.get("level"), "recover_s": dr.get("recover_s"),
            "started_ducked": dr.get("started_ducked"),
            "row_ts": rec["ts"]}


def _pct(xs: list, p: float) -> float:
    xs = sorted(xs)
    if not xs:
        return float("nan")
    k = (len(xs) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def summary(runs: list) -> dict:
    ok = [r for r in runs if r.get("ok")]
    out = {"runs": len(runs), "ok": len(ok)}
    for k in ("dip_ms", "applied_ms", "ahead_ms", "total_ms", "bot_ms", "head_ms"):
        xs = [r[k] for r in ok if r.get(k) is not None]
        if xs:
            out[k] = {"p50": round(_pct(xs, 0.5), 1), "p90": round(_pct(xs, 0.9), 1),
                      "max": round(max(xs), 1), "mean": round(statistics.fmean(xs), 1)}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--runs", type=int, default=200)
    ap.add_argument("--rate", type=int, default=48000)
    ap.add_argument("--frames", type=int, default=512, help="device callback buffer (device mode)")
    ap.add_argument("--block-s", type=float, default=0.05, help="the call writer's block (call mode)")
    ap.add_argument("--call-rate", type=int, default=24000,
                    help="the synth's rate into CallOut.write (callout mode; resampled to 48 kHz)")
    ap.add_argument("--modes", default="device,call,callout")
    ap.add_argument("--json", type=Path, help="append every run as one JSON line")
    ap.add_argument("--seed", type=int, default=315)
    a = ap.parse_args(argv)
    random.seed(a.seed)
    load = os.getloadavg()
    print(json.dumps({"bench": "duck_latency", "host": os.uname().nodename,
                      "load": [round(x, 2) for x in load], "poll_s": onset.POLL_S,
                      "python": sys.version.split()[0], "started": datetime.now().astimezone()
                      .isoformat(timespec="seconds")}))
    with tempfile.TemporaryDirectory(prefix="duck-latency-") as tmp:
        d = Path(tmp)
        # the callout mode reads the barge policy: built-in defaults only, never this host's
        os.environ.update(MOUTH_DIR=str(d / "mouth"),
                          MOUTH_SETTINGS=str(d / "no-settings.json"))
        for k in ("MOUTH_BARGE", "MOUTH_BARGE_WORDS",
                  "MOUTH_BARGE_WORDS_CALL"):
            for n in envnames.group(k):   # and its old VOICEMODE_MOUTH_ name
                os.environ.pop(n, None)
        for mode in [m.strip() for m in a.modes.split(",") if m.strip()]:
            if mode == "callout":
                bot = FakeBot()
                os.environ["MOUTH_CALL_SOCKS"] = bot.path
                try:
                    runs = [one_callout_run(d, bot, a.call_rate) for _ in range(a.runs)]
                finally:
                    bot.close()
                rate, frames = a.call_rate, int(a.call_rate * backends.BLOCK_S)
            else:
                rate = a.rate
                frames = a.frames if mode == "device" else int(round(a.block_s * a.rate))
                runs = [one_run(d, frames, a.rate) for _ in range(a.runs)]
            if a.json:
                with open(a.json, "a") as f:
                    for r in runs:
                        f.write(json.dumps({"mode": mode, "frames": frames, "rate": rate, **r}) + "\n")
            print(json.dumps({"mode": mode, "frames": frames, "rate": rate,
                              "period_ms": round(1000 * frames / rate, 2), **summary(runs)}))
    print(json.dumps({"load_after": [round(x, 2) for x in os.getloadavg()]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
