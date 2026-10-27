"""EARS-483: every voice log goes in ONE folder, named in ONE place: ``ears.home.log_dir()``.

The folder is ``~/.sessionkit/voice/log`` (the kit's log home, beside the settings), and
``$EARS_LOG_DIR`` moves it. Nothing else in the repo names it: the stdlib-only scripts load
``src/ears/home.py`` by its path, the keeper's shell asks ``python -m ears.home log-dir``, and
``bin/render-plist`` fills the templates' ``@EARS_LOG_DIR@`` from it. The old folders
(``ears.home.LEGACY_LOGS``, under ``~/Library/Logs``) are only ever LOOKED at, for one pointer
line in ``ears services status``; nothing writes there, and nothing deletes from there, not
even ``ears services uninstall``.

The SCAN at the bottom holds the repo's code (src/, keeper/, launchd/, view/, bin/) to that:
no old folder, no copy of the new one, no other folder a log defaults to. What it exempts it
names, with the reason.

Every test runs under a temp HOME; nothing here touches launchd or the real home."""
from __future__ import annotations

import ast
import importlib.util
import os
import plistlib
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from ears import home, services
from ears.home import LEGACY_LOGS, LOG, log_dir, log_status_lines, old_logs
from test_services_install_checks import _store, fence  # noqa: F401  (fence: a fixture)
from test_services_speakers import sp  # noqa: F401  (a fixture)

ROOT = Path(__file__).resolve().parents[1]
UID = os.getuid()


@pytest.fixture
def h(tmp_path, monkeypatch):
    """A temp HOME, with no EARS_LOG_DIR from the runner."""
    d = tmp_path / "home"
    d.mkdir()
    monkeypatch.setenv("HOME", str(d))
    monkeypatch.delenv("EARS_LOG_DIR", raising=False)
    return d


def _clean_env(home_dir: Path, **extra) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "EARS_LOG_DIR"}
    env.update(HOME=str(home_dir), **extra)
    return env


# ---- the resolver ----------------------------------------------------------------------------

def test_the_folder_is_the_kits_log_home_beside_the_settings(h):
    assert LOG == ".sessionkit/voice/log"
    assert log_dir() == h / ".sessionkit" / "voice" / "log"
    assert log_dir().parent == (h / home.SETTINGS).parent          # beside voice/settings
    assert log_dir() != h / ".sessionkit" / "log"                    # never a top-level kit log


def test_ears_log_dir_moves_it_expanded_and_empty_is_unset(h, monkeypatch, tmp_path):
    monkeypatch.setenv("EARS_LOG_DIR", str(tmp_path / "mine"))
    assert log_dir() == tmp_path / "mine"
    monkeypatch.setenv("EARS_LOG_DIR", "~/elsewhere")
    assert log_dir() == h / "elsewhere"
    monkeypatch.setenv("EARS_LOG_DIR", "")
    assert log_dir() == h / LOG
    assert log_dir(environ={"EARS_LOG_DIR": "/x"}, home=tmp_path) == Path("/x")
    assert log_dir(environ={}, home=tmp_path) == tmp_path / LOG


def test_log_dir_makes_nothing(h):
    log_dir()
    assert list(h.iterdir()) == []


@pytest.mark.parametrize("how", ["module", "file"])
def test_the_cli_prints_it_for_shell(h, tmp_path, how):
    argv = ([sys.executable, "-m", "ears.home", "log-dir"] if how == "module"
            else [sys.executable, str(ROOT / "src/ears/home.py"), "log-dir"])
    p = subprocess.run(argv, env=_clean_env(h), capture_output=True, text=True, timeout=30)
    assert p.returncode == 0 and p.stdout == f"{h / LOG}\n", p.stderr
    p = subprocess.run(argv, env=_clean_env(h, EARS_LOG_DIR=str(tmp_path / "l")),
                       capture_output=True, text=True, timeout=30)
    assert p.stdout == f"{tmp_path / 'l'}\n"
    p = subprocess.run(argv[:-1], env=_clean_env(h), capture_output=True, text=True, timeout=30)
    assert p.returncode == 2 and "log-dir" in p.stderr and not p.stdout


def test_services_logs_and_the_install_log_are_the_resolvers(h, tmp_path):
    """services.LOGS is log_dir() at import: in a fresh process, under a temp HOME."""
    code = "from ears import services; print(services.LOGS); print(services.install_log_path())"
    for extra, want in (({}, h / LOG), ({"EARS_LOG_DIR": str(tmp_path / "l")}, tmp_path / "l")):
        p = subprocess.run([sys.executable, "-c", code], env=_clean_env(h, **extra),
                           capture_output=True, text=True, timeout=60)
        assert p.returncode == 0, p.stderr
        assert p.stdout.splitlines() == [str(want), str(want / "install.log")]


# ---- the keeper's shell asks the resolver ----------------------------------------------------

def _keeper_logdir(env) -> subprocess.CompletedProcess:
    line = [ln for ln in (ROOT / "keeper/ears-keeper.sh").read_text().splitlines()
            if ln.startswith("LOGDIR=")]
    assert len(line) == 1
    return subprocess.run(["bash", "-c", f'{line[0]}\necho "L[$LOGDIR]"'], env=env,
                          capture_output=True, text=True, timeout=30)


def test_the_keeper_asks_the_resolver_and_stops_when_it_cannot_answer(h, tmp_path):
    p = _keeper_logdir(_clean_env(h, PY=sys.executable))
    assert p.returncode == 0 and p.stdout == f"L[{h / LOG}]\n", p.stderr
    p = _keeper_logdir(_clean_env(h, PY=sys.executable, EARS_LOG_DIR=str(tmp_path / "k")))
    assert p.stdout == f"L[{tmp_path / 'k'}]\n"
    p = _keeper_logdir(_clean_env(h, PY="/usr/bin/false"))       # no answer: no guess, exit 2
    assert p.returncode == 2 and "L[" not in p.stdout and "ears.home log-dir" in p.stderr


# ---- the stdlib-only scripts load home.py by its path ----------------------------------------

LOADERS = ("keeper/ears-sounds.py", "keeper/mouth-sounds.py", "view/push")


def _loader(script: str, here: Path):
    """The script's voice_log_dir(), cut out by its AST and run with HERE set: the scripts
    themselves import audio libraries and start at import."""
    src = (ROOT / script).read_text()
    fn = next(n for n in ast.parse(src).body
              if isinstance(n, ast.FunctionDef) and n.name == "voice_log_dir")
    ns = {"HERE": here, "Path": Path}
    exec(compile(ast.Module([fn], []), script, "exec"), ns)
    return ns["voice_log_dir"]


@pytest.mark.parametrize("script", LOADERS)
def test_a_script_in_the_checkout_gets_the_resolvers_folder(script, h, monkeypatch, tmp_path):
    fn = _loader(script, (ROOT / script).parent)
    assert fn() == h / LOG
    monkeypatch.setenv("EARS_LOG_DIR", str(tmp_path / "e"))
    assert fn() == tmp_path / "e"


@pytest.mark.parametrize("script", LOADERS[:2])
def test_a_keeper_script_in_the_wheel_gets_it_from_the_packages_home_py(script, h, tmp_path):
    """The wheel ships keeper/ as ears/_keeper/: home.py is the package's, one folder up."""
    pkg = tmp_path / "site" / "ears"
    (pkg / "_keeper").mkdir(parents=True)
    (pkg / "home.py").write_text((ROOT / "src/ears/home.py").read_text())
    assert _loader(script, pkg / "_keeper")() == h / LOG


@pytest.mark.parametrize("script", LOADERS)
def test_no_home_py_is_a_loud_exit_2_never_a_guess(script, h, tmp_path, capsys):
    lone = tmp_path / "lone" / "keeper"
    lone.mkdir(parents=True)
    with pytest.raises(SystemExit) as e:
        _loader(script, lone)()
    assert e.value.code == 2
    assert "home.py" in capsys.readouterr().err


def test_the_sounds_scripts_default_their_logs_from_it():
    for script, name, var in (("keeper/ears-sounds.py", "ears-sounds.jsonl", "EARS_SOUNDS_LOG"),
                              ("keeper/mouth-sounds.py", "mouth-sounds.jsonl", "MOUTH_SOUNDS_LOG")):
        text = (ROOT / script).read_text()
        assert f'voice_log_dir() / "{name}"' in text and var in text, script
    assert 'LOG = str(voice_log_dir() / "ears-view-push.log")' in (ROOT / "view/push").read_text()


# ---- the mouth's player.log and split.log ----------------------------------------------------

class _Child:
    pid = 4242


def test_the_player_logs_to_the_folder_not_the_mouth_dir(h, tmp_path, monkeypatch):
    import subprocess as sp_mod

    import mouth

    seen = []
    monkeypatch.setattr(mouth, "_player_running", lambda d: False)
    monkeypatch.setattr(sp_mod, "Popen", lambda argv, **kw: seen.append(kw["stdout"].name) or _Child())
    d = tmp_path / "mouth"
    assert mouth.ensure_player(d) is True
    assert seen == [str(h / LOG / "player.log")]
    assert not (d / "player.log").exists()


def test_a_split_logs_to_the_folder_not_the_splits_dir(h, tmp_path, monkeypatch):
    from mouth import split

    seen = []
    monkeypatch.setattr(split.subprocess, "Popen",
                        lambda argv, **kw: seen.append(kw["stdout"].name) or _Child())
    d = tmp_path / "mouth"
    assert split.spawn(tmp_path / "s.flac", d) == 4242
    assert seen == [str(h / LOG / "split.log")]
    assert (d / "splits").is_dir() and not (d / "splits" / "split.log").exists()


# ---- old logs stay: uninstall deletes plists only --------------------------------------------

def _tree(root: Path) -> dict:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_uninstall_deletes_plists_only_and_never_a_log(sp, capsys):
    """The ruling (EARS-372's brief, Rules): nothing in voice deletes from ~/Library/Logs,
    ever. uninstall boots out and deletes plists; every log, old folder and new, stays."""
    home_dir = sp["home"]
    old = {rel: home_dir / rel for rel in LEGACY_LOGS}
    for d in old.values():
        (d / "sub").mkdir(parents=True)
        (d / "ears-keeper.log").write_text("old keeper lines\n")
        (d / "sub" / "deep.jsonl").write_text("{}\n")
    services.LOGS.mkdir(parents=True, exist_ok=True)
    (services.LOGS / "ears-mail.log").write_text("new mail lines\n")
    (services.LOGS / "install.log").write_text("=== an install\n")
    services.AGENTS.mkdir(parents=True, exist_ok=True)
    plists = []
    for n in services.jobs(services.ALL):
        p = services.AGENTS / f"{services._label(n)}.plist"
        p.write_bytes(plistlib.dumps({"Label": services._label(n),
                                      "StandardOutPath": str(services.LOGS / "x.log"),
                                      "EnvironmentVariables": dict([services.MARKER])}))
        plists.append(p)
    logs_before = {d: _tree(d) for d in (*old.values(), services.LOGS)}
    assert services.main(["uninstall"]) == 0
    assert not [p for p in plists if p.exists()]                     # the plists: gone
    for d, before in logs_before.items():
        assert d.is_dir() and _tree(d) == before, d                  # every log: untouched
    assert "removed" in capsys.readouterr().out


def test_uninstall_has_no_code_path_to_a_log():
    """The pin above, by construction too: uninstall() names no log folder and removes only
    the files it builds from AGENTS."""
    import inspect

    src = inspect.getsource(services.uninstall)
    assert "LOGS" not in src and "Library/Logs" not in src and "log_dir" not in src
    assert re.findall(r"(\w+)\.unlink\(", src) == ["plist"]
    assert "rmtree" not in src and "rmdir" not in src


# ---- the old-path reader: one pointer line in `ears services status` --------------------------

def test_no_old_folder_no_pointer(h):
    assert old_logs() == []
    assert log_status_lines() == [f"logs   {h / LOG} (the default)"]


def test_an_old_folder_with_files_gets_one_pointer_naming_the_new(h):
    old = h / "Library/Logs/ears"
    old.mkdir(parents=True)
    (old / "ears-keeper.log").write_text("x")
    rows = log_status_lines()
    assert len(rows) == 2
    assert str(h / LOG) in rows[1] and str(old) in rows[1]
    assert "left in place, never deleted" in rows[1]
    assert (old / "ears-keeper.log").read_text() == "x"              # a look, not a touch


def test_both_old_folders_share_the_one_line(h):
    for rel in LEGACY_LOGS:
        (h / rel / "deep").mkdir(parents=True)
        (h / rel / "deep" / "f.log").write_text("x")
    rows = log_status_lines()
    assert len(rows) == 2
    assert all(str(h / rel) in rows[1] for rel in LEGACY_LOGS)


def test_an_empty_old_folder_needs_no_pointer(h):
    (h / "Library/Logs/ears/empty").mkdir(parents=True)
    assert old_logs() == [] and len(log_status_lines()) == 1


def test_the_pointer_names_ears_log_dir_when_it_moves_the_folder(h, monkeypatch, tmp_path):
    monkeypatch.setenv("EARS_LOG_DIR", str(tmp_path / "m"))
    (h / "Library/Logs/ears").mkdir(parents=True)
    (h / "Library/Logs/ears/a.log").write_text("x")
    rows = log_status_lines()
    assert rows[0] == f"logs   {tmp_path / 'm'} ($EARS_LOG_DIR)"
    assert f"now in {tmp_path / 'm'};" in rows[1]


def test_services_status_prints_the_pointer(sp, capsys):
    old = sp["home"] / "Library/Logs/ears"
    old.mkdir(parents=True)
    (old / "ears-mail.log").write_text("x")
    assert services.status() == 0
    out = capsys.readouterr().out.splitlines()
    rows = [ln for ln in out if ln.startswith("logs   ")]
    assert rows == [f"logs   {services.LOGS} (the default)"]
    ptr = out[out.index(rows[0]) + 1]
    assert f"the logs are now in {services.LOGS};" in ptr and str(old) in ptr
    assert (old / "ears-mail.log").read_text() == "x"


def test_services_status_has_no_pointer_once_the_old_folders_are_empty(sp, capsys):
    assert services.status() == 0
    out = capsys.readouterr().out
    assert f"logs   {services.LOGS} (the default)" in out and "the logs are now in" not in out


# ---- the SCAN: no log default outside the resolver's folder ----------------------------------

SCANNED = ("src", "keeper", "launchd", "view", "bin")
#: The one file that names the folder, and the old ones (LEGACY_LOGS, for the pointer).
RESOLVER = "src/ears/home.py"
#: A log that is NOT in the folder, on purpose, by (file, the text that names it): why.
EXEMPT = {
    # the heard log (and its speakers_ rows beside it): <sessionvoice home>/logs/conversations,
    # ears.heard.log_dir. Out of scope (EARS-372 brief: "NOT in scope: the heard logs"). Every
    # stdlib script keeps its own copy of the home rule to find it.
    "logs/conversations": "the heard log, under sessionvoice's home: not a voice log",
    '"logs", "conversations"': "the heard log, by parts",
    '"logs" / "conversations"': "the heard log, by parts (ears.heard, ears.home.populated)",
    # VoiceMode's own exchanges log, in VoiceMode's home: never ours to move
    ".voicemode/logs/conversations": "VoiceMode's exchanges log",
    # mlx-audio's own --log-dir default, which services.spec("speech") overrides with LOGS
    'a relative "logs"': "mlx-audio's default, named in the comment on the flag that replaces it",
}
#: Text that names the install log's old place as history, in the docstring that says it moved.
HISTORY = ("home>/logs/install.log``; that file stays where it is",)


def _scanned():
    out = subprocess.run(["git", "ls-files", *SCANNED], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout.split()
    for rel in out:
        p = ROOT / rel
        # symlinks are the wheel's views of keeper/ and speakers/ (src/ears/_keeper, _speakers):
        # keeper/ is scanned as itself; speakers/ is out of scope (the live bus)
        if p.is_symlink() or not p.is_file() or rel.endswith(".md"):
            continue
        try:
            yield rel, p.read_text()
        except UnicodeDecodeError:
            continue


def test_the_scan_sees_every_writer():
    seen = {rel for rel, _ in _scanned()}
    for rel in ("src/ears/services.py", "src/mouth/__init__.py", "src/mouth/split.py",
                "keeper/ears-keeper.sh", "keeper/ears-sounds.py", "keeper/mouth-sounds.py",
                "view/push", "bin/render-plist", RESOLVER,
                *(f"launchd/{t.name}" for t in (ROOT / "launchd").glob("*.plist.in"))):
        assert rel in seen, rel


def test_no_code_names_the_old_log_folders():
    hits = [f"{rel}:{i}: {ln.strip()}" for rel, text in _scanned() if rel != RESOLVER
            for i, ln in enumerate(text.splitlines(), 1) if "Library/Logs" in ln]
    assert hits == []


def test_no_code_copies_the_new_folder():
    """ONE place names it: everything else asks log_dir() (EARS-483: no new hand copy)."""
    hits = [f"{rel}:{i}: {ln.strip()}" for rel, text in _scanned() if rel != RESOLVER
            for i, ln in enumerate(text.splitlines(), 1)
            if "sessionkit/voice/log" in ln or ".sessionkit/log" in ln]
    assert hits == []


def test_no_log_defaults_to_any_other_logs_folder():
    """A `logs` folder anywhere in code is the heard log's (exempt, by name) or a bug."""
    word = re.compile(r"""(?<![\w-])logs/|["']logs["']""")
    hits = []
    for rel, text in _scanned():
        if rel == RESOLVER:
            continue
        for i, ln in enumerate(text.splitlines(), 1):
            if not word.search(ln) or any(h in ln for h in HISTORY):
                continue
            rest = ln
            for name in sorted(EXEMPT, key=len, reverse=True):
                rest = rest.replace(name, "")
            if word.search(rest):
                hits.append(f"{rel}:{i}: {ln.strip()}")
    assert hits == []


def test_every_launch_agent_log_is_under_the_placeholder():
    """In the plist body (comments are prose), every log file a template names is
    @EARS_LOG_DIR@/<name>: render-plist fills it from log_dir()."""
    for t in sorted((ROOT / "launchd").glob("*.plist.in")):
        body = re.sub(r"<!--.*?-->", "", t.read_text(), flags=re.S)
        for m in re.finditer(r"<string>([^<]*\.(?:log|jsonl))</string>", body):
            assert m.group(1).startswith("@EARS_LOG_DIR@/"), (t.name, m.group(1))


def test_every_keeper_log_in_shell_is_under_logdir():
    """keeper/ears-keeper.sh writes its logs as $LOGDIR/<name>; the .jsonl it names are the
    heard log's (heard_/speakers_, read, never a voice log)."""
    text = (ROOT / "keeper/ears-keeper.sh").read_text()
    for i, ln in enumerate(text.splitlines(), 1):
        code = ln.split("#", 1)[0] if not ln.lstrip().startswith("#") else ""
        for m in re.finditer(r"[\w$/{}.\"-]*\.log\b", code):
            assert '$LOGDIR/' in m.group(0), (i, ln.strip())


def test_the_python_log_names_are_known():
    """Every log file name a Python writer spells, as a literal, is one this move put in the
    folder (WRITERS) or one it leaves where it is, with the reason (KEPT). A new name fails
    here until someone decides which it is."""
    WRITERS = {"ears-mail.fire.jsonl", "ears-sounds.jsonl", "mouth-sounds.jsonl",
               "ears-view-push.log", "install.log", "player.log", "split.log",
               "{}.log", "ears-{}.log",   # services.spec's job logs, LOGS / f"..."
               ".log"}   # an f-string's tail, which ast.walk also visits on its own
    KEPT = {
        "capture-{}.log": "keeper/call-ears: each call ear's log in $CALL_EARS_DIR (state), "
                          "inside the EARS-372 conflict fence: left, a named gap",
        ".nextcloudsync.log": "a sync tool's marker file, looked for, never written",
        ".owncloudsync.log": "a sync tool's marker file, looked for, never written",
        "sd.log": "keeper/test-*: a hand test's fake sounddevice log, in its temp dir",
        "sounds.jsonl": "keeper/test-*: a hand test's sound log, in its temp dir",
        "h1.jsonl": "keeper/test-*: a hand test's heard files", "h2.jsonl": "the same",
        "{}.jsonl": "ears.eval: an evaluation's output, in the OUT dir it is given",
        "smart_{}.jsonl": "talk: the smart-turn shadow log, off unless a dir is set",
    }
    heard = re.compile(r"(heard|speakers)_|^/?heard_|^\*?\.jsonl$|^\.jsonl$|%s_%s\.jsonl")
    pat = re.compile(r"\.(log|jsonl)$")
    found: dict[str, set] = {}
    for rel, text in _scanned():
        if not (rel.endswith(".py") or text.startswith("#!/usr/bin/env python")):
            continue
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.JoinedStr):
                s = "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and "\n" not in node.value:
                s = node.value
            else:
                continue
            if pat.search(s):
                found.setdefault(s, set()).add(rel)
    unknown = {s: sorted(r) for s, r in found.items()
               if s not in WRITERS and s not in KEPT and not heard.search(s)}
    assert unknown == {}
