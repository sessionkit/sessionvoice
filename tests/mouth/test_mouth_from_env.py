"""mouth say and mouth play are From whoever ran them, as ``message send`` names them (EARS-523).

Before: the From was the voice-agent word (``VOICEMODE_AGENT`` or ``CLAUDE_CODE_AGENT``),
else ``mouth``: every seat on a host was one sender, and a person's ``mouth play`` was
the mouth mailing itself. Now ``mouth.sender`` reads the environment in sessionmail's
order: the seat (its declared plus address, else the seat variable), then the session,
and in a plain shell the person's login name. The domain is always this host's.

The trust path is untouched (``box.allowed``): every From written here is on this host,
so it is admitted with no operator block and with one, and a stranger is still refused.
"""
from __future__ import annotations

import email
import email.policy
import json
import threading
import wave
from email.message import EmailMessage
from pathlib import Path

import numpy as np
import pytest

import mouth
from ears import operator_config as oc
from mouth import audio, box, notice, player, sender

from maildir_put import put
from tests import env_fence

HOST = box._host()
SEAT = "widget+tm-1.worker.0a1b2c3d"
CONFIGURED = {"operator": "you@host.example", "mailbox": "you", "privileged": ["you", "you@host.example"]}
STRANGER = "stranger@elsewhere.example"
IDENTITY = ("EMAIL", "TM_AGENT_ADDRESS", "CLAUDE_CODE_AGENT", "CLAUDE_CODE_SESSION_ID",
            "CLAUDE_SESSION_ID", "VOICEMODE_AGENT", "VOICEMODE_SESSION_ID")


@pytest.fixture
def shell(monkeypatch):
    """A plain shell: no seat, no session, and the login name ``vera``."""
    for k in IDENTITY:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(sender.getpass, "getuser", lambda: "vera")
    return monkeypatch


@pytest.fixture
def seat(shell):
    """A Taskmaster seat: its declared plus address, its agent word, its session."""
    shell.setenv("EMAIL", f"{SEAT}@{HOST}")
    shell.setenv("CLAUDE_CODE_AGENT", "worker")
    shell.setenv("CLAUDE_CODE_SESSION_ID", "5e55-1d")
    return shell


@pytest.fixture
def d(tmp_path, monkeypatch):
    d = tmp_path / "mouth"
    monkeypatch.setenv("MOUTH_DIR", str(d))
    monkeypatch.setenv("MOUTH_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MOUTH_DEVICE", "null")
    monkeypatch.setenv("MOUTH_BACKEND", "silence")
    monkeypatch.setenv("MOUTH_IDLE_S", "1")
    monkeypatch.setenv("MOUTH_RESUME_S", "0")
    monkeypatch.setenv("MOUTH_SOUNDS_DIR", str(tmp_path / "sounds"))
    env_fence.delenv(monkeypatch, "MOUTH_MAILBOX")
    env_fence.delenv(monkeypatch, "MOUTH_MAIL_LOG")
    env_fence.delenv(monkeypatch, "MOUTH_MAIL_ALLOW")
    oc.clear()
    yield d
    oc.clear()


@pytest.fixture(params=["unconfigured", "operator"])
def mode(request, monkeypatch):
    """Both trust modes, the operator block stubbed (no ``session`` runs)."""
    answer = oc.NOTHING if request.param == "unconfigured" else oc.parse(CONFIGURED)
    monkeypatch.setattr(oc, "current", lambda: answer)
    return request.param


def mails(d) -> list:
    return [email.message_from_bytes(f.read_bytes(), policy=email.policy.default)
            for f in sorted((d / "box" / "new").iterdir())]


def wav(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\0\0" * 800)
    return path


def saying(tmp_path) -> list:
    return [r for f in sorted((tmp_path / "logs").glob("heard_*.jsonl"))
            for r in map(json.loads, f.read_text().splitlines()) if r.get("kind") == "saying"]


def play_all(d) -> None:
    t = threading.Thread(target=player.serve, kwargs={"d": d, "idle_exit_s": 0.3}, daemon=True)
    t.start()
    t.join(10)


# -- the order, pinned to message send's ----------------------------------------------

def test_the_order_is_sessionmails():
    # sessionmail src/sessionmail/_seat.py: SEAT_VARS (:64), IDENTITY_VARS (:69, with
    # sessiontools' SESSION_ID_VARS), ADDRESS_VARS (:77); resolve() (:861-873)
    assert sender.IDENTITY_VARS == ("TM_AGENT_ADDRESS", "CLAUDE_CODE_AGENT",
                                    "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID")
    assert sender.ADDRESS_VARS == ("EMAIL", "TM_AGENT_ADDRESS")


@pytest.mark.parametrize("env, want", [
    # a seat: EMAIL declares its plus address; the domain is never kept
    ({"EMAIL": f"{SEAT}@{HOST}", "CLAUDE_CODE_AGENT": "worker"}, SEAT),
    ({"EMAIL": f"{SEAT}@{HOST}.tail.example", "CLAUDE_CODE_AGENT": "worker"}, SEAT),
    # TM_AGENT_ADDRESS is both the identity and the declaration
    ({"TM_AGENT_ADDRESS": "widget+tm-1.worker"}, "widget+tm-1.worker"),
    # EMAIL wins over TM_AGENT_ADDRESS as the declaration
    ({"EMAIL": f"{SEAT}@{HOST}", "TM_AGENT_ADDRESS": "widget+tm-1.worker"}, SEAT),
    # an EMPTY TM_AGENT_ADDRESS is unset (a seat runs with one): the agent word is next
    ({"TM_AGENT_ADDRESS": "  ", "CLAUDE_CODE_AGENT": "worker"}, "worker"),
    # a plus address on another host declares nothing
    ({"EMAIL": "widget+tm-1.worker@otherhost.example", "CLAUDE_CODE_AGENT": "worker"}, "worker"),
    # a person's plain EMAIL declares nothing
    ({"EMAIL": f"vera@{HOST}", "CLAUDE_CODE_AGENT": "Some Agent"}, "some-agent"),
    # a session alone is a seat
    ({"CLAUDE_CODE_SESSION_ID": "5E55-1D"}, "5e55-1d"),
    ({"CLAUDE_SESSION_ID": "abc"}, "abc"),
    # an identity, then a declaration it does not name: the declaration is the address
    ({"CLAUDE_CODE_SESSION_ID": "abc", "EMAIL": f"{SEAT}@{HOST}"}, SEAT),
    # VOICEMODE_* name a voice and a session for the voice, never a From
    ({"VOICEMODE_AGENT": "golden", "VOICEMODE_SESSION_ID": "x"}, "vera"),
    # a plain shell: message send refuses; the mouth is From the person
    ({}, "vera"),
    # no declaration, but a stray one-sided plus: not a plus address
    ({"EMAIL": f"+x@{HOST}"}, "vera"),
])
def test_the_local_part(shell, env, want):
    assert sender.local_part(env, HOST) == want
    assert sender.address(env) == f"{want}@{HOST}"


def test_no_login_name_is_the_mouth_as_before(shell):
    def none():
        raise OSError("no login")
    shell.setattr(sender.getpass, "getuser", none)
    assert sender.address({}) == f"mouth@{HOST}"


# -- (1) a seat: say and play are From the seat, admitted, and played -----------------

def test_a_seats_say_is_from_the_seat_and_plays(seat, d, mode, tmp_path):
    item = mouth.say("from the seat", d=d, spawn=False)
    (m,) = mails(d)
    assert m["From"] == f"{SEAT}@{HOST}"
    assert m["X-Mouth-Agent"] == "worker"          # the agent word rides as before
    assert box.allowed(str(m["From"]))
    (line,) = box.Queue(d).lines()
    assert line["mail_id"] == item["mail_id"] and line["agent"] == "worker"
    play_all(d)
    assert [r["text"] for r in saying(tmp_path)] == ["from the seat"]


def test_a_seats_play_is_from_the_seat_and_plays(seat, d, mode, tmp_path):
    song = wav(tmp_path / "sounds" / "song.wav")
    mouth.play(str(song), d=d, spawn=False, instrumental=False)
    (m,) = mails(d)
    assert m["From"] == f"{SEAT}@{HOST}"
    (line,) = box.Queue(d).lines()
    assert line["_allowed"] and not line.get("_untrusted") and line["file"]
    play_all(d)
    assert [r["text"] for r in saying(tmp_path)] == ["[sound song.wav]"]


# -- (2) a plain shell: From the person, admitted, and played --------------------------

def test_a_plain_shells_say_is_from_the_person_and_plays(shell, d, mode, tmp_path):
    mouth.say("from a person", d=d, spawn=False)
    (m,) = mails(d)
    assert m["From"] == f"vera@{HOST}"
    assert m["X-Mouth-Agent"] == "mouth"   # agentless: what read() made of it before
    (line,) = box.Queue(d).lines()
    assert line["agent"] == "mouth" and line["mail_from"] == f"vera@{HOST}"
    play_all(d)
    assert [r["text"] for r in saying(tmp_path)] == ["from a person"]


def test_a_plain_shells_play_is_from_the_person_and_plays(shell, d, mode, tmp_path):
    song = wav(tmp_path / "sounds" / "song.wav")
    mouth.play(str(song), d=d, spawn=False, instrumental=False)
    (m,) = mails(d)
    assert m["From"] == f"vera@{HOST}"
    play_all(d)
    assert [r["text"] for r in saying(tmp_path)] == ["[sound song.wav]"]


def test_a_plain_shells_drop_notice_still_goes_nowhere(shell, d, monkeypatch):
    # A box named for the person exists. Read back, the agentless line's agent is
    # still ``mouth`` (not the From's local part), so its drop notice has no
    # recipient, as before: no new mail to the person's box.
    monkeypatch.setattr(notice, "_box_exists", lambda name: name == "vera")
    mouth.say("dropped later", d=d, spawn=False)
    (f,) = (d / "box" / "new").iterdir()
    back = box.read(f, d)
    assert back["agent"] == "mouth" and back.get("notice_to") is None
    to, _reply, why = notice.recipient(back)
    assert to is None and "agent mouth" in why


# -- (3) an unknown sender is still refused -----------------------------------------

def test_an_unknown_sender_is_still_refused(shell, d, mode, tmp_path):
    mouth.say("mine", d=d, spawn=False)
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = STRANGER, f"mouth@{HOST}", "hi"
    m["Message-ID"] = "<1.stranger@elsewhere.example>"
    m.set_content("buy now")
    put(box.box_dir(d), f"1.stranger.{HOST}", bytes(m))
    assert not box.allowed(STRANGER)
    lines = box.Queue(d).lines(act=True)
    assert [x["text"] for x in lines] == ["mine"]
    assert [p.name for p in (box.box_dir(d) / "cur").iterdir()] == [f"1.stranger.{HOST}:2,T"]


# -- re-drops keep the line's own sender ---------------------------------------------

def test_an_amend_keeps_the_lines_from(seat, d):
    item = mouth.say("first", d=d, spawn=False)
    seat.setenv("EMAIL", f"other+tm-2.worker@{HOST}")    # someone else amends it
    mouth.amend(item["utt"], "second", d=d)
    froms = sorted((str(m["Subject"]), str(m["From"])) for m in mails(d))
    assert froms == [("first", f"{SEAT}@{HOST}"), ("second", f"{SEAT}@{HOST}")]


def test_a_resume_is_from_the_cut_lines_sender_not_the_players(shell, d):
    # The player's env is whoever spawned it: a plain shell's here.
    mouth.say("the rest", d=d, spawn=False,
              extra={"resume_of": "abc", "agent": "worker", "mail_from": f"{SEAT}@{HOST}"})
    (m,) = mails(d)
    assert m["From"] == f"{SEAT}@{HOST}"


def test_a_resume_with_no_sender_is_from_its_agent_as_before(shell, d):
    mouth.say("the rest", d=d, spawn=False, extra={"resume_of": "abc", "agent": "worker"})
    (m,) = mails(d)
    assert m["From"] == f"worker@{HOST}"


def test_a_resumes_from_is_this_hosts(shell, d):
    mouth.say("the rest", d=d, spawn=False,
              extra={"resume_of": "abc", "mail_from": f"{SEAT}@{HOST}.tail.example"})
    (m,) = mails(d)
    assert m["From"] == f"{SEAT}@{HOST}"


def test_a_say_again_replay_is_from_the_original_sender(seat, d, tmp_path, monkeypatch):
    monkeypatch.setenv("MOUTH_KEEP_AUDIO", "1")
    root = tmp_path / "audio"
    kept = audio.keep({"utt": "abc123", "text": "the line", "agent": "worker",
                       "mail_from": f"{SEAT}@{HOST}"},
                      [np.zeros(800, dtype=np.float32)], 8000, "kokoro", root=root)
    assert json.loads(kept.with_suffix(".json").read_text())["mail_from"] == f"{SEAT}@{HOST}"
    kept.unlink()                      # rung 2: the note's text, in the note's voice
    seat.setenv("EMAIL", f"keeper+tm-3.worker@{HOST}")   # the keeper runs `mouth again`
    mouth.again("abc123", d=d, spawn=False, root=root)
    (m,) = mails(d)
    assert m["From"] == f"{SEAT}@{HOST}"
