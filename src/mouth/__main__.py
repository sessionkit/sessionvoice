"""mouth CLI: say, again, stop, status, duck, gain, timeline, devices, voices, resolve, render, audio, serve.  ``python -m mouth --help``"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import NoAudio, again, amend, play, retract, say, status, stop

#: The verbs that write a drop notice in their own process, so mark it as the mouth
#: service: the player (``serve``, a CLI say's; ``mail``, the launch
#: agent's) and ``retract``, whose said row for a queued line, and so its notice to
#: the line's sender, is written by the retracting CLI itself (``mouth.retract``).
#: ``again`` writes the said row of a queued resume it replaces, in its
#: own process too (``mouth._replace_resume``); that notice is quiet by rule.
SERVICE_VERBS = ("mail", "serve", "retract", "again")

DEVICES_HELP = """\
The output device names, one per line, exactly as --device takes them, then a
line per live call (call:N). Notes ride after a TAB as '# ...':

  Mac Studio Speakers\t# current output, volume 62%
  External Headphones
  # current input: MacBook Pro Microphone

The current output is marked with its volume ('(muted)' when muted). The last
line names the current input; it starts with '#' and is not a --device name.
All three are asked of CoreAudio live; what is unknown is left out."""


def main(argv=None) -> int:
    from ears.home import STRANDED_HELP, warn_stranded
    warn_stranded("mouth")   # a 0.1.0 settings file not moved yet: one line each, once per process
    ap = argparse.ArgumentParser(prog="mouth", description="speak back: say returns at once",
                                 epilog=STRANDED_HELP)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("say", help="queue TEXT ('-' reads stdin); prints the utterance id")
    s.add_argument("text", nargs="+")
    s.add_argument("--voice", help="a clone (alice, bob) or a kokoro voice (af_sky); else the "
                   "caller's session/seat/agent settings, $MOUTH_VOICE, the settings file's voice, af_sky (`mouth voices`)")
    s.add_argument("--speed", type=float)
    s.add_argument("--backend", choices=["auto", "kokoro", "clone", "apple", "silence"])
    s.add_argument("--device", help="exact output device name, 'null', 'default', or call:<N> (a live "
                   "Delta Chat call, `mouth devices`); else $MOUTH_DEVICE, else `output` in the "
                   "settings file, else the system default output")
    side = s.add_mutually_exclusive_group()
    side.add_argument("--pan", type=float, help="-1 left ear only .. 1 right ear only; $MOUTH_PAN")
    side.add_argument("--channel", choices=["left", "right", "both"], help="--pan -1 / 1 / none")
    s.add_argument("--log-dir", type=Path, help="write saying/said here, not the real heard log")
    s.add_argument("--volume", metavar="N", help="this line's level, 0-100 (100: full); "
                   "else mouth.volume in the settings cascade")
    snorm = s.add_mutually_exclusive_group()
    snorm.add_argument("--normalize", dest="normalize", action="store_const", const=True,
                       help="level the voice to mouth.normalize_lufs by its own gain "
                            "(the default: mouth.normalize)")
    snorm.add_argument("--no-normalize", dest="normalize", action="store_const", const=False,
                       help="play the voice at its own loudness")
    s.add_argument("--lead", metavar="S", help="buffer S seconds of this line's audio before it "
                   "plays (2.5: no chop from a clone rendering below real time); else "
                   "mouth.lead_s in the settings cascade, 0")
    sbarge = s.add_mutually_exclusive_group()
    sbarge.add_argument("--barge-words", metavar="N|off",
                        help="how many of the user's words cut this line (off: none; speech still "
                             "ducks it and \"stop\" still cuts it); else the barge_words setting, "
                             "$MOUTH_BARGE_WORDS, 2 (`mouth status` names the layer)")
    sbarge.add_argument("--no-barge", dest="barge_words", action="store_const", const="off",
                        help="--barge-words off: the user's words never cut this line, a stop ask still does")
    s.add_argument("--wait", action="store_true", help="block until said; print it")
    s.add_argument("--hold", choices=["turn-end", "none"],
                   help="turn-end: wait for the end of the user's turn (the ears' heard log), "
                        "then speak (the default on a room device: room_hold); none: play at "
                        "once, as --now does without cutting the line playing")
    s.add_argument("--interest", choices=["high", "normal", "low", "zero"],
                   help="how much you want the user's reply: high (wake on their partials), normal (their "
                        "turn), low, zero (speak and leave); read by heard-first reply:AGENT")
    s.add_argument("--expires", type=float, metavar="S",
                   help="drop the line, unspoken, if it has not started S seconds from now")
    s.add_argument("--in-reply-to", metavar="ID",
                   help="the Message-ID of the heard turn this line answers (<id> or bare): the "
                        "mail threads under it; root and Call-ID come from the heard box "
                        "($MOUTH_HEARD_BOX)")
    pri = s.add_mutually_exclusive_group()
    pri.add_argument("--next", dest="priority", action="store_const", const="next",
                     help="the front of the queue (priority 0)")
    pri.add_argument("--now", dest="priority", action="store_const", const="now",
                     help="interrupt the line playing now, then speak")
    pri.add_argument("--priority", dest="priority", metavar="N",
                     help="a place in the playlist: lower plays sooner; next is 0, none is 50")

    pl = sub.add_parser("play", help="queue a sound file, or a section of it")
    pl.add_argument("source")
    pl.add_argument("--start", type=float, help="seconds into the file")
    pl.add_argument("--end", type=float, help="seconds into the file")
    pl.add_argument("--device")
    pside = pl.add_mutually_exclusive_group()
    pside.add_argument("--pan", type=float)
    pside.add_argument("--channel", choices=["left", "right", "both"])
    pl.add_argument("--log-dir", type=Path)
    pl.add_argument("--volume", metavar="N", help="this file's level, 0-100 (100: full); "
                    "else mouth.play_volume, then mouth.volume, in the settings cascade")
    pduck = pl.add_mutually_exclusive_group()
    pduck.add_argument("--duck", dest="duck", action="store_const", const=True,
                       help="dip this file under speech, even under a `mouth duck off` override "
                       "(the default: mouth.music_duck)")
    pduck.add_argument("--no-duck", dest="duck", action="store_const", const=False,
                       help="never dip this file (beats the setting and any override); "
                       "it still mixes, and speech never cuts it")
    pl.add_argument("--duck-level", metavar="N",
                    help="its level while ducked, 0-100 of its own (else mouth.music_duck_level, 20)")
    pl.add_argument("--wait", action="store_true")
    ppri = pl.add_mutually_exclusive_group()
    ppri.add_argument("--next", dest="priority", action="store_const", const="next")
    ppri.add_argument("--now", dest="priority", action="store_const", const="now")

    m = sub.add_parser("amend", help="rewrite a queued line in place (before it is spoken)")
    m.add_argument("utt")
    m.add_argument("text", nargs="+")
    r = sub.add_parser("retract", help="drop a queued line, or cut it if it is playing")
    r.add_argument("utt")
    ag = sub.add_parser("again", help="replay a kept line (its WAV, else its text) as a new line "
                        "held for the end of the user's turn; replaces a resume of it. Exit 3: "
                        "nothing to replay")
    ag.add_argument("utt", help="the line to replay: its utt, as the saying/said rows name it")
    ag.add_argument("--seq", type=int, help="the heard seq of the turn that asked for it")
    ag.add_argument("--heard", metavar="TEXT", help="what the user said to ask for it")
    ag.add_argument("--volume", metavar="N", help="the level the line played at, 0-100 "
                    "(its said row's volume); else the host's")
    ag.add_argument("--text", help="the line's text, said again if its audio and note are gone")
    ag.add_argument("--voice", help="the voice for --text")
    ag.add_argument("--agent", help="the line's agent, for --text (its note names it otherwise)")
    ag.add_argument("--session", help="the line's session, for --text (likewise)")
    ag.add_argument("--device", help="as `mouth say --device`; else a new line's device")

    t = sub.add_parser("stop", help="cut what is playing, and flush the queue")
    t.add_argument("--reason", default="stop", help="stop (default) or barge-in")
    t.add_argument("--current", action="store_true", help="cut only what is playing; keep the queue")

    sub.add_parser("status", help="player, queue, what is playing")
    from . import duckswitch as _duckswitch

    dk = sub.add_parser("duck", help="ducking now: off or on for a while, reset, or no argument "
                        "for the state (JSON)",
                        description="Turn the music's dip under speech off or on for a while, on this\n"
                        "host, above the music_duck setting; it expires back to the setting.\n"
                        "No argument prints the state: effective, by, and the override's remaining_s.",
                        epilog="precedence: " + _duckswitch.PRECEDENCE + "\n\n"
                        "off means no dip: the music plays on at its own level, lines mix over\n"
                        "it, and speech never cuts it (mouth stop still does). It gates the\n"
                        "DJ (mpv) dip too. --for takes 90s, 30m, 1h or seconds; default 1h.",
                        formatter_class=argparse.RawDescriptionHelpFormatter)
    dk.add_argument("state", nargs="?", choices=["off", "on", "reset"],
                    help="off: no dip; on: dip even if the setting is off; reset: the setting again now")
    dk.add_argument("--for", dest="for_", metavar="T",
                    help="how long off/on lasts: 90s, 30m, 1h or seconds (default 1h)")
    from . import bargeswitch as _bargeswitch

    bg = sub.add_parser("barge", help="barge-in for a while: N words or off, the duck, its cap, more "
                        "ignored words; reset; or no argument for the policy (JSON)",
                        description="The model's timed override of barge-in, on this host: how many of the user's\n"
                        "words cut a line, its duck, and words that never count. Above the user's\n"
                        "barge_* settings; it expires back to them. Each set replaces the override:\n"
                        "name every value you want. No argument prints the policy now, each value\n"
                        "with its from/by, and the override with its remaining_s.",
                        epilog="precedence: " + _bargeswitch.PRECEDENCE + "\n\n"
                        "off: no cut on a word count; speech still ducks the line and a stop\n"
                        "ask (\"stop\", \"wait\") still cuts it. A line's own --barge-words sets N\n"
                        "only, so this override's duck and ignore words still apply to it.\n"
                        "--ignore only ADDS to the ignored words: a backchannel never counts.\n"
                        "--for takes 90s, 30m, 1h or seconds; default 1h. No forever: a standing\n"
                        "change is the user's setting.\n\n"
                        "  mouth barge 3 --for 40m             the rest of this call\n"
                        "  mouth barge --ignore shell --for 1h  a phantom word, tonight",
                        formatter_class=argparse.RawDescriptionHelpFormatter)
    bg.add_argument("words", nargs="?", metavar="N|off|reset",
                    help="N: words that cut a line (1 or more); off: none do; reset: the settings again now")
    bg.add_argument("--duck", metavar="L|off", help="the line's level while ducked, 0-100; off: no dip")
    bg.add_argument("--duck-max", dest="duck_max", metavar="S",
                    help="the longest a line stays ducked under unbroken voice, seconds")
    bg.add_argument("--ignore", metavar="W,..", help="more words that never count (added, never removed)")
    bg.add_argument("--for", dest="for_", metavar="T",
                    help="how long it lasts: 90s, 30m, 1h or seconds (default 1h)")
    gn = sub.add_parser("gain", help="a voice's own gain (JSON): no value shows it, a number in dB "
                        "sets a hand gain, reset goes back to the measured gain",
                        description="A voice's per-voice gain, in the settings.json beside the voice\n"
                        "(normalize). No value shows it and writes nothing: the file's\n"
                        "gain_db, measured_lufs, measured_at and gain_from, and the level a line\n"
                        "of the voice gets now. A number sets a hand gain (gain_from: hand),\n"
                        "-24 to 24 dB, which is never measured over. reset goes back to the\n"
                        "measured gain: from the kept measured_lufs, or a fresh measure on the\n"
                        "voice's next line.",
                        epilog="  mouth gain af_sky          show\n"
                        "  mouth gain af_sky 3.5      set a hand gain of +3.5 dB\n"
                        "  mouth gain af_sky -6       set -6 dB\n"
                        "  mouth gain af_sky reset    back to measured",
                        formatter_class=argparse.RawDescriptionHelpFormatter)
    gn.add_argument("voice", help="the voice, as --voice takes it (af_sky, alice, group/voice)")
    gn.add_argument("value", nargs="?", metavar="DB|reset",
                    help="DB: a hand gain, -24 to 24; reset: the measured gain again")
    from . import timeline as _timeline

    tl = sub.add_parser("timeline", help=_timeline.TIMELINE_HELP, description=_timeline.TIMELINE_HELP)
    _timeline.add_arguments(tl)
    ml = sub.add_parser("mail", help="the player, resident on the queue maildir (mail to mouth@<host> lands there)")
    ml.add_argument("--box", type=Path, help="the maildir (default ~/.mail/agents/mouth)")
    ml.add_argument("--once", action="store_true", help="speak what is in new/, then exit")
    dv = sub.add_parser("devices", help="output device names, exactly as --device takes them; "
                        "the current output is marked, with its volume, and the current input named",
                        description=DEVICES_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    dv.add_argument("--json", action="store_true",
                    help="one JSON object: outputs, calls, current_output, current_input, "
                    "output_volume (0-1), output_muted; unknown is null")
    sub.add_parser("voices", help="JSON: the default voice, where it came from, and every voice")
    rv = sub.add_parser("resolve", help="JSON: what a --voice expression speaks with (a clone's "
                        "reference clip, or kokoro); exit 2, with the reason, if it does not resolve")
    rv.add_argument("voice", help="a name (alice, af_sky), group/voice, voice[N] or group/voice/clip.wav")
    rv.add_argument("--backend", default="auto", choices=["auto", "kokoro", "clone"])
    rn = sub.add_parser("render", help="synthesise TEXT to a WAV file without playing it; "
                        "JSON out (play it later with `mouth play FILE`)")
    rn.add_argument("text", nargs="+")
    rn.add_argument("--voice", required=True, help="as for say, e.g. group/voice/clip.wav")
    rn.add_argument("--out", required=True, type=Path, help="the WAV to write")
    rn.add_argument("--speed", type=float)
    rn.add_argument("--backend", default="auto", choices=["auto", "kokoro", "clone"])
    rn.add_argument("--if-missing", action="store_true", help="make nothing if --out exists")
    au = sub.add_parser("audio", help="JSON lines: the lines the mouth kept (a WAV each), newest first")
    au.add_argument("--voice", help="only this voice's lines")
    au.add_argument("--last", type=int, help="at most this many")
    v = sub.add_parser("serve", help="run the player in the foreground (say starts one for you)")
    v.add_argument("--idle", type=float, help="exit after this long with nothing queued (default 600 s)")

    a = ap.parse_args(argv)
    if a.cmd in SERVICE_VERBS:
        # the mouth service's own verbs, and only these, may use the default `message`
        # for a drop notice. A library import never is.
        from . import notice

        notice.mark_service(f"mouth {a.cmd}")
    if a.cmd == "say":
        text = sys.stdin.read() if a.text == ["-"] else " ".join(a.text)
        pan = a.channel or a.pan
        try:
            r = say(text.strip(), voice=a.voice, speed=a.speed, backend=a.backend, device=a.device,
                    pan=pan, log_dir=a.log_dir, wait=a.wait, priority=a.priority,
                    hold=a.hold, expires_s=a.expires, interest=a.interest,
                    in_reply_to=a.in_reply_to, volume=a.volume, normalize=a.normalize,
                    lead_s=a.lead, barge_words=a.barge_words)
        except (ValueError, TimeoutError) as e:
            print(str(e), file=sys.stderr)
            return 2
        print(json.dumps(r) if a.wait else r["utt"])
        if a.wait and r.get("cut"):  # the waiter is the drop's notice
            from . import notice

            print(notice.cli_line(text.strip(), r), file=sys.stderr)
        return 0 if not a.wait or r.get("reason") == "done" else 1
    if a.cmd == "play":
        try:
            r = play(a.source, start=a.start, end=a.end, device=a.device, pan=a.channel or a.pan,
                     log_dir=a.log_dir, wait=a.wait, priority=a.priority, volume=a.volume,
                     duck=a.duck, duck_level=a.duck_level)
        except (ValueError, TimeoutError) as e:
            print(str(e), file=sys.stderr)
            return 2
        print(json.dumps(r) if a.wait else r["utt"])
        if a.wait and r.get("cut"):
            from . import notice, sound_label

            print(notice.cli_line(sound_label(r.get("file") or a.source, r.get("start"), r.get("end")), r),
                  file=sys.stderr)
        return 0 if not a.wait or r.get("reason") == "done" else 1
    if a.cmd in ("amend", "retract"):
        try:
            r = amend(a.utt, " ".join(a.text)) if a.cmd == "amend" else retract(a.utt)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
        print(json.dumps(r))
        return 0
    if a.cmd == "again":
        try:
            r = again(a.utt, seq=a.seq, heard_text=a.heard, volume=a.volume, text=a.text,
                      voice=a.voice, agent=a.agent, session=a.session, device=a.device)
        except NoAudio as e:
            print(str(e), file=sys.stderr)
            return 3
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
        print(json.dumps({k: r.get(k) for k in ("utt", "again_of", "again_from", "again_why",
                                                 "replaced") if r.get(k) is not None}))
        return 0
    if a.cmd == "stop":
        print(json.dumps(stop(a.reason, flush=not a.current)))
        return 0
    if a.cmd == "mail":
        from .inbox import watch

        if a.once:
            return watch(a.box, once=True)
        import signal

        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # so 'disabled' is logged
        return watch(a.box)
    if a.cmd == "status":
        print(json.dumps(status()))
        return 0
    if a.cmd == "duck":
        return _duck(a)
    if a.cmd == "barge":
        return _barge(a)
    if a.cmd == "gain":
        return _gain(a)
    if a.cmd == "timeline":
        return _timeline.run(a)
    if a.cmd == "devices":
        from .devices import devices, devices_text

        d = devices()
        print(json.dumps(d, ensure_ascii=False) if a.json else devices_text(d))
        return 0
    if a.cmd == "voices":
        from ears import heard

        from .voices import voices

        print(json.dumps(voices(sid=heard.caller_session())))
        return 0
    if a.cmd == "resolve":
        from .backends import resolve as _resolve

        try:
            be, voice = _resolve(a.backend, a.voice)
        except ValueError as e:
            print(f"mouth resolve: {e}", file=sys.stderr)
            return 2
        extra = getattr(be, "extra_body", {}) or {}
        print(json.dumps({"voice": a.voice, "backend": be.name, "sends": voice,
                          "ref_audio": extra.get("ref_audio"), "ref_text": extra.get("ref_text")},
                         ensure_ascii=False))
        return 0
    if a.cmd == "render":
        from .audio import render

        text = sys.stdin.read() if a.text == ["-"] else " ".join(a.text)
        try:
            info = render(a.voice, text, a.out, backend=a.backend, speed=a.speed,
                          if_missing=a.if_missing)
        except ValueError as e:
            print(f"mouth render: {e}", file=sys.stderr)
            return 2
        print(json.dumps(info, ensure_ascii=False))
        return 0
    if a.cmd == "audio":
        from .audio import kept

        for meta in kept(voice=a.voice, last=a.last):
            print(json.dumps(meta, ensure_ascii=False))
        return 0
    if a.cmd == "serve":
        import signal

        from .player import serve

        # A TERM (a restart between utterances) must run serve's cleanup:
        # release the lock and remove player.pid, not leave a stale one.
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        return serve(idle_exit_s=a.idle)
    return 1


def _duck(a) -> int:
    """``mouth duck [off|on [--for T] | reset]``: write, then
    print the readout, so one call confirms the state and when it ends."""
    from ears import heard

    from . import duck_state, duckswitch
    from .paths import mouth_dir

    if a.for_ is not None and a.state not in ("off", "on"):
        print("mouth duck: --for goes with off or on", file=sys.stderr)
        return 2
    d = mouth_dir()
    if a.state in ("off", "on"):
        try:
            for_s = duckswitch.parse_for(a.for_) if a.for_ is not None else None
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
        duckswitch.set(d, on=a.state == "on", for_s=for_s,
                       by=heard.caller_session() or heard.caller_agent())
    elif a.state == "reset":
        duckswitch.reset(d)
    print(json.dumps(duck_state(d)))
    return 0


def _barge(a) -> int:
    """``mouth barge [N|off] [--duck L|off] [--duck-max S] [--ignore W,..] [--for T] |
    reset``: write, then print the policy, so one call
    confirms what a line gets now and when the override ends."""
    from ears import heard

    from . import barge_state, bargeswitch
    from .paths import mouth_dir

    sets = {"duck_level": a.duck, "duck_max_s": a.duck_max, "ignore": a.ignore}
    words = a.words
    d = mouth_dir()
    if words == "reset":
        if any(v is not None for v in (*sets.values(), a.for_)):
            print("mouth barge: reset takes nothing else", file=sys.stderr)
            return 2
        bargeswitch.reset(d)
    elif words is not None or any(v is not None for v in sets.values()):
        try:
            for_s = bargeswitch.parse_for(a.for_) if a.for_ is not None else None
            bargeswitch.set(d, words=words, for_s=for_s,
                            by=heard.caller_session() or heard.caller_agent(), **sets)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
    elif a.for_ is not None:
        print("mouth barge: --for goes with N, off, --duck, --duck-max or --ignore", file=sys.stderr)
        return 2
    print(json.dumps(barge_state(d)))
    return 0


def _gain(a) -> int:
    """``mouth gain VOICE [DB|reset]``: write, then print
    the readout, so one call confirms what the voice's next line gets."""
    from ears import heard

    from . import level, voices
    from .paths import mouth_dir

    d = mouth_dir()
    cascaded = voices.cascade(heard.caller_session(), voices.LINE_KEYS)   # as say() reads it
    try:
        if a.value == "reset":
            level.reset_gain(a.voice, voices.lufs_setting(cascaded=cascaded, d=d)[0], d)
        elif a.value is not None:
            level.set_gain(a.voice, level.parse_gain(a.value), d)
        print(json.dumps(level.gain_state(a.voice, d, cascaded)))
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
