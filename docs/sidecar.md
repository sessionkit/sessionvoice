# The audio sidecar: a song's stems and instrumental, in the folder beside it

A played file's separated parts live in a hidden folder next to it, its
sidecar: `.<file>.d/`, a dot, then the file's full name with its extension.
`mouth split FILE` writes one layout there, four stems, the instrumental and
a `split.json` manifest. `mouth play` reads it, and so does `mouth play
--stem`. The mouth also reads, and never moves or writes, three older layouts
that other tools leave on disk. This page is the contract for any tool that
reads or writes these folders. How the mouth plays them is in
[mouth.md](mouth.md#play-music-or-a-sound).

## What `mouth split` writes

For a played file `<dir>/<name>`, where `<name>` keeps its extension and
`<dir>` is the real folder (links resolved first):

```text
<dir>/.<name>.d/                 e.g. .01-song.m4a.d/
  stems/vocals.flac
  stems/drums.flac
  stems/bass.flac
  stems/other.flac
  mixes/instrumental.flac        drums + bass + other
  split.json                     the manifest: what made them, from which bytes
```

- **FLAC, 16-bit, 44.1 kHz, stereo.** The instrumental is the sum of the
  `drums`, `bass` and `other` stems. If any of the five would clip, all five
  are scaled down by one common gain, so the stems still sum to the
  instrumental; the manifest records it as `peak_scaled_db`.
- **All at once.** A split writes into `.<name>.d/.split-<pid>/`, then
  renames `stems/`, `mixes/` and `split.json` into place. An older set is
  moved aside to `.old-<pid>/` first and removed after. A reader never sees
  half a set, and a split that fails leaves none. A later split removes any
  `.split-*` or `.old-*` folder left behind.
- **Someone else's folder is left alone.** A `.<name>.d/` that holds
  `stems/` or `mixes/` with no `split.json` was filled by hand or by another
  tool. `mouth split` refuses it (exit 2) unless `--force` is given.
- **Other files are kept.** Only `stems/`, `mixes/` and `split.json` are
  replaced; anything else in the folder is never touched.

## The manifest, `split.json`

One JSON object. A reader ignores keys it does not know.

| Key | What it holds |
| --- | --- |
| `version` | `1`, the layout this page describes. |
| `separator` | `demucs`. |
| `model` | `htdemucs` ([models.md](models.md#htdemucs)). |
| `signature` | The model file's signature, `955717e8`. |
| `versions` | The separator's package versions, such as `demucs` and `torch`. |
| `device` | Where it ran: `cpu`. |
| `split_s` | Seconds the split took. |
| `made_t` | When it was made, Unix seconds. |
| `source` | The bytes it was made from: `name`, `size`, `mtime_ns`, `duration_s`. |
| `outputs` | Each output's path inside the folder: `vocals`, `drums`, `bass`, `other`, `instrumental`. |
| `levels_db` | RMS levels in dB, measured before any scaling: `mix`, `vocals`, `instrumental`. |
| `peak_scaled_db` | The common gain applied to keep the outputs from clipping, or `null`. |
| `verdict` | `music`, or `speech` when the instrumental is 20 dB or more below the mix. |

- **Staleness.** When the source's size and `mtime_ns` match `source`, the
  outputs are current. When they differ, the source's duration is probed
  (`ffmpeg -i`). Within 1 s of `duration_s` the outputs are still current, so
  a re-tag never forces a new split. Anything else is stale: another length,
  no duration, or a manifest that cannot be read. A stale folder is skipped
  whole, and the read goes on to the next layout.
- **The verdict.** A file judged `speech` (a podcast, a voice memo) plays as
  it is by default, from then on; `mouth play --instrumental` still plays its
  instrumental.

## What the mouth reads

Four layouts, first hit wins. They are read, never moved or written.

1. `.<name>.d/`, the layout above.
2. `<name>.d/`, the same folder with no dot.
3. `<base>.d/`, where `<base>` is the name without its extension.
4. Raw separator outputs inside any of those folders, in the same order:
   `htdemucs*/<base>/<part>.*` (Demucs's own output tree), then, for the
   instrumental only, `mlx-roformer/no_vocals.*`.

- **An instrumental** is `mixes/instrumental`, then `mixes/karaoke`, then
  `stems/instrumental`, in each of folders 1 to 3; then `no_vocals` in step 4.
- **A stem** is `stems/<NAME>` in each of folders 1 to 3, then `<NAME>` in
  step 4. The names are `vocals`, `drums`, `bass` and `other`.
- **Extensions, best first:** `flac`, `wav`, `aiff`, `aif`, `m4a`, `mp3`,
  `ogg`, `opus`. A folder that holds both `instrumental.wav` and
  `instrumental.flac` gives the FLAC.
- **Only folder 1 is judged stale,** because only it carries a manifest. A
  `.<name>.d/` with no `split.json` is read as it stands.
- **Two sidecars can sit side by side:** `song.flac.d/` from another tool and
  `.song.flac.d/` from the mouth. The mouth reads both, the dot folder first.
- **A mailed play stays inside the sounds directory.** For a play mailed from
  outside, an instrumental or stem is used only if its real path, links
  resolved, is inside the sounds directory, as its source file must be.

## Where a split's state is kept

Not in the sidecar. `<mouth dir>/splits/` holds one status file per source
(`running`, then `done` or `failed`), the host's split speed for the next
estimate, the split log and the lock that lets one split run at a time.
`mouth status` lists the running splits. They are the mouth's own records,
not part of this contract.
