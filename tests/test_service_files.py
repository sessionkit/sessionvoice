"""`ears services install` writes the kit's service files; `uninstall` turns them off.

One file per launch agent, ``~/.sessionkit/voice/services/<name>.json``, which
``sessionkit service`` reads. Install writes ``enabled: true`` once ours has loaded; uninstall
rewrites it ``enabled: false`` and keeps it. Each file is checked against the kit's own schema,
vendored byte for byte as ``fixtures/sessionkit-service.schema.json`` (the kit's
``src/sessionkit/service.schema.json`` at a881f13), by the small validator below: no
``jsonschema`` dependency. Talk's file must equal the kit's own docs example
(``fixtures/sessionkit-service-talk.json``).

Every test runs under a temp HOME with launchctl faked (test_services_talk.tk, over
test_services_install_checks.fence): nothing here loads a launch agent or touches the real
home."""
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from ears import home as ears_home
from ears import service_files, services
from test_services_install_checks import _store, fence  # noqa: F401  (a fixture)
from test_services_speakers import sp  # noqa: F401  (a fixture)
from test_services_talk import tk, venv  # noqa: F401  (fixtures)

FIXTURES = Path(__file__).parent / "fixtures"
SCHEMA_FILE = FIXTURES / "sessionkit-service.schema.json"
TALK_FILE = FIXTURES / "sessionkit-service-talk.json"
#: the vendored schema's bytes, as the kit has them at a881f13: a change here is a new copy
#: from the kit, never an edit
SCHEMA_SHA256 = "e35f5f76553446c8e52c66274fea775bf119dab3ae69cb7b4ab5458546f51182"
SCHEMA = json.loads(SCHEMA_FILE.read_text())
REQUIRED = ("service", "name", "label", "enabled", "install")


# ---- a small validator for the kit's schema: the few rules it uses ---------------------------

_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool}


def _type_ok(value, want: str) -> bool:
    if want == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, _TYPES[want])


def errors(value, schema: dict = SCHEMA, at: str = "$") -> list[str]:
    """Every way VALUE breaks SCHEMA, read from the schema itself: type, const, enum, pattern,
    minLength, minItems, items, required, properties, oneOf and not. An unknown keyword the
    schema uses fails loudly, so a new rule in a new copy is never silently passed."""
    known = {"$schema", "title", "description", "default", "type", "const", "enum", "pattern",
             "minLength", "minItems", "items", "required", "properties", "oneOf", "not"}
    unknown = set(schema) - known
    assert not unknown, f"the validator does not know {sorted(unknown)}: teach it"
    out: list[str] = []
    if "type" in schema and not _type_ok(value, schema["type"]):
        return [f"{at}: not {schema['type']}: {value!r}"]
    if "const" in schema and value != schema["const"]:
        out.append(f"{at}: not {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{at}: {value!r} not in {schema['enum']}")
    if "pattern" in schema and isinstance(value, str) and not re.search(schema["pattern"], value):
        out.append(f"{at}: {value!r} does not match {schema['pattern']}")
    if "minLength" in schema and isinstance(value, str) and len(value) < schema["minLength"]:
        out.append(f"{at}: shorter than {schema['minLength']}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            out.append(f"{at}: fewer than {schema['minItems']} items")
        if "items" in schema:
            for i, v in enumerate(value):
                out += errors(v, schema["items"], f"{at}[{i}]")
    if isinstance(value, dict):
        out += [f"{at}: {k} missing" for k in schema.get("required", ()) if k not in value]
        for k, sub in schema.get("properties", {}).items():
            if k in value:
                out += errors(value[k], sub, f"{at}.{k}")
    if "oneOf" in schema:
        n = sum(not errors(value, sub, at) for sub in schema["oneOf"])
        if n != 1:
            out.append(f"{at}: matches {n} of oneOf, not exactly 1")
    if "not" in schema and not errors(value, schema["not"], at):
        out.append(f"{at}: matches what it must not")
    return out


def test_the_vendored_schema_is_the_kits_copy():
    assert hashlib.sha256(SCHEMA_FILE.read_bytes()).hexdigest() == SCHEMA_SHA256
    assert SCHEMA["required"] == ["service", "name", "enabled", "install"]


def test_the_validator_says_no_to_a_bad_file():
    good = json.loads(TALK_FILE.read_text())
    assert errors(good) == []
    for bad in ({**good, "service": 2}, {**good, "enabled": "yes"},
                {**good, "label": "com.example.talk"}, {**good, "run": ["talk"]},
                {k: v for k, v in good.items() if k != "label"},
                {k: v for k, v in good.items() if k != "install"},
                {**good, "hazard": ["fire"]}, {**good, "port": "8780"}, {**good, "name": ""}):
        assert errors(bad), bad


# ---- the records ------------------------------------------------------------------------------

def _all_jobs():
    return services.jobs(services.ALL)


def test_the_path_comes_from_ears_home(tmp_path):
    assert ears_home.SERVICES == ".sessionkit/voice/services"
    assert ears_home.services_dir(tmp_path) == tmp_path / ears_home.SERVICES
    assert ears_home.services_dir(tmp_path).parent == ears_home.settings_dir(tmp_path).parent
    for job in _all_jobs():
        assert service_files.path_for(job, tmp_path).parent == ears_home.services_dir(tmp_path)


@pytest.mark.parametrize("job", services.jobs(services.ALL))
def test_every_record_is_a_valid_kit_file_built_from_the_codes_facts(job, monkeypatch):
    for k in ("EARS_SPEECH_PORT", "EARS_STT_PORT"):
        monkeypatch.delenv(k, raising=False)
    for enabled in (True, False):
        rec = service_files.record(job, enabled)
        assert errors(rec) == [], rec
        assert rec["enabled"] is enabled and "note" not in rec
        assert rec["label"] == services._label(job)
        assert rec["label"] == "dev.sessionkit.voice." + rec["name"]
        word = services._service(job)
        assert rec["install"] == f"ears services install --only {word}"
        assert rec["uninstall"] == f"ears services uninstall --only {word}"
        assert ("hazard" in rec) == (job in ("stt", "speech", "mouth", "keeper", "mail"))
        assert rec["description"]


def test_the_names_are_the_labels_tails():
    names = {j: service_files.name_of(j) for j in _all_jobs()}
    assert names == {"stt": "ears-stt", "speech": "mlx-audio", "mouth": "ears-mouth",
                     "keeper": "ears-keeper", "mail": "ears-mail", "speakers": "ears-speakers",
                     "ears-sounds": "ears-sounds", "mouth-sounds": "mouth-sounds", "talk": "talk"}
    assert service_files.record("ears-sounds", True)["install"] == "ears services install --only sounds"


def test_talk_is_the_kits_own_example():
    assert service_files.record("talk", True) == json.loads(TALK_FILE.read_text())


def test_the_ports_follow_the_jobs(monkeypatch):
    for k in ("EARS_SPEECH_PORT", "EARS_STT_PORT"):
        monkeypatch.delenv(k, raising=False)
    sp_ = service_files.record("speech", True)
    assert sp_["port"] == 8890 and sp_["bind"] == "127.0.0.1" and sp_["order"] == 10
    assert sp_["health"] == {"http": "http://127.0.0.1:8890/v1/models"}
    assert service_files.record("stt", True)["port"] == 2023
    monkeypatch.setenv("EARS_SPEECH_PORT", "18890")
    monkeypatch.setenv("EARS_STT_PORT", "12023")
    assert service_files.record("speech", True)["health"] == {"http": "http://127.0.0.1:18890/v1/models"}
    assert service_files.record("stt", True)["port"] == 12023


def test_a_note_only_when_given():
    assert service_files.record("keeper", False, note="why")["note"] == "why"
    assert errors(service_files.record("keeper", False, note="why")) == []


# ---- the write --------------------------------------------------------------------------------

def test_write_is_atomic_and_stays_in_its_folder(tmp_path):
    p = service_files.write("talk", True, home=tmp_path)
    assert p == tmp_path / ".sessionkit/voice/services/talk.json"
    assert json.loads(p.read_text())["enabled"] is True and p.read_text().endswith("}\n")
    assert sorted(x.relative_to(tmp_path).as_posix() for x in tmp_path.rglob("*") if x.is_file()) == [
        ".sessionkit/voice/services/talk.json"]


def test_a_failed_replace_leaves_the_old_file_and_no_temp(tmp_path, monkeypatch):
    p = service_files.write("talk", True, home=tmp_path)
    before = p.read_bytes()

    def boom(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(service_files.os, "replace", boom)
    with pytest.raises(OSError):
        service_files.write("talk", False, home=tmp_path)
    assert p.read_bytes() == before
    assert [x.name for x in p.parent.iterdir()] == ["talk.json"]


def test_save_says_one_line_and_never_raises(tmp_path, capsys):
    (tmp_path / ".sessionkit/voice").mkdir(parents=True)
    (tmp_path / ".sessionkit/voice/services").write_text("a file where the folder goes")
    assert service_files.save("talk", True, home=tmp_path, verb="ears services install") is None
    err = capsys.readouterr().err
    path = service_files.path_for("talk", tmp_path)
    assert err.splitlines() == [f"ears services install: service file {path} not written: "
                                "File exists; the job stands"]
    assert "WARNING" not in err


def test_the_exit_code_collides_with_none_the_verb_has():
    # 0 done, 1 Python's own, 2 usage/refused/no launchd/not loaded, 130 an uncaught Ctrl-C
    assert service_files.EXIT_UNWRITTEN == 3
    assert service_files.EXIT_UNWRITTEN not in (0, 1, 2, 130)


# ---- install and uninstall, each --only word --------------------------------------------------

def _services(home: Path) -> Path:
    return ears_home.services_dir(home)


def _read(home: Path, job: str) -> dict:
    return json.loads(service_files.path_for(job, home).read_text())


def _files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()} if root.exists() else set()


@pytest.mark.parametrize("word", services.ALL)
def test_install_then_uninstall_each_word_flips_its_file_and_keeps_it(tk, word, capsys):
    home = tk["home"]
    if word == "mail":
        _store(home)
    settings_before = _files(home / ears_home.SETTINGS)
    assert services.install([word], agent="dave") == 0, capsys.readouterr()
    jobs = services.jobs([word])
    want = {f"{service_files.name_of(j)}.json" for j in jobs}
    assert _files(_services(home)) == want                       # one per job, no .tmp, nothing else
    for j in jobs:
        rec = _read(home, j)
        assert errors(rec) == [] and rec["enabled"] is True, rec
        assert all(k in rec for k in REQUIRED)
    assert services.uninstall([word]) == 0
    assert _files(_services(home)) == want                       # kept
    for j in jobs:
        rec = _read(home, j)
        assert errors(rec) == [] and rec["enabled"] is False, rec
    settings_after = _files(home / ears_home.SETTINGS)
    assert not {f for f in settings_after - settings_before if f in want}   # nothing in settings/


def test_a_failed_bootstrap_writes_no_file(tk, monkeypatch, capsys):
    real = services._launchctl

    def launchctl(*a):
        if a[0] == "bootstrap":
            return subprocess.CompletedProcess(a, 5, "", "Input/output error")
        return real(*a)
    monkeypatch.setattr(services, "_launchctl", launchctl)
    assert services.install(["talk"]) == 2
    assert not _services(tk["home"]).exists()


def test_a_failed_bootstrap_keeps_a_file_already_there(tk, monkeypatch, capsys):
    service_files.write("talk", False)
    real = services._launchctl
    monkeypatch.setattr(services, "_launchctl", lambda *a: subprocess.CompletedProcess(a, 5, "", "x")
                        if a[0] == "bootstrap" else real(*a))
    services.install(["talk"])
    assert _read(tk["home"], "talk")["enabled"] is False          # never claimed loaded


def test_a_dry_run_install_writes_no_file(tk, capsys):
    _store(tk["home"])
    services.install(list(services.ALL), agent="dave", dry_run=True)
    assert not _services(tk["home"]).exists()


def test_uninstall_makes_the_file_when_absent_and_its_dry_run_writes_none(tk, capsys):
    assert services.uninstall(["mouth"], dry_run=True) == 0
    out = capsys.readouterr().out
    path = service_files.path_for("mouth")
    assert f"mouth  would write {path} (enabled: false)" in out
    assert not path.exists()
    assert services.uninstall(["mouth"]) == 0
    assert _read(tk["home"], "mouth")["enabled"] is False


def test_uninstall_keeper_writes_no_file_for_the_old_agents(tk, capsys):
    services.uninstall(["keeper"])
    assert _files(_services(tk["home"])) == {"ears-keeper.json"}


def test_a_write_that_fails_exits_3_install_and_uninstall_alike(tk, capsys):
    folder = _services(tk["home"])
    folder.parent.mkdir(parents=True, exist_ok=True)
    folder.write_text("in the way")
    assert services.install(["talk"]) == service_files.EXIT_UNWRITTEN
    assert services.uninstall(["talk"]) == service_files.EXIT_UNWRITTEN
    err = capsys.readouterr().err
    assert err.count(" not written: ") == 2 and "WARNING" not in err


def test_no_service_file_lands_outside_the_services_folder(tk, capsys):
    _store(tk["home"])
    assert services.install(list(services.ALL), agent="dave") == 0
    services.uninstall(list(services.ALL))
    home = tk["home"]
    named = {f"{service_files.name_of(j)}.json" for j in _all_jobs()}
    stray = [p for p in home.rglob("*.json") if p.name in named and p.parent != _services(home)]
    assert stray == []
    assert not list(home.rglob("*.json.tmp"))
    assert os.listdir(_services(home)) and set(os.listdir(_services(home))) == named


def test_a_record_that_cannot_be_built_exits_3_and_the_next_job_gets_its_file(tk, monkeypatch, capsys):
    monkeypatch.setenv("EARS_STT_PORT", "not-a-port")      # the plist takes it as text; the file cannot
    assert services.install(["stt", "talk"]) == service_files.EXIT_UNWRITTEN
    assert services.uninstall(["stt"]) == service_files.EXIT_UNWRITTEN
    err = capsys.readouterr().err
    assert err.count(" not written: ") == 2 and "ears-stt.json" in err and "WARNING" not in err
    assert _read(tk["home"], "talk")["enabled"] is True                # the next job still got its file
    assert not service_files.path_for("stt").exists()
    assert not list(_services(tk["home"]).glob(".*.tmp"))


# ---- an unwritable services folder: the job stands, the verb exits 3 ---------------------------

@pytest.fixture
def locked(tk):
    """The services folder, made and then read-only: every write in it fails (Permission
    denied). Unlocked again at teardown so the temp tree can be removed."""
    if os.geteuid() == 0:
        pytest.skip("root writes through a read-only folder")
    folder = _services(tk["home"])
    folder.mkdir(parents=True)
    folder.chmod(0o555)
    yield folder
    folder.chmod(0o755)


def _after(calls, label: str) -> list[str]:
    """The bootstraps and bootouts of LABEL, in order (its plist path or its gui/<uid>/ target)."""
    return [a[0] for a in calls if a[0] in ("bootstrap", "bootout") and label in a[-1]]


def test_install_into_an_unwritable_folder_exits_3_and_the_job_stands(tk, locked, capsys):
    label = services._label("talk")
    assert services.install(["talk"]) == service_files.EXIT_UNWRITTEN == 3
    cap = capsys.readouterr()
    path = service_files.path_for("talk")
    assert cap.err.splitlines() == [f"ears services install: service file {path} not written: "
                                    "Permission denied; the job stands"]
    assert f"talk   loaded  {services.AGENTS / f'{label}.plist'}" in cap.out
    assert (services.AGENTS / f"{label}.plist").exists()          # the plist stays...
    acts = _after(tk["calls"]["launchctl"], label)
    assert acts[-1] == "bootstrap", acts                          # ...and nothing booted it out after
    assert os.listdir(locked) == []                               # no file, no temp left behind


def test_uninstall_into_an_unwritable_folder_exits_3_and_the_job_is_still_gone(tk, capsys):
    if os.geteuid() == 0:
        pytest.skip("root writes through a read-only folder")
    label = services._label("talk")
    assert services.install(["talk"]) == 0
    folder = _services(tk["home"])
    before = _read(tk["home"], "talk")
    folder.chmod(0o555)
    try:
        capsys.readouterr()
        assert services.uninstall(["talk"]) == service_files.EXIT_UNWRITTEN
        err = capsys.readouterr().err
    finally:
        folder.chmod(0o755)
    path = service_files.path_for("talk")
    assert err.splitlines() == [f"ears services uninstall: service file {path} not written: "
                                "Permission denied; the job stands"]
    assert _after(tk["calls"]["launchctl"], label)[-1] == "bootout"   # uninstall did its work
    assert not (services.AGENTS / f"{label}.plist").exists()
    assert _read(tk["home"], "talk") == before                         # the old file, untouched


def test_the_cli_exits_3_and_its_install_log_says_so(tk, locked, monkeypatch, capsys):
    monkeypatch.setattr(services, "has_launchd", lambda: True)
    assert services.main(["install", "--only", "talk"]) == 3
    log = (services.LOGS / "install.log").read_text().splitlines()
    assert log[-1].endswith("rc 3"), log[-1]


def test_a_job_that_did_not_load_outranks_an_unwritten_file(tk, locked, monkeypatch, capsys):
    real = services._launchctl
    talk = services._label("talk")
    monkeypatch.setattr(services, "_launchctl", lambda *a: subprocess.CompletedProcess(a, 5, "", "x")
                        if a[0] == "bootstrap" and a[-1].endswith(f"{talk}.plist") else real(*a))
    assert services.install(["stt", "talk"]) == 2                 # stt's file failed too: 2 wins
    assert " not written: " in capsys.readouterr().err


def test_help_names_every_exit_code(capsys):
    with pytest.raises(SystemExit):
        services.main(["--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert ("Exit status: 0 done; 2 a usage error, a refusal, no launchd, a job that did not load, "
            "or a mic NOT HEARD; 3 ") in out
    assert "3 install or uninstall did its launchd work but could not write a service file" in out
