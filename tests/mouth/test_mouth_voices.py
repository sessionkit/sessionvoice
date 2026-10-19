"""The mouth's default voice.

Two orders, one function (EARS-329). A CALLER's line (``mouth say``, ``mouth
voices``): the line's own, the caller's cascade (test_mouth_voice_cascade),
$MOUTH_VOICE unless the host locks ``voice``, the settings file,
af_sky. The BOX's order, for a line the player resolves itself (a mail with no
X-Mouth-Voice, a replay): the line's own, the settings file, then
$MOUTH_VOICE, then af_sky, so the player's environment, which may be
a seat's, never beats the Settings app.
"""

import json

import pytest

from ears.home import SETTINGS
import mouth
from mouth import voices
from tests import env_fence


@pytest.fixture
def d(tmp_path, monkeypatch):
    d = tmp_path / "mouth"
    d.mkdir()
    monkeypatch.setenv("MOUTH_DIR", str(d))
    monkeypatch.setenv("MOUTH_LOG_DIR", str(tmp_path / "logs"))
    env_fence.delenv(monkeypatch, "MOUTH_SETTINGS")
    env_fence.delenv(monkeypatch, "MOUTH_VOICE")
    env_fence.delenv(monkeypatch, "MOUTH_MAIL_LOG")
    return d


def queued(d, **kw):
    return mouth.say("hello", device="null", spawn=False, d=d, **kw)["voice"]


def test_builtin_when_nothing_is_set(d):
    assert voices.default_voice(d) == ("af_sky", "builtin")
    assert queued(d) == "af_sky"


def test_env_when_no_file(d, monkeypatch):
    monkeypatch.setenv("MOUTH_VOICE", "laurie")
    assert voices.default_voice(d) == ("laurie", "env")
    assert voices.default_voice(d, caller=True) == ("laurie", "env")
    assert queued(d) == "laurie"


def test_a_callers_env_beats_the_settings_file(d, monkeypatch):
    # EARS-329: a seat sets its voice once, by its environment, over the host's shared pick
    monkeypatch.setenv("MOUTH_VOICE", "laurie")
    (d / "settings.json").write_text(json.dumps({"voice": "bf_emma", "output": "default"}))
    assert voices.default_voice(d, caller=True) == ("laurie", "env")
    assert queued(d) == "laurie"


def test_the_box_order_still_reads_the_settings_file_before_env(d, monkeypatch):
    # the default, which the player uses for a line it resolves itself
    monkeypatch.setenv("MOUTH_VOICE", "laurie")
    (d / "settings.json").write_text(json.dumps({"voice": "bf_emma", "output": "default"}))
    assert voices.default_voice(d) == ("bf_emma", "settings")


def test_the_lines_own_voice_beats_the_file(d):
    (d / "settings.json").write_text(json.dumps({"voice": "bf_emma"}))
    assert queued(d, voice="ivy") == "ivy"


def test_read_for_every_line(d):
    f = d / "settings.json"
    f.write_text(json.dumps({"voice": "bf_emma"}))
    assert queued(d) == "bf_emma"
    f.write_text(json.dumps({"voice": "am_adam"}))
    assert queued(d) == "am_adam"


@pytest.mark.parametrize("body", ["", "not json", "[1, 2]", '{"voice": ""}', '{"voice": 7}',
                                  '{"output": "default"}'])
def test_a_file_that_does_not_answer_falls_through(d, monkeypatch, body):
    # the box's order: the file is read, passed over, and the env under it answers
    monkeypatch.setenv("MOUTH_VOICE", "laurie")
    (d / "settings.json").write_text(body)
    assert voices.default_voice(d) == ("laurie", "env")
    # the caller's order: with no env, the file is read and passed over to af_sky
    env_fence.delenv(monkeypatch, "MOUTH_VOICE")
    assert voices.default_voice(d, caller=True) == ("af_sky", "builtin")


def test_settings_path_from_env(d, tmp_path, monkeypatch):
    f = tmp_path / "elsewhere" / "mouth.json"
    f.parent.mkdir()
    f.write_text(json.dumps({"voice": "bm_george"}))
    monkeypatch.setenv("MOUTH_SETTINGS", str(f))
    assert voices.settings_path(d) == f
    assert queued(d) == "bm_george"


# -- EARS-180: ~/.sessionkit/voice/settings/mouth.json first, then the old <mouth dir>/settings.json.
# HOME is the fence's tmp dir (tests/mouth/conftest.py), so ~ here is never the live one.

def home_settings(body):
    from pathlib import Path

    f = Path.home() / SETTINGS / "mouth.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(body))
    return f


def test_home_settings_reach_a_bare_say_with_no_symlink(d):
    # mouth.say passes the mouth dir EXPLICITLY (d = d or mouth_dir()): the new home must
    # win there too, not only when d is None.
    f = home_settings({"voice": "bm_george"})
    assert not (d / "settings.json").exists()
    assert voices.settings_path(d) == f and voices.settings_path() == f
    assert voices.default_voice(d) == ("bm_george", "settings")
    assert queued(d) == "bm_george"
    assert voices.voices(d)["settings"] == str(f)


def test_home_settings_beat_the_old_file(d):
    (d / "settings.json").write_text(json.dumps({"voice": "bf_emma"}))
    home_settings({"voice": "bm_george"})
    assert queued(d) == "bm_george"


def test_the_old_file_still_works_when_home_settings_are_absent(d):
    from pathlib import Path

    assert not (Path.home() / SETTINGS / "mouth.json").exists()
    (d / "settings.json").write_text(json.dumps({"voice": "bf_emma"}))
    assert voices.settings_path(d) == d / "settings.json"
    assert voices.default_voice(d) == ("bf_emma", "settings")
    assert queued(d) == "bf_emma"


def test_env_still_beats_home_settings(d, tmp_path, monkeypatch):
    home_settings({"voice": "bm_george"})
    f = tmp_path / "elsewhere.json"
    f.write_text(json.dumps({"voice": "am_adam"}))
    monkeypatch.setenv("MOUTH_SETTINGS", str(f))
    assert voices.settings_path(d) == f
    assert queued(d) == "am_adam"


def test_home_settings_are_read_for_every_line(d):
    home_settings({"voice": "bm_george"})
    assert queued(d) == "bm_george"
    home_settings({"voice": "am_adam"})
    assert queued(d) == "am_adam"


def test_voices_lists_both_kinds_and_the_default(d, monkeypatch):
    monkeypatch.setattr(voices, "clones", lambda: ["laurie", "ivy"])
    (d / "settings.json").write_text(json.dumps({"voice": "ivy"}))
    v = voices.voices(d)
    assert v["default"] == "ivy" and v["from"] == "settings"
    assert v["settings"] == str(d / "settings.json")
    assert "af_sky" in v["kokoro"] and v["clone"] == ["laurie", "ivy"]


def test_mouth_voices_reports_env_when_env_answers(d, monkeypatch):
    # Success Criteria 3: the env set, no cascade, a host file with a voice
    monkeypatch.setattr(voices, "clones", lambda: [])
    monkeypatch.setenv("MOUTH_VOICE", "laurie")
    (d / "settings.json").write_text(json.dumps({"voice": "bf_emma"}))
    v = voices.voices(d)
    assert (v["default"], v["from"]) == ("laurie", "env")


# -- EARS-329: a host lock pins the host's pick over the caller's env ----------

def test_a_host_lock_beats_the_callers_env(d, monkeypatch):
    # source (i): the _locked list in the file the mouth itself reads, no session id
    monkeypatch.setattr(voices, "clones", lambda: [])
    monkeypatch.setenv("MOUTH_VOICE", "laurie")
    (d / "settings.json").write_text(json.dumps({"voice": "bf_emma", "_locked": ["voice"]}))
    assert voices.default_voice(d, caller=True) == ("bf_emma", "settings")
    assert queued(d) == "bf_emma"
    assert voices.voices(d)["from"] == "settings"


def test_a_lock_with_no_host_voice_still_stops_the_env(d, monkeypatch):
    # no layer above the host picks the voice, the env included: the built-in answers
    monkeypatch.setenv("MOUTH_VOICE", "laurie")
    (d / "settings.json").write_text(json.dumps({"_locked": ["voice"]}))
    assert voices.default_voice(d, caller=True) == ("af_sky", "builtin")
    assert queued(d) == "af_sky"


@pytest.mark.parametrize("locked", ["voice", "nonvoice", {"voice": True}, ["voice", 7],
                                    ["nonvoice"], [], None])
def test_a_lock_that_is_not_a_list_of_strings_naming_voice_is_no_lock(d, monkeypatch, locked):
    # sessiontools refuses a _locked that is not a list of strings: the mouth counts it as none
    monkeypatch.setenv("MOUTH_VOICE", "laurie")
    (d / "settings.json").write_text(json.dumps({"voice": "bf_emma", "_locked": locked}))
    assert voices.default_voice(d, caller=True) == ("laurie", "env")
    assert queued(d) == "laurie"


# -- EARS-329: the player's env never beats the host file (R2) -----------------

def outside_mail(d, name, **headers):
    from mouth import box

    bx = box.ensure(box.box_dir(d))
    lines = ["From: someone@localhost", "To: mouth@localhost", "Subject: hi",
             f"Message-ID: <{name}@test>"]
    lines += [f"{k.replace('_', '-')}: {v}" for k, v in headers.items()]
    from maildir_put import put

    p = put(bx, name, "\n".join(lines) + "\n\nHello from outside.\n")
    return box.read(p, d)


@pytest.fixture
def player_env(d, monkeypatch):
    """The player started by a seat's `say` (ensure_player): the seat's whole env,
    MOUTH_VOICE included; the host file has the Settings app's pick."""
    env_fence.delenv(monkeypatch, "MOUTH_MAILBOX")
    monkeypatch.setenv("MOUTH_VOICE", "seat_voice")
    (d / "settings.json").write_text(json.dumps({"voice": "host_pick"}))
    return d


def test_a_mail_with_no_voice_keeps_the_host_pick(player_env):
    assert outside_mail(player_env, "m1")["voice"] == "host_pick"


def test_a_mails_own_voice_still_wins(player_env):
    assert outside_mail(player_env, "m2", X_Mouth_Voice="bm_lewis")["voice"] == "bm_lewis"


def test_a_replay_in_the_player_keeps_the_host_pick(player_env):
    # review M1: a mail whose X-Mouth-Extra clears the voice reaches the player with
    # none; player._resume replays the rest through say() in the PLAYER's process
    # (player.py, say(rest, voice=item.get("voice"), extra={"resume_of": ...})).
    item = outside_mail(player_env, "m3", X_Mouth_Extra=json.dumps({"voice": None}))
    assert item["voice"] is None
    got = mouth.say("from outside.", voice=item.get("voice"), device="null", spawn=False,
                    d=player_env, hold="turn-end", extra={"resume_of": item["utt"]})
    assert got["voice"] == "host_pick"


# -- R4: say --help names the steps in the docstring's order -------------------

#: one marker per step of the caller's order, highest first
STEPS = ("--voice", "session", "seat", "agent", "$MOUTH_VOICE", "settings file", "af_sky")


def test_the_docstring_numbers_the_callers_order():
    import re

    doc = voices.__doc__
    steps = re.findall(r"^(\d)\. (.*?)(?=^\d\. |\n\n)", doc, re.S | re.M)
    assert [int(n) for n, _ in steps] == list(range(1, len(STEPS) + 1))
    for (_, text), marker in zip(steps, STEPS):
        assert marker in text, (marker, text)
    assert "CALLER's environment" in steps[4][1]          # step 5's note
    assert "the host file, then\n``$MOUTH_VOICE``" in doc   # and the box's order


def test_say_help_names_the_steps_in_the_docstrings_order(capsys):
    from mouth.__main__ import main

    with pytest.raises(SystemExit) as e:
        main(["say", "--help"])
    assert e.value.code == 0
    text = " ".join(capsys.readouterr().out.split())
    assert "--voice" in text
    after = text[text.index("else the caller's"):]
    at = [after.index(m) for m in STEPS[1:]]
    assert at == sorted(at), dict(zip(STEPS[1:], at))


def test_the_docs_number_the_callers_order_as_the_docstring_does():
    # R4: docs/mouth.md "Your voice" lists the same seven steps, in the same order
    import re
    from pathlib import Path

    doc = (Path(__file__).resolve().parents[2] / "docs" / "mouth.md").read_text()
    section = doc.split("## Your voice", 1)[1].split("\n## ", 1)[0]
    steps = re.findall(r"^(\d)\. (.*?)(?=^\d\. |\n\n)", section, re.S | re.M)
    assert [int(n) for n, _ in steps] == list(range(1, len(STEPS) + 1))
    for (_, text), marker in zip(steps, STEPS):
        assert marker in text, (marker, text)
    assert "caller's environment" in steps[4][1]   # step 5 is the caller's, never the mouth's own
