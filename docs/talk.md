# Talk

Talk shows the live voice conversation in a browser: the person's partials and
turns, every line the mouth speaks, and which seats have heard what. It is
read-only, it binds 127.0.0.1 by default, and each browser pairs once before
it can see anything. Run it with `talk serve` and pair with `talk pair`.

- **Run:** `talk serve`, then `talk pair` in a terminal on the same host;
  open the link it prints.
- **Pairing:** one link per browser; the token never goes in a flag, an
  environment variable or a URL query.
- **On a tailnet:** optional. Keep talk on 127.0.0.1 and put Tailscale in
  front; socket mode is the safer way to do it.
- **In the kit's web page:** Talk is a room behind the kit's front door,
  which logs you in, so there is no pairing.
- **What it reads:** the heard log, each seat's waiter offsets, presence
  and faces. It writes nothing.
- **Settings:** all off by default; each one is a `talk serve` flag or an
  environment variable.

## Run

Talk ships in the sessionvoice wheel as the `talk` command (`src/talk/`).

    talk serve                      # http://127.0.0.1:8780/
    talk serve --port 8781          # another port
    talk serve --host 127.0.0.1     # the default: this machine only

- It binds 127.0.0.1 unless you pass `--host`. Stop it with Ctrl-C.
- **It answers only a Host it knows,** so a web page that rebinds its own
  DNS name to 127.0.0.1 cannot read the conversation. By default those are
  `127.0.0.1:<port>` and `localhost:<port>` on the port it is bound to, and
  the `--host` address when you name one. Any other Host, or an `Origin`
  that is not one of those names, gets a plain-text 403 that names the
  remedy. Add a name with `--allow-host NAME` (see [Settings][settings]).
  `X-Forwarded-*` headers are ignored.
- **It answers only a paired browser.** Every route but the page and the
  manifest needs the pairing cookie ([Pairing][pairing]); without it a
  request gets a plain-text 403 that names `talk pair`. The page and the
  manifest stay open: they are the package's own files and say nothing
  about the room. An open event stream checks its cookie again at each
  keep-alive (every 15 s), so after a rotation it ends within about 15 s.
- **As a service:** `ears services install --only talk` runs `talk serve`
  on 127.0.0.1:8780 as the launch agent `dev.sessionkit.voice.talk`, from an
  installed package or a checkout. It is opt-in: a bare
  `ears services install` never loads it.
  - It runs the `talk` installed beside the Python that runs `ears`, never
    the first `talk` on `PATH`: macOS ships an unrelated `/usr/bin/talk`.
    When there is none, install refuses and writes nothing.
  - Its log is `~/.sessionkit/voice/log/talk.log`. `ears services status` has a
    row for it, and `ears services uninstall --only talk` removes it.
  - `--dry-run` prints the plist and changes nothing.
  - To change the port or add a `TALK_*` variable, render
    `launchd/dev.sessionkit.voice.talk.plist.in` by hand instead. Its header
    carries the line that fills it in and the lines that load and remove it.
    Install leaves a hand render alone and skips its own job, so two never
    fight over the port.

## Pairing

Each browser pairs once. On the host, as the user who runs talk:

    talk pair                       # http://127.0.0.1:8780/#pair=<token>
    talk pair --url http://127.0.0.1:8781/
    talk pair --url https://machine.example.ts.net/talk    # behind tailscale serve
    talk token --rotate             # a new token: every browser pairs again

- **Open the link it prints** in the browser you want to pair. The token
  rides in the `#pair=` fragment, which never reaches a server, and the page
  drops it from the address bar before it sends anything. It then pairs and
  shows the room.
- **Or paste it.** A browser that is not paired shows a pairing panel: paste
  the link, or just its token, and the box is cleared at once. On a
  home-screen app, pair in Safari first, then add it to the Home Screen; if
  the app comes up unpaired, paste the link into its panel.
- **The page keeps the token nowhere:** not in `localStorage`,
  `sessionStorage` or any URL. Pairing sets an `HttpOnly`, `SameSite=Strict`
  cookie (plus `Secure` over https), with no `Path` and no `Domain`, so
  behind `tailscale serve` it stays under `/talk`.
- **Open talk at `http://127.0.0.1:<port>/`, never at `localhost`.**
  `talk pair` refuses a `localhost` or `*.localhost` URL (exit 2, naming
  `127.0.0.1`), and `talk serve` refuses to pair at `localhost`: a cookie
  set there would go to whatever answers `localhost`, which can be another
  user's listener on `[::1]`. `talk pair` refuses an IPv6 address such as
  `[::1]` too: talk binds IPv4 only.
- **It prints only to a terminal.** Piped, redirected, or run in an agent's
  tool shell, `talk pair` exits 2 and prints no token, so the link never
  lands in a file, a log or a transcript. Run it yourself.
- **`--url`** is talk's address as the browser opens it: `http` or `https`,
  with a host, and no query, fragment or user info. A trailing slash is
  added. It is only printed, never fetched. The default is
  `http://127.0.0.1:8780/`.
- **Rotate when in doubt:** after a link went somewhere it should not, or
  when a device is lost. `talk token --rotate` writes a fresh token in one
  step and prints the file's path, never the token. A running talk uses it
  at once, with no restart; open streams end within about 15 s, and every
  browser pairs again with a new `talk pair` link.

### What the token does not stop

It proves the browser to talk, never talk to the browser, and a browser
hands its cookie to whatever answers the address it dials.

- Another user on the same machine who *listens* can collect the cookie:
  on talk's port while talk is down (a restart, a deploy, a boot before you
  log in), or on any other 127.0.0.1 port whose page your browser opens,
  because cookies ignore the port. Socket mode ([On a
  tailnet][tailnet]) closes the first of these.
- Your own user's processes, root and an admin can read the token file in
  any case.

### The token file

- **Where:** `<home>/talk/token`, under sessionvoice's home ([where that
  is][home]). The file is 0600 and its `talk/` directory 0700, both owned by
  the user who runs talk.
- **What:** 43 characters of `A-Z a-z 0-9 _ -` (256 bits) and a newline.
- **Made once, whole,** by the first `talk serve`, before it binds. The
  file appears complete or not at all; two starts racing end up with the
  same token. `talk pair` reads it and never makes it.
- **Refused, never repaired.** Talk will not use a token file, or
  directory, that another user owns, that has any group or other permission
  bit, that is a symlink or not a plain file, or that does not hold a token.
  `talk serve` then exits 2 and serves nothing; the message names the path
  and the fix, never the token. A wider mode is not tightened for you: a
  token that was readable may be known, so rotate it.
- **A deleted token is never quietly re-made** while talk runs: nothing
  matches until you rotate, and talk's log names the fix on each refused
  request.
- **Never** in argv, a URL query, a log line, the launchd plist or the
  job's environment.

## On a tailnet

Optional, and never a default: nothing in sessionvoice runs Tailscale for
you.

- **Over TCP:** leave talk on 127.0.0.1 and put Tailscale in front, e.g.
  `tailscale serve --bg --set-path /talk http://127.0.0.1:8780`, then allow
  the name your devices use: `talk serve --allow-host
  machine.example.ts.net` (or `TALK_ALLOW_HOSTS=machine.example.ts.net`).
  tailscale serve passes the browser's Host through, with no port, so
  without it the phone gets the 403.
- **Behind a proxy, prefer socket mode:** `talk serve --unix` (or
  `TALK_UNIX_SOCKET=PATH`) binds a unix socket and **no TCP port**, so no
  other user can take talk's port while it is down and collect your
  browser's cookie. Map it with
  `tailscale serve --bg --set-path /talk unix:<PATH>`, with
  `--allow-host machine.example.ts.net` as before. PATH defaults to
  `<home>/talk/talk.sock`, beside the token.
  - The socket is 0600 in a 0700 directory. A directory with group or
    other bits, or a file at PATH that is not a stale socket of yours,
    exits 2 before anything binds. A unix socket path holds at most 103
    bytes.
  - Only talk's own uid and the proxy's may connect: `--proxy-uid UID`,
    default 0 (root, as tailscaled runs). Any other peer gets a 403 before
    its request is read.
  - Over the socket talk answers the proxy's `Host: localhost`, and pairs
    there: the browser's cookie belongs to the proxy's name. Origin is
    checked against `--allow-host`, and the pairing cookie is needed
    exactly as on TCP.
  - **Then `http://127.0.0.1:8780/` is not talk:** nothing of talk's holds
    that port, so anyone may. `talk pair` refuses a 127.0.0.1 URL, its
    default included, whenever `TALK_UNIX_SOCKET` is set or a socket sits
    at the default PATH. Pair with the proxy's address:
    `talk pair --url https://machine.example.ts.net/talk`.

## In the kit's web page

The kit's web page has a front door that logs you in and serves each
installed package's page as a room, under its own path. Talk is one, at
`/talk/`. Nothing here is needed to run Talk on its own.

- **How the front door finds Talk:** it asks for Talk's room descriptor,
  one JSON object with a name, a title, the package and the command that
  starts the room. A front door installed in the same environment as
  sessionvoice loads `talk.web:room` from the `sessionkit.web` entry-point
  group and calls it. From another environment it runs
  `sessionvoice-web web-room`, which prints the same object on one line and
  exits 0. It loads no audio or model code, so it answers well within the
  front door's 2 s.

      sessionvoice-web web-room
      {"name": "talk", "title": "Talk", "package": "sessionvoice", "command": ["<venv>/bin/python", "-I", "-m", "talk", "serve", "--kit", "--unix", "{socket}"]}

- **The command** is `talk serve --kit` on the unix socket the front door
  names (`{socket}`), run by the interpreter of the environment that
  answered. Nothing looks `talk` up on `PATH`, so the unrelated
  `/usr/bin/talk` on macOS is never run.
- **It runs isolated (`-I`):** the front door's working directory, the user
  site and every `PYTHON*` variable are left out, so a `talk.py` lying in
  that directory is never what is served. Install sessionvoice in that
  interpreter's own environment (a venv, `uv tool` or `pipx`); one reached
  only through the user site (`pip install --user`) or `PYTHONPATH` is not
  seen, and the room exits at start.

`talk serve --kit` is socket mode ([On a tailnet][tailnet]) with the changes
below. The front door runs it; you never run it by hand.

- **A unix socket and no TCP port.** `--kit` needs `--unix PATH` (or
  `TALK_UNIX_SOCKET`); without one it exits 2 before anything binds.
  `--host` and `--port` are unused, as with `--unix`.
- **Only talk's own uid may connect.** There is no proxy uid: `--kit` with
  `--proxy-uid` exits 2 before anything binds. Any other peer gets a 403
  before its request is read.
- **The front door's login is the one login.** Every request, the page and
  the manifest included, must carry exactly one `X-Sessionkit-User` header:
  non-empty printable ASCII, at most 64 characters. The front door sets it
  after it has removed any the browser sent. Without it a request gets a
  plain-text 403. There is no token: none is made or read, the page shows no
  pairing panel, and `POST pair` is a 404. The name in the header is not
  shown or logged. `talk pair` and `talk token` are unchanged; they are for
  Talk outside the kit.
- **Links resolve under any prefix.** Every link on the page is relative, so
  it resolves under the path the front door serves Talk at. Its
  `X-Sessionkit-Prefix` header is not read, and the page is served byte for
  byte. The manifest link asks for credentials
  (`crossorigin="use-credentials"`), so the manifest loads behind the front
  door's login and the page can still be added to a home screen.
- **Host and Origin.** The front door checks the browser's Host, forwards it
  as it came, and passes Talk no list of its names. So kit mode answers any
  well-formed Host, an IPv6 address in brackets included, except `localhost`
  and `*.localhost`, which the front door never forwards. No Host, more than
  one, or one that does not parse is refused. An `Origin`, when sent, must
  be `http` or `https` and name the same host and port as the Host, a
  missing port counting as the Origin's scheme default on both sides. Any
  other request gets a plain-text 403. `--allow-host` names are still
  answered.
- **At start** it prints two lines to the front door's room log: the socket
  it serves, and that it answers its own uid only, trusts
  `X-Sessionkit-User` and does no pairing.

## What it reads

Read-only; talk writes none of these.

- **Today's heard log,** where the kit's own writer puts it:
  `<home>/logs/conversations/heard_<date>.jsonl`, where `<home>` is
  sessionvoice's home ([where that is][home]).
- **Each seat's waiter offsets:** `heard.state` in the seat's home, found
  the way `ears-arm` finds it: `$EARS_HOME`, else
  `~/.session/agents/home/<seat>`, else `~/.<seat>`, else `~/.ears` (shown
  as "ears"). No heard.state at all is fine: turns and mouth lines still
  show; only the "scanned" ticks need one.
- **Presence and the "shown to" ticks:** `session list --json`
  (sessiontools), the heard-cursor (`<home>/state/heard-cursor`) and the
  pager's ride records. Without the `session` command those are simply
  empty.
- **Faces, if any:** `~/.session/agents/<name>/avatar.jpg`; else a coloured
  initial.

## Settings

All off or unset by default; each is a `talk serve` flag or an environment
variable.

| Flag | Env | What |
|---|---|---|
| `--person NAME` | `TALK_PERSON` | the label on the person's side; else a row's `speaker`, else "you" |
| `--smart-turn DIR` | `TALK_SMART_TURN_DIR` | a smart-turn shadow log dir (`smart_<date>.jsonl`) for the Debug view |
| `--settings-url URL` | `TALK_SETTINGS_URL` | a Settings link in the header; no link without it |
| `--call-url URL` | `TALK_CALL_URL` | a phone button in the header that opens this web call (sessionphone) in a new tab; no button without it. Only `https:`, or `http://localhost` / `http://127.0.0.1`; anything else is refused at start and nothing is served |
| `--allow-host NAME` | `TALK_ALLOW_HOSTS` | another Host to answer, beside `127.0.0.1:<port>`, `localhost:<port>` and the `--host` address. The flag repeats; the env var takes a comma- or space-separated list, and both are merged. `NAME` is answered on any port or none, `NAME:PORT` only on that port. Behind `tailscale serve`, add its name (`machine.example.ts.net`). A scheme, path, `@`, whitespace or `*` is refused at start and nothing is served |
| `--unix [PATH]` | `TALK_UNIX_SOCKET` | bind a unix socket and no TCP port ([On a tailnet][tailnet]) |
| `--proxy-uid UID` | | in socket mode, the one other uid that may connect; default 0; not with `--kit` |
| `--kit` | | run as a room behind the kit's web front door; needs `--unix`. The front door runs it, never you ([In the kit's web page][kit]) |

`SESSIONVOICE_HOME` moves the heard log and the token, the same as for the
ears.

[home]: logs.md#sessionvoices-home
[kit]: #in-the-kits-web-page
[pairing]: #pairing
[settings]: #settings
[tailnet]: #on-a-tailnet
