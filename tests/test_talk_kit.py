"""Kit mode: `talk serve --kit --unix PATH`, talk as a room behind the kit's web front door.

The front door starts the room with `--kit --unix {socket}`, strips the room's prefix,
forwards the browser's Host and Origin verbatim, and adds its own X-Sessionkit-User
(after stripping every inbound X-Sessionkit-*). So, in kit mode:

- only talk's own uid may connect (no proxy uid; --proxy-uid with --kit is refused);
- every route, the page included, needs exactly one X-Sessionkit-User, and nothing else:
  no token is made or read, GET pair is 204 and POST pair is a 404;
- the Host is any the front door forwards but localhost / *.localhost, and an Origin,
  when sent, must name that Host (a missing port is the Origin scheme's default);
- there is no TCP port, in process or out of it.

Without --kit nothing changes: tests/test_talk_unix.py and the rest stand as they are.
Every test chdirs into a fresh 0700 dir and binds a RELATIVE socket name (a unix socket
path holds 104 bytes; macOS tmp paths are long). HOME and SESSIONVOICE_HOME are fenced.
"""
import json, os, shutil, signal, socket, subprocess, sys, threading, time
from datetime import date
from unittest import mock

import pytest

from talk import cli, server, token_store
from tests._anchor import ANCHOR
from tests.test_talk_unix import get

SECRET = "the words the microphone heard"
SOCK = "k.sock"
TS = "box.example.ts.net"
FRONT = "127.0.0.1:8765"                     # the front door's default address, as the browser sends it
USER = ("X-Sessionkit-User", "someone")
PAGE = open(os.path.join(server.HERE, "index.html"), "rb").read()


@pytest.fixture
def room(tmp_path, monkeypatch):
    """cwd = a fresh 0700 dir; a fenced install (relative home, so paths stay short) with a
    heard log and NO token: kit mode must never need one."""
    d = tmp_path / "s"
    d.mkdir(mode=0o700)
    monkeypatch.chdir(d)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SESSIONVOICE_HOME", "sv")
    monkeypatch.delenv("VOICEMODE_BASE_DIR", raising=False)
    monkeypatch.delenv("EARS_HOME", raising=False)
    monkeypatch.delenv("TALK_UNIX_SOCKET", raising=False)
    monkeypatch.delenv("TALK_ALLOW_HOSTS", raising=False)
    logs = d / "sv" / "logs" / "conversations"
    logs.mkdir(parents=True)
    (logs / f"heard_{date.today():%Y-%m-%d}.jsonl").write_text(
        json.dumps({"ts": f"{ANCHOR:%Y-%m-%dT%H:%M:%S}", "seq": 1, "kind": "turn", "text": SECRET}) + "\n")
    return d


@pytest.fixture
def kit(room):
    """start(path=SOCK, allow=None) -> the running kit-mode UnixHTTPServer."""
    servers = []

    def start(path=SOCK, allow=None):
        server.configure(allow_hosts=allow)
        srv = server.make_unix_server(path, kit=True)
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


def kget(url="/", host=FRONT, headers=(USER,), **kw):
    return get(SOCK, url, host=host, headers=list(headers), **kw)


# -- 1. the front door's request: same uid, X-Sessionkit-User, no token ----------------------

def test_the_front_doors_request_gets_the_page_with_no_token(kit):
    kit()
    status, hd, body = kget("/")
    assert status == 200 and body == PAGE and "set-cookie" not in hd
    status, hd, body = kget("/manifest.json")
    assert status == 200 and hd["content-type"] == ["application/manifest+json"]
    status, hd, body = kget("/pair")
    assert status == 204 and "set-cookie" not in hd
    assert not os.path.exists(token_store.path())       # no token was made, none is needed


def test_the_events_stream_needs_no_cookie(kit):
    kit()
    status, hd, body = kget("/events?n=5", until=SECRET.encode())
    assert status == 200 and hd["content-type"] == ["text/event-stream"] and "set-cookie" not in hd
    assert body.startswith(b"event: config\n") and SECRET.encode() in body
    assert not os.path.exists(token_store.path())


def test_a_token_cookie_is_neither_needed_nor_enough(kit, room):
    """Even a valid talk_token cookie does not stand in for the front door's header."""
    token = token_store.load_or_create()
    kit()
    assert kget("/", headers=[("Cookie", f"talk_token={token}")])[0] == 403
    assert kget("/events?n=1", headers=[("Cookie", f"talk_token={token}")])[0] == 403


def test_the_keepalive_recheck_passes_for_the_life_of_the_stream(kit, monkeypatch):
    """/events re-checks `paired()` at each keep-alive; in kit mode that is the header."""
    monkeypatch.setattr(server, "KEEPALIVE", 0)
    kit()
    status, _, body = kget("/events?n=1", until=b": ping\n\n")
    assert status == 200 and b": ping\n\n" in body


def test_the_kit_starts_with_two_lines_and_no_token(room, monkeypatch, capsys):
    made = []
    monkeypatch.setattr(server, "poll_state", lambda: None)
    monkeypatch.setattr(server.UnixHTTPServer, "serve_forever", lambda self, *a: made.append(self))
    try:
        assert cli.main(["serve", "--kit", "--unix", SOCK]) == 0
    finally:
        server.configure()
    (srv,) = made
    assert server.kit_server(srv) and srv.talk_peer_uids == frozenset({os.geteuid()})
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 2
    assert out[0].startswith(f"talk-web: serving {server.HERE} on unix socket {SOCK} (no TCP port)")
    assert out[1] == (f"talk-web: kit mode: answering uid {os.geteuid()} only; trusting X-Sessionkit-User"
                      " over the socket; no pairing")
    assert "token" not in "\n".join(out)
    assert not os.path.exists(token_store.path()) and not os.path.exists(SOCK)


# -- 2. no header, or not exactly one good one: 403 on every route ----------------------------

ROUTES = ["/", "/index.html", "/manifest.json", "/events?n=1", "/pair", "/avatar/x", "/nowhere"]


@pytest.mark.parametrize("url", ROUTES)
def test_without_the_header_every_route_is_403(kit, url):
    kit()
    status, hd, body = kget(url, headers=())
    assert status == 403 and hd["connection"] == ["close"]
    assert body == b"talk: this request did not come through the kit's front door\n"
    assert SECRET.encode() not in body and PAGE not in body


@pytest.mark.parametrize("headers", [
    [USER, ("X-Sessionkit-User", "other")],               # two
    [("X-Sessionkit-User", "")],                          # empty
    [("X-Sessionkit-User", "   ")],                       # blank
    [("X-Sessionkit-User", "x" * 65)],                    # too long
    [("X_Sessionkit_User", "someone")],                   # the other spelling is a different header
])
def test_a_bad_header_is_403(kit, headers):
    kit()
    assert kget("/", headers=headers)[0] == 403
    assert kget("/events?n=1", headers=headers)[0] == 403


def test_a_non_ascii_header_is_403(kit):
    kit()
    req = f"GET / HTTP/1.1\r\nHost: {FRONT}\r\n".encode() + "X-Sessionkit-User: sé\r\n\r\n".encode("latin-1")
    from tests.test_talk_unix import over
    assert over(SOCK, req)[0] == 403


def test_the_longest_header_is_answered(kit):
    kit()
    assert kget("/", headers=[("X-Sessionkit-User", "x" * 64)])[0] == 200


# -- 3. a different uid is refused before a byte is read ------------------------------------

def test_only_this_uid_is_in_the_set(kit):
    srv = kit()
    assert srv.talk_peer_uids == frozenset({os.geteuid()})
    assert server.kit_server(srv) and server.unix_server(srv)


@pytest.mark.parametrize("uid", [0, os.geteuid() + 1, None])
def test_another_uid_gets_403_and_nothing_is_read(kit, uid, capsys):
    if uid == os.geteuid():
        pytest.skip("the test runs as root: uid 0 is this uid")
    srv = kit()
    read = []
    real = server.Handler.handle_one_request

    def recorded(self):
        if self.server is srv:
            read.append(self.client_address)
        return real(self)

    with mock.patch.object(server, "peer_uid", lambda conn: uid), \
            mock.patch.object(server.Handler, "handle_one_request", recorded):
        status, hd, body = kget("/events?n=5")
    assert status == 403 and hd["connection"] == ["close"]
    assert body.startswith(b"talk: a connection from ") and b"is not allowed on this socket" in body
    assert SECRET.encode() not in body and b"event:" not in body
    assert read == []
    assert f"(allowed: uid {os.geteuid()})" in capsys.readouterr().err


def test_proxy_uid_is_ignored_by_make_unix_server_in_kit_mode(room):
    srv = server.make_unix_server(SOCK, proxy_uid=4242, kit=True)
    try:
        assert srv.talk_peer_uids == frozenset({os.geteuid()})
    finally:
        srv.server_close()


# -- 4. Host and Origin ---------------------------------------------------------------------

@pytest.mark.parametrize("host, origin", [
    (FRONT, None),
    ("[::1]:8765", None),
    (TS, None),
    ("BOX.Example.TS.net.", None),
    (FRONT, f"http://{FRONT}"),
    ("[::1]:8765", "http://[::1]:8765"),
    (TS, f"https://{TS}"),                    # the tailnet pair: portless Host, https Origin: 443 = 443
    (TS, f"http://{TS}"),
    (f"{TS}:443", f"https://{TS}"),
    (TS, f"https://{TS}:443"),
    ("127.0.0.1:80", "http://127.0.0.1"),
])
def test_hosts_and_origins_the_front_door_forwards_are_answered(kit, host, origin):
    kit()
    hs = [USER] + ([("Origin", origin)] if origin else [])
    status, _, body = kget("/", host=host, headers=hs)
    assert status == 200 and body == PAGE


@pytest.mark.parametrize("host", ["localhost", "localhost:8765", "LOCALHOST.", "x.localhost", "a.b.localhost:1",
                                  "", None, "junk host", "a:b:c", "127.0.0.1:0", "[::1", "ex@mple"])
def test_localhost_and_junk_hosts_are_refused(kit, host):
    kit()
    status, _, body = kget("/", host=host)
    assert status == 403 and SECRET.encode() not in body and PAGE not in body


def test_two_hosts_are_refused(kit):
    kit()
    status, _, body = kget("/", headers=[USER, ("Host", TS)])
    assert status == 403 and b"more than one Host" in body


def test_an_allowed_host_does_not_answer_localhost(kit):
    """--allow-host adds nothing under the kit rule, and cannot reopen localhost."""
    kit(allow={"localhost"})
    assert kget("/", host="localhost")[0] == 403


@pytest.mark.parametrize("host, origin", [
    (FRONT, "https://evil.example"),
    (FRONT, "http://127.0.0.1:8766"),         # another port
    (FRONT, "http://127.0.0.1"),              # 80, not 8765
    (FRONT, "https://127.0.0.1:8765x"),
    (TS, f"https://{TS}:8443"),
    (TS, "https://other.example.ts.net"),
    (TS, f"http://{TS}:443"),                 # http's default is 80: 443 is a different port
    ("[::1]:8765", f"http://{FRONT}"),
    (FRONT, "null"),
    (FRONT, f"ftp://{FRONT}"),
    (FRONT, f"http://user@{FRONT}"),
])
def test_an_origin_that_is_not_its_host_is_refused(kit, host, origin):
    kit()
    status, _, body = kget("/", host=host, headers=[USER, ("Origin", origin)])
    assert status == 403 and b"Origin" in body and PAGE not in body


def test_two_origins_are_refused(kit):
    kit()
    o = ("Origin", f"http://{FRONT}")
    status, _, body = kget("/", headers=[USER, o, o])
    assert status == 403 and b"more than one Origin" in body


def test_the_host_is_checked_before_the_header(kit):
    """Host and Origin come first, on every route: a bad Host is refused as a Host."""
    kit()
    status, _, body = kget("/", host="localhost", headers=())
    assert status == 403 and b'Host "localhost"' in body


# -- 5. POST pair: no pairing in kit mode ----------------------------------------------------

def test_post_pair_is_404_with_no_cookie(kit, room):
    token = token_store.load_or_create()
    kit()
    status, hd, body = kget("/pair", method="POST", headers=[USER, ("X-Talk-Token", token)])
    assert status == 404 and "set-cookie" not in hd and token.encode() not in body
    status, hd, _ = kget("/talk/pair", method="POST", headers=[USER])
    assert status == 404 and "set-cookie" not in hd


def test_post_without_the_header_is_403(kit):
    kit()
    status, hd, _ = kget("/pair", method="POST", headers=[("X-Talk-Token", "x")])
    assert status == 403 and "set-cookie" not in hd


# -- 6. the CLI ------------------------------------------------------------------------------

def test_kit_without_a_socket_exits_2_and_binds_nothing(room, capsys):
    with mock.patch.object(server, "serve") as serve, \
            mock.patch.object(server, "make_unix_server") as mus, \
            mock.patch.object(server, "make_server") as ms:
        assert cli.main(["serve", "--kit"]) == 2
    assert not serve.called and not mus.called and not ms.called
    assert capsys.readouterr().err == ("talk serve: --kit needs --unix PATH (kit mode listens on a unix"
                                       " socket only)\n")


@pytest.mark.parametrize("uid", ["0", "4242"])
def test_kit_with_proxy_uid_exits_2(room, capsys, uid):
    with mock.patch.object(server, "serve") as serve:
        assert cli.main(["serve", "--kit", "--unix", SOCK, "--proxy-uid", uid]) == 2
    assert not serve.called
    assert capsys.readouterr().err == ("talk serve: --proxy-uid does not apply with --kit: kit mode admits"
                                       " talk's own uid only\n")


def test_kit_passes_unix_and_kit_and_no_proxy_uid(room):
    with mock.patch.object(server, "serve") as serve:
        assert cli.main(["serve", "--kit", "--unix", SOCK]) == 0
    kw = serve.call_args.kwargs
    assert kw["unix"] == SOCK and kw["kit"] is True and "proxy_uid" not in kw


def test_kit_takes_the_socket_from_the_env(room, monkeypatch):
    monkeypatch.setenv("TALK_UNIX_SOCKET", SOCK)
    with mock.patch.object(server, "serve") as serve:
        assert cli.main(["serve", "--kit"]) == 0
    assert serve.call_args.kwargs["unix"] == SOCK and serve.call_args.kwargs["kit"] is True


def test_without_kit_the_calls_are_as_before(room):
    """R5: no `kit` keyword reaches serve() unless --kit was given."""
    with mock.patch.object(server, "serve") as serve:
        assert cli.main(["serve", "--unix", SOCK]) == 0
    assert serve.call_args.kwargs["proxy_uid"] == 0 and "kit" not in serve.call_args.kwargs
    with mock.patch.object(server, "serve") as serve:
        assert cli.main(["serve"]) == 0
    assert not {"kit", "unix", "proxy_uid"} & set(serve.call_args.kwargs)


def test_serve_kit_never_binds_tcp_nor_touches_the_token(room, monkeypatch):
    monkeypatch.setattr(server, "poll_state", lambda: None)
    monkeypatch.setattr(server.UnixHTTPServer, "serve_forever", lambda self, *a: None)
    with mock.patch.object(server, "make_server") as ms, \
            mock.patch.object(server.token_store, "load_or_create") as loc:
        try:
            server.serve(unix=SOCK, kit=True)
        finally:
            server.configure()
    assert not ms.called and not loc.called


def test_serve_kit_without_a_socket_raises_before_binding(room):
    with mock.patch.object(server, "make_server") as ms, mock.patch.object(server, "make_unix_server") as mus:
        with pytest.raises(server.UnixSocketError, match="--kit needs --unix"):
            server.serve(kit=True)
    assert not ms.called and not mus.called


# -- 7. no TCP listener ------------------------------------------------------------------------

def test_the_kit_server_holds_no_af_inet_socket(kit):
    srv = kit()
    assert srv.socket.family == socket.AF_UNIX and srv.server_address == SOCK


LSOF = shutil.which("lsof") or next((p for p in ("/usr/sbin/lsof", "/usr/bin/lsof") if os.path.exists(p)), None)


@pytest.mark.skipif(LSOF is None, reason="lsof is not installed")
def test_a_real_talk_serve_kit_opens_no_tcp_port(room, tmp_path):
    """Out of process: `python -m talk serve --kit --unix S`, with a fenced HOME and home, and a
    PATH with no `session` command. lsof sees its unix socket and no TCP socket at all."""
    env = {"HOME": str(tmp_path / "home"), "SESSIONVOICE_HOME": str(tmp_path / "svh"),
           "PATH": os.pathsep.join([os.path.dirname(sys.executable), "/usr/bin", "/bin"])}
    log = open(tmp_path / "room.log", "wb")
    proc = subprocess.Popen([sys.executable, "-m", "talk", "serve", "--kit", "--unix", SOCK],
                            cwd=room, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                            start_new_session=True)
    try:
        deadline = time.monotonic() + 20
        while not os.path.exists(SOCK):
            assert proc.poll() is None, (tmp_path / "room.log").read_text()
            assert time.monotonic() < deadline, "the socket never appeared"
            time.sleep(0.05)
        status, _, body = kget("/")
        assert status == 200 and body == PAGE
        unix = subprocess.run([LSOF, "-nP", "-a", "-p", str(proc.pid), "-U"], capture_output=True, text=True)
        assert SOCK in unix.stdout                       # lsof can see this process: the control
        tcp = subprocess.run([LSOF, "-nP", "-a", "-p", str(proc.pid), "-iTCP"], capture_output=True, text=True)
        assert tcp.stdout == ""
        assert not os.path.exists(tmp_path / "svh" / "talk" / "token")
    finally:
        try:
            os.killpg(proc.pid, signal.SIGTERM)          # its own group only (start_new_session)
        except ProcessLookupError:
            pass
        proc.wait(timeout=10)
        log.close()
