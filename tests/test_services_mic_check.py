"""`ears services install` checks the microphone while the user is there.

After it loads the keeper, with listening on, install waits (bounded) for the keeper's room
capture to write a heartbeat with sound. Heard: one row, exit unchanged. Only silence, no
beat, or a failed capture at the bound: ONE stderr line naming System Settings > Privacy &
Security > Microphone and the app macOS asks about, exit 2. Every case where it cannot
check says so in one row, exit unchanged.

Everything runs inside test_services_install_checks' fence: a temp HOME, launchctl faked,
and the check's seams (the heard reader, the inputs, ps, its clock and sleep) faked by the
fence. No microphone is opened, and nothing waits in real time."""
import json
import subprocess
import sys

import pytest

from ears import mic_check as mc
from ears import services
from ears.home import SETTINGS
from test_services_install_checks import MIC_HEARD, _store, fence  # noqa: F401  (a fixture)

PRIVACY = "System Settings > Privacy & Security > Microphone"
KEEPER = services._label("keeper")


def _conf(fence, d=None):
    """ears.json as the user left it: listening on (no listen key reads as on) unless D says."""
    p = fence["home"] / SETTINGS / "ears.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"input": "default"} if d is None else d))
    return p


def _bound():
    return mc.bound()[0]


def _beat(peak, **kw):
    return {**MIC_HEARD, "peak": peak, **kw}


def _install(capsys, *argv):
    capsys.readouterr()
    rc = services.main(["install", "--only", "keeper", *argv])
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


def _mic_rows(out):
    return [ln for ln in out.splitlines() if ln.startswith("mic ")]


def _fail_lines(err):
    return [ln for ln in err.splitlines() if ln.startswith("ears services install: mic ")]


def _no_seams(fence, monkeypatch):
    """Every seam of the check fails if called: the case must be decided without them."""
    def boom(*a, **kw):
        raise AssertionError("the mic check touched a seam it must not")
    for name in ("_mic_read", "_mic_inputs", "_mic_comm"):
        monkeypatch.setattr(services, name, boom)


# -- the README's success criteria: zeros, signal, error or nothing ----------------------


def test_install_names_the_privacy_setting_and_exits_2_on_zeros(fence, capsys):
    _conf(fence)
    fence["mic"]["polls"] = [[_beat(0)], [], [_beat(0)], [], [_beat(0)]]
    rc, out, err = _install(capsys)
    assert rc == 2
    [line] = _fail_lines(err)
    assert line == mc.fail(mc.NOT_HEARD_LINE.format(bound=mc.secs(_bound()), why="only silence",
                                                    app="python3.13"))
    assert PRIVACY in line and "python3.13" in line
    assert len(err.strip().splitlines()) == 1, "ONE line on stderr"
    assert _bound() <= fence["mic"]["t"] <= _bound() + mc.POLL_S, "judged at the bound, and no later"
    assert fence["mic"]["pids"] == [4242], "the app is named from the capture's own pid"


def test_install_says_heard_and_keeps_its_exit_on_signal(fence, monkeypatch, capsys):
    _conf(fence)
    _install(capsys)          # once first, so the two runs compared are both re-installs
    fence["mic"]["t"] = 0.0
    fence["mic"]["polls"] = [[_beat(0)], [], [_beat(1834)]]
    rc, out, err = _install(capsys)
    assert rc == 0 and not _fail_lines(err)
    assert _mic_rows(out)[-1] == "mic    heard: MacBook Pro Microphone (peak 1834), 2 s after the keeper loaded"
    assert fence["mic"]["t"] < _bound(), "heard is said at once"
    # the rest of stdout is what an unchecked run prints: only mic rows differ
    monkeypatch.setenv("EARS_MIC_CHECK_S", "0")
    _, off_out, _ = _install(capsys)

    def rest(o):
        return [ln for ln in o.splitlines() if not ln.startswith("mic ")]
    assert rest(out) == rest(off_out)


@pytest.mark.parametrize("polls, why", [
    ([[{**MIC_HEARD, "event": "listen-started"}],
      [{**MIC_HEARD, "event": "listen-stopped", "reason": "error", "error": "cannot open"}]], "the capture failed"),
    ([[{**MIC_HEARD, "event": "capture-wedged", "why": "no frames"}]], "the capture failed"),
    ([], "no sound arrived"),
], ids=["error", "wedged", "nothing"])
def test_install_exits_2_on_an_error_or_nothing_within_the_bound(fence, monkeypatch, capsys, polls, why):
    _conf(fence)
    monkeypatch.setenv("EARS_PY", "/opt/fake/bin/python3.13")   # no row named a pid: install's EARS_PY
    fence["mic"]["polls"] = polls
    rc, out, err = _install(capsys)
    assert rc == 2
    [line] = _fail_lines(err)
    assert f"({why})" in line and PRIVACY in line and "turn on python3.13 " in line
    assert _bound() <= fence["mic"]["t"] <= _bound() + mc.POLL_S


def test_the_wait_line_comes_first_and_names_the_bound(fence, capsys):
    _conf(fence)
    rc, out, _ = _install(capsys)
    rows = _mic_rows(out)
    assert rows[0] == mc.waiting(_bound())
    assert f"up to {mc.secs(_bound())} s" in rows[0] and "click Allow" in rows[0]
    assert rows[1].startswith("mic    heard: ") and rc == 0


def test_a_bound_from_the_environment_is_used(fence, monkeypatch, capsys):
    _conf(fence)
    monkeypatch.setenv("EARS_MIC_CHECK_S", "7")
    fence["mic"]["polls"] = []
    rc, _, err = _install(capsys)
    assert rc == 2 and "NOT HEARD in 7 s" in err and 7 <= fence["mic"]["t"] <= 7 + mc.POLL_S


def test_a_bound_that_is_not_seconds_is_the_default_and_says_so(fence, monkeypatch, capsys):
    _conf(fence)
    monkeypatch.setenv("EARS_MIC_CHECK_S", "soon")
    _, out, _ = _install(capsys)
    assert f"(EARS_MIC_CHECK_S=soon is not seconds: {mc.secs(mc.BOUND_S)} s)" in _mic_rows(out)[0]


def test_the_check_reads_from_the_heard_logs_end_before_the_keeper_loaded(fence, monkeypatch, capsys):
    from ears.listen import HeardSink
    d = mc.heard_dir()
    for _ in range(3):
        HeardSink(d).write({"kind": "event", "event": "heartbeat", "source": "mic",
                                  "session": "ears-keeper", "peak": 9})
    seen = []
    inner = services._mic_read

    def read(seq, **kw):
        seen.append(seq)
        assert kw["directory"] == d
        return inner(seq, **kw)
    monkeypatch.setattr(services, "_mic_read", read)
    _conf(fence)
    _install(capsys)
    assert seen and seen[0] == 3


# -- the lid, a call, the pin, no input: decided without the Privacy line ---------------------


def test_a_shut_lid_names_the_lid_and_exits_2_at_once(fence, capsys):
    _conf(fence)
    fence["mic"]["polls"] = [[{"kind": "event", "event": "ears-deaf", "device": "MacBook Pro Microphone",
                               "text": "room: lid: shut"}]]
    rc, _, err = _install(capsys)
    assert rc == 2
    assert _fail_lines(err) == [mc.fail(mc.LID_LINE)]
    assert PRIVACY not in err and fence["mic"]["t"] < _bound()


def test_a_live_call_is_not_checked_and_keeps_the_exit(fence, capsys):
    _conf(fence)
    fence["mic"]["polls"] = [[{"kind": "event", "event": "room-off", "device": "call:2",
                               "text": "call live: the room mic is off until hang-up"}]]
    rc, out, err = _install(capsys)
    assert rc == 0 and not _fail_lines(err)
    assert _mic_rows(out)[-1] == "mic    not checked: a call is live, so the room mic is off until it ends"


def test_no_input_device_is_not_checked_and_waits_for_nothing(fence, capsys):
    _conf(fence)
    fence["mic"]["inputs"] = []
    rc, out, err = _install(capsys)
    assert rc == 0 and not _fail_lines(err)
    assert _mic_rows(out) == ["mic    not checked: this Mac has no microphone (no audio input device)"]
    assert fence["mic"]["reads"] == 0 and fence["mic"]["t"] == 0


def test_no_input_device_with_listen_keeps_the_exit_too(fence, capsys):
    fence["mic"]["inputs"] = []
    rc, out, _ = _install(capsys, "--listen")
    assert rc == 0 and mc.row(mc.NC_NO_INPUT) in _mic_rows(out)


def test_a_pinned_input_that_is_not_connected_is_named_and_exits_2_at_once(fence, capsys):
    _conf(fence, {"input": "Yeti Stereo Microphone"})
    rc, out, err = _install(capsys)
    assert rc == 2
    assert _fail_lines(err) == [
        "ears services install: mic NOT HEARD: ears.json pins the input to Yeti Stereo Microphone, which is "
        "not connected; plug in Yeti Stereo Microphone, or remove the pin from ears.json, then run: "
        "ears services install --only keeper"]
    assert PRIVACY not in err and fence["mic"]["reads"] == 0, "no wait out of the bound: the right fix"


def test_a_pinned_input_that_is_connected_is_checked_as_usual(fence, capsys):
    _conf(fence, {"input": "macbook pro microphone"})   # the keeper matches any case
    rc, out, _ = _install(capsys)
    assert rc == 0 and _mic_rows(out)[-1].startswith("mic    heard: ")


def test_unknown_inputs_wait_as_usual(fence, capsys):
    _conf(fence, {"input": "Yeti Stereo Microphone"})
    fence["mic"]["inputs"] = None
    rc, out, _ = _install(capsys)
    assert rc == 0 and fence["mic"]["reads"] >= 1


# -- the cases where it does not check: one row each, exit unchanged ------------------------


def test_listening_off_is_said_verbatim_and_keeps_the_exit(fence, monkeypatch, capsys):
    _no_seams(fence, monkeypatch)
    rc, out, err = _install(capsys)          # a fresh install without --listen
    assert rc == 0 and not _fail_lines(err)
    assert _mic_rows(out) == ["mic    not checked: listening is off; install with --listen to check now"]


def test_an_existing_listen_false_is_off_too(fence, monkeypatch, capsys):
    _conf(fence, {"listen": False})
    _no_seams(fence, monkeypatch)
    rc, out, _ = _install(capsys)
    assert rc == 0 and _mic_rows(out) == [mc.row(mc.NC_LISTEN_OFF)]


def test_listen_on_a_fresh_install_runs_the_check(fence, capsys):
    rc, out, _ = _install(capsys, "--listen")
    assert rc == 0 and _mic_rows(out)[-1].startswith("mic    heard: ")


def test_the_off_switch_skips_the_check(fence, monkeypatch, capsys):
    _conf(fence)
    monkeypatch.setenv("EARS_MIC_CHECK_S", "0")
    _no_seams(fence, monkeypatch)
    rc, out, err = _install(capsys)
    assert rc == 0 and not _fail_lines(err)
    assert _mic_rows(out) == ["mic    not checked: EARS_MIC_CHECK_S=0"]


def test_only_with_no_keeper_is_not_checked(fence, monkeypatch, capsys):
    _no_seams(fence, monkeypatch)
    capsys.readouterr()
    rc = services.main(["install", "--only", "mouth"])
    out = capsys.readouterr().out
    assert rc == 0 and _mic_rows(out) == ["mic    not checked: --only has no keeper"]


@pytest.mark.parametrize("argv", [["--agent", "dave"], ["--only", "keeper,mouth"]])
def test_a_dry_run_whose_keeper_was_refused_says_no_keeper_in_this_run(fence, monkeypatch, capsys, argv):
    _no_seams(fence, monkeypatch)
    _store(fence["home"])
    monkeypatch.setattr(services, "_shipped_refusals",
                        lambda names: [("keeper", "the keeper script is not shipped (stand-in)")])
    capsys.readouterr()
    rc = services.main(["install", "--dry-run", *argv])
    cap = capsys.readouterr()
    assert rc == 2 and "REFUSED" in cap.err
    assert _mic_rows(cap.out) == ["mic    not checked: no keeper in this run"]
    assert "--only has no keeper" not in cap.out


def _hand_keeper(monkeypatch):
    label = "dev.someone.my-ears"
    monkeypatch.setattr(services, "_hand", lambda uid, n: (label, services.AGENTS / f"{label}.plist")
                        if n == "keeper" else None)
    monkeypatch.setattr(services, "_say_skipped", lambda uid, dry_run, n, hand: print(f"{n:6s} SKIPPED (stand-in)"))
    return label


@pytest.mark.parametrize("listen", [[], ["--listen"]])
def test_a_hand_made_keeper_is_not_checked_and_keeps_the_exit(fence, monkeypatch, capsys, listen):
    _conf(fence)
    label = _hand_keeper(monkeypatch)
    _no_seams(fence, monkeypatch)
    rc, out, err = _install(capsys, *listen)
    assert rc == 0 and not _fail_lines(err)
    assert _mic_rows(out) == [f"mic    not checked: the keeper here is hand-made ({label}); install did not load it"]
    assert "listening is off" not in out, "it says nothing of listening: --listen still set it"
    if listen:
        assert json.loads((fence["home"] / SETTINGS / "ears.json").read_text())["listen"] is True


def test_a_keeper_that_did_not_load_is_not_checked(fence, monkeypatch, capsys):
    _conf(fence)
    inner = services._launchctl

    def launchctl(*a):
        if a[0] == "bootstrap" and a[-1].endswith(f"{KEEPER}.plist"):
            return subprocess.CompletedProcess(a, 5, "", "Bootstrap failed: 5: Input/output error\n")
        return inner(*a)
    monkeypatch.setattr(services, "_launchctl", launchctl)
    _no_seams(fence, monkeypatch)
    rc, out, err = _install(capsys)
    assert rc == 2   # the load failure's own exit
    assert _mic_rows(out) == ["mic    not checked: the keeper did not load (above)"]
    assert not _fail_lines(err)


# -- the dry run: says what it would do, and touches nothing --------------------------------


def test_a_dry_run_with_listening_on_says_it_would_wait(fence, monkeypatch, capsys):
    _conf(fence)
    _no_seams(fence, monkeypatch)
    rc, out, _ = _install(capsys, "--dry-run")
    assert rc == 0
    assert _mic_rows(out) == [f"mic    would wait up to {mc.secs(_bound())} s for the keeper's first heartbeat "
                              "with sound; nothing is opened now"]
    assert fence["mic"]["t"] == 0


def test_a_dry_run_with_listen_would_wait_too(fence, monkeypatch, capsys):
    _no_seams(fence, monkeypatch)
    rc, out, _ = _install(capsys, "--dry-run", "--listen")
    assert rc == 0 and _mic_rows(out)[0].startswith("mic    would wait up to ")


def test_a_fresh_dry_run_says_listening_is_off(fence, monkeypatch, capsys):
    _no_seams(fence, monkeypatch)
    rc, out, _ = _install(capsys, "--dry-run")
    assert rc == 0 and _mic_rows(out) == [mc.row(mc.NC_LISTEN_OFF)]


def test_a_dry_run_with_the_off_switch_says_so(fence, monkeypatch, capsys):
    _conf(fence)
    monkeypatch.setenv("EARS_MIC_CHECK_S", "0")
    _no_seams(fence, monkeypatch)
    _, out, _ = _install(capsys, "--dry-run")
    assert _mic_rows(out) == [mc.row(mc.NC_OFF)]


# -- the bare install: proof line, install log, order -----------------------------------------


def _bare(capsys):
    capsys.readouterr()
    rc = services.main(["install", "--agent", "dave"])
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


def test_the_proof_line_is_left_out_when_not_heard(fence, capsys):
    _store(fence["home"])
    _conf(fence)
    fence["mic"]["polls"] = []
    rc, out, err = _bare(capsys)
    assert rc == 2 and _fail_lines(err)
    assert "proof  speak to the agent" not in out, "it would be false"


def test_the_proof_line_stays_when_heard(fence, capsys):
    _store(fence["home"])
    _conf(fence)
    rc, out, _ = _bare(capsys)
    assert rc == 0 and "proof  speak to the agent" in out


def test_the_mic_line_comes_after_the_last_load_and_before_the_proof(fence, capsys):
    _store(fence["home"])
    _conf(fence)
    _, out, _ = _bare(capsys)
    lines = out.splitlines()
    last_load = max(i for i, ln in enumerate(lines) if " loaded  " in ln)
    heard_at = next(i for i, ln in enumerate(lines) if ln.startswith("mic    heard: "))
    proof = next(i for i, ln in enumerate(lines) if ln.startswith("proof "))
    assert last_load < heard_at < proof


def test_the_install_log_holds_the_evidence_and_the_screen_does_not(fence, capsys):
    _conf(fence)
    fence["mic"]["polls"] = [[_beat(0)], [], [_beat(0)]]
    rc, out, err = _install(capsys)
    log = (services.LOGS / "install.log").read_text()
    [rec] = [ln for ln in log.splitlines() if " mic verdict " in ln]
    assert "verdict zeros" in rec and "peaks 0,0" in rec and "pid 4242" in rec
    assert "path /fake/uv/bin/python3.13" in rec and f"of {mc.secs(_bound())} s" in rec
    assert "peaks" not in out + err, "the evidence is the log's alone"
    assert log.rstrip().splitlines()[-1].endswith("rc 2")


def test_the_install_log_says_what_heard(fence, capsys):
    _conf(fence)
    _install(capsys)
    log = (services.LOGS / "install.log").read_text()
    assert any(" mic verdict heard" in ln and "peaks 1834" in ln for ln in log.splitlines())


# -- no microphone, by construction (R8) ---------------------------------------------------


def test_a_checked_install_loads_no_audio_module(fence, capsys):
    _conf(fence)
    before = set(sys.modules)
    rc, out, _ = _install(capsys)
    assert rc == 0 and _mic_rows(out)[-1].startswith("mic    heard: ")
    new = set(sys.modules) - before
    assert not [m for m in new if m == "sounddevice" or m.startswith(("sounddevice.", "ears.listen.sources"))]


# -- with the service files: a mic NOT HEARD (2) outranks a service file not written (3) ---


def _block_service_files(fence):
    """A file where the services folder goes: every service-file write fails."""
    from ears import home as ears_home
    folder = ears_home.services_dir(fence["home"])
    folder.parent.mkdir(parents=True, exist_ok=True)
    folder.write_text("in the way")
    return folder


def test_a_mic_not_heard_and_a_service_file_not_written_exit_2(fence, capsys):
    from ears import service_files
    _conf(fence)
    _block_service_files(fence)
    fence["mic"]["polls"] = [[_beat(0)], [], [_beat(0)]]
    rc, out, err = _install(capsys)
    assert rc == 2 != service_files.EXIT_UNWRITTEN, "2 outranks 3"
    assert len(_fail_lines(err)) == 1 and PRIVACY in _fail_lines(err)[0]
    assert " not written: " in err and "the job stands" in err, "the file's line is still said"


def test_heard_and_a_service_file_not_written_exit_3(fence, capsys):
    from ears import service_files
    _conf(fence)
    _block_service_files(fence)
    fence["mic"]["polls"] = [[_beat(1834)]]
    rc, out, err = _install(capsys)
    assert rc == service_files.EXIT_UNWRITTEN == 3
    assert not _fail_lines(err) and _mic_rows(out)[-1].startswith("mic    heard: ")
    assert " not written: " in err
