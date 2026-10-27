"""talk command line.

  talk serve [--host H] [--port N] [--person NAME] [--smart-turn DIR] [--settings-url URL]
             [--call-url URL] [--allow-host NAME ...] [--unix [PATH]] [--proxy-uid UID] [--kit]
      the live view: the page at /, its events at /events
  talk pair [--url URL]
      print this install's pairing link, URL#pair=<token>, to a terminal only
  talk token --rotate
      replace the token: every paired browser must pair again

--call-url URL (env TALK_CALL_URL; off by default) puts a phone button in the
header that opens the sessionphone web call:
a plain top-level link, new tab, rel="noopener noreferrer", never an iframe. The
URL is checked at start, before anything binds: only https:, or http://localhost
/ http://127.0.0.1 for development; anything else (javascript:, data:, http: to
another host, junk) exits 2 with a message and serves nothing. Talk appends
nothing to it and never fetches it.

Binds 127.0.0.1 by default. To reach it from another machine, put it behind
something you choose (e.g. `tailscale serve`); talk never does that itself.

Every route refuses (403) a Host it does not know, so a DNS-rebinding page
cannot read the transcript. Known by default: 127.0.0.1:PORT,
localhost:PORT, and the --host address when it names one. --allow-host NAME
(repeatable; env TALK_ALLOW_HOSTS, comma- or space-separated; both merge) adds
a name: a bare NAME on any port or none, NAME:PORT on that port only. Behind
`tailscale serve` the browser sends the tailnet name with no port, so add it:
`--allow-host machine.example.ts.net`. A bad entry (a scheme, a path, "@",
whitespace, a "*") exits 2 before anything binds.

Every route but the page and the manifest needs a paired browser: a
`talk_token` cookie holding the per-install token, <sessionvoice home>/talk/token.
The first `talk serve` makes it; a token file that is not 0600 in a 0700 dir
owned by you, or that holds no token, exits 2 before anything binds, naming the
path and the fix, never the token.

`talk pair` pairs a browser: open the link it prints, URL#pair=<token>. URL
defaults to http://127.0.0.1:8780/ (a trailing slash is added); it must be http
or https, with no query, fragment or user info, and never at localhost (or a
*.localhost name): a cookie set there goes to whatever answers localhost,
perhaps another user on [::1], so open talk at 127.0.0.1. An IPv6 address such
as [::1] is refused too: talk binds IPv4 only, so only another listener could
answer it. It prints only when stdout is a terminal:
piped, redirected or in an agent's tool shell it exits 2 and prints no token,
so the link never lands in a file, a log or a transcript. It reads the token,
never makes it (`talk serve` does). The token is never a flag or an env var:
another user can read argv.

`talk serve --unix [PATH]` (env TALK_UNIX_SOCKET=PATH, a path and not a secret)
binds a unix socket and NO TCP port, for a proxy such as
`tailscale serve --set-path /talk unix:PATH`. PATH defaults to
<sessionvoice home>/talk/talk.sock, beside the token. The socket is 0600 in a
0700 directory; a directory with group or other bits, or a file at PATH that is
not a stale socket of yours, exits 2 before anything binds. Only talk's own uid
and --proxy-uid (default 0, root, as tailscaled runs) may connect. There
`http://127.0.0.1:8780/` is not talk, so `talk pair` refuses a 127.0.0.1 URL
whenever TALK_UNIX_SOCKET is set or a socket sits at the default PATH: give the
proxy's address with --url.

`talk serve --kit --unix PATH` is the kit's web front door running talk as a
room; you never run it by hand. It is socket mode with only talk's own uid let
in (so --proxy-uid is refused with it), and the front door's login trusted:
each request must carry its X-Sessionkit-User header, there is no token and no
pairing, and the Host is the browser's, forwarded (never localhost), with an
Origin that names the same host and port. --kit without a unix socket
(--unix, or TALK_UNIX_SOCKET) exits 2 before anything binds.

`talk token --rotate` writes a fresh token over the old one in one step. A
running talk sees it at the next request, with no restart; open streams end
within one keep-alive, and every browser shows its pairing panel. It prints the
file's path, never the token.
"""
import argparse, ipaddress, os, stat, sys
from urllib.parse import urlsplit

from . import server, token_store


def parser():
    from ears.home import STRANDED_HELP
    ap = argparse.ArgumentParser(prog="talk", description="Talk: the live voice conversation on the web.",
                                 epilog=STRANDED_HELP)
    sub = ap.add_subparsers(dest="cmd", metavar="COMMAND")
    sp = sub.add_parser("serve", help="serve the live view (page + /events)",
                        description="Serve the live view: the page at /, Server-Sent Events at /events.",
                        epilog="Every route but the page and the manifest needs a paired browser: run `talk pair` on the host"
                               " and open the link it prints. `talk token --rotate` makes every browser pair"
                               " again.")
    sp.add_argument("--host", default=server.HOST,
                    help=f"address to bind (default {server.HOST}; anything else is your choice to expose it)")
    sp.add_argument("--port", type=int, default=server.PORT, help=f"port (default {server.PORT})")
    sp.add_argument("--person", default=os.environ.get("TALK_PERSON"),
                    help="the label on the person's side (env TALK_PERSON; else a row's speaker, else 'you')")
    sp.add_argument("--smart-turn", metavar="DIR", default=os.environ.get("TALK_SMART_TURN_DIR"),
                    help="a smart-turn shadow log dir for the debug view (env TALK_SMART_TURN_DIR; off by default)")
    sp.add_argument("--settings-url", metavar="URL", default=os.environ.get("TALK_SETTINGS_URL"),
                    help="link a Settings page from the header (env TALK_SETTINGS_URL; no link by default)")
    sp.add_argument("--call-url", metavar="URL", default=os.environ.get("TALK_CALL_URL"),
                    help="a phone button in the header that opens this web call in a new tab (env TALK_CALL_URL;"
                         " no button by default). Only https:, or http://localhost / http://127.0.0.1;"
                         " anything else is refused at start")
    sp.add_argument("--allow-host", metavar="NAME", action="append", default=[],
                    help="also answer this Host (repeatable; env TALK_ALLOW_HOSTS, comma- or space-separated,"
                         " merged). NAME on any port, NAME:PORT on that port only. Answered by default:"
                         " 127.0.0.1:PORT, localhost:PORT and the --host address; every other Host gets 403."
                         " Behind tailscale serve, add its name (e.g. machine.example.ts.net)")
    sp.add_argument("--unix", metavar="PATH", nargs="?", const=True,
                    default=os.environ.get("TALK_UNIX_SOCKET") or None,
                    help="bind a unix socket at PATH and NO TCP port (env TALK_UNIX_SOCKET=PATH; PATH defaults"
                         " to <sessionvoice home>/talk/talk.sock). For a proxy such as tailscale serve"
                         " --set-path /talk unix:PATH; --host and --port are then unused")
    sp.add_argument("--proxy-uid", metavar="UID", type=int, default=None,      # None: not given (--kit refuses it)
                    help="with --unix: the proxy's uid, which may connect besides talk's own"
                         f" (default {server.PROXY_UID}, root, as tailscaled runs; not with --kit)")
    sp.add_argument("--kit", action="store_true",
                    help="run as a room behind the kit's web front door: needs --unix PATH; only talk's own"
                         " uid may connect; the front door's X-Sessionkit-User is trusted, so there is no"
                         " token and no pairing")
    pp = sub.add_parser("pair", help="print this install's pairing link (to a terminal only)",
                        description="Print URL#pair=<token>: open it in the browser to pair it with this"
                                    " install's talk. Printed only when stdout is a terminal; otherwise exit 2"
                                    " and no token. Never at localhost: open talk at 127.0.0.1.")
    pp.add_argument("--url", default=PAIR_URL,
                    help=f"talk's address as the browser opens it (default {PAIR_URL}); http or https,"
                         " e.g. https://machine.example.ts.net/talk behind tailscale serve")
    tp = sub.add_parser("token", help="rotate the pairing token",
                        description="Rotate the per-install token: every paired browser must pair again"
                                    " (talk pair). Prints the file's path, never the token.")
    tp.add_argument("--rotate", action="store_true", help="write a fresh token over the old one")
    return ap


PAIR_URL = f"http://127.0.0.1:{server.PORT}/"


def pair_url(url):
    """The pairing link's base: `url` with a trailing slash. ValueError (naming the fix,
    never a token) for anything but a plain http(s) URL with a host, or for localhost."""
    try:
        u = urlsplit(url)
        host = (u.hostname or "").rstrip(".")
        u.port                                   # a bad port raises here
    except ValueError:
        raise ValueError(f"{url!r} is not a URL") from None
    if u.scheme not in ("http", "https") or not host:
        raise ValueError(f"{url!r}: give an http:// or https:// URL with a host")
    if u.query or u.fragment or "#" in url or "?" in url or u.username is not None or u.password is not None:
        raise ValueError(f"{url!r}: give talk's address only, with no query, fragment or user info")
    at127 = f"{u.scheme}://127.0.0.1{':' + str(u.port) if u.port else ''}{u.path or '/'}"
    if host == "localhost" or host.endswith(".localhost"):          # RFC 6761 6.3: loopback, [::1] too
        raise ValueError(f"{url!r}: talk does not pair at localhost (its cookie would go to whatever answers"
                         f" localhost, perhaps another user on [::1]); use 127.0.0.1, e.g. {at127}")
    if ":" in host:                                                 # an IPv6 literal, e.g. [::1]
        raise ValueError(f"{url!r}: talk binds IPv4 only and refuses an IPv6 Host, so only another"
                         f" listener could answer this link, and its page would read the token; use"
                         f" 127.0.0.1, e.g. {at127}")
    return url if url.endswith("/") else url + "/"


def socket_mode():
    """Is this a socket-mode host? TALK_UNIX_SOCKET is set, or a socket sits at the default
    PATH. Then nothing holds 127.0.0.1:8780 for talk, and any local user may."""
    if os.environ.get("TALK_UNIX_SOCKET"):
        return True
    try:
        return stat.S_ISSOCK(os.lstat(server.default_socket()).st_mode)
    except OSError:
        return False


def loopback_v4(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def stdout_is_terminal():
    """Is stdout a terminal? `talk pair` prints the token only to one."""
    return sys.stdout.isatty()


def pair(url):
    """`talk pair`: print URL#pair=<token> to a terminal; exit 2 otherwise, with no token."""
    try:
        base = pair_url(url)
    except ValueError as e:
        print(f"talk pair: --url {e}", file=sys.stderr)
        return 2
    if loopback_v4(urlsplit(base).hostname or "") and socket_mode():
        print(f"talk pair: --url {base}: on this host talk serves a unix socket (TALK_UNIX_SOCKET, or"
              f" {server.default_socket()}) and binds no TCP port, so {base} is not talk: anyone may hold"
              " that port. Give the proxy's address, e.g. talk pair --url"
              " https://machine.example.ts.net/talk", file=sys.stderr)
        return 2
    if not stdout_is_terminal():
        print("talk pair: stdout is not a terminal, so no link is printed: the link holds the token, and"
              " a pipe, a file or an agent's transcript would keep it. Run talk pair yourself, in a"
              " terminal on this host.", file=sys.stderr)
        return 2
    try:
        token = token_store.load()
    except FileNotFoundError:
        print(f"talk pair: no token yet at {token_store.path()}: start `talk serve` once (it makes"
              f" one), or run `talk token --rotate`", file=sys.stderr)
        return 2
    except token_store.TokenError as e:
        print(f"talk pair: {e}", file=sys.stderr)
        return 2
    print(f"{base}#pair={token}")
    return 0


def rotate():
    """`talk token --rotate`: a fresh token; prints the path, never the token."""
    try:
        p = token_store.rotate()
    except (token_store.TokenError, OSError) as e:
        print(f"talk token: {e}", file=sys.stderr)
        return 2
    print(f"talk token: a new token is in {p}. Every paired browser must pair again: run `talk pair`"
          f" and open its link. A running talk uses it at once; open streams end within"
          f" {server.KEEPALIVE} s.")
    return 0


def main(argv=None):
    from ears.home import warn_stranded
    warn_stranded("talk")   # a 0.1.0 settings file not moved yet: one line each, once per process
    ap = parser()
    args = ap.parse_args(argv)
    if args.cmd == "serve":
        try:                                    # before anything binds
            call_url = server.check_call_url(args.call_url)
        except ValueError as e:
            print(f"talk serve: --call-url / TALK_CALL_URL: {e}", file=sys.stderr)
            return 2
        try:                                    # likewise: env and flags, merged
            allow_hosts = (server.check_allow_hosts(os.environ.get("TALK_ALLOW_HOSTS"))
                           | server.check_allow_hosts(args.allow_host))
        except ValueError as e:
            print(f"talk serve: --allow-host / TALK_ALLOW_HOSTS: {e}", file=sys.stderr)
            return 2
        if args.kit and not args.unix:          # before anything binds
            print("talk serve: --kit needs --unix PATH (kit mode listens on a unix socket only)", file=sys.stderr)
            return 2
        if args.kit and args.proxy_uid is not None:
            print("talk serve: --proxy-uid does not apply with --kit: kit mode admits talk's own uid only",
                  file=sys.stderr)
            return 2
        if args.kit:                            # the kit's room: own uid only, no proxy uid
            unix = {"unix": args.unix, "kit": True}
        elif args.unix:                         # socket mode: no TCP port
            unix = {"unix": args.unix,
                    "proxy_uid": server.PROXY_UID if args.proxy_uid is None else args.proxy_uid}
        else:
            unix = {}
        try:
            server.serve(args.host, args.port, person=args.person,
                         smart_dir=args.smart_turn, settings_url=args.settings_url, call_url=call_url,
                         allow_hosts=allow_hosts, **unix)
        except (token_store.TokenError, server.UnixSocketError) as e:   # before anything binds
            print(f"talk serve: {e}", file=sys.stderr)
            return 2
        except KeyboardInterrupt:
            return 0
        return 0
    if args.cmd == "pair":
        return pair(args.url)
    if args.cmd == "token":
        if args.rotate:
            return rotate()
        print("talk token: say --rotate to replace the token (it is never printed; `talk pair` prints"
              " the pairing link)", file=sys.stderr)
        return 2
    ap.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
