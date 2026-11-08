"""EARS-502: ears.heardbox, the one rule for where the heard box is; SM 0.3.3: it asks
sessionmail (ears.boxes) and never spells the mail root.

`ears services install` and the mouth's default (``mouth.reply.heard_boxes``) both ask
it. Every layout here is built under the fenced HOME's mail root, and the fake `message`
(tests/fixtures/fake_sessionmail.py, on PATH through `fake_message_on_path`) answers for
it: the real mail root is never read.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ears import boxes, heardbox


@pytest.fixture
def agents(tmp_path, monkeypatch, fake_message_on_path) -> Path:
    """The boxes folder of the fenced HOME's mail root: an old tree (`agents/`)."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    a = home / ".mail" / "agents"
    a.mkdir(parents=True)
    boxes.forget()
    return a


def box(path: Path) -> Path:
    for sub in ("new", "cur", "tmp"):
        (path / sub).mkdir(parents=True, exist_ok=True)
    return path


def store(agents: Path, name: str = "store") -> Path:
    d = agents / name
    d.mkdir(parents=True, exist_ok=True)
    (d / ".tm-store").write_text("")
    return d


def test_the_boxes_folder_is_sessionmails_answer(agents, monkeypatch, tmp_path):
    """`message path --boxes`: agents/ while that is there and stores/ is not; else stores/."""
    assert heardbox.where() == agents
    (agents.parent / "stores").mkdir()
    boxes.forget()
    assert heardbox.where() == agents.parent / "stores"


def test_a_fresh_root_is_stores(tmp_path, monkeypatch, fake_message_on_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    boxes.forget()
    assert heardbox.where() == home / ".mail" / "stores"
    assert heardbox.flat() == home / ".mail" / "stores" / "heard"


def test_drop_the_flat_box_setup_makes(agents):
    """A mail-drop host: `message setup --box heard` makes <boxes>/heard."""
    flat = box(agents / "heard")
    assert heardbox.find() == flat == heardbox.flat()


def test_mta_the_nested_box_under_the_store(agents):
    nested = box(store(agents) / "heard")
    assert heardbox.find() == nested
    assert heardbox.nested() == [nested]


def test_nested_wins_over_flat(agents):
    """A fleet host keeps its nested box even with a flat one beside it: sessionmail's
    own answer for `heard` is asked first, and here (no postfix map) that is the flat
    one, which is kept only when no store box holds a heard."""
    box(agents / "heard")
    nested = box(store(agents) / "heard")
    assert heardbox.find() == nested
    boxes.forget()
    assert heardbox.find() == nested


def test_two_stores_the_first_by_name(agents):
    box(store(agents, "b") / "heard")
    first = box(store(agents, "a") / "heard")
    assert heardbox.find() == first
    assert [d.name for d in heardbox.stores()] == ["a", "b"]


@pytest.mark.parametrize("layout", ["empty", "no-root", "store-without-box", "box-without-new",
                                    "unmarked-dir", "flat-without-new", "mark-is-a-dir"])
def test_none(agents, layout):
    """No box: None, and nothing is made."""
    if layout == "no-root":
        agents.rmdir()
    elif layout == "store-without-box":
        store(agents)
    elif layout == "box-without-new":
        (store(agents) / "heard" / "cur").mkdir(parents=True)
    elif layout == "unmarked-dir":                 # a seat's own dir with a heard box: not a store
        box(agents / "someone" / "heard")
    elif layout == "flat-without-new":
        (agents / "heard" / "cur").mkdir(parents=True)
    elif layout == "mark-is-a-dir":
        (agents / "store" / ".tm-store").mkdir(parents=True)     # a dir, not a store's mark
        box(agents / "store" / "heard")
    before = sorted(p for p in agents.parent.rglob("*"))
    assert heardbox.find() is None
    assert sorted(p for p in agents.parent.rglob("*")) == before


def test_looked_for_names_both_places(agents):
    said = heardbox.looked_for()
    assert f"{agents}/<store>/heard" in said and str(agents / "heard") in said
    assert "message box list" in said and "\n" not in said


def test_no_message_no_box(agents, monkeypatch):
    """Without sessionmail there is no heard box and nothing is guessed: every answer is
    None or empty, and the words say where the folder would come from."""
    monkeypatch.setenv("PATH", str(agents.parent))          # nothing runnable on it
    monkeypatch.delenv("HOME")                              # no uv tool dirs either
    boxes.forget()
    assert heardbox.where() is None and heardbox.flat() is None
    assert heardbox.stores() == [] and heardbox.nested() == []
    assert heardbox.find() is None
    assert heardbox.looked_for().startswith(heardbox.BOXES_WORDS)


def test_install_reads_the_same_rule():
    """services keeps no second copy: its box name is heardbox's, and its root and
    generator are sessionmail's answers (ears.boxes), never a path of its own."""
    import inspect

    from ears import services
    assert services.HEARD == heardbox.HEARD
    assert not hasattr(services, "_mail_agents")
    assert not hasattr(services, "_mail_root")
    assert ".mail" not in inspect.getsource(services._vmailbox_gen_cmd)
    assert ".mail" not in inspect.getsource(heardbox)
