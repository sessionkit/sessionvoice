"""A fake ``message send`` for the mouth tests (EARS-164 fence T2, EARS-381). Stdlib only.

The file keeps its old name: ``$MOUTH_SENDMAIL`` (the command notices go through)
points here, and the ``MOUTH_FAKE_*`` names are in the fence tables.

Called as ``fake_sendmail.py send TO -s SUBJ -f - --raw --no-shorten --json
[--message-id ID] [-r ID] [--header 'K: V' ...]`` with the body on stdin, as
``mouth.notice`` calls sessionmail's ``message``. It BUILDS the mail as a
delivery would (From ``<$SESSIONMAIL_FROM_NAME> <$TM_AGENT_ADDRESS@$SESSIONMAIL_HOST>``,
To, Date, Subject, Message-ID, In-Reply-To and References from ``-r``, every
``--header``), writes it into ``$MOUTH_FAKE_MAIL_ROOT/<TO's local part>/new/``
(``tmp/``, then one rename), and prints ``--json``'s one object. Each call
appends ``{"argv": [...], "env": {...}}`` to ``$MOUTH_FAKE_MAIL_ROOT/.sendmail.jsonl``:
the env is the seat and signing variables it saw, so a test can pin who a
notice would have gone out as (rca-001 row 1i).

It refuses what the real one refuses (exit 2, ``sent: no`` and a ``kind``): a
subject that is not ASCII, could execute (a backtick, ``$(`` or a line break) or
runs past 69 characters (``subject``); a ``--message-id`` not at
``$SESSIONMAIL_HOST`` (``message-id``); a recipient it cannot deliver to (``address``).

- No ``$MOUTH_FAKE_MAIL_ROOT``: it writes nothing and exits 75 (EX_TEMPFAIL), so a
  fake started outside the fence is harmless.
- ``$MOUTH_FAKE_SENDMAIL_SLEEP`` (seconds): sleep first, as a stalled MTA.
- ``$MOUTH_FAKE_SENDMAIL_EXIT`` (a code): fail with it, kind ``mta``, writing nothing.
"""
import email.message
import email.utils
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from maildir_put import put  # noqa: E402  (tests/mouth: tmp/, then one rename into new/)

#: The variables the log keeps: the seat, its name, the host, signing.
SEEN = ("TM_AGENT_ADDRESS", "EMAIL", "SESSIONMAIL_FROM_NAME", "SESSIONMAIL_HOST",
        "SESSIONMAIL_REQUIRE_SIGNATURE", "SESSION_MAIL_REQUIRE_SIGNATURE",
        "CLAUDE_CODE_SESSION_ID")


def answer(obj: dict, code: int) -> None:
    print(json.dumps(obj))
    sys.exit(code)


def refuse(kind: str, message: str) -> None:
    answer({"sent": "no", "kind": kind, "message": message}, 2)


data = sys.stdin.buffer.read()
root = os.environ.get("MOUTH_FAKE_MAIL_ROOT")
if not root:
    sys.exit(75)
nap = float(os.environ.get("MOUTH_FAKE_SENDMAIL_SLEEP") or 0)
if nap:
    time.sleep(nap)
argv = sys.argv[1:]
seen = {k: os.environ[k] for k in os.environ if k in SEEN or k.startswith("CLAUDE_SESSION_")}
with open(os.path.join(root, ".sendmail.jsonl"), "a") as log:
    log.write(json.dumps({"argv": argv, "env": seen}) + "\n")
code = int(os.environ.get("MOUTH_FAKE_SENDMAIL_EXIT") or 0)
if code:
    answer({"sent": "no", "kind": "mta", "message": "fake message: told to fail"}, code)

if argv[:1] != ["send"] or len(argv) < 2:
    refuse("usage", f"fake message: not a send: {argv!r}")
to, rest = argv[1], argv[2:]
opts: dict = {"--header": []}
while rest:
    flag = rest.pop(0)
    if flag in ("--raw", "--no-shorten", "--json"):
        opts[flag] = True
    elif flag in ("-s", "-f", "--message-id", "-r", "--header") and rest:
        v = rest.pop(0)
        opts[flag].append(v) if flag == "--header" else opts.__setitem__(flag, v)
    else:
        refuse("usage", f"fake message: unknown flag {flag!r}")

subject = opts.get("-s")
if subject is None or opts.get("-f") != "-":
    refuse("usage", "fake message: -s SUBJECT and -f - are required")
if any(ord(c) > 126 or ord(c) < 32 for c in subject):
    refuse("subject", f"the subject is not ASCII ({subject!r})")
if "`" in subject or "$(" in subject:
    refuse("subject", "the subject could execute (a backtick, $( or a line break)")
if len(subject) > 69:
    refuse("subject", f"the subject is {len(subject)} characters; the limit is 69")
host = os.environ.get("SESSIONMAIL_HOST") or ""
mid = opts.get("--message-id")
if mid is not None:
    right = mid.strip("<>").rpartition("@")[2]
    if not host or right.lower() != host.lower():
        refuse("message-id", f"its right-hand side is {right!r}, not this host {host!r}")
local = to.split("@", 1)[0]
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", local):
    refuse("address", f"fake message: {to}: user unknown")
seat = os.environ.get("TM_AGENT_ADDRESS")
if not seat:
    refuse("identity", "no seat in the environment to send From")

m = email.message.EmailMessage()
name = os.environ.get("SESSIONMAIL_FROM_NAME")
m["From"] = f"{name} <{seat}@{host}>" if name else f"{seat}@{host}"
m["To"] = to
m["Date"] = email.utils.formatdate(localtime=True)
m["Subject"] = subject
m["Message-ID"] = mid or f"<{time.time_ns()}.fake@{host}>"
if opts.get("-r"):
    m["In-Reply-To"] = opts["-r"]
    m["References"] = opts["-r"]
for h in opts["--header"]:
    k, _, v = h.partition(":")
    m[k.strip()] = v.strip()
m.set_content(data.decode("utf-8"))
put(os.path.join(root, local), f"{time.time_ns()}.{os.getpid()}.fake", bytes(m))
answer({"sent": "yes", "id": m["Message-ID"], "notices": []}, 0)
