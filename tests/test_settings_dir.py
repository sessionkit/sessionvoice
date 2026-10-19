"""The voice settings folder: ``ears.home.SETTINGS``, and every copy of it.

Every voice settings file (the ears' and the mouth's alike) sits in ONE folder under
$HOME, named in one place per language: ``ears.home.SETTINGS`` for the package, and a
copy in each script that imports nothing from src/ (the keeper's Python, bin/render-plist)
and in the keeper's shell. These tests hold every copy to the module, check that each
reader resolves its file there under its own name, and refuse an old ``~/.settings`` path
anywhere in the repo but the lines ALLOWED names, each exactly once. Everything is under a
fake HOME in tmp_path.
"""
import ast
import os
import re
import subprocess
from pathlib import Path

import pytest

from ears import home, keys, recording, wake
from ears.listen import stt
from mouth import backends, voices

ROOT = Path(__file__).resolve().parents[1]

#: The kit's map: <kit root>/<package>/settings, with the ears and the mouth in "voice".
SHAPE = Path(".sessionkit") / "voice" / "settings"

#: The stdlib-only scripts that keep their own copy, as VOICE_SETTINGS = "...".
PY_COPIES = (
    "bin/render-plist",
    "keeper/ears-mail.py",
    "keeper/ears-sounds.py",
    "keeper/mouth-sounds.py",
)
SH_COPY = "keeper/ears-keeper.sh"
#: Where code that reads a settings file could live.
SCANNED = ("src", "bin", "keeper", "launchd", "settings", "shadow", "skills", "speakers",
           "view", "bench")
#: An old-settings path in any form: ``.settings`` not after a name (``self.settings``) and
#: not the start of a longer one (``.settings_url``).
OLD = re.compile(r"(?<![\w)\]])\.settings\b")
#: The ONLY lines in the repo where OLD may match: (file, a marker unique to the line,
#: why). Each must match exactly once while its file is there; any other hit fails.
#: The receipt names each by file:line; a marker, not a line number, so that a line added
#: above one (a CHANGELOG entry, say) does not move it.
NOTIFICATIONS = "a named exception: live path, not a kit package, unassigned"
FIXTURE = "a HOME=tmp test or fixture of the old path"
ALLOWED = (
    ("src/ears/home.py", "LEGACY_SETTINGS = {", "the one legacy constant: the stranded line only"),
    ("docs/settings.md", "(`ears.json`, `wake.json`, `ears-sounds.json`, `stt.key`) and",
     "the one upgrading-from-0.1.0 note"),
    ("keeper/ears-sounds.py", "GLOBAL = Path(", NOTIFICATIONS),
    ("docs/configuration.md", "**The global switch:** `allow` in", NOTIFICATIONS),
    ("docs/settings.md", "silences these sounds as well", NOTIFICATIONS),
    ("docs/internal/INVENTORY.md", "Settings in `", "the history block"),
    ("docs/internal/INVENTORY.md", "README.md`). Branch", "the history block"),
    ("docs/internal/INVENTORY.md", "`: `ears.json` (`EARS_CONF`)", "the history block"),
    ("docs/internal/INVENTORY.md", "`: `mouth.json`, `mouth-sounds.json`", "the history block"),
    ("docs/internal/INVENTORY.md", "(`NOTIFY_GLOBAL`): ", "the history block"),
    ("CHANGELOG.md", 'then `"record"` in `', "a landed CHANGELOG entry"),
    ("CHANGELOG.md", "sessiontools' `", "a landed CHANGELOG entry"),
    # The HOME=tmp tests and fixtures of the old path, line by line like the rest: a new
    # line in either file fails too. No marker holds the old path, so none hits itself.
    ("tests/test_settings_dir.py", "reader resolves its file there under its own name, and refuse",
     FIXTURE),
    ("tests/test_settings_dir.py", "#: An old-settings path in any form:", FIXTURE),
    ("tests/test_settings_dir.py", 'OLD = re.compile(r"', FIXTURE),
    ("tests/test_settings_dir.py", 'extra, missing = judge(good + [("src/ears/x.py", 9,', FIXTURE),
    ("tests/test_settings_dir.py", 'assert extra == ["src/ears/x.py:9:', FIXTURE),
    ("tests/test_settings_dir.py", 'judge(good + [("tests/test_settings_stranded.py", 999,', FIXTURE),
    ("tests/test_settings_dir.py", 'assert extra == ["tests/test_settings_stranded.py:999:', FIXTURE),
    ("tests/test_settings_dir.py", "old = fake_home / ", FIXTURE),
    ("tests/test_settings_dir.py", "not in recording.conf_path()", FIXTURE),
    ("tests/test_settings_dir.py", "not in str(wake.target())", FIXTURE),
    ("tests/test_settings_stranded.py", 'assert home.LEGACY_SETTINGS == {"ears": ', FIXTURE),
    ("tests/test_settings_stranded.py", "assert home.legacy_path(name) == fake_home / ", FIXTURE),
    ("tests/test_settings_stranded.py", "not in home.STRANDED_HELP and", FIXTURE),
    ("tests/test_settings_stranded.py", "not in text and", FIXTURE),
)
#: The files whose allowed lines are tests and fixtures, not code (the one-constant check).
FIXTURE_FILES = {rel for rel, _, why in ALLOWED if why == FIXTURE}
#: Not walked when the repo is not a git checkout (an archive).
PRUNE = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", "build", "dist"}


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    for n in ("EARS_CONF", "EARS_WAKE", "MOUTH_SETTINGS"):
        monkeypatch.delenv(n, raising=False)
    return h


def test_the_folder_is_the_kits_package_first_shape(fake_home):
    assert Path(home.SETTINGS) == SHAPE
    assert home.settings_dir() == fake_home / SHAPE
    assert home.settings_dir(fake_home / "other") == fake_home / "other" / SHAPE


def test_settings_dir_reads_home_on_every_call(fake_home, monkeypatch, tmp_path):
    first = home.settings_dir()
    monkeypatch.setenv("HOME", str(tmp_path / "moved"))
    assert home.settings_dir() == tmp_path / "moved" / SHAPE != first


@pytest.mark.parametrize("path", PY_COPIES)
def test_each_python_copy_matches_the_module(path):
    tree = ast.parse((ROOT / path).read_text())
    got = [n.value.value for n in ast.walk(tree)
           if isinstance(n, ast.Assign) and len(n.targets) == 1
           and getattr(n.targets[0], "id", None) == "VOICE_SETTINGS"
           and isinstance(n.value, ast.Constant)]
    assert got == [home.SETTINGS], path


def test_the_shell_copy_matches_the_module():
    lines = re.findall(r'^VOICE_SETTINGS="([^"]*)"$', (ROOT / SH_COPY).read_text(), re.M)
    assert lines == [f"$HOME/{home.SETTINGS}"]


def _scanned():
    """(relative path, text) of every text file where a reader could live."""
    for top in SCANNED:
        for f in sorted((ROOT / top).rglob("*")):
            if f.is_file() and not f.is_symlink() and "__pycache__" not in f.parts:
                try:
                    yield str(f.relative_to(ROOT)), f.read_text()
                except (UnicodeDecodeError, OSError):
                    continue


def test_every_copy_is_listed():
    """Code that builds the folder from a string literal is the module or a listed copy.
    Prose (a docstring's ``~/.sessionkit/...``, a template comment) is not code."""
    literal = re.compile(r"""["'](?:~/|\$HOME/|@HOME@/)?\.sessionkit\b""")
    named = {path for path, text in _scanned() if literal.search(text)}
    assert named == {"src/ears/home.py", SH_COPY, *PY_COPIES}


def _repo_files():
    """Every file of the repo, relative: git's list in a checkout, else a walk (an archive)."""
    try:
        r = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True,
                           text=True, timeout=60)
        if r.returncode == 0 and r.stdout:
            return sorted(f for f in r.stdout.split("\0") if f and (ROOT / f).is_file())
    except (OSError, subprocess.SubprocessError):
        pass
    out = []
    for top, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in PRUNE]
        out += [str((Path(top) / f).relative_to(ROOT)) for f in files]
    return sorted(out)


def old_hits(files=None):
    """(file, line number, line) of every OLD match in the repo's text files."""
    hits = []
    for rel in _repo_files() if files is None else files:
        f = ROOT / rel
        if f.is_symlink():
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        hits += [(rel, i, line) for i, line in enumerate(text.splitlines(), 1) if OLD.search(line)]
    return hits


def judge(hits, allowed=ALLOWED, present=None):
    """(extra, missing): hits no allowed line covers, and allowed lines not hit exactly once
    (while their file is present), each as ``file:line`` text."""
    present = (lambda rel: (ROOT / rel).is_file()) if present is None else present
    extra, by_rule = [], {rule: [] for rule in allowed}
    for rel, i, line in hits:
        rules = [r for r in allowed if r[0] == rel and r[1] in line]
        if len(rules) == 1:
            by_rule[rules[0]].append(f"{rel}:{i}")
        else:
            extra.append(f"{rel}:{i}: {line.strip()}")
    missing = [f"{rel}: {marker!r} ({why}) hit {len(at)}x {at}"
               for (rel, marker, why), at in by_rule.items() if len(at) != 1 and present(rel)]
    return extra, missing


def test_the_old_path_appears_only_on_the_allowed_lines():
    """No reader, writer, template, schema, default, doc or help names the old folders,
    but the ALLOWED lines, each exactly once: fails on any other hit AND on a missing one."""
    extra, missing = judge(old_hits())
    assert extra == [], "an old-settings path outside ALLOWED"
    assert missing == [], "an ALLOWED line is gone or doubled"


def test_the_gate_fails_on_an_extra_hit_and_on_a_missing_one():
    good = [(rel, n, f"x {marker} y") for n, (rel, marker, _) in enumerate(ALLOWED, 1)]
    assert judge(good, present=lambda rel: True) == ([], [])
    extra, missing = judge(good + [("src/ears/x.py", 9, "p = HOME / '.settings'")],
                           present=lambda rel: True)
    assert extra == ["src/ears/x.py:9: p = HOME / '.settings'"] and missing == []
    extra, missing = judge(good[1:], present=lambda rel: True)
    assert extra == [] and len(missing) == 1 and missing[0].startswith("src/ears/home.py:")
    extra, missing = judge(good + good[:1], present=lambda rel: True)   # doubled
    assert extra == [] and "hit 2x" in missing[0]
    # a test file is held line by line too: a new hit in it is extra, a gone one missing
    fixture = [h for h in good if h[0] == "tests/test_settings_stranded.py"]
    extra, missing = judge(good + [("tests/test_settings_stranded.py", 999, "x = '.settings/new'")],
                           present=lambda rel: True)
    assert extra == ["tests/test_settings_stranded.py:999: x = '.settings/new'"] and missing == []
    extra, missing = judge([h for h in good if h != fixture[0]], present=lambda rel: True)
    assert extra == [] and len(missing) == 1 and missing[0].startswith("tests/test_settings_stranded.py:")
    # an export-ignored file absent (an archive): its lines are not required
    assert judge([h for h in good if h[0] != "docs/internal/INVENTORY.md"],
                 present=lambda rel: rel != "docs/internal/INVENTORY.md") == ([], [])


def test_exactly_one_legacy_constant_in_the_whole_repo():
    """The old folders are named by ONE module-level constant, in src/ears/home.py."""
    code = sorted((rel, line.split("=")[0].strip()) for rel, _, line in old_hits()
                  if rel not in FIXTURE_FILES and not rel.endswith(".md"))
    # beside it, only the notifications line (NOTIFICATIONS), which is not a voice folder
    assert code == [("keeper/ears-sounds.py", "GLOBAL"), ("src/ears/home.py", "LEGACY_SETTINGS")]
    defs = [rel for rel in _repo_files() if rel.endswith(".py")
            for n in ast.walk(ast.parse((ROOT / rel).read_text()))
            if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "LEGACY_SETTINGS"
                                                 for t in n.targets)]
    assert defs == ["src/ears/home.py"]


def test_every_reader_resolves_its_file_in_the_folder_by_its_own_name(fake_home):
    d = home.settings_dir()
    assert Path(recording.conf_path()) == d / "ears.json"
    assert Path(stt.conf_path()) == d / "ears.json"
    assert keys.stt_key_path() == d / "stt.key"
    assert wake.target() == d / "wake.json"
    assert voices.home_settings_path() == d / "mouth.json"
    assert backends.tts_key_path() == d / "tts.key"


def test_the_per_file_variables_still_name_one_file_each(fake_home, monkeypatch, tmp_path):
    """The folder has no variable; the variables that name one file each still do."""
    monkeypatch.setenv("EARS_CONF", str(tmp_path / "e.json"))
    monkeypatch.setenv("EARS_WAKE", str(tmp_path / "w.json"))
    monkeypatch.setenv("MOUTH_SETTINGS", str(tmp_path / "m.json"))
    assert recording.conf_path() == stt.conf_path() == str(tmp_path / "e.json")
    assert wake.target() == tmp_path / "w.json"
    assert voices.settings_path() == tmp_path / "m.json"
    # the key files are fixed: no variable moves them
    assert keys.stt_key_path() == home.settings_dir() / "stt.key"
    assert backends.tts_key_path() == home.settings_dir() / "tts.key"


def test_nothing_reads_the_old_folder_when_only_it_holds_a_file(fake_home):
    """No fallback: a file left at the old path is not read."""
    old = fake_home / ".settings"
    for sub, name, body in (("ears", "ears.json", "{}"), ("mouth", "mouth.json", '{"voice": "bm_george"}'),
                            ("ears", "wake.json", "{}")):
        (old / sub).mkdir(parents=True, exist_ok=True)
        (old / sub / name).write_text(body)
    assert ".settings" not in recording.conf_path()
    assert ".settings" not in str(wake.target())
    assert voices.home_settings_path().parent == home.settings_dir()
    assert not voices.home_settings_path().exists()
    assert voices.settings().get("voice") != "bm_george"
