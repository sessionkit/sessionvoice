"""NemotronBackend: Nemotron 3.5 ASR streaming 0.6B on MLX (mlx-audio >= 0.5.4).

The low-compute always-on option, and the one whose model also runs on Linux
(NeMo), so ears does not splinter from Linux. MEASURED on one Mac (a streaming
eval, 2030-06-09): 2.4-3.5% WER at 1.12 s chunks, a final 25 ms after close,
1.35 GB RSS.

Segmenting: a VAD opens a session at speech onset (with LEAD_IN of pre-roll) and
the session stays open THROUGH short pauses - its cache-aware encoder keeps the
context - and closes after CLOSE_AFTER of quiet, which is when the `final`
comes. Closing at every 0.2 s pause cost a point of WER (2.74 -> 3.66, same
eval), and the user's within-thought gaps are <= 0.98 s (the bot's call data), so
1.0 s keeps thoughts whole and still finalises well inside the 1.6 s turn join.
commit() closes the segment NOW, for a turn layer that has already decided.

Partials replace, never append (events v1): the backend accumulates the
token deltas, so a partial is the whole segment so far. Its last word may
still be growing ("Ameri" -> "Americans"); the next partial replaces it.
No word timings yet: finals carry words=[].
"""
import collections, os, queue, threading, time
import numpy as np
from ..events import Event, ready, error, eof

MODEL = os.environ.get("EARS_NEMOTRON_MODEL", "mlx-community/nemotron-3.5-asr-streaming-0.6b")
RIGHT_CTX = 6          # chunk = (RIGHT_CTX+1) x 80 ms = 560 ms: best preview-at-end (9.4-10.6%) near the best finals
CLOSE_AFTER = 1.0      # s of quiet that ends a segment
LEAD_IN = 0.3          # s of pre-roll fed ahead of the onset

def why_not():
    try:
        import mlx.core  # noqa: F401
        from mlx_audio.stt.models.nemotron_asr import session  # noqa: F401
    except Exception as e:
        return f"mlx-audio with Nemotron not importable ({e.__class__.__name__}: {e}); pip install 'sessionvoice[nemotron]'"
    return None


class NemotronLocal:
    """In-process decoder: what the child runs. Use NemotronBackend (the child)."""
    name = "nemotron"
    BLOCK = 640            # bytes: 20 ms at 16 kHz s16le

    def __init__(self, rate=16000, model=MODEL, right_ctx=RIGHT_CTX, language="en",
                 vad="auto", close_after=CLOSE_AFTER, lead_in=LEAD_IN):
        if rate != 16000:
            raise ValueError("Nemotron wants 16 kHz; resample before feed()")
        from ..vad import open_vad, EnergyVAD
        from mlx_audio.stt.utils import load_model
        self.t0 = time.monotonic()
        self.model = load_model(model)
        self.model.default_att_context_size = [56, right_ctx]
        self.hop = self.model.preprocessor_config.hop_length
        self.rate, self.language, self.close_after = rate, language, close_after
        # Segment on debounced VAD EDGES. The child computes its own with the same
        # detector and debounce as the Pipeline (Silero is deterministic on the same
        # samples, so the decisions match): edges sent over a second pipe raced the
        # audio - an onset could arrive after its speech had gone by (2 s clips came
        # back empty, 2030-06-09). vad="external" (on_vad) remains for callers who
        # can guarantee order.
        from ..vad import VadEdges
        self.vad = None
        self.own_edges = None if vad == "external" else VadEdges(open_vad(vad, rate) or EnergyVAD(rate))
        self.speaking, self.last_voice = False, None
        self.edges = collections.deque()    # external edges not yet reached by OUR audio clock
        self.pre = collections.deque()
        self.pre_n, self.pre_max = 0, int((lead_in + 0.2) * rate)   # + the edge's debounce
        self.s, self.seg, self.text, self.seg_start, self.t = None, 0, "", 0.0, 0.0
        self.inq, self.q = queue.Queue(), queue.Queue()
        self.q.put(ready("nemotron", os.path.basename(model), rate, time.monotonic() - self.t0,
                         chunk_s=round((right_ctx + 1) * 0.08, 2), vad=self.own_edges.vad.name if self.own_edges else "external"))
        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()

    # -- the audio thread: never blocks on MLX
    def feed(self, pcm_bytes):
        self.inq.put(("pcm", bytes(pcm_bytes)))

    def on_vad(self, e):
        """A `vad` edge from the Pipeline, queued in order with the audio."""
        self.inq.put(("vad", e))

    def commit(self):
        self.inq.put("commit")

    def close(self):
        self.inq.put(None)
        self.worker.join()

    def events(self):
        out = []
        while True:
            try: out.append(self.q.get_nowait())
            except queue.Empty: return out

    # -- the decoder thread: all MLX work happens here
    def _now(self):
        return time.monotonic() - self.t0

    def _run(self):
        try:
            while True:
                item = self.inq.get()
                if item is None:
                    break
                if item == "commit":
                    self._close_segment()
                    continue
                kind, x = item
                if kind == "vad":
                    self.edges.append(x)          # applied when our audio reaches x.at
                    continue
                for off in range(0, len(x), self.BLOCK):   # 20 ms steps: edges land between them
                    self._audio(x[off:off + self.BLOCK])
                    if self.s is not None and self.s._queued > self.rate:
                        self._decode()                    # never let the session queue near its 30 s cap
                if self.inq.empty():
                    self._decode()
            self._close_segment()
        except Exception as e:
            self.q.put(error(f"nemotron: {e.__class__.__name__}: {e}", fatal=True))
        self.q.put(eof(self.t, t=self._now()))

    def _audio(self, pcm):
        x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        if self.own_edges:
            self.edges.extend(self.own_edges.feed(pcm))
        self.t += len(x) / self.rate
        # External edges describe AUDIO time, and may arrive before or after that
        # audio does (the child lags the pipeline, by a lot in fast replay). Apply
        # each one when our own clock reaches its `at`, never on arrival: an
        # onset+offset pair that both arrived early would otherwise leave the
        # speech between them with no segment at all.
        while self.edges and self.edges[0].get("at", 0.0) <= self.t:
            e = self.edges.popleft()
            if e.get("speech"):
                self.speaking = True
            else:
                self.speaking, self.last_voice = False, e.get("at")
        if self.s is None:
            self.pre.append(x); self.pre_n += len(x)
            while self.pre and self.pre_n - len(self.pre[0]) >= self.pre_max:
                self.pre_n -= len(self.pre.popleft())
            if not self.speaking:
                return
            self.s = self.model.create_streaming_session(language=self.language)
            self.limit = self.s._encoder.chunk_mel * self.hop
            self.seg, self.text = self.seg + 1, ""
            self.seg_start = max(0.0, self.t - self.pre_n / self.rate)
            self.s.feed(np.concatenate(self.pre)); self.pre.clear(); self.pre_n = 0
            return
        self.s.feed(x)
        last = self.last_voice
        if not self.speaking and last is not None and last > self.seg_start and self.t - last >= self.close_after:
            self._decode()
            self._close_segment()

    def _decode(self):
        if self.s is None:
            return
        before = self.text
        while True:
            d = self.s.step(max_decode_tokens=256)
            if d:
                self.text += "".join(d)
            if not self.s._encoded and self.s._queued < self.limit:
                break
        if self.text.strip() and self.text != before:
            self.q.put(Event("partial", self.text.strip(), self.seg_start, self.t, seg=self.seg, t=self._now()))

    def _close_segment(self):
        if self.s is None:
            return
        self.s.close()
        while not self.s.done:
            self.text += "".join(self.s.step(max_decode_tokens=4096))
        end = self.last_voice if self.last_voice is not None else self.t
        self.q.put(Event("final", " ".join(self.text.split()), self.seg_start, max(self.seg_start, end), [],
                         seg=self.seg, t=self._now()))
        self.s, self.text = None, ""


class NemotronBackend:
    """Nemotron as a child process (see proc.py for why): same events, same
    commit, and the Pipeline's VAD edges on the control pipe."""
    name = "nemotron"

    def __new__(cls, rate=16000, **kw):
        import sys
        from .proc import ProcessBackend
        cmd = [sys.executable, "-m", "ears.backends.nemotron", "--rate", str(rate)]
        for k, v in kw.items():
            cmd += [f"--{k.replace('_', '-')}", str(v)]
        b = ProcessBackend(cmd, env=dict(os.environ, HF_HUB_OFFLINE=os.environ.get("HF_HUB_OFFLINE", "1")),
                           control=False)
        b.name = "nemotron"
        return b


def main(argv=None):
    """python -m ears.backends.nemotron: PCM on stdin -> events v1 on stdout.
    --control-fd N: `vad` edges arrive there (else it runs its own VAD)."""
    import argparse, signal, sys
    ap = argparse.ArgumentParser(prog="ears-nemotron")
    ap.add_argument("--rate", type=int, default=16000)
    ap.add_argument("--control-fd", type=int)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--right-ctx", type=int, default=RIGHT_CTX)
    ap.add_argument("--close-after", type=float, default=CLOSE_AFTER)
    ap.add_argument("--language", default="en")
    a = ap.parse_args(argv)
    b = NemotronLocal(a.rate, a.model, a.right_ctx, a.language, vad="external" if a.control_fd else "auto",
                      close_after=a.close_after)
    signal.signal(signal.SIGUSR1, lambda *_: b.commit())
    done = threading.Event()

    def pump():
        while True:
            for e in b.events():
                sys.stdout.write(e.to_json() + "\n"); sys.stdout.flush()
                if e.type == "eof":
                    done.set(); return
            time.sleep(0.005)
    threading.Thread(target=pump, daemon=True).start()
    if a.control_fd:
        def control():
            for line in os.fdopen(a.control_fd, "r"):
                try: e = Event.from_json(line)
                except ValueError: continue
                if e.type == "vad": b.on_vad(e)
                elif e.type == "commit": b.commit()
        threading.Thread(target=control, daemon=True).start()
    src = sys.stdin.buffer
    while True:
        pcm = src.read1(65536)
        if not pcm:
            break
        b.feed(pcm)
    b.close()
    done.wait(30)


if __name__ == "__main__":
    main()
