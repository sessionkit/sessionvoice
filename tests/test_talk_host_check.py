"""`talk serve` answers only a Host it knows (EARS-336, SK-80 S1).

A DNS-rebinding page reaches 127.0.0.1:<port> under its own name, so the
browser sends `Host: attacker.example:<port>`. Every route must refuse that
before it routes, and refuse a bad Origin too. Loopback on the bound port, the
--host address and the configured names (--allow-host / TALK_ALLOW_HOSTS) are
answered.

Every server here binds a FREE port picked by the kernel (port 0), never 8780
(the live talk-web), on 127.0.0.1. HOME and VOICEMODE_BASE_DIR are a tmp dir,
the state poll never starts, and every request is made by hand over a raw
socket, so a test can send any Host it likes, two of them, or none.

Every request carries the room's pairing cookie (EARS-338) unless a test says
cookie=False: the Host check runs first, so each refusal here is the Host
check's even for a paired browser. tests/test_talk_token_gate.py tests the gate.
"""
import json, os, socket, threading
from datetime import date
from unittest import mock

import pytest

from talk import cli, server, token_store
from tests._anchor import ANCHOR

SECRET = "the words the microphone heard"
AVATAR = b"\xff\xd8\xff fake jpeg"


@pytest.fixture
def room(tmp_path, monkeypatch):
    """A fenced HOME with a heard log holding one turn, and a fake face."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm"))
    monkeypatch.delenv("EARS_HOME", raising=False)
    logs = tmp_path / "vm" / "logs" / "conversations"
    logs.mkdir(parents=True)
    (logs / f"heard_{date.today():%Y-%m-%d}.jsonl").write_text(
        json.dumps({"ts": f"{ANCHOR:%Y-%m-%dT%H:%M:%S}", "seq": 1, "kind": "turn", "text": SECRET}) + "\n")
    face = tmp_path / "home" / ".session" / "agents" / "ivy"
    face.mkdir(parents=True)
    (face / "avatar.jpg").write_bytes(AVATAR)
    token_store.load_or_create()            # the fenced install's token: raw() sends it as the cookie
    return tmp_path


@pytest.fixture
def talk(room):
    """start(host="127.0.0.1", allow=None) -> the bound port of a running server."""
    servers = []
    before = server.ALLOW_HOSTS

    def start(host="127.0.0.1", allow=None):
        server.configure(allow_hosts=allow)
        srv = server.make_server(host, 0)
        threading.Thread(target=srv.serve_forever, args=(0.05,), daemon=True).start()
        servers.append(srv)
        return srv.server_address[1]

    try:
        yield start
    finally:
        for srv in servers:
            srv.shutdown()
            srv.server_close()
        server.configure()
        server.ALLOW_HOSTS = before


def raw(port, path="/", host=None, headers=(), version="HTTP/1.1", until=None, cookie=True):
    """One request over a raw socket. host=None sends no Host at all; a list sends
    each. Reads to EOF, or until `until` appears (the SSE stream never ends).
    cookie=True sends the fenced install's pairing cookie (talk_token)."""
    lines = [f"GET {path} {version}"]
    for h in ([] if host is None else [host] if isinstance(host, str) else host):
        lines.append(f"Host: {h}")
    lines += [f"{k}: {v}" for k, v in headers]
    if cookie:
        lines.append(f"Cookie: {server.COOKIE}={token_store.load()}")
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
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
            length = [ln.split(b":", 1)[1] for ln in head.split(b"\r\n")
                      if ln.lower().startswith(b"content-length:")]
            if sep and length and len(body) >= int(length[0]):
                break                   # a keep-alive 200: the whole body is here
            if sep and head.split(b" ", 2)[1:2] in ([b"204"], [b"304"]):
                break                   # no body, and no Content-Length (RFC 9110 8.6): the head is all
    head, _, body = data.partition(b"\r\n\r\n")
    status_line, *hdrs = head.decode("latin-1").split("\r\n")
    hd = {}
    for h in hdrs:
        k, _, v = h.partition(":")
        hd[k.strip().lower()] = v.strip()
    return int(status_line.split()[1]), hd, body


def refused(resp):
    status, hd, body = resp
    assert status == 403, resp
    assert hd["content-type"] == "text/plain; charset=utf-8"
    assert hd["cache-control"] == "no-store"
    assert hd["connection"] == "close"
    assert int(hd["content-length"]) == len(body)
    assert body.startswith(b"talk: ") and b"--allow-host" in body and b"TALK_ALLOW_HOSTS" in body
    assert b"text/event-stream" not in body and SECRET.encode() not in body
    assert "text/event-stream" not in hd.get("content-type", "")
    return body


def served(resp, path="/"):
    status, hd, body = resp
    assert status == 200, resp
    if path.startswith("/events"):
        assert hd["content-type"] == "text/event-stream"
        assert body.startswith(b"event: config\n"), body[:80]
    return body


ROUTES = ["/", "/index.html", "/manifest.json", "/events?n=1", "/avatar/ivy", "/no/such/path", "/talk/events"]


def get(port, path, **kw):
    if path.startswith("/events") or path.endswith("/events"):
        kw.setdefault("until", b"\n\n")
    return raw(port, path, **kw)


# -- the rebinding case: a foreign Host is refused on every route -------------------

@pytest.mark.parametrize("path", ROUTES)
def test_foreign_host_refused_on_every_route(talk, path):
    port = talk()
    body = refused(get(port, path, host=f"attacker.example:{port}"))
    assert f'Host "attacker.example:{port}"'.encode() in body


def test_the_same_routes_are_served_to_loopback(talk):
    """The control: what the foreign Host was refused is there for 127.0.0.1."""
    port = talk()
    assert served(get(port, "/", host=f"127.0.0.1:{port}")).startswith(b"<!")
    assert served(get(port, "/avatar/ivy", host=f"127.0.0.1:{port}")) == AVATAR
    body = served(get(port, "/events?n=5", host=f"127.0.0.1:{port}", until=SECRET.encode()), "/events")
    assert SECRET.encode() in body
    assert get(port, "/no/such/path", host=f"127.0.0.1:{port}")[0] == 404


@pytest.mark.parametrize("name", ["127.0.0.1", "localhost", "LOCALHOST", "localhost."])
@pytest.mark.parametrize("path", ["/", "/events?n=1"])
def test_loopback_on_the_bound_port_served(talk, name, path):
    port = talk()
    served(get(port, path, host=f"{name}:{port}"), path)


@pytest.mark.parametrize("host", [
    "127.0.0.1:1",                    # loopback, the wrong port
    "localhost:8780",                 # ... the default port, which this server is not on
    "127.0.0.1",                      # no port means 80, and this server is not on 80
    "localhost",
    "localhost.evil.com:{port}",      # suffix tricks: matching is exact
    "127.0.0.1.nip.io:{port}",
    "evil.localhost:{port}",
    "127.1:{port}",                   # other spellings of loopback
    "0x7f.0.0.1:{port}",
    "2130706433:{port}",
    "0.0.0.0:{port}",
    "[::1]:{port}",                   # AF_INET only; parsed so it is refused cleanly
    "[::1]",
    "127.0.0.1:{port}:{port}",        # unparsable
    "127.0.0.1:",
    "127.0.0.1:0",
    "127.0.0.1:99999",
    "127.0.0.1:+{port}",
    "127.0.0.1:{port} x",
    "localhost..:{port}",
    "-localhost:{port}",
    "",                               # an empty Host
])
def test_bad_hosts_refused(talk, host):
    port = talk()
    refused(get(port, "/events?n=1", host=host.format(port=port)))
    refused(get(port, "/", host=host.format(port=port)))


def test_missing_host_refused(talk):
    """HTTP/1.0 with no Host, and HTTP/1.1 with none."""
    port = talk()
    assert b"no Host" in refused(raw(port, "/", host=None, version="HTTP/1.0"))
    assert b"no Host" in refused(raw(port, "/events?n=1", host=None))


def test_duplicate_host_refused(talk):
    port = talk()
    good = f"127.0.0.1:{port}"
    assert b"more than one Host" in refused(raw(port, "/", host=[good, good]))
    refused(raw(port, "/events?n=1", host=[good, "attacker.example"]))
    refused(raw(port, "/events?n=1", host=["attacker.example", good]))


def test_forwarded_headers_ignored(talk):
    """A rebound page is same-origin to the browser, so it can set these."""
    port = talk()
    xf = [("X-Forwarded-Host", f"127.0.0.1:{port}"), ("X-Forwarded-For", "127.0.0.1"), ("Forwarded", "host=localhost")]
    refused(get(port, "/events?n=1", host=f"attacker.example:{port}", headers=xf))


# -- the bind host: --host as given, and as the socket reports it -------------------

def test_bind_host_localhost_serves_both_names(talk):
    """Binding "localhost" leaves server_address[0] == "127.0.0.1": both are answered."""
    port = talk(host="localhost")
    served(get(port, "/", host=f"localhost:{port}"))
    served(get(port, "/", host=f"127.0.0.1:{port}"))
    refused(get(port, "/", host=f"attacker.example:{port}"))


@pytest.fixture
def bound(room):
    """bound(given, reported) -> the port of a server on 127.0.0.1 that believes
    --host was `given` and the socket reports `reported`. Neither need be loopback,
    so only the bind-host rule can serve them: a real non-loopback bind would put
    the test on a LAN, and binding "localhost" is masked by the loopback rule."""
    servers = []
    server.configure()

    def start(given, reported):
        srv = server.make_server("127.0.0.1", 0)
        port = srv.server_address[1]
        srv.talk_bind_host, srv.server_address = given, (reported, port)
        threading.Thread(target=srv.serve_forever, args=(0.05,), daemon=True).start()
        servers.append(srv)
        return port

    try:
        yield start
    finally:
        for srv in servers:
            srv.shutdown()
            srv.server_close()


def test_bind_host_as_given_and_as_reported_served(bound):
    port = bound("Talk.LAN.", "192.0.2.7")
    served(get(port, "/", host=f"talk.lan:{port}"))                       # as given (lower-cased, dot dropped)
    served(get(port, "/events?n=1", host=f"192.0.2.7:{port}"), "/events")  # as the socket reports it
    served(get(port, "/", host=f"192.0.2.7:{port}", headers=[("Origin", f"http://talk.lan:{port}")]))
    served(get(port, "/", host=f"127.0.0.1:{port}"))                      # loopback still answered
    refused(get(port, "/", host="talk.lan"))                              # the bound port only (portless = 80)
    refused(get(port, "/", host="192.0.2.7:1"))
    refused(get(port, "/", host=f"192.0.2.7:{port}", headers=[("Origin", "http://talk.lan")]))
    refused(get(port, "/", host=f"x.talk.lan:{port}"))


@pytest.mark.parametrize("any_addr", ["0.0.0.0", "", "::"])
def test_a_bind_host_naming_no_one_address_adds_no_name(bound, any_addr):
    port = bound(any_addr, "0.0.0.0")
    refused(get(port, "/", host=f"0.0.0.0:{port}"))
    served(get(port, "/", host=f"127.0.0.1:{port}"))
    served(get(port, "/", host=f"localhost:{port}"))


def test_make_server_records_the_bind_host(room):
    srv = server.make_server("localhost", 0)
    try:
        assert srv.talk_bind_host == "localhost"
        assert srv.server_address[0] == "127.0.0.1"
    finally:
        srv.server_close()


# -- configured names ----------------------------------------------------------

def test_configured_name_portless_served(talk):
    """tailscale serve passes the browser's Host through with no port."""
    port = talk(allow={"talk.example.ts.net"})
    served(get(port, "/", host="talk.example.ts.net"))
    served(get(port, "/events?n=1", host="talk.example.ts.net"), "/events")
    served(get(port, "/", host="TALK.example.ts.net."))
    served(get(port, "/", host="talk.example.ts.net:8443"))      # a bare name: any port
    refused(get(port, "/", host="other.example.ts.net"))
    refused(get(port, "/", host="x.talk.example.ts.net"))
    refused(get(port, "/", host="talk.example.ts.net.evil.com"))


def test_configured_name_with_port_only_on_that_port(talk):
    port = talk(allow={"talk.example.ts.net:8443"})
    served(get(port, "/", host="talk.example.ts.net:8443"))
    served(get(port, "/events?n=1", host="talk.example.ts.net:8443"), "/events")
    refused(get(port, "/", host="talk.example.ts.net:8444"))
    refused(get(port, "/", host="talk.example.ts.net"))           # a portless Host never matches name:port
    refused(get(port, "/", host=f"talk.example.ts.net:{port}"))


def test_configured_name_port_80_still_needs_the_port(talk):
    port = talk(allow={"talk.example.ts.net:80"})
    served(get(port, "/", host="talk.example.ts.net:80"))
    refused(get(port, "/", host="talk.example.ts.net"))


def test_configured_names_do_not_widen_loopback(talk):
    port = talk(allow={"talk.example.ts.net"})
    served(get(port, "/", host=f"127.0.0.1:{port}"))
    refused(get(port, "/", host="127.0.0.1:1"))


# -- Origin: defence in depth ------------------------------------------------------

@pytest.mark.parametrize("path", ["/", "/events?n=1", "/avatar/ivy"])
@pytest.mark.parametrize("origin", [
    "http://attacker.example",
    "null",
    "http://127.0.0.1:{other}",       # loopback, another port: another origin
    "http://localhost",               # port 80
    "https://127.0.0.1:{port}.evil.com",
    "ftp://127.0.0.1:{port}",
    "file://",
    "http://127.0.0.1:{port}/path",   # anything after the authority but a lone "/"
    "http://127.0.0.1:{port}?q",
    "http://u@127.0.0.1:{port}",      # userinfo
    "http://127.0.0.1:{port}#f",
    "",
    "127.0.0.1:{port}",
])
def test_bad_origin_refused(talk, path, origin):
    port = talk()
    other = 1 if port != 1 else 2
    body = refused(get(port, path, host=f"127.0.0.1:{port}",
                       headers=[("Origin", origin.format(port=port, other=other))]))
    assert b"Origin" in body


@pytest.mark.parametrize("origin", ["http://127.0.0.1:{port}", "http://localhost:{port}",
                                    "http://LOCALHOST:{port}/", "HTTP://127.0.0.1:{port}"])
@pytest.mark.parametrize("path", ["/", "/events?n=1"])
def test_good_origin_served(talk, path, origin):
    port = talk()
    served(get(port, path, host=f"127.0.0.1:{port}", headers=[("Origin", origin.format(port=port))]), path)


@pytest.mark.parametrize("path", ["/", "/events?n=1"])
def test_no_origin_served(talk, path):
    """Same-origin EventSource, plain navigation and curl send none."""
    port = talk()
    served(get(port, path, host=f"127.0.0.1:{port}"), path)


def test_duplicate_origin_refused(talk):
    port = talk()
    good = f"http://127.0.0.1:{port}"
    assert b"more than one Origin" in refused(
        get(port, "/", host=f"127.0.0.1:{port}", headers=[("Origin", good), ("Origin", good)]))


def test_tailscale_origin_served(talk):
    """The tailscale page: Host with no port, Origin https://<name> (443)."""
    port = talk(allow={"talk.example.ts.net"})
    served(get(port, "/events?n=1", host="talk.example.ts.net",
               headers=[("Origin", "https://talk.example.ts.net")]), "/events")
    refused(get(port, "/events?n=1", host="talk.example.ts.net",
                headers=[("Origin", "https://attacker.example")]))


def test_origin_matches_a_configured_name_with_port_on_hostname_alone(talk):
    port = talk(allow={"talk.example.ts.net:8443"})
    served(get(port, "/", host="talk.example.ts.net:8443", headers=[("Origin", "https://talk.example.ts.net")]))
    served(get(port, "/", host="talk.example.ts.net:8443", headers=[("Origin", "https://talk.example.ts.net:8443")]))
    refused(get(port, "/", host="talk.example.ts.net"))


def test_good_origin_does_not_rescue_a_foreign_host(talk):
    port = talk()
    refused(get(port, "/", host=f"attacker.example:{port}", headers=[("Origin", f"http://127.0.0.1:{port}")]))


# -- the refusal itself --------------------------------------------------------

def test_refusal_closes_the_connection(talk):
    """Keep-alive is on for HTTP/1.1, but a refused client gets no second request."""
    port = talk()
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        req = f"GET / HTTP/1.1\r\nHost: attacker.example:{port}\r\n\r\n".encode()
        s.sendall(req + f"GET / HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode())
        data = b""
        while chunk := s.recv(65536):
            data += chunk
    assert data.count(b"HTTP/1.") == 1 and data.startswith(b"HTTP/1.1 403")


def test_refusal_echo_and_log_line_are_sanitised(talk, capsys):
    """A forged Host cannot put a newline, a control character or a wall of
    text into the body or the log; the log gets ONE line per refusal."""
    port = talk()
    forged = "attacker.example\x1b[31m\x07" + "a" * 300 + "\xe9"
    status, hd, body = raw(port, "/", host=forged)
    assert status == 403
    shown = body.decode("ascii")
    assert "\x1b" not in shown and "\x07" not in shown and "\xe9" not in shown
    assert shown.count("\n") == 1 and shown.endswith("\n")
    assert len(shown) < 250
    err = capsys.readouterr().err
    lines = [ln for ln in err.splitlines() if "403" in ln]
    assert len(lines) == 1
    assert "\x1b" not in err and "\x07" not in err and "attacker.example" in lines[0]


def test_the_refusal_is_logged_before_its_403_is_written():
    """EARS-373: refuse() logs before the first byte of the 403, so a client that has its
    403 can read the line; and a client that hangs up before the write is still logged."""
    from tests.test_talk_unix import bare_handler
    events = []
    h = bare_handler(events)
    h.requestline, h.request_version = "GET / HTTP/1.1", "HTTP/1.1"
    h.refuse('Host "attacker.example"')
    assert [kind for kind, _ in events][0] == "log"
    assert events[0][1] == '"GET / HTTP/1.1" 403 refused: Host "attacker.example"'
    assert b"".join(b for kind, b in events if kind == "write").startswith(b"HTTP/1.1 403 Forbidden\r\n")

    events = []
    h = bare_handler(events, fail=BrokenPipeError(32, "Broken pipe"))
    h.requestline, h.request_version = "GET /events HTTP/1.1", "HTTP/1.1"
    with pytest.raises(BrokenPipeError):
        h.refuse("this browser is not paired", hint=server.PAIR_HINT)
    assert events == [("log", '"GET /events HTTP/1.1" 403 refused: this browser is not paired')]


def test_refusal_writes_nothing_of_the_room(talk):
    port = talk()
    status, hd, body = get(port, "/events?n=150", host=f"attacker.example:{port}")
    assert status == 403
    assert b"event:" not in body and b"data:" not in body and SECRET.encode() not in body
    assert "x-accel-buffering" not in hd


# -- check_allow_hosts: the shape of a configured entry ---------------------------

@pytest.mark.parametrize("given, parsed", [
    (None, frozenset()),
    ("", frozenset()),
    ([], frozenset()),
    (["talk.example.ts.net"], {"talk.example.ts.net"}),
    (["Talk.Example.TS.net."], {"talk.example.ts.net"}),
    (["talk.example.ts.net:8443"], {"talk.example.ts.net:8443"}),
    (["192.0.2.7", "192.0.2.7:8780"], {"192.0.2.7", "192.0.2.7:8780"}),
    (["host:0080"], {"host:80"}),
    ("a.example, b.example c.example,,", {"a.example", "b.example", "c.example"}),
    ("  a.example\tb.example:1  ", {"a.example", "b.example:1"}),
])
def test_allow_hosts_accepted(given, parsed):
    got = server.check_allow_hosts(given)
    assert got == frozenset(parsed) and isinstance(got, frozenset)
    assert server.check_allow_hosts(got) == got           # idempotent: configure() re-checks


BAD_ALLOW = [
    "", "*", "*.example.ts.net", "talk.*", "https://talk.example.ts.net", "http://x",
    "talk.example.ts.net/path", "/talk", "user@talk.example.ts.net", "talk example",
    "talk.example\n", "talk:", "talk:0", "talk:65536", "talk:http", "talk:8:9", "-talk.example",
    "talk-.example", "talk..example", ".talk", "[::1]", "[::1]:8780", "::1", "talk_example.ts.net",
    "tälk.example",
]


@pytest.mark.parametrize("entry", BAD_ALLOW)
def test_allow_hosts_refused(entry):
    with pytest.raises(ValueError) as e:
        server.check_allow_hosts([entry])
    assert "refused" in str(e.value)


def test_configure_refuses_and_changes_nothing(talk):
    server.configure(allow_hosts={"keep.example"})
    before = (server.ALLOW_HOSTS, dict(server.SETTINGS))
    with pytest.raises(ValueError):
        server.configure(person="Zed", allow_hosts=["https://bad.example"])
    assert (server.ALLOW_HOSTS, dict(server.SETTINGS)) == before


def test_serve_refuses_a_bad_entry_before_binding():
    with mock.patch.object(server, "make_server") as make:
        with pytest.raises(ValueError):
            server.serve("127.0.0.1", 1, allow_hosts=["*"])
    make.assert_not_called()


# -- the command line: --allow-host and TALK_ALLOW_HOSTS ---------------------------

@pytest.fixture
def no_talk_env(monkeypatch):
    for k in ("TALK_PERSON", "TALK_SMART_TURN_DIR", "TALK_SETTINGS_URL", "TALK_CALL_URL", "TALK_ALLOW_HOSTS"):
        monkeypatch.delenv(k, raising=False)


def test_cli_default_is_no_configured_names(no_talk_env):
    with mock.patch.object(server, "serve") as serve:
        assert cli.main(["serve"]) == 0
    assert serve.call_args.kwargs["allow_hosts"] == frozenset()


def test_cli_flags_and_env_merge(no_talk_env, monkeypatch):
    monkeypatch.setenv("TALK_ALLOW_HOSTS", "a.example.ts.net, B.example.ts.net:8443")
    with mock.patch.object(server, "serve") as serve:
        assert cli.main(["serve", "--allow-host", "c.example", "--allow-host", "a.example.ts.net"]) == 0
    assert serve.call_args.kwargs["allow_hosts"] == {"a.example.ts.net", "b.example.ts.net:8443", "c.example"}


def test_cli_env_alone(no_talk_env, monkeypatch):
    monkeypatch.setenv("TALK_ALLOW_HOSTS", "talk.example.ts.net")
    with mock.patch.object(server, "serve") as serve:
        assert cli.main(["serve"]) == 0
    assert serve.call_args.kwargs["allow_hosts"] == {"talk.example.ts.net"}


@pytest.mark.parametrize("via", ["flag", "env"])
@pytest.mark.parametrize("entry", ["*", "https://talk.example.ts.net", "talk.example/x", "u@talk.example", "talk:0"])
def test_cli_bad_entry_exits_2_before_binding(no_talk_env, monkeypatch, capsys, via, entry):
    argv = ["serve", "--port", "1"]
    if via == "flag":
        argv += ["--allow-host", entry]
    else:
        monkeypatch.setenv("TALK_ALLOW_HOSTS", entry)
    with mock.patch.object(server, "serve") as serve, mock.patch.object(server, "make_server") as make:
        assert cli.main(argv) == 2
    serve.assert_not_called()
    make.assert_not_called()
    err = capsys.readouterr().err
    assert "--allow-host" in err and "TALK_ALLOW_HOSTS" in err and "refused" in err


def test_cli_empty_flag_refused(no_talk_env, capsys):
    with mock.patch.object(server, "serve") as serve:
        assert cli.main(["serve", "--allow-host", ""]) == 2
    serve.assert_not_called()


def test_cli_configured_name_reaches_the_running_server(no_talk_env, room, monkeypatch):
    """End to end through cli.main: the flag's name is answered, a stranger is not."""
    monkeypatch.setenv("TALK_ALLOW_HOSTS", "env.example.ts.net")
    made, ready = [], threading.Event()
    real_make = server.make_server

    def make(host, p):
        made.append(real_make(host, p))
        ready.set()
        return made[-1]

    with mock.patch.object(server, "poll_state", lambda: None), mock.patch.object(server, "make_server", make):
        t = threading.Thread(target=cli.main, args=(["serve", "--port", "0", "--allow-host", "talk.example.ts.net"],),
                             daemon=True)
        t.start()
        assert ready.wait(5), "talk serve never bound"
        srv = made[0]
        port = srv.server_address[1]
        try:
            served(get(port, "/", host="talk.example.ts.net"))
            served(get(port, "/", host="env.example.ts.net"))
            served(get(port, "/", host=f"127.0.0.1:{port}"))
            refused(get(port, "/", host="attacker.example"))
        finally:
            srv.shutdown()
            srv.server_close()
            t.join(5)
            server.configure()


def test_help_names_the_flag(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["serve", "--help"])
    assert e.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "--allow-host" in help_text and "TALK_ALLOW_HOSTS" in help_text
    assert "tailscale serve" in help_text
