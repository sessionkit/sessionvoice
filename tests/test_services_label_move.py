"""EARS-294 T6, EARS-407: the launch agents move off every old label (com.voicemode.ears-NAME,
then com.sessionvoice.ears-NAME) to dev.sessionkit.voice.ears-NAME.

`ears services install` moves each service it installs: the old label is booted out ONCE, its
plist removed, and the new label loaded only once the old has gone, at most once. An old label
that will not go fails the install loudly, and the new one is not loaded beside it (two keepers
would fight over the room's capture). A second install makes no call naming the old label.

Every test drives a FAKE launchctl that records its calls and keeps its own loaded set: nothing
here asks or changes the live launchd."""
import os
import plistlib
import subprocess

import pytest

from ears import services, speakers

OLD, NEW = "com.voicemode.ears-", "dev.sessionkit.voice.ears-"
PREV = "com.sessionvoice.ears-"          # the label between the two (EARS-294 to EARS-407)


def print_out(label, env):
    """`launchctl print gui/UID/LABEL` for a loaded job, as far as install reads it: the
    environment block, one KEY => VALUE line each."""
    lines = "".join(f"\t\t{k} => {v}\n" for k, v in env.items())
    return f"gui/501/{label} = {{\n\tstate = running\n\tenvironment = {{\n{lines}\t}}\n}}\n"


OURS_ENV = dict([services.MARKER])     # a job install loaded: its environment carries the marker


class FakeLaunchd:
    """launchctl as a set of loaded labels. bootout unloads after LINGER more `print`s of the
    label (launchd removes the job after bootout returns); a label in STUCK never goes."""

    def __init__(self, loaded=(), linger=0, stuck=(), env=None):
        self.loaded, self.linger, self.stuck = set(loaded), linger, set(stuck)
        # what `print` lists as each job's environment: a bootstrap records its plist's
        # EnvironmentVariables, as launchd does; ENV sets it for a job loaded before the test
        self.env: dict[str, dict] = dict(env or {})
        self.going: dict[str, int] = {}
        self.calls: list[tuple] = []

    def __call__(self, *a):
        self.calls.append(a)
        verb, label = a[0], a[-1].split("/")[-1]
        if verb == "print":
            if label in self.going:
                self.going[label] -= 1
                if self.going[label] < 0:
                    self.loaded.discard(label); del self.going[label]
            if label not in self.loaded:
                return subprocess.CompletedProcess(a, 113, "", "")
            return subprocess.CompletedProcess(a, 0, print_out(label, self.env.get(label, {})), "")
        if verb == "list":
            out = "PID\tStatus\tLabel\n" + "".join(f"1{i}\t0\t{l}\n" for i, l in enumerate(sorted(self.loaded)))
            return subprocess.CompletedProcess(a, 0, out, "")
        if verb == "bootout" and label in self.loaded and label not in self.stuck:
            self.going[label] = self.linger
        if verb == "bootstrap":
            label = os.path.basename(a[2]).removesuffix(".plist")
            self.loaded.add(label)
            try:
                self.env[label] = plistlib.loads(open(a[2], "rb").read()).get("EnvironmentVariables", {})
            except Exception:
                self.env[label] = {}
        return subprocess.CompletedProcess(a, 0, "", "")

    def naming(self, label):
        return [c for c in self.calls if c[-1].split("/")[-1] == label
                or (c[0] == "bootstrap" and c[2].endswith(f"/{label}.plist"))]

    def verbs(self, label):
        return [c[0] for c in self.naming(label)]


@pytest.fixture
def launchd(monkeypatch, tmp_path):
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    monkeypatch.setattr(services, "AGENTS", agents)
    monkeypatch.setattr(services, "LOGS", tmp_path / "logs")
    monkeypatch.setattr(services, "seed_stt", lambda **kw: "stt kept")
    monkeypatch.setattr(services, "silero", lambda dry_run=False: (True, "silero present"))
    monkeypatch.setattr(services, "install_schema", lambda: "schema")
    monkeypatch.setattr(services, "BOOT_WAIT_S", 0.05)
    monkeypatch.setattr("time.sleep", lambda s: None)
    box = {}

    def make(**kw):
        box["fake"] = FakeLaunchd(**kw)
        monkeypatch.setattr(services, "_launchctl", box["fake"])
        return box["fake"]
    return agents, make


def ours_plist(path, label=None):
    """A plist at PATH as `ears services install` writes one: its label and its MARKER."""
    label = label or path.name.removesuffix(".plist")
    path.write_bytes(plistlib.dumps({"Label": label, "ProgramArguments": ["/usr/bin/true"],
                                     "EnvironmentVariables": dict([services.MARKER])}))
    return path


def _old_plist(agents, n="keeper"):
    p = agents / f"{OLD}{n}.plist"
    p.write_text("<plist/>")
    return p


def test_the_labels_are_ours_now():
    assert services.PREFIX == NEW and services.OLD_PREFIXES == (PREV, OLD)
    assert services.spec("keeper")["Label"] == NEW + "keeper"
    assert services._old_labels("keeper") == [PREV + "keeper", OLD + "keeper"]
    assert services._old_label("keeper") == PREV + "keeper"
    assert services._old_labels("speech") == ["com.sessionvoice.mlx-audio"]
    # every old label a host may hold: each service under both old prefixes, speech's own
    assert {l for n in services.ALL for l in services._old_labels(n)} == {
        p + n for p in (PREV, OLD) for n in ("stt", "mouth", "keeper", "mail", "speakers")} | {
        "com.sessionvoice.mlx-audio"}
    assert services._labels("call") == [PREV + "call", OLD + "call"]
    assert services._labels("mouth") == [NEW + "mouth", PREV + "mouth", OLD + "mouth"]


def test_old_loaded_with_its_plist_moves_once(launchd, capsys):
    agents, make = launchd
    fake = make(loaded={OLD + "keeper"}, linger=2)      # the room's keeper, as loaded on a host
    plist = _old_plist(agents)
    assert services.install(["keeper"]) == 0
    assert fake.verbs(OLD + "keeper").count("bootout") == 1
    boots = fake.verbs(NEW + "keeper").count("bootstrap")
    assert boots == 1 and NEW + "keeper" in fake.loaded and OLD + "keeper" not in fake.loaded
    assert not plist.exists() and (agents / f"{NEW}keeper.plist").is_file()
    # the load waited for the old job to go: its last print came before the bootstrap
    i_boot = fake.calls.index(fake.naming(NEW + "keeper")[-1])
    i_last_old = max(fake.calls.index(c) for c in fake.naming(OLD + "keeper") if c[0] == "print")
    assert i_last_old < i_boot
    out = capsys.readouterr().out
    assert f"old label {OLD}keeper (loaded): booted out, plist removed" in out
    assert "keeper loaded" in out


def test_the_old_plist_alone_is_removed_with_no_bootout(launchd, capsys):
    agents, make = launchd
    fake = make()
    plist = _old_plist(agents)
    assert services.install(["keeper"]) == 0
    assert "bootout" not in fake.verbs(OLD + "keeper") and not plist.exists()
    assert fake.verbs(NEW + "keeper").count("bootstrap") == 1
    assert "plist only, not loaded" in capsys.readouterr().out


def test_nothing_old_makes_no_old_label_call(launchd):
    agents, make = launchd
    fake = make()
    assert services.install(["keeper", "mouth"]) == 0
    assert not fake.naming(OLD + "keeper") and not fake.naming(OLD + "mouth")


def test_a_second_install_makes_zero_old_label_calls(launchd):
    agents, make = launchd
    fake = make(loaded={OLD + "keeper", OLD + "mouth"}, linger=1)
    _old_plist(agents); _old_plist(agents, "mouth")
    assert services.install(["mouth", "keeper"]) == 0
    first = len(fake.calls)
    assert services.install(["mouth", "keeper"]) == 0
    again = fake.calls[first:]
    assert not [c for c in again if OLD in " ".join(c)]
    assert [c[0] for c in again if c[0] == "bootstrap"] == ["bootstrap", "bootstrap"]   # one each, as before


def test_an_old_label_that_will_not_go_fails_loud_and_loads_nothing(launchd, capsys):
    agents, make = launchd
    fake = make(loaded={OLD + "keeper"}, stuck={OLD + "keeper"})
    _old_plist(agents)
    assert services.install(["keeper"]) == 2
    assert "bootstrap" not in fake.verbs(NEW + "keeper")
    assert NEW + "keeper" not in fake.loaded and OLD + "keeper" in fake.loaded
    assert fake.verbs(OLD + "keeper").count("bootout") == 1          # once, never a second try
    cap = capsys.readouterr()
    assert "keeper FAILED" in cap.err and f"{OLD}keeper is still loaded" in cap.err
    assert f"{NEW}keeper NOT loaded" in cap.out


def test_one_stuck_service_does_not_stop_the_others(launchd):
    agents, make = launchd
    fake = make(loaded={OLD + "keeper", OLD + "mouth"}, stuck={OLD + "keeper"})
    assert services.install(["mouth", "keeper"]) == 2
    assert NEW + "mouth" in fake.loaded and NEW + "keeper" not in fake.loaded


def test_the_bootstrap_succeeds_at_most_once(launchd):
    agents, make = launchd
    fake = make(loaded={OLD + "mail"})
    calls = []
    real = fake

    def once(*a):        # the bootstrap's retry is only for a failed first try
        calls.append(a)
        return real(*a)
    services._launchctl = once
    path = agents / f"{NEW}mail.plist"
    r = services._bootstrap(os.getuid(), "mail", path)
    assert r.returncode == 0 and [c[0] for c in calls].count("bootstrap") == 1


def test_bootstrap_waits_for_the_old_label_it_was_given(launchd):
    agents, make = launchd
    fake = make(loaded={OLD + "keeper"}, linger=3)
    fake("bootout", f"gui/{os.getuid()}/{OLD}keeper")
    r = services._bootstrap(os.getuid(), "keeper", agents / f"{NEW}keeper.plist", wait_s=5, old=OLD + "keeper")
    assert r.returncode == 0 and OLD + "keeper" not in fake.loaded
    assert fake.verbs(OLD + "keeper") == ["bootout", "print", "print", "print", "print"]


def test_dry_run_names_the_old_label_and_changes_nothing(launchd, capsys):
    agents, make = launchd
    fake = make(loaded={OLD + "keeper"})
    plist = _old_plist(agents)
    services.install(["keeper"], dry_run=True)
    assert "would be booted out" in capsys.readouterr().out and plist.exists()
    assert not [c for c in fake.calls if c[0] not in ("print", "list")]


def test_uninstall_removes_every_label(launchd):
    agents, make = launchd
    fake = make(loaded={OLD + "mouth", PREV + "mouth", NEW + "mouth"})
    for p in (agents / f"{OLD}mouth.plist", agents / f"{PREV}mouth.plist"):
        p.write_text("x")
    ours_plist(agents / f"{NEW}mouth.plist")
    services.uninstall(["mouth"])
    outs = {c[1] for c in fake.calls if c[0] == "bootout"}
    assert outs == {f"gui/{os.getuid()}/{l}mouth" for l in (OLD, PREV, NEW)}
    assert not list(agents.glob("*mouth.plist"))


def test_status_reports_an_old_label_still_loaded(launchd, monkeypatch, capsys):
    agents, make = launchd
    make(loaded={OLD + "keeper"})
    monkeypatch.setattr(services.speech, "probe", lambda *a, **kw: None)
    monkeypatch.setattr(services.speech, "status_row", lambda p: "-")
    monkeypatch.setattr(services, "weights_status", lambda: [])
    services.status()
    out = capsys.readouterr().out
    keeper = [l for l in out.splitlines() if l.startswith("keeper")]
    assert keeper and "running, pid" in keeper[0]                 # the job still runs, under the old name
    assert f"old label: {OLD}keeper is still loaded; `ears services install --only keeper` moves it" in out


def test_status_says_when_both_labels_are_loaded(launchd, monkeypatch, capsys):
    agents, make = launchd
    make(loaded={OLD + "mouth", NEW + "mouth"})
    monkeypatch.setattr(services.speech, "probe", lambda *a, **kw: None)
    monkeypatch.setattr(services.speech, "status_row", lambda p: "-")
    monkeypatch.setattr(services, "weights_status", lambda: [])
    services.status()
    assert f"{OLD}mouth is still loaded BESIDE {NEW}mouth" in capsys.readouterr().out


@pytest.mark.parametrize("loaded, want", [
    ({NEW + "speakers", OLD + "speakers"}, NEW + "speakers"),        # the new label first
    ({OLD + "speakers"}, OLD + "speakers"),                          # a host not moved yet
])
def test_speakers_restarts_whichever_label_is_loaded(launchd, monkeypatch, loaded, want):
    agents, make = launchd
    fake = make(loaded=loaded)
    said = []
    monkeypatch.setattr(speakers, "_say", said.append)
    speakers._restart()
    kicks = [c for c in fake.calls if c[0] == "kickstart"]
    assert kicks == [("kickstart", "-k", f"gui/{os.getuid()}/{want}")]
    assert f"restarted {want}" in said[0]


def test_speakers_with_neither_label_loaded_names_the_install(launchd, monkeypatch):
    agents, make = launchd
    fake = make()
    said = []
    monkeypatch.setattr(speakers, "_say", said.append)
    speakers._restart()
    assert not [c for c in fake.calls if c[0] == "kickstart"]
    assert said[0].startswith(f"{NEW}speakers is not loaded")
