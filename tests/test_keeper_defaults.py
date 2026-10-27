"""The keeper's defaults are a stranger's Mac's, not one host's (EARS-202 G21).

Every variable keeps its name; only what it means when unset changed:

    EARS_NOTIFY    <an agent>                -> unset: mail nobody, the log notes it
                                                (EARS-304 R3: unset -> the operator, when
                                                `session operator` names one; "" -> nobody)
    CALL_EARS_DIR  ~/.<agent>/call-ears      -> ${XDG_STATE_HOME:-~/.local/state}/ears/call-ears
    EARS_LOG_DIR   ~/Library/Logs/failmode   -> ~/Library/Logs/ears (as src/ears/services.py)
                                             -> ears.home.log_dir, ~/.sessionkit/voice/log
                                                (EARS-483: the keeper asks `$PY -m ears.home
                                                log-dir`; no copy of the path in the shell)

These run the keeper's own lines (cut out of keeper/ears-keeper.sh) under a fake HOME,
with the variables unset, and never start a capture, a call-ears or a mail.
"""
import importlib.machinery
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ears.home import LOG
from tests import _scanlist

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
# by path: the operator tests give the child a PATH of stub dirs only
BASH = shutil.which("bash") or "/bin/bash"
KEEPER = ROOT / "keeper" / "ears-keeper.sh"
VARS = ("EARS_NOTIFY", "CALL_EARS_DIR", "EARS_LOG_DIR", "XDG_STATE_HOME")
# the keeper's PY (a python that can `import ears`), which its LOGDIR line asks
PY_LINE = f"PY={shlex.quote(sys.executable)}"


def _line(prefix: str) -> str:
    """The ONE line of the keeper that starts with PREFIX."""
    hits = [ln for ln in KEEPER.read_text().splitlines() if ln.startswith(prefix)]
    assert len(hits) == 1, (prefix, hits)
    return hits[0]


def _mail_me() -> str:
    s = KEEPER.read_text()
    m = re.search(r"^mail_me\(\) \{.*?^\}\n", s, re.S | re.M)
    assert m, "mail_me() not found"
    return m.group(0)


def _env(home: Path, **extra) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in VARS}
    env.update(HOME=str(home), **extra)
    return env


def _bash(script: str, env: dict) -> str:
    out = subprocess.run([BASH, "-c", script], env=env, capture_output=True, text=True,
                         timeout=30)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


# -- the defaults, unset ---------------------------------------------------------

def _defaults(home, **extra):
    script = "\n".join([_line("NOTIFY="), PY_LINE, _line("LOGDIR="), _line("CALL_EARS_DIR="),
                        'echo "N[$NOTIFY]"; echo "L[$LOGDIR]"; echo "C[$CALL_EARS_DIR]"'])
    return _bash(script, _env(home, **extra)).splitlines()


def _tails(out, home):
    """Only what the DEFAULTS add after the fake HOME: the HOME itself sits under pytest's
    tmp dir (pytest-of-<user>), so it would hold the user's own name (EARS-326)."""
    return "\n".join(out).replace(str(home), "~")


def test_unset_defaults_are_neutral(home):
    out = _defaults(home)
    assert out == ["N[]", f"L[{home}/{LOG}]",
                   f"C[{home}/.local/state/ears/call-ears]"]


def test_unset_defaults_name_no_private_word(home):
    """EARS-406: what the defaults add holds no word of the private scan list (every row),
    and the same scan fires on each word planted from it as EARS_NOTIFY, which wins."""
    rows = _scanlist.rows()
    found = _scanlist.hits(_tails(_defaults(home), home), rows)
    assert found == [], f"the unset defaults hold a word of row(s) {found}"
    missed = [name for name, word in _scanlist.controls(rows)
              if name not in _scanlist.hits(_tails(_defaults(home, EARS_NOTIFY=word), home), rows)]
    assert missed == [], f"the scan missed a word planted from row(s) {missed}"


def test_call_ears_dir_follows_xdg_state_home(home, tmp_path):
    script = _line("CALL_EARS_DIR=") + '\necho "$CALL_EARS_DIR"'
    assert _bash(script, _env(home, XDG_STATE_HOME=str(tmp_path / "st"))) == \
        f"{tmp_path}/st/ears/call-ears"


def test_set_values_still_win(home, tmp_path):
    """The names are unchanged: a host that sets them (to keep its old paths) gets them."""
    script = "\n".join([_line("NOTIFY="), PY_LINE, _line("LOGDIR="), _line("CALL_EARS_DIR="),
                        'echo "$NOTIFY|$LOGDIR|$CALL_EARS_DIR"'])
    env = _env(home, EARS_NOTIFY="someone", EARS_LOG_DIR=str(tmp_path / "logs"),
               CALL_EARS_DIR=str(tmp_path / "ce"))
    assert _bash(script, env) == f"someone|{tmp_path}/logs|{tmp_path}/ce"


def test_calls_published_reads_the_same_default_dir(home):
    """calls_published() has its own fallback; it must name the same dir as CALL_EARS_DIR."""
    s = KEEPER.read_text()
    fn = s[s.index("calls_published() {"):s.index("# --- calls:")]
    live = home / ".local/state/ears/call-ears/live"
    live.parent.mkdir(parents=True)
    live.write_text("call:7\n")
    assert _bash(fn + "\ncalls_published", _env(home)) == "call:7"


# -- mail_me: nobody to mail -------------------------------------------------------

@pytest.fixture
def fake_message(tmp_path):
    """A `message` on PATH that only records its argv: no mail is ever sent."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    rec = tmp_path / "sent"
    m = bindir / "message"
    m.write_text(f'#!/bin/sh\necho "$@" >> "{rec}"\n')
    m.chmod(0o755)
    return bindir, rec


def _mail(home, fake_message, **extra):
    bindir, _ = fake_message
    env = _env(home, PATH=f"{bindir}:{os.environ['PATH']}", **extra)
    script = "\n".join(['note() { echo "NOTE $*"; }', _line("NOTIFY="), _mail_me(),
                        'mail_me "ears: capture restarted on x at 12:00 EOM"'])
    return _bash(script, env)


def test_mail_me_unset_mails_nobody_and_says_so(home, fake_message):
    _, rec = fake_message
    out = _mail(home, fake_message)
    assert out == ("NOTE not mailed (EARS_NOTIFY empty, or unset and no operator configured): "
                   "ears: capture restarted on x at 12:00 EOM")
    assert not rec.exists()


def test_mail_me_set_mails_that_address(home, fake_message):
    _, rec = fake_message
    assert _mail(home, fake_message, EARS_NOTIFY="someone") == ""
    assert rec.read_text().startswith("send someone -s ears: capture restarted")


# -- EARS_NOTIFY unset: the operator (EARS-304 R3, SC3; design section 3) -----------------
#
# The keeper's own lines, cut out: PY and MOD as the keeper sets them (PY is this test's
# python, which imports ears from src/), the ${EARS_NOTIFY+set} block, then NOTIFY=.
# `session` is a stub alone on PATH; MOUTH_SESSION_BIN (conftest's off switch)
# is removed from the child's env only where a test switches the reader ON.

CONFIGURED = {"operator": "you@host.example", "mailbox": "you", "privileged": ["you", "you@host.example"]}
UNCONFIGURED = {"operator": None, "mailbox": None, "privileged": []}
# AGENT_OPERATOR_ADDR alone: sessiontools moves `operator` and leaves privileged empty.
ADDR_ONLY = {"operator": "someone@elsewhere.example", "mailbox": None, "privileged": []}
OFF = "MOUTH_SESSION_BIN"
OFF_OLD = "VOICEMODE_MOUTH_SESSION_BIN"


def _notify_block() -> str:
    """From the ``${EARS_NOTIFY+set}`` test through the NOTIFY= line, as the keeper has them."""
    s = KEEPER.read_text()
    m = re.search(r'^if \[ -z "\$\{EARS_NOTIFY\+set\}" \]; then\n.*?^fi\n^NOTIFY=[^\n]*\n', s,
                  re.DOTALL | re.MULTILINE)
    assert m, "the EARS_NOTIFY block is not found"
    # a few lines, never more: a cut that ran on would run the keeper's own loop
    assert m.group(0).count("\n") <= 6, m.group(0)
    return m.group(0)


@pytest.fixture
def session(tmp_path):
    """``session(answer)``: a stub ``session`` alone in a bin dir; it marks every run."""
    bindir = tmp_path / "sbin"
    bindir.mkdir()
    marker = tmp_path / "session-ran"

    def make(answer=CONFIGURED, body=None):
        if body is None:
            body = f"printf '%s\\n' {shlex.quote(json.dumps(answer))}"
        exe = bindir / "session"
        # `: >` is a builtin: PATH holds only stub dirs, so `touch` would not be found
        exe.write_text(f'#!/bin/sh\n: > {shlex.quote(str(marker))}\n[ "$1" = operator ] || exit 9\n{body}\n')
        exe.chmod(0o755)
        return bindir

    make.dir, make.marker = bindir, marker
    return make


def _keeper_env(home, bindirs, reader_on=True, **extra) -> dict:
    env = _env(home, PATH=os.pathsep.join(str(b) for b in bindirs), **extra)
    env["PYTHONPATH"] = os.pathsep.join([str(SRC)] + [p for p in [os.environ.get("PYTHONPATH")] if p])
    if reader_on:
        for n in (OFF, OFF_OLD):   # both names (EARS-294): the old one is an off switch too
            env.pop(n, None)
    else:
        env[OFF] = ""
    return env


def _notify(env) -> str:
    script = "\n".join([f"PY={shlex.quote(sys.executable)}", _notify_block(), 'echo "N[$NOTIFY]"'])
    return _bash(script, env)


def test_the_block_sits_after_py_and_before_any_use():
    """The reader needs PY (the keeper sets it a few lines above); NOTIFY is
    read nowhere above the block, and there is still ONE NOTIFY= line (_line finds it)."""
    s = KEEPER.read_text()
    block = s.index(_notify_block())
    assert s.index('PY="${EARS_PY:-$ROOT/.venv/bin/python}"; MOD="ears.listen"') < block
    assert s.index('[ -x "$PY" ] ||') < block
    assert "$NOTIFY" not in s[:block] and "${NOTIFY" not in s[:block]
    assert _line("NOTIFY=") == 'NOTIFY="${EARS_NOTIFY:-}"'


def test_unset_takes_the_operator(home, session):
    session(CONFIGURED)
    assert _notify(_keeper_env(home, [session.dir])) == "N[you@host.example]"
    assert session.marker.exists()


def test_explicit_wins_and_the_reader_is_not_asked(home, session):
    session(CONFIGURED)
    assert _notify(_keeper_env(home, [session.dir], EARS_NOTIFY="someone")) == "N[someone]"
    assert not session.marker.exists()


def test_explicit_empty_mails_nobody_even_with_an_operator(home, session):
    """Q7: EARS_NOTIFY="" is an explicit "mail nobody": the only way a configured host opts out."""
    session(CONFIGURED)
    assert _notify(_keeper_env(home, [session.dir], EARS_NOTIFY="")) == "N[]"
    assert not session.marker.exists()


@pytest.mark.parametrize("answer", [UNCONFIGURED, ADDR_ONLY], ids=["unconfigured", "addr-only"])
def test_unconfigured_or_agent_operator_addr_alone_mails_nobody(home, session, answer):
    """R7, R2-F2: an operator with privileged [] (AGENT_OPERATOR_ADDR alone) is unconfigured."""
    session(answer)
    assert _notify(_keeper_env(home, [session.dir])) == "N[]"
    assert session.marker.exists()                  # it was asked, and said nobody


def test_no_session_mails_nobody(home, session):
    assert _notify(_keeper_env(home, [session.dir])) == "N[]"      # an empty bin dir


@pytest.mark.parametrize("body", ["exit 1", "printf 'not json\\n'"])
def test_a_failing_session_mails_nobody(home, session, body):
    session(body=body)
    assert _notify(_keeper_env(home, [session.dir])) == "N[]"


def test_the_off_switch_mails_nobody(home, session):
    session(CONFIGURED)
    assert _notify(_keeper_env(home, [session.dir], reader_on=False)) == "N[]"
    assert not session.marker.exists()


def _launch_block() -> str:
    """From the W=/PY=/MOD= line through HEARD=, as the keeper has them (EARS-294 T3)."""
    s = KEEPER.read_text()
    m = re.search(r'^W="\$ROOT"; PY=.*?^HEARD=[^\n]*\n', s, re.DOTALL | re.MULTILINE)
    assert m, "the launch block is not found"
    assert m.group(0).count("\n") <= 6, m.group(0)
    return m.group(0)


@pytest.mark.parametrize("ears_w", ["", "/somewhere/voicemode"])
def test_ears_w_is_ignored_and_said_once(home, tmp_path, ears_w):
    """EARS-294 T3 (RT-09): the EARS_W branch that ran voice_mode.listen is gone. Set, EARS_W
    changes nothing but one log line that says it is ignored; unset, there is no such line."""
    env = _env(home, EARS_PY=sys.executable, EARS_W=ears_w)
    out = _bash(f"ROOT={shlex.quote(str(tmp_path))}\n" + _launch_block()
                + 'echo "W=$W MOD=$MOD HEARD=$HEARD"; echo "PYTHONPATH=${PYTHONPATH:-}"', env).splitlines()
    assert out[-2] == f"W={tmp_path} MOD=ears.listen HEARD={sys.executable} -m ears.heard"
    assert not ears_w or ears_w not in out[-1]   # the old branch exported PYTHONPATH=$EARS_W
    out = out[:-1]
    said = [ln for ln in out[:-1] if "EARS_W" in ln]
    assert len(said) == (1 if ears_w else 0) and len(out) == len(said) + 1, out
    assert not said or said[0].endswith(f"EARS_W is set but ignored: the capture is ears.listen, run by {sys.executable}")


def test_no_voice_mode_launch_is_left_in_the_keepers():
    """EARS-294 T3: no keeper starts a voice_mode module. The capture-adoption patterns keep
    their voice_mode alternative on purpose (read-old: a running legacy capture is adopted)."""
    keeper = KEEPER.read_text()
    assert not re.search(r'MOD="voice_mode|-m voice_mode', keeper)
    assert "(ears|voice_mode)\\.listen capture" in keeper
    calls = (KEEPER.parent / "call-ears").read_text()
    assert 'MOD = "ears.listen"' in calls and "voice_mode.listen\"" not in calls
    assert "PYTHONPATH=CODE" not in calls
    assert not (KEEPER.parent / "mouth").exists()   # RT-11: the voice_mode.mouth launcher


def test_mail_me_mails_the_operator_when_unset(home, session, fake_message):
    bindir, rec = fake_message
    session(CONFIGURED)
    env = _keeper_env(home, [session.dir, bindir])
    script = "\n".join(['note() { echo "NOTE $*"; }', f"PY={shlex.quote(sys.executable)}",
                        _notify_block(), _mail_me(),
                        'mail_me "ears: capture restarted on x at 12:00 EOM"'])
    assert _bash(script, env) == ""
    assert rec.read_text().startswith("send you@host.example -s ears: capture restarted")


def test_mail_me_explicit_empty_mails_nobody_and_says_so(home, session, fake_message):
    bindir, rec = fake_message
    session(CONFIGURED)
    env = _keeper_env(home, [session.dir, bindir], EARS_NOTIFY="")
    script = "\n".join(['note() { echo "NOTE $*"; }', f"PY={shlex.quote(sys.executable)}",
                        _notify_block(), _mail_me(),
                        'mail_me "ears: capture restarted on x at 12:00 EOM"'])
    assert _bash(script, env).startswith("NOTE not mailed (EARS_NOTIFY empty")
    assert not rec.exists()


# -- the other readers of the same defaults ----------------------------------------------

def _load(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def test_call_ears_default_dir_is_the_keepers(monkeypatch, home, tmp_path):
    for k in VARS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", str(home))
    assert _load("call_ears", ROOT / "keeper" / "call-ears").DIR == \
        home / ".local/state/ears/call-ears"
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "st"))
    assert _load("call_ears", ROOT / "keeper" / "call-ears").DIR == tmp_path / "st/ears/call-ears"


def test_view_push_log_default_is_the_keepers(monkeypatch, home, tmp_path):
    monkeypatch.delenv("EARS_LOG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(home))
    assert _load("view_push", ROOT / "view" / "push").LOG == \
        str(home / LOG / "ears-view-push.log")
    monkeypatch.setenv("EARS_LOG_DIR", str(tmp_path / "logs"))
    assert _load("view_push", ROOT / "view" / "push").LOG == str(tmp_path / "logs/ears-view-push.log")


def test_sound_logs_default_beside_the_keepers_logs():
    """EARS-202 fix-003, EARS-483: mouth-sounds and ears-sounds log in the voice log folder, as
    EARS_LOG_DIR and every launchd/*.plist.in do: each asks ears.home.log_dir (voice_log_dir,
    no copy of the path); their env overrides are unchanged."""
    for script, var in (("mouth-sounds.py", "MOUTH_SOUNDS_LOG"), ("ears-sounds.py", "EARS_SOUNDS_LOG")):
        text = (ROOT / "keeper" / script).read_text()
        assert "Library/Logs" not in text and LOG not in text, script
        assert var in text and f'voice_log_dir() / "{script[:-3]}.jsonl"' in text, script
    for t in (ROOT / "launchd").glob("*.plist.in"):
        assert "Library/Logs" not in t.read_text(), t.name