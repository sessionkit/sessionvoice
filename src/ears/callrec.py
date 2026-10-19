"""callrec - the name of a Delta call's recording in its call dir, the one place it is spelt.

The bot writes the audio the ears heard to STEM.wav, and STEM.json beside it (start_epoch is
audio second 0 on the wall clock). STEM was mike-call until sessionphone named it for
the role: caller-call. Old call dirs keep the old name, so every reader takes caller-call
first and falls back to mike-call.

Stdlib only, and it imports nothing from ears: `ears eval` imports it as ears.callrec, and the
speaker side-car (its own venv, no ears) and bench/clock_check.py (the bot's venv) load this
file by its path. Keep it that way.
"""
from pathlib import Path

STEMS = ("caller-call", "mike-call")  # newest first: mike-call was renamed caller-call


def names(ext):
    """The file names a reader looks for, in the order it looks: ["caller-call.EXT", "mike-call.EXT"]."""
    return [f"{stem}.{ext}" for stem in STEMS]


def looked_for(ext):
    """Both names, for a message saying none was there: "caller-call.EXT or mike-call.EXT"."""
    return " or ".join(names(ext))


def call_file(call_dir, ext):
    """CALL_DIR's recording file with extension EXT ("wav" or "json"): caller-call.EXT if it
    is there, else mike-call.EXT, else None."""
    for name in names(ext):
        path = Path(call_dir) / name
        if path.exists():
            return path
    return None
