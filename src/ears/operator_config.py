"""operator_config - who the operator is, and which seats are privileged.

The one reader of the host's operator block. The block is
sessiontools' setting, ``~/.sessionkit/tools/settings/session.json``, and it is read ONLY
through ``session operator`` (its frozen contract), which prints::

    {"operator": "alice@host.example", "mailbox": "alice", "privileged": ["helper", "alice"]}

This module never imports sessiontools and never opens session.json: it runs the
CLI and parses its stdout. The mouth's ``allowed()`` and ``barge_in()`` and the
keeper's notice address all ask here, so there is one parser.

Rules:

* ``MOUTH_SESSION_BIN`` (or its old name, ``SESSION_BIN_ENV_OLD``) set AT ALL
  (``""`` or a path) is the off switch:
  the answer is NOTHING. Only when it is unset is ``session`` looked up on PATH. A
  path in it is NOT honoured here (``voices.cascade`` honours one for voices): an
  env var must not choose where privilege comes from. ``tests/conftest.py`` sets it
  to ``""``, so no test reads this host's block by accident.
* Every failure reads as NOTHING, never as an error: no ``session``, OSError, a
  timeout, a non-zero exit, stdout that is not JSON or not a JSON object.
* A bad answer can only REMOVE privilege: unknown keys are ignored, a
  ``privileged`` that is not a list is empty, non-strings and empties are dropped.
* "Configured" means ``privileged`` is non-empty, nothing else. sessiontools puts
  the operator into ``privileged`` whenever the settings name one, so
  ``AGENT_OPERATOR_ADDR`` on its own (which moves ``operator`` only) is
  unconfigured: an env var cannot grant privilege or switch on narrowing.
* ``current()`` caches the answer for ``TTL_S`` (failures too), so a hanging
  ``session`` costs at most ``TIMEOUT_S`` a minute. The cache is one immutable
  tuple swapped whole: two threads racing on an expired stamp just read twice.

``python -m ears.operator_config address|mailbox|json`` prints one value (or an
empty line) and always exits 0; the keeper uses ``address``.

Standard library only; ``subprocess`` and ``shutil`` are imported where used, so
``import mouth`` stays cheap.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import NamedTuple, Optional

#: Set at all (even to "") = do not read the operator block (shared with voices.cascade).
SESSION_BIN_ENV = "MOUTH_SESSION_BIN"
#: Its old name, still an off switch. Kept here, not read through
#: ``ears.envnames``: this module imports only the standard library.
#: ``tests/test_envnames.py`` holds the pair to the table.
SESSION_BIN_ENV_OLD = "VOICEMODE_MOUTH_SESSION_BIN"  # old name of "MOUTH_SESSION_BIN"

#: How long ``current()`` trusts an answer, and how long one ``session operator`` may take.
TTL_S = 60.0
TIMEOUT_S = 2.0

#: Why an answer is NOTHING (or not configured). Only the first three are a fallback.
OFF, ABSENT, FAILED, UNCONFIGURED = "off", "absent", "failed", "unconfigured"
_FALLBACK = (OFF, ABSENT, FAILED)

_REASON_TEXT = {
    OFF: f"off (${SESSION_BIN_ENV} is set)",
    ABSENT: "missing (no session on PATH)",
    FAILED: "failing (exit, timeout or bad output)",
}

#: The fallback warning (in the ``NO_HEARD_BOX`` style): fill ``{reason}`` with ``reason_text()``.
ALLOW_FALLBACK = (
    "mouth: the operator reader is {reason}, so $MOUTH_MAIL_ALLOW still ADDS "
    "senders. If this host has an operator block, its privileged seats are not read "
    "and ALLOW does not narrow."
)


class Operator(NamedTuple):
    operator: Optional[str]      # the ask address (AGENT_OPERATOR_ADDR may move it)
    mailbox: Optional[str]       # settings only, lowercased, stripped
    privileged: frozenset        # settings only, lowercased, stripped, empties dropped


NOTHING = Operator(None, None, frozenset())

# (answer, its reason, monotonic stamp): one tuple, swapped whole.
_cache: Optional[tuple[Operator, Optional[str], float]] = None
# Why the last answer current() (or read()) gave was what it was; beside the
# cached tuple, never inside the Operator.
_reason: Optional[str] = None


def _off() -> bool:
    # either name, set at all (presence): the new one empty is still the switch
    return any(n in os.environ for n in (SESSION_BIN_ENV, SESSION_BIN_ENV_OLD))


def _text(v) -> Optional[str]:
    return v.strip() if isinstance(v, str) and v.strip() else None


def parse(data) -> Operator:
    """An Operator from ``session operator``'s decoded JSON; anything odd is dropped."""
    if not isinstance(data, dict):
        return NOTHING
    operator = _text(data.get("operator"))
    mailbox = _text(data.get("mailbox"))
    raw = data.get("privileged")
    priv = frozenset(x.strip().lower() for x in raw if isinstance(x, str) and x.strip()) \
        if isinstance(raw, list) else frozenset()
    return Operator(operator, mailbox.lower() if mailbox else None, priv)


def _read(timeout: float) -> tuple[Operator, Optional[str]]:
    """(answer, reason): reason is off/absent/failed/unconfigured, or None when configured."""
    if _off():
        return NOTHING, OFF
    import shutil
    import subprocess  # here, not at the top: importing mouth stays cheap

    exe = shutil.which("session")
    if not exe:
        return NOTHING, ABSENT
    try:
        r = subprocess.run([exe, "operator"], capture_output=True, text=True,
                           timeout=timeout, check=False)
        if r.returncode != 0:
            return NOTHING, FAILED
        data = json.loads(r.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return NOTHING, FAILED
    if not isinstance(data, dict):
        return NOTHING, FAILED
    op = parse(data)
    return op, (None if op.privileged else UNCONFIGURED)


def read(timeout: float = TIMEOUT_S) -> Operator:
    """One ``session operator`` run (no cache). Never raises; every failure is NOTHING."""
    global _reason
    op, _reason = _read(timeout)
    return op


def current() -> Operator:
    """``read()``, cached for ``TTL_S``. The off switch is checked first, on every call."""
    global _cache, _reason
    if _off():
        _reason = OFF
        return NOTHING
    c = _cache
    now = time.monotonic()
    if c is None or now - c[2] >= TTL_S:
        op, why = _read(TIMEOUT_S)
        c = (op, why, now)
        _cache = c
    _reason = c[1]
    return c[0]


def fallback_reason() -> Optional[str]:
    """``off``, ``absent`` or ``failed`` when the last answer was NOTHING for that
    reason, else None (``unconfigured`` and a configured answer never warn).
    Call ``current()`` first: this reports the answer it last gave."""
    r = _reason
    return r if r in _FALLBACK else None


def reason_text(code: Optional[str]) -> str:
    """The words ``ALLOW_FALLBACK`` puts in for a reason code ("" for None)."""
    return _REASON_TEXT.get(code, "") if code else ""


def clear() -> None:
    """Forget the cached answer and its reason (for tests)."""
    global _cache, _reason
    _cache = None
    _reason = None


def is_privileged(sender: str, host: str) -> bool:
    """Is ``sender`` one of ``current()``'s privileged seats, on this ``host``?

    An entry with an ``@`` matches that address exactly (case-insensitive). A bare
    entry is a mailbox on THIS host only: ``helper`` matches ``helper@<host>`` and
    ``helper@<host>.<anything>``, never ``helper@<another host>``.
    """
    s = (sender or "").strip().lower()
    h = (host or "").strip().lower()
    if "@" not in s:
        return False
    for p in current().privileged:
        if "@" in p:
            if s == p:
                return True
        elif h and (s == f"{p}@{h}" or s.startswith(f"{p}@{h}.")):
            return True
    return False


def address() -> str:
    """The operator's ask address for notices, or "" when nothing is configured.

    Only a configured block (``privileged`` non-empty) gives one, so
    ``AGENT_OPERATOR_ADDR`` alone gives "": the keeper mails nobody and
    ``ears services install`` says its notices are log only."""
    op = current()
    return (op.operator or "") if op.privileged else ""


def _value(what: str) -> str:
    op = current()
    if what == "address":           # only when configured: AGENT_OPERATOR_ADDR alone gives nobody
        return address()
    if what == "mailbox":
        return op.mailbox or ""
    if what == "json":
        return json.dumps({"operator": op.operator, "mailbox": op.mailbox,
                           "privileged": sorted(op.privileged)})
    raise ValueError(what)


def main(argv: Optional[list] = None) -> int:
    """``address|mailbox|json``: print it (or an empty line). Always 0."""
    args = sys.argv[1:] if argv is None else argv
    try:
        out = _value(args[0] if len(args) == 1 else "")
    except ValueError:
        print("usage: python -m ears.operator_config address|mailbox|json", file=sys.stderr)
        out = ""
    except Exception as e:  # noqa: BLE001 - never fail the keeper's start
        print(f"operator_config: {e}", file=sys.stderr)
        out = ""
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
