"""Run capture, "the ears", from a shell; print its result as JSON on exit.

    python -m ears.listen capture --source mic --device C930e
    python -m ears.listen capture --source file:X.wav [--tail-silence 10]

It writes through ``heard`` (``<sessionvoice home>/logs/conversations/``,
or ``--log-dir``) and runs until stopped: SIGINT/SIGTERM or ``--stop-file``
(reason ``stop``), an error, the end of a file source (``eof``), or
``--ceiling`` if given. It does not return on a turn. Exit 0 on a clean
stop, 1 on reason ``error``, 2 on bad arguments, 3 when its watchdog ended a
capture that was stuck (``ears.listen.watchdog``). SIGTERM and SIGUSR1 write
every thread's stack to stderr first; SIGUSR1 does not stop it. Run as
``python -m ears.listen``, it flushes its output and ends with ``os._exit``, so
a thread still stuck in a call cannot keep the process alive after its result.

It only ever opens an INPUT: no TTS, no playback, no conch.
"""

from __future__ import annotations

import argparse
import faulthandler
import json
import os
import signal
import sys
import threading
from pathlib import Path
from typing import NoReturn

from .. import heard, recording
from .chunker import Chunker
from .detector import DEFAULT_SILENCE_S, DETECTORS, SilenceTurnDetector, make_detector
from .loop import DEFAULT_HEARTBEAT_S, DEFAULT_REDECODE_MAX_S, TURN_TEXT_MODES, capture
from .missed import DEFAULT_WINDOW_S, MissedReply, SaidTail
from .sink import HeardSink
from .sources import SourceError, open_source
from . import watchdog as _watchdog
from .streaming import Partials
from .stt import (DEFAULT_WHISPER_MODEL, DEFAULT_WHISPER_URL, FollowingSTT, SttHint, WhisperSTT, conf_path,
                  stt_pinned)


def _speaker_labels(log_dir):
    """The speaker side-car's labels for today, beside the heard log this capture writes."""
    from .speakers import LabelTail, LineTexts
    d = Path(log_dir).expanduser() if log_dir else None
    # the mouth's lines, by utt, from the same heard log, for the echo guard
    return LabelTail(lambda: heard.speakers_path(heard.log_path(directory=d)),
                     lines=LineTexts(lambda: heard.log_path(directory=d)))


def _switch(args) -> recording.Switch:
    """The recording switch this capture holds: its own --record/--no-record,
    then $EARS_RECORD, then ears.json's ``record`` (followed live), then off."""
    return recording.Switch(cli=getattr(args, "record", None))


def _missed_reply(args, switch=None):
    """A window of audio kept when a mouth line ends and no turn comes.
    The ``said`` lines are read from the heard log this capture writes.

    OPT-IN, off for every source unless ``--missed-reply-window S`` (S > 0) is given.
    It is for MIC audio, the user's voice, and a default that is on leaks
    through every launcher that forgets to turn it off: a call leg (``call:N``, the far
    end's voice) and ``bin/call-tap``'s far-end ``--source mic`` on BlackHole 2ch are
    two. The one capture on the user's mic, ``keeper/ears-keeper.sh``'s
    room capture, passes the window (``EARS_MISSED_REPLY_S``, default 30).

    The window only says WHEN a clip would be cut. Whether it is written is the recording
    switch's (:mod:`ears.recording`), checked at each clip: off, the default,
    writes no file."""
    window = args.missed_reply_window
    if window <= 0:
        return None
    d = Path(args.log_dir).expanduser() if args.log_dir else None
    return MissedReply(SaidTail(lambda: heard.log_path(directory=d)),
                       Path(args.missed_reply_dir) if args.missed_reply_dir else None,
                       window_s=window, switch=switch if switch is not None else _switch(args))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m ears.listen", description="listen spike")
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser(
        "capture",
        help="the ears: capture and write what is heard until stopped",
        description="Capture: write started/heartbeat/partial/turn/stopped through heard, until stopped.",
    )
    c.add_argument("--source", default="mic", help="'mic', 'file:PATH.wav', or 'call' / 'call:N' (a live Delta call, from the bot's ear leg) (default: mic)")
    c.add_argument("--device", default=None, help="mic input: an index, or a case-insensitive substring of its name (e.g. C930e)")
    c.add_argument("--log-dir", default=None, help="write heard_YYYY-MM-DD.jsonl here instead of <sessionvoice home>/logs/conversations")
    c.add_argument("--heartbeat", type=float, default=DEFAULT_HEARTBEAT_S, help="heartbeat period, seconds; 0 disables (default: %(default)s)")
    c.add_argument("--ceiling", type=float, default=None, help="stop after this many seconds (default: none)")
    c.add_argument("--stop-file", default=None, help="stop (reason stop) once this file exists")
    c.add_argument("--detector", choices=sorted(DETECTORS), default=SilenceTurnDetector.name,
                   help="speech call behind the end-of-turn rule: webrtcvad ('vad-silence', the default) or Silero VAD "
                        "('silero'; needs onnxruntime and --silero-model) (default: %(default)s)")
    c.add_argument("--silero-model", default=None, help="silero_vad.onnx (v5) for --detector silero (default: $EARS_SILERO_MODEL)")
    c.add_argument("--silero-threshold", type=float, default=0.5, help="Silero speech probability that starts speech (default: %(default)s)")
    c.add_argument("--silence", type=float, default=DEFAULT_SILENCE_S, help="silence that ends a turn, seconds (default: %(default)s)")
    c.add_argument("--quiet", type=float, default=0.35, help="quiet that cuts a partial chunk, seconds (default: %(default)s)")
    c.add_argument("--max-chunk", type=float, default=2.0, help="longest chunk before a soft cut, seconds; sets how soon a partial lands while speech goes on (default: %(default)s)")
    c.add_argument("--cut-window", type=float, default=0.4, help="a max-length chunk is cut at the quietest frame of its last this-many seconds (default: %(default)s)")
    c.add_argument("--turn-text", choices=TURN_TEXT_MODES, default="joined",
                   help="turn line text: 'joined' partials (the spec's default) or a 'redecode' of the turn's whole audio, "
                        "launched at the last quiet cut so it hides inside the silence wait (default: %(default)s)")
    c.add_argument("--speaker-split", type=int, default=2, metavar="K",
                   help="end a turn where another named speaker holds K partials in a row, from the speaker "
                        "side-car's speakers_*.jsonl beside the heard log, and name every turn the side-car "
                        "named by the same rule (speaker_from: labels; only a turn of fewer than K partials is named "
                        "by its one named label); 0 = off. No side-car "
                        "file: turns as before (default: %(default)s)")
    c.add_argument("--redecode-max", type=float, default=DEFAULT_REDECODE_MAX_S,
                   help="with --turn-text redecode, a turn longer than this many seconds keeps its joined text (default: %(default)s)")
    c.add_argument("--no-pace", action="store_true", help="feed a file source as fast as possible, not at real time")
    c.add_argument("--tail-silence", type=float, default=0.0, help="seconds of silence fed after a file source ends (default: %(default)s)")
    follow = f"follow {os.path.expanduser(conf_path())}'s stt live, checked before every transcribe"
    c.add_argument("--stt-url", default=None,
                   help=f"OpenAI-compatible STT base URL; given (or EARS_LISTEN_STT_URL), it pins the engine "
                        f"(default: {follow}; now {DEFAULT_WHISPER_URL})")
    c.add_argument("--stt-model", default=None,
                   help=f"STT model name; given (or EARS_LISTEN_STT_MODEL), it pins the engine "
                        f"(default: {follow}; now {DEFAULT_WHISPER_MODEL})")
    c.add_argument("--keep-annotations", action="store_true", help="keep whisper's non-speech tags ('(static)', '[BLANK_AUDIO]') as text")
    c.add_argument("--partials", choices=("final", "nemotron"), default=None,
                   help="what writes the partials: 'final' (the engine above, per chunk) or 'nemotron' (a streaming "
                        "child; its settled words, appended; needs sessionvoice[nemotron]). Given, it pins the choice "
                        f"(default: follow {os.path.expanduser(conf_path())}'s partials, looked at as each turn opens)")
    c.add_argument("--missed-reply-window", type=float, default=0.0, metavar="S",
                   help="when a mouth line ends (its said) and no turn comes within S seconds, keep that window's "
                        "mic audio as one WAV (0600, kept for record_days, default 7; 0 = forever) and log a missed-reply event naming it, "
                        "only while recording is on (--record, $EARS_RECORD or ears.json's "
                        "\"record\"; off by default). Opt-in: default 0 = off, for every source. Give it only on the user's own mic; "
                        f"keeper/ears-keeper.sh's room capture passes {DEFAULT_WINDOW_S:g} (EARS_MISSED_REPLY_S)")
    c.add_argument("--missed-reply-dir", default=None,
                   help="where missed-reply WAVs go; refused if it is in a synced location "
                        "(default: <sessionvoice home>/state/missed-reply)")
    # not mutually exclusive: the last of the two given wins
    c.add_argument("--record", dest="record", action="store_const", const=True, default=None,
                     help="keep captured audio (each turn's WAV, and the missed-reply clip) whatever $EARS_RECORD and ears.json say. "
                          f"While it is on, {recording.ALL_VOICES}")
    c.add_argument("--no-record", dest="record", action="store_const", const=False,
                     help="keep no captured audio, whatever $EARS_RECORD and ears.json say "
                          "(default: $EARS_RECORD, then ears.json's \"record\", followed live, then off)")
    c.add_argument("--session", default=heard.caller_session(), help="session id stamped on lines (default: from the environment)")
    c.add_argument("--agent", default=heard.caller_agent(), help="agent name stamped on lines (default: from the environment)")
    return p


def make_stt(args):
    """The engine: pinned when a flag or EARS_LISTEN_STT_URL/_MODEL names it, else one
    that follows ears.json's ``stt`` live."""
    drop = not args.keep_annotations
    # the wake file's seat names as whisper's prompt, both branches, on the turn's FINAL
    # re-decode only: final_prompt, never prompt, so no partial carries it
    hint = SttHint()
    if args.stt_url or args.stt_model or stt_pinned():
        return WhisperSTT(args.stt_url or DEFAULT_WHISPER_URL, model=args.stt_model or DEFAULT_WHISPER_MODEL,
                          drop_annotations=drop, final_prompt=hint)
    return FollowingSTT(make=lambda url, model: WhisperSTT(url, model=model, drop_annotations=drop,
                                                           final_prompt=hint))


def _error_json(msg: str) -> str:
    return json.dumps({"reason": "error", "text": "", "cursor": 0, "error": msg})


def _register_stack_dumps() -> None:
    """Write every thread's stack to stderr on SIGTERM and SIGUSR1.

    Each beat, each STOP check and each effect of SIGTERM runs on the main thread,
    so a capture stuck in any wait there looks the same from outside. When the
    keeper TERMs it, this dump names the frame it was stuck in, in the capture's
    log. faulthandler's handler is C code, so it dumps even when the main thread
    is stuck inside C and Python's handler never runs.

    The order matters, and it must run AFTER ``signal.signal``: that call replaces
    faulthandler's C handler, and the dump is lost. Registered after it, with
    ``chain=True``, faulthandler dumps and then calls the Python handler, which sets
    the stop Event as before. SIGUSR1 dumps and does nothing else: a look at a live
    capture that does not stop it.

    Best effort: a stderr with no file descriptor (an in-process caller that
    captures it) gets no dump, and the capture runs as before.
    """
    try:
        if not faulthandler.is_enabled():  # a fatal signal (a crash in C) dumps too
            faulthandler.enable(all_threads=True)
        for sig, chain in ((signal.SIGTERM, True), (getattr(signal, "SIGUSR1", None), False)):
            if sig is None:
                continue
            # A second main() in one process: signal.signal has just replaced the C
            # handler, and register() alone would not put it back.
            faulthandler.unregister(sig)
            faulthandler.register(sig, all_threads=True, chain=chain)
    except (AttributeError, OSError, ValueError, RuntimeError):  # io.UnsupportedOperation is both
        pass


def main(argv: list[str] | None = None, *, own_process: bool | None = None) -> int:
    """``own_process``: this call is the whole process (``python -m ears.listen``, the
    ``ears`` script), so the capture gets its watchdog, which may ``os._exit`` it. The
    default is True only when ``argv`` is None, as from a command line. A caller that
    passes ``argv`` in-process (a test) gets no watchdog and is never exited."""
    if own_process is None:
        own_process = argv is None
    args = build_parser().parse_args(argv)
    try:
        source = open_source(args.source, realtime=not args.no_pace, device=args.device, tail_silence_s=args.tail_silence)
    except SourceError as exc:
        print(_error_json(str(exc)))
        return 1
    try:
        if args.detector == SilenceTurnDetector.name:
            detector = SilenceTurnDetector(args.silence)
        else:
            detector = make_detector(args.detector, silence_s=args.silence, model=args.silero_model, threshold=args.silero_threshold)
    except Exception as exc:  # no onnxruntime, no model: say so, as JSON, before listening
        source.close()
        print(_error_json(f"detector {args.detector}: {exc}"))
        return 1
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    _register_stack_dumps()
    stt = make_stt(args)
    switch = _switch(args)
    try:
        result = capture(
            source,
            stt,
            HeardSink(Path(args.log_dir) if args.log_dir else None),
            heartbeat=args.heartbeat,
            ceiling=args.ceiling,
            detector=detector,
            chunker=Chunker(quiet_s=args.quiet, max_s=args.max_chunk, cut_window_s=args.cut_window),
            stop=stop,
            stop_file=args.stop_file,
            session=args.session,
            agent=args.agent,
            turn_text=args.turn_text,
            redecode_max_s=args.redecode_max,
            speaker_split=args.speaker_split,
            speaker_labels=_speaker_labels(args.log_dir) if args.speaker_split > 0 else None,
            partials=Partials(args.partials),
            missed_reply=_missed_reply(args, switch),
            record=switch,
            # STOP and SIGTERM are honoured off the main thread; a wedge, a stop
            # not honoured, or an exit that hangs ends the process (exit 3), after a dump
            watchdog=_watchdog.Watchdog(exit_grace_s=_watchdog.STOP_GRACE_S) if own_process else None,
        )
    finally:
        stt.close()
    print(json.dumps(result.to_dict(), ensure_ascii=False))
    return 1 if result.reason == "error" else 0


def exit_process(rc: int) -> NoReturn:
    """Flush stdout and stderr, then ``os._exit(rc)``.

    A plain exit joins every non-daemon thread first, the STT executor's workers
    included, so one worker still stuck in a call keeps the process alive after the
    result is printed. ``os._exit`` skips the join and the atexit handlers (none of
    ours matter). The flush comes first: the keeper sends stdout to a file, which is
    block-buffered, and ``os._exit`` would drop the result line. Only the
    ``__main__`` path calls this; ``main()`` itself returns, for in-process callers.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:  # noqa: BLE001 - a closed or broken stream must not stop the exit
            pass
    os._exit(rc)


if __name__ == "__main__":
    exit_process(main())
