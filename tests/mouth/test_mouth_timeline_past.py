"""mouth timeline, the past rows and the call keying (EARS-70 do-002; design.md D2, D4, D5, D9).

Every test runs on a scratch mouth dir and a scratch heard dir (conftest.py fences
HOME and the mail variables as well). Heard records are written by hand, in the
shape ``ears.heard.append`` writes, so each test states exactly what was heard.
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import mouth
from mouth import box, timeline

SRC = Path(__file__).resolve().parents[2] / "src"
T0 = time.time() - 3600.0  # an hour ago: every record below is placed from here


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
def h(tmp_path):
    h = tmp_path / "heard"
    h.mkdir()
    return h


def iso(t: float) -> str:
    return datetime.fromtimestamp(t).astimezone().isoformat(timespec="milliseconds")


class Log:
    """A heard log written by hand: ``seq`` counts up, like the writer's."""

    def __init__(self, h: Path, day: str = None):
        self.h, self.seq = h, 1000
        self.day = day or datetime.fromtimestamp(T0).strftime("%Y-%m-%d")

    def put(self, kind: str, at: float, *, day: str = None, raw: bytes = None, **kw) -> dict:
        self.seq += 1
        r = {"ts": iso(T0 + at), "seq": self.seq, "kind": kind, "v": 1, **kw}
        p = self.h / f"heard_{day or self.day}.jsonl"
        with p.open("ab") as f:
            f.write(raw if raw is not None else (json.dumps(r) + "\n").encode())
        return r

    def turn(self, at, dur=2.0, device="airpods", **kw):
        return self.put("turn", at, source="mic", device=device, text=kw.pop("text", "his words"),
                        t0=1.0, t1=1.0 + dur, final=True, **kw)

    def line(self, at, played=3.0, utt=None, agent="vera", device="airpods", reason="done", **kw):
        utt = utt or f"u{self.seq + 1:011d}"
        common = {"utt": utt, "agent": agent, "device": device, "source": "mouth",
                  "mail_id": f"<{utt}@host1.mouth>", "requested_ts": iso(T0 + at - 1), **kw}
        self.put("saying", at, text="the whole line", final=False, **common)
        return self.put("said", at + played, played_s=played, cut=reason != "done",
                        text_played_est="the whole line", reason=reason, final=True, **common)

    def unplayed(self, at, utt="unplayed0001", reason="retracted", agent="ivy", **kw):
        return self.put("said", at, utt=utt, agent=agent, device="airpods", source="mouth",
                        played_s=0.0, cut=True, text_played_est="", reason=reason, **kw)


def doc(d, h, *, at=None, **kw):
    """The document as of ``T0 + at`` (default: just after the last record)."""
    return timeline.document(d, h, now=T0 + (at if at is not None else 3000.0), **kw)


def past(document) -> list:
    return [r for r in document["rows"] if r["when"] == "past"]


def say(text, *, agent="vera", monkeypatch, d, **kw):
    monkeypatch.setenv("VOICEMODE_AGENT", agent)
    return mouth.say(text, d=d, spawn=False, **kw)


# -- saying/said pairing (D2) ---------------------------------------------------

def test_saying_and_said_pair_by_utt(d, h):
    log = Log(h)
    said = log.line(100.0, played=17.25, utt="707011ae6b59")
    (r,) = past(doc(d, h, at=200.0, words=True))
    assert (r["kind"], r["who"], r["utt"], r["state"], r["cut"]) == ("line", "vera", "707011ae6b59", "done", False)
    assert r["start"] == iso(T0 + 100.0), "a line starts at its saying (the first frame)"
    assert r["dur_s"] == 17.25 and r["seq"] == said["seq"]
    assert r["mail_id"] == "<707011ae6b59@host1.mouth>" and r["queued_at"] == iso(T0 + 99.0)
    assert r["text"] == "the whole line", "--words: the text as played"
    assert tuple(r)[:-1] == timeline.ROW_KEYS


def test_a_cut_line_has_the_said_reason_and_cut(d, h):
    Log(h).line(100.0, played=2.5, reason="barge-in")
    (r,) = past(doc(d, h, at=200.0))
    assert (r["state"], r["cut"], r["dur_s"]) == ("barge-in", True, 2.5)
    assert "text" not in r, "no words without --words"


def test_a_said_with_no_saying_never_played(d, h):
    log = Log(h)
    log.turn(50.0)
    log.unplayed(120.0, reason="expired")
    rows = past(doc(d, h, at=200.0, words=True))
    r = rows[-1]
    assert (r["kind"], r["who"], r["state"], r["dur_s"]) == ("line", "ivy", "expired", 0.0)
    assert r["start"] == iso(T0 + 120.0) and r["text"] == ""


def test_a_saying_with_no_said_yet_is_not_a_past_row(d, h):
    log = Log(h)
    log.turn(50.0)
    log.put("saying", 60.0, utt="playing0001", agent="vera", device="airpods", text="still going")
    rows = past(doc(d, h, at=70.0))
    assert [r["kind"] for r in rows] == ["turn"], "the playing line is the now row's, not a past one"


def test_a_line_that_ended_between_the_reads_shows_once(d, h, monkeypatch):
    p = say("just ended", d=d, monkeypatch=monkeypatch)
    x = box.Queue(d).find(p["utt"])
    box.Queue(d).take(x)
    d.mkdir(parents=True, exist_ok=True)
    (d / "playing.json").write_text(json.dumps(box.public(x)))  # not yet removed
    Log(h).line(100.0, utt=p["utt"])
    rows = doc(d, h, at=200.0)["rows"]
    assert [(r["when"], r["utt"]) for r in rows] == [("past", p["utt"])]
    # --future (NOTIF-10's read) builds no past rows, and must drop it all the same
    assert doc(d, h, at=200.0, which="future")["rows"] == []
    # and a --last that trims its past row away does not bring it back as playing
    Log(h).turn(150.0)
    rows = doc(d, h, at=200.0, last=1)["rows"]
    assert [(r["when"], r["kind"]) for r in rows] == [("past", "turn")]


# -- turns (D2, D3) ---------------------------------------------------------------

def test_a_turn_starts_t1_minus_t0_before_its_ts_and_has_no_guessed_speaker(d, h):
    t = Log(h).turn(100.0, dur=6.16, text="so I was saying")
    (r,) = past(doc(d, h, at=200.0, words=True))
    assert (r["kind"], r["seq"], r["dur_s"], r["device"]) == ("turn", t["seq"], 6.16, "airpods")
    assert r["start"] == iso(T0 + 100.0 - 6.16)
    assert r["who"] is None, "no speaker label: who is null, never a guessed 'theo'"
    assert r["utt"] is None and r["state"] is None and r["text"] == "so I was saying"


def test_a_turn_names_its_speaker_from_the_sidecar_or_the_record(d, h):
    log = Log(h)
    a = log.turn(100.0)
    b = log.turn(110.0)
    c = log.turn(120.0, speaker="alice")
    e = log.turn(130.0)
    with (h / f"speakers_{log.day}.jsonl").open("w") as f:
        f.write(json.dumps({"seq": a["seq"], "speaker": "theo"}) + "\n")
        f.write(json.dumps({"seq": b["seq"], "speaker": "mixed", "fracs": {"alice": 0.7, "theo": 0.3}}) + "\n")
        f.write(json.dumps({"seq": c["seq"], "speaker": "theo"}) + "\n")
        f.write(json.dumps({"seq": e["seq"], "speaker": "silent"}) + "\n")
    who = [r["who"] for r in past(doc(d, h, at=200.0))]
    assert who == ["theo", "alice?", "alice", None], "the record's own label wins; silent is no label"


# -- the window: room + 600 s linger (D4) -----------------------------------------

def test_a_gap_over_600_s_splits_the_call(d, h):
    log = Log(h)
    first = log.turn(10.0)
    log.line(20.0)
    second = log.turn(20.0 + 3.0 + 600.5 + 2.0)  # 600.5 s of silence before it starts
    log.line(700.0)
    out = doc(d, h, at=800.0)
    assert out["call"]["id"] == f"window:local:{second['seq']}"
    assert [r["seq"] for r in past(out)][0] == second["seq"]
    old = doc(d, h, at=800.0, call=f"window:local:{first['seq']}")
    assert old["call"]["open"] is False and len(past(old)) == 2


def test_a_gap_of_600_s_does_not_split(d, h):
    log = Log(h)
    first = log.turn(10.0)
    log.turn(10.0 + 600.0 + 2.0)  # starts exactly 600 s after the last one ended
    out = doc(d, h, at=700.0)
    assert out["call"]["id"] == f"window:local:{first['seq']}" and len(past(out)) == 2


def test_partials_and_events_never_bridge_a_gap(d, h):
    log = Log(h)
    log.turn(10.0)
    for k in range(1, 20):  # a heartbeat and a partial every minute: still silence
        log.put("event", 10.0 + 60 * k, event="heartbeat", source="mic", device="airpods")
        log.put("partial", 11.0 + 60 * k, text="hm", source="mic", device="airpods", final=False)
    second = log.turn(1300.0)
    out = doc(d, h, at=1400.0)
    assert out["call"]["id"] == f"window:local:{second['seq']}" and len(past(out)) == 1


def test_a_device_change_does_not_split_the_call(d, h):
    log = Log(h)
    first = log.turn(10.0, device="airpods")
    log.line(20.0, device="airpods")
    log.turn(100.0, device="MacBook Pro Microphone")  # the AirPods dropped out
    log.line(110.0, device="MacBook Pro Speakers")
    log.turn(200.0, device="airpods")
    out = doc(d, h, at=300.0)
    assert out["call"]["id"] == f"window:local:{first['seq']}"
    assert len(past(out)) == 5 and out["call"]["room"] == "local"


def test_a_delta_call_is_its_own_call(d, h):
    log = Log(h)
    local = log.turn(10.0)
    on_call = log.turn(20.0, device="call:1196", speaker="alice")
    log.line(30.0, device="call:1196")
    log.turn(40.0)
    for k in range(1, 15):  # the Delta call talks through a local silence ...
        log.turn(40.0 + 60 * k, device="call:1196")
    later = log.turn(1300.0)  # ... which still splits the local room
    out = doc(d, h, at=1400.0)
    assert out["call"]["id"] == f"window:local:{later['seq']}" and len(past(out)) == 1
    call = doc(d, h, at=1400.0, call=f"window:call:1196:{on_call['seq']}")
    assert call["call"]["room"] == "call:1196"
    assert all(r["device"] == "call:1196" for r in past(call)) and len(past(call)) == 16
    old = doc(d, h, at=1400.0, call=f"window:local:{local['seq']}")
    assert [r["device"] for r in past(old)] == ["airpods", "airpods"]


def test_the_call_id_is_its_first_seq_and_stays_while_it_grows(d, h):
    log = Log(h)
    first = log.turn(10.0)
    ids = []
    for k in range(5):
        log.line(20.0 + 30 * k)
        ids.append(doc(d, h, at=200.0)["call"]["id"])
    assert set(ids) == {f"window:local:{first['seq']}"}


# -- the current call (D4) ----------------------------------------------------------

def test_an_open_call_takes_the_queued_lines(d, h, monkeypatch):
    log = Log(h)
    first = log.turn(time.time() - T0 - 30.0)  # 30 s ago
    q = say("my opener", d=d, monkeypatch=monkeypatch)
    out = timeline.document(d, h)
    assert out["call"]["id"] == f"window:local:{first['seq']}" and out["call"]["open"] is True
    assert [(r["when"], r["utt"]) for r in out["rows"]] == [("past", None), ("future", q["utt"])]


def test_a_closed_call_with_nothing_queued_is_still_shown_closed(d, h):
    first = Log(h).turn(10.0)
    out = doc(d, h, at=10.0 + 601.0)
    assert out["call"]["id"] == f"window:local:{first['seq']}" and out["call"]["open"] is False
    assert out["call"]["started"] == iso(T0 + 8.0) and out["call"]["last"] == iso(T0 + 10.0)


def test_queued_lines_after_a_closed_call_are_a_new_call(d, h, monkeypatch):
    Log(h).turn(10.0)  # an hour ago
    q = say("good morning", d=d, monkeypatch=monkeypatch)
    out = timeline.document(d, h)
    assert out["call"] == {"id": "window:local:new", "key": "window", "room": "local",
                           "started": None, "last": None, "open": True}
    assert [(r["when"], r["utt"]) for r in out["rows"]] == [("future", q["utt"])]
    assert timeline.document(d, h, call="window:local:new")["rows"] == out["rows"]


def test_nothing_heard_and_nothing_queued_is_an_empty_closed_call(d, h):
    out = timeline.document(d, h)
    assert out["v"] == 1 and out["rows"] == []
    assert out["call"]["id"] == "window:local:new" and out["call"]["open"] is False


def test_a_line_queued_to_a_delta_call_is_not_in_the_room(d, h, monkeypatch):
    Log(h).turn(time.time() - T0 - 30.0)
    local = say("to the room", d=d, monkeypatch=monkeypatch)
    say("to alice", device="call:1196", d=d, monkeypatch=monkeypatch)
    rows = timeline.document(d, h, which="future")["rows"]
    assert [r["utt"] for r in rows] == [local["utt"]]


def test_future_and_past_filters(d, h, monkeypatch):
    log = Log(h)
    log.turn(time.time() - T0 - 30.0)
    log.line(time.time() - T0 - 20.0)
    q = say("next", d=d, monkeypatch=monkeypatch)
    fut = timeline.document(d, h, which="future")["rows"]
    pst = timeline.document(d, h, which="past")["rows"]
    assert [r["utt"] for r in fut] == [q["utt"]] and all(r["when"] == "future" for r in fut)
    assert [r["kind"] for r in pst] == ["turn", "line"] and all(r["when"] == "past" for r in pst)
    with pytest.raises(ValueError):
        timeline.document(d, h, which="later")


def test_last_keeps_the_newest_past_rows(d, h):
    log = Log(h)
    for k in range(30):
        log.turn(10.0 + 10 * k)
    out = doc(d, h, at=400.0)
    assert len(past(out)) == 20 and past(out)[-1]["seq"] == log.seq
    assert len(past(doc(d, h, at=400.0, last=5))) == 5
    assert len(past(doc(d, h, at=400.0, last=0))) == 30


@pytest.mark.parametrize("bad", ["window:local:999999", "window:local:abc", "window:den:1001",
                                 "window:", "<no-such-call@host1>", "window:local:new"])
def test_an_unknown_call_id_is_a_lookup_error(d, h, bad):
    Log(h).turn(10.0)
    with pytest.raises(LookupError):
        doc(d, h, at=100.0, call=bad)


def test_a_window_id_mid_call_is_not_a_call(d, h):
    log = Log(h)
    log.turn(10.0)
    second = log.turn(20.0)
    with pytest.raises(LookupError):
        doc(d, h, at=100.0, call=f"window:local:{second['seq']}")


# -- Call-ID: one seam, same document (D5) ------------------------------------------

def _keys(document) -> tuple:
    return (tuple(document), tuple(document["call"]),
            tuple(tuple(r) for r in document["rows"]), tuple(r["when"] for r in document["rows"]))


def _conversation(h, **kw):
    log = Log(h)
    log.turn(10.0, **kw)
    log.line(20.0, **kw)
    log.unplayed(30.0, **kw)
    log.turn(40.0, device="MacBook Pro Microphone", **kw)
    return log


def test_a_call_id_log_has_the_window_documents_keys(d, h, tmp_path):
    _conversation(h)
    window = doc(d, h, at=100.0, words=True)
    h2 = tmp_path / "heard2"
    h2.mkdir()
    _conversation(h2, call_id="<call-1@host1>")
    by_id = doc(d, h2, at=100.0, words=True)
    assert _keys(window) == _keys(by_id) and len(window["rows"]) == 4
    assert (window["call"]["key"], by_id["call"]["key"]) == ("window", "call-id")
    assert by_id["call"]["id"] == "<call-1@host1>"
    strip = lambda x: {**x, "call": {**x["call"], "key": None, "id": None},
                       "rows": [{**r, "call_id": None} for r in x["rows"]]}
    assert strip(window) == strip(by_id), "only call.key, call.id (and the rows' call_id) differ"
    assert doc(d, h2, at=100.0, call="<call-1@host1>", words=True) == by_id


def test_call_of_is_the_one_seam():
    assert timeline.call_of({"kind": "turn"}, "window:local:7") == ("window", "window:local:7")
    assert timeline.call_of({"call_id": "<c@host1>"}, "window:local:7") == ("call-id", "<c@host1>")
    assert timeline.call_of({"call_id": None}, "window:local:7") == ("window", "window:local:7")


def test_half_window_half_call_id_is_the_newest_call_id(d, h):
    log = Log(h)
    log.turn(10.0)
    log.turn(20.0, call_id="<call-2@host1>")
    log.line(30.0, call_id="<call-2@host1>")
    out = doc(d, h, at=100.0)
    assert out["call"]["key"] == "call-id" and len(past(out)) == 2
    assert all(r["call_id"] == "<call-2@host1>" for r in past(out))


def test_room_of():
    assert [timeline.room_of(x) for x in ("call:1196", "call", "airpods", "MacBook Pro Speakers", None)] \
        == ["call:1196", "call", "local", "local", "local"]


# -- reply keys before EARS-62 (D9) and the write path (D2) ---------------------------

def test_past_rows_without_reply_keys_carry_them_as_null(d, h):
    _conversation(h)
    for r in past(doc(d, h, at=100.0)):
        assert "in_reply_to" in r and "call_id" in r
        assert r["in_reply_to"] is None and r["call_id"] is None


def test_past_rows_carry_reply_keys_when_the_records_do(d, h):
    Log(h).line(10.0, in_reply_to="<turn-9@host1>")
    (r,) = past(doc(d, h, at=100.0))
    assert r["in_reply_to"] == "<turn-9@host1>"


def test_the_player_writes_the_reply_keys_on_saying_and_said(tmp_path, monkeypatch):
    from mouth import player

    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm"))
    item = {"utt": "abcdef012345", "agent": "vera", "device": "airpods",
            "in_reply_to": "<turn-9@host1>", "call_id": "<call-1@host1>"}
    rec = player.said_unplayed(item, "retracted")
    assert (rec["in_reply_to"], rec["call_id"]) == ("<turn-9@host1>", "<call-1@host1>")
    plain = player.said_unplayed({"utt": "abcdef012346", "agent": "vera"}, "expired")
    assert "in_reply_to" not in plain and "call_id" not in plain, "additive: absent when unset"
    hd = tmp_path / "vm" / "logs" / "conversations"
    (r,) = timeline.document(tmp_path / "mouth", hd, which="past", call="<call-1@host1>")["rows"]
    assert (r["utt"], r["in_reply_to"], r["call_id"]) == ("abcdef012345", "<turn-9@host1>", "<call-1@host1>")
    # the plain line is not in that call (D5): it stays in its window, with null keys
    first = json.loads(next(iter((hd).glob("heard_*.jsonl"))).read_text().splitlines()[0])
    (r,) = timeline.document(tmp_path / "mouth", hd, which="past",
                             call=f"window:local:{first['seq']}")["rows"]
    assert (r["utt"], r["in_reply_to"], r["call_id"]) == ("abcdef012346", None, None)


# -- reading backwards ---------------------------------------------------------------

def test_a_line_being_written_and_torn_lines_are_skipped(d, h):
    log = Log(h)
    log.turn(10.0)
    log.put("turn", 0, raw=b'{"ts": "torn", "kind": "turn"\n')
    log.put("turn", 0, raw=b"not json at all, said \"turn\"\n")
    log.turn(20.0)
    # whole json, but no newline yet: a write in flight, never trusted (as heard._scan)
    log.put("turn", 0, raw=json.dumps({"ts": iso(T0 + 30), "seq": 9999, "kind": "turn",
                                        "device": "airpods", "t0": 0, "t1": 1}).encode())
    rows = past(doc(d, h, at=100.0))
    assert len(rows) == 2 and 9999 not in [r["seq"] for r in rows]


def test_chunk_boundaries_lose_no_line(d, h, monkeypatch):
    log = Log(h)
    for k in range(40):
        log.turn(10.0 + 5 * k, text="x" * (37 * k))
    whole = doc(d, h, at=400.0, last=0)
    monkeypatch.setattr(timeline, "_CHUNK", 97)
    assert doc(d, h, at=400.0, last=0) == whole and len(past(whole)) == 40


def test_a_call_across_midnight_reads_yesterdays_file(d, h):
    log = Log(h)
    first = log.turn(10.0, day="2030-10-03")
    log.line(20.0, day="2030-10-03")
    log.turn(100.0, day="2030-10-04")
    out = doc(d, h, at=200.0)
    assert out["call"]["id"] == f"window:local:{first['seq']}" and len(past(out)) == 3
    only_today = doc(d, h, at=200.0, max_files=1)
    assert len(past(only_today)) == 1


def test_the_current_call_stops_reading_at_its_gap(d, h, monkeypatch):
    log = Log(h)
    for k in range(50):  # a long call, then a 2-hour silence
        log.turn(10.0 + 10 * k)
    log.turn(8000.0)
    log.line(8010.0)
    seen = []
    real = timeline._records_backwards

    def counting(*a, **kw):
        for p in real(*a, **kw):
            seen.append(p.seq)
            yield p

    monkeypatch.setattr(timeline, "_records_backwards", counting)
    out = doc(d, h, at=8100.0, which="future")
    assert out["call"]["id"].startswith("window:local:")
    assert len(seen) == 4, "the call's turn, saying and said, then the one record that proves the gap"


def test_an_old_call_is_found_files_back(d, h):
    log = Log(h)
    first = log.turn(10.0, day="2030-09-30")
    for day in ("2030-10-01", "2030-10-02", "2030-10-03"):
        log.turn(1000.0, day=day)
    out = doc(d, h, at=2000.0, call=f"window:local:{first['seq']}")
    assert [r["seq"] for r in past(out)] == [first["seq"]] and out["call"]["open"] is False


# -- read-only (D7) and player-free (D6) ---------------------------------------------

def _tree(root: Path) -> dict:
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None, p.stat().st_mtime_ns)
            for p in sorted(root.rglob("*"))}


def test_document_changes_nothing(d, h, monkeypatch, tmp_path):
    log = Log(h)
    log.turn(time.time() - T0 - 30.0)
    log.line(time.time() - T0 - 20.0)
    say("queued", hold="turn-end", d=d, monkeypatch=monkeypatch)
    before = _tree(tmp_path)
    for which in ("all", "future", "past"):
        timeline.document(d, h, which=which, words=True)
    assert _tree(tmp_path) == before


def test_document_imports_no_player(d, h, monkeypatch):
    Log(h).line(time.time() - T0 - 20.0)
    say("queued", d=d, monkeypatch=monkeypatch)
    code = (
        "import json, sys\n"
        "from pathlib import Path\n"
        "from mouth import timeline\n"
        "doc = timeline.document(Path(sys.argv[1]), Path(sys.argv[2]), words=True)\n"
        "print(json.dumps({'player': 'mouth.player' in sys.modules, 'rows': len(doc['rows'])}))\n"
    )
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    out = subprocess.run([sys.executable, "-c", code, str(d), str(h)], env=env,
                         capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == {"player": False, "rows": 2}
