"""`talk serve`: the console script's parsing, its default bind, and the page it
serves from package data (EARS-242 do-002).

Every server here binds a FREE port picked by the kernel, never 8780 (the live
talk-web), and the state poll is replaced by a no-op, so nothing runs
`session list` or reads a seat's heard.state.
"""
import http.client, json, os, socket, subprocess, sys, threading
from importlib import resources
from unittest import mock

import pytest

from talk import cli, server, token_store


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_serve_defaults(no_talk_env):
    args = cli.parser().parse_args(["serve"])
    assert (args.host, args.port) == ("127.0.0.1", 8780)
    assert (args.person, args.smart_turn, args.settings_url, args.call_url) == (None, None, None, None)


@pytest.fixture
def no_talk_env(monkeypatch):
    for k in ("TALK_PERSON", "TALK_SMART_TURN_DIR", "TALK_SETTINGS_URL", "TALK_CALL_URL", "TALK_ALLOW_HOSTS"):
        monkeypatch.delenv(k, raising=False)


def test_serve_flags_pass_through(no_talk_env):
    with mock.patch.object(server, "serve") as serve:
        assert cli.main(["serve", "--host", "::1", "--port", "9999"]) == 0
    serve.assert_called_once_with("::1", 9999, person=None, smart_dir=None, settings_url=None, call_url=None,
                                  allow_hosts=frozenset())


def test_settings_flags_pass_through(no_talk_env):
    with mock.patch.object(server, "serve") as serve:
        assert cli.main(["serve", "--person", "Zed", "--smart-turn", "/x/smart",
                         "--settings-url", "/settings/", "--call-url", "https://host1.t.ts.net:8775/"]) == 0
    serve.assert_called_once_with("127.0.0.1", 8780, person="Zed", smart_dir="/x/smart",
                                  settings_url="/settings/", call_url="https://host1.t.ts.net:8775/",
                                  allow_hosts=frozenset())


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("TALK_PERSON", "Ana")
    monkeypatch.setenv("TALK_SMART_TURN_DIR", "/y/smart")
    monkeypatch.setenv("TALK_SETTINGS_URL", "https://s/")
    monkeypatch.setenv("TALK_CALL_URL", "https://c/")
    args = cli.parser().parse_args(["serve"])
    assert (args.person, args.smart_turn, args.settings_url, args.call_url) == \
        ("Ana", "/y/smart", "https://s/", "https://c/")


# -- the phone button's URL (EARS-317): checked at start, before anything binds ----

GOOD_CALL_URLS = [
    "https://host1.tail1234.ts.net:8775/",          # PHONE-139 design.md 5.1: the browser-facing URL
    "https://host2.tail1234.ts.net:8775/",
    "https://call.example/",
    "http://localhost:8775/",                    # development, same machine
    "http://127.0.0.1:8775/",
    "http://localhost/",
    "https://host1.tail1234.ts.net:8775/?a=1#f",    # byte-for-byte: Talk does not rewrite what it was given
]

BAD_CALL_URLS = [
    "javascript:alert(1)",                       # script on Talk's page (the reason for the check)
    "JavaScript:alert(document.cookie)",
    "data:text/html,<script>alert(1)</script>",
    "http://host1.tail1234.ts.net:8775/",           # plain http: to a host that is not this machine
    "http://example.com/",
    "http://localhost.evil.com/",
    "http://127.0.0.1.evil.com/",
    "http://localhost@evil.com/",                # the host is evil.com, the "localhost" is userinfo
    "https://user:pw@host1.tail1234.ts.net/",       # credentials: Talk holds the URL and nothing else
    "ftp://host1/",
    "file:///etc/passwd",
    "//host1.tail1234.ts.net/",                     # scheme-relative
    "/call/",                                    # relative
    "https://",                                  # no host
    "https:host1",
    "https://host1:notaport/",
    "https://host1:99999/",
    " https://host1/",                              # whitespace, which a browser would strip before javascript:
    "https://host1/\njavascript:alert(1)",
    "\tjavascript:alert(1)",
    "not a url",
]


@pytest.mark.parametrize("url", GOOD_CALL_URLS)
def test_call_url_accepted_byte_for_byte(no_talk_env, url):
    assert server.check_call_url(url) == url
    with mock.patch.object(server, "serve") as serve:
        assert cli.main(["serve", "--call-url", url]) == 0
    assert serve.call_args.kwargs["call_url"] == url


@pytest.mark.parametrize("url", GOOD_CALL_URLS[:5])
def test_call_url_accepted_from_env(monkeypatch, url):
    monkeypatch.setenv("TALK_CALL_URL", url)
    with mock.patch.object(server, "serve") as serve:
        assert cli.main(["serve"]) == 0
    assert serve.call_args.kwargs["call_url"] == url


def test_call_url_unset_or_empty_is_off(no_talk_env, monkeypatch):
    assert server.check_call_url(None) is None and server.check_call_url("") is None
    monkeypatch.setenv("TALK_CALL_URL", "")
    with mock.patch.object(server, "serve") as serve:
        assert cli.main(["serve"]) == 0
    assert serve.call_args.kwargs["call_url"] is None


@pytest.mark.parametrize("via", ["flag", "env"])
@pytest.mark.parametrize("url", BAD_CALL_URLS)
def test_call_url_refused_at_start(no_talk_env, monkeypatch, capsys, via, url):
    """Refused before serve(): non-zero, a clear message on stderr, nothing bound."""
    with pytest.raises(ValueError):
        server.check_call_url(url)
    argv = ["serve", "--port", "1"]
    if via == "flag":
        argv += ["--call-url", url]
    else:
        monkeypatch.setenv("TALK_CALL_URL", url)
    with mock.patch.object(server, "serve") as serve, \
            mock.patch.object(server, "make_server") as make:
        assert cli.main(argv) == 2
    serve.assert_not_called()
    make.assert_not_called()
    err = capsys.readouterr().err
    assert "TALK_CALL_URL" in err and "--call-url" in err and "refused" in err
    assert "https:" in err or "credentials" in err or "not a URL" in err


def test_call_url_refused_by_configure_too(no_talk_env):
    """serve() and configure() check it as well, so a caller past the CLI cannot bind with it."""
    before = dict(server.SETTINGS)
    with mock.patch.object(server, "make_server") as make:
        with pytest.raises(ValueError):
            server.serve("127.0.0.1", 1, call_url="javascript:alert(1)")
    make.assert_not_called()
    assert server.SETTINGS == before


@pytest.mark.parametrize("via", ["flag", "env"])
def test_call_url_refused_by_the_real_command(via):
    """`python -m talk serve` with a javascript: call URL exits non-zero, says why, and never binds."""
    port = free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith("TALK_")}
    argv = [sys.executable, "-m", "talk", "serve", "--port", str(port)]
    if via == "flag":
        argv += ["--call-url", "javascript:alert(1)"]
    else:
        env["TALK_CALL_URL"] = "javascript:alert(1)"
    out = subprocess.run(argv, capture_output=True, text=True, timeout=30, env=env)
    assert out.returncode == 2
    assert "refused" in out.stderr and "javascript:alert(1)" in out.stderr
    assert "serving" not in out.stdout
    with socket.socket() as s:                   # the port is still free: nothing was served on it
        s.bind(("127.0.0.1", port))


def test_no_command_is_usage():
    with mock.patch.object(server, "serve") as serve:
        assert cli.main([]) == 2
    serve.assert_not_called()


def test_bad_port_refused():
    with pytest.raises(SystemExit) as e:
        cli.main(["serve", "--port", "eighty"])
    assert e.value.code == 2


def test_package_data_present():
    """index.html and manifest.json ship inside the package, beside server.py."""
    pkg = resources.files("talk")
    assert (pkg / "index.html").is_file()
    assert json.loads((pkg / "manifest.json").read_text())["name"] == "Talk"
    assert os.path.dirname(os.path.abspath(server.__file__)) == server.HERE


@pytest.fixture
def running(tmp_path, monkeypatch):
    """`talk serve --port <free>` in a thread, default host; yields the server. HOME and
    VOICEMODE_BASE_DIR are fenced: serve() makes the install's token (EARS-338), and
    under the real HOME that would write ~/.voicemode/talk/token (tests/home_guard.py)."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm"))
    port = free_port()
    made, ready = [], threading.Event()
    real_make = server.make_server

    def make(host, p):
        made.append(real_make(host, p))
        ready.set()
        return made[-1]

    with mock.patch.object(server, "poll_state", lambda: None), \
            mock.patch.object(server, "make_server", make):
        t = threading.Thread(target=cli.main, args=(["serve", "--port", str(port)],), daemon=True)
        t.start()
        assert ready.wait(5), "talk serve never bound"
        srv = made[0]
        try:
            yield srv, port
        finally:
            srv.shutdown()
            srv.server_close()
            t.join(5)


def get(port, path, cookie=False):
    """cookie=True sends the fenced install's pairing cookie (EARS-338)."""
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        c.request("GET", path, headers={"Cookie": f"{server.COOKIE}={token_store.load()}"} if cookie else {})
        r = c.getresponse()
        return r.status, r.getheader("Content-Type"), r.read()
    finally:
        c.close()


def test_serve_binds_loopback_by_default(running):
    srv, port = running
    assert srv.server_address[:2] == ("127.0.0.1", port)


def test_serve_returns_the_page_and_manifest(running):
    _, port = running
    status, ctype, body = get(port, "/")
    assert status == 200 and ctype.startswith("text/html")
    assert body == (resources.files("talk") / "index.html").read_bytes()
    status, ctype, body = get(port, "/manifest.json")
    assert status == 200 and ctype == "application/manifest+json"
    assert json.loads(body)["short_name"] == "Talk"
    assert get(port, "/nope", cookie=True)[0] == 404      # paired: an unknown path is a 404
    assert get(port, "/nope")[0] == 403                   # unpaired: the gate answers first (EARS-338)


def test_module_entry_point_help():
    """`python -m talk serve --help` names the flags and the loopback default."""
    out = subprocess.run([sys.executable, "-m", "talk", "serve", "--help"],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0
    assert "--port" in out.stdout and "--host" in out.stdout and "127.0.0.1" in out.stdout
    assert "--call-url" in out.stdout and "TALK_CALL_URL" in out.stdout


# -- talk pair and talk token --rotate (EARS-338 S5) -------------------------------------
# Every test here fences HOME and VOICEMODE_BASE_DIR (the `install` fixture), so the token
# is a scratch one. A test that provokes a refusal checks that the token is in neither
# stdout nor stderr.

@pytest.fixture
def install(tmp_path, monkeypatch):
    """A fenced install with its token made; yields the token."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm"))
    return token_store.load_or_create()


@pytest.fixture
def tty(monkeypatch):
    """stdout is a terminal: cli.stdout_is_terminal (sys.stdout.isatty) faked true. Faked on
    the helper, because pytest swaps sys.stdout for its capture after fixtures run."""
    monkeypatch.setattr(cli, "stdout_is_terminal", lambda: True)


def test_stdout_is_terminal_asks_isatty(capsys):
    with mock.patch.object(sys.stdout, "isatty", return_value=True):
        assert cli.stdout_is_terminal() is True
    with mock.patch.object(sys.stdout, "isatty", return_value=False):
        assert cli.stdout_is_terminal() is False


def never(token, out):
    assert token not in out.out and token not in out.err
    assert token[:20] not in out.out + out.err


def test_pair_prints_the_link_with_the_file_token(install, tty, capsys):
    assert cli.main(["pair", "--url", "http://x.example/talk"]) == 0
    assert capsys.readouterr().out == f"http://x.example/talk/#pair={token_store.load()}\n"


@pytest.mark.parametrize("url, base", [
    (None, "http://127.0.0.1:8780/"),                               # the default, printed, never fetched
    ("http://127.0.0.1:8781", "http://127.0.0.1:8781/"),
    ("http://127.0.0.1:8781/", "http://127.0.0.1:8781/"),            # the slash is never doubled
    ("https://machine.example.ts.net/talk", "https://machine.example.ts.net/talk/"),
    ("HTTPS://machine.example.ts.net/talk/", "HTTPS://machine.example.ts.net/talk/"),
])
def test_pair_url_forms(install, tty, capsys, url, base):
    assert cli.main(["pair"] + (["--url", url] if url else [])) == 0
    assert capsys.readouterr().out == f"{base}#pair={install}\n"


@pytest.mark.parametrize("url", ["http://localhost:8780/", "http://LOCALHOST:8780", "https://localhost/talk",
                                 "http://localhost.:8780/"])
def test_pair_refuses_localhost_naming_127(install, tty, capsys, url):
    assert cli.main(["pair", "--url", url]) == 2
    out = capsys.readouterr()
    assert out.out == "" and "localhost" in out.err and "127.0.0.1" in out.err
    never(install, out)


@pytest.mark.parametrize("url", ["http://foo.localhost:8780/", "http://talk.LOCALHOST:8780/x",
                                 "http://a.b.localhost./"])
def test_pair_refuses_a_localhost_subdomain(install, tty, capsys, url):
    """RFC 6761 6.3: browsers send *.localhost to loopback, [::1] included, so it is T7b too."""
    assert cli.main(["pair", "--url", url]) == 2
    out = capsys.readouterr()
    assert out.out == "" and "localhost" in out.err and "127.0.0.1" in out.err
    never(install, out)


@pytest.mark.parametrize("url", ["http://[::1]:8780/", "http://[0:0:0:0:0:0:0:1]:8780/",
                                 "http://[::ffff:127.0.0.1]:8780/", "https://[::1]/talk",
                                 "http://[fe80::1]:8780/"])
def test_pair_refuses_an_ipv6_address(install, tty, capsys, url):
    """talk binds IPv4 only and its Host check refuses an IPv6 Host, so talk itself can never
    answer such a link: only another listener can, and its page would read the #pair= token."""
    assert cli.main(["pair", "--url", url]) == 2
    out = capsys.readouterr()
    assert out.out == "" and "IPv6" in out.err and "127.0.0.1" in out.err
    never(install, out)


def test_pair_still_takes_names_that_merely_contain_localhost(install, tty, capsys):
    for url in ("http://localhostx.example/", "http://my-localhost.example.ts.net/talk"):
        assert cli.main(["pair", "--url", url]) == 0
        assert capsys.readouterr().out == f"{url.rstrip('/')}/#pair={install}\n"


@pytest.mark.parametrize("url", ["ftp://x.example/", "javascript:alert(1)", "x.example/talk", "http:///talk",
                                 "http://x.example/talk?a=1", "http://x.example/talk#pair=x",
                                 "http://user:pw@x.example/", "http://x.example:99999/", ""])
def test_pair_refuses_anything_but_a_plain_http_url(install, tty, capsys, url):
    assert cli.main(["pair", "--url", url]) == 2
    out = capsys.readouterr()
    assert out.out == "" and out.err.startswith("talk pair: --url ")
    never(install, out)


def test_pair_refuses_when_stdout_is_not_a_terminal(install, capsys):
    with mock.patch.object(sys.stdout, "isatty", return_value=False):
        assert cli.main(["pair"]) == 2
    out = capsys.readouterr()
    assert out.out == "" and "not a terminal" in out.err
    never(install, out)


def test_pair_through_the_real_command_with_no_tty_prints_no_token(install):
    """The console path, with stdout a pipe (an agent's tool shell is the same)."""
    r = subprocess.run([sys.executable, "-m", "talk", "pair"], capture_output=True, text=True, timeout=30,
                       env=dict(os.environ))
    assert r.returncode == 2 and r.stdout == ""
    assert install not in r.stderr and "not a terminal" in r.stderr


def test_pair_never_makes_the_token(tmp_path, monkeypatch, tty, capsys):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm"))
    assert cli.main(["pair"]) == 2
    out = capsys.readouterr()
    assert out.out == "" and "no token yet" in out.err and "talk serve" in out.err
    assert not token_store.path().exists()


def test_pair_refuses_a_wide_token_file(install, tty, capsys):
    os.chmod(token_store.path(), 0o644)
    assert cli.main(["pair"]) == 2
    out = capsys.readouterr()
    assert out.out == "" and str(token_store.path()) in out.err and "0644" in out.err
    never(install, out)


def test_the_token_is_never_a_flag_or_an_env_var():
    """Another user can read argv (design M8), so there is no --token anywhere."""
    for verb in ("serve", "pair", "token"):
        with pytest.raises(SystemExit):
            cli.parser().parse_args([verb, "--token", "x"])
    assert "TALK_TOKEN" not in open(cli.__file__).read()


def test_token_rotate_changes_the_file_and_prints_no_token(install, capsys):
    assert cli.main(["token", "--rotate"]) == 0
    new = token_store.load()
    assert new != install and len(new) == 43
    out = capsys.readouterr()
    assert str(token_store.path()) in out.out and "talk pair" in out.out
    never(install, out)
    never(new, out)


def test_token_without_rotate_is_usage(install, capsys):
    assert cli.main(["token"]) == 2
    out = capsys.readouterr()
    assert "--rotate" in out.err
    assert token_store.load() == install                 # nothing changed
    never(install, out)


def test_token_rotate_refuses_a_wide_dir(install, capsys):
    os.chmod(token_store.path().parent, 0o755)
    assert cli.main(["token", "--rotate"]) == 2
    out = capsys.readouterr()
    assert str(token_store.path().parent) in out.err
    never(install, out)


def test_rotate_takes_effect_on_a_running_server_with_no_restart(running, capsys):
    """A server started before the rotation refuses the old cookie at once; the new one works."""
    _, port = running
    old = token_store.load()
    assert get(port, "/pair", cookie=True)[0] == 204
    assert cli.main(["token", "--rotate"]) == 0
    new = token_store.load()

    def probe(token):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            c.request("GET", "/pair", headers={"Cookie": f"{server.COOKIE}={token}"})
            return c.getresponse().status
        finally:
            c.close()
    assert probe(old) == 403
    assert probe(new) == 204


def test_serve_help_mentions_talk_pair():
    out = subprocess.run([sys.executable, "-m", "talk", "serve", "--help"],
                         capture_output=True, text=True, timeout=30)
    text = " ".join(out.stdout.split())                 # argparse wraps the epilog
    assert out.returncode == 0 and "talk pair" in text and "talk token --rotate" in text


def test_top_level_help_lists_pair_and_token():
    out = subprocess.run([sys.executable, "-m", "talk", "--help"], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0 and "pair" in out.stdout and "token" in out.stdout
