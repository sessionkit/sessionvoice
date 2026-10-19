# The audio devices file, v1

One JSON file that says which audio devices this Mac has, inputs and outputs,
and which ones are the defaults. One long-lived process writes it, from Core Audio
property listeners. Any package can read it instead of asking Core Audio itself.

Why: every `SwitchAudioSource` or `system_profiler` run is a new Core Audio
client, and each one costs `coreaudiod`. A listener costs about nothing. Reading
a file costs `coreaudiod` nothing at all.

**The machine contract:** [`audio-devices.schema.json`](audio-devices.schema.json)
(JSON Schema 2020-12). This page and that schema list the same fields, and a test
(`tests/test_audio_devices_schema.py`) holds the two to each other and to what the
writer actually writes.

## Where it is

    ${XDG_STATE_HOME:-$HOME/.local/state}/audio/devices.json

`AUDIO_DEVICES_FILE` (a full path) overrides it. It is state other packages read,
so it lives under the subject (`audio/`), not under its writer.

## Who writes it

`keeper/input-watch.swift` (`input-watch STATE_FILE --json JSON_FILE`). It
listens on the device list, the default input and the default output. One burst
of notifications (AirPods leaving changes the list and both defaults within
milliseconds) becomes one read, 100 ms after the last of it. It never spawns a
process.

## Fields

One UTF-8 JSON object, sorted keys, ending in a newline.

| field | type | meaning |
|---|---|---|
| `version` | integer | Schema major version: **1**. Bumped only for a breaking change. A field added later is not breaking and does not bump it. |
| `writer` | string | `"input-watch"`. |
| `pid` | integer | The writer's process id. The staleness rule (below) runs on it. |
| `started_at` | number | When this writer process started: seconds since the Unix epoch, 3 decimals (ms). |
| `written_at` | number | When this file was written: seconds since the Unix epoch, 3 decimals (ms). The writer writes on a change (or to re-assert its file, below), never as a heartbeat, so an old `written_at` is not a stale file. |
| `seq` | integer | 1 for this writer's first write, then up by one on each write. A reader can tell "changed since I last looked" without comparing bodies. |
| `default_input` | device or null | The default input (`kAudioHardwarePropertyDefaultInputDevice`). `null` when there is none. |
| `default_output` | device or null | The default output (`kAudioHardwarePropertyDefaultOutputDevice`), the one `SwitchAudioSource -c -t output` names. `null` when there is none. |
| `inputs` | device[] | Every device with input streams. Sorted by `name`, then `uid`. |
| `outputs` | device[] | Every device with output streams, the set `SwitchAudioSource -a -t output` lists. Sorted by `name`, then `uid`. |
| device `.name` | string | `kAudioObjectPropertyName`: the name `SwitchAudioSource` and the ears keeper match on. |
| device `.uid` | string or null | `kAudioDevicePropertyDeviceUID`, stable across reboots. `null` if the device gives none. |
| device `.transport` | string | From `kAudioDevicePropertyTransportType`: one of `builtin`, `bluetooth`, `usb`, `virtual`, `aggregate`, `hdmi`, `displayport`, `airplay`, `thunderbolt`, `pci`, `firewire`, `avb`, `continuity`, `unknown`. |

A device with both input and output streams is in both lists.

Example:

    {"default_input":{"name":"MacBook Pro Microphone","transport":"builtin","uid":"BuiltInMicrophoneDevice"},
     "default_output":{"name":"MacBook Pro Speakers","transport":"builtin","uid":"BuiltInSpeakerDevice"},
     "inputs":[{"name":"MacBook Pro Microphone","transport":"builtin","uid":"BuiltInMicrophoneDevice"}],
     "outputs":[{"name":"MacBook Pro Speakers","transport":"builtin","uid":"BuiltInSpeakerDevice"}],
     "pid":81234,"seq":7,"started_at":1921356800.104,"version":1,"writer":"input-watch",
     "written_at":1921356856.552}

(The file is one line; it is wrapped here to read.)

**Readers MUST ignore unknown fields**, as in [`events.md`](events.md). The
schema says `additionalProperties: false` so the writer's test catches drift in
either direction; a reader stays lenient.

## Fresh or stale: the rule every reader follows

The file is **fresh** when all three hold, and **stale** otherwise:

1. it exists and parses as a JSON object;
2. `version` is an integer equal to the version the reader knows (1);
3. the process `pid` is alive: `kill(pid, 0)` succeeds, or fails with `EPERM`.

**Stale means: do your own read**, exactly as you did before this file existed
(for the ears keeper, `SwitchAudioSource`). Never act on a stale file.

Why the writer's liveness and not the file's age: the file is written on change,
so a file six hours old is right if nothing changed in six hours. What can go
stale is the writer, and a live writer keeps its file true:

- Listeners deliver a change at once: well under a second, plus the 100 ms
  coalescing.
- **The 30 s backstop.** Every 30 s the writer re-reads Core Audio in-process
  (no spawn) and writes only if something changed, so a missed notification
  leaves a live writer at most 30 s wrong.
- **The 30 s own-file check.** On the same tick it reads the file's `pid` (one
  file read: no Core Audio, no spawn). If the file is missing, or names any pid
  but its own, it rewrites its current snapshot with the next `seq`; otherwise it
  writes nothing. Over a pid that is still alive it also says so on stderr. So a
  file another process replaced or deleted is the live writer's again within 30 s.
- A writer that died leaves a file with a dead `pid`: stale by rule 3. The
  keeper restarts the watcher, and the new writer's first snapshot rewrites the
  file with its own pid.
- `kill -0` is a bash builtin and `os.kill(pid, 0)` a syscall: the check spawns
  nothing.

Known gap, accepted: pid reuse. If the writer dies, nothing restarts it, and an
unrelated process later gets its pid, a reader would take the dead file for
fresh. Closing that needs a spawn (`ps`) or a heartbeat.

## Readers

- **The ears keeper's output check** (`keeper/ears-keeper.sh`, `audio_outputs` and
  `check_output`) reads `default_output` and `outputs` from this file while it
  is fresh, and spawns no `SwitchAudioSource` at all. When the file is missing or stale
  (the rule above), it asks `SwitchAudioSource -c`/`-a` instead, exactly as before. It is
  stricter than the rule, never looser: it also counts the file as stale when `outputs` is
  missing or not a list, when an entry of `outputs` is `null`, when a device (an output or
  `default_output`) has no string `name`, or when a name holds a newline (its answer is one
  name per line). Setting
  the output is always `SwitchAudioSource -s`. The keeper log gets one line each time the
  source flips between the file and `SwitchAudioSource`, not one per check. The keeper starts
  the writer with `--json` and honours `AUDIO_DEVICES_FILE`.
- Its input path (`inputs()`, `default_input()`) still reads the legacy file below.
- Other readers (`settings` `apply.py`/`web.py`, `audio-state`, `keeper/speak`) have not
  been cut over: each is a separate change.

## Writing it: what any writer does

- **On change only.** A change is anything in `default_input`, `default_output`,
  `inputs` or `outputs`. The stamps (`pid`, `started_at`, `written_at`, `seq`) are
  not compared. A writer's first snapshot always writes.
- **Atomically:** write `<path>.tmp.<pid>` in the same directory, then `rename(2)`
  it onto `<path>`. A reader sees the old file or the new one, never part of one.
  Mode 0644.
- **Never delete it** on exit. A successor may already have written it; a dead
  writer is told apart by its `pid`.

### One-shot runs never replace a live writer's file

`input-watch --once` (one Core Audio read, write, exit) and `input-watch
--fixture` (the tests' seam: no Core Audio at all) **refuse**, exit 3, and write
nothing, when the file at their `--json` path names a live pid that is not their
own. Pointed at a missing file, or one whose pid is dead, they write it. A
one-shot's own file is stale the moment it exits (its pid is dead), which is
right: nothing keeps it true.

## The legacy file

`input-watch STATE_FILE` still writes the plain-text file the ears keeper's input
path reads: line 1 the default input's name (empty if none), then the input
device names, deduped and sorted, one per line. It is written after the JSON
file, and only when its own text changes. It has no version and no field names:
new readers use the JSON file.
