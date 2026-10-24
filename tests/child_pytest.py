"""A child pytest gets its own basetemp (EARS-436).

A test that runs a CHILD pytest (``sys.executable -m pytest ...``) hands it
:func:`basetemp`, a ``--basetemp`` inside the parent test's own tmp dir.

Without it, the child makes a numbered dir in the host-wide
``$TMPDIR/pytest-of-<user>/`` that every pytest of this account shares (other
seats' suites, other projects). Its ``pytest_sessionfinish`` (``_pytest/tmpdir.py``)
then deletes every dir in that root with no live lock and three newer
numbers, and every ``garbage-*`` dir left there. That includes the whole basetemp of any suite that has
just finished, tens of thousands of files or more, and the child does it before it can exit.
The child prints its dots and ``[100%]``, then deletes another session's files until the
parent's ``subprocess`` timeout kills it. On a loaded host that was 120 s
(test_audio_fence, test_live_home_guard). With ``--basetemp`` the child never
touches that root: pytest uses the given dir as it is and registers no cleanup.

``tests/test_child_pytest.py`` holds the mechanism and holds every spawner in
``tests/`` to this.
"""
from __future__ import annotations

from pathlib import Path

#: Short on purpose: a child's tmp_path is this dir plus its own test's name, and some
#: children put a unix socket there (104 bytes on macOS).
DIRNAME = "bt"


def basetemp(tmp_path) -> str:
    """The ``--basetemp=`` argument for a child pytest run by the test that owns ``tmp_path``."""
    return f"--basetemp={Path(tmp_path) / DIRNAME}"
