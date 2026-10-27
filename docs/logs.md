# Logs: the heard log and the log files

Where sessionvoice writes its logs, and what a line in each one means. Most
questions are answered by the heard log: one JSON line for each thing the
ears heard or the mouth said, in
`<home>/logs/conversations/heard_YYYY-MM-DD.jsonl`, where `<home>` is
sessionvoice's home ([below](#sessionvoices-home)). The services keep
their own logs in `~/.sessionkit/voice/log/`, beside the settings
(`EARS_LOG_DIR` moves it). In 0.1.0 those were split between
`~/Library/Logs/ears/` and `~/Library/Logs/failmode/`; master puts them all
in one folder, and a move from `~/Library/Logs/` left the old files where they
were ([below](#what-010-does-differently)). Everything here was read from the code at master `138484c`,
and each `file:line` cited is a line at that commit.

## Where each thing is logged

- **What the ears heard, and what the mouth said:** the heard log,
  `heard_YYYY-MM-DD.jsonl` ([the heard log](#the-heard-log)).
- **Who said each heard line:** `speakers_YYYY-MM-DD.jsonl`, beside the
  heard log, where the speakers side-car runs.
- **What the keeper did** (device changes, restarts, calls):
  `ears-keeper.log`. The room capture's own output is in
  `ears-keeper-capture.log`, and the call ears' output is in
  `ears-keeper-calls.log`.
- **Which turn mails were sent, and which were skipped:**
  `ears-mail.fire.jsonl` (sent) and `ears-mail.log` (sent and skipped).
- **What the mouth's player did** (barge and duck records):
  `ears-mouth.log`, or `player.log` in the same log folder.
- **The user's audio when a reply went unheard:** the missed-reply clips are WAV
  files, not a log, and they are kept only while recording is on (off by
  default: [ears.md](ears.md#recording)). When a mouth line ends and no turn
  comes within the window (30 s in the keeper's room capture), the capture
  keeps that window's mic audio as one 16 kHz mono WAV, mode 0600, in
  `<home>/state/missed-reply/`. Clips older than `record_days` (7 by
  default, `0` = kept forever) are deleted, but only while recording is on
  (`src/ears/listen/missed.py`). A `missed-reply` event names each one. A
  30 s clip is about 1 MB.
- **The user's audio for each turn:** also only while recording is on. Each
  final turn's audio is one WAV in `<home>/audio/<YYYY-MM-DD>/`, named by
  the turn's `audio` field and the heard mail's `Ears-Audio` header, and
  kept for `record_days` (7) days ([ears.md](ears.md#kept-turn-audio)).

The [list of log files](#the-log-files) gives each file's path, its writer,
and what changed since 0.1.0.

## The heard log

- **Path:** `<home>/logs/conversations/heard_YYYY-MM-DD.jsonl`, under
  [sessionvoice's home](#sessionvoices-home). The mouth
  writes to `$MOUTH_LOG_DIR` when that is set, and otherwise to
  the same folder (`src/mouth/box.py:277`).
- **One line, one record.** A line is never edited or removed, and a
  correction is a new line. A new file starts at local midnight.
- **`seq` counts across days.** Each writer takes `heard.lock` in the same
  folder, then writes the last `seq` plus one (`src/ears/heard.py:194-212`).
  A `seq` therefore names one line on its own.
- **Writers:** the capture (`src/ears/listen/loop.py`, through
  `src/ears/listen/sink.py:34`), the mouth (`src/mouth/player.py`,
  `src/mouth/bed.py`), and the keeper, through `heard.py write`
  (`keeper/ears-keeper.sh:486-488`, `1184-1212`). VoiceMode's `converse` and an earlier Delta bot's
  bridge write the same shape from outside this repo (`src/ears/heard.py:15`).

Read today's log with `jq`. The `home` row of `ears services status` names
the home:

```bash
LOG=~/.voicemode/logs/conversations/heard_$(date +%F).jsonl   # or ~/.sessionvoice/...
jq -c 'select(.kind == "turn") | {seq, ts, device, text}' "$LOG" | tail
jq -c 'select(.kind == "event") | {seq, event, device}' "$LOG" | tail
```

### sessionvoice's home

The heard log, `heard.lock`, the speakers rows, the mouth's folder (`mouth/`), talk's token (`talk/`) and
the ears' state (`state/`) all live under one root, sessionvoice's home. The logs are not under it: they have their own folder,
`~/.sessionkit/voice/log/`. `src/ears/home.py` finds the home, in this order:

1. `$SESSIONVOICE_HOME`, if it is set.
2. `$VOICEMODE_BASE_DIR`, if it is set. This is the old name, and it is
   still read.
3. `~/.sessionvoice`, if it holds the heard log.
4. `~/.voicemode`, if it holds the heard log.
5. Otherwise `~/.sessionvoice`.

A home "holds the heard log" when its `logs/conversations` folder has a
`heard.lock` or a `heard_*.jsonl` in it. A folder that exists but has neither
is ignored. An empty variable counts as not set.

So a host that already keeps its heard log in `~/.voicemode` keeps using it
there, and a new host starts in `~/.sessionvoice`. Nothing is moved. Moving
the files is a separate step, run by hand.

If both folders hold a heard log, the log is split in two, and `seq` no
longer names one line. sessionvoice uses `~/.sessionvoice`, and
`ears services status` prints a warning naming both folders and the fix
below.

**A fresh host that also runs VoiceMode.** VoiceMode's `converse` writes
the heard log as a guest (see below), in `~/.voicemode`. It reads
`$VOICEMODE_BASE_DIR` but not `$SESSIONVOICE_HOME`. On a FRESH host that
also runs `converse` as a heard guest, set `SESSIONVOICE_HOME=~/.voicemode`
(or `VOICEMODE_BASE_DIR`), so that both write one log. Otherwise whichever
writes first decides. If `converse` writes first, sessionvoice adopts
`~/.voicemode`. If sessionvoice writes first, it writes `~/.sessionvoice`
and `converse`'s lines split off into `~/.voicemode`. sessionvoice does not
look for VoiceMode; the variable is the only switch.

VoiceMode's own files stay in `~/.voicemode` whatever the home is:
`voicemode.env`, `voices/`, `services/` and its `exchanges_*.jsonl` logs.

### Who owns this format

sessionvoice owns the heard log's format. It is **heard-log v1**: every line
carries `"v": 1`, which is `SCHEMA` in `src/ears/heard.py`. The format began
as VoiceMode's, and sessionvoice now keeps it.

- **The path is part of the contract.** The log is
  `logs/conversations/heard_YYYY-MM-DD.jsonl` under sessionvoice's home, one file
  per local day, with `heard.lock` in the same folder. Every writer and
  every reader finds the log there. The one exception is the mouth: when
  `$MOUTH_LOG_DIR` is set, the mouth writes its own lines to that
  folder instead (`src/mouth/box.py:277-285`).
- **Outside writers are guests.** VoiceMode's `converse` and an earlier Delta bot's
  bridge write to the same log from outside this repo. `converse` writes
  in `~/.voicemode`, so on a fresh host set `SESSIONVOICE_HOME` to it
  ([sessionvoice's home](#sessionvoices-home)). A guest writes v1
  lines only, and it takes `heard.lock` around each write, as `append()`
  does, so its `seq` is the last one plus one.
- **A change to the format bumps `v`.** A field or kind added within v1
  needs no bump, so a reader must ignore the fields and kinds it does not
  know and never fail on them. That is the rule for the engine's stream in
  [events.md][events] too.

### Fields on every line

`append()` builds every record (`src/ears/heard.py:158-225`). A field whose
value is `None` is dropped, so a missing field means it was not set. It is
never written as `null`.

- `ts`: local time with offset, to the millisecond. `seq`: see above.
  `kind`: one of `partial`, `turn`, `event`, `saying`, `said`
  (`heard.py:53`). `v`: the schema, `1` (`heard.py:45`). These four are the
  writer's to set: a caller that passes one gets a `ValueError`
  (`heard.py:69`, `188-190`).
- `source`: where it came from. The capture writes its source's name: `mic`,
  `call` or `file` (`src/ears/listen/sources.py:160`, `225`, `378`). The
  mouth writes `mouth`. A writer that names no source gets `mic`.
- `device`: the input or output by name, `call:N` for a call, and the
  device named `null` (the string `"null"`) for the mouth's silent output.
- `session` and `agent`: who the capture or the line belongs to.
- `final`: `false` on a `partial` or a `saying`, `true` on a `turn` or a
  `said`, absent on an `event` (`heard.py:191-192`).

`append()` refuses a record that breaks its rules (`heard.py:180-187`).
`partial`, `turn` and `saying` need `text`. `saying` and `said` need `utt`.
An `event` needs its name in `event`.

### `audio_ts` and `lag_s`: when the audio was spoken

`ts` is when a line was written. When the speech to text falls behind,
that can be well after the words were said. So on a real-time source (the
mic, a call, or a paced `file:`) the capture stamps each `partial`,
`turn`, `turn-discarded`, `vad` and `heartbeat` with two more fields
(`src/ears/lag.py:65-67`, `src/ears/listen/loop.py:325-330`):

- `audio_ts`: the wall time the line's audio ended, in the same format as
  `ts`. It is the wall time of `listen-started` plus the line's stream
  seconds: `t1` for a `partial`, a `turn` or a `turn-discarded`, `at` for
  a `vad` and `stream_s` for a `heartbeat` (`loop.py:732`).
- `lag_s`: how late the line was written, in seconds: its write time
  minus `audio_ts`. A `turn`'s includes the detector's wait for silence
  after the last word, so read lag from partials and heartbeats.

A heartbeat also carries two fields its own `lag_s` cannot show
(`loop.py:332-342`):

- `stt_backlog_s`: the age of the oldest chunk still waiting on the speech
  to text, `0.0` when none is waiting. Partials can fall behind while the
  heartbeat itself is on time.
- `input_overflows`: on the mic, how many blocks the input device has
  dropped since the capture started (`src/ears/listen/sources.py:248`).
  Dropped audio does not advance stream time, so after a drop `audio_ts`
  reads early and `lag_s` reads large.

An unpaced `file:` source (`ears capture --no-pace`) runs faster
than the wall clock, so its lines carry none of these fields, nor do lines
from an older capture. The mouth's barge-in reads `audio_ts` to tell fresh
words from late ones. Without it, the mouth adds `t1` to the time of the
capture's `listen-started`, or failing that uses `ts`
(`src/mouth/hold.py:208-230`).

### `partial`: words recognised mid-turn

A chunk of speech turned into text, which the turn's own text will cover
later (`src/ears/listen/loop.py:380-383`).

- `text`, `final: false`.
- `t0`, `t1`: the stream seconds the chunk covers.
- `engine`: present only when the words came from a streaming engine
  (`loop.py:666`).
- `audio_ts`, `lag_s`: on a real-time source, see
  [`audio_ts` and `lag_s`](#audio_ts-and-lag_s-when-the-audio-was-spoken).

<!-- markdownlint-disable MD013 -->

```json
{"ts": "2030-12-08T08:00:00.402+11:00", "seq": 4, "kind": "partial", "text": "hello there", "source": "file", "device": "synthetic", "session": "s-doc", "agent": "doc", "final": false, "t0": 0.33, "t1": 1.86, "v": 1}
```

<!-- markdownlint-enable MD013 -->

### `turn`: a finished turn

What the user said, once the detector decided they had finished
(`loop.py:423-435`).

- `text`, `final: true`.
- `detector`: what ended the turn, for example `silero` or `vad-silence`.
- `t0`, `t1`: stream seconds, from the turn's start to the end of its
  speech.
- `audio_ts`, `lag_s`: on a real-time source, for the end of its speech.
- While recording is on ([ears.md](ears.md#kept-turn-audio)): `audio`, the
  path of the turn's kept WAV, and `audio_s`, its length in seconds. The
  file is written before this line. `audio_truncated: true` marks a turn
  longer than 120 s, of which only the first 120 s is kept. With recording
  off, none of the three is written (`src/ears/listen/turnaudio.py`).
- With `--turn-text redecode`, the keeper's setting, the text is a fresh
  decode of the whole turn's audio (`loop.py:570-590`). `text_from` is
  `redecode` or `joined` (the partials, joined). `joined` holds the joined
  partials when the re-decode won. `redecode` says why the joined text was
  kept: `too-long`, `no-audio`, `empty`, `error: ...` or `timeout`.
  `timeout` means the re-decode had not answered after `REDECODE_WAIT_S`
  (20 s). The capture stops waiting so its heartbeats and STOP checks go
  on, and it runs later STT calls on a fresh worker.
  `redecode_wait_s` and `speculative` say how long the turn waited for its
  decode, and whether that decode was started early.
  `head: "spliced"` marks a re-decode that had lost the turn's first words:
  its opening words (three, or all of them if it has fewer, compared
  lowercased with punctuation stripped) were found in the joined partials
  after their first word, so the joined words before them were put back in
  front of it. `head_words` holds the words put back. `text_from` stays
  `redecode`. Without `head`, nothing was put back
  (`restored_head` in `src/ears/listen/loop.py`).
- With `--speaker-split`: `speaker` is who said it, `split:
  "speaker-change"` marks a turn cut where another speaker took over, and
  `opened_by: "speaker-change"` marks the turn opened by that cut
  (`loop.py:403`, `482`). `speaker_from: "labels"` marks a speaker read
  from the side-car's labels without a split (`loop.py:408`). Another
  speaker takes over with K labels in a row (2), except that ONE partial
  labelled with a named person takes a turn held by the mouth's agent (a
  side-car row with `speaker_by: "mouth"`), unless that partial's text is
  the line's own echo (`mouth.hold.is_echo` against the line's `saying`
  text; `src/ears/listen/speakers.py`). The open turn is checked
  every half second, and once more as it closes, so a reply that came just
  before the end of the speech is cut off too (`loop.py:393-397`).

<!-- markdownlint-disable MD013 -->

```json
{"ts": "2030-12-08T08:00:00.402+11:00", "seq": 6, "kind": "turn", "text": "hello there", "source": "file", "device": "synthetic", "session": "s-doc", "agent": "doc", "final": true, "detector": "vad-silence", "t0": 0.33, "t1": 1.51, "v": 1}
```

<!-- markdownlint-enable MD013 -->

### `event`: everything else that happened

A named happening, with `event` holding the name and the other fields
depending on it. The names are listed in [Events](#events).

<!-- markdownlint-disable MD013 -->

```json
{"ts": "2030-12-08T08:00:00.402+11:00", "seq": 5, "kind": "event", "event": "heartbeat", "source": "file", "device": "synthetic", "session": "s-doc", "agent": "doc", "period": 2.0, "stream_s": 2.01, "peak": 4000, "rms": 1994.7, "rms_dbfs": -24.3, "v": 1}
```

<!-- markdownlint-enable MD013 -->

### `saying`: the mouth started a line

Written at the line's first audio frame, like a partial
(`src/mouth/player.py:622-625`). A `partial` of the user's between a
`saying` and its `said` is a barge-in, seen on one timeline.

- `text`, `utt` (the line's id), `final: false`, `source: "mouth"`.
- The line's own fields, on both `saying` and `said`
  (`player.py:205-224`): `voice`, `backend`, `backend_asked`, `pan`,
  `device_from` (the rung that chose the device: `mail`, `call`, `env`,
  `settings` or `default`, as in [mouth.md](mouth.md); none on a
  `mouth say` line whose caller named the device),
  `requested_ts`, `mail_id`, `file`, `start`, `end`, `hold`, `interest`,
  `resume_of`, `again_of` (a say-again replay: the utt it replays),
  `expires_t`, `volume`, `volume_from`, `in_reply_to` and `call_id`.
- A played file's own, each dropped when it has none: `source_file` (the
  file asked for, when its instrumental or a stem played in its place;
  `file` is then what played), `instrumental_from` (the layer that turned
  the instrumental on or off: `flag`, `env`, `session`, `seat`, `agent`,
  `settings`, `mail` or `builtin`), `stem` (the names `mouth play --stem`
  asked for) and `split` (for a play that waited on its file's split: what
  the split came to). See [mouth.md](mouth.md#play-music-or-a-sound).
- At the start: `speed`, `gen_s` (time to the first audio), `prefetched`,
  `lead_s` and `lead_from` (the buffer before it played), `held_s`,
  `pan_ignored`, and `level` (the voice's loudness levelling).

<!-- markdownlint-disable MD013 -->

```json
{"ts": "2030-12-08T08:00:00.555+11:00", "seq": 13, "kind": "saying", "text": "Hello Alice.", "source": "mouth", "device": "null", "session": "s-doc", "agent": "carol", "final": false, "utt": "8fa303b0e002", "voice": "af_sky", "backend": "silence", "requested_ts": "2030-12-08T08:00:00.446+11:00", "mail_id": "<1922907600499517000.8fa303b0e002@host.mouth>", "volume": 100, "volume_from": "builtin", "gen_s": 0.0, "v": 1}
```

<!-- markdownlint-enable MD013 -->

### `said`: the mouth finished a line

Written when playback ends, or for a line that never played
(`player.py:668-684`, `330-336`). It says what played and, if the line was
cut, where and why.

- `utt`, `final: true`, `source: "mouth"`, and the line's own fields, as
  on `saying`. There is no `text`. What played is in `text_played_est`.
- `played_s`, `dur_s` (the whole line, once synthesis finished), `cut`,
  `cut_at_s`, `text_played_est` (the text cut back to the last whole
  word), and `audio` (the kept WAV).
- `reason`: `done` when it played to the end. Otherwise it is the stop's
  reason (`stop`, `barge-in` and others), or `device-lost`,
  `device-absent`, `open-failed`, `error`, `expired`, `stale`, `lost` or
  `amended`. `detail` holds the error text. `device-absent` means the device
  (or PortAudio) was not there; `open-failed` means it was there but refused
  to open, and `detail` carries the audio system's words, such as
  `PortAudioError: ... Invalid number of channels [-9998]`. `amended` closes a line the player took
  but cut before its first frame, because `mouth amend` replaced it.
  Its replacement keeps the same `utt`, so that `utt` gets a
  second `said`, the replacement's own.
- The chop counts: `underruns`, `underrun_s`, `device_gaps`,
  `device_gap_s` and `device_gap_max_s`. How to read them is in
  [Troubleshooting][troubleshooting].
- `over`: the music bed it was mixed over. `level`: as on `saying`, plus
  `limited`.
- On a barge-in cut: `cut_by`, `cut_how`, `barge_words` and
  `barge_words_from` name the words that cut it (`player.py:343`).
  `resume` (`will` or `won't`) with `resume_utt` and `resume_s`, or
  `resume_why`, says whether the line will be replayed
  (`player.py:818-846`).
- `notice`: where the drop notice went, on a cut line or on a replay
  heard in full (`player.py:245-255`).

<!-- markdownlint-disable MD013 -->

```json
{"ts": "2030-12-08T08:00:01.298+11:00", "seq": 14, "kind": "said", "source": "mouth", "device": "null", "session": "s-doc", "agent": "carol", "final": true, "utt": "8fa303b0e002", "voice": "af_sky", "backend": "silence", "requested_ts": "2030-12-08T08:00:00.446+11:00", "mail_id": "<1922907600499517000.8fa303b0e002@host.mouth>", "volume": 100, "volume_from": "builtin", "played_s": 0.733, "dur_s": 0.733, "cut": false, "text_played_est": "Hello Alice.", "underrun_s": 0.0, "underruns": 0, "device_gaps": 0, "device_gap_s": 0.0, "device_gap_max_s": 0.0, "reason": "done", "v": 1}
```

<!-- markdownlint-enable MD013 -->

The example lines are real. They came from one scratch run at `138484c`
into a temporary folder: the capture (`capture()`) on a synthetic tone with
a fake speech-to-text engine, then `mouth say` on the `silence` backend and
the `null` device. No microphone or speaker was used. The `heartbeat`
line is older than the beat's `pid` field, so it has none. A
beat written now carries one.

### Events

Each event is an `event` line with these fields, besides the common ones.
`stream_s`, `t0`, `t1` and `at` are seconds into the capture's audio.

The capture writes these (`src/ears/listen/loop.py`):

- `listen-started` (`loop.py:716`): `detector`, `stt`, `partials`,
  `heartbeat`, `period`, `ceiling`, `pid`, `missed_reply_s` when a
  missed-reply window is given, and the recording switch as it stood at the
  start: `record` (true or false) and `record_from` (`cli`, `env`, `file` or
  `default`), with `record_invalid: true` when that layer's value was not
  valid (so it is off). The file layer is followed live, so a later clip
  can see another value ([ears.md](ears.md#recording)).
- `heartbeat` (`loop.py:786`), every `period` seconds: `period`,
  `stream_s`, `pid` (the capture's process id, as on `listen-started`: the
  keeper tells its room capture's beats from a call leg's by it), and the
  input's level over the period: `peak`, `rms`,
  `rms_dbfs`. Under silero it also carries `speech_prob_max`. `peak: 0`
  is a dead microphone.
  On a real-time source it adds `audio_ts`, `lag_s` and `stt_backlog_s`,
  and on the mic `input_overflows`.
- `vad` (`loop.py:739`): a voice onset (`speech: true`) or offset
  (`speech: false`) at `at`, with `stream_s` and `by` (the detector), and
  `audio_ts` and `lag_s` on a real-time source. The mouth ducks on an
  onset.
- `capture-wedged` (`src/ears/listen/watchdog.py`): the capture's watchdog
  ended a capture that was stuck, and the process exited 3 right after.
  `why` is `wedge` (no audio frame for `WEDGE_S`, 90 s), `stop-ignored` (a
  STOP or SIGTERM not honoured within `STOP_GRACE_S`, 10 s) or `exit` (the
  capture had finished, but the process was still alive `STOP_GRACE_S`
  later). `waited_s` is how long that took and `stalled_s` how long since
  the last frame. `stream_s`, `pid` and `exit` (3) are also given. No
  `listen-stopped` row follows, except after `exit`, where it came first.
  One more exception: killing the streaming child can free a write the
  capture was stuck in, and the capture may then write `listen-stopped`
  just after this row, before the exit.
  The row is best effort: it is skipped when another writer holds
  `heard.lock`, and dropped if it is not written within 1 s. Either way
  the capture log (`ears-keeper-capture.log`) has the watchdog's line and
  every thread's stack.
- `listen-stopped` (`loop.py:818`): `reason` (`eof`, `stop`, `ceiling` or
  `error`), `stream_s`, `wall_s`, `turns`, and `error` when there was one.
- `turn-discarded` (`loop.py:418`): speech ended, but no text was
  recognised in it. `why`, `t0`, `t1`, and `audio_ts` and `lag_s` on a
  real-time source.
- `stt-error` (`loop.py:630`): one chunk's speech to text failed. `detail`,
  `t0`, `t1`. Several in a row stop the capture. `detail: "timeout"`, with
  `wait_s`, means the chunk had not answered after `STT_WAIT_S` (10 s). The
  capture stops waiting for it and runs the chunks queued behind it on a
  fresh worker. A timeout counts toward the failures in a row that stop the
  capture.
- `partials` (`loop.py:645`): the streaming partials engine changed.
  `detail`, `stream_s`.
- `partials-error` (`loop.py:657`): the streaming engine failed. `detail`,
  `stream_s`. `detail` is `nemotron not reading its stdin: N frames
  dropped` when the child took no audio for 10 s (`LIVE_FAIL_S`,
  `src/ears/backends/proc.py`). The capture then goes back to chunk
  partials, and the child is killed.
- `speaker-split-error` (`loop.py:450`): the speaker labels could not be
  read. `detail`, written once.
- `recogniser-lag` (`src/ears/lag.py`, `LagAlarm`): a partial's `lag_s`,
  or a heartbeat's `lag_s` or `stt_backlog_s`, went over the threshold
  (`LAG_WARN_S`, 2 s; `$EARS_LAG_WARN_S`). Written once per
  episode, never per record, and only on a real-time source. `lag_s` (the
  reading that raised it), `stt_backlog_s` (the newest heartbeat's, once
  there is one), `threshold_s`, `since` (when it was raised), `on`
  (`partial` or `heartbeat`) and `text`. A turn's `lag_s` never raises it.
  The keeper mails it (see `ears-keeper.log` below).
- `recogniser-lag-ok`: the episode is over. The newest partial and the
  newest heartbeat are both 0.5 s under the threshold. A heartbeat that
  finds no chunk waiting on the speech to text drops an old late partial
  from that test. Under a streaming engine it cannot see the engine's own
  queue, so there the episode ends on the user's next word in time. `lag_s`,
  `max_lag_s`, `threshold_s`, `since`, `lasted_s`, `on`, `text`.
- `missed-reply` (`src/ears/listen/missed.py:340-353`): a mouth line ended
  and no turn came, so the window's audio was kept. `file`, `utt`,
  `said_seq`, `window_s`, `t0`, `t1`, `audio_s`, `lag_s`, `speech_prob_max`
  and `pruned`. `file` is the clip, `said_seq` the seq of the line's `said`
  row, `t0`..`t1` and `audio_s` the clip's span, and `pruned` the expired
  clips removed as it was written.
- `missed-reply-off` (`missed.py:274`): no clip will be written. Either
  the clip folder was refused as a synced location (`why: synced location:
  ...`, `dir`), or the recording switch is off (`why: recording off`,
  `record_from`, and `record_invalid` when its value was not valid). The
  second is written once each time the switch turns off, at the start
  included, never once per missed reply.
- `missed-reply-error` (`loop.py:366`): the clip path failed. `detail`,
  written once.
- `turn-audio-error` (`loop.py`, `_kept_do`): a turn's kept audio could
  not be written, or its prune failed. `detail`, written once per capture.
  The turn still lands, without `audio`.

The keeper writes these through `heard.py write event`, with `source` left
as the default, `mic`:

- `device-change` (`keeper/ears-keeper.sh:1184`): the inputs changed.
  `device` is the input it now follows, and `text` reads
  `added: ...; removed: ...`.
- `room-off` (`ears-keeper.sh:1206`): a call went live, and the room's
  microphone is off until it ends. `device` is the call, for example
  `call:3`, plus `text`.
- `room-on` (`ears-keeper.sh:1211`): the call ended and the room's
  microphone is back. `device`, `text`.
- `ears-deaf` (`ears-keeper.sh:499`, `507`): a capture looks alive
  but hears nothing. `device` is the input it is on (`none` if unknown), and
  `text` reads `CHANNEL: CAUSE: DETAIL`, for example
  `room: model: /path/silero_vad.onnx`. `CHANNEL` is `room` (the keeper's
  capture) or `speakers` (the sidecar). One is written when an episode
  opens, and one more for each new cause during it. A cause already open
  writes nothing again. The keeper's causes today: `model` (no Silero
  model), `beat` (the room capture's heartbeat stopped: `text` reads
  `room: beat: since HH:MM`), `stt` (the room capture stopped twice in
  a row because its speech to text failed: `text` reads `room: stt: 500`,
  the HTTP status from the error, or `?`), `zeros` (4 of the room
  capture's heartbeats in a row had `peak` 0: `text` reads
  `room: zeros: 4 beats of peak 0`) and `lid` (the target is the built-in
  mic and the lid is shut: `text` reads `room: lid: shut`, and `device` is
  the built-in). The `speakers` channel's causes are `cover` (20 of the
  side-car's mic rows in a row had 0% of their line covered: `text` reads
  `speakers: cover: 20`) and `speech` (6 of its covered mic turns in a row
  had 2 or more words heard by the capture and no speech found by the
  side-car: `text` reads `speakers: speech: 6`). They are muted while the
  room is DEAF or no room capture runs.
- `ears-hearing` (`ears-keeper.sh:521`): the episode's last cause
  cleared. `device`, and `text` reads
  `CHANNEL: hearing again, was CAUSE for DURATION`, for example
  `room: hearing again, was model for 4h40m`.

The mouth writes these, with `source: "mouth"` and the line's or the
music's `utt`:

- `duck` (`src/mouth/bed.py:384`): a music bed was lowered. `level`, `why`,
  `causes`, `by`.
- `unduck` (`bed.py:400`): the bed came back up. `ducked_s`, `causes`,
  `by`.
- `duck-skipped`: a dip the duck setting stopped. With `lane: "bed"`
  (`bed.py:372`) it carries `why`, `causes` and `by`. With `lane: "dj"`
  (`src/mouth/player.py:90`) it carries `why` and `by`.

`src/ears/heard.py:56-66` also names `device-changed`, `wake-word`,
`end-word`, `barge-in`, `call-started` and `call-ended`. Nothing in this
repo writes them at `138484c`, but readers look for them. `heard_wait`
ends on `end-word` (`src/ears/heard_wait.py:207`). The talk page shows
`call-started` and `call-ended` (`src/talk/server.py:44`). The ride-along
hook shows those six events only, and passes over every other event
(`heard.py:548`). So the hook shows `device-changed`, but not the keeper's
`device-change`. The talk page also lists and renders `battery-low`
(`src/talk/server.py:44`, `src/talk/index.html:169`), which nothing in this
repo writes either.

## The log files

Paths are master's. Every log below is in `~/.sessionkit/voice/log/` unless
its entry says otherwise. `EARS_LOG_DIR` moves that folder, for `ears services`,
the keeper, the mouth's player and the sound agents alike. Nothing copies,
moves or deletes the old files in `~/Library/Logs/`; `ears services status`
prints the folder, and one line pointing at it while the old folders still hold
a file.

- **`heard_YYYY-MM-DD.jsonl`** in `<home>/logs/conversations/`
  ([sessionvoice's home](#sessionvoices-home); `~/.voicemode` in 0.1.0). Writer: `src/ears/heard.py:87-89`, `214-217`. It shows the heard log
  above. The same in 0.1.0.
- **`install.log`** in `~/.sessionkit/voice/log/` (it was `<home>/logs/install.log`
  before). Writer: `ears services
  install` (`src/ears/services.py`, `install_log_path`). Every real run
  appends to it: a `=== <time> ears services install <services>` header, one
  line for each line the run printed (`<time> stdout ...` or `<time> stderr
  ...`; a line the terminal redraws, the weights' progress, once, as it
  ended), one `<time> launchctl <args> -> rc N: <stderr>` line for each
  launchctl call that changes a job (bootstrap, bootout; the read-only
  `print` and `list` are not logged), and a `=== <time> rc N` footer. The
  time is local, ISO 8601, to the second. The run prints the path as its
  `log` row. A dry run, and a refusal before anything is written, write no
  log. Nothing rotates or trims it. The log never stops an install: when it
  cannot be made or written, install prints one `WARNING` line naming it, and
  goes on unlogged with no `log` row; the exit code is the services' alone.
- **`speakers_YYYY-MM-DD.jsonl`**, beside the heard log. Writer: the
  speakers side-car, `speakers/speaker-sidecar2.py:363` (its installed copy
  is `src/ears/_speakers/`). One line per heard line that names who spoke:
  `seq`, `kind`, `speaker` (a name, `other`, `mixed`, `silent` or
  `unheard`), `fracs`, `speech_s`, `t0`, `t1`, `by`, `at` and `v: 2`.
  `cover` is the share of the line's window that its device's audio
  covered, a number from 0 to 1 rounded to 2 places. It is present whenever
  that share is known, and absent on a row with no window. An `unheard` row
  also carries a `why`. Both fields were added within `v: 2`. A line heard
  inside a playing mouth line, with at least half its words that line's,
  carries the line's `mouth_utt`; when that renamed its label to the line's
  agent (it was `other`, or an agent's reference), it also carries
  `speaker_by: "mouth"` (within `v: 2`).
  It is read by the ride-along (`src/ears/heard.py:596`), by
  `--speaker-split`, and by the keeper, which counts the mic rows (no
  `leg`) with 0% of their line covered, and the covered mic turns whose
  `why` starts `words in the line, no speech found` (see `ears-deaf` above).
  No field was added for that: the keeper reads `kind`, `text`, `why` and
  `mouth_utt`.
  The keeper reads a row's `cover` field first: a number, and 0 or less
  is 0% covered. A row without it, from an older side-car that does not
  write it, is read by its `why`: one ending `(0% of the line covered)` counts, and
  one with a higher share, or with no `why` at all, is covered. Any other
  row is passed over. The file is written only where the speakers
  service runs.
  `ears services install` loads it only when asked (`--only`).
- **`ears-keeper.log`** in `~/.sessionkit/voice/log/`. Writer: the keeper's
  launch agent, as stdout and stderr (`src/ears/services.py:717`), or the
  hand template (`launchd/dev.sessionkit.voice.ears-keeper.plist.in:54`). It shows
  one `HH:MM:SS` line per keeper event (`keeper/ears-keeper.sh:292`): an
  input change, a capture start or restart, a call live or ended, the
  missed-reply window, a recogniser that fell behind or caught up, and
  every notice it mailed or could not mail. The keeper reads the heard
  log's new lines each loop and mails a `recogniser-lag` as
  `ears: recogniser N s behind on DEVICE at HH:MM EOM`, at most once every
  `EARS_LAG_MAIL_GAP` seconds (default 600). The ones it holds back are
  noted here. `EARS_HEARD_DIR` moves the log it reads. A capture that is
  DEAF is one `DEAF (CHANNEL): CAUSE` line here, one
  `ears-deaf` event and one mail, then one `hearing again` line, event and
  mail when it ends. While the room is DEAF, the `capture restarted` and
  `restart FAILED` notices are noted here and not mailed. The keeper also
  judges its room capture by its heartbeats (`--heartbeat 15`): none for
  `EARS_BEAT_STALE` seconds (120) is DEAF, cause `beat`. None for
  `EARS_BEAT_KILL` seconds (300) is a wedge, and the capture is stopped
  hard. Every stop of the room capture escalates and notes each step:
  its STOP file, then SIGTERM after `EARS_STOP_WAIT` seconds (15), then
  SIGKILL after `EARS_TERM_WAIT` more (5). At start the keeper adopts a
  running room capture only if its last heartbeat in the heard log is
  within `EARS_BEAT_KILL`. Otherwise it notes `not adopting wedged
  capture`, sends the one DEAF mail and stops that capture hard. A keeper
  loop that `ears-keeper.pid` (in the keeper's run dir) names is ended
  first, so two loops never run at once. A room capture that stops because
  its speech to text failed (`listen-stopped`, `error` carrying
  `STT failed`) is noted as `capture stopped: STT failing (CODE), N in a
  row`. `EARS_STT_DEAF` such stops in a row (2), with no partial or turn
  between them, is DEAF, cause `stt`, and the room capture's next partial
  or turn with text ends it. A room capture that ends by itself (any
  reason but `stop`) and heard nothing is started again only after a wait,
  noted once as `next start in Ns`: 30 s, doubling to 300 s
  (`EARS_BACKOFF_MIN`, `EARS_BACKOFF_MAX`). One that heard is started again
  at once, and the wait goes back to 30 s. So is the first capture on a new
  input: a FOLLOW move drops the wait, noted as `input is now DEVICE: the
  restart wait (Ns) is dropped`. A wait belongs to the input it was armed
  on, and it never holds a start on another input, whichever the keeper
  reads first: the old capture's stop row or the move. `EARS_ZERO_BEATS` (4) of the room capture's
  heartbeats in a row with `peak` 0 is DEAF, cause `zeros`, and the keeper
  reopens that capture once (`reopening it, once`): a stop, and a fresh
  start that no wait holds. If the fresh capture gives only zeros too, the
  keeper holds (`still only zeros after the reopen: holding`) and restarts
  nothing more. A heartbeat with `peak` above 0 ends it. When the target is
  the built-in mic (the input whose `transport` is `builtin` in
  `devices.json`; of two such inputs, such as a headset on the jack, the
  one named like `MacBook Air Microphone`; while that file is stale, the
  name alone), the keeper reads the lid at most every `EARS_LID_EVERY` s
  (5) and notes each change as `lid: open -> closed`. Lid shut is DEAF,
  cause `lid`: the keeper stops the room capture (`lid shut: stopping the
  room capture, starting none`; one on the built-in at once, one on the
  input the Mac left once the move has held `EARS_STABLE` loops), starts
  none, and holds; it does not go
  looking for another input. While that lasts, an input change is noted as
  `not mailed, the room is DEAF (lid): ears: mic -> DEVICE`. The first
  heartbeat with sound once the hold is over ends it.
- **`ears-keeper-capture.log`** in the keeper's `LOGDIR`
  (`keeper/ears-keeper.sh:96`). Writer: the room capture's stdout and
  stderr (`ears-keeper.sh:982`). It shows the capture's start-up and crash
  output, and the stack of every thread each time the capture is sent
  SIGTERM or SIGUSR1: a wedged capture's dump names the call it was stuck
  in. When the capture's watchdog ends a stuck capture, its
  `ears capture watchdog: WHY (...)` line and every thread's stack are here
  too, and so is `ears capture: input device ... did not close in 2s` when
  a mic's close did not return. What it heard goes to the heard log, not here.
- **`ears-keeper-calls.log`** in the keeper's `LOGDIR`. Writer: `call-ears`,
  as stdout (`ears-keeper.sh:404`). It shows one `HH:MM:SS` line each
  time a call's ear goes up or down (`keeper/call-ears:50-51`, `114-142`).
- **`capture-N.log`**, one per call, in
  `${XDG_STATE_HOME:-~/.local/state}/ears/call-ears/`, or `CALL_EARS_DIR`.
  Writer: `keeper/call-ears:108`, with the folder set at `call-ears:28`. It
  shows the call ear's capture output for call `N`.
- **`ears-mail.log`** in `~/.sessionkit/voice/log/`. Writer: the turn-mail
  launch agent, as stdout and stderr (`src/ears/services.py:717`, or
  `launchd/dev.sessionkit.voice.ears-mail.plist.in:84`). It shows a start banner,
  one JSON line per mail sent, and one `{"skip": WHY, "seq": N}` line per
  turn it did not mail (`skip()` in `keeper/ears-mail.py`). `--to device`
  skips a call turn for one of two reasons: `{"skip": "call", "seq",
  "source"}` when `--calls off` is set (or the turn's source is another
  non-mic one, with no `call:N` device), and `{"skip": "call-bot", "seq",
  "call": N}` when no `call-ears.sock` leg names call `N`'s bot (a hung-up
  call the mailer never saw live, a web leg, a leg with no agent), with
  nothing mailed. A mailed call turn is an ordinary sent line whose `to` is
  `calls+<bot>-<N>` and whose `rule` is null. A say-again
  adds one line on stderr: `{"again": "replayed", "seq", "seat",
  "utt", "mouth"}` when `mouth again` exited 0 (`mouth` is what it
  printed), `{"again": "dry run", ..., "cmd"}` under `--dry-run`, or the
  skip line `{"skip": "again-failed", "seq", "seat", "utt", "fallback":
  "call", "cause", ...}` when the mouth failed, timed out (`cause`
  `timeout`, with `timeout_s`), would not start (`not run`) or had nothing
  to replay (`exit 3`, with its `err`). That turn's
  mail then carries `call` instead of `again`.
- **`ears-mail.fire.jsonl`** in `~/.sessionkit/voice/log/`. Writer:
  `deliver()` in `keeper/ears-mail.py`, given as `--fire-log`
  (`spec("mail")` in `src/ears/services.py`). It shows one line per mail
  sent: `at`, `seq`, `to`, `rule`, `subject`, `exit`, `id`, and `err` if it
  failed. A call-box mail is a row To `calls+<bot>-<N>` with a null `rule`. A
  `rule` entry `<seat>@<host>=again` is a say-again the mouth replayed:
  it woke nobody. One the mouth could not replay is written `=call` instead.
- **`ears-mouth.log`** in `~/.sessionkit/voice/log/`. Writer: the `mouth mail`
  launch agent, as stdout and stderr (`src/ears/services.py:717`). The
  player runs inside it and prints its records there: `kind: barge`
  decisions and duck records (`src/mouth/player.py:259-266`), and one line
  per drop notice (`src/mouth/notice.py:542`).
- **`player.log`** and **`split.log`** in `~/.sessionkit/voice/log/` (they were
  in the mouth's folder, `$MOUTH_DIR`, by default `<home>/mouth/`, and its
  `splits/`; `~/.voicemode/mouth/` in 0.1.0). The mouth's other records, locks
  and speed stay in the mouth's folder. `player.log`'s writer: a player that
  `mouth say` started because none was running (`src/mouth/__init__.py:72`).
  It shows the same records as `ears-mouth.log`.
- **`ears-stt.log`** and **`ears-speakers.log`** in `~/.sessionkit/voice/log/`.
  Writer: the opt-in `stt` and `speakers` launch agents
  (`src/ears/services.py:717`). The side-car started by hand logs to
  `speakers.log` in its home (`speakers/run.sh:78`).
- **`ears-speech.log`** in `~/.sessionkit/voice/log/`, and mlx-audio's own logs
  in `~/.sessionkit/voice/log/mlx-audio/` (its `--log-dir`,
  `src/ears/services.py:712`). Writer: the speech server's launch agent,
  `dev.sessionkit.voice.mlx-audio`, only when `ears services install` installed
  it. A server sessionvoice found already running logs where its own
  installer put it.
- **`ears-sounds.jsonl`** and **`mouth-sounds.jsonl`** in
  `~/.sessionkit/voice/log/`, one line per sound played. Writers:
  `keeper/ears-sounds.py:80` and `keeper/mouth-sounds.py:74`. Their agents'
  own output goes to `ears-sounds.log` and `mouth-sounds.log`, and the talk
  page's to `talk.log`. All three are hand templates in `launchd/`, not
  installed by `ears services`.
- **The mouth's mail log**, only when `$MOUTH_MAIL_LOG` is set:
  `queued`, `saying` and `said` for each line, as a mail thread in the
  mouth's box (`src/mouth/maillog.py`).

## What 0.1.0 does differently

- **Two log folders.** In 0.1.0 `ears services` put its agents' logs
  (`ears-keeper.log`, `ears-mouth.log`) in `~/Library/Logs/ears`. The keeper
  defaulted its own `LOGDIR` to `~/Library/Logs/failmode`, and `ears
  services` did not pass `EARS_LOG_DIR` to it. So `ears-keeper-capture.log`
  and `ears-keeper-calls.log` landed in `~/Library/Logs/failmode`. Master
  defaults both to `~/.sessionkit/voice/log` (`keeper/ears-keeper.sh`) and
  passes `EARS_LOG_DIR` to the keeper (`src/ears/services.py:692`).
- **The turn mail and the sounds.** 0.1.0's `ears services` had no `mail`
  or `speakers` agent. The hand templates wrote `ears-mail.log`,
  `ears-mail.fire.jsonl`, `ears-speakers.log`, `ears-sounds.jsonl` and
  `mouth-sounds.jsonl` to `~/Library/Logs/failmode`. At master all of them
  are in `~/.sessionkit/voice/log`. The exceptions are the two hand jobs,
  a hand-made launch job for the speakers and `dev.voicemode.ears-sounds`:
  their logs stay in an older log folder until each is given its own GO.
- **The move out of `~/Library/Logs/`.** Master writes every log to
  `~/.sessionkit/voice/log/`. The old files in `~/Library/Logs/ears` and in
  an older log folder are left in place, never deleted; read them there
  for anything written before the move.
- **The call ears' folder** was `~/.<agent>/call-ears`, under the agent's
  own dot-folder. It is now
  `${XDG_STATE_HOME:-~/.local/state}/ears/call-ears`.
- **The heard log** has the same path and the same five kinds. These
  fields and events are new since 0.1.0, so a 0.1.0 log never has them:
  - the `vad`, `missed-reply`, `missed-reply-off`, `missed-reply-error` and
    `duck-skipped` events;
  - `speech_prob_max` and `pid` on `heartbeat`, and `missed_reply_s`,
    `record`, `record_from` and `record_invalid` on `listen-started`;
  - `audio_ts` and `lag_s` on `partial`, `turn`, `turn-discarded`, `vad`
    and `heartbeat`, and `stt_backlog_s` and `input_overflows` on
    `heartbeat`;
  - `by` on `duck` and `unduck`;
  - on `saying`: `lead_s`, `lead_from`, `held_s` and `level`;
  - on `said`: `device_gaps`, `device_gap_s`, `device_gap_max_s`,
    `level`, `cut_by`, `cut_how`, `barge_words`, `barge_words_from`,
    `resume`, `resume_utt`, `resume_s` and `resume_why`.

## Related

- [The ears][ears]: the capture, the keeper and the waiter that write and
  read the heard log.
- [The mouth][mouth]: the player behind `saying`, `said` and the duck rows.
- [Troubleshooting][troubleshooting]: reading a `said` row's chop counts.
- [events.md][events]: the engine's stdout events, which are a different
  stream from the heard log.

[troubleshooting]: troubleshooting.md
[ears]: ears.md
[mouth]: mouth.md
[events]: events.md
