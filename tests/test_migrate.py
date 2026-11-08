"""``ears home migrate`` (ears.migrate, design.md D7), tests M1-M16, all on fake HOMEs under
tmp_path. No test runs the verb on a real HOME: every Host here is built by the ``mk``
fixture, with a fake launchctl that records its argv (and answers ``print`` only), a fake
process list and no rlimit change. The swap is the real ``renamex_np``, on tmp_path.

Two kinds of host: an S host keeps the heard log in 0.3.2's own root (``~/.sessionvoice``), a
V host in VoiceMode's (``~/.voicemode``), beside VoiceMode's own files. Strays sit at their
old paths (ears.home.ITEMS)."""
import ast
import fcntl
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from ears import heard, home as H, migrate as M

ROOT = Path(__file__).resolve().parents[1]
SV, VM = H.OLD_ROOTS
UID = 501
#: The fixtures' heard day: yesterday, by the real clock, so that a line the code appends today
#: goes to a later file, as on a live host (the heard writer reads the real clock).
DAY = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
YEAR, MONTH = DAY[:4], DAY[5:7]
OLD_DAY = f"heard_{DAY}.jsonl"
KEEPER = "dev.sessionkit.voice.ears-keeper"


# --- the fake machine and the fake HOMEs -------------------------------------------------------

class Machine:
    """launchctl and ps, faked. launchctl answers ``print`` only, and records every call."""

    def __init__(self):
        self.calls, self.loaded, self.domain, self.procs = [], set(), "", []

    def launchctl(self, *a):
        self.calls.append(a)
        assert a[0] == "print", a
        if a[1] == f"gui/{UID}":
            return 0, self.domain
        return (0, "") if a[1].rsplit("/", 1)[-1] in self.loaded else (113, "")


@pytest.fixture
def mk(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for v in ("SESSIONVOICE_HOME", "VOICEMODE_BASE_DIR", "XDG_STATE_HOME", "CLAUDE_CONFIG_DIR",
              "EARS_HOME", "EARS_SPEAKER_PY", *(it.env for it in H.ITEMS.values() if it.env)):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(M, "LOCK_RETRY_S", 0.05)
    m = Machine()
    lines = []

    def build(environ=None, **kw):
        kw.setdefault("installed_lines", lambda: list(lines))
        kw.setdefault("raise_nofile", lambda n: True)
        kw.setdefault("nofile_ok", lambda n: True)
        return M.Host(home=home, environ=dict(environ or {}), uid=UID, launchctl=m.launchctl,
                      processes=lambda: list(m.procs), **kw)

    return SimpleNamespace(home=home, m=m, host=build, new=home / H.NEW, lines=lines,
                           sv=home / SV, vm=home / VM)


def _write(p: Path, data, mode=None):
    p.parent.mkdir(parents=True, exist_ok=True)
    (p.write_bytes if isinstance(data, bytes) else p.write_text)(data)
    if mode is not None:
        os.chmod(p, mode)
    return p


def _lines(*seqs, day=DAY):
    return "".join(json.dumps({"ts": f"{day}T08:00:{i:02d}+10:00", "seq": i, "kind": "turn",
                               "text": f"line {i}", "v": 1}) + "\n" for i in seqs)


def voice_tree(r: Path):
    """voice's own tree under root R: the heard log, every lock kind, talk's secret, cursors."""
    lc = r / "logs" / "conversations"
    _write(lc / OLD_DAY, _lines(1, 2, 3), 0o600)
    _write(lc / "heard.lock", b"", 0o600)
    _write(lc / f"speakers_{DAY}.jsonl", '{"seq": 3, "speaker": "theo"}\n')
    (r / "talk").mkdir(parents=True)
    os.chmod(r / "talk", 0o700)
    _write(r / "talk" / "token", "s3cret", 0o600)
    (r / "state" / "missed-reply").mkdir(parents=True)
    os.chmod(r / "state" / "missed-reply", 0o700)
    _write(r / "state" / "missed-reply" / "w1.json", "{}", 0o600)
    _write(r / "state" / "heard-cursor" / "abc.lock", b"", 0o600)
    _write(r / "state" / "heard-cursor" / "abc", json.dumps({"seq": 2, "file": OLD_DAY, "offset": 0}))
    _write(r / "mouth" / "player.lock", b"")
    _write(r / "mouth" / "done" / "u1.wait", b"")
    for n in ("k.lock", "k.json.lock", ".one.lock"):
        _write(r / "mouth" / "splits" / n, b"")
    _write(r / "mouth" / "stop", "")
    _write(r / "audio" / DAY / "t1.wav", b"RIFF-turn")
    return r


def s_host(mk):
    return voice_tree(mk.sv)


def v_host(mk):
    """VoiceMode's root, with voice's tree in it and VoiceMode's own files beside."""
    vm = voice_tree(mk.vm)
    _write(vm / "voicemode.env", "VOICEMODE_X=1\n")
    _write(vm / "voices" / "v.json", "{}")
    _write(vm / "logs" / "conversations" / f"exchanges_{DAY}.jsonl", "{}\n")
    _write(vm / "audio" / YEAR / MONTH / "a.wav", b"RIFF-vm")
    os.symlink(f"{YEAR}/{MONTH}/a.wav", vm / "audio" / "latest.wav")
    return vm


def strays(mk):
    """Every stray at its old path; the speakers home with its venv beside the refs."""
    h = mk.home
    _write(h / ".local/state/ears/call-tap/LISTEN-OFF", "")
    _write(h / ".local/state/ears/call-ears/STOP", "")
    _write(h / ".local/state/audio/devices.json", '{"v": 1}')
    sp = h / ".local/share/ears/speakers"
    _write(sp / "refs" / "theo.npy", b"\x00ref", 0o600)
    _write(sp / "STOP", "")
    _write(sp / "venv" / "bin" / "python", "#!venv", 0o755)
    os.chmod(sp, 0o700)
    _write(h / ".cache/ears/ears-mail.state.json", json.dumps({"offsets": {}}))
    return sp


def tree(root: Path) -> dict:
    """Every node under ROOT: kind, mode and content (links by target, never followed)."""
    out = {}
    if not os.path.lexists(root):
        return out
    for dp, dirs, files in os.walk(root, followlinks=False):
        for n in sorted(dirs + files):
            p = Path(dp) / n
            st = os.lstat(p)
            rel = str(p.relative_to(root))
            if os.path.islink(p):
                out[rel] = ("l", os.readlink(p))
            elif p.is_dir():
                out[rel] = ("d", oct(st.st_mode & 0o7777))
            elif p.is_file():
                out[rel] = ("f", oct(st.st_mode & 0o7777), hashlib.sha256(p.read_bytes()).hexdigest())
            else:
                out[rel] = ("s",)
    return out


def run(h, *args):
    return M.run(h, dry_run="--dry-run" in args)


def refusals(doc):
    return [r["n"] for r in doc["refusals"]]


def _hold(path):
    fd = os.open(str(path), os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


# --- M1 the verb -------------------------------------------------------------------------------

def test_m1_the_dry_run_writes_nothing_and_prints_the_plan(mk, capsys):
    s_host(mk)
    strays(mk)
    before = tree(mk.home)
    assert M.main(["--dry-run"], h=mk.host()) == 0
    out = capsys.readouterr().out
    assert tree(mk.home) == before
    assert "dry run" in out and f"{mk.sv} -> {mk.new}" in out and "exit 0" in out
    assert M.main(["--dry-run", "--json"], h=mk.host()) == 0
    doc = json.loads(capsys.readouterr().out)
    assert tree(mk.home) == before
    assert doc["kind"] == M.OURS and doc["dry_run"] and doc["exit"] == 0
    assert {i["from"] for i in doc["items"]} >= {str(mk.sv), str(mk.home / ".cache/ears/ears-mail.state.json")}
    assert all({"from", "to", "bytes", "mode", "how"} <= set(i) for i in doc["items"])


def test_m1_a_bare_run_on_a_host_with_a_loaded_job_refuses_and_writes_nothing(mk, capsys):
    s_host(mk)
    mk.m.loaded.add(KEEPER)
    before = tree(mk.home)
    assert M.main([], h=mk.host()) == 1
    out = capsys.readouterr().out
    assert tree(mk.home) == before
    assert "plan:" in out and "refusal 2" in out and KEEPER in out


def test_m1_usage_and_help(mk, capsys):
    assert M.main(["--apply"], h=mk.host()) == 2
    assert "unknown argument" in capsys.readouterr().err
    assert M.main(["--help"], h=mk.host()) == 0
    assert "--dry-run" in capsys.readouterr().out


def test_m1_the_ears_cli_dispatches_the_verb(mk, monkeypatch):
    from ears import cli
    seen = {}
    monkeypatch.setattr(M, "main", lambda argv, prog: seen.setdefault("a", (argv, prog)) and 0)
    assert cli.main(["home", "migrate", "--dry-run"]) == 0
    assert seen["a"] == (["--dry-run"], "ears home migrate")


def test_m1_a_fresh_home_has_nothing_to_do(mk):
    code, doc = run(mk.host())
    assert code == 0 and "nothing to do" in " ".join(doc["notes"])
    assert not os.path.lexists(mk.new) and mk.m.calls == []


# --- M2 the refusals ---------------------------------------------------------------------------

@pytest.mark.parametrize("var", H.ENV)
def test_m2_refusal_1_a_root_override(mk, var):
    s_host(mk)
    before = tree(mk.home)
    code, doc = run(mk.host({var: str(mk.sv)}))
    assert (code, refusals(doc)) == (1, [1]) and tree(mk.home) == before


def test_m2_refusal_2_a_loaded_label_of_ours(mk):
    s_host(mk)
    mk.m.loaded.add("dev.sessionkit.voice.ears-mail")
    code, doc = run(mk.host())
    assert code == 1 and 2 in refusals(doc) and not os.path.lexists(mk.new)


@pytest.mark.parametrize("argv", [
    "/bin/bash /x/keeper/ears-keeper.sh default",
    "/x/.venv/bin/python -m ears.listen capture --device 0",
    "/bin/bash /x/keeper/call-ears 3",
    "/x/bin/call-tap",
    "/x/venv/bin/python /x/speakers/speaker-sidecar2.py",
    "/x/.venv/bin/python -m mouth serve",
    "/x/.venv/bin/python -m mouth mail",
    "/usr/bin/python3 /x/keeper/ears-mail.py --state /s",
    "/x/.venv/bin/talk serve --host 127.0.0.1 --port 8080",
])
def test_m2_refusal_2_a_writer_process(mk, argv):
    s_host(mk)
    mk.m.procs = [(4242, argv)]
    code, doc = run(mk.host())
    assert code == 1 and 2 in refusals(doc)
    assert "pid 4242" in " ".join(r["why"] for r in doc["refusals"])


def _record(mk, plugins, raw=None):
    rec = mk.home / ".claude" / "plugins" / "installed_plugins.json"
    _write(rec, raw if raw is not None else json.dumps({"version": 2, "plugins": plugins}))
    return rec


def _plugin(mk, name, marker=True, home_py=True):
    ip = mk.home / ".claude" / "plugins" / "cache" / name
    if home_py:
        text = (ROOT / "src/ears/home.py").read_text()
        _write(ip / "src/ears/home.py", text if marker else text.replace("ASKS_THE_PACKAGE = 1", ""))
    else:
        ip.mkdir(parents=True)
    return {"scope": "user", "installPath": str(ip), "version": "0.3.3"}


@pytest.mark.parametrize("case,refused", [
    ("asks", False), ("no-marker", True), ("no-home-py", True), ("unparseable", True),
    ("no-record", False), ("other-plugin-only", False)])
def test_m2_refusal_3_the_plugin_and_the_package_disagree(mk, case, refused):
    s_host(mk)
    if case == "asks":
        _record(mk, {"sessionvoice@mkt": [_plugin(mk, "a")]})
    elif case == "no-marker":
        _record(mk, {"sessionvoice@mkt": [_plugin(mk, "a"), _plugin(mk, "b", marker=False)]})
    elif case == "no-home-py":
        _record(mk, {"sessionvoice@mkt": [_plugin(mk, "a", home_py=False)]})
    elif case == "unparseable":
        _record(mk, None, raw="{not json")
    elif case == "other-plugin-only":
        _record(mk, {"voicemode@voicemode": [_plugin(mk, "vm", marker=False)]})
    code, doc = run(mk.host(), "--dry-run")
    assert (3 in refusals(doc)) is refused and code == (1 if refused else 0)
    if case == "no-record":
        assert "no plugin install record" in " ".join(doc["notes"])


def test_m2_refusal_3_reads_the_record_under_claude_config_dir(mk, tmp_path):
    s_host(mk)
    cfg = tmp_path / "cfg"
    ip = _plugin(mk, "old", marker=False)
    _write(cfg / "plugins" / "installed_plugins.json",
           json.dumps({"version": 2, "plugins": {"sessionvoice@m": [ip]}}))
    _, doc = run(mk.host({"CLAUDE_CONFIG_DIR": str(cfg)}), "--dry-run")
    assert 3 in refusals(doc)


def test_m2_refusal_4_a_new_root_that_is_not_empty_and_has_no_manifest(mk):
    s_host(mk)
    _write(mk.new / "seats" / "ivy" / "heard.state", "{}")
    code, doc = run(mk.host())
    assert code == 1 and 4 in refusals(doc)
    assert "stranded" not in json.dumps(doc)
    _write(mk.new / "logs" / "conversations" / OLD_DAY, _lines(9))
    _, doc = run(mk.host())
    assert "stranded" in json.dumps(doc)


def test_m2_an_empty_new_root_made_ahead_of_time_is_replaced(mk):
    s_host(mk)
    mk.new.mkdir(parents=True)
    code, doc = run(mk.host())
    assert code == 0 and (mk.new / M.MANIFEST).is_file()


def test_m2_refusal_5_a_stray_in_two_places(mk):
    s_host(mk)
    strays(mk)
    _write(mk.sv / "call-tap" / "STOP", "")
    code, doc = run(mk.host())
    assert code == 1 and 5 in refusals(doc)


def test_m2_refusal_6_another_filesystem(mk):
    s_host(mk)
    code, doc = run(mk.host(dev=lambda p: 7 if SV in str(p) else 1))
    assert code == 1 and 6 in refusals(doc)


def test_m2_refusal_7_a_volume_that_cannot_swap(mk):
    s_host(mk)
    code, doc = run(mk.host(can_swap=lambda p: False))
    assert code == 1 and 7 in refusals(doc)


def test_m2_the_real_volume_check_answers_on_tmp(tmp_path):
    assert M._can_swap(tmp_path) is True


def test_m2_refusal_8_a_full_disk(mk):
    s_host(mk)
    code, doc = run(mk.host(free=lambda p: 1000))
    assert code == 1 and 8 in refusals(doc)


@pytest.mark.parametrize("which", ["heard", "player", "session"])
def test_m2_refusal_9_a_busy_lock(mk, which):
    s_host(mk)
    f = {"heard": mk.sv / "logs/conversations/heard.lock", "player": mk.sv / "mouth/player.lock",
         "session": mk.sv / "state/heard-cursor/abc.lock"}[which]
    fd = _hold(f)
    try:
        before = tree(mk.home)
        code, doc = run(mk.host(), "--dry-run")
        assert code == 0 and any(str(f) in x for x in doc["probes"]) and tree(mk.home) == before
        code, doc = run(mk.host())
        assert code == 1 and refusals(doc) == [9] and str(f) in doc["refusals"][0]["why"]
        assert tree(mk.home) == before
    finally:
        os.close(fd)


def test_m2_the_dry_run_takes_no_lock(mk):
    s_host(mk)
    lock = mk.sv / "logs/conversations/heard.lock"
    seen = []

    def pause(stage, e=None):
        seen.append(stage)
    run(mk.host(pause=pause), "--dry-run")
    fd = os.open(str(lock), os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)   # free: the dry run kept nothing
    finally:
        os.close(fd)
    assert seen == []


def test_m2_refusal_9_an_open_file_limit_that_cannot_be_raised(mk):
    s_host(mk)
    code, doc = run(mk.host(raise_nofile=lambda n: False))
    assert code == 1 and refusals(doc) == [9]
    _, doc = run(mk.host(nofile_ok=lambda n: False), "--dry-run")
    assert any("open-file limit" in x for x in doc["probes"])


GUEST_FIXTURES = {
    "capture": ("proc", "/x/python -m voice_mode.listen capture"),
    "serve": ("proc", "/x/python -m voice_mode.mouth serve"),
    "mail": ("proc", "/x/python -m voice_mode.mouth mail"),
    "legacy-loaded-com": ("loaded", "com.failmode.mouth-mail"),
    "legacy-loaded-dev": ("loaded", "dev.voicemode.mouth-mail"),
    "legacy-plist": ("plist", "com.failmode.mouth-mail"),
    "settings": ("settings", None),
    "settings-config-dir": ("config", None),
    "phone-label": ("domain", "dev.sessionkit.phone.bot.ivy"),
    "phone-old-label": ("domain", "com.sessionphone.bot.ivy"),
    "phone-proc": ("proc", "/x/.venv/bin/sessionphone bot run ivy"),
}


def _guest(mk, tmp_path, kind, what):
    env = {}
    if kind == "proc":
        mk.m.procs = [(77, what)]
    elif kind == "loaded":
        mk.m.loaded.add(what)
    elif kind == "plist":
        _write(mk.home / "Library/LaunchAgents" / f"{what}.plist", "<plist/>")
    elif kind == "settings":
        _write(mk.home / ".claude/settings.json", json.dumps({"hooks": {"PostToolUse": [{"hooks": [
            {"type": "command", "command": f"{mk.vm}/bin/voicemode-heard-hook"}]}]}}))
    elif kind == "config":
        cfg = tmp_path / "cfg"
        _write(cfg / "settings.json", json.dumps({"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": "python3 /y/voicemode-heard-hook"}]}]}}))
        env["CLAUDE_CONFIG_DIR"] = str(cfg)
    elif kind == "domain":
        mk.m.domain = f"gui/{UID} = {{\n\tservices = {{\n\t\t0 - {what}\n\t}}\n}}\n"
    return env


@pytest.mark.parametrize("case", sorted(GUEST_FIXTURES))
def test_m2_refusal_10_a_guest_copy_of_the_rule_on_voicemodes_root(mk, tmp_path, case):
    v_host(mk)
    env = _guest(mk, tmp_path, *GUEST_FIXTURES[case])
    before = tree(mk.home)
    code, doc = run(mk.host(env))
    assert code == 1 and 10 in refusals(doc) and tree(mk.home) == before


@pytest.mark.parametrize("case", sorted(GUEST_FIXTURES))
def test_m2_refusal_10_never_refuses_on_our_own_old_root(mk, tmp_path, case):
    s_host(mk)
    env = _guest(mk, tmp_path, *GUEST_FIXTURES[case])
    code, doc = run(mk.host(env), "--dry-run")
    assert 10 not in refusals(doc)


def test_m2_voicemodes_mcp_server_alone_does_not_refuse(mk):
    v_host(mk)
    mk.m.procs = [(5, "/x/python -m voice_mode.server"), (6, "/x/bin/voicemode-mcp-launcher")]
    code, doc = run(mk.host(), "--dry-run")
    assert code == 0 and refusals(doc) == []


def test_m2_a_legacy_plist_moved_aside_by_install_mouth_no_longer_refuses(mk, monkeypatch):
    """design-002 final review F1: the plist alone refuses (launchd loads it at the next login),
    and once `ears services install mouth` has moved it aside (_replace_legacy) it does not."""
    from ears import services
    v_host(mk)
    agents = mk.home / "Library/LaunchAgents"
    plist = _write(agents / "com.failmode.mouth-mail.plist", "<plist/>")
    _, doc = run(mk.host(), "--dry-run")
    why = " ".join(r["why"] for r in doc["refusals"] if r["n"] == 10)
    assert "com.failmode.mouth-mail" in why and "ears services install mouth" in why
    assert "move" in why and str(agents) in why
    monkeypatch.setattr(services, "AGENTS", agents)
    monkeypatch.setattr(services, "_launchctl",
                        lambda *a: subprocess.CompletedProcess(a, 113 if a[0] == "print" else 0, "", ""))
    gone, lines = services._replace_legacy(UID, "mouth")
    assert not plist.exists() and any(p.name.startswith(plist.name + ".replaced-") for p in agents.iterdir())
    code, doc = run(mk.host(), "--dry-run")
    assert code == 0 and 10 not in refusals(doc)


def test_m2_the_hook_file_alone_only_warns(mk):
    v_host(mk)
    _write(mk.vm / "bin" / "voicemode-heard-hook", "#!python\n")
    code, doc = run(mk.host(), "--dry-run")
    assert code == 0 and refusals(doc) == []
    warn = " ".join(doc["warnings"])
    assert "voicemode-heard-hook" in warn and "Project-scope" in warn


# --- M3 an S host ------------------------------------------------------------------------------

def test_m3_an_s_host_copies_verifies_publishes_and_swaps(mk):
    s_host(mk)
    strays(mk)
    mail = mk.home / ".cache/ears/ears-mail.state.json"
    mail_bytes = mail.read_bytes()
    before = tree(mk.sv)
    code, doc = run(mk.host())
    assert code == 0, doc
    assert os.path.islink(mk.sv) and os.readlink(mk.sv) == str(mk.new)
    pre = mk.home / (SV + M.SUFFIX)
    assert tree(pre) == before                         # the original's bytes and modes
    assert (mk.new / "talk").stat().st_mode & 0o777 == 0o700
    assert (mk.new / "talk/token").stat().st_mode & 0o777 == 0o600
    assert (mk.new / "state/missed-reply").stat().st_mode & 0o777 == 0o700
    assert (mk.new / "state/missed-reply/w1.json").stat().st_mode & 0o777 == 0o600
    assert H.state_source({}, mk.home) == (mk.new, "new")
    for name, it in H.ITEMS.items():
        assert H.state_path(name, {}, mk.home) == mk.new / it.name, name
    assert (mk.new / "ears-mail.state.json").read_bytes() == mail_bytes
    assert H.split(mk.home) is None
    man = json.loads((mk.new / M.MANIFEST).read_text())
    assert man["kind"] == M.OURS and man["old"] == str(mk.sv)


# --- M4 a V host -------------------------------------------------------------------------------

def test_m4_a_v_host_moves_only_voices_entries(mk):
    v_host(mk)
    own = ["voicemode.env", "voices", f"logs/conversations/exchanges_{DAY}.jsonl",
           f"audio/{YEAR}", "audio/latest.wav"]
    before = {k: v for k, v in tree(mk.vm).items() if any(k == o or k.startswith(o + "/") for o in own)}
    code, doc = run(mk.host())
    assert code == 0, doc
    after = tree(mk.vm)
    assert {k: after[k] for k in before} == before
    lc = mk.vm / "logs/conversations"
    assert lc.is_dir() and not lc.is_symlink()
    for n in (OLD_DAY, "heard.lock", f"speakers_{DAY}.jsonl"):
        assert os.path.islink(lc / n) and os.readlink(lc / n) == str(mk.new / "logs/conversations" / n)
        assert (lc / (n + M.SUFFIX)).is_file()
    assert os.path.islink(mk.vm / "audio" / DAY) and (mk.vm / "audio" / YEAR).is_dir()
    for n in ("mouth", "talk", "state"):
        assert os.path.islink(mk.vm / n)
    assert not (mk.new / "voicemode.env").exists() and not (mk.new / "audio" / YEAR).exists()
    assert not H.populated(mk.vm)
    assert H.state_source({}, mk.home) == (mk.new, "new") and H.split(mk.home) is None


def test_m4_a_heard_lock_the_hold_makes_on_a_v_host_moves_too(mk):
    """Heard files with no heard.lock yet. The hold makes it in VoiceMode's
    folder after the plan was listed; it is voice's, so it is swapped too. Left behind, a
    lone real heard.lock keeps ~/.voicemode POPULATED (a false split), the new root has
    none, and a re-run finds nothing to do, so nothing ever cures it."""
    v_host(mk)
    lc = mk.vm / "logs/conversations"
    os.unlink(lc / "heard.lock")
    code, doc = run(mk.host())
    assert code == 0, doc
    assert os.path.islink(lc / "heard.lock")
    assert os.path.samefile(mk.new / "logs/conversations/heard.lock", lc / ("heard.lock" + M.SUFFIX))
    assert not H.populated(mk.vm) and H.split(mk.home) is None
    assert run(mk.host())[0] == 0                       # and a second run is still a no-op


# --- the speakers item -------------------------------------------------------------------------

def test_m5_the_speakers_item_moves_entry_by_entry_and_the_venv_stays(mk):
    from ears import services
    s_host(mk)
    sp = strays(mk)
    venv = tree(sp / "venv")
    py = services.speaker_py()
    code, doc = run(mk.host())
    assert code == 0, doc
    assert services.speaker_py() == py == mk.home / H.SPEAKERS_VENV / "bin/python"
    assert tree(sp / "venv") == venv and not (sp / "venv").is_symlink()
    assert not (mk.new / "speakers/venv").exists()
    aside = sp.with_name("speakers" + M.SUFFIX)
    assert {p.name for p in aside.iterdir()} == {"refs", "STOP"}
    assert (aside / "refs/theo.npy").read_bytes() == b"\x00ref"
    assert (mk.new / "speakers").stat().st_mode & 0o777 == 0o700
    assert (mk.new / "speakers/refs/theo.npy").stat().st_mode & 0o777 == 0o600
    assert os.path.islink(sp / "refs") and os.path.islink(sp / "STOP")
    assert H.item_source("speakers", {}, mk.home)[0] == mk.new / "speakers"


# --- M6 special files --------------------------------------------------------------------------

def test_m6_a_stale_socket_is_skipped_and_named(mk, monkeypatch):
    s_host(mk)
    monkeypatch.chdir(mk.sv / "talk")
    s = socket.socket(socket.AF_UNIX)
    s.bind("talk.sock")          # relative: a tmp path is longer than a socket path may be
    s.close()
    code, doc = run(mk.host())
    assert code == 0
    assert not os.path.lexists(mk.new / "talk/talk.sock")
    assert any("talk.sock" in x for x in doc["skipped"])


# --- M7 the locks ------------------------------------------------------------------------------

LOCK_FILES = ("logs/conversations/heard.lock", "state/heard-cursor/abc.lock", "mouth/player.lock",
              "mouth/done/u1.wait", "mouth/splits/k.lock", "mouth/splits/k.json.lock",
              "mouth/splits/.one.lock")


@pytest.mark.parametrize("kind", ["s", "v"])
def test_m7_every_lock_file_is_one_inode_on_both_sides(mk, kind):
    old = s_host(mk) if kind == "s" else v_host(mk)
    code, _ = run(mk.host())
    assert code == 0
    pre = mk.home / (SV + M.SUFFIX) if kind == "s" else None
    for rel in LOCK_FILES:
        new = mk.new / rel
        orig = pre / rel if pre else None
        if orig is None:   # V: each heard item swapped by itself, the folders as one item
            top = rel.split("/")[0]
            orig = (mk.vm / "logs/conversations" / ("heard.lock" + M.SUFFIX)) if top == "logs" \
                else mk.vm / (top + M.SUFFIX) / rel.split("/", 1)[1]
        assert os.path.samefile(new, orig), rel
        assert new.stat().st_nlink >= 2


def test_m7_writers_across_the_swap_take_different_seqs_and_hooks_and_players_are_held_off(mk):
    s_host(mk)
    out, threads, held = {}, [], {}

    def write(tag):
        out[tag] = heard.append("turn", text=tag)["seq"]

    def pause(stage, e=None):
        if stage == "published":
            t = threading.Thread(target=write, args=("blocked",))
            t.start()
            threads.append(t)
            time.sleep(0.3)
            assert "blocked" not in out                    # it waits on the one heard.lock
        if stage == "swapped":
            with heard._SessionLock("abc", 0.1, mk.new / "state/heard-cursor") as got:
                held["hook"] = got
            from mouth import _player_running
            held["player"] = _player_running(mk.new / "mouth")
    code, _ = run(mk.host(pause=pause))
    assert code == 0
    for t in threads:
        t.join(5)
    write("after")
    assert out["blocked"] != out["after"] and {out["blocked"], out["after"]} == {4, 5}
    assert held == {"hook": False, "player": True}


def test_m7_every_lock_site_under_the_root_is_named(mk):
    """A flock site in voice's code under the root that LOCKS does not name fails here, as
    test_every_copy_is_listed fails on a copy of the rule."""
    named = {   # file -> the LOCKS patterns its flock sites take; [] for a lock outside the root
        "src/ears/heard.py": ["logs/conversations/heard.lock", "state/heard-cursor/*.lock"],
        "src/ears/listen/watchdog.py": ["logs/conversations/heard.lock"],
        "src/mouth/__init__.py": ["mouth/player.lock", "mouth/done/*.wait"],
        "src/mouth/notice.py": ["mouth/done/*.wait"],
        "src/mouth/player.py": ["mouth/done/*.wait", "mouth/player.lock"],
        "src/mouth/split.py": ["mouth/splits/*.lock"],
        "src/ears/wake.py": [],     # the wake file's lock, in the settings folder
        "src/ears/migrate.py": [],  # the run's own holds, on the files above
    }
    found = set()
    for d in ("src", "bin", "keeper", "skills/ears/scripts", "speakers", "view", "shadow"):
        for p in (ROOT / d).rglob("*"):
            if p.is_file() and not p.is_symlink() and "__pycache__" not in p.parts:
                try:
                    if re.search(r"\b(flock|lockf)\(", p.read_text()):
                        found.add(p.relative_to(ROOT).as_posix())
                except UnicodeDecodeError:
                    continue
    assert found == set(named)
    for pats in named.values():
        assert set(pats) <= set(M.LOCKS)
    assert set(M.LOCKS) == {p for ps in named.values() for p in ps}
    for rel in LOCK_FILES:
        assert M._is_lock(rel), rel
    assert not M._is_lock("logs/conversations/" + OLD_DAY)


# --- M8 one exchange ---------------------------------------------------------------------------

def test_m8_the_old_path_is_present_at_every_step(mk):
    s_host(mk)
    seen = []

    def pause(stage, e=None):
        seen.append(stage)
        assert os.path.lexists(mk.sv) and (mk.sv / "talk/token").read_text() == "s3cret"
    code, _ = run(mk.host(pause=pause))
    assert code == 0 and seen[:2] == ["copied", "published"] and "swapped" in seen


def test_m8_the_swap_is_one_exchange_and_no_rename_touches_an_old_path(mk, monkeypatch):
    """A rename followed by a link leaves a moment with no old path: after the publish, no
    rename may name an old path or its .pre-sessionkit name; the exchange is the only move."""
    s_host(mk)
    strays(mk)
    published, renames, swaps = [], [], []
    real_rename = os.rename

    def rename(a, b, *k, **kw):
        if published:
            renames.append((str(a), str(b)))
        return real_rename(a, b, *k, **kw)

    def swap(a, b):
        swaps.append((str(a), str(b)))
        M._swap(a, b)
    monkeypatch.setattr(os, "rename", rename)
    code, doc = run(mk.host(swap=swap, pause=lambda st, e=None: st == "published" and published.append(1)))
    assert code == 0 and renames == []
    assert len(swaps) == len(doc["result"]["swapped"]) == len(doc["items"])


def test_m8_the_real_exchange_swaps_a_folder_and_a_file_with_links(tmp_path):
    d, f = tmp_path / "d", tmp_path / "f"
    _write(d / "x", "folder")
    _write(f, "file")
    for orig, new in ((d, tmp_path / "nd"), (f, tmp_path / "nf")):
        link = orig.with_name(orig.name + M.SUFFIX)
        os.symlink(str(new), link)
        M._swap(link, orig)
        assert os.readlink(orig) == str(new) and not link.is_symlink() and link.exists()
    assert (tmp_path / ("d" + M.SUFFIX) / "x").read_text() == "folder"
    assert (tmp_path / ("f" + M.SUFFIX)).read_text() == "file"
    with pytest.raises(OSError):
        M._swap(tmp_path / "missing", f)


def test_m8_the_old_heard_file_is_present_at_every_step_on_v(mk):
    v_host(mk)
    f = mk.vm / "logs/conversations" / OLD_DAY

    def pause(stage, e=None):
        assert os.path.lexists(f) and f.read_text().startswith('{"ts"')
    assert run(mk.host(pause=pause))[0] == 0


def _failing_swap(match):
    def swap(a, b):
        if match(Path(b)):
            raise OSError(45, "Operation not supported", str(a))
        M._swap(a, b)
    return swap


def test_m8_a_failed_exchange_on_s_leaves_the_old_root_answering(mk):
    s_host(mk)
    strays(mk)
    code, doc = run(mk.host(swap=_failing_swap(lambda b: b == mk.sv)))
    assert code == 3 and any(str(mk.sv) in x for x in doc["result"]["failed"])
    assert mk.sv.is_dir() and not mk.sv.is_symlink()
    assert H.state_source({}, mk.home) == (mk.sv, "old")
    code, doc = run(mk.host())
    assert code == 0 and os.path.islink(mk.sv) and H.state_source({}, mk.home) == (mk.new, "new")


def test_m8_a_failed_exchange_of_a_stray_leaves_it_answering_its_old_path(mk):
    s_host(mk)
    strays(mk)
    old = mk.home / ".local/state/ears/call-ears"
    code, _ = run(mk.host(swap=_failing_swap(lambda b: b == old)))
    assert code == 3 and old.is_dir() and not old.is_symlink()
    assert H.state_path("call-ears", {}, mk.home) == old
    assert run(mk.host())[0] == 0 and H.state_path("call-ears", {}, mk.home) == mk.new / "call-ears"


def test_m8_a_failed_exchange_on_v_answers_the_new_copy_and_a_rerun_swaps_it(mk):
    v_host(mk)
    f = mk.vm / "logs/conversations" / OLD_DAY
    code, _ = run(mk.host(swap=_failing_swap(lambda b: b == f)))
    assert code == 3 and f.is_file() and not f.is_symlink()
    assert H.state_dir({}, mk.home) == mk.new
    assert (mk.new / "logs/conversations" / OLD_DAY).read_bytes() == f.read_bytes()
    assert run(mk.host())[0] == 0 and os.path.islink(f)


# --- M9 the switch on a V host -----------------------------------------------------------------

def test_m9_appends_around_the_publish_land_in_the_new_root(mk):
    """One append resolved the old folder before the publish (it waits on heard.lock), one
    started after it. Both land in the new root: the first looks again under the lock. Today's
    file does not exist before, so an append into the old folder would make it there, real."""
    v_host(mk)
    out, threads = {}, []
    today = heard.log_path().name

    def write(tag):
        out[tag] = heard.append("turn", text=tag)

    def start(tag):
        t = threading.Thread(target=write, args=(tag,))
        t.start()
        threads.append(t)
        time.sleep(0.3)

    def pause(stage, e=None):
        if stage == "copied":
            assert heard.log_dir() == mk.vm / "logs/conversations"
            start("before")
        elif stage == "published":
            start("between")
    code, _ = run(mk.host(pause=pause))
    assert code == 0
    for t in threads:
        t.join(5)
    assert set(out) == {"before", "between"}
    assert not os.path.lexists(mk.vm / "logs/conversations" / today)
    rows = [json.loads(x) for x in (mk.new / "logs/conversations" / today).read_text().splitlines()]
    assert sorted(r["text"] for r in rows) == ["before", "between"]
    assert len({r["seq"] for r in rows}) == 2 and min(r["seq"] for r in rows) == 4


def test_heard_append_looks_again_under_the_lock(tmp_path, monkeypatch):
    """The unit of M9: the folder changes while append waits for the lock."""
    a, b = tmp_path / "a", tmp_path / "b"
    import itertools
    seq = itertools.chain([a], itertools.repeat(b))
    monkeypatch.setattr(heard, "log_dir", lambda: next(seq))
    rec = heard.append("event", event="heartbeat")
    assert not (a / heard.log_path().name).exists() and (b / heard.log_path().name).exists()
    assert rec["seq"] == 1
    d = tmp_path / "given"
    monkeypatch.setattr(heard, "log_dir", lambda: (_ for _ in ()).throw(AssertionError("asked")))
    heard.append("event", event="heartbeat", directory=d)   # a given folder is not looked up


# --- M10 the sweep -----------------------------------------------------------------------------

def test_m10_what_arrives_during_the_run_is_carried_and_nothing_is_overwritten(mk):
    s_host(mk)

    def pause(stage, e=None):
        if stage == "published":
            _write(mk.sv / "state/heard-cursor/new.lock", b"")
            _write(mk.sv / "state/heard-cursor/new", '{"seq": 3}')
            _write(mk.sv / "mouth/inbox/line-1.json", '{"text": "hi"}')
            _write(mk.sv / "mouth/clash.txt", "old side")
            _write(mk.new / "mouth/clash.txt", "new side")
    code, doc = run(mk.host(pause=pause))
    assert code == 0, doc
    pre = mk.home / (SV + M.SUFFIX)
    assert os.path.samefile(mk.new / "state/heard-cursor/new.lock", pre / "state/heard-cursor/new.lock")
    assert (mk.new / "state/heard-cursor/new").read_text() == '{"seq": 3}'
    assert (mk.new / "mouth/inbox/line-1.json").read_text() == '{"text": "hi"}'
    assert (mk.new / "mouth/clash.txt").read_text() == "new side"
    assert (pre / "mouth/clash.txt").read_text() == "old side"
    carried = " ".join(doc["result"]["carried"])
    assert "new.lock" in carried and "line-1.json" in carried and "hard-linked" in carried
    assert any("clash.txt" in x for x in doc["result"]["left"])
    assert run(mk.host())[0] == 0                       # carried once: a re-run has nothing to do


def test_m10_a_file_changed_after_the_verify_is_named_and_kept(mk):
    s_host(mk)

    def pause(stage, e=None):
        if stage == "linked":
            (mk.sv / "mouth/stop").write_text("stop")   # a `mouth stop`, through the old path
    code, doc = run(mk.host(pause=pause))
    assert code == 3 and any("mouth/stop" in x for x in doc["result"]["changed"])
    code, doc = run(mk.host())
    assert code == 3 and any("mouth/stop" in x for x in doc["result"]["changed"])


# --- M11 the hook ------------------------------------------------------------------------------

def test_m11_a_hook_held_off_during_the_run_catches_up_after(mk):
    s_host(mk)
    assert heard.take("sess1")[0] == ""                    # the cursor starts at the end
    heard.append("turn", text="before the run")
    during = {}

    def pause(stage, e=None):
        if stage == "published":
            during["take"] = heard.take("sess1", lock_timeout=0.1)
    code, _ = run(mk.host(pause=pause))
    assert code == 0
    assert during["take"] == ("", None)
    text, seq = heard.take("sess1")
    assert "before the run" in text and seq == 4


# --- M12 the offsets ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["s", "v"])
def test_m12_a_saved_offset_is_found_again_by_samefile(mk, kind):
    old = s_host(mk) if kind == "s" else v_host(mk)
    old_key = str(old / "logs/conversations" / OLD_DAY)
    gone = str(mk.home / "gone" / OLD_DAY)
    assert run(mk.host())[0] == 0
    p = str(mk.new / "logs/conversations" / OLD_DAY)
    size = os.path.getsize(p)
    assert H.saved_offset({gone: 1, old_key: 5}, p, size) == 5
    assert H.saved_offset({p: 7, old_key: 5}, p, size) == 7      # its own key first
    assert H.saved_offset({gone: 1}, p, size) == size              # a gone key: the end
    assert H.saved_offset({old_key: size + 1}, p, size) == size    # past the end: the end
    assert H.saved_offset({}, p, size) == size


def test_m12_both_readers_resume_by_the_lookup():
    for script in ("bin/heard-first", "keeper/ears-mail.py"):
        text = (ROOT / script).read_text()
        assert "voice_home().saved_offset(saved, p, size)" in text, script
        assert "saved[p] <= size" not in text, script


@pytest.mark.parametrize("kind", ["s", "v"])
def test_m12_heard_first_resumes_at_its_place_after_a_migrate(mk, kind, tmp_path):
    old = s_host(mk) if kind == "s" else v_host(mk)
    seat = _write(mk.home / ".ivy" / "heard.state", "{}")
    lc = old / "logs/conversations"
    day = lc / OLD_DAY
    at = day.stat().st_size
    now = datetime.now().astimezone().isoformat()
    with open(day, "a") as f:
        f.write(json.dumps({"ts": now, "seq": 4, "kind": "turn", "text": "after the place",
                            "source": "mic"}) + "\n")
    state = tmp_path / "heard.state"
    seat_before = tree(mk.home / ".ivy")
    assert run(mk.host())[0] == 0
    assert tree(mk.home / ".ivy") == seat_before and seat.read_text() == "{}"
    env = {"HOME": str(mk.home), "PATH": "/usr/bin:/bin"}
    state.write_text(json.dumps({"offsets": {str(day): at}}))
    p = subprocess.run([sys.executable, str(ROOT / "bin/heard-first"), "--rule", "turn",
                        "--state", str(state), "--timeout", "3"],
                       capture_output=True, text=True, timeout=30, env=env)
    assert p.returncode == 0 and "after the place" in p.stdout, p.stdout + p.stderr
    state.write_text(json.dumps({"offsets": {str(mk.home / "gone" / OLD_DAY): at}}))
    p = subprocess.run([sys.executable, str(ROOT / "bin/heard-first"), "--rule", "turn",
                        "--state", str(state), "--timeout", "1"],
                       capture_output=True, text=True, timeout=30, env=env)
    assert p.returncode == 124, p.stdout + p.stderr     # a gone key: from the end, no replay


# --- M13 re-runs and stops ---------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["s", "v"])
def test_m13_a_second_run_is_a_no_op(mk, kind):
    (s_host if kind == "s" else v_host)(mk)
    strays(mk)
    assert run(mk.host())[0] == 0
    before = tree(mk.home)
    mk.m.loaded.add(KEEPER)                   # a host back at work: still nothing to do
    code, doc = run(mk.host())
    assert code == 0 and "nothing to do" in " ".join(doc["notes"]) and tree(mk.home) == before


def test_m13_a_staging_folder_left_by_a_stop_is_renamed_aside(mk):
    s_host(mk)
    stale = _write(mk.new.parent / M.STAGING / "half.txt", "half")
    code, doc = run(mk.host())
    assert code == 0
    aside = [p for p in mk.new.parent.iterdir() if p.name.startswith(M.FAILED)]
    assert len(aside) == 1 and (aside[0] / "half.txt").read_text() == "half"
    assert not stale.exists() and any("renamed aside" in x for x in doc["notes"])


def test_m13_a_stop_at_the_verify_publishes_nothing(mk):
    s_host(mk)

    def pause(stage, e=None):
        if stage == "copied":
            (mk.sv / "talk/token").write_text("changed")
    code, doc = run(mk.host(pause=pause))
    assert code == 3 and "verify" in doc["result"]["stopped"]
    assert not os.path.lexists(mk.new) and (mk.new.parent / M.STAGING).is_dir()
    assert mk.sv.is_dir() and not mk.sv.is_symlink()


def test_m13_a_stop_after_the_publish_rerun_swaps_the_rest_and_refuses_a_changed_one(mk):
    v_host(mk)
    lc = mk.vm / "logs/conversations"
    code, _ = run(mk.host(swap=_failing_swap(lambda b: True)))
    assert code == 3 and (mk.new / M.MANIFEST).is_file()
    (mk.vm / "talk/token").write_text("a writer ran")
    code, doc = run(mk.host())
    assert code == 3
    assert any(str(mk.vm / "talk") in x for x in doc["result"]["failed"])
    assert not os.path.islink(mk.vm / "talk") and os.path.islink(lc / OLD_DAY)
    assert os.path.islink(mk.vm / "mouth")


def test_m13_a_pre_link_naming_the_new_place_is_reused_and_any_other_refused(mk):
    s_host(mk)
    pre = mk.home / (SV + M.SUFFIX)
    code, _ = run(mk.host(swap=_failing_swap(lambda b: b == mk.sv)))
    assert code == 3 and os.path.islink(pre) and os.readlink(pre) == str(mk.new)
    assert run(mk.host())[0] == 0 and os.path.islink(mk.sv) and pre.is_dir()



def test_m13_a_pre_link_naming_anything_else_is_refused(mk, tmp_path):
    s_host(mk)
    pre = mk.home / (SV + M.SUFFIX)
    code, _ = run(mk.host(swap=_failing_swap(lambda b: b == mk.sv)))
    assert code == 3
    os.rename(pre, tmp_path / "moved-link")              # a test's hand, standing in for a human
    os.symlink(str(tmp_path / "elsewhere"), pre)
    code, doc = run(mk.host())
    assert code == 3 and any("not a link to" in x for x in doc["result"]["failed"])
    assert mk.sv.is_dir() and not mk.sv.is_symlink()


# --- M14 the report ----------------------------------------------------------------------------

def _plist(path, env=None, args=None):
    import plistlib
    from ears import services
    d = {"Label": path.stem, "ProgramArguments": args or ["/x"],
         "EnvironmentVariables": {services.MARKER[0]: services.MARKER[1], **(env or {})}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(d))


def test_m14_installed_plists_naming_an_old_path_warn_in_the_plan_and_the_result(mk, monkeypatch):
    from ears import services
    s_host(mk)
    sp = strays(mk)
    agents = mk.home / "Library/LaunchAgents"
    monkeypatch.setattr(services, "AGENTS", agents)
    old_mail = mk.home / ".cache/ears/ears-mail.state.json"
    _plist(agents / "dev.sessionkit.voice.ears-mail.plist", args=["/py", "ears-mail.py", "--state", str(old_mail)])
    _plist(agents / "dev.sessionkit.voice.ears-speakers.plist", env={"SPEAKER_HOME": str(sp)})
    _, doc = run(mk.host(), "--dry-run")
    warn = " ".join(doc["warnings"])
    assert str(old_mail) in warn and str(sp) in warn and "ears services install" in warn
    code, doc = run(mk.host(installed_lines=None))
    assert code == 0
    after = "\n".join(doc["result"]["after"])
    assert str(old_mail) in after and str(sp) in after and "--only mail" in after


@pytest.mark.parametrize("kind", ["s", "v"])
def test_m14_live_readers_are_listed_with_their_rearm_line(mk, kind):
    (s_host if kind == "s" else v_host)(mk)
    mk.m.procs = [(11, "/usr/bin/python3 /p/bin/heard-first --rule turn"),
                  (12, "/usr/bin/python3 /p/keeper/partial-wake.py"),
                  (13, "/bin/bash /p/skills/ears/scripts/ears-mid-turn"),
                  (14, "/bin/bash /p/skills/ears/scripts/ears-mouth-tail"),
                  (15, "/usr/bin/python3 /p/keeper/heard-tail.py")]
    run_procs = mk.m.procs
    mk.m.procs = []                                  # no reader runs at the preflight's look...

    def pause(stage, e=None):
        mk.m.procs = run_procs                       # ...they are listed in the report
    code, doc = run(mk.host(pause=pause))
    assert code == 0
    after = doc["result"]["after"]
    by = {pid: next(x for x in after if f"pid {pid}:" in x) for pid in (11, 12, 13, 14, 15)}
    for pid in (12, 13, 14):
        assert "re-arm it NOW" in by[pid]
    assert ("before the next local midnight" in by[11]) is (kind == "v")
    assert ("need not" in by[11]) is (kind == "s")
    assert "follows the name" in by[15]
    assert any(x.startswith("2. Re-arm every heard-first") for x in after) is (kind == "v")
    first = [x[:2] for x in after if re.match(r"^\d\. ", x)]
    assert first == sorted(first)                    # the order it must be done in


def test_m14_a_real_shared_seat_home_and_the_originals_are_named(mk):
    s_host(mk)
    _write(mk.home / H.SHARED_OLD / "heard.state", "{}")
    code, doc = run(mk.host())
    after = "\n".join(doc["result"]["after"])
    assert str(mk.home / H.SHARED_OLD) in after and "not moved" in after
    assert str(mk.home / (SV + M.SUFFIX)) in after
    assert doc["result"]["originals"][0]["bytes"] > 0
    assert (mk.home / H.SHARED_OLD / "heard.state").read_text() == "{}"


def test_m14_on_voicemodes_root_the_guest_cure_is_repeated_with_the_hook_file(mk):
    v_host(mk)
    _write(mk.vm / "bin" / "voicemode-heard-hook", "#!python\n")
    code, doc = run(mk.host())
    assert code == 0
    nine = next(x for x in doc["result"]["after"] if x.startswith("9. "))
    assert "DC_HEARD_PY" in nine and "voicemode-heard-hook" in nine


# --- M15 what it never does --------------------------------------------------------------------

def test_m15_the_module_has_no_code_path_that_deletes():
    tree_ = ast.parse((ROOT / "src/ears/migrate.py").read_text())
    calls = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", None)
             for n in ast.walk(tree_) if isinstance(n, ast.Call)}
    assert not calls & {"unlink", "rmtree", "rmdir", "remove", "removedirs", "unlinkat"}
    assert "missing_ok" not in (ROOT / "src/ears/migrate.py").read_text()


def test_m15_launchctl_is_only_read_and_nothing_else_changes(mk):
    v_host(mk)
    sp = strays(mk)
    _write(mk.home / ".sessionkit/voice/settings/ears.json", "{}")
    _write(mk.home / ".sessionkit/voice/log/keeper.log", "x\n")
    _write(mk.home / ".local/share/ears/speech/venv/bin/python", "#!")
    keep = {p: tree(p) for p in (mk.home / ".sessionkit/voice/settings", mk.home / ".sessionkit/voice/log",
                                 mk.home / ".local/share/ears/speech", sp / "venv", mk.vm / "voices")}
    env_before = (mk.vm / "voicemode.env").read_bytes()
    assert run(mk.host())[0] == 0
    assert {p: tree(p) for p in keep} == keep and (mk.vm / "voicemode.env").read_bytes() == env_before
    assert mk.m.calls and all(c[0] == "print" for c in mk.m.calls)
    with pytest.raises(AssertionError):
        M._launchctl("bootout", "gui/1/x")


def test_m15_a_host_on_this_machine_is_never_built_by_these_tests(mk):
    h = mk.host()
    assert h.launchctl == mk.m.launchctl and h.home == mk.home
    assert str(h.home).startswith(str(mk.home.parent))


# --- M16 the split across a linked folder ------------------------------------------------------

@pytest.mark.parametrize("old", [SV, VM])
def test_m16_a_folder_link_between_two_roots_is_one_log(mk, old):
    nl = mk.new / "logs/conversations"
    _write(nl / OLD_DAY, _lines(1))
    _write(nl / "heard.lock", b"")
    r = mk.home / old
    (r / "logs").mkdir(parents=True)
    os.symlink(nl, r / "logs/conversations")
    assert H.populated(r) and H.populated(mk.new)
    assert H.split(mk.home) is None
    assert not any("WARNING" in x for x in H.status_lines({}, mk.home))
    assert H.state_source({}, mk.home) == ((r, "old") if old == SV else (mk.new, "new"))
    code, doc = run(mk.host())
    assert code == 1 and 4 in refusals(doc)
    assert "folder link" in json.dumps(doc) and str(r / "logs/conversations") in json.dumps(doc)


def test_m16_two_roots_with_their_own_heard_files_still_warn(mk):
    _write(mk.new / "logs/conversations" / OLD_DAY, _lines(1))
    _write(mk.vm / "logs/conversations" / OLD_DAY, _lines(1))
    assert H.split(mk.home) == (mk.new, mk.vm)
    assert any("WARNING" in x for x in H.status_lines({}, mk.home))
