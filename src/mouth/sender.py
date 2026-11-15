"""Who a line is from: the From ``mouth say`` and ``mouth play`` write.

A say or a play is mail to the mouth's box, and it is From whoever ran it,
read from the environment the way sessionmail's ``message send`` reads it.
There is no ``--from``. sessionmail is not a dependency of sessionvoice, so
this mirrors its rule rather than importing it (sessionmail ``_seat.py``,
``resolve`` and ``declared_address``):

1. The identity is the first non-empty (after ``strip``) of
   ``TM_AGENT_ADDRESS``, ``CLAUDE_CODE_AGENT`` (``SEAT_VARS``), then
   ``CLAUDE_CODE_SESSION_ID``, ``CLAUDE_SESSION_ID`` (``SESSION_VARS``,
   sessiontools' chain). An empty variable counts as unset.
2. With an identity, the address is the first of ``EMAIL``,
   ``TM_AGENT_ADDRESS`` (``ADDRESS_VARS``) that holds a PLUS address
   (``<base>+<ext>``) on this host, folded. If neither holds one, the
   identity's own value is folded. A bare ``EMAIL=alice@...`` from a
   person's shell declares nothing.
3. With no identity, ``message send`` refuses (``NoIdentity``). The mouth
   never refuses a say: a plain shell's line is From the person's own login
   name (``getpass.getuser()``), folded. When even that is empty it is
   ``mouth``, the From every line had before.

Folding keeps the local part only: lower case, ``+`` kept, any run outside
``[a-z0-9._+-]`` made one ``-``. The domain is always this host's short
name, as the mouth's own ``To: mouth@<host>`` is. So every From the mouth
writes is on this host, and ``box.allowed()`` admits it whatever the trust
mode. ``VOICEMODE_AGENT`` is a voice name, not an address: it never picks
the From. It still rides in ``X-Mouth-Agent``.
"""

from __future__ import annotations

import getpass
import os
import re
from typing import Mapping, Optional

#: sessionmail ``_seat.py`` ``SEAT_VARS``, then sessiontools' ``SESSION_ID_VARS``:
#: together ``IDENTITY_VARS``, in this order.
SEAT_VARS = ("TM_AGENT_ADDRESS", "CLAUDE_CODE_AGENT")
SESSION_VARS = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID")
IDENTITY_VARS = SEAT_VARS + SESSION_VARS
#: sessionmail ``_seat.py`` ``ADDRESS_VARS``: where a seat declares its plus address.
ADDRESS_VARS = ("EMAIL", "TM_AGENT_ADDRESS")
#: The From local part when nothing else answers: the mouth's own, as before.
FALLBACK = "mouth"

_UNSAFE = re.compile(r"[^a-z0-9._-]+")            # sessionmail _seat._UNSAFE
_UNSAFE_ADDRESS = re.compile(r"[^a-z0-9._+-]+")   # sessionmail _seat._UNSAFE_ADDRESS


def _host() -> str:
    """This host's short name: the mouth's one rule (``box._host``), never a second copy."""
    from .box import _host as host

    return host()


def seat_name(value: str) -> str:
    """sessionmail ``seat_name``: no domain, no plus-extension, safe."""
    local = value.strip().lower().split("@", 1)[0].split("+", 1)[0]
    return _UNSAFE.sub("-", local).strip("-")


def fold(value: str) -> str:
    """sessionmail ``fold_address``: the local part, lower case, safe, ``+`` kept."""
    local = value.strip().lower().split("@", 1)[0]
    return _UNSAFE_ADDRESS.sub("-", local).strip("-")


def _plus(address: str) -> bool:
    """sessionmail ``split_plus``: a first ``+`` with both halves non-empty."""
    base, plus, ext = address.partition("+")
    return bool(plus and base and ext)


def _on_host(value: str, host: str) -> bool:
    """sessionmail ``local_domain``: no domain, or this host (``host`` or ``host.*``)."""
    _, at, domain = value.strip().lower().partition("@")
    if not at:
        return True
    return bool(host) and (domain == host or domain.startswith(host + "."))


def declared(env: Mapping[str, str], host: str) -> Optional[str]:
    """sessionmail ``declared_address``: the first of ADDRESS_VARS holding a plus
    address on this host, folded; None when none declares one."""
    for var in ADDRESS_VARS:
        value = env.get(var) or ""
        address = fold(value)
        if _plus(address) and _on_host(value, host):
            return address
    return None


def _login() -> str:
    try:
        return getpass.getuser() or ""
    except Exception:  # noqa: BLE001 - no login name (no USER, no passwd row): the fallback
        return ""


def local_part(env: Optional[Mapping[str, str]] = None, host: Optional[str] = None) -> str:
    """The From's local part for a line this process says (the module's rule)."""
    env = os.environ if env is None else env
    host = _host() if host is None else host
    for var in IDENTITY_VARS:
        value = (env.get(var) or "").strip()
        if value and seat_name(value):
            return declared(env, host) or fold(value) or seat_name(value)
    return fold(_login()) or FALLBACK


def address(env: Optional[Mapping[str, str]] = None) -> str:
    """``<local part>@<this host>``: the From ``mouth say`` and ``mouth play`` write."""
    host = _host()
    return f"{local_part(env, host)}@{host}"
