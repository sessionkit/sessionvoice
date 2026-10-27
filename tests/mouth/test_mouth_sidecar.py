"""The sidecar reader (sidecar.py): one layout written, four read.

Each layout is built in a temp folder from empty files; the reader only looks
at names, the manifest and the source's size and mtime, so no audio is decoded
and no process runs.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

import pytest

from mouth import sidecar


@pytest.fixture
def song(tmp_path):
    p = tmp_path / "01-song.m4a"
    p.write_bytes(b"\0" * 64)
    return p


def put(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


def manifest(song: Path, **over) -> dict:
    st = song.stat()
    m = {
        "version": sidecar.MANIFEST_VERSION,
        "separator": "demucs",
        "model": "htdemucs",
        "source": {"name": song.name, "size": st.st_size, "mtime_ns": st.st_mtime_ns, "duration_s": 205.0},
        "verdict": "music",
    }
    m.update(over)
    return m


def write_manifest(song: Path, m) -> Path:
    d = sidecar.canonical_dir(song)
    d.mkdir(parents=True, exist_ok=True)
    (d / sidecar.MANIFEST).write_text(json.dumps(m) if not isinstance(m, str) else m)
    return d


def change_source(song: Path):
    """A different size and mtime: the manifest no longer matches by stat."""
    song.write_bytes(b"\0" * 128)
    st = song.stat()
    os.utime(song, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


# -- the folders ------------------------------------------------------------


def test_the_written_folder_has_the_dot_and_the_full_name(song):
    assert sidecar.canonical_dir(song) == song.parent / ".01-song.m4a.d"


def test_the_read_order_is_dot_then_name_then_base(song):
    assert sidecar.read_dirs(song) == [
        ("dot", song.parent / ".01-song.m4a.d"),
        ("name", song.parent / "01-song.m4a.d"),
        ("base", song.parent / "01-song.d"),
    ]


def test_a_file_with_no_extension_lists_its_folder_once(tmp_path):
    f = tmp_path / "song"
    assert [lay for lay, _ in sidecar.read_dirs(f)] == ["dot", "name"]


@pytest.mark.parametrize("name, base", [
    ("song.flac", "song"), ("07 - A.Track.Name.flac", "07 - A.Track.Name"),
    ("song", "song"), (".hidden", ".hidden"),
])
def test_base_name_drops_the_last_extension_only(name, base):
    assert sidecar.base_name(name) == base


# -- each layout hits ---------------------------------------------------------


def test_nothing_is_found_with_no_sidecar(song):
    assert sidecar.instrumental(song) is None
    assert sidecar.stem(song, "vocals") is None


def test_the_written_layout(song):
    d = sidecar.canonical_dir(song)
    inst = put(d / "mixes" / "instrumental.flac")
    for s in sidecar.STEMS:
        put(d / "stems" / f"{s}.flac")
    write_manifest(song, manifest(song))
    hit = sidecar.instrumental(song)
    assert (hit.path, hit.folder, hit.layout) == (inst, d, "dot")
    assert hit.manifest["model"] == "htdemucs"
    for s in sidecar.STEMS:
        h = sidecar.stem(song, s)
        assert (h.path, h.layout) == (d / "stems" / f"{s}.flac", "dot")


def test_the_no_dot_layout(song):
    d = song.parent / "01-song.m4a.d"
    inst = put(d / "mixes" / "instrumental.wav")
    drums = put(d / "stems" / "drums.wav")
    assert sidecar.instrumental(song) == sidecar.Hit(inst, d, "name")
    assert sidecar.stem(song, "drums") == sidecar.Hit(drums, d, "name")


def test_the_base_layout(song):
    d = song.parent / "01-song.d"
    inst = put(d / "mixes" / "instrumental.wav")
    bass = put(d / "stems" / "bass.wav")
    assert sidecar.instrumental(song) == sidecar.Hit(inst, d, "base")
    assert sidecar.stem(song, "bass") == sidecar.Hit(bass, d, "base")


def test_raw_demucs_outputs_in_the_dot_folder(song):
    """The hand command's tree: htdemucs/<base>/{vocals,no_vocals}.wav."""
    d = sidecar.canonical_dir(song)
    nv = put(d / "htdemucs" / "01-song" / "no_vocals.wav")
    voc = put(d / "htdemucs" / "01-song" / "vocals.wav")
    assert sidecar.instrumental(song) == sidecar.Hit(nv, d, "raw")
    assert sidecar.stem(song, "vocals") == sidecar.Hit(voc, d, "raw")


def test_raw_mlx_output(song):
    d = sidecar.canonical_dir(song)
    nv = put(d / "mlx-roformer" / "no_vocals.wav")
    assert sidecar.instrumental(song) == sidecar.Hit(nv, d, "raw")
    # the mlx route makes no other stem
    put(d / "mlx-roformer" / "vocals.wav")
    assert sidecar.stem(song, "vocals") is None


def test_raw_outputs_in_the_no_dot_and_base_folders(song):
    d = song.parent / "01-song.d"
    nv = put(d / "htdemucs_ft" / "01-song" / "no_vocals.flac")
    assert sidecar.instrumental(song) == sidecar.Hit(nv, d, "raw")
    d2 = song.parent / "01-song.m4a.d"
    nv2 = put(d2 / "htdemucs" / "01-song" / "no_vocals.flac")
    assert sidecar.instrumental(song) == sidecar.Hit(nv2, d2, "raw")


def test_a_raw_tree_for_another_track_is_not_this_one(song):
    put(sidecar.canonical_dir(song) / "htdemucs" / "02-other" / "no_vocals.wav")
    assert sidecar.instrumental(song) is None


# -- precedence -------------------------------------------------------------


def test_the_dot_folder_wins_over_the_others(song):
    dot = put(sidecar.canonical_dir(song) / "mixes" / "instrumental.flac")
    put(song.parent / "01-song.m4a.d" / "mixes" / "instrumental.flac")
    put(song.parent / "01-song.d" / "mixes" / "instrumental.flac")
    assert sidecar.instrumental(song).path == dot


def test_the_no_dot_folder_wins_over_the_base_folder(song):
    name = put(song.parent / "01-song.m4a.d" / "stems" / "other.wav")
    put(song.parent / "01-song.d" / "stems" / "other.flac")
    assert sidecar.stem(song, "other").path == name


def test_a_kept_mix_in_any_folder_wins_over_raw_outputs(song):
    """Layout 4 is read last: a curated mix beats a raw tree, even one in the dot folder."""
    put(sidecar.canonical_dir(song) / "htdemucs" / "01-song" / "no_vocals.wav")
    mix = put(song.parent / "01-song.d" / "mixes" / "instrumental.wav")
    assert sidecar.instrumental(song) == sidecar.Hit(mix, mix.parent.parent, "base")


@pytest.mark.parametrize("have, want", [
    (["mixes/instrumental", "mixes/karaoke", "stems/instrumental"], "mixes/instrumental"),
    (["mixes/karaoke", "stems/instrumental"], "mixes/karaoke"),
    (["stems/instrumental"], "stems/instrumental"),
])
def test_the_instrumental_names_in_order(song, have, want):
    d = song.parent / "01-song.m4a.d"
    for n in have:
        put(d / f"{n}.wav")
    assert sidecar.instrumental(song).path == d / f"{want}.wav"


def test_the_best_extension_wins(song):
    d = song.parent / "01-song.m4a.d" / "mixes"
    for ext in ("mp3", "wav", "flac", "opus"):
        put(d / f"instrumental.{ext}")
    assert sidecar.instrumental(song).path.name == "instrumental.flac"
    (d / "instrumental.flac").unlink()
    assert sidecar.instrumental(song).path.name == "instrumental.wav"


def test_a_folder_named_like_audio_is_not_audio(song):
    (song.parent / "01-song.m4a.d" / "mixes" / "instrumental.flac").mkdir(parents=True)
    assert sidecar.instrumental(song) is None


def test_an_unknown_extension_is_not_read(song):
    put(song.parent / "01-song.m4a.d" / "mixes" / "instrumental.txt")
    assert sidecar.instrumental(song) is None


def test_an_unknown_stem_name_is_refused(song):
    with pytest.raises(ValueError, match="vocals, drums, bass, other"):
        sidecar.stem(song, "piano")


# -- staleness ----------------------------------------------------------------


def test_a_matching_manifest_is_current(song):
    assert not sidecar.is_stale(manifest(song), song)


def test_a_changed_source_with_the_same_duration_is_current(song):
    """A re-tag changes the bytes, not the length."""
    m = manifest(song)
    change_source(song)
    assert not sidecar.is_stale(m, song, probe=lambda p: 205.4)


@pytest.mark.parametrize("probe", [
    lambda p: 207.0,      # off by more than the tolerance
    lambda p: None,       # no duration (Duration: N/A, no ffmpeg)
    None,                 # nothing to probe with
])
def test_a_changed_source_is_stale_unless_its_duration_holds(song, probe):
    m = manifest(song)
    change_source(song)
    assert sidecar.is_stale(m, song, probe=probe)


def test_a_probe_that_raises_is_stale(song):
    m = manifest(song)
    change_source(song)

    def boom(p):
        raise OSError("no ffmpeg")

    assert sidecar.is_stale(m, song, probe=boom)


@pytest.mark.parametrize("bad", [{}, {"source": "x"}, {"source": {"size": 1}}])
def test_a_manifest_without_a_source_record_is_stale(song, bad):
    assert sidecar.is_stale(bad, song, probe=lambda p: 205.0)


def test_a_missing_source_is_stale(song):
    m = manifest(song)
    song.unlink()
    assert sidecar.is_stale(m, song)


def test_a_stale_written_folder_is_skipped_for_the_next_layout(song):
    d = sidecar.canonical_dir(song)
    put(d / "mixes" / "instrumental.flac")
    put(d / "stems" / "vocals.flac")
    write_manifest(song, manifest(song))
    older = put(song.parent / "01-song.m4a.d" / "mixes" / "instrumental.wav")
    change_source(song)
    hit = sidecar.instrumental(song, probe=lambda p: 300.0)
    assert hit == sidecar.Hit(older, older.parent.parent, "name")
    assert sidecar.stem(song, "vocals", probe=lambda p: 300.0) is None


def test_a_stale_written_folder_hides_its_raw_outputs_too(song):
    d = sidecar.canonical_dir(song)
    put(d / "htdemucs" / "01-song" / "no_vocals.wav")
    write_manifest(song, manifest(song))
    assert sidecar.instrumental(song).layout == "raw"
    change_source(song)
    assert sidecar.instrumental(song) is None


def test_a_stale_written_folder_with_the_same_duration_is_still_read(song):
    d = sidecar.canonical_dir(song)
    inst = put(d / "mixes" / "instrumental.flac")
    write_manifest(song, manifest(song))
    change_source(song)
    assert sidecar.instrumental(song, probe=lambda p: 205.0).path == inst


@pytest.mark.parametrize("text", ["{not json", "[1, 2]", "\xff"])
def test_an_unreadable_manifest_makes_the_folder_stale(song, text):
    d = write_manifest(song, text)
    put(d / "mixes" / "instrumental.flac")
    assert sidecar.read_manifest(d) == {}
    assert sidecar.instrumental(song, probe=lambda p: 205.0) is None


def test_a_dot_folder_with_no_manifest_is_read_as_it_stands(song):
    inst = put(sidecar.canonical_dir(song) / "mixes" / "instrumental.wav")
    hit = sidecar.instrumental(song)
    assert (hit.path, hit.layout, hit.manifest, hit.verdict) == (inst, "dot", None, None)


def test_no_manifest_is_none(tmp_path):
    assert sidecar.read_manifest(tmp_path) is None


def test_staleness_is_judged_in_the_written_folder_only(song):
    """The other layouts have no manifest: a split.json there is not ours and not read."""
    d = song.parent / "01-song.m4a.d"
    (d).mkdir()
    (d / sidecar.MANIFEST).write_text("{}")
    inst = put(d / "mixes" / "instrumental.wav")
    hit = sidecar.instrumental(song)
    assert (hit.path, hit.manifest, hit.verdict) == (inst, None, None)


# -- the speech verdict -----------------------------------------------------------


@pytest.mark.parametrize("verdict", ["music", "speech"])
def test_the_verdict_is_reported(song, verdict):
    d = sidecar.canonical_dir(song)
    put(d / "mixes" / "instrumental.flac")
    put(d / "stems" / "drums.flac")
    write_manifest(song, manifest(song, verdict=verdict))
    assert sidecar.instrumental(song).verdict == verdict
    assert sidecar.stem(song, "drums").verdict == verdict


@pytest.mark.parametrize("bad", ["Speech", "", 1, None])
def test_an_invalid_verdict_is_none(song, bad):
    d = sidecar.canonical_dir(song)
    put(d / "mixes" / "instrumental.flac")
    write_manifest(song, manifest(song, verdict=bad))
    hit = sidecar.instrumental(song)
    assert hit.verdict is None and hit.manifest is not None


def test_other_layouts_report_no_verdict(song):
    put(song.parent / "01-song.d" / "mixes" / "instrumental.wav")
    assert sidecar.instrumental(song).verdict is None


@pytest.mark.parametrize("mix, inst, want", [
    (-14.0, -20.0, "music"),
    (-14.0, -33.9, "music"),
    (-14.0, -34.0, "speech"),    # exactly the gap
    (-14.0, -60.0, "speech"),
    (-14.0, -math.inf, "speech"),  # a silent instrumental
    (-math.inf, -math.inf, "music"),
    (None, -40.0, "music"),
    (-14.0, "x", "music"),
    (-14.0, math.nan, "music"),
    (True, -40.0, "music"),
])
def test_speech_verdict(mix, inst, want):
    assert sidecar.speech_verdict(mix, inst) == want


@pytest.mark.parametrize("m, want", [
    ({"verdict": "music"}, "music"), ({"verdict": "speech"}, "speech"),
    ({"verdict": "SPEECH"}, None), ({}, None), (None, None), ("x", None),
])
def test_manifest_verdict(m, want):
    assert sidecar.manifest_verdict(m) == want


# -- the duration probe's parser ------------------------------------------------


@pytest.mark.parametrize("text, want", [
    ("  Duration: 00:01:35.00, start: 0.000000, bitrate: 1411 kb/s", 95.0),
    ("Duration: 01:02:03.50, start", 3723.5),
    ("Duration: 00:00:00.30,", 0.3),
    ("  Duration: N/A, bitrate: N/A", None),
    ("x.m4a: No such file or directory", None),
    ("", None),
    (None, None),
])
def test_parse_duration(text, want):
    assert sidecar.parse_duration(text) == want
