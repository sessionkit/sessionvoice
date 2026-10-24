# The ears

The ears are how an agent hears the user. A capture listens on one input
for as long as it runs, turns speech into text, decides when the user has
finished a turn, and writes its partials, turns and events to the heard
log. An agent does not open the microphone itself: it reads the log, or is
woken by mail. Code: `src/ears/`, `keeper/`, `bin/`.

- **In:** audio from one input: the room's microphone (the Mac's default
  input, as the installed keeper follows it, or one input it pins), or
  one leg of a call.
- **Out:** the heard log,
  `<home>/logs/conversations/heard_YYYY-MM-DD.jsonl` (the home:
  [logs.md](logs.md#sessionvoices-home)), and, where
  the mail agent runs, a mail to `heard+<device>@<host>` for a finished
  turn heard in the room. A call's turns go to the log only.
- **A seat uses it** in one of three ways:
  - subscribe to the device's box, e.g. the built-in mic's:
    `message subscription add heard+macbook`. The pager reads each turn
    mail's headers to wake the seat or let the mail ride. By mail, "hey
    NAME" wakes only a seat that `ears wake add NAME` has made a wake seat;
  - arm a waiter on the log, in the background, re-armed on every fire:
    `ears-arm <seat> [talk|alone|room]` (count first with `ears-count <seat>`);
  - opt into the `ears heard hook` ride-along, the canonical heard hook:
    after a tool call, it shows the seat a short digest of what the heard
    log has gained since the seat last looked. A seat that also gets heard
    mail runs it with `--partials-only`
    ([The heard hook](#the-heard-hook)).

## How it works

- **The capture** is `ears capture`, the same code the keeper runs as
  `python -m ears.listen capture` (`src/ears/listen/`). In the keeper's
  capture, Silero VAD tells speech from noise (`--detector silero`,
  `src/ears/listen/silero.py`; `ears capture` alone uses webrtcvad,
  `vad-silence`). A turn ends after a run
  of silence (`src/ears/listen/detector.py`), or where the speakers
  side-car names a new speaker (`--speaker-split`,
  `src/ears/listen/loop.py`): two labels in a row, or one named person's
  label after the mouth's own line, unless its words are that line's echo. Speech to text turns it into words: by
  default Parakeet on the speech server at `127.0.0.1:8890`; `final` in
  `~/.sessionkit/voice/settings/ears.json` names another engine
  (`src/ears/listen/stt.py`). The keeper asks for each turn's text as a
  re-decode of the whole turn's audio (`--turn-text redecode`). With
  `"stt_hint": true` in ears.json (or `EARS_STT_HINT=1`), that re-decode
  carries a prompt naming the wake file's seats as a bare list ("Carol,
  Dave."), so whisper hears the name, not a sound-alike; partials never
  carry it, a transcript that is only the prompt's words is dropped as an
  echo, and Parakeet ignores it. No re-decode, no hint: under
  `--turn-text joined` it never applies. It is off by default. `ears wake
  add SEAT --hint NAME` sets how a name is spelled there (in capitals
  for an acronym, say).
- **The heard log** is append-only, one JSON line per partial, turn and
  event (a heartbeat, a voice onset, a start, a stop and others), beside
  the mouth's `saying` and `said`. Each line gets a `seq` as it is written.
  Its base is sessionvoice's home: `$SESSIONVOICE_HOME`, else
  `~/.voicemode` on a host that already keeps the log there, else
  `~/.sessionvoice` (`src/ears/home.py`, [logs.md](logs.md#sessionvoices-home)).
  On a fresh host that also runs VoiceMode's `converse` as a heard guest, set
  `SESSIONVOICE_HOME=~/.voicemode` (or `VOICEMODE_BASE_DIR`), so that both
  write one log; otherwise whichever writes first decides. `ts` is when a line was written. On the mic or a
  call, a partial, turn, voice edge or heartbeat also carries `audio_ts`,
  when its audio ended, and `lag_s`, how late it was written. A heartbeat
  adds `stt_backlog_s`, the age of the oldest chunk still waiting on the
  speech to text, and the mic's `input_overflows`. A recogniser that falls
  behind shows there (`src/ears/lag.py`), and when a partial or heartbeat
  is over 2 s late (`$EARS_LAG_WARN_S`) the capture writes one
  `recogniser-lag` event for the episode. [logs.md](logs.md) gives each
  kind of line with its fields, and every log file the ears and the mouth
  write.
- **The turn mail** is sent To and From `heard+<device>@<host>` by
  `keeper/ears-mail.py`, through sessionmail's `message send`
  (`$EARS_MAIL_MESSAGE`, else `message` on PATH, else where uv installs
  it: `~/.local/bin/message`, then
  `~/.local/share/uv/tools/sessionmail/bin/message`), never straight to an
  MTA, and with no fallback to one. A launchd PATH holds neither uv
  directory, so a lookup on PATH alone failed every send. A
  `$EARS_MAIL_MESSAGE` that is set is looked up on PATH only: what is
  named is what runs. With `message` in none of those places, each send
  fails with exit 127 and an `err` that names every place looked. It goes unsigned, display name `Ears`, with
  any inherited `CLAUDE_SESSION_*` and must-sign dropped;
  `$EARS_MAIL_SIGNER_SOCK` and `$EARS_MAIL_SIGNER_PUB`, both set, sign it.
  The send mints the Message-ID, and the fire log keeps the one it
  returned; a failed send is a fire-log row with `exit` and `err`, plus one
  `ears-mail: ERROR: send to <seat> failed, exit <code>: <why>` line on
  stderr (the launchd log), and the keeper keeps running. Under `--to seats` it is one send per seat, so one
  dead box does not silence the rest. With no `sendmail` key in
  sessionmail's settings file (`~/.sessionkit/mail/settings/mail.json`)
  and no `SESSIONMAIL_SENDMAIL`, sessionmail (0.2.0 and later) delivers
  by its mail drop: it writes each mail straight into your own mailbox,
  as you, with no mail server. That is how the launch agents deliver on
  a Mac with no postfix. The drop never makes a mailbox, so the `heard`
  box must exist first: `message setup --box heard` makes it. A turn
  mailed to a box that is not there is refused, nothing is delivered,
  and the turn is lost. The drop puts the host in the keeper's bare
  From, and the pager carries an unsigned heard turn as a call, not as
  plain mail. A fire-log row with `exit 0` proves only that the turn
  reached the `heard` box; it says nothing about any agent. To send by
  postfix instead, set `"sendmail": "/usr/sbin/sendmail"` in that file:
  the launch agents do not see a `SESSIONMAIL_SENDMAIL` set in your
  shell. The turn mail is what pages a subscribed agent: an agent hears
  a heard turn only once it subscribes to `heard+<device>`. A heard line
  on its own pages nobody; only a reader of the log (a waiter, the
  hook) sees it. `--to device` mails the room's turns to `heard+<device>`;
  a Delta call's turns go to the call's own box (the next item). Turns heard
  on a BlackHole input (the call tap), the mouth's echo and empty turns are
  skipped.
- **The call-box mail** (`--to device`, `--calls on`, the default): each
  FINISHED `turn` of a Delta call (source `call`, device `call:N`) is
  mailed To `calls+<bot>-<N>@<host>`, the box the mouth reads. It is one
  send per turn, and the turn never goes to `heard+<device>`, so a call
  turn marks no room line answered. The box carries both directions: the
  caller's turns come in from this mailer, and a seat's replies go To the
  same box, where the mouth says them into `call:N`. Only the caller's
  `turn` rows are mailed; the mouth's `said` rows are not, because the
  line's own mail is already in the box.
  - **The bot** is learned from the call's own leg. The mailer sends
    `{"probe": true}` to each `call-ears.sock` (1 s each) and reads back
    `{"agent", "profile", "calls": [N...]}`. The bot is `agent`, or
    `<agent>-<profile>` when `profile` is set, so `calls+ivy-public-17` is
    bot `ivy-public`, call 17. It probes the first time it sees a
    `call:N` in any row (a partial is written while the call is live),
    again at each capture's `listen-started` (a call id is the bot's
    message id, so another bot's later call can reuse one), and again for
    a turn whose call it does not know. It keeps `{N: bot}` after hang-up,
    so the last turn still finds its box, and saves the newest 256 in
    `--state` as `call_bots`, so a restart knows them too. When two legs
    list the same N, the first wins (the legs are sorted, unless
    `$EARS_CALL_SOCKS` names them: then in its order). A turn whose bot no
    leg names is skipped (`call-bot`) and the mailer goes on.
  - **Where the legs are:** `$EARS_CALL_SOCKS` (colon-separated; old name
    `VOICEMODE_CALL_EARS_SOCKS`), else `call-ears.sock` in sessionphone's
    root (`$SESSIONPHONE_ROOT`, default `~/.sessionphone`) and in each
    `~/.<agent>/delta-chat`, each `[/<profile>]/call-ears.sock`. So a Delta
    bot kept in sessionphone's root (`~/.sessionphone[/<profile>]`) gets
    call-box mail too, as well as one under `~/.<agent>/delta-chat`. The
    one leg left out is sessionphone web's, `<sessionphone root>/web/`: its
    server mails its own web turns, and its probe would name an agent that
    collides with a Delta bot's box. A Delta profile named `web` in
    sessionphone's root has that path and is skipped as a web leg.
  - **The mail:** To `calls+<bot>-<N>`, From `ears` (`--calls-from LOCAL`),
    display name `Ears`, unsigned, with exactly four headers: `Ears-Kind:
    turn`, `Ears-Seq` (the row's `seq`, when it is a whole number),
    `Call-Id: <N>` and `Call-Bot: <bot>`. There is no `Ears-Rule`, so a
    call-box mail wakes no rule; the other `Ears-*` headers of a device mail
    are not sent. The subject is `<speaker>: ` when the row has a speaker,
    then the text, cut to 56 characters and made safe, or `a turn` when
    nothing is left. The body is the whole heard row as one JSON line.
  - **Not mailed:** a turn that is wholly the mouth's own line heard back
    (the echo), and an empty turn. `--calls off` skips every call turn
    again, as before this release (`skip: call`). `--to seats` is
    unchanged: it still mails a call turn to the seats it is for (rule
    `call`) and probes no leg, whatever `--calls` says.
  - **The cursor** is the mailer's one `--state` offsets: device mail and
    call-box mail move together, and a restart reopens yesterday's log, so a
    turn just before midnight is not lost. A fresh state starts at the end
    of the log and replays nothing. A crash in mid-chunk can send that
    chunk's turns again; `Ears-Seq` names each one.
  - **If a hand-made job already mails call turns** to `calls+<bot>-<N>`, it
    and this mailer will both send, so retire one in the same step; two
    writers put every turn in the box twice. A script of that kind may also
    mail the echo turns this one drops.
- **Say again:** a turn that is wholly one say-again phrase asks for the
  last line again, with no model turn (`again_target` in
  `keeper/ears-mail.py`). The keeper splits the user's words with its own
  tokeniser and strips, from the two ends only, every seat's names, the
  openers and `sorry`, `please`, `um`, `uh`, `er`, `oh`, `ok`, `okay`,
  `could you` and `can you`. What is left must be one phrase from the
  seat's `again` list, word for word. "Sorry, say again?" and "Hey Alice,
  pardon?" match. "I'll say again that the build is green" and "Say
  again, what was the second option?" do not, and keep today's verdict.
  The line it asks for is the newest heard line of the one seat the turn
  names, or, if no seat is named, of whoever spoke last. A heard line is a
  speech line, not a bed, with `played_s` above 0, that ended by the
  turn's end (a line the user's words cut counts) and within the seat's
  `again_window` seconds of the turn's start. Whether the user answered it
  does not matter. A replay (`again_of`) or a resume (`resume_of`) stands for
  its original. It is not a say-again when two seats are named, a line is
  still playing, the turn is the seat's own voice, a film plays and no seat
  is named, or the seat has `again` off. The verdict is checked before
  every other rule. The keeper then runs `mouth again <utt>` (its
  `--mouth` command; `$EARS_MOUTH`, else its own python's `-m mouth`)
  with the turn's seq and words, the level the line played at, and the
  line's text, voice, agent and session. The mouth has 5 s. If it exits
  0, the Ears-Rule entry is `<seat>@<host>=again`, which calls nobody, so
  the seat is not woken; the mouth replays the line and sends the seat
  one note, which rides. If the mouth fails, times out or has nothing to
  replay, the entry is `<seat>@<host>=call` instead, so the seat wakes and
  says it again, and stderr gets an `again-failed` skip line. Under
  `--dry-run` the command is printed on stderr and not run. Under
  `--to seats` a say-again mails no turn to the seat (a direct mail would
  wake it); a failed one is mailed as a call. It marks nothing answered and lights no seat. When the user's
  reply to the replay is mailed, the replay and its original are both
  marked answered. The wake rules' defaults (`keeper/defaults/wake.json`;
  overridable per seat in `~/.sessionkit/voice/settings/wake.json`) are
  `"again": ["say again", "repeat that", "pardon", "come again", "say
  that again"]` and
  `"again_window": 120`. `"again": []` or `false` turns it off.
- **The waiter:** `ears-arm` runs `heard-first` (`bin/heard-first`),
  which exits when a rule matches. `bin/ears-turn SEQ` then prints the
  first whole turn at or after that seq.
- **The keeper** (`keeper/ears-keeper.sh`, launch agent
  `dev.sessionkit.voice.ears-keeper`) keeps the capture on the right input as
  AirPods come and go. By default it also runs an ear for each live Delta
  call (`keeper/call-ears`), and turns the room's capture off while a call
  is live. `listen: false` in ears.json stops every ear and starts none
  until it is `true` again, whether the keeper follows the default input
  or is pinned to a device. Off stops the room's capture, the
  call ears and every call's capture (`--source call:N`), and `bin/call-tap`'s
  far-end capture. call-tap's passthrough, the user's own voice out to the
  call app, keeps running ([call-tap.md](call-tap.md)). The keeper notes
  and mails each change once: `ears: listening OFF by ears.json`, then
  `ears: listening ON by ears.json`. On `true`, the call ears start again
  and give every live call an ear, and the room's capture comes back. A
  keeper started with `listen: false` starts no ear and notes that once.
  The call ears' own STOP file (`$CALL_EARS_DIR/STOP`) is still the
  human's stop: listening on does not start them while it is there. When a capture
  writes `recogniser-lag`, the keeper mails it, at most once every
  `EARS_LAG_MAIL_GAP` seconds (600); it restarts nothing. A capture that
  looks alive but hears nothing is DEAF, and the keeper reports it one way:
  one mail when it goes deaf and one when it hears
  again, with nothing in between. [When the ears go
  deaf](#when-the-ears-go-deaf) gives every cause and its mail. The
  keeper judges its room capture by its heartbeats, not by its pid. A
  capture that ends by itself having heard nothing is restarted after
  30 s, doubling to 5 min, not at once; on a new input it starts at once.
  Stopping a capture escalates from its STOP file to SIGTERM to SIGKILL,
  so a wedged capture cannot outlast it. On SIGTERM the capture first
  writes the stack of every thread to stderr, which is
  `ears-keeper-capture.log`, so a wedged capture shows where it was
  stuck; then it stops as before. SIGUSR1 writes the same stacks and
  leaves the capture running. A watchdog thread in the capture
  (`src/ears/listen/watchdog.py`) watches the frame loop and the stop
  request from outside the loop. If no audio frame is processed for 90 s
  (`WEDGE_S`), or a STOP or SIGTERM is not honoured within 10 s
  (`STOP_GRACE_S`), or the process is still alive 10 s after the capture
  finished, it ends the capture itself. It writes every thread's stack to
  stderr and a `capture-wedged` row to the heard log, which it skips when
  another writer holds `heard.lock`. It SIGKILLs the capture's streaming
  child, then exits 3. So a wedged capture ends itself before the
  keeper's TERM and its 300 s kill, which stay as the second line. The
  watchdog runs only in a capture launched from the command line. A
  program that calls `capture()` itself gets none, unless it passes one.
  Run as `python -m ears.listen`, a capture that has finished flushes its
  output and exits at once, without waiting for a speech to text call
  still in progress. The mic is closed by aborting its stream, and the
  capture waits at most 2 s for that, so `listen-stopped` is written even
  when the device never answers.
  A restarted keeper adopts a
  running capture only if it still beats, and ends any keeper loop its
  pidfile names. The capture is launched with no
  shell left waiting on it, so no stray `ears-keeper.sh` outlives a restart.
  `bin/call-tap` puts a call from any app in.
- **The engine alone:** `ears listen` (PCM on stdin) and `ears file` run
  the staged engine (an STT backend, with `src/ears/vad.py` and
  `src/ears/turns.py` as asked) and write the event stream of
  [events.md](events.md) to stdout, not the heard log.

Set up on a Mac:

    ears install                         # fetch the Silero VAD model
    ears services install --agent NAME   # speech server, mouth, keeper and
                                         # mail agents; NAME becomes a wake seat

With neither `--agent NAME` nor `--no-agent`, and no seat in the wake file,
install refuses (exit 2, nothing written): the mail would wake nobody.
`--no-agent` installs anyway, with that warning; a wake file that already
names a seat is not refused.

The speech server is the mlx-audio server on `127.0.0.1:8890`. Install
uses one that is already running and says it is mlx-audio, and starts
none; when there is none, it installs its own,
`dev.sessionkit.voice.mlx-audio`. It never starts a second server and never
replaces another process on `:8890`. `--dry-run` says which it would do.
A legacy mail or mouth job (`dev.voicemode.ears-mail`,
`dev.voicemode.mouth-mail`, `com.failmode.mouth-mail`) is replaced: it is
booted out and its plist is moved aside, never deleted.
The README's Requirements give every case.

The proof of an install: say "hey NAME", and NAME wakes and answers. A
heard line alone proves only the capture. The README's Install section
says how to opt a seat into the hook.

## The heard hook

`ears heard hook` is the canonical heard hook: the one command this project
documents and tests for a seat to ride along on the heard log. A seat opts
in by running it on `PostToolUse` and `PostToolBatch`, as
`ears heard hook || exit 1` (the README's Install section has the snippet).
`ears heard hook --help` prints its usage.

- **What it shows.** After each tool call, what the heard log gained since
  this seat last looked: each finished turn, the partials of a turn still
  being spoken (joined, as a preview), the shown events (wake word, end
  word, barge-in, device change, call start and end) and other seats' said
  lines. Quiet events (heartbeat, start, stop, voice edges) and the seat's
  own speech move its cursor and show nothing. A seat with no cursor yet
  starts at the end of the log.
- **Partials-only.** A seat that gets heard mail (it subscribes to
  `heard+<device>`) already gets each finished turn by mail. Run the hook
  with `--partials-only` there, as `ears heard hook --partials-only || exit 1`:
  it then drops every finished turn, with the partials that turn covers,
  and carries only the partial preview, the shown events and the said
  lines. The cursor still moves past what it dropped, so no later run shows
  it. The waiter (`ears-arm`) never reads this setting.

Partials-only is read from the first layer that is set:

| Layer | How | Values |
|---|---|---|
| CLI | `--partials-only` / `--no-partials-only` | the last one given wins |
| environment | `EARS_HEARD_PARTIALS_ONLY` | on: `1`, `on`, `true`, `yes`; off: `0`, `off`, `false`, `no` |
| ears.json | `"heard_partials_only"` | `true` or `false` |
| default | | off |

- The environment value is matched in any case, trimmed. Unset or empty is
  not set, so ears.json decides. Any other value is the default, off. The
  hook never fails on a value.
- ears.json is `$EARS_CONF` when it is set and not empty, else
  `~/.sessionkit/voice/settings/ears.json`. A value that is not `true` or
  `false` is off. No file, a file that cannot be read, or one that is not a
  JSON object is not set. It is read on every run, with no restart.

The hook's other settings:

- `EARS_HEARD_HOOK=off` (or `0`, `false`, `no`) silences it without editing
  the settings file.
- `EARS_HEARD_BUDGET` is the token budget for one run (default 400), and
  `EARS_HEARD_MAX_LINES` caps the lines; `--budget` and `--max-lines` win
  over them. Past the budget the last line says how many more are waiting,
  and the next run shows them.

## Recording

**Off by default: the ears keep no audio unless someone turns recording
on.** The default is off so that recording happens only with the informed
consent of whoever runs the system. With recording off, a capture writes
text to the heard log and no audio file anywhere.

**While recording is on, every voice the mic hears is kept, guests and TV
included.** The ears do not choose whose voice to keep.

One switch, `record`, decides for every path that keeps captured audio on
its own: each turn's audio ([below](#kept-turn-audio)) and the missed-reply
clip ([logs.md](logs.md)). Each setting is read from the first layer that is
set:

| setting | capture flag | environment | `ears.json` key | default |
| --- | --- | --- | --- | --- |
| `record` | `--record` / `--no-record` | `EARS_RECORD` | `"record"` | off |
| `record_days` | none | `EARS_RECORD_DAYS` | `"record_days"` | 7 |

- `ears.json` is `$EARS_CONF` when it is set and not empty, else
  `~/.sessionkit/voice/settings/ears.json`, the file that holds the other ears settings
  (`settings/ears.schema.json`).
- `EARS_RECORD` takes `1`, `true` or `on`, or `0`, `false` or `off`, in any
  case. The file's `record` takes a JSON `true` or `false`. An empty
  variable counts as not set.
- **A value that is not one of those keeps nothing.** That layer still
  wins, with recording off, and a warning names it (`EARS_RECORD=yes`,
  `"record": "on"`). A garbled switch never records.
- A missing file, a file that will not parse or a key that is not there
  sets nothing: the next layer decides.
- `record_days` is how long kept audio stays, in whole days; `0` keeps it
  forever. A value that is not a whole number of 0 or more is ignored with a
  warning, and the next layer (or 7) is used. It applies to both kinds of
  kept audio: each turn's audio and the missed-reply clips.
- **Nothing is pruned while recording is off.** Each prune reads the switch
  when it runs. It deletes only when `record` is on then and `record_days`
  is more than 0. With recording off, audio already on disk stays there
  until recording is on again or someone deletes it. Each prune deletes only
  the files the ears wrote, in their own folders: turn audio in
  `<home>/audio/<YYYY-MM-DD>/`, and `missed-reply_*.wav` in
  `<home>/state/missed-reply/`.
- **The file is followed live.** A capture checks the switch as each turn
  or clip is about to be written, so a change to `ears.json` lands at the
  next turn or clip with no restart. The flag and the environment are fixed for the life of
  the capture. When the switch turns off, an open clip is dropped unwritten.
- **The keeper never passes `--record`.** Its room capture inherits
  `EARS_RECORD` from the keeper's own environment and reads `ears.json`
  itself. Each capture start notes the result in `ears-keeper.log`, as
  `recording: off (default), retention 7 days (default)`, and each
  capture's `listen-started` event carries `record` and `record_from`.

The command:

    ears recording status [--json]   # on or off, which layer decided, the retention
    ears recording on [--days N]     # "record": true (and "record_days": N) in ears.json
    ears recording off               # "record": false in ears.json

- `status` names the winning layer: `cli`, `env`, `file` or `default`. It
  runs in your shell, so it shows your environment, not the keeper's; the
  keeper log's start note shows the keeper's. `--json` prints one object
  with `record`, `record_days`, `record_notice` and `record_spoken_notice`,
  each with its `source`, plus `file`, `warnings`, and `captures` (the
  running captures started with a flag).
- `on` and `off` write the key in one atomic write, keeping every other
  key in the file. A file that is not a JSON object is refused and left as
  it is. When a higher layer wins over what was written, the command says
  so, naming the layer and its value, and still exits 0: `wrote record:
  false to ..., but EARS_RECORD=1 in this environment wins: recording stays
  ON`. A running capture started with `--record` or `--no-record` is named
  the same way.
- `on` and `off` see only the environment of the shell they run in. A
  capture started with `EARS_RECORD` set (the keeper's, if its environment
  has it) keeps to that, not the file, so both commands end by saying so.
  The keeper log's start note shows what the room capture resolved.
- `on --days 0` turns recording on and keeps the audio forever, in one
  command.

### Kept turn audio

While recording is on, the capture keeps each final turn's audio as one
WAV file. **Every turn is kept, whoever spoke: every voice the mic hears,
guests and TV included.**

- The file is `<home>/audio/<YYYY-MM-DD>/<HHMMSS-mmm>-<n>.wav`, beside the
  heard logs: 16 kHz mono 16-bit, mode 0600, in 0700 folders. `n` is 1, or
  the next free number when two turns close in the same millisecond. It is
  written to a temporary name and then moved into place, before the turn's
  line, so the line can name it.
- It runs from the turn's start (`t0`, the pre-roll included) to the moment
  the detector closed the turn, so it ends a little after `t1`. A turn
  longer than 120 s keeps its first 120 s, and its line says
  `audio_truncated: true`.
- The turn's line in the heard log carries `audio` (the path) and `audio_s`
  (its length in seconds), and the turn's heard mail carries an
  `Ears-Audio: <path>` header ([logs.md](logs.md#turn-a-finished-turn)).
- A turn the recogniser heard no words in (`turn-discarded`) keeps no
  audio. Nothing is held while recording is off. A turn that opened while
  recording was off keeps nothing, even if recording is on when it closes:
  the next turn is kept. Recording off when the turn closes means no file.
- **Retention.** At each capture start, and then at most once an hour, the
  capture deletes the date folders older than `record_days` (7 by default;
  `0` never deletes), but only while recording is on. It touches only `<home>/audio/`, only folders named
  like a date, and in them only the files it names this way. A folder is
  removed only once it is empty. The missed-reply clips, the mouth's audio
  and anything else are never touched by it.
- **Disk:** 32 kB per second of speech, about 1.9 MB a minute or 115 MB an
  hour. Only turns are kept, never the silence between them, so a quiet day
  costs far less.
- A file that cannot be written (a full disk, a folder it may not use) is
  reported once, as a `turn-audio-error` event. The turn still lands, with
  no `audio`.

**Not covered by the switch, on purpose:**

- `ears speakers record` and `enrol`, which record a person reading lines to
  learn their voice, and `ears eval`, which cuts clips from files already on
  disk. A person runs each by hand, and typing the command is the consent.
- **Call recordings.** The call bot writes them, not this repo. It reads the
  same `record` and `record_days` by the same rules, and two settings of
  its own from the same file: `record_notice` (whether a call states its
  recording status in the chat; `EARS_RECORD_NOTICE`) and
  `record_spoken_notice` (whether a recorded call starts with a spoken
  notice; `EARS_RECORD_SPOKEN_NOTICE`). Both default to on, and a value that
  is not valid counts as on. The ears only report these two in `ears
  recording status`.

## When the ears go deaf

**Lid shut: the built-in mic is off in hardware; use Bluetooth buds or a
USB mic.** A shut lid disconnects a MacBook's own mic, but it still hands
over frames, every sample 0, so a capture on it looks alive and hears
nothing. The keeper follows the Mac's default input, so the buds or the USB
mic must be that input.

A capture that runs and hears nothing is DEAF. The keeper reports every
cause of that through one path (`deaf_set` and `deaf_clear`,
`keeper/ears-keeper.sh`), on two channels: `room`, its own capture of the
room's mic, and `speakers`, the speakers side-car, which can be deaf while
the room hears. Each episode is two mails: one when it opens and one when
it ends.

| cause | DEAF when (default; env) | the mail it sends | `ears-deaf` text |
| --- | --- | --- | --- |
| `model` | no Silero model file | `ears: DEAF, no Silero model: run ears install EOM` | `room: model: PATH` |
| `lid` | the input to follow is the built-in mic and the lid is shut | `ears: DEAF, lid shut, built-in mic off, holding at HH:MM EOM` | `room: lid: shut` |
| `stt` | the capture stopped twice in a row because speech to text failed, with nothing heard between (`EARS_STT_DEAF` 2) | `ears: DEAF, STT failing (CODE) at HH:MM EOM` | `room: stt: CODE` |
| `beat` | no heartbeat from the capture for 120 s (`EARS_BEAT_STALE`) | `ears: DEAF, no heartbeat since HH:MM on DEVICE EOM` | `room: beat: since HH:MM` |
| `zeros` | 4 heartbeats in a row with `peak` 0 (`EARS_ZERO_BEATS`) | `ears: DEAF, mic gives only zeros on DEVICE at HH:MM EOM` | `room: zeros: 4 beats of peak 0` |
| `cover` | 20 of the side-car's mic rows in a row with 0% of the line covered (`EARS_SPEAKERS_DEAF_LINES`) | `ears: speakers DEAF, 0% of N lines on DEVICE EOM` | `speakers: cover: N` |
| room recovery | its last cause no longer holds | `ears: hearing again on DEVICE at HH:MM EOM` | `ears-hearing`: `room: hearing again, was CAUSE for DURATION` |
| speakers recovery | a mic row with some of its line covered | `ears: speakers hearing again at HH:MM EOM` | `ears-hearing`: `speakers: hearing again, was cover for DURATION` |

- `CODE` is the HTTP status in the capture's error, or `?` when it has
  none. `DEVICE` is cut to 20 characters, and to 15 in the `zeros` mail.
  With no device known, the room's recovery mail reads `ears: hearing
  again at HH:MM EOM`. `N` is the run that tripped it (20). `HH:MM` is when
  the keeper mailed, but in the `beat` mail it is the last heartbeat (or,
  when none came, when the keeper took that capture on). Every subject fits in 64
  characters.
- **One mail each way, per channel.** A cause that is already open says
  nothing again. A second cause during an episode is a keeper-log line and
  an `ears-deaf` event, never a second mail. When several causes hold, the
  first of `model`, `lid`, `stt`, `beat`, `zeros` names the episode, and
  the recovery mail comes when the last of them clears.
- **No restart mails while the room is DEAF.** `ears: capture restarted on
  DEVICE` and `ears: capture restart on DEVICE FAILED` are only noted in
  the keeper log then; the DEAF mail is the one mail.
- **`beat`** reports at 120 s and does not kill: a capture that is slow but
  alive can go that long without a heartbeat. None for 300 s
  (`EARS_BEAT_KILL`) is a wedge: the keeper stops the capture and starts a
  fresh one, whose first heartbeat is the recovery.
- **`stt`** ends at the room capture's next partial or turn with text: the
  speech to text answered.
- **`zeros`** is a mic that hands over only zeros, never a quiet room (a
  quiet room's `peak` is low but not 0). The keeper reopens that capture
  once, then holds, and a heartbeat with sound ends it.
- **`lid`, the hold, as built.** The built-in mic is the input whose
  `transport` is `builtin` in `devices.json`, so an Air's works. When two
  inputs are `builtin` (a headset on the jack is one too), the one the
  name pattern names wins (`MacBook ... Microphone` or `Built-in
  Microphone`, `EARS_BUILTIN_PAT`), else the first. While that file is
  stale, the pattern alone finds it; `EARS_BUILTIN_MIC` overrides both.
  The keeper reads the lid only while the built-in is the input to follow,
  at most every 5 s (`EARS_LID_EVERY`); a Mac with no lid counts as open.
  With the lid shut it starts no capture. It stops one on the built-in at
  once, and one still on the input the Mac left once the move has held
  `EARS_STABLE` loops (2), so a one-loop flap of the default input does not
  cut a capture that hears. It holds: it does not go looking for another
  input the Mac did not make the default. Its mail replaces the `ears: mic
  ->` mail, and a `mic ->` change during the episode is only noted. The
  first heartbeat with sound once the hold is over (the buds came back, or
  the lid opened) is the one recovery mail.
- **`cover`** says nothing while the room is DEAF or no room capture runs:
  the side-car hears only while the keeper's capture runs, so its 0% rows
  are then a consequence, not a second fault. A row from a mic that gives
  only zeros is covered and does not count. A speakers episode that opened
  before such a mute stays open, silently, until a covered row arrives.
- The heard log gets one `ears-deaf` event as an episode opens, one for
  each new cause during it, and one `ears-hearing` as it ends
  ([logs.md](logs.md)). The keeper log has a `DEAF (CHANNEL): CAUSE` line
  and a `hearing again (CHANNEL)` line.

## Old environment names

Thirteen of the ears' settings had VoiceMode's `VOICEMODE_` prefix. They now have
names of their own. The old names are **deprecated aliases**: still
read, with no warning, and to be removed by a later release.

| Name | Old name, still read |
|---|---|
| `EARS_LISTEN_STT_URL` | `VOICEMODE_LISTEN_STT_URL` |
| `EARS_LISTEN_STT_MODEL` | `VOICEMODE_LISTEN_STT_MODEL` |
| `EARS_LISTEN_STT_KEY` | `VOICEMODE_LISTEN_STT_KEY` |
| `EARS_LISTEN_SILENCE_S` | `VOICEMODE_LISTEN_SILENCE_S` |
| `EARS_LISTEN_VAD_AGGRESSIVENESS` | `VOICEMODE_LISTEN_VAD_AGGRESSIVENESS` |
| `EARS_SILERO_MODEL` | `VOICEMODE_SILERO_MODEL` |
| `EARS_CALL_SOCKS` | `VOICEMODE_CALL_EARS_SOCKS` |
| `EARS_LAG_WARN_S` | `VOICEMODE_EARS_LAG_WARN_S` |
| `EARS_HEARD_EVENT` | `VOICEMODE_HEARD_EVENT` |
| `EARS_HEARD_CALLER` | `VOICEMODE_HEARD_CALLER` |
| `EARS_HEARD_HOOK` | `VOICEMODE_HEARD_HOOK` |
| `EARS_HEARD_BUDGET` | `VOICEMODE_HEARD_BUDGET` |
| `EARS_HEARD_MAX_LINES` | `VOICEMODE_HEARD_MAX_LINES` |

- The new name wins when both are set.
- A name set to the empty string counts as set. An empty new name is not
  passed over for the old one.
- `EARS_LISTEN_STT_URL` and `EARS_LISTEN_STT_MODEL` pin one capture's engine.
  They are not `EARS_STT_URL` and `EARS_STT_MODEL`, which `ears services
  install` reads.
- These keep their `VOICEMODE_` names, because VoiceMode reads them too or
  owns them: `VOICEMODE_AGENT` and `VOICEMODE_SESSION_ID`, and the
  voice-clone settings the mouth reads from VoiceMode's config.
- `EARS_HEARD_PARTIALS_ONLY` is new, and has no old name.
- `VOICEMODE_BASE_DIR` is the old name of `SESSIONVOICE_HOME`, and it is
  still read after it ([logs.md](logs.md#sessionvoices-home)).

The table is `src/ears/envnames.py`. It holds the mouth's 39 renamed settings
too (`VOICEMODE_MOUTH_<X>` is now `MOUTH_<X>`): see
[mouth.md](mouth.md#old-environment-names).

## Go deeper

- [events.md](events.md): the event stream the stages read and write:
  ready, partial, final, vad, turn, preview, error and eof.
- [logs.md](logs.md): the heard log's lines and fields, and every log
  file, with what each shows.
- [models.md](models.md): the models the ears use (Parakeet and Silero
  by default, and the opt-in ones), and where each one runs.
- [audio-devices.md](audio-devices.md): the file the keeper's input
  watcher writes, naming this Mac's devices and defaults.
- [call-tap.md](call-tap.md): a call from any app into the ears, both
  ways.
- [architecture.md](architecture.md): how the ears and the
  mouth fit together, in two diagrams.
- [troubleshooting.md](troubleshooting.md): when the mouth's lines chop.
- [The ears skill](../skills/ears/SKILL.md): the waiter's modes, and who
  wakes when.
- [bin/README.md](../bin/README.md): the waiters' rules and flags.
- [mouth.md](mouth.md): the other half, which speaks.
- [The README's Components](../README.md#components): capture, keeper,
  sounds, turn mail, waiter and calls, in depth.
- [The README's Install](../README.md#install): the launch agents, and
  the heard hook for one seat ([The heard hook](#the-heard-hook)).
