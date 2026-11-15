"""``mouth play URL``, end to end (EARS-519 design.md section 6, section 10 slice 3).

Through ``mouth.play(..., spawn=False)`` and ``main(["play", ...])``, every README
criterion that names what play or the CLI does:

- a path plays as before; ``file://`` queues exactly the path's ``file``, decoded,
  never fetched or copied, whatever the remote switch says;
- ``http(s)://`` is fetched into the play cache and the CACHED path is queued; a
  second play is a hit with no request; two URLs never share a file;
- refusals (a scheme, a ``file://`` host, the remote switch, a lock, a failed fetch,
  an unwritable cache) raise before anything is queued, and the CLI exits 2;
- the cache settings move, expire and switch off the cache;
- ``mouth play`` returns once the line is queued, and its options are the same set.

Offline: every fetch goes through ``httpx.MockTransport`` (``fetch.client`` is the
seam), and a client that is never meant to be built fails the test if it is. The
split is spied (``split.spawn``), the duration probe faked; nothing runs ffmpeg.
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from pathlib import Path

import httpx
import pytest

import mouth
from mouth import __main__ as cli
from mouth import box, fetch
from mouth import instrumental as _instrumental
from mouth import split as _split
from tests import env_fence

SID = "test-sid-play-url"
SONG = b"RIFF" + b"\0" * 64
URL = "https://music.invalid/albums/song.m4a"
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def home(tmp_path, monkeypatch):
    d = tmp_path / "mouth"
    d.mkdir()
    host = tmp_path / "mouth.json"
    monkeypatch.setenv("MOUTH_DIR", str(d))
    monkeypatch.setenv("MOUTH_SETTINGS", str(host))
    monkeypatch.setenv("MOUTH_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MOUTH_DEVICE", "null")
    monkeypatch.setenv("MOUTH_BACKEND", "silence")
    monkeypatch.setenv("MOUTH_SESSION_BIN", "")   # the cascade off unless a test fakes it
    monkeypatch.setenv("VOICEMODE_SESSION_ID", SID)
    env_fence.delenv(monkeypatch, "MOUTH_VOICE")
    monkeypatch.delenv(_split.SEPARATOR_ENV, raising=False)
    box.ensure(box.box_dir(d))
    song = tmp_path / "music" / "song.m4a"
    song.parent.mkdir()
    song.write_bytes(SONG)
    h = {"d": d, "host": host, "tmp": tmp_path, "song": song, "requests": [], "spawns": [],
         "routes": {}}

    def handler(request: httpx.Request) -> httpx.Response:
        h["requests"].append(str(request.url))
        route = h["routes"].get(str(request.url))
        if route is None:
            return httpx.Response(200, content=SONG, headers={"content-type": "audio/mp4"})
        return route(request) if callable(route) else route

    monkeypatch.setattr(fetch, "client", lambda: httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=False))
    monkeypatch.setattr(_instrumental, "probe_duration", lambda path, *a, **kw: 200.0)
    monkeypatch.setattr(_split, "spawn", lambda source, d=None: h["spawns"].append(str(source)))
    monkeypatch.setattr(mouth, "ensure_player", lambda d=None: False)
    return h


def host(h, **kw):
    h["host"].write_text(json.dumps(kw))


def fake_session(h, monkeypatch, layer="session", locked=(), **values):
    """A fake ``session settings --json``: ``values`` set in ``layer``; each key in
    ``locked`` locked by the host layer."""
    path = f"/fake/{layer}/mouth.json"
    hpath = str(h["host"])
    out = {"tools": {"mouth": {
        "values": values, "origins": {k: path for k in values},
        "locked": {k: hpath for k in locked},
        "sources": [{"layer": layer, "path": path}, {"layer": "host", "path": hpath}]}}}
    p = h["tmp"] / f"fake-session-{layer}"
    p.write_text(f"#!/bin/sh\ncat <<'EOF'\n{json.dumps(out)}\nEOF\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("MOUTH_SESSION_BIN", str(p))


def no_client(monkeypatch):
    """Any request at all fails the test: for plays that must never fetch."""
    def boom():
        raise AssertionError("no client may be built for this play")
    monkeypatch.setattr(fetch, "client", boom)


def played(h, source, **kw):
    return mouth.play(str(source), spawn=False, d=h["d"], **kw)


def queued(h) -> list:
    return [box.public(x) for x in box.Queue(h["d"]).lines()]


def files(h) -> list:
    return [x["file"] for x in queued(h)]


def cache(h) -> Path:
    return h["d"] / "play-cache"


def spool(h) -> Path:
    return h["d"] / "play-spool"


def nothing_queued(h):
    assert queued(h) == []
    assert list((box.box_dir(h["d"]) / "new").iterdir()) == []


# -- paths are unchanged ------------------------------------------------------------------

def test_a_path_and_a_home_relative_path_queue_the_file(home, monkeypatch):
    no_client(monkeypatch)
    monkeypatch.setenv("HOME", str(home["tmp"]))
    played(home, home["song"])
    played(home, "~/music/song.m4a")
    real = str(home["song"].resolve())
    assert files(home) == [real, real]
    assert not cache(home).exists() and not spool(home).exists()
    assert all("source_url" not in x for x in queued(home))


def test_a_missing_path_is_todays_refusal(home):
    with pytest.raises(ValueError, match=r"^mouth: no such file /nope/a\.m4a$"):
        played(home, "/nope/a.m4a")
    nothing_queued(home)


# -- file:// plays the local file, in place ----------------------------------------------

def test_a_file_url_queues_exactly_the_paths_file(home, monkeypatch):
    no_client(monkeypatch)
    by_path = played(home, home["song"])
    by_url = played(home, home["song"].resolve().as_uri())
    assert by_url["file"] == by_path["file"] == str(home["song"].resolve())
    assert by_url["text"] == by_path["text"]
    assert "source_url" not in by_url


def test_a_file_url_is_percent_decoded(home, monkeypatch):
    no_client(monkeypatch)
    spaced = home["tmp"] / "My Music" / "My Song #1.m4a"
    spaced.parent.mkdir()
    spaced.write_bytes(SONG)
    uri = spaced.resolve().as_uri()
    assert "%20" in uri and "%23" in uri
    assert played(home, uri)["file"] == str(spaced.resolve())


def test_file_localhost_plays_and_another_host_is_refused(home, monkeypatch):
    no_client(monkeypatch)
    real = str(home["song"].resolve())
    assert played(home, f"file://localhost{real}")["file"] == real
    with pytest.raises(ValueError, match="names this host only"):
        played(home, f"file://otherhost{real}")
    assert files(home) == [real]


def test_a_missing_file_url_names_the_decoded_path(home, monkeypatch):
    no_client(monkeypatch)
    with pytest.raises(ValueError, match=r"^mouth: no such file /nope/My Song\.m4a$"):
        played(home, "file:///nope/My%20Song.m4a")
    nothing_queued(home)


def test_a_file_url_is_never_cached_or_spooled(home, monkeypatch):
    no_client(monkeypatch)
    before = sorted(p.name for p in home["song"].parent.iterdir())
    played(home, home["song"].resolve().as_uri())
    assert not cache(home).exists() and not spool(home).exists()
    assert sorted(p.name for p in home["song"].parent.iterdir()) == before


def test_the_remote_switch_never_refuses_a_file_url_or_a_path(home, monkeypatch):
    no_client(monkeypatch)
    host(home, play_remote=False, _locked=["play_remote"])
    played(home, home["song"].resolve().as_uri())
    played(home, home["song"])
    assert len(queued(home)) == 2


# -- http(s): fetched, cached, the cached path queued -------------------------------------

def test_a_remote_url_is_fetched_and_the_cached_path_queued(home, capsys):
    item = played(home, URL)
    want = cache(home) / fetch.key(URL) / "song.m4a"
    assert home["requests"] == [URL]
    assert item["file"] == str(want.resolve()) and want.read_bytes() == SONG
    assert item["source_url"] == URL
    assert files(home) == [str(want.resolve())]
    assert queued(home)[0]["source_url"] == URL
    assert f"mouth: fetching {URL}" in capsys.readouterr().err


def test_a_second_play_is_a_hit_with_no_request(home, capsys):
    first = played(home, URL)
    capsys.readouterr()
    second = played(home, URL)
    assert home["requests"] == [URL]
    assert second["file"] == first["file"]
    assert "fetching" not in capsys.readouterr().err


def test_http_plays_too(home):
    item = played(home, "http://music.invalid/a/b.mp3")
    assert Path(item["file"]).name == "b.mp3"


def test_the_cached_name_keeps_the_extension_and_two_urls_never_share(home):
    a = played(home, "https://one.invalid/x/song.mp3")
    b = played(home, "https://two.invalid/y/song.mp3")
    assert Path(a["file"]).name == Path(b["file"]).name == "song.mp3"
    assert a["file"] != b["file"]
    assert a["text"] == "[sound song.mp3]"


def test_a_shown_url_drops_its_password(home, capsys):
    item = played(home, "https://ann:s3cret@music.invalid/song.m4a")
    err = capsys.readouterr().err
    assert item["source_url"] == "https://music.invalid/song.m4a"
    assert "s3cret" not in err and "s3cret" not in json.dumps(queued(home))


def test_the_split_runs_on_the_cached_file_and_play_does_not_wait_for_it(home, monkeypatch):
    """Need 4: play returns once queued; autosplit's split is spawned, not awaited."""
    monkeypatch.setenv(_split.SEPARATOR_ENV, f"{sys.executable} -c pass")
    item = played(home, URL)
    assert home["spawns"] == [item["file"]]
    assert queued(home)[0]["utt"] == item["utt"]


# -- refusals: nothing requested, nothing queued ------------------------------------------

@pytest.mark.parametrize("url, scheme", [("ftp://music.invalid/a.mp3", "ftp"),
                                         ("rtsp://music.invalid/live", "rtsp"),
                                         ("HTTPX://music.invalid/a.mp3", "httpx")])
def test_an_unsupported_scheme_is_refused_naming_it(home, monkeypatch, url, scheme):
    no_client(monkeypatch)
    with pytest.raises(ValueError, match=f"a {scheme}:// URL is not played"):
        played(home, url)
    nothing_queued(home)


@pytest.mark.parametrize("layer", ["settings", "session", "seat", "agent"])
def test_remote_off_refuses_a_remote_url_naming_the_setting(home, monkeypatch, layer):
    no_client(monkeypatch)
    if layer == "settings":
        host(home, play_remote=False)
    else:
        fake_session(home, monkeypatch, layer, play_remote=False)
    with pytest.raises(ValueError, match=rf"play_remote is off \({layer}\)"):
        played(home, URL)
    nothing_queued(home)
    assert not cache(home).exists() and not spool(home).exists()


def test_a_locked_host_beats_a_session_that_turns_remote_on(home, monkeypatch):
    no_client(monkeypatch)
    host(home, play_remote=False, _locked=["play_remote"])
    fake_session(home, monkeypatch, "session", play_remote=True)
    with pytest.raises(ValueError, match=r"play_remote is off \(settings\)"):
        played(home, URL)
    nothing_queued(home)


def test_without_a_lock_the_session_turns_remote_back_on(home, monkeypatch):
    host(home, play_remote=False)
    fake_session(home, monkeypatch, "session", play_remote=True)
    assert played(home, URL)["source_url"] == URL


@pytest.mark.parametrize("route, why", [
    (httpx.Response(404), "HTTP 404"),
    (httpx.Response(200, text="<html>", headers={"content-type": "text/html"}),
     r"not a sound file \(text/html\)"),
])
def test_a_failed_fetch_says_so_and_leaves_nothing(home, route, why):
    home["routes"][URL] = route
    with pytest.raises(ValueError, match=rf"^mouth: could not fetch {re.escape(URL)}: {why}"):
        played(home, URL)
    nothing_queued(home)
    assert not list((cache(home) / fetch.TMP).glob("*.part"))
    assert not (cache(home) / fetch.key(URL)).exists()


@pytest.mark.parametrize("exc, why", [(httpx.ConnectTimeout, "timed out"),
                                      (httpx.ReadTimeout, "timed out"),
                                      (httpx.ConnectError, "connection failed")])
def test_a_timeout_or_a_connect_error_says_so_and_a_later_play_retries(home, exc, why):
    def fail(request):
        raise exc("down", request=request)

    home["routes"][URL] = fail
    with pytest.raises(ValueError, match=f"could not fetch {re.escape(URL)}: {why}"):
        played(home, URL)
    nothing_queued(home)
    del home["routes"][URL]
    assert played(home, URL)["source_url"] == URL
    assert home["requests"] == [URL, URL]


def test_an_unwritable_cache_is_a_plain_refusal(home):
    """Foreman ruling (do-001 review A1): an OSError out of the fetch, here a cache dir
    under a plain file, is the plain line and nothing queued, never a traceback."""
    blocker = home["tmp"] / "not-a-dir"
    blocker.write_text("x")
    host(home, play_cache_dir=str(blocker / "cache"))
    with pytest.raises(ValueError, match=rf"^mouth: could not fetch {re.escape(URL)}: "
                                         r"could not write it \("):
        played(home, URL)
    nothing_queued(home)


def test_a_bad_range_is_refused_before_any_download(home, monkeypatch):
    no_client(monkeypatch)
    with pytest.raises(ValueError, match="--end must be after --start"):
        played(home, URL, start=10, end=5)
    nothing_queued(home)


# -- the cache settings -------------------------------------------------------------------

def test_the_default_cache_is_under_the_mouth_dir_and_kept_forever(home):
    item = played(home, URL)
    entry = cache(home) / fetch.key(URL) / fetch.ENTRY
    data = json.loads(entry.read_text())
    data["fetched_t"] = 0.0                 # 1970: still fresh, with no age set
    entry.write_text(json.dumps(data))
    assert played(home, URL)["file"] == item["file"]
    assert home["requests"] == [URL]


def test_the_cache_dir_setting_moves_the_files(home):
    elsewhere = home["tmp"] / "elsewhere"
    host(home, play_cache_dir=str(elsewhere))
    item = played(home, URL)
    assert Path(item["file"]).parent == (elsewhere / fetch.key(URL)).resolve()
    assert not cache(home).exists()


def test_an_entry_older_than_the_age_is_fetched_again(home):
    host(home, play_cache_max_age_s=3600)
    played(home, URL)
    played(home, URL)
    assert home["requests"] == [URL]
    entry = cache(home) / fetch.key(URL) / fetch.ENTRY
    data = json.loads(entry.read_text())
    data["fetched_t"] -= 7200
    entry.write_text(json.dumps(data))
    played(home, URL)
    assert home["requests"] == [URL, URL]


@pytest.mark.parametrize("where", ["settings", "session"])
def test_cache_off_still_plays_and_leaves_nothing_in_the_cache(home, monkeypatch, where):
    if where == "settings":
        host(home, play_cache=False)
    else:
        fake_session(home, monkeypatch, "seat", play_cache=False)
    a = played(home, URL)
    b = played(home, URL)
    assert home["requests"] == [URL, URL]           # every cache-off play fetches
    assert not cache(home).exists()
    for item in (a, b):
        assert Path(item["file"]).is_file()
        assert Path(item["file"]).is_relative_to(spool(home).resolve())
    assert a["file"] != b["file"]


def test_a_remote_play_sweeps_an_old_unnamed_spool_folder(home):
    old = spool(home) / "deadbeef-0000"
    old.mkdir(parents=True)
    (old / "gone.mp3").write_bytes(SONG)
    os.utime(old, (0, 0))
    played(home, URL)
    assert not old.exists()


# -- the CLI: exit codes, returns once queued, no new flags -------------------------------

def test_the_cli_plays_a_url_and_prints_the_utt(home, capsys):
    assert cli.main(["play", URL]) == 0
    out = capsys.readouterr().out.strip()
    assert [x["utt"] for x in queued(home)] == [out]
    assert queued(home)[0]["file"].startswith(str(cache(home).resolve()))


def test_the_cli_plays_a_file_url(home, capsys, monkeypatch):
    no_client(monkeypatch)
    assert cli.main(["play", home["song"].resolve().as_uri()]) == 0
    assert files(home) == [str(home["song"].resolve())]


@pytest.mark.parametrize("case", ["scheme", "host", "remote-off", "404", "timeout",
                                  "unwritable", "missing-file"])
def test_every_refusal_exits_non_zero_with_nothing_queued(home, capsys, case):
    url = URL
    if case == "scheme":
        url = "ftp://music.invalid/a.mp3"
    elif case == "host":
        url = "file://elsewhere/a.mp3"
    elif case == "remote-off":
        host(home, play_remote=False)
    elif case == "404":
        home["routes"][URL] = httpx.Response(404)
    elif case == "timeout":
        def slow(request):
            raise httpx.ReadTimeout("slow", request=request)
        home["routes"][URL] = slow
    elif case == "unwritable":
        (home["tmp"] / "f").write_text("x")
        host(home, play_cache_dir=str(home["tmp"] / "f" / "c"))
    elif case == "missing-file":
        url = "file:///nope/a.mp3"
    assert cli.main(["play", url]) == 2
    err = capsys.readouterr().err
    assert err.splitlines()[-1].startswith("mouth: ") and "Traceback" not in err
    nothing_queued(home)


def test_play_has_no_new_options(home, capsys):
    """Need 5: the remote switch has no flag. The option set is pinned: d31c31f's,
    plus ``--last``, the third queue mode beside ``--now`` and ``--next``, and the
    four of ``--find`` (``--find``, ``--finder``, ``--matches``, ``--pick``)."""
    with pytest.raises(SystemExit):
        cli.main(["play", "--help"])
    usage = capsys.readouterr().out
    got = set(re.findall(r"(?<![\w-])(--?[a-z][a-z-]*)", usage))
    assert got == {"-h", "--help", "--start", "--end", "--device", "--pan", "--channel",
                   "--log-dir", "--volume", "--duck", "--no-duck", "--duck-level",
                   "--instrumental", "--no-instrumental", "--stem", "--wait", "--next",
                   "--now", "--last", "--find", "--finder", "--matches", "--pick"}


def test_the_help_says_source_is_a_path_or_a_url(home, capsys):
    with pytest.raises(SystemExit):
        cli.main(["play", "--help"])
    usage = " ".join(capsys.readouterr().out.split())
    assert "file://, http:// or https:// URL" in usage


# -- what stays as it was -----------------------------------------------------------------

@pytest.mark.parametrize("rel, line", [
    ("src/mouth/backends.py", 'cmd += ["-protocol_whitelist", "file"]'),
    ("src/mouth/instrumental.py", '"-protocol_whitelist", "file", "-i", str(path)'),
    ("src/mouth/split.py", '"-protocol_whitelist", "file", "-i", str(path)'),
])
def test_the_decoder_lines_still_read_files_only(rel, line):
    assert line in (REPO / rel).read_text()


def test_no_never_a_url_text_is_left_for_mouth_play():
    doc = (REPO / "docs" / "mouth.md").read_text()
    assert "never a URL" not in doc
    assert "URL is not played; download it" not in (REPO / "src/mouth/__init__.py").read_text()
    assert "file://" in mouth.play.__doc__ and "https://" in mouth.play.__doc__


#: What the CHANGELOG entry for URL play says, in its ``### Added`` heading or bullet.
URL_PLAY = "`mouth play` takes a URL"
#: The newest release that shipped without URL play: the entry may never sit in it or
#: any release before it.
SHIPPED_WITHOUT = (0, 3, 3)


def _changelog_section_that_announced_url_play(text: str) -> str:
    """The heading of the ``## [...]`` section whose ``### Added`` names URL play.

    Found by what it says, not where it sits (the test_instrumental_docs anchor): the
    oldest such section, so a later entry naming it again cannot move the anchor, and a
    release stamp that empties [Unreleased] into ``## [x.y.z] - date`` leaves it found.
    The anchor must be [Unreleased] or a release newer than SHIPPED_WITHOUT: an entry
    buried in a release that shipped before the feature does not count."""
    sections = re.findall(r"^## (\[[^\]]+\][^\n]*)\n(.*?)(?=^## \[|\Z)", text, re.M | re.S)
    assert sections, "CHANGELOG.md has no ## [...] section"
    for heading, body in reversed(sections):  # oldest first
        for added in re.findall(r"^### Added\b([^\n]*\n.*?)(?=^### |\Z)", body, re.M | re.S):
            if URL_PLAY in added:
                name = heading.split(" - ")[0]
                if name != "[Unreleased]":
                    version = tuple(int(n) for n in name.strip("[]").split("."))
                    assert version > SHIPPED_WITHOUT, (
                        f"{name} announces URL play, but it shipped without it")
                return name
    raise AssertionError(f"no CHANGELOG section's ### Added names {URL_PLAY}")


def test_the_changelog_announces_url_play():
    """Under [Unreleased] on a branch, and in the release it became once the release
    script stamps [Unreleased] as ``[x.y.z] - date`` above it."""
    _changelog_section_that_announced_url_play((REPO / "CHANGELOG.md").read_text())


def _stamp(log: str, version: str) -> str:
    """What scripts/release.py does to CHANGELOG.md at a release."""
    return log.replace("## [Unreleased]", f"## [Unreleased]\n\n## [{version}] - 2030-06-09", 1)


_LOG = ("# Changelog\n\n## [Unreleased]\n\n### Added: `mouth play` takes a URL\n\n"
        "- `mouth play SOURCE` takes a `file://`, `http://` or `https://` URL.\n\n"
        "## [0.3.3] - 2030-06-09\n\n### Fixed\n\n- dummy\n")


def _edit(text: str, find: str, put: str) -> str:
    """``text`` with the first ``find`` made ``put``, failing if ``find`` is absent: a
    replace that matches nothing would hand the assertion an unchanged log and prove
    nothing."""
    assert find in text, f"{find!r} is not in the log this test edits"
    return text.replace(find, put, 1)


def test_the_url_play_anchor_survives_the_release_stamp():
    """[Unreleased] on a branch; the release it became after one stamp, and after the
    next (a newer entry under [Unreleased] does not move it)."""
    assert _changelog_section_that_announced_url_play(_LOG) == "[Unreleased]"
    once = _stamp(_LOG, "0.3.4")
    unreleased = once.split("## [Unreleased]", 1)[1].split("\n## [", 1)[0]
    assert URL_PLAY not in unreleased  # the old test's slice: red on a release commit
    assert _changelog_section_that_announced_url_play(once) == "[0.3.4]"
    later = "## [Unreleased]\n\n### Added\n\n- `mouth play` takes a URL list.\n"
    twice = _edit(_stamp(once, "0.3.5"), "## [Unreleased]\n", later)
    assert _changelog_section_that_announced_url_play(twice) == "[0.3.4]"


def test_the_url_play_anchor_fails_when_the_entry_is_missing_or_too_old():
    """No ### Added naming it fails loudly; nor do a ### Fixed mention or an entry in a
    release that shipped before the feature count."""
    missing = _edit(_LOG, "takes a URL", "takes a path")
    with pytest.raises(AssertionError, match="names"):
        _changelog_section_that_announced_url_play(missing)
    fixed = _edit(_LOG, "### Added:", "### Fixed:")
    with pytest.raises(AssertionError, match="names"):
        _changelog_section_that_announced_url_play(fixed)
    old = _edit(_LOG, "## [0.3.3] - 2030-06-09", "## [0.3.2] - 2030-06-09")
    old = _edit(old, "## [Unreleased]\n\n", "## [Unreleased]\n\n## [0.3.3] - 2030-06-09\n\n")
    with pytest.raises(AssertionError, match="shipped without it"):
        _changelog_section_that_announced_url_play(old)


def test_an_edit_that_matches_nothing_fails_instead_of_proving_nothing():
    """The too-old proof edits _LOG's headings; if a date in _LOG changes and the edit's
    target does not, the edit must fail, not hand back the log unchanged."""
    with pytest.raises(AssertionError, match="is not in the log"):
        _edit(_LOG, "## [0.3.3] - 2030-12-08", "## [0.3.2] - 2030-06-09")
