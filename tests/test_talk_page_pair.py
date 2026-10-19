"""The page's half of pairing (EARS-338, S4): src/talk/index.html.

The page reads `#pair=<token>`, drops it from the address bar (history.replaceState)
before it sends anything, POSTs the token once in an X-Talk-Token header to "pair",
then connects. A pairing panel takes a pasted link or bare token and clears its input.
When the EventSource is CLOSED it asks GET pair: a 403 shows the panel and stops the
3-second retry; anything else retries as before. The token never goes in
localStorage, sessionStorage or any URL.

Static tests, in the style of tests/test_talk_generalise.py's page tests, read the
page's text. A behaviour test runs the page's script under node with a fake DOM, and
skips when node is not installed. A real browser is verify-001's, not this file's.
"""
import json, re, shutil, subprocess
from pathlib import Path

import pytest

from talk import server

PAGE = Path(server.__file__).parent / "index.html"


def page():
    return PAGE.read_text()


def script():
    js = page()
    return js[js.index("<script>") + len("<script>"):js.rindex("</script>")]


def code_lines():
    """The script's lines, without whole-line comments."""
    return [ln.strip() for ln in script().splitlines() if ln.strip() and not ln.strip().startswith("//")]


def uncommented(text):
    """Text with // comments dropped: a whole-line comment, or one after whitespace (a URL's
    "http://" has none before it)."""
    out = []
    for ln in text.splitlines():
        if ln.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", ln))
    return "\n".join(out)


def block(start, end):
    js = script()
    return js[js.index(start):js.index(end, js.index(start))]


def function(name):
    """The text of `function name(...) { ... }`, by brace matching."""
    js = script()
    i = js.index(f"function {name}(")
    j = js.index("{", i)
    depth = 0
    for k in range(j, len(js)):
        depth += {"{": 1, "}": -1}.get(js[k], 0)
        if depth == 0:
            return js[i:k + 1]
    raise AssertionError(f"unbalanced function {name}")


# -- the order: drop the fragment, then pair, then connect ----------------------------

def test_start_drops_the_fragment_before_it_pairs_or_connects():
    start = script()[script().index("if (!redirecting) {"):]
    lines = [ln.strip() for ln in start.splitlines()]
    replace = next(i for i, ln in enumerate(lines) if "history.replaceState" in ln)
    first_send = next(i for i, ln in enumerate(lines) if "tryPair(" in ln or "connect()" in ln)
    assert replace < first_send
    assert lines[replace] == ('if (location.hash.startsWith("#pair=")) '
                              'history.replaceState(null, "", location.pathname + location.search);')


def test_fetch_is_called_in_two_places_only():
    """POST pair (pairWith) and the GET pair probe (probe); no fetch runs at load."""
    calls = [ln for ln in code_lines() if "fetch(" in ln]
    assert len(calls) == 2
    assert any(ln.startswith("const pairWith = tok => fetch(PAIR,") for ln in calls)
    assert any(ln.startswith("fetch(PAIR, {cache:") for ln in calls)
    assert 'const PAIR = "pair";' in script()


def test_nothing_is_sent_while_the_slash_redirect_runs():
    """/talk without its slash: a relative "pair" would be the root's /pair, another app's
    on a shared origin. Pairing and connect() start only inside `if (!redirecting)`."""
    js = script()
    assert "const redirecting = !location.pathname.endsWith(\"/\");" in js
    assert "if (redirecting) location.replace(" in js
    top = [ln for ln in js.splitlines() if not ln.startswith((" ", "\t"))]      # top-level statements
    assert not any(re.match(r"(connect|tryPair|probe)\(", ln) for ln in top)


# -- the token's one use: X-Talk-Token on a POST to "pair" ------------------------------

def test_the_token_is_used_only_in_the_pair_header():
    """Every line that touches a token variable (tok, fromLink) is one of these, exactly:
    no URL, no storage, no template string, no concatenation is built from it."""
    uses = [ln for ln in code_lines() if re.search(r"\b(tok|fromLink)\b", ln)]
    assert uses == [
        'const pairWith = tok => fetch(PAIR, {method: "POST", headers: {"X-Talk-Token": tok}, '
        'credentials: "same-origin", cache: "no-store"})',
        "function tryPair(tok) {          // 204: paired, connect. 403: refused, say so. "
        "No answer: connect and let probe() decide",
        "return pairWith(tok).then(st => st === 204 ? paired()",
        "const tok = tokenIn(pairInput.value); pairInput.value = \"\";   // the input never keeps it",
        'if (tok) tryPair(tok); else unpaired("Paste the whole link that talk pair printed, or its token.");',
        'const fromLink = tokenIn(location.hash.startsWith("#pair=") ? location.hash : "");',
        "if (fromLink) tryPair(fromLink); else connect();",
    ]


def test_the_pair_post_has_no_body_and_one_header():
    line = next(ln for ln in code_lines() if ln.startswith("const pairWith"))
    assert '{method: "POST", headers: {"X-Talk-Token": tok}' in line
    assert "body" not in line                       # the server never reads one (S3 review, obs. 1)
    assert sum("X-Talk-Token" in ln for ln in code_lines()) == 1


def test_token_shape_matches_the_token_store():
    """tokenIn accepts what token_store writes (43+ urlsafe characters), as a bare token or
    after #pair= at the end of a link."""
    line = next(ln for ln in code_lines() if ln.startswith("const tokenIn"))
    assert "[A-Za-z0-9_-]{43,}" in line and "#pair=" in line


# -- never stored ---------------------------------------------------------------------

def test_no_storage_near_pair():
    code = uncommented(block("// -- pairing --", "// -- end pairing")).splitlines()
    assert not any(w in ln for ln in code for w in ("localStorage", "sessionStorage", "document.cookie",
                                                    "indexedDB", "caches."))
    assert "sessionStorage" not in uncommented(script())
    assert "document.cookie" not in uncommented(script())
    stored = [ln for ln in code_lines() if "localStorage" in ln]
    assert stored and all('"talk-debug"' in ln for ln in stored)   # the debug switch, and nothing else


def test_the_paste_box_clears_and_is_not_remembered():
    m = re.search(r"<input id=\"pair-input\"[^>]*>", page())
    assert m and 'autocomplete="off"' in m.group(0) and 'type="text"' in m.group(0)
    assert 'pairInput.value = ""' in script()


# -- a 403 probe shows the panel and stops the retry -----------------------------------

def test_onerror_probes_instead_of_retrying_blindly():
    on = block("es.onerror = () => {", "\n  };")
    assert "setTimeout" not in on
    assert "if (es.readyState === EventSource.CLOSED && !retry && !probing) probe();" in on


def test_a_403_probe_schedules_no_retry():
    body = function("probe")
    lines = [ln.strip() for ln in body.splitlines()]
    stop = lines.index("if (st === 403) return unpaired();")
    again = next(i for i, ln in enumerate(lines) if "retry = setTimeout" in ln)
    assert stop < again                              # the 403 returns before the retry is scheduled
    assert "connect" in lines[again]                 # anything else: retry in 3 s, as before
    assert "3000" in lines[again]


def test_unpaired_never_reconnects():
    body = uncommented(function("unpaired"))
    assert "setTimeout" not in body and "connect()" not in body and "retry" not in body
    assert "pairing.hidden = false" in body and "es.close()" in body


# -- behaviour, under node with a fake DOM (skipped without node) -------------------------

HARNESS = r"""
const vm = require("vm");
const [script, scenario] = [process.argv[2], JSON.parse(process.argv[3])];
const log = [];
const els = {};
function el(id) {
  if (!els[id]) els[id] = {id, hidden: id === "pairing" || id === "pair-why", textContent: "", value: "",
    innerHTML: "", dataset: {}, style: {}, href: "",
    classList: {add() {}, remove() {}, toggle() {}, contains() { return false; }},
    listeners: {}, addEventListener(t, f) { this.listeners[t] = f; }, removeAttribute() {},
    appendChild() {}, querySelector() { return null; }};
  return els[id];
}
const url = new URL(scenario.url);
const location = {href: url.href, pathname: url.pathname, search: url.search, hash: url.hash,
  hostname: url.hostname, port: url.port,
  replace(u) { log.push(["location.replace", u]); }};
const history = {replaceState(a, b, u) { log.push(["replaceState", u]); location.hash = ""; }};
const pending = [];
function fetch(u, opts = {}) {
  log.push(["fetch", u, opts.method || "GET", JSON.stringify(opts.headers || {}), "body" in opts]);
  const status = scenario.fetch.shift();
  return status === "neterr" ? Promise.reject(new Error("net")) : Promise.resolve({status});
}
class EventSource {
  constructor(u) { this.url = u; this.readyState = 0; EventSource.made.push(this); log.push(["EventSource", u]); }
  addEventListener() {}
  close() { this.readyState = 2; }
}
EventSource.CLOSED = 2; EventSource.made = [];
const store = {};
const ctx = {
  document: {getElementById: el, querySelector() { return null; }, querySelectorAll() { return []; },
             createElement() { return el("x" + Math.random()); }, body: {scrollHeight: 0}},
  location, history, fetch, EventSource,
  localStorage: {getItem(k) { return store[k] || null; }, setItem(k, v) { store[k] = v; log.push(["localStorage", k, v]); }},
  setTimeout(f, ms) { log.push(["setTimeout", ms]); return 1; }, setInterval() { return 1; },
  addEventListener() {}, scrollTo() {}, innerHeight: 0, scrollY: 0, console, URL, Promise, JSON,
};
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(script, ctx);
const tick = () => new Promise(r => setImmediate(r));
(async () => {
  for (let i = 0; i < 5; i++) await tick();
  for (const step of scenario.steps || []) {
    if (step === "close") { const es = EventSource.made[EventSource.made.length - 1]; es.readyState = 2; es.onerror(); }
    if (step.paste !== undefined) { el("pair-input").value = step.paste; el("pair-form").listeners.submit({preventDefault() {}}); }
    for (let i = 0; i < 5; i++) await tick();
  }
  console.log(JSON.stringify({log, pairing_hidden: el("pairing").hidden, why: el("pair-why").textContent,
                              input: el("pair-input").value, status: el("status").textContent, store}));
})();
"""

TOKEN = "T" * 21 + "_-" + "k" * 20                   # 43 urlsafe characters, like token_store's


@pytest.fixture
def run(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("no node on PATH: the static tests above cover the page")
    (tmp_path / "harness.js").write_text(HARNESS)
    (tmp_path / "page.js").write_text(script())

    def go(url, fetch=(), steps=()):
        out = subprocess.run([node, str(tmp_path / "harness.js"), script(),
                              json.dumps({"url": url, "fetch": list(fetch), "steps": list(steps)})],
                             capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, out.stderr[-2000:]
        return json.loads(out.stdout)
    return go


def names(res):
    return [e[0] for e in res["log"]]


def test_link_drops_fragment_then_posts_then_connects(run):
    res = run(f"http://127.0.0.1:8781/#pair={TOKEN}", fetch=[204])
    assert names(res) == ["replaceState", "fetch", "EventSource"]
    assert res["log"][0] == ["replaceState", "/"]
    assert res["log"][1] == ["fetch", "pair", "POST", json.dumps({"X-Talk-Token": TOKEN}, separators=(",", ":")),
                             False]
    assert res["log"][2] == ["EventSource", "events?n=150"]
    assert res["pairing_hidden"] is True
    assert TOKEN not in json.dumps(res["store"])


def test_behind_tailscale_the_path_is_kept_and_pair_is_relative(run):
    res = run(f"https://talk.example.ts.net/talk/#pair={TOKEN}", fetch=[204])
    assert res["log"][0] == ["replaceState", "/talk/"]
    assert res["log"][1][1] == "pair"                # resolved against /talk/: /talk/pair


def test_a_refused_link_shows_the_panel_and_does_not_connect(run):
    res = run(f"http://127.0.0.1:8781/#pair={TOKEN}", fetch=[403])
    assert names(res) == ["replaceState", "fetch"]
    assert res["pairing_hidden"] is False and "did not pair" in res["why"]


def test_localhost_says_to_open_127(run):
    res = run(f"http://localhost:8781/#pair={TOKEN}", fetch=[403])
    assert "open http://127.0.0.1:8781/ and pair there" in res["why"]


def test_no_slash_redirects_and_sends_nothing(run):
    res = run(f"https://talk.example.ts.net/talk#pair={TOKEN}")
    assert names(res) == ["location.replace"]
    assert res["log"][0][1] == f"/talk/#pair={TOKEN}"


def test_no_link_connects_and_a_403_probe_stops(run):
    res = run("http://127.0.0.1:8781/", fetch=[403], steps=["close"])
    assert names(res) == ["EventSource", "fetch"]
    assert res["log"][1][:3] == ["fetch", "pair", "GET"]
    assert "setTimeout" not in names(res)
    assert res["pairing_hidden"] is False and res["status"] == "not paired"


@pytest.mark.parametrize("status", [502, "neterr", 204])
def test_any_other_probe_answer_retries_in_3s(run, status):
    res = run("http://127.0.0.1:8781/", fetch=[status], steps=["close"])
    assert ["setTimeout", 3000] in res["log"]
    assert res["pairing_hidden"] is True


@pytest.mark.parametrize("pasted", [TOKEN, f"http://127.0.0.1:8781/#pair={TOKEN}",
                                    f"  https://talk.example.ts.net/talk/#pair={TOKEN}\n"])
def test_paste_box_takes_a_link_or_a_token_and_clears(run, pasted):
    res = run("http://127.0.0.1:8781/", fetch=[403, 204], steps=["close", {"paste": pasted}])
    posts = [e for e in res["log"] if e[0] == "fetch" and e[2] == "POST"]
    assert posts == [["fetch", "pair", "POST", json.dumps({"X-Talk-Token": TOKEN}, separators=(",", ":")), False]]
    assert res["input"] == ""
    assert names(res)[-1] == "EventSource" and res["pairing_hidden"] is True


@pytest.mark.parametrize("pasted", ["", "short", "http://x/#pair=short", f"{TOKEN} extra", f"#other={TOKEN}"])
def test_paste_box_refuses_junk_and_sends_nothing(run, pasted):
    res = run("http://127.0.0.1:8781/", fetch=[403], steps=["close", {"paste": pasted}])
    assert not any(e[0] == "fetch" and e[2] == "POST" for e in res["log"])
    assert res["input"] == "" and "Paste the whole link" in res["why"]
