"""The DJ's mpv, over its JSON IPC socket: the music's volume, and nothing else.

The ducking client. It speaks mpv's own protocol
(https://mpv.io/manual/stable/#json-ipc) with the stdlib, so the mouth needs
no DJ package. ``SOCKET`` is where the DJ's mpv listens;
``$MOUTH_MPV_SOCKET`` names another (a test's, a private mpv's).

A dead DJ never stalls speech: every call is one short connection with a short
timeout, and no socket, a refusal, a timeout or an mpv error is just no answer
(``volume()`` is None, a set does nothing). Nothing here raises.
"""

from __future__ import annotations

import json
import os
import socket
import time
from typing import Any, Optional

from ears import envnames

SOCKET = "/tmp/voicemode-mpv.sock"
TIMEOUT_S = 0.2   # a whole call's budget: mpv answers in a few ms, a dead DJ in none


class Mpv:
    def __init__(self, path: Optional[str] = None, timeout: float = TIMEOUT_S):
        self.path = path or envnames.get("MOUTH_MPV_SOCKET") or SOCKET
        self.timeout = timeout

    def command(self, *args: Any) -> Any:
        """mpv's ``data`` for one command, or None on any failure."""
        end = time.monotonic() + self.timeout
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(self.timeout)
                s.connect(self.path)
                s.sendall(json.dumps({"command": list(args), "request_id": 1}).encode() + b"\n")
                buf = b""
                while time.monotonic() < end:
                    while b"\n" in buf:   # mpv may interleave events: skip to our reply
                        line, buf = buf.split(b"\n", 1)
                        msg = json.loads(line)
                        if msg.get("request_id") == 1 and "event" not in msg:
                            return msg.get("data") if msg.get("error") == "success" else None
                    s.settimeout(max(end - time.monotonic(), 0.001))
                    chunk = s.recv(4096)
                    if not chunk:
                        return None
                    buf += chunk
        except (OSError, ValueError, AttributeError):   # no DJ, refused, timeout, junk
            return None
        return None

    def volume(self, level: Optional[int] = None) -> Optional[int]:
        """Set the volume to ``level`` if given; the volume now (0-100), or None: no DJ."""
        if level is not None:
            self.command("set_property", "volume", level)
        v = self.command("get_property", "volume")
        return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
