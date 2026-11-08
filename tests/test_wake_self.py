"""EARS-504 R4: `ears wake add --self` registers the seat a launch names, with no hand edit.

An agent file lists `ears wake add --self` under `start: context: system-prompt:`, and
every `session start NAME` runs it as a shell line (sessiontools runs a generator with
`sh -c`, its environment the caller's plus CLAUDE_CODE_AGENT=NAME). The key is resolved
in the pager's order; it is REFUSED when the address names a seat other than the
launched agent (a caller's EMAIL is inherited, F11). The write holds a lock
(.wake.json.lock) and writes nothing when nothing changed.
Design: the EARS-504 task's design.md, section R4 and the test plan (SC4).

Every test here sets the three identity variables itself: `make test` runs inside seats
whose plus EMAIL is on this host, which the check refuses (F15).
"""
import json, os, socket, subprocess, sys, threading
from pathlib import Path

import pytest

from ears import wake
from ears.cli import main as ears_main
from tests.test_wake_rules_live import Keeper, turn

REPO = Path(__file__).resolve().parent.parent
SHIPPED_DIR = REPO / "keeper" / "defaults"
IDENTITY = ("EMAIL", "TM_AGENT_ADDRESS", "CLAUDE_CODE_AGENT") + wake.HOST_VARS
LOCK = ".wake.json.lock"


def host():
    """This host's name, read as the code reads it (never a literal)."""
    return socket.gethostname().split(".")[0].strip().lower()


def seat_env(tmp_path, **ids):
    """os.environ with every identity variable removed, then IDS set; HOME in tmp."""
    env = {k: v for k, v in os.environ.items() if k not in IDENTITY}
    env.update(HOME=str(tmp_path / "home"), EARS_WAKE=str(tmp_path / "wake.json"),
               PATH=os.pathsep.join([str(Path(sys.executable).parent), env.get("PATH", "")]))
    env.update(ids)
    return env


def generator(tmp_path, **ids):
    """`ears wake add --self` run exactly as sessiontools runs a generator: sh -c."""
    return subprocess.run(["sh", "-c", "ears wake add --self"], env=seat_env(tmp_path, **ids),
                          capture_output=True, text=True, timeout=60)


@pytest.fixture
def ids(monkeypatch, tmp_path):
    """In-process: no identity variable, EARS_WAKE in tmp; returns a setter."""
    for k in IDENTITY:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("EARS_WAKE", str(tmp_path / "wake.json"))

    def set_(**kv):
        for k, v in kv.items():
            monkeypatch.setenv(k, v)
    return set_


def seats_in(tmp_path):
    f = tmp_path / "wake.json"
    return sorted(json.loads(f.read_text())["seats"]) if f.exists() else None


# -- SC4: the generator line, end to end -------------------------------------------

def test_the_generator_line_writes_the_launched_agent(tmp_path):
    r = generator(tmp_path, CLAUDE_CODE_AGENT="mini-me")
    assert r.returncode == 0, r.stderr
    assert seats_in(tmp_path) == ["mini-me"]
    assert r.stdout.splitlines() == [
        f'ears wake: this seat is a wake seat, mini-me: "hey Mini Me" calls it '
        f'(names mini\\-me; {tmp_path / "wake.json"})']


def test_a_running_keeper_then_calls_the_seat(tmp_path):
    rules = tmp_path / "wake.json"
    k = Keeper(tmp_path, rules)
    try:
        k.stderr_line("names known: )")              # started, with no seats
        r = generator(tmp_path, CLAUDE_CODE_AGENT="mini-me")
        assert r.returncode == 0, r.stderr
        k.stderr_line("wake rules reloaded")
        k.append(turn(1, "hey mini me, what time is it", 10))
        m = k.mail(10)
        assert m["rule"] == "mini-me@host1=call" and m["addressed"] == "mini-me@host1"
    finally:
        errs = k.stop()
    assert "Traceback" not in "\n".join(errs)


def test_a_second_launch_writes_nothing(tmp_path):
    """The no-op: the same add again keeps the file's inode and mtime (no reload)."""
    assert generator(tmp_path, CLAUDE_CODE_AGENT="mini-me").returncode == 0
    st = os.stat(tmp_path / "wake.json")
    r = generator(tmp_path, CLAUDE_CODE_AGENT="mini-me")
    assert r.returncode == 0 and "mini-me" in r.stdout
    st2 = os.stat(tmp_path / "wake.json")
    assert (st2.st_mtime_ns, st2.st_ino) == (st.st_mtime_ns, st.st_ino)


# -- the key: the pager's order, folded -------------------------------------------

def test_a_plus_email_on_this_host_beats_tm_agent_address(ids):
    ids(EMAIL=f"sessionmail+engineer@{host()}.example.net", TM_AGENT_ADDRESS="other+seat")
    assert wake.self_key() == ("sessionmail+engineer", "EMAIL")


def test_a_plus_email_with_no_domain_is_this_host(ids):
    ids(EMAIL="sessionmail+engineer")
    assert wake.self_key() == ("sessionmail+engineer", "EMAIL")


def test_a_plus_tm_agent_address_on_this_host_beats_a_bare_email(ids):
    ids(EMAIL=f"alice@{host()}", TM_AGENT_ADDRESS=f"sessionmail+engineer@{host()}")
    assert wake.self_key() == ("sessionmail+engineer", "TM_AGENT_ADDRESS")


def test_a_plus_email_on_another_host_declares_nothing(ids):
    ids(EMAIL="me+git@example.com", TM_AGENT_ADDRESS="mini-me")
    assert wake.self_key() == ("mini-me", "TM_AGENT_ADDRESS")


def test_tm_agent_address_beats_claude_code_agent_when_they_agree(ids):
    ids(TM_AGENT_ADDRESS="mini-me@elsewhere", CLAUDE_CODE_AGENT="mini-me")
    assert wake.self_key() == ("mini-me", "TM_AGENT_ADDRESS")


def test_claude_code_agent_last_and_folded(ids):
    ids(CLAUDE_CODE_AGENT=" Mini Me ")
    assert wake.self_key() == ("mini-me", "CLAUDE_CODE_AGENT")


def test_sessionmails_host_variable_names_this_host(ids):
    """A host named by SESSIONMAIL_HOST is this host too, so its plus EMAIL is read."""
    ids(EMAIL="alice+desk@deskhost", SESSIONMAIL_HOST="DeskHost")
    assert wake.self_key() == ("alice+desk", "EMAIL")


def test_fold_matches_sessionmail():
    assert [wake.fold(v) for v in ("Alice+Desk@H", "mini me", "a/b..c+d", "-x-")] == [
        "alice+desk", "mini-me", "a-b..c+d", "x"]


# -- refusals -----------------------------------------------------------------------

def test_no_seat_in_the_environment_exits_1(tmp_path):
    r = generator(tmp_path)
    assert r.returncode == 1
    assert r.stderr.splitlines() == [
        "ears wake: no seat in this environment (EMAIL, TM_AGENT_ADDRESS, "
        "CLAUDE_CODE_AGENT): give it as ears wake add SEAT"]
    assert sorted(p.name for p in tmp_path.iterdir()) == []


@pytest.mark.parametrize("argv", [["mini-me", "--self"], []], ids=["both", "neither"])
def test_seat_and_self_are_exclusive(ids, tmp_path, argv, capsys):
    ids(CLAUDE_CODE_AGENT="mini-me")
    with pytest.raises(SystemExit) as e:
        ears_main(["wake", "add", *argv])
    assert e.value.code == 2
    assert "--self" in capsys.readouterr().err
    assert not (tmp_path / "wake.json").exists()


@pytest.mark.parametrize("email, agent, named", [
    ("alice+desk@{host}", "mini-me", "alice+desk"),                  # a caller's EMAIL
    ("proj+task-1.worker.0123abcd@{host}", "worker",                 # a task seat
     "proj+task-1.worker.0123abcd"),
], ids=["caller", "task-seat"])
def test_an_address_that_is_not_the_launch_is_refused(tmp_path, email, agent, named):
    """F11: the generator inherits the caller's EMAIL; refusing never registers it."""
    r = generator(tmp_path, EMAIL=email.format(host=host()), CLAUDE_CODE_AGENT=agent)
    assert r.returncode == 1
    lines = r.stderr.splitlines()
    assert len(lines) == 1, lines
    assert named in lines[0] and agent in lines[0] and "EMAIL" in lines[0]
    assert f"ears wake add {agent}" in lines[0]
    assert sorted(p.name for p in tmp_path.iterdir()) == []    # no wake file, no lock file


@pytest.mark.parametrize("extra", [{"EMAIL": "me+git@example.com"},
                                   {"TM_AGENT_ADDRESS": "mini-me"}],
                         ids=["email-elsewhere", "agreeing-tm-agent-address"])
def test_what_does_not_disagree_registers_the_launch(tmp_path, extra):
    r = generator(tmp_path, CLAUDE_CODE_AGENT="mini-me", **extra)
    assert r.returncode == 0, r.stderr
    assert seats_in(tmp_path) == ["mini-me"]


@pytest.mark.parametrize("name", ["wake.json", "wake.example.json"])
def test_a_refused_add_on_the_shipped_file_leaves_no_lock(name):
    before = sorted(p.name for p in SHIPPED_DIR.iterdir())
    with pytest.raises(wake.WakeError, match="shipped default"):
        wake.add("dave", "dav", SHIPPED_DIR / name)
    assert sorted(p.name for p in SHIPPED_DIR.iterdir()) == before
    assert not any(n.endswith(".lock") for n in before)


def test_a_bad_names_is_refused_before_the_lock(tmp_path):
    with pytest.raises(wake.WakeError, match="do not compile"):
        wake.add("dave", "dav(", tmp_path / "wake.json")
    assert sorted(p.name for p in tmp_path.iterdir()) == []


# -- the lock and the no-op -------------------------------------------------------------

def test_eight_concurrent_adds_keep_all_eight_seats(tmp_path):
    f = tmp_path / "wake.json"
    code = "import sys; from ears import wake; wake.add(sys.argv[1], None, sys.argv[2])"
    procs = [subprocess.Popen([sys.executable, "-c", code, f"seat{i}", str(f)],
                              stderr=subprocess.PIPE, text=True) for i in range(8)]
    for p in procs:
        assert p.wait(timeout=60) == 0, p.stderr.read()
    assert seats_in(tmp_path) == [f"seat{i}" for i in range(8)]
    assert sorted(p.name for p in tmp_path.iterdir()) == [LOCK, "wake.json"]


def test_an_add_waits_for_the_lock(tmp_path):
    """Deterministic: while the lock is held, an add neither reads nor writes."""
    f = tmp_path / "wake.json"
    done = threading.Event()
    t = threading.Thread(target=lambda: (wake.add("dave", None, f), done.set()), daemon=True)
    with wake.locked(f):
        t.start()
        assert not done.wait(0.3)
        assert not f.exists()
    t.join(timeout=10)
    assert done.is_set() and seats_in(tmp_path) == ["dave"]


def test_an_unchanged_add_writes_nothing_a_changed_one_does(tmp_path):
    f = tmp_path / "wake.json"
    wake.add("mini-me", None, f)
    st = os.stat(f)
    assert wake.add("mini-me", None, f)[2] == r"mini\-me"
    assert wake.add("mini-me", "mini\\-me", f)[2] == r"mini\-me"   # a name it holds
    st2 = os.stat(f)
    assert (st2.st_mtime_ns, st2.st_ino) == (st.st_mtime_ns, st.st_ino)
    wake.add("mini-me", "minnie me", f)
    assert os.stat(f).st_ino != st.st_ino
    assert wake.seats(f) == {"mini-me": r"mini\-me|minnie me"}


def test_the_line_says_the_key_when_spoken_is_off(tmp_path, ids, capsys):
    f = tmp_path / "wake.json"
    f.write_text(json.dumps({"defaults": {"spoken": False}, "seats": {}}))
    ids(CLAUDE_CODE_AGENT="mini-me")
    assert ears_main(["wake", "add", "--self"]) == 0
    assert '"hey mini-me" calls it' in capsys.readouterr().out


def test_self_takes_names_and_hint_as_seat_does(tmp_path, ids, capsys):
    ids(CLAUDE_CODE_AGENT="mini-me")
    assert ears_main(["wake", "add", "--self", "--names", "minnie me", "--hint", "Mini Me"]) == 0
    assert json.loads((tmp_path / "wake.json").read_text())["seats"] == {
        "mini-me": {"names": r"mini\-me|minnie me|Mini\ Me", "hint": "Mini Me"}}
