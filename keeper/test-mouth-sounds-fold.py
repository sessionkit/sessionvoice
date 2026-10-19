#!/usr/bin/env python3
"""test-mouth-sounds-fold: the behaviours folded into mouth-sounds.py from an agent's
fork (~/.<agent>/moonshot-ambient-listen/mouth-sounds.py, sha256 0c8bc282, HEAD 307358d).
Every path is a temp dir; the audio is a FAKE sounddevice, the default output a FAKE
`SwitchAudioSource`, so no audio plays and nothing live (~/.voicemode, an agent's home) is read.

  1. scoop: a words ride-along cursor moving forward over the user's rows plays
     `scoop` {session[:8], rows}; agent-only rows, a first sighting, a file change, a
     move back, a .lock and a .tmp cursor play nothing. The DEFAULT settings carry scoop.
  2. on-error rescan: a PortAudioError on the play rescans
     (terminate + initialize) and plays once more, error null, device named; when the
     route moved under that failed play, the NEXT sound does not re-init again.
  3. on-error rescan of follow_default: a PortAudioError raised by follow_default's
     own re-init gets the rescan too, and the sound still plays.
  4. MAX_FAILS: MOUTH_SOUNDS_MAX_FAILS failures in a row exit 3 for launchd; a
     success in between resets the count.

Run with the ears .venv (mouth-sounds imports numpy):
  <checkout>/.venv/bin/python keeper/test-mouth-sounds-fold.py
"""
import json, os, subprocess, sys, tempfile, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOUNDS = HERE / "mouth-sounds.py"
fails = []

# A fake sounddevice. Counters live in files the test writes, read on every call:
#   FAKE_SD_FAIL_PLAYS  the next N play() calls raise PortAudioError
#   FAKE_SD_FAIL_MOVE   on such a failure, the system default moves to this name
#   FAKE_SD_FAIL_INITS  the next N _initialize() calls raise PortAudioError
FAKE = '''
import os
class PortAudioError(Exception):
    pass
_LOG, _DEFAULT = os.environ["FAKE_SD_LOG"], os.environ["FAKE_SD_DEFAULT"]
_DIR = os.path.dirname(_LOG)
def _note(what):
    with open(_LOG, "a") as f:
        f.write(what + "\\n")
def _system_default():
    return open(_DEFAULT).read().strip()
def _take(name):
    p = os.path.join(_DIR, name)
    try:
        n = int(open(p).read().strip() or 0)
    except (OSError, ValueError):
        return False
    if n <= 0:
        return False
    open(p, "w").write(str(n - 1))
    return True
_cached = _system_default()   # Pa_Initialize at import reads the default once
def _dev(name):
    return {"name": name, "max_output_channels": 2, "default_samplerate": 48000.0}
def query_devices(device=None, kind=None):
    if kind:
        return _dev(_cached)
    return [_dev("AirPods"), _dev("MacBook Pro Speakers")]
def play(data, sr, device=None, blocking=False):
    if _take("fail-plays"):
        _note("play-error:" + _cached)
        try:
            move = open(os.path.join(_DIR, "fail-move")).read().strip()
        except OSError:
            move = ""
        if move:
            open(_DEFAULT, "w").write(move + "\\n")
        raise PortAudioError("Error opening OutputStream: Internal PortAudio error [PaErrorCode -9986]")
    _note("play:" + _cached)
def _terminate():
    _note("terminate")
def _initialize():
    global _cached
    if _take("fail-inits"):
        _note("initialize-error")
        raise PortAudioError("Error initializing PortAudio [PaErrorCode -9986]")
    _cached = _system_default()
    _note("initialize:" + _cached)
'''
FAKE_SWITCH = '''#!/bin/sh
# fake SwitchAudioSource: -c prints the current default output; anything else is refused
case " $* " in
  *" -c "*) cat "$FAKE_SD_DEFAULT" ;;
  *) echo "fake SwitchAudioSource: refused: $*" >&2; exit 2 ;;
esac
'''


def check(name, ok, got=None):
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"  (got {got!r})"))
    if not ok:
        fails.append(name)


def until(pred, timeout=6.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


class Rig:
    """One mouth-sounds process on temp dirs, with the fakes first on the paths."""

    def __init__(self, d, dry=False, settings=None, **extra):
        t = self.t = Path(d)
        self.fake, self.new, self.run = t / "fake", t / "mouth" / "new", t / "run"
        self.cursors, self.heard = t / "heard-cursor", t / "conversations"
        for p in (self.fake, self.new, self.run, self.cursors, self.heard, t / "mouth" / "cur"):
            p.mkdir(parents=True)
        (self.fake / "sounddevice.py").write_text(FAKE)
        (self.fake / "SwitchAudioSource").write_text(FAKE_SWITCH)
        (self.fake / "SwitchAudioSource").chmod(0o755)
        self.default = t / "default-output"
        self.default.write_text("AirPods\n")
        self.settings = t / "settings.json"
        if settings is not None:
            self.settings.write_text(json.dumps(settings))
        self.sd_log, self.log = self.fake / "sd.log", t / "sounds.jsonl"
        self.env = dict(os.environ, PYTHONPATH=str(self.fake),
                        PATH=f"{self.fake}:{os.environ.get('PATH', '')}",
                        FAKE_SD_LOG=str(self.sd_log), FAKE_SD_DEFAULT=str(self.default),
                        MOUTH_MAILDIR=str(t / "mouth"),
                        EARS_RUN=str(self.run), MOUTH_SOUNDS_FIRED=str(self.run / "mid-turn.fired"),
                        MOUTH_SOUNDS_CANNED=str(self.run / "canned"),
                        MOUTH_SOUNDS_SETTINGS=str(self.settings), MOUTH_SOUNDS_LOG=str(self.log),
                        MOUTH_SOUNDS_STOP=str(t / "STOP"),
                        MOUTH_SOUNDS_CURSORS=str(self.cursors), MOUTH_SOUNDS_HEARD_DIR=str(self.heard),
                        MOUTH_SOUNDS_DEFAULT_CMD="SwitchAudioSource -c -t output", **extra)
        if dry:
            self.env["MOUTH_SOUNDS_DRY"] = "1"
        else:
            self.env.pop("MOUTH_SOUNDS_DRY", None)

    def set(self, name, value):
        (self.fake / name).write_text(str(value))

    def start(self):
        self.p = subprocess.Popen([sys.executable, str(SOUNDS)], env=self.env,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        until(lambda: self.settings.exists(), 5)
        time.sleep(0.6)   # past the first cursor look and the initial listings

    def stop(self):
        (self.t / "STOP").write_text("")
        try:
            out, _ = self.p.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            self.p.kill()
            out, _ = self.p.communicate()
        return out

    def sounds(self):
        try:
            return [json.loads(x) for x in self.log.read_text().splitlines()]
        except OSError:
            return []

    def sd(self):
        try:
            return self.sd_log.read_text().splitlines()
        except OSError:
            return []

    def drop(self, utt):
        n = len(self.sounds())
        # a line lands in the mouth maildir (the old queue folder is not watched)
        (self.new / f"{time.time_ns()}.{utt}.host").write_text(f"X-Mouth-Utt: {utt}\n\na line\n")
        return until(lambda: len(self.sounds()) > n or self.p.poll() is not None)


def row(kind, source=None, text="x"):
    r = {"kind": kind, "text": text}
    if source is not None:
        r["source"] = source
    return (json.dumps(r) + "\n").encode()


def cursor(dirpath, name, fname, offset):
    (dirpath / name).write_text(json.dumps({"file": fname, "offset": offset}))


# 1. scoop: a ride-along cursor moving forward over the user's rows
with tempfile.TemporaryDirectory() as d:
    r = Rig(d, dry=True)          # no settings file: the DEFAULT is written, scoop in it
    h1, h2 = r.heard / "h1.jsonl", r.heard / "h2.jsonl"
    base = row("turn", "agent")
    h1.write_bytes(base)
    h2.write_bytes(base)
    b0 = len(base)
    # the cursors present at start (a cursor's first sighting never sounds)
    for s in ("aaaaaaaa-1", "cccccccc-1", "dddddddd-1", "hhhhhhhh-1",
              "ffffffff-1.lock", "gggggggg-1.tmp-77"):
        cursor(r.cursors, s, "h1.jsonl", b0)
    cursor(r.cursors, "eeeeeeee-1", "h1.jsonl", 10_000)   # will move BACK
    (r.cursors / "junk-cursor").write_text("[1, 2]")      # not an object: skipped, no crash
    cursor(r.cursors, "nnnnnnnn-1", 5, b0)                # a non-string file: skipped, no crash
    r.start()
    settings = json.loads(r.settings.read_text()) if r.settings.exists() else {}
    check("1 scoop: the DEFAULT settings carry scoop [[1760,22],[2637,28]] vol 0.07",
          settings.get("sounds", {}).get("scoop") ==
          {"on": True, "notes": [[1760, 22], [2637, 28]], "volume": 0.07}, settings)

    # The user's rows: a partial with NO source (counts as mic) and a call turn; then agent-only rows
    user_rows = row("partial") + row("turn", "call")
    agent = row("turn", "agent") + row("say", "mic") + b"not json\n" + b"[3]\n"
    mixed = row("partial", "mic") + row("partial", "agent")
    with open(h1, "ab") as f:
        f.write(user_rows)
    b1 = b0 + len(user_rows)
    with open(h1, "ab") as f:
        f.write(agent)
    b2 = b1 + len(agent)
    with open(h1, "ab") as f:
        f.write(mixed)
    b3 = b2 + len(mixed)
    with open(h2, "ab") as f:
        f.write(user_rows)

    cursor(r.cursors, "aaaaaaaa-1", "h1.jsonl", b1)                  # forward over the user's 2 rows
    cursor(r.cursors, "bbbbbbbb-1", "h1.jsonl", b1)                  # first sighting
    cursor(r.cursors, "cccccccc-1", "h1.jsonl", b0)                  # placed at b0 ...
    cursor(r.cursors, "dddddddd-1", "h2.jsonl", b0 + len(user_rows))      # a file change
    cursor(r.cursors, "eeeeeeee-1", "h1.jsonl", b0)                  # a move back
    cursor(r.cursors, "ffffffff-1.lock", "h1.jsonl", b1)             # a lock file
    cursor(r.cursors, "gggggggg-1.tmp-77", "h1.jsonl", b1)           # a temp file
    cursor(r.cursors, "hhhhhhhh-1", "h1.jsonl", b0)                  # placed at b0 ...
    cursor(r.cursors, "nnnnnnnn-1", 5, b1)                           # moves on, file still not a name
    time.sleep(0.8)
    (r.cursors / "cccccccc-1").write_text(json.dumps({"file": "h1.jsonl", "offset": b1}))
    (r.cursors / "hhhhhhhh-1").write_text(json.dumps({"file": "h1.jsonl", "offset": b1}))
    time.sleep(0.8)
    cursor(r.cursors, "cccccccc-1", "h1.jsonl", b2)                  # ... then over agent rows only
    cursor(r.cursors, "hhhhhhhh-1", "h1.jsonl", b3)                  # ... then over 1 mic + 1 agent
    time.sleep(0.8)
    out = r.stop()
    scoops = [s for s in r.sounds() if s["sound"] == "scoop"]
    got = sorted((s["session"], s["rows"]) for s in scoops)
    check("1 scoop: forward over the user's rows (no source = mic, call) -> one scoop, rows 2",
          ("aaaaaaaa", 2) in got and got.count(("aaaaaaaa", 2)) == 1, (got, out[-400:]))
    check("1 scoop: rows counts only the user's (1 mic partial beside 1 agent row)",
          ("hhhhhhhh", 1) in got, got)
    check("1 scoop: forward over agent-only rows -> none (cccccccc's 2nd move)",
          [g for g in got if g[0] == "cccccccc"] == [("cccccccc", 2)], got)
    check("1 scoop: first sighting -> none", not any(g[0] == "bbbbbbbb" for g in got), got)
    check("1 scoop: a different heard file -> none", not any(g[0] == "dddddddd" for g in got), got)
    check("1 scoop: a move back -> none", not any(g[0] == "eeeeeeee" for g in got), got)
    check("1 scoop: a cursor whose file is not a name is skipped (review M-1)",
          not any(g[0] == "nnnnnnnn" for g in got) and "TypeError" not in out, (got, out[-400:]))
    check("1 scoop: .lock and .tmp cursors ignored",
          not any(g[0] in ("ffffffff", "gggggggg") for g in got), got)
    # cccccccc and hhhhhhhh's FIRST moves (b0 -> b1) cross the user's 2 rows too: a scoop each
    check("1 scoop: exactly the expected scoops, no errors, still running at STOP",
          got == [("aaaaaaaa", 2), ("cccccccc", 2), ("hhhhhhhh", 1), ("hhhhhhhh", 2)]
          and all(s["error"] is None for s in r.sounds())
          and "stop file seen" in out, (got, out[-400:]))

# 2. on-error rescan: a failed play rescans and plays once more; the route moved under it
with tempfile.TemporaryDirectory() as d:
    r = Rig(d, settings={"sounds": {"drop": {"on": True, "notes": [[1568, 5]], "volume": 0.1}}})
    r.start()
    r.set("fail-plays", 1)
    r.set("fail-move", "MacBook Pro Speakers")    # the AirPods left as the play failed
    r.drop("utta")
    r.set("fail-move", "")
    mark = len(r.sd())
    r.drop("uttb")
    out = r.stop()
    sd_log, sounds = r.sd(), r.sounds()
    first = sd_log[:mark]
    check("2 rescan: the failed play is followed by terminate + initialize, then a play",
          first == ["play-error:AirPods", "terminate", "initialize:MacBook Pro Speakers",
                    "play:MacBook Pro Speakers"], (first, out[-400:]))
    check("2 rescan: the record has error null and names the device it played on",
          sounds[:1] and sounds[0]["error"] is None and sounds[0]["device"] == "MacBook Pro Speakers",
          sounds)
    check("2 rescan: the NEXT sound does not re-init again (D3: _seen = the rescan's default)",
          sd_log[mark:] == ["play:MacBook Pro Speakers"], sd_log[mark:])
    check("2 rescan: two sounds, both clean", len(sounds) == 2 and all(s["error"] is None for s in sounds)
          and sounds[1]["device"] == "MacBook Pro Speakers", sounds)

# 3. a PortAudioError raised by follow_default's own re-init gets the rescan too
with tempfile.TemporaryDirectory() as d:
    r = Rig(d, settings={"sounds": {"drop": {"on": True, "notes": [[1568, 5]], "volume": 0.1}}})
    r.start()
    r.default.write_text("MacBook Pro Speakers\n")   # the route moves: follow_default re-inits
    r.set("fail-inits", 1)                           # ... and that re-init raises
    r.drop("uttc")
    out = r.stop()
    sd_log, sounds = r.sd(), r.sounds()
    check("3 follow-rescan: follow's re-init raised, the rescan re-inited and the sound played",
          sd_log == ["terminate", "initialize-error", "terminate", "initialize:MacBook Pro Speakers",
                     "play:MacBook Pro Speakers"], (sd_log, out[-400:]))
    check("3 follow-rescan: the record is clean and names the new default",
          len(sounds) == 1 and sounds[0]["error"] is None
          and sounds[0]["device"] == "MacBook Pro Speakers", sounds)

# 4. MAX_FAILS: failures in a row exit 3 for launchd; a success resets the count
with tempfile.TemporaryDirectory() as d:
    r = Rig(d, settings={"sounds": {"drop": {"on": True, "notes": [[1568, 5]], "volume": 0.1}}},
            MOUTH_SOUNDS_MAX_FAILS="3")
    r.start()
    r.set("fail-plays", 4)                      # two sounds, each fails its play and its retry
    r.drop("f1"); r.drop("f2")
    r.set("fail-plays", 0)
    r.drop("ok1")                               # a success: the count goes back to 0
    r.set("fail-plays", 6)
    r.drop("f3"); r.drop("f4")
    alive = r.p.poll() is None
    r.drop("f5")                                # the 3rd failure in a row
    try:
        rc = r.p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        rc = None
    out = r.stop() if rc is None else r.p.communicate()[0]
    errs = [bool(s["error"]) for s in r.sounds()]
    check("4 max-fails: a success between resets the count (alive after 2+ok+2 failures)",
          alive, (errs, out[-400:]))
    check("4 max-fails: the 3rd failure in a row exits 3", rc == 3, (rc, out[-400:]))
    check("4 max-fails: every failure is logged with its error",
          errs == [True, True, False, True, True, True], errs)
    check("4 max-fails: it says why it exits", "3 failures in a row" in out, out[-400:])

print("ALL GREEN" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
