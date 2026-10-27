# Models: every model sessionvoice uses, and where it runs

By default, sessionvoice uses three models. **Parakeet** hears the words,
**Silero** says when someone is speaking, and **Kokoro** speaks. A fourth,
**Nemotron 3 Diarization**, names who spoke, on a Mac where the speakers
side-car is installed. That is opt-in: `ears services install --only
speakers` installs it, and a bare install never does. Parakeet and Kokoro
are not loaded by sessionvoice. They run on the [mlx-audio] server at
`127.0.0.1:8890`, and sessionvoice asks for them by name over HTTP. `ears
services install` uses an mlx-audio server that is already there (one
VoiceMode installed, for example), or installs its own,
`dev.sessionkit.voice.mlx-audio` ([the speech server](speech-server.md) says how). Silero
and the diarizer run inside sessionvoice's own processes. Every other model
on this page is opt-in. Talk loads no model at all.

| Model | Part | Served | On by default |
| --- | --- | --- | --- |
| [Parakeet TDT 0.6B v3][parakeet-tdt-06b-v3] | ears | mlx-audio `:8890` | yes |
| [Silero VAD][silero-vad] | ears | in process (CPU) | yes |
| [Kokoro-82M][kokoro-82m] | mouth | mlx-audio `:8890` | yes |
| [Nemotron 3 Diarization][nemotron-3-diarization] | speakers | in process (GPU) | no, `--only speakers` |
| [Qwen3-TTS 1.7B Base][qwen3-tts-17b-base] | mouth (clones) | mlx-audio `:8890` | for a clone voice |
| [Nemotron 3.5 ASR][nemotron-35-asr-streaming-06b] | ears | child process (GPU) | no |
| [Smart Turn v3][smart-turn-v3] | ears | in process | no |
| [Apple SpeechTranscriber][apple-speechtranscriber] | ears | helper, `:2023` | no |
| [Whisper][whisper-whispercpp] | ears | whisper.cpp `:2022` | no |
| [WebRTC VAD][webrtc-vad] | ears | in process (CPU) | no |
| [Apple `say` voices][apple-say-voices] | mouth | macOS `say` | no server |
| [HTDemucs][htdemucs] | mouth (`mouth split`) | separator venv (CPU) | no, `mouth split --install` |

The call ears (`keeper/call-ears`) and `bin/call-tap` use the same models
as the room's ears: they run the same capture, with the same flags, on a
different source.
VoiceMode, sessionphone and the voice cloner use models of their own. See
[Outside sessionvoice][outside-sessionvoice].

Sizes are bytes on disk in the Hugging Face cache (`du`, nothing loaded),
measured on one Mac. RAM is given only where it was
measured, and its source is named. The code these entries were read from is
sessionvoice master `fdbfb11`. The Nemotron 3 Diarization entry was read
again at `f615433`.

## The speech server's weights are not pinned

The mlx-audio server fetches Parakeet and Kokoro from Hugging Face the first
time each is asked for, and Kokoro's voices from a third repo,
`prince-canuma/Kokoro-82M`. It always takes `main`: mlx-audio 0.5.8 passes no
revision and has no setting for one. So a first use on another day (a new
Mac, a cleared cache) can load different weights from the ones sessionvoice
was checked against. sessionvoice cannot pin them from its side yet; a pin
fixed at install is not built yet.

What it does instead is read the cache and report. `SPEECH_WEIGHTS` in
`src/ears/services.py` is the one place that names the commit expected for
each of the three repos, and `SPEECH_WEIGHT_SIZES` beside it holds what the
server fetches from each at that commit. `ears services weights` reads each
weight's state from the Hugging Face cache's files alone. It imports no
Hugging Face code, runs no command and opens no connection, so it is safe to
call during a call. `ears services status` prints the same `weight` rows.

| weight | use | what the server fetches | size |
| --- | --- | --- | --- |
| `mlx-community/parakeet-tdt-0.6b-v3` | stt | the files mlx-audio's allow patterns select (4) | 2,508,940,517 bytes |
| `mlx-community/Kokoro-82M-bf16` | tts | the files mlx-audio's allow patterns select (56) | 355,322,783 bytes |
| `prince-canuma/Kokoro-82M` | voices | `voices/<voice>.safetensors`, one per voice it is asked for (54 in all; never the `.pt` file beside each) | 28,206,306 bytes for all |

The sizes come from the Hugging Face metadata API
(`/api/models/<repo>/tree/<sha>`), never from fetching the weights. In all
that is about 2.9 GB.

Each weight is in one of three states, decided in this order:

1. `downloading`: a `blobs/*.incomplete` file in the repo's cache folder was
   written in the last 120 seconds. huggingface_hub writes each file there
   as it arrives, then renames it into place.
2. `cached`: `refs/main` names a commit, its `snapshots/<commit>/` folder
   exists, no `.incomplete` file is left, and the bytes on disk reach the
   expected size (or the size is unknown).
3. `missing`: anything else. That covers nothing there yet, a partial
   snapshot, and an `.incomplete` file nobody has written for 120 seconds (a
   download that stopped). The next first use fetches whatever `main` is
   then.

Separately, a weight drifts when the cache's `main` is another commit from
the one expected. The server loads that commit. Its size is then unknown.

The server fetches the voice packs one at a time, as each voice is first
asked for. So the voices weight reads `missing`, with a count such as
`3 of 54 packs`, until every pack is there.

### `ears services weights --json`

One JSON object on one line, on stdout, with the schema `ears.weights/1`. It
exits 0 whenever it could read the cache. A missing cache is a state, not an
error.

```json
{"schema":"ears.weights/1","cache":"<home>/.cache/huggingface/hub","state":"downloading","bytes_done":412000000,"bytes_total":2892469606,"weights":[{"repo":"mlx-community/parakeet-tdt-0.6b-v3","use":"stt","state":"downloading","bytes_done":412000000,"bytes_total":2508940517,"expected":"<40 hex>","cached_ref":"<40 hex>","drift":false}]}
```

`<home>` stands in for your home directory. The real output prints the
cache's absolute path.

| key | type | meaning |
| --- | --- | --- |
| `schema` | str | always `ears.weights/1`. New keys may be added under /1. A rename, a removal or a new `state` value bumps it to /2. |
| `cache` | str | the Hugging Face hub cache it read: the one the speech server reads, as an absolute path |
| `state` | `cached` \| `downloading` \| `missing` | overall: `downloading` if any weight is, else `cached` if all are, else `missing` |
| `bytes_done` | int | the sum over the weights |
| `bytes_total` | int \| null | the sum over the weights when every weight's size is known, else null |
| `weights` | list | one entry per weight: stt, tts, voices, in that order |
| `weights[].repo` | str | the Hugging Face repo |
| `weights[].use` | `stt` \| `tts` \| `voices` | what it is for |
| `weights[].state` | `cached` \| `downloading` \| `missing` | as above |
| `weights[].bytes_done` | int | bytes on disk for this repo: its complete files (read through a link into the shared store) plus its `.incomplete` files |
| `weights[].bytes_total` | int \| null | what the server fetches at `expected`; null when the weight drifts |
| `weights[].expected` | str | the 40-hex commit sessionvoice was checked against |
| `weights[].cached_ref` | str \| null | what `refs/main` holds, at most 40 characters, or null |
| `weights[].drift` | bool | `cached_ref` is set and is not `expected` |

A caller that gets a non-zero exit, output that does not parse, or another
`schema` should treat the weights as not confirmed.

The cache it reads is the one the speech server reads, so the reading
answers one question: will the server's first call fetch? launchd gives
sessionvoice's own server (`dev.sessionkit.voice.mlx-audio`) none of the
shell's variables, and its plist sets no `HF_*`, so that cache is
`~/.cache/huggingface/hub`. If the plist ever sets one, that value wins, by
huggingface_hub's own order (`HF_HUB_CACHE`, else `HF_HOME/hub`). The
shell's own `HF_HUB_CACHE` or `HF_HOME` does not change it: weights cached
only there are reported as not cached, because the server cannot see them.
A server started by hand with another `HF_HOME` has its own cache, which
this does not see. The rows are a report: `status` and `weights` exit 0
whatever they say, and nothing is fetched, removed or blocked.

### The install-time prefetch

When `ears services install` installs sessionvoice's own speech server, it
fetches the three weights once, after the server's job is loaded. Then the
first call does not wait about a minute for each download. The fetch is the
server's own call, run on the server's python: `snapshot_download(repo,
revision="main", allow_patterns=...)`, with the patterns from
`SPEECH_WEIGHT_SIZES` (mlx-audio's allow patterns for stt and tts, and
`voices/*.safetensors` for the voices). The cache then holds what the server
looks for, and `refs/main` is written.

- **A heads-up before anything is written.** Once install knows it installs
  its own speech server, and before any plist is written, it prints one
  `speech heads-up:` line: the whole download (the sum of
  `SPEECH_WEIGHT_SIZES`, about 2.9 GB), how much of it is still to fetch when
  some is cached, and that the speech server runs as a login service
  (launchd starts it now and at every login). `--dry-run` prints the same
  line. Under the opt-out it says the first call fetches it instead. When
  every weight is already cached it says so, and gives no heads-up. Install
  does not stop to ask: it warns and goes on, so a scripted install never
  waits on a prompt.
- **The size comes first.** One line gives the total still to fetch, from
  the reader (each weight's size minus what is already on disk). Weights
  already cached are named and skipped.
- **Then progress.** While a weight downloads, install reads the cache every
  5 seconds and prints `N of M` for the weights it is fetching. On a terminal
  the line is rewritten in place. When the output is piped, each reading is a
  new line.
- **The opt-out.** `--no-prefetch`, or `EARS_NO_PREFETCH` set to `1`, `true`
  or `on`, skips the fetch. Install then says how much the first call will
  fetch. `--dry-run` prints the size and the plan and fetches nothing.
- **A failure is not fatal.** If a fetch fails or is interrupted, install
  says why and how much the first call will now fetch, and carries on. It
  never fails because of the prefetch.
- **A final reading.** Install ends with the `weight` rows, which show what
  is cached now.

The fetch goes into the cache the speech server reads, not the cache of the
shell that runs install. launchd gives the server none of the shell's
variables, and its plist sets no `HF_*`, so that cache is
`~/.cache/huggingface/hub`. If the plist ever sets one, that value wins.
Install hands the fetch this path as `HF_HUB_CACHE`, with the server's
`HF_*` in place of the shell's, so what huggingface_hub keeps beside the
cache (its xet cache, under `HF_HOME`) goes where the server's would. It
reads the size and the progress from the same path, and prints it. When the shell's own cache is
elsewhere (because `HF_HUB_CACHE` or `HF_HOME` is set), install says so in
one line. Weights cached only there are fetched again, because the server
cannot see them. When the server on `:8890` is someone else's (install's
step 2, which installs nothing), install fetches nothing for it either.

### An adopted speech server: the cold probe

When the server on `:8890` is someone else's (VoiceMode's, say), install
does not load it, so it fetches nothing for it. Install still probes the
new `final` before it writes it: one short clip sent to
`/audio/transcriptions`. That server may serve only Kokoro and never have
loaded Parakeet. If so, its first Parakeet request downloads Parakeet's
weights (most of the speech download) and loads them before it answers. A 30-second probe cannot
wait that long, so this probe is cold-aware:

- **It says so first.** One `speech` line, printed before the `probing`
  line, says that the server may download and load Parakeet, with its size
  from the `stt` row of `SPEECH_WEIGHT_SIZES`. It says how long install waits, and that
  Ctrl-C skips to whisper.
- **It waits up to 15 minutes** (`COLD_PROBE_S`). Every 15 seconds it
  prints a `probing still waiting` line, so a long wait never looks like a
  hang. The download is the server's own, into the server's own cache. If
  the probe gives up, the download goes on anyway.
- **Ctrl-C skips** the wait: whisper on `:2022` is probed and written, and
  install goes on to the services.
- Every other probe keeps 30 seconds: whisper on `:2022`, a hosted
  `final`, and Apple's `:2023`.

**If it still falls back**, whisper on `:2022` is written to `final`, as
before. Install also records the fallback outside `ears.json`, in
`state/stt-fallback.json` under sessionvoice's home. The record names the
engine install wanted, why its probe failed, and when. Install then says it
three times:

- in the `FALLBACK` line where the probe failed;
- in a closing `stt    FALLBACK` line, the last line of the install;
- in a `stt    FALLBACK since ...` row of `ears services status`, until it
  is fixed.

The closing line and the status row name one command,
`ears services install --only keeper`. If the keeper's notices go to an
agent, it adds `--notify NAME`, so the reinstalled keeper keeps sending
them there. That command probes the wanted engine again. Once it answers,
`final` moves back to it and the record goes. While it still fails, the
fallback and the record stay, and install says so again. The fallback
never becomes permanent without a word. Install still exits 0 on whisper,
because the install did work.

A `final` you set by hand afterwards is never touched. The record is then
stale: status ignores it, and the next install deletes it. When install
loads its own speech server, nothing above applies. The prefetch runs, and
the new `final` is not probed (the server is still starting).

## Parakeet TDT 0.6B v3

- **Model:** [`mlx-community/parakeet-tdt-0.6b-v3`][parakeet-mlx], an MLX
  conversion of [`nvidia/parakeet-tdt-0.6b-v3`][parakeet-nvidia].
- **Used by:** the ears, as the `final` engine of the room capture and of
  every call ear. It writes the partials, one chunk at a time. The keeper
  starts the capture with `--turn-text redecode`, so Parakeet also
  re-decodes the whole turn, and that re-decode is the text of the turn.
  It is also the re-decode in the `smart` turn stage (`ears listen --turns
  smart`, `src/ears/redecode.py`).
- **Served:** by the mlx-audio server on `:8890`, through
  `POST http://127.0.0.1:8890/v1/audio/transcriptions`. The capture loads no
  weights itself. The `smart` stage is different: it loads the model in
  process, with mlx-audio's `load_model`.
- **Default, and what changes it:** `ears services install` writes
  `"final": {"url": "http://127.0.0.1:8890/v1", "model":
  "mlx-community/parakeet-tdt-0.6b-v3"}` into `~/.sessionkit/voice/settings/ears.json`
  when the file has no `final`. With no file at all, the capture uses the
  same pair (`src/ears/listen/stt.py`). The capture follows `final` live, so
  a change takes effect on the next chunk.
  - At install, `EARS_STT_URL` and `EARS_STT_MODEL` write a different `final`.
  - `--stt-url`/`--stt-model`, or `EARS_LISTEN_STT_URL`/`_MODEL`, pin a
    single capture's engine instead.
  - If a URL is given with no model, the request names `whisper-1`.
  - The `smart` stage reads `EARS_PARAKEET`, and `EARS_REDECODE=0` turns its
    re-decode off.
- **A key, for a hosted `final`** (OpenAI's, or any keyed server). Sent as
  `Authorization: Bearer <your-key>` by the capture and by the install's
  probe. They take the first of: `EARS_LISTEN_STT_KEY` in their
  environment; else the key file `~/.sessionkit/voice/settings/stt.key`; else
  `OPENAI_API_KEY`, but only when the URL is `https://api.openai.com`. The
  installed capture is a launch agent whose environment never holds a key
  exported in your shell, so the key file is how it gets one. Make it private
  first, as the mouth's (`src/ears/keys.py`, one reader for both):

  ```sh
  install -m 600 /dev/null ~/.sessionkit/voice/settings/stt.key
  $EDITOR ~/.sessionkit/voice/settings/stt.key
  ```

  One line, the key alone. Nothing creates or writes it, and `EARS_CONF` does
  not move it. A file that is not yours, or that others may read, is not used:
  one `ears: WARNING` names the path, never the key. The capture reads the key
  when it starts (and when ears.json changes its engine), so a new key needs a
  capture restart. A key with a space or a control character in it (a pasted
  newline) is refused before any request, by its source's name (the variable
  or the path), never quoted.
- **Revision:** not pinned; the server fetches `main`. `ears services
  status` reports drift from the expected commit. See
  [The speech server's weights are not pinned][weights-unpinned].
- **Size:** 2.51 GB (`model.safetensors`, 2,508,288,736 bytes).
- **RAM:** 3.4 GB, measured for the in-process re-decode
  (`src/ears/redecode.py`). Over HTTP, the memory belongs to the mlx-audio
  server.
- **Licence:** [CC-BY-4.0][cc-by-4], which requires attribution.

## Silero VAD

- **Model:** `silero_vad.onnx` from [snakers4/silero-vad][silero], as ONNX,
  pinned to release `v6.2.3` (commit
  `5cd7945676eb32225748052e2e6a0580e4686a08`). `ears install` downloads it
  from that commit to `~/.cache/ears/silero_vad.onnx`. `ears services install`
  downloads it too when the model is missing and there is no VoiceMode copy to
  link, to where the keeper reads it: `EARS_SILERO_ONNX` when that is set, else
  the same path. Both check it against
  sha256
  `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`. The pin
  is set once, in `src/ears/vad.py` (`SILERO_TAG`, `SILERO_COMMIT`,
  `SILERO_URL`, `SILERO_SHA256`).
  - The download goes to a temp file in the same directory. It replaces the
    model path only when the sum matches. On a mismatch, or if the fetch
    fails, the temp file is deleted and nothing is written at the model path.
    `ears install` then prints one line naming the URL and exits non-zero.
    `ears services install` refuses with that line and exits 2, before it
    writes any plist. Its `--dry-run` fetches nothing and says what it would
    fetch.
  - A model already on disk is left alone. It may be your own
    `EARS_SILERO_ONNX`, or the VoiceMode copy that `ears services install`
    links. `ears install` hashes it and prints one line saying whether it
    matches the pin. It never replaces it. To get the pinned file, remove it
    and run `ears install` again.
  - The v6.2.3 file is the same as v6.2's, and the same as the file the
    `master` branch served. Its inputs are the ones both
    callers feed: `input`, `state` (2, 1, 128) and `sr`.
    `src/ears/listen/silero.py` says v5, and `src/ears/vad.py` allows v5 or
    v6.
- **Used by:** the ears.
  - It decides speech or not-speech for the end-of-turn rule, in the room
    capture and in every call ear: the keeper, `call-ears` and `call-tap`
    all pass `--detector silero`.
  - It is the Nemotron child's own segmenting VAD.
  - It is the `auto` VAD of `ears listen` and `ears file`.
  - It is the VAD in `shadow/shadow.py`.
- **Served:** in process, through onnxruntime, on one CPU thread.
- **Default, and what changes it:** on in the keeper and the call ears.
  `ears capture` run alone defaults to `--detector vad-silence`, which is
  [WebRTC VAD][webrtc-vad].
  - The model path is `EARS_SILERO_ONNX` for the keeper and `ears.vad`,
    `CALL_EARS_SILERO` for a call ear, and `--silero-model` or
    `EARS_SILERO_MODEL` for a capture run by hand. `call-tap` reads
    `EARS_SILERO_MODEL`, then `EARS_SILERO_ONNX`.
  - `--silero-threshold` sets the speech threshold (default 0.5).
- **Size:** 2.3 MB (2,327,524 bytes).
- **RAM:** not measured.
- **Licence:** MIT.

## Kokoro-82M

- **Model:** [`mlx-community/Kokoro-82M-bf16`][kokoro-mlx], an MLX conversion
  of [`hexgrad/Kokoro-82M`][kokoro]. The mouth knows its 28 English voices
  (`src/mouth/voices.py`), and `af_sky` is the default voice.
- **Used by:** the mouth, for every line spoken in a Kokoro voice.
- **Served:** by the mlx-audio server on `:8890`, through
  `POST http://127.0.0.1:8890/v1/audio/speech`, streamed as PCM.
- **Default, and what changes it:** backend `auto` uses Kokoro unless the
  voice is a clone. There is one exception. On a Mac where nothing answers
  on `:8890`, `auto` speaks with [Apple `say`][apple-say-voices] instead.
  - `MOUTH_KOKORO_URL` sets the server and
    `MOUTH_KOKORO_MODEL` sets the model. `tts` in
    `~/.sessionkit/voice/settings/mouth.json` sets them too, and the env wins. See
    [A hosted speech endpoint instead](#a-hosted-speech-endpoint-instead).
  - `MOUTH_BACKEND`, `--backend`, or a mail's `X-Mouth-Backend`
    header choose the backend.
- **Revision:** not pinned, for the model or for the voices, which come from
  `prince-canuma/Kokoro-82M`; the server fetches `main` of each. `ears
  services weights` reports what is cached and any drift from the expected
  commits. See
  [The speech server's weights are not pinned][weights-unpinned].
- **Size:** 356 MB: the weights (`kokoro-v1_0.safetensors`, 327 MB) plus 55
  voice files.
- **RAM:** not measured, because the server holds it.
- **Licence:** Apache-2.0.

### A hosted speech endpoint instead

With no mlx-audio on the Mac, the mouth can speak through any
OpenAI-compatible `/v1/audio/speech` server, a hosted one included. Put this
in `~/.sessionkit/voice/settings/mouth.json` (the schema is
`settings/mouth.schema.json`):

```json
{
  "tts": {"url": "https://speech.example.com/v1", "model": "example-tts-model"},
  "voice": "alloy"
}
```

The URL, model and voice above are placeholders. Use your provider's.

- **URL and model.** Each field on its own, as the ears read theirs:
  `MOUTH_KOKORO_URL`, else `tts.url`, else
  `http://127.0.0.1:8890/v1`; `MOUTH_KOKORO_MODEL`, else
  `tts.model`, else Kokoro-82M. Set `model` too: a URL alone still asks for
  Kokoro-82M. The file is read for every line, so a change applies to the
  next line, with no restart.
- **The voice.** Set `voice` to one your provider has. The default, `af_sky`,
  is a Kokoro voice, and a hosted endpoint answers it with HTTP 400. Under
  `auto`, a voice the mouth does not know (OpenAI's `coral`, say) is sent to
  a remote `tts` URL as it is, and a typo comes back as the provider's 400,
  the line's error. "Remote" means any host but `localhost`, `127.0.0.0/8`
  and `::1`. With a local URL, or none, an unknown voice is refused as before;
  `--backend kokoro` sends any name to a local server that knows it.
- **The key.** Sent as `Authorization: Bearer <your-key>` to the `tts` URL
  only, never to a clone voice's server. The mouth takes the first of:
  `MOUTH_TTS_KEY=<your-key>` in its environment; else the key file
  `~/.sessionkit/voice/settings/tts.key`; else `OPENAI_API_KEY`, but only when the URL
  is `https://api.openai.com`. The key is never read from mouth.json, and
  never goes in the URL. It is never written to an error, a log, the heard
  log or a notice.
- **The key file** is how the installed mouth gets its key. Make it yours
  and private first, then put the key in it:

  ```sh
  install -m 600 /dev/null ~/.sessionkit/voice/settings/tts.key
  $EDITOR ~/.sessionkit/voice/settings/tts.key
  ```

  One line, the key alone; outer whitespace is ignored, and an empty file is
  no key. Neither the mouth nor `ears services install` creates or writes
  it. The mouth reads it for every line, so a new key applies to the next
  line, with no restart. A file that is not yours, or that has any group or
  other permission bit (`chmod 644`, say), is not used: the mouth logs one
  WARNING that names the path, never the key, and sends no key from it. No
  file is no key, as before.
- **The environment variable must be in the player's environment**: the
  process that speaks, not the shell that runs `mouth say`. The installed
  mouth is a launch agent (`dev.sessionkit.voice.ears-mouth`, which runs
  `mouth mail`), and its environment is only `PATH`, `EARS_PY` and install's
  own marker, `SESSIONKIT_INSTALLED_BY`, so a key exported in your shell never
  reaches it: use the key file. For a hand test
  only, give it to launchd and restart the agent:

  ```sh
  launchctl setenv MOUTH_TTS_KEY '<your-key>'
  launchctl kickstart -k "gui/$(id -u)/dev.sessionkit.voice.ears-mouth"
  ```

  It does not survive a reboot, and every agent launched afterwards sees it
  too. `launchctl unsetenv MOUTH_TTS_KEY` takes it back.
- **Offline.** Before each line `auto` checks that the URL answers (a TCP
  connect, 0.3 s). On a Mac where it does not, the line is spoken with Apple
  `say`. For the hosted voice or nothing, set `MOUTH_BACKEND=kokoro`
  (or `--backend kokoro`), which skips the check.

## Nemotron 3 Diarization

- **Model:** [`mlx-community/Nemotron-3-Diarization`][diar-mlx], an MLX
  conversion of [`nvidia/Nemotron-3-Diarization`][diar-nvidia].
- **Used by:** speakers, the speaker-name side-car
  (`speakers/speaker-sidecar2.py`), which names who spoke each heard line
  into `speakers_YYYY-MM-DD.jsonl` beside the heard log, in the room and on
  a call. `ears speakers enrol` runs `speakers/cluster-room.py` and
  `speakers/make-refs.py`, which use the same model to build each person's
  reference clip.
- **Served:** in process, on the GPU, in the side-car's own venv. The ears
  venv does not have it.
  - Install (`--only speakers`) builds that venv in the speakers home
    (`~/.local/share/ears/speakers/venv`; `EARS_SPEAKERS_HOME` moves the
    home). It uses uv, and installs the `speakers/requirements.lock` that
    the package ships, with its hashes. The lock pins the `mlx-audio==0.5.8`
    release. It is not a git build.
  - The diarizer needs mlx-audio 0.5.6 or later; 0.5.5 has no
    `nemotron_diarization`. Install refuses a python that cannot import it,
    and names the first module missing.
  - `EARS_SPEAKER_PY` names a python that already has it. Then no venv is
    built.
  - The job is the launch agent `dev.sessionkit.voice.ears-speakers`, which runs
    `speakers/run.sh`.
- **Default, and what changes it:** off. Only `ears services install
  --only speakers` installs the side-car; a bare install never does.
  - Install refuses unless the Mac is Apple silicon (arm64) on macOS 14 or
    later.
  - A hand-made side-car already on the Mac (such as the `launchd/`
    template's job) is left alone, and ours is skipped. If ours was
    installed first, ours is removed. Two side-cars would label every line
    twice.
  - The job cannot run until someone has a reference clip in the speakers
    home's `refs/`. `ears speakers enrol` makes them.
  - The model is hard-coded, and no setting changes it. So is its revision:
    one pinned commit, `SPEAKER_REVISION` in `src/ears/services.py`, which
    every script that loads it also names.
  - Install downloads the model at that revision, unless it is already
    cached. That is the one download. The job and `ears speakers enrol` run
    offline (`HF_HUB_OFFLINE=1`), so they never fetch it.
- **Size:** 199 MB (198,636,662 bytes).
- **RAM:** not measured. A line takes about 30 ms (`speakers/README.md`).
- **Licence:** [OpenMDW 1.1][openmdw].

## Qwen3-TTS 1.7B Base

- **Model:** [`mlx-community/Qwen3-TTS-12Hz-1.7B-Base-4bit`][qwen-tts-mlx], an
  MLX conversion of [`Qwen/Qwen3-TTS-12Hz-1.7B-Base`][qwen-tts].
- **Used by:** the mouth, for a clone voice (a voice directory under
  `~/.voicemode/voices`). The mouth never names this model itself. It sends
  whatever model its resolver reads (`src/mouth/resolver.py`, the mouth's
  copy of VoiceMode's voice profiles), along with that voice's `ref_audio`
  and `ref_text`.
- **Served:** by mlx-audio, through `POST <base>/audio/speech`. The base is
  `VOICEMODE_MLX_AUDIO_BASE_URL`, by default `http://127.0.0.1:8890/v1`.
- **Default, and what changes it:** `VOICEMODE_IMPRESSIONS_MODEL`, which
  defaults to the 4-bit model above. The resolver reads both keys from the
  environment, else from `~/.voicemode/voicemode.env` (read, never written).
  Clone voices do not need VoiceMode installed: the mouth reads the voice
  directories itself. It refuses a voice it cannot resolve rather than
  substitute another.
- **Size:** 2.34 GB (2,336,074,141 bytes).
- **RAM:** not measured.
- **Licence:** Apache-2.0.

## Nemotron 3.5 ASR streaming 0.6B

- **Model:** [`mlx-community/nemotron-3.5-asr-streaming-0.6b`][nemotron-mlx],
  an MLX conversion of
  [`nvidia/nemotron-3.5-asr-streaming-0.6b`][nemotron-nvidia].
- **Used by:** the ears, for streaming partials. It applies when
  `"partials": "nemotron"` is in `ears.json`, or when the capture is given
  `--partials nemotron`. The turn's text is still Parakeet's re-decode. It
  is also the `nemotron` backend of `ears listen` and `ears file`.
- **Served:** in a child process (`src/ears/backends/proc.py`), on the GPU,
  through mlx-audio's `load_model`. It is never served over HTTP.
  - The capture never waits on the child. It writes the child's audio
    without blocking, and keeps every frame whole. If the child falls 1 s
    behind, new frames are dropped whole and counted. If it then takes
    nothing for 10 s of audio, the stream is failed: the capture writes
    `partials-error` and goes back to chunk partials. When the capture
    closes the stream, the child is killed, so a stuck child is never left
    running. `ears listen` and `ears file` still wait for the child.
- **Default, and what changes it:** off. `partials` is never seeded. In
  `ears listen --backend auto`, it is second, after Apple.
  - `EARS_NEMOTRON_MODEL` sets the model.
  - `--backend` or `EARS_BACKEND` pick the backend.
  - It needs the `sessionvoice[nemotron]` extra (mlx-audio 0.5.4 or later).
- **Size:** 1.28 GB (1,276,703,116 bytes).
- **RAM:** 1.35 GB RSS, measured in a streaming eval
  (`src/ears/backends/nemotron.py`).
- **Licence:** [NVIDIA Open Model License][nvidia-oml].

## Smart Turn v3

- **Model:** [`mlx-community/smart-turn-v3`][smart-mlx] for MLX, or
  `smart-turn-v3.2-gpu.onnx` from [`pipecat-ai/smart-turn-v3`][smart] for
  ONNX. It answers one question: does this sound finished?
- **Used by:** the ears' `smart` turn stage (`ears listen --turns smart`),
  and `shadow/shadow.py`, which only logs its verdicts and is run by hand.
  The room capture and the call ears do not use it. Talk only reads the
  shadow log, when `talk serve --smart-turn DIR` is given, and that is off
  by default.
- **Served:** in process. It uses MLX (mlx-audio, on the GPU) when
  mlx-audio imports. Otherwise it uses ONNX on one CPU core, which also
  needs `transformers` for the Whisper feature extractor: the
  `sessionvoice[smart-turn]` extra brings both.
- **Default, and what changes it:** off. `--turns` defaults to `none`.
  - `EARS_SMART_TURN_MLX` sets the MLX model. It defaults to
    `~/.cache/ears/smart-turn-v3-mlx`, then the repo above.
  - `EARS_SMART_TURN_ONNX` sets the ONNX model. It defaults to
    `~/.cache/ears/smart-turn-v3.2-gpu.onnx`.
  - `EARS_SMART_MODE` is `veto` (the default) or `shorten`.
- **Size:** 32 MB for MLX. The ONNX file was not on the measured Mac.
- **RAM:** not measured.
- **Licence:** BSD-2-Clause.

## Apple SpeechTranscriber

- **Model:** Apple's on-device speech (SpeechAnalyzer and
  SpeechTranscriber), on macOS 27. No weights ship with sessionvoice.
  macOS downloads the speech assets for each locale.
- **Used by:** the ears, in two ways.
  - `ears serve`, the opt-in `stt` launch agent on `:2023`, is an
    OpenAI-compatible URL that a capture's `final` can point at.
  - It is the `apple` backend of `ears listen`, which `auto` tries first.
- **Served:** by the `apple-speech` Swift helper, in its own process. That
  isolates crashes: the Speech framework traps on some bad input.
  `ears install --apple` builds the helper and fetches the assets.
- **Default, and what changes it:** opt-in.
  - `EARS_STT_URL=http://127.0.0.1:2023/v1 ears services install --apple`
    makes it the `final` engine.
  - `EARS_LOCALE` sets the locale of the `apple` backend (default `en-AU`).
    `ears serve` uses `en-AU` unless a request names a `language`.
  - `EARS_APPLE_HELPER` sets the path to the helper.
- **Size:** the assets are managed by macOS and were not measured.
- **Licence:** part of macOS, under Apple's terms.

## Whisper (whisper.cpp)

- **Model:** a whisper.cpp `ggml-*.bin` model. VoiceMode chooses it
  (`VOICEMODE_WHISPER_MODEL`, default `base`); sessionvoice does not.
  The measured Mac has `ggml-large-v2.bin` (3.09 GB) and `ggml-base.bin`
  (148 MB).
- **Used by:** the ears, only when pointed at it.
  - It is the `whisper` backend of `ears listen`, which `auto` tries last.
    That backend posts to `EARS_WHISPER_URL`, by default
    `http://127.0.0.1:2022/v1/audio/transcriptions`.
  - A capture uses it when its `final` URL is `:2022`.
- **Served:** by VoiceMode's whisper.cpp server on `:2022`.
- **Default, and what changes it:** off. The seeded `final` is Parakeet, so
  a capture reaches whisper.cpp only when `final` names `:2022`.
  `EARS_WHISPER_URL` sets the `whisper` backend's URL.
- **Licence:** MIT, for both the weights and whisper.cpp.

## WebRTC VAD

- **Model:** none to download. A small model is compiled into the library,
  [webrtcvad-wheels].
- **Used by:** `ears capture` when it is run without `--detector`. Its
  default detector is `vad-silence`. The keeper and the call ears do not use
  it.
- **Served:** in process, on the CPU. `EARS_LISTEN_VAD_AGGRESSIVENESS`
  sets how aggressive it is (default 3).
- **Licence:** MIT (the wheel). WebRTC itself is BSD-3-Clause.

## Apple `say` voices

- **Model:** the voices installed in macOS, through `/usr/bin/say`.
- **Used by:** the mouth's `apple` backend. Backend `auto` also falls back
  to it on a Mac with no speech server on `:8890`.
- **Default, and what changes it:** `MOUTH_APPLE_VOICE` sets the
  voice. Otherwise the mouth uses the requested voice if it is an installed
  Apple voice, and the system voice if not.
- **Licence:** part of macOS, under Apple's terms.

## HTDemucs

- **Model:** `htdemucs`, Demucs's hybrid transformer model, one file:
  [`955717e8.safetensors`][htdemucs-hub] from the Hugging Face repo
  `adefossez/HTDemucs`.
- **Used by:** the mouth. `mouth split FILE` separates a song into its four
  stems (vocals, drums, bass, other) and its instrumental, the song without
  its vocals, in the file's [sidecar](sidecar.md). `mouth play` plays the
  instrumental by default, and `mouth play --stem` plays stems.
- **Served:** in a child process on the CPU, run at a lower priority, in the
  separator's own venv (`~/.local/share/ears/separator/venv`;
  `MOUTH_SEPARATOR_HOME` moves the home). Neither the sessionvoice package
  nor the speech server has it. The play path never imports torch.
  - `mouth split --install` builds that venv with uv (Python 3.12) from the
    shipped `separator/requirements.lock`, installed with
    `--require-hashes`: demucs 4.1.0 and torch 2.14.1, the speech venv's
    torch, plus numpy and soundfile. Every pin is a wheel. It needs no
    ffmpeg and no torchaudio.
  - Install refuses unless the Mac is Apple silicon (arm64) on macOS 14 or
    later. `--dry-run` prints the plan and the sizes and does nothing.
  - `MOUTH_SEPARATOR` names a command run in place of the installed runner.
- **Default, and what changes it:** off until installed. Only `mouth split
  --install` installs it; `ears services install` never does. Once it is
  installed, `mouth.music_instrumental` (on by default) makes a song's first
  play start a split; `false`, or `mouth play --no-instrumental`, plays the
  file as it is and starts none.
- **The pin.** The model and its revision are hard-coded (`MODEL` in
  `src/mouth/separator.py`), and no setting changes them:
  - revision `cbc8a9b1a87023b7fd74e7b3412e6321c0eab003`, a commit, never a
    branch;
  - 84,025,440 bytes, SHA-256
    `d9fa14133cfcc034a6758923bb3a8ca9f8dfd0b582134643bbf83f72c17576dd`,
    the same digest the hub publishes for the file;
  - the class its metadata names, `demucs.htdemucs.HTDemucs`.

  Install downloads the file at that revision to a temporary name, checks
  its size, its full SHA-256 and that class, and only then renames it into
  place. A file that fails any check is removed, and the install is refused.
  The pin is what makes the file safe to load: Demucs's loader builds the
  class the file's metadata names. A split never fetches. It loads only the
  checked file, by path, never through the hub.
- **Why this file, not the older `.th` checkpoint:** its full SHA-256 is
  published by the hub, so the pin is checked against a source other than
  our own download; it is never unpickled; and it needs no other file.
- **Size:** 84 MB (84,025,440 bytes). The venv is about 1 GB, mostly torch.
- **RAM:** the split's peak memory footprint, measured with
  `/usr/bin/time -l` on one Apple-silicon Mac, on the CPU: about 2 GB for
  a 1-minute file, 4.5 GB for a 10-minute one and 7.3 GB for a 20-minute
  one. Most of it is Demucs's own while the model runs, about 3 GB for 10
  minutes and 5 GB for 20. The four stems are float 32-bit samples, about
  1.7 GB for 20 minutes, and the runner holds them once: it puts the mix's
  level back on them in place. That is why a play never splits a file over
  10 minutes on its own; `--instrumental` splits a longer one.
- **Speed:** about 0.15 s of CPU time per second of audio on one Apple-silicon
  Mac; the mouth assumes 0.4 until it has measured its own host.
- **Licence:** the demucs package, the code that loads and runs the model, is
  MIT (its PyPI metadata). The model card of `adefossez/HTDemucs` states no
  licence for the weights, and the repo is not gated. sessionvoice ships no
  weights: each install fetches the file from the hub.

## Only for `ears eval`

`ears eval` compares speech-to-text engines on the same audio. It is a
development tool, and no service runs it. Its engines are:

- Apple, on `:2023`.
- whisper.cpp large-v2, on `:2022`.
- whisper.cpp `ggml-large-v3-turbo.bin`.
- Parakeet `ggml-parakeet-tdt-0.6b-v3-q8_0.bin`, through `parakeet-cli`.
- Parakeet and Nemotron 3.5, on MLX.

The ggml files live in `EARS_EVAL_HARNESS`, by default `~/.cache/ears/eval`.
That folder was not on the measured Mac.

## Outside sessionvoice

The kit has other parts that call models. Most of them share the same
mlx-audio server on `:8890`. That server is not tied to any one model: it
loads whatever a request names. So its memory is the total of every model
anyone has asked it for.

- **VoiceMode.** Where it is installed, it may run the mlx-audio server
  (its `mlx-audio` launch agent) that Parakeet, Kokoro and the clone voices
  use, and sessionvoice then uses that one and starts none of its own.
  VoiceMode's own speech also uses models:
  - Speech-to-text: `VOICEMODE_STT_BASE_URLS` defaults to whisper.cpp on
    `:2022`, then OpenAI. On mlx-audio, the default model is
    `mlx-community/whisper-large-v3-turbo-asr-4bit`.
  - Text-to-speech: `VOICEMODE_TTS_BASE_URLS` defaults to kokoro-fastapi on
    `:8880`, then OpenAI. On mlx-audio, the default model is
    `mlx-community/Kokoro-82M-bf16`.
  - Clone voices: `VOICEMODE_IMPRESSIONS_MODEL`, which defaults to Qwen3-TTS
    1.7B Base 4-bit.
- **sessionphone.** It sends speech to text over HTTP, for voice notes:
  `DC_STT_URL`, which defaults to
  `http://127.0.0.1:2023/v1/audio/transcriptions` (`ears serve`, Apple).
  Unless `DC_STT_MODEL` is set, it picks the model from the kind of
  endpoint:
  - `apple-speechtranscriber` on `ears serve`, the default.
  - `mlx-community/parakeet-tdt-0.6b-v3` on mlx-audio.
  - `large-v2` on whisper.cpp.
  - `whisper-1` on OpenAI.

  Its calls speak through `:8890`. A Kokoro voice goes to `DC_KOKORO_URL`
  with `DC_KOKORO_MODEL` (default `mlx-community/Kokoro-82M-bf16`). A clone
  voice goes to `DC_TTS_URL` with `DC_TTS_MODEL` (default
  `mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16`, 4.54 GB). macOS `say` is
  also available.
- **The voice cloner (voice-lab).** Its `sayas` CLI and its testing UI
  speak through an mlx-audio server (`VOICE_LAB_SERVER`) with
  `mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16`. `--model` changes the
  model. Its scripts that prepare reference clips transcribe them with
  `mlx-community/whisper-large-v3-turbo`.

[parakeet-tdt-06b-v3]: #parakeet-tdt-06b-v3
[htdemucs]: #htdemucs
[htdemucs-hub]: https://huggingface.co/adefossez/HTDemucs
[silero-vad]: #silero-vad
[kokoro-82m]: #kokoro-82m
[nemotron-3-diarization]: #nemotron-3-diarization
[qwen3-tts-17b-base]: #qwen3-tts-17b-base
[nemotron-35-asr-streaming-06b]: #nemotron-35-asr-streaming-06b
[smart-turn-v3]: #smart-turn-v3
[apple-speechtranscriber]: #apple-speechtranscriber
[whisper-whispercpp]: #whisper-whispercpp
[webrtc-vad]: #webrtc-vad
[apple-say-voices]: #apple-say-voices
[outside-sessionvoice]: #outside-sessionvoice
[weights-unpinned]: #the-speech-servers-weights-are-not-pinned
[mlx-audio]: https://github.com/Blaizzy/mlx-audio
[parakeet-mlx]: https://huggingface.co/mlx-community/parakeet-tdt-0.6b-v3
[parakeet-nvidia]: https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3
[cc-by-4]: https://creativecommons.org/licenses/by/4.0/
[silero]: https://github.com/snakers4/silero-vad
[kokoro-mlx]: https://huggingface.co/mlx-community/Kokoro-82M-bf16
[kokoro]: https://huggingface.co/hexgrad/Kokoro-82M
[diar-mlx]: https://huggingface.co/mlx-community/Nemotron-3-Diarization
[diar-nvidia]: https://huggingface.co/nvidia/Nemotron-3-Diarization
[openmdw]: https://openmdw.ai/license/
[qwen-tts-mlx]: https://huggingface.co/mlx-community/Qwen3-TTS-12Hz-1.7B-Base-4bit
[qwen-tts]: https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base
[nemotron-mlx]: https://huggingface.co/mlx-community/nemotron-3.5-asr-streaming-0.6b
[nemotron-nvidia]: https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b
[nvidia-oml]: https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-license/
[smart-mlx]: https://huggingface.co/mlx-community/smart-turn-v3
[smart]: https://huggingface.co/pipecat-ai/smart-turn-v3
[webrtcvad-wheels]: https://pypi.org/project/webrtcvad-wheels/
