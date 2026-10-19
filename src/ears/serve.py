"""OpenAI-compatible POST /v1/audio/transcriptions over the Apple helper.

A drop-in for a whisper.cpp server on :2022 (the one VoiceMode points at, for
one), so on macOS 27 a client of that server needs no whisper install and no
code change - only its STT base URL. Stdlib only. Multipart parse via the email package.
"""
import argparse, email.parser, email.policy, json, os, subprocess, tempfile, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from .backends.apple import helper_path

def transcribe(path, locale):
    out = subprocess.run([helper_path(), "file", path], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120,
                         env=dict(os.environ, APPLE_SPEECH_LOCALE=locale))
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or f"helper rc={out.returncode}")
    return json.loads(out.stdout.strip().splitlines()[-1])

def silent_wav(audio: bytes) -> bool:
    """True for a RIFF/WAVE whose data chunk holds no samples (header only). Other formats pass."""
    if audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        return False
    i = 12
    while i + 8 <= len(audio):
        cid, size = audio[i:i + 4], int.from_bytes(audio[i + 4:i + 8], "little")
        if cid == b"data":
            return min(size, len(audio) - i - 8) <= 0
        i += 8 + size + (size & 1)
    return True   # no data chunk at all

class H(BaseHTTPRequestHandler):
    locale = "en-AU"
    def do_GET(self):
        if self.path.rstrip("/") in ("/health", "/v1/models"):
            return self._send(200, {"object": "list", "data": [{"id": "apple-speechtranscriber", "object": "model"}]})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self.path.startswith("/v1/audio/transcriptions"):
            return self._send(404, {"error": "not found"})
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n)
        msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
            b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + body)
        fields, audio = {}, None
        for part in msg.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if name == "file": audio = part.get_payload(decode=True)
            else: fields[name] = part.get_content().strip() if part.get_content_type().startswith("text") else part.get_payload(decode=True).decode()
        if not audio:
            return self._send(400, {"error": "no file"})
        lang = fields.get("language")
        locale = {"en": self.locale}.get(lang, lang) if lang else self.locale
        if silent_wav(audio):
            # A WAV with no samples: the helper HANGS on one (a 78-byte header ran to a 40 s
            # timeout) and would stall the ear that asked. Nothing to hear.
            return self._send(200, {"text": ""} if fields.get("response_format", "json") != "text" else "\n",
                              ctype="application/json" if fields.get("response_format", "json") != "text" else "text/plain")
        with tempfile.NamedTemporaryFile(suffix=".audio") as f:
            f.write(audio); f.flush()
            t0 = time.monotonic()
            try: r = transcribe(f.name, locale)
            except Exception as e: return self._send(500, {"error": str(e)})
        fmt = fields.get("response_format", "json")
        if fmt == "text":
            return self._send(200, r["text"] + "\n", ctype="text/plain")
        payload = {"text": r["text"]}
        if fmt == "verbose_json":
            payload.update(duration=r["audio_s"], language=locale, processing_s=time.monotonic() - t0)
        self._send(200, payload)

    def _send(self, code, obj, ctype="application/json"):
        b = (obj if isinstance(obj, str) else json.dumps(obj)).encode()
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)

    def log_message(self, *a): pass

def main():
    ap = argparse.ArgumentParser(prog="ears-serve", description="Serve OpenAI-compatible POST "
        "/v1/audio/transcriptions over the Apple speech helper.",
        epilog="EARS_SERVE_HOST sets the bind address (default 127.0.0.1, loopback only); set it to "
        "open the port to a network.")
    ap.add_argument("port", metavar="PORT", nargs="?", type=int, default=2023, help="port to listen on (default 2023)")
    port = ap.parse_args().port
    # Loopback by default: anything that can reach the port can transcribe with it. EARS_SERVE_HOST
    # opens it to a network (a Mac mini serving a Linux box), on purpose.
    host = os.environ.get("EARS_SERVE_HOST", "127.0.0.1")
    print(f"ears serve: http://{host}:{port}/v1/audio/transcriptions (helper {helper_path()})", flush=True)
    ThreadingHTTPServer((host, port), H).serve_forever()

if __name__ == "__main__":
    main()
