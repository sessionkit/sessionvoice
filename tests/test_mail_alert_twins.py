"""The two mail alerts are one machine, written twice (EARS-391).

The turn mailer (keeper/ears-mail.py, ``MailAlert``) runs on the system python and
imports nothing from src/, so the mouth keeps its own copy for its notices
(src/mouth/notice.py, ``NoticeAlert``). One scenario table goes through both, and
they must give the same answers and the same state at every step, as
test_wake_spoken_forms.py holds the keeper's ``spoken_words`` to the ears'.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from mouth import notice

ROOT = Path(__file__).resolve().parent.parent


def _em():
    spec = importlib.util.spec_from_file_location("ears_mail_twins", ROOT / "keeper" / "ears-mail.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


em = _em()
F, S = False, True

#: name -> [(ok, channel, after, what)]: each step and the answer both must give.
SCENARIOS = {
    "N-1 fail: nothing; the Nth: alert; N+1..2N: nothing": [
        (F, "speak", 3, None), (F, "speak", 3, None), (F, "speak", 3, "alert"),
        (F, "speak", 3, None), (F, "speak", 3, None), (F, "speak", 3, None)],
    "after an alert, one send recovers; a new run alerts again": [
        (F, "speak", 3, None), (F, "speak", 3, None), (F, "speak", 3, "alert"),
        (S, "speak", 3, "recovered"), (S, "speak", 3, None),
        (F, "speak", 3, None), (F, "speak", 3, None), (F, "speak", 3, "alert")],
    "a send before N resets the count": [
        (F, "speak", 3, None), (F, "speak", 3, None), (S, "speak", 3, None),
        (F, "speak", 3, None), (F, "speak", 3, None)],
    "off mid-run clears it; on again starts afresh": [
        (F, "speak", 3, None), (F, "speak", 3, None), (F, "off", 3, None),
        (F, "speak", 3, None), (F, "speak", 3, None), (F, "speak", 3, "alert")],
    "off after an alert: no late recovery": [
        (F, "speak", 2, None), (F, "speak", 2, "alert"), (F, "off", 2, None),
        (S, "speak", 2, None)],
    "N lowered mid-run alerts at the next failure": [
        (F, "speak", 5, None), (F, "speak", 5, None), (F, "speak", 5, None),
        (F, "speak", 2, "alert"), (F, "speak", 2, None)],
    "N raised after an alert: still alerted, no second alert": [
        (F, "speak", 1, "alert"), (F, "speak", 9, None), (S, "speak", 9, "recovered")],
    "N = 1: every run alerts on its first failure": [
        (F, "speak", 1, "alert"), (F, "speak", 1, None), (S, "speak", 1, "recovered"),
        (F, "speak", 1, "alert")],
    "a send with nothing failed says nothing": [(S, "speak", 3, None), (S, "off", 3, None)],
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_both_machines_walk_the_table_alike(name):
    a, b = em.MailAlert(), notice.NoticeAlert()
    for i, (ok, channel, after, what) in enumerate(SCENARIOS[name]):
        got = (a.observe(ok, channel, after), b.observe(ok, channel, after))
        assert got == (what, what), f"{name}: step {i}"
        assert a.state() == b.state(), f"{name}: step {i}"


@pytest.mark.parametrize("saved", [
    None, "x", [], {}, {"failed": 4, "alerted": True}, {"failed": 0, "alerted": False},
    {"failed": "3", "alerted": True}, {"failed": 2}, {"failed": True, "alerted": False},
    {"failed": -1, "alerted": False}, {"failed": 2, "alerted": "yes"},
    {"failed": 2.0, "alerted": True}])
def test_both_read_a_saved_run_alike(saved):
    assert em.MailAlert(saved).state() == notice.NoticeAlert(saved).state()


def test_both_have_the_same_channels_and_defaults():
    assert em.ALERT_CHANNELS == notice.ALERT_CHANNELS == ("speak", "off")
    assert (em.ALERT_DEFAULT, em.ALERT_AFTER_DEFAULT) == \
        (notice.ALERT_DEFAULT, notice.ALERT_AFTER_DEFAULT) == ("speak", 3)


@pytest.mark.parametrize("value", [" speak ", "OFF", "", "loud", None, "3", " 4 ", "0", "-1",
                                   "2.5", "٣"])
def test_both_read_a_variable_alike(value):
    ears = em.alert_setting({"EARS_MAIL_ALERT": value, "EARS_MAIL_ALERT_AFTER": value}
                            if value is not None else {}, conf="/nonexistent/ears.json")
    mouth = notice.notice_alert_setting({"MOUTH_NOTICE_ALERT": value,
                                         "MOUTH_NOTICE_ALERT_AFTER": value}
                                        if value is not None else {}, settings={})
    assert ears == mouth


@pytest.mark.parametrize("value", ["speak", "off", "SPEAK", 0, 1, 5, True, -1, 2.5, "3", None,
                                   ["speak"]])
def test_both_read_a_file_value_alike(tmp_path, value):
    conf = tmp_path / "ears.json"
    conf.write_text(json.dumps({"mail_alert": value, "mail_alert_after": value}))
    ears = em.alert_setting({}, conf=conf)
    mouth = notice.notice_alert_setting({}, settings={"notice_alert": value,
                                                      "notice_alert_after": value})
    assert ears == mouth
