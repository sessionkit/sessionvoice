"""mouth timeline, the now and future rows (EARS-70 do-001; design.md D2, D3, D6, D7).

Every test runs on a scratch mouth dir, a scratch maildir and a scratch heard
dir (conftest.py fences HOME and the mail variables as well).
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

import mouth
from mouth import box, timeline

SRC = Path(__file__).resolve().parents[2] / "src"


@pytest.fixture
def d(tmp_path, monkeypatch):
    d = tmp_path / "mouth"
    monkeypatch.setenv("MOUTH_DIR", str(d))
    monkeypatch.setenv("MOUTH_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MOUTH_DEVICE", "airpods")
    monkeypatch.setenv("MOUTH_BACKEND", "silence")
    monkeypatch.setenv("VOICEMODE_AGENT", "vera")
    return d


@pytest.fixture
def heard_dir(tmp_path):
    h = tmp_path / "heard"
    h.mkdir()
    return h


def say(text, *, agent="vera", monkeypatch, d, **kw):
    monkeypatch.setenv("VOICEMODE_AGENT", agent)
    return mouth.say(text, d=d, spawn=False, **kw)


def heard_log(heard_dir: Path, *recs: dict) -> Path:
    p = heard_dir / f"heard_{datetime.now().strftime('%Y-%m-%d')}.jsonl"
    with p.open("a") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    return p


def iso(t: float) -> str:
    return datetime.fromtimestamp(t).astimezone().isoformat(timespec="milliseconds")


def write_playing(d: Path, item: dict) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    p = d / "playing.json"
    p.write_text(json.dumps({**box.public(item), "box_file": "x"}))
    return p


# -- the row shape (D3) --------------------------------------------------------

def test_every_row_key_is_present_and_text_only_with_words():
    r = timeline.row(when="future", kind="line", utt="u1")
    assert tuple(r) == timeline.ROW_KEYS
    assert r["seq"] is None and r["call_id"] is None and "text" not in r
    w = timeline.row(when="future", kind="line", words=True, text="hi")
    assert w["text"] == "hi" and tuple(w)[:-1] == timeline.ROW_KEYS
    with pytest.raises(ValueError):
        timeline.row(when="future", bogus=1)


def test_empty_queue_is_no_rows_and_writes_nothing(d):
    assert timeline.future(d) == []
    assert not d.exists(), "a reader never makes the mouth dir"


# -- future rows: play order, priority, supersede, retract, hold ---------------

def test_future_rows_in_play_order_with_priority(d, monkeypatch, heard_dir):
    a = say("first in", d=d, monkeypatch=monkeypatch)
    b = say("second in", agent="ivy", d=d, monkeypatch=monkeypatch)
    c = say("jumps the queue", agent="ivy", priority="next", d=d, monkeypatch=monkeypatch)
    n = say("numbered", priority=20, d=d, monkeypatch=monkeypatch)
    rows = timeline.future(d, heard_dir=heard_dir)
    assert [r["utt"] for r in rows] == [c["utt"], n["utt"], a["utt"], b["utt"]]
    assert [r["position"] for r in rows] == [1, 2, 3, 4]
    assert [r["who"] for r in rows] == ["ivy", "vera", "vera", "ivy"]
    assert [r["priority"] for r in rows] == ["next", 20, None, None]
    for r in rows:
        assert tuple(r) == timeline.ROW_KEYS, "no words without words=True"
        assert (r["when"], r["kind"], r["state"]) == ("future", "line", "queued")
        assert r["utt"] and r["mail_id"].startswith("<") and r["device"] == "airpods"
        assert r["queued_at"] and r["amended"] is False
        assert r["seq"] is None and r["start"] is None and r["dur_s"] is None


def test_the_order_shown_is_the_order_the_player_takes(d, monkeypatch, heard_dir):
    for i, p in enumerate([None, 30, "next", 10, None]):
        say(f"line {i}", priority=p, d=d, monkeypatch=monkeypatch)
    assert [r["utt"] for r in timeline.future(d, heard_dir=heard_dir)] == \
        [x["utt"] for x in box.Queue(d).lines(act=False)]


def test_amended_row_keeps_utt_and_position(d, monkeypatch, heard_dir):
    a = say("draft opener", d=d, monkeypatch=monkeypatch)
    b = say("someone else", agent="ivy", d=d, monkeypatch=monkeypatch)
    mouth.amend(a["utt"], "better opener", d=d)
    rows = timeline.future(d, words=True, heard_dir=heard_dir)
    assert [(r["utt"], r["position"]) for r in rows] == [(a["utt"], 1), (b["utt"], 2)]
    assert rows[0]["amended"] is True and rows[0]["text"] == "better opener"
    assert rows[0]["queued_at"] == a["requested_ts"], "an amend keeps its place in the queue"
    assert rows[1]["amended"] is False and rows[1]["text"] == "someone else"
    assert len(list((d / "box" / "new").iterdir())) == 3, "the reader files nothing"


def test_retracted_row_is_gone(d, monkeypatch, heard_dir):
    a = say("never mind", d=d, monkeypatch=monkeypatch)
    b = say("stays", agent="ivy", d=d, monkeypatch=monkeypatch)
    # a retract still sitting in new/: an empty supersede (what the queue resolves)
    box.drop(box.box_dir(d), {**a, "text": ""}, supersedes=a["mail_id"])
    rows = timeline.future(d, heard_dir=heard_dir)
    assert [r["utt"] for r in rows] == [b["utt"]] and rows[0]["position"] == 1
    assert len(list((d / "box" / "new").iterdir())) == 3, "act=False leaves the retract in new/"


def test_retract_verb_then_row_gone(d, monkeypatch, heard_dir):
    a = say("drop me", d=d, monkeypatch=monkeypatch)
    say("keep me", d=d, monkeypatch=monkeypatch)
    mouth.retract(a["utt"], d=d)
    assert [r["text"] for r in timeline.future(d, words=True, heard_dir=heard_dir)] == ["keep me"]


def test_hold_is_held_while_his_turn_is_open(d, monkeypatch, heard_dir):
    h = say("opener", hold="turn-end", expires_s=60, d=d, monkeypatch=monkeypatch)
    plain = say("not held", d=d, monkeypatch=monkeypatch)
    now = time.time()
    heard_log(heard_dir, {"kind": "partial", "source": "mic", "ts": iso(now - 1), "text": "so I was"})
    rows = {r["utt"]: r for r in timeline.future(d, heard_dir=heard_dir, now=now)}
    assert rows[h["utt"]]["state"] == "held" and rows[h["utt"]]["hold"] == "turn-end"
    assert rows[h["utt"]]["expires_at"] is not None
    assert rows[plain["utt"]]["state"] == "queued" and rows[plain["utt"]]["hold"] is None
    heard_log(heard_dir, {"kind": "turn", "source": "mic", "ts": iso(now), "text": "so I was saying"})
    rows = {r["utt"]: r for r in timeline.future(d, heard_dir=heard_dir, now=now + 0.5)}
    assert rows[h["utt"]]["state"] == "queued", "his turn ended: the hold line is next to play"


def test_hold_with_no_heard_log_is_queued(d, monkeypatch, heard_dir):
    say("opener", hold="turn-end", d=d, monkeypatch=monkeypatch)
    (r,) = timeline.future(d, heard_dir=heard_dir)
    assert (r["hold"], r["state"]) == ("turn-end", "queued")


def test_reply_columns_ride_from_the_mail(d, monkeypatch, heard_dir):
    item = say("answering", in_reply_to="<turn-1@host1>", d=d, monkeypatch=monkeypatch)
    plain = say("not a reply", d=d, monkeypatch=monkeypatch)
    rows = {r["utt"]: r for r in timeline.future(d, heard_dir=heard_dir)}
    assert rows[item["utt"]]["in_reply_to"] == "<turn-1@host1>"
    assert rows[plain["utt"]]["in_reply_to"] is None and rows[plain["utt"]]["call_id"] is None


# -- the now row (playing.json) -----------------------------------------------

def test_playing_row_then_future(d, monkeypatch, heard_dir):
    p = say("speaking now", agent="ivy", d=d, monkeypatch=monkeypatch)
    q = say("after it", d=d, monkeypatch=monkeypatch)
    x = box.Queue(d).find(p["utt"])
    box.Queue(d).take(x)                       # what the player does, then playing.json
    pj = write_playing(d, x)
    rows = timeline.future(d, words=True, heard_dir=heard_dir)
    assert [(r["when"], r["utt"]) for r in rows] == [("now", p["utt"]), ("future", q["utt"])]
    now = rows[0]
    assert (now["state"], now["who"], now["kind"], now["text"]) == ("playing", "ivy", "line", "speaking now")
    assert now["position"] is None and now["mail_id"] == p["mail_id"]
    assert now["start"] == iso(pj.stat().st_mtime)
    assert rows[1]["position"] == 1
    assert list(rows[0])[:-1] == list(timeline.ROW_KEYS)


def test_a_read_racing_the_take_shows_the_line_once(d, monkeypatch, heard_dir):
    p = say("taken", d=d, monkeypatch=monkeypatch)
    write_playing(d, box.Queue(d).find(p["utt"]))  # playing.json written, new/ not yet moved
    rows = timeline.future(d, heard_dir=heard_dir)
    assert [(r["when"], r["utt"]) for r in rows] == [("now", p["utt"])]


@pytest.mark.parametrize("body", ["", "{not json", "[1, 2]"])
def test_a_torn_or_odd_playing_json_is_no_now_row(d, body):
    d.mkdir(parents=True)
    (d / "playing.json").write_text(body)
    assert timeline.now_row(d) is None


def test_utt_on_every_line_row(d, monkeypatch, heard_dir):
    p = say("one", d=d, monkeypatch=monkeypatch)
    say("two", d=d, monkeypatch=monkeypatch)
    say("three", hold="turn-end", d=d, monkeypatch=monkeypatch)
    x = box.Queue(d).find(p["utt"])
    box.Queue(d).take(x)
    write_playing(d, x)
    rows = timeline.future(d, heard_dir=heard_dir)
    assert len(rows) == 3 and all(r["kind"] == "line" and r["utt"] for r in rows)


# -- read-only (D7) and player-free (D6) --------------------------------------

def _tree(root: Path) -> dict:
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None, p.stat().st_mtime_ns)
            for p in sorted(root.rglob("*"))}


def test_the_reader_changes_nothing(d, monkeypatch, heard_dir, tmp_path):
    a = say("one", d=d, monkeypatch=monkeypatch)
    say("held", hold="turn-end", d=d, monkeypatch=monkeypatch)
    mouth.amend(a["utt"], "one, amended", d=d)
    x = box.Queue(d).find(say("playing", d=d, monkeypatch=monkeypatch)["utt"])
    write_playing(d, x)
    heard_log(heard_dir, {"kind": "partial", "source": "mic", "ts": iso(time.time()), "text": "hm"})
    before = _tree(tmp_path)
    for words in (False, True):
        timeline.future(d, words=words, heard_dir=heard_dir)
        timeline.now_row(d, words=words)
    assert _tree(tmp_path) == before


def test_the_reader_imports_no_player(d, monkeypatch, heard_dir, tmp_path):
    a = say("queued", hold="turn-end", d=d, monkeypatch=monkeypatch)
    say("supersede and retract resolve without the player", d=d, monkeypatch=monkeypatch)
    mouth.amend(a["utt"], "amended", d=d)
    box.drop(box.box_dir(d), {**a, "text": ""}, supersedes="<gone@host1.mouth>")
    write_playing(d, {**a, "utt": "playing0001", "mail_id": "<p@host1.mouth>"})
    code = (
        "import json, sys\n"
        "from mouth import timeline\n"
        "rows = timeline.future(words=True, heard_dir=__import__('pathlib').Path(sys.argv[1]))\n"
        "print(json.dumps({'player': 'mouth.player' in sys.modules, 'rows': len(rows)}))\n"
    )
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    out = subprocess.run([sys.executable, "-c", code, str(heard_dir)], env=env,
                         capture_output=True, text=True, check=True)
    got = json.loads(out.stdout)
    assert got == {"player": False, "rows": 3}, out.stderr
