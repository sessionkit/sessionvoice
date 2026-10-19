"""Who may barge in by default, from the operator block (EARS-304 R4, SC3; design section 4).

``hold.barge_in()``:

    the ``barge_in`` key present (any value):  explicit, as today ("anyone" included)
    absent, ``session operator`` names a mailbox:  {"room": [mailbox]}  (calls stay anyone)
    absent, nothing configured:                anyone (None), as today

Only the settings-only ``mailbox`` field counts, never ``operator``: an
``AGENT_OPERATOR_ADDR``-only answer (operator set, mailbox null) is anyone (R7).

Every test stubs ``session``: a script alone in a tmp bin dir (or nothing at all),
with ``MOUTH_SESSION_BIN`` unset only where a test switches the reader ON.
No test runs this host's ``session operator`` or reads its block.
"""
from __future__ import annotations

import json
import os
import shlex

import pytest

from ears import operator_config as oc
from mouth import hold
from tests import env_fence

CONFIGURED = {"operator": "you@host.example", "mailbox": "you", "privileged": ["you", "you@host.example"]}
UNCONFIGURED = {"operator": None, "mailbox": None, "privileged": []}
# AGENT_OPERATOR_ADDR alone: sessiontools moves `operator` and leaves mailbox and privileged empty.
ADDR_ONLY = {"operator": "someone@elsewhere.example", "mailbox": None, "privileged": []}

ROOM = (None, "airpods", "speaker", "")
CALLS = ("call", "call:54541")


@pytest.fixture(autouse=True)
def _fresh():
    oc.clear()
    yield
    oc.clear()


@pytest.fixture
def session(tmp_path, monkeypatch):
    """``session(answer)`` / ``session(body=...)``: a fake ``session`` alone on PATH, reader ON."""
    bindir = tmp_path / "bin"
    bindir.mkdir()

    def make(answer=CONFIGURED, body=None):
        monkeypatch.setenv("PATH", str(bindir))
        env_fence.delenv(monkeypatch, oc.SESSION_BIN_ENV)
        if body is None:
            body = f"printf '%s\\n' {shlex.quote(json.dumps(answer))}"
        exe = bindir / "session"
        exe.write_text(f'#!/bin/sh\n[ "$1" = operator ] || exit 9\n{body}\n')
        exe.chmod(0o755)
        oc.clear()

    def absent():
        monkeypatch.setenv("PATH", str(bindir))      # an empty dir: no session anywhere
        env_fence.delenv(monkeypatch, oc.SESSION_BIN_ENV)
        oc.clear()

    make.absent = absent
    return make


# -- configured, nothing explicit: the operator's mailbox, room only ------------------

def test_configured_default_is_the_operators_mailbox_in_the_room(session):
    session(CONFIGURED)
    for device in ROOM:
        assert hold.barge_in(device, {}) == {"you"}, device


def test_configured_default_leaves_calls_anyone(session):
    """The default names the room only: a call line is anyone at once, with no 2 s label wait."""
    session(CONFIGURED)
    for device in CALLS:
        assert hold.barge_in(device, {}) is None, device


def test_configured_default_from_a_settings_file_without_the_key(tmp_path, monkeypatch, session):
    """``settings=None`` loads the mouth's own file; a file with no ``barge_in`` takes the default."""
    session(CONFIGURED)
    f = tmp_path / "mouth.json"
    f.write_text(json.dumps({"voice": "x"}))
    monkeypatch.setenv("MOUTH_SETTINGS", str(f))
    assert hold.barge_in("airpods") == {"you"}
    assert hold.barge_in("call:1") is None
    f.write_text(json.dumps({"barge_in": "anyone"}))
    assert hold.barge_in("airpods") is None


def test_the_mailbox_is_read_as_a_speaker_name_lowercased(session):
    session({**CONFIGURED, "mailbox": "  You  "})
    assert hold.barge_in("airpods", {}) == {"you"}


def test_other_keys_do_not_count_as_explicit(session):
    """Only the ``barge_in`` key is explicit: other settings leave the default in place."""
    session(CONFIGURED)
    assert hold.barge_in("airpods", {}) == hold.barge_in("airpods", {"voice": "x"}) == {"you"}


# -- explicit always wins ----------------------------------------------------------

@pytest.mark.parametrize("device", ROOM + CALLS)
def test_explicit_anyone_wins_over_the_operator(session, device):
    session(CONFIGURED)
    assert hold.barge_in(device, {"barge_in": "anyone"}) is None


def test_explicit_list_wins_over_the_operator(session):
    session(CONFIGURED)
    assert hold.barge_in("airpods", {"barge_in": ["alice"]}) == {"alice"}
    assert hold.barge_in("call:1", {"barge_in": ["alice"]}) == {"alice"}
    assert hold.barge_in("airpods", {"barge_in": []}) == frozenset()    # nobody, as written


def test_explicit_per_place_wins_and_a_place_left_out_is_anyone(session):
    """A per-place object is explicit: its missing place is anyone, never the operator."""
    session(CONFIGURED)
    assert hold.barge_in("call:1", {"barge_in": {"call": ["alice"]}}) == {"alice"}
    assert hold.barge_in("airpods", {"barge_in": {"call": ["alice"]}}) is None
    assert hold.barge_in("airpods", {"barge_in": {}}) is None


@pytest.mark.parametrize("value", [3, None, {"room": 3}, "", "*"])
def test_explicit_unreadable_value_still_reads_as_anyone(session, value):
    """A present key is explicit even when malformed: it reads as anyone, as today."""
    session(CONFIGURED)
    assert hold.barge_in("airpods", {"barge_in": value}) is None


# -- unconfigured: anyone, exactly as today ------------------------------------------

@pytest.mark.parametrize("device", ROOM + CALLS)
def test_unconfigured_is_anyone(session, device):
    session(UNCONFIGURED)
    assert hold.barge_in(device, {}) is None


@pytest.mark.parametrize("device", ROOM + CALLS)
def test_agent_operator_addr_only_is_anyone(session, device):
    """R7: an operator with no settings mailbox (AGENT_OPERATOR_ADDR alone) picks nobody."""
    session(ADDR_ONLY)
    assert hold.barge_in(device, {}) is None


def test_privileged_without_a_mailbox_is_anyone(session):
    """Only ``mailbox`` names the speaker: privileged seats are senders, not voices."""
    session({"operator": "you@host.example", "mailbox": None, "privileged": ["vera"]})
    assert hold.barge_in("airpods", {}) is None


def test_session_missing_is_anyone(session):
    session.absent()
    assert hold.barge_in("airpods", {}) is None


@pytest.mark.parametrize("body", ["exit 1", "printf 'not json\\n'", "printf '[1]\\n'"])
def test_session_failing_is_anyone(session, body):
    session(body=body)
    assert hold.barge_in("airpods", {}) is None


def test_the_off_switch_is_anyone_even_with_a_configured_session(session, monkeypatch):
    """``MOUTH_SESSION_BIN`` set at all: the block is not read."""
    session(CONFIGURED)
    monkeypatch.setenv(oc.SESSION_BIN_ENV, "")
    oc.clear()
    assert hold.barge_in("airpods", {}) is None


def test_the_test_fence_is_unconfigured():
    """conftest's ``MOUTH_SESSION_BIN=""`` keeps every other barge-in test at anyone."""
    assert os.environ.get(oc.SESSION_BIN_ENV) == ""
    assert hold.barge_in("airpods", {}) is None


def test_an_explicit_setting_never_runs_session(tmp_path, monkeypatch):
    """The key present: the reader is never asked (a hanging ``session`` costs nothing)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    marker = tmp_path / "ran"
    exe = bindir / "session"
    # `: >` is a shell builtin: PATH holds only bindir, so `touch` would not be found
    # and the marker could never appear, whether or not session ran.
    exe.write_text(f"#!/bin/sh\n: > {shlex.quote(str(marker))}\nexit 1\n")
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))
    env_fence.delenv(monkeypatch, oc.SESSION_BIN_ENV)
    assert hold.barge_in("airpods", {"barge_in": ["alice"]}) == {"alice"}
    assert not marker.exists()
    # control: with the key absent the reader IS asked, so the marker can appear
    assert hold.barge_in("airpods", {}) is None
    assert marker.exists()
