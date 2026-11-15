"""The kit's service file when ours does not run: a hand-made job, or a speech server install
did not install.

- a hand-made job ours is skipped for (services.HAND's old names, or a hand render at our own
  label) gets OUR label's file with ``enabled: false`` and the note ``a hand-made <label> runs
  instead``; uninstall, leaving a hand render in place, writes the same note;
- speech not installed, by the step speech.decide took: ours already serving (or loaded and
  down) is ``enabled: true``; :8890 held by another program, final.url elsewhere, or step 4's
  checks refused as a WARNING are ``enabled: false`` with a note saying which;
- a refusal or a dry run writes no file, and no file ever carries a label outside
  ``dev.sessionkit.``.

Every test runs under a temp HOME with launchctl and the speech probe faked
(test_services_kit_labels.kit, test_services_speech.sx, over test_services_install_checks'
fence): nothing here loads a launch agent, asks this host's :8890 or touches the real home."""
import json
import plistlib
from pathlib import Path

import pytest

from ears import service_files, services, speech
from ears.home import services_dir
from ears.speech import Holder, Job, Probe
from test_service_files import errors
from test_services_install_checks import _store, fence  # noqa: F401  (a fixture)
from test_services_kit_labels import _write_old, kit  # noqa: F401  (kit: a fixture)
from test_services_label_move import ours_plist
from test_services_speakers import sp  # noqa: F401  (a fixture)
from test_services_speech import FOREIGN, VM, sx  # noqa: F401  (sx: a fixture)

OURS = speech.LABEL
OTHER = ":8890 is served by another program"


def _read(job: str) -> dict:
    rec = json.loads(service_files.path_for(job).read_text())
    assert errors(rec) == [], rec                     # the kit's schema, every time
    assert rec["label"] == services._label(job) and rec["label"].startswith("dev.sessionkit.")
    return rec


def _hand_at_ours(n: str) -> Path:
    """A hand render at N's own label: our label, no MARKER."""
    label = services._label(n)
    p = services.AGENTS / f"{label}.plist"
    p.write_bytes(plistlib.dumps({"Label": label, "ProgramArguments": ["/usr/bin/true"],
                                  "EnvironmentVariables": {"PATH": "/usr/bin:/bin"}}))
    return p


# ---- a hand-made job ---------------------------------------------------------------------------

@pytest.mark.parametrize("n,hand", [("speakers", "com.failmode.ears-speakers"),
                                    ("keeper", "com.failmode.ears-keeper"),
                                    ("mail", "dev.sessionvoice.ears-mail")])
def test_a_hand_made_job_under_its_old_name_gets_our_file_turned_off(kit, capsys, n, hand):
    _write_old(hand)
    kit["make"](loaded={hand})
    assert services.install([n]) == 0
    assert f"{n} SKIPPED: the hand-made {hand}" in capsys.readouterr().out
    rec = _read(n)
    assert rec["enabled"] is False and rec["note"] == f"a hand-made {hand} runs instead"
    assert rec["label"] == services._label(n) != hand      # our label, never the hand's


def test_hand_made_speakers_is_the_speakers_file_turned_off(kit, capsys):
    _write_old("com.failmode.ears-speakers")
    kit["make"](loaded={"com.failmode.ears-speakers"})
    assert services.install(["speakers"]) == 0
    assert _read("speakers") == {**service_files.record("speakers", False),
                                 "note": "a hand-made com.failmode.ears-speakers runs instead"}
    assert _read("speakers")["label"] == "dev.sessionkit.voice.ears-speakers"


@pytest.mark.parametrize("n", ["keeper", "mail", "speakers", "mouth"])
def test_a_hand_render_at_our_label_install_and_uninstall_say_it_runs(kit, capsys, n):
    label = services._label(n)
    _hand_at_ours(n)
    kit["make"](loaded={label})
    assert services.install([n]) == 0
    note = f"a hand-made {label} runs instead"
    assert _read(n)["enabled"] is False and _read(n)["note"] == note
    service_files.path_for(n).unlink()                   # uninstall makes it again, with the note
    services.uninstall([n])
    assert f"left the hand-made {label}" in capsys.readouterr().out
    assert _read(n)["enabled"] is False and _read(n)["note"] == note


@pytest.mark.parametrize("n,hand", [("speakers", "com.failmode.ears-speakers"),
                                    ("keeper", "com.failmode.ears-keeper"),
                                    ("mail", "dev.sessionvoice.ears-mail")])
def test_uninstall_beside_a_hand_made_job_under_its_old_name_keeps_the_note(kit, capsys, n, hand):
    """Uninstall never boots out a hand job under its old name (HAND), so it still runs: the
    file it rewrites says so, as install's did."""
    _write_old(hand)
    kit["make"](loaded={hand})
    assert services.install([n]) == 0
    fake = kit["make"](loaded={hand})                    # uninstall's own calls, from here
    services.uninstall([n])
    rec = _read(n)
    assert rec["enabled"] is False and rec["note"] == f"a hand-made {hand} runs instead"
    assert fake.naming(hand) == []                       # told by its plist: theirs is never asked


def test_uninstall_dry_run_beside_a_hand_made_job_writes_no_file(kit, capsys):
    _write_old("com.failmode.ears-speakers")
    kit["make"](loaded={"com.failmode.ears-speakers"})
    services.uninstall(["speakers"], dry_run=True)
    assert not services_dir().exists()


def test_uninstall_of_ours_has_no_note(kit, capsys):
    kit["make"]()
    assert services.install(["mouth"]) == 0
    assert _read("mouth")["enabled"] is True and "note" not in _read("mouth")
    services.uninstall(["mouth"])
    assert _read("mouth")["enabled"] is False and "note" not in _read("mouth")


def test_a_dry_run_beside_a_hand_made_job_writes_no_file(kit, capsys):
    _write_old("com.failmode.ears-speakers")
    kit["make"](loaded={"com.failmode.ears-speakers"})
    assert services.install(["speakers"], dry_run=True) == 0
    assert not services_dir().exists()


# ---- speech, not installed ---------------------------------------------------------------------

def test_another_program_serving_8890_is_speech_turned_off(sx, capsys):
    sx["state"]["probe"] = Probe(holder=VM, connects=True, identity="mlx-audio")
    assert services.install(["speech"]) == 0
    assert "step 2, use" in capsys.readouterr().out
    rec = _read("speech")
    assert rec["enabled"] is False and rec["note"] == OTHER
    assert rec["label"] == OURS and "com.voicemode" not in json.dumps(rec)


@pytest.mark.parametrize("probe", [
    Probe(jobs=(Job("com.voicemode.mlx-audio", Path("/x.plist"), "-"),)),          # step 3
    Probe(holder=Holder(pid=7), connects=True, identity="mlx-audio"),             # lsof names no label
    Probe(holder=None, connects=True, identity="mlx-audio"),                      # lsof blind
])
def test_another_job_loaded_or_unnamed_is_speech_turned_off(sx, capsys, probe):
    sx["state"]["probe"] = probe
    assert services.install(["speech"]) == 0
    assert _read("speech")["enabled"] is False and _read("speech")["note"] == OTHER


def test_a_foreign_holder_only_the_mouth_wants_is_speech_turned_off(sx, capsys, monkeypatch):
    monkeypatch.setenv("EARS_STT_URL", "https://stt.example/v1")
    sx["state"]["probe"] = Probe(holder=FOREIGN, connects=True, identity="foreign", why="GET / answered 404")
    assert services.install(["speech", "mouth"]) == 0
    assert "refuse-foreign" in capsys.readouterr().out
    assert _read("speech")["enabled"] is False and _read("speech")["note"] == OTHER
    assert _read("mouth")["enabled"] is True                  # the rest installs


def test_the_note_names_the_port_speech_serves_on(sx, capsys, monkeypatch):
    monkeypatch.setenv("EARS_SPEECH_PORT", "18890")
    sx["state"]["probe"] = Probe(holder=VM, connects=True, identity="mlx-audio")
    assert services.install(["speech"]) == 0
    assert _read("speech")["note"] == ":18890 is served by another program"


@pytest.mark.parametrize("probe", [
    Probe(holder=Holder(pid=4242, label=OURS), connects=True, identity="mlx-audio"),   # ours serves
    Probe(jobs=(Job(OURS, Path("/x.plist"), "-"),)),                                   # ours, loaded, down
])
def test_ours_already_serving_or_loaded_is_speech_turned_on(sx, capsys, probe):
    services.AGENTS.mkdir(parents=True, exist_ok=True)
    ours_plist(services.AGENTS / f"{OURS}.plist")             # as install wrote it: its MARKER
    sx["state"]["probe"] = probe
    assert services.install(["speech"]) == 0
    rec = _read("speech")
    assert rec == service_files.record("speech", True)        # enabled, no note


def test_a_hand_render_at_our_speech_label_serving_is_turned_off(sx, capsys):
    services.AGENTS.mkdir(parents=True, exist_ok=True)
    _hand_at_ours("speech")
    sx["state"]["probe"] = Probe(holder=Holder(pid=4242, label=OURS), connects=True, identity="mlx-audio")
    assert services.install(["speech"]) == 0
    rec = _read("speech")
    assert rec["enabled"] is False and rec["note"] == f"a hand-made {OURS} runs instead"


def test_final_url_elsewhere_is_speech_opted_out(sx, capsys, monkeypatch):
    monkeypatch.setenv("EARS_STT_URL", "https://stt.example/v1")
    assert services.install(["speech"]) == 0
    assert "step 1, opt-out" in capsys.readouterr().out
    rec = _read("speech")
    assert rec["enabled"] is False and rec["note"] == "final.url points elsewhere; not managed here"


def test_checks_that_skip_speech_say_why_in_one_line(sx, capsys, monkeypatch):
    monkeypatch.setenv("EARS_STT_URL", "https://stt.example/v1")
    monkeypatch.setattr(services, "_mac", lambda: ("x86_64", "13.6"))
    monkeypatch.setattr(services, "_speech_platform",
                        lambda: services._mlx_platform("the speech server", "mlx-audio's runtime"))
    assert services.install(["speech", "mouth"]) == 0
    assert "WARNING: speech SKIPPED" in capsys.readouterr().out
    rec = _read("speech")
    assert rec["enabled"] is False and "\n" not in rec["note"]
    assert rec["note"].startswith("not installed: the speech server needs an Apple-silicon Mac")


def test_a_skip_reason_of_many_lines_is_cut_to_its_first(fence):
    d = speech.Decision("install", "speech: nothing on :8890")
    assert services._file_not_loaded(0, "speech", None, d, "first line\nsecond\nthird") == (
        False, "not installed: first line")


def test_a_refused_install_writes_no_speech_file(sx, capsys):
    sx["state"]["probe"] = Probe(holder=FOREIGN, connects=True, identity="foreign", why="GET / answered 404")
    assert services.install(["speech"]) == 2
    assert not services_dir().exists()


def test_a_dry_run_writes_no_speech_file(sx, capsys, monkeypatch):
    sx["state"]["probe"] = Probe(holder=VM, connects=True, identity="mlx-audio")
    assert services.install(["speech"], dry_run=True) == 0
    monkeypatch.setenv("EARS_STT_URL", "https://stt.example/v1")
    assert services.install(["speech"], dry_run=True) == 0
    assert not services_dir().exists()


def test_step_4_is_ours_loaded_and_on(sx, capsys):
    assert services.install(["speech"]) == 0
    assert _read("speech") == service_files.record("speech", True)


# ---- the decision carries its job's label --------------------------------------------------------

@pytest.mark.parametrize("p,kind,label", [
    (Probe(holder=VM, connects=True, identity="mlx-audio"), "use", "com.voicemode.mlx-audio"),
    (Probe(holder=Holder(pid=1, label=OURS), connects=True, identity="mlx-audio"), "use", OURS),
    (Probe(holder=VM, connects=True, identity="no-answer"), "refuse-foreign", "com.voicemode.mlx-audio"),
    (Probe(holder=FOREIGN, connects=True, identity="foreign"), "refuse-foreign", None),
    (Probe(jobs=(Job(OURS, Path("/x.plist"), "-"),)), "refuse-loaded-down", OURS),
    (Probe(), "install", None),
])
def test_the_decision_names_the_job_it_is_about(p, kind, label):
    d = speech.decide(p)
    assert (d.kind, d.label) == (kind, label)
    assert speech.decide(p, "https://stt.example/v1").label is None        # opt-out


def test_no_file_ever_carries_a_label_outside_the_kit(kit, capsys):
    for hand in ("com.failmode.ears-speakers", "com.failmode.ears-keeper", "dev.sessionvoice.ears-mail"):
        _write_old(hand)
    kit["make"](loaded={"com.failmode.ears-speakers", "com.failmode.ears-keeper",
                        "dev.sessionvoice.ears-mail"})
    services.install(list(services.ALL), agent="dave")
    services.uninstall(list(services.ALL))
    files = list(services_dir().iterdir())
    assert files and all(p.suffix == ".json" for p in files)
    for p in files:
        rec = json.loads(p.read_text())
        assert rec["label"].startswith("dev.sessionkit.voice.") and errors(rec) == [], rec
