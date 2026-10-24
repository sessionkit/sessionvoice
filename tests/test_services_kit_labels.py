"""EARS-407: every launchd label the kit ships is dev.sessionkit.voice.<job>, and `ears services
install` retires or recognises every old form, idempotent and dry-run aware, never two of a job.

The old forms, by what install does with each:

- MOVE: an install-made agent under an old label (com.sessionvoice.ears-NAME,
  com.voicemode.ears-NAME, the call and room agents, com.sessionvoice.mlx-audio). Booted out
  once, its plist removed, and ours loaded only once it has gone.
- LEGACY: a stale hand render (services.LEGACY: the dev.voicemode mailer and the old hand
  mouth players). Booted out, its plist moved aside, and ours loaded.
- HAND: a hand render under a name the template carried before (services.HAND: the mailer's,
  the keeper's, the side-car's, the sounds' and talk's old names). Left alone, ours skipped
  (and ours removed when it was there first). (The sounds and talk were NAMED, never touched,
  until install ran them, EARS-429: every template is a job install runs now.)

Each form is run through five cases: old only, new only, both, neither, and a re-run that is a
no-op; plus a dry run that writes nothing. Then the rule that makes the shared labels safe:
install tells its own plist from a hand render by CONTENT (services.MARKER in
EnvironmentVariables), never by label, both ways.

launchctl is a FAKE that keeps its own loaded set (test_services_label_move.FakeLaunchd),
AGENTS is a temp dir under a temp HOME, and the speech probe is faked: nothing here asks or
changes the live launchd, and no live LaunchAgents dir is read or written."""
import os
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

from ears import services, speech
from ears.speech import Holder, Job, Probe
from test_services_install_checks import _store, fence  # noqa: F401  (fence: a fixture)
from test_services_label_move import FakeLaunchd, ours_plist
from test_services_speakers import sp  # noqa: F401  (a fixture)

REPO = Path(__file__).resolve().parents[1]
UID = os.getuid()
LOOK = ("print", "list")
NEW = "dev.sessionkit.voice."

MOVE = [(n, p + n) for p in services.OLD_PREFIXES for n in ("stt", "mouth", "keeper", "mail", "speakers")] + [
    ("keeper", p + o) for p in services.OLD_PREFIXES for o in services.OLD] + [
    ("speech", "com.sessionvoice.mlx-audio")]
LEGACY = [("mail", "dev.voicemode.ears-mail"), ("mouth", "dev.voicemode.mouth-mail"),
          ("mouth", "com.failmode.mouth-mail")]
HAND = [("mail", "dev.sessionvoice.ears-mail"), ("speakers", "com.failmode.ears-speakers"),
        ("speakers", "com.failmode.speaker-sidecar"), ("keeper", "com.failmode.ears-keeper")] + [
    # the sounds' and talk's old hand names: HAND since install runs them (EARS-429)
    (j, f"{p}.{j}") for p in ("dev.sessionvoice", "dev.voicemode") for j in ("ears-sounds", "mouth-sounds", "talk")]
FORMS = [("move", *f) for f in MOVE] + [("legacy", *f) for f in LEGACY] + [("hand", *f) for f in HAND]
CASES = ("old-only", "new-only", "both", "neither", "rerun")


def test_every_old_form_in_the_card_is_covered():
    """README Objective item 5, form by form: each is in exactly one class above, and the
    classes are services' own tables."""
    labels = [f[2] for f in FORMS]
    assert len(labels) == len(set(labels))
    assert {l for _, l in MOVE} == {l for n in (*services.jobs(services.ALL), *services.OLD)
                                    for l in services._old_labels(n)}
    assert {l for _, l in LEGACY} == {l for ls in services.LEGACY.values() for l in ls}
    assert {l for _, l in HAND} == {l for ls in services.HAND.values() for l in ls}
    assert not hasattr(services, "HAND_RENAMED")    # no shipped template is a job install skips
    for form in ("com.sessionvoice.ears-", "com.sessionvoice.mlx-audio", "com.voicemode.ears-",
                 "dev.sessionvoice.", "dev.voicemode.", "com.failmode.ears-keeper",
                 "com.failmode.ears-speakers", "com.failmode.speaker-sidecar", "com.failmode.mouth-mail"):
        assert any(l.startswith(form) for l in labels), form


def test_every_shipped_label_is_a_kit_label():
    for n in services.jobs(services.ALL):
        assert services._label(n).startswith(NEW), n
    assert speech.LABEL == NEW + "mlx-audio"
    for t in sorted((REPO / "launchd").glob("*.plist.in")):
        assert t.name.startswith(NEW), t.name
        label = t.name.removesuffix(".plist.in")
        assert f"<key>Label</key><string>{label}</string>" in t.read_text(), t.name
    assert '"dev.sessionkit.voice.multi-output." + newName' in (REPO / "bin/multi-output.swift").read_text()


# ---- the fixture: the fence, a stateful fake launchctl, and a faked speech probe ----------------

@pytest.fixture
def kit(sp, monkeypatch):  # noqa: F811
    """sp (temp HOME and AGENTS, the mail and speakers checks passing) with a FakeLaunchd in
    place of its launchctl. ``make(**kw)`` sets the loaded labels. The speech probe sees ours
    (new or old) holding :8890 when it is loaded, else nothing."""
    monkeypatch.setattr(services, "BOOT_WAIT_S", 0.05)
    monkeypatch.setattr(services, "_stray_sidecars", lambda: None)
    services.AGENTS.mkdir(parents=True, exist_ok=True)
    _store(sp["home"])
    box = {}

    def probe(agents, listed, **kw):
        mine = (speech.LABEL, *speech.OLD_LABELS)
        jobs = tuple(Job(label=l, plist=agents / f"{l}.plist", pid=listed.get(l)) for l in mine
                     if l in listed or (agents / f"{l}.plist").exists())
        holder = next((l for l in mine if l in listed), None)
        return Probe(port=kw.get("port", speech.PORT), holder=Holder(pid=4242, label=holder) if holder else None,
                     connects=bool(holder), identity="mlx-audio" if holder else None, jobs=jobs)
    monkeypatch.setattr(speech, "probe", probe)

    def make(**kw):
        box["l"] = FakeLaunchd(**kw)
        monkeypatch.setattr(services, "_launchctl", box["l"])
        return box["l"]
    sp["make"] = make
    return sp


def _write_old(label):
    """A plist under an old LABEL, as a host kept one: an mlx-audio server for speech's."""
    p = services.AGENTS / f"{label}.plist"
    args = (["/x/venv/bin/python", "-m", "mlx_audio.server", "--port", str(speech.PORT)]
            if "mlx-audio" in label else ["/usr/bin/true"])
    p.write_bytes(plistlib.dumps({"Label": label, "ProgramArguments": args}))
    return p


def _ours(n):
    return services.AGENTS / f"{services._label(n)}.plist"


def _changing(fake, label):
    """The calls on LABEL that change launchd (print and list only look)."""
    return [c for c in fake.naming(label) if c[0] not in LOOK]


def _setup(kit, cls, n, old, case):
    """The host for CASE: OLD loaded with its plist and/or ours loaded with its plist."""
    loaded, new = set(), None
    if case in ("old-only", "both", "rerun"):
        _write_old(old)
        loaded.add(old)
    if case in ("new-only", "both"):
        new = ours_plist(_ours(n))
        loaded.add(services._label(n))
        if n == "speech":   # ours runs the server too: the never-two scan sees it
            d = plistlib.loads(new.read_bytes())
            d["ProgramArguments"] = ["/x/venv/bin/python", "-m", "mlx_audio.server"]
            new.write_bytes(plistlib.dumps(d))
    return kit["make"](loaded=loaded, linger=1), new


def _install(n):
    return services.install([n])


# ---- the retire matrix: every old form x {old only, new only, both, neither, re-run} --------------

@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("cls, n, old", FORMS, ids=[f[2] for f in FORMS])
def test_retire_matrix(kit, capsys, cls, n, old, case):
    fake, new = _setup(kit, cls, n, old, case)
    old_plist = services.AGENTS / f"{old}.plist"
    old_body = old_plist.read_bytes() if old_plist.exists() else None
    assert _install(n) == 0
    out = capsys.readouterr().out
    if case == "rerun":                      # the second install, on the host the first left
        for l in list(fake.going):           # time passes: launchd has finished every bootout
            fake.loaded.discard(l)
            del fake.going[l]
        first = fake.calls[:]
        agents_after_first = sorted(p.name for p in services.AGENTS.iterdir())
        ours_after_first = _ours(n).read_bytes() if n and _ours(n).exists() else None
        assert _install(n) == 0
        out = capsys.readouterr().out
        again = fake.calls[len(first):]
        assert sorted(p.name for p in services.AGENTS.iterdir()) == agents_after_first
        if cls in ("move", "legacy"):
            # nothing old is left to act on: no call changes the old label (MOVE asks none at all)
            assert not [c for c in again if c[-1].split("/")[-1] == old and c[0] not in LOOK]
            if cls == "move":
                assert not [c for c in again if old in " ".join(c)]
        if ours_after_first is not None:     # ours is the same job, byte for byte
            assert _ours(n).read_bytes() == ours_after_first
        case = "old-only"                    # and the host is as the first install left it

    ours_loaded = services._label(n) in fake.loaded
    if cls == "move":
        if case in ("old-only", "both"):
            assert fake.verbs(old).count("bootout") == 1 and not old_plist.exists()
            if not old.endswith(("-call", "-room")):   # ours waited for it to go (the room ears
                assert old not in fake.loaded           # are retired, the keeper waits on its own)
        if case in ("neither", "new-only") and n != "speech":
            assert not fake.naming(old)      # no sign of it: no call names it at all
        assert ours_loaded and services._ours(_ours(n))
    elif cls == "legacy":
        asides = sorted(services.AGENTS.glob(f"{old}.plist.replaced-*"))
        if case in ("old-only", "both"):
            assert old not in fake.loaded and not old_plist.exists()
            assert len(asides) == 1 and asides[0].read_bytes() == old_body
        else:
            assert not asides and not _changing(fake, old)
        assert ours_loaded and services._ours(_ours(n))
    else:                                    # hand
        if case in ("old-only", "both"):
            assert old in fake.loaded and old_plist.read_bytes() == old_body   # theirs, untouched
            assert not _changing(fake, old)
            assert f"{n} SKIPPED: the hand-made {old}" in out
            assert not _ours(n).exists() and not fake.verbs(services._label(n)).count("bootstrap")
            if case == "both":                      # ours was there first: it goes, never two
                assert f"WARNING: {n} ours REMOVED" in out
                assert fake.verbs(services._label(n)).count("bootout") == 1
            else:
                assert services._label(n) not in fake.loaded
        else:
            assert ours_loaded and services._ours(_ours(n))
            assert not _changing(fake, old)


@pytest.mark.parametrize("cls, n, old", FORMS, ids=[f[2] for f in FORMS])
def test_dry_run_writes_nothing(kit, capsys, cls, n, old):
    """Old and new both on the host, both loaded: a dry run only looks."""
    fake, _ = _setup(kit, cls, n, old, "both")
    before = {p.name: p.read_bytes() for p in services.AGENTS.iterdir()}
    loaded = set(fake.loaded)
    services.install([n], dry_run=True)
    assert {p.name: p.read_bytes() for p in services.AGENTS.iterdir()} == before
    assert fake.loaded == loaded
    assert not [c for c in fake.calls if c[0] not in LOOK]
    out = capsys.readouterr().out
    if cls == "move" and not old.endswith(("-call", "-room")):
        assert f"old label {old} (loaded): would be booted out" in out
    if cls == "legacy":
        assert f"LEGACY {old} (loaded)" in out and "would be REPLACED" in out
    if cls == "hand":
        assert f"{n} SKIPPED" in out and "ours would be REMOVED" in out


@pytest.mark.parametrize("cls, n, old", FORMS, ids=[f[2] for f in FORMS])
def test_status_names_every_old_form(kit, capsys, monkeypatch, cls, n, old):
    monkeypatch.setattr(services, "weights_status", lambda: [])
    _write_old(old)
    kit["make"](loaded={old})
    services.status()
    out = capsys.readouterr().out
    line = next((l for l in out.splitlines() if old in l and not l.startswith("speech ")), None)
    assert line, out
    if cls == "move" and old.endswith(("-call", "-room")):   # their own row, naming the label
        assert line.startswith(old.rsplit("-", 1)[1]) and f"(OLD {old}: the keeper runs it now" in line
    elif cls == "move":
        assert f"old label: {old} is still loaded" in line
    elif cls == "legacy":
        assert "(LEGACY render)" in line
    else:                                    # hand
        assert "(hand-made)" in line and "install skips" in line


# ---- whose plist: told by CONTENT, never by label ------------------------------------------------

def test_every_plist_install_writes_carries_the_marker_in_its_environment():
    for n in services.jobs(services.ALL):
        env = services.spec(n)["EnvironmentVariables"]
        assert env[services.MARKER[0]] == services.MARKER[1], n
    k, v = services.MARKER
    assert k == "SESSIONKIT_INSTALLED_BY" and "failmode" not in (k + v).lower()


def _render(template, home, *args):
    return subprocess.run([sys.executable, str(REPO / "bin/render-plist"), *map(str, args), str(template)],
                          capture_output=True, text=True, timeout=30,
                          env={"HOME": str(home), "PATH": os.environ["PATH"], "SPEAKER_PY": "/x/py",
                               "AGENT_REFS": "ada", "TALK": "/x/talk"})


@pytest.mark.parametrize("t", sorted((REPO / "launchd").glob("*.plist.in")), ids=lambda p: p.name)
def test_no_template_renders_the_marker(t, tmp_path):
    r = _render(t, tmp_path)
    assert r.returncode == 0, r.stderr
    d = plistlib.loads(r.stdout.encode())
    assert services.MARKER[0] not in d.get("EnvironmentVariables", {})


SHARED = [("keeper", "ears-keeper"), ("mail", "ears-mail"), ("speakers", "ears-speakers")]


@pytest.mark.parametrize("n, job", SHARED)
def test_three_templates_carry_installs_own_label(n, job):
    assert services._label(n) == NEW + job
    assert (REPO / "launchd" / f"{NEW}{job}.plist.in").is_file()


@pytest.mark.parametrize("n", ["keeper", "mail", "speakers", "mouth"])
def test_a_hand_render_at_the_new_label_is_never_overwritten(kit, capsys, monkeypatch, n):
    """Super ruling 18:13, 2: a hand render at the new name, then install: the plist is
    unchanged, ours is skipped, and status names it. uninstall leaves it too."""
    monkeypatch.setattr(services, "weights_status", lambda: [])
    label = services._label(n)
    hand = _ours(n)
    hand.write_bytes(plistlib.dumps({"Label": label, "ProgramArguments": ["/usr/bin/true"],
                                     "EnvironmentVariables": {"PATH": "/usr/bin:/bin"}}))
    body = hand.read_bytes()
    fake = kit["make"](loaded={label})
    assert services.install([n]) == 0
    out = capsys.readouterr().out
    assert f"{n} SKIPPED: the hand-made {label} (its plist was not written by `ears services install`)" in out
    assert hand.read_bytes() == body and label in fake.loaded
    assert not _changing(fake, label)                # never booted out, never bootstrapped
    services.status()
    assert f"{label} (hand-made): running" in capsys.readouterr().out
    services.uninstall([n])
    assert hand.read_bytes() == body and not _changing(fake, label)
    assert f"left the hand-made {label}" in capsys.readouterr().out


@pytest.mark.parametrize("n", ["keeper", "mail", "speakers"])
def test_an_unreadable_plist_at_the_new_label_is_hand_made(kit, capsys, n):
    hand = _ours(n)
    hand.write_text("not a plist")
    kit["make"]()
    assert services.install([n]) == 0
    assert hand.read_text() == "not a plist" and f"{n} SKIPPED" in capsys.readouterr().out


@pytest.mark.parametrize("n", ["keeper", "mail", "speakers", "mouth", "stt"])
def test_ours_at_the_new_label_is_ours_and_a_rerun_is_a_no_op(kit, capsys, n):
    """The reverse of the hand case: the plist install wrote, found again, is ours. A re-run
    replaces it with the same job, skips nothing, and names no old label."""
    fake = kit["make"]()
    assert services.install([n]) == 0
    first_out = capsys.readouterr().out
    body = _ours(n).read_bytes()
    assert services._ours(_ours(n)) and "SKIPPED" not in first_out
    first = len(fake.calls)
    assert services.install([n]) == 0
    out = capsys.readouterr().out
    again = fake.calls[first:]
    assert "SKIPPED" not in out and _ours(n).read_bytes() == body
    assert [c[0] for c in again if c[0] == "bootstrap"] == ["bootstrap"]          # once, as before
    assert not [c for c in again if any(p in " ".join(c) for p in services.OLD_PREFIXES)]
    assert services._label(n) in fake.loaded


def test_rendering_a_template_over_ours_replaces_installs_job(kit, capsys):
    """Super ruling 18:13, 3: rendering the hand template over the plist install wrote replaces
    install's job (render-plist -o is the user's own act). The next install leaves that render
    alone and skips ours: never two keepers."""
    fake = kit["make"]()
    assert services.install(["keeper"]) == 0
    assert services._ours(_ours("keeper"))
    capsys.readouterr()
    r = _render(REPO / "launchd/dev.sessionkit.voice.ears-keeper.plist.in", kit["home"], "-o", services.AGENTS)
    assert r.returncode == 0, r.stderr
    body = _ours("keeper").read_bytes()
    assert not services._ours(_ours("keeper"))       # the render: no marker
    first = len(fake.calls)
    assert services.install(["keeper"]) == 0
    out = capsys.readouterr().out
    assert "keeper SKIPPED" in out and _ours("keeper").read_bytes() == body
    assert not [c for c in fake.calls[first:] if c[0] in ("bootout", "bootstrap")
                and services._label("keeper") in " ".join(c)]


def test_render_plist_writes_only_stdout_or_its_out_dir(tmp_path):
    """The reverse case is only ever the user's: render-plist never writes a LaunchAgents path
    on its own. With no -o it writes nothing at all."""
    before = sorted(tmp_path.rglob("*"))
    r = _render(REPO / "launchd/dev.sessionkit.voice.ears-keeper.plist.in", tmp_path)
    assert r.returncode == 0 and r.stdout.startswith("<?xml")
    assert sorted(tmp_path.rglob("*")) == before


def test_a_hand_keeper_under_its_old_name_means_one_keeper(kit, capsys):
    """com.failmode.ears-keeper (the template's old name) is a hand keeper: before EARS-407
    install loaded ours beside it, two keepers on one capture."""
    _write_old("com.failmode.ears-keeper")
    fake = kit["make"](loaded={"com.failmode.ears-keeper"})
    assert services.install(["keeper"]) == 0
    out = capsys.readouterr().out
    assert "keeper SKIPPED: the hand-made com.failmode.ears-keeper" in out
    assert "two keepers would fight over the room's capture" in out
    assert services._label("keeper") not in fake.loaded and not _ours("keeper").exists()


def test_a_hand_keeper_still_retires_the_old_room_ears(kit, capsys):
    _write_old("com.failmode.ears-keeper")
    _write_old("com.voicemode.ears-room")
    fake = kit["make"](loaded={"com.failmode.ears-keeper", "com.voicemode.ears-room"})
    assert services.install(["keeper"]) == 0
    assert fake.verbs("com.voicemode.ears-room").count("bootout") == 1
    assert not (services.AGENTS / "com.voicemode.ears-room.plist").exists()
    assert "com.failmode.ears-keeper" in fake.loaded and not _changing(fake, "com.failmode.ears-keeper")


def test_an_old_label_that_will_not_go_stops_that_service_only(kit, capsys):
    old = "com.sessionvoice.ears-keeper"
    _write_old(old)
    fake = kit["make"](loaded={old}, stuck={old})
    assert services.install(["keeper"]) == 2
    assert services._label("keeper") not in fake.loaded and old in fake.loaded
    assert f"{old} is still loaded" in capsys.readouterr().err


def test_both_old_labels_at_once_are_both_moved_before_ours_loads(kit):
    olds = [p + "mouth" for p in services.OLD_PREFIXES]
    for o in olds:
        _write_old(o)
    fake = kit["make"](loaded=set(olds), linger=2)
    assert services.install(["mouth"]) == 0
    assert not set(olds) & fake.loaded and services._label("mouth") in fake.loaded
    i_boot = fake.calls.index(fake.naming(services._label("mouth"))[-1])
    for o in olds:
        assert fake.verbs(o).count("bootout") == 1
        assert max(fake.calls.index(c) for c in fake.naming(o)) < i_boot     # gone before ours


# ---- speech: ours under its old label is moved, never a second server ---------------------------

def _p(holder=None, jobs=(), identity="mlx-audio"):
    return Probe(holder=holder, connects=holder is not None, identity=identity if holder else None,
                 jobs=tuple(jobs))


OLD_SPEECH = "com.sessionvoice.mlx-audio"


def test_decide_moves_ours_holding_the_port_under_its_old_label():
    d = speech.decide(_p(Holder(pid=7, label=OLD_SPEECH), [Job(OLD_SPEECH, Path("x"), "7")]))
    assert d.kind == "install" and OLD_SPEECH in d.line and speech.LABEL in d.line


def test_decide_moves_ours_loaded_and_down_under_its_old_label():
    d = speech.decide(_p(None, [Job(OLD_SPEECH, Path("x"), "-")]))
    assert d.kind == "install"


def test_decide_with_both_of_ours_loaded_moves_the_old():
    d = speech.decide(_p(Holder(pid=7, label=speech.LABEL),
                         [Job(OLD_SPEECH, Path("x"), "-"), Job(speech.LABEL, Path("y"), "7")]))
    assert d.kind == "install"


def test_decide_never_moves_beside_another_server():
    vm = Job("com.voicemode.mlx-audio", Path("v"), "-")
    d = speech.decide(_p(None, [Job(OLD_SPEECH, Path("x"), "-"), vm]))
    assert d.kind == "refuse-loaded-down"


def test_decide_never_moves_when_an_unknown_holder_has_the_port():
    d = speech.decide(_p(Holder(pid=9, command="/usr/bin/python3 -m http.server"),
                         [Job(OLD_SPEECH, Path("x"), "-")], identity="foreign"))
    assert d.kind == "refuse-foreign"


def test_decide_uses_ours_under_the_new_label_and_moves_nothing():
    d = speech.decide(_p(Holder(pid=7, label=speech.LABEL), [Job(speech.LABEL, Path("y"), "7")]))
    assert d.kind == "use"


def test_status_names_ours_still_under_the_old_speech_label(kit, capsys, monkeypatch):
    monkeypatch.setattr(services, "weights_status", lambda: [])
    _write_old(OLD_SPEECH)
    kit["make"](loaded={OLD_SPEECH})
    services.status()
    assert (f"old label: {OLD_SPEECH} is still loaded; `ears services install --only speech` moves it"
            in capsys.readouterr().out)


# ---- super ruling 18:57: a job loaded with NO plist is judged by its environment, not its name ---

@pytest.mark.parametrize("n", ["keeper", "mail", "speakers", "mouth"])
def test_loaded_with_no_plist_and_the_marker_in_its_environment_is_ours(kit, capsys, n):
    label = services._label(n)
    fake = kit["make"](loaded={label}, env={label: dict([services.MARKER])})
    assert not _ours(n).exists()
    assert services.install([n]) == 0
    out = capsys.readouterr().out
    assert "SKIPPED" not in out
    assert ("bootout", f"gui/{UID}/{label}") in fake.calls                    # ours replaced
    assert fake.verbs(label).count("bootstrap") == 1 and services._ours(_ours(n))


@pytest.mark.parametrize("n", ["keeper", "mail", "speakers", "mouth"])
def test_loaded_with_no_plist_and_no_marker_is_hand_made(kit, capsys, monkeypatch, n):
    """A hand render bootstrapped from a plist elsewhere (render-plist -o ~/scratch, then
    launchctl bootstrap): its name is ours, its environment is not. Left alone, said so with
    the line that switches to ours."""
    monkeypatch.setattr(services, "weights_status", lambda: [])
    label = services._label(n)
    fake = kit["make"](loaded={label}, env={label: {"PATH": "/usr/bin:/bin"}})
    assert services.install([n]) == 0
    out = capsys.readouterr().out
    assert f"{n} SKIPPED: the hand-made {label} (loaded from a plist elsewhere; its environment has no " \
           "install marker)" in out
    assert f"To run ours instead: launchctl bootout gui/{UID}/{label}, then `ears services install --only {n}`" in out
    assert not _changing(fake, label) and label in fake.loaded and not _ours(n).exists()
    services.uninstall([n])
    assert not _changing(fake, label) and f"left the hand-made {label}" in capsys.readouterr().out


def test_status_says_ours_is_skipped_beside_a_hand_keeper_and_how_to_switch(kit, capsys, monkeypatch):
    """Call 2 (super 18:57): status says plainly that OURS is skipped because of the hand
    keeper, and how to switch: boot the hand keeper out, then run install."""
    monkeypatch.setattr(services, "weights_status", lambda: [])
    old = "com.failmode.ears-keeper"
    plist = _write_old(old)
    kit["make"](loaded={old})
    services.status()
    line = next(l for l in capsys.readouterr().out.splitlines() if old in l)
    assert "(hand-made)" in line and "install skips keeper while it is here (ours is NOT loaded)" in line
    assert (f"To run ours instead: launchctl bootout gui/{UID}/{old}, move {plist} out of "
            f"{services.AGENTS}, then `ears services install --only keeper`") in line


# ---- foreman 20:25 + super 20:24: a hand plist that is NOT loaded is DORMANT, not stale ----------

@pytest.mark.parametrize("ours_first", [False, True], ids=["ours-absent", "ours-first"])
@pytest.mark.parametrize("n, old", [(n, l) for _, n, l in [("hand", *h) for h in HAND]], ids=[h[1] for h in HAND])
def test_a_dormant_hand_plist_says_why_ours_is_skipped_and_the_way_out(kit, capsys, monkeypatch, n, old, ours_first):
    """An old-name hand plist in LaunchAgents, not loaded: launchd loads it at the next login,
    so it still counts (one job per name). The dry run AND status say it is NOT loaded, why that
    still means ours is skipped, and the way out for its state: move it aside, nothing to boot
    out. The real install obeys it, leaves it byte for byte, and removes ours if ours was first."""
    monkeypatch.setattr(services, "weights_status", lambda: [])
    plist = _write_old(old)
    body = plist.read_bytes()
    loaded = set()
    if ours_first:
        ours_plist(_ours(n))
        loaded.add(services._label(n))
    fake = kit["make"](loaded=loaded)
    way = (f"To run ours instead: move {plist} out of {services.AGENTS} (it is not loaded: nothing to boot "
           f"out), then `ears services install --only {services._service(n)}`")

    services.install([n], dry_run=True)
    out = capsys.readouterr().out
    skip = next(l for l in out.splitlines() if f"{n} SKIPPED" in l)
    assert f"the hand-made {old} is already on this host ({plist}: NOT loaded, {services.DORMANT})" in skip
    assert skip.endswith(way) and "bootout" not in skip
    if ours_first:
        assert (f"WARNING: {n} ours would be REMOVED ({services._label(n)}): the hand-made one is not loaded "
                "now, but launchd loads its plist at the next login") in out
    assert not [c for c in fake.calls if c[0] not in LOOK]          # the dry run only looked

    services.status()
    line = next(l for l in capsys.readouterr().out.splitlines() if old in l)
    assert f"{old} (hand-made): plist present, NOT loaded ({services.DORMANT})" in line
    assert line.endswith(way) and "bootout" not in line

    assert services.install([n]) == 0
    out = capsys.readouterr().out
    assert plist.read_bytes() == body and not _changing(fake, old) and old not in fake.loaded
    assert not _ours(n).exists() and not fake.verbs(services._label(n)).count("bootstrap")
    if ours_first:                           # ours was booted out once (the fake lets it linger)
        assert fake.verbs(services._label(n)).count("bootout") == 1
        assert f"WARNING: {n} ours REMOVED ({services._label(n)}, installed before): the hand-made one is not " \
               "loaded now" in out


def test_status_never_says_ours_is_not_loaded_while_it_runs(kit, capsys, monkeypatch):
    """A dormant hand keeper plist beside our running keeper: status says ours runs now and
    that the next install REMOVES it, never "ours is NOT loaded" (that hid the removal)."""
    monkeypatch.setattr(services, "weights_status", lambda: [])
    _write_old("com.failmode.ears-keeper")                      # dormant: plist, not loaded
    _write_old("com.sessionvoice.ears-keeper")
    kit["make"](loaded={"com.sessionvoice.ears-keeper"})
    services.status()
    line = next(l for l in capsys.readouterr().out.splitlines() if "com.failmode.ears-keeper (hand-made)" in l)
    assert "plist present, NOT loaded (dormant, not stale" in line
    assert "(ours runs now as com.sessionvoice.ears-keeper, and the next install REMOVES it)" in line
    assert "ours is NOT loaded" not in line


def test_removing_ours_beside_a_dormant_hand_one_says_nothing_runs_until_login(kit, capsys):
    _write_old("com.failmode.ears-keeper")
    _write_old("com.sessionvoice.ears-keeper")
    kit["make"](loaded={"com.sessionvoice.ears-keeper"})
    services.install(["keeper"], dry_run=True)
    out = capsys.readouterr().out
    assert ("WARNING: keeper ours would be REMOVED (com.sessionvoice.ears-keeper): the hand-made one is not "
            "loaded now, but launchd loads its plist at the next login; until then NO keeper runs") in out
    assert "(it is not loaded: nothing to boot out), then `ears services install --only keeper`" in out
