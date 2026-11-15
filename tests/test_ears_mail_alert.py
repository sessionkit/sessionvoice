"""ears-mail says it when turn mail fails, once, and once when it works again (EARS-391).

At the Nth send in a row that fails (ears.json's ``mail_alert_after``, default 3), the
keeper runs ``MOUTH say --alert TEXT`` once and writes one ``ears-mail: ERROR: mail
alert:`` line; then it is quiet until a send goes through, and says once that mail is
working again. ``mail_alert`` ``off`` turns it off. The run is kept in ``--state``.
An alert line is no seat's: no turn answers it, a say-again never targets it, and no
cut notice is mailed about it.

Every run here is the real script against two fakes: ``tests/fixtures/fake_message.py``
(``$EARS_FAKE_MESSAGE_PLAN``: ``f`` fails, ``.`` sends) and
``tests/fixtures/fake_mouth.py`` as ``--mouth`` (records each argv; ``say`` prints a
plain utt). No real mail, no real mouth, no clock. The env fence turns the alert off for
every other test; these set it back.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "keeper" / "ears-mail.py"
FAKE_MESSAGE = ROOT / "tests" / "fixtures" / "fake_message.py"
FAKE_MOUTH = ROOT / "tests" / "fixtures" / "fake_mouth.py"
RULES = {"seats": {"ivy": {"names": "ivy"}, "vera": {"names": "vera"}}}

ALERT3 = ("Turn mail is failing: 3 in a row did not go out, so agents are not getting your "
          "turns. The ears-mail log says why.")
RECOVERED = ("Turn mail is working again. What you said while it failed was not sent, so say "
             "it once more if it matters.")


def _em():
    spec = importlib.util.spec_from_file_location("ears_mail_alert", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


em = _em()


def ts(sec):
    return f"2030-06-09T01:{int(sec // 60):02d}:{sec % 60:06.3f}+10:00"


def turn(sec, text, span=1.0, seq=None, **kw):
    return dict(ts=ts(sec), kind="turn", source="mic", device="airpods", text=text,
                t0=100.0, t1=100.0 + span, seq=seq or int(sec * 10), **kw)


def saying(sec, agent, text, utt, **kw):
    return dict(ts=ts(sec), kind="saying", agent=agent, text=text, utt=utt, **kw)


def said(sec, agent, utt, played_s=3.0, **kw):
    return dict(ts=ts(sec), kind="said", agent=agent, utt=utt, played_s=played_s, **kw)


def calls(n, start=0):
    """N turns that each call ivy: under --to seats, one send each."""
    return [turn(10 * (start + i + 1), f"Hey Ivy, number {start + i + 1}.", seq=start + i + 1)
            for i in range(n)]


class Keeper:
    """One host's keeper: a heard log, a state file, the fakes' logs. run() appends
    RECORDS to the log and runs ears-mail --once over what is new."""

    def __init__(self, tmp_path, monkeypatch=None):
        self.d = tmp_path
        self.logs = tmp_path / "logs" / "conversations"
        self.logs.mkdir(parents=True)
        self.heard = self.logs / "heard_2030-06-09.jsonl"
        self.rules = tmp_path / "wake.json"
        self.rules.write_text(json.dumps(RULES))
        self.state = tmp_path / "state.json"
        self.sends = tmp_path / "message.jsonl"
        self.mouth_log = tmp_path / "mouth.jsonl"
        self.fire = tmp_path / "fire.jsonl"
        self.conf = tmp_path / "ears.json"          # absent unless a test writes it
        self.runs = 0

    def run(self, records, plan="", env=(), mouth=None, args=(), state=True, to="seats"):
        with open(self.heard, "a") as f:
            f.write("".join(json.dumps(r) + "\n" for r in records))
        cmd = [sys.executable, str(SCRIPT), "--rules", str(self.rules), "--once",
               "--domain", "host1", "--fire-log", str(self.fire), "--to", to,
               "--mouth", mouth or f"{sys.executable} {FAKE_MOUTH} --log {self.mouth_log} --"]
        if state:
            cmd += ["--state", str(self.state)]
        if not (state and self.state.exists()):
            cmd.append("--from-start")
        e = dict(os.environ, VOICEMODE_BASE_DIR=str(self.d),
                 EARS_FAKE_MESSAGE_LOG=str(self.sends),
                 EARS_MAIL_MESSAGE=f"{sys.executable} {FAKE_MESSAGE}",
                 EARS_FAKE_MESSAGE_PLAN=plan, EARS_CONF=str(self.conf),
                 EARS_MAIL_ALERT="speak")   # the fence turns it off for every other test
        e.pop("EARS_MAIL_ALERT_AFTER", None)
        e.update(dict(env))
        e = {k: v for k, v in e.items() if v is not None}
        p = subprocess.run(cmd + list(args), capture_output=True, text=True, timeout=60, env=e)
        assert p.returncode == 0, p.stderr
        self.runs += 1
        self.stderr = p.stderr
        return p

    def said(self):
        """Each `mouth say` the keeper ran: its argv after the mouth."""
        if not self.mouth_log.exists():
            return []
        return [json.loads(x) for x in self.mouth_log.read_text().splitlines()]

    def texts(self):
        return [a[2] for a in self.said()]

    def lines(self, prefix):
        return [x for x in self.stderr.splitlines() if x.startswith(prefix)]

    def alerts(self):
        return self.lines("ears-mail: ERROR: mail alert:") + self.lines("ears-mail: mail recovered:")

    def sent(self):
        return [json.loads(x) for x in self.sends.read_text().splitlines()] if self.sends.exists() else []


@pytest.fixture
def k(tmp_path):
    return Keeper(tmp_path)


# -- criterion 1: once at N, then quiet ----------------------------------------------

def test_two_failures_say_nothing_the_third_says_it_once_and_the_next_three_nothing(k):
    k.run(calls(2), plan="ff")
    assert k.said() == [] and k.alerts() == []
    assert len(k.lines("ears-mail: ERROR: send to ivy failed")) == 2   # today's lines stay
    k.run(calls(1, start=2), plan="ff" + "f")
    assert k.said() == [["say", "--alert", ALERT3]]
    assert k.alerts() == ["ears-mail: ERROR: mail alert: 3 sends in a row failed; "
                          "queued for the mouth (utt a1e700000000)"]
    k.run(calls(3, start=3), plan="ffffff")
    assert len(k.said()) == 1 and k.alerts() == []


def test_the_alert_line_follows_the_nth_sends_own_error_line(k):
    k.run(calls(3), plan="fff")
    errs = k.lines("ears-mail: ERROR:")
    assert len(errs) == 4
    assert errs[2].startswith("ears-mail: ERROR: send to ivy failed, exit 75: mta: planned failure")
    assert errs[3].startswith("ears-mail: ERROR: mail alert: 3 sends in a row failed")


# -- criterion 2: once when it works again, then a new run alerts again -----------------

def test_a_send_that_goes_through_after_the_alert_says_so_once_and_a_new_run_alerts(k):
    k.run(calls(4), plan="fff.")
    assert k.texts() == [ALERT3, RECOVERED]
    assert k.alerts()[1] == ("ears-mail: mail recovered: a send went through after 3 in a row "
                             "failed; queued for the mouth (utt a1e700000001)")
    k.run(calls(4, start=4), plan="fff." + ".fff")
    assert k.texts() == [ALERT3, RECOVERED, ALERT3]


def test_the_recovery_names_the_runs_whole_count(k):
    k.run(calls(6), plan="fffff.")
    assert k.alerts()[-1].startswith("ears-mail: mail recovered: a send went through after 5 "
                                     "in a row failed")


# -- criterion 3: a send through resets the run -------------------------------------------

def test_two_failures_a_send_and_two_failures_say_nothing(k):
    k.run(calls(5), plan="ff.ff")
    assert k.said() == [] and k.alerts() == []


def test_one_sent_mail_among_failed_ones_resets_the_count():
    """Counts sends, not turns: one live box among dead ones never reaches N in a row."""
    a = em.MailAlert()
    assert [a.observe(ok, "speak", 3) for ok in (False, False, True, False, False)] == [None] * 5
    assert a.state() == {"failed": 2, "alerted": False}


# -- criterion 4: the alert never reaches the sender ---------------------------------------

def test_the_alert_is_never_mailed(k):
    k.run(calls(6), plan="ffff..")
    sent = k.sent()
    assert len(sent) == 6                               # one row per turn mail, no more
    assert [c["argv"][1] for c in sent] == ["ivy@host1"] * 6
    assert not [c for c in sent if "failing" in c["body"] or "working again" in c["body"]]
    assert k.texts() == [ALERT3, RECOVERED]


# -- criterion 6: settings ---------------------------------------------------------------

def test_with_no_settings_file_it_speaks_at_three():
    assert em.alert_setting({}, conf="/nonexistent/ears.json") == ("speak", 3)


def test_the_files_after_moves_the_alert_to_the_fifth_failure(k):
    k.conf.write_text(json.dumps({"mail_alert_after": 5}))
    k.run(calls(4), plan="ffff")
    assert k.said() == []
    k.run(calls(1, start=4), plan="fffff")
    assert k.texts() == [ALERT3.replace("3 in a row", "5 in a row")]


def test_the_variable_after_beats_the_files(k):
    k.conf.write_text(json.dumps({"mail_alert_after": 5}))
    k.run(calls(2), plan="ff", env={"EARS_MAIL_ALERT_AFTER": "2"})
    assert k.texts() == [ALERT3.replace("3 in a row", "2 in a row")]


def test_the_files_off_says_nothing(k):
    k.conf.write_text(json.dumps({"mail_alert": "off"}))
    k.run(calls(5), plan="fffff", env={"EARS_MAIL_ALERT": None})
    assert k.said() == [] and k.alerts() == []
    assert json.loads(k.state.read_text())["mail_alert"] == {"failed": 0, "alerted": False}


def test_the_variables_off_beats_the_files_speak(k):
    k.conf.write_text(json.dumps({"mail_alert": "speak"}))
    k.run(calls(5), plan="fffff", env={"EARS_MAIL_ALERT": "off"})
    assert k.said() == [] and k.alerts() == []


def test_unset_it_speaks_by_default(k):
    k.run(calls(3), plan="fff", env={"EARS_MAIL_ALERT": None})
    assert k.texts() == [ALERT3]


@pytest.mark.parametrize("value", ["loud", 0, "x", True, None, ["speak"], "SPEAK"])
def test_an_invalid_channel_in_the_file_falls_to_the_default(tmp_path, value):
    conf = tmp_path / "ears.json"
    conf.write_text(json.dumps({"mail_alert": value}))
    assert em.alert_setting({}, conf=conf)[0] == "speak"


@pytest.mark.parametrize("value", ["loud", 0, "x", True, -1, 2.5, "3", None])
def test_an_invalid_after_in_the_file_falls_to_the_default(tmp_path, value):
    conf = tmp_path / "ears.json"
    conf.write_text(json.dumps({"mail_alert_after": value}))
    assert em.alert_setting({}, conf=conf)[1] == 3


@pytest.mark.parametrize("value", ["loud", "", "0", "x", "true", "-1", "2.5", "٣"])
def test_an_invalid_variable_falls_to_the_file(tmp_path, value):
    conf = tmp_path / "ears.json"
    conf.write_text(json.dumps({"mail_alert": "off", "mail_alert_after": 5}))
    env = {"EARS_MAIL_ALERT": value, "EARS_MAIL_ALERT_AFTER": value}
    assert em.alert_setting(env, conf=conf) == ("off", 5)


def test_the_variables_any_case_trimmed_are_read(tmp_path):
    env = {"EARS_MAIL_ALERT": "  OFF ", "EARS_MAIL_ALERT_AFTER": " 4 "}
    assert em.alert_setting(env, conf=tmp_path / "none.json") == ("off", 4)


@pytest.mark.parametrize("body", ["not json", "[1, 2]", '"speak"'])
def test_a_file_that_is_not_a_json_object_is_no_layer(tmp_path, body):
    conf = tmp_path / "ears.json"
    conf.write_text(body)
    assert em.alert_setting({}, conf=conf) == ("speak", 3)


def test_the_file_is_ears_conf_else_the_voice_settings_folder(monkeypatch):
    assert em.ears_conf_path({"EARS_CONF": "/x/ears.json"}) == Path("/x/ears.json")
    monkeypatch.setenv("HOME", "/h")
    assert em.ears_conf_path({"EARS_CONF": ""}) == Path("/h/.sessionkit/voice/settings/ears.json")


def test_the_settings_are_read_at_each_send_with_no_restart():
    """`off` mid-run clears the run, so turning it on again starts afresh; N lowered
    mid-run alerts at the next failure (>= N)."""
    a = em.MailAlert()
    assert [a.observe(False, "speak", 3) for _ in range(2)] == [None, None]
    assert a.observe(False, "off", 3) is None and a.state() == {"failed": 0, "alerted": False}
    assert [a.observe(False, "speak", 3) for _ in range(2)] == [None, None]
    assert a.observe(False, "speak", 2) == "alert"      # 3 in a row now, N lowered to 2
    assert a.observe(False, "off", 3) is None
    assert a.observe(True, "speak", 3) is None          # an old alert gets no late recovery


# -- persistence: --state keeps the run across restarts -----------------------------------

def test_the_run_survives_a_restart_in_state(k):
    k.run(calls(3), plan="fff")
    assert k.texts() == [ALERT3]
    st = json.loads(k.state.read_text())
    assert st["mail_alert"] == {"failed": 3, "alerted": True}
    assert "offsets" in st
    k.run(calls(1, start=3), plan="ffff")                # restarted: one more failure
    assert k.texts() == [ALERT3] and k.alerts() == []
    k.run(calls(1, start=4), plan="ffff.")               # then a send goes through
    assert k.texts() == [ALERT3, RECOVERED]
    assert json.loads(k.state.read_text())["mail_alert"] == {"failed": 0, "alerted": False}


@pytest.mark.parametrize("saved", [None, "x", [], {"failed": "3", "alerted": True},
                                   {"failed": 2}, {"failed": True, "alerted": False},
                                   {"failed": -1, "alerted": False},
                                   {"failed": 2, "alerted": "yes"}])
def test_a_missing_or_malformed_saved_run_starts_fresh(saved):
    assert em.MailAlert(saved).state() == {"failed": 0, "alerted": False}


def test_a_state_file_without_the_key_starts_fresh(k):
    k.run(calls(2), plan="ff")
    st = json.loads(k.state.read_text())
    del st["mail_alert"]                                 # an older keeper's file
    k.state.write_text(json.dumps(st))
    k.run(calls(2, start=2), plan="ffff")
    assert k.said() == []                                # 2 in this run, not 4
    assert json.loads(k.state.read_text())["mail_alert"] == {"failed": 2, "alerted": False}


def test_without_state_the_run_lives_in_memory_only(k):
    k.run(calls(3), plan="fff", state=False)
    assert k.texts() == [ALERT3] and not k.state.exists()


# -- the speaker fails ---------------------------------------------------------------------

def test_a_mouth_that_fails_is_named_and_the_keeper_goes_on(k):
    mouth = f"{sys.executable} {FAKE_MOUTH} --log {k.mouth_log} --exit 1 --"
    p = k.run(calls(3), plan="fff", mouth=mouth)
    assert p.returncode == 0
    assert k.alerts() == ["ears-mail: ERROR: mail alert: 3 sends in a row failed; not said: "
                          "mouth say exit 1: mouth: no audio for it"]
    k.run(calls(3, start=3), plan="ffffff", mouth=mouth)
    assert len(k.said()) == 1 and k.alerts() == []       # no second `mouth say` in the run
    assert json.loads(k.state.read_text())["mail_alert"]["alerted"] is True


def test_a_mouth_that_will_not_run_is_named(k):
    k.run(calls(3), plan="fff", mouth="/nonexistent/mouth")
    (line,) = k.alerts()
    assert line.startswith("ears-mail: ERROR: mail alert: 3 sends in a row failed; not said: "
                           "mouth say not run: ")


def test_a_mouth_that_hangs_is_cut_off():
    mouth = f"{sys.executable} -c 'import time; time.sleep(30)'"
    assert em.run_alert(mouth, "x", timeout=0.2) == (False, "mouth say timed out after 0.2 s")


def test_an_older_mouth_without_the_flag_is_not_said(tmp_path):
    """An older `mouth say` exits 2 on --alert, as argparse does."""
    old = tmp_path / "old_mouth.py"
    old.write_text("import sys\nprint('mouth: error: unrecognized arguments: --alert', "
                   "file=sys.stderr)\nsys.exit(2)\n")
    ok, why = em.run_alert(f"{sys.executable} {old}", "x")
    assert not ok and why == "mouth say exit 2: mouth: error: unrecognized arguments: --alert"


def test_run_alert_takes_the_last_line_of_stdout_as_the_utt(tmp_path):
    m = tmp_path / "m.py"
    m.write_text("print('a banner')\nprint('0123456789ab')\n")
    assert em.run_alert(f"{sys.executable} {m}", "x") == (True, "0123456789ab")


# -- case 10: --dry-run never counts ---------------------------------------------------------

def test_a_dry_run_never_counts(k):
    k.run(calls(5), plan="fffff", args=("--dry-run",))
    assert k.said() == [] and k.sent() == [] and k.alerts() == []


# -- every send counts: a cut notice too -----------------------------------------------------

def test_a_cut_notice_that_fails_counts(k):
    cut = [saying(1, "ivy", "Building it now.", "c1"),
           said(3, "ivy", "c1", cut=True, reason="barge-in", notice="failed: x")]
    k.run(cut + calls(2, start=1), plan="fff")
    assert [c["argv"][1] for c in k.sent()] == ["ivy@host1"] * 3
    assert k.texts() == [ALERT3]


# -- case 12: wording --------------------------------------------------------------------

def test_the_spoken_lines_are_the_designs_exactly():
    assert em.ALERT_TEXT.format(n=3) == ALERT3
    assert em.RECOVERED_TEXT == RECOVERED
    for text in (ALERT3, RECOVERED):
        assert text.isascii() and "  " not in text


def test_no_spoken_line_is_a_say_again():
    rules = em.rules_of(RULES, [])
    phrases = set().union(*(r["_again"] for r in rules.values()))
    assert phrases                                       # the default say-again phrases
    for text in (ALERT3, RECOVERED):
        words, _ = em.again_match(text, rules)
        assert words not in phrases


# -- case 13: no turn answers an alert line -----------------------------------------------

VERA = [saying(1, "vera", "The build is green and the second option is safer.", "vera00000001"),
        said(4, "vera", "vera00000001")]
ALERT_ROWS = [saying(6, "mouth", ALERT3, "a1e700000000", interest="zero", alert=True),
              said(14, "mouth", "a1e700000000", played_s=8.0, interest="zero", alert=True)]


def _room(records):
    room = em.Room()
    for r in records:
        room.note(r)
    return room


def _verdict(records, text, at, conf=RULES):
    rules = em.rules_of(conf, [])
    room = _room(records)
    return em.verdicts(turn(at, text), rules, room), room


def test_a_reply_after_an_alert_line_still_wakes_the_seat_that_spoke():
    got, _ = _verdict(VERA + ALERT_ROWS, "Yes, go with that one.", 16)
    assert got == {"vera": "reply"}


def test_the_same_rows_without_alert_lose_the_reply():
    """The case the rule guards: a line of agent mouth would take the reply."""
    plain = [{k: v for k, v in r.items() if k != "alert"} for r in ALERT_ROWS]
    got, _ = _verdict(VERA + plain, "Yes, go with that one.", 16)
    assert got == {}


def test_an_unnamed_say_again_after_an_alert_line_targets_the_seats_line():
    got, room = _verdict(VERA + ALERT_ROWS, "Say again?", 16)
    assert got == {"vera": "again"} and room.again["utt"] == "vera00000001"


def test_an_alert_lines_rows_are_still_noted_for_echo_and_in_flight():
    room = _room(VERA + ALERT_ROWS)
    assert room.lines["a1e700000000"][1] is not None     # it ended: not in flight
    assert "mouth" not in room.said and [f[2] for f in room.finished] == ["vera00000001"]


def test_a_cut_alert_row_is_mailed_to_nobody_even_with_a_seat_named_mouth(k):
    k.rules.write_text(json.dumps({"seats": {**RULES["seats"], "mouth": {"names": "mouth"}}}))
    cut = [saying(1, "mouth", ALERT3, "a1e700000000", interest="zero", alert=True),
           said(3, "mouth", "a1e700000000", cut=True, reason="barge-in", alert=True,
                interest="zero", notice="none: an alert line, never mailed")]
    k.run(cut, plan="")
    assert k.sent() == []
    # the control: the same row without alert is told to its seat
    k2 = Keeper(k.d / "control")
    k2.rules.write_text(k.rules.read_text())
    k2.run([{x: v for x, v in r.items() if x != "alert"} for r in cut], plan="")
    assert [c["argv"][1] for c in k2.sent()] == ["mouth@host1"]
