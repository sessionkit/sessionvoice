"""mouth timeline, the CLI (EARS-70 do-003; design.md D1, D3, D6, D7, D9).

Every test runs on a scratch tree: a scratch VOICEMODE_BASE_DIR (the heard log and
its speakers sidecar), a scratch mouth dir with its queue maildir, playing.json
and player.lock. conftest.py fences HOME and the mail variables as well.
"""

import itertools
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
from mouth.__main__ import main as mouth_main

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
WRAPPER = ROOT / "bin" / "mouth-timeline"


def iso(t: float) -> str:
    return datetime.fromtimestamp(t).astimezone().isoformat(timespec="milliseconds")


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A busy scratch mouth: an old call, the current call (open), a Delta call
    line, a line playing, and a queue with a hold, a --next, an amend and a
    retract. Returns (root, mouth dir, the current call's id).

    The rows are written relative to the clock read HERE, at fixture time, never
    at import (EARS-297): a module's import happens at collection, and a full
    suite under load can reach this file more than LINGER_S - 100 s later, by
    when an import-time clock has already ended the current call."""
    now = time.time()
    base = tmp_path / "base"
    conv = base / "logs" / "conversations"
    conv.mkdir(parents=True)
    d = tmp_path / "mouth"
    for k, v in {"VOICEMODE_BASE_DIR": str(base), "MOUTH_DIR": str(d),
                 "MOUTH_LOG_DIR": str(tmp_path / "logs"),
                 "MOUTH_DEVICE": "airpods",
                 "MOUTH_BACKEND": "silence"}.items():
        monkeypatch.setenv(k, v)

    seq = iter(range(5000, 6000))
    recs = []

    def put(kind, at, **kw):
        recs.append({"ts": iso(now + at), "seq": next(seq), "kind": kind, "v": 1, **kw})

    def line(at, utt, agent, played=3.0, device="airpods", reason="done"):
        common = {"utt": utt, "agent": agent, "device": device, "source": "mouth",
                  "mail_id": f"<{utt}@host1.mouth>", "requested_ts": iso(now + at - 1)}
        put("saying", at, text="said aloud", **common)
        put("said", at + played, played_s=played, cut=reason != "done", reason=reason,
            text_played_est="said aloud", **common)

    put("turn", -4000, source="mic", device="airpods", text="an old call", t0=0, t1=2, speaker="theo")
    line(-3990, "old000000001", "vera")
    put("turn", -300, source="mic", device="airpods", text="hello there", t0=0, t1=2.5)
    line(-290, "cur000000001", "ivy", played=4.0)
    put("turn", -200, source="mic", device="speaker", text="from the speaker", t0=1, t1=3,
        speaker="theo")
    line(-150, "dlt000000001", "dave", device="call:7")
    line(-120, "cur000000002", "vera", reason="barge-in", played=1.5)
    put("said", -100, utt="ret000000001", agent="ivy", device="airpods", source="mouth",
        played_s=0.0, cut=True, text_played_est="", reason="retracted")
    put("partial", -10, source="mic", device="airpods", text="ignored")
    day = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
    (conv / f"heard_{day}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    turn_seq = next(r["seq"] for r in recs if r.get("text") == "hello there")
    (conv / f"speakers_{day}.jsonl").write_text(json.dumps({"seq": turn_seq, "speaker": "theo"}) + "\n")

    def say(text, agent, **kw):
        monkeypatch.setenv("VOICEMODE_AGENT", agent)
        return mouth.say(text, d=d, spawn=False, **kw)

    say("first in the queue", "vera")
    say("after his turn", "ivy", hold="turn-end")
    say("jump the queue", "dave", priority="next")
    amended = say("to be amended", "vera", expires_s=600)
    mouth.amend(amended["utt"], "amended words", d=d)
    gone = say("to be retracted", "ivy")
    mouth.retract(gone["utt"], d=d)
    playing = say("playing right now", "vera")
    (d / "playing.json").write_text(json.dumps({**box.public(playing), "box_file": "x"}))
    (d / "player.lock").write_bytes(b"")
    (d / "player.pid").write_text("99999\n")

    cid = timeline.document()["call"]["id"]
    assert cid.startswith("window:local:"), cid
    return tmp_path, d, cid


def snapshot(root: Path) -> dict:
    """Every dir and file under root: bytes (files), mode and mtime_ns (both)."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in [""] + dirnames + filenames:
            p = Path(dirpath) / name
            st = os.lstat(p)
            data = p.read_bytes() if p.is_file() and not p.is_symlink() else None
            out[str(p.relative_to(root))] = (st.st_mode, st.st_mtime_ns, data)
    return out


def run_main(argv, capsys, entry: str = "mouth") -> tuple:
    """``mouth timeline ARGV`` (entry ``mouth``), or the wrapper's way in,
    ``python -m mouth.timeline ARGV`` (entry ``timeline``: the fast parser)."""
    try:
        rc = mouth_main(["timeline", *argv]) if entry == "mouth" else timeline.main(argv)
    except SystemExit as e:
        rc = e.code
    o = capsys.readouterr()
    return rc, o.out, o.err


WHICH = ([], ["--future"], ["--past"])
WORDS = ([], ["--words"])
JSON = ([], ["--json"])
LAST = ([], ["--last", "0"], ["--last", "2"])


def calls(cid):
    """--call values and the exit each must give: the current call's id (0), and
    ids that name no call (2)."""
    return (([], 0), (["--call", cid], 0), (["--call", "nope"], 2),
            (["--call", "<unknown@host1>"], 2), (["--call", "window:local:1"], 2),
            (["--call", "window:local:new"], 2))  # the queued lines join the open call


# -- D7: read-only over every flag combination ----------------------------------

def test_every_flag_combination_leaves_the_trees_byte_and_mtime_identical(tree, capsys):
    root, d, cid = tree
    before = snapshot(root)
    n = 0
    for which, words, js, (call, want), last, entry in itertools.product(
            WHICH, WORDS, JSON, calls(cid), LAST, ("mouth", "timeline")):
        argv = [*which, *words, *js, *call, *last]
        rc, out, err = run_main(argv, capsys, entry)
        assert rc == want, (argv, entry, rc, err)
        if rc == 0 and js:
            doc = json.loads(out)
            assert set(doc) == {"v", "call", "now", "rows"} and doc["v"] == 1
            whens = {r["when"] for r in doc["rows"]}
            if which == ["--future"]:
                assert "past" not in whens and doc["rows"], argv
            if which == ["--past"]:
                assert whens <= {"past"}, argv
            for r in doc["rows"]:
                assert ("text" in r) == bool(words), argv
            if last == ["--last", "2"]:
                assert sum(r["when"] == "past" for r in doc["rows"]) <= 2
        n += 1
    for bad, entry in itertools.product(
            (["--future", "--past"], ["--last", "-1"], ["--last", "x"], ["--bogus"],
             ["--call"], ["extra"]), ("mouth", "timeline")):
        rc, _, err = run_main(bad, capsys, entry)
        assert rc == 2 and err, (bad, entry)
    assert n == len(WHICH) * len(WORDS) * len(JSON) * len(calls(cid)) * len(LAST) * 2
    assert snapshot(root) == before, "a read changed the scratch mouth or heard tree"


def test_the_wrapper_reads_and_leaves_the_trees_identical(tree):
    """The real invocation (D9): bin/mouth-timeline as a cold process."""
    root, d, cid = tree
    before = snapshot(root)
    env = {**os.environ}
    for argv in (["--future", "--json"], ["--words"], ["--past", "--json", "--last", "0"],
                 ["--call", cid, "--json", "--words"]):
        p = subprocess.run([str(WRAPPER), *argv], env=env, capture_output=True, text=True,
                           timeout=30)
        assert p.returncode == 0, (argv, p.stderr)
        assert p.stdout.strip()
    p = subprocess.run([str(WRAPPER), "--call", "nope"], env=env, capture_output=True,
                       text=True, timeout=30)
    assert p.returncode == 2 and "no call 'nope'" in p.stderr
    assert snapshot(root) == before


# -- D1: the documents the flags give ----------------------------------------------

def test_the_current_call_in_order(tree, capsys):
    root, d, cid = tree
    rc, out, _ = run_main(["--json", "--words"], capsys)
    assert rc == 0
    doc = json.loads(out)
    assert doc["call"] == {**doc["call"], "id": cid, "key": "window", "room": "local", "open": True}
    rows = doc["rows"]
    past = [r for r in rows if r["when"] == "past"]
    assert [(r["kind"], r["who"]) for r in past] == [
        ("turn", "theo"), ("line", "ivy"), ("turn", "theo"), ("line", "vera"), ("line", "ivy")]
    assert past[0]["text"] == "hello there", "the speakers sidecar names a turn with no label"
    assert "old000000001" not in out and "dlt000000001" not in out, \
        "the old call and the Delta call are other calls"
    assert past[-1]["state"] == "retracted" and past[-1]["dur_s"] == 0.0
    now = [r for r in rows if r["when"] == "now"]
    assert len(now) == 1 and now[0]["text"] == "playing right now"
    fut = [r for r in rows if r["when"] == "future"]
    assert [r["text"] for r in fut] == ["jump the queue", "first in the queue",
                                        "after his turn", "amended words"]
    assert [r["position"] for r in fut] == [1, 2, 3, 4]
    assert fut[3]["amended"] is True and fut[3]["expires_at"]
    assert all(r["utt"] for r in fut + now), "every line row carries its utt"


def test_last_counts_past_rows_and_never_caps_the_future(tree, capsys):
    _, _, _ = tree
    rc, out, _ = run_main(["--json", "--last", "1"], capsys)
    rows = json.loads(out)["rows"]
    assert [r["when"] for r in rows] == ["past", "now"] + ["future"] * 4
    rc, out, _ = run_main(["--json", "--last", "0"], capsys)
    assert sum(r["when"] == "past" for r in json.loads(out)["rows"]) == 5


def test_a_quiet_mouth_is_exit_0_with_no_rows(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "base"))
    monkeypatch.setenv("MOUTH_DIR", str(tmp_path / "mouth"))
    rc, out, err = run_main(["--future", "--json"], capsys)
    assert rc == 0 and not err
    assert json.loads(out)["rows"] == []
    rc, out, _ = run_main(["--future"], capsys)
    assert rc == 0 and "nothing playing, nothing queued" in out
    assert not (tmp_path / "mouth").exists() and not (tmp_path / "base").exists()


# -- D3: the human form ----------------------------------------------------------

def test_the_human_form(tree, capsys):
    root, d, cid = tree
    rc, out, _ = run_main([], capsys)
    lines = out.splitlines()
    assert lines[0].startswith(f"call {cid}  since ") and "open" in lines[0] and "(keyed: window)" in lines[0]
    i = lines.index(" -- now --")
    assert all("next" not in ln for ln in lines[1:i])
    assert "playing" in lines[i + 1] and " vera " in lines[i + 1]
    nxt = lines[i + 2:]
    assert [ln.split()[:3] for ln in nxt] == [["next", "1", "dave"], ["next", "2", "vera"],
                                               ["next", "3", "ivy"], ["next", "4", "vera"]]
    assert all(" queued " in ln for ln in nxt)
    assert "(amended)" in nxt[3]
    assert "said aloud" not in out, "no words without --words"
    rc, out, _ = run_main(["--words"], capsys)
    assert '"amended words"' in out and '"hello there"' in out
    rc, out, _ = run_main(["--past"], capsys)
    assert "-- now --" not in out and "barge-in" in out and "4.0s" in out


def test_human_rows_for_a_row_with_nothing_known():
    doc = {"v": 1, "now": None, "rows": [timeline.row(when="past", kind="turn")],
           "call": {"id": "window:local:new", "key": "window", "room": "local",
                    "started": None, "last": None, "open": False}}
    out = timeline.human(doc)
    assert out.splitlines()[0] == "call window:local:new  nothing heard yet  closed  (keyed: window)"
    assert out.splitlines()[1].split() == ["-", "-", "turn"]


# -- D1/D6/D9: help, the entry points, the cold path ----------------------------------

def test_help_lists_every_flag(capsys):
    with pytest.raises(SystemExit) as e:
        mouth_main(["timeline", "--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    for flag in ("--future", "--past", "--words", "--json", "--call", "--last"):
        assert flag in out
    with pytest.raises(SystemExit):
        mouth_main(["--help"])
    assert "timeline" in capsys.readouterr().out


def test_the_wrapper_is_cheap_and_never_imports_the_player(tmp_path):
    """D6/D9: the wrapper runs its own python -S -m mouth.timeline from this checkout, not uv,
    and the cold path never imports mouth.player (+115 ms)."""
    text = WRAPPER.read_text()
    assert os.access(WRAPPER, os.X_OK)
    assert "uv run" not in text.replace("NOT `uv run`", "")
    assert "-m mouth.timeline" in text and "PYTHONPATH" in text
    env = {**os.environ, "VOICEMODE_BASE_DIR": str(tmp_path / "base"),
           "MOUTH_DIR": str(tmp_path / "mouth"), "PYTHONPATH": str(SRC)}
    p = subprocess.run([sys.executable, "-S", "-X", "importtime", "-m", "mouth.timeline",
                        "--future", "--json"], env=env, capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["rows"] == []
    imported = {ln.rsplit("|", 1)[-1].strip() for ln in p.stderr.splitlines() if "|" in ln}
    assert "mouth.box" in imported and "mouth.player" not in imported
    assert "subprocess" not in imported, "mouth's init imports subprocess only to spawn"


def test_the_wrapper_follows_a_symlink(tmp_path):
    link = tmp_path / "bin" / "mouth-timeline"
    link.parent.mkdir()
    link.symlink_to(WRAPPER)
    env = {**os.environ, "VOICEMODE_BASE_DIR": str(tmp_path / "base"),
           "MOUTH_DIR": str(tmp_path / "mouth")}
    env.pop("PYTHONPATH", None)
    p = subprocess.run([str(link), "--future", "--json"], env=env, cwd="/",
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["v"] == 1


# -- the fast parser (D6): the same namespace as argparse, or it gives up ------------

def _argparse_ns(argv):
    import argparse

    ap = argparse.ArgumentParser(prog="mouth timeline")
    timeline.add_arguments(ap)
    return vars(ap.parse_args(argv))


def test_the_fast_parser_agrees_with_argparse_on_every_combination():
    n = 0
    for which, words, js, call, last in itertools.product(
            WHICH, WORDS, JSON,
            ([], ["--call", "window:local:9"], ["--call=<a@b>"], ["--call=-x"]),
            ([], ["--last", "0"], ["--last=7"])):
        for argv in ([*which, *words, *js, *call, *last], [*last, *call, *js, *words, *which]):
            fast = timeline.fast_args(argv)
            assert fast is not None, argv
            assert vars(fast) == _argparse_ns(argv), argv
            n += 1
    assert n == 2 * 3 * 2 * 2 * 4 * 3


@pytest.mark.parametrize("argv", [
    ["-h"], ["--help"], ["--future", "--past"], ["--fut"], ["--json", "--json"],
    ["--last"], ["--last", "-1"], ["--last=x"], ["--last="], ["--call"], ["--call", "-x"],
    ["--future=1"], ["--"], ["extra"], ["--last", "1", "--last", "2"]])
def test_the_fast_parser_leaves_help_errors_and_oddities_to_argparse(argv):
    assert timeline.fast_args(argv) is None
