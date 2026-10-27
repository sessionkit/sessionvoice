"""EARS-337 (SK-80 S3): the service venvs install by hash, and the diarizer by one revision.

- both shipped locks carry a --hash for every requirement, the one URL wheel included, and
  install runs `uv pip install --require-hashes`, so an artifact whose bytes differ is refused;
- SPEAKER_REVISION is a full commit sha, passed by the install's fetch and its cache check;
- the three speakers/ scripts that load the model hold the same literal and pass it. Fetched by
  its sha, the cache writes no refs/main, so a loader that asked for `main` would find nothing
  offline (HF_HUB_OFFLINE=1): every reader must name the same revision.

Offline, no venv: the locks and the scripts are read as text, and the cache check's python is
faked at subprocess.run. The tampered-wheel and offline-load proofs run against a real uv and
huggingface_hub in a throwaway venv, outside `make test` (the task's proofs/)."""
import re
import subprocess
from pathlib import Path

import pytest

from ears import services

from mouth.separator import separator_file

REPO = Path(__file__).resolve().parents[1]
# every shipped service lock, by name: a lock not listed here is checked by nothing below
LOCKS = {"speakers": (services._speakers_file, 35), "speech": (services._speech_file, 111),
         "separator": (separator_file, 32)}
LOADERS = ("speaker-sidecar2.py", "make-refs.py", "cluster-room.py")
HASH = re.compile(r"--hash=sha256:[0-9a-f]{64}")


def entries(text: str) -> list[str]:
    """The lock's requirements as logical lines: each `\\` continuation joined to its line, the
    `# via` comments and the header dropped."""
    out, cur = [], ""
    for line in text.splitlines():
        if line.lstrip().startswith("#") and not cur:
            continue
        cur += line.rstrip("\\").strip() + " "
        if not line.endswith("\\"):
            if cur.strip():
                out.append(cur.strip())
            cur = ""
    return out


@pytest.mark.parametrize("name", LOCKS)
def test_every_requirement_in_the_lock_carries_a_hash(name):
    get, pins = LOCKS[name]
    reqs = entries(get("requirements.lock").read_text())
    assert len(reqs) == pins                       # the pins install counts and prints, unchanged
    unhashed = [r.split()[0] for r in reqs if not HASH.search(r)]
    assert unhashed == []
    assert all(r.split()[0][:1].isalnum() for r in reqs)   # a requirement per entry, nothing else


def test_the_url_wheel_is_hashed_too():
    reqs = entries(services._speech_file("requirements.lock").read_text())
    urls = [r for r in reqs if "://" in r.split(" --hash")[0]]
    assert len(urls) == 1 and urls[0].startswith("en-core-web-sm @ https://") and HASH.search(urls[0])


@pytest.mark.parametrize("name", LOCKS)
def test_the_lock_and_its_source_say_how_it_is_compiled_and_installed(name):
    head = [l for l in LOCKS[name][0]("requirements.lock").read_text().splitlines()[:10]
            if l.startswith("#")]
    assert any("--require-hashes -r requirements.lock" in l for l in head)
    src = (REPO / name / "requirements.in").read_text()
    assert "--generate-hashes" in src and "over the existing lock" in src


def test_the_parser_sees_a_missing_hash():
    """The check above is only as good as entries(): a pin with no hash is caught."""
    text = "# head\nfoo==1.0 \\\n    --hash=sha256:" + "a" * 64 + "\n    # via x\nbar==2.0\n"
    assert [r.split()[0] for r in entries(text) if not HASH.search(r)] == ["bar==2.0"]


def test_the_revision_is_a_full_commit_sha():
    """A short sha is read as a ref name (refs/<short>), which never exists: offline it fails."""
    assert re.fullmatch(r"[0-9a-f]{40}", services.SPEAKER_REVISION)


def test_the_cache_check_asks_for_the_pinned_revision(monkeypatch, tmp_path):
    seen = []

    def run(argv, **kw):
        seen.append((argv, kw.get("env", {})))
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(services.subprocess, "run", run)
    assert services.speakers_model_cached(tmp_path / "py") is True
    (argv, env), = seen
    assert argv[1] == "-c" and "local_files_only=True" in argv[2]
    assert f"revision={services.SPEAKER_REVISION!r}" in argv[2]
    assert env.get("HF_HUB_OFFLINE") == "1"


@pytest.mark.parametrize("script", LOADERS)
def test_each_loader_holds_the_same_revision_and_passes_it(script):
    text = (REPO / "speakers" / script).read_text()
    found = re.findall(r'^REVISION = "([0-9a-f]{40})"$', text, re.M)
    assert found == [services.SPEAKER_REVISION]          # one literal, equal to services'
    loads = [l for l in text.splitlines() if re.search(r'\bload\("' + re.escape(services.SPEAKER_MODEL), l)]
    assert loads and all("revision=REVISION" in l for l in loads)


@pytest.mark.parametrize("script", LOADERS)
def test_the_package_ships_the_same_loader(script):
    assert (REPO / "src/ears/_speakers" / script).read_text() == (REPO / "speakers" / script).read_text()
