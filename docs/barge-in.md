# Barge-in

Barge-in is the user talking over a line the mouth is speaking. The mouth
dips the line as soon as the user's voice starts. It cuts the line once they
have said 2 words of their own, or a stop word. Later it replays the part
they did not hear. Headphones and a call share one code path; they differ only in where
the audio goes and which ears it listens to. Road noise can dip a line but
should not cut it. The knobs for that are under "Tuning against road noise".

- **For whom:** anyone tuning the mouth, and the agent whose line was cut.
- **Measured at:** `18ac9c1` (origin/master, after the change that put the
  barge policy in one place). Code is in `src/mouth/` unless a path says otherwise.

## What the mouth does

1. **Dip.** The user's voice onset dips the line to 25% of its level (about
   -12 dB) before any word is decoded. It stays down while their voice is on.
   It comes back up `barge_duck_recover_s` after their voice stops, or after
   `barge_duck_max_s` (4 s) if no stop is seen. Noise only ever dips a line
   (`onset.BargeDuck`).
2. **Cut.** 2 content words of the user's cut the line. Content words are words
   that are not filler (`barge_ignore_words`) and not the line's own (echo).
   A stop word cuts at once: `stop`, `wait`, `hang`, `hold`, `pause`,
   `shush`, `quiet`, `enough`, `interrupt(s/ing/ed)` (`hold.STOP_ASKS`).
   Only words they said after the line began count, and only if the ears wrote
   them within 4 s of the audio (`hold.barge`).
3. **Flush.** Every line queued before their words is dropped too, because it
   is stale now. Music is never dropped (`player._handle_stop`).
4. **Hold the rest.** The unsaid rest is queued again from the start of the
   sentence the cut fell in (`hold.rest_of`). It is held for the end of their
   turn (`hold: turn-end`) and then plays (`player._resume`).
5. **Drop it after 20 s.** If the replay has not started 20 s
   (`resume_s`) after the later of the cut and the end of the user's turn, it
   is dropped as `expired` (`hold.check`). It is also dropped if they asked
   for quiet (`quiet`, `shush`, `hush`, `shut`, `stop`, `silence`), and if
   their turn answered it: the sentence it was cut in ends in `?` and their turn
   holds 2 or more content words, not counting stop asks or the line's own
   echo (`hold.answered`, detail `the user answered it`). So "hang on", "wait", a
   blip or a one-word "yes" still gets the replay; a real answer does not.
6. **Tell the sender.** The cut line's sender gets a notice. It names the
   words that cut the line (`cut by:`) and the replay's utt. While the replay
   is queued, the sender may `mouth amend UTT TEXT` it, or `mouth retract
   UTT` it. It does not re-say the line. A second notice says how the replay
   ended.

There is no replay when:

- the cut line was itself a replay (a replay is never replayed);
- the user asked for quiet;
- the user's turn answered the cut question (dropped at the end of that turn);
- it was a played file;
- nothing was left unsaid;
- `resume_s` is 0 (`player._plan`).

A line its sender retracts while it plays (`mouth retract`) is cut, but
that is not a barge-in, and it is never replayed.

## Which path, per device

**One path.** Both kinds of device run the same functions with the same
policy, so a call line is cut on the same 2 words as a headphone line. There
is no per-call word count any more. `$MOUTH_BARGE_WORDS_CALL`
is retired: the mouth ignores it.
The device kind changes only these steps:

| Step | Local (AirPods, headphones) | Call (`call:<N>`, the bot's socket) |
| --- | --- | --- |
| May it be cut? `hold.barge_device` | Only if the output's name matches `airpods\|headphone\|headset\|buds\|beats` | Always |
| Whose ears count: `hold.barge_sources_for` | `mic`, the room capture: `launch_capture` in `src/ears/_keeper/ears-keeper.sh` | `call` only, the call capture: `start()` in `src/ears/_keeper/call-ears` |
| Where the dip is applied: `output.open_output` | `output.DeviceOut._callback`, after the 200 ms queue | `output.CallOut.write`, before the bot's 0.2 s paced queue |
| Who may cut: `hold.barge_in` | The `barge_in` setting; unset, `anyone`, or the operator's mailbox name when one is configured | `anyone` when unset. The speaker side-car names room audio only, so names set for a call only delay a cut by the 2 s label wait |

- **The computer's own speakers never barge.** The mic hears the line
  garbled, so the mouth neither dips nor cuts a line there.
- **A call ignores the room mic.** The mic would hear the call played aloud.
  The keeper also stops the room capture while a call is live, unless
  `room_on_call` is `true` in `~/.sessionkit/voice/settings/ears.json`.
- **A line over music** (`output.MixLane`) uses its device's or call's dip.

**The shared path:**

- `player.play_one` reads the policy once per line (`hold.barge_policy`).
- Every poll, `check_stop` calls `hold.barge`. On a cut it writes the stop
  file (`player._write_stop`).
- The dip is `onset.for_line`, which starts `onset.BargeDuck`. That follows
  the ears' `vad` rows through `onset.OnsetTail`.
- `player._said` decides the replay (`player._plan`). `player.serve` then
  flushes the queue (`_handle_stop`) and queues the replay (`_resume`).
- `hold.check` holds the replay and expires it.

## Tuning against road noise

**No detector threshold can fix noise.** Silero scores road noise 0.95-0.98,
and real speech 1.00. So noise will dip a line, and the defence is the
word rule: the recogniser must turn the noise into 2 content words before
anything is cut.

**Read before you tune.** `mouth status` prints `barge`: each value, with
the layer that set it (`from`, `by`). A cut line's `said` row carries
`cut_by` and `cut_how`. The player's own log, `player.log`, holds a record
of every weighing and dip:

    jq -cR 'fromjson? | select(.kind=="barge" or .kind=="duck")' player.log

**Where knobs are set.** Settings go in `~/.sessionkit/voice/settings/mouth.json`
(`$MOUTH_SETTINGS`), schema `settings/mouth.schema.json`. The
player reads them once per line, so a change applies to the next line.
`$MOUTH_*` variables are read from the player's own environment,
not the caller's. Highest wins: `mouth say --barge-words N|off` or
`--no-barge` for one line, then `mouth barge` for a while, then the setting,
then the variable, then the built-in.

| Knob | Default | Set it with | Against noise |
| --- | --- | --- | --- |
| `barge_words` | 2 | setting; `mouth barge N\|off --for T`; `mouth say --barge-words`; `$MOUTH_BARGE_WORDS` | 3 needs one more word, and still cuts on a stop word |
| `barge_ignore_words` | 159 built-in filler words (`hold.STOP_WORDS`) | setting (replaces the list); `mouth barge --ignore W,..` (adds) | Ignore a word the recogniser keeps hearing in noise |
| `barge_duck_level` | 25 (0-100) | setting; `mouth barge --duck L\|off` | 100 or `off`: noise no longer dips the line |
| `barge_duck_max_s` | 4 s | setting; `mouth barge --duck-max S` | Shorter: unbroken noise holds the line down for less time |
| `barge_duck_recover_s` | 1.0 s on `nemotron` partials, 2.5 s on `final` | setting only | Shorter: back up sooner after a burst |
| `barge_in` | `anyone`; `{"room": [<mailbox>]}` with an operator | setting | Names gate the room. On a call, unnamed words count after the 2 s wait |
| Freshness bound | 4 s | `$MOUTH_BARGE_FRESH_S` | Words written later than this do not count |
| Wait for a speaker name | 2 s | `$MOUTH_LABEL_WAIT_S` | Unnamed words then count as anyone's |
| Replay window | 20 s | `$MOUTH_RESUME_S` (0: no replay) | Nothing |
| `resume_answer_words` | 2 | setting; `$MOUTH_RESUME_ANSWER_WORDS` | Nothing: content words of the user's turn that answer a cut question, so its replay is dropped |
| `resume_answer_question` | `true` | setting; `$MOUTH_RESUME_ANSWER_QUESTION=off` | Nothing: `false` drops the replay of any cut line the user's turn answered, not only a question |
| Word barge-in on/off | on (`partial`) | `$MOUTH_BARGE=off` | Off for every line: no word cuts, not even a stop word; the dip stays |
| Devices that barge | `airpods\|headphone\|headset\|buds\|beats` | `$MOUTH_BARGE_DEVICES` (a regex) | Nothing; a call always barges |

The `mouth barge` override (`src/mouth/bargeswitch.py`) is written to
`<mouth dir>/barge.json`. It lasts 1 h by default (`--for 90s|30m|1h`), and
`mouth barge reset` ends it. Each `mouth barge` replaces the whole
override, so name every value you want in one command. For one noisy drive:

    mouth barge 3 --for 40m                # the rest of this call
    mouth barge 3 --ignore shell --for 40m # and a word the recogniser keeps hearing

To make it permanent, put `"barge_words": 3` in the settings file.

**The detector** is in the ears, not the mouth. Both captures run Silero VAD
(`--detector silero`).

- **Threshold:** 0.5. Speech starts at it and ends below 0.35
  (`src/ears/listen/silero.py`). The only knob is `ears.listen capture
  --silero-threshold`. Neither the keeper nor call-ears passes it, and there
  is no setting or variable. Leave it: it cannot tell noise from speech.
- **Voice edges:** an onset after 60 ms of voice, an offset after 210 ms of
  quiet (`MIN_SPEECH`, `MIN_SILENCE` in `src/ears/vad.py`, used by `_Edges`
  in `src/ears/listen/loop.py`). These are constants, with no knob.
- **Partials:** `partials` in `~/.sessionkit/voice/settings/ears.json` (`$EARS_CONF`),
  `final` by default or `nemotron`. It sets how soon the words arrive, and
  the built-in `barge_duck_recover_s`.

## Go deeper

- [mouth.md][mouth]: the mouth, its queue, devices and voice.
- [troubleshooting.md][trouble]: when a line chops for other reasons.
- [architecture.md][arch]: how the room ear, the call ear and the mouth fit
  together.
- [events.md][events]: the `vad`, `partial`, `saying` and `said` rows.

[mouth]: mouth.md
[trouble]: troubleshooting.md
[arch]: architecture.md
[events]: events.md
