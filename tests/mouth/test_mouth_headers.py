"""One bad mail header cannot kill the mouth or write outside it (EARS-168).

Parse-only, in a scratch queue: no player is served, no device is opened, no
port is touched. Each hole has a test that fails on 5e988bc:

1. ``X-Mouth-Requested`` nan/inf/-inf/1e20 raised in ``read()``
   (``datetime.fromtimestamp``), before ``allowed()``: treated as absent now.
2. ``X-Mouth-Utt`` (or an extra's ``utt``) went unchecked into
   ``d/done/<utt>.json``: replaced by the derived utt now.
3. ``X-Mouth-Extra`` could set the mail's own ``_`` facts (``_gone`` hid the
   line, and the line it supersedes): every ``_`` key is dropped now.
4. A ``read()`` that raises in ``Queue.entries`` stopped every reader and
   serve: the mail is filed aside (``T``) and logged, and the next one is read.
5. ``X-Mouth-Extra`` could set a numeric fact (``expires_t: "x"``) that
   ``read()`` let through and ``hold.check`` then raised on, in serve's
   ``_next``: the mail's own value is put back and logged now (fix-002, on 20638f8).
6. ``X-Mouth-Priority`` nan/inf/-inf/1e400 raised in ``read()`` (``int(nan)``,
   ``int(inf)``), so the mail was filed aside unspoken: any non-finite header
   number reads as absent now, and the mail is spoken (fix-003, on 1dea1eb).
"""

import hashlib
import json
from datetime import datetime

import pytest

from mouth import box, hold

from maildir_put import put
from tests import env_fence
from tests._anchor import ANCHOR


@pytest.fixture
def d(tmp_path, monkeypatch):
    d = tmp_path / "mouth"
    monkeypatch.setenv("MOUTH_DIR", str(d))
    monkeypatch.setenv("MOUTH_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MOUTH_DEVICE", "null")
    monkeypatch.setenv("MOUTH_BACKEND", "silence")
    env_fence.delenv(monkeypatch, "MOUTH_MAILBOX")
    env_fence.delenv(monkeypatch, "MOUTH_MAIL_LOG")
    box.ensure(d / "box")
    return d


def mail(d, name, headers, body="hello there\n"):
    """A mail from an allowed sender, delivered into new/."""
    host = box._host()
    hdr = "".join(f"{k}: {v}\n" for k, v in headers.items())
    return put(d / "box", name, f"From: ivy@{host}\nTo: mouth@{host}\nSubject: x\n{hdr}\n{body}")


def logged(capsys) -> list:
    return [json.loads(x) for x in capsys.readouterr().err.splitlines() if x.startswith("{")]


def derived(mid: str) -> str:
    return "m" + hashlib.sha1(mid.encode()).hexdigest()[:11]


# -- 1. X-Mouth-Requested: treated as absent --------------------------------

@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "1e20", "-1e20", "NaN", "garbage"])
def test_a_bad_requested_is_treated_as_absent(d, capsys, value):
    p = mail(d, "1.bad", {"Message-ID": "<r1@x>", "X-Mouth-Order": "1921356800000000000",
                          "X-Mouth-Requested": value})
    x = box.read(p, d)                                   # never raises
    assert x["requested_t"] == 1921356800.0, "falls back to the mail's own order"
    assert datetime.fromisoformat(x["requested_ts"]).timestamp() == 1921356800.0
    assert [r for r in logged(capsys) if r.get("bad_header") == "X-Mouth-Requested"] == [
        {"bad_header": "X-Mouth-Requested", "mail_id": "<r1@x>", "value": value, "as": "absent"}]


def test_a_good_requested_is_kept_and_not_logged(d, capsys):
    p = mail(d, "1.ok", {"Message-ID": "<r2@x>", "X-Mouth-Requested": "1921356923.5"})
    assert box.read(p, d)["requested_t"] == 1921356923.5
    assert logged(capsys) == []


def test_a_nan_requested_mail_does_not_stop_the_next(d):
    mail(d, "1.nan", {"Message-ID": "<n1@x>", "X-Mouth-Requested": "nan"}, body="first\n")
    mail(d, "2.good", {"Message-ID": "<g1@x>"}, body="second\n")
    q = box.Queue(d)
    assert sorted(x["mail_id"] for x in q.lines()) == ["<g1@x>", "<n1@x>"]
    assert sorted(p.name for p in (d / "box" / "new").iterdir()) == ["1.nan", "2.good"]


# -- 2. utt: one safe path component, or the derived one --------------------

@pytest.mark.parametrize("utt", ["../../x", "..", ".", "a/b", "/etc/x", "a\\b", "sp ace", "é", "x" * 129])
def test_a_bad_utt_header_is_replaced(d, capsys, utt):
    p = mail(d, "1.u", {"Message-ID": "<u1@x>", "X-Mouth-Utt": utt})
    x = box.read(p, d)
    assert x["utt"] == derived("<u1@x>")
    (r,) = [r for r in logged(capsys) if "bad_utt" in r]
    assert r["as"] == x["utt"] and r["mail_id"] == "<u1@x>"


@pytest.mark.parametrize("utt", ["../y", "", "a\u0000b", 7, None, ["x"]])
def test_a_bad_utt_from_extras_is_replaced(d, utt):
    p = mail(d, "1.e", {"Message-ID": "<e1@x>", "X-Mouth-Utt": "fine",
                        "X-Mouth-Extra": json.dumps({"utt": utt})})
    assert box.read(p, d)["utt"] == derived("<e1@x>")


@pytest.mark.parametrize("utt", ["abc123def456", "m0123456789a", "a.b-c_d", "x" * 128])
def test_a_good_utt_is_kept(d, capsys, utt):
    p = mail(d, "1.g", {"Message-ID": "<k1@x>", "X-Mouth-Utt": utt})
    assert box.read(p, d)["utt"] == utt
    assert logged(capsys) == []


def test_a_waited_utt_of_dotdot_writes_nothing_outside_done(d, tmp_path):
    """The real ``_done`` path, no player: a retract (lines(act=True)) of a
    ``X-Mouth-Wait: 1`` line writes its done/<utt>.json."""
    before = {p for p in tmp_path.rglob("*")}
    mail(d, "1.w", {"Message-ID": "<w1@x>", "X-Mouth-Wait": "1", "X-Mouth-Utt": "../../x"})
    mail(d, "2.r", {"Message-ID": "<w2@x>", "Supersedes": "<w1@x>"}, body="")
    assert box.Queue(d).lines(act=True) == []
    made = {p for p in tmp_path.rglob("*")} - before
    assert not [p for p in made if p.suffix == ".json" and p.parent != d / "done"]
    assert not (tmp_path / "x.json").exists() and not (d.parent.parent / "x.json").exists()
    assert (d / "done" / f"{derived('<w1@x>')}.json").exists()


# -- 3. extras never set a ``_`` fact -----------------------------------------

def test_extras_gone_cannot_hide_a_line_or_what_it_supersedes(d):
    """cf. EARS-164 reviews/2030-06-09T1625-design-001/gone_probe.py: it got []."""
    mail(d, "1.b", {"Message-ID": "<b1@x>"})
    mail(d, "2.a", {"Message-ID": "<a1@x>", "Supersedes": "<b1@x>",
                    "X-Mouth-Extra": json.dumps({"_gone": "x"})}, body="replaced text\n")
    mail(d, "3.c", {"Message-ID": "<c1@x>", "X-Mouth-Extra": json.dumps({"_gone": 1})})
    lines = box.Queue(d).lines()
    assert [x["mail_id"] for x in lines] == ["<a1@x>", "<c1@x>"]
    (a,) = [x for x in lines if x["mail_id"] == "<a1@x>"]
    assert [p["mail_id"] for p in a["_chain"]] == ["<b1@x>"], "a takes b's place, as any supersede"


def test_extras_gone_without_supersedes_is_still_a_line(d):
    mail(d, "1.c", {"Message-ID": "<c2@x>", "X-Mouth-Extra": json.dumps({"_gone": 1})})
    assert [x["mail_id"] for x in box.Queue(d).lines()] == ["<c2@x>"]


def test_extras_set_no_underscore_key_but_keep_the_rest(d):
    p = mail(d, "1.x", {"Message-ID": "<x1@x>", "X-Mouth-Extra": json.dumps(
        {"_gone": 1, "_whatever": 2, "_path": "/etc", "colour": "blue"})})
    x = box.read(p, d)
    assert "_gone" not in x and "_whatever" not in x and x["_path"] == p
    assert x["colour"] == "blue"


@pytest.mark.parametrize("extra", ["[1, 2]", "7", "\"s\"", "null"])
def test_extras_that_are_not_an_object_are_ignored(d, extra):
    p = mail(d, "1.n", {"Message-ID": "<j1@x>", "X-Mouth-Extra": extra})
    assert box.read(p, d)["mail_id"] == "<j1@x>"


# -- 4. one bad mail never stops the queue ------------------------------------

def test_a_mail_read_cannot_parse_is_filed_aside_and_the_next_is_read(d, monkeypatch, capsys):
    """A read() that raises: the queue guard files the mail T and goes on. No header
    makes read() raise now (X-Mouth-Priority: nan was the vehicle until fix-003), so
    read is made to raise a ValueError on this one file."""
    mail(d, "1.bad", {"Message-ID": "<p1@x>"}, body="bad\n")
    mail(d, "2.good", {"Message-ID": "<p2@x>"}, body="good\n")
    real = box.read

    def read(path, dd=None, **kw):
        if path.name == "1.bad":
            raise ValueError("cannot convert float NaN to integer")
        return real(path, dd, **kw)

    monkeypatch.setattr(box, "read", read)
    assert [x["mail_id"] for x in box.Queue(d).lines()] == ["<p2@x>"]
    assert [p.name for p in (d / "box" / "new").iterdir()] == ["2.good"]
    assert [p.name for p in (d / "box" / "cur").iterdir()] == ["1.bad:2,T"]
    (r,) = [r for r in logged(capsys) if "unreadable" in r]
    assert r["unreadable"] == "1.bad" and r["error"].startswith("ValueError")


def test_any_read_failure_is_filed_aside(d, monkeypatch, capsys):
    mail(d, "1.boom", {"Message-ID": "<q1@x>"})
    mail(d, "2.good", {"Message-ID": "<q2@x>"})
    real = box.read

    def read(path, dd=None, **kw):
        if path.name == "1.boom":
            raise OverflowError("boom")
        return real(path, dd, **kw)

    monkeypatch.setattr(box, "read", read)
    q = box.Queue(d)
    assert [x["mail_id"] for x in q.lines(act=True)] == ["<q2@x>"]
    assert [x["mail_id"] for x in q.lines()] == ["<q2@x>"], "once aside, never read again"
    assert (d / "box" / "cur" / "1.boom:2,T").exists()
    assert [r["unreadable"] for r in logged(capsys) if "unreadable" in r] == ["1.boom"]


# -- 5. extras never set a numeric fact the player or hold cannot do sums on --
#
# fix-002 (F1 of reviews/2030-06-09T1039-fix-001): an allowed mail with
# X-Mouth-Extra {"expires_t": "x"} passed read() and lines(), then hold.check's
# float(exp) raised inside serve's _next, and serve died with the mail in new/.
# Extras may still set these keys (and wait, text...: test_mouth_notice.py:957);
# a value that is not None or a finite real number is put back to the mail's own.

REQ = 1921356800.0          # the mail's own X-Mouth-Requested
NUMERIC_BAD = ["x", "", "1.5", True, [1], {"a": 1}, float("nan"), float("inf"), float("-inf"),
               10 ** 400]


def numeric_mail(d, extras, name="1.n", mid="<num1@x>", **more):
    headers = {"Message-ID": mid, "X-Mouth-Requested": repr(REQ), "X-Mouth-Expires": "30",
               "X-Mouth-Speed": "1.25", "X-Mouth-Start": "2", "X-Mouth-End": "4",
               "X-Mouth-Lead": "1.5", "X-Mouth-Pan": "left", **more,
               "X-Mouth-Extra": json.dumps(extras)}
    return mail(d, name, headers)


#: What the mail's own headers above give each fact (read() with no extras).
OWN = {"requested_t": REQ, "expires_s": 30.0, "expires_t": REQ + 30.0, "speed": 1.25,
       "start": 2.0, "end": 4.0, "lead_s": 1.5, "pan": -1.0}


@pytest.mark.parametrize("key", sorted(OWN))
@pytest.mark.parametrize("bad", NUMERIC_BAD, ids=repr)
def test_a_bad_numeric_extra_is_restored_to_the_mails_own_and_logged(d, capsys, key, bad):
    p = numeric_mail(d, {key: bad})
    x = box.read(p, d)                                   # never raises
    assert x[key] == OWN[key]
    (r,) = [r for r in logged(capsys) if "bad_extra" in r]
    assert r == {"bad_extra": key, "mail_id": "<num1@x>", "value": repr(bad)[:40], "as": OWN[key]}


@pytest.mark.parametrize("bad", [None, -1e20, 1e20, 253402318800.0 * 1000])
def test_requested_t_from_extras_must_be_a_time_a_clock_can_hold(d, capsys, bad):
    """None is fine for the rest, never for requested_t: player.py compares it with >."""
    x = box.read(numeric_mail(d, {"requested_t": bad}), d)
    assert x["requested_t"] == REQ
    assert [r["bad_extra"] for r in logged(capsys) if "bad_extra" in r] == ["requested_t"]


@pytest.mark.parametrize("key", sorted(k for k in OWN if k != "requested_t"))
def test_none_from_extras_is_kept_for_the_rest(d, capsys, key):
    x = box.read(numeric_mail(d, {key: None}), d)
    assert x[key] is None
    assert logged(capsys) == []


def test_good_numeric_extras_are_kept_and_not_logged(d, capsys):
    good = {"requested_t": 1921357300, "expires_t": 1921357400.5, "expires_s": 0, "speed": 0.8,
            "start": 0, "end": 9.5, "lead_s": 2.5, "pan": 0.25, "wait": True}
    x = box.read(numeric_mail(d, good), d)
    assert {k: x[k] for k in good} == good
    assert logged(capsys) == []


def test_requested_ts_follows_requested_t(d):
    def ts(t):
        return datetime.fromtimestamp(t).astimezone().isoformat(timespec="milliseconds")

    t = int(ANCHOR.timestamp())                         # any time but REQ
    x = box.read(numeric_mail(d, {"requested_t": t}), d)
    assert x["requested_ts"] == ts(t)
    x = box.read(numeric_mail(d, {"requested_t": "x", "requested_ts": "never"}, name="2.n"), d)
    assert x["requested_t"] == REQ and x["requested_ts"] == ts(REQ)
    x = box.read(numeric_mail(d, {"requested_ts": "a lie"}, name="3.n"), d)
    assert x["requested_ts"] == ts(REQ)


def test_a_bad_header_without_extras_is_not_logged_as_an_extra(d, capsys):
    """X-Mouth-Expires: nan is the header's own, read as absent (fix-003): no bad_extra."""
    x = box.read(mail(d, "1.h", {"Message-ID": "<h1@x>", "X-Mouth-Expires": "nan"}), d)
    assert x["expires_s"] is None and x["expires_t"] is None
    assert not [r for r in logged(capsys) if "bad_extra" in r]


@pytest.mark.parametrize("pan", [5.0, -1.5, 1.0000001])
def test_an_extras_pan_outside_minus_one_to_one_is_restored(d, capsys, pan):
    """One range for a pan, header or extra (fix-002 review M1): ``_pan`` refuses a header's."""
    x = box.read(numeric_mail(d, {"pan": pan}), d)
    assert x["pan"] == OWN["pan"]
    (r,) = [r for r in logged(capsys) if "bad_extra" in r]
    assert r["bad_extra"] == "pan" and r["as"] == OWN["pan"]


@pytest.mark.parametrize("pan", [-1.0, 0, 1, 0.5])
def test_an_extras_pan_inside_minus_one_to_one_is_kept(d, capsys, pan):
    assert box.read(numeric_mail(d, {"pan": pan}), d)["pan"] == pan
    assert not [r for r in logged(capsys) if "bad_extra" in r]


@pytest.fixture
def floor(tmp_path, monkeypatch):
    """A scratch heard log (no ears run): ``floor("partial")`` makes him speaking."""
    from ears import heard

    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm"))
    heard.log_dir().mkdir(parents=True, exist_ok=True)

    def write(kind, age_s=0.0):
        t = datetime.fromtimestamp(datetime.now().timestamp() - age_s)
        rec = {"ts": t.astimezone().isoformat(timespec="milliseconds"), "seq": 1, "kind": kind,
               "text": "x", "source": "mic"}
        with open(heard.log_path(), "a") as f:
            f.write(json.dumps(rec) + "\n")
        for c in hold._caches.values():
            c.update(t=0.0, v=None)
    for c in hold._caches.values():
        c.update(t=0.0, v=None)
    return write


BAD_FOR_HOLD = [{"expires_t": "x"}, {"expires_s": "x"}, {"requested_t": "x"},
                {"requested_t": None}, {"expires_t": [1]}, {"expires_s": {"a": 1}}]


@pytest.mark.parametrize("mode", ["unheld", "held-free", "held-speaking"])
@pytest.mark.parametrize("extras", BAD_FOR_HOLD, ids=lambda e: json.dumps(e))
def test_a_bad_extras_mail_then_a_good_one_never_raise_in_lines_or_hold(d, floor, extras, mode):
    """The serve path, no serve: lines() then hold.check() on each line, as
    player._next does (player.py:609). Unheld reads expires_t (the review's
    probe); held reads requested_t and expires_s, his floor free or not."""
    if mode == "held-speaking":
        floor("partial")
    more = {"X-Mouth-When": "turn-end"} if mode.startswith("held") else {}
    numeric_mail(d, extras, name="1.bad", mid="<bad@x>", **more)
    mail(d, "2.good", {"Message-ID": "<good@x>", "X-Mouth-Requested": repr(REQ),
                       "X-Mouth-Expires": "30"}, body="good\n")
    lines = box.Queue(d).lines()
    assert sorted(x["mail_id"] for x in lines) == ["<bad@x>", "<good@x>"]
    for x in lines:
        assert hold.check(x, now=REQ + 1) in ("play", "wait", "stale", "expire")
    (good,) = [x for x in lines if x["mail_id"] == "<good@x>"]
    assert hold.check(good, now=REQ + 1) == "play"
    assert hold.check(good, now=REQ + 31) == "expire"
    a, b = lines                                         # player.py:366 compares them
    # A "does not raise" probe: a str or None requested_t would raise TypeError here.
    assert (a["requested_t"] > b["requested_t"]) in (True, False)


# -- 6. a non-finite header number reads as absent ------------------------------
#
# fix-003 (super ruling): X-Mouth-Priority nan/inf/-inf/1e400 raised in read()
# (int(nan) is ValueError, int(inf) OverflowError; 1e400 parses to inf), so a mail whose
# only fault was its priority was filed aside unspoken. A numeric fact is None or
# finite: _float reads a non-finite header as absent, for every header it parses.

NON_FINITE = ["nan", "NaN", "inf", "-inf", "Infinity", "1e400", "-1e400",
              pytest.param("9" * 400, id="9x400")]


@pytest.mark.parametrize("value", NON_FINITE)
def test_a_non_finite_priority_is_read_as_absent(d, value):
    x = box.read(mail(d, "1.p", {"Message-ID": "<r1@x>", "X-Mouth-Priority": value}), d)
    assert x["priority"] is None and x["_rank"] == box.DEFAULT_RANK


@pytest.mark.parametrize("value", NON_FINITE)
def test_a_non_finite_priority_mail_is_spoken_not_filed_aside(d, capsys, value):
    mail(d, "1.p", {"Message-ID": "<r1@x>", "X-Mouth-Priority": value}, body="bad pri\n")
    mail(d, "2.g", {"Message-ID": "<r2@x>"}, body="good\n")
    assert sorted(x["mail_id"] for x in box.Queue(d).lines(act=True)) == ["<r1@x>", "<r2@x>"]
    assert not list((d / "box" / "cur").iterdir())
    assert not [r for r in logged(capsys) if "unreadable" in r]


@pytest.mark.parametrize("value,priority,rank", [
    ("10", 10, 10.0), ("2.5", 2.5, 2.5), ("-3", -3, -3.0), ("1e308", int(1e308), 1e308),
    ("now", "now", box.RANKS["now"]), ("garbage", None, box.DEFAULT_RANK)])
def test_a_finite_or_named_priority_is_kept(d, value, priority, rank, signed):
    # signed, and its h= covers the header: a now or a negative rank is then the mail's
    # own (EARS-335); this is the parse, not the trust
    x = box.read(mail(d, "1.p", {"Message-ID": "<r3@x>", "X-Mouth-Priority": value,
                                 "X-Session-Signature": signed("2", "x-mouth-priority")}), d)
    assert x["priority"] == priority and x["_rank"] == rank


@pytest.mark.parametrize("header,key", [
    ("X-Mouth-Speed", "speed"), ("X-Mouth-Start", "start"), ("X-Mouth-End", "end"),
    ("X-Mouth-Lead", "lead_s"), ("X-Mouth-Expires", "expires_s"), ("X-Mouth-Pan", "pan")])
@pytest.mark.parametrize("value", NON_FINITE)
def test_a_non_finite_header_number_is_read_as_absent(d, header, key, value):
    """The same rule for every header _float reads (fix-002 review M2)."""
    x = box.read(mail(d, "1.n", {"Message-ID": "<r4@x>", header: value}), d)
    assert x[key] is None
    if key == "expires_s":
        assert x["expires_t"] is None


def test_finite_header_numbers_are_kept(d):
    x = box.read(mail(d, "1.n", {"Message-ID": "<r5@x>", "X-Mouth-Speed": "1.5",
                                 "X-Mouth-Start": "0", "X-Mouth-End": "2.25",
                                 "X-Mouth-Lead": "3", "X-Mouth-Expires": "60",
                                 "X-Mouth-Pan": "-0.5"}), d)
    assert (x["speed"], x["start"], x["end"], x["lead_s"], x["expires_s"], x["pan"]) == \
        (1.5, 0.0, 2.25, 3.0, 60.0, -0.5)
    assert x["expires_t"] == x["requested_t"] + 60.0
