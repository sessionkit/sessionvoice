"""The default ``message`` is the mouth service's alone (EARS-346, EARS-381).

The pytest fence (``tests/conftest.py``) exists only inside pytest. A review rig
that imports the mouth in its own process got the default sender (then
postfix's binary), and one test notice reached a live seat (the EARS-335
incident). Now only a process that a service entry point marked (``mouth serve``,
``mouth mail``, ``mouth retract``, ``mouth again``) may use the default,
sessionmail's ``message``. Anything else with no ``$MOUTH_SENDMAIL`` is refused,
loudly, on the said row: ``failed: message send fenced: ...``.

The subprocess tests run the mouth the way a rig would: ``sys.executable -c``,
outside pytest's conftest, with no ``$MOUTH_SENDMAIL`` and HOME in tmp.
**None of them can reach the real ``message``.** Each child points
``notice.DEFAULT_SENDMAIL`` at ``fixtures/fake_sendmail.py``, a recording fake.
It also wraps ``subprocess.run``, so any argv whose program is a ``message``
binary, or names postfix's ``sendmail``, exits the child with 99 before anything
runs. Against 18ac9c1 (no fence), the child reached the stub, which is how the
first test was shown to fail there.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from mouth import notice
from tests import env_fence
from ears import envnames

FAKE = Path(__file__).parent / "fixtures" / "fake_sendmail.py"
STUB = " ".join(shlex.quote(x) for x in (sys.executable, str(FAKE)))

#: The child's first lines: the default is a recording stub, and the real path is a tripwire.
_GUARD = textwrap.dedent(f"""
    import os, subprocess, sys
    from mouth import notice
    assert notice.DEFAULT_SENDMAIL == "message", notice.DEFAULT_SENDMAIL
    notice.DEFAULT_SENDMAIL = {STUB!r}
    _run = subprocess.run
    def _tripwire(argv, *a, **k):
        words = [str(x) for x in argv] if isinstance(argv, (list, tuple)) else str(argv).split()
        if (os.path.basename(words[0]) == "message"
                or any(x.endswith("/sendmail") for x in words)):
            print("TRIPWIRE: the real message (or sendmail) was named", file=sys.stderr, flush=True)
            os._exit(99)
        return _run(argv, *a, **k)
    subprocess.run = _tripwire
    ITEM = {{"utt": "u1", "text": "a rig's line", "notice_to": "ivy@" + notice._host()}}
    CUT = {{"reason": "error", "cut": True}}
""")


def _rig_env(tmp_path: Path) -> dict:
    """What a rig's process sees: the runner's env with no ``VOICEMODE_*`` and none of
    the renamed ``MOUTH_*`` (EARS-294), so no sendmail and no notice switch, but the
    suite's verifier and cascade fences, and HOME, the heard log, the mouth dir and the
    fake's mail root all in tmp."""
    keep = ("MOUTH_VERIFY_BIN", "MOUTH_SESSION_BIN")   # the suite's fences
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith("VOICEMODE_") or k in envnames.ALIASES) or k in keep}
    home = tmp_path / "home"
    (home / ".mail" / "agents").mkdir(parents=True, exist_ok=True)
    env.update(HOME=str(home), MOUTH_FAKE_MAIL_ROOT=str(home / ".mail" / "agents"),
               VOICEMODE_BASE_DIR=str(home / ".voicemode"),     # the heard log _said writes
               MOUTH_DIR=str(home / "mouth"))
    return env


def _child(tmp_path: Path, body: str) -> subprocess.CompletedProcess:
    p = subprocess.run([sys.executable, "-c", _GUARD + textwrap.dedent(body)],
                       env=_rig_env(tmp_path), cwd=tmp_path, capture_output=True, text=True,
                       timeout=60, check=False)
    assert p.returncode != 99, f"the real message was named: {p.stderr}"
    return p


def _sent(tmp_path: Path) -> list:
    log = tmp_path / "home" / ".mail" / "agents" / ".sendmail.jsonl"
    return [json.loads(x)["argv"] for x in log.read_text().splitlines()] if log.exists() else []


# -- a rig, outside pytest ---------------------------------------------------------

def test_a_rig_outside_pytest_cannot_reach_the_default_message(tmp_path):
    """The incident's shape: a script imports the mouth and a line is dropped, with no
    $MOUTH_SENDMAIL. The default is never run; the row says why, loudly."""
    p = _child(tmp_path, """
        print(notice.dropped(ITEM, CUT))
    """)
    assert p.returncode == 0, p.stderr
    out = p.stdout.strip()
    assert out.startswith("failed: message send fenced:"), out
    assert "$MOUTH_SENDMAIL" in out
    assert _sent(tmp_path) == [], "the default message was run outside the service"
    # the C6 log line on stderr carries the same outcome: a reader sees it
    logged = [json.loads(x) for x in p.stderr.splitlines() if x.startswith("{")]
    assert logged and logged[-1]["outcome"].startswith("failed: message send fenced:")


def test_a_rig_through_the_player_cannot_reach_the_default_message(tmp_path):
    """The incident's vector exactly: ``from mouth import player`` and a drop through
    ``_said``, the said row carrying the refusal."""
    p = _child(tmp_path, """
        import json
        from mouth import player
        rec = player._said({**ITEM, "device": "null"}, utt="u1", cut=True, reason="error",
                           played_s=0)
        print(json.dumps({"notice": rec.get("notice")}))
    """)
    assert p.returncode == 0, p.stderr
    row = json.loads(p.stdout.strip().splitlines()[-1])
    assert row["notice"].startswith("failed: message send fenced:"), row
    assert _sent(tmp_path) == []


def test_a_rig_may_still_name_its_own_command(tmp_path):
    """An explicit $MOUTH_SENDMAIL is a choice, honoured in any process: it is
    how a rig points notices at a stub, and how the suite's fakes work."""
    p = _child(tmp_path, f"""
        os.environ["MOUTH_SENDMAIL"] = {STUB!r}
        print(notice.dropped(ITEM, CUT))
    """)
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == f"sent ivy@{notice._host()}"
    assert len(_sent(tmp_path)) == 1


# -- the service: both live player paths still send --------------------------------

@pytest.mark.parametrize("argv", [["serve"], ["mail", "--once"], ["mail"], ["retract", "u1"],
                                  ["again", "u1"]],
                         ids=["mouth serve", "mouth mail --once", "mouth mail", "mouth retract",
                              "mouth again"])
def test_the_service_entry_points_still_use_the_default(tmp_path, argv):
    """``python -m mouth serve`` (a CLI say's player) and ``python -m mouth mail`` (the
    resident launch agent) mark their process, so a drop there still goes through the
    default ``message``, here the recording fake, with NO env opt-in: a plain upgrade
    keeps both live paths sending. So does ``mouth retract``: another session's
    retract of a queued line writes its said row, and its notice, in the CLI's own
    process. So does ``mouth again`` (EARS-22), which writes the said row of a
    resume it replaces. The player loop (or the retract, or the again) is stubbed
    by one drop."""
    p = _child(tmp_path, f"""
        import runpy
        import mouth
        from mouth import inbox, player
        def one_drop(*a, **k):
            print(notice.dropped(ITEM, CUT), flush=True)
            return 0
        def one_retract(utt):
            one_drop()
            return {{"utt": utt}}
        player.serve = one_drop
        inbox.watch = one_drop
        mouth.retract = one_retract
        def one_again(utt, **k):
            one_drop()
            return {{"utt": utt}}
        mouth.again = one_again
        sys.argv = ["mouth", *{argv!r}]
        try:
            runpy.run_module("mouth", run_name="__main__", alter_sys=True)
        except SystemExit as e:
            assert not e.code, e.code
    """)
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip().splitlines()[0] == f"sent ivy@{notice._host()}", p.stdout + p.stderr
    sent = _sent(tmp_path)
    assert len(sent) == 1 and sent[0][:2] == ["send", f"ivy@{notice._host()}"]


@pytest.mark.parametrize("argv", [["status"], ["stop"], ["amend", "u-none", "new", "text"]],
                         ids=["mouth status", "mouth stop", "mouth amend"])
def test_the_other_verbs_do_not_mark_the_process(tmp_path, argv):
    """Only the service's verbs mark it: any other verb leaves the process fenced.
    ``stop`` writes a stop file and ``amend`` drops a mail; the player writes the row
    for both, so neither is the service. (``say`` and ``play`` are left out: they
    spawn a real player, which the child's tripwire would not cover.)"""
    p = _child(tmp_path, f"""
        from mouth import __main__ as cli
        cli.main({argv!r})
        print(notice.dropped(ITEM, CUT))
    """)
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip().splitlines()[-1].startswith("failed: message send fenced:")
    assert _sent(tmp_path) == []


# -- in process --------------------------------------------------------------------

def test_in_process_the_default_is_fenced_until_marked(monkeypatch, heard_box):
    """The rule itself, in this process: unmarked and no $MOUTH_SENDMAIL is
    refused; the same drop, marked as the service, goes through the default."""
    env_fence.delenv(monkeypatch, "MOUTH_SENDMAIL")
    monkeypatch.setattr(notice, "DEFAULT_SENDMAIL", STUB)
    monkeypatch.setattr(notice, "_SERVICE", None)
    item = {"utt": "u1", "text": "x", "notice_to": "ivy@" + notice._host()}
    fenced = notice.dropped(item, {"reason": "error", "cut": True})
    assert fenced.startswith("failed: message send fenced: not the mouth service")
    notice.mark_service("mouth serve")
    assert notice.dropped(item, {"reason": "error", "cut": True}) == f"sent ivy@{notice._host()}"


def test_an_empty_send_variable_is_unset(monkeypatch, heard_box):
    """``MOUTH_SENDMAIL=`` names no command: the default's rule applies."""
    monkeypatch.setenv("MOUTH_SENDMAIL", "")
    monkeypatch.setattr(notice, "DEFAULT_SENDMAIL", STUB)
    monkeypatch.setattr(notice, "_SERVICE", None)
    item = {"utt": "u1", "text": "x", "notice_to": "ivy@" + notice._host()}
    assert notice.dropped(item, {"reason": "error", "cut": True}).startswith("failed: message send fenced:")


def test_the_default_is_message_on_path_resolved_to_an_absolute_path(monkeypatch, heard_box,
                                                                      tmp_path):
    """The marked service's default is sessionmail's ``message``, found on PATH and run
    by its absolute path (rca-001 N3: launchd's PATH is short, so a relative name is
    no answer), with ``message send``'s arguments."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    exe = bin_ / "message"
    exe.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(FAKE))} \"$@\"\n")
    exe.chmod(0o755)
    env_fence.delenv(monkeypatch, "MOUTH_SENDMAIL")
    monkeypatch.setattr(notice, "DEFAULT_SENDMAIL", "message")
    monkeypatch.setattr(notice, "_SERVICE", "mouth serve")
    monkeypatch.setenv("PATH", f"{bin_}{os.pathsep}/usr/bin{os.pathsep}/bin")
    ran = []
    real = subprocess.run
    monkeypatch.setattr(notice.subprocess, "run", lambda argv, *a, **k: ran.append(argv) or real(argv, *a, **k))
    item = {"utt": "u1", "text": "x", "notice_to": "ivy@" + notice._host()}
    assert notice.dropped(item, {"reason": "error", "cut": True}) == f"sent ivy@{notice._host()}"
    assert ran[0][0] == str(exe) and os.path.isabs(ran[0][0])
    assert ran[0][1:3] == ["send", f"ivy@{notice._host()}"]


def test_the_default_is_found_where_uv_installs_it_under_a_launchd_path(monkeypatch, heard_box):
    """EARS-388: under launchd's PATH (/usr/bin:/bin:/usr/sbin:/sbin) `message` lives
    only in ~/.local/bin and uv's tool dir; the marked service still sends. HOME is
    the fence's tmp HOME."""
    home = Path(os.environ["HOME"])
    prog = home / ".local/share/uv/tools/sessionmail/bin/message"
    prog.parent.mkdir(parents=True)
    prog.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(FAKE))} \"$@\"\n")
    prog.chmod(0o755)
    (home / ".local/bin").mkdir(parents=True, exist_ok=True)
    (home / ".local/bin/message").symlink_to(prog)
    env_fence.delenv(monkeypatch, "MOUTH_SENDMAIL")
    monkeypatch.setattr(notice, "DEFAULT_SENDMAIL", "message")
    monkeypatch.setattr(notice, "_SERVICE", "mouth serve")
    monkeypatch.setenv("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    ran = []
    real = subprocess.run
    monkeypatch.setattr(notice.subprocess, "run", lambda argv, *a, **k: ran.append(argv) or real(argv, *a, **k))
    item = {"utt": "u1", "text": "x", "notice_to": "ivy@" + notice._host()}
    assert notice.dropped(item, {"reason": "error", "cut": True}) == f"sent ivy@{notice._host()}"
    assert ran[0][0] == str(home / ".local/bin/message")


def test_no_message_binary_is_a_failed_row_and_never_a_fallback(monkeypatch, heard_box, tmp_path):
    """Design C: with no ``message`` on PATH the marked service writes
    ``failed: no message binary (...)`` on the row and runs nothing: there is no
    fallback to postfix's binary, ever."""
    env_fence.delenv(monkeypatch, "MOUTH_SENDMAIL")
    monkeypatch.setattr(notice, "DEFAULT_SENDMAIL", "message")
    monkeypatch.setattr(notice, "_SERVICE", "mouth serve")
    monkeypatch.setenv("PATH", str(tmp_path))          # nothing on it
    ran = []
    monkeypatch.setattr(notice.subprocess, "run", lambda *a, **k: ran.append(a) or 1 / 0)
    item = {"utt": "u1", "text": "x", "notice_to": "ivy@" + notice._host()}
    out = notice.dropped(item, {"reason": "error", "cut": True})
    # nor where uv installs it, under the fence's tmp HOME (EARS-388)
    assert out == ("failed: no message binary (message is not an executable on PATH, "
                   "~/.local/bin/message, ~/.local/share/uv/tools/sessionmail/bin/message; "
                   "it is sessionmail's)"), out
    assert ran == []


# -- the sweep: every writer of a said row, and the process it runs in -------------

#: Every function in ``src/`` that writes a said row or sends a drop notice (it calls
#: ``_said``, ``said_unplayed`` or ``notice.dropped``), and the process that runs it.
#: Only ``mouth.retract`` and ``mouth._replace_resume`` run outside the player, in the
#: ``mouth retract`` and ``mouth again`` CLIs, which is why both verbs are in
#: ``SERVICE_VERBS``.
WRITERS = {
    ("mouth/player.py", "_said"): "the player (via the rows below)",
    ("mouth/player.py", "said_unplayed"): "the player (via the rows below)",
    ("mouth/player.py", "play_one"): "the player: serve",
    ("mouth/player.py", "_next"): "the player: serve",
    ("mouth/player.py", "_handle_stop"): "the player: serve",
    ("mouth/player.py", "_resume"): "the player: serve",
    ("mouth/player.py", "recover"): "the player: Queue.lines(act=True), from _next",
    ("mouth/player.py", "serve"): "the player: mouth serve, mouth mail",
    ("mouth/bed.py", "Bed._run"): "the player: Beds, made in serve",
    ("mouth/bed.py", "Bed._play"): "the player: Beds, made in serve",
    ("mouth/box.py", "Queue.lines"): "the player: lines(act=True), from _next",
    ("mouth/box.py", "Queue._refuse"): "the player: lines(act=True), from _next",
    ("mouth/box.py", "_refused_row"): "the player: lines(act=True), from _next",
    ("mouth/__init__.py", "retract"): "the mouth retract CLI (SERVICE_VERBS)",
    ("mouth/__init__.py", "_replace_resume"): "the mouth again CLI (SERVICE_VERBS, EARS-22)",
}


def _writers(src: Path) -> set:
    import ast

    found = set()
    for f in sorted(src.rglob("*.py")):
        stack: list = []

        class Visit(ast.NodeVisitor):
            def scope(self, n):
                stack.append(n.name)
                self.generic_visit(n)
                stack.pop()

            visit_ClassDef = visit_FunctionDef = visit_AsyncFunctionDef = scope

            def visit_Call(self, n):
                fn = n.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
                if name in ("_said", "said_unplayed", "dropped"):
                    found.add((f.relative_to(src).as_posix(), ".".join(stack) or "<module>"))
                self.generic_visit(n)

        Visit().visit(ast.parse(f.read_text(encoding="utf-8")))
    return found


def test_every_said_row_writer_is_accounted_for():
    """The fence's completeness, pinned. A said row's notice is sent in the process
    that writes the row, and only a marked process may use the default ``message``. So
    every writer must run in the player, or in a CLI verb listed in
    ``mouth.__main__.SERVICE_VERBS``. A new writer fails this test: name the process
    it runs in, add it to ``WRITERS``, and, if a CLI verb runs it in its own process,
    add that verb to ``SERVICE_VERBS`` (else its notices are ``failed: message send
    fenced``)."""
    from mouth import __main__ as cli

    src = Path(notice.__file__).resolve().parents[1]
    assert _writers(src) == set(WRITERS)
    assert cli.SERVICE_VERBS == ("mail", "serve", "retract", "again")
