"""EARS-285: the lead buffer. A line asked for a lead holds its first frame
until ``lead_s`` of its audio is buffered, so a slow render eats into the
lead and not into the line.

fix-002: the lead is a SETTING, default 0 for
every backend, clone included. A caller asks per line: ``mouth say --lead S``,
``mouth.say(lead_s=S)`` or a mail's ``X-Mouth-Lead: S``; else ``lead_s`` in
the caller's cascade (session, seat, agent), then the host settings file.

The player is driven for real (``player.serve`` / ``play_one``, the null
output) with ``bench/clone_underrun.py``'s :class:`CloneLike`, small chunks
and a short lead so each test takes a second or two. No server, no sound.
"""
from __future__ import annotations

import email
import email.policy
import json
import os
import signal
import subprocess
import sys
import threading
import time
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace

import pytest

import mouth
from bench.clone_underrun import CloneLike
from mouth import __main__ as cli
from mouth import backends, box as mbox, player, voices

from maildir_put import put
from tests import env_fence


@pytest.fixture
def box(tmp_path, monkeypatch):
    d, logs = tmp_path / "mouth", tmp_path / "logs"
    monkeypatch.setenv("MOUTH_DIR", str(d))
    monkeypatch.setenv("MOUTH_LOG_DIR", str(logs))
    monkeypatch.setenv("VOICEMODE_AGENT", "ivy")
    monkeypatch.setenv("VOICEMODE_SESSION_ID", "s-test")
    monkeypatch.setenv("MOUTH_RESUME_S", "0")
    monkeypatch.setenv("MOUTH_BARGE_DEVICES", "")
    for k in ("MOUTH_SETTINGS", "MOUTH_MAILBOX", "MOUTH_MAIL_LOG"):
        env_fence.delenv(monkeypatch, k)   # both names (EARS-294)
    return SimpleNamespace(d=d, logs=logs)


def settings_file(box, **kw) -> None:
    box.d.mkdir(parents=True, exist_ok=True)
    (box.d / "settings.json").write_text(json.dumps(kw))


def records(logs: Path, utt: str) -> dict:
    rows = [json.loads(x) for f in sorted(logs.glob("heard_*.jsonl")) for x in f.read_text().splitlines()]
    return {r["kind"]: r for r in rows if r.get("utt") == utt}


def serve(box, wait=15.0, stop_after=None) -> float:
    t0 = time.monotonic()
    th = threading.Thread(target=player.serve, kwargs={"d": box.d, "idle_exit_s": 0.3}, daemon=True)
    th.start()
    if stop_after is not None:
        time.sleep(stop_after)
        mouth.stop()
    th.join(wait)
    assert not th.is_alive()
    return time.monotonic() - t0


def speak(box, monkeypatch, fake, text="a clone line", stop_after=None, **say):
    """Say one line with ``fake`` as its backend; return (saying, said, wall seconds)."""
    monkeypatch.setattr(backends, "resolve", lambda b, v: (fake, v))
    item = mouth.say(text, spawn=False, device="null", backend="clone", **say)
    wall = serve(box, stop_after=stop_after)
    got = records(box.logs, item["utt"])
    return got.get("saying"), got.get("said"), wall


def slow():
    """Render at 0.8x real time (rtf 1.25), 0.1 s chunks, a 1.5 s line: with
    no lead it dries out on every chunk; a 0.6 s lead covers the 0.35 s it needs."""
    return CloneLike(dur_s=1.5, rtf=1.25, interval_s=0.1)


# -- the default: no lead, for every backend ---------------------------------

def test_the_default_lead_is_zero_for_every_backend(box):
    assert voices.BUILTIN_LEAD_S == 0.0
    for b in ("clone", "kokoro", "apple", "silence", "auto"):
        assert voices.lead_setting(item={"backend": b}, d=box.d) == (0.0, "builtin")
    kokoro, _ = backends.resolve("kokoro", "af_sky")
    assert not hasattr(kokoro, "lead_buffer_s")     # a backend asks for nothing (fix-001 did)


def test_a_clone_line_with_no_override_holds_nothing(box, monkeypatch):
    """The slow clone line, nothing asked: it plays as it streams (today's
    behaviour), so it starts on its first chunk and dries out."""
    saying, said, _ = speak(box, monkeypatch, slow())
    assert "lead_s" not in saying and "held_s" not in saying
    assert saying["gen_s"] == pytest.approx(0.125, abs=0.08)
    assert said["underruns"] > 0 and said["underrun_s"] > 0.2


# -- a line's own lead: python, CLI, mail -------------------------------------

def test_a_lines_own_lead_rides_out_a_slow_render(box, monkeypatch):
    saying, said, _ = speak(box, monkeypatch, slow(), lead_s=0.6)
    assert (saying["lead_s"], saying["lead_from"]) == (0.6, "flag")
    assert saying["held_s"] == pytest.approx(0.625, abs=0.12)
    assert said["reason"] == "done" and said["played_s"] == pytest.approx(1.5, abs=0.06)
    assert (said["underruns"], said["underrun_s"]) == (0, 0)


def test_a_lines_own_lead_is_for_that_line_only(box, monkeypatch):
    fake = slow()
    monkeypatch.setattr(backends, "resolve", lambda b, v: (fake, v))
    one = mouth.say("held line", spawn=False, device="null", backend="clone", lead_s=0.6)
    two = mouth.say("next line", spawn=False, device="null", backend="clone")
    assert one["lead_s"] == 0.6 and "lead_s" not in two
    serve(box, wait=20)
    a, b = records(box.logs, one["utt"]), records(box.logs, two["utt"])
    assert a["saying"]["lead_s"] == 0.6 and a["saying"]["held_s"] > 0.3
    assert "lead_s" not in b["saying"] and "held_s" not in b["saying"]


def test_a_lines_lead_of_zero_beats_the_setting(box, monkeypatch):
    settings_file(box, lead_s=0.6)
    saying, said, _ = speak(box, monkeypatch, slow(), lead_s=0)
    assert "held_s" not in saying
    assert said["underruns"] > 0


@pytest.mark.parametrize("bad", [-1, "nonsense", float("nan"), float("inf"), True])
def test_a_bad_lead_is_refused_before_anything_is_written(box, bad):
    with pytest.raises(ValueError, match="lead is seconds"):
        mouth.say("x", spawn=False, device="null", lead_s=bad)
    assert not (box.d / "box" / "new").exists() or not any((box.d / "box" / "new").iterdir())


def reap(procs, grace: float = 5.0) -> None:
    """Stop each process BY ITS PID and its own group (``start_new_session``
    makes it a group leader): SIGTERM, wait ``grace``, then SIGKILL. The wait
    reaps it, so no zombie keeps the pid. Never a pattern kill, and never a
    signal to a pid we already reaped: the OS may have handed it on."""
    for p in procs:
        if p.poll() is not None:
            continue
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(p.pid, sig)
            except ProcessLookupError:
                pass
            try:
                p.wait(grace)
                break
            except subprocess.TimeoutExpired:
                continue


def group_gone(pgid: int, within: float = 3.0) -> bool:
    """True once no process is left in group ``pgid`` (an orphaned grandchild
    is a zombie until launchd reaps it, so give it a moment)."""
    end = time.monotonic() + within
    while True:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        if time.monotonic() > end:
            return False
        time.sleep(0.05)


@pytest.fixture
def reap_serve(monkeypatch):
    """EARS-302: ``mouth say`` starts a real ``mouth serve`` (``ensure_player``)
    that nothing stopped, so it outlived the test re-parented to launchd with
    the fenced HOME. Wrap ``subprocess.Popen`` so the server still starts for
    real, keep its handle, and at teardown (which runs when the test fails
    too) reap it by that pid and assert nothing of it is left."""
    real, spawned = subprocess.Popen, []

    def popen(args, *a, **k):
        p = real(args, *a, **k)
        if list(args)[-3:] == ["-m", "mouth", "serve"]:
            spawned.append(p)
        return p

    monkeypatch.setattr(subprocess, "Popen", popen)
    yield spawned
    reap(spawned)
    for p in spawned:
        assert p.poll() is not None, f"mouth serve {p.pid} outlived the test"
        assert group_gone(p.pid), f"mouth serve {p.pid} left its group behind"


@pytest.fixture
def child():
    """Start a python child leading its own group, as ``ensure_player`` starts
    one; it prints ``ready`` once ``code`` has run. Whatever a failing test
    left is SIGKILLed by its pid at teardown."""
    made = []

    def start(code: str) -> subprocess.Popen:
        p = subprocess.Popen([sys.executable, "-c", code + "\nprint('ready', flush=True)\n"
                              "import time\ntime.sleep(60)"],
                             stdout=subprocess.PIPE, stdin=subprocess.DEVNULL,
                             start_new_session=True)
        made.append(p)
        assert p.stdout.readline().strip() == b"ready"
        return p

    yield start
    reap(made, grace=0.5)
    for p in made:
        p.stdout.close()


def test_reap_stops_a_child_that_ignores_sigterm(child):
    p = child("import signal\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)")
    t0 = time.monotonic()
    reap([p], grace=0.5)
    assert p.returncode == -signal.SIGKILL and time.monotonic() - t0 < 3
    assert group_gone(p.pid)


def test_reap_takes_the_childs_whole_group(child):
    p = child("import subprocess, sys\n"
              "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])")
    reap([p])
    assert p.returncode == -signal.SIGTERM
    assert group_gone(p.pid)                # the grandchild went with it


def test_reap_never_signals_a_child_already_reaped(monkeypatch):
    p = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    p.wait(10)
    sent = []
    monkeypatch.setattr(os, "killpg", lambda *a: sent.append(a))
    reap([p], grace=0.5)                    # its pid may be someone else's by now
    assert sent == [] and p.returncode == 0


def test_say_lead_on_the_cli(box, capsys, reap_serve):
    assert cli.main(["say", "--device", "null", "--backend", "silence", "--lead", "2.5", "hi"]) == 0
    assert len(reap_serve) == 1             # the say started one server, and the fixture holds it
    utt = capsys.readouterr().out.strip()
    (f,) = (box.d / "box" / "new").iterdir()
    m = email.message_from_bytes(f.read_bytes(), policy=email.policy.default)
    assert m["X-Mouth-Utt"] == utt and m["X-Mouth-Lead"] == "2.5"
    back = mbox.read(f, box.d)
    assert (back["lead_s"], back["lead_s_from"]) == (2.5, "flag")
    assert voices.lead_setting(item=back, d=box.d) == (2.5, "flag")


def test_a_bad_cli_lead_exits_2(box, capsys):
    assert cli.main(["say", "--device", "null", "--lead", "-1", "hi"]) == 2
    assert "lead is seconds" in capsys.readouterr().err


def outside_mail(box, lead: str) -> Path:
    m = EmailMessage(policy=email.policy.default)
    m["From"], m["To"], m["Subject"] = "ivy@host1", "mouth@host1", "x"
    m["Message-ID"] = "<lead-1@host1>"
    m["X-Mouth-Lead"] = lead
    m.set_content("Hello Theo.\n")
    return put(box.d / "box", "1.lead.host1", bytes(m))


def test_a_mail_asks_for_a_lead_with_x_mouth_lead(box):
    back = mbox.read(outside_mail(box, "1.5"), box.d)
    assert (back["lead_s"], back["lead_s_from"]) == (1.5, "mail")
    assert voices.lead_setting(item=back, d=box.d) == (1.5, "mail")


def test_a_bad_x_mouth_lead_falls_to_the_setting(box):
    settings_file(box, lead_s=0.8)
    for bad in ("-2", "soon"):
        back = mbox.read(outside_mail(box, bad), box.d)
        assert voices.lead_setting(item=back, d=box.d) == (0.8, "settings")


# -- the setting: the host file and the cascade -------------------------------

def test_the_setting_gives_every_line_a_lead(box, monkeypatch):
    settings_file(box, lead_s=0.6)
    saying, said, _ = speak(box, monkeypatch, slow())
    assert (saying["lead_s"], saying["lead_from"]) == (0.6, "settings")
    assert saying["held_s"] == pytest.approx(0.625, abs=0.12)
    assert (said["underruns"], said["underrun_s"]) == (0, 0)


@pytest.mark.parametrize("bad", [-1, "2.5", None, True, [2]])
def test_a_bad_setting_is_the_default(box, bad):
    settings_file(box, lead_s=bad)
    assert voices.lead_setting(item={"backend": "clone"}, d=box.d) == (0.0, "builtin")


def test_a_seats_cascade_lead_rides_on_the_line(box, monkeypatch):
    settings_file(box, lead_s=0.2)
    monkeypatch.setattr(voices, "cascade", lambda sid, keys: {"lead_s": (0.6, "seat")}
                        if "lead_s" in keys else {})
    item = mouth.say("x", spawn=False, device="null", backend="clone")
    assert (item["lead_s"], item["lead_s_from"]) == (0.6, "seat")
    assert voices.lead_setting(item=item, d=box.d) == (0.6, "seat")   # over the host's 0.2
    flag = mouth.say("y", spawn=False, device="null", backend="clone", lead_s=1)
    assert (flag["lead_s"], flag["lead_s_from"]) == (1.0, "flag")      # the line's own wins


def test_a_played_file_never_holds(box):
    settings_file(box, lead_s=2.5)
    assert voices.lead_setting(item={"backend": "file", "file": "x.wav"}, d=box.d) == (0.0, "file")


# -- the hold itself, through the real player ---------------------------------

def test_synth_wait_for_says_when_the_lead_is_buffered(monkeypatch):
    fake = CloneLike(dur_s=0.6, rtf=1.0, interval_s=0.1)
    monkeypatch.setattr(backends, "resolve", lambda b, v: (fake, v))
    s = player.Synth({"utt": "u", "text": "x", "backend": "clone"})
    need = int(0.3 * fake.sample_rate)
    assert s.wait_for(need, tick=0.01) is False   # the first chunk is 0.1 s away
    t0 = time.monotonic()
    while not s.wait_for(need, tick=0.01):
        pass
    assert s.frames >= need and time.monotonic() - t0 < 0.6
    while not s.wait_for(10 ** 9, tick=0.05):     # more than the line: true once it ends
        pass
    assert s.done and s.finished and s.frames == int(0.6 * fake.sample_rate)


def test_a_line_holds_its_first_frame_for_the_lead_and_logs_it(box, monkeypatch):
    """Real-time render (rtf 1.0), 0.1 s chunks, a 0.6 s lead: the first frame
    waits for 6 chunks, so ~0.5 s after the first one, and nothing dries out."""
    saying, said, _ = speak(box, monkeypatch, CloneLike(dur_s=1.5, rtf=1.0, interval_s=0.1),
                            lead_s=0.6)
    assert saying["gen_s"] == pytest.approx(0.1, abs=0.08)
    assert saying["held_s"] == pytest.approx(0.5, abs=0.12)
    assert said["reason"] == "done" and said["played_s"] == pytest.approx(1.5, abs=0.06)
    assert (said["underruns"], said["underrun_s"]) == (0, 0)


def test_a_line_shorter_than_its_lead_renders_whole_then_plays(box, monkeypatch):
    saying, said, _ = speak(box, monkeypatch, CloneLike(dur_s=0.5, rtf=1.0, interval_s=0.1),
                            lead_s=2.5)
    assert saying["held_s"] == pytest.approx(0.4, abs=0.12)   # the rest of the line, then play
    assert said["reason"] == "done" and said["played_s"] == pytest.approx(0.5, abs=0.06)
    assert said["dur_s"] == pytest.approx(0.5, abs=0.01)
    assert (said["underruns"], said["underrun_s"]) == (0, 0)


def test_a_stop_cuts_a_line_while_it_holds(box, monkeypatch):
    """Stop, retract, --now and barge-in must still cut a line in its lead."""
    saying, said, wall = speak(box, monkeypatch, CloneLike(dur_s=4.0, rtf=1.0, interval_s=0.3),
                               stop_after=0.8, lead_s=2.5)
    assert saying is None                     # never started: no first frame
    assert said["reason"] == "stop" and said["played_s"] == 0.0
    assert wall < 2.0                         # cut in the hold, not after 2.5 s of it


def test_a_fast_backend_with_a_lead_starts_at_once(box, monkeypatch):
    saying, said, _ = speak(box, monkeypatch, backends.Silence(), text="Hello Theo.", lead_s=2.5)
    assert saying["held_s"] < 0.1
    assert said["reason"] == "done"
