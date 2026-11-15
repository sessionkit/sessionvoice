"""The test that runs next, for the CHILD pytest in ``tests/test_mouth_thread_settle.py``
only (the suite never collects this file). A root test, so the mouth fence is not on it:
HOME is the session's and no fake ``message`` is on PATH, as in the first moments of any
test's setup. It does nothing but wait (up to 30 s) until the thread the mouth test before
it left running has touched ``$CHILD_LATE_DONE``."""
import os
import time
from pathlib import Path


def test_the_next_test():
    done = Path(os.environ["CHILD_LATE_DONE"])
    end = time.monotonic() + 30
    while not done.exists() and time.monotonic() < end:
        time.sleep(0.05)
    assert done.exists()
