"""keeper/defaults/mouth-sounds.json is what mouth-sounds.py writes on a fresh Mac.

With no mouth-sounds.json in the settings folder, the watcher writes its DEFAULT there and
plays from it, and a bare `ears services install` loads it. The shipped copy in
keeper/defaults must say the same, cue for cue: it once lacked scoop, which the watcher
gained after the copy was made. test_ears_sounds_call holds ears-sounds.json to
ears-sounds.py the same way.

The watcher runs DRY (no device asked for), with sounddevice stubbed first on its PYTHONPATH,
as test_mouth_sounds_scoop_by.py runs it, under paths in a temp dir; its STOP file ends it."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("numpy")
SD_STUB = "class PortAudioError(Exception):\n    pass\n"
SCRIPT = Path(__file__).resolve().parents[1] / "keeper" / "mouth-sounds.py"
SHIPPED = SCRIPT.parent / "defaults" / "mouth-sounds.json"
CUES = ("drop", "front", "canned", "bud", "scoop")


def _written(tmp_path: Path) -> Path:
    """Start the watcher with no settings file, wait for its banner, stop it; the file."""
    for d in ("cursors", "heard", "md/new", "md/cur", "stub"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    (tmp_path / "stub" / "sounddevice.py").write_text(SD_STUB)
    settings, stop = tmp_path / "settings" / "mouth-sounds.json", tmp_path / "STOP"
    settings.parent.mkdir()
    env = dict(os.environ, PYTHONPATH=str(tmp_path / "stub"), MOUTH_SOUNDS_DRY="1",
               MOUTH_SOUNDS_SETTINGS=str(settings), MOUTH_SOUNDS_STOP=str(stop),
               MOUTH_SOUNDS_LOG=str(tmp_path / "sounds.jsonl"),
               MOUTH_SOUNDS_CURSORS=str(tmp_path / "cursors"),
               MOUTH_SOUNDS_HEARD_DIR=str(tmp_path / "heard"), MOUTH_MAILDIR=str(tmp_path / "md"),
               MOUTH_SOUNDS_FIRED=str(tmp_path / "fired"),
               MOUTH_SOUNDS_CANNED=str(tmp_path / "canned"))
    keeper = subprocess.Popen([sys.executable, str(SCRIPT)], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        banner = keeper.stdout.readline()
        assert banner.startswith("mouth-sounds: watching"), banner + keeper.stdout.read()
    finally:
        stop.touch()
        try:
            keeper.wait(timeout=10)
        except subprocess.TimeoutExpired:
            keeper.kill()
    assert keeper.returncode == 0, keeper.stdout.read()
    return settings


def test_the_shipped_defaults_are_what_the_watcher_writes(tmp_path):
    written = json.loads(_written(tmp_path).read_text())
    shipped = {k: v for k, v in json.loads(SHIPPED.read_text()).items() if not k.startswith("_")}
    assert written == shipped, "keeper/defaults/mouth-sounds.json and mouth-sounds.py's DEFAULT must be the same values"


def test_the_five_cues_are_on_by_default():
    sounds = json.loads(SHIPPED.read_text())["sounds"]
    assert tuple(sounds) == CUES
    assert all(sounds[c]["on"] is True for c in CUES)
