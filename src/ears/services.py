"""ears services - the launch agents a bare install needs, written and loaded by one verb.

    ears services install [--only stt,speech,mouth,keeper,mail,speakers,sounds,talk] [--apple] [--agent NAME | --no-agent] [--notify ADDR] [--dry-run] [--no-prefetch]
    ears services status
    ears services weights [--json]
    ears services uninstall [--only ...]

Eight services, each a macOS launch agent labelled dev.sessionkit.voice.ears-<name>, but speech,
which is dev.sessionkit.voice.mlx-audio, sounds, which is two agents under their own labels, and
talk, which is dev.sessionkit.voice.talk:

- stt    `ears serve 2023`: Apple speech as an OpenAI-compatible URL, on the helper that
         `ears install --apple` builds (macOS 27). Not the default engine, so install
         loads it only with --apple (or --only stt): without the
         helper it answers 500 to every request.
- speech mlx-audio's server on 127.0.0.1:8890: the ears' Parakeet and the mouth's
         Kokoro. In a bare install, but install first ASKS what holds :8890 (speech.decide):
         a server that says it is mlx-audio (GET / returns its welcome line, or /openapi.json
         lists its audio routes; VoiceMode's, say) is USED and nothing is started. Anything
         else on :8890 is REFUSED by name and never replaced: install exits 2 when the ears
         hear through :8890, and only warns when the mouth alone does (it falls back to
         `say`). A loaded launch agent that runs mlx_audio.server, its port closed between
         restarts, counts too: never a second server. Only an empty port with no such job
         builds its own venv (~/.local/share/ears/speech/venv, from the shipped
         speech/requirements.lock; EARS_SPEECH_PY skips the build) and loads it as
         dev.sessionkit.voice.mlx-audio (ours under its old label is moved, below). A final.url elsewhere, with no mouth installed, manages
         nothing here (the opt-out). VoiceMode's own installer asks nothing: run after this
         one, it loads a second mlx-audio job, and one of the two crash-loops.
- mouth  `mouth mail`: the mouth's player - speaks each queued line, one at a time.
- keeper `keeper/ears-keeper.sh default`: every ear. The room's on the default input
         (asks for the microphone the first time; macOS prompts, the user says yes), and,
         as its child, `keeper/call-ears`: an ear per live Delta call (needs the Delta
         package's bot). The room mic is off while a call is live.
- mail   `keeper/ears-mail.py --to device`: mails every finished turn To and From
         heard+<device>@<host>, which is what PAGES an agent, and each finished turn of
         a Delta call To that call's box, calls+<bot>-<N>@<host> (`--calls off` stops
         it). The keeper alone writes the heard line and wakes nobody, so "a heard
         line appears" is no proof of a working install: "you speak, and the agent
         WAKES and answers" is. A hand-made
         mailer (the shipped template, rendered by hand: see "Whose plist" below) already on
         the host is left alone and ours is skipped: never two mailers (each turn would be
         mailed twice). Its PATH is what the `message` check reads then, never ours.
         The older dev.voicemode.ears-mail is a LEGACY render and is replaced (below).
- speakers `speakers/run.sh`: the speaker side-car, which names who said each
         heard line into speakers_YYYY-MM-DD.jsonl beside the heard log. OPT-IN: only
         `--only speakers` installs it, never a bare install. Its data (refs/, STOP) lives in
         the speakers home, ~/.local/share/ears/speakers (EARS_SPEAKERS_HOME), 0700, outside
         the package, so an upgrade keeps the references. A hand-made sidecar already on the
         host (the template rendered by hand, or an interim follow-capture job) is left alone
         and ours is skipped: never two sidecars (each line would be labelled twice).
- sounds two watchers, each its own agent: dev.sessionkit.voice.ears-sounds runs
         `keeper/ears-sounds.py` (a sound on a turn, a partial, an event: the heard log) and
         dev.sessionkit.voice.mouth-sounds runs `keeper/mouth-sounds.py` (a sound as a line
         lands in the mouth, a mid-turn wake, a scoop). OPT-IN: only `--only sounds`
         installs them, never a bare install: they are indicators that play into the user's
         ears, which the user switches on, not part of hearing or speaking. Both run on the
         agents' python (numpy and sounddevice are base dependencies) from the checkout's
         keeper/ or the wheel's ears/_keeper/ (with held_stream.py, which ears-sounds.py
         imports from beside itself). Their settings are ears-sounds.json and mouth-sounds.json
         in the settings folder, reread live; their sound logs ears-sounds.jsonl and
         mouth-sounds.jsonl beside the agents' logs; their STOP files ~/.sessionkit/voice/
         STOP-ears-sounds and STOP-mouth-sounds (each exits 0 on its STOP and stays down). The
         mid-turn marker and the opener's canned lines stay in the keeper's RUN dir
         (EARS_RUN, else ~/.cache/ears/run), where their writers put them. A hand render of
         either (the template, or one under an old name, HAND) is left alone and ours skipped:
         never two (each sound would play twice).
- talk   `talk serve --host 127.0.0.1 --port 8780`: the live view in a browser (src/talk,
         docs/talk.md), as launchd/dev.sessionkit.voice.talk.plist.in runs it. OPT-IN: only
         `--only talk` installs it, never a bare install: it is a web page the user chooses to
         serve (a listening port, and a pairing per browser), not part of hearing or speaking.
         It runs the INSTALLED talk console script, found beside the agents' python (or in
         sys.prefix's bin), never the first `talk` on PATH: macOS ships an unrelated
         /usr/bin/talk. None there: install refuses. The port is talk's default (talk has no
         port setting). A hand render (the template, or one under an old name,
         dev.sessionvoice.talk or dev.voicemode.talk: HAND) is left alone and ours skipped:
         never two (they would fight over the port).

Before anything is written, install checks what the services need and says so (--dry-run
lists the same checks, changing nothing):
- the Silero VAD model the keeper's capture runs on: present, linked from VoiceMode's copy,
  or fetched by the pin `ears install` uses once nothing else refuses (a failed fetch
  REFUSES; a refused run names it as not fetched);
- for mail: sessionmail's `message` and its carrier, postfix or the mail drop (checked,
  never configured), the nested `heard` box under the store dir
  that carries .tm-store (made when there is exactly one, then vmailbox-gen routes it), and
  the heard+<device> box an agent subscribes to;
- for speakers: an arm64 Mac on macOS 14+ (else REFUSED), its own venv under the speakers
  home (built with uv from the shipped requirements.lock, mlx-audio==0.5.8; EARS_SPEAKER_PY
  skips the build), the modules it imports (the first missing is named, and install
  REFUSES), and the diarizer model (fetched here, the one download; the job never fetches);
- a new ``final`` engine answers one short clip (else whisper :2022, said so). A speech
  server on :8890 that this install does not load (VoiceMode's, adopted) may have to
  download and load Parakeet before it can answer, so its probe says so first and waits
  up to COLD_PROBE_S, saying it is still waiting; Ctrl-C skips to whisper. A fallback is
  recorded (state/stt-fallback.json under sessionvoice's home, never ears.json), repeated
  as the install's last line and as a `status` row, both naming the one command that
  probes the engine again; that later install moves the ears back once it answers;
- --agent NAME: the wake seat (`ears wake add NAME`) and the keeper's notices (EARS_NOTIFY)
  go to the installing agent; --notify ADDR sends the notices elsewhere. An install with
  mail in it and no --agent REFUSES (exit 2, nothing written; a --dry-run says REFUSED,
  goes on listing and exits 2 too) when the wake file names no seat: nobody would be
  woken when you speak. The refusal prints both forms, --agent NAME and --no-agent, and
  `ears wake add NAME`; with $TM_AGENT_ADDRESS set it suggests that address's local part
  as NAME (a suggestion: never installed on it). --no-agent installs anyway, and install
  warns that NOBODY is woken and the notices are log-only. A wake file that already names
  a seat (a re-run, the upgrade path) is not refused: install says which seat it wakes.
  --only without mail (speakers, speech, mouth, keeper) wakes nobody either way and is
  never refused for it. The refusal says what each choice means: --agent NAME for a Mac
  where an agent seat should be woken when you speak; --no-agent for a phone-only Mac (for
  example a Delta bot), where nobody is woken and the notices go to the log (PHONE_ONLY, the
  same paragraph, closes --help);
- the speech weights, when install installs its own speech server: before any plist is
  written, one heads-up line (speech_heads_up) gives the whole download, summed from
  SPEECH_WEIGHT_SIZES, and says the speech server runs as a login service. --dry-run prints
  the same line; every weight already cached says so instead. It warns and goes on, never
  asks: a scripted install never waits on a prompt.

Whose plist: ONE label per job, whoever installs it, and install tells its own plist from a
hand render by CONTENT, never by name. The hand templates in launchd/ render to the same
labels install loads (dev.sessionkit.voice.ears-keeper, -mail, -speakers) and so to the same
path, ~/Library/LaunchAgents/<label>.plist. Every plist install writes carries MARKER in its
EnvironmentVariables (SESSIONKIT_INSTALLED_BY=ears services install); no template does. So:
- a plist at our label WITH the marker is ours: a re-run replaces it with the same job, and
  makes no call on any old label (a no-op on the host);
- a plist at our label WITHOUT it (or unreadable) is HAND-MADE: install never overwrites it,
  leaves it running, skips ours and says so, with the line that switches to ours (boot it out,
  move its plist away, install); `status` names it; `uninstall` leaves it;
- our label LOADED with no plist in AGENTS is judged the same way, by the environment the
  loaded job carries (`launchctl print` lists it): MARKER there is ours; absent is a hand
  render bootstrapped from a plist elsewhere, left alone as above;
- a hand one under a name the template carried before (HAND: dev.sessionvoice.ears-mail,
  com.failmode.ears-speakers, com.failmode.speaker-sidecar, com.failmode.ears-keeper), plist
  or loaded, is hand-made too, and ours is skipped (and removed if it was installed before).
  A hand plist in AGENTS that is NOT loaded is DORMANT, not stale: launchd loads every plist
  there at the next login, so obeying it is what keeps one job per name. The dry run, the
  skip line and `status` say it is not loaded, why it still counts, and the way out by its
  state: a dormant one is moved aside (nothing to boot out), a loaded one booted out (and its
  plist moved out of AGENTS), then install;
- the reverse is the user's own act: rendering a template over a plist install wrote replaces
  install's job (bin/render-plist writes only to stdout or -o; the hand render has no marker,
  so the next install leaves it alone).

Old labels: install-made agents were com.sessionvoice.ears-<name> and com.sessionvoice.mlx-audio
(from the earlier rename to the label rename), and com.voicemode.ears-<name> before that
(OLD_PREFIXES, speech.OLD_LABELS), beside VoiceMode's own com.voicemode.* services, which are
never touched.
Nothing moves on a pull: a host keeps its old jobs, which run the new code under the old name.
Installing service N moves it: each old label of N still on the host is booted out once and its
plist removed, and the new one is loaded only once every old one has gone. If one will not go
within BOOT_WAIT_S, install FAILS for N, names the label, and does not load the new one: two
keepers would fight over the room's capture. A second install finds nothing old and makes no
call on an old label. A dry run names each old label it would move and changes nothing. `status`
names an old label still on the host (loaded, or its plist), `uninstall` removes all of them,
and `ears speakers` restarts whichever speakers label is loaded, the new one first. Speech moves
the same way: its old job, found holding :8890 or loaded, is ours (speech.decide).

LEGACY renders (LEGACY) are hand plists under labels no shipped template carries any more:
dev.voicemode.ears-mail for mail, dev.voicemode.mouth-mail and com.failmode.mouth-mail for the
mouth. Their PATH predates the one `message` needs (a host kept running one, and every send
failed), and a mouth one beside ours is two players draining one queue. Installing mail or
the mouth REPLACES them: each loaded one is booted out, each plist is moved aside to
LABEL.plist.replaced-STAMP (kept, never deleted; launchd loads only *.plist), and ours is loaded
only once they have gone. One that will not go within BOOT_WAIT_S stops that service with
FAILED, and ours is not loaded beside it. `status` names a legacy job still on the host;
`uninstall` leaves them, as it leaves every hand-made job.

Every shipped template is a job install runs now. The old hand names of the sounds and talk
(dev.sessionvoice.*, dev.voicemode.*) are HAND: install skips ours beside one, and `status`
names it.

The room and the calls were once two agents, ears-room and ears-call, now
merged into the keeper. `call` and
`room` still name the keeper in --only, and installing it boots the two old agents out and
removes their plists, so a machine never runs both.

The engine is a SETTING, not part of the launch agents: ``final`` (old name ``stt``) in
~/.sessionkit/voice/settings/ears.json (``{"url": ..., "model": ...}``), followed live by every capture:
each checks it before every transcribe (FollowingSTT), so a change lands on the next chunk
with no restart, in the room and on a call alike. Install seeds it when absent: Parakeet on
the mlx-audio server on :8890 (the speech service above, found or installed), which the mouth's
Kokoro runs on too, so the ears and the mouth need one service.
EARS_STT_URL and EARS_STT_MODEL given at install write it instead, e.g. Apple's speech on the
stt service:

    EARS_STT_URL=http://127.0.0.1:2023/v1 ears services install --apple

The room and the call ear are one capture on two sources, so they share this one setting.
``partials`` beside it (``"final"`` or ``"nemotron"``) is never seeded: absent, the partials
come from the final engine chunk by chunk, as before.
"""
from __future__ import annotations

import os
import plistlib
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import envnames, maildir, speech   # speech: the server on :8890, found or ours
from .home import SETTINGS as _VOICE_SETTINGS   # <kit root>/voice/settings

ROOT = Path(__file__).resolve().parents[2]          # the package checkout (src/ears/ -> root)
AGENTS = Path.home() / "Library/LaunchAgents"
LOGS = Path(os.environ.get("EARS_LOG_DIR", Path.home() / "Library/Logs/ears"))
PREFIX = "dev.sessionkit.voice.ears-"
# every prefix an install-made agent carried before, newest first: what `install` MOVES a host
# off. com.sessionvoice.ears- from the earlier rename to the label rename; com.voicemode.ears-
# before that, and the call/room agents' only label. Every name they carried is a service here
# (stt, mouth, keeper, mail, speakers) or OLD (call, room); speech's old label is
# speech.OLD_LABELS
OLD_PREFIXES = ("com.sessionvoice.ears-", "com.voicemode.ears-")
BOOT_WAIT_S = 10.0          # how long a load waits for the job it replaces (old label or new) to go
ALL = ("stt", "speech", "mouth", "keeper", "mail", "speakers", "sounds", "talk")
# what a bare install loads; stt only with --apple, speakers, sounds and talk only by --only.
# speech first: the server the mouth and the ears ride is loading before they start. Not talk:
# it is a web page a user chooses to serve (a listening port, a pairing per browser), and
# nothing in hearing or speaking needs it
DEFAULT = ("speech", "mouth", "keeper", "mail")
#: a service that is more than one launch agent: sounds is the ears' sounds and the mouth's
#: sounds, two watchers, each its own job under its own label (JOB_LABELS). `--only sounds`
#: names both; every per-label step (install, uninstall, status) runs on the jobs (jobs()).
#: Not in DEFAULT: sounds are an indicator a user switches on, not part of hearing and
#: speaking, and they play into the user's ears
GROUPS = {"sounds": ("ears-sounds", "mouth-sounds")}
#: the jobs whose label is not PREFIX + name: the sounds' and talk's templates' own labels
JOB_LABELS = {"ears-sounds": "dev.sessionkit.voice.ears-sounds",
              "mouth-sounds": "dev.sessionkit.voice.mouth-sounds",
              "talk": "dev.sessionkit.voice.talk"}
#: how install knows its own plist: this EnvironmentVariables entry, which spec() writes into
#: every plist install makes and no hand template carries. The hand templates in launchd/ now
#: render to install's own labels (dev.sessionkit.voice.ears-keeper, -mail, -speakers), so the
#: label says nothing about who wrote the plist; this entry does (_ours)
MARKER = ("SESSIONKIT_INSTALLED_BY", "ears services install")
# HAND-MADE agents under the names the templates carried before the label rename, one tuple per
# service install also runs: a hand one on the host (plist or loaded) is left alone and ours is
# skipped, never two of the job. Under the new name the plist's CONTENT says hand-made (_hand_at).
# mail and keeper: each template's own old name; speakers: the template's old name and an
# interim follow-capture job
HAND = {"mail": ("dev.sessionvoice.ears-mail",),
        "speakers": ("com.failmode.ears-speakers", "com.failmode.speaker-sidecar"),
        "keeper": ("com.failmode.ears-keeper",),
        # the sounds' and talk's template names before the two label renames
        "ears-sounds": ("dev.sessionvoice.ears-sounds", "dev.voicemode.ears-sounds"),
        "mouth-sounds": ("dev.sessionvoice.mouth-sounds", "dev.voicemode.mouth-sounds"),
        "talk": ("dev.sessionvoice.talk", "dev.voicemode.talk")}
HAND_MAILS = HAND["mail"]
HAND_MAIL = HAND_MAILS[0]           # the hand-rendered mailer under its name before the label rename
HAND_SPEAKERS = HAND["speakers"]
# LEGACY renders, stale by label (their PATH predates the one `message` needs): never left
# running, never run beside ours. Install replaces them (_replace_legacy). mail: the template's
# label before the dev.sessionvoice rename; mouth: hand players from before `ears services` had
# one (no template here ever made them)
LEGACY = {"mail": ("dev.voicemode.ears-mail",),
          "mouth": ("dev.voicemode.mouth-mail", "com.failmode.mouth-mail")}
#: launchd's PATH for a job whose plist names none
LAUNCHD_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
SIDECAR = "speaker-sidecar2.py"        # the side-car's script: what a stray hand-started one runs
OLD = ("call", "room")                 # the two agents the keeper replaced; --only maps them to it


class NoKeeper(RuntimeError):
    """No ears-keeper.sh to point the keeper agent at: install refuses, writes no plist."""


class NoMailer(NoKeeper):
    """No ears-mail.py to point the mail agent at: install refuses, writes no plist."""


class NoSpeakers(NoKeeper):
    """No speakers/NAME (run.sh, the side-car, enrol's scripts) to point the speakers agent
    at: install refuses, writes no plist."""


class NoSounds(NoKeeper):
    """No keeper/NAME for a sounds agent (ears-sounds.py, mouth-sounds.py, or held_stream.py,
    which ears-sounds.py imports from beside itself): install refuses, writes no plist."""


class NoTalk(NoKeeper):
    """No installed talk console script beside the agents' python: install refuses, writes no
    plist (never the first `talk` on PATH, which on macOS is /usr/bin/talk)."""


class NoSchema(NoKeeper):
    """No settings/NAME.schema.json to copy beside the settings: install refuses, writes no
    plist and copies no schema, never a silent empty copy."""


# what install_schema() copies: settings/ in a checkout, ears/_settings/ in the wheel.
# tests/test_settings_in_wheel.py holds this list, settings/ and the package data to each other.
SCHEMAS = ("ears-sounds.schema.json", "ears.schema.json", "mouth.schema.json")


def _shipped_file(src: str, pkg: str, name: str, exc: type[NoKeeper]) -> Path:
    """SRC/NAME: the checkout's when run from one, else the wheel's ears/PKG/ copy (package
    data: symlinks into SRC/ in the checkout, files in the wheel). Neither: EXC."""
    checkout = ROOT / src / name
    if checkout.is_file():
        return checkout
    from importlib.resources import files
    shipped = Path(str(files("ears") / pkg / name))
    if shipped.is_file():
        return shipped
    raise exc(f"no {name}: not at {checkout} (a checkout) nor at {shipped} (the "
              f"package). Reinstall sessionvoice from a wheel that ships ears/{pkg}/, "
              "or run `ears services install` from a git checkout (uv run ears ...)")


def _keeper_file(name: str, exc: type[NoKeeper]) -> Path:
    """keeper/NAME: the checkout's when run from one, else the wheel's ears/_keeper/ copy."""
    return _shipped_file("keeper", "_keeper", name, exc)


def _speakers_file(name: str) -> Path:
    """speakers/NAME, found as the keeper is: the checkout's speakers/ when run from one,
    else the wheel's ears/_speakers/ copy. Neither: NoSpeakers."""
    return _shipped_file("speakers", "_speakers", name, NoSpeakers)


def keeper_script() -> Path:
    """The ears-keeper.sh the keeper agent runs: the checkout's keeper/ when run from one (a
    git checkout, an editable install), else the copy the wheel ships as package data,
    ears/_keeper/ (from the kit, ROOT is <venv>/lib/python3.X, and the plist
    pointed at a <venv>/lib/python3.X/keeper/ears-keeper.sh that no install made). Neither:
    NoKeeper, so install refuses loudly instead of writing a keeper agent that cannot start."""
    return _keeper_file("ears-keeper.sh", NoKeeper)


def mail_script() -> Path:
    """The ears-mail.py the mail agent runs, found as keeper_script() finds the keeper (it is
    stdlib only and imports nothing beside it). Neither: NoMailer, and install refuses."""
    return _keeper_file("ears-mail.py", NoMailer)


#: what each sounds job runs: its script, and what that script imports from beside itself
SOUNDS_FILES = {"ears-sounds": ("ears-sounds.py", "held_stream.py"), "mouth-sounds": ("mouth-sounds.py",)}


def sounds_script(job: str) -> Path:
    """The watcher the sounds job JOB runs (keeper/ears-sounds.py, keeper/mouth-sounds.py),
    found as keeper_script() finds the keeper: the checkout's keeper/, else the wheel's
    ears/_keeper/ copy. Each file it imports from beside itself must be there too (ears-sounds:
    held_stream.py), or the job dies on import. Any one missing: NoSounds, and install refuses."""
    script, *beside = SOUNDS_FILES[job]
    found = _keeper_file(script, NoSounds)
    for name in beside:
        if not (found.parent / name).is_file():
            raise NoSounds(f"no {name} beside {found}: {script} imports it from there. Reinstall "
                           "sessionvoice from a wheel that ships ears/_keeper/, or run `ears services "
                           "install` from a git checkout (uv run ears ...)")
    return found


def talk_bin() -> Path:
    """The talk console script the talk job runs: the one installed beside the agents' python
    (_py(): in the kit's venv, its bin/), else sys.prefix's bin/. Never the first `talk` on
    PATH: launchd does not search PATH, and macOS ships an unrelated /usr/bin/talk. A file
    there that is not talk's own entry point (it imports talk.cli) is not it either. None:
    NoTalk, and install refuses."""
    seen = []
    for d in dict.fromkeys((Path(_py()).parent, Path(sys.prefix) / "bin")):
        cand = d / "talk"
        seen.append(str(cand))
        try:
            head = cand.read_bytes()[:4096] if cand.is_file() and os.access(cand, os.X_OK) else b""
        except OSError:
            head = b""
        if b"talk.cli" in head:
            return cand
    raise NoTalk(f"no installed talk console script at {' or '.join(seen)}. Reinstall sessionvoice "
                 "(it ships `talk`), or run `ears services install` from the python it is installed beside")


def _talk_port() -> int:
    """The port the talk job serves: talk's own default (talk.server.PORT, the template's 8780).
    talk has no port setting."""
    from talk.server import PORT
    return PORT


def voice_dir() -> Path:
    """The kit's folder for the voice package, ~/.sessionkit/voice (the settings folder's
    parent). The sounds agents' STOP files sit here, never beside a script in the package."""
    from .home import settings_dir
    return settings_dir().parent


def schema_files() -> list[Path]:
    """Each settings schema install copies (SCHEMAS), found as the keeper is: the checkout's
    settings/ when run from one, else the wheel's ears/_settings/ copy (from the kit,
    ROOT is <venv>/lib/python3.X, and the old settings/ glob found nothing and copied nothing,
    silently). Any one missing: NoSchema, so install refuses before it writes anything."""
    return [_shipped_file("settings", "_settings", name, NoSchema) for name in SCHEMAS]


def _py() -> str:
    return os.environ.get("EARS_PY", sys.executable)


def speakers_home() -> Path:
    """The speakers home: refs/, refs-call/, STOP and the service venv. Per user,
    outside the package, so an upgrade keeps the references: EARS_SPEAKERS_HOME, else
    ~/.local/share/ears/speakers. Install makes it 0700 (voices are private)."""
    raw = os.environ.get("EARS_SPEAKERS_HOME")
    return Path(os.path.expanduser(raw)) if raw else Path.home() / ".local/share/ears/speakers"


def speaker_py() -> Path:
    """The python the side-car runs on: EARS_SPEAKER_PY (a host that already has one), else the
    service venv's, <speakers home>/venv/bin/python (the `EARS_PY` pattern, _py())."""
    raw = os.environ.get("EARS_SPEAKER_PY")
    return Path(os.path.expanduser(raw)) if raw else speakers_home() / "venv/bin/python"


def _speakers_enrolled() -> bool:
    """True when the speakers home holds a reference: what run.sh needs to start at all."""
    return any((speakers_home() / "refs").glob("*.wav"))


# What the side-car's python must import: the diarizer (PyPI mlx-audio 0.5.6+;
# 0.5.5 has no nemotron_diarization), and the side-car's own two. Checked one at a time, so
# a refusal names the FIRST one missing, not a traceback.
SPEAKER_MODULES = ("mlx_audio.vad.models.nemotron_diarization", "numpy", "sounddevice")
SPEAKER_MODEL = "mlx-community/Nemotron-3-Diarization"   # about 190 MB, fetched once, at install
# The one snapshot of SPEAKER_MODEL every step reads: the fetch, the cache check and
# the three speakers/ loaders, which hold the same literal (a test keeps them equal). A full
# 40-hex commit, never a branch: fetched by its sha, the cache writes no refs/main, so a reader
# that asks for `main` finds nothing offline. It is `main` of the repo on 2030-06-09, the one
# commit after "initial commit". To bump: read the new `sha` from
# https://huggingface.co/api/models/mlx-community/Nemotron-3-Diarization, change it here and in
# speakers/speaker-sidecar2.py, make-refs.py and cluster-room.py together, and run
# speakers/test-labels-unchanged.sh on recorded lines, as for an mlx-audio bump.
SPEAKER_REVISION = "59ed2dbfc1346dcea9d423c71306a3a2499c568f"

# The weights the speech server on :8890 loads: Parakeet for the ears, Kokoro for the
# mouth, and Kokoro's voice packs, which come from a third repo (mlx-audio's kokoro.py REPO_ID).
# These are NOT a pin. mlx-audio 0.5.8's server fetches each one with snapshot_download(repo,
# revision=None), so it takes whatever `main` is on the day of first use, and it has no revision
# knob. These are the commits that were `main` when sessionvoice
# was checked against them, and `ears services status` reports, read-only, whether the HF cache's
# refs/main still points at each (weights_status). This is the one place these shas live; a test
# refuses a copy. To bump one, read `sha` from https://huggingface.co/api/models/<repo> (the
# metadata API: never fetch the weights to find it).
SPEECH_WEIGHTS = (
    ("mlx-community/parakeet-tdt-0.6b-v3", "ed2b7e8c15f9aaa0b5772e2efb986255eaef7e15"),  # ears
    ("mlx-community/Kokoro-82M-bf16", "a71e4d38b236d968966a2002c4c895dbd12b1c3c"),       # mouth
    ("prince-canuma/Kokoro-82M", "e02c9eada7ce7416798af36b190a8a2dd2ecd566"),            # voices
)


#: the variables huggingface_hub reads its hub cache from (hf_cache)
HF_CACHE_VARS = ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "HF_HOME", "XDG_CACHE_HOME")


def hf_cache(environ=None) -> Path:
    """The Hugging Face hub cache, by huggingface_hub's own rule (its constants.py), with no
    import: HF_HUB_CACHE, else HUGGINGFACE_HUB_CACHE, else <HF_HOME>/hub, where HF_HOME is
    $XDG_CACHE_HOME/huggingface when unset, and XDG_CACHE_HOME is ~/.cache. An empty variable
    counts as unset. ENVIRON: the variables to read (default: this process's)."""
    env = (os.environ if environ is None else environ).get
    hub = env("HF_HUB_CACHE") or env("HUGGINGFACE_HUB_CACHE")
    if not hub:
        home = env("HF_HOME") or os.path.join(env("XDG_CACHE_HOME") or os.path.join("~", ".cache"),
                                              "huggingface")
        hub = os.path.join(home, "hub")
    return Path(os.path.expandvars(os.path.expanduser(hub)))


# What each weight's pinned size covers, beside SPEECH_WEIGHTS and in its order: (use, the first
# 12 hex of the sha it was measured at, bytes, files, the allow patterns summed). The size is the
# sum of the files those patterns select (huggingface_hub's fnmatchcase) at that sha, read from the
# HF metadata API, https://huggingface.co/api/models/<repo>/tree/<sha>?recursive=true, never by
# fetching weights. stt and tts are what mlx-audio 0.5.8's server fetches, DEFAULT_ALLOW_PATTERNS
# (mlx_audio/utils.py). The voices repo is never fetched that way: the server takes one
# voices/<voice>.safetensors per voice asked for (kokoro/pipeline.py load_single_voice) and lists
# voices/*.safetensors (server.py /v1/audio/voices), so its size is every voice pack, and never the
# voices/<voice>.pt beside each one, which the server does not fetch. A test holds
# each size to its sha: bump a sha in SPEECH_WEIGHTS and you re-measure its size here with it.
MLX_AUDIO_ALLOW_PATTERNS = ("*.json", "*.safetensors", "*.py", "*.model", "*.tiktoken", "*.txt",
                            "*.jinja", "*.jsonl", "*.yaml", "*.npz", "*.pth")
VOICE_PACK_PATTERNS = ("voices/*.safetensors",)
SPEECH_WEIGHT_SIZES = (
    ("stt", "ed2b7e8c15f9", 2_508_940_517, 4, MLX_AUDIO_ALLOW_PATTERNS),
    ("tts", "a71e4d38b236", 355_322_783, 56, MLX_AUDIO_ALLOW_PATTERNS),
    ("voices", "e02c9eada7ce", 28_206_306, 54, VOICE_PACK_PATTERNS),
)
WEIGHTS_SCHEMA = "ears.weights/1"
# A download in progress keeps writing its blobs/<etag>.<8hex>.incomplete (hf_xet 1.7.0 writes it
# in byte order, a term at a time); one untouched for longer than this is a download that died.
WEIGHTS_FRESH_S = 120
_REF_MAX = 40        # refs/main is a file anyone can write: read and shown at most this long


def _weight_folder(cache: Path, repo: str) -> Path:
    return cache / ("models--" + repo.replace("/", "--"))


def _cached_ref(folder: Path) -> str | None:
    """What refs/main holds, stripped and cut to _REF_MAX, or None (absent, empty, unreadable)."""
    try:
        with open(folder / "refs" / "main", "rb") as f:
            raw = f.read(256)
    except OSError:                  # no cache, no repo, no refs/main, refs not a dir, unreadable
        return None
    ref = raw.decode("utf-8", "replace").strip()[:_REF_MAX]
    return ref or None


def _blobs_on_disk(folder: Path, now: float) -> tuple[int, bool, bool]:
    """(bytes, any .incomplete, any FRESH .incomplete) in FOLDER/blobs. A complete blob may be a
    symlink into huggingface_hub's shared store (<cache>/blobs/<2hex>/<xet hash>), so its size is
    read through the link; a dangling link counts nothing."""
    done, partial, fresh = 0, False, False
    try:
        entries = list(os.scandir(folder / "blobs"))
    except OSError:
        return 0, False, False
    for e in entries:
        try:
            if e.name.endswith(".incomplete"):
                st = e.stat(follow_symlinks=False)
                partial = True
                fresh = fresh or now - st.st_mtime <= WEIGHTS_FRESH_S
            else:
                st = e.stat()
                if not e.is_file():
                    continue
        except OSError:              # gone between the listing and the stat, or dangling
            continue
        done += st.st_size
    return done, partial, fresh


def _packs(folder: Path, ref: str | None) -> int:
    """The voice packs that snapshots/<ref>/voices holds (each a link to a blob that is there)."""
    if not ref:
        return 0
    try:
        return sum(1 for p in (folder / "snapshots" / ref / "voices").glob("*.safetensors")
                   if p.is_file())
    except OSError:
        return 0


def _read_weight(repo: str, expected: str, use: str, total: int | None, cache: Path,
                 now: float) -> tuple[dict, bool, int]:
    """(the weight_reading entry, a stale .incomplete is there, voice packs in the snapshot)."""
    folder = _weight_folder(cache, repo)
    ref = _cached_ref(folder)
    drift = ref is not None and ref != expected
    total = None if drift else total
    done, partial, fresh = _blobs_on_disk(folder, now)
    if fresh:
        state = "downloading"
    elif (ref and (folder / "snapshots" / ref).is_dir() and not partial
          and (total is None or done >= total)):
        state = "cached"
    else:
        state = "missing"
    entry = {"repo": repo, "use": use, "state": state, "bytes_done": done, "bytes_total": total,
             "expected": expected, "cached_ref": ref, "drift": drift}
    return entry, partial and not fresh, _packs(folder, ref) if use == "voices" else 0


def weight_reading(repo: str, expected: str, use: str, total: int | None, cache: Path,
                   now: float | None = None) -> dict:
    """One weight's entry of ``ears services weights --json`` (schema ears.weights/1): its state
    (downloading, cached or missing, decided in that order), bytes on disk, the pinned TOTAL
    (None when refs/main names another commit) and the drift flag. Filesystem only: no
    huggingface_hub, no network, no launchctl, so it cannot fetch or hang, and it writes nothing."""
    import time
    return _read_weight(repo, expected, use, total, cache, time.time() if now is None else now)[0]


def _readings(cache: Path, now: float | None) -> list[tuple[dict, bool, int]]:
    import time
    now = time.time() if now is None else now
    return [_read_weight(repo, sha, use, size, cache, now)
            for (repo, sha), (use, _, size, _, _) in zip(SPEECH_WEIGHTS, SPEECH_WEIGHT_SIZES)]


def _rollup(cache: Path, weights: list[dict]) -> dict:
    states = [w["state"] for w in weights]
    totals = [w["bytes_total"] for w in weights]
    return {"schema": WEIGHTS_SCHEMA, "cache": str(cache),
            "state": ("downloading" if "downloading" in states
                      else "cached" if all(s == "cached" for s in states) else "missing"),
            "bytes_done": sum(w["bytes_done"] for w in weights),
            "bytes_total": None if None in totals else sum(totals),
            "weights": weights}


def weights_reading(cache: Path | None = None, now: float | None = None) -> dict:
    """The whole of ``ears services weights --json``: every SPEECH_WEIGHTS entry, in its order,
    and the rollup (downloading if any is, else cached if all are, else missing). CACHE: the hub
    cache to read (default: the one the speech server reads, speech_hf_cache, so the reading
    answers whether its first call will fetch, whatever this shell's HF_* say)."""
    cache = speech_hf_cache() if cache is None else cache
    return _rollup(cache, [w for w, _, _ in _readings(cache, now)])


def _size(n: int) -> str:
    """Bytes for a person, decimal: 412 MB, 2.5 GB, 1.6 MB, 522 KB."""
    for unit, f in (("GB", 10**9), ("MB", 10**6), ("KB", 10**3)):
        if n >= f:
            v = n / f
            return f"{v:.1f} {unit}" if v < 10 else f"{v:.0f} {unit}"
    return f"{n} B"


def _weight_said(w: dict, stale: bool, packs: int) -> str:
    """The words after `weight <repo>: ` for one weight_reading entry."""
    done, total, sha = w["bytes_done"], w["bytes_total"], w["expected"][:12]
    of = f"{_size(done)} of {_size(total)}" if total is not None else f"{_size(done)} so far"
    if w["state"] == "downloading":
        said = f"downloading {of}" if total is not None else f"downloading, {of}"
    elif w["state"] == "cached":
        said = f"cached, {_size(done)}" + ("" if w["drift"] else f" ({sha})")
    elif stale:
        said = f"not cached: a download stopped at {of} (expected {sha}; fetched from main at first use)"
    elif packs:
        files = next(n for u, _, _, n, _ in SPEECH_WEIGHT_SIZES if u == w["use"])
        said = (f"{packs} of {files} packs ({of}); the rest fetch at first use of each voice; "
                f"`ears services install` fetches them all")
    elif w["use"] == "voices":
        files = next(n for u, _, _, n, _ in SPEECH_WEIGHT_SIZES if u == w["use"])
        every = f", {_size(total)} for all {files}" if total is not None else ""
        said = f"not cached (expected {sha}; the first use of each voice fetches its pack from main{every})"
    else:
        about = f"about {_size(total)}" if total is not None else "it"
        said = f"not cached (expected {sha}; the first use fetches {about} from main)"
    if w["drift"]:
        said += f"; DIFFERS: main is {w['cached_ref']!r} in the cache, expected {sha}"
    return said


def weights_status(cache: Path | None = None, now: float | None = None) -> list[str]:
    """The ``weight`` rows of ``ears services status`` and ``ears services weights``: one header
    naming the cache, the total and the limit, then one row for each of SPEECH_WEIGHTS, from the
    same reading as weights_reading, of the same cache (default: speech_hf_cache)."""
    cache = speech_hf_cache() if cache is None else cache
    readings = _readings(cache, now)
    r = _rollup(cache, [w for w, _, _ in readings])
    total = f", {_size(r['bytes_total'])} in all" if r["bytes_total"] is not None else ""
    rows = [f"{'weight':6s} the speech server's weights in the HF cache {cache}{total}, read-only. "
            f"A drift report, not a pin: the server fetches `main` at first use"]
    for w, stale, packs in readings:
        rows.append(f"{'weight':6s} {w['repo']}: {_weight_said(w, stale, packs)}")
    return rows


def weights(as_json: bool = False) -> int:
    """``ears services weights [--json]``: the reader, alone. Exits 0 whenever it could read;
    a missing cache is a state, not an error."""
    if as_json:
        import json
        print(json.dumps(weights_reading(), separators=(",", ":")))
    else:
        for row in weights_status():
            print(row)
    return 0


def _offline_env() -> dict:
    """The env every speakers step runs its python in: HF_HUB_OFFLINE=1, so a check, an
    enrol step or a start never downloads (Requirement "a start never downloads")."""
    return dict(os.environ, HF_HUB_OFFLINE="1")


def speakers_missing_module(py) -> str | None:
    """The first of SPEAKER_MODULES that PY cannot import, or None: each imported in its own
    python, offline. A PY that does not run at all is named as its first module missing."""
    return _missing_module(py, SPEAKER_MODULES)


def _missing_module(py, modules) -> str | None:
    """The first of MODULES that PY cannot import, or None (speakers_missing_module)."""
    for mod in modules:
        try:
            r = subprocess.run([str(py), "-c", f"import {mod}"], capture_output=True, text=True,
                               env=_offline_env(), timeout=120)
        except (OSError, subprocess.TimeoutExpired):
            return mod
        if r.returncode != 0:
            return mod
    return None


def speakers_model_cached(py) -> bool:
    """True when SPEAKER_MODEL at SPEAKER_REVISION is in PY's Hugging Face cache:
    snapshot_download with local_files_only, under HF_HUB_OFFLINE=1, so asking never fetches it.
    A cache holding only another snapshot, or a half-fetched one, reads as not cached."""
    code = ("from huggingface_hub import snapshot_download; "
            f"snapshot_download({SPEAKER_MODEL!r}, revision={SPEAKER_REVISION!r}, "
            "local_files_only=True)")
    try:
        r = subprocess.run([str(py), "-c", code], capture_output=True, text=True,
                           env=_offline_env(), timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


# The service venv: built under the speakers home
# by install, from the compiled lock the package ships (speakers/requirements.lock), never
# into the ears' own venv and never from a git URL. mlx has wheels only for arm64 macOS 14+.
SPEAKER_PIN = "mlx-audio==0.5.8"
SPEAKER_PYTHON = "3.12"
SPEAKER_MACOS = (14, 0)
SPEAKER_MODEL_SIZE = "about 190 MB, once"


def _mac() -> tuple[str, str]:
    """(machine, macOS version; "" off macOS) of this host. A seam: tests fake the host."""
    import platform
    return platform.machine(), platform.mac_ver()[0]


def _uv() -> str | None:
    """The uv that builds the service venv (a kit's bin/ears already needs it), or None."""
    import shutil
    return shutil.which("uv")


def _speakers_sh(argv) -> subprocess.CompletedProcess:
    """One network step of install (uv venv, uv pip install, the model download): a seam, so
    `make test` builds no venv and fetches nothing. Never HF_HUB_OFFLINE: these are the
    explicit steps that may fetch."""
    env = {k: v for k, v in os.environ.items() if k != "HF_HUB_OFFLINE"}
    return subprocess.run([str(a) for a in argv], capture_output=True, text=True, env=env)


def _speakers_platform() -> str | None:
    """Why this host cannot run the side-car (not arm64, not macOS 14+), or None."""
    return _mlx_platform("speakers", "the diarizer's runtime")


def _mlx_platform(what: str, runtime: str) -> str | None:
    """Why this host cannot run WHAT on mlx (not arm64, not macOS 14+), or None."""
    machine, ver = _mac()
    try:
        parts = (tuple(int(x) for x in ver.split(".")[:2]) + (0,))[:2] if ver else ()   # "14" is 14.0
    except ValueError:
        parts = ()
    if machine == "arm64" and parts and parts >= SPEAKER_MACOS:
        return None
    return (f"{what} needs an Apple-silicon Mac (arm64) on macOS {SPEAKER_MACOS[0]} or later; "
            f"this host is {machine or 'unknown'} on {'macOS ' + ver if ver else 'no macOS'} "
            f"(mlx, {runtime}, has no wheel for it)")


def _last_line(r: subprocess.CompletedProcess) -> str:
    out = (r.stderr or r.stdout or "").strip().splitlines()
    return out[-1] if out else f"exit {r.returncode}"


def speakers_refusal() -> str | None:
    """Why speakers cannot be installed on this host as it stands, read-only and offline, or
    None: speakers_checks' first failure among the host, EARS_SPEAKER_PY, the shipped lock and
    a uv to build the venv with (or a venv already there). The one-pass preflight asks this
    before anything is written; speakers_checks asks it first, so the two say the same words.
    The build, the module imports and the model download come later, in speakers_checks."""
    bad = _speakers_platform()
    if bad:
        return bad
    py = speaker_py()
    if os.environ.get("EARS_SPEAKER_PY"):
        return None if py.exists() else f"EARS_SPEAKER_PY={py}: no such python"
    try:
        _speakers_file("requirements.lock")
    except NoSpeakers as e:
        return str(e)
    if not _uv() and not py.exists():
        return (f"no uv on PATH: speakers builds its own venv ({py.parent.parent}) with uv. Install uv "
                "(https://docs.astral.sh/uv/), or set EARS_SPEAKER_PY to a python that has "
                "mlx-audio>=0.5.6")
    return None


def speakers_checks(dry_run: bool = False) -> bool:
    """Before any plist: the host, the service python, its modules, the model.
    Prints what it found or did; a refusal is ONE line on stderr, and False (install exits 2
    and writes no plist). --dry-run builds, fetches and writes nothing: it says what it would.

    1. the host: arm64 and macOS 14+, else refused (mlx has no wheel elsewhere);
    2. the python: EARS_SPEAKER_PY skips the build (the check still runs on it). Else the venv
       <speakers home>/venv: built when absent (`uv venv --python 3.12`), and the shipped lock
       installed into it (`uv pip install --require-hashes -r requirements.lock`: also what
       moves an existing venv to a bumped pin). A fresh build checks every artifact against the
       lock's hashes; a re-apply checks only what it installs, not what is already there. No uv
       to build with: refused;
    3. the modules, each imported on its own: the first missing is named (SPEAKER_MODULES);
    4. the model at SPEAKER_REVISION: cached (local_files_only), else fetched here, the one
       download, said so."""
    def refuse(msg: str) -> bool:
        print(f"ears services install: REFUSED: {msg}", file=sys.stderr)
        return False

    bad = speakers_refusal()  # 1, and 2's checks: read-only, the preflight asks the same
    if bad:
        return refuse(bad)
    py = speaker_py()
    if os.environ.get("EARS_SPEAKER_PY"):
        print(f"{'speakers':6s} python {py} (EARS_SPEAKER_PY: no venv built)")
    else:
        venv = py.parent.parent
        try:
            lock = _speakers_file("requirements.lock")
        except NoSpeakers as e:   # gone since speakers_refusal() found it
            return refuse(str(e))
        pins = sum(1 for l in lock.read_text().splitlines() if l[:1].isalnum())
        uv = _uv()
        if dry_run:
            if not py.exists():
                print(f"{'speakers':6s} would build {venv} (python {SPEAKER_PYTHON}, {SPEAKER_PIN}, "
                      f"{pins} packages pinned in {lock}; network)")
                print(f"{'speakers':6s} would download {SPEAKER_MODEL} ({SPEAKER_MODEL_SIZE})")
                return True
            print(f"{'speakers':6s} venv present: {py}")
        elif uv:
            if not py.exists():
                home = speakers_home()
                home.mkdir(parents=True, exist_ok=True)
                home.chmod(0o700)                       # voices are private; mkdir's mode is umask'd
                print(f"{'speakers':6s} building {venv} (python {SPEAKER_PYTHON}, {SPEAKER_PIN}; "
                      "network)", flush=True)
                # a venv dir with no python in it (its base python removed, a build cut short):
                # uv builds over it only with --clear (uv 0.11: "already exists"). It is ours.
                clear = ["--clear"] if venv.exists() else []
                r = _speakers_sh([uv, "venv", "--python", SPEAKER_PYTHON, *clear, venv])
                if r.returncode != 0:
                    return refuse(f"uv venv {venv} failed: {_last_line(r)}")
            # no -b, unlike speech: this lock has no sdist-only pin, so uv builds nothing and
            # fetches no build backend (test_service_build_constraints refuses one)
            r = _speakers_sh([uv, "pip", "install", "--python", py, "--require-hashes", "-r", lock])
            if r.returncode != 0:
                return refuse(f"uv pip install --require-hashes -r {lock} into {venv} failed: "
                              f"{_last_line(r)}")
            print(f"{'speakers':6s} venv {py}: {pins} packages pinned ({SPEAKER_PIN})")
        else:
            print(f"WARNING: no uv on PATH: the venv at {venv} is used as it is, its pins not re-applied")
    missing = speakers_missing_module(py)
    if missing:
        why = (f"the speaker side-car needs mlx-audio>=0.5.6 (this install pins {SPEAKER_PIN})"
               if missing.startswith("mlx_audio") else "the speaker side-car imports it")
        return refuse(f"{py} has no {missing}: {why}")
    if speakers_model_cached(py):
        print(f"{'speakers':6s} model {SPEAKER_MODEL} cached")
    elif dry_run:
        print(f"{'speakers':6s} would download {SPEAKER_MODEL} ({SPEAKER_MODEL_SIZE})")
    else:
        print(f"{'speakers':6s} downloading {SPEAKER_MODEL} ({SPEAKER_MODEL_SIZE})", flush=True)
        r = _speakers_sh([py, "-c", "from huggingface_hub import snapshot_download; "
                          f"snapshot_download({SPEAKER_MODEL!r}, revision={SPEAKER_REVISION!r})"])
        if r.returncode != 0:
            return refuse(f"the download of {SPEAKER_MODEL} failed: {_last_line(r)}")
    return True


# --- the speech server: mlx-audio on :8890, under sessionvoice's own label ------------
# speech.decide() runs first: only an
# empty port AND no loaded mlx_audio.server job builds or writes anything. The venv copies the
# speakers one: under the speech home, built by uv from the shipped speech/requirements.lock.
# Never `uv tool install mlx-audio` (VoiceMode's env), never another installer's plist or job.
SPEECH_PIN = "mlx-audio==0.5.8"            # the speakers pin: one mlx-audio for the host
# what the server's python must import: the server, Kokoro's text front end (the mouth) and the
# spaCy model it loads (absent, misaki fetches it with pip at the first line spoken)
SPEECH_MODULES = ("mlx_audio.server", "misaki.en", "en_core_web_sm")


class NoSpeech(NoKeeper):
    """No speech/requirements.lock to build the speech server's venv from: install refuses."""


def _speech_file(name: str) -> Path:
    """speech/NAME, found as the keeper is: the checkout's, else the wheel's ears/_speech/."""
    return _shipped_file("speech", "_speech", name, NoSpeech)


def speech_home() -> Path:
    """The speech server's home: its venv. EARS_SPEECH_HOME, else ~/.local/share/ears/speech."""
    raw = os.environ.get("EARS_SPEECH_HOME")
    return Path(os.path.expanduser(raw)) if raw else Path.home() / ".local/share/ears/speech"


def speech_py() -> Path:
    """The python the speech server runs on: EARS_SPEECH_PY (a host that already has one), else
    the venv's, <speech home>/venv/bin/python (speaker_py's pattern)."""
    raw = os.environ.get("EARS_SPEECH_PY")
    return Path(os.path.expanduser(raw)) if raw else speech_home() / "venv/bin/python"


def _speech_platform() -> str | None:
    """Why this host cannot run the speech server (not arm64, not macOS 14+), or None."""
    return _mlx_platform("the speech server", "mlx-audio's runtime")


def _speech_sh(argv) -> subprocess.CompletedProcess:
    """One network step of the speech install (uv venv, uv pip install): a seam, as
    _speakers_sh, so `make test` builds no venv and fetches nothing."""
    env = {k: v for k, v in os.environ.items() if k != "HF_HUB_OFFLINE"}
    return subprocess.run([str(a) for a in argv], capture_output=True, text=True, env=env)


def _speech_port() -> int:
    """The speech server's port: EARS_SPEECH_PORT (a stub on an ephemeral port, in a test or the
    verify demo; EARS_STT_PORT's pattern), else speech.PORT, 8890."""
    return int(os.environ.get("EARS_SPEECH_PORT") or speech.PORT)


def jobs(names) -> list[str]:
    """NAMES with each GROUPS service (sounds) replaced by its jobs, in order, once each: what
    install, uninstall and status step through, one launch agent each."""
    return list(dict.fromkeys(j for n in names for j in GROUPS.get(n, (n,))))


def _service(job: str) -> str:
    """The --only name that installs JOB: the GROUPS service it is a job of (ears-sounds,
    mouth-sounds: sounds), else JOB itself. Every `ears services install --only ...` hint names
    this, never a job --only refuses."""
    return next((g for g, js in GROUPS.items() if job in js), job)


def _label(name: str) -> str:
    """A job's launchd label: dev.sessionkit.voice.ears-NAME, but the speech server's is its
    own, speech.LABEL (dev.sessionkit.voice.mlx-audio), never a ...ears-speech, and the sounds
    jobs' and talk's are their templates' (JOB_LABELS: dev.sessionkit.voice.ears-sounds,
    -.mouth-sounds, -.talk)."""
    if name in JOB_LABELS:
        return JOB_LABELS[name]
    return speech.LABEL if name == "speech" else PREFIX + name


def _old_labels(name: str) -> list[str]:
    """Every label NAME's install-made agent carried before, newest first: one per OLD_PREFIXES
    (com.sessionvoice.ears-NAME, com.voicemode.ears-NAME), and speech's speech.OLD_LABELS. The
    sounds jobs and talk have none: install never made them before (their hand names are HAND)."""
    if name == "speech":
        return list(speech.OLD_LABELS)
    if name in JOB_LABELS:
        return []
    return [p + name for p in OLD_PREFIXES]


def _old_label(name: str) -> str | None:
    """The label NAME carried just before this one (com.sessionvoice.ears-NAME,
    com.sessionvoice.mlx-audio for speech), else None."""
    return next(iter(_old_labels(name)), None)


def _labels(name: str) -> list[str]:
    """Every label NAME's agent may be under on a host, the new one first: call and room only
    ever had old ones."""
    if name in OLD:
        return _old_labels(name)
    return [_label(name), *_old_labels(name)]


def _ours(path: Path) -> bool:
    """Did `ears services install` write the plist at PATH? Its EnvironmentVariables carry
    MARKER. False for a plist that is absent, unreadable or has no MARKER: a hand render."""
    env = _plist_env(path)
    return bool(env) and env.get(MARKER[0]) == MARKER[1]


def _job_ours(uid: int, label: str) -> bool | None:
    """Is the job loaded as LABEL install's own, by the MARKER in the environment `launchctl
    print` lists for it? None: not loaded. False: loaded, and its environment (as print shows
    it) has no MARKER, or print shows none: a hand job bootstrapped from a plist elsewhere."""
    r = _launchctl("print", f"gui/{uid}/{label}")
    if r.returncode != 0:
        return None
    k, v = MARKER
    return any(line.strip() == f"{k} => {v}" for line in (r.stdout or "").splitlines())


def _hand_at(name: str, uid: int | None = None, look: bool = True) -> tuple[str, Path] | None:
    """(label, plist) when NAME's own label on this host is NOT install's: a hand render,
    which writes the same path. Told by CONTENT, never by the name:
    - its plist in AGENTS: ours only if the plist carries MARKER (absent, unreadable: hand);
    - no plist there but the label LOADED: ours only if the loaded job's environment carries
      MARKER (`launchctl print`); a hand render can be bootstrapped from any path;
    - neither: None, and the label is install's to load.
    Install never overwrites or boots out a hand one. LOOK False asks launchctl nothing (the
    plist alone decides): `uninstall --dry-run` makes no launchctl call at all."""
    label = _label(name)
    path = AGENTS / f"{label}.plist"
    if path.exists():
        return None if _ours(path) else (label, path)
    if look and _job_ours(os.getuid() if uid is None else uid, label) is False:
        return label, path
    return None


def _loaded(uid: int, label: str) -> bool:
    return _launchctl("print", f"gui/{uid}/{label}").returncode == 0


def loaded_label(uid: int, name: str) -> str | None:
    """The label NAME's agent is loaded under, the new one first; None when neither is."""
    return next((l for l in _labels(name) if _loaded(uid, l)), None)


def no_stt_help() -> str:
    """What to run when no speech-to-text will answer the ears (install says what to
    run, not only that it failed)."""
    from .keys import stt_key_path
    return ("what to run: nothing will transcribe for the ears. One of:\n"
            "  - sessionvoice's own mlx-audio (an Apple-silicon Mac, macOS 14+): free :8890, then "
            "`ears services install --only speech`\n"
            "  - Apple's speech on this Mac (macOS 27): `ears install --apple`, then "
            "`EARS_STT_URL=http://127.0.0.1:2023/v1 ears services install --apple`\n"
            "  - a hosted OpenAI-compatible STT: `EARS_STT_URL=https://HOST/v1 EARS_STT_MODEL=MODEL "
            f"ears services install`, its key in {stt_key_path()} (or $EARS_LISTEN_STT_KEY)")


# What a failed C build prints: setuptools' `command '/usr/bin/cc' failed`, and on a Mac with
# no Command Line Tools the cc stub's xcrun error or its xcode-select prompt. setuptools prints
# the same line when the compiler ran and the code failed (a missing header, say), and the
# output cannot tell the two apart, so the help is conditional: true with the CLT or without.
# The speech lock builds no C extension (webrtcvad comes as webrtcvad-wheels), so this is for
# a lock or host that brings one back.
_NO_COMPILER = re.compile(r"command '[^']*\b(?:cc|gcc|clang|c\+\+|g\+\+|clang\+\+)' failed|"
                          r"xcrun: error|xcode-select|CommandLineTools", re.I)
NO_COMPILER_HELP = ("a package built from source needs a C compiler, and its build failed: if "
                    "`xcode-select -p` finds no developer tools on this Mac, install Apple's "
                    "Command Line Tools (`xcode-select --install`), then run this again")


def _needs_compiler(r: subprocess.CompletedProcess) -> bool:
    """True when R's output says a C build failed: no compiler, or a compiler that failed."""
    return bool(_NO_COMPILER.search(f"{r.stdout or ''}\n{r.stderr or ''}"))


def speech_missing_module(py) -> str | None:
    """The first of SPEECH_MODULES that PY cannot import, offline, or None."""
    return _missing_module(py, SPEECH_MODULES)


def speech_refusal() -> str | None:
    """Why the speech server cannot be installed on this host as it stands, read-only and
    offline, or None: speech_checks' first failure among the host, EARS_SPEECH_PY, the shipped
    lock and build constraints, and a uv to build the venv with (or a venv already there). The
    one-pass preflight asks this before anything is written; speech_checks asks it first, so
    the two say the same words. The build and the module imports come later, in speech_checks."""
    bad = _speech_platform()
    if bad:
        return bad
    py = speech_py()
    if os.environ.get("EARS_SPEECH_PY"):
        return None if py.exists() else f"EARS_SPEECH_PY={py}: no such python"
    try:
        _speech_file("requirements.lock")
        _speech_file("build-constraints.txt")
    except NoSpeech as e:
        return str(e)
    if not _uv() and not py.exists():
        return (f"no uv on PATH: the speech server gets its own venv ({py.parent.parent}), built with uv. "
                "Install uv (https://docs.astral.sh/uv/), or set EARS_SPEECH_PY to a python that "
                f"has {SPEECH_PIN} with its server and stt extras")
    return None


def speech_checks(dry_run: bool = False) -> str | None:
    """The install branch only (step 4), before the plist: the host, the python, its modules.
    None when the server can run (built, or would be: --dry-run builds and writes nothing),
    else why not, in one line; the caller says REFUSED or WARNING.

    1. the host: arm64 and macOS 14+ (mlx has no wheel elsewhere);
    2. the python: EARS_SPEECH_PY skips the build (the check still runs on it). Else the venv
       <speech home>/venv: built when absent (`uv venv --python 3.12`), and the shipped lock
       installed into it (`uv pip install --require-hashes -b build-constraints.txt -r
       requirements.lock`; a fresh build checks every artifact against the lock's hashes, a
       re-apply only what it installs). `-b` hash-pins the backend uv fetches to build the
       lock's sdist-only pins: --require-hashes alone does not reach build dependencies.
       A failed install whose output shows a C build failing says how to check for, and
       install, the Command Line Tools (NO_COMPILER_HELP). No uv to build with: refused;
    3. the modules, each imported on its own, offline: the first missing is named."""
    bad = speech_refusal()    # 1, and 2's checks: read-only, the preflight asks the same
    if bad:
        return bad
    py = speech_py()
    if os.environ.get("EARS_SPEECH_PY"):
        print(f"{'speech':6s} python {py} (EARS_SPEECH_PY: no venv built)")
    else:
        venv = py.parent.parent
        try:
            lock = _speech_file("requirements.lock")
            build = _speech_file("build-constraints.txt")     # the sdists' backend, hashed
        except NoSpeech as e:  # gone since speech_refusal() found them
            return str(e)
        pins = sum(1 for l in lock.read_text().splitlines() if l[:1].isalnum())
        uv = _uv()
        if dry_run:
            if not py.exists():
                print(f"{'speech':6s} would build {venv} (python {SPEAKER_PYTHON}, {SPEECH_PIN}, "
                      f"{pins} packages pinned in {lock}; network)")
                return None
            print(f"{'speech':6s} venv present: {py} (install re-applies the {pins} pins in {lock})")
        elif uv:
            if not py.exists():
                speech_home().mkdir(parents=True, exist_ok=True)
                print(f"{'speech':6s} building {venv} (python {SPEAKER_PYTHON}, {SPEECH_PIN}; network)",
                      flush=True)
                clear = ["--clear"] if venv.exists() else []    # a venv cut short: ours, rebuilt
                r = _speech_sh([uv, "venv", "--python", SPEAKER_PYTHON, *clear, venv])
                if r.returncode != 0:
                    return f"uv venv {venv} failed: {_last_line(r)}"
            r = _speech_sh([uv, "pip", "install", "--python", py, "--require-hashes",
                            "-b", build, "-r", lock])
            if r.returncode != 0:
                return (f"uv pip install --require-hashes -b {build} -r {lock} into {venv} failed: "
                        f"{_last_line(r)}" + (f"; {NO_COMPILER_HELP}" if _needs_compiler(r) else ""))
            print(f"{'speech':6s} venv {py}: {pins} packages pinned ({SPEECH_PIN})")
        else:
            print(f"WARNING: no uv on PATH: the venv at {venv} is used as it is, its pins not re-applied")
    missing = speech_missing_module(py)
    if missing:
        return (f"{py} has no {missing}: the speech server needs {SPEECH_PIN} with its server and "
                "stt extras, misaki[en] and en_core_web_sm (speech/requirements.lock)")
    return None


def _speech_said(d) -> tuple[str, str]:
    """(head, said): the step D took, and what it found, as install prints them."""
    return (f"{'speech':6s} step {speech.STEP[d.kind]}, {d.kind}: ",
            d.line.removeprefix("speech: ").removeprefix("REFUSED: "))


def _speech_decide(names, url, port):
    """speech.py's steps 1-4, decided read-only: who holds PORT, and what install does there."""
    return speech.decide(speech.probe(AGENTS, _listed(_launchctl("list").stdout.splitlines()), port=port),
                         url, mouth="mouth" in names)


def speech_step(names, dry_run: bool = False, d: speech.Decision | None = None) -> tuple[bool, bool]:
    """speech.py's steps 1-4 for `install`, before anything is written: (ok, installs).
    It prints which step it took and why. ``installs`` is True only on step 4 when the checks
    pass (the plist loop writes and loads ours; a dry run prints it). ``ok`` is False when the
    ears would be deaf (they hear through :8890 and nothing there will serve them): install
    exits 2 before any plist, and says what to run. When only the mouth wants :8890, a refusal
    is a WARNING: the mouth falls back to `say`, and the rest installs. D: the decision the
    preflight already made (so :8890 is probed once per install); None probes here."""
    url, port = speech.final_url(), _speech_port()
    here = speech.ears_here(url, port=port)
    if d is None:
        d = _speech_decide(names, url, port)
    head, said = _speech_said(d)
    if d.kind != "install":
        if d.fatal:
            print(f"ears services install: REFUSED: {head}{said}", file=sys.stderr)
            print(no_stt_help(), file=sys.stderr)
            return False, False
        tail = "; nothing built or written" if dry_run else "; nothing built, written or started"
        print(head + said + tail)
        return True, False
    print(head + said)
    why = speech_checks(dry_run)
    if why is None:
        return True, True
    if here:
        print(f"ears services install: REFUSED: speech: {why}", file=sys.stderr)
        print(no_stt_help(), file=sys.stderr)
        return False, False
    print(f"WARNING: speech SKIPPED: {why}; the mouth falls back to `say` until :8890 serves")
    return True, False


# The install-time prefetch (docs/models.md): the three SPEECH_WEIGHTS fetched once by install, so
# the first call does not wait on them. The fetch is the server's own call, run on the speech
# server's python: snapshot_download(repo, revision="main", allow_patterns=<the weight's patterns in
# SPEECH_WEIGHT_SIZES>), so the cache holds what the server looks for and refs/main is written.
# Progress is the reader's, polled every PREFETCH_POLL_S while the fetch runs.
PREFETCH_POLL_S = 5
PREFETCH_ENV = "EARS_NO_PREFETCH"
_PREFETCH_PY = ("import sys\n"
                "from huggingface_hub import snapshot_download\n"
                "snapshot_download(sys.argv[1], revision='main', allow_patterns=sys.argv[2:])\n")


def speech_hf_cache() -> Path:
    """The Hugging Face cache the speech SERVER reads, which is where the prefetch fetches to:
    hf_cache() over the HF_* its plist (spec) carries, never this shell's. launchd gives the job
    none of this shell's environment, and the plist sets none today, so it is ~/.cache/huggingface/hub
    under its HOME; an HF_* the plist ever sets wins, by huggingface_hub's own order. The reader
    (weights_reading, weights_status) reads it too, so status and the weights verb report it."""
    return hf_cache(_speech_hf_env())


def _speech_hf_env() -> dict:
    """The HF_CACHE_VARS the speech server runs with: those its plist (spec) carries, and no
    other (launchd passes it none of this shell's)."""
    plist = spec("speech")["EnvironmentVariables"]
    return {k: plist[k] for k in HF_CACHE_VARS if k in plist}


def _prefetch_sh(argv, out, cache: Path) -> subprocess.Popen:
    """Start one weight's fetch (network) into CACHE, the hub cache (HF_HUB_CACHE, which wins
    over every other name): a seam, as _speech_sh, so `make test` fetches nothing. Returns the
    running process; its stdout and stderr go to OUT, a file. Never HF_HUB_OFFLINE, and no
    progress bars: install prints its own progress, from the reader. The HF_CACHE_VARS are the
    server's (_speech_hf_env), not this shell's, so what huggingface_hub keeps beside the hub
    cache under HF_HOME (the xet cache) goes where the server's would, not under this shell's."""
    env = {k: v for k, v in os.environ.items() if k != "HF_HUB_OFFLINE" and k not in HF_CACHE_VARS}
    env.update(_speech_hf_env())
    env["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    env["HF_HUB_CACHE"] = str(cache)
    return subprocess.Popen([str(a) for a in argv], stdin=subprocess.DEVNULL, stdout=out,
                            stderr=subprocess.STDOUT, env=env)


def prefetch_off(flag: bool = False) -> str | None:
    """Why install does not prefetch: --no-prefetch, or EARS_NO_PREFETCH set to 1, true or on.
    None: it prefetches."""
    if flag:
        return "--no-prefetch"
    raw = os.environ.get(PREFETCH_ENV, "").strip()
    return f"{PREFETCH_ENV}={raw}" if raw.lower() in ("1", "true", "on") else None


def _of(n: int, total: int | None) -> str:
    return f"{_size(n)} of {_size(total)}" if total is not None else f"{_size(n)} so far"


def _left(ws: list[dict]) -> str:
    """What is still to fetch for the weights WS, for a person: 'about 2.9 GB', or 'about 2.5 GB
    and the rest of <repo>' when a weight's total is unknown (refs/main moved)."""
    known = sum(w["bytes_total"] - min(w["bytes_done"], w["bytes_total"])
                for w in ws if w["bytes_total"] is not None)
    unknown = [w["repo"] for w in ws if w["bytes_total"] is None]
    if not unknown:
        return f"about {_size(known)}"
    rest = f"all of {', '.join(unknown)} (size unknown: main moved from the measured commit)"
    return f"about {_size(known)} and {rest}" if known else rest


def _fetch_one(w: dict, patterns, plan: list[str], tty: bool, cache: Path) -> str | None:
    """Fetch weight W into CACHE through _prefetch_sh, printing the reader's N of M (of CACHE)
    for the PLAN's repos while it runs. None when it fetched; else why not, in a few words."""
    import tempfile
    argv = [speech_py(), "-c", _PREFETCH_PY, w["repo"], *patterns]
    with tempfile.TemporaryFile("w+") as out:
        try:
            proc = _prefetch_sh(argv, out, cache)
        except OSError as e:
            return f"could not start {speech_py()}: {e}"
        said = None
        try:
            while True:
                try:
                    rc = proc.wait(timeout=PREFETCH_POLL_S)
                    break
                except subprocess.TimeoutExpired:
                    ws = [x for x in weights_reading(cache)["weights"] if x["repo"] in plan]
                    totals = [x["bytes_total"] for x in ws]
                    line = (f"{'speech':6s} weights: fetching {w['repo']}: "
                            f"{_of(sum(x['bytes_done'] for x in ws), None if None in totals else sum(totals))}")
                    if line != said:
                        print("\r" + line + "\x1b[K" if tty else line, end="" if tty else "\n", flush=True)
                        said = line
        except KeyboardInterrupt:
            proc.kill()
            proc.wait()
            rc = "interrupted"
        finally:
            if tty and said:
                print()
        if rc == 0:
            return None
        if rc == "interrupted":
            return "interrupted (Ctrl-C)"
        out.seek(0)
        lines = [l.strip() for l in out.read().splitlines() if l.strip()]
        return f"exit {rc}" + (f": {lines[-1][:200]}" if lines else "")


def speech_weights_total() -> int:
    """Every SPEECH_WEIGHTS entry's pinned size, summed from SPEECH_WEIGHT_SIZES: the whole
    download, in bytes. Never a hand-typed number: re-measure a size and this follows."""
    return sum(size for _, _, size, _, _ in SPEECH_WEIGHT_SIZES)


def speech_heads_up(off: str | None = None, cache: Path | None = None) -> str:
    """The line install prints once it knows it installs the speech server (step 4), before any
    plist is written and so before the prefetch starts: the total size (speech_weights_total),
    what is still to fetch, and that the server runs as a login service. A dry run prints the
    same line. OFF (prefetch_off): the download waits for the first call, and the line says so.
    Every weight already cached in CACHE (default: speech_hf_cache): it says so instead, and
    gives no heads-up."""
    cache = speech_hf_cache() if cache is None else cache
    ws = weights_reading(cache)["weights"]
    todo = [w for w in ws if w["state"] != "cached"]
    if not todo:
        return (f"{'speech':6s} weights: all {len(ws)} already cached in {cache}: no download, so "
                "no heads-up")
    total = _size(speech_weights_total())
    left = "" if len(todo) == len(ws) else f"; {_left(todo)} of it is still to fetch"
    when = (f"not fetched now ({off}): the first call fetches it, and waits" if off else
            f"fetched once, into {cache}, after the plists load (Ctrl-C stops the fetch; the "
            "install goes on)")
    return (f"{'speech':6s} heads-up: the speech models are a {total} download{left}, {when}. "
            f"The speech server runs as a login service ({_label('speech')}): launchd starts it "
            "now and at every login")


def prefetch(dry_run: bool = False, off: str | None = None) -> None:
    """The install-time prefetch of the speech server's three weights (docs/models.md). It
    prints the size first (from the reader), skips and names the weights already cached, and
    fetches the rest one at a time through _prefetch_sh, printing N of M from the reader while
    it runs. OFF (prefetch_off) skips it and says what the first call will fetch; a dry run
    prints the size and the plan and fetches nothing. A failed fetch never fails the install:
    it says why, how much, and that the first call fetches it. A final reader pass follows.
    Every pass reads, and the fetch writes, the cache the server reads (speech_hf_cache)."""
    cache = speech_hf_cache()
    ws = weights_reading(cache)["weights"]
    patterns = {repo: pats for (repo, _), (_, _, _, _, pats) in zip(SPEECH_WEIGHTS, SPEECH_WEIGHT_SIZES)}
    have = [w for w in ws if w["state"] == "cached"]
    todo = [w for w in ws if w["state"] != "cached"]
    head = f"{'speech':6s} weights:"
    for w in have:
        print(f"{head} {w['repo']} cached ({_size(w['bytes_done'])}), skipped")
    if not todo:
        print(f"{head} all {len(ws)} cached in {cache}; nothing to fetch")
        return
    names = ", ".join(w["repo"] for w in todo)
    left = _left(todo)
    if off:
        print(f"{head} prefetch skipped ({off}): the first call will fetch {left} ({names}), "
              f"and wait for it")
        return
    if dry_run:
        print(f"{head} would fetch {left} into {cache}: {names} (snapshot_download, revision main, "
              "the server's own patterns; network). Nothing fetched")
        return
    print(f"{head} fetching {left} into {cache}: {names} (network; --no-prefetch skips this)",
          flush=True)
    if hf_cache() != cache:
        print(f"{head} the speech server's cache, not this shell's ({hf_cache()})")
    plan = [w["repo"] for w in todo]
    tty = sys.stdout.isatty()
    for w in todo:
        why = _fetch_one(w, patterns[w["repo"]], plan, tty, cache)
        now = next(x for x in weights_reading(cache)["weights"] if x["repo"] == w["repo"])
        if why is None:
            print(f"{head} {w['repo']} fetched ({_of(now['bytes_done'], now['bytes_total'])})")
        else:
            print(f"WARNING: speech prefetch of {w['repo']} failed: {why}. The install goes on; "
                  f"the first call will fetch {_left([now])} instead, and wait for it", flush=True)
            if why.startswith("interrupted"):
                break
    for row in weights_status(cache):       # the final reader pass: what is cached now
        print(row)


DEFAULT_STT_URL = "http://127.0.0.1:8890/v1"                 # the speech server: found, or ours
DEFAULT_STT_MODEL = "mlx-community/parakeet-tdt-0.6b-v3"


def _stt_url() -> str:
    return os.environ.get("EARS_STT_URL", DEFAULT_STT_URL)


def _stt_model() -> str | None:
    """The model name the captures ask the STT server for: EARS_STT_MODEL, else Parakeet on
    the default URL. A URL given alone names no model (Apple's server has no Parakeet)."""
    if os.environ.get("EARS_STT_MODEL"):
        return os.environ["EARS_STT_MODEL"]
    return None if os.environ.get("EARS_STT_URL") else DEFAULT_STT_MODEL


WHISPER_URL = "http://127.0.0.1:2022/v1"   # VoiceMode's whisper.cpp: the fallback a failed probe takes
#: How long the probe waits for a speech server on :8890 that this install does not load (an
#: adopted one, VoiceMode's): if it has never loaded Parakeet, its first request downloads the
#: weights (SPEECH_WEIGHT_SIZES' stt row, about 2.5 GB) and loads them before it answers, which
#: no 30 s probe meets on a home link. Every other probe keeps probe_stt's 30 s.
COLD_PROBE_S = 900.0
#: While that probe waits, a line says so this often (seconds)
COLD_TICK_S = 15.0
#: The one command that probes a fallen-back engine again (seed_stt re-probes on a record)
RETRY_STT = "ears services install --only keeper"


def _fallback_path() -> Path:
    """The record of a whisper :2022 fallback: the ears' own state, under sessionvoice's home
    (beside state/heard-cursor), never the settings folder: ears.json's schema has no room
    for it, and a chosen whisper must stay distinguishable from a fallen-back one."""
    from .home import sessionvoice_home
    return Path(sessionvoice_home()) / "state" / "stt-fallback.json"


def stt_fallback() -> dict | None:
    """The fallback record, ``{"final": {url, model?}, "why": str, "at": str}``: the engine the
    install wanted, why its probe failed and when. None when there is none, or it is unreadable."""
    import json
    try:
        d = json.loads(_fallback_path().read_text())
    except Exception:
        return None
    f = d.get("final") if isinstance(d, dict) else None
    if not isinstance(f, dict) or not isinstance(f.get("url"), str):
        return None
    return {"final": f, "why": str(d.get("why", "")), "at": str(d.get("at", ""))}


def _fallback_write(final: dict, why: str) -> None:
    """Record that FINAL fell back to whisper :2022 (atomic). Never fatal: the fallback line
    was already said; a record that cannot be written only loses the later re-probe."""
    import json
    from datetime import datetime
    path = _fallback_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({"final": final, "why": why,
                                   "at": datetime.now().astimezone().isoformat(timespec="seconds")},
                                  indent=2) + "\n")
        os.replace(tmp, path)
    except OSError:
        pass


def _fallback_clear() -> None:
    """Drop the fallback record, when there is one (no record: nothing is touched)."""
    path = _fallback_path()
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def _is_whisper(final) -> bool:
    return isinstance(final, dict) and str(final.get("url", "")).rstrip("/") == WHISPER_URL


def retry_stt(notify: str | None = None) -> str:
    """RETRY_STT, carrying the keeper's notices address (NOTIFY, else the one our keeper plist
    holds) so the re-install does not drop it."""
    import shlex
    if notify is None:
        notify = (_plist_env(AGENTS / f"{_label('keeper')}.plist") or {}).get("EARS_NOTIFY")
    return RETRY_STT + (f" --notify {shlex.quote(notify)}" if notify else "")


def fallback_said(fb: dict, notify: str | None = None) -> str:
    """The loud line a fallback earns: the install's last line, and a `status` row."""
    f = fb["final"]
    what = f"{f['model']} on {f['url']}" if f.get("model") else f["url"]
    since = f" since {fb['at']}" if fb.get("at") else ""
    return (f"{'stt':6s} FALLBACK{since}: the ears hear through whisper :2022, NOT {what}: it "
            f"FAILED its probe ({fb['why']}). When it can answer, `{retry_stt(notify)}` probes it "
            "again and moves the ears back")


def _cold_heads_up(url: str, model: str | None) -> str:
    size = next(s for u, _, s, _, _ in SPEECH_WEIGHT_SIZES if u == "stt")
    what = (f"Parakeet (about {_size(size)})" if model in (None, DEFAULT_STT_MODEL)
            else f"{model}")
    return (f"{'speech':6s} {url} is a speech server this install does not load: if it has not "
            f"loaded {what} yet, its first request downloads and loads it. Waiting up to "
            f"{COLD_PROBE_S / 60:g} min for the probe's answer; Ctrl-C skips to whisper :2022")


def _cold_probe(url: str, model: str | None = None) -> str | None:
    """probe_stt with COLD_PROBE_S, a line every COLD_TICK_S while it waits, and Ctrl-C taken
    as a skip (a reason, so the whisper fallback goes on) instead of the end of the install."""
    import threading, time
    stop, t0 = threading.Event(), time.monotonic()

    def tick():
        while not stop.wait(COLD_TICK_S):
            print(f"probing still waiting for {url}: {time.monotonic() - t0:.0f}s of "
                  f"{COLD_PROBE_S:g}s (Ctrl-C skips to whisper :2022)", flush=True)
    beat = threading.Thread(target=tick, daemon=True)
    beat.start()
    try:
        return probe_stt(url, model, timeout=COLD_PROBE_S)
    except KeyboardInterrupt:
        return "skipped by Ctrl-C"
    finally:
        stop.set()
        beat.join()


def _probe_clip() -> bytes:
    """Half a second of a quiet 220 Hz tone, 16 kHz mono s16 WAV: enough for a server to load
    its model and answer, small enough to cost nothing. Deterministic, stdlib only."""
    import io, math, struct, wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"".join(struct.pack("<h", int(300 * math.sin(2 * math.pi * 220 * i / 16000)))
                               for i in range(8000)))
    return buf.getvalue()


def probe_stt(url: str, model: str | None = None, timeout: float = 30.0) -> str | None:
    """POST one short clip to URL's /audio/transcriptions, as the captures do (WhisperSTT).
    None = it answers: a 200 whose JSON carries a ``text`` string (a tone has no words, so
    the text may be empty; what is refused is a server that cannot transcribe at all, like
    a new host's mlx-audio answering every Parakeet request 500). Else the failure, said
    in a few words. Stdlib only: httpx is the capture extra's, not the installer's."""
    import json, urllib.error, urllib.request, uuid
    from .listen.stt import model_for
    b = uuid.uuid4().hex
    parts = [f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
             for k, v in (("model", model_for(url, model)), ("response_format", "json"))]
    parts.append(f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="probe.wav"\r\n'
                 f"Content-Type: audio/wav\r\n\r\n".encode() + _probe_clip() + b"\r\n")
    body = b"".join(parts) + f"--{b}--\r\n".encode()
    headers = {"Content-Type": f"multipart/form-data; boundary={b}"}
    from .keys import stt_key   # env, else the settings folder's stt.key, else the OpenAI rule
    try:
        key = stt_key(url)   # as WhisperSTT
    except ValueError as e:   # refused by its source's NAME, before any request: never the key
        return f"no key sent: {e}"
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url.rstrip("/") + "/audio/transcriptions", data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        detail = e.read()[:160].decode(errors="replace").strip().replace("\n", " ")
        return f"HTTP {e.code}" + (f": {detail}" if detail else "")
    except Exception as e:
        return f"no answer: {e}"
    if status != 200:
        return f"HTTP {status}"
    try:
        d = json.loads(raw)
    except Exception:
        return f"200 but not JSON: {raw[:80]!r}"
    if not isinstance(d, dict) or not isinstance(d.get("text"), str):
        return "200 but no text in the answer"
    return None


def seed_stt(dry_run: bool = False, no_probe: str | None = None,
             fell_back: list | None = None) -> str:
    """Write ``final`` into ears.json: from EARS_STT_URL/EARS_STT_MODEL when given (they
    replace it), else the default (Parakeet on mlx-audio) only when the file has none. An
    old ``stt`` counts as one (it is ``final``'s old name); a written ``final`` drops it, so
    the file never names two engines. ``partials`` is never written here. Every other key is
    kept; the write is atomic (the keeper reads the file every loop). Returns what it did.

    A NEW final is probed first (probe_stt): one that does not answer is not written, and
    whisper :2022 is, said loudly with the failure (a new host's ears.json once named a
    Parakeet the installed mlx-audio could not load, and the capture went deaf). A speech
    server on :8890 that this install does not load is probed cold-aware (_cold_probe): it may
    have to download and load Parakeet first. A fallback is recorded (stt_fallback); a kept
    final is not re-probed, except a kept whisper :2022 with that record, whose wanted engine
    is probed again and written back once it answers. ``no_probe`` (a reason) skips the
    probe: the server is one this install has not loaded yet. ``dry_run``: says what it would
    write, writes nothing, records nothing (it still probes). ``fell_back`` (a list): the
    fallback the ears end on, if any, is appended to it (stt_fallback's shape), for install's
    closing line; install calls this, never _seed_stt, so a test's stand-in for it holds.
    A STRANDED ears.json (``ears.home.stranded``) is not written, nor probed for."""
    said, fb = _seed_stt(dry_run, no_probe)
    if fb and fell_back is not None:
        fell_back.append(fb)
    return said


def _seed_stt(dry_run: bool = False, no_probe: str | None = None) -> tuple[str, dict | None]:
    """seed_stt, and the fallback it ends on (stt_fallback's shape, None when the ears do not
    end on a fallback), for install's closing line."""
    import json
    from .home import refuse_stranded
    from .listen.stt import conf_path
    path = Path(conf_path())
    why = refuse_stranded(path, "ears.json", "ears services")   # a 0.1.0 file not moved yet
    if why:
        return f"final NOT set: {why}", None
    try:
        d = json.loads(path.read_text())
    except Exception:
        d = {}
    would = "would be " if dry_run else ""

    def write():
        if dry_run:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(d, indent=2) + "\n")
        os.replace(tmp, path)

    def probe(url, model=None):
        # said BEFORE every request, the dry run's too: a check may ask the user's own STT, but
        # never silently
        cold = speech.ears_here(url, port=_speech_port())   # not loaded here (else no_probe)
        if cold:
            print(_cold_heads_up(url, model), flush=True)
        print(f"probing {url} (read-only)", flush=True)
        return _cold_probe(url, model) if cold else probe_stt(url, model)

    def record(final, why):
        if not dry_run:
            _fallback_write(final, why)
        return {"final": final, "why": why, "at": ""}

    def forget():
        if not dry_run:
            _fallback_clear()

    given = bool(os.environ.get("EARS_STT_URL") or os.environ.get("EARS_STT_MODEL"))
    have = d.get("final") or d.get("stt")
    if have and not given:
        if "stt" not in d:
            fb = stt_fallback()
            if fb is None:
                return f"final kept: {have}", None
            if not _is_whisper(have):     # set by hand since: whisper was no fallback any more
                forget()
                return f"final kept: {have}", None
            want = fb["final"]            # the fallen-back engine: probe it again
            if no_probe and want["url"].rstrip("/") == _stt_url().rstrip("/"):
                said, why = f" (not probed: {no_probe})", None
            else:
                why = probe(want["url"], want.get("model"))
                said = " (probe: it answers)"
            if why is not None:
                fb = record(want, why)
                return (f"final kept: {have} - still the FALLBACK: {want} FAILED its probe again "
                        f"({why})"), fb
            d["final"] = want
            write()
            forget()
            return (f"final {'would be set' if dry_run else 'set'}: {want}{said}, back from the "
                    "whisper :2022 fallback"), None
        # the old name goes: the schema no longer draws it, and refuses a file that has it.
        # Beside final it is dead (final wins in every reader); alone it becomes final.
        old = d.pop("stt")
        how = "dropped stt, final wins" if "final" in d else "renamed from stt"
        d.setdefault("final", old)
        write()
        return f"final kept: {d['final']} ({would}{how})", None
    stt = {"url": _stt_url()}
    if _stt_model():
        stt["model"] = _stt_model()

    said, fb = "", None
    if no_probe:
        said = f" (not probed: {no_probe})"
        forget()
    else:
        why = probe(stt["url"], stt.get("model"))
        if why is None:
            said = " (probe: it answers)"
            forget()
        elif stt["url"].rstrip("/") == WHISPER_URL:
            said = (f" (FAILED its probe: {why}; start whisper :2022, or set final in {path})\n"
                    + no_stt_help())
            forget()                      # whisper was chosen: nothing to move back to
        else:
            failed, stt = stt, {"url": WHISPER_URL}
            fb = record(failed, why)
            why2 = probe(WHISPER_URL)
            said = (f" - FALLBACK: {failed} FAILED its probe ({why}), so whisper :2022 instead"
                    + (f", which did not answer either ({why2}): start whisper :2022, or set final "
                       f"in {path} to an engine that answers\n" + no_stt_help()
                       if why2 else " (probe: it answers)"))
    d.pop("stt", None)
    d["final"] = stt
    write()
    return f"final {'would be set' if dry_run else 'set'}: {stt}{said}", fb


def spec(name: str, notify: str | None = None) -> dict:
    """The launch agent for one service, as a plist dict. ``notify``: the keeper's EARS_NOTIFY,
    the address its notices go to (unset: log only, ears-keeper.sh's NOTIFY)."""
    py, bindir = _py(), str(Path(_py()).parent)
    # ~/.local/bin: the uv tools the services shell out to (`message send`: the keeper's
    # notices, ears-mail's turns and the mouth's notices); without it every keeper mail
    # failed "message: command not found"
    env = {"PATH": _service_path(),
           "EARS_PY": py,   # no STT here: it is ears.json's (seed_stt), so a change needs no reinstall
           MARKER[0]: MARKER[1]}   # how install knows this plist is its own (_ours)
    if name == "keeper":
        # the seat the ears write and mail as (the keeper's `message send` reads it);
        # what the hand template's keeper (launchd/dev.sessionkit.voice.ears-keeper) carries
        seat = os.environ.get("EARS_AGENT", "ears")
        env.update(EARS_AGENT=seat, TM_AGENT_ADDRESS=seat)
        # one log folder: the keeper's own logs beside the agents' ones
        env["EARS_LOG_DIR"] = str(LOGS)
        if notify:
            env["EARS_NOTIFY"] = notify
        if os.environ.get("EARS_SILERO_ONNX"):   # the model install checked is the one it reads
            env["EARS_SILERO_ONNX"] = os.environ["EARS_SILERO_ONNX"]
    args = {     # lambdas: only the named one is built, so no keeper script spares the mouth
        "stt": lambda: [py, "-m", "ears.cli", "serve", os.environ.get("EARS_STT_PORT", "2023")],
        "mouth": lambda: [py, "-m", "mouth", "mail"],
        "keeper": lambda: ["/bin/bash", str(keeper_script()), "default"],
        # what launchd/dev.sessionkit.voice.ears-mail.plist.in runs, on the agents' python (stdlib only)
        "mail": lambda: [py, str(mail_script()), "--to", "device", "--sender", "heard",
                         "--rules", str(_wake_file()),
                         "--state", str(Path.home() / ".cache/ears/ears-mail.state.json"),
                         "--fire-log", str(LOGS / "ears-mail.fire.jsonl"),
                         # an again verdict runs `mouth again`: the mouth on this python
                         "--mouth", shlex.join([py, "-m", "mouth"])],
        # the hand template's job (launchd/dev.sessionkit.voice.ears-speakers.plist.in), its run.sh
        # found in the checkout or the package, its data in the speakers home
        "speakers": lambda: ["/bin/bash", str(_speakers_file("run.sh"))],
        # the sounds templates' jobs (launchd/dev.sessionkit.voice.{ears,mouth}-sounds.plist.in)
        # on the agents' python, which has numpy and sounddevice (base dependencies); the script
        # found in the checkout or the package. ears-sounds takes its STOP file only as a flag
        "ears-sounds": lambda: [py, str(sounds_script("ears-sounds")),
                                "--settings", str(_sounds_settings("ears-sounds")),
                                "--sound-log", str(LOGS / "ears-sounds.jsonl"),
                                "--stop-file", str(voice_dir() / "STOP-ears-sounds")],
        "mouth-sounds": lambda: [py, str(sounds_script("mouth-sounds"))],
        # the talk template's job (launchd/dev.sessionkit.voice.talk.plist.in): the installed
        # console script, on loopback only
        "talk": lambda: [str(talk_bin()), "serve", "--host", "127.0.0.1", "--port", str(_talk_port())],
        # mlx-audio's server on its own venv's python, on 127.0.0.1 only; its logs
        # beside the agents' (its --log-dir default is a relative "logs")
        "speech": lambda: [str(speech_py()), "-m", "mlx_audio.server", "--host", speech.HOST,
                           "--port", str(_speech_port()), "--log-dir", str(LOGS / "mlx-audio")],
    }[name]()
    log = LOGS / (f"{name}.log" if name in JOB_LABELS else f"ears-{name}.log")   # the templates' names
    d = {"Label": _label(name), "ProgramArguments": args, "EnvironmentVariables": env,
         "WorkingDirectory": str(Path.home()), "RunAtLoad": True, "KeepAlive": True,
         "ThrottleInterval": 10,
         "StandardOutPath": str(log),
         "StandardErrorPath": str(log)}
    if name == "keeper":
        # a keeper restart adopts the running captures, room and call alike: no gap
        d["AbandonProcessGroup"] = True
    if name == "speakers":
        from .heard import log_dir
        home = speakers_home()
        env.pop("EARS_PY")    # it runs on SPEAKER_PY, never the ears' python
        env.update(
            SPEAKER_PY=str(speaker_py()),
            SPEAKER_HOME=str(home),                     # refs/, STOP: run.sh cds here
            SPEAKER_LOGDIR=str(log_dir()),              # the heard log the ears write (ears.home)
            HOME=str(Path.home()),                      # the template's key: the model cache install filled
            SPEAKER_DEVICE="follow",
            SPEAKER_AGENT_REFS=os.environ.get("EARS_SPEAKER_AGENT_REFS", ""),
            HF_HUB_OFFLINE="1")                         # a start never downloads
        d["WorkingDirectory"] = str(home)
        # the template's restart semantics: `touch STOP` exits 0 and must STAY down, and a
        # refused reference reloads the model on every start, so not every 10 s
        d["KeepAlive"] = {"SuccessfulExit": False}
        d["ThrottleInterval"] = 30
    if name == "mouth-sounds":
        # mouth-sounds.py reads its paths from the environment only. Its settings, sound log
        # and STOP file on the kit's paths (STOP beside the script would be in the package);
        # the mid-turn marker and the opener's canned lines where their writers put them, the
        # keeper's RUN dir (skills/ears/scripts/ears-mid-turn, ears-mouth-tail), so the
        # watcher hears what they write
        run = keeper_run()
        env.update(MOUTH_SOUNDS_SETTINGS=str(_sounds_settings("mouth-sounds")),
                   MOUTH_SOUNDS_LOG=str(LOGS / "mouth-sounds.jsonl"),
                   MOUTH_SOUNDS_STOP=str(voice_dir() / "STOP-mouth-sounds"),
                   MOUTH_SOUNDS_FIRED=str(run / "mid-turn.fired"),
                   MOUTH_SOUNDS_CANNED=str(run / "auto-opener.utts"))
        d["ThrottleInterval"] = 30            # the template's: it exits 3 after 20 failed sounds
    if name in SOUNDS_FILES:
        # each exits 0 when its STOP file appears, and must STAY down (the speakers' semantics);
        # a failure exit (too many failed sounds in a row) restarts it fresh
        d["KeepAlive"] = {"SuccessfulExit": False}
    if name == "talk":
        env.pop("EARS_PY")    # it runs on its console script's own python
        env.update(HOME=str(Path.home()), PYTHONUNBUFFERED="1")   # the template's keys
    if name == "speech":
        env.pop("EARS_PY")    # it runs on speech_py(), never the ears' python
        env["PATH"] = env["PATH"].replace(bindir, str(speech_py().parent), 1)
        d["WorkingDirectory"] = str(speech_home())
    return d




def _sounds_settings(job: str) -> Path:
    """JOB's settings file, ~/.sessionkit/voice/settings/JOB.json (ears-sounds.json,
    mouth-sounds.json): reread live by the watcher, so a change needs no restart."""
    from .home import settings_dir
    return settings_dir() / f"{job}.json"


def _launchctl(*a: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *a], capture_output=True, text=True)


class OldStillLoaded(RuntimeError):
    """The old label would not go within the wait: the new one is NOT loaded beside it.
    ``label``: the one that would not go."""

    def __init__(self, msg: str, label: str = ""):
        super().__init__(msg)
        self.label = label


def _bootstrap(uid: int, name: str, path: Path, wait_s: float | None = None, old=None):
    """bootstrap once the old job has gone. bootout returns before launchd has removed the
    job, and a bootstrap in that window fails "5: Input/output error" (seen twice, the
    room ear left unloaded until a retry by hand).

    OLD, the pre-rename label this install booted out, is waited for too, for as long: if it
    is still loaded at the deadline, OldStillLoaded, and nothing is bootstrapped (two keepers
    would fight over the capture). OLD may be a list: the legacy renders replaced too
    (_replace_legacy), each waited for the same way. The new label still there at the
    deadline is tried anyway, as before. At most one load succeeds: the retry runs only
    when the first attempt failed."""
    import time
    wait = BOOT_WAIT_S if wait_s is None else wait_s
    deadline = time.monotonic() + wait
    olds = [o for o in ([old] if isinstance(old, str) else (old or ())) if o]
    waiting = [*olds, _label(name)]
    while waiting and time.monotonic() < deadline:
        waiting = [l for l in waiting if _loaded(uid, l)]
        if waiting:
            time.sleep(0.2)
    stuck = [o for o in olds if o in waiting and _loaded(uid, o)]
    if stuck:
        o = stuck[0]
        raise OldStillLoaded(f"{o} is still loaded after {wait:g}s: "
                             f"{_label(name)} NOT loaded beside it (two would run). Boot it out "
                             f"(launchctl bootout gui/{uid}/{o}), then `ears services install --only {_service(name)}`", o)
    r = _launchctl("bootstrap", f"gui/{uid}", str(path))
    if r.returncode != 0:                      # one more go: the window can outlast print's answer
        time.sleep(1.0)
        r = _launchctl("bootstrap", f"gui/{uid}", str(path))
    return r


def install_schema() -> str:
    """Copy each settings schema the ears ship (settings/NAME.schema.json) beside its settings
    file as the dot file .NAME.schema.json, which Settings draws the screen from, and remove
    the old visible NAME.schema.json. The tool owns its schema; nobody edits the copy.
    Each copy lands whole (a temp file,
    then a rename), because Settings may read it at any moment. The mouth's schema goes
    beside the mouth's settings, which is the ears' folder too unless a variable moves
    one (``ears.home.settings_dir``): beside $MOUTH_SETTINGS when it is set, else in
    that folder whether or not mouth.json exists yet, never the old
    <sessionvoice home>/mouth. The schemas come from schema_files(): a checkout's settings/, else the wheel's ears/_settings/; none there is
    NoSchema, raised before any copy. A schema whose settings file is STRANDED
    (``ears.home.stranded``) is not copied into the settings folder: nothing is written
    there until the host step has moved the 0.1.0 file."""
    import shutil
    from .home import refuse_stranded
    from .listen.stt import conf_path
    ears_dir = Path(conf_path()).parent
    srcs = schema_files()     # all found, or NoSchema: never a partial or an empty copy
    done, held = [], []
    for src in srcs:
        d = _mouth_schema_dir() if src.name == "mouth.schema.json" else ears_dir
        name = src.name.replace(".schema.json", ".json")   # ears.schema.json -> ears.json
        if refuse_stranded(d / name, name, "ears services"):
            held.append(src.name)
            continue
        d.mkdir(parents=True, exist_ok=True)
        dst, tmp = d / f".{src.name}", d / f".{src.name}.tmp"
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
        (d / src.name).unlink(missing_ok=True)
        done.append(dst.name)
    line = f"schema: {', '.join(done) or 'none'} in {ears_dir}"
    return line + (f"; NOT copied (stranded, nothing written): {', '.join(held)}" if held else "")


def _mouth_schema_dir() -> Path:
    """Where the mouth's schema goes: beside $MOUTH_SETTINGS if it is set, else the
    voice settings folder (``ears.home.settings_dir``). Never settings_path()'s fallback:
    that is <sessionvoice home>/mouth until the user's mouth.json exists."""
    from mouth.voices import home_settings_path
    env = envnames.get("MOUTH_SETTINGS")
    return Path(env).expanduser().resolve().parent if env else home_settings_path().parent


def _retire_old(uid: int, listed: dict[str, str] | None = None) -> str:
    """Boot out and remove the old ears-call and ears-room agents, under every old prefix,
    before the keeper loads: two room keepers would fight over one capture. ears-call goes
    with its process group, so a live call's ear dies with it; the keeper's call-ears starts
    a new one within seconds (a live call with no ear gets one)."""
    if listed is None:
        listed = _listed(_launchctl("list").stdout.splitlines())
    gone = []
    for o in OLD:
        for label in _old_labels(o):
            path = AGENTS / f"{label}.plist"
            # print only on a sign of it (listed, or its plist): a host with neither gets no
            # call naming the old label, so a second install makes none
            loaded = (label in listed or path.exists()) and _loaded(uid, label)
            if loaded:
                _launchctl("bootout", f"gui/{uid}/{label}")
            if loaded or path.exists():
                path.unlink(missing_ok=True)
                if o not in gone:
                    gone.append(o)
    return f"old    {', '.join(gone) + ' removed: the keeper runs them now' if gone else 'none'}"


def _wake_file() -> Path:
    """The wake rules ears-mail reads: the file `ears wake add` writes ($EARS_WAKE, else the
    user file), so the seat install adds is the seat the mailer calls."""
    from .wake import target
    return target()


# --- install checks: each says what it found; none configures the host's mail ---
#: What ears-mail and the mouth's notices send by: sessionmail's `message`, found
#: on the services' own PATH (spec). A path here is checked as it is (tests patch it).
MESSAGE = "message"
#: sessionmail's carrier switch: `drop` writes to the local maildirs, with no postfix.
CARRIER_VAR = "SESSIONMAIL_SENDMAIL"
#: sessionmail's settings file, as sessionmail names it (<kit root>/mail/settings/mail.json;
#: the kit root is named once, in ears.home). Its `sendmail` key is what the launchd services
#: send by; the variable above reaches only this shell's children. Shown, never read here.
MAIL_SETTINGS = f"~/{Path(_VOICE_SETTINGS).parts[0]}/mail/settings/mail.json"
#: The heard box on a mail-drop host: FLAT, and made by sessionmail's own verb, never by hand.
HEARD = "heard"
SETUP_BOX = f"message setup --box {HEARD}"


def _service_path() -> str:
    """The PATH every service runs with (spec): its python's bin dir, ~/.local/bin (the uv
    tools: `message`), Homebrew, then the system's."""
    bindir, local_bin = str(Path(_py()).parent), str(Path.home() / ".local/bin")
    return f"{bindir}:{local_bin}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"


def _message_bin() -> str | None:
    """`message` as the services will find it, or None."""
    import shutil
    return shutil.which(os.path.expanduser(MESSAGE), path=_service_path())


def _silero_want() -> Path:
    """Where the keeper's capture reads the model: EARS_SILERO_ONNX, else where `ears install`
    puts it (ears-keeper.sh SILERO, src/ears/vad.py)."""
    return Path(os.path.expanduser(os.environ.get("EARS_SILERO_ONNX") or "~/.cache/ears/silero_vad.onnx"))


def _silero_copy() -> Path:
    """One known copy on a VoiceMode host (seen on a new host's install)."""
    return Path.home() / ".voicemode/models/silero/silero_vad.onnx"


# How install fetches a missing Silero model: None is urllib's opener (vad.fetch_silero's own
# default). Tests set it to a fake, and the install tests' fence to one that refuses the network.
SILERO_OPENER = None


def silero(dry_run: bool = False) -> tuple[bool, str]:
    """(ok, line): the Silero VAD model the keeper's capture needs. Present: ok. Missing, and
    VoiceMode's copy is there: link it (dry run: would link). Neither: fetch it by the pin
    `ears install` fetches by (vad.SILERO_URL, SHA-256 checked; dry run: would fetch, and
    fetches nothing), saying where from and to before it starts. A failed fetch is not ok, and
    install REFUSES before any plist is written (without it the capture dies every 4 s);
    fetch_silero leaves nothing behind."""
    from . import vad
    want, copy = _silero_want(), _silero_copy()
    if _nonempty(want):
        return True, f"silero present: {want}"
    if _nonempty(copy):
        if dry_run:
            return True, f"silero would link {want} -> {copy} (VoiceMode's copy)"
        want.parent.mkdir(parents=True, exist_ok=True)
        if want.is_symlink() or want.exists():
            want.unlink()                  # a dangling link, or an empty file: never the model
        want.symlink_to(copy)
        return True, f"silero linked {want} -> {copy} (VoiceMode's copy)"
    what = f"Silero VAD {vad.SILERO_TAG} ({_size(vad.SILERO_SIZE)}) from {vad.SILERO_URL} -> {want}"
    if dry_run:
        return True, f"silero would fetch {what}"
    print(f"silero fetching {what}", flush=True)
    try:   # the pin read now, so a test can pin a fake's sha; a dangling link or empty file is replaced
        vad.fetch_silero(want, url=vad.SILERO_URL, sha256=vad.SILERO_SHA256, opener=SILERO_OPENER)
    except vad.SileroFetchError as e:
        return False, f"silero: {e}; the keeper's capture cannot start without it"
    return True, (f"silero fetched: {want} matches the pin ({vad.SILERO_TAG}, "
                  f"sha256 {vad.SILERO_SHA256[:12]})")


def silero_not_fetched() -> str | None:
    """The line a refused install says about a Silero model it would have fetched, or None when
    the model (or VoiceMode's copy) is there. A refused run fetches nothing: this is the fetch
    it would make once the refusals are fixed. Read-only."""
    from . import vad
    want, copy = _silero_want(), _silero_copy()
    if _nonempty(want) or _nonempty(copy):
        return None
    return (f"ears services install: not fetched: Silero VAD {vad.SILERO_TAG} "
            f"({_size(vad.SILERO_SIZE)}) is missing at {want} (nor VoiceMode's {copy}); install "
            "fetches it once the refusals above are fixed")


def _nonempty(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _postconf(param: str) -> str | None:
    """postfix's value for PARAM (read only), or None when postconf is missing or fails."""
    import shutil
    pc = shutil.which("postconf") or "/usr/sbin/postconf"
    try:
        r = subprocess.run([pc, "-h", param], capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def _vmailbox_gen() -> tuple[int, str] | None:
    """Run vmailbox-gen, as this user and never with sudo: (exit, stderr), or None when it is
    not on PATH."""
    import shutil
    gen = shutil.which("vmailbox-gen")
    if not gen:
        return None
    r = subprocess.run([gen], capture_output=True, text=True, timeout=60)
    return r.returncode, r.stderr.strip()


def _mail_root() -> Path:
    return Path(os.environ.get("MAIL_ROOT") or Path.home() / ".mail")


def _mail_agents() -> Path:
    return _mail_root() / "agents"


def _vmailbox_gen_cmd() -> str:
    """The exact vmailbox-gen line to print when it is not on PATH. sessionmail keeps the
    generator OFF the PATH on purpose (its pyproject.toml) and a host's copy sits at
    $MAIL_ROOT/.postfix/vmailbox-gen, so a bare `vmailbox-gen` names a command no host has."""
    import shutil
    if shutil.which("vmailbox-gen"):
        return "vmailbox-gen"
    return str(_mail_root() / ".postfix" / "vmailbox-gen")


def _plist_env(path: Path) -> dict | None:
    """PATH's plist's EnvironmentVariables ({} when it names none), or None when the plist
    cannot be read (absent: a job loaded with its file gone; or not a plist)."""
    try:
        d = plistlib.loads(path.read_bytes())
    except Exception:
        return None
    env = d.get("EnvironmentVariables") if isinstance(d, dict) else None
    return {k: str(v) for k, v in env.items()} if isinstance(env, dict) else {}


def _message_lines(runs: tuple[str, Path] | None = None) -> list[str]:
    """The `message` check, against the mailer that will RUN. RUNS None: ours, on
    _service_path(). RUNS (label, plist): a hand-made mailer is left running, so the check
    reads THAT plist's PATH and HOME (launchd's bare PATH when it names none) and looks as
    ears-mail does: an EARS_MAIL_MESSAGE it sets, on its PATH alone; else `message` on its
    PATH, else where uv installs it (ears.message_bin). Never `present` from our PATH for a
    job that does not run on it: that was the false green while a launchd PATH held neither."""
    import shutil
    if runs is None:
        msg = _message_bin()
        if msg:
            return [f"message present: {msg}"]
        return [f"WARNING: message MISSING: sessionmail's `{MESSAGE}` is not on the services' "
                "PATH. ears-mail and the mouth's notices send by it, so no agent is paged. "
                "Install sessionmail first"]
    label, plist = runs
    redo = (f"Re-render it (bin/render-plist launchd/{_label('mail')}.plist.in), or remove it and run "
            "`ears services install --only mail`")
    env = _plist_env(plist)
    if env is None:
        return [f"WARNING: message UNCHECKED: the hand-made {label} is the mailer that runs, and "
                f"its plist ({plist}) cannot be read, so the PATH it sends on is unknown. {redo}"]
    path = env.get("PATH") or LAUNCHD_PATH
    explicit = env.get("EARS_MAIL_MESSAGE", "").strip()
    try:
        name = shlex.split(explicit)[0] if explicit else MESSAGE
    except (ValueError, IndexError):
        return [f"WARNING: message MISSING for {label}: its EARS_MAIL_MESSAGE ({explicit!r}) does "
                f"not parse, so every turn fails 127 and no agent is paged. {redo}"]
    found = shutil.which(os.path.expanduser(name), path=path)
    if found:
        return [f"message present: {found} (on {label}'s PATH: the mailer that runs)"]
    if explicit:
        return [f"WARNING: message MISSING for {label}: its EARS_MAIL_MESSAGE ({name}) is not an "
                f"executable on its PATH ({path}), so every turn fails 127 and no agent is "
                f"paged. {redo}"]
    from . import message_bin
    menv = {"PATH": path, "HOME": env.get("HOME") or str(Path.home())}
    found = message_bin.find(menv)
    if found:
        return [f"WARNING: message is not on {label}'s PATH ({path}): ears-mail finds it only by "
                f"the fallback to where uv installs it ({found}). A mailer from before "
                f"that fails every turn with 127, and no agent is paged. {redo}"]
    return [f"WARNING: message MISSING for {label}: not on its PATH ({path}) nor at "
            f"{message_bin.where_looked(menv)}, so every turn fails 127 and no agent is paged. "
            f"Install sessionmail. {redo}"]


def _setup_env(runs: tuple[str, Path] | None = None) -> dict[str, str]:
    """The environment the mailer that RUNS has, not this shell's: launchd gives a service
    only its plist's. Ours: the services' PATH and HOME. A hand-made mailer: its plist's
    PATH, HOME and SESSIONMAIL_SENDMAIL, when it sets them."""
    env = {"PATH": _service_path(), "HOME": str(Path.home())}
    if runs is not None:
        penv = _plist_env(runs[1]) or {}
        env.update({k: v for k, v in penv.items() if k in ("PATH", "HOME", CARRIER_VAR)})
    return env


def _message_setup(argv: list[str], runs: tuple[str, Path] | None = None
                   ) -> tuple[int, str, str] | None:
    """`message setup ARGV`, run as the services would: (exit, stdout, stderr), or None when
    there is no `message` or it could not start. Its checks read only; `--dry-run` applies
    nothing. Its exit is not read for the answer (4 is common: a step left, such as notmuch
    not installed): its rows are."""
    msg = _message_bin()
    if not msg:
        return None
    try:
        r = subprocess.run([msg, "setup", *argv], capture_output=True, text=True, timeout=60,
                           env=_setup_env(runs), stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.returncode, r.stdout, r.stderr


_SETUP_ROW = re.compile(r"^  (\S+)\s+(DONE|TODO|BLOCKED|MANUAL)\s+(.*)$")
_APPLIED_ROW = re.compile(r"^  (\S+)\s+(.*)$")


def _setup_rows(text: str) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    """`message setup`'s printed plan, {step: (state, what)}, and what it applied,
    {step: line}. A continuation line (a step's long reason) is not a row. This reads
    sessionmail's human-readable text: a stated fallback. sessionmail keeps its `carrier`
    row stable as a contract until it offers a machine-readable carrier query."""
    plan: dict[str, tuple[str, str]] = {}
    applied: dict[str, str] = {}
    block = None
    for line in text.splitlines():
        if not line.startswith("  "):
            block = line if line in ("steps:", "applying:") else None
            continue
        if block == "steps:" and (m := _SETUP_ROW.match(line)):
            plan[m[1]] = (m[2], m[3])
        elif block == "applying:" and (m := _APPLIED_ROW.match(line)):
            applied[m[1]] = m[2]
    return plan, applied


def sendmail_in_effect(runs: tuple[str, Path] | None = None) -> tuple[str, str, tuple | None]:
    """(kind, said, got): what the launchd services send by, in sessionmail's own words, from
    `message setup --box heard --dry-run` in their environment (sessionmail 0.2.0 and later).
    KIND: "drop", "mta", "mta-broken" (an MTA named that is not an executable file),
    "refused" (setup refuses: its words in SAID) or "unknown". GOT: that run, reused for the
    heard box. The rule is sessionmail's and is never re-derived here."""
    got = _message_setup(["--box", HEARD, "--dry-run"], runs)
    if got is None:
        return "unknown", "sessionmail's `message` could not be run", None
    code, out, err = got
    if code == 2 and "REFUSED" in err:
        return "refused", err.strip().splitlines()[-1], got
    state, what = _setup_rows(out)[0].get("carrier", ("", ""))
    if what.startswith("the mail drop"):
        return "drop", what, got
    if state == "DONE" and what.startswith("the MTA at"):
        return "mta", what, got
    if state == "MANUAL":
        return "mta-broken", what, got
    return "unknown", ("its `message` has no `setup` verb, or printed no carrier row "
                       f"(exit {code})"), got


def _heard_drop(dry_run: bool, got: tuple[int, str, str] | None,
                runs: tuple[str, Path] | None) -> list[str]:
    """The heard box on a mail-drop host: FLAT, made by `message setup --box heard`, which is
    idempotent. A heard box already NESTED under a store dir is used as it is. GOT: the dry
    run sendmail_in_effect already made. DRY_RUN makes nothing. A failure is a WARNING
    naming the command, and install goes on."""
    lost = ("Run it by hand: the mail drop never makes a box, so until the heard box exists "
            "each heard turn is refused and nothing is delivered")
    plan = _setup_rows(got[1])[0] if got else {}
    state, what = plan.get(f"box:{HEARD}", ("", ""))
    out: list[str] = []
    agents = _mail_agents()           # a fleet host on the drop keeps its nested box: no second one
    nested = [d / HEARD for d in sorted(agents.glob("*")) if (d / ".tm-store").is_file()
              and (d / HEARD / "new").is_dir()] if agents.is_dir() else []
    if nested:
        out.append(f"heard  box present: {nested[0]}")
    elif state == "DONE":
        out.append(f"heard  box present: {what}")
    elif state == "TODO" and dry_run:
        out.append(f"heard  would make the heard box: `{SETUP_BOX}` ({what})")
        also = [f"{k} ({w})" for k, (s, w) in plan.items() if s == "TODO" and k != f"box:{HEARD}"]
        if also:
            out.append("mail   `message setup` would also, as its plan has it: " + "; ".join(also))
    elif state == "TODO":
        ran = _message_setup(["--box", HEARD], runs)
        applied = _setup_rows(ran[1])[1] if ran else {}
        made = applied.get(f"box:{HEARD}", "")
        if made.startswith("made "):
            out.append(f"heard  box {made} (by `{SETUP_BOX}`)")
        else:
            why = made or (ran[2].strip().splitlines()[-1] if ran and ran[2].strip() else
                           f"exit {ran[0]}" if ran else "it could not be run")
            out.append(f"WARNING: heard box NOT made: `{SETUP_BOX}`: {why}. {lost}")
        also = [f"{k} {line}" for k, line in applied.items() if k != f"box:{HEARD}"]
        if also:
            out.append("mail   `message setup` also did, as its plan has it: " + "; ".join(also))
    else:
        out.append(f"WARNING: heard box NOT made: `{SETUP_BOX}`: "
                   f"{what or 'no heard row in its plan'}. {lost}")
    out.append("heard  an agent hears heard turns only once it subscribes to heard+<device> "
               "(heard+macbook for the built-in mic; ears-mail.py device_slug names the rest)")
    return out


def _heard_mta(dry_run: bool) -> list[str]:
    """The heard box on an MTA host (the fleet's layout): NESTED under the one store dir
    carrying .tm-store, then routed with vmailbox-gen, which writes postfix's maps."""
    out: list[str] = []
    maps = _postconf("virtual_mailbox_maps")
    if not maps:
        out.append("WARNING: postfix has no virtual_mailbox_maps: heard+<device>@<host> "
                   "reaches no maildir by postfix, so no agent is paged. Set up this host's "
                   "agent mail (sessionmail) first")
    else:
        out.append(f"postfix present (virtual_mailbox_maps: {maps}); not changed")
    agents = _mail_agents()
    stores = sorted(d for d in agents.glob("*") if (d / ".tm-store").is_file()) if agents.is_dir() else []
    have = [d / HEARD for d in stores if (d / HEARD / "new").is_dir()]
    flat = agents / HEARD
    gen = _vmailbox_gen_cmd()
    if have:
        out.append(f"heard  box present: {have[0]}")
        loose = [d for d in (have[0], *(have[0] / s for s in maildir.SUBDIRS))
                 if d.is_dir() and (d.stat().st_mode & 0o777) != maildir.DIR_MODE]
        if loose and dry_run:
            out.append(f"heard  would make {have[0]} and its new/cur/tmp 0700 (an agent box is private)")
        elif loose:
            maildir.private(have[0])
            out.append(f"heard  box mode repaired: {have[0]} and its new/cur/tmp are 0700")
    elif len(stores) == 1:
        box = stores[0] / HEARD
        if dry_run:
            out.append(f"heard  would make {box}/{{new,cur,tmp}}, then route it: {gen}")
        else:
            maildir.private(box)                    # 0700, as every agent box is
            out.append(f"heard  box made: {box}")
            ran = _vmailbox_gen()
            if ran is None:
                out.append(f"WARNING: vmailbox-gen is not on PATH, so not run: run {gen} so postfix "
                           "routes heard@<host> to the new box (it writes the postfix map; no sudo)")
            elif ran[0] != 0:
                out.append(f"WARNING: vmailbox-gen FAILED (exit {ran[0]}): {ran[1]}. Run it again "
                           f"by hand ({gen}) so postfix routes heard@<host>")
            else:
                out.append("heard  routed: vmailbox-gen ran")
    else:
        why = (f"{len(stores)} store dirs carry .tm-store ({', '.join(d.name for d in stores)}): "
               "which one is not mine to choose" if stores else f"no store dir under {agents} carries .tm-store")
        out.append(f"WARNING: heard box MISSING, not made: {why}. Make it by hand under the "
                   f"store's dir, 0700, then route it: "
                   f"(umask 077 && mkdir -p {agents}/<store>/heard/{{new,cur,tmp}}) && {gen}")
    if flat.is_dir():
        out.append(f"WARNING: {flat} is a FLAT box: sessionmail will not subscribe to it (it reads it "
                   "as a seat's own). The heard box belongs under the store's dir")
    out.append("heard  an agent hears a device by subscribing to heard+<device>: "
               "`message subscription add heard+macbook` (the built-in mic; heard+airpods for "
               "AirPods; ears-mail.py device_slug names the rest)")
    return out


def mail_checks(dry_run: bool = False, runs: tuple[str, Path] | None = None) -> list[str]:
    """What ears-mail needs to page anyone, checked and said. It configures nothing but the
    heard box on a mail-drop host: not postfix, not sessionmail's `sendmail` setting.
    ears-mail and the mouth's notices send by sessionmail's `message`. What carries a mail is
    sessionmail's to resolve (0.2.0 and later: SESSIONMAIL_SENDMAIL, else the `sendmail` key
    in MAIL_SETTINGS, else its mail drop, the default; postfix is opt-in). It is asked, never
    re-derived (sendmail_in_effect), in launchd's environment: the services get none of this
    shell's. Postfix being installed does not choose it, and macOS ships postconf, so postfix
    is checked only when sessionmail says an MTA carries the mail.
    Mail drop: no postfix check. The drop never makes a box, so the heard box is made FLAT
    by `message setup --box heard` (_heard_drop): sessionmail's first-run setup, every row
    it applied printed. An MTA: postfix's maps and the heard box NESTED under the one store
    dir carrying .tm-store, the fleet's layout (_heard_mta). An answer that cannot be read
    is its own state, never a guessed MTA: a WARNING, no box, neither branch's checks, and
    the install goes on. Every line says sessionmail's own words for where the carrier
    came from (the default, the template, the settings file, the variable).
    No line cites the fire log. One that does must say that its exit 0 proves only that the
    heard box got the turn, nothing about an agent.
    RUNS: the hand-made mailer left running (label, plist), whose PATH `message` is checked
    on instead of ours (_message_lines), and in whose environment sessionmail is asked."""
    out = _message_lines(runs)
    shell = os.environ.get(CARRIER_VAR, "").strip()
    kind, said, got = sendmail_in_effect(runs)
    if shell:
        out.append(f"the launchd services will not see {CARRIER_VAR}={shell} from your shell: "
                   f"they go by the `sendmail` key in sessionmail's settings file "
                   f"({MAIL_SETTINGS}), as sessionmail says next. Not configured here")
    if kind == "drop":
        out.append(f"sendmail {said.split(';')[0]} for the launchd services (sessionmail's own "
                   "answer): no postfix needed, and none is checked. Postfix is opt-in: the services "
                   f"send by it only when the `sendmail` key in {MAIL_SETTINGS} names it")
        return out + _heard_drop(dry_run, got, runs)
    if kind == "refused":
        out.append(f"WARNING: sendmail REFUSED: sessionmail refuses its settings, so nothing "
                   f"sends until they are fixed: {said}. Not configured here")
        return out
    if kind == "mta":
        out.append(f"sendmail {said} for the launchd services (sessionmail's own answer)")
    elif kind == "mta-broken":
        out.append(f"WARNING: sendmail BROKEN for the launchd services: {said}")
    else:
        out.append(f"WARNING: sendmail UNKNOWN: what carries the launchd services' mail could "
                   f"not be read from `message setup --dry-run` (sessionmail 0.2.0 or later): "
                   f"{said}. No heard box is made and nothing is checked; install goes on")
        return out
    return out + _heard_mta(dry_run)


def wake_checks(agent: str | None, dry_run: bool = False) -> list[str]:
    """--agent NAME: the wake seat, `ears wake add NAME`. Without it, say
    who the wake file wakes, and loudly when that is nobody."""
    from . import wake
    if agent:
        if dry_run:
            return [f"wake   would add {agent} to {wake.target()} (`ears wake add {agent}`)"]
        try:
            p, key, names = wake.add(agent)
        except wake.WakeError as e:
            return [f"WARNING: wake seat NOT added: {e}"]
        return [f"wake   {key}: {names}  ({p})"]
    try:
        have = wake.seats()
    except Exception:
        have = {}
    if have:
        return [f"wake   no --agent; {wake.target()} already wakes {', '.join(have)}"]
    return [f"WARNING: no --agent NAME: NOBODY will be woken when you speak ({wake.target()} names "
            "no seat). Run `ears services install --agent NAME`, or `ears wake add NAME`"]


# What each answer to the refusal means, in plain words: the refusal, --help and docs/ears.md
# ("A phone-only Mac") say the same (PHONE_ONLY).
AGENT_MEANS = ("a Mac where an agent seat should be woken when you speak: NAME is that seat, and "
               "it gets the keeper's notices")
NO_AGENT_MEANS = ("a phone-only Mac (for example a Delta bot): nobody is woken, and the notices go "
                  "to the log")
PHONE_ONLY = ("A phone-only Mac (for example one that runs a Delta bot, with no agent seat to wake): "
              "install with --no-agent. Nobody is woken when you speak, and the keeper's notices go "
              "to the log. --agent NAME is for a Mac where an agent seat should be woken when you "
              "speak. The speech models are a one-time download of about "
              "{size}, fetched by install after the speech server loads (--dry-run shows the size "
              "first; --no-prefetch leaves the download to the first call); the speech server runs "
              "as a login service.")


def seat_hint() -> str | None:
    """The seat this shell appears to be: $TM_AGENT_ADDRESS's local part, WHOLE (a wake seat
    is the address's local part: dave, sessionmail+engineer). A hint for the refusal only:
    install never takes it as --agent."""
    addr = (os.environ.get("TM_AGENT_ADDRESS") or "").strip()
    local = addr.split("@", 1)[0].strip()
    return local or None


def agent_refusal(names, agent: str | None, no_agent: bool) -> str | None:
    """Why a bare install must not run, or None. Refused only when nobody would be
    woken: the mail service (the one that wakes anyone) is installed, neither --agent NAME nor
    --no-agent was given, and the wake file names no seat (the read wake_checks makes). A wake
    file that already names a seat is a re-run, the kit's upgrade path: it wakes that seat."""
    if "mail" not in names or agent or no_agent:
        return None
    from . import wake
    try:
        if wake.seats():
            return None
    except Exception:         # unreadable: it names nobody ears-mail could wake
        pass
    lines = [f"ears services install: REFUSED: no --agent NAME, and {wake.target()} names no seat: "
             "the mail service would install, and NOBODY would be woken when you speak. "
             "Choose the one that fits this Mac:",
             f"    ears services install --agent NAME   # {AGENT_MEANS}",
             f"    ears services install --no-agent     # {NO_AGENT_MEANS}",
             "either way, `ears wake add NAME` adds a seat to wake later"]
    seat = seat_hint()
    if seat:
        lines.append(f"you appear to be seat {seat}: ears services install --agent {seat}")
    lines.append("nothing was written")
    return "\n".join(lines)


def notify_to(agent: str | None, notify: str | None) -> str | None:
    """Who the keeper's notices go to: --notify, else --agent, else EARS_NOTIFY at install."""
    return notify or agent or os.environ.get("EARS_NOTIFY") or None


def _notices_line(to: str | None) -> str:
    """What install says about the keeper's notices. With no ``to``, EARS_NOTIFY stays out
    of the plist and the keeper takes the operator at its start, so name the
    one `session operator` gives now, or say they are log only."""
    if to:
        return f"notify keeper notices to {to} (EARS_NOTIFY)"
    from . import operator_config
    op = operator_config.address()
    if op:
        return (f"notify keeper notices to the operator ({op}, from `session operator`): "
                "no --agent or --notify, so the keeper reads it at its start")
    return ("WARNING: keeper notices go to the operator if `session operator` names one at the "
            "keeper's start; none is configured now, so they are LOG ONLY (no --agent or --notify, "
            "EARS_NOTIFY unset): nobody is told when the mic goes or a capture fails")


def _hand_made(uid: int, labels) -> tuple[str, Path] | None:
    """(label, its plist path) for the first of LABELS whose hand-made agent is on this host:
    its plist in AGENTS, or loaded (`launchctl print` answers). None: none of them is. LABELS
    are names install never writes (HAND), so the name alone says hand-made."""
    for label in labels:
        path = AGENTS / f"{label}.plist"
        if path.exists() or _launchctl("print", f"gui/{uid}/{label}").returncode == 0:
            return label, path
    return None


def _hand(uid: int, name: str) -> tuple[str, Path] | None:
    """(label, plist) of a hand-made NAME on this host, else None: under install's own label
    when its plist's content says install did not write it (_hand_at), else under a name the
    template carried before (HAND)."""
    return _hand_at(name, uid) or _hand_made(uid, HAND.get(name, ()))


def _hand_mailer(uid: int) -> tuple[str, Path] | None:
    """(label, plist) of the hand-made mailer when it is on this host: a render of
    launchd/dev.sessionkit.voice.ears-mail.plist.in (install's label, no MARKER), or one under
    its old name, dev.sessionvoice.ears-mail (plist or loaded). The older dev.voicemode.ears-mail
    is not one: it is a LEGACY render, and install replaces it (_replace_legacy)."""
    return _hand(uid, "mail")


def _move_old(uid: int, name: str, listed: dict[str, str], dry_run: bool = False) -> tuple[list[str], list[str]]:
    """Move NAME off every label it carried before (_old_labels): each one loaded (launchctl
    print) gets ONE bootout; then its plist goes, loaded or not. LISTED is this install's one
    `launchctl list`. (the labels to wait for: booted out here; the lines to print).

    An old label with no sign on this host (not in LISTED, no plist) gets no call at all, so a
    second install makes none. print is asked only when there is a sign of the old job. A dry
    run says what it would do and changes nothing."""
    gone, lines = [], []
    for old in _old_labels(name):
        path = AGENTS / f"{old}.plist"
        there = path.exists()
        if not (there or old in listed):
            continue
        loaded = _loaded(uid, old)
        if not (loaded or there):
            continue
        what = "loaded" if loaded else "plist only, not loaded"
        if dry_run:
            lines.append(f"{name:6s} old label {old} ({what}): would be booted out and its plist removed")
            continue
        if loaded:
            _launchctl("bootout", f"gui/{uid}/{old}")
            gone.append(old)
        path.unlink(missing_ok=True)
        lines.append(f"{name:6s} old label {old} ({what}): " + ("booted out, " if loaded else "") + "plist removed")
    return gone, lines


def _replace_legacy(uid: int, name: str, dry_run: bool = False) -> tuple[list[str], list[str]]:
    """Take NAME's LEGACY renders off this host (a stale PATH): each loaded one is booted out, and
    each plist is moved aside to LABEL.plist.replaced-STAMP, never deleted (launchd loads only
    *.plist, so the copy is inert and the host keeps it). (the labels booted out, which the
    caller waits for before ours loads; the lines to print). A dry run says it and changes
    nothing: launchctl print only."""
    import time
    gone, lines = [], []
    for label in LEGACY.get(name, ()):
        path = AGENTS / f"{label}.plist"
        there, loaded = path.exists(), _loaded(uid, label)
        if not (there or loaded):
            continue
        what = "loaded" if loaded else "plist only, not loaded"
        aside = path.with_name(f"{path.name}.replaced-{time.strftime('%Y%m%d-%H%M%S')}")
        n = 1
        while aside.exists() or aside.is_symlink():
            n += 1
            aside = aside.with_name(f"{path.name}.replaced-{time.strftime('%Y%m%d-%H%M%S')}-{n}")
        act = ", ".join((["booted out"] if loaded else []) + ([f"plist moved aside to {aside}"] if there else []))
        head = f"WARNING: {name} LEGACY {label} ({what}): a stale render, never left running"
        if dry_run:
            lines.append(f"{head}; would be REPLACED ({act})")
            continue
        if loaded:
            _launchctl("bootout", f"gui/{uid}/{label}")
            gone.append(label)
        if there:
            os.replace(path, aside)
        lines.append(f"{head}; REPLACED ({act})")
    return gone, lines


def _gone_or_stuck(uid: int, labels, wait_s: float | None = None) -> list[str]:
    """Wait up to BOOT_WAIT_S for LABELS to leave launchd; the ones still loaded then."""
    import time
    deadline = time.monotonic() + (BOOT_WAIT_S if wait_s is None else wait_s)
    waiting = list(labels)
    while waiting:
        waiting = [l for l in waiting if _loaded(uid, l)]
        if not waiting or time.monotonic() >= deadline:
            break
        time.sleep(0.2)
    return waiting


def _retire_ours_beside(uid: int, dry_run: bool = False, name: str = "mail", hand: str | None = None,
                        hand_loaded: bool = True) -> str:
    """Never two of NAME (two mailers, two sidecars, two keepers), from the other side: the
    hand-made one (label HAND) is here AND ours was installed before it, so skipping ours would
    still leave both running. Ours goes (boot out, plist removed), under any of its labels but
    HAND's own: a hand render at install's label is theirs, never touched."""
    found = [(l, AGENTS / f"{l}.plist", _loaded(uid, l)) for l in _labels(name) if l != hand]
    found = [f for f in found if f[2] or f[1].exists()]
    if not found:
        return f"{name:6s} ours not installed: nothing to remove"
    labels = ", ".join(f[0] for f in found)
    # a hand plist that is not loaded is DORMANT, not stale: launchd loads it at the next login
    runs = "the hand-made one runs" if hand_loaded else \
        ("the hand-made one is not loaded now, but launchd loads its plist at the next login; "
         f"until then NO {name} runs")
    if dry_run:
        return f"WARNING: {name} ours would be REMOVED ({labels}): {runs}"
    for label, ours, loaded in found:
        if loaded:
            _launchctl("bootout", f"gui/{uid}/{label}")
        ours.unlink(missing_ok=True)
    return f"WARNING: {name} ours REMOVED ({labels}, installed before): {runs}"


def _pgrep(pattern: str) -> list[int]:
    """The pids whose command line holds PATTERN (pgrep -f; read only, never a kill)."""
    try:
        r = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True, timeout=10)
    except Exception:
        return []
    return [int(x) for x in r.stdout.split() if x.isdigit()]


def _listed(out: list[str]) -> dict[str, str]:
    """`launchctl list` lines as {label: pid}, the pid "-" when loaded and not running."""
    return {l.split("\t")[2]: l.split("\t")[0] for l in out if l.count("\t") >= 2}


def _stray_sidecars() -> str | None:
    """A speaker-sidecar2.py running that is not our job (a hand `run.sh --detach`, which
    no label check sees, or an enrol's --record-only). A WARNING only, never a refusal, and
    never a kill: an enrol in progress would trip a refusal."""
    listed = _listed(_launchctl("list").stdout.splitlines())
    ours = {listed.get(l) for l in _labels("speakers")}
    stray = [p for p in _pgrep(SIDECAR) if str(p) not in ours]
    if not stray:
        return None
    return (f"WARNING: a {SIDECAR} not started by {PREFIX}speakers is running (pid "
            f"{', '.join(map(str, stray))}): if it labels the heard log too, every line is labelled "
            "twice. Stop it (touch its STOP) unless it is an enrol recording. Nothing was stopped")


def _speakers_row(listed: dict[str, str]) -> str:
    """The speakers status row: exactly one of not installed / installed, not enrolled (no
    refs/*.wav: wins whether or not the job is loaded) / running, pid N / loaded, not running."""
    pid = next((listed[l] for l in _labels("speakers") if l in listed), None)   # new label first
    if pid is None and not any((AGENTS / f"{l}.plist").exists() for l in _labels("speakers")):
        return "not installed"
    if not _speakers_enrolled():
        return (f"installed, not enrolled (no reference in {speakers_home() / 'refs'}: "
                "`ears speakers enrol`)")
    return "running, pid " + pid if pid and pid != "-" else "loaded, not running"


def _hand_line(listed: dict[str, str], label: str, what: str) -> str | None:
    """The line status prints beside a row for a hand-made LABEL on this host, else None."""
    hand = listed.get(label)
    if hand is None and not (AGENTS / f"{label}.plist").exists():
        return None
    return (f"{'':6s} {label} (hand-made): " + (f"running, pid {hand}" if hand and hand != "-" else
            "loaded, not running" if hand else f"plist present, NOT loaded ({DORMANT})") + \
        f"; install skips {what} while it is here ({_ours_now(listed, label, what)}). "
        f"{_switch(label, what, hand is not None)}")


def _ours_now(listed: dict[str, str], hand: str, name: str) -> str:
    """Beside a hand-made NAME (label HAND): is ours loaded now? A status line must not say
    ours is not loaded while it runs: the next install REMOVES it (never two of the job)."""
    ours = next((l for l in _labels(name) if l != hand and l in listed), None)
    if ours is None:
        return "ours is NOT loaded"
    return f"ours runs now as {ours}, and the next install REMOVES it"


#: why a hand plist that is not loaded still means ours is skipped
DORMANT = "dormant, not stale: launchd loads every plist in LaunchAgents at the next login, so ours would run beside it"


def _switch(label: str, name: str, loaded: bool = True) -> str:
    """How to run ours in place of the hand-made LABEL, by its state. Loaded: boot it out.
    Its plist in AGENTS: move it out too (a hand plist there is still hand-made after a
    bootout, and launchd loads it at the next login). Not loaded (DORMANT): moving the plist
    aside is all, there is nothing to boot out. Then install."""
    path = AGENTS / f"{label}.plist"
    steps = []
    if loaded:
        steps.append(f"launchctl bootout gui/{os.getuid()}/{label}")
    if path.exists():
        steps.append(f"move {path} out of {AGENTS}" + ("" if loaded else " (it is not loaded: nothing to boot out)"))
    return f"To run ours instead: {', '.join(steps)}, then `ears services install --only {_service(name)}`"


def _legacy_line(listed: dict[str, str], name: str, label: str) -> str | None:
    """The line status prints beside NAME's row for a LEGACY render on this host, else None."""
    pid = listed.get(label)
    if pid is None and not (AGENTS / f"{label}.plist").exists():
        return None
    state = (f"running, pid {pid}" if pid and pid != "-" else "loaded, not running" if pid
             else "plist present")
    return (f"{'':6s} WARNING: {label} (LEGACY render): {state}; a stale render, "
            f"`ears services install --only {_service(name)}` replaces it")


def _no_probe(names, speech_installs: bool = False) -> str | None:
    """Why the new final is not probed: it names the stt service, or the speech server, this
    very install loads (a probe now would find it still starting, and fall back to whisper)."""
    from urllib.parse import urlparse
    if speech_installs and speech.ears_here(_stt_url(), port=_speech_port()):
        return "it is the speech server this install loads"
    if "stt" not in names or not os.environ.get("EARS_STT_URL"):
        return None
    u = urlparse(_stt_url())
    if u.hostname in ("127.0.0.1", "localhost") and str(u.port) == os.environ.get("EARS_STT_PORT", "2023"):
        return "it is the stt service this install loads"
    return None


#: why a hand-made one means ours is skipped, per service
SKIP_WHY = {"mail": "ours would mail every turn twice",
            "speakers": "two sidecars would label every line twice",
            "ears-sounds": "every ears sound would play twice",
            "mouth-sounds": "every mouth sound would play twice",
            "talk": "two would fight over talk's port",
            "keeper": "two keepers would fight over the room's capture"}


def _skipped(name: str, hand: tuple[str, Path], loaded: bool = True) -> str:
    """The line install prints when a hand-made NAME (HAND: label, plist) means ours is skipped.
    LOADED False: its plist is on the host but not loaded, DORMANT, and the line says why that
    still counts."""
    label, path = hand
    how, where = "", f" ({path})"
    if not loaded:
        where = f" ({path}: NOT loaded, {DORMANT})"
    if label == _label(name):
        how = " (its plist was not written by `ears services install`)"
        if not path.exists():
            how = " (loaded from a plist elsewhere; its environment has no install marker)"
            where = ""
    return (f"WARNING: {name} SKIPPED: the hand-made {label}{how} is already on this host{where}; "
            f"{SKIP_WHY.get(name, 'two would run')}. It is left as it is. {_switch(label, name, loaded)}")


def _say_skipped(uid: int, dry_run: bool, name: str, hand: tuple[str, Path]) -> None:
    """Ours is skipped for the hand-made NAME (HAND): say so, and why, by its state (loaded,
    or a DORMANT plist launchd loads at the next login), then remove ours if it was there
    first: never two of the job."""
    loaded = _loaded(uid, hand[0])
    print(_skipped(name, hand, loaded))
    print(_retire_ours_beside(uid, dry_run, name, hand[0], hand_loaded=loaded))


REFUSED = "ears services install: REFUSED: "


@dataclass
class Preflight:
    """What install found missing before writing anything (preflight()).

    ``items``: each refused item as install prints it on stderr (one may be several lines), in
    the order agent, shipped files, speakers, speech. ``later``: the lines for steps that need
    every item cleared first (the Silero fetch), said only inside a refused block. ``drop``: the services a dry
    run stops listing, because their own item refused. ``decision``: the speech server's, so
    :8890 is probed once per install. ``agent``: the first item is the agent refusal."""
    items: list[str] = field(default_factory=list)
    later: list[str] = field(default_factory=list)
    drop: set[str] = field(default_factory=set)
    decision: speech.Decision | None = None
    agent: bool = False

    @property
    def refused(self) -> bool:
        return bool(self.items)


def _shipped_refusals(names) -> list[tuple[str, str]]:
    """(service, why) for EVERY shipped file NAMES' agents run that is missing, not only the
    first: the files a plist would point at (never a plist that cannot start)."""
    out = []
    for n, find in (("keeper", keeper_script), ("keeper", schema_files), ("mail", mail_script),
                    ("speakers", lambda: _speakers_file("run.sh")),
                    ("ears-sounds", lambda: sounds_script("ears-sounds")),
                    ("mouth-sounds", lambda: sounds_script("mouth-sounds")),
                    ("talk", talk_bin)):
        if n in names:
            try:
                find()
            except NoKeeper as e:
                out.append((n, str(e)))
    return out


def preflight(names, agent_why: str | None = None) -> Preflight:
    """Every check install can make before it writes anything, all of them, in one pass:
    read-only, offline (the speech probe asks only this host's :8890) and silent. Each failure
    is one item, with its fix, worded exactly as install's single refusal was. AGENT_WHY: the
    agent refusal main() found (agent_refusal), the first item; install() called directly
    never checks it. The same whether or not it is a dry run.

    A check that needs another to pass first is not asked when that one failed: speakers'
    host, python, lock and uv once its run.sh is found (and only when no hand-made speakers is
    on this host, as install skips them then); speech's once the probe chose step 4 and the
    ears hear through it. Builds, downloads and module imports are never asked here: they
    write or fetch, and stay late, single refusals. A missing Silero model is not an item:
    install fetches it once nothing refuses, so a refused block names it as not fetched (a
    refused run fetches nothing)."""
    pre = Preflight(agent=bool(agent_why))
    if agent_why:
        pre.items.append(agent_why)
    for n, why in _shipped_refusals(names):
        pre.items.append(REFUSED + why)
        pre.drop.add(n)
    if "keeper" in names:   # never a refusal now: install fetches it once nothing else refuses
        line = silero_not_fetched()
        if line:
            pre.later.append(line)
    if "speakers" in names and "speakers" not in pre.drop and not _hand(os.getuid(), "speakers"):
        why = speakers_refusal()
        if why:
            pre.items.append(REFUSED + why)
            pre.drop.add("speakers")
    if "speech" in names:
        url, port = speech.final_url(), _speech_port()
        d = pre.decision = _speech_decide(names, url, port)
        why = None
        if d.kind != "install":
            if d.fatal:
                head, said = _speech_said(d)
                why = head + said
        elif speech.ears_here(url, port=port):   # not here: a late WARNING, and the rest installs
            why = speech_refusal()
            why = why and f"speech: {why}"
        if why:
            pre.items.append(f"{REFUSED}{why}\n{no_stt_help()}")
            pre.drop.add("speech")
    return pre


def _refused_block(pre: Preflight) -> None:
    """PRE's refusals as ONE block on stderr: each item, then each later line, then (more than
    one) how many and that nothing was written. A block of one item is that single refusal,
    byte for byte."""
    items = list(pre.items)
    many = len(items) + len(pre.later) > 1
    if many and pre.agent:    # said once, by the footer
        items[0] = items[0].removesuffix("\nnothing was written")
    for line in items + pre.later:
        print(line, file=sys.stderr)
    if many:
        print(f"ears services install: {len(items)} refused above, from one run; nothing was "
              "written, fetched or built. Fix each, then run it again", file=sys.stderr)


def install_log_path() -> Path:
    """The install log, ``<sessionvoice's home>/logs/install.log``: every real run of
    ``ears services install`` appends what it printed and what launchctl answered to it,
    each line timestamped. Under ``logs/`` beside the heard log's ``conversations/``; the
    home's resolver keys on the heard log alone, so this file never decides the root."""
    from .home import sessionvoice_home
    return Path(sessionvoice_home()) / "logs" / "install.log"


def _log_now() -> str:
    from datetime import datetime
    return datetime.now().astimezone().isoformat(timespec="seconds")


class _InstallLog:
    """One install run's log. Each line is stamped when it is printed and held until arm(),
    the run's first write to the host; from then on it is written through, so a run that
    stops part-way still leaves what it printed. A run that never arms (a dry run, a refusal
    before anything is written) leaves no file, as it says. launchctl's answers are logged
    for every call that changes a job (bootstrap, bootout, ...); its read-only questions
    (print, list) are asked many times while a job goes and are not.

    The log never stops an install: a log that cannot be made or written (an OSError at any
    point) says so once on stderr, naming its path, and the run goes on unlogged. Its exit
    code is the services' alone."""

    QUIET = ("print", "list")

    def __init__(self, names):
        self.names = list(names)
        self.path: Path | None = None
        self.said = False             # its path printed
        self._start = _log_now()
        self._fh = None
        self._held: list[str] = []
        self._part = {"stdout": "", "stderr": ""}
        self.err = sys.stderr         # where the one warning goes: the stream before any tee
        self.lost = False             # it could not be written: the run goes on unlogged

    @property
    def armed(self) -> bool:
        return self._fh is not None

    def row(self) -> str:
        self.said = True
        return f"{'log':6s} {self.path}"

    def _lose(self, e: OSError) -> None:
        """Give the log up, once: one warning, then nothing more is held or written."""
        fh, self._fh, self._held = self._fh, None, []
        if fh:
            try:
                fh.close()
            except OSError:
                pass
        if not self.lost:
            self.lost = True
            where = self.path or "logs/install.log, under sessionvoice's home,"
            print(f"ears services install: WARNING: the install log {where} cannot be written "
                  f"({e.strerror or e}); installing on without it", file=self.err)

    def _line(self, kind: str, text: str) -> None:
        if self.lost:
            return
        line = f"{_log_now()} {kind} {text}\n"
        if self._fh:
            try:
                self._fh.write(line)
                self._fh.flush()
            except OSError as e:
                self._lose(e)
        else:
            self._held.append(line)

    _ESC = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")   # a terminal control sequence (erase-line)

    @classmethod
    def _plain(cls, text: str) -> str:
        """TEXT as a terminal leaves it: a carriage return redraws the line (the prefetch's
        progress does, on a tty), so only the last state is kept; control sequences go."""
        return next((s for s in reversed(cls._ESC.sub("", text).split("\r")) if s), "")

    def feed(self, kind: str, text: str) -> None:
        *lines, part = (self._part[kind] + text).split("\n")
        for line in lines:
            self._line(kind, self._plain(line))
        # a line still being redrawn holds only its last state, however long the redraw runs
        self._part[kind] = "\r" + self._plain(part) if "\r" in part else part

    def launchctl(self, inner):
        def launchctl(*a: str) -> subprocess.CompletedProcess:
            r = inner(*a)
            if a and a[0] not in self.QUIET:
                why = (r.stderr or "").strip().replace("\n", " | ")
                self._line("launchctl", f"{' '.join(a)} -> rc {r.returncode}" + (f": {why}" if why else ""))
            return r
        return launchctl

    def arm(self) -> None:
        if self._fh or self.lost:
            return
        try:
            self.path = install_log_path()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = self.path.open("a", encoding="utf-8")
            self._fh.write(f"=== {self._start} ears services install {' '.join(self.names)}\n")
            self._fh.writelines(self._held)
            self._held = []
            self._fh.flush()
        except OSError as e:
            self._lose(e)

    def close(self, end: str) -> None:
        for kind, part in self._part.items():   # a last line with no newline, as it ended
            if self._plain(part):
                self._line(kind, self._plain(part))
        if self._fh:
            try:
                self._fh.write(f"=== {_log_now()} {end}\n")
                self._fh.close()
                self._fh = None
            except OSError as e:
                self._lose(e)


class _Tee:
    """A stream that also feeds an install log: what the user sees, byte for byte, is what it
    was."""

    def __init__(self, inner, log: _InstallLog, kind: str):
        self._inner, self._log, self._kind = inner, log, kind

    def write(self, text: str) -> int:
        n = self._inner.write(text)
        self._log.feed(self._kind, text)
        return n

    def __getattr__(self, name):
        return getattr(self._inner, name)


_RUN_LOG: _InstallLog | None = None     # the running install's log, armed by install()


def _arm_install_log() -> None:
    if _RUN_LOG is not None:
        _RUN_LOG.arm()


def _logged(run):
    """install(), teed to the install log for its whole run: its stdout and stderr, and
    launchctl's answers through the module's one launchctl seam (so a faked launchctl is what
    a test's log shows). An armed run that returned before its own log row prints the row
    last; the log's last line is the run's exit code."""
    import functools

    @functools.wraps(run)
    def install(names, *a, **kw):
        global _RUN_LOG, _launchctl
        log = _InstallLog(jobs(names))
        saved = _RUN_LOG, _launchctl, sys.stdout, sys.stderr
        _RUN_LOG, _launchctl = log, log.launchctl(_launchctl)
        sys.stdout, sys.stderr = _Tee(sys.stdout, log, "stdout"), _Tee(sys.stderr, log, "stderr")
        end = "raised"
        try:
            rc = run(names, *a, **kw)
            if log.armed and not log.said:
                print(log.row())
            end = f"rc {rc}"
            return rc
        except BaseException as e:
            end = f"raised {type(e).__name__}: {e}"
            raise
        finally:
            _RUN_LOG, _launchctl, sys.stdout, sys.stderr = saved
            log.close(end)
    return install


def _failed_block(failed: list[tuple[str, str]], log: Path | None) -> None:
    """The services that did not load, as ONE block on stderr: each with launchctl's reason,
    then where to look next. Printed once every service has been tried."""
    for n, why in failed:
        print(f"ears services install: FAILED: {n} did not load: {why}", file=sys.stderr)
    where = f"read the install log ({log}), then " if log else ""
    print(f"ears services install: {len(failed)} service{'s' if len(failed) > 1 else ''} did not "
          f"load (above), from one run; every service was tried. Next: {where}run "
          "`ears services status`", file=sys.stderr)


@_logged
def install(names, dry_run=False, agent=None, notify=None, no_prefetch=False, agent_why=None) -> int:
    names = jobs(names)       # sounds: its two agents
    refused = False
    pre = preflight(names, agent_why)   # before anything is written: every refusal, in one run
    if pre.refused:
        _refused_block(pre)
        if not dry_run:
            return 2
        names = [n for n in names if n not in pre.drop]   # a dry run lists the rest
        refused = True        # ...and exits 2 as install would
    if "speakers" in names:   # its state as `status` gives it, before anything changes
        print(f"{'speakers':6s} {_speakers_row(_listed(_launchctl('list').stdout.splitlines()))}")
    if "keeper" in names:     # the model, before any plist: a keeper without it loops deaf
        ok, line = silero(dry_run)
        if not ok:            # the fetch failed (a dry run fetches nothing, so never here):
            print(f"{REFUSED}{line}", file=sys.stderr)   # a block of one; nothing written yet
            return 2
        else:
            print(line)
    if not dry_run:           # a dry run changes nothing, not even an empty log dir
        LOGS.mkdir(parents=True, exist_ok=True)
        _arm_install_log()    # the install log too: from here on the run changes the host
    uid = os.getuid()
    hands: dict[str, tuple[str, Path] | None] = {}   # service -> the hand-made one: ours is skipped
    hand = None
    stuck = False
    failed: list[tuple[str, str]] = []   # (service, why): did not load, after every one is tried
    if "mail" in names:
        hand = hands["mail"] = _hand_mailer(uid)
        if hand:
            _say_skipped(uid, dry_run, "mail", hand)
            # ours does not load, so a LEGACY mailer beside the hand one is replaced here: it
            # would be a second mailer, and a stale one
            gone, lines = _replace_legacy(uid, "mail", dry_run)
            for line in lines:
                print(line)
            for label in _gone_or_stuck(uid, gone):
                print(f"ears services install: mail FAILED: {label} is still loaded after "
                      f"{BOOT_WAIT_S:g}s beside {hand[0]}: two mailers. Boot it out "
                      f"(launchctl bootout gui/{uid}/{label})", file=sys.stderr)
                stuck = True
        for line in mail_checks(dry_run, runs=hand):
            print(line)
        for line in wake_checks(agent, dry_run):
            print(line)
    if "speakers" in names:
        hand_sp = hands["speakers"] = _hand(uid, "speakers")
        if hand_sp:
            _say_skipped(uid, dry_run, "speakers", hand_sp)
        else:
            stray = _stray_sidecars()
            if stray:
                print(stray)
            if not speakers_checks(dry_run):   # the host, the python, its modules, the model
                if not dry_run:
                    return 2                   # before any plist: never a job that cannot start
                refused = True                 # a dry run goes on listing, and exits 2 as install would
    speech_installs = False
    fell_back = []
    if "speech" in names:     # steps 1-4: only step 4 builds or writes anything
        ok, speech_installs = speech_step(names, dry_run, d=pre.decision)
        if not ok:
            if not dry_run:
                return 2                       # before any plist: the ears would be deaf
            refused = True
        if speech_installs:   # the size, before any plist and so before the download
            print(speech_heads_up(prefetch_off(no_prefetch)), flush=True)
    to = notify_to(agent, notify)
    if "keeper" in names:
        print(_notices_line(to))
        print(seed_stt(dry_run=dry_run, no_probe=_no_probe(names, speech_installs),
                       fell_back=fell_back))
        if not dry_run:
            print(install_schema())
    # the old labels' first sign (a dry run whose every service refused has none to look up)
    listed = _listed(_launchctl("list").stdout.splitlines()) if names else {}
    for n in names:           # the rest: a hand render at our label (or the keeper's old name)
        if n in hands or (n == "speech" and not speech_installs):
            continue
        hands[n] = _hand(uid, n)
        if hands[n]:
            _say_skipped(uid, dry_run, n, hands[n])
            if n == "keeper" and not dry_run:
                print(_retire_old(uid, listed))      # the old room ears, beside the hand keeper
    for n in names:
        if hands.get(n) or (n == "speech" and not speech_installs):
            continue
        path = AGENTS / f"{_label(n)}.plist"
        data = plistlib.dumps(spec(n, notify=to))
        if dry_run:
            for line in _move_old(uid, n, listed, dry_run=True)[1]:
                print(line)
            for line in _replace_legacy(uid, n, dry_run=True)[1]:
                print(line)
            print(f"--- {path}\n{data.decode()}"); continue
        AGENTS.mkdir(parents=True, exist_ok=True)
        if n == "keeper":
            print(_retire_old(uid, listed))
        if n == "mail":       # its --state lives here; ears-mail makes no directory
            (Path.home() / ".cache/ears").mkdir(parents=True, exist_ok=True)
        if n == "speakers":   # its WorkingDirectory: launchd starts no job whose cwd is missing
            home = speakers_home()
            home.mkdir(parents=True, exist_ok=True)
            home.chmod(0o700)                   # voices are private; mkdir's mode is umask'd
        if n == "speech":     # its WorkingDirectory (speakers' reason)
            speech_home().mkdir(parents=True, exist_ok=True)
        if n in SOUNDS_FILES:  # the settings folder (mouth-sounds writes its defaults there when
            # mouth-sounds.json is absent, and fails without the folder) and the STOP files' one
            _sounds_settings(n).parent.mkdir(parents=True, exist_ok=True)
        olds, lines = _move_old(uid, n, listed)  # off every old label of N, once
        for line in lines:
            print(line)
        legacy, lines = _replace_legacy(uid, n)  # off a stale hand render (a stale PATH)
        for line in lines:
            print(line)
        _launchctl("bootout", f"gui/{uid}/{_label(n)}")          # a re-install replaces, never doubles
        path.write_bytes(data)
        try:
            r = _bootstrap(uid, n, path, old=[*olds, *legacy])
        except OldStillLoaded as e:          # never two of one service: stop loud, load nothing
            print(f"ears services install: {n} FAILED: {e}", file=sys.stderr)
            print(f"{n:6s} FAILED: {e.label} would not go; {_label(n)} NOT loaded  {path}")
            stuck = True
            failed.append((n, f"{e.label} would not go; {_label(n)} NOT loaded"))
            continue
        print(f"{n:6s} {'loaded' if r.returncode == 0 else 'FAILED: ' + r.stderr.strip()}  {path}")
        if r.returncode != 0:                # the next service is still tried: every failure, one run
            failed.append((n, r.stderr.strip() or f"launchctl bootstrap exit {r.returncode}"))
    if speech_installs:       # its weights, so the first call does not wait on them: never fatal
        prefetch(dry_run, prefetch_off(no_prefetch))
    log = _RUN_LOG if _RUN_LOG is not None and _RUN_LOG.armed else None
    if failed:                # after every service was tried: never a proof of a job not loaded
        _failed_block(failed, log and log.path)
    if "mail" in names and not dry_run and not failed:
        print("proof  speak to the agent: it WAKES and answers. A heard line alone proves only the "
              "keeper; the mail is what pages it")
    if log:
        print(log.row())
    for fb in fell_back:      # the last line, so it is not missed among the rest: exit unchanged
        print(fallback_said(fb, to))
    return 2 if refused or stuck or failed else 0


def uninstall(names, dry_run: bool = False) -> int:
    """Boot out and delete the plist of every label NAMES' agents may be under. ``dry_run``
    says each label it would boot out and each plist it would delete, and changes
    nothing: no launchctl call at all, no file removed. A temp HOME would not contain a real
    run: the bootout acts on the user's own launchd domain."""
    uid = os.getuid()
    names = jobs(names)       # sounds: its two agents
    if "keeper" in names:
        names = list(names) + [o for o in OLD if o not in names]
    for n in names:
        hand = _hand_at(n, uid, look=not dry_run) if n not in OLD else None
        if hand:                              # a hand render at our label is theirs: left as it is
            print(f"{n:6s} {'would leave' if dry_run else 'left'} the hand-made {hand[0]} "
                  f"({hand[1]}: not written by `ears services install`)")
        for label in _labels(n):              # new and every old one
            if hand and label == hand[0]:
                continue
            plist = AGENTS / f"{label}.plist"
            if dry_run:
                print(f"{n:6s} would boot out gui/{uid}/{label}")
                if plist.exists():
                    print(f"{n:6s} would delete {plist}")
                continue
            _launchctl("bootout", f"gui/{uid}/{label}")
            plist.unlink(missing_ok=True)
        venv = f" ({_label(n)}; its venv stays in {speech_home()})" if n == "speech" else ""
        print(f"{n:6s} {'would be removed' if dry_run else 'removed'}{venv}")
    if dry_run:
        print("dry run: nothing booted out, nothing deleted")
    return 0


def _state(pid: str | None, label: str) -> str:
    """A status row's state for LABEL: running (its pid), loaded, or only its plist on disk."""
    if pid is None:
        return "not installed" if not (AGENTS / f"{label}.plist").exists() else "loaded, not running"
    return "running, pid " + pid if pid != "-" else "loaded, not running"


def _there(listed: dict[str, str], label: str) -> bool:
    """Is LABEL on this host: loaded (in LISTED) or its plist in AGENTS."""
    return label in listed or (AGENTS / f"{label}.plist").exists()


def _old_line(listed: dict[str, str], name: str, old: str, new: str) -> str:
    """The line status prints for NAME's install-made agent still under OLD, an old label."""
    state = "is still loaded" + (f" BESIDE {new}" if new in listed else "") if old in listed \
        else "plist present, not loaded"
    return f"{'':6s} old label: {old} {state}; `ears services install --only {_service(name)}` moves it"


def keeper_run() -> Path:
    """The keeper's RUN dir (ears-keeper.sh RUN): EARS_RUN, else ~/.cache/ears/run."""
    return Path(os.path.expanduser(os.environ.get("EARS_RUN") or "~/.cache/ears/run"))


def keeper_mode_line(run: Path | None = None) -> str | None:
    """The status line under the keeper's row: which way it reads the inputs, from the
    input-watch.mode file the keeper writes ("<keeper pid> <mode>"). input-watch, or
    polling system_profiler and why (no Command Line Tools: it never runs the swiftc stub).
    None when no live keeper wrote one: a mode left by a keeper that is gone is not told."""
    try:
        pid, mode = (Path(run or keeper_run()) / "input-watch.mode").read_text().strip().split(" ", 1)
        if int(pid) <= 0:        # 0 or less would ask a process group, not the keeper
            return None
        os.kill(int(pid), 0)
    except PermissionError:
        pass                     # alive, another user's: still the keeper that wrote it
    except (OSError, ValueError):
        return None
    mode = "".join(c for c in mode if c.isprintable())[:120]
    return f"{'':6s} mode: {mode}"


def _fallback_status_row() -> str | None:
    """`status`'s FALLBACK row: only while ears.json's final IS whisper :2022 and the record
    says it fell back (a final set by hand since makes the record stale, and says nothing)."""
    import json
    from .listen.stt import conf_path
    fb = stt_fallback()
    if fb is None:
        return None
    try:
        d = json.loads(Path(conf_path()).read_text())
    except Exception:
        return None
    if not isinstance(d, dict) or not _is_whisper(d.get("final") or d.get("stt")):
        return None
    return fallback_said(fb)


def status() -> int:
    listed = _listed(_launchctl("list").stdout.splitlines())
    old = [o for o in OLD if any(_there(listed, l) for l in _labels(o))]
    for n in jobs(ALL) + old:
        if n == "speech":     # its row is who serves :8890, below: never a ...ears-speech
            continue
        new = _labels(n)[0]   # call, room: their newest old label
        pid = listed.get(new)
        here = next((l for l in _labels(n) if _there(listed, l)), new)
        olds = [] if n in OLD else [o for o in _old_labels(n) if _there(listed, o)]
        if n == "speakers":
            state = _speakers_row(listed)
        elif n in OLD:
            state = _state(listed.get(here), here)
        elif new in listed or (AGENTS / f"{new}.plist").exists():
            state = _state(pid, new)
        elif any(o in listed for o in olds):   # not moved yet: the job runs under an old label
            o = next(o for o in olds if o in listed)
            state = _state(listed[o], o)
        else:
            state = "not installed"
        print(f"{n:6s} {state}" + (f"  (OLD {here}: the keeper runs it now; `ears services install` "
                                     "removes it)" if n in OLD else ""))
        for o in olds:
            print(_old_line(listed, n, o, new))
        if n == "keeper":
            line = keeper_mode_line()
            if line:
                print(line)
        if n not in OLD:
            hand = _hand_at(n)
            labels = ([hand[0]] if hand else []) + list(HAND.get(n, ()))
            for label in labels:
                line = _hand_line(listed, label, n)
                if line:
                    print(line)
        for label in LEGACY.get(n, ()):
            line = _legacy_line(listed, n, label)
            if line:
                print(line)
    # who serves :8890, asked read-only
    print(f"{'speech':6s} {speech.status_row(speech.probe(AGENTS, listed, port=_speech_port()))}")
    for o in _old_labels("speech"):
        if _there(listed, o):
            print(_old_line(listed, "speech", o, _label("speech")))
    # the ears on a whisper :2022 fallback: said until a later install moves them back
    line = _fallback_status_row()
    if line:
        print(line)
    # the weights that server fetches, against the expected shas: read-only, never fetched
    # and never a failure: drift is reported, status still exits 0
    for row in weights_status():
        print(row)
    # sessionvoice's home and why; LOUD when the heard log is split across two
    from .home import status_lines
    for row in status_lines():
        print(row)
    return 0


def main(argv=None) -> int:
    import argparse
    import textwrap
    p = argparse.ArgumentParser(prog="ears services", description=__doc__.split("\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("verb", choices=["install", "uninstall", "status", "weights"])
    p.add_argument("--only", default=None, help="comma list of: " + ",".join(ALL) + " (call, room: keeper;"
                   + " sounds: " + " and ".join(GROUPS["sounds"]) + ")"
                   + " (default: install " + ",".join(DEFAULT) + "; uninstall all)")
    p.add_argument("--apple", action="store_true",
                   help="install the Apple stt server (:2023) too; needs `ears install --apple`")
    who = p.add_mutually_exclusive_group()
    who.add_argument("--agent", default=None, help="for a Mac where an agent seat should be woken "
                     "when you speak: NAME is that seat (`ears wake add`), and gets the keeper's "
                     "notices (EARS_NOTIFY). Installing mail needs it or --no-agent, unless the wake "
                     "file already names a seat")
    who.add_argument("--no-agent", action="store_true", help="for a phone-only Mac (for example a "
                     "Delta bot): install mail with no wake seat; nobody is woken when you speak, and "
                     "the notices go to the log (install warns). `ears wake add NAME` adds a seat later")
    p.add_argument("--notify", default=None, help="where the keeper's notices go instead of --agent")
    p.add_argument("--json", action="store_true", help="weights: one line of JSON, schema "
                   + WEIGHTS_SCHEMA + " (docs/models.md)")
    p.add_argument("--dry-run", action="store_true", help="print the checks and the plists, change nothing "
                   "(install: also the size of the speech weights and of a missing Silero model it "
                   "would fetch, fetching nothing; "
                   "uninstall: the labels it would boot out and the plists it would delete)")
    p.add_argument("--no-prefetch", action="store_true", help="install: do not fetch the speech "
                   f"server's weights (about {_size(speech_weights_total())}) now; the first call fetches them, "
                   "and waits. "
                   f"{PREFETCH_ENV}=1 does the same")
    from .home import STRANDED_HELP, warn_stranded
    p.epilog = "\n\n".join(textwrap.fill(t, 78) for t in   # two paragraphs, each wrapped
                           (PHONE_ONLY.format(size=_size(speech_weights_total())), STRANDED_HELP))
    warn_stranded("ears services")   # once per process: status and install alike
    a = p.parse_args(argv)
    if a.only:
        names = [n for n in a.only.split(",") if n]
    else:                     # uninstall takes them all, so an stt agent from before goes too
        names = list(DEFAULT if a.verb == "install" else ALL)
    names = list(dict.fromkeys("keeper" if n in OLD else n for n in names))   # call, room -> keeper
    if a.apple and "stt" not in names:
        names.insert(0, "stt")
    bad = [n for n in names if n not in ALL]
    if bad:
        p.error(f"unknown service {bad}; choose from {ALL}")
    names = jobs(names)       # sounds: its two agents, each its own label
    if a.verb == "status":
        return status()
    if a.verb == "weights":
        return weights(a.json)
    if a.verb == "install":
        why = agent_refusal(names, a.agent, a.no_agent)
        if why and not a.dry_run:   # refused, before install(): with everything else missing
            _refused_block(preflight(names, why))   # in the same block; nothing written
            return 2
        kw = {"agent_why": why} if why else {}      # a dry run lists on, and exits 2
        return install(names, a.dry_run, agent=a.agent, notify=a.notify, no_prefetch=a.no_prefetch, **kw)
    return uninstall(names, a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
