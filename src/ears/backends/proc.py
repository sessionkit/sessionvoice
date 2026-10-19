"""ProcessBackend: a backend that is a child process speaking ears events v1.

    child stdin   s16le mono PCM
    child stdout  events, JSON lines (ready ... eof)
    child fd N    (optional, --control-fd N) control, JSON lines: `vad` edges from the Pipeline
    SIGUSR1       commit: finalize what was fed so far, now

Why a process: the heavy model never shares an interpreter (or a crash) with
the audio loop. Nemotron decoding in a THREAD put the audio loop 0.6-0.75 s
behind real time (the GIL; measured 2030-06-09); Apple's Speech framework
TRAPS on some bad input and would take the capture down with it.

Live mode (``live()``): the capture's frame loop must never wait on the child.
A child that stops reading its stdin (a stopped interpreter) would block a plain write
once the 64 KB pipe is full, and with it every heartbeat, STOP check and SIGTERM. In live
mode stdin is non-blocking and ``feed`` never waits:

- frames are kept WHOLE: a write that moves part of a frame keeps the rest, and it goes
  first next time, so the child's int16 stream never goes out of step;
- past ``buffer_s`` of audio waiting, each new frame is dropped whole and counted
  (``dropped_frames``);
- past ``fail_s`` of audio dropped with no byte moved, the stream is declared failed: a
  fatal ``error`` event, which the capture says as ``partials-error`` before falling back
  to chunk partials;
- ``close`` makes sure the child is gone. The capture closes a stream only when it wants
  nothing more from it (the setting changed, the stream failed, or the capture ended), so
  the child is SIGKILLed at once (a stopped child would never exit on stdin EOF), and reaped
  off the caller's thread. A failed child is never left beside the next one.

``ears listen`` and ``ears file`` do not use live mode: there the feed SHOULD wait for the
child, and close lets it finish.
"""
import json, os, queue, signal, subprocess, threading, time
from ..events import Event, error, eof

LIVE_BUFFER_S = 1.0      # audio waiting for the child before whole frames are dropped
LIVE_FAIL_S = 10.0       # audio dropped with no byte moved before the stream is failed


class ProcessBackend:
    name = "process"

    def __init__(self, cmd, env=None, control=False):
        self.t0 = time.monotonic()
        self.ctl_w = None
        kw = {}
        if control:
            r, w = os.pipe()
            os.set_inheritable(r, True)
            kw = dict(pass_fds=(r,))           # the child reads the SAME fd number
            cmd = list(cmd) + ["--control-fd", str(r)]
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env, bufsize=0, **kw)
        if control:
            os.close(r)
            self.ctl_w = os.fdopen(w, "w", buffering=1)
        self.q = queue.Queue()
        self._live = False
        self._pending = bytearray()   # live mode: bytes not yet written; only the head frame is partial
        self._stalled_bytes = 0       # live mode: audio dropped since the last byte moved
        self.dropped_frames = 0
        self.stalled = False          # live mode: declared failed for not reading
        threading.Thread(target=self._read, daemon=True).start()

    def live(self, *, rate=16000, buffer_s=None, fail_s=None):
        """Switch to live mode (see the module docstring); returns self."""
        os.set_blocking(self.p.stdin.fileno(), False)
        self._live = True
        self._cap = int((LIVE_BUFFER_S if buffer_s is None else buffer_s) * rate) * 2
        self._fail = int((LIVE_FAIL_S if fail_s is None else fail_s) * rate) * 2
        return self

    def _read(self):
        saw_eof = False
        for line in self.p.stdout:
            try:
                e = Event.from_json(line)
            except ValueError:
                continue
            saw_eof = saw_eof or e.type == "eof"
            self.q.put(e)
        rc = self.p.wait()
        if not saw_eof:        # died without saying goodbye: say it for it
            self.q.put(error(f"{self.name} backend exited {rc}", fatal=True))
            self.q.put(eof(0.0))

    def feed(self, pcm_bytes):
        if self._live:
            return self._feed_live(bytes(pcm_bytes))
        try:
            self.p.stdin.write(pcm_bytes)
        except (BrokenPipeError, ValueError):
            pass               # the reader reports the death

    def _write_some(self):
        """Live mode: write what the pipe takes now; never wait. False once stdin is gone."""
        while self._pending:
            try:
                n = self.p.stdin.write(self._pending)
            except BlockingIOError as e:      # a buffered writer says it this way
                n = e.characters_written
            except (BrokenPipeError, ValueError, OSError):
                self._pending.clear()
                return False               # the reader reports the death
            if not n:                      # None: EAGAIN on an unbuffered FileIO; nothing moved
                return True
            del self._pending[:n]
            self._stalled_bytes = 0
        return True

    def _feed_live(self, frame):
        if self.stalled or not self._write_some():
            return
        if len(self._pending) + len(frame) > self._cap:
            self.dropped_frames += 1           # the whole frame, never part of one
            self._stalled_bytes += len(frame)
            if self._stalled_bytes >= self._fail:
                self.stalled = True
                self.q.put(error(f"{self.name} not reading its stdin: {self.dropped_frames} frames dropped",
                                 fatal=True))
            return
        self._pending += frame
        self._write_some()

    def on_vad(self, e):
        if self.ctl_w:
            try: self.ctl_w.write(e.to_json() + "\n")
            except (BrokenPipeError, ValueError): pass

    def commit(self):
        try: os.kill(self.p.pid, signal.SIGUSR1)
        except ProcessLookupError: pass

    def close(self):
        for f in (self.p.stdin, self.ctl_w):
            try:
                if f: f.close()
            except (BrokenPipeError, ValueError, OSError):
                pass
        if self._live:
            try:
                self.p.kill()          # SIGKILL now: os.kill does not wait
            except (ProcessLookupError, OSError):
                pass
            threading.Thread(target=self._reap, name="stream-reap", daemon=True).start()

    def _reap(self):
        try:
            self.p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

    def events(self):
        out = []
        while True:
            try: out.append(self.q.get_nowait())
            except queue.Empty: return out
