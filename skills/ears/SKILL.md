---
name: ears
description: >-
  Ambient voice for any agent: hear the user through the shared ears (the
  heard log) and speak through the shared mouth, with the etiquette of who
  wakes when. Use when an agent should answer to its name in the room, keep
  a listener armed across turns, know which wake rules to drop in a shop, on
  a call, or while the user thinks aloud, and speak one line at a time on
  the user's device.
tokens: 101/2825/7583  # seed/body/extras - run `skill tokens` after edits
---

# ears

> **The ears are the room.** One capture on this machine hears everything
> the user says and writes it to the heard log,
> `<home>/logs/conversations/heard_<date>.jsonl`, where `<home>` is
> `$SESSIONVOICE_HOME`, else `~/.sessionvoice`, or `~/.voicemode` on a host
> that already keeps the log there. On a fresh host that also runs VoiceMode's
> `converse`, set `SESSIONVOICE_HOME=~/.voicemode` (or `VOICEMODE_BASE_DIR`), so
> that both write one log. Every agent in the room reads the same log;
> nobody owns it. Converse is someone dialling in; the ears are being there.

## Power moves

`ears-arm` and `ears-count` are on PATH by bare name, through the plugin's
`bin/`; `scripts/ears-arm` and `scripts/ears-count` still work. The rest run
from `scripts/`.

- `ears-arm <seat> [talk|alone|room] [names-regex] [--dry-run]`:
  arm the ONE waiter for your seat, in the right mode. Run it with the Bash
  tool's `run_in_background: true`, never a trailing `&`. It execs
  `heard-first`; when it fires, read its output, then run it AGAIN (re-arm
  on every fire). The mode defaults to `talk`; the names regex defaults to
  your seat name. `--dry-run` prints the command and arms nothing.
- `ears-count <seat>`: how many waiters are up for your seat, by
  the `cut:<seat>` rule, so one armed by hand with another state file counts
  too. Run it BEFORE arming; two waiters means two wakes per line. One,
  always.
- `scripts/ears-mid-turn [S]`: fires once the user has talked S seconds
  (default 8) in one turn, with the partials so far, so you can start on a
  long ask before it ends. Arm it only while you are in conversation;
  re-arm only on its own fire.
- `scripts/ears-mouth-tail [--back N] [--heartbeats]`: one live view of
  what the ears heard and what the mouth said, for debugging.
- `mouth say --device <the user's device> --voice <yours> --hold turn-end --expires 300 "..."`:
  speak one line. Pass `--voice <yours>`, or set it once (docs/mouth.md,
  "Your voice") and leave it off. `--interest high` for a question (the
  reply wakes you sooner). `mouth amend <utt> "..."` rewrites it while
  queued;
  `mouth status` shows what is playing; `mouth devices` lists the device
  names `--device` takes. The `said` record in the heard log says `cut` and
  why.
- `mouth duck off --for 30m` (or `on`): music stops dipping under speech
  for a while (karaoke), then the setting applies again. `mouth duck`
  shows the state and time left; `mouth duck reset` ends it now. A
  per-call `mouth play --duck`/`--no-duck` still beats it.
- `mouth play FILE --device <the user's device>`: to play music or a
  sound for the user, use `mouth play`, never a separate player. It ducks
  under speech and mixes with the mouth's lines. `--start`/`--end` take a
  section, `--volume N` its level, and `mouth retract <utt>` stops it
  (docs/mouth.md, "Play music or a sound").
- `SwitchAudioSource -c -t output` (if installed) names the user's output
  device now: a headset, or the built-in speakers. A headset vanishing from
  the list usually means it moved to a phone: a call.

## The modes (which rules to drop, and when)

Every rule wakes you on the FIRST line it matches. Fewer rules, fewer
wakes.

- **`talk`**: the user is talking with you. Rules: turn, your name, `?`,
  cut, reply.
- **`alone`**: the user is thinking aloud, or answering at length. Rules:
  turn, your name, cut.
- **`room`**: other people, a shop, a call, driving alone. Rules: "hey" plus
  your name only (partial and turn), cut. Not the bare name: a mention to
  someone else in the room must not wake you. Speech-to-text hears "hey" as
  hey, hay, or a; the script covers all three. Set your own with
  `EARS_OPENERS`, and `EARS_NAME_FIRST=1` to also wake when your name opens
  the user's turn ("Carol, ..."). The measured traps:
  - `and` as an opener wakes you on a mention: "some stuff for you and
    Carol" is not a call to Carol.
  - `a` wakes a name that is a common noun: "a scout" is not a call to an
    agent named Scout. If your name is a word, arm with
    `EARS_OPENERS='hey|hay'`.
  - **"Hey everyone" wakes every seat, by default**, like @here in a chat.
    The words are `everyone|everybody`; `EARS_EVERYONE='...'` replaces
    them and `EARS_EVERYONE=` turns it off. Your own names and openers are
    extras on top of the standard, not a replacement for it.
  - **The user's reply to your last line wakes you** (`reply:<seat>`, on by
    default in room mode). It only fires when YOUR line was the last one
    spoken, so the answer to your question reaches you with no name in it.
    `EARS_REPLY=0` turns it off.
  - **Linger, `EARS_LINGER=300`**: every turn the user finishes wakes you,
    until they have said nothing for 300 s. Use more than a minute: people
    talk for more than a minute. The clock restarts on each of their lines
    (heard-first `--linger`), so a long turn is never cut off. Then the arm
    exits 124, and you re-arm without it. Arm it after each exchange.
    `EARS_LIGHT` is the old name, and it still works.
- **Any mode, `EARS_SPEAKERS='alice,bob'`**: only a line the speaker
  side-car names as one of those people wakes you. Use it when a machine's
  own mouth, or someone else, could say your wake words.

- `?` (`partial:\?`) fires on questions as they form. It is right when the
  user is asking you; it is false more than half the time (56%, measured)
  when they are talking to themselves. Drop it the moment you can tell
  they're alone.
- `reply:<seat>` wakes you on the answer to your last line. Drop it during
  a long reply, or every pause wakes you.
- Bare `turn` fires on EVERY turn. In a shop that is every word to the
  shop assistant. `room` mode drops it: only "hey" and your name reach you.
- Say which mode you are in when you change it; users want to know.

### More than one agent in the room

- **When the user talks with more than one agent, `room` is the
  default.** People talk ABOUT an agent as often as TO it: "Carol's written up
  some skills" should not wake Carol.
- **A waiter keeps the rules it was armed with.** After this skill changes,
  kill yours by pid and re-arm, or you are on the old rules.
- **Being mentioned isn't being asked.** If a mention reaches you anyway (a
  ride-along line mid-work), don't answer it.
- **Riding along is a hook, not the waiter.** Every mic line reaches your
  session only if your launch directory's `.claude/settings.local.json`
  runs the heard hook on `PostToolUse` and `PostToolBatch`: with the ears
  package, `ears heard hook || exit 1` (the `ears` command must be on the
  hook's PATH, not just the Bash tool's; see `ears heard --help`). It takes
  effect on your next tool call, with no restart. It prints `[heard ...]`
  lines after each tool call. It has no sieve yet: every agent that has it
  sees every line. Waking stays `ears-arm`, so you can ride along on
  everything and wake only on your name. A debug view's "X's waiter read
  it" means the waiter scanned the line, not that it was delivered; "rode
  to X" is the hook delivering it.

## Speaking: the rules that cost the user nothing

- **One line in flight.** Three queued lines cut the user off; one
  repeated. If you must add, `mouth amend` the queued one.
- **Hold to turn end.** `--hold turn-end` waits for the user's turn to
  finish; a line spoken over them is a line they never hear.
- **A cut is on you.** The `said` record carries `cut: true` and a reason:
  `barge-in` (the user spoke over you: say it again, shorter),
  `device-lost` (their headset dropped), `device-absent` (you named a
  device that isn't there: `mouth devices`).
- **Chopping has a number.** A played `said` row carries `device_gaps`,
  `device_gap_s` and `device_gap_max_s`. `device_gaps > 0` means the machine
  was too busy to feed the audio (its callback ran late), not the line or the
  voice. `underruns` is different: synthesis fell behind. Both are 0 on a
  clean line; on `null` and `call:` the `device_gap*` fields are always 0.
- **Speak only when something changed.** No sign-offs, no "still
  listening".
- **Identifiers are unspeakable.** Say what it means; put the id in writing.
- **A list goes in writing**, not speech; people read a list, they can't
  repeat one back.

## Reading the room from the log

- A `device` change from a headset to the built-in microphone mid-sentence:
  the headset went to a phone call. Go to `room` mode; speak on `default`
  only if the user calls you.
- `event` records with no text every 15 s: silence. The user is away or
  listening.
- "Hello?" on the built-in mic after a headset vanishes: they're on the
  phone.
- Self-talk has "what was I gonna say?", "so...", lists said to no one.
  That's `alone`.
- The user says your name: back to `talk`. Restore the rules you dropped.

## Where the state lives

`ears-arm` keeps the waiter's place in `<your home>/heard.state`, so a
re-arm catches up on what landed while it was down (no gaps). Your home is
`$EARS_HOME` if set; else a session-fleet seat home
(`~/.session/agents/home/<seat>`) if one exists; else `~/.<seat>`; else
`~/.ears`.

## Not in this skill

- **The capture**: one per machine, run as a service, not by an agent.
  With the sessionvoice plugin, `/ears-setup` walks through it: `ears backends`,
  `ears install`, then `ears services install` (`--only stt,mouth` first;
  `room` is the always-on microphone, so ask the user; `call` only with a
  Delta Chat bot). `ears services status` shows what runs; `ears services
  uninstall` stops it. If the heard log stops growing, check
  `ears services status` first.
- **Converse** (VoiceMode's `converse` tool) is the other way to talk: it
  takes the conch and does not know about the ears or the mouth.
