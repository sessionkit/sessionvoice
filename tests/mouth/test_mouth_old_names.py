"""EARS-294 T5: the mouth's readers still honour the old VOICEMODE_MOUTH_ names.

tests/test_envnames.py proves the table (every pair: old alone, new alone, new wins,
set-but-empty) and that no reader goes round it. These are the readers whose old name
a live host is known to set, or whose old name is a safety switch:

- DEVICE and VOICE: a hand-made launch agent sets these two under the old names, and
  must keep playing on its device in its voice.
- SESSION_BIN and VERIFY_BIN: set AT ALL (``""`` included) they are off switches, under
  either name; an empty new name is not passed over for an old path.
- SENDMAIL and NOTICE: a rig or a host that names its sendmail, or turns notices off,
  under the old name keeps doing so; the new name wins.
- BARGE_WORDS and the retired BARGE_WORDS_CALL, MAIL_LOG (``mouth mail``'s default),
  DIR, and the Kokoro URL.
"""
from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path

import pytest

import mouth
from ears import envnames
from ears import operator_config as oc
from mouth import backends, box, hold, inbox, maillog, notice, paths, voices
from tests import env_fence

ROOT = Path(__file__).resolve().parents[2]
MOUTH_PAIRS = sorted((n, o) for n, o in envnames.ALIASES.items() if n.startswith("MOUTH_"))


def only_old(monkeypatch, new, value):
    """The host of today: ``new``'s old name set to ``value``, the new name not at all."""
    env_fence.delenv(monkeypatch, new)
    monkeypatch.setenv(envnames.old(new), value)


@pytest.fixture
def d(tmp_path, monkeypatch):
    d = tmp_path / "mouth"
    d.mkdir()
    monkeypatch.setenv("MOUTH_DIR", str(d))
    monkeypatch.setenv("MOUTH_LOG_DIR", str(tmp_path / "logs"))
    for name in ("MOUTH_SETTINGS", "MOUTH_VOICE", "MOUTH_DEVICE", "MOUTH_MAIL_LOG"):
        env_fence.delenv(monkeypatch, name)
    return d


# -- every one of the 39 has a reader that goes through the table -----------------

@pytest.mark.parametrize("new, old", MOUTH_PAIRS)
def test_every_mouth_name_is_read_by_the_code(new, old):
    """No alias is dead: the new name is named by the code under src/ (outside the table),
    and the old one only beside its new one (a module that keeps its own pair, as
    operator_config does: the table is every other reader's)."""
    text = "\n".join(p.read_text() for p in sorted((ROOT / "src").rglob("*.py"))
                     if p.name != "envnames.py")
    assert new in text
    assert all(f'"{new}"' in line for line in text.splitlines() if old in line)


# -- DEVICE and VOICE: the hand-made launch agent's two -----------------------------

def test_the_old_device_name_still_picks_the_device(d, monkeypatch):
    only_old(monkeypatch, "MOUTH_DEVICE", "null")
    assert mouth.say("hello", spawn=False, d=d)["device"] == "null"


def test_the_new_device_name_wins(d, monkeypatch):
    monkeypatch.setenv("VOICEMODE_MOUTH_DEVICE", "old-device")
    monkeypatch.setenv("MOUTH_DEVICE", "null")
    assert mouth.say("hello", spawn=False, d=d)["device"] == "null"


def test_with_neither_device_name_say_still_refuses(d):
    with pytest.raises(ValueError, match=r"set MOUTH_DEVICE"):
        mouth.say("hello", spawn=False, d=d)


def test_the_old_voice_name_still_picks_the_voice(d, monkeypatch):
    only_old(monkeypatch, "MOUTH_VOICE", "laurie")
    assert voices.default_voice(d) == ("laurie", "env")
    assert voices.default_voice(d, caller=True) == ("laurie", "env")


def test_the_new_voice_name_wins(d, monkeypatch):
    monkeypatch.setenv("VOICEMODE_MOUTH_VOICE", "laurie")
    monkeypatch.setenv("MOUTH_VOICE", "bm_george")
    assert voices.default_voice(d) == ("bm_george", "env")


# -- SESSION_BIN: the off switch, under either name ----------------------------------

@pytest.mark.parametrize("value", ["", "/nowhere/session"])
def test_the_old_session_bin_name_alone_is_the_off_switch(monkeypatch, value):
    only_old(monkeypatch, "MOUTH_SESSION_BIN", value)
    assert oc.current() == oc.NOTHING
    assert oc.fallback_reason() == "off"


def test_with_neither_session_bin_name_the_operator_reader_is_on(monkeypatch):
    env_fence.delenv(monkeypatch, "MOUTH_SESSION_BIN")
    assert not oc._off()


def test_the_cascade_runs_the_old_names_session_bin(monkeypatch):
    ran = []

    def run(argv, **kw):
        ran.append(argv[0])
        raise OSError("not run")

    monkeypatch.setattr("subprocess.run", run)
    only_old(monkeypatch, "MOUTH_SESSION_BIN", "/old/session")
    assert voices.cascade("s-1", ("voice",)) == {}
    assert ran == ["/old/session"]


def test_an_empty_new_session_bin_is_not_passed_over_for_the_old_path(monkeypatch):
    """Presence: ``MOUTH_SESSION_BIN=""`` turns the cascade off even with an old path set."""
    ran = []
    monkeypatch.setattr("subprocess.run", lambda argv, **kw: ran.append(argv) or None)
    monkeypatch.setenv("VOICEMODE_MOUTH_SESSION_BIN", "/old/session")
    monkeypatch.setenv("MOUTH_SESSION_BIN", "")
    assert voices.cascade("s-1", ("voice",)) == {}
    assert ran == []
    assert oc._off()


# -- VERIFY_BIN: set at all, no `message verify` runs --------------------------------

def test_the_old_verify_bin_name_alone_still_turns_the_verifier_off(monkeypatch, tmp_path):
    looked = []
    monkeypatch.setattr("shutil.which", lambda name: looked.append(name))
    only_old(monkeypatch, "MOUTH_VERIFY_BIN", "")
    assert box.verify_signed(tmp_path / "m") is None
    assert looked == []
    env_fence.delenv(monkeypatch, "MOUTH_VERIFY_BIN")   # the control: unset, it looks
    assert box.verify_signed(tmp_path / "m") is None
    assert looked == ["message"]


# -- SENDMAIL and NOTICE ------------------------------------------------------------

def test_the_old_sendmail_name_is_still_the_send_command(monkeypatch):
    cmd = [sys.executable, "/old/sendmail.py"]
    only_old(monkeypatch, "MOUTH_SENDMAIL", shlex.join(cmd))
    assert notice._sendmail_cmd() == (cmd, "")


def test_the_new_sendmail_name_wins(monkeypatch):
    new = [sys.executable, "/new/sendmail.py"]       # resolved: its program must be there
    monkeypatch.setenv("VOICEMODE_MOUTH_SENDMAIL", "/old/sendmail")
    monkeypatch.setenv("MOUTH_SENDMAIL", shlex.join(new))
    assert notice._sendmail_cmd() == (new, "")


def test_an_empty_new_sendmail_is_not_passed_over_for_the_old(monkeypatch):
    """New wins even empty, and empty names no command: outside the service, fenced."""
    monkeypatch.setattr(notice, "_SERVICE", None)
    monkeypatch.setenv("VOICEMODE_MOUTH_SENDMAIL", "/old/sendmail")
    monkeypatch.setenv("MOUTH_SENDMAIL", "")
    cmd, why = notice._sendmail_cmd()
    assert cmd is None and "$MOUTH_SENDMAIL" in why


def test_the_old_notice_name_still_turns_notices_off(monkeypatch):
    only_old(monkeypatch, "MOUTH_NOTICE", "off")
    assert notice._decide({"utt": "u1"}, {"reason": "error", "cut": True}) == (None, "none: off")


# -- BARGE_WORDS, and the retired BARGE_WORDS_CALL ------------------------------------

def test_the_old_barge_words_name_is_still_read():
    assert hold._env_words({"VOICEMODE_MOUTH_BARGE_WORDS": "3"}) == 3
    assert hold._env_words({"VOICEMODE_MOUTH_BARGE_WORDS": "3", "MOUTH_BARGE_WORDS": "4"}) == 4


@pytest.mark.parametrize("name", ["VOICEMODE_MOUTH_BARGE_WORDS_CALL", "MOUTH_BARGE_WORDS_CALL"])
def test_the_retired_call_variable_is_ignored_under_either_name(name):
    assert hold.barge_warnings({name: "1"}) == []


def test_a_stale_barge_words_warns_under_the_name_it_was_set_by():
    warns = hold.barge_warnings({"VOICEMODE_MOUTH_BARGE_WORDS": "two"})
    assert [w["var"] for w in warns] == ["VOICEMODE_MOUTH_BARGE_WORDS"]


# -- the mail log's default, the mouth dir, the Kokoro URL ----------------------------

def test_watch_keeps_an_old_mail_log_name_and_gives_the_env_back(monkeypatch, tmp_path):
    """``mouth mail`` makes the box its own log only when no mail log is set, under
    either name; whatever it set, every name comes back as it was."""
    seen = {}

    def fake_watch(bx, retry_s, once):
        seen["log"] = envnames.get(maillog.LOG_VAR)
        seen["box"] = envnames.get("MOUTH_MAILBOX")
        return 0

    monkeypatch.setattr(inbox, "_watch", fake_watch)
    only_old(monkeypatch, "MOUTH_MAIL_LOG", "0")
    monkeypatch.setenv("VOICEMODE_MOUTH_MAILBOX", str(tmp_path / "old-box"))
    assert inbox.watch(tmp_path / "box", once=True) == 0
    assert seen == {"log": "0", "box": str(tmp_path / "box")}
    assert os.environ["VOICEMODE_MOUTH_MAIL_LOG"] == "0"
    assert os.environ["VOICEMODE_MOUTH_MAILBOX"] == str(tmp_path / "old-box")
    assert "MOUTH_MAILBOX" not in os.environ and "MOUTH_MAIL_LOG" not in os.environ


def test_watch_makes_the_box_its_own_log_when_neither_name_is_set(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(inbox, "_watch", lambda bx, r, o: seen.update(log=envnames.get(maillog.LOG_VAR)) or 0)
    assert inbox.watch(tmp_path / "box", once=True) == 0
    assert seen["log"] == str(tmp_path / "box")
    assert not envnames.isset(maillog.LOG_VAR)


def test_the_old_mouth_dir_name_is_still_the_mouth_dir(monkeypatch, tmp_path):
    only_old(monkeypatch, "MOUTH_DIR", str(tmp_path / "old"))
    assert paths.mouth_dir() == tmp_path / "old"


def test_the_player_is_started_with_the_new_mouth_dir_name(monkeypatch, tmp_path):
    """Write-new: what the mouth hands its player is the NEW name, which wins over an
    old one the caller's env still carries."""
    got = {}

    class Popen:
        def __init__(self, argv, env=None, **kw):
            got.update(env)

    monkeypatch.setattr("subprocess.Popen", Popen)
    monkeypatch.setattr(mouth, "_player_running", lambda d: False)
    monkeypatch.setenv("VOICEMODE_MOUTH_DIR", str(tmp_path / "old"))
    assert mouth.ensure_player(tmp_path / "new") is True
    assert got["MOUTH_DIR"] == str(tmp_path / "new")
    assert envnames.get("MOUTH_DIR", environ=got) == str(tmp_path / "new")


def test_the_old_kokoro_url_name_is_still_read(monkeypatch):
    only_old(monkeypatch, "MOUTH_KOKORO_URL", "http://127.0.0.1:9999/v1")
    env_fence.delenv(monkeypatch, "MOUTH_KOKORO_MODEL")
    url, _model = backends.speech_endpoint({})
    assert url == "http://127.0.0.1:9999/v1"


def test_the_settings_file_named_by_the_old_name_is_read(monkeypatch, tmp_path):
    f = tmp_path / "mouth.json"
    f.write_text(json.dumps({"voice": "bf_emma"}))
    only_old(monkeypatch, "MOUTH_SETTINGS", str(f))
    assert voices.settings_path() == f
