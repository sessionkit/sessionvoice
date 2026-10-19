"""Who may make the mouth speak, from the operator block (EARS-304 R2, R2a, R7, Q8).

``box.allowed()`` reads ``ears.operator_config.current()``:

    unconfigured (privileged empty):  accept = inALLOW or host            (today, byte for byte)
    configured, ALLOW unset:          accept = host or privileged
    configured, ALLOW set:            accept = (host or privileged) and inALLOW

and the player logs one ``allow-fallback`` row per message ALLOW admits while the
reader did not answer (off, CLI missing, bad output), plus one line at start.

Every test stubs ``session``: a script alone in a tmp bin dir (or nothing at all),
with ``MOUTH_SESSION_BIN`` unset only where a test switches the reader ON.
No test runs this host's ``session operator`` or reads its block.
"""
from __future__ import annotations

import json
import os
import shlex
import threading
import time
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace

import pytest

from ears import operator_config as oc
from mouth import box, inbox, player

from maildir_put import put
from tests import env_fence

HOST = box._host()
ALLOW = "MOUTH_MAIL_ALLOW"
STRANGER = "stranger@elsewhere.example"

CONFIGURED = {"operator": "you@host.example", "mailbox": "you", "privileged": ["you", "you@host.example"]}
UNCONFIGURED = {"operator": None, "mailbox": None, "privileged": []}
# AGENT_OPERATOR_ADDR alone: sessiontools moves `operator` and leaves `privileged` empty.
ADDR_ONLY = {"operator": "someone@elsewhere.example", "mailbox": None, "privileged": []}


def _today(sender: str) -> bool:
    """``allowed()`` as it was at 26e4a6f, kept verbatim: SC1's reference."""
    s = sender.lower()
    extra = [x.strip().lower() for x in os.environ.get(ALLOW, "").split(",") if x.strip()]
    if any(s == x or (x.startswith("@") and s.endswith(x)) for x in extra):
        return True
    at = s[s.rfind("@"):] if "@" in s else ""
    host = box._host()
    return at == f"@{host}" or at.startswith(f"@{host}.")


@pytest.fixture(autouse=True)
def _fresh():
    oc.clear()
    yield
    oc.clear()


@pytest.fixture
def session(tmp_path, monkeypatch):
    """``session(answer)`` / ``session(body=...)``: a fake ``session`` FIRST on PATH, reader ON.

    ``alone=True`` makes the stub's dir the whole PATH. ``session.runs()`` counts runs."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    count = tmp_path / "session-runs"
    path0 = os.environ.get("PATH", "")

    def make(answer=CONFIGURED, body=None, alone=False):
        monkeypatch.setenv("PATH", str(bindir) if alone else f"{bindir}{os.pathsep}{path0}")
        env_fence.delenv(monkeypatch, oc.SESSION_BIN_ENV)
        if body is None:
            out = answer if isinstance(answer, str) else json.dumps(answer)
            body = f"printf '%s\\n' {shlex.quote(out)}"
        exe = bindir / "session"
        exe.write_text("\n".join(["#!/bin/sh", f"echo run >> {shlex.quote(str(count))}",
                                  '[ "$1" = operator ] || exit 9', body, ""]))
        exe.chmod(0o755)
        oc.clear()

    make.runs = lambda: len(count.read_text().splitlines()) if count.exists() else 0

    def absent():
        monkeypatch.setenv("PATH", str(bindir))      # an empty dir: no session anywhere
        env_fence.delenv(monkeypatch, oc.SESSION_BIN_ENV)
        oc.clear()

    make.absent = absent
    return make


# -- R2: the truth table -----------------------------------------------------------

SENDERS = [f"vera@{HOST}", f"mouth@{HOST}.tail.example", f"VERA@{HOST.upper()}",
           STRANGER, "theo@example.com", "vera@host4.example.net", "you@host.example",
           "you@elsewhere.example", "nobody", ""]
ALLOWS = [None, "", " , ", "theo@example.com,@host4.example.net", STRANGER, f"@{HOST}"]


def _table(monkeypatch) -> list:
    out = []
    for a in ALLOWS:
        if a is None:
            env_fence.delenv(monkeypatch, ALLOW)
        else:
            monkeypatch.setenv(ALLOW, a)
        out.append([(a, s, box.allowed(s)) for s in SENDERS])
    return out


def _reference(monkeypatch) -> list:
    out = []
    for a in ALLOWS:
        if a is None:
            env_fence.delenv(monkeypatch, ALLOW)
        else:
            monkeypatch.setenv(ALLOW, a)
        out.append([(a, s, _today(s)) for s in SENDERS])
    return out


def test_the_reference_table_lets_allow_add_a_stranger(monkeypatch):
    # the table SC1 compares against really exercises ALLOW ADDING
    monkeypatch.setenv(ALLOW, STRANGER)
    assert _today(STRANGER) and not _today("other@elsewhere.example")


@pytest.mark.parametrize("case", ["off", "unconfigured", "addr-only", "absent", "exit2", "hang", "junk",
                                  "not-an-object", "privileged-not-a-list"])
def test_sc1_nothing_configured_is_byte_identical_to_today(session, monkeypatch, case):
    if case == "off":
        monkeypatch.setenv(oc.SESSION_BIN_ENV, "")
    elif case == "unconfigured":
        session(UNCONFIGURED)
    elif case == "addr-only":                        # R7: AGENT_OPERATOR_ADDR alone grants nothing
        session(ADDR_ONLY)
    elif case == "absent":
        session.absent()
    elif case == "exit2":                            # a host with no `operator` verb
        session(body="echo 'usage: session' >&2; exit 2")
    elif case == "hang":
        monkeypatch.setattr(oc, "TIMEOUT_S", 0.3)
        session(body="exec /bin/sleep 5", alone=True)
    elif case == "junk":
        session("this is not json")
    elif case == "not-an-object":
        session(["you"])
    elif case == "privileged-not-a-list":
        session({"operator": "you@host.example", "mailbox": "you", "privileged": "you"})
    assert _table(monkeypatch) == _reference(monkeypatch)


def test_sc1_the_same_table_with_and_without_the_stub(session, monkeypatch):
    monkeypatch.setenv(oc.SESSION_BIN_ENV, "")
    without = _table(monkeypatch)
    session(UNCONFIGURED)
    with_stub = _table(monkeypatch)
    assert session.runs() == 1                       # read, and cached across the whole table
    assert with_stub == without


def test_sc2_a_privileged_seat_on_another_host_is_admitted(session, monkeypatch):
    session({"operator": "you@host.example", "mailbox": "you", "privileged": ["vera@otherhost.example"]})
    env_fence.delenv(monkeypatch, ALLOW)
    assert box.allowed("vera@otherhost.example")
    assert box.allowed("VERA@OtherHost.Example")                 # case-insensitive
    assert not box.allowed("stranger@otherhost.example")         # not privileged, host or ALLOW
    assert not box.allowed(STRANGER)
    assert box.allowed(f"mouth@{HOST}")                          # this host still speaks


def test_a_bare_privileged_entry_is_this_host_only(session, monkeypatch):
    session({"operator": None, "mailbox": "vera", "privileged": ["vera"]})
    env_fence.delenv(monkeypatch, ALLOW)
    assert box.allowed(f"vera@{HOST}") and box.allowed(f"vera@{HOST}.tail.example")
    assert not box.allowed("vera@otherhost.example")


def test_a_configured_allow_never_adds(session, monkeypatch):
    # (a): the sender is only in ALLOW, not privileged and not on this host
    session(CONFIGURED)
    monkeypatch.setenv(ALLOW, STRANGER)
    assert not box.allowed(STRANGER)
    monkeypatch.setenv(ALLOW, "@elsewhere.example")              # an @suffix entry adds nothing either
    assert not box.allowed(STRANGER)


def test_a_configured_allow_narrows_by_intersection(session, monkeypatch):
    # (b): privileged a and b, ALLOW b
    session({"operator": "a@x.example", "mailbox": None, "privileged": ["a@x.example", "b@x.example"]})
    monkeypatch.setenv(ALLOW, "b@x.example")
    assert not box.allowed("a@x.example")                        # privileged, not in ALLOW
    assert box.allowed("b@x.example")                            # privileged and in ALLOW
    env_fence.delenv(monkeypatch, ALLOW)
    assert box.allowed("a@x.example") and box.allowed("b@x.example")


def test_a_local_seat_under_narrowing(session, monkeypatch):
    session({"operator": "b@x.example", "mailbox": None, "privileged": ["b@x.example"]})
    monkeypatch.setenv(ALLOW, "b@x.example")
    assert not box.allowed(f"mouth@{HOST}")                      # narrowing drops local seats too
    monkeypatch.setenv(ALLOW, f"@{HOST},b@x.example")
    assert box.allowed(f"mouth@{HOST}") and box.allowed("b@x.example")


@pytest.mark.parametrize("blank", ["", " , "])
def test_a_blank_allow_is_unset_when_configured(session, monkeypatch, blank):
    session({"operator": None, "mailbox": None, "privileged": ["b@x.example"]})
    monkeypatch.setenv(ALLOW, blank)
    assert box.allowed("b@x.example") and box.allowed(f"mouth@{HOST}")
    assert not box.allowed(STRANGER)


def test_agent_operator_addr_alone_is_unconfigured(session, monkeypatch):
    session(ADDR_ONLY)
    monkeypatch.setenv(ALLOW, STRANGER)
    assert box.allowed(STRANGER)                                  # ALLOW still adds, as today
    assert not box.allowed("someone@elsewhere.example")           # the moved address gains nothing
    assert box.allowed(f"mouth@{HOST}")


def test_the_answer_is_cached_across_calls(session, monkeypatch):
    session(CONFIGURED)
    env_fence.delenv(monkeypatch, ALLOW)
    for s in SENDERS:
        box.allowed(s)
    assert session.runs() == 1
    oc.clear()
    box.allowed(STRANGER)
    assert session.runs() == 2


# -- the player: refusal detail and the Q8 warning -----------------------------------

@pytest.fixture
def mbox(tmp_path, monkeypatch):
    d, logs, mail = tmp_path / "mouth", tmp_path / "logs", tmp_path / "maildir"
    for sub in ("new", "cur", "tmp"):
        (mail / sub).mkdir(parents=True)
    monkeypatch.setenv("MOUTH_DIR", str(d))
    monkeypatch.setenv("MOUTH_MAILBOX", str(mail))
    monkeypatch.setenv("MOUTH_LOG_DIR", str(logs))
    monkeypatch.setenv("MOUTH_DEVICE", "null")
    monkeypatch.setenv("MOUTH_BACKEND", "silence")
    monkeypatch.setenv("MOUTH_IDLE_S", "1")
    monkeypatch.setenv("MOUTH_RESUME_S", "0")
    env_fence.delenv(monkeypatch, ALLOW)
    return SimpleNamespace(d=d, logs=logs, mail=mail, n=[0])


def deliver(b, body="hello", sender=STRANGER, **headers) -> str:
    b.n[0] += 1
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = sender, f"mouth@{HOST}", "hi"
    mid = f"<{b.n[0]}.test@{HOST}.session-mail>"
    m["Message-ID"] = mid
    for k, v in headers.items():
        m[k.replace("_", "-")] = v
    m.set_content(body)
    put(b.mail, f"{time.time_ns()}.{b.n[0]}.{HOST}", bytes(m))
    return mid


def rows(logs) -> list:
    return [json.loads(x) for f in sorted(Path(logs).glob("heard_*.jsonl")) for x in f.read_text().splitlines()]


def play(b) -> None:
    t = threading.Thread(target=player.serve, kwargs={"d": b.d, "idle_exit_s": 0.3}, daemon=True)
    t.start()
    t.join(5)


def warnings(err: str) -> list:
    out = []
    for line in err.splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and r.get("warning") == "allow-fallback":
            out.append(r)
    return out


def _refusal_detail(b) -> str:
    deliver(b, "buy now")
    play(b)
    refused = [r for r in rows(b.logs) if r.get("kind") == "said" and r.get("reason") == "refused"]
    assert len(refused) == 1
    return refused[0]["detail"]


def test_the_unconfigured_refusal_detail_is_unchanged(mbox):
    # reader off (tests/conftest.py): the text test_mouth_notice.py asserts, byte for byte
    assert _refusal_detail(mbox) == f"From {STRANGER} is not on this host or in $MOUTH_MAIL_ALLOW"


def test_the_configured_refusal_detail_without_allow(mbox, session):
    session(CONFIGURED)
    assert _refusal_detail(mbox) == f"From {STRANGER} is not on this host or in the operator's privileged list"


def test_the_configured_refusal_detail_with_allow(mbox, session, monkeypatch):
    session(CONFIGURED)
    monkeypatch.setenv(ALLOW, STRANGER)              # ALLOW names him, and still cannot add him
    assert _refusal_detail(mbox) == (
        f"From {STRANGER} is not both (on this host or privileged) and in $MOUTH_MAIL_ALLOW")


def test_allow_riding_the_fallback_warns_once_per_message(mbox, monkeypatch, capfd):
    monkeypatch.setenv(ALLOW, STRANGER)              # reader off: conftest's ""
    mid = deliver(mbox, "let me in")
    q = box.Queue(mbox.d)
    q.lines(act=True)
    q.lines(act=True)                                # two scans, one row
    got = warnings(capfd.readouterr().err)
    assert got == [{"warning": "allow-fallback", "from": STRANGER, "mail_id": mid, "reason": "off"}]


def test_each_message_gets_its_own_row_and_it_is_spoken(mbox, monkeypatch, capfd):
    monkeypatch.setenv(ALLOW, STRANGER)
    a, b = deliver(mbox, "one"), deliver(mbox, "two")
    play(mbox)
    got = warnings(capfd.readouterr().err)
    assert [(r["mail_id"], r["from"], r["reason"]) for r in got] == [(a, STRANGER, "off"), (b, STRANGER, "off")]
    assert [r["text"] for r in rows(mbox.logs) if r["kind"] == "saying"] == ["one", "two"]


def test_x_mouth_raw_cannot_hide_the_warning(mbox, monkeypatch, capfd):
    monkeypatch.setenv(ALLOW, STRANGER)
    mid = deliver(mbox, "raw", X_Mouth_Raw="1")
    q = box.Queue(mbox.d)
    q.lines(act=True)
    q.lines(act=True)
    assert [(r["mail_id"], r["reason"]) for r in warnings(capfd.readouterr().err)] == [(mid, "off")]


@pytest.mark.parametrize("reader,code", [("absent", "absent"), ("junk", "failed")])
def test_the_row_names_the_reason_code(mbox, session, monkeypatch, capfd, reader, code):
    if reader == "absent":
        session.absent()
    else:
        session("not json")
    monkeypatch.setenv(ALLOW, STRANGER)
    deliver(mbox)
    box.Queue(mbox.d).lines(act=True)
    got = warnings(capfd.readouterr().err)
    assert [r["reason"] for r in got] == [code]
    assert got[0]["reason"] != oc.reason_text(code)               # the code word, not the text


def test_a_notice_reply_from_an_allow_stranger_gives_no_row(mbox, monkeypatch, capfd):
    monkeypatch.setenv(ALLOW, STRANGER)
    deliver(mbox, "re: your line", In_Reply_To=f"<1.abc@{HOST}.mouth-notice>")
    q = box.Queue(mbox.d)
    q.lines(act=True)
    assert warnings(capfd.readouterr().err) == []


def test_no_row_for_a_local_sender(mbox, monkeypatch, capfd):
    monkeypatch.setenv(ALLOW, f"vera@{HOST}")
    deliver(mbox, sender=f"vera@{HOST}")
    box.Queue(mbox.d).lines(act=True)
    assert warnings(capfd.readouterr().err) == []


def test_no_row_when_allow_is_unset(mbox, capfd):
    deliver(mbox)                                    # refused, not admitted via ALLOW
    deliver(mbox, sender=f"vera@{HOST}")
    box.Queue(mbox.d).lines(act=True)
    assert warnings(capfd.readouterr().err) == []


def test_no_row_when_the_reader_answers_unconfigured(mbox, session, monkeypatch, capfd):
    session(UNCONFIGURED)
    monkeypatch.setenv(ALLOW, STRANGER)
    deliver(mbox)
    q = box.Queue(mbox.d)
    assert [e["_from"] for e in q.lines(act=True)] == [STRANGER]  # ALLOW adds: the designed fallback
    assert warnings(capfd.readouterr().err) == []


def test_no_row_when_the_reader_answers_configured(mbox, session, monkeypatch, capfd):
    session({"operator": None, "mailbox": None, "privileged": [STRANGER]})
    monkeypatch.setenv(ALLOW, STRANGER)
    deliver(mbox)
    assert [e["_from"] for e in box.Queue(mbox.d).lines(act=True)] == [STRANGER]
    assert warnings(capfd.readouterr().err) == []


def test_a_reader_lines_scan_never_warns(mbox, monkeypatch, capfd):
    monkeypatch.setenv(ALLOW, STRANGER)
    deliver(mbox)
    box.Queue(mbox.d).lines(act=False)               # status / timeline: a reader, not the player
    assert warnings(capfd.readouterr().err) == []


# -- the resident start line ----------------------------------------------------------

class _Stop(Exception):
    pass


def _start(mbox, monkeypatch) -> None:
    def serve(**_kw):
        raise _Stop
    monkeypatch.setattr(player, "serve", serve)
    with pytest.raises(_Stop):
        inbox._watch(box.box_dir(), 0.0, False)


def test_the_start_line_names_the_reason_once(mbox, monkeypatch, capfd):
    monkeypatch.setenv(ALLOW, STRANGER)
    _start(mbox, monkeypatch)
    err = capfd.readouterr().err
    want = oc.ALLOW_FALLBACK.format(reason=oc.reason_text("off"))
    assert err.count(want) == 1 and "configured host" not in err


@pytest.mark.parametrize("reader", ["allow-unset", "unconfigured", "configured"])
def test_no_start_line(mbox, session, monkeypatch, capfd, reader):
    if reader == "allow-unset":
        env_fence.delenv(monkeypatch, ALLOW)
    else:
        session(UNCONFIGURED if reader == "unconfigured" else CONFIGURED)
        monkeypatch.setenv(ALLOW, STRANGER)
    _start(mbox, monkeypatch)
    assert "operator reader" not in capfd.readouterr().err


def test_once_mode_has_no_start_line(mbox, monkeypatch, capfd):
    monkeypatch.setenv(ALLOW, STRANGER)
    monkeypatch.setattr(player, "serve", lambda **_kw: 0)
    assert inbox._watch(box.box_dir(), 0.0, True) == 0
    assert "operator reader" not in capfd.readouterr().err
