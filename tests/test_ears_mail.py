"""ears-mail: the keeper decides once who a turn is for, and mails them.

Each case is a synthetic heard log run through keeper/ears-mail.py with
--dry-run --once --from-start; the mails it would send are compared. The
cases before "the device mail" run --to seats (the pre-genie mail, whose
verdicts the device mail's Ears-Rule reuses); the device cases run the
default, --to device (EARS-6).
"""
import json, os, re, subprocess, sys, tempfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "keeper" / "ears-mail.py"
RULES = {
    "defaults": {"openers": "hey|hay|a"},
    "seats": {
        "ivy": {"names": "ivy|ivey|ivie"},
        "vera": {"names": "vera|vira", "name_first": True},
        "walter": {"names": "walter", "openers": "hey|hay"},
    },
}


def ts(sec):
    return f"2030-06-09T01:00:{sec:06.3f}+10:00"


def turn(sec, text, span=2.0, seq=None, **kw):
    return dict(ts=ts(sec), kind="turn", source="mic", device="airpods", text=text,
                t0=100.0, t1=100.0 + span, seq=seq or int(sec * 10), **kw)


def saying(sec, agent, text, utt, interest="normal"):
    return dict(ts=ts(sec), kind="saying", agent=agent, text=text, utt=utt, interest=interest)


def said(sec, agent, utt, **kw):
    return dict(ts=ts(sec), kind="said", agent=agent, utt=utt, **kw)


def run(records, rules=RULES, seats=None, args=(), to="seats"):
    with tempfile.TemporaryDirectory() as d:
        logs = Path(d, "logs", "conversations")
        logs.mkdir(parents=True)
        (logs / "heard_2030-06-09.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
        rf = Path(d, "wake.json")
        rf.write_text(json.dumps(rules))
        cmd = [sys.executable, str(SCRIPT), "--rules", str(rf), "--dry-run", "--once", "--from-start",
               "--domain", "host1"] + (["--to", to] if to else [])
        if seats:
            cmd += ["--seats", seats]
        cmd += list(args)
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                           env=dict(os.environ, VOICEMODE_BASE_DIR=d))
        assert p.returncode == 0, p.stderr
        run.stderr = p.stderr
        return [json.loads(l) for l in p.stdout.splitlines() if l.strip()]


def who(mails):
    return [(m["seq"], m["to"], m["rule"]) for m in mails]


def headers_of(args):
    """The --header pairs of `message send`'s arguments, as a dict."""
    return dict(args[i + 1].split(": ", 1) for i, a in enumerate(args) if a == "--header")


def test_hey_name_wakes_that_seat_only():
    m = run([turn(1, "Hey Ivy, what's the latency?", seq=1)])
    assert who(m) == [(1, ["ivy"], "ivy=call")]
    assert m[0]["mail"]["body"] == "Hey Ivy, what's the latency?\n"
    assert m[0]["mail"]["headers"]["Ears-Kind"] == "spoken"


def test_a_mention_is_not_a_call():
    assert run([turn(1, "I think Ivy said the keeper should mail it.")]) == []


def test_hey_everyone_is_one_send_per_seat_each_with_the_whole_verdict():
    # EARS-381 R3: one send per seat, so one dead box cannot silence the rest; each
    # copy keeps the one Ears-Rule, so it tells the whole verdict
    m = run([turn(1, "Hey everyone, please commit your work.", seq=7)])
    rule = "ivy=everyone, vera=everyone, walter=everyone"
    assert who(m) == [(7, ["ivy"], rule), (7, ["vera"], rule), (7, ["walter"], rule)]


def test_opener_a_is_per_seat():
    # the walter seat dropped 'a' (two false wakes on "a walter"); Ivy kept it
    m = run([turn(1, "It's a walter thing, a Ivy thing too.", seq=3)])
    assert who(m) == [(3, ["ivy"], "ivy=call")]


def test_name_first_only_where_asked():
    m = run([turn(1, "Vera, queue the film.", seq=4), turn(5, "Ivy, the side-car?", seq=5)])
    assert who(m) == [(4, ["vera"], "vera=first")]


def test_reply_to_the_seat_that_spoke_last():
    m = run([saying(1, "ivy", "Shall I build it on a branch?", "u1"), said(4, "ivy", "u1"),
             turn(9, "Yep, sure, go for it.", span=1.5, seq=9)])
    assert who(m) == [(9, ["ivy"], "ivy=reply")]


def test_a_turn_in_flight_is_not_a_reply():
    # his answer to Vera began 0.4 s before Ivy's line was queued
    m = run([saying(35.0, "ivy", "AirPods. My vote's in the thread.", "u2"),
             turn(37.6, "it's in the pipeline, it writes it to a log.", span=3.0, seq=58815),
             said(49, "ivy", "u2"),
             turn(56.1, "Yep, sure, go for it Ivy, yes.", span=2.2, seq=58830)])
    # no "hey", but it names Ivy as it answers her line: a call (EARS-18)
    assert who(m) == [(58830, ["ivy"], "ivy=call")]


def test_a_named_reply_is_a_call():
    # EARS-18, seq 160899, Sun 2030-06-09: it named Vera twice, came out
    # vera=reply, rode, and she slept 5 minutes.
    m = run([saying(1, "vera", "The genie is live. Anything else before the meetup?", "u20"),
             said(4, "vera", "u20"),
             turn(9, "Okay, thanks, Vera. So Vera, are there any other important things to do?",
                  span=4, seq=160899)])
    assert who(m) == [(160899, ["vera"], "vera=call")]


def test_a_named_reply_is_one_wake_per_line():
    m = run([saying(1, "vera", "Which film?", "u21"), said(2, "vera", "u21"),
             turn(5, "The detective, Vera.", span=1, seq=5), turn(9, "The first one.", span=1, seq=9)])
    assert who(m) == [(5, ["vera"], "vera=call")]


def test_a_reply_naming_another_seat_stays_a_reply():
    # a bare "Ivy" is no call to Ivy (finding 3.6); it still answers Vera's line
    m = run([saying(1, "vera", "Which film?", "u22"), said(2, "vera", "u22"),
             turn(5, "Ask Ivy, she picked it.", span=1, seq=5)])
    assert who(m) == [(5, ["vera"], "vera=reply")]


def test_a_named_turn_with_no_line_to_answer_is_unchanged():
    assert run([turn(1, "Okay, thanks, Vera. So Vera, anything else?", seq=1)]) == []
    m = run([saying(1, "vera", "Done.", "u23", interest="zero"), said(2, "vera", "u23"),
             turn(5, "Thanks, Vera.", span=1, seq=5)])
    assert m == []   # a zero-interest line is still never answered


def test_one_reply_wake_per_line():
    m = run([saying(1, "vera", "Which film?", "u3"), said(2, "vera", "u3"),
             turn(5, "The detective.", span=1, seq=5), turn(9, "The first one.", span=1, seq=9)])
    assert who(m) == [(5, ["vera"], "vera=reply")]


def test_zero_interest_never_takes_a_reply():
    m = run([saying(1, "ivy", "Done.", "u4", interest="zero"), said(2, "ivy", "u4"),
             turn(5, "Thanks.", span=1, seq=5)])
    assert m == []


def test_echo_of_the_mouth_is_not_him():
    m = run([saying(1, "vera", "Hey Ivy, your film side-car is running.", "u5"),
             turn(4, "hey ivy your film side-car is running", span=2, seq=4), said(5, "vera", "u5")])
    assert m == []


def test_light_after_a_line():
    rules = json.loads(json.dumps(RULES))
    rules["seats"]["ivy"]["light"] = 60
    m = run([saying(1, "ivy", "Question?", "u6"), said(3, "ivy", "u6"),
             turn(10, "First answer.", span=1, seq=10), turn(30, "And another thing.", span=1, seq=30)],
            rules=rules)
    assert who(m) == [(10, ["ivy"], "ivy=reply"), (30, ["ivy"], "ivy=light")]


def test_cut_tells_the_seat():
    m = run([saying(1, "ivy", "A long line", "u7"), said(2, "ivy", "u7", cut=True, reason="barge-in")])
    assert who(m) == [(None, ["ivy"], "ivy=cut")]
    assert m[0]["subject"] == "your line was cut: barge-in"


def test_keeper_skips_a_cut_the_mouth_told():
    # EARS-164: the mouth told the sender itself (by mail, or a live --wait waiter)
    m = run([said(1, "ivy", "u1", cut=True, reason="device-absent", notice="sent ivy@host2"),
             said(2, "ivy", "u2", cut=True, reason="device-absent", notice="waiter")])
    assert m == []


def test_keeper_still_tells_when_the_mouth_could_not():
    # a notice that never reached its sender leaves the keeper's notice in place,
    # and so does an older mouth's row, with no notice at all
    m = run([said(1, "ivy", "u1", cut=True, reason="error", notice="failed: message send exit 75"),
             said(2, "ivy", "u2", cut=True, reason="stop", notice="none: off"),
             said(3, "ivy", "u3", cut=True, reason="barge-in", notice="none: no box for session s"),
             said(4, "ivy", "u4", cut=True, reason="expired")])
    assert [x["subject"] for x in m] == ["your line was cut: error", "your line was cut: stop",
                                         "your line was cut: barge-in", "your line was cut: expired"]


# EARS-204, replayed from Sun 2030-06-09 (seq 415083-415092), in seconds on the
# replay's clock: Vera's line was queued at 8.0, he began "So the secret word is
# pajama party." at 15.8, the mouth cut her line for the barge-in at 18.4, and his
# turn ended at 19.4. The cut came 2.7 s after he began, so no line had "finished" before
# his turn and it woke nobody: "I spoke again, but you didn't hear me."
BARGE = [saying(8.0, "vera", "Right, I never re-arm it. It's one process.", "c1"),
         said(18.4, "vera", "c1", cut=True, reason="barge-in", notice="sent vera@host2",
              played_s=10.09, dur_s=15.75, device="airpods",
              text_played_est="Right, I never re-arm it. It's one")]


def test_a_barge_in_is_the_reply_to_the_line_it_cut():
    m = run(BARGE + [turn(19.4, "So the secret word is pajama party.", span=3.7, seq=415092)])
    assert who(m) == [(415092, ["vera"], "vera=reply")]


def test_a_barge_in_beats_an_older_finished_line():
    m = run([saying(1, "ivy", "Pushed.", "p1"), said(3, "ivy", "p1")] + BARGE
            + [turn(19.4, "So the secret word is pajama party.", span=3.7, seq=415092)])
    assert who(m) == [(415092, ["vera"], "vera=reply")]


def test_a_barge_in_is_one_reply_per_line():
    m = run(BARGE + [turn(19.4, "So the secret word is pajama party.", span=3.7, seq=1),
                     turn(25.0, "And another thing.", span=1, seq=2)])
    assert who(m) == [(1, ["vera"], "vera=reply")]


def test_a_barge_in_that_names_the_seat_is_a_call():
    m = run([saying(8.0, "ivy", "I'll merge it now.", "p2"),
             said(10.0, "ivy", "p2", cut=True, reason="barge-in", notice="sent ivy@host2", played_s=2.0),
             turn(11.0, "No Ivy, wait for the review.", span=2.0, seq=3)])
    assert who(m) == [(3, ["ivy"], "ivy=call")]


def test_a_line_cut_for_another_reason_is_not_his_reply():
    m = run([saying(8.0, "vera", "A long line.", "c2"),
             said(18.4, "vera", "c2", cut=True, reason="expired", notice="sent vera@host2"),
             turn(19.4, "So the secret word is pajama party.", span=3.7, seq=4)])
    assert m == []


def test_a_cut_outside_his_turn_is_not_his_barge_in():
    # cut after the turn ended: not this turn's barge-in (a late barge, EARS-192)
    m = run([saying(8.0, "vera", "A long line.", "c3"),
             turn(12.0, "Okay.", span=1.0, seq=5),
             said(14.0, "vera", "c3", cut=True, reason="barge-in", notice="sent vera@host2",
                  played_s=6.0)])
    assert m == []


def test_a_line_dropped_unplayed_by_the_barge_in_is_not_the_one_answered():
    # Sun 2030-06-09 (seqs 394828-394830): the barge-in cut the playing line
    # and dropped the next one with 0.0 s played; he answered the one he heard
    m = run([saying(10.0, "vera", "The ears connect to the host.", "c5"),
             saying(10.5, "vera", "And the keeper mails it.", "c6"),
             said(13.2, "vera", "c5", cut=True, reason="barge-in", played_s=2.77, notice="sent vera@host2"),
             said(13.3, "vera", "c6", cut=True, reason="barge-in", played_s=0.0, notice="sent vera@host2"),
             turn(14.0, "There we go. Continue.", span=1.7, seq=7),
             turn(25.0, "Each other, like a child.", span=4.4, seq=8)])
    assert who(m) == [(7, ["vera"], "vera=reply"), (8, ["vera"], "vera=reply")]


def test_a_barge_in_without_played_s_is_judged_as_before():
    # an older mouth's row: no played_s, so its echo can't be told from him
    m = run([saying(8.0, "vera", "Right, I never re-arm it.", "c4"),
             said(18.4, "vera", "c4", cut=True, reason="barge-in", notice="sent vera@host2"),
             turn(19.4, "So the secret word is pajama party.", span=3.7, seq=6)])
    assert m == []


def test_a_barge_in_tripped_by_the_lines_own_echo_is_not_a_reply():
    # seq 59682 with today's played fields: the speakers' echo of Vera's line
    # tripped the barge guard; one misheard word ("loads") survives the trim
    played = ("Load's down to four, from fifteen. The audio system is still top, "
              "at eighty-six percent. And a corr")
    rest = "And a correction: Ivy says her keeper doesn't restart when the iPhone mic flaps."
    m = run([saying(10, "vera", LOAD, "c2"),
             said(16, "vera", "c2", cut=True, reason="barge-in", played_s=6.0, text_played_est=played),
             turn(16.5, "loads down to four from 15, the audio system is still top at 86%. And a current...",
                  span=5, seq=59682),
             saying(18, "vera", rest, "c3"),
             turn(26, "Yeah, okay. And a correction, Ivy says her keeper doesn't restart. The phone mic flaps.",
                  span=7, seq=59692),
             said(27, "vera", "c3")])
    assert who(m) == [(None, ["vera"], "vera=cut"), (59692, ["vera"], "vera=reply")]


def test_seats_narrows_for_side_by_side():
    m = run([turn(1, "Hey everyone, stand up.", seq=1)], seats="ivy")
    assert who(m) == [(1, ["ivy"], "ivy=everyone")]


def test_seats_narrows_the_mail_not_the_names():
    # Sun 2030-06-09, seq 59778: run with --seats ivy, "Hey Vera, ..." after
    # Ivy's line woke Ivy as a reply, because Vera's names were never loaded.
    m = run([saying(1, "ivy", "Done.", "u10"), said(3, "ivy", "u10"),
             turn(9, '"Hey Vera, can you install Grafana?"', span=1, seq=9)], seats="ivy")
    assert m == []


def test_subject_is_short_and_safe():
    long = "Hey Ivy, " + "the `keeper` mails $(whoami) " * 10
    m = run([turn(1, long, seq=1)])
    s = m[0]["subject"]
    assert len(s) <= 69 and "`" not in s and "$(" not in s
    assert m[0]["mail"]["body"] == long + "\n"   # the body is whole


def test_the_mail_carries_its_facts_in_headers():
    import importlib.util
    spec = importlib.util.spec_from_file_location("ears_mail", SCRIPT)
    em = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(em)
    rec = turn(1, "Hey everyone, stand up.", seq=42)
    m = em.mail_for(rec, ["ivy", "vera"], {"ivy": "everyone", "vera": "everyone"})
    with pytest.raises(ValueError, match="one recipient per send"):
        em.build(m, "ears", "host1")                 # EARS-381 R3: never one mail to several
    args = em.build(dict(m, to=["vera"]), "ears", "host1")
    assert args[:2] == ["send", "vera@host1"]
    h = headers_of(args)
    assert h["Ears-Kind"] == "spoken" and h["Ears-Seq"] == "42"
    assert h["Ears-Rule"] == "ivy=everyone, vera=everyone"
    assert not [k for k in h if k.lower().startswith("x-")]   # /RULINGS 3970
    assert "--message-id" not in args                # RULING B: the send mints it
    env = em.child_env("ears", {})
    assert env["TM_AGENT_ADDRESS"] == "ears" and env["SESSIONMAIL_FROM_NAME"] == "Ears"
    assert m["body"] == "Hey everyone, stand up.\n"


def test_a_line_not_yet_played_cannot_be_answered():
    # the walter seat's line ended; Ivy's was queued 2.2 s later
    # and hadn't played when he began "Okay, and then."
    m = run([saying(6.6, "walter", "Every role reads its list.", "d1"), said(18.6, "walter", "d1"),
             saying(20.8, "ivy", "Mine's pushed.", "p1"),
             turn(24.0, "Okay, and then.", span=2.7, seq=57945),
             said(31.6, "ivy", "p1")])
    assert who(m) == [(57945, ["walter"], "walter=reply")]


def test_an_addressed_turn_is_nobody_elses_reply():
    m = run([saying(1, "ivy", "Done.", "u8"), said(3, "ivy", "u8"),
             turn(9, "Hey Vera, queue the film.", span=1, seq=9)])
    assert who(m) == [(9, ["vera"], "vera=call")]


def test_a_reply_has_a_window():
    m = run([saying(1, "ivy", "Done.", "u9"), said(3, "ivy", "u9"),
             dict(turn(0, "Right.", span=1, seq=200), ts="2030-06-09T01:05:00.000+10:00")])
    assert m == []   # 4 minutes later is not an answer


KODI = "It's not in the library: the file sits in a folder Kodi doesn't scan. I've put it back up on the TV, paused at the start."


def test_a_leading_echo_is_cut_and_the_rest_answers_that_line():
    # Sun 2030-06-09, seq 60043: on the MacBook speakers the mic heard
    # Vera's line and joined his question to it; the keeper woke Ivy as a reply.
    m = run([saying(1, "ivy", "Yes, I hear you.", "p1"), said(3, "ivy", "p1"),
             saying(20, "vera", KODI, "c1"), said(28, "vera", "c1"),
             turn(34, "It's not in the library. The file sits in a folder Cody doesn't scan. "
                      "I've put it back up on the TV, paused at the start. "
                      "All right, why is it not in the library?", span=12, seq=60043)])
    assert who(m) == [(60043, ["vera"], "vera=reply")]
    assert m[0]["subject"] == "All right, why is it not in the library?"
    assert "echo included" in m[0]["mail"]["body"]


def test_a_late_whole_echo_wakes_nobody():
    m = run([saying(1, "ivy", "Yes, I hear you.", "p1"), said(3, "ivy", "p1"),
             saying(20, "vera", KODI, "c1"), said(28, "vera", "c1"),
             turn(34, "It's not in the library. The file sits in a folder Cody doesn't scan. "
                      "I've put it back up on the TV, paused at the start.", span=12, seq=60044)])
    assert m == []


def test_his_own_words_first_are_not_trimmed():
    m = run([saying(1, "vera", KODI, "c1"), said(9, "vera", "c1"),
             turn(12, "Hey Ivy, is it not in the library?", span=2, seq=7)])
    assert who(m) == [(7, ["ivy"], "ivy=call")]


def test_the_start_of_a_line_that_began_mid_turn_is_echo_too():
    # Sun, seq 59682: his "Yeah, okay" came next; the fragment must not take the reply.
    m = run([saying(10, "vera", "Load's down to four, from fifteen. The audio system is still top, at eighty-six percent.", "c2"),
             said(16, "vera", "c2"),
             saying(18.2, "vera", "And a correction: Ivy says her keeper doesn't restart when the iPhone mic flaps.", "c3"),
             turn(18.5, "loads down to four from 15, the audio system is still top at 86%. And a current...", span=5, seq=59682),
             said(29, "vera", "c3"),
             turn(30.5, "Yeah, okay.", span=1, seq=59692)])
    assert who(m) == [(59692, ["vera"], "vera=reply")]


LOAD = ("Load's down to four, from fifteen. The audio system is still top, at eighty-six percent. "
        "And a correction: Ivy says her keeper doesn't restart when the iPhone mic flaps.")


def test_a_clipped_word_at_a_cut_is_echo_and_his_words_before_an_echo_are_his():
    # Sun, seqs 59682/59692: Vera's line was cut at "And a corr-"; she
    # re-said the rest; his "Yeah, okay." came before its echo.
    rest = "And a correction: Ivy says her keeper doesn't restart when the iPhone mic flaps."
    m = run([saying(10, "vera", LOAD, "c2"), said(16, "vera", "c2", cut=True, reason="barge-in"),
             turn(16.5, "loads down to four from 15, the audio system is still top at 86%. And a current...",
                  span=5, seq=59682),
             saying(18, "vera", rest, "c3"),
             turn(26, "Yeah, okay. And a correction, Ivy says her keeper doesn't restart. The phone mic flaps.",
                  span=7, seq=59692),
             said(27, "vera", "c3")])
    assert who(m) == [(None, ["vera"], "vera=cut"), (59692, ["vera"], "vera=reply")]
    assert m[1]["subject"].startswith("Yeah, okay.")



# -- twins: the call leg and the room mic hear the same words ----------------

def on_call(rec):
    return dict(rec, source="call", device="delta:ivy/12")


def test_a_call_line_and_the_room_mics_copy_mail_once():
    """Sun: seq 61416 (call) and 61417 (mic), 0.43 s apart, both mailed."""
    m = run([on_call(turn(1.0, "Hey Ivy, with your questions, you've got tag triage, yes", seq=1)),
             turn(1.4, "Hey, Ivy, with your questions. You've got tag triage. Yes", seq=2)])
    assert who(m) == [(1, ["ivy"], "ivy=call")]


def test_the_same_source_twice_is_him_repeating_himself():
    m = run([turn(1.0, "Hey Ivy, are you there?", seq=1),
             turn(4.0, "Hey Ivy, are you there?", seq=2)])
    assert [s for s, _, _ in who(m)] == [1, 2]


def test_a_twin_outside_the_window_is_mailed():
    m = run([on_call(turn(1.0, "Hey Ivy, check the build", seq=1)),
             turn(20.0, "Hey Ivy, check the build", seq=2)])
    assert [s for s, _, _ in who(m)] == [1, 2]


def test_different_words_from_the_other_ear_are_mailed():
    m = run([on_call(turn(1.0, "Hey Ivy, check the build", seq=1)),
             turn(2.0, "Hey Ivy, what did Vera say about the battery alarm?", seq=2)])
    assert [s for s, _, _ in who(m)] == [1, 2]


# -- a film is playing: only a turn that names a seat wakes it -----------------
# Replay Sun 2030-06-09 with two Ivy lines put in mid-film: the
# film's dialogue (60116) and "17." (60762) were mailed to Ivy as replies.

class KodiStub:
    """Kodi's JSON-RPC: SPEEDS are the answers to Player.GetProperties, in order
    (the last one repeats); None = no video player at all."""

    def __init__(self, *speeds):
        import http.server, threading
        self.speeds, self.asked = list(speeds), 0
        stub = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                speed = stub.speeds[min(stub.asked, len(stub.speeds) - 1)]
                if req["method"] == "Player.GetActivePlayers":
                    res = [] if speed is None else [{"playerid": 1, "type": "video"}]
                else:
                    stub.asked += 1
                    res = {"speed": speed, "time": {"hours": 0, "minutes": 12, "seconds": 5}}
                body = json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": res}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}/jsonrpc"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.srv.shutdown()


LINE = [saying(1, "ivy", "The side-car is logging.", "f1"), said(3, "ivy", "f1")]


def test_a_film_playing_is_nobodys_reply():
    with KodiStub(1) as k:
        m = run(LINE + [turn(9, "We have just received word that the villain is planning a trap.", seq=60116)],
                args=["--kodi", k.url])
    assert m == []
    skip = [json.loads(l) for l in run.stderr.splitlines() if l.startswith('{"skip": "film"')]
    assert skip == [{"skip": "film", "seq": 60116, "seats": ["ivy"], "rules": ["reply"],
                     "media": "kodi playing at 0:12:05"}]


def test_a_named_turn_wakes_through_a_film():
    with KodiStub(1) as k:
        m = run(LINE + [turn(9, "Hey Ivy, pause that.", seq=9)], args=["--kodi", k.url])
    assert who(m) == [(9, ["ivy"], "ivy=call")]
    assert k.asked == 0   # a named turn never asks Kodi


def test_a_named_reply_wakes_through_a_film():
    # EARS-18: a reply by name is a call, and a call never waits on the film
    with KodiStub(1) as k:
        m = run(LINE + [turn(9, "Yeah, pause it, Ivy.", seq=9)], args=["--kodi", k.url])
    assert who(m) == [(9, ["ivy"], "ivy=call")]
    assert k.asked == 0


def test_a_paused_film_lets_his_reply_through():
    with KodiStub(0) as k:
        m = run(LINE + [turn(9, "Good, leave it running.", seq=9)], args=["--kodi", k.url])
    assert who(m) == [(9, ["ivy"], "ivy=reply")]


def test_the_line_stays_answerable_after_the_film_turn():
    with KodiStub(1, 0) as k:   # the film talks, then he pauses and answers
        m = run(LINE + [turn(9, "Seventeen.", seq=9), turn(20, "Yes, score it later.", seq=20)],
                args=["--kodi", k.url])
    assert who(m) == [(20, ["ivy"], "ivy=reply")]


def test_nothing_playing_lets_his_reply_through():
    with KodiStub(None) as k:
        m = run(LINE + [turn(9, "Good.", seq=9)], args=["--kodi", k.url])
    assert who(m) == [(9, ["ivy"], "ivy=reply")]


def test_kodi_unreachable_is_todays_behaviour():
    m = run(LINE + [turn(9, "Good.", seq=9)], args=["--kodi", "http://127.0.0.1:9/jsonrpc"])
    assert who(m) == [(9, ["ivy"], "ivy=reply")]
    assert '"kodi": "unreachable"' in run.stderr


def test_light_waits_out_a_film_too():
    rules = json.loads(json.dumps(RULES))
    rules["seats"]["vera"]["light"] = 60
    rules["seats"]["vera"]["reply"] = False
    with KodiStub(1) as k:
        m = run([saying(1, "vera", "Moved it.", "c1"), said(3, "vera", "c1"),
                 turn(9, "Oh, behave!", seq=9)], rules=rules, args=["--kodi", k.url])
    assert m == []


# -- the device mail (EARS-6, genie X1's finished-turn slice) ------------------
# Every finished turn of his is ONE mail To and From heard+room@<host>, whatever
# input device the capture follows, Cc heard+<device>@<host> on the same send
# (R10, P-2); Ears-Rule is keyed by address (N10); no X- headers (3970);
# no drafts, partials or calls. The contract is sessionmail's device-mail
# format, as of sessionmail d156f13.

def dev(records, rules=RULES, args=()):
    return run(records, rules, args=args, to=None)        # the default is --to device


def em_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("ears_mail", SCRIPT)
    em = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(em)
    return em


def test_device_every_finished_turn_is_mailed_to_its_device():
    m = dev([turn(1, "Hey Ivy, what's the latency?", seq=1),
             turn(5, "I think Ivy said the keeper should mail it.", seq=2),
             turn(9, "Right.", seq=3)])
    assert who(m) == [(1, ["heard+room"], "ivy@host1=call"),
                      (2, ["heard+room"], ""), (3, ["heard+room"], "")]
    assert all(x["mail"]["from"] == "heard+room" for x in m)
    assert all(x["mail"]["cc"] == x["cc"] == ["heard+airpods"] for x in m)   # the device's copy


def test_device_hey_everyone_is_one_everyone_entry():
    m = dev([turn(1, "Hey everyone, please commit your work.", seq=7)])
    assert who(m) == [(7, ["heard+room"], "everyone=everyone")]


def test_device_rules_are_keyed_by_address():
    m = dev([saying(1, "ivy", "Shall I build it on a branch?", "u1"), said(4, "ivy", "u1"),
             turn(9, "Yep, sure, go for it.", span=1.5, seq=9),
             turn(20, "Vera, queue the film.", seq=10),
             turn(30, "Hey everyone, and hey Ivy, stand up.", seq=11)])
    assert [r for _, _, r in who(m)] == ["ivy@host1=reply", "vera@host1=first",
                                         "everyone=everyone, ivy@host1=call"]


def test_device_a_barge_in_carries_the_seats_reply_entry():
    # Vera's seat carries `ears-rule:vera@*=reply`: this entry is what wakes her
    m = dev(BARGE + [turn(19.4, "So the secret word is pajama party.", span=3.7, seq=415092)])
    assert who(m) == [(415092, ["heard+room"], "vera@host1=reply")]


def test_device_the_built_in_mic_is_macbook_and_says_its_name():
    m = dev([dict(turn(1, "Hey Ivy, are you there?", seq=1), device="MacBook Pro Microphone")])
    assert who(m) == [(1, ["heard+room"], "ivy@host1=call")]
    assert m[0]["cc"] == ["heard+macbook"]
    assert m[0]["mail"]["headers"]["Ears-Device"] == "MacBook Pro Microphone"


def test_device_calls_are_not_this_slice():
    m = dev([on_call(turn(1, "Hey Ivy, on the call", seq=1)),
             dict(turn(3, "Hey Ivy, through the tap", seq=2), device="BlackHole 16ch"),
             turn(5, "Hey Ivy, in the room", seq=3)])
    assert who(m) == [(3, ["heard+room"], "ivy@host1=call")]
    skips = [json.loads(x) for x in run.stderr.splitlines() if x.startswith('{"skip"')]
    assert [(k["skip"], k["seq"]) for k in skips] == [("call", 1), ("device", 2)]


def test_device_echo_of_the_mouth_is_not_his_turn():
    line = "The build is green and the branch is pushed."
    m = dev([saying(1, "ivy", line, "u1"),
             turn(3, line, seq=2),                                    # all echo: not mailed
             said(4, "ivy", "u1"),
             turn(5, "The build is green and the branch is pushed. Great, merge it, then tell Vera "
                     "the keeper is ready for its review this afternoon.", span=4, seq=3)])
    assert [s for s, _, _ in who(m)] == [3]
    assert m[0]["mail"]["body"] == ("Great, merge it, then tell Vera the keeper is ready for its "
                                    "review this afternoon.\n")   # his words only (review S1)


def test_device_a_turn_during_a_film_is_mailed_without_its_reply():
    with KodiStub(1) as k:
        m = dev(LINE + [turn(9, "We have just received word that the villain is planning a trap.", seq=60116)],
                args=("--kodi", k.url))
    assert who(m) == [(60116, ["heard+room"], "")]      # mailed; the reply is held
    skip = [json.loads(x) for x in run.stderr.splitlines() if x.startswith('{"skip": "film"')]
    assert skip[0]["seats"] == ["ivy"] and skip[0]["mailed"] == "without those rules"


def test_device_mail_headers_are_the_contract():
    em = em_module()
    rec = turn(1, "Hey Ivy, check the build.", seq=42)
    m = em.mail_for(rec, ["heard+room"], {"ivy": "call"}, rule=em.rule_header({"ivy": "call"}, "host1"),
                    frm="heard+room", speaker="theo@host1", cc=["heard+airpods"])
    args = em.build(m, "heard", "host1")
    msg = headers_of(args)
    # To and From heard+room: To is the argument, From the seat in the child env;
    # the device's address is a --cc on the same send
    assert args[:4] == ["send", "heard+room@host1", "--cc", "heard+airpods@host1"]
    assert args.count("--cc") == 1
    env = em.child_env(m["from"], {})
    assert env["TM_AGENT_ADDRESS"] == env["EMAIL"] == "heard+room"
    assert env["SESSIONMAIL_FROM_NAME"] == "Ears"
    assert msg["Ears-Kind"] == "spoken" and msg["Ears-Rule"] == "ivy@host1=call"
    assert msg["Ears-Seq"] == "42" and msg["Ears-Source"] == "mic" and msg["Ears-Device"] == "airpods"
    assert msg["Ears-Speaker"] == "theo@host1"
    from datetime import datetime
    assert msg["Ears-End"] == rec["ts"]                    # both carry their offset: comparable
    span = datetime.fromisoformat(msg["Ears-End"]) - datetime.fromisoformat(msg["Ears-Start"])
    assert span.total_seconds() == 2.0
    assert not [k for k in msg if k.lower().startswith("x-")]   # /RULINGS 3970
    assert args[args.index("-s") + 1] == "Hey Ivy, check the build."
    assert args[args.index("-f") + 1] == "-"               # the body goes on stdin
    assert m["body"] == "Hey Ivy, check the build.\n"


def test_device_speaker_defaults_to_unknown_at_the_host(monkeypatch):
    """EARS-110 (unknown everywhere): a turn
    nobody placed is unknown@<host>, never theo@: the TV must not wake ears-speaker:theo@*."""
    monkeypatch.delenv("EARS_SPEAKER", raising=False)
    m = dev([turn(1, "Hey Ivy.", seq=1)])
    assert m[0]["mail"]["headers"]["Ears-Speaker"] == "unknown@host1"


def test_device_speaker_is_pinned_per_label_end_to_end(monkeypatch):
    """EARS-110: through main(), no --speaker, no $EARS_SPEAKER (the live plist passes
    neither): unnamed and neutral-only turns mail unknown@host1; named turns are unchanged."""
    monkeypatch.delenv("EARS_SPEAKER", raising=False)
    pins = [(None, "unknown@host1"), ("other", "unknown@host1"), ("mixed", "unknown@host1"),
            ("silent", "unknown@host1"), ("unheard", "unknown@host1"),
            ("theo", "theo@host1"), ("vera", "vera@host1"), ("alice", "alice@host1")]
    recs = [turn(i + 1, "Hey Ivy.", seq=i + 1, **({} if lab is None else {"speaker": lab}))
            for i, (lab, _) in enumerate(pins)]
    m = dev(recs)
    assert [x["mail"]["headers"]["Ears-Speaker"] for x in m] == [want for _, want in pins]


def test_device_a_is_no_longer_an_opener_by_default():
    rules = {"seats": {"ivy": {"names": "ivy"}}}                 # code defaults only
    m = dev([turn(1, "It's a Ivy thing.", seq=1), turn(5, "Hey Ivy, now.", seq=2)], rules)
    assert who(m) == [(1, ["heard+room"], ""), (2, ["heard+room"], "ivy@host1=call")]


def test_device_slug():
    em = em_module()
    assert [em.device_slug(n) for n in ("airpods", "Theo's AirPods Pro", "MacBook Pro Microphone",
                                        "Speakers + AirPods", "BlackHole 16ch", "", None, "USB Mic #2")] \
        == ["airpods", "airpods", "macbook", "airpods", None, None, None, "usb-mic-2"]


def test_device_box_name_is_the_sender():
    """The box is heard: ears+<device> fell into the
    ears project box (review D1). It is --sender, so a rename is configuration."""
    m = dev([turn(1, "Hey Ivy.", seq=1)])
    assert who(m) == [(1, ["heard+room"], "ivy@host1=call")]       # the default
    m = dev([turn(1, "Hey Ivy.", seq=1)], args=("--sender", "other"))
    assert who(m) == [(1, ["other+room"], "ivy@host1=call")]
    assert m[0]["mail"]["from"] == "other+room" and m[0]["cc"] == ["other+airpods"]


# -- one room address, whatever device the capture follows -------------------
# The capture follows the default input; a new device must not move the room's
# turns to an address no session subscribes to. Room turns go To and From
# heard+room; the device stays in Ears-Device and is a Cc on the same send.

def test_device_a_new_input_device_keeps_the_room_address():
    """The regression: the capture moved from the built-in mic to a headset (B3),
    and its turns went To heard+b3, which no session heard. Both go To heard+room."""
    m = dev([dict(turn(1, "Hey Ivy, are you there?", seq=1), device="MacBook Air Microphone"),
             dict(turn(5, "Hey Ivy, and now?", seq=2), device="B3")])
    assert who(m) == [(1, ["heard+room"], "ivy@host1=call"), (2, ["heard+room"], "ivy@host1=call")]
    assert [x["mail"]["from"] for x in m] == ["heard+room", "heard+room"]
    assert [x["cc"] for x in m] == [["heard+macbook"], ["heard+b3"]]
    assert [x["mail"]["headers"]["Ears-Device"] for x in m] == ["MacBook Air Microphone", "B3"]


def test_send_a_room_turn_is_one_send_with_the_device_cc():
    """One message per turn: ONE call of `message send` (so ONE Message-ID; the keeper
    mints none and the send mints one per call), To heard+room AND --cc
    heard+<device> in that one argv, so the device's address still gets the turn,
    and ONE fire-log row with its one id. One write per box is the delivery's
    property, pinned in sessionmail by tests/test_mail_drop.py
    test_no_postfix_and_one_copy_per_box_when_recipients_overlap; the fake
    delivers nothing."""
    rows, calls = send_run([dict(turn(1, "Hey Ivy, check the build.", seq=1), device="B3")], to=None)
    assert len(rows) == len(calls) == 1
    argv = calls[0]["argv"]
    assert argv[1] == "heard+room@host1"
    assert argv[argv.index("--cc"):argv.index("--cc") + 2] == ["--cc", "heard+b3@host1"]
    assert argv.count("--cc") == 1
    assert calls[0]["env"]["TM_AGENT_ADDRESS"] == calls[0]["env"]["EMAIL"] == "heard+room"
    assert rows[0]["to"] == ["heard+room"] and rows[0]["cc"] == ["heard+b3"]
    assert rows[0]["exit"] == 0 and rows[0]["id"] == calls[0]["id"]
    # two turns on two devices: two sends, two ids, never four
    rows, calls = send_run([dict(turn(1, "Hey Ivy, one.", seq=1), device="MacBook Air Microphone"),
                            dict(turn(5, "Hey Ivy, two.", seq=2), device="B3")], to=None)
    assert len(rows) == len(calls) == 2
    assert [c["argv"][1] for c in calls] == ["heard+room@host1", "heard+room@host1"]
    assert [c["argv"][c["argv"].index("--cc") + 1] for c in calls] == \
        ["heard+macbook@host1", "heard+b3@host1"]
    assert len({r["id"] for r in rows}) == 2 and [r["id"] for r in rows] == [c["id"] for c in calls]


def test_device_copy_off_sends_no_cc():
    m = dev([turn(1, "Hey Ivy.", seq=1)], args=("--device-copy", "off"))
    assert who(m) == [(1, ["heard+room"], "ivy@host1=call")]
    assert "cc" not in m[0] and "cc" not in m[0]["mail"]
    rows, calls = send_run([turn(1, "Hey Ivy.", seq=1)], to=None, args=("--device-copy", "off"))
    assert "--cc" not in calls[0]["argv"] and "cc" not in rows[0]
    assert set(rows[0]) == {"at", "seq", "to", "rule", "subject", "exit", "id"}


def test_device_named_like_the_room_gets_no_cc():
    m = dev([dict(turn(1, "Hey Ivy.", seq=1), device="Room")])
    assert who(m) == [(1, ["heard+room"], "ivy@host1=call")] and "cc" not in m[0]


def test_a_blackhole_turn_is_still_not_mailed():
    """The call tap's input (BlackHole) and a turn with no device stay unmailed: the
    room's address needs no slug, but the slug is still the gate."""
    m = dev([dict(turn(1, "Hey Ivy, through the tap", seq=1), device="BlackHole 2ch"),
             dict(turn(3, "Hey Ivy, no device", seq=2), device="")])
    assert m == []
    assert [(k["skip"], k["seq"]) for k in skips()] == [("device", 1), ("device", 2)]


def test_room_and_sender_are_configuration(monkeypatch):
    m = dev([turn(1, "Hey Ivy.", seq=1)], args=("--sender", "other", "--room", "den"))
    assert who(m) == [(1, ["other+den"], "ivy@host1=call")]
    assert m[0]["mail"]["from"] == "other+den" and m[0]["cc"] == ["other+airpods"]
    monkeypatch.setenv("EARS_MAIL_ROOM", "study")
    m = dev([turn(1, "Hey Ivy.", seq=1)])
    assert who(m) == [(1, ["heard+study"], "ivy@host1=call")]
    assert "heard+study@host1" in run.stderr               # the banner names the room


@pytest.mark.parametrize("bad", ["Den", "a+b", "a/b", "a@b", "", "den room"])
def test_a_bad_room_exits_2(bad):
    p = subprocess.run([sys.executable, str(SCRIPT), "--dry-run", "--once", "--room", bad],
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 2 and "--room" in p.stderr


# -- Ears-Speaker: one speaker per turn (EARS-14) -----------------------------

def test_device_a_split_pair_is_two_mails_each_with_its_own_speaker():
    """Alice, then Theo with no pause: the capture writes two turns, each with its
    `speaker` label. Each is mailed on its own, Ears-Speaker LABEL@DOMAIN."""
    m = dev([turn(1, "So the tide turns at four this afternoon.", seq=10,
                  speaker="alice", split="speaker-change"),
             turn(3, "Right, then we launch the boat at three.", seq=11,
                  speaker="theo", opened_by="speaker-change")])
    assert who(m) == [(10, ["heard+room"], ""), (11, ["heard+room"], "")]
    assert [x["mail"]["headers"]["Ears-Speaker"] for x in m] == ["alice@host1", "theo@host1"]


def test_device_three_speakers_each_mail_their_own_speaker():
    m = dev([turn(1, "I reckon we wait for the wind.", seq=1, speaker="alice"),
             turn(4, "Hey Ivy, what does the forecast say?", seq=2, speaker="theo"),
             turn(7, "Wind drops after lunch.", seq=3, speaker="dave")])
    assert [x["mail"]["headers"]["Ears-Speaker"] for x in m] == ["alice@host1", "theo@host1", "dave@host1"]
    assert who(m)[1] == (2, ["heard+room"], "ivy@host1=call")      # the rule is unchanged


def test_device_a_turn_without_speaker_keeps_the_default_speaker(monkeypatch):
    """No `speaker` field: --speaker, else $EARS_SPEAKER, else unknown@<domain>
    (theo@<domain> until EARS-110). The overrides are unchanged."""
    monkeypatch.delenv("EARS_SPEAKER", raising=False)
    recs = [turn(1, "Hey Ivy.", seq=1)]
    assert dev(recs)[0]["mail"]["headers"]["Ears-Speaker"] == "unknown@host1"
    assert dev(recs, args=("--speaker", "theo@host1"))[0]["mail"]["headers"]["Ears-Speaker"] == "theo@host1"
    assert dev(recs, args=("--speaker", "alice@host1"))[0]["mail"]["headers"]["Ears-Speaker"] == "alice@host1"
    monkeypatch.setenv("EARS_SPEAKER", "someone@elsewhere")
    assert dev(recs)[0]["mail"]["headers"]["Ears-Speaker"] == "someone@elsewhere"


def test_device_a_label_beats_the_speaker_flag():
    m = dev([turn(1, "Hey Ivy.", seq=1, speaker="alice")], args=("--speaker", "theo@host1"))
    assert m[0]["mail"]["headers"]["Ears-Speaker"] == "alice@host1"


def test_speaker_of_maps_a_label_to_the_speaker_address_form():
    em = em_module()
    d = "theo@host1"
    assert em.speaker_of({"speaker": "alice"}, d, "host1") == "alice@host1"
    assert em.speaker_of({"speaker": "Alice"}, d, "host1") == "alice@host1"
    assert em.speaker_of({"speaker": " Mary Jane "}, d, "host1") == "mary-jane@host1"
    assert em.speaker_of({"speaker": "o'brien"}, d, "host1") == "o-brien@host1"
    # nothing usable: today's default, exactly
    for bad in ({}, {"speaker": None}, {"speaker": ""}, {"speaker": "  "}, {"speaker": 7},
                {"speaker": ["alice"]}, {"speaker": "other"}, {"speaker": "Mixed"},
                {"speaker": "silent"}, {"speaker": "unheard"}, {"speaker": "!!"}):
        assert em.speaker_of(bad, d, "host1") == d, bad


def test_keeper_neutral_labels_match_the_capture():
    """keeper/ is stdlib-only, so it keeps its own copy of the capture's NEUTRAL."""
    from ears.listen.speakers import NEUTRAL
    assert em_module().NEUTRAL_SPEAKERS == NEUTRAL


def test_seats_mail_is_unchanged_by_a_speaker_label():
    m = run([turn(1, "Hey Ivy, what's the latency?", seq=1, speaker="alice")])
    assert who(m) == [(1, ["ivy"], "ivy=call")]
    assert m[0]["mail"]["headers"]["Ears-Speaker"] == ""


# -- a seat never answers itself (EARS-126) -----------------------------------
# Sun 2030-06-09: the tail of Vera's own line, heard on the laptop
# speakers and labelled vera, was mailed vera=reply, and her reply rule woke her.

def _lit(seat, secs):
    rules = json.loads(json.dumps(RULES))
    rules["seats"][seat]["light"] = secs
    return rules


VERA_LINE = [saying(1, "vera", "The keeper is restarted and the queue is clear.", "u1"),
             said(5, "vera", "u1")]


def own_turn(sec, text, seq, speaker="vera", **kw):
    """A turn the capture labelled SPEAKER, on the built-in mic: words that are
    not the line's, so the echo trim leaves them alone."""
    return dict(turn(sec, text, span=1.5, seq=seq, **kw), device="MacBook Pro Microphone",
                speaker=speaker)


def test_device_a_seats_own_voice_is_not_its_reply_or_light():
    m = dev(VERA_LINE + [own_turn(9, "and that is where it stands for now", 9),
                         own_turn(20, "one more thing from me", 20)], rules=_lit("vera", 60))
    assert who(m) == [(9, ["heard+room"], ""), (20, ["heard+room"], "")]
    assert [x["mail"]["headers"]["Ears-Speaker"] for x in m] == ["vera@host1", "vera@host1"]


def test_seats_a_seats_own_voice_is_not_its_reply_or_light():
    m = run(VERA_LINE + [own_turn(9, "and that is where it stands for now", 9),
                         own_turn(20, "one more thing from me", 20)], rules=_lit("vera", 60))
    assert m == []


def test_a_dropped_self_reply_leaves_the_line_answerable():
    for go in (run, dev):
        m = go(VERA_LINE + [own_turn(9, "and that is where it stands for now", 9),
                            turn(12, "Good, thanks.", span=1, seq=12)])
        assert [(x["seq"], x["rule"]) for x in m if x["rule"]] == \
            [(12, "vera@host1=reply" if go is dev else "vera=reply")], go


def test_a_seats_own_voice_still_calls_it():
    m = run(VERA_LINE + [own_turn(9, "Hey Vera, the queue is clear.", 9)])
    assert who(m) == [(9, ["vera"], "vera=call")]
    m = dev([own_turn(2, "Hey Vera, are you there?", 2)])
    assert who(m) == [(2, ["heard+room"], "vera@host1=call")]


def test_a_label_names_the_seat_whatever_its_case():
    m = run(VERA_LINE + [own_turn(9, "and that is where it stands", 9, speaker=" Vera ")])
    assert m == []


def test_another_speakers_turn_still_replies_and_lights():
    # only the seat's own label is dropped: alice, or a label that names nobody, is not it
    for lab in ("alice", "other", None):
        m = run(VERA_LINE + [own_turn(9, "Sounds good.", 9, speaker=lab),
                             own_turn(20, "And another thing.", 20, speaker=lab)],
                rules=_lit("vera", 60))
        assert who(m) == [(9, ["vera"], "vera=reply"), (20, ["vera"], "vera=light")], lab


def test_one_seats_own_voice_still_lights_another_seat():
    rules = _lit("ivy", 60)
    m = run([saying(1, "ivy", "Question?", "u6"), said(3, "ivy", "u6")] + VERA_LINE[:1] +
            [said(6, "vera", "u1"), own_turn(9, "and that is where it stands", 9),
             own_turn(20, "more of the same", 20)], rules=rules)
    # Vera's line finished last; her own voice does not answer it, and Ivy's light
    # (60 s from 3 s) still holds for both turns
    assert who(m) == [(9, ["ivy"], "ivy=light"), (20, ["ivy"], "ivy=light")]


def test_speaker_label_is_the_local_part_or_none():
    em = em_module()
    assert em.speaker_label({"speaker": " Mary Jane "}) == "mary-jane"
    assert em.speaker_label({"speaker": "Vera"}) == "vera"
    for bad in ({}, {"speaker": None}, {"speaker": ""}, {"speaker": 7}, {"speaker": "other"},
                {"speaker": "!!"}):
        assert em.speaker_label(bad) is None, bad


# -- the send: sessionmail's `message send`, one per seat (EARS-381) -----------
# No --dry-run here: EARS_MAIL_MESSAGE points at tests/fixtures/fake_message.py,
# which logs what it was given (argv, the seat env, the body) and answers --json.

FAKE = Path(__file__).resolve().parent / "fixtures" / "fake_message.py"


def send_run(records, rules=RULES, to="seats", env=(), args=()):
    """RECORDS through ears-mail for real, against the fake: (fire-log rows, fake calls)."""
    with tempfile.TemporaryDirectory() as d:
        logs = Path(d, "logs", "conversations")
        logs.mkdir(parents=True)
        (logs / "heard_2030-06-09.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
        rf = Path(d, "wake.json")
        rf.write_text(json.dumps(rules))
        fire, calls = Path(d, "fire.jsonl"), Path(d, "calls.jsonl")
        cmd = [sys.executable, str(SCRIPT), "--rules", str(rf), "--once", "--from-start",
               "--domain", "host1", "--fire-log", str(fire)] + (["--to", to] if to else []) + list(args)
        e = dict(os.environ, VOICEMODE_BASE_DIR=d, EARS_FAKE_MESSAGE_LOG=str(calls),
                 EARS_MAIL_MESSAGE=f"{sys.executable} {FAKE}")
        e.update(dict(env))
        e = {k: v for k, v in e.items() if v is not None}
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=e)
        assert p.returncode == 0, p.stderr
        send_run.stderr = p.stderr
        read = lambda f: [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []
        return read(fire), read(calls)


def test_send_one_dead_seat_does_not_silence_the_rest():
    rows, calls = send_run([turn(1, "Hey everyone, please commit your work.", seq=7)],
                           env={"EARS_FAKE_MESSAGE_DEAD": "vera@host1"})
    assert [(r["to"], r["exit"]) for r in rows] == [(["ivy"], 0), (["vera"], 3), (["walter"], 0)]
    assert [c["argv"][1] for c in calls] == ["ivy@host1", "vera@host1", "walter@host1"]
    dead = rows[1]
    assert dead["id"] is None and dead["err"] == "address: vera@host1: no such box"
    assert all("err" not in r for r in (rows[0], rows[2]))
    # every copy tells the whole verdict
    assert {r["rule"] for r in rows} == {"ivy=everyone, vera=everyone, walter=everyone"}


def test_send_logs_the_id_the_send_returned_and_mints_none():
    rows, calls = send_run([turn(1, "Hey Ivy, check the build.", seq=1)])
    assert len(rows) == len(calls) == 1
    assert rows[0]["exit"] == 0 and rows[0]["id"] == calls[0]["id"]
    assert "--message-id" not in calls[0]["argv"]
    assert calls[0]["body"] == "Hey Ivy, check the build.\n"


def test_send_never_signs_as_the_seat_that_started_it():
    """A keeper started from a seat's shell inherits its session and must-sign: the
    turn still goes as heard+room, named Ears, unsigned (RULINGS A1)."""
    caller = {"CLAUDE_SESSION_ID": "caller-sid", "CLAUDE_SESSION_AGENT_SOCK": "/caller/sock",
              "CLAUDE_SESSION_PUB": "/caller/pub", "CLAUDE_CODE_SESSION_ID": "caller-sid",
              "SESSIONMAIL_REQUIRE_SIGNATURE": "1", "SESSION_MAIL_REQUIRE_SIGNATURE": "1",
              "TM_AGENT_ADDRESS": "vera", "EMAIL": "vera@host1"}
    rows, calls = send_run([turn(1, "Hey Ivy, check the build.", seq=1)], to=None, env=caller)
    assert rows[0]["exit"] == 0 and calls[0]["argv"][1] == "heard+room@host1"
    assert calls[0]["env"] == {"TM_AGENT_ADDRESS": "heard+room", "EMAIL": "heard+room",
                               "SESSIONMAIL_FROM_NAME": "Ears"}


def test_child_env_signs_only_with_the_keepers_own_key():
    em = em_module()
    caller = {"CLAUDE_SESSION_AGENT_SOCK": "/caller/sock", "CLAUDE_SESSION_PUB": "/caller/pub",
              "PATH": "/bin"}
    env = em.child_env("heard+room", caller)
    assert not [k for k in env if k.startswith("CLAUDE_SESSION_")] and env["PATH"] == "/bin"
    assert "SESSIONMAIL_REQUIRE_SIGNATURE" not in env
    # one of the pair is not a key: still unsigned
    assert "CLAUDE_SESSION_PUB" not in em.child_env("heard", dict(caller, EARS_MAIL_SIGNER_SOCK="/k"))
    env = em.child_env("heard", dict(caller, EARS_MAIL_SIGNER_SOCK="/k/sock",
                                     EARS_MAIL_SIGNER_PUB="/k/pub"))
    assert env["CLAUDE_SESSION_AGENT_SOCK"] == "/k/sock" and env["CLAUDE_SESSION_PUB"] == "/k/pub"
    assert env["SESSIONMAIL_REQUIRE_SIGNATURE"] == "1"


def test_send_with_no_message_binary_fails_each_turn_and_keeps_running():
    rows, calls = send_run([turn(1, "Hey Ivy, one.", seq=1), turn(5, "Hey Ivy, two.", seq=2)],
                           env={"EARS_MAIL_MESSAGE": "/nonexistent/message"})
    assert calls == [] and [r["seq"] for r in rows] == [1, 2]
    assert all(r["exit"] == 127 and r["id"] is None and r["err"].startswith("no message binary")
               for r in rows)
    assert "no message binary" in send_run.stderr       # said once at start, too


def test_send_resolves_message_on_path_to_an_absolute_path(tmp_path):
    em = em_module()
    fake = tmp_path / "message"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    assert em.resolve_message({"PATH": str(tmp_path)}) == ([str(fake)], "")
    cmd, why = em.resolve_message({"PATH": str(tmp_path / "none")})
    assert cmd is None and why.startswith("no message binary (message is not")
    cmd, _ = em.resolve_message({"PATH": str(tmp_path), "EARS_MAIL_MESSAGE": "message --x"})
    assert cmd == [str(fake), "--x"]
    cmd, why = em.resolve_message({"PATH": str(tmp_path), "EARS_MAIL_MESSAGE": "'message"})
    assert cmd is None and why.startswith("no message binary ($EARS_MAIL_MESSAGE: ")


def _uv_message(home):
    """sessionmail's `message` as uv installs it under HOME (EARS-388): the program in
    uv's tool dir, running the fake, linked into ~/.local/bin."""
    prog = home / ".local/share/uv/tools/sessionmail/bin/message"
    prog.parent.mkdir(parents=True)
    prog.write_text(f"#!/bin/sh\nexec '{sys.executable}' '{FAKE}' \"$@\"\n")
    prog.chmod(0o755)
    (home / ".local/bin").mkdir(parents=True)
    (home / ".local/bin/message").symlink_to(prog)
    return home / ".local/bin/message"


LAUNCHD_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


def test_send_finds_message_where_uv_installs_it_under_a_launchd_path(tmp_path):
    """EARS-388: launchd ran ears-mail with PATH /usr/bin:/bin:/usr/sbin:/sbin and
    `message` only in ~/.local/bin (uv's link) and uv's tool dir. A lookup on PATH
    alone failed every send with 127, and every heard turn was dropped. HOME is the
    one ENVIRON names, a tmp dir: never this machine's."""
    em = em_module()
    home = tmp_path / "home"
    link = _uv_message(home)
    env = {"PATH": LAUNCHD_PATH, "HOME": str(home)}
    assert em.resolve_message(env) == ([str(link)], "")
    (home / ".local/bin/message").unlink()               # no link: uv's tool dir answers
    assert em.resolve_message(env) == (
        [str(home / ".local/share/uv/tools/sessionmail/bin/message")], "")


def test_send_mails_a_turn_under_a_launchd_path(tmp_path):
    """The same, end to end: ears-mail run as launchd runs it mails the turn."""
    home = tmp_path / "home"
    link = _uv_message(home)
    rows, calls = send_run([turn(1, "Hey Ivy, check the build.", seq=1)],
                           env={"EARS_MAIL_MESSAGE": None, "PATH": LAUNCHD_PATH,
                                "HOME": str(home)})
    assert [r["exit"] for r in rows] == [0] and len(calls) == 1, rows
    assert f"sends by {link}" in send_run.stderr


def test_send_a_stalled_message_times_out_and_is_reported(monkeypatch):
    em = em_module()
    monkeypatch.setattr(em, "SEND_TIMEOUT_S", 0.3)
    monkeypatch.setattr(em, "_MESSAGE", ([sys.executable, "-c", "import time; time.sleep(5)"], ""))
    m = em.mail_for(turn(1, "Hey Ivy.", seq=1), ["ivy"], {"ivy": "call"})
    assert em.send(m, "ears", "host1") == (124, None, "message send timed out after 0.3s")


def test_send_a_non_ascii_turn_is_sent_with_an_ascii_subject():
    """rca-001 N1: STT text carries curly quotes and accents; `message send` refuses a
    subject that is not ASCII. The body keeps the words as heard."""
    text = "Hey Ivy, it’s the café’s “build”… okay"
    rows, calls = send_run([turn(1, text, seq=1)])
    assert rows[0]["exit"] == 0
    subj = calls[0]["argv"][calls[0]["argv"].index("-s") + 1]
    assert subj == "Hey Ivy, it's the cafe's \"build\"... okay"
    assert calls[0]["body"] == text + "\n"


def test_clean_subject_is_ascii_safe_and_short():
    em = em_module()
    assert em.clean_subject("a `b` $(c) é—中") == "a 'b' $ (c) e-?"
    long = em.clean_subject("word " * 30)
    assert len(long) <= 69 and long.endswith("...") and long.isascii()
    assert em.subject_of("") == "(the user spoke)"


def test_the_fence_never_reaches_the_real_message():
    """Under pytest $EARS_MAIL_MESSAGE is the refusing fixture (tests/env_fence.py):
    a run without --dry-run sends nothing, and each turn's row says exit 75."""
    from tests import env_fence
    assert "refuse_sendmail.py" in env_fence.FENCE["EARS_MAIL_MESSAGE"]
    assert os.environ["EARS_MAIL_MESSAGE"] == env_fence.FENCE["EARS_MAIL_MESSAGE"]
    rows, calls = send_run([turn(1, "Hey Ivy.", seq=1)],
                           env={"EARS_MAIL_MESSAGE": os.environ["EARS_MAIL_MESSAGE"]})
    assert calls == [] and rows[0]["exit"] == 75 and rows[0]["id"] is None


def test_send_finds_a_message_installed_after_start(monkeypatch, tmp_path):
    """A keeper started before sessionmail's `message` was installed fails each turn
    while it is missing, then sends once it is there, with no restart (launchd keeps
    the keeper running, so a missing binary cached for good would lose every turn)."""
    em = em_module()
    calls = tmp_path / "calls.jsonl"
    monkeypatch.setenv("EARS_FAKE_MESSAGE_LOG", str(calls))
    monkeypatch.setenv("EARS_MAIL_MESSAGE", "/nonexistent/message")
    m = em.mail_for(turn(1, "Hey Ivy.", seq=1), ["ivy"], {"ivy": "call"})
    code, mid, err = em.send(m, "ears", "host1")
    assert code == 127 and mid is None and err.startswith("no message binary")
    monkeypatch.setenv("EARS_MAIL_MESSAGE", f"{sys.executable} {FAKE}")   # now installed
    code, mid, err = em.send(m, "ears", "host1")
    assert (code, err) == (0, "") and mid == json.loads(calls.read_text())["id"]


# -- a failed send is loud in the service log, not only in the fire row --------
# launchd keeps stderr as the keeper's log; the fire row (stdout) alone was all
# there was while every turn failed. One ERROR line per failed send, none for a sent one.

def _errors(stderr):
    return [l for l in stderr.splitlines() if l.startswith("ears-mail: ERROR:")]


def test_a_failed_send_writes_one_error_line_per_send():
    rows, _ = send_run([turn(1, "Hey everyone, please commit your work.", seq=7)],
                       env={"EARS_FAKE_MESSAGE_DEAD": "vera@host1"})
    assert [r["exit"] for r in rows] == [0, 3, 0]
    assert _errors(send_run.stderr) == [
        "ears-mail: ERROR: send to vera failed, exit 3: address: vera@host1: no such box"]


def test_a_missing_message_writes_one_error_line_per_turn():
    rows, _ = send_run([turn(1, "Hey Ivy, one.", seq=1), turn(5, "Hey Ivy, two.", seq=2)],
                       env={"EARS_MAIL_MESSAGE": "/nonexistent/message"})
    errs = _errors(send_run.stderr)
    assert len(errs) == len(rows) == 2
    assert all(e.startswith("ears-mail: ERROR: send to ivy failed, exit 127: no message binary")
               for e in errs)
    for e in errs:                       # B2: its own words carry no tracker id, clock or he
        assert "EARS-" not in e and " he " not in f" {e} "
        assert not re.search(r"\b\d{1,2}:\d{2}\b", e)


def test_a_sent_turn_writes_no_error_line_and_the_row_keeps_its_shape():
    rows, calls = send_run([turn(1, "Hey Ivy, check the build.", seq=1)])
    assert _errors(send_run.stderr) == [] and len(calls) == 1
    assert set(rows[0]) == {"at", "seq", "to", "rule", "subject", "exit", "id"}


# -- a Delta call's turns: To its call box (EARS-431) --------------------------
# A FINAL turn with source call and device call:N goes To calls+<bot>-<N>@<host>,
# never to heard+; the bot is what the call's own leg (call-ears.sock) answers to a
# probe. Each fake leg here is a thread on a unix socket that answers
# {"agent", "profile", "calls"}; its reply can change mid-test (a hang-up).

import socket as _socket
import threading


class Leg:
    """A fake call-ears.sock. REPLY is what a probe gets (None: never answers; a
    callable: REPLY(k) for the k-th probe)."""

    def __init__(self, path, reply):
        self.path, self.reply, self.probes = str(path), reply, 0
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.srv = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        self.srv.bind(self.path)
        self.srv.listen(8)
        if reply is not None:
            threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            try:
                c.makefile("rb").readline()
                self.probes += 1
                r = self.reply(self.probes) if callable(self.reply) else self.reply
                c.sendall((json.dumps(r) + "\n").encode())
            except OSError:
                pass
            finally:
                c.close()

    def close(self):
        self.srv.close()
        if os.path.exists(self.path):
            os.unlink(self.path)


@pytest.fixture
def legs():
    """Make fake legs under one short temp dir (a unix socket path has ~104 bytes)."""
    d = tempfile.mkdtemp(prefix="em-")
    made = []

    def make(rel, reply):
        made.append(Leg(Path(d, rel), reply))
        return made[-1]
    make.dir = d
    yield make
    for x in made:
        x.close()


def call_rows(n=1317, seq=215130, text="Okay, please confirm the build.", **kw):
    """A call's capture: a heartbeat, a partial, then its final turn."""
    dev_ = f"call:{n}"
    return [dict(ts=ts(0.5), kind="event", event="heartbeat", source="call", device=dev_, stream_s=1.0),
            dict(ts=ts(1.0), kind="partial", source="call", device=dev_, text=text[:8]),
            dict(turn(2.0, text, seq=seq), source="call", device=dev_, **kw)]


def socks_env(monkeypatch, *legs_):
    monkeypatch.setenv("EARS_CALL_SOCKS", ":".join(x.path for x in legs_))


def skips(kind=None):
    out = [json.loads(x) for x in run.stderr.splitlines() if x.startswith('{"skip"')]
    return [s for s in out if kind is None or s["skip"] == kind]


def render(mail, domain="host1", mid="<turn@test>"):
    """The mail as `message send` would deliver it: build()'s To, subject and headers,
    From the seat child_env names (Ears <LOCAL@DOMAIN>), the body as sent."""
    em = em_module()
    args = em.build(mail, "heard", domain)
    lines = [f"From: {em.FROM_NAME} <{mail['from']}@{domain}>", f"To: {args[1]}",
             f"Subject: {args[args.index('-s') + 1]}", f"Message-ID: {mid}"]
    lines += [args[i + 1] for i, a in enumerate(args) if a == "--header"]
    return "\n".join(lines) + "\n\n" + mail["body"]


def test_call_turn_goes_to_its_call_box_and_the_mouth_files_it_heard(legs, monkeypatch, tmp_path, capsys):
    """Test plan 1, the acceptance test: ONE mail To calls+ivy-1317, From ears, exactly
    the four headers, the row as its JSON body; read by mouth.box it is _heard, filed
    unspoken. It goes through deliver() (no Ears-Rule: REVIEW GAP 3)."""
    leg = legs("ivy/call-ears.sock", {"agent": "ivy", "profile": "", "calls": [1317]})
    socks_env(monkeypatch, leg)
    rows = call_rows()
    m = dev(rows)
    assert len(m) == 1
    assert who(m) == [(215130, ["calls+ivy-1317"], None)]
    mail = m[0]["mail"]
    assert mail["from"] == "ears"
    assert mail["headers"] == {"Ears-Kind": "turn", "Ears-Seq": "215130",
                               "Call-Id": "1317", "Call-Bot": "ivy"}
    assert json.loads(mail["body"]) == rows[-1] and mail["body"].count("\n") == 1
    assert mail["subject"] == "Okay, please confirm the build."
    assert skips() == []
    args = em_module().build(mail, "heard", "host1")
    assert args[1] == "calls+ivy-1317@host1"
    assert headers_of(args) == mail["headers"]          # exactly four, none dropped

    from mouth import box
    from tests import env_fence
    from tests.mouth.maildir_put import put
    d = tmp_path / "mouth"
    monkeypatch.setenv("MOUTH_DIR", str(d))
    monkeypatch.setenv("MOUTH_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MOUTH_DEVICE", "null")
    monkeypatch.setenv("MOUTH_BACKEND", "silence")
    for k in ("MOUTH_MAILBOX", "MOUTH_MAIL_LOG", "MOUTH_VOICE"):
        env_fence.delenv(monkeypatch, k)
    bx = box.ensure(box.box_dir(d))
    put(bx, "turn", render(mail))
    x = box.read(bx / "new" / "turn", d)
    assert x["_heard"] is True and x["device"] == "call:1317"
    assert box.Queue(d).lines(act=True) == []
    out = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.startswith("{")]
    assert {"skipped": "heard", "mail_id": "<turn@test>"} in out
    assert not (bx / "new" / "turn").exists()


def test_call_the_leg_is_probed_once_while_the_call_is_live(legs, monkeypatch):
    leg = legs("ivy/call-ears.sock", {"agent": "ivy", "calls": [1317]})
    socks_env(monkeypatch, leg)
    dev(call_rows() + [dict(r, seq=r.get("seq") and r["seq"] + 1) for r in call_rows()[1:]])
    assert leg.probes == 1           # the first row naming call:1317; the turns find it known


def test_call_subject_carries_the_speaker_and_is_cut(legs, monkeypatch):
    leg = legs("ivy/call-ears.sock", {"agent": "ivy", "calls": [1317]})
    socks_env(monkeypatch, leg)
    long = "Right, so here is the plan for the evening and it is a long one indeed."
    m = dev(call_rows(text=long, speaker="theo"))
    assert m[0]["mail"]["subject"] == ("theo: " + long)[:56].strip()
    assert len(m[0]["mail"]["subject"]) <= 56
    m = dev(call_rows(text="Say `rm` and $(this) too"))
    assert m[0]["mail"]["subject"] == "Say 'rm' and $ (this) too"


def test_call_a_profile_makes_the_bot_agent_dash_profile(legs, monkeypatch):
    leg = legs("ivy/public/call-ears.sock", {"agent": " Ivy ", "profile": "public", "calls": [17]})
    socks_env(monkeypatch, leg)
    m = dev(call_rows(n=17))
    assert who(m) == [(215130, ["calls+ivy-public-17"], None)]
    assert m[0]["mail"]["headers"]["Call-Bot"] == "ivy-public"
    from mouth import box
    assert box.call_box(m[0]["mail"]["to"][0]) == ("ivy-public", 17)


@pytest.mark.parametrize("reply", [
    {"agent": "", "profile": "x", "calls": [1317]},          # no agent: names no bot
    {"profile": "public", "calls": [1317]},
    {"agent": "ivy", "calls": [99]},                          # the leg does not list N
])
def test_call_with_no_bot_is_skipped_and_never_mailed(legs, monkeypatch, reply):
    leg = legs("ivy/call-ears.sock", reply)
    socks_env(monkeypatch, leg)
    assert dev(call_rows()) == []
    assert [(s["seq"], s["call"]) for s in skips("call-bot")] == [(215130, 1317)]
    assert skips("call") == []                           # not the old skip


def test_call_no_leg_at_all_is_skipped(monkeypatch, tmp_path):
    monkeypatch.setenv("EARS_CALL_SOCKS", str(tmp_path / "none.sock"))
    assert dev(call_rows()) == []
    assert [s["skip"] for s in skips()] == ["call-bot"]


def test_call_a_web_leg_names_no_bot(legs, monkeypatch):
    """sessionphone web's leg (<root>/web/call-ears.sock) is skipped: its server mails
    its own calls' turns, and its agent would collide with the Delta bot's box."""
    leg = legs("sp/web/call-ears.sock", {"agent": "ivy", "calls": [1317]})
    socks_env(monkeypatch, leg)
    monkeypatch.setenv("SESSIONPHONE_ROOT", os.path.join(legs.dir, "sp"))
    assert dev(call_rows()) == []
    assert [s["skip"] for s in skips()] == ["call-bot"]
    assert leg.probes == 0


def test_call_the_first_leg_in_sorted_order_wins(legs, monkeypatch):
    """Two legs list one call: the first the search finds (sorted), as CallSource opens."""
    home = Path(legs.dir)
    legs(".bob/delta-chat/call-ears.sock", {"agent": "bob", "calls": [1317]})
    legs(".alice/delta-chat/call-ears.sock", {"agent": "alice", "calls": [1317]})
    for k in ("EARS_CALL_SOCKS", "VOICEMODE_CALL_EARS_SOCKS", "SESSIONPHONE_ROOT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", str(home))
    m = dev(call_rows())
    assert who(m) == [(215130, ["calls+alice-1317"], None)]


def test_call_learned_while_live_is_mailed_after_the_hang_up(legs, monkeypatch, tmp_path):
    """Test plan 3: learned on the partial; the leg then lists nothing (hung up) and the
    turn still goes to the learned box. A second process restores the map from --state."""
    leg = legs("ivy/call-ears.sock", {"agent": "ivy", "calls": [1317]})
    socks_env(monkeypatch, leg)
    rows = call_rows()
    leg.reply = {"agent": "ivy", "calls": [1317]}
    em = em_module()
    bots = em.CallBots(socks=lambda: [leg.path])
    bots.saw(1317)
    leg.reply = {"agent": "ivy", "calls": []}            # hung up
    assert bots.bot(1317) == "ivy" and bots.state() == {"1317": "ivy"}

    state = tmp_path / "state.json"
    leg.reply = {"agent": "ivy", "calls": [1317]}
    m = dev(rows[:2], args=("--state", str(state)))      # the live part: learned
    assert m == [] and json.loads(state.read_text())["call_bots"] == {"1317": "ivy"}
    leg.reply = {"agent": "ivy", "calls": []}
    with tempfile.TemporaryDirectory() as d:             # a restart, after the hang-up
        logs = Path(d, "logs", "conversations")
        logs.mkdir(parents=True)
        log = logs / "heard_2030-06-09.jsonl"
        log.write_text("".join(json.dumps(r) + "\n" for r in rows[:2]))
        st = json.loads(state.read_text())
        st["offsets"] = {str(log): log.stat().st_size}
        state.write_text(json.dumps(st))
        with log.open("a") as f:
            f.write(json.dumps(rows[2]) + "\n")
        p = subprocess.run([sys.executable, str(SCRIPT), "--rules", "/nonexistent", "--dry-run",
                            "--once", "--domain", "host1", "--state", str(state)],
                           capture_output=True, text=True, timeout=30,
                           env=dict(os.environ, VOICEMODE_BASE_DIR=d))
        assert p.returncode == 0, p.stderr
        m = [json.loads(l) for l in p.stdout.splitlines() if l.strip()]
    assert who(m) == [(215130, ["calls+ivy-1317"], None)]


def test_call_bots_keeps_the_newest(monkeypatch):
    em = em_module()
    saved = {str(n): f"b{n}" for n in range(em.CALL_BOTS_KEPT + 10)}
    bots = em.CallBots(saved, socks=lambda: [])
    assert len(bots.bots) == em.CALL_BOTS_KEPT and 9 not in bots.bots and 10 in bots.bots
    replies = {"/a": {"agent": "new", "calls": [5000]}}
    bots = em.CallBots(saved, socks=lambda: ["/a"], probe=replies.get)
    bots.saw(5000)
    assert list(bots.bots)[-1] == 5000 and len(bots.bots) == em.CALL_BOTS_KEPT
    assert em.CallBots({"x": "a", "3": "", "4": None, "5": "ok"}, socks=lambda: []).bots == {5: "ok"}


def test_call_a_leg_that_never_answers_does_not_stall_the_mail(legs, monkeypatch):
    """REVIEW GAP 2: the probe has a short timeout. A dead leg (accepts, never answers)
    costs about a second; the live leg after it still names the bot, and the room's
    device mail still goes."""
    import time as _time
    dead = legs("a/call-ears.sock", None)
    live = legs("b/call-ears.sock", {"agent": "ivy", "calls": [1317]})
    socks_env(monkeypatch, dead, live)
    em = em_module()
    t = _time.monotonic()
    assert em.probe(dead.path) == {}
    assert _time.monotonic() - t < 3 * em.PROBE_TIMEOUT_S
    t = _time.monotonic()
    m = dev(call_rows() + [turn(9, "Hey Ivy, in the room", seq=9)])
    assert _time.monotonic() - t < 15
    assert who(m) == [(215130, ["calls+ivy-1317"], None), (9, ["heard+room"], "ivy@host1=call")]


def test_call_turns_never_go_to_heard_and_mic_turns_are_unchanged(legs, monkeypatch):
    """Test plan 4: a mic turn mails heard+room with no call headers; --calls off
    restores skip: call; a call:N turn never goes to heard+, To or Cc."""
    leg = legs("ivy/call-ears.sock", {"agent": "ivy", "calls": [1317]})
    socks_env(monkeypatch, leg)
    rows = call_rows() + [turn(9, "Hey Ivy, in the room", seq=9)]
    m = dev(rows)
    assert who(m) == [(215130, ["calls+ivy-1317"], None), (9, ["heard+room"], "ivy@host1=call")]
    room = m[1]["mail"]["headers"]
    assert not {"Call-Id", "Call-Bot"} & set(room) and room["Ears-Kind"] == "spoken"
    m = dev(rows, args=("--calls", "off"))
    assert who(m) == [(9, ["heard+room"], "ivy@host1=call")]
    assert [(s["skip"], s["seq"]) for s in skips()] == [("call", 215130)]
    assert leg.probes == 1                                 # off probes nothing
    assert not [x for x in dev(rows) if any(t.startswith("heard+") for t in x["to"])
                and x["seq"] == 215130]
    call = [x for x in dev(rows) if x["seq"] == 215130]
    assert len(call) == 1 and "cc" not in call[0] and "cc" not in call[0]["mail"]   # no copy


def test_call_from_is_configuration(legs, monkeypatch):
    leg = legs("ivy/call-ears.sock", {"agent": "ivy", "calls": [1317]})
    socks_env(monkeypatch, leg)
    m = dev(call_rows(), args=("--calls-from", "calls-keeper"))
    assert m[0]["mail"]["from"] == "calls-keeper"
    assert em_module().child_env("calls-keeper", {})["EMAIL"] == "calls-keeper"


def test_call_echo_of_the_mouth_is_not_mailed(legs, monkeypatch):
    leg = legs("ivy/call-ears.sock", {"agent": "ivy", "calls": [1317]})
    socks_env(monkeypatch, leg)
    line = "The build is green and the branch is pushed."
    rows = call_rows(text=line)
    m = dev([saying(1.5, "ivy", line, "u1")] + rows)
    assert m == []


def test_call_seats_mail_is_unchanged(legs, monkeypatch):
    """--to seats is the pre-genie mail: a call turn is mailed as before, and no leg is
    probed; --calls changes nothing there."""
    leg = legs("ivy/call-ears.sock", {"agent": "ivy", "calls": [1317]})
    socks_env(monkeypatch, leg)
    rows = call_rows(text="Hey Ivy, on the call") + [turn(9, "Hey Vera, in the room", seq=9)]
    on, off = run(rows), run(rows, args=("--calls", "off"))
    strip = lambda ms: [{k: v for k, v in x.items() if k != "at"} for x in ms]
    assert strip(on) == strip(off)
    assert who(on) == [(215130, ["ivy"], "ivy=call"), (9, ["vera"], "vera=call")]
    assert leg.probes == 0


def test_call_the_send_carries_the_four_headers_and_from_ears(legs, monkeypatch):
    """For real against the fake `message`: From is the seat ears, display name Ears."""
    leg = legs("ivy/call-ears.sock", {"agent": "ivy", "calls": [1317]})
    socks_env(monkeypatch, leg)
    rows, calls = send_run(call_rows(), to=None)
    assert [(r["to"], r["exit"], r["rule"]) for r in rows] == [(["calls+ivy-1317"], 0, None)]
    c = calls[0]
    assert c["argv"][1] == "calls+ivy-1317@host1"
    assert "--cc" not in c["argv"] and "cc" not in rows[0]   # a call turn has no device copy
    assert headers_of(c["argv"]) == {"Ears-Kind": "turn", "Ears-Seq": "215130",
                                     "Call-Id": "1317", "Call-Bot": "ivy"}
    assert c["env"]["EMAIL"] == c["env"]["TM_AGENT_ADDRESS"] == "ears"
    assert c["env"]["SESSIONMAIL_FROM_NAME"] == "Ears"
    assert json.loads(c["body"])["seq"] == 215130


@pytest.mark.parametrize("calls", [5, "1317", {"1317": 1}, None, [True, "1317", 1317.0]])
def test_call_a_bad_probe_reply_names_no_call_and_stops_no_mail(legs, monkeypatch, calls):
    """A leg whose reply's calls is not a list of ints names no call: the turn is
    skipped as call-bot, the keeper does not crash, and the room's device mail still
    goes (a str is not read digit by digit, a bool is not a call id)."""
    leg = legs("ivy/call-ears.sock", {"agent": "ivy", "calls": calls})
    socks_env(monkeypatch, leg)
    m = dev(call_rows() + [turn(9, "Hey Ivy, in the room", seq=9)])
    assert who(m) == [(9, ["heard+room"], "ivy@host1=call")]
    assert [(s["skip"], s["call"]) for s in skips("call-bot")] == [("call-bot", 1317)]


def test_call_a_leg_that_trickles_and_never_ends_its_line_is_cut_off(legs):
    """The probe's timeout is for the whole reply, not each read."""
    import time as _time
    leg = legs("t/call-ears.sock", None)
    stop = threading.Event()

    def trickle():
        c, _ = leg.srv.accept()
        while not stop.is_set():
            try:
                c.sendall(b" ")
            except OSError:
                break
            _time.sleep(0.05)
        c.close()
    threading.Thread(target=trickle, daemon=True).start()
    em = em_module()
    got = []
    t = _time.monotonic()
    p = threading.Thread(target=lambda: got.append(em.probe(leg.path, timeout=0.5)), daemon=True)
    p.start()
    p.join(5)                       # a probe that never returns fails here, never hangs
    stop.set()
    assert not p.is_alive() and got == [{}]
    assert _time.monotonic() - t < 2


def raw_leg(legs, rel, data, hold=0.0):
    """A leg that answers its first probe with the raw bytes DATA, then holds the
    connection open HOLD seconds (so only the probe's own cap or deadline ends it)."""
    import time as _time
    leg = legs(rel, None)

    def answer():
        c, _ = leg.srv.accept()
        try:
            c.sendall(data)
            _time.sleep(hold)
        except OSError:
            pass
        c.close()
    threading.Thread(target=answer, daemon=True).start()
    return leg


def test_call_a_reply_nested_thousands_deep_is_no_reply(legs):
    """A reply under the 64 KiB cap that nests too deep for json raises
    RecursionError, not ValueError: the probe gives {} and never crashes the keeper."""
    leg = raw_leg(legs, "d/call-ears.sock", b"[" * 60000 + b"\n")
    assert em_module().probe(leg.path, timeout=2) == {}


def test_call_a_reply_over_the_cap_is_given_up_before_its_deadline(legs):
    """A leg that pours bytes and never ends its line is cut off at PROBE_MAX_BYTES,
    well inside a long timeout, not read on until the deadline."""
    import time as _time
    em = em_module()
    leg = raw_leg(legs, "c/call-ears.sock", b" " * (4 * em.PROBE_MAX_BYTES), hold=10)
    got, t = [], _time.monotonic()
    p = threading.Thread(target=lambda: got.append(em.probe(leg.path, timeout=8)), daemon=True)
    p.start()
    p.join(10)
    assert not p.is_alive() and got == [{}]
    assert _time.monotonic() - t < 4


@pytest.mark.parametrize("bad", [[1, 2], "x", 7])
def test_call_a_bad_call_bots_in_state_is_ignored(bad, tmp_path):
    """A --state whose call_bots is not a map starts empty, never crashes."""
    em = em_module()
    assert em.CallBots(bad, socks=lambda: []).bots == {}
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"offsets": {}, "call_bots": bad}))
    m = dev([turn(1, "Hey Ivy, hello", seq=1)], args=("--state", str(state)))
    assert who(m) == [(1, ["heard+room"], "ivy@host1=call")]


def started(n=1317, at=0.1):
    """A capture's first row on call:N (every heard row carries source and device)."""
    return dict(ts=ts(at), kind="event", event="listen-started", source="call", device=f"call:{n}")


def test_call_a_reused_call_id_goes_to_its_new_bot_not_the_kept_one():
    """A call id is the bot's message id, so a second bot's later call can reuse one
    that is kept after its hang-up. Its capture's listen-started forgets the kept bot
    and probes afresh; if that probe misses, the turn probes again."""
    em = em_module()
    replies = {"/a": {"agent": "alice", "calls": [1317]}, "/b": {"agent": "bob", "calls": []}}
    bots = em.CallBots(socks=lambda: ["/a", "/b"], probe=lambda p: replies[p])
    bots.saw(1317, True)
    assert bots.bot(1317) == "alice"
    replies["/a"]["calls"], replies["/b"]["calls"] = [], [1317]      # alice hung up; bob's 1317
    bots.saw(1317)                                                   # a later row: no probe
    assert bots.bot(1317) == "alice"                                 # the hang-up is bridged
    bots.saw(1317, True)                                             # bob's capture starts
    assert bots.bot(1317) == "bob" and bots.state() == {"1317": "bob"}
    replies["/b"]["calls"] = []                                      # its probe misses...
    bots.saw(1317, True)
    assert 1317 not in bots.bots
    replies["/b"]["calls"] = [1317]
    assert bots.bot(1317) == "bob"                                   # ...the turn probes again


def test_call_a_second_capture_of_one_call_id_mails_its_own_bot(legs, monkeypatch):
    """The whole script, ONE process: alice's call 1317, its hang-up, then bob's call
    1317. Each leg answers its first probe one way and every later one the other, so
    only the second capture's listen-started can learn bob."""
    a = legs("a/call-ears.sock", lambda k: {"agent": "alice", "calls": [1317] if k == 1 else []})
    b = legs("b/call-ears.sock", lambda k: {"agent": "bob", "calls": [] if k == 1 else [1317]})
    socks_env(monkeypatch, a, b)
    first = [started(at=0.1)] + call_rows(seq=1)
    second = [started(at=10.1)] + [dict(r, ts=ts(10.5 + i)) for i, r in
                                   enumerate(call_rows(seq=2, text="Bob here, on a new call."))]
    m = dev(first + second)
    assert who(m) == [(1, ["calls+alice-1317"], None), (2, ["calls+bob-1317"], None)]
    assert (a.probes, b.probes) == (2, 2)                # once per capture, no more
