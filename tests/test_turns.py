"""The silence stage: open partials count, commit is called, the seams work."""
from ears.events import Event, vad
from ears.turns import SilenceTurns

BLK = b"\0" * 640  # 20 ms at 16 kHz

def run(st, script, secs=6.0):
    out = []
    for i in range(int(secs / 0.02)):
        t = round((i + 1) * 0.02, 2)
        st.pcm(BLK, t)
        for e in script.get(t, []):
            out += st.event(e)
        out += st.tick(t)
    return out + st.flush(t)

def test_turn_keeps_the_open_partial_and_commits():
    calls = []
    st = SilenceTurns(commit=lambda: calls.append(1))
    out = run(st, {1.0: [vad(True, 0.98, "t")],
                   1.5: [Event("final", "one.", 1.0, 1.4, seg=1)],
                   2.0: [Event("partial", "two three", 1.5, 2.0, seg=2)],
                   2.3: [vad(False, 2.2, "t")]})
    turns = [e for e in out if e.type == "turn"]
    assert calls, "commit was not called"
    assert len(turns) == 1 and turns[0].text == "one. two three"
    assert turns[0].extra.get("partial_tail") is True

def test_seams_gate_and_redecode():
    seen = {}
    st = SilenceTurns(gate=lambda pcm, text: (0.5, 0.9), redecode=lambda pcm: "better")
    out = run(st, {1.0: [vad(True, 0.98, "t")],
                   2.0: [Event("final", "hello.", 1.0, 2.0, seg=1)],
                   2.3: [vad(False, 2.2, "t")]})
    t = [e for e in out if e.type == "turn"][0]
    assert t.text == "better" and t.extra["by"] == "redecode" and t.extra["p"] == 0.9
    assert t.extra["reason"] == "smart-turn" and abs(t.extra["wait_s"] - 0.5) < 0.03

def test_events_round_trip():
    e = Event("final", "hi", 1.0, 2.0, [("hi", 1.0, 2.0)], seg=3, extra={"x": 1})
    assert Event.from_json(e.to_json()).to_dict() == e.to_dict()

def test_verdict_survives_an_onset_while_commit_pending():
    # case 1: gate says (1.0, p); commit pending; he starts again; the verdict must stand
    st = SilenceTurns(gate=lambda pcm, text: (1.0, 0.8), commit=lambda: None)
    st.COMMIT_WAIT = 0.0
    out = run(st, {1.0: [vad(True, 0.98, "t")],
                   1.5: [Event("partial", "so then", 1.0, 1.5, seg=1)],
                   2.0: [vad(False, 1.9, "t")],
                   2.92: [vad(True, 2.91, "t")]})
    t = [e for e in out if e.type == "turn"][0]
    assert t.extra["p"] == 0.8 and t.extra["reason"] == "smart-turn"

def test_a_late_final_never_makes_a_second_turn():
    # case 2: the turn closed on the partial; seg 1's final arrives after
    st = SilenceTurns(commit=lambda: None)
    st.COMMIT_WAIT = 0.0
    out = run(st, {1.0: [vad(True, 0.98, "t")],
                   1.5: [Event("partial", "hello there", 1.0, 1.5, seg=1)],
                   1.7: [vad(False, 1.6, "t")],
                   4.0: [Event("final", "hello there.", 1.0, 1.6, seg=1)]})
    turns = [e for e in out if e.type == "turn"]
    assert len(turns) == 1 and turns[0].extra.get("partial_tail")

def test_not_complete_fallback_is_silence():
    # case 3: Smart Turn said NOT complete; the 2 s fallback fired -> reason silence
    st = SilenceTurns(gate=lambda pcm, text: (2.0, 0.2))
    out = run(st, {1.0: [vad(True, 0.98, "t")],
                   1.5: [Event("final", "and then", 1.0, 1.5, seg=1)],
                   1.7: [vad(False, 1.6, "t")]})
    t = [e for e in out if e.type == "turn"][0]
    assert t.extra["reason"] == "silence" and t.extra["p"] == 0.2

def test_turn_audio_survives_the_gap_before_the_first_partial():
    # case 4: offset at 1.5, first partial at 2.4 -> the turn's audio must start before 1.0
    seen = {}
    st = SilenceTurns(redecode=lambda pcm: seen.setdefault("n", len(pcm)) and None)
    run(st, {1.0: [vad(True, 1.0, "t")],
             1.5: [vad(False, 1.5, "t")],
             2.4: [Event("partial", "yes", 1.0, 1.5, seg=1)],
             2.6: [Event("final", "yes.", 1.0, 1.5, seg=1)]})
    assert seen["n"] >= int(1.9 * 16000) * 2      # from ~0.5 s pre-roll before 1.0, through the close
