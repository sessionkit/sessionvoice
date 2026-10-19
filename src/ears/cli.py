"""ears command line. Events out are JSON lines (docs/events.md).

  ears listen  [--backend B] [--rate HZ] [--vad V] [--turns T] [--previews]
               s16le mono PCM on stdin -> events on stdout
  ears file    WAV [--realtime] [--tail S] [same options]
  ears backends                        which backends run here, and why not
  ears install [--apple] [--locale L]  fetch Silero; --apple also builds the Mac helper (swift,
                                       macOS 27) and fetches Apple's speech assets
  ears serve   [PORT]                  OpenAI-compatible /v1/audio/transcriptions on the Apple helper
  ears eval    FILE... | --call DIR     every STT engine on the same audio; WER with a reference
  ears wake    add SEAT [--names 'a|b'] [--hint NAME] | list
                                       the seats "hey NAME" wakes, in ~/.sessionkit/voice/settings/wake.json;
                                       their names also prime the STT
  ears speakers enrol | record | clusters DIR | name DIR L0=NAME ...
                                       a reference per person for the speaker side-car
"""
import argparse, json, os, shutil, subprocess, sys, time
from .backends import open_backend, available, NoBackend
from .events import Event
from .vad import (open_vad, VadEdges, silero_model_path, silero_pin_check, fetch_silero,
                  SileroFetchError, SILERO_TAG, SILERO_URL)
from .turns import open_turns

BLOCK = 0.02           # s per feed: the bot's call leg uses 20 ms frames

def out(e):
    sys.stdout.write((e.to_json() if isinstance(e, Event) else json.dumps(e)) + "\n")
    sys.stdout.flush()


class Pipeline:
    """backend -> vad edges -> turn stage, as one event stream, in-process.

    One VAD, here: its edges go to the stream, to the turn stage, and to a
    backend that segments on them (Nemotron's on_vad). The turn stage also sees
    every PCM block the backend sees (Smart Turn and re-decode need audio)."""

    def __init__(self, backend="auto", rate=16000, vad="auto", turns="none", previews=False, emit=out, **kw):
        from .backends import pick
        self.rate, self.emit, self.t0, self.fed = rate, emit, time.monotonic(), 0
        v = open_vad(vad, rate)
        self.edges = VadEdges(v) if v else None
        self.b = open_backend(pick(backend), rate=rate, **kw)
        self.stage = open_turns(turns, previews, have_vad=v is not None, commit=getattr(self.b, "commit", None))

    def audio_now(self):
        return self.fed / 2 / self.rate

    def wall(self):
        return time.monotonic() - self.t0

    def _out(self, evs):
        for e in evs:
            e.t = self.wall()          # t = when THIS stream emitted it (children's clocks start elsewhere)
            self.emit(e)
            if self.stage and e.type not in ("turn", "preview"):
                self._out(self.stage.event(e))

    def feed(self, pcm):
        self.b.feed(pcm)
        self.fed += len(pcm)
        if self.stage:
            self.stage.pcm(pcm, self.audio_now())
        if self.edges:
            evs = self.edges.feed(pcm, t=self.wall())
            if evs and hasattr(self.b, "on_vad"):
                for e in evs: self.b.on_vad(e)
            self._out(evs)
        self.pump()

    def pump(self):
        self._out(self.b.events())
        if self.stage:
            self._out(self.stage.tick(self.audio_now()))

    def close(self):
        if self.edges:
            evs = self.edges.flush(t=self.wall())
            if evs and hasattr(self.b, "on_vad"):
                for e in evs: self.b.on_vad(e)
            self._out(evs)
        self.b.close()
        deadline, done = time.monotonic() + 30, False
        while time.monotonic() < deadline and not done:
            evs = self.b.events()
            done = any(e.type == "eof" for e in evs)
            eofs = [e for e in evs if e.type == "eof"]
            self._out([e for e in evs if e.type != "eof"])
            if done:
                if self.stage:
                    self._out(self.stage.flush(self.audio_now()))
                self._out(eofs)
            else:
                time.sleep(0.02)


def decode(path, rate):
    return subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", path, "-ar", str(rate), "-ac", "1",
                           "-f", "s16le", "-"], capture_output=True, check=True).stdout


def common(p):
    p.add_argument("--backend", default=os.environ.get("EARS_BACKEND", "auto"), help="apple | nemotron | whisper | auto")
    p.add_argument("--rate", type=int, default=16000)
    p.add_argument("--vad", default="auto", help="silero | energy | none | auto")
    p.add_argument("--turns", default="none", help="silence | smart | none (smart = the Smart Turn stage)")
    p.add_argument("--previews", action="store_true", help="emit preview events (needs --turns)")


def main(argv=None):
    own_process = argv is None  # from the command line: the capture may own (and exit) it
    argv = list(sys.argv[1:] if argv is None else argv)
    from .home import STRANDED_HELP, warn_stranded
    warn_stranded("ears")   # a 0.1.0 settings file not moved yet: one line each, once per process
    # Verbs that own their whole argument list, handed on untouched:
    #   ears capture ...   the capture (mic or call leg -> the heard log), = python -m ears.listen capture
    #   ears services ...  the launch agents a bare install needs (stt, mouth, keeper, mail; speakers opt-in)
    #   ears speakers ...  the guided enrol for the speaker side-car: enrol, record, clusters, name
    #   ears heard ...     the heard log's own CLI (hook, write, last-seq)
    #   ears eval ...      every STT engine on the same audio (a file, or a call's turns)
    #   ears wake ...      the seats "hey NAME" wakes: add, list (the user wake file)
    if argv and argv[0] == "capture":
        from .listen.__main__ import main as listen_main
        return listen_main(argv, own_process=own_process)
    if argv and argv[0] == "services":
        from .services import main as services_main
        return services_main(argv[1:])
    if argv and argv[0] == "speakers":
        from .speakers import main as speakers_main
        return speakers_main(argv[1:])
    if argv and argv[0] == "eval":
        from .eval import main as eval_main
        return eval_main(argv[1:])
    if argv and argv[0] == "heard":
        from .heard import main as heard_main
        return heard_main(argv[1:])
    if argv and argv[0] == "wake":
        from .wake import main as wake_main
        return wake_main(argv[1:])
    if argv and argv[0] == "recording":
        from .recording import main as recording_main
        return recording_main(argv[1:])
    ap = argparse.ArgumentParser(prog="ears", description=__doc__, epilog=STRANDED_HELP,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    common(sub.add_parser("listen", help="PCM on stdin -> events on stdout"))
    f = sub.add_parser("file", help="a file -> events"); f.add_argument("wav"); common(f)
    f.add_argument("--realtime", action="store_true", help="pace the feed like a microphone")
    f.add_argument("--tail", type=float, default=2.0, help="s of silence after the file, so the last turn can close")
    sub.add_parser("backends", help="which backends run here")
    i = sub.add_parser("install", help="fetch Silero; --apple: build the helper, fetch Apple assets"); i.add_argument("--locale", default="en-AU")
    # off by default: the swift build fails on macOS 26 and the default engine is
    # Parakeet on mlx-audio
    i.add_argument("--apple", action="store_true", help="also build the Apple speech helper (Xcode's swift)")
    s = sub.add_parser("serve", help="OpenAI-compatible transcriptions over the Apple helper"); s.add_argument("port", nargs="?", default="2023")
    sub.add_parser("capture", help="the capture: mic or call leg -> the heard log (ears capture --help)")
    sub.add_parser("services", help="install/status/uninstall the launch agents (stt, mouth, keeper, mail)")
    sub.add_parser("heard", help="the heard log: hook, write, last-seq")
    sub.add_parser("eval", help="a clip or a call through every STT engine, side by side (ears eval --help)")
    sub.add_parser("wake", help="the seats \"hey NAME\" wakes: add, list (ears wake --help)")
    sub.add_parser("recording", help="whether captured audio is kept (off by default): status, on, off")
    a = ap.parse_args(argv)

    if a.cmd == "backends":
        for n, why in available().items():
            print(f"{n:9} {'ok' if why is None else 'no: ' + why}")
        print(f"{'silero':9} {'ok: ' + silero_model_path() if silero_model_path() else 'no model: ears install'}")
        return 0
    if a.cmd == "serve":
        from . import serve
        sys.argv = ["ears-serve", a.port]
        return serve.main()
    if a.cmd == "install":
        return install(a.locale, apple=a.apple)

    try:
        pipe = Pipeline(a.backend, a.rate, a.vad, a.turns, a.previews)
    except NoBackend as e:
        # one line a stranger can act on, not a traceback; `ears backends` lists them
        print(f"ears: {e}", file=sys.stderr)
        return 1
    step = int(a.rate * BLOCK) * 2
    try:
        if a.cmd == "listen":
            src = sys.stdin.buffer
            while True:
                pcm = src.read1(step) if hasattr(src, "read1") else src.read(step)
                if not pcm:
                    break
                pipe.feed(pcm)
        else:
            pcm = decode(a.wav, a.rate) + b"\0" * (int(a.tail * a.rate) * 2)
            t0 = time.monotonic()
            for off in range(0, len(pcm), step):
                pipe.feed(pcm[off:off + step])
                if a.realtime:
                    ahead = pipe.audio_now() - (time.monotonic() - t0)
                    if ahead > 0: time.sleep(ahead)
    except (KeyboardInterrupt, BrokenPipeError):
        pass
    pipe.close()
    return 0


def apple_assets(helper, locale):
    """Run `apple-speech install` for `locale`: None once the assets are in, else the one-line
    reason. The helper's stderr is captured and its last line is the reason; an old
    helper's raw NSError still comes back as one line, and a helper that says nothing is named."""
    try:
        r = subprocess.run([helper, "install"], env=dict(os.environ, APPLE_SPEECH_LOCALE=locale),
                           stderr=subprocess.PIPE, text=True)
    except OSError as e:
        return f"could not run {helper}: {e}"
    if r.returncode == 0:
        return None
    last = next((ln for ln in reversed((r.stderr or "").splitlines()) if ln.strip()), "")
    return " ".join(last.split()) or f"apple-speech install exited {r.returncode} with no message"


def install(locale, apple=False):
    here = os.path.dirname(os.path.abspath(__file__))
    pkg = os.path.normpath(os.path.join(here, "..", "..", "swift"))
    if apple and not os.path.exists(os.path.join(pkg, "Package.swift")):
        # the wheel does not ship swift/: asked for, the helper is refused loudly,
        # before anything is fetched, never skipped with a `done`
        print(f"ears install --apple: REFUSED: the Apple helper (apple-speech) builds from a source "
              f"checkout's swift/, and there is none here ({pkg}). Run `uv run ears install --apple` "
              "in a git checkout of sessionvoice; `ears install` without --apple fetches Silero only",
              file=sys.stderr)
        return 2
    bindir = os.path.expanduser("~/.cache/ears/bin"); os.makedirs(bindir, exist_ok=True)
    if apple and sys.platform == "darwin" and os.path.exists(os.path.join(pkg, "Package.swift")):
        print("building apple-speech ...", flush=True)
        try:
            subprocess.run(["swift", "build", "-c", "release", "--package-path", pkg], check=True)
        except (subprocess.CalledProcessError, OSError) as e:
            print(f"ears install: apple-speech helper did not build: {e}", file=sys.stderr)
            return 1
        shutil.copy2(os.path.join(pkg, ".build", "release", "apple-speech"), os.path.join(bindir, "apple-speech"))
        print("fetching Apple speech assets for", locale, flush=True)
        why = apple_assets(os.path.join(bindir, "apple-speech"), locale)
        if why:
            print(f"ears install: Apple speech assets for {locale} not installed: {why}", file=sys.stderr)
            return 1
    have = silero_model_path()
    if have:
        # a model already here is left alone (it may be the user's own EARS_SILERO_ONNX or the
        # VoiceMode copy services.py links); it is only checked against the pin
        print(silero_pin_check(have), flush=True)
    else:
        dst = os.path.expanduser("~/.cache/ears/silero_vad.onnx")
        print(f"fetching Silero VAD {SILERO_TAG} from {SILERO_URL} -> {dst}", flush=True)
        try:
            fetch_silero(dst)
        except SileroFetchError as e:
            print(f"ears install: {e}", file=sys.stderr)
            return 1
    print("done"); return 0


if __name__ == "__main__":
    sys.exit(main())
