"""Every place a mail can make the player write is fenced (EARS-303 fix-001).

The repro (``test_mouth_mail_log_dir.py``) pins the ``X-Mouth-Log-Dir`` header
and ``X-Mouth-Order``. This file covers the other doors the RCA and the super's
SC4b ruling named, and the CLI's up-front refusal (SC3):

- ``X-Mouth-Extra`` may never set ``log_dir``, ``mail_log`` or ``log_root`` the
  mouth set itself (``item.update(extras)`` ran after them on 26e4a6f);
- ``X-Mouth-Raw: 1`` (any sender can set it) with ``X-Mouth-Log-Dir`` or
  ``X-Mouth-Mail-Log`` is confined exactly as a mail from outside. Since EARS-335 the
  header alone no longer makes a line raw (a local drop carries ``drop()``'s MAC), so
  ``mail()`` mints a raw mail as ``drop()`` does: these tests still pin the raw
  branch's fences, now on a line that really is local;
- a refused path logs ONE warning naming the sender and the path, and the
  line still plays, end to end, writing nothing outside the root;
- ``mouth say --log-dir`` outside the root is refused before anything is dropped.

Tests marked ``FAILS ON 26e4a6f`` fail on master 26e4a6f. The player runs
in-process on the ``null`` device with the ``silence`` backend, in tmp_path: no
port, no live mouth.
"""

import json
from pathlib import Path

import pytest

import mouth
from mouth import __main__ as cli
from mouth import box

from tests.mouth.test_mouth_inbox import HOST, deliver, lines, run_player
from tests.mouth.test_mouth_inbox import box as inbox_box  # noqa: F401 - the fixture
from maildir_put import put
from tests import env_fence


@pytest.fixture
def d(tmp_path, monkeypatch):
    d = tmp_path / "mouth"
    monkeypatch.setenv("MOUTH_DIR", str(d))
    monkeypatch.setenv("MOUTH_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "base"))
    monkeypatch.setenv("MOUTH_DEVICE", "null")
    monkeypatch.setenv("MOUTH_BACKEND", "silence")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    env_fence.delenv(monkeypatch, "MOUTH_MAILBOX")
    env_fence.delenv(monkeypatch, "MOUTH_MAIL_LOG")
    box.ensure(d / "box")
    return d


def mail(d, name, headers, body="hello there\n", sender=None):
    """A mail from an allowed sender, delivered into new/. One with
    ``X-Mouth-Raw: 1`` is minted as ``drop()`` mints a local line (EARS-335)."""
    host = box._host()
    hdr = "".join(f"{k}: {v}\n" for k, v in headers.items())
    data = f"From: {sender or f'ivy@{host}'}\nTo: mouth@{host}\nSubject: x\n{hdr}\n{body}".encode()
    return put(d / "box", name, box._minted(d / "box", data) if headers.get("X-Mouth-Raw") == "1" else data)


def logged(capsys) -> list:
    return [json.loads(x) for x in capsys.readouterr().err.splitlines() if x.startswith("{")]


def outside(tmp_path) -> Path:
    out = tmp_path / "elsewhere"
    out.mkdir(exist_ok=True)
    return out


# -- confined(): the one helper --------------------------------------------------

def test_confined_takes_the_root_and_below(tmp_path):
    root = tmp_path / "root"
    (root / "a").mkdir(parents=True)
    assert box.confined(str(root), root) == str(root)
    assert box.confined(str(root / "a" / "b"), root) == str(root / "a" / "b")   # need not exist
    assert box.confined(f"{root}/a/../a", root) == f"{root}/a/../a"            # given back as given


@pytest.mark.parametrize("bad", [None, "", 7, ["x"], {"x": 1}, True, "a\0b"])
def test_confined_refuses_what_is_not_a_path(tmp_path, bad):
    assert box.confined(bad, tmp_path) is None


def test_confined_refuses_escapes_and_a_sibling_prefix(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "link").symlink_to(outside(tmp_path), target_is_directory=True)
    assert box.confined(f"{root}/../elsewhere", root) is None
    assert box.confined(str(root / "link" / "x"), root) is None
    assert box.confined(f"{root}-evil", root) is None      # a prefix is not a parent
    assert box.confined("/", root) is None


def test_confined_matches_a_root_reached_through_a_link(tmp_path):
    """macOS /var is /private/var: the root's real path is compared, not its spelling."""
    real = tmp_path / "real"
    (real / "sub").mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    assert box.confined(str(real / "sub"), alias) == str(real / "sub")
    assert box.confined(str(alias / "sub"), real) == str(alias / "sub")


def test_confined_takes_a_relative_path_against_the_cwd(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "in").mkdir(parents=True)
    monkeypatch.chdir(root)
    assert box.confined("in", root) == "in"
    assert box.confined("../x", root) is None


def test_the_roots_are_the_players_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MOUTH_LOG_DIR", str(tmp_path / "l"))
    monkeypatch.setenv("MOUTH_MAIL_LOG", str(tmp_path / "m"))
    assert box.heard_root() == tmp_path / "l" and box.mail_log_root() == tmp_path / "m"
    env_fence.delenv(monkeypatch, "MOUTH_LOG_DIR")
    env_fence.delenv(monkeypatch, "MOUTH_MAIL_LOG")
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "b"))
    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    assert box.heard_root() == tmp_path / "b" / "logs" / "conversations"
    assert box.mail_log_root() == tmp_path / "h" / ".mail" / "agents" / "mouth"


# -- SC1: the warning ---------------------------------------------------------------

def test_a_refused_log_dir_logs_one_warning_naming_sender_and_path(d, tmp_path, capsys):
    """FAILS ON 26e4a6f: nothing is refused, so nothing is logged."""
    bad = str(outside(tmp_path))
    item = box.read(mail(d, "1.w", {"Message-ID": "<w1@x>", "X-Mouth-Log-Dir": bad},
                         sender=f"spam@{HOST}"))
    assert item["log_dir"] == str(tmp_path / "logs")    # the default a mail from outside gets
    warns = [e for e in logged(capsys) if e.get("level") == "WARNING"]
    assert len(warns) == 1
    w = warns[0]
    assert w["refused_path"] == "X-Mouth-Log-Dir" and w["value"] == bad
    assert w["from"] == f"spam@{HOST}" and w["mail_id"] == "<w1@x>"
    assert w["root"] == str(tmp_path / "logs")


def test_an_in_root_log_dir_logs_nothing(d, tmp_path, capsys):
    item = box.read(mail(d, "1.i", {"Message-ID": "<i@x>", "X-Mouth-Log-Dir": str(tmp_path / "logs" / "s")}))
    assert item["log_dir"] == str(tmp_path / "logs" / "s")
    assert logged(capsys) == []


# -- SC4b door 1: an extra never sets a place the mouth set ----------------------------

@pytest.mark.parametrize("raw", [False, True], ids=["outside", "raw"])
@pytest.mark.parametrize("key", ["log_dir", "mail_log"])
def test_an_extra_never_sets_a_write_path(d, tmp_path, capsys, monkeypatch, key, raw):
    """FAILS ON 26e4a6f: item.update(extras) overrode the mouth's own value."""
    monkeypatch.setenv("MOUTH_MAIL_LOG", str(tmp_path / "maillog"))
    evil = str(outside(tmp_path))
    hdr = {"Message-ID": "<e1@x>", "X-Mouth-Extra": json.dumps({key: evil})}
    if raw:
        hdr["X-Mouth-Raw"] = "1"
    item = box.read(mail(d, "1.e", hdr))
    assert item[key] != evil
    want = {"log_dir": None if raw else str(tmp_path / "logs"),
            "mail_log": None if raw else str(tmp_path / "maillog")}[key]
    assert item[key] == want
    assert [e["bad_extra"] for e in logged(capsys) if "bad_extra" in e] == [key]


def test_an_extra_never_rethreads_a_mail_from_outside(d, capsys):
    """FAILS ON 26e4a6f: an extra log_root replaced the mail's own thread root."""
    item = box.read(mail(d, "1.r", {"Message-ID": "<r1@x>",
                                    "X-Mouth-Extra": json.dumps({"log_root": "<forged@x>"})}))
    assert item["log_root"] == "<r1@x>"
    assert [e["bad_extra"] for e in logged(capsys) if "bad_extra" in e] == ["log_root"]


def test_a_raw_lines_thread_root_still_rides_as_an_extra(d, capsys):
    """``mouth say`` threads its log by an extra log_root: the mouth sets none on a raw line."""
    item = box.read(mail(d, "1.t", {"Message-ID": "<t1@x>", "X-Mouth-Raw": "1",
                                    "X-Mouth-Extra": json.dumps({"log_root": "<q1@x>"})}))
    assert item["log_root"] == "<q1@x>"
    assert logged(capsys) == []


def test_an_extra_after_a_good_header_keeps_the_header(d, tmp_path):
    inside = str(tmp_path / "logs" / "mine")
    item = box.read(mail(d, "1.h", {"Message-ID": "<h1@x>", "X-Mouth-Log-Dir": inside,
                                    "X-Mouth-Extra": json.dumps({"log_dir": str(tmp_path)})}))
    assert item["log_dir"] == inside


def test_other_extras_still_ride(d):
    item = box.read(mail(d, "1.o", {"Message-ID": "<o1@x>",
                                    "X-Mouth-Extra": json.dumps({"duck": True, "volume_from": "flag"})}))
    assert item["duck"] is True and item["volume_from"] == "flag"


# -- SC4b door 2: a raw mail's header paths are confined ---------------------------------

@pytest.mark.parametrize("kind", ["absolute", "dotdot", "symlink"])
def test_a_raw_mails_log_dir_is_confined(d, tmp_path, kind):
    """FAILS ON 26e4a6f: X-Mouth-Raw is only a header, and the raw path took Log-Dir as is."""
    root = tmp_path / "logs"
    root.mkdir(exist_ok=True)
    out = outside(tmp_path)
    if kind == "symlink":
        (root / "link").symlink_to(out, target_is_directory=True)
    bad = {"absolute": str(out), "dotdot": f"{root}/../elsewhere", "symlink": str(root / "link")}[kind]
    item = box.read(mail(d, "1.rl", {"Message-ID": "<rl@x>", "X-Mouth-Raw": "1", "X-Mouth-Log-Dir": bad}))
    assert item["log_dir"] == str(root) and item["text"] == "hello there"   # the root itself


def test_a_refused_log_dir_with_no_env_root_is_the_heard_default(d, tmp_path, monkeypatch):
    env_fence.delenv(monkeypatch, "MOUTH_LOG_DIR")
    item = box.read(mail(d, "1.nd", {"Message-ID": "<nd@x>", "X-Mouth-Raw": "1",
                                     "X-Mouth-Log-Dir": str(outside(tmp_path))}))
    assert item["log_dir"] is None   # heard.append's own default: $VOICEMODE_BASE_DIR/logs/conversations


@pytest.mark.parametrize("env_set", [True, False], ids=["env-root", "default-root"])
def test_a_raw_mails_mail_log_outside_the_root_is_ignored(d, tmp_path, monkeypatch, capsys, env_set):
    """FAILS ON 26e4a6f: X-Mouth-Raw + X-Mouth-Mail-Log picked where maillog.entry writes."""
    if env_set:
        monkeypatch.setenv("MOUTH_MAIL_LOG", str(tmp_path / "maillog"))
    bad = str(outside(tmp_path))
    item = box.read(mail(d, "1.ml", {"Message-ID": "<ml@x>", "X-Mouth-Raw": "1", "X-Mouth-Mail-Log": bad}))
    assert item["mail_log"] == (str(tmp_path / "maillog") if env_set else None)   # the player's own
    warns = [e for e in logged(capsys) if e.get("level") == "WARNING"]
    assert [(w["refused_path"], w["value"]) for w in warns] == [("X-Mouth-Mail-Log", bad)]


def test_a_raw_mails_mail_log_inside_the_root_is_honoured(d, tmp_path, monkeypatch):
    """``mouth say`` writes the caller's own $MOUTH_MAIL_LOG: the player's root."""
    monkeypatch.setenv("MOUTH_MAIL_LOG", str(tmp_path / "maillog"))
    item = box.read(mail(d, "1.mi", {"Message-ID": "<mi@x>", "X-Mouth-Raw": "1",
                                     "X-Mouth-Mail-Log": str(tmp_path / "maillog")}))
    assert item["mail_log"] == str(tmp_path / "maillog")


def test_a_say_round_trip_keeps_its_log_dir_and_mail_log(d, tmp_path, monkeypatch):
    """A real ``mouth say`` drop, read back: in-root values pass the fence unchanged."""
    monkeypatch.setenv("MOUTH_MAIL_LOG", str(tmp_path / "maillog"))
    item = mouth.say("hi", spawn=False, d=d, log_dir=tmp_path / "logs" / "sub")
    got = box.Queue(d).lines()
    assert [x["utt"] for x in got] == [item["utt"]]
    assert got[0]["log_dir"] == str(tmp_path / "logs" / "sub")
    assert got[0]["mail_log"] == str(tmp_path / "maillog")
    assert got[0]["log_root"] == item["log_root"]



# -- under the root is not enough when the root is a maildir (fix-001 review) -----------

def test_in_a_queue_is_a_maildirs_own_subdirs(tmp_path):
    bx = tmp_path / "box"
    for sub in ("tmp", "new", "cur"):
        (bx / sub).mkdir(parents=True)
        assert box.in_a_queue(str(bx / sub), bx)
        assert box.in_a_queue(str(bx / sub / "x" / "y"), bx)
    assert not box.in_a_queue(str(bx), bx)               # the box itself: maillog's own home
    assert not box.in_a_queue(str(bx / ".log"), bx)      # a Maildir++ folder beside them
    assert not box.in_a_queue(str(bx / "newer"), bx)     # a prefix is not a parent
    assert box.in_a_queue(f"{bx}/.log/../new", bx)       # .. collapsed
    assert not box.in_a_queue(str(bx / "new"))            # no box, no refusal


@pytest.mark.parametrize("sub", ["new", "cur", "tmp", "new/deep"])
def test_a_raw_mails_mail_log_in_the_queues_own_maildir_is_ignored(d, monkeypatch, capsys, sub):
    """FAILS ON 1d13665 (and 26e4a6f): the mail log's root is the mouth's own box, so
    ``<box>/new`` was "under the root", and maillog made tmp/ cur/ new/ inside the queue."""
    monkeypatch.setenv("MOUTH_MAIL_LOG", str(d / "box"))
    bad = str(d / "box" / sub)
    item = box.read(mail(d, "1.q", {"Message-ID": "<q@x>", "X-Mouth-Raw": "1", "X-Mouth-Mail-Log": bad}))
    assert item["mail_log"] == str(d / "box")   # the player's own
    warns = [e for e in logged(capsys) if e.get("level") == "WARNING"]
    assert [(w["refused_path"], w["value"]) for w in warns] == [("X-Mouth-Mail-Log", bad)]


def test_a_raw_mails_mail_log_in_the_default_roots_maildir_is_ignored(d, tmp_path, monkeypatch):
    """With no $MOUTH_MAIL_LOG the root is ~/.mail/agents/mouth: its new/ is refused."""
    root = tmp_path / "home" / ".mail" / "agents" / "mouth"
    item = box.read(mail(d, "1.qd", {"Message-ID": "<qd@x>", "X-Mouth-Raw": "1",
                                     "X-Mouth-Mail-Log": str(root / "new")}))
    assert item["mail_log"] is None


def test_a_log_dir_in_the_queues_own_maildir_is_ignored(d, monkeypatch, capsys):
    """A heard root that holds the box (here the mouth dir) still never writes in its queue."""
    monkeypatch.setenv("MOUTH_LOG_DIR", str(d))
    bad = str(d / "box" / "new")
    item = box.read(mail(d, "1.ql", {"Message-ID": "<ql@x>", "X-Mouth-Log-Dir": bad}))
    assert item["log_dir"] == str(d)
    assert [e["refused_path"] for e in logged(capsys) if e.get("level") == "WARNING"] == ["X-Mouth-Log-Dir"]


def _case_blind(p: Path) -> bool:
    return (p / "new").is_dir() and (p / "NEW").is_dir()


@pytest.mark.parametrize("sub", ["NEW", "Cur", "TMP/x", "nEw/deep/er"])
def test_in_a_queue_sees_a_case_variant_on_a_case_blind_disk(tmp_path, sub):
    """FAILS ON 1ece2a3 (macOS): APFS is case-insensitive, so ``<box>/NEW`` IS the queue's
    ``new/``, but realpath keeps the case given and the string test let it by."""
    bx = box.ensure(tmp_path / "box")
    if not _case_blind(bx):
        pytest.skip("case-sensitive filesystem: NEW is another directory here")
    assert box.in_a_queue(str(bx / sub), bx)
    assert not box.in_a_queue(str(bx / "NEWER"), bx)   # still not a prefix match
    assert not box.in_a_queue(str(bx / ".LOG"), bx)


def test_in_a_queue_follows_a_link_into_the_queue(tmp_path):
    bx = box.ensure(tmp_path / "box")
    (tmp_path / "ln").symlink_to(bx / "new")
    assert box.in_a_queue(str(tmp_path / "ln" / "x"), bx)


@pytest.mark.parametrize("bad", [None, "", 7, "a\0b"])
def test_in_a_queue_refuses_nothing_that_is_not_a_path(tmp_path, bad):
    assert not box.in_a_queue(bad, box.ensure(tmp_path / "box"))


def test_a_raw_mails_mail_log_in_a_case_variant_of_the_queue_is_ignored(d, monkeypatch, capsys):
    """FAILS ON 1ece2a3 (macOS): X-Mouth-Mail-Log <box>/NEW wrote maildirs into the queue."""
    if not _case_blind(d / "box"):
        pytest.skip("case-sensitive filesystem")
    monkeypatch.setenv("MOUTH_MAIL_LOG", str(d / "box"))
    bad = str(d / "box" / "NEW")
    item = box.read(mail(d, "1.qc", {"Message-ID": "<qc@x>", "X-Mouth-Raw": "1", "X-Mouth-Mail-Log": bad}))
    assert item["mail_log"] == str(d / "box")
    warns = [e for e in logged(capsys) if e.get("level") == "WARNING"]
    assert [(w["refused_path"], w["value"]) for w in warns] == [("X-Mouth-Mail-Log", bad)]


def test_a_log_dir_in_a_case_variant_of_the_queue_is_ignored(d, monkeypatch, capsys):
    """FAILS ON 1ece2a3 (macOS): the heard root holds the box; X-Mouth-Log-Dir <box>/Cur."""
    if not _case_blind(d / "box"):
        pytest.skip("case-sensitive filesystem")
    monkeypatch.setenv("MOUTH_LOG_DIR", str(d))
    item = box.read(mail(d, "1.qk", {"Message-ID": "<qk@x>", "X-Mouth-Log-Dir": str(d / "box" / "Cur")}))
    assert item["log_dir"] == str(d)
    assert [e["refused_path"] for e in logged(capsys) if e.get("level") == "WARNING"] == ["X-Mouth-Log-Dir"]


def test_a_mail_log_aimed_at_the_queue_never_wedges_it(d, monkeypatch):
    """FAILS ON 1d13665: the second log entry left new/cur that serve could not file
    aside (cur/cur:2,T existed, ENOTEMPTY), so Queue.entries raised on every start."""
    from mouth import maillog

    monkeypatch.setenv("MOUTH_MAIL_LOG", str(d / "box"))
    p = mail(d, "1.w", {"Message-ID": "<w@x>", "X-Mouth-Raw": "1",
                        "X-Mouth-Mail-Log": str(d / "box" / "new")})
    item = box.read(p)
    p.rename(d / "box" / "cur" / "1.w:2,S")
    q = box.Queue(d)
    for kind in ("saying", "said"):
        maillog.entry(item, kind, {"utt": item["utt"]})
        assert q.entries() == []
    assert sorted(x.name for x in (d / "box" / "new").iterdir()) == []


# -- SC1 end to end: the player writes nothing outside the root -------------------------

def test_an_outside_mail_writes_nothing_outside_the_root(inbox_box, tmp_path, monkeypatch):
    """FAILS ON 26e4a6f: the player mkdir'd the evil dirs and wrote heard rows there."""
    monkeypatch.setenv("MOUTH_MAIL_LOG", str(inbox_box.mail))
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "base"))
    evil = [tmp_path / "evil-dir", tmp_path / "evil-extra", tmp_path / "evil-mail-log",
            tmp_path / "evil-raw-mail-log"]
    deliver(inbox_box, "one", X_Mouth_Log_Dir=str(evil[0]),
            X_Mouth_Extra=json.dumps({"log_dir": str(evil[1]), "mail_log": str(evil[2])}))
    deliver(inbox_box, "two", X_Mouth_Raw="1", X_Mouth_Mail_Log=str(evil[3]),
            X_Mouth_Log_Dir=f"{inbox_box.logs}/../evil-dir")
    run_player(inbox_box.d).join(8)
    assert not any(p.exists() for p in evil)
    said = [x for x in lines(inbox_box.logs) if x["kind"] == "said"]
    assert sorted(x["text"] for x in lines(inbox_box.logs) if x["kind"] == "saying") == ["one", "two"]
    assert len(said) == 2                                     # both rows in the root
    assert not (tmp_path / "base").exists()                   # nor the heard default


# -- SC3: the CLI refuses up front ------------------------------------------------------

def test_say_refuses_a_log_dir_outside_the_root_before_dropping(d, tmp_path):
    """FAILS ON 26e4a6f: say() dropped it, and the player took it."""
    with pytest.raises(ValueError, match="not under the mouth's log root"):
        mouth.say("hi", spawn=False, d=d, log_dir=outside(tmp_path))
    assert list((d / "box" / "new").iterdir()) == []


def test_say_takes_a_log_dir_inside_the_root(d, tmp_path):
    item = mouth.say("hi", spawn=False, d=d, log_dir=tmp_path / "logs" / "x")
    assert item["log_dir"] == str(tmp_path / "logs" / "x")


def test_say_takes_the_envs_own_value_as_given(d, tmp_path, monkeypatch):
    """A resume re-drops the env's own Log-Dir, even unexpanded (``~/...``)."""
    monkeypatch.setenv("MOUTH_LOG_DIR", "~/logs")
    item = mouth.say("hi", spawn=False, d=d, log_dir=Path("~/logs"))
    assert item["log_dir"] == "~/logs"


def test_the_cli_says_why_and_exits_2(d, tmp_path, capsys, monkeypatch):
    """FAILS ON 26e4a6f: the CLI queued it and printed a utt."""
    monkeypatch.setattr(mouth, "ensure_player", lambda *a, **k: None)   # never spawn one
    out = outside(tmp_path)
    assert cli.main(["say", "--log-dir", str(out), "hi"]) == 2
    err = capsys.readouterr().err
    assert str(out) in err and str(tmp_path / "logs") in err
    assert list((d / "box" / "new").iterdir()) == []


# -- SC4: the rest of the bad-Order family ----------------------------------------------

@pytest.mark.parametrize("value", ["1" + "0" * 21, "1" + "0" * 27, "-1" + "0" * 27, "1" + "0" * 5000,
                                   "12abc", "1.5e18"],
                         ids=["1e21-valueerror", "1e27-oserror", "neg-1e27", "past-int-digits",
                              "garbage", "float"])
def test_every_bad_order_reads_as_absent_and_is_logged(d, capsys, value):
    """FAILS ON 26e4a6f for the digit ids: fromtimestamp raised ValueError or OSError."""
    p = mail(d, "1.o", {"Message-ID": "<bo@x>", "X-Mouth-Order": value})
    item = box.read(p)
    assert item["_order"] == item["_own_order"] == p.stat().st_mtime_ns
    bad = [e for e in logged(capsys) if e.get("bad_header") == "X-Mouth-Order"]
    assert len(bad) == 1 and bad[0]["as"] == "absent" and bad[0]["value"] == value[:40]


def test_a_bad_order_reads_as_absent_even_with_requested(d):
    """FAILS ON 26e4a6f: with Requested present, the absurd order was kept as the sort key."""
    p = mail(d, "1.or", {"Message-ID": "<or@x>", "X-Mouth-Order": "1" + "0" * 30,
                         "X-Mouth-Requested": "1921356800.5"})
    item = box.read(p)
    assert item["_own_order"] == p.stat().st_mtime_ns
    assert item["requested_t"] == 1921356800.5


def test_no_order_logs_nothing(d, capsys):
    box.read(mail(d, "1.n", {"Message-ID": "<n@x>"}))
    assert [e for e in logged(capsys) if "bad_header" in e] == []
