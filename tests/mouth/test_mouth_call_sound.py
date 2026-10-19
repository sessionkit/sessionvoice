"""call_sound - a SOUND mixed into a live call, never a line.

sessionphone's mouth leg takes {"sound": true}: no queue, no transcript, no
echo, never cut by a barge-in. A bot from before sounds would take the same
header for a LINE, and a line's EOF is a CUT - so the mouth probes for
"sounds" first and sends nothing to a bot without it. The fake bot here speaks
both dialects on a real Unix socket (sessionphone's mouthleg.py is the real
one; its own tests cover that side).
"""

from __future__ import annotations

import json
import struct

import numpy as np
import pytest

from mouth import output

from test_mouth_call import FakeBot


class SoundBot(FakeBot):
    """sounds=True: the probe says so and a sound header is mixed.
    took_line=True: advertises sounds but answers a sound header as a LINE
    (the reply the mouth must end as done, never as a cut)."""

    def __init__(self, calls, sounds=True, took_line=False, **kw):
        self.sounds, self.took_line = sounds, took_line
        self.mixed: list[dict] = []
        super().__init__(calls, **kw)

    def _one(self, c):
        f = c.makefile("rb")
        hdr = json.loads(f.readline())
        if hdr.get("probe"):
            info = {"ok": True, "agent": self.agent, "profile": "", "calls": self.calls}
            if self.sounds:
                info["sounds"] = True
            c.sendall((json.dumps(info) + "\n").encode())
            c.close()
            return
        if not (hdr.get("sound") and self.sounds) or self.took_line:
            # a pre-sounds bot (or a confused one): this is a LINE now
            return self._line(c, f, hdr)
        snd = {"header": hdr, "samples": 0, "ended": "eof", "pcm": []}
        self.mixed.append(snd)
        c.sendall(json.dumps({"ok": True, "call": hdr["call"], "sound": True}).encode() + b"\n")
        while True:
            head = f.read(5)
            if len(head) < 5:
                break
            kind, n = head[:1], struct.unpack(">I", head[1:])[0]
            payload = f.read(n) if n else b""
            if kind == b"A":
                a = np.frombuffer(payload, dtype="<i2")
                snd["samples"] += len(a)
                snd["pcm"].append(a)
            else:
                snd["ended"] = {b"D": "done", b"C": "cut"}[kind]
                break
        c.sendall(json.dumps({"ok": snd["ended"] == "done", "ended": snd["ended"], "sound": True,
                              "call": hdr["call"],
                              "mixed_s": round(snd["samples"] / 48000, 3)}).encode() + b"\n")
        c.close()

    def _line(self, c, f, hdr):
        line = {"header": hdr, "samples": 0, "ended": "eof", "pcm": []}
        self.lines.append(line)
        c.sendall(json.dumps({"ok": True, "call": hdr["call"]}).encode() + b"\n")
        while True:
            head = f.read(5)
            if len(head) < 5:
                break
            kind, n = head[:1], struct.unpack(">I", head[1:])[0]
            payload = f.read(n) if n else b""
            if kind == b"A":
                line["samples"] += len(np.frombuffer(payload, dtype="<i2"))
            else:
                line["ended"] = {b"D": "done", b"C": "cut"}[kind]
                break
        try:
            c.sendall(json.dumps({"ok": line["ended"] == "done", "ended": line["ended"]}).encode() + b"\n")
        except OSError:
            pass
        c.close()


@pytest.fixture
def sbots(monkeypatch):
    made = []

    def make(*a, **kw):
        b = SoundBot(*a, **kw)
        made.append(b)
        monkeypatch.setenv("MOUTH_CALL_SOCKS", ":".join(x.path for x in made))
        return b
    yield make
    for b in made:
        b.close()


def tick(rate, seconds=0.1, level=0.25):
    return np.full(int(rate * seconds), level, dtype=np.float32)


def test_a_sound_goes_in_whole_at_48k_and_is_mixed_not_queued(sbots):
    bot = sbots([5531])
    reply = output.call_sound("call:5531", tick(24000), 24000, label="turn")
    assert reply["ok"] and reply["ended"] == "done" and reply["sound"]
    assert bot.lines == []                                   # never a line
    (snd,) = bot.mixed
    assert snd["header"] == {"call": 5531, "rate": 48000, "sound": True, "name": "turn"}
    assert snd["samples"] == 4800                            # 0.1 s at 24k -> 48k
    assert snd["ended"] == "done"                            # D, never C
    pcm = np.concatenate(snd["pcm"])
    assert abs(int(pcm.mean()) - int(0.25 * 32767)) <= 1


def test_the_bare_call_device_finds_the_only_live_call(sbots):
    bot = sbots([77])
    output.call_sound("call", tick(48000), 48000)
    assert bot.mixed[0]["header"]["call"] == 77
    assert bot.mixed[0]["samples"] == 4800                   # already 48k: no resample


def test_a_bot_from_before_sounds_gets_nothing(sbots):
    bot = sbots([5531], sounds=False)
    with pytest.raises(output.DeviceAbsent, match="predates sounds"):
        output.call_sound("call:5531", tick(24000), 24000)
    assert bot.lines == [] and bot.mixed == []               # no line, so no EOF, so no cut


def test_a_bot_that_takes_it_for_a_line_is_ended_done_never_cut(sbots):
    bot = sbots([5531], took_line=True)
    with pytest.raises(output.DeviceAbsent, match="no sounds here"):
        output.call_sound("call:5531", tick(24000), 24000)
    (line,) = bot.lines
    assert line["ended"] == "done" and line["samples"] == 0  # an empty line, ended cleanly


def test_no_call_live_is_device_absent(monkeypatch):
    monkeypatch.setenv("MOUTH_CALL_SOCKS", "/tmp/nope-nothing-here.sock")
    with pytest.raises(output.DeviceAbsent, match="no call is live"):
        output.call_sound("call", tick(24000), 24000)


def test_the_wrong_call_is_device_absent(sbots):
    sbots([5531])
    with pytest.raises(output.DeviceAbsent, match="call 9 is not live"):
        output.call_sound("call:9", tick(24000), 24000)


def test_a_loud_sound_is_clipped_not_wrapped(sbots):
    bot = sbots([5531])
    output.call_sound("call:5531", tick(48000, level=3.0), 48000)
    pcm = np.concatenate(bot.mixed[0]["pcm"])
    assert pcm.min() == 32767                                # +3.0 clips to full scale, not negative
