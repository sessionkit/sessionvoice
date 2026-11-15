"""Two mouth tests that leave a thread running past their end, for the CHILD pytest in
``tests/test_mouth_thread_settle.py`` only. The suite never collects this file (its name
is no ``test_*.py``); the child names it.

Each thread is a player dropping a line after its test's ``join`` gave up, as one did on
a loaded host: a second on, it asks sessionmail for the line's sender's box
(``notice.recipient``, route 4: ``message path ivy``), then touches ``$CHILD_LATE_DONE``.

- ``test_a_player_left_running`` starts it as every player is started, a
  ``threading.Thread``: the mouth fence's ``threads_settled`` waits for it.
- ``test_a_raw_thread_left_running`` starts it with ``_thread``, which the fence cannot
  see: the control, which shows what an unsettled thread does to the next test.
"""
import _thread
import os
import threading
import time
from pathlib import Path

from mouth import notice


def _late_drop() -> None:
    try:
        time.sleep(1.0)
        notice.recipient({"utt": "u1", "agent": "ivy"})
    finally:
        Path(os.environ["CHILD_LATE_DONE"]).touch()


def test_a_player_left_running():
    t = threading.Thread(target=_late_drop, name="late-drop")
    t.start()
    t.join(0.05)                    # a loaded host's join that timed out
    assert t.is_alive()


def test_a_raw_thread_left_running():
    _thread.start_new_thread(_late_drop, ())
