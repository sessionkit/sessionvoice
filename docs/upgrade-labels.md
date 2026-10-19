# Upgrading to the dev.sessionkit.voice labels

Every launch agent voice ships is now `dev.sessionkit.voice.<job>`. On a Mac
that ran an older voice, the old jobs keep running under their old labels
until you move them. This page is the move: a measurement, a dry run, then
each step with its check and its way back.

- **`ears services install` moves what it installed.** It moves the keeper,
  mouth, mail, speech, stt and speakers jobs off `com.sessionvoice.ears-<name>`,
  `com.sessionvoice.mlx-audio` and `com.voicemode.ears-<name>`. Each old job is
  booted out once and its plist removed. The new one loads only after the old
  one has gone.
- **It never moves a job you rendered by hand.** That covers the sounds, talk,
  a hand speakers side-car, and a hand keeper or mailer. You boot each one out
  and render it again from its renamed template in `launchd/`.
- **A hand keeper, mailer or side-car means install skips its own job.** Two
  would run otherwise. `ears services status` says so, with the exact command
  that switches to install's job.
- **Nothing moves on a pull.** The old jobs run the new code under their old
  names until you do this. `ears services status` names every old-form job it
  finds.
- **A dry run changes nothing.** `ears services install --dry-run` asks
  launchd only `print` and `list`.

The rule behind it, and the labels: [configuration.md][labels]. The changelog
entry: [CHANGELOG.md][changelog].

## 0. Measure

Write down what is on the Mac before anything changes. Keep it: it is the
undo.

    launchctl list | grep -E 'com\.sessionvoice\.|com\.voicemode\.ears-|dev\.sessionvoice\.|dev\.voicemode\.|dev\.sessionkit\.voice\.'
    ls -l ~/Library/LaunchAgents
    ears services status

- Every old-form job `status` finds is on its own line:
  - `old label: <label> is still loaded; ... moves it`: one install made.
  - `<label> (hand-made): ...; install skips <name> while it is here`: a hand
    keeper, mailer or side-car.
  - `hand <label> (old name): ...; boot it out and render launchd/<new>.plist.in in its place`:
    a hand sounds or talk job.
  - `WARNING: <label> (LEGACY render)`: a stale hand mailer or player. Install
    replaces it and keeps its plist as `<label>.plist.replaced-<stamp>`.
- Copy every old-form plist somewhere outside `~/Library/LaunchAgents` (launchd
  loads only `*.plist` from there):

      mkdir -p ~/labels-backup && cp -p ~/Library/LaunchAgents/<old-label>.plist ~/labels-backup/

## 1. Dry run

    ears services install --dry-run; echo "rc=$?"

Pass the same flags you installed with (`--agent NAME`, `--only ...`).
Expect `rc=0`; `rc=2` means a refusal, so read it. For each service install
made, expect:

    <name>  old label com.sessionvoice.ears-<name> (loaded): would be booted out and its plist removed
    --- ~/Library/LaunchAgents/dev.sessionkit.voice.ears-<name>.plist

and for the speech server, when it is install's own:

    speech step 4, install: com.sessionvoice.mlx-audio is ours under its old label; install dev.sessionkit.voice.mlx-audio in its place
    speech old label com.sessionvoice.mlx-audio (loaded): would be booted out and its plist removed

Stop and look before going on if the dry run says any of these:

- `REFUSED` or `FAILED`.
- `<name> SKIPPED: the hand-made <label> ...`: a hand job holds that service.
  Read [step 4](#4-a-hand-keeper-mailer-or-side-car).
- A `speech` step other than the two above: another mlx-audio server is
  there. Install will not start a second one; read the README's "A speech
  server".

## 2. Install

    ears services install; echo "rc=$?"

- **Check:** `rc=0` (`rc=2` means a service FAILED or was refused). Each moved
  service prints
  `<name>  old label <old> (loaded): booted out, plist removed`, then
  `<name>  loaded  ~/Library/LaunchAgents/dev.sessionkit.voice.ears-<name>.plist`.
  Then:

      launchctl list > ~/labels-list.txt; echo "rc=$?"
      grep -cE 'com\.sessionvoice\.' ~/labels-list.txt
      grep dev.sessionkit.voice ~/labels-list.txt
      ears services status | grep -c 'old label:'

  Expect `rc=0`, then a count of `0`. Then each service with a pid, which
  also shows the list was read. Then a `0` count of `old label:` lines. Read
  the counts, never an empty screen: a command that fails prints nothing too.
  `grep -c` exits 1 on a zero count, and 2 when it cannot read its input.
  (No command here carries a trailing `# comment`: zsh, the macOS default
  shell, does not read `#` as a comment in an interactive session unless
  `interactivecomments` is set.)

- **A second run does nothing to an old label.** It makes no call on one, and
  reloads install's own jobs as before.
- **An old job that will not go** stops that service only:
  `<name> FAILED: <old> would not go; <new> NOT loaded`. Boot it out
  (`launchctl bootout gui/$(id -u)/<old>`) and run install again.
- **Undo:** for each new job, boot it out and set its plist aside:
  `launchctl bootout gui/$(id -u)/dev.sessionkit.voice.ears-<name>`, then
  `mv ~/Library/LaunchAgents/dev.sessionkit.voice.ears-<name>.plist ~/labels-new/`.
  Check out the voice you ran before, put the plists from `~/labels-backup`
  back, and `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/<old>.plist`
  each one.

## 3. The jobs you rendered by hand

Do one job at a time, the same way each time. Re-render, never edit: the
template is the source.

1. **Render to a scratch folder**, never straight into `~/Library/LaunchAgents`.
   Use the same values the old plist used: read them with
   `plutil -p ~/Library/LaunchAgents/<old>.plist`. Run it from the voice
   checkout the old job ran (the paths are relative to it). `render-plist`
   needs only the system `python3`.

       mkdir -p ~/labels-new
       bin/render-plist -o ~/labels-new launchd/dev.sessionkit.voice.<job>.plist.in; echo "rc=$?"

   Expect `rc=0`. `rc=2` names a placeholder with no value, or a result that
   is not a plist, and writes nothing.

   `SPEAKER_PY=` and `AGENT_REFS=` (speakers) and `TALK=` (talk) have no
   default. Pass them, from the old plist, as `--set NAME=VALUE`.
2. **Check:** `diff <(plutil -p ~/labels-backup/<old>.plist) <(plutil -p ~/labels-new/<new>.plist); echo "rc=$?"`
   shows the `Label` lines only, unless you meant a change, and `rc=1`.
   `rc=2`, or a `plutil` error, means a wrong path: the check failed.
3. **Swap in one step**, so two of the job never run:

       launchctl bootout gui/$(id -u)/<old>
       mv ~/Library/LaunchAgents/<old>.plist ~/labels-backup/<old>.plist.retired
       cp ~/labels-new/<new>.plist ~/Library/LaunchAgents/
       launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/<new>.plist

4. **Check:** `launchctl print gui/$(id -u)/<new> | grep 'state ='` shows
   `state = running` (no line means the job is not loaded), and
   the job's log (named in its template's header) has a fresh start line.
   `ears services status` no longer names `<old>`.
5. **Undo:** `launchctl bootout gui/$(id -u)/<new>`, remove
   `~/Library/LaunchAgents/<new>.plist`, put `<old>.plist` back, and
   `launchctl bootstrap` it.

- **The side-car's hand template renders to install's own label**
  (`dev.sessionkit.voice.ears-speakers`), and so do the keeper's and the
  mailer's. Install tells a render from its own plist by content: every plist
  it writes carries `SESSIONKIT_INSTALLED_BY` in `EnvironmentVariables`, and a
  render does not. So install leaves your render alone and skips its own job.
  The reverse is yours: rendering over a plist install wrote replaces install's
  job.
- **mouth-sounds** is a draft template. Its header says switching a running
  mouth-sounds job over to it is its owner's separate go. Re-render it under
  the new name only with that go.

## 4. A hand keeper, mailer or side-car

Install skips its own job while one is there, and says how to switch:

    WARNING: keeper SKIPPED: the hand-made <label> is already on this host (<plist>); two keepers would fight over the room's capture. It is left as it is. To run ours instead: launchctl bootout gui/<uid>/<label>, move <plist> out of ~/Library/LaunchAgents, then `ears services install --only keeper`

A hand plist that is in `~/Library/LaunchAgents` but **not loaded** still
counts. It is dormant, not stale: launchd loads every plist there at the next
login, so ours would run beside it. The line then says `NOT loaded`, says why
that still counts, and gives the one step that fits: move the plist aside.
There is nothing to boot out. `ears services status` says the same.

**Mind the gap.** If install's own job runs now beside such a dormant plist,
install removes it, never two at the next login. Until that login, **nothing
runs the job**. `status` warns first (`ours runs now as <label>, and the next
install REMOVES it`). So do the dry run first, and move the dormant plist
aside, or load it on purpose, before you install.

- To keep the hand job under the new name, re-render it as in [step 3](#3-the-jobs-you-rendered-by-hand).
- To switch to install's job, run the three steps it prints. **Check:** `ears
  services status` shows the service `running` with no `(hand-made)` line.
  **Undo:** `ears services uninstall --only <name>`, then put the hand plist
  back and `launchctl bootstrap` it.

## 5. Prove it

A heard line alone proves only the keeper. The proof is the whole loop:

1. `mouth say "the voice labels moved"`: you hear it.
2. Answer it out loud: the agent that subscribes to your `heard+<device>`
   address wakes and answers.

## Not moved

- **Multi-Output devices** that `bin/multi-output.swift` made keep their old
  UID. Devices are found by name, so they go on working. A new one gets
  `dev.sessionkit.voice.multi-output.<name>`.
- **Another installer's jobs** (`com.voicemode.*` services other than the old
  `com.voicemode.ears-*` agents) are never touched.

[labels]: configuration.md#the-keeper-and-the-launch-agents
[changelog]: ../CHANGELOG.md
