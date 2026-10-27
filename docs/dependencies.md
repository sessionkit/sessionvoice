# Dependencies

The direct dependencies of sessionvoice and the tools a user meets: what needs
each one, whether you need it, how it gets onto your Mac, and how you upgrade
it. The README's [requirements](../README.md#requirements) link here.
The full resolved set, every package at the version this repo is tested with, is in [`uv.lock`][uv-lock].

- **Required:** Python 3.10 to 3.14, uv, the Python packages the installer
  brings, and the ears' voice-activity model, which `ears install` fetches
  (`ears services install` fetches it too when it is missing).
- **Optional:** the speech server and its weights, sessionmail's `message`,
  Claude Code, Apple's Command Line Tools, SwitchAudioSource, the speaker
  side-car, the Apple speech helper, Nemotron, Smart Turn, the separator,
  and a few tools single scripts use.
- **Pending a ruling:** Homebrew, tmux, notmuch and ffmpeg. Whether the
  family requires them is not decided yet; their entries say what uses each
  one today ([pending a ruling](#pending-a-ruling)).
- **Upgrade:** `uv tool upgrade sessionkit` when the kit installed
  sessionvoice, or `uv tool upgrade sessionvoice` when you installed it on
  its own. Then, if sessionvoice runs its own speech server, `ears services
  uninstall --only speech` and `ears services install` again, with the
  `--agent NAME` or `--no-agent` the first install used
  ([upgrade](#upgrade)).

Required means a default command fails without it. Optional means a feature
you turn on needs it, or a command still works without it but does less.

The always-on parts are macOS only: the launch agents `ears services
install` writes, the keeper, which follows devices through Core Audio, and
the speech server. The engine alone (`ears file`, and `ears listen` with the
`whisper` backend) needs no Mac.

## Required

| Dependency | What needs it | How you get it | How you upgrade it |
|---|---|---|---|
| Python 3.10 to 3.14 | Every command: `ears`, `ears-serve`, `mouth`, `talk`, `sessionvoice-web`. The launch agents run on the same Python, and the keeper's JSON reads run its `python3`: install puts that Python's own folder first on the agents' `PATH`. | uv fetches one when it installs sessionvoice. | It stays with the install: `uv tool upgrade` keeps it. |
| [uv](https://docs.astral.sh/uv/) | Installing sessionvoice; `ears services install`, which builds the speech server's venv with it, and the speaker side-car's with `--only speakers`; `make test`, which runs `uv run --locked`. | The uv installer. | `uv self update`. |
| The Python packages | Every command. | The installer brings them from PyPI (below). | With sessionvoice: `uv tool upgrade`. |
| The voice-activity model (Silero VAD) | The ears telling speech from silence: the keeper, every call ear, `ears listen` and `ears file`. | `ears install`, 2.3 MB to `~/.cache/ears/silero_vad.onnx`, pinned to one upstream release and checked against its SHA-256 ([models](models.md#silero-vad)). `ears services install` fetches the same file in the same way when it is missing and VoiceMode's copy is not there to link. | `ears install` leaves a model already on disk alone (your own `EARS_SILERO_ONNX`, or VoiceMode's copy) and only says whether it matches the pin. To replace it, remove the file and run `ears install`. |

Without the voice-activity model the ears cannot hear you. `ears services
install` fetches it when it is missing, by the same pin, before it writes
anything else. If the fetch fails or the file does not match the checksum,
install refuses and leaves no file behind. `--dry-run` says what it would
fetch and fetches nothing. When several prerequisites are missing, `ears
services install` lists them all in one run and writes nothing. Without uv,
`ears services install` refuses to build the speech server, unless one is
already running or `EARS_SPEECH_PY` names a python that has it.

The Python packages, from `pyproject.toml`:

- **Always:** numpy, pyyaml (a voice file's front matter), httpx (the
  mouth's speech backends) and sounddevice (the mouth's player; its macOS
  wheel brings PortAudio). A bare install can run `mouth say`.
- **`capture`**, the mic and call ears: scipy, onnxruntime and
  webrtcvad-wheels, with httpx and sounddevice. The kit installs
  sessionvoice with this extra.
- **`silero`**: onnxruntime. **`smart-turn`**: onnxruntime and transformers.
  **`nemotron`**: mlx-audio 0.5.6 or later. **`all`**: every one of these.
- An import that needs an extra you did not install fails naming it, as
  `pip install 'sessionvoice[EXTRA]'`.
- The lock holds a macOS arm64 wheel for every Python from 3.10 to 3.14.

## Optional

| Dependency | What needs it | Without it | How you get it | How you upgrade it |
|---|---|---|---|---|
| [The speech server](speech-server.md) | The ears' speech-to-text (Parakeet) and the mouth's Kokoro voices, on `127.0.0.1:8890`. An Apple-silicon Mac on macOS 14 or later. | The ears, which hear through it by default, cannot turn speech into text; the mouth's `auto` speaks with macOS `say`. Or point both at another OpenAI-compatible server. | `ears services install` uses an mlx-audio server already there, or builds its own with uv from [`speech/requirements.lock`](../speech/requirements.lock), the speech venv's own hash-pinned set. | `ears services uninstall --only speech`, then `ears services install` with the `--agent NAME` or `--no-agent` the first install used ([upgrade it](speech-server.md#upgrade-it)). |
| The speech server's weights | Parakeet, Kokoro and Kokoro's voices, about 2.9 GB. | The first transcription and the first line spoken wait on the download. | `ears services install` fetches them from Hugging Face and prints the size first; `--no-prefetch` leaves them to the first call. | Nothing pins them: a later first use can load different weights. `ears services weights` says whether the cache holds the ones sessionvoice expects ([its weights](speech-server.md#its-weights)). |
| sessionmail's `message` | The turn mail that wakes an agent when you speak (`keeper/ears-mail.py`), and the mouth's notices. | `ears services install` warns `message MISSING`; nothing is mailed, so no agent is woken. | `uv tool install sessionmail`, or the kit. | `uv tool upgrade sessionmail`. |
| [Claude Code](https://claude.ai/code) | The plugin: `/ears-setup`, the `ears` skill, and the heard hook a seat opts into (`ears heard hook`). | The commands still run in a terminal. | Its installer; the plugin from a marketplace: the kit's, or this repo's own, `sessionvoice`. | Claude Code's own update; `claude plugin update sessionvoice@MARKETPLACE`. |
| Apple's Command Line Tools | `swiftc`, which builds the keeper's fast device watcher. | The keeper polls `system_profiler` for device changes. It never runs the `swiftc` stub, which would open the install dialog. | `xcode-select --install`. | Software Update. |
| SwitchAudioSource | The keeper's output fail-over, which moves the Mac's default output off a cable or a dead device; the keeper reads devices with it only when the device watcher's file is missing or stale. | The keeper notes `output switch ... FAILED` and does not fail over. | Homebrew, `switchaudio-osx`. | With Homebrew. |
| The speaker side-car | Naming who said each heard line (`ears services install --only speakers`). An Apple-silicon Mac on macOS 14 or later. | Heard turns carry no speaker. | `--only speakers` builds its own venv with uv from `speakers/requirements.lock` and fetches the diarizer, about 190 MB, once ([models](models.md#nemotron-3-diarization)). | `ears services install --only speakers` again: it re-applies the lock's pins. |
| The Apple speech helper | The `apple` backend and `ears serve`: macOS 27, Xcode's `swift`, and a source checkout. | No `apple` backend. The wheel does not ship `swift/`, so `--apple` without a checkout refuses. | `uv run ears install --apple`, from a checkout ([models](models.md#apple-speechtranscriber)). | Pull the checkout, then run it again. |
| Nemotron | The `nemotron` backend and Nemotron partials. | No streaming Nemotron partials. | The `nemotron` extra; mlx-audio fetches the model, 1.28 GB, on first use ([models](models.md#nemotron-35-asr-streaming-06b)). | With sessionvoice. |
| The Smart Turn model | `ears listen --turns smart`. | That turn stage cannot load. | `ears install` does not fetch it. With mlx-audio it is fetched from Hugging Face on first use; the ONNX file you download yourself ([models](models.md#smart-turn-v3)). | Fetch it again. |
| The separator | The instrumental `mouth play` plays by default, and the stems `mouth play --stem` plays. An Apple-silicon Mac on macOS 14 or later. | A song with no instrumental yet plays as it is, and `mouth play` names `mouth split --install`; `--stem` is refused with the command that makes the stems. | `mouth split --install` builds its own venv with uv from [`separator/requirements.lock`](../separator/requirements.lock) (Demucs and torch, about 1 GB) and fetches its model, about 84 MB, checked against a pinned sha256 ([models](models.md#htdemucs)). | `mouth split --install` again: it re-applies the lock's pins. |

A few tools serve one script each, outside the commands above: `bin/call-tap`
needs sox and the BlackHole audio drivers ([call tap](call-tap.md)), and
`ears eval` runs whisper.cpp's `whisper-cli` from VoiceMode's build when it
is there, and skips that engine when it is not. mpv is not a dependency: the
mouth only talks to an mpv player you started yourself.

## Pending a ruling

Whether the family requires these four is not decided yet, so this page gives
them no class and no install step. Each entry says what uses it today, what
happens without it, and where it comes from today.

### Homebrew

Status: pending a ruling. It hangs on the ruling for ffmpeg, below, and on
where the tools above that come from it should come from.

- **What uses it:** no sessionvoice command runs `brew`. It is where ffmpeg,
  SwitchAudioSource, sox and BlackHole come from today. The launch agents'
  `PATH` includes Homebrew's folders, so tools it installs are found there.
- **Without it:** those tools need another source.
- **Where it comes from today:** its installer, from its own site.

### tmux

Status: pending a ruling.

- **What uses it:** no sessionvoice command runs tmux.
- **Without it:** sessionvoice works the same.
- **Where it comes from today:** Homebrew.

### notmuch

Status: pending a ruling. Whether the family needs notmuch, and a
replacement for its mail search, are being researched.

- **What uses it:** no sessionvoice command runs notmuch. `ears services
  install` runs sessionmail's `message setup --box heard` on a host that
  delivers by sessionmail's mail drop, and that step also writes
  sessionmail's notmuch profile when it is missing.
- **Without it:** sessionvoice works the same; `message setup` reports its
  own notmuch step as a step left, and `ears services install` reads its
  rows, not its exit.
- **Where it comes from today:** Homebrew.

### ffmpeg

Status: pending a ruling. What the family needs ffmpeg for is being
researched.

- **What uses it:** `mouth play` of a sound file; `mouth render` to an
  `.mp3` or `.m4a` file; measuring a voice's loudness, so the mouth can
  level it; `ears file`; `ears eval`.
- **Without it:** `mouth play` ends the line with an error and plays
  nothing; `mouth render` cannot write `.mp3` or `.m4a`; a voice not yet
  measured is not levelled, silently; `ears file` and `ears eval` fail. `make test` skips
  the tests that need it, and says so in its summary.
- **Where it comes from today:** Homebrew.

## Upgrade

```bash
uv tool upgrade sessionkit             # or: uv tool upgrade sessionvoice
ears services uninstall --only speech
ears services install --agent NAME     # or --no-agent: what the first install used
ears services schema                   # the settings-schema copies
```

- **The first line** upgrades sessionvoice and its Python packages, and keeps
  the Python the install chose. Use `uv tool upgrade sessionkit` when the
  kit installed sessionvoice. From a checkout, pull, then run the same
  `uv pip install -e` again.
- **The next two** apply only when sessionvoice runs its own speech server
  (`ears services status` says `(ours)` on its `speech` row). While that
  server runs, a re-install only uses it, so remove it first; install then
  re-applies the new pins to its venv, which uninstall keeps. A bare
  `ears services install` is refused when the wake file names no seat, as
  on a Mac first installed with `--no-agent`, so give it the flag the first
  install used. A server someone else installed is theirs to upgrade.
- **The last line** brings the settings-schema copies up to date with the
  new schemas, so a settings screen does not refuse a key the new version
  writes ([the schema copies](settings.md#the-files)). A copy that
  already matches is left alone. The keeper does it at its next start too;
  this line does it now.
- **The speaker side-car**, if you installed it: `ears services install
  --only speakers` again.
- **The plugin:** `claude plugin update sessionvoice@MARKETPLACE`, then
  restart Claude Code.

[uv-lock]: ../uv.lock
