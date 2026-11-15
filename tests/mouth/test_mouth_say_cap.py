"""EARS-528: ``mouth say`` refuses a line over 300 characters unless ``--long``.

The refusal is one line on stderr naming the cap in force, exit 2, and nothing
queued. The cap is ``say_max_chars``: the caller's cascade (session, seat,
agent), the host file, then 300; 0 or ``"off"`` is no cap, and a bad value is
skipped so the next layer answers. Only ``mouth say`` is capped (``say(cap=True)``);
a library caller that does not ask is never refused.

The CLI is driven with ``spawn=False`` so no player starts: the box's ``new/``
is the receipt of what was queued.
"""
from __future__ import annotations

import email
import email.policy
import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

import mouth
from mouth import __main__ as cli
from mouth import box as mbox, voices

from tests import env_fence

CAP_LINE = "mouth: over {n} characters: split it, or mail it (or --long)"


@pytest.fixture
def box(tmp_path, monkeypatch):
    d, logs = tmp_path / "mouth", tmp_path / "logs"
    monkeypatch.setenv("MOUTH_DIR", str(d))
    monkeypatch.setenv("MOUTH_LOG_DIR", str(logs))
    monkeypatch.setenv("VOICEMODE_AGENT", "ivy")
    monkeypatch.setenv("VOICEMODE_SESSION_ID", "s-test")
    monkeypatch.setenv("MOUTH_SESSION_BIN", "")      # no cascade unless a test fakes one
    for k in ("MOUTH_SETTINGS", "MOUTH_MAILBOX", "MOUTH_MAIL_LOG"):
        env_fence.delenv(monkeypatch, k)   # both names (EARS-294)
    real = cli.say
    monkeypatch.setattr(cli, "say", lambda text, **kw: real(text, spawn=False, **kw))
    return SimpleNamespace(d=d, logs=logs)


def settings_file(box, **kw) -> None:
    box.d.mkdir(parents=True, exist_ok=True)
    (box.d / "settings.json").write_text(json.dumps(kw))


def queued(box) -> list[Path]:
    new = box.d / "box" / "new"
    return sorted(new.iterdir()) if new.exists() else []


def heard_rows(box) -> list[str]:
    return [x for f in sorted(box.logs.glob("heard_*.jsonl")) for x in f.read_text().splitlines()]


def say(box, capsys, *args) -> tuple[int, str, str]:
    rc = cli.main(["say", "--device", "null", "--backend", "silence", *args])
    out, err = capsys.readouterr()
    return rc, out, err


def fake_session(tmp_path, monkeypatch, value, layer="seat") -> None:
    """A `session` whose cascade sets ``say_max_chars`` at ``layer``."""
    out = {"tools": {"mouth": {"values": {"say_max_chars": value},
                               "origins": {"say_max_chars": "/l"},
                               "sources": [{"layer": layer, "path": "/l"}]}}}
    p = tmp_path / "session"
    p.write_text(f"#!/bin/sh\ncat <<'EOF'\n{json.dumps(out)}\nEOF\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("MOUTH_SESSION_BIN", str(p))


# -- the CLI: 300 spoken, 301 refused, --long spoken --------------------------

def test_a_300_character_line_is_queued(box, capsys):
    rc, out, err = say(box, capsys, "x" * 300)
    assert rc == 0 and err == ""
    (f,) = queued(box)
    assert out.strip() and out.strip() in f.read_text()


def test_a_301_character_line_is_refused_in_one_line_and_nothing_is_queued(box, capsys):
    rc, out, err = say(box, capsys, "x" * 301)
    assert rc == 2 and out == ""
    assert err == CAP_LINE.format(n=300) + "\n"     # one line, naming the cap
    assert queued(box) == []
    assert heard_rows(box) == []                    # no saying, no said


def test_a_301_character_line_with_long_is_queued(box, capsys):
    rc, out, err = say(box, capsys, "--long", "x" * 301)
    assert rc == 0 and err == ""
    assert len(queued(box)) == 1


def test_long_speaks_a_line_of_any_length(box, capsys):
    rc, _, _ = say(box, capsys, "--long", "word " * 2000)
    assert rc == 0 and len(queued(box)) == 1


def test_the_count_is_of_the_joined_words(box, capsys):
    """Args are joined with one space: 150 + 1 + 150 is 301 characters."""
    rc, _, err = say(box, capsys, "a" * 150, "b" * 150)
    assert rc == 2 and "over 300 characters" in err
    rc, _, _ = say(box, capsys, "a" * 150, "b" * 149)
    assert rc == 0 and len(queued(box)) == 1


def test_the_count_is_of_stdin_as_given(box, capsys, monkeypatch):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("y" * 301 + "\n"))
    rc, _, err = say(box, capsys, "-")
    assert rc == 2 and "over 300 characters" in err and queued(box) == []
    monkeypatch.setattr("sys.stdin", io.StringIO("y" * 300 + "\n"))   # the trailing newline is stripped
    rc, _, _ = say(box, capsys, "-")
    assert rc == 0 and len(queued(box)) == 1


def test_characters_not_bytes(box, capsys):
    """300 characters of a multi-byte script is 300, not 900."""
    rc, _, _ = say(box, capsys, "é" * 300)
    assert rc == 0
    rc, _, _ = say(box, capsys, "é" * 301)
    assert rc == 2


# -- the setting: say_max_chars ------------------------------------------------

def test_the_built_in_cap_is_300(box):
    assert voices.BUILTIN_SAY_MAX_CHARS == 300
    assert voices.say_max_chars_setting(d=box.d) == (300, "builtin")


def test_the_host_file_moves_the_cap(box, capsys):
    settings_file(box, say_max_chars=20)
    rc, _, err = say(box, capsys, "x" * 21)
    assert rc == 2 and err == CAP_LINE.format(n=20) + "\n"
    rc, _, _ = say(box, capsys, "x" * 20)
    assert rc == 0
    settings_file(box, say_max_chars=1000)
    rc, _, _ = say(box, capsys, "x" * 301)
    assert rc == 0 and len(queued(box)) == 2


@pytest.mark.parametrize("off", [0, "off", "OFF", " off "])
def test_zero_or_off_disables_the_cap(box, capsys, off):
    settings_file(box, say_max_chars=off)
    assert voices.say_max_chars_setting(d=box.d) == (0, "settings")
    rc, _, _ = say(box, capsys, "x" * 5000)
    assert rc == 0 and len(queued(box)) == 1


@pytest.mark.parametrize("bad", [-1, 12.5, "300", "on", True, None, [300], {"n": 1}])
def test_an_invalid_value_falls_through_to_300(box, capsys, bad):
    settings_file(box, say_max_chars=bad)
    assert voices.say_max_chars_setting(d=box.d) == (300, "builtin")
    rc, _, err = say(box, capsys, "x" * 301)
    assert rc == 2 and err == CAP_LINE.format(n=300) + "\n"


def test_a_whole_float_counts(box):
    settings_file(box, say_max_chars=40.0)
    assert voices.say_max_chars_setting(d=box.d) == (40, "settings")


@pytest.mark.parametrize("layer", ["session", "seat", "agent"])
def test_the_cascade_beats_the_host_file(box, capsys, tmp_path, monkeypatch, layer):
    settings_file(box, say_max_chars=1000)
    fake_session(tmp_path, monkeypatch, 10, layer)
    cascaded = voices.cascade("s-test", voices.LINE_KEYS)
    assert voices.say_max_chars_setting(cascaded=cascaded, d=box.d) == (10, layer)
    rc, _, err = say(box, capsys, "x" * 11)
    assert rc == 2 and err == CAP_LINE.format(n=10) + "\n"


def test_off_in_the_cascade_disables_the_cap(box, capsys, tmp_path, monkeypatch):
    settings_file(box, say_max_chars=10)
    fake_session(tmp_path, monkeypatch, "off")
    rc, _, _ = say(box, capsys, "x" * 500)
    assert rc == 0 and len(queued(box)) == 1


def test_a_bad_cascade_value_falls_to_the_host_file(box, capsys, tmp_path, monkeypatch):
    settings_file(box, say_max_chars=10)
    fake_session(tmp_path, monkeypatch, "lots")
    rc, _, err = say(box, capsys, "x" * 11)
    assert rc == 2 and err == CAP_LINE.format(n=10) + "\n"


def test_the_cap_never_rides_on_the_line(box, capsys):
    settings_file(box, say_max_chars=1000)
    rc, _, _ = say(box, capsys, "hello")
    assert rc == 0
    (f,) = queued(box)
    m = email.message_from_bytes(f.read_bytes(), policy=email.policy.default)
    assert not [h for h in m.keys() if "max" in h.lower() or "cap" in h.lower()]
    assert not any(k.startswith("say_max_chars") for k in mbox.read(f, box.d))


# -- only mouth say is capped ---------------------------------------------------

def test_a_library_caller_is_not_capped(box):
    item = mouth.say("x" * 5000, spawn=False, device="null", backend="silence")
    assert item["text"] == "x" * 5000 and len(queued(box)) == 1


def test_say_cap_refuses_before_anything_is_queued(box):
    with pytest.raises(ValueError, match=r"^mouth: over 300 characters: split it, or mail it \(or --long\)$"):
        mouth.say("x" * 301, spawn=False, device="null", backend="silence", cap=True)
    assert queued(box) == []


def test_a_resume_is_never_capped_by_the_cascade(box, tmp_path, monkeypatch):
    """A resume reads no cascade; asked to cap, it still answers from the host and built-in."""
    fake_session(tmp_path, monkeypatch, 5)
    item = mouth.say("x" * 200, spawn=False, device="null", backend="silence", cap=True,
                     extra={"resume_of": "abc"})
    assert len(queued(box)) == 1 and item["text"] == "x" * 200


# -- the help names the cap, the flag and the setting --------------------------

def test_say_help_names_the_cap_the_flag_and_the_setting(capsys):
    with pytest.raises(SystemExit):
        cli.main(["say", "--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert "300 characters" in out and "--long" in out and "say_max_chars" in out
    assert "0 or off" in out


def test_the_top_help_names_the_cap(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert "300 characters at most, unless --long" in out


def test_the_docs_name_the_cap(request):
    root = Path(request.config.rootpath)
    mouth_md = (root / "docs" / "mouth.md").read_text()
    settings_md = (root / "docs" / "settings.md").read_text()
    for doc in (mouth_md, settings_md):
        assert "say_max_chars" in doc and "--long" in doc and "300" in doc
    schema = json.loads((root / "settings" / "mouth.schema.json").read_text())
    assert schema["properties"]["say_max_chars"]["default"] == voices.BUILTIN_SAY_MAX_CHARS
