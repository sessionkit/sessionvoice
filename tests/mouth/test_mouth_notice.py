"""A line the mouth drops always reaches its sender (EARS-164).

Every drop (a said row with ``cut: true``) goes through ``player._said``, which
calls ``notice.dropped``: a mailed line (R3) gets a reply To its From, a CLI
line (R1) mail To its session's box (else its agent's), and the said row
records where the notice went. Nothing here reaches sessionmail: the conftest
fence points ``$MOUTH_SENDMAIL`` at ``fixtures/fake_sendmail.py``, a fake
``message send`` that builds the mail and "delivers" it into the fenced
``$HOME/.mail/agents/<local part>/new``.
A ``--wait`` caller still waiting (R2) hears its drop on stderr instead, and
the row says ``waiter``; one that has gone is mailed like any CLI line.
"""

from __future__ import annotations

import contextlib
import email
import email.policy
import io
import json
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import mouth
from mouth import box as _box
from mouth import notice, output, player

from maildir_put import put
from tests import env_fence

HOST = socket.gethostname().split(".")[0].lower()
FAKE = Path(__file__).parent / "fixtures" / "fake_sendmail.py"
LONG = "word " * 20            # ~6.7 s of silence: long enough to cut mid-line


# -- fixtures and helpers ----------------------------------------------------------

@pytest.fixture
def env(tmp_path, monkeypatch):
    """A CLI caller: agent ivy, session s-test. Boxes exist only where a test makes them."""
    d, logs, q = tmp_path / "mouth", tmp_path / "logs", tmp_path / "queue"
    for sub in ("new", "cur", "tmp"):
        (q / sub).mkdir(parents=True)
    monkeypatch.setenv("MOUTH_DIR", str(d))
    monkeypatch.setenv("MOUTH_MAILBOX", str(q))
    monkeypatch.setenv("MOUTH_LOG_DIR", str(logs))
    monkeypatch.setenv("MOUTH_BACKEND", "silence")
    monkeypatch.setenv("MOUTH_DEVICE", "null")
    monkeypatch.setenv("MOUTH_IDLE_S", "1")
    monkeypatch.setenv("MOUTH_RESUME_S", "0")   # a resume would hold the player open
    monkeypatch.setenv("VOICEMODE_AGENT", "ivy")
    monkeypatch.setenv("VOICEMODE_SESSION_ID", "s-test")
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    env_fence.delenv(monkeypatch, "MOUTH_MAIL_ALLOW")
    agents = Path(os.environ["MOUTH_FAKE_MAIL_ROOT"])
    return SimpleNamespace(d=d, logs=logs, q=q, agents=agents, n=[0])


def mkbox(env, name: str) -> Path:
    b = env.agents / name
    for sub in ("new", "cur", "tmp"):
        (b / sub).mkdir(parents=True, exist_ok=True)
    return b


def say(text, **kw):
    kw.setdefault("device", "null")
    kw.setdefault("backend", "silence")
    return mouth.say(text, spawn=False, **kw)


def run_player(d: Path, idle: float = 0.3) -> threading.Thread:
    t = threading.Thread(target=player.serve, kwargs={"d": d, "idle_exit_s": idle}, daemon=True)
    t.start()
    return t


def rows(logs: Path) -> list[dict]:
    return [json.loads(x) for f in sorted(Path(logs).glob("heard_*.jsonl")) for x in f.read_text().splitlines()]


def said(logs: Path) -> dict:
    return {r["utt"]: r for r in rows(logs) if r["kind"] == "said"}


def mails(env, name: str) -> list:
    new = env.agents / name / "new"
    if not new.is_dir():
        return []
    return [email.message_from_bytes(p.read_bytes(), policy=email.policy.default)
            for p in sorted(new.iterdir())]


def sends(env) -> list:
    """Every ``message send`` the fake saw: ``{"argv": [...], "env": {...}}``."""
    log = env.agents / ".sendmail.jsonl"
    return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []


def argvs(env) -> list:
    return [x["argv"] for x in sends(env)]


def flag(argv: list, name: str):
    """The value of ``name`` in a send's argv (the first), or None."""
    return argv[argv.index(name) + 1] if name in argv else None


def absent(monkeypatch):
    def refuse(name, sr, pan=None, meta=None):
        raise output.DeviceAbsent(f"output device {name!r} is not present")
    monkeypatch.setattr(output, "open_output", refuse)
    # A played file is a bed (EARS-152): its Mixer opens the device through
    # _plain_output, never open_output. Refuse there too, or the file finds the
    # REAL device by that name and plays on it (F-M1, verify-001).
    monkeypatch.setattr(output, "_plain_output", refuse)


def deliver(env, body="", subject="hi", sender=f"ivy@{HOST}", **headers) -> str:
    env.n[0] += 1
    m = email.message.EmailMessage()
    m["From"], m["To"], m["Subject"] = sender, f"mouth@{HOST}", subject
    mid = headers.pop("Message_ID", None) or f"<{env.n[0]}.test@{HOST}.session-mail>"
    m["Message-ID"] = mid
    for k, v in headers.items():
        m[k.replace("_", "-")] = v
    m.set_content(body)
    put(env.q, f"{time.time_ns()}.{env.n[0]}.{HOST}", bytes(m))
    return mid


def flags(env) -> list:
    return [f.name.split(":2,")[1] for f in sorted((env.q / "cur").iterdir()) if ":2," in f.name]


class _FailingOut(output.NullOut):
    """Takes a few blocks, then fails as told: a device that went, or a write error."""

    def __init__(self, sr, pan, exc, after=6):
        super().__init__(sr, pan)
        self._exc, self._left = exc, after

    def write(self, block):
        if self._left <= 0:
            raise self._exc
        self._left -= 1
        return super().write(block)


def failing_out(monkeypatch, exc):
    monkeypatch.setattr(output, "open_output",
                        lambda name, sr, pan=None, meta=None: _FailingOut(sr, pan, exc))


# -- SC1: a mailed line (R3) refused as device-absent ------------------------------

def test_r3_device_absent_mails_the_sender(env, monkeypatch):
    absent(monkeypatch)
    mid = deliver(env, "Yes, I have booked the table for three at seven.", sender=f"ivy@{HOST}",
                  X_Mouth_Device="airpods", X_Session_From="9af31984")
    run_player(env.d).join(5)
    got = mails(env, "ivy")
    assert len(got) == 1
    m = got[0]
    assert m["To"] == f"ivy@{HOST}"
    assert "mouth@" in m["From"]
    assert m["Subject"].startswith('not said, device-absent: "Yes, I have booked')
    assert len(m["Subject"]) <= 69
    text = m.get_content()
    assert "Yes, I have booked the table for three at seven." in text
    assert "output device 'airpods' is not present" in text
    assert m["In-Reply-To"] == mid
    assert m["Auto-Submitted"] == "auto-replied"
    assert m["Message-ID"].endswith(f".mouth-notice@{HOST}>")     # the new form (EARS-381)
    assert m["Mouth-Notice"] == "device-absent"
    assert m["Priority"] is None                      # a device-absent drop wakes
    assert list((env.q / "new").iterdir()) == []      # nothing new in the mouth's own queue
    (argv,) = argvs(env)                              # one `message send`, To the sender
    assert argv[:2] == ["send", f"ivy@{HOST}"] and flag(argv, "-f") == "-"
    assert flag(argv, "-r") == mid and flag(argv, "--message-id") == m["Message-ID"]
    assert {"--raw", "--no-shorten", "--json"} <= set(argv)
    row = next(iter(said(env.logs).values()))
    assert row["reason"] == "device-absent" and row["notice"] == f"sent ivy@{HOST}"


def test_r1_is_auto_generated_r3_auto_replied(env, monkeypatch):
    absent(monkeypatch)
    mkbox(env, "s-test")
    say("from the cli", device="airpods")
    deliver(env, "from a mail", X_Mouth_Device="airpods")
    run_player(env.d).join(5)
    assert [m["Auto-Submitted"] for m in mails(env, "s-test")] == ["auto-generated"]
    assert [m["Auto-Submitted"] for m in mails(env, "ivy")] == ["auto-replied"]


def test_extras_cannot_redirect_a_mailed_notice(env, monkeypatch):
    absent(monkeypatch)
    mkbox(env, "vera")
    deliver(env, "mine", X_Mouth_Device="airpods",
            X_Mouth_Extra=json.dumps({"notice_to": f"vera@{HOST}"}))
    run_player(env.d).join(5)
    assert len(mails(env, "ivy")) == 1 and mails(env, "vera") == []


def test_mid_and_from_ignore_the_extras(env):
    mid = deliver(env, "x", X_Mouth_Extra=json.dumps({"_mid": "<forged@x>", "_from": "vera@elsewhere",
                                                      "notice_to": "vera@elsewhere"}))
    e = _box.read(next((env.q / "new").iterdir()), env.d)
    assert (e["_mid"], e["_from"], e["notice_to"]) == (mid, f"ivy@{HOST}", f"ivy@{HOST}")


def test_a_raw_line_carries_no_notice_to(env):
    say("from the cli")
    e = _box.read(next((env.q / "new").iterdir()), env.d)
    assert "notice_to" not in e


# -- SC3: a CLI say (R1) refused as device-absent -----------------------------------

def test_r1_device_absent_mails_the_session(env, monkeypatch):
    absent(monkeypatch)
    mkbox(env, "s-test")
    mkbox(env, "ivy")
    item = say("hello there", device="airpods")
    run_player(env.d).join(5)
    got = mails(env, "s-test")
    assert len(got) == 1 and mails(env, "ivy") == []   # the session first: the exact address
    m = got[0]
    assert m["To"] == f"s-test@{HOST}" and "mouth@" in m["From"]
    assert m["Subject"] == 'not said, device-absent: "hello there"'
    body = m.get_content()
    assert "what:   hello there" in body and "is not present" in body
    assert "heard:  nothing (it never started)" in body
    assert "`mouth devices`" in body
    assert body.rstrip().endswith("Replies to this mail are not spoken. To say it again, send a new line.")
    assert m["Mouth-Utt"] == item["utt"]
    assert said(env.logs)[item["utt"]]["notice"] == f"sent s-test@{HOST}"


def test_r1_falls_back_to_the_agent_box(env, monkeypatch):
    absent(monkeypatch)
    mkbox(env, "ivy")
    item = say("hello there", device="airpods")
    run_player(env.d).join(5)
    assert len(mails(env, "ivy")) == 1
    assert said(env.logs)[item["utt"]]["notice"] == f"sent ivy@{HOST}"


def test_r1_no_box_records_none(env, monkeypatch):
    absent(monkeypatch)
    item = say("hello there", device="airpods")
    run_player(env.d).join(5)
    n = said(env.logs)[item["utt"]]["notice"]
    assert n.startswith("none: ") and "session s-test" in n and "agent ivy" in n
    assert argvs(env) == []                            # nothing was sent anywhere


# -- D1-D10 on R1: each reason notifies ---------------------------------------------

@pytest.fixture
def told(env):
    """The caller's session box exists; returns the notices it got, by utt."""
    mkbox(env, "s-test")

    def get() -> dict:
        return {m["Mouth-Utt"]: m for m in mails(env, "s-test")}
    return get


def test_synth_error_notifies(env, monkeypatch, told):
    class Broken:
        def __init__(self, *a, **kw):
            raise RuntimeError("no such voice")
    monkeypatch.setattr(player, "Synth", Broken)
    item = say("a voice it cannot do")
    run_player(env.d).join(5)
    m = told()[item["utt"]]
    assert m["Mouth-Notice"] == "error" and "no such voice" in m.get_content()
    assert said(env.logs)[item["utt"]]["notice"] == f"sent s-test@{HOST}"


def test_error_mid_line_names_what_was_not_said(env, monkeypatch, told):
    failing_out(monkeypatch, OSError("write failed"))
    item = say(LONG)
    run_player(env.d).join(8)
    row = said(env.logs)[item["utt"]]
    assert row["reason"] == "error" and row["played_s"] > 0
    m = told()[item["utt"]]
    body = m.get_content()
    assert m["Subject"].startswith("cut at ") and ', error: "...word' in m["Subject"]
    assert f"heard:  {row['text_played_est']}  (an estimate)" in body   # cut inside a word, perhaps
    assert "unsaid: word" in body                     # the cut word is unsaid in full
    assert "write failed" in body


def test_device_lost_notifies(env, monkeypatch, told):
    failing_out(monkeypatch, output.DeviceLost("the device stopped taking audio"))
    item = say(LONG)
    run_player(env.d).join(8)
    assert said(env.logs)[item["utt"]]["reason"] == "device-lost"
    assert told()[item["utt"]]["Mouth-Notice"] == "device-lost"


def test_play_one_raising_notifies(env, monkeypatch, told):
    def boom(*a, **kw):
        raise RuntimeError("boom")
    monkeypatch.setattr(player, "play_one", boom)
    item = say("never played")
    run_player(env.d).join(5)
    assert said(env.logs)[item["utt"]]["reason"] == "error"
    assert "boom" in told()[item["utt"]].get_content()


def test_stop_cut_and_flush_notify_each_line(env, told):
    a, b = say(LONG), say("queued behind it")
    th = run_player(env.d)
    time.sleep(0.5)
    mouth.stop()
    th.join(5)
    got = told()
    assert set(got) == {a["utt"], b["utt"]}               # one notice per line
    assert got[a["utt"]]["Subject"].startswith("cut at ")
    assert got[b["utt"]]["Subject"] == 'not said, stop: "queued behind it"'
    rows_ = said(env.logs)
    assert rows_[a["utt"]]["notice"] == rows_[b["utt"]]["notice"] == f"sent s-test@{HOST}"


def test_barge_in_cut_and_flush_notify(env, told):
    a, b = say(LONG), say("queued behind it")
    th = run_player(env.d)
    time.sleep(0.5)
    mouth.stop("barge-in", flush=True)
    th.join(5)
    got = told()
    assert set(got) == {a["utt"], b["utt"]}
    assert all(m["Mouth-Notice"] == "barge-in" for m in got.values())


def test_barge_in_notice_rides(env, told):
    a = say(LONG)
    th = run_player(env.d)
    time.sleep(0.5)
    mouth.stop("barge-in", flush=True)
    th.join(5)
    assert told()[a["utt"]]["Priority"] == "non-urgent"    # rides, never wakes (Q1)


def test_a_flushed_barge_in_line_is_told_it_will_not_return(env, told):
    _, b = say(LONG), say("queued behind it")
    th = run_player(env.d)
    time.sleep(0.5)
    mouth.stop("barge-in", flush=True)
    th.join(5)
    assert "underruns" not in said(env.logs)[b["utt"]]
    body = told()[b["utt"]].get_content()
    assert "flushed" in body and "will not come back" in body
    assert "may re-offer" not in body


def test_barge_in_notice_names_the_unsaid_rest(env, told):
    # D13: the cut line's notice already names what was not said, so a resume that
    # could not be queued needs no second notice
    a = say(LONG)
    th = run_player(env.d)
    time.sleep(0.5)
    mouth.stop("barge-in", flush=True)
    th.join(5)
    body = told()[a["utt"]].get_content()
    assert "unsaid: word" in body and "(an estimate)" in body


def test_a_cut_playing_line_is_told_the_decision_on_its_row(monkeypatch):
    # EARS-306: the notice reads the player's decision (player._plan, on the row), never
    # a guess of its own: no decision on the row is a WON'T, whatever resume_s says
    monkeypatch.setenv("MOUTH_RESUME_S", "20")
    rec = {"reason": "barge-in", "played_s": 1.0, "cut_at_s": 1.0, "text_played_est": "one two",
           "underruns": 0}
    item = {"utt": "u1", "text": "one two three four", "backend": "silence"}
    assert "WILL replay" in notice.body(item, {**rec, "resume": "will", "resume_utt": "r1",
                                               "resume_s": 20.0})
    assert "will not come back" in notice.body(item, rec)
    assert "will not come back" in notice.body({**item, "resume_of": "u0"}, rec)


def test_interrupted_notifies(env, told):
    a, _ = say(LONG), say("queued behind")
    th = run_player(env.d)
    time.sleep(0.4)
    say("urgent", priority="now")
    th.join(8)
    assert said(env.logs)[a["utt"]]["reason"] == "interrupted"
    got = told()
    assert set(got) == {a["utt"]} and got[a["utt"]]["Mouth-Notice"] == "interrupted"


def _retract_queued(env, monkeypatch, session):
    """A long line plays; a second waits behind it and is retracted from SESSION."""
    _, b = say(LONG), say("never mind")
    th = run_player(env.d)
    time.sleep(0.3)
    if session is None:
        monkeypatch.delenv("VOICEMODE_SESSION_ID", raising=False)
    else:
        monkeypatch.setenv("VOICEMODE_SESSION_ID", session)
    mouth.retract(b["utt"])
    th.join(8)              # the long line plays out: no stop notice of its own
    mouth.stop()
    return b["utt"]


def test_retract_by_another_notifies(env, monkeypatch, told):
    # D9 on R1: another session retracted it, so its sender is told
    utt = _retract_queued(env, monkeypatch, "s-other")
    assert told()[utt]["Mouth-Notice"] == "retracted"
    assert said(env.logs)[utt]["notice"] == f"sent s-test@{HOST}"


def test_retract_by_its_sender_is_quiet(env, monkeypatch, told):
    # the retractor is the sender, and it got the said record on stdout
    utt = _retract_queued(env, monkeypatch, "s-test")
    assert utt not in told() and argvs(env) == []
    assert said(env.logs)[utt]["notice"] == "none: retracted by its sender"


def test_self_retract_needs_a_session(env, monkeypatch):
    # a line with no session, retracted from a shell with no session: two unset
    # values are not a match, so the sender (here its agent box) is still told
    monkeypatch.delenv("VOICEMODE_SESSION_ID")
    mkbox(env, "ivy")
    utt = _retract_queued(env, monkeypatch, None)
    assert [m["Mouth-Utt"] for m in mails(env, "ivy")] == [utt]
    assert said(env.logs)[utt]["notice"] == f"sent ivy@{HOST}"


def test_retract_of_a_playing_line_notifies(env, told):
    # its own session retracted it, but it was PLAYING: that retractor got only
    # the stop record, so the notice tells it how much was heard
    a = say(LONG)
    th = run_player(env.d)
    time.sleep(0.5)
    mouth.retract(a["utt"])
    th.join(8)
    row = said(env.logs)[a["utt"]]
    assert row["reason"] == "retracted" and row["played_s"] > 0
    assert row["notice"] == f"sent s-test@{HOST}" and told()[a["utt"]]["Mouth-Notice"] == "retracted"


def _mailed_retract(env, sender, **headers):
    mkbox(env, "ivy")
    mid = deliver(env, "the meeting is at three")
    deliver(env, "", subject="retract", sender=sender, Supersedes=mid, **headers)
    run_player(env.d).join(5)
    (row,) = [r for r in said(env.logs).values() if r["mail_id"] == mid]
    return row


def test_mailed_retract_by_its_sender_is_quiet(env):
    row = _mailed_retract(env, f"ivy@{HOST}")
    assert row["reason"] == "retracted" and row["notice"] == "none: retracted by its sender"
    assert mails(env, "ivy") == [] and argvs(env) == []


def test_mailed_retract_by_another_notifies(env, signed):
    # another sender's retract counts only signed (v2) or local (EARS-335 rule (b))
    row = _mailed_retract(env, f"vera@{HOST}", X_Session_Signature=signed("2"))
    assert row["notice"] == f"sent ivy@{HOST}"
    assert [m["Mouth-Notice"] for m in mails(env, "ivy")] == ["retracted"]


def test_an_unsigned_retract_by_another_is_refused_and_its_sender_told(env):
    mkbox(env, "ivy")
    mkbox(env, "vera")
    mid = deliver(env, "the meeting is at three")
    rid = deliver(env, "", subject="retract", sender=f"vera@{HOST}", Supersedes=mid)
    run_player(env.d).join(5)
    rows = {r["mail_id"]: r for r in said(env.logs).values()}
    assert rows[mid]["reason"] == "done", "ivy's line is spoken"
    assert rows[rid]["reason"] == "untrusted"
    assert rows[rid]["notice"] == f"sent vera@{HOST}"
    assert [m["Mouth-Notice"] for m in mails(env, "vera")] == ["untrusted"]
    assert mails(env, "ivy") == []


def test_a_retracts_extras_cannot_claim_its_sender(env, signed):
    # N13: retracted_by is the retract mail's parsed From (_from), set after the extras
    extra = json.dumps({"mail_from": f"ivy@{HOST}", "_from": f"ivy@{HOST}"})
    row = _mailed_retract(env, f"vera@{HOST}", X_Mouth_Extra=extra, X_Session_Signature=signed("2"))
    assert row["notice"] == f"sent ivy@{HOST}" and len(mails(env, "ivy")) == 1


def test_expired_and_stale_notify(env, told):
    item = say("too late now", expires_s=0.01)
    time.sleep(0.05)
    run_player(env.d).join(5)
    assert said(env.logs)[item["utt"]]["reason"] == "expired"
    m = told()[item["utt"]]
    assert m["Mouth-Notice"] == "expired" and m["Priority"] is None   # an expiry wakes


def test_a_stale_held_line_notifies(env, monkeypatch, told):
    # D10's other half: a held line queued before his turn began is filed P as stale
    monkeypatch.setattr(player._hold, "check", lambda x: "stale")
    item = say("an answer to the last turn", hold="turn-end")
    run_player(env.d).join(5)
    assert said(env.logs)[item["utt"]]["reason"] == "stale"
    m = told()[item["utt"]]
    assert m["Mouth-Notice"] == "stale" and m["Priority"] is None    # stale wakes, as expiry does
    assert "went stale" in m.get_content()
    assert said(env.logs)[item["utt"]]["notice"] == f"sent s-test@{HOST}"


def test_a_resumes_expiry_rides_and_names_the_line_it_resumed(env, told):
    # N15 (super.sessionvoice): a resume's expiry rides, like the barge-in before it
    item = say("the rest of it", expires_s=0.01, extra={"resume_of": "abc123def456"})
    time.sleep(0.05)
    run_player(env.d).join(5)
    m = told()[item["utt"]]
    assert m["Priority"] == "non-urgent"
    assert "the replay of line abc123def456" in m.get_content()


def test_done_rows_are_unchanged(env, told):
    item = say("all of it")
    run_player(env.d).join(5)
    row = said(env.logs)[item["utt"]]
    assert row["reason"] == "done" and "notice" not in row
    assert told() == {} and argvs(env) == []


# -- guards ----------------------------------------------------------------------

def test_never_mails_the_mouth_or_a_call_box(env, monkeypatch):
    for to in (f"mouth@{HOST}", f"MOUTH+x@{HOST}.tailnet", f"calls+ivy-1368@{HOST}"):
        assert notice._refuse(to) is not None, to
    mkbox(env, "mouth")
    item = {"utt": "u1", "text": "x", "notice_to": f"mouth@{HOST}", "session": "mouth", "agent": "mouth"}
    to, _, why = notice.recipient(item)
    assert to is None and "the mouth's own box" in why


def test_r3_recipient_must_be_a_plain_address():
    for bad in ('"a b"@x', "a,b@x", "-oX@x", "a b@x", "<a@x>", "a@", "@x", "a@-x"):
        assert notice._refuse(bad) == "not a plain address", bad
    assert notice._refuse(f"ivy@{HOST}") is None
    assert notice._refuse(f"ivy@{HOST}.example.net") is None
    assert notice._refuse("ivy@elsewhere") == "not an address the mouth takes lines from"


def test_an_unusable_from_falls_back_to_the_session_box(env, monkeypatch):
    mkbox(env, "s-x")
    to, reply, _ = notice.recipient({"notice_to": "-oQ@x", "session": "s-x", "agent": None})
    assert (to, reply) == (f"s-x@{HOST}", False)


def test_a_box_name_must_be_flat(env):
    mkbox(env, "ok.name-1")
    assert notice._box_exists("ok.name-1")
    for bad in ("../ok.name-1", "a/b", "a+b", "a@b", ".hidden", "", None, {"x": 1}):
        assert not notice._box_exists(bad), bad


def test_notice_off(env, monkeypatch):
    absent(monkeypatch)
    mkbox(env, "s-test")
    monkeypatch.setenv("MOUTH_NOTICE", "off")
    item = say("hello there", device="airpods")
    run_player(env.d).join(5)
    assert said(env.logs)[item["utt"]]["notice"] == "none: off"
    assert argvs(env) == [] and mails(env, "s-test") == []


def test_delivery_failure_is_recorded_not_raised(env, monkeypatch):
    absent(monkeypatch)
    mkbox(env, "s-test")
    monkeypatch.setenv("MOUTH_FAKE_SENDMAIL_EXIT", "75")
    a = say("first", device="airpods")
    run_player(env.d).join(5)
    monkeypatch.setenv("MOUTH_SENDMAIL", "/no/such/message")
    b = say("second", device="airpods")
    run_player(env.d).join(5)
    rows_ = said(env.logs)
    assert rows_[a["utt"]]["notice"] == "failed: message send exit 75 kind mta: fake message: told to fail"
    assert rows_[b["utt"]]["notice"].startswith("failed: no message binary ($MOUTH_SENDMAIL: "
                                                "/no/such/message is not an executable")
    assert flags(env) == ["T", "T"]                   # both still filed


def test_dropped_never_raises(env, monkeypatch):
    monkeypatch.setattr(notice, "recipient", lambda item: 1 / 0)
    out = notice.dropped({"utt": "u1", "text": "x"}, {"reason": "error", "cut": True})
    assert out.startswith("failed: ZeroDivisionError")


def test_a_refused_line_is_never_mailed(env):
    mkbox(env, "s-test")
    item = {"utt": "u1", "text": "x", "notice_to": f"ivy@{HOST}", "session": "s-test", "agent": "ivy"}
    assert notice.dropped(item, {"reason": "refused", "cut": True}) == "none: refused sender, not mailed"
    assert argvs(env) == []


def test_a_strange_utt_never_shapes_a_header():
    m = notice.message({"utt": "../x\r\nBcc: a@b", "text": "hi", "mail_id": {"x": 1}},
                       {"reason": "error"}, f"ivy@{HOST}", reply=False)
    assert m.headers["Mouth-Utt"].isalnum() and "\n" not in m.msgid
    assert m.in_reply_to is None                      # a mail_id that is no msg-id is left out
    assert "-r" not in m.args()


# -- a reply to a notice is never spoken (§2.8) --------------------------------------

#: A notice id in the OLD form, minted before EARS-381: a reply to one is still a notice reply.
NOTICE_ID = f"<1921356800054190000.eb1da083a8bf@{HOST}.mouth-notice>"
#: The NEW form, the one `message send --message-id` takes.
NOTICE_ID_NEW = f"<1921356800054190000.eb1da083a8bf.mouth-notice@{HOST}>"


def test_a_reply_to_a_notice_is_never_spoken(env, capfd):
    deliver(env, "resending on airpods", In_Reply_To=NOTICE_ID, References=NOTICE_ID)
    run_player(env.d).join(5)
    assert flags(env) == ["T"]
    assert rows(env.logs) == []                       # no saying, no said, no notice
    assert argvs(env) == []
    assert '"skipped": "notice-reply"' in capfd.readouterr().out


def test_a_reply_to_a_new_form_notice_is_never_spoken(env, capfd):
    deliver(env, "resending on airpods", In_Reply_To=NOTICE_ID_NEW, References=NOTICE_ID_NEW)
    run_player(env.d).join(5)
    assert flags(env) == ["T"]
    assert rows(env.logs) == [] and argvs(env) == []
    assert '"skipped": "notice-reply"' in capfd.readouterr().out


def test_a_new_form_notice_deep_in_references_is_a_notice_reply(env):
    deliver(env, "thanks", References=f"<a@b> {NOTICE_ID_NEW} <c@d>")
    assert _box.Queue(env.d).lines() == []


def test_a_notice_reply_is_hidden_from_readers(env):
    deliver(env, "thanks", References=f"<a@b> {NOTICE_ID}")
    assert _box.Queue(env.d).lines() == []
    assert mouth.status(env.d)["queued"] == 0


def test_a_notice_itself_is_never_spoken(env):
    # N11's backstop: however a notice reaches the queue, it is filed unspoken
    deliver(env, "Your line was not said", Mouth_Notice="device-absent")
    deliver(env, "Your line was not said", Message_ID=NOTICE_ID)
    deliver(env, "Your line was not said", Message_ID=NOTICE_ID_NEW)
    run_player(env.d).join(5)
    assert flags(env) == ["T", "T", "T"] and rows(env.logs) == []


def test_an_amended_line_still_plays(env):
    # N10: an amend's re-drop answers <...@<host>.mouth>, not a notice id
    a = say("the old words")
    mouth.amend(a["utt"], "the new words")
    run_player(env.d).join(5)
    spoken = [r["text"] for r in rows(env.logs) if r["kind"] == "saying"]
    assert spoken == ["the new words"]


def test_a_reply_to_some_other_mail_still_plays(env):
    deliver(env, "a plain reply", In_Reply_To=f"<1.x@{HOST}.mouth>")
    run_player(env.d).join(5)
    assert [r["text"] for r in rows(env.logs) if r["kind"] == "saying"] == ["a plain reply"]


def test_a_cli_line_threaded_on_a_notice_still_plays(env):
    # a raw line is a CLI say, never a mail reply: its --in-reply-to is the caller's choice
    say("deliberately threaded", in_reply_to=NOTICE_ID)
    run_player(env.d).join(5)
    assert [r["text"] for r in rows(env.logs) if r["kind"] == "saying"] == ["deliberately threaded"]


# -- what a notice says ------------------------------------------------------------

def test_subject_is_at_most_69_characters():
    long = "Yes, I have booked the table for three at seven, and the car comes at six thirty."
    s = notice.subject(long, {"reason": "device-absent", "played_s": 0.0})
    assert len(s) <= 69 and s.startswith('not said, device-absent: "Yes, I have') and s.endswith('..."')
    assert s.isascii()                                # message send refuses any other (rca-001 N1)
    p = notice.subject(long, {"reason": "barge-in", "played_s": 2.1, "cut_at_s": 2.1,
                              "text_played_est": "Yes, I have booked"})
    assert len(p) <= 69 and p.startswith('cut at 2.1s, barge-in: "...the table') and p.isascii()


def test_cli_line_matches_the_mail():
    rec = {"reason": "device-absent", "detail": "call 62216 is not live; live calls: none",
           "played_s": 0.0}
    assert notice.cli_line("Yes, booked.", rec) == \
        'mouth: not said, device-absent (call 62216 is not live; live calls: none): "Yes, booked."'
    assert notice.cli_line("x" * 400, rec).endswith('…"')


# -- one send budget per burst (§2.9 N3) ---------------------------------------------

def _r1(env, n: int) -> list:
    mkbox(env, "s-test")
    return [{"utt": f"u{i}", "text": f"line {i}", "session": "s-test"} for i in range(n)]


def test_a_flush_shares_one_send_budget(env, monkeypatch):
    monkeypatch.setattr(notice, "BURST_BUDGET_S", 1.0)
    monkeypatch.setattr(notice, "SEND_TIMEOUT_S", 1.0)
    monkeypatch.setenv("MOUTH_FAKE_SENDMAIL_SLEEP", "5")
    t0 = time.monotonic()
    got = [notice.dropped(x, {"reason": "stop", "cut": True}) for x in _r1(env, 3)]
    took = time.monotonic() - t0
    assert got[0] == "failed: message send timed out after 1.0s"
    assert got[1:] == ["failed: send budget spent"] * 2
    assert took < 2.0                                  # at most one stall, not three


def test_a_long_flush_under_a_stall_stalls_once(env, monkeypatch):
    # the gap counts every decision, so skipped rows 0.4 s apart stay in the same burst
    monkeypatch.setattr(notice, "BURST_BUDGET_S", 0.5)
    monkeypatch.setattr(notice, "SEND_TIMEOUT_S", 0.5)
    monkeypatch.setenv("MOUTH_FAKE_SENDMAIL_SLEEP", "5")
    got = []
    for x in _r1(env, 5):
        got.append(notice.dropped(x, {"reason": "stop", "cut": True}))
        time.sleep(0.4)
    assert got[0].startswith("failed: message send timed out")
    assert got[1:] == ["failed: send budget spent"] * 4


def test_a_burst_has_a_cap(env, monkeypatch):
    # N19: a sustained flood cannot keep a spent budget spent for ever
    monkeypatch.setattr(notice, "BURST_BUDGET_S", 0.5)
    monkeypatch.setattr(notice, "SEND_TIMEOUT_S", 0.5)
    monkeypatch.setattr(notice, "BURST_MAX_S", 1.35)
    monkeypatch.setenv("MOUTH_FAKE_SENDMAIL_SLEEP", "5")
    got = []
    for x in _r1(env, 4):                              # at about 0, 0.85, 1.2 and 1.55 s
        got.append(notice.dropped(x, {"reason": "stop", "cut": True}))
        time.sleep(0.35)
    timed_out = "failed: message send timed out after 0.5s"
    # the 4th decision is still within the 1 s gap, but its burst is past the cap:
    # a fresh burst, with a fresh budget, so it tries again
    assert got == [timed_out, "failed: send budget spent", "failed: send budget spent", timed_out]


class _Clock:
    """``notice``'s clock, moved by hand: each fake send takes ``each`` seconds, or its
    timeout if that is shorter (a send cut short by the budget times out)."""

    def __init__(self, each: float) -> None:
        self.t, self.each, self.sends = 1000.0, each, []

    def monotonic(self) -> float:
        return self.t

    def __getattr__(self, name):
        return getattr(time, name)

    def send(self, addr, n, timeout):
        took = min(self.each, timeout)
        self.sends.append((self.t, took, timeout))
        self.t += took
        return f"sent {addr}" if took >= self.each else f"failed: message send timed out after {timeout:.1f}s"


def _burst(monkeypatch, each: float, n: int, gap: float = 0.0) -> tuple:
    """N decisions GAP apart (after each send), through a fresh budget on the clock."""
    clock = _Clock(each)
    monkeypatch.setattr(notice, "time", clock)
    monkeypatch.setattr(notice, "_sendmail", clock.send)
    monkeypatch.setattr(notice, "_BUDGET", notice._Budget())
    got = []
    for _ in range(n):
        got.append(notice._BUDGET.send("s-test@h", None))
        clock.t += gap
    return got, clock


def test_a_burst_of_six_sends_every_notice_at_the_measured_send_time(monkeypatch):
    # EARS-381 F1: `message send` takes about 1.2 s; six notices in one burst all go,
    # each with its whole timeout, not a scrap of the budget
    got, clock = _burst(monkeypatch, 1.2, 6)
    assert got == ["sent s-test@h"] * 6
    assert [timeout for _, _, timeout in clock.sends] == [notice.SEND_TIMEOUT_S] * 6


def test_the_old_budget_dropped_the_fifth_notice_of_a_burst(monkeypatch):
    # the defect F1 found: at 5 s, a burst of 6 sends 4 and drops the rest
    monkeypatch.setattr(notice, "BURST_BUDGET_S", 5.0)
    got, _ = _burst(monkeypatch, 1.2, 6)
    assert got[:4] == ["sent s-test@h"] * 4 and not got[4].startswith("sent")


def test_a_flood_is_still_bounded_by_the_burst_cap(monkeypatch):
    # decisions 0.5 s apart never end a burst (BURST_GAP_S); only the cap does
    assert notice.BURST_BUDGET_S < notice.BURST_MAX_S
    got, clock = _burst(monkeypatch, 1.2, 80, gap=0.5)
    assert "failed: send budget spent" in got
    t0 = clock.sends[0][0]
    first = sum(took for at, took, _ in clock.sends if at < t0 + notice.BURST_MAX_S)
    assert first <= notice.BURST_BUDGET_S + 1e-9                # never more than the budget
    later = [at for at, _, _ in clock.sends if at >= t0 + notice.BURST_MAX_S]
    assert later and later[0] - t0 < notice.BURST_MAX_S + 1.2  # the cap starts a fresh one


def test_a_healthy_flush_sends_every_notice(env):
    got = [notice.dropped(x, {"reason": "stop", "cut": True}) for x in _r1(env, 3)]
    assert got == [f"sent s-test@{HOST}"] * 3 and len(mails(env, "s-test")) == 3


# -- the fences (§2.9 N4) ----------------------------------------------------------

def test_fake_sendmail_refuses_without_its_root(tmp_path):
    e = {k: v for k, v in os.environ.items() if k != "MOUTH_FAKE_MAIL_ROOT"}
    e["HOME"] = str(tmp_path)
    p = subprocess.run([sys.executable, str(FAKE), "send", "ivy@x", "-s", "x", "-f", "-", "--json"],
                       input=b"x\n", env=e, capture_output=True, timeout=10, check=False)
    assert p.returncode == 75
    assert list(tmp_path.iterdir()) == []


def test_the_fence_points_at_the_fake(env):
    assert os.environ["MOUTH_SENDMAIL"].endswith(str(FAKE.name))
    assert Path(os.environ["MOUTH_FAKE_MAIL_ROOT"]) == Path.home() / ".mail" / "agents"


# -- SC2: the live --wait caller (R2) hears it on stderr, and nothing is mailed --------

needs_ffmpeg = pytest.mark.skipif(__import__("shutil").which("ffmpeg") is None, reason="no ffmpeg")


def _tone_wav(path, seconds=0.5, rate=24000):
    import wave

    import numpy as np
    x = (0.3 * np.sin(2 * np.pi * 440 * np.arange(int(seconds * rate)) / rate) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(x.tobytes())
    return path


class _ByThread(io.StringIO):
    """A stream that keeps only this thread's writes: the player thread prints too."""

    def __init__(self, other):
        super().__init__()
        self._me, self._other = threading.get_ident(), other

    def write(self, s):
        return super().write(s) if threading.get_ident() == self._me else self._other.write(s)


def _wait_cli(env, monkeypatch, argv):
    """Run the CLI with a player thread already serving: ensure_player is a no-op,
    so no real subprocess player is spawned (design 5, SC2). Returns (rc, the
    CLI's own stdout, its own stderr)."""
    from mouth.__main__ import main

    monkeypatch.setattr(mouth, "ensure_player", lambda d=None: False)
    out, err = _ByThread(sys.stdout), _ByThread(sys.stderr)
    th = run_player(env.d, idle=1.0)
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = main(argv)
    th.join(5)
    return rc, out.getvalue(), err.getvalue()


def test_sc2_a_waiter_hears_its_drop_on_stderr_and_is_not_mailed(env, monkeypatch):
    absent(monkeypatch)
    mkbox(env, "s-test")                               # a mail would land here, if one were sent
    mkbox(env, "ivy")
    rc, out, err = _wait_cli(env, monkeypatch, ["say", "--wait", "--device", "airpods",
                                                "--backend", "silence", "hello there"])
    assert rc == 1
    rec = json.loads(out)                              # stdout is the said JSON, as today
    assert rec["kind"] == "said" and rec["reason"] == "device-absent" and rec["cut"] is True
    assert rec["notice"] == "waiter"                   # ... plus where the notice went
    assert err == ("mouth: not said, device-absent (output device 'airpods' is not present): "
                   '"hello there"\n')
    assert argvs(env) == [] and mails(env, "s-test") == [] and mails(env, "ivy") == []
    assert said(env.logs)[rec["utt"]]["notice"] == "waiter"
    assert list((env.d / "done").iterdir()) == []      # the lock and the record are both gone


@needs_ffmpeg
def test_sc2_play_wait_names_the_sound(env, monkeypatch, tmp_path):
    absent(monkeypatch)
    wav = _tone_wav(tmp_path / "tone.wav", seconds=0.5)
    rc, out, err = _wait_cli(env, monkeypatch, ["play", "--wait", "--device", "airpods",
                                                "--start", "0.1", "--end", "0.4", str(wav)])
    assert rc == 1 and json.loads(out)["notice"] == "waiter"
    assert err == ('mouth: not said, device-absent (output device \'airpods\' is not present): '
                   '"[sound tone.wav 0.1-0.4s]"\n')
    assert argvs(env) == []


def test_a_wait_that_plays_prints_no_notice(env, monkeypatch):
    rc, out, err = _wait_cli(env, monkeypatch, ["say", "--wait", "--backend", "silence", "hi"])
    assert rc == 0 and json.loads(out)["reason"] == "done" and "notice" not in json.loads(out)
    assert err == ""


def test_a_waiter_that_has_gone_is_mailed(env, monkeypatch, told):
    # a --wait timeout, then the line drops: the caller is mailed like any CLI line
    absent(monkeypatch)
    with pytest.raises(TimeoutError):
        say("too slow for me", device="airpods", wait=True, timeout=0.1)
    assert list((env.d / "done").glob("*.wait")) == []  # released on the way out
    run_player(env.d).join(5)
    (utt, m), = told().items()
    assert m["Mouth-Notice"] == "device-absent"
    assert said(env.logs)[utt]["notice"] == f"sent s-test@{HOST}"


def _sound_out_the_lock(env, seen: dict):
    """Watch done/ for the waiter's lock; once it is RELEASED, write the said record."""
    done = env.d / "done"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        locks = list(done.glob("*.wait")) if done.is_dir() else []
        if locks:
            utt = locks[0].stem
            seen["held"] = notice.waiter({"wait": True, "utt": utt})
            while locks[0].exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            seen["after"] = notice.waiter({"wait": True, "utt": utt})
            rec = {"kind": "said", "utt": utt, "reason": "device-absent", "cut": True}
            (done / f"{utt}.json").write_text(json.dumps(rec))
            return
        time.sleep(0.01)


def test_a_waiter_that_gives_up_still_reads_a_record_that_comes_in_the_grace(env):
    # design 2.9 N1: on a timeout the waiter releases its lock FIRST, then looks
    # once more, for WAIT_GRACE_S, for a drop that found it still there
    seen: dict = {}
    th = threading.Thread(target=_sound_out_the_lock, args=(env, seen), daemon=True)
    th.start()
    rec = say("late", wait=True, timeout=0.2)
    th.join(5)
    assert seen == {"held": True, "after": False}      # held while waiting; released before the grace
    assert rec["reason"] == "device-absent"
    assert list((env.d / "done").iterdir()) == []


def test_a_waiter_with_no_record_in_the_grace_times_out(env, monkeypatch):
    monkeypatch.setattr(mouth, "WAIT_GRACE_S", 0.1)
    t0 = time.monotonic()
    with pytest.raises(TimeoutError):
        say("nobody plays this", wait=True, timeout=0.1)
    assert 0.2 <= time.monotonic() - t0 < 1.0
    assert list((env.d / "done").glob("*.wait")) == []


def test_ctrl_c_releases_then_looks_once_more(env, monkeypatch):
    real, calls = mouth._take_done, []

    def take(done):
        calls.append(notice.waiter({"wait": True, "utt": done.stem}))
        if len(calls) == 1:
            raise KeyboardInterrupt
        return real(done)
    monkeypatch.setattr(mouth, "_take_done", take)
    monkeypatch.setattr(mouth, "WAIT_GRACE_S", 0.1)
    with pytest.raises(KeyboardInterrupt):
        say("interrupted", wait=True, timeout=5)
    assert calls[0] is True and len(calls) > 1 and not any(calls[1:])   # re-checked, unlocked
    assert list((env.d / "done").glob("*.wait")) == []


def test_the_lock_is_released_when_the_drop_fails(env, monkeypatch):
    def broken(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(_box, "drop", broken)
    with pytest.raises(OSError):
        say("never queued", wait=True, timeout=1)
    assert list((env.d / "done").glob("*.wait")) == []


# -- the waiter lock and its probe ----------------------------------------------------

def test_the_waiter_lock_is_exclusive_and_not_inherited(env):
    # N8: a LOCK_SH waiter would grant the probe's LOCK_SH, and read as no waiter
    env.d.mkdir(parents=True)
    fd, p = mouth._wait_lock(env.d, "abc123")
    try:
        assert notice.waiter({"wait": True, "utt": "abc123"}) is True
        assert os.get_inheritable(fd) is False         # a spawned player never holds it
    finally:
        mouth._wait_unlock((fd, p))
    assert not p.exists()
    assert notice.waiter({"wait": True, "utt": "abc123"}) is False


def test_the_probe_looks_only_at_a_line_that_waits(env):
    env.d.mkdir(parents=True)
    lock = mouth._wait_lock(env.d, "abc123")
    try:
        assert notice.waiter({"utt": "abc123"}) is False
        assert notice.waiter({"wait": False, "utt": "abc123"}) is False
    finally:
        mouth._wait_unlock(lock)


def test_an_unlocked_lock_file_is_no_waiter(env):
    # a waiter killed by a signal leaves the file, and the kernel drops its lock
    (env.d / "done").mkdir(parents=True)
    (env.d / "done" / "abc123.wait").write_text("")
    assert notice.waiter({"wait": True, "utt": "abc123"}) is False


def _probe_in_time(item, timeout=2.0):
    got = []
    th = threading.Thread(target=lambda: got.append(notice.waiter(item)), daemon=True)
    th.start()
    th.join(timeout)
    assert not th.is_alive(), "the probe blocked"
    return got[0]


def test_the_probe_never_blocks_on_a_fifo(env):
    (env.d / "done").mkdir(parents=True)
    os.mkfifo(env.d / "done" / "abc123.wait")
    assert _probe_in_time({"wait": True, "utt": "abc123"}) is False


def test_the_probe_never_follows_a_symlink(env, tmp_path):
    (env.d / "done").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    fd, p = mouth._wait_lock(elsewhere, "abc123")      # a held lock, but not ours
    try:
        (env.d / "done" / "abc123.wait").symlink_to(p)
        assert _probe_in_time({"wait": True, "utt": "abc123"}) is False
    finally:
        mouth._wait_unlock((fd, p))


def test_the_probe_never_opens_a_header_chosen_path(env, tmp_path, monkeypatch):
    # N9: the path is derived from the mouth dir and a checked utt, never read from the item
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    lock = mouth._wait_lock(elsewhere, "abc123")
    opened = []
    real_open = os.open
    monkeypatch.setattr(notice.os, "open", lambda path, *a, **kw: opened.append(path) or real_open(path, *a, **kw))
    try:
        assert notice.waiter({"wait": True, "utt": "abc123", "wait_lock": str(lock[1])}) is False
        for bad in ("../elsewhere/done/abc123", "abc/../x", "", None, 7, "a" * 33):
            assert notice.waiter({"wait": True, "utt": bad}) is False
    finally:
        mouth._wait_unlock(lock)
    assert opened == [str(env.d / "done" / "abc123.wait")]


def test_a_mailed_line_claiming_a_wait_is_still_mailed(env, monkeypatch):
    # X-Mouth-Extra can set wait and wait_lock; with no waiter holding the derived path,
    # the sender is mailed as usual
    absent(monkeypatch)
    elsewhere = env.d.parent / "elsewhere"
    elsewhere.mkdir()
    lock = mouth._wait_lock(elsewhere, "abc123")
    try:
        deliver(env, "mine", X_Mouth_Device="airpods",
                X_Mouth_Extra=json.dumps({"wait": True, "wait_lock": str(lock[1])}))
        run_player(env.d).join(5)
    finally:
        mouth._wait_unlock(lock)
    assert len(mails(env, "ivy")) == 1
    assert next(iter(said(env.logs).values()))["notice"] == f"sent ivy@{HOST}"


# -- D11: a refused sender gets a said row, a record only ---------------------------

SPAM = "spam@example.com"


def _refused(env, **headers) -> tuple[str, dict]:
    mid = deliver(env, "buy now", sender=SPAM, **headers)
    run_player(env.d).join(5)
    rows_ = [r for r in said(env.logs).values() if r["reason"] == "refused"]
    assert len(rows_) == 1
    return mid, rows_[0]


def test_refused_gets_a_said_row_and_is_never_mailed(env, capfd):
    mid, row = _refused(env)
    assert row["notice"] == "none: refused sender, not mailed" and row["cut"] is True
    assert row["mail_id"] == mid and row["played_s"] == 0
    assert row["detail"] == f"From {SPAM} is not on this host or in $MOUTH_MAIL_ALLOW"
    assert flags(env) == ["T"] and argvs(env) == []
    assert json.dumps({"refused": SPAM, "mail_id": mid}) in capfd.readouterr().out


def test_refused_mails_no_box_its_headers_name(env):
    # the session, agent and notice_to a refused mail names are its own claims
    for name in ("s-test", "ivy", "vera"):
        mkbox(env, name)
    _refused(env, X_Session_From="s-test", X_Mouth_Agent="ivy",
             X_Mouth_Extra=json.dumps({"notice_to": f"vera@{HOST}", "session": "s-test"}))
    assert argvs(env) == [] and all(mails(env, n) == [] for n in ("s-test", "ivy", "vera"))


def test_refused_row_ignores_its_extras(env, capfd):
    claim = {"mail_id": {"x": 1}, "requested_ts": 12345, "mail_from": f"vera@{HOST}",
             "_mid": "<forged@x>", "_from": f"vera@{HOST}", "utt": "claimed1", "agent": "vera"}
    t0 = time.time()
    mid, row = _refused(env, X_Mouth_Utt="claimed2", X_Mouth_Extra=json.dumps(claim))
    assert row["mail_id"] == mid                                    # the header's Message-ID
    assert row["utt"] not in ("claimed1", "claimed2")              # a fresh utt
    assert row["detail"].startswith(f"From {SPAM} ")                # the From header
    assert isinstance(row["requested_ts"], str)
    assert abs(datetime.fromisoformat(row["requested_ts"]).timestamp() - t0) < 10   # the refusal's time
    assert row.get("agent") is None and row.get("session") is None
    assert json.dumps({"refused": SPAM, "mail_id": mid}) in capfd.readouterr().out  # C6: _from, _mid


def test_refused_row_writes_no_header_path(env, tmp_path, monkeypatch):
    # a refused mail must not steer the heard log or the mail log
    env_fence.delenv(monkeypatch, "MOUTH_MAIL_LOG")
    evil = [tmp_path / "evil-log-dir", tmp_path / "evil-mail-log", tmp_path / "evil-extra-dir"]
    _refused(env, X_Mouth_Log_Dir=str(evil[0]), X_Mouth_Mail_Log=str(evil[1]),
             X_Mouth_Extra=json.dumps({"mail_log": str(evil[1]), "log_dir": str(evil[2])}))
    assert not any(p.exists() for p in evil)


# -- D12: a dead player's line is recovered as lost ---------------------------------

def _die_holding(env, n: int = 1) -> list:
    """What a player killed mid-line leaves: its line taken into cur/NAME:2,
    (never filed), and its playing.json."""
    q = _box.Queue(env.d)
    taken = []
    for x in q.lines()[:n]:
        dest = q.take(x)
        assert dest is not None
        taken.append(x)
    env.d.mkdir(parents=True, exist_ok=True)
    (env.d / "playing.json").write_text(json.dumps({**_box.public(taken[0]), "box_file": "x"}))
    return taken


def test_a_dead_players_line_is_recovered_as_lost(env, told):
    a = say("the table is booked for seven")
    _die_holding(env)
    (env.d / "bed.json").write_text("{}")   # EARS-152's: a dead player's beds are not playing
    run_player(env.d).join(5)
    row = said(env.logs)[a["utt"]]
    assert row["reason"] == "lost" and row["cut"] is True
    assert "the player stopped before it filed this line" in row["detail"]
    assert row["notice"] == f"sent s-test@{HOST}"
    m = told()[a["utt"]]
    assert m["Mouth-Notice"] == "lost" and "heard:  unknown" in m.get_content()
    assert flags(env) == ["T"]
    # the lock holder clears both: the dead player's line and its beds (bed.py, EARS-152)
    assert not (env.d / "playing.json").exists() and not (env.d / "bed.json").exists()


def test_a_dead_players_mailed_line_is_a_reply(env):
    mkbox(env, "ivy")
    mid = deliver(env, "the table is booked")
    _die_holding(env)
    run_player(env.d).join(5)
    (m,) = mails(env, "ivy")
    assert m["Mouth-Notice"] == "lost" and m["In-Reply-To"] == mid


def test_recovered_line_reaches_its_waiter(env):
    # the CLI waiter outlived its player: the next player's recovery hands it 'lost'
    got: dict = {}
    th = threading.Thread(target=lambda: got.update(rec=say("are you there", wait=True, timeout=10)),
                          daemon=True)
    th.start()
    deadline = time.monotonic() + 5
    while not list((env.q / "new").iterdir()) and time.monotonic() < deadline:
        time.sleep(0.01)
    _die_holding(env)
    run_player(env.d).join(5)
    th.join(5)
    rec = got["rec"]
    assert rec["reason"] == "lost" and rec["notice"] == "waiter"
    assert argvs(env) == []


def test_an_old_recovered_line_is_recorded_not_mailed(env):
    mkbox(env, "ivy")
    mid = deliver(env, "from two hours ago", X_Mouth_Requested=str(time.time() - 7200))
    _die_holding(env)
    run_player(env.d).join(5)
    (row,) = [r for r in said(env.logs).values() if r["mail_id"] == mid]
    assert row["reason"] == "lost"
    assert row["notice"] == "none: recovered 2 h after it was asked, too old to mail"
    assert mails(env, "ivy") == [] and argvs(env) == []


def test_recover_survives_a_bad_mail(env, capfd, monkeypatch):
    # box.read raises on one mail; the file is filed T, the rest go on. No header makes
    # read() raise now (X-Mouth-Requested: nan, then X-Mouth-Priority: nan, were the
    # vehicle until EARS-168 read them as absent), so read raises on this one file.
    deliver(env, "unreadable")
    bad = next((env.q / "new").iterdir())
    real = _box.read

    def read(path, d=None, **kw):
        if path.name.startswith(bad.name):    # in new/, or in cur/ as <name>:2,
            raise ValueError("cannot convert float NaN to integer")
        return real(path, d, **kw)

    monkeypatch.setattr(_box, "read", read)
    with pytest.raises(ValueError):
        _box.read(bad)
    bad.rename(env.q / "cur" / f"{bad.name}:2,")
    good = say("still here")
    _die_holding(env)
    later = say("and after")
    run_player(env.d).join(5)
    out = capfd.readouterr().out
    assert '"recover_failed"' in out
    rows_ = said(env.logs)
    assert rows_[good["utt"]]["reason"] == "lost" and rows_[later["utt"]]["reason"] == "done"
    assert sorted(flags(env)) == ["S", "T", "T"]


def test_recover_runs_once_per_queue_and_only_for_the_player(env, monkeypatch):
    calls = []
    monkeypatch.setattr(player, "recover", lambda d, q: calls.append(q) or 0)
    q = _box.Queue(env.d)
    q.lines()
    q.lines(act=False)
    assert calls == []                                     # a reader never recovers
    q.lines(act=True)
    q.lines(act=True)
    assert calls == [q]                                    # the player, once per Queue


def test_recover_leaves_filed_lines_alone(env):
    say("played")
    run_player(env.d).join(5)
    before = sorted(p.name for p in (env.q / "cur").iterdir())
    assert player.recover(env.d, _box.Queue(env.d)) == 0
    assert sorted(p.name for p in (env.q / "cur").iterdir()) == before


def test_stale_wait_files_are_swept(env):
    # N16: an old, unlocked .wait is a killed waiter's; a held one is live, whatever its age
    done = env.d / "done"
    done.mkdir(parents=True)
    old = time.time() - player.STALE_WAIT_S - 60
    dead, young = done / "dead1.wait", done / "young1.wait"
    dead.write_text("")
    young.write_text("")
    os.utime(dead, (old, old))
    fd, held = mouth._wait_lock(env.d, "held1")
    os.utime(held, (old, old))
    (done / "keep1.json").write_text("{}")
    try:
        player.recover(env.d, _box.Queue(env.d))
        assert not dead.exists() and young.exists() and held.exists() and (done / "keep1.json").exists()
        assert notice.waiter({"wait": True, "utt": "held1"}) is True
    finally:
        mouth._wait_unlock((fd, held))


def test_a_lost_line_of_no_known_age_is_not_mailed(env):
    mkbox(env, "s-test")
    for t in ("nan", float("inf"), None, "x"):
        item = {"utt": "u1", "text": "x", "session": "s-test", "requested_t": t}
        assert notice.dropped(item, {"reason": "lost", "cut": True}) == \
            "none: recovered an unknown time after it was asked, too old to mail"
    young = {"utt": "u2", "text": "x", "session": "s-test", "requested_t": time.time() - 60}
    assert notice.dropped(young, {"reason": "lost", "cut": True}) == f"sent s-test@{HOST}"


# -- EARS-346: the retracting CLI is the service --------------------------------------

def test_a_cli_retract_by_another_still_uses_the_default_message(env, monkeypatch, told):
    # EARS-346: `mouth retract` writes a queued line's said row, and so its notice, in
    # the retracting CLI's own process, with no $MOUTH_SENDMAIL there. The
    # verb marks its process as the service, so the notice still goes through the
    # default (here the recording fake), as it did before the fence.
    import shlex

    from mouth import __main__ as cli

    env_fence.delenv(monkeypatch, "MOUTH_SENDMAIL")
    monkeypatch.setattr(notice, "DEFAULT_SENDMAIL", shlex.join([sys.executable, str(FAKE)]))
    monkeypatch.setattr(notice, "_SERVICE", None)
    _, b = say(LONG), say("never mind")
    th = run_player(env.d)
    time.sleep(0.3)
    monkeypatch.setenv("VOICEMODE_SESSION_ID", "s-other")
    with contextlib.redirect_stdout(io.StringIO()):
        assert cli.main(["retract", b["utt"]]) == 0
    th.join(8)
    mouth.stop()
    assert said(env.logs)[b["utt"]]["notice"] == f"sent s-test@{HOST}"
    assert told()[b["utt"]]["Mouth-Notice"] == "retracted"
