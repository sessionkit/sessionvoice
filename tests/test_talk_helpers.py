"""The talk tests' waits (tests/talk_helpers.py): recv_until, the raw-socket wait, on a
local socket pair (no server, no port), and log_until, the wait on the server's log, on
capsys. Each waits on what it waits for, so a slow peer or a late log line only makes it
slower, and its deadline covers the whole call, so a peer that sends only keep-alives, or
a log line that never comes, goes red at the ceiling instead of hanging.

Nothing here bounds a speed: each peer is held back by a sleep or an event, never raced."""
import socket, sys, threading, time

import pytest

from tests import talk_helpers
from tests.talk_helpers import log_until, recv_until, requests_logged


@pytest.fixture
def pair():
    a, b = socket.socketpair()
    try:
        yield a, b
    finally:
        a.close()
        b.close()


def test_returns_when_done_holds_and_the_line_is_still_open(pair):
    a, b = pair
    b.sendall(b"event: backfill\n\n")
    data, closed = recv_until(a, lambda d: b"backfill" in d)
    assert data == b"event: backfill\n\n" and not closed


def test_returns_at_eof_with_closed_true(pair):
    a, b = pair
    b.sendall(b"HTTP/1.1 204 No Content\r\n\r\n")
    b.shutdown(socket.SHUT_WR)
    data, closed = recv_until(a)
    assert data == b"HTTP/1.1 204 No Content\r\n\r\n" and closed


def test_a_late_answer_is_waited_for_where_a_fixed_socket_timeout_reds(pair):
    """The stall a loaded host makes: the answer comes after a fixed per-read timeout
    would have fired. Held open on purpose, so the old shape reds every time."""
    a, b = pair
    late = threading.Timer(0.3, b.sendall, args=(b"the answer",))
    late.start()
    try:
        a.settimeout(0.1)                    # the old shape: a fixed timeout on one read
        with pytest.raises(socket.timeout):
            a.recv(65536)
        data, closed = recv_until(a, lambda d: d == b"the answer")
        assert data == b"the answer" and not closed
    finally:
        late.cancel()


def test_keepalives_cannot_hold_the_wait_past_its_ceiling(pair, monkeypatch):
    """A stream that sends only keep-alives, faster than any per-read timeout: each one
    would reset a socket timeout, so only a deadline over the whole call ends the wait.
    The default ceiling is read at call time, so lowering it here is what the test runs."""
    a, b = pair
    monkeypatch.setattr(talk_helpers, "HANG_CEILING", 0.5)
    stop = threading.Event()

    def keepalives():
        give_up = time.monotonic() + 20      # if the wait never ends, EOF ends it: a red, not a hang
        while not stop.is_set() and time.monotonic() < give_up:
            b.sendall(b": ping\n\n")
            stop.wait(0.05)
        b.shutdown(socket.SHUT_WR)

    t = threading.Thread(target=keepalives, daemon=True)
    t.start()
    try:
        t0 = time.monotonic()
        with pytest.raises(TimeoutError, match=r"still waiting after 0.5s; the tail was .*: ping"):
            recv_until(a, lambda d: b"event:" in d)
        assert time.monotonic() - t0 >= 0.5  # the deadline was honoured, not cut short
    finally:
        stop.set()
        t.join(5)


# -- log_until: the server's log, waited for line by line -------------------------

def test_log_until_waits_for_a_late_line(capsys):
    """A handler thread that logs after a fixed sleep would have read: waited for."""
    late = threading.Timer(0.3, lambda: sys.stderr.write('"GET / HTTP/1.1" 200 -\n'))
    late.start()
    try:
        err = log_until(capsys, requests_logged(1))
        assert '"GET / HTTP/1.1" 200 -' in err
    finally:
        late.cancel()


def test_log_until_keeps_what_it_read_before_the_line_came(capsys):
    sys.stderr.write('"GET /a HTTP/1.1" 200 -\n')
    late = threading.Timer(0.2, lambda: sys.stderr.write('"GET /b HTTP/1.1" 403 refused: x\n'))
    late.start()
    try:
        err = log_until(capsys, requests_logged(2))
        assert "/a HTTP" in err and "/b HTTP" in err
    finally:
        late.cancel()


def test_a_line_that_never_comes_reds_at_the_ceiling(capsys, monkeypatch):
    monkeypatch.setattr(talk_helpers, "HANG_CEILING", 0.3)
    sys.stderr.write('"GET / HTTP/1.1" 200 -\n')
    t0 = time.monotonic()
    with pytest.raises(TimeoutError, match=r"after 0.3s; the tail was .*GET / HTTP"):
        log_until(capsys, requests_logged(2))
    assert time.monotonic() - t0 >= 0.3
