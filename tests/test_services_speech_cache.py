"""EARS-478: the speech job caps MLX's buffer cache before mlx_audio.server starts.

- the job runs ``python -c SPEECH_LAUNCH`` and the server's own flags: 536870912 bytes by
  default, ``MLX_AUDIO_CACHE_LIMIT`` (whole bytes) in its environment overrides, anything
  else warns on stderr and keeps the default (a KeepAlive job never crash-loops on a typo);
- the cap is set before ``mlx_audio.server`` is imported, and the server's argparse sees
  only the job's flags (under -c, sys.argv[0] is '-c');
- the literal ``mlx_audio.server`` stays in the plist's ProgramArguments (speech._servers()
  finds the kit's own job by it) and in the running process's command line (``ps``);
- install copies ``MLX_AUDIO_CACHE_LIMIT`` from its own environment into the plist, so a
  reinstall keeps an override.

The launcher runs for real on this python, against stub ``mlx.core`` and ``mlx_audio.server``
packages in tmp: no mlx, no server, no port."""
import json
import os
import plistlib
import shutil
import subprocess
import sys
import textwrap

import pytest

from ears import services, speech
from test_services_install_checks import fence  # noqa: F401  (a fixture)
from test_services_speech import OURS, sx  # noqa: F401  (a fixture)

DEFAULT = 536870912
PREVIOUS = 130567005798                    # what 0.32.3 reported on a 128 GB Mac (fix-001 notes)

# mlx.core: set_cache_limit records the limit and returns the one before it, as MLX's does
MLX_CORE = textwrap.dedent(f"""\
    limit = {PREVIOUS}
    calls = []
    def set_cache_limit(n):
        global limit
        prev, limit = limit, n
        calls.append(n)
        return prev
    """)
# mlx_audio.server: notes what the cache limit was when it was imported, and main() prints
# what it saw as one JSON line on stdout (or waits on stdin, for the ps test)
SERVER = textwrap.dedent("""\
    import json, sys
    import mlx.core as mx
    at_import = list(mx.calls)
    def main():
        print(json.dumps({"argv": sys.argv[1:], "at_import": at_import, "limit": mx.limit}),
              flush=True)
        if "--wait" in sys.argv:
            sys.stdin.read()
    """)


@pytest.fixture
def stubs(tmp_path):
    root = tmp_path / "stubs"
    for pkg, mod, body in (("mlx", "core", MLX_CORE), ("mlx_audio", "server", SERVER)):
        (root / pkg).mkdir(parents=True)
        (root / pkg / "__init__.py").write_text("")
        (root / pkg / f"{mod}.py").write_text(body)
    return root


def _env(stubs, limit=None):
    env = {"PATH": os.environ["PATH"], "PYTHONPATH": str(stubs)}
    if limit is not None:
        env["MLX_AUDIO_CACHE_LIMIT"] = limit
    return env


def _launch(stubs, tmp_path, limit=None, flags=("--host", "127.0.0.1", "--port", "8890")):
    r = subprocess.run([sys.executable, "-c", services.SPEECH_LAUNCH, *flags], cwd=tmp_path,
                       env=_env(stubs, limit), capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout), r.stderr.splitlines()


# ---- the constant and the argv ---------------------------------------------------------------

def test_the_default_is_512_mib_beside_the_pin():
    assert services.SPEECH_CACHE_LIMIT == DEFAULT == 512 * 1024 * 1024
    assert services.SPEECH_CACHE_ENV == "MLX_AUDIO_CACHE_LIMIT"
    assert str(DEFAULT) in services.SPEECH_LAUNCH


def test_the_launcher_is_one_line_that_names_the_server():
    """One line, so a holder's `ps` command is one line; it names mlx_audio.server, which is
    what speech.SERVER matches."""
    assert "\n" not in services.SPEECH_LAUNCH
    assert speech.SERVER == "mlx_audio.server" and speech.SERVER in services.SPEECH_LAUNCH
    compile(services.SPEECH_LAUNCH, "-c", "exec")


def test_the_job_runs_the_launcher_with_the_servers_flags(sx):
    args = services.spec("speech")["ProgramArguments"]
    assert args == [os.environ["EARS_SPEECH_PY"], "-c", services.SPEECH_LAUNCH,
                    "--host", "127.0.0.1", "--port", "8890",
                    "--log-dir", str(services.LOGS / "mlx-audio")]
    assert speech.SERVER in " ".join(args)


def test_servers_still_finds_the_kits_job_on_its_port_only(sx, tmp_path):
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / f"{OURS}.plist").write_bytes(plistlib.dumps(services.spec("speech")))
    assert [lab for lab, _ in speech._servers(agents, speech.PORT)] == [OURS]
    assert list(speech._servers(agents, 8891)) == []     # its --port is still read


# ---- the launcher, run: the cap, then the server ---------------------------------------------

def test_unset_applies_the_default_before_the_server_is_imported(stubs, tmp_path):
    got, err = _launch(stubs, tmp_path)
    assert got["at_import"] == [DEFAULT] and got["limit"] == DEFAULT
    assert err == [f"[ears] mx.set_cache_limit({DEFAULT}) (previous {PREVIOUS})"]


def test_the_servers_argparse_sees_only_the_jobs_flags(stubs, tmp_path):
    flags = ["--host", "127.0.0.1", "--port", "8890", "--log-dir", "/x/logs"]
    got, _ = _launch(stubs, tmp_path, flags=flags)
    assert got["argv"] == flags                          # sys.argv[1:], no '-c', no code


@pytest.mark.parametrize("value, want", [("2147483648", 2147483648), (" 2147483648\n", 2147483648),
                                         ("0", 0), (str(2**64 - 1), 2**64 - 1)])
def test_mlx_audio_cache_limit_overrides(stubs, tmp_path, value, want):
    got, err = _launch(stubs, tmp_path, limit=value)
    assert got["at_import"] == [want] and got["limit"] == want
    assert err == [f"[ears] mx.set_cache_limit({want}) (previous {PREVIOUS})"]


@pytest.mark.parametrize("value", ["abc", "-5", "1.5", "1e9", "512M", "0x20000000", "١٢٣",
                                   str(2**64), "9" * 23, "1" * 5000])
def test_a_bad_value_warns_and_keeps_the_default(stubs, tmp_path, value):
    """Never an exit: launchd's KeepAlive would restart it every 10 s, for ever."""
    got, err = _launch(stubs, tmp_path, limit=value)
    assert got["limit"] == DEFAULT
    assert err == [f"[ears] MLX_AUDIO_CACHE_LIMIT={value!r} is not a whole number of bytes under 2**64: "
                   f"using the default {DEFAULT}",
                   f"[ears] mx.set_cache_limit({DEFAULT}) (previous {PREVIOUS})"]


@pytest.mark.parametrize("value", ["", "  "])
def test_an_empty_value_is_unset_and_says_nothing_more(stubs, tmp_path, value):
    got, err = _launch(stubs, tmp_path, limit=value)
    assert got["limit"] == DEFAULT and len(err) == 1


@pytest.mark.skipif(not shutil.which("ps"), reason="no ps on this host")
def test_the_running_processs_command_line_names_the_server(stubs, tmp_path):
    """What speech.port_holder reads: `ps -o command= -p PID`."""
    p = subprocess.Popen([sys.executable, "-c", services.SPEECH_LAUNCH, "--port", "8890", "--wait"],
                         cwd=tmp_path, env=_env(stubs), stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        p.stdout.readline()                              # main() is running
        ps = subprocess.run(["ps", "-o", "command=", "-p", str(p.pid)],
                            capture_output=True, text=True, timeout=10)
        assert ps.returncode == 0
        assert speech.SERVER in ps.stdout and "--port 8890" in ps.stdout
        assert len(ps.stdout.strip().splitlines()) == 1
    finally:
        p.communicate("", timeout=30)


# ---- install: an override is written into the plist --------------------------------------------

def test_install_carries_the_override_into_the_plist(sx, monkeypatch):
    monkeypatch.setenv("MLX_AUDIO_CACHE_LIMIT", "2147483648")
    assert services.spec("speech")["EnvironmentVariables"]["MLX_AUDIO_CACHE_LIMIT"] == "2147483648"
    assert "MLX_AUDIO_CACHE_LIMIT" not in services.spec("mouth")["EnvironmentVariables"]


@pytest.mark.parametrize("value", [None, "", "   "])
def test_no_override_writes_none(sx, monkeypatch, value):
    if value is None:
        monkeypatch.delenv("MLX_AUDIO_CACHE_LIMIT", raising=False)
    else:
        monkeypatch.setenv("MLX_AUDIO_CACHE_LIMIT", value)
    assert "MLX_AUDIO_CACHE_LIMIT" not in services.spec("speech")["EnvironmentVariables"]


def test_a_bad_override_is_written_as_given_for_the_server_to_judge(sx, monkeypatch):
    """Install does not refuse it: the server warns in its log and keeps the default."""
    monkeypatch.setenv("MLX_AUDIO_CACHE_LIMIT", " 512M ")
    assert services.spec("speech")["EnvironmentVariables"]["MLX_AUDIO_CACHE_LIMIT"] == "512M"


@pytest.mark.parametrize("value", ["\x01", "5\x1b", "1\x0b2", "\udcff\udcfe", "\x7f"])
def test_an_override_no_plist_can_hold_is_left_out(sx, monkeypatch, value):
    """plistlib.dumps raises on a control character or a lone surrogate (a byte not UTF-8 in
    the shell's environment): written as given, install would stop half-way, a traceback."""
    monkeypatch.setenv("MLX_AUDIO_CACHE_LIMIT", value)
    d = services.spec("speech")
    assert "MLX_AUDIO_CACHE_LIMIT" not in d["EnvironmentVariables"]
    plistlib.dumps(d)


def test_install_survives_an_override_no_plist_can_hold(sx, monkeypatch, capsys):
    from test_services_install_checks import _store
    _store(sx["home"])
    monkeypatch.setenv("MLX_AUDIO_CACHE_LIMIT", "\x01")
    assert services.main(["install", "--agent", "dave", "--only", "speech"]) == 0
    d = plistlib.loads((services.AGENTS / f"{OURS}.plist").read_bytes())
    assert "MLX_AUDIO_CACHE_LIMIT" not in d["EnvironmentVariables"]


def test_install_writes_the_override_into_the_plist_it_loads(sx, monkeypatch, capsys):
    from test_services_install_checks import _store
    _store(sx["home"])
    monkeypatch.setenv("MLX_AUDIO_CACHE_LIMIT", "2147483648")
    assert services.main(["install", "--agent", "dave", "--only", "speech"]) == 0
    d = plistlib.loads((services.AGENTS / f"{OURS}.plist").read_bytes())
    assert d["EnvironmentVariables"]["MLX_AUDIO_CACHE_LIMIT"] == "2147483648"
    assert d["ProgramArguments"][1:3] == ["-c", services.SPEECH_LAUNCH]
