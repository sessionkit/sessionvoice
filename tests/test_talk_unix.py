"""Socket mode: `talk serve --unix [PATH]` binds a unix socket and no TCP port (EARS-338, S6).

It closes T7 on a proxied path: with no TCP port there is nothing for another local
user to squat. The socket is 0600 in a 0700 directory; each connection's peer uid must
be talk's own or the proxy's (--proxy-uid, default 0), else a 403 before its request is
read. The proxy's portless `Host: localhost` is answered, and pairing there is not
refused (the browser's cookie belongs to the proxy's name). The token gate is unchanged.

Every test chdirs into its own tmp dir and binds a RELATIVE socket name, because a unix
socket path holds 104 bytes (design M4) and macOS tmp paths are long. HOME and
VOICEMODE_BASE_DIR are fenced. No TCP port, no tailscale serve, no launchctl.
"""
import json, os, socket, stat, struct, threading
from datetime import date
from unittest import mock

import pytest

from talk import cli, server, token_store
from tests._anchor import ANCHOR

SECRET = "the words the microphone heard"
SOCK = "t.sock"
TS = "talk.example.ts.net"


@pytest.fixture
def room(tmp_path, monkeypatch):
    """cwd = a fresh 0700 dir; a fenced install (relative base dir, so its default socket path
    is short) with a heard log and the token. Yields the token."""
    d = tmp_path / "s"
    d.mkdir(mode=0o700)
    monkeypatch.chdir(d)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VOICEMODE_BASE_DIR", "vm")
    monkeypatch.delenv("EARS_HOME", raising=False)
    monkeypatch.delenv("TALK_UNIX_SOCKET", raising=False)
    logs = d / "vm" / "logs" / "conversations"
    logs.mkdir(parents=True)
    (logs / f"heard_{date.today():%Y-%m-%d}.jsonl").write_text(
        json.dumps({"ts": f"{ANCHOR:%Y-%m-%dT%H:%M:%S}", "seq": 1, "kind": "turn", "text": SECRET}) + "\n")
    return token_store.load_or_create()


@pytest.fixture
def unix(room):
    """start(path=SOCK, allow=None, proxy_uid=0) -> the running UnixHTTPServer."""
    servers = []

    def start(path=SOCK, allow=None, proxy_uid=0):
        server.configure(allow_hosts=allow)
        srv = server.make_unix_server(path, proxy_uid)
        threading.Thread(target=srv.serve_forever, args=(0.05,), daemon=True).start()
        servers.append(srv)
        return srv

    try:
        yield start
    finally:
        for srv in servers:
            srv.shutdown()
            srv.server_close()
        server.configure()


def over(path, request, until=None):
    """Send raw bytes over the unix socket; read to EOF, or until `until` appears.
    A refused peer is answered and closed before its request is read, so the send can
    meet a closed socket (EPIPE); the 403 is already in the receive buffer (EARS-373)."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(5)
        s.connect(path)
        try:
            s.sendall(request)
        except (BrokenPipeError, ConnectionResetError):
            pass
        data = b""
        while True:
            try:
                chunk = s.recv(65536)
            except socket.timeout:
                break
            if not chunk:
                break
            data += chunk
            if until and until in data:
                break
            head, sep, body = data.partition(b"\r\n\r\n")
            length = [ln.split(b":", 1)[1] for ln in head.split(b"\r\n") if ln.lower().startswith(b"content-length:")]
            if sep and length and len(body) >= int(length[0]):
                break
            if sep and head.split(b" ", 2)[1:2] == [b"204"]:
                break
    head, _, body = data.partition(b"\r\n\r\n")
    status, *hdrs = head.decode("latin-1").split("\r\n")
    hd = {}
    for h in hdrs:
        k, _, v = h.partition(":")
        hd.setdefault(k.strip().lower(), []).append(v.strip())
    return int(status.split()[1]), hd, body


def get(path, url="/", host="localhost", headers=(), until=None, method="GET"):
    lines = [f"{method} {url} HTTP/1.1"] + ([f"Host: {host}"] if host is not None else [])
    lines += [f"{k}: {v}" for k, v in headers]
    if until is None and "events" in url:
        until = b"\n\n"
    return over(path, ("\r\n".join(lines) + "\r\n\r\n").encode(), until=until)


# -- over the socket: the proxy's Host, the gate unchanged --------------------------------

def test_host_localhost_gets_the_page(unix):
    unix()
    status, hd, body = get(SOCK, "/")
    assert status == 200 and body == open(os.path.join(server.HERE, "index.html"), "rb").read()
    assert get(SOCK, "/talk/manifest.json")[0] == 200


def test_events_need_the_cookie_over_the_socket(unix, room):
    unix()
    status, _, body = get(SOCK, "/events?n=1")
    assert status == 403 and body == b'talk: this browser is not paired (run "talk pair" on the host)\n'
    assert SECRET.encode() not in body
    status, hd, body = get(SOCK, "/events?n=5", headers=[("Cookie", f"talk_token={room}")], until=SECRET.encode())
    assert status == 200 and hd["content-type"] == ["text/event-stream"]
    assert body.startswith(b"event: config\n") and SECRET.encode() in body


@pytest.mark.parametrize("host", ["localhost:80", "localhost:8780", "127.0.0.1", "127.0.0.1:8780", "LOCALHOST.:1",
                                  "attacker.example", "", None])
def test_only_the_portless_localhost_or_a_configured_name(unix, host):
    unix()
    status, _, body = get(SOCK, "/", host=host)
    assert status == 403 and b"--allow-host" in body


def test_a_configured_name_and_its_origin(unix, room):
    unix(allow={TS})
    assert get(SOCK, "/", host=TS)[0] == 200
    hs = [("Origin", f"https://{TS}"), ("Cookie", f"talk_token={room}")]
    assert get(SOCK, "/events?n=1", host="localhost", headers=hs)[0] == 200
    for origin in ("https://attacker.example", "http://localhost", "http://localhost:80", "null"):
        status, _, body = get(SOCK, "/", headers=[("Origin", origin)])
        assert status == 403 and b"Origin" in body


# -- pairing over the socket: Host localhost is the proxy's (foreman add-on 1) ----------

def test_pairing_over_the_socket_at_host_localhost_sets_the_cookie(unix, room):
    unix(allow={TS})
    status, hd, body = get(SOCK, "/talk/pair", method="POST", headers=[("X-Talk-Token", room)])
    assert status == 204 and hd["set-cookie"] == [f"talk_token={room}; Max-Age=34560000; HttpOnly; SameSite=Strict"]
    status, hd, _ = get(SOCK, "/talk/pair", method="POST",
                        headers=[("X-Talk-Token", room), ("Origin", f"https://{TS}")])
    assert status == 204 and hd["set-cookie"][0].endswith("; SameSite=Strict; Secure")
    status, hd, _ = get(SOCK, "/talk/pair", method="POST", headers=[("X-Talk-Token", "wrong")])
    assert status == 403 and "set-cookie" not in hd


def test_the_tcp_listener_still_refuses_localhost_pairing(room):
    """The exemption is the socket's alone: over TCP, Host localhost stays refused (T7b)."""
    server.configure()
    srv = server.make_server("127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, args=(0.05,), daemon=True).start()
    try:
        port = srv.server_address[1]
        with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
            s.sendall(f"POST /pair HTTP/1.1\r\nHost: localhost:{port}\r\nX-Talk-Token: {room}\r\n\r\n".encode())
            data = b""
            while chunk := s.recv(65536):
                data += chunk
        assert data.startswith(b"HTTP/1.1 403") and b"cannot pair at localhost" in data
        assert b"set-cookie" not in data.lower()
    finally:
        srv.shutdown()
        srv.server_close()


# -- the peer: talk's own uid or the proxy's, else 403 before the request is read ----------

def test_the_real_peer_is_this_uid(unix):
    unix()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
        c.connect(SOCK)
        assert server.peer_uid(c) == os.geteuid()


@pytest.mark.parametrize("uid", [os.geteuid() + 1, 1, None])
def test_a_foreign_peer_gets_403_and_nothing_is_read(unix, room, uid, capsys):
    srv = unix(proxy_uid=0)
    read = []
    real = server.Handler.handle_one_request

    def recorded(self):
        # The patch is class-wide, so a handler thread another test left serving on a TCP
        # port calls it too. Only a request read by the server under test counts.
        if self.server is srv:
            read.append(self.client_address)
        return real(self)

    with mock.patch.object(server, "peer_uid", lambda conn: uid), \
            mock.patch.object(server.Handler, "handle_one_request", recorded):
        status, hd, body = get(SOCK, "/events?n=5", headers=[("Cookie", f"talk_token={room}")])
    assert status == 403 and hd["connection"] == ["close"]
    assert body.startswith(b"talk: a connection from ") and b"is not allowed on this socket" in body
    assert SECRET.encode() not in body and b"event:" not in body
    assert read == []                                  # refused before the request line was read
    assert "socket peer refused" in capsys.readouterr().err


@pytest.mark.parametrize("uid, proxy", [(0, 0), (4242, 4242)])
def test_the_proxy_uid_may_connect(unix, uid, proxy):
    unix(proxy_uid=proxy)
    with mock.patch.object(server, "peer_uid", lambda conn: uid):
        assert get(SOCK, "/")[0] == 200


def test_another_proxy_uid_is_refused(unix):
    unix(proxy_uid=4242)
    with mock.patch.object(server, "peer_uid", lambda conn: 0):     # root, but root is not the proxy here
        assert get(SOCK, "/")[0] == 403


class Wire:
    """A handler's wfile that records each write into a shared list of events."""
    def __init__(self, events, fail=None):
        self.events, self.fail = events, fail

    def write(self, b):
        if self.fail:
            raise self.fail
        self.events.append(("write", bytes(b)))
        return len(b)

    def flush(self):
        pass


def bare_handler(events, fail=None):
    """A Handler with no socket: its writes and its log lines land, in order, in `events`."""
    h = server.Handler.__new__(server.Handler)
    h.wfile = Wire(events, fail)
    h.server = type("Srv", (), {"talk_peer_uids": {0, 501}})()
    h.log_message = lambda fmt, *args: events.append(("log", fmt % args))
    return h


@pytest.mark.parametrize("uid", [1, None])
def test_the_refused_peer_is_logged_before_its_403_is_written(uid):
    """EARS-373: the log line is written before the first byte of the 403, so a client that
    has its 403 can read the line. Logged after, a test reading stderr raced the thread."""
    events = []
    bare_handler(events).refuse_peer(uid)
    assert [kind for kind, _ in events][0] == "log"
    assert events[0][1] == f"socket peer refused: {'an unknown uid' if uid is None else f'uid {uid}'} (allowed: uid 0, 501)"
    assert b"".join(b for kind, b in events if kind == "write").startswith(b"HTTP/1.1 403 Forbidden\r\n")


def test_a_peer_that_hung_up_is_still_logged():
    events = []
    bare_handler(events, fail=BrokenPipeError(32, "Broken pipe")).refuse_peer(1)    # swallowed, as before
    assert events == [("log", "socket peer refused: uid 1 (allowed: uid 0, 501)")]


class FakeConn:
    def __init__(self, raw=None, err=None):
        self.raw, self.err, self.asked = raw, err, []

    def getsockopt(self, level, opt, size):
        self.asked.append((level, opt, size))
        if self.err:
            raise self.err
        return self.raw


def xucred(version, uid):
    return struct.pack("Ii", version, uid) + struct.pack("h", 1) + b"\0" * 66


def test_peer_uid_reads_local_peercred_on_macos(monkeypatch):
    monkeypatch.setattr(server.sys, "platform", "darwin")
    c = FakeConn(xucred(0, 501))
    assert server.peer_uid(c) == 501
    assert c.asked[0][:2] == (0, 0x001)                # SOL_LOCAL, LOCAL_PEERCRED
    assert server.peer_uid(FakeConn(xucred(1, 501))) is None      # an unknown struct version
    assert server.peer_uid(FakeConn(b"\0\0")) is None             # short
    assert server.peer_uid(FakeConn(err=OSError(22, "EINVAL"))) is None


def test_peer_uid_reads_so_peercred_on_linux(monkeypatch):
    monkeypatch.setattr(server.sys, "platform", "linux")
    monkeypatch.setattr(server.socket, "SO_PEERCRED", 17, raising=False)
    assert server.peer_uid(FakeConn(struct.pack("3i", 99, 1000, 1000))) == 1000


# -- no TCP port --------------------------------------------------------------------

def test_the_server_holds_no_af_inet_socket(unix):
    srv = unix()
    assert srv.socket.family == socket.AF_UNIX
    assert srv.server_address == SOCK


def test_serve_in_socket_mode_never_binds_tcp(room, monkeypatch, capsys):
    made = []
    monkeypatch.setattr(server, "make_server", lambda *a, **k: pytest.fail("a TCP port was bound"))
    monkeypatch.setattr(server, "poll_state", lambda: None)
    monkeypatch.setattr(server.UnixHTTPServer, "serve_forever", lambda self, *a: made.append(self))
    try:
        assert cli.main(["serve", "--unix", SOCK]) == 0
    finally:
        server.configure()
    (srv,) = made
    assert srv.socket.family == socket.AF_UNIX
    assert not os.path.exists(SOCK)                    # removed when serving ended
    out = capsys.readouterr().out
    assert "no TCP port" in out and room not in out


def test_talk_unix_socket_env_turns_socket_mode_on(room, monkeypatch):
    monkeypatch.setenv("TALK_UNIX_SOCKET", SOCK)
    with mock.patch.object(server, "serve") as serve:
        assert cli.main(["serve"]) == 0
    assert serve.call_args.kwargs["unix"] == SOCK and serve.call_args.kwargs["proxy_uid"] == 0


def test_bare_unix_flag_means_the_default_path(room, monkeypatch):
    with mock.patch.object(server, "serve") as serve:
        assert cli.main(["serve", "--unix", "--proxy-uid", "7"]) == 0
    assert serve.call_args.kwargs["unix"] is True and serve.call_args.kwargs["proxy_uid"] == 7


def test_tcp_serve_passes_no_socket_settings(room):
    """make_server and the TCP call are unchanged: no unix or proxy_uid keyword at all."""
    with mock.patch.object(server, "serve") as serve:
        assert cli.main(["serve"]) == 0
    assert "unix" not in serve.call_args.kwargs and "proxy_uid" not in serve.call_args.kwargs


# -- modes, and what refuses to start ------------------------------------------------------

def test_socket_0600_in_a_0700_dir(unix):
    unix()
    assert stat.S_ISSOCK(os.lstat(SOCK).st_mode)
    assert stat.S_IMODE(os.lstat(SOCK).st_mode) == 0o600
    assert stat.S_IMODE(os.lstat(".").st_mode) == 0o700


def test_the_default_path_sits_beside_the_token(unix, room):
    srv = unix(path=None)
    assert srv.server_address == os.path.join("vm", "talk", "talk.sock") == server.default_socket()
    assert stat.S_IMODE(os.lstat(srv.server_address).st_mode) == 0o600
    assert stat.S_IMODE(os.lstat(token_store.path().parent).st_mode) == 0o700


def serve_exit(argv, monkeypatch):
    monkeypatch.setattr(server, "poll_state", lambda: None)
    monkeypatch.setattr(server.UnixHTTPServer, "serve_forever", lambda self, *a: None)
    try:
        return cli.main(argv)
    finally:
        server.configure()


@pytest.mark.parametrize("m", [0o750, 0o705, 0o770, 0o755])
def test_a_dir_with_group_or_other_bits_refuses_to_start(room, monkeypatch, capsys, m):
    os.mkdir("d", 0o700)
    os.chmod("d", m)
    assert serve_exit(["serve", "--unix", "d/t.sock"], monkeypatch) == 2
    err = capsys.readouterr().err
    assert err.startswith("talk serve: socket directory ") and f"{m:04o}" in err and room not in err
    assert not os.path.exists("d/t.sock")


def test_a_stale_non_socket_refuses_to_start(room, monkeypatch, capsys):
    open(SOCK, "w").close()
    assert serve_exit(["serve", "--unix", SOCK], monkeypatch) == 2
    assert "is not a socket" in capsys.readouterr().err
    assert os.path.isfile(SOCK)                        # left as found


def test_a_symlink_at_the_path_refuses_to_start(room, monkeypatch, capsys):
    os.symlink("elsewhere.sock", SOCK)
    assert serve_exit(["serve", "--unix", SOCK], monkeypatch) == 2
    assert "is not a socket" in capsys.readouterr().err


def test_a_stale_socket_of_ours_is_replaced(room, monkeypatch):
    dead = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    dead.bind(SOCK)
    dead.close()                                       # the file stays; nobody answers
    assert stat.S_ISSOCK(os.lstat(SOCK).st_mode)
    assert serve_exit(["serve", "--unix", SOCK], monkeypatch) == 0


def test_a_live_socket_refuses_to_start(unix, monkeypatch, capsys):
    unix()
    assert serve_exit(["serve", "--unix", SOCK], monkeypatch) == 2
    assert "in use" in capsys.readouterr().err
    assert get(SOCK, "/")[0] == 200                    # the running one is untouched


def test_a_socket_of_another_user_refuses_to_start(room, monkeypatch, capsys):
    dead = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    dead.bind(SOCK)
    dead.close()
    real = os.lstat

    def lstat(p, *a, **k):
        st = real(p, *a, **k)
        if os.fspath(p) == SOCK:
            return mock.Mock(st_mode=st.st_mode, st_uid=os.geteuid() + 1)
        return st
    monkeypatch.setattr(server.os, "lstat", lstat)
    assert serve_exit(["serve", "--unix", SOCK], monkeypatch) == 2
    assert "owned by uid" in capsys.readouterr().err


def test_a_path_too_long_refuses_to_start(room, monkeypatch, capsys):
    assert serve_exit(["serve", "--unix", "x" * 104], monkeypatch) == 2
    assert "at most 103" in capsys.readouterr().err


# -- talk pair on a socket-mode host refuses the 127.0.0.1 URL -------------------------------

@pytest.fixture
def tty(monkeypatch):
    monkeypatch.setattr(cli, "stdout_is_terminal", lambda: True)


def test_pair_with_a_socket_at_the_default_path_refuses_127(unix, room, tty, capsys):
    unix(path=None)                                    # a live socket at the default PATH
    for argv in (["pair"], ["pair", "--url", "http://127.0.0.1:8781/"], ["pair", "--url", "http://127.0.0.2/"]):
        assert cli.main(argv) == 2
        out = capsys.readouterr()
        assert out.out == "" and "unix socket" in out.err and "--url" in out.err
        assert room not in out.out + out.err


def test_pair_with_talk_unix_socket_set_refuses_127(room, tty, capsys, monkeypatch):
    monkeypatch.setenv("TALK_UNIX_SOCKET", "/somewhere/talk.sock")
    assert cli.main(["pair"]) == 2
    out = capsys.readouterr()
    assert out.out == "" and room not in out.err


def test_pair_on_a_socket_host_prints_the_proxy_url(unix, room, tty, capsys):
    unix(path=None)
    assert cli.main(["pair", "--url", f"https://{TS}/talk"]) == 0
    assert capsys.readouterr().out == f"https://{TS}/talk/#pair={room}\n"


def test_pair_on_a_tcp_host_still_takes_127(room, tty, capsys):
    assert not os.path.exists(server.default_socket())
    assert cli.main(["pair"]) == 0
    assert capsys.readouterr().out == f"http://127.0.0.1:8780/#pair={room}\n"


def test_a_stale_file_that_is_not_a_socket_does_not_count(room, tty, capsys):
    open(server.default_socket(), "w").close()
    assert cli.main(["pair"]) == 0
