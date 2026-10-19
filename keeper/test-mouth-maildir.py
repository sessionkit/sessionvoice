#!/usr/bin/env python3
"""test-mouth-maildir: mouth-sounds.py against the mouth maildir, and the agent's opener against
BOTH the old queue folder and the maildir, in temp dirs
only. mouth-sounds no longer watches the old queue folder. Nothing live is
touched: no audio (MOUTH_SOUNDS_DRY=1), no real mouth (a fake `mouth` on PATH), and a
private STOP file so the running mouth-sounds is not stopped.

Run with a Python that has numpy and sounddevice (mouth-sounds imports them):
  <checkout>/.venv/bin/python keeper/test-mouth-maildir.py
The opener test needs the agent's opener script: EARS_OPENER, else `opener` on PATH;
without one it is skipped, and says so.
"""
import json, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOUNDS = HERE / "mouth-sounds.py"
OPENER = os.environ.get("EARS_OPENER") or shutil.which("opener")
fails = []


def check(name, ok, got=None):
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"  (got {got!r})"))
    if not ok:
        fails.append(name)


def mail(d, name, headers, body="a line\n"):
    tmp = d / "tmp" / name
    tmp.write_text("".join(f"{k}: {v}\n" for k, v in headers.items()) + "\n" + body)
    tmp.rename(d / "new" / name)  # maildir delivery: tmp/, then one rename


def wait_for(fn, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.05)
    return False


def test_sounds(t):
    md = t / "mouth"
    for d in (md / "new", md / "cur", md / "tmp"):
        d.mkdir(parents=True)
    canned, log, stop = t / "canned.utts", t / "sounds.jsonl", t / "STOP"
    canned.write_text("eee555\n")
    env = dict(os.environ, MOUTH_MAILDIR=str(md), MOUTH_SOUNDS_DRY="1",
               MOUTH_SOUNDS_FIRED=str(t / "fired"), MOUTH_SOUNDS_CANNED=str(canned),
               MOUTH_SOUNDS_SETTINGS=str(t / "settings.json"), MOUTH_SOUNDS_LOG=str(log),
               MOUTH_SOUNDS_STOP=str(stop),
               # the scoop watch must not read the live ~/.voicemode cursors
               MOUTH_SOUNDS_CURSORS=str(t / "cursors"), MOUTH_SOUNDS_HEARD_DIR=str(t / "heard"))
    # present before start: must NOT fire
    mail(md, "1.old000.host1", {"X-Mouth-Agent": "ada", "X-Mouth-Utt": "old000"})
    p = subprocess.Popen([sys.executable, str(SOUNDS)], env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    time.sleep(0.6)
    host = "host1"
    mail(md, f"2.aaa111.{host}", {"X-Mouth-Agent": "ada", "X-Mouth-Utt": "aaa111"})
    mail(md, f"3.bbb222.{host}", {"X-Mouth-Utt": "bbb222", "X-Mouth-Priority": "next"})
    mail(md, f"4.ccc333.{host}", {"X-Mouth-Utt": "ccc333", "X-Mouth-Priority": "10"})
    mail(md, f"5.ddd444.{host}", {"X-Mouth-Utt": "ddd444", "X-Mouth-Priority": "50"})
    mail(md, f"6.eee555.{host}", {"X-Mouth-Utt": "eee555"})                  # canned
    mail(md, f"7.ggg777.{host}", {"X-Mouth-Utt": "ggg777", "X-Mouth-Priority": "now"})
    mail(md, "1790.M1P2.host1", {"From": "lee@host1", "Importance": "high"})    # postfix-style
    (md / "new" / ".hidden").write_text("x")                                 # ignored
    ok = wait_for(lambda: log.exists() and len(log.read_text().splitlines()) >= 7, 8)
    time.sleep(0.5)
    stop.write_text("")
    try:
        out, _ = p.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        p.kill(); out = "(killed)"
    rows = [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
    got = {r["utt"]: r["sound"] for r in rows}
    check("seven lines fired", ok and len(rows) == 7, [(r["utt"], r["sound"]) for r in rows])
    check("present-at-start does not fire", "old000" not in got, got)
    check("maildir plain -> drop", got.get("aaa111") == "drop", got.get("aaa111"))
    check("maildir priority next -> front", got.get("bbb222") == "front", got.get("bbb222"))
    check("maildir priority 10 -> front", got.get("ccc333") == "front", got.get("ccc333"))
    check("maildir priority 50 -> drop", got.get("ddd444") == "drop", got.get("ddd444"))
    check("maildir canned utt -> canned", got.get("eee555") == "canned", got.get("eee555"))
    check("maildir priority now -> front", got.get("ggg777") == "front", got.get("ggg777"))
    check("postfix mail, Importance high -> front, utt from name", got.get("M1P2") == "front", got)
    check("dot-file ignored", ".hidden" not in got, got)
    check("via is recorded", {r.get("via") for r in rows} == {"maildir"}, rows)
    check("no audio errors", all(r["error"] is None for r in rows), rows)
    check("exits on its own STOP file", p.returncode == 0, out)


def test_opener(t):
    q, md, bin_ = t / "oq", t / "om", t / "bin"
    for d in (q, md / "new", md / "cur", md / "tmp", bin_):
        d.mkdir(parents=True)
    calls = t / "mouth-calls.txt"
    fake = bin_ / "mouth"
    fake.write_text(f'#!/usr/bin/env bash\necho "$*" >> {calls}\n[ "$1" = say ] && echo newutt000000\nexit 0\n')
    fake.chmod(0o755)
    (q / "50-1-qada01.json").write_text(json.dumps({"agent": "ada", "utt": "qada01"}))
    (q / "50-2-qsam01.json").write_text(json.dumps({"agent": "sam", "utt": "qsam01"}))
    mail(md, "1.mada01.host1", {"From": "ada@host1", "X-Mouth-Agent": "ada", "X-Mouth-Utt": "mada01"})
    mail(md, "2.msam01.host1", {"From": "sam@host1", "X-Mouth-Agent": "sam", "X-Mouth-Utt": "msam01"})
    mail(md, "3.mada02.host1", {"From": "Ada <ada@host1.example.net>", "X-Mouth-Utt": "mada02"})
    mail(md, "4.msam02.host1", {"From": "sam@host1", "X-Mouth-Utt": "msam02"})
    mail(md, "5.settng.host1", {"From": "settings@host1", "X-Mouth-Utt": "settng"})
    env = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}", MOUTH_QUEUE=str(q),
               MOUTH_MAILDIR=str(md), CLAUDE_CODE_AGENT="ada")
    r = subprocess.run([str(OPENER), "hello there"], env=env, capture_output=True, text=True, timeout=20)
    lines = calls.read_text().splitlines() if calls.exists() else []
    retracted = sorted(x.split()[1] for x in lines if x.startswith("retract "))
    check("opener exit 0", r.returncode == 0, r.stderr)
    check("opener retracts only ada's (queue + maildir, header + From)",
          retracted == ["mada01", "mada02", "qada01"], retracted)
    check("opener then says, at the front, held",
          any(x.startswith("say ") and "--next" in x and "--hold turn-end" in x and x.endswith("hello there")
              for x in lines), lines)
    check("opener prints the new utt", r.stdout.strip().endswith("newutt000000"), r.stdout)


with tempfile.TemporaryDirectory(prefix="mouth-maildir-test-") as d:
    t = Path(d)
    (t / "s").mkdir(); (t / "o").mkdir()
    test_sounds(t / "s")
    if OPENER:
        test_opener(t / "o")
    else:
        print("skip opener: no EARS_OPENER and no `opener` on PATH")
print(f"{'ALL PASSED' if not fails else str(len(fails)) + ' FAILED'}")
sys.exit(1 if fails else 0)
