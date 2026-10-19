"""The gate takes what is signed from the verifier's verdict, not its own parse (EARS-345).

EARS-335 gates ``call:N``, ``now`` and ``Supersedes`` on a signed line. Until EARS-345 the
mouth asked ``message verify --trust``, kept only the exit code (0 or 4), then read the
version, the ``h=`` and every field VALUE from its own parse of the mail. sessionmail
prints a machine-readable verdict (``message verify --json``, schema 1, SM-788): the
version and ``h`` it verified, and each covered field's value read back from the bytes
``ssh-keygen`` verified (sessionmail ``docs/verify-json-verdict.md``). Wherever the two
readings differ, the verdict is the truth, and a mail that has none fails closed.

Each case here makes them differ, with a fake ``message`` on PATH (never this host's
real one): it records its argv, prints the verdict object only when asked ``--json`` (as
the real one does), and exits with the code the verdict names. The mail's own
``X-Session-Signature`` always claims to cover the ask.

- (a) the verdict's ``h`` (or version) does not cover the gated field, while the mail's
  ``h=`` does: refused.
- (b) the verdict's ``covered`` value for a gated field differs from the value Python's
  ``email`` parses from the mail: refused (never the parsed value).
- (c) no object, an unparseable one, ``values_b64``, a null signature, a trust verdict
  outside FULL/UNBLESSED, or a gated field signed twice: fail closed.

These were strict xfails at repro-001 (EARS-345); fix-001 made them pass. The controls
at the bottom: a verdict that agrees with the mail keeps its asks.

In tmp_path, on the ``null`` device and the ``silence`` backend.
"""

import json
import shutil
from pathlib import Path

import pytest

from mouth import box

from maildir_put import put
from tests import env_fence

DIGEST = "A" * 43 + "="
CAT = shutil.which("cat")   # the fake's PATH holds only itself
V1_H = ["from", "to", "subject", "date", "message-id"]


@pytest.fixture
def d(tmp_path, monkeypatch):
    d = tmp_path / "mouth"
    monkeypatch.setenv("MOUTH_DIR", str(d))
    monkeypatch.setenv("MOUTH_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MOUTH_DEVICE", "null")
    monkeypatch.setenv("MOUTH_BACKEND", "silence")
    env_fence.delenv(monkeypatch, "MOUTH_MAILBOX")
    env_fence.delenv(monkeypatch, "MOUTH_MAIL_LOG")
    box.ensure(box.box_dir(d))
    return d


def sig(*names: str) -> str:
    """A v2 ``X-Session-Signature`` as the sender writes it, its ``h=`` naming NAMES."""
    return f"v=2; a=sshsig; n=test; h={': '.join(['from', 'to', *names])}; p={DIGEST}; b=fake"


def mail(d, name, headers=(), *, names=(), to=None, body="hello there") -> Path:
    """A signed-looking mail delivered into new/ (tmp/, then a rename): an on-host From,
    its h= covering NAMES."""
    host = box._host()
    lines = [f"From: ivy@{host}", f"To: {to or f'mouth@{host}'}", f"Subject: {body[:40]}",
             f"Message-ID: <{name}@test>", f"X-Session-Signature: {sig(*names)}"]
    lines += [f"{k}: {v}" for k, v in dict(headers).items()]
    return put(box.box_dir(d), name, "\n".join(lines) + "\n\n" + body + "\n")


def verdict(covered: dict, *, rc: int = 4, version: str = "2", h=None,
            trust: object = "default", signature: object = "default") -> dict:
    """A schema-1 ``message verify --json --trust`` object (sessionmail
    ``docs/verify-json-verdict.md``). COVERED maps each signed name to its values (a
    list), or to ``{"values_b64": [...]}``; ``h`` defaults to COVERED's names, in order."""
    names = list(covered) if h is None else list(h)
    cov = []
    for n in names:
        v = covered.get(n, [""])   # a field the mail lacks is signed as empty
        cov.append({"name": n, **(v if isinstance(v, dict) else {"values": list(v)})})
    if signature == "default":
        signature = {"version": version, "h": names, "covered": cov,
                     "parts": [{"index": 1, "type": "text/plain", "digest": DIGEST}],
                     "signer": {"key": "ssh-ed25519 AAAAfake", "session_id": "fake"}}
    if trust == "default":
        trust = ({"verdict": "FULL", "leg": None, "reason": None, "agent": "ivy",
                  "line": "TRUST: FULL"} if rc == 0 else
                 {"verdict": "UNBLESSED", "leg": None, "reason": "bless-absent", "agent": "ivy",
                  "line": "TRUST: UNBLESSED reason=bless-absent"})
    return {"schema": 1, "rc": rc, "result": "good",
            "reason": None if rc == 0 else "bless-absent", "signature": signature, "trust": trust}


def fake_verifier(tmp_path, monkeypatch, rc: int, out) -> Path:
    """``message`` on PATH: logs its argv, prints OUT (a dict as one JSON line, a str as
    given) only when called with ``--json``, and exits RC. Nothing else is on PATH."""
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    stdout = tmp_path / "verdict.out"
    stdout.write_text(json.dumps(out, sort_keys=True) + "\n" if isinstance(out, dict) else out)
    m = bin_ / "message"
    m.write_text("#!/bin/sh\n"
                 f"echo \"$@\" >> '{tmp_path / 'argv.txt'}'\n"
                 f"case \" $* \" in *\" --json \"*) '{CAT}' '{stdout}' ;; esac\n"
                 f"exit {rc}\n")
    m.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_))
    env_fence.delenv(monkeypatch, box.VERIFY_VAR)
    return m


def judged(d, tmp_path, monkeypatch, p: Path, rc: int, out) -> dict:
    """The line the mouth makes of P with the fake verifier's verdict, waited for."""
    fake_verifier(tmp_path, monkeypatch, rc, out)
    x = box.read(p, d, sync=True)
    assert (tmp_path / "argv.txt").exists(), "the verifier was never asked"
    return x


def in_call(x: dict) -> bool:
    return not x.get("_untrusted") and box._is_call(x.get("device"))


def cuts(x: dict) -> bool:
    return not x.get("_untrusted") and (x.get("priority") == "now" or x["_rank"] < 0)


def mail_values(**kv) -> dict:
    """The covered values the mail itself carries (the floor's from and to included)."""
    host = box._host()
    return {"from": [f"ivy@{host}"], "to": [f"mouth@{host}"], **{k: [v] for k, v in kv.items()}}


# -- (a) the verdict's h or version does not cover what the mail's h= claims --------------

def test_a_call_the_verdicts_h_does_not_cover_is_refused(d, tmp_path, monkeypatch):
    p = mail(d, "1.a", {"X-Mouth-Device": "call:1317"}, names=["x-mouth-device"])
    x = judged(d, tmp_path, monkeypatch, p, 4, verdict(mail_values()))   # h = from, to
    assert not in_call(x), f"the mail's own h= routed it into {x['device']}"


def test_a_now_the_verdicts_h_does_not_cover_is_dropped(d, tmp_path, monkeypatch):
    p = mail(d, "1.n", {"X-Mouth-Priority": "now"}, names=["x-mouth-priority"])
    x = judged(d, tmp_path, monkeypatch, p, 4, verdict(mail_values()))
    assert not cuts(x), f"the mail's own h= kept its now: {x['priority']!r} rank {x['_rank']}"


def test_a_supersede_the_verdicts_h_does_not_cover_is_not_trusted(d, tmp_path, monkeypatch):
    p = mail(d, "1.s", {"Supersedes": "<other@test>"}, names=["supersedes"])
    x = judged(d, tmp_path, monkeypatch, p, 4, verdict(mail_values()))
    assert not x["_sup_trust"], "the mail's own h= made its Supersedes trusted"


def test_the_version_is_the_verdicts_not_the_mails(d, tmp_path, monkeypatch):
    # the mail says v=2 and names the field; the verifier verified a v1 signature
    p = mail(d, "1.v", {"X-Mouth-Device": "call:1317"}, names=["x-mouth-device"])
    x = judged(d, tmp_path, monkeypatch, p, 4,
               verdict(mail_values(), version="1", h=V1_H))
    assert not in_call(x), f"a v1 verdict routed a v2-looking mail into {x['device']}"


# -- (b) the verdict's covered value differs from the mail's parse ------------------------

def test_a_call_device_is_the_covered_value_never_the_parsed_one(d, tmp_path, monkeypatch):
    p = mail(d, "1.b", {"X-Mouth-Device": "call:1317"}, names=["x-mouth-device"])
    x = judged(d, tmp_path, monkeypatch, p, 4,
               verdict(mail_values(**{"x-mouth-device": "call:9"})))
    assert not (in_call(x) and x["device"] == "call:1317"), \
        "the device is Python's parse (call:1317), not the signed value (call:9)"


def test_a_now_is_the_covered_value_never_the_parsed_one(d, tmp_path, monkeypatch):
    p = mail(d, "1.p", {"X-Mouth-Priority": "now"}, names=["x-mouth-priority"])
    x = judged(d, tmp_path, monkeypatch, p, 4,
               verdict(mail_values(**{"x-mouth-priority": "next"})))
    assert not cuts(x), "the signed value is next, yet the parsed now cuts in"


def test_a_call_box_is_the_covered_to_never_the_parsed_one(d, tmp_path, monkeypatch):
    host = box._host()
    p = mail(d, "1.t", to=f"calls+ivy-1317@{host}")
    x = judged(d, tmp_path, monkeypatch, p, 4, verdict(mail_values()))   # to: mouth@host
    assert not in_call(x), f"the parsed To routed it into {x['device']}; the signed To is mouth@"


# -- (c) no verdict, or one that is not a yes: fail closed --------------------------------

def _call_mail(d):
    return mail(d, "1.c", {"X-Mouth-Device": "call:1317"}, names=["x-mouth-device"])


_GOOD = mail_values(**{"x-mouth-device": "call:1317"})
_CLOSED = {
    "no-object": "",
    "unparseable": '{"schema": 1, "rc": 4, "signature": {\n',
    "not-an-object": "[]\n",
    "signature-null": verdict(_GOOD, signature=None),
    "trust-null": verdict(_GOOD, trust=None),
    "trust-fail": verdict(_GOOD, trust={"verdict": "FAIL", "leg": 3, "reason": "bless-invalid",
                                        "agent": "ivy", "line": "TRUST: FAIL leg=3 reason=bless-invalid"}),
    "values-b64": verdict({**_GOOD, "x-mouth-device": {"values_b64": ["Y2FsbDoxMzE3"]}}),
    "signed-twice": verdict({**_GOOD, "x-mouth-device": ["call:1317", "call:9"]}),
}


@pytest.mark.parametrize("case", list(_CLOSED), ids=list(_CLOSED))
def test_no_usable_verdict_fails_closed(d, tmp_path, monkeypatch, case):
    x = judged(d, tmp_path, monkeypatch, _call_mail(d), 4, _CLOSED[case])
    assert not in_call(x), f"{case}: exit 4 alone routed it into {x['device']}"


# -- controls: a verdict that agrees with the mail keeps its asks (pass today, and after) --

@pytest.mark.parametrize("rc", [0, 4])
def test_an_agreeing_verdict_keeps_the_call_and_the_now(d, tmp_path, monkeypatch, rc):
    p = mail(d, "1.k", {"X-Mouth-Device": "call:1317", "X-Mouth-Priority": "now"},
             names=["x-mouth-device", "x-mouth-priority"])
    x = judged(d, tmp_path, monkeypatch, p, rc, verdict(
        mail_values(**{"x-mouth-device": "call:1317", "x-mouth-priority": "now"}), rc=rc))
    assert in_call(x) and x["device"] == "call:1317"
    assert cuts(x) and x["priority"] == "now"


def test_a_verdict_that_is_no_still_refuses(d, tmp_path, monkeypatch):
    out = {"schema": 1, "rc": 1, "result": "bad", "reason": "bad-signature", "signature": None,
           "trust": {"verdict": "FAIL", "leg": 1, "reason": "bad-signature", "agent": None,
                     "line": "TRUST: FAIL leg=1 reason=bad-signature"}}
    x = judged(d, tmp_path, monkeypatch, _call_mail(d), 1, out)
    assert not in_call(x)


# -- (d) every gated field is compared, value for value (fix-001) ------------------------

def test_an_extras_device_is_kept_only_when_its_signed_key_is_the_one_read(d, tmp_path, monkeypatch):
    """X-Mouth-Extra is compared on the one key that set the effect: other keys, and the
    whitespace the signer collapses, never refuse an honest extra."""
    ext = json.dumps({"device": "call:1317", "volume": 40})
    p = mail(d, "1.e", {"X-Mouth-Extra": ext}, names=["x-mouth-extra"])
    signed_same = json.dumps({"volume": 50, "device": "call:1317"}, separators=(",", ":"))
    x = judged(d, tmp_path, monkeypatch, p, 4, verdict(mail_values(**{"x-mouth-extra": signed_same})))
    assert in_call(x) and x["device"] == "call:1317"


@pytest.mark.parametrize("signed_ext", [
    '{"device": "call:9"}',          # another call
    '{"volume": 40}',                # no device at all
    '{"_device": "call:1317"}',      # a _ key is never an extra
    '{"device": "call:1317"',        # not JSON
    '["device", "call:1317"]',       # not an object
])
def test_an_extras_device_signed_otherwise_is_refused(d, tmp_path, monkeypatch, signed_ext):
    p = mail(d, "1.e", {"X-Mouth-Extra": json.dumps({"device": "call:1317"})}, names=["x-mouth-extra"])
    x = judged(d, tmp_path, monkeypatch, p, 4, verdict(mail_values(**{"x-mouth-extra": signed_ext})))
    assert not in_call(x)
    assert "but the device its x-mouth-extra signs is not the one read" in x["_untrusted"]


def test_an_extras_now_signed_otherwise_is_dropped(d, tmp_path, monkeypatch):
    p = mail(d, "1.e", {"X-Mouth-Extra": json.dumps({"priority": "now"})}, names=["x-mouth-extra"])
    x = judged(d, tmp_path, monkeypatch, p, 4,
               verdict(mail_values(**{"x-mouth-extra": '{"priority": "NOW"}'})))
    assert not cuts(x), "a signed NOW is not the now read"


def test_a_barge_is_kept_only_when_its_when_is_the_signed_one(d, tmp_path, monkeypatch):
    host = box._host()
    to = f"calls+ivy-1317@{host}"
    calls = {**mail_values(**{"x-mouth-when": "now"}), "to": [to]}
    p = mail(d, "1.w", {"X-Mouth-When": "now"}, names=["x-mouth-when"], to=to)
    x = judged(d, tmp_path, monkeypatch, p, 4, verdict(calls))
    assert in_call(x) and x["hold"] is None, "signed and the same: it barges"
    p = mail(d, "2.w", {"X-Mouth-When": "now"}, names=["x-mouth-when"], to=to)
    x = judged(d, tmp_path, monkeypatch, p, 4, verdict({**calls, "x-mouth-when": ["turn-end"]}))
    assert in_call(x) and x["hold"] == "turn-end", "signed turn-end: it waits"


def test_a_supersede_is_trusted_only_for_the_signed_target(d, tmp_path, monkeypatch):
    p = mail(d, "1.s", {"Supersedes": "<other@test>"}, names=["supersedes"])
    x = judged(d, tmp_path, monkeypatch, p, 4, verdict(mail_values(supersedes="<other@test>")))
    assert x["_sup_trust"] and x["_sup_why"] is None
    p = mail(d, "2.s", {"Supersedes": "<other@test>"}, names=["supersedes"])
    x = judged(d, tmp_path, monkeypatch, p, 4, verdict(mail_values(supersedes="<mine@test>")))
    assert not x["_sup_trust"]
    assert x["_sup_why"] == "signed v2, but the supersedes it signs is not the one read"


def test_a_v1_verdict_never_trusts_a_supersede(d, tmp_path, monkeypatch):
    p = mail(d, "1.s", {"Supersedes": "<other@test>"}, names=["supersedes"])
    x = judged(d, tmp_path, monkeypatch, p, 0, verdict(mail_values(), rc=0, version="1", h=V1_H))
    assert not x["_sup_trust"] and x["_sup_why"] == "signed v1, which does not sign supersedes"


def test_a_covered_field_signed_empty_is_not_covered(d, tmp_path, monkeypatch):
    """An absent field is signed as empty: a field Python reads that the verifier
    signed as ``""`` was not signed (a relay added it, or hid it from the split)."""
    p = mail(d, "1.c", {"X-Mouth-Device": "call:1317"}, names=["x-mouth-device"])
    x = judged(d, tmp_path, monkeypatch, p, 4, verdict(mail_values(**{"x-mouth-device": ""})))
    assert not in_call(x) and "but it signs no x-mouth-device" in x["_untrusted"]
