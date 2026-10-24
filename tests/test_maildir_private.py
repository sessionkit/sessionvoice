"""Every agent maildir sessionvoice makes is 0700: the box and tmp/new/cur.

``ears.maildir.private`` is the one maker; the mouth's box (tests/mouth/
test_mouth_box_mode.py) and the store's heard box at install use it. All on tmp
paths and a fenced HOME: no real mail tree, no launchctl, no postfix.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from ears import maildir, services
from test_services_install_checks import _store, fence  # noqa: F401  (the fixture)

REPO = Path(__file__).resolve().parents[1]
SUBS = ("tmp", "new", "cur")


def modes(bx: Path) -> dict:
    return {p.name: oct(p.stat().st_mode & 0o777) for p in (bx, *(bx / s for s in SUBS))}


def private(bx: Path) -> dict:
    return {p.name: "0o700" for p in (bx, *(bx / s for s in SUBS))}


@pytest.fixture(autouse=True)
def umask_022():
    old = os.umask(0o022)
    yield
    os.umask(old)


def loose(bx: Path) -> None:
    for s in SUBS:
        (bx / s).mkdir(parents=True, exist_ok=True)
    for p in (bx, *(bx / s for s in SUBS)):
        p.chmod(0o755)


# --- the helper ----------------------------------------------------------------------------

def test_a_fresh_box_is_0700_and_its_parents_are_made(tmp_path):
    bx = tmp_path / "a" / "b" / "box"
    assert maildir.private(bx) == []                          # 0700 & ~022 is 0700: no chmod
    assert modes(bx) == private(bx)
    assert (tmp_path / "a" / "b").is_dir()


@pytest.mark.parametrize("mask", [0o000, 0o002, 0o022, 0o077, 0o277])
def test_whatever_the_umask(tmp_path, mask):
    os.umask(mask)
    bx = tmp_path / "box"
    maildir.private(bx)
    assert modes(bx) == private(bx)


def test_a_0755_box_is_repaired_and_its_mail_kept(tmp_path):
    bx = tmp_path / "box"
    loose(bx)
    (bx / "cur" / "1.mail:2,S").write_text("said")
    assert set(maildir.private(bx)) == {bx, *(bx / s for s in SUBS)}
    assert modes(bx) == private(bx)
    assert (bx / "cur" / "1.mail:2,S").read_text() == "said"


def test_a_private_box_is_left_alone(tmp_path):
    bx = tmp_path / "box"
    maildir.private(bx)
    assert maildir.private(bx) == []


def test_a_half_box_is_completed(tmp_path):
    bx = tmp_path / "box"
    (bx / "new").mkdir(parents=True)
    maildir.private(bx)
    assert modes(bx) == private(bx)


def test_the_parents_keep_their_mode(tmp_path):
    parent = tmp_path / "agents"
    parent.mkdir()
    parent.chmod(0o755)
    maildir.private(parent / "box")
    assert oct(parent.stat().st_mode & 0o777) == "0o755"      # sessionmail's, not ours


def test_a_file_in_the_way_is_an_error(tmp_path):
    (tmp_path / "box").write_text("not a dir")
    with pytest.raises(FileExistsError):
        maildir.private(tmp_path / "box")


# --- the heard box at install (the MTA layout: nested under the one store) ------------------

def test_install_makes_the_heard_box_0700(fence):
    store = _store(fence["home"])
    services.mail_checks()
    assert modes(store / "heard") == private(store / "heard")


def test_install_repairs_a_0755_heard_box(fence):
    store = _store(fence["home"])
    loose(store / "heard")
    out = services.mail_checks()
    assert modes(store / "heard") == private(store / "heard")
    assert any(l.startswith("heard  box mode repaired") for l in out)
    assert fence["calls"]["vmailbox"] == 0                       # present: not routed again


def test_a_dry_run_says_it_would_repair_and_changes_nothing(fence):
    store = _store(fence["home"])
    loose(store / "heard")
    out = services.mail_checks(dry_run=True)
    assert modes(store / "heard")["heard"] == "0o755"
    assert any("would make" in l and "0700" in l for l in out)


def test_a_private_heard_box_says_nothing_more(fence):
    store = _store(fence["home"])
    maildir.private(store / "heard")
    out = services.mail_checks()
    assert not any("repaired" in l or "would make" in l for l in out)


def test_the_hand_advice_makes_it_0700(fence):
    out = services.mail_checks()
    miss = next(l for l in out if "heard box MISSING" in l)
    assert "umask 077" in miss and "mkdir -p" in miss


# --- no maildir in src/ is made by a bare mkdir --------------------------------------------

def test_no_bare_maildir_mkdir_in_src():
    """A maildir's tmp/new/cur made anywhere but ears/maildir.py would be umask-mode again."""
    pat = re.compile(r"""\(\s*["'](?:tmp|new|cur)["'](?:\s*,\s*["'](?:tmp|new|cur)["']){2}\s*\)""")
    hits = []
    for p in sorted((REPO / "src").rglob("*.py")):
        if p == REPO / "src" / "ears" / "maildir.py":
            continue
        text = p.read_text()
        for m in pat.finditer(text):
            tail = text[m.end():m.end() + 200]
            if "mkdir" in tail.split("\n\n")[0]:
                hits.append(f"{p.relative_to(REPO)}:{text.count(chr(10), 0, m.start()) + 1}")
    assert hits == []
