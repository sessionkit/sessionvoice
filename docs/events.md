# ears events, v1

One stream format for every stage of ears. The STT backends, the VAD, the turn
detector and the previews all read and write it. A consumer never needs to know
which backend produced it: Apple on the Mac, Nemotron anywhere MLX or NeMo runs,
and whisper chunks on anything.

**Status:** v1, agreed on 2030-06-09. `turn` and `preview` belong to the turn
layer, which set their fields.

## Wire

- JSON Lines: one UTF-8 object per line, flushed per line.
- In Python it is `ears.Event`. `Event.to_json()` and `Event.from_json()` are the
  wire.
- Every object has `type`. Everything else depends on the type.
- **Forward-compatible:** ignore unknown fields and unknown types; never fail on
  them.
- One stream carries everything, so the bot reads a single pipe.
- **The turn layer runs in-process, not behind the pipe.** Smart Turn reads up to
  the last 8 s of PCM, and a re-decode reads the whole turn's audio. So the turn
  stage sits inside `ears listen --turns smart|silence`, and it sees every PCM
  block the backend sees ("Turn stages" below).

## Clocks

- **Audio time** (`start`, `end`, `at`, `audio_s`): seconds of audio since the
  stream began, which is samples fed divided by rate. Every layer judges on this
  clock, so a replayed call behaves exactly like a live one.
- **Wall time** (`t`): seconds since the stream began, taken when this stream
  emitted the event. The pipeline restamps it: a child's own clock started
  somewhere else. Latency is `t - end`, and for `vad` it is `t - at`.

## Types

The backend emits `ready`, `partial`, `final`, `error` and `eof`. The VAD emits
`vad`. The turn layer emits `turn` and `preview`.

### ready

The first line, always:

```
{"type": "ready", "v": 1, "backend": "apple", "model": "SpeechTranscriber",
 "locale": "en-AU", "rate": 16000, "load_s": 0.41}
```

### partial

This is the unfinished hypothesis for segment `seg`.

**Rule: replace, never append.** `text` is the whole of the segment so far, and a
newer partial for the same `seg` replaces the older one.

- Backends that produce deltas, as Nemotron does, accumulate them before
  emitting. The last word of a partial can still be growing ("Ameri", then
  "Americans"), and the next partial replaces it.
- Partials may be sparse. Apple's arrive about 1 s apart, so never use them to
  tell whether the speaker is still talking. Use `vad` for that.

```
{"type": "partial", "seg": 3, "text": "so the thing about", "start": 12.4, "end": 13.5, "t": 13.9}
```

### final

Segment `seg` is done, and its `text` never changes afterwards.

- `seg` strictly increases.
- A final may have empty text (noise). Consumers skip it.
- `words` is `[[word, start, end], ...]`, or `[]` when the backend has no word
  timings (whisper).

```
{"type": "final", "seg": 3, "text": "So the thing about the bridge is",
 "start": 12.4, "end": 14.1, "words": [["So", 12.4, 12.6], ...], "t": 14.9}
```

### vad

Emitted on edges only: speech began (`speech: true`) or speech ended
(`speech: false`).

- `at` is the audio time of the edge. For an offset, that is the end of the last
  voiced frame, not the moment the silence was confirmed.
- `p` is optional: the detector's probability at the edge.
- `by` names the detector: `silero`, `energy` or `apple`.

```
{"type": "vad", "speech": false, "at": 14.08, "by": "silero", "t": 14.6}
```

### turn (turn layer)

The speaker has finished; reply now. Only a `turn` means that. A `preview` never does.

- `reason` is one of:
  - `smart-turn`: the model judged the turn complete.
  - `silence`: the fallback timer fired.
  - `eof`: the stream ended.
  - `manual`: a button or a command.
- `text` is the best text for the whole turn. `by` says where it came from:
  - `stt`: the joined finals, plus the open partial if one was never finalised.
  - `redecode`: a full-context model such as Parakeet re-read the turn audio.
- Optional tuning fields:
  - `p`: the Smart Turn probability at the decision.
  - `wait_s`: the silence waited before firing.
  - `partial_tail: true`: the last phrase is volatile text, because its final
    did not arrive within the commit wait.
- **No retract event in v1.** If `vad speech:true` arrives within TURN_JOIN (1.6 s)
  after a `turn`, the bot joins the two, as it does today.

```
{"type": "turn", "turn": 4, "text": "...", "start": 12.4, "end": 19.8,
 "reason": "smart-turn", "by": "redecode", "t": 20.3}
```

### preview (turn layer)

The turn so far, sent while the speaker is still talking, so the agent can think, search
and read ahead of time.

- `text` is the whole turn so far, and the latest preview wins.
- `supersedes` is the `seq` of the preview this one replaces.
- `by` is `stt` or `redecode`, as for `turn`. `start` and `end` are audio times
  of the text covered, so preview latency is measured the same way as a turn's.
- The `turn` event supersedes the turn's last preview.

```
{"type": "preview", "turn": 4, "seq": 2, "text": "...", "supersedes": 1, "t": 17.0}
```

### error, eof

```
{"type": "error", "message": "helper exited 5", "fatal": true}
{"type": "eof", "audio_s": 121.3, "t": 122.2}
```

`eof` is the last line. After a fatal error, `eof` follows.

## Commit: ending a segment on demand

When the turn stage decides the speaker has finished, it calls the backend's `commit()`:
finalise everything fed so far, now. The final for the open segment follows.

- **Apple needs this.** It holds the last phrase before a silence open until the
  speaker talks again. Measured on 2030-06-09: 4 s of quiet and no final. Without a
  commit, a turn built from finals loses its last phrase to the next turn.
- **Commit only at a decision, never at every pause.** Finalising at every pause
  loses the words spoken just after the finalise point (a streaming eval:
  13.4% WER against 0.67%).
- **Apple starts a fresh analyzer at each commit.** In a bot replay, `finalize()`
  on the live analyzer damaged the whole next turn: 18 s of dots and dropped
  words. So the helper finishes the old analyzer and sends all later audio to a
  new one. It relays the old analyzer's finals first, so `seg` stays in order,
  and the audio timeline carries on unbroken.
- **Mechanism:**
  - Apple helper and the Nemotron child: `SIGUSR1`.
  - whisper: sends the open chunk now.
  - In Python: `backend.commit()`.
- The silence stage waits up to 0.6 s for the committed final. Then it closes
  the turn on the partial's text (`partial_tail: true`).

## Turn stages

A turn stage is in-process and implements four calls. The pipeline drives them
in stream order:

| call                           | when                                  |
|--------------------------------|---------------------------------------|
| `pcm(pcm_bytes, audio_end)`    | every block; the backend's own bytes  |
| `event(e) -> [Event]`          | every backend and `vad` event         |
| `tick(audio_now) -> [Event]`   | after every 20 ms block               |
| `flush(audio_now) -> [Event]`  | end of stream                         |

- It is constructed with `previews=` and `commit=` (the backend's `commit`).
- It returns `turn` and `preview` events; the pipeline stamps `t`.
- `silence` is `ears.turns.SilenceTurns`, the fallback. `smart` is
  `ears.smartturn.TurnDetector`.

## One VAD

The pipeline runs the only VAD (Silero by default, `--vad`). Its edges go to the
stream, to the turn stage, and to any backend with `on_vad()`. Nemotron uses them
to open and close segments; it receives them over a control pipe, fd N, passed
as `--control-fd N`. Run on its own, the Nemotron child uses its own VAD.

## Not in this stream

This stream is what `ears listen` writes. The capture's heard log has
lines and events of its own, the recording switch's fields among them
(`record` and `record_from` on `listen-started`, and `missed-reply-off`):
see [logs.md](logs.md#events) and [ears.md](ears.md#recording). While
recording is on, a heard `turn` line also carries `audio` (the path of the
turn's kept WAV), `audio_s` (its length in seconds) and, for a turn cut at
120 s, `audio_truncated: true`; a `turn-audio-error` event says a file could
not be written ([logs.md](logs.md#turn-a-finished-turn),
[ears.md](ears.md#kept-turn-audio)). This stream's own `turn` carries no
audio field.
With `--turn-text redecode`, a heard `turn` line says where its text came
from (`text_from`, `joined`, `redecode`) and, when the re-decode had lost the
turn's first words and the joined partials put them back, `head: "spliced"`
and `head_words` ([logs.md](logs.md#turn-a-finished-turn)).

## Commands

- `ears listen [--backend apple|nemotron|whisper|auto] [--rate 16000]
  [--vad silero|energy|none]`: s16le mono PCM on stdin, events on stdout.
  A client (the Delta bot, for one) spawns this, or imports `ears` directly.
- `ears file WAV [--realtime]`: the same events from a file. `--realtime` paces
  the feed like a microphone.
- `apple-speech stream --rate HZ`: the Mac helper. `ears listen --backend apple`
  runs it. It emits `ready`, `partial`, `final` and `eof` directly in this format.
- `python -m ears.backends.nemotron --rate HZ [--control-fd N]`: the Nemotron
  child, with the same wire. It runs anywhere MLX does. NeMo on Linux would be a
  third child on this same wire.

`--backend auto` means Apple on macOS 27 with the Speech assets installed, then
Nemotron when MLX is importable, then whisper at `$EARS_WHISPER_URL`.
