"""A mail cannot pick where the player writes, or kill a line by its order (EARS-303).

Parse-only, in a scratch queue under ``tmp_path``: no player is served, no
device is opened, no port is touched, and nothing is written outside tmp.

Tests marked ``FAILS ON 26e4a6f`` are the repro: they fail on master 26e4a6f
and must pass once the fix lands. The rest pin behaviour that already holds
and must keep holding.

SC1. ``X-Mouth-Log-Dir`` from a mail is honoured only if its REAL path
(symlinks resolved, ``..`` collapsed) lies under the mouth log root:
``$MOUTH_LOG_DIR``, or the mouth's default when unset
(``ears.heard.log_dir()``: ``$VOICEMODE_BASE_DIR/logs/conversations``).
On 26e4a6f ``read()`` takes the header unfenced (box.py:421), so an absolute
path elsewhere, a ``../`` escape and an in-root symlink pointing out all
become the line's ``log_dir``.

SC4. An ``X-Mouth-Order`` with no ``X-Mouth-Requested`` that is non-finite or
out of range reads as ABSENT (the file's mtime), and the line is spoken. On
26e4a6f two shapes raise in ``read()``, so ``Queue.entries`` files the mail
aside (``cur/...:2,T``) unspoken:

- ``10**400`` as digits: ``int()`` takes it, then ``own_order / 1e9``
  raises ``OverflowError: int too large to convert to float`` (box.py:384).
- ``10**30`` as digits (and ``-10**30``): ``own_order / 1e9`` is 1e21 s,
  then ``_ts`` raises ``OverflowError: timestamp out of range for platform
  time_t`` (box.py:418 -> box.py:285).

``1e30``, ``inf``, ``nan`` and ``-inf`` already fall back silently: ``int()``
raises ``ValueError`` (box.py:378), so ``own_order`` is the mtime. They are
pinned here so the fix keeps them.
"""

import json
import os
from pathlib import Path

import pytest

from mouth import box

from maildir_put import put
from tests import env_fence
from tests._anchor import ANCHOR

ESCAPES = ("absolute", "dotdot", "symlink")


@pytest.fixture
def d(tmp_path, monkeypatch):
    d = tmp_path / "mouth"
    monkeypatch.setenv("MOUTH_DIR", str(d))
    monkeypatch.setenv("MOUTH_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "base"))
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


def root_for(tmp_path, env_set: bool) -> Path:
    """The mouth log root: the env var, or the mouth's default when unset."""
    if env_set:
        return tmp_path / "logs"
    from ears import heard

    return heard.log_dir()


def under(path, root: Path) -> bool:
    """True if PATH's real path (symlinks resolved, ``..`` collapsed) is under ROOT's."""
    return Path(os.path.realpath(path)).is_relative_to(os.path.realpath(root))


def escape(kind: str, root: Path, tmp_path: Path) -> str:
    """An X-Mouth-Log-Dir that leaves ROOT, all of it inside tmp_path."""
    out = tmp_path / "elsewhere"
    out.mkdir(exist_ok=True)
    root.mkdir(parents=True, exist_ok=True)
    if kind == "absolute":
        return str(out)
    if kind == "dotdot":
        return f"{root}/../elsewhere"
    link = root / "link"   # lexically under the root, really outside it
    if not link.exists():
        link.symlink_to(out, target_is_directory=True)
    return str(link)


# -- SC1: X-Mouth-Log-Dir is confined to the log root --------------------------

@pytest.mark.parametrize("env_set", [True, False], ids=["env-root", "default-root"])
@pytest.mark.parametrize("kind", ESCAPES)
def test_a_log_dir_outside_the_root_is_not_where_the_line_writes(d, tmp_path, monkeypatch, kind, env_set):
    """FAILS ON 26e4a6f: box.py:421 takes the header as the line's log_dir."""
    if not env_set:
        env_fence.delenv(monkeypatch, "MOUTH_LOG_DIR")
    root = root_for(tmp_path, env_set)
    bad = escape(kind, root, tmp_path)
    assert not under(bad, root)   # the case really leaves the root
    item = box.read(mail(d, "1.out", {"Message-ID": "<o1@x>", "X-Mouth-Log-Dir": bad}))
    assert item is not None and item["text"] == "hello there"   # the line still plays
    got = item["log_dir"]
    assert got is None or under(got, root), f"{kind}: log_dir {got!r} is outside {root}"


@pytest.mark.parametrize("kind", ESCAPES)
def test_a_log_dir_outside_the_root_still_speaks(d, tmp_path, kind):
    """The refused header never costs the line: it is a line in the queue."""
    bad = escape(kind, tmp_path / "logs", tmp_path)
    mail(d, "1.out", {"Message-ID": "<o2@x>", "X-Mouth-Log-Dir": bad})
    assert [x["mail_id"] for x in box.Queue(d).lines()] == ["<o2@x>"]


@pytest.mark.parametrize("env_set", [True, False], ids=["env-root", "default-root"])
def test_a_log_dir_inside_the_root_is_honoured(d, tmp_path, monkeypatch, env_set):
    """SC2, holds on 26e4a6f: a Log-Dir under the root is the line's, as today."""
    if not env_set:
        env_fence.delenv(monkeypatch, "MOUTH_LOG_DIR")
    root = root_for(tmp_path, env_set)
    inside = root / "sub" / "dir"
    item = box.read(mail(d, "1.in", {"Message-ID": "<i1@x>", "X-Mouth-Log-Dir": str(inside)}))
    assert Path(item["log_dir"]) == inside


# -- SC4: a bad X-Mouth-Order reads as absent -----------------------------------

#: id -> the header value. ``raises`` fail on 26e4a6f; ``falls_back`` hold already.
BAD_ORDERS = {
    "1e30": ("1e30", "falls_back"),
    "1e30-digits": ("1" + "0" * 30, "raises"),        # _ts: timestamp out of range
    "neg-1e30-digits": ("-1" + "0" * 30, "raises"),   # _ts: timestamp out of range
    "1e400-digits": ("1" + "0" * 400, "raises"),      # own_order / 1e9: int too large
    "inf": ("inf", "falls_back"),
    "nan": ("nan", "falls_back"),
    "-inf": ("-inf", "falls_back"),
}


@pytest.mark.parametrize("value", [v for v, _ in BAD_ORDERS.values()], ids=list(BAD_ORDERS))
def test_a_bad_order_without_requested_is_read_as_absent(d, value):
    """FAILS ON 26e4a6f for the ``raises`` ids (OverflowError in read())."""
    p = mail(d, "1.ord", {"Message-ID": "<n1@x>", "X-Mouth-Order": value})
    mtime_ns = p.stat().st_mtime_ns
    item = box.read(p)
    assert item["_own_order"] == item["_order"] == mtime_ns   # absent: the file's own time
    assert item["requested_t"] == mtime_ns / 1e9
    assert item["text"] == "hello there"


@pytest.mark.parametrize("value", [v for v, _ in BAD_ORDERS.values()], ids=list(BAD_ORDERS))
def test_a_bad_order_mail_is_spoken_not_filed_aside(d, capsys, value):
    """FAILS ON 26e4a6f for the ``raises`` ids: the mail is filed aside (T)."""
    mail(d, "1.ord", {"Message-ID": "<n2@x>", "X-Mouth-Order": value})
    mail(d, "2.ok", {"Message-ID": "<n3@x>"})
    assert sorted(x["mail_id"] for x in box.Queue(d).lines(act=True)) == ["<n2@x>", "<n3@x>"]
    assert list((d / "box" / "cur").iterdir()) == []
    errs = [json.loads(x) for x in capsys.readouterr().err.splitlines() if x.startswith("{")]
    assert not [e for e in errs if "unreadable" in e]


def test_a_good_order_is_kept(d):
    """Holds on 26e4a6f: a sane nanosecond order is the line's order and time."""
    order = int(ANCHOR.timestamp()) * 10**9 + 123_456_789
    item = box.read(mail(d, "1.ord", {"Message-ID": "<g1@x>", "X-Mouth-Order": str(order)}))
    assert item["_own_order"] == order
    assert item["requested_t"] == order / 1e9
