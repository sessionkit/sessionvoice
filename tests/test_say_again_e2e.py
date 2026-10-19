"""Say again, end to end and offline (EARS-22 S6, designs/say-again.md section 6).

A synthetic heard log runs through the real keeper (``keeper/ears-mail.py --once``),
and its ``--mouth`` is the real ``python -m mouth`` CLI, run as its own process. The
test checks the README's success criteria short of live audio:

- the say-again turn's device mail carries ``<seat>@<host>=again`` and no ``call``,
  so the seat is not woken;
- the mouth's queue holds exactly one replay of that line, in the speech lane (not
  a bed), carrying what the replay's note to the seat needs;
- a phrase inside a longer sentence replays nothing.

NOTHING PLAYS, and nothing live is touched:

- **No player.** The test holds the temp mouth dir's ``player.lock``, so
  ``mouth again``'s ``ensure_player`` sees a player and starts none. The replay
  only waits in the queue, and the test asserts no player was started.
- **Temp paths only:** HOME, the heard log, the mouth dir, its maildir (pinned with
  ``MOUTH_MAILBOX``, never the live mouth's box), the audio dir, the
  settings and the voices.
- **No sound or synthesis:** the ``null`` device and the ``silence`` backend.
- **No mail:** the mouth's send command is ``tests/fixtures/refuse_sendmail.py``.
  The keeper's own ``send`` is replaced here by one that hands ``message send``'s
  arguments (``em.build``) and the body to that same refusing fixture, as
  ``$EARS_MAIL_MESSAGE`` would, and records the mail.
"""

from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from mouth import audio, bed, notice
from mouth import box as _box
from tests import env_fence

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "keeper" / "ears-mail.py"
REFUSE = ROOT / "tests" / "fixtures" / "refuse_sendmail.py"
RULES = {"seats": {"vera": {"names": "vera|vira"}, "ivy": {"names": "ivy|ivey"}}}
UTT = "c0ra11ne0001"
VERA_LINE = "The build is green and the second option is safer."
SR = 24000


def _em():
    spec = importlib.util.spec_from_file_location("ears_mail_e2e", SCRIPT)
    em = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(em)
    return em


em = _em()


def ts(sec):
    return f"2030-09-29T01:{int(sec // 60):02d}:{sec % 60:06.3f}+10:00"


def vera_line():
    """Vera's line as the mouth logs it: saying at 7 s, said (heard in full) at 10 s."""
    return [dict(ts=ts(7), kind="saying", agent="vera", text=VERA_LINE, utt=UTT,
                 voice="af_sky", session="s-vera-1"),
            dict(ts=ts(10), kind="said", agent="vera", utt=UTT, played_s=3.0, volume=40,
                 session="s-vera-1")]


def turn(sec, text, seq):
    return dict(ts=ts(sec), kind="turn", source="mic", device="airpods", text=text,
                t0=100.0, t1=101.0, seq=seq)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    home, base, d = tmp_path / "home", tmp_path / "base", tmp_path / "mouth"
    aud, hb = tmp_path / "audio", tmp_path / "heard-box"
    for p in (home / ".mail" / "agents" / "s-vera-1" / "new", base / "logs" / "conversations", d):
        p.mkdir(parents=True)
    for sub in ("tmp", "new", "cur"):
        (hb / sub).mkdir(parents=True)
    env = {
        "HOME": home, "VOICEMODE_BASE_DIR": base, "MOUTH_DIR": d,
        "MOUTH_MAILBOX": d / "box", "MOUTH_AUDIO_DIR": aud,
        "MOUTH_LOG_DIR": tmp_path / "mouth-logs",
        "MOUTH_SETTINGS": tmp_path / "mouth.json",
        "VOICEMODE_VOICES_DIR": tmp_path / "voices", "MOUTH_HEARD_BOX": hb,
        "MOUTH_MPV_SOCKET": tmp_path / "no-mpv.sock",
        "MOUTH_DEVICE": "null", "MOUTH_BACKEND": "silence",
        "MOUTH_SESSION_BIN": "",
        "MOUTH_SENDMAIL": shlex.join([sys.executable, str(REFUSE)]),
        "VOICEMODE_AGENT": "keeper", "VOICEMODE_SESSION_ID": "s-keeper",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))
    for k in ("EARS_MOUTH", "MOUTH_KEEP_AUDIO", "MOUTH_NOTICE",
              "MOUTH_MAIL_LOG", "MOUTH_TTS_KEY", "OPENAI_API_KEY"):
        env_fence.delenv(monkeypatch, k)   # both names (EARS-294)
    # The real `mouth again` runs in its own process: the design's 5 s holds on a quiet
    # host, but a loaded test run may start python slower, and this test is about the
    # wire, not the bound (tests/test_keeper_again.py pins MOUTH_TIMEOUT_S == 5.0).
    monkeypatch.setattr(em, "MOUTH_TIMEOUT_S", 60.0)
    lock = os.open(str(d / "player.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX)              # "a player holds its lock": none is started
    try:
        yield SimpleNamespace(home=home, base=base, d=d, audio=aud)
    finally:
        os.close(lock)


def keep(rig, *, wav=True, utt=UTT):
    """Vera's line as audio.keep leaves it: <day>/<utt>.wav beside <utt>.json."""
    folder = rig.audio / "2030-09-29"
    folder.mkdir(parents=True, exist_ok=True)
    w = folder / f"{utt}.wav"
    if wav:
        audio.write_wav(w, audio.pcm16([np.full(2400, 0.25, dtype=np.float32)]), SR)
    note = {"utt": utt, "voice": "af_sky", "backend": "kokoro", "speed": None, "agent": "vera",
            "session": "s-vera-1", "text": VERA_LINE, "sample_rate": SR, "dur_s": 0.1,
            "wav": str(w)}
    (folder / f"{utt}.json").write_text(json.dumps(note))
    return w


def keeper(rig, monkeypatch, capsys, records):
    """The real keeper, --to device --once, over RECORDS: ([mail], [stderr JSON], fire log).
    Its mails go to the refusing fixture and are recorded on the way."""
    (rig.base / "logs" / "conversations" / "heard_2030-09-29.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records))
    rf = rig.base / "wake.json"
    rf.write_text(json.dumps(RULES))
    sent = []

    def send(m, sender, domain):
        sent.append(m)
        p = subprocess.run([sys.executable, str(REFUSE), *em.build(m, sender, domain)],
                           input=m["body"].encode(), env=em.child_env(m.get("from") or sender),
                           capture_output=True, timeout=30)
        return p.returncode, None, p.stderr.decode(errors="replace")
    monkeypatch.setattr(em, "send", send)
    fire = rig.base / "fire.jsonl"
    capsys.readouterr()
    assert em.main(["--rules", str(rf), "--once", "--from-start", "--domain", "host1",
                    "--fire-log", str(fire),
                    "--mouth", shlex.join([sys.executable, "-m", "mouth"])]) == 0
    err = capsys.readouterr().err
    fired = [json.loads(x) for x in fire.read_text().splitlines()] if fire.exists() else []
    return sent, [json.loads(x) for x in err.splitlines() if x.startswith("{")], fired


def queued(rig):
    return [_box.public(x) for x in _box.Queue(rig.d).lines()]


def assert_nothing_played(rig):
    assert not (rig.d / "player.log").exists()        # ensure_player started nobody
    done = rig.d / "done"
    assert not (done.exists() and list(done.glob("*.json")))   # and no line was said


@pytest.mark.parametrize("text", [
    "Say again?",                    # unnamed: the seat that spoke last
    "Vera, say that again.",         # the added phrase, named
    "Can you say that again?",       # a worked example
])
def test_say_again_queues_one_speech_lane_replay_and_wakes_nobody(rig, monkeypatch, capsys, text):
    wav = keep(rig)
    sent, err, fired = keeper(rig, monkeypatch, capsys, vera_line() + [turn(13, text, seq=7)])

    # the wire: one device mail, rule again, no call, so Vera's seat is not woken
    assert [m["headers"]["Ears-Rule"] for m in sent] == ["vera@host1=again"]
    assert [f["rule"] for f in fired] == ["vera@host1=again"]
    assert not [e for e in err if e.get("skip") == "again-failed"]

    # the mouth: one replay of that utt, from its kept WAV, held for his turn's end
    [x] = queued(rig)
    assert (x["again_of"], x["again_from"], x["file"]) == (UTT, "audio", str(wav))
    assert not bed.is_file(x)                         # the speech lane, never a bed
    assert (x["hold"], x["priority"]) == ("turn-end", "next")
    assert (x["text"], x["agent"], x["session"], x["volume"]) == (VERA_LINE, "vera", "s-vera-1", 40)
    assert (x["again_seq"], x["again_heard"]) == (7, text)

    # what the keeper heard back from the real mouth names the same replay
    [done] = [e for e in err if e.get("again") == "replayed"]
    assert (done["utt"], done["mouth"]["utt"], done["mouth"]["again_of"]) == (UTT, x["utt"], UTT)

    # the replay's note (S3) goes to Vera's own box, never to the keeper that asked
    assert "notice_to" not in x
    assert notice.recipient(x)[0] == f"s-vera-1@{notice._host()}"
    assert_nothing_played(rig)


def test_a_wav_that_is_gone_replays_the_line_from_its_text_and_still_wakes_nobody(
        rig, monkeypatch, capsys):
    keep(rig, wav=False)                              # pruned: the note stays, the WAV is gone
    sent, _, _ = keeper(rig, monkeypatch, capsys, vera_line() + [turn(13, "Pardon?", seq=8)])
    assert [m["headers"]["Ears-Rule"] for m in sent] == ["vera@host1=again"]
    [x] = queued(rig)
    assert (x["again_of"], x["again_from"], x["text"]) == (UTT, "text", VERA_LINE)
    assert not x.get("file") and not bed.is_file(x)  # a TTS line: the box reads no file
    assert_nothing_played(rig)


def test_a_phrase_inside_a_longer_sentence_replays_nothing(rig, monkeypatch, capsys):
    keep(rig)
    recs = vera_line() + [turn(13, "Say that again later.", seq=9),
                          turn(16, "I'll say again that the build is green.", seq=10)]
    sent, err, _ = keeper(rig, monkeypatch, capsys, recs)
    # today's verdicts stand: the first is his reply to Vera's line, which it answers
    assert [m["headers"]["Ears-Rule"] for m in sent] == ["vera@host1=reply", ""]
    assert queued(rig) == [] and not [e for e in err if "again" in e]
    assert_nothing_played(rig)
