# How the ears and the mouth work

Hearing and speaking are separate processes joined by one log and one
queue. Every ear writes its rows to the heard log; a waiter reads the log
and wakes the agent; the agent queues its reply for the mouth, and the mouth
speaks it and writes its own rows back to the log, so its echo is never
taken for the user speaking.

Two diagrams: a **data flow** (what moves where) and a **sequence**
(one spoken turn on a Delta call, in time order). Drawn on 2030-06-09 from
the processes running on one Mac at that time. Where the code lived then is on the right of each box.
That part is historical: the capture and the mouth have since moved into this
repo (`ears.listen capture` and `mouth mail`), and `ears services install` runs
them as launch agents. The design, the log and the queue are as drawn.

Five diagram types would each answer a different question. This page has
the two that explain the design best:

- **Context** (C4 level 1): the whole system as one box, and the people
  and devices around it. Written for users.
- **Data flow** (this page): audio in, text through, audio out.
- **Sequence** (this page): one turn in time, which shows the delays and
  the echo.
- **State machine**: the waiter and the mouth, armed, in a turn, holding.
- **Component / deployment**: which process, which launch agent, which
  socket.

## 1. Data flow

```mermaid
flowchart TB
  subgraph IN["Hearing"]
    mic["Room mic<br/>(AirPods or MacBook)"]
    phone["The user's phone<br/>Delta Chat call"]
    bot["Delta bot<br/>calls.py"]
    keeper["ears-keeper<br/>keeper/ears-keeper.sh"]
    callears["call-ears agent<br/>~/.#lt;agent#gt;/tools/call-ears"]
    capR["capture, source mic<br/>voice_mode.listen"]
    capC["capture, source call:N<br/>one per call"]
    stt["Whisper STT<br/>whisper-server :2022"]
    side["speaker side-car<br/>names alice / bob / carol"]
  end

  subgraph LOG["Shared record"]
    heard[("heard log<br/>heard_DAY.jsonl")]
    spk[("speakers_DAY.jsonl<br/>keyed by seq")]
  end

  subgraph THINK["Deciding"]
    waiter["heard-first<br/>the waiter: rules,<br/>echo gate, speaker gate"]
    agent["Agent seat<br/>Claude Code (carol, dave)"]
  end

  subgraph OUT["Speaking"]
    q[("mouth queue<br/>maildir ~/.mail/agents/mouth")]
    player["mouth player<br/>launchd mouth-mail"]
    tts["TTS :8890<br/>Kokoro, or a cloned voice"]
    spkr["Speaker<br/>(AirPods)"]
  end

  mic --> keeper --> capR
  phone -- "WebRTC audio" --> bot
  bot -- "call-ears.sock" --> capC
  callears -. "starts on a new call" .-> capC
  capR <--> stt
  capC <--> stt
  capR -- "partial, turn, vad rows" --> heard
  capC -- "partial, turn, vad rows" --> heard
  mic --> side --> spk
  heard --> waiter
  spk --> waiter
  waiter -- "exits with the row:<br/>wakes the agent" --> agent
  agent -- "mouth say" --> q --> player
  player <--> tts
  player -- "saying, said rows" --> heard
  player -- "device default" --> spkr
  player -- "device call:<br/>call-mouth.sock" --> bot
  bot -- "WebRTC audio" --> phone
```

How to read it:

- **Every ear writes to one log.** The room capture and each call capture
  write the same row format ([events.md](events.md)) into the same file. The
  only difference is `source` (`mic` or `call`) and `device`
  (`airpods`, `call:1169`).
- **The mouth writes to the log too.** Its `saying` and `said` rows are
  how the waiter knows which heard lines are its own echo.
- **The waiter is the only part that wakes an agent.** It is a process
  the agent starts in the background; it tails the log and exits on the
  first row that passes its rules (`turn`, `partial:NAME`, `reply`,
  `cut`). Exiting is the wake.
- **Nothing talks to the agent directly.** Hearing and speaking are
  separate processes joined by a log and a queue, so any agent can use
  them, and one can be restarted without the other.
- **The call ear is started by the call-ears agent.** A new call gets
  its own ear, and that ear exits when the call ends. The bot also keeps
  its own transcriber, which posts each turn to mail.

## 2. Sequence: one turn on a Delta call

```mermaid
sequenceDiagram
  autonumber
  actor User as User (phone)
  participant Bot as Delta bot
  participant Ear as call ear
  participant STT as Whisper :2022
  participant Log as heard log
  participant Wait as heard-first
  participant Agent as Agent seat
  participant Mouth as mouth player
  participant TTS as TTS :8890

  Agent->>Wait: arm (background), rule turn
  User->>Bot: speaks
  Bot->>Ear: PCM over call-ears.sock
  loop while the user speaks
    Ear->>STT: audio so far
    STT-->>Ear: partial text
    Ear->>Log: partial row
  end
  Note over Ear: Silero VAD hears silence and cuts the turn
  Ear->>STT: re-decode the whole turn
  Ear->>Log: turn row
  Log-->>Wait: turn row read
  Note over Wait: echo gate: is it the mouth's own line?<br/>speaker gate: is it the user?
  Wait-->>Agent: exits with the row (the wake)
  Agent->>Log: ears-turn: read the whole turn
  Note over Agent: think and write the reply
  Agent->>Mouth: mouth say --device call --hold turn-end
  Note over Mouth: hold: wait until the user's turn has ended
  Mouth->>Log: saying row
  Mouth->>TTS: POST /v1/audio/speech
  TTS-->>Mouth: audio, streamed
  Mouth->>Bot: audio over call-mouth.sock
  Bot->>User: hears the agent
  Mouth->>Log: said row (played_s)
  Agent->>Wait: re-arm
```

### Where the time goes (call 1169, about 9 minutes, 2030-06-09)

From the end of the user's turn to the mouth asking for speech, for three
turns in that call:

- turn row to TTS request: **20 s**.
- turn row to TTS request: **27 s**.
- turn row to TTS request: **96 s**. A long answer; the user spoke again
  70 s after the turn row and the hold waited for that turn to end.

The ear cuts a turn about 1.6 to 1.8 s after the user stops (README,
"Measured"). So **almost all the delay is the agent**: reading the turn,
thinking, and writing the reply. A faster ear cannot fix that; a short
spoken opener ("Mm, good question") queued first, then amended, can.

## 3. Room and call, side by side

How does one turn differ between the room and a Delta call? Only at the
ends. Where they
differ, the diagram splits into two branches, **room** and **call**:

```mermaid
sequenceDiagram
  autonumber
  actor User
  participant Room as Room audio<br/>(AirPods or MacBook)
  participant Bot as Delta bot
  participant Ear as ear (capture)
  participant Log as heard log
  participant Wait as heard-first
  participant Agent as Agent seat
  participant Mouth as mouth player

  alt In the room
    User->>Room: speaks
    Room->>Ear: CoreAudio input, source mic<br/>one ear, always on (ears-keeper)
  else On a Delta call
    User->>Bot: speaks into the phone (WebRTC)
    Bot->>Ear: PCM over call-ears.sock, source call:N<br/>one ear per call (call-ears agent)
  end
  Note over Ear,Log: the same from here: Silero VAD, Whisper, re-decode
  Ear->>Log: turn row
  Log-->>Wait: turn row read
  alt In the room
    Note over Wait: room rules: "hey Carol", the seat's name first,<br/>a reply to my last line, and a speaker gate (alice, bob)
  else On a Delta call
    Note over Wait: rule "turn": every finished turn is for me
  end
  Note over Wait: echo gate on both: skip my own voice
  Wait-->>Agent: exits with the row (the wake)
  Agent->>Mouth: mouth say --hold turn-end
  alt In the room
    Mouth->>Room: --device default: AirPods output
    Room->>User: hears the agent in the AirPods
  else On a Delta call
    Mouth->>Bot: --device call: call-mouth.sock
    Bot->>User: hears the agent on the call
  end
```

Leg by leg:

- **In.** Room: the input device the keeper picks (AirPods, else the
  MacBook mic), one ear that is always on. Call: the bot decodes the
  call audio to 16 kHz PCM, and the call-ears agent starts an ear for
  each call.
- **Who is it for.** The room has other people in it, so the waiter
  needs my name ("hey Carol") or a reply to my last line, and a speaker
  gate. A call is one to one, so every finished turn is the user's.
- **Speaker names.** The side-car reads the room mic only. On a call a
  line has no name (`speaker_gate: no-name`), which is why the call
  waiter does not use `--speakers`.
- **Echo.** In the room the mic can hear the mouth; on a call the user's
  phone cancels most of it. The waiter's echo gate runs on both, because the
  mouth's `saying` rows are in the same log.
- **Out.** Room: CoreAudio, to the AirPods. Call: `call-mouth.sock`;
  the bot sends what is queued as call audio, paced at 20 ms, and
  silence otherwise.
- **The middle is one path:** the same capture code, log, waiter,
  agent and mouth queue. That is why the call ear was a small change.

## Where each part lived (historical: one Mac, 2030-06-09)

The snapshot the diagrams were drawn from, kept as it was but for one name: the
mouth's launch agent carries the label later measured live, not the one first
written here. Today the capture and the mouth run from this repo; see
[ears.md](ears.md) and [mouth.md](mouth.md).

- Room capture: started by `keeper/ears-keeper.sh`; runs
  `voice_mode.listen capture` from a VoiceMode task worktree of the listen
  spike.
- Call capture: `~/.<agent>/tools/call-ears` (launchd `com.failmode.call-ears`),
  the same capture with code from `~/.<agent>/wt/vm-call-source`.
- Waiter: `heard-first` and `ears-turn` in `~/.local/bin` (branch
  `feat/waiters`).
- Speaker names: `~/.<agent>/speaker-sidecar/speaker-sidecar2.py` (branch
  `feat/speaker-names`).
- Mouth: `voice_mode.mouth` from `~/.<agent>/wt/voicemode-mouth`
  (`feat/mouth-say`), run by a hand-made launch agent, `com.failmode.mouth-mail`.
- Delta bot: `~/.<agent>/delta-chat/bot/` (an earlier bot checkout),
  launchd `com.failmode.<agent>-chat`.
