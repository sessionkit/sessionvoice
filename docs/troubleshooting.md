# Troubleshooting

What to check when sessionvoice misbehaves, one symptom to a section.
Today there are four. **The mouth's voice chops**, which has three causes (the
Mac is starved, a clone voice streams slower than real time, or the
Bluetooth link loses audio), each with its own check and remedy. **The
ears hear nothing**, which the keeper reports by mail, one cause at a time.
**Install says a service did not load**. And **you hear that mail is
failing**.

## Mouth: the voice chops

A line breaks up as it plays. It stutters, drops words or goes silent for a
moment. There are three causes. They often arrive together, and each one has
its own check and its own remedy.

Source: a measured incident on a Mac on 2030-06-09, about two minutes long,
with AirPods on the HFP (call) link. The diagnosis: first the Mac was starved
for a minute (load average 58), then the Bluetooth link fell to -82 dBm.

### First, read the line's own counts

Every `said` row in the heard log says whether its line broke up, and on
which side. That is the direct check for a chop, so start there:

    jq -c 'select(.kind == "said") | {ts, utt, voice, backend, device,
      underruns, underrun_s, device_gaps, device_gap_s, device_gap_max_s}' \
      "$(ears home state-dir)/logs/conversations/heard_$(date +%F).jsonl" | tail -20

The heard log is `<root>/logs/conversations/heard_YYYY-MM-DD.jsonl`
(`src/ears/heard.py`). The state root is `~/.sessionkit/voice/state`, or the old
`~/.sessionvoice` or `~/.voicemode` on a host that already keeps its heard log
there; `$SESSIONVOICE_HOME` moves it ([logs.md](logs.md#the-state-root)).
`ears home state-dir` prints it, and so does the `state` row of
`ears services status`.
On a fresh host that also runs VoiceMode's `converse` as a heard guest, set
`SESSIONVOICE_HOME=~/.voicemode` (or `VOICEMODE_BASE_DIR`), so that both
write one log; otherwise whichever writes first decides.

| the row says | the line chopped because | cause |
| --- | --- | --- |
| `device_gaps` > 0 | the audio callback ran late. The device played time nobody filled, because the host was starved | 1 |
| `underruns` > 0 | the callback ran on time, but synthesis had not made the audio yet | 2 |
| both 0, and the user still heard it chop | the break happened after the Mac. Suspect the Bluetooth link | 3 |

What the fields mean (`src/mouth/output.py`, `src/mouth/player.py`):

- `device_gaps` counts episodes, and late samples in a row are one episode.
  `device_gap_s` is the total time lost. `device_gap_max_s` is the longest
  episode, at about 50 ms resolution. They read 0 on `null` and `call:`
  outputs, which have no device callback.
- `underruns` and `underrun_s` count the silence that synthesis put in
  mid-line.
- On a shared device (a line over a music bed), a gap is charged to the lines
  live at mix time. That can be off by about 0.2 s at each end of a line.
- `device_gaps` was measured on a Mac's display speakers and has not yet been
  checked on a Bluetooth output. A Bluetooth chop with `device_gaps` 0 is
  therefore weaker evidence than one with `device_gaps` > 0.

### Cause 1: the Mac is starved

**Symptom.** Any voice can chop, kokoro as well as a clone, and music with
it, because the device itself is starved. On 2030-06-09, the load average hit 58.6. Two fresh
`claude` launches ran at over 120% CPU each, mlx-audio held 9.1 G mid-line,
and coreaudiod was pinned at 55-69%. On the mic side, the packets the Mac
consumed fell from 66.8 to 42 a second while the link stayed clean.

**Check.**

- `device_gaps` > 0 on the `said` rows (above).
- `uptime` shows load averages far above the core count.
- `ps -Ao pid,pcpu,rss,comm -r | head -12` shows what is eating the CPU,
  busiest first. Look for fresh `claude` processes, mlx-audio and
  coreaudiod.
- bluetoothd's HFP LinkQualityReport, one line a second:

      log show --last 10m --predicate 'process == "bluetoothd" AND eventMessage CONTAINS "HFP LinkQualityReport"'

  A starved Mac shows **AudioInput below the SCO rate** (the link delivered
  66.8/s and the Mac took 42/s), with RSSI and PLC still healthy. The link
  carried the audio and the Mac did not keep up.

**Remedy.** Take the load off: stop the music, launch nothing heavy (see the
remedies in order, below).

### Cause 2: a clone voice streams slower than real time

**Symptom.** A clone voice chops and a kokoro voice does not. On 2030-06-09
the clone chopped, and af_sky (kokoro) did not. The kept WAV
of the same line is clean. The chop happened in the delivery, not in the
audio that was made.

**Why.** The clone path asks mlx-audio to stream (`"stream": True,
"streaming_interval": 0.3`, `src/mouth/backends.py:260`). The mouth plays
audio as it arrives, so when a starved mlx-audio makes it slower than real
time, the device runs dry mid-line.

**Check.**

- `underruns` > 0 on the `said` rows, with `backend` `clone`.
- The kept WAV is clean. `ffmpeg silencedetect` finds only natural pauses:

      ffmpeg -nostdin -hide_banner -i $(ears home state-dir)/mouth/audio/YYYY-MM-DD/UTT.wav \
        -af silencedetect=noise=-40dB:d=0.3 -f null - 2>&1 | grep silence_

  The path is `<audio dir>/<YYYY-MM-DD>/<utt>.wav` (`src/mouth/audio.py`).
  The audio dir is `$MOUTH_AUDIO_DIR`, else `<mouth dir>/audio`,
  and the mouth dir defaults to `<root>/mouth` (the state root). The `said` row's
  `audio` field holds the exact path. Lines are kept for 7 days by default
  (`$MOUTH_AUDIO_DAYS`).
- Request-to-kept time over `dur_s`, from `mouth audio --voice CLONE`. Each
  JSON line carries `requested_ts`, `kept_ts` and `dur_s`:

      mouth audio --voice carol --last 20 | python3 -c '
      import json, sys, datetime as d
      for l in sys.stdin:
          m = json.loads(l)
          if m.get("requested_ts") and m.get("dur_s"):
              t = (d.datetime.fromisoformat(m["kept_ts"]) - d.datetime.fromisoformat(m["requested_ts"])).total_seconds()
              print(m["kept_ts"], m["utt"], m["dur_s"], round(t / m["dur_s"], 2))'

  It read 1.01-1.08 when healthy and 1.1-2.9 during the incident. Read it
  with care: `kept_ts` has whole seconds only, so a short line is noisy, and
  a line that waited in the queue behind another reads high anyway. Prefer
  `underrun_s`, which now measures the same thing directly.

**Remedy.** Switch to a kokoro voice, and take the load off the Mac (cause 1
starves the clone too).

- For one line: `mouth say --voice af_sky "..."`.
- For a seat: set it once, and leave `--voice` off. See
  [Your voice](mouth.md#your-voice).
- For the host: `voice` in the mouth's settings file
  (`$MOUTH_SETTINGS`, else `~/.sessionkit/voice/settings/mouth.json` when it
  exists, else the old `<root>/mouth/settings.json`, `<root>` being
  voice's state root),
  which the phone's Settings app writes. A session, seat or agent
  `mouth.voice`, or the caller's `$MOUTH_VOICE`, outranks it for
  that caller's lines, unless the file locks `voice`
  (`"_locked": ["voice"]`). `mouth voices` prints the default and which
  layer set it (`"from"`).

A lead buffer also helps here. A clone line buffers 3 s of its audio
before it plays by default, so a clone that renders a little slower than
real time has audio in hand; `mouth say --lead S` (or `lead_s` in the
mouth's settings) sets another lead, `0` none. See
[mouth.md](mouth.md).

### Cause 3: the Bluetooth link is losing audio

**Symptom.** Chop on a Bluetooth headset (AirPods) while the Mac is not
loaded, often as the user moves away or turns. On 2030-06-09, RSSI
fell to -82 dBm and PLC rose to 13 a second, from distance or a body in the
way.

**Check.** The same LinkQualityReport as cause 1, read for **RSSI** and
**PLC** (packet-loss concealment). Healthy that evening was RSSI -44 to -57
and PLC 0 to 0.12. The loss read -82 dBm with PLC up to 13/s. The `said`
rows show `device_gaps` 0 and `underruns` 0, because the Mac delivered every
sample.

**Remedy.** Move closer to the Mac and clear the line of sight. Nothing on
the Mac fixes it.

### Music over HFP always sounds poor

While the headset's mic is in use, the link is HFP: 16 kHz mono, built for
speech. Music over it always sounds poor, and that is not a fault. A music
bed that ducks (the default) is mixed with the lines, block by block, by the
mouth's own pacing thread on its way to the device callback (`Mixer`,
`src/mouth/output.py`). So the music is also the first thing to break when
the Mac stalls.

### Cheapest live remedies, in order

1. **Stop the music.** `mouth stop` cuts what is playing, every music bed
   included, and flushes the queue. `mouth stop --current` cuts what is
   playing and the beds but keeps the queued lines. To cut only the music,
   take the bed's `utt` from `mouth status` (`"music"`) and run
   `mouth retract UTT`.
2. **Switch to a kokoro voice** (cause 2, above).
3. **Launch nothing heavy on the Mac while the user is on voice.** No fresh
   `claude` processes and no renders (`mouth render`, video, builds).
4. **Pause other heavy launch agents.** A background watcher or indexer that
   is not part of sessionvoice starves the Mac the same way; ask its owner to
   pause it while the user is on voice.

### Related

- [First, read the line's own counts](#first-read-the-lines-own-counts): the
  device-gap counts on the `said` row.
- [mouth.md](mouth.md): the lead buffer before a line starts (`--lead`).
- [logs.md](logs.md): the heard log's format and its files.

## Ears: they hear nothing

**Lid shut: the built-in mic is off in hardware; use Bluetooth buds or a USB
mic.** That is the commonest cause. The shut lid disconnects a MacBook's own
mic, but the mic still hands over frames, every sample 0. In older releases the
keeper moved onto it when the AirPods left, and the ears ran deaf with
nothing said. Now it holds: one mail, `ears: DEAF, lid shut, built-in mic
off, holding at HH:MM EOM`, and no capture on the built-in until another
input becomes the Mac's default or the lid opens.

**Check.** A deaf capture tells you so. The keeper mails one `ears: DEAF, ...`
line when it goes deaf and one `ears: hearing again ...` when it hears again,
and it writes an `ears-deaf` and an `ears-hearing` event to the heard log.
[When the ears go deaf](ears.md#when-the-ears-go-deaf) lists every cause
with its exact mail. To read the events:

    jq -c 'select(.event == "ears-deaf" or .event == "ears-hearing") | {ts, event, device, text}' \
      "$(ears home state-dir)/logs/conversations/heard_$(date +%F).jsonl"

The keeper log, `~/.sessionkit/voice/log/ears-keeper.log`, has a
`DEAF (CHANNEL): CAUSE` line for each, and every step it took.

**Remedy, by cause** (the word after `room:` or `speakers:` in the event):

- `lid`: make Bluetooth buds or a USB mic the Mac's input, or open the lid.
- `model`: run `ears install`. It fetches the Silero model.
- `stt`: the speech server failed. Check it is up (`ears services
  status`; by default it is on `127.0.0.1:8890`), and read its log for the
  error. The mail's `CODE` is its HTTP status.
- `zeros`: a mic that gives only zeros and no shut lid. The keeper has
  reopened it once already. Check the microphone grant in System Settings,
  Privacy & Security, Microphone, and that the input is the mic you meant.
  The same zeros at install: [Install: the microphone was not
  heard](#install-the-microphone-was-not-heard).
- `beat`: the capture stopped writing heartbeats. The keeper stops it after
  300 s and starts a fresh one; if it comes back, read
  `ears-keeper-capture.log`.
- `cover` (the speakers side-car): its mic stream hears nothing while the
  room does. The keeper only reports this; the side-car's own reopen is
  separate.

## Install: a service did not load

`ears services install` printed a row with `FAILED:` and exited 2. It tried
every service, and the block at the end of its stderr names each one that did
not load, with launchctl's reason.

**Check.** Read the install log: the path is the run's `log` row,
`~/.sessionkit/voice/log/install.log`. Each run is one block, from its `=== ... ears services install`
header to its `=== ... rc N` footer. Its `launchctl bootstrap ... -> rc N`
lines are launchctl's own answers. Then run `ears services status`, which says
what is loaded now.

**Remedy.** `Bootstrap failed: 5: Input/output error` most often means the old
job had not gone yet; install already retried it once. Run `ears services
install --only NAME` for each one named: NAME is the name in the block, but
`sounds` for `ears-sounds` or `mouth-sounds` (`--only` takes the service,
not its job). If it fails the same way, the
service's own log in `~/.sessionkit/voice/log/` (`EARS_LOG_DIR` moves it) may say
why it would not start.

## Install: the microphone was not heard

`ears services install` waits for the keeper's room capture to hear something,
and printed a `mic NOT HEARD` line on stderr and exited 2.

**What it checks.** After the keeper loads, with listening on, install waits up
to 60 s (`EARS_MIC_CHECK_S`) for the capture's first heartbeat with sound.
macOS asks for the microphone on behalf of that capture's Python, not your
terminal, so this is the only proof that the grant the room uses is in place.
Install opens no microphone itself.

- Listening is on after `--listen`, or when `ears.json` already has it on (a
  re-install or an upgrade).
- It prints this before the wait. When macOS asks, click Allow:

      mic    listening up to 60 s for the keeper's first sound (EARS_MIC_CHECK_S); if macOS asks to use the microphone, click Allow

- Heard, the exit unchanged:

      mic    heard: MacBook Pro Microphone (peak 1834), 18 s after the keeper loaded

**The lines that exit 2**, one of them on stderr:

    ears services install: mic NOT HEARD in 60 s (only silence): open System Settings > Privacy & Security > Microphone and turn on python3.13 (the ears keeper's capture), then run: ears services install --only keeper
    ears services install: mic NOT HEARD in 60 s (no sound arrived): open System Settings > Privacy & Security > Microphone and turn on python3.13 (the ears keeper's capture), then run: ears services install --only keeper
    ears services install: mic NOT HEARD in 60 s (the capture failed): open System Settings > Privacy & Security > Microphone and turn on python3.13 (the ears keeper's capture), then run: ears services install --only keeper
    ears services install: mic NOT HEARD: the lid is shut, so the built-in mic is off; open the lid or plug in a mic, then run: ears services install --only keeper
    ears services install: mic NOT HEARD: ears.json pins the input to USB Mic, which is not connected; plug in USB Mic, or remove the pin from ears.json, then run: ears services install --only keeper

- `python3.13` is the app macOS shows: the file the capture runs as (a
  framework Python shows as `Python`). Install names it from the capture's
  own process, else from the Python it installed with.
- The lid and the pinned input are said at once, with no wait: the Privacy
  setting would be the wrong fix for either.

**Causes of the first three.**

- **No grant.** You clicked Don't Allow, or the grant was turned off.
- **Allow came too late.** The first beat comes about 20 s after the keeper
  loads, then one every 15 s. Inside the 60 s bound, that leaves about 40 s
  from the prompt to your click. A later Allow still works for the room;
  only this check missed it. Re-run.
- **The app is not in the list yet.** If the prompt was never answered, the
  Python may not be listed under Microphone, and there is no `+` to add it.
  Re-run the remedy below, and click Allow when macOS asks.
- **A muted input, or one at volume 0,** also gives only silence. Check the
  input's level in System Settings > Sound > Input.
- **The wrong input.** The room follows the Mac's default input unless
  `ears.json` pins one (`input`).

**The evidence** is in the install log, never on the screen: one `mic` line per
run, with the verdict, the time waited, each beat's peak, the capture's pid,
the app's path and any capture error. The path is the run's `log` row,
`~/.sessionkit/voice/log/install.log`.

**Remedy.** Fix the cause, then run the check again. Listening is on by then,
so no flag is needed. It reloads only the keeper, whose next beat answers
within 15 s:

    ears services install --only keeper

**The cases it does not check**, each one line, the exit unchanged:

    mic    not checked: listening is off; install with --listen to check now
    mic    not checked: EARS_MIC_CHECK_S=0
    mic    not checked: this Mac has no microphone (no audio input device)
    mic    not checked: a call is live, so the room mic is off until it ends
    mic    not checked: the keeper here is hand-made (LABEL); install did not load it
    mic    not checked: the keeper did not load (above)
    mic    not checked: --only has no keeper
    mic    not checked: no keeper in this run

- Listening off comes first. On a fresh install without `--listen`, a Mac
  with no input device prints the listening-off line, not the
  no-microphone one.
- A hand-made keeper (`LABEL` is its launchd label) is not checked because
  install did not load it. `--listen` still turned listening on: the hand-made
  keeper reads the same `ears.json`.
- `no keeper in this run`: a refusal above dropped the keeper.
- `EARS_MIC_CHECK_S=0` turns the check off for a run where nobody is at the
  Mac (a scripted re-install, an upgrade). Without it, a run with listening on
  can exit 2 on a shut lid, a muted input or an unanswered prompt.
- A dry run says what it would do and opens nothing:

      mic    would wait up to 60 s for the keeper's first heartbeat with sound; nothing is opened now

**Known limits.**

- `--only ...,speakers` also loads the speakers side-car, a second microphone
  user that runs on its own Python. The check does not prove that one's grant.
- A re-install keeps a capture that is already running, so the check proves
  that capture. A new Python with the same code is checked only after the
  capture restarts.

## Mail: you hear that it is failing

The mouth said one of these, once:

- *"Turn mail is failing: 3 in a row did not go out, so agents are not
  getting your turns."* The turn mailer (`keeper/ears-mail.py`) could not
  mail your turns to the agents.
- *"The mouth's notices are failing: 3 in a row did not go out, so agents
  are not told when their lines are cut or dropped."* The mouth could not
  mail the notice a seat gets when its line is cut or dropped.
- *"The ears' notices are failing: 3 in a row did not go out, so agents
  are not told when the microphone or the capture changes."* The keeper
  could not mail its own notices (mic changes, DEAF, a device gone or back).

Each is said once per run of failures, never once per turn. When a send
goes through again you hear that it works again, also once. Nothing stops
over it: the ears keep hearing and the mouth keeps speaking.

**Check.** Read the log the line names, in `~/.sessionkit/voice/log/`
(`EARS_LOG_DIR` moves it). Turn mail: `ears-mail.log`. Notices:
`ears-mouth.log`, or `player.log` when `mouth say` started the player.
The keeper's notices: `ears-keeper.log`, where each failed one is `mail
FAILED (<why>): <subject>`.
Each failed send has its own line just above the alert, `ears-mail: ERROR:
send to <seat> failed, exit <code>: <why>` or `mouth: ERROR: notice to
<addr> failed: <why>`, and the why names the cause. The commonest is that
`message` (sessionmail's) is not installed or not found: exit 127, or `no
message binary`.

**Remedy.** Fix the cause the log names; the next send that goes through
brings the "working again" line. What you said while turn mail failed was
not sent, so say it once more if it matters.

**Turn it off.** `"mail_alert": "off"` in `ears.json` stops the turn
mailer's and the keeper's; `"notice_alert": "off"` in `mouth.json` stops the mouth's. The
failures are still logged. `mail_alert_after` and `notice_alert_after` set
how many in a row it takes, 3 by default ([settings](settings.md)).
