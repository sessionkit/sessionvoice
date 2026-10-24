"""EARS-379: every `ears services` --dry-run changes nothing.

`uninstall --dry-run` was a real uninstall: main() dropped the flag, and uninstall() booted out
every label (new and old, EARS-294) and deleted every plist. A temp HOME does not contain that:
the bootout acts on the user's own launchd domain, so a dry run took down live voice jobs.

Here launchctl is a fake that FAILS the test on any verb that changes launchd (bootout,
bootstrap, ...): only `print` and `list`, which look, are answered. Every unlink is refused
the same way, and the whole temp tree is compared before and after, byte for byte. The host
carries every label, new and old, loaded and with its plist, so each path has something it
could take down. Nothing here asks or changes the live launchd."""
import os
import pathlib
import plistlib
import subprocess

import pytest

from ears import services, speech
from test_services_install_checks import _store, fence  # noqa: F401  (fence: a fixture)
from test_services_speakers import sp  # noqa: F401  (a fixture)

UID = os.getuid()
LOOK = ("print", "list")


def _every_label():
    names = services.jobs(services.ALL) + list(services.OLD)
    return list(dict.fromkeys(l for n in names for l in services._labels(n)))


def _tree(root):
    """Every file and directory under ROOT, with each file's bytes."""
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None)
            for p in sorted(root.rglob("*"))}


@pytest.fixture
def host(sp, monkeypatch, tmp_path):
    """A host carrying every label, new and old, loaded and with its plist on disk. Every
    launchctl call is recorded; one that would change launchd FAILS the test. So does every
    unlink. Returns the fence, with ``plists`` and ``changed`` (the calls refused)."""
    services.AGENTS.mkdir(parents=True, exist_ok=True)
    plists = {}
    ours = {services._label(n) for n in services.jobs(services.ALL)}
    for label in _every_label():
        p = services.AGENTS / f"{label}.plist"
        if label in ours:     # as install wrote it (its MARKER), so never taken for hand-made
            p.write_bytes(plistlib.dumps({"Label": label, "EnvironmentVariables": dict([services.MARKER])}))
        else:
            p.write_text(f"<plist>{label}</plist>")
        plists[label] = (p, p.read_bytes())
        sp["listed"][label] = "1" + str(len(sp["listed"]))
    _store(sp["home"])
    looks = services._launchctl                          # sp's: answers print and list from listed
    changed = []

    def launchctl(*a):
        if a[0] not in LOOK:
            changed.append(a)
            pytest.fail(f"a dry run called launchctl {' '.join(a)}")
        return looks(*a)
    monkeypatch.setattr(services, "_launchctl", launchctl)

    def speech_run(argv, timeout=10.0):              # the speech probes' own launchctl: look only
        if argv[0] == "launchctl" and argv[1] not in LOOK:
            changed.append(tuple(argv))
            pytest.fail(f"a dry run called {' '.join(argv)}")
        return None                                  # never this host's: "not on this host"
    monkeypatch.setattr(speech, "_run", speech_run)

    def no_unlink(self, missing_ok=False):
        changed.append(("unlink", str(self)))
        pytest.fail(f"a dry run deleted {self}")
    monkeypatch.setattr(pathlib.Path, "unlink", no_unlink)
    monkeypatch.setattr(os, "remove", lambda p, *a, **kw: no_unlink(p))
    monkeypatch.setattr(os, "unlink", lambda p, *a, **kw: no_unlink(p))
    sp.update(plists=plists, changed=changed, before=_tree(tmp_path))
    return sp


def _unchanged(host, tmp_path):
    assert host["changed"] == []
    assert not [a for a in host["calls"]["launchctl"] if a[0] not in LOOK]
    assert not host["sh"]                                # no venv built, no model fetched
    assert _tree(tmp_path) == host["before"]
    for label, (p, body) in host["plists"].items():
        assert p.read_bytes() == body, label


# ---- uninstall --dry-run -----------------------------------------------------------------------

UNINSTALL = [[], ["--only", "stt"], ["--only", "speech"], ["--only", "mouth"], ["--only", "keeper"],
             ["--only", "mail"], ["--only", "speakers"], ["--only", "call"], ["--only", "room"],
             ["--only", ",".join(services.ALL)]]


@pytest.mark.parametrize("only", UNINSTALL, ids=lambda o: o[-1] if o else "all")
def test_uninstall_dry_run_names_each_label_and_plist_and_changes_nothing(host, tmp_path, capsys, only):
    assert services.main(["uninstall", "--dry-run", *only]) == 0
    out = capsys.readouterr().out
    _unchanged(host, tmp_path)
    assert host["calls"]["launchctl"] == []              # it does not even look
    names = list(services.ALL) if not only else [only[1]] if only[1] in services.OLD else only[1].split(",")
    names = ["keeper" if n in services.OLD else n for n in services.jobs(names)]
    if "keeper" in names:
        names += list(services.OLD)
    labels = list(dict.fromkeys(l for n in names for l in services._labels(n)))
    for label in labels:
        assert f"would boot out gui/{UID}/{label}" in out, label
        assert f"would delete {host['plists'][label][0]}" in out, label
    others = [l for l in _every_label() if l not in labels]
    for label in others:                                 # nothing outside the set is named
        assert label not in out, label
    assert "removed" not in out.replace("would be removed", "")
    assert "dry run: nothing booted out, nothing deleted" in out


def test_uninstall_dry_run_names_no_plist_that_is_not_there(host, capsys):
    """Every label it would boot out is named; only the plists that exist are."""
    gone = services.AGENTS / f"{services.OLD_PREFIXES[0]}mouth.plist"
    os.replace(gone, gone.with_suffix(".away"))
    assert services.main(["uninstall", "--dry-run", "--only", "mouth"]) == 0
    out = capsys.readouterr().out
    assert f"would boot out gui/{UID}/{services.OLD_PREFIXES[0]}mouth" in out
    assert f"would delete {gone}" not in out
    assert f"would delete {services.AGENTS / (services.PREFIX + 'mouth.plist')}" in out
    assert host["changed"] == [] and services.AGENTS.joinpath(f"{services.PREFIX}mouth.plist").exists()


def test_uninstall_dry_run_on_an_empty_host_calls_nothing(sp, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(services, "_launchctl", lambda *a: calls.append(a) or pytest.fail(f"launchctl {a}"))
    assert services.uninstall(list(services.ALL), dry_run=True) == 0
    assert calls == []
    out = capsys.readouterr().out
    assert "would boot out" in out and "would delete" not in out


# ---- without --dry-run: uninstall as before ----------------------------------------------------

def test_a_real_uninstall_still_boots_out_and_deletes_every_label(sp, capsys):
    services.AGENTS.mkdir(parents=True, exist_ok=True)
    plists = []
    ours = {services._label(n) for n in services.jobs(services.ALL)}
    for label in _every_label():
        p = services.AGENTS / f"{label}.plist"
        if label in ours:     # as install wrote it (its MARKER): a hand render would be left
            p.write_bytes(plistlib.dumps({"Label": label, "EnvironmentVariables": dict([services.MARKER])}))
        else:
            p.write_text("<plist/>")
        plists.append(p)
    assert services.main(["uninstall"]) == 0
    booted = [a[1] for a in sp["calls"]["launchctl"] if a[0] == "bootout"]
    assert booted == [f"gui/{UID}/{l}" for l in _every_label()]
    assert not [p for p in plists if p.exists()]
    out = capsys.readouterr().out
    assert "would" not in out and "mouth  removed" in out


def test_main_hands_uninstall_the_flag(monkeypatch):
    got = []
    monkeypatch.setattr(services, "uninstall", lambda names, dry=False: got.append(dry) or 0)
    services.main(["uninstall", "--dry-run"]); services.main(["uninstall"])
    assert got == [True, False]


# ---- install --dry-run: every set it takes -----------------------------------------------------

# [] and ["--apple"] are the bare form: with no wake seat it is REFUSED (EARS-421), and a refused
# dry run still lists and still changes nothing; --no-agent is the same set, installed on purpose
INSTALL = [[], ["--apple"], ["--agent", "dave"], ["--no-agent"], ["--only", "stt"], ["--only", "speech"],
           ["--only", "mouth"], ["--only", "keeper"], ["--only", "mail"], ["--only", "mail", "--no-agent"],
           ["--only", "speakers"], ["--only", "call,room"], ["--only", ",".join(services.ALL)]]


@pytest.mark.parametrize("args", INSTALL, ids=lambda a: "-".join(a).strip("-") or "default")
def test_install_dry_run_changes_nothing(host, tmp_path, capsys, args):
    assert services.main(["install", "--dry-run", *args]) in (0, 2)
    _unchanged(host, tmp_path)
    assert "--- " in capsys.readouterr().out or args == ["--only", "speech"]


@pytest.mark.parametrize("args", [[], ["--no-agent"], ["--only", "speakers"], ["--only", "keeper"]],
                         ids=["default", "no-agent", "speakers", "keeper"])
def test_install_dry_run_on_a_fresh_host_changes_nothing(sp, monkeypatch, tmp_path, capsys, args):
    _store(sp["home"])
    before = _tree(tmp_path)
    monkeypatch.setattr(services, "_launchctl", lambda *a: a[0] in LOOK and
                        subprocess.CompletedProcess(a, 113 if a[0] == "print" else 0, "", "")
                        or pytest.fail(f"a dry run called launchctl {' '.join(a)}"))
    assert services.main(["install", "--dry-run", *args]) in (0, 2)
    assert _tree(tmp_path) == before and not sp["sh"]
