#!/usr/bin/env python3
"""A fake `mouth` CLI for the keeper's say-again tests (EARS-22 S5). It plays nothing,
queues nothing and touches no mouth: it records its argv and exits as it is told.

    fake_mouth.py --log FILE [--exit N] [--sleep S] [--err TEXT] -- again UTT [flags...]

Everything after "--" is what the keeper passed (the keeper runs `MOUTH again UTT ...`,
so give it --mouth "python3 fake_mouth.py --log FILE --"). It appends that argv to
FILE as one JSON line, sleeps S seconds, then exits N (default 0). On 0 it prints the
JSON line the real `mouth again` prints; otherwise it prints TEXT on stderr.
"""
import json
import sys
import time


def main(argv):
    opts, rest = {"--exit": "0", "--sleep": "0", "--err": "mouth: no audio for it"}, []
    if "--" in argv:
        i = argv.index("--")
        head, rest = argv[:i], argv[i + 1:]
    else:
        head = argv
    for k, v in zip(head[::2], head[1::2]):
        opts[k] = v
    with open(opts["--log"], "a") as f:
        f.write(json.dumps(rest) + "\n")
    time.sleep(float(opts["--sleep"]))
    code = int(opts["--exit"])
    if code == 0:
        utt = rest[1] if len(rest) > 1 else ""
        print(json.dumps({"utt": "replay0000001", "again_of": utt, "again_from": "audio"}))
    else:
        print(opts["--err"], file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
