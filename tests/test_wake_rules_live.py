"""EARS-504 R3 (EARS-458 folded in): ears-mail follows the wake file live.

`WakeRules` stats the rules file once per poll and reloads it when it changed, so a seat
written while the keeper runs is a wake target on the next turn, with no restart. A bad
or missing file mid-run keeps the last good rules and says so ONCE. At start a bad
file counts as missing (the --seats seats, plus one line, never a traceback). The load
itself refuses a file that would load and then stop the keeper at a turn: a bad number
(F12) or a regex field that is not a string (F14).
Design: the EARS-504 task's design.md, section R3 and the test plan (SC3).
"""
import importlib.util
import json
import os
import queue
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from ears import wake

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "keeper" / "ears-mail.py"
REFUSE = REPO / "tests" / "fixtures" / "refuse_sendmail.py"
KEPT = "kept the last good rules"


def _em():
    spec = importlib.util.spec_from_file_location("ears_mail_live", SCRIPT)
    em = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(em)
    return em


em = _em()


def _replace(path, text):
    """Write TEXT as wake.write does: a temp file, then a rename (a new inode)."""
    tmp = Path(f"{path}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


class Said(list):
    def __call__(self, line):
        self.append(line)


@pytest.fixture
def live(tmp_path):
    f = tmp_path / "wake.json"
    said = Said()
    return f, said, em.WakeRules(str(f), [], say=said)


# -- SC3 unit: the holder -------------------------------------------------------------

def test_a_seat_survives_a_corrupt_write_and_a_delete_then_a_good_file_loads(live):
    f, said, rules = live
    assert rules() == {} and said == []                 # missing at start: no seats, no line
    wake.add("mini-me", path=f)
    assert set(rules()) == {"mini-me"}                  # added after start: a target now
    assert said == [f"ears-mail: wake rules reloaded from {f}: names known: mini-me"]
    said.clear()
    _replace(f, "{not json")
    assert set(rules()) == {"mini-me"} and len(said) == 1 and KEPT in said[0]
    assert "JSONDecodeError" in said[0] and "seats: mini-me" in said[0]
    assert set(rules()) == {"mini-me"} and len(said) == 1   # the same broken file: silent
    f.unlink()
    assert set(rules()) == {"mini-me"} and len(said) == 2   # deleted: kept, one line
    assert "is missing" in said[1] and KEPT in said[1]
    assert set(rules()) == {"mini-me"} and len(said) == 2   # still missing: silent
    wake.add("ada", path=f)
    assert set(rules()) == {"ada"} and len(said) == 3 and "reloaded" in said[2]
    assert rules.failed is False


def test_an_unchanged_file_is_never_read_again(live, monkeypatch):
    f, said, rules = live
    wake.add("mini-me", path=f)
    first = rules()
    monkeypatch.setattr(rules, "_load", lambda: pytest.fail("read an unchanged file"))
    assert rules() is first and rules() is first


def test_each_new_broken_version_says_it_once_again(live):
    f, said, rules = live
    wake.add("mini-me", path=f)
    rules()
    said.clear()
    _replace(f, "{")
    rules(), rules()
    _replace(f, "[1, 2")
    rules(), rules()
    assert len(said) == 2 and all(KEPT in s for s in said)


def test_clearing_every_seat_is_an_empty_seats_object(live):
    f, said, rules = live
    wake.add("mini-me", path=f)
    rules()
    _replace(f, json.dumps({"seats": {}}))
    assert rules() == {} and rules.failed is False


def test_a_hand_edit_in_place_is_seen(live):
    f, said, rules = live
    f.write_text(json.dumps({"seats": {"ada": {}}}))
    assert set(rules()) == {"ada"}
    with open(f, "w") as fh:                            # same inode: mtime or size moved
        fh.write(json.dumps({"seats": {"ada": {}, "bob": {}}}))
    assert set(rules()) == {"ada", "bob"}


def test_only_seats_are_kept_beside_the_file(tmp_path):
    f = tmp_path / "wake.json"
    wake.add("mini-me", path=f)
    rules = em.WakeRules(str(f), ["alice"], say=Said())
    assert set(rules()) == {"mini-me", "alice"}


# -- F12 / F14 unit: a file that loads must not stop a turn ----------------------------

BAD = {
    "reply_window x": {"reply_window": "x"},
    "reply_window null": {"reply_window": None},
    "again_window x": {"again_window": "x"},
    "light x": {"light": "x"},
    "light list": {"light": [1]},
    "openers list": {"openers": ["hey", "hay"]},
    "openers null": {"openers": None},
    "openers number": {"openers": 3},
    "names list": {"names": ["mini", "me"]},
    "names number": {"names": 7},
    "everyone list": {"everyone": ["everyone"]},
    "everyone true": {"everyone": True},
    "seat not an object": "mini-me",
}


@pytest.mark.parametrize("own", list(BAD.values()), ids=list(BAD))
def test_a_bad_seat_entry_keeps_the_last_good_rules(live, own):
    f, said, rules = live
    wake.add("mini-me", path=f)
    good = rules()
    said.clear()
    _replace(f, json.dumps({"seats": {"mini-me": own, "ada": {}}}))
    assert rules() is good and rules.failed is True
    assert len(said) == 1 and KEPT in said[0]


@pytest.mark.parametrize("conf", [[], {"seats": []}, {"defaults": []}, {"seats": {"x": {"names": "("}}}],
                         ids=["a list", "seats a list", "defaults a list", "a bad regex"])
def test_a_wrong_shape_keeps_the_last_good_rules(live, conf):
    f, said, rules = live
    wake.add("mini-me", path=f)
    good = rules()
    said.clear()
    _replace(f, json.dumps(conf))
    assert rules() is good and len(said) == 1 and KEPT in said[0]


def test_json_nested_too_deep_keeps_the_last_good_rules(live):
    """'[' * 200000 raises RecursionError, not ValueError: the keeper keeps its rules."""
    f, said, rules = live
    wake.add("mini-me", path=f)
    good = rules()
    said.clear()
    _replace(f, "[" * 200000)
    assert rules() is good and len(said) == 1 and KEPT in said[0]


@pytest.mark.parametrize("field", ["names", "openers"])
@pytest.mark.parametrize("value", [["mini", "me"], None, 3])
def test_a_regex_field_that_is_not_a_string_fails_the_load(field, value):
    with pytest.raises(TypeError, match=field):
        em.rules_of({"seats": {"mini-me": {field: value}}}, [])


@pytest.mark.parametrize("field", ["reply_window", "again_window", "light"])
def test_a_number_a_turn_would_choke_on_fails_the_load(field):
    with pytest.raises(ValueError, match=field):
        em.rules_of({"seats": {"mini-me": {field: "x"}}}, [])


@pytest.mark.parametrize("own", [{"everyone": False}, {"everyone": None}, {"light": 0},
                                 {"light": None}, {"light": "30"}, {"reply_window": "60"},
                                 {"again_window": 0}, {"everyone": "all of you"}])
def test_what_a_turn_reads_fine_still_loads(own):
    assert "mini-me" in em.rules_of({"seats": {"mini-me": own}}, [])


def test_everyone_false_turns_the_all_call_off():
    r = em.rules_of({"seats": {"mini-me": {"everyone": False}}}, [])["mini-me"]
    assert r["_everyone"] is None


def test_a_turn_reads_a_seat_number_as_the_load_does():
    """The guard mirrors the turn: a window the load accepts, a turn accepts too."""
    rules = em.rules_of({"seats": {"ada": {"reply_window": "60", "light": "30"}}}, [])
    room = em.Room()
    assert em.verdicts({"ts": "2030-06-09T01:00:01+10:00", "text": "fine thanks"},
                       rules, room) == {}


# -- start: a bad file counts as missing ----------------------------------------------

def _start(tmp_path, rules_text, *args):
    logs = tmp_path / "logs" / "conversations"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "heard_2030-06-09.jsonl").write_text("")
    rf = tmp_path / "wake.json"
    rf.write_text(rules_text)
    return subprocess.run([sys.executable, str(SCRIPT), "--rules", str(rf), "--dry-run", "--once",
                           "--from-start", "--domain", "host1", *args],
                          capture_output=True, text=True, timeout=30,
                          env=dict(os.environ, VOICEMODE_BASE_DIR=str(tmp_path)))


def _wake_lines(stderr):
    return [l for l in stderr.splitlines() if "wake file" in l or "no seats" in l]


def test_a_bad_file_at_start_starts_with_the_given_seats_and_one_line(tmp_path):
    p = _start(tmp_path, "{not json", "--to", "seats", "--seats", "alice")
    assert p.returncode == 0, p.stderr
    assert "Traceback" not in p.stderr
    lines = _wake_lines(p.stderr)
    assert len(lines) == 1 and "not loaded" in lines[0] and "starting with seats: alice" in lines[0]
    assert "seats alice" in p.stderr                    # the banner: alice is mailed


def test_a_bad_file_at_start_with_no_seats_starts_with_none(tmp_path):
    p = _start(tmp_path, json.dumps({"seats": {"mini-me": {"openers": ["hey"]}}}))
    assert p.returncode == 0, p.stderr
    assert "Traceback" not in p.stderr
    lines = _wake_lines(p.stderr)
    assert len(lines) == 1 and "starting with seats: none" in lines[0]
    assert "no turn is a call" in lines[0] and "names known: )" in p.stderr


def test_seats_mail_with_no_seats_is_still_refused(tmp_path):
    p = _start(tmp_path, "{not json", "--to", "seats")
    assert p.returncode == 2 and "no seats: give --seats" in p.stderr
    assert "Traceback" not in p.stderr


def test_a_good_file_at_start_says_no_reload_line(tmp_path):
    wake.add("mini-me", path=tmp_path / "w" / "wake.json")
    p = _start(tmp_path, (tmp_path / "w" / "wake.json").read_text())
    assert p.returncode == 0 and _wake_lines(p.stderr) == [] and "reloaded" not in p.stderr
    assert "names known: mini-me" in p.stderr


# -- SC3 live loop: a running keeper ---------------------------------------------------

def turn(sec, text, seq):
    return dict(ts=f"2030-06-09T01:00:{sec:06.3f}+10:00", kind="turn", source="mic",
                device="airpods", text=text, t0=100.0, t1=102.0, seq=seq)


class Keeper:
    """ears-mail as a child, without --once: its stdout and stderr read line by line
    on threads, each wait bounded by a deadline (never a fixed sleep)."""

    def __init__(self, tmp_path, rules):
        logs = tmp_path / "logs" / "conversations"
        logs.mkdir(parents=True)
        self.log = logs / "heard_2030-06-09.jsonl"
        self.log.write_text("")
        env = dict(os.environ, VOICEMODE_BASE_DIR=str(tmp_path),
                   EARS_MAIL_MESSAGE=" ".join(shlex.quote(x) for x in (sys.executable, str(REFUSE))))
        self.p = subprocess.Popen(
            [sys.executable, str(SCRIPT), "--rules", str(rules), "--dry-run", "--poll", "0.05",
             "--from-start", "--domain", "host1"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        self.out, self.err, self.errs = queue.Queue(), queue.Queue(), []
        for stream, q in ((self.p.stdout, self.out), (self.p.stderr, self.err)):
            threading.Thread(target=self._pump, args=(stream, q), daemon=True).start()

    @staticmethod
    def _pump(stream, q):
        for line in stream:
            q.put(line.rstrip("\n"))

    def append(self, rec):
        with self.log.open("a") as f:
            f.write(json.dumps(rec) + "\n")

    def _next(self, q, pred, timeout, keep=None):
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                pytest.fail(f"no line in {timeout}s; stderr so far: {self.errs}")
            try:
                line = q.get(timeout=left)
            except queue.Empty:
                continue
            if keep is not None:
                keep.append(line)
            if pred(line):
                return line

    def stderr_line(self, needle, timeout=20):
        return self._next(self.err, lambda l: needle in l, timeout, self.errs)

    def mail(self, seq, timeout=20):
        return json.loads(self._next(self.out, lambda l: json.loads(l).get("seq") == seq, timeout))

    def stop(self):
        """Stop only this test's own child, by its pid."""
        if self.p.poll() is None:
            self.p.terminate()
            try:
                self.p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.p.kill()
                self.p.wait(timeout=10)
        while True:                                 # whatever stderr held until the end
            try:
                self.errs.append(self.err.get(timeout=0.5))
            except queue.Empty:
                return self.errs


def test_a_running_keeper_picks_up_a_seat_and_survives_a_corrupt_write(tmp_path):
    rules = tmp_path / "wake.json"                 # missing at start: no seats
    k = Keeper(tmp_path, rules)
    try:
        k.stderr_line("names known: )")            # started, with no seats
        k.append(turn(1, "hey mini me, what time is it", 10))
        assert not k.mail(10)["rule"]              # no seat yet: mailed, a call to nobody
        wake.add("mini-me", path=rules)            # written while it runs
        k.stderr_line("wake rules reloaded")
        k.append(turn(2, "hey mini me, what time is it", 20))
        m = k.mail(20)
        assert m["rule"] == "mini-me@host1=call" and m["addressed"] == "mini-me@host1"
        _replace(rules, "{not json")               # corrupt, by rename as wake.write writes
        k.stderr_line(KEPT)
        k.append(turn(3, "hey mini me, are you there", 30))
        assert k.mail(30)["rule"] == "mini-me@host1=call"
        assert k.p.poll() is None                  # still running
    finally:
        errs = k.stop()
    assert "Traceback" not in "\n".join(errs)
    assert len([l for l in errs if KEPT in l]) == 1, errs
