"""EARS-339 fix-002: the speech server's weights, gauged read-only.

mlx-audio's server fetches Parakeet, Kokoro and Kokoro's voice packs from Hugging Face `main`
at first use, with no revision. sessionvoice cannot pin them there (EARS-357), so it names the
expected commits in ONE place (services.SPEECH_WEIGHTS) and `ears services status` reports
what the HF cache's refs/main points at for each: match, differs, or not cached.

Everything here is offline: a fake HF cache in tmp_path, huggingface_hub made unimportable and
every socket refused, so a gauge that reached for the hub or the network would fail, not fetch."""
import builtins
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


# ---- the three states ---------------------------------------------------------------------

def test_match_when_main_is_the_expected_snapshot(offline, tmp_path):
    cache = _cache(tmp_path, PARAKEET, WEIGHTS[PARAKEET])
    assert services.weight_state(PARAKEET, WEIGHTS[PARAKEET], cache) == ("match", WEIGHTS[PARAKEET])


def test_differs_when_main_moved(offline, tmp_path):
    cache = _cache(tmp_path, KOKORO, OTHER)
    assert services.weight_state(KOKORO, WEIGHTS[KOKORO], cache) == ("differs", OTHER)


def test_differs_reads_main_not_any_snapshot(offline, tmp_path):
    """The expected snapshot is on disk, but main points elsewhere: the server loads main."""
    cache = _cache(tmp_path, KOKORO, OTHER)
    (cache / ("models--" + KOKORO.replace("/", "--")) / "snapshots" / WEIGHTS[KOKORO]).mkdir()
    assert services.weight_state(KOKORO, WEIGHTS[KOKORO], cache)[0] == "differs"


def test_a_trailing_newline_in_refs_main_still_matches(offline, tmp_path):
    cache = _cache(tmp_path, VOICES, WEIGHTS[VOICES] + "\n", snapshot=WEIGHTS[VOICES])
    assert services.weight_state(VOICES, WEIGHTS[VOICES], cache)[0] == "match"


@pytest.mark.parametrize("how", ["no cache dir", "no repo", "no refs/main", "empty refs/main",
                                 "main, no snapshot", "refs is a file"])
def test_not_cached(offline, tmp_path, how):
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
        folder = cache / ("models--" + VOICES.replace("/", "--"))
        folder.mkdir(parents=True)
        (folder / "refs").write_text("not a directory")
    assert services.weight_state(VOICES, sha, cache)[0] == "not cached"


# ---- the status rows ----------------------------------------------------------------------

def test_weights_status_one_header_and_a_row_each(offline, tmp_path):
    cache = _cache(tmp_path, PARAKEET, WEIGHTS[PARAKEET])
    _cache(cache, KOKORO, OTHER)
    rows = services.weights_status(cache)
    assert len(rows) == 4 and all(r.startswith("weight ") for r in rows)
    head, *each = rows
    assert str(cache) in head and "read-only" in head and "not a pin" in head and "EARS-" not in head
    assert each[0] == f"weight {PARAKEET}: match ({WEIGHTS[PARAKEET][:12]})"
    assert each[1].startswith(f"weight {KOKORO}: DIFFERS: main is '{OTHER}'")
    assert WEIGHTS[KOKORO][:12] in each[1]
    assert each[2].startswith(f"weight {VOICES}: not cached")


def test_a_differing_ref_is_quoted_and_cut(offline, tmp_path):
    """refs/main is a file anyone can write: it is shown quoted, at most 40 characters."""
    cache = _cache(tmp_path, KOKORO, "x" * 500 + "\x1b[2J", snapshot="")
    row = services.weights_status(cache)[2]
    assert "x" * 40 + "'" in row and "x" * 41 not in row and "\x1b" not in row


def test_status_reads_a_non_default_hf_home(offline, sx, monkeypatch, tmp_path, capsys):
    hfhome = tmp_path / "somewhere-else"
    _cache(hfhome / "hub", PARAKEET, WEIGHTS[PARAKEET])
    _cache(hfhome / "hub", VOICES, OTHER)
    monkeypatch.setenv("HF_HOME", str(hfhome))
    assert services.status() == 0
    out = capsys.readouterr().out.splitlines()
    rows = [l for l in out if l.startswith("weight ")]
    assert str(hfhome / "hub") in rows[0]
    assert rows[1:] == [
        f"weight {PARAKEET}: match ({WEIGHTS[PARAKEET][:12]})",
        f"weight {KOKORO}: not cached (expected {WEIGHTS[KOKORO][:12]}; fetched from main at first use)",
        f"weight {VOICES}: DIFFERS: main is '{OTHER}' in the cache, expected {WEIGHTS[VOICES][:12]}",
    ]
    speech_at = next(i for i, l in enumerate(out) if l.startswith("speech "))
    assert out.index(rows[0]) == speech_at + 1, "the weights follow the speech row"


def test_status_with_no_cache_at_all_still_exits_0(offline, sx, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "never-made"))
    assert services.status() == 0
    rows = [l for l in capsys.readouterr().out.splitlines() if l.startswith("weight ")]
    assert len(rows) == 4 and all("not cached" in r for r in rows[1:])
    assert not (tmp_path / "never-made").exists(), "the gauge creates nothing"


def test_the_gauge_writes_nothing_in_the_cache(offline, tmp_path):
    cache = _cache(tmp_path, PARAKEET, OTHER)
    before = sorted((p, p.stat().st_mtime_ns) for p in cache.rglob("*"))
    services.weights_status(cache)
    assert sorted((p, p.stat().st_mtime_ns) for p in cache.rglob("*")) == before
