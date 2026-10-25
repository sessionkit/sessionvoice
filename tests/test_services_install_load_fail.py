"""`ears services install` when a launch agent fails to load, and the install log.

A service whose launchctl bootstrap fails makes install exit 2, once every service has been
tried, with one FAILED block on stderr naming each service, the install log and `ears
services status`. Every real run appends what it printed, and what launchctl answered, to
``<sessionvoice's home>/logs/install.log``, each line timestamped, and prints that path.

Every case runs inside test_services_install_checks' fence (a temp HOME, services._launchctl
faked), plus a tripwire: a `launchctl` stub first on PATH that writes a sentinel and exits 99.
Teardown asserts the sentinel is absent, so no call got past the seam to a real launchctl."""
import os
import re
import subprocess
import sys

import pytest

from ears import services
from test_services_install_checks import _store, fence  # noqa: F401  (a fixture)
from test_services_install_golden import pinned  # noqa: F401  (a fixture)
from test_services_label_move import NEW, OLD, _old_plist, launchd  # noqa: F401  (a fixture)

FAIL_ERR = "Bootstrap failed: 5: Input/output error"
KEEPER = "dev.sessionkit.voice.ears-keeper"
STAMP = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d"


@pytest.fixture
def tripwire(pinned, monkeypatch):
    """The fence, and a PATH `launchctl` that must never run."""
    bindir = pinned["tmp"] / "tripwire-bin"
    bindir.mkdir()
    sentinel = pinned["tmp"] / "REAL-LAUNCHCTL-REACHED"
    stub = bindir / "launchctl"
    stub.write_text(f'#!/bin/sh\necho "$@" >> "{sentinel}"\necho "TRIPWIRE launchctl $*" >&2\nexit 99\n')
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    _store(pinned["home"])
    pinned["log"] = pinned["home"] / ".sessionvoice/logs/install.log"
    yield pinned
    assert not sentinel.exists(), f"a real launchctl call escaped the seam: {sentinel.read_text()}"


def _failing(monkeypatch, fail_labels):
    """Wrap the fence's fake: `bootstrap` of a plist whose label is in FAIL_LABELS returns 5."""
    inner = services._launchctl
    boots = []

    def launchctl(*a):
        if a[0] == "bootstrap":
            label = os.path.basename(a[2]).removesuffix(".plist")
            boots.append(label)
            if label in fail_labels:
                return subprocess.CompletedProcess(a, 5, "", FAIL_ERR + "\n")
        return inner(*a)
    monkeypatch.setattr(services, "_launchctl", launchctl)
    return boots


def _install(capsys, argv=("install", "--agent", "dave")):
    capsys.readouterr()
    rc = services.main(list(argv))
    return rc, capsys.readouterr()


# ---- a service that fails to load ----------------------------------------------------------

def test_one_failed_load_exits_2_after_every_service_was_tried(tripwire, monkeypatch, capsys):
    """The regression: keeper's bootstrap fails. Before the fix this printed FAILED and exited 0."""
    boots = _failing(monkeypatch, {KEEPER})
    rc, cap = _install(capsys)
    assert rc == 2
    labels = {services._label(n) for n in services.DEFAULT}
    assert labels <= set(boots)                                  # every service was tried
    assert services._label("mail") in boots[boots.index(KEEPER):]   # including those after keeper
    keeper_plist = services.AGENTS / f"{KEEPER}.plist"
    assert f"keeper FAILED: {FAIL_ERR}  {keeper_plist}\n" in cap.out   # the row, as it was
    assert f"ears services install: FAILED: keeper did not load: {FAIL_ERR}\n" in cap.err
    assert ("ears services install: 1 service did not load (above), from one run; every service "
            f"was tried. Next: read the install log ({tripwire['log']}), then run "
            "`ears services status`\n") in cap.err
    assert cap.err.count("did not load:") == 1                   # mouth, mail and speech loaded
    assert "proof  speak to the agent" not in cap.out            # never a proof of a keeper not loaded
    assert cap.out.endswith(f"log    {tripwire['log']}\n")


def test_every_failed_load_is_named_in_one_block(tripwire, monkeypatch, capsys):
    labels = {services._label(n) for n in services.DEFAULT}
    _failing(monkeypatch, labels)
    rc, cap = _install(capsys)
    assert rc == 2
    named = re.findall(r"^ears services install: FAILED: (\S+) did not load: ", cap.err, re.M)
    assert sorted(named) == sorted(services.DEFAULT)
    assert "4 services did not load (above), from one run" in cap.err
    assert cap.err.count("`ears services status`") == 1          # one summary, not one per service
    assert "loaded  " not in cap.out


def test_a_failed_load_with_no_reason_names_launchctl_exit(tripwire, monkeypatch, capsys):
    inner = services._launchctl

    def launchctl(*a):
        if a[0] == "bootstrap" and a[2].endswith(f"{KEEPER}.plist"):
            return subprocess.CompletedProcess(a, 37, "", "")
        return inner(*a)
    monkeypatch.setattr(services, "_launchctl", launchctl)
    rc, cap = _install(capsys)
    assert rc == 2
    assert "FAILED: keeper did not load: launchctl bootstrap exit 37\n" in cap.err


def test_an_old_label_that_will_not_go_is_named_in_the_block(launchd, capsys, monkeypatch, tmp_path):
    agents, make = launchd
    make(loaded={OLD + "keeper", OLD + "mouth"}, stuck={OLD + "keeper"})
    _old_plist(agents)
    assert services.install(["mouth", "keeper"]) == 2
    err = capsys.readouterr().err
    assert (f"ears services install: FAILED: keeper did not load: {OLD}keeper would not go; "
            f"{NEW}keeper NOT loaded\n") in err
    assert "1 service did not load (above)" in err             # mouth loaded


# ---- the install log -----------------------------------------------------------------------

def test_a_successful_install_appends_its_output_and_launchctl_to_the_log(tripwire, capsys):
    rc, cap = _install(capsys)
    assert rc == 0 and cap.err == ""
    log = tripwire["log"]
    assert services.install_log_path() == log
    assert cap.out.endswith(f"proof  speak to the agent: it WAKES and answers. A heard line alone "
                            f"proves only the keeper; the mail is what pages it\nlog    {log}\n")
    lines = log.read_text().splitlines()
    assert re.fullmatch(rf"=== {STAMP} ears services install speech mouth keeper mail", lines[0])
    assert re.fullmatch(rf"=== {STAMP} rc 0", lines[-1])
    body = lines[1:-1]
    assert all(re.match(rf"{STAMP} (stdout|stderr|launchctl) ", line) for line in body)
    printed = [re.sub(rf"^{STAMP} stdout ", "", line) for line in body if " stdout " in line]
    assert printed == cap.out.splitlines()                      # every line it printed, in order
    calls = [re.sub(rf"^{STAMP} launchctl ", "", line) for line in body if " launchctl " in line]
    uid = os.getuid()
    for n in services.DEFAULT:
        path = services.AGENTS / f"{services._label(n)}.plist"
        assert f"bootstrap gui/{uid} {path} -> rc 0" in calls
        assert f"bootout gui/{uid}/{services._label(n)} -> rc 0" in calls
    assert not [c for c in calls if c.startswith(("print ", "list"))]   # the read-only questions


def test_the_log_stamps_each_line_with_its_clock(tripwire, monkeypatch, capsys):
    monkeypatch.setattr(services, "_log_now", lambda: "2030-06-09T10:00:00+10:00")
    _install(capsys, ("install", "--agent", "dave", "--only", "mouth"))
    lines = tripwire["log"].read_text().splitlines()
    assert lines[0] == "=== 2030-06-09T10:00:00+10:00 ears services install mouth"
    assert f"2030-06-09T10:00:00+10:00 stdout log    {tripwire['log']}" in lines
    assert lines[-1] == "=== 2030-06-09T10:00:00+10:00 rc 0"


def test_a_failed_load_is_in_the_log_with_launchctl_answer(tripwire, monkeypatch, capsys):
    _failing(monkeypatch, {KEEPER})
    _install(capsys)
    text = tripwire["log"].read_text()
    path = services.AGENTS / f"{KEEPER}.plist"
    assert re.search(rf"^{STAMP} launchctl bootstrap gui/{os.getuid()} {re.escape(str(path))} -> rc 5: "
                     rf"{re.escape(FAIL_ERR)}$", text, re.M)
    assert re.search(rf"^{STAMP} stderr ears services install: FAILED: keeper did not load: ", text, re.M)
    assert re.search(rf"^=== {STAMP} rc 2$", text.splitlines()[-1])


def test_a_progress_line_redrawn_on_a_tty_is_logged_as_it_ended(tripwire, monkeypatch, capsys):
    """The prefetch redraws one progress line on a tty (a carriage return, the line, an erase):
    the terminal gets every byte as before, the log one clean line, its last state."""
    def prefetch(dry_run=False, off=None):
        for i in range(1, 400):
            print(f"\rspeech weights: fetching r: {i} of 399\x1b[K", end="", flush=True)
            assert len(services._RUN_LOG._part["stdout"]) < 80    # held: its last state only
        print()
    monkeypatch.setattr(services, "prefetch", prefetch)
    rc, cap = _install(capsys)
    assert rc == 0
    assert "\rspeech weights: fetching r: 1 of 399\x1b[K" in cap.out       # the terminal, unchanged
    assert "\rspeech weights: fetching r: 399 of 399\x1b[K\n" in cap.out
    text = tripwire["log"].read_bytes().decode()    # raw: read_text() turns a \r into \n
    assert "\r" not in text and "\x1b" not in text
    assert re.search(rf"^{STAMP} stdout speech weights: fetching r: 399 of 399$", text, re.M)
    assert "1 of 399" not in text


def test_a_run_that_stops_mid_redraw_logs_that_line_plain(tripwire, monkeypatch, capsys):
    """A redrawn line still open when the run stops (no newline after it) is logged as it
    ended, with no carriage return or control sequence, just before the footer."""
    def prefetch(dry_run=False, off=None):
        print("\rspeech weights: fetching r: 1 of 2\x1b[K", end="", flush=True)
        print("\rspeech weights: fetching r: 2 of 2\x1b[K", end="", flush=True)
        raise RuntimeError("the fetch died")
    monkeypatch.setattr(services, "prefetch", prefetch)
    with pytest.raises(RuntimeError):
        services.main(["install", "--agent", "dave", "--only", "speech"])
    text = tripwire["log"].read_bytes().decode()    # raw: read_text() turns a \r into \n
    assert "\r" not in text and "\x1b" not in text
    lines = text.splitlines()
    assert re.fullmatch(rf"{STAMP} stdout speech weights: fetching r: 2 of 2", lines[-2])
    assert re.fullmatch(rf"=== {STAMP} raised RuntimeError: the fetch died", lines[-1])


def _unwritable(tripwire):
    """A FILE where the log's folder goes: mkdir fails, as on a host whose logs/ is not ours."""
    logs = tripwire["log"].parent
    logs.parent.mkdir(parents=True, exist_ok=True)
    logs.write_text("not a folder")
    return logs


WARNING = "ears services install: WARNING: the install log "


def test_an_unwritable_log_never_stops_the_install(tripwire, capsys):
    logs = _unwritable(tripwire)
    rc, cap = _install(capsys)
    assert rc == 0                                              # the services decide, alone
    assert cap.err.count(WARNING) == 1 and cap.err.count("\n") == 1   # one line, no traceback
    assert cap.err.startswith(f"{WARNING}{tripwire['log']} cannot be written (")
    assert cap.err.endswith("); installing on without it\n")
    for n in services.DEFAULT:
        assert f"{n:6s} loaded  {services.AGENTS / (services._label(n) + '.plist')}" in cap.out
    assert "proof  speak to the agent" in cap.out
    assert "\nlog    " not in cap.out                         # no path for a log not written
    assert logs.read_text() == "not a folder"


def test_an_unwritable_log_leaves_a_failed_load_its_exit_2(tripwire, monkeypatch, capsys):
    _unwritable(tripwire)
    _failing(monkeypatch, {KEEPER})
    rc, cap = _install(capsys)
    assert rc == 2
    assert cap.err.count(WARNING) == 1
    assert f"FAILED: keeper did not load: {FAIL_ERR}\n" in cap.err
    assert "every service was tried. Next: run `ears services status`\n" in cap.err   # no log named


def test_a_log_write_that_fails_mid_run_stops_nothing(tripwire, monkeypatch, capsys):
    """The disk fills once the log is armed: one warning, and every service still loads."""
    arm = services._InstallLog.arm

    class Full:
        def __init__(self, fh):
            self.fh = fh

        def write(self, text):
            raise OSError(28, "No space left on device")

        def flush(self):
            pass

        def close(self):
            self.fh.close()

    def arm_then_fill(self):
        arm(self)
        self._fh = Full(self._fh)
    monkeypatch.setattr(services._InstallLog, "arm", arm_then_fill)
    rc, cap = _install(capsys)
    assert rc == 0
    assert cap.err == f"{WARNING}{tripwire['log']} cannot be written (No space left on device); installing on without it\n"
    assert all(f"{n:6s} loaded  " in cap.out for n in services.DEFAULT)
    assert "\nlog    " not in cap.out
    lines = tripwire["log"].read_text().splitlines()
    assert lines[0].startswith("=== ") and " ears services install " in lines[0]   # before it filled
    assert not re.match(r"=== .* rc ", lines[-1])               # nothing after: no footer


def test_each_run_appends(tripwire, capsys):
    _install(capsys)
    first = tripwire["log"].read_text()
    _install(capsys)
    both = tripwire["log"].read_text()
    assert both.startswith(first) and both.count("=== ") == 4   # two headers, two footers


def test_a_dry_run_writes_no_log_and_prints_no_log_row(tripwire, capsys):
    rc, cap = _install(capsys, ("install", "--agent", "dave", "--dry-run"))
    assert rc == 0
    assert not tripwire["log"].exists() and not tripwire["log"].parent.exists()
    assert "\nlog    " not in cap.out


def test_a_refusal_before_anything_is_written_writes_no_log(tripwire, capsys):
    rc, cap = _install(capsys, ("install",))                     # no agent: refused, nothing written
    assert rc == 2 and "nothing was written" in cap.err
    assert not tripwire["log"].parent.exists()


def test_a_late_refusal_after_the_host_changed_is_logged_and_says_where(tripwire, monkeypatch, capsys):
    monkeypatch.setattr(services, "speech_step", lambda names, dry_run, d=None: (False, False))
    rc, cap = _install(capsys)
    assert rc == 2
    assert cap.out.endswith(f"log    {tripwire['log']}\n")      # printed by the run's wrapper
    assert re.search(rf"^=== {STAMP} rc 2$", tripwire["log"].read_text().splitlines()[-1])


def test_a_run_that_raises_closes_its_log_and_restores_the_seams(tripwire, monkeypatch, capsys):
    fake = services._launchctl

    def launchctl(*a):
        if a[0] == "bootstrap":
            raise RuntimeError("launchd went away")
        return fake(*a)
    monkeypatch.setattr(services, "_launchctl", launchctl)
    out, err = sys.stdout, sys.stderr
    with pytest.raises(RuntimeError):
        services.main(["install", "--agent", "dave"])
    assert services._launchctl is launchctl and services._RUN_LOG is None
    assert sys.stdout is out and sys.stderr is err
    last = tripwire["log"].read_text().splitlines()[-1]
    assert re.fullmatch(rf"=== {STAMP} raised RuntimeError: launchd went away", last)
