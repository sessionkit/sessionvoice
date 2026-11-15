"""Shared waits for the talk tests: a reader of /events and a raw-socket read, each
bounded by a deadline over the whole call, never by a fixed socket timeout alone.

A test waits on what it waits for: the stream's own events, a marker in the bytes, a
whole response, or the server hanging up. A fixed socket timeout is the wrong bound for
talk in both directions:

- set short, it is a speed bound: a loaded host that answers late reds a correct server;
- set long, on /events it never fires: the server's keep-alive (server.KEEPALIVE, 15s)
  is a line that resets a socket timeout, so a broken stream hangs instead of going red.

HANG_CEILING is a HANG CEILING, not a speed bound. It turns a broken connection into a
red instead of a hang, and it is generous so a loaded host never reaches it.
"""
import http.client, json, socket, time

from talk import server, token_store

HANG_CEILING = 30


def ceiling_or_default(ceiling):
    """The ceiling a wait uses: the one asked for, else HANG_CEILING read at call time
    (so a test can lower it with monkeypatch to prove a broken stream reds)."""
    return HANG_CEILING if ceiling is None else ceiling


def recv_until(s, done=None, ceiling=None):
    """Read socket `s` until the peer closes it or done(data) is true; (data, closed).

    The deadline covers the whole call: each recv may wait only for what is left of it,
    so bytes that keep arriving (keep-alives) cannot hold the wait open past it. At the
    deadline it raises TimeoutError naming the tail of what did arrive."""
    ceiling = ceiling_or_default(ceiling)
    deadline = time.monotonic() + ceiling
    data = b""
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError(f"still waiting after {ceiling}s; the tail was {data[-200:]!r}")
        s.settimeout(left)
        try:
            chunk = s.recv(65536)
        except socket.timeout:
            raise TimeoutError(f"still waiting after {ceiling}s; the tail was {data[-200:]!r}") from None
        if not chunk:
            return data, True
        data += chunk
        if done is not None and done(data):
            return data, False


def log_until(capsys, done, ceiling=None):
    """What the server has written to stderr, read until done(err) is true; returns err.

    The server logs from its handler threads, so a test waits for the lines it expects
    instead of sleeping a fixed spell: a late line on a loaded host only waits longer, and
    one that never comes reds at the ceiling, naming what did arrive."""
    ceiling = ceiling_or_default(ceiling)
    deadline = time.monotonic() + ceiling
    err = ""
    while True:
        err += capsys.readouterr().err
        if done(err):
            return err
        if time.monotonic() >= deadline:
            raise TimeoutError(f"the log still lacked what was awaited after {ceiling}s; "
                               f"the tail was {err[-400:]!r}")
        time.sleep(0.02)


def requests_logged(n):
    """done() for log_until: the server has logged n requests (one line each)."""
    return lambda err: err.count(' HTTP/1.1" ') >= n


class SSE:
    """A minimal reader of /events: next() returns (event name or None, data).

    next() waits on the stream's own events (config, backfill, then the first "state",
    sent once the tail is positioned), so a slow host only makes it slower; a deadline
    over the whole call turns a stream sending only keep-alives into a red."""

    def __init__(self, port, query="n=150"):
        self.c = http.client.HTTPConnection("127.0.0.1", port, timeout=HANG_CEILING)
        self.c.request("GET", "/events?" + query,
                       headers={"Cookie": f"{server.COOKIE}={token_store.load()}"})
        self.r = self.c.getresponse()
        assert self.r.status == 200

    def next(self):
        name, data = None, None
        deadline = time.monotonic() + HANG_CEILING
        while True:
            if time.monotonic() > deadline:              # only keep-alives for HANG_CEILING
                raise TimeoutError(f"no event on /events in {HANG_CEILING}s")
            line = self.r.fp.readline().decode()
            if not line:
                raise EOFError
            line = line.rstrip("\n")
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
            elif line == "" and data is not None:
                return name, data

    def close(self):
        self.c.close()


def live_row(sse, write):
    """After backfill: wait for the tail, write one row, return the next row event.

    The server sends "backfill", THEN seeks today's log to its end, THEN loops, and its
    first "state" goes out only from inside that loop. A row written before the seek
    lies behind the tail and is never streamed, however long the reader waits; so the
    first "state" is the readiness signal, and the row is written only after it."""
    name, _ = sse.next()
    assert name == "state"                                       # the tail is positioned
    write()
    name, row = sse.next()
    while name == "state":
        name, row = sse.next()
    return name, row
