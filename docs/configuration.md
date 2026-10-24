# Configuration

What sessionvoice configures, part by part: which files, which environment
variables, and where each one lives. Most of it is three JSON settings files
in `~/.sessionkit/voice/settings/`, read live, plus a few variables that move
a file or win over one key. Each key, its type and its default are on the
[settings page][settings].

- **sessionvoice's home:** one folder for the heard log and the state of
  every part ([the home](#sessionvoices-home)).
- **The ears:** `~/.sessionkit/voice/settings/ears.json`, followed live by every
  capture ([the ears](#the-ears)).
- **The keeper and the launch agents:** `ears services install` writes
  them; the keeper reads `ears.json` every loop
  ([the keeper](#the-keeper-and-the-launch-agents)).
- **Turn mail:** the wake rules, `~/.sessionkit/voice/settings/wake.json`
  ([turn mail](#turn-mail)).
- **Ears sounds:** `~/.sessionkit/voice/settings/ears-sounds.json`
  ([ears sounds](#ears-sounds)).
- **The mouth:** `~/.sessionkit/voice/settings/mouth.json`, and for some keys the
  layers above it ([the mouth](#the-mouth)).
- **Mouth sounds:** `~/.sessionkit/voice/settings/mouth-sounds.json`
  ([mouth sounds](#mouth-sounds)).
- **Talk:** flags and `TALK_*` variables; no settings file
  ([talk](#talk)).
- **The speech server, the speakers side-car, the audio devices file and
  the operator:** [below](#the-speech-server).
- **Settings:** every key of the three settings files:
  [docs/settings.md][settings].

Paths are shown under `~`, your home folder. A launch agent's variables are
fixed when its plist is written, so a change to one takes a fresh
`ears services install`; a settings file is reread with no restart.

## sessionvoice's home

The heard log, `heard.lock`, the speakers rows, the mouth's folder
(`mouth/`), talk's token (`talk/`) and the ears' state (`state/`) all live
under one folder, the home. It is the first of:

1. `$SESSIONVOICE_HOME`, if it is set;
2. `$VOICEMODE_BASE_DIR`, if it is set (the old name, still read);
3. `~/.sessionvoice`, if it holds a heard log;
4. `~/.voicemode`, if it holds a heard log;
5. otherwise `~/.sessionvoice`.

`ears services status` prints the home it found and why, and warns when
both folders hold a heard log. The full rule, and the case of a host that
also runs VoiceMode: [logs.md][home].

## The ears

The capture (`ears capture`, run by the keeper) reads one settings file,
`ears.json`, and follows it live: a new engine lands on the next chunk,
with no restart.

- **The file:** `~/.sessionkit/voice/settings/ears.json`, or the path in `EARS_CONF`.
  `ears services install` seeds its `final` engine when the file has none.
  `ears recording on|off` writes its `record` key.
- **Only this file.** The ears do not read the agent, seat or session
  layers ([the settings page][cascade-ears]).
- **The key files:** the speech-to-text key is `$EARS_LISTEN_STT_KEY`, else
  `~/.sessionkit/voice/settings/stt.key` (a fixed path; `EARS_CONF` does not move it).
- **The models:** `ears install` puts the Silero model at
  `~/.cache/ears/silero_vad.onnx`; `EARS_SILERO_ONNX`, when set, is read
  first. The Smart
  Turn gate's ONNX file is read from `~/.cache/ears/`, or the path in
  `EARS_SMART_TURN_ONNX` ([models.md][models]).

The variables that win over a key of `ears.json`, each on its own:

| Variable | Wins over | What it does |
| --- | --- | --- |
| `EARS_LISTEN_STT_URL`, `EARS_LISTEN_STT_MODEL` | `final` | Pin one capture's engine. A capture started with either keeps it and does not follow the file. |
| `EARS_STT_HINT` | `stt_hint` | `0`, `false`, `no` or `off` turns the seat-name prompt off; anything else turns it on. |
| `EARS_RECORD` | `record` | `1`, `true`, `on` or `0`, `false`, `off`. A capture's `--record` or `--no-record` wins over both. |
| `EARS_RECORD_DAYS` | `record_days` | A whole number of days, 0 or more. |
| `EARS_RECORD_NOTICE`, `EARS_RECORD_SPOKEN_NOTICE` | `record_notice`, `record_spoken_notice` | Read by the call bot; the ears only report them (`ears recording status`). |
| `EARS_HEARD_PARTIALS_ONLY` | `heard_partials_only` | The heard hook's partials-only mode: `1`, `on`, `true`, `yes` or `0`, `off`, `false`, `no`. The hook's `--partials-only` or `--no-partials-only` wins over both ([ears.md][heard-hook]). |

`EARS_STT_URL` and `EARS_STT_MODEL` are different: `ears services install`
reads them once, to write `final` into `ears.json`. Thirteen of the ears'
variables had a `VOICEMODE_` name before, which is still read
([ears.md][ears-old]).

## The keeper and the launch agents

`ears services install` writes one launch agent per service to
`~/Library/LaunchAgents/`, and `ears services status` reports each one.

| Service | Label | What it runs |
| --- | --- | --- |
| `keeper` | `dev.sessionkit.voice.ears-keeper` | the keeper, following the Mac's default input |
| `mouth` | `dev.sessionkit.voice.ears-mouth` | `mouth mail`, the mouth's player |
| `mail` | `dev.sessionkit.voice.ears-mail` | the turn mailer |
| `speech` | `dev.sessionkit.voice.mlx-audio` | the speech server on `127.0.0.1:8890` |
| `stt` | `dev.sessionkit.voice.ears-stt` | Apple's speech on `127.0.0.1:2023`, with `--apple` or `--only stt` |
| `speakers` | `dev.sessionkit.voice.ears-speakers` | the speakers side-car, with `--only speakers` only |
| `sounds` | `dev.sessionkit.voice.ears-sounds`, `dev.sessionkit.voice.mouth-sounds` | the ears sounds and the mouth sounds, two agents, with `--only sounds` only |
| `talk` | `dev.sessionkit.voice.talk` | `talk serve` on `127.0.0.1:8780`, with `--only talk` only |

How install tells its own plist from a hand render of a `launchd/` template,
which carries the same label: every plist install writes has
`SESSIONKIT_INSTALLED_BY=ears services install` in its
`EnvironmentVariables`, and no template does. A plist at one of these labels
without it is hand-made: install leaves it alone and skips its own job. A job
loaded under one of these labels with no plist in `~/Library/LaunchAgents` is
judged by the environment `launchctl print` lists for it, the same way.
`ears services status` names a hand-made one and says how to run ours instead. These
labels were `com.sessionvoice.ears-<name>` and `com.sessionvoice.mlx-audio`
before; install moves each service it installs off its old label. The move,
step by step, with each check and undo: [upgrade-labels.md][upgrade-labels].

The keeper reads `ears.json` on every loop: `listen` (off stops every ear),
`input` (`default` follows the Mac, a name pins the ears to it),
`room_on_call`, and the engine keys, which the capture then follows. Its
variables, set in the plist or the shell that starts it:

| Variable | Default | What it does |
| --- | --- | --- |
| `EARS_CONF` | `~/.sessionkit/voice/settings/ears.json` | The settings file it reads. |
| `EARS_LOG_DIR` | `~/Library/Logs/ears` | Where its logs go, and every agent's. |
| `EARS_RUN` | `~/.cache/ears/run` | Its stop files, the input watcher and its state. |
| `EARS_NOTIFY` | the operator's address | Who its notices are mailed to. Set but empty: nobody. |
| `EARS_CALLS` | `1` | `0` runs the room's ear only, no call ears. |
| `CALL_EARS_DIR` | `${XDG_STATE_HOME:-~/.local/state}/ears/call-ears` | The call ears' folder and their `STOP` file. |
| `EARS_OUTPUT_FALLBACK` | `1` | `0` never moves the Mac's default output. |
| `EARS_PY` | `.venv/bin/python` in the checkout it runs from | The Python that runs the capture. Install sets it to its own. |
| `EARS_AGENT` | `ears` | The seat the keeper writes and mails as. Install reads it once. |

Its timing knobs (`EARS_INTERVAL`, `EARS_STABLE`, `EARS_BEAT_STALE` and
more) are listed with the log lines they change: [logs.md][log-files].

`ears-sounds`, `mouth-sounds` and talk have no `ears services` agent. Their
plists are templates in `launchd/`, which `bin/render-plist` fills in for
this machine.

## Turn mail

The turn mailer (`keeper/ears-mail.py`) mails each finished turn, and wakes
a seat called by name.

- **The wake rules:** `~/.sessionkit/voice/settings/wake.json`, or the path in
  `EARS_WAKE`. `ears wake add SEAT` writes it; the copy the repo ships in
  `keeper/defaults/` names no seats and is never written.
- **Install fixes the path.** The launch agent is given `--rules` with the
  path as it was at install.
- **Its state:** `~/.cache/ears/ears-mail.state.json`.
- **Its variables:** `EARS_SPEAKER` (who a turn is from, when the speaker
  side-car names nobody), `EARS_MAIL_FROM` (the box, `heard` by default),
  `EARS_MAIL_DOMAIN` (the host part of the address) and `EARS_MAIL_MESSAGE`
  (the `message` command it sends with).
- **Delta-call turns:** with `--calls on` (the default) each finished turn
  of a Delta call is mailed To `calls+<bot>-<N>`, From `ears`
  (`--calls-from`), instead of `heard+<device>`. `EARS_CALL_SOCKS` names
  the bots' `call-ears.sock` legs, else they are found in sessionphone's
  root and each `~/.<agent>/delta-chat`. See "The call-box mail" in
  [ears.md](ears.md).

## Ears sounds

`keeper/ears-sounds.py` plays a short sound when the ears log an event.

- **The file:** `~/.sessionkit/voice/settings/ears-sounds.json`, or the path in
  `EARS_SOUNDS_SETTINGS` (or `--settings`). It is reread when it changes;
  a bad edit keeps the last good copy.
- **The global switch:** `allow` in `~/.settings/notifications/global.json`
  (or `NOTIFY_GLOBAL`). Off there, no sound plays.
- **Its log:** `~/Library/Logs/ears/ears-sounds.jsonl`, or
  `EARS_SOUNDS_LOG`.
- **The heard log it follows:** the home's, or `EARS_SOUNDS_HEARD_DIR`.
- **As a launch agent:** `ears services install --only sounds` runs it as
  `dev.sessionkit.voice.ears-sounds`, beside the mouth sounds, on the
  agents' Python. The plist passes the settings file and the log above, and
  `--stop-file ~/.sessionkit/voice/STOP-ears-sounds`: create that file and
  it exits and stays down until the next install. Its output goes to
  `ears-sounds.log` beside the other agents' logs.

## The mouth

The mouth reads its settings file for every line, so a change applies to
the next line queued.

- **The file:** `$MOUTH_SETTINGS`; else `~/.sessionkit/voice/settings/mouth.json`
  when it exists; else the old `<home>/mouth/settings.json`.
- **The layers above it.** For a line's voice, volume, duck, levelling and
  lead, the mouth also reads the agent, seat and session layers, through
  `session settings`, over the file. Every other key comes from the file
  alone ([the settings page][cascade-mouth]).
- **The key:** a speech server's key is `$MOUTH_TTS_KEY`, else
  `~/.sessionkit/voice/settings/tts.key` (yours, mode 600). It is never read from
  `mouth.json`.
- **Its folder:** `$MOUTH_DIR`, else `<home>/mouth/`. The timed overrides
  live there: `barge.json` (`mouth barge`) and `duck.json` (`mouth duck`).
- **Clone voices:** read from VoiceMode's config, never written:
  `VOICEMODE_VOICES_DIR` and the other `VOICEMODE_` clone variables, from
  the environment, then `~/.voicemode/voicemode.env` ([models.md][models]).

The variables most often set:

| Variable | What it does |
| --- | --- |
| `MOUTH_DEVICE` | The output a line plays on when it names none. Wins over `output`. |
| `MOUTH_VOICE` | The voice for a line that names none. In the caller, it wins over `voice` unless the host file locks `voice`. |
| `MOUTH_KOKORO_URL`, `MOUTH_KOKORO_MODEL` | The speech server and its model. Each wins over its half of `tts`. |
| `MOUTH_BARGE_WORDS` | The words that cut a line, when `barge_words` is unset. The setting wins over it. |
| `MOUTH_RESUME_ANSWER_WORDS`, `MOUTH_RESUME_ANSWER_QUESTION` | Used when `resume_answer_words` or `resume_answer_question` is unset. The setting wins. |
| `MOUTH_ROOM_HOLD` | `off` plays a room line that asks no hold at once, as before; `on` holds it for the end of your turn. Wins over `room_hold`. |
| `MOUTH_LOG_DIR` | Where the mouth writes its heard-log lines, in place of the home's. |
| `MOUTH_SESSION_BIN` | The `session` command. Set to `""`, the layers above the file are off. |
| `MOUTH_SENDMAIL`, `MOUTH_NOTICE` | How the mouth mails its notices; `MOUTH_NOTICE=off` sends none. |

The mouth's 39 variables had a `VOICEMODE_MOUTH_` name before, which is
still read; the full list is in [mouth.md][mouth-old].

## Mouth sounds

`keeper/mouth-sounds.py` plays small sounds for the mouth's flow: a line
landing, a line sent to the front, the canned opener, a mid-turn wake, and
an agent taking your words.

- **The file:** `~/.sessionkit/voice/settings/mouth-sounds.json`, or the path in
  `MOUTH_SOUNDS_SETTINGS`. It has no schema; its defaults are in the
  script, and `keeper/defaults/mouth-sounds.json` shows the shape.
- **Its log:** `~/Library/Logs/ears/mouth-sounds.jsonl`, or
  `MOUTH_SOUNDS_LOG`.
- **As a launch agent:** `ears services install --only sounds` runs it as
  `dev.sessionkit.voice.mouth-sounds`. The plist sets `MOUTH_SOUNDS_SETTINGS`
  and `MOUTH_SOUNDS_LOG` to the paths above and `MOUTH_SOUNDS_STOP` to
  `~/.sessionkit/voice/STOP-mouth-sounds`. `MOUTH_SOUNDS_FIRED` and
  `MOUTH_SOUNDS_CANNED` stay in the keeper's run folder (`EARS_RUN`, else
  `~/.cache/ears/run`): `mid-turn.fired` and `auto-opener.utts`, where the
  scripts that write them put them.

## Talk

`talk serve` has no settings file. Each setting is a flag or a `TALK_*`
variable: `TALK_PERSON`, `TALK_ALLOW_HOSTS`, `TALK_UNIX_SOCKET`,
`TALK_SETTINGS_URL`, `TALK_CALL_URL` and `TALK_SMART_TURN_DIR`. The table
is in [talk.md][talk-settings].

- **As a service:** `ears services install --only talk` (opt-in) runs it
  as `dev.sessionkit.voice.talk` on `127.0.0.1:8780`, its log
  `~/Library/Logs/ears/talk.log`.
- **Its token:** `<home>/talk/token`, mode 0600, made by the first
  `talk serve`.
- **Its socket,** in socket mode: `<home>/talk/talk.sock` by default.
- **Each seat's offsets** it reads: `$EARS_HOME`, else the seat's home.

## The speech server

The mlx-audio server that the ears' Parakeet and the mouth's Kokoro both
use, on `127.0.0.1:8890`. `ears services install` uses an mlx-audio
server it finds there, and installs its own only when the port is free.

- **Its home:** `~/.local/share/ears/speech` (its venv), or
  `EARS_SPEECH_HOME`. `EARS_SPEECH_PY` names a Python that already has it.
- **Its logs:** `~/Library/Logs/ears/mlx-audio/`.

## The speakers side-car

Opt-in (`ears services install --only speakers`). It names who spoke each
heard line.

- **Its home:** `~/.local/share/ears/speakers` (references and venv), or
  `EARS_SPEAKERS_HOME`. Install makes it 0700.
- **More:** [speakers/README.md][speakers].

## The audio devices file

The keeper's input watcher writes this Mac's audio devices to
`${XDG_STATE_HOME:-~/.local/state}/audio/devices.json`. `AUDIO_DEVICES_FILE`
(a full path) moves it ([audio-devices.md][devices]).

## The operator

Who the operator is comes from sessiontools' settings, read only through
`session operator`. sessionvoice never opens that file. Three things use
it: the keeper's notices (when `EARS_NOTIFY` is unset), who may barge in
when `barge_in` is unset, and which senders the mouth takes mail from. With
no operator configured, none of the three uses one.

## Settings

Every key of `ears.json`, `ears-sounds.json` and `mouth.json`: its type,
its default, what it does and which file it lives in, and how far each
part follows the family's settings layers today:
[docs/settings.md][settings].

[settings]: settings.md
[cascade-ears]: settings.md#the-ears-read-one-file
[cascade-mouth]: settings.md#the-mouth-reads-some-keys-from-the-layers
[home]: logs.md#sessionvoices-home
[log-files]: logs.md#the-log-files
[models]: models.md
[ears-old]: ears.md#old-environment-names
[heard-hook]: ears.md#the-heard-hook
[mouth-old]: mouth.md#old-environment-names
[talk-settings]: talk.md#settings
[speakers]: ../speakers/README.md
[devices]: audio-devices.md
[upgrade-labels]: upgrade-labels.md
