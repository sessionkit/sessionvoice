"""EARS-246: the wake file's seat names reach STT as whisper's ``prompt``.

On whisper, a user's "Hey DAVE" came out "Hey Davis", "Hey, Dane" and "Hey, caves", so their
seat never woke. The capture can send a prompt built from the wake file's seat KEYS, as
a bare list with no opener ("Dave, Bob."; do-004 measured the "Hey X." shape echoing and
swallowing the last seat), never from the sound-alikes in ``names`` (priming with "caves"
would bias whisper toward "caves"). It rides the turn's FINAL re-decode only, never a
partial; a transcript that is only prompt words is dropped as an echo. It follows the wake
file live; EARS_STT_HINT / ears.json's ``stt_hint`` switch it, else stt.HINT_DEFAULT (one
setting). No engine rejects the field (findings/stt-prompt-engines.md), so it is sent to
every engine. Every request here goes to a fake transport; no server runs.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import httpx
import numpy as np
import pytest

from ears import envnames, wake
from ears.listen import __main__ as listen_main
from ears.listen import stt

REPO = Path(__file__).resolve().parent.parent
AUDIO = np.zeros(1600, dtype=np.int16)
SOUND_ALIKES = ("dav", "daev", "days", "davis", "dane", "caves")


def form(request) -> dict:
    """The text parts of REQUEST's multipart body: {name: value} (the file part is skipped)."""
    boundary = request.headers["content-type"].split("boundary=", 1)[1].encode()
    out = {}
    for part in request.read().split(b"--" + boundary):
        head, _, value = part.partition(b"\r\n\r\n")
        m = re.search(rb'name="([^"]+)"', head)
        if m and b"filename=" not in head:
            out[m.group(1).decode()] = value.rsplit(b"\r\n", 1)[0].decode()
    return out


def fake(client, sent):
    """Point CLIENT (a WhisperSTT) at a fake engine that records each request's form."""
    def handler(request):
        sent.append(form(request))
        return httpx.Response(200, json={"text": "hey dave"})
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    return client


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A wake file and an ears.json of the test's own; the switch unset."""
    monkeypatch.setenv("EARS_WAKE", str(tmp_path / "wake.json"))
    monkeypatch.setenv("EARS_CONF", str(tmp_path / "ears.json"))
    monkeypatch.delenv("EARS_STT_HINT", raising=False)
    return tmp_path


def seats_file(path, seats):
    path.write_text(json.dumps({"defaults": {"openers": "hey|hay"}, "seats": seats}))


def words_of(text):
    return {w.lower() for w in re.findall(r"[A-Za-z0-9]+", text)}


# -- 1. the hint is the seat KEYS, spoken; never the sound-alikes ----------------------

def test_hint_words_are_the_keys_never_the_sound_alikes(env):
    seats_file(env / "wake.json", {
        "dave": {"names": "dave|dav|daev|days|davis|caves"},
        "ivy": {"names": "ivy|ivey"},
        "sessionmail+engineer": {},
        "vera": None,
    })
    assert wake.hint_words() == ["Dave", "Ivy", "Sessionmail Engineer", "Vera"]
    prompt = wake.hint_prompt(wake.hint_words())
    assert prompt == "Dave, Ivy, Sessionmail Engineer, Vera."
    assert not words_of(prompt) & {"ivey", *SOUND_ALIKES}


def test_a_seats_own_hint_wins_and_is_used_as_written(env):
    seats_file(env / "wake.json", {"dave": {"names": "dave|dav", "hint": "  DAVE "},
                                   "ivy": {"hint": ""}, "kit": {"hint": 7}})
    assert wake.hint_words() == ["DAVE", "Ivy", "Kit"]     # empty or non-string: the key


def test_hint_words_de_dupe_case_blind(env):
    seats_file(env / "wake.json", {"dave": {}, "DAVE2": {"hint": "Dave"}, "ivy": {}})
    assert wake.hint_words() == ["Dave", "Ivy"]


def test_no_file_no_words_and_a_bad_file_says_so(env):
    assert wake.hint_words() == [] and wake.hint_prompt([]) == ""
    (env / "wake.json").write_text("{not json")
    with pytest.raises(wake.WakeError):
        wake.hint_words()


def test_the_prompt_drops_whole_trailing_seats_past_its_budget():
    words = [f"Seat{i:03d}" for i in range(100)]
    p = wake.hint_prompt(words, limit=60)
    assert len(p) <= 60 and p.startswith("Seat000, Seat001,") and p.endswith(".")
    assert len(wake.hint_prompt(words)) <= wake.HINT_MAX_CHARS


# -- 2. WakeHint follows the wake file with no restart --------------------------------

def test_wake_hint_follows_ears_wake_add_with_no_restart(env):
    h = wake.WakeHint()
    assert h() == ""                                           # no file yet: no hint
    wake.add("dave", "dav|days")
    assert h() == "Dave."
    wake.add("ivy")
    assert h() == "Dave, Ivy."                                  # the same object, re-read
    wake.add("dave", hint="DAVE")
    assert h() == "DAVE, Ivy."
    (env / "wake.json").unlink()
    assert h() == ""


def test_wake_hint_is_empty_on_a_bad_file_and_recovers(env):
    h = wake.WakeHint()
    (env / "wake.json").write_text("{half a write")
    assert h() == ""
    (env / "wake.json").write_text(json.dumps({"seats": []}))   # no seats object
    assert h() == ""
    seats_file(env / "wake.json", {"dave": {}})
    assert h() == "Dave."


def test_wake_hint_reads_the_file_once_per_change(env, monkeypatch):
    seats_file(env / "wake.json", {"dave": {}})
    h, reads = wake.WakeHint(), []
    real = wake.hint_words
    monkeypatch.setattr(wake, "hint_words", lambda p=None: reads.append(p) or real(p))
    for _ in range(5):
        assert h() == "Dave."
    assert len(reads) == 1                                      # a stat per call, one parse


# -- 3. WhisperSTT(prompt=...) sends the field only when there is one ----------------

def test_no_prompt_sends_no_prompt_field():
    sent = []
    fake(stt.WhisperSTT("http://127.0.0.1:2022/v1"), sent).transcribe(AUDIO)
    assert "prompt" not in sent[0] and sent[0]["response_format"] == "json"


@pytest.mark.parametrize("prompt, want", [
    ("Dave.", "Dave."),
    (lambda: "Ivy.", "Ivy."),
    ("", None), ("   ", None), (None, None), (lambda: "", None), (lambda: None, None),
])
def test_the_prompt_field_carries_a_non_empty_prompt_only(prompt, want):
    sent = []
    fake(stt.WhisperSTT("http://127.0.0.1:2022/v1", prompt=prompt), sent).transcribe(AUDIO)
    assert sent[0].get("prompt") == want


def test_a_callable_prompt_is_asked_per_request():
    sent, said = [], iter(["Dave.", "Dave, Ivy."])
    c = fake(stt.WhisperSTT("http://127.0.0.1:2022/v1", prompt=lambda: next(said)), sent)
    c.transcribe(AUDIO)
    c.transcribe(AUDIO)
    assert [s["prompt"] for s in sent] == ["Dave.", "Dave, Ivy."]


def test_a_failing_hint_never_costs_the_turn():
    def boom():
        raise RuntimeError("wake file gone mad")
    sent = []
    c = fake(stt.WhisperSTT("http://127.0.0.1:2022/v1", prompt=boom), sent)
    assert c.transcribe(AUDIO) == "hey dave" and "prompt" not in sent[0]


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:2022/v1",       # whisper.cpp: ACCEPT (uses it)
    "http://127.0.0.1:8890/v1",       # Parakeet on mlx-audio: IGNORE (200, dropped)
    "http://127.0.0.1:2023/v1",       # ears serve (Apple): IGNORE (200, never read)
])
def test_no_engine_is_gated_none_rejects_the_field(url):
    """do-001 (findings/stt-prompt-engines.md): no engine rejects ``prompt``, so there is no
    per-engine rule to key on; every engine gets it."""
    sent = []
    fake(stt.WhisperSTT(url, prompt="Dave."), sent).transcribe(AUDIO)
    assert sent[0]["prompt"] == "Dave."


# -- 4. sound-alikes never reach the request -----------------------------------------

def test_sound_alikes_never_reach_the_prompt_field(env, monkeypatch):
    monkeypatch.setenv("EARS_STT_HINT", "1")
    wake.add("dave", "|".join(SOUND_ALIKES))
    wake.add("ivy", "ivey|ive")
    sent = []
    fake(stt.WhisperSTT("http://127.0.0.1:2022/v1", prompt=stt.SttHint(log=lambda m: None)),
         sent).transcribe(AUDIO)
    assert sent[0]["prompt"] == "Dave, Ivy."
    assert not words_of(sent[0]["prompt"]) & {"ivey", "ive", *SOUND_ALIKES}


# -- 5. the switch: EARS_STT_HINT wins over ears.json stt_hint; else HINT_DEFAULT (off) -

@pytest.mark.parametrize("conf, envv, on", [
    (None, None, False),                      # no ears.json: the default, off
    ({}, None, False),                        # not set: the default
    ({"stt_hint": None}, None, False),        # null: the default
    ({}, "1", True),                          # the session layer turns it on
    ({"stt_hint": True}, None, True),
    ({"stt_hint": False}, None, False),
    ({"stt_hint": "off"}, None, False),
    ({"stt_hint": False}, "1", True),         # the session layer wins
    ({"stt_hint": True}, "0", False),
    ({}, "false", False), ({}, "off", False), ({}, "no", False),
    ({"stt_hint": False}, "", False),         # empty env: unset, ears.json decides
])
def test_the_off_switch(env, monkeypatch, conf, envv, on):
    seats_file(env / "wake.json", {"dave": {"names": "dave|dav"}})
    if conf is not None:
        (env / "ears.json").write_text(json.dumps(conf))
    if envv is not None:
        monkeypatch.setenv("EARS_STT_HINT", envv)
    sent = []
    fake(stt.WhisperSTT("http://127.0.0.1:2022/v1", prompt=stt.SttHint(log=lambda m: None)),
         sent).transcribe(AUDIO)
    assert sent[0].get("prompt") == ("Dave." if on else None)


def test_the_switch_is_live_and_a_mid_write_keeps_the_last_setting(env, monkeypatch):
    seats_file(env / "wake.json", {"dave": {}})
    log, sent = [], []
    c = fake(stt.WhisperSTT("http://127.0.0.1:2022/v1", prompt=stt.SttHint(log=log.append)), sent)
    conf = env / "ears.json"
    conf.write_text(json.dumps({"stt_hint": True}))
    c.transcribe(AUDIO)                                         # on
    conf.write_text(json.dumps({"stt_hint": False}))
    c.transcribe(AUDIO)                                         # off, no restart
    conf.write_text('{"stt_hint": tr')                          # caught mid-write
    c.transcribe(AUDIO)                                         # still off
    conf.write_text(json.dumps({"stt_hint": True}))
    c.transcribe(AUDIO)
    monkeypatch.setenv("EARS_STT_HINT", "0")
    c.transcribe(AUDIO)
    assert [s.get("prompt") for s in sent] == ["Dave.", None, None, "Dave.", None]
    # said once per change, never per chunk
    assert log == ["ears: stt hint 'Dave.' (wake file)",
                   "ears: stt hint off (EARS_STT_HINT, ears.json stt_hint, else the default)",
                   "ears: stt hint 'Dave.' (wake file)",
                   "ears: stt hint off (EARS_STT_HINT, ears.json stt_hint, else the default)"]


def test_on_with_no_seats_says_none_not_off(env, monkeypatch):
    monkeypatch.setenv("EARS_STT_HINT", "1")
    log = []
    assert stt.SttHint(log=log.append)() == ""
    assert log == ["ears: stt hint none: no seats in the wake file"]


# -- 6. make_stt wires the hint on both branches --------------------------------------

@pytest.mark.parametrize("argv, envs", [
    (["--stt-url", "http://127.0.0.1:2022/v1"], {}),                       # pinned by flag
    ([], {"EARS_LISTEN_STT_URL": "http://127.0.0.1:2022/v1"}),       # pinned by env
    ([], {}),                                                              # follows ears.json
])
def test_make_stt_sends_the_hint_on_both_branches_final_only(env, monkeypatch, argv, envs):
    for k in (*envnames.group("EARS_LISTEN_STT_URL"), *envnames.group("EARS_LISTEN_STT_MODEL")):
        monkeypatch.delenv(k, raising=False)
    for k, v in envs.items():
        monkeypatch.setenv(k, v)
    (env / "ears.json").write_text(json.dumps({"final": {"url": "http://127.0.0.1:2022/v1"},
                                               "stt_hint": True}))
    wake.add("dave", "dav|days")
    s = listen_main.make_stt(listen_main.build_parser().parse_args(["capture", *argv]))
    try:
        inner = s.current() if isinstance(s, stt.FollowingSTT) else s
        assert isinstance(inner.final_prompt, stt.SttHint) and inner.prompt is None
        sent = []
        fake(inner, sent)
        s.transcribe_final(AUDIO)
        s.transcribe(AUDIO)                                         # a partial: no prompt
        wake.add("ivy")                                             # live, through make_stt's client
        s.transcribe_final(AUDIO)
        assert [x.get("prompt") for x in sent] == ["Dave.", None, "Dave, Ivy."]
    finally:
        s.close()


def test_a_following_capture_keeps_the_hint_across_an_engine_change(env, monkeypatch):
    for k in (*envnames.group("EARS_LISTEN_STT_URL"), *envnames.group("EARS_LISTEN_STT_MODEL")):
        monkeypatch.delenv(k, raising=False)
    conf = env / "ears.json"
    conf.write_text(json.dumps({"final": {"url": "http://127.0.0.1:8890/v1"}}))
    s = listen_main.make_stt(listen_main.build_parser().parse_args(["capture"]))
    try:
        first = s.current()
        conf.write_text(json.dumps({"final": {"url": "http://127.0.0.1:2022/v1"}}))
        second = s.current()
        assert second is not first and second.final_prompt is first.final_prompt
    finally:
        s.close()


# -- 7. ears wake add --hint, and ears-mail reading a file that has one ---------------

def test_ears_wake_add_hint_sets_keeps_and_removes(env, capsys):
    f = env / "wake.json"
    assert wake.main(["add", "dave", "--names", "dav", "--hint", "DAVE"]) == 0
    assert "hint: DAVE" in capsys.readouterr().out
    assert json.loads(f.read_text())["seats"]["dave"] == {"names": "dave|dav", "hint": "DAVE"}
    wake.main(["add", "dave", "--names", "days"])                  # no --hint: kept
    assert json.loads(f.read_text())["seats"]["dave"]["hint"] == "DAVE"
    capsys.readouterr()
    assert wake.main(["list"]) == 0
    assert capsys.readouterr().out == "dave: dave|dav|days  hint: DAVE\n"
    wake.main(["add", "dave", "--hint", ""])                        # '' removes it
    assert "hint" not in json.loads(f.read_text())["seats"]["dave"]
    capsys.readouterr()
    wake.main(["list"])
    assert capsys.readouterr().out == "dave: dave|dav|days\n"


def test_ears_mail_still_wakes_with_a_hint_in_the_file(env):
    """The hint field is new to the rules file ears-mail reads: it must ride along unread."""
    wake.add("dave", "dav", hint="DAVE")
    logs = env / "logs" / "conversations"
    logs.mkdir(parents=True)
    rec = dict(ts="2030-06-09T01:00:05.000+10:00", kind="turn", source="mic", device="airpods",
               text="Hey Dav, what's on?", t0=100.0, t1=102.0, seq=1)
    (logs / "heard_2030-06-09.jsonl").write_text(json.dumps(rec) + "\n")
    p = subprocess.run([sys.executable, str(REPO / "keeper" / "ears-mail.py"), "--rules",
                        str(env / "wake.json"), "--dry-run", "--once", "--from-start",
                        "--domain", "host1", "--to", "device"],
                       capture_output=True, text=True, timeout=30,
                       env=dict(os.environ, VOICEMODE_BASE_DIR=str(env)))
    mails = [json.loads(l) for l in p.stdout.splitlines() if l.strip()]
    assert p.returncode == 0, p.stderr
    assert any("dave" in str(m.get("rule", "")) for m in mails), mails


# -- 8. the settings schema draws the switch --------------------------------------------

def test_the_schema_draws_stt_hint_with_the_one_default():
    """The default is ONE setting, stt.HINT_DEFAULT; the schema's default mirrors it, so a
    flip there that misses the schema fails here."""
    schema = json.loads((REPO / "settings" / "ears.schema.json").read_text())
    p = schema["properties"]["stt_hint"]
    assert p["type"] == "boolean" and p["default"] is stt.HINT_DEFAULT
    assert stt.hint_on_conf({}) is stt.HINT_DEFAULT and stt.hint_on_conf(None) is stt.HINT_DEFAULT


def test_the_default_ships_off_pending_the_owner():
    """do-005: nothing ships default-ON before the near-word question is answered."""
    assert stt.HINT_DEFAULT is False


def test_flipping_the_one_default_flips_every_unset_layer(env, monkeypatch):
    seats_file(env / "wake.json", {"dave": {}})
    for default, want in ((False, None), (True, "Dave.")):
        monkeypatch.setattr(stt, "HINT_DEFAULT", default)
        sent = []
        fake(stt.WhisperSTT("http://127.0.0.1:2022/v1", prompt=stt.SttHint(log=lambda m: None)),
             sent).transcribe(AUDIO)
        assert sent[0].get("prompt") == want, default


# -- 9. do-005: the opener-free shape, in more than one order ---------------------------
# do-004's rotation finding: with "Hey X." the LAST slot was swallowed (Vera 47.9 -> 100
# when moved first). The bare list holds no opener, so each order's prompt must name every
# seat, hold no "hey", and the guard must treat each order alike.

SEATS = ["DAVE", "Bob", "Carol"]
ORDERS = [list(o) for o in __import__("itertools").permutations(SEATS)]


@pytest.mark.parametrize("order", ORDERS, ids=lambda o: "-".join(o))
def test_the_prompt_is_a_bare_list_in_every_order(order):
    p = wake.hint_prompt(order)
    assert p == ", ".join(order) + "."
    assert "hey" not in words_of(p) and words_of(p) == {w.lower() for w in order}
    assert not hasattr(wake, "HINT_OPENER")


@pytest.mark.parametrize("order", ORDERS, ids=lambda o: "-".join(o))
def test_every_order_keeps_a_real_call_to_each_seat_and_drops_each_echo(order):
    p = wake.hint_prompt(order)
    for seat in SEATS:
        assert not stt.echoes_prompt(f"Hey {seat}, what's on?", p)   # a call: kept
        assert not stt.echoes_prompt(f"hey {seat.lower()}", p)
        assert stt.echoes_prompt(f"{seat}.", p)                        # the last slot alone, etc.
    assert stt.echoes_prompt(p, p)
    assert stt.echoes_prompt(", ".join(reversed(order)), p)


def test_wake_file_order_is_the_prompt_order(env):
    seats_file(env / "wake.json", {"carol": {}, "dave": {"hint": "DAVE"}, "bob": {}})
    assert wake.hint_prompt(wake.hint_words()) == "Carol, DAVE, Bob."


def test_the_budget_drops_whole_trailing_seats_and_keeps_the_shape():
    assert wake.hint_prompt(["Alpha", "Bravo", "Charlie"], limit=len("Alpha, Bravo.")) == "Alpha, Bravo."
    assert wake.hint_prompt(["Alpha", "Bravo"], limit=3) == ""


# -- 10. the drop-equals-prompt guard ---------------------------------------------------

@pytest.mark.parametrize("text, echo", [
    ("DAVE, Bob, Carol.", True),
    ("dave bob carol", True),                  # case-blind
    ("Carol!", True),                          # one seat, punctuation stripped
    ("...Bob?  DAVE", True),
    ("Hey DAVE.", False),                      # a call needs "hey", never a prompt word
    ("DAVE, stop.", False),
    ("Bobby", False),                          # a whole word, not a prefix
    ("", False), ("   ", False), ("...", False),   # no words: nothing to drop
])
def test_echoes_prompt(text, echo):
    assert stt.echoes_prompt(text, "DAVE, Bob, Carol.") is echo


def test_no_prompt_no_echo():
    assert not stt.echoes_prompt("DAVE", "") and not stt.echoes_prompt("DAVE", "...")


def fake_says(client, sent, said):
    """CLIENT at a fake engine that records each form and answers SAID(form)."""
    def handler(request):
        f = form(request)
        sent.append(f)
        return httpx.Response(200, json={"text": said(f)})
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def test_whisper_drops_a_transcript_that_is_only_the_prompt():
    sent = []
    c = fake_says(stt.WhisperSTT("http://127.0.0.1:2022/v1", final_prompt="DAVE, Bob."), sent,
                  lambda f: "DAVE, Bob." if f.get("prompt") else "DAVE, Bob.")
    assert c.transcribe_final(AUDIO) == ""          # prompted, and only prompt words: an echo
    assert c.transcribe(AUDIO) == "DAVE, Bob."      # no prompt sent: nothing to echo, kept


def test_whisper_keeps_a_call_under_the_prompt():
    sent = []
    c = fake_says(stt.WhisperSTT("http://127.0.0.1:2022/v1", final_prompt="DAVE, Bob."), sent,
                  lambda f: "Hey DAVE, what's on?")
    assert c.transcribe_final(AUDIO) == "Hey DAVE, what's on?"


def test_the_guard_reads_the_text_after_annotations_are_stripped():
    sent = []
    c = fake_says(stt.WhisperSTT("http://127.0.0.1:2022/v1", final_prompt="DAVE."), sent,
                  lambda f: "[BLANK_AUDIO] DAVE.")
    assert c.transcribe_final(AUDIO) == ""
    k = fake_says(stt.WhisperSTT("http://127.0.0.1:2022/v1", final_prompt="DAVE.",
                                 drop_annotations=False), [], lambda f: "[BLANK_AUDIO] DAVE.")
    assert k.transcribe_final(AUDIO) == "[BLANK_AUDIO] DAVE."   # kept tags are words too


# -- 11. the FINAL only: transcribe_final carries it, transcribe (partials) never -------

def test_final_prompt_rides_transcribe_final_only():
    sent = []
    c = fake(stt.WhisperSTT("http://127.0.0.1:2022/v1", final_prompt="DAVE."), sent)
    c.transcribe(AUDIO)
    c.transcribe_final(AUDIO)
    assert [s.get("prompt") for s in sent] == [None, "DAVE."]


def test_prompt_rides_both_when_no_final_prompt_is_given():
    """The bench's raw path: WhisperSTT(prompt=...) sends it on every request."""
    sent = []
    c = fake(stt.WhisperSTT("http://127.0.0.1:2022/v1", prompt="DAVE."), sent)
    c.transcribe(AUDIO)
    c.transcribe_final(AUDIO)
    assert [s.get("prompt") for s in sent] == ["DAVE.", "DAVE."]


def test_final_transcriber_falls_back_to_transcribe():
    class Plain:
        def transcribe(self, audio):
            return "plain"
    assert stt.final_transcriber(Plain())(AUDIO) == "plain"
    w = stt.WhisperSTT("http://127.0.0.1:2022/v1")
    try:
        assert stt.final_transcriber(w) == w.transcribe_final
    finally:
        w.close()


def test_following_stt_passes_transcribe_final_through(env):
    (env / "ears.json").write_text(json.dumps({"final": {"url": "http://127.0.0.1:2022/v1"}}))
    sent = []
    f = stt.FollowingSTT(make=lambda url, model: fake(
        stt.WhisperSTT(url, model=model, final_prompt="DAVE."), sent))
    try:
        f.transcribe(AUDIO)
        f.transcribe_final(AUDIO)
        assert [s.get("prompt") for s in sent] == [None, "DAVE."]
    finally:
        f.close()


def test_a_capture_hints_the_turn_redecode_and_no_partial(tmp_path):
    """End to end through the capture loop: every partial goes out unprompted, the turn's
    re-decode carries the hint, and the turn line is the hinted text."""
    from ears.listen import capture
    from tests.listen_helpers import CountingSource, RecordingSink, energy_detector, read_lines, synth
    sent = []
    w = fake_says(stt.WhisperSTT("http://127.0.0.1:2022/v1", final_prompt="DAVE, Bob."), sent,
                  lambda f: "hey DAVE" if f.get("prompt") else "hey caves")
    source = CountingSource(synth([("silence", 0.5), ("speech", 3.0)]), tail_silence_s=3.0)
    try:
        capture(source, w, RecordingSink(tmp_path / "logs", source), detector=energy_detector(),
                heartbeat=0, stt_workers=0, turn_text="redecode")
    finally:
        w.close()
    lines = read_lines(tmp_path / "logs")
    partials = [l for l in lines if l["kind"] == "partial"]
    (turn,) = [l for l in lines if l["kind"] == "turn"]
    assert len(partials) >= 2 and {p["text"] for p in partials} == {"hey caves"}
    prompted = [s for s in sent if s.get("prompt")]
    assert len(prompted) == 1 and prompted[0]["prompt"] == "DAVE, Bob."
    assert len(sent) == len(partials) + 1
    assert turn["text"] == "hey DAVE" and turn["text_from"] == "redecode"


def test_a_capture_whose_redecode_echoes_keeps_the_unhinted_partials(tmp_path):
    """An echo on the final is dropped to '', so the turn falls back to its joined
    partials (never the prompt)."""
    from ears.listen import capture
    from tests.listen_helpers import CountingSource, RecordingSink, energy_detector, read_lines, synth
    sent = []
    w = fake_says(stt.WhisperSTT("http://127.0.0.1:2022/v1", final_prompt="DAVE, Bob."), sent,
                  lambda f: "DAVE, Bob." if f.get("prompt") else "hmm")
    source = CountingSource(synth([("silence", 0.5), ("speech", 1.0)]), tail_silence_s=3.0)
    try:
        capture(source, w, RecordingSink(tmp_path / "logs", source), detector=energy_detector(),
                heartbeat=0, stt_workers=0, turn_text="redecode")
    finally:
        w.close()
    (turn,) = [l for l in read_lines(tmp_path / "logs") if l["kind"] == "turn"]
    assert turn["text"] == "hmm" and turn["text_from"] == "joined" and turn["redecode"] == "empty"
