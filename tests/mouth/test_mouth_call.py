"""call:<N> - the mouth speaks into a live call through a Delta bot's mouth leg.

A fake bot speaks the leg's protocol on a real Unix socket (the bot's
own mouth leg is the real one; its own tests cover that side).
"""

from __future__ import annotations

import json
import os
import socket
import struct
import tempfile
import threading
import time

import numpy as np
import pytest

from mouth import output, player

from test_mouth import box, lines, run_player, say  # noqa: F401 - fixtures


class FakeBot:
    def __init__(self, calls, agent="ivy", listen=True, probe_delay=0.0, header_delay=0.0):
        self.dir = tempfile.mkdtemp(prefix="mc-", dir="/tmp")   # AF_UNIX paths are short
        self.path = os.path.join(self.dir, "call-mouth.sock")
        self.calls, self.agent = list(calls), agent
        self.lines: list[dict] = []
        self.probes = 0
        self.probe_delay, self.header_delay = probe_delay, header_delay
        self.closed = threading.Event()          # ends a delay early at teardown
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(self.path)
        if listen:
            self.srv.listen(8)
            threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._one, args=(c,), daemon=True).start()

    def _one(self, c):
        f = c.makefile("rb")
        hdr = json.loads(f.readline())
        if hdr.get("probe"):
            self.probes += 1
            if self.closed.wait(self.probe_delay):   # a busy bot answers late
                c.close()
                return
            try:                                  # the mouth may have stopped waiting
                c.sendall((json.dumps({"ok": True, "agent": self.agent, "profile": "",
                                       "calls": self.calls}) + "\n").encode())
            except OSError:
                pass
            c.close()
            return
        if hdr.get("call") not in self.calls:
            c.sendall(b'{"ok": false, "error": "call is not live here"}\n')
            c.close()
            return
        if self.closed.wait(self.header_delay):
            c.close()
            return
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
                a = np.frombuffer(payload, dtype="<i2")
                line["samples"] += len(a)
                line["pcm"].append(a)
            else:
                line["ended"] = {b"D": "done", b"C": "cut"}[kind]
                break
        ok = line["ended"] == "done"
        try:
            c.sendall(json.dumps({"ok": ok, "ended": line["ended"]}).encode() + b"\n")
        except OSError:
            pass
        c.close()

    def close(self):
        self.closed.set()
        self.srv.close()


@pytest.fixture
def bots(monkeypatch):
    made = []

    def make(*a, **kw):
        b = FakeBot(*a, **kw)
        made.append(b)
        monkeypatch.setenv("MOUTH_CALL_SOCKS", ":".join(x.path for x in made))
        return b
    yield make
    for b in made:
        b.close()


def test_no_bot_no_call(monkeypatch):
    monkeypatch.setenv("MOUTH_CALL_SOCKS", "/tmp/nope-nothing-here.sock")
    with pytest.raises(output.DeviceAbsent, match="no call is live; live calls: none"):
        output.open_output("call", 24000)


def test_the_wrong_call_names_the_live_ones(bots):
    bots([5531])
    with pytest.raises(output.DeviceAbsent, match=r"call 77 is not live; live calls: call:5531 \(ivy\)"):
        output.open_output("call:77", 24000)


def test_a_call_device_takes_a_number(bots):
    bots([5531])
    with pytest.raises(output.DeviceAbsent, match="call:<N>"):
        output.open_output("call:abc", 24000)


def test_a_stale_socket_is_skipped(bots):
    bots([1], listen=False)          # the file is there, nobody is listening
    live = bots([5531])
    out = output.open_output("call", 24000)
    out.abort()
    assert live.lines[0]["header"]["call"] == 5531


def test_bare_call_with_two_live_calls_is_ambiguous(bots):
    bots([1])
    bots([2], agent="vera")
    with pytest.raises(output.DeviceAbsent, match="ambiguous"):
        output.open_output("call", 24000)


def test_a_line_goes_in_whole_at_48k_and_the_bot_keeps_it(bots):
    bot = bots([5531])
    out = output.open_output("call:5531", 24000, meta={"text": "Morning Theo.", "who": "ivy"})
    assert out.name == "call:5531" and out.pan_ignored is None
    t0 = time.monotonic()
    for _ in range(6):                               # 0.3 s in 50 ms blocks
        out.write(np.full(1200, 0.25, dtype=np.float32))
    out.drain()
    took = time.monotonic() - t0
    assert 0.25 < took < 0.6                         # paced in real time, not dumped
    line = bot.lines[0]
    assert line["ended"] == "done" and out.result == {"ok": True, "ended": "done"}
    assert line["header"] == {"call": 5531, "rate": 48000, "text": "Morning Theo.", "who": "ivy"}
    assert abs(line["samples"] - 14400) <= 2         # 0.3 s at 48 kHz
    assert out.frames_played == 7200


def test_a_cut_tells_the_bot(bots):
    bot = bots([5531])
    out = output.open_output("call:5531", 24000)
    out.write(np.zeros(1200, dtype=np.float32))
    out.abort()
    time.sleep(0.05)
    assert bot.lines[0]["ended"] == "cut"


def test_resampling_has_no_seams():
    out = output.CallOut.__new__(output.CallOut)
    out.sample_rate, out._last, out._pos = 24000, np.zeros(0, dtype=np.float32), 0.0
    ramp = np.linspace(-1, 1, 24000, dtype=np.float32)
    y = np.concatenate([out._resample(ramp[i:i + 1200]) for i in range(0, 24000, 1200)])
    assert abs(len(y) - 48000) <= 2
    assert np.all(np.diff(y) > 0)                    # monotonic straight through every seam


def test_pan_on_a_call_plays_mono_and_says_so(bots):
    bots([5531])
    out = output.open_output("call:5531", 24000, pan=-1)
    assert out.pan_ignored == "a call is mono"
    out.abort()


def test_the_player_speaks_into_the_call(box, bots):  # noqa: F811
    bot = bots([5531])
    item = say("Hello from the mouth.", device="call:5531")
    run_player(box.d).join(10)
    said = [r for r in lines(box.logs) if r["kind"] == "said"]
    assert said and said[0]["utt"] == item["utt"]
    assert said[0]["reason"] == "done" and said[0]["device"] == "call:5531"
    assert bot.lines[0]["header"]["text"] == "Hello from the mouth."
    assert bot.lines[0]["header"]["who"] == "ivy"
    assert bot.lines[0]["ended"] == "done"


def test_the_player_refuses_a_call_that_is_not_live(box, bots):  # noqa: F811
    bots([5531])
    say("Hello?", device="call:9")
    run_player(box.d).join(10)
    said = [r for r in lines(box.logs) if r["kind"] == "said"]
    assert said[0]["reason"] == "device-absent" and "call 9 is not live" in said[0]["detail"]


# -- EARS-156: a slow bot is not an absent one -------------------------------
#
# Call 62216, Sun 2030-06-09: the bot took more than CALL_LOOK_S to
# answer its probe, the timeout read as a stale socket, and a line to the user was
# dropped as "not live; live calls: none". The bounds are scaled down here:
# 0.1 s stands for 1 s, 0.6 s for 4 s.

@pytest.fixture
def fast_bounds(monkeypatch):
    monkeypatch.setattr(output, "CALL_LOOK_S", 0.1)
    monkeypatch.setattr(output, "CALL_SLOW_S", 0.6)


def test_a_slow_probe_still_finds_the_call_and_the_line_plays(box, bots, fast_bounds):  # noqa: F811
    bot = bots([62216], probe_delay=0.25)          # past the first look, inside the second
    item = say("Done, Theo.", device="call:62216")
    run_player(box.d).join(10)
    said = [r for r in lines(box.logs) if r["kind"] == "said"]
    assert said and said[0]["utt"] == item["utt"]
    assert said[0]["reason"] == "done" and said[0]["device"] == "call:62216"
    assert bot.lines[0]["header"]["text"] == "Done, Theo." and bot.lines[0]["ended"] == "done"
    assert bot.probes == 2                         # it looked again


def test_a_slow_header_is_not_an_absent_bot(bots, fast_bounds):
    bot = bots([62216], header_delay=0.25)         # the probe is prompt, the line's header stalls
    out = output.open_output("call:62216", 24000)
    out.write(np.zeros(1200, dtype=np.float32))
    out.drain()
    assert bot.lines[0]["ended"] == "done"


def test_a_truly_absent_call_is_refused_the_same_way_and_quickly(bots, fast_bounds, monkeypatch):
    monkeypatch.setattr(output, "CALL_SLOW_S", 5.0)  # a prompt bot must never cost the long bound
    bot = bots([5531])
    t0 = time.monotonic()
    with pytest.raises(output.DeviceAbsent) as e:
        output.open_output("call:77", 24000)
    assert time.monotonic() - t0 < 1.0
    assert str(e.value) == "call 77 is not live; live calls: call:5531 (ivy)"
    assert bot.probes == 2                         # one extra round of prompt probes, no more


def test_a_bot_that_never_answers_is_named_not_called_absent(box, bots, fast_bounds):  # noqa: F811
    bot = bots([62216], probe_delay=30)
    say("Hello?", device="call:62216")
    run_player(box.d).join(10)
    said = [r for r in lines(box.logs) if r["kind"] == "said"]
    assert said[0]["reason"] == "device-absent"
    assert said[0]["detail"] == ("call 62216 is not live; live calls: none; "
                                 f"no answer in 0.6s from {bot.path}")
