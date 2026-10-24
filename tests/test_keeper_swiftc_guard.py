"""The keeper never runs swiftc on a Mac without the Command Line Tools (EARS-424).

watch_up() builds input-watch with a bare `swiftc` when the binary is absent. Without
the Command Line Tools, /usr/bin/swiftc is the xcode-select install stub: it opens the
"install the command line developer tools" dialog and exits non-zero. Each fresh keeper
start (every launchd restart or reinstall) tried again, so the dialog came back. Now the
keeper asks `xcode-select -p` first (it never prompts), and builds only when it names a
directory that exists; otherwise it polls system_profiler, says so once, and writes the
mode `ears services status` reports.

FAKES ONLY: a tmp dir first on PATH holds a stub `swiftc` that appends its argv to a
calls file and exits 1, and a stub `xcode-select`. The real swiftc and xcode-select are
never run (on a Mac without the tools, running them is the bug). watch_up() runs alone,
sliced out of the keeper the way tests/test_keeper_fallback.py runs its functions.
"""
import os
import subprocess
import time
from pathlib import Path

import pytest

from ears import services
from test_services_install_checks import _store, fence  # noqa: F401  (a fixture)
from test_services_speech import sx  # noqa: F401  (a fixture)

KEEPER = Path(__file__).resolve().parents[1] / "keeper" / "ears-keeper.sh"
NO_CLT = "no Command Line Tools: polling system_profiler (install them for faster device changes)"


def _region(start: str, end: str) -> str:
    s = KEEPER.read_text()
    return s[s.index(start):s.index(end)]


def _keeper_line(prefix: str) -> str:
    return next(l for l in KEEPER.read_text().splitlines() if l.startswith(prefix))


def _stub(path: Path, body: str) -> None:
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(0o755)


def _watch_up(tmp_path: Path, *, clt: str, built: bool = False,
              loops: int = 1) -> tuple[subprocess.CompletedProcess, Path, Path]:
    """Run watch_up() LOOPS times, as the keeper's start and loop do. CLT: "here" (-p names a
    directory that exists), "stale" (-p exits 0 naming one that does not), "missing" (-p exits
    2, a clean Mac). BUILT: input-watch is already there and newer than its source (a stub that
    writes a state file), else absent. Returns (the run, the swiftc calls file, RUN)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "swiftc.calls"
    _stub(bin_dir / "swiftc", f'printf "%s\\n" "$*" >>{str(calls)!r}\nexit 1\n')
    if clt == "missing":   # what the real `xcode-select -p` does on a clean Mac
        _stub(bin_dir / "xcode-select",
              'if [ "${1:-}" = -p ]; then\n'
              '  echo "xcode-select: error: unable to get active developer directory" >&2; exit 2\n'
              'fi\nexit 1\n')
    else:                  # -p prints the developer dir and exits 0; "stale": that dir is gone
        dev = tmp_path / "CommandLineTools"
        if clt == "here":
            dev.mkdir()
        _stub(bin_dir / "xcode-select", f'if [ "${{1:-}}" = -p ]; then echo {str(dev)!r}; exit 0; fi\nexit 0\n')
    run = tmp_path / "run"
    run.mkdir()
    src = tmp_path / "input-watch.swift"
    src.write_text("// not compiled: swiftc here is a stub\n")
    if built:              # the watcher: writes its state file (default input, then the inputs), stays up
        _stub(run / "input-watch", 'printf "MacBook Pro Microphone\\nMacBook Pro Microphone\\n" >"$1"\nexec sleep 30\n')
        old = time.time() - 3600
        os.utime(src, (old, old))   # an hour older than the binary: no build needed
    env = dict(os.environ,
               PATH=f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin",
               EARS_WATCH_BIN=str(run / "input-watch"))
    assert (run / "input-watch").exists() == built
    # The keeper's own WATCH_BIN and WATCH_MODE lines, then watching() .. watch_down().
    script = "\n".join([
        "set -uo pipefail",
        'note() { echo "NOTE $*"; }',
        f"RUN={str(run)!r}",
        _keeper_line("WATCH_BIN="),
        f"WATCH_SRC={str(src)!r}",
        'WATCH_STATE="$RUN/input-watch.state"; WATCH_PIDF="$RUN/input-watch.pid"',
        _keeper_line("WATCH_MODE="),
        'AUDIO_DEVICES="$RUN/devices.json"',
        'watch_pid=""; watch_fails=0',
        _region("watching() {", "watch_down() {"),
        _region("watch_down() {", "inputs() {"),
        f'for _ in $(seq {loops}); do watch_up; echo "rc=$? watch_fails=$watch_fails"; done',
        'echo "MODE $(cat "$WATCH_MODE")"',
        'echo "KEEPER $$"',
        "watch_down",
    ])
    proc = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=30)
    return proc, calls, run


def test_without_the_command_line_tools_swiftc_is_never_called(tmp_path):
    proc, calls, _ = _watch_up(tmp_path, clt="missing")
    assert "rc=1" in proc.stdout, proc.stdout + proc.stderr   # no watcher: polling system_profiler
    assert not calls.exists(), f"the swiftc stub was called: {calls.read_text()!r}\n{proc.stdout}"


def test_without_the_tools_it_says_so_once_per_keeper_start_not_once_per_loop(tmp_path):
    proc, calls, _ = _watch_up(tmp_path, clt="missing", loops=5)
    assert not calls.exists()
    assert proc.stdout.count(f"NOTE {NO_CLT}\n") == 1, proc.stdout
    assert proc.stdout.count("rc=1 watch_fails=3") == 5, proc.stdout   # given up: every loop polls
    assert "building" not in proc.stdout and "build FAILED" not in proc.stdout


def test_a_developer_dir_that_is_gone_counts_as_no_tools(tmp_path):
    """`xcode-select -p` can name a deleted CommandLineTools: swiftc would still prompt."""
    proc, calls, _ = _watch_up(tmp_path, clt="stale")
    assert not calls.exists(), calls.read_text()
    assert f"NOTE {NO_CLT}" in proc.stdout and "rc=1 watch_fails=3" in proc.stdout


def test_with_the_command_line_tools_input_watch_is_still_built(tmp_path):
    """The guard must not stop the build where the tools ARE installed (the stub swiftc
    fails it, so the keeper falls back as before)."""
    proc, calls, _ = _watch_up(tmp_path, clt="here")
    assert calls.exists(), proc.stdout + proc.stderr
    assert calls.read_text().startswith("-O -swift-version 5 -o "), calls.read_text()
    assert "input-watch: build FAILED; polling system_profiler" in proc.stdout
    assert "rc=1 watch_fails=3" in proc.stdout
    assert NO_CLT not in proc.stdout
    assert "MODE " in proc.stdout and "polling system_profiler: the input-watch build failed" in proc.stdout


def test_a_built_watcher_needs_no_tools_and_no_compiler(tmp_path):
    """The guard sits inside the must-build branch: a current binary runs, tools or none."""
    proc, calls, run = _watch_up(tmp_path, clt="missing", built=True)
    assert not calls.exists()
    assert NO_CLT not in proc.stdout, proc.stdout
    assert "rc=0 watch_fails=0" in proc.stdout, proc.stdout + proc.stderr
    keeper = next(l.split()[1] for l in proc.stdout.splitlines() if l.startswith("KEEPER "))
    mode = next(l for l in proc.stdout.splitlines() if l.startswith("MODE "))
    assert mode.startswith(f"MODE {keeper} input-watch, pid "), mode
    assert not (run / "input-watch.mode").exists(), "watch_down (the keeper's exit) removes the mode"


def test_without_the_tools_the_mode_file_says_polling_and_why(tmp_path):
    proc, _, _ = _watch_up(tmp_path, clt="missing")
    keeper = next(l.split()[1] for l in proc.stdout.splitlines() if l.startswith("KEEPER "))
    assert f"MODE {keeper} polling system_profiler: no Command Line Tools\n" in proc.stdout, proc.stdout


def test_the_keeper_never_names_xcode_select_by_absolute_path():
    """Bare, on PATH (rca-001 point 2): /usr/bin is on the launchd PATH, and a test can fake it."""
    s = KEEPER.read_text()
    assert "/usr/bin/xcode-select" not in s
    assert "xcode-select -p" in _region("clt_here() {", "watch_up() {")


# ---- `ears services status`: which mode the keeper is in -----------------------------------------

def _mode(run: Path, text: str) -> None:
    run.mkdir(parents=True, exist_ok=True)
    (run / "input-watch.mode").write_text(text)


def test_mode_line_from_a_live_keeper(tmp_path):
    _mode(tmp_path, f"{os.getpid()} polling system_profiler: no Command Line Tools\n")
    assert services.keeper_mode_line(tmp_path) == "       mode: polling system_profiler: no Command Line Tools"
    _mode(tmp_path, f"{os.getpid()} input-watch, pid 4242\n")
    assert services.keeper_mode_line(tmp_path) == "       mode: input-watch, pid 4242"


@pytest.mark.parametrize("text", [None, "", "garbage", "0 input-watch, pid 1", "-1 input-watch",
                                  "{dead} input-watch, pid 1"], ids=["absent", "empty", "no-pid",
                                                                    "pid-0", "pid-neg", "dead-keeper"])
def test_no_mode_line_unless_a_live_keeper_wrote_it(tmp_path, text):
    if text is not None:
        dead = subprocess.Popen(["true"]); dead.wait()
        _mode(tmp_path, text.format(dead=dead.pid))
    assert services.keeper_mode_line(tmp_path) is None


def test_mode_line_is_printable_and_cut(tmp_path):
    _mode(tmp_path, f"{os.getpid()} input-watch\x1b[2J" + "x" * 500)
    line = services.keeper_mode_line(tmp_path)
    assert "\x1b" not in line and len(line) <= len("       mode: ") + 120


def test_keeper_run_follows_ears_run(monkeypatch, tmp_path):
    monkeypatch.setenv("EARS_RUN", str(tmp_path / "elsewhere"))
    assert services.keeper_run() == tmp_path / "elsewhere"
    monkeypatch.delenv("EARS_RUN")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert services.keeper_run() == tmp_path / ".cache/ears/run"


def test_status_prints_the_keepers_mode_under_its_row(sx, monkeypatch, capsys):
    monkeypatch.setattr(services, "weights_status", lambda: [])
    monkeypatch.delenv("EARS_RUN", raising=False)
    label = services._label("keeper")
    sx["listed"][label] = str(os.getpid())
    (services.AGENTS).mkdir(parents=True, exist_ok=True)
    _mode(sx["home"] / ".cache/ears/run", f"{os.getpid()} polling system_profiler: no Command Line Tools\n")
    assert services.status() == 0
    out = capsys.readouterr().out.splitlines()
    row = next(i for i, l in enumerate(out) if l.startswith("keeper "))
    assert out[row + 1] == "       mode: polling system_profiler: no Command Line Tools", out
