"""The suite's default send command (EARS-164 fence T4): it writes nothing and exits 75.

``tests/conftest.py`` points ``$MOUTH_SENDMAIL`` and ``$EARS_MAIL_MESSAGE`` here
at import, so a test outside ``tests/mouth`` (whose HOME is not fenced), and an
ears-mail run without ``--dry-run``, can never mail anyone.
It ignores its argv, so it fences ``message send``'s arguments (EARS-381) as it
fenced sendmail's. 75 is EX_TEMPFAIL: the notice is recorded
``failed: message send exit 75``, never raised."""
import sys

try:
    sys.stdin.buffer.read()  # drain it, so the sender never sees a broken pipe
except Exception:  # noqa: BLE001
    pass
sys.exit(75)
