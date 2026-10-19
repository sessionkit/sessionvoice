"""Mouth notices go by sessionmail's ``message send`` (EARS-381).

- **Who it goes out as** (RULINGS A1): the seat ``mouth``, name ``Mouth``,
  unsigned by default. A player a CLI ``say`` spawned inherits its caller's
  environment, so every ``CLAUDE_SESSION_*``, the session id and both must-sign
  switches are dropped: the notice never signs, or answers, as the caller.
  ``$MOUTH_SIGNER_SOCK`` and ``$MOUTH_SIGNER_PUB``, both set, are the mouth's
  own key, and must-sign turns on.
- **What the send would refuse** (rca-001 N1, N2, N5): the subject is ASCII with
  a backtick and ``$(`` defused, and ``SESSIONMAIL_HOST`` is pinned to the host
  the id is minted at. The fake ``message send`` refuses each, as the real one does.
- **A bounce of a notice is never spoken** (RULING R2-a): the envelope is the
  seat now, not NULL, so a postfix bounce comes back to the mouth's box.
  ``box.read`` files a delivery report unspoken, on either test; a foreign
  host's report keeps its D11 refusal, and a plain mail from this host's
  MAILER-DAEMON that names no notice follows the ordinary rules.

The fake is ``fixtures/fake_sendmail.py`` (the conftest's ``$MOUTH_SENDMAIL``);
it logs the seat and signing variables it saw beside its argv.
"""

from __future__ import annotations

import email.message
import json
import os
import subprocess
import sys
import textwrap

import pytest

from mouth import box as _box
from mouth import notice
from maildir_put import put
from tests._anchor import ANCHOR
from tests.mouth.test_mouth_notice import (  # noqa: F401 - env is the fixture
    FAKE, HOST, absent, env, flags, mails, mkbox, rows, run_player, said, say, sends)

#: What a caller's shell carries that must never reach the send.
CALLER = {
    "CLAUDE_SESSION_AGENT_SOCK": "/caller/agent.sock",
    "CLAUDE_SESSION_PUB": "/caller/key.pub",
    "CLAUDE_SESSION_ID": "caller-session",
    "CLAUDE_CODE_SESSION_ID": "caller-code-session",
    "SESSIONMAIL_REQUIRE_SIGNATURE": "1",
    "SESSION_MAIL_REQUIRE_SIGNATURE": "1",
    "TM_AGENT_ADDRESS": "ivy+caller",
    "EMAIL": f"ivy+caller@{HOST}",
    "SESSIONMAIL_FROM_NAME": "Ivy",
}
CUT = {"reason": "error", "cut": True}


def _assert_the_mouth_seat(seen: dict) -> None:
    assert seen["TM_AGENT_ADDRESS"] == seen["EMAIL"] == "mouth", seen
    assert seen["SESSIONMAIL_FROM_NAME"] == "Mouth", seen
    assert seen["SESSIONMAIL_HOST"] == HOST, seen
    assert not [k for k in seen if k.startswith("CLAUDE_SESSION_")], seen
    assert "CLAUDE_CODE_SESSION_ID" not in seen, seen
    assert not [k for k in notice.MUST_SIGN_VARS if k in seen], seen


# -- who it goes out as (RULINGS A1) ---------------------------------------------------

def test_a_player_never_signs_as_its_caller(env, monkeypatch):
    """The REQUIRED test: a player running in its caller's environment (as one a CLI
    ``say`` spawned does, through ``ensure_player``) sends the notice as the mouth,
    unsigned: the caller's key, session and must-sign never reach ``message send``."""
    for k, v in CALLER.items():
        monkeypatch.setenv(k, v)
    absent(monkeypatch)
    mkbox(env, "s-test")
    item = say("from the cli", device="airpods")
    run_player(env.d).join(5)
    assert said(env.logs)[item["utt"]]["notice"] == f"sent s-test@{HOST}"
    (sent,) = sends(env)
    _assert_the_mouth_seat(sent["env"])
    (m,) = mails(env, "s-test")
    assert m["From"] == f"Mouth <mouth@{HOST}>"


def test_a_spawned_process_never_signs_as_its_caller(env, tmp_path):
    """The same across a process boundary: a child that inherits the caller's whole
    environment, as ``ensure_player``'s ``mouth serve`` does, and drops a line."""
    e = {**os.environ, **CALLER}
    code = textwrap.dedent(f"""
        from mouth import notice
        print(notice.dropped({{"utt": "u1", "text": "x", "notice_to": "ivy@{HOST}"}},
                             {{"reason": "error", "cut": True}}))
    """)
    p = subprocess.run([sys.executable, "-c", code], env=e, cwd=tmp_path, capture_output=True,
                       text=True, timeout=60, check=False)
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == f"sent ivy@{HOST}"
    (sent,) = sends(env)
    _assert_the_mouth_seat(sent["env"])


def test_child_env_strips_the_caller_and_names_the_seat():
    env = notice.child_env({**CALLER, "PATH": "/bin", "SESSIONMAIL_HOST": "elsewhere",
                            "SESSIONMAIL_SENDMAIL": "drop"})
    _assert_the_mouth_seat({k: v for k, v in env.items()})
    assert env["PATH"] == "/bin" and env["SESSIONMAIL_SENDMAIL"] == "drop"   # the carrier passes


def test_the_mouth_signs_only_with_both_of_its_own_key_settings(tmp_path):
    """The A2 hook, kept small: both set, they become the send's key and must-sign
    is on; one alone is no key, and the notice goes unsigned (not refused)."""
    both = notice.child_env({**CALLER, "MOUTH_SIGNER_SOCK": "~/mouth.sock",
                             "MOUTH_SIGNER_PUB": str(tmp_path / "mouth.pub")})
    assert both["CLAUDE_SESSION_AGENT_SOCK"] == os.path.expanduser("~/mouth.sock")
    assert both["CLAUDE_SESSION_PUB"] == str(tmp_path / "mouth.pub")
    assert both["SESSIONMAIL_REQUIRE_SIGNATURE"] == "1"
    assert both["TM_AGENT_ADDRESS"] == "mouth"
    for half in ({"MOUTH_SIGNER_SOCK": "/s"}, {"MOUTH_SIGNER_PUB": "/p"}):
        env = notice.child_env({**CALLER, **half})
        assert not [k for k in env if k.startswith("CLAUDE_SESSION_")]
        assert not [k for k in notice.MUST_SIGN_VARS if k in env]


def test_the_signer_settings_are_fenced():
    assert "MOUTH_SIGNER_SOCK" not in os.environ and "MOUTH_SIGNER_PUB" not in os.environ


# -- what the send would refuse (rca-001 N1, N2, N5) ------------------------------------

@pytest.mark.parametrize("text", [
    "Café au lait — “please”, it’s ready…",
    "run `make test` then $(cat out) " + "and more " * 10,
    "日本語のテキスト",
], ids=["typographic", "could-execute", "cjk"])
def test_any_line_gets_a_subject_message_send_takes(env, text):
    """A non-ASCII subject, or one that could execute, is refused by ``message
    send``, and the notice would be lost. The subject is folded and defused; the
    body keeps the line as it was."""
    mkbox(env, "s-test")
    item = {"utt": "u1", "text": text, "session": "s-test"}
    assert notice.dropped(item, CUT) == f"sent s-test@{HOST}"
    (m,) = mails(env, "s-test")
    s = str(m["Subject"])
    assert s.isascii() and "`" not in s and "$(" not in s and len(s) <= notice.SUBJECT_MAX, s
    assert text in m.get_content()


def test_a_cut_subject_is_ascii_too():
    rec = {"reason": "barge-in", "played_s": 1.0, "cut_at_s": 1.0, "text_played_est": "one two"}
    s = notice.subject("one two three … `four`", rec)
    assert s == """cut at 1.0s, barge-in: "...three ... 'four'\"""", s


def test_the_id_host_is_pinned_whatever_the_caller_exported(env, monkeypatch):
    """N5: the id is minted at this host, and sessionmail refuses an id at any host
    but its own. A caller's ``SESSIONMAIL_HOST`` would have refused every notice."""
    monkeypatch.setenv("SESSIONMAIL_HOST", "not-this-host")
    mkbox(env, "s-test")
    assert notice.dropped({"utt": "u1", "text": "x", "session": "s-test"}, CUT) == \
        f"sent s-test@{HOST}"
    (m,) = mails(env, "s-test")
    assert m["Message-ID"].endswith(f".mouth-notice@{HOST}>")


def test_a_refusal_is_a_failed_row_naming_its_kind(env, monkeypatch):
    monkeypatch.setattr(notice, "subject", lambda text, rec: "a `subject` that could execute")
    mkbox(env, "s-test")
    out = notice.dropped({"utt": "u1", "text": "x", "session": "s-test"}, CUT)
    assert out.startswith("failed: message send exit 2 kind subject: the subject could execute")
    assert mails(env, "s-test") == []


def test_every_notice_says_it_is_automatic(env):
    """repro-001 R2: ``Auto-Submitted`` is set on every notice, as a header the send carries."""
    mkbox(env, "s-test")
    notice.dropped({"utt": "u1", "text": "x", "session": "s-test"}, CUT)
    (sent,) = sends(env)
    assert "Auto-Submitted: auto-generated" in sent["argv"]
    (m,) = mails(env, "s-test")
    assert m["Auto-Submitted"] == "auto-generated" and m["Mouth-Notice"] == "error"


# -- a bounce of a notice is never spoken (RULING R2-a) ---------------------------------

OLD_ID = f"<1921356800903984000.u1@{HOST}.mouth-notice>"
NEW_ID = f"<1921356800903984000.u1.mouth-notice@{HOST}>"
FQDN = f"{HOST}.tailnet.example"
_SAID = "This is the mail system. I'm sorry to have to inform you that your message could not be delivered."


def _original(mid: str = NEW_ID, *, notice_header: bool = True) -> str:
    lines = [f"From: Mouth <mouth@{HOST}>", f"To: ghost@{HOST}", f"Message-ID: {mid}",
             'Subject: not said, dropped: "the line"', "Auto-Submitted: auto-replied"]
    if notice_header:
        lines.append("Mouth-Notice: dropped")
    return "\n".join(lines) + "\n"


def _bounce(*, sender: str, report: bool = True, headers_only: bool = False,
            original: str | None = None) -> bytes:
    """A postfix bounce(8): multipart/report with a delivery-status part, or, with
    ``report=False``, a multipart/mixed carrying only the bounced mail."""
    original = _original() if original is None else original
    kind = "text/rfc822-headers" if headers_only else "message/rfc822"
    b = f"BOUNDARY.{int(ANCHOR.timestamp())}"
    ctype = (f'multipart/report; report-type=delivery-status; boundary="{b}"' if report
             else f'multipart/mixed; boundary="{b}"')
    parts = [f"Content-Type: text/plain; charset=us-ascii\n\n{_SAID}\n"]
    if report:
        parts.append("Content-Type: message/delivery-status\n\nReporting-MTA: dns; "
                     f"{FQDN}\n\nFinal-Recipient: rfc822; ghost@{FQDN}\nAction: failed\n"
                     "Status: 5.1.1\n")
    parts.append(f"Content-Type: {kind}\n\n{original}" + ("" if headers_only else "\nthe body\n"))
    body = "".join(f"--{b}\n{p}\n" for p in parts) + f"--{b}--\n"
    head = (f"Return-Path: <>\nFrom: {sender} (Mail Delivery System)\nTo: mouth@{HOST}\n"
            f"Subject: Undelivered Mail Returned to Sender\nAuto-Submitted: auto-replied\n"
            f"Message-ID: <{os.urandom(4).hex()}.bounce@{FQDN}>\nMIME-Version: 1.0\n"
            f"Content-Type: {ctype}\n\n")
    return (head + "This is a MIME-encapsulated message.\n\n" + body).encode()


def _drop(env, raw: bytes) -> None:
    env.n[0] += 1
    put(env.q, f"{os.urandom(6).hex()}.{env.n[0]}.{HOST}", raw)


def _spoken(env) -> list:
    return [r["text"] for r in rows(env.logs) if r["kind"] == "saying"]


@pytest.mark.parametrize("sender, headers_only", [
    (f"MAILER-DAEMON@{FQDN}", False), (f"MAILER-DAEMON@{HOST}", False),
    (f"MAILER-DAEMON@{FQDN}", True)], ids=["bounce-fqdn", "bounce-short", "bounce-headers-only"])
def test_a_bounce_of_a_notice_is_filed_unspoken(env, capfd, sender, headers_only):
    """The three shapes repro-001 R2 measured, each of which played before the fix."""
    _drop(env, _bounce(sender=sender, headers_only=headers_only))
    run_player(env.d).join(5)
    assert flags(env) == ["T"]
    assert rows(env.logs) == [] and sends(env) == []           # no saying, no said, no notice
    out = [json.loads(x) for x in capfd.readouterr().out.splitlines() if x.startswith("{")]
    assert {"skipped": "delivery-report"}.items() <= out[-1].items(), out


def test_a_bounce_is_hidden_from_readers(env):
    _drop(env, _bounce(sender=f"MAILER-DAEMON@{HOST}"))
    assert _box.Queue(env.d).lines() == []


def test_a_foreign_hosts_report_keeps_its_d11_refusal(env, capfd):
    """A report from another host is never spoken, and it is still refused (D11),
    not quietly filed as a bounce: the D11 test runs first."""
    _drop(env, _bounce(sender="MAILER-DAEMON@elsewhere.example"))
    run_player(env.d).join(5)
    assert flags(env) == ["T"] and _spoken(env) == []
    assert '"refused": "MAILER-DAEMON@elsewhere.example"' in capfd.readouterr().out
    assert [r["notice"] for r in said(env.logs).values()] == ["none: refused sender, not mailed"]


def test_a_plain_mail_from_this_hosts_daemon_follows_todays_rules(env):
    """No report, and nothing in it names a notice: MAILER-DAEMON alone is not a bounce."""
    m = email.message.EmailMessage()
    m["From"], m["To"], m["Subject"] = f"MAILER-DAEMON@{HOST}", f"mouth@{HOST}", "hello"
    m["Message-ID"] = f"<plain.1@{HOST}.session-mail>"
    m["Auto-Submitted"] = "auto-generated"                     # never keyed on alone
    m.set_content("the queue is clear")
    _drop(env, bytes(m))
    run_player(env.d).join(5)
    assert _spoken(env) == ["the queue is clear"]


@pytest.mark.parametrize("mid, notice_header", [(NEW_ID, False), (OLD_ID, False), (NEW_ID, True)],
                         ids=["new-id", "old-id", "mouth-notice-header"])
def test_a_daemons_non_report_naming_a_notice_is_a_bounce(env, mid, notice_header):
    """Test (ii): this host's MAILER-DAEMON, no delivery-status part, but the bounced
    mail inside is a notice: by either id form, or by ``Mouth-Notice``."""
    raw = _bounce(sender=f"MAILER-DAEMON@{HOST}", report=False,
                  original=_original(mid, notice_header=notice_header))
    _drop(env, raw)
    run_player(env.d).join(5)
    assert flags(env) == ["T"] and rows(env.logs) == []


def test_a_postmaster_non_report_naming_a_notice_is_a_bounce(env):
    _drop(env, _bounce(sender=f"postmaster@{HOST}", report=False, headers_only=True))
    run_player(env.d).join(5)
    assert flags(env) == ["T"] and rows(env.logs) == []


def test_a_daemons_non_report_naming_no_notice_plays(env):
    """Test (ii) needs a notice inside: a bounced mail that is not one is ordinary."""
    raw = _bounce(sender=f"MAILER-DAEMON@{HOST}", report=False,
                  original=_original(f"<x.1@{HOST}.session-mail>", notice_header=False))
    _drop(env, raw)
    run_player(env.d).join(5)
    assert _spoken(env) == [_SAID]

