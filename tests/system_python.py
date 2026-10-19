"""The system python BELOW the package's 3.10 floor, for the tests that run a script on it
(EARS-402): Apple's /usr/bin/python3, 3.9.6 on a clean Mac.

`below_floor()` gives (path, "3.9.6") when this host has one, else (None, why). A test with
no such python SKIPS LOUDLY with that reason: it never passes silently on a host that could
not prove anything. On a Mac without the Command Line Tools, /usr/bin/python3 is an install
stub that opens a dialog, so it is not run unless `xcode-select -p` finds the tools.
"""

import functools
import platform
import shutil
import subprocess
from pathlib import Path

SYSTEM_PYTHON = Path("/usr/bin/python3")
FLOOR = (3, 10)


@functools.lru_cache(maxsize=None)
def below_floor() -> tuple:
    py = SYSTEM_PYTHON
    if not py.exists():
        return None, f"no {py} on this host: no system python below the 3.10 floor to run against"
    if platform.system() == "Darwin":
        xs = shutil.which("xcode-select", path="/usr/bin")
        if not xs or subprocess.run([xs, "-p"], capture_output=True, timeout=30).returncode != 0:
            return None, (f"{py} is macOS's install stub (no Command Line Tools): running it "
                          "would prompt, so there is no system python to run against")
    try:
        out = subprocess.run([str(py), "-I", "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"{py} did not run ({e}): no system python to run against"
    if out.returncode != 0:
        return None, f"{py} exited {out.returncode}: no system python to run against"
    version = out.stdout.strip()
    if tuple(int(x) for x in version.split(".")[:2]) >= FLOOR:
        return None, f"{py} is {version}, not below the 3.10 floor: nothing below it to run against"
    return str(py), version
