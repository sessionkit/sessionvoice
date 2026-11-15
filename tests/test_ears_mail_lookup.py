"""ears-mail looks for `message` once more after a send that could not start (EARS-391).

A `message` found at start that is later removed, moved or made unrunnable made every
send fail with exit 126 until the job restarted. Now the send that could not start
calls resolve_message() ONCE, keeps what it finds for the next send, and returns 126 as
before; the failed mail is never sent again, and nothing loops.

In-process, as test_send_a_stalled_message_times_out_and_is_reported: em.send() with
_MESSAGE and the environment set by monkeypatch. The stub `message` is a shell script
in tmp running tests/fixtures/fake_message.py, whose log has one line per run. Removing,
moving or restoring it is a file operation between sends: no clock. HOME is the
session's tmp (the mail fence), so no real `message` can be reached.
"""
import json
import subprocess
import sys

import pytest

from test_ears_mail import FAKE, em_module, turn


def _stub(path):
    """A `message` at PATH that runs the fake (one log line per run)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\nexec '{sys.executable}' '{FAKE}' \"$@\"\n")
    path.chmod(0o755)
    return path


@pytest.fixture
def em(monkeypatch, tmp_path):
    """ears-mail with resolve_message wrapped to count its calls (em.lookups), and
    subprocess.run wrapped to count the runs one send makes (em.runs)."""
    mod = em_module()
    log = tmp_path / "message.log"
    monkeypatch.setenv("EARS_FAKE_MESSAGE_LOG", str(log))
    monkeypatch.delenv("EARS_FAKE_MESSAGE_PLAN", raising=False)
    monkeypatch.setattr(mod, "_MESSAGE", None)
    mod.lookups, mod.runs, mod.log = [], [], log
    real_resolve, real_run = mod.resolve_message, subprocess.run

    def resolve(*a, **k):
        got = real_resolve(*a, **k)
        mod.lookups.append(got)
        return got

    def run(*a, **k):
        mod.runs.append(a[0])
        return real_run(*a, **k)

    monkeypatch.setattr(mod, "resolve_message", resolve)
    monkeypatch.setattr(mod.subprocess, "run", run)
    mod.mail = mod.mail_for(turn(1, "Hey Ivy, check the build.", seq=1), ["ivy"],
                            {"ivy": "call"})
    return mod


def send(em):
    """One send; (code, err, the lookups it made, the runs it made)."""
    n_look, n_run = len(em.lookups), len(em.runs)
    code, _mid, err = em.send(em.mail, "ears", "host1")
    return code, err, len(em.lookups) - n_look, len(em.runs) - n_run


def logged(em):
    return [json.loads(x) for x in em.log.read_text().splitlines()] if em.log.exists() else []


def gone_then_back(em, monkeypatch, tmp_path):
    """Case A's sends: found at start, removed, sent twice, put back, sent twice."""
    stub = _stub(tmp_path / "bin" / "message")
    monkeypatch.setenv("EARS_MAIL_MESSAGE", str(stub))
    em._MESSAGE = em.resolve_message()               # the start's lookup finds it
    assert em._MESSAGE == ([str(stub)], "")
    stub.unlink()
    out = [send(em), send(em)]
    _stub(stub)
    out += [send(em), send(em)]
    return out


def test_a_gone_message_is_looked_for_once_and_found_when_back(em, monkeypatch, tmp_path):
    """Case A: 126 with one lookup, then 127 (the existing rule), then sent; the failed
    mail is never sent again, and a send that ran made no lookup."""
    em.lookups.clear()
    (c1, e1, l1, r1), (c2, e2, l2, r2), (c3, e3, l3, r3), (c4, e4, l4, r4) = \
        gone_then_back(em, monkeypatch, tmp_path)
    assert (c1, l1, r1) == (126, 1, 1)
    assert e1.startswith("message send: FileNotFoundError: ")
    assert "; looked again: no message binary ($EARS_MAIL_MESSAGE: " in e1
    assert e1.endswith("is not an executable on PATH; it is sessionmail's)")
    assert (c2, l2, r2) == (127, 1, 0) and e2.startswith("no message binary (")
    assert (c3, e3, r3) == (0, "", 1)
    assert (c4, e4, l4, r4) == (0, "", 0, 1)        # a send that ran: no lookup
    assert len(logged(em)) == 2                      # sends 3 and 4 only: none re-sent


def test_a_moved_message_is_found_in_its_new_place(em, monkeypatch, tmp_path):
    """Case B: a bare name on a PATH of two dirs, moved from the first to the second."""
    first, second = tmp_path / "one", tmp_path / "two"
    second.mkdir()
    _stub(first / "message-stub")
    monkeypatch.setenv("PATH", f"{first}:{second}")
    monkeypatch.setenv("EARS_MAIL_MESSAGE", "message-stub")
    em._MESSAGE = em.resolve_message()
    assert em._MESSAGE[0] == [str(first / "message-stub")]
    (first / "message-stub").unlink()
    _stub(second / "message-stub")
    code, err, looks, runs = send(em)
    assert (code, looks, runs) == (126, 1, 1)
    assert err.endswith(f"; looked again: sends by {second / 'message-stub'}")
    assert logged(em) == []
    code, err, looks, runs = send(em)
    assert (code, err, looks, runs) == (0, "", 0, 1)
    assert em._MESSAGE[0] == [str(second / "message-stub")] and len(logged(em)) == 1


def test_a_message_that_can_never_start_costs_one_run_and_one_lookup_a_send(em, monkeypatch,
                                                                         tmp_path):
    """Case C: found, but not a program (exec format error). Never a loop."""
    junk = tmp_path / "bin" / "message"
    junk.parent.mkdir()
    junk.write_bytes(b"\x00\x01 not a program\n")
    junk.chmod(0o755)
    monkeypatch.setenv("EARS_MAIL_MESSAGE", str(junk))
    em._MESSAGE = em.resolve_message()
    out = [send(em) for _ in range(3)]
    assert [(c, looks, runs) for c, _e, looks, runs in out] == [(126, 1, 1)] * 3
    assert all(e.startswith("message send: OSError: ") and "Exec format error" in e
               for _c, e, _l, _r in out)
    assert all(e.endswith(f"; looked again: sends by {junk}") for _c, e, _l, _r in out)


def test_a_message_installed_later_is_found_with_no_restart(em, monkeypatch, tmp_path):
    """Case D: today's 127 rule, pinned: none at start, then installed."""
    stub = tmp_path / "bin" / "message"
    monkeypatch.setenv("EARS_MAIL_MESSAGE", str(stub))
    code, err, looks, runs = send(em)
    assert (code, looks, runs) == (127, 1, 0) and err.startswith("no message binary (")
    _stub(stub)
    code, err, looks, runs = send(em)
    assert (code, err, looks, runs) == (0, "", 1, 1) and len(logged(em)) == 1


def test_a_gone_message_alerts_at_the_third_failure_and_recovers_when_back(em, monkeypatch,
                                                                        tmp_path):
    """Case E: case A's codes, with one more 127, fed to MailAlert at N = 3."""
    stub = _stub(tmp_path / "bin" / "message")
    monkeypatch.setenv("EARS_MAIL_MESSAGE", str(stub))
    em._MESSAGE = em.resolve_message()
    stub.unlink()
    codes = [send(em)[0] for _ in range(3)]
    _stub(stub)
    codes.append(send(em)[0])
    assert codes == [126, 127, 127, 0]
    run = em.MailAlert()
    assert [run.observe(c == 0, "speak", 3) for c in codes] == [None, None, "alert",
                                                                "recovered"]
