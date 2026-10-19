"""EARS-388: sessionmail's ``message`` is found where uv installs it, not on PATH alone.

On a host whose ears-mail plist predates the wider PATH, launchd ran ears-mail with
``/usr/bin:/bin:/usr/sbin:/sbin``. ``message`` is a uv tool
(``~/.local/share/uv/tools/sessionmail/bin/message``, linked into ``~/.local/bin``),
so a PATH-only lookup failed every send with 127 and every heard turn was dropped.

``ears.message_bin`` is the rule: PATH, then each of ``KNOWN`` under HOME. Its copies
(``keeper/ears-mail.py``'s ``resolve_message``, ``keeper/ears-keeper.sh``'s
``mail_me``) are held to it here, over the same fake HOMEs. The launchd-PATH
regressions themselves sit beside the code they guard, importing nothing new, so they
fail at d4dc5e5 on the bug and not on an import: ``tests/test_ears_mail.py`` and
``tests/mouth/test_mouth_sendmail_fence.py``. HOME is always a tmp dir
and is passed in: a lookup that read the real ``~`` would find this machine's real
``message`` (rca-001's test-fence pitfall). The mouth's two callers are tested in
``tests/mouth/test_mouth_message_bin.py``.
"""
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ears import message_bin
from test_ears_mail import FAKE, em_module, turn

ROOT = Path(__file__).resolve().parents[1]
KEEPER = ROOT / "keeper" / "ears-keeper.sh"
BASH = shutil.which("bash") or "/bin/bash"
#: launchd's PATH when a plist names none: neither uv directory is on it
LAUNCHD_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
UV = ".local/share/uv/tools/sessionmail/bin/message"
LINK = ".local/bin/message"


FAKE_SEND = f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(FAKE))} \"$@\"\n"


def install(home: Path, link: bool = True, script: str = FAKE_SEND) -> Path:
    """sessionmail's ``message`` as uv lays it out under HOME: the program in uv's tool
    dir (by default a stand-in that runs the fake ``message send``), linked into
    ~/.local/bin."""
    prog = home / UV
    prog.parent.mkdir(parents=True)
    prog.write_text(script)
    prog.chmod(0o755)
    if link:
        (home / LINK).parent.mkdir(parents=True)
        (home / LINK).symlink_to(prog)
    return prog


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture
def empty_path(tmp_path):
    """A PATH with nothing on it: no host's own `message` can answer."""
    d = tmp_path / "empty-bin"
    d.mkdir()
    return str(d)


# -- the rule: ears.message_bin ------------------------------------------------------

def test_the_known_locations_are_uvs_link_then_its_tool_dir():
    assert message_bin.NAME == "message"
    assert message_bin.KNOWN == (LINK, UV)


def test_a_launchd_path_finds_the_uv_link(home):
    install(home)
    assert message_bin.find({"PATH": LAUNCHD_PATH, "HOME": str(home)}) == str(home / LINK)


def test_with_no_link_the_uv_tool_dir_answers(home):
    prog = install(home, link=False)
    assert message_bin.find({"PATH": LAUNCHD_PATH, "HOME": str(home)}) == str(prog)


def test_path_is_looked_at_first(home, tmp_path):
    install(home)
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "message").write_text("#!/bin/sh\n")
    (bin_ / "message").chmod(0o755)
    assert message_bin.find({"PATH": str(bin_), "HOME": str(home)}) == str(bin_ / "message")


def test_a_file_that_is_not_executable_is_not_message(home, empty_path):
    prog = install(home, link=False)
    prog.chmod(0o644)
    assert message_bin.find({"PATH": empty_path, "HOME": str(home)}) is None


def test_home_is_the_environs_and_none_is_looked_for_without_one(home, empty_path):
    install(home)
    assert message_bin.find({"PATH": empty_path}) is None
    assert message_bin.where_looked({"PATH": empty_path}) == "PATH (no HOME)"
    assert message_bin.where_looked({"HOME": str(home)}) == f"PATH, ~/{LINK}, ~/{UV}"


# -- ears-mail's copy -----------------------------------------------------------------

def test_ears_mails_copy_is_the_rules():
    assert em_module().KNOWN_MESSAGE == message_bin.KNOWN


def test_ears_mail_message_still_wins(home, tmp_path):
    """An explicit $EARS_MAIL_MESSAGE is what runs, even with `message` installed."""
    install(home)
    fake = tmp_path / "bin" / "fake"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    em = em_module()
    env = {"PATH": LAUNCHD_PATH, "HOME": str(home), "EARS_MAIL_MESSAGE": f"{fake} --x"}
    assert em.resolve_message(env) == ([str(fake), "--x"], "")
    # named but not there: refused, never swapped for the installed one
    cmd, why = em.resolve_message(dict(env, EARS_MAIL_MESSAGE=str(tmp_path / "gone")))
    assert cmd is None
    assert why == (f"no message binary ($EARS_MAIL_MESSAGE: {tmp_path / 'gone'} is not an "
                   f"executable on PATH; it is sessionmail's)")


def test_ears_mail_with_no_message_anywhere_is_127_and_says_where_it_looked(
        home, empty_path, monkeypatch):
    em = em_module()
    monkeypatch.delenv("EARS_MAIL_MESSAGE", raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", empty_path)
    monkeypatch.setattr(em, "_MESSAGE", None)
    m = em.mail_for(turn(1, "Hey Ivy.", seq=1), ["ivy"], {"ivy": "call"})
    code, mid, why = em.send(m, "ears", "host1")
    assert (code, mid) == (127, None)
    assert why == (f"no message binary (message is not an executable on PATH, "
                   f"~/{LINK}, ~/{UV}; it is sessionmail's)")
    assert why == (f"no message binary (message is not an executable on "
                   f"{message_bin.where_looked({'HOME': str(home)})}; it is sessionmail's)")


# -- the keeper's shell copy ------------------------------------------------------------

def _mail_me() -> str:
    m = re.search(r"^mail_me\(\) \{.*?^\}\n", KEEPER.read_text(), re.S | re.M)
    assert m, "mail_me() not found"
    return m.group(0)


def _keeper_mail(home: Path, path: str, tmp_path: Path) -> tuple:
    rec = tmp_path / "calls.jsonl"
    env = {"PATH": path, "HOME": str(home), "EARS_FAKE_MESSAGE_LOG": str(rec)}
    script = "\n".join(['note() { echo "NOTE $*"; }', "NOTIFY=someone", _mail_me(),
                        'mail_me "ears: capture restarted on x at 12:00 EOM"'])
    out = subprocess.run([BASH, "-c", script], env=env, capture_output=True, text=True,
                         timeout=30)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip(), rec


def test_keepers_copy_looks_in_the_rules_places():
    body = _mail_me()
    for rel in message_bin.KNOWN:
        assert f'"$HOME/{rel}"' in body, rel
    assert body.index("command -v message") < body.index(f'"$HOME/{message_bin.KNOWN[0]}"') \
        < body.index(f'"$HOME/{message_bin.KNOWN[1]}"')


def test_keeper_mails_under_a_launchd_path(home, tmp_path):
    install(home, script='#!/bin/sh\necho "$0 $*" >> "$EARS_FAKE_MESSAGE_LOG"\n')
    out, rec = _keeper_mail(home, LAUNCHD_PATH, tmp_path)
    assert out == ""
    assert rec.read_text() == (f"{home / LINK} send someone -s "
                               "ears: capture restarted on x at 12:00 EOM --no-body\n")


def test_keeper_with_no_message_anywhere_says_so(home, empty_path, tmp_path):
    out, rec = _keeper_mail(home, empty_path, tmp_path)
    assert out == ("NOTE mail FAILED (no message binary on PATH, "
                   "~/.local/bin or ~/.local/share/uv/tools/sessionmail/bin): "
                   "ears: capture restarted on x at 12:00 EOM")
    assert not rec.exists()
