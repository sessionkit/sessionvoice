"""EARS-202 G3: bin/call-tap finds its Silero VAD model where the package does, not
under one machine's private tree. EARS_SILERO_MODEL, then EARS_SILERO_ONNX, then
~/.cache/ears/silero_vad.onnx: the home src/ears/vad.py silero_model_path() reads and
`ears install` downloads to. A missing model is refused up front, naming `ears install`.

Every run is fenced: HOME and XDG_STATE_HOME under tmp_path, `sox` and EARS_PY are
stubs that record their argv and exit, so no audio device, service or port is touched."""
import os, subprocess
from pathlib import Path

import pytest

from ears import envnames

ROOT = Path(__file__).resolve().parents[1]
CALL_TAP = ROOT / "bin" / "call-tap"


@pytest.fixture
def fence(tmp_path):
    home = tmp_path / "home" / "jim"; home.mkdir(parents=True)
    stubs = tmp_path / "stubs"; stubs.mkdir()
    argv = tmp_path / "py-argv"
    sox = stubs / "sox"; sox.write_text("#!/bin/sh\nexit 0\n"); sox.chmod(0o755)
    py = stubs / "python"
    py.write_text(f'#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done > "{argv}"\nexit 0\n')
    py.chmod(0o755)
    env = {k: v for k, v in os.environ.items()
           if k not in (*envnames.group("EARS_SILERO_MODEL"), "EARS_SILERO_ONNX")}
    env.update(HOME=str(home), XDG_STATE_HOME=str(tmp_path / "state"), EARS_PY=str(py),
               PATH=f"{stubs}:{env.get('PATH', '/usr/bin:/bin')}")

    def run(*args, **extra):
        e = dict(env, **{k: str(v) for k, v in extra.items()})
        return subprocess.run(["bash", str(CALL_TAP), *args], capture_output=True,
                              text=True, env=e, timeout=30)

    def model_used():
        lines = argv.read_text().splitlines()
        return lines[lines.index("--silero-model") + 1]

    run.home, run.tmp, run.argv, run.model_used = home, tmp_path, argv, model_used
    return run


def put(path):
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b"onnx"); return path


def test_call_tap_names_no_private_path():
    text = CALL_TAP.read_text()
    for ours in (".vera", ".ivy", "/Users/", "apple-fm"):
        assert ours not in text, ours


def test_default_is_the_ears_cache_under_home(fence):
    model = put(fence.home / ".cache" / "ears" / "silero_vad.onnx")
    r = fence("up")
    assert r.returncode == 0, r.stderr
    assert fence.model_used() == str(model)


def test_ears_silero_onnx_beats_the_cache(fence):
    put(fence.home / ".cache" / "ears" / "silero_vad.onnx")
    model = put(fence.tmp / "elsewhere" / "vad.onnx")
    r = fence("up", EARS_SILERO_ONNX=model)
    assert r.returncode == 0, r.stderr
    assert fence.model_used() == str(model)


def test_ears_silero_model_beats_both(fence):
    put(fence.home / ".cache" / "ears" / "silero_vad.onnx")
    other = put(fence.tmp / "elsewhere" / "vad.onnx")
    model = put(fence.tmp / "documented" / "silero.onnx")
    r = fence("up", EARS_SILERO_ONNX=other, EARS_SILERO_MODEL=model)
    assert r.returncode == 0, r.stderr
    assert fence.model_used() == str(model)


def test_the_old_name_is_still_read(fence):
    """EARS-294: VOICEMODE_SILERO_MODEL is EARS_SILERO_MODEL's deprecated alias."""
    put(fence.home / ".cache" / "ears" / "silero_vad.onnx")
    other = put(fence.tmp / "elsewhere" / "vad.onnx")
    model = put(fence.tmp / "documented" / "silero.onnx")
    r = fence("up", EARS_SILERO_ONNX=other, VOICEMODE_SILERO_MODEL=model)
    assert r.returncode == 0, r.stderr
    assert fence.model_used() == str(model)


def test_the_new_name_beats_the_old(fence):
    new = put(fence.tmp / "new" / "silero.onnx")
    old = put(fence.tmp / "old" / "silero.onnx")
    r = fence("up", EARS_SILERO_MODEL=new, VOICEMODE_SILERO_MODEL=old)
    assert r.returncode == 0, r.stderr
    assert fence.model_used() == str(new)


def test_an_empty_new_name_is_set_so_the_old_one_is_not_read(fence):
    """Presence, not truthiness, between the two names: EARS_SILERO_MODEL="" is set,
    so VOICEMODE_SILERO_MODEL is not read. An empty path is no model, so the search
    then goes on to EARS_SILERO_ONNX, as an empty old name always did."""
    old = put(fence.tmp / "old" / "silero.onnx")
    onnx = put(fence.tmp / "onnx" / "vad.onnx")
    r = fence("up", EARS_SILERO_MODEL="", VOICEMODE_SILERO_MODEL=old, EARS_SILERO_ONNX=onnx)
    assert r.returncode == 0, r.stderr
    assert fence.model_used() == str(onnx)


def test_an_empty_old_name_alone_falls_through_as_before(fence):
    onnx = put(fence.tmp / "onnx" / "vad.onnx")
    r = fence("up", VOICEMODE_SILERO_MODEL="", EARS_SILERO_ONNX=onnx)
    assert r.returncode == 0, r.stderr
    assert fence.model_used() == str(onnx)


def test_missing_model_is_refused_naming_ears_install(fence):
    r = fence("up")
    assert r.returncode == 2
    assert "ears install" in r.stderr
    assert str(fence.home / ".cache" / "ears" / "silero_vad.onnx") in r.stderr
    assert not fence.argv.exists(), "no capture may start without a model"
    assert not (fence.tmp / "state" / "ears" / "call-tap" / "sox.pid").exists()


def test_an_explicit_model_that_is_missing_is_refused_not_skipped(fence):
    put(fence.home / ".cache" / "ears" / "silero_vad.onnx")
    gone = fence.tmp / "gone.onnx"
    r = fence("up", EARS_SILERO_MODEL=gone)
    assert r.returncode == 2
    assert str(gone) in r.stderr and "ears install" in r.stderr
    assert not fence.argv.exists()


def test_status_and_down_need_no_model(fence):
    assert fence("status").returncode == 0
    assert fence("down").returncode == 0


def test_the_far_end_leg_keeps_no_missed_reply_clips(fence):
    # EARS-280: the missed-reply clip is for the user's MIC audio, and it defaults on
    # for every --source mic. call-tap's leg is --source mic on "BlackHole 2ch", the FAR
    # END's voice, so it must turn clips off by name. Parsed by the capture's own parser.
    from ears.listen.__main__ import _missed_reply, build_parser

    put(fence.home / ".cache" / "ears" / "silero_vad.onnx")
    assert fence("up").returncode == 0
    lines = fence.argv.read_text().splitlines()
    args = build_parser().parse_args(lines[lines.index("capture"):])
    assert args.source == "mic" and args.device == "BlackHole 2ch"
    assert args.missed_reply_window == 0
    assert _missed_reply(args) is None
