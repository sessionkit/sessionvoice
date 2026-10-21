# sessionvoice (the ears and the mouth)

sessionvoice is the distribution and the Claude Code plugin. The commands
and modules keep their names: `ears`, `ears-serve`, `mouth`, `src/ears`,
`src/mouth`.

One listening engine for coding agents and the Delta bot. It is always listening,
streams transcription, and decides when the speaker has finished.

    PCM (s16le mono) -> backend ----------> events (docs/events.md) -> stdout
                          apple | nemotron | whisper       ^
                        VAD (Silero) ----- edges ----------+
                        turn stage (silence | smart) -- turn, preview

- **Backends**, all speaking one event format:
  - `apple`: SpeechTranscriber on macOS 27, through the `apple-speech` Swift
    helper.
  - `nemotron`: Nemotron 3.5 streaming on MLX, run as a child process.
  - `whisper`: any OpenAI-compatible transcription URL, for Linux or pre-27
    macOS.
- **One VAD** (Silero, falling back to energy). It produces `vad` edges, and
  Nemotron segments on them too.
- **Turn stages:** `silence` (the bot's join rules, plus commit) and `smart`
  (Smart Turn and a Parakeet re-decode), both in-process with the audio.
- **Ducking:** music dips while the assistant speaks, then comes back. That is
  music the mouth plays itself, and your own mpv player if you turn it on
  (`$MOUTH_DUCK`); other apps keep their volume. `mouth duck off` turns it off
  for a while.
- **Barge-in:** on headphones or a call, talk over the assistant and its voice
  dips at once; a couple of your own words, or "stop", cut it off
  ([barge-in](docs/barge-in.md)).

## Requirements

- **macOS** for the always-on parts. The keeper follows devices through
  CoreAudio (it builds `keeper/input-watch.swift` with `swiftc`,
  `keeper/ears-keeper.sh:104`), and `ears services install` writes launchd
  agents to `~/Library/LaunchAgents` (`src/ears/services.py:93`). The
  `apple` backend needs macOS 27, Xcode's `swift` and a source checkout
  (`uv run ears install --apple`: the wheel does not ship `swift/`, and
  `--apple` without it refuses). The engine alone (`ears file`, `ears
  listen` with `whisper`) needs no Mac.
- **Python 3.10-3.14** (`requires-python` in `pyproject.toml`) and
  **[uv](https://docs.astral.sh/uv/)**: `make test` runs `uv run --locked`.
- **A speech server on `127.0.0.1:8890`**: mlx-audio's server, which serves
  both STT and TTS. It is the ears' default STT (Parakeet;
  `src/ears/listen/stt.py:86`, `src/ears/services.py:530`) and the
  mouth's Kokoro voices (`src/mouth/backends.py:289`). `ears services
  install` finds one or installs its own (`src/ears/speech.py`), and asks
  before it acts, in this order:
  - **Opt-out.** `final.url` in `~/.sessionkit/voice/settings/ears.json` (or
    `EARS_STT_URL` at install) names another engine, and the mouth is not
    being installed: `:8890` is not sessionvoice's to manage.
  - **Use.** Something listens on `:8890` and says it is mlx-audio: `GET /`
    returns mlx-audio's welcome line, or `/openapi.json` lists its audio
    routes. sessionvoice uses that server, whoever installed it (VoiceMode,
    for example), and starts nothing.
  - **Refuse.** Something else holds `:8890`, or holds it and does not
    answer. Install starts nothing, replaces nothing, and names the holder.
    When the ears hear through `:8890` it exits 2 and says what to run;
    when only the mouth does, it warns and the mouth uses `say`. Free the
    port, or point `final.url` at another speech server.
  - **Never a second.** Nothing answers on `:8890`, but a loaded launch
    agent runs `mlx_audio.server` (under any label, so another installer's
    job between restarts counts). Install starts nothing. When that job is
    sessionvoice's own, run `ears services uninstall --only speech`, then
    install again.
  - **Install.** Nothing on `:8890` and no such job. On an Apple-silicon
    Mac (macOS 14 or later), install builds sessionvoice's own venv,
    `~/.local/share/ears/speech/venv`, from the shipped
    `speech/requirements.lock` (`mlx-audio==0.5.8`), and loads it as the
    launch agent `dev.sessionkit.voice.mlx-audio`. `EARS_SPEECH_PY` names a
    python that already has it, and no venv is built. On any other host it
    installs nothing: it refuses and says what to run when the ears need
    `:8890`, and warns when only the mouth does.

  `--dry-run` says which of these install would take, and builds and writes
  nothing. `ears services status` has a `speech` row that names who serves
  `:8890`. `ears services uninstall --only speech` removes only
  `dev.sessionkit.voice.mlx-audio`, never a server someone else installed, and
  keeps its venv. While sessionvoice's own server runs, a re-install just
  uses it, so to upgrade it, uninstall it and install again: install
  re-applies the lock's pins to the venv it finds. The lock carries a hash
  for every pin, and install refuses an artifact that does not match it.
  Two pins, `docopt` and `webrtcvad`, ship only as source, so uv builds them,
  and the setuptools it builds them with is hash-checked too, from the
  shipped `speech/build-constraints.txt`. A new venv is checked in full. A
  re-apply checks only what it installs, not what is already in the venv, so
  to check an existing venv, remove it and install again. The server fetches no
  model at install. The first transcription downloads Parakeet, and the
  first line spoken downloads Kokoro and its voices, so each waits on a large
  download once.

  **These weights are not pinned.** mlx-audio's server takes whatever is on
  Hugging Face `main` at first use, and it has no way to ask for a revision,
  so a later first use (a new Mac, a cleared cache) can load different
  weights. `ears services status` has three `weight` rows, for
  `mlx-community/parakeet-tdt-0.6b-v3`, `mlx-community/Kokoro-82M-bf16` and
  `prince-canuma/Kokoro-82M` (the voices). Each says whether the commit the
  Hugging Face cache's `main` points at is the one sessionvoice expects
  (`SPEECH_WEIGHTS` in `src/ears/services.py`): `match`, `DIFFERS`, or
  `not cached`. It reads the cache's files and never fetches. It only reports
  drift; it does not stop or change what the server loads. A real pin, fixed
  at install, is not built yet.

  sessionvoice can promise "never two servers" only from its own side:
  VoiceMode's installer does not ask, so `voicemode service install
  mlx-audio` run after sessionvoice's install loads a second mlx-audio job,
  and one of the two crash-loops.

  Without a speech server, point the ears at another OpenAI-compatible STT
  (`EARS_LISTEN_STT_URL`, or `url` in `~/.sessionkit/voice/settings/ears.json`;
  `src/ears/listen/stt.py:105`) and the mouth at another TTS
  (`MOUTH_KOKORO_URL`, or `tts` in `~/.sessionkit/voice/settings/mouth.json`),
  a hosted one included, with `MOUTH_TTS_KEY` or the key file
  `~/.sessionkit/voice/settings/tts.key` for a keyed one: see
  [A hosted speech endpoint](docs/models.md#a-hosted-speech-endpoint-instead).
  On a Mac the mouth's `auto` falls back to `say`
  (`src/mouth/backends.py:200`).
- **ffmpeg** on `PATH`: `mouth play` (`src/mouth/backends.py:261`), the
  mouth's `.mp3`/`.m4a` output (`src/mouth/audio.py:137`), `ears file`
  (`src/ears/cli.py:92`) and `ears eval` (`src/ears/eval.py:169`).
- **SwitchAudioSource** on `PATH` (Homebrew `switchaudio-osx`): the keeper
  sets the default output with it, and reads with it only as a fallback
  (`keeper/ears-keeper.sh`): `check_output` reads the default output and the
  output list from input-watch's JSON file (`docs/audio-devices.md`) and asks
  SwitchAudioSource only when that file is missing or stale; `default_input`
  reads the legacy `input-watch.state` while the watcher is running and asks
  SwitchAudioSource only when it is not. `keeper/speak:13-17` still reads with it.
- **sessionmail's `message`** only if you use mail: the mouth's notices and
  the keeper's turn mail (`keeper/ears-mail.py`) go out by `message send`,
  never straight to an MTA. With no `sendmail` key in
  sessionmail's settings file (`~/.sessionkit/mail/settings/mail.json`)
  and no `SESSIONMAIL_SENDMAIL`, sessionmail (0.2.0 and later) delivers
  by its mail drop: it writes each mail straight into your own mailbox,
  as you, with no mail server. That is how the launch agents deliver on
  a Mac with no postfix. The drop never makes a mailbox, so the `heard`
  box must exist first: `message setup --box heard` makes it. A turn
  mailed to a box that is not there is refused, nothing is delivered,
  and the turn is lost. To send by postfix instead, set
  `"sendmail": "/usr/sbin/sendmail"` in that file: the launch agents do
  not see a `SESSIONMAIL_SENDMAIL` set in your shell.
  On a drop host, `ears services install` runs sessionmail's first-run setup, `message setup --box heard`.
  Mail to `mouth@<host>` reaches the speech queue only if sessionmail
  delivers it there (`src/mouth/box.py`). The mouth's notices go through
  `MOUTH_SENDMAIL` (`src/mouth/notice.py`): only the mouth service (`mouth serve`, `mouth mail`,
  `mouth retract`, `mouth again`) uses the default `message`; any other process
  must name its own. ears-mail's command is `EARS_MAIL_MESSAGE`, else
  `message` on PATH, else where uv installs it (`~/.local/bin`, then uv's
  tool dir; `src/ears/message_bin.py`).
- **Models.** `ears install` fetches only Silero VAD, to
  `~/.cache/ears/silero_vad.onnx`, pinned to one upstream commit and checked
  against its SHA-256 (`install()` in `src/ears/cli.py`; the pin is in
  `src/ears/vad.py`, and `docs/models.md` names it). **It does not
  fetch the Smart Turn model** that `--turns smart` needs
  (`src/ears/smartturn.py:48-72`). With mlx-audio installed, the `mlx`
  backend loads `~/.cache/ears/smart-turn-v3-mlx` if it is there, and
  otherwise `mlx-community/smart-turn-v3` from Hugging Face on first use.
  Without mlx-audio, the `onnx` backend needs `smart-turn-v3.2-gpu.onnx` at
  `~/.cache/ears/` (or the path in `EARS_SMART_TURN_ONNX`). Download it
  yourself from
  [pipecat-ai/smart-turn-v3](https://huggingface.co/pipecat-ai/smart-turn-v3).

## Components

The engine above is the core. Around it, these parts make it an always-on
pair of ears on a Mac (depth in [docs/architecture.md](docs/architecture.md)):

- **Capture** (`src/ears/listen`): `ears.listen capture` reads one input
  device and writes every partial, turn and heartbeat to the **heard log**
  (`src/ears/heard.py`):
  `<home>/logs/conversations/heard_YYYY-MM-DD.jsonl`, where `<home>` is
  `~/.voicemode` on a host that already keeps it there, else
  `~/.sessionvoice` ([docs/logs.md](docs/logs.md#sessionvoices-home)).
  On a fresh host that also runs VoiceMode's `converse` as a heard guest,
  set `SESSIONVOICE_HOME=~/.voicemode` (or `VOICEMODE_BASE_DIR`), so that
  both write one log.
  - **A speaker change ends the turn** (`--speaker-split K`): when
    another named speaker holds K partials in a row (by the speaker
    side-car's `speakers_YYYY-MM-DD.jsonl`), the capture closes the turn
    before them and opens a new one, so each heard turn has one speaker. A
    split turn carries `speaker` (the side-car's label: `alice`, `bob`, ...).
  - **Every turn the side-car named carries `speaker`**: an
    unsplit turn is named by the same rule, from its own partials' labels
    (the one name that held K in a row; only a turn of fewer than K
    partials, too short to reach K at all, is named by its named labels
    if they are all one name; a longer turn no name held K in a row,
    such as a mostly-neutral TV turn with one stray `bob`, carries
    none), and marked
    `speaker_from: labels`. A turn of neutral labels only (the TV), no
    labels yet, or a change the split had not cut when the turn closed,
    carries none. **Default 2; 0 = off** (no `speaker` on any turn: today's
    turns exactly). No side-car file: turns as before. Labels that name
    nobody (`other`, `mixed`, `silent`, `unheard`) never count.
  - **Deploy turns it on.** `keeper/ears-keeper.sh` starts the capture
    without `--speaker-split`, so the default K=2 goes live at the next
    capture restart after merge. **Rollback:** add `--speaker-split 0` to
    the capture line in `start_capture` and restart the capture.
- **Keeper** (`keeper/ears-keeper.sh`, launchd `dev.sessionkit.voice.ears-keeper`):
  keeps the capture alive on a device that comes and goes. It follows the
  Mac's default input through `keeper/input-watch.swift`, and when the
  AirPods leave or return it stops the capture and starts one on the right
  device. It only manages the capture a keeper started; other captures
  (a call-tap, a call leg) are left alone.
- **Sounds** (`keeper/ears-sounds.py`, launchd `dev.sessionkit.voice.ears-sounds`):
  a short sound in the user's ears when the ears log an event, so they know
  they were heard.
- **Turn mail** (`keeper/ears-mail.py`, launchd `dev.sessionkit.voice.ears-mail`):
  mails each of the user's finished turns To and From
  `heard+<device>@<host>`, with the `Ears-*` headers of the pager genie's
  contract (sessionmail `openspec/changes/pager-genie`). A session hears a
  device by subscribing to that address. The box is `heard` (`--sender`),
  because `ears` is this project's own box.
  sessionmail trusts only its keeper's From (`_voice.KEEPER`), so the
  plist loads only once that says `heard` too. `--to seats` is the older mail, To
  each seat a turn is for. **Never run `--to seats` and `--to device` for
  the same seat: it would get each call twice.**
  `Ears-Speaker` is whose turn it is. A turn with a `speaker` label (a
  split turn, or any turn the side-car named) is mailed as `<label>@<host>`: `alice` becomes
  `alice@<host>`, the same form as `--speaker`. The label is lower-cased, and any
  character outside `[a-z0-9._-]` becomes `-`. Every other turn, including
  one whose label is empty or names nobody, keeps `--speaker`
  (`$EARS_SPEAKER`, else `unknown@<host>`: a voice the matcher could not
  place is never mailed under a person's name).
  `Ears-Echo` says a mouth line heard back through the mic was cut out of
  the turn. On the speakers the mic hears the mouth, so a line's
  words are cut wherever they sit in the user's turn: at its start (up to `3 + 5
  words/s × the seconds the line began after the turn` of the user's words
  ahead of it), in the middle, or at the end. The aim is to cut only the echo,
  never the user's words around it. That is what the fixtures test (8 real echo turns, 8
  AirPods quotes), not a guarantee: the cut is a text match. A span starts
  on 2 or more matched words in a row, or on a lone word joined to them by
  one-for-one swaps (a misheard name), so a lone "the" of the user's does not
  drag their words into a clipped echo. Inside a span, up to 30% unmatched words
  (half, when the span is a word-for-word copy of the line) are taken as
  misheard echo. The header is ONE line, a comma list with an entry per line
  cut, in turn order: `Ears-Echo: carol/2d492fb06d3b 31 words cut,
  carol/6c681f74374e 30 words cut` (`<seat>/<utt> <n> words cut`; the seat is
  the line's agent). It goes on both the device mail and the `--to seats`
  mail; the seats mail also keeps its "heard whole" section. A mail with no
  cut has no `Ears-Echo`. A turn that is all echo is still not mailed.
  Neither is one word left BETWEEN two cuts: that is the seam misheard
  ("86%" for "eighty-six"). A lone word before or after both cuts is the
  user's and is mailed. Timing uses the line's play span (`said` − `played_s` ..
  `said`) and the turn's real span (the stream start, from the heartbeat or
  `listen-started`, + `t0`..`t1`), else `ts − (t1 − t0)`. Three guards keep
  the user's quotes of a line: a line played on headphones (AirPods, as the mouth's
  `BARGE_DEVICES`) is never cut; a cut needs half the line's played words;
  and, only when the stream start is known (the real span), the turn must
  have opened within 2 s of the line beginning, unless the turn is mostly
  the line. On the `ts − (t1 − t0)` estimate, only the first two guards
  apply. Numerals (`9%` for "nine percent") still slip past the word match.
- **Waiter** (`bin/heard-first`): an agent blocks on the heard log
  until a line is for it, by `--rule`: its name in a partial or a
  turn (`partial:REGEX`, `turn:REGEX`), a reply to its last line
  (`reply:AGENT`), or the user's first words (`first`); then it wakes.
  `bin/ears-turn` then reads the whole turn, one line.
  To arm the waiter for a seat, run `ears-arm <seat> [talk|alone|room]`;
  `ears-count <seat>` counts the waiters already up. Both are the ears
  skill's tools, on PATH through the plugin's `bin/`; the old path,
  `skills/ears/scripts/ears-arm`, still works. `ears-arm` runs `heard-first`
  from PATH. Claude Code puts the plugin's `bin/` on its Bash tool's PATH
  while the plugin is enabled. Outside Claude Code, put this repo's
  `bin/` on PATH first.
- **Mouth** (`src/mouth`, `bin/mouth`): an agent speaks through one queue,
  and its lines go into the same log, so the waiters can gate out echo.
- **Calls:** `bin/call-tap` puts a call from any app into the ears, both
  ways; `keeper/call-ears` grows an ear for each Delta call and drops it
  at hang-up. The ears keeper runs it as its child: one launch agent,
  `dev.sessionkit.voice.ears-keeper` (`ears services install`), holds the room's
  ear and every call's.
- **Speaker names** (`speakers/`, opt-in:
  `ears services install --only speakers`): a side-car tags each heard line
  with who spoke, in `speakers_YYYY-MM-DD.jsonl` beside the heard log, and
  the waiters read it. See [speakers/README.md](speakers/README.md).

## Configuration

Three JSON settings files, each reread with no restart:

- **The ears:** `~/.sessionkit/voice/settings/ears.json` (or `$EARS_CONF`): listening
  on or off, the input, the speech-to-text engine, and recording.
- **The sounds:** `~/.sessionkit/voice/settings/ears-sounds.json`: which events make a
  sound, and where it plays.
- **The mouth:** `~/.sessionkit/voice/settings/mouth.json` (or `$MOUTH_SETTINGS`): the
  output, the voice, the levels and barge-in. For a line's voice and
  levels, the mouth also reads the agent, seat and session layers; the ears
  read only their own file.

Environment variables win over single keys (`EARS_RECORD` over `record`,
`MOUTH_DEVICE` over `output`). `SESSIONVOICE_HOME` moves the home that holds
the heard log and each part's state; by default it is `~/.sessionvoice`, or
`~/.voicemode` on a host that already keeps its heard log there. The launch
agents are in `~/Library/LaunchAgents/`.

Which files and which variables each part reads, and where each one lives:
[docs/configuration.md](docs/configuration.md).
Every key, its type and its default:
[docs/settings.md](docs/settings.md).

## Logs

- **The heard log:** `<home>/logs/conversations/heard_YYYY-MM-DD.jsonl`,
  one JSON line for each thing the ears heard and the mouth said, where
  `<home>` is the home above. A new file starts each day.
- **The services' logs:** `~/Library/Logs/ears/` (`EARS_LOG_DIR` moves it).
  `ears-keeper.log` is what the keeper did (devices, restarts, calls, a
  deaf mic); `ears-keeper-capture.log` is the room capture's own output;
  `ears-mail.log` is each turn mail sent or skipped; `ears-mouth.log` is
  the mouth's player.

Follow one with `tail -F`, for example
`tail -F ~/Library/Logs/ears/ears-keeper.log`, or today's heard log with
`tail -F <home>/logs/conversations/heard_$(date +%F).jsonl`.
Every log file and what each line means:
[docs/logs.md](docs/logs.md).

- **Health:** `ears services status` prints one row per launch agent
  (`running, pid N`, `loaded, not running` or `not installed`), the one
  that serves speech on `127.0.0.1:8890`, and the home in use.

## Use

    ears backends                          # what runs here
    ears install                           # fetch Silero (the engine: Parakeet on mlx-audio :8890)
    ears install --apple                   # also build the Apple helper (swift, macOS 27; a checkout) + assets
    ears file talk.wav --realtime --turns silence --previews
    rec ... | ears listen --turns silence  # live
    ears serve 2023                        # a whisper-compatible URL on Apple STT

In Python:

    from ears.cli import Pipeline
    p = Pipeline("auto", turns="silence", previews=True, emit=my_handler)
    p.feed(pcm_bytes)   # every 20 ms
    p.close()

## Talk: the live view

`talk serve` is the live voice conversation in a browser: the user's partials and
turns, every mouth line, and who has heard what, read from the heard log.

    talk serve                  # http://127.0.0.1:8780/ (127.0.0.1 by default)
    talk serve --port 8781

It binds 127.0.0.1 unless you pass `--host`, and it answers only a Host it
knows: `127.0.0.1:<port>`, `localhost:<port>` and the `--host` address. Any
other Host gets a 403, so a DNS-rebinding page cannot read the conversation.
To see it on your other devices, `tailscale serve` in front of it is one
option (a note, not a default). It then needs the
tailnet name allowed: `talk serve --allow-host machine.example.ts.net`, or
`TALK_ALLOW_HOSTS`. Behind a proxy, `talk serve --unix` serves a unix socket
and binds no TCP port at all, so no other user can take its port.

Each browser pairs once: every route but the page and the manifest
needs a pairing cookie, so another user on the same machine cannot read the
conversation either. On the host, run `talk pair` in a terminal and open the
link it prints; `talk token --rotate` makes every browser pair again. Open talk
at `http://127.0.0.1:<port>/`, never at `localhost`. The code is `src/talk/`.

## Install

The distribution is `sessionvoice` (`sessionvoice[nemotron]`, `sessionvoice[all]`);
it installs the `ears`, `ears-serve`, `mouth` and `talk` commands. A bare
`pip install sessionvoice` carries what `mouth say` needs. The extras are
`capture` (the mic and call ears), `silero` (the Silero VAD), `smart-turn`
(the Smart Turn gate's ONNX backend), `nemotron` (mlx-audio) and `all`. An
import that needs one fails naming it, as `pip install 'sessionvoice[EXTRA]'`.

    uv venv && uv pip install -e '.[all]'  # onnxruntime + mlx-audio
    ears install
    ears services install --agent NAME     # the launch agents: mouth, keeper, mail

### The proof of a working install: you speak, and the agent WAKES and answers

**Not "a heard line appears".** The keeper alone writes the heard line, and
it pages nobody: the agent is woken by the turn MAIL (`mail`,
`keeper/ears-mail.py`), which needs sessionmail's `message` and a carrier
(postfix, or the mail drop), the `heard` box and a wake seat.
An install that stops at "the user speaks, a heard line appears" can leave the
agent deaf to its person, and one that retires room mode on that proof goes
deaf for good. So the proof is: say "hey NAME", and NAME wakes and
answers.

`ears services install` (`--dry-run` lists the same checks and writes nothing):

- first finds the mlx-audio speech server on `:8890`, or installs its own
  (`dev.sessionkit.voice.mlx-audio`), as "A speech server" under Requirements
  says, and prints which step it took. When it loads its own, the new
  `final` below is written without the probe: that server has not started
  yet;
- installs `dev.sessionkit.voice.ears-mail` beside the mouth and the keeper, unless
  a hand-made mailer is already on the host: then ours is skipped and said so,
  because two mailers would mail every turn twice, and the `message` check
  reads that plist's PATH, not ours. The hand template renders to the same
  label and path as install's own job, so install tells them apart by the
  plist's content: every plist install writes carries
  `SESSIONKIT_INSTALLED_BY` in its `EnvironmentVariables`, and a hand render
  does not. Install never overwrites a hand render; `ears services status`
  names it. Rendering a template over the plist install wrote replaces
  install's job. The same rule holds for the keeper and the speakers
  side-car, and for a hand one under the template's old name
  (`dev.sessionvoice.ears-mail`);
- replaces a legacy render: `dev.voicemode.ears-mail` for mail, and
  `dev.voicemode.mouth-mail` or `com.failmode.mouth-mail` for the mouth (two
  players would drain one queue). Each is booted out and its plist moved
  aside to `<label>.plist.replaced-<stamp>`, never deleted, and ours loads
  once it has gone. One that will not go stops that service with FAILED;
- moves each service it installs off every old label,
  `com.sessionvoice.ears-<name>` and `com.sessionvoice.mlx-audio` (the
  previous ones) and `com.voicemode.ears-<name>` (older): each old job is
  booted out once and its plist removed, and the new
  `dev.sessionkit.voice.ears-<name>` is loaded only after the old has gone. An
  old job that will not go stops that service with FAILED and its label, and
  the new one is not loaded beside it. A pull alone moves nothing: the old
  jobs run the new code under the old name until you run install. A second
  install finds nothing old and does nothing to it. `ears services status`
  names an old label still on the host, and `ears services uninstall`
  removes them all;
- checks `message` and asks sessionmail what carries the launch agents'
  mail (`message setup --dry-run`; it configures neither postfix nor the
  `sendmail` key), and says when the launch agents will not see a
  `SESSIONMAIL_SENDMAIL` your shell sets. Under the mail drop it checks no
  postfix and makes the `heard` box with `message setup --box heard`
  (which also makes sessionmail's mail root, your own box and its notmuch
  profile, if they are missing, and prints each one). On a host that
  sends by postfix, it makes the `heard` box NESTED under the store dir
  that carries `.tm-store`, as `~/.mail/agents/<store>/heard/`, when there
  is exactly one such dir, then runs `vmailbox-gen` if that is on `PATH`,
  and otherwise prints the commands (`~/.mail/.postfix/vmailbox-gen`:
  sessionmail keeps it off `PATH`). When sessionmail's answer cannot be
  read (no `message`, or one older than 0.2.0), it warns, makes no box,
  checks neither, and goes on.
  On a postfix host it also prints the box to subscribe to:
  `message subscription add heard+macbook`;
- `--agent NAME` adds NAME's wake seat (`ears wake add NAME`) and sends the
  keeper's notices to NAME (`EARS_NOTIFY`; `--notify ADDR` sends them
  elsewhere). Without it, nobody is woken, and install says so. The notices
  then go to the operator when `session operator` names one (read at the
  keeper's start; install names the address), else they are log-only. To
  mail nobody on a host with an operator, set `EARS_NOTIFY=""` (empty) in
  the keeper's environment. See "Operator and privileged seats" below;
- refuses, before any plist is written, when the settings schemas are
  missing: a checkout's `settings/`, else the wheel's `ears/_settings/`. It
  then copies each beside its settings file as a dot file
  (`.ears.schema.json`, `.ears-sounds.schema.json`, `.mouth.schema.json`);
- refuses, before any plist is written, when the Silero VAD model is missing,
  unless VoiceMode's copy (`~/.voicemode/models/silero/`) is there to link.
  Run `ears install` first. A keeper that finds no model says so once and
  holds, and does not restart its capture every few seconds;
- probes a new `final` engine with one short clip before writing it to
  `ears.json`, and falls back to whisper `:2022` (and says so) when it does
  not answer. Each probe is said first, the dry run's too: `probing <url>
  (read-only)`. Parakeet on the `:8890` server needs an mlx-audio with its
  STT models: this project's own extra pins `mlx-audio>=0.5.4`. An mlx-audio
  that answers every Parakeet request 500 (`No module named
  mlx_audio.tts.models.parakeet_tdt_0`) is not one, and the probe catches it.
  That the cause is an older mlx-audio is an inference, not measured.

**The ride-along hook is a step of its own.** Installing the Claude Code
plugin does not turn it on: the plugin ships no hooks, so no agent sees what
the mic heard until you opt its seat in. To opt one seat in, add the heard hook to
`.claude/settings.local.json` in the directory that agent is launched from
(that seat only; `~/.claude/settings.json` would be every agent):

    {
      "hooks": {
        "PostToolUse":   [{"hooks": [{"type": "command", "command": "ears heard hook || exit 1", "timeout": 5}]}],
        "PostToolBatch": [{"hooks": [{"type": "command", "command": "ears heard hook || exit 1", "timeout": 5}]}]
      }
    }

If the file already exists, merge these two entries into its `hooks` rather
than replacing it. Keep `|| exit 1`: it turns any failure into exit 1, which
shows a hook error and lets the agent carry on; an exit 2 (an argument error
gives one) would stop the agent's turn. `ears` must be on the PATH that `claude` was started with,
not just the Bash tool's: start `claude` from a shell with the venv active,
or link `.venv/bin/ears` into a directory on your PATH. It takes effect on
the agent's next tool call. `VOICEMODE_HEARD_HOOK=off` silences it without
editing the file.

## Tests

One command runs the whole suite, offline:

    make test                  # uv run --locked pytest
    make test ARGS="-k mouth"  # pytest flags pass through

`uv sync` (or `make sync`) once installs the `dev` group from `uv.lock`; after
that nothing in the suite touches the network, and nothing plays a sound.
`sounddevice` is a base dependency, so it is installed, but
`tests/audio_fence.py` refuses the real module in every test; the
`tests/test_ears_sounds_call.py` tests run against a fake one. One silero
test skips without `onnxruntime`: `uv sync --extra silero` brings it. The
music-ducking tests need no mpv: they run against a fake mpv socket in a
temp dir (`tests/mouth/test_mouth_mpv.py`).

**Review rigs and scripts outside pytest** get none of the suite's fences.
A process that imports the mouth is refused the real `message`, and its drop
notices are recorded `failed: message send fenced`. But a rig that starts a
player (`mouth say`, `mouth serve`, `mouth mail`, or `mouth.say()` with its
spawn), or runs `mouth retract` or `mouth again`, runs the real service, and that does mail. Set
`MOUTH_SENDMAIL` to a stub (`python3 tests/fixtures/refuse_sendmail.py`,
by its absolute path) or `MOUTH_NOTICE=off`, and HOME to a temp
dir, before anything starts. A temp HOME alone is not a fence: a reply notice
needs no box. A rig that runs `keeper/ears-mail.py` without `--dry-run` sets
`EARS_MAIL_MESSAGE` to the same stub.

## Measured (one Mac, 2030-06-09; our own eval set and scoring script, whisper normaliser)

WER through the ears pipeline:

| backend  | 20 turns (1858 w) | 150 singles | how                       |
|----------|-------------------|-------------|---------------------------|
| apple    | 1.51%             | 2.16%       | fast feed, finals joined  |
| apple    | 1.35%             | -           | one 827 s call, real time |
| nemotron | 3.01%             | 3.79%       | fast feed, finals joined  |

- **The real-time call** is the 20 turns joined with 3 s gaps, using silence
  turns with commits on. It produced 22 turns for 20 inputs.
- **The old helper**, which finalised the live analyzer at each commit, scored
  5.17% on the same call, with 54 deletions.
- **Turn latency on turn01, fed in real time:**
  - Apple fires 1.82 s after speech ends, including a 0.2 s commit wait.
  - Nemotron fires at 1.65 s.
  - VAD edges lag about 140 ms at onset and about 280 ms at offset (a 0.2 s
    offset debounce).
- **Memory:** Apple uses about 75 MB of RSS on our side; Nemotron about 2.2 GB.
- **Turn stages (20 turns, real time):** `silence` cut 1 of 20, with a
  p50 of 1.59 s. `smart` veto cut 0 of 20, with a p50 of 2.43 s.

## The mouth (`src/mouth`)

The speaking half: `mouth say` queues a line (a maildir), a resident
player speaks it through Kokoro or a cloned voice, to the room or into a
Delta call, and writes `saying`/`said` rows to the heard log so the
waiters can tell echo from speech. When a line chops:
`docs/troubleshooting.md`.

    mouth say --voice carol --device default "Hello."
    mouth say --voice carol --device call --hold turn-end "On the call."
    mouth --help            # stop, retract, amend, status, devices, serve

Moved here from VoiceMode `feat/mouth-say` @ 64cccd80, so the mouth lives
with the ears. The player is this repo's
`mouth mail`, which `ears services install` runs as a launch agent. The
mouth imports nothing from VoiceMode.
Music ducking (`$MOUTH_DUCK`) sets the DJ's mpv volume over mpv's
JSON IPC socket (`src/mouth/mpv.py`). The socket is `/tmp/voicemode-mpv.sock`
unless `$MOUTH_MPV_SOCKET` names another. With no mpv on that
socket, nothing is ducked and the line still plays.

The heard log came over with the capture (`ears.heard`, ba37091), and clone
voices resolve through the mouth's own copy of VoiceMode's resolver
(`src/mouth/resolver.py`).

Tests: `make test ARGS=tests/mouth` (see Tests above).

## Operator and privileged seats

Who the operator is, and which seats are privileged, is one setting on the
host, never a name in this code. It is sessiontools' operator block in
`~/.sessionkit/tools/settings/session.json`. **sessiontools owns it**, and
this repo never parses that file: it runs `session operator` and reads the one
line of JSON it prints, through one reader, `ears.operator_config`.

    $ session operator
    {"operator": "you@host.example", "mailbox": "you", "privileged": ["helper", "you", "you@host.example"]}

"Configured" means `privileged` is not empty. `AGENT_OPERATOR_ADDR` on its own
moves `operator` only, so it configures nothing and grants nothing.

What reads it:

- **mouth `allowed()`**, who may make the mouth speak. Unconfigured, it is a
  From on this host or in `$MOUTH_MAIL_ALLOW`, as before. Configured,
  it is a From on this host or in `privileged`. Then `$MOUTH_MAIL_ALLOW`
  only **narrows**: when it is set, a sender must be in it as well. An env var
  never adds a sender. A **local** seat outside ALLOW is refused too (`mouth
  say` writes `From: <agent>@<host>`), so an operator who narrows puts
  `@<host>` in ALLOW to keep this host's seats.
- **Matching.** An entry with an `@` matches that address exactly. A bare
  entry (`helper`) is a mailbox on **this host only** (`helper@<host>`), never
  `helper@<another host>`. To admit a seat on another host, write its full
  address.
- **barge-in** (`barge_in` in `~/.sessionkit/voice/settings/mouth.json`). With the key
  absent and an operator configured, the default is the operator's `mailbox`
  in the room (calls stay `anyone`), read as a speaker name: **enroll under
  your mailbox name** with the speaker side-car. Set the key, to `anyone` or to
  names, and it wins.
- **the keeper's notices** (`EARS_NOTIFY`). Unset, they go to the operator's
  address when one is configured, read once at the keeper's start. Set but
  empty (`EARS_NOTIFY=""`) means mail nobody. Set to an address, they go
  there.

**The fallback.** If nothing is configured, or there is no `session` CLI, or
it fails, times out or prints something that is not a JSON object, everything
behaves as before this setting existed, `$MOUTH_MAIL_ALLOW` included.
`MOUTH_SESSION_BIN` set to **any** value (`""`, or a path set for the
voice cascade) switches the reader off: a path in it is never honoured for
privilege.

**KNOWN LIMIT** (accepted): on a CONFIGURED host whose reader is off
(`MOUTH_SESSION_BIN` set, or `session` not on the mouth's PATH), the mouth
falls back and ALLOW still ADDS, as it did before this setting existed. Only
the mouth's own service env reaches this; a sender cannot. The mouth logs a
WARNING when ALLOW rides the fallback.

The WARNING fires when the operator **reader did not answer** (it is off, the
`session` CLI is missing, or its output is bad) and ALLOW is set. The mouth
cannot tell whether the host has an operator block then, so the warning never
claims one. It is said once at the mouth's start (`mouth: the operator reader
is ..., so $MOUTH_MAIL_ALLOW still ADDS senders ...`), and once per
message that ALLOW alone admits, as an `allow-fallback` row in the player's
log naming the sender and the reason (`off`, `absent` or `failed`).
