"""The mouth's directory: ``$MOUTH_DIR``, else ``<sessionvoice home>/mouth``."""

from __future__ import annotations

from pathlib import Path

from ears import envnames
from ears.home import sessionvoice_home


def default_mouth_dir() -> Path:
    """``<sessionvoice home>/mouth`` (``ears.home``): the live mouth's, whatever $MOUTH_DIR says."""
    return sessionvoice_home() / "mouth"


def mouth_dir() -> Path:
    env = envnames.get("MOUTH_DIR")
    if env:
        return Path(env).expanduser()
    return default_mouth_dir()
