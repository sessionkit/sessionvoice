"""EARS-388: `ears services install` never leaves a LEGACY render running, and its `message`
check reads the PATH of the mailer that actually runs.

A host kept a hand-rendered dev.voicemode.ears-mail (and a dev.voicemode.mouth-mail) whose
PATH predated `message`. Install skipped ours beside it, so the stale render was the only
mailer, and `message present` was said from OUR PATH: a false green on the failing host.

Every plist here is a FIXTURE in a temp LaunchAgents dir (services.AGENTS is patched) and
launchctl is a fake that keeps its own loaded set: nothing asks or changes the live launchd,
and no live LaunchAgents dir is read or written."""
import os
import plistlib
from pathlib import Path

import pytest

from ears import services
from test_services_install_checks import _store, fence  # noqa: F401  (fence: the fixture)
from test_services_label_move import FakeLaunchd

UID = os.getuid()
LEGACY_MAIL = "dev.voicemode.ears-mail"
LEGACY_MOUTHS = ("dev.voicemode.mouth-mail", "com.failmode.mouth-mail")
OURS = "dev.sessionkit.voice.ears-"


@pytest.fixture
def fake(fence, monkeypatch):  # noqa: F811
    """fence (temp HOME, temp AGENTS, postfix and STT stubbed) with a FakeLaunchd in place of
    its launchctl; ``make(**kw)`` sets the loaded labels."""
    monkeypatch.setattr(services, "BOOT_WAIT_S", 0.05)
    box = {}

    def make(**kw):
        box["l"] = FakeLaunchd(**kw)
        monkeypatch.setattr(services, "_launchctl", box["l"])
        return box["l"]
    services.AGENTS.mkdir(parents=True, exist_ok=True)
    _store(fence["home"])
    return make


def _legacy(label, path="/usr/bin:/bin:/usr/sbin:/sbin"):
    """A fixture legacy plist, as a host kept one: its own label, a PATH with no `message`."""
    p = services.AGENTS / f"{label}.plist"
    p.write_bytes(plistlib.dumps({"Label": label, "ProgramArguments": ["/usr/bin/true"],
                                  "EnvironmentVariables": {"PATH": path}}))
    return p


def _asides(label):
    return sorted(services.AGENTS.glob(f"{label}.plist.replaced-*"))


# --- the legacy mailer is REPLACED, never left running beside ours or alone -----------------

def test_a_loaded_legacy_mailer_is_booted_out_moved_aside_and_ours_loads(fake, capsys):
    l = fake(loaded={LEGACY_MAIL}, linger=2)
    plist = _legacy(LEGACY_MAIL)
    body = plist.read_bytes()
    assert services.install(["mail"]) == 0
    out = capsys.readouterr().out
    assert "mail SKIPPED" not in out                                      # the old behaviour
    assert f"LEGACY {LEGACY_MAIL} (loaded)" in out and "REPLACED" in out
    assert l.verbs(LEGACY_MAIL).count("bootout") == 1
    assert LEGACY_MAIL not in l.loaded and OURS + "mail" in l.loaded
    # moved aside, never deleted: the host keeps the render, and launchd will not load it
    assert not plist.exists()
    [aside] = _asides(LEGACY_MAIL)
    assert aside.read_bytes() == body and not aside.name.endswith(".plist")
    # ours loaded only after the legacy job had gone: never two mailers
    i_boot = l.calls.index(l.naming(OURS + "mail")[-1])
    i_last = max(l.calls.index(c) for c in l.naming(LEGACY_MAIL) if c[0] == "print")
    assert l.naming(OURS + "mail")[-1][0] == "bootstrap" and i_last < i_boot


def test_a_legacy_plist_not_loaded_is_still_moved_aside(fake, capsys):
    # launchd would load it at the next login: a plist alone is a stale render waiting to run
    l = fake()
    plist = _legacy(LEGACY_MAIL)
    assert services.install(["mail"]) == 0
    assert "plist only, not loaded" in capsys.readouterr().out
    assert not plist.exists() and len(_asides(LEGACY_MAIL)) == 1
    assert "bootout" not in l.verbs(LEGACY_MAIL)
    assert OURS + "mail" in l.loaded


def test_a_legacy_mailer_that_will_not_go_fails_loudly_and_ours_is_not_loaded(fake, capsys):
    l = fake(loaded={LEGACY_MAIL}, stuck={LEGACY_MAIL})
    _legacy(LEGACY_MAIL)
    assert services.install(["mail"]) == 2
    cap = capsys.readouterr()
    assert f"mail FAILED: {LEGACY_MAIL} is still loaded" in cap.err
    assert f"FAILED: {LEGACY_MAIL} would not go" in cap.out
    assert "bootstrap" not in l.verbs(OURS + "mail") and OURS + "mail" not in l.loaded


def test_a_second_install_finds_no_legacy_and_leaves_the_aside_copy(fake, capsys):
    fake(loaded={LEGACY_MAIL})
    _legacy(LEGACY_MAIL)
    assert services.install(["mail"]) == 0
    [aside] = _asides(LEGACY_MAIL)
    capsys.readouterr()
    assert services.install(["mail"]) == 0
    assert "LEGACY" not in capsys.readouterr().out
    assert _asides(LEGACY_MAIL) == [aside]


def test_the_dry_run_says_it_would_replace_and_changes_nothing(fake, capsys):
    l = fake(loaded={LEGACY_MAIL})
    plist = _legacy(LEGACY_MAIL)
    assert services.install(["mail"], dry_run=True) == 0
    assert f"LEGACY {LEGACY_MAIL} (loaded)" in capsys.readouterr().out
    assert plist.exists() and not _asides(LEGACY_MAIL)
    assert [c for c in l.calls if c[0] not in ("print", "list")] == []     # read-only
    assert LEGACY_MAIL in l.loaded


def test_the_dry_run_line_says_would(fake, capsys):
    fake(loaded={LEGACY_MAIL})
    _legacy(LEGACY_MAIL)
    services.install(["mail"], dry_run=True)
    line = [x for x in capsys.readouterr().out.splitlines() if "LEGACY" in x]
    assert len(line) == 1 and "would be REPLACED" in line[0] and line[0].startswith("WARNING:")


def test_beside_the_hand_template_ours_is_skipped_and_the_legacy_one_still_goes(fake, capsys):
    # two hand mailers: the current template's render stays the one mailer; the legacy goes
    l = fake(loaded={LEGACY_MAIL, services.HAND_MAIL})
    _legacy(LEGACY_MAIL)
    hand = _legacy(services.HAND_MAIL, path=f"{Path.home()}/.local/bin:/usr/bin:/bin")
    body = hand.read_bytes()
    assert services.install(["mail"]) == 0
    out = capsys.readouterr().out
    assert f"mail SKIPPED: the hand-made {services.HAND_MAIL}" in out
    assert f"LEGACY {LEGACY_MAIL}" in out and "REPLACED" in out
    assert LEGACY_MAIL not in l.loaded and services.HAND_MAIL in l.loaded
    assert hand.read_bytes() == body and "bootout" not in l.verbs(services.HAND_MAIL)
    assert "bootstrap" not in l.verbs(OURS + "mail")


def test_beside_the_hand_template_a_stuck_legacy_mailer_fails_the_install(fake, capsys):
    fake(loaded={LEGACY_MAIL, services.HAND_MAIL}, stuck={LEGACY_MAIL})
    _legacy(LEGACY_MAIL)
    _legacy(services.HAND_MAIL)
    assert services.install(["mail"]) == 2
    assert "two mailers" in capsys.readouterr().err


# --- the mouth's legacy players: never two players draining one queue ----------------------

@pytest.mark.parametrize("label", LEGACY_MOUTHS)
def test_a_legacy_mouth_player_is_replaced_before_ours_loads(fake, capsys, label):
    l = fake(loaded={label}, linger=1)
    plist = _legacy(label, path="/opt/homebrew/bin:/usr/bin:/bin")
    assert services.install(["mouth"]) == 0
    out = capsys.readouterr().out
    assert f"mouth LEGACY {label} (loaded)" in out and "REPLACED" in out
    assert not plist.exists() and len(_asides(label)) == 1
    assert label not in l.loaded and OURS + "mouth" in l.loaded
    i_boot = l.calls.index(l.naming(OURS + "mouth")[-1])
    i_last = max(l.calls.index(c) for c in l.naming(label) if c[0] == "print")
    assert i_last < i_boot


def test_both_legacy_mouths_go(fake, capsys):
    l = fake(loaded=set(LEGACY_MOUTHS))
    for label in LEGACY_MOUTHS:
        _legacy(label)
    assert services.install(["mouth"]) == 0
    assert not (set(LEGACY_MOUTHS) & l.loaded) and OURS + "mouth" in l.loaded
    assert all(len(_asides(label)) == 1 for label in LEGACY_MOUTHS)


def test_a_legacy_mouth_that_will_not_go_fails_and_ours_is_not_loaded(fake, capsys):
    label = LEGACY_MOUTHS[0]
    l = fake(loaded={label}, stuck={label})
    _legacy(label)
    assert services.install(["mouth"]) == 2
    assert f"FAILED: {label} would not go" in capsys.readouterr().out
    assert OURS + "mouth" not in l.loaded


def test_a_mouth_install_never_touches_the_mail_legacy_and_vice_versa(fake, capsys):
    l = fake(loaded={LEGACY_MAIL, *LEGACY_MOUTHS})
    assert services.install(["mouth"]) == 0
    assert LEGACY_MAIL in l.loaded and "bootout" not in l.verbs(LEGACY_MAIL)


def test_uninstall_leaves_legacy_jobs_as_every_hand_made_one(fake):
    l = fake(loaded={LEGACY_MAIL, *LEGACY_MOUTHS})
    services.uninstall(list(services.ALL))
    assert not any(c[0] == "bootout" and c[-1].split("/")[-1] in (LEGACY_MAIL, *LEGACY_MOUTHS)
                   for c in l.calls)


def test_status_names_a_legacy_render_still_on_the_host(fake, capsys):
    fake(loaded={LEGACY_MAIL})
    _legacy(LEGACY_MOUTHS[1])
    services.status()
    out = capsys.readouterr().out
    assert f"WARNING: {LEGACY_MAIL} (LEGACY render): running, pid" in out
    assert f"{LEGACY_MOUTHS[1]} (LEGACY render): plist present" in out
    assert "install --only mail` replaces it" in out and "install --only mouth` replaces it" in out


# --- the `message` check reads the PATH of the plist that RUNS ------------------------------

@pytest.fixture
def uv_message(fence, monkeypatch):  # noqa: F811
    """`message` where uv puts it, under the temp HOME only (~/.local/bin): the check looks
    for the default name, as the services do."""
    local = Path.home() / ".local/bin"
    local.mkdir(parents=True)
    (local / "message").write_text("#!/bin/sh\n")
    (local / "message").chmod(0o755)
    monkeypatch.setattr(services, "MESSAGE", "message")
    monkeypatch.setenv("PATH", "/nonexistent")
    return local / "message"


def _hand(env):
    p = services.AGENTS / f"{services.HAND_MAIL}.plist"
    p.parent.mkdir(parents=True, exist_ok=True)
    d = {"Label": services.HAND_MAIL, "ProgramArguments": ["/usr/bin/true"]}
    if env is not None:
        d["EnvironmentVariables"] = env
    p.write_bytes(plistlib.dumps(d))
    return services.HAND_MAIL, p


def test_the_false_green_reproduced_then_gone(uv_message):
    """The hand plist runs on a bare PATH; ours would have found `message`. Before EARS-388 the
    check said `present` from ours; now it reads theirs."""
    assert services._message_bin() == str(uv_message)            # what the old check said
    runs = _hand({"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(Path.home())})
    out = services.mail_checks(dry_run=True, runs=runs)
    assert not any(l.startswith("message present") for l in out)
    [warn] = [l for l in out if l.startswith("WARNING: message")]
    assert f"not on {services.HAND_MAIL}'s PATH (/usr/bin:/bin:/usr/sbin:/sbin)" in warn
    assert str(uv_message) in warn and "127" in warn and "Re-render" in warn
    assert "EARS-" not in warn                 # a printed line carries no tracker id


def test_message_on_the_hand_plists_path_is_present_naming_it(uv_message):
    runs = _hand({"PATH": f"{uv_message.parent}:/usr/bin:/bin", "HOME": str(Path.home())})
    out = services.mail_checks(dry_run=True, runs=runs)
    assert f"message present: {uv_message} (on {services.HAND_MAIL}'s PATH: the mailer that runs)" in out


def test_a_hand_plist_with_no_environment_runs_on_launchds_bare_path(uv_message):
    out = services._message_lines(_hand(None))
    assert len(out) == 1 and out[0].startswith("WARNING:")
    assert f"PATH ({services.LAUNCHD_PATH})" in out[0]


def test_message_nowhere_for_the_hand_plist_is_missing(fence, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(services, "MESSAGE", "message")
    runs = _hand({"PATH": str(tmp_path / "empty"), "HOME": str(tmp_path / "nohome")})
    [line] = services._message_lines(runs)
    assert line.startswith(f"WARNING: message MISSING for {services.HAND_MAIL}")
    assert "~/.local/share/uv/tools/sessionmail/bin/message" in line


def test_the_hand_plists_home_is_where_the_fallback_looks(fence, monkeypatch, tmp_path):  # noqa: F811
    """HOME is the plist's, never this process's: a test HOME's message is not theirs."""
    monkeypatch.setattr(services, "MESSAGE", "message")
    theirs = tmp_path / "theirs"
    tool = theirs / ".local/share/uv/tools/sessionmail/bin"
    tool.mkdir(parents=True)
    (tool / "message").write_text("#!/bin/sh\n"); (tool / "message").chmod(0o755)
    [line] = services._message_lines(_hand({"PATH": "/usr/bin:/bin", "HOME": str(theirs)}))
    assert str(tool / "message") in line and "fallback" in line


def test_a_hand_plists_ears_mail_message_is_looked_up_on_its_path_alone(uv_message, tmp_path):
    runs = _hand({"PATH": "/usr/bin:/bin", "HOME": str(Path.home()),
                  "EARS_MAIL_MESSAGE": "no-such-message --flag"})
    [line] = services._message_lines(runs)
    assert line.startswith(f"WARNING: message MISSING for {services.HAND_MAIL}")
    assert "EARS_MAIL_MESSAGE (no-such-message)" in line
    named = tmp_path / "named"; named.write_text("#!/bin/sh\n"); named.chmod(0o755)
    runs = _hand({"PATH": "/usr/bin:/bin", "EARS_MAIL_MESSAGE": f"{named} --flag"})
    assert services._message_lines(runs)[0].startswith(f"message present: {named}")


@pytest.mark.parametrize("how", ["absent", "garbage"])
def test_a_hand_plist_that_cannot_be_read_is_unchecked_never_present(uv_message, how):
    label, p = _hand({"PATH": "/usr/bin"})
    if how == "absent":
        p.unlink()                          # loaded with its file gone
    else:
        p.write_text("HAND")
    [line] = services._message_lines((label, p))
    assert line.startswith("WARNING: message UNCHECKED") and str(p) in line


def test_install_beside_a_stale_hand_mailer_says_no_present(fake, uv_message, capsys):
    """End to end: the hand mailer runs on a bare PATH; install never says `message present`."""
    fake(loaded={services.HAND_MAIL})
    _hand({"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(Path.home())})
    assert services.install(["mail"]) == 0
    out = capsys.readouterr().out
    assert "message present" not in out
    assert f"WARNING: message is not on {services.HAND_MAIL}'s PATH" in out


def test_our_own_mailer_is_still_checked_on_our_path(fake, uv_message, capsys):
    fake()
    assert services.install(["mail"]) == 0
    assert f"message present: {uv_message}\n" in capsys.readouterr().out
