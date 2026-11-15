"""The mouth says it when its notices fail, once, and once when they go out again (EARS-391).

At the Nth notice in a row that fails (``notice_alert_after`` in mouth.json, default 3)
the mouth queues ONE alert line (``say(..., alert=True)``) and writes one ``mouth:
ERROR: notice alert:`` line; then it is quiet until a notice goes out, and says once
that notices go out again. ``notice_alert`` ``off`` turns it off. The run is kept in
``<mouth dir>/notice-alert.json``, so it spans processes. A fenced process and a spent
budget try no send, so they count nothing.

Notices go through the tests/mouth fake ``message send``; ``$MOUTH_FAKE_SENDMAIL_EXIT``
makes it fail (kind ``mta``, writing nothing). Most tests stub ``notice._say_alert`` to
record the text; the ones that run the real ``say(alert=True)`` hold the player lock, so
no player starts. The env fence turns the alert off for every other test; these set it
back. No real mail, no real mouth, no clock.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from mouth import box as _box
from mouth import notice
from tests import env_fence
from test_mouth_alert_line import player_lock
from test_mouth_notice import HOST, env, mkbox, sends  # noqa: F401  (fixtures)

ALERT3 = ("The mouth's notices are failing: 3 in a row did not go out, so agents are not "
          "told when their lines are cut or dropped. The mouth's log says why.")
RECOVERED = "The mouth's notices are going out again."
#: A drop, as player._said hands it to dropped().
CUT = {"reason": "barge-in", "cut": True, "played_s": 1.0, "cut_at_s": 1.0,
       "text_played_est": "one two", "underruns": 0}
#: The real speaker, before the fixture stubs it.
_REAL_SAY_ALERT = notice._say_alert
ITEM = {"utt": "u1", "text": "Building it now, three steps to go.", "session": "s-test"}


@pytest.fixture
def m(env, monkeypatch, tmp_path):
    """The mouth with the alert on, its notices To s-test's box, a fresh and endless
    burst budget, and a stub speaker. m.notify(ok) sends one notice that goes out (ok) or
    fails; m.said holds each text the stub was asked to say."""
    mkbox(env, "s-test")
    monkeypatch.setenv("MOUTH_NOTICE_ALERT", "speak")   # the fence turns it off
    env_fence.delenv(monkeypatch, "MOUTH_NOTICE_ALERT_AFTER")
    monkeypatch.setenv("MOUTH_SETTINGS", str(tmp_path / "mouth.json"))  # absent unless written
    monkeypatch.setattr(notice, "_BUDGET", notice._Budget())
    monkeypatch.setattr(notice, "BURST_BUDGET_S", 1e9)  # many sends in one burst
    said: list = []

    def stub(text):
        said.append(text)
        return True, f"a1e7{len(said) - 1:08x}"

    monkeypatch.setattr(notice, "_say_alert", stub)

    def notify(ok):
        if ok:
            monkeypatch.delenv("MOUTH_FAKE_SENDMAIL_EXIT", raising=False)
        else:
            monkeypatch.setenv("MOUTH_FAKE_SENDMAIL_EXIT", "75")
        return notice.dropped(dict(ITEM), dict(CUT))

    env.notify, env.said, env.mp = notify, said, monkeypatch
    env.state = env.d / notice.ALERT_FILE
    env.settings = tmp_path / "mouth.json"
    return env


def run(m, plan):
    """One notice per character: ``f`` fails, ``.`` goes out."""
    return [m.notify(c == ".") for c in plan]


def alerts(err: str) -> list:
    return [x for x in err.splitlines()
            if x.startswith(("mouth: ERROR: notice alert:", "mouth: notices recovered:"))]


def state(m) -> dict:
    return json.loads(m.state.read_text())


# -- criterion 1: once at N, then quiet ------------------------------------------------

def test_two_failures_say_nothing_the_third_says_it_once_and_the_next_three_nothing(m, capsys):
    outs = run(m, "ff")
    assert all(o.startswith("failed: message send exit 75 kind mta") for o in outs)
    assert m.said == [] and alerts(capsys.readouterr().err) == []
    run(m, "f")
    err = capsys.readouterr().err
    assert m.said == [ALERT3]
    assert alerts(err) == ["mouth: ERROR: notice alert: 3 notices in a row failed; "
                           "queued for the mouth (utt a1e700000000)"]
    # today's per-notice ERROR line stays, and the alert follows it
    lines = [x for x in err.splitlines() if x.startswith("mouth: ERROR:")]
    assert lines[0].startswith(f"mouth: ERROR: notice to s-test@{HOST} failed: message send exit 75")
    assert lines[1].startswith("mouth: ERROR: notice alert:")
    run(m, "fff")
    assert m.said == [ALERT3] and alerts(capsys.readouterr().err) == []
    assert state(m) == {"failed": 6, "alerted": True}


# -- criterion 2: once when they go out again, then a new run alerts again -----------------

def test_a_notice_that_goes_out_after_the_alert_says_so_once_and_a_new_run_alerts(m, capsys):
    run(m, "fff")
    capsys.readouterr()
    assert run(m, ".") == [f"sent s-test@{HOST}"]
    assert m.said == [ALERT3, RECOVERED]
    assert alerts(capsys.readouterr().err) == [
        "mouth: notices recovered: one went out after 3 in a row failed; "
        "queued for the mouth (utt a1e700000001)"]
    run(m, ".fff")
    assert m.said == [ALERT3, RECOVERED, ALERT3]


def test_the_recovery_names_the_runs_whole_count(m, capsys):
    run(m, "fffff.")
    assert alerts(capsys.readouterr().err)[-1].startswith(
        "mouth: notices recovered: one went out after 5 in a row failed")


# -- criterion 3: a notice out resets the run ------------------------------------------------

def test_two_failures_a_notice_out_and_two_failures_say_nothing(m, capsys):
    run(m, "ff.ff")
    assert m.said == [] and alerts(capsys.readouterr().err) == []
    assert state(m) == {"failed": 2, "alerted": False}


# -- criterion 4: the alert never reaches the sender ----------------------------------------

def test_the_sendmail_log_holds_exactly_the_notices(m):
    run(m, "ffff..")
    assert m.said == [ALERT3, RECOVERED]
    got = sends(m)
    assert len(got) == 6                                 # one `message send` per notice, no more
    assert all(s["argv"][:2] == ["send", f"s-test@{HOST}"] for s in got)
    assert not [s for s in got if any("failing" in a or "going out" in a for a in s["argv"])]


# -- criterion 6: settings ----------------------------------------------------------------

def test_with_no_settings_it_speaks_at_three():
    assert notice.notice_alert_setting({}, settings={}) == ("speak", 3)


def test_the_files_after_moves_the_alert_to_the_fifth_failure(m):
    m.settings.write_text(json.dumps({"notice_alert_after": 5}))
    run(m, "ffff")
    assert m.said == []
    run(m, "f")
    assert m.said == [ALERT3.replace("3 in a row", "5 in a row")]


def test_the_variable_after_beats_the_files(m):
    m.settings.write_text(json.dumps({"notice_alert_after": 5}))
    m.mp.setenv("MOUTH_NOTICE_ALERT_AFTER", "2")
    run(m, "ff")
    assert m.said == [ALERT3.replace("3 in a row", "2 in a row")]


def test_the_files_off_says_nothing_and_makes_no_file(m, capsys):
    m.settings.write_text(json.dumps({"notice_alert": "off"}))
    m.mp.delenv("MOUTH_NOTICE_ALERT")
    run(m, "fffff")
    assert m.said == [] and alerts(capsys.readouterr().err) == []
    assert not m.state.exists()


def test_the_variables_off_beats_the_files_speak(m):
    m.settings.write_text(json.dumps({"notice_alert": "speak"}))
    m.mp.setenv("MOUTH_NOTICE_ALERT", "off")
    run(m, "fffff")
    assert m.said == [] and not m.state.exists()


def test_off_resets_an_old_file_with_one_write(m):
    m.d.mkdir(parents=True, exist_ok=True)
    m.state.write_text(json.dumps({"failed": 7, "alerted": True}))
    m.mp.setenv("MOUTH_NOTICE_ALERT", "off")
    run(m, "f")
    assert state(m) == {"failed": 0, "alerted": False}
    m.mp.setenv("MOUTH_NOTICE_ALERT", "speak")
    run(m, ".")                                         # an old alert gets no late recovery
    assert m.said == []


def test_unset_it_speaks_by_default(m):
    m.mp.delenv("MOUTH_NOTICE_ALERT")
    run(m, "fff")
    assert m.said == [ALERT3]


@pytest.mark.parametrize("value", ["loud", 0, "x", True, None, ["speak"], "SPEAK"])
def test_an_invalid_channel_in_the_file_falls_to_the_default(value):
    assert notice.notice_alert_setting({}, settings={"notice_alert": value})[0] == "speak"


@pytest.mark.parametrize("value", ["loud", 0, "x", True, -1, 2.5, "3", None])
def test_an_invalid_after_in_the_file_falls_to_the_default(value):
    assert notice.notice_alert_setting({}, settings={"notice_alert_after": value})[1] == 3


@pytest.mark.parametrize("value", ["loud", "", "0", "x", "true", "-1", "2.5", "٣"])
def test_an_invalid_variable_falls_to_the_file(value):
    env = {"MOUTH_NOTICE_ALERT": value, "MOUTH_NOTICE_ALERT_AFTER": value}
    got = notice.notice_alert_setting(env, settings={"notice_alert": "off",
                                                     "notice_alert_after": 5})
    assert got == ("off", 5)


def test_the_variables_any_case_trimmed_are_read():
    env = {"MOUTH_NOTICE_ALERT": "  OFF ", "MOUTH_NOTICE_ALERT_AFTER": " 4 "}
    assert notice.notice_alert_setting(env, settings={}) == ("off", 4)


@pytest.mark.parametrize("body", ["not json", "[1, 2]", '"speak"'])
def test_a_settings_file_that_is_not_a_json_object_is_no_layer(m, body):
    m.settings.write_text(body)
    assert notice.notice_alert_setting() == ("speak", 3)


def test_the_file_is_the_mouths_settings_file(m):
    m.settings.write_text(json.dumps({"notice_alert": "off", "notice_alert_after": 9}))
    m.mp.delenv("MOUTH_NOTICE_ALERT")
    assert notice.notice_alert_setting() == ("off", 9)


# -- persistence: one file, every process --------------------------------------------------

def test_the_run_is_one_file_that_a_second_process_carries_on(m):
    """F F here; the third F in a second process (the real say, the player lock held, so
    it starts no player): one alert, queued in the shared queue."""
    run(m, "ff")
    assert state(m) == {"failed": 2, "alerted": False}
    assert os.stat(m.state).st_mode & 0o777 == 0o600
    code = ("import json, sys\n"
            "from mouth import notice\n"
            "notice.BURST_BUDGET_S = 1e9\n"
            f"print(notice.dropped({ITEM!r}, {CUT!r}))\n")
    e = dict(os.environ, MOUTH_FAKE_SENDMAIL_EXIT="75")
    with player_lock(m.d):
        p = subprocess.run([sys.executable, "-c", code], env=e, capture_output=True,
                           text=True, timeout=120)
    assert p.returncode == 0, p.stderr
    (line,) = alerts(p.stderr)
    assert line.startswith("mouth: ERROR: notice alert: 3 notices in a row failed; "
                           "queued for the mouth (utt ")
    assert state(m) == {"failed": 3, "alerted": True}
    (q,) = [_box.public(x) for x in _box.Queue(m.d).lines()]
    assert q["text"] == ALERT3 and q["alert"] is True and q["session"] is None
    assert m.said == []                                  # the stub was this process's only
    run(m, "f")                                          # back here: alerted, so quiet
    assert m.said == []


@pytest.mark.parametrize("body", ["", "not json", "[]", '{"failed": "2", "alerted": false}',
                                  '{"failed": true, "alerted": false}',
                                  '{"failed": 2, "alerted": "yes"}'])
def test_a_file_that_cannot_be_read_starts_a_fresh_run(m, body):
    m.d.mkdir(parents=True, exist_ok=True)
    m.state.write_text(body)
    run(m, "ff")
    assert m.said == [] and state(m) == {"failed": 2, "alerted": False}


def test_a_run_that_cannot_be_kept_never_stops_a_notice(m):
    """A mouth folder the file cannot be made in: the notice's outcome is still returned."""
    m.d.mkdir(parents=True, exist_ok=True)
    m.state.mkdir()                                      # a directory where the file goes
    assert run(m, "fff")[-1].startswith("failed: message send exit 75")
    assert run(m, ".") == [f"sent s-test@{HOST}"]
    assert m.said == []


# -- the speaker fails ----------------------------------------------------------------------

def test_a_line_that_could_not_be_queued_is_named_and_not_tried_again(m, capsys):
    tried: list = []
    m.mp.setattr(notice, "_say_alert", lambda text: tried.append(text) or (False, "OSError: x"))
    run(m, "fff")
    assert alerts(capsys.readouterr().err) == [
        "mouth: ERROR: notice alert: 3 notices in a row failed; not said: OSError: x"]
    assert state(m)["alerted"] is True
    run(m, "fff")
    assert tried == [ALERT3]


def test_the_real_speaker_never_raises():
    """_say_alert turns any failure of say() into (False, why)."""
    import mouth
    real = mouth.say
    try:
        def boom(*a, **k):
            raise RuntimeError("the queue is\nnot there")
        mouth.say = boom
        assert notice._say_alert("x") == (False, "RuntimeError: the queue is not there")
    finally:
        mouth.say = real


# -- outcomes the mouth does not count ---------------------------------------------------

def test_a_fenced_process_counts_nothing(m, capsys):
    env_fence.delenv(m.mp, "MOUTH_SENDMAIL")
    m.mp.setattr(notice, "_SERVICE", None)
    outs = run(m, "fffff")
    assert all(o.startswith("failed: message send fenced") for o in outs)
    assert m.said == [] and not m.state.exists()
    assert alerts(capsys.readouterr().err) == []


def test_a_spent_budget_counts_nothing(m):
    run(m, "ff")
    m.mp.setattr(notice, "BURST_BUDGET_S", 0.0)
    m.mp.setattr(notice, "_BUDGET", notice._Budget())
    assert run(m, "fff") == ["failed: send budget spent"] * 3
    assert state(m) == {"failed": 2, "alerted": False} and m.said == []
    m.mp.setattr(notice, "BURST_BUDGET_S", 1e9)
    m.mp.setattr(notice, "_BUDGET", notice._Budget())
    run(m, "f")
    assert m.said == [ALERT3]


def test_quiet_decisions_count_nothing(m):
    """A refused line, an alert line, a self-retract, NOTICE=off: no send, no count."""
    for item, rec in (({**ITEM}, {"reason": "refused", "cut": True}),
                      ({**ITEM, "alert": True}, CUT),
                      ({**ITEM, "retracted_by": "s-test"}, {**CUT, "reason": "retracted"})):
        for _ in range(4):
            assert notice.dropped(item, rec).startswith("none:")
    m.mp.setenv("MOUTH_NOTICE", "off")
    for _ in range(4):
        assert notice.dropped(dict(ITEM), CUT) == "none: off"
    assert not m.state.exists() and m.said == []


def test_the_again_note_counts_too(m):
    """A say-again's note goes through the same send, so it is counted the same."""
    m.mp.setenv("MOUTH_FAKE_SENDMAIL_EXIT", "75")
    for _ in range(3):
        notice.again({**ITEM, "again_of": "u0"}, {"cut": False})
    assert m.said == [ALERT3]


# -- no deadlock: the real say from inside _Budget.send -------------------------------------

def test_an_alert_raised_inside_the_budget_lock_returns(m):
    """The real _say_alert runs under _Budget.send's lock; say() never touches the
    budget, so the notice returns. Run in a thread so a deadlock fails, not hangs."""
    m.mp.setattr(notice, "_say_alert", _REAL_SAY_ALERT)
    outs: list = []
    with player_lock(m.d):
        t = threading.Thread(target=lambda: outs.extend(run(m, "fff.")), daemon=True)
        t.start()
        t.join(120)
        assert not t.is_alive(), "a notice raised inside _Budget.send did not return"
    assert outs[-1] == f"sent s-test@{HOST}"
    q = [_box.public(x) for x in _box.Queue(m.d).lines()]
    assert [x["text"] for x in q] == [ALERT3, RECOVERED]
    assert all(x["alert"] is True and x["session"] is None and x["interest"] == "zero"
               for x in q)
    assert len(sends(m)) == 4                            # the four notices, and nothing else


# -- wording --------------------------------------------------------------------------------

def test_the_spoken_lines_are_the_designs_exactly():
    assert notice.ALERT_TEXT.format(n=3) == ALERT3
    assert notice.RECOVERED_TEXT == RECOVERED
    for text in (ALERT3, RECOVERED):
        assert text.isascii() and "  " not in text


def test_the_state_file_lives_in_the_mouths_folder(m):
    run(m, "f")
    assert m.state == Path(os.environ["MOUTH_DIR"]) / "notice-alert.json"
