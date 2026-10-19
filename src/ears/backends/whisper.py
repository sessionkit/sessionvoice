"""WhisperBackend: any OpenAI-compatible /v1/audio/transcriptions (whisper.cpp
server, or `ears serve` on a Mac) as ears events.

Energy-VAD chunks (speech between 0.35 s pauses, the bot's CHUNK_QUIET) go to the
URL and come back as `final` events. No partials: whisper is batch. The layers
above cannot tell the difference, which is the point.
"""
import io, json, os, queue, threading, time, urllib.request, uuid, wave
from ..events import Event, ready, error, eof

URL = os.environ.get("EARS_WHISPER_URL", "http://127.0.0.1:2022/v1/audio/transcriptions")
CHUNK_QUIET, MIN_SPEECH, LEAD_IN = 0.35, 0.30, 0.30

def _post(url, pcm, rate):
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(pcm)
    bd = uuid.uuid4().hex
    body = (f"--{bd}\r\nContent-Disposition: form-data; name=\"response_format\"\r\n\r\njson\r\n"
            f"--{bd}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"c.wav\"\r\nContent-Type: audio/wav\r\n\r\n").encode() \
        + b.getvalue() + f"\r\n--{bd}--\r\n".encode()
    req = urllib.request.Request(url, body, {"Content-Type": f"multipart/form-data; boundary={bd}"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())["text"].strip()

def why_not(url=URL):
    try:
        urllib.request.urlopen(url.replace("/v1/audio/transcriptions", "/health"), timeout=1)
        return None
    except urllib.error.HTTPError:
        return None            # it answered: something is listening
    except Exception as e:
        return f"nothing at {url} ({e.__class__.__name__})"


class WhisperBackend:
    name = "whisper"

    def __init__(self, rate=16000, url=URL):
        from ..vad import EnergyVAD
        self.rate, self.url, self.vad = rate, url, EnergyVAD(rate)
        self.q, self.buf, self.t, self.quiet, self.voiced, self.start = queue.Queue(), bytearray(), 0.0, 0.0, 0.0, None
        self.t0, self.seg, self.inflight, self.lock = time.monotonic(), 0, [], threading.Lock()
        self.q.put(ready("whisper", url, rate, 0.0))

    def feed(self, pcm):
        dt = len(pcm) / 2 / self.rate
        speaking = self.vad.feed(pcm)
        self.t += dt
        if self.start is None:
            if speaking:
                self.start = max(0.0, self.t - dt - LEAD_IN); self.buf = bytearray(pcm); self.quiet = 0.0; self.voiced = dt
            return
        self.buf += pcm
        if speaking: self.quiet, self.voiced = 0.0, self.voiced + dt
        else: self.quiet += dt
        if self.quiet >= CHUNK_QUIET:
            self._close()

    def _close(self):
        pcm, start, end, voiced = bytes(self.buf), self.start, self.t - self.quiet, self.voiced
        self.start, self.buf = None, bytearray()
        if voiced < MIN_SPEECH:
            return
        self.seg += 1
        seg, prev = self.seg, self.inflight[-1] if self.inflight else None
        def run():
            try:
                text, err = _post(self.url, pcm, self.rate), None
            except Exception as e:
                text, err = "", e
            if prev: prev.join()         # finals leave in segment order
            if err: self.q.put(error(f"whisper: {err}"))
            self.q.put(Event("final", text, start, end, [], seg=seg, t=time.monotonic() - self.t0))
        th = threading.Thread(target=run, daemon=True); self.inflight.append(th); th.start()

    def commit(self):
        """Send the open chunk now instead of waiting for CHUNK_QUIET."""
        if self.start is not None: self._close()

    def close(self):
        if self.start is not None: self._close()
        for th in self.inflight: th.join()
        self.q.put(eof(self.t, t=time.monotonic() - self.t0))

    def events(self):
        out = []
        while True:
            try: out.append(self.q.get_nowait())
            except queue.Empty: return out
