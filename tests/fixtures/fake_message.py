"""A fake ``message send`` for keeper/ears-mail.py's tests (EARS-381). Stdlib only.

Called as ``fake_message.py send TO -s SUBJ -f - --raw --no-shorten --json
[--header 'K: V' ...]`` with the body on stdin, as ears-mail calls sessionmail's
``message``. It delivers nothing. It appends one JSON line per call to
``$EARS_FAKE_MESSAGE_LOG``: ``{"argv", "env", "body"}``, where ``env`` is the
seat, its name and the signing variables it saw (and any ``CLAUDE_SESSION_*``),
so a test can pin who a turn would have gone out as, and the ``id`` it answers
with. Then it prints ``--json``'s
one object, as the real one does:

- ``{"sent": "yes", "id": "<N.fake@host>"}``, exit 0;
- a TO in ``$EARS_FAKE_MESSAGE_DEAD`` (a comma list): ``sent: no``, kind
  ``address``, exit 3, as one dead box refuses the real send;
- a subject the real one refuses (not ASCII, or a backtick, ``$(`` or a line
  break, or past 69 characters): kind ``subject``, exit 2;
- no seat in the env (``TM_AGENT_ADDRESS``): kind ``identity``, exit 2.

With no ``$EARS_FAKE_MESSAGE_LOG`` it writes nothing and exits 75 (EX_TEMPFAIL),
so a fake started outside a test is harmless.
"""
import json
import os
import sys
import time

SEEN = ("TM_AGENT_ADDRESS", "EMAIL", "SESSIONMAIL_FROM_NAME",
        "SESSIONMAIL_REQUIRE_SIGNATURE", "SESSION_MAIL_REQUIRE_SIGNATURE",
        "CLAUDE_CODE_SESSION_ID")


def answer(obj, code):
    print(json.dumps(obj))
    sys.exit(code)


body = sys.stdin.buffer.read()
log = os.environ.get("EARS_FAKE_MESSAGE_LOG")
if not log:
    sys.exit(75)
argv = sys.argv[1:]
env = {k: os.environ[k] for k in os.environ if k in SEEN or k.startswith("CLAUDE_SESSION_")}
mid = f"<{time.time_ns()}.{os.getpid()}.fake@{argv[1].rpartition('@')[2] if len(argv) > 1 else ''}>"
with open(log, "a") as f:
    f.write(json.dumps({"argv": argv, "env": env, "body": body.decode("utf-8"), "id": mid}) + "\n")

if argv[:1] != ["send"] or len(argv) < 2:
    answer({"sent": "no", "kind": "usage", "message": f"not a send: {argv!r}"}, 2)
to = argv[1]
subject = argv[argv.index("-s") + 1] if "-s" in argv else None
if subject is None:
    answer({"sent": "no", "kind": "usage", "message": "-s SUBJECT is required"}, 2)
if not subject.isascii() or any(ord(c) < 32 for c in subject) or "`" in subject \
        or "$(" in subject or len(subject) > 69:
    answer({"sent": "no", "kind": "subject", "message": f"refused subject {subject!r}"}, 2)
if not os.environ.get("TM_AGENT_ADDRESS"):
    answer({"sent": "no", "kind": "identity", "message": "no seat in the environment"}, 2)
dead = {x.strip() for x in os.environ.get("EARS_FAKE_MESSAGE_DEAD", "").split(",") if x.strip()}
if to in dead:
    answer({"sent": "no", "kind": "address", "message": f"{to}: no such box"}, 3)
answer({"sent": "yes", "id": mid, "notices": []}, 0)
