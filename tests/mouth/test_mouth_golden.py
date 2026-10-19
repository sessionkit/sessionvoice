"""Golden: a said line without --in-reply-to is the mail it was (EARS-30, criterion 4).

The fixture ``fixtures/golden_say.json`` was captured at base 03e5ba1, BEFORE
any EARS-30 change to ``src/mouth``, by::

    MOUTH_GOLDEN_CAPTURE=1 python -m pytest tests/mouth/test_mouth_golden.py

Every input that varies from run to run is pinned: the clock (``time.time``
and ``time.time_ns`` as the mouth sees them), the utt (``uuid4``), the host
(``socket.gethostname``), the time zone, and the environment (every
``VOICEMODE_*`` and ``CLAUDE_*`` variable cleared, then the few the mouth
reads set to fixed values). Each case's mail is compared as its ordered
header list (name, value) and its body. Never re-capture to make this pass:
a difference is a change to the mail that every say writes.

Since EARS-335 every drop's FIRST line is ``X-Mouth-Local: v1 <hex MAC>``, keyed by
the box's random key. ``_mail`` pins that line's shape and place, never its value,
and compares the rest (byte for byte what the drop wrote before) with the fixture.
"""

from __future__ import annotations

import email
import email.policy
import json
import os
import re
import time
import types
import uuid
from pathlib import Path

import pytest

import mouth
from mouth import box
from ears import envnames

FIXTURE = Path(__file__).parent / "fixtures" / "golden_say.json"
T0 = 1917302400.0             # a fixed wall clock: 2030-10-04 00:00:00 UTC
NS0 = 1917302400000000000     # time_ns at T0; each call steps 1 ms
TZ = "AEST-10"                # POSIX: UTC+10, no DST, no tzdata needed
HOST = "golden.example"       # the mouth keeps the first label: "golden"


class _Clock:
    """A stand-in for the ``time`` module as mouth and mouth.box import it."""

    def __init__(self) -> None:
        self.n = 0

    def time(self) -> float:
        return T0

    def time_ns(self) -> int:
        self.n += 1
        return NS0 + self.n * 1_000_000

    def __getattr__(self, name):  # monotonic, sleep, ...: the real ones
        return getattr(time, name)


@pytest.fixture
def pinned(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith(("VOICEMODE_", "CLAUDE_")) or k in envnames.ALIASES:   # the renamed, too
            monkeypatch.delenv(k)
    d = tmp_path / "mouth"
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "base"))
    monkeypatch.setenv("MOUTH_DIR", str(d))
    monkeypatch.setenv("MOUTH_SETTINGS", str(tmp_path / "no-settings.json"))
    monkeypatch.setenv("MOUTH_LOG_DIR", "/nonexistent/golden-logs")  # a header value; never written
    monkeypatch.setenv("MOUTH_DEVICE", "null")
    monkeypatch.setenv("MOUTH_BACKEND", "silence")
    monkeypatch.setenv("VOICEMODE_AGENT", "golden-agent")
    monkeypatch.setenv("VOICEMODE_SESSION_ID", "golden-session")
    monkeypatch.setenv("TZ", TZ)
    time.tzset()
    monkeypatch.setattr("socket.gethostname", lambda: HOST)

    def restart() -> None:
        """A fresh clock and utt sequence: the run starts from T0 again."""
        clock = _Clock()
        monkeypatch.setattr(mouth, "time", clock)
        monkeypatch.setattr(box, "time", clock)
        utts = iter(f"{i:02x}" * 16 for i in range(0xA1, 0x100))
        monkeypatch.setattr(mouth, "uuid", types.SimpleNamespace(uuid4=lambda: uuid.UUID(next(utts))))

    restart()
    yield types.SimpleNamespace(d=d, restart=restart)
    monkeypatch.undo()
    time.tzset()


def _mail(path: Path) -> dict:
    first, rest = path.read_bytes().split(b"\n", 1)
    assert re.fullmatch(rb"X-Mouth-Local: v1 [0-9a-f]{64}", first), f"not a local drop: {first[:80]!r}"
    m = email.message_from_bytes(rest, policy=email.policy.default)
    return {"headers": [[k, str(v)] for k, v in m.items()],
            "body": m.get_body(preferencelist=("plain",)).get_content()}


def _new(d: Path) -> list:
    return sorted((d / "box" / "new").iterdir())


def _cases(d: Path) -> dict:
    out = {}
    plain = mouth.say("Hello, golden line.", d=d, spawn=False)
    (f,) = _new(d)
    out["say"] = _mail(f)
    mouth.amend(plain["utt"], "Hello, amended golden line.", d=d)
    out["amend"] = _mail(next(p for p in _new(d) if p != f))
    for p in _new(d):
        p.unlink()
    mouth.say("Every option, golden.", voice="ivy", speed=1.25, pan="left", priority=20,
              hold="turn-end", expires_s=30, interest="high", d=d, spawn=False)
    (f,) = _new(d)
    out["say_options"] = _mail(f)
    return out


def test_golden_say_and_amend(pinned):
    got = _cases(pinned.d)
    if os.environ.get("MOUTH_GOLDEN_CAPTURE") == "1":
        FIXTURE.parent.mkdir(exist_ok=True)
        FIXTURE.write_text(json.dumps(got, indent=1, ensure_ascii=False) + "\n")
        pytest.skip(f"captured {FIXTURE.name}")
    want = json.loads(FIXTURE.read_text())
    assert set(got) == set(want)
    for case in want:
        assert got[case]["headers"] == want[case]["headers"], f"{case}: the header list changed"
        assert got[case]["body"] == want[case]["body"], f"{case}: the body changed"


def test_golden_pins_hold(pinned):
    """The pins really pin: two runs write the same mails."""
    first = _cases(pinned.d)
    for p in _new(pinned.d):
        p.unlink()
    pinned.restart()
    assert _cases(pinned.d) == first
