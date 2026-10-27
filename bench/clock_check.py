"""clock_check.py [DELAY] - the bot call leg's tap clock. Run with the bot venv.
Pre-roll test: the first frame arrives DELAY s late (so the leg writes
pre-roll silence); a tone burst sits 1.0 s into the real audio. Pass iff
start_epoch + (burst onset in caller-call.wav) == wall time the burst was delivered."""
import asyncio, fractions, importlib.util, json, os, pathlib, sys, tempfile, time, wave
import numpy as np
os.environ["PIP_CHAT_EARS"] = "1"
os.environ["PIP_CHAT_CALLS_DIR"] = tempfile.mkdtemp(prefix="ears-preroll-")
# EARS_BOT_DIR: the Delta bot checkout that has calls.py (machine-local, no default).
sys.path.insert(0, os.path.expanduser(os.environ.get("EARS_BOT_DIR")
                                      or sys.exit("set EARS_BOT_DIR to the Delta bot checkout with calls.py")))
import calls
# src/ears/callrec.py, by its path: the recording's name (caller-call). Stdlib
# only, so the bot venv this runs in needs nothing from ears.
_spec = importlib.util.spec_from_file_location(
    "callrec", pathlib.Path(__file__).resolve().parents[1] / "src" / "ears" / "callrec.py")
callrec = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(callrec)
from av import AudioFrame
DELAY = float(sys.argv[1]) if len(sys.argv) > 1 else 1.6
calls.setup(lambda m: None, lambda p: "", lambda *a, **k: None)
pcm = np.zeros(48000 * 3, dtype=np.int16)
t = np.arange(4800) / 48000
pcm[48000:48000 + 4800] = (8000 * np.sin(2 * np.pi * 1000 * t)).astype(np.int16)
burst_wall = None
class Track:
    def __init__(s): s.i, s.t0 = 0, None
    async def recv(s):
        global burst_wall
        if s.t0 is None:                    # survives wait_for's cancel: a fixed deadline
            s.t0 = time.monotonic() + DELAY
        await asyncio.sleep(max(0, s.t0 - time.monotonic()))
        if s.i >= len(pcm): raise Exception("track ended")
        await asyncio.sleep(max(0, s.t0 + s.i / 48000 - time.monotonic()))
        x = pcm[s.i:s.i + 960]
        if s.i == 48000: burst_wall = time.time()
        s.i += 960
        f = AudioFrame.from_ndarray(x.reshape(1, -1), format="s16", layout="mono")
        f.sample_rate, f.pts, f.time_base = 48000, s.i, fractions.Fraction(1, 48000)
        return f
async def main():
    s = calls.CallSession.__new__(calls.CallSession)
    calls.CallSession.__init__(s, manager=None, account=None, chat_id=0, offer_sdp="x")
    s.chat_name, s.caller, s.guest_list = "preroll", "Alice", (lambda: [])
    await s._listen_ears(Track())
    d = s.dir
    meta_path, wav_path = callrec.call_file(d, "json"), callrec.call_file(d, "wav")
    for path, ext in ((meta_path, "json"), (wav_path, "wav")):
        if path is None: sys.exit(f"clock_check: no {callrec.name(ext)} in {d}")
    meta = json.loads(meta_path.read_text())
    w = wave.open(str(wav_path)); a = np.frombuffer(w.readframes(w.getnframes()), np.int16)
    onset = int(np.argmax(np.abs(a) > 1000)) / meta["rate"]
    err = meta["start_epoch"] + onset - burst_wall
    print(json.dumps(meta))
    print(f"DELAY {DELAY}s preroll {meta['preroll_s']}s onset {onset:.3f}s in wav; "
          f"mapped-vs-true {err*1000:+.0f} ms; slip {(meta['end_epoch']-meta['start_epoch'])-meta['audio_s']:+.3f}s")
    print("PASS" if abs(err) < 0.06 else "FAIL")
asyncio.run(main())
