"""EARS-362: `ears heard hook` in partials-only mode, and its --help.

Partials-only is for a seat that gets each finished turn by mail (heard+<device>):
the hook then drops every item a turn record completes (the turn, and the partials
it subsumes), and keeps the partial preview, the shown events and the said lines.
The cursor still moves past what it dropped. The setting is read CLI > env > file
> default (off); the heard waiter (heard_wait) never reads it.

`ears heard hook --help` / `-h` print the usage to stdout and exit 0, before the off
switch, before stdin, and before any cursor is made or moved.
"""
from __future__ import annotations

import io
import json
import sys

import pytest

from ears import heard
from tests import env_fence

SESSION = "seat-1"
OTHER = "seat-2"


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSIONVOICE_HOME", str(tmp_path / "home"))
    for v in (heard.HEARD_HOOK_ENV, heard.HEARD_BUDGET_ENV, heard.HEARD_MAX_LINES_ENV,
              heard.HEARD_PARTIALS_ONLY_ENV, heard.HEARD_EVENT_ENV, heard.HEARD_CALLER_ENV):
        env_fence.delenv(monkeypatch, v)
    conf = tmp_path / "ears.json"            # absent until a test writes it
    monkeypatch.setenv("EARS_CONF", str(conf))
    return conf


def start_cursor():
    """A seat's first take starts its cursor at the end of the log, showing nothing."""
    assert heard.take(SESSION) == ("", heard.last_seq())


def write_room():
    """A finished turn (two partials and its turn), a shown event, a quiet one, another
    seat's said line, then a partial with no turn yet. Returns the records."""
    return [
        heard.partial("hello", device="mic1"),
        heard.partial("there", device="mic1"),
        heard.turn("hello there", device="mic1", detector="silence"),
        heard.event(heard.EV_WAKE_WORD, device="mic1", word="assistant"),
        heard.event(heard.EV_HEARTBEAT, device="mic1"),
        heard.said(utt="u1", agent="bob", text_played_est="on it", session=OTHER),
        heard.partial("and the next", device="mic1"),
    ]


def cursor_seq():
    return heard.load_cursor(SESSION).seq


# --- collapse / take ---------------------------------------------------------------

def test_partials_only_drops_the_finished_turn_and_keeps_the_rest(base):
    start_cursor()
    recs = write_room()
    text, seq = heard.take(SESSION, partials_only=True)
    assert "hello there" not in text
    assert "hello" not in text and "there" not in text.replace("and the next", "")
    lines = text.splitlines()
    assert any("wake-word" in l and "assistant" in l for l in lines)       # a shown event
    assert any(l.startswith("[said bob") and "on it" in l for l in lines)  # another seat's said
    assert any(l.endswith("and the next …") and "partial]" in l for l in lines)  # the preview
    assert "heartbeat" not in text                                        # quiet, as ever
    assert len(lines) == 3
    assert seq == recs[-1]["seq"] == cursor_seq()                         # past everything


def test_without_it_the_turn_is_shown(base):
    start_cursor()
    write_room()
    text, _ = heard.take(SESSION)
    assert "hello there" in text
    assert "partial] hello there …" not in text                           # subsumed, not a preview


def test_a_turn_alone_is_silent_and_the_cursor_still_moves(base):
    start_cursor()
    heard.partial("one", device="mic1")
    t = heard.turn("one two", device="mic1")
    text, seq = heard.take(SESSION, partials_only=True)
    assert text == ""
    assert seq == t["seq"] == cursor_seq()
    # nothing comes back on the next take: the dropped turn is behind the cursor
    assert heard.take(SESSION) == ("", t["seq"])


def test_collapse_marks_the_turns_items_silent_with_the_same_span(base):
    start_cursor()
    write_room()
    recs, _ = heard.read_after(0)
    on = heard.collapse(recs, SESSION, partials_only=True)
    off = heard.collapse(recs, SESSION)
    assert [(i.first, i.last) for i in on] == [(i.first, i.last) for i in off]
    changed = [(a, b) for a, b in zip(off, on) if a.text != b.text]
    assert len(changed) == 1
    was, now = changed[0]
    assert "hello there" in was.text and now.text is None


def test_a_converse_turn_is_dropped_too(base):
    start_cursor()
    heard.turn("from converse", device="mic1", via="converse", session=OTHER)
    text, _ = heard.take(SESSION, partials_only=True)
    assert text == ""


# --- the setting: CLI > env > file > default ----------------------------------------

def write_conf(conf, value):
    conf.write_text(value if isinstance(value, str) else json.dumps(value))


def test_default_is_off(base):
    assert heard.partials_only_setting() is False


@pytest.mark.parametrize("word", ["1", "on", "true", "yes", "ON", " Yes "])
def test_env_on_words(base, monkeypatch, word):
    monkeypatch.setenv(heard.HEARD_PARTIALS_ONLY_ENV, word)
    assert heard.partials_only_setting() is True


@pytest.mark.parametrize("word", ["0", "off", "false", "no", "OFF", " No "])
def test_env_off_words_win_over_the_file(base, monkeypatch, word):
    write_conf(base, {"heard_partials_only": True})
    monkeypatch.setenv(heard.HEARD_PARTIALS_ONLY_ENV, word)
    assert heard.partials_only_setting() is False


def test_env_on_wins_over_the_file(base, monkeypatch):
    write_conf(base, {"heard_partials_only": False})
    monkeypatch.setenv(heard.HEARD_PARTIALS_ONLY_ENV, "1")
    assert heard.partials_only_setting() is True


@pytest.mark.parametrize("empty", ["", "   "])
def test_an_empty_env_is_not_set_and_the_file_decides(base, monkeypatch, empty):
    write_conf(base, {"heard_partials_only": True})
    monkeypatch.setenv(heard.HEARD_PARTIALS_ONLY_ENV, empty)
    assert heard.partials_only_setting() is True


@pytest.mark.parametrize("garbage", ["maybe", "2", "enabled"])
def test_a_garbage_env_is_the_default_off(base, monkeypatch, garbage):
    write_conf(base, {"heard_partials_only": True})
    monkeypatch.setenv(heard.HEARD_PARTIALS_ONLY_ENV, garbage)
    assert heard.partials_only_setting() is False


def test_the_cli_wins_over_env_and_file(base, monkeypatch):
    write_conf(base, {"heard_partials_only": True})
    monkeypatch.setenv(heard.HEARD_PARTIALS_ONLY_ENV, "1")
    assert heard.partials_only_setting(False) is False
    monkeypatch.setenv(heard.HEARD_PARTIALS_ONLY_ENV, "0")
    write_conf(base, {"heard_partials_only": False})
    assert heard.partials_only_setting(True) is True


def test_the_file_key_turns_it_on(base):
    write_conf(base, {"listen": True, "heard_partials_only": True})
    assert heard.partials_only_setting() is True
    write_conf(base, {"heard_partials_only": False})
    assert heard.partials_only_setting() is False


@pytest.mark.parametrize("content", [
    "{not json",                                   # bad JSON
    "[1, 2]",                                      # not an object
    {"listen": True},                              # no key
    {"heard_partials_only": "true"},               # not a boolean
    {"heard_partials_only": 1},
    "",                                            # empty file
])
def test_an_invalid_file_is_the_default(base, content):
    write_conf(base, content)
    assert heard.partials_only_setting() is False


def test_a_missing_or_unreadable_file_is_the_default(base, tmp_path, monkeypatch):
    assert not base.exists()
    assert heard.partials_only_setting() is False
    monkeypatch.setenv("EARS_CONF", str(tmp_path))       # a directory: open() fails
    assert heard.partials_only_setting() is False


def test_ears_conf_unset_names_the_settings_folder(base, tmp_path, monkeypatch):
    monkeypatch.delenv("EARS_CONF")
    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    from ears.home import settings_dir
    assert heard.ears_conf_path() == settings_dir() / "ears.json"
    (tmp_path / "h" / ".sessionkit" / "voice" / "settings").mkdir(parents=True)
    write_conf(settings_dir() / "ears.json", {"heard_partials_only": True})
    assert heard.partials_only_setting() is True


def test_the_waiter_path_never_reads_the_setting(base, monkeypatch):
    """take() is heard_wait's too: with no partials_only it shows the turn, whatever is set."""
    write_conf(base, {"heard_partials_only": True})
    monkeypatch.setenv(heard.HEARD_PARTIALS_ONLY_ENV, "1")
    start_cursor()
    heard.turn("for the waiter", device="mic1")
    text, _ = heard.take(SESSION)
    assert "for the waiter" in text


# --- run_hook and the script's flags -----------------------------------------------

def hook_input():
    return json.dumps({"session_id": SESSION, "hook_event_name": "PostToolBatch"})


def context(out):
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""


def test_run_hook_reads_the_setting(base, monkeypatch):
    start_cursor()
    heard.turn("said in the room", device="mic1")
    heard.event(heard.EV_BARGE_IN, device="mic1")
    monkeypatch.setenv(heard.HEARD_PARTIALS_ONLY_ENV, "on")
    ctx = context(heard.run_hook(hook_input()))
    assert "barge-in" in ctx and "said in the room" not in ctx


def test_run_hook_cli_off_beats_the_env(base, monkeypatch):
    start_cursor()
    heard.turn("said in the room", device="mic1")
    monkeypatch.setenv(heard.HEARD_PARTIALS_ONLY_ENV, "on")
    assert "said in the room" in context(heard.run_hook(hook_input(), partials_only=False))


def run_main(monkeypatch, capsys, argv, stdin):
    monkeypatch.setattr(sys, "stdin", stdin)
    rc = heard.main(argv)
    out = capsys.readouterr()
    return rc, out.out, out.err


@pytest.mark.parametrize("argv, shown", [
    (["hook", "--partials-only"], False),
    (["hook", "--partials-only", "--budget", "400"], False),
    (["hook", "--partials-only", "--no-partials-only"], True),     # the last one wins
    (["hook", "--no-partials-only", "--partials-only"], False),
    (["hook"], True),
])
def test_the_hook_flags(base, monkeypatch, capsys, argv, shown):
    start_cursor()
    heard.turn("said in the room", device="mic1")
    heard.partial("still talking", device="mic1")
    rc, out, err = run_main(monkeypatch, capsys, argv, io.StringIO(hook_input()))
    assert rc == 0, err
    ctx = context(out.strip())
    assert ("said in the room" in ctx) is shown
    assert "still talking" in ctx


def test_no_partials_only_beats_env_and_file(base, monkeypatch, capsys):
    write_conf(base, {"heard_partials_only": True})
    monkeypatch.setenv(heard.HEARD_PARTIALS_ONLY_ENV, "1")
    start_cursor()
    heard.turn("said in the room", device="mic1")
    rc, out, _ = run_main(monkeypatch, capsys, ["hook", "--no-partials-only"],
                          io.StringIO(hook_input()))
    assert rc == 0 and "said in the room" in context(out.strip())


# --- --help -----------------------------------------------------------------------

class NoRead(io.StringIO):
    def read(self, *a):
        raise AssertionError("--help read stdin")

    def isatty(self):
        return False


@pytest.mark.parametrize("argv", [
    ["hook", "--help"], ["hook", "-h"],
    ["hook", "--partials-only", "--help"],
    ["hook", "--budget", "10", "-h"],
    ["--help"], ["-h"], ["help"],
])
def test_help_prints_usage_and_touches_nothing(base, monkeypatch, capsys, argv):
    start_cursor()
    heard.turn("waiting", device="mic1")
    before = heard.cursor_dir().joinpath(heard._session_file_name(SESSION)).read_text()
    rc, out, err = run_main(monkeypatch, capsys, argv, NoRead())
    assert rc == 0 and err == ""
    assert out.startswith("usage: ears heard hook")
    assert "--partials-only" in out and "--no-partials-only" in out
    assert "EARS_HEARD_PARTIALS_ONLY" in out and "heard_partials_only" in out
    after = heard.cursor_dir().joinpath(heard._session_file_name(SESSION)).read_text()
    assert after == before                                         # no cursor moved
    assert sorted(p.name for p in heard.cursor_dir().iterdir()) == sorted(
        [heard._session_file_name(SESSION), heard._session_file_name(SESSION) + ".lock"])


def test_help_makes_no_cursor(base, monkeypatch, capsys):
    rc, out, _ = run_main(monkeypatch, capsys, ["hook", "--help"], NoRead())
    assert rc == 0 and out
    assert not heard.cursor_dir().exists()


def test_help_prints_even_when_the_hook_is_off(base, monkeypatch, capsys):
    monkeypatch.setenv(heard.HEARD_HOOK_ENV, "off")
    rc, out, _ = run_main(monkeypatch, capsys, ["hook", "--help"], NoRead())
    assert rc == 0 and out.startswith("usage:")


def test_ears_heard_hook_help_through_the_ears_cli(base, monkeypatch, capsys):
    from ears.cli import main as ears_main
    monkeypatch.setattr(sys, "stdin", NoRead())
    assert ears_main(["heard", "hook", "--help"]) == 0
    assert ears_main(["heard", "--help"]) == 0
    out = capsys.readouterr().out
    assert out.count("usage: ears heard hook") == 2


def test_the_off_switch_still_silences_the_hook(base, monkeypatch, capsys):
    start_cursor()
    heard.turn("said in the room", device="mic1")
    monkeypatch.setenv(heard.HEARD_HOOK_ENV, "off")
    rc, out, _ = run_main(monkeypatch, capsys, ["hook"], io.StringIO(hook_input()))
    assert rc == 0 and out == ""
