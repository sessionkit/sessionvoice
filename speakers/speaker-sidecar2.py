#!/usr/bin/env python3
"""speaker-sidecar2: tag each heard line with WHO spoke it - a named person (bob, alice, ...),
other, mixed or silent.

Speaker diarization tells one person from another (a tester from the
user, say), so ears-mouth-tail can show
who spoke each line, by name.

Same side-car as speaker-sidecar.py: a second reader of the capture's mic,
only while that capture runs, a ring of wall-clock-stamped audio, each heard line's window
cut from the ring by its t0..t1. New here:
  --enroll NAME=WAV (repeat): one reference clip per person, recorded over the SAME mic.
     They are prefixed in order with 0.5 s gaps; Nemotron 3 Diarization numbers speakers by
     arrival, so label i is the i-th reference (checked at start: each clip must come back
     as one speaker, and the clips as different speakers).
  --record-only DIR: no labels, only each line's audio as SEQ.wav + SEQ.json, to pick
     reference clips from lines whose words say who spoke.
  --relabel DIR: label the lines recorded in DIR offline, then exit.
  --device follow (the default): open whatever device the ears keeper's capture is
     on, and move with it. Once the AirPods came out, the keeper moved to
     the MacBook mic, and a side-car pinned to --device airpods went deaf for good;
     speaker identification has to work all the time. The stream
     moves inside this process, so the model is not loaded again. --device NAME still pins.
  --call-enroll NAME=WAV (repeat): references for CALL audio (for the mouth's barge_in
     gate: barge in only on an identified voice). A
     phone mic through Opus is not the room's voice: on one call the room refs named only
     192 of 317 of the user's call partials; a 12 s clip of the user from a call named 289 of 309.
     With these, a heard line from source "call" (device call:N) is cut from that call's
     caller-call.wav at wav_s + t0 .. wav_s + t1, wav_s from the bot's ear-taps.jsonl (the
     audio position where the capture's tap began: the wall clock drifted 2.0 s in 21
     minutes on that call). Its row carries "leg": "call". Without them, call lines get no
     row, as before.
  --call-dirs DIR:DIR: where the call dirs are: a Delta bot's (<stamp>-chat<C>-msg<N>/) and
     sessionphone's web calls (<N>/, N the 10-digit epoch second the call began).
     Default every agent's Delta bot, ~/.*/delta-chat/calls, and sessionphone's web calls,
     <root>/web/calls under each sessionphone root, as the ears find its sockets:
     $SESSIONPHONE_ROOT alone when it is set, else ~/.sessionkit/phone/state and
     ~/.sessionphone, a root reached by both (one a symlink to the other) searched once.
     Both kinds hold the same caller-call.wav and ear-taps.jsonl. A call
     line with no recording gets unheard, its why naming the call dir it looked in, or every
     root it searched when it found no dir.
Out: speakers_YYYY-MM-DD.jsonl beside the heard log (0600), one JSON line per heard line,
keyed by seq, speaker = a name | other | mixed | silent | unheard. The heard readers glob
heard_????-??-??.jsonl, so they never see it.
  unheard: this side-car could not say - no audio from the line's device for (most of) the
     line, or words in the line and no speech in its audio. It carries a "why". A line is
     never skipped without a row, and a line with words is never called silent (measured:
     seq 157865 had words and came out silent as the AirPods died).
"""
import argparse, collections, datetime as dt, importlib.util, json, os, re, subprocess, sys, threading, time, wave
from pathlib import Path
import numpy as np

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


SR = 16000
# The diarizer's pinned snapshot: equal to ears.services.SPEAKER_REVISION (a test holds them).
REVISION = "59ed2dbfc1346dcea9d423c71306a3a2499c568f"
LOGDIR = Path(sessionvoice_home()) / "logs/conversations"
def phone_roots():
    """sessionphone's root(s): a stdlib copy of keeper/phone_roots.py's phone_roots()
    (tests/test_speakers_call.py holds them equal), as this venv imports nothing from ears.
    $SESSIONPHONE_ROOT when it is set, alone; else the new root, ~/.sessionkit/phone/state,
    then the old, ~/.sessionphone, deduped by realpath (for one release the old is a symlink
    to the new: searched once)."""
    explicit = os.environ.get("SESSIONPHONE_ROOT")
    if explicit:
        return [os.path.expanduser(explicit)]
    home, seen, out = os.path.expanduser("~"), set(), []
    for p in (os.path.join(home, ".sessionkit", "phone", "state"), os.path.join(home, ".sessionphone")):
        if os.path.realpath(p) not in seen:
            seen.add(os.path.realpath(p)); out.append(p)
    return out
# every ~/.<agent>/delta-chat bot's calls, no agent named; then sessionphone's web calls
# under each of its roots, as the ears' bot_roots() reads them
CALL_DIRS = ":".join(["~/.*/delta-chat/calls"] + [os.path.join(r, "web", "calls") for r in phone_roots()])
ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
ap.add_argument("--device", default="follow",
                help="follow (the keeper capture's device, moving with it) or a device name to pin")
ap.add_argument("--enroll", action="append", default=[], metavar="NAME=WAV")
ap.add_argument("--enroll-s", type=float, default=12.0, help="seconds used from each reference")
ap.add_argument("--dominant", type=float, default=0.7, help="share of speech frames to name a line")
ap.add_argument("--ring", type=float, default=300.0)
ap.add_argument("--logdir", default=str(LOGDIR))
ap.add_argument("--record-only", metavar="DIR")
ap.add_argument("--relabel", metavar="DIR")
ap.add_argument("--stop-file")
ap.add_argument("--agent-refs", default="", metavar="NAMES",
                help="enrolled names that are AGENT voices (ada): over a mouth line of this "
                     "machine's, they yield to that line's agent like 'other' does")
ap.add_argument("--mouth-tail", type=float, default=1.5, metavar="S",
                help="a heard line up to S s after a mouth line ends can still be its echo")
ap.add_argument("--call-enroll", action="append", default=[], metavar="NAME=WAV",
                help="references for call audio (a phone mic through Opus), one per person")
ap.add_argument("--call-dirs", default=CALL_DIRS, metavar="DIR:DIR",
                help="the call dirs' roots, each holding <stamp>-chat<C>-msg<N>/ (a Delta bot) or <N>/ "
                     "(a sessionphone web call); a DIR may be a glob (default "
                     f"{CALL_DIRS}: every agent's Delta bot and sessionphone's web calls, as the ears "
                     "find their sockets)")
ap.add_argument("--cover", type=float, default=0.5, metavar="F",
                help="share of a line's window the device's audio must cover to name it")
ap.add_argument("--deaf-lines", type=int, default=10, metavar="N",
                help="after N mic lines in a row at 0%% coverage, close the stream, rescan the device "
                     "list and reopen it, at most once a minute (also after a run of covered turns "
                     "with no speech in their audio); 0 turns both reopens off")
a = ap.parse_args()

def say(m): print(f"{time.strftime('%H:%M:%S')} {m}", flush=True)
def epoch(ts): return dt.datetime.fromisoformat(ts).timestamp()
def read_wav(p):
    with wave.open(str(p)) as w:
        assert w.getframerate() == SR and w.getnchannels() == 1, f"{p}: need 16 kHz mono"
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
def write_wav(p, x):
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())
    os.chmod(p, 0o600)
def append(path, rec):
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try: os.write(fd, (json.dumps(rec, ensure_ascii=False) + "\n").encode())
    finally: os.close(fd)

# ---- this machine's mouth: its own lines are named by the agent that said them ----
# The agents' voices are distinctive but showed up as other, so any agent voice that is
# used is named by the agent that said it.
# The mouth writes saying/said rows (agent, utt) into the same heard log, so on the SAME
# machine no voice reference is needed: a heard line inside a playing line, whose words are
# mostly that line's, is the agent's. A named person (bob) talking over it stays that person.
mouth = {}  # utt -> [agent, start, end or None, words]
agent_refs = set(x.strip() for x in a.agent_refs.split(",") if x.strip())
def words(t): return "".join(c.lower() if c.isalnum() else " " for c in str(t or "")).split()
def note_mouth(r):
    k = r.get("kind")
    if k == "saying" and r.get("utt"):
        # the words IN ORDER, so the line's ending is known
        mouth[r["utt"]] = [r.get("agent") or "mouth", epoch(r["ts"]), None, words(r.get("text"))]
        while len(mouth) > 64: mouth.pop(next(iter(mouth)))
        return True
    if k == "said" and r.get("utt") in mouth:
        mouth[r["utt"]][2] = epoch(r["ts"])
        return True
    return k in ("saying", "said")
def mouth_name(who, fr, text, w0, w1, lines, tail=1.5, agents=frozenset()):
    """(who, fracs, utt): an 'other' (or an agent ref's) line over a playing mouth line is
    the line's agent when at least half its HEARD words are the line's; a mixed line keeps
    'mixed' and its 'other' share is renamed, so its lead (bob) still counts for a speaker
    gate.

    A heard line mostly AFTER the mouth line ended (its window's midpoint past the end)
    can only be the line's echo if it is the line's ENDING: an in-order run of heard words
    that is the line's last words, at least half the heard words. A person's "Anything."
    after "... say anything, and I'll confirm I heard you." keeps its voice label.
    A line whose said row was never seen ends at the next line's start (the
    mouth plays one line at a time), else at an estimate: s0 + max(10 s, 0.6 s a word)."""
    def end_of(utt, s0, s1, lw):
        if s1 is not None:
            return s1
        nxt = [v[1] for k, v in lines.items() if k != utt and v[1] > s0]
        return min(nxt) if nxt else s0 + max(10.0, 0.6 * len(lw))

    def ending_run(hw, lw):
        """The longest run hw[i:j] that is lw's last j-i words."""
        n = len(lw)
        for k in range(min(len(hw), n), 0, -1):
            if any(hw[i:i + k] == lw[n - k:] for i in range(len(hw) - k + 1)):
                return k
        return 0

    hw = words(text)
    for utt, (agent, s0, s1, lw) in reversed(list(lines.items())):
        e = end_of(utt, s0, s1, lw)
        if w1 < s0 - 0.5 or w0 > e + tail:
            continue
        if len(hw) < 1:
            continue
        if (w0 + w1) / 2 > e:   # mostly after the line: only its ending can be its echo
            if ending_run(hw, list(lw)) / len(hw) < 0.5:
                continue
        elif sum(w in set(lw) for w in hw) / len(hw) < 0.5:
            continue
        fr = {(agent if k == "other" or k in agents else k): v for k, v in fr.items()}
        if who == "other" or who in agents:
            who = agent
        return who, fr, utt
    return who, fr, None

def named_by_mouth(heard_as, utt, agents=frozenset()):
    """True when mouth_name gave the label (the line's agent, by attribution, not
    by voice): written as speaker_by "mouth". The capture lets ONE named person take a turn
    from such a label (ears.listen.speakers._holders); a voice's label still needs K."""
    return utt is not None and (heard_as == "other" or heard_as in agents)

# ---- the model and the references (not needed to record) ----
model = None
GAP = np.zeros(SR // 2, np.float32)
if not a.record_only:
    import mlx.core as mx
    from mlx_audio.vad import load
    model = load("mlx-community/Nemotron-3-Diarization", strict=True, revision=REVISION)
    model.set_streaming_config("offline")
def probs(x):
    p = np.array(model.generate(x).speaker_probs)
    # each heard line has a new length, so Nemotron's last window and its mel
    # front end get new buffer shapes; MLX keeps every freed Metal buffer in its cache and
    # reuses one only within +32 KB of its size, so the cache grew ~5 GB/hour toward
    # ~24 GB. Hand the freed buffers back after every call, once the result is copied to
    # numpy (labels identical, see test-labels-unchanged.sh).
    mx.clear_cache()
    return p if p.size else np.zeros((0, 8))
def enroll(specs, leg):
    """One set of references -> {"names", "label" (model label -> name), "pre", "frames"}, or
    None for no specs. Refuses (exits) a clip that is not one speaker, or two clips that come
    back as one. The room and the call each get their own set: the prefix a line is labelled
    behind decides which voices it can be."""
    names, refs, LABEL = [], [], {}
    for spec in specs:
        n, w = spec.split("=", 1)
        names.append(n); refs.append(read_wav(w)[: int(a.enroll_s * SR)])
    if not refs:
        return None
    for n, r in zip(names, refs):
        act = (probs(r) > 0.5).sum(0)
        say(f"{leg} reference {n}: {len(r)/SR:.1f}s, frames per label {act.tolist()}")
        if act.sum() == 0 or act.max() / act.sum() < 0.9:
            sys.exit(f"speaker-sidecar2: {leg} reference {n} is not one speaker - refused")
    pre = np.concatenate([np.concatenate([r, GAP]) for r in refs])
    p = probs(pre); edges = np.cumsum([0] + [len(r) + len(GAP) for r in refs]) // 160
    got = [int((p[edges[i]:edges[i] + len(refs[i]) // 160] > 0.5).sum(0).argmax()) for i in range(len(refs))]
    say(f"{leg} references together -> labels {dict(zip(names, got))}")
    # Each reference must get its OWN label; the order may differ (once a 12 s reference
    # beside a Kokoro-voiced one put that one on label 5, not 4, and this refused).
    if len(set(got)) != len(got):
        sys.exit(f"speaker-sidecar2: two {leg} references came back as one label {dict(zip(names, got))} - refused")
    if got != list(range(len(refs))):
        say(f"{leg} references: labels not in arrival order {dict(zip(names, got))} - named by label")
    LABEL.update({g: n for n, g in zip(names, got)})
    return {"names": names, "label": LABEL, "pre": pre, "frames": len(pre) // 160}
ROOM = enroll(a.enroll, "room") if model is not None else None
CALL = enroll(a.call_enroll, "call") if model is not None else None
names = ROOM["names"] if ROOM else []

def label(seg, refset=None):
    rs = refset or ROOM
    t = time.perf_counter()
    p = probs(np.concatenate([rs["pre"], seg]))[rs["frames"]:]
    ms = (time.perf_counter() - t) * 1000
    LABEL = rs["label"]
    act = (p > 0.5).sum(0) if p.size else np.zeros(8, int)
    tot = int(act.sum())
    fr = {}
    for i in range(len(act)):  # voices with no reference are all "other"
        if tot and act[i]:
            k = LABEL.get(i, "other")
            fr[k] = round(fr.get(k, 0) + float(act[i]) / tot, 2)
    if tot < 10: return "silent", fr, tot, ms
    d = int(act.argmax()); share = act[d] / tot
    who = LABEL.get(d, "other") if share >= a.dominant else "mixed"
    return who, fr, tot, ms

SILENT_WHY = "words in the line, no speech found in its audio"   # the keeper keys on this prefix
def unheard_why(who, text, seg):
    """(who, why): a line whose words say someone spoke is never 'silent'. A device that
    hands over only zeros (the AirPods as they died, Sun 2030-06-09) is no signal;
    words with no speech found in the audio is the diarizer missing them. Either: unheard."""
    if seg is not None and len(seg) and not np.abs(seg).max():
        return "unheard", "no signal: the device gave only zeros"
    if who == "silent" and words(text):
        return "unheard", SILENT_WHY
    return who, None

# ---- a stream that is open and active and never calls back ----
# The incident: 27k rows at 0% in 3h19m, ended only by a kickstart. Each mic line cut with
# cover 0 is counted here, on the main thread; at --deaf-lines in a row the mic thread is asked
# to close, rescan and reopen (mic_step), at most once per REOPEN_S.
REOPEN_S = 60.0  # at most one forced reopen (and its log line) per 60 s
deaf = {"n": 0, "dev": None, "due": False}
deaf_lock = threading.Lock()
def note_cover(dev, cover):
    """Count one mic line's cover. Rounded as the row's cover field is, so a line the row calls
    0 is the line counted (the keeper reads cover <= 0 as 0%). Anything above 0, or another
    device, starts the count again."""
    with deaf_lock:
        if dev != deaf["dev"]:
            deaf.update(n=0, dev=dev, due=False)
        if round(float(cover), 2) > 0:
            deaf.update(n=0, due=False)
            return
        deaf["n"] += 1
        if 0 < a.deaf_lines <= deaf["n"]:
            deaf["due"] = True
def deaf_due(dev):
    """The count of 0% lines on DEV if a reopen is due, else 0. Clears the request and the
    count: rows already queued behind a reopen start a new count."""
    with deaf_lock:
        if not deaf["due"] or not same_dev(deaf["dev"], dev):
            return 0
        n = deaf["n"]; deaf.update(n=0, due=False)
        return n
# ---- a stream that is covered and still hears nobody ----
# The other deaf: blocks arrive, the line is covered, and the audio holds no speech while the
# capture heard words in it. A cover count cannot see that; this one counts it. Evidence is
# narrow on purpose: a mic TURN (one per utterance; partials repeat and flap) of at least
# SILENT_MIN_WORDS words (the diarizer can miss a one-word "Short."), cut at --cover or more,
# whose why is SILENT_WHY (the diarizer found under 10 frames of speech), and not the mouth's
# own words (mouth_utt: an echo of the machine's voice is not the person). No words in the line
# is no evidence, so a quiet room never counts. A turn where speech WAS found ends the run; a
# line that is no evidence either way (a partial, 0%, zeros, one word) leaves it as it is.
SILENT_TURNS = 3      # three spoken turns in a row unheard: a person talking, not one miss
SILENT_MIN_WORDS = 2
silent = {"n": 0, "dev": None, "due": False}
def note_speech(dev, kind, text, heard_as, why, utt):
    """Count one covered mic line by what the diarizer found (HEARD_AS, before the mouth's
    renaming) against what the capture heard (TEXT). Shares deaf_lock with note_cover."""
    with deaf_lock:
        if dev != silent["dev"]:
            silent.update(n=0, dev=dev, due=False)
        if heard_as not in ("unheard", "silent"):
            silent.update(n=0, due=False)
            return
        if kind != "turn" or utt or why != SILENT_WHY or len(words(text)) < SILENT_MIN_WORDS:
            return
        silent["n"] += 1
        if a.deaf_lines > 0 and SILENT_TURNS <= silent["n"]:   # --deaf-lines 0: no reopens
            silent["due"] = True
def silent_due(dev):
    """The count of covered turns with no speech on DEV if a reopen is due, else 0. Clears
    the request and the count, as deaf_due does."""
    with deaf_lock:
        if not silent["due"] or not same_dev(silent["dev"], dev):
            return 0
        n = silent["n"]; silent.update(n=0, due=False)
        return n
def deaf_reset():
    with deaf_lock: deaf.update(n=0, due=False); silent.update(n=0, due=False)

def record(r, seg, w0, w1, day, cover=None, why=None, leg=None):
    # a mic line with a window: count it, in every mode (a call leg's row has its own recording)
    if leg is None and cover is not None:
        note_cover(r.get("device"), cover)
    rec = {"seq": r["seq"], "kind": r["kind"], "speaker": None, "session": r.get("session"),
           "t0": r.get("t0"), "t1": r.get("t1"), "text": r.get("text", ""), "ts": r["ts"]}
    if a.record_only and seg is None:
        say(f"{r['seq']} {r['kind']:7s} not recorded: {why or 'no audio'}  {rec['text'][:50]}")
        return
    if a.record_only:
        d = Path(a.record_only); d.mkdir(mode=0o700, exist_ok=True)
        write_wav(d / f"{r['seq']}.wav", seg); (d / f"{r['seq']}.json").write_text(json.dumps(rec))
        say(f"{r['seq']} {r['kind']:7s} rec {len(seg)/SR:4.1f}s  {rec['text'][:70]}")
        return
    if seg is None or not len(seg):
        who, fr, frames, ms, utt, by_mouth = "unheard", {}, 0, 0.0, None, False
        why = why or f"no audio from {r.get('device')!r}"
    else:
        who, fr, frames, ms = label(seg, CALL if leg == "call" else None)
        who, why = unheard_why(who, r.get("text", ""), seg)
        heard_as = who
        who, fr, utt = mouth_name(who, fr, r.get("text", ""), w0, w1, mouth, a.mouth_tail, agent_refs)
        by_mouth = named_by_mouth(heard_as, utt, agent_refs)
        if leg is None and cover is not None:   # a covered mic line: did its audio hold speech
            note_speech(r.get("device"), r.get("kind"), r.get("text", ""), heard_as, why, utt)
    if why and cover is not None:
        why += f" ({cover:.0%} of the line covered)"
    append(Path(a.logdir) / f"speakers_{day}.jsonl",
           {"seq": r["seq"], "kind": r["kind"], "speaker": who, "fracs": fr, "speech_s": round(frames / 100, 2),
            "session": r.get("session"), "device": r.get("device"), "t0": r.get("t0"), "t1": r.get("t1"), "ms": round(ms, 1),
            "lag_s": round(time.time() - w1, 2), "refs": CALL["names"] if leg == "call" and CALL else names,
            "by": "speaker-sidecar2",
            "at": dt.datetime.now().astimezone().isoformat(timespec="milliseconds"), "v": 2,
            **({"leg": leg} if leg else {}),
            # the line's covered share, 0..1, whenever it is known: the keeper reads it, not why
            **({"cover": round(float(cover), 2)} if cover is not None else {}),
            **({"why": why} if why else {}), **({"mouth_utt": utt} if utt else {}),
            **({"speaker_by": "mouth"} if by_mouth else {})})
    say(f"{r['seq']} {r['kind']:7s} {who:6s} {fr}  {r.get('text', '')[:60]}" + (f"  [{why}]" if why else ""))

if a.relabel:
    for j in sorted((q for q in Path(a.relabel).glob("*.json") if q.stem.isdigit()), key=lambda p: int(p.stem)):
        r = json.loads(j.read_text())
        record(r, read_wav(j.with_suffix(".wav")), 0.0, time.time(), r["ts"][:10])
    sys.exit(0)

# ---- the mic: a second reader, only while the capture it labels runs ----
KEEPER_SESSION = "ears-keeper"
CAPTURE = re.compile(r"(?:voice_mode|ears)\.listen capture\b")
def virtual(dev): return "blackhole" in str(dev or "").lower()  # a call tap, never a room mic
def keeper_device(ps_text):
    """The --device of the ears keeper's capture, from `ps -axo command` text; None if no
    keeper capture runs on a real mic. A BlackHole capture is a call tap: skipped.
    The name may hold spaces ("MacBook Pro Microphone"): it ends at the next --option."""
    for line in ps_text.splitlines():
        if not CAPTURE.search(line) or f"--session {KEEPER_SESSION}" not in line:
            continue
        m = re.search(r"--device (.+?)(?= --[a-z]|$)", line)
        if m and not virtual(m.group(1)):
            return m.group(1).strip()
    return None
def same_dev(x, y):
    """A heard line's device and a capture's: one may be a substring of the other
    (--device airpods opens "Ada's AirPods Pro")."""
    x, y = str(x or "").lower(), str(y or "").lower()
    return bool(x and y) and (x in y or y in x)
def ps_text():
    return subprocess.run(["ps", "-axo", "command"], capture_output=True, text=True).stdout
def target_device():
    """The device to listen on now, or None (no capture to label)."""
    if a.device.lower() == "follow":
        return keeper_device(ps_text())
    pinned = re.compile(rf"(voice_mode|ears)\.listen capture.*--device {re.escape(a.device)}", re.I)
    return a.device if any(pinned.search(l) for l in ps_text().splitlines()) else None
class Ring:
    """Wall-clock-stamped 20 ms blocks, each tagged with the device it came from, so a line
    is cut only from its own device's audio, and a gap (the device away) is a gap. A block's
    stamp is when its audio was taken (block_start), not when it arrived."""
    def __init__(self, keep): self.keep, self.blocks, self.lock = keep, collections.deque(), threading.Lock()
    def add(self, t_end, x, dev):
        with self.lock:
            self.blocks.append((t_end - len(x) / SR, x, dev))
            while self.blocks and self.blocks[0][0] < t_end - self.keep: self.blocks.popleft()
    def span(self, dev):
        with self.lock:
            mine = [(t, x) for t, x, d in self.blocks if same_dev(d, dev)]
        if not mine: return None, None
        return mine[0][0], mine[-1][0] + len(mine[-1][1]) / SR
    def cut(self, w0, w1, dev):
        """(audio, covered share of w0..w1): only dev's blocks, each sliced on its own stamp.
        The share is of the window's span, not of samples found: where blocks claim the same
        moment (a burst after a stall, stamped on arrival, piles onto one moment), that
        moment is cut once, from the block stamped first. Audio from the wrong moment does
        not count as cover, so a stalled stream's line reads as the gap it is."""
        with self.lock:
            parts = [(t, x) for t, x, d in self.blocks if same_dev(d, dev) and t + len(x) / SR > w0 and t < w1]
        out, edge = [], w0
        for t, x in sorted(parts, key=lambda p: p[0]):
            i0 = max(0, int(round((max(w0, edge) - t) * SR))); i1 = min(len(x), int(round((w1 - t) * SR)))
            if i1 > i0: out.append(x[i0:i1]); edge = t + i1 / SR
        if not out: return np.zeros(0, np.float32), 0.0
        seg = np.concatenate(out).astype(np.float32) / 32768
        return seg, min(1.0, len(seg) / SR / max(w1 - w0, 1e-6))
ring = Ring(a.ring)
RESCAN_S = 5.0  # at most one PortAudio rescan per 5 s while a device will not open
def close_stream(st, why):
    """Close the open stream. stop() and close() each get their own try: a device that
    has gone raises in stop(), and close() must still run, or the stream leaks."""
    for verb in ("stop", "close"):
        try: getattr(st["stream"], verb)()
        except Exception as exc:  # noqa: BLE001 - the device may already be gone
            say(f"mic: {verb} {st['dev']!r} raised {exc}")
    say(f"mic: closed {st['dev']!r} - {why}"); st["stream"], st["dev"] = None, None
def rescan(sd):
    """Throw away PortAudio's device list and read it again. PortAudio reads it once, at
    start: AirPods put in after that are invisible, and a reconnect can leave a stale entry
    that fails -9986 (shadow.py find_input, ears-sounds.py reinit, mouth/output.py)."""
    sd._terminate(); sd._initialize()
# ---- block timing: when a block's audio was taken, and the stalls between blocks ----
# A Bluetooth input can stall and then deliver what it held in a burst. Stamped on arrival,
# the burst's blocks all land on one moment, and a line's window holds audio from another
# moment. So each block is stamped by the stream's own clock, and the stalls are logged
# (a rate-limited line from the mic thread, never from the audio callback).
GAP_S = 0.25        # a wait between blocks over a block's length plus this, or a block this
                    # much later than the stream's least delay, or this much audio missing,
                    # is a stall
GAP_LOG_S = 10.0    # at most one stall line per 10 s; it counts what it held back
STAMP_MAX_S = 10.0  # a stream clock that puts a block in the future, or older than this, is
                    # not trusted for it: that block is stamped on arrival
gaps = {}           # dev -> the stalls since the last line
gaps_lock = threading.Lock()
gaps_said = {"t": None}
def block_start(ti, now, dur):
    """(wall time the block's first sample was taken, "stream" or "arrival"). PortAudio
    gives the block's time on the stream's clock (inputBufferAdcTime) and what that clock
    reads now (currentTime), so the block began now - (currentTime - inputBufferAdcTime),
    however late it arrives. With no stream clock (no time_info, or 0, which some hosts
    give), or one out of range, it is stamped on arrival: it ends now."""
    try:
        adc, cur = float(ti.inputBufferAdcTime), float(ti.currentTime)
    except (AttributeError, TypeError, ValueError):
        adc = cur = 0.0
    if adc > 0 and 0.0 <= cur - adc <= STAMP_MAX_S:
        return now - (cur - adc), "stream"
    return now - dur, "arrival"
def note_gap(dev, clk, now, t0, dur, how):
    """Count a block that stalled, from the audio callback: no I/O here. CLK is the stream's
    own last arrival, last end and least delay. A stall is a block that came more than GAP_S
    after the last one was due (wait), or more than GAP_S later after its own audio ended
    than the stream's least delay so far (late: a Bluetooth input's steady latency is not a
    stall), or one whose stream clock starts more than GAP_S after the last block ended
    (lost audio)."""
    wait = None if clk["arr"] is None else now - clk["arr"]
    delay = now - (t0 + dur)
    if how == "stream":   # an arrival stamp shows no delay: it must not set the least delay
        clk["base"] = delay if clk["base"] is None else min(clk["base"], delay)
    late = delay - clk["base"] if how == "stream" else 0.0
    lost = max(0.0, t0 - clk["end"]) if clk["end"] is not None and how == "stream" else 0.0
    clk["arr"], clk["end"] = now, t0 + dur
    if (wait is None or wait <= dur + GAP_S) and late <= GAP_S and lost <= GAP_S:
        return
    with gaps_lock:
        g = gaps.setdefault(dev, {"blocks": 0, "wait": 0.0, "late": 0.0, "lost": 0.0})
        g["blocks"] += 1; g["wait"] = max(g["wait"], wait or 0.0); g["late"] = max(g["late"], late)
        if lost > GAP_S: g["lost"] += lost
        g["stamps"] = how
def gap_report(now):
    """The mic thread's line for the stalls noted since the last one, at most once per
    GAP_LOG_S: blocks that stalled, the longest wait between two blocks, the latest a block
    came after its audio ended beyond the stream's least delay, the seconds of audio the
    stream clock says are missing, and whether the stamps are the stream's clock or arrival
    (arrival: the late and lost figures cannot be seen). A steady stream says nothing."""
    with gaps_lock:
        if not gaps or (gaps_said["t"] is not None and now - gaps_said["t"] < GAP_LOG_S):
            return
        held = dict(gaps); gaps.clear(); gaps_said["t"] = now
    for dev, g in held.items():
        say(f"mic: stalls on {dev!r}: blocks={g['blocks']} wait_max_s={g['wait']:.2f} "
            f"late_max_s={g['late']:.2f} lost_s={g['lost']:.2f} stamps={g['stamps']}")
def try_open(sd, want):
    """An open, started stream on the first input whose name holds WANT, or None.
    It opens at the device's own default rate first and resamples each block to SR in the
    callback; SR is only the fallback when the default will not open. A 16 kHz stream asked
    of a device that runs at another rate (AirPods' input runs at 24000) can open, deliver
    blocks on time and carry no speech, so the side-car hears nobody while the capture does."""
    idx = next((i for i, d in enumerate(sd.query_devices())
                if d["max_input_channels"] > 0 and want.lower() in d["name"].lower()), None)
    if idx is None:
        say(f"mic: no input matching {want!r}"); return None
    dflt = int(sd.query_devices(idx, kind="input")["default_samplerate"])
    for rate in dict.fromkeys((dflt, SR)):   # one try when the default is SR
        clk = {"arr": None, "end": None, "base": None}   # this stream's own timing
        def cb(indata, frames, ti, status, rate=rate, dev=want, clk=clk):
            now, x = time.time(), indata[:, 0].copy()
            t0, how = block_start(ti, now, len(x) / rate)
            note_gap(dev, clk, now, t0, len(x) / rate, how)
            if rate != SR:
                n = int(len(x) * SR / rate)
                x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.int16)
            ring.add(t0 + len(x) / SR, x, dev)
        try:
            stream = sd.InputStream(samplerate=rate, channels=1, dtype="int16",
                                    blocksize=int(rate * 0.02), device=idx, callback=cb)
            stream.start()
        except Exception as exc:  # noqa: BLE001 - PortAudio -9986 from a stale list
            say(f"mic: {want!r} at {rate} Hz failed: {exc}"); continue
        say(f"mic: open {want!r} at {rate} Hz"); return stream
    return None
def mic_step(sd, st, now):
    """One look: close a stream whose capture moved, stopped or died; open the capture's
    device if none is open, rescanning PortAudio's list once when it will not open.
    A stream that is open and active but whose lines all come out at 0% (--deaf-lines in a
    row), or whose covered turns hold no speech while the capture heard words (SILENT_TURNS
    in a row), is closed, the list rescanned at once and the device reopened: the same
    name, the stream 'active', and still deaf. At most once per REOPEN_S. Each look first
    says what stalls the callback noted (gap_report)."""
    gap_report(now)
    try: want = target_device()
    except Exception as exc:  # noqa: BLE001 - ps failing must not close a good stream
        say(f"mic: cannot read the capture's device: {exc}"); want = st["dev"]
    if st["stream"] is not None and want != st["dev"]:
        close_stream(st, f"the capture moved to {want!r}" if want else "the capture stopped")
    elif st["stream"] is not None and not st["stream"].active:
        close_stream(st, "the stream died")
    elif st["stream"] is not None and now - st.get("reopened", -REOPEN_S) >= REOPEN_S \
            and ((n := deaf_due(st["dev"])) or (q := silent_due(st["dev"]))):
        st["reopened"] = st["rescanned"] = now   # the rescan here stands for RESCAN_S's too
        why = f"{n} lines at 0% coverage" if n else f"{q} covered turns with no speech"
        say(f"mic: {why}: reopening {st['dev']!r}")
        close_stream(st, why)   # close first: rescan must not run under a live stream
        say("mic: rescanning the device list"); rescan(sd)
    if not want or st["stream"] is not None:
        return
    stream = try_open(sd, want)
    if stream is None and now - st.get("rescanned", -RESCAN_S) >= RESCAN_S:
        st["rescanned"] = now
        say("mic: rescanning the device list"); rescan(sd)
        stream = try_open(sd, want)
    if stream is not None:
        st["stream"], st["dev"] = stream, want
        deaf_reset()   # a new stream starts its own count
def mic_thread():
    """Never ends: an error in one look (a device leaving mid-stream, PortAudio -9986) is
    logged and the next look tries again, or the side-car is deaf while it looks alive."""
    import sounddevice as sd
    st = {"stream": None, "dev": None}
    while True:
        try:
            mic_step(sd, st, time.monotonic())
        except Exception as exc:  # noqa: BLE001
            say(f"mic: {exc!r}; retrying")
            if st["stream"] is not None: close_stream(st, "after an error")
        time.sleep(1)  # a moved capture is reopened within ~1 s, before the user's first line ends
threading.Thread(target=mic_thread, daemon=True).start()

# ---- the call leg: a call line is cut from the bot's own recording of the call ----
# The capture of call:N hears the bot's ear tap, and the bot writes the same frames to
# caller-call.wav. Its t0/t1 are seconds into the tap, so wav second = wav_s + t0, where
# wav_s is where the tap began (the bot's ear-taps.jsonl). No ring and no wall clock: on one
# call the wall clock drifted 2.0 s from the capture's audio in 21 minutes.
CALL_ID = re.compile(r"call:(\d+)")
def _load_callrec():
    """src/ears/callrec.py, by its path: the one place the recording's name is spelt
    (caller-call). Stdlib only, so this venv still needs nothing from ears. Only a
    call leg needs it: a copy run outside the repo with no --call-enroll never looks for it.
    From a wheel this file is ears/_speakers/speaker-sidecar2.py, so the helper is
    ears/callrec.py, one up: the second candidate. In a checkout the first still wins."""
    up = Path(__file__).resolve().parents[1]
    tried = (up / "src" / "ears" / "callrec.py", up / "callrec.py")
    path = next((p for p in tried if p.is_file()), None)
    if path is None:
        sys.exit(f"speaker-sidecar2: the call leg needs {tried[0]} or {tried[1]} "
                 "(run it from a sessionvoice checkout or its installed package)")
    spec = importlib.util.spec_from_file_location("callrec", path)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod
callrec = _load_callrec() if CALL else None
def call_dir(n, roots):
    """The newest dir for call N under any root, or None: a Delta bot's <stamp>-chat<C>-msg<N>,
    or a sessionphone web call's dir named exactly <N> (web ids are 10 digits, Delta's
    4-5, so the two never name one call). A root may be a glob (~/.*/delta-chat/calls); an
    empty one is skipped, never the cwd."""
    found = []
    for root in roots:
        if not root: continue
        r = Path(root).expanduser()
        try:
            bases = (Path(r.anchor).glob(str(r.relative_to(r.anchor))) if r.is_absolute()
                     and any(c in str(r) for c in "*?[") else [r])
            for b in bases: found += [d for d in (*b.glob(f"*-msg{n}"), b / n) if d.is_dir()]
        except (OSError, ValueError): pass
    return max(found, key=lambda d: d.name) if found else None
def tap_offset(rows, listen_t, rec_t):
    """wav_s of the tap a capture listens on: the tap that began nearest the capture's
    listen-started (within 10 s), else the newest that began before the line. None: no row."""
    starts = [r for r in rows if "wav_s" in r and "t" in r]
    if listen_t is not None:
        near = [r for r in starts if abs(r["t"] - listen_t) <= 10]
        if near: return float(min(near, key=lambda r: abs(r["t"] - listen_t))["wav_s"])
    before = [r for r in starts if r["t"] <= rec_t]
    return float(max(before, key=lambda r: r["t"])["wav_s"]) if before else None
def read_taps(d):
    rows = []
    try: text = (Path(d) / "ear-taps.jsonl").read_text()
    except OSError: return rows
    for line in text.splitlines():
        try: rows.append(json.loads(line))
        except ValueError: pass
    return rows
def wav_data_at(path):
    """Byte offset of a wav's sample data (its header may still say 0 frames: the bot closes it
    only when the call ends)."""
    with open(path, "rb") as f: head = f.read(4096)
    i = head.find(b"data")
    return i + 8 if i >= 0 else 44
def cut_wav(path, s0, s1):
    """(16 kHz audio of seconds s0..s1, covered share): only what is on disk so far."""
    at = wav_data_at(path)
    i0, i1 = max(0, int(round(s0 * SR))), max(0, int(round(s1 * SR)))
    with open(path, "rb") as f:
        f.seek(at + 2 * i0); raw = f.read(2 * (i1 - i0))
    seg = np.frombuffer(raw[: len(raw) // 2 * 2], np.int16).astype(np.float32) / 32768
    return seg, min(1.0, len(seg) / max(i1 - i0, 1))
def take_call(r, day):
    """A call line: queued with its call's wav and its window in it, or its unheard row now."""
    m = CALL_ID.fullmatch(str(r.get("device") or ""))
    now = time.time()
    if not m or r.get("t0") is None or r.get("t1") is None:
        record(r, None, now, now, day, why="a call line with no call id or no t0/t1", leg="call"); return
    n, roots = m.group(1), a.call_dirs.split(":")
    d = call_dir(n, roots)
    wav = callrec.call_file(d, "wav") if d is not None else None
    if wav is None:   # the why names where it looked, so the next miss explains itself
        where = (f"in {d}" if d is not None else f"(no dir *-msg{n} or {n}/ under "
                 + ":".join(str(Path(x).expanduser()) for x in roots if x) + ")")
        record(r, None, now, now, day, why=f"no {callrec.name('wav')} for call {n} {where}",
               leg="call"); return
    off = tap_offset(read_taps(d), listen_t.get((r.get("session"), r.get("device"))), epoch(r["ts"]))
    if off is None:
        record(r, None, now, now, day, why=f"no ear-taps.jsonl row in {d.name} (a bot without the tap offset)",
               leg="call"); return
    call_backlog.append((r, wav, off + float(r["t0"]), off + float(r["t1"]), now))
def drain_call(now, day):
    """Label the queued call lines whose audio is on disk; a line waits up to 5 s for it."""
    while call_backlog:
        r, wav, s0, s1, queued = call_backlog[0]
        try: have = (wav.stat().st_size - wav_data_at(wav)) / 2 / SR
        except OSError: have = 0.0
        if have < s1 and now - queued < 5: return
        call_backlog.popleft()
        try: seg, cover = cut_wav(wav, s0, s1)
        except OSError as exc:
            record(r, None, now, now, day, why=f"cannot read {wav.name}: {exc}", leg="call"); continue
        w1 = epoch(r["ts"]); w0 = w1 - (s1 - s0)   # for the mouth's lines, on the wall clock
        record(r, seg if cover >= a.cover else None, w0, w1, day, cover, leg="call")

# ---- follow the heard log ----
starts, backlog, listen_t, call_backlog = {}, collections.deque(), {}, collections.deque()
def note_start(r):
    """A capture stream's wall-clock start, keyed (session, device): every line's t0/t1 is
    seconds into its stream. listen-started gives it at once, so a new device's first line
    is labelled (it runs 0.06-0.3 s early, measured over 12 starts on 2030-06-09); a
    heartbeat (every 15 s) corrects it."""
    if not r.get("session"): return False
    if r.get("event") == "heartbeat" and "stream_s" in r:
        starts[(r["session"], r.get("device"))] = epoch(r["ts"]) - float(r["stream_s"]); return True
    if r.get("event") == "listen-started":
        starts[(r["session"], r.get("device"))] = listen_t[(r["session"], r.get("device"))] = epoch(r["ts"])
        return True
    return False
def open_today():
    day = dt.date.today().isoformat(); path = Path(a.logdir) / f"heard_{day}.jsonl"
    while not path.exists():
        # STOP is honoured while it waits for the day's first line too (an enrol
        # recording stops by its stop file, never a kill, even before the keeper has written)
        if a.stop_file and Path(a.stop_file).exists(): say("stop file: exiting"); sys.exit(0)
        time.sleep(2)
    f = open(path)
    for line in f:
        try: r = json.loads(line)
        except ValueError: continue
        note_start(r)
    return day, f
def take(r, day):
    """One heard-log record: a stream start, a mouth row, or a mic line to label. A mic
    line on a real device is queued with its wall-clock window, or, if its stream start
    is unknown, gets its unheard row now. Never skipped without a row."""
    if note_start(r) or note_mouth(r): return
    if r.get("kind") in ("partial", "turn") and r.get("source") == "call" and CALL:
        take_call(r, day); return
    if r.get("kind") not in ("partial", "turn") or r.get("source") != "mic": return
    if virtual(r.get("device")): return  # a call tap: not this mic
    if a.device.lower() != "follow" and not same_dev(r.get("device"), a.device): return
    s = starts.get((r.get("session"), r.get("device")))
    if s is None or r.get("t0") is None:
        now = time.time()
        record(r, None, now, now, day, why="no stream start for its capture yet (no listen-started or heartbeat)")
        return
    backlog.append((r, s + float(r["t0"]), s + float(r["t1"])))
def drain(now, day):
    """Label the queued lines whose audio is in. A line waits (up to 5 s after it ends)
    while its device's audio has not reached its end; a line whose window is in the
    future has a wrong stream start and is answered at once, so it blocks nobody."""
    while backlog:
        r, w0, w1 = backlog[0]
        if w1 > now + 5:  # a bad stream start: never trust it for a name
            backlog.popleft()
            record(r, None, now, now, day, why="the line's window is in the future: its stream start is wrong")
            continue
        r0, r1 = ring.span(r.get("device"))
        # its audio is still coming, or its device is still opening (a move): wait up to 5 s
        if (r1 is None or r1 < w1 + 0.1) and now - w1 < 5: return
        backlog.popleft()
        seg, cover = ring.cut(w0, w1, r.get("device"))
        record(r, seg if cover >= a.cover else None, w0, w1, day, cover)
day, f = open_today()
say(f"following {f.name}; mode: {'record-only -> ' + a.record_only if a.record_only else 'label ' + ','.join(names) + ('; call ' + ','.join(CALL['names']) if CALL else '')}")
while not (a.stop_file and Path(a.stop_file).exists()):
    line = f.readline()
    if not line:
        if dt.date.today().isoformat() != day: f.close(); day, f = open_today()
        drain(time.time(), day); drain_call(time.time(), day)
        time.sleep(0.2); continue
    try: r = json.loads(line)
    except ValueError: continue
    take(r, day)
