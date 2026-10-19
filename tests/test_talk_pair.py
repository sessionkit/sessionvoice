"""Pairing: POST .../pair with the token in X-Talk-Token sets the cookie (EARS-338, S3).

The page shows the token once, in a header, and the answer is a 204 carrying the
exact cookie of the design (section 2): `talk_token=<t>; Max-Age=34560000;
HttpOnly; SameSite=Strict`, plus `; Secure` when the Origin is https, and never a
Path or a Domain. Anything else is refused with no Set-Cookie: a wrong or missing
header, a foreign Host or Origin (EARS-336's check, first), and a Host of
localhost (T7b: its cookie would go to whatever answers localhost). POST to any
other path is a 404. No body is read; the connection is always closed.

Every server binds a FREE port picked by the kernel, never 8780, on 127.0.0.1.
HOME and VOICEMODE_BASE_DIR are a tmp dir; the state poll never starts.
"""
import os, socket, threading, time

import pytest

from talk import server, token_store
from tests.test_talk_host_check import raw

TS = "talk.example.ts.net"


@pytest.fixture
def room(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm"))
    monkeypatch.delenv("EARS_HOME", raising=False)
    (tmp_path / "home").mkdir()
    return token_store.load_or_create()


@pytest.fixture
def talk(room):
    """start(allow=None) -> the bound port of a running server; the token is room's."""
    servers = []

    def start(allow=None):
        server.configure(allow_hosts=allow)
        srv = server.make_server("127.0.0.1", 0)
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


def post(port, path="/pair", host=None, token=None, headers=(), body=b"", after=b""):
    """One POST over a raw socket, read to EOF. token=None sends no X-Talk-Token.
    Returns (status, [(name, value), ...] in order, body, closed): every header line is
    kept, so a test can count Set-Cookie lines; closed is True when the server hung up."""
    lines = [f"POST {path} HTTP/1.1", f"Host: {host or f'127.0.0.1:{port}'}"]
    if token is not None:
        lines.append(f"X-Talk-Token: {token}")
    lines += [f"{k}: {v}" for k, v in headers]
    if body:
        lines.append(f"Content-Length: {len(body)}")
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body + after)
        data, closed = b"", False
        while True:
            try:
                chunk = s.recv(65536)
            except socket.timeout:
                break
            if not chunk:
                closed = True
                break
            data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    status_line, *hdrs = head.decode("latin-1").split("\r\n")
    pairs = [(k.strip().lower(), v.strip()) for k, _, v in (h.partition(":") for h in hdrs)]
    return int(status_line.split()[1]), pairs, rest, closed


def header(pairs, name):
    return [v for k, v in pairs if k == name]


def no_cookie(resp, status=403):
    got, pairs, body, closed = resp
    assert got == status, resp
    assert header(pairs, "set-cookie") == []
    assert closed and header(pairs, "connection") == ["close"]
    return body


# -- the cookie, exactly -------------------------------------------------------

def test_loopback_pairing_sets_the_exact_cookie_with_no_secure(talk, room):
    port = talk()
    status, pairs, body, closed = post(port, token=room)
    assert status == 204 and body == b"" and closed
    assert header(pairs, "set-cookie") == [
        f"talk_token={room}; Max-Age=34560000; HttpOnly; SameSite=Strict"]
    assert header(pairs, "cache-control") == ["no-store"]
    assert header(pairs, "connection") == ["close"]
    assert header(pairs, "content-length") == []          # a 204 carries no Content-Length (RFC 9110 8.6)


def test_a_same_origin_http_post_gets_no_secure(talk, room):
    port = talk()
    _, pairs, _, _ = post(port, token=room, headers=[("Origin", f"http://127.0.0.1:{port}")])
    assert header(pairs, "set-cookie") == [
        f"talk_token={room}; Max-Age=34560000; HttpOnly; SameSite=Strict"]


@pytest.mark.parametrize("origin", [f"https://{TS}", f"HTTPS://{TS}", f"https://{TS}/"])
def test_an_https_origin_gets_secure(talk, room, origin):
    port = talk(allow={TS})
    status, pairs, _, _ = post(port, host=TS, token=room, headers=[("Origin", origin)])
    assert status == 204
    assert header(pairs, "set-cookie") == [
        f"talk_token={room}; Max-Age=34560000; HttpOnly; SameSite=Strict; Secure"]


@pytest.mark.parametrize("path", ["/pair", "/talk/pair"])
def test_never_a_path_or_a_domain(talk, room, path):
    port = talk(allow={TS})
    for host, hs in ((None, []), (TS, [("Origin", f"https://{TS}")])):
        _, pairs, _, _ = post(port, path=path, host=host, token=room, headers=hs)
        (cookie,) = header(pairs, "set-cookie")
        attrs = [a.strip().split("=")[0].lower() for a in cookie.split(";")[1:]]
        assert "path" not in attrs and "domain" not in attrs


def test_the_cookie_pairs_the_browser(talk, room):
    """GET pair with the cookie the POST returned: 204. Without it: the gate's 403."""
    port = talk()
    _, pairs, _, _ = post(port, token=room)
    (cookie,) = header(pairs, "set-cookie")
    pair = cookie.split(";")[0]
    status, hd, body = raw(port, "/pair", host=f"127.0.0.1:{port}", headers=[("Cookie", pair)], cookie=False)
    assert status == 204 and body == b""
    status, _, body = raw(port, "/events?n=1", host=f"127.0.0.1:{port}", headers=[("Cookie", pair)],
                          until=b"\n\n", cookie=False)
    assert status == 200 and body.startswith(b"event: config")
    assert raw(port, "/pair", host=f"127.0.0.1:{port}", cookie=False)[0] == 403


# -- refused: no Set-Cookie ----------------------------------------------------

@pytest.mark.parametrize("token", [None, "", "wrong", "{short}", "{token}x", "{token} x", "Bearer {token}"])
def test_a_wrong_or_missing_header_is_refused(talk, room, token):
    port = talk()
    t = None if token is None else token.format(token=room, short=room[:-1])
    body = no_cookie(post(port, token=t))
    assert body == b'talk: the pairing token does not match (run "talk pair" on the host)\n'


def test_two_token_headers_are_refused(talk, room):
    port = talk()
    no_cookie(post(port, token=room, headers=[("X-Talk-Token", room)]))
    no_cookie(post(port, token="wrong", headers=[("X-Talk-Token", room)]))


@pytest.mark.parametrize("where", ["body", "query", "cookie"])
def test_the_token_anywhere_but_the_header_is_refused(talk, room, where):
    port = talk()
    if where == "body":
        resp = post(port, body=f"talk_token={room}".encode(),
                    headers=[("Content-Type", "application/x-www-form-urlencoded")])
    elif where == "query":
        resp = post(port, path=f"/pair?talk_token={room}")
    else:
        resp = post(port, headers=[("Cookie", f"talk_token={room}")])
    no_cookie(resp)


@pytest.mark.parametrize("origin", ["https://attacker.example", "http://attacker.example:1", "null",
                                    f"https://{TS}.attacker.example"])
def test_a_foreign_origin_is_refused_by_the_host_check(talk, room, origin):
    port = talk(allow={TS})
    body = no_cookie(post(port, token=room, headers=[("Origin", origin)]))
    assert body.startswith(b"talk: Origin ") and b"--allow-host" in body


@pytest.mark.parametrize("host", ["attacker.example", "attacker.example:{port}", "127.0.0.1:1"])
def test_a_foreign_host_is_refused_by_the_host_check(talk, room, host):
    port = talk()
    body = no_cookie(post(port, host=host.format(port=port), token=room))
    assert body.startswith(b"talk: Host ") and b"--allow-host" in body


@pytest.mark.parametrize("path", ["/", "/index.html", "/manifest.json", "/events", "/avatar/ivy", "/nope",
                                  "/pair/", "/pairing", "/talk"])
def test_post_to_any_other_path_is_a_404_with_no_cookie(talk, room, path):
    port = talk()
    no_cookie(post(port, path=path, token=room), status=404)


@pytest.mark.parametrize("host", ["localhost:{port}", "LOCALHOST:{port}", "localhost.:{port}"])
def test_localhost_pairing_is_refused_naming_127(talk, room, host):
    """T7b: a cookie set at localhost goes to whatever answers localhost, which may be another
    user's listener on [::1]. So pairing at localhost is refused, even with the right token."""
    port = talk()
    body = no_cookie(post(port, host=host.format(port=port), token=room))
    assert body == (f"talk: this browser cannot pair at localhost "
                    f"(open http://127.0.0.1:{port}/ and pair there)\n").encode()


@pytest.mark.parametrize("name", ["foo.localhost", "a.b.LOCALHOST", "talk.localhost."])
def test_a_localhost_subdomain_pairing_is_refused(talk, room, name):
    """*.localhost goes to loopback, [::1] included (RFC 6761 6.3), so it is T7b like localhost
    (S6 add-on, matching talk pair's 0008a91). It needs --allow-host to pass check_host at all."""
    port = talk(allow={name.lower().rstrip(".")})
    body = no_cookie(post(port, host=f"{name}:{port}", token=room))
    assert body == (f"talk: this browser cannot pair at localhost "
                    f"(open http://127.0.0.1:{port}/ and pair there)\n").encode()


def test_a_name_that_merely_contains_localhost_still_pairs(talk, room):
    port = talk(allow={"localhostx.example"})
    status, pairs, _, _ = post(port, host="localhostx.example", token=room)
    assert status == 204 and header(pairs, "set-cookie")


def test_localhost_still_serves_the_page(talk, room):
    """The refusal is pairing's only: GET at localhost is EARS-336's, unchanged."""
    port = talk()
    assert raw(port, "/", host=f"localhost:{port}", cookie=False)[0] == 200


# -- the token file at run time --------------------------------------------------

def test_a_rotated_token_pairs_and_the_old_one_does_not(talk, room):
    port = talk()
    token_store.rotate()
    new = token_store.load()
    no_cookie(post(port, token=room))
    status, pairs, _, _ = post(port, token=new)
    assert status == 204 and header(pairs, "set-cookie")[0].startswith(f"talk_token={new};")


def test_a_deleted_token_pairs_nothing_and_is_not_remade(talk, room, capsys):
    port = talk()
    os.unlink(token_store.path())
    no_cookie(post(port, token=room))
    assert not token_store.path().exists()
    err = capsys.readouterr().err
    assert "the pairing token does not match; token: no token file at" in err
    assert room not in err


# -- no body read; the connection is closed ------------------------------------------

def test_no_body_is_read_and_the_connection_closes(talk, room):
    """A body and a pipelined second request after it: one answer, then EOF. The body is
    never parsed as a request, and the second request is never served."""
    port = talk()
    after = f"GET /events?n=1 HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nCookie: talk_token={room}\r\n\r\n".encode()
    status, pairs, rest, closed = post(port, token=room, body=b"GET /nope HTTP/1.1\r\n\r\n", after=after)
    assert status == 204 and closed
    assert rest == b""                                    # nothing after the 204: no second response
    assert len(header(pairs, "set-cookie")) == 1


def test_other_methods_are_unchanged(talk, room):
    port = talk()
    for method in ("PUT", "DELETE", "HEAD"):
        with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
            s.sendall(f"{method} /pair HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nX-Talk-Token: {room}\r\n\r\n"
                      .encode())
            head = s.recv(65536)
        assert head.startswith(b"HTTP/1.1 501") and b"set-cookie" not in head.lower()


# -- never the token in stderr ---------------------------------------------------

def test_the_token_never_reaches_stderr(talk, room, capsys):
    port = talk(allow={TS})
    post(port, token=room)
    post(port, path="/talk/pair", host=TS, token=room, headers=[("Origin", f"https://{TS}")])
    post(port, token=room + "x")
    post(port, token=room[:-1])
    post(port, host=f"localhost:{port}", token=room)
    post(port, path="/nope", token=room)
    post(port, token=room, headers=[("Origin", "https://attacker.example")])
    post(port, path=f"/pair?talk_token={room}")
    time.sleep(0.1)
    err = capsys.readouterr().err
    assert room not in err and room[:20] not in err
    assert err.count('"POST /pair HTTP/1.1" 204') == 1
    assert "403 refused: the pairing token does not match" in err
    assert "403 refused: this browser cannot pair at localhost" in err


def test_pair_cookie():
    assert server.pair_cookie("t", False) == "talk_token=t; Max-Age=34560000; HttpOnly; SameSite=Strict"
    assert server.pair_cookie("t", True).endswith("; SameSite=Strict; Secure")
    assert server.COOKIE_MAX_AGE == 400 * 24 * 3600
