"""A STRANDED voice settings file: absent from the settings folder, its 0.1.0 copy still there.

``ears.home.stranded`` is a stat, never a read. Readers keep their defaults and say ONE line
naming both paths, once per process, on stderr; writers write nothing. Every test runs
under a fake HOME in tmp_path, and these are the fixtures of the old path.
"""
import builtins
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ears import home, recording, services, wake
from ears.cli import main as ears_main

ROOT = Path(__file__).resolve().parents[1]

#: Which 0.1.0 folder each file sat in, as measured on the last 0.1.0-shaped master.
FOLDER_AT_0_1_0 = {
    "ears.json": "ears", "wake.json": "ears", "ears-sounds.json": "ears", "stt.key": "ears",
    "mouth.json": "mouth", "mouth-sounds.json": "mouth", "tts.key": "mouth",
}


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    for n in ("EARS_CONF", "EARS_WAKE", "MOUTH_SETTINGS", "EARS_RECORD", "EARS_RECORD_DAYS",
              "EARS_STT_URL", "EARS_STT_MODEL"):
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setattr(home, "_said", set())   # a fresh "process" for the once-per-process rule
    return h


def strand(h, name, body='{"record": true}'):
    """Leave NAME's 0.1.0 copy under fake HOME H, and nothing in the settings folder."""
    old = home.legacy_path(name, h)
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_text(body)
    return home.settings_dir(h) / name, old


def put_new(h, name, body):
    new = home.settings_dir(h) / name
    new.parent.mkdir(parents=True, exist_ok=True)
    new.write_text(body)
    return new


@pytest.fixture
def no_old_open(monkeypatch, fake_home):
    """Once armed (``.arm()``), fail any open of a path under the 0.1.0 folders: their
    content is never read. The fixture's own files are written before it is armed."""
    roots = [str(fake_home / rel) for rel in home.LEGACY_SETTINGS.values()]

    class Opened(list):
        armed = False

        def arm(self):
            self.armed = True

    opened = Opened()

    def guard(path):
        if not opened.armed:
            return
        p = os.fspath(path) if isinstance(path, (str, bytes, os.PathLike)) else ""
        p = p.decode() if isinstance(p, bytes) else p
        if any(os.path.abspath(p).startswith(r) for r in roots):
            opened.append(p)
            raise AssertionError(f"opened a 0.1.0 settings file: {p}")

    real_open, real_io_open, real_os_open = builtins.open, io.open, os.open
    monkeypatch.setattr(builtins, "open", lambda f, *a, **k: (guard(f), real_open(f, *a, **k))[1])
    monkeypatch.setattr(io, "open", lambda f, *a, **k: (guard(f), real_io_open(f, *a, **k))[1])
    monkeypatch.setattr(os, "open", lambda f, *a, **k: (guard(f), real_os_open(f, *a, **k))[1])
    return opened


# --- the one constant -------------------------------------------------------------------


def test_the_legacy_constant_is_spelled_plainly_and_maps_every_file():
    assert home.LEGACY_SETTINGS == {"ears": ".settings/ears", "mouth": ".settings/mouth"}
    assert home.LEGACY_FOLDER == FOLDER_AT_0_1_0


def test_legacy_path_is_the_0_1_0_folder_by_file(fake_home):
    for name, folder in FOLDER_AT_0_1_0.items():
        assert home.legacy_path(name) == fake_home / ".settings" / folder / name


# --- stranded: the stat ----------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(FOLDER_AT_0_1_0))
def test_old_present_new_absent_is_stranded(fake_home, name):
    new, old = strand(fake_home, name)
    assert home.stranded(name) == (new, old)
    line = home.stranded_line(name)
    assert str(new) in line and str(old) in line


@pytest.mark.parametrize("name", sorted(FOLDER_AT_0_1_0))
def test_both_present_is_not_stranded(fake_home, name):
    strand(fake_home, name)
    put_new(fake_home, name, "{}")
    assert home.stranded(name) is None and home.stranded_line(name) is None


@pytest.mark.parametrize("name", sorted(FOLDER_AT_0_1_0))
def test_neither_present_is_not_stranded(fake_home, name):
    assert home.stranded(name) is None


def test_a_dangling_link_at_the_new_path_is_something_there(fake_home):
    """lexists, as the model: a link to nothing is the host step's own work, not stranded."""
    new, _ = strand(fake_home, "ears.json")
    new.parent.mkdir(parents=True)
    new.symlink_to(fake_home / "nowhere.json")
    assert home.stranded("ears.json") is None


# --- the line: once per process, stderr, both paths -------------------------------------


def test_the_line_names_both_paths_once_per_process(fake_home, capsys):
    new, old = strand(fake_home, "ears.json")
    said = home.warn_stranded("ears")
    err = capsys.readouterr()
    assert err.out == ""
    assert err.err.splitlines() == said == [f"ears: {home.STRANDED.format(new=new, old=old)}"]
    assert str(new) in said[0] and str(old) in said[0]
    assert home.warn_stranded("ears") == [] and home.warn_stranded("mouth") == []
    assert capsys.readouterr().err == ""


def test_one_line_per_stranded_file(fake_home, capsys):
    strand(fake_home, "ears.json")
    strand(fake_home, "tts.key", "sk-0")
    put_new(fake_home, "wake.json", "{}")
    strand(fake_home, "wake.json")                 # both: silent for this one
    lines = home.warn_stranded("mouth")
    assert capsys.readouterr().err.splitlines() == lines
    assert [ln.split(" is missing")[0] for ln in lines] == [
        f"mouth: {home.settings_dir() / 'ears.json'}", f"mouth: {home.settings_dir() / 'tts.key'}"]


def test_both_present_and_neither_are_silent(fake_home, capsys):
    assert home.warn_stranded("ears") == []            # neither
    for name in FOLDER_AT_0_1_0:
        strand(fake_home, name)
        put_new(fake_home, name, "{}")
    assert home.warn_stranded("ears") == []            # both
    assert capsys.readouterr().err == ""
    assert not home.settings_dir().joinpath(".ears.schema.json").exists()


def test_warning_writes_nothing(fake_home):
    strand(fake_home, "mouth.json")
    home.warn_stranded("mouth")
    assert not (fake_home / ".sessionkit").exists()


# --- readers: their defaults, never the old content -------------------------------------


def test_readers_keep_their_defaults_and_never_open_the_old_file(fake_home, no_old_open):
    from mouth import voices
    strand(fake_home, "ears.json", '{"record": true, "record_days": 3}')
    strand(fake_home, "mouth.json", '{"voice": "bm_george"}')
    strand(fake_home, "wake.json", '{"seats": {"dave": {}}}')
    no_old_open.arm()
    assert len(home.warn_stranded("ears")) == 3
    assert recording.read_file().data is None                 # no file: the defaults
    assert recording.resolve_all(None, env={}).record.on is False
    assert voices.settings().get("voice") != "bm_george"
    assert wake.seats() == {}
    assert no_old_open == []


def test_both_present_reads_the_new_file(fake_home, capsys):
    strand(fake_home, "ears.json", '{"record": true}')
    put_new(fake_home, "ears.json", '{"record": false, "record_days": 9}')
    assert recording.read_file().data == {"record": False, "record_days": 9}
    assert home.warn_stranded("ears") == [] and capsys.readouterr().err == ""


# --- writers: nothing written -----------------------------------------------------------


def test_ears_recording_on_writes_nothing(fake_home, capsys):
    new, old = strand(fake_home, "ears.json", '{"record": false}')
    assert recording.main(["on"]) == 1
    err = capsys.readouterr().err.splitlines()
    assert err[0] == f"ears recording: {home.STRANDED.format(new=new, old=old)}"
    assert err[1:] == [f"ears recording on: {new} is stranded (the line above): nothing written"]
    assert not new.exists() and not new.parent.exists()
    assert old.read_text() == '{"record": false}'


def test_write_record_refuses_only_the_stranded_default(fake_home, tmp_path, monkeypatch):
    strand(fake_home, "ears.json")
    with pytest.raises(recording.WriteRefused, match="nothing written"):
        recording.write_record(True)
    other = tmp_path / "elsewhere.json"                # a file the variable names: written
    monkeypatch.setenv("EARS_CONF", str(other))
    assert recording.write_record(True) == str(other)
    assert json.loads(other.read_text())["record"] is True


def test_ears_wake_add_writes_nothing(fake_home, capsys):
    new, old = strand(fake_home, "wake.json", '{"seats": {}}')
    assert wake.main(["add", "dave"]) == 1
    err = capsys.readouterr().err
    assert str(new) in err and str(old) in err and "nothing written" in err
    assert not new.exists() and not new.parent.exists()
    assert old.read_text() == '{"seats": {}}'


def test_install_wake_check_writes_nothing(fake_home):
    strand(fake_home, "wake.json")
    (line,) = services.wake_checks("dave")
    assert line.startswith("WARNING: wake seat NOT added:") and "nothing written" in line
    assert not home.settings_dir().exists()


def test_seed_stt_writes_nothing_and_probes_nothing(fake_home, monkeypatch):
    new, _ = strand(fake_home, "ears.json")
    monkeypatch.setattr(services, "probe_stt", lambda *a, **k: pytest.fail("probed"))
    said = services.seed_stt()
    assert said.startswith("final NOT set:") and str(new) in said
    assert not new.parent.exists()


def test_install_schema_skips_only_the_stranded_files_schema(fake_home):
    strand(fake_home, "ears.json")
    said = services.install_schema()
    d = home.settings_dir()
    assert not (d / ".ears.schema.json").exists()
    assert (d / ".ears-sounds.schema.json").exists() and (d / ".mouth.schema.json").exists()
    assert "NOT copied (stranded, nothing written): ears.schema.json" in said
    assert not (d / "ears.json").exists()


def test_install_schema_with_every_schema_stranded_makes_no_folder(fake_home):
    for name in ("ears.json", "ears-sounds.json", "mouth.json"):
        strand(fake_home, name)
    assert "schema: none in" in services.install_schema()
    assert not home.settings_dir().exists()


# --- the commands: ears, ears services, mouth, talk -------------------------------------


def test_ears_cli_says_it_once_on_stderr(fake_home, capsys):
    new, old = strand(fake_home, "stt.key", "sk-0")
    assert ears_main(["recording", "status", "--json"]) == 0
    out, err = capsys.readouterr()
    assert err.splitlines() == [f"ears: {home.STRANDED.format(new=new, old=old)}"]
    assert "is missing" not in out
    assert ears_main(["recording", "status", "--json"]) == 0
    assert capsys.readouterr().err == ""


def test_once_per_process_in_a_real_process(fake_home):
    """Two runs in one process: one line. A second process: the line again."""
    strand(fake_home, "ears.json")
    code = ("from ears.cli import main; main(['recording', 'status', '--json']); "
            "main(['recording', 'status', '--json'])")
    env = {**os.environ, "HOME": str(fake_home)}
    for _ in range(2):
        r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                           text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        assert [ln for ln in r.stderr.splitlines() if "is missing" in ln] == [
            f"ears: {home.STRANDED.format(new=home.settings_dir(fake_home) / 'ears.json', old=home.legacy_path('ears.json', fake_home))}"]


def test_ears_services_says_it(fake_home, capsys, monkeypatch):
    new, old = strand(fake_home, "mouth-sounds.json")
    monkeypatch.setattr(services, "status", lambda: 0)
    assert services.main(["status"]) == 0
    assert capsys.readouterr().err.splitlines() == [
        f"ears services: {home.STRANDED.format(new=new, old=old)}"]


@pytest.mark.parametrize("prog, run", [
    ("ears", lambda: ears_main(["--help"])),
    ("ears services", lambda: services.main(["--help"])),
    ("mouth", lambda: __import__("mouth.__main__", fromlist=["main"]).main(["--help"])),
    ("talk", lambda: __import__("talk.cli", fromlist=["main"]).main(["--help"])),
])
def test_each_command_says_it_and_its_help_says_what_it_means(fake_home, capsys, prog, run):
    new, old = strand(fake_home, "ears-sounds.json")
    with pytest.raises(SystemExit):
        run()
    out, err = capsys.readouterr()
    assert err.splitlines() == [f"{prog}: {home.STRANDED.format(new=new, old=old)}"]
    assert " ".join(home.STRANDED_HELP.split()) in " ".join(out.split())


def test_help_says_what_it_means_without_the_old_path():
    assert ".settings" not in home.STRANDED_HELP and "0.1.0" in home.STRANDED_HELP


# --- the daemons' stdlib copies carry none of it ----------------------------------------


@pytest.mark.parametrize("path", ["bin/render-plist", "keeper/ears-mail.py", "keeper/ears-sounds.py",
                                  "keeper/mouth-sounds.py", "keeper/ears-keeper.sh"])
def test_the_stdlib_copies_neither_carry_nor_warn(path):
    text = (ROOT / path).read_text()
    assert "LEGACY" not in text and "stranded" not in text.lower()
    assert ".settings/ears" not in text and ".settings/mouth" not in text
