"""mouth.fetch: SOURCE to a local file (EARS-519 design.md sections 2, 4 and 5).

Offline: every fetch goes through ``httpx.MockTransport`` (AGENTS.md: never a real
server). The transport counts its requests, so a hit is proved to make none and a
refused redirect to never reach the ``file://`` hop."""
import json
import os
import time
from pathlib import Path

import httpx
import pytest

import mouth
from mouth import box, fetch
from tests import env_fence

SONG = b"ID3" + b"\x00" * 997


class Server:
    """A MockTransport with routes: URL -> response (or a callable), and a request log."""

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.seen: list = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(str(request.url))
        r = self.routes.get(str(request.url))
        if r is None:
            return httpx.Response(404)
        return r(request) if callable(r) else r

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler), follow_redirects=False)


def ok(body=SONG, ctype="audio/mpeg", **headers):
    return httpx.Response(200, content=body, headers={"content-type": ctype, **headers})


def moved(to, status=302):
    return httpx.Response(status, headers={"location": to})


@pytest.fixture
def server(monkeypatch):
    s = Server()
    monkeypatch.setattr(fetch, "client", s.client)   # the one injection point
    return s


@pytest.fixture
def d(tmp_path, monkeypatch):
    d = tmp_path / "mouth"
    monkeypatch.setenv("MOUTH_DIR", str(d))
    monkeypatch.setenv("MOUTH_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MOUTH_DEVICE", "null")
    monkeypatch.setenv("MOUTH_BACKEND", "silence")
    env_fence.delenv(monkeypatch, "MOUTH_MAILBOX")
    env_fence.delenv(monkeypatch, "MOUTH_MAIL_LOG")
    env_fence.delenv(monkeypatch, "MOUTH_SOUNDS_DIR")
    box.ensure(box.box_dir(d))
    return d


def leftovers(root: Path) -> list:
    """Every part file and entry under ROOT's .tmp/: none may survive a fetch."""
    tmp = root / fetch.TMP
    return sorted(p.name for p in tmp.iterdir()) if tmp.exists() else []


# -- classify (section 2) -------------------------------------------------------------------

@pytest.mark.parametrize("source", ["/a/song.m4a", "~/song.m4a", "song.m4a", "./a://b",
                                    "file:/a/song.m4a", ""])
def test_a_source_with_no_scheme_at_its_start_is_a_path(source):
    assert fetch.classify(source) == ("path", source)


@pytest.mark.parametrize("url, path", [
    ("file:///abs/song.m4a", "/abs/song.m4a"),
    ("file:///a/My%20Song.m4a", "/a/My Song.m4a"),
    ("FILE:///a/b.wav", "/a/b.wav"),
    ("file://localhost/a/b.wav", "/a/b.wav"),
    ("file://LOCALHOST/a/b.wav", "/a/b.wav"),
    ("file:///a/b.wav?x=1#t=3", "/a/b.wav"),
])
def test_a_file_url_names_the_decoded_local_path(url, path):
    assert fetch.classify(url) == ("file", path)


@pytest.mark.parametrize("url", ["https://", "http:///x.mp3", "https://[::1/x.mp3",
                                 "https://u:s3cret@/x.mp3"])
def test_an_http_url_with_no_usable_host_is_refused_plainly(url):
    with pytest.raises(ValueError, match=r"^mouth: not a usable https?:// URL \(no host\)") as e:
        fetch.classify(url)
    assert "s3cret" not in str(e.value)


@pytest.mark.parametrize("url", ["https://", "https://[::1/x.mp3", "https://h.invalid/\x00.mp3",
                                 "https://u:s3cret@h.invalid/\x00.mp3"])
def test_a_url_httpx_cannot_send_is_the_plain_valueerror_and_asks_nothing(server, tmp_path, url):
    """httpx.InvalidURL is not an httpx.HTTPError: it must not reach the CLI raw."""
    with pytest.raises(ValueError, match=r"^mouth: could not fetch .*(not a valid URL|with a host)") as e:
        fetch.cached(url, tmp_path / "c")
    assert "s3cret" not in str(e.value) and server.seen == []
    _assert_clean(tmp_path / "c", url)


def test_a_file_url_naming_another_host_is_refused():
    with pytest.raises(ValueError, match="file:// URL names this host only"):
        fetch.classify("file://otherbox/a/b.wav")


@pytest.mark.parametrize("url", ["http://h.invalid/s.mp3", "https://h.invalid/s.mp3",
                                 "HTTPS://h.invalid/s.mp3"])
def test_http_and_https_are_remote(url):
    assert fetch.classify(url) == ("remote", url)


@pytest.mark.parametrize("url, scheme", [("ftp://h.invalid/s.mp3", "ftp"),
                                         ("rtsp://h.invalid/live", "rtsp"),
                                         ("data://x", "data"), ("S3://bucket/k", "s3")])
def test_any_other_scheme_is_refused_naming_it(url, scheme):
    with pytest.raises(ValueError, match=rf"a {scheme}:// URL is not played: mouth play takes"):
        fetch.classify(url)


# -- shown: no credentials ------------------------------------------------------------------

def test_shown_drops_the_userinfo_and_keeps_the_query():
    assert fetch.shown("https://u:pw@h.invalid:8443/s.mp3?id=3") == "https://h.invalid:8443/s.mp3?id=3"
    assert fetch.shown("https://h.invalid/s.mp3") == "https://h.invalid/s.mp3"


@pytest.mark.parametrize("url, want", [
    ("https://u:s3cret@[::1/x.mp3", "https://[::1/x.mp3"),             # urlsplit raises on it
    ("https://u:s3cret@/x.mp3", "https:///x.mp3"),
    ("https://a@b:s3cret@h.invalid/x?y=@z", "https://h.invalid/x?y=@z"),  # last @ of the authority
    ("https://h.invalid/x?u=a@b", "https://h.invalid/x?u=a@b"),          # an @ past it stays
])
def test_shown_drops_the_userinfo_even_from_a_url_that_does_not_parse(url, want):
    assert fetch.shown(url) == want


def test_a_refused_unparseable_url_never_shows_its_password():
    with pytest.raises(ValueError) as e:
        fetch.classify("https://u:s3cret@[::1/x.mp3")
    assert "s3cret" not in str(e.value)


# -- the cache: fetch, hit, names (section 4) ----------------------------------------------

def test_a_remote_url_is_fetched_into_its_own_folder_keeping_its_name(server, tmp_path):
    url = "https://h.invalid/x/song.m4a"
    server.routes[url] = ok(ctype="audio/mp4")
    told = []
    f = fetch.cached(url, tmp_path / "c", tell=told.append)
    assert f == tmp_path / "c" / fetch.key(url) / "song.m4a"
    assert f.read_bytes() == SONG
    entry = json.loads((f.parent / fetch.ENTRY).read_text())
    assert entry["url"] == url and entry["size"] == len(SONG) and entry["name"] == "song.m4a"
    assert entry["content_type"] == "audio/mp4"
    assert told == [f"mouth: fetching {url}"]
    assert leftovers(tmp_path / "c") == []


def test_a_second_play_is_a_hit_with_no_request_and_the_file_untouched(server, tmp_path):
    url = "https://h.invalid/song.mp3"
    server.routes[url] = ok()
    first = fetch.cached(url, tmp_path / "c")
    st = first.stat()
    told = []
    again = fetch.cached(url, tmp_path / "c", tell=told.append)
    assert again == first and server.seen == [url] and told == []
    assert (again.stat().st_mtime_ns, again.stat().st_size) == (st.st_mtime_ns, st.st_size)


def test_a_cached_files_sidecar_is_keyed_by_its_bytes_and_found_for_the_second(server, tmp_path, d):
    """design.md section 11: once play() registers it, a cached file's sidecar is
    url-sidecars/<sha256>/, never beside it in <key>/, and a hit finds the same one."""
    import hashlib

    from mouth import sidecar, sourcekey

    url = "https://h.invalid/song.mp3"
    server.routes[url] = ok()
    first = fetch.cached(url, tmp_path / "c")
    sha = sourcekey.register(first, d)
    folder = sidecar.canonical_dir(first)
    assert folder == d / "url-sidecars" / hashlib.sha256(SONG).hexdigest() == d / "url-sidecars" / sha
    assert fetch.cached(url, tmp_path / "c") == first
    assert sourcekey.lookup(first, d) == (sha, True)
    assert sidecar.canonical_dir(first) == folder
    assert sorted(p.name for p in first.parent.iterdir()) == [fetch.ENTRY, "song.mp3"]


def test_two_urls_with_one_last_segment_never_share_a_file(server, tmp_path):
    a, b = "https://h.invalid/a/song.mp3", "https://other.invalid/b/song.mp3"
    server.routes[a], server.routes[b] = ok(b"A" * 10), ok(b"B" * 20)
    fa, fb = fetch.cached(a, tmp_path / "c"), fetch.cached(b, tmp_path / "c")
    assert fa != fb and fa.name == fb.name == "song.mp3"
    assert (fa.read_bytes(), fb.read_bytes()) == (b"A" * 10, b"B" * 20)


def test_the_key_ignores_the_fragment_and_keeps_the_query():
    assert fetch.key("https://h.invalid/s.mp3#t=3") == fetch.key("https://h.invalid/s.mp3")
    assert fetch.key("https://h.invalid/s?id=1") != fetch.key("https://h.invalid/s?id=2")
    assert len(fetch.key("https://h.invalid/s")) == 24


def test_a_redirect_is_followed_and_cached_under_the_given_url(server, tmp_path):
    short, real = "https://h.invalid/go/42", "https://cdn.invalid/files/track.flac"
    server.routes[short] = moved(real)
    server.routes[real] = ok(ctype="audio/flac")
    f = fetch.cached(short, tmp_path / "c")
    assert f.parent.name == fetch.key(short) and f.name == "track.flac"
    assert json.loads((f.parent / fetch.ENTRY).read_text())["final_url"] == real
    server.seen.clear()
    assert fetch.cached(short, tmp_path / "c") == f and server.seen == []


@pytest.mark.parametrize("url, ctype, name", [
    ("https://h.invalid/stream", "audio/mpeg", "stream.mp3"),
    ("https://h.invalid/stream", "application/octet-stream", "stream"),
    ("https://h.invalid/", "audio/ogg", "audio.ogg"),
    ("https://h.invalid/", "", "audio"),
    ("https://h.invalid/a/My%20Song.wav", "audio/wav", "My Song.wav"),
    ("https://h.invalid/a/..hidden.mp3", "audio/mpeg", "hidden.mp3"),
    ("https://h.invalid/a/x%2Fy%5Cz.mp3", "audio/mpeg", "x_y_z.mp3"),
])
def test_the_name_falls_back_to_the_content_type_then_audio(server, tmp_path, url, ctype, name):
    server.routes[url] = ok(ctype=ctype) if ctype else httpx.Response(200, content=SONG)
    assert fetch.cached(url, tmp_path / "c").name == name


@pytest.mark.parametrize("url, name", [
    ("https://h.invalid/a/%20.mp3", "mp3.mp3"),            # was ".mp3.mp3": hidden, never a hit
    ("https://h.invalid/a/.%20.song.mp3", "song.mp3"),
    ("https://h.invalid/a/%20..", "audio.mp3"),            # was "...mp3"; with no type, ".."
    ("https://h.invalid/a/%09.x.mp3%20", "x.mp3"),
])
def test_a_name_is_never_hidden_even_behind_a_blank(server, tmp_path, url, name):
    server.routes[url] = ok()
    root = tmp_path / "c"
    assert fetch.cached(url, root).name == name
    assert fetch.cached(url, root).name == name
    assert server.seen == [url]                            # the second play is a hit


def test_a_dot_dot_name_never_reaches_the_folder_above(server, tmp_path):
    url = "https://h.invalid/a/%20.."
    server.routes[url] = httpx.Response(200, content=SONG)  # no type: the segment as it is
    p = fetch.cached(url, tmp_path / "c")
    assert p.name == "audio" and p.parent.name == fetch.key(url)


def test_a_long_name_is_cut_keeping_its_extension():
    name = fetch.name_for("https://h.invalid/" + "x" * 300 + ".mp3", "", "")
    assert len(name) == fetch.NAME_MAX and name.endswith(".mp3")


def test_a_long_cjk_name_is_cut_to_the_byte_limit_on_a_character_boundary(server, tmp_path):
    from urllib.parse import quote

    stem = "\u6b4c" * 100                     # 3 bytes each in UTF-8: 300 bytes
    url = "https://h.invalid/" + quote(stem + ".flac")
    server.routes[url] = ok(ctype="audio/flac")
    f = fetch.cached(url, tmp_path / "c")
    raw = f.name.encode("utf-8")
    assert f.name.endswith(".flac") and len(raw) <= fetch.NAME_MAX
    assert f.name == "\u6b4c" * ((fetch.NAME_MAX - 5) // 3) + ".flac"   # 38 whole characters
    assert fetch.cached(url, tmp_path / "c") == f and server.seen == [url]


def test_a_long_cjk_name_given_its_extension_by_the_content_type_is_cut_too():
    name = fetch.name_for("https://h.invalid/" + "\u97f3" * 80, "", "audio/mpeg")
    assert name.endswith(".mp3") and len(name.encode("utf-8")) <= fetch.NAME_MAX
    assert name == "\u97f3" * ((fetch.NAME_MAX - 4) // 3) + ".mp3"


def test_a_cut_never_splits_a_character():
    for n in range(1, 4):                      # every offset of a 3-byte character
        name = fetch._fit("x" * n + "\u6b4c" * 60, ".mp3")
        assert len(name.encode("utf-8")) <= fetch.NAME_MAX
        name.encode("utf-8").decode("utf-8")   # whole characters only


def test_a_client_that_follows_redirects_itself_is_still_checked_hop_by_hop(monkeypatch, tmp_path):
    """A4: fetch passes follow_redirects=False to send, so even a client built to follow
    (an injected one) never sends the file:// hop."""
    s = Server({"https://h.invalid/s.mp3": moved("file:///etc/passwd"),
                "file:///etc/passwd": ok(b"root:x:0:0")})
    monkeypatch.setattr(fetch, "client", lambda: httpx.Client(
        transport=httpx.MockTransport(s.handler), follow_redirects=True))
    with pytest.raises(ValueError, match="redirected to a file:// URL"):
        fetch.cached("https://h.invalid/s.mp3", tmp_path / "c")
    assert s.seen == ["https://h.invalid/s.mp3"]


# -- failures: plain, nothing left (section 4) ---------------------------------------------

def _assert_clean(root: Path, url: str):
    assert leftovers(root) == []
    assert not (root / fetch.key(url) / fetch.ENTRY).exists()


def test_a_404_says_so_and_leaves_nothing(server, tmp_path):
    url = "https://h.invalid/gone.mp3"
    with pytest.raises(ValueError, match=r"mouth: could not fetch https://h.invalid/gone.mp3: HTTP 404"):
        fetch.cached(url, tmp_path / "c")
    _assert_clean(tmp_path / "c", url)


def test_a_timeout_says_so_and_leaves_nothing(server, tmp_path):
    url = "https://h.invalid/slow.mp3"

    def slow(request):
        raise httpx.ReadTimeout("read timed out", request=request)

    server.routes[url] = slow
    with pytest.raises(ValueError, match="could not fetch .*: timed out"):
        fetch.cached(url, tmp_path / "c")
    _assert_clean(tmp_path / "c", url)


def test_a_connect_error_says_so_and_leaves_nothing(server, tmp_path):
    url = "https://h.invalid/s.mp3"

    def down(request):
        raise httpx.ConnectError("connection refused", request=request)

    server.routes[url] = down
    with pytest.raises(ValueError, match=r"connection failed \(connection refused\)"):
        fetch.cached(url, tmp_path / "c")
    _assert_clean(tmp_path / "c", url)


def test_a_body_that_breaks_off_leaves_no_part_file_and_a_retry_fetches(server, tmp_path):
    url = "https://h.invalid/s.mp3"

    class Broken(httpx.SyncByteStream):
        def __iter__(self):
            yield b"half"
            raise httpx.RemoteProtocolError("peer closed connection")

    server.routes[url] = lambda request: httpx.Response(
        200, headers={"content-type": "audio/mpeg"}, stream=Broken())
    with pytest.raises(ValueError, match="could not fetch .*RemoteProtocolError"):
        fetch.cached(url, tmp_path / "c")
    _assert_clean(tmp_path / "c", url)
    server.routes[url] = ok()
    assert fetch.cached(url, tmp_path / "c").read_bytes() == SONG


@pytest.mark.parametrize("ctype", ["text/html; charset=utf-8", "text/plain", "application/json"])
def test_a_web_page_is_refused_as_not_a_sound_file(server, tmp_path, ctype):
    url = "https://h.invalid/watch"
    server.routes[url] = ok(b"<html>", ctype=ctype)
    with pytest.raises(ValueError, match=r"not a sound file \(" + ctype.split(";")[0]):
        fetch.cached(url, tmp_path / "c")
    _assert_clean(tmp_path / "c", url)


def test_the_size_cap_refuses_by_content_length_before_the_body(server, tmp_path):
    url = "https://h.invalid/radio"
    server.routes[url] = ok(b"x" * 64, **{"content-length": str(10 ** 12)})
    with pytest.raises(ValueError, match="larger than 2 GB"):
        fetch.cached(url, tmp_path / "c")
    _assert_clean(tmp_path / "c", url)


def test_the_size_cap_refuses_a_stream_that_runs_past_it(server, tmp_path):
    url = "https://h.invalid/radio"

    class Endless(httpx.SyncByteStream):
        def __iter__(self):
            while True:
                yield b"x" * 100

    server.routes[url] = lambda request: httpx.Response(
        200, headers={"content-type": "audio/mpeg"}, stream=Endless())
    with pytest.raises(ValueError, match="larger than"):
        fetch.cached(url, tmp_path / "c", max_bytes=1000)
    _assert_clean(tmp_path / "c", url)


# -- redirects followed by hand (section 4, review F2 as corrected at r2) -----------------

def test_a_redirect_to_a_file_url_is_refused_before_it_is_requested(server, tmp_path):
    url = "https://h.invalid/s.mp3"
    server.routes[url] = moved("file:///etc/passwd")
    server.routes["file:///etc/passwd"] = ok(b"root:x:0:0")
    with pytest.raises(ValueError, match=r"redirected to a file:// URL"):
        fetch.cached(url, tmp_path / "c")
    assert server.seen == [url], "the file:// hop was never asked for"
    _assert_clean(tmp_path / "c", url)


def test_an_eleventh_redirect_is_too_many(server, tmp_path):
    for i in range(12):
        server.routes[f"https://h.invalid/{i}"] = moved(f"https://h.invalid/{i + 1}")
    with pytest.raises(ValueError, match="could not fetch https://h.invalid/0: too many redirects"):
        fetch.cached("https://h.invalid/0", tmp_path / "c")
    assert len(server.seen) == fetch.MAX_REDIRECTS + 1
    _assert_clean(tmp_path / "c", "https://h.invalid/0")


def test_any_other_httpx_error_is_the_plain_valueerror(server, tmp_path):
    url = "https://h.invalid/s.mp3"

    def odd(request):
        raise httpx.UnsupportedProtocol("no", request=request)

    server.routes[url] = odd
    with pytest.raises(ValueError, match="could not fetch .*UnsupportedProtocol"):
        fetch.cached(url, tmp_path / "c")


# -- credentials never shown (review F3) ---------------------------------------------------

def test_credentials_never_show_in_the_error_or_the_stderr_line(server, tmp_path):
    url = "https://user:s3cret@h.invalid/s.mp3"
    told = []
    with pytest.raises(ValueError) as e:
        fetch.cached(url, tmp_path / "c", tell=told.append)
    assert "s3cret" not in str(e.value) and "h.invalid/s.mp3" in str(e.value)
    assert told == ["mouth: fetching https://h.invalid/s.mp3"]


def test_credentials_in_an_httpx_message_are_scrubbed(server, tmp_path):
    url = "https://user:s3cret@h.invalid/s.mp3"

    def down(request):
        raise httpx.ConnectError(f"cannot reach {url}", request=request)

    server.routes[url] = down
    with pytest.raises(ValueError) as e:
        fetch.cached(url, tmp_path / "c")
    assert "s3cret" not in str(e.value)


# -- expiry, and a re-fetch under a new name -----------------------------------------------

def test_an_entry_older_than_the_age_is_fetched_again(server, tmp_path):
    url = "https://h.invalid/song.mp3"
    server.routes[url] = ok(b"old")
    f = fetch.cached(url, tmp_path / "c", max_age_s=60)
    assert fetch.cached(url, tmp_path / "c", max_age_s=60, now=time.time() + 30) == f
    assert server.seen == [url]
    server.routes[url] = ok(b"new!")
    g = fetch.cached(url, tmp_path / "c", max_age_s=60, now=time.time() + 61)
    assert g == f and g.read_bytes() == b"new!" and server.seen == [url, url]


def test_no_age_keeps_an_entry_forever(server, tmp_path):
    url = "https://h.invalid/song.mp3"
    server.routes[url] = ok()
    f = fetch.cached(url, tmp_path / "c")
    assert fetch.cached(url, tmp_path / "c", now=time.time() + 10 * 365 * 86400) == f
    assert server.seen == [url]


def test_a_torn_copy_or_a_missing_entry_is_a_miss(server, tmp_path):
    url = "https://h.invalid/song.mp3"
    server.routes[url] = ok()
    f = fetch.cached(url, tmp_path / "c")
    f.write_bytes(b"torn")
    assert fetch.cached(url, tmp_path / "c").read_bytes() == SONG
    (f.parent / fetch.ENTRY).unlink()
    fetch.cached(url, tmp_path / "c")
    assert len(server.seen) == 3


def test_a_refetch_under_a_new_name_leaves_one_file_in_its_folder(server, tmp_path):
    url = "https://h.invalid/stream"
    server.routes[url] = ok(ctype="audio/mpeg")
    first = fetch.cached(url, tmp_path / "c", max_age_s=60)
    (first.parent / ".stream.mp3.d").mkdir()          # its sidecar: left, stale
    server.routes[url] = ok(ctype="audio/ogg")
    second = fetch.cached(url, tmp_path / "c", max_age_s=60, now=time.time() + 120)
    assert second.name == "stream.ogg"
    assert sorted(p.name for p in second.parent.iterdir()) == [
        fetch.ENTRY, ".stream.mp3.d", "stream.ogg"]


# -- cache off: the spool, and its sweep (section 5) ---------------------------------------

def test_cache_off_fetches_every_time_into_the_spool_and_never_makes_the_cache_dir(server, d):
    url = "https://h.invalid/song.mp3"
    server.routes[url] = ok()
    a = fetch.spooled(url, fetch.spool_dir(d))
    b = fetch.spooled(url, fetch.spool_dir(d))
    assert a != b and a.name == b.name == "song.mp3" and a.read_bytes() == SONG
    assert a.parent.parent == fetch.spool_dir(d) and server.seen == [url, url]
    assert not fetch.cache_dir(d).exists()
    assert leftovers(fetch.spool_dir(d)) == []


def test_a_failed_spool_fetch_leaves_no_folder(server, d):
    with pytest.raises(ValueError, match="HTTP 404"):
        fetch.spooled("https://h.invalid/gone.mp3", fetch.spool_dir(d))
    assert [p.name for p in fetch.spool_dir(d).iterdir()] == [fetch.TMP]
    assert leftovers(fetch.spool_dir(d)) == []


def _age(p: Path, s: float):
    t = time.time() - s
    os.utime(p, (t, t))


def _spool(server, d, name):
    url = f"https://h.invalid/{name}"
    server.routes[url] = ok()
    f = fetch.spooled(url, fetch.spool_dir(d))
    _age(f.parent, fetch.SPOOL_KEEP_S + 60)
    return f


def test_the_sweep_removes_an_old_unnamed_folder_and_keeps_a_young_one(server, d):
    old = _spool(server, d, "old.mp3")
    (old.parent / ".old.mp3.d").mkdir()       # a sidecar goes with its folder
    _age(old.parent, fetch.SPOOL_KEEP_S + 60)
    young_url = "https://h.invalid/young.mp3"
    server.routes[young_url] = ok()
    young = fetch.spooled(young_url, fetch.spool_dir(d))
    stale_part = fetch.spool_dir(d) / fetch.TMP / "dead.part"
    stale_part.write_bytes(b"x")
    _age(stale_part, fetch.SPOOL_KEEP_S + 60)
    gone = fetch.sweep_spool(d)
    assert sorted(gone) == sorted([old.parent, stale_part])
    assert not old.parent.exists() and young.exists()
    assert (fetch.spool_dir(d) / fetch.TMP).is_dir()


def test_the_sweep_skips_a_folder_a_queued_line_names(server, d):
    f = _spool(server, d, "queued.mp3")
    mouth.play(str(f), d=d, spawn=False)
    assert [x["file"] for x in box.Queue(d).lines()] == [str(f.resolve())]
    _age(f.parent, fetch.SPOOL_KEEP_S + 60)
    assert fetch.sweep_spool(d) == [] and f.exists()


def test_the_sweep_skips_the_line_playing_now(server, d):
    f = _spool(server, d, "playing.mp3")
    (d / "playing.json").write_text(json.dumps({"utt": "u1", "file": str(f)}))
    assert fetch.sweep_spool(d) == [] and f.exists()


def test_the_sweep_skips_a_live_bed(server, d):
    f = _spool(server, d, "bed.mp3")
    (d / "bed.json").write_text(json.dumps({"default": {"utt": "u2", "file": str(f)}}))
    assert fetch.sweep_spool(d) == [] and f.exists()
    (d / "bed.json").write_text(json.dumps({}))
    assert fetch.sweep_spool(d) == [f.parent] and not f.exists()


def test_the_sweep_sweeps_nothing_when_playing_json_is_unreadable(server, d):
    f = _spool(server, d, "x.mp3")
    (d / "playing.json").write_text("{torn")
    assert fetch.sweep_spool(d) == [] and f.exists()


def test_the_sweep_of_no_spool_is_quiet(d):
    assert fetch.sweep_spool(d) == []


# -- the default dirs live under the mouth dir --------------------------------------------

def test_the_cache_and_spool_live_under_the_mouth_dir(d):
    assert fetch.cache_dir(d) == d / "play-cache"
    assert fetch.spool_dir(d) == d / "play-spool"


def test_the_real_client_follows_no_redirect_itself():
    c = fetch.client()
    try:
        assert c.follow_redirects is False
        assert (c.timeout.connect, c.timeout.read) == (fetch.CONNECT_S, fetch.READ_S)
    finally:
        c.close()
