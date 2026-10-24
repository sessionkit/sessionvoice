"""A throwaway copy of the plugin's ears-arm layout, with a STUB heard-first (EARS-422).

ears-arm finds heard-first from its OWN location first (the plugin root's bin/, three
levels up from skills/ears/scripts/), before $CLAUDE_PLUGIN_ROOT and PATH. So a stub
put earlier on PATH no longer fences the real one: run from the checkout, ears-arm
would find the checkout's real bin/heard-first and could arm a real waiter on the
real mic log.

The fence is therefore a copy. :func:`make` copies ears-arm and ears-count (the
scripts and their bin/ shims) into ``<tmp>/plugin/``, laid out as the plugin is,
and writes ``<tmp>/plugin/bin/heard-first`` as a stub that records its own path and
argv and exits 99. Run the COPY, and its own location IS the stub. Nothing here
reaches the checkout's bin/heard-first.

:func:`stub_heard_first` writes the same stub anywhere, for the $CLAUDE_PLUGIN_ROOT
and PATH routes. Every stub records ``$0`` first, so a test can assert WHICH stub ran.

:data:`STRIP` lists the variables a fence must drop from the runner's env: a runner
inside Claude Code may export CLAUDE_PLUGIN_ROOT, which names a real plugin root.

:func:`probe_seat` names a seat for ONE test (EARS-432). ears-count counts heard-first
waiters HOST-WIDE, by ``cut:<seat>`` in ps; no HOME or state dir fences that count.
A fixed probe seat therefore collides with a concurrent run of the same file: its
stub, briefly in ps, is counted as a waiter, and ears-arm exits 2. A seat name no
other run can hold is the only fence the count has.
"""
from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ("ears-arm", "ears-count")
STRIP = ("EARS_HOME", "EARS_SPEAKERS", "CLAUDE_PLUGIN_ROOT")
STUB_RC = 99


def probe_seat(prefix: str) -> str:
    """A seat name unique to one call: ``prefix`` and 12 hex digits, e.g.
    ``ears422probe3f9a0c1b2d4e``. Lowercase alphanumeric only, because ears-count puts
    the seat into the regex ``cut:<seat>( |$)`` unescaped. Every name is the same
    length for a prefix, so no probe seat is a prefix of another."""
    seat = f"{prefix}{uuid.uuid4().hex[:12]}"
    assert re.fullmatch(r"[a-z0-9]+", seat), seat
    return seat


def stub_heard_first(bindir: Path, ran: Path) -> Path:
    """Write an executable stub ``bindir/heard-first``: it writes ``$0`` on the first
    line of ``ran`` and its argv on the second, says so on stderr, and exits 99."""
    bindir.mkdir(parents=True, exist_ok=True)
    hf = bindir / "heard-first"
    hf.write_text("#!/bin/sh\n"
                  'echo "STUB heard-first EXECD: $0 $*" >&2\n'
                  f'printf "%s\\n%s\\n" "$0" "$*" > "{ran}"\n'
                  f"exit {STUB_RC}\n")
    hf.chmod(0o755)
    return hf


def make(tmp: Path, *, heard_first: bool = True) -> Path:
    """Copy the ears-arm layout into ``tmp/plugin`` and return that root.

    With ``heard_first`` the copy's bin/ holds the stub (it records to
    ``tmp/plugin-ran``); without, bin/ has no heard-first at all, so the own-location
    route misses and a test can drive the other two."""
    plugin = tmp / "plugin"
    scripts = plugin / "skills" / "ears" / "scripts"
    scripts.mkdir(parents=True)
    (plugin / "bin").mkdir()
    for name in SCRIPTS:
        shutil.copy2(ROOT / "skills" / "ears" / "scripts" / name, scripts / name)
        shutil.copy2(ROOT / "bin" / name, plugin / "bin" / name)
    if heard_first:
        stub_heard_first(plugin / "bin", tmp / "plugin-ran")
    return plugin
