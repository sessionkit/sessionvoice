"""The speech server's weights, read-only: EARS-339 fix-002's drift gauge, and EARS-419's reader.

mlx-audio's server fetches Parakeet, Kokoro and Kokoro's voice packs from Hugging Face `main`
at first use, with no revision. sessionvoice cannot pin them there (EARS-357), so it names the
expected commits in ONE place (services.SPEECH_WEIGHTS), the size of each beside it
(services.SPEECH_WEIGHT_SIZES), and `ears services weights [--json]` (schema ears.weights/1,
docs/models.md) reads each one's state from the HF cache the speech SERVER reads
(services.speech_hf_cache, never this shell's HF_*): downloading, cached or missing, with bytes
done and bytes total, and a drift flag. `ears services status` shows the same rows.

Everything here is offline: a fake HF cache in tmp_path, huggingface_hub made unimportable and
every socket refused, so a gauge that reached for the hub or the network would fail, not fetch."""
import builtins
import json
import os
import re
import socket
import sys
from pathlib import Path

import pytest

from ears import services
from ears.listen import stt
from mouth import backends
from test_services_install_checks import _store, fence  # noqa: F401  (a fixture)
from test_services_speech import sx  # noqa: F401  (a fixture)

REPO = Path(__file__).resolve().parents[1]
WEIGHTS = dict(services.SPEECH_WEIGHTS)
PARAKEET, KOKORO, VOICES = (r for r, _ in services.SPEECH_WEIGHTS)
HF_VARS = ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "HF_HOME", "XDG_CACHE_HOME")
OTHER = "0123456789abcdef0123456789abcdef01234567"
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", "build", "dist"}


@pytest.fixture
def offline(monkeypatch):
    """No hub, no network: importing huggingface_hub raises, and so does any socket connect.
    HF_HUB_OFFLINE is not set on purpose: the gauge must not need it, because it never asks."""
    real_import = builtins.__import__

    def no_hub(name, *a, **kw):
        if name == "huggingface_hub" or name.startswith("huggingface_hub."):
            raise AssertionError(f"the weights gauge imported {name}")
        return real_import(name, *a, **kw)

    def no_net(*a, **kw):
        raise AssertionError("the weights gauge opened a connection")

    monkeypatch.setattr(builtins, "__import__", no_hub)
    for mod in [m for m in sys.modules if m == "huggingface_hub" or m.startswith("huggingface_hub.")]:
        monkeypatch.delitem(sys.modules, mod)
    monkeypatch.setattr(socket.socket, "connect", no_net)
    monkeypatch.setattr(socket, "create_connection", no_net)
    for v in HF_VARS + ("HF_HUB_OFFLINE",):
        monkeypatch.delenv(v, raising=False)


@pytest.fixture
def server(monkeypatch, tmp_path) -> Path:
    """The cache the speech server reads, which is what the verb and status read by default:
    ~/.cache/huggingface/hub under a temp HOME (its plist sets no HF_*). Not made: a test makes
    what it needs. This shell's HF_* (unset by `offline`) are a test's to set elsewhere."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    cache = home / ".cache/huggingface/hub"
    assert services.speech_hf_cache() == cache
    return cache


def _cache(root: Path, repo: str, main: str | None, snapshot: str | None = None) -> Path:
    """A fake HF hub cache entry for REPO: refs/main holding MAIN (None: no refs/main), and a
    snapshots/<SNAPSHOT> directory (default: MAIN's). The layout huggingface_hub writes."""
    folder = root / ("models--" + repo.replace("/", "--"))
    (folder / "blobs").mkdir(parents=True, exist_ok=True)
    if main is not None:
        (folder / "refs").mkdir(exist_ok=True)
        (folder / "refs" / "main").write_text(main)
    snap = snapshot if snapshot is not None else main
    if snap:
        (folder / "snapshots" / snap).mkdir(parents=True, exist_ok=True)
    return root


# ---- the one place ----------------------------------------------------------------------

def test_the_three_repos_and_their_shas():
    assert services.SPEECH_WEIGHTS == (
        ("mlx-community/parakeet-tdt-0.6b-v3", "ed2b7e8c15f9aaa0b5772e2efb986255eaef7e15"),
        ("mlx-community/Kokoro-82M-bf16", "a71e4d38b236d968966a2002c4c895dbd12b1c3c"),
        ("prince-canuma/Kokoro-82M", "e02c9eada7ce7416798af36b190a8a2dd2ecd566"),
    )
    for _, sha in services.SPEECH_WEIGHTS:
        assert re.fullmatch(r"[0-9a-f]{40}", sha), "a full commit, never a branch"


def test_the_gauged_repos_are_the_ones_ears_and_mouth_ask_for():
    """The gauge reads the repos the clients name: if a default model moves, so must this."""
    assert PARAKEET == stt.FALLBACK_MODEL == services.DEFAULT_STT_MODEL
    assert KOKORO == backends.KOKORO_MODEL


def test_nothing_else_hard_codes_the_shas():
    """services.py holds each sha once. No other shipped file (code, scripts, settings, docs,
    README) carries a copy that could drift from it. This test names them to look for them."""
    shas = [sha for _, sha in services.SPEECH_WEIGHTS]
    here = Path(__file__).resolve()
    found = []
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.endswith(".egg-info")]
        for name in files:
            path = Path(root) / name
            if path == here:
                continue
            try:
                text = path.read_text(errors="ignore")
            except OSError:
                continue
            for sha in shas:
                n = text.count(sha)
                if n:
                    found.append((str(path.relative_to(REPO)), sha[:12], n))
    assert sorted(found) == sorted(("src/ears/services.py", sha[:12], 1) for sha in shas)


# ---- the cache it reads -------------------------------------------------------------------

def test_hf_cache_default_is_home_cache(offline, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert services.hf_cache() == tmp_path / ".cache/huggingface/hub"


@pytest.mark.parametrize("var, value, want", [
    ("HF_HUB_CACHE", "{t}/hub-cache", "{t}/hub-cache"),
    ("HUGGINGFACE_HUB_CACHE", "{t}/legacy", "{t}/legacy"),
    ("HF_HOME", "{t}/hfhome", "{t}/hfhome/hub"),
    ("XDG_CACHE_HOME", "{t}/xdg", "{t}/xdg/huggingface/hub"),
])
def test_hf_cache_follows_huggingface_hubs_env(offline, monkeypatch, tmp_path, var, value, want):
    monkeypatch.setenv(var, value.format(t=tmp_path))
    assert services.hf_cache() == Path(want.format(t=tmp_path))


def test_hf_hub_cache_wins_over_hf_home(offline, monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    assert services.hf_cache() == tmp_path / "hub"


def test_an_empty_variable_counts_as_unset(offline, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HF_HUB_CACHE", "")
    monkeypatch.setenv("HF_HOME", "")
    assert services.hf_cache() == tmp_path / ".cache/huggingface/hub"


def test_hf_home_with_a_tilde_expands(offline, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HF_HOME", "~/elsewhere")
    assert services.hf_cache() == tmp_path / "elsewhere/hub"


# ---- the sizes, beside the shas ------------------------------------------------------------

# mlx-audio 0.5.8's DEFAULT_ALLOW_PATTERNS (mlx_audio/utils.py), what its server fetches with.
MLX_AUDIO_058 = ("*.json", "*.safetensors", "*.py", "*.model", "*.tiktoken", "*.txt", "*.jinja",
                 "*.jsonl", "*.yaml", "*.npz", "*.pth")


def test_every_weight_has_a_size_measured_at_its_sha():
    """A size is only true at the commit it was measured at. Each row names the first 12 hex of
    that commit, so a sha bumped in SPEECH_WEIGHTS without re-measuring its size fails here."""
    assert len(services.SPEECH_WEIGHT_SIZES) == len(services.SPEECH_WEIGHTS)
    for (repo, sha), (use, at, size, files, patterns) in zip(services.SPEECH_WEIGHTS,
                                                              services.SPEECH_WEIGHT_SIZES):
        assert at == sha[:12], f"{repo}: its size was measured at {at}, but SPEECH_WEIGHTS says {sha}"
        assert isinstance(size, int) and size > 0 and isinstance(files, int) and files > 0
        assert patterns
    assert [u for u, *_ in services.SPEECH_WEIGHT_SIZES] == ["stt", "tts", "voices"]


def test_the_pinned_sizes():
    """Measured from the HF metadata API (/api/models/<repo>/tree/<sha>?recursive=true): the sum of the files the patterns select (fnmatchcase). Never by fetching. voices is the 54 voices/*.safetensors packs only: the voices/<voice>.pt beside each is never fetched."""
    sizes = {u: (size, files) for u, _, size, files, _ in services.SPEECH_WEIGHT_SIZES}
    assert sizes == {"stt": (2_508_940_517, 4), "tts": (355_322_783, 56), "voices": (28_206_306, 54)}
    assert services.weights_reading(Path("/nonexistent-cache"))["bytes_total"] == 2_892_469_606


def test_the_patterns_are_the_servers_own():
    """stt and tts: what mlx-audio 0.5.8's server fetches with. voices: the server takes one
    voices/<voice>.safetensors per voice (kokoro/pipeline.py), never DEFAULT_ALLOW_PATTERNS,
    which would also select a second kokoro-v1_0.safetensors the server never loads from there."""
    assert services.MLX_AUDIO_ALLOW_PATTERNS == MLX_AUDIO_058
    pats = {u: p for u, _, _, _, p in services.SPEECH_WEIGHT_SIZES}
    assert pats == {"stt": MLX_AUDIO_058, "tts": MLX_AUDIO_058, "voices": ("voices/*.safetensors",)}


# ---- a fake HF cache, laid out the way huggingface_hub 2.2.0 writes it ----------------------

NOW = 2_000_000_000.0


def _folder(cache: Path, repo: str) -> Path:
    return cache / ("models--" + repo.replace("/", "--"))


def _blob(cache: Path, repo: str, name: str, size: int, age: float = 0.0, link: str | None = None) -> Path:
    """A file of SIZE bytes in the repo's blobs/, AGE seconds before NOW. Sparse, so a 2.5 GB
    blob costs nothing. LINK: the name it gets under snapshots/<ref>/."""
    blobs = _folder(cache, repo) / "blobs"
    blobs.mkdir(parents=True, exist_ok=True)
    path = blobs / name
    with open(path, "wb") as f:
        f.truncate(size)
    os.utime(path, (NOW - age, NOW - age))
    if link:
        ref = (_folder(cache, repo) / "refs" / "main").read_text().strip()
        dst = _folder(cache, repo) / "snapshots" / ref / link
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.symlink_to(os.path.relpath(path, dst.parent))
    return path


def _full(cache: Path, repo: str) -> Path:
    """REPO cached at its expected sha with its whole pinned size on disk."""
    use = dict(zip([r for r, _ in services.SPEECH_WEIGHTS], services.SPEECH_WEIGHT_SIZES))[repo]
    _cache(cache, repo, WEIGHTS[repo])
    _blob(cache, repo, "a" * 64, use[2], age=3600)
    return cache


def _one(cache, repo, now=NOW):
    return next(w for w in services.weights_reading(cache, now)["weights"] if w["repo"] == repo)


# ---- the JSON: schema ears.weights/1, exactly as docs/models.md ------------------------------

TOP = ["schema", "cache", "state", "bytes_done", "bytes_total", "weights"]
EACH = ["repo", "use", "state", "bytes_done", "bytes_total", "expected", "cached_ref", "drift"]


def _types(r):
    assert list(r) == TOP
    assert r["schema"] == "ears.weights/1" and isinstance(r["cache"], str)
    assert r["state"] in ("cached", "downloading", "missing")
    assert type(r["bytes_done"]) is int and (r["bytes_total"] is None or type(r["bytes_total"]) is int)
    assert [w["use"] for w in r["weights"]] == ["stt", "tts", "voices"]
    assert [w["repo"] for w in r["weights"]] == [PARAKEET, KOKORO, VOICES]
    for w in r["weights"]:
        assert list(w) == EACH
        assert w["state"] in ("cached", "downloading", "missing")
        assert type(w["bytes_done"]) is int
        assert w["bytes_total"] is None or type(w["bytes_total"]) is int
        assert re.fullmatch(r"[0-9a-f]{40}", w["expected"]) and w["expected"] == WEIGHTS[w["repo"]]
        assert w["cached_ref"] is None or isinstance(w["cached_ref"], str)
        assert type(w["drift"]) is bool


def test_the_verb_prints_one_line_of_json_and_exits_0(offline, server, capsys):
    assert services.main(["weights", "--json"]) == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1 and out.endswith("\n")
    r = json.loads(out)
    _types(r)
    assert r["cache"] == str(server) and os.path.isabs(r["cache"])
    assert r["state"] == "missing" and r["bytes_done"] == 0 and r["bytes_total"] == 2_892_469_606
    assert not server.exists(), "the reader creates nothing"


def test_the_verb_through_ears(offline, server, capsys):
    """`ears services weights --json`, as phone runs it."""
    from ears import cli
    assert cli.main(["services", "weights", "--json"]) == 0
    r = json.loads(capsys.readouterr().out)
    _types(r)
    assert r["cache"] == str(server)


def test_the_verb_without_json_prints_the_status_rows(offline, server, capsys):
    _full(server, PARAKEET)
    assert services.main(["weights"]) == 0
    assert capsys.readouterr().out.splitlines() == services.weights_status(server)


# ---- the cache it reads is the server's, never this shell's (EARS-419 do-003) ---------------

SHELL_VARS = [("HF_HOME", "{t}/shell-hf", "{t}/shell-hf/hub"),
              ("HF_HUB_CACHE", "{t}/shell-hub", "{t}/shell-hub"),
              ("HUGGINGFACE_HUB_CACHE", "{t}/shell-hub", "{t}/shell-hub"),
              ("XDG_CACHE_HOME", "{t}/shell-xdg", "{t}/shell-xdg/huggingface/hub")]


@pytest.mark.parametrize("var,value,shell", SHELL_VARS)
def test_a_shell_cache_with_every_weight_does_not_make_the_server_cached(
        offline, server, monkeypatch, tmp_path, capsys, var, value, shell):
    """Install fills the server's cache; a shell whose HF_* point elsewhere must not see the
    weights it holds there as the server's: the server's first call would still fetch them."""
    monkeypatch.setenv(var, value.format(t=tmp_path))
    shell = Path(shell.format(t=tmp_path))
    assert services.hf_cache() == shell != server
    for r in WEIGHTS:
        _full(shell, r)
    assert services.main(["weights", "--json"]) == 0
    r = json.loads(capsys.readouterr().out)
    _types(r)
    assert r["cache"] == str(server)
    assert r["state"] == "missing" and [w["state"] for w in r["weights"]] == ["missing"] * 3
    assert r["bytes_done"] == 0


@pytest.mark.parametrize("var,value,shell", SHELL_VARS)
def test_the_servers_cached_weights_read_cached_from_any_shell(
        offline, server, monkeypatch, tmp_path, capsys, var, value, shell):
    """The other way round: what install fetched into the server's cache reads cached, in a shell
    whose own cache is elsewhere and empty."""
    monkeypatch.setenv(var, value.format(t=tmp_path))
    for r in WEIGHTS:
        _full(server, r)
    assert services.main(["weights", "--json"]) == 0
    r = json.loads(capsys.readouterr().out)
    assert r["cache"] == str(server) and r["state"] == "cached"
    assert r["bytes_done"] == r["bytes_total"] == 2_892_469_606
    assert not Path(shell.format(t=tmp_path)).exists()
    assert services.main(["weights"]) == 0
    rows = capsys.readouterr().out.splitlines()
    assert str(server) in rows[0] and all(": cached, " in row for row in rows[1:])


def test_the_reader_reads_an_hf_variable_the_plist_carries(offline, server, monkeypatch, tmp_path, capsys):
    """Should the speech plist ever set HF_*, the server reads that cache, and so does the reader."""
    spec = services.spec

    def with_hf(name, **kw):
        d = spec(name, **kw)
        if name == "speech":
            d["EnvironmentVariables"]["HF_HUB_CACHE"] = str(tmp_path / "plist-hub")
        return d
    monkeypatch.setattr(services, "spec", with_hf)
    monkeypatch.setenv("HF_HOME", str(tmp_path / "shell-hf"))
    _full(tmp_path / "plist-hub", PARAKEET)
    assert services.main(["weights", "--json"]) == 0
    r = json.loads(capsys.readouterr().out)
    assert r["cache"] == str(tmp_path / "plist-hub")
    assert [w["state"] for w in r["weights"]] == ["cached", "missing", "missing"]



def test_every_state_keeps_the_keys_and_types(offline, tmp_path):
    _full(tmp_path, PARAKEET)                                         # cached
    _cache(tmp_path, KOKORO, OTHER)                                   # drift
    _cache(tmp_path, VOICES, WEIGHTS[VOICES])
    _blob(tmp_path, VOICES, "b" * 40 + ".0a1b2c3d.incomplete", 1000)  # downloading
    _types(services.weights_reading(tmp_path, NOW))


# ---- the states, decided in order: downloading, cached, missing -----------------------------

def test_missing_with_no_folder(offline, tmp_path):
    w = _one(tmp_path, PARAKEET)
    assert w == {"repo": PARAKEET, "use": "stt", "state": "missing", "bytes_done": 0,
                 "bytes_total": 2_508_940_517, "expected": WEIGHTS[PARAKEET], "cached_ref": None,
                 "drift": False}


def test_downloading_while_an_incomplete_is_fresh(offline, tmp_path):
    """huggingface_hub 2.2.0 writes refs/main first, then each file into
    blobs/<etag>.<8hex>.incomplete, which hf_xet 1.7.0 grows in byte order."""
    _cache(tmp_path, PARAKEET, WEIGHTS[PARAKEET])
    _blob(tmp_path, PARAKEET, "c" * 40, 244_093, age=300, link="config.json")
    _blob(tmp_path, PARAKEET, "d" * 64 + ".9f8e7d6c.incomplete", 412_000_000, age=5)
    w = _one(tmp_path, PARAKEET)
    assert w["state"] == "downloading"
    assert w["bytes_done"] == 244_093 + 412_000_000 and w["bytes_total"] == 2_508_940_517
    assert w["cached_ref"] == WEIGHTS[PARAKEET] and w["drift"] is False


def test_downloading_before_refs_main_is_read(offline, tmp_path):
    """The state rests on the .incomplete alone: no ref is needed to say downloading."""
    _blob(tmp_path, KOKORO, "e" * 64 + ".00000000.incomplete", 10, age=0)
    assert _one(tmp_path, KOKORO)["state"] == "downloading"


@pytest.mark.parametrize("age, state", [(0, "downloading"), (119, "downloading"),
                                        (120, "downloading"), (121, "missing"), (86400, "missing")])
def test_the_120_second_line(offline, tmp_path, age, state):
    _cache(tmp_path, KOKORO, WEIGHTS[KOKORO])
    _blob(tmp_path, KOKORO, "f" * 64 + ".12345678.incomplete", 5_000_000, age=age)
    assert services.WEIGHTS_FRESH_S == 120
    assert _one(tmp_path, KOKORO)["state"] == state


def test_a_stale_incomplete_is_missing_even_beside_a_full_snapshot(offline, tmp_path):
    """A download that died leaves its .incomplete: the snapshot is not trusted as cached."""
    _full(tmp_path, KOKORO)
    _blob(tmp_path, KOKORO, "0" * 64 + ".aaaaaaaa.incomplete", 7, age=600)
    w = _one(tmp_path, KOKORO)
    assert w["state"] == "missing" and w["bytes_done"] == 355_322_783 + 7


def test_one_fresh_incomplete_wins_over_a_stale_one(offline, tmp_path):
    _cache(tmp_path, KOKORO, WEIGHTS[KOKORO])
    _blob(tmp_path, KOKORO, "1" * 64 + ".aaaaaaaa.incomplete", 7, age=600)
    _blob(tmp_path, KOKORO, "1" * 64 + ".bbbbbbbb.incomplete", 9, age=1)
    assert _one(tmp_path, KOKORO)["state"] == "downloading"


def test_cached_when_ref_snapshot_and_every_byte_are_there(offline, tmp_path):
    _full(tmp_path, PARAKEET)
    w = _one(tmp_path, PARAKEET)
    assert w["state"] == "cached" and w["bytes_done"] == w["bytes_total"] == 2_508_940_517
    assert w["cached_ref"] == WEIGHTS[PARAKEET] and w["drift"] is False


def test_a_partial_snapshot_is_missing(offline, tmp_path):
    """refs/main and snapshots/<ref> exist before the files arrive: short of the total is missing."""
    _cache(tmp_path, PARAKEET, WEIGHTS[PARAKEET])
    _blob(tmp_path, PARAKEET, "2" * 40, 244_093, link="config.json")
    w = _one(tmp_path, PARAKEET)
    assert w["state"] == "missing" and w["bytes_done"] == 244_093


def test_no_snapshot_dir_is_missing_however_many_bytes(offline, tmp_path):
    _cache(tmp_path, KOKORO, WEIGHTS[KOKORO], snapshot="")
    _blob(tmp_path, KOKORO, "3" * 64, 400_000_000)
    assert _one(tmp_path, KOKORO)["state"] == "missing"


def test_drift_is_a_flag_and_the_total_is_unknown(offline, tmp_path):
    """main moved: the reader cannot know that commit's size, so bytes_total is null, and the
    top-level total with it. It is cached when there is no .incomplete (the total is unknown)."""
    _cache(tmp_path, KOKORO, OTHER)
    _blob(tmp_path, KOKORO, "4" * 64, 300_000_000)
    r = services.weights_reading(tmp_path, NOW)
    w = r["weights"][1]
    assert w["drift"] is True and w["cached_ref"] == OTHER and w["bytes_total"] is None
    assert w["state"] == "cached" and w["bytes_done"] == 300_000_000
    assert r["bytes_total"] is None


def test_drift_while_downloading(offline, tmp_path):
    _cache(tmp_path, KOKORO, OTHER)
    _blob(tmp_path, KOKORO, "5" * 64 + ".12121212.incomplete", 1)
    w = _one(tmp_path, KOKORO)
    assert (w["state"], w["drift"], w["bytes_total"]) == ("downloading", True, None)


def test_the_expected_snapshot_on_disk_does_not_hide_drift(offline, tmp_path):
    """The server loads what main names, not any snapshot that happens to be there."""
    _full(tmp_path, KOKORO)
    (_folder(tmp_path, KOKORO) / "refs" / "main").write_text(OTHER)
    (_folder(tmp_path, KOKORO) / "snapshots" / OTHER).mkdir()
    w = _one(tmp_path, KOKORO)
    assert w["drift"] is True and w["cached_ref"] == OTHER


def test_a_trailing_newline_in_refs_main_still_matches(offline, tmp_path):
    _full(tmp_path, VOICES)
    (_folder(tmp_path, VOICES) / "refs" / "main").write_text(WEIGHTS[VOICES] + "\n")
    w = _one(tmp_path, VOICES)
    assert w["cached_ref"] == WEIGHTS[VOICES] and w["drift"] is False and w["state"] == "cached"


@pytest.mark.parametrize("how", ["no cache dir", "no repo", "no refs/main", "empty refs/main",
                                 "main, no snapshot", "refs is a file"])
def test_missing_without_a_ref_or_a_snapshot(offline, tmp_path, how):
    sha = WEIGHTS[VOICES]
    cache = tmp_path / "hub"
    if how == "no repo":
        _cache(cache, PARAKEET, WEIGHTS[PARAKEET])           # another repo only
    elif how == "no refs/main":
        _cache(cache, VOICES, None, snapshot=sha)            # fetched by sha: no refs/main
    elif how == "empty refs/main":
        _cache(cache, VOICES, "", snapshot=sha)
    elif how == "main, no snapshot":
        _cache(cache, VOICES, sha, snapshot="")
    elif how == "refs is a file":
        folder = _folder(cache, VOICES)
        folder.mkdir(parents=True)
        (folder / "refs").write_text("not a directory")
    if how != "no cache dir":
        _blob(cache, VOICES, "6" * 64, 28_206_306)            # every byte, but no usable ref
    w = _one(cache, VOICES)
    assert w["state"] == "missing" and w["drift"] is False
    assert w["cached_ref"] is None or w["cached_ref"] == sha


def test_a_blob_in_the_shared_store_counts_through_its_link(offline, tmp_path):
    """huggingface_hub 2.2.0 may move a finished Xet blob to <cache>/blobs/<2hex>/<hash> and leave
    models--x/blobs/<etag> as a relative symlink to it: the size is read through the link."""
    _cache(tmp_path, KOKORO, WEIGHTS[KOKORO])
    store = tmp_path / "blobs" / "ab"
    store.mkdir(parents=True)
    target = store / ("ab" + "7" * 62)
    with open(target, "wb") as f:
        f.truncate(355_322_783)
    link = _folder(tmp_path, KOKORO) / "blobs" / ("8" * 64)
    link.symlink_to(os.path.relpath(target, link.parent))
    (_folder(tmp_path, KOKORO) / "blobs" / ("9" * 64)).symlink_to("../../blobs/zz/gone")  # dangling
    w = _one(tmp_path, KOKORO)
    assert w["bytes_done"] == 355_322_783 and w["state"] == "cached"


def test_a_directory_in_blobs_counts_nothing(offline, tmp_path):
    _cache(tmp_path, KOKORO, WEIGHTS[KOKORO])
    (_folder(tmp_path, KOKORO) / "blobs" / "odd").mkdir()
    assert _one(tmp_path, KOKORO)["bytes_done"] == 0


def test_an_odd_refs_main_is_read_at_most_40_characters(offline, tmp_path):
    _cache(tmp_path, KOKORO, "x" * 500 + "\x1b[2J", snapshot="")
    w = _one(tmp_path, KOKORO)
    assert w["cached_ref"] == "x" * 40 and w["drift"] is True


# ---- the rollup -----------------------------------------------------------------------------

def test_overall_cached_only_when_all_are(offline, tmp_path):
    for repo in (PARAKEET, KOKORO, VOICES):
        _full(tmp_path, repo)
    r = services.weights_reading(tmp_path, NOW)
    assert r["state"] == "cached" and r["bytes_done"] == r["bytes_total"] == 2_892_469_606
    _blob(tmp_path, VOICES, "a" * 64 + ".00000000.incomplete", 0, age=900)   # one died
    assert services.weights_reading(tmp_path, NOW)["state"] == "missing"


def test_overall_downloading_if_any_is(offline, tmp_path):
    _full(tmp_path, PARAKEET)
    _blob(tmp_path, VOICES, "b" * 64 + ".00000000.incomplete", 522_339)
    r = services.weights_reading(tmp_path, NOW)
    assert r["state"] == "downloading"
    assert r["bytes_done"] == 2_508_940_517 + 522_339 and r["bytes_total"] == 2_892_469_606


def test_overall_missing_otherwise(offline, tmp_path):
    _full(tmp_path, PARAKEET)
    r = services.weights_reading(tmp_path, NOW)
    assert r["state"] == "missing" and r["bytes_done"] == 2_508_940_517


# ---- the rows: `ears services status` and `ears services weights` ---------------------------

def test_weights_status_one_header_and_a_row_each(offline, tmp_path):
    _full(tmp_path, PARAKEET)
    _cache(tmp_path, KOKORO, OTHER)
    rows = services.weights_status(tmp_path, NOW)
    assert len(rows) == 4 and all(r.startswith("weight ") for r in rows)
    head, *each = rows
    assert str(tmp_path) in head and "read-only" in head and "not a pin" in head and "EARS-" not in head
    assert "in all" not in head, "a drifted weight has no known total"
    assert each[0] == f"weight {PARAKEET}: cached, 2.5 GB ({WEIGHTS[PARAKEET][:12]})"
    assert f"DIFFERS: main is '{OTHER}' in the cache" in each[1] and WEIGHTS[KOKORO][:12] in each[1]
    assert each[2] == (f"weight {VOICES}: not cached (expected {WEIGHTS[VOICES][:12]}; the first use "
                       f"of each voice fetches its pack from main, 28 MB for all 54)")


def test_the_header_names_the_total(offline, tmp_path):
    assert "2.9 GB in all" in services.weights_status(tmp_path, NOW)[0]


def test_a_row_says_downloading_n_of_m(offline, tmp_path):
    _cache(tmp_path, PARAKEET, WEIGHTS[PARAKEET])
    _blob(tmp_path, PARAKEET, "c" * 64 + ".11111111.incomplete", 412_000_000)
    assert services.weights_status(tmp_path, NOW)[1] == \
        f"weight {PARAKEET}: downloading 412 MB of 2.5 GB"


def test_a_row_says_n_so_far_when_the_total_is_unknown(offline, tmp_path):
    _cache(tmp_path, KOKORO, OTHER)
    _blob(tmp_path, KOKORO, "c" * 64 + ".11111111.incomplete", 41_000_000)
    assert services.weights_status(tmp_path, NOW)[2].startswith(
        f"weight {KOKORO}: downloading, 41 MB so far; DIFFERS: main is '{OTHER}'")


def test_a_row_says_a_download_stopped(offline, tmp_path):
    _cache(tmp_path, PARAKEET, WEIGHTS[PARAKEET])
    _blob(tmp_path, PARAKEET, "c" * 64 + ".11111111.incomplete", 950_000_000, age=3600)
    assert services.weights_status(tmp_path, NOW)[1] == (
        f"weight {PARAKEET}: not cached: a download stopped at 950 MB of 2.5 GB "
        f"(expected {WEIGHTS[PARAKEET][:12]}; fetched from main at first use)")


def test_a_row_counts_the_voice_packs_the_server_fetched(offline, tmp_path):
    """The server fetches one pack per voice it is asked for: some packs is not every pack."""
    _cache(tmp_path, VOICES, WEIGHTS[VOICES])
    for i, v in enumerate(("af_heart", "af_bella", "am_adam")):
        _blob(tmp_path, VOICES, f"{i}" * 64, 522_339, age=3600, link=f"voices/{v}.safetensors")
    assert _one(tmp_path, VOICES)["state"] == "missing"
    assert services.weights_status(tmp_path, NOW)[3] == (
        f"weight {VOICES}: 3 of 54 packs (1.6 MB of 28 MB); the rest fetch at first use of each "
        f"voice; `ears services install` fetches them all")


@pytest.mark.parametrize("n, said", [(0, "0 B"), (999, "999 B"), (522_339, "522 KB"),
                                     (1_567_017, "1.6 MB"), (28_206_306, "28 MB"),
                                     (412_000_000, "412 MB"), (2_892_469_606, "2.9 GB"),
                                     (12_300_000_000, "12 GB")])
def test_sizes_for_a_person(n, said):
    assert services._size(n) == said


def test_a_differing_ref_is_quoted_and_cut(offline, tmp_path):
    """refs/main is a file anyone can write: it is shown quoted, at most 40 characters."""
    _cache(tmp_path, KOKORO, "x" * 500 + "\x1b[2J", snapshot="")
    row = services.weights_status(tmp_path, NOW)[2]
    assert "x" * 40 + "'" in row and "x" * 41 not in row and "\x1b" not in row


def test_a_control_character_in_a_short_ref_is_escaped(offline, tmp_path):
    _cache(tmp_path, KOKORO, "\x1b[2Jboo", snapshot="")
    _blob(tmp_path, KOKORO, "d" * 64, 1)
    row = services.weights_status(tmp_path, NOW)[2]
    assert "\x1b" not in row and "\\x1b[2Jboo" in row


def test_status_reads_the_servers_cache_not_this_shells_hf_home(offline, sx, monkeypatch, tmp_path, capsys):
    """The fence's HF_HOME (this shell's) is in tmp and holds every weight; status reports the
    server's cache, ~/.cache/huggingface/hub under the fence's HOME, and what it holds."""
    shell, server = services.hf_cache(), sx["home"] / ".cache/huggingface/hub"
    assert shell != server == services.speech_hf_cache()
    for r in WEIGHTS:
        _full(shell, r)
    _full(server, PARAKEET)
    _cache(server, VOICES, OTHER)
    assert services.status() == 0
    out = capsys.readouterr().out.splitlines()
    rows = [l for l in out if l.startswith("weight ")]
    assert f"the HF cache {server}," in rows[0] and str(shell) not in rows[0]
    assert rows[1:] == [
        f"weight {PARAKEET}: cached, 2.5 GB ({WEIGHTS[PARAKEET][:12]})",
        f"weight {KOKORO}: not cached (expected {WEIGHTS[KOKORO][:12]}; the first use fetches about "
        f"355 MB from main)",
        f"weight {VOICES}: cached, 0 B; DIFFERS: main is '{OTHER}' in the cache, expected "
        f"{WEIGHTS[VOICES][:12]}",
    ]
    speech_at = next(i for i, l in enumerate(out) if l.startswith("speech "))
    assert out.index(rows[0]) == speech_at + 1, "the weights follow the speech row"


def test_status_shows_downloading(offline, sx, monkeypatch, tmp_path, capsys):
    """The status rows are the reader's: a live download says so there too."""
    import time
    server = services.speech_hf_cache()
    _cache(server, PARAKEET, WEIGHTS[PARAKEET])
    part = _blob(server, PARAKEET, "e" * 64 + ".22222222.incomplete", 412_000_000)
    os.utime(part, (time.time(), time.time()))
    assert services.status() == 0
    out = capsys.readouterr().out
    assert f"weight {PARAKEET}: downloading 412 MB of 2.5 GB" in out.splitlines()


def test_status_with_no_cache_at_all_still_exits_0(offline, sx, monkeypatch, tmp_path, capsys):
    server = services.speech_hf_cache()
    assert services.status() == 0
    rows = [l for l in capsys.readouterr().out.splitlines() if l.startswith("weight ")]
    assert len(rows) == 4 and all("not cached" in r for r in rows[1:])
    assert not server.exists(), "the gauge creates nothing"


def test_the_reader_writes_nothing_in_the_cache(offline, tmp_path):
    _cache(tmp_path, PARAKEET, OTHER)
    _blob(tmp_path, PARAKEET, "f" * 64 + ".33333333.incomplete", 9)
    before = sorted((p, p.lstat().st_mtime_ns) for p in tmp_path.rglob("*"))
    services.weights_status(tmp_path)
    services.weights_reading(tmp_path)
    assert sorted((p, p.lstat().st_mtime_ns) for p in tmp_path.rglob("*")) == before


def test_the_reader_runs_no_command(offline, monkeypatch, tmp_path):
    """No launchctl, no python of the speech venv: nothing is spawned to read the cache."""
    def refuse(*a, **kw):
        raise AssertionError(f"the reader ran {a!r}")
    monkeypatch.setattr(services.subprocess, "run", refuse)
    monkeypatch.setattr(services.subprocess, "Popen", refuse)
    monkeypatch.setattr(services, "_launchctl", refuse)
    _full(tmp_path, PARAKEET)
    services.weights_reading(tmp_path)
    services.weights_status(tmp_path)
