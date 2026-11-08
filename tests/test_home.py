"""sessionvoice's state root, ``ears.home.state_dir()`` (EARS-294 T7; EARS-492 do-001).
It moves no file and makes none.

The order (design.md D1): $SESSIONVOICE_HOME, $VOICEMODE_BASE_DIR, ~/.sessionvoice if
POPULATED and REAL, ~/.sessionkit/voice/state if POPULATED, ~/.voicemode if POPULATED and
REAL, else ~/.sessionkit/voice/state. POPULATED means logs/conversations holds a heard.lock
or a heard_*.jsonl that is a REAL file (a symlink is not); a directory that is merely there
never decides. REAL means the old folder is not the new root itself (a symlink to it).

heard.py's copy of the rule is held to the module over the full grid: every state of each
of the three roots, under each env. It is the ONE copy left (EARS-492 do-002): every other
stdlib script loads home.py by its path and asks the PACKAGE through it
(``asked_state_dir``, design.md D4.1), the keeper's shell asks ``"$PY" -m ears.home
state-dir``, and ``quiet-for`` asks ``home.py asked-state-dir``. Each loader is pinned here in
the checkout layout, the wheel layout (where it ships) and with no home.py; the ask itself
against a fake package (``$EARS_BIN``): agree, differ, cannot be asked. A scan fails any new
copy of the rule, and any root literal, outside home.py and heard.py.
Everything is in tmp_path.
"""
import ast
import glob
import itertools
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from ears import cli, heard, home, services
from mouth import paths
from test_services_install_checks import _store, fence  # noqa: F401  (a fixture)
from test_services_speech import sx  # noqa: F401  (a fixture)

ROOT = Path(__file__).resolve().parents[1]
NEW = home.NEW
SV, VM = home.OLD_ROOTS

#: heard.py keeps its copy for good (design.md D4): it runs alone under any python3 (the
#: ride-along hook) and sessionphone loads a copy of it by path.
HEARD_COPY = "src/ears/heard.py"
PY_COPIES = (HEARD_COPY,)
#: The stdlib scripts that load home.py by its path and ask the package (design.md D4, D4.1),
#: and where the wheel ships each one (None: the checkout and the plugin only).
LOADERS = {
    "bin/ears-turn": None,
    "bin/heard-first": None,
    "keeper/ears-mail.py": "_keeper",
    "keeper/ears-sounds.py": "_keeper",
    "keeper/heard-tail.py": None,
    "keeper/mouth-sounds.py": "_keeper",
    "keeper/partial-wake.py": None,
    "keeper/sound-tour.py": None,
    "shadow/score": None,
    "skills/ears/scripts/ears-mid-turn": None,
    "skills/ears/scripts/ears-mouth-tail": None,
    "speakers/speaker-sidecar2.py": "_speakers",
    "view/push": None,
}
#: The shell callers of the root: the keeper asks its package python, quiet-for asks home.py.
KEEPER, QUIET_FOR = "keeper/ears-keeper.sh", "keeper/quiet-for"
# where a script that reads the home could live
SCANNED = ("src", "bin", "keeper", "shadow", "skills/ears/scripts", "speakers", "view", "bench")


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    for n in home.ENV:
        monkeypatch.delenv(n, raising=False)
    return h


def make(root: Path, state: str, new: Path = None) -> None:
    """Put ``root`` in ``state``: none (absent), bare (the dir only), empty (an empty
    logs/conversations), other (only files that are not the heard log), lock, jsonl,
    linked (each heard file a symlink into ``new``'s logs/conversations, as a migrate leaves
    ~/.voicemode), symlink (the root itself a symlink to ``new``, as a migrate leaves
    ~/.sessionvoice). ``new`` defaults to the HOME's new root."""
    new = root.parent / NEW if new is None else new
    conv = root / "logs" / "conversations"
    if state == "none":
        return
    if state == "symlink":
        root.parent.mkdir(parents=True, exist_ok=True)
        root.symlink_to(new, target_is_directory=True)
        return
    if state == "bare":
        root.mkdir(parents=True)
        (root / "mouth").mkdir()
        (root / "talk").mkdir()
        return
    conv.mkdir(parents=True)
    if state == "other":
        (conv / "exchanges_2030-06-09.jsonl").write_text("{}\n")
        (conv / "speakers_2030-06-09.jsonl").write_text("{}\n")
        (conv / "heard_cursor").write_text("1\n")
    elif state == "lock":
        (conv / "heard.lock").write_text("")
    elif state == "jsonl":
        (conv / "heard_2030-06-09.jsonl").write_text('{"seq": 1}\n')
    elif state == "linked":
        target = new / "logs" / "conversations"
        for name in ("heard.lock", "heard_2030-06-09.jsonl"):
            (conv / name).symlink_to(target / name)


def build(h: Path, sv: str, new: str, vm: str) -> None:
    """A HOME with ~/.sessionvoice, the new root and ~/.voicemode in the given states (the
    new root first: the old roots' links point into it)."""
    make(h / NEW, new, new=h / "elsewhere")
    make(h / SV, sv)
    make(h / VM, vm)


# --- the module: the six steps --------------------------------------------------------------

def test_the_root_is_the_kits_state_home_beside_settings_and_log():
    assert home.NEW == ".sessionkit/voice/state"
    assert home.OLD_ROOTS == (".sessionvoice", ".voicemode")
    assert Path(home.NEW).parent == Path(home.SETTINGS).parent == Path(home.LOG).parent


def test_fresh_host_is_the_new_default_and_nothing_is_made(fake_home):
    assert home.state_dir() == fake_home / ".sessionkit/voice/state"
    assert home.state_source() == (fake_home / NEW, "fresh-new")
    assert home.sessionvoice_home() == home.state_dir(), "the old name is an alias"
    assert list(fake_home.iterdir()) == [], "the resolver makes no directory"


@pytest.mark.parametrize("marker", ["lock", "jsonl"])
def test_a_populated_sessionvoice_is_adopted(fake_home, marker):
    """An existing host is unchanged: our own old root wins while it is real, even over a
    populated new root, and nothing is written under the new one."""
    make(fake_home / SV, marker)
    assert home.state_source() == (fake_home / SV, "old")
    make(fake_home / NEW, "jsonl")
    make(fake_home / VM, "jsonl")
    assert home.state_source() == (fake_home / SV, "old")


@pytest.mark.parametrize("marker", ["lock", "jsonl"])
def test_a_populated_voicemode_is_adopted_in_place(fake_home, marker):
    make(fake_home / VM, marker)
    assert home.state_source() == (fake_home / VM, "old")
    assert not (fake_home / ".sessionkit").exists(), "nothing written under the new root"


@pytest.mark.parametrize("marker", ["lock", "jsonl"])
def test_a_populated_new_root_is_new(fake_home, marker):
    make(fake_home / NEW, marker)
    assert home.state_source() == (fake_home / NEW, "new")


def test_voicemode_never_outranks_a_populated_new_root_and_status_says_split(fake_home):
    """The converse case (D1, D6): voice wrote first at the new root, then converse filled
    ~/.voicemode. The host stays in the kit, and status names both roots and the FIX."""
    make(fake_home / NEW, "jsonl")
    make(fake_home / VM, "jsonl")
    assert home.state_source() == (fake_home / NEW, "new")
    assert home.split() == (fake_home / NEW, fake_home / VM)
    rows = home.status_lines()
    assert rows[0] == f"state  {fake_home / NEW} (has the heard log)"
    warning = "\n".join(rows[1:])
    assert "WARNING: THE HEARD LOG IS SPLIT" in rows[1]
    assert str(fake_home / NEW) in rows[1] and str(fake_home / VM) in rows[1]
    assert "SESSIONVOICE_HOME=~/.voicemode (or VOICEMODE_BASE_DIR)" in warning
    assert "so that both write one log" in warning


@pytest.mark.parametrize("stray", ["bare", "empty", "other", "linked"])
def test_an_unpopulated_root_never_decides(fake_home, stray):
    """A folder that is merely there (an installer's mkdir of the new root, say) never pulls
    a live ~/.voicemode host out of its root; nor does an unpopulated ~/.sessionvoice."""
    make(fake_home / NEW, stray, new=fake_home / "elsewhere")
    make(fake_home / SV, stray)
    make(fake_home / VM, "jsonl")
    assert home.state_source() == (fake_home / VM, "old")


@pytest.mark.parametrize("stray", ["bare", "empty", "other", "linked", "symlink"])
def test_an_unpopulated_old_root_never_decides(fake_home, stray):
    make(fake_home / SV, stray)
    make(fake_home / VM, stray)
    assert home.state_source() == (fake_home / NEW, "fresh-new")
    assert not (fake_home / NEW).exists()


def test_a_heard_file_that_is_a_link_is_not_populated(fake_home):
    """A ~/.voicemode whose heard files are links into the new root (as a migrate leaves it)
    is not that root's own log: the new root answers, and there is no split."""
    make(fake_home / NEW, "jsonl")
    (fake_home / NEW / "logs/conversations/heard.lock").write_text("")
    make(fake_home / VM, "linked")
    assert (fake_home / VM / "logs/conversations/heard.lock").resolve().is_file()
    assert not home.populated(fake_home / VM)
    assert home.state_source() == (fake_home / NEW, "new")
    assert home.split() is None


@pytest.mark.parametrize("old", [SV, VM])
def test_an_old_root_that_is_a_link_to_the_new_one_is_not_real(fake_home, old):
    """After a migrate the old path is a symlink to the new root: it is never handed out
    (talk refuses a symlinked token folder), and it is no second root."""
    make(fake_home / NEW, "jsonl")
    make(fake_home / old, "symlink")
    assert home.populated(fake_home / old), "it shows the new root's own log"
    assert home.state_source() == (fake_home / NEW, "new")
    assert home.split() is None
    assert home.status_lines() == [f"state  {fake_home / NEW} (has the heard log)"]


def test_an_old_root_linked_elsewhere_is_still_real(fake_home, tmp_path):
    """REAL is "not the new root itself", not "not a link": an old root that is a symlink
    to some other folder of its own is adopted as before."""
    make(tmp_path / "disk", "jsonl")
    (fake_home / SV).symlink_to(tmp_path / "disk", target_is_directory=True)
    assert home.state_source() == (fake_home / SV, "old")


def test_all_three_populated_picks_sessionvoice_and_names_all_three(fake_home):
    make(fake_home / SV, "lock")
    make(fake_home / NEW, "jsonl")
    make(fake_home / VM, "lock")
    roots = (fake_home / SV, fake_home / NEW, fake_home / VM)
    assert home.state_dir() == roots[0]
    assert home.split() == roots
    rows = home.status_lines()
    assert rows[0] == f"state  {roots[0]} (has the heard log; an old root, adopted in place)"
    assert all(str(r) in rows[1] for r in roots) and "All of" in rows[1]
    assert f"sessionvoice reads and writes {roots[0]};" in rows[2]


def test_both_old_roots_populated_picks_sessionvoice_and_says_split(fake_home):
    make(fake_home / SV, "jsonl")
    make(fake_home / VM, "lock")
    assert home.state_dir() == fake_home / SV
    assert home.split() == (fake_home / SV, fake_home / VM)
    rows = home.status_lines()
    assert rows[0] == f"state  {fake_home / SV} (has the heard log; an old root, adopted in place)"
    assert "WARNING: THE HEARD LOG IS SPLIT. Both" in rows[1]
    assert str(fake_home / SV) in rows[1] and str(fake_home / VM) in rows[1]


def test_one_populated_is_no_split(fake_home):
    make(fake_home / VM, "jsonl")
    make(fake_home / SV, "empty")
    make(fake_home / NEW, "other")
    assert home.split() is None
    assert home.status_lines() == [
        f"state  {fake_home / VM} (has the heard log; an old root, adopted in place)",
        f"state  the state is in {fake_home / VM}; `ears home migrate` moves it to "
        f"{fake_home / NEW} (this host's wave-4 step, then `ears services install`)"]


def test_the_fresh_row_says_it_is_the_default(fake_home):
    assert home.status_lines() == [f"state  {fake_home / NEW} (the default; no heard log yet)"]


def test_voicemode_base_dir_is_read_old(fake_home, tmp_path, monkeypatch):
    """A populated old root, with $VOICEMODE_BASE_DIR set: it wins."""
    make(fake_home / SV, "empty")
    make(fake_home / VM, "jsonl")
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm"))
    assert home.state_source() == (tmp_path / "vm", "override")
    assert home.status_lines()[0] == f"state  {tmp_path / 'vm'} ($VOICEMODE_BASE_DIR)"


@pytest.mark.parametrize("populated_root", [SV, NEW, VM])
def test_each_override_beats_every_populated_root(fake_home, tmp_path, monkeypatch, populated_root):
    make(fake_home / populated_root, "lock")
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm"))
    assert home.state_source() == (tmp_path / "vm", "override")
    monkeypatch.setenv("SESSIONVOICE_HOME", str(tmp_path / "sv"))
    assert home.state_source() == (tmp_path / "sv", "override")
    assert home.status_lines()[0].endswith("($SESSIONVOICE_HOME)")


def test_an_empty_variable_is_not_set(fake_home, tmp_path, monkeypatch):
    monkeypatch.setenv("SESSIONVOICE_HOME", "")
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm"))
    assert home.state_dir() == tmp_path / "vm"
    monkeypatch.setenv("VOICEMODE_BASE_DIR", "")
    assert home.state_source() == (fake_home / NEW, "fresh-new")


def test_a_tilde_in_the_variable_is_the_home(fake_home, monkeypatch):
    monkeypatch.setenv("SESSIONVOICE_HOME", "~/elsewhere")
    assert home.state_dir() == fake_home / "elsewhere"
    monkeypatch.setenv("SESSIONVOICE_HOME", "")
    monkeypatch.setenv("VOICEMODE_BASE_DIR", "~/vm")
    assert home.state_dir() == fake_home / "vm"


def test_an_empty_environ_leaves_the_overrides_out(fake_home, tmp_path, monkeypatch):
    """``environ={}`` is the rule without the two root overrides (D3's items use it)."""
    monkeypatch.setenv("SESSIONVOICE_HOME", str(tmp_path / "sv"))
    make(fake_home / VM, "jsonl")
    assert home.state_dir(environ={}) == fake_home / VM
    assert home.state_dir(environ={}, home=tmp_path / "other") == tmp_path / "other" / NEW


def test_the_subtree_follows_the_root(fake_home):
    """heard_*, heard.lock, state/, mouth/ and talk/ all hang off the one root: the new one
    on a fresh HOME, an adopted old one on a live host."""
    from talk import token_store
    for root in (fake_home / NEW, fake_home / VM):
        if root.name == VM:
            make(root, "lock")
        assert home.state_dir() == root
        assert heard.base_dir() == root
        assert heard.log_dir() == root / "logs" / "conversations"
        assert heard.cursor_dir() == root / "state" / "heard-cursor"
        assert paths.default_mouth_dir() == root / "mouth"
        assert token_store.path().parent == root / "talk"
    assert not (fake_home / ".sessionkit").exists(), "nothing made under the new root"


def test_a_heard_write_on_a_fresh_home_lands_in_the_kit_only(fake_home):
    heard.append("event", event=heard.EV_HEARTBEAT)
    assert list(fake_home.iterdir()) == [fake_home / ".sessionkit"]
    assert home.state_source() == (fake_home / NEW, "new")
    for gone in (".sessionvoice", ".voicemode"):
        assert not (fake_home / gone).exists()


def test_services_status_prints_the_state_row_and_the_split(sx, capsys):
    h = sx["home"]
    make(h / SV, "jsonl")
    make(h / VM, "jsonl")
    assert services.status() == 0
    out = capsys.readouterr().out
    assert f"state  {h / SV} (has the heard log; an old root, adopted in place)" in out
    assert "WARNING: THE HEARD LOG IS SPLIT" in out
    assert "SESSIONVOICE_HOME=~/.voicemode (or VOICEMODE_BASE_DIR)" in out
    assert "\nhome   " not in out, "the row is labelled state, by what it shows"


# --- the CLI and the --json contract -------------------------------------------------------

def _clean_env(h: Path, **extra) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in home.ENV}
    env.update(HOME=str(h), **extra)
    return env


def _argv(how: str, *args) -> list:
    if how == "module":
        return [sys.executable, "-m", "ears.home", *args]
    return [sys.executable, str(ROOT / "src/ears/home.py"), *args]


def _pyproject_version() -> str:
    return re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M).group(1)


def _contract(doc: dict) -> None:
    """The --json contract the kit's peers parse: exactly these keys, tools' source words."""
    assert set(doc) == {"version", "root", "items"}
    assert isinstance(doc["version"], str) and doc["version"]
    assert set(doc["root"]) == {"path", "source"}
    assert doc["root"]["source"] in ("override", "old", "new", "fresh-new")
    assert isinstance(doc["items"], dict)
    for name, item in doc["items"].items():
        assert set(item) == {"path", "source"}, name
        assert item["source"] in ("override", "old", "new", "fresh-new"), name


@pytest.mark.parametrize("how", ["module", "file"])
def test_the_cli_prints_the_root_for_shell(fake_home, tmp_path, how):
    p = subprocess.run(_argv(how, "state-dir"), env=_clean_env(fake_home),
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 0 and p.stdout == f"{fake_home / NEW}\n", p.stderr
    p = subprocess.run(_argv(how, "state-dir"),
                       env=_clean_env(fake_home, SESSIONVOICE_HOME="~/sv", VOICEMODE_BASE_DIR="x"),
                       capture_output=True, text=True, timeout=30)
    assert p.stdout == f"{fake_home / 'sv'}\n"
    p = subprocess.run(_argv(how, "state-dir"),
                       env=_clean_env(fake_home, SESSIONVOICE_HOME="",
                                      VOICEMODE_BASE_DIR=str(tmp_path / "vm")),
                       capture_output=True, text=True, timeout=30)
    assert p.stdout == f"{tmp_path / 'vm'}\n"
    assert list(fake_home.iterdir()) == [], "the CLI makes nothing"


@pytest.mark.parametrize("how", ["module", "file"])
def test_the_cli_json_is_the_contract(fake_home, how):
    p = subprocess.run(_argv(how, "state-dir", "--json"), env=_clean_env(fake_home),
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    doc = json.loads(p.stdout)
    _contract(doc)
    assert doc == {"version": _pyproject_version(),
                   "root": {"path": str(fake_home / NEW), "source": "fresh-new"},
                   "items": {n: {"path": str(fake_home / NEW / it.name), "source": "fresh-new"}
                             for n, it in home.ITEMS.items()}}
    assert set(doc["items"]) == {"call-tap", "call-ears", "audio-devices", "speakers",
                                 "ears-mail-state"}, "every D3 item, and only those"
    make(fake_home / VM, "jsonl")
    doc = json.loads(subprocess.run(_argv(how, "state-dir", "--json"), env=_clean_env(fake_home),
                                    capture_output=True, text=True, timeout=30).stdout)
    assert doc["root"] == {"path": str(fake_home / VM), "source": "old"}


@pytest.mark.parametrize("how", ["module", "file"])
def test_the_cli_refuses_a_bad_line_and_an_unknown_item(fake_home, how):
    for args in ([], ["state-dir", "--nope"], ["state-dir", "a", "b"], ["bogus"]):
        p = subprocess.run(_argv(how, *args), env=_clean_env(fake_home),
                           capture_output=True, text=True, timeout=30)
        assert p.returncode == 2 and not p.stdout and "state-dir [ITEM] [--json]" in p.stderr, args
    p = subprocess.run(_argv(how, "state-dir", "seats"), env=_clean_env(fake_home),
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 2 and not p.stdout and "no item 'seats'" in p.stderr
    assert "items: audio-devices, call-ears, call-tap, ears-mail-state, speakers" in p.stderr


@pytest.mark.parametrize("args", [["state-dir"], ["state-dir", "--json"]])
def test_the_ears_cli_and_the_module_print_the_same(fake_home, capsys, args):
    make(fake_home / NEW, "lock")
    assert cli.main(["home", *args]) == 0
    got = capsys.readouterr().out
    p = subprocess.run(_argv("file", *args), env=_clean_env(fake_home),
                       capture_output=True, text=True, timeout=30)
    assert got == p.stdout
    if "--json" in args:
        _contract(json.loads(got))
        assert json.loads(got)["root"] == {"path": str(fake_home / NEW), "source": "new"}
    else:
        assert got == f"{fake_home / NEW}\n"


def test_the_ears_cli_names_itself_in_its_usage(fake_home, capsys):
    assert cli.main(["home"]) == 2
    assert "usage: ears home log-dir | state-dir [ITEM] [--json]" in capsys.readouterr().err


def test_state_json_in_process_is_the_contract(fake_home, tmp_path):
    for env, src in (({}, "fresh-new"), ({"SESSIONVOICE_HOME": str(tmp_path / "x")}, "override")):
        doc = home.state_json(environ=env)
        _contract(doc)
        assert doc["root"]["source"] == src
    assert home.version() == _pyproject_version()


def test_the_version_of_a_wheel_comes_from_its_metadata(tmp_path, monkeypatch):
    """A home.py with no sessionvoice pyproject two folders up (a wheel's) asks the
    distribution's metadata, and says ``unknown`` when there is none: never a crash."""
    pkg = tmp_path / "site" / "ears"
    pkg.mkdir(parents=True)
    (pkg / "home.py").write_text((ROOT / "src/ears/home.py").read_text())
    code = "import home, json; print(json.dumps(home.version()))"
    p = subprocess.run([sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(pkg)!r}); {code}"],
                       env=_clean_env(tmp_path), capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    try:
        from importlib.metadata import version as dist_version
        want = dist_version("sessionvoice")
    except Exception:
        want = "unknown"
    assert json.loads(p.stdout) == want


# --- every copy, held to the module ---------------------------------------------------------

#: The states each root is put in. A symlink to the new root is an old root's state only.
STATES = ("none", "bare", "empty", "other", "lock", "jsonl", "linked")
OLD_STATES = (*STATES, "symlink")
ENVS = (
    {},
    {"VOICEMODE_BASE_DIR": "{tmp}/vm"},
    {"SESSIONVOICE_HOME": "{tmp}/sv"},
    {"SESSIONVOICE_HOME": "{tmp}/sv", "VOICEMODE_BASE_DIR": "{tmp}/vm"},
    {"SESSIONVOICE_HOME": "", "VOICEMODE_BASE_DIR": "{tmp}/vm"},
    {"VOICEMODE_BASE_DIR": "~/vm"},
)


def _env(env: dict, tmp_path: Path) -> dict:
    return {k: v.replace("{tmp}", str(tmp_path)) for k, v in env.items()}


def grid(tmp_path):
    """Each (HOME, env, case): every state of ~/.sessionvoice, the new root and ~/.voicemode,
    under each env. Each HOME is a fresh dir."""
    for i, (sv, new, vm) in enumerate(itertools.product(OLD_STATES, STATES, OLD_STATES)):
        h = tmp_path / f"g{i}"
        h.mkdir()
        build(h, sv, new, vm)
        for env in ENVS:
            yield h, _env(env, tmp_path), (sv, new, vm)


def _py_copy(rel: str):
    """The file's own ``sessionvoice_home``, compiled alone (its script never runs)."""
    src = (ROOT / rel).read_text()
    fn = [n for n in ast.parse(src).body
          if isinstance(n, ast.FunctionDef) and n.name == "sessionvoice_home"]
    assert len(fn) == 1, f"{rel}: one top-level sessionvoice_home, found {len(fn)}"
    ns = {"os": os, "glob": glob, "Path": Path}
    exec(compile(ast.Module(body=fn, type_ignores=[]), rel, "exec"), ns)
    return ns["sessionvoice_home"]


def _set(monkeypatch, h: Path, env: dict) -> None:
    monkeypatch.setenv("HOME", str(h))
    for n in home.ENV:
        monkeypatch.delenv(n, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)


def test_the_grid_covers_every_case_the_rule_tells_apart(tmp_path):
    """The grid reaches each source, and each of the five non-override answers."""
    seen = set()
    for h, env, case in grid(tmp_path):
        if not env:
            root, src = home.state_source(environ={}, home=h)
            seen.add((src, root.name if src == "old" else NEW))
    assert seen == {("old", SV), ("new", NEW), ("old", VM), ("fresh-new", NEW)}


def test_heard_py_copy_agrees_with_the_module_over_the_full_grid(tmp_path, monkeypatch):
    """heard.py keeps its copy (D4): held to state_dir() over every state of the three roots
    (none, bare, empty, other, lock, jsonl, linked, and a symlink to the new root for the
    two old ones), under each env."""
    copy = _py_copy(HEARD_COPY)
    n = 0
    for h, env, case in grid(tmp_path):
        _set(monkeypatch, h, env)
        assert str(copy()) == str(home.state_dir()), (case, env)
        n += 1
    assert n == len(OLD_STATES) ** 2 * len(STATES) * len(ENVS)


def test_heard_base_dir_is_its_copy(fake_home):
    for state in ("none", "jsonl"):
        make(fake_home / VM, state)
        assert heard.base_dir() == Path(heard.sessionvoice_home()) == home.state_dir()


def _scanned_files():
    for d in SCANNED:
        for p in sorted((ROOT / d).rglob("*")):
            # a symlink (src/ears/_keeper/, _speakers/: what the wheel ships) is its target
            if p.is_file() and not p.is_symlink() and "__pycache__" not in p.parts and ".egg-info" not in str(p) \
                    and p.suffix not in (".md", ".json", ".onnx", ".wav", ".txt", ".plist", ".in"):
                yield p


def test_every_copy_is_listed():
    """The rule is defined in home.py and in heard.py's pinned copy, and nowhere else: a new
    copy, in Python or in shell, fails here (design.md D4)."""
    found = set()
    for p in _scanned_files():
        try:
            text = p.read_text()
        except UnicodeDecodeError:
            continue
        if re.search(r"^def sessionvoice_home\(|^sessionvoice_home\(\) \{", text, re.M):
            found.add(p.relative_to(ROOT).as_posix())
    assert found == {HEARD_COPY, "src/ears/home.py"}


# VoiceMode's own paths: these stay in ~/.voicemode whatever sessionvoice's home is
VOICEMODE_OWNED = re.compile(
    r"\.voicemode[\"']?\s*/\s*[\"']?(voicemode\.env|voices|services|models|eval)\b"
    r"|exchanges_|backchannel_|VM_(LOGDIR|CONV)\b")


def test_no_hard_coded_home_is_left():
    """No script names ~/.voicemode for sessionvoice's state outside a copy of the rule.
    VoiceMode-owned paths (voicemode.env, voices/, services/, models/, eval/, exchanges_*)
    are left alone; so are prose lines that only say where the state USED to be."""
    allowed_prose = (
        "on a host that keeps it there", "on a host that already keeps it there",
        "repo with NO remote is local: ``~/.voicemode``",  # missed.py: a git repo with no remote
        "old voice_mode.mouth queue folder",    # mouth-sounds.py: a folder no longer watched
    )
    bad = []
    for p in _scanned_files():
        rel = p.relative_to(ROOT).as_posix()
        if rel.startswith("keeper/test-") or rel == "src/ears/home.py":
            continue
        try:
            text = p.read_text()
        except UnicodeDecodeError:
            continue
        if rel in PY_COPIES:
            fn = [n for n in ast.parse(text).body
                  if isinstance(n, ast.FunctionDef) and n.name == "sessionvoice_home"][0]
            lines = text.splitlines(keepends=True)
            text = "".join(lines[:fn.lineno - 1] + lines[fn.end_lineno:])
        for i, line in enumerate(text.splitlines(), 1):
            if ".voicemode" not in line and "VOICEMODE_BASE_DIR" not in line:
                continue
            if VOICEMODE_OWNED.search(line) or any(a in line for a in allowed_prose):
                continue
            if re.search(r"\b(com|dev)\.voicemode\.", line) and ".voicemode/" not in line:
                continue                          # a launchd label, not a path (T6)
            bad.append(f"{rel}: {line.strip()}")
    allowed = {
        "src/ears/envnames.py: \"VOICEMODE_BASE_DIR\",",           # UNTOUCHED: read by ears.home
        "src/ears/heard.py: ``~/.sessionvoice`` or ``~/.voicemode``). It rolls at the",
        # the bench strips the runner's old name before it sets the new one
        'bench/timeline_bench.py: and k not in ("PYTHONPATH", "VOICEMODE_BASE_DIR")}',
    }
    assert [b for b in bad if b not in allowed] == []


def test_no_root_literal_outside_the_module_and_heard_py():
    """The state root's NAMES (``~/.sessionvoice``, the kit's ``.sessionkit/voice/state``)
    are written in home.py and in heard.py's pinned copy only. A label such as
    ``dev.sessionvoice.talk`` is not a path, and is not matched. ``~/.voicemode`` is held by
    test_no_hard_coded_home_is_left. The stray old paths are do-003's scan."""
    literal = re.compile(r"(?<![\w.])\.sessionvoice(?![\w.-])|sessionkit/voice/state")
    bad = []
    for p in _scanned_files():
        rel = p.relative_to(ROOT).as_posix()
        if rel in ("src/ears/home.py", HEARD_COPY) or rel.startswith("keeper/test-"):
            continue
        try:
            text = p.read_text()
        except UnicodeDecodeError:
            continue
        bad += [f"{rel}:{i}: {ln.strip()}" for i, ln in enumerate(text.splitlines(), 1)
                if literal.search(ln)]
    assert bad == []


def test_every_stdlib_loader_is_listed_and_none_carries_the_rule():
    """Each file that loads home.py by its path is in LOADERS, asks through
    asked_state_dir, and defines no rule of its own."""
    found = set()
    for p in _scanned_files():
        rel = p.relative_to(ROOT).as_posix()
        try:
            text = p.read_text()
        except UnicodeDecodeError:
            continue
        if re.search(r"^def voice_state_dir\(", text, re.M):
            found.add(rel)
            assert ".asked_state_dir(" in text, rel
            assert not re.search(r"SESSIONVOICE_HOME|VOICEMODE_BASE_DIR", text), rel
    assert found == set(LOADERS)


# --- the loaders (design.md D4): checkout, wheel, no home.py ----------------------------------

from test_log_dir import _loader  # noqa: E402  (the same AST cut as the log loaders)


@pytest.fixture
def no_process(monkeypatch):
    """A rung-1 answer starts no process: any subprocess.run fails the test."""
    def refuse(*a, **k):
        raise AssertionError(f"a process was started: {a[:1]}")
    monkeypatch.setattr(subprocess, "run", refuse)


#: The HOMEs a loader is run under: fresh, each old root populated, the new root populated.
SCENARIOS = (("none", "none", "none"), ("jsonl", "none", "none"), ("none", "none", "lock"),
             ("none", "jsonl", "none"), ("none", "jsonl", "jsonl"), ("symlink", "lock", "none"))


def _scenarios(tmp_path):
    for i, (sv, new, vm) in enumerate(SCENARIOS):
        h = tmp_path / f"s{i}"
        h.mkdir(parents=True)
        build(h, sv, new, vm)
        for env in ENVS:
            yield h, _env(env, tmp_path), (sv, new, vm)


@pytest.mark.parametrize("script", LOADERS)
def test_a_loader_in_the_checkout_answers_as_the_module(script, tmp_path, monkeypatch, no_process):
    """Under the package's own python (the test's: the checkout is installed editable), the
    checkout's home.py IS the package (rung 1): its answer, with no process, over fresh,
    old, new and overridden HOMEs."""
    for h, env, case in _scenarios(tmp_path):
        _set(monkeypatch, h, env)
        fn = _loader(script, (ROOT / script).parent, "voice_state_dir")
        assert fn() == home.state_dir(), (script, case, env)


@pytest.mark.parametrize("script", [s for s, w in LOADERS.items() if w])
def test_a_loader_in_the_wheel_is_the_package(script, tmp_path, monkeypatch, no_process):
    """The wheel ships it as ears/<_keeper|_speakers>/: home.py is one folder up, and the
    dist-info beside ears/ makes that file the package's own, whatever python runs it (the
    speakers side-car's venv cannot import ears). No process."""
    site = tmp_path / "site"
    pkg = site / "ears"
    (pkg / LOADERS[script]).mkdir(parents=True)
    (pkg / "home.py").write_text((ROOT / "src/ears/home.py").read_text())
    (site / "sessionvoice-0.3.3.dist-info").mkdir()
    for h, env, case in _scenarios(tmp_path / "homes"):
        _set(monkeypatch, h, env)
        fn = _loader(script, pkg / LOADERS[script], "voice_state_dir")
        assert fn() == home.state_dir(), (script, case, env)


@pytest.mark.parametrize("script", LOADERS)
def test_a_loader_with_no_home_py_exits_2_naming_where_it_looked(script, tmp_path, capsys):
    here = tmp_path / "lone" / Path(script).parent
    here.mkdir(parents=True)
    fn = _loader(script, here, "voice_state_dir")
    with pytest.raises(SystemExit) as e:
        fn()
    assert e.value.code == 2
    err = capsys.readouterr().err
    want = ([here.parent / "src/ears/home.py", here.parent / "home.py"] if LOADERS[script]
            else [(here.parents[2] if script.startswith("skills/") else here.parent)
                  / "src/ears/home.py"])
    assert all(str(w) in err for w in want), err
    if LOADERS[script]:
        assert " nor " in err, "both candidates are named"


def test_each_loader_loads_home_py_once(tmp_path, monkeypatch, no_process):
    fake_home = tmp_path / "h"
    fake_home.mkdir()
    _set(monkeypatch, fake_home, {})
    for script in LOADERS:
        vh = _loader(script, (ROOT / script).parent, "voice_home")
        assert vh() is vh(), script


# --- asking the package (design.md D4.1) -------------------------------------------------------

def _plugin(tmp_path, version="9.9.9") -> Path:
    """A plugin checkout that is NOT the test python's package: a copy of home.py in
    <repo>/src/ears/, with a pyproject naming sessionvoice, so rung 1 never answers."""
    repo = tmp_path / "plugin"
    (repo / "src" / "ears").mkdir(parents=True)
    (repo / "src/ears/home.py").write_text((ROOT / "src/ears/home.py").read_text())
    (repo / "pyproject.toml").write_text(f'[project]\nname = "sessionvoice"\nversion = "{version}"\n')
    return repo


def _fake_ears(path: Path, *, root=None, code=0, out=None, err="", sleep=0, log=None) -> Path:
    """A fake package ``ears``. By default it IS the package: it runs the real home.py with
    the args after ``home``. Else it prints the contract naming ROOT, or OUT, and exits CODE."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["#!/bin/sh"]
    if log:
        lines.append(f'echo "$@" >> {str(log)!r}')
    if sleep:
        lines.append(f"sleep {sleep}")
    if root is None and out is None and code == 0:
        lines.append(f'shift; exec {sys.executable!r} {str(ROOT / "src/ears/home.py")!r} "$@"')
    else:
        if out is None and root is not None:
            out = json.dumps({"version": "0.3.4", "root": {"path": str(root), "source": "new"},
                              "items": {}})
        if out:
            lines.append(f"cat <<'EOF'\n{out}\nEOF")
        if err:
            lines.append(f"echo {err!r} >&2")
        lines.append(f"exit {code}")
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o755)
    return path


def _plugin_home(repo: Path):
    """The plugin's home.py, loaded by path as a loader loads it (a fresh module: no cache)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_voice_home_t", repo / "src/ears/home.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ask_env(h: Path, **extra) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in (*home.ENV, home.ASK_ENV, "UV_TOOL_DIR", "XDG_DATA_HOME")}
    env.update({"HOME": str(h), "PATH": "/usr/bin:/bin", **extra})
    return env


def _want(env: dict, h: Path, monkeypatch) -> Path:
    """The module's answer for a process whose HOME is H (a ``~`` in a variable is H)."""
    monkeypatch.setenv("HOME", str(h))
    return home.state_dir(environ=env, home=h)


def test_the_package_agrees_so_the_plugin_uses_the_packages_answer(tmp_path, monkeypatch):
    """The real rule on both sides, over fresh, old, new and overridden HOMEs: the plugin's
    answer is the package's, asked by one process."""
    mod_repo = _plugin(tmp_path)
    ears = _fake_ears(tmp_path / "pkg/bin/ears")
    for h, env, case in _scenarios(tmp_path / "homes"):
        m = _plugin_home(mod_repo)
        root, how = m.ask_package(environ=_ask_env(h, EARS_BIN=str(ears), **env))
        want = _want(env, h, monkeypatch)
        assert root == want and how == f"asked {ears}", (case, env, how)


def test_the_package_disagrees_so_the_plugin_refuses_naming_both(tmp_path, capsys):
    repo = _plugin(tmp_path)
    h = tmp_path / "h"
    h.mkdir()
    other = tmp_path / "elsewhere"
    ears = _fake_ears(tmp_path / "pkg/bin/ears", root=other)
    m = _plugin_home(repo)
    env = _ask_env(h, EARS_BIN=str(ears))
    root, why = m.ask_package(environ=env)
    assert root is None
    assert str(other) in why and str(h / NEW) in why, "both paths are named"
    assert "version 0.3.4" in why and "version 9.9.9" in why and "disagree" in why
    with pytest.raises(SystemExit) as e:
        m.asked_state_dir("heard-first", environ=env)
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith("heard-first: the sessionvoice package") and err.count("\n") == 1


def test_a_link_to_the_same_folder_is_agreement(tmp_path):
    """realpath on both sides: the package naming the same folder by a link agrees."""
    repo = _plugin(tmp_path)
    h = tmp_path / "h"
    h.mkdir()
    (h / NEW).mkdir(parents=True)
    (tmp_path / "link").symlink_to(h / NEW, target_is_directory=True)
    ears = _fake_ears(tmp_path / "pkg/bin/ears", root=tmp_path / "link")
    root, how = _plugin_home(repo).ask_package(environ=_ask_env(h, EARS_BIN=str(ears)))
    assert root == tmp_path / "link" and how.startswith("asked ")


@pytest.mark.parametrize("kind", ["old-package", "fails", "garbage", "no-root", "timeout",
                                  "not-there"])
def test_a_package_that_cannot_answer_is_a_refusal_naming_the_plugins_answer(kind, tmp_path,
                                                                               monkeypatch):
    repo = _plugin(tmp_path)
    h = tmp_path / "h"
    h.mkdir()
    b = tmp_path / "pkg/bin/ears"
    if kind == "old-package":   # 0.3.2's ears: no `home` verb, argparse exits 2
        _fake_ears(b, code=2, err="ears: error: argument cmd: invalid choice: 'home'")
    elif kind == "fails":
        _fake_ears(b, code=1, err="boom")
    elif kind == "garbage":
        _fake_ears(b, out="not json")
    elif kind == "no-root":
        _fake_ears(b, out=json.dumps({"version": "0.3.4", "items": {}}))
    elif kind == "timeout":
        _fake_ears(b, root=h / NEW, sleep=5)
    m = _plugin_home(repo)
    if kind == "timeout":
        m.ASK_TIMEOUT = 0.3
    root, why = m.ask_package(environ=_ask_env(h, EARS_BIN=str(b)))
    assert root is None
    assert str(h / NEW) in why and "version 9.9.9" in why, "the plugin's own answer is named"
    assert f"{b} home state-dir --json" in why and "Nothing is read or written" in why
    if kind == "old-package":
        assert "older than 0.3.3" in why and "invalid choice" in why
    if kind == "timeout":
        assert "did not answer" in why


def test_a_plugin_only_host_refuses(tmp_path):
    """No $EARS_BIN, no checkout venv, no ears on PATH, no uv tool: there is no package to
    ask, and the plugin does not answer on its own."""
    repo = _plugin(tmp_path)
    h = tmp_path / "h"
    h.mkdir()
    root, why = _plugin_home(repo).ask_package(environ=_ask_env(h))
    assert root is None
    assert why.startswith("no sessionvoice package to ask") and str(h / NEW) in why
    assert "EARS_BIN" in why
    assert list(h.iterdir()) == [], "nothing is made"


def test_the_candidates_in_order(tmp_path):
    """$EARS_BIN alone; else the checkout's .venv/bin/ears; else ears on PATH, never the
    checkout's own bin/ears shim; else uv's tool installs."""
    repo = _plugin(tmp_path)
    h = tmp_path / "h"
    h.mkdir()
    shim = _fake_ears(repo / "bin/ears", code=9)          # the plugin's own shim: never asked
    on_path = _fake_ears(tmp_path / "path/ears")
    tools = tmp_path / "tools"
    kit = _fake_ears(tools / "sessionkit/bin/ears")
    own = _fake_ears(tools / "sessionvoice/bin/ears")
    path = f"{repo / 'bin'}:{tmp_path / 'path'}:/usr/bin:/bin"

    def how(**extra):
        return _plugin_home(repo).ask_package(environ=_ask_env(h, UV_TOOL_DIR=str(tools), **extra))

    assert how(PATH=path)[1] == f"asked {on_path}"
    venv = _fake_ears(repo / ".venv/bin/ears")
    assert how(PATH=path)[1] == f"asked {venv}"
    assert how(PATH=path, EARS_BIN=str(own))[1] == f"asked {own}"
    venv.unlink()
    assert how(PATH=f"{repo / 'bin'}:/usr/bin:/bin")[1] == f"asked {kit}", "the shim is skipped"
    kit.unlink()
    assert how(PATH="/usr/bin:/bin")[1] == f"asked {own}"
    assert shim.exists()
    root, why = how(PATH=path, EARS_BIN=str(tmp_path / "nope"))
    assert root is None and "could not be run" in why, "a named package is the only one asked"


def test_the_answer_is_asked_once_per_process(tmp_path):
    repo = _plugin(tmp_path)
    h = tmp_path / "h"
    h.mkdir()
    log = tmp_path / "calls"
    ears = _fake_ears(tmp_path / "pkg/bin/ears", root=h / NEW, log=log)
    m = _plugin_home(repo)
    env = _ask_env(h, EARS_BIN=str(ears))
    for _ in range(3):
        assert m.ask_package(environ=env)[0] == h / NEW
        assert m.asked_state_dir("x", environ=env) == h / NEW
    assert log.read_text().splitlines() == ["home state-dir --json"]
    env2 = dict(env, SESSIONVOICE_HOME=str(tmp_path / "o"))
    m.ask_package(environ=env2)                      # another override: asked again
    assert len(log.read_text().splitlines()) == 2


@pytest.mark.parametrize("script", LOADERS)
def test_each_loader_in_a_plugin_asks_the_package_and_refuses_on_a_mismatch(script, tmp_path,
                                                                             monkeypatch, capsys):
    """Each loader, with its home.py a plugin's (not the running python's package): it runs
    on the package's answer when the two agree, and exits 2 naming both when they differ."""
    repo = _plugin(tmp_path)
    here = repo / Path(script).parent
    here.mkdir(parents=True, exist_ok=True)
    h = tmp_path / "h"
    h.mkdir()
    for n in (*home.ENV, home.ASK_ENV, "UV_TOOL_DIR", "XDG_DATA_HOME"):
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv(home.ASK_ENV, str(_fake_ears(tmp_path / "ok/ears")))
    assert _loader(script, here, "voice_state_dir")() == h / NEW
    other = tmp_path / "elsewhere"
    monkeypatch.setenv(home.ASK_ENV, str(_fake_ears(tmp_path / "bad/ears", root=other)))
    with pytest.raises(SystemExit) as e:
        _loader(script, here, "voice_state_dir")()
    assert e.value.code == 2
    err = capsys.readouterr().err
    prog = Path(script).name.removesuffix(".py")
    assert err.startswith(f"{prog}: ") and str(other) in err and str(h / NEW) in err


def test_asked_state_dir_on_the_command_line(tmp_path):
    """Shell's way in: ``home.py asked-state-dir --as PROG`` prints the root, or exits 2 with
    nothing on stdout and the refusal, under PROG, on stderr."""
    repo = _plugin(tmp_path)
    h = tmp_path / "h"
    h.mkdir()
    argv = [sys.executable, str(repo / "src/ears/home.py"), "asked-state-dir", "--as", "quiet-for"]
    ok = _fake_ears(tmp_path / "ok/ears")
    p = subprocess.run(argv, env=_ask_env(h, EARS_BIN=str(ok)), capture_output=True, text=True,
                       timeout=30)
    assert p.returncode == 0 and p.stdout == f"{h / NEW}\n", p.stderr
    p = subprocess.run(argv, env=_ask_env(h), capture_output=True, text=True, timeout=30)
    assert p.returncode == 2 and p.stdout == "" and p.stderr.startswith("quiet-for: no sessionvoice")
    p = subprocess.run(argv[:3] + ["--as"], env=_ask_env(h), capture_output=True, text=True,
                       timeout=30)
    assert p.returncode == 2 and "asked-state-dir [--as PROG]" in p.stderr


def test_the_package_itself_needs_no_process(fake_home, no_process):
    """Rung 1: this checkout's home.py is the test python's ears.home."""
    assert home._is_the_package()
    assert home.ask_package() == (home.state_dir(), "this home.py is the package's own")


def test_asks_package_is_the_predicate_the_migrate_needs(tmp_path):
    """True for a home.py that carries the mechanism; False for a 0.3.2 one, or none."""
    assert home.asks_package(ROOT / "src/ears/home.py")
    old = tmp_path / "home.py"
    old.write_text('"""0.3.2"""\ndef sessionvoice_home():\n    pass\n')
    assert not home.asks_package(old)
    assert not home.asks_package(tmp_path / "missing.py")


# --- the shell callers -------------------------------------------------------------------------

def _keeper_heard_dir(env) -> subprocess.CompletedProcess:
    """The keeper's two lines for the heard folder: the ask at the top, and HEARD_DIR."""
    text = (ROOT / KEEPER).read_text()
    a = text.index('if [ -z "${EARS_HEARD_DIR:-}" ]; then')
    b = text.index("\nfi\n", a) + 4
    line = [ln for ln in text.splitlines() if ln.startswith("HEARD_DIR=")]
    assert len(line) == 1
    return subprocess.run(["bash", "-c", f'set -uo pipefail\n{text[a:b]}{line[0]}\necho "H[$HEARD_DIR]"'],
                          env=env, capture_output=True, text=True, timeout=60)


def test_the_keeper_asks_its_package_python_and_agrees(tmp_path, monkeypatch):
    for h, env, case in _scenarios(tmp_path):
        p = _keeper_heard_dir({**_ask_env(h), **env, "PY": sys.executable})
        want = _want(env, h, monkeypatch) / "logs" / "conversations"
        assert p.returncode == 0 and p.stdout == f"H[{want}]\n", (case, env, p.stderr)


def test_the_keeper_stops_when_its_package_cannot_answer(tmp_path):
    h = tmp_path / "h"
    h.mkdir()
    p = _keeper_heard_dir({**_ask_env(h), "PY": "/usr/bin/false", "ROOT": str(ROOT)})
    assert p.returncode == 2 and "H[" not in p.stdout
    assert "ears.home state-dir" in p.stderr and "older than 0.3.3" in p.stderr
    p = _keeper_heard_dir({**_ask_env(h), "PY": "/usr/bin/false", "EARS_HEARD_DIR": "/x/y"})
    assert p.returncode == 0 and p.stdout == "H[/x/y]\n", "EARS_HEARD_DIR asks nothing"


def _today_log(root: Path, age_s: float) -> None:
    from datetime import datetime, timedelta, timezone
    conv = root / "logs" / "conversations"
    conv.mkdir(parents=True, exist_ok=True)
    ts = (datetime.now(timezone.utc) - timedelta(seconds=age_s)).isoformat()
    name = f"heard_{datetime.now():%Y-%m-%d}.jsonl"
    (conv / name).write_text(json.dumps({"source": "mic", "kind": "turn", "text": "hi",
                                         "ts": ts}) + "\n")


def test_quiet_for_reads_the_log_under_the_packages_root(tmp_path, monkeypatch):
    """Run as it is, with the test python as python3 (the package itself, rung 1): it reads
    today's log under the root state_dir() names, in each HOME."""
    py_dir = Path(sys.executable).parent
    for h, env, case in _scenarios(tmp_path):
        root = _want(env, h, monkeypatch)
        _today_log(root, age_s=1)
        p = subprocess.run(["bash", str(ROOT / QUIET_FOR), "60"],
                           env={**_ask_env(h), **env, "PATH": f"{py_dir}:/usr/bin:/bin"},
                           capture_output=True, text=True, timeout=60)
        assert p.returncode == 1 and "TALKING" in p.stdout, (case, env, p.stderr)


def test_quiet_for_in_a_plugin_refuses_a_package_that_disagrees(tmp_path):
    repo = _plugin(tmp_path)
    (repo / "keeper").mkdir()
    (repo / QUIET_FOR).write_text((ROOT / QUIET_FOR).read_text())
    h = tmp_path / "h"
    h.mkdir()
    py_dir = Path(sys.executable).parent
    bad = _fake_ears(tmp_path / "bad/ears", root=tmp_path / "elsewhere")
    p = subprocess.run(["bash", str(repo / QUIET_FOR), "6"],
                       env=_ask_env(h, EARS_BIN=str(bad), PATH=f"{py_dir}:/usr/bin:/bin"),
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 2 and p.stdout == ""
    assert p.stderr.startswith("quiet-for: ") and str(tmp_path / "elsewhere") in p.stderr
    assert str(h / NEW) in p.stderr
    (repo / "src/ears/home.py").unlink()
    p = subprocess.run(["bash", str(repo / QUIET_FOR), "6"], env=_ask_env(h), capture_output=True,
                       text=True, timeout=60)
    assert p.returncode == 2 and "no ears/home.py at" in p.stderr


# --- heard.py: no ask, no refusal (design.md D4.1 (d)) ---------------------------------------

def test_heard_py_never_asks_and_imports_only_the_standard_library():
    """heard.py's copy answers on its own: it starts no process for the root and never exits
    on a root question (a hook that runs on every tool call must never fail on an import)."""
    text = (ROOT / HEARD_COPY).read_text()
    assert "asked_state_dir" not in text and "ask_package" not in text
    fn = next(n for n in ast.parse(text).body
              if isinstance(n, ast.FunctionDef) and n.name == "sessionvoice_home")
    body = ast.get_source_segment(text, fn)
    assert "subprocess" not in body and "exit" not in body
    top = ast.parse(text).body                    # module level: what a hook run imports
    mods = {a.name.split(".")[0] for n in top if isinstance(n, ast.Import) for a in n.names}
    mods |= {n.module.split(".")[0] for n in top
             if isinstance(n, ast.ImportFrom) and n.module and n.level == 0}
    assert mods <= set(sys.stdlib_module_names) | {"__future__"}, mods - set(sys.stdlib_module_names)


def test_a_guest_copy_of_heard_py_answers_alone_with_no_package(tmp_path):
    """A copy run standalone (VoiceMode's hook, sessionphone's sink): no package, no PATH,
    nothing beside it, and it still answers, quietly, from its pinned copy."""
    guest = tmp_path / "guest"
    guest.mkdir()
    (guest / "voicemode-heard-hook").write_text((ROOT / HEARD_COPY).read_text())
    h = tmp_path / "h"
    h.mkdir()
    make(h / VM, "jsonl")
    p = subprocess.run([sys.executable, "-I", str(guest / "voicemode-heard-hook"), "last-seq"],
                       env=_ask_env(h, PATH=""), capture_output=True, text=True, timeout=30)
    assert p.returncode == 0 and p.stderr == "", p.stderr
    assert p.stdout.strip() == "1", "it read ~/.voicemode's log, the module's answer"
