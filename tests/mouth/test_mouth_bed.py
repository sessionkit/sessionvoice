"""EARS-152 do-003: a played file is a BED, one per device, on its own thread.

design.md s2 (the bed), s1 "Setting off", s5 (stop, retract, status), s6 (the
file rows). Every played file leaves the speech queue, so a line or a sound
for ANOTHER device never waits behind it (README "other devices never wait",
super ruling, Q9), whatever the duck setting. EARS-295 (design.md s1)
superseded Q9's same-device clause: on its own device every bed mixes, duck
on or off. A line mixes over it, a ``now`` line cuts the line playing and
never the music, and his words never cut it. With ducking on (the default) it
dips under his words and under a line (test_mouth_duck.py); with ducking off
it plays on at its own level.

Seams: setting-off, other-device-never-waits, stop-stops-music,
retract-music, second-play-waits, volume-per-item (files, duck off).

Two devices: ``null``, and ``dev-*`` names that a patched ``_plain_output``
(the device under ``open_output`` and under a mixer) turns into recording null outputs, so a level is measured on the blocks that
reached the device, not read off a field.
"""

from __future__ import annotations

import json
import shutil
import socket
import threading
import time
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import mouth
from ears import heard
from mouth import backends, bed, hold, output, player

from maildir_put import put
from tests import env_fence

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg")

HOST = socket.gethostname().split(".")[0].lower()
MODES = ("host-off", "flag-off", "default-on")
OFF_MODES = ("host-off", "flag-off")   # EARS-295: these once waited/cut; now they mix and play on


class RecOut(output.NullOut):
    """A null output that keeps every block written to it."""

    def __init__(self, name: str, sample_rate: int, pan=None) -> None:
        super().__init__(sample_rate, pan)
        self.name = name
        self.blocks: list = []

    def write(self, block) -> None:
        self.blocks.append(np.array(block, copy=True))
        super().write(block)


@pytest.fixture
def env(tmp_path, monkeypatch):
    d, logs, mail = tmp_path / "mouth", tmp_path / "logs", tmp_path / "mail"
    for sub in ("tmp", "new", "cur"):
        (mail / sub).mkdir(parents=True)
    settings = tmp_path / "mouth.json"
    monkeypatch.setenv("MOUTH_DIR", str(d))
    monkeypatch.setenv("MOUTH_MAILBOX", str(mail))
    monkeypatch.setenv("MOUTH_LOG_DIR", str(logs))
    monkeypatch.setenv("MOUTH_DEVICE", "null")
    monkeypatch.setenv("MOUTH_BACKEND", "silence")
    monkeypatch.setenv("MOUTH_SETTINGS", str(settings))
    monkeypatch.setenv("MOUTH_SESSION_BIN", "")     # the cascade stays off this machine
    monkeypatch.setenv("MOUTH_RESUME_S", "0")
    monkeypatch.setenv("VOICEMODE_AGENT", "ivy")
    monkeypatch.setenv("VOICEMODE_SESSION_ID", "s-bed")
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm"))   # the ears' heard log
    env_fence.delenv(monkeypatch, "MOUTH_BARGE_DEVICES")
    heard.log_dir().mkdir(parents=True, exist_ok=True)
    for c in hold._caches.values():
        c.update(t=0.0, v=None)
    outs: dict = {}
    real = output._plain_output   # the device under any mixer: open_output still asks for one

    def fake_open(name, sample_rate, pan=None, meta=None):
        if str(name).startswith("dev-"):
            o = RecOut(name, sample_rate, pan)
            outs.setdefault(name, []).append(o)
            return o
        return real(name, sample_rate, pan, meta)

    monkeypatch.setattr(output, "_plain_output", fake_open)
    return SimpleNamespace(d=d, logs=logs, mail=mail, tmp=tmp_path, settings=settings, outs=outs, n=[0])


def tone(env, name="song.wav", seconds=2.0, amp=0.3, rate=24000) -> Path:
    import wave

    path = env.tmp / name
    x = (amp * np.sin(2 * np.pi * 440 * np.arange(int(seconds * rate)) / rate) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(x.tobytes())
    return path


def mode(env, m: str) -> dict:
    """The duck setting under test: the play() keywords it needs."""
    if m == "host-off":
        env.settings.write_text(json.dumps({"music_duck": False}))
    return {"duck": False} if m == "flag-off" else {}


def play(env, seconds=2.0, name="song.wav", **kw) -> dict:
    kw.setdefault("device", "null")
    return mouth.play(str(tone(env, name, seconds)), spawn=False, d=env.d, **kw)


def say(env, text, **kw) -> dict:
    kw.setdefault("device", "null")
    kw.setdefault("backend", "silence")
    return mouth.say(text, spawn=False, d=env.d, **kw)


def deliver(env, body="", **headers) -> str:
    env.n[0] += 1
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = f"vera@{HOST}.example.ts.net", f"mouth@{HOST}", "hi"
    mid = f"<{env.n[0]}.bed@{HOST}.session-mail>"
    m["Message-ID"] = mid
    for k, v in headers.items():
        m[k.replace("_", "-")] = v
    m.set_content(body)
    put(env.mail, f"{time.time_ns()}.{env.n[0]}.{HOST}", bytes(m))
    return mid


def run(env, idle=0.3) -> threading.Thread:
    t = threading.Thread(target=player.serve, kwargs={"d": env.d, "idle_exit_s": idle}, daemon=True)
    t.start()
    return t


def rows(env) -> list:
    return [json.loads(x) for f in sorted(Path(env.logs).glob("heard_*.jsonl")) for x in f.read_text().splitlines()]


def kind(env, k: str) -> dict:
    return {r["utt"]: r for r in rows(env) if r["kind"] == k}


def started(env, utt) -> bool:
    return utt in kind(env, "saying")


def wait_for(pred, s=5.0) -> bool:
    end = time.time() + s
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def ears(text="hang on, wait", source="mic"):
    """His words, as a heard partial (the ears' own log)."""
    ts = datetime.now().astimezone().isoformat(timespec="milliseconds")
    with open(heard.log_path(), "a") as f:
        f.write(json.dumps({"ts": ts, "seq": 1, "kind": "partial", "text": text, "source": source}) + "\n")
    for c in hold._caches.values():
        c.update(t=0.0, v=None)


# -- the bed: its rows ---------------------------------------------------------

def test_a_file_is_a_bed_with_its_own_saying_and_said(env):
    f = play(env, seconds=1.0, volume=80)
    run(env).join(8)
    saying, said = kind(env, "saying")[f["utt"]], kind(env, "said")[f["utt"]]
    assert (saying["lane"], saying["duck"]["on"], saying["volume"]) == ("file", True, 80)
    assert saying["text"] == "[sound song.wav]" and saying["device"] == "null"
    assert (said["reason"], said["cut"], said["lane"], said["ducks"], said["ducked_s"]) == ("done", False, "file", 0, 0.0)
    assert said["dur_s"] == pytest.approx(1.0, abs=0.03) and said["played_s"] == pytest.approx(1.0, abs=0.06)
    assert said["volume"] == 80 and "cut_at_s" not in said     # None is dropped
    flags = [p.name.split(":2,")[1] for p in (env.mail / "cur").iterdir()]
    assert flags == ["S"]                               # filed S, as a played file is today
    assert not (env.d / "bed.json").exists() and not (env.d / "playing.json").exists()


def test_the_player_never_idles_out_under_the_music(env):
    f = play(env, seconds=1.0)
    th = run(env, idle=0.1)
    time.sleep(0.6)
    assert th.is_alive(), "the player left while a bed played"
    th.join(5)
    assert kind(env, "said")[f["utt"]]["reason"] == "done"


def held_at_the_end(monkeypatch) -> threading.Event:
    """Every file's decoder holds after its last block until the returned event
    is set, so its bed plays on (and stays in ``bed.json``) until then. Two beds
    started apart on a loaded machine are then both still playing when the test
    looks: the look never races the length of the file."""
    base, release = backends.FileSound, threading.Event()

    class Held(base):
        def stream(self, text, voice, speed):
            yield from base.stream(self, text, voice, speed)
            release.wait(30)   # a backstop only: a bed that ends early fails the look

    monkeypatch.setattr(backends, "FileSound", Held)
    return release


def test_status_lists_the_music_and_bed_json_one_entry_per_device(env, monkeypatch):
    release = held_at_the_end(monkeypatch)
    a = play(env, seconds=1.5, volume=60)
    b = play(env, seconds=1.5, name="tink.wav", device="dev-b")
    th = run(env)
    try:
        assert wait_for(lambda: started(env, a["utt"]) and started(env, b["utt"]))
        st = mouth.status(env.d)
        music = {m["utt"]: m for m in st["music"]}
        assert set(music) == {a["utt"], b["utt"]} and st["playing"] is None
        assert (music[a["utt"]]["device"], music[a["utt"]]["volume"]) == ("null", 60)
        assert music[a["utt"]]["file"].endswith("song.wav") and music[a["utt"]]["played_s"] >= 0
        assert music[a["utt"]]["duck"] == {"on": True, "level": 20}
        beds = json.loads((env.d / "bed.json").read_text())
        assert set(beds) == {"null", "dev-b"}
    finally:
        release.set()   # both beds end now, on their own
    th.join(8)
    assert mouth.status(env.d)["music"] == [] and not (env.d / "bed.json").exists()


# -- other-device-never-waits (duck on AND off, both directions) ---------------

@pytest.mark.parametrize("duck", [True, False], ids=["duck-on", "duck-off"])
@pytest.mark.parametrize("song_on,other", [("null", "dev-b"), ("dev-b", "null")])
def test_other_devices_never_wait_behind_a_bed(env, duck, song_on, other):
    env.settings.write_text(json.dumps({"music_duck": duck}))
    song = play(env, seconds=2.5, device=song_on)
    tink = play(env, seconds=0.3, name="tink.wav", device=other)   # an agent's Stop-hook sound
    line = say(env, "a line elsewhere", device=other)
    th = run(env)
    th.join(10)
    saying, said = kind(env, "saying"), kind(env, "said")
    order = sorted(rows(env), key=lambda r: r["seq"])
    pos = {(r["kind"], r["utt"]): i for i, r in enumerate(order)}
    for x in (tink, line):
        assert said[x["utt"]]["reason"] == "done"
        assert pos[("said", x["utt"])] < pos[("said", song["utt"])], "it waited for the song"
    assert said[song["utt"]]["reason"] == "done" and not said[song["utt"]]["cut"]
    # the sound started while the song was in its first moments: two beds at once
    t_song = datetime.fromisoformat(saying[song["utt"]]["ts"]).timestamp()
    t_tink = datetime.fromisoformat(saying[tink["utt"]]["ts"]).timestamp()
    assert abs(t_tink - t_song) < 0.5


# -- second-play-waits (Q3: the same device only) --------------------------------

def test_a_second_file_for_a_busy_device_waits_and_others_pass_it(env):
    one = play(env, seconds=1.0)
    two = play(env, seconds=0.3, name="two.wav")
    other = play(env, seconds=0.3, name="tink.wav", device="dev-b")
    run(env).join(10)
    order = sorted(rows(env), key=lambda r: r["seq"])
    pos = {(r["kind"], r["utt"]): i for i, r in enumerate(order)}
    assert pos[("saying", two["utt"])] > pos[("said", one["utt"])], "the second file replaced the first"
    assert pos[("said", other["utt"])] < pos[("said", one["utt"])], "another device waited"
    assert all(kind(env, "said")[x["utt"]]["reason"] == "done" for x in (one, two, other))


# -- setting-off (and, where MODES, the default too) -----------------------------

@pytest.mark.parametrize("m", OFF_MODES)
def test_a_line_to_the_beds_device_mixes_over_it_with_ducking_off(env, m):
    """EARS-295: a line no longer waits behind a duck-off file; it mixes, undipped."""
    f = play(env, seconds=1.5, **mode(env, m))
    line = say(env, "over the song")
    run(env).join(10)
    order = sorted(rows(env), key=lambda r: r["seq"])
    pos = {(r["kind"], r["utt"]): i for i, r in enumerate(order)}
    assert pos[("saying", line["utt"])] < pos[("said", f["utt"])]
    said = kind(env, "said")
    assert said[f["utt"]]["reason"] == "done" and said[line["utt"]]["reason"] == "done"
    assert said[line["utt"]]["over"] == f["utt"]
    assert said[f["utt"]]["ducks"] == 0
    assert not any(r.get("event") in ("duck", "unduck") for r in rows(env))


@pytest.mark.parametrize("m", OFF_MODES)
def test_barge_ignore_words_never_makes_his_words_cut_the_file(env, m, monkeypatch):
    """EARS-270 x EARS-295: barge_ignore_words reaches a line's barge count only.
    270 tested that an off-bed counted his words against it ([] let four filler
    words cut the file). Since 295 no bed is barge-cut, so the setting has
    nothing to reach there: with [] the same four words leave the file playing."""
    monkeypatch.setenv("MOUTH_BARGE_DEVICES", "null")
    kw = mode(env, m)   # host-off writes music_duck; add the ignore list beside it
    cfg = json.loads(env.settings.read_text()) if env.settings.exists() else {}
    env.settings.write_text(json.dumps({**cfg, "barge_ignore_words": []}))
    f = play(env, seconds=1.5, **kw)
    th = run(env, idle=0.5)
    assert wait_for(lambda: started(env, f["utt"]))
    time.sleep(0.3)
    ears("okay so well now")
    th.join(10)
    said = kind(env, "said")
    assert (said[f["utt"]]["reason"], said[f["utt"]]["cut"]) == ("done", False)


@pytest.mark.parametrize("m", MODES)
def test_the_file_prints_no_barge_records(env, m, monkeypatch, capsys):
    """EARS-270 x EARS-295: kind:barge records are a line's. 270 printed them
    for an off-bed's barge test, cut last. Since 295 hold.barge() never weighs
    his words over a bed (on or off), so it prints none, and its own record is
    the music's duck row (test_mouth_duck.py)."""
    monkeypatch.setenv("MOUTH_BARGE_DEVICES", "null")
    kw = mode(env, m)   # host-off writes music_duck; add the ignore list beside it
    cfg = json.loads(env.settings.read_text()) if env.settings.exists() else {}
    env.settings.write_text(json.dumps({**cfg, "barge_ignore_words": ["okay"]}))
    f = play(env, seconds=1.5, **kw)
    th = run(env, idle=0.5)
    assert wait_for(lambda: started(env, f["utt"]))
    time.sleep(0.3)
    ears("okay trying barge happens")
    th.join(10)
    assert kind(env, "said")[f["utt"]]["reason"] == "done"
    out = capsys.readouterr().out.splitlines()
    assert not [x for x in out if x.startswith('{"kind": "barge"')]


@pytest.mark.parametrize("m", OFF_MODES)   # duck on, they duck it: test_mouth_duck.py
def test_his_words_never_cut_the_file_on_its_own_device(env, m, monkeypatch):
    """EARS-295 (karaoke): with ducking off his words neither cut nor dip it, and
    no barge-in flush is written, so the next file queued behind it plays too."""
    monkeypatch.setenv("MOUTH_BARGE_DEVICES", "null")
    f = play(env, seconds=2.0, **mode(env, m))
    nxt = play(env, seconds=0.3, name="next.wav", **({"duck": False} if m == "flag-off" else {}))
    th = run(env, idle=0.5)
    assert wait_for(lambda: started(env, f["utt"]))
    time.sleep(0.3)
    ears("hang on, wait")
    th.join(10)
    said = kind(env, "said")
    assert (said[f["utt"]]["reason"], said[f["utt"]]["cut"]) == ("done", False)
    assert said[f["utt"]]["played_s"] == pytest.approx(2.0, abs=0.06)
    assert said[nxt["utt"]]["reason"] == "done"
    assert not any(r.get("event") in ("duck", "unduck") for r in rows(env))


@pytest.mark.parametrize("m", OFF_MODES)
@pytest.mark.parametrize("how", ["cli", "mail"])
def test_a_now_line_to_its_device_plays_at_once_and_spares_the_file(env, m, how, signed):
    """EARS-295: now-spares-music holds with ducking off too (was: the now cut it)."""
    f = play(env, seconds=3.0, **mode(env, m))
    th = run(env)
    assert wait_for(lambda: started(env, f["utt"]))
    time.sleep(0.2)
    t0 = time.time()
    if how == "cli":
        now = say(env, "right now", priority="now")["utt"]
    else:
        deliver(env, "right now", X_Mouth_Priority="now",
                X_Session_Signature=signed("2", "x-mouth-priority"))   # EARS-335
        now = None
    th.join(10)
    said, saying = kind(env, "said"), kind(env, "saying")
    assert (said[f["utt"]]["reason"], said[f["utt"]]["cut"]) == ("done", False)
    hit = saying[now] if now else next(r for r in saying.values() if r["text"] == "right now")
    assert datetime.fromisoformat(hit["ts"]).timestamp() - t0 < 0.6


@pytest.mark.parametrize("m", OFF_MODES)
def test_a_now_line_spares_the_file_while_a_line_plays_on_another_device(env, m):
    f = play(env, seconds=3.0, **mode(env, m))
    other = say(env, "word " * 12, device="dev-b")             # ~4 s on another device
    th = run(env)
    assert wait_for(lambda: started(env, f["utt"]) and started(env, other["utt"]))
    time.sleep(0.2)
    now = say(env, "right now", priority="now")
    th.join(10)
    said = kind(env, "said")
    assert said[f["utt"]]["reason"] == "done"                # EARS-295: was interrupted
    assert said[other["utt"]]["reason"] == "interrupted"   # say()'s stop, today's (review 1118 input 3)
    assert said[now["utt"]]["reason"] == "done"


@pytest.mark.parametrize("m", MODES)
def test_a_now_line_to_another_device_does_not_cut_the_file(env, m):
    f = play(env, seconds=1.5, **mode(env, m))
    th = run(env)
    assert wait_for(lambda: started(env, f["utt"]))
    now = say(env, "elsewhere now", priority="now", device="dev-b")
    th.join(10)
    said = kind(env, "said")
    assert said[f["utt"]]["reason"] == "done" and said[now["utt"]]["reason"] == "done"


@pytest.mark.parametrize("m", MODES)
def test_at_volume_100_the_blocks_are_todays_bytes(env, m):
    path = tone(env, "song.wav", 0.7)
    mouth.play(str(path), spawn=False, d=env.d, device="dev-a", **mode(env, m))
    run(env).join(8)
    got = env.outs["dev-a"][0].blocks
    step = int(backends.SAMPLE_RATE * backends.BLOCK_S)
    want = [b[i:i + step] for b in backends.FileSound(str(path)).stream("", "", None)
            for i in range(0, len(b), step)]            # play_one's own split, today
    assert len(got) == len(want)
    assert all(g.dtype == w.dtype and np.array_equal(g, w) for g, w in zip(got, want))


# -- volume-per-item (files, duck off: the mixer's bed_volume since EARS-295) ---

def test_a_files_volume_is_its_own(env):
    env.settings.write_text(json.dumps({"music_duck": False}))
    quiet = play(env, seconds=0.5, device="dev-a", volume=30)
    full = play(env, seconds=0.5, name="b.wav", device="dev-b", volume=100)
    deflt = play(env, seconds=0.5, name="c.wav", device="dev-c")
    run(env).join(8)

    def peak(dev):
        return max(float(np.abs(b).max()) for b in env.outs[dev][0].blocks)

    assert peak("dev-a") == pytest.approx(0.3 * peak("dev-b"), rel=0.01)
    assert peak("dev-c") == pytest.approx(peak("dev-b"), rel=1e-6)    # the built-in, 100
    said = kind(env, "said")
    assert (said[quiet["utt"]]["volume"], said[quiet["utt"]]["volume_from"]) == (30, "flag")
    assert (said[deflt["utt"]]["volume"], said[deflt["utt"]]["volume_from"]) == (100, "builtin")
    assert said[full["utt"]]["volume"] == 100


# -- stop-stops-music ------------------------------------------------------------

@pytest.mark.parametrize("m", MODES)   # EARS-295 T9: a stop still stops it, duck off too
def test_mouth_stop_stops_the_music_and_the_speech(env, m):
    f = play(env, seconds=3.0, **mode(env, m))
    line = say(env, "word " * 12, device="dev-b")
    th = run(env)
    assert wait_for(lambda: started(env, f["utt"]) and started(env, line["utt"]))
    time.sleep(0.2)
    mouth.stop(d=env.d)
    th.join(10)
    said = kind(env, "said")
    assert (said[f["utt"]]["reason"], said[f["utt"]]["cut"]) == ("stop", True)
    assert said[line["utt"]]["reason"] == "stop"
    assert said[f["utt"]]["cut_at_s"] < 1.5


@pytest.mark.parametrize("m", MODES)
def test_mouth_stop_stops_the_music_when_nothing_else_plays(env, m):
    f = play(env, seconds=3.0, **mode(env, m))
    th = run(env)
    assert wait_for(lambda: started(env, f["utt"]))
    time.sleep(0.2)
    t0 = time.time()
    mouth.stop(d=env.d)
    assert wait_for(lambda: f["utt"] in kind(env, "said"), 2.0)
    assert time.time() - t0 < 0.5
    th.join(5)
    assert kind(env, "said")[f["utt"]]["reason"] == "stop"


def test_a_barge_in_stop_from_another_lane_never_cuts_a_bed(env, monkeypatch):
    """design.md s4: a bed never obeys another lane's barge-in stop."""
    f = play(env, seconds=1.5)
    th = run(env)
    assert wait_for(lambda: started(env, f["utt"]))
    player._write_stop(env.d, {"t": time.time(), "reason": "barge-in", "flush": True})
    th.join(8)
    assert kind(env, "said")[f["utt"]]["reason"] == "done"


# -- retract-music ---------------------------------------------------------------

@pytest.mark.parametrize("m", MODES)   # EARS-295 T9: a retract still cuts it, duck off too
@pytest.mark.parametrize("while_a_line_plays", [False, True])
def test_mouth_retract_cuts_the_music(env, while_a_line_plays, m):
    f = play(env, seconds=3.0, **mode(env, m))
    line = say(env, "word " * 12, device="dev-b") if while_a_line_plays else None
    th = run(env)
    assert wait_for(lambda: started(env, f["utt"]) and (line is None or started(env, line["utt"])))
    time.sleep(0.2)
    t0 = time.time()
    mouth.retract(f["utt"], d=env.d)
    assert wait_for(lambda: f["utt"] in kind(env, "said"), 2.0)
    assert time.time() - t0 < 0.5, "the retract waited for the line"
    th.join(10)
    said = kind(env, "said")
    assert (said[f["utt"]]["reason"], said[f["utt"]]["cut"]) == ("retracted", True)
    if line:
        assert said[line["utt"]]["reason"] == "done"       # aimed at the music only
    assert not (env.d / "stop").exists()


def test_a_retract_by_mail_cuts_the_music(env, signed):
    f = play(env, seconds=3.0)
    th = run(env)
    assert wait_for(lambda: started(env, f["utt"]))
    time.sleep(0.2)
    deliver(env, "", Supersedes=f["mail_id"], X_Session_Signature=signed("2"))   # rule (a), EARS-335
    th.join(10)
    assert kind(env, "said")[f["utt"]]["reason"] == "retracted"


def test_retract_of_nothing_still_refuses(env):
    with pytest.raises(ValueError):
        mouth.retract("nope", d=env.d)


# -- the player around it --------------------------------------------------------

def test_prefetch_never_decodes_a_file(env, monkeypatch):
    made = []
    real = player.Synth

    def spy(item, on_done=None):
        made.append(item)
        return real(item, on_done=on_done)

    monkeypatch.setattr(player, "Synth", spy)
    say(env, "a line first", device="dev-b")
    f = play(env, seconds=0.5)
    run(env).join(8)
    assert not [x for x in made if x.get("file")], "a file was decoded by Synth"
    assert kind(env, "said")[f["utt"]]["reason"] == "done"


class NoDevicesSd:
    """PortAudio with no output devices: a real one would start CoreAudio's own
    threads, and a later test's fork() warns about them."""

    default = SimpleNamespace(device=(-1, -1))

    def _terminate(self):
        pass

    def _initialize(self):
        pass

    def query_devices(self, i=None):
        return []


def test_a_missing_device_closes_the_bed_and_frees_the_device(env, monkeypatch):
    monkeypatch.setattr(output, "_sd", lambda: NoDevicesSd())
    f = play(env, seconds=0.5, device="no-such-device-ears152")
    run(env).join(8)
    said = kind(env, "said")[f["utt"]]
    assert said["reason"] == "device-absent" and not (env.d / "bed.json").exists()


def test_device_keys():
    assert bed.device_key(None) is None
    assert bed.device_key("null") == "null" and bed.device_key("NULL") == "null"
    assert bed.device_key("call:61689") == "call:61689" == bed.device_key("CALL: 61689".replace(" ", ""))
    assert bed.device_key("MacBook Air Speakers") == "macbook air speakers"


def test_a_bare_call_is_looked_up_once_and_cached(monkeypatch):
    calls = []

    def find_call(name):
        calls.append(name)
        return 4242, "/x.sock"

    monkeypatch.setattr(output, "find_call", find_call)
    bed._KEYS.clear()
    assert bed.device_key("call") == "call:4242" == bed.device_key("call")
    assert calls == ["call"]
    bed._KEYS.clear()


def test_the_device_rescan_waits_while_a_stream_is_open(monkeypatch):
    """A PortAudio rescan is a terminate, and a terminate closes every open
    stream: a line opening its device must not kill a bed's (and back)."""
    events = []

    class Sd:
        default = SimpleNamespace(device=(0, 1))

        def _terminate(self):
            events.append("terminate")

        def _initialize(self):
            pass

        def query_devices(self, i=None):
            devs = [{"name": "A", "max_output_channels": 2}, {"name": "B", "max_output_channels": 2}]
            return devs if i is None else devs[i]

        def OutputStream(self, **kw):
            return SimpleNamespace(start=lambda: None, abort=lambda: None, close=lambda: None, latency=0.0)

    monkeypatch.setattr(output, "_sd", lambda: Sd())
    monkeypatch.setattr(output, "_open_streams", 0)
    a = output.DeviceOut("A", 24000)
    assert events == ["terminate"]          # nothing open yet: it looks again
    b = output.DeviceOut("B", 24000)
    assert events == ["terminate"]          # A is open: no rescan
    a.abort()
    a.abort()                               # twice is once
    assert output._open_streams == 1
    b.abort()
    output.DeviceOut("A", 24000).abort()
    assert events == ["terminate", "terminate"]


# -- review fixes (do-003 peer review, worker bef5a334) --------------------------

def test_a_second_player_that_loses_the_lock_leaves_the_live_beds_alone(env):
    """A second ``serve`` (two says racing in ensure_player, or a hand-run
    ``mouth serve``) must not wipe the live player's bed.json on its way out:
    retract and status read it for the whole song."""
    f = play(env, seconds=2.0)
    th = run(env)
    assert wait_for(lambda: started(env, f["utt"]))
    assert player.serve(d=env.d, idle_exit_s=0.1) == 0       # the lock is held: it leaves
    assert [b["utt"] for b in bed.read(env.d)] == [f["utt"]]
    mouth.retract(f["utt"], d=env.d)                          # still found
    th.join(8)
    assert kind(env, "said")[f["utt"]]["reason"] == "retracted"


def test_a_duck_off_beds_now_look_never_cuts_it_but_a_retract_does(env, signed):
    """The ``now`` look itself, with no race to hide behind (EARS-295: a duck-off
    bed once cut on a ``now`` for its own device; now no ``now`` cuts any bed).
    The duck-on twin is test_mouth_mixer.py's."""
    f = play(env, seconds=1.0, duck=False)
    item = mouth.box.public(mouth.box.Queue(env.d).lines()[0])
    b = bed.Bed(bed.Beds(env.d), item, None, bed.device_key(item["device"]))
    assert not b.ducks
    say(env, "elsewhere now", priority="now", device="dev-b")
    b._poll()
    assert b._why is None
    say(env, "here now", priority="now")
    b._next_look = 0.0
    b._poll()                                                 # no cut
    assert b._why is None
    deliver(env, "", Supersedes=f["mail_id"], X_Session_Signature=signed("2"))   # rule (a), EARS-335
    signed.settle(env.mail)   # its verdict has landed: a bed's poll never waits for one
    b._next_look = 0.0
    with pytest.raises(bed._Cut):
        b._poll()
    assert b._why == "retracted"


def test_a_retracted_beds_stop_is_gone_while_a_line_still_plays(env):
    """Beds._end clears a stop aimed at its own utt at once, not when the loop
    next idles: left in place it hides every later line's mail look."""
    f = play(env, seconds=3.0)
    line = say(env, "word " * 12, device="dev-b")             # ~4 s on another device
    th = run(env)
    assert wait_for(lambda: started(env, f["utt"]) and started(env, line["utt"]))
    time.sleep(0.2)
    mouth.retract(f["utt"], d=env.d)
    assert wait_for(lambda: f["utt"] in kind(env, "said"), 2.0)
    assert wait_for(lambda: not (env.d / "stop").exists(), 0.3)
    assert line["utt"] not in kind(env, "said"), "the line ended first: the check proved nothing"
    th.join(10)
