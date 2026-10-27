# Speaker names for the heard log

Tags every heard line with who spoke it: a person's name (`alice`, `bob`,
...), an agent's (`carol`), or `other`, `mixed` or `silent`. It is a second reader of the
ears capture's mic, and it listens only while that capture runs.

## Install, enrol, status, uninstall (the way to run it)

The side-car is an opt-in `ears services` service. Everything it runs
ships in the sessionvoice wheel, so a kit install needs no checkout and no git.
It needs an Apple-silicon Mac (arm64) on macOS 14 or later, and `uv`.

    ears services install --only speakers --dry-run   # the checks and the plist; changes nothing
    ears services install --only speakers             # venv, model, plist, load
    ears speakers enrol                               # record, name each voice, make the references
    ears services status                              # the speakers row
    ears services uninstall --only speakers           # the job and its plist; references kept

- **Install.** `install --only speakers` checks the host and refuses, in one
  line and with no plist written, if the side-car cannot run there. It builds
  the side-car its own venv at `~/.local/share/ears/speakers/venv`, with python
  3.12 and the pins in `requirements.lock` (`mlx-audio==0.5.8` from PyPI: 0.5.6
  and later ship `nemotron_diarization`, 0.5.5 does not). It checks that the
  venv imports the diarizer, `numpy` and `sounddevice`, and names the first one
  missing. It downloads the model, `mlx-community/Nemotron-3-Diarization`
  (about 190 MB, once). That is the only download: the job runs with
  `HF_HUB_OFFLINE=1`, and enrol never fetches. Then it writes and loads
  `~/Library/LaunchAgents/dev.sessionkit.voice.ears-speakers.plist`. A bare
  `ears services install` does not install it.
- **Enrol.** `ears speakers enrol` records a few minutes of the room while the
  ears keeper's capture runs (`--seconds`, 180 by default; Enter stops it
  sooner). It groups the lines by voice and shows each group's words, then asks
  for a name per group. A blank answer skips that group. Nobody's name is
  guessed: it comes from a person who read what the group said. It makes one
  reference per name, refuses a group that is not one voice, checks that the
  side-car starts on the new set, and only then commits it. A loaded job is
  then restarted on the new references. For an installer with no terminal (an agent), the same steps are
  `ears speakers record`, `ears speakers clusters DIR` and
  `ears speakers name DIR L0=alice L1=bob`, with the names the user gives.
- **Status.** The `speakers` row reads `not installed`,
  `installed, not enrolled` (no reference yet, whether or not the job is
  loaded), `running, pid N` or `loaded, not running`. Log:
  `~/.sessionkit/voice/log/ears-speakers.log`.
- **Uninstall.** `uninstall --only speakers`, or a bare `ears services
  uninstall`, boots the job out and removes its plist. The references and the
  venv stay.
- **Never two side-cars.** A hand-made side-car job on the Mac makes install
  skip speakers and say why: a render of the hand template (it carries
  install's own label, `dev.sessionkit.voice.ears-speakers`, but not the
  `SESSIONKIT_INSTALLED_BY` entry every plist install writes carries in its
  `EnvironmentVariables`), or a job under an older hand label such as
  `com.failmode.speaker-sidecar`, loaded or only a plist in
  `~/Library/LaunchAgents`. If ours was installed first, ours is removed. The hand-made one is never
  touched, and `status` prints its own line for it.

The speakers home, `~/.local/share/ears/speakers` (or `$EARS_SPEAKERS_HOME`),
holds `refs/` (0600 clips in a 0700 dir, never in the package, so an upgrade
keeps them), `refs-call/`, `STOP` and `venv/`. `touch` its `STOP` to stop the
job and keep it down until the next load or login. Overrides, read at install:

- `EARS_SPEAKER_PY`: a python that already has mlx-audio 0.5.6 or later. No
  venv is built, but the module check still runs.
- `EARS_SPEAKER_AGENT_REFS`: names of other machines' agents (see "Agent voices").

The sections below are how it works, and the hand path from a checkout.

## What an agent reads

`<home>/logs/conversations/speakers_YYYY-MM-DD.jsonl`, beside the heard
log under sessionvoice's home ([docs/logs.md](../docs/logs.md#sessionvoices-home)),
0600, one line per heard line, joined to it by `seq`:

    {"seq": 74343, "kind": "partial", "speaker": "alice",
     "fracs": {"alice": 1.0}, "speech_s": 1.6, "lag_s": 0.48, ...}

`speaker` is the answer. `fracs` is each person's share of the line's speech
(a `mixed` line's biggest share says whose voice leads). `lag_s` is how long
after the line the name landed, usually under a second. The heard readers
glob `heard_????-??-??.jsonl`, so they never see this file.

## Needs

- The ears capture running on this machine (it writes the heard log).
- `$SPEAKER_DEVICE`: `follow` (the default) opens whatever device the ears
  keeper's capture is on, and moves with it when the AirPods come out or go
  back in, with no restart and no model reload. A device name pins it to that
  device. (A side-car pinned to the AirPods was seen to go deaf when they
  came out.)
- Its own venv, separate from the ears venv (the diarizer needs mlx-audio,
  which the ears venv does not have). `ears services install --only speakers`
  builds it (above). PyPI's mlx-audio 0.5.5 has no `nemotron_diarization`;
  0.5.6 and later do, and their diarizer code is byte-identical to the git
  build `03a4d99` this side-car was first measured on.

By hand, from a checkout (development only; the service is the way to run it).
The same pins the service uses:

    cd speakers
    uv venv --python 3.12 .venv
    uv pip install --python .venv/bin/python --require-hashes -r requirements.lock
    export SPEAKER_PY=$PWD/.venv/bin/python   # run.sh finds ./.venv by itself

The lock carries a hash for every pin, and `--require-hashes` refuses any
artifact that does not match.

The model, `mlx-community/Nemotron-3-Diarization` (about 190 MB), is fetched
by the service install, at one pinned commit (`SPEAKER_REVISION` in
`src/ears/services.py`). Each script here loads that same commit, its
`REVISION`, and a test keeps them equal. By hand, it downloads on first use
without `HF_HUB_OFFLINE`. It runs on the GPU; one line takes about 30 ms.

## References: one clip per person, recorded on THIS mic

References are per mic and room. The ones made on one Mac do not travel.
`ears speakers enrol` does the steps below for the service. They are written
out here for the hand path, where `refs/` sits beside `run.sh`.

1. Record a few minutes of normal talk, everyone taking turns:
   `$SPEAKER_PY speaker-sidecar2.py --device "$SPEAKER_DEVICE" --record-only room --stop-file STOP`,
   then `touch STOP`. Each heard line lands as `room/SEQ.wav` + `.json`.
2. Group the lines by voice: `$SPEAKER_PY cluster-room.py room`. It prints
   each cluster (`L0`, `L1`, ...) with its lines' words.
3. Read the words, decide who each cluster is, and name them:
   `$SPEAKER_PY make-refs.py room L0=alice L1=bob L2=carol`.
   It writes `refs/NAME.wav` (12 s of that person talking alone) and
   refuses a cluster that does not come back as one voice.

Nobody's voice comes from anywhere else: the names come from what was said.
The clips are someone's voice: 0600, and never in git (`.gitignore`).

## Calls: names for the call leg

A call's audio is not the room's: the caller's phone mic, through Opus. On one
call (2030-06-09) the room references named only 192 of the caller's 317 call
partials correctly. A 12 s clip of the caller cut from a call named 289 of the
other 309.

- `refs-call/NAME.wav`: 12 s of NAME alone, cut from a bot's
  `calls/<stamp>-chat<C>-msg<N>/caller-call.wav` (16 kHz mono). It is the caller's voice:
  0600, never in git (`*.wav`). `run.sh` passes each as `--call-enroll`.
- A heard line from `source: "call"` (`device: call:N`) is cut from that
  call's `caller-call.wav` at `wav_s + t0 .. wav_s + t1`. No
  `caller-call.wav`: the row is `unheard`, and its why names it. A call dir
  from before sessionphone's stem rename is not read. `wav_s` is where the
  capture's ear tap began in the wav, from the bot's `ear-taps.jsonl`
  (sessionphone `ready/feat/ear-tap-wav-offset`). The wall clock will not do:
  it drifted 2.0 s from the capture's audio in 21 minutes on that call.
- Which name to read comes from `src/ears/callrec.py`, loaded by its path, so a
  side-car with `--call-enroll` runs from a sessionvoice checkout. A copy run
  elsewhere refuses at start, naming that file. Room only, it never needs it.
- Its row says `"leg": "call"`; `refs` lists the call references.
- No `ear-taps.jsonl` (a bot that predates it): the line's row is `unheard`
  with a why, never a guess.
- What reads it: the mouth's `barge_in` setting (`["alice"]`: only alice's
  words cut a line). Replayed over that call, the road-noise phantoms that
  cut an agent's lines came back `silent`, `silent`, `other`.

## Run

Always on: the service, `dev.sessionkit.voice.ears-speakers` (above). It runs the
packaged `run.sh` in the foreground with `SPEAKER_HOME` set to the speakers
home, and launchd starts it again after a crash or a reboot.

From a checkout, the hand-made job is
`launchd/dev.sessionkit.voice.ears-speakers.plist.in`. Its comment says how to fill it
and load it. Install treats a render as hand-made (by its content, not its
label: it is install's own label) and never runs beside it or overwrites it.
Rendering it over the plist install wrote replaces install's job. Log:
`~/.sessionkit/voice/log/ears-speakers.log` (`EARS_LOG_DIR` moves it). A job
loaded before the move keeps its old log path until it is re-rendered and
reloaded, and its old log stays in `~/Library/Logs/`.

By hand:

    ./run.sh --detach      # its own session; outlives the shell that ran it
    touch STOP             # stop it (within a line); never pkill -f. Under
                           # launchd it stays down until the next load or login

It checks at start that each reference is one speaker, and that they are
different speakers. Log: `speakers.log`.

Its name is `ears-speakers` in `ps -c`, `ps -o comm=` and Activity Monitor,
not `Python`. Those read the kernel's process name, the name of
the file exec'd, and `setproctitle` cannot change it on macOS (measured on
a Mac: `ps -c` shows the new name, Activity Monitor still shows `Python`). So
`run.sh` copies the binary the venv's python really runs as (for a Homebrew
or python.org python, `Python.app/Contents/MacOS/Python`) to
`$SPEAKER_PY`'s `bin/ears-speakers`. The copy sits beside the venv's
`pyvenv.cfg`, so the venv's packages still load. Each start refreshes it if
it differs, e.g. after a python upgrade. macOS grants the microphone per
binary, so under launchd the grant is for `bin/ears-speakers`. A `SPEAKER_PY`
that is not a venv runs under its own name, and `run.sh` logs a line saying
so.

## It never goes deaf without saying so

Every heard line from a real mic gets a row. When the side-car cannot name
the speaker, the row says `"speaker": "unheard"` and a `why`:

- `no audio from 'airpods' (20% of the line covered)`: the device's audio
  covers less than `--cover` (half) of the line, e.g. the line began before
  the stream opened.
- `no signal: the device gave only zeros`: a dying device, or a mic with no
  macOS grant (under launchd, check this first).
- `words in the line, no speech found in its audio`: the line has words and the
  model found no speech. Such a line is never called `silent`.
- `no stream start for its capture yet`: the line came before any
  `listen-started` or heartbeat for its capture.

`silent` is kept for a line with no words and no speech. A line on a BlackHole
input is a call tap, not this mic, and gets no row.

Every row whose line had a window also carries `cover`: the share of the line
its device's audio covered, from `0.0` to `1.0`, rounded to 2 places. The ears
keeper reads this number, and reads the `why` text only for older rows that
have no `cover`. A row with no window, such as `no stream start for its capture
yet`, has no `cover`.

A stream can open, report itself active, and never deliver a block of audio,
for example from a stale entry in PortAudio's device list.
Every line on that stream comes out `(0% of the line covered)`. Neither the
device name nor the stream's state changes, so nothing else would make the
side-car reopen it. After `--deaf-lines` mic lines in a row at 0% (default
10), the side-car logs `mic: N lines at 0% coverage: reopening <device>`. It
then closes the stream, rescans the device list and opens the device again.
It does this at most once a minute, and keeps retrying once a minute for as
long as the lines stay at 0%. A line with any coverage, or a line from another
device, starts the count again. Call-leg rows are not counted. `--deaf-lines 0`
turns the reopen off. If reopening does not bring the audio back, the keeper
counts the same rows and reports the speakers as deaf.

A stream can also be covered and still hear nobody: blocks arrive, the line
is covered, and the diarizer finds no speech in audio where the capture heard
words. The row's `why` then reads `words in the line, no speech found in its
audio`. Three such mic turns in a row (`SILENT_TURNS`) make the side-car log
`mic: N covered turns with no speech: reopening <device>` and reopen the
stream, under the same once-a-minute limit. Only a turn counts, because
partials repeat its words. It needs 2 or more words, because the diarizer can
miss a one-word reply. It must not be the mouth's own words (`mouth_utt`).
A line with no words is no evidence, so a quiet room never counts. A line
where speech was found starts the count again. `--deaf-lines 0` turns this
reopen off too. The keeper reports what the reopen does not cure (below).

Each block of audio is stamped with the moment it was taken, by the stream's
own clock (PortAudio's `inputBufferAdcTime`), not the moment it arrived. A
Bluetooth input can stall and then deliver what it held in a burst; with stream
stamps, the burst's audio lands where it belongs. A host that gives no stream
clock falls back to the arrival time, and then the burst piles onto one moment.
Either way `cover` is the share of the line's span that has audio, and a moment
claimed by two blocks counts once, so a burst cannot make a line look covered.

A stall is a block that arrived more than 0.25 s later than due, or more than
0.25 s later after its own audio ended than the stream's usual delay (the least
it has shown; a steady Bluetooth latency is not a stall), or a hole of more
than 0.25 s in the stream clock. The side-car logs stalls at most once every
10 s, in one line per device:

    mic: stalls on 'airpods': blocks=37 wait_max_s=1.00 late_max_s=0.98 lost_s=0.00 stamps=stream

- `blocks`: how many blocks stalled since the last line.
- `wait_max_s`: the longest time between two blocks.
- `late_max_s`: the latest a block arrived after its audio ended, beyond the
  stream's usual delay.
- `lost_s`: the seconds of audio the stream clock says never arrived.
- `stamps`: `stream`, or `arrival` when the stream had no clock. With
  `arrival`, the late and lost figures cannot be measured and read 0.

A steady stream logs no stall line. If AirPods rows still say `no speech found`
and no stall lines appear, the stream is not stalling.

## Replacing follow-capture

Before this service, a Mac may have run an interim instead. That interim is
`follow-capture`, a bash loop in `~/.<agent>/speaker-sidecar` that runs under the
launchd job `com.failmode.speaker-sidecar`. It restarts a pinned side-car
whenever the keeper's capture changes device. `run.sh`, the
`dev.sessionkit.voice.ears-speakers` plist and `--device follow` cover everything it
does. Each row below names the test that shows it. `follow:` means
`tests/test_speakers_follow.py` and `parity:` means
`tests/test_speakers_parity.py`. None of them needs the model or a mic.

| follow-capture does | here, by | test |
| --- | --- | --- |
| Reads the keeper capture's `--device` from `ps` every 2 s | `--device follow`, the default in `run.sh` and the plist: `keeper_device()` reads the same `ps` line and the mic thread looks every 1 s | follow: `test_keeper_device`, `test_device_defaults_to_follow`; parity: `test_run_sh_gives_the_side_car_what_follow_capture_gave_it` |
| Ignores a BlackHole capture (a call tap) | `virtual()`: a BlackHole capture is skipped and the keeper's real one found after it (follow-capture read only the first keeper line); a BlackHole line gets no row | follow: `test_keeper_device`, `test_call_taps_and_pinned_other_devices_get_no_row` |
| On a device move, stops the side-car (stop file, kill after 10 s) and starts a new one on the new device, which reloads the model | The stream moves inside one process within about 1 s, with no reload. Ring blocks carry their device, so a line is cut only from its own device's audio. AirPods put in after start are found by a rescan | follow: `test_mic_step_follows_the_capture_and_survives_a_dying_device`, `test_ring_cuts_only_the_lines_own_device`, `test_mic_step_rescans_for_airpods_put_in_after_it_started` |
| Restarts a dead side-car after 5 s, on the same device | Process death: launchd `KeepAlive {SuccessfulExit: false}` (at once, or 30 s after the last start if it died sooner: `ThrottleInterval`), and `run.sh` execs the side-car, so the job's exit (a code or a signal) is the side-car's own. A dead stream in a live process is reopened, and an error in one look is retried | follow: `test_plist_renders_and_stays_down_on_stop`, `test_mic_step_follows_...` (a stream that dies under a steady target), `test_mic_thread_survives_an_error_in_a_look`; parity: `test_run_sh_passes_the_side_cars_exit_status_to_launchd`, `test_run_sh_a_killed_side_car_is_not_a_clean_exit`, `test_side_car_killed_is_not_a_clean_exit` |
| Is back after a reboot or a login (its own job: `RunAtLoad`, `KeepAlive {SuccessfulExit: false}`) | The same keys in `dev.sessionkit.voice.ears-speakers` | follow: `test_plist_renders_and_stays_down_on_stop`; parity: `test_plist_runs_from_the_repo_with_its_own_log` |
| `touch STOP-FOLLOW` stops the side-car, exits 0, and stays down | `touch speakers/STOP`: the side-car exits 0, within 0.2 s when the heard log is quiet, and `SuccessfulExit: false` leaves it down | follow: `test_plist_renders_and_stays_down_on_stop`; parity: `test_side_car_exits_0_on_its_stop_file_while_following` |
| Deletes `STOP-FOLLOW` at start, so the next load or login runs it | `run.sh` runs `rm -f STOP` before it execs the side-car | parity: `test_run_sh_clears_a_stale_stop_so_the_next_load_comes_back` |
| Side-car args: `--enroll alice= bob= carol=`, `--agent-refs carol`, `--enroll-s 12`, `--ring 300` | `run.sh` passes every `refs/*.wav` as `--enroll`, in name order, plus `$SPEAKER_AGENT_REFS`, `--enroll-s 12` and `--ring 300`. `refs-call/*.wav` become `--call-enroll`; follow-capture had none. `$SPEAKER_DEVICE` pins one device | parity: `test_run_sh_gives_the_side_car_what_follow_capture_gave_it`, `test_run_sh_pins_a_named_device_as_one_argument` |
| `HF_HUB_OFFLINE=1`: a start never downloads | The plist sets it | follow: `test_plist_renders_and_stays_down_on_stop` |
| No python or no clips: the side-car dies and restarts every 5 s, forever | `run.sh` exits 1 (never 0) and launchd retries 30 s apart | parity: `test_run_sh_refuses_without_refs_and_launchd_retries` |
| Logs to `~/Library/Logs/failmode/speaker-sidecar.log` and runs from an agent's home | Logs to `ears-speakers.log` and runs the repo's own `speakers/` with `@SPEAKER_PY@` | parity: `test_plist_runs_from_the_repo_with_its_own_log` |
| `PYTHONUNBUFFERED=1` | Not needed: every log line is written with `flush=True`, and tracebacks go to stderr, which is unbuffered | parity: `test_side_car_exits_0_on_its_stop_file_while_following` (it reads the side-car's log while the side-car runs) |

Not kept, on purpose:

- **No keeper capture: follow-capture started the side-car on "MacBook Pro
  Microphone" anyway.** Here no mic is opened until the keeper's capture runs.
  With no capture there are no heard lines to label, and opening the mic would
  only hold it. (follow: `test_mic_step_follows_...`, the "capture stopped"
  step.)
- **Capture only on BlackHole: follow-capture stayed on the last real device.**
  Here the stream closes, for the same reason: a BlackHole capture produces no
  room-mic lines. When the keeper is back on a real mic, the stream reopens.
  (follow: `test_keeper_device`.)
- **TERM, INT or HUP: follow-capture trapped them, stopped its side-car and
  exited 0, so it stayed down.** Here a signal ends the side-car with that
  signal, and launchd starts it again. "All the time" means a stray `kill`
  must not leave it deaf. `touch STOP` is the way to stop it, and
  `launchctl bootout` removes the job for good. (parity:
  `test_side_car_killed_is_not_a_clean_exit`.)
- **"Kill after 10 s if the side-car ignores its stop file".** There is no
  restart per move left to wait on. On `bootout`, launchd sends TERM, then KILL
  after its exit timeout. That is launchd's own behaviour and is not tested
  here.
- **Shared with follow-capture, not fixed:** if STOP is touched while launchd
  is waiting to restart a crashed side-car, `run.sh` deletes it at the next
  start, the same way follow-capture deleted `STOP-FOLLOW`. To keep it down
  whatever happens, `launchctl bootout` the job.

### The deploy (on each Mac that runs the interim)

Two side-cars must never run at once. Both would open the mic and append to
the same `speakers_YYYY-MM-DD.jsonl`, so every line would be labelled twice.
So `com.failmode.speaker-sidecar` is booted out and its plist removed in the
**same step** that loads `dev.sessionkit.voice.ears-speakers`. To see whether a Mac
runs the interim:
`launchctl print gui/$(id -u)/com.failmode.speaker-sidecar`.

Before the swap (this changes nothing that is running):

- Check out the repo at the landed commit. Call its path `$EARS`; launchd runs
  `$EARS/speakers/run.sh`.
- Copy in the clips follow-capture enrolls, under the names `run.sh` reads.
  `refs/` is not in git (voices never are), so a fresh checkout has none:
  make it first, or `install` fails. Each `enroll-room-<name>.wav` becomes
  `refs/<name>.wav`:

      install -d -m 700 "$EARS/speakers/refs"
      for f in ~/.<agent>/speaker-sidecar/enroll-room-*.wav; do
        n=${f##*enroll-room-}
        install -m 600 "$f" "$EARS/speakers/refs/$n"
      done

  `run.sh` enrolls every clip in `refs/`, so `refs/` must hold exactly the
  `--enroll` set follow-capture passes, no more and no fewer. Check
  `ls "$EARS/speakers/refs"` against its `--enroll` arguments
  (`grep -o -- '--enroll [^ ]*' ~/.<agent>/speaker-sidecar/follow-capture`), and
  remove any clip it does not enroll.

  `refs-call/` is optional. follow-capture passes no `--call-enroll`, so leave
  it out for a like-for-like swap. With `refs-call/alice.wav` (from
  `enroll-call-alice.wav`), call lines get names too.
- `SPEAKER_PY` is a venv python with mlx-audio 0.5.6 or later (or the git
  build) and the model already cached. The venv follow-capture uses,
  `~/.<agent>/diarize-spike/.venv/bin/python`, will do. `run.sh` puts
  `bin/ears-speakers` in that venv.
- Fill in the plist as its comment says, into
  `~/Library/LaunchAgents/dev.sessionkit.voice.ears-speakers.plist`, then run
  `plutil -lint` on it.

The swap, as one paste:

    U=gui/$(id -u)
    # TERM: follow-capture stops its side-car (stop file, kill after 10 s) and exits 0
    launchctl bootout $U/com.failmode.speaker-sidecar
    # out of LaunchAgents, so the next login cannot load it again; kept for a rollback
    mv ~/Library/LaunchAgents/com.failmode.speaker-sidecar.plist \
       ~/.<agent>/speaker-sidecar/com.failmode.speaker-sidecar.plist.retired
    for i in $(seq 1 30); do pgrep -f speaker-sidecar2.py >/dev/null || break; sleep 1; done
    if pgrep -fl speaker-sidecar2.py; then
      echo "a side-car still runs: not loading ears-speakers"
    else
      launchctl bootstrap $U ~/Library/LaunchAgents/dev.sessionkit.voice.ears-speakers.plist
    fi

Then check:

- `launchctl print $U/dev.sessionkit.voice.ears-speakers` shows a pid and
  `state = running`.
- `pgrep -lx ears-speakers` shows exactly one process.
- `~/.sessionkit/voice/log/ears-speakers.log` has a `mic: open` line, and new
  rows reach `speakers_$(date +%F).jsonl`.
- macOS may ask for a mic grant for `bin/ears-speakers`. If it is denied, rows
  say `unheard`, `no signal`.

Rollback: `launchctl bootout $U/dev.sessionkit.voice.ears-speakers`, move the
`.retired` plist back into `~/Library/LaunchAgents/`, and `launchctl bootstrap`
it. After the swap, retiring `follow-capture` and the rest of
`~/.<agent>/speaker-sidecar` is up to whoever runs it.

## Limits

- A voice with no reference shows as `other`.
- A new mic or room needs new references.
- It never writes the heard log; it only adds its own file beside it.

## Agent voices

- **Same machine, no reference.** The mouth writes `saying`/`said` rows (agent, utt) into
  the heard log the side-car follows. A heard line inside a playing mouth line, at least
  half of whose words are in that line, takes the line's agent when the model said `other`
  (or an agent ref). A person talking over it stays that person; a `mixed` line stays
  `mixed`, with its `other` share renamed. The row gets `mouth_utt`.
- **After the line ends** (up to `--mouth-tail` 1.5 s, judged by the heard line's
  midpoint), only the line's ENDING can be its echo: at least half the heard words must be
  an in-order run of the line's last words. "heard you." after "... I'll confirm I heard
  you." is the agent; "Anything." after "... say anything ..." is the person who answered.
- **A line whose `said` row was never seen** ends where the next mouth line starts, or,
  with no next line, 10 s or 0.6 s a word after it started, whichever is longer.
- **Other machines' agents** (heard through this machine's mic) need a reference like a
  person's. List them in `SPEAKER_AGENT_REFS=dave` so they, too, yield to this machine's mouth.
- **Labels are named by reference**, not by arrival order: five references may come back
  as labels 0,1,2,3,5 (a 12 s agent reference beside another agent's Kokoro voice did). Only two references on ONE
  label is refused.

## Memory

Each heard line has a new length, so every label call gives the model buffers of
new shapes. MLX keeps every freed Metal buffer in its cache, so the side-car
grew about 5 GB an hour toward 24 GB. `probs()` calls `mx.clear_cache()` after
every `model.generate`. To check that no label changed, relabel some recorded
lines with the side-car before the fix and with this one, and diff the results:

    PY=$SPEAKER_PY SEGS=room ./test-labels-unchanged.sh   # 0 same, 1 differ, 2 could not run

It reads files only: no mic, no voice. It uses `refs/` and `refs-call/` the way
`run.sh` does.

Tests: `make test` from the repo root (no model; a stand-in diarizer). Only these:
`uv run --no-project --with pytest --with numpy python -m pytest tests/test_speakers.py tests/test_speakers_follow.py tests/test_speakers_call.py tests/test_speakers_parity.py`.
