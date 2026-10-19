"""The gate: every talk route but the page and the manifest needs a paired browser (EARS-338, S2).

A paired browser sends `Cookie: talk_token=<the per-install token>`. Without it,
/events, the avatars, unknown paths and GET pair get a plain-text 403 that names
`talk pair`, with no transcript bytes and no event stream. The token is never
taken from a query, never logged, and an open stream ends within one keep-alive
of a rotation.

Every server binds a FREE port picked by the kernel, never 8780, on 127.0.0.1.
HOME and VOICEMODE_BASE_DIR are a tmp dir; the state poll never starts.
"""
import json, os, socket, threading, time
from datetime import date
from unittest import mock

import pytest

from talk import cli, server, token_store
from tests._anchor import ANCHOR
from tests.test_talk_host_check import raw

SECRET = "the words the microphone heard"
AVATAR = b"\xff\xd8\xff fake jpeg"
GATED = ["/events?n=1", "/avatar/ivy", "/nope", "/pair", "/talk/events", "/talk/pair", "/talk/avatar/ivy"]
OPEN = [("/", "text/html"), ("/index.html", "text/html"), ("/manifest.json", "application/manifest+json"),
        ("/talk/", "text/html"), ("/talk/manifest.json", "application/manifest+json")]


@pytest.fixture
def room(tmp_path, monkeypatch):
    """A fenced install: a heard log holding one turn, a face, and the token."""
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
    return token_store.load_or_create()


@pytest.fixture
def talk(room):
    """A running server on a free port; yields (port, token)."""
    server.configure()
    srv = server.make_server("127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, args=(0.05,), daemon=True).start()
    try:
        yield srv.server_address[1], room
    finally:
        srv.shutdown()
        srv.server_close()
        server.configure()


def req(port, path, cookie=None, headers=(), until=None):
    """GET with a loopback Host; cookie is the whole Cookie header value, or None for none."""
    hs = list(headers) + ([("Cookie", cookie)] if cookie is not None else [])
    if until is None and ("/events" in path):
        until = b"\n\n"
    return raw(port, path, host=f"127.0.0.1:{port}", headers=hs, until=until, cookie=False)


def unpaired(resp):
    """The gate's refusal: 403, plain text naming `talk pair`, nothing of the room."""
    status, hd, body = resp
    assert status == 403, resp
    assert hd["content-type"] == "text/plain; charset=utf-8"
    assert hd["cache-control"] == "no-store" and hd["connection"] == "close"
    assert int(hd["content-length"]) == len(body)
    assert body == b'talk: this browser is not paired (run "talk pair" on the host)\n'
    assert SECRET.encode() not in body and AVATAR not in body
    assert b"event:" not in body and b"data:" not in body
    assert "text/event-stream" not in hd.get("content-type", "") and "x-accel-buffering" not in hd
    assert "set-cookie" not in hd
    return body


# -- refused: no cookie, a wrong one, or the token only in the query ------------

@pytest.mark.parametrize("path", GATED)
def test_no_cookie_refused(talk, path):
    port, _ = talk
    unpaired(req(port, path))


@pytest.mark.parametrize("path", GATED)
@pytest.mark.parametrize("cookie", [
    "talk_token=wrong",
    "talk_token=",
    "talk_token",
    "a=b; vm_session=x",
    "talk_token={short}",          # the token less its last character
    "talk_token={token}x",         # ... and with one more
    "Talk_Token={token}",          # the name is exact
    "xtalk_token={token}",
    'talk_token="{token}"',        # quoted is another value
])
def test_wrong_cookie_refused(talk, path, cookie):
    port, token = talk
    unpaired(req(port, path, cookie=cookie.format(token=token, short=token[:-1])))


@pytest.mark.parametrize("path", ["/events?n=1&talk_token={token}", "/avatar/ivy?talk_token={token}",
                                  "/pair?talk_token={token}", "/nope?talk_token={token}",
                                  "/events?talk_token={token}&n=1"])
def test_token_only_in_the_query_refused(talk, path):
    port, token = talk
    unpaired(req(port, path.format(token=token)))


def test_token_in_another_header_refused(talk):
    port, token = talk
    unpaired(req(port, "/events?n=1", headers=[("X-Talk-Token", token), ("Authorization", f"Bearer {token}")]))


# -- served: the page and the manifest open; the rest with the cookie --------------

@pytest.mark.parametrize("path, ctype", OPEN)
def test_page_and_manifest_served_with_no_cookie(talk, path, ctype):
    port, _ = talk
    status, hd, body = req(port, path)
    assert status == 200 and hd["content-type"].startswith(ctype)
    assert body == open(os.path.join(server.HERE, "manifest.json" if "manifest" in path else "index.html"),
                        "rb").read()


def test_right_cookie_among_others_streams_events_config_first(talk):
    port, token = talk
    status, hd, body = req(port, "/events?n=5", cookie=f"a=b; talk_token={token}; vm_session=x",
                           until=SECRET.encode())
    assert status == 200 and hd["content-type"] == "text/event-stream"
    assert body.startswith(b"event: config\n")
    assert SECRET.encode() in body


@pytest.mark.parametrize("cookie", [
    "talk_token={token}",
    "talk_token={token};",
    " talk_token = {token} ",
    "a=b;talk_token={token}",
    "junk; =x; ;; talk_token=wrong; talk_token={token}",   # a stale cookie beside the right one
    "a=\"unclosed; talk_token={token}",                     # another app's junk cannot break the parse
])
def test_cookie_split_by_hand(talk, cookie):
    port, token = talk
    assert req(port, "/events?n=1", cookie=cookie.format(token=token))[0] == 200


def test_cookie_in_a_second_cookie_header(talk):
    port, token = talk
    status, _, _ = req(port, "/events?n=1", headers=[("Cookie", "a=b"), ("Cookie", f"talk_token={token}")])
    assert status == 200


def test_avatar_and_404_served_when_paired(talk):
    port, token = talk
    status, hd, body = req(port, "/avatar/ivy", cookie=f"talk_token={token}")
    assert status == 200 and body == AVATAR
    assert req(port, "/nope", cookie=f"talk_token={token}")[0] == 404
    assert req(port, "/avatar/nobody", cookie=f"talk_token={token}")[0] == 404


@pytest.mark.parametrize("path", ["/pair", "/talk/pair"])
def test_get_pair_is_the_probe(talk, path):
    port, token = talk
    status, hd, body = req(port, path, cookie=f"talk_token={token}")
    assert status == 204 and body == b""
    assert hd["cache-control"] == "no-store"
    assert "content-length" not in hd                     # RFC 9110 8.6: never in a 204 (S6 add-on)
    assert "set-cookie" not in hd                         # the probe answers; it never pairs
    unpaired(req(port, path))


def test_the_host_check_runs_before_the_gate(talk):
    """A foreign Host gets EARS-336's refusal, paired or not, on open routes too."""
    port, token = talk
    for cookie in (None, f"talk_token={token}"):
        for path in ("/", "/events?n=1", "/pair"):
            hs = [("Cookie", cookie)] if cookie else []
            status, _, body = raw(port, path, host=f"attacker.example:{port}", headers=hs, cookie=False)
            assert status == 403 and b"--allow-host" in body and b"talk pair" not in body


# -- the token file at run time: missing or refused, nothing matches -------------

def test_a_deleted_token_refuses_everything_and_is_not_remade(talk, capsys):
    port, token = talk
    assert req(port, "/events?n=1", cookie=f"talk_token={token}")[0] == 200
    os.unlink(token_store.path())
    unpaired(req(port, "/events?n=1", cookie=f"talk_token={token}"))
    unpaired(req(port, "/pair", cookie=f"talk_token={token}"))
    assert not token_store.path().exists()
    assert req(port, "/", cookie=f"talk_token={token}")[0] == 200      # the page still loads
    err = capsys.readouterr().err
    assert "no token file at" in err and "talk token --rotate" in err
    assert token not in err


def test_a_widened_token_refuses_and_the_log_says_why(talk, capsys):
    port, token = talk
    os.chmod(token_store.path(), 0o644)
    unpaired(req(port, "/events?n=1", cookie=f"talk_token={token}"))
    err = capsys.readouterr().err
    assert "0644" in err and str(token_store.path()) in err
    assert token not in err


def test_a_rotated_token_is_used_with_no_restart(talk):
    port, old = talk
    token_store.rotate()
    new = token_store.load()
    unpaired(req(port, "/events?n=1", cookie=f"talk_token={old}"))
    assert req(port, "/events?n=1", cookie=f"talk_token={new}")[0] == 200


# -- never the token in stderr ---------------------------------------------------

def test_the_token_never_reaches_stderr(talk, capsys):
    port, token = talk
    req(port, "/events?n=1", cookie=f"talk_token={token}")
    req(port, "/avatar/ivy", cookie=f"a=b; talk_token={token}")
    req(port, "/pair", cookie=f"talk_token={token}")
    req(port, f"/events?n=1&talk_token={token}")
    req(port, f"/pair?talk_token={token}&x=1")
    req(port, f"/talk/nope?TALK_TOKEN={token}")
    req(port, "/events?n=1", cookie=f"talk_token={token}x")
    req(port, "/nope", headers=[("X-Talk-Token", token)])
    time.sleep(0.2)                                       # the /events handlers log from their threads
    err = capsys.readouterr().err
    assert token not in err and token[:20] not in err
    assert "<redacted>" in err                            # the query token was logged blanked, not raw
    assert err.count("403 refused: this browser is not paired") >= 4


# -- rotation ends an open stream within one keep-alive --------------------------

def open_stream(port, token):
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.sendall((f"GET /events?n=1 HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
               f"Cookie: talk_token={token}\r\n\r\n").encode())
    data = b""
    while b"event: backfill" not in data:
        data += s.recv(65536)
    return s, data


def read_until_closed(s, limit):
    """Read until EOF; returns (data, seconds taken) or raises if the stream is still open at `limit`."""
    s.settimeout(0.2)
    data, t0 = b"", time.monotonic()
    while time.monotonic() - t0 < limit:
        try:
            chunk = s.recv(65536)
        except socket.timeout:
            continue
        if not chunk:
            return data, time.monotonic() - t0
        data += chunk
    raise AssertionError(f"the stream was still open after {limit}s: {data[-200:]!r}")


def test_rotation_ends_an_open_stream_within_one_keepalive(talk, monkeypatch):
    port, token = talk
    monkeypatch.setattr(server, "KEEPALIVE", 0.3)
    s, head = open_stream(port, token)
    try:
        assert head.startswith(b"HTTP/1.1 200") and b"event: config" in head
        s.settimeout(3)
        got = b""
        while got.count(b": ping") < 2:                  # still paired: keep-alives keep coming
            got += s.recv(65536)
        token_store.rotate()
        data, took = read_until_closed(s, limit=3)
        assert took < 0.3 + 0.25 + 1.0                    # one keep-alive, one poll tick, and slack
    finally:
        s.close()


def test_a_stream_stays_open_while_the_token_is_unchanged(talk, monkeypatch):
    port, token = talk
    monkeypatch.setattr(server, "KEEPALIVE", 0.2)
    s, _ = open_stream(port, token)
    try:
        s.settimeout(3)
        got = b""
        while got.count(b": ping") < 4:
            chunk = s.recv(65536)
            assert chunk, "the stream ended with the token unchanged"
            got += chunk
    finally:
        s.close()


def test_keepalive_is_a_module_constant():
    assert server.KEEPALIVE == 15


# -- route(): one table for the dispatcher and the gate --------------------------

@pytest.mark.parametrize("path, name", [
    ("/events", "events"), ("/talk/events", "events"),
    ("/", "page"), ("/index.html", "page"), ("/talk/", "page"), ("/talk/index.html", "page"),
    ("/manifest.json", "manifest"), ("/talk/manifest.json", "manifest"),
    ("/avatar/ivy", "avatar"), ("/talk/avatar/ivy", "avatar"), ("/avatar/pair", "avatar"),
    ("/pair", "pair"), ("/talk/pair", "pair"),
    ("/nope", "404"), ("/talk", "404"), ("", "404"), ("/pair/", "page"), ("/events/x", "404"),
])
def test_route(path, name):
    assert server.route(path) == name


def test_only_the_page_and_manifest_are_open():
    assert server.OPEN_ROUTES == {"page", "manifest"}


def test_cookie_values():
    assert server.cookie_values(None) == []
    assert server.cookie_values(["a=b; talk_token=x; talk_token = y ;c"]) == ["x", "y"]
    assert server.cookie_values(["a=b", "talk_token=z"]) == ["z"]
    assert server.cookie_values(["talk_token; =q; talk_tokenx=1"]) == []


# -- serve(): the token before anything binds; a banner with no token ------------

class FakeServer:
    server_address = ("127.0.0.1", 0)

    def serve_forever(self):
        return None


def test_serve_makes_the_token_first_and_never_prints_it(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm"))
    order = []
    monkeypatch.setattr(server, "make_server",
                        lambda h, p: order.append(token_store.path().exists()) or FakeServer())
    monkeypatch.setattr(server, "poll_state", lambda: None)
    try:
        server.serve("127.0.0.1", 0)
    finally:
        server.configure()
    assert order == [True]                                # the token existed before the bind
    token = token_store.load()
    out = capsys.readouterr()
    assert token not in out.out and token not in out.err
    assert "talk pair" in out.out and str(token_store.path()) in out.out


@pytest.mark.parametrize("spoil", ["mode", "dir", "content"])
def test_a_refused_token_exits_2_before_binding(tmp_path, monkeypatch, capsys, spoil):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm"))
    token = token_store.load_or_create()
    p = token_store.path()
    if spoil == "mode":
        os.chmod(p, 0o644)
    elif spoil == "dir":
        os.chmod(p.parent, 0o755)
    else:
        p.write_text("not a token\n")
    bound = []
    monkeypatch.setattr(server, "make_server", lambda h, port: bound.append(port) or FakeServer())
    monkeypatch.setattr(server, "poll_state", lambda: None)
    try:
        assert cli.main(["serve", "--port", "1"]) == 2
    finally:
        server.configure()
    assert bound == []
    out = capsys.readouterr()
    assert out.err.startswith("talk serve: token ") and str(p.parent) in out.err
    assert token not in out.err and token not in out.out
