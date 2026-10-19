"""A say-again replay's note to the seat (EARS-22, design.md section 4c, S3).

The replay's ``said`` row sends ONE note, ``notice.again``, in place of the
drop notice: played in full, cut by his words, dropped stale, or re-said
from its text. It is one line, ``Priority: non-urgent`` (it rides, never
wakes), ``Mouth-Notice: again``, To the line's own seat: its session box,
else its agent box, never an outside sender.

No sound, no ffmpeg, no MTA: the ``null`` device, a fake decoder standing in
for ``backends.FileSound``, and the conftest's recording ``message send``
(``fixtures/fake_sendmail.py``), which "delivers" into the fenced
``$HOME/.mail/agents/<local part>/new``.
"""

from __future__ import annotations

import email
import email.policy
import json
import os
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import mouth
from mouth import audio, backends, level, notice, player
from tests import env_fence

HOST = socket.gethostname().split(".")[0].lower()
SR = 24000
UTT = "3f9c1a2b7e40"
TEXT = "The build is green, and the second option is the safer one."


class Decoded:
    """Stands in for ``backends.FileSound`` (ffmpeg): the kept WAV's audio, as
    ``blocks`` blocks of 50 ms. The null device paces them in real time."""

    name, sample_rate = "file", SR
    blocks = 3

    def __init__(self, path, start=None, end=None):
        pass

    def stream(self, text, voice, speed):
        for _ in range(Decoded.blocks):
            yield np.full(1200, 0.25, dtype=np.float32)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """The keeper calls ``mouth again``; the line was ivy's, in session s-ivy."""
    d, logs, aud = tmp_path / "mouth", tmp_path / "logs", tmp_path / "audio"
    monkeypatch.setenv("MOUTH_DIR", str(d))
    monkeypatch.setenv("MOUTH_LOG_DIR", str(logs))
    monkeypatch.setenv("MOUTH_AUDIO_DIR", str(aud))
    monkeypatch.setenv("MOUTH_DEVICE", "null")
    monkeypatch.setenv("MOUTH_IDLE_S", "1")
    monkeypatch.setenv("MOUTH_RESUME_S", "0")
    monkeypatch.setenv("MOUTH_SETTINGS", str(tmp_path / "mouth.json"))
    monkeypatch.setenv("MOUTH_SESSION_BIN", "")
    monkeypatch.setenv("VOICEMODE_VOICES_DIR", str(tmp_path / "voices"))
    monkeypatch.setenv("VOICEMODE_AGENT", "keeper")          # the caller is the keeper,
    monkeypatch.setenv("VOICEMODE_SESSION_ID", "s-keeper")   # never the line's seat
    env_fence.delenv(monkeypatch, "MOUTH_KEEP_AUDIO")
    env_fence.delenv(monkeypatch, "MOUTH_BACKEND")
    env_fence.delenv(monkeypatch, "MOUTH_MAIL_ALLOW")
    monkeypatch.setitem(audio._last_prune, "t", 0.0)
    monkeypatch.setattr(backends, "FileSound", Decoded)
    monkeypatch.setattr(player._hold, "check", lambda x: "play")
    monkeypatch.setattr(Decoded, "blocks", 3)
    level.forget_tries()
    agents = Path(os.environ["MOUTH_FAKE_MAIL_ROOT"])
    return SimpleNamespace(d=d, logs=logs, audio=aud, agents=agents)


def keep_line(root: Path, utt: str = UTT, day: str = "2030-10-16", *,
              make_wav: bool = True, **meta) -> Path:
    """A kept line as audio.keep leaves it: <day>/<utt>.wav and <utt>.json."""
    folder = root / day
    folder.mkdir(parents=True, exist_ok=True)
    w = folder / f"{utt}.wav"
    if make_wav:
        audio.write_wav(w, audio.pcm16([np.full(2400, 0.25, dtype=np.float32)]), SR)
    note = {"utt": utt, "voice": "af_sky", "backend": "kokoro", "speed": None, "agent": "ivy",
            "session": "s-ivy", "text": TEXT, "sample_rate": SR, "dur_s": 0.1, "wav": str(w), **meta}
    (folder / f"{utt}.json").write_text(json.dumps(note))
    return w


def mkbox(rig, name: str) -> Path:
    b = rig.agents / name
    for sub in ("new", "cur", "tmp"):
        (b / sub).mkdir(parents=True, exist_ok=True)
    return b


def mails(rig, name: str) -> list:
    new = rig.agents / name / "new"
    if not new.is_dir():
        return []
    return [email.message_from_bytes(p.read_bytes(), policy=email.policy.default)
            for p in sorted(new.iterdir())]


def sends(rig) -> list:
    """Every `message send` the fake saw, whoever it was to."""
    log = rig.agents / ".sendmail.jsonl"
    return [json.loads(x)["argv"] for x in log.read_text().splitlines()] if log.exists() else []


def said(rig) -> dict:
    rows = [json.loads(x) for f in sorted(rig.logs.glob("heard_*.jsonl"))
            for x in f.read_text().splitlines()]
    return {r["utt"]: r for r in rows if r["kind"] == "said"}


def serve(rig, *, then=None, wait: float = 10.0) -> None:
    t = threading.Thread(target=player.serve, kwargs={"d": rig.d, "idle_exit_s": 0.3}, daemon=True)
    t.start()
    if then:
        then()
    t.join(wait)
    assert not t.is_alive()


def replay(rig, **kw) -> dict:
    kw.setdefault("seq", 161204)
    kw.setdefault("heard_text", "say again")
    return mouth.again(UTT, d=rig.d, spawn=False, **kw)


def only_note(rig, box: str = "s-ivy"):
    """The ONE mail of the run: in BOX, and nothing sent anywhere else."""
    got = mails(rig, box)
    assert len(got) == 1 and len(sends(rig)) == 1, (got, sends(rig))
    return got[0]


def assert_a_note(m, item: dict) -> str:
    assert m["Mouth-Notice"] == "again"
    assert m["Priority"] == "non-urgent"              # rides, never wakes (rules.py)
    assert m["Mouth-Utt"] == item["utt"]              # the replay's utt; the line's is in the body
    assert m["To"] == f"s-ivy@{HOST}"
    assert m["Message-ID"].endswith(f"{notice.NOTICE_TAG}{HOST}>")   # the new form (EARS-381)
    assert len(m["Subject"]) <= notice.SUBJECT_MAX
    body = m.get_content()
    assert body.endswith("\n") and "\n" not in body.rstrip("\n")   # ONE line
    assert body.startswith('The user said "say again" (heard seq 161204): the mouth ')
    assert f"your line {UTT} from its " in body
    return body.rstrip("\n")


# -- through the player: one note per replay, whatever became of it -------------

def test_a_replay_played_in_full_sends_one_note_to_the_session_box(rig):
    keep_line(rig.audio)
    mkbox(rig, "s-ivy")
    mkbox(rig, "ivy")                                 # the agent box is NOT used: the session's is
    item = replay(rig)
    serve(rig)
    row = said(rig)[item["utt"]]
    assert row["reason"] == "done" and not row.get("cut")
    m = only_note(rig)
    body = assert_a_note(m, item)
    assert body.endswith(f"the mouth replayed your line {UTT} from its audio, in full. "
                         "No reply needed.")
    assert m["Subject"].startswith('replayed in full: "The build is green')
    assert row["notice"] == f"sent s-ivy@{HOST}"
    assert mails(rig, "ivy") == [] and mails(rig, "keeper") == mails(rig, "s-keeper") == []


def test_a_replay_his_words_cut_sends_one_note_and_no_drop_notice(rig, monkeypatch):
    keep_line(rig.audio)
    mkbox(rig, "s-ivy")
    monkeypatch.setattr(Decoded, "blocks", 200)       # 10 s: long enough to cut mid-line
    monkeypatch.setenv("MOUTH_RESUME_S", "20")   # resume ON: the replay still won't
    item = replay(rig)

    def barge():
        time.sleep(0.6)
        mouth.stop("barge-in", flush=False)
    serve(rig, then=barge)
    row = said(rig)[item["utt"]]
    assert row["reason"] == "barge-in" and row["cut"] and row["played_s"] > 0
    assert row["resume"] == "won't" and row["resume_why"] == player.AGAIN_NOT_RESUMED
    m = only_note(rig)                                # no drop notice beside it
    body = assert_a_note(m, item)
    at = float(row.get("cut_at_s") or row["played_s"])
    assert body.endswith(f"the mouth replayed your line {UTT} from its audio, "
                         f"cut at {at:.1f} s by the user's words. "
                         "Re-say it only if it still matters.")
    assert m["Subject"].startswith(f'replay cut at {at:.1f}s: "')
    assert row["notice"] == f"sent s-ivy@{HOST}"


def test_a_replay_gone_stale_sends_one_note_dropped_unheard(rig, monkeypatch):
    keep_line(rig.audio)
    mkbox(rig, "s-ivy")
    monkeypatch.setattr(player._hold, "check", lambda x: "stale")   # he began a new turn first
    item = replay(rig)
    serve(rig)
    row = said(rig)[item["utt"]]
    assert row["reason"] == "stale" and row["played_s"] == 0
    m = only_note(rig)
    body = assert_a_note(m, item)
    assert body.endswith(f"the mouth queued a replay of your line {UTT} from its audio, "
                         "dropped unheard: the user started talking again. "
                         "Re-say it only if it still matters.")
    assert m["Subject"].startswith('replay dropped unheard: "')
    assert m["Priority"] == "non-urgent"              # where a stale line's own notice wakes
    assert row["notice"] == f"sent s-ivy@{HOST}"


def test_a_replay_from_its_text_sends_one_note_saying_so(rig):
    keep_line(rig.audio, make_wav=False, backend="silence")   # the WAV is gone: rung 2
    mkbox(rig, "s-ivy")
    item = replay(rig)
    assert item["again_from"] == "text"
    serve(rig)
    row = said(rig)[item["utt"]]
    assert row["reason"] == "done"
    body = assert_a_note(only_note(rig), item)
    assert body.endswith(f"your line {UTT} from its text: the audio was gone, in full. "
                         "No reply needed.")


def test_notice_off_sends_no_note(rig, monkeypatch):
    keep_line(rig.audio)
    mkbox(rig, "s-ivy")
    monkeypatch.setenv("MOUTH_NOTICE", "off")
    item = replay(rig)
    serve(rig)
    assert said(rig)[item["utt"]]["notice"] == "none: off"
    assert mails(rig, "s-ivy") == [] and sends(rig) == []


def test_no_box_for_the_seat_records_none(rig):
    keep_line(rig.audio)
    item = replay(rig)
    serve(rig)
    got = said(rig)[item["utt"]]["notice"]
    assert got.startswith("none: ") and "no box for session s-ivy" in got
    assert sends(rig) == []


# -- the hook itself --------------------------------------------------------------

ITEM = {"utt": "r3p1ay000001", "text": TEXT, "again_of": UTT, "again_from": "audio",
        "again_seq": 161204, "again_heard": "say again", "agent": "ivy", "session": "s-ivy",
        "file": "/x/3f9c1a2b7e40.wav"}


def test_said_routes_a_replay_to_again_never_to_dropped(rig, monkeypatch):
    calls = []
    monkeypatch.setattr(notice, "dropped", lambda item, f: calls.append("dropped") or "x")
    monkeypatch.setattr(notice, "again", lambda item, f: calls.append("again") or "sent y")
    for fields in ({"cut": False, "reason": "done", "played_s": 2.0},
                   {"cut": True, "reason": "stale", "played_s": 0.0},
                   {"cut": True, "reason": "stop", "played_s": 0.5}):
        rec = player._said({**ITEM, "log_dir": str(rig.logs)}, **fields,
                           **{k: ITEM[k] for k in ("utt", "again_of")})
        assert rec["notice"] == "sent y"
    assert calls == ["again", "again", "again"]
    calls.clear()
    player._said({"utt": "plain1", "text": "x", "log_dir": str(rig.logs)}, utt="plain1",
                 cut=False, reason="done", played_s=1.0)
    assert calls == []                                # a plain line heard in full: no notice, as today


def test_the_note_never_goes_to_an_outside_sender(rig):
    mkbox(rig, "s-ivy")
    item = {**ITEM, "notice_to": f"stranger@{HOST}"}
    assert notice.again(item, {"cut": False, "reason": "done", "played_s": 2.0}) == \
        f"sent s-ivy@{HOST}"
    [m] = mails(rig, "s-ivy")
    assert m["Auto-Submitted"] == "auto-generated" and mails(rig, "stranger") == []


def test_with_no_session_box_the_note_goes_to_the_agent_box(rig):
    mkbox(rig, "ivy")
    assert notice.again(dict(ITEM), {"cut": False, "reason": "done"}) == f"sent ivy@{HOST}"
    assert [m["Mouth-Notice"] for m in mails(rig, "ivy")] == ["again"]


@pytest.mark.parametrize("rec,tail", [
    ({"cut": True, "reason": "stop", "played_s": 1.25, "cut_at_s": 1.25},
     "cut at 1.2 s: stop. Re-say it only if it still matters."),
    ({"cut": True, "reason": "device-absent", "played_s": 0.0},
     "dropped unheard: device-absent. Re-say it only if it still matters."),
    ({"cut": True, "reason": "barge-in", "played_s": 0.0},
     "dropped unheard: the user's words flushed it. Re-say it only if it still matters."),
    ({"cut": True, "reason": "lost", "played_s": 0.0},
     "the player stopped part way, so how much the user heard is unknown. "
     "Re-say it only if it still matters."),
], ids=["stop", "device-absent", "flushed", "lost"])
def test_every_other_end_is_one_line_naming_what_became_of_it(rec, tail):
    body = notice.again_body(dict(ITEM), rec)
    assert body.count("\n") == 1 and body.rstrip("\n").endswith(f"from its audio, {tail}")
    unheard = rec["played_s"] == 0 and rec["reason"] != "lost"
    assert ("queued a replay of your line" in body) == unheard      # never "replayed" unheard
    assert ("the mouth replayed your line" in body) == (not unheard)


def test_a_note_with_no_heard_turn_still_reads():
    item = {k: v for k, v in ITEM.items() if k not in ("again_seq", "again_heard")}
    assert notice.again_body(item, {"cut": False, "reason": "done"}).startswith(
        f"The user asked to hear it again: the mouth replayed your line {UTT} from its audio")


def test_the_heard_words_cannot_break_the_line_or_the_subject():
    item = {**ITEM, "again_heard": "say\nagain\r\n" + "x" * 300, "text": "y" * 300}
    body = notice.again_body(item, {"cut": False, "reason": "done"})
    assert body.count("\n") == 1 and "…" in body
    m = notice.again_message(item, {"cut": False, "reason": "done"}, f"s-ivy@{HOST}", reply=False)
    assert len(m.subject) <= notice.SUBJECT_MAX and m.subject.isascii() and "\n" not in m.subject


def test_a_self_retracted_replay_is_quiet_as_any_line(rig):
    mkbox(rig, "s-ivy")
    item = {**ITEM, "retracted_by": "s-ivy"}
    assert notice.again(item, {"cut": True, "reason": "retracted"}) == \
        "none: retracted by its sender"
    assert sends(rig) == []


def test_again_never_raises(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("boom")
    monkeypatch.setattr(notice, "recipient", boom)
    assert notice.again(dict(ITEM), {"cut": False, "reason": "done"}) == "failed: RuntimeError: boom"


def test_a_replays_drop_notice_would_ride_too():
    """``_non_urgent`` covers ``again_of`` (design 4c: it extends the existing rule)."""
    assert notice._non_urgent({"again_of": UTT}, {"reason": "stale"})
    assert not notice._non_urgent({}, {"reason": "stale"})
