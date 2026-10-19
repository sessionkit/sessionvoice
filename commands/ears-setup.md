---
description: Set up ears - hear the user (the room, or a phone call) and speak back, on this Mac
---

Set up the sessionvoice plugin on this machine, step by step. Report each step's result in one line, and stop and say so plainly if a step fails.

1. **Check the machine:** `ears backends`. The default engine is Parakeet on the mlx-audio speech server at 127.0.0.1:8890, the same server the mouth's Kokoro voice runs on. `ears services install` (step 3) finds one that is already running or installs its own. Any OpenAI-compatible URL works instead, set as `EARS_STT_URL` at install; then sessionvoice leaves :8890 alone.
2. **Install the assets:** `ears install`. This fetches the Silero voice-activity model. `ears install --apple` also builds the Apple speech helper, which needs the Xcode command-line tools (`xcode-select --install`), and fetches Apple's speech model for the locale.
3. **Start the services:** `ears services install`. Run `ears services install --dry-run` first and report what it says it would do. This loads these launch agents:
   - `speech` is the mlx-audio server on 127.0.0.1:8890. If a server there says it is mlx-audio (one VoiceMode installed, for example), install uses it and starts nothing. If nothing is there and no mlx-audio job is loaded, install builds its own venv and loads `dev.sessionkit.voice.mlx-audio` (an Apple-silicon Mac, macOS 14 or later). If anything else holds :8890, install refuses, names the holder and starts nothing: tell the user, and never stop or replace that process yourself. VoiceMode's own installer does not check, so do not run `voicemode service install mlx-audio` after this: it would load a second server.
   - `mouth` is the player that speaks queued lines.
   - `keeper` is every ear: the always-on room microphone, and an ear for each Delta Chat call when a Delta bot is installed. While a call is live, the room microphone is off. **Ask the user before installing it.** macOS will ask them to allow the microphone once. Without it: `ears services install --only speech,mouth`.
   - **Tell the user:** lid shut: the built-in mic is off in hardware; use Bluetooth buds or a USB mic. With the lid shut, the keeper does not listen on the built-in mic. It sends one mail, `ears: DEAF, lid shut, built-in mic off, holding at HH:MM EOM`, and holds until another input becomes the Mac's default or the lid opens.
   - `--apple` adds `stt`, Apple speech behind an OpenAI-compatible URL on 127.0.0.1:2023. Use it only after `ears install --apple`.
4. **Check:** run `ears services status`, then `mouth say --device default "ears are up"`. The user should hear that line. `mouth say` needs a device: `--device NAME`, else `$MOUTH_DEVICE`, else `output` in `~/.sessionkit/voice/settings/mouth.json`. With none of them it refuses the line.
5. **Tell the user in two lines:** what is running, and how to stop it (`ears services uninstall`).

Transcription stays on this machine: Parakeet on mlx-audio, or Apple's on-device model with `--apple`.
