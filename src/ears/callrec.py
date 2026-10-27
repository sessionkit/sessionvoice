"""callrec - the name of a Delta call's recording in its call dir, the one place it is spelt.

The bot writes the audio the ears heard to STEM.wav, and STEM.json beside it (start_epoch is
audio second 0 on the wall clock). STEM is caller-call, named for the role. It is the only
name read: a call dir from before sessionphone's stem rename is not read.

Stdlib only, and it imports nothing from ears: `ears eval` imports it as ears.callrec, and the
speaker side-car (its own venv, no ears) and bench/clock_check.py (the bot's venv) load this
file by its path. Keep it that way.
"""
from pathlib import Path

STEM = "caller-call"


def name(ext):
    """The file name a reader looks for, and a message saying it was not there names:
    "caller-call.EXT"."""
    return f"{STEM}.{ext}"


def call_file(call_dir, ext):
    """CALL_DIR's recording file with extension EXT ("wav" or "json"): caller-call.EXT if it
    is there, else None."""
    path = Path(call_dir) / name(ext)
    return path if path.exists() else None
