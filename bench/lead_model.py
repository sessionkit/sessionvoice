"""lead_model.py [--json OUT.jsonl] - the lead-buffer designs for a streamed clone, ON PAPER.

Pure arithmetic: no audio, no server, no player. It runs
the same arrival schedule as ``clone_underrun.CloneLike`` (chunk k of
``interval_s`` audio lands at ``ttfa + k*interval_s*rtf`` plus every stall
before it) against a playback that starts at a policy's chosen moment and
then plays in real time, waiting (an underrun) whenever the next chunk is not
there yet. For each policy it prints the wall time the lead adds before the
first frame (``added_s``, the time-to-first-audio cost) and the silence left
mid-line (``under_s``, what ``said.underrun_s`` would count).

Policies:

- ``none``: today. Play from the first chunk.
- ``fixed:L``: hold until ``L`` s of audio is buffered (or the line is done).
- ``whole``: hold until the line is fully rendered.
- ``adapt:F``: hold until ``F`` s of audio is buffered AND the buffer covers
  the projected shortfall ``R * (1/rate - 1)``, where ``rate`` is the
  stream's own measured audio-seconds per wall-second since its first chunk
  and ``R`` the audio still to come (the text's estimated length less what
  is buffered); a done line starts at once. ``adapt:F@e`` runs it with the
  length estimate off by a factor ``e`` (0.8 = guesses 20% short).

The cross-check against the live player is ``none``: its ``under_s`` must be
the bench's (repro-001: 0.68 s at rtf 1.25, 3 s; 1.95 s at 8 s).
"""

from __future__ import annotations

import json
import math
import sys
from typing import Optional

I = 0.3  # streaming_interval: the clone's chunk


def arrivals(dur_s: float, rtf: float, stalls=(), ttfa_s: Optional[float] = None) -> list:
    n = max(1, math.ceil(dur_s / I - 1e-9))
    t0 = I * rtf if ttfa_s is None else ttfa_s
    out = []
    for k in range(n):
        at = k * I
        out.append(t0 + k * I * rtf + sum(s for a, s in stalls if a <= at + 1e-9))
    return out


def start_index(policy: str, arr: list, dur_s: float) -> int:
    """The chunk whose arrival starts playback."""
    n = len(arr)
    if policy == "none":
        return 0
    if policy == "whole":
        return n - 1
    if policy.startswith("fixed:"):
        lead = float(policy.split(":")[1])
        return min(n - 1, max(0, math.ceil(lead / I - 1e-9) - 1))
    if policy.startswith("adapt:"):
        spec = policy.split(":")[1]
        floor, _, err = spec.partition("@")
        floor, err = float(floor), float(err or 1.0)
        est = dur_s * err
        for k in range(n):
            buffered = (k + 1) * I
            if k == n - 1:
                return k
            if buffered + 1e-9 < floor:
                continue
            if k == 0:
                continue          # one chunk: no rate yet
            rate = k * I / (arr[k] - arr[0])   # audio s per wall s, since the first chunk
            remaining = max(0.0, est - buffered)
            if rate >= 1.0 or buffered + 1e-9 >= remaining * (1.0 / rate - 1.0):
                return k
        return n - 1
    raise ValueError(policy)


def play(policy: str, dur_s: float, rtf: float, stalls=()) -> dict:
    arr = arrivals(dur_s, rtf, stalls)
    k0 = start_index(policy, arr, dur_s)
    t = arr[k0]                 # the first frame
    under = 0.0
    unders = 0
    for k, a in enumerate(arr):
        if a > t + 1e-9:        # the next chunk is late: the device runs dry
            under += a - t
            unders += 1
            t = a
        t += I
    return {"policy": policy, "dur_s": dur_s, "rtf": rtf, "stalls": list(stalls),
            "added_s": round(arr[k0] - arr[0], 3), "under_s": round(under, 3), "unders": unders}


CASES = [
    # (label, dur_s, rtf, stalls)
    ("healthy-0.5 3s", 3.0, 0.5, ()),
    ("healthy-0.5 8s", 8.0, 0.5, ()),
    ("idle-0.95 3s", 3.0, 0.95, ()),
    ("idle-0.95 6s", 6.0, 0.95, ()),
    ("idle-0.95 8s", 8.0, 0.95, ()),
    ("stall-1s 3s", 3.0, 0.95, ((1.5, 1.0),)),
    ("stall-2s 6s", 6.0, 0.95, ((1.5, 2.0),)),
    ("stall-3s 6s", 6.0, 0.95, ((1.5, 3.0),)),
    ("loaded-1.1 8s", 8.0, 1.1, ()),
    ("loaded-1.25 3s", 3.0, 1.25, ()),
    ("loaded-1.25 8s", 8.0, 1.25, ()),
    ("loaded-1.25 15s", 15.0, 1.25, ()),
    ("loaded-1.5 8s", 8.0, 1.5, ()),
    ("loaded-1.25 8s+1s", 8.0, 1.25, ((1.5, 1.0),)),
    ("starve-90s 6s", 6.0, 0.95, ((1.5, 90.0),)),
    # the worst place for a stall: just after a 2.5 s lead has started to play
    ("late-1s 6s", 6.0, 0.95, ((2.7, 1.0),)),
    ("late-2s 6s", 6.0, 0.95, ((2.7, 2.0),)),
    ("late-2.5s 6s", 6.0, 0.95, ((2.7, 2.5),)),
    ("late-3s 6s", 6.0, 0.95, ((2.7, 3.0),)),
]

POLICIES = ["none", "fixed:2", "fixed:2.5", "fixed:3", "whole",
            "adapt:2.5", "adapt:2.5@0.8", "adapt:2.5@1.2", "adapt:1"]


def main(argv: list) -> int:
    out = None
    if argv[:1] == ["--json"]:
        out, argv = open(argv[1], "w"), argv[2:]
    pols = argv or POLICIES
    print(f"{'case':<20}" + "".join(f"{p:>14}" for p in pols))
    print(f"{'':<20}" + "".join(f"{'added/under':>14}" for _ in pols))
    for label, d, r, st in CASES:
        row = f"{label:<20}"
        for p in pols:
            res = play(p, d, r, st)
            row += f"{res['added_s']:>7.2f}/{res['under_s']:<6.2f}"
            if out:
                out.write(json.dumps({"case": label, **res}) + "\n")
        print(row)
    if out:
        out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
