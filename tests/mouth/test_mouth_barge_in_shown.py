"""Who may cut, shown: ``mouth barge`` and ``mouth status`` print the effective
``barge_in`` per place (room, call), with where it came from.

    the ``barge_in`` key present:      from user,    by setting
    absent, an operator's mailbox:     from default, by operator  (room only)
    absent, unconfigured:              from default, by builtin   (anyone)

``who`` is ``"anyone"`` or the sorted names; ``[]`` is nobody. The answer is
`hold.barge_in`'s, place by place. Every test stubs ``session`` (the operator
reader) as ``test_barge_in_operator`` does, or keeps conftest's reader off.
"""
from __future__ import annotations

import json

import pytest

import mouth
from mouth import __main__ as cli
from mouth import hold

from test_barge_in_operator import ADDR_ONLY, CONFIGURED, UNCONFIGURED, _fresh, session  # noqa: F401


def place(who, frm, by) -> dict:
    return {"who": who, "from": frm, "by": by}


# -- the readout --------------------------------------------------------------------

def test_unconfigured_is_anyone_in_both_places_by_builtin(session):
    session(UNCONFIGURED)
    assert hold.barge_in_shown({}) == {"room": place("anyone", "default", "builtin"),
                                       "call": place("anyone", "default", "builtin")}


def test_the_operator_default_names_the_room_and_leaves_the_call_anyone(session):
    session(CONFIGURED)
    assert hold.barge_in_shown({}) == {"room": place(["you"], "default", "operator"),
                                       "call": place("anyone", "default", "operator")}


def test_an_operator_with_no_mailbox_is_builtin_anyone(session):
    session(ADDR_ONLY)
    assert hold.barge_in_shown({})["room"] == place("anyone", "default", "builtin")


@pytest.mark.parametrize("value, room, call", [
    ("anyone", "anyone", "anyone"),
    (["Theo", "alice"], ["alice", "theo"], ["alice", "theo"]),
    ([], [], []),                                               # nobody
    ({"room": ["theo"]}, ["theo"], "anyone"),                   # a place left out: anyone
    ({"room": "anyone", "call": ["alice"]}, "anyone", ["alice"]),
    (3, "anyone", "anyone"),                                    # unreadable: anyone, still the setting
])
def test_the_setting_wins_and_is_shown_per_place(session, value, room, call):
    session(CONFIGURED)
    got = hold.barge_in_shown({"barge_in": value})
    assert got == {"room": place(room, "user", "setting"), "call": place(call, "user", "setting")}


@pytest.mark.parametrize("settings", [{}, {"barge_in": ["theo"]}, {"barge_in": {"call": ["a"]}},
                                      {"barge_in": []}])
def test_it_is_barge_ins_own_answer(session, settings):
    session(CONFIGURED)
    shown = hold.barge_in_shown(settings)
    for p, device in (("room", "airpods"), ("call", "call:54541")):
        who = hold.barge_in(device, settings)
        assert shown[p]["who"] == ("anyone" if who is None else sorted(who))


# -- mouth barge and mouth status print it -------------------------------------------

@pytest.fixture
def d(tmp_path, monkeypatch):
    m = tmp_path / "mouth"
    m.mkdir()
    monkeypatch.setenv("MOUTH_DIR", str(m))
    monkeypatch.setenv("MOUTH_SETTINGS", str(tmp_path / "mouth.json"))
    return m


def test_mouth_barge_prints_who_may_cut(d, session, capsys):
    session(CONFIGURED)
    assert cli.main(["barge"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["barge_in"]["room"] == place(["you"], "default", "operator")
    (d.parent / "mouth.json").write_text(json.dumps({"barge_in": "anyone"}))
    assert cli.main(["barge"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["barge_in"]["room"] == place("anyone", "user", "setting")


def test_mouth_status_carries_the_same_readout(d, session):
    session(UNCONFIGURED)
    (d.parent / "mouth.json").write_text(json.dumps({"barge_in": {"room": ["theo"]}}))
    st = mouth.status(d)["barge"]
    assert st["barge_in"] == {"room": place(["theo"], "user", "setting"),
                              "call": place("anyone", "user", "setting")}
    assert st["barge_in"] == mouth.barge_state(d)["barge_in"]
    json.dumps(st)
