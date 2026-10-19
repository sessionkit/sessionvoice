"""bin/heard-first (the reply window) and bin/ears-turn (the whole turn after a fire), run as
the seats run them, over a made-up heard log in a temp VOICEMODE_BASE_DIR."""
import json, subprocess, sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parents[1] / "bin"
T = datetime(2030, 1, 7, 8, 0).astimezone()
DAY = "heard_2030-01-07.jsonl"


def heard(tmp, recs):
    logs = tmp / "logs" / "conversations"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / DAY).write_text("".join(json.dumps(r) + "\n" for r in recs))
    return logs


def at(s):
    return (T + timedelta(seconds=s)).isoformat()


def run(tool, tmp, *args):
    return subprocess.run([sys.executable, str(BIN / tool), *args], capture_output=True, text=True,
                          timeout=30, env={"VOICEMODE_BASE_DIR": str(tmp), "PATH": "/usr/bin:/bin"})


# -- heard-first --reply-window: DAVE woke on "I'm back" 21 min after his own line --
@pytest.mark.parametrize("gap,window,dur,want", [
    (30, 120, None, 0),      # a reply half a minute later
    (119, 120, None, 0),     # just inside
    (1260, 120, None, 124),  # 21 minutes later: not a reply
    (1260, 0, None, 0),      # 0 = no limit
    (150, 120, 60, 0),       # a 60 s line, answered 90 s after it ENDED (a review case)
    (200, 120, 60, 124),     # 140 s after it ended
])
def test_reply_window(tmp_path, gap, window, dur, want):
    recs = [{"ts": at(0), "kind": "saying", "utt": "u1", "agent": "dave", "source": "mouth",
             "text": "The kettle is on and the tea is steeping."}]
    if dur is not None:
        recs.append({"ts": at(dur), "kind": "said", "utt": "u1", "agent": "dave", "source": "mouth"})
    recs.append({"ts": at(gap), "kind": "turn", "text": "I'm back.", "source": "mic", "t0": 100.0, "t1": 101.0})
    logs = heard(tmp_path, recs)
    state = tmp_path / "heard.state"
    state.write_text(json.dumps({"offsets": {str(logs / DAY): 0}}))
    p = run("heard-first", tmp_path, "--rule", "reply:dave", "--state", str(state), "--timeout", "1",
            "--reply-window", str(window))
    assert p.returncode == want, p.stdout + p.stderr


# -- ears-turn: after a fire on "Hey Ivy, can we...", the whole sentence, one line --
def test_ears_turn_prints_the_turn(tmp_path):
    heard(tmp_path, [
        {"ts": at(0), "seq": 10, "kind": "partial", "text": "Hey Ivy, can we", "source": "mic"},
        {"ts": at(1), "seq": 11, "kind": "partial", "text": "try the backchannel?", "source": "mic"},
        {"ts": at(2), "seq": 12, "kind": "turn", "text": "Hey Ivy, can we try the backchannel?", "source": "mic"},
    ])
    p = run("ears-turn", tmp_path, "10", "--wait", "3")
    assert p.returncode == 0
    assert p.stdout.strip() == "08:00:02 12 -: Hey Ivy, can we try the backchannel?"


def test_ears_turn_takes_the_fire_file_and_skips_the_mouth(tmp_path):
    logs = heard(tmp_path, [
        {"ts": at(0), "seq": 20, "kind": "saying", "utt": "u9", "agent": "ivy", "text": "Sure.", "source": "mouth"},
        {"ts": at(1), "seq": 21, "kind": "partial", "text": "Ivy, one", "source": "mic"},
        {"ts": at(2), "seq": 22, "kind": "turn", "text": "Sure.", "source": "mic"},       # the mouth, heard back
        {"ts": at(3), "seq": 23, "kind": "turn", "text": "Ivy, one more thing.", "source": "mic"},
    ])
    (logs / "speakers_2030-01-07.jsonl").write_text(
        json.dumps({"seq": 22, "speaker": "ivy"}) + "\n" + json.dumps({"seq": 23, "speaker": "theo"}) + "\n")
    fire = tmp_path / "fire.output"
    fire.write_text(json.dumps({"seq": 21, "ts": at(1), "kind": "partial", "rule": "first"}) + "\n\n[exited with code 0]\n")
    p = run("ears-turn", tmp_path, str(fire), "--wait", "3")
    assert p.returncode == 0
    assert p.stdout.strip() == "08:00:03 23 theo: Ivy, one more thing."


def test_ears_turn_times_out_with_the_partials(tmp_path):
    heard(tmp_path, [{"ts": at(0), "seq": 30, "kind": "partial", "text": "Hey Ivy", "source": "mic"}])
    p = run("ears-turn", tmp_path, "30", "--wait", "1")
    assert p.returncode == 124 and "Hey Ivy" in p.stdout


# -- heard-first --linger: S seconds since he last spoke, not since the arm (a turn
# can run more than a minute, and --timeout cut a turn mid-flight) --
def _talk(tmp, linger_flag, n_partials, then_turn):
    import time
    logs = heard(tmp, [])
    state = tmp / "heard.state"
    state.write_text(json.dumps({"offsets": {str(logs / DAY): 0}}))
    p = subprocess.Popen([sys.executable, str(BIN / "heard-first"), "--rule", "turn", "--state", str(state),
                          *linger_flag], stdout=subprocess.PIPE, text=True,
                         env={"VOICEMODE_BASE_DIR": str(tmp), "PATH": "/usr/bin:/bin"})
    with open(logs / DAY, "a") as f:
        for i in range(n_partials):
            time.sleep(1.0)
            now = datetime.now().astimezone().isoformat()
            f.write(json.dumps({"ts": now, "seq": i, "kind": "partial", "text": f"word {i}", "source": "mic"}) + "\n"); f.flush()
        if then_turn:
            now = datetime.now().astimezone().isoformat()
            f.write(json.dumps({"ts": now, "seq": 99, "kind": "turn", "text": "the whole long turn", "source": "mic"}) + "\n"); f.flush()
    out, _ = p.communicate(timeout=20)
    return p.returncode, out


def test_linger_lets_a_long_turn_finish(tmp_path):
    code, out = _talk(tmp_path, ["--linger", "2"], 4, True)   # 4 s of talk > 2 s linger
    assert code == 0 and "the whole long turn" in out


def test_timeout_cuts_that_turn(tmp_path):
    code, out = _talk(tmp_path, ["--timeout", "2"], 4, True)  # the old light: gone mid-turn
    assert code == 124


def test_linger_ends_in_quiet(tmp_path):
    code, out = _talk(tmp_path, ["--linger", "1.5"], 0, False)
    assert code == 124 and '"linger": true' in out


# -- the echo gate (a seat's own line, played on the laptop speakers, once woke it) --
LINE = "I'm near thirty percent, so I'm writing my summary and next letter, then a fresh me picks up."


def _echo_case(tmp, turn_text, turn_end, span, sidecar=None):
    recs = [{"ts": at(0), "seq": 1, "kind": "saying", "utt": "u1", "agent": "vera", "source": "mouth", "text": LINE},
            {"ts": at(8), "seq": 2, "kind": "said", "utt": "u1", "agent": "vera", "source": "mouth"},
            {"ts": at(turn_end), "seq": 3, "kind": "turn", "text": turn_text, "source": "mic",
             "t0": 100.0, "t1": 100.0 + span}]
    logs = heard(tmp, recs)
    if sidecar is not None:
        (logs / "speakers_2030-01-07.jsonl").write_text(json.dumps({"seq": 3, **sidecar}) + "\n")
    state = tmp / "heard.state"
    state.write_text(json.dumps({"offsets": {str(logs / DAY): 0}}))
    p = run("heard-first", tmp, "--rule", "reply:vera", "--state", str(state), "--timeout", "3")
    return p.returncode


def test_a_late_turn_row_is_judged_by_its_start(tmp_path):
    # the words ARE the line; the row lands 4 s after the line ended, but began inside it
    assert _echo_case(tmp_path, LINE, turn_end=12, span=10) == 124


def test_the_side_car_catches_what_the_words_miss(tmp_path):
    # 77% of the words (whisper: "30%", "freshman", "Thank you.") - the side-car matched it
    heardtext = "I'm near 30%, so I'm writing my summary and next letter, then a freshman picks up. Thank you."
    assert _echo_case(tmp_path, heardtext, 12, 10, {"speaker": "vera", "mouth_utt": "u1"}) == 124


def test_him_talking_over_the_line_still_wakes(tmp_path):
    # his own words, over and after the line: the side-car names him, no mouth_utt
    assert _echo_case(tmp_path, "Hang on, what about the backup?", 10, 4, {"speaker": "theo"}) == 0


# -- a turn begun over the line wakes but does not spend it (once
# "Hey walter, can you let me know?" began 2 s into his question and took the reply; his real
# answer 66 s after the line ended woke nothing --
@pytest.mark.parametrize("said_first,rearmed", [(False, False), (True, False), (False, True)])
def test_a_turn_over_the_line_leaves_the_reply_for_the_answer(tmp_path, said_first, rearmed):
    over = {"ts": at(14 if said_first else 4), "kind": "turn", "text": "Hey walter, can you let me know?",
            "source": "mic", "t0": 100.0, "t1": 100.0 + (13 if said_first else 3)}
    said = {"ts": at(13), "kind": "said", "utt": "u1", "agent": "walter", "source": "mouth"}
    recs = [{"ts": at(0), "kind": "saying", "utt": "u1", "agent": "walter", "source": "mouth",
             "text": "Is that note about the lingering fix, or about the cleaning bucket?"},
            *([said, over] if said_first else [over, said]),
            {"ts": at(79), "kind": "turn", "text": "It was about the cleaning bucket.", "source": "mic",
             "t0": 200.0, "t1": 205.0}]
    logs = heard(tmp_path, recs)
    state = tmp_path / "heard.state"
    # rearmed: the waiter was armed after the line began - the tail scan must learn it
    skip = len(json.dumps(recs[0])) + 1 if rearmed else 0
    state.write_text(json.dumps({"offsets": {str(logs / DAY): skip}}))
    args = ("--rule", "reply:walter", "--state", str(state), "--timeout", "1")
    p = run("heard-first", tmp_path, *args)
    assert p.returncode == 0 and "let me know" in p.stdout, p.stdout + p.stderr
    p = run("heard-first", tmp_path, *args)
    assert p.returncode == 0 and "cleaning bucket" in p.stdout, p.stdout + p.stderr


def test_a_turn_after_the_line_still_spends_it(tmp_path):
    recs = [{"ts": at(0), "kind": "saying", "utt": "u1", "agent": "walter", "source": "mouth",
             "text": "Is that note about the lingering fix, or about the cleaning bucket?"},
            {"ts": at(8), "kind": "said", "utt": "u1", "agent": "walter", "source": "mouth"},
            {"ts": at(20), "kind": "turn", "text": "The bucket.", "source": "mic", "t0": 100.0, "t1": 102.0},
            {"ts": at(40), "kind": "turn", "text": "Anyway, lunch.", "source": "mic", "t0": 120.0, "t1": 122.0}]
    logs = heard(tmp_path, recs)
    state = tmp_path / "heard.state"
    state.write_text(json.dumps({"offsets": {str(logs / DAY): 0}}))
    args = ("--rule", "reply:walter", "--state", str(state), "--timeout", "1")
    p = run("heard-first", tmp_path, *args)
    assert p.returncode == 0 and "The bucket" in p.stdout, p.stdout + p.stderr
    assert run("heard-first", tmp_path, *args).returncode == 124  # one wake per line


# -- a turn he opens to someone else is not a reply (once "Hey Vera,
# what should we be doing next?" began 10 s after the walter's line ended and woke reply:walter) --
@pytest.mark.parametrize("text,want", [
    ("Hey Vera, what should we be doing next?", 124),          # named someone else
    ("Oh hey Vera, so do we actually need voice mode?", 124),  # a filler before the opener
    ("Hey walter, it doesn't seem to have worked. Hey Ivy, the walter didn't hear me.", 0),
    ("Vera said the bucket is coming from Shepparton.", 0),   # a name, no opener: still his reply
    ("Hey, so what's next?", 0),                               # an opener, no agent's name
])
def test_a_turn_opened_to_another_agent_is_not_a_reply(tmp_path, text, want):
    recs = [{"ts": at(-30), "kind": "saying", "utt": "c1", "agent": "vera", "source": "mouth",
             "text": "The roadmap has six milestones."},
            {"ts": at(-25), "kind": "said", "utt": "c1", "agent": "vera", "source": "mouth"},
            {"ts": at(0), "kind": "saying", "utt": "u1", "agent": "walter", "source": "mouth",
             "text": "I'll write the note now and tell you when it's done."},
            {"ts": at(14), "kind": "said", "utt": "u1", "agent": "walter", "source": "mouth"},
            {"ts": at(60), "kind": "turn", "text": text, "source": "mic", "t0": 100.0, "t1": 135.0}]
    logs = heard(tmp_path, recs)
    state = tmp_path / "heard.state"
    state.write_text(json.dumps({"offsets": {str(logs / DAY): 0}}))
    p = run("heard-first", tmp_path, "--rule", "reply:walter", "--state", str(state), "--timeout", "1")
    assert p.returncode == want, p.stdout + p.stderr
