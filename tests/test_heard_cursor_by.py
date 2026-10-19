"""Who moved the heard cursor (EARS-148): every cursor write records its
mutator, ``by`` = {event, caller, pid, at, heard, session}.

Every test runs in a tmp VOICEMODE_BASE_DIR; nothing touches ~/.voicemode.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

from ears import heard
from tests._anchor import ANCHOR_SUMMER

SESSION_ENVS = ("VOICEMODE_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID")
KEYS = {"event", "caller", "pid", "at", "heard", "session"}


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path))
    for v in ("VOICEMODE_HEARD_BUDGET", "VOICEMODE_HEARD_MAX_LINES",
              heard.HEARD_EVENT_ENV, heard.HEARD_CALLER_ENV, *heard.HEARD_ENV_OLD.values(),
              *SESSION_ENVS):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "writer-session")
    return tmp_path


def cursor_file(session="s"):
    return heard.cursor_dir() / heard._session_file_name(session)


def raw_cursor(session="s"):
    return json.loads(cursor_file(session).read_text())


def put_cursor(raw, session="s"):
    """A cursor written by someone else (an old writer, or a previous one)."""
    p = cursor_file(session)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(raw) + "\n")


# a previous writer's stamp: the anchor, at the +11:00 offset it was written with
PREV_AT = ANCHOR_SUMMER.astimezone(timezone(timedelta(hours=11))).isoformat(timespec="milliseconds")
PREV = {"event": "PostToolBatch", "caller": "/the/previous/writer", "pid": 1,
        "at": PREV_AT, "heard": "/old/heard.py", "session": "prev"}


def assert_by(by, event, caller):
    assert set(by) == KEYS
    assert by["event"] == event and by["caller"] == caller
    assert by["pid"] == os.getpid()
    assert by["heard"] == os.path.abspath(heard.__file__)
    assert by["session"] == "writer-session"
    assert datetime.fromisoformat(by["at"]).utcoffset() is not None
    assert by["at"] != PREV["at"]


def lines_end(path, n):
    """The byte offset just past the n-th line of ``path``."""
    data = path.read_bytes()
    pos = 0
    for _ in range(n):
        pos = data.index(b"\n", pos) + 1
    return pos


# --- the round trip and the legacy cursor ---------------------------------------

def test_save_then_load_keeps_by(base):
    heard.save_cursor("s", heard.Cursor(5, "heard_2030-10-09.jsonl", 10),
                      by={"event": "PostToolBatch", "caller": "/x/hook"})
    cur = heard.load_cursor("s")
    assert (cur.seq, cur.file, cur.offset) == (5, "heard_2030-10-09.jsonl", 10)
    assert_by(cur.by, "PostToolBatch", "/x/hook")
    assert raw_cursor()["by"] == cur.by          # on disk, beside the offset


def test_pid_at_heard_session_are_stamped_at_the_write_not_the_callers(base, monkeypatch):
    stamps = iter(["2030-10-09T05:00:00.000+11:00", "2030-10-09T05:09:00.000+11:00"])
    monkeypatch.setattr(heard, "_now_iso", lambda: next(stamps))
    by = heard.mutator(event="tool", caller="ears.heard_wait")   # armed at 05:00
    assert by["at"] == "2030-10-09T05:00:00.000+11:00"
    heard.save_cursor("s", heard.Cursor(1), by={**by, "pid": 1, "heard": "x",
                                                "session": "someone-else"})
    got = heard.load_cursor("s").by
    assert got["at"] == "2030-10-09T05:09:00.000+11:00"            # the write's time
    assert (got["event"], got["caller"]) == ("tool", "ears.heard_wait")
    assert got["pid"] == os.getpid() and got["session"] == "writer-session"
    assert got["heard"] == os.path.abspath(heard.__file__)


def test_a_legacy_cursor_with_no_by_loads_with_by_none(base):
    put_cursor({"seq": 3, "file": "heard_2030-10-09.jsonl", "offset": 7})
    cur = heard.load_cursor("s")
    assert (cur.seq, cur.file, cur.offset, cur.by) == (3, "heard_2030-10-09.jsonl", 7, None)
    assert "by" not in json.loads(cur.to_json())   # and writes no "by": null either


@pytest.mark.parametrize("bad", ["vera", ["PostToolBatch"], 7, None, True])
def test_a_non_dict_by_loads_as_none(base, bad):
    put_cursor({"seq": 3, "file": "heard_2030-10-09.jsonl", "offset": 7, "by": bad})
    cur = heard.load_cursor("s")
    assert cur.seq == 3 and cur.offset == 7 and cur.by is None


def test_an_old_shape_cursor_still_parses(base):
    put_cursor({"seq": 12})                       # no hint at all
    cur = heard.load_cursor("s")
    assert (cur.seq, cur.file, cur.offset, cur.by) == (12, None, 0, None)


@pytest.mark.parametrize("junk", ["[1, 2]", "\"seq\"", "{\"file\": \"x\"}", "not json"])
def test_an_unreadable_cursor_is_still_absent(base, junk):
    p = cursor_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(junk)
    assert heard.load_cursor("s") is None


def test_an_old_reader_still_reads_a_cursor_with_by(base):
    heard.save_cursor("s", heard.Cursor(9, "heard_2030-10-09.jsonl", 40),
                      by={"event": "PostToolBatch", "caller": "/x"})
    raw = raw_cursor()   # the old load_cursor's exact reads
    assert (int(raw["seq"]), raw.get("file"), int(raw.get("offset") or 0)) == (
        9, "heard_2030-10-09.jsonl", 40)


def test_a_resaved_loaded_cursor_never_keeps_the_previous_writers_name(base):
    put_cursor({"seq": 3, "file": "heard_2030-10-09.jsonl", "offset": 7, "by": PREV})
    cur = heard.load_cursor("s")
    assert cur.by == PREV
    heard.save_cursor("s", cur)                   # no by: this writer's defaults
    got = heard.load_cursor("s").by
    assert got["event"] == "other" and got["caller"] != PREV["caller"]
    assert_by(got, "other", heard._default_caller())


# --- mutator()'s defaults ----------------------------------------------------------

def test_mutator_defaults_are_other_and_the_running_script(base, monkeypatch, tmp_path):
    script = tmp_path / "hook.py"
    script.write_text("")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["hook.py"])
    by = heard.mutator()
    assert by["event"] == "other"
    assert by["caller"] == str(script)           # abspath, because it names a file


def test_mutator_keeps_a_non_file_argv0_verbatim(base, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["-c"])     # python3 -c: never <cwd>/-c
    assert heard.mutator()["caller"] == "-c"
    monkeypatch.setattr(sys, "argv", [])
    assert heard.mutator()["caller"] is None


def test_mutator_env_names_a_wrapper_that_cannot_change_code(base, monkeypatch):
    monkeypatch.setenv(heard.HEARD_EVENT_ENV, "UserPromptSubmit")
    monkeypatch.setenv(heard.HEARD_CALLER_ENV, "vera-hook")
    by = heard.mutator()
    assert (by["event"], by["caller"]) == ("UserPromptSubmit", "vera-hook")
    by = heard.mutator(event="tool", caller="ears.heard_wait")   # the argument wins
    assert (by["event"], by["caller"]) == ("tool", "ears.heard_wait")


def test_mutator_session_is_none_safe(base, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    assert heard.mutator()["session"] is None
    heard.save_cursor("s", heard.Cursor(1))
    assert "session" in raw_cursor()["by"] and raw_cursor()["by"]["session"] is None


# --- every write path stamps its own by, never the previous writer's -------------

def test_take_on_a_new_session_init_cursor_stamps_its_by(base):
    heard.turn("before the session", device="airpods")
    text, seq = heard.take("s", by=heard.mutator("PostToolBatch", "/hook/a"))
    assert text == "" and seq == 1
    assert_by(heard.load_cursor("s").by, "PostToolBatch", "/hook/a")


def test_pending_on_a_new_session_init_cursor_stamps_its_by(base):
    heard.turn("before the session", device="airpods")
    assert heard.pending("s", by=heard.mutator("tool", "ears.heard_wait")) == []
    assert_by(heard.load_cursor("s").by, "tool", "ears.heard_wait")


def test_take_with_rows_stamps_its_by_not_the_previous(base):
    heard.turn("one", device="airpods")
    p = heard.log_path()
    put_cursor({"seq": 1, "file": p.name, "offset": p.stat().st_size, "by": PREV})
    heard.turn("two", device="airpods")
    text, seq = heard.take("s", by=heard.mutator("PostToolBatch", "/hook/b"))
    assert text.endswith("] two") and seq == 2
    assert_by(heard.load_cursor("s").by, "PostToolBatch", "/hook/b")


def test_take_with_no_rows_but_a_moved_tail_stamps_its_by_not_the_previous(base):
    heard.turn("one", device="airpods")
    heard.turn("two", device="airpods")
    p = heard.log_path()
    put_cursor({"seq": 2, "file": p.name, "offset": 0, "by": PREV})  # a stale hint
    text, seq = heard.take("s", by=heard.mutator("PostToolBatch", "/hook/c"))
    assert text == "" and seq == 2
    cur = heard.load_cursor("s")
    assert cur.offset == p.stat().st_size                 # the hint re-synced
    assert_by(cur.by, "PostToolBatch", "/hook/c")


def test_advance_cursor_with_no_cursor_stamps_its_by(base):
    heard.turn("one", device="airpods")
    assert heard.advance_cursor("s", 1, by={"event": "other", "caller": "walter-catchup"})
    assert_by(heard.load_cursor("s").by, "other", "walter-catchup")


def test_advance_cursor_over_a_cursor_stamps_its_by_not_the_previous(base):
    heard.turn("one", device="airpods")
    heard.turn("two", device="airpods")
    p = heard.log_path()
    put_cursor({"seq": 1, "file": p.name, "offset": lines_end(p, 1), "by": PREV})
    assert heard.advance_cursor("s", 2, by={"event": "other", "caller": "walter-catchup"})
    cur = heard.load_cursor("s")
    assert cur.seq == 2
    assert_by(cur.by, "other", "walter-catchup")


def test_a_take_that_moves_nothing_writes_nothing(base):
    heard.turn("one", device="airpods")
    p = heard.log_path()
    put_cursor({"seq": 1, "file": p.name, "offset": p.stat().st_size, "by": PREV})
    assert heard.take("s", by=heard.mutator("PostToolBatch", "/hook")) == ("", 1)
    assert heard.load_cursor("s").by == PREV              # no write, so no new name


# --- run_hook records the RAW hook event --------------------------------------------

def test_run_hook_with_no_hook_event_name_records_other(base):
    heard.run_hook(json.dumps({"session_id": "s"}))
    assert heard.load_cursor("s").by["event"] == "other"   # never a guessed PostToolUse


def test_run_hook_records_the_hook_event_it_was_given(base):
    heard.run_hook(json.dumps({"session_id": "s", "hook_event_name": "PostToolBatch"}))
    by = heard.load_cursor("s").by
    assert by["event"] == "PostToolBatch"
    assert by["caller"] == heard._default_caller()
    heard.turn("hey claude", device="airpods")
    out = heard.run_hook(json.dumps({"session_id": "s", "hook_event_name": "UserPromptSubmit"}))
    assert json.loads(out)["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert heard.load_cursor("s").by["event"] == "UserPromptSubmit"


def test_run_hook_reply_still_defaults_to_post_tool_use(base):
    heard.run_hook(json.dumps({"session_id": "s"}))
    heard.turn("hey claude", device="airpods")
    out = heard.run_hook(json.dumps({"session_id": "s"}))
    assert json.loads(out)["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert heard.load_cursor("s").by["event"] == "other"


def test_run_hook_event_env_fills_in_only_when_the_input_names_none(base, monkeypatch):
    monkeypatch.setenv(heard.HEARD_EVENT_ENV, "UserPromptSubmit")
    heard.run_hook(json.dumps({"session_id": "s"}))
    assert heard.load_cursor("s").by["event"] == "UserPromptSubmit"
    heard.run_hook(json.dumps({"session_id": "t", "hook_event_name": "PostToolBatch"}))
    assert heard.load_cursor("t").by["event"] == "PostToolBatch"


# --- advance_cursor moves the byte hint with the seq (design section 5 (i)) --------

def five_turns():
    recs = [heard.turn(f"turn {i}", device="airpods") for i in range(1, 6)]
    return recs, heard.log_path()


def test_advance_cursor_moves_the_offset_to_the_end_of_the_newest_record_at_or_before(base):
    recs, p = five_turns()
    put_cursor({"seq": 1, "file": p.name, "offset": lines_end(p, 1)})
    assert heard.advance_cursor("s", recs[2]["seq"])
    cur = heard.load_cursor("s")
    assert (cur.seq, cur.file, cur.offset) == (3, p.name, lines_end(p, 3))


def test_advance_cursor_past_the_log_end_puts_the_hint_at_the_last_record(base):
    _, p = five_turns()
    put_cursor({"seq": 1, "file": p.name, "offset": lines_end(p, 1)})
    assert heard.advance_cursor("s", 99)
    cur = heard.load_cursor("s")
    assert (cur.seq, cur.offset) == (99, p.stat().st_size)


def test_advance_cursor_with_no_cursor_scans_for_the_hint(base):
    _, p = five_turns()
    assert heard.advance_cursor("s", 4)
    cur = heard.load_cursor("s")
    assert (cur.seq, cur.file, cur.offset) == (4, p.name, lines_end(p, 4))


def test_advance_cursor_with_no_log_keeps_no_hint(base):
    assert heard.advance_cursor("s", 4)
    cur = heard.load_cursor("s")
    assert (cur.seq, cur.file, cur.offset) == (4, None, 0)


def test_advance_cursor_never_moves_back_and_writes_nothing(base):
    _, p = five_turns()
    put_cursor({"seq": 4, "file": p.name, "offset": lines_end(p, 4), "by": PREV})
    assert heard.advance_cursor("s", 2)
    cur = heard.load_cursor("s")
    assert (cur.seq, cur.offset, cur.by) == (4, lines_end(p, 4), PREV)


def test_after_advance_cursor_the_next_take_moves_no_bytes(base):
    """The scoop fires at advance_cursor's write and names it; the next
    hook's take finds nothing to re-sync, so it neither moves the offset
    nor takes the name."""
    _, p = five_turns()
    put_cursor({"seq": 1, "file": p.name, "offset": lines_end(p, 1)})
    assert heard.advance_cursor("s", 5, by={"event": "other", "caller": "walter-catchup"})
    before = heard.load_cursor("s")
    assert before.offset == p.stat().st_size
    assert heard.take("s", by=heard.mutator("PostToolBatch", "/hook")) == ("", 5)
    after = heard.load_cursor("s")
    assert (after.offset, after.by) == (before.offset, before.by)
    assert after.by["caller"] == "walter-catchup"


def test_advance_cursor_reads_on_from_a_hint_in_yesterdays_file(base):
    from datetime import date, timedelta
    d = heard.log_dir()
    d.mkdir(parents=True)
    y = heard.log_path(date.today() - timedelta(days=1))
    y.write_text(json.dumps({"seq": 1, "kind": "turn", "text": "y"}) + "\n")
    heard.turn("today one", device="airpods")              # seq 2
    heard.turn("today two", device="airpods")              # seq 3
    t = heard.log_path()
    put_cursor({"seq": 1, "file": y.name, "offset": y.stat().st_size})
    assert heard.advance_cursor("s", 2)
    cur = heard.load_cursor("s")
    assert (cur.seq, cur.file, cur.offset) == (2, t.name, lines_end(t, 1))
