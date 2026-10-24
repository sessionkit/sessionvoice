"""The mouth's box is 0700, like every other agent box, whatever the umask.

Its two makers (``box.ensure``, reached by ``drop`` and the player, and
``maillog.write``) both make the box and tmp/new/cur 0700, and both repair a box
that was made 0755 before. HOME is the fenced one (tests/mouth/conftest.py), so
``~/.mail/agents/mouth`` here is in tmp.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mouth import box, maillog
from tests import env_fence

SUBS = ("tmp", "new", "cur")


def modes(bx: Path) -> dict:
    return {p.name: oct(p.stat().st_mode & 0o777) for p in (bx, *(bx / s for s in SUBS))}


def private(bx: Path) -> dict:
    return {p.name: "0o700" for p in (bx, *(bx / s for s in SUBS))}


@pytest.fixture(autouse=True)
def umask_022():
    """The usual umask: the one a bare mkdir turns into 0755."""
    old = os.umask(0o022)
    yield
    os.umask(old)


@pytest.fixture
def live_box(monkeypatch) -> Path:
    """The mouth dir is the default one, so the box is ``$HOME/.mail/agents/mouth``."""
    env_fence.delenv(monkeypatch, "MOUTH_DIR")
    env_fence.delenv(monkeypatch, "MOUTH_MAILBOX")
    bx = box.box_dir()
    assert bx == Path(os.environ["HOME"]) / ".mail" / "agents" / "mouth"
    assert not bx.exists()
    return bx


def made_loose(bx: Path) -> None:
    """A box as the old code left it: everything 0755."""
    for s in SUBS:
        (bx / s).mkdir(parents=True, exist_ok=True)
    for p in (bx, *(bx / s for s in SUBS)):
        p.chmod(0o755)


def test_ensure_makes_the_live_box_0700(live_box):
    box.ensure(live_box)
    assert modes(live_box) == private(live_box)


def test_ensure_repairs_a_0755_box(live_box):
    made_loose(live_box)
    assert modes(live_box)["mouth"] == "0o755"
    box.ensure(live_box)
    assert modes(live_box) == private(live_box)


def test_a_drop_makes_the_box_0700(live_box):
    box.drop(live_box, {"utt": "u1", "text": "hello", "requested_t": 1.0})
    assert modes(live_box) == private(live_box)
    assert len(list((live_box / "new").iterdir())) == 1        # the line still lands


def test_the_mail_log_makes_the_default_box_0700(live_box, monkeypatch):
    env_fence.setenv(monkeypatch, "MOUTH_MAIL_LOG", "1")
    assert Path(maillog.box_from_env()) == live_box
    maillog.write(maillog.box_from_env(), "queued", {"utt": "u1", "text": "hi"})
    assert modes(live_box) == private(live_box)
    assert len(list((live_box / "cur").iterdir())) == 1


def test_the_mail_log_repairs_a_0755_box(live_box):
    made_loose(live_box)
    maillog.write(live_box, "queued", {"utt": "u1", "text": "hi"})
    assert modes(live_box) == private(live_box)


@pytest.mark.parametrize("mask", [0o000, 0o002, 0o022, 0o027])
def test_whatever_the_umask(tmp_path, mask):
    os.umask(mask)
    bx = tmp_path / "some" / "box"          # not the live one: $MOUTH_MAILBOX or <dir>/box
    box.ensure(bx)
    assert modes(bx) == private(bx)


def test_ensure_leaves_the_parents_mode_alone(live_box):
    """~/.mail and ~/.mail/agents are sessionmail's: an existing mode there is kept."""
    agents = live_box.parent
    agents.chmod(0o755)
    box.ensure(live_box)
    assert oct(agents.stat().st_mode & 0o777) == "0o755"
    assert modes(live_box) == private(live_box)
