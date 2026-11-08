"""The strays: state 0.3.2 kept outside the root, now items of it (EARS-492 do-003, design.md D3).

Each item (``ears.home.ITEMS``) has one rule: its own override, else its OLD path while that is
REAL, else ``<root>/<name>``, where the root is ``state_dir(environ={})``: the two ROOT
overrides are left out in every caller, because a launchd job uses each item and no job of
ours carries one (D3, D8). A live host keeps each item where it is; a fresh host writes none
outside the kit.

Pinned here: the rule over the item table (in process and from both CLIs), the speakers split
(the enrolment moves, 0700; the venv stays), ears-mail's ``--state`` in the plist render, in
``services.spec("mail")`` and in install's mkdir, the speakers job's ``SPEAKER_HOME`` and
``SPEAKER_LOGDIR``, the all-jobs test (no job follows an installer's root override), call-tap
and the keeper naming one folder, the installed-plist check, and a scan for any old path
written outside the item table. Every test runs on a fake HOME under tmp_path; nothing is
installed, launchctl is faked (the install fence), and nothing is restarted.
"""
import json
import os
import plistlib
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from ears import cli, home, services, speakers
from test_services_install_checks import _store, fence  # noqa: F401  (a fixture)

ROOT = Path(__file__).resolve().parents[1]
NEW = home.NEW
SV, VM = home.OLD_ROOTS
KEEPER = ROOT / "keeper" / "ears-keeper.sh"
CALL_TAP = ROOT / "bin" / "call-tap"
#: Every variable the item rule reads, cleared for each test: the root overrides, each item's
#: own, XDG_STATE_HOME and the speakers' venv.
VARS = (*home.ENV, *(it.env for it in home.ITEMS.values() if it.env), "XDG_STATE_HOME",
        "EARS_SPEAKER_PY")
#: The items the README names (design.md D3), every one in the table.
D3 = ("call-tap", "call-ears", "audio-devices", "speakers", "ears-mail-state")


@pytest.fixture
def h(tmp_path, monkeypatch):
    d = tmp_path / "home"
    d.mkdir()
    monkeypatch.setenv("HOME", str(d))
    for k in VARS:
        monkeypatch.delenv(k, raising=False)
    return d


def _old(h: Path, name: str, xdg: Path = None) -> Path:
    it = home.ITEMS[name]
    return ((xdg or h / ".local/state") if it.xdg else h) / it.old


def _make_real(p: Path, name: str) -> None:
    """Put a REAL old item at P: a folder (the speakers one with a reference in it) or a file."""
    kind = home.ITEMS[name].kind
    if kind == "file":
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}")
    else:
        p.mkdir(parents=True)
        if kind == "speakers":
            (p / "refs").mkdir()


def _populate(root: Path) -> None:
    conv = root / "logs" / "conversations"
    conv.mkdir(parents=True)
    (conv / "heard.lock").write_text("")


def _env(h: Path, **extra) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in VARS}
    env.update(HOME=str(h), **extra)
    return env


def test_the_table_is_the_readmes_items_and_nothing_else():
    assert set(home.ITEMS) == set(D3)
    # each item's name under the root (design.md D2), never <root>/audio/ (turn audio)
    assert {n: it.name for n, it in home.ITEMS.items()} == {
        "call-tap": "call-tap", "call-ears": "call-ears", "audio-devices": "audio-devices.json",
        "speakers": "speakers", "ears-mail-state": "ears-mail.state.json"}
    assert {n: it.env for n, it in home.ITEMS.items()} == {
        "call-tap": "EARS_CALL_TAP_RUN", "call-ears": "CALL_EARS_DIR",
        "audio-devices": "AUDIO_DEVICES_FILE", "speakers": "EARS_SPEAKERS_HOME",
        "ears-mail-state": None}


# --- the rule, one parametrized test over the item table ---------------------------------------

@pytest.mark.parametrize("name", D3)
def test_each_item_by_the_rule(name, h, tmp_path, monkeypatch):
    it = home.ITEMS[name]
    new = h / NEW / it.name
    # a fresh HOME: the item's place in the kit, not made, and nothing made anywhere
    assert home.item_source(name) == (new, "fresh-new")
    assert list(h.iterdir()) == []
    # its old path, while REAL, is adopted in place
    old = _old(h, name)
    _make_real(old, name)
    assert home.item_source(name) == (old, "old")
    # its own override wins over the old path
    if it.env:
        monkeypatch.setenv(it.env, str(tmp_path / "own"))
        assert home.item_source(name) == (tmp_path / "own", "override")
        monkeypatch.delenv(it.env)


@pytest.mark.parametrize("name", D3)
def test_a_symlink_into_the_new_place_is_not_real(name, h):
    """As a migrate leaves it: the old path a link to the item's new place answers the new."""
    it = home.ITEMS[name]
    new = h / NEW / it.name
    _make_real(new, name)
    old = _old(h, name)
    old.parent.mkdir(parents=True, exist_ok=True)
    old.symlink_to(new, target_is_directory=it.kind != "file")
    assert home.item_source(name) == (new, "new")


@pytest.mark.parametrize("name", D3)
def test_an_old_path_that_is_the_wrong_kind_is_not_real(name, h):
    old = _old(h, name)
    if home.ITEMS[name].kind == "file":
        old.mkdir(parents=True)                  # a folder where a file was
    else:
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text("")                       # a file where a folder was
    assert home.state_path(name) == h / NEW / home.ITEMS[name].name


@pytest.mark.parametrize("name", [n for n in D3 if home.ITEMS[n].xdg])
def test_xdg_state_home_only_locates_the_old_path(name, h, tmp_path, monkeypatch):
    """A fresh host writes under the kit whatever XDG_STATE_HOME says; a host with a real
    folder there keeps it (design.md D3)."""
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg))
    assert home.state_path(name) == h / NEW / home.ITEMS[name].name
    _make_real(_old(h, name, xdg), name)
    assert home.state_path(name) == _old(h, name, xdg)
    # the default XDG folder is not looked at when XDG_STATE_HOME names another
    assert not (h / ".local").exists()


@pytest.mark.parametrize("old_root", [SV, VM])
def test_an_adopted_old_root_holds_every_item_and_nothing_is_written_in_the_kit(old_root, h):
    _populate(h / old_root)
    for name, it in home.ITEMS.items():
        assert home.item_source(name) == (h / old_root / it.name, "old"), name
    assert not (h / ".sessionkit").exists()


@pytest.mark.parametrize("name", D3)
def test_the_root_overrides_are_left_out_of_every_item(name, h, tmp_path, monkeypatch):
    """re-reviewer 3: with SESSIONVOICE_HOME and VOICEMODE_BASE_DIR set, every item answers
    <state_dir(environ={})>/<name>, in process and from `python -m ears.home state-dir ITEM`,
    and its own override still wins over them."""
    it = home.ITEMS[name]
    monkeypatch.setenv("SESSIONVOICE_HOME", str(tmp_path / "sv"))
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm"))
    want = home.state_dir(environ={}) / it.name
    assert want == h / NEW / it.name
    assert home.state_path(name) == want
    for argv in ([sys.executable, "-m", "ears.home"], [sys.executable, str(ROOT / "src/ears/home.py")]):
        p = subprocess.run([*argv, "state-dir", name], env=dict(os.environ), capture_output=True,
                           text=True, timeout=30)
        assert p.returncode == 0 and p.stdout == f"{want}\n", p.stderr
    if it.env:
        monkeypatch.setenv(it.env, str(tmp_path / "own"))
        assert home.state_path(name) == tmp_path / "own"
        p = subprocess.run([sys.executable, "-m", "ears.home", "state-dir", name, "--json"],
                           env=dict(os.environ), capture_output=True, text=True, timeout=30)
        assert json.loads(p.stdout) == {"path": str(tmp_path / "own"), "source": "override"}
    assert not (tmp_path / "sv").exists() and not (tmp_path / "vm").exists()


def test_json_lists_every_item_from_both_clis(h, capsys):
    """The do-001 contract, items filled: `ears home state-dir --json` and the stdlib module
    print the same map, and each item answers alone as it does there."""
    _make_real(_old(h, "call-ears"), "call-ears")
    p = subprocess.run([sys.executable, "-m", "ears.home", "state-dir", "--json"], env=_env(h),
                       capture_output=True, text=True, timeout=30)
    mod = json.loads(p.stdout)
    assert cli.main(["home", "state-dir", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == mod
    want = {n: {"path": str(path), "source": src} for n, (path, src) in home.state_items().items()}
    assert mod["items"] == want and set(want) == set(D3)
    assert mod["items"]["call-ears"] == {"path": str(_old(h, "call-ears")), "source": "old"}
    for n in D3:
        assert cli.main(["home", "state-dir", n, "--json"]) == 0
        assert json.loads(capsys.readouterr().out) == want[n]


def test_state_path_names_an_unknown_item():
    with pytest.raises(KeyError, match="no state item 'seats'"):
        home.state_path("seats")


# --- the speakers split ------------------------------------------------------------------------

def test_a_venv_only_old_speakers_home_is_not_adopted(h):
    """A fresh host's venv (or a host installed but never enrolled) never adopts the old
    folder; an entry other than venv/ that is not a link does (design.md D3)."""
    old = _old(h, "speakers")
    (old / "venv/bin").mkdir(parents=True)
    assert services.speakers_home() == h / NEW / "speakers"
    (old / "refs").symlink_to(h / NEW / "speakers" / "refs")      # a migrate's link: still not
    assert services.speakers_home() == h / NEW / "speakers"
    (old / "STOP").write_text("")
    assert services.speakers_home() == old


def test_the_venv_stays_where_it_was(h, tmp_path, monkeypatch):
    venv_py = h / home.SPEAKERS_VENV / "bin/python"
    assert services.speaker_py() == venv_py == h / ".local/share/ears/speakers/venv/bin/python"
    _make_real(_old(h, "speakers"), "speakers")                  # adopted or not, the same venv
    assert services.speaker_py() == venv_py
    monkeypatch.setenv("SESSIONVOICE_HOME", str(tmp_path / "sv"))
    assert services.speaker_py() == venv_py
    # one custom home holds both, as before
    monkeypatch.setenv("EARS_SPEAKERS_HOME", str(tmp_path / "own"))
    assert services.speaker_py() == tmp_path / "own/venv/bin/python"
    assert services.speakers_home() == tmp_path / "own"
    monkeypatch.setenv("EARS_SPEAKER_PY", str(tmp_path / "py"))
    assert services.speaker_py() == tmp_path / "py"


def _speakers_fence(fence, monkeypatch):
    monkeypatch.setattr(services, "_mac", lambda: ("arm64", "15.0"))
    monkeypatch.setattr(services, "_uv", lambda: "/fake/uv")
    monkeypatch.setattr(services, "_speakers_sh", lambda argv: subprocess.CompletedProcess(argv, 0, "", ""))
    monkeypatch.setattr(services, "speakers_missing_module", lambda py: None)
    monkeypatch.setattr(services, "speakers_model_cached", lambda py: True)
    for k in VARS:
        monkeypatch.delenv(k, raising=False)


def test_install_makes_the_speakers_home_private_in_the_kit_on_a_fresh_home(fence, monkeypatch):
    _speakers_fence(fence, monkeypatch)
    h = fence["home"]
    assert services.install(["speakers"]) == 0
    sp = h / NEW / "speakers"
    assert sp.is_dir() and (sp.stat().st_mode & 0o777) == 0o700
    env = plistlib.loads((services.AGENTS / "dev.sessionkit.voice.ears-speakers.plist").read_bytes())
    assert env["EnvironmentVariables"]["SPEAKER_HOME"] == env["WorkingDirectory"] == str(sp)
    assert env["EnvironmentVariables"]["SPEAKER_PY"] == str(h / home.SPEAKERS_VENV / "bin/python")
    # nothing of the enrolment outside the kit: only the venv's own folder may be there
    old = _old(h, "speakers")
    assert not old.exists() or {p.name for p in old.iterdir()} <= {"venv"}


# --- ears-mail's --state: the render, services.spec("mail") and install's mkdir ---------------

def _render(h: Path, **env) -> dict:
    p = subprocess.run([sys.executable, str(ROOT / "bin/render-plist"),
                        str(ROOT / "launchd/dev.sessionkit.voice.ears-mail.plist.in")],
                       env=_env(h, **env), capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return plistlib.loads(p.stdout.encode())


def _state_flag(d: dict) -> str:
    a = d["ProgramArguments"]
    return a[a.index("--state") + 1]


def test_ears_mail_state_in_the_render_the_spec_and_install(fence, tmp_path, monkeypatch):
    h = fence["home"]
    want = str(h / NEW / "ears-mail.state.json")
    assert _state_flag(_render(h)) == want
    assert _state_flag(services.spec("mail")) == want
    # an installer's root overrides reach neither: the job carries none
    for var in home.ENV:
        assert _state_flag(_render(h, **{var: str(tmp_path / "x")})) == want
        monkeypatch.setenv(var, str(tmp_path / "x"))
        assert _state_flag(services.spec("mail")) == want
        monkeypatch.delenv(var)
    # install's mkdir makes the file's folder, the kit's root on a fresh HOME
    _store(h)
    assert services.install(["mail"]) == 0
    assert Path(want).parent.is_dir() and not Path(want).exists()
    assert not (tmp_path / "x").exists()


def test_a_live_hosts_ears_mail_state_stays_where_it_is(fence):
    """The old file while it is real (the render reads what HOME holds, and writes nothing)."""
    h = fence["home"]
    old = h / ".cache/ears/ears-mail.state.json"
    old.write_text("{}")
    assert _state_flag(_render(h)) == _state_flag(services.spec("mail")) == str(old)
    assert not (h / NEW).exists()


def test_the_template_names_no_state_path_of_its_own():
    tpl = (ROOT / "launchd/dev.sessionkit.voice.ears-mail.plist.in").read_text()
    assert "<string>--state</string><string>@EARS_MAIL_STATE@</string>" in tpl
    body = re.sub(r"<!--.*?-->", "", tpl, flags=re.S)
    assert ".cache/ears" not in body


# --- the speakers job, and the all-jobs test -----------------------------------------------------

@pytest.mark.parametrize("var", home.ENV)
def test_the_speakers_job_follows_no_installers_root(var, fence, tmp_path, monkeypatch):
    h = fence["home"]
    for k in VARS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv(var, str(tmp_path / "x"))
    env = services.spec("speakers")["EnvironmentVariables"]
    root = home.state_dir(environ={})
    assert env["SPEAKER_LOGDIR"] == str(root / "logs/conversations") == str(h / NEW / "logs/conversations")
    assert env["SPEAKER_HOME"] == str(root / "speakers")
    # an installer's own EARS_SPEAKERS_HOME still names it
    monkeypatch.setenv("EARS_SPEAKERS_HOME", str(tmp_path / "own"))
    d = services.spec("speakers")
    assert d["EnvironmentVariables"]["SPEAKER_HOME"] == d["WorkingDirectory"] == str(tmp_path / "own")


def _values(d: dict) -> list[str]:
    """Every path-like value a job's plist carries: its environment, arguments, working folder
    and log paths."""
    vals = list(d["EnvironmentVariables"].values()) + [str(a) for a in d["ProgramArguments"]]
    vals += [d["WorkingDirectory"], d["StandardOutPath"], d["StandardErrorPath"]]
    return [v for a in vals for v in [a, *shlex.split(a)] if isinstance(v, str)]


def test_no_job_follows_an_installers_root_override(fence, tmp_path, monkeypatch):
    """The all-jobs test (design.md D8, re-implementer 2 and re-reviewer 3): with both root
    overrides set in the installer's shell, no job `spec()` builds carries either, and no value
    in its environment, arguments, working folder or log paths lies under their roots."""
    roots = {"SESSIONVOICE_HOME": tmp_path / "sv", "VOICEMODE_BASE_DIR": tmp_path / "vm"}
    for k, v in roots.items():
        monkeypatch.setenv(k, str(v))
    built = []
    for job in services.jobs(services.ALL):
        d = services.spec(job)
        built.append(job)
        assert not set(home.ENV) & set(d["EnvironmentVariables"]), job
        bad = [v for v in _values(d) for r in roots.values() if str(r) in v]
        assert bad == [], (job, bad)
    assert len(built) == 9 and {"speakers", "mail", "keeper"} <= set(built)


# --- call-tap and the keeper: one folder -----------------------------------------------------

def test_call_tap_with_a_root_override_and_the_keeper_without_name_one_folder(h, tmp_path):
    """re-reviewer 3: a shell that has SESSIONVOICE_HOME runs call-tap; the keeper's job has none.
    Both ask the item, so a listen:false pauses the tap's ear (LISTEN-OFF in one folder)."""
    env = _env(h, EARS_PY=sys.executable, SESSIONVOICE_HOME=str(tmp_path / "sv"),
               VOICEMODE_BASE_DIR=str(tmp_path / "vm"))
    p = subprocess.run(["bash", str(CALL_TAP), "status"], env=env, capture_output=True, text=True,
                       timeout=60)
    assert p.returncode == 0, p.stderr
    made = [d for d in (h / NEW / "call-tap", tmp_path / "sv/call-tap", tmp_path / "vm/call-tap")
            if d.exists()]
    assert made == [h / NEW / "call-tap"]
    line = next(l for l in KEEPER.read_text().splitlines() if l.startswith("TAP_RUN="))
    out = subprocess.run(["bash", "-c", f"PY={shlex.quote(sys.executable)}\n{line}\necho \"$TAP_RUN\""],
                         env=_env(h), capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and out.stdout.strip() == str(made[0]), out.stderr


def test_call_tap_with_no_package_answer_exits_2_and_makes_nothing(h, tmp_path):
    p = subprocess.run(["bash", str(CALL_TAP), "status"], env=_env(h, EARS_PY="/bin/false"),
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 2 and "state-dir call-tap named no run folder" in p.stderr
    assert list(h.iterdir()) == []
    p = subprocess.run(["bash", str(CALL_TAP), "status"],
                       env=_env(h, EARS_PY="/bin/false", EARS_CALL_TAP_RUN=str(tmp_path / "r")),
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0 and (tmp_path / "r").is_dir()


@pytest.mark.parametrize("prefix,name", [("AUDIO_DEVICES=", "audio-devices"),
                                         ("CALL_EARS_DIR=", "call-ears"), ("TAP_RUN=", "call-tap")])
def test_each_keeper_line_asks_its_item(prefix, name, h, tmp_path):
    line = next(l for l in KEEPER.read_text().splitlines() if l.startswith(prefix))
    var = prefix[:-1]
    run = lambda env: subprocess.run(
        ["bash", "-c", f"set -u\nPY={shlex.quote(sys.executable)}\n{line}\necho \"${var}\""],
        env=env, capture_output=True, text=True, timeout=60)
    out = run(_env(h, SESSIONVOICE_HOME=str(tmp_path / "sv")))
    assert out.returncode == 0 and out.stdout.strip() == str(h / NEW / home.ITEMS[name].name)
    own = home.ITEMS[name].env
    out = run(_env(h, **{own: str(tmp_path / "own")}))
    assert out.stdout.strip() == str(tmp_path / "own")
    _make_real(_old(h, name), name)
    assert run(_env(h)).stdout.strip() == str(_old(h, name))


def test_call_ears_by_hand_asks_its_item(h, tmp_path):
    """keeper/call-ears, run by hand (the keeper always passes CALL_EARS_DIR), takes the item
    through the loader; a plugin whose package disagrees refuses."""
    code = ("import importlib.machinery, importlib.util, sys\n"
            "l = importlib.machinery.SourceFileLoader('ce', sys.argv[1])\n"
            "m = importlib.util.module_from_spec(importlib.util.spec_from_loader('ce', l))\n"
            "l.exec_module(m); print(m.DIR)")
    p = subprocess.run([sys.executable, "-c", code, str(ROOT / "keeper/call-ears")], env=_env(h),
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0 and p.stdout.strip() == str(h / NEW / "call-ears"), p.stderr
    assert not (h / NEW).exists()


def test_asked_state_path_refuses_when_the_package_disagrees(h, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(home, "ask_package", lambda env=None, home_=None: (None, "they disagree"))
    monkeypatch.setattr(home, "_asked", {})
    with pytest.raises(SystemExit) as e:
        home.asked_state_path("call-ears", "call-ears")
    assert e.value.code == 2 and "call-ears: they disagree" in capsys.readouterr().err
    # its own variable names it for both sides: nothing is asked
    monkeypatch.setenv("CALL_EARS_DIR", str(tmp_path / "own"))
    assert home.asked_state_path("call-ears", "call-ears") == tmp_path / "own"


def test_asked_state_path_asks_with_the_root_overrides_left_out(h, tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(home, "ask_package",
                        lambda environ=None, home=None: seen.append(dict(environ)) or (Path("/r"), "ok"))
    monkeypatch.setenv("SESSIONVOICE_HOME", str(tmp_path / "sv"))
    assert home.asked_state_path("audio-devices", "x") == h / NEW / "audio-devices.json"
    assert seen and not set(home.ENV) & set(seen[0])


# --- the installed-plist check (D3 re-implementer; D7.7 item 3) ---------------------------------

def _installed(job: str, label: str, **env_or_state) -> Path:
    services.AGENTS.mkdir(parents=True, exist_ok=True)
    d = services.spec(job)
    if job == "speakers":
        d["EnvironmentVariables"]["SPEAKER_HOME"] = env_or_state["path"]
        d["WorkingDirectory"] = env_or_state["path"]
    else:
        a = d["ProgramArguments"]
        a[a.index("--state") + 1] = env_or_state["path"]
    p = services.AGENTS / f"{label}.plist"
    p.write_bytes(plistlib.dumps(d))
    return p


def test_status_and_enrol_name_install_when_the_installed_speakers_job_reads_the_old_home(
        fence, monkeypatch, capsys):
    """A venv-only old speakers home is not adopted, so enrolment writes in the kit while the
    installed job still reads the old folder. Status and enrol say so, naming install; nothing
    moves and launchd is not asked."""
    _speakers_fence(fence, monkeypatch)
    h = fence["home"]
    old = _old(h, "speakers")
    (old / "venv/bin").mkdir(parents=True)
    plist = _installed("speakers", "dev.sessionkit.voice.ears-speakers", path=str(old))
    lines = services.installed_state_lines()
    assert len(lines) == 1 and str(old) in lines[0] and str(h / NEW / "speakers") in lines[0]
    assert "`ears services install --only speakers`" in lines[0] and str(plist) in lines[0]
    services.status()
    assert lines[0] in capsys.readouterr().out
    monkeypatch.setattr(speakers, "_told", set())
    monkeypatch.setattr(speakers, "_py", lambda: Path(sys.executable))
    speakers.preflight()
    speakers.preflight()                          # once per process, though enrol asks at each step
    assert capsys.readouterr().out.count("ears services install --only speakers") == 1
    assert not (h / NEW / "speakers").exists() and {p.name for p in old.iterdir()} == {"venv"}
    assert [a for a in fence["calls"]["launchctl"] if a[0] not in ("list", "print")] == []


def test_no_line_while_the_installed_jobs_name_what_voice_uses(fence, monkeypatch):
    _speakers_fence(fence, monkeypatch)
    _installed("speakers", "dev.sessionkit.voice.ears-speakers", path=str(services.speakers_home()))
    _installed("mail", "dev.sessionkit.voice.ears-mail", path=str(services.mail_state()))
    assert services.installed_state_lines() == []
    # a hand-made plist (no MARKER) is the user's: never named
    hand = services.AGENTS / "dev.sessionkit.voice.ears-speakers.plist"
    d = plistlib.loads(hand.read_bytes())
    d["EnvironmentVariables"].pop(services.MARKER[0])
    d["EnvironmentVariables"]["SPEAKER_HOME"] = "/elsewhere"
    hand.write_bytes(plistlib.dumps(d))
    assert services.installed_state_lines() == []


def test_a_migrated_ears_mail_state_names_the_installed_job(fence):
    """After a migrate the old state file is a link to the new one, so the item answers the new
    place; the installed job's os.replace would make the old path real again (D7.7 item 3)."""
    h = fence["home"]
    new = h / NEW / "ears-mail.state.json"
    new.parent.mkdir(parents=True)
    new.write_text("{}")
    old = h / ".cache/ears/ears-mail.state.json"
    old.symlink_to(new)
    _installed("mail", "dev.sessionkit.voice.ears-mail", path=str(old))
    lines = services.installed_state_lines()
    assert len(lines) == 1 and lines[0].startswith("mail ") and str(new) in lines[0]
    assert "`ears services install --only mail`" in lines[0]


# --- the scan: no stray's old path outside home.py's item table ---------------------------------

SCANNED = ("src", "bin", "keeper", "shadow", "skills/ears/scripts", "speakers", "view", "bench",
           "launchd")
#: The old paths of the strays, in any spelling a script would write them. The speakers
#: VENV (~/.local/share/ears/speakers/venv) stays where it is, and is not matched.
OLD_PATHS = re.compile(r"local/state|XDG_STATE_HOME|share/ears/speakers(?!/venv)"
                       r"|\.cache/ears\W+ears-mail|ears/call-(tap|ears)\b|audio/devices\.json")


def _code():
    for d in SCANNED:
        for p in sorted((ROOT / d).rglob("*")):
            if p.is_file() and not p.is_symlink() and "__pycache__" not in p.parts \
                    and ".egg-info" not in str(p) and p.suffix not in (".md", ".onnx", ".wav", ".txt"):
                yield p


def test_no_strays_old_path_outside_the_item_table():
    """Each old path is written ONCE, in home.py's ITEMS (moved here from do-002 by the
    foreman, F1). docs/ and the READMEs are prose (do-005's); code, plists and schemas are
    scanned."""
    bad = []
    for p in _code():
        rel = p.relative_to(ROOT).as_posix()
        try:
            text = p.read_text()
        except UnicodeDecodeError:
            continue
        if rel == "src/ears/home.py":   # the item table and the one function that reads it
            start = text.index("# --- the items:")
            text = text[:start] + text[text.index("def _real_old(", start):]
        bad += [f"{rel}:{i}: {ln.strip()}" for i, ln in enumerate(text.splitlines(), 1)
                if OLD_PATHS.search(ln)]
    assert bad == []


def test_the_scan_sees_each_old_path():
    for line in ('RUN="${XDG_STATE_HOME:-$HOME/.local/state}/ears/call-tap"',
                 'Path.home() / ".local/share/ears/speakers"',
                 'str(Path.home() / ".cache/ears/ears-mail.state.json")',
                 '"~/.cache/ears", "ears-mail.state.json"', "audio/devices.json"):
        assert OLD_PATHS.search(line), line
    assert not OLD_PATHS.search("~/.local/share/ears/speakers/venv/bin/python")
    assert not OLD_PATHS.search('"~/.cache/ears/silero_vad.onnx"')
