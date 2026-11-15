"""``mouth play --find WORDS``: the CLI that asks the host's finders and plays what
they found (``mouth.__main__._play_find``).

- ``--find`` replaces SOURCE: one or the other, never both, never neither.
  ``--finder``, ``--matches`` and ``--pick`` go with ``--find`` only, and ``--pick``
  and ``--matches`` are mutually exclusive.
- With no finder in the host file: one line naming ``play_finders`` and the host
  ``mouth.json``, exit 2. Not found: exit 1, naming the finders asked. A finder that
  fails or times out: exit 2, naming it.
- The match mode: ``--pick N``, else ``--matches``, else ``play_find_matches``
  (layered and lockable), else ``first``. ``list`` prints numbered labels, plays
  nothing and exits 3, even for one match. ``all`` queues every match, in the
  finder's order, as one run; a match that cannot be prepared is one line, skipped,
  and only a run with none left exits 2. ``--wait`` with a run is refused before
  any finder runs.
- After the find the path is today's: a ``file://`` URL plays in place, and an
  http(s) URL obeys ``play_remote`` and ``play_cache``.

Every finder is ``tests/fixtures/fake_finder.py``; the player is never started
(``ensure_player`` is faked), so what was played is read off the queue itself.
"""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import httpx
import pytest

import mouth
from mouth import __main__ as cli
from mouth import box, fetch, voices
from mouth import instrumental as _instrumental
from mouth import split as _split
from tests import env_fence

SID = "test-sid-play-find"
FAKE = Path(__file__).resolve().parents[1] / "fixtures" / "fake_finder.py"
SONG = b"RIFF" + b"\0" * 64
URL = "https://music.invalid/albums/song.m4a"


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
    music = tmp_path / "music"
    music.mkdir()
    songs = {}
    for name in ("no-scrubs.flac", "no-scrubs.mp3", "creep.flac"):
        (music / name).write_bytes(SONG)
        songs[name] = (music / name).resolve()
    h = {"d": d, "host": host, "tmp": tmp_path, "songs": songs, "requests": []}

    def handler(request: httpx.Request) -> httpx.Response:
        h["requests"].append(str(request.url))
        return httpx.Response(200, content=SONG, headers={"content-type": "audio/mp4"})

    monkeypatch.setattr(fetch, "client", lambda: httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=False))
    monkeypatch.setattr(_instrumental, "probe_duration", lambda path, *a, **kw: 200.0)
    monkeypatch.setattr(_split, "spawn", lambda source, d=None: None)
    monkeypatch.setattr(mouth, "ensure_player", lambda d=None: False)
    return h


def entry(name: str, *opts: str) -> dict:
    """A ``play_finders`` entry running the fake with OPTS; the words go after ``--``."""
    return {"name": name, "argv": [sys.executable, str(FAKE), *opts, "--"]}


def prints(*lines: str) -> list[str]:
    return [o for ln in lines for o in ("--print", ln)]


def host(h, *finders, **kw):
    """The test host's mouth.json: FINDERS and KW. ``music_instrumental`` is off unless
    KW sets it, so no "plays with its vocals" line rides on stderr."""
    h["host"].write_text(json.dumps({"play_finders": list(finders),
                                     "music_instrumental": False, **kw}))


def uri(h, name: str) -> str:
    return h["songs"][name].as_uri()


def queued(h) -> list:
    return [box.public(x) for x in box.Queue(h["d"]).lines()]


def files(h) -> list:
    return [x["file"] for x in queued(h)]


def logged(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text().splitlines()] if path.exists() else []


def run(capsys, *args) -> tuple[int, str, str]:
    rc = cli.main(["play", *args])
    c = capsys.readouterr()
    return rc, c.out, c.err


def two_matches(h, **kw):
    """One finder, ``music``, finding the two copies of No Scrubs, flac first."""
    host(h, entry("music", *prints(f"No Scrubs\tTLC\t{uri(h, 'no-scrubs.flac')}",
                                   f"No Scrubs\tTLC\t{uri(h, 'no-scrubs.mp3')}")), **kw)


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
    def boom():
        raise AssertionError("no client may be built for this play")
    monkeypatch.setattr(fetch, "client", boom)


# -- the outcomes and their exit codes -----------------------------------------------------

def test_no_finder_is_one_line_naming_the_setting_and_the_host_file(home, capsys):
    rc, out, err = run(capsys, "--find", "no scrubs")
    assert rc == 2 and out == ""
    assert err == f"mouth: no finder: set play_finders in the host's mouth.json ({home['host']})\n"
    assert queued(home) == []


def test_found_plays_the_match_and_prints_its_utt(home, capsys, monkeypatch):
    no_client(monkeypatch)
    host(home, entry("music", *prints(f"No Scrubs\tTLC\t{uri(home, 'no-scrubs.flac')}")))
    rc, out, err = run(capsys, "--find", "no scrubs")
    assert rc == 0
    assert out.strip() == queued(home)[0]["utt"]
    assert files(home) == [str(home["songs"]["no-scrubs.flac"])]   # file:// plays in place


def test_not_found_exits_1_naming_every_finder_asked(home, capsys):
    host(home, entry("local", "--exit", "1"), entry("remote", "--exit", "1"))
    rc, out, err = run(capsys, "--find", "no  scrubs")
    assert (rc, out) == (1, "")
    assert err == "mouth: not found: no scrubs (asked: local, remote)\n"
    assert queued(home) == []


def test_a_finder_error_exits_2_naming_it(home, capsys):
    host(home, entry("local", "--exit", "4", "--stderr", "index locked"),
         entry("remote", *prints(f"x\t{uri(home, 'creep.flac')}")))
    rc, out, err = run(capsys, "--find", "creep")
    assert (rc, out) == (2, "")
    assert err == "mouth: finder local failed: exit 4: index locked\n"
    assert queued(home) == []   # the chain stopped: remote never answered


def test_a_finder_timeout_exits_2_naming_it(home, capsys):
    host(home, entry("slow", *prints(f"x\t{uri(home, 'creep.flac')}"), "--sleep", "30"),
         play_finder_timeout_s=0.5)
    rc, out, err = run(capsys, "--find", "creep")
    assert (rc, out) == (2, "")
    assert err == "mouth: finder slow failed: timed out after 0.5s\n"
    assert queued(home) == []


def test_a_line_with_no_url_exits_2(home, capsys):
    host(home, entry("music", *prints(f"a\t{uri(home, 'creep.flac')}", "b\t/music/b.flac")))
    rc, _, err = run(capsys, "--find", "creep")
    assert rc == 2 and err == "mouth: finder music failed: printed a line with no URL (line 2)\n"
    assert queued(home) == []


def test_empty_words_are_refused(home, capsys):
    host(home, entry("music", *prints(f"a\t{uri(home, 'creep.flac')}")))
    rc, _, err = run(capsys, "--find", "   ")
    assert rc == 2 and err == "mouth: --find needs words\n"


# -- the finders: order and --finder ---------------------------------------------------------

def test_finder_name_asks_that_one_only(home, capsys, tmp_path):
    log = tmp_path / "a.log"
    host(home, entry("a", "--log", str(log), *prints(f"A\t{uri(home, 'no-scrubs.flac')}")),
         entry("b", *prints(f"B\t{uri(home, 'creep.flac')}")))
    rc, _, _ = run(capsys, "--find", "x", "--finder", "b")
    assert rc == 0
    assert files(home) == [str(home["songs"]["creep.flac"])]
    assert logged(log) == []


def test_an_unknown_finder_name_is_refused(home, capsys):
    host(home, entry("a", "--exit", "1"), entry("b", "--exit", "1"))
    rc, _, err = run(capsys, "--find", "x", "--finder", "c")
    assert rc == 2 and err == "mouth: no finder named c (have: a, b)\n"


def test_the_first_finder_that_finds_wins(home, capsys):
    host(home, entry("local", "--exit", "1"),
         entry("remote", *prints(f"R\t{uri(home, 'creep.flac')}")))
    assert run(capsys, "--find", "creep")[0] == 0
    assert files(home) == [str(home["songs"]["creep.flac"])]


# -- the match modes ---------------------------------------------------------------------

def test_first_is_the_default_and_plays_match_one(home, capsys):
    two_matches(home)
    rc, out, _ = run(capsys, "--find", "no scrubs")
    assert rc == 0 and len(out.split()) == 1
    assert files(home) == [str(home["songs"]["no-scrubs.flac"])]


def test_list_prints_numbered_labels_and_plays_nothing(home, capsys):
    two_matches(home)
    rc, out, err = run(capsys, "--find", "no scrubs", "--matches", "list")
    assert (rc, err) == (3, "")
    assert out == "1  No Scrubs | TLC\n2  No Scrubs | TLC\n"
    assert queued(home) == []


def test_list_with_one_match_still_lists_and_plays_nothing(home, capsys):
    host(home, entry("music", *prints(f"Creep\tTLC\t{uri(home, 'creep.flac')}")))
    rc, out, _ = run(capsys, "--find", "creep", "--matches", "list")
    assert (rc, out) == (3, "1  Creep | TLC\n")
    assert queued(home) == []


def test_list_labels_a_bare_url_line_with_its_url(home, capsys):
    host(home, entry("music", *prints(uri(home, "creep.flac"))))
    rc, out, _ = run(capsys, "--find", "creep", "--matches", "list")
    assert (rc, out) == (3, f"1  {uri(home, 'creep.flac')}\n")


def test_pick_plays_the_nth(home, capsys):
    two_matches(home)
    assert run(capsys, "--find", "no scrubs", "--pick", "2")[0] == 0
    assert files(home) == [str(home["songs"]["no-scrubs.mp3"])]


def test_pick_out_of_range_is_refused(home, capsys):
    two_matches(home)
    rc, out, err = run(capsys, "--find", "no scrubs", "--pick", "3")
    assert (rc, out) == (2, "")
    assert err == "mouth play: --pick 3: only 2 matches\n"
    assert queued(home) == []


def test_pick_out_of_range_of_one_match_says_match(home, capsys):
    host(home, entry("music", *prints(f"a\t{uri(home, 'creep.flac')}")))
    rc, _, err = run(capsys, "--find", "creep", "--pick", "2")
    assert rc == 2 and err == "mouth play: --pick 2: only 1 match\n"


@pytest.mark.parametrize("bad", ["0", "-1", "two"])
def test_pick_takes_a_whole_number_from_1(home, capsys, bad):
    with pytest.raises(SystemExit) as e:
        cli.main(["play", "--find", "x", "--pick", bad])
    assert e.value.code == 2
    assert "is not a match number" in capsys.readouterr().err


def test_pick_with_matches_is_refused(home, capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["play", "--find", "x", "--pick", "1", "--matches", "first"])
    assert e.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


def test_all_queues_every_match_in_order_as_one_run(home, capsys):
    host(home, entry("music", *prints(f"a\t{uri(home, 'no-scrubs.flac')}",
                                      f"b\t{uri(home, 'creep.flac')}",
                                      f"c\t{uri(home, 'no-scrubs.mp3')}")))
    rc, out, err = run(capsys, "--find", "tlc", "--matches", "all")
    assert (rc, err) == (0, "")
    s = home["songs"]
    assert files(home) == [str(s["no-scrubs.flac"]), str(s["creep.flac"]), str(s["no-scrubs.mp3"])]
    assert out.split() == [x["utt"] for x in queued(home)]
    assert len({x["_rank"] for x in box.Queue(home["d"]).lines()}) == 1   # one block


def test_all_skips_a_match_that_fails_with_one_line(home, capsys):
    host(home, entry("music", *prints(f"a\t{uri(home, 'no-scrubs.flac')}",
                                      "Gone\tTLC\tfile:///nowhere/gone.flac",
                                      f"c\t{uri(home, 'creep.flac')}")))
    rc, out, err = run(capsys, "--find", "tlc", "--matches", "all")
    assert rc == 0
    assert err == "mouth play: skipped Gone | TLC: no such file /nowhere/gone.flac\n"
    assert files(home) == [str(home["songs"]["no-scrubs.flac"]), str(home["songs"]["creep.flac"])]
    assert len(out.split()) == 2


def test_all_with_none_queued_exits_2(home, capsys):
    host(home, entry("music", *prints("a\tfile:///nowhere/a.flac", "b\tfile:///nowhere/b.flac")))
    rc, out, err = run(capsys, "--find", "tlc", "--matches", "all")
    assert (rc, out) == (2, "")
    assert err.splitlines() == ["mouth play: skipped a: no such file /nowhere/a.flac",
                                "mouth play: skipped b: no such file /nowhere/b.flac",
                                "mouth: nothing to play"]
    assert queued(home) == []


def test_all_with_one_match_plays_it(home, capsys):
    host(home, entry("music", *prints(f"a\t{uri(home, 'creep.flac')}")))
    assert run(capsys, "--find", "creep", "--matches", "all")[0] == 0
    assert files(home) == [str(home["songs"]["creep.flac"])]


def test_all_takes_the_queue_mode_as_one_block(home, capsys):
    mouth.play(str(home["songs"]["creep.flac"]), spawn=False, d=home["d"])   # waiting at 50
    two_matches(home)
    assert run(capsys, "--find", "no scrubs", "--matches", "all", "--next")[0] == 0
    s = home["songs"]
    assert files(home) == [str(s["no-scrubs.flac"]), str(s["no-scrubs.mp3"]), str(s["creep.flac"])]


# -- the setting -------------------------------------------------------------------------

def test_the_setting_answers_when_no_flag_does(home, capsys):
    two_matches(home, play_find_matches="list")
    assert run(capsys, "--find", "no scrubs")[0] == 3
    assert queued(home) == []


def test_matches_beats_the_setting(home, capsys):
    two_matches(home, play_find_matches="list")
    assert run(capsys, "--find", "no scrubs", "--matches", "first")[0] == 0
    assert files(home) == [str(home["songs"]["no-scrubs.flac"])]


def test_pick_beats_the_setting(home, capsys):
    two_matches(home, play_find_matches="list")
    assert run(capsys, "--find", "no scrubs", "--pick", "2")[0] == 0
    assert files(home) == [str(home["songs"]["no-scrubs.mp3"])]


def test_the_setting_all_queues_a_run(home, capsys):
    two_matches(home, play_find_matches="all")
    assert run(capsys, "--find", "no scrubs")[0] == 0
    assert len(files(home)) == 2


def test_a_session_layer_answers_over_the_host(home, capsys, monkeypatch):
    two_matches(home, play_find_matches="first")
    fake_session(home, monkeypatch, play_find_matches="list")
    assert run(capsys, "--find", "no scrubs")[0] == 3


def test_a_host_lock_beats_the_session_layer(home, capsys, monkeypatch):
    two_matches(home, play_find_matches="first", _locked=["play_find_matches"])
    fake_session(home, monkeypatch, locked=("play_find_matches",), play_find_matches="list")
    assert run(capsys, "--find", "no scrubs")[0] == 0
    assert len(files(home)) == 1


def test_the_setting_is_layered_and_lockable(home):
    host(home, play_find_matches="all")
    seat = {"play_find_matches": ("list", "seat")}
    assert voices.play_mode_settings(seat, home["d"])["play_find_matches"] == ("list", "seat")
    assert voices.play_mode_settings({"play_find_matches": ("bad", "seat")}, home["d"]
                                     )["play_find_matches"] == ("all", "settings")
    host(home, play_find_matches="all", _locked=["play_find_matches"])
    assert voices.play_mode_settings(seat, home["d"])["play_find_matches"] == ("all", "settings")
    home["host"].write_text("{}")
    assert voices.play_mode_settings({}, home["d"])["play_find_matches"] == ("first", "builtin")


def test_no_layer_is_read_when_flags_answer_both_modes(home, capsys, monkeypatch):
    asked = []
    monkeypatch.setattr(voices, "cascade", lambda sid, keys: asked.append(tuple(keys)) or {})
    two_matches(home)
    assert run(capsys, "--find", "no scrubs", "--matches", "first", "--last")[0] == 0
    assert voices.PLAY_MODE_KEYS not in asked


def session_runs(h, monkeypatch) -> list:
    """The order of the ``session`` runs and the finds: ``"session"`` for each run of
    the fake ``session`` binary, ``"finder"`` for each ask of the finders."""
    import subprocess

    from mouth import finder

    events = []
    real_run, real_find = subprocess.run, finder.find
    exe = str(h["tmp"] / "fake-session-session")

    def run_(args, *a, **kw):
        if args and str(args[0]) == exe:
            events.append("session")
        return real_run(args, *a, **kw)

    monkeypatch.setattr(subprocess, "run", run_)
    monkeypatch.setattr(finder, "find", lambda *a, **kw: events.append("finder") or real_find(*a, **kw))
    return events


def test_a_plain_find_makes_one_session_run_before_any_finder(home, capsys, monkeypatch):
    """ONE run, read up front, serves the match mode, the queue mode and the line."""
    two_matches(home)
    fake_session(home, monkeypatch, play_find_matches="first", play_queue="next", play_volume=40,
                 music_duck=False)
    events = session_runs(home, monkeypatch)
    assert run(capsys, "--find", "no scrubs")[0] == 0
    assert events == ["session", "finder"]
    (x,) = queued(home)
    assert x["file"] == str(home["songs"]["no-scrubs.flac"])
    assert (x["queue"], x["queue_from"]) == ("next", "session")
    assert (x["volume"], x["volume_from"]) == (40, "session")
    assert x["duck"]["on"] is False


def test_a_find_run_under_all_is_one_session_run(home, capsys, monkeypatch):
    two_matches(home)
    fake_session(home, monkeypatch, play_find_matches="all", play_volume=40)
    events = session_runs(home, monkeypatch)
    assert run(capsys, "--find", "no scrubs")[0] == 0
    assert events == ["session", "finder"]
    assert [x["volume"] for x in queued(home)] == [40, 40]


def test_a_find_that_lists_is_one_session_run(home, capsys, monkeypatch):
    two_matches(home)
    fake_session(home, monkeypatch, play_find_matches="list")
    events = session_runs(home, monkeypatch)
    assert run(capsys, "--find", "no scrubs")[0] == 3
    assert events == ["session", "finder"]


@pytest.mark.parametrize("flags, keys", [
    ([], voices.play_keys(queue=True, matches=True)),
    (["--matches", "all"], voices.play_keys(queue=True)),
    (["--pick", "2", "--now"], voices.LINE_KEYS),
    (["--matches", "first", "--last"], voices.LINE_KEYS),
])
def test_the_one_run_asks_for_the_modes_no_flag_answers(home, capsys, monkeypatch, flags, keys):
    asked = []
    monkeypatch.setattr(voices, "cascade", lambda sid, k: asked.append(tuple(k)) or {})
    two_matches(home)
    assert run(capsys, "--find", "no scrubs", *flags)[0] == 0
    assert asked == [keys]


def test_find_matches_refuses_a_bad_word():
    with pytest.raises(ValueError, match="matches is first, list, all"):
        mouth.find_matches("some")
    assert mouth.find_matches(" ALL ") == ("all", "flag")


# -- --wait with a run ---------------------------------------------------------------------

def test_wait_with_matches_all_is_refused_before_the_finder_runs(home, capsys, tmp_path):
    log = tmp_path / "f.log"
    host(home, entry("music", "--log", str(log), *prints(f"a\t{uri(home, 'creep.flac')}")))
    rc, out, err = run(capsys, "--find", "creep", "--matches", "all", "--wait")
    assert (rc, out) == (2, "")
    assert err == "mouth play: --wait plays one: use --pick N or --matches first\n"
    assert logged(log) == [] and queued(home) == []


def test_wait_with_the_setting_all_is_refused_before_the_finder_runs(home, capsys, tmp_path):
    log = tmp_path / "f.log"
    host(home, entry("music", "--log", str(log), *prints(f"a\t{uri(home, 'creep.flac')}")),
         play_find_matches="all")
    rc, _, err = run(capsys, "--find", "creep", "--wait")
    assert rc == 2 and "--wait plays one" in err
    assert logged(log) == []


# -- SOURCE and --find -----------------------------------------------------------------------

def test_source_and_find_together_are_refused(home, capsys):
    host(home, entry("music", *prints(f"a\t{uri(home, 'creep.flac')}")))
    rc, _, err = run(capsys, str(home["songs"]["creep.flac"]), "--find", "creep")
    assert rc == 2 and err == "mouth play: SOURCE or --find, not both\n"
    assert queued(home) == []


def test_neither_source_nor_find_is_refused(home, capsys):
    rc, _, err = run(capsys)
    assert rc == 2 and err == "mouth play: name a SOURCE, or --find WORDS\n"


@pytest.mark.parametrize("flag", [["--finder", "a"], ["--matches", "all"], ["--pick", "1"]])
def test_the_find_flags_need_find(home, capsys, flag):
    rc, _, err = run(capsys, str(home["songs"]["creep.flac"]), *flag)
    assert rc == 2 and err == "mouth play: --finder, --matches and --pick go with --find\n"
    assert queued(home) == []


def test_find_is_in_the_help_with_no_finder_set(home, capsys):
    with pytest.raises(SystemExit):
        cli.main(["play", "--help"])
    usage = " ".join(capsys.readouterr().out.split())
    assert "--find WORDS" in usage and "play_finders in the host's mouth.json" in usage


# -- after the find, the path is today's -------------------------------------------------

def test_the_other_play_options_still_apply(home, capsys):
    host(home, entry("music", *prints(f"a\t{uri(home, 'creep.flac')}")))
    assert run(capsys, "--find", "creep", "--start", "1", "--end", "2", "--volume", "30",
               "--no-instrumental")[0] == 0
    (x,) = queued(home)
    assert (x["start"], x["end"]) == (1.0, 2.0)
    assert (x["volume"], x["volume_from"]) == (30, "flag")
    assert (x["instrumental"], x["instrumental_from"]) == (False, "flag")


def test_an_http_match_is_fetched_to_the_play_cache(home, capsys):
    host(home, entry("music", *prints(f"Song\t{URL}")))
    assert run(capsys, "--find", "song")[0] == 0
    assert home["requests"] == [URL]
    (x,) = queued(home)
    assert x["source_url"] == URL
    assert x["file"].startswith(str((home["d"] / "play-cache").resolve()))


def test_an_http_match_with_play_remote_off_is_refused(home, capsys, monkeypatch):
    no_client(monkeypatch)
    host(home, entry("music", *prints(f"Song\t{URL}")), play_remote=False)
    rc, out, err = run(capsys, "--find", "song")
    assert (rc, out) == (2, "")
    assert err.startswith("mouth: ") and "Traceback" not in err
    assert queued(home) == []


def test_an_http_match_with_play_remote_locked_off_is_refused(home, capsys, monkeypatch):
    no_client(monkeypatch)
    host(home, entry("music", *prints(f"Song\t{URL}")), play_remote=False,
         _locked=["play_remote"])
    fake_session(home, monkeypatch, locked=("play_remote",), play_remote=True)
    assert run(capsys, "--find", "song")[0] == 2
    assert queued(home) == []
