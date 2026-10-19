"""EARS-291 do-001: how sessionvoice finds the mlx-audio speech server on :8890 (src/ears/speech.py).

- decide() takes design.md's steps 1-4 top to bottom, and every row of its trade-off table has
  a test (fakes only);
- never two servers: an occupied port, or a loaded mlx_audio.server job with the port closed,
  never yields install;
- the real GET / and /openapi.json probes run against a stub HTTP server on an ephemeral port,
  and the stub records that nothing else was asked (no POST, no /v1/audio/voices);
- `ears services status` prints a speech row naming the holder, or none.

Nothing here asks the live :8890: the stubs bind 127.0.0.1:0, and status runs inside
test_services_install_checks' fence, whose speech probes are fakes."""
import http.server
import json
import os
import plistlib
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

from ears import services, speech
from ears.speech import Decision, Holder, Job, Probe, decide
from test_services_install_checks import fence  # noqa: F401  (a fixture)

WELCOME = {"message": "Welcome to the MLX Audio API server! Visit https://localhost:3000 for the UI."}
VM = Holder(pid=2000, command="/x/mlx-audio/bin/python /x/mlx_audio.server --host 127.0.0.1 --port 8890",
            label="com.voicemode.mlx-audio")
FOREIGN = Holder(pid=3000, command="/usr/bin/python3 -m http.server 8890", label=None)
VM_JOB_DOWN = Job("com.voicemode.mlx-audio", Path("/x/com.voicemode.mlx-audio.plist"), pid="-")
DEFAULT = "http://127.0.0.1:8890/v1"


def P(**kw):
    return Probe(**kw)


@pytest.fixture(autouse=True)
def no_host_plists(monkeypatch, tmp_path):
    """The plist scan reads this temp tree, never the host's /Library, and launchctl print
    answers only from ``system`` ({label: its print output})."""
    system = {}
    monkeypatch.setattr(speech, "SYSTEM_AGENTS", tmp_path / "sys/LaunchAgents")
    monkeypatch.setattr(speech, "SYSTEM_DAEMONS", tmp_path / "sys/LaunchDaemons")
    real = speech._run

    def run(argv, timeout=10.0):
        if argv[:2] == ["launchctl", "print"]:
            label = argv[2].split("/", 1)[1]
            out = system.get(label)
            return subprocess.CompletedProcess(argv, 113 if out is None else 0, out or "", "")
        if argv[0] in ("lsof", "ps"):
            return real(argv, timeout)          # read-only; a fake replaces it where it matters
        raise AssertionError(f"unexpected command {argv}")
    monkeypatch.setattr(speech, "_run", run)
    return system


# ---- the constants: the defaults are the live ones; the label is never VoiceMode's ----------

def test_defaults_are_the_ears_and_mouth_server():
    assert (speech.HOST, speech.PORT) == ("127.0.0.1", 8890)
    assert speech.default_url() == DEFAULT
    assert speech.LABEL == "dev.sessionkit.voice.mlx-audio" and not speech.LABEL.startswith("com.voicemode.")


# ---- decide(): steps 1-4 ----------------------------------------------------------------------

def test_step1_final_url_elsewhere_and_no_mouth_opts_out():
    d = decide(P(), final_url="http://127.0.0.1:2023/v1", mouth=False)
    assert d.kind == "opt-out" and not d.installs and not d.fatal
    assert d.line == "speech: final.url is http://127.0.0.1:2023/v1; not managing :8890"


def test_step1_the_mouth_still_needs_the_port_so_no_opt_out():
    """design-001 reviewer fix: an installed mouth always speaks Kokoro on :8890 (RT-04)."""
    assert decide(P(), final_url="http://127.0.0.1:2023/v1", mouth=True).kind == "install"
    d = decide(P(holder=VM, connects=True, identity="mlx-audio"), final_url="https://api.example/v1", mouth=True)
    assert d.kind == "use"


@pytest.mark.parametrize("url", [None, DEFAULT, DEFAULT + "/", "http://localhost:8890/v1", "http://127.0.0.1:8890"])
def test_step1_the_default_url_in_any_spelling_is_not_an_opt_out(url):
    assert decide(P(), final_url=url).kind == "install"


def test_step2_mlx_audio_on_the_port_is_used_and_named():
    d = decide(P(holder=VM, connects=True, identity="mlx-audio", why="welcome"))
    assert d.kind == "use" and not d.installs and not d.fatal
    assert d.line == "speech: using mlx-audio on :8890 (pid 2000, launchd com.voicemode.mlx-audio)"


def test_step2_a_holder_with_no_label_is_named_by_its_command():
    h = Holder(pid=41, command="/opt/mlx/bin/python -m mlx_audio.server --port 8890")
    d = decide(P(holder=h, connects=True, identity="mlx-audio"))
    assert d.kind == "use" and "pid 41, /opt/mlx/bin/python -m mlx_audio.server --port 8890" in d.line


def test_step2_a_root_listener_lsof_cannot_see_is_still_held_by_the_connect():
    """design OQ6 reviewer note: non-root lsof misses a root daemon; the connect catches it."""
    d = decide(P(holder=None, connects=True, identity="foreign", why="GET / answered 404"))
    assert d.kind == "refuse-foreign" and "holder unknown" in d.line


def test_step2_a_foreign_server_is_refused_loudly_when_the_ears_need_the_port():
    d = decide(P(holder=FOREIGN, connects=True, identity="foreign", why="GET / answered 404 b'nope'"))
    assert d.kind == "refuse-foreign" and d.fatal and not d.installs
    assert "REFUSED" in d.line and "pid 3000, /usr/bin/python3 -m http.server 8890" in d.line
    assert "point final.url" in d.line


def test_step2_a_foreign_server_only_warns_when_only_the_mouth_wants_the_port():
    d = decide(P(holder=FOREIGN, connects=True, identity="foreign"), final_url="http://127.0.0.1:2023/v1", mouth=True)
    assert d.kind == "refuse-foreign" and not d.fatal and "`say`" in d.line


def test_step2_a_wedged_server_is_occupied_unknown_never_none():
    d = decide(P(holder=VM, connects=True, identity="no-answer", why="GET / did not answer in 2s"))
    assert d.kind == "refuse-foreign" and "does not answer" in d.line and d.fatal


def test_step3_a_loaded_job_with_the_port_closed_is_never_a_second_server():
    d = decide(P(jobs=(VM_JOB_DOWN,)))
    assert d.kind == "refuse-loaded-down" and not d.installs
    assert d.line == "speech: com.voicemode.mlx-audio is loaded but :8890 is not answering; not installing a second server"


def test_step3_a_plist_that_is_not_loaded_does_not_block():
    d = decide(P(jobs=(Job("com.voicemode.mlx-audio", Path("/x.plist"), pid=None),)))
    assert d.kind == "install"


def test_step4_nothing_there_installs_ours():
    d = decide(P())
    assert d.kind == "install" and d.installs and not d.fatal
    assert d.line == "speech: nothing on :8890 and no mlx-audio job loaded; install dev.sessionkit.voice.mlx-audio"


def test_the_port_is_a_parameter_in_every_line():
    d = decide(P(port=51234, holder=VM, connects=True, identity="mlx-audio"))
    assert "on :51234" in d.line
    assert decide(P(port=51234), final_url="http://127.0.0.1:51234/v1").kind == "install"
    assert decide(P(port=51234), final_url=DEFAULT).kind == "opt-out"


# ---- design.md's trade-off table, one test per column ----------------------------------------
# | foreign on :8890 | job restarting | non-127 bind | label/installer unknown | setting

def test_table_foreign_on_the_port_is_detected_not_used():
    assert decide(P(holder=FOREIGN, connects=True, identity="foreign")).kind == "refuse-foreign"


def test_table_job_restarting_is_detected_not_a_second_install():
    # VoiceMode's KeepAlive job between restarts: the port is closed, the job is loaded
    assert decide(P(holder=None, connects=False, jobs=(VM_JOB_DOWN,))).kind == "refuse-loaded-down"


def test_table_a_bind_the_connect_misses_is_seen_by_lsof():
    # bound to ::1 or a LAN address only: 127.0.0.1's connect fails, lsof still sees it
    d = decide(P(holder=VM, connects=False, identity="no-answer", why="refused"))
    assert d.kind == "refuse-foreign" and not d.installs


def test_table_an_unknown_label_still_counts_when_it_runs_mlx_audio():
    job = Job("homebrew.mxcl.mlx-audio", Path("/x.plist"), pid="-")
    assert decide(P(jobs=(job,))).kind == "refuse-loaded-down"


def test_table_the_setting_opts_out():
    assert decide(P(holder=FOREIGN, connects=True, identity="foreign"), final_url="http://box:9000/v1").kind == "opt-out"


# ---- never two servers -----------------------------------------------------------------------

OCCUPIED = {
    "mlx-audio": P(holder=VM, connects=True, identity="mlx-audio"),
    "mlx-audio, lsof blind": P(holder=None, connects=True, identity="mlx-audio"),
    "foreign": P(holder=FOREIGN, connects=True, identity="foreign"),
    "wedged": P(holder=VM, connects=True, identity="no-answer"),
    "wedged, lsof blind": P(holder=None, connects=True, identity="no-answer"),
    "listener the connect misses": P(holder=VM, connects=False, identity="no-answer"),
    "loaded job, port closed": P(jobs=(VM_JOB_DOWN,)),
    "loaded job, running elsewhere in its cycle": P(jobs=(Job("x.mlx", Path("/x.plist"), pid="77"),)),
}


@pytest.mark.parametrize("name", OCCUPIED)
@pytest.mark.parametrize("final_url,mouth", [(None, False), (None, True), ("http://127.0.0.1:2023/v1", True)])
def test_never_two_an_occupied_port_never_installs(name, final_url, mouth):
    assert not decide(OCCUPIED[name], final_url=final_url, mouth=mouth).installs


# ---- the probes, on fakes --------------------------------------------------------------------

def _ran(monkeypatch, answers):
    seen = []

    def run(argv, timeout=10.0):
        seen.append(argv)
        out = answers.get(argv[0])
        return None if out is None else subprocess.CompletedProcess(argv, out[0], out[1], "")
    monkeypatch.setattr(speech, "_run", run)
    return seen


def test_port_holder_names_pid_command_and_label(monkeypatch):
    seen = _ran(monkeypatch, {"lsof": (0, "p2000\nf12\np2001\n"), "ps": (0, VM.command + "\n")})
    h = speech.port_holder(8890, {"com.voicemode.mlx-audio": "2000", "other": "9"})
    assert h == VM
    assert seen[0] == ["lsof", "-nP", "-iTCP:8890", "-sTCP:LISTEN", "-Fp"]   # LISTEN, any address
    assert seen[1] == ["ps", "-o", "command=", "-p", "2000"]


def test_port_holder_is_none_when_nothing_listens_or_there_is_no_lsof(monkeypatch):
    _ran(monkeypatch, {"lsof": (1, "")})
    assert speech.port_holder(8890, {}) is None
    _ran(monkeypatch, {})                                   # no lsof on this host
    assert speech.port_holder(8890, {}) is None


def test_port_holder_without_ps_still_has_the_pid(monkeypatch):
    _ran(monkeypatch, {"lsof": (0, "p5\n")})
    assert speech.port_holder(8890, {}) == Holder(pid=5, command=None, label=None)


def _plist(dir_, label, args=None, program=None):
    d = {"Label": label}
    if args is not None:
        d["ProgramArguments"] = args
    if program is not None:
        d["Program"] = program
    p = dir_ / f"{label}.plist"
    p.write_bytes(plistlib.dumps(d))
    return p


def test_loaded_jobs_finds_mlx_audio_under_any_label(tmp_path):
    vm = _plist(tmp_path, "com.voicemode.mlx-audio",   # VoiceMode hides it in a sh -c string
                ["/bin/sh", "-c", "exec /x/mlx-audio/bin/python -m mlx_audio.server --host 127.0.0.1 --port 8890"])
    _plist(tmp_path, "homebrew.mxcl.mlx", program="/opt/homebrew/bin/mlx_audio.server")
    _plist(tmp_path, "dev.sessionkit.voice.ears-keeper", ["/bin/sh", "ears-keeper.sh"])           # not a server
    _plist(tmp_path, "elsewhere", ["mlx_audio.server", "--port=8000"])                    # another port
    (tmp_path / "broken.plist").write_text("not a plist")
    jobs = speech.loaded_jobs(tmp_path, {"com.voicemode.mlx-audio": "-"}, 8890)
    assert [j.label for j in jobs] == ["com.voicemode.mlx-audio", "homebrew.mxcl.mlx"]
    assert jobs[0] == Job("com.voicemode.mlx-audio", vm, "-") and jobs[0].loaded
    assert not jobs[1].loaded                                # its plist is there, launchd has not loaded it


def test_loaded_jobs_reads_the_system_dirs_too(tmp_path, no_host_plists):
    """Engineer r2: a system job is invisible to a non-root lsof, so a loaded one that is down
    must still stop an install. /Library/LaunchAgents is in the user's launchctl list;
    /Library/LaunchDaemons answers `launchctl print system/LABEL`."""
    sa, sd = speech.SYSTEM_AGENTS, speech.SYSTEM_DAEMONS
    sa.mkdir(parents=True)
    sd.mkdir(parents=True)
    _plist(sa, "org.site.mlx-agent", ["/opt/mlx/bin/mlx_audio.server", "--port", "8890"])
    _plist(sd, "org.site.mlx-daemon", ["/opt/mlx/bin/python", "-m", "mlx_audio.server", "--port", "8890"])
    _plist(sd, "org.site.idle-daemon", ["/opt/mlx/bin/mlx_audio.server"])
    no_host_plists["org.site.mlx-daemon"] = "system/org.site.mlx-daemon = {\n\tstate = waiting\n}\n"
    jobs = {j.label: j for j in speech.loaded_jobs(tmp_path / "user", {"org.site.mlx-agent": "-"})}
    assert jobs["org.site.mlx-agent"].pid == "-"
    assert jobs["org.site.mlx-daemon"].pid == "-" and jobs["org.site.mlx-daemon"].loaded   # loaded, down
    assert not jobs["org.site.idle-daemon"].loaded                                         # launchd: unknown
    no_host_plists["org.site.mlx-daemon"] = "system/org.site.mlx-daemon = {\n\tpid = 812\n}\n"
    assert {j.label: j.pid for j in speech.loaded_jobs(tmp_path / "user", {})}["org.site.mlx-daemon"] == "812"


def test_never_two_a_loaded_system_daemon_that_is_down_blocks_install(tmp_path, no_host_plists, monkeypatch):
    sd = speech.SYSTEM_DAEMONS
    sd.mkdir(parents=True)
    _plist(sd, "org.site.mlx-daemon", ["/opt/mlx/bin/mlx_audio.server", "--port", "8890"])
    no_host_plists["org.site.mlx-daemon"] = "system/org.site.mlx-daemon = {\n\tstate = waiting\n}\n"
    monkeypatch.setattr(speech, "port_holder", lambda port, listed: None)   # root's: lsof is blind
    monkeypatch.setattr(speech, "connects", lambda host, port: False)       # and it is down
    d = decide(speech.probe(tmp_path / "user", {}))
    assert d.kind == "refuse-loaded-down" and not d.installs and "org.site.mlx-daemon" in d.line


def test_loaded_jobs_with_no_agents_dir_is_empty(tmp_path):
    assert speech.loaded_jobs(tmp_path / "missing", {}) == ()


def test_probe_asks_nothing_when_nothing_holds_the_port(monkeypatch, tmp_path):
    monkeypatch.setattr(speech, "port_holder", lambda port, listed: None)
    monkeypatch.setattr(speech, "connects", lambda host, port: False)
    monkeypatch.setattr(speech, "identify", lambda *a, **k: pytest.fail("a GET with nothing on the port"))
    p = speech.probe(tmp_path, {}, port=51111)
    assert not p.held and p.identity is None and decide(p).installs


# ---- the real GETs, against a stub on an ephemeral port --------------------------------------

class Stub:
    """A local HTTP server on 127.0.0.1:0 that answers GET / and GET /openapi.json as told, and
    records every request it gets."""

    def __init__(self, root, openapi=None, root_status=200):
        self.seen = []
        stub = self

        class H(http.server.BaseHTTPRequestHandler):
            def _send(self, status, body):
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                stub.seen.append(("GET", self.path))
                if self.path == "/":
                    self._send(root_status, root)
                elif self.path == "/openapi.json" and openapi is not None:
                    self._send(200, openapi)
                else:
                    self._send(404, {"detail": "Not Found"})

            def do_POST(self):
                stub.seen.append(("POST", self.path))
                self._send(500, b"")

            def log_message(self, *a):
                pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        assert self.port != 8890
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def stub():
    made = []

    def make(*a, **k):
        s = Stub(*a, **k)
        made.append(s)
        return s
    yield make
    for s in made:
        s.close()
        assert all(m == "GET" and p in ("/", "/openapi.json") for m, p in s.seen), s.seen


MLX_PATHS = {"/": {}, "/v1/models": {}, "/v1/audio/speech": {}, "/v1/audio/transcriptions": {},
             "/v1/audio/voices": {}, "/v1/audio/separations": {}}


def test_identify_the_welcome_line_is_mlx_audio(stub):
    s = stub(WELCOME)
    assert speech.identify("127.0.0.1", s.port) == ("mlx-audio", "GET / returns the mlx-audio welcome line")
    assert s.seen == [("GET", "/")]                          # one GET, nothing more


def test_identify_falls_back_to_openapi_with_all_three_routes(stub):
    s = stub({"message": "Welcome to some later mlx-audio wording"}, openapi={"paths": MLX_PATHS})
    assert speech.identify("127.0.0.1", s.port)[0] == "mlx-audio"
    assert s.seen == [("GET", "/"), ("GET", "/openapi.json")]


def test_identify_the_openai_pair_alone_is_foreign(stub):
    paths = {"/v1/audio/speech": {}, "/v1/audio/transcriptions": {}}   # any OpenAI-compatible server
    s = stub({"message": "hello"}, openapi={"paths": paths})
    assert speech.identify("127.0.0.1", s.port)[0] == "foreign"


@pytest.mark.parametrize("root,status", [({"detail": "Not Found"}, 404), (b"<html>hi</html>", 200),
                                         ({"message": "Welcome"}, 200), (b"", 200)])
def test_identify_anything_else_is_foreign(stub, root, status):
    s = stub(root, root_status=status)
    identity, why = speech.identify("127.0.0.1", s.port)
    assert identity == "foreign" and "not the mlx-audio welcome line" in why


def test_identify_a_wedged_server_is_no_answer_not_none():
    """It accepts the connection (the backlog does) and never answers: occupied, unknown."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    try:
        port = sock.getsockname()[1]
        assert speech.connects("127.0.0.1", port)
        t = time.monotonic()
        identity, why = speech.identify("127.0.0.1", port, timeout=0.3)
        assert identity == "no-answer" and "did not answer" in why
        assert time.monotonic() - t < 5
    finally:
        sock.close()


def test_identify_and_connect_on_a_closed_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()                                             # bound once, now nothing listens
    assert not speech.connects("127.0.0.1", port)
    assert speech.identify("127.0.0.1", port, timeout=0.3)[0] == "no-answer"


def test_identify_ignores_a_proxy(stub, monkeypatch):
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9/")   # a dead proxy: used, the GET would fail
    monkeypatch.setenv("no_proxy", "")
    s = stub(WELCOME)
    assert speech.identify("127.0.0.1", s.port)[0] == "mlx-audio"


def test_probe_and_decide_end_to_end_mlx_audio_stub_is_used(stub, tmp_path):
    s = stub(WELCOME)
    p = speech.probe(tmp_path, {}, port=s.port)
    assert p.held and p.connects and p.identity == "mlx-audio"
    if p.holder is not None:                                  # lsof is on this host: it names us
        assert p.holder.pid == os.getpid()
    d = decide(p, final_url=f"http://127.0.0.1:{s.port}/v1")
    assert d.kind == "use" and not d.installs and f"using mlx-audio on :{s.port}" in d.line


def test_probe_and_decide_end_to_end_foreign_stub_is_refused(stub, tmp_path):
    s = stub({"detail": "Not Found"}, root_status=404)
    d = decide(speech.probe(tmp_path, {}, port=s.port), final_url=f"http://127.0.0.1:{s.port}/v1")
    assert d.kind == "refuse-foreign" and d.fatal and not d.installs


# ---- final_url(): what the opt-out reads -----------------------------------------------------

def test_final_url_reads_the_env_then_ears_json(tmp_path, monkeypatch):
    conf = tmp_path / "ears.json"
    monkeypatch.setenv("EARS_CONF", str(conf))
    monkeypatch.delenv("EARS_STT_URL", raising=False)
    assert speech.final_url() is None                        # no file: the default
    conf.write_text(json.dumps({"stt": {"url": "http://127.0.0.1:2023/v1"}}))   # the old name
    assert speech.final_url() == "http://127.0.0.1:2023/v1"
    conf.write_text(json.dumps({"final": {"url": DEFAULT}}))
    assert speech.final_url() == DEFAULT
    monkeypatch.setenv("EARS_STT_URL", "http://box:1/v1")
    assert speech.final_url() == "http://box:1/v1"


# ---- `ears services status`: the speech row --------------------------------------------------

def _speech_row(capsys):
    rows = [l for l in capsys.readouterr().out.splitlines() if l.startswith("speech ")]
    assert len(rows) == 1, rows
    return rows[0][len("speech "):]


def test_status_speech_row_none(fence, capsys):
    services.status()
    assert _speech_row(capsys) == "none on :8890: no listener, no mlx-audio job loaded"
    assert ("connect", 8890) in fence["calls"]["speech"]     # the fence's fake, never the live port


def test_status_speech_row_names_the_holder(fence, monkeypatch, capsys):
    monkeypatch.setattr(speech, "port_holder", lambda port=None, listed=None: VM)
    monkeypatch.setattr(speech, "connects", lambda host=None, port=None, timeout=None: True)
    monkeypatch.setattr(speech, "identify", lambda host=None, port=None, timeout=None: ("mlx-audio", "welcome"))
    services.status()
    assert _speech_row(capsys) == "mlx-audio on :8890, pid 2000, launchd com.voicemode.mlx-audio"


def test_status_speech_row_says_ours(fence, monkeypatch, capsys):
    monkeypatch.setattr(speech, "port_holder", lambda port=None, listed=None: Holder(7, "py", speech.LABEL))
    monkeypatch.setattr(speech, "connects", lambda host=None, port=None, timeout=None: True)
    monkeypatch.setattr(speech, "identify", lambda host=None, port=None, timeout=None: ("mlx-audio", "welcome"))
    services.status()
    assert _speech_row(capsys) == "mlx-audio on :8890, pid 7, launchd dev.sessionkit.voice.mlx-audio (ours)"


def test_status_speech_row_foreign(fence, monkeypatch, capsys):
    monkeypatch.setattr(speech, "port_holder", lambda port=None, listed=None: FOREIGN)
    monkeypatch.setattr(speech, "connects", lambda host=None, port=None, timeout=None: True)
    monkeypatch.setattr(speech, "identify", lambda host=None, port=None, timeout=None: ("foreign", "404"))
    services.status()
    row = _speech_row(capsys)
    assert "NOT mlx-audio" in row and "pid 3000" in row


def test_status_speech_row_loaded_job_down(fence, monkeypatch, capsys):
    agents = services.AGENTS
    agents.mkdir(parents=True, exist_ok=True)
    _plist(agents, "com.voicemode.mlx-audio", ["/bin/sh", "-c", "mlx_audio.server --port 8890"])
    monkeypatch.setattr(services, "_launchctl", lambda *a: subprocess.CompletedProcess(
        a, 0, "PID\tStatus\tLabel\n-\t0\tcom.voicemode.mlx-audio\n", ""))
    services.status()
    assert _speech_row(capsys) == "none answering on :8890; com.voicemode.mlx-audio is loaded, not running"
