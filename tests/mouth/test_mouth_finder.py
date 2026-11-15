"""The finders ``mouth play --find`` asks (``mouth.finder``) and their settings
(``voices.finder_settings``).

- ``play_finders`` (an ordered list of ``{"name", "argv"}``) and
  ``play_finder_timeout_s`` (seconds over 0, else 10) come from the HOST FILE ALONE:
  no session, seat or agent layer can choose which program the mouth runs.
- The words are split with ``str.split()`` and appended as separate arguments; no
  shell. The first finder that finds wins and the rest are not run; ``name=`` asks
  one, and an unknown name is refused.
- The contract: one match per line, tab-separated, the LAST field a URL
  (``file:///abs`` or ``http(s)://``). Exit 0 found, 1 none, anything else or a
  timeout a FinderError that stops the chain. Exit 0 with nothing printed is an
  error, and one line with no URL refuses the whole answer.

Every finder is ``tests/fixtures/fake_finder.py``, run with this Python: nothing
here needs a real music collection. The cascade is a fake ``session settings
--json`` (``MOUTH_SESSION_BIN``), as in ``test_mouth_play_url_settings.py``.
Assertions are on outcomes (what was found, what ran, what is still alive), never
on elapsed time.
"""

from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from mouth import finder, voices
from mouth.finder import FinderError, Found, Match, NoFinder, NotFound

SID = "test-sid-finder"
FAKE = Path(__file__).resolve().parents[1] / "fixtures" / "fake_finder.py"
SCHEMA = Path(__file__).resolve().parents[2] / "settings" / "mouth.schema.json"
SONG = "file:///music/tlc/no-scrubs.flac"


@pytest.fixture
def home(tmp_path, monkeypatch):
    d = tmp_path / "mouth"
    d.mkdir()
    host = tmp_path / "mouth.json"
    monkeypatch.setenv("MOUTH_DIR", str(d))
    monkeypatch.setenv("MOUTH_SETTINGS", str(host))
    monkeypatch.setenv("MOUTH_SESSION_BIN", "")   # the cascade off unless a test fakes it
    return {"d": d, "host": host, "tmp": tmp_path}


def entry(name: str, *opts: str) -> dict:
    """A ``play_finders`` entry running the fake with OPTS; the words go after ``--``."""
    return {"name": name, "argv": [sys.executable, str(FAKE), *opts, "--"]}


def host(home, *finders, **kw):
    home["host"].write_text(json.dumps({"play_finders": list(finders), **kw}))


def logged(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text().splitlines()] if path.exists() else []


def find(home, words="no scrubs", **kw) -> Found:
    return finder.find(words, d=home["d"], **kw)


def gone(pid: int, within_s: float = 10.0) -> bool:
    """Whether PID is gone (or a zombie waiting for init to reap it). Polled, since an
    orphan is reaped by init in its own time; the bound only stops a hang."""
    end = time.monotonic() + within_s
    while time.monotonic() < end:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        stat_ = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                               capture_output=True, text=True).stdout.strip()
        if not stat_ or stat_.startswith("Z"):
            return True
        time.sleep(0.05)
    return False


def fake_session(home, monkeypatch, layer="session", **values):
    """A fake ``session settings --json``: ``values`` set in ``layer``."""
    path = f"/fake/{layer}/mouth.json"
    hpath = str(home["host"])
    out = {"tools": {"mouth": {
        "values": values, "origins": {k: path for k in values}, "locked": {},
        "sources": [{"layer": layer, "path": path}, {"layer": "host", "path": hpath}]}}}
    p = home["tmp"] / f"fake-session-{layer}"
    p.write_text(f"#!/bin/sh\ncat <<'EOF'\n{json.dumps(out)}\nEOF\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("MOUTH_SESSION_BIN", str(p))


# -- found: exit 0 -----------------------------------------------------------------------

def test_one_line_is_one_match_split_on_the_last_tab(home):
    host(home, entry("music", "--print", f"No Scrubs\tTLC\t{SONG}"))
    got = find(home)
    assert got == Found(finder="music", matches=[Match("No Scrubs | TLC", SONG)], asked=["music"])


def test_several_lines_are_several_matches_in_order(home):
    host(home, entry("music",
                     "--print", "No Scrubs\tTLC\tfile:///music/a.mp3",
                     "--print", "  ",                                # a blank line is skipped
                     "--print", "No Scrubs (live)\t\thttps://music.invalid/b.m4a\r",
                     "--print", "file:///music/c.flac"))             # no label: the URL is it
    got = find(home).matches
    assert got == [Match("No Scrubs | TLC", "file:///music/a.mp3"),
                   Match("No Scrubs (live)", "https://music.invalid/b.m4a"),
                   Match("file:///music/c.flac", "file:///music/c.flac")]


@pytest.mark.parametrize("url", ["FILE:///m/a.mp3", "HTTP://h.invalid/a", "Https://h.invalid/a"])
def test_the_scheme_is_any_case(home, url):
    host(home, entry("music", "--print", f"x\t{url}"))
    assert find(home).matches == [Match("x", url)]


# -- none: exit 1 ------------------------------------------------------------------------

def test_exit_1_everywhere_is_not_found_naming_the_words_and_every_finder(home):
    host(home, entry("music", "--exit", "1"),
          entry("beets", "--exit", "1", "--print", "ignored\tnot a url"))   # 1 is none, whatever it prints
    with pytest.raises(NotFound) as e:
        find(home, "  no   scrubs ")
    assert str(e.value) == "mouth: not found: no scrubs (asked: music, beets)"


# -- finder errors -----------------------------------------------------------------------

def test_another_exit_is_a_finder_error_naming_it_with_its_stderr_tail(home):
    host(home, entry("music", "--exit", "4", "--stderr", "first", "--stderr", "index locked"))
    with pytest.raises(FinderError) as e:
        find(home)
    assert str(e.value) == "mouth: finder music failed: exit 4: index locked"


def test_the_stderr_tail_is_capped(home):
    host(home, entry("music", "--exit", "3", "--stderr", "x" * 500))
    with pytest.raises(FinderError) as e:
        find(home)
    assert str(e.value) == "mouth: finder music failed: exit 3: " + "x" * finder.STDERR_TAIL


def test_a_finder_killed_by_a_signal_says_so(home, tmp_path):
    script = tmp_path / "suicide.py"
    script.write_text("import os, signal\nos.kill(os.getpid(), signal.SIGKILL)\n")
    host(home, {"name": "music", "argv": [sys.executable, str(script)]})
    with pytest.raises(FinderError) as e:
        find(home)
    assert str(e.value) == "mouth: finder music failed: killed by signal 9"


@pytest.mark.parametrize("opts", [(), ("--print", " "), ("--print", "\r"), ("--print", " \t ")])
def test_exit_0_with_nothing_printed_is_a_finder_error(home, opts):
    host(home, entry("music", *opts))
    with pytest.raises(FinderError) as e:
        find(home)
    assert str(e.value) == "mouth: finder music failed: exited 0 but printed no match"


@pytest.mark.parametrize("last", ["/music/a.mp3", "ftp://h.invalid/a.mp3", "file://host/a.mp3",
                                  "file:/music/a.mp3", "a.mp3", "No Scrubs"])
def test_one_line_with_no_url_refuses_the_whole_answer(home, last):
    host(home, entry("music", "--print", f"good\t{SONG}", "--print", f"bad\t{last}",
                     "--print", f"good too\t{SONG}"))
    with pytest.raises(FinderError) as e:
        find(home)
    assert str(e.value) == "mouth: finder music failed: printed a line with no URL (line 2)"


def test_a_program_that_is_not_there_is_a_finder_error(home, tmp_path):
    missing = str(tmp_path / "no-such-finder")
    host(home, {"name": "music", "argv": [missing, "find"]})
    with pytest.raises(FinderError) as e:
        find(home)
    assert str(e.value) == f"mouth: finder music failed: could not run: {missing} (No such file or directory)"


def test_a_timeout_is_a_finder_error_and_kills_the_whole_group(home):
    log = home["tmp"] / "log.jsonl"
    host(home, entry("music", "--fork", "--sleep", "60", "--log", str(log)),
          play_finder_timeout_s=0.5)
    with pytest.raises(FinderError) as e:
        find(home)
    assert str(e.value) == "mouth: finder music failed: timed out after 0.5s"
    (run,) = logged(log)
    assert gone(run["pid"]) and gone(run["child"])      # the finder AND its forked child


def test_a_child_that_left_the_group_cannot_hold_the_mouth(home):
    """A grandchild in a session of its own still holds stdout: the timeout returns
    anyway, because the pipes are closed, not read to the end."""
    log = home["tmp"] / "log.jsonl"
    host(home, entry("music", "--escape", "--sleep", "60", "--log", str(log)),
          play_finder_timeout_s=0.5)
    try:
        with pytest.raises(FinderError, match="timed out after 0.5s"):
            find(home)
        (run,) = logged(log)
        assert gone(run["pid"])
    finally:
        for run in logged(log):
            try:
                os.kill(run["child"], signal.SIGKILL)
            except (ProcessLookupError, TypeError):
                pass


# -- the order ---------------------------------------------------------------------------

def test_the_first_finder_that_finds_wins_and_the_rest_are_not_run(home):
    a, b = home["tmp"] / "a.jsonl", home["tmp"] / "b.jsonl"
    host(home, entry("a", "--print", f"A\t{SONG}", "--log", str(a)),
          entry("b", "--print", "B\tfile:///b.mp3", "--log", str(b)))
    got = find(home)
    assert (got.finder, got.asked, got.matches) == ("a", ["a"], [Match("A", SONG)])
    assert len(logged(a)) == 1 and logged(b) == []


def test_a_finder_that_answers_none_hands_on_to_the_next(home):
    a, b = home["tmp"] / "a.jsonl", home["tmp"] / "b.jsonl"
    host(home, entry("a", "--exit", "1", "--log", str(a)),
          entry("b", "--print", f"B\t{SONG}", "--log", str(b)))
    got = find(home)
    assert (got.finder, got.asked, got.matches) == ("b", ["a", "b"], [Match("B", SONG)])
    assert len(logged(a)) == 1 and len(logged(b)) == 1


def test_a_finder_error_stops_the_chain(home):
    b = home["tmp"] / "b.jsonl"
    host(home, entry("a", "--exit", "2"), entry("b", "--print", f"B\t{SONG}", "--log", str(b)))
    with pytest.raises(FinderError, match="finder a failed: exit 2"):
        find(home)
    assert logged(b) == []


def test_a_named_finder_is_the_only_one_asked(home):
    a, b = home["tmp"] / "a.jsonl", home["tmp"] / "b.jsonl"
    host(home, entry("a", "--print", f"A\t{SONG}", "--log", str(a)),
          entry("b", "--print", "B\tfile:///b.mp3", "--log", str(b)))
    got = find(home, name="b")
    assert (got.finder, got.asked, got.matches) == ("b", ["b"], [Match("B", "file:///b.mp3")])
    assert logged(a) == []


def test_a_named_finder_that_answers_none_is_not_found_naming_it_alone(home):
    host(home, entry("a", "--print", f"A\t{SONG}"), entry("b", "--exit", "1"))
    with pytest.raises(NotFound) as e:
        find(home, name="b")
    assert str(e.value) == "mouth: not found: no scrubs (asked: b)"


def test_an_unknown_name_is_refused_listing_the_names(home):
    a = home["tmp"] / "a.jsonl"
    host(home, entry("a", "--log", str(a)), entry("b"))
    with pytest.raises(ValueError) as e:
        find(home, name="beets")
    assert str(e.value) == "mouth: no finder named beets (have: a, b)"
    assert not isinstance(e.value, (FinderError, NotFound)) and logged(a) == []


# -- the words, and no shell -------------------------------------------------------------

def test_the_words_arrive_as_separate_arguments_exactly(home):
    log = home["tmp"] / "log.jsonl"
    host(home, entry("music", "--print", f"x\t{SONG}", "--log", str(log)))
    find(home, "  walk\tof  life ; $(touch pwned) `id` \"quoted words\" ")
    (run,) = logged(log)
    assert run["words"] == ["walk", "of", "life", ";", "$(touch", "pwned)", "`id`",
                            '"quoted', 'words"']
    assert not (Path.cwd() / "pwned").exists()


def test_no_shell_reads_the_argv(home):
    """A ``|`` in a finder's argv is one argument, printed back intact: a shell would
    have made it a pipe."""
    host(home, entry("music", "--print", f"Walk | Of Life\t{SONG}", "--stderr", "a > b"))
    assert find(home).matches == [Match("Walk | Of Life", SONG)]


@pytest.mark.parametrize("words", ["", "   ", "\t\n"])
def test_empty_words_are_refused_before_any_finder_runs(home, words):
    log = home["tmp"] / "log.jsonl"
    host(home, entry("music", "--log", str(log)))
    with pytest.raises(ValueError) as e:
        find(home, words)
    assert str(e.value) == "mouth: --find needs words" and logged(log) == []


# -- the settings: the host file alone ---------------------------------------------------

def test_no_finder_set_is_refused_naming_the_host_file(home):
    with pytest.raises(NoFinder) as e:
        find(home)
    assert str(e.value) == f"mouth: no finder: set play_finders in the host's mouth.json ({home['host']})"


@pytest.mark.parametrize("layer", voices.CASCADE)
def test_no_layer_can_set_play_finders(home, monkeypatch, layer):
    """A session, seat or agent layer naming a finder runs nothing: with no host
    entry it is still "no finder", and beside a host entry it changes nothing."""
    log = home["tmp"] / "layer.jsonl"
    fake_session(home, monkeypatch, layer,
                 play_finders=[entry("evil", "--print", "x\tfile:///evil", "--log", str(log))],
                 play_finder_timeout_s=0.01)
    with pytest.raises(NoFinder):
        find(home)
    host(home, entry("music", "--print", f"x\t{SONG}"))
    got = find(home)
    assert got.finder == "music" and logged(log) == []
    assert voices.finder_settings(home["d"])["timeout_s"] == voices.BUILTIN_FINDER_TIMEOUT_S


def test_finding_never_reads_the_cascade(home, monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("cascade() read for play_finders")
    monkeypatch.setattr(voices, "cascade", boom)
    host(home, entry("music", "--print", f"x\t{SONG}"))
    assert find(home).finder == "music"


def test_malformed_entries_are_skipped_and_the_first_name_wins(home):
    good = entry("music", "--print", f"x\t{SONG}")
    home["host"].write_text(json.dumps({"play_finders": [
        "music find",                                  # not an object
        {"name": "", "argv": ["x"]},                   # empty name
        {"name": "  ", "argv": ["x"]},                 # blank name
        {"name": 3, "argv": ["x"]},                    # not a string
        {"name": "noargv"},                            # no argv
        {"name": "strargv", "argv": "music find"},     # argv not a list
        {"name": "emptyargv", "argv": []},             # empty argv
        {"name": "badword", "argv": ["music", ""]},    # an empty word
        {"name": "intword", "argv": ["music", 1]},     # a word not a string
        good,
        {"name": "music", "argv": ["something", "else"]},   # a duplicate: the first wins
        {"name": "beets", "argv": ["beet", "ls"]},
    ]}))
    got = voices.finder_settings(home["d"])["finders"]
    assert got == [("music", good["argv"]), ("beets", ["beet", "ls"])]


@pytest.mark.parametrize("value", [None, "music", {"name": "music", "argv": ["m"]}, 3, []])
def test_play_finders_not_a_good_list_is_no_finder(home, value):
    home["host"].write_text(json.dumps({"play_finders": value}))
    assert voices.finder_settings(home["d"])["finders"] == []
    with pytest.raises(NoFinder):
        find(home)


def test_every_entry_malformed_is_no_finder(home):
    home["host"].write_text(json.dumps({"play_finders": [{"name": "x"}, {"argv": ["y"]}]}))
    with pytest.raises(NoFinder):
        find(home)


@pytest.mark.parametrize("value, want", [(None, 10.0), (0, 10.0), (-1, 10.0), ("5", 10.0),
                                         (True, 10.0), (float("inf"), 10.0),
                                         (2, 2.0), (0.25, 0.25), (30, 30.0)])
def test_the_timeout_setting(home, value, want):
    raw = '{"play_finder_timeout_s": %s}' % ("Infinity" if value == float("inf")
                                              else json.dumps(value))
    home["host"].write_text(raw)
    assert voices.finder_settings(home["d"])["timeout_s"] == want


def test_the_settings_name_the_host_file(home):
    assert voices.finder_settings(home["d"]) == {"finders": [], "timeout_s": 10.0,
                                                 "path": home["host"]}


# -- the schema --------------------------------------------------------------------------

def test_the_schema_carries_both_keys_with_their_defaults():
    props = json.loads(SCHEMA.read_text())["properties"]
    finders, timeout = props["play_finders"], props["play_finder_timeout_s"]
    assert finders["type"] == "array" and finders["default"] == []
    item = finders["items"]
    assert item["type"] == "object" and set(item["required"]) == {"name", "argv"}
    assert item["properties"]["argv"]["type"] == "array"
    assert item["properties"]["argv"]["minItems"] == 1
    assert timeout["default"] == voices.BUILTIN_FINDER_TIMEOUT_S
    assert timeout["exclusiveMinimum"] == 0
