"""The prefix receipt: Talk behind the kit's front door at /talk/, with relative URLs only.

The front door serves a room at /<name>/, strips that prefix before it forwards, and adds
X-Sessionkit-Prefix: /talk. Talk does not read the prefix for its links: the page and the
manifest use relative URLs throughout, and the router matches on suffixes, so every link
resolves under whatever prefix the browser is at. These tests pin both halves:

1. Static: every URL that index.html and manifest.json carry is found, the set is exactly
   the one measured (so a new URL forces a look), and none is absolute except the two
   operator-given cfg.*_url links. Each resolves under http://h/talk/.
2. Through a kit socket: what the front door sends (the stripped path, the prefix header,
   X-Sessionkit-User) gets the page, the manifest and the event stream. Every page link,
   resolved under /talk/ and stripped as the front door strips it, reaches its route. The
   page's bytes are the same with and without the prefix header: there is no injection.
3. R6: the manifest link says crossorigin="use-credentials", so the browser's manifest fetch
   carries the front door's cookie instead of being sent to its login.
"""
import json, os, re
from html.parser import HTMLParser
from collections import Counter
from urllib.parse import urljoin, urlsplit

import pytest

from talk import server
from tests.test_talk_kit import FRONT, PAGE, SECRET, USER, kget, kit, room  # noqa: F401 (fixtures)

PREFIX = ("X-Sessionkit-Prefix", "/talk")
BASE = "http://h/talk/"                      # where the browser is, behind the front door
HTML = PAGE.decode("utf-8")
MANIFEST_BYTES = open(os.path.join(server.HERE, "manifest.json"), "rb").read()
MANIFEST = json.loads(MANIFEST_BYTES)

# The operator-given links (--settings-url, --call-url): absolute by design, not Talk's own.
OPERATOR_LINKS = {"cfg.settings_url", "cfg.call_url"}

# Every URL the page carries, as measured in design.md section 1 (sessionvoice 6be3de4).
# (kind, value): a quoted value is a string literal, a bare one an identifier.
MEASURED = Counter({
    ("href", "manifest.json"): 1,             # <link rel="manifest">
    ("src", "avatar/${k}"): 1,                # the faces
    ("href", "cfg.settings_url"): 1,          # operator link
    ("href", "cfg.call_url"): 1,              # operator link
    ("EventSource", "events?n=400&debug=1"): 1,
    ("EventSource", "events?n=150"): 1,
    ("fetch", "PAIR"): 2,                     # GET and POST pair
    ("PAIR", "pair"): 1,                      # const PAIR = "pair"
})

# A URL-bearing manifest member other than start_url would need its own look.
MANIFEST_URL_MEMBERS = {"start_url", "scope", "id", "icons", "shortcuts", "screenshots",
                        "share_target", "protocol_handlers", "related_applications", "file_handlers"}

VALUE = r"""(?:"([^"]*)"|'([^']*)'|([A-Za-z_$][\w.$]*))"""
PATTERNS = [
    ("href", re.compile(r"\bhref\s*=\s*" + VALUE)),
    ("src", re.compile(r"\bsrc\s*=\s*" + VALUE)),
    ("action", re.compile(r"\baction\s*=\s*" + VALUE)),
    ("fetch", re.compile(r"\bfetch\(\s*" + VALUE)),
    ("PAIR", re.compile(r"\bconst\s+PAIR\s*=\s*" + VALUE)),
]
EVENTSOURCE = re.compile(r"\bEventSource\(([^)]*)\)")
LITERAL = re.compile(r""""([^"]*)"|'([^']*)'""")
# Ways to emit a URL the parser does not read: none may appear at all.
UNREAD = re.compile(r"url\(|<base\b|\bWebSocket\(|XMLHttpRequest|sendBeacon|\bimport\(|<form\b[^>]*\baction")


def urls(html):
    """Every (kind, value) URL site in a page. A quoted value comes back unquoted; an
    identifier comes back as written."""
    found = Counter()
    for kind, pat in PATTERNS:
        for m in pat.finditer(html):
            found[(kind, next(g for g in m.groups() if g is not None))] += 1
    for m in EVENTSOURCE.finditer(html):
        for lit in LITERAL.finditer(m.group(1)):
            found[("EventSource", next(g for g in lit.groups() if g is not None))] += 1
    return found


def pair_value(found):
    (value,) = [v for (k, v) in found if k == "PAIR"]
    return value


def literal_urls(found):
    """The URL strings a browser would resolve: literals, with fetch(PAIR) read through
    its constant and ${k} filled in."""
    out = []
    for (kind, value), n in found.items():
        if kind == "PAIR" or value in OPERATOR_LINKS:
            continue
        if kind == "fetch" and value == "PAIR":
            value = pair_value(found)
        out += [value.replace("${k}", "someone")] * n
    return out


def absolute(u):
    """A URL that would not resolve under the browser's prefix: root-relative or with a
    scheme or host of its own."""
    s = urlsplit(u)
    return u.startswith("/") or bool(s.scheme) or bool(s.netloc)


def offenders(found):
    """The URL sites that would escape the prefix: absolute, and neither an operator
    link nor fetch(PAIR) (whose constant is a site of its own)."""
    return [(k, v) for (k, v) in found
            if not (k == "href" and v in OPERATOR_LINKS) and not (k == "fetch" and v == "PAIR")
            and absolute(v)]


def strip(url):
    """What the front door forwards for a browser URL under /talk/ (proxy.py room_target)."""
    s = urlsplit(url)
    assert s.path.startswith("/talk/"), url
    return s.path[len("/talk"):] + (f"?{s.query}" if s.query else "")


# -- 1. static: the page and the manifest carry relative URLs only ---------------------------

def test_the_parser_finds_exactly_the_measured_set():
    assert urls(HTML) == MEASURED


def test_the_page_has_no_url_the_parser_does_not_read():
    assert UNREAD.search(HTML) is None


def test_no_url_is_absolute_but_the_two_operator_links():
    found = urls(HTML)
    assert offenders(found) == []
    assert {(k, v) for (k, v) in found if v in OPERATOR_LINKS} == {("href", v) for v in OPERATOR_LINKS}
    assert {v for (k, v) in found if k == "fetch"} == {"PAIR"}


def test_every_page_url_resolves_under_the_prefix():
    us = literal_urls(urls(HTML))
    assert sorted(us) == sorted(["manifest.json", "avatar/someone", "events?n=400&debug=1",
                                 "events?n=150", "pair", "pair"])
    for u in us:
        assert urlsplit(urljoin(BASE, u)).path.startswith("/talk/"), u


def test_the_manifest_starts_under_the_prefix():
    assert MANIFEST_URL_MEMBERS & MANIFEST.keys() == {"start_url"}
    assert not absolute(MANIFEST["start_url"])
    assert urljoin(urljoin(BASE, "manifest.json"), MANIFEST["start_url"]) == BASE


def manifest_links(html):
    """The attributes of every <link rel="manifest"> in a page."""
    out = []

    class P(HTMLParser):
        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if tag == "link" and "manifest" in (a.get("rel") or "").lower().split():
                out.append(a)
    P().feed(html)
    return out


def test_the_manifest_is_fetched_with_credentials():
    """R6: a browser fetches a web-app manifest without cookies unless the link says
    use-credentials, and behind the front door a cookieless request is sent to its login.
    Same origin, so the attribute changes nothing when Talk is served on its own."""
    assert manifest_links(HTML) == [{"rel": "manifest", "href": "manifest.json",
                                     "crossorigin": "use-credentials"}]


@pytest.mark.parametrize("snippet", ['<link rel="manifest" href="manifest.json">',
                                     '<link rel="manifest" href="manifest.json" crossorigin>',
                                     '<link rel="manifest" href="manifest.json" crossorigin="anonymous">'])
def test_a_manifest_link_without_use_credentials_fails_r6(snippet):
    (a,) = manifest_links(snippet)
    assert a.get("crossorigin") != "use-credentials"


@pytest.mark.parametrize("snippet", [
    '<link rel="manifest" href="/manifest.json">',
    '<img src="https://cdn.example/x.png">',
    'fetch("/pair", {})',
    'new EventSource("/events?n=150")',
    "new EventSource(debug ? 'events?n=1' : '//elsewhere.example/events')",
    'const PAIR = "/pair";',
])
def test_an_absolute_url_would_fail_the_receipt(snippet):
    """The check bites: a future absolute URL is found, and found absolute."""
    found = urls(snippet)
    assert found and found != MEASURED and offenders(found), snippet


@pytest.mark.parametrize("snippet", ['<base href="/talk/">', "background: url(/x.png)",
                                     'new WebSocket("ws://h/x")', '<form action="/pair">'])
def test_a_url_the_parser_does_not_read_fails_the_receipt(snippet):
    assert UNREAD.search(snippet)


# -- 2. through a kit socket: what the front door sends -------------------------------------

def fget(url, headers=(USER, PREFIX), **kw):
    """A request as the front door forwards it: the browser's Host, the stripped path."""
    return kget(url, headers=headers, **kw)


def test_the_page_manifest_and_events_answer_under_the_prefix(kit):
    kit()
    status, hd, body = fget(strip(BASE))
    assert status == 200 and body == PAGE and "location" not in hd
    status, hd, body = fget(strip(urljoin(BASE, "manifest.json")))
    assert status == 200 and hd["content-type"] == ["application/manifest+json"]
    assert body == MANIFEST_BYTES
    status, hd, body = fget(strip(urljoin(BASE, "events?n=1")), until=b"\n\n")
    assert status == 200 and hd["content-type"] == ["text/event-stream"]
    assert body.startswith(b"event: config\n")


def test_every_page_link_reaches_its_route_under_the_prefix(kit, tmp_path):
    """Each URL the page carries, resolved at /talk/ and stripped as the front door
    strips it, is answered by its route; none redirects."""
    face = tmp_path / "home" / ".session" / "agents" / "someone"
    face.mkdir(parents=True)
    (face / "avatar.jpg").write_bytes(b"\xff\xd8 a face \xff\xd9")
    kit()
    expect = {"manifest": 200, "avatar": 200, "events": 200, "pair": 204}
    seen = set()
    for u in literal_urls(urls(HTML)):
        path = strip(urljoin(BASE, u))
        r = server.route(urlsplit(path).path)
        status, hd, body = fget(path, until=SECRET.encode() if r == "events" else None)
        assert status == expect[r] and "location" not in hd, (u, path, status)
        if r == "avatar":
            assert body == b"\xff\xd8 a face \xff\xd9"
        if r == "events":
            assert body.startswith(b"event: config\n") and SECRET.encode() in body
        seen.add(r)
    assert seen == set(expect)


@pytest.mark.parametrize("prefix", [None, "/talk", "/elsewhere", '"><script>x</script>'])
def test_the_page_bytes_do_not_depend_on_the_prefix_header(kit, prefix):
    """No injection: the prefix header is accepted and never read for links."""
    kit()
    headers = [USER] + ([("X-Sessionkit-Prefix", prefix)] if prefix is not None else [])
    status, _, body = fget("/", headers=headers)
    assert status == 200 and body == PAGE
    status, _, body = fget("/manifest.json", headers=headers)
    assert status == 200 and body == MANIFEST_BYTES
