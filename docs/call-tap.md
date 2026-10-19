# Call tap: a call from any app, into the ears, both ways

Two BlackHole cables and two Multi-Output devices put a call's far end into
the ears as its own speaker, and let agents speak into the call.

First used live on 2030-06-09 on a Delta Desktop call. It ran for 80
minutes. The far end heard an agent speak, and every far-end line landed as
`heard mic/BlackHole 2ch`, never mixed up with the local user's.

## The wiring

```text
far end --> app speaker "AirPods + BlackHole 2ch" --> your AirPods
                                                  \-> BlackHole 2ch --> 2nd ears capture
                                                                        (device "BlackHole 2ch")

your AirPods mic --(sox)--\
                           +--> BlackHole 16ch --> app microphone
agents: mouth say --------/
  --device "AirPods + BlackHole 16ch" (you hear them too)
```

BlackHole is a virtual cable: whatever is played into it comes out as an
input. It **sums** everything written into it. So one cable carries the far
end *to* the ears, and the other carries you and the agents *to* the far end.

## Setup, once per machine

1. BlackHole 2ch and 16ch (the brew casks). `SwitchAudioSource` (brew
   `switchaudio-osx`) and `sox`.
2. The two Multi-Output devices. The first device named is the clock; the
   others get drift correction:

   ```bash
   swift bin/multi-output.swift make "AirPods + BlackHole 2ch" airpods "BlackHole 2ch"
   swift bin/multi-output.swift make "AirPods + BlackHole 16ch" airpods "BlackHole 16ch"
   swift bin/multi-output.swift list
   ```

## Each call

1. **If the app has a device picker**, set mic = `BlackHole 16ch` and
   speaker = `AirPods + BlackHole 2ch`.
2. **If it hasn't** (Delta Desktop 2.59.1 has none), set the Mac's defaults
   instead. **Pin the ears to the AirPods FIRST.** In FOLLOW mode the
   ears-keeper follows the default input and would move the room capture onto
   BlackHole 16ch.
   - put `"input": "airpods"` in `~/.sessionkit/voice/settings/ears.json`
   - `SwitchAudioSource -t input -s "BlackHole 16ch"`
   - `SwitchAudioSource -t output -s "AirPods + BlackHole 2ch"`
3. `bin/call-tap up`. It runs in the foreground; from an agent, use a
   backgrounded Bash call, never a raw `&`.
4. **Agents speak with `--device "AirPods + BlackHole 16ch"`** for the whole
   call. A line to the default device goes into BlackHole 2ch, where the ears
   hear it as the far end, and the far end never hears it.
5. **Wear the AirPods.** On laptop speakers, the far end hears itself.
6. **Tell the other people** that they're being transcribed.

## After the call

```bash
bin/call-tap down
SwitchAudioSource -t output -s airpods
SwitchAudioSource -t input -s airpods
```

Then set `"input": "default"` in `ears.json`, and check
`pgrep -fl 'listen capture'` still shows `--device airpods`.

## Listening off

`listen: false` in `~/.sessionkit/voice/settings/ears.json` stops the far-end capture
and leaves the passthrough running: the far end still hears you, and the
ears hear nothing. The ears keeper does it with two files in
call-tap's run dir, `${XDG_STATE_HOME:-~/.local/state}/ears/call-tap`:

- **`LISTEN-OFF`**: the keeper creates it on `listen: false` and removes it
  on `listen: true`. While it is there, `call-tap up` runs no capture. It
  checks once a second, and starts the capture again when the file is gone.
- **`STOP-ear`**: the capture's own stop file. The keeper creates it after
  `LISTEN-OFF`, to stop a capture that is running.

`call-tap up` with `LISTEN-OFF` already there starts the passthrough and no
capture. `call-tap status` prints `capture: paused (listening OFF by
ears.json)` while it waits. `call-tap down` creates `STOP` and `STOP-ear`
and ends both legs, paused or not.

If the keeper dies while listening is off, `LISTEN-OFF` stays and the ear
stays paused until a keeper starts with `listen: true`. That fails closed:
the ears stay off rather than listening when you said off. To clear it by
hand, set `listen: true`, or remove `LISTEN-OFF`.

## Gotchas, measured

- **The keeper stops a second capture.** It treats any `ears.listen capture`
  or `voice_mode.listen capture` process as THE room capture, and it stops one
  on a non-default device. `call-tap` starts its capture through `runpy`, so
  the keeper doesn't see it. The real fix is for the keeper to skip captures
  that aren't the room's.
- **No missed-reply clips on the far end.** Its capture is `--source mic`, but
  the "mic" is BlackHole 2ch, the far end's voice. The missed-reply clip
  is meant for the local user's mic audio only. It is opt-in
  (off unless `--missed-reply-window` is given), and `call-tap` also passes
  `--missed-reply-window 0` by name. Keep that flag if you change the capture line.
- **Whisper hallucinates on a quiet line.** The far end's silence came back as
  "Bye." and "Yeah." every 20-60 s. Every one is a turn, and every turn wakes
  an agent that's waiting on turns.
- **Teams, Zoom and a browser call** should wire up the same way through their
  own device pickers. Not tried yet.

## Files

- `bin/call-tap`: `up`, `down`, `status`. Run dir: `STOP` (`down`),
  `STOP-ear` (the far-end capture's stop file), `LISTEN-OFF` (the keeper's
  pause), `sox.pid`, `capture.pid`
- `bin/multi-output.swift`: `make` and `list` Multi-Output devices from the
  command line (CoreAudio `AudioHardwareCreateAggregateDevice`, stacked)
  A device it makes gets the UID `dev.sessionkit.voice.multi-output.<name>`.
  Devices are found by name, never by UID, so a device made before the
  rename keeps its old UID and goes on working: nothing to redo.
