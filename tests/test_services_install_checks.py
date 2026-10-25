"""EARS-222: `ears services install` installs the mailer that PAGES an agent, and checks what
the services need before it writes a plist (a new host's install
night: the keeper alone wrote the heard line while nothing paged the agent).

Every test runs under a temp HOME, with launchctl, postfix, `message`, vmailbox-gen and the STT
servers stubbed: nothing here loads a launch agent or asks this host's live servers anything."""
import http.server
import json
import os
import plistlib
import pwd
import re
import shlex
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from ears import operator_config, services, wake
from ears.home import SETTINGS
from test_services_label_move import OURS_ENV, ours_plist, print_out
from tests import setup_fence

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def fence(monkeypatch, tmp_path):
    home = tmp_path / "home"
    (home / ".cache/ears").mkdir(parents=True)
    (home / ".cache/ears/silero_vad.onnx").write_bytes(b"onnx")      # present unless a test removes it
    message = tmp_path / "message"; message.write_text("#!/bin/sh\n"); message.chmod(0o755)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("MAIL_ROOT", str(home / ".mail"))
    monkeypatch.setenv("EARS_WAKE", str(home / SETTINGS / "wake.json"))
    monkeypatch.setenv("EARS_CONF", str(home / SETTINGS / "ears.json"))
    # sessionvoice's home (seed_stt's fallback record) by HOME, never a root this shell names
    for k in ("SESSIONVOICE_HOME", "VOICEMODE_BASE_DIR"):
        monkeypatch.delenv(k, raising=False)
    for k in ("EARS_SILERO_ONNX", "EARS_NOTIFY", "EARS_STT_URL", "EARS_STT_MODEL", "EARS_AGENT", "EARS_STT_PORT",
              "SESSIONMAIL_SENDMAIL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(services, "AGENTS", home / "Library/LaunchAgents")
    monkeypatch.setattr(services, "LOGS", home / "Library/Logs/ears")
    monkeypatch.setattr(services, "MESSAGE", str(message))
    calls = {"launchctl": [], "vmailbox": 0, "probe": []}
    loaded = set()
    env: dict[str, dict] = {}     # what `launchctl print` lists as a loaded job's environment

    def launchctl(*a):
        calls["launchctl"].append(a)
        if a[0] == "print":
            label = a[1].split("/")[-1]
            if label not in loaded:
                return subprocess.CompletedProcess(a, 113, "", "")
            return subprocess.CompletedProcess(a, 0, print_out(label, env.get(label, {})), "")
        if a[0] == "list":
            return subprocess.CompletedProcess(a, 0, "", "")
        return subprocess.CompletedProcess(a, 0, "", "")

    def vmailbox():
        calls["vmailbox"] += 1
        return 0, ""

    def probe(url, model=None, timeout=30.0):
        calls["probe"].append((url, model))
        return None

    monkeypatch.setattr(services, "_launchctl", launchctl)
    monkeypatch.setattr(services, "_postconf", lambda param: "hash:/etc/postfix/agents.d/vmailbox")
    monkeypatch.setattr(services, "_vmailbox_gen", vmailbox)
    monkeypatch.setattr(services, "probe_stt", probe)
    monkeypatch.setattr(services, "install_schema", lambda: "schema")
    monkeypatch.setattr("time.sleep", lambda s: None)
    # the speech server probes (EARS-291): `status` asks who holds :8890. Here nobody does,
    # and nothing asks this host's live :8890 (no lsof, no connect, no GET)
    from ears import speech
    calls["speech"] = []

    def no_get(host=None, port=None, timeout=None):
        raise AssertionError(f"a fenced test sent a GET to {host}:{port}")
    monkeypatch.setattr(speech, "port_holder", lambda port=None, listed=None: calls["speech"].append(("lsof", port)))
    monkeypatch.setattr(speech, "connects", lambda host=None, port=None, timeout=None: calls["speech"].append(("connect", port)) or False)
    monkeypatch.setattr(speech, "identify", no_get)
    monkeypatch.setattr(speech, "SYSTEM_AGENTS", tmp_path / "Library/LaunchAgents")   # never the host's
    monkeypatch.setattr(speech, "SYSTEM_DAEMONS", tmp_path / "Library/LaunchDaemons")
    # a bare install now takes the speech server's step 4 (nothing on the port, no job): its
    # python is a given one, so no venv is built, nothing is fetched and the host is not asked
    for k in ("EARS_SPEECH_HOME", "EARS_SPEECH_PY", "EARS_SPEECH_PORT"):
        monkeypatch.delenv(k, raising=False)
    speech_py = tmp_path / "speech-py"; speech_py.write_text("")
    monkeypatch.setenv("EARS_SPEECH_PY", str(speech_py))
    monkeypatch.setattr(services, "_speech_platform", lambda: None)
    monkeypatch.setattr(services, "speech_missing_module", lambda py: calls["speech"].append(("import", str(py))))
    monkeypatch.setattr(services, "_speech_sh", lambda argv: pytest.fail(f"a fenced test ran {argv}"))
    # ...nor fetches its weights (EARS-419): the prefetch is off (env_fence) and its seam
    # refuses, and the reader it prints reads the server's cache, under the temp HOME, never the host's
    monkeypatch.setattr(services, "_prefetch_sh", lambda argv, out, cache: pytest.fail(f"a fenced test fetched {argv}"))
    # ...nor the Silero model (EARS-449): install fetches a missing one through this opener, and
    # here it refuses, so a test that removes the model and gives no fake opener fails offline
    def no_network(url, timeout=None):
        raise OSError("network refused by the fence")
    monkeypatch.setattr(services, "SILERO_OPENER", no_network)
    # (HF_HOME, the weakest of huggingface_hub's names: a test that sets another still wins)
    for k in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf-home"))
    return {"home": home, "calls": calls, "loaded": loaded, "env": env, "tmp": tmp_path}


def _store(home, name="store"):
    d = home / ".mail/agents" / name
    d.mkdir(parents=True)
    (d / ".tm-store").write_text("marker")
    return d


def _plist(fence, n):
    return services.AGENTS / f"dev.sessionkit.voice.ears-{n}.plist"


# --- item 1: the mail service --------------------------------------------------------------

def test_mail_is_a_service_and_a_bare_install_loads_it():
    assert "mail" in services.ALL and "mail" in services.DEFAULT and "stt" not in services.DEFAULT


def test_the_mail_agent_runs_the_device_mailer_as_the_hand_template_does(fence):
    d = services.spec("mail")
    assert d["Label"] == "dev.sessionkit.voice.ears-mail" and d["KeepAlive"] is True
    a = d["ProgramArguments"]
    assert a[1] == str(REPO / "keeper/ears-mail.py")              # the checkout's, run from one
    flags = dict(zip(a[2::2], a[3::2]))
    assert flags["--to"] == "device" and flags["--sender"] == "heard"
    assert flags["--rules"] == str(wake.target())                  # the file `ears wake add` writes
    assert flags["--state"] == str(fence["home"] / ".cache/ears/ears-mail.state.json")
    assert flags["--fire-log"] == str(services.LOGS / "ears-mail.fire.jsonl")
    # an again verdict runs `mouth again` on the agents' python (EARS-22 S5), the python
    # that runs `-m mouth mail`, given whole so a path with a space survives the split
    assert shlex.split(flags["--mouth"]) == [a[0], "-m", "mouth"]
    assert services.spec("mouth")["ProgramArguments"][:3] == [a[0], "-m", "mouth"]
    # the hand template's arguments, the same flags in the same order
    tpl = (REPO / "launchd/dev.sessionkit.voice.ears-mail.plist.in").read_text()
    for f in ("--to", "--sender", "--rules", "--state", "--fire-log", "--mouth"):
        assert f"<string>{f}</string>" in tpl and f in a


def test_a_spaced_python_survives_the_mouth_split(fence, monkeypatch):
    monkeypatch.setattr(services, "_py", lambda: "/Users/a b/venv/bin/python")
    flags = dict(zip(*[iter(services.spec("mail")["ProgramArguments"][2:])] * 2))
    assert shlex.split(flags["--mouth"]) == ["/Users/a b/venv/bin/python", "-m", "mouth"]


def test_the_hand_templates_mouth_is_the_checkouts_venv_not_the_system_python():
    """Its header renders PYTHON=/usr/bin/python3, which has no mouth (EARS-22 S5)."""
    env = dict(os.environ, PYTHON="/usr/bin/python3", HOME="/Users/someone", EARS_REPO="/srv/ears")
    out = subprocess.run([sys.executable, str(REPO / "bin/render-plist"),
                  str(REPO / "launchd/dev.sessionkit.voice.ears-mail.plist.in")],
                 capture_output=True, text=True, env=env, timeout=30)
    assert out.returncode == 0, out.stderr
    a = plistlib.loads(out.stdout.encode())["ProgramArguments"]
    assert a[0] == "/usr/bin/python3"
    assert shlex.split(a[a.index("--mouth") + 1]) == ["/srv/ears/.venv/bin/python", "-m", "mouth"]


def test_from_a_wheel_the_mailer_is_the_shipped_one(monkeypatch, tmp_path):
    monkeypatch.setattr(services, "ROOT", tmp_path)                # installed: no keeper/ beside src
    got = services.mail_script()
    assert got.is_file() and got.name == "ears-mail.py" and got.parent.name == "_keeper"
    assert got.read_bytes() == (REPO / "keeper/ears-mail.py").read_bytes()


def test_the_mailer_needs_nothing_beside_it():
    # stdlib only: a shipped ears-mail.py imports no sibling file (so only it ships)
    src = (REPO / "keeper/ears-mail.py").read_text()
    siblings = {p.stem.replace("-", "_") for p in (REPO / "keeper").glob("*.py")}
    imported = {w for line in src.splitlines() if line.lstrip().startswith(("import ", "from "))
                for w in line.replace(",", " ").split()[1:]}
    assert not (siblings & imported), siblings & imported


def test_no_mailer_anywhere_refuses_and_writes_nothing(fence, monkeypatch, tmp_path, capsys):
    import importlib.resources
    monkeypatch.setattr(services, "ROOT", tmp_path / "root")
    monkeypatch.setattr(importlib.resources, "files", lambda pkg: tmp_path / "pkg")
    for dry in (True, False):
        assert services.install(["mail"], dry_run=dry) == 2
        assert "REFUSED" in capsys.readouterr().err
    assert not services.AGENTS.exists() and not fence["calls"]["launchctl"]


def test_install_writes_and_loads_all_three(fence, capsys):
    _store(fence["home"])
    assert services.install(list(services.DEFAULT), agent="dave") == 0
    out = capsys.readouterr().out
    for n in ("mouth", "keeper", "mail"):
        assert _plist(fence, n).is_file()
        assert ("bootstrap", f"gui/{os.getuid()}", str(_plist(fence, n))) in fence["calls"]["launchctl"]
    assert plistlib.loads(_plist(fence, "mail").read_bytes())["Label"] == "dev.sessionkit.voice.ears-mail"
    assert (fence["home"] / ".cache/ears").is_dir()               # the mailer's --state dir
    assert "WAKES and answers" in out


# the template's label. Its old one, dev.voicemode.ears-mail (EARS-294), is a LEGACY render
# now: install replaces it (EARS-388, tests/test_services_legacy_plists.py)
HANDS = ["dev.sessionvoice.ears-mail"]


@pytest.mark.parametrize("label", HANDS)
@pytest.mark.parametrize("how", ["plist", "loaded"])
def test_never_two_mailers(fence, capsys, how, label):
    hand = services.AGENTS / f"{label}.plist"
    if how == "plist":
        hand.parent.mkdir(parents=True); hand.write_text("HAND")
    else:
        fence["loaded"].add(label)
    assert services.install(["mail"]) == 0
    out = capsys.readouterr().out
    assert "mail SKIPPED" in out and label in out
    assert not _plist(fence, "mail").exists()
    if how == "plist":
        assert hand.read_text() == "HAND"                           # theirs untouched
    touched = [a for a in fence["calls"]["launchctl"] if a[0] in ("bootout", "bootstrap", "kickstart")]
    assert not any("ears-mail" in " ".join(a) for a in touched)


@pytest.mark.parametrize("label", HANDS)
def test_dry_run_says_the_mailer_would_be_skipped(fence, capsys, label):
    fence["loaded"].add(label)
    assert services.install(["mail"], dry_run=True) == 0
    out = capsys.readouterr().out
    assert "mail SKIPPED" in out and f"--- {_plist(fence, 'mail')}" not in out    # ours not printed


def test_uninstall_and_status_know_mail(fence, capsys):
    services.uninstall(list(services.ALL))
    assert ("bootout", f"gui/{os.getuid()}/dev.sessionkit.voice.ears-mail") in fence["calls"]["launchctl"]
    assert ("bootout", f"gui/{os.getuid()}/com.voicemode.ears-mail") in fence["calls"]["launchctl"]  # ours, old label
    assert not any(a[1].split("/")[-1] in services.HAND_MAILS                   # never theirs
                   for a in fence["calls"]["launchctl"])
    services.status()
    assert any(l.startswith("mail") for l in capsys.readouterr().out.splitlines())


@pytest.mark.parametrize("label", HANDS)
def test_status_names_a_hand_made_mailer(fence, monkeypatch, capsys, label):
    monkeypatch.setattr(services, "_launchctl", lambda *a: subprocess.CompletedProcess(
        a, 0, f"PID\tStatus\tLabel\n123\t0\t{label}\n", ""))
    services.status()
    assert f"{label} (hand-made): running, pid 123" in capsys.readouterr().out


# --- items 2 and 10: postfix and the heard box --------------------------------------------

def test_message_present_is_said(fence):
    out = services.mail_checks(dry_run=True)
    assert any(l.startswith("message present: ") and l.endswith("/message") for l in out)


def test_message_missing_is_said_naming_sessionmail(fence, monkeypatch, tmp_path):
    monkeypatch.setattr(services, "MESSAGE", str(tmp_path / "no-message"))
    miss = [l for l in services.mail_checks(dry_run=True) if "message MISSING" in l]
    assert len(miss) == 1 and miss[0].startswith("WARNING:") and "sessionmail" in miss[0]


def test_message_is_looked_for_on_the_services_path(fence, monkeypatch, tmp_path):
    """The check finds `message` where a service will: ~/.local/bin is on its PATH (spec)."""
    local = Path.home() / ".local/bin"
    local.mkdir(parents=True)
    (local / "message").write_text("#!/bin/sh\n")
    (local / "message").chmod(0o755)
    monkeypatch.setattr(services, "MESSAGE", "message")
    monkeypatch.setenv("PATH", "/nonexistent")        # not the installer's shell PATH
    assert services._message_bin() == str(local / "message")
    assert str(local) in services.spec("mail")["EnvironmentVariables"]["PATH"].split(":")


# --- the mail check asks sessionmail what carries the mail (sessionmail 0.2.0 `message setup`) ---
# Canned from sessionmail 0.2.0's own `message setup` run in a sandbox (temp settings home).
# No test here runs a real one (tests/setup_fence.py): each answer is a fake or a temp stub.
PLAN = """message setup: the plan, printed whole before anything is applied
privileged steps (sudo):
  none
steps:
  mail-root        {root}
  box:ada          TODO     make /H/.mail/agents/ada/{{cur,new,tmp}} (0700)
  box:heard        {heard}
  maps             DONE     the carrier is the mail drop: it reads the maps per send; none are written
  notmuch-profile  TODO     write /H/.config/notmuch/sessionkit/config (database.path=/H/.mail)
  notmuch-hook     BLOCKED  install notmuch: 'notmuch' is not found ($SESSIONMAIL_NOTMUCH, else PATH)
  carrier          {carrier}
note: nothing here runs `notmuch new`
"""
DROP = "DONE     the mail drop (the default); no suffix is needed"
MTA = "DONE     the MTA at /usr/sbin/sendmail (from ~/.sessionkit/mail/settings/mail.json)"
BROKEN = ('MANUAL   write "sendmail": "drop" in ~/.sessionkit/mail/settings/mail.json (or set '
          'SESSIONMAIL_SENDMAIL=drop): the MTA at /nonexistent/sendmail (from '
          '~/.sessionkit/mail/settings/mail.json) is not an executable file, so `message send` '
          'refuses with no MTA')
TODO_BOX = "TODO     make /H/.mail/agents/heard/{cur,new,tmp} (0700)"
DONE_BOX = "DONE     /H/.mail/agents/heard"
APPLIED = ("applying:\n  mail-root        made /H/.mail/agents (0700)\n"
           "  box:ada          made /H/.mail/agents/ada\n  box:heard        made /H/.mail/agents/heard\n"
           "  notmuch-profile  wrote /H/.config/notmuch/sessionkit/config (database.path=/H/.mail)\n"
           "message setup: done what it can; 1 step(s) left for you: notmuch-hook (BLOCKED)\n")
FALSE = ("until sessionmail", "not qualified", "plain mail", "needs SESSIONMAIL_SENDMAIL=drop",
         "send by postfix", "deliver nothing", "signed under postfix", '"carrier":')
REAL_SETUP = services._message_setup        # before any test's patch: the stub tests run it
LIVE = Path(pwd.getpwuid(os.getuid()).pw_dir)
_SESSION_BOX = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _live_mail() -> dict:
    """What a real `message setup` would change under the LIVE home (from pwd, never $HOME):
    the agents root's boxes (session boxes aside: live seats birth those at any moment), the
    flat heard box, and every file under ~/.config/notmuch and the notmuch hook dir, with
    its mtime. Read only."""
    def tree(d: Path):
        return {str(p): p.lstat().st_mtime_ns for p in d.rglob("*")} if d.is_dir() else None
    agents = LIVE / ".mail/agents"
    return {"agents": sorted(n for n in os.listdir(agents) if not _SESSION_BOX.fullmatch(n))
            if agents.is_dir() else None,
            "heard": (agents / "heard").exists(),
            "notmuch": tree(LIVE / ".config/notmuch"),
            "hooks": tree(LIVE / ".mail/.notmuch/hooks")}


def _setup(monkeypatch, carrier=DROP, heard=TODO_BOX, apply=APPLIED, refused=None):
    calls = []

    def fake(argv, runs=None):
        calls.append(list(argv))
        if refused:
            return 2, "", f"message setup: REFUSED - {refused}. Nothing was applied.\n"
        plan = PLAN.format(root="TODO     make /H/.mail/agents (0700)", heard=heard, carrier=carrier)
        if "--dry-run" in argv:
            return 4, plan + "dry run: nothing applied (4 step(s) TODO)\n", ""
        return 4, plan + apply, ""
    monkeypatch.setattr(services, "_message_setup", fake)
    return calls


def test_drop_host_makes_the_flat_heard_box_by_setup_and_checks_no_postfix(fence, monkeypatch):
    calls = _setup(monkeypatch)
    monkeypatch.setattr(services, "_postconf", lambda p: pytest.fail("postconf asked on a drop host"))
    out = services.mail_checks()
    assert calls == [["--box", "heard", "--dry-run"], ["--box", "heard"]]
    drop = [l for l in out if l.startswith("sendmail the mail drop")]
    assert len(drop) == 1 and "no postfix needed" in drop[0]
    assert "Postfix is opt-in" in drop[0] and "`sendmail` key" in drop[0]
    assert "heard  box made /H/.mail/agents/heard (by `message setup --box heard`)" in out
    assert any(l.startswith("mail   `message setup` also did") and "notmuch-profile" in l for l in out)
    assert not any("postfix" in l and "no postfix needed" not in l for l in out)
    assert not any(".tm-store" in l or "vmailbox-gen" in l or "FLAT" in l for l in out)
    assert any(l.startswith("heard  an agent hears heard turns only once it subscribes") for l in out)
    assert not any("message subscription add" in l for l in out)     # not how a stranger subscribes
    assert not any(f in l for l in out for f in FALSE)
    assert fence["calls"]["vmailbox"] == 0


def test_drop_host_box_present(fence, monkeypatch):
    calls = _setup(monkeypatch, heard=DONE_BOX)
    out = services.mail_checks()
    assert calls == [["--box", "heard", "--dry-run"]]                  # idempotent: nothing applied
    assert "heard  box present: /H/.mail/agents/heard" in out


def test_drop_host_dry_run_makes_nothing_and_says_would_make(fence, monkeypatch):
    calls = _setup(monkeypatch)
    out = services.mail_checks(dry_run=True)
    assert calls == [["--box", "heard", "--dry-run"]]
    assert any(l.startswith("heard  would make the heard box: `message setup --box heard`") for l in out)
    assert any(l.startswith("mail   `message setup` would also") for l in out)
    assert not (fence["home"] / ".mail").exists()


def test_drop_host_setup_failing_warns_names_the_command_and_goes_on(fence, monkeypatch):
    _setup(monkeypatch, apply="applying:\n  box:heard        FAILED: disk full\n")
    out = services.mail_checks()
    warn = [l for l in out if l.startswith("WARNING: heard box NOT made")]
    assert len(warn) == 1 and "`message setup --box heard`" in warn[0] and "refused" in warn[0]
    assert "disk full" in warn[0]
    assert out[-1].startswith("heard  an agent hears")                   # the install goes on


def test_drop_host_setup_that_cannot_run_warns(fence, monkeypatch):
    calls = _setup(monkeypatch)
    dry = services._message_setup
    monkeypatch.setattr(services, "_message_setup",
                        lambda argv, runs=None: dry(argv) if "--dry-run" in argv else None)
    warn = [l for l in services.mail_checks() if l.startswith("WARNING: heard box NOT made")]
    assert len(warn) == 1 and "it could not be run" in warn[0] and calls == [["--box", "heard", "--dry-run"]]


def test_drop_host_heard_blocked_warns(fence, monkeypatch):
    calls = _setup(monkeypatch, heard="BLOCKED  /H/.mail/agents/heard is there and is not a directory")
    out = services.mail_checks()
    assert any(l.startswith("WARNING: heard box NOT made") and "not a directory" in l for l in out)
    assert calls == [["--box", "heard", "--dry-run"]]                  # nothing applied over it


def test_drop_host_configures_nothing_beyond_the_box(fence, monkeypatch):
    """RULING C: on a drop host install asks setup for the heard box and nothing else (no
    --write-maps, no other box), and sets no carrier, in this process or a plist."""
    calls = _setup(monkeypatch)
    services.mail_checks()
    assert calls == [["--box", "heard", "--dry-run"], ["--box", "heard"]]
    assert "SESSIONMAIL_SENDMAIL" not in os.environ
    assert not (fence["home"] / ".sessionkit").exists()                # no settings key written
    for name in ("mail", "mouth", "keeper"):
        env = services.spec(name)["EnvironmentVariables"]
        assert "SESSIONMAIL_SENDMAIL" not in env and "MAGICDNS_SUFFIX" not in env


def test_mta_host_keeps_the_postfix_checks_and_the_store_dir_box(fence, monkeypatch):
    calls = _setup(monkeypatch, carrier=MTA)
    out = services.mail_checks(dry_run=True)
    assert calls == [["--box", "heard", "--dry-run"]]                  # no box made by setup here
    assert any(l.startswith("sendmail the MTA at /usr/sbin/sendmail") for l in out)
    assert any(l.startswith("postfix present") for l in out)
    assert any(".tm-store" in l for l in out)                          # the fleet's nested box
    assert not any(f in l for l in out for f in FALSE)


def test_mta_host_install_takes_the_mta_branch_and_makes_nothing(fence, monkeypatch):
    """A fleet host's settings name postfix (`sendmail`: an MTA). A real install there, not a
    dry run, with no store dir: setup is only ever asked with --dry-run, no box is made, no
    map is rebuilt, and the live home's mail and notmuch files are untouched."""
    before = _live_mail()
    calls = _setup(monkeypatch, carrier=MTA)
    out = services.mail_checks()
    assert calls == [["--box", "heard", "--dry-run"]]
    assert any(l.startswith("sendmail the MTA at") for l in out)
    assert any(l.startswith("WARNING: heard box MISSING, not made") for l in out)
    assert fence["calls"]["vmailbox"] == 0
    agents = fence["home"] / ".mail/agents"
    assert not agents.exists() or not any(agents.rglob("*"))
    assert _live_mail() == before


def test_mta_named_but_broken_is_a_warning(fence, monkeypatch):
    _setup(monkeypatch, carrier=BROKEN)
    out = services.mail_checks(dry_run=True)
    assert any(l.startswith("WARNING: sendmail BROKEN") and "not an executable file" in l for l in out)


def test_refused_settings_are_said_and_nothing_is_made(fence, monkeypatch):
    calls = _setup(monkeypatch, refused='"sendmail": "nonsense" in ~/.sessionkit/mail/settings/mail.json is not a carrier')
    out = services.mail_checks()
    assert calls == [["--box", "heard", "--dry-run"]]
    assert any(l.startswith("WARNING: sendmail REFUSED") and "is not a carrier" in l for l in out)
    assert not any(l.startswith("heard") or "postfix" in l for l in out)


UNREAD = [None,                                                     # no `message`, or it would not start
          (2, "", "Error: No such command 'setup'."),                # an older `message`
          (4, "steps:\n  carrier          SOMETHING  new\n", ""),   # a row this cannot read
          (0, "", "")]                                                # nothing printed


@pytest.mark.parametrize("dry", [True, False])
@pytest.mark.parametrize("got", UNREAD)
def test_an_unreadable_answer_is_its_own_state_never_a_guessed_mta(fence, monkeypatch, got, dry):
    """Unknown is not an MTA: a WARNING naming `message setup --dry-run`, no box, neither the
    drop's lines nor the postfix checks, and the install goes on."""
    calls = []
    monkeypatch.setattr(services, "_message_setup", lambda argv, runs=None: calls.append(argv) or got)
    monkeypatch.setattr(services, "_postconf", lambda p: pytest.fail("postconf asked on an unknown carrier"))
    store = _store(fence["home"])
    out = services.mail_checks(dry_run=dry)
    warn = [l for l in out if l.startswith("WARNING: sendmail UNKNOWN")]
    assert len(warn) == 1 and "could not be read" in warn[0] and "`message setup --dry-run`" in warn[0]
    assert "install goes on" in warn[0]
    assert calls == [["--box", "heard", "--dry-run"]]                  # asked once; nothing applied
    assert not any(l.startswith(("heard", "postfix", "sendmail the", "mail ")) for l in out)
    assert not (store / "heard").exists() and fence["calls"]["vmailbox"] == 0
    assert not any(f in l for l in out for f in FALSE)


def test_an_unknown_carrier_install_goes_on(fence, capsys, monkeypatch):
    monkeypatch.setattr(services, "_message_setup", lambda argv, runs=None: None)
    assert services.install(["mail"], dry_run=True) == 0
    assert "WARNING: sendmail UNKNOWN" in capsys.readouterr().out


@pytest.mark.parametrize("row,source", [
    ("DONE     the mail drop (the default); no suffix is needed", "(the default)"),
    ("DONE     the mail drop (from ~/.sessionkit/mail/settings/mail.json); no suffix is needed",
     "(from ~/.sessionkit/mail/settings/mail.json)"),
    ("DONE     the mail drop (the default, from ~/.sessionkit/mail/settings/mail.template.json); "
     "no suffix is needed", "(the default, from ~/.sessionkit/mail/settings/mail.template.json)"),
    ("DONE     the mail drop (from $SESSIONMAIL_SENDMAIL); no suffix is needed",
     "(from $SESSIONMAIL_SENDMAIL)"),
])
def test_the_drop_line_says_sessionmails_own_source(fence, monkeypatch, row, source):
    """Never a fixed "(the default)": a key set to "drop" reads as the settings file's."""
    _setup(monkeypatch, carrier=row, heard=DONE_BOX)
    drop = [l for l in services.mail_checks(dry_run=True) if l.startswith("sendmail ")]
    assert drop == [l for l in drop if l.startswith(f"sendmail the mail drop {source} for the launchd")]
    assert len(drop) == 1
    if source != "(the default)":
        assert "(the default)" not in drop[0]


def test_the_shell_variable_is_said_never_used(fence, monkeypatch):
    _setup(monkeypatch)
    monkeypatch.setenv("SESSIONMAIL_SENDMAIL", "/opt/x/sendmail")
    out = services.mail_checks(dry_run=True)
    gap = [l for l in out if "will not see SESSIONMAIL_SENDMAIL=/opt/x/sendmail" in l]
    assert len(gap) == 1 and "`sendmail` key" in gap[0] and "~/.sessionkit/mail/settings/mail.json" in gap[0]
    assert not gap[0].startswith("WARNING") and "the default" not in gap[0]


@pytest.mark.parametrize("carrier", [DROP, MTA, BROKEN, None])
@pytest.mark.parametrize("shell", ["", "drop", "/opt/x/sendmail"])
def test_no_false_line_on_any_branch(fence, monkeypatch, carrier, shell):
    if carrier is None:
        _setup(monkeypatch, refused="it is not a carrier")
    else:
        _setup(monkeypatch, carrier=carrier)
    if shell:
        monkeypatch.setenv("SESSIONMAIL_SENDMAIL", shell)
    for dry in (True, False):
        out = services.mail_checks(dry_run=dry)
        assert not any(f in l for l in out for f in FALSE), out


def test_setup_runs_in_launchds_environment_not_this_shells(fence, monkeypatch):
    monkeypatch.setenv("SESSIONMAIL_SENDMAIL", "drop")
    env = services._setup_env()
    assert "SESSIONMAIL_SENDMAIL" not in env and set(env) == {"PATH", "HOME"}
    assert env["PATH"] == services._service_path()


def test_setup_runs_in_a_hand_made_mailers_environment(fence, tmp_path):
    plist = tmp_path / "hand.plist"
    plist.write_bytes(plistlib.dumps({"EnvironmentVariables": {
        "PATH": "/x/bin", "HOME": "/x", "SESSIONMAIL_SENDMAIL": "drop", "OTHER": "no"}}))
    env = services._setup_env(("dev.sessionvoice.ears-mail", plist))
    assert env == {"PATH": "/x/bin", "HOME": "/x", "SESSIONMAIL_SENDMAIL": "drop"}


def _stub_message(fence, carrier=DROP, heard=TODO_BOX) -> Path:
    """The fence's `message`, now a stub of sessionmail's: it logs its argv and environment
    and prints a canned plan. It lives in the test's temp dir, so the setup fence lets it run."""
    d = fence["tmp"]
    (d / "plan.txt").write_text(PLAN.format(root="DONE     /H/.mail/agents", heard=heard, carrier=carrier))
    (d / "applied.txt").write_text(APPLIED)
    stub = Path(services.MESSAGE)
    stub.write_text('#!/bin/sh\nd=$(dirname "$0")\necho "$*" >> "$d/argv.log"\nenv > "$d/env.log"\n'
                    'cat "$d/plan.txt"\ncase " $* " in *" --dry-run "*) ;; *) cat "$d/applied.txt" ;; esac\n'
                    'exit 4\n')
    stub.chmod(0o755)
    return d


def test_the_real_message_setup_call_runs_a_stub_in_the_services_env(fence, monkeypatch):
    """_message_setup itself, unpatched, against a stub `message`: the argv, the exit 4 that
    is not read as failure, and launchd's environment (no shell SESSIONMAIL_SENDMAIL)."""
    before = _live_mail()
    monkeypatch.setattr(services, "_message_setup", REAL_SETUP)
    monkeypatch.setenv("SESSIONMAIL_SENDMAIL", "/opt/x/sendmail")
    d = _stub_message(fence)
    out = services.mail_checks()
    assert (d / "argv.log").read_text().splitlines() == ["setup --box heard --dry-run", "setup --box heard"]
    env = dict(l.split("=", 1) for l in (d / "env.log").read_text().splitlines() if "=" in l)
    assert "SESSIONMAIL_SENDMAIL" not in env and env["HOME"] == str(fence["home"])
    assert env["PATH"] == services._service_path()
    assert any(l.startswith("sendmail the mail drop") for l in out)
    assert "heard  box made /H/.mail/agents/heard (by `message setup --box heard`)" in out
    assert _live_mail() == before


def test_the_setup_fence_refuses_a_real_message_setup():
    with pytest.raises(setup_fence.RealMessageSetup):
        subprocess.run(["/nonexistent/bin/message", "setup", "--dry-run"], capture_output=True)
    with pytest.raises(setup_fence.RealMessageSetup):
        subprocess.run(["message", "setup", "--box", "heard"], capture_output=True)   # a bare name too


def test_setup_rows_parse_sessionmails_own_output():
    plan, applied = services._setup_rows(PLAN.format(root="DONE     /H", heard=TODO_BOX, carrier=DROP) + APPLIED)
    assert plan["carrier"] == ("DONE", "the mail drop (the default); no suffix is needed")
    assert plan["box:heard"][0] == "TODO" and applied["box:heard"] == "made /H/.mail/agents/heard"
    assert "note:" not in plan and "applying:" not in plan


def test_drop_host_with_a_nested_heard_box_makes_no_second_one(fence, monkeypatch):
    calls = _setup(monkeypatch)
    store = fence["home"] / ".mail/agents/store"
    (store / "heard/new").mkdir(parents=True); (store / ".tm-store").write_text("")
    out = services.mail_checks()
    assert calls == [["--box", "heard", "--dry-run"]]
    assert f"heard  box present: {store / 'heard'}" in out
    assert not any(".tm-store" in l for l in out)


def test_the_mail_check_configures_nothing(fence, monkeypatch):
    """It checks and says (RULING C): no carrier is set, in this process or a plist."""
    monkeypatch.setattr(services, "_postconf", lambda p: None)
    services.mail_checks(dry_run=True)
    assert "SESSIONMAIL_SENDMAIL" not in os.environ
    for name in ("mail", "mouth", "keeper"):
        env = services.spec(name)["EnvironmentVariables"]
        assert "SESSIONMAIL_SENDMAIL" not in env and "MAGICDNS_SUFFIX" not in env


def test_postfix_without_agent_maps_is_said(fence, monkeypatch):
    monkeypatch.setattr(services, "_postconf", lambda p: "" if p == "virtual_mailbox_maps" else "3.8.6")
    assert any("no virtual_mailbox_maps" in l for l in services.mail_checks(dry_run=True))


def test_postfix_present_is_never_changed(fence):
    out = services.mail_checks(dry_run=True)
    assert any(l.startswith("postfix present") and "not changed" in l for l in out)


def test_one_store_gets_a_nested_heard_box_and_a_route(fence):
    store = _store(fence["home"])
    out = services.mail_checks()
    assert all((store / "heard" / s).is_dir() for s in ("new", "cur", "tmp"))
    assert not (fence["home"] / ".mail/agents/heard").exists()      # never flat
    assert fence["calls"]["vmailbox"] == 1 and any("routed" in l for l in out)
    assert any("heard+macbook" in l for l in out)                   # the box to subscribe to


def test_dry_run_makes_no_box_and_runs_nothing(fence):
    store = _store(fence["home"])
    out = services.mail_checks(dry_run=True)
    assert not (store / "heard").exists() and fence["calls"]["vmailbox"] == 0
    assert any("would make" in l for l in out)


@pytest.mark.parametrize("stores", [0, 2])
def test_no_single_store_makes_nothing_and_says_how(fence, stores):
    made = [_store(fence["home"], f"s{i}") for i in range(stores)]
    out = services.mail_checks()
    assert not any((d / "heard").exists() for d in made) and fence["calls"]["vmailbox"] == 0
    miss = next(l for l in out if "heard box MISSING" in l)
    assert "mkdir -p" in miss and "vmailbox-gen" in miss
    if stores == 2:
        assert "s0" in miss and "s1" in miss


def test_a_heard_box_already_there_is_kept(fence):
    store = _store(fence["home"])
    (store / "heard/new").mkdir(parents=True)
    out = services.mail_checks()
    assert any(l.startswith("heard  box present") for l in out) and fence["calls"]["vmailbox"] == 0


def test_a_flat_heard_box_is_called_out(fence):
    (fence["home"] / ".mail/agents/heard/new").mkdir(parents=True)
    _store(fence["home"])
    assert any("FLAT box" in l for l in services.mail_checks(dry_run=True))


@pytest.mark.parametrize("ran,said", [(None, "not on PATH"), ((1, "boom"), "FAILED (exit 1): boom")])
def test_vmailbox_gen_absent_or_failing_is_said(fence, monkeypatch, ran, said):
    _store(fence["home"])
    monkeypatch.setattr(services, "_vmailbox_gen", lambda: ran)
    assert any(said in l for l in services.mail_checks())


def test_off_path_vmailbox_gen_is_named_by_its_host_path(fence, monkeypatch):
    # sessionmail keeps vmailbox-gen OFF the PATH (SM-74); a host's copy is
    # $MAIL_ROOT/.postfix/vmailbox-gen, so "run vmailbox-gen" names nothing runnable
    _store(fence["home"])
    monkeypatch.setattr("shutil.which", lambda name, *a, **k: None)
    monkeypatch.setattr(services, "_vmailbox_gen", lambda: None)
    host_copy = str(fence["home"] / ".mail/.postfix/vmailbox-gen")
    assert any("not on PATH" in l and host_copy in l for l in services.mail_checks())


def test_the_missing_box_line_names_the_runnable_generator(fence, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name, *a, **k: None)
    miss = next(l for l in services.mail_checks() if "heard box MISSING" in l)
    assert miss.endswith("&& " + str(fence["home"] / ".mail/.postfix/vmailbox-gen"))
    _store(fence["home"])
    assert any(str(fence["home"] / ".mail/.postfix/vmailbox-gen") in l
               for l in services.mail_checks(dry_run=True) if "would make" in l)


@pytest.mark.parametrize("label", HANDS)
@pytest.mark.parametrize("how", ["plist", "loaded"])
def test_ours_installed_before_the_hand_made_one_goes(fence, capsys, how, label):
    # never two mailers from the other side: ours was there first, theirs came after
    fence["loaded"].add(label)
    ours = _plist(fence, "mail")
    if how == "plist":
        ours.parent.mkdir(parents=True); ours_plist(ours)          # as install wrote it: its MARKER
    else:                                       # loaded by install: its environment carries MARKER
        fence["loaded"].add("dev.sessionkit.voice.ears-mail")
        fence["env"]["dev.sessionkit.voice.ears-mail"] = OURS_ENV
    assert services.install(["mail"]) == 0
    out = capsys.readouterr().out
    assert "ours REMOVED" in out and not ours.exists()
    booted = [a for a in fence["calls"]["launchctl"] if a[0] == "bootout"]
    assert booted == ([("bootout", f"gui/{os.getuid()}/dev.sessionkit.voice.ears-mail")] if how == "loaded" else [])
    assert not any(label in " ".join(a) for a in fence["calls"]["launchctl"] if a[0] != "print")


def test_dry_run_only_says_ours_would_go(fence, capsys):
    fence["loaded"].update({"dev.sessionvoice.ears-mail", "dev.sessionkit.voice.ears-mail"})
    ours = _plist(fence, "mail"); ours.parent.mkdir(parents=True); body = ours_plist(ours).read_bytes()
    assert services.install(["mail"], dry_run=True) == 0
    assert "ours would be REMOVED" in capsys.readouterr().out and ours.read_bytes() == body
    assert not [a for a in fence["calls"]["launchctl"] if a[0] not in ("print", "list")]   # read-only


# --- items 4, 5 and 6: the Silero model ---------------------------------------------------

def _no_model(fence):
    (fence["home"] / ".cache/ears/silero_vad.onnx").unlink()


def test_silero_present(fence):
    assert services.silero() == (True, f"silero present: {fence['home'] / '.cache/ears/silero_vad.onnx'}")


def test_silero_linked_from_voicemodes_copy(fence):
    _no_model(fence)
    copy = fence["home"] / ".voicemode/models/silero/silero_vad.onnx"
    copy.parent.mkdir(parents=True); copy.write_bytes(b"onnx")
    want = fence["home"] / ".cache/ears/silero_vad.onnx"
    ok, line = services.silero(dry_run=True)
    assert ok and "would link" in line and not want.exists()
    ok, line = services.silero()
    assert ok and "linked" in line and want.is_symlink() and want.resolve() == copy.resolve()


def test_silero_missing_and_unfetchable_refuses_before_any_plist(fence, capsys):
    """Install fetches a missing model (EARS-449); the fence's opener refuses the network, so
    the fetch fails, and that failure is the refusal: rc 2, nothing written, no traceback."""
    _no_model(fence)
    assert services.install(["mouth", "keeper", "mail"]) == 2
    err = capsys.readouterr().err
    assert "REFUSED: silero: could not fetch" in err and "network refused by the fence" in err
    assert "Traceback" not in err
    assert not services.AGENTS.exists() and not services.LOGS.exists()
    assert not services._silero_want().exists()
    assert not fence["calls"]["launchctl"]


def test_dry_run_says_it_would_fetch_the_missing_model_and_fetches_nothing(fence, monkeypatch, capsys):
    _no_model(fence)
    monkeypatch.setattr(services, "SILERO_OPENER", lambda *a, **kw: pytest.fail("a dry run fetched"))
    assert services.install(["keeper"], dry_run=True) == 0
    cap = capsys.readouterr()
    assert "silero would fetch Silero VAD" in cap.out and "dev.sessionkit.voice.ears-keeper" in cap.out
    assert cap.err == ""
    assert not services.AGENTS.exists() and not services._silero_want().exists()


def test_the_model_install_checked_is_the_one_the_keeper_reads(fence, monkeypatch):
    m = fence["tmp"] / "elsewhere.onnx"; m.write_bytes(b"onnx")
    monkeypatch.setenv("EARS_SILERO_ONNX", str(m))
    assert services.silero()[1] == f"silero present: {m}"
    assert services.spec("keeper")["EnvironmentVariables"]["EARS_SILERO_ONNX"] == str(m)


# --- items 7 and 8: the STT probe ---------------------------------------------------------

class _Stub(http.server.BaseHTTPRequestHandler):
    reply = (200, b'{"text": ""}')
    seen = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        type(self).seen.append((self.path, self.headers["Content-Type"], body))
        code, data = type(self).reply
        self.send_response(code); self.send_header("Content-Length", str(len(data))); self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture
def stub():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Stub)
    _Stub.seen = []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()


def test_probe_posts_one_clip_and_takes_a_200_with_text(stub):
    _, url = stub
    _Stub.reply = (200, b'{"text": "hello"}')
    assert services.probe_stt(url, "mlx-community/parakeet-tdt-0.6b-v3") is None
    path, ctype, body = _Stub.seen[0]
    assert path == "/v1/audio/transcriptions" and ctype.startswith("multipart/form-data")
    assert b"RIFF" in body and b"mlx-community/parakeet-tdt-0.6b-v3" in body


@pytest.mark.parametrize("reply,said", [
    ((500, b"ModuleNotFoundError: No module named mlx_audio.tts.models.parakeet_tdt_0"), "HTTP 500"),
    ((200, b'{"error": "x"}'), "no text"),
    ((200, b"<html>"), "not JSON"),
])
def test_probe_says_what_failed(stub, reply, said):
    _, url = stub
    _Stub.reply = reply
    assert said in services.probe_stt(url)


def test_probe_of_nothing_listening():
    assert services.probe_stt("http://127.0.0.1:9/v1", timeout=2).startswith("no answer")


def _real_probe(monkeypatch, answers):
    """probe_stt answering per URL from ANSWERS (None = it answers), recording each URL."""
    asked = []
    monkeypatch.setattr(services, "probe_stt", lambda url, model=None, timeout=30.0:
                        asked.append(url) or answers.get(url.rstrip("/")))
    return asked


def test_a_failed_new_final_falls_back_to_whisper(fence, monkeypatch):
    asked = _real_probe(monkeypatch, {"http://127.0.0.1:8890/v1": "HTTP 500"})
    out = services.seed_stt()
    conf = json.loads((fence["home"] / SETTINGS / "ears.json").read_text())
    assert conf["final"] == {"url": "http://127.0.0.1:2022/v1"}
    assert "FALLBACK" in out and "HTTP 500" in out and "parakeet" in out
    assert asked == ["http://127.0.0.1:8890/v1", "http://127.0.0.1:2022/v1"]


@pytest.mark.parametrize("dry_run", [True, False])
def test_every_probe_is_said_before_its_request(fence, monkeypatch, capsys, dry_run):
    # A check may ask the user's own STT, the dry run's
    # too, but never silently: `probing <url> (read-only)` comes BEFORE each request
    before = []

    def probe(url, model=None, timeout=30.0):
        before.append((url, capsys.readouterr().out))   # what was printed before this request
        return "HTTP 500" if "8890" in url else None
    monkeypatch.setattr(services, "probe_stt", probe)
    services.seed_stt(dry_run=dry_run)
    assert [u for u, _ in before] == ["http://127.0.0.1:8890/v1", "http://127.0.0.1:2022/v1"]
    for url, printed in before:                          # the chosen engine AND the fallback
        assert printed.splitlines()[-1] == f"probing {url} (read-only)"


def test_an_unprobed_final_says_no_probing(fence, monkeypatch, capsys):
    monkeypatch.setattr(services, "probe_stt", lambda *a, **k: pytest.fail("probed"))
    services.seed_stt(no_probe="it is the stt service this install loads")
    assert "probing" not in capsys.readouterr().out


def test_a_fallback_that_fails_too_is_said(fence, monkeypatch):
    _real_probe(monkeypatch, {"http://127.0.0.1:8890/v1": "HTTP 500", "http://127.0.0.1:2022/v1": "no answer"})
    out = services.seed_stt()
    assert "did not answer either" in out


def test_an_answering_final_is_written(fence, monkeypatch):
    _real_probe(monkeypatch, {})
    assert "probe: it answers" in services.seed_stt()
    conf = json.loads((fence["home"] / SETTINGS / "ears.json").read_text())
    assert conf["final"]["model"] == "mlx-community/parakeet-tdt-0.6b-v3"


def test_a_kept_final_is_not_reprobed(fence, monkeypatch):
    conf = fence["home"] / SETTINGS / "ears.json"
    conf.parent.mkdir(parents=True); conf.write_text(json.dumps({"final": {"url": "http://x:1/v1"}}))
    asked = _real_probe(monkeypatch, {})
    assert services.seed_stt().startswith("final kept") and asked == []


def test_a_given_final_is_probed(fence, monkeypatch):
    conf = fence["home"] / SETTINGS / "ears.json"
    conf.parent.mkdir(parents=True); conf.write_text(json.dumps({"final": {"url": "http://x:1/v1"}}))
    monkeypatch.setenv("EARS_STT_URL", "http://127.0.0.1:2099/v1")
    asked = _real_probe(monkeypatch, {"http://127.0.0.1:2099/v1": "HTTP 500"})
    assert "FALLBACK" in services.seed_stt() and asked[0] == "http://127.0.0.1:2099/v1"


def test_the_stt_service_this_install_loads_is_not_probed(fence, monkeypatch):
    monkeypatch.setenv("EARS_STT_URL", "http://127.0.0.1:2023/v1")
    asked = _real_probe(monkeypatch, {})
    assert services._no_probe(["stt", "keeper"]) and services._no_probe(["keeper"]) is None
    out = services.seed_stt(no_probe=services._no_probe(["stt", "keeper"]))
    assert "not probed" in out and asked == []


def test_dry_run_seed_writes_nothing(fence, monkeypatch):
    _real_probe(monkeypatch, {"http://127.0.0.1:8890/v1": "HTTP 500"})
    out = services.seed_stt(dry_run=True)
    assert out.startswith("final would be set") and "FALLBACK" in out
    assert not (fence["home"] / SETTINGS / "ears.json").exists()


def test_readme_says_the_mlx_audio_parakeet_needs():
    assert "mlx-audio>=0.5.4" in (REPO / "README.md").read_text()


# --- item 9: one log folder ---------------------------------------------------------------

def test_the_keeper_logs_where_the_agents_do(fence):
    env = services.spec("keeper")["EnvironmentVariables"]
    assert env["EARS_LOG_DIR"] == str(services.LOGS)
    assert services.spec("keeper")["StandardOutPath"].startswith(env["EARS_LOG_DIR"])


# --- items 11 and 12: --agent and --notify ------------------------------------------------

def test_agent_adds_the_wake_seat_and_takes_the_notices(fence, capsys):
    _store(fence["home"])
    assert services.install(["keeper", "mail"], agent="dave") == 0
    assert wake.seats() == {"dave": "dave"}
    env = plistlib.loads(_plist(fence, "keeper").read_bytes())["EnvironmentVariables"]
    assert env["EARS_NOTIFY"] == "dave"
    out = capsys.readouterr().out
    assert "WARNING" not in out.replace("WARNING: vmailbox", "")


def test_notify_overrides_the_notices_alone(fence):
    _store(fence["home"])
    services.install(["keeper", "mail"], agent="dave", notify="jim+ears")
    env = plistlib.loads(_plist(fence, "keeper").read_bytes())["EnvironmentVariables"]
    assert env["EARS_NOTIFY"] == "jim+ears" and wake.seats() == {"dave": "dave"}


def test_no_agent_says_nobody_is_woken_and_notices_are_log_only(fence, capsys):
    _store(fence["home"])
    services.install(["keeper", "mail"])
    out = capsys.readouterr().out
    assert "NOBODY will be woken" in out and "--agent NAME" in out
    assert "LOG ONLY" in out
    env = plistlib.loads(_plist(fence, "keeper").read_bytes())["EnvironmentVariables"]
    assert "EARS_NOTIFY" not in env
    assert not Path(os.environ["EARS_WAKE"]).exists()


@pytest.mark.parametrize("answer", [
    {"operator": None, "mailbox": None, "privileged": []},
    {"operator": "someone@elsewhere.example", "mailbox": None, "privileged": []},   # AGENT_OPERATOR_ADDR alone
], ids=["unconfigured", "addr-only"])
def test_no_agent_and_no_operator_says_log_only_and_names_the_operator_rule(fence, capsys, monkeypatch, answer):
    """EARS-304 R3: with nothing to put in the plist the keeper takes the operator at its start;
    none configured now (or only AGENT_OPERATOR_ADDR), so install still says LOG ONLY."""
    _store(fence["home"])
    monkeypatch.setattr(operator_config, "current", lambda: operator_config.parse(answer))
    services.install(["keeper", "mail"])
    out = capsys.readouterr().out
    assert "LOG ONLY" in out and "`session operator`" in out
    assert "someone@elsewhere.example" not in out
    env = plistlib.loads(_plist(fence, "keeper").read_bytes())["EnvironmentVariables"]
    assert "EARS_NOTIFY" not in env


def test_no_agent_with_an_operator_names_it_and_leaves_the_plist_alone(fence, capsys, monkeypatch):
    """EARS-304 R3: an operator configured: install names that address, the plist still leaves
    EARS_NOTIFY out (so the keeper reads it at its start), and nothing says LOG ONLY."""
    _store(fence["home"])
    answer = {"operator": "you@host.example", "mailbox": "you", "privileged": ["you"]}
    monkeypatch.setattr(operator_config, "current", lambda: operator_config.parse(answer))
    services.install(["keeper", "mail"])
    out = capsys.readouterr().out
    assert "notify keeper notices to the operator (you@host.example, from `session operator`)" in out
    assert "LOG ONLY" not in out
    env = plistlib.loads(_plist(fence, "keeper").read_bytes())["EnvironmentVariables"]
    assert "EARS_NOTIFY" not in env


def test_an_explicit_notify_never_asks_the_operator_reader(fence, capsys, monkeypatch):
    _store(fence["home"])

    def boom():
        raise AssertionError("the reader was asked")
    monkeypatch.setattr(operator_config, "current", boom)
    services.install(["keeper", "mail"], notify="jim+ears")
    assert "notify keeper notices to jim+ears (EARS_NOTIFY)" in capsys.readouterr().out


def test_no_agent_but_seats_already_named(fence, capsys):
    wake.add("vera")
    assert services.wake_checks(None) == [f"wake   no --agent; {wake.target()} already wakes vera"]


def test_dry_run_agent_adds_nothing(fence, capsys):
    _store(fence["home"])
    assert services.install(["keeper", "mail"], dry_run=True, agent="dave") == 0
    out = capsys.readouterr().out
    assert "would add dave" in out and "<string>dave</string>" in out     # EARS_NOTIFY in the plist shown
    assert not Path(os.environ["EARS_WAKE"]).exists() and not services.AGENTS.exists()


def test_main_passes_agent_and_notify(monkeypatch):
    got = {}
    monkeypatch.setattr(services, "install", lambda names, dry=False, **kw: got.update(kw, names=names) or 0)
    services.main(["install", "--agent", "dave", "--notify", "jim"])
    assert got == {"agent": "dave", "notify": "jim", "no_prefetch": False,
                   "names": ["speech", "mouth", "keeper", "mail"]}


# ---- EARS-421: a bare install that would wake nobody is REFUSED -----------------------------------
# Refused only when ALL hold: mail is installed, no --agent, no --no-agent, and the wake file names
# no seat. The refusal lives in main(), before install(): nothing is written.

def _no_install(monkeypatch):
    monkeypatch.setattr(services, "install", lambda *a, **kw: pytest.fail("install() ran after a refusal"))


def _nothing_written(fence):
    assert not services.AGENTS.exists() and not services.LOGS.exists()
    assert not Path(os.environ["EARS_WAKE"]).exists()
    assert not [c for c in fence["calls"]["launchctl"] if c and c[0] in ("bootstrap", "bootout")]


def test_a_bare_install_with_no_seat_is_refused_before_install_runs(fence, monkeypatch, capsys):
    monkeypatch.delenv("TM_AGENT_ADDRESS", raising=False)
    _no_install(monkeypatch)
    assert services.main(["install"]) == 2
    cap = capsys.readouterr()
    assert cap.err.startswith("ears services install: REFUSED: no --agent NAME")
    assert "NOBODY would be woken when you speak" in cap.err and str(wake.target()) in cap.err
    assert "    ears services install --agent NAME" in cap.err        # both forms, each runnable
    assert "    ears services install --no-agent" in cap.err
    assert "`ears wake add NAME`" in cap.err and "nothing was written" in cap.err
    assert "you appear to be seat" not in cap.err                     # TM_AGENT_ADDRESS unset: no hint


def test_a_bare_install_with_no_seat_writes_nothing(fence, monkeypatch, capsys):
    """The real install(), not a spy: no plist, no log dir, no wake file, no launchctl load."""
    monkeypatch.delenv("TM_AGENT_ADDRESS", raising=False)
    _store(fence["home"])
    assert services.main(["install", "--only", "keeper,mail"]) == 2
    assert "REFUSED" in capsys.readouterr().err
    _nothing_written(fence)


@pytest.mark.parametrize("only", [None, "mail", "keeper,mail", "call,mail"])
def test_every_set_with_mail_in_it_is_refused(fence, monkeypatch, capsys, only):
    monkeypatch.delenv("TM_AGENT_ADDRESS", raising=False)
    _no_install(monkeypatch)
    assert services.main(["install"] + (["--only", only] if only else [])) == 2


@pytest.mark.parametrize("addr, seat", [("dave", "dave"), ("dave@host1", "dave"),
                                        ("sessionmail+engineer@host1", "sessionmail+engineer"),
                                        (" vera@host1 ", "vera")])
def test_the_refusal_suggests_the_seat_from_tm_agent_address_and_never_installs_on_it(
        fence, monkeypatch, capsys, addr, seat):
    monkeypatch.setenv("TM_AGENT_ADDRESS", addr)
    _no_install(monkeypatch)
    assert services.main(["install"]) == 2               # suggest, never assume
    err = capsys.readouterr().err
    assert f"you appear to be seat {seat}: ears services install --agent {seat}\n" in err
    assert not Path(os.environ["EARS_WAKE"]).exists()


@pytest.mark.parametrize("addr", ["", "   ", "@host1"])
def test_an_empty_tm_agent_address_gives_no_hint(fence, monkeypatch, capsys, addr):
    monkeypatch.setenv("TM_AGENT_ADDRESS", addr)
    _no_install(monkeypatch)
    assert services.main(["install"]) == 2
    assert "you appear to be seat" not in capsys.readouterr().err


def test_an_unreadable_wake_file_names_nobody_so_it_is_refused(fence, monkeypatch, capsys):
    monkeypatch.delenv("TM_AGENT_ADDRESS", raising=False)
    Path(os.environ["EARS_WAKE"]).parent.mkdir(parents=True, exist_ok=True)
    Path(os.environ["EARS_WAKE"]).write_text("not json")
    _no_install(monkeypatch)
    assert services.main(["install"]) == 2
    assert "REFUSED" in capsys.readouterr().err


def test_no_agent_installs_as_a_bare_install_did_and_still_warns(fence, monkeypatch, capsys):
    monkeypatch.setenv("TM_AGENT_ADDRESS", "dave@host1")          # a hint is never a --agent
    _store(fence["home"])
    assert services.main(["install", "--only", "keeper,mail", "--no-agent"]) == 0
    cap = capsys.readouterr()
    assert "REFUSED" not in cap.err
    assert "NOBODY will be woken" in cap.out and "LOG ONLY" in cap.out     # the warning stays
    assert _plist(fence, "mail").exists() and _plist(fence, "keeper").exists()
    env = plistlib.loads(_plist(fence, "keeper").read_bytes())["EnvironmentVariables"]
    assert "EARS_NOTIFY" not in env                                       # the notices line unchanged
    assert not Path(os.environ["EARS_WAKE"]).exists()                     # no seat added


def test_main_hands_install_no_agent_as_no_agent(monkeypatch):
    got = {}
    monkeypatch.setattr(services, "install", lambda names, dry=False, **kw: got.update(kw, dry=dry) or 0)
    assert services.main(["install", "--no-agent", "--notify", "jim"]) == 0
    assert got == {"agent": None, "notify": "jim", "no_prefetch": False, "dry": False}


def test_agent_and_no_agent_are_mutually_exclusive(fence, monkeypatch, capsys):
    _no_install(monkeypatch)
    with pytest.raises(SystemExit) as e:
        services.main(["install", "--agent", "dave", "--no-agent"])
    assert e.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err
    _nothing_written(fence)


def test_a_wake_file_that_names_a_seat_is_not_refused_and_says_who_it_wakes(fence, monkeypatch, capsys):
    """A re-run (the upgrade path) wakes the seat already named: install goes on, and says which
    seat it wakes (super ruling: no silent 'it worked')."""
    monkeypatch.delenv("TM_AGENT_ADDRESS", raising=False)
    _store(fence["home"])
    wake.add("vera")
    assert services.main(["install", "--only", "keeper,mail"]) == 0
    cap = capsys.readouterr()
    assert "REFUSED" not in cap.err
    assert f"wake   no --agent; {wake.target()} already wakes vera" in cap.out.splitlines()
    assert "NOBODY" not in cap.out
    assert _plist(fence, "mail").exists()


@pytest.mark.parametrize("only", ["speakers", "speech", "mouth", "keeper", "mouth,keeper", "stt"])
def test_only_without_mail_wakes_nobody_either_way_and_is_not_refused(fence, monkeypatch, capsys, only):
    monkeypatch.delenv("TM_AGENT_ADDRESS", raising=False)
    got = []
    monkeypatch.setattr(services, "install", lambda names, dry=False, **kw: got.append(names) or 0)
    assert services.main(["install", "--only", only]) == 0
    assert got and "mail" not in got[0]
    assert "REFUSED" not in capsys.readouterr().err


def test_a_bare_dry_run_says_refused_goes_on_listing_and_exits_2(fence, monkeypatch, capsys):
    """The file's dry-run convention: a dry run says REFUSED and exits 2 as install would, and
    still lists the checks and the plists. Nothing is written either way."""
    monkeypatch.delenv("TM_AGENT_ADDRESS", raising=False)
    _store(fence["home"])
    assert services.main(["install", "--only", "keeper,mail", "--dry-run"]) == 2
    cap = capsys.readouterr()
    assert cap.err.startswith("ears services install: REFUSED: no --agent NAME")
    assert "--- " in cap.out and "NOBODY will be woken" in cap.out
    _nothing_written(fence)


def test_a_dry_run_with_no_agent_or_agent_exits_0(fence, monkeypatch, capsys):
    monkeypatch.delenv("TM_AGENT_ADDRESS", raising=False)
    _store(fence["home"])
    assert services.main(["install", "--only", "keeper,mail", "--dry-run", "--no-agent"]) == 0
    assert services.main(["install", "--only", "keeper,mail", "--dry-run", "--agent", "dave"]) == 0
    assert "REFUSED" not in capsys.readouterr().err
    _nothing_written(fence)
