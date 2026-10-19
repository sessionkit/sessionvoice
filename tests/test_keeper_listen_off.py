"""EARS-364: `listen: false` in ears.json stops EVERY ear, not only the room's.

Off means nothing listens (EARS-188 item 6). Before this the keeper stopped the room capture
only, and only while it followed the default input: call-ears and its call legs ran on, and in
pinned-DEVICE mode nothing read `listen` at all. These run the keeper's own functions (the
listen region, with the calls and stop regions it calls) against FAKES only:

- the room capture and every call leg are sleeping pythons with a capture argv, under a pattern
  no live capture matches (EARS_CAPTURE_PAT), started through a shell that exits at once, so
  each is reaped when it dies, as a real one is;
- call-ears is a fake (EARS_CALL_EARS, EARS_CALL_EARS_PAT) that does what makes the order
  matter: it starts a call leg again whenever none runs;
- call-tap's far-end ear is a fake named by a capture.pid under a temp EARS_CALL_TAP_RUN;
- ears.json is a temp file (EARS_CONF).

Every test runs under bash 5 (`bash`) AND macOS's /bin/bash 3.2: the keeper runs under either.
No real capture, call-ears, call-tap or ears.json is ever touched.
"""
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

KEEPER = Path(__file__).resolve().parents[1] / "keeper" / "ears-keeper.sh"
SHELLS = ["bash", "/bin/bash"]
shells = pytest.mark.parametrize("sh", [pytest.param(s, id=s) for s in SHELLS])

# exits once its --stop-file exists, as a healthy capture does (src/ears/listen/loop.py)
HONOURS_STOP = ("import os,sys,time;s=sys.argv[sys.argv.index('--stop-file')+1];"
                "[time.sleep(0.05) for _ in iter(lambda: os.path.exists(s), True)]")
# a call leg as call-ears starts one: no stop file; the default SIGTERM ends it
SLEEPS = "import time;time.sleep(60)"
# a wedged capture: never looks at its stop file, ignores SIGTERM
WEDGED = "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)"

# A call-ears that gives the live call 5 an ear whenever it has none, as the real one restarts an
# ear that dies while its call is live. Stopping the legs BEFORE call-ears would leave one running.
FAKE_CALL_EARS = r'''
import os, subprocess, sys, time
tag = os.environ["FAKE_TAG"]
leg = [sys.executable, "-c", "import time; time.sleep(60)", tag + ".listen", "capture",
       "--source", "call:5", "--session", "call-ears-5"]
while True:
    if subprocess.run(["pgrep", "-f", tag + r"\.listen capture --source call:5"],
                      capture_output=True).returncode != 0:
        subprocess.run(["/bin/sh", "-c", '"$@" </dev/null >/dev/null 2>&1 &', "sh"] + leg)
    time.sleep(0.1)
'''


def _fn(s: str, name: str) -> str:
    """One top-level function's text: from `name() {` to its closing `}` at column 0."""
    a = s.index(f"\n{name}() {{") + 1
    return s[a:s.index("\n}\n", a) + 3]


def _functions() -> str:
    s = KEEPER.read_text()
    return ('note() { echo "NOTE $*"; }\nmail_me() { echo "MAIL $1"; }\n'
            + _fn(s, "conf") + _fn(s, "listen_on")
            + s[s.index("CAPTURE_PAT="):s.index("room_on_call() {")]
            + s[s.index("# --- calls:"):s.index("# --- end calls ---")]
            + s[s.index("# --- stop:"):s.index("# --- end stop ---")]
            + s[s.index("# --- listen:"):s.index("# --- end listen ---")])


def _start_block() -> str:
    """The keeper's start: listen read before any ear starts."""
    s = KEEPER.read_text()
    return s[s.index('last_l="$(listen_on)"'):s.index('prev="$(inputs)"')]


def _loop_top() -> str:
    """The top of the keeper's loop: the listen read, its one note and mail, ears_off/ears_on."""
    s = KEEPER.read_text()
    loop = s[s.index("while :; do"):]
    return loop[loop.index('l="$(listen_on)"'):loop.index('beat_track "$(capture_pid)"')]


def _alive(pid: int) -> bool:
    st = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(st) and not st.startswith("Z")


def _pgrep(pat: str) -> list:
    out = subprocess.run(["pgrep", "-f", pat], capture_output=True, text=True).stdout
    return [int(p) for p in out.split()]


def _wait_for(cond, secs=5.0):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.1)
    return cond()


@pytest.fixture
def rig(tmp_path):
    if not Path("/bin/bash").exists():
        pytest.skip("no /bin/bash")
    tag = f"earstest{uuid.uuid4().hex[:8]}"
    d = tmp_path / tag
    d.mkdir()
    fake_ce = d / "call-ears"
    fake_ce.write_text(FAKE_CALL_EARS)
    conf = tmp_path / "ears.json"
    tap = tmp_path / "tap"
    pids = []

    def spawn(code, *argv):
        out = subprocess.run(["/bin/sh", "-c", '"$@" </dev/null >/dev/null 2>&1 & echo $!', "sh",
                              sys.executable, "-c", code, f"{tag}.listen", "capture", *argv],
                             capture_output=True, text=True, check=True)
        pid = int(out.stdout.strip())
        pids.append(pid)
        return pid

    def run(sh, script, listen=None, timeout=60, **env):
        if listen is not None:
            conf.write_text('{"listen": %s}' % ("true" if listen else "false"))
        time.sleep(0.4)  # let ps see the fakes' argv
        e = dict(os.environ, EARS_CAPTURE_PAT=rf"{tag}\.listen capture", EARS_CONF=str(conf), CONF=str(conf),
                 EARS_CALL_EARS=str(fake_ce), EARS_CALL_EARS_PAT=rf"[Pp]ython[0-9.]* [^ ]*{tag}/call-ears( |$)",
                 CALL_EARS_DIR=str(tmp_path / "ce"), EARS_CALL_TAP_RUN=str(tap), FAKE_TAG=tag,
                 PY=sys.executable, LOGDIR=str(tmp_path), CALLS_PIDF=str(tmp_path / "call-ears.pid"),
                 FOLLOW="1", EARS_STOP_WAIT="1", EARS_TERM_WAIT="1")
        e.update(env)
        out = subprocess.run([sh, "-c", _functions() + "\n" + script], env=e,
                             capture_output=True, text=True, timeout=timeout)
        assert out.returncode == 0, out.stderr
        return out.stdout

    def room():
        return spawn(HONOURS_STOP, "--source", "mic", "--device", "airpods",
                     "--stop-file", str(tmp_path / "STOP-capture-keeper-120000"))

    def tap_ear(code=HONOURS_STOP):
        tap.mkdir(exist_ok=True)
        pid = spawn(code, "--source", "mic", "--device", "BlackHole 2ch", "--session", "call-tap",
                    "--stop-file", str(tap / "STOP-ear"))
        (tap / "capture.pid").write_text(f"{pid}\n")
        return pid

    def legs():
        return _pgrep(rf"{tag}\.listen capture --source call:")

    def call_ears():
        return _pgrep(rf"{tag}/call-ears")

    yield SimpleNamespace(spawn=spawn, run=run, room=room, tap_ear=tap_ear, legs=legs, call_ears=call_ears,
                          tmp=tmp_path, tap=tap, conf=conf, tag=tag)
    subprocess.run(["pkill", "-KILL", "-f", tag])   # this test's own fakes: the tag is unique to it
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


# -- off stops every ear --------------------------------------------------------------------

@shells
def test_off_stops_the_room_call_ears_every_call_leg_and_the_tap_ear(rig, sh):
    room = rig.room()
    ear = rig.tap_ear()
    sox = rig.spawn(SLEEPS, "--not-a-capture")   # call-tap's passthrough stands in: never touched
    out = rig.run(sh, 'calls_up; ce=$calls_pid; '
                      'for _ in $(seq 50); do [ -n "$(call_legs)" ] && break; sleep 0.1; done; '
                      'echo "LEGS BEFORE $(call_legs | wc -l | tr -d " ")"; '
                      'ears_off; sleep 1; '   # a call-ears still alive would have started a leg again by now
                      'echo "CE $(pid_alive "$ce" && echo alive || echo gone) [$calls_pid]"', listen=False)
    assert "LEGS BEFORE 1" in out
    assert "CE gone []" in out
    assert rig.legs() == [] and rig.call_ears() == []
    assert not _alive(room) and not _alive(ear)
    assert _alive(sox), "the passthrough is not an ear: listen:false leaves it running"
    assert (rig.tap / "LISTEN-OFF").exists()
    assert not (rig.tmp / "ce" / "STOP").exists(), "never the human's stop: it would block the resume"
    assert "NOTE listening off: stopping call ears" in out
    assert "call leg" in out and "call-tap's far-end ear" in out
    # call-ears FIRST: a live one restarts a leg that dies under it (the next test shows the fake does)
    assert out.index("stopping call ears") < out.index("stopping call leg")


@shells
def test_call_ears_goes_first_or_its_leg_comes_back(rig, sh):
    """The order is load-bearing: stop the legs first and the call-ears restarts one."""
    out = rig.run(sh, 'calls_up; for _ in $(seq 50); do [ -n "$(call_legs)" ] && break; sleep 0.1; done; '
                      'for p in $(call_legs); do kill -TERM "$p"; done; sleep 1; '
                      'echo "BACK $(call_legs | wc -l | tr -d " ")"; calls_down', listen=False)
    assert "BACK 1" in out   # the fake is real enough: a leg killed under a live call-ears returns


@shells
def test_a_leg_with_a_stop_file_is_stopped_by_it_and_a_wedged_one_by_sigkill(rig, sh):
    polite = rig.spawn(HONOURS_STOP, "--source", "call:7", "--stop-file", str(rig.tmp / "STOP-leg7"))
    wedged = rig.spawn(WEDGED, "--source", "call:8")
    out = rig.run(sh, "ears_off", listen=False)
    assert f"stopping call leg {polite} via {rig.tmp / 'STOP-leg7'}" in out
    assert f"call leg {wedged} still running 1 s after SIGTERM: SIGKILL" in out
    assert not _alive(polite) and not _alive(wedged)


@shells
def test_off_with_nothing_running_starts_nothing_and_says_nothing(rig, sh):
    out = rig.run(sh, 'ears_off; ears_off; echo "CE [$calls_pid]"', listen=False)
    assert out.strip().splitlines() == ["CE []"]
    assert rig.call_ears() == [] and rig.legs() == []
    assert (rig.tap / "LISTEN-OFF").exists(), "a tap started later must start paused"


@shells
def test_a_stale_tap_pid_file_signals_no_one(rig, sh):
    """capture.pid naming a process that is not call-tap's ear (pid reuse) is left alone."""
    other = rig.spawn(SLEEPS, "--source", "mic", "--stop-file", str(rig.tmp / "STOP-other"))
    rig.tap.mkdir()
    (rig.tap / "capture.pid").write_text(f"{other}\n")
    out = rig.run(sh, "ears_off", listen=False)
    assert _alive(other) and "far-end ear" not in out


# -- the keeper's start ---------------------------------------------------------------------

@shells
def test_a_keeper_started_off_starts_no_ear_and_says_so_once(rig, sh):
    room = rig.room()   # what the last keeper left running: off means it stops too
    out = rig.run(sh, _start_block() + '\necho "CE [$calls_pid] L $last_l"', listen=False)
    assert out.count("listening OFF by ears.json: starting no ear") == 1
    assert "CE [] L 0" in out
    assert rig.call_ears() == [] and not _alive(room)
    assert (rig.tap / "LISTEN-OFF").exists()


@shells
def test_a_keeper_started_on_starts_call_ears_and_frees_the_tap(rig, sh):
    rig.tap.mkdir()
    (rig.tap / "LISTEN-OFF").touch()   # a keeper that died while off left the tap paused
    out = rig.run(sh, _start_block() + '\necho "CE [$calls_pid] L $last_l"; calls_down', listen=True)
    assert "NOTE call ears: up, pid" in out and "L 1" in out and "CE []" not in out
    assert not (rig.tap / "LISTEN-OFF").exists()
    assert "starting no ear" not in out


# -- the loop: one note and one mail per change; on resumes -----------------------------------

def _loop(top: str, rounds: list) -> str:
    """Run the loop's top once per entry of rounds: 1 (on) or 0 (off), writing ears.json first."""
    body = []
    for r in rounds:
        body.append(f"printf '%s' '{{\"listen\": {'true' if r else 'false'}}}' >\"$EARS_CONF\"")
        body.append(top)
        body.append(f'echo "ROUND {r} CE [$calls_pid] LEGS $(call_legs | wc -l | tr -d " ")"; sleep 0.6')
    return 'last_l=1; seen=0\n' + "\n".join(body) + "\ncalls_down\n"


@shells
def test_one_note_and_one_mail_per_change_never_per_loop(rig, sh):
    out = rig.run(sh, _loop(_loop_top(), [1, 0, 0, 0, 1, 1, 1]), listen=True)
    assert out.count("NOTE listen: 1 -> 0 (ears.json)") == 1
    assert out.count("NOTE listen: 0 -> 1 (ears.json)") == 1
    mails = [l for l in out.splitlines() if l.startswith("MAIL ")]
    assert len(mails) == 2
    assert mails[0].startswith("MAIL ears: listening OFF by ears.json at ")
    assert mails[1].startswith("MAIL ears: listening ON by ears.json at ")
    assert out.count("NOTE listening off: stopping call ears") == 1   # loops 2 and 3 find nothing


@shells
def test_listen_true_resumes_call_ears_and_the_live_call_gets_its_ear(rig, sh):
    out = rig.run(sh, _loop(_loop_top(), [1, 0, 1]), listen=True)
    rounds = [l for l in out.splitlines() if l.startswith("ROUND ")]
    assert "CE []" not in rounds[0]
    assert rounds[1].startswith("ROUND 0 CE [] LEGS 0")
    assert "CE []" not in rounds[2], "listen:true starts call-ears again"
    # the resumed call-ears gives the live call an ear, as call-ears' main() does
    assert out.count("NOTE call ears: up, pid") == 2
    assert _wait_for(lambda: rig.legs() != []), "the live call has an ear again"
    assert not (rig.tap / "LISTEN-OFF").exists()


@shells
def test_pinned_device_mode_honours_listen_false(rig, sh):
    room = rig.room()
    out = rig.run(sh, 'FOLLOW=0; echo "L $(listen_on)"\n' + _loop(_loop_top(), [0]), listen=False)
    assert "L 0" in out
    assert "NOTE listen: 1 -> 0 (ears.json)" in out
    assert not _alive(room)


@pytest.mark.parametrize("text, want", [('{"listen": false}', "0"), ('{"listen": true}', "1"),
                                        ("{}", "1"), ("not json", "1"), ('{"listen": 0}', "1")])
@shells
def test_listen_on_reads_ears_json_in_both_modes(rig, sh, text, want):
    rig.conf.write_text(text)
    out = rig.run(sh, 'FOLLOW=1; echo "F $(listen_on)"; FOLLOW=0; echo "D $(listen_on)"')
    assert f"F {want}" in out and f"D {want}" in out


# -- the keeper against a real bin/call-tap (fake sox, fake capture) -------------------------

@shells
def test_the_keeper_pauses_a_real_call_tap_ear_and_lets_it_back(rig, sh, tmp_path):
    call_tap = KEEPER.parents[1] / "bin" / "call-tap"
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    starts = tmp_path / "starts"
    (stubs / "sox").write_text("#!/bin/sh\nexec sleep 120\n")
    (stubs / "python").write_text(
        '#!/bin/sh\necho start >>"%s"\ns=""; prev=""\n'
        'for a in "$@"; do [ "$prev" = "--stop-file" ] && s="$a"; prev="$a"; done\n'
        'while [ ! -e "$s" ]; do sleep 0.1; done\n' % starts)
    for f in ("sox", "python"):
        (stubs / f).chmod(0o755)
    model = tmp_path / "vad.onnx"
    model.write_bytes(b"onnx")
    state = tmp_path / "state"
    env = {k: v for k, v in os.environ.items() if not k.startswith("CALL_TAP_")}
    env.update(HOME=str(tmp_path), XDG_STATE_HOME=str(state), EARS_PY=str(stubs / "python"),
               EARS_SILERO_MODEL=str(model), PATH=f"{stubs}:/usr/bin:/bin")
    run_dir = state / "ears" / "call-tap"
    up = subprocess.Popen([sh, str(call_tap), "up"], env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, start_new_session=True)
    try:
        assert _wait_for(lambda: starts.exists() and (run_dir / "capture.pid").exists())
        sox = int((run_dir / "sox.pid").read_text())
        out = rig.run(sh, "tap_off", listen=False, EARS_CALL_TAP_RUN=str(run_dir))
        assert "call-tap's far-end ear (its passthrough runs on)" in out
        time.sleep(1.5)
        assert starts.read_text().count("start") == 1 and _alive(sox)
        out = rig.run(sh, "tap_on", EARS_CALL_TAP_RUN=str(run_dir))
        assert "far-end ear may run again" in out
        assert _wait_for(lambda: starts.read_text().count("start") == 2)
        subprocess.run([sh, str(call_tap), "down"], env=env, check=True, capture_output=True)
        up.wait(timeout=10)
        assert not _alive(sox)
    finally:
        if up.poll() is None:
            os.killpg(up.pid, signal.SIGKILL)
            up.wait()
