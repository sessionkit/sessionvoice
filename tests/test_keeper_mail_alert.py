"""The keeper's own notices fail loudly too: mail_me runs ``ears-mail.py keeper-send``.

ears-keeper.sh's ``mail_me`` (device and capture notices) is a sender of its own. Each
notice now goes through ``keeper/ears-mail.py keeper-send``: the send, bounded at
SEND_TIMEOUT_S, then the turn mailer's own machine (``alert_setting``, ``MailAlert``,
``run_alert``) over a run kept in the keeper's own state file (``$RUN/keeper-mail.json``,
never ears-mail's ``--state``). At the Nth failed notice in a row (``mail_alert_after``,
default 3) one ``ears-keeper: ERROR: mail alert:`` line and one ``mouth say --alert``;
then quiet until a notice goes through, which says ``ears-keeper: mail recovered:`` once.
"No message binary" counts as a failure; nobody to mail (NOTIFY empty) is no send.

Every run fakes both ends: a ``message`` that follows a plan (``f`` fails, ``.`` sends,
``h`` hangs) and ``tests/fixtures/fake_mouth.py``. No live keeper, capture or launchd,
no real mail and no real mouth. The env fence turns the alert off for every other test;
these set it back.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "keeper" / "ears-mail.py"
KEEPER = ROOT / "keeper" / "ears-keeper.sh"
FAKE_MOUTH = ROOT / "tests" / "fixtures" / "fake_mouth.py"
BASH = shutil.which("bash") or "/bin/bash"
SUBJECT = "ears: mic -> MacBook Pro Microphone at 12:00 EOM"

ALERT3 = ("The ears' notices are failing: 3 in a row did not go out, so agents are not told "
          "when the microphone or the capture changes. The ears keeper log says why.")
RECOVERED = "The ears' notices are going out again. A change while they failed was not sent."

# A `message` that follows $FAKE_PLAN, one character per call (the call's index is the
# lines already in $FAKE_LOG): f exits 1, h sleeps past any bound, anything else sends.
FAKE_MESSAGE = """#!/bin/sh
n=$(cat "$FAKE_LOG" 2>/dev/null | wc -l | tr -d ' ')
echo "$*" >> "$FAKE_LOG"
c=$(printf '%s' "$FAKE_PLAN" | cut -c$((n + 1)))
case "$c" in
  f) echo "message: refused" >&2; exit 1 ;;
  h) exec sleep 30 ;;
esac
exit 0
"""


def _em():
    spec = importlib.util.spec_from_file_location("ears_mail_keeper", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


em = _em()


def _mail_me() -> str:
    m = re.search(r"^mail_me\(\) \{.*?^\}\n", KEEPER.read_text(), re.S | re.M)
    assert m, "mail_me() not found"
    return m.group(0)


class Host:
    """One host's keeper notices: a fake message (on PATH, or none), a fake mouth, the
    keeper's run file and ears.json in tmp."""

    def __init__(self, tmp_path):
        self.d = tmp_path
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.message = self.bin / "message"
        self.message.write_text(FAKE_MESSAGE)
        self.message.chmod(0o755)
        self.sends = tmp_path / "sends.log"
        self.mouth_log = tmp_path / "mouth.jsonl"
        self.state = tmp_path / "run" / "keeper-mail.json"
        self.state.parent.mkdir()
        self.conf = tmp_path / "ears.json"     # absent unless a test writes it
        self.empty = tmp_path / "empty-bin"
        self.empty.mkdir()

    def env(self, plan="", **extra):
        e = dict(os.environ, HOME=str(self.d / "home"), FAKE_LOG=str(self.sends),
                 FAKE_PLAN=plan, EARS_CONF=str(self.conf),
                 EARS_MAIL_ALERT="speak")   # the fence turns it off for every other test
        e.pop("EARS_MAIL_ALERT_AFTER", None)
        e.update(extra)
        return {k: v for k, v in e.items() if v is not None}

    def mouth(self, *flags):
        return shlex.join([sys.executable, str(FAKE_MOUTH), "--log", str(self.mouth_log),
                           *flags, "--"])

    def send(self, plan="", message=True, mouth=None, **extra):
        """One keeper-send run, as mail_me runs it: (exit, stdout lines)."""
        cmd = [sys.executable, str(SCRIPT), "keeper-send", "--state", str(self.state),
               "--conf", str(self.conf), "--message", str(self.message) if message else "",
               "--mouth", mouth or self.mouth(), "--", "op@host1", SUBJECT]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                           env=self.env(plan, **extra))
        return p.returncode, p.stdout.splitlines()

    def mail_me(self, plan="", n=1, notify="op@host1", path=None, helper=None, mouth=None,
                **extra):
        """mail_me N times, cut out of the keeper with its sender as the keeper sets it."""
        pre = "\n".join([
            'note() { echo "NOTE $*"; }',
            f"PY={shlex.quote(sys.executable)}",
            f"MAIL_HELPER={shlex.quote(str(helper or SCRIPT))}",
            f"MAIL_STATE={shlex.quote(str(self.state))}",
            f"CONF={shlex.quote(str(self.conf))}",
            f"NOTIFY={shlex.quote(notify)}",
            _mail_me(),
        ])
        calls = "\n".join(f'mail_me {shlex.quote(SUBJECT)} || echo "RC $?"' for _ in range(n))
        env = self.env(plan, PATH=path or f"{self.bin}:/usr/bin:/bin",
                       EARS_MOUTH=mouth or self.mouth(), **extra)
        p = subprocess.run([BASH, "-c", pre + "\n" + calls], capture_output=True, text=True,
                           timeout=120, env=env)
        assert p.returncode == 0, p.stderr
        return p.stdout.splitlines()

    def said(self):
        if not self.mouth_log.exists():
            return []
        return [json.loads(x) for x in self.mouth_log.read_text().splitlines()]

    def texts(self):
        return [a[2] for a in self.said()]

    def sent(self):
        return self.sends.read_text().splitlines() if self.sends.exists() else []

    def run(self):
        return json.loads(self.state.read_text())["mail_alert"]


@pytest.fixture
def h(tmp_path):
    return Host(tmp_path)


def _alerts(lines):
    return [x for x in lines if "ears-keeper: ERROR: mail alert:" in x
            or "ears-keeper: mail recovered:" in x]


# -- once at N, then quiet; once when it works again ----------------------------------

def test_two_failures_say_nothing_the_third_says_it_once_and_the_next_two_nothing(h):
    outs = [h.send("fffff")[1] for _ in range(5)]
    assert [len(_alerts(o)) for o in outs] == [0, 0, 1, 0, 0]
    assert _alerts(outs[2]) == ["ears-keeper: ERROR: mail alert: 3 notices in a row failed; "
                                "queued for the mouth (utt a1e700000000)"]
    assert h.said() == [["say", "--alert", ALERT3]]
    assert h.run() == {"failed": 5, "alerted": True}


def test_each_failed_send_has_its_own_line_and_the_alert_follows_the_nths(h):
    for _ in range(2):
        assert h.send("fff") == (0, [f"mail FAILED (exit 1): {SUBJECT}"])
    code, out = h.send("fff")
    assert code == 0
    assert out[0] == f"mail FAILED (exit 1): {SUBJECT}"
    assert out[1].startswith("ears-keeper: ERROR: mail alert: 3 notices in a row failed")


def test_a_notice_that_goes_through_after_the_alert_says_so_once_and_a_new_run_alerts(h):
    for _ in range(4):
        h.send("ffff.." + "fff")
    code, out = h.send("ffff.." + "fff")
    assert code == 0
    assert out == ["ears-keeper: mail recovered: a notice went through after 4 in a row "
                   "failed; queued for the mouth (utt a1e700000001)"]
    assert h.send("ffff.." + "fff")[1] == []                 # a second success: nothing
    assert h.run() == {"failed": 0, "alerted": False}
    outs = [h.send("ffff.." + "fff")[1] for _ in range(3)]
    assert [len(_alerts(o)) for o in outs] == [0, 0, 1]
    assert h.texts() == [ALERT3, RECOVERED, ALERT3]


def test_two_failures_a_send_and_two_failures_say_nothing(h):
    for _ in range(5):
        assert _alerts(h.send("ff.ff")[1]) == []
    assert h.said() == []


def test_the_run_survives_between_notices_so_a_restart_says_nothing_twice(h):
    for _ in range(3):
        h.send("ffffff")
    # each notice is its own process: what carries the run is the state file alone
    assert h.run() == {"failed": 3, "alerted": True}
    for _ in range(3):
        assert _alerts(h.send("ffffff")[1]) == []
    assert len(h.said()) == 1


@pytest.mark.parametrize("body", ["not json", "[1, 2]", '{"mail_alert": {"failed": "3"}}'])
def test_a_missing_or_malformed_run_starts_fresh(h, body):
    h.state.write_text(body)
    h.send("f")
    assert h.run() == {"failed": 1, "alerted": False}


def test_a_run_that_cannot_be_saved_is_said_and_the_notice_still_goes(h):
    state = h.d / "no-such-dir" / "keeper-mail.json"
    cmd = [sys.executable, str(SCRIPT), "keeper-send", "--state", str(state),
           "--message", str(h.message), "--mouth", h.mouth(), "--", "op@host1", SUBJECT]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=h.env("."))
    assert p.returncode == 0
    assert p.stdout.startswith("ears-keeper: ERROR: mail alert: run not saved in ")
    assert h.sent() == [f"send op@host1 -s {SUBJECT} --no-body"]


# -- what counts ---------------------------------------------------------------------

def test_no_message_binary_counts_as_a_failure(h):
    for _ in range(2):
        assert h.send(message=False) == (0, [])     # mail_me writes its own line for it
    code, out = h.send(message=False)
    assert out == ["ears-keeper: ERROR: mail alert: 3 notices in a row failed; queued for "
                   "the mouth (utt a1e700000000)"]
    assert h.sent() == []


def test_the_send_is_mail_mes_own_argv(h):
    h.send(".")
    assert h.sent() == [f"send op@host1 -s {SUBJECT} --no-body"]


def test_a_hung_send_is_cut_off_and_counts_as_a_failure(h, monkeypatch):
    for k, v in h.env("h").items():
        monkeypatch.setenv(k, v)
    assert em.keeper_notice_send(str(h.message), "op@host1", SUBJECT, timeout=0.5) \
        == "timed out after 0.5 s"


def test_the_send_is_bounded_at_the_turn_mailers_timeout():
    assert em.SEND_TIMEOUT_S == 30


def test_a_send_that_will_not_run_is_a_failure(h):
    assert em.keeper_notice_send(str(h.d / "gone"), "op@host1", SUBJECT).startswith("not run: ")
    assert em.keeper_notice_send("", "op@host1", SUBJECT) == "no message binary"


# -- the settings: the turn mailer's, read at each notice -------------------------------

def test_the_files_after_moves_the_alert(h):
    h.conf.write_text(json.dumps({"mail_alert_after": 1}))
    assert len(_alerts(h.send("f")[1])) == 1


def test_the_variable_after_beats_the_files(h):
    h.conf.write_text(json.dumps({"mail_alert_after": 5}))
    assert len(_alerts(h.send("f", EARS_MAIL_ALERT_AFTER="1")[1])) == 1


def test_off_says_nothing_and_clears_the_run(h):
    h.conf.write_text(json.dumps({"mail_alert": "off"}))
    for _ in range(4):
        assert _alerts(h.send("ffff", EARS_MAIL_ALERT=None)[1]) == []
    assert h.said() == []
    assert h.run() == {"failed": 0, "alerted": False}


def test_the_variables_off_beats_the_files_speak(h):
    h.conf.write_text(json.dumps({"mail_alert": "speak"}))
    for _ in range(3):
        h.send("fff", EARS_MAIL_ALERT="off")
    assert h.said() == []


# -- a mouth that fails is named, never fatal -----------------------------------------

def test_a_mouth_that_fails_is_named_and_the_notice_goes_on(h):
    for _ in range(2):
        h.send("ffff")
    code, out = h.send("ffff", mouth=h.mouth("--exit", "2", "--err", "no --alert"))
    assert code == 0
    assert _alerts(out) == ["ears-keeper: ERROR: mail alert: 3 notices in a row failed; not "
                            "said: mouth say exit 2: no --alert"]
    assert h.send("ffff")[1] == [f"mail FAILED (exit 1): {SUBJECT}"]   # nothing tried again


# -- the keeper's mail_me, cut out of ears-keeper.sh ------------------------------------

def test_mail_me_says_it_once_at_three_and_once_when_it_works_again(h):
    out = h.mail_me("fffff.", n=6)
    assert [x for x in out if x.startswith("RC")] == []          # mail_me returns 0 every time
    assert [x for x in out if "mail FAILED" in x] == [f"NOTE mail FAILED (exit 1): {SUBJECT}"] * 5
    assert _alerts(out) == [
        "NOTE ears-keeper: ERROR: mail alert: 3 notices in a row failed; queued for the mouth "
        "(utt a1e700000000)",
        "NOTE ears-keeper: mail recovered: a notice went through after 5 in a row failed; "
        "queued for the mouth (utt a1e700000001)"]
    assert h.texts() == [ALERT3, RECOVERED]
    assert len(h.sent()) == 6


def test_mail_me_with_no_message_binary_counts_it(h):
    out = h.mail_me(n=3, path=str(h.empty))
    no_bin = ("NOTE mail FAILED (no message binary on PATH, ~/.local/bin or "
              f"~/.local/share/uv/tools/sessionmail/bin): {SUBJECT}")
    assert out == [no_bin, no_bin, no_bin,
                   "NOTE ears-keeper: ERROR: mail alert: 3 notices in a row failed; queued "
                   "for the mouth (utt a1e700000000)"]
    assert h.sent() == []


def test_mail_me_with_nobody_to_mail_sends_and_counts_nothing(h):
    out = h.mail_me("fff", n=3, notify="")
    assert all(x.startswith("NOTE not mailed (EARS_NOTIFY empty") for x in out)
    assert not h.state.exists() and h.sent() == [] and h.said() == []


def test_mail_me_whose_sender_will_not_run_says_so_and_goes_on(h):
    out = h.mail_me(".", n=1, helper=h.d / "gone.py")
    assert out == [f"NOTE mail FAILED (ears-mail.py keeper-send exit 2): {SUBJECT}"]


def test_the_keepers_run_is_its_own_file_under_run_not_the_turn_mailers():
    s = KEEPER.read_text()
    assert 'MAIL_STATE="$RUN/keeper-mail.json"' in s
    assert 'MAIL_HELPER="$(cd "$(dirname "$0")" && pwd)/ears-mail.py"' in s
    assert s.index('RUN="${EARS_RUN:') < s.index('MAIL_STATE="$RUN/')
    body = _mail_me()
    assert '"$PY" "$MAIL_HELPER" keeper-send --state "$MAIL_STATE"' in body
    # no second counter in the shell: the run is ears-mail.py's MailAlert
    code = "\n".join(ln.split("#")[0] for ln in body.splitlines())
    assert "$((" not in code and "let " not in code


# -- the spoken lines -------------------------------------------------------------------

def test_the_spoken_lines_are_exactly_these():
    assert em.KEEPER_ALERT_TEXT.format(n=3) == ALERT3
    assert em.KEEPER_RECOVERED_TEXT == RECOVERED


@pytest.mark.parametrize("text", [ALERT3, RECOVERED])
def test_a_spoken_line_names_no_tracker_id_no_time_and_no_he(text):
    assert not re.search(r"\b[A-Z]{2,}-\d+\b", text)
    assert not re.search(r"\d{1,2}:\d{2}", text)
    assert not re.search(r"\bhe\b", text, re.I)
