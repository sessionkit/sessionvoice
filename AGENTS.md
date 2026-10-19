# AGENTS.md

Instructions for any coding agent working in this repo.

## What sessionvoice is

One listening engine and one speaking queue for coding agents. Read
[README.md](README.md) first; [docs/architecture.md](docs/architecture.md)
shows how the parts fit.

- **The ears** (`src/ears`): audio in, a speech-to-text backend (`apple`,
  `nemotron` or `whisper`), one VAD and a turn stage, all writing one event
  stream.
- **The mouth** (`src/mouth`): a queue of lines, spoken one at a time in a
  TTS voice.
- **talk** (`src/talk`): the live conversation in a browser.

It ships as a Python distribution (`pyproject.toml`) and as a Claude Code
plugin (`.claude-plugin/`, `commands/`, `skills/`).

## Build and test

Python 3.10-3.14 and [uv](https://docs.astral.sh/uv/).

    make sync                  # uv sync --locked: the dev group, from uv.lock (once)
    make test                  # uv run --locked pytest: the whole suite, offline
    make test ARGS="-k mouth"  # pytest flags pass through
    make scan                  # the pre-publish scrub check (needs SESSIONKIT_SCAN)

- Run `make test` before you hand anything back. It needs no network and
  reaches no audio device.
- Everything runs `--locked`. To change a dependency, edit `pyproject.toml`,
  run `uv lock`, and commit `uv.lock` with it.

## Refer to the specs before building

The docs in `docs/` are the contracts. Read the one for the part you are
changing first. If your change alters a contract, change the doc in the same
commit.

- [docs/events.md](docs/events.md): the event stream, v1. Consumers ignore
  unknown fields and types and never fail on them.
- [docs/audio-devices.md](docs/audio-devices.md) and
  [docs/audio-devices.schema.json](docs/audio-devices.schema.json): the
  audio devices file. `tests/test_audio_devices_schema.py` holds the page, the
  schema and the writer to each other.
- [docs/ears.md](docs/ears.md), [docs/mouth.md](docs/mouth.md),
  [docs/models.md](docs/models.md) and [docs/call-tap.md](docs/call-tap.md):
  how each part behaves.

## Rules

- **Tests never touch the real machine.** `tests/conftest.py` fences every
  test: no writes under the real home (`tests/home_guard.py`), no real audio
  device (`tests/audio_fence.py`), no mail (a refusing `message`). Never
  weaken a fence to make a test pass; give the test a fake
  (`tests/fixtures/`).
- **So do review rigs and scripts.** Outside pytest none of those fences
  hold. Before a rig runs any mouth code, set HOME to a temp dir and
  `MOUTH_SENDMAIL` to `python3 tests/fixtures/refuse_sendmail.py` (or
  `MOUTH_NOTICE=off`). These were `VOICEMODE_MOUTH_SENDMAIL` and
  `VOICEMODE_MOUTH_NOTICE` before the rename. The old names are still read, but
  the new name wins when both are set, so a rig that sets only the old name is
  bypassed by an exported `MOUTH_SENDMAIL`. Set the new name. An imported mouth is refused the real
  `message`, but a player the rig starts (`mouth say`, `mouth serve`), and a
  `mouth retract` or `mouth again` it runs, are the real service and mail
  for real. The fixture stands in for sessionmail's `message send`. A rig
  that runs `keeper/ears-mail.py` without `--dry-run` sets
  `EARS_MAIL_MESSAGE` to it too.
  New code that writes a said row outside the player fails
  `test_every_said_row_writer_is_accounted_for`. If a CLI verb runs it in its
  own process, add that verb to `mouth.__main__.SERVICE_VERBS`.
- **Tests stay offline.** Nothing fences the network, so keep it that way by
  hand: drive httpx through `httpx.MockTransport` or a fake client, never a
  real server.
- **This repo is public.** Keep private names, hostnames, home paths and
  internal infrastructure out of code, docs and commit messages. `make scan`
  checks for them. The scanner and its word list are private: never commit
  them here.
- **Record user-facing changes** in `CHANGELOG.md` under `[Unreleased]`
  (Keep a Changelog).
- **Never rewrite a CHANGELOG bullet that has landed on master; add a new
  one.** `CHANGELOG.md` merges with `merge=union`, which keeps both versions
  of an edited bullet silently instead of conflicting.
- **Work on a branch.** The owner reviews and merges.
