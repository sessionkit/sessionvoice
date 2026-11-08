"""A client that hangs up costs talk.log nothing.

A browser keeps its connection to talk alive between requests and later drops it
with a reset. talk's handler is then waiting for the next request line, and the
reset (ECONNRESET) used to reach socketserver's handle_error, which printed
"Exception occurred during processing of request" and a whole traceback into
talk.log, once per dropped connection. A hang-up is not an error: nothing is left
to answer. Any other exception still reaches the log as before.

Every server binds a FREE port picked by the kernel, never 8780, on 127.0.0.1
(test_talk_token_gate's fixtures: HOME and VOICEMODE_BASE_DIR are a tmp dir)."""
import socket, struct, time

from talk import server
from tests.test_talk_token_gate import req, room, talk  # noqa: F401  (fixtures)


def _keep_alive_then_reset(port):
    """One answered request on a kept-alive connection, then a reset (SO_LINGER 0), as a
    browser drops an idle connection."""
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.sendall(f"GET /manifest.json HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode())
    got = b""
    while b"\r\n\r\n" not in got:
        got += s.recv(65536)
    assert got.startswith(b"HTTP/1.1 200 ")
    s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    s.close()
    time.sleep(0.3)                    # the handler's thread meets the reset


def test_a_reset_kept_alive_connection_logs_no_traceback(talk, capsys):
    port, _ = talk
    _keep_alive_then_reset(port)
    err = capsys.readouterr().err
    assert '"GET /manifest.json HTTP/1.1" 200' in err          # the request it answered is logged
    assert "Traceback" not in err and "Exception occurred" not in err
    assert "ConnectionResetError" not in err
    assert req(port, "/")[0] == 200                             # and talk answers the next one


def test_any_other_exception_still_reaches_the_log(talk, capsys, monkeypatch):
    port, _ = talk

    def broken(self):
        raise RuntimeError("a fault in talk itself")
    monkeypatch.setattr(server.Handler, "do_GET", broken)
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.sendall(f"GET / HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode())
    s.settimeout(5)
    try:
        s.recv(65536)                  # the handler closes it without an answer
    except OSError:
        pass
    s.close()
    time.sleep(0.3)
    err = capsys.readouterr().err
    assert "Traceback" in err and "RuntimeError: a fault in talk itself" in err


def test_the_hang_ups_are_named_once():
    assert server.CLIENT_GONE == (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)
