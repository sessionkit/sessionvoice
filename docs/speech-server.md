# The speech server

The speech server is mlx-audio's server on `127.0.0.1:8890`. It serves both
speech-to-text and text-to-speech: the ears' default engine (Parakeet) and
the mouth's Kokoro voices. `ears services install` finds one or installs its
own. This page says how it decides, how you upgrade it, what it downloads,
and what to use instead. It is one entry of [dependencies](dependencies.md).

## How install finds or installs it

`ears services install` asks before it acts, in this order:

- **Opt-out.** `final.url` in `~/.sessionkit/voice/settings/ears.json` (or
  `EARS_STT_URL` at install) names another engine, and the mouth is not
  being installed: `:8890` is not sessionvoice's to manage.
- **Use.** Something listens on `:8890` and says it is mlx-audio: `GET /`
  returns mlx-audio's welcome line, or `/openapi.json` lists its audio
  routes. sessionvoice uses that server, whoever installed it (VoiceMode,
  for example), and starts nothing.
- **Refuse.** Something else holds `:8890`, or holds it and does not
  answer. Install starts nothing, replaces nothing, and names the holder.
  When the ears hear through `:8890` it exits 2 and says what to run;
  when only the mouth does, it warns and the mouth uses `say`. Free the
  port, or point `final.url` at another speech server.
- **Never a second.** Nothing answers on `:8890`, but a loaded launch
  agent runs `mlx_audio.server` (under any label, so another installer's
  job between restarts counts). Install starts nothing. When that job is
  sessionvoice's own, run `ears services uninstall --only speech`, then
  install again.
- **Install.** Nothing on `:8890` and no such job. On an Apple-silicon
  Mac (macOS 14 or later), install builds sessionvoice's own venv,
  `~/.local/share/ears/speech/venv`, with uv, from the shipped
  `speech/requirements.lock` (`mlx-audio==0.5.8`), and loads it as the
  launch agent `dev.sessionkit.voice.mlx-audio`. `EARS_SPEECH_PY` names a
  python that already has it, and no venv is built. With no uv on `PATH`
  and no venv yet, install refuses and says to install uv or set
  `EARS_SPEECH_PY`. On any other host it installs nothing: it refuses and
  says what to run when the ears need `:8890`, and warns when only the
  mouth does.

`--dry-run` says which of these install would take, and builds and writes
nothing. `ears services status` has a `speech` row that names who serves
`:8890`. `ears services uninstall --only speech` removes only
`dev.sessionkit.voice.mlx-audio`, never a server someone else installed, and
keeps its venv.

sessionvoice can promise "never two servers" only from its own side:
VoiceMode's installer does not ask, so `voicemode service install
mlx-audio` run after sessionvoice's install loads a second mlx-audio job,
and one of the two crash-loops.

## Upgrade it

While sessionvoice's own server runs, a re-install just uses it (the
**Use** step above) and changes nothing in its venv. So to upgrade it, for
example after `uv tool upgrade` brought a new sessionvoice with a new pin,
uninstall it and install again:

    ears services uninstall --only speech
    ears services install --agent NAME     # or --no-agent: what the first install used

Install then re-applies the lock's pins to the venv it finds. Give it the
`--agent NAME` or `--no-agent` the first install used. A bare
`ears services install` is refused when
`~/.sessionkit/voice/settings/wake.json` names no seat, as it does on a Mac
first installed with `--no-agent`: nobody would be woken when you speak.
When the wake file names a seat, a bare re-run is accepted and says which
seat it wakes.

## Its memory cap

MLX keeps freed GPU buffers in a cache for reuse, and by default that cache
may grow to almost all of the Mac's memory. mlx-audio has no setting for it.
So sessionvoice's own server caps it before the server starts: its launch
agent runs a one-line `python -c` that calls `mx.set_cache_limit`, then
`mlx_audio.server`'s own `main()` with the same flags as before. The cap is
512 MiB (536870912 bytes). In sessionvoice's measurements it cut the peak
memory of a long spoken line by about 4 GiB, with no measurable loss of
speed.

To set another cap, run install with `MLX_AUDIO_CACHE_LIMIT` in whole
bytes. Install writes the value into the plist's environment, so it holds
across restarts and logins. An install run without it goes back to the
default:

    MLX_AUDIO_CACHE_LIMIT=2147483648 ears services install --agent NAME

`0` turns the cache off. A value that is not a whole number of bytes under
2**64 is not refused: the server says so in its log and uses 512 MiB. A
value with a control character in it is not written into the plist at all,
so the server uses 512 MiB then too. Each start
logs the cap it applied to `~/.sessionkit/voice/log/ears-speech.log`:

    [ears] mx.set_cache_limit(536870912) (previous 130567005798)

The cap applies only to the server sessionvoice installs. A server it uses
(VoiceMode's, for example) runs as its installer set it up. A server
installed before the cap runs uncapped until you reinstall it, as in
[Upgrade it](#upgrade-it).

## What the lock checks

The lock carries a hash for every pin, and install refuses an artifact that
does not match it. One pin, `docopt`, ships only as source, so uv builds it.
It is pure Python, so it needs no C compiler, and the setuptools uv builds
it with is hash-checked too, from the shipped `speech/build-constraints.txt`.
The server's `webrtcvad` comes from `webrtcvad-wheels`, prebuilt wheels, so
that needs no compiler either. A new venv is checked in full. A re-apply
checks only what it installs, not what is already in the venv, so to check
an existing venv, remove it and install again.

## Its weights

Install then fetches the server's weights, about 2.9 GB, so the first call
does not wait on them. It prints the size first, skips any already cached
and prints progress while it fetches. `--no-prefetch` (or
`EARS_NO_PREFETCH=1`) skips this on a host that must not download, and
`--dry-run` prints the size and fetches nothing. A fetch that fails does
not fail the install. It says why and how much is left. Whatever is not
fetched at install is downloaded by the server on first use: the first
transcription waits on Parakeet, and the first line spoken waits on Kokoro
and its voices.

**These weights are not pinned.** mlx-audio's server takes whatever is on
Hugging Face `main` at first use, and it has no way to ask for a revision,
so a later first use (a new Mac, a cleared cache) can load different
weights. `ears services weights` (and the `weight` rows of `ears services
status`) reads the three, `mlx-community/parakeet-tdt-0.6b-v3`,
`mlx-community/Kokoro-82M-bf16` and `prince-canuma/Kokoro-82M` (the
voices), from the Hugging Face cache: `cached`, `downloading 412 MB of
2.5 GB`, or `not cached`, and `DIFFERS` when the cache's `main` is not the
commit sessionvoice expects. `--json` prints the same for a program, schema
`ears.weights/1` ([models](models.md#the-speech-servers-weights-are-not-pinned)).
It reads the cache's files and never fetches. It only reports; it does not
stop or change what the server loads. A real pin, fixed at install, is not
built yet, so there is no upgrade step for the weights.

## Without it

Point the ears at another OpenAI-compatible STT (`EARS_LISTEN_STT_URL`, or
`url` in `~/.sessionkit/voice/settings/ears.json`) and the mouth at another
TTS (`MOUTH_KOKORO_URL`, or `tts` in
`~/.sessionkit/voice/settings/mouth.json`), a hosted one included, with
`MOUTH_TTS_KEY` or the key file `~/.sessionkit/voice/settings/tts.key` for a
keyed one: see
[a hosted speech endpoint](models.md#a-hosted-speech-endpoint-instead).
On a Mac the mouth's `auto` falls back to `say` when nothing answers.
