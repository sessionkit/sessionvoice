"""Nothing is born outside the kit: the unit stand-in for the born-after scan
(EARS-492 do-005, design.md's test plan, row "Fresh HOME, nothing born outside").

On an EMPTY fake HOME the test drives every fresh-install writer that runs headless:

- the resolver's CLIs (``python -m ears.home``, ``home.py`` run as a file, ``ears home``);
- ``ears-arm``, run from a copy of the plugin layout whose ``bin/heard-first`` is a stub;
- a heard write (``heard.py write event``);
- the speakers-home mkdir, by ``ears services install --only speakers`` behind the install
  fence (launchctl faked, LaunchAgents under the fake HOME);
- a render of each of the 6 plist templates (``bin/render-plist``).

Afterwards it finds none of the paths in :data:`FORBIDDEN`, each named on its own, and
nothing born under HOME outside ``~/.sessionkit``, the fenced ``~/Library/LaunchAgents`` and
the speakers side-car's venv (``~/.local/share/ears/speakers/venv``: venvs stay, by ruling).
install's network seam is faked to leave that venv as uv would, so the scan sees the folder a
real install makes; ``~/.local/share/ears/speakers`` counts as born when it holds anything
beside ``venv/`` (the enrolment, which moved).

The twin, an EXISTING host: with ``~/.sessionvoice`` (and separately ``~/.voicemode``)
holding a heard log, the same writers leave ``~/.sessionkit/voice/state`` absent, and they
write into the old root instead. Every test runs on a fake HOME under tmp_path: nothing is
installed for real, launchctl is never run, nothing is restarted.
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

from ears import cli, home, services
from test_services_install_checks import fence  # noqa: F401  (a fixture)
from test_state_items import _speakers_fence
from tests import plugin_copy

ROOT = Path(__file__).resolve().parents[1]
#: Every path a fresh install must NOT make, under HOME. The README's list, written out in
#: full: a fresh host keeps voice's state in ~/.sessionkit/voice/state, and nothing else.
FORBIDDEN = (
    ".sessionvoice",                         # the 0.3.2 root
    ".voicemode",                            # VoiceMode's folder, an old root
    ".ears",                                 # the old shared seat home (ears-arm made it)
    ".local/state/ears",                     # call-tap's and call-ears' old folders
    ".local/state/audio",                    # the audio devices file's old folder
    ".local/share/ears/speakers",            # the speaker enrolment's old home
    ".cache/ears/ears-mail.state.json",      # ears-mail's old --state
)
#: What the fresh writers may make under HOME: the kit, and the LaunchAgents folder the
#: install fence puts under the fake HOME (services.AGENTS).
ALLOWED = (".sessionkit", "Library/LaunchAgents", home.SPEAKERS_VENV)
#: The one forbidden path a real install makes the parent of: the speakers side-car's venv,
#: which the ruling keeps outside the kit (venvs stay). There, only the ENROLMENT is
#: forbidden: the folder may hold its venv/ and nothing else.
VENV_PARENT = ".local/share/ears/speakers"
#: The variables that would move a path away from the fake HOME, or name a real one.
STRIP = (*home.ENV, *(it.env for it in home.ITEMS.values() if it.env), home.SEAT_ENV,
         "EARS_SPEAKER_PY", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME",
         "XDG_CONFIG_HOME", "EARS_LOG_DIR", "MOUTH_LOG_DIR", "SESSIONS_DIR", "PYTHONPATH",
         *plugin_copy.STRIP)
TEMPLATES = sorted((ROOT / "launchd").glob("*.plist.in"))


def _env(h: Path, **extra) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in STRIP}
    env.update(HOME=str(h), **extra)
    return env


def _run(argv, h: Path, **extra) -> subprocess.CompletedProcess:
    return subprocess.run([str(a) for a in argv], env=_env(h, **extra), capture_output=True,
                          text=True, timeout=60)


def _uv_venv(argv) -> subprocess.CompletedProcess:
    """install's network seam, faked as uv would leave it: `uv venv ... DIR` makes DIR with a
    python in it, so the scan sees the venv a real install builds; the pip install and the
    model download write nothing a scan of HOME would see."""
    argv = [str(a) for a in argv]
    if argv[1:2] == ["venv"]:
        py = Path(argv[-1]) / "bin/python"
        py.parent.mkdir(parents=True, exist_ok=True)
        py.write_text("#!/bin/sh\n")
        py.chmod(0o755)
    return subprocess.CompletedProcess(argv, 0, "", "")


def _born(h: Path, skip: str = "") -> list[str]:
    """The forbidden paths under h, each by name. The speakers folder counts only when it holds
    more than the venv: its enrolment is what moved, and the venv stays by ruling."""
    born = []
    for rel in FORBIDDEN:
        if rel == skip or not os.path.lexists(h / rel):
            continue
        if rel == VENV_PARENT and sorted(c.name for c in (h / rel).iterdir()) == ["venv"]:
            continue
        born.append(rel)
    return born


def _tree(h: Path) -> list[str]:
    """Every path under h (links not followed), relative to it."""
    return sorted(p.relative_to(h).as_posix() for p in h.rglob("*"))


@pytest.fixture
def h(fence, monkeypatch, tmp_path):
    """An EMPTY fake HOME, behind the install fence (tests/test_services_install_checks.py)."""
    d = fence["home"]
    _speakers_fence(fence, monkeypatch)
    monkeypatch.setattr(services, "_speakers_sh", _uv_venv)
    for k in STRIP:
        monkeypatch.delenv(k, raising=False)
    # the fence leaves a cache file for the stt checks; the speakers install needs none of it,
    # and the scan starts from an empty HOME
    (d / ".cache/ears/silero_vad.onnx").unlink()
    (d / ".cache/ears").rmdir()
    (d / ".cache").rmdir()
    assert _tree(d) == []
    return d


def drive(h: Path, tmp_path: Path, capsys) -> dict:
    """Run every headless fresh-install writer on h; return what each answered."""
    out = {}
    # 1. the resolver, from all three CLIs
    for argv in ([sys.executable, "-B", "-m", "ears.home", "state-dir"],
                 [sys.executable, "-B", "-m", "ears.home", "state-dir", "--json"],
                 [sys.executable, "-B", ROOT / "src/ears/home.py", "state-dir"],
                 *([sys.executable, "-B", "-m", "ears.home", "state-dir", n] for n in home.ITEMS)):
        p = _run(argv, h)
        assert p.returncode == 0, (argv, p.stderr)
    out["root"] = Path(_run([sys.executable, "-B", "-m", "ears.home", "state-dir"], h)
                       .stdout.strip())
    assert cli.main(["home", "state-dir", "--json"]) == 0
    capsys.readouterr()

    # 2. ears-arm, from a copy of the plugin layout (its heard-first a stub: nothing listens)
    plugin = plugin_copy.make(tmp_path)
    seat = plugin_copy.probe_seat("ears492born")
    p = _run(["bash", plugin / "skills/ears/scripts/ears-arm", seat, "talk"], h,
             PATH=os.pathsep.join(["/usr/bin", "/bin"]))
    assert p.returncode == plugin_copy.STUB_RC, p.stderr
    out["seat"] = seat

    # 3. a heard write, as the keeper writes one
    p = _run([sys.executable, "-B", ROOT / "src/ears/heard.py", "write", "event", "born-scan"], h)
    assert p.returncode == 0, p.stderr

    # 4. the speakers-home mkdir: install's own path, behind the fence
    assert services.install(["speakers"]) == 0
    out["speakers"] = services.speakers_home()

    # 5. a render of each template (to stdout: the renderer writes nothing else)
    fake = {"SPEAKER_PY": h / home.SPEAKERS_VENV / "bin/python", "AGENT_REFS": "alice",
            "TALK": tmp_path / "bin/talk"}
    for t in TEMPLATES:
        p = _run([sys.executable, "-B", ROOT / "bin/render-plist", t], h,
                 **{k: str(v) for k, v in fake.items()})
        assert p.returncode == 0, (t.name, p.stderr)
        plistlib.loads(p.stdout.encode())
    return out


def test_every_writer_is_driven():
    """The list the README names, and six templates: a scan of fewer writers proves less."""
    assert len(TEMPLATES) == 6
    assert set(home.ITEMS) == {"call-tap", "call-ears", "audio-devices", "speakers",
                               "ears-mail-state"}


def test_nothing_is_born_outside_the_kit_on_a_fresh_home(h, tmp_path, capsys):
    out = drive(h, tmp_path, capsys)
    new = h / home.NEW
    # the writers really wrote, and wrote in the kit
    assert out["root"] == new
    assert list((new / "logs/conversations").glob("heard_*.jsonl"))
    assert (new / home.SEATS / out["seat"]).is_dir()
    assert out["speakers"] == new / "speakers"
    assert (new / "speakers").stat().st_mode & 0o777 == 0o700
    # none of the old paths, each named
    # the venv a real install builds is there, outside the kit, as ruled; the speakers home
    # still answers the kit with it present (a venv-only old folder is never adopted)
    assert (h / home.SPEAKERS_VENV / "bin/python").is_file()
    assert services.speakers_home() == new / "speakers"
    born = _born(h)
    assert born == [], f"born outside the kit: {born}"
    # and nothing at all outside the kit, but the fenced LaunchAgents folder, the speakers
    # venv, and their parents
    stray = [p for p in _tree(h)
             if not any(p == a or p.startswith(a + "/") or a.startswith(p + "/")
                        for a in ALLOWED)]
    assert stray == [], stray


@pytest.mark.parametrize("old", home.OLD_ROOTS)
def test_an_existing_host_keeps_its_old_root_and_the_kit_gets_no_state(old, h, tmp_path,
                                                                       capsys):
    root = h / old
    conv = root / "logs/conversations"
    conv.mkdir(parents=True)
    (conv / "heard.lock").write_text("")
    (conv / "heard_2030-06-09.jsonl").write_text('{"seq": 1}\n')   # the anchor day, never today
    out = drive(h, tmp_path, capsys)
    # every writer used the old root, adopted in place
    assert out["root"] == root
    assert len(list(conv.glob("heard_*.jsonl"))) == 2
    assert (root / home.SEATS / out["seat"]).is_dir()
    assert out["speakers"] == root / "speakers"
    # ...and nothing under the kit's state root
    assert not os.path.lexists(h / home.NEW)
    # no other old path was born either: only the root this host already had
    born = _born(h, skip=old)
    assert born == [], f"born outside the kit: {born}"


def test_the_scan_sees_each_forbidden_path(tmp_path):
    """A zero is a receipt only when the same check is shown to hit."""
    for rel in FORBIDDEN:
        d = tmp_path / rel.replace("/", "_")
        d.mkdir()
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.mkdir() if not rel.endswith(".json") else p.write_text("{}")
        assert _born(d) == [rel]


def test_the_scan_lets_the_venv_stay_but_sees_an_enrolment_beside_it(tmp_path):
    """The speakers folder holding only venv/ is the ruled venv; anything beside it is the
    enrolment, and is born."""
    (tmp_path / home.SPEAKERS_VENV / "bin").mkdir(parents=True)
    assert _born(tmp_path) == []
    (tmp_path / VENV_PARENT / "refs").mkdir()
    assert _born(tmp_path) == [VENV_PARENT]
