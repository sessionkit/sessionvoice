"""ears services - the launch agents a bare install needs, written and loaded by one verb.

    ears services install [--only stt,speech,mouth,keeper,mail,speakers] [--apple] [--agent NAME] [--notify ADDR] [--dry-run]
    ears services status
    ears services uninstall [--only ...]

Six services, each a macOS launch agent labelled dev.sessionkit.voice.ears-<name>, but speech,
which is dev.sessionkit.voice.mlx-audio:

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
         heard+<device>@<host>, which is what PAGES an agent. The keeper alone
         writes the heard line and wakes nobody, so "a heard line appears" is no proof of a
         working install: "you speak, and the agent WAKES and answers" is. A hand-made
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

Before anything is written, install checks what the services need and says so (--dry-run
lists the same checks, changing nothing):
- the Silero VAD model the keeper's capture runs on: present, linked from VoiceMode's copy,
  or MISSING, and then install REFUSES (run `ears install` first);
- for mail: sessionmail's `message` and its carrier, postfix or the mail drop (checked,
  never configured), the nested `heard` box under the store dir
  that carries .tm-store (made when there is exactly one, then vmailbox-gen routes it), and
  the heard+<device> box an agent subscribes to;
- for speakers: an arm64 Mac on macOS 14+ (else REFUSED), its own venv under the speakers
  home (built with uv from the shipped requirements.lock, mlx-audio==0.5.8; EARS_SPEAKER_PY
  skips the build), the modules it imports (the first missing is named, and install
  REFUSES), and the diarizer model (fetched here, the one download; the job never fetches);
- a new ``final`` engine answers one short clip (else whisper :2022, said so);
- --agent NAME: the wake seat (`ears wake add NAME`) and the keeper's notices (EARS_NOTIFY)
  go to the installing agent; --notify ADDR sends the notices elsewhere. Without --agent
  nobody is woken and the notices are log-only: install says so.

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

Hand renders of the jobs install does not run (ears-sounds, mouth-sounds, talk) under their old
names, dev.sessionvoice.* and dev.voicemode.* (HAND_RENAMED), are never touched: nothing would
replace the job. `status` names each one and the template to re-render it from.

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
import shlex
import subprocess
import sys
from pathlib import Path

from . import envnames, speech        # the speech server on :8890: found, or ours

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
ALL = ("stt", "speech", "mouth", "keeper", "mail", "speakers")
# what a bare install loads; stt only with --apple, speakers only by --only. speech first: the
# server the mouth and the ears ride is loading before they start
DEFAULT = ("speech", "mouth", "keeper", "mail")
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
        "keeper": ("com.failmode.ears-keeper",)}
HAND_MAILS = HAND["mail"]
HAND_MAIL = HAND_MAILS[0]           # the hand-rendered mailer under its name before the label rename
HAND_SPEAKERS = HAND["speakers"]
# LEGACY renders, stale by label (their PATH predates the one `message` needs): never left
# running, never run beside ours. Install replaces them (_replace_legacy). mail: the template's
# label before the dev.sessionvoice rename; mouth: hand players from before `ears services` had
# one (no template here ever made them)
LEGACY = {"mail": ("dev.voicemode.ears-mail",),
          "mouth": ("dev.voicemode.mouth-mail", "com.failmode.mouth-mail")}
# hand renders of the jobs install does NOT run (the sounds, talk), under their old names, and
# the template each is rendered from now. Install never touches them (nothing would replace the
# job); `status` names each one it finds, and the CHANGELOG says to boot it out and re-render
HAND_RENAMED = {
    "dev.sessionvoice.ears-sounds": "dev.sessionkit.voice.ears-sounds",
    "dev.sessionvoice.mouth-sounds": "dev.sessionkit.voice.mouth-sounds",
    "dev.sessionvoice.talk": "dev.sessionkit.voice.talk",
    "dev.voicemode.ears-sounds": "dev.sessionkit.voice.ears-sounds",
    "dev.voicemode.mouth-sounds": "dev.sessionkit.voice.mouth-sounds",
    "dev.voicemode.talk": "dev.sessionkit.voice.talk",
}
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


def hf_cache() -> Path:
    """The Hugging Face hub cache, by huggingface_hub's own rule (its constants.py), with no
    import: HF_HUB_CACHE, else HUGGINGFACE_HUB_CACHE, else <HF_HOME>/hub, where HF_HOME is
    $XDG_CACHE_HOME/huggingface when unset, and XDG_CACHE_HOME is ~/.cache. An empty variable
    counts as unset."""
    env = os.environ.get
    hub = env("HF_HUB_CACHE") or env("HUGGINGFACE_HUB_CACHE")
    if not hub:
        home = env("HF_HOME") or os.path.join(env("XDG_CACHE_HOME") or os.path.join("~", ".cache"),
                                              "huggingface")
        hub = os.path.join(home, "hub")
    return Path(os.path.expandvars(os.path.expanduser(hub)))


def weight_state(repo: str, expected: str, cache: Path) -> tuple[str, str | None]:
    """(state, the cached sha) of REPO in CACHE: what refs/main points at, against EXPECTED.
    State is "match", "differs" or "not cached". Read from the filesystem only: no
    huggingface_hub, no network, so it cannot fetch or hang on a missing cache. A refs/main
    naming EXPECTED whose snapshot directory is absent is "not cached": nothing is there to load."""
    folder = cache / ("models--" + repo.replace("/", "--"))
    try:
        ref = (folder / "refs" / "main").read_text(errors="replace").strip()
    except OSError:                  # no cache, no repo, no refs/main, or not readable
        return "not cached", None
    if not ref:
        return "not cached", None
    if ref != expected:
        return "differs", ref
    return ("match", ref) if (folder / "snapshots" / ref).is_dir() else ("not cached", ref)


def weights_status(cache: Path | None = None) -> list[str]:
    """The ``weight`` rows of ``ears services status``: one header naming the cache and the limit,
    then one row for each of SPEECH_WEIGHTS (weight_state)."""
    cache = hf_cache() if cache is None else cache
    rows = [f"{'weight':6s} the speech server's weights in the HF cache {cache}, read-only. A drift "
            f"report, not a pin: the server fetches `main` at first use"]
    for repo, expected in SPEECH_WEIGHTS:
        state, got = weight_state(repo, expected, cache)
        if state == "match":
            said = f"match ({expected[:12]})"
        elif state == "differs":
            said = f"DIFFERS: main is {got[:40]!r} in the cache, expected {expected[:12]}"
        else:
            said = f"not cached (expected {expected[:12]}; fetched from main at first use)"
        rows.append(f"{'weight':6s} {repo}: {said}")
    return rows


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

    bad = _speakers_platform()
    if bad:
        return refuse(bad)
    py = speaker_py()
    if os.environ.get("EARS_SPEAKER_PY"):
        if not py.exists():
            return refuse(f"EARS_SPEAKER_PY={py}: no such python")
        print(f"{'speakers':6s} python {py} (EARS_SPEAKER_PY: no venv built)")
    else:
        venv = py.parent.parent
        try:
            lock = _speakers_file("requirements.lock")
        except NoSpeakers as e:
            return refuse(str(e))
        pins = sum(1 for l in lock.read_text().splitlines() if l[:1].isalnum())
        uv = _uv()
        if not uv and not py.exists():
            return refuse(f"no uv on PATH: speakers builds its own venv ({venv}) with uv. Install uv "
                          "(https://docs.astral.sh/uv/), or set EARS_SPEAKER_PY to a python that has "
                          "mlx-audio>=0.5.6")
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


def _label(name: str) -> str:
    """A service's launchd label: dev.sessionkit.voice.ears-NAME, but the speech server's is its
    own, speech.LABEL (dev.sessionkit.voice.mlx-audio). Never a ...ears-speech."""
    return speech.LABEL if name == "speech" else PREFIX + name


def _old_labels(name: str) -> list[str]:
    """Every label NAME's install-made agent carried before, newest first: one per OLD_PREFIXES
    (com.sessionvoice.ears-NAME, com.voicemode.ears-NAME), and speech's speech.OLD_LABELS."""
    if name == "speech":
        return list(speech.OLD_LABELS)
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


def speech_missing_module(py) -> str | None:
    """The first of SPEECH_MODULES that PY cannot import, offline, or None."""
    return _missing_module(py, SPEECH_MODULES)


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
       No uv to build with: refused;
    3. the modules, each imported on its own, offline: the first missing is named."""
    bad = _speech_platform()
    if bad:
        return bad
    py = speech_py()
    if os.environ.get("EARS_SPEECH_PY"):
        if not py.exists():
            return f"EARS_SPEECH_PY={py}: no such python"
        print(f"{'speech':6s} python {py} (EARS_SPEECH_PY: no venv built)")
    else:
        venv = py.parent.parent
        try:
            lock = _speech_file("requirements.lock")
            build = _speech_file("build-constraints.txt")     # the sdists' backend, hashed
        except NoSpeech as e:
            return str(e)
        pins = sum(1 for l in lock.read_text().splitlines() if l[:1].isalnum())
        uv = _uv()
        if not uv and not py.exists():
            return (f"no uv on PATH: the speech server gets its own venv ({venv}), built with uv. "
                    "Install uv (https://docs.astral.sh/uv/), or set EARS_SPEECH_PY to a python that "
                    f"has {SPEECH_PIN} with its server and stt extras")
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
                        f"{_last_line(r)}")
            print(f"{'speech':6s} venv {py}: {pins} packages pinned ({SPEECH_PIN})")
        else:
            print(f"WARNING: no uv on PATH: the venv at {venv} is used as it is, its pins not re-applied")
    missing = speech_missing_module(py)
    if missing:
        return (f"{py} has no {missing}: the speech server needs {SPEECH_PIN} with its server and "
                "stt extras, misaki[en] and en_core_web_sm (speech/requirements.lock)")
    return None


def speech_step(names, dry_run: bool = False) -> tuple[bool, bool]:
    """speech.py's steps 1-4 for `install`, before anything is written: (ok, installs).
    It prints which step it took and why. ``installs`` is True only on step 4 when the checks
    pass (the plist loop writes and loads ours; a dry run prints it). ``ok`` is False when the
    ears would be deaf (they hear through :8890 and nothing there will serve them): install
    exits 2 before any plist, and says what to run. When only the mouth wants :8890, a refusal
    is a WARNING: the mouth falls back to `say`, and the rest installs."""
    url, port = speech.final_url(), _speech_port()
    here = speech.ears_here(url, port=port)
    d = speech.decide(speech.probe(AGENTS, _listed(_launchctl("list").stdout.splitlines()), port=port),
                      url, mouth="mouth" in names)
    said = d.line.removeprefix("speech: ").removeprefix("REFUSED: ")
    head = f"{'speech':6s} step {speech.STEP[d.kind]}, {d.kind}: "
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


def seed_stt(dry_run: bool = False, no_probe: str | None = None) -> str:
    """Write ``final`` into ears.json: from EARS_STT_URL/EARS_STT_MODEL when given (they
    replace it), else the default (Parakeet on mlx-audio) only when the file has none. An
    old ``stt`` counts as one (it is ``final``'s old name); a written ``final`` drops it, so
    the file never names two engines. ``partials`` is never written here. Every other key is
    kept; the write is atomic (the keeper reads the file every loop). Returns what it did.

    A NEW final is probed first (probe_stt): one that does not answer is not written, and
    whisper :2022 is, said loudly with the failure (a new host's ears.json once named a
    Parakeet the installed mlx-audio could not load, and the capture went deaf). A kept final is not
    re-probed. ``no_probe`` (a reason) skips the probe: the server is one this install has
    not loaded yet. ``dry_run``: says what it would write, writes nothing (it still probes).
    A STRANDED ears.json (``ears.home.stranded``) is not written, nor probed for."""
    import json
    from .home import refuse_stranded
    from .listen.stt import conf_path
    path = Path(conf_path())
    why = refuse_stranded(path, "ears.json", "ears services")   # a 0.1.0 file not moved yet
    if why:
        return f"final NOT set: {why}"
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

    given = bool(os.environ.get("EARS_STT_URL") or os.environ.get("EARS_STT_MODEL"))
    have = d.get("final") or d.get("stt")
    if have and not given:
        if "stt" not in d:
            return f"final kept: {have}"
        # the old name goes: the schema no longer draws it, and refuses a file that has it.
        # Beside final it is dead (final wins in every reader); alone it becomes final.
        old = d.pop("stt")
        how = "dropped stt, final wins" if "final" in d else "renamed from stt"
        d.setdefault("final", old)
        write()
        return f"final kept: {d['final']} ({would}{how})"
    stt = {"url": _stt_url()}
    if _stt_model():
        stt["model"] = _stt_model()

    def probe(url, model=None):
        # said BEFORE every request, the dry run's too: a check may ask the user's own STT, but
        # never silently
        print(f"probing {url} (read-only)", flush=True)
        return probe_stt(url, model)

    said = ""
    if no_probe:
        said = f" (not probed: {no_probe})"
    else:
        why = probe(stt["url"], stt.get("model"))
        if why is None:
            said = " (probe: it answers)"
        elif stt["url"].rstrip("/") == WHISPER_URL:
            said = (f" (FAILED its probe: {why}; start whisper :2022, or set final in {path})\n"
                    + no_stt_help())
        else:
            failed, stt = stt, {"url": WHISPER_URL}
            why2 = probe(WHISPER_URL)
            said = (f" - FALLBACK: {failed} FAILED its probe ({why}), so whisper :2022 instead"
                    + (f", which did not answer either ({why2}): start whisper :2022, or set final "
                       f"in {path} to an engine that answers\n" + no_stt_help()
                       if why2 else " (probe: it answers)"))
    d.pop("stt", None)
    d["final"] = stt
    write()
    return f"final {'would be set' if dry_run else 'set'}: {stt}{said}"


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
        # mlx-audio's server on its own venv's python, on 127.0.0.1 only; its logs
        # beside the agents' (its --log-dir default is a relative "logs")
        "speech": lambda: [str(speech_py()), "-m", "mlx_audio.server", "--host", speech.HOST,
                           "--port", str(_speech_port()), "--log-dir", str(LOGS / "mlx-audio")],
    }[name]()
    d = {"Label": _label(name), "ProgramArguments": args, "EnvironmentVariables": env,
         "WorkingDirectory": str(Path.home()), "RunAtLoad": True, "KeepAlive": True,
         "ThrottleInterval": 10,
         "StandardOutPath": str(LOGS / f"ears-{name}.log"),
         "StandardErrorPath": str(LOGS / f"ears-{name}.log")}
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
    if name == "speech":
        env.pop("EARS_PY")    # it runs on speech_py(), never the ears' python
        env["PATH"] = env["PATH"].replace(bindir, str(speech_py().parent), 1)
        d["WorkingDirectory"] = str(speech_home())
    return d




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
                             f"(launchctl bootout gui/{uid}/{o}), then `ears services install --only {name}`", o)
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


def silero(dry_run: bool = False) -> tuple[bool, str]:
    """(ok, line): the Silero VAD model the keeper's capture needs. Present: ok. Missing, and
    VoiceMode's copy is there: link it (dry run: would link). Neither: not ok, and install
    REFUSES before any plist is written (without it the capture dies every 4 s)."""
    want, copy = _silero_want(), _silero_copy()
    if want.is_file() and want.stat().st_size > 0:
        return True, f"silero present: {want}"
    if copy.is_file() and copy.stat().st_size > 0:
        if dry_run:
            return True, f"silero would link {want} -> {copy} (VoiceMode's copy)"
        want.parent.mkdir(parents=True, exist_ok=True)
        if want.is_symlink() or want.exists():
            want.unlink()                  # a dangling link, or an empty file: never the model
        want.symlink_to(copy)
        return True, f"silero linked {want} -> {copy} (VoiceMode's copy)"
    return False, (f"silero MISSING: no Silero VAD model at {want} (nor VoiceMode's {copy}); the "
                   "keeper's capture cannot start without it. Run `ears install` (it fetches it), "
                   "then `ears services install` again")


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


def mail_checks(dry_run: bool = False, runs: tuple[str, Path] | None = None) -> list[str]:
    """What ears-mail needs to page anyone, checked and said. Never configures the host's
    mail: not postfix, not sessionmail's carrier.
    ears-mail and the mouth's notices send by sessionmail's `message`, which carries a mail
    through postfix, or through the mail drop on a Mac with no postfix
    (SESSIONMAIL_SENDMAIL=drop). The heard box must sit NESTED under a store dir (the one
    carrying .tm-store): sessionmail reads a flat ~/.mail/agents/heard as a seat's own box
    and refuses to subscribe to it. Made only when exactly one store dir is found, then
    routed with vmailbox-gen when it is on PATH; otherwise the exact commands are printed.
    RUNS: the hand-made mailer left running (label, plist), whose PATH `message` is checked
    on instead of ours (_message_lines)."""
    out = _message_lines(runs)
    carrier = os.environ.get(CARRIER_VAR, "").strip()
    version = _postconf("mail_version")          # postfix's own tool: None or "" is no postfix
    if carrier == "drop":
        out.append(f"carrier mail drop ({CARRIER_VAR}=drop, read by sessionmail): no postfix "
                   "needed. Under it a heard turn's From is not qualified yet, so the pager "
                   "reads it as plain mail until sessionmail qualifies From")
    elif carrier:
        out.append(f"carrier {CARRIER_VAR}={carrier} (sessionmail's to read; not checked here)")
    if carrier:
        # sessionmail reads the carrier from the environment alone, and launchd
        # gives the services none of this shell's. Said, never copied into a plist.
        out.append(("WARNING: " if not version else "")
                   + f"the launchd services will not see {CARRIER_VAR} from your shell; "
                   + ("with no postfix they deliver nothing until sessionmail reads the carrier "
                      "from its settings (asked of sessionmail). Not configured here"
                      if not version else "they send by postfix"))
    if not version:
        if not carrier:
            out.append(f"WARNING: no postfix: message send needs {CARRIER_VAR}=drop (read by "
                       "sessionmail), and the launchd services do not see it from your shell: with "
                       "no postfix they deliver nothing until sessionmail reads the carrier from "
                       "its settings (asked of sessionmail). Not configured here")
    else:
        maps = _postconf("virtual_mailbox_maps")
        if not maps:
            out.append("WARNING: postfix has no virtual_mailbox_maps: heard+<device>@<host> "
                       "reaches no maildir by postfix, so no agent is paged. Set up this host's "
                       "agent mail (sessionmail) first")
        else:
            out.append(f"postfix present (virtual_mailbox_maps: {maps}); not changed")
    agents = _mail_agents()
    stores = sorted(d for d in agents.glob("*") if (d / ".tm-store").is_file()) if agents.is_dir() else []
    have = [d / "heard" for d in stores if (d / "heard" / "new").is_dir()]
    flat = agents / "heard"
    gen = _vmailbox_gen_cmd()
    if have:
        out.append(f"heard  box present: {have[0]}")
    elif len(stores) == 1:
        box = stores[0] / "heard"
        if dry_run:
            out.append(f"heard  would make {box}/{{new,cur,tmp}}, then route it: {gen}")
        else:
            for sub in ("new", "cur", "tmp"):
                (box / sub).mkdir(parents=True, exist_ok=True)
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
                   f"store's dir, then route it: mkdir -p {agents}/<store>/heard/{{new,cur,tmp}} && {gen}")
    if flat.is_dir():
        out.append(f"WARNING: {flat} is a FLAT box: sessionmail will not subscribe to it (it reads it "
                   "as a seat's own). The heard box belongs under the store's dir")
    out.append("heard  an agent hears a device by subscribing to heard+<device>: "
               "`message subscription add heard+macbook` (the built-in mic; heard+airpods for "
               "AirPods; ears-mail.py device_slug names the rest)")
    return out


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
    return f"To run ours instead: {', '.join(steps)}, then `ears services install --only {name}`"


def _legacy_line(listed: dict[str, str], name: str, label: str) -> str | None:
    """The line status prints beside NAME's row for a LEGACY render on this host, else None."""
    pid = listed.get(label)
    if pid is None and not (AGENTS / f"{label}.plist").exists():
        return None
    state = (f"running, pid {pid}" if pid and pid != "-" else "loaded, not running" if pid
             else "plist present")
    return (f"{'':6s} WARNING: {label} (LEGACY render): {state}; a stale render, "
            f"`ears services install --only {name}` replaces it")


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


def install(names, dry_run=False, agent=None, notify=None) -> int:
    for n, find in (("keeper", keeper_script), ("keeper", schema_files), ("mail", mail_script),
                    ("speakers", lambda: _speakers_file("run.sh"))):
        if n in names:        # before anything is written: never a plist that cannot start
            try:
                find()
            except NoKeeper as e:
                print(f"ears services install: REFUSED: {e}", file=sys.stderr)
                return 2
    if "speakers" in names:   # its state as `status` gives it, before anything changes
        print(f"{'speakers':6s} {_speakers_row(_listed(_launchctl('list').stdout.splitlines()))}")
    refused = False
    if "keeper" in names:     # the model, before any plist: a keeper without it loops deaf
        ok, line = silero(dry_run)
        if not ok:
            print(f"ears services install: REFUSED: {line}", file=sys.stderr)
            if not dry_run:
                return 2
            refused = True    # a dry run goes on listing, and exits 2 as install would
        else:
            print(line)
    if not dry_run:           # a dry run changes nothing, not even an empty log dir
        LOGS.mkdir(parents=True, exist_ok=True)
    uid = os.getuid()
    hands: dict[str, tuple[str, Path] | None] = {}   # service -> the hand-made one: ours is skipped
    hand = None
    stuck = False
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
    if "speech" in names:     # steps 1-4: only step 4 builds or writes anything
        ok, speech_installs = speech_step(names, dry_run)
        if not ok:
            if not dry_run:
                return 2                       # before any plist: the ears would be deaf
            refused = True
    to = notify_to(agent, notify)
    if "keeper" in names:
        print(_notices_line(to))
        print(seed_stt(dry_run=dry_run, no_probe=_no_probe(names, speech_installs)))
        if not dry_run:
            print(install_schema())
    listed = _listed(_launchctl("list").stdout.splitlines())   # the old labels' first sign
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
            continue
        print(f"{n:6s} {'loaded' if r.returncode == 0 else 'FAILED: ' + r.stderr.strip()}  {path}")
    if "mail" in names and not dry_run:
        print("proof  speak to the agent: it WAKES and answers. A heard line alone proves only the "
              "keeper; the mail is what pages it")
    return 2 if refused or stuck else 0


def uninstall(names, dry_run: bool = False) -> int:
    """Boot out and delete the plist of every label NAMES' agents may be under. ``dry_run``
    says each label it would boot out and each plist it would delete, and changes
    nothing: no launchctl call at all, no file removed. A temp HOME would not contain a real
    run: the bootout acts on the user's own launchd domain."""
    uid = os.getuid()
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
    return f"{'':6s} old label: {old} {state}; `ears services install --only {name}` moves it"


def status() -> int:
    listed = _listed(_launchctl("list").stdout.splitlines())
    old = [o for o in OLD if any(_there(listed, l) for l in _labels(o))]
    for n in ALL + tuple(old):
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
    # hand renders of the jobs install does not run, under an old name: named, never touched
    for label, now in HAND_RENAMED.items():
        if _there(listed, label):
            pid = listed.get(label)
            state = (f"running, pid {pid}" if pid and pid != "-" else "loaded, not running" if pid
                     else "plist present")
            print(f"{'hand':6s} {label} (old name): {state}; boot it out and render "
                  f"launchd/{now}.plist.in in its place")
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
    p = argparse.ArgumentParser(prog="ears services", description=__doc__.split("\n")[0])
    p.add_argument("verb", choices=["install", "uninstall", "status"])
    p.add_argument("--only", default=None, help="comma list of: " + ",".join(ALL) + " (call, room: keeper)"
                   + " (default: install " + ",".join(DEFAULT) + "; uninstall all)")
    p.add_argument("--apple", action="store_true",
                   help="install the Apple stt server (:2023) too; needs `ears install --apple`")
    p.add_argument("--agent", default=None, help="the installing agent: its wake seat (`ears wake add`) "
                   "and the keeper's notices (EARS_NOTIFY). Without it nobody is woken")
    p.add_argument("--notify", default=None, help="where the keeper's notices go instead of --agent")
    p.add_argument("--dry-run", action="store_true", help="print the checks and the plists, change nothing "
                   "(uninstall: the labels it would boot out and the plists it would delete)")
    from .home import STRANDED_HELP, warn_stranded
    p.epilog = STRANDED_HELP
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
    if a.verb == "status":
        return status()
    if a.verb == "install":
        return install(names, a.dry_run, agent=a.agent, notify=a.notify)
    return uninstall(names, a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
