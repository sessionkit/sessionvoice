"""EARS-289 (SK-46 G35, voice half): no usable backend is one clear line, not a traceback.

`pick("auto")` raised a bare RuntimeError and `ears listen`/`ears file` caught only
KeyboardInterrupt and BrokenPipeError, so a stranger with no backend saw a traceback.
Now pick raises NoBackend (still a RuntimeError) carrying every backend's reason, and
`ears` prints it as one stderr line and exits 1. Every backend's why_not is faked here:
nothing probes :2022, imports MLX or looks for the Swift helper.
"""
import subprocess
import sys
from pathlib import Path

import pytest

from ears import backends, cli
from ears.backends import NoBackend, apple

ROOT = Path(__file__).resolve().parents[1]

REASONS = {
    "apple": "not macOS",
    "nemotron": "mlx-audio with Nemotron not importable (ModuleNotFoundError: No module named 'mlx')",
    "whisper": "nothing at http://127.0.0.1:2022/v1/audio/transcriptions (URLError)",
}


@pytest.fixture
def none_usable(monkeypatch):
    for name, why in REASONS.items():
        monkeypatch.setattr(backends._mod(name), "why_not", lambda why=why: why)


def test_pick_auto_raises_no_backend_with_every_reason(none_usable):
    with pytest.raises(NoBackend) as ei:
        backends.pick("auto")
    e = ei.value
    assert isinstance(e, RuntimeError)          # callers catching RuntimeError still do
    assert e.reasons == REASONS
    msg = str(e)
    assert "\n" not in msg
    for name, why in REASONS.items():
        assert f"{name}: {why}" in msg


def test_pick_auto_returns_the_first_usable_without_raising(monkeypatch):
    monkeypatch.setattr(backends._mod("apple"), "why_not", lambda: "not macOS")
    monkeypatch.setattr(backends._mod("nemotron"), "why_not", lambda: None)
    monkeypatch.setattr(backends._mod("whisper"), "why_not", lambda: pytest.fail("probed past the first usable"))
    assert backends.pick("auto") == "nemotron"


def test_pick_by_name_is_unchanged():
    assert backends.pick("whisper") == "whisper"


def test_a_multiline_reason_still_makes_one_line():
    e = NoBackend({"apple": "not macOS", "nemotron": "ImportError: first\n  second line"})
    assert "\n" not in str(e)
    assert "nemotron: ImportError: first second line" in str(e)


@pytest.mark.parametrize("argv", [["listen"], ["file", "nothing.wav"]])
def test_ears_prints_one_line_and_exits_non_zero(none_usable, capsys, argv):
    rc = cli.main(argv + ["--vad", "none"])
    out, err = capsys.readouterr()
    assert rc != 0
    assert out == ""
    lines = err.splitlines()
    assert len(lines) == 1, err
    assert lines[0].startswith("ears: no ears backend usable here: ")
    for name in REASONS:
        assert f"{name}: " in lines[0]
    assert "Traceback" not in err


def test_the_real_command_shows_no_traceback(tmp_path):
    # The whole process, as a stranger runs it: no backend is usable because each
    # why_not is replaced before main runs (an interpreter-level stub, no network).
    stub = (
        "import sys\n"
        "from ears import backends, cli\n"
        f"R = {REASONS!r}\n"
        "for n, w in R.items():\n"
        "    setattr(backends._mod(n), 'why_not', lambda w=w: w)\n"
        "sys.exit(cli.main(['listen', '--vad', 'none']))\n"
    )
    r = subprocess.run([sys.executable, "-c", stub], input=b"", capture_output=True,
                       cwd=tmp_path, timeout=60)
    err = r.stderr.decode()
    assert r.returncode == 1, err
    assert "Traceback" not in err
    assert len(err.splitlines()) == 1, err
    assert err.startswith("ears: no ears backend usable here: ")


def test_the_apple_hint_names_the_real_install_command(monkeypatch):
    monkeypatch.setattr(apple.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(apple.platform, "mac_ver", lambda: ("27.0", ("", "", ""), ""))
    monkeypatch.setattr(apple, "helper_path", lambda: None)
    why = apple.why_not()
    assert "`ears install --apple`" in why
