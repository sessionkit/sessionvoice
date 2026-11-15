"""A URL source's sidecar lives in ``<mouth dir>/url-sidecars/<sha256>/`` (EARS-519 need 3).

design.md section 11 (design-002) and section 10 item 4. A file played by ``file://``
or ``http(s)://`` is registered (``mouth.sourcekey``), and every reader and writer of
its sidecar (autosplit's split, ``mouth split``, the instrumental swap, ``--stem``)
uses ``url-sidecars/<sha256 of its bytes>/``. Nothing is ever made beside the source
file. A plain path keeps ``.<name>.d/`` beside it.

Offline: http goes through ``httpx.MockTransport``; ``split.spawn`` is spied, so no
separator runs for a play; the ``split_file`` tests run the fake separator
(``tests/fixtures/fake_separator.py``), as ``test_mouth_split.py`` does.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shlex
import stat
import struct
import sys
import time
import wave
from pathlib import Path

import httpx
import pytest

import mouth
from mouth import __main__ as cli
from mouth import box, fetch, sidecar, sourcekey, split, stems
from mouth import instrumental as _instrumental
from tests import env_fence
from tests._anchor import ANCHOR

SID = "test-sid-url-sidecar"
SONG = b"RIFF" + b"\0" * 64
OTHER = b"RIFF" + b"\1" * 64
URL = "https://music.invalid/albums/song.m4a"
FAKE = Path(__file__).resolve().parents[1] / "fixtures" / "fake_separator.py"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def home(tmp_path, monkeypatch):
    d = tmp_path / "mouth"
    d.mkdir()
    monkeypatch.setenv("MOUTH_DIR", str(d))
    monkeypatch.setenv("MOUTH_SETTINGS", str(tmp_path / "mouth.json"))
    monkeypatch.setenv("MOUTH_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MOUTH_DEVICE", "null")
    monkeypatch.setenv("MOUTH_BACKEND", "silence")
    monkeypatch.setenv("MOUTH_SESSION_BIN", "")
    monkeypatch.setenv("VOICEMODE_SESSION_ID", SID)
    env_fence.delenv(monkeypatch, "MOUTH_VOICE")
    # a separator is "present", so autosplit spawns a split for a song with none
    monkeypatch.setenv(split.SEPARATOR_ENV, f"{shlex.quote(sys.executable)} -c pass")
    box.ensure(box.box_dir(d))
    # a kept copy, as `music get` leaves it: <sha256>.flac, 0400, in a 0700 dir
    atom = tmp_path / "atom" / "files"
    atom.mkdir(parents=True)
    kept = atom / f"{sha(SONG)}.flac"
    kept.write_bytes(SONG)
    kept.chmod(0o400)
    atom.chmod(0o700)
    h = {"d": d, "tmp": tmp_path, "atom": atom, "kept": kept, "requests": [], "spawns": [],
         "probes": []}

    def handler(request: httpx.Request) -> httpx.Response:
        h["requests"].append(str(request.url))
        return httpx.Response(200, content=SONG, headers={"content-type": "audio/mp4"})

    def probe(path, *a, **kw):
        h["probes"].append(str(path))
        return 200.0

    monkeypatch.setattr(fetch, "client", lambda: httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=False))
    monkeypatch.setattr(_instrumental, "probe_duration", probe)
    monkeypatch.setattr(split, "spawn", lambda source, d=None: h["spawns"].append(str(source)))
    monkeypatch.setattr(mouth, "ensure_player", lambda d=None: False)
    yield h
    for p in (atom, kept):          # leave tmp removable whatever a test did to modes
        if p.exists():
            p.chmod(0o700)


def played(h, source, **kw):
    return mouth.play(str(source), spawn=False, d=h["d"], **kw)


def queued(h) -> list:
    return [box.public(x) for x in box.Queue(h["d"]).lines()]


def listing(folder: Path) -> list:
    return sorted(p.name for p in folder.iterdir())


def keyed(h, data: bytes = SONG) -> Path:
    return h["d"] / "url-sidecars" / sha(data)


def fill(folder: Path, data: bytes, *, source=None, duration_s=200.0, parts=("drums",)) -> dict:
    """A written-layout sidecar in FOLDER: an instrumental, the PARTS stems and a manifest
    whose source records DATA's sha256 (as split_file writes for a URL source)."""
    (folder / "mixes").mkdir(parents=True, exist_ok=True)
    (folder / "stems").mkdir(exist_ok=True)
    (folder / "mixes" / "instrumental.wav").write_bytes(b"inst")
    for p in parts:
        (folder / "stems" / f"{p}.wav").write_bytes(p.encode())
    rec = {"name": "x", "size": len(data), "mtime_ns": 1, "duration_s": duration_s,
           "sha256": sha(data), **(source or {})}
    m = {"version": sidecar.MANIFEST_VERSION, "source": rec,
         "outputs": {"instrumental": "mixes/instrumental.wav"}, "verdict": "music"}
    (folder / sidecar.MANIFEST).write_text(json.dumps(m))
    return m


def tone(path: Path, seconds: float, hz: int = 440, rate: int = 8000) -> Path:
    """A stereo 16-bit WAV tone (the split tests' shape, a lower rate)."""
    frames = bytearray()
    for i in range(int(seconds * rate)):
        v = int(16000 * math.sin(2 * math.pi * hz * i / rate))
        frames += struct.pack("<hh", v, v)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return path


# -- file://: registered, keyed by its bytes, nothing beside it ---------------------------

def test_a_file_url_play_of_a_kept_copy_makes_nothing_beside_it(home):
    kept, atom = home["kept"], home["atom"]
    before = listing(atom)
    item = played(home, kept.as_uri())
    real = str(kept.resolve())
    assert item["file"] == real
    assert sourcekey.lookup(kept) == (sha(SONG), True)
    assert sidecar.canonical_dir(kept) == keyed(home)
    assert sidecar.read_dirs(kept) == [("dot", keyed(home))]
    assert home["spawns"] == [real]                    # autosplit's split, spied
    assert split._writable(Path(real), home["d"]) is None
    assert listing(atom) == before                     # the 0700 dir is unchanged
    assert stat.S_IMODE(kept.stat().st_mode) == 0o400


def test_a_cached_http_play_keeps_only_the_file_and_its_entry_in_its_cache_folder(home):
    item = played(home, URL)
    f = Path(item["file"])
    assert sourcekey.lookup(f) == (sha(SONG), True)
    assert sidecar.canonical_dir(f) == keyed(home)
    assert home["spawns"] == [str(f)]
    assert listing(f.parent) == sorted([fetch.ENTRY, f.name])


def test_a_plain_path_keeps_its_sidecar_beside_it_and_registers_nothing(home):
    song = home["tmp"] / "music" / "song.wav"
    song.parent.mkdir()
    song.write_bytes(SONG)
    played(home, song)
    assert sourcekey.lookup(song) is None
    assert sidecar.canonical_dir(song) == song.parent / ".song.wav.d"
    assert [lay for lay, _ in sidecar.read_dirs(song)] == ["dot", "name", "base"]
    assert not (home["d"] / "url-sidecars").exists()   # a lookup never writes


def test_a_url_and_a_kept_copy_with_the_same_bytes_share_one_folder(home):
    a = Path(played(home, URL)["file"])
    played(home, home["kept"].as_uri())
    assert sidecar.canonical_dir(a) == sidecar.canonical_dir(home["kept"]) == keyed(home)


# -- the hashing cost ---------------------------------------------------------------------

def test_a_second_play_does_not_hash_again_and_a_changed_file_is_hashed_once(home, monkeypatch):
    calls = []
    real = sourcekey.hash_file
    monkeypatch.setattr(sourcekey, "hash_file", lambda p: calls.append(p) or real(p))
    uri = home["kept"].as_uri()
    played(home, uri)
    played(home, uri)
    played(home, home["kept"])                         # a plain path: a memo hit too
    assert len(calls) == 1
    os.utime(home["kept"], ns=(time.time_ns(), time.time_ns() + 5_000_000_000))
    assert sourcekey.lookup(home["kept"]).fresh is False
    played(home, uri)
    played(home, uri)
    assert len(calls) == 2
    assert sourcekey.lookup(home["kept"]) == (sha(SONG), True)


def test_two_paths_with_the_same_bytes_share_a_folder_and_its_manifest_needs_no_probe(home):
    b = home["tmp"] / "elsewhere" / "copy.flac"
    b.parent.mkdir()
    b.write_bytes(SONG)
    t = int(ANCHOR.timestamp()) * 10**9
    os.utime(b, ns=(t, t))                             # another mtime than the manifest's
    played(home, home["kept"].as_uri())
    played(home, b.as_uri())
    assert sidecar.canonical_dir(b) == sidecar.canonical_dir(home["kept"]) == keyed(home)
    fill(keyed(home), SONG)

    def no_probe(path):
        raise AssertionError("the sha256 short-cut needs no probe")

    hit = sidecar.instrumental(b, no_probe)
    assert hit.path == keyed(home) / "mixes" / "instrumental.wav" and hit.layout == "dot"
    assert sidecar.stem(b, "drums", no_probe).path == keyed(home) / "stems" / "drums.wav"


# -- every reader finds it ----------------------------------------------------------------

def test_the_swap_and_stems_find_a_registered_paths_url_sidecar(home):
    kept = str(home["kept"].resolve())
    played(home, home["kept"].as_uri())
    fill(keyed(home), SONG, parts=("drums", "bass"))
    line = {"backend": "file", "file": kept, "instrumental": True}
    got = _instrumental.swap(line, local=True, d=home["d"])
    assert got["file"] == str(keyed(home) / "mixes" / "instrumental.wav")
    assert got["source_file"] == kept
    got = stems.resolve({"backend": "file", "file": kept, "stem": "drums,bass"}, local=True,
                        d=home["d"])
    assert got["stem_files"] == [str(keyed(home) / "stems" / "drums.wav"),
                                 str(keyed(home) / "stems" / "bass.wav")]
    assert stems.check(kept, ["drums"]) == [str(keyed(home) / "stems" / "drums.wav")]


def test_a_missing_stem_of_a_url_source_is_refused_naming_mouth_split(home):
    kept = str(home["kept"].resolve())
    played(home, home["kept"].as_uri())
    fill(keyed(home), SONG, parts=("drums",))
    with pytest.raises(ValueError, match=r"no vocals stem yet: run `mouth split .*"
                                         + home["kept"].name):
        stems.check(kept, ["vocals"])
    n = len(queued(home))
    with pytest.raises(ValueError, match="mouth split"):
        played(home, home["kept"].as_uri(), stem="vocals")
    assert len(queued(home)) == n


# -- a file replaced with other bytes (r2) ------------------------------------------------

def test_a_replaced_file_never_gets_the_old_bytes_parts_and_a_play_rehashes_it(home):
    f = home["tmp"] / "songs" / "track.flac"
    f.parent.mkdir()
    f.write_bytes(SONG)
    played(home, f.as_uri())
    old = keyed(home, SONG)
    fill(old, SONG, parts=("drums",))
    snapshot = {p: p.read_bytes() for p in old.rglob("*") if p.is_file()}
    f.write_bytes(OTHER)                               # same length, other bytes
    os.utime(f, ns=(time.time_ns(), time.time_ns() + 7_000_000_000))
    before = listing(f.parent)
    # the entry is left stale on purpose, as at take time: the probe says 200 s, as for
    # the old song, and still nothing of the old folder is handed out
    assert sourcekey.lookup(f) == (sha(SONG), False)
    assert sidecar.canonical_dir(f) == old               # never beside the file
    line = {"backend": "file", "file": str(f), "instrumental": True}
    assert _instrumental.swap(line, local=True, d=home["d"]) == line
    got = stems.resolve({"backend": "file", "file": str(f), "stem": "drums"}, local=True,
                        d=home["d"])
    assert "stem_files" not in got and "drums" in got["stem_error"]
    assert sidecar.instrumental(f, lambda p: 200.0) is None
    assert sidecar.outputs_stale(json.loads((old / sidecar.MANIFEST).read_text()), f,
                                 lambda p: 200.0)
    assert listing(f.parent) == before
    # a plain-path play re-hashes it and finds its new bytes' folder
    played(home, f)
    assert sourcekey.lookup(f) == (sha(OTHER), True)
    assert sidecar.canonical_dir(f) == keyed(home, OTHER)
    assert listing(f.parent) == before
    assert {p: p.read_bytes() for p in old.rglob("*") if p.is_file()} == snapshot


# -- split_file and _writable ---------------------------------------------------------------

@pytest.fixture
def rig(tmp_path, monkeypatch):
    """The split tests' rig: a mouth dir in tmp and the fake separator."""
    d = tmp_path / "mouth"
    monkeypatch.setenv("MOUTH_DIR", str(d))
    monkeypatch.setenv(split.SEPARATOR_ENV,
                       f"{shlex.quote(sys.executable)} {shlex.quote(str(FAKE))}")
    monkeypatch.setenv("MOUTH_FAKE_SEPARATOR_LOG", str(tmp_path / "runs.jsonl"))
    src = tone(tmp_path / "kept" / "song.wav", 2.0)
    yield {"d": d, "tmp": tmp_path, "src": src}
    src.parent.chmod(0o700)


@pytest.mark.needs_ffmpeg
def test_split_file_on_a_registered_path_writes_its_url_sidecar_and_records_sha256(rig):
    src, d = rig["src"], rig["d"]
    digest = sourcekey.register(src, d)
    src.parent.chmod(0o500)                            # the source's dir is not ours
    before = listing(src.parent)
    code, line = split.split_file(src)
    assert code == 0, line
    folder = d / "url-sidecars" / digest
    assert line["sidecar"] == str(folder) and line["state"] == "made"
    m = json.loads((folder / sidecar.MANIFEST).read_text())
    assert m["source"]["sha256"] == digest
    assert listing(src.parent) == before
    assert sidecar.instrumental(src).path.parent.parent == folder
    assert split.split_file(src)[1]["state"] == "present"


@pytest.mark.needs_ffmpeg
def test_a_split_of_a_replaced_file_writes_to_its_new_bytes_folder(rig):
    src, d = rig["src"], rig["d"]
    first = sourcekey.register(src, d)
    assert split.split_file(src)[0] == 0
    old = d / "url-sidecars" / first
    snapshot = {p: p.read_bytes() for p in old.rglob("*") if p.is_file()}
    tone(src, 2.5, hz=660)                             # other bytes, within 1 s of the old
    os.utime(src, ns=(time.time_ns(), time.time_ns() + 3_000_000_000))
    assert sourcekey.lookup(src, d).fresh is False     # left stale, as a hand-run split finds it
    assert sidecar.instrumental(src, _instrumental.probe_duration) is None
    code, line = split.split_file(src)
    assert code == 0, line
    new = sourcekey.register(src, d)
    assert new != first and line["sidecar"] == str(d / "url-sidecars" / new)
    m = json.loads((d / "url-sidecars" / new / sidecar.MANIFEST).read_text())
    assert m["source"]["sha256"] == new
    assert {p: p.read_bytes() for p in old.rglob("*") if p.is_file()} == snapshot
    assert listing(src.parent) == ["song.wav"]


def test_writable_never_checks_a_registered_sources_own_dir(rig):
    src, d = rig["src"], rig["d"]
    src.parent.chmod(0o500)
    with pytest.raises(split.Refused, match="not writable"):
        split._writable(src, d)                        # a plain path: its dir, as today
    sourcekey.register(src, d)
    split._writable(src, d)                            # the mouth dir's nearest ancestor


def test_a_url_split_is_still_refused(rig):
    code, line = split.split_file("https://music.invalid/a.mp3")
    assert line["state"] == "refused" and "URL is never split" in line["detail"]


# -- the sweep, and an unreadable source --------------------------------------------------

def test_the_sweep_forgets_a_swept_path_and_keeps_its_url_sidecar(home):
    folder = fetch.spool_dir(home["d"]) / "abc-0001"
    folder.mkdir(parents=True)
    f = folder / "song.mp3"
    f.write_bytes(SONG)
    digest = sourcekey.register(f, home["d"])
    fill(keyed(home), SONG)
    os.utime(folder, (0, 0))
    gone = fetch.sweep_spool(home["d"])
    assert gone == [folder] and not folder.exists()
    assert sourcekey.lookup(f, home["d"]) is None
    assert not list((sourcekey.root(home["d"]) / sourcekey.PATHS).iterdir())
    assert (keyed(home) / sidecar.MANIFEST).is_file() and digest == sha(SONG)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a 0000 file")
def test_an_unreadable_file_url_is_refused_plainly_with_nothing_queued(home, capsys):
    home["kept"].chmod(0)
    with pytest.raises(ValueError, match=r"^mouth: cannot read .*: Permission denied$"):
        played(home, home["kept"].as_uri())
    assert queued(home) == []
    assert cli.main(["play", home["kept"].as_uri()]) == 2
    err = capsys.readouterr().err
    assert err.splitlines()[-1].startswith("mouth: cannot read") and "Traceback" not in err
    assert queued(home) == [] and home["spawns"] == []
    assert not (home["d"] / "url-sidecars" / sourcekey.PATHS).exists()


# -- the registry itself ------------------------------------------------------------------

def test_the_registry_entry_is_written_whole_and_names_the_real_path(home, tmp_path):
    link = tmp_path / "link.flac"
    link.symlink_to(home["kept"])
    digest = sourcekey.register(link, home["d"])
    assert sourcekey.lookup(home["kept"], home["d"]) == (digest, True)
    entries = list((sourcekey.root(home["d"]) / sourcekey.PATHS).iterdir())
    assert len(entries) == 1 and not entries[0].name.endswith(".part")
    rec = json.loads(entries[0].read_text())
    assert rec["path"] == os.path.realpath(home["kept"]) and rec["sha256"] == digest
    assert {"size", "mtime_ns", "ino", "t"} <= set(rec)
    assert sourcekey.forget(link, home["d"]) is True
    assert sourcekey.lookup(home["kept"], home["d"]) is None
    assert sourcekey.forget(link, home["d"]) is False
