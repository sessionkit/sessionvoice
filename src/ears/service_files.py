"""service_files - the kit's service files: one ``<name>.json`` per launch agent we install.

``sessionkit service list|status|restart`` finds a package's services by convention: one
JSON file per service in ``~/.sessionkit/<package>/services/``, which the package's own
install writes. For voice that folder is ``ears.home.services_dir()``
(``~/.sessionkit/voice/services``), beside ``settings/`` and never inside it. The kit writes
none of them; it reads them.

``ears services install`` writes a job's file with ``enabled: true`` once that job, from a
plist install wrote itself, has loaded. ``ears services uninstall`` rewrites it with
``enabled: false`` and keeps the file: the service is still known on this Mac.

Each file is built from the code's own facts (:func:`record`), never a second copy of them:
the label (``services._label``), the ``--only`` word that installs it (``services._service``),
and the ports the jobs serve. The kit's contract is its schema, ``service.schema.json``
(``docs/service.md`` in the kit): ``service`` (1), ``name`` (the label after
``dev.sessionkit.voice.``), ``label``, ``enabled`` and ``install`` are required; the rest are
optional, and the kit ignores keys it does not know.

A write is atomic (:func:`write`): a temp file ``.<name>.json.tmp`` in the same folder, a dot
name not ending ``.json`` so the kit never reads a half file, then ``os.replace``. Nothing is
written outside the services folder. A write that fails is one line on stderr naming the file
and the reason (:func:`save`). The verb still finishes its launchd work and rolls back no job,
then exits :data:`EXIT_UNWRITTEN` (3): the job stands, its service file is not written. A
refusal or a job that did not load (exit 2) outranks it.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .home import services_dir

#: the exit code of an ``ears services install`` or ``uninstall`` that did its launchd work but
#: could not write a service file: the job stands, the file is not written. It collides with
#: none the verb has: 0 done, 1 Python's own (an uncaught error), 2 usage, refused, no
#: launchd or a job that did not load (2 wins over 3), 130 an uncaught Ctrl-C
EXIT_UNWRITTEN = 3
#: the schema version every file carries, as the kit's ``service`` key
VERSION = 1
#: the label prefix the kit reads ``name`` after: dev.sessionkit.<package>.
LABEL_PREFIX = "dev.sessionkit.voice."
#: the jobs whose stop or restart can drop a live phone call (the kit refuses one mid-call
#: unless --force): the ears, the mailer, the player, and the speech servers they ride
CALL_HAZARD = ("stt", "speech", "mouth", "keeper", "mail")
#: one short, user-facing line per job, as ``sessionkit service list`` shows it
DESCRIPTIONS = {
    "stt": "Apple speech to text, as an OpenAI-compatible server",
    "speech": "the speech server: speech to text for the ears, the voice for the mouth",
    "mouth": "the mouth's player: speaks each queued line",
    "keeper": "the ears: the room's microphone and each live call",
    "mail": "mails each finished turn, which wakes the agent",
    "speakers": "names who said each heard line",
    "ears-sounds": "a sound as you are heard",
    "mouth-sounds": "a sound as a line lands in the mouth",
    "talk": "the live view in a browser",
}
#: the order every file's keys are written in (any other key follows, sorted)
KEYS = ("service", "name", "label", "enabled", "install", "uninstall", "description", "port",
        "bind", "health", "hazard", "order", "note")


def name_of(job: str) -> str:
    """The kit's short name for JOB: its label after ``dev.sessionkit.voice.`` (speech:
    ``mlx-audio``; the sounds jobs and talk: their own; the rest ``ears-<job>``)."""
    from . import services
    label = services._label(job)
    if not label.startswith(LABEL_PREFIX):        # never a file for a label outside the kit's
        raise ValueError(f"{job}: label {label} is not under {LABEL_PREFIX}")
    return label[len(LABEL_PREFIX):]


def path_for(job: str, home=None) -> Path:
    """Where JOB's file sits: ``<services_dir>/<name>.json``."""
    return services_dir(home) / f"{name_of(job)}.json"


def record(job: str, enabled: bool, note: str | None = None) -> dict:
    """The whole file for JOB (``services.jobs()`` vocabulary: stt speech mouth keeper mail
    speakers ears-sounds mouth-sounds talk), ENABLED as given, NOTE only when given."""
    from . import services
    word = services._service(job)               # the sounds' two jobs both say --only sounds
    rec: dict = {"service": VERSION, "name": name_of(job), "label": services._label(job),
                 "enabled": bool(enabled),
                 "install": f"ears services install --only {word}",
                 "uninstall": f"ears services uninstall --only {word}"}
    if job in DESCRIPTIONS:
        rec["description"] = DESCRIPTIONS[job]
    if job == "stt":
        rec["port"] = services._stt_port()
    elif job == "speech":
        port = services._speech_port()
        rec.update(port=port, bind="127.0.0.1", health={"http": f"http://127.0.0.1:{port}/v1/models"},
                   order=10)
    elif job == "talk":
        rec.update(port=services._talk_port(), bind="127.0.0.1")
    if job in CALL_HAZARD:
        rec["hazard"] = ["call"]
    if note is not None:
        rec["note"] = note
    return {k: rec[k] for k in (*[k for k in KEYS if k in rec], *sorted(set(rec) - set(KEYS)))}


def write(job: str, enabled: bool, note: str | None = None, home=None) -> Path:
    """Write JOB's file atomically and return its path. Makes the services folder (and only
    it); the temp file ``.<name>.json.tmp`` beside it is renamed over the file, and removed
    if the write fails. Raises OSError on a failure to write, and ValueError when the record
    cannot be built (a port variable that is not a number) before any file is touched:
    :func:`save` is the caller that warns."""
    path = path_for(job, home)
    text = json.dumps(record(job, enabled, note), indent=2) + "\n"
    folder = path.parent
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / f".{path.name}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return path


def save(job: str, enabled: bool, note: str | None = None, home=None,
         verb: str = "ears services") -> Path | None:
    """:func:`write`, reported: a failure is ONE line on stderr, ``VERB: service file <path> not
    written: <reason>; the job stands``, and None (the caller exits :data:`EXIT_UNWRITTEN`).
    Never raises an OSError or a ValueError, so it never stops the next job's install."""
    try:
        return write(job, enabled, note, home)
    except (OSError, ValueError) as e:
        try:
            where = path_for(job, home)
        except ValueError:
            where = services_dir(home)
        why = (e.strerror if isinstance(e, OSError) and e.strerror else str(e)) or type(e).__name__
        why = " ".join(why.split())                # one line, whatever the reason carried
        print(f"{verb}: service file {where} not written: {why}; the job stands", file=sys.stderr)
        return None
