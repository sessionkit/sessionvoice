#!/usr/bin/env python3
"""talk serve - the live voice conversation, on the web.

A page that shows the chat: the voice conversation as it happens, the
real one, live, fed over Server-Sent Events (``/events``) rather than
a WebSocket, so the browser only listens and never
talks back on that stream.

Reads ONE file, today's heard log (`ears.heard.log_path()`, so under
sessionvoice's home, ears.home): the user's partials and turns, every
mouth line (saying/said), and the ears' events. Serves index.html beside this
file, and /events as Server-Sent Events: a "config" event (the settings
below), one "backfill" event (the recent rows), then each new row as it is
written, each carrying "off" (its end byte in the log).

A "state" event carries:
  - receipts: for each agent, how far its heard-first waiter has read the log
    (the "offsets" in its heard.state, found where ears-arm keeps it). A row
    whose "off" is at or below an agent's offset has reached that agent's
    ears: the page ticks it.
  - presence: each agent's session status from `session list --json`
    (busy = thinking; otherwise listening), heard.state or not.
  - ride: per agent, the highest heard seq shown to its live session, from the
    heard-cursor (the ride-along hook's) or the pager's ride/done mail
    (Ears-Seq), max.
With no seats and no `session` command, turns and mouth lines still stream.
Settings (flags of `talk serve`, see cli.py): the person's label, the smart-turn
shadow log dir (debug view; OFF unless named), a Settings link (hidden unless
named), and a phone button's call URL (hidden unless named, and
refused by check_call_url unless https:, or http://localhost / 127.0.0.1).
Read-only. Binds 127.0.0.1 by default (`talk serve [--host H] [--port N]`);
putting it on a tailnet with tailscale serve is a choice, never a default.
Every route answers only a request whose Host is one it knows (so a
DNS-rebinding page cannot read the transcript): 127.0.0.1:<port> and
localhost:<port> (the BOUND port), the --host address when it names one, and
the names configured with --allow-host / TALK_ALLOW_HOSTS (a bare name on any
port or none, `name:port` on that port only). An Origin, when sent, must name
the same. Anything else gets a plain-text 403 and no transcript bytes.
X-Forwarded-* headers are ignored. The tailscale-serve name is not allowed by
default: add it with --allow-host.
Then every route but the page and the manifest needs this browser to be paired:
a `talk_token` cookie whose value is the per-install token
(token_store.py, <home>/talk/token, 0600 in a 0700 dir). That covers
/events, the avatars, unknown paths and GET pair (204 when paired). A request
without it gets a plain-text 403 naming `talk pair`. A browser pairs with POST
pair, showing the token once in an X-Talk-Token header: the answer is a 204 whose
cookie is `talk_token=<token>; Max-Age=34560000; HttpOnly; SameSite=Strict` (and
`; Secure` when the Origin is https), never with a Path or Domain; a wrong or
missing header gets a 403 and no cookie. POST reads no body and always closes
the connection. Pairing at Host `localhost` is refused, naming 127.0.0.1: its
cookie would go to whatever answers localhost, perhaps another user on [::1]. The token is never read
from a query, and a `talk_token=` value is blanked in every log line. An open
/events stream checks its cookie again at each keep-alive, so it ends within
one keep-alive of a rotation. `serve()` makes or checks the token before
anything binds; a refused token file is a ValueError (cli: exit 2). This stops
a local user who CONNECTS; it does not stop one who LISTENS where the browser
dials (the port while talk is down, or another 127.0.0.1 port the browser
opens): the browser hands its cookie to whatever answers there.
Socket mode (`talk serve --unix [PATH]`) closes that on a proxied path: talk
binds a unix socket (0600, in a 0700 dir, <home>/talk/talk.sock by
default) and NO TCP port, so there is no port to squat. Each connection's peer
uid (LOCAL_PEERCRED; SO_PEERCRED on Linux) must be talk's own or the proxy's
(--proxy-uid, default 0), or it gets a 403 before its request is read. Over the
socket the proxy's portless `Host: localhost` is answered, pairing there is not
refused (the browser's cookie belongs to the proxy's name), and Origin is
checked against --allow-host as before; the token gate is unchanged.
Stdlib only, plus the kit's own ears.heard (itself stdlib only) for the log
and cursor paths.
"""
import json, os, re, socket, socketserver, stat, struct, subprocess, sys, threading, time
from datetime import date
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, urlsplit

from ears import heard
from talk import token_store   # absolute, like ears: server.py also runs as a script (__main__)

HERE = os.path.dirname(os.path.abspath(__file__))
EVENTS = {"listen-started", "listen-stopped", "call-started", "call-ended", "battery-low"}
FIELDS = ("ts", "seq", "kind", "text", "source", "device", "agent", "utt", "voice",
          "cut", "played_s", "dur_s", "text_played_est", "event", "interest", "final", "speaker")
SEAT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
SHARED_SEAT = "ears"     # the label for a heard.state in the shared home that no named seat found
SMARTDIR = None          # the smart-turn shadow log dir: a setting, OFF (None) by default
SETTINGS = {"person": None, "settings_url": None, "call_url": None}   # sent to the page as its "config" event
CALL_LOCAL = ("localhost", "127.0.0.1")   # the only hosts a plain http: call URL may name (development)
ALLOW_HOSTS = frozenset()   # configured Host names (check_allow_hosts), beside loopback and the bind host
LOOPBACK = ("127.0.0.1", "localhost")      # allowed on the bound port; no other spelling of loopback
ANY_ADDR = ("", "0.0.0.0", "::")           # a bind host that names no one address
HOSTNAME = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*")
ORIGIN = re.compile(r"(https?)://([^/?#@\\]+)/?", re.IGNORECASE)
ALLOW_HINT = "add it with --allow-host or TALK_ALLOW_HOSTS"
PAIR_HINT = 'run "talk pair" on the host'
COOKIE = "talk_token"         # the pairing cookie: its value is the per-install token
OPEN_ROUTES = frozenset({"page", "manifest"})   # served unpaired: the package's own static files
MAX_COOKIES = 16              # talk_token values compared per request, at most
KEEPALIVE = 15                # seconds between /events keep-alives; each one re-checks the cookie
REDACT = re.compile(r"(talk_token=)[^&;\s\"']*", re.IGNORECASE)
PAIR_HEADER = "X-Talk-Token"  # POST pair: the one place a browser shows the token itself
COOKIE_MAX_AGE = 34560000     # 400 days: RFC 6265bis section 5.5's cap
SESSIONS = os.path.expanduser("~/.sessionmail/sessions")


def home(*parts):
    """A path under $HOME, read at call time (so a test or a launchd job's HOME counts)."""
    return os.path.join(os.path.expanduser("~"), *parts)


def seat_home(seat):
    """A seat's home, in the order ears-arm finds it (skills/ears/scripts/ears-arm):
    $EARS_HOME if set, else the session-fleet seat home ~/.session/agents/home/<seat>
    if it exists, else ~/.<seat> if it exists, else ~/.ears. Read-only: it makes
    nothing (ears-arm makes the dir when it arms)."""
    if os.environ.get("EARS_HOME"):
        return os.path.expanduser(os.environ["EARS_HOME"])
    for d in (home(".session", "agents", "home", seat), home("." + seat)):
        if seat and os.path.isdir(d):
            return d
    return home(".ears")


def waiters(names=()):
    """Each seat's heard-first state: <seat_home(seat)>/heard.state, for the seats
    named by the session-fleet homes dir (if there is one) and by `names` (the
    live sessions' agents). A heard.state in the shared home ($EARS_HOME or
    ~/.ears) that no named seat resolved to shows as "ears". Seats with no
    heard.state are left out here; they still get presence and ride."""
    cands = {n for n in names if n and SEAT_NAME.match(n)}
    try:
        cands |= {n for n in os.listdir(home(".session", "agents", "home")) if SEAT_NAME.match(n)}
    except OSError:
        pass
    out = {}
    for n in sorted(cands):
        sf = os.path.join(seat_home(n), "heard.state")
        if os.path.isfile(sf):
            out[n] = sf
    shared = os.path.join(seat_home(""), "heard.state")
    if os.path.isfile(shared) and shared not in out.values():
        out.setdefault(SHARED_SEAT, shared)
    return out


def avatars_dir():
    """Faces: ~/.session/agents/<name>/avatar.jpg when the session fleet keeps
    them; optional, the page falls back to a coloured initial."""
    return home(".session", "agents")


STATE = {"receipts": {}, "presence": {}, "ride": {}}
STATE_LOCK = threading.Lock()
RIDE_CACHE = {}     # ride/done dir -> {filename: Ears-Seq or None}; done/ files never change


def ears_seq(path):
    """The Ears-Seq header of one maildir file, reading headers only (they end
    at the first blank line). None when it has none: agent mail rides too."""
    with open(path, "rb") as f:
        for line in f:
            if not line.strip():
                return None
            name, _, value = line.partition(b":")
            if name.strip().lower() == b"ears-seq":
                try:
                    return int(value.strip())
                except ValueError:
                    return None
    return None


def ride_done_seq(session_id):
    """The highest Ears-Seq the pager's ride-along gave this session: the heard
    mails it filed under ~/.sessionmail/sessions/<sid>/ride/done/ (a seat
    with no heard-cursor gets its heard turns this way).
    Each file is parsed once and cached by name. 0 when there is none."""
    if not session_id or os.sep in session_id or session_id.startswith("."):
        return 0
    d = os.path.join(SESSIONS, session_id, "ride", "done")
    try:
        names = os.listdir(d)
    except OSError:
        RIDE_CACHE.pop(d, None)
        return 0
    old = RIDE_CACHE.get(d, {})
    new = {}
    for n in names:
        if n.startswith("."):
            continue
        if n in old:
            new[n] = old[n]
            continue
        try:
            new[n] = ears_seq(os.path.join(d, n))
        except OSError:
            continue        # unreadable (or moved away): skip, try again next poll
    RIDE_CACHE[d] = new
    return max((v for v in new.values() if v is not None), default=0)


def today_log():
    """Today's heard log, where the kit's own writer puts it (honours SESSIONVOICE_HOME)."""
    return str(heard.log_path(date.today(), heard.log_dir()))


def slim(line):
    """One log line -> the dict the page draws, or None for rows it never shows."""
    try:
        d = json.loads(line)
    except ValueError:
        return None
    k = d.get("kind")
    if k == "event" and d.get("event") not in EVENTS:
        return None
    if k not in ("turn", "partial", "saying", "said", "event"):
        return None
    return {f: d[f] for f in FIELDS if f in d}


def today_smart():
    """Today's smart-turn shadow log, or None when the setting is off (the default)."""
    if not SMARTDIR:
        return None
    return os.path.join(os.path.expanduser(SMARTDIR), f"smart_{date.today():%Y-%m-%d}.jsonl")


def smart_row(line):
    """A smart-turn shadow verdict at one of the user's pauses (debug view only)."""
    try:
        d = json.loads(line)
    except ValueError:
        return None
    if d.get("kind") != "offset":
        return None
    return {"kind": "smart", "ts": d.get("ts"), "p": d.get("p"), "complete": d.get("complete"),
            "voice_end": d.get("voice_end")}


def smart_backfill(since_ts, limit=400):
    path = today_smart()
    if not path:
        return []
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 400_000))
            lines = f.read().decode("utf-8", "replace").splitlines()[1:]
    except FileNotFoundError:
        return []
    rows = [r for r in map(smart_row, lines) if r and (r["ts"] or "") >= since_ts]
    return rows[-limit:]


def backfill(path, n, debug=False):
    """The last n rows worth drawing: no partials older than the last turn."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            start = max(0, size - 3_000_000)
            f.seek(start)
            data = f.read()
    except FileNotFoundError:
        return []
    rows, pos = [], start
    for i, part in enumerate(data.split(b"\n")):
        end = pos + len(part) + 1
        if part and not (i == 0 and start > 0):     # the first piece may be half a line
            r = slim(part.decode("utf-8", "replace"))
            if r:
                r["off"] = end
                rows.append(r)
        pos = end
    if debug:                                      # every partial, plus the smart-turn verdicts
        out = rows[-n:]
        if out:
            out = sorted(out + smart_backfill(out[0]["ts"]), key=lambda r: r.get("ts") or "")
        return out
    out = [r for r in rows if r["kind"] != "partial"][-n:]
    last_turn = max((r["seq"] for r in rows if r["kind"] == "turn"), default=-1)
    live = [r for r in rows if r["kind"] == "partial" and r.get("seq", 0) > last_turn]
    return out + live[-1:]


def live_sessions():
    """`session list --json` (sessiontools), or [] when there is no such command
    or it fails: a kit without the session fleet still streams every row."""
    try:
        out = subprocess.run(["session", "list", "--json"], capture_output=True, text=True, timeout=10)
        rows = json.loads(out.stdout or "[]")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []
    return [s for s in rows if isinstance(s, dict)] if isinstance(rows, list) else []


def poll_state():
    """Every 2 s: each waiter's offset in today's log, and each agent's status."""
    while True:
        receipts, presence, ride = {}, {}, {}
        path = today_log()
        sessions = live_sessions()
        for agent, sf in waiters(s.get("agent") for s in sessions).items():
            try:
                with open(sf) as f:
                    receipts[agent] = json.load(f).get("offsets", {}).get(path, 0)
            except (OSError, ValueError, AttributeError):
                pass
        for s in sessions:
            a, sid = s.get("agent"), s.get("session_id") or ""
            if not a or not isinstance(a, str):
                continue
            if sid:      # the ride-along's cursor (ears.heard's own): the last seq shown mid-work
                c = heard.load_cursor(sid)
                if c and c.file == os.path.basename(path):
                    ride[a] = max(ride.get(a, 0), c.seq)
            # and the pager's ride record: seq is global-monotonic across
            # days, so no day filter; whichever source is higher wins
            seq = ride_done_seq(sid)
            if seq:
                ride[a] = max(ride.get(a, 0), seq)
            st = "thinking" if s.get("status") == "busy" else "listening"
            if presence.get(a) != "thinking":
                presence[a] = st
        with STATE_LOCK:
            STATE["receipts"], STATE["presence"], STATE["ride"] = receipts, presence, ride
        time.sleep(2)


def state_now():
    with STATE_LOCK:
        return json.loads(json.dumps(STATE))


def split_host(value):
    """A Host header's value (or an Origin's authority) -> (hostname, port).

    The hostname is lower-cased with one trailing dot stripped; the port is an
    int, or None when there is none. `[v6]:port` is parsed only so it can be
    refused cleanly (it never matches: the server is AF_INET only). None when
    it is empty or unparsable: whitespace, a stray colon, a bad port, a
    hostname outside [a-z0-9.-].
    """
    if not value or any(not 32 < ord(c) < 127 for c in value):      # whitespace, controls, non-ASCII
        return None
    port = None
    if value.startswith("["):
        name, sep, rest = value[1:].partition("]")
        if not sep or not name:
            return None
        if rest:
            if not rest.startswith(":"):
                return None
            port = rest[1:]
        if any(c not in "0123456789abcdefABCDEF:." for c in name):
            return None
        name = "[" + name.lower() + "]"
    else:
        name, sep, port = value.partition(":")
        if not sep:
            port = None
        name = name.lower()
        if name.endswith("."):
            name = name[:-1]
        if not HOSTNAME.fullmatch(name):
            return None
    if port is not None:
        if not port.isdigit() or not port.isascii() or not 0 < int(port) < 65536:
            return None
        port = int(port)
    return name, port


def check_allow_hosts(entries):
    """The configured Host names (--allow-host / TALK_ALLOW_HOSTS) as a frozenset
    of `name` or `name:port` strings, lower-cased; ValueError if one is refused.

    `entries` is an iterable of entries, or one string of them separated by
    commas or whitespace (the env var's form). None, or nothing, is the default
    (loopback and the bind host only) and not an error. An entry is a hostname
    ([A-Za-z0-9.-], no label starting or ending in "-", one trailing dot
    dropped) or a dotted IPv4, then an optional :port in 1-65535. Refused: an
    empty entry, whitespace, "*", a scheme, a path, "@" and a bad port. A
    refused entry makes the whole set refused.
    """
    if entries is None:
        return frozenset()
    if isinstance(entries, str):
        entries = [e for e in re.split(r"[,\s]+", entries) if e]
    why = "a hostname or IPv4 address, optionally :port"
    out = set()
    for e in entries:
        if not isinstance(e, str) or not e:
            raise ValueError(f"allowed host {e!r} refused: empty ({why})")
        if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in e):
            raise ValueError(f"allowed host {e!r} refused: whitespace or control characters ({why})")
        if "*" in e:
            raise ValueError(f"allowed host {e!r} refused: no wildcards, name each host ({why})")
        if "://" in e:
            raise ValueError(f"allowed host {e!r} refused: a scheme; give the host only ({why})")
        if "/" in e:
            raise ValueError(f"allowed host {e!r} refused: a path; give the host only ({why})")
        if "@" in e:
            raise ValueError(f"allowed host {e!r} refused: credentials; give the host only ({why})")
        if e.startswith("["):
            raise ValueError(f"allowed host {e!r} refused: an IPv6 address (talk binds IPv4 only)")
        _, sep, port = e.partition(":")
        if sep and (not port.isdigit() or not port.isascii() or not 0 < int(port) < 65536):
            raise ValueError(f"allowed host {e!r} refused: a bad port ({why})")
        parsed = split_host(e)
        if parsed is None:
            raise ValueError(f"allowed host {e!r} refused: not a hostname ({why})")
        out.add(parsed[0] if parsed[1] is None else f"{parsed[0]}:{parsed[1]}")
    return frozenset(out)


def shown(value, cap=100):
    """A header as it may be echoed or logged: printable ASCII only, capped."""
    return "".join(c for c in str(value) if 32 <= ord(c) < 127)[:cap]


def route(path):
    """The route a request path is served by, in dispatch order: "events", "page",
    "manifest", "avatar", "pair" or "404". do_GET dispatches on it and the gate
    reads it, so the two cannot drift apart; a route added later is gated unless it
    joins OPEN_ROUTES."""
    if path.endswith("/events"):
        return "events"
    if path.endswith(("/", "/index.html")):
        return "page"
    if path.endswith("/manifest.json"):
        return "manifest"
    if "/avatar/" in path:
        return "avatar"
    if path.endswith("/pair"):
        return "pair"
    return "404"


def cookie_values(headers, name=COOKIE):
    """Every value of cookie `name` in the Cookie header(s), split by hand: another
    app's junk cookie on a shared origin cannot break the parse (SimpleCookie
    rejects the whole header on one bad pair)."""
    out = []
    for header in headers or ():
        for part in header.split(";"):
            key, sep, value = part.strip().partition("=")
            if sep and key.strip() == name:
                out.append(value.strip())
    return out


def pair_cookie(token, secure):
    """The pairing cookie, exactly: host-only (no Domain), no
    Path (so the browser scopes it to the page's directory: /talk behind tailscale serve),
    HttpOnly, SameSite=Strict, and Secure when the page came over https."""
    return (f"{COOKIE}={token}; Max-Age={COOKIE_MAX_AGE}; HttpOnly; SameSite=Strict"
            + ("; Secure" if secure else ""))


def token_trouble():
    """Why the token file cannot be used now, with the fix, for the log; never the token."""
    try:
        token_store.load()
    except FileNotFoundError:
        return f"no token file at {token_store.path()} (run `talk token --rotate`)"
    except (OSError, ValueError) as e:
        return str(e)
    return None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        line = REDACT.sub(r"\1<redacted>", fmt % args)    # a token pasted into a query never reaches the log
        sys.stderr.write(time.strftime("%H:%M:%S ") + line + "\n")

    def handle(self):
        """Over the unix socket, refuse a peer uid that is neither talk's own nor the
        proxy's before reading a byte of its request."""
        if unix_server(self.server):
            uid = peer_uid(self.connection)
            if uid is None or uid not in self.server.talk_peer_uids:
                return self.refuse_peer(uid)
        return super().handle()

    def refuse_peer(self, uid):
        """403 to a socket peer that may not connect: plain text, nothing read, closed.
        The log line comes first: once the peer has its 403, the line is there."""
        who = "an unknown uid" if uid is None else f"uid {uid}"
        body = f"talk: a connection from {who} is not allowed on this socket\n".encode()
        self.close_connection = True
        self.log_message("socket peer refused: %s (allowed: uid %s)", who,
                         ", ".join(str(u) for u in sorted(self.server.talk_peer_uids)))
        try:
            self.wfile.write(b"HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain; charset=utf-8\r\n"
                             b"Cache-Control: no-store\r\nConnection: close\r\n"
                             + f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
            self.wfile.flush()
        except OSError:
            pass

    def local_names(self):
        """The names allowed on the BOUND port: loopback, and the bind host when
        --host names one address (as given, and as the socket reports it)."""
        names = set(LOOPBACK)
        given = (getattr(self.server, "talk_bind_host", None) or "").lower().rstrip(".")
        if given not in ANY_ADDR:
            names.add(given)
            names.add(str(self.server.server_address[0]).lower())
        return names

    def configured(self, name, port, origin):
        """Does a configured --allow-host entry match? `name` on any port (or
        none); `name:port` only on that explicit port for a Host, and on the
        hostname alone for an Origin (whose port is its scheme's default)."""
        for entry in ALLOW_HOSTS:
            ename, _, eport = entry.partition(":")
            if ename != name:
                continue
            if not eport or origin or (port is not None and int(eport) == port):
                return True
        return False

    def allowed(self, name, port, origin=False):
        """Loopback and the bind host on the bound port only (a portless Host
        counts as 80); else a configured name (`configured`). Over the unix socket
        there is no port: the proxy's portless Host `localhost` is answered,
        and otherwise only a configured name (an Origin never as localhost)."""
        if unix_server(self.server):
            if name == "localhost" and port is None and not origin:
                return True
            return self.configured(name, port, origin)
        bound = self.server.server_address[1]
        if name in self.local_names() and (80 if port is None else port) == bound:
            return True
        return self.configured(name, port, origin)

    def check_host(self):
        """None when the request may be served, else why it is refused.
        Host is the check that stops DNS rebinding; Origin is defence in depth."""
        hosts = self.headers.get_all("Host") or []
        if len(hosts) != 1:
            return "a request with no Host" if not hosts else "a request with more than one Host"
        parsed = split_host(hosts[0])
        if parsed is None or not self.allowed(*parsed):
            return f'Host "{shown(hosts[0])}"'
        origins = self.headers.get_all("Origin") or []
        if not origins:
            return None                     # same-origin EventSource, navigation, curl
        if len(origins) > 1:
            return "a request with more than one Origin"
        m = ORIGIN.fullmatch(origins[0])
        auth = split_host(m.group(2)) if m else None
        if auth is None:
            return f'Origin "{shown(origins[0])}"'
        scheme = m.group(1).lower()
        port = auth[1] if auth[1] is not None else (443 if scheme == "https" else 80)
        if not self.allowed(auth[0], port, origin=True):
            return f'Origin "{shown(origins[0])}"'
        return None

    def paired(self):
        """Does a talk_token cookie match the per-install token (constant time)? A
        missing or refused token file matches nothing (token_store.matches)."""
        values = cookie_values(self.headers.get_all("Cookie"))[:MAX_COOKIES]
        return any(token_store.matches(v) for v in values)

    def refuse_unpaired(self):
        """The gate's 403. The log line says why the token file is unusable, if it is."""
        trouble = token_trouble() if token_store.current() is None else None
        why = "this browser is not paired" + (f"; token: {trouble}" if trouble else "")
        return self.refuse("this browser is not paired", hint=PAIR_HINT, log_why=why)

    def refuse(self, why, hint=None, log_why=None):
        """403, plain text naming the remedy, no transcript, connection closed.
        With no hint it is the Host/Origin refusal; with one, `talk: WHY (HINT)`.
        The log line comes first: once the client has its 403, the line is there,
        and a client that hangs up before the 403 is written is still logged."""
        text = f"talk: {why} is not allowed ({ALLOW_HINT})" if hint is None else f"talk: {why} ({hint})"
        body = (text + "\n").encode()
        self.close_connection = True
        self.log_message('"%s" 403 refused: %s', shown(self.requestline, 200), log_why or why)
        self.send_response_only(403)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        why = self.check_host()             # before any routing: every route, 404s included
        if why:
            return self.refuse(why)
        u = urlparse(self.path)
        r = route(u.path)
        if r not in OPEN_ROUTES and not self.paired():    # the gate: closed by default
            return self.refuse_unpaired()
        if r == "events":
            return self.events(parse_qs(u.query))
        if r == "page":
            return self.file("index.html", "text/html; charset=utf-8")
        if r == "manifest":
            return self.file("manifest.json", "application/manifest+json")
        if r == "avatar":
            return self.avatar(u.path.rsplit("/", 1)[-1])
        if r == "pair":
            return self.paired_probe()
        self.send_error(404)

    def do_POST(self):
        """POST .../pair: the page shows the token once, in X-Talk-Token, and is answered
        with the pairing cookie. Host and Origin are checked first, as for GET.
        No body is read, so the connection is closed whatever the answer. A request whose
        Host is localhost (or *.localhost) is refused: its cookie would be handed to
        whatever answers localhost, which may be another user's listener on [::1] (T7b).
        Not over the unix socket: there `Host: localhost` is the proxy's, and the
        browser's cookie belongs to the proxy's name, never localhost."""
        self.close_connection = True        # an unread body must never be taken for a next request
        why = self.check_host()
        if why:
            return self.refuse(why)
        if route(urlparse(self.path).path) != "pair":
            return self.send_error(404)
        name, port = split_host(self.headers["Host"])      # check_host passed: one Host, parsable
        if (name == "localhost" or name.endswith(".localhost")) and not unix_server(self.server):
            at = f"http://127.0.0.1:{port or self.server.server_address[1]}/"
            return self.refuse("this browser cannot pair at localhost", hint=f"open {at} and pair there")
        values = self.headers.get_all(PAIR_HEADER) or []
        if len(values) != 1 or not token_store.matches(values[0]):
            trouble = token_trouble() if token_store.current() is None else None
            why = "the pairing token does not match"
            return self.refuse(why, hint=PAIR_HINT,
                               log_why=why + (f"; token: {trouble}" if trouble else ""))
        origin = self.headers.get("Origin") or ""
        self.send_response(204)             # logs the request line only: never the header
        self.send_header("Set-Cookie", pair_cookie(values[0], origin.lower().startswith("https://")))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()

    def paired_probe(self):
        """GET pair: the page asks "am I paired?". Past the gate, so yes: 204, no body,
        and no Content-Length (RFC 9110 8.6: never in a 204)."""
        self.send_response(204)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def avatar(self, name):
        """An agent's face: ~/.session/agents/<name>/avatar.jpg (the session
        skill, "An agent's folder").
        404 when it has none, and the page falls back to its initial."""
        if not name.replace("-", "").replace("_", "").isalnum():
            return self.send_error(404)
        path = os.path.join(avatars_dir(), name, "avatar.jpg")
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            return self.send_error(404)
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=300")
        self.end_headers()
        self.wfile.write(body)

    def file(self, name, ctype):
        with open(os.path.join(HERE, name), "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_event(self, name, data):
        msg = (f"event: {name}\n" if name else "") + "data: " + json.dumps(data, ensure_ascii=False) + "\n\n"
        self.wfile.write(msg.encode())
        self.wfile.flush()

    def events(self, q):
        n = int((q.get("n") or ["150"])[0])
        debug = (q.get("debug") or ["0"])[0] == "1"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        path = today_log()
        try:
            self.send_event("config", dict(SETTINGS))
            self.send_event("backfill", backfill(path, n, debug))
            sm_path, sm, sm_buf = today_smart(), None, b""
            if debug and sm_path and os.path.exists(sm_path):
                sm = open(sm_path, "rb")
                sm.seek(0, 2)
            f, pos, buf = None, 0, b""
            if os.path.exists(path):
                f = open(path, "rb")
                f.seek(0, 2)
                pos = f.tell()
            last_ping, last_state = time.time(), None
            while True:
                if path != today_log():            # midnight: follow the new day's file
                    path, pos, buf = today_log(), 0, b""
                    if f:
                        f.close()
                    f = None
                if f is None and os.path.exists(path):
                    f = open(path, "rb")
                chunk = f.read() if f else b""
                if chunk:
                    buf += chunk
                    *lines, buf = buf.split(b"\n")
                    for line in lines:
                        pos += len(line) + 1
                        r = slim(line.decode("utf-8", "replace"))
                        if r:
                            r["off"] = pos
                            self.send_event(None, r)
                if sm:
                    sc = sm.read()
                    if sc:
                        sm_buf += sc
                        *slines, sm_buf = sm_buf.split(b"\n")
                        for line in slines:
                            r = smart_row(line.decode("utf-8", "replace"))
                            if r:
                                self.send_event(None, r)
                if not chunk:
                    time.sleep(0.25)
                st = state_now()
                if st != last_state:
                    self.send_event("state", st)
                    last_state = st
                if time.time() - last_ping > KEEPALIVE:
                    if not self.paired():          # rotated (or the file went bad): end the stream
                        self.close_connection = True
                        self.log_message('"%s" stream ended: %s', shown(self.requestline, 200),
                                         "the token changed; this browser is no longer paired")
                        return
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    last_ping = time.time()
        except (BrokenPipeError, ConnectionResetError):
            return


HOST, PORT = "127.0.0.1", 8780


def make_server(host=HOST, port=PORT):
    """The HTTP server, bound but not yet serving. 127.0.0.1 unless asked."""
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    srv.talk_bind_host = host      # as given: binding "localhost" reports 127.0.0.1
    return srv


# -- socket mode: a unix socket and no TCP port ----------------------------

LOCAL_PEERCRED = 0x001       # macOS sys/un.h: getsockopt(SOL_LOCAL=0, LOCAL_PEERCRED) -> struct xucred
XUCRED = struct.Struct("Ii")  # cr_version, cr_uid: the head of struct xucred
SUN_PATH_MAX = 104           # sun_path holds 104 bytes on macOS, the NUL included
PROXY_UID = 0                # tailscaled runs as root; --proxy-uid names another


class UnixSocketError(ValueError):
    """The socket path is refused: talk serve exits 2 before anything binds."""


def unix_server(srv):
    return getattr(srv, "talk_unix", False)


def default_socket():
    """<sessionvoice home>/talk/talk.sock: beside the token, in its 0700 directory."""
    return str(token_store.path().parent / "talk.sock")


def peer_uid(conn):
    """The uid at the other end of a unix socket connection, or None if the kernel will not
    say (then the connection is refused). macOS: LOCAL_PEERCRED; Linux: SO_PEERCRED."""
    try:
        if sys.platform == "darwin":
            raw = conn.getsockopt(0, LOCAL_PEERCRED, 256)
            if len(raw) < XUCRED.size:
                return None
            version, uid = XUCRED.unpack_from(raw)
            return uid if version == 0 else None
        if hasattr(socket, "SO_PEERCRED"):
            pid, uid, gid = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                                                struct.calcsize("3i")))
            return uid
    except (OSError, struct.error):
        return None
    return None


class UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """talk over a unix socket: bound 0600 (umask 0o177), no TCP port anywhere."""
    daemon_threads = True
    talk_unix = True

    def server_bind(self):
        old = os.umask(0o177)                    # the socket is born 0600: no moment wider
        try:
            super().server_bind()
        finally:
            os.umask(old)
        st = os.lstat(self.server_address)
        self.talk_socket_id = (st.st_dev, st.st_ino)

    def server_close(self):
        super().server_close()
        try:                                     # remove the socket file, if it is still ours
            st = os.lstat(self.server_address)
            if (st.st_dev, st.st_ino) == getattr(self, "talk_socket_id", None):
                os.unlink(self.server_address)
        except OSError:
            pass


def clear_stale_socket(path):
    """Before binding: nothing at `path`, or a stale socket we own (removed). Anything else
    is refused: a file that is not a socket, a symlink, another user's socket, or a socket
    that a running server still answers."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(st.st_mode):
        raise UnixSocketError(f"{path} exists and is not a socket; move it away, or give another --unix PATH")
    if st.st_uid != os.geteuid():
        raise UnixSocketError(f"{path} is a socket owned by uid {st.st_uid}, not this user; remove it")
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.settimeout(1)
        probe.connect(path)
    except OSError:
        pass                                     # nobody answers: stale
    else:
        raise UnixSocketError(f"{path} is in use: another talk serve is answering there")
    finally:
        probe.close()
    os.unlink(path)


def make_unix_server(path=None, proxy_uid=PROXY_UID):
    """The HTTP server on a unix socket, bound but not yet serving. `path` defaults to
    default_socket(). Its directory must be ours alone (0700, made if missing); the socket
    is 0600. Peers other than this euid and `proxy_uid` are refused per connection.
    UnixSocketError or token_store.TokenError (both ValueErrors) if refused."""
    path = path or default_socket()
    if len(os.fsencode(path)) >= SUN_PATH_MAX:
        raise UnixSocketError(f"socket path {path} is {len(os.fsencode(path))} bytes; a unix socket path"
                              f" holds at most {SUN_PATH_MAX - 1}: give a shorter --unix PATH")
    token_store.private_dir(os.path.dirname(os.path.abspath(path)), "socket directory")
    clear_stale_socket(path)
    srv = UnixHTTPServer(path, Handler)
    srv.talk_peer_uids = frozenset({os.geteuid(), int(proxy_uid)})
    return srv


def check_call_url(url):
    """The phone button's URL, or None when unset; ValueError if refused.

    The page draws it into an href, so anything but a web URL would be script on
    Talk's page. Accepted: https://HOST..., or
    http://localhost... / http://127.0.0.1... for development. Refused: every
    other scheme (javascript:, data:, ...), http: to any other host, no host,
    a bad port, credentials in the URL (Talk holds the URL and nothing else),
    and whitespace or control characters. Returned unchanged: Talk appends
    nothing to it and never fetches it.
    """
    if not url:
        return None
    why = "must be https:, or http://localhost / http://127.0.0.1 for development"
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url):
        raise ValueError(f"call URL {url!r} refused: whitespace or control characters ({why})")
    try:
        u = urlsplit(url)
        u.port                                  # a bad port raises here
    except ValueError as e:
        raise ValueError(f"call URL {url!r} refused: not a URL ({e})") from None
    if u.scheme not in ("https", "http") or not u.hostname:
        raise ValueError(f"call URL {url!r} refused: {why}")
    if u.scheme == "http" and u.hostname not in CALL_LOCAL:
        raise ValueError(f"call URL {url!r} refused: plain http: to {u.hostname} ({why})")
    if "@" in u.netloc:
        raise ValueError(f"call URL {url!r} refused: credentials in the URL (Talk holds the URL and nothing else)")
    return url


def configure(person=None, smart_dir=None, settings_url=None, call_url=None, allow_hosts=None):
    """The settings `talk serve` takes. Each is off/unset unless given.
    A refused call_url (check_call_url) or allow_hosts entry (check_allow_hosts)
    raises ValueError and changes nothing."""
    global SMARTDIR, ALLOW_HOSTS
    call_url = check_call_url(call_url)
    allow_hosts = check_allow_hosts(allow_hosts)
    SMARTDIR = smart_dir or None
    ALLOW_HOSTS = allow_hosts
    SETTINGS.update(person=person or None, settings_url=settings_url or None, call_url=call_url)


def serve(host=HOST, port=PORT, unix=None, proxy_uid=PROXY_UID, **settings):
    """Start the state poll and serve until killed (`talk serve`). The token is made
    (first start) or checked before anything binds: a refused token file raises
    token_store.TokenError, a ValueError, and nothing is served. With `unix` (a path, or
    True for default_socket()) talk binds that socket and no TCP port."""
    configure(**settings)
    token_store.load_or_create()
    if unix:
        srv = make_unix_server(None if unix is True else unix, proxy_uid)
        print(f"talk-web: serving {HERE} on unix socket {srv.server_address} (no TCP port),"
              f" following {today_log()}", flush=True)
        print(f"talk-web: answering peer uid {', '.join(str(u) for u in sorted(srv.talk_peer_uids))};"
              f" Host localhost (the proxy){''.join(', ' + h for h in sorted(ALLOW_HOSTS))}", flush=True)
    else:
        srv = make_server(host, port)
        h, p = srv.server_address[:2]
        print(f"talk-web: serving {HERE} on {h}:{p}, following {today_log()}", flush=True)
        names = sorted({f"127.0.0.1:{p}", f"localhost:{p}"} | ({f"{host}:{p}"} if host not in ANY_ADDR else set()))
        print(f"talk-web: answering Host {', '.join(names + sorted(ALLOW_HOSTS))}", flush=True)
    threading.Thread(target=poll_state, daemon=True).start()
    print(f'talk-web: browsers pair with "talk pair"; the token is in {token_store.path()}', flush=True)
    try:
        srv.serve_forever()
    finally:
        if unix:
            srv.server_close()                   # removes the socket file it made


if __name__ == "__main__":
    serve(port=int(sys.argv[1]) if len(sys.argv) > 1 else PORT)
