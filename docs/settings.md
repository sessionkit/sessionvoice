# Settings

sessionvoice's user-level settings: every key of its three JSON settings
files, with its type, its default, what it does and which file it lives in.
Each file has a JSON schema in `settings/`, and this page follows it. Where
the code does something the schema does not say, this page says so.

- **The files:** `ears.json`, `ears-sounds.json` and `mouth.json`, all in
  `~/.sessionkit/voice/settings/`
  ([which file](#the-files)).
- **The layers, today:** the mouth reads some keys from the agent, seat and
  session layers as well as its file. The ears and the sounds read only
  their own file ([the layers](#how-far-each-part-follows-the-layers)).
- **The keys:** [ears.json](#earsjson), [ears-sounds.json](#ears-soundsjson),
  [mouth.json](#mouthjson).
- **Where the schema and the code differ:**
  [the differences](#where-the-schema-and-the-code-differ).

How the session family's settings work in general, its layers and
`_locked`: the kit's settings guide, [sessionkit's docs/settings.md][kit].
Which files and variables each part of sessionvoice reads:
[configuration.md][configuration].

## The files

| File | Schema | Read by | When a change applies |
| --- | --- | --- | --- |
| `~/.sessionkit/voice/settings/ears.json`, or `$EARS_CONF` | `settings/ears.schema.json` | the keeper, every capture, and the mouth (for `partials`) | at once: the keeper reads it every loop, and a capture follows it |
| `~/.sessionkit/voice/settings/ears-sounds.json`, or `$EARS_SOUNDS_SETTINGS` | `settings/ears-sounds.schema.json` | `keeper/ears-sounds.py` | when the file changes |
| `~/.sessionkit/voice/settings/mouth.json`, or `$MOUTH_SETTINGS` | `settings/mouth.schema.json` | the mouth | on the next line |

- **JSON, one object.** A missing file means every key takes its default.
  A file that will not parse is never repaired: a running capture and
  `ears-sounds` keep the last copy they read, and the mouth and the
  recording switch read it as empty.
- **The schema copies.** `ears services install` copies each schema beside
  its file as a dot file: `.ears.schema.json` and `.ears-sounds.schema.json`
  in `~/.sessionkit/voice/settings/`, and `.mouth.schema.json` beside the
  mouth's file (the same folder, unless `$MOUTH_SETTINGS` moves it).
  A settings screen can be drawn from them. Do not edit the copies.
- **Notes for people.** In `ears.json` and `ears-sounds.json` a key that
  starts with `_` (such as `_about`) is a note, and the tools never read
  it. The schema refuses any other key; the tools ignore one.
- **The mouth's old file.** Until `~/.sessionkit/voice/settings/mouth.json` exists, the
  mouth reads `<home>/mouth/settings.json` instead, where `<home>` is
  [sessionvoice's home][home].
- **No file is installed.** The copies in `keeper/defaults/` are examples
  of the shape, and nothing copies them for you.

### Upgrading from 0.1.0

Version 0.1.0 kept these files in two other folders, by file:
`~/.settings/ears/` (`ears.json`, `wake.json`, `ears-sounds.json`, `stt.key`) and `~/.settings/mouth/` (`mouth.json`, `mouth-sounds.json`, `tts.key`).
Nothing reads them now, and nothing falls back to them. A host step moves
the files into `~/.sessionkit/voice/settings/`. Until it has run, a file
that is missing from the new folder while its 0.1.0 copy is still there is
*stranded*, and the `ears`, `mouth` and `talk` commands say so on stderr,
once per run, in one line naming both paths:

    <new file> is missing and <old file> still exists, so nothing is read
    from it and nothing is written: the defaults apply until the host step
    that moves the 0.1.0 settings into the new folder has run.

- **Nothing is read from the old file.** Only its existence is checked.
  The tools use their defaults, as for a missing file.
- **Nothing is written.** `ears recording on|off`, `ears wake add` and
  `ears services install` write no stranded file, and no schema copy
  beside one, so the move finds nothing in its way. Run the host step,
  then run the command again.
- **The line is the only change.** The scripts the launch agents run
  (`keeper/`) say nothing; `ears services status` shows the line.
- A file named by its variable (`$EARS_CONF`, `$EARS_WAKE`,
  `$MOUTH_SETTINGS`) is written there as before.

## How far each part follows the layers

The family's settings stack in layers: template, host, agent, seat and
session (the kit's guide,
[sessionkit's docs/settings.md#the-cascade][kit-cascade]). sessionvoice
follows them only in part today.

### The ears read one file

The ears, the keeper and the sounds read their own file and nothing else:
no template, no `ears.d/` folder, and no agent, seat or session layer.

- **The environment wins over a key**, one variable per key, as each key
  below says. A capture's own flag (`--record`, `--no-record`) wins over
  both.
- **`EARS_CONF`** names another file in place of
  `~/.sessionkit/voice/settings/ears.json`.

### The mouth reads some keys from the layers

For these keys the mouth asks the session, seat and agent layers first,
over its file: `voice`, `volume`, `play_volume`, `music_duck`,
`music_duck_level`, `music_duck_restore_s`, `normalize`, `normalize_lufs`
and `lead_s`.

- **How it reads them:** one `session settings --json <session-id> mouth`
  run (sessiontools) when a line is queued, for the session in
  `$VOICEMODE_SESSION_ID`, else `$CLAUDE_CODE_SESSION_ID`, else
  `$CLAUDE_SESSION_ID`. No session id, no `session` command, or
  `MOUTH_SESSION_BIN` set to `""`: the layers are skipped and the file
  answers.
- **The order,** highest first: the line's own (a flag such as
  `mouth say --volume`, or a mail header), the session layer, the seat
  layer, the agent layer, then the file, then the built-in default. A value
  that is not valid is skipped, and the next layer answers.
- **Only the caller reads the layers.** A line mailed in from outside has
  no caller: it takes the file, then the built-in (for its voice, the
  player's `$MOUTH_VOICE` comes between them). The replayed rest of a cut
  line keeps what its first line was given.
- **The host's own folder is not read.** For values the mouth reads
  `mouth.json` and nothing beside it: no `mouth.d/*.json` and no template.
- **A lock.** `_locked: ["voice"]` in the host file stops a caller's
  `$MOUTH_VOICE` from beating the file's `voice`.

Every other key in `mouth.json` comes from the file alone, whatever the
layers say: `output`, `tts`, `barge_in`, `barge_words`, `barge_duck_level`,
`barge_duck_recover_s`, `barge_duck_max_s`, `barge_ignore_words`,
`resume_answer_words`, `resume_answer_question` and `room_hold`.

## ears.json

The ears' settings: the room's ear and every call's ear.

| Key | Type | Default | What it does | Read by |
| --- | --- | --- | --- | --- |
| `listen` | boolean | `true` | Off stops every ear, the room's and each call's; nothing is heard until it is on again. | the keeper, `bin/call-tap` |
| `room_on_call` | boolean | `false` | Off: while a call is live the room's ear stops, so the call is the only way in. On: both are heard. | the keeper |
| `input` | string | `"default"` | `"default"` follows the Mac's default input; an input's exact name pins the ears to it. | the keeper |
| `final` | object: `url`, `model` | `{"url": "http://127.0.0.1:8890/v1", "model": "mlx-community/parakeet-tdt-0.6b-v3"}` | The engine that writes each kept turn, and the partials unless `partials` says otherwise: an OpenAI-compatible `/v1` URL and its model. Its old name, `stt`, is still read. `EARS_LISTEN_STT_URL` and `EARS_LISTEN_STT_MODEL` win. | every capture, before each chunk |
| `partials` | `"final"` or `"nemotron"` | `"final"` | `final`: the engine above, one chunk at a time. `nemotron`: Nemotron streaming inside the capture, word by word. | every capture, as each turn opens; the mouth |
| `stt_hint` | boolean | `false` | On: each turn's final decode is primed with the names of the seats in the wake file. Only a whisper server uses it. `EARS_STT_HINT` wins. | every capture, on each request |
| `record` | boolean | `false` | On: captured audio is kept on disk (the missed-reply clip and each turn's audio). `EARS_RECORD` wins, and a capture's `--record` or `--no-record` wins over both. A value that is not `true` or `false` keeps nothing. `ears recording on\|off` writes it. | every capture |
| `record_days` | integer, 0 or more | `7` | How many days kept audio stays; `0` keeps it until you delete it. `EARS_RECORD_DAYS` wins. A value that is not valid is ignored. | every capture |
| `record_notice` | boolean | `true` | On: a call states its recording status in one line of its chat. `EARS_RECORD_NOTICE` wins. A value that is not `true` or `false` counts as on. | the call bot; the ears only report it |
| `record_spoken_notice` | boolean | `true` | On: a recorded call starts with a spoken notice saying so. `EARS_RECORD_SPOKEN_NOTICE` wins. A value that is not `true` or `false` counts as on. | the call bot; the ears only report it |
| `heard_partials_only` | boolean | `false` | On: the heard hook drops each finished turn and shows only the partial preview, the shown events and other seats' said lines, for a seat that gets its turns by mail. `EARS_HEARD_PARTIALS_ONLY` wins, and the hook's `--partials-only` or `--no-partials-only` wins over both. A value that is not `true` or `false` counts as off. | `ears heard hook`, on every run |

`ears recording status` prints what `record`, `record_days` and the two
notices resolve to, and which layer set each one.

## ears-sounds.json

Which of the ears' events make a sound, and where it plays.

| Key | Type | Default | What it does |
| --- | --- | --- | --- |
| `allow` | boolean | `true` | Off: no sound plays, whatever each sound says. |
| `sounds` | object: one entry per kind | none | Each kind's sound: `{"on": ..., "freq": ..., "ms": ..., "volume": ...}`. |
| `to` | list of output names | `[]` | Outputs in order of preference; the first one present wins. Empty: the Mac's default output. |
| `pair` | boolean | `false` | On: the sound plays on the output that goes with the mic in use, and a call's sound is mixed into that call. |

Each entry in `sounds`:

| Field | Type | Default | What it does |
| --- | --- | --- | --- |
| `on` | boolean | required | Whether this kind makes a sound. |
| `freq` | integer, 20-20000 | `1320` | The pitch, in Hz. |
| `ms` | integer, 1-2000 | `45` | The length, in milliseconds. |
| `volume` | number, 0-1 | `0.25` | The level. |

- **The kinds the schema names:** `turn` (you finished speaking),
  `partial` (about every 2 s while you talk), `event:device-change`,
  `event:turn-discarded` and `event:listen-stopped`. Any other kind of
  heard-log line may have an entry too, such as `saying`, `said` or
  another `event:NAME`.
- **No file, no sound.** With no `ears-sounds.json`, or no entry for a
  kind, that kind is silent.
- **The global switch.** `allow` set to `false` in
  `~/.settings/notifications/global.json` silences these sounds as well.

## mouth.json

The mouth's settings. "Layers" marks the keys it also reads from the
agent, seat and session layers ([above](#the-mouth-reads-some-keys-from-the-layers)).

| Key | Type | Default | What it does | Layers |
| --- | --- | --- | --- | --- |
| `output` | string | none: see [the differences](#where-the-schema-and-the-code-differ) | The output a line plays on when it names none (no `--device`, no mailed device, no `$MOUTH_DEVICE`). `"default"` is the Mac's default output, `"null"` plays nothing; otherwise an output's exact name. Unset, the line plays on the Mac's default output. | no |
| `voice` | string | `af_sky` | The voice for a line that names none. For a caller's line, `$MOUTH_VOICE` comes after the layers and before the file, unless the file locks `voice`. | yes |
| `tts` | object: `url`, `model` | `url` `http://127.0.0.1:8890/v1`, `model` `mlx-community/Kokoro-82M-bf16` | The OpenAI-compatible speech server the Kokoro backend speaks through, and its model. A `url` alone still asks for Kokoro. `$MOUTH_KOKORO_URL` and `$MOUTH_KOKORO_MODEL` win, each on its own. Never a key: see [configuration.md][mouth-config]. | no |
| `volume` | integer, 0-100 | `100` | A spoken line's level, linear (50 is about -6 dB). `mouth say --volume N` wins. | yes |
| `play_volume` | integer, 0-100 | follows `volume` | A played file's level (`mouth play`). `mouth play --volume N` wins. | yes |
| `normalize` | boolean | `true` | On: every voice is levelled to `normalize_lufs`. `mouth say --no-normalize` wins. Played files are never levelled. | yes |
| `normalize_lufs` | number, -40 to -5 | `-16` | The loudness each voice is levelled to, in LUFS. | yes |
| `music_duck` | boolean | `true` | On: a played file turns down while a line is spoken, or while you talk over it. `mouth duck` overrides it for a while; `mouth play --duck` or `--no-duck` wins over both. | yes |
| `music_duck_level` | integer, 0-100 | `20` | A ducked file's level, as a share of its own. | yes |
| `music_duck_restore_s` | number, 0 or more | `1.0` | Seconds of quiet before a ducked file comes back up. | yes |
| `barge_in` | `"anyone"`, a list of names, or `{"room": ..., "call": ...}` | `"anyone"`: see [the differences](#where-the-schema-and-the-code-differ) | Whose words may cut a line. A list allows only the voices the speaker side-car names; an empty list lets nobody cut. | no |
| `barge_words` | integer, 1 or more | `2` | How many of your own words cut a line you talk over. A stop word ("stop", "wait") cuts at once. It wins over `$MOUTH_BARGE_WORDS`; `mouth barge N\|off` and a line's own `--barge-words` win over it. | no |
| `barge_duck_level` | integer, 0-100 | `25` | The level a line drops to the moment you start talking over it; `100` turns that off. `mouth barge --duck` wins for a while. | no |
| `barge_duck_recover_s` | number, 0 or more | `2.5`: see [the differences](#where-the-schema-and-the-code-differ) | How long a ducked line waits, after you stop talking, before it comes back up. | no |
| `barge_duck_max_s` | number, 0 or more | `4` | The longest a line stays ducked while your voice never pauses. `mouth barge --duck-max` wins for a while. | no |
| `barge_ignore_words` | list of strings | a built-in list of filler and common words | Words barge-in never counts as yours. An empty list counts every word; deleting the key restores the list. `mouth barge --ignore` adds words for a while. | no |
| `resume_answer_words` | integer, 1 or more | `2` | How many of your own words answer a cut question, so its rest is not replayed. It wins over `$MOUTH_RESUME_ANSWER_WORDS`. | no |
| `resume_answer_question` | boolean | `true` | On: only a cut question is dropped once answered. Off: any cut line you answered is dropped. It wins over `$MOUTH_RESUME_ANSWER_QUESTION`. | no |
| `room_hold` | boolean | `true` | On: a line on a room device (any output that is not a call) that asks no hold waits for the end of your turn. `mouth say --now` or `--hold none` plays at once; played files and call lines are not changed. `$MOUTH_ROOM_HOLD` wins over it. | no |
| `lead_s` | number, 0 or more | `0` | Seconds of a line's audio buffered before it plays, so a slow voice cannot chop. `mouth say --lead` wins. | yes |

`mouth status` and `mouth barge` name the layer that set each barge
value. The schema allows other keys in `mouth.json`; `_locked` is the one
the mouth reads.

## Where the schema and the code differ

Each of these is what the code does today. The schema's `default` is a
hint for a settings screen, and in these cases the code does not apply it.

- **`output`:** the schema says `"default"`, but an unset `output` is not
  read as `"default"` from the file. A line that names no output, with no
  `$MOUTH_DEVICE` and no `output` in the file, still plays on the Mac's
  default output: that is the last rung of the mouth's device order
  ([mouth.md][mouth-device]), and the line's `device_from` is `default`,
  not `settings`. Write `"output": "null"` to play nothing instead.
- **`voice` and `tts`:** the schema gives no default. The code's are
  `af_sky`, and Kokoro on `127.0.0.1:8890`, as in the table.
- **`barge_in`:** the schema says `"anyone"`. When the key is absent and
  `session operator` names an operator with a mailbox, the code's default
  is `{"room": ["<mailbox>"]}`: in the room only the operator's voice,
  enrolled under the mailbox name, cuts a line, and calls stay
  `"anyone"`. The key set to any value, `"anyone"` included, wins.
- **`barge_duck_recover_s`:** the schema says `2.5`. Unset, the code takes
  `2.5` when the ears' `partials` is `"final"` and `1.0` when it is
  `"nemotron"`.
- **`ears-sounds.json`'s `freq`, `ms` and `volume`:** the schema gives no
  default. The code plays `1320` Hz for `45` ms at `0.25` for a field left
  out.
- **`ears.json`'s `final` with a `url` and no `model`:** the code asks for
  `whisper-1`, except on `http://127.0.0.1:8890/v1`, where it asks for
  Parakeet.

[kit]: https://github.com/sessionkit/sessionkit/blob/HEAD/docs/settings.md
[kit-cascade]: https://github.com/sessionkit/sessionkit/blob/HEAD/docs/settings.md#the-cascade
[configuration]: configuration.md
[mouth-config]: configuration.md#the-mouth
[mouth-device]: mouth.md#how-it-works
[home]: logs.md#sessionvoices-home
