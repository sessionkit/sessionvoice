#!/usr/bin/env python3
"""mouth-sounds: little sounds for the mouth's flow, so the user can hear the machinery work.

A little sound when an opener has been added, another when a mid-turn
wake has fired, and one for when things land in the mouth ready to speak:
sounds in the flow, each one
switchable.

Watches (no model, no mouth changes). The loop WAITS on a kqueue over these places (it
once slept 50 ms between looks, 5% of a core), with a 250 ms backstop that also paces
the cursor look; where kqueue is not there (or MOUTH_SOUNDS_WAIT=poll) it polls every 250 ms:
  - where a line lands, a NEW file = a line landed; one line fires once, deduped by its utt:
      the mouth maildir ~/.mail/agents/mouth/new/ (front = X-Mouth-Priority
      next | now | a number under 50, or Importance: high; utt = X-Mouth-Utt,
      else the name's middle part; log entries go to cur/, never new/)
    The old voice_mode.mouth queue folder (~/.voicemode/mouth/queue) is no longer watched:
    no mouth writes it since the maildir cutover.
      drop     a real line (anyone's)          two falling notes, a droplet
      front    a line sent to the front        the droplet, higher
      canned   the auto-opener's canned line    one soft low note
  - MOUTH_SOUNDS_FIRED (mtime; default EARS_RUN/mid-turn.fired, EARS_RUN defaulting
    to ~/.cache/ears/run), touched by skills/ears/scripts/ears-mid-turn when it fires:
      bud      a mid-turn wake                  two rising notes
  - MOUTH_SOUNDS_CURSORS (default <home>/state/heard-cursor/<session>, <home> being
    sessionvoice's home (ears.home; ~/.voicemode on a host that keeps it there): the words
    ride-along's place in the heard log, rewritten by `heard.py hook` on each tool
    batch). When one moves forward, within the same heard log (MOUTH_SOUNDS_HEARD_DIR,
    default <home>/logs/conversations), past at least one partial or turn of
    the user's (source mic or call; no source counts as mic):
      scoop    an agent's ride-along took your words   a quick high pip
    Its line carries "seq": [old, new] (null inside the pair when not an int) and "by",
    the cursor's mutator ({event, caller, pid, at, heard, session}) or null when the
    cursor names none, never a guess: the log says which one
    scooped.
    A sound plays when an agent's ride-along scoops the
    partials. The pager can't give this: it only knows mail.
Settings: mouth-sounds.json beside this file, reread live: {"sounds": {name: {on,
notes: [[freq, ms], ...], volume}}}. One JSON line per sound to mouth-sounds.jsonl.
Run with the ears .venv (sounddevice, numpy), like ears-sounds.py:
  <checkout>/.venv/bin/python keeper/mouth-sounds.py
Plays to the CURRENT default output: PortAudio reads the devices once (at import), so
before each sound it asks CoreAudio for the default (~0.1 ms) and rescans when that
moved (after a route change ears-sounds once played 557 sounds, no error, to a
stale default). Each record names the `device` PortAudio opened. On a PortAudio
error it rescans and tries once more; MOUTH_SOUNDS_MAX_FAILS (default 20) failures in
a row exit 3, so launchd (KeepAlive) starts a fresh process.
Env, for tests: MOUTH_MAILDIR, MOUTH_SOUNDS_FIRED, _CANNED, _SETTINGS,
_LOG, _STOP, _CURSORS, _HEARD_DIR, _MAX_FAILS, _WAIT (poll: no kqueue), _BACKSTOP (s); MOUTH_SOUNDS_DRY=1 logs without touching the audio device;
MOUTH_SOUNDS_DEFAULT_CMD, a command printing the default output's name, read
instead of CoreAudio.
"""
import ctypes, ctypes.util, email.parser, fnmatch, json, os, select, shlex, subprocess, time
from datetime import datetime
from pathlib import Path

import numpy as np
import sounddevice as sd

#: The voice settings folder under $HOME: a copy of ears.home.SETTINGS (keeper/ imports
#: nothing from src/; tests/test_settings_dir.py holds every copy to it).
VOICE_SETTINGS = ".sessionkit/voice/settings"


def sessionvoice_home():
    """sessionvoice's home: a copy of ears.home.sessionvoice_home (tests/test_home.py holds
    it). $SESSIONVOICE_HOME, $VOICEMODE_BASE_DIR, ~/.sessionvoice or ~/.voicemode if it
    holds the heard log, else ~/.sessionvoice."""
    import glob, os
    for name in ("SESSIONVOICE_HOME", "VOICEMODE_BASE_DIR"):
        if os.environ.get(name):
            return os.path.expanduser(os.path.expandvars(os.environ[name]))
    for d in ("~/.sessionvoice", "~/.voicemode"):
        c = os.path.expanduser(d + "/logs/conversations")
        if os.path.exists(c + "/heard.lock") or glob.glob(glob.escape(c) + "/heard_*.jsonl"):
            return os.path.expanduser(d)
    return os.path.expanduser("~/.sessionvoice")


HERE = Path(__file__).resolve().parent
ENV = os.environ.get


def _p(var, default):
    return Path(ENV(var) or default).expanduser()


MAILDIR = _p("MOUTH_MAILDIR", Path.home() / ".mail/agents/mouth")
NEW = MAILDIR / "new"
RUN = _p("EARS_RUN", Path.home() / ".cache/ears/run")
FIRED = _p("MOUTH_SOUNDS_FIRED", RUN / "mid-turn.fired")
CANNED = _p("MOUTH_SOUNDS_CANNED", RUN / "auto-opener.utts")
SETTINGS = _p("MOUTH_SOUNDS_SETTINGS", Path.home() / VOICE_SETTINGS / "mouth-sounds.json")
LOG = _p("MOUTH_SOUNDS_LOG", Path.home() / "Library/Logs/ears/mouth-sounds.jsonl")
STOP = _p("MOUTH_SOUNDS_STOP", HERE / "STOP-mouth-sounds")
CURSORS = _p("MOUTH_SOUNDS_CURSORS", Path(sessionvoice_home()) / "state/heard-cursor")
HEARD_DIR = _p("MOUTH_SOUNDS_HEARD_DIR", Path(sessionvoice_home()) / "logs/conversations")
DRY = ENV("MOUTH_SOUNDS_DRY") == "1"
DEFAULT = {"sounds": {
    "drop":   {"on": True, "notes": [[1568, 35], [1175, 55]], "volume": 0.10},
    "front":  {"on": True, "notes": [[2093, 35], [1568, 55]], "volume": 0.10},
    "canned": {"on": True, "notes": [[784, 60]], "volume": 0.06},
    "bud":    {"on": True, "notes": [[880, 40], [1319, 60]], "volume": 0.09},
    "scoop":  {"on": True, "notes": [[1760, 22], [2637, 28]], "volume": 0.07},
}}
if not SETTINGS.exists():
    SETTINGS.write_text(json.dumps(DEFAULT, indent=2) + "\n")
_s, _m = DEFAULT, None


def settings():
    global _s, _m
    try:
        m = SETTINGS.stat().st_mtime
        if m != _m:
            _s, _m = json.loads(SETTINGS.read_text()), m
    except (OSError, ValueError):
        pass
    return _s


def tone(sr, notes, vol):
    parts = []
    for f, ms in notes:
        n = max(2, int(sr * ms / 1000)); t = np.arange(n) / sr
        fade = min(0.004, t[-1] / 3)
        env = np.clip(np.minimum(t / fade, (t[-1] - t) / fade), 0, 1)
        parts.append(vol * env * np.sin(2 * np.pi * f * t))
    return np.concatenate(parts).astype(np.float32)


_ca = None


def _coreaudio_default():
    """(device id, name) of the system default output, from CoreAudio (~0.1 ms, no fork).
    The same as ears-sounds.py's; the id changes when a device is re-added."""
    global _ca
    if _ca is None:
        class Addr(ctypes.Structure):
            _fields_ = [("sel", ctypes.c_uint32), ("scope", ctypes.c_uint32), ("elem", ctypes.c_uint32)]
        lib = ctypes.CDLL(ctypes.util.find_library("CoreAudio"))
        lib.AudioObjectGetPropertyData.argtypes = [ctypes.c_uint32, ctypes.POINTER(Addr), ctypes.c_uint32,
                                                   ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
        lib.AudioObjectGetPropertyData.restype = ctypes.c_int32
        cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
        cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
        cf.CFStringGetCString.restype = ctypes.c_bool
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        _ca = (Addr, lib, cf)
    Addr, lib, cf = _ca

    def get(obj, sel, out):  # global scope, main element
        n = ctypes.c_uint32(ctypes.sizeof(out))
        r = lib.AudioObjectGetPropertyData(obj, Addr(int.from_bytes(sel, "big"), int.from_bytes(b"glob", "big"), 0),
                                           0, None, ctypes.byref(n), ctypes.byref(out))
        if r:
            raise OSError(f"AudioObjectGetPropertyData {sel.decode()} -> {r}")
    dev, ref = ctypes.c_uint32(), ctypes.c_void_p()
    get(1, b"dOut", dev)          # kAudioObjectSystemObject, kAudioHardwarePropertyDefaultOutputDevice
    get(dev.value, b"lnam", ref)  # kAudioObjectPropertyName: a CFString we own
    buf = ctypes.create_string_buffer(512)
    ok = cf.CFStringGetCString(ref, buf, len(buf), 0x08000100)  # kCFStringEncodingUTF8
    cf.CFRelease(ref)
    return dev.value, buf.value.decode() if ok else ""


def default_output():
    """(id, name) of the CURRENT default output; None if it can't be read.
    MOUTH_SOUNDS_DEFAULT_CMD (tests): a command printing its name (id None)."""
    try:
        cmd = ENV("MOUTH_SOUNDS_DEFAULT_CMD")
        if cmd:
            name = subprocess.run(shlex.split(cmd), capture_output=True, text=True, timeout=5).stdout.strip()
            return (None, name) if name else None
        return _coreaudio_default()
    except Exception:  # a failed query must not cost a sound
        return None


_seen = None if DRY else default_output()  # the default PortAudio read the devices with


def follow_default():
    """Rescan PortAudio's devices when the system default moved since it last read them."""
    global _seen
    now = default_output()
    if now is None:
        return
    if _seen is not None and now != _seen:
        print(f"mouth-sounds: default output {_seen[1]!r} -> {now[1]!r} - rescanning the devices", flush=True)
        sd._terminate()
        sd._initialize()
        now = default_output() or now
    _seen = now


MAX_FAILS = int(ENV("MOUTH_SOUNDS_MAX_FAILS") or 20)
_fails = 0


def _out(snd):
    """Play once; the name of the device PortAudio opened."""
    dev = sd.query_devices(kind="output")
    sr = int(dev["default_samplerate"])
    sd.play(tone(sr, snd["notes"], snd.get("volume", 0.08)), sr, blocking=True)
    return dev["name"]


def play(name, detail):
    """Play one sound. Before it, follow_default() rescans when the system default moved:
    a stale default raises nothing, so an on-error rescan alone never fires.
    On a PortAudio error (in the follow or the play), rescan the devices and try once
    more: PortAudio reads the device list once, so after the AirPods came or went every
    sound failed (-9986, the fault ears-sounds hit and fixed
    with this rescan). MAX_FAILS failures in a row exit, so launchd
    (dev.sessionkit.voice.mouth-sounds, KeepAlive) starts a fresh process."""
    global _fails, _seen
    snd = settings().get("sounds", {}).get(name)
    if not snd or not snd.get("on"):
        return
    t0, err, device = time.time(), None, None
    try:
        if not DRY:
            try:
                follow_default()
                device = _out(snd)
            except sd.PortAudioError as x:
                print(f"mouth-sounds: {x} - rescanning the devices, one retry", flush=True)
                sd._terminate()
                sd._initialize()
                _seen = default_output()   # as ears-sounds' reinit(): the default this rescan read
                device = _out(snd)
        _fails = 0
    except Exception as x:  # a missing device must not kill the watcher
        err = f"{type(x).__name__}: {x}"
        _fails += 1
    with open(LOG, "a") as f:
        f.write(json.dumps({"ts": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                            "sound": name, **detail, "device": device,
                            "play_s": round(time.time() - t0, 3), "error": err}) + "\n")
    if _fails >= MAX_FAILS:
        print(f"mouth-sounds: {_fails} failures in a row ({err}); exiting for launchd to restart", flush=True)
        raise SystemExit(3)


def is_canned(utt):
    for _ in range(6):  # the auto-opener records the utt just after mouth say returns
        try:
            if utt in CANNED.read_text().split():
                return True
        except OSError:
            pass
        time.sleep(0.05)
    return False


HEADERS = email.parser.BytesHeaderParser()
RANKS = {"now": -1.0, "next": 0.0}  # as the mouth's box.py ranks them; none = 50


def mail_line(name):
    """(utt, front) for a mail that landed in NEW. It may already have moved to cur/
    (the player took it); then read it there. Unreadable: the name alone, not front."""
    for p in [NEW / name, *sorted((MAILDIR / "cur").glob(name + ":*"))]:
        try:
            with open(p, "rb") as f:
                h = HEADERS.parse(f)
            break
        except OSError:
            continue
    else:
        h = {}
    parts = name.split(".")
    utt = (h.get("X-Mouth-Utt") or "").strip() or (parts[1] if len(parts) >= 3 else name)
    pri = (h.get("X-Mouth-Priority") or "").strip().lower()
    try:
        rank = RANKS[pri] if pri in RANKS else float(pri) if pri else 50.0
    except ValueError:
        rank = 50.0
    front = rank < 50 or (h.get("Importance") or "").strip().lower() == "high"
    return utt, front


def listing(d, pattern):
    """The names in folder d that match pattern: set() when d is absent, None when it cannot be
    read (the loop then keeps what it last saw, so a permission flap chimes nothing). Read with
    os.listdir: Path.glob reads an unreadable d as empty, and from Python 3.14 Path.exists reads
    an unreachable d as absent, so neither can tell the two apart."""
    try:
        names = os.listdir(d)
    except (FileNotFoundError, NotADirectoryError):
        return set()
    except OSError:
        return None
    return {n for n in names if fnmatch.fnmatchcase(n, pattern)}


played = {}  # utt -> time: one line fires once


def landed(utt, front, where):
    now_t = time.time()
    for u in [u for u, t in played.items() if now_t - t > 600]:
        del played[u]
    if utt in played:
        return
    played[utt] = now_t
    kind = "canned" if is_canned(utt) else ("front" if front else "drop")
    play(kind, {"utt": utt, "via": where})


def _seq(r):
    """A cursor's seq when it is an int (not a bool), else None: logged as null, never skips."""
    v = r.get("seq")
    return v if isinstance(v, int) and not isinstance(v, bool) else None


_cursor_cache = {}   # file name -> (stat signature, parsed row or None): re-read only what changed


def cursors():
    """{session file: (heard log name, offset, seq, by)} for every words ride-along cursor;
    lock and temp files skipped, an unreadable cursor skipped. `seq` is None unless an int;
    `by` (the writer that moved it) is the dict the cursor carries, else None:
    a legacy cursor, or a non-dict `by`, is unattributed, never guessed.
    A file is read only when its (mtime, size, inode) moved since the last look (measured:
    ~400 cursor files, read 4 times a second, were most of the idle CPU); the writer replaces
    a cursor by rename, so the inode moves, and an in-place write moves the mtime."""
    out, cache = {}, {}
    try:
        for c in os.scandir(CURSORS):
            if c.name.endswith(".lock") or ".tmp" in c.name:
                continue
            try:
                st = c.stat()
                sig = (st.st_mtime_ns, st.st_size, st.st_ino)
                hit = _cursor_cache.get(c.name)
                if hit and hit[0] == sig:
                    row = hit[1]
                else:
                    row = None
                    try:
                        r = json.loads(Path(c.path).read_text())
                        if isinstance(r.get("file"), str):   # a name, or open() raises out of the loop
                            by = r.get("by")
                            row = (r.get("file"), int(r.get("offset") or 0), _seq(r),
                                   by if isinstance(by, dict) else None)
                    except (OSError, ValueError, TypeError, AttributeError):
                        pass
                cache[c.name] = (sig, row)
                if row:
                    out[c.name] = row
            except OSError:
                continue
    except OSError:
        pass
    _cursor_cache.clear()
    _cursor_cache.update(cache)
    return out


def mikes_rows(name, start, end):
    """How many of the user's partial/turn rows sit in bytes [start, end) of one heard log."""
    try:
        with open(HEARD_DIR / name, "rb") as f:
            f.seek(start)
            chunk = f.read(max(0, end - start))
    except OSError:
        return 0
    n = 0
    for line in chunk.splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and r.get("kind") in ("partial", "turn") \
                and r.get("source", "mic") in ("mic", "call"):
            n += 1
    return n


def scoops(old, new):
    """[(session, rows, by, [old seq, new seq])] for each cursor that moved forward within
    the same heard log past at least one of the user's rows. A first sighting, a new file or a
    move back: no sound. `by` is the NEW cursor's: the write that moved it."""
    out = []
    for sess, (fname, off, seq, by) in new.items():
        was = old.get(sess)
        if was and fname and was[0] == fname and off > was[1]:
            n = mikes_rows(fname, was[1], off)
            if n:
                out.append((sess, n, by, [was[2], seq]))
    return out


class Waiter:
    """Sleep until something we watch changes, or `timeout` runs out.
    A kqueue vnode watch on each place the loop looks at: NEW, the maildir (NEW may not
    exist yet), the FIRED file and its folder (a touch changes the file, not the folder; a
    replace changes the folder), CURSORS, and the STOP file's folder. A place that does not
    exist yet is watched through its nearest existing parent; a place that came, went or was
    replaced (new inode) is re-opened at the top of every wait, BEFORE the caller looks, so
    nothing that landed in between is missed: a wait that opened a watch returns at once, the
    caller always rescans after a wake, and the
    timeout is a backstop for anything a watch cannot see. No kqueue (or MOUTH_SOUNDS_WAIT=poll):
    a plain sleep of the timeout."""
    FLAGS = 0
    if hasattr(select, "kqueue"):
        FLAGS = (select.KQ_NOTE_WRITE | select.KQ_NOTE_EXTEND | select.KQ_NOTE_ATTRIB | select.KQ_NOTE_DELETE
                 | select.KQ_NOTE_RENAME | select.KQ_NOTE_LINK | select.KQ_NOTE_REVOKE)

    def __init__(self, places):
        self.places = list(places)
        self.kq = select.kqueue() if hasattr(select, "kqueue") and ENV("MOUTH_SOUNDS_WAIT") != "poll" else None
        self.open = {}   # watched path -> (fd, (dev, inode))

    @staticmethod
    def _nearest(p):
        for q in (p, *p.parents):
            try:
                return q, os.stat(q)
            except OSError:
                continue
        return None, None

    def sync(self):
        """Match the open watches to the places as they are now; True when it opened a new one
        (a place that just appeared may already hold what we have not seen)."""
        opened = False
        want = {}
        for p in self.places:
            q, st = self._nearest(p)
            if q is not None:
                want[q] = (st.st_dev, st.st_ino)
        for q, (fd, ident) in list(self.open.items()):
            if want.get(q) != ident:         # gone, or a different file now: its vnode is dead
                del self.open[q]
                try:
                    os.close(fd)             # closing drops the kevent
                except OSError:
                    pass
        for q, ident in want.items():
            if q in self.open:
                continue
            try:
                fd = os.open(q, getattr(os, "O_EVTONLY", os.O_RDONLY))
            except OSError:
                continue
            try:
                self.kq.control([select.kevent(fd, select.KQ_FILTER_VNODE, select.KQ_EV_ADD | select.KQ_EV_CLEAR,
                                               self.FLAGS)], 0, 0)
            except OSError:
                os.close(fd)
                continue
            self.open[q] = (fd, ident)
            opened = True
        return opened

    def wait(self, timeout):
        timeout = max(0.0, timeout)
        if self.kq is None:
            time.sleep(timeout)
            return
        try:
            if self.sync():   # a new watch saw nothing before it opened: look again now
                timeout = 0.0
            self.kq.control(None, 16, timeout)
        except OSError:
            time.sleep(min(timeout, 0.05))   # never spin on a broken kqueue


seen_mail = listing(NEW, "*") or set()
fired = FIRED.stat().st_mtime if FIRED.exists() else 0
places, next_cursor_look = cursors(), 0.0
waiter = Waiter([NEW, MAILDIR, FIRED, FIRED.parent, CURSORS, STOP.parent])
BACKSTOP = float(ENV("MOUTH_SOUNDS_BACKSTOP") or 0.25)   # s: the longest a look waits, and the cursor look's pace (4 a second, as before); tests raise it to prove the wake
print(f"mouth-sounds: watching {NEW} and {FIRED} and {CURSORS}; settings {SETTINGS}; log {LOG}",
      flush=True)
while not STOP.exists():
    now_mail = listing(NEW, "*")
    now_mail = seen_mail if now_mail is None else now_mail
    for name in sorted(n for n in now_mail - seen_mail if not n.startswith(".")):
        utt, front = mail_line(name)
        landed(utt, front, "maildir")
    seen_mail = now_mail
    try:
        m = FIRED.stat().st_mtime
        if m != fired:
            fired = m
            play("bud", {})
    except OSError:
        pass
    if time.time() >= next_cursor_look:   # 4 looks a second: a cursor moves once per tool batch
        next_cursor_look = time.time() + BACKSTOP
        now_places = cursors()
        for sess, n, by, seqs in scoops(places, now_places):
            play("scoop", {"session": sess[:8], "rows": n, "seq": seqs, "by": by})
        places = now_places
    waiter.wait(min(BACKSTOP, next_cursor_look - time.time()))
print("mouth-sounds: stop file seen, exiting", flush=True)
