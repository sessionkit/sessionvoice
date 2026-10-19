"""mail feeds mouth: a mail to mouth@<host> is a line.

Since the queue became a maildir there is no watcher: the
player reads new/ itself. No sound, no server, no postfix: mails are
delivered into a temp maildir as an MTA delivers them (tmp/, then one
rename into new/: ``maildir_put.put``), the silence backend speaks them
onto the null device.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace

import pytest

from mouth import inbox, player

from maildir_put import put
from tests import env_fence

HOST = socket.gethostname().split(".")[0].lower()


@pytest.fixture
def box(tmp_path, monkeypatch):
    d, logs, mail = tmp_path / "mouth", tmp_path / "logs", tmp_path / "maildir"
    for sub in ("new", "cur", "tmp"):
        (mail / sub).mkdir(parents=True)
    monkeypatch.setenv("MOUTH_DIR", str(d))
    monkeypatch.setenv("MOUTH_MAILBOX", str(mail))
    monkeypatch.setenv("MOUTH_LOG_DIR", str(logs))
    monkeypatch.setenv("MOUTH_DEVICE", "null")
    monkeypatch.setenv("MOUTH_BACKEND", "silence")
    # A player spawned for a test's own queue must not idle 600 s after it
    # (two were left running one Saturday).
    monkeypatch.setenv("MOUTH_IDLE_S", "1")
    env_fence.delenv(monkeypatch, "MOUTH_MAIL_ALLOW")
    return SimpleNamespace(d=d, logs=logs, mail=mail, n=[0])


def deliver(box, body="", subject="hi", sender=f"vera@{HOST}.example.net", **headers) -> str:
    box.n[0] += 1
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = sender, f"mouth@{HOST}", subject
    mid = f"<{box.n[0]}.test@{HOST}.session-mail>"
    m["Message-ID"] = mid
    m["X-Session-From"] = "9af31984"
    for k, v in headers.items():
        m[k.replace("_", "-")] = v
    m.set_content(body)
    put(box.mail, f"{time.time_ns()}.{box.n[0]}.{HOST}", bytes(m))
    return mid


def flags(box) -> list:
    """The flags of every mail filed to cur/, oldest first."""
    return [f.name.split(":2,")[1] for f in sorted((box.mail / "cur").iterdir()) if ":2," in f.name]


def spoken(box) -> list:
    return [x["text"] for x in lines(box.logs) if x["kind"] == "saying"]


def lines(logs):
    return [json.loads(x) for f in sorted(Path(logs).glob("heard_*.jsonl")) for x in f.read_text().splitlines()]


def run_player(d, idle=0.3):
    t = threading.Thread(target=player.serve, kwargs={"d": d, "idle_exit_s": idle}, daemon=True)
    t.start()
    return t


def test_a_mail_becomes_a_line_spoken_as_its_sender(box):
    mid = deliver(box, "**Hello** Theo, the `mouth` reads [mail](http://x).")
    run_player(box.d).join(5)
    saying = next(x for x in lines(box.logs) if x["kind"] == "saying")
    assert saying["text"] == "Hello Theo, the mouth reads mail."
    assert (saying["agent"], saying["session"], saying["mail_id"]) == ("vera", "9af31984", mid)
    assert not list((box.mail / "new").iterdir()) and flags(box) == ["S"]   # spoken, filed


def test_an_empty_body_speaks_the_subject(box):
    deliver(box, "", subject="Parking runs out in forty seconds")
    run_player(box.d).join(5)
    assert spoken(box) == ["Parking runs out in forty seconds"]


def test_supersedes_amends_a_line_not_yet_spoken(box):
    deliver(box, "word " * 20)                      # keeps the player busy
    old = deliver(box, "the old words")
    th = run_player(box.d)
    time.sleep(0.3)
    deliver(box, "the new words", Supersedes=old)
    th.join(8)
    assert "the new words" in spoken(box) and "the old words" not in spoken(box)
    assert sorted(flags(box)) == ["S", "S", "T"]    # the old one: replaced, never spoken


def test_the_newer_takes_the_older_ones_place(box):
    deliver(box, "word " * 20)
    old = deliver(box, "second, amended later")
    deliver(box, "third")
    th = run_player(box.d)
    time.sleep(0.3)
    deliver(box, "second, as amended", Supersedes=old)
    th.join(8)
    assert spoken(box)[1:] == ["second, as amended", "third"]


def test_supersedes_with_an_empty_body_retracts(box):
    deliver(box, "word " * 20)
    old = deliver(box, "never mind this")
    th = run_player(box.d)
    time.sleep(0.3)
    deliver(box, "", subject="retract", Supersedes=old)
    th.join(8)
    said = [x for x in lines(box.logs) if x["kind"] == "said"]
    assert any(x["reason"] == "retracted" for x in said)
    assert "never mind this" not in spoken(box)
    assert sorted(flags(box)) == ["S", "T", "T"]


def test_a_retract_by_mail_cuts_the_line_playing(box, signed):
    # only a local or signed v2 retract cuts a line playing (EARS-335 rule (a))
    old = deliver(box, "word " * 40)
    th = run_player(box.d)
    time.sleep(0.4)
    deliver(box, "", subject="retract", Supersedes=old, X_Session_Signature=signed("2"))
    th.join(8)
    said = next(x for x in lines(box.logs) if x["kind"] == "said")
    assert said["reason"] == "retracted" and said["cut"]


def test_a_correction_to_a_line_already_spoken_is_queued_as_news(box):
    old = deliver(box, "the meeting is at three")
    run_player(box.d).join(5)
    deliver(box, "correction, the meeting is at four", Supersedes=old)
    run_player(box.d).join(5)
    assert spoken(box) == ["the meeting is at three", "correction, the meeting is at four"]


def test_importance_high_and_x_mouth_priority(box):
    deliver(box, "first")
    deliver(box, "second")
    deliver(box, "urgent", Importance="high")
    run_player(box.d).join(8)
    assert spoken(box) == ["urgent", "first", "second"]


def test_a_playlist_by_number(box):
    """Append by default; next is 0; low numbers first."""
    deliver(box, "plain one")
    deliver(box, "thirty", X_Mouth_Priority="30")
    deliver(box, "plain two")
    deliver(box, "ten", X_Mouth_Priority="10")
    deliver(box, "next", X_Mouth_Priority="next")
    deliver(box, "ninety", X_Mouth_Priority="90")
    run_player(box.d).join(10)
    assert spoken(box) == ["next", "ten", "thirty", "plain one", "plain two", "ninety"]


def test_a_now_mail_cuts_the_line_playing(box, signed):
    # only a local line, or a signed one whose h= covers it, cuts in (EARS-335)
    deliver(box, "word " * 40)
    th = run_player(box.d)
    time.sleep(0.4)
    deliver(box, "urgent", X_Mouth_Priority="now", X_Session_Signature=signed("2", "x-mouth-priority"))
    th.join(8)
    said = [x for x in lines(box.logs) if x["kind"] == "said"]
    assert said[0]["reason"] == "interrupted" and spoken(box)[1] == "urgent"


def test_per_line_headers(box):
    deliver(box, "left ear please", X_Mouth_Channel="left", X_Mouth_Voice="af_bella")
    run_player(box.d).join(5)
    saying = next(x for x in lines(box.logs) if x["kind"] == "saying")
    assert (saying["pan"], saying["voice"]) == (-1.0, "af_bella")


def test_a_stranger_is_refused_and_never_spoken(box):
    deliver(box, "buy now", sender="spam@example.com")
    run_player(box.d).join(5)
    assert not list((box.mail / "new").iterdir())          # filed, not left to retry
    assert flags(box) == ["T"] and spoken(box) == []


def test_the_allow_list_admits_a_named_sender(box, monkeypatch):
    monkeypatch.setenv("MOUTH_MAIL_ALLOW", "theo@example.com,@host4.example.net")
    deliver(box, "from host4", sender="vera@host4.example.net")
    deliver(box, "from theo", sender="theo@example.com")
    run_player(box.d).join(5)
    assert spoken(box) == ["from host4", "from theo"]


def test_a_sound_by_mail(box, tmp_path, monkeypatch):
    import shutil
    if not shutil.which("ffmpeg"):
        pytest.skip("no ffmpeg")
    import wave
    import numpy as np
    monkeypatch.setenv("MOUTH_SOUNDS_DIR", str(tmp_path / "sounds"))  # a mail's sound (EARS-335)
    (tmp_path / "sounds").mkdir()
    wav = tmp_path / "sounds" / "chime.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
        w.writeframes((np.zeros(24000) + 1000).astype("<i2").tobytes())
    deliver(box, "", subject="chime", X_Mouth_File=str(wav), X_Mouth_End="0.5")
    run_player(box.d).join(5)
    said = next(x for x in lines(box.logs) if x["kind"] == "said")
    assert said["backend"] == "file" and said["dur_s"] == pytest.approx(0.5, abs=0.03)


def test_speakable():
    assert inbox.speakable("## Head\n- one\n- **two**\n> quoted") == "Head\none\ntwo\nquoted"
