"""Talk, made general for a kit install (EARS-242 do-003).

- seats: heard.state found in ears-arm's order ($EARS_HOME, the session-fleet
  seat home, ~/.<seat>, ~/.ears), read-only;
- with no heard.state and no `session` command, turns and mouth lines still
  stream (the rows are written through the kit's own ears.heard writer);
- VOICEMODE_BASE_DIR moves the log dir (ears.heard.log_dir(), imported);
- smart-turn is a setting, off by default;
- no seat name is hard-coded in src/talk.

HOME and VOICEMODE_BASE_DIR are temp dirs; every server binds a FREE port,
never 8780, with the state poll stubbed unless a test runs one pass itself.
"""
import http.client, json, os, re, socket, subprocess, threading
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

import pytest

from ears import heard
from talk import server, token_store
from tests import _scanlist

TALK_SRC = Path(server.__file__).parent


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A clean HOME and VOICEMODE_BASE_DIR, no EARS_HOME, settings at their defaults."""
    home, vm = tmp_path / "home", tmp_path / "vm"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(vm))
    monkeypatch.delenv("EARS_HOME", raising=False)
    server.configure()
    yield home, vm
    server.configure()


def state_file(d, offsets=None):
    d.mkdir(parents=True, exist_ok=True)
    (d / "heard.state").write_text(json.dumps({"offsets": offsets or {}}))
    return str(d / "heard.state")


# -- seats: the ears-arm order ------------------------------------------------

def test_seat_home_order(env, monkeypatch, tmp_path):
    home, _ = env
    assert server.seat_home("zed") == str(home / ".ears")          # nothing: the shared home
    assert not (home / ".ears").exists()                          # ...and it made nothing
    (home / ".zed").mkdir()
    assert server.seat_home("zed") == str(home / ".zed")          # ~/.<seat>
    (home / ".session/agents/home/zed").mkdir(parents=True)
    assert server.seat_home("zed") == str(home / ".session/agents/home/zed")   # the fleet home first
    monkeypatch.setenv("EARS_HOME", str(tmp_path / "eh"))
    assert server.seat_home("zed") == str(tmp_path / "eh")        # EARS_HOME over everything


def test_waiters_from_live_agents_and_fleet_homes(env):
    home, _ = env
    zed = state_file(home / ".zed")
    ana = state_file(home / ".session/agents/home/ana")
    (home / ".bob").mkdir()                                       # a home with no heard.state
    got = server.waiters(["zed", "bob", "..", "a/b", "", None])
    assert got == {"zed": zed, "ana": ana}


def test_shared_home_shows_once(env):
    home, _ = env
    shared = state_file(home / ".ears")
    assert server.waiters([]) == {"ears": shared}                 # nobody named: labelled "ears"
    assert server.waiters(["bob"]) == {"bob": shared}             # bob resolves there: not twice


def test_ears_home_is_every_seats_home(env, monkeypatch, tmp_path):
    eh = state_file(tmp_path / "eh")
    monkeypatch.setenv("EARS_HOME", str(tmp_path / "eh"))
    assert server.waiters(["zed"]) == {"zed": eh}


def test_receipts_key_is_the_kit_log_path(env):
    """A waiter's offsets are keyed by the heard log's path; the poll reads them
    at the kit's own path (VOICEMODE_BASE_DIR), so the tick still lands."""
    home, _ = env
    state_file(home / ".zed", {server.today_log(): 123})
    fake = subprocess.CompletedProcess(["session"], 0, json.dumps([{"agent": "zed", "session_id": "s1"}]), "")
    assert one_poll(fake)["receipts"] == {"zed": 123}


# -- no heard.state, no session command: rows still stream ----------------------

class _Stop(Exception):
    pass


def one_poll(run_result=None, run_error=None):
    run = mock.Mock(return_value=run_result, side_effect=run_error)
    with mock.patch.object(server.subprocess, "run", run), \
            mock.patch.object(server.time, "sleep", side_effect=_Stop):
        with pytest.raises(_Stop):
            server.poll_state()
    return server.state_now()


def test_no_session_command_no_seats_poll_is_empty_not_broken(env):
    st = one_poll(run_error=FileNotFoundError("session"))
    assert st == {"receipts": {}, "presence": {}, "ride": {}}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live(env):
    """`talk serve` on a free port, the poll stubbed; yields the port. The fenced
    install has its token, and SSE sends it as the pairing cookie (EARS-338)."""
    token_store.load_or_create()
    port = free_port()
    with mock.patch.object(server, "poll_state", lambda: None):
        srv = server.make_server("127.0.0.1", port)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            yield port
        finally:
            srv.shutdown()
            srv.server_close()
            t.join(5)


class SSE:
    """A minimal reader of /events: next() returns (event name or None, data)."""

    def __init__(self, port, query="n=150"):
        self.c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        self.c.request("GET", "/events?" + query,
                       headers={"Cookie": f"{server.COOKIE}={token_store.load()}"})
        self.r = self.c.getresponse()
        assert self.r.status == 200

    def next(self):
        name, data = None, None
        while True:
            line = self.r.fp.readline().decode()
            if not line:
                raise EOFError
            line = line.rstrip("\n")
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
            elif line == "" and data is not None:
                return name, data

    def close(self):
        self.c.close()


def test_turns_and_mouth_lines_stream_with_no_seats(env, live):
    home, vm = env
    heard.turn("is the kettle on", source="mic")
    heard.saying("it is", utt="u1", agent="claude")
    heard.said(utt="u1", agent="claude", played_s=0.8)
    assert not (home / ".ears").exists() and not (home / ".session").exists()
    sse = SSE(live)
    try:
        assert sse.next() == ("config", {"person": None, "settings_url": None, "call_url": None})
        name, rows = sse.next()
        assert name == "backfill"
        assert [r["kind"] for r in rows] == ["turn", "saying", "said"]
        assert rows[0]["text"] == "is the kettle on" and rows[1]["agent"] == "claude"
        heard.saying("and now?", utt="u2")                       # live, and with no agent
        name, row = sse.next()
        while name == "state":
            name, row = sse.next()
        assert name is None and row["kind"] == "saying" and "agent" not in row
    finally:
        sse.close()


# -- VOICEMODE_BASE_DIR -----------------------------------------------------------

def test_voicemode_base_dir_moves_the_log(env, monkeypatch, tmp_path):
    _, vm = env
    day = f"heard_{date.today():%Y-%m-%d}.jsonl"
    assert server.today_log() == str(vm / "logs" / "conversations" / day)
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "other"))
    assert server.today_log() == str(tmp_path / "other" / "logs" / "conversations" / day)
    heard.turn("over here")
    assert [r["text"] for r in server.backfill(server.today_log(), 10)] == ["over here"]


def test_ride_cursor_read_from_the_kit_cursor_dir(env):
    _, vm = env
    heard.save_cursor("s-1", heard.Cursor(41, os.path.basename(server.today_log())))
    assert (vm / "state" / "heard-cursor" / "s-1").is_file()
    fake = subprocess.CompletedProcess(["session"], 0, json.dumps([{"agent": "zed", "session_id": "s-1"}]), "")
    assert one_poll(fake)["ride"] == {"zed": 41}


# -- smart-turn: a setting, off by default ----------------------------------------

def smart_line(ts):
    return json.dumps({"kind": "offset", "ts": ts, "p": 0.9, "complete": True}) + "\n"


def test_smart_turn_off_by_default(env):
    home, _ = env
    old = home / ".<agent>/smart-turn-shadow/log"                # the old hard-coded place
    old.mkdir(parents=True)
    (old / f"smart_{date.today():%Y-%m-%d}.jsonl").write_text(smart_line("0000") * 3)
    heard.partial("hello")
    assert server.SMARTDIR is None and server.today_smart() is None
    assert server.smart_backfill("") == []
    assert [r["kind"] for r in server.backfill(server.today_log(), 10, debug=True)] == ["partial"]


def test_smart_turn_when_named(env, tmp_path):
    d = tmp_path / "smart"
    d.mkdir()
    (d / f"smart_{date.today():%Y-%m-%d}.jsonl").write_text("\n" + smart_line("9999"))
    heard.partial("hello")
    server.configure(smart_dir=str(d))
    kinds = [r["kind"] for r in server.backfill(server.today_log(), 10, debug=True)]
    assert kinds == ["partial", "smart"]


def test_settings_reach_the_page(env, live):
    server.configure(person="Zed", settings_url="/settings/", call_url="https://host1.t.ts.net:8775/")
    sse = SSE(live)
    try:
        assert sse.next() == ("config", {"person": "Zed", "settings_url": "/settings/",
                                         "call_url": "https://host1.t.ts.net:8775/"})
    finally:
        sse.close()


# -- the phone button (EARS-317; PHONE-139 design.md 5.4) ---------------------------

def test_call_url_null_by_default(env):
    assert server.SETTINGS["call_url"] is None
    server.configure(person="Zed")
    assert server.SETTINGS["call_url"] is None


def test_call_url_reaches_the_page_byte_for_byte(env, live):
    url = "http://127.0.0.1:8775/"
    server.configure(call_url=url)
    sse = SSE(live)
    try:
        name, cfg = sse.next()
        assert name == "config" and cfg["call_url"] == url
    finally:
        sse.close()


def test_configure_refuses_a_bad_call_url_and_keeps_the_old_settings(env):
    server.configure(person="Zed", call_url="https://host1/")
    for bad in ("javascript:alert(1)", "data:text/html,x", "http://example.com/"):
        with pytest.raises(ValueError):
            server.configure(person="Other", call_url=bad)
        assert server.SETTINGS == {"person": "Zed", "settings_url": None, "call_url": "https://host1/"}


class Anchors(HTMLParser):
    """Every <a> and every <iframe> in the page, with its attributes."""
    def __init__(self):
        super().__init__()
        self.a, self.iframes = {}, 0

    def handle_starttag(self, tag, attrs):
        if tag == "a" and dict(attrs).get("id"):
            self.a[dict(attrs)["id"]] = attrs
        self.iframes += tag == "iframe"


def page():
    return (TALK_SRC / "index.html").read_text()


def test_phone_button_markup_is_exact():
    """A top-level link: target=_blank, rel exactly "noopener noreferrer", hidden, no href
    until the config event names one (S1, S2)."""
    p = Anchors()
    p.feed(page())
    attrs = dict(p.a["call"])
    assert attrs["target"] == "_blank"
    assert attrs["rel"] == "noopener noreferrer"
    assert "hidden" in attrs and "href" not in attrs
    assert [k for k, _ in p.a["call"]].count("rel") == 1       # one rel, nothing to override it


def test_no_iframe_in_the_page():
    p = Anchors()
    p.feed(page())
    assert p.iframes == 0 and "iframe" not in page().lower()


def test_phone_button_href_is_call_url_verbatim_or_absent():
    """The config handler sets href to cfg.call_url and nothing else, and hides the
    button (no href) when it is null. call_url is never fetched or built upon (R5)."""
    js = page()
    line = next(l for l in js.splitlines() if "cfg.call_url" in l)
    assert line.strip() == ('if (cfg.call_url) { call.href = cfg.call_url; call.hidden = false; }'
                            ' else { call.removeAttribute("href"); call.hidden = true; }')
    uses = [l for l in js.splitlines() if "call_url" in l and not l.lstrip().startswith("//")]
    assert len(uses) == 2 and line in uses                       # that line, and cfg's comment of fields
    assert not re.search(r"call_url\s*[+`]|[+`]\s*cfg\.call_url|fetch\([^)]*call|\$\{[^}]*call_url", js)


def test_speaker_field_passes_to_the_page(env):
    heard.turn("mine", speaker="ana")
    assert server.backfill(server.today_log(), 5)[0]["speaker"] == "ana"


# -- nothing owner-specific left in src/talk ---------------------------------------

TALK_SHIPPED = [p for p in sorted(TALK_SRC.iterdir()) if p.suffix in (".py", ".html", ".json")]
# The only dot-folders under the home src/talk may name: the session fleet's and the ears'
# own. An agent's own dot-folder (~/.<seat>, as a placeholder, is fine) is a fixed roster.
HOME_DOTS = {".ears", ".session", ".sessionmail"}
# The text a src/talk file's help carries legitimately that the private scan list matches,
# for that file alone (measured): the tailnet proxy it documents, and the session fleet's seat
# home. A file's guard leaves out exactly the rows its samples match (by WHAT THEY MATCH,
# never by row name, so a list that renames its rows leaves the same rows out). Every other
# row guards it, and a file not named here keeps every row.
TALK_LEGIT = {
    "cli.py": ("tailscale serve", "machine.example.ts.net"),
    "index.html": ("tailscale serve",),
    "server.py": ("tailscale serve", "~/.session/agents/home/<seat>"),
}


def home_dots(text):
    return sorted(set(re.findall(r"~/(\.[A-Za-z0-9_-]+)", text)) - HOME_DOTS)


def test_no_agent_dot_folder_in_src_talk():
    """EARS-406: a shape, no list needed: src/talk names no dot-folder under the home but the
    fleet's and the ears' own."""
    assert TALK_SHIPPED
    hits = {p.name: home_dots(p.read_text()) for p in TALK_SHIPPED}
    assert {k: v for k, v in hits.items() if v} == {}
    assert home_dots("~/.session/agents ~/.<seat> ~/.vera/smart-turn ~/.ears.") == [".vera"]


def test_no_fixed_roster_or_vera_paths_in_src_talk():
    """EARS-406: no word of the private scan list in src/talk, and the same scan fires on each
    word planted from that list into a copy of server.py. Every row but the ones a file's help
    rightly hits (those its TALK_LEGIT samples match, per file): the agents' names are ruling
    rows, not P0, so a P0-only scan would miss the very names this guard is for."""
    assert set(TALK_LEGIT) <= {p.name for p in TALK_SHIPPED}     # no stale key
    missing = {name: [s for s in samples if s not in (TALK_SRC / name).read_text()]
               for name, samples in TALK_LEGIT.items()}
    assert {k: v for k, v in missing.items() if v} == {}         # a sample is the file's own text
    found = {p.name: names for p in TALK_SHIPPED
             if (names := _scanlist.hits(p.read_text(), _scanlist.rows(legit=TALK_LEGIT.get(p.name, ()))))}
    assert found == {}, f"src/talk holds a word of the private list: {found}"
    rows = _scanlist.rows(legit=TALK_LEGIT["server.py"])
    text = (TALK_SRC / "server.py").read_text()
    missed = [name for name, word in _scanlist.controls(rows)
              if name not in _scanlist.hits(f"{text}\nOWNER = {word!r}\n", rows)]
    assert missed == [], f"the scan missed a word planted from row(s) {missed}"
