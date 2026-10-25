"""EARS-449: `ears services install` names every missing prerequisite in one run.

On a fresh Mac (no wake seat, no Silero VAD model, no VoiceMode copy of it), a bare install used
to print only the agent refusal and exit 2; the missing Silero model showed only on the next run.
Now one run lists every refusal in one REFUSED block, each with its fix, exits 2, and writes
nothing. A dry run prints the same block, then goes on listing what it can.

Everything runs inside test_services_speech's fence (a temp HOME, launchctl faked, the speech
probes faked). The network is refused outright: any Silero fetch through the default opener
fails, as a fetch on an offline fresh Mac would, and the fetcher itself is a tripwire. A single
refusal's exact bytes are pinned by tests/test_services_install_golden.py."""
import hashlib
import importlib.resources
import io
import urllib.request

import pytest

from ears import services, vad, wake
from ears.speech import Probe
from test_services_install_checks import _store, fence  # noqa: F401  (a fixture)
from test_services_speech import FOREIGN, sx  # noqa: F401  (a fixture)

HEAD = "ears services install: REFUSED: "


@pytest.fixture
def fresh(sx, monkeypatch):
    """A fresh Mac: no wake seat, no Silero model at _silero_want(), no VoiceMode copy, and no
    network (a fetch through urllib's opener fails). ``fetched`` records any call to the Silero
    fetcher: a refused run must make none."""
    monkeypatch.delenv("TM_AGENT_ADDRESS", raising=False)
    services._silero_want().unlink()
    assert not services._silero_copy().exists()

    def offline(*a, **kw):
        raise OSError("network refused by the test")
    monkeypatch.setattr(urllib.request, "urlopen", offline)
    fetched = []
    monkeypatch.setattr(vad, "fetch_silero", lambda *a, **kw: fetched.append((a, kw)) or pytest.fail("fetched"))
    monkeypatch.setattr(services, "SILERO_OPENER", lambda *a, **kw: fetched.append(a) or pytest.fail("opened"))
    monkeypatch.setattr(services, "_pgrep", lambda pattern: [])
    sx["fetched"] = fetched
    return sx


def _plists() -> list[str]:
    return sorted(p.name for p in services.AGENTS.glob("*.plist")) if services.AGENTS.exists() else []


def _nothing_written(fx) -> None:
    assert _plists() == []
    assert not services.LOGS.exists()
    assert not services._silero_want().exists()
    assert not (fx["home"] / ".sessionkit/voice/settings/wake.json").exists()
    assert not [a for a in fx["calls"]["launchctl"] if a[0] not in ("list", "print")]
    assert fx["fetched"] == []


def _heads(err: str) -> list[str]:
    """Each refused item's first line, cut at its first colon after the head."""
    return [l[len(HEAD):].split(":")[0] for l in err.splitlines() if l.startswith(HEAD)]


def _a1() -> str:
    return f"no --agent NAME, and {wake.target()} names no seat"


def _not_fetched() -> str:
    """The block's line for the Silero model a refused run did not fetch."""
    return (f"ears services install: not fetched: Silero VAD {vad.SILERO_TAG} (2.3 MB) is missing at "
            f"{services._silero_want()} (nor VoiceMode's {services._silero_copy()}); install fetches "
            "it once the refusals above are fixed")


def _footer(n: int) -> str:
    return (f"ears services install: {n} refused above, from one run; nothing was written, fetched or "
            "built. Fix each, then run it again")


def _no_shipped_files(monkeypatch, tmp_path):
    """A checkout and a package with no keeper/, settings/, speakers/ or talk: every shipped
    file is missing."""
    monkeypatch.setattr(services, "ROOT", tmp_path / "root")
    monkeypatch.setattr(importlib.resources, "files", lambda pkg: tmp_path / "pkg")


# ---- success criterion 1: a fresh HOME, one run, one block, rc 2, nothing written ----------

def test_a_fresh_install_lists_the_agent_refusal_and_silero_in_one_run(fresh, capsys):
    assert services.main(["install"]) == 2
    cap = capsys.readouterr()
    lines = cap.err.splitlines()
    assert lines[0].startswith(HEAD + "no --agent NAME")
    assert lines[3] == "either way, `ears wake add NAME` adds a seat to wake later"
    # the block: the agent refusal, then Silero (not fetched: a refused run fetches nothing),
    # then the footer, contiguous and in that order
    assert lines[4] == _not_fetched()
    assert lines[5] == _footer(1)
    assert len(lines) == 6
    assert "nothing was written" not in lines[:5]          # said once, by the footer
    assert cap.out == ""
    _nothing_written(fresh)


def test_a_fresh_dry_run_prints_the_same_block_byte_for_byte(fresh, capsys):
    assert services.main(["install"]) == 2
    plain = capsys.readouterr().err
    assert services.main(["install", "--dry-run"]) == 2
    cap = capsys.readouterr()
    assert cap.err == plain
    assert "--- " in cap.out                                # it went on listing the plists
    _nothing_written(fresh)


def test_a_dry_run_with_a_keeper_file_missing_still_lists_silero(fresh, monkeypatch, tmp_path, capsys):
    """A missing shipped file (NoKeeper) used to return 2 at once, even in a dry run."""
    _no_shipped_files(monkeypatch, tmp_path)
    assert services.main(["install", "--no-agent", "--dry-run"]) == 2
    err = capsys.readouterr().err
    assert "REFUSED: no ears-keeper.sh" in err
    assert "silero" in err.lower(), f"the dry run stopped at the missing keeper file:\n{err}"


def test_every_shipped_file_missing_is_listed_with_the_agent_and_silero(fresh, monkeypatch, tmp_path, capsys):
    _no_shipped_files(monkeypatch, tmp_path)
    assert services.main(["install"]) == 2
    err = capsys.readouterr().err
    assert _heads(err) == [_a1(),
                           "no ears-keeper.sh", "no ears-sounds.schema.json", "no ears-mail.py"]
    assert err.splitlines()[-2:] == [_not_fetched(), _footer(4)]
    _nothing_written(fresh)


def test_a_dry_run_gets_past_the_missing_files_and_lists_the_rest(fresh, monkeypatch, tmp_path, capsys):
    """The keeper and mail refused (their files are gone): a dry run stops listing those two,
    so it never builds a plist for a file that is not there, and lists mouth and speech."""
    _no_shipped_files(monkeypatch, tmp_path)
    _store(fresh["home"])
    assert services.main(["install", "--agent", "dave", "--dry-run"]) == 2
    cap = capsys.readouterr()
    assert _heads(cap.err) == ["no ears-keeper.sh", "no ears-sounds.schema.json", "no ears-mail.py"]
    assert _not_fetched() in cap.err.splitlines()
    assert "dev.sessionkit.voice.ears-mouth.plist" in cap.out and "dev.sessionkit.voice.mlx-audio.plist" in cap.out
    assert "ears-keeper.plist" not in cap.out and "ears-mail.plist" not in cap.out
    _nothing_written(fresh)


def test_speakers_and_speech_refusals_join_the_block(fresh, monkeypatch, capsys):
    """An Intel Mac with something foreign on :8890: speakers and speech each refuse, in the
    same block as the agent and Silero; the speech item keeps its what-to-run lines."""
    monkeypatch.setattr(services, "_mac", lambda: ("x86_64", "13.6"))
    fresh["state"]["probe"] = Probe(holder=FOREIGN, connects=True, identity="foreign", why="GET / answered 404")
    assert services.main(["install", "--only", "speech,mouth,keeper,mail,speakers"]) == 2
    err = capsys.readouterr().err
    assert [h.split(" ")[0] for h in _heads(err)] == ["no", "speakers", "speech"]
    assert "speech step 2, refuse-foreign" in err
    speech_at = err.index("speech step 2, refuse-foreign")
    assert err.index(services.no_stt_help()) > speech_at    # its help follows it, as before
    assert err.splitlines()[-2:] == [_not_fetched(), _footer(3)]
    _nothing_written(fresh)


def test_a_speech_check_that_fails_where_the_ears_hear_joins_the_block(fresh, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("EARS_SPEECH_PY", str(tmp_path / "no-such-python"))
    assert services.main(["install"]) == 2
    err = capsys.readouterr().err
    assert f"{HEAD}speech: EARS_SPEECH_PY={tmp_path / 'no-such-python'}: no such python\n" in err
    assert err.splitlines()[-1] == _footer(2)
    _nothing_written(fresh)


def test_a_speech_check_that_only_the_mouth_needs_stays_a_late_warning(fresh, monkeypatch, tmp_path, capsys):
    """The ears hear elsewhere: speech failing is a WARNING later (the mouth falls back to
    `say`), not an item, so the agent refusal is the whole block and is what it always was."""
    monkeypatch.setenv("EARS_STT_URL", "https://stt.example/v1")
    monkeypatch.setenv("EARS_SPEECH_PY", str(tmp_path / "no-such-python"))
    (fresh["home"] / ".cache/ears/silero_vad.onnx").write_bytes(b"onnx")
    assert services.main(["install"]) == 2
    err = capsys.readouterr().err
    assert _heads(err) == [_a1()]
    assert err.splitlines()[-1] == "nothing was written" and "refused above" not in err


@pytest.mark.parametrize("only,why", [("speakers", "speakers needs an Apple-silicon Mac"),
                                      ("speech", "speech step 2, refuse-foreign")])
def test_a_dry_run_says_a_service_refusal_once_and_stops_listing_that_service(fresh, monkeypatch, capsys, only, why):
    monkeypatch.setattr(services, "_mac", lambda: ("x86_64", "13.6"))
    fresh["state"]["probe"] = Probe(holder=FOREIGN, connects=True, identity="foreign", why="GET / answered 404")
    assert services.main(["install", "--only", only, "--dry-run"]) == 2
    cap = capsys.readouterr()
    assert cap.err.count(HEAD) == 1 and why in cap.err
    assert "--- " not in cap.out                            # its plist is not listed
    _nothing_written(fresh)


def test_a_hand_made_speakers_is_not_checked_by_the_preflight(fresh, monkeypatch, capsys):
    """Install skips a hand-made speakers' checks; so does the preflight."""
    monkeypatch.setattr(services, "_hand", lambda uid, name: ("com.example.speakers", fresh["tmp"] / "x.plist")
                        if name == "speakers" else None)
    monkeypatch.setattr(services, "speakers_refusal", lambda: pytest.fail("checked a hand-made speakers"))
    assert services.main(["install", "--only", "speakers", "--dry-run"]) == 0


# ---- what did not change -------------------------------------------------------------------

def test_a_direct_install_never_asks_the_agent_question(fresh, capsys):
    """install() called directly with no agent_why checks no wake seat, as before; only main()
    asks it."""
    (fresh["home"] / ".cache/ears/silero_vad.onnx").write_bytes(b"onnx")
    _store(fresh["home"])
    assert services.install(["mail"]) == 0
    assert "REFUSED" not in capsys.readouterr().err


def test_the_speech_port_is_probed_once_per_install(fresh, capsys):
    (fresh["home"] / ".cache/ears/silero_vad.onnx").write_bytes(b"onnx")
    _store(fresh["home"])
    assert services.main(["install", "--agent", "dave"]) == 0
    assert len([c for c in fresh["calls"]["speech"] if c[0] == "probe"]) == 1


def test_the_preflight_is_silent_and_writes_nothing(fresh, monkeypatch, tmp_path, capsys):
    _no_shipped_files(monkeypatch, tmp_path)
    pre = services.preflight(services.jobs(services.ALL), agent_why="agent")
    cap = capsys.readouterr()
    assert cap.out == "" and cap.err == ""
    assert pre.refused and pre.agent and pre.items[0] == "agent"
    assert {"keeper", "mail", "speakers", "ears-sounds", "mouth-sounds"} <= pre.drop
    _nothing_written(fresh)


# ---- success criteria 2 and 3: install fetches a missing Silero model, by the pin ----------

REAL_FETCH = vad.fetch_silero      # bound at import, before any fixture's tripwire
FAKE = b"fake onnx"


class _Opener:
    """A fake for fetch_silero's opener: serves BODY (or raises RAISE), records each call and
    what install had written by then. Never the network."""

    def __init__(self, body=FAKE, raise_=None):
        self.body, self.raise_, self.calls = body, raise_, []

    def __call__(self, url, timeout=None):
        self.calls.append({"url": url, "logs": services.LOGS.exists(), "plists": _plists()})
        if self.raise_:
            raise self.raise_
        return io.BytesIO(self.body)


@pytest.fixture
def fetchable(fresh, monkeypatch):
    """A fresh Mac with a wake seat: Silero is the one thing missing. The real fetch_silero runs,
    through a fake opener (``opener``), and the pin is the fake's sha."""
    _store(fresh["home"])
    monkeypatch.setattr(vad, "fetch_silero", REAL_FETCH)
    monkeypatch.setattr(vad, "SILERO_SHA256", hashlib.sha256(FAKE).hexdigest())
    fresh["opener"] = _Opener()
    monkeypatch.setattr(services, "SILERO_OPENER", fresh["opener"])
    return fresh


def _fetching(want) -> str:
    return f"silero fetching Silero VAD {vad.SILERO_TAG} (2.3 MB) from {vad.SILERO_URL} -> {want}"


def _parts(d) -> list[str]:
    return sorted(p.name for p in d.glob(".silero_vad.*.part")) if d.exists() else []


def test_install_fetches_a_missing_silero_by_the_pin_and_goes_on(fetchable, capsys):
    want = services._silero_want()
    assert services.main(["install", "--agent", "dave"]) == 0
    cap = capsys.readouterr()
    assert want.read_bytes() == FAKE
    out = cap.out.splitlines()
    sha = vad.SILERO_SHA256
    at = out.index(_fetching(want))
    assert out[at + 1] == f"silero fetched: {want} matches the pin ({vad.SILERO_TAG}, sha256 {sha[:12]})"
    assert [c["url"] for c in fetchable["opener"].calls] == [vad.SILERO_URL]
    # before anything else is written: no log dir, no plist
    assert fetchable["opener"].calls[0]["logs"] is False and fetchable["opener"].calls[0]["plists"] == []
    assert "dev.sessionkit.voice.ears-keeper.plist" in _plists()
    assert "REFUSED" not in cap.err and _parts(want.parent) == []


def test_the_fetch_lands_where_the_keeper_reads_it(fetchable, monkeypatch, capsys):
    want = fetchable["tmp"] / "models/elsewhere.onnx"
    monkeypatch.setenv("EARS_SILERO_ONNX", str(want))
    assert services.main(["install", "--agent", "dave", "--only", "keeper"]) == 0
    assert want.read_bytes() == FAKE
    assert _fetching(want) in capsys.readouterr().out
    assert not (fetchable["home"] / ".cache/ears/silero_vad.onnx").exists()
    assert services.spec("keeper")["EnvironmentVariables"]["EARS_SILERO_ONNX"] == str(want)


def test_a_wrong_sha_is_one_refusal_and_leaves_nothing(fetchable, capsys):
    want = services._silero_want()
    fetchable["opener"].body = b"not the model"
    assert services.main(["install", "--agent", "dave"]) == 2
    cap = capsys.readouterr()
    sha, got = vad.SILERO_SHA256, hashlib.sha256(b"not the model").hexdigest()
    assert cap.err == (f"{HEAD}silero: checksum mismatch for {vad.SILERO_URL}: expected sha256 {sha[:12]}, "
                       f"got {got[:12]}; nothing written at {want}; the keeper's capture cannot start "
                       "without it\n")
    assert _fetching(want) in cap.out
    assert not want.exists() and _parts(want.parent) == []
    assert _plists() == [] and not services.LOGS.exists()


def test_a_fetch_that_cannot_reach_its_source_is_one_refusal_never_a_traceback(fetchable, capsys):
    want = services._silero_want()
    fetchable["opener"].raise_ = OSError("no route to host")
    assert services.main(["install", "--agent", "dave"]) == 2
    err = capsys.readouterr().err
    assert err == (f"{HEAD}silero: could not fetch {vad.SILERO_URL}: no route to host; nothing written "
                   f"at {want}; the keeper's capture cannot start without it\n")
    assert not want.exists() and _parts(want.parent) == []
    assert _plists() == [] and not services.LOGS.exists()


def test_voicemodes_copy_is_linked_and_nothing_is_fetched(fetchable, capsys):
    copy = services._silero_copy()
    copy.parent.mkdir(parents=True)
    copy.write_bytes(b"onnx")
    assert services.main(["install", "--agent", "dave"]) == 0
    assert services._silero_want().is_symlink()
    assert fetchable["opener"].calls == []
    assert "silero linked" in capsys.readouterr().out


def test_a_dry_run_says_what_it_would_fetch_and_fetches_nothing(fetchable, capsys):
    want = services._silero_want()
    assert services.main(["install", "--agent", "dave", "--dry-run"]) == 0
    cap = capsys.readouterr()
    assert (f"silero would fetch Silero VAD {vad.SILERO_TAG} (2.3 MB) from {vad.SILERO_URL} -> {want}"
            in cap.out.splitlines())
    assert fetchable["opener"].calls == [] and cap.err == ""
    assert not want.exists() and _parts(want.parent) == []


# ---- success criterion 5: no test reaches the network --------------------------------------

def test_the_fence_gives_install_an_opener_that_refuses(fence):
    with pytest.raises(OSError, match="network refused by the fence"):
        services.SILERO_OPENER(vad.SILERO_URL, timeout=1)


def test_silero_passes_the_pin_and_the_opener_to_fetch_silero(fence, monkeypatch):
    """The pin is read when install fetches (never fetch_silero's def-time defaults), and the
    fetch goes through SILERO_OPENER."""
    services._silero_want().unlink()
    seen, opener = [], object()
    monkeypatch.setattr(services, "SILERO_OPENER", opener)
    monkeypatch.setattr(vad, "SILERO_URL", "https://example.invalid/silero.onnx")
    monkeypatch.setattr(vad, "fetch_silero", lambda dst, **kw: seen.append((dst, kw)))
    ok, line = services.silero()
    assert ok and line.startswith("silero fetched: ")
    assert seen == [(services._silero_want(), {"url": "https://example.invalid/silero.onnx",
                                               "sha256": vad.SILERO_SHA256, "opener": opener})]


def test_the_dry_run_help_names_the_silero_fetch(capsys):
    """--help says a dry run also sizes a missing Silero model, and fetches nothing (EARS-449)."""
    with pytest.raises(SystemExit):
        services.main(["--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert ("--dry-run print the checks and the plists, change nothing (install: also the size of "
            "the speech weights and of a missing Silero model it would fetch, fetching nothing;") in out
