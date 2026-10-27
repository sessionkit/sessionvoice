"""sessionvoice-web: Talk's room descriptor, for the kit's web front door.

The front door finds the room two ways and hands both to one validator: the
`sessionkit.web` entry point (`talk = "talk.web:room"`, loaded and called in its own
process) and `sessionvoice-web web-room` (run, its JSON read). So:

- the descriptor passes a COPY of the front door's `validate_room` (the package boundary
  holds: nothing here imports the front door's package);
- the entry point, read from installed metadata, and the installed script give the same dict;
- the command is `[sys.executable, "-I", "-m", "talk", "serve", "--kit", "--unix", "{socket}"]`,
  with the interpreter read when room() runs, absolute, never `talk` on PATH;
- `web-room` imports nothing heavy and answers well inside the front door's 2 s;
- run from a directory holding a `talk.py`, the room serves the PACKAGE, because of `-I`,
  and `-I` still imports the environment's own talk.

The children run with `-I` skip the audio fence's sitecustomize (tests/audio_fence.py).
They are `talk serve --kit` and a `-c` that imports talk: neither can reach a player.
"""
import json, os, re, signal, subprocess, sys, time
from importlib import metadata
from pathlib import Path

import pytest

import talk
from talk import server, web
from tests.test_talk_unix import get

COMMAND_TAIL = ["-I", "-m", "talk", "serve", "--kit", "--unix", "{socket}"]
SCRIPT = Path(sys.executable).parent / "sessionvoice-web"      # this venv's own bin, never PATH
SOCK = "k.sock"
FRONT = "127.0.0.1:8765"
USER = ("X-Sessionkit-User", "someone")
PAGE = open(os.path.join(server.HERE, "index.html"), "rb").read()
HEAVY = ("ears", "numpy", "mlx", "mlx_audio", "sounddevice", "scipy", "onnxruntime")


# -- 1. the front door's validate_room rules, COPIED ---------------------------------------
# Verbatim from sessiontools src/sessiontools/web/rooms.py @ de422cd
# (blob 8babb7f2eeac0542eb33e8cd8e87a46b1b07c4fb): KEYS, RESERVED, _NAME, SOCKET, _TCP_ARG
# and the body of validate_room. Copied, never imported; the one change is that it returns
# the validated dict instead of a Room. CATALOG_NAME and CATALOG_COMMAND are the catalog's
# Talk entry there (rooms.py:87): transport 2 runs that command and refuses another name.

CATALOG_NAME, CATALOG_COMMAND = "talk", "sessionvoice-web"
SOCKET = "{socket}"
RESERVED = ("settings", "login", "static")
_NAME = re.compile(r"[a-z][a-z0-9-]{0,31}")
KEYS = ("name", "title", "package", "command")
_TCP_ARG = re.compile(r"--(host|port|bind|listen|address|addr)(=.*)?")


class RoomRefused(ValueError):
    pass


def validate_room(data):
    if not isinstance(data, dict):
        raise RoomRefused(f"a room descriptor is a JSON object, not {type(data).__name__}")
    extra = sorted(set(data) - set(KEYS))
    if extra:
        raise RoomRefused(f"unknown key(s) {', '.join(map(repr, extra))}: a room is reached "
                          "by its unix socket only")
    name = data.get("name")
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise RoomRefused(f"room name {name!r}: must match [a-z][a-z0-9-]*")
    if name in RESERVED:
        raise RoomRefused(f"room name {name!r} is the front door's own")
    for key in ("title", "package"):
        v = data.get(key)
        if not isinstance(v, str) or not v.strip() or len(v) > 64:
            raise RoomRefused(f"room {name!r}: {key} must be a short string")
    command = data.get("command")
    if (not isinstance(command, list) or not command
            or not all(isinstance(a, str) and a for a in command)):
        raise RoomRefused(f"room {name!r}: command must be a non-empty list of strings")
    if not any(SOCKET in a for a in command):
        raise RoomRefused(f"room {name!r}: command must take {SOCKET}, the unix socket it "
                          "listens on")
    for a in command:
        if _TCP_ARG.fullmatch(a):
            raise RoomRefused(f"room {name!r}: command argument {a!r} asks for a TCP "
                              "listener; a room listens on its unix socket only")
    return {"name": name, "title": data["title"].strip(), "package": data["package"].strip(),
            "command": list(command)}


def test_the_room_passes_the_front_doors_validator():
    desc = web.room()
    assert validate_room(desc) == desc
    assert sorted(desc) == ["command", "name", "package", "title"]
    assert list(desc) == ["name", "title", "package", "command"]          # the one-line order
    assert (desc["name"], desc["title"], desc["package"]) == ("talk", "Talk", "sessionvoice")


@pytest.mark.parametrize("change, why", [
    (lambda d: {**d, "url": "http://127.0.0.1:8780/"}, "unknown key"),
    (lambda d: {**d, "command": d["command"] + ["--port", "8780"]}, "TCP"),
    (lambda d: {**d, "command": d["command"] + ["--host=0.0.0.0"]}, "TCP"),
    (lambda d: {**d, "command": [a for a in d["command"] if a != SOCKET]}, SOCKET),
    (lambda d: {**d, "name": "settings"}, "front door's own"),
    (lambda d: {**d, "name": "Talk"}, "must match"),
    (lambda d: {**d, "command": []}, "non-empty list"),
    (lambda d: {k: v for k, v in d.items() if k != "title"}, "title"),
])
def test_the_copied_validator_bites(change, why):
    """Negative controls: a gutted copy would pass the room and these alike."""
    with pytest.raises(RoomRefused, match=re.escape(why)):
        validate_room(change(web.room()))


# -- 2. the entry point from installed metadata == the installed script's web-room ---------

def run_script(*args, timeout=10):
    return subprocess.run([str(SCRIPT), *args], capture_output=True, text=True, timeout=timeout,
                          stdin=subprocess.DEVNULL)


def test_the_entry_point_and_the_script_give_the_same_room():
    eps = [ep for ep in metadata.entry_points(group="sessionkit.web") if ep.value.startswith("talk.")]
    assert [(ep.name, ep.value) for ep in eps] == [("talk", "talk.web:room")]
    assert [ep.name for ep in metadata.entry_points(group="sessionkit.web")].count("talk") == 1
    assert SCRIPT.is_file(), "the sessionvoice-web console script is not installed in this venv"
    r = run_script("web-room")
    assert r.returncode == 0, r.stderr
    from_script = json.loads(r.stdout)
    assert eps[0].load()() == from_script                 # same venv, so the same interpreter too
    assert validate_room(from_script) == from_script
    scripts = {ep.name: ep.value for ep in metadata.entry_points(group="console_scripts")}
    assert scripts[CATALOG_COMMAND] == "talk.web:main"    # the name transport 2 looks up
    assert from_script["name"] == CATALOG_NAME            # and the name it requires back
    assert SCRIPT.name == CATALOG_COMMAND


# -- 3. command[0] is the interpreter that ran room(), absolute, read at call time ---------

def test_the_command_is_this_interpreter_then_the_ruled_tail():
    cmd = web.room()["command"]
    assert cmd[0] == sys.executable and os.path.isabs(cmd[0])
    assert cmd[1:] == COMMAND_TAIL
    assert "talk" != cmd[0] and os.path.basename(cmd[0]) != "talk"


def test_a_child_names_its_own_interpreter():
    """Run through main() in a child: the command it prints starts with ITS sys.executable."""
    code = "import sys; from talk import web; rc = web.main(['web-room']); print(sys.executable); sys.exit(rc)"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, r.stderr
    line, exe = r.stdout.splitlines()
    cmd = json.loads(line)["command"]
    assert cmd[0] == exe and os.path.isabs(exe)
    assert cmd[1:] == COMMAND_TAIL


def test_sys_executable_is_read_when_room_runs(monkeypatch, tmp_path):
    other = str(tmp_path / "venv" / "bin" / "python")
    monkeypatch.setattr(sys, "executable", other)
    assert web.room()["command"] == [other, *COMMAND_TAIL]


def test_a_venv_python_is_not_resolved_through_its_symlink(monkeypatch, tmp_path):
    """A venv finds itself by its python's own path; the resolved target cannot import talk."""
    (tmp_path / "base").write_text("")
    link = tmp_path / "python"
    link.symlink_to(tmp_path / "base")
    monkeypatch.setattr(sys, "executable", str(link))
    assert web.room()["command"][0] == str(link)


@pytest.mark.parametrize("exe", ["", None, "python3", "bin/python"])
def test_a_relative_or_empty_interpreter_raises(monkeypatch, exe):
    monkeypatch.setattr(sys, "executable", exe)
    with pytest.raises(RuntimeError, match="not an absolute path"):
        web.room()


def test_web_room_exits_1_when_the_interpreter_is_unknown(monkeypatch, capsys):
    monkeypatch.setattr(sys, "executable", "")
    assert web.main(["web-room"]) == 1
    out, err = capsys.readouterr()
    assert out == "" and err.startswith("sessionvoice-web: talk.web.room: sys.executable is not")


# -- 4. a light import, and well inside the front door's 2 s -------------------------------

def test_web_room_imports_nothing_heavy():
    code = ("import json, sys, talk.web; talk.web.main(['web-room']); "
            "print(json.dumps(sorted(sys.modules)))")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, r.stderr
    line, mods = r.stdout.splitlines()
    assert json.loads(line) == web.room()
    loaded = json.loads(mods)
    assert "talk.web" in loaded                                            # the control
    heavy = [m for m in loaded if m.split(".")[0] in HEAVY or m in ("talk.server", "talk.cli")]
    assert heavy == []


def test_the_script_answers_within_the_front_doors_timeout():
    start = time.monotonic()
    r = run_script("web-room", timeout=2.0)
    took = time.monotonic() - start
    assert r.returncode == 0, r.stderr
    assert took < 2.0, f"web-room took {took:.2f} s"


# -- 5. the command line's edges ------------------------------------------------------------

def test_the_output_is_one_line_of_json():
    r = run_script("web-room")
    assert r.returncode == 0 and r.stderr == ""
    assert r.stdout.endswith("\n") and r.stdout.count("\n") == 1
    assert json.loads(r.stdout) == validate_room(json.loads(r.stdout))


def test_no_verb_exits_2_with_usage():
    r = run_script()
    assert r.returncode == 2 and r.stdout == ""
    assert r.stderr.startswith("usage: sessionvoice-web") and "web-room" in r.stderr


@pytest.mark.parametrize("args", [["serve"], ["web-room", "--port", "8780"], ["room"]])
def test_anything_else_exits_2(args):
    r = run_script(*args)
    assert r.returncode == 2 and r.stdout == "" and "usage: sessionvoice-web" in r.stderr


def test_in_process_no_verb_returns_2(capsys):
    assert web.main([]) == 2
    assert "a verb is required (web-room)" in capsys.readouterr().err


def test_help_names_the_one_verb():
    r = run_script("--help")
    assert r.returncode == 0 and "web-room" in r.stdout


# -- 6. from a directory holding a talk.py, the room serves the PACKAGE --------------------

PLANT = """import pathlib, sys
pathlib.Path("planted-ran").write_text("the planted talk.py ran\\n")
sys.exit(3)
"""


@pytest.fixture
def planted(tmp_path, monkeypatch):
    """cwd = a fresh 0700 dir holding a talk.py that marks the dir and exits 3, as the front
    door's working directory might; a RELATIVE socket there (a socket path holds 104 bytes).
    The child's env is fenced: HOME and SESSIONVOICE_HOME in tmp, a PATH of the venv's bin and
    /usr/bin:/bin (no `session` command)."""
    d = tmp_path / "s"
    d.mkdir(mode=0o700)
    (d / "talk.py").write_text(PLANT)
    monkeypatch.chdir(d)
    env = {"HOME": str(tmp_path / "home"), "SESSIONVOICE_HOME": str(tmp_path / "svh"),
           "PATH": os.pathsep.join([os.path.dirname(sys.executable), "/usr/bin", "/bin"])}
    return d, env


def start(argv, cwd, env, log):
    return subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=log,
                            stderr=subprocess.STDOUT, start_new_session=True)


def stop(proc):
    try:
        os.killpg(proc.pid, signal.SIGTERM)                # its own group only (start_new_session)
    except ProcessLookupError:
        pass
    proc.wait(timeout=10)


def test_the_room_command_serves_the_package_not_a_planted_talk_py(planted, tmp_path):
    d, env = planted
    argv = [a.replace("{socket}", SOCK) for a in web.room()["command"]]
    with open(tmp_path / "room.log", "wb") as log:
        proc = start(argv, d, env, log)
        try:
            deadline = time.monotonic() + 20
            while not os.path.exists(SOCK):
                assert proc.poll() is None, (tmp_path / "room.log").read_text()
                assert time.monotonic() < deadline, "the socket never appeared"
                time.sleep(0.05)
            status, _, body = get(SOCK, "/", host=FRONT, headers=[USER])
            assert status == 200 and body == PAGE
            assert not (d / "planted-ran").exists()
        finally:
            stop(proc)
    assert "kit mode" in (tmp_path / "room.log").read_text()               # the package's own lines


def test_without_dash_i_the_plant_would_have_run(planted, tmp_path):
    """The control: the same argv with -I removed, the same cwd, runs the plant. So it is -I
    that keeps the room the package's."""
    d, env = planted
    argv = [a.replace("{socket}", SOCK) for a in web.room()["command"] if a != "-I"]
    assert argv[1:3] == ["-m", "talk"]
    with open(tmp_path / "room.log", "wb") as log:
        proc = start(argv, d, env, log)
        try:
            assert proc.wait(timeout=20) == 3
        finally:
            stop(proc)
    assert (d / "planted-ran").read_text() == "the planted talk.py ran\n"
    assert not os.path.exists(SOCK)


# -- 7. -I keeps the environment's own talk, and drops the cwd, user site and PYTHON* ------

PROBE = """import json, os, sys, talk
print(json.dumps({"talk": talk.__file__, "isolated": sys.flags.isolated,
                  "no_user_site": sys.flags.no_user_site, "ignore_environment": sys.flags.ignore_environment,
                  "cwd": os.path.realpath(os.getcwd()), "path": sys.path, "version": sys.version.split()[0]}))
"""


def test_dash_i_keeps_this_environments_talk(planted, tmp_path):
    d, env = planted
    shadow = tmp_path / "pp"
    (shadow / "talk").mkdir(parents=True)
    (shadow / "talk" / "__init__.py").write_text("raise SystemExit('the PYTHONPATH talk ran')\n")
    r = subprocess.run([sys.executable, "-I", "-c", PROBE], cwd=d, capture_output=True, text=True,
                       timeout=10, env={**env, "PYTHONPATH": str(shadow)})
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)
    assert got["talk"] == talk.__file__                   # the package this venv imports, editable or not
    assert (got["isolated"], got["no_user_site"], got["ignore_environment"]) == (1, 1, 1)
    paths = [os.path.realpath(p) if p else "" for p in got["path"]]
    assert "" not in paths and got["cwd"] not in paths
    assert os.path.realpath(shadow) not in paths
    print(f"\n-I measured on Python {got['version']}: talk = {got['talk']}; isolated/no_user_site/"
          f"ignore_environment = 1/1/1; cwd and PYTHONPATH absent from sys.path")
