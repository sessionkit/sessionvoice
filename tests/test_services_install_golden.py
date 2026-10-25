"""Golden: what `ears services install` prints and writes, pinned before the one-pass preflight.

The fixture ``fixtures/golden_services_install.json`` was captured BEFORE any change to
``src/`` for the one-pass preflight, by::

    EARS_INSTALL_GOLDEN_CAPTURE=1 uv run --locked pytest tests/test_services_install_golden.py

Never re-capture to make this pass: a difference is a change to what every install prints.

One deliberate change since the capture, made by hand to that one string: G4-speech-use (an
adopted mlx-audio on :8890) prints the cold-probe heads-up line just before it probes :8890.

A second deliberate change, made by hand to the three success stdouts (G1-default,
G3-voicemode-link, G4-speech-use): a real install now ends with one ``log    <path>`` row,
the install log it appended to (``services.install_log_path``). The dry runs write no log
and print no row, so they are unchanged.

Two kinds of case:

- **success** (every prerequisite present): rc, stdout, stderr and the bytes of every plist
  written are compared whole. The preflight must leave all of them exactly as they were.
- **refusal** (exactly one prerequisite missing): rc and stderr are compared whole. A block
  of one refused item is the single refusal it was. Stdout and the writes are not pinned
  here: a refused run now stops before the lines and writes that came ahead of a late
  refusal (the speakers row, the log dir), which is the fix.

Every case runs inside test_services_install_checks' fence (a temp HOME, launchctl and the
speech probes faked, nothing fetched or built). Each value is normalised the same way: the
test's temp dir becomes ``<TMP>``, ``services.ROOT`` ``<ROOT>``, ``sys.executable`` ``<PY>``,
``sys.prefix`` ``<PREFIX>`` and the uid in ``gui/<uid>`` ``<UID>``. Nothing else is masked."""
import json
import os
import sys
from pathlib import Path

import pytest

from ears import services
from ears.speech import Probe
from test_services_install_checks import _store, fence  # noqa: F401  (a fixture)
from test_services_speech import FOREIGN, VM, sx  # noqa: F401  (a fixture)

FIXTURE = Path(__file__).parent / "fixtures" / "golden_services_install.json"
CAPTURE = os.environ.get("EARS_INSTALL_GOLDEN_CAPTURE") == "1"


def _norm(text: str, tmp: Path) -> str:
    for real, mask in ((str(tmp.resolve()), "<TMP>"), (str(tmp), "<TMP>"),
                       (str(services.ROOT), "<ROOT>"),
                       (sys.executable, "<PY>"), (sys.prefix, "<PREFIX>"),
                       (f"gui/{os.getuid()}", "gui/<UID>")):
        text = text.replace(real, mask)
    return text


def _plists(tmp: Path) -> dict[str, str]:
    if not services.AGENTS.exists():
        return {}
    return {p.name: _norm(p.read_bytes().decode(), tmp) for p in sorted(services.AGENTS.glob("*.plist"))}


def _run(argv, capsys, tmp: Path, *, whole: bool) -> dict:
    capsys.readouterr()
    rc = services.main(argv)
    cap = capsys.readouterr()
    got = {"argv": argv, "rc": rc, "stderr": _norm(cap.err, tmp)}
    if whole:
        got.update(stdout=_norm(cap.out, tmp), plists=_plists(tmp))
    return got


def _check(case: str, got: dict) -> None:
    if CAPTURE:
        cases = json.loads(FIXTURE.read_text()) if FIXTURE.exists() else {}
        cases[case] = got
        FIXTURE.parent.mkdir(exist_ok=True)
        FIXTURE.write_text(json.dumps(dict(sorted(cases.items())), indent=1, ensure_ascii=False) + "\n")
        pytest.skip(f"captured {case} into {FIXTURE.name}")
    want = json.loads(FIXTURE.read_text())[case]
    assert got == want


@pytest.fixture
def pinned(fence, monkeypatch):
    """The fence, with what else varies between hosts pinned: no seat hint, the agents' python
    is this one, and the speakers' stray-sidecar scan sees nothing."""
    for k in ("TM_AGENT_ADDRESS", "EARS_PY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(services, "_pgrep", lambda pattern: [])
    return fence


# ---- success: every prerequisite present ---------------------------------------------------

@pytest.mark.parametrize("dry", [False, True], ids=["install", "dry-run"])
def test_a_bare_install_with_everything_present_is_unchanged(pinned, capsys, dry):
    _store(pinned["home"])
    argv = ["install", "--agent", "dave"] + (["--dry-run"] if dry else [])
    _check(f"G1-default{'-dry' if dry else ''}", _run(argv, capsys, pinned["tmp"], whole=True))


@pytest.mark.parametrize("dry", [False, True], ids=["install", "dry-run"])
def test_the_voicemode_copy_link_is_unchanged(pinned, capsys, dry):
    _store(pinned["home"])
    (pinned["home"] / ".cache/ears/silero_vad.onnx").unlink()
    copy = pinned["home"] / ".voicemode/models/silero/silero_vad.onnx"
    copy.parent.mkdir(parents=True)
    copy.write_bytes(b"onnx")
    argv = ["install", "--agent", "dave"] + (["--dry-run"] if dry else [])
    _check(f"G3-voicemode-link{'-dry' if dry else ''}", _run(argv, capsys, pinned["tmp"], whole=True))


def test_speech_use_with_mlx_audio_on_the_port_is_unchanged(sx, monkeypatch, capsys):
    for k in ("TM_AGENT_ADDRESS", "EARS_PY"):
        monkeypatch.delenv(k, raising=False)
    _store(sx["home"])
    sx["state"]["probe"] = Probe(holder=VM, connects=True, identity="mlx-audio")
    _check("G4-speech-use", _run(["install", "--agent", "dave"], capsys, sx["tmp"], whole=True))


# ---- refusal: exactly one prerequisite missing ---------------------------------------------

def test_the_agent_refusal_alone_is_unchanged(pinned, capsys):
    _check("R-A1-agent", _run(["install"], capsys, pinned["tmp"], whole=False))


def test_a_missing_keeper_script_alone_is_unchanged(pinned, monkeypatch, capsys):
    """K1 only: the checkout has settings/ (K2 passes) but no keeper/, and the package has
    neither."""
    import importlib.resources
    root = pinned["tmp"] / "root"
    root.mkdir()
    (root / "settings").symlink_to(services.ROOT / "settings")
    monkeypatch.setattr(services, "ROOT", root)
    monkeypatch.setattr(importlib.resources, "files", lambda pkg: pinned["tmp"] / "pkg")
    _check("R-K1-keeper", _run(["install", "--only", "keeper"], capsys, pinned["tmp"], whole=False))


def test_speakers_on_an_intel_mac_alone_is_unchanged(pinned, monkeypatch, capsys):
    for k in ("EARS_SPEAKERS_HOME", "EARS_SPEAKER_PY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(services, "_mac", lambda: ("x86_64", "13.6"))
    _check("R-P1-speakers", _run(["install", "--only", "speakers"], capsys, pinned["tmp"], whole=False))


def test_a_foreign_holder_on_the_speech_port_alone_is_unchanged(sx, monkeypatch, capsys):
    for k in ("TM_AGENT_ADDRESS", "EARS_PY"):
        monkeypatch.delenv(k, raising=False)
    sx["state"]["probe"] = Probe(holder=FOREIGN, connects=True, identity="foreign", why="GET / answered 404")
    _check("R-D1-speech", _run(["install", "--only", "speech"], capsys, sx["tmp"], whole=False))
