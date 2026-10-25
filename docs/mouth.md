# The mouth

The mouth is how an agent speaks out loud. `mouth say TEXT` queues a line
and returns at once (unless `--wait`). One resident player speaks the
queue's lines one at a time, each in a TTS voice, on the output device the
line names. A sound from `mouth play` plays on its own lane beside them
(`src/mouth/bed.py`). Code: `src/mouth/`.

- **In:** text from `mouth say`, a sound file from `mouth play`, or mail to
  `mouth@<host>`. `mouth play` takes a file, never a URL: the decoder reads
  files only (ffmpeg's `-protocol_whitelist file`).
- **Out:** the speech (or the sound) on the line's device, and rows in the
  heard log (the same log the ears write, unless `--log-dir` names
  another): a `saying` row when a line starts to play, and a `said` row
  when it ends or is dropped.
- **A seat uses it** like this:

      mouth devices          # the names --device takes; the current one marked
      mouth say --device "<device>" --hold turn-end "Hello."

  `say` prints the line's id (UTT); `mouth amend UTT TEXT`,
  `mouth retract UTT` and `mouth stop` change course. `amend` replaces any
  line not yet heard, a held one included, and keeps its place. It refuses a
  line being heard; retract that one and say it again. A line the player has
  taken but not yet made audible is cut for a supersede only when the
  `Supersedes` is trusted (a local drop, as `amend` writes, or signed v2) or
  comes From the line's own sender. Any other supersede of it plays after
  it, as news (`src/mouth/player.py` `_superseded`).

## How it works

- **The queue** is a maildir: `~/.mail/agents/mouth` for the live mouth,
  unless `$MOUTH_MAILBOX` names another. The box and its `tmp/`, `new/`
  and `cur/` are 0700, like every agent box, and a box found with another
  mode is set back to 0700 when the mouth next uses it. `new/` holds lines to
  speak. `cur/` holds the line playing and the lines done, each flagged S
  (spoken), T (not spoken) or P (passed). Mail to `mouth@<host>` lands in
  `new/` too, when the mail carrier delivers it there (postfix, or
  sessionmail's mail drop). Only an allowed sender's
  mail is spoken (a From on this host, or in
  `$MOUTH_MAIL_ALLOW`); any other is filed as refused, unplayed
  (`src/mouth/box.py`).
- **What a line may make the mouth do** depends on where it came from, not
  only on its From (`_trust` in `src/mouth/box.py`):
  - A **local** line, one that `mouth say`, `play` or `amend` dropped, may
    play any file, speak into a call (`call:<N>`), cut in (`now`) and
    retract the line playing. Each drop's first line is a MAC of the whole
    message, keyed by `<box>/.mouth-key`, which the first drop makes. The
    `X-Mouth-Raw: 1` header alone proves nothing. Deleting the key makes
    every queued local line count as mail.
  - A **signed** mail may do what its signature covers, as the verifier's
    own verdict states it. The mouth runs `message verify --json --trust`
    and reads the one JSON object it prints (sessionmail's
    `docs/verify-json-verdict.md`, schema 1). It never reads the mail's
    `X-Session-Signature` itself. A mail is signed only when the verdict is
    `FULL` (exit 0) or `UNBLESSED` (exit 4) and the object is well formed:
    one ASCII line, `rc` equal to the exit code, a `signature` whose
    `covered` lists one entry per name of its `h`, in order. Anything else
    is unsigned: no object, two lines, a `FAIL` or `ERROR`, a null
    `signature`, or a covered value carried as `values_b64` (not UTF-8).
    With no `message` on `PATH`, or with `$MOUTH_VERIFY_BIN` set
    at all, no mail is signed.
  - A signed ask is kept only when its field is named in the verdict's `h`
    (case as signed: an upper-case name covers nothing), is signed exactly
    once and not empty, and the value the mouth acts on equals the value
    signed. For `X-Mouth-Device`, `-Priority`, `-When` and `Supersedes`
    that is the header's value; for `X-Mouth-Extra`, the one key that set
    the effect (`device`, `priority` or `hold`); for a call box, the call
    box in the signed `To` (on v1, the verifier's own reduction of `To` to
    its local part). A v1 signature signs five fixed fields, so it covers a
    call box in `To` and nothing else. Any other ask is refused or dropped
    as if unsigned.
  - **The verdict needs sessionmail with `message verify --json`** on the
    host the mouth runs on, found as the notices find it: on PATH, else
    where uv installs it. With no `message` at all, every mail is unsigned. On a host whose `message` lacks `--json`, every
    signed ask is refused until that host's sessionmail is updated to a
    verifier that has it: a line to a call box is filed
    `untrusted`, a signed `now` plays in its turn, and a signed supersede
    of another sender's line is refused. Unsigned and local lines are not
    affected.
  - **Any other mail** still speaks, and it may play a sound from the
    sounds directory (`$MOUTH_SOUNDS_DIR`, else
    `<mouth dir>/sounds`; never made by the mouth). That directory is the
    only place a mail's file may come from, signed or not. It may not route
    into a call or ask for a file outside the sounds directory: that line is
    filed `untrusted`, unplayed, and its sender gets a notice. Its `now` (or
    a negative priority) is dropped and it plays in its turn. It may amend
    or retract only its own queued lines (the same From), never the line
    playing, and a replacement never takes a place ahead of `next`. The same
    holds for a line the player has taken but not yet made audible: only its
    own From may replace it there. Another sender's supersede of
    it cuts nothing, and plays after it, as news, and a supersede refused
    whole cuts nothing at all (`tests/mouth/test_mouth_amend_held.py`). A signed
    call line whose barge (`now`, `X-Mouth-When: now`) its signature does
    not cover waits for the user's turn.
- **The voice** is Kokoro or a cloned voice, by default from the speech
  server on `127.0.0.1:8890`. Kokoro's URL is `MOUTH_KOKORO_URL`,
  else `tts.url` in mouth.json, and it may be a hosted endpoint with a key
  ([models.md](models.md#a-hosted-speech-endpoint-instead),
  `src/mouth/backends.py`); a clone's is `VOICEMODE_MLX_AUDIO_BASE_URL`
  (`src/mouth/resolver.py`). With `--backend auto` on a Mac where Kokoro's
  server does not answer, it speaks with macOS `say` (`resolve()` in
  `src/mouth/backends.py`).
- **The device** is an exact device name, `default` (the system default
  output), `null` (plays nothing), or `call:<N>` for a live Delta Chat call
  (bare `call`: the only live call). Every line takes ONE order,
  `say`, `play` and `again` and a mailed line alike
  (`resolve_device()` in `src/mouth/__init__.py`):
  1. the line's own ask: `--device`, or a mail's `X-Mouth-Device` (or its
     `X-Mouth-Extra` `device`), or the call box the mail is To;
  2. `$MOUTH_DEVICE`;
  3. `output` in the settings file (`mouth.json`);
  4. `default`, the system default output.

  `null` at any rung is honoured and plays nothing. The line's
  `device_from` names the rung (`flag`, `mail`, `call`, `env`, `settings`
  or `default`) on its `saying` and `said` rows, so a line that played on
  the system default because nothing was set says so. A `mouth say` line
  whose caller named its device (`--device` or `$MOUTH_DEVICE`) is the
  mail it always was, so its rows carry no `device_from`; `mouth say`
  itself still prints the rung. A device that is
  absent at play gets `said reason=device-absent` and never falls back to
  another one. On a machine with no default output at all, a line that
  reached the last rung gets the same, with the detail "no default output
  device" (`src/mouth/output.py`).
- **A host that can play nothing is refused before the id.** `mouth say`,
  `mouth play` and `mouth again` ask PortAudio in the caller's own process
  before a line is queued (`src/mouth/canplay.py`). If PortAudio is not
  installed, if it sees no output device at all, or if a line on `default`
  finds no default output, they exit 1 with one line on stderr and print no
  id. The line names the cause and the fix: PortAudio's package
  (`portaudio` on Fedora, `libportaudio2` on Debian and Ubuntu), or, on
  Linux, the `audio` group, because a login outside it cannot open
  `/dev/snd`. The Linux line says how to check (`id -nG`, `ls -l /dev/snd`)
  and names the fix (`sudo usermod -aG audio $USER`, then log in again);
  mouth runs neither. `null` and `call:<N>` are never asked. A named device that is
  merely absent while other outputs exist is still queued and gets
  `said reason=device-absent` at play, as above. `--wait` is not probed: the
  waiter already hears the player's own refusal, exit 1.
  If PortAudio is installed but raises `PortAudioError` as it starts or lists
  its devices, they exit 1 with one line, `mouth: the audio system did not
  start: <PortAudio's message>`. While it asks, the probe points fd 2 at the
  null device (and puts it back after), because on Linux ALSA writes
  `ALSA lib ...` lines straight to fd 2 as PortAudio starts; the one line
  prints after. The player is never silenced this way.
- **A room line waits for the user's turn.** A line on a room device (any
  output that is not `call` or `call:<N>`) that asks no hold is held as
  `--hold turn-end` is, with the same release and the same safety net: it
  plays when the user's turn ends, or when the ears have been quiet for
  `$MOUTH_HOLD_IDLE_S` (8 s), so it never waits forever. Its `saying` row says
  `hold_from: room`. Unlike a held opener, it is never dropped as `stale`: it
  waits through the user's next turn too. A line that must interrupt uses
  `mouth say --now`; `--hold none` (`X-Mouth-When: now` on a mail) plays at
  once without cutting the line playing. A played file (`mouth play`) and a
  call line are not changed: a file plays at once, and a call line already
  waits for the call's turn. It is decided when the player reads the line,
  where its device is known, so the mail `mouth say` writes is unchanged
  (`src/mouth/box.py` `_room_hold`). To turn it off, set `"room_hold": false`
  in mouth.json, or `MOUTH_ROOM_HOLD=off`, which wins over the file; a line's
  own `--hold` wins over both.
- **The record:** `saying` is written at the first audio frame and `said`
  at the end (`src/ears/heard.py`, `src/mouth/player.py`). A line that
  never plays gets a `said` alone. `said` says what played, and if the
  line was cut, where and why. That is how the waiters tell the mouth's
  echo from the user's speech.
- **Barge-in goes by when the user spoke**, not when the ears wrote it down. A
  word cuts a line only if it was said after the line began and the
  recogniser wrote it within 4 s of the audio
  (`$MOUTH_BARGE_FRESH_S`). A backlog the recogniser flushes late
  does not cut a line, and it does not hold or stale a held opener. Its
  `kind: barge` trace says `skip: stale` with the `lag_s`. A record with no
  audio time is judged by arrival, as before (`src/mouth/hold.py`). The
  whole of barge-in (the dip, the cut, the replay, and tuning it against
  road noise) is in [barge-in.md](barge-in.md).
- **A cut question the user answered is not replayed.** When their words
  cut a line, its unsaid rest is held for the end of their turn. If the
  sentence it was cut in ends in `?` and their turn holds 2 or more content
  words, that turn was the answer: the replay is dropped as `expired`, detail `he
  answered it`, and the seat's second notice says so. Content words are not
  filler (`hold.STOP_WORDS`), not stop asks (`hold.STOP_ASKS`), and not the
  line coming back through the mic. A cut statement, "hang on" or "wait", a
  blip with no words, and a one-word "yes" are still replayed
  (`hold.answered`, at release in `hold.check`).

  Echo is judged against only what the mic could have heard: the cut line's
  played estimate (the said row's `text_played_est`, carried on the replay
  as `resume_played`), plus `hold.CUT_ECHO_AHEAD_S` (0.5 s) of the line after
  it, because the estimate is a proportion of the audio. That margin is not
  barge-in's 3 s (`hold.AHEAD_S`), which is for a line still playing. A
  replay queued without the estimate falls back to the line up to the end of
  the sentence it was cut in. The limit: echo is judged on text alone. So an
  answer made of words the line had already played (or that lie inside the
  margin) reads the same as the line coming back, and the replay still plays.
  For "Is that the 0.2.0 publish to the new sessionkit org?", the answer
  "Yeah, the new sessionkit org" is dropped as answered when the line was cut
  early in that sentence. Cut from about 3.2 s in, once "org" was in reach, it
  reads as echo and the question is replayed.

  Two settings in
  `~/.sessionkit/voice/settings/mouth.json` tune it, each beating its variable:

  | Setting | Default | Variable | What it does |
  | --- | --- | --- | --- |
  | `resume_answer_words` | 2 | `$MOUTH_RESUME_ANSWER_WORDS` | Content words of the user's turn that answer a cut line (a whole number, 1 or more) |
  | `resume_answer_question` | `true` | `$MOUTH_RESUME_ANSWER_QUESTION` (`off`) | `false`: drop any cut line the user's turn answered, not only a question |
- **A say-again replay** plays a kept line again, from its WAV, with no
  new synthesis (`mouth.again(utt)`, `src/mouth/__init__.py`). It is a
  spoken line, not a played file: every played file is a bed, which the
  user's voice never cuts and lines mix over. The replay carries the line's own
  text, voice, agent and session, the kept WAV as `file`, and `again_of`,
  the utt it replays. It is held for the end of the user's turn, queued `next`,
  and dropped after 60 s. A played file is a `file` without `again_of`
  (`voices.played_file`, which `bed.is_file` asks), so the replay stays in
  the speech lane, is levelled as its voice is, takes `volume`, not
  `play_volume`, gets no duck, and holds a line's lead. It is not kept
  again, and when the user's words cut it, it is not resumed. Its `saying` and
  `said` rows carry `again_of`. The line is looked up by utt only
  (`audio.find`), in the mouth's own audio directory, so a replay never
  plays an arbitrary file.

  The verb is `mouth again UTT`, which the keeper runs with `--seq N`,
  `--heard TEXT` and `--volume N` from the line's said row. It prints one
  JSON line: the replay's `utt`, `again_of`, `again_from` (`audio` or
  `text`) and `replaced`. Exit 0 means it is queued, 2 is a usage error (an
  utt that is not a plain id, a bad volume), and 3 means there is
  nothing to replay.
  - **The WAV is gone** (pruned, `MOUTH_KEEP_AUDIO=0`, or cut
    before its synthesis finished): the note's text is said again in the
    note's voice, a plain TTS line with `again_from: text` and `again_why`.
    If the note is gone too, it says the `--text` in the `--voice` (and
    `--agent`/`--session`) the keeper read off the line's `saying` row.
    There is still no model turn. With neither, it exits 3, `no audio for
    UTT: WHY`.
  - **It replaces a resume of the same line**, so the whole line plays
    instead of its rest. A queued `resume_of` UTT is filed T with
    `said reason=again`. A playing one gets a stop aimed at its utt alone,
    reason `again`, so a line of any other utt is never cut. Both are quiet:
    the notice is `none: replaced by a say-again`. This happens only after
    the replay is queued, so a replay that fails leaves the resume as it
    was. A resume that has finished is left alone, and the whole line plays
    once more.
  - **The seat gets one note per replay** (`notice.again`), from the
    replay's `said` row, in place of a drop notice: played in full, cut,
    or dropped. It is one line, `Priority: non-urgent`, so it rides along
    with the seat's next wake and never wakes it, with `Mouth-Notice:
    again` and the replay's `Mouth-Utt`. It goes To the line's session box,
    else its agent box, never to an outside sender. It names the heard
    turn and seq, the line's utt, whether it played from its audio or from
    its text, and what became of it: `in full`, `cut at 2.1 s by the
    user's words`, or `dropped unheard: the user started talking again`. For example:
    `The user said "say again" (heard seq 161204): the mouth replayed your line
    3f9c1a2b7e40 from its audio, in full. No reply needed.` The quiet
    rules, `$MOUTH_NOTICE=off` and the send fence are the drop
    notice's own, and the `said` row's `notice` says where it went.
- **A dropped line** (one not played to its end) is reported back: to a
  `--wait` caller still waiting, else by notice mail to its sender, where
  it has a box. Some drops are quiet by rule: a queued line its own
  sender retracts, a resume a say-again replaced, a refused mail, a line
  the player recovers as `lost`
  an hour or more after it was asked (or at an age it cannot tell), and
  any drop while `$MOUTH_NOTICE` is `off`. The `said` row's
  `notice` says which (`src/mouth/notice.py`).
  A notice goes by sessionmail's `message send`, never straight to an MTA,
  and there is no fallback: with no `message` binary, or a refused send,
  the row reads `failed: no message binary (...)` or
  `failed: message send exit N kind K: ...`, and stderr (the service's
  log) gets one `mouth: ERROR: notice to <addr> failed: ...` line. It goes From the seat
  `mouth`, display name `Mouth`, unsigned. A caller's `CLAUDE_SESSION_*`
  key, session id and must-sign never reach the send, so a player a CLI
  `say` started never signs as its caller. `$MOUTH_SIGNER_SOCK` and
  `$MOUTH_SIGNER_PUB`, both set, give the mouth its own key, and must-sign
  turns on. Sends share one budget per burst, 15 s: at about 1.2 s a send,
  a burst of 6 notices all go.
  The default command, `message` (on PATH, else `~/.local/bin/message`,
  then `~/.local/share/uv/tools/sessionmail/bin/message`, where uv installs
  it: a launchd PATH holds neither), is the service's alone: the
  player (`mouth serve`, `mouth mail`), `mouth retract`, which writes a
  queued line's said row itself, and `mouth again`, which writes the said
  row of a resume it replaces.
  Any other process that drops a line, a script or a review rig, must set
  `$MOUTH_SENDMAIL` (a command that takes `message send`'s arguments), or
  its notice is recorded `failed: message send fenced: ...`.
- **A reply to a notice is filed, never spoken.** A notice's Message-ID is
  `<ns.uid.mouth-notice@<host>>`; a mail whose In-Reply-To or References
  names one, in that form or the older `<ns.uid@<host>.mouth-notice>`, is
  filed unspoken. The envelope sender is the seat now, not a null one, so a
  bounce of a notice comes back to the mouth's box: `box.read` files a
  delivery report unspoken too (a `multipart/report` delivery-status mail
  from any sender, or this host's `MAILER-DAEMON` or `postmaster` with the
  bounced notice inside) and logs `{"skipped": "delivery-report", ...}`. A
  report from another host is refused as any foreign mail is.
- **The player:** `say` starts one if none holds `player.lock`. On a Mac
  set up with `ears services install`, the launch agent
  `dev.sessionkit.voice.ears-mouth` keeps one running as `mouth mail`
  (`src/ears/services.py`). Install replaces a legacy player it finds
  (`dev.voicemode.mouth-mail`, `com.failmode.mouth-mail`) before ours
  loads: it boots it out and moves its plist aside, so two players never
  drain one queue.

More commands (`mouth --help` lists them all):

    mouth say --wait ...         # block until said; print it
    mouth say --next ... / --now # jump the queue / cut in at once
    mouth play FILE --start S --end S   # a file, never a URL
    mouth status                 # player, queue, what is playing

## Devices: which output, which input, how loud

`mouth devices` lists the output names `--device` takes, one per line,
then one line per live call. It also says which output is the system's
current one, how loud it is, and which input is current:

    HP Z27i
    Mac Studio Speakers    # current output, volume 62%
    External Headphones
    call:3                 # live call, sessionvoice+super
    # current input: MacBook Pro Microphone

- **Notes ride after a TAB** as `# ...`, as a live call's line always has
  (shown above as spaces). Cut each line at the TAB and you have the
  names, unchanged.
- **The current output** is marked with its volume, a whole percent of the
  device's own volume. A muted output reads `volume 62% (muted)`. A device
  with no volume control (many HDMI and Bluetooth sinks) gets no volume;
  the mark stays.
- **The current input** is the last line. It starts with `#` and has no
  name before it, because an input is not something `--device` takes.
- **Unknown is left out, never guessed.** Off macOS, or when CoreAudio does
  not answer, there is no volume and no input line. The output is still
  marked if PortAudio names a default (see below); if nothing names one,
  the list is as it always was.
- **No output device is an exit 1, never an empty list.** When PortAudio
  lists no output, `mouth devices` prints one line on stderr naming the
  cause and the fix (on Linux, often the `audio` group) and exits 1. Any
  live call lines still print, and `--json` still prints its object, with
  `"outputs": []`. With no PortAudio library at all it prints one line
  naming PortAudio and how to install it, and exits 1. If PortAudio raises
  `PortAudioError` as it starts or lists its devices, it prints
  `mouth: the audio system did not start: <PortAudio's message>` and
  exits 1. It asks PortAudio as `mouth say`'s probe does, with fd 2 pointed
  at the null device, so ALSA's `ALSA lib ...` lines never reach the
  terminal; its own line prints after fd 2 is back.
- **A current output not in the list gets no mark.** The list is
  PortAudio's and the current output is CoreAudio's. When CoreAudio names
  a device the list does not hold under the same name, the text marks
  nothing, and `--json` still names it in `current_output`.
- **`--json`** prints the same facts as one object:

      {"outputs": ["HP Z27i", "Mac Studio Speakers", "External Headphones"],
       "calls": [{"name": "call:3", "who": "sessionvoice+super"}],
       "current_output": "Mac Studio Speakers",
       "current_input": "MacBook Pro Microphone",
       "output_volume": 0.62,
       "output_muted": false}

  `output_volume` runs from 0 to 1. Anything unknown is `null`.

**Where the answer comes from.** CoreAudio, asked live each time, by
ctypes (`src/mouth/coreaudio.py`). There is nothing to install: no
Homebrew `SwitchAudioSource`, no `osascript`. PortAudio is not used for
this because it caches the device list and the default when it starts,
so its default can be stale after a switch. It is used only when
CoreAudio cannot name the output at all. The volume is the output's
virtual main volume where the device has one, else its main volume,
else the mean of channels 1 and 2.

**Why the mouth reports the input too.** The ears have no device verb,
and `mouth devices` is the kit's one device listing. A seat that wants
the current output, the current input and the volume makes one call.

## Play music or a sound

To play music or a sound file for the user, queue it on the mouth, not on a
separate player:

    mouth play FILE --device <the user's device>   # prints the utt id
    mouth play FILE --start 30 --end 90            # a section, in seconds
    mouth play FILE --volume 40                    # this file's level, 0-100

- **It ducks under speech.** A played file plays on its own lane beside the
  spoken lines. Lines mix over it, and it dips while one plays, to
  `mouth.music_duck_level` (20 of its own level) by default. `--duck` dips it
  even under a `mouth duck off` override; `--no-duck` never dips it (it
  still mixes); `--duck-level N` sets its level while ducked. The user's
  voice never cuts it.
- **Its level** is `--volume N`, else `mouth.play_volume`, else
  `mouth.volume`, in the settings cascade.
- **To stop it,** `mouth retract <utt>` with the id `mouth play` printed.
  `mouth stop` stops it too, but it also cuts the line playing and
  flushes the queue.
- `--wait` blocks until it ends and prints the result as JSON.
- **Why not a separate player:** it does not duck under the mouth's lines,
  and while the mouth holds a device such as AirPods, another player that
  opens the same device can fail with "could not open/initialize audio
  device".

## Your voice

A seat sets its voice once, and every `mouth say` line without `--voice`
speaks in it. The host's shared pick stays as it is for every seat that
sets nothing. A line's voice is the first of these that answers
(`src/mouth/voices.py`, and `mouth say --help`):

1. **The line's own:** `mouth say --voice`, or a mail's `X-Mouth-Voice`.
2. **The caller's session layer:** `mouth.voice` in
   `~/.session/sessions/<sid>/settings/mouth.json` (or `mouth.d/*.json`).
3. **The caller's seat layer:** `~/.session/seats/<seat>/settings/mouth.json`
   (or `mouth.d/*.json`).
4. **The caller's agent layer:** `settings:` → `mouth:` → `voice:` in the
   front matter of `~/.session/agent/config/<name>.md`.
5. **`$MOUTH_VOICE`, in the caller's environment.** It is skipped
   when the host locks `voice` (step 6's `_locked`).
6. **The settings file's voice** (the host's shared pick, which the phone's
   Settings app writes): `voice` in `$MOUTH_SETTINGS`, else
   `~/.sessionkit/voice/settings/mouth.json` when it exists, else the old
   `<home>/mouth/settings.json` under sessionvoice's home
   ([logs.md](logs.md#sessionvoices-home); `~/.voicemode/mouth/` in 0.1.0).
7. **`af_sky`**, the built-in voice.

`mouth voices` prints the voice your next bare `say` would get, and the step
that answered, in `"from"`: `session`, `seat`, `agent`, `env`, `settings` or
`builtin`. Steps 2 to 4 are the settings cascade that
`session settings <sid> mouth` reads, layer by layer.

**Set it once, for a seat** (the main knob). Find the seat's name in the
`"seat"` field of `session settings --json <sid> mouth`. Then add `voice` to
the seat's `mouth.json`, keeping whatever it already holds (`volume`,
`lead_s`):

    f=~/.session/seats/<seat>/settings/mouth.json
    mkdir -p "${f%/*}"; [ -s "$f" ] || echo '{}' > "$f"
    jq '.voice = "bm_george"' "$f" > "$f.new" && mv "$f.new" "$f"
    mouth voices      # "default": "bm_george", "from": "seat"

The same `voice` key in the session's `mouth.json` sets it for one session.
In the agent file's front matter (`settings: {mouth: {voice: bm_george}}`)
it sets it for every seat of that agent.

**Or by the environment** (the quick knob): `MOUTH_VOICE=bm_george`.
Set it where the seat is launched: in its launcher, or in an agent file's
`environment: variables:`. An agent's `export` in one Bash tool call is gone
by the next. It ranks below the seat's files and above the settings file,
and never past a host lock.

- **It is the caller's environment, never the mouth's own.** A mailed line
  with no `X-Mouth-Voice`, and a cut line the player replays, are voiced by
  the player. The player reads the settings file BEFORE its own
  `$MOUTH_VOICE`, because it may be running with the environment
  of whichever seat started it. So a seat's env var never beats the host's
  pick on outside mail. (With no `voice` in the settings file, the player's
  own `$MOUTH_VOICE` still answers there, as it always has.) A mail
  sender that wants a voice sets `X-Mouth-Voice`.
- **A host keeps its pick over every seat** with `"_locked": ["voice"]` in
  its settings file. No seat file and no caller's env var gets past that. A
  lock with no `voice` beside it gives a seat's lines `af_sky`.
- **Converse is a separate knob.** VoiceMode's `converse` speaks
  `VOICEMODE_VOICES`, which the mouth does not read. A seat that wants both
  in one voice sets both.

## Old environment names

The mouth's 39 settings in the environment had VoiceMode's `VOICEMODE_MOUTH_`
prefix. They are now `MOUTH_`: `VOICEMODE_MOUTH_DEVICE` is
`MOUTH_DEVICE`, and so on. The old names are **deprecated aliases**: still
read, with no warning, and to be removed by a later release. A launch agent
or a shell that still sets an old name keeps working.

| Name | Old name, still read |
|---|---|
| `MOUTH_APPLE_VOICE` | `VOICEMODE_MOUTH_APPLE_VOICE` |
| `MOUTH_AUDIO_DAYS` | `VOICEMODE_MOUTH_AUDIO_DAYS` |
| `MOUTH_AUDIO_DIR` | `VOICEMODE_MOUTH_AUDIO_DIR` |
| `MOUTH_BACKEND` | `VOICEMODE_MOUTH_BACKEND` |
| `MOUTH_BARGE` | `VOICEMODE_MOUTH_BARGE` |
| `MOUTH_BARGE_DEVICES` | `VOICEMODE_MOUTH_BARGE_DEVICES` |
| `MOUTH_BARGE_FRESH_S` | `VOICEMODE_MOUTH_BARGE_FRESH_S` |
| `MOUTH_BARGE_WORDS` | `VOICEMODE_MOUTH_BARGE_WORDS` |
| `MOUTH_BARGE_WORDS_CALL` | `VOICEMODE_MOUTH_BARGE_WORDS_CALL` |
| `MOUTH_CALL_SOCKS` | `VOICEMODE_MOUTH_CALL_SOCKS` |
| `MOUTH_DEVICE` | `VOICEMODE_MOUTH_DEVICE` |
| `MOUTH_DIR` | `VOICEMODE_MOUTH_DIR` |
| `MOUTH_DUCK` | `VOICEMODE_MOUTH_DUCK` |
| `MOUTH_HEARD_BOX` | `VOICEMODE_MOUTH_HEARD_BOX` |
| `MOUTH_HOLD_GRACE_S` | `VOICEMODE_MOUTH_HOLD_GRACE_S` |
| `MOUTH_HOLD_IDLE_S` | `VOICEMODE_MOUTH_HOLD_IDLE_S` |
| `MOUTH_IDLE_S` | `VOICEMODE_MOUTH_IDLE_S` |
| `MOUTH_KEEP_AUDIO` | `VOICEMODE_MOUTH_KEEP_AUDIO` |
| `MOUTH_KOKORO_MODEL` | `VOICEMODE_MOUTH_KOKORO_MODEL` |
| `MOUTH_KOKORO_URL` | `VOICEMODE_MOUTH_KOKORO_URL` |
| `MOUTH_LABEL_WAIT_S` | `VOICEMODE_MOUTH_LABEL_WAIT_S` |
| `MOUTH_LOG_DIR` | `VOICEMODE_MOUTH_LOG_DIR` |
| `MOUTH_MAIL_ALLOW` | `VOICEMODE_MOUTH_MAIL_ALLOW` |
| `MOUTH_MAIL_LOG` | `VOICEMODE_MOUTH_MAIL_LOG` |
| `MOUTH_MAILBOX` | `VOICEMODE_MOUTH_MAILBOX` |
| `MOUTH_MPV_SOCKET` | `VOICEMODE_MOUTH_MPV_SOCKET` |
| `MOUTH_NOTICE` | `VOICEMODE_MOUTH_NOTICE` |
| `MOUTH_PAN` | `VOICEMODE_MOUTH_PAN` |
| `MOUTH_REBUFFER_S` | `VOICEMODE_MOUTH_REBUFFER_S` |
| `MOUTH_RESUME_ANSWER_QUESTION` | `VOICEMODE_MOUTH_RESUME_ANSWER_QUESTION` |
| `MOUTH_RESUME_ANSWER_WORDS` | `VOICEMODE_MOUTH_RESUME_ANSWER_WORDS` |
| `MOUTH_RESUME_S` | `VOICEMODE_MOUTH_RESUME_S` |
| `MOUTH_SENDMAIL` | `VOICEMODE_MOUTH_SENDMAIL` |
| `MOUTH_SESSION_BIN` | `VOICEMODE_MOUTH_SESSION_BIN` |
| `MOUTH_SETTINGS` | `VOICEMODE_MOUTH_SETTINGS` |
| `MOUTH_SOUNDS_DIR` | `VOICEMODE_MOUTH_SOUNDS_DIR` |
| `MOUTH_TTS_KEY` | `VOICEMODE_MOUTH_TTS_KEY` |
| `MOUTH_VERIFY_BIN` | `VOICEMODE_MOUTH_VERIFY_BIN` |
| `MOUTH_VOICE` | `VOICEMODE_MOUTH_VOICE` |

- The new name wins when both are set.
- A name set to the empty string counts as set. An empty new name is not
  passed over for the old one. This matters for the two off switches:
  `MOUTH_SESSION_BIN` and `MOUTH_VERIFY_BIN` set to anything, `""`
  included, under either name, turn the settings cascade and the mail
  verifier off.
- `MOUTH_BARGE_WORDS_CALL` is retired under both names: the mouth
  ignores it.
- The test fence (`tests/env_fence.py`) sets or clears each fenced setting
  under both names. A review rig sets the new name (AGENTS.md): it wins.

The table is `src/ears/envnames.py`, shared with the ears
([ears.md](ears.md#old-environment-names)).

## Go deeper

- [barge-in.md](barge-in.md): talking over a line: the dip, the cut, the
  replay of the unsaid rest, one path for headphones and calls, and the
  knobs against road noise.
- [troubleshooting.md](troubleshooting.md): when a line chops as it plays.
- [models.md](models.md): Kokoro, the Qwen3-TTS clone voices, and Apple
  `say`.
- [audio-devices.md](audio-devices.md): the file that lists this Mac's
  audio devices, inputs and outputs.
- [call-tap.md](call-tap.md): speaking into a call from any app.
- [architecture.md](architecture.md): how the ears and the
  mouth fit together, in two diagrams.
- [ears.md](ears.md): the other half, which hears.
- [The mouth section of the README](../README.md#the-mouth-srcmouth):
  where the code came from.
