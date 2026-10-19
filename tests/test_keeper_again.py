"""Say again: the keeper's match and its addressing (EARS-22, designs/say-again.md S4).

A turn is a say-again only if ALL of it, once normalised, is one phrase from
the seat's list: the keeper's `_words()`, then every seat's names, the openers
and a few courtesy words stripped from the two ENDS only. The target is the
newest heard line of the seat it names, else of whoever spoke last, within
`again_window`. The verdict is `{seat: "again"}`, checked before any other rule;
it marks nothing answered and lights no seat. These tests are pure: they load
keeper/ears-mail.py as a module, or run it with --dry-run --once on a synthetic
heard log, in the style of tests/test_ears_mail.py. None of them reaches the
shell keeper (keeper/ears-keeper.sh).
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "keeper" / "ears-mail.py"
SHIPPED = ROOT / "keeper" / "defaults" / "wake.json"
EXAMPLE = ROOT / "keeper" / "defaults" / "wake.example.json"
RULES = {"seats": {"vera": {"names": "vera|vira"}, "ivy": {"names": "ivy|ivey"}}}
# the README's four, and "say that again"
PHRASES = ["say again", "repeat that", "pardon", "come again", "say that again"]
VERA_LINE = "The build is green and the second option is safer."
IVY_LINE = "Latency sits near forty milliseconds today."


def _em():
    spec = importlib.util.spec_from_file_location("ears_mail_again", SCRIPT)
    em = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(em)
    return em


em = _em()


def ts(sec):
    return f"2030-09-29T01:{int(sec // 60):02d}:{sec % 60:06.3f}+10:00"


def turn(sec, text, span=1.0, seq=None, **kw):
    return dict(ts=ts(sec), kind="turn", source="mic", device="airpods", text=text,
                t0=100.0, t1=100.0 + span, seq=seq or int(sec * 10), **kw)


def saying(sec, agent, text, utt, **kw):
    return dict(ts=ts(sec), kind="saying", agent=agent, text=text, utt=utt, **kw)


def said(sec, agent, utt, played_s=3.0, **kw):
    return dict(ts=ts(sec), kind="said", agent=agent, utt=utt, played_s=played_s, **kw)


def line(sec, agent, text, utt, dur=3.0, **kw):
    """A line heard in full: its saying row DUR s before its said row at SEC."""
    extra = {k: kw.pop(k) for k in ("again_of", "resume_of", "lane", "voice", "session")
             if k in kw}
    return [saying(sec - dur, agent, text, utt, **extra),
            said(sec, agent, utt, played_s=kw.pop("played_s", dur), **extra, **kw)]


def rules(conf=RULES):
    return em.load_rules(None, []) if conf is None else _load(conf)


def _load(conf):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d, "wake.json")
        p.write_text(json.dumps(conf))
        return em.load_rules(str(p), [])


def room_of(records):
    room = em.Room()
    for r in records:
        room.note(r)
    return room


def judge(records, text, at, conf=RULES, playing=None, **kw):
    """(verdict, room) for the turn TEXT ending at AT after RECORDS."""
    room = room_of(records)
    rec = turn(at, text, **kw)
    return em.verdicts(rec, rules(conf), room, playing=playing), room


def run(records, conf=RULES):
    """The script itself, --to device: [(seq, Ears-Rule)] per finished turn."""
    with tempfile.TemporaryDirectory() as d:
        logs = Path(d, "logs", "conversations")
        logs.mkdir(parents=True)
        (logs / "heard_2030-09-29.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
        rf = Path(d, "wake.json")
        rf.write_text(json.dumps(conf))
        p = subprocess.run([sys.executable, str(SCRIPT), "--rules", str(rf), "--dry-run", "--once",
                            "--from-start", "--domain", "host1"], capture_output=True, text=True,
                           timeout=30, env=dict(os.environ, VOICEMODE_BASE_DIR=d))
        assert p.returncode == 0, p.stderr
        return [(m["seq"], m["rule"]) for m in map(json.loads, p.stdout.splitlines()) if m.get("seq")]


VERA = line(10, "vera", VERA_LINE, "c0ra11ne0001")
IVY = line(20, "ivy", IVY_LINE, "p1p11ne00001")


# -- the defaults ------------------------------------------------------------------

def test_the_defaults_are_the_readmes_four_and_say_that_again_and_a_120_s_window():
    assert em.DEFAULTS["again"] == PHRASES and em.DEFAULTS["again_window"] == 120
    for f in (SHIPPED, EXAMPLE):
        d = json.loads(f.read_text())["defaults"]
        assert d["again"] == PHRASES and d["again_window"] == 120, f
    r = rules()
    assert r["vera"]["_again"] == {("say", "again"), ("repeat", "that"), ("pardon",),
                                   ("come", "again"), ("say", "that", "again")}


def test_the_shipped_file_and_the_code_agree():
    conf = json.loads(SHIPPED.read_text())
    conf["seats"] = {"vera": {}}
    assert _load(conf)["vera"]["_again"] == rules()["vera"]["_again"]


# -- section 1: every worked example ------------------------------------------------

@pytest.mark.parametrize("text,words,named", [
    ("Say again?", ("say", "again"), set()),
    ("Sorry, say again.", ("say", "again"), set()),
    ("Vera, say again.", ("say", "again"), {"vera"}),
    ("Hey Vera, pardon?", ("pardon",), {"vera"}),
    ("Repeat that, please.", ("repeat", "that"), set()),
    ("Come again?", ("come", "again"), set()),
    ("I'll say again that the build is green.",
     ("i", "ll", "say", "again", "that", "the", "build", "is", "green"), set()),
    ("Say again, what was the second option?",
     ("say", "again", "what", "was", "the", "second", "option"), set()),
    ("Pardon me, Vera, I meant the other branch.",
     ("pardon", "me", "vera", "i", "meant", "the", "other", "branch"), set()),
    ("Can you say that again?", ("say", "that", "again"), set()),
    ("What?", ("what",), set()),
    ("Vera, Ivy, say again.", ("say", "again"), {"vera", "ivy"}),
])
def test_again_match_normalises_each_worked_example(text, words, named):
    assert em.again_match(text, rules()) == (words, named)


@pytest.mark.parametrize("text,verdict", [
    ("Say again?", {"ivy": "again"}),                 # the last speaker
    ("Sorry, say again.", {"ivy": "again"}),
    ("Vera, say again.", {"vera": "again"}),          # named: that seat's line
    ("Hey Vera, pardon?", {"vera": "again"}),
    ("Repeat that, please.", {"ivy": "again"}),
    ("Come again?", {"ivy": "again"}),
    ("Hey Vira, come again?", {"vera": "again"}),    # a sound-alike name is that seat's
    ("Um, could you say again please?", {"ivy": "again"}),
    ("Oh, ok, pardon?", {"ivy": "again"}),
    ("Can you say that again?", {"ivy": "again"}),   # the added phrase
    ("Vera, say that again please.", {"vera": "again"}),
])
def test_a_say_again_targets_the_named_seat_else_the_last_speaker(text, verdict):
    got, room = judge(VERA + IVY, text, 23)
    assert got == verdict
    seat = next(iter(verdict))
    assert room.again["seat"] == seat
    assert room.again["utt"] == {"vera": "c0ra11ne0001", "ivy": "p1p11ne00001"}[seat]


def test_readme_non_trigger_a_phrase_inside_a_statement():
    """'I'll say again that ...' is the README's own non-trigger: today's verdict stands."""
    got, room = judge(VERA + IVY, "I'll say again that the build is green.", 23)
    assert got == {"ivy": "reply"} and room.again is None


def test_readme_non_trigger_a_phrase_opening_a_question():
    """'Say again, what was ...' is a question for the model: a reply, not a replay."""
    got, room = judge(VERA + IVY, "Say again, what was the second option?", 23)
    assert got == {"ivy": "reply"} and room.again is None


@pytest.mark.parametrize("text", [
    "Pardon me, Vera, I meant the other branch.",   # a name in the middle, words left over
    "Say that again later.",                         # words left after the phrase
    "What?",                                         # not a trigger (Q8)
])
def test_other_non_triggers_keep_todays_verdict(text):
    got, room = judge(VERA + IVY, text, 23)
    want = em.verdicts(turn(23, text), rules({**RULES, "defaults": {"again": []}}), room_of(VERA + IVY))
    assert got == want and room.again is None
    assert "again" not in got.values()


def test_two_seats_named_is_not_a_say_again():
    got, room = judge(VERA + IVY, "Vera, Ivy, say again.", 23)
    assert "again" not in got.values() and room.again is None


def test_a_single_phrase_may_be_a_string():
    conf = {"seats": {"vera": {"names": "vera", "again": "Say it again!"}}}
    assert rules(conf)["vera"]["_again"] == {("say", "it", "again")}
    got, _ = judge(VERA, "Say it again?", 13, conf=conf)
    assert got == {"vera": "again"}


def test_a_seats_own_phrase_can_be_added():
    conf = {"seats": {"vera": {"names": "vera", "again": PHRASES + ["what was that"]},
                      "ivy": {"names": "ivy"}}}
    got, _ = judge(VERA, "Sorry, what was that?", 13, conf=conf)
    assert got == {"vera": "again"}
    got, _ = judge(IVY, "Sorry, what was that?", 23, conf=conf)
    assert got == {"ivy": "reply"}                  # not in ivy's list: today's verdict


@pytest.mark.parametrize("off", [[], False, None, True, 3])
def test_again_off_for_a_seat(off):
    conf = {"seats": {"vera": {"names": "vera"}, "ivy": {"names": "ivy", "again": off}}}
    got, room = judge(VERA + IVY, "Say again?", 23, conf=conf)
    assert got == {"ivy": "reply"} and room.again is None
    got, _ = judge(VERA + IVY, "Vera, say again.", 23, conf=conf)
    assert got == {"vera": "again"}                  # vera's is still on


# -- section 2: the last-line lookup ------------------------------------------------

def test_the_newest_heard_line_from_any_agent():
    got, room = judge(line(10, "ivy", IVY_LINE, "p1") + line(20, "vera", VERA_LINE, "c1"),
                      "Say again?", 23)
    assert got == {"vera": "again"} and room.again["utt"] == "c1"


def test_named_finds_that_seats_line_even_when_another_spoke_after():
    got, room = judge(VERA + IVY, "Vera, say again.", 23)
    assert room.again["utt"] == "c0ra11ne0001" and room.again["line"] == "c0ra11ne0001"


def test_named_with_no_line_of_that_seat_is_todays_verdict():
    got, room = judge(IVY, "Vera, say again.", 23)
    assert got == {"ivy": "reply"} and room.again is None      # as today: no opener, no call


def test_the_window_edge():
    # the turn starts at 132 - 1 = 131; vera's line ended at 11 -> 120 s exactly: in
    got, _ = judge(line(11, "vera", VERA_LINE, "c1"), "Say again?", 132)
    assert got == {"vera": "again"}
    got, room = judge(line(10.5, "vera", VERA_LINE, "c1"), "Say again?", 132)   # 120.5 s: out
    assert "again" not in got.values() and room.again is None


def test_the_window_is_per_seat():
    conf = {"seats": {"vera": {"names": "vera", "again_window": 30}}}
    got, _ = judge(line(10, "vera", VERA_LINE, "c1"), "Say again?", 41, conf=conf)
    assert got == {"vera": "again"}
    got, room = judge(line(10, "vera", VERA_LINE, "c1"), "Say again?", 42, conf=conf)
    assert "again" not in got.values() and room.again is None


def test_answered_is_ignored():
    """He may want a line again after he answered it."""
    room = room_of(VERA)
    r = rules()
    assert em.verdicts(turn(13, "Yes, ship it."), r, room) == {"vera": "reply"}
    assert "c0ra11ne0001" in room.answered
    assert em.verdicts(turn(16, "Say again?"), r, room) == {"vera": "again"}


def test_a_bed_is_skipped():
    recs = VERA + line(20, "ivy", "[sound jingle.wav]", "b3d000000001", lane="file")
    got, room = judge(recs, "Say again?", 23)
    assert got == {"vera": "again"} and room.again["utt"] == "c0ra11ne0001"


def test_a_line_never_heard_is_skipped():
    """played_s 0: cut before he heard a word, or an amended line's unspoken row."""
    recs = VERA + [saying(19, "ivy", IVY_LINE, "p0"),
                   said(20, "ivy", "p0", played_s=0.0, cut=True, reason="amended")]
    got, room = judge(recs, "Say again?", 23)
    assert got == {"vera": "again"} and room.again["utt"] == "c0ra11ne0001"


def test_a_line_his_turn_cut_is_the_one_he_means():
    """His 'say again' over a playing line cuts it by barge-in; that said row lands
    mid-turn, before the turn row, and is the line he means."""
    recs = VERA + [saying(19, "ivy", IVY_LINE, "p1"),
                   said(21.5, "ivy", "p1", played_s=2.5, cut=True, reason="barge-in")]
    got, room = judge(recs, "Say again?", 22, span=2.0)
    assert got == {"ivy": "again"} and room.again["utt"] == "p1"


def test_a_line_that_ended_after_the_turn_is_not_the_one():
    """end <= until: a line that began and ended after his turn is not the one he means."""
    recs = VERA + line(27, "ivy", IVY_LINE, "p1")             # saying at 24, said at 27
    got, room = judge(recs, "Say again?", 23)
    assert got == {"vera": "again"} and room.again["utt"] == "c0ra11ne0001"


@pytest.mark.parametrize("key", ["again_of", "resume_of"])
def test_a_chain_resolves_to_the_original(key):
    recs = VERA + line(16, "vera", "the second option is safer.", "c2", **{key: "c0ra11ne0001"})
    got, room = judge(recs, "Say again?", 19)
    assert got == {"vera": "again"}
    assert room.again["utt"] == "c0ra11ne0001" and room.again["line"] == "c2"


def test_a_replay_of_a_resume_resolves_to_the_original():
    recs = VERA + line(16, "vera", "the rest", "r1", resume_of="c0ra11ne0001")
    recs += line(30, "vera", VERA_LINE, "a1", again_of="c0ra11ne0001")
    got, room = judge(recs, "Say again?", 33)
    assert room.again["utt"] == "c0ra11ne0001"


def test_a_chain_loop_is_never_followed_forever():
    recs = line(10, "vera", "x one", "u1", again_of="u2") + line(12, "vera", "x two", "u2",
                                                                    again_of="u1")
    got, room = judge(recs, "Say again?", 15)
    assert got == {"vera": "again"} and room.again["utt"] in ("u1", "u2")


def test_the_target_carries_what_the_mouth_needs():
    recs = line(10, "vera", VERA_LINE, "c1", voice="af_sky", session="s-vera", volume=40)
    got, room = judge(recs, "Say again?", 13)
    assert room.again == {"seat": "vera", "utt": "c1", "line": "c1", "volume": 40,
                          "voice": "af_sky", "session": "s-vera", "text": VERA_LINE, "named": []}


def test_meta_is_a_side_map_the_lines_stay_eight_slots():
    room = room_of(line(10, "vera", VERA_LINE, "c1", voice="af_sky", again_of="c0", volume=40,
                        audio="/a/c1.wav"))
    assert len(room.lines["c1"]) == 8
    assert room.meta["c1"] == {"voice": "af_sky", "again_of": "c0", "volume": 40,
                               "audio": "/a/c1.wav", "played_s": 3.0}


# -- the guards ---------------------------------------------------------------------

def test_guard_a_line_in_flight():
    recs = VERA + [saying(21, "ivy", IVY_LINE, "p1")]       # playing, no said row yet
    got, room = judge(recs, "Say again?", 23)
    assert room.again is None and "again" not in got.values()


def test_guard_a_bed_playing_is_not_a_line_in_flight():
    recs = VERA + [saying(11, "ivy", "[sound song.mp3]", "b1", lane="file")]
    got, _ = judge(recs, "Say again?", 13)
    assert got == {"vera": "again"}


def test_guard_a_saying_row_long_lost_does_not_hold_it_off():
    recs = [saying(0, "ivy", IVY_LINE, "p1")] + line(700, "vera", VERA_LINE, "c1")
    got, _ = judge(recs, "Say again?", 703)
    assert got == {"vera": "again"}


def test_guard_the_seats_own_voice():
    got, room = judge(VERA, "Say again?", 13, speaker="vera")
    assert room.again is None and got != {"vera": "again"}


def test_guard_a_film_under_an_unnamed_turn():
    film = lambda: {"title": "a film"}  # noqa: E731
    got, room = judge(VERA, "Pardon?", 13, playing=film)
    assert room.again is None and got == {}            # the film's own "pardon?"
    got, _ = judge(VERA, "Vera, pardon?", 13, playing=film)
    assert got == {"vera": "again"}                     # named still works, as a call does


def test_guard_no_line_at_all():
    got, room = judge([], "Say again?", 13)
    assert got == {} and room.again is None


def test_an_unknown_agents_line_is_not_a_target():
    got, room = judge(line(10, "stranger", VERA_LINE, "s1"), "Say again?", 13)
    assert room.again is None


# -- what the say-again does to the room (design 4d) ----------------------------------

def test_the_say_again_turn_marks_nothing_answered_and_lights_nothing():
    conf = {"seats": {"vera": {"names": "vera", "light": 300}, "ivy": {"names": "ivy"}}}
    room = room_of(VERA)
    assert em.verdicts(turn(13, "Say again?"), rules(conf), room) == {"vera": "again"}
    assert room.answered == set()
    # the original stays answerable: his next unaddressed turn replies to it
    assert em.verdicts(turn(16, "Yes, ship it."), rules(conf), room) == {"vera": "reply"}


def test_the_next_turn_after_a_replay_is_reply_to_the_replay_and_marks_its_original():
    room = room_of(VERA)
    r = rules()
    assert em.verdicts(turn(13, "Say again?"), r, room) == {"vera": "again"}
    for rec in line(20, "vera", VERA_LINE, "a1", again_of="c0ra11ne0001"):
        room.note(rec)
    assert em.verdicts(turn(23, "Yes, ship it."), r, room) == {"vera": "reply"}
    assert room.answered == {"a1", "c0ra11ne0001"}     # one content, never answered twice
    assert em.verdicts(turn(26, "And another thing."), r, room) == {}


def test_a_named_answer_to_a_replay_marks_its_original_too():
    room = room_of(VERA + line(20, "vera", VERA_LINE, "a1", again_of="c0ra11ne0001"))
    assert em.verdicts(turn(23, "Thanks Vera, ship it."), rules(), room) == {"vera": "call"}
    assert room.answered == {"a1", "c0ra11ne0001"}


def test_a_replays_echo_is_trimmed_like_any_line():
    """The replay's rows carry the original text, so its sound coming back is echo."""
    recs = VERA + [saying(14, "vera", VERA_LINE, "a1", again_of="c0ra11ne0001"),
                   said(17, "vera", "a1", played_s=3.0, again_of="c0ra11ne0001", device="mac")]
    room = room_of(recs)
    rec = dict(turn(17.5, VERA_LINE, span=3.0), device="mac")
    assert room.echoes(rec)


# -- through the script: the device mail's Ears-Rule ------------------------------------

def test_the_device_mail_carries_seat_at_host_again():
    out = run(VERA + IVY + [turn(23, "Say again?", seq=1), turn(26, "Vera, pardon?", seq=2),
                            turn(29, "I'll say again that the build is green.", seq=3)])
    # the say-agains mark nothing answered: the statement after them is ivy's reply
    assert out == [(1, "ivy@host1=again"), (2, "vera@host1=again"), (3, "ivy@host1=reply")]


def test_no_seats_no_say_again():
    assert run(VERA + [turn(13, "Say again?", seq=1)], conf={"seats": {}}) == [(1, "")]


# -- S5: the action and the wire (design sections 3, 4b and 5) ------------------------------
# The keeper runs `--mouth` as `MOUTH again UTT ...` on an again verdict. Here MOUTH is
# tests/fixtures/fake_mouth.py, which records its argv and exits as told: nothing plays,
# no real mouth is touched. The keeper runs in-process with `send` replaced by a recorder,
# so the mails it would hand `message send` are read back without --dry-run.

FAKE = ROOT / "tests" / "fixtures" / "fake_mouth.py"
VOICED = [saying(7, "vera", VERA_LINE, "c0ra11ne0001", voice="af_sky", session="s-vera-1"),
          said(10, "vera", "c0ra11ne0001", played_s=3.0, volume=40, session="s-vera-1")]


def mouth(tmp_path, code=0, sleep=0, err="mouth: no audio for it"):
    """(the --mouth command for the fake, the file its calls land in)."""
    log = tmp_path / "mouth-calls.jsonl"
    cmd = [sys.executable, str(FAKE), "--log", str(log), "--exit", str(code),
           "--sleep", str(sleep), "--err", err, "--"]
    return em.shlex.join(cmd), log


def calls(log):
    return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []


def live(tmp_path, monkeypatch, capsys, records, cmd, conf=RULES, args=()):
    """The keeper run on RECORDS, sending for real into a recorder: ([mail], [stderr JSON])."""
    logs = tmp_path / "logs" / "conversations"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "heard_2030-09-29.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    rf = tmp_path / "wake.json"
    rf.write_text(json.dumps(conf))
    sent = []

    def send(m, sender, domain):
        sent.append(m)
        return 0, f"<{len(sent)}@test>", ""
    monkeypatch.setattr(em, "send", send)
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path))
    capsys.readouterr()
    assert em.main(["--rules", str(rf), "--once", "--from-start", "--domain", "host1",
                    "--mouth", cmd, *args]) == 0
    err = capsys.readouterr().err
    return sent, [json.loads(x) for x in err.splitlines() if x.startswith("{")]


def test_success_is_again_on_the_wire_and_no_call_so_the_seat_is_not_woken(
        tmp_path, monkeypatch, capsys):
    cmd, log = mouth(tmp_path)
    sent, err = live(tmp_path, monkeypatch, capsys, VOICED + [turn(13, "Vera, say again.", seq=7)], cmd)
    assert [m["headers"]["Ears-Rule"] for m in sent] == ["vera@host1=again"]
    assert "call" not in sent[0]["headers"]["Ears-Rule"]
    # the utt, the seq, his words, the original's level, and the saying row's facts
    assert calls(log) == [["again", "c0ra11ne0001", "--seq=7", "--heard=Vera, say again.",
                           "--volume=40", f"--text={VERA_LINE}", "--voice=af_sky",
                           "--agent=vera", "--session=s-vera-1"]]
    done = [e for e in err if e.get("again") == "replayed"]
    assert done == [{"again": "replayed", "seq": 7, "seat": "vera", "utt": "c0ra11ne0001",
                     "mouth": {"utt": "replay0000001", "again_of": "c0ra11ne0001",
                               "again_from": "audio"}}]
    assert not [e for e in err if e.get("skip")]


def test_success_asks_for_the_original_of_a_resume(tmp_path, monkeypatch, capsys):
    """The chain resolves to the original, so the mouth is asked for the whole line."""
    recs = VOICED + line(16, "vera", "second option is safer.", "r3sume000001",
                         resume_of="c0ra11ne0001")
    cmd, log = mouth(tmp_path)
    sent, _ = live(tmp_path, monkeypatch, capsys, recs + [turn(18, "Say again?", seq=8)], cmd)
    assert [m["headers"]["Ears-Rule"] for m in sent] == ["vera@host1=again"]
    assert calls(log)[0][:2] == ["again", "c0ra11ne0001"]


@pytest.mark.parametrize("code", [3, 2, 1])
def test_a_mouth_that_fails_falls_back_to_call_with_a_skip_line(tmp_path, monkeypatch, capsys, code):
    cmd, log = mouth(tmp_path, code=code, err="mouth: no audio for c0ra11ne0001: gone")
    sent, err = live(tmp_path, monkeypatch, capsys, VOICED + [turn(13, "Say again?", seq=9)], cmd)
    assert [m["headers"]["Ears-Rule"] for m in sent] == ["vera@host1=call"]   # the seat is woken
    assert len(calls(log)) == 1
    skips = [e for e in err if e.get("skip") == "again-failed"]
    assert skips == [{"skip": "again-failed", "seq": 9, "seat": "vera", "utt": "c0ra11ne0001",
                      "fallback": "call", "cause": f"exit {code}",
                      "err": "mouth: no audio for c0ra11ne0001: gone"}]
    assert not [e for e in err if e.get("again")]


def test_a_mouth_that_will_not_run_falls_back_to_call(tmp_path, monkeypatch, capsys):
    gone = em.shlex.join([str(tmp_path / "no-such-mouth")])
    sent, err = live(tmp_path, monkeypatch, capsys, VOICED + [turn(13, "Say again?", seq=9)], gone)
    assert [m["headers"]["Ears-Rule"] for m in sent] == ["vera@host1=call"]
    (skip,) = [e for e in err if e.get("skip") == "again-failed"]
    assert skip["cause"] == "not run" and skip["fallback"] == "call"


def test_a_mouth_that_times_out_falls_back_to_call(tmp_path, monkeypatch, capsys):
    assert em.MOUTH_TIMEOUT_S == 5.0                 # the design's bound
    monkeypatch.setattr(em, "MOUTH_TIMEOUT_S", 0.3)
    cmd, log = mouth(tmp_path, sleep=5)
    sent, err = live(tmp_path, monkeypatch, capsys, VOICED + [turn(13, "Say again?", seq=9)], cmd)
    assert [m["headers"]["Ears-Rule"] for m in sent] == ["vera@host1=call"]
    (skip,) = [e for e in err if e.get("skip") == "again-failed"]
    assert skip["cause"] == "timeout" and skip["timeout_s"] == 0.3


def test_dry_run_prints_the_command_and_runs_nothing(tmp_path):
    cmd, log = mouth(tmp_path)
    logs = tmp_path / "logs" / "conversations"
    logs.mkdir(parents=True)
    recs = VOICED + [turn(13, "Say again?", seq=5)]
    (logs / "heard_2030-09-29.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    rf = tmp_path / "wake.json"
    rf.write_text(json.dumps(RULES))
    p = subprocess.run([sys.executable, str(SCRIPT), "--rules", str(rf), "--dry-run", "--once",
                        "--from-start", "--domain", "host1", "--mouth", cmd], capture_output=True,
                       text=True, timeout=30, env=dict(os.environ, VOICEMODE_BASE_DIR=str(tmp_path)))
    assert p.returncode == 0, p.stderr
    assert not log.exists()                         # the fake was never run
    mails = [json.loads(x) for x in p.stdout.splitlines()]
    assert [m["rule"] for m in mails] == ["vera@host1=again"]
    (dry,) = [json.loads(x) for x in p.stderr.splitlines() if x.startswith('{"again"')]
    assert dry["again"] == "dry run" and dry["utt"] == "c0ra11ne0001"
    assert dry["cmd"][:len(em.shlex.split(cmd)) + 2] == em.shlex.split(cmd) + ["again", "c0ra11ne0001"]


def test_to_seats_mails_no_turn_for_an_again(tmp_path, monkeypatch, capsys):
    """To the seat, the turn would match the pager's `wake to:$me` and wake it (4b)."""
    cmd, log = mouth(tmp_path)
    recs = VOICED + [turn(13, "Say again?", seq=5), turn(16, "Thanks, that is clear.", seq=6)]
    sent, _ = live(tmp_path, monkeypatch, capsys, recs, cmd, args=("--to", "seats"))
    assert len(calls(log)) == 1
    # only the next turn is mailed: the reply to vera's line, which the again left answerable
    assert [(m["to"], m["headers"]["Ears-Rule"], m["headers"]["Ears-Seq"]) for m in sent] == [
        (["vera"], "vera=reply", "6")]


def test_to_seats_a_failed_again_is_mailed_as_a_call(tmp_path, monkeypatch, capsys):
    cmd, _ = mouth(tmp_path, code=3)
    sent, _ = live(tmp_path, monkeypatch, capsys, VOICED + [turn(13, "Say again?", seq=5)], cmd,
                   args=("--to", "seats"))
    assert [(m["to"], m["headers"]["Ears-Rule"]) for m in sent] == [(["vera"], "vera=call")]


def test_to_seats_dry_run_mails_no_turn_and_runs_nothing(tmp_path):
    cmd, log = mouth(tmp_path)
    recs = VOICED + [turn(13, "Say again?", seq=5)]
    logs = tmp_path / "logs" / "conversations"
    logs.mkdir(parents=True)
    (logs / "heard_2030-09-29.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    rf = tmp_path / "wake.json"
    rf.write_text(json.dumps(RULES))
    p = subprocess.run([sys.executable, str(SCRIPT), "--rules", str(rf), "--dry-run", "--once",
                        "--from-start", "--domain", "host1", "--to", "seats", "--mouth", cmd],
                       capture_output=True, text=True, timeout=30,
                       env=dict(os.environ, VOICEMODE_BASE_DIR=str(tmp_path)))
    assert p.returncode == 0, p.stderr
    assert p.stdout == "" and not log.exists()


def test_to_seats_a_seat_not_mailed_here_is_not_replayed_here(tmp_path, monkeypatch, capsys):
    """--seats narrows who this keeper acts for; a side-by-side keeper replays its own."""
    cmd, log = mouth(tmp_path)
    sent, _ = live(tmp_path, monkeypatch, capsys, VOICED + [turn(13, "Say again?", seq=5)], cmd,
                   args=("--to", "seats", "--seats", "ivy"))
    assert sent == [] and calls(log) == []


def test_a_turn_that_is_no_say_again_never_runs_the_mouth(tmp_path, monkeypatch, capsys):
    cmd, log = mouth(tmp_path)
    recs = VOICED + [turn(13, "I'll say again that the build is green.", seq=5),
                     turn(16, "Hey Vera, what next?", seq=6)]
    sent, _ = live(tmp_path, monkeypatch, capsys, recs, cmd)
    assert [m["headers"]["Ears-Rule"] for m in sent] == ["vera@host1=reply", "vera@host1=call"]
    assert calls(log) == []


def test_the_worked_examples_through_the_wire(tmp_path, monkeypatch, capsys):
    """'say that again' ships. 'Can you say that again?' is a
    say-again; 'say that again later' is not, and keeps today's verdict (a reply)."""
    assert em.again_match("Can you say that again?", rules()) == (("say", "that", "again"), set())
    assert em.again_match("say that again later", rules())[0] == ("say", "that", "again", "later")
    cmd, log = mouth(tmp_path)
    recs = VOICED + [turn(13, "Can you say that again?", seq=5), turn(16, "say that again later", seq=6)]
    sent, _ = live(tmp_path, monkeypatch, capsys, recs, cmd)
    assert [m["headers"]["Ears-Rule"] for m in sent] == ["vera@host1=again", "vera@host1=reply"]
    assert [c[:2] for c in calls(log)] == [["again", "c0ra11ne0001"]]


def test_the_again_entry_calls_nobody():
    # sessionmail src/sessionmail/_parsers.py:107-108 (Entry.calls, at ee5ce67): an entry
    # calls its seat only when its rule is exactly "call". Pinned here by this comment and
    # the keeper's own, never by importing sessionmail.
    assert em.rule_header({"vera": "again"}, "host1") == "vera@host1=again"
    assert "_parsers.py:107" in SCRIPT.read_text()


def test_again_argv_keeps_values_whole_and_drops_what_is_unknown():
    ag = {"seat": "vera", "utt": "c0ra11ne0001", "volume": None, "text": "-x line", "voice": "",
          "session": None}
    got = em.again_argv("mouth", ag, {"seq": "not-a-seq"}, "-pardon")
    assert got == ["mouth", "again", "c0ra11ne0001", "--heard=-pardon", "--text=-x line",
                   "--agent=vera"]


def test_the_default_mouth_is_ears_mouth_else_this_pythons(monkeypatch):
    monkeypatch.setenv("EARS_MOUTH", "/opt/x/bin/mouth")
    assert em.default_mouth() == "/opt/x/bin/mouth"
    monkeypatch.delenv("EARS_MOUTH")
    assert em.shlex.split(em.default_mouth()) == [sys.executable, "-m", "mouth"]


def test_to_seats_a_twin_of_a_say_again_replays_nothing_twice(tmp_path, monkeypatch, capsys):
    """The same words heard on the call tap too are one turn: one replay, not two."""
    cmd, log = mouth(tmp_path)
    recs = VOICED + [turn(13, "Say again?", seq=5),
                     dict(turn(13.2, "Say again?", seq=6), source="call")]
    sent, err = live(tmp_path, monkeypatch, capsys, recs, cmd, args=("--to", "seats"))
    assert sent == [] and len(calls(log)) == 1
    assert [e["twin_of"] for e in err if e.get("skip") == "twin"] == [5]
