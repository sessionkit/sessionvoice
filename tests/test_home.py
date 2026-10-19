"""EARS-294 T7: sessionvoice's home, ``ears.home.sessionvoice_home()``. It moves no file.

The order: $SESSIONVOICE_HOME, $VOICEMODE_BASE_DIR, ~/.sessionvoice if POPULATED,
~/.voicemode if POPULATED, else ~/.sessionvoice. POPULATED means logs/conversations
holds a heard.lock or a heard_*.jsonl; a directory that is merely there never decides.

Every copy of the rule (the stdlib-only scripts, heard.py, and the two shell scripts)
is held to the module here, over the same fake HOMEs. Everything is in tmp_path.
"""
import ast
import glob
import itertools
import os
import re
import subprocess
from pathlib import Path

import pytest

from ears import heard, home, services
from mouth import paths
from test_services_install_checks import _store, fence  # noqa: F401  (a fixture)
from test_services_speech import sx  # noqa: F401  (a fixture)

ROOT = Path(__file__).resolve().parents[1]

#: Every file that keeps its own copy of the rule. A new copy is added here, or
#: test_every_copy_is_listed fails.
PY_COPIES = (
    "src/ears/heard.py",
    "bin/ears-turn",
    "bin/heard-first",
    "keeper/ears-mail.py",
    "keeper/ears-sounds.py",
    "keeper/heard-tail.py",
    "keeper/mouth-sounds.py",
    "keeper/partial-wake.py",
    "keeper/sound-tour.py",
    "shadow/score",
    "skills/ears/scripts/ears-mid-turn",
    "skills/ears/scripts/ears-mouth-tail",
    "speakers/speaker-sidecar2.py",
    "view/push",
)
SH_COPIES = ("keeper/ears-keeper.sh", "keeper/quiet-for")
SH_START = "# --- sessionvoice_home:"
SH_END = "# --- end sessionvoice_home ---"
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


def make(root: Path, state: str) -> None:
    """Put ``root`` in ``state``: none (absent), bare (the dir only), empty (an empty
    logs/conversations), other (only files that are not the heard log), lock, jsonl."""
    conv = root / "logs" / "conversations"
    if state == "none":
        return
    if state == "bare":
        root.mkdir(parents=True)
        (root / "mouth").mkdir()
        (root / "talk").mkdir()
        return
    conv.mkdir(parents=True)
    if state == "other":
        (conv / "exchanges_2030-01-04.jsonl").write_text("{}\n")
        (conv / "speakers_2030-01-04.jsonl").write_text("{}\n")
        (conv / "heard_cursor").write_text("1\n")
    elif state == "lock":
        (conv / "heard.lock").write_text("")
    elif state == "jsonl":
        (conv / "heard_2030-01-04.jsonl").write_text('{"seq": 1}\n')


# --- the module: the five steps ---------------------------------------------------------

def test_fresh_host_is_the_new_default_and_nothing_is_made(fake_home):
    assert home.sessionvoice_home() == fake_home / ".sessionvoice"
    assert list(fake_home.iterdir()) == [], "the resolver makes no directory"


@pytest.mark.parametrize("marker", ["lock", "jsonl"])
def test_new_before_old_when_only_new_is_populated(fake_home, marker):
    make(fake_home / ".sessionvoice", marker)
    make(fake_home / ".voicemode", "other")
    assert home.sessionvoice_home() == fake_home / ".sessionvoice"


@pytest.mark.parametrize("marker", ["lock", "jsonl"])
def test_old_is_adopted_in_place_when_only_old_is_populated(fake_home, marker):
    make(fake_home / ".voicemode", marker)
    assert home.sessionvoice_home() == fake_home / ".voicemode"


@pytest.mark.parametrize("stray", ["bare", "empty", "other"])
def test_an_unpopulated_new_dir_never_decides(fake_home, stray):
    """An EMPTY ~/.sessionvoice beside a populated ~/.voicemode resolves to ~/.voicemode."""
    make(fake_home / ".sessionvoice", stray)
    make(fake_home / ".voicemode", "jsonl")
    assert home.sessionvoice_home() == fake_home / ".voicemode"


@pytest.mark.parametrize("stray", ["bare", "empty", "other"])
def test_an_unpopulated_old_dir_never_decides(fake_home, stray):
    make(fake_home / ".voicemode", stray)
    assert home.sessionvoice_home() == fake_home / ".sessionvoice"


def test_both_populated_picks_new_and_says_split(fake_home):
    make(fake_home / ".sessionvoice", "jsonl")
    make(fake_home / ".voicemode", "lock")
    assert home.sessionvoice_home() == fake_home / ".sessionvoice"
    assert home.split() == (fake_home / ".sessionvoice", fake_home / ".voicemode")
    rows = home.status_lines()
    assert rows[0] == f"home   {fake_home / '.sessionvoice'} (has the heard log)"
    assert "WARNING: THE HEARD LOG IS SPLIT" in rows[1]
    assert str(fake_home / ".sessionvoice") in rows[1] and str(fake_home / ".voicemode") in rows[1]


def test_the_split_warning_names_the_fix(fake_home):
    """A fresh host that also runs VoiceMode's converse as a heard guest splits the log
    unless sessionvoice is pointed at converse's folder; the warning says exactly how."""
    make(fake_home / ".sessionvoice", "jsonl")
    make(fake_home / ".voicemode", "jsonl")
    warning = "\n".join(home.status_lines()[1:])
    assert "VoiceMode's converse" in warning
    assert "SESSIONVOICE_HOME=~/.voicemode (or VOICEMODE_BASE_DIR)" in warning
    assert "so that both write one log" in warning


def test_one_populated_is_no_split(fake_home):
    make(fake_home / ".voicemode", "jsonl")
    make(fake_home / ".sessionvoice", "empty")
    assert home.split() is None
    assert home.status_lines() == [f"home   {fake_home / '.voicemode'} (has the heard log)"]


def test_voicemode_base_dir_is_read_old(fake_home, tmp_path, monkeypatch):
    """The same EMPTY-new, populated-old host, with $VOICEMODE_BASE_DIR set: it wins."""
    make(fake_home / ".sessionvoice", "empty")
    make(fake_home / ".voicemode", "jsonl")
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm"))
    assert home.sessionvoice_home() == tmp_path / "vm"
    assert home.status_lines()[0] == f"home   {tmp_path / 'vm'} ($VOICEMODE_BASE_DIR)"


def test_voicemode_base_dir_beats_a_populated_new_dir(fake_home, tmp_path, monkeypatch):
    make(fake_home / ".sessionvoice", "lock")
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm"))
    assert home.sessionvoice_home() == tmp_path / "vm"


def test_sessionvoice_home_wins(fake_home, tmp_path, monkeypatch):
    make(fake_home / ".sessionvoice", "lock")
    make(fake_home / ".voicemode", "lock")
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm"))
    monkeypatch.setenv("SESSIONVOICE_HOME", str(tmp_path / "sv"))
    assert home.sessionvoice_home() == tmp_path / "sv"
    assert home.status_lines()[0].endswith("($SESSIONVOICE_HOME)")


def test_an_empty_variable_is_not_set(fake_home, tmp_path, monkeypatch):
    monkeypatch.setenv("SESSIONVOICE_HOME", "")
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm"))
    assert home.sessionvoice_home() == tmp_path / "vm"
    monkeypatch.setenv("VOICEMODE_BASE_DIR", "")
    assert home.sessionvoice_home() == fake_home / ".sessionvoice"


def test_a_tilde_in_the_variable_is_the_home(fake_home, monkeypatch):
    monkeypatch.setenv("SESSIONVOICE_HOME", "~/elsewhere")
    assert home.sessionvoice_home() == fake_home / "elsewhere"


def test_the_subtree_follows_the_root(fake_home):
    """heard_*, heard.lock, state/, mouth/ and talk/ all hang off the one root."""
    make(fake_home / ".voicemode", "lock")
    root = fake_home / ".voicemode"
    assert heard.base_dir() == root
    assert heard.log_dir() == root / "logs" / "conversations"
    assert heard.cursor_dir() == root / "state" / "heard-cursor"
    assert paths.default_mouth_dir() == root / "mouth"
    from talk import token_store
    assert token_store.path().parent == root / "talk"


def test_services_status_prints_the_home_and_the_split(sx, capsys):
    h = sx["home"]
    make(h / ".sessionvoice", "jsonl")
    make(h / ".voicemode", "jsonl")
    assert services.status() == 0
    out = capsys.readouterr().out
    assert f"home   {h / '.sessionvoice'} (has the heard log)" in out
    assert "WARNING: THE HEARD LOG IS SPLIT" in out
    assert "SESSIONVOICE_HOME=~/.voicemode (or VOICEMODE_BASE_DIR)" in out


# --- every copy, held to the module ---------------------------------------------------------

STATES = ("none", "bare", "empty", "other", "lock", "jsonl")
ENVS = (
    {},
    {"VOICEMODE_BASE_DIR": "{tmp}/vm"},
    {"SESSIONVOICE_HOME": "{tmp}/sv"},
    {"SESSIONVOICE_HOME": "{tmp}/sv", "VOICEMODE_BASE_DIR": "{tmp}/vm"},
    {"SESSIONVOICE_HOME": "", "VOICEMODE_BASE_DIR": "{tmp}/vm"},
    {"VOICEMODE_BASE_DIR": "~/vm"},
)


def scenarios(tmp_path):
    """Each (HOME, env): every state of ~/.sessionvoice beside every state of ~/.voicemode,
    under each env. Yields after building the HOME; the next one is a fresh dir."""
    for i, (new, old) in enumerate(itertools.product(STATES, STATES)):
        h = tmp_path / f"h{i}"
        h.mkdir()
        make(h / ".sessionvoice", new)
        make(h / ".voicemode", old)
        for env in ENVS:
            yield h, {k: v.replace("{tmp}", str(tmp_path)) for k, v in env.items()}, (new, old)


def _py_copy(rel: str):
    """The file's own ``sessionvoice_home``, compiled alone (its script never runs)."""
    src = (ROOT / rel).read_text()
    fn = [n for n in ast.parse(src).body
          if isinstance(n, ast.FunctionDef) and n.name == "sessionvoice_home"]
    assert len(fn) == 1, f"{rel}: one top-level sessionvoice_home, found {len(fn)}"
    ns = {"os": os, "glob": glob, "Path": Path}
    exec(compile(ast.Module(body=fn, type_ignores=[]), rel, "exec"), ns)
    return ns["sessionvoice_home"]


def _sh_copy(rel: str) -> str:
    text = (ROOT / rel).read_text()
    assert text.count(SH_START) == 1 and text.count(SH_END) == 1, rel
    return text[text.index(SH_START):text.index(SH_END) + len(SH_END)]


@pytest.mark.parametrize("rel", PY_COPIES)
def test_each_python_copy_agrees_with_the_module(rel, tmp_path, monkeypatch):
    copy = _py_copy(rel)
    for h, env, case in scenarios(tmp_path):
        monkeypatch.setenv("HOME", str(h))
        for n in home.ENV:
            monkeypatch.delenv(n, raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        assert str(copy()) == str(home.sessionvoice_home()), (rel, case, env)


@pytest.mark.parametrize("rel", SH_COPIES)
def test_each_shell_copy_agrees_with_the_module(rel, tmp_path, monkeypatch):
    fn = _sh_copy(rel)
    cases, script = [], [fn]
    for h, env, case in scenarios(tmp_path):
        cases.append((h, env, case))
        assigns = " ".join(f"{k}={v!r}" for k, v in env.items())
        script.append(f"( unset SESSIONVOICE_HOME VOICEMODE_BASE_DIR; HOME={str(h)!r}; {assigns}\n"
                      f"  {'export ' + ' '.join(env) + ';' if env else ''} sessionvoice_home )")
    clean = {k: v for k, v in os.environ.items() if k not in home.ENV}
    got = subprocess.run(["bash", "-c", "\n".join(script)], env=clean, capture_output=True,
                         text=True, check=True).stdout.splitlines()
    assert len(got) == len(cases)
    for line, (h, env, case) in zip(got, cases):
        monkeypatch.setenv("HOME", str(h))
        for n in home.ENV:
            monkeypatch.delenv(n, raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        assert line == str(home.sessionvoice_home()), (rel, case, env)


def test_heard_base_dir_is_its_copy(fake_home):
    make(fake_home / ".voicemode", "jsonl")
    assert heard.base_dir() == Path(heard.sessionvoice_home()) == home.sessionvoice_home()


def _scanned_files():
    for d in SCANNED:
        for p in sorted((ROOT / d).rglob("*")):
            # a symlink (src/ears/_keeper/, _speakers/: what the wheel ships) is its target
            if p.is_file() and not p.is_symlink() and "__pycache__" not in p.parts and ".egg-info" not in str(p) \
                    and p.suffix not in (".md", ".json", ".onnx", ".wav", ".txt", ".plist", ".in"):
                yield p


def test_every_copy_is_listed():
    """A file that defines the rule is one of the copies above (or the module itself)."""
    found = set()
    for p in _scanned_files():
        try:
            text = p.read_text()
        except UnicodeDecodeError:
            continue
        if re.search(r"^def sessionvoice_home\(|^sessionvoice_home\(\) \{", text, re.M):
            found.add(p.relative_to(ROOT).as_posix())
    assert found == set(PY_COPIES) | set(SH_COPIES) | {"src/ears/home.py"}


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
        if rel in SH_COPIES:
            text = text.replace(_sh_copy(rel), "")
        elif rel in PY_COPIES:
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
        "src/ears/heard.py: or, on a host that already keeps it there, ``~/.voicemode``). It rolls at the",
        # the bench strips the runner's old name before it sets the new one
        'bench/timeline_bench.py: and k not in ("PYTHONPATH", "VOICEMODE_BASE_DIR")}',
    }
    assert [b for b in bad if b not in allowed] == []
