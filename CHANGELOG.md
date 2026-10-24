# Changelog

<!-- markdownlint-configure-file { "MD024": { "siblings_only": true } } -->

All notable changes to sessionvoice are documented here. The format is
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

<!-- markdownlint-configure-file {"MD024": {"siblings_only": true}} -->

## [Unreleased]

## [0.3.0] - 2026-10-24

### Added

- Release mechanics, the same shape as the other repos in the family.
  `make release` (`BUMP=patch|minor|major`, or `VERSION=X.Y.Z`) runs
  `scripts/release.py`: uv sets the one version in `pyproject.toml`, the
  script copies it into `.claude-plugin/plugin.json` and
  `marketplace.json`, dates this `[Unreleased]` section with a fresh one
  above it, commits those files by name and makes the annotated tag
  `vX.Y.Z`. It never pushes; it prints the push line. It refuses a dirty
  tree, a missing changelog or `[Unreleased]` section, and a tag that
  already exists, and it checks all of them before it writes anything.
  `--dry-run` says what it would do, and `--current` prints the version.
  `make push` pushes the branch and the tags. `make publish` builds and
  uploads this version with `uv publish`, and the token comes from the
  password store or the environment, never from a command line.
- `mouth devices` marks the system's current output and names the current
  input, and reports the output volume (EARS-430). The current output's
  line gains a note after a TAB, `# current output, volume 62%`
  (`(muted)` when muted), and a last line reads
  `# current input: NAME`. A reader that cuts each line at the TAB still
  gets only names. The new `--json` prints one object: `outputs`, `calls`,
  `current_output`, `current_input`, `output_volume` (0 to 1) and
  `output_muted`. All of it is asked of CoreAudio live, by ctypes, so a
  seat needs neither Homebrew's `SwitchAudioSource` nor `osascript` for
  it. What is unknown (off macOS, or a device with no volume control) is
  left out of the text and `null` in the JSON, never guessed.
- `ears heard hook` has a partials-only mode, for a seat that gets each
  finished turn by mail (`heard+<device>`). It drops every finished turn,
  with the partials that turn covers, and keeps the partial preview of a
  turn still being spoken, the shown events (wake word, barge-in, device
  change) and other seats' said lines; the cursor still moves past what it
  drops. Turned on by `--partials-only` (or off by `--no-partials-only`),
  else `EARS_HEARD_PARTIALS_ONLY`, else `heard_partials_only` in
  `ears.json` (new in its schema), else off. The heard waiter does not
  read it.
- `ears services weights [--json]` reads the speech server's three weights
  from the Hugging Face cache, without fetching anything (EARS-419). Each
  one is `cached`, `downloading` (with bytes done and the expected total) or
  `missing`, plus a drift flag. `--json` prints one line with the schema
  `ears.weights/1`, documented in `docs/models.md`, so a caller can say
  "downloading 412 MB of 2.9 GB" instead of going quiet. The expected size
  of each weight is pinned beside its commit, about 2.9 GB in all.
- `ears services install` fetches the speech server's three weights (about
  2.9 GB) when it installs that server, so the first call no longer waits
  silently on the download (EARS-419). It prints the size first, skips and
  names weights already cached, and prints progress during the fetch. The
  fetch is the server's own `snapshot_download` call with revision `main`,
  into the cache the server reads, whatever this shell's `HF_HOME` says.
  `--no-prefetch` or `EARS_NO_PREFETCH=1` skips it and says what the first
  call will fetch. `--dry-run` prints the size and fetches nothing. A failed
  fetch never fails the install: it says why and how much is left.
- `ears services install --only sounds` runs the ears sounds and the mouth
  sounds as two launch agents, `dev.sessionkit.voice.ears-sounds` and
  `dev.sessionkit.voice.mouth-sounds`, from an installed package with no
  checkout (EARS-429). The package now ships `keeper/ears-sounds.py`,
  `keeper/mouth-sounds.py` and `keeper/held_stream.py`, which ears-sounds
  imports from beside itself. Both run on the agents' Python; their
  settings are `ears-sounds.json` and `mouth-sounds.json` in the settings
  folder, their STOP files are `~/.sessionkit/voice/STOP-ears-sounds` and
  `STOP-mouth-sounds`, and their logs sit beside the other agents' logs.
  A STOP file keeps its agent down until the next install. Sounds are
  opt-in: a bare install never loads them. `--dry-run` prints both plists
  and writes nothing, `status` has a row for each, and `uninstall --only
  sounds` removes both. A hand render of either, under its label or an
  older `dev.sessionvoice.*` or `dev.voicemode.*` name, is left running and
  ours is skipped, so no sound plays twice.
- `ears services install --only talk` runs `talk serve` as the launch agent
  `dev.sessionkit.voice.talk`, from an installed package with no checkout
  (EARS-429). It runs the `talk` command installed beside the agents'
  Python, never the first `talk` on `PATH` (macOS ships an unrelated
  `/usr/bin/talk`); with none there, install refuses and writes nothing. It
  serves `127.0.0.1:8780`, talk's default, and logs to `talk.log` beside the
  other agents' logs. Talk is opt-in: a bare install never loads it.
  `--dry-run` prints the plist and writes nothing, `status` has a row for
  it, and `uninstall --only talk` removes it. A hand render, under its
  label or an older `dev.sessionvoice.talk` or `dev.voicemode.talk` name,
  is left running and ours is skipped, so two never fight over the port;
  `status` now names one under an old name as hand-made.
- The turn mailer writes each finished turn of a Delta call to that call's
  box, `calls+<bot>-<N>@<host>` (EARS-431), carrying both directions: the
  caller's turns come in from the mailer, a seat's replies go to the same
  box. The bot is named by the call's own `call-ears.sock` probe, so a
  Delta bot kept in sessionphone's root (`~/.sessionphone[/<profile>]`)
  gets call-box mail too, as one under `~/.<agent>/delta-chat` does; a
  Delta profile named `web` there is skipped as a web leg. The mail
  carries `Ears-Kind: turn`, `Ears-Seq`, `Call-Id` and `Call-Bot`, from
  `ears`. The mouth's echo turns are no longer mailed. New switches
  `--calls on|off` (off restores the old skip) and `--calls-from`; a call
  whose bot no leg names is skipped as `call-bot`. A hand-made job that
  mails the same box should be retired in the same step, or each turn
  arrives twice. Details: "The call-box mail" in `docs/ears.md`.

### Changed

- The heard hook's settings have names of their own: `EARS_HEARD_HOOK`,
  `EARS_HEARD_BUDGET` and `EARS_HEARD_MAX_LINES`. The old
  `VOICEMODE_HEARD_HOOK`, `VOICEMODE_HEARD_BUDGET` and
  `VOICEMODE_HEARD_MAX_LINES` are still read as deprecated aliases; the new
  name wins when both are set, and an empty one counts as set.
- `docs/ears.md` names `ears heard hook` the canonical heard hook, in a new
  section, "The heard hook", and says a seat that gets heard mail runs it
  with `--partials-only`.
- Barge-in: a partial the speaker side-car labels `unheard` (too little
  audio to score, often a quiet voice) now counts toward a cut as if
  `barge_in` were `anyone`, as a partial nobody names in time already did.
  Before, it was skipped like another speaker's, so with names set a quiet
  voice could not cut a line. `other`, `silent` and `mixed` still never
  cut on a place with names. The bed's duck reads the label the same way.
- `mouth barge` and `mouth status` show who may cut: `barge.barge_in` holds
  `room` and `call`, each with `who` (`anyone` or the names) and where it
  came from (`by`: `setting`, `operator` for the operator's default, or
  `builtin` when nothing is configured).
- A line on a room device now waits for the end of the user's turn by
  default (EARS-434). A line that asked no hold used to play at once, so a
  seat that forgot `--hold turn-end` talked over the user. Now any output
  that is not a call holds such a line as `--hold turn-end` does, with the
  same release and the same safety net (`$MOUTH_HOLD_IDLE_S`), so it never
  waits forever; unlike a held opener it is never dropped as `stale`. Its
  `saying` row says `hold_from: room`. `mouth say --now` still interrupts,
  and the new `--hold none` (`X-Mouth-When: now` on a mail) plays at once
  without cutting the line playing. Played files (`mouth play`) and call
  lines are unchanged. To restore the old behaviour, set
  `"room_hold": false` in `mouth.json`, or `MOUTH_ROOM_HOLD=off`, which wins
  over the file.
- The ears skill's speaking rules open with "reply in the batch you are
  in": when a turn wants an answer and the words are right, `mouth say`
  goes in the current tool batch, beside the other calls, not after them.
- `ears services install` refuses a bare install that would wake nobody
  (EARS-421). When `mail` is installed, neither `--agent NAME` nor the new
  `--no-agent` is given, and the wake file names no seat, it exits 2
  before anything is written, instead of installing with a warning. The
  refusal prints both forms and `ears wake add NAME`, and inside an agent
  seat it suggests `TM_AGENT_ADDRESS`'s local part as NAME (a suggestion
  only). `--dry-run` says `REFUSED`, still lists, and exits 2.
  `--no-agent` installs as a bare install did, with the NOBODY warning;
  it and `--agent` are mutually exclusive. A re-run on a host whose wake
  file already names a seat, and `--only` without `mail`, are not refused.
- A line with no device now plays on the system default output (EARS-368).
  `mouth say`, `play`, `again` and a mailed line all take one order,
  through one function (`mouth.resolve_device`): the line's own device
  (`--device`, or a mail's `X-Mouth-Device`, or the call it is To), then
  `$MOUTH_DEVICE`, then `output` in `mouth.json`, then `default`. Before
  this, `say` with none of the first three refused the line. A mailed
  line never read `output` at all: it reached the player with no device,
  was filed `said reason=device-absent`, and played nothing. `null` at
  any rung still plays nothing, and a named device that is absent is
  still `device-absent`, never the default instead. Only a machine with
  no default output at all now gets `device-absent` for an unnamed line,
  with the detail "no default output device". A line's `device_from`
  (`flag`, `mail`, `call`, `env`, `settings` or `default`) names the rung
  that chose its device: `mouth say` prints it, and the `saying` and
  `said` rows carry it, except for a `mouth say` line whose caller named
  the device, whose mail is unchanged.
- The synthetic dates in the docs and tests fall on one of two days,
  2030-06-09 and 2030-12-08. Where one came before another, the earlier
  one reads 2030-06-09 and the later one 2030-12-08, so the order holds;
  an age in days is counted back from one of them, never typed. Clock
  times are unchanged, and release headings keep their real dates.
- The `weight` rows of `ears services status` are the reader's rows. They
  say `cached`, `downloading N of M`, `not cached`, or that a download
  stopped, with `DIFFERS` when the cache's `main` is another commit. They
  used to say `match`, `DIFFERS` or `not cached` (EARS-419).

### Fixed

- `ears heard hook --help` and `-h` print the hook's usage and exit 0,
  without reading stdin or moving a cursor. Before, the hook ignored the
  flag, ran, and printed nothing. `ears heard --help` prints the same usage
  on stdout and exits 0.
- The wake rules example's note no longer says ears-mail never reads "this
  copy", which was false once the example was copied to the live
  `wake.json`. It names the file ears-mail reads instead.
- `ears-arm` finds `heard-first` without PATH (EARS-422). Claude Code puts a
  plugin's `bin/` on the Bash tool's PATH only for a plugin enabled when the
  session started, so after a mid-session install `ears-arm` failed with
  `exec: heard-first: not found` until Claude Code restarted. It now looks in
  the `bin/` of the plugin it sits in (symlinks resolved), then in
  `$CLAUDE_PLUGIN_ROOT/bin`, then on PATH. If all three miss it prints one
  line, that `heard-first` was not found and that restarting Claude Code puts
  the plugin's `bin/` on PATH, and exits 127. `--dry-run` prints the path it
  found.
- `ears services weights` reads the cache the speech server reads,
  whatever this shell's `HF_HOME` says (EARS-419).
- The ears keeper no longer runs `swiftc` on a Mac without the Command
  Line Tools (EARS-424). There `/usr/bin/swiftc` is the install stub, and
  each fresh keeper start (every launchd restart or reinstall) brought
  back the "install the command line developer tools" dialog. The keeper
  now asks `xcode-select -p` first, which never prompts, and builds
  `input-watch` only when it names a directory that exists. Without the
  tools it polls `system_profiler`, logs once per start
  `no Command Line Tools: polling system_profiler (install them for faster
  device changes)`, and never calls the stub. A watcher already built
  needs no tools. `ears services status` prints the keeper's mode under
  its row: `input-watch, pid N`, or `polling system_profiler` and why.
- The mouth's mail box (`~/.mail/agents/mouth`, or `$MOUTH_MAILBOX`) and
  its `tmp/`, `new/` and `cur/` are made 0700, like every other agent
  box, whatever the umask. Before this they were made with the umask's
  mode, usually 0755, so other local users could list the box. A box
  already made 0755 is set to 0700 the next time the mouth starts, says a
  line or writes a mail log entry.
- `ears services install` makes the `heard` box it nests under the store
  0700 as well, and sets an existing one back to 0700 (a dry run says it
  would). The hand-made-box advice it prints now makes the box 0700 too.

## [0.2.1] - 2026-10-21

### Fixed

- `docs/ears.md` no longer says the turn mail waits on sessionmail under
  its mail drop (EARS-413). Two claims were false against sessionmail
  0.2.0: the drop does qualify a heard turn's bare From with the host, and
  the carrier is set by the `sendmail` key in sessionmail's settings file,
  with the drop as the default when neither it nor `SESSIONMAIL_SENDMAIL`
  is set. The page now says the `heard` box must exist first
  (`message setup --box heard`), that a fire-log `exit 0` proves only
  that the turn reached that box, and that an agent hears a heard turn
  only once it subscribes to `heard+<device>`.
- `ears services install` asks sessionmail what carries the launch
  agents' mail (`message setup --dry-run`, sessionmail 0.2.0 or later)
  instead of guessing from postfix (EARS-414). macOS ships `postconf`, so
  before this a Mac with no postfix took the postfix checks: it never said
  "mail drop" and warned that no agent would be paged. Under the mail
  drop, which is sessionmail's default, install runs no postfix check and
  makes the `heard` box itself with `message setup --box heard`. The drop
  never makes a mailbox, and with no box each heard turn is refused. On a
  host whose `sendmail` key names an MTA, the postfix checks and the
  store-dir `heard` box are as before. When sessionmail's answer cannot be
  read, install says so, makes no box, checks neither, and goes on.
- `ears services install` and the README no longer say that under the
  mail drop a heard turn's From is not qualified and reads as plain mail,
  that a Mac with no postfix delivers nothing until sessionmail reads its
  settings, that `message send` needs `SESSIONMAIL_SENDMAIL=drop`, or that
  the launch agents send by postfix because it is installed (EARS-414).
  With sessionmail 0.2.0 the drop is the default, and postfix is used only
  when the `sendmail` key in `~/.sessionkit/mail/settings/mail.json` names
  it (`"sendmail": "/usr/sbin/sendmail"`). Install still says that the
  launch agents do not see a `SESSIONMAIL_SENDMAIL` set in your shell. This
  supersedes the 0.2.0 note that described the drop's From and the
  settings file as waiting on sessionmail.

## [0.2.0] - 2026-10-20

### Added

- One recording switch for the ears, off by default (EARS-387). With nothing
  set, the ears keep no audio. It is read from the capture's `--record` or
  `--no-record`, then `EARS_RECORD` (`1`/`true`/`on` or `0`/`false`/`off`),
  then `"record"` in `~/.settings/ears/ears.json` (followed live, with no
  restart), then off. A value that is not valid keeps nothing.
  `record_days` (`EARS_RECORD_DAYS`, default 7, 0 = forever) sets the
  retention. `ears recording status [--json]` says on or off and which
  layer decided. `ears recording on [--days N]` and `ears recording off`
  write the file key atomically, keep the other keys, and say so when the
  environment or a running capture's flag wins over what they wrote.
  While recording is on, every voice the mic hears is kept, guests and TV
  included. `listen-started` carries `record` and `record_from`, and the
  keeper notes both at each capture start. The schema gains `record`,
  `record_days`, and the call bot's `record_notice` and
  `record_spoken_notice`, which the ears only report.
  `tests/test_recording.py` runs the shared table of cases
  (`tests/fixtures/recording-switch-cases.json`).
- Kept turn audio, only while recording is on (EARS-387). Each final turn's
  audio, from its start (pre-roll included) to the detector's close, is one
  16 kHz mono WAV, 0600 in 0700 folders, at
  `<home>/audio/<YYYY-MM-DD>/<HHMMSS-mmm>-<n>.wav`, written before the
  turn's heard line. The line carries `audio` and `audio_s`, and
  `audio_truncated: true` for a turn longer than the 120 s it keeps. The
  heard mail carries `Ears-Audio: <path>`. Every voice the mic hears is
  kept, guests and TV included. A turn with no recognised words keeps
  nothing. Date folders older than `record_days` (7) are pruned at capture
  start and hourly; the prune touches only the files it wrote. About
  115 MB per hour of speech. `tests/test_turn_audio.py`.
- `docs/talk.md`: how to run, pair, reach over a tailnet and set up talk,
  and what it reads (EARS-382). These user instructions were in
  `talk/README.md`, which no longer ships. The launchd template and
  `src/talk/__init__.py` now point there, and `tests/test_export_ignore.py`
  fails when any shipped file, not only a Markdown page, cites a note the
  archive leaves out.
- A capture writes the stack of every thread to stderr when it is sent
  SIGTERM, then stops as before (EARS-359). When the keeper stops a wedged
  capture, `ears-keeper-capture.log` shows the call it was stuck in.
  `faulthandler` does the dump in C, so it works even when the capture is
  stuck inside C code. SIGUSR1 writes the same stacks and leaves the
  capture running. `tests/test_listen_stack_dump.py` signals a capture
  whose re-decode hangs.
- Say again (EARS-22). A turn that is wholly a say-again phrase ("say again",
  "repeat that", "pardon", "come again" or "say that again") replays the
  last line of the seat it names, else of whoever
  spoke last, within `again_window` (120 s). The model is not asked again,
  and the seat is not woken. The keeper runs the new `mouth again UTT`
  (`--mouth`, 5 s), and the device mail's `Ears-Rule` entry is
  `<seat>@<host>=again`, which calls nobody. The mouth queues the line's kept
  WAV as a speech line held for the end of the user's turn, at the level it
  first played. If the WAV is gone, it says the text again. A replay replaces a
  pending resume of the same line. When it has played, been cut or been
  dropped, the seat gets one non-urgent note, which rides with its next wake.
  If the mouth cannot replay the line, the turn is a `call`, so the seat
  says it again. A phrase inside a longer sentence ("say that again later")
  is not a say-again. The phrases are set per seat under `again` in
  `wake.json`; `[]` turns it off. `tests/test_say_again_e2e.py` runs it end
  to end offline: the real keeper and the real `mouth again`, with nothing
  played.
- A test builds the wheel and reads it (EARS-160). `tests/test_wheel_build.py`
  builds offline, with the dev group's setuptools and no build isolation, in a
  copy of the sources under a temp dir. It checks that the wheel holds the
  `ears`, `mouth` and `talk` packages and every module in `src/`, the keeper's
  files, the speaker side-car, the speech lock, the settings schemas and talk's
  page, each the same bytes as the checkout's, and no `swift/`. It checks that
  `METADATA` requires numpy, pyyaml, httpx and sounddevice as the base, with
  onnxruntime and transformers in the `smart-turn` extra. `setuptools>=77`
  joins the `dev` dependency group for it.
- A test fails when the four places that state sessionvoice's version drift
  apart: `pyproject.toml`, `.claude-plugin/plugin.json`,
  `.claude-plugin/marketplace.json` and uv.lock's own package entry. It names
  each file and the version it holds (EARS-353, test).
- The ears' docs say what to do with the lid shut: the built-in mic is off
  in hardware; use Bluetooth buds or a USB mic (EARS-341).
  `commands/ears-setup.md` tells the user at step 3,
  `docs/troubleshooting.md` has a section for ears that hear nothing, and
  `docs/ears.md` has one table of every DEAF and recovery mail the keeper
  sends, with each cause's trigger and heard-log text.
- The keeper reports a speakers side-car that hears nothing (EARS-341). It
  reads `speakers_DAY.jsonl` in the same pass as the heard log, and 20 mic
  rows in a row with 0% of the line covered (`EARS_SPEAKERS_DEAF_LINES`)
  send one `ears: speakers DEAF, 0% of N lines on DEVICE` mail. A covered
  line sends one `ears: speakers hearing again`. It stays quiet while the
  room is DEAF or no room capture runs. A row from a mic that gives only
  zeros is covered and does not count.
- The keeper holds instead of failing over to a shut lid (EARS-341). When
  the input to follow is the built-in mic and the lid is shut, it sends one
  `ears: DEAF, lid shut, built-in mic off, holding at HH:MM` mail in place
  of `mic ->`, starts no capture on the built-in (and stops one that runs) and
  holds; it does not pick another input. The next capture that hears sends
  one `ears: hearing again`. The lid is read only while the built-in is
  the target, at most every 5 s (`EARS_LID_EVERY`; `EARS_LID_PROBE` swaps
  the probe); a Mac with no lid counts as open. The built-in is now known
  by its `builtin` transport in `devices.json`, or a `MacBook ...
  Microphone` name while that file is stale, so the EARS-2 fallback off a
  virtual input works on an Air (`EARS_BUILTIN_MIC` still overrides).
- When two inputs are `builtin`, the keeper's built-in mic is the one named
  like `MacBook ... Microphone` (EARS-341). A headset on a MacBook's jack,
  `External Microphone`, sorts first and may be `builtin` too, so the keeper
  could hold that working headset with the lid shut, or let the real
  built-in mic take over when the AirPods left.
- The keeper reports a mic that gives only zeros (EARS-341). Four of the
  room capture's heartbeats in a row with `peak` 0 (`EARS_ZERO_BEATS`) send
  one `ears: DEAF, mic gives only zeros on DEVICE at HH:MM` mail; a quiet
  room, whose peak is low but never 0, sends none. The keeper reopens that
  capture once, then holds with no restart loop, and the next heartbeat with
  sound sends one `ears: hearing again`. A restart wait belongs to the
  input it was armed on: a FOLLOW move to a new input drops it, and it never
  holds a start on another input. A port in an STT URL (`:443`) is no
  longer read as the HTTP code.
- The keeper reports failing speech to text (EARS-341). Two room-capture
  stops in a row with `STT failed` in the error, and nothing heard between
  them, send one `ears: DEAF, STT failing (500) at HH:MM` mail; the code
  comes from the error, `(?)` when it has none. The next partial or turn
  with text sends one `ears: hearing again`. A room capture that ends by
  itself having heard nothing is restarted after 30 s, doubling to 5 min
  (`EARS_STT_DEAF`, `EARS_BACKOFF_MIN`, `EARS_BACKOFF_MAX`), not at once,
  and while the room is DEAF its restarts are only noted. The Silero model
  arriving after a model DEAF episode is now one mail, `hearing again`,
  sent once the capture is seen running, where it was two.
- `docs/barge-in.md` (EARS-351): what barge-in does (the dip, the cut, the
  replay of the unsaid rest and its 20 s window), that headphones and a
  call share one path (file and function for each step), and every knob
  against road noise with its default and where it is set. Linked from
  `docs/mouth.md`.
- The speakers side-car reopens a capture stream that has gone deaf
  (EARS-342, EARS-226). Such a stream stays open and active but
  delivers no audio. After `--deaf-lines` mic lines in a row at 0% coverage
  (default 10), it logs `mic: N lines at 0% coverage: reopening <device>`,
  then closes the stream, rescans PortAudio's device list and reopens the
  device. This happens at most once a minute. Until now, only a restart
  ended such a stream: one ran for 3h19m. Each row in `speakers_DAY.jsonl`
  now carries `cover`, the share of the line its device's audio covered,
  rounded to 2 places. The `why` text is unchanged.
- A restarted keeper adopts a running capture only if it still beats
  (EARS-226). If the capture's last heartbeat is older than
  `EARS_BEAT_KILL` (300 s), or it has none at that age, the keeper does not
  adopt it: one `ears: DEAF, no heartbeat since HH:MM` mail, a hard stop and
  a fresh capture. A heartbeat with no `pid` counts when its session is the
  room capture's. The keeper now launches the capture with `exec`, so no
  `ears-keeper.sh` shell is left behind as the capture's parent (ppid 1)
  after a restart. A keeper also names itself in `ears-keeper.pid` and ends
  the loop an earlier one left running.
- The keeper judges its room capture by its heartbeats, not just by its
  pid (EARS-226). A capture alive with no heartbeat for 120 s
  (`EARS_BEAT_STALE`) is reported once as
  `ears: DEAF, no heartbeat since HH:MM on DEVICE`. One with none for 300 s
  (`EARS_BEAT_KILL`) is stopped and started fresh, and its first heartbeat
  sends `ears: hearing again`. A heartbeat without a `pid` (a capture from
  before that field) counts when its session is the room capture's.
- The keeper reports a deaf capture one way, through one path (EARS-226).
  A capture that looks alive but hears nothing sends one `ears: DEAF, ...`
  mail and writes one `ears-deaf` heard event, then sends one
  `ears: hearing again ...` mail and writes one `ears-hearing` event when it
  recovers. Nothing repeats in between, and the `capture restarted` mails
  become keeper-log notes while the room is DEAF. A missing Silero model is
  the first cause to report this way, so its mails change to
  `ears: DEAF, no Silero model: run ears install EOM` and
  `ears: hearing again on DEVICE at HH:MM EOM`.
- Every heard-log `heartbeat` now carries the capture's `pid`, as
  `listen-started` already did (EARS-226). The keeper can then tell its room
  capture's beats from a call leg's in the same log. The field is additive:
  readers ignore it.
- The ears say when the recogniser falls behind (EARS-192). When a partial,
  or a heartbeat's loop lag or STT backlog, is over 2 s late
  (`$EARS_LAG_WARN_S`), the capture writes one `recogniser-lag`
  event for the episode and one `recogniser-lag-ok` when it has caught up.
  The keeper mails the alarm, at most once every `EARS_LAG_MAIL_GAP` seconds
  (600). Nothing restarts the recogniser on its own.
- sessionvoice manages its own speech server, and VoiceMode is no longer
  a prerequisite for it (EARS-291). `ears services install` now has a
  `speech` service, part of a bare install. It first asks what holds
  `127.0.0.1:8890`. An mlx-audio server already there (VoiceMode's, for
  example) is used, and nothing is started. Anything else there is refused
  by name and never replaced. A loaded `mlx_audio.server` job whose port is
  closed between restarts also stops it, so it never starts a second
  server. Only an empty port with no such job builds sessionvoice's own
  venv (`~/.local/share/ears/speech/venv`, `mlx-audio==0.5.8`) and loads
  `com.sessionvoice.mlx-audio`. A `final.url` elsewhere opts the ears out.
  `--dry-run` says which step it took, and `ears services status` has a
  `speech` row naming who serves `:8890`. VoiceMode's own installer does
  not check, so running it afterwards can still load a second server. The
  README's Requirements, `docs/models.md` and `commands/ears-setup.md` say
  how the server is found or installed.
- The ears can prime STT with the seats' names (EARS-246), off by default:
  `"stt_hint": true` in ears.json, or `EARS_STT_HINT=1`, turns it on. Each
  turn's final re-decode then carries a `prompt` built from the wake file's
  seat keys as a bare list ("Carol, Dave."), never from their sound-alikes, so
  whisper hears "hey Carol", not "hey Carl". Partials never carry it, and a
  transcript that is only the prompt's words is dropped as an echo. It
  follows the wake file live. `ears wake add SEAT --hint NAME` sets how a
  name is spelled. Parakeet and Apple ignore the field.
- `mouth gain VOICE [DB|reset]` (EARS-214): show a voice's gain as JSON
  (`gain_db`, `measured_lufs`, `gain_from`, the settings path, and the level
  a line gets now), set a hand gain of -24 to 24 dB (`gain_from: hand`), or
  `reset` it to the measured gain. A bad value, a voice that names no
  directory, or a settings file a hand broke exits 2 and writes nothing.
- One barge policy on a call and off it (EARS-315). A sound ducks the line,
  `barge_words` (2) of your words cut it, a stop ask cuts at once; a call
  line ducks in `CallOut.write`. This supersedes the one real word on calls.
  The duck follows your voice (back up `barge_duck_recover_s` after it
  stops, capped at `barge_duck_max_s`, 4 s), a line that starts under your
  voice starts ducked, `barge_ignore_words` is a setting, and `mouth barge`
  is the model's timed override (`N|off`, `--duck`, `--duck-max`, `--ignore`,
  `--for`). A cut line names the words that cut it (`cut_by`, `cut_how`,
  `barge_words`, `barge_words_from`). `$MOUTH_BARGE_WORDS_CALL` is
  retired: not honoured, and the player warns once if it is set.
- The operator and the privileged seats come from the host's operator block
  (`session operator`, sessiontools' `~/.settings/session/session.json`),
  never from a name in this code (EARS-304). One reader, `ears.operator_config`,
  runs the CLI; nothing parses session.json. Configured, the mouth also
  speaks for a privileged seat on another host, and `$MOUTH_MAIL_ALLOW`
  only narrows (it never adds). `barge_in` defaults to the operator's mailbox
  in the room. The keeper's notices go to the operator when `EARS_NOTIFY` is
  unset (`EARS_NOTIFY=""` mails nobody). Unconfigured, or with no `session`
  CLI, everything is as before. When the reader does not answer and ALLOW
  admits a sender alone, the mouth warns (the known limit; README, "Operator
  and privileged seats"). The keeper's example files use placeholders now:
  `wake.example.json`'s seats (it had mirrored one machine's live seat
  names) and `voice-prefs.json`'s hearts (it had been seeded with two clone
  voices that only one machine has). The `mouth --help` voice examples are
  placeholders too.

- `ears services install` installs the turn mailer that pages an agent,
  `com.voicemode.ears-mail` (`keeper/ears-mail.py --to device`, now shipped in
  the wheel), in a bare install beside the mouth and the keeper (EARS-222).
  A hand-made `dev.voicemode.ears-mail` already on the host is left alone and
  ours is skipped (and removed, if it was installed before it), said loudly:
  never two mailers. `status` and `uninstall`
  know `mail`.
- Install checks, all listed by `--dry-run` too (EARS-222). It checks postfix
  and never configures it. It makes the nested `heard` box under the one store
  dir that carries `.tm-store`, then runs `vmailbox-gen`, and prints the
  `heard+<device>` box to subscribe to. It refuses with no Silero model, or
  links VoiceMode's copy. It probes a new `final` engine with one short clip,
  and falls back to whisper `:2022` when it fails. Each probe prints
  `probing <url> (read-only)` before its request, under `--dry-run` too.
- `ears services install --agent NAME` adds NAME's wake seat and sends the
  keeper's notices to NAME (`EARS_NOTIFY`). `--notify ADDR` sends the notices
  elsewhere. Without `--agent`, install says nobody will be woken.
- `docs/models.md`: every model the ears, the mouth, Talk and the speakers
  side-car use, and how each one is served. Each entry gives the default and
  the setting that changes it, the size on disk, and the licence. It ends
  with the models that VoiceMode, sessionphone and the voice cloner call
  (EARS-262).
- The mouth's `said` record carries `device_gaps`, `device_gap_s` and
  `device_gap_max_s`: break-ups the output device inserted because the
  mouth's audio callback ran late (a starved host). They are measured from
  the device clock, not PortAudio's underflow flag, which reads 0 on CoreAudio
  through real gaps. They sit beside `underruns`/`underrun_s`, which still
  count silence from synthesis falling behind. On `null` and `call:` they
  are 0.
- `mouth duck off|on [--for T]` (default 1 h), `mouth duck reset`, and
  `mouth duck` to show the state and time left: a temporary override of the
  music's dip under speech, for karaoke (EARS-295). It
  reaches a file already playing, with no restart. `mouth status` gains a
  `duck` key. The order: per-call `--duck`/`--no-duck` > the override >
  `music_duck` (session > seat > agent > settings) > on. A skipped dip writes
  `event: duck-skipped` with `by`; `said` gains `ducks_skipped`.
- Mouth: a lead buffer, so a clone line need not chop when the clone renders
  slower than real time (EARS-285). A line with a lead holds its first frame
  until that many seconds of its audio are buffered, or the whole line if it
  is shorter; a stop still cuts it while it holds. The default is 0, no added
  wait, for every backend. Ask per line with `mouth say --lead S`,
  `mouth.say(lead_s=S)` or a mail's `X-Mouth-Lead: S`, or for every line with
  `lead_s` in the mouth's settings (the seat's cascade or the host's
  `mouth.json`). For no chop on a clone, pass 2.5: a clone renders at only
  0.93-0.96x real time on an idle MacBook Air, and measurement sized the cure
  at 2-3 s. It costs about 2.3 s more before a cold clone line starts at that
  rate, and 0.6-1.5 s on a second Mac. `saying` logs `lead_s`, `lead_from`
  and `held_s`, so time
  to first audio is `gen_s + held_s`. Details: `src/mouth/DESIGN.md`.
- Barge-in: the words that never count as the user's are now a setting,
  `barge_ignore_words`, a list of strings in the mouth's settings (EARS-270).
  Left unset, it is the built-in list of 159 filler and stop words, so nothing
  changes until you set it. `[]` counts every word, and deleting the key
  restores the default. There is no environment variable. It is read once a
  line, and it changes the barge count only, not the echo test.
- Barge-in explains itself in `player.log` (EARS-270). For each of the user's
  partials that barge-in weighs while a line plays, there is a
  `kind: barge` record,
  printed once and again only if its classification changes. It holds
  the words dropped and why (`ignored`, `own`), or the partial skipped and why
  (`echo`, `speaker`, `unnamed`); the words counted; the running `count`
  against `need`; the `ignore` list in force (`default` or `setting`); and the
  `verdict` (`wait`, `cut` or `stop-ask`). A line that had a barge duck also
  gets one `kind: duck` record when it ends: its `onsets`, `dip_ms` (from the
  user's onset row to the dip), `applied_ms` (from the dip to the device),
  `recovered`, `cut` and `reason`. To read them (player.log also holds the
  player's stderr, so the lines that are not JSON are skipped):
  `jq -cR 'fromjson? | select(.kind=="barge" or .kind=="duck")' player.log`.
- Docs: how to play music or a sound for the user through the mouth
  (`mouth play`, which ducks under speech), and a bullet for it in the ears
  skill.
- Docs: the configuration and the settings pages (EARS-394).
  `docs/configuration.md` says which files and variables each part reads,
  and where each one lives. `docs/settings.md` lists every key of
  `ears.json`, `ears-sounds.json` and `mouth.json`, with its type and
  default, says which keys the mouth reads from the agent, seat and session
  layers (the ears read only their own file), and names where the schema and
  the code differ. The README gains Configuration and Logs sections, with
  `ears services status` as the Health line.
  `tests/test_configuration_docs.py` holds the pages to the schemas and the
  code.

### Changed

- Every launchd label the kit ships is now `dev.sessionkit.voice.<job>`
  (EARS-407). `ears services install` loads `dev.sessionkit.voice.ears-<name>`
  and `dev.sessionkit.voice.mlx-audio`, and the `launchd/` hand templates are
  `dev.sessionkit.voice.ears-keeper`, `-ears-speakers`, `-ears-mail`,
  `-ears-sounds`, `-mouth-sounds` and `-talk`, file and label.
  `bin/multi-output.swift` gives a new device the UID
  `dev.sessionkit.voice.multi-output.<name>`; a device made before keeps its
  old UID and still works, since devices are found by name. Three templates
  now carry install's own label, so install tells its own plist from a hand
  render by content: every plist it writes has
  `SESSIONKIT_INSTALLED_BY=ears services install` in `EnvironmentVariables`.
  A plist at its label without that is hand-made: install never overwrites
  it, skips its own job and says so, and `status` names it. A hand keeper
  under its old template name is now recognised too, so install no longer
  loads a second keeper beside it. **Upgrading:** `ears services install`
  moves each service it installs off its old labels
  (`com.sessionvoice.ears-<name>`, `com.sessionvoice.mlx-audio`,
  `com.voicemode.ears-<name>` and the old call and room agents): each is
  booted out once and its plist removed, and the new label loads only once
  the old has gone. It still replaces the legacy renders
  (`dev.voicemode.ears-mail`, `dev.voicemode.mouth-mail`,
  `com.failmode.mouth-mail`). A hand-rendered job is never moved for you:
  boot it out, remove its plist, and render it again from the renamed
  template (the old `dev.sessionvoice.*`, `dev.voicemode.*`,
  `com.failmode.ears-keeper`, `com.failmode.ears-speakers` and
  `com.failmode.speaker-sidecar` names). Until then install skips its own
  job beside a hand mailer, keeper or side-car, and `ears services status`
  names every old-form job it finds. The move, step by step:
  `docs/upgrade-labels.md`.
- Every voice settings file (`ears.json`, `wake.json`, `ears-sounds.json`,
  `mouth.json`, `mouth-sounds.json`, `stt.key`, `tts.key`) now lives in
  `~/.sessionkit/voice/settings/`, under the same filename, and the session
  file is read from `~/.sessionkit/tools/settings/session.json`. The
  commands, the keeper scripts, the launch agent templates and the schemas
  all use the new folder, and nothing falls back to the 0.1.0 folders. A
  file still at its 0.1.0 path, with none in the new folder, is named once
  per run on stderr and is never read or written; the defaults apply until
  it is moved (docs/settings.md, "Upgrading from 0.1.0"). The variables
  that name a file elsewhere (`EARS_CONF` and the rest) are unchanged.

- The README lists ducking and barge-in among its features (EARS-401).

- The README's links into the repository are absolute (EARS-392), so they
  resolve on the package page, which renders README.md with no repository
  around it. Each one names `https://github.com/sessionkit/sessionvoice/`
  with `blob/main/` for a file or `tree/main/` for a folder; `docs/` keeps
  its relative links. `tests/test_readme_links.py` fails on a README link
  into the repo that is not absolute, or whose file, folder or anchor is
  missing from the exported tree.

- The README's and the docs' links into this repo are relative (EARS-408),
  so they work in a checkout, offline, without opening a browser: from the
  README `docs/models.md` or `speakers/README.md`, from a docs page
  `settings.md` or `../README.md`. They were full github.com URLs on a
  `main` branch, which 404s, as the kit's public repos carry only master.
  A link to another kit repo stays a full URL onto `HEAD`, which GitHub
  resolves to that repo's default branch, and its text names the repo and
  the file (sessionkit's `docs/settings.md`), so a reader holding that repo
  can find it. Rendering the README for the package page is the release
  step's job, at publish time. `tests/test_readme_links.py` now fails on a
  full URL into this repo and checks that each relative link in the README
  and `docs/` resolves in the exported tree, anchors included.
  `tests/test_github_links.py` fails on any shipped file but this changelog
  that links this repo by full URL, or names a branch, rather than `HEAD`
  or a release tag, in a link to another kit repo's file.

- The text the code prints, logs, mails or speaks is neutral (EARS-390). The
  mouth's notices and the details they quote say "the user" where they said
  he, his or him: "The user's words cut it", "Dropped: the user asked for
  quiet", the `expired` details `the user asked for quiet` and `the user
  answered it`, and the say-again note's `The user said "..."`. ears-mail's
  empty subject is `(the user spoke)`. `ears services install` with no
  `--agent` warns that nobody will be woken "when you speak". `shadow/score`
  says Smart Turn would have "CUT THE USER OFF", and the Settings screen's
  Partials text says "your words while you are still talking". Tracker ids
  leave that text and stay in comments and this changelog: the mouth's URL
  refusal, the stale `MOUTH_BARGE_WORDS` warning, ears-mail's one-recipient
  error, and the `ears services status` weights header and mail-carrier
  lines.
  `tests/test_emitted_neutral.py` scans the shipped Python's string constants
  (not docstrings or comments) and the settings schemas' titles and
  descriptions, and fails on a tracker id, a he, him or his, or a weekday
  beside a date.

- The tests and docs hold no captured real time (EARS-393). The replayed
  heard-log rows (`tests/test_echo_cut.py`), the barge-in replay and its
  onset (`tests/mouth/test_mouth_barge_log.py`, `test_mouth_onset.py`) and
  the `docs/logs.md` example rows are each moved by one constant onto one
  synthetic base date, so every delta, the order, the milliseconds and
  each seq are as recorded and the tests prove the same things. The
  example's ns mail id moves with them. Captured epochs (a web call id,
  ears and notice message ids, the audio-devices example) move to the house
  epoch, keeping their differences. Incident clocks in prose go too, and
  each keeps its date and durations: two ranges in
  `docs/troubleshooting.md`, one call's turn and TTS times in
  `docs/architecture.md` and its built page, and the clocks in test comments
  and docstrings. The barge-in lines in `test_mouth_barge_words_two.py` now
  say eight in the morning, as the replay does. The shared recording-switch
  contract string no longer holds the time it was frozen at; it reads
  `EARS-387 recording switch contract, rules 1-11`, so a copy of
  `tests/fixtures/recording-switch-cases.json` pinned by hash needs the new
  hash. `tests/test_no_real_times.py` scans
  `git archive HEAD` for a fractional ISO time with a +10/+11 offset, a
  10-digit epoch and a 19-digit ns epoch (inside a Message-ID too), and fails
  on any hit its in-file list of synthetic rows (path, text, count) does not
  name, or on a listed row that is gone.

- Every date and time typed into the tests, docs and code comments is now
  visibly synthetic: it falls in 2030 or later (EARS-397). A test that needs
  a fixed moment imports it from `tests/_anchor.py`; a family of related
  values moves by one whole-week shift, keeping its weekdays, daylight
  saving, order and gaps. Comments that named the day a measurement was
  taken no longer name it. `tests/test_no_real_times.py` no longer keeps a
  list of synthetic rows. It fails on any date (also written as a year and
  month, or with slashes), compact stamp, epoch second, ms or ns epoch
  (digit-grouped too), month named beside a year (a mail Date included) or
  date tuple in `git archive HEAD` that falls before 2030, except in
  `uv.lock`, the root `LICENSE`, the CHANGELOG's release headings and the
  JSON Schema dialect id. A real date planted in a scratch repo is caught. The `tests/fixtures/verify_json/`
  goldens are copied again from sessionmail, whose mail Date there is
  already synthetic.

- Retention of kept audio follows `record_days` for both kinds (EARS-387).
  Missed-reply clips were always deleted after two days; they are now kept
  for `record_days` (7 by default, `0` = kept forever), like each turn's
  audio. Both prunes run only while recording is on, read when the prune
  runs: with recording off, nothing on disk is deleted. Each prune deletes
  only the files the ears wrote in their own folders (`<home>/audio/` and
  the `missed-reply_*.wav` clips in `<home>/state/missed-reply/`).
  `tests/test_listen_missed_reply.py`, `tests/test_turn_audio.py`.
- Missed-reply clips are now off unless recording is on (EARS-387). The
  keeper's room capture still passes its 30 s window, but a clip is written
  only while the recording switch is on, checked as each clip is about to
  be written. Off, the default, writes no file and one `missed-reply-off`
  event (`why: recording off`). To keep clips as before, run `ears
  recording on`, or set `EARS_RECORD=1` in the keeper's environment.
- The mouth's drop and say-again notices go by sessionmail's `message send`,
  not postfix's `sendmail` (EARS-381). On a Mac with no postfix they can now
  be delivered through sessionmail's mail drop (`SESSIONMAIL_SENDMAIL=drop`).
  There is no fallback to postfix's binary: with no `message` binary, or a
  refused send, the said row reads `failed: no message binary (...)` or
  `failed: message send exit N kind K: ...`. Notices go From the seat `mouth`
  (name `Mouth`), unsigned. A player started by a CLI `say` no longer passes
  its caller's `CLAUDE_SESSION_*` key, session id or must-sign switch to the
  send, so a notice never signs as its caller. `MOUTH_SIGNER_SOCK` and
  `MOUTH_SIGNER_PUB`, both set, give the mouth its own key, and must-sign
  turns on. `MOUTH_SENDMAIL` keeps its name. It is the command notices go
  through, and it now takes `message send`'s arguments. The default is
  `message` on PATH, used only by the mouth service (EARS-346); a fenced
  process reads `failed: message send fenced: ...`.
- A mouth notice is now sent by sessionmail's `message send`, inline, so a
  cut or a say-again may pause the next line by a fraction of a second.
  Normal play is unchanged.
- The keeper's turn mail goes by sessionmail's `message send`, not postfix's
  `sendmail` (EARS-381). `keeper/ears-mail.py` no longer names an MTA, and
  there is no fallback to one. On a Mac with no postfix a turn can now be
  delivered through sessionmail's mail drop (`SESSIONMAIL_SENDMAIL=drop`).
  The command is `EARS_MAIL_MESSAGE`, else `message` on PATH, found once at
  start; while it is missing it is looked for again at each send, so a
  `message` installed later is used with no restart. A missing binary, a timeout or a refused send is a fire-log row
  with `exit` and `err` (`no message binary (...)`, or `kind: why` from
  the send), and the keeper keeps running. A turn still goes To and From
  `heard+<device>@<host>`, display name `Ears`, unsigned. A keeper started
  from a seat's shell no longer passes that seat's `CLAUDE_SESSION_*` key,
  session id or must-sign switch to the send. `EARS_MAIL_SIGNER_SOCK` and
  `EARS_MAIL_SIGNER_PUB`, both set, sign it, and must-sign turns on. The
  keeper mints no Message-ID now: the send does, and the fire log's `id` is
  the one it returned. A turn's subject is ASCII (curly quotes, dashes and
  accents folded), because `message send` refuses any other; the body keeps
  the words as heard. The hand-made template
  (`launchd/dev.sessionvoice.ears-mail.plist.in`) puts `~/.local/bin` on
  PATH, where uv puts `message`.
- Under `SESSIONMAIL_SENDMAIL=drop`, heard turns read as plain mail until
  sessionmail qualifies From (asked of sessionmail as SM-847), because the
  drop writes From with no `@host` and the pager trusts only a qualified
  keeper From (EARS-381). The launch agents do not see a `SESSIONMAIL_SENDMAIL` set in
  your shell, and nothing copies it into a plist: on a Mac with no postfix
  they deliver nothing until sessionmail reads the carrier from its settings
  file (asked of sessionmail as SM-660).
- `ears services install` checks sessionmail's `message` and its carrier,
  not postfix's `sendmail` (EARS-381). It finds `message` on the services'
  own PATH and says `message MISSING` (naming sessionmail) when it is not
  there. It reads postfix with `postconf`, and `SESSIONMAIL_SENDMAIL` from
  the installer's shell: `drop` is said as the mail drop, with no postfix
  needed. With no postfix and no drop, it says that `message send` needs
  `SESSIONMAIL_SENDMAIL=drop` (sessionmail SM-735). With a carrier set, it
  says that the launch agents will not see it. It configures neither.
- The mouth's send budget per burst of notices is 15 s, up from 5 s
  (EARS-381). A `message send` takes about 1.2 s, so at 5 s a burst of six
  notices sent four and dropped the rest. `BURST_MAX_S` (30 s) still bounds
  a flood.
- A notice's Message-ID is now `<ns.uid.mouth-notice@<host>>`, the form
  `message send --message-id` takes (EARS-381). A reply to a notice in either
  form, the new one or the old `<ns.uid@<host>.mouth-notice>`, is still filed
  unspoken. A notice's subject is ASCII, with a backtick or `$(` defused,
  because `message send` refuses any other; the body keeps the line as it was.
- The supported Python range is now 3.10-3.14 (EARS-358):
  `requires-python = ">=3.10,<3.15"`. mlx 0.32.2 and onnxruntime 1.30.0
  ship no wheel for Python 3.15 and no sdist, so the capture, silero,
  nemotron and all extras could not install there. On 3.15, pip and uv now
  refuse sessionvoice up front instead of failing partway through an
  install. `tests/test_lock_wheels.py` checks every Python the declared
  range admits. An uncapped range is always checked through 3.15, so a
  range cannot claim a Python the locked wheels do not cover.
- `CHANGELOG.md` merges with `merge=union` (`.gitattributes`), so two
  branches that each add a bullet under `[Unreleased]` no longer conflict
  (EARS-356). `tests/test_changelog_union.py` replays four real clashes with
  and without it, and fails on a duplicated bullet, one way union damage
  shows. Union keeps both versions of a bullet edited on both sides without
  a conflict, so `AGENTS.md` says never to rewrite a bullet that has landed
  on master.
- The heard log's format is sessionvoice's contract (EARS-294). The new
  "Who owns this format" section of `docs/logs.md` says sessionvoice owns
  heard-log v1 (`SCHEMA` in `src/ears/heard.py`). The path, under the base
  dir, is part of the contract, with `heard.lock` beside it. VoiceMode's
  `converse` and the Delta bridge are guests that write v1 lines and take
  the lock. A format change bumps `v`, and readers ignore the fields and
  kinds they do not know. `heard.py`'s docstring names sessionvoice as the
  owner. No path or variable changed. `tests/test_heard_contract.py` holds
  the documented version to `SCHEMA`.
- The docs and the package description no longer describe sessionvoice as
  part of VoiceMode (EARS-294). The package is "one listening engine for
  coding agents and the Delta bot". The README no longer says the live
  player runs VoiceMode's copy: the player is this repo's `mouth mail`. The
  architecture page and `docs/internal/` are marked as historical. Comments
  and docstrings that named VoiceMode modules, or said a crash would take
  VoiceMode down, now name sessionvoice's own parts. The settings schemas
  title the speech servers "mlx-audio" and "whisper.cpp", without
  "VoiceMode". Wording only: no variable, path or launchd label changed.
- Ten of the ears' settings have names of their own, in place of VoiceMode's
  `VOICEMODE_` prefix (EARS-294): `EARS_LISTEN_STT_URL`, `_STT_MODEL`,
  `_STT_KEY`, `_SILENCE_S` and `_VAD_AGGRESSIVENESS`, `EARS_SILERO_MODEL`,
  `EARS_CALL_SOCKS`, `EARS_LAG_WARN_S`, and `EARS_HEARD_EVENT` and
  `_CALLER`. The old names are still read, with no warning, as deprecated
  aliases; `docs/ears.md` lists them. The new name wins when both are set,
  and a name set to the empty string counts as set. Every read goes through
  one table, `src/ears/envnames.py`, which a test holds every reader to. The
  test fence (`tests/env_fence.py`) pops or sets each fenced variable under
  all of its names.
- The mouth's 39 settings in the environment are `MOUTH_<X>`, in place of
  VoiceMode's `VOICEMODE_MOUTH_<X>` (EARS-294): `MOUTH_DEVICE`, `MOUTH_VOICE`,
  `MOUTH_SENDMAIL`, `MOUTH_NOTICE`, `MOUTH_SETTINGS`, `MOUTH_SESSION_BIN`,
  `MOUTH_VERIFY_BIN` and the rest; `docs/mouth.md` lists them all. The old
  names are still read, with no warning, as deprecated aliases, so a launch
  agent that sets `VOICEMODE_MOUTH_DEVICE` keeps working. The new name wins
  when both are set, and a name set to the empty string counts as set:
  `MOUTH_SESSION_BIN` and `MOUTH_VERIFY_BIN` set to anything are still off
  switches, under either name. The reads go through the ears' table
  (`src/ears/envnames.py`). The test fences, the conftest's and the mouth
  tests' own, set or clear each variable under both names. The review-rig
  rule in `AGENTS.md` names `MOUTH_SENDMAIL` and `MOUTH_NOTICE`.
- The launch agents `ears services install` writes are
  `com.sessionvoice.ears-<name>`, in place of `com.voicemode.ears-<name>`
  (EARS-294). A pull moves nothing: a host's old jobs run the new code under
  the old label until install runs. Install moves each service it installs.
  It boots the old label out once, removes the old plist, and loads the new
  label once the old has gone, with at most one successful load. If the old
  job is still loaded after 10 s, that service FAILS with the label named,
  the new one is not loaded beside it, and install exits 2. A second install
  makes no call that names an old label. `ears services status` reports an
  old label still loaded, `ears services uninstall` removes both labels, and
  `ears speakers` restarts whichever speakers label is loaded, the new one
  first. The hand templates are `launchd/dev.sessionvoice.*.plist.in`, in
  place of `dev.voicemode.*`, and their headers say how to boot out the old
  label. Install skips its mailer when a hand mailer runs under either
  `dev.sessionvoice.ears-mail` or `dev.voicemode.ears-mail`.
  `tests/test_services_label_move.py` drives a fake launchctl.
- sessionvoice's state has its own home, `~/.sessionvoice`, and nothing is
  moved (EARS-294). One rule, `src/ears/home.py`, finds it:
  `$SESSIONVOICE_HOME`, then `$VOICEMODE_BASE_DIR` (still read), then
  `~/.sessionvoice` if it holds the heard log, then `~/.voicemode` if it
  does, else `~/.sessionvoice`. A folder holds the heard log when its
  `logs/conversations` has a `heard.lock` or a `heard_*.jsonl`. So a host
  whose heard log is in `~/.voicemode` keeps using it there, and a new host
  starts in `~/.sessionvoice`. The heard log, the speakers rows, the mouth's
  folder, talk's token and the ears' state all follow it. Every script that
  named `~/.voicemode` for them uses the rule, and the scripts that import
  nothing from the package carry a short copy that `tests/test_home.py`
  holds to it. If both folders hold a heard log, `ears services status`
  warns that the log is split, and names the fix: on a fresh host that also
  runs VoiceMode's `converse` as a heard guest, set
  `SESSIONVOICE_HOME=~/.voicemode` (or `VOICEMODE_BASE_DIR`), so that both
  write one log. VoiceMode's own files stay in `~/.voicemode`.
- `ears-arm` and `ears-count` are on PATH by bare name, through the plugin's
  `bin/` (EARS-363). `bin/ears-arm` and `bin/ears-count` are shims in the
  `bin/ears` pattern that exec the ears skill's scripts, so the one
  implementation stays in `skills/ears/scripts/`, and that path still works.
  This reverses EARS-289's placement ("stays where it is", under Fixed) on
  a later decision. The README, `docs/ears.md`, the ears skill and
  `bin/README.md` use the bare names.
- Release metadata (EARS-352). The version is 0.2.0 in `pyproject.toml`,
  the plugin manifest and the marketplace manifest. The author is
  `Mike Bailey <mike@sessionkit.ai>` in all three. The package now lists its
  classifiers (`Development Status :: 3 - Alpha`, `Operating System :: MacOS`
  and `Programming Language :: Python :: 3`) and gains Homepage and
  Repository URLs (<https://github.com/sessionkit/sessionvoice>). The plugin
  manifest gains `homepage` and `repository` with the same URL.
- No behaviour default names a particular seat or person (EARS-333). The
  "Ears: live" page labels the agent's lines with `EARS_VIEW_LABEL`, which
  `view/push` sends as the payload's `label`, and shows `Agent` when it is
  unset. `bench/clone_live_ab.py` now requires `--voice`: clone voices
  belong to a host, so there is no default to fall back on.
- A seat sets its voice once, and `mouth say` respects it without `--voice`
  on every line (EARS-329). A caller's `$MOUTH_VOICE` now outranks
  the host's settings file for that caller's own lines. It still ranks below
  the session, seat and agent `mouth.voice`, and it never gets past a host
  that locks `voice` (`"_locked": ["voice"]`). `mouth voices` reports
  `"from": "env"` when it answers. A mailed line with no `X-Mouth-Voice`, and
  a line the player replays, still read the settings file before the mouth's
  own environment. docs/mouth.md gains "Your voice": the full order, where a
  seat's file lives, and one worked example.
- A voice's `settings.json` with a `gain_db` the mouth did not write (no
  `gain_from`, a null one, or any value but `measured`, e.g. `{"gain_db": 6}`)
  is a hand gain (EARS-214). It plays at that gain, reports `from: "hand"`,
  and is no longer measured over. `mouth render` stays unlevelled, by
  decision: it writes the raw synthesis whatever the voice's gain.
- docs/ is one flat folder of Markdown (EARS-266). `docs/architecture/README.md`
  is now `docs/architecture.md` (its page, `docs/architecture.html`, beside it),
  and `docs/design/phone-voice.md` is `docs/phone-voice.md`. The page's builder
  moved to `bin/build-architecture-page`. Links are plain Markdown links, every
  page opens with its point, and `tests/test_docs_layout.py` holds all three.
  `docs/internal/` is unchanged.
- The keeper's launch agent passes `EARS_LOG_DIR`, so the keeper's own logs
  land beside the agents' logs (EARS-222).
- A keeper with no Silero model says so once (a log line, and a mail when
  `EARS_NOTIFY` is set) and holds, re-checking every `EARS_MODEL_RECHECK`
  seconds (default 30). It no longer starts a capture that dies "Silero model
  not found" every few seconds (EARS-222).
- The README's proof of a working install is "you speak, and the agent WAKES
  and answers", not "a heard line appears".

- Barge-in: two content words from the user now cut a line they talk over,
  not three (`barge_words` default 2; EARS-274). Filler and stop words
  still never count, and a call line still cuts on one. On AirPods the
  onset-to-cut fell from 5.3 s and 2.5 s to 3.0 s and 1.4 s in replay.
  `barge_words` in the mouth's settings or `$MOUTH_BARGE_WORDS`
  sets it back to 3.
- **Ducking off now means "don't dip"** (EARS-295; supersedes EARS-152's
  same-device clause). Hosts with `music_duck: false` today, and any
  `mouth play --no-duck`, change on deploy: before, the user's words CUT the
  played file and a spoken line WAITED for it to end; now the file plays on
  at its own level, the user's words never cut it, and lines mix over it. Only
  `mouth stop` or `mouth retract` stops it. `music_duck: false` or a
  `mouth duck off` override now also stops the DJ (mpv) dip set by
  `$MOUTH_DUCK`. Check each host's `music_duck` before deploying.
- `keeper/mouth-sounds.py` waits on a kqueue over its queue folder, maildir, mid-turn
  file, cursors and stop file instead of sleeping 50 ms between looks, and re-reads a
  heard cursor only when it changed. Idle CPU on a Mac with 400 cursor files fell from
  6.4% of a core to 0.7%; a sound that is already known plays within 1 ms of landing
  (was up to 50 ms). Where kqueue is missing, or `MOUTH_SOUNDS_WAIT=poll`, it polls
  every 250 ms. No change to which sounds play.
- The ears' blips (turn, partial, turn-discarded) now go into ONE output
  stream, held open while any sound is on, instead of opening and closing a
  stream for every blip (EARS-281). Over 60 s of partials at
  75 a minute on a wired display: 82 stream opens fell to 1, the watcher's time
  per blip from ~0.28 s to ~0.012 s, and lag p95 from ~0.33 s to ~0.07 s. The
  stream reopens when the `to` output or the system default changes, and after
  a PortAudio error (one rescan and retry, as before). It closes while no sound
  is on. Each sound-log record carries `opens`. Cost: idle CPU of ~0.2% of a
  core becomes ~0.6% while sounds are on. A real device switch, AirPods and
  laptop speakers are not yet measured.

### Removed

- The player's warning for a set `MOUTH_BARGE_WORDS_CALL` (EARS-390). The
  variable stays retired and ignored, under both names; `barge_warnings`
  and `mouth status` no longer list it.

- Internal working notes are left out of the release archive (EARS-382):
  `keeper/QUESTIONS.md`, `keeper/ROADMAP.md`, `src/mouth/DESIGN.md`,
  `docs/phone-voice.md` and every Markdown note under `talk/`. The code
  beside them still ships. The links to them from `README.md`,
  `docs/ears.md`, `docs/mouth.md` and `docs/barge-in.md` are gone, and
  `tests/test_export_ignore.py` builds `git archive HEAD` and checks that
  none of them is in it.
- The legacy VoiceMode launch paths (EARS-294). The ears keeper no longer
  runs `voice_mode.listen` from a VoiceMode checkout when `EARS_W` is set,
  and `keeper/call-ears` no longer does when `CALL_EARS_CODE` is set. Both
  always run `ears.listen`. If either variable is still set, the keeper's
  log says once that it is ignored. `com.failmode.ears-keeper.plist.in`
  no longer sets `EARS_W`, so it renders with no value of yours.
  `keeper/mouth`, the launcher for VoiceMode's old `voice_mode.mouth`, is
  deleted: nothing ran it. `keeper/mouth-sounds.py` no longer watches the
  old mouth queue folder, `~/.voicemode/mouth/queue`, because no mouth writes
  there now: lines arrive in the mouth maildir. A `voice_mode.listen`
  capture that is already running is still adopted by the keeper and the
  speaker side-car, never started twice.

### Fixed

- `bin/mouth-timeline` runs the checkout's own `.venv/bin/python`, not the
  `python3` on PATH (EARS-402). On a Mac with only the system python, PATH's
  `python3` is 3.9, below the package's 3.10 floor, and every call failed
  with a TypeError. `MOUTH_PYTHON` still overrides it; with no such python
  the wrapper prints one line naming the floor and `uv sync`, and exits 2.
  The test suite's `ffmpeg_on_path` check now skips, naming the documented
  prerequisite (README, Requirements), where it failed; a failure that is not
  the missing ffmpeg still fails. A new guard runs the nine scripts that start
  on PATH's `python3` under a system python below 3.10, when the host has one.
- Two checks now tell a path that cannot be read from one that is absent
  the same way on every Python version. From Python 3.14,
  `Path.exists()`, `is_dir()` and `is_file()` return False where 3.13
  raised a permission error. The keeper's mail chime
  (`keeper/mouth-sounds.py`) now keeps what it last saw while the maildir's
  `new/` cannot be read, so a permission flap no longer chimes every line
  already waiting there. That burst came on 3.14 when the folder above
  `new/` flapped, and on every version when `new/` itself did. The missed-reply clip's synced-folder check
  now refuses a sync marker or `.git` it cannot look up. Before, 3.13
  crashed and 3.14 allowed it as local. Both now read with `os.stat` or
  `os.listdir`, never a `Path` predicate or `glob`.
- Heard turns are mailed again when launchd runs ears-mail with a short
  PATH (EARS-388). sessionmail's `message` is a uv tool, in
  `~/.local/share/uv/tools/sessionmail/bin` and linked into `~/.local/bin`,
  and a launchd PATH such as `/usr/bin:/bin:/usr/sbin:/sbin` holds neither.
  ears-mail looked on PATH alone, so every send failed with exit 127 and
  every turn was dropped. ears-mail, the mouth's notices, the mouth's mail
  verifier and the keeper's `mail_me` now look on PATH, then at
  `~/.local/bin/message`, then in uv's tool dir (`src/ears/message_bin.py`;
  ears-mail and the keeper keep copies, and `tests/test_message_bin.py`
  holds them to it). An explicit `EARS_MAIL_MESSAGE` or `MOUTH_SENDMAIL`
  still wins and is looked up on PATH only. A `message` found nowhere is
  still exit 127, and the reason now names every place looked.
- `ears services install` no longer leaves a stale launch agent running
  (EARS-388). A host kept a hand render under the old label,
  `dev.voicemode.ears-mail`, on a PATH that predates `message`. Install
  skipped ours beside it, so that stale job was the only mailer. Install now
  REPLACES it: a loaded one is booted out, its plist is moved aside to
  `<label>.plist.replaced-<stamp>` (kept, never deleted), and ours loads
  once it has gone. A job that will not go fails the service, and ours is not
  loaded beside it. The mouth's legacy players, `dev.voicemode.mouth-mail` and
  `com.failmode.mouth-mail`, are replaced the same way, so two players never
  drain one queue. `ears services status` names a legacy job still on the
  host. The hand template's own label, `dev.sessionvoice.ears-mail`, is
  still left alone.
- `ears services install` no longer says `message present` for a mailer that
  cannot find it (EARS-388). The check used our PATH even when a hand-made
  mailer was the one that ran. It now reads that plist's PATH and HOME and
  looks as ears-mail does. If `message` is reachable only by the uv-location
  fallback, it warns that the plist needs a re-render. An unreadable plist
  is `UNCHECKED`, never `present`.
- The ears-mail launchd template's PATH now holds `/opt/homebrew/bin` and
  `/usr/local/bin` after `~/.local/bin`, as the keeper's and talk's do
  (EARS-388). `tests/test_launchd_templates.py` checks every template whose
  job sends by `message`.
- A failed send is now loud in the service's log (EARS-388). ears-mail
  writes one `ears-mail: ERROR: send to <seat> failed, exit <code>: <why>`
  line to stderr for each send that fails, besides the `err` field in its
  fire row, and the mouth writes one `mouth: ERROR: notice to <addr>
  failed: <why>` line for each notice that does not go out. Under launchd,
  stderr is the job's log, so a missing `message` no longer drops turns
  with nothing said there. A sent mail writes no such line, and the fire
  row is unchanged.

- One dead seat box no longer silences a turn for every other seat
  (EARS-381). Under `--to seats`, ears-mail sends once per seat, because
  `message send` refuses a whole mail for one undeliverable address. Each
  copy keeps the one `Ears-Rule` naming every seat, so it tells the whole
  verdict, and each send is its own fire-log and `--dry-run` row.
- A bounce of a mouth notice is never spoken (EARS-381). A notice now goes
  out with the seat as its envelope sender, not a null one, so a postfix
  bounce comes back to the mouth's box. Before this, it would have been read
  aloud. `box.read` files a delivery report unspoken and logs
  `{"skipped": "delivery-report", ...}`. A mail counts as a delivery report
  if it is a `multipart/report; report-type=delivery-status` (or carries a
  delivery-status part) from any sender, or if it comes from this host's
  `MAILER-DAEMON` or `postmaster` with the bounced notice, or its headers,
  inside. `Auto-Submitted` alone never decides it. A report from another host
  is still refused (D11), and a plain mail from this host's MAILER-DAEMON
  follows the ordinary rules.
- `ears services uninstall --dry-run` changes nothing (EARS-379). Before,
  the flag was dropped and the dry run was a real uninstall: it booted out
  every label, old and new, in the user's own launchd domain and deleted
  every plist. A temporary HOME did not contain it, so on a host with live
  voice jobs a dry run took them down. It now prints `would boot out
  gui/<uid>/<label>` for each label and `would delete <plist>` for each
  plist that exists, and makes no launchctl call. `install --dry-run` was
  already clean; `tests/test_services_dry_run.py` now holds every dry run,
  install and uninstall, to a launchctl that fails the test on any change.
- talk writes a refusal's log line before it sends the 403, not after
  (EARS-373). The line is the same: `403 refused: ...`, or `socket peer
  refused: ...` in socket mode. In HTTP mode, a client that hangs up before
  its 403 is written is now logged too; before, only the stdlib's traceback
  reached stderr. Socket mode already logged that case. Tests that read the
  line once the client had its 403 could run before it was written, so they
  failed now and then.
- `ears services install` from a wheel installs the settings schemas
  (EARS-160). The wheel ships `settings/*.schema.json` as package data,
  `ears/_settings/`, and install finds them as it finds the keeper: a
  checkout's `settings/` first, then the package's. Before, from the kit it
  found none, printed `schema:  in ...` and succeeded, so Settings had no
  schema to draw. With no schema anywhere, install now refuses before it
  writes any plist, and exits 2.
- `ears install --apple` without `swift/` refuses (EARS-160). The wheel does
  not ship `swift/`: the Apple helper builds from a source checkout only. From
  the kit, `--apple` skipped the helper without a word, fetched Silero and
  printed `done`. It now exits 2 before anything is fetched, saying to run
  `uv run ears install --apple` in a git checkout.
- A bare `pip install sessionvoice` can play what the mouth speaks:
  `sounddevice` is a base dependency now, as `httpx` became in EARS-292
  (EARS-160). It stays listed in the `capture` and `all` extras.
- Smart Turn's ONNX backend has an extra, `sessionvoice[smart-turn]`
  (`onnxruntime` and `transformers`); `all` includes `transformers` too.
  `transformers` was declared nowhere before (EARS-160).
- An import that needs an extra says which one (EARS-160). Resampling
  (`capture`: scipy), the webrtc VAD (`capture`: webrtcvad), the Silero VAD in
  `ears.vad` and `ears.listen` (`silero`: onnxruntime), Smart Turn's ONNX
  backend (`smart-turn`) and the Parakeet re-decode (`nemotron`: mlx-audio)
  raise an `ImportError` reading `<what> needs <module>, which is not
  installed: pip install 'sessionvoice[<extra>]'`, in place of a bare
  `No module named ...`. Fallbacks still fall back: `--vad auto` to energy and
  the listen detector to its energy classifier, silently. The Silero
  classifier's error was a `SileroError` saying `pip install onnxruntime`.
- `ears eval`'s mlx engines say what their interpreter lacks (EARS-160).
  `eval_mlx.py` runs in `EARS_EVAL_MLX_PY`, not in ears' venv, so `soundfile`
  is not an ears dependency; when that interpreter lacks it (or numpy, or
  mlx-audio), each clip's error names the interpreter and the
  `-m pip install numpy soundfile mlx-audio` line for it.
- `listen: false` in ears.json now stops every ear, not only the room's
  (EARS-364). The keeper stops the call ears first, then every call's
  capture (`--source call:N`), then the room's capture, and starts none
  until `listen: true`. It reads `listen` in pinned-device mode too, not only
  when it follows the default input. It notes and mails each change once, and
  a keeper started with `listen: false` starts no ear. `listen: true` starts
  the call ears again, which give each live call an ear, and brings the room
  back. `bin/call-tap` pauses its far-end capture while listening is off and
  keeps its passthrough, the user's voice out to the call app, running. The
  capture comes back when listening is on. `call-tap down` still ends both,
  and `call-tap status` names a paused ear.
- A cut question the user answered is no longer replayed (EARS-365). When
  the user's words cut a line, the mouth replays its unsaid rest at the end of
  their turn; if those words were the answer, they heard an answered question
  again. Now, at the end of their turn, the replay is dropped when the
  sentence it was cut in ends in `?` and their turn holds 2 or more content
  words (not filler,
  not a stop ask, not the line's own echo). Its `said` row says `expired`,
  detail `he answered it`, and the seat's second notice says so (its first
  notice now names the drop too). Echo is judged against only what had played
  of the line, plus half a second: an answer in the words the line had already
  played still reads as echo, and is replayed. A cut
  statement, "hang on" or "wait", a blip and a one-word "yes" still get the
  replay. New mouth settings: `resume_answer_words` (2) and
  `resume_answer_question` (`true`; `false` drops any answered cut line),
  with `$MOUTH_RESUME_ANSWER_WORDS` and
  `$MOUTH_RESUME_ANSWER_QUESTION` below them. See `docs/mouth.md`.
- `mouth say` with no device plays on the settings file's `output`
  (EARS-361). The order is `--device`, then `$MOUTH_DEVICE`, then
  `output` in the host settings file (`mouth.json`); `mouth play` and
  `mouth again` follow it too. An `output` of `default` plays on the system
  default, as written. With no `output` set (no file, no key, or an empty
  value), the line is still refused, and the error now names all three
  fixes and the settings file's path. The schema's default is never applied.
  A mailed line is unchanged.
- A short reply right after the mouth's line is no longer named as the
  agent's (EARS-360). The mic hears the line back, and the speakers side-car
  labels that echo with the line's agent. A reply of one partial stayed in
  the agent's turn, because a speaker change needed two labels in a row.
  Now ONE partial labelled with a named person takes over a turn held by the
  mouth's agent. The side-car marks those labels `speaker_by: "mouth"`. A
  partial whose words are the line's own echo is not counted
  (`mouth.hold.is_echo`, against the line's `saying` text): the voice match
  can score the line's last word as the person. Every other change still
  needs two in a row. After a cut, the new turn is held by the speaker it
  opened with, so the agent's next line needs two labels to take it back.
  The turn is also checked once more as it closes, so a reply that came
  just before the end of the speech is cut off too, not left in the agent's
  turn with no speaker named.
- A streaming partials child that stops reading can no longer stop the
  capture (EARS-359). The capture writes to the nemotron child without
  blocking, and keeps every frame whole, so the child's audio never goes
  out of step. If the child falls 1 s behind, new frames are dropped whole
  and counted. If it takes nothing for 10 s of audio, the stream fails
  (`partials-error`), and the capture goes back to chunk partials. Before,
  a full pipe to the child stopped the heartbeats and the capture ignored
  STOP and SIGTERM. The child is now killed when its stream is closed, so
  a failed child is not left running beside the next one. `ears listen`
  and `ears file` are unchanged: they still wait for the child.
  `tests/test_listen_stream_feed.py` and the `pipe-full` and
  `pipe-full-shifted` cases in `tests/test_listen_wedge_scenarios.py` cover
  it. `docs/logs.md`'s line references to the capture's code are
  re-measured.
- A finished capture leaves at once, and a mic that will not close no
  longer holds it (EARS-359). Run as `python -m ears.listen`, as the keeper
  runs it, the capture flushes its output and exits without waiting for
  its worker threads, so a speech to text call still stuck at the end
  cannot keep the process alive after its result. The mic's stream is
  aborted instead of stopped, which needs no drain, and the capture waits
  at most 2 s for it, so `listen-stopped` is still written when PortAudio
  never comes back. The watchdog flushes stdout before it exits.
  `tests/test_listen_clean_exit.py` runs both cases.
- A wedged capture ends itself (EARS-359). A watchdog thread watches the
  frame loop and the stop request from outside the loop. If no audio frame
  is processed for 90 s, a STOP or SIGTERM is not honoured within 10 s, or
  the process is still alive 10 s after the capture finished, it writes
  every thread's stack to stderr. It also writes a `capture-wedged` heard
  row, which it skips when another writer holds `heard.lock`. Then it
  SIGKILLs the streaming child and exits 3. Before, any wait the frame
  loop never came back from (an STT call, `heard.lock`, the mic's close, a
  full pipe to the nemotron child, a thread joined at exit) needed a
  SIGKILL from outside. The keeper's beat-stale kill is unchanged, as the
  second line. Only a capture run from the command line has the watchdog.
  `tests/test_listen_wedge_scenarios.py` runs each of EARS-344's wedges (A
  to G, `pipe-full-shifted` included) as a real capture process.
- A capture no longer waits forever on a speech to text call that never
  answers (EARS-359). The frame loop gives a chunk 10 s and a turn's
  re-decode 20 s. Before, one stuck call stopped the heartbeats and the
  capture ignored STOP and SIGTERM. A re-decode that times out leaves the turn
  with its joined text and `redecode: "timeout"`. A chunk that times out is
  an `stt-error` with `detail: "timeout"`, and it counts toward the failures
  that end the capture. After a timeout, later calls run on a fresh worker,
  so they do not queue behind the stuck one. `tests/test_listen_stt_waits.py`
  ports EARS-344's `redecode-hang`, `chunk-hang` and `stop-drain-hang`
  cases, and adds a turn under streaming partials.
- `uv sync --python 3.10 --all-extras` installs: uv.lock takes onnxruntime 1.23.2, the last with a cp310 wheel (EARS-328).
- `docs/models.md` describes the Nemotron 3 Diarization side-car as it is
  installed now (docs, EARS-340). It said the side-car's venv needs a git
  build of mlx-audio and that `ears services install` does not include it.
  The entry now says that `ears services install --only speakers` installs
  it (a bare install never does), builds its venv in the speakers home from
  the shipped `speakers/requirements.lock` (`mlx-audio==0.5.8`), and fetches
  the model then. It also says that a hand-made side-car already on the Mac
  makes install skip ours.
- `tests/test_keeper_room.py` compares `foreign_captures` by pid, not by
  substring, so a checkout path that holds a capture's pid digits no longer
  fails it (test only, EARS-354).
- Barge-in counts a partial the ears wrote in the same millisecond as the
  line's first frame (EARS-334). The ears stamp to the millisecond and the
  first frame is a full float, so such a partial read as said before the line
  and was dropped; both ends are now compared in whole milliseconds.
- `mouth amend` replaces a line that nobody has heard yet, even when the
  player has already taken it (EARS-349). A barge-in replay held for the end
  of the user's turn was refused as "already playing" in the gap between the
  player
  taking the line and its first frame (synthesis, the lead hold, the device
  opening). The player now writes `first_frame_t` on `playing.json` at the
  first frame. Before that mark, an amend cuts the taken line unspoken
  (`said reason=amended`, no drop notice), and the new text plays next in its
  place. A line being heard still refuses, now with the true words: `<utt> is
  being heard now; retract it and say again`.
  This cut follows EARS-335's trust rules.
  Without that gate, an UNSIGNED mail from another sender could cut someone
  else's taken line before its first frame, silently, because an `amended`
  cut sends no notice. The taken line is now cut only for a trusted
  `Supersedes` (a local drop, which is what `mouth amend` writes, or signed
  v2) or one From the line's own sender. Any other supersede plays after it,
  as news. A supersede that EARS-335's rules refuse whole (for example, a
  file outside the
  sounds directory) never cuts the line, because it never plays in the line's
  place. Neither does a drop notice. Tested in
  `tests/mouth/test_mouth_amend_held.py`
  (`test_another_senders_unsigned_supersede_cannot_cut_a_taken_line`, the
  trust matrix `test_superseded_cuts_only_on_trust_or_the_same_from`,
  `test_a_supersede_refused_whole_never_cuts_a_taken_line` and
  `test_a_notice_reply_never_cuts_a_taken_line`).
- Stopping the room capture no longer gives up on a wedged one
  (EARS-226). The keeper touched its STOP file, waited 15 s and gave up,
  every loop. Now it escalates: the STOP file, then SIGTERM after
  `EARS_STOP_WAIT` (15 s), then SIGKILL after `EARS_TERM_WAIT` (5 s), and
  it notes each step. It still touches only its own room capture.
- The turn mail no longer wakes a seat on its own voice (EARS-126). When
  the capture labels a turn with a seat's name (its `speaker` is `carol`,
  say: the seat's line coming back through the speakers), `ears-mail` no
  longer gives that seat a `reply` or `light` verdict for it, on the device
  path's `Ears-Rule` or the seats path. The line it would have answered
  stays answerable, so the user's real reply still wakes the seat. A turn whose
  words call the seat (`call`, `everyone`, `first`) still wakes it, so a
  wrong label cannot lose a call.
- The plugin's description no longer says there is no STT server to run
  (EARS-289). It now says what README.md's Requirements say: you run a speech
  server yourself, by default Parakeet on `127.0.0.1:8890` (VoiceMode's
  mlx-audio), or point the ears at any OpenAI-compatible STT. Apple speech
  (macOS 27) is opt-in through `ears install --apple`.
- `ears-arm` stays where it is, `skills/ears/scripts/ears-arm` in the plugin
  (EARS-289). It is the ears skill's tool: the agent runs it from the skill,
  and a person runs it by that path inside the installed plugin. It execs
  `heard-first`, which comes from the plugin's `bin/`; Claude Code puts that
  directory on its Bash tool's PATH while the plugin is enabled. It is not
  moved to `bin/` and not added to the wheel's commands.
- `ears listen` and `ears file` with no usable backend now print one line on
  stderr, `ears: no ears backend usable here: apple: …; nemotron: …;
  whisper: …`, and exit 1, instead of a traceback (EARS-289). `pick()` raises
  `NoBackend`, a `RuntimeError`, so callers that already catch `RuntimeError`
  still catch it. Its `reasons` attribute holds each backend's reason.
- The Apple backend's reason for a missing helper now names the real command,
  `ears install --apple`. It used to say `ears install`, which no longer builds
  the helper (EARS-289).
- `commands/ears-setup.md` and `docs/troubleshooting.md` quote the lid DEAF
  mail as the keeper sends it, `ears: DEAF, lid shut, built-in mic off,
  holding at HH:MM EOM`, with its trailing `EOM` (EARS-355).

### Security

- The mouth judges a signed mail by the verifier's own verdict (EARS-345).
  It runs `message verify --json --trust` and takes the version, the
  signed names (`h`) and each signed value from the one JSON object it
  prints. It no longer reads the mail's `X-Session-Signature` itself. A
  signed ask (a call box, `now`, a negative priority, a barge, a
  `Supersedes`) is kept only when the value it acts on equals the value
  signed; any other is refused, as if unsigned. A missing, malformed or
  non-ASCII verdict counts as unsigned. **Per host, sessionmail first:**
  this needs `message verify --json` (sessionmail SM-788 or later) on the
  host the mouth runs on. On a host whose `message` lacks `--json`, every
  signed ask is refused (call lines, a signed `now`, a signed supersede)
  until that host is pulled to an SM-788 or later verifier. Unsigned and
  local lines are not affected.
- Only the mouth's own player may mail through the default
  `/usr/sbin/sendmail` (EARS-346). `mouth serve`, `mouth mail` and
  `mouth retract` (which writes a queued line's said row itself) mark their
  process as the service. Any other process that drops a line, a script or a
  review rig that imports the mouth, now records its notice as
  `failed: sendmail fenced: ...` instead of mailing it. One such rig had mailed
  a test notice to a live seat. An explicit `MOUTH_SENDMAIL` still
  works anywhere. The live service is unchanged, and nothing needs
  reinstalling. A rig that starts a player or runs `mouth retract` must still
  set its own sendmail stub; README "Tests" says how.

- `talk serve` answers only a paired browser (EARS-338). Another user on the
  same machine could connect to 127.0.0.1 with any Host and read `/events`, the
  live transcript. Every route but the page and the manifest now needs a
  `talk_token` cookie holding the install's token,
  `$VOICEMODE_BASE_DIR/talk/token` (0600 in a 0700 directory), which the first
  `talk serve` makes. A request without it gets a plain-text 403 that names
  `talk pair`. A browser pairs once: the page sends the token in an
  `X-Talk-Token` header to `POST pair`, and the answer sets an HttpOnly,
  SameSite=Strict cookie (Secure over https); pairing at `localhost` is refused,
  naming `127.0.0.1`. The page pairs from the `#pair=<token>` link that `talk
  pair` prints, dropping it from the address bar first, or from a link or token
  pasted into its pairing panel; when it is refused it shows the panel and stops
  retrying. The token is never read from a query string and is blanked in talk's
  log, and an open stream ends within one keep-alive of a rotation. A token file
  that is not 0600 in a 0700 directory owned by you, or that holds no token,
  exits 2 before anything binds. **Breaking:** every browser that uses talk must
  pair once: run `talk pair` in a terminal on the host and open the link it
  prints (it refuses a `localhost`, `*.localhost` or IPv6 URL, and prints no
  token when stdout is not a terminal). `talk token --rotate` writes a new
  token, and every browser pairs again. The token stops a user who connects; it
  does not stop one who listens on talk's port while talk is down, or on another
  127.0.0.1 port the browser opens (`talk/README.md`).
- `talk serve --unix [PATH]`, or `TALK_UNIX_SOCKET=PATH`, serves over a unix
  socket and binds no TCP port (EARS-338), for a proxy such as
  `tailscale serve --set-path /talk unix:PATH`. With no port, another user on
  the machine cannot take talk's port while it is down and collect a browser's
  cookie. PATH defaults to `$VOICEMODE_BASE_DIR/talk/talk.sock`. The socket is
  0600 in a 0700 directory; only talk's own uid and `--proxy-uid` (default 0)
  may connect. The proxy's `Host: localhost` is answered and may pair there.
  On such a host `talk pair` refuses a 127.0.0.1 URL: give the proxy's address.
  `GET pair`'s 204 no longer carries `Content-Length` (RFC 9110 8.6), and
  pairing at a `*.localhost` name is refused, as at `localhost`.
- `talk serve` answers only a Host it knows (EARS-336). It used to serve the
  page and the live transcript at `/events` to any Host, so a web page that
  rebinds its own DNS name to 127.0.0.1 could read what the microphone heard.
  Every route now refuses, with a plain-text 403, a Host other than
  `127.0.0.1:<port>`, `localhost:<port>` (the bound port), the `--host`
  address, or a name added with the new `--allow-host NAME` (repeatable) or
  `TALK_ALLOW_HOSTS` (comma- or space-separated). A request whose `Origin` is
  none of those is refused too. `X-Forwarded-*` headers are ignored. A bad
  entry exits 2 before anything binds. This stops DNS rebinding; another user
  on the same machine can still connect to 127.0.0.1 and read `/events`.
- **Behind `tailscale serve`, add the tailnet name** or the phone gets the 403:
  `talk serve --allow-host machine.example.ts.net`, or `TALK_ALLOW_HOSTS` in
  the launch agent's environment. tailscale serve passes the browser's Host
  through, so it is not 127.0.0.1. A running `talk serve` keeps the old
  behaviour until it is restarted.
- The speakers and speech service venvs are hash-pinned (EARS-337).
  `speakers/requirements.lock` and `speech/requirements.lock` now carry a
  `--hash` for every pin, the one URL wheel included, and
  `ears services install` installs them with `uv pip install
  --require-hashes`. An artifact whose bytes differ from the lock is refused.
  A fresh venv is checked in full. A re-apply over an existing venv checks
  only what it installs, so to check an existing venv, remove it and install
  again. The build backend uv fetches to build the two sdist-only speech pins
  (`docopt`, `webrtcvad`) is hash-pinned too: see the EARS-339 entry below.
- The build backend for the speech venv's two source-only pins is
  hash-verified (EARS-339). `docopt==0.6.2` and `webrtcvad==2.0.10` ship no
  wheel, so uv builds them, and `--require-hashes` does not reach the
  setuptools it fetches to do it. `ears services install` now passes
  `-b speech/build-constraints.txt`, which pins `setuptools==80.10.2` (the
  lock's own runtime pin) with both of its PyPI hashes. A setuptools whose
  bytes differ is refused. A test fails when a re-compiled lock gains a
  source-only pin whose build closure that file does not pin. The speakers
  lock has no source-only pin. When `EARS_SPEECH_PY` names an existing
  python, install builds nothing, so this does not apply.
- `ears services status` reports drift in the speech server's weights
  (EARS-339). mlx-audio's server fetches Parakeet, Kokoro and Kokoro's
  voices (`prince-canuma/Kokoro-82M`) from Hugging Face `main` at first use,
  with no revision, and has no setting for one, so they are **not pinned**.
  Three new `weight` rows say, for each repo, whether the Hugging Face
  cache's `main` is the commit sessionvoice expects
  (`services.SPEECH_WEIGHTS`, the one place those commits are named):
  `match`, `DIFFERS` (naming the cached commit), or `not cached`. It reads
  the cache's files only (`HF_HUB_CACHE`, else `HF_HOME`, else
  `~/.cache/huggingface`), never imports Hugging Face code or fetches, and
  `status` still exits 0. This reports drift; it pins nothing. A pin fixed
  at install is EARS-357.
- The speaker diarizer, `mlx-community/Nemotron-3-Diarization`, is fetched
  and loaded at one pinned commit, `services.SPEAKER_REVISION`, not whatever
  `main` is on the day (EARS-337). The install fetch, the cache check and the
  three speakers scripts all pass it. The commit is the snapshot every host
  already holds, so no host gets a different model.
- The mouth no longer lets a mail it admits make it fetch a file or URL,
  speak into a live call, or cut in (EARS-335, SK-80). The From check
  still says who may speak; what a line may make the mouth DO now depends
  on where it came from.
  - **Local** is what `mouth say`, `play` and `amend` drop into the box.
    Each drop's first line is an HMAC of the whole message, keyed by
    `<box>/.mouth-key` (32 bytes, mode 0600, made by the first drop). A
    local line keeps every ask, as before. `X-Mouth-Raw: 1` on its own no
    longer makes a line local: a mail that carries it without the MAC is
    read as mail from outside, and logged `forged_raw`.
  - **Signed** is a mail that `message verify --trust` passes (FULL or
    UNBLESSED). It keeps only the asks its signature covers: under v1, a
    call box that is the `To` its signature signs; under v2, each
    `X-Mouth-*` header it signed, and `Supersedes`. With no `message` on
    `PATH`, or with `$MOUTH_VERIFY_BIN` set at all, no mail is
    signed. What a signature covers is the verifier's own verdict, never
    the mouth's reading of `X-Session-Signature` (EARS-345, under
    Security).
  - **Any other mail** plays a sound only from the sounds directory
    (`$MOUTH_SOUNDS_DIR`, else `<mouth dir>/sounds`), and never
    from outside it, signed or not. It may not route into a call
    (`X-Mouth-Device: call:N`, an extra's `device`, or mail to a call's
    box): the line is filed `untrusted`, unplayed, and its sender gets a
    notice. Its `now` (or a negative priority) is dropped and the line
    plays in its turn. A signed call line whose barge its signature does
    not cover waits for the user's turn.
  - **Supersedes:** only a local or signed v2 retract cuts the line
    playing. Without one, a mail retracts or replaces only a queued line
    from its own From, and a replacement never inherits a place ahead of
    `next`. A retract or replace that breaks either rule is filed
    `untrusted`. A mailed retract by ANOTHER sender now needs a signed v2
    mail.
  - ffmpeg now runs with `-protocol_whitelist file` for every sound, so
    `mouth play` no longer plays a URL: it refuses one before anything is
    queued. Download the file, then play that.
  - On a mouth host where `message verify --trust` cannot run (no registry),
    every call-box mail is now refused. `mouth say --device call:N` still
    works there.

## [0.1.0] - 2026-10-10

### Added

- First public alpha, released with the Session Kit family.
