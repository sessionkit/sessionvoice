"""An alert line: the host's own line, never a caller's (``mouth say --alert``).

It is what a part of sessionvoice says when its mail fails, so it must never
feed what it reports. Whoever runs it, no seat is named on it: ``say()``
returns it with no agent, session or sender, and the box writes the mouth's
own (``mouth``, ``mouth@<host>``), as for any line with no agent. It reads no
caller's cascade, its interest is zero, it goes to the front of the queue
unless a priority is given, and it is never refused for length. Its saying and
said rows carry ``alert: true``, and so does the rest a barge-in's replay
queues again of it (``player._resume``), so the keeper can let no turn answer
either. No drop notice is ever mailed about it.

Nothing here reaches the MTA (the conftest fence), a live mouth, or a real
player: the env fixture is test_mouth_notice's, a tmp mouth dir with the
``silence`` backend and the ``null`` device. ``say`` there queues with
``spawn=False``; the CLI runs in-process with the player lock held, so its
``ensure_player`` finds a player and starts none.
"""

from __future__ import annotations

import contextlib
import email
import email.policy
import fcntl
import io
import json
import os
import socket
import stat
import time

import pytest

import mouth
from mouth import box as _box
from mouth import hold, notice, player
from test_mouth_notice import env, mkbox, rows, run_player, say, sends  # noqa: F401  (fixtures)

HOST = socket.gethostname().split(".")[0].lower()
TEXT = ("Turn mail is failing: 3 in a row did not go out, so agents are not getting your "
        "turns. The ears-mail log says why.")
#: A barge-in cut one second in, as player._said hands it to _plan.
CUT = {"reason": "barge-in", "cut": True, "played_s": 1.0, "cut_at_s": 1.0,
       "text_played_est": "Turn mail is failing: 3 in a row", "underruns": 0}


def queued(env) -> list[dict]:
    """The queue's lines as the player reads them back from the box."""
    return [_box.public(x) for x in _box.Queue(env.d).lines()]


@contextlib.contextmanager
def player_lock(d):
    """A player holds ``player.lock``, so ``ensure_player`` starts none."""
    d.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(d / "player.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        yield
    finally:
        os.close(fd)


def cli(env, monkeypatch, *args) -> tuple[int, str, str, list]:
    """``mouth say ARGS`` in-process, the player lock held: (rc, stdout, stderr,
    what each ``ensure_player`` call returned)."""
    from mouth.__main__ import main

    real, started = mouth.ensure_player, []
    monkeypatch.setattr(mouth, "ensure_player", lambda d=None: started.append(real(d)) or started[-1])
    out, err = io.StringIO(), io.StringIO()
    with player_lock(env.d), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = main(["say", "--device", "null", "--backend", "silence", *args])
    return rc, out.getvalue(), err.getvalue(), started


@pytest.fixture
def caller(env, monkeypatch):
    """A seat's shell: its session and agent under every name the mouth reads."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "11111111-2222-3333-4444-555555555555")
    monkeypatch.setenv("VOICEMODE_AGENT", "ivy")
    monkeypatch.setenv("VOICEMODE_SESSION_ID", "s-test")
    return env


# -- the line: no caller on it --------------------------------------------------------

def test_say_alert_returns_the_line_with_no_caller_on_it(caller):
    item = say(TEXT, alert=True)
    assert item["alert"] is True
    assert item["agent"] is None and item["session"] is None and item["mail_from"] is None
    assert item["interest"] == "zero" and item["priority"] == "next"


def test_read_back_its_agent_and_from_are_the_mouths_own_never_the_callers(caller):
    utt = say(TEXT, alert=True)["utt"]
    (line,) = queued(caller)
    assert line["utt"] == utt and line["text"] == TEXT
    assert line["alert"] is True
    assert line["session"] is None
    assert line["agent"] == "mouth" and line["mail_from"] == f"mouth@{HOST}"
    assert line["interest"] == "zero" and line["priority"] == "next"
    m = email.message_from_bytes(next((caller.q / "new").iterdir()).read_bytes(),
                                 policy=email.policy.default)
    assert m["X-Session-From"] is None
    assert m["X-Mouth-Agent"] == "mouth" and m["From"] == f"mouth@{HOST}"
    assert json.loads(m["X-Mouth-Extra"])["alert"] is True


def test_a_line_that_is_not_an_alert_line_carries_no_alert_key(caller):
    item = say("hello there")
    assert "alert" not in item
    (line,) = queued(caller)
    assert "alert" not in line
    assert line["session"] == "s-test" and line["agent"] == "ivy"   # the caller, as before


def test_a_priority_given_is_kept(caller):
    say(TEXT, alert=True, priority=30)
    (line,) = queued(caller)
    assert line["priority"] == 30


def test_an_interest_other_than_zero_is_refused_and_nothing_is_queued(caller):
    with pytest.raises(ValueError, match="an alert line's interest is zero"):
        say(TEXT, alert=True, interest="high")
    assert queued(caller) == []
    say(TEXT, alert=True, interest="zero")
    assert [x["interest"] for x in queued(caller)] == ["zero"]


def test_a_line_over_say_max_chars_is_not_refused(caller):
    long = "word " * 80                                  # 400 characters
    say(long.strip(), alert=True, cap=True)
    assert [len(x["text"]) for x in queued(caller)] == [399]


def test_no_callers_cascade_is_read(caller, monkeypatch, tmp_path):
    """A fake `session` that marks each run: the alert line never runs it. A plain
    line from the same shell does, so the fake is one the mouth would run."""
    ran = tmp_path / "session-ran"
    fake = tmp_path / "session"
    fake.write_text(f"#!/bin/sh\necho run >> '{ran}'\necho '{{}}'\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("MOUTH_SESSION_BIN", str(fake))
    say(TEXT, alert=True)
    assert not ran.exists()
    say("hello there")                                   # the control
    assert ran.read_text().splitlines() == ["run"]


# -- the CLI: mouth say --alert -----------------------------------------------------

def test_mouth_say_alert_queues_an_alert_line_and_prints_its_utt(caller, monkeypatch):
    rc, out, err, started = cli(caller, monkeypatch, "--alert", TEXT)
    assert rc == 0 and err == ""
    (line,) = queued(caller)
    assert out == line["utt"] + "\n"                     # the last line of stdout is the utt
    assert line["alert"] is True and line["session"] is None and line["agent"] == "mouth"
    assert line["mail_from"] == f"mouth@{HOST}"          # the mouth's own From, not the caller's
    assert line["interest"] == "zero" and line["priority"] == "next"
    assert started == [False]                            # the lock was held: no player started


def test_mouth_say_alert_is_never_refused_for_length(caller, monkeypatch):
    rc, _, err, _ = cli(caller, monkeypatch, "--alert", "x" * 400)
    assert rc == 0 and err == ""
    assert [len(x["text"]) for x in queued(caller)] == [400]


def test_mouth_say_alert_with_another_interest_exits_2_and_queues_nothing(caller, monkeypatch):
    rc, out, err, _ = cli(caller, monkeypatch, "--alert", "--interest", "high", TEXT)
    assert rc == 2 and out == ""
    assert err == "mouth: an alert line's interest is zero\n"
    assert queued(caller) == []


def test_mouth_say_without_alert_is_unchanged(caller, monkeypatch):
    rc, _, _, _ = cli(caller, monkeypatch, "hello there")
    assert rc == 0
    (line,) = queued(caller)
    assert "alert" not in line and line["agent"] == "ivy" and line["interest"] is None


# -- its rows: saying and said carry alert ----------------------------------------------

def test_its_saying_and_said_rows_carry_alert_and_a_plain_lines_do_not(caller):
    """Played by the player, in-process: short lines, so the silence plays quickly."""
    a = say("Mail is failing.", alert=True)["utt"]
    b = say("hello there")["utt"]
    th = run_player(caller.d, idle=0.3)
    th.join(30)
    assert not th.is_alive()
    got = {(r["kind"], r["utt"]): r for r in rows(caller.logs) if r["kind"] in ("saying", "said")}
    assert got[("saying", a)]["alert"] is True and got[("said", a)]["alert"] is True
    assert got[("said", a)]["agent"] == "mouth" and "session" not in got[("said", a)]
    assert "alert" not in got[("saying", b)] and "alert" not in got[("said", b)]


def test_an_alert_line_dropped_unplayed_is_never_mailed_about(caller):
    """The said row of an alert line that never played (it expired) carries alert, and
    its notice is the quiet one: no send, though the caller's boxes exist."""
    mkbox(caller, "s-test")
    mkbox(caller, "ivy")
    say(TEXT, alert=True)
    (line,) = _box.Queue(caller.d).lines()
    rec = player.said_unplayed(line, "expired")
    assert rec["alert"] is True and rec["cut"] is True
    assert rec["notice"] == notice.ALERT_QUIET == "none: an alert line, never mailed"
    assert sends(caller) == []


# -- notice.dropped: never mailed ------------------------------------------------------

def test_dropped_on_an_alert_item_is_quiet_and_the_fake_sendmail_is_never_called(caller):
    mkbox(caller, "s-test")
    mkbox(caller, "ivy")
    item = {"utt": "a1e470000001", "text": TEXT, "session": "s-test", "agent": "ivy",
            "alert": True, "requested_t": time.time()}
    assert notice.dropped(item, CUT) == "none: an alert line, never mailed"
    assert sends(caller) == []
    # the control: the same line, not an alert line, is mailed to its session's box
    plain = {k: v for k, v in item.items() if k != "alert"}
    assert notice.dropped(plain, CUT) == f"sent s-test@{HOST}"
    assert len(sends(caller)) == 1


def test_only_a_true_alert_counts(caller):
    """A mailed line's X-Mouth-Extra can set any value: only JSON true is an alert line."""
    mkbox(caller, "s-test")
    item = {"utt": "a1e470000002", "text": TEXT, "session": "s-test", "agent": "ivy",
            "alert": "yes", "requested_t": time.time()}
    assert notice.dropped(item, CUT) == f"sent s-test@{HOST}"
    assert player._common(item)["alert"] is None         # dropped from the row, as None is


def test_a_refused_line_stays_refused_its_extra_unread(caller):
    item = {"utt": "a1e470000003", "text": TEXT, "alert": True}
    assert notice.dropped(item, {"reason": "refused", "cut": True}) == \
        "none: refused sender, not mailed"


# -- the fast follow: a cut alert line's rest is an alert line too ------------------------

@pytest.fixture
def resume_on(monkeypatch):
    """Replay on (5 s), and no partial of the user's asks for quiet."""
    monkeypatch.setenv("MOUTH_RESUME_S", "5")
    monkeypatch.setattr(hold, "asked_for_quiet", lambda *a, **kw: None)


def _cut_and_resumed(env, **kw) -> tuple[dict, dict]:
    """Queue a line, cut it by the user's words, let the player's fast follow re-queue
    its rest; (the line as the player read it, its rest read back)."""
    say(TEXT, **kw)
    (line,) = _box.Queue(env.d).lines()
    rec = {**CUT, **player._plan(line, CUT)}
    assert rec["resume"] == "will"
    player._resume(env.d, line, rec)
    (rest,) = [x for x in queued(env) if x.get("resume_of") == line["utt"]]
    return line, rest


def test_a_barge_in_cut_of_an_alert_line_queues_its_rest_as_an_alert_line(caller, resume_on):
    line, rest = _cut_and_resumed(caller, alert=True)
    assert line["alert"] is True
    assert rest["alert"] is True
    assert rest["session"] is None and rest["agent"] == "mouth"
    assert rest["interest"] == "zero"
    assert rest["text"] == TEXT                          # cut in its first sentence: all of it


def test_a_cut_lines_rest_carries_no_alert_key(caller, resume_on):
    _, rest = _cut_and_resumed(caller)
    assert "alert" not in rest


def test_the_rests_drop_is_never_mailed_about_either(caller, resume_on):
    mkbox(caller, "s-test")
    _, rest = _cut_and_resumed(caller, alert=True)
    assert notice.dropped(rest, {"reason": "expired", "cut": True, "played_s": 0.0}) == \
        notice.ALERT_QUIET
    assert sends(caller) == []
