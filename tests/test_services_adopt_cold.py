"""`ears services install` ADOPTING a speech server it does not load (VoiceMode's mlx-audio
already on :8890), while that server has never loaded Parakeet.

The adopted server serves the mouth (Kokoro), so Parakeet is not in its cache: its first Parakeet
request downloads the weights (about 2.5 GB) and loads them before it answers. This install does
not load that server, so it neither prefetches its weights (the prefetch runs only for our own
server) nor skips the probe (``_no_probe`` names only a server this install loads). The new
``final`` is probed with ``probe_stt``'s 30 s timeout, which a cold first load cannot meet: the
probe says ``no answer: timed out``, install writes whisper :2022 with a FALLBACK line, and a later
install keeps it ("final kept"; a kept final is not re-probed), long after Parakeet has loaded.

The fix: a speech server on :8890 that this install does not load is probed cold-aware (a
heads-up line, then up to ``COLD_PROBE_S``, a "still waiting" line every ``COLD_TICK_S``, Ctrl-C
skips to whisper); every other probe keeps 30 s. A fallback that still happens is recorded outside
ears.json (state/stt-fallback.json under sessionvoice's home), repeated as the install's last line
and as a ``status`` row naming ``RETRY_STT``, and a later install probes the wanted engine again
and moves the ears back once it answers. The own-server path is unchanged.

Offline: the REAL ``probe_stt`` runs, but ``urllib.request.urlopen`` is a fake STT server on a
fake clock (``ColdServer``), so a timeout costs no real second and nothing reaches :8890, :2022 or
the network. Everything else is test_services_speech's fence (a temp HOME, launchctl faked, the
speech probes faked, the weights' fetch refused)."""
import json
import threading

import pytest

from ears import services
from ears.home import SETTINGS
from ears.speech import Probe
from test_services_install_checks import _store, fence  # noqa: F401  (a fixture)
from test_services_speech import VM, sx  # noqa: F401  (a fixture)

REAL_PROBE_STT = services.probe_stt          # the fence replaces it; these tests run the real one
SPEECH = "http://127.0.0.1:8890/v1"          # the adopted server
WHISPER = services.WHISPER_URL               # VoiceMode's whisper.cpp: the fallback
PARAKEET = services.DEFAULT_STT_MODEL
JOBS = ["speech", "mouth", "keeper", "mail"]

#: a cold first Parakeet load: 2.5 GB at about 25 MB/s, then the load. Longer than the probe's
#: 30 s, and short enough that a probe built to wait for a cold load meets it.
COLD_LOAD_S = 120.0


class _Answer:
    """What ``urlopen`` returns: a context manager with ``status`` and ``read()``."""

    def __init__(self, status, body):
        self.status, self._body = status, body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class ColdServer:
    """A fake ``urllib.request.urlopen``: the adopted mlx-audio on :8890 and whisper.cpp on :2022,
    on a fake clock (``now``, seconds). Parakeet on :8890 is cold: the first request for it starts
    its load, which ends ``cold_s`` later and goes on whether or not the client waits (a client
    that gives up does not stop the server's download). A request answers once the load has ended;
    one whose timeout runs out first moves the clock by the timeout and raises what a socket read
    timeout raises (``TimeoutError: timed out``). Whisper answers at once. ``asked`` lists each
    request as (url, model, timeout)."""

    def __init__(self, cold_s=COLD_LOAD_S):
        self.cold_s, self.now, self.ready_at, self.asked = cold_s, 0.0, None, []

    def __call__(self, req, timeout=None):
        url = req.full_url
        model = "whisper-1"
        if b'name="model"\r\n\r\n' + PARAKEET.encode() in req.data:
            model = PARAKEET
        self.asked.append((url, model, timeout))
        assert url.startswith((SPEECH, WHISPER)), f"a fenced test asked {url}"
        if url.startswith(WHISPER):
            return _Answer(200, b'{"text": ""}')
        if self.ready_at is None:                    # the first Parakeet request: the load starts
            self.ready_at = self.now + self.cold_s
        wait = self.ready_at - self.now
        if timeout is not None and wait > timeout:
            self.now += timeout
            raise TimeoutError("timed out")
        self.now = max(self.now, self.ready_at)
        return _Answer(200, b'{"text": ""}')


@pytest.fixture
def adopt(sx, monkeypatch):
    """The ADOPT path: VoiceMode's mlx-audio already holds :8890 (so this install does not load
    a speech server), no ears.json yet, and the real probe asking a cold ``ColdServer``."""
    _store(sx["home"])
    sx["state"]["probe"] = Probe(holder=VM, connects=True, identity="mlx-audio")
    server = ColdServer()
    monkeypatch.setattr(services, "probe_stt", REAL_PROBE_STT)
    monkeypatch.setattr("urllib.request.urlopen", server)
    sx["server"] = server
    sx["conf"] = sx["home"] / SETTINGS / "ears.json"
    sx["record"] = sx["home"] / ".sessionvoice" / "state" / "stt-fallback.json"
    return sx


def _final(fx):
    return json.loads(fx["conf"].read_text())["final"]


def test_the_fixture_is_the_adopt_path(adopt, capsys):
    """The control: the server on the port is someone else's, so this install loads no speech
    server, prefetches nothing (the fence's fetch seam would fail the test), and does probe."""
    assert services._no_probe(JOBS, speech_installs=False) is None
    assert services.install(JOBS) == 0
    out = capsys.readouterr().out
    assert "speech weights:" not in out                       # no prefetch
    assert f"probing {SPEECH} (read-only)" in out
    assert not (services.AGENTS / "dev.sessionkit.voice.mlx-audio.plist").exists()
    assert adopt["server"].asked[0][:2] == (SPEECH + "/audio/transcriptions", PARAKEET)


def test_a_cold_adopted_server_ends_the_install_on_parakeet(adopt, capsys):
    assert services.install(JOBS) == 0
    out = capsys.readouterr().out
    assert "FALLBACK" not in out, out
    assert _final(adopt) == {"url": SPEECH, "model": PARAKEET}
    assert adopt["server"].asked == [(SPEECH + "/audio/transcriptions", PARAKEET, services.COLD_PROBE_S)]
    assert not adopt["record"].exists()


def test_the_cold_probe_says_so_before_it_asks(adopt, capsys):
    """The heads-up (the size from SPEECH_WEIGHT_SIZES, the wait, Ctrl-C) comes before the
    probing line, which stays the last line before the request."""
    assert services.install(JOBS) == 0
    lines = capsys.readouterr().out.splitlines()
    i = lines.index(f"probing {SPEECH} (read-only)")
    size = services._size(next(n for u, _, n, _, _ in services.SPEECH_WEIGHT_SIZES if u == "stt"))
    assert lines[i - 1].startswith(f"speech {SPEECH} is a speech server this install does not load")
    assert f"Parakeet (about {size})" in lines[i - 1] and size == "2.5 GB"
    assert "15 min" in lines[i - 1] and "Ctrl-C skips to whisper :2022" in lines[i - 1]


def test_a_cold_adopted_server_that_never_answers_falls_back_loudly(adopt, capsys):
    """Still cold past COLD_PROBE_S: whisper :2022, the fallback recorded outside ears.json, the
    install's LAST line repeating it with the retry command, `status` saying it too. Exit 0:
    the install did work, on whisper. Whisper's probe keeps 30 s."""
    adopt["server"].cold_s = 6 * 3600.0
    assert services.install(JOBS, agent="dave") == 0
    out = capsys.readouterr().out
    assert "FALLBACK" in out and "(no answer: timed out)" in out
    assert _final(adopt) == {"url": WHISPER}
    assert json.loads(adopt["conf"].read_text()).keys() == {"final"}       # ears.json: no record in it
    rec = json.loads(adopt["record"].read_text())
    assert rec["final"] == {"url": SPEECH, "model": PARAKEET} and rec["why"] == "no answer: timed out"
    assert [t for _, _, t in adopt["server"].asked] == [services.COLD_PROBE_S, 30.0]
    last = out.rstrip("\n").splitlines()[-1]
    assert last.startswith("stt    FALLBACK: the ears hear through whisper :2022, NOT ")
    assert f"{PARAKEET} on {SPEECH}" in last and "(no answer: timed out)" in last
    assert "`ears services install --only keeper --notify dave`" in last   # the notices kept
    assert services.status() == 0
    rows = [l for l in capsys.readouterr().out.splitlines() if "FALLBACK" in l]
    assert len(rows) == 1 and rows[0].startswith("stt    FALLBACK since ")
    assert "`ears services install --only keeper --notify dave`" in rows[0]   # read from our keeper


def test_a_later_install_keeps_the_fallback_while_it_still_fails(adopt, capsys):
    adopt["server"].cold_s = 6 * 3600.0
    services.install(JOBS)
    capsys.readouterr()
    assert services.install(JOBS) == 0                        # still loading: probed again, fails again
    out = capsys.readouterr().out
    assert f"probing {SPEECH} (read-only)" in out and "still the FALLBACK" in out
    assert _final(adopt) == {"url": WHISPER} and adopt["record"].exists()
    assert out.rstrip("\n").splitlines()[-1].startswith("stt    FALLBACK")


def test_ctrl_c_skips_the_cold_probe_to_whisper(adopt, monkeypatch, capsys):
    """KeyboardInterrupt is not an Exception: taken around the cold probe alone, it is a reason,
    and the install goes on to whisper and the plists."""
    server = adopt["server"]

    def interrupted(req, timeout=None):
        if req.full_url.startswith(SPEECH):
            raise KeyboardInterrupt
        return server(req, timeout)
    monkeypatch.setattr("urllib.request.urlopen", interrupted)
    assert services.install(JOBS) == 0
    out = capsys.readouterr().out
    assert "FAILED its probe (skipped by Ctrl-C)" in out and _final(adopt) == {"url": WHISPER}
    assert (services.AGENTS / "dev.sessionkit.voice.ears-keeper.plist").exists()


def test_the_cold_probe_says_it_is_still_waiting(adopt, monkeypatch, capsys):
    monkeypatch.setattr(services, "COLD_TICK_S", 0.01)
    server = adopt["server"]

    def slow(req, timeout=None):
        if req.full_url.startswith(SPEECH):
            threading.Event().wait(0.3)   # real, but short (the fence makes time.sleep a no-op): ticks are 10 ms
        return server(req, timeout)
    monkeypatch.setattr("urllib.request.urlopen", slow)
    assert services.seed_stt().startswith("final set")
    assert f"probing still waiting for {SPEECH}: " in capsys.readouterr().out


def test_a_dry_run_records_nothing(adopt, capsys):
    adopt["server"].cold_s = 6 * 3600.0
    assert services.install(JOBS, dry_run=True) == 0
    out = capsys.readouterr().out
    assert "FALLBACK" in out and out.rstrip("\n").splitlines()[-1].startswith("stt    FALLBACK")
    assert not adopt["record"].exists() and not adopt["conf"].exists()


def test_a_final_set_by_hand_makes_the_record_stale(adopt, capsys):
    adopt["server"].cold_s = 6 * 3600.0
    services.install(JOBS)
    adopt["conf"].write_text(json.dumps({"final": {"url": "http://127.0.0.1:2023/v1"}}))
    capsys.readouterr()
    services.status()
    assert "FALLBACK" not in capsys.readouterr().out          # the ears are not on whisper now
    asked = len(adopt["server"].asked)
    assert services.seed_stt() == "final kept: {'url': 'http://127.0.0.1:2023/v1'}"
    assert len(adopt["server"].asked) == asked and not adopt["record"].exists()


def test_the_speech_server_this_install_loads_is_still_not_probed(adopt, monkeypatch, capsys):
    """The own-server path: no heads-up, no probe, Parakeet written; a fallback recorded earlier
    is moved back the same way (the server is still starting, so it is not probed either)."""
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: pytest.fail("probed"))
    adopt["conf"].parent.mkdir(parents=True, exist_ok=True)
    adopt["conf"].write_text(json.dumps({"final": {"url": WHISPER}}))
    services._fallback_write({"url": SPEECH, "model": PARAKEET}, "no answer: timed out")
    why = services._no_probe(JOBS, speech_installs=True)
    assert why == "it is the speech server this install loads"
    out = services.seed_stt(no_probe=why)
    assert out.endswith("(not probed: it is the speech server this install loads), back from the "
                        "whisper :2022 fallback")
    assert "is a speech server this install does not load" not in capsys.readouterr().out
    assert _final(adopt) == {"url": SPEECH, "model": PARAKEET} and not adopt["record"].exists()


def test_a_later_install_moves_the_ears_back_to_parakeet(adopt, capsys):
    adopt["server"].cold_s = 6 * 3600.0                       # a slow link: no probe waits this long
    assert services.install(JOBS) == 0
    first = capsys.readouterr().out
    assert "FALLBACK" in first and _final(adopt) == {"url": WHISPER}
    adopt["server"].now += 7 * 3600.0                         # later: Parakeet has loaded
    assert services.install(JOBS) == 0
    second = capsys.readouterr().out
    assert "final kept" not in second, second
    assert "back from the whisper :2022 fallback" in second and "FALLBACK" not in second
    assert _final(adopt) == {"url": SPEECH, "model": PARAKEET}
    assert not adopt["record"].exists()
    services.status()
    assert "FALLBACK" not in capsys.readouterr().out


def test_install_seeds_through_seed_stt_so_a_stand_in_holds(adopt, monkeypatch, capsys):
    """install reaches the probe only through services.seed_stt: a test that stands in for it
    (test_services_one_keeper, test_services_label_move) must not find the real one probing a
    live :8890 and writing the conf behind its back. A stand-in that reports no fallback earns
    no closing line."""
    seen = []
    monkeypatch.setattr(services, "seed_stt", lambda **kw: seen.append(kw) or "stt kept")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: pytest.fail("probed"))
    assert services.install(JOBS) == 0
    out = capsys.readouterr().out
    assert len(seen) == 1 and isinstance(seen[0]["fell_back"], list)
    assert "stt kept" in out and "FALLBACK" not in out
    assert not adopt["conf"].exists() and not adopt["record"].exists()
