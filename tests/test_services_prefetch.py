"""EARS-419 do-002: `ears services install` prefetches the speech server's three weights.

When install installs the speech server (step 4) it fetches SPEECH_WEIGHTS with the server's own
call, snapshot_download(repo, revision main, the weight's patterns from SPEECH_WEIGHT_SIZES), on the
speech python, through the seam services._prefetch_sh. It prints the size first, from the reader;
skips and names the weights already cached; prints N of M from the reader while a fetch runs.
--no-prefetch and EARS_NO_PREFETCH=1 skip it and say what the first call will fetch; a dry run
prints the size and the plan and fetches nothing; a failed fetch never fails the install and
says why, how much, and that the first call fetches it; a final reader pass follows.

Everything runs inside test_services_speech's fence (a temp HOME, launchctl faked, the speech
probes faked), whose HF_HOME is in tmp: so this shell's HF cache is NOT the server's, which is
~/.cache/huggingface/hub under the temp HOME (its plist sets no HF_*). The seam is a fake that
writes the cache it is handed the way huggingface_hub does: a growing blobs/*.incomplete, then
the blob, refs/main and the snapshot. Nothing is downloaded."""
import os
import subprocess
from pathlib import Path

import pytest

from ears import services
from ears.speech import Probe
from test_services_install_checks import _store, fence  # noqa: F401  (a fixture)
from test_services_speech import VM, sx  # noqa: F401  (a fixture)

WEIGHTS = dict(services.SPEECH_WEIGHTS)
PARAKEET, KOKORO, VOICES = (r for r, _ in services.SPEECH_WEIGHTS)
SIZE = {r: size for (r, _), (_, _, size, _, _) in zip(services.SPEECH_WEIGHTS, services.SPEECH_WEIGHT_SIZES)}
PATTERNS = {r: p for (r, _), (_, _, _, _, p) in zip(services.SPEECH_WEIGHTS, services.SPEECH_WEIGHT_SIZES)}


def _folder(repo: str, cache: Path | None = None) -> Path:
    """REPO's folder in CACHE, by default the cache the speech server reads."""
    cache = services.speech_hf_cache() if cache is None else cache
    return cache / ("models--" + repo.replace("/", "--"))


def _cached(repo: str, cache: Path | None = None) -> None:
    """REPO as huggingface_hub leaves it after a whole fetch at its expected sha. Sparse."""
    f = _folder(repo, cache)
    for d in ("blobs", "refs", f"snapshots/{WEIGHTS[repo]}"):
        (f / d).mkdir(parents=True, exist_ok=True)
    (f / "refs" / "main").write_text(WEIGHTS[repo])
    with open(f / "blobs" / ("a" * 64), "wb") as b:
        b.truncate(SIZE[repo])
    for p in (f / "blobs").glob("*.incomplete"):
        p.unlink()


class FakeFetch:
    """One fetch process: each wait() that times out grows the repo's blobs/*.incomplete by a
    third of its size (written now, so the reader says downloading), TICKS times; then it exits
    RC, having cached the repo when RC is 0, or written ERR to its output when not."""

    def __init__(self, repo, out, cache, rc=0, err="", ticks=2, interrupt=False):
        self.repo, self.out, self.cache, self.rc, self.err, self.ticks = repo, out, cache, rc, err, ticks
        self.interrupt, self.killed, self.waits = interrupt, False, []

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self.interrupt and not self.killed:
            raise KeyboardInterrupt
        if self.killed:
            return -9
        if len(self.waits) <= self.ticks:
            blobs = _folder(self.repo, self.cache) / "blobs"
            blobs.mkdir(parents=True, exist_ok=True)
            with open(blobs / ("b" * 64 + ".0123abcd.incomplete"), "wb") as f:
                f.truncate(SIZE[self.repo] * len(self.waits) // 3)
            raise subprocess.TimeoutExpired("fetch", timeout)
        if self.rc == 0:
            _cached(self.repo, self.cache)
        else:
            self.out.write(self.err)
            self.out.flush()
        return self.rc

    def kill(self):
        self.killed = True


@pytest.fixture
def px(sx, monkeypatch):
    """The speech fence with the prefetch ON and its seam faked: ``px["argv"]`` lists each
    fetch started, ``px["caches"]`` the cache each was handed; ``px["rc"]`` / ``px["err"]`` / ``px["raise_"]`` (repo -> ...) make one fail
    (a failing fetch fails at once, as one that cannot reach the Hub does)."""
    monkeypatch.delenv(services.PREFETCH_ENV, raising=False)
    sx.update(argv=[], caches=[], procs=[], rc={}, err={}, raise_={}, interrupt=set())

    def seam(argv, out, cache):
        argv = [str(a) for a in argv]
        sx["argv"].append(argv)
        sx["caches"].append(cache)
        repo = argv[3]
        if repo in sx["raise_"]:
            raise sx["raise_"][repo]
        rc = sx["rc"].get(repo, 0)
        p = FakeFetch(repo, out, cache, rc=rc, err=sx["err"].get(repo, ""), ticks=2 if rc == 0 else 0,
                      interrupt=repo in sx["interrupt"])
        sx["procs"].append(p)
        return p
    monkeypatch.setattr(services, "_prefetch_sh", seam)
    return sx


def _states(cache: Path | None = None):
    cache = services.speech_hf_cache() if cache is None else cache
    return {w["repo"]: w["state"] for w in services.weights_reading(cache)["weights"]}


def _index(out: str, text: str) -> int:
    i = out.find(text)
    assert i >= 0, f"{text!r} not in:\n{out}"
    return i


# ---- a fresh host: size first, progress during, cached after -------------------------------

def test_a_fresh_install_fetches_all_three_with_the_servers_own_call(px, capsys):
    assert services.install(["speech"]) == 0
    py = os.environ["EARS_SPEECH_PY"]
    assert px["argv"] == [[py, "-c", services._PREFETCH_PY, r, *PATTERNS[r]] for r in (PARAKEET, KOKORO, VOICES)]
    assert "snapshot_download(sys.argv[1], revision='main', allow_patterns=sys.argv[2:])" in services._PREFETCH_PY
    assert PATTERNS[VOICES] == ("voices/*.safetensors",)          # the packs only, never *.pt
    assert PATTERNS[PARAKEET] == PATTERNS[KOKORO] == services.MLX_AUDIO_ALLOW_PATTERNS
    assert _states() == {PARAKEET: "cached", KOKORO: "cached", VOICES: "cached"}
    assert all(t == services.PREFETCH_POLL_S for p in px["procs"] for t in p.waits)


def test_the_size_is_printed_first_then_progress_then_the_final_reader_pass(px, capsys):
    assert services.install(["speech"]) == 0
    out = capsys.readouterr().out
    cache = services.speech_hf_cache()
    size = _index(out, f"speech weights: fetching about 2.9 GB into {cache}: {PARAKEET}, {KOKORO}, {VOICES}")
    loaded = _index(out, "speech loaded")
    first = _index(out, f"speech weights: fetching {PARAKEET}: ")
    assert loaded < size < first                    # after the plist: the server is up meanwhile
    # N of M, from the reader: a third, then two thirds, of Parakeet, of the 2.9 GB in the plan
    assert f"speech weights: fetching {PARAKEET}: 836 MB of 2.9 GB\n" in out
    assert f"speech weights: fetching {PARAKEET}: 1.7 GB of 2.9 GB\n" in out
    assert f"speech weights: fetching {KOKORO}: 2.6 GB of 2.9 GB\n" in out
    done = _index(out, f"speech weights: {VOICES} fetched (28 MB of 28 MB)")
    final = _index(out, "weight the speech server's weights in the HF cache")
    assert first < done < final
    assert f"weight {PARAKEET}: cached, 2.5 GB" in out[final:]


def test_on_a_terminal_the_progress_rewrites_one_line(px, capsys, monkeypatch):
    import sys
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    assert services.install(["speech"]) == 0
    out = capsys.readouterr().out
    assert f"\rspeech weights: fetching {PARAKEET}: 836 MB of 2.9 GB\x1b[K\rspeech weights: fetching " in out
    assert f"\x1b[K\nspeech weights: {PARAKEET} fetched" in out     # a newline closes the line


def test_cached_weights_are_skipped_and_named(px, capsys):
    _cached(PARAKEET)
    assert services.install(["speech"]) == 0
    out = capsys.readouterr().out
    assert [a[3] for a in px["argv"]] == [KOKORO, VOICES]
    assert f"speech weights: {PARAKEET} cached (2.5 GB), skipped" in out
    assert f"fetching about 384 MB into {services.speech_hf_cache()}: {KOKORO}, {VOICES} " in out


def test_all_cached_fetches_nothing(px, capsys):
    for r in WEIGHTS:
        _cached(r)
    assert services.install(["speech"]) == 0
    out = capsys.readouterr().out
    assert px["argv"] == []
    assert f"speech weights: all 3 cached in {services.speech_hf_cache()}; nothing to fetch" in out


def test_a_partial_voices_snapshot_is_fetched_for_the_rest(px, capsys):
    """The server fetched one voice pack at a first use: the rest are still missing."""
    f = _folder(VOICES)
    (f / "refs").mkdir(parents=True)
    (f / "refs/main").write_text(WEIGHTS[VOICES])
    (f / "snapshots" / WEIGHTS[VOICES] / "voices").mkdir(parents=True)
    (f / "blobs").mkdir()
    (f / "blobs" / ("c" * 64)).write_bytes(b"x" * 522_000)
    _cached(PARAKEET)
    _cached(KOKORO)
    assert services.install(["speech"]) == 0
    assert [a[3] for a in px["argv"]] == [VOICES]
    assert f"fetching about 28 MB into {services.speech_hf_cache()}: {VOICES} " in capsys.readouterr().out


# ---- the cache it fetches into is the server's, never this shell's ----------------------------

def test_the_fetch_lands_in_the_servers_cache_not_this_shells(px, capsys):
    """The fence's HF_HOME is in tmp, so this shell's cache differs from the server's."""
    server = px["home"] / ".cache/huggingface/hub"
    shell = services.hf_cache()
    assert shell != server and services.speech_hf_cache() == server
    assert services.install(["speech"]) == 0
    out = capsys.readouterr().out
    assert px["caches"] == [server] * 3                             # every fetch, the server's
    assert _states(server) == {PARAKEET: "cached", KOKORO: "cached", VOICES: "cached"}
    assert not shell.exists()                                       # nothing went to the shell's
    assert f"speech weights: fetching about 2.9 GB into {server}: " in out
    assert f"speech weights: the speech server's cache, not this shell's ({shell})\n" in out
    assert "WARNING" not in out
    final = _index(out, f"weight the speech server's weights in the HF cache {server}")
    assert f"weight {PARAKEET}: cached, 2.5 GB" in out[final:]      # the final pass reads it too


def test_progress_is_read_from_the_servers_cache(px, capsys):
    """With the shell's cache elsewhere, N of M still grows: it reads where the fetch writes."""
    assert services.install(["speech"]) == 0
    out = capsys.readouterr().out
    assert f"speech weights: fetching {PARAKEET}: 836 MB of 2.9 GB\n" in out


def test_weights_cached_in_the_shells_cache_only_are_still_fetched(px, capsys):
    """What this shell has is not what the server has: skip only what the server's cache holds."""
    for r in WEIGHTS:
        _cached(r, services.hf_cache())
    assert services.install(["speech"]) == 0
    assert [a[3] for a in px["argv"]] == [PARAKEET, KOKORO, VOICES]
    assert "cached (2.5 GB), skipped" not in capsys.readouterr().out


def test_an_hf_variable_the_plist_carries_wins(px, capsys, monkeypatch):
    """Should the speech plist ever set HF_*, the server reads that cache, so the fetch goes there."""
    spec = services.spec

    def with_hf(name, **kw):
        d = spec(name, **kw)
        if name == "speech":
            d["EnvironmentVariables"]["HF_HOME"] = str(px["tmp"] / "server-hf")
        return d
    monkeypatch.setattr(services, "spec", with_hf)
    want = px["tmp"] / "server-hf" / "hub"
    assert services.speech_hf_cache() == want
    assert services.install(["speech"]) == 0
    assert px["caches"] == [want] * 3 and _states(want)[VOICES] == "cached"


def test_the_shells_cache_is_not_mentioned_when_it_is_the_servers(px, capsys, monkeypatch):
    monkeypatch.delenv("HF_HOME")
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    assert services.hf_cache() == services.speech_hf_cache() == px["home"] / ".cache/huggingface/hub"
    assert services.install(["speech"]) == 0
    out = capsys.readouterr().out
    assert "not this shell's" not in out and _states()[PARAKEET] == "cached"


def test_the_servers_cache_ignores_this_shells_hf_variables(px, monkeypatch):
    for k in services.HF_CACHE_VARS:
        monkeypatch.setenv(k, str(px["tmp"] / k))
    assert services.speech_hf_cache() == px["home"] / ".cache/huggingface/hub"


# ---- the opt-out and the dry run -------------------------------------------------------------

def test_no_prefetch_skips_it_and_says_what_the_first_call_fetches(px, capsys):
    assert services.install(["speech"], no_prefetch=True) == 0
    out = capsys.readouterr().out
    assert px["argv"] == []
    assert (f"speech weights: prefetch skipped (--no-prefetch): the first call will fetch about "
            f"2.9 GB ({PARAKEET}, {KOKORO}, {VOICES}), and wait for it") in out
    assert "speech loaded" in out                   # the rest of the install is the same


@pytest.mark.parametrize("value", ["1", "true", "ON"])
def test_the_env_opts_out_too(px, capsys, monkeypatch, value):
    monkeypatch.setenv("EARS_NO_PREFETCH", value)
    assert services.install(["speech"]) == 0
    assert px["argv"] == []
    assert f"prefetch skipped (EARS_NO_PREFETCH={value}): the first call will fetch about 2.9 GB" in capsys.readouterr().out


@pytest.mark.parametrize("value", ["", "0", "false", "no"])
def test_any_other_env_value_prefetches(px, monkeypatch, value):
    monkeypatch.setenv("EARS_NO_PREFETCH", value)
    assert services.prefetch_off() is None
    assert services.prefetch_off(True) == "--no-prefetch"


def test_the_flag_reaches_install(monkeypatch):
    got = {}
    monkeypatch.setattr(services, "install", lambda names, dry=False, **kw: got.update(kw) or 0)
    services.main(["install", "--agent", "dave", "--no-prefetch"])   # bare and seatless: refused
    assert got["no_prefetch"] is True
    services.main(["install", "--agent", "dave"])
    assert got["no_prefetch"] is False


def test_the_install_help_names_the_prefetch_and_its_opt_out(capsys):
    with pytest.raises(SystemExit):
        services.main(["--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert "--no-prefetch" in out and "EARS_NO_PREFETCH=1" in out and "2.9 GB" in out


def test_a_dry_run_prints_the_size_and_the_plan_and_fetches_nothing(px, capsys):
    assert services.install(["speech"], dry_run=True) == 0
    out = capsys.readouterr().out
    assert px["argv"] == []
    assert (f"speech weights: would fetch about 2.9 GB into {services.speech_hf_cache()}: {PARAKEET}, {KOKORO}, "
            f"{VOICES} (snapshot_download, revision main, the server's own patterns; network). "
            "Nothing fetched") in out
    assert not services.speech_hf_cache().exists() and not services.hf_cache().exists()


# ---- a fetch that fails never fails the install ------------------------------------------------

def test_a_failed_fetch_says_why_how_much_and_that_the_first_call_fetches_it(px, capsys):
    px["rc"][PARAKEET] = 1
    px["err"][PARAKEET] = "Traceback ...\nLocalEntryNotFoundError: cannot reach the Hub\n"
    assert services.install(["speech"]) == 0
    out = capsys.readouterr().out
    assert (f"WARNING: speech prefetch of {PARAKEET} failed: exit 1: LocalEntryNotFoundError: cannot "
            f"reach the Hub. The install goes on; the first call will fetch about 2.5 GB instead, "
            "and wait for it") in out
    assert [a[3] for a in px["argv"]] == [PARAKEET, KOKORO, VOICES]     # the others still fetched
    assert _states() == {PARAKEET: "missing", KOKORO: "cached", VOICES: "cached"}
    final = _index(out, "weight the speech server's weights in the HF cache")
    assert f"weight {PARAKEET}: not cached" in out[final:]


def test_a_fetch_that_cannot_start_is_said_and_the_install_goes_on(px, capsys):
    px["raise_"][KOKORO] = FileNotFoundError(2, "No such file or directory")
    assert services.install(["speech"]) == 0
    out = capsys.readouterr().out
    assert (f"WARNING: speech prefetch of {KOKORO} failed: could not start {os.environ['EARS_SPEECH_PY']}: "
            "[Errno 2] No such file or directory. The install goes on; the first call will fetch about "
            "355 MB instead") in out
    assert _states()[VOICES] == "cached"


def test_ctrl_c_stops_the_fetch_and_the_install_goes_on(px, capsys):
    px["interrupt"].add(PARAKEET)
    assert services.install(["speech"]) == 0
    out = capsys.readouterr().out
    assert px["procs"][0].killed
    assert [a[3] for a in px["argv"]] == [PARAKEET]                   # nothing more after Ctrl-C
    assert f"failed: interrupted (Ctrl-C). The install goes on; the first call will fetch about 2.5 GB" in out
    assert "weight the speech server's weights in the HF cache" in out


# ---- only when install installs the speech server ------------------------------------------

def test_no_prefetch_when_the_server_on_the_port_is_someone_elses(px, capsys):
    _store(px["home"])
    px["state"]["probe"] = Probe(holder=VM, connects=True, identity="mlx-audio")
    assert services.install(["speech", "mouth", "keeper", "mail"]) == 0
    assert px["argv"] == []
    assert "speech weights:" not in capsys.readouterr().out


def test_no_prefetch_when_speech_is_not_installed(px, capsys):
    assert services.install(["mouth"]) == 0
    assert px["argv"] == [] and "speech weights:" not in capsys.readouterr().out


@pytest.mark.parametrize("argv", [["install"], ["install", "--only", "speech,mail"]])
def test_a_refused_install_prefetches_nothing(px, monkeypatch, capsys, argv):
    """EARS-421 refuses a bare install that would wake nobody, in main(), before install(): so the
    prefetch, which runs inside install(), never starts. Nothing fetched, nothing cached, no plist."""
    monkeypatch.delenv("TM_AGENT_ADDRESS", raising=False)
    _store(px["home"])
    assert services.main(argv) == 2
    cap = capsys.readouterr()
    assert "REFUSED: no --agent NAME" in cap.err
    assert px["argv"] == [] and "speech weights:" not in cap.out
    assert set(_states().values()) == {"missing"}
    assert not services.AGENTS.exists()


def test_a_refused_dry_run_lists_the_plan_and_fetches_nothing(px, monkeypatch, capsys):
    """The refused dry run goes on listing, as install would: the prefetch's plan included, and
    still nothing fetched."""
    monkeypatch.delenv("TM_AGENT_ADDRESS", raising=False)
    _store(px["home"])
    assert services.main(["install", "--only", "speech,mail", "--dry-run"]) == 2
    cap = capsys.readouterr()
    assert "REFUSED: no --agent NAME" in cap.err
    assert px["argv"] == [] and "Nothing fetched" in cap.out
    assert set(_states().values()) == {"missing"}


def test_the_same_install_with_an_agent_prefetches(px, monkeypatch, capsys):
    """The control: the refusal, not the fence, is what kept the fetch away."""
    monkeypatch.delenv("TM_AGENT_ADDRESS", raising=False)
    _store(px["home"])
    assert services.main(["install", "--only", "speech,mail", "--agent", "dave"]) == 0
    assert [a[3] for a in px["argv"]] == [PARAKEET, KOKORO, VOICES]


# ---- the seam -----------------------------------------------------------------------------

def test_the_seam_runs_online_without_progress_bars(monkeypatch, tmp_path):
    got = {}
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setattr(services.subprocess, "Popen", lambda argv, **kw: got.update(kw, argv=argv) or "proc")
    with open(tmp_path / "out", "w") as out:
        monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "shell-hub"))
        assert services._prefetch_sh([Path("/x/python"), "-c", "pass", PARAKEET], out,
                                     tmp_path / "server-hub") == "proc"
        assert got["stdout"] is out and got["stderr"] == subprocess.STDOUT
    assert got["argv"] == ["/x/python", "-c", "pass", PARAKEET]
    assert "HF_HUB_OFFLINE" not in got["env"] and got["env"]["HF_HUB_DISABLE_PROGRESS_BARS"] == "1"
    assert got["stdin"] == subprocess.DEVNULL
    assert got["env"]["HF_HUB_CACHE"] == str(tmp_path / "server-hub")   # the cache it is handed wins


@pytest.mark.parametrize("plist_home", [None, "server-hf"])
def test_the_seam_runs_with_the_servers_hf_variables_not_this_shells(monkeypatch, tmp_path, plist_home):
    """What huggingface_hub keeps under HF_HOME beside the hub cache (the xet cache) follows
    HF_HOME, so the fetch runs with the server's HF_* (none from launchd; the plist's, if any),
    not this shell's: nothing lands under the shell's HF_HOME."""
    got = {}
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for k in services.HF_CACHE_VARS:
        monkeypatch.setenv(k, str(tmp_path / ("shell-" + k)))
    monkeypatch.setenv("HF_TOKEN", "passes-through")
    if plist_home:
        spec = services.spec

        def with_hf(name, **kw):
            d = spec(name, **kw)
            if name == "speech":
                d["EnvironmentVariables"]["HF_HOME"] = str(tmp_path / plist_home)
            return d
        monkeypatch.setattr(services, "spec", with_hf)
    monkeypatch.setattr(services.subprocess, "Popen", lambda argv, **kw: got.update(kw) or "proc")
    with open(tmp_path / "out", "w") as out:
        services._prefetch_sh(["/x/python", "-c", "pass", PARAKEET], out, tmp_path / "server-hub")
    env = got["env"]
    assert env["HF_HUB_CACHE"] == str(tmp_path / "server-hub")
    assert {k: env.get(k) for k in ("HUGGINGFACE_HUB_CACHE", "HF_HOME", "XDG_CACHE_HOME")} == {
        "HUGGINGFACE_HUB_CACHE": None, "XDG_CACHE_HOME": None,
        "HF_HOME": str(tmp_path / plist_home) if plist_home else None}
    assert not any(str(tmp_path / "shell-") in v for v in env.values())
    assert env["HF_TOKEN"] == "passes-through" and env["HOME"] == str(tmp_path / "home")


def test_the_suite_never_prefetches_by_default():
    """tests/env_fence.py turns it off for every test: only this file's fixture turns it on."""
    assert os.environ.get("EARS_NO_PREFETCH") == "1" and services.prefetch_off() == "EARS_NO_PREFETCH=1"
