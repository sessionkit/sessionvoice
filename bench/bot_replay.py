"""bot_replay.py WAV... - drive the Delta bot's REAL call leg (calls.CallSession
._listen_ears) with a fake WebRTC track: each WAV at 48 kHz in 20 ms frames, at
real-time pace, GAP s of silence between them. Nothing is mailed: mail_in is
captured and printed. Run with the bot's venv, PIP_CHAT_EARS=1.
"""
import asyncio, fractions, os, pathlib, subprocess, sys, tempfile, time
import numpy as np
os.environ.setdefault("PIP_CHAT_EARS", "1")
os.environ.setdefault("PIP_CHAT_CALLS_DIR", tempfile.mkdtemp(prefix="ears-bot-replay-"))
# EARS_BOT_DIR: the Delta bot checkout that has calls.py (machine-local, no default).
sys.path.insert(0, os.path.expanduser(os.environ.get("EARS_BOT_DIR")
                                      or sys.exit("set EARS_BOT_DIR to the Delta bot checkout with calls.py")))
import calls
from av import AudioFrame

GAP = float(os.environ.get("GAP", "3.0"))
mails = []
def mail_in(chat_name, chat_id, who, body, **kw):
    mails.append((time.monotonic(), body, kw)); print(f"  MAIL {'PREVIEW' if 'X-Call-Preview' in (kw.get('headers') or {}) else 'TURN'}: {body[:110]}", flush=True)
calls.setup(lambda m: print(f"{time.monotonic()-T0:7.2f} {m[:170]}", flush=True), lambda p: "", mail_in)

class FakeTrack:
    def __init__(self, pcm48):
        self.pcm, self.i, self.t0 = pcm48, 0, None
    async def recv(self):
        if self.t0 is None: self.t0 = time.monotonic()
        n = 960                                    # 20 ms at 48 kHz
        if self.i >= len(self.pcm): raise Exception("track ended")
        due = self.t0 + self.i / 48000
        await asyncio.sleep(max(0, due - time.monotonic()))
        x = self.pcm[self.i:self.i + n]; self.i += n
        f = AudioFrame.from_ndarray(x.reshape(1, -1), format="s16", layout="mono")
        f.sample_rate, f.pts, f.time_base = 48000, self.i, fractions.Fraction(1, 48000)
        return f

def load48(path):
    raw = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", path, "-ar", "48000", "-ac", "1", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.int16)

async def main(wavs):
    gap = np.zeros(int(GAP * 48000), dtype=np.int16)
    pcm = np.concatenate(sum(([load48(w), gap] for w in wavs), []))
    s = calls.CallSession.__new__(calls.CallSession)
    calls.CallSession.__init__(s, manager=None, account=None, chat_id=0, offer_sdp="x")
    s.chat_name, s.caller = "replay", "Alice"
    s.guest_list = lambda: []
    print(f"audio {len(pcm)/48000:.1f} s, calls dir {calls.CALLS_DIR}", flush=True)
    await s._listen_ears(FakeTrack(pcm))
    await asyncio.sleep(0.5)
    print(f"{len(mails)} mails; turns: {sum(1 for m in mails if 'X-Call-Preview' not in (m[2].get('headers') or {}))}")

T0 = time.monotonic()
asyncio.run(main(sys.argv[1:]))
