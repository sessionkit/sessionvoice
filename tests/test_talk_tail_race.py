"""Talk's live tail starts where backfill stopped reading (EARS-538).

/events sends backfill(), which reads today's heard log, and then opens the
log again for the live tail. A row written between that read and the tail's
start must be streamed exactly once: before the fix the tail started at the
log's end at that moment, so such a row was sent in neither (a lost turn).

The interleaving is made inside the test, with no timing luck and no load:
server.backfill is wrapped so a writer lands after the real backfill has read
the log and before events() opens the tail. A sentinel row written once the
stream is live (its first "state" event comes after the tail is open) marks
the end of what to collect. Every wait is bounded: a lost row is a red, never
a hang.
"""
import json, os, socket, time
from unittest import mock

import pytest

from ears import heard
from talk import server
from tests._anchor import ANCHOR_ISO
from tests.test_talk_generalise import SSE, env, live  # noqa: F401  (fixtures)

CEILING = 10.0   # seconds: the most the test waits for the sentinel


def wrap_backfill(after_read):
    """server.backfill, with after_read() run once the real backfill has read
    the log: a writer landing between backfill's read and the tail's start."""
    real = server.backfill

    def wrapped(path, n, debug=False):
        rows = real(path, n, debug)
        after_read()
        return rows
    return mock.patch.object(server, "backfill", wrapped)


def stream(port, before_tail=None):
    """Open /events and return (backfill rows, live rows up to the sentinel).

    before_tail(), when given, runs once the stream is live, before the
    sentinel is written."""
    sse = SSE(port)
    try:
        assert sse.next()[0] == "config"
        name, backfilled = sse.next()
        assert name == "backfill"
        rows, sentinel, deadline = [], None, time.monotonic() + CEILING
        while True:
            if time.monotonic() > deadline:
                pytest.fail(f"no sentinel within {CEILING}s; live rows: {rows}")
            try:
                name, data = sse.next()
            except (socket.timeout, TimeoutError):
                pytest.fail(f"the stream went quiet before the sentinel; live rows: {rows}")
            if name == "state":
                if sentinel is None:              # the tail is open: now mark the end
                    if before_tail:
                        before_tail()
                    sentinel = heard.turn("sentinel")["seq"]
                continue
            assert name is None, name
            if data.get("seq") == sentinel:
                return backfilled, rows
            rows.append(data)
    finally:
        sse.close()


def texts(rows):
    return [r.get("text") for r in rows]


def test_a_row_written_between_backfill_and_tail_is_streamed_once(env, live):
    heard.turn("before")
    with wrap_backfill(lambda: heard.turn("in the window")):
        backfilled, rows = stream(live)
    assert texts(backfilled) == ["before"]
    assert texts(rows).count("in the window") == 1, f"live rows: {rows}"


def test_the_last_backfilled_row_is_not_streamed_again(env, live):
    heard.turn("one")
    heard.turn("two")
    backfilled, rows = stream(live)
    assert texts(backfilled) == ["one", "two"]
    assert "two" not in texts(rows) and "one" not in texts(rows), f"live rows: {rows}"
    assert rows == [], f"nothing new was written, yet live sent: {rows}"


def test_a_half_written_line_at_backfill_is_streamed_once_finished(env, live):
    """backfill reads while a writer is mid-line (no newline yet); the writer
    finishes after the read. The row is streamed exactly once, whole."""
    heard.turn("before")
    path = server.today_log()
    line = json.dumps({"ts": ANCHOR_ISO, "seq": 1000, "kind": "turn",
                       "text": "half then whole", "source": "mic", "final": True,
                       "v": heard.SCHEMA}) + "\n"
    head, tail = line[:len(line) // 2], line[len(line) // 2:]
    with open(path, "a") as f:
        f.write(head)

    def finish():
        with open(path, "a") as f:
            f.write(tail)
    with wrap_backfill(finish):
        backfilled, rows = stream(live)
    assert texts(backfilled) == ["before"]
    assert texts(rows).count("half then whole") == 1, f"live rows: {rows}"
    assert [{k: v for k, v in r.items() if k != "off"} for r in rows if r.get("seq") == 1000] == [
        {"ts": ANCHOR_ISO, "seq": 1000, "kind": "turn",
         "text": "half then whole", "source": "mic", "final": True}]


def test_a_day_turn_before_the_tail_opens_starts_the_new_day_at_0(env, live, tmp_path):
    """backfill reads yesterday's log; the day turns before the tail opens. Its
    offset belongs to yesterday's file: today's rows are streamed from byte 0,
    even when today's file is shorter than that offset."""
    yesterday = str(tmp_path / "yesterday.jsonl")
    with open(yesterday, "w") as f:
        f.write(json.dumps({"ts": ANCHOR_ISO, "seq": 1, "kind": "turn", "text": "x" * 4000,
                            "source": "mic", "final": True, "v": heard.SCHEMA}) + "\n")
    today = server.today_log()
    day = {"log": yesterday}

    def turn_the_day():
        day["log"] = today
        heard.turn("the new day")                 # far shorter than yesterday's offset
    with mock.patch.object(server, "today_log", lambda: day["log"]), wrap_backfill(turn_the_day):
        backfilled, rows = stream(live)
    assert texts(backfilled) == ["x" * 4000]
    assert texts(rows).count("the new day") == 1, f"live rows: {rows}"


# -- backfill's end: the byte the tail starts at ------------------------------

def write(path, data):
    with open(path, "wb") as f:
        f.write(data)


def row(seq, text):
    return (json.dumps({"ts": ANCHOR_ISO, "seq": seq, "kind": "turn", "text": text,
                        "source": "mic", "final": True, "v": heard.SCHEMA}) + "\n").encode()


def test_backfill_end_is_after_the_last_whole_line(env, tmp_path):
    path = str(tmp_path / "log.jsonl")
    a, b = row(1, "a"), row(2, "b")
    write(path, a + b)
    rows = server.backfill(path, 10)
    assert isinstance(rows, list) and texts(rows) == ["a", "b"]
    assert rows.end == len(a + b) == rows[-1]["off"]
    assert [r["off"] for r in rows] == [len(a), len(a + b)]


def test_backfill_leaves_a_half_written_line_to_the_tail(env, tmp_path):
    """Even a trailing piece that parses as a row is not sent until its newline lands."""
    path = str(tmp_path / "log.jsonl")
    a, b = row(1, "a"), row(2, "b")
    write(path, a + b[:-1])                        # b whole but for its newline
    rows = server.backfill(path, 10)
    assert texts(rows) == ["a"] and rows.end == len(a)
    write(path, a + b[:len(b) // 2])
    rows = server.backfill(path, 10, debug=True)
    assert texts(rows) == ["a"] and rows.end == len(a)


def test_backfill_end_with_no_whole_line_or_no_file(env, tmp_path):
    path = str(tmp_path / "log.jsonl")
    assert server.backfill(path, 10) == [] and server.backfill(path, 10).end == 0
    write(path, row(1, "a")[:10])
    rows = server.backfill(path, 10)
    assert rows == [] and rows.end == 0


def test_backfill_end_counts_rows_it_does_not_draw(env, tmp_path):
    """A row the page never shows still lies before the tail's start: not re-read."""
    path = str(tmp_path / "log.jsonl")
    a, hidden = row(1, "a"), (json.dumps({"kind": "nothing-drawn"}) + "\n").encode()
    write(path, a + hidden)
    rows = server.backfill(path, 10)
    assert texts(rows) == ["a"] and rows.end == len(a + hidden)


# -- the day turn: yesterday's file is drained before the tail follows today's -----

def append(path, data):
    with open(path, "ab") as f:
        f.write(data)


class Day:
    """server.today_log, turned by the test.

    turn(last) makes the stream's NEXT look at the day run last() (the writer's
    final bytes in yesterday's file) and then answer today's log. Both happen
    inside the one call the tail makes at the top of a pass, so no read of
    yesterday's file can fall between the write and the turn: the turn is the
    next thing the tail sees, every run, with no timing involved."""

    def __init__(self, yesterday, today):
        self.log, self.today, self.pending = yesterday, today, None

    def turn(self, last):
        self.pending = last

    def __call__(self):
        if self.pending:
            last, self.pending = self.pending, None
            last()
            self.log = self.today
        return self.log


def the_day_turns(port, tmp_path, yesterday_bytes, last_bytes):
    """Yesterday's log holds yesterday_bytes when the stream opens; once the tail
    is live, "the new day" is written to today's log and the day turns, with
    last_bytes appended to yesterday's log at the turn. Returns (yesterday's
    path, backfill rows, live rows up to the sentinel)."""
    yesterday = str(tmp_path / "yesterday.jsonl")
    if yesterday_bytes is not None:
        write(yesterday, yesterday_bytes)
    day = Day(yesterday, server.today_log())

    def turn_the_day():
        heard.turn("the new day")      # today's log: the tail is still on yesterday's
        day.turn(lambda: append(yesterday, last_bytes))
    with mock.patch.object(server, "today_log", day):
        backfilled, rows = stream(port, before_tail=turn_the_day)
    return yesterday, backfilled, rows


def test_a_row_written_to_yesterday_at_the_turn_is_streamed_once(env, live, tmp_path):
    """The writer's last row lands in yesterday's log after the tail's last read
    of it; the day turns before the next. The row is streamed once, before the
    new day's, with its offset in yesterday's file."""
    first = row(1001, "backfilled")
    yesterday, backfilled, rows = the_day_turns(live, tmp_path, first, row(1002, "yesterday's last"))
    assert texts(backfilled) == ["backfilled"]
    assert texts(rows) == ["yesterday's last", "the new day"], f"live rows: {rows}"
    assert rows[0]["off"] == os.path.getsize(yesterday)


def test_a_line_half_read_before_the_turn_is_finished_and_streamed(env, live, tmp_path):
    """The tail's first read takes half a line into its buffer (backfill leaves
    it); the writer finishes it at the turn, then starts a row it never ends.
    The finished row is streamed once, whole. The unended one is not streamed,
    though it parses: the writer has moved to the new day and will not end it."""
    first, half_read = row(1001, "backfilled"), row(1002, "finished at the turn")
    cut = len(half_read) // 2
    never_ended = row(1003, "never ended")[:-1]          # whole but for its newline
    yesterday, backfilled, rows = the_day_turns(
        live, tmp_path, first + half_read[:cut], half_read[cut:] + never_ended)
    assert texts(backfilled) == ["backfilled"]
    assert texts(rows) == ["finished at the turn", "the new day"], f"live rows: {rows}"
    assert rows[0]["off"] == len(first + half_read)
    assert [{k: v for k, v in r.items() if k != "off"} for r in rows[:1]] == [
        {"ts": ANCHOR_ISO, "seq": 1002, "kind": "turn",
         "text": "finished at the turn", "source": "mic", "final": True}]


def test_a_turn_with_no_file_for_yesterday_follows_the_new_day(env, live, tmp_path):
    """No log for yesterday when the stream opened (nothing to drain): the turn
    still follows the new day's log from byte 0."""
    _, backfilled, rows = the_day_turns(live, tmp_path, None, b"")
    assert backfilled == []
    assert texts(rows) == ["the new day"], f"live rows: {rows}"
