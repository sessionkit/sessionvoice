"""The one reader of the operator block (EARS-304 R1, R2a, R7): ``ears.operator_config``.

Every test stubs ``session``: PATH is a tmp dir holding only the stub (or nothing),
so no test ever runs this host's ``session operator`` or reads its block. The stub
uses shell builtins only (plus ``/bin/sleep`` for the hang), and it counts its runs
and touches a marker file, so a test can prove whether it ran at all.
"""
from __future__ import annotations

import ast
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ears import operator_config as oc
from tests import env_fence

SRC = Path(__file__).resolve().parents[1] / "src"
HOST = "host1"

CONFIGURED = {"operator": "theo@host1.example.ts.net", "mailbox": "theo",
              "privileged": ["theo", "theo@host1.example.ts.net", "vera"]}
UNCONFIGURED = {"operator": None, "mailbox": None, "privileged": []}
# AGENT_OPERATOR_ADDR alone: sessiontools moves `operator` and leaves `privileged` empty.
ADDR_ONLY = {"operator": "someone@elsewhere.example", "mailbox": None, "privileged": []}


@pytest.fixture(autouse=True)
def _fresh():
    oc.clear()
    yield
    oc.clear()


@pytest.fixture
def stub(tmp_path, monkeypatch):
    """``stub(answer=..., body=...)``: a fake ``session`` alone on PATH, reader switched ON.

    ``answer`` (any JSON value, or a raw str) is printed for ``session operator``;
    ``body`` replaces the whole behaviour. Returns a handle with ``runs()`` and
    ``marker`` (touched on every run)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    marker = tmp_path / "session-ran"
    count = tmp_path / "session-runs"
    monkeypatch.setenv("PATH", str(bindir))
    env_fence.delenv(monkeypatch, oc.SESSION_BIN_ENV)

    class Stub:
        path = bindir / "session"

        def __init__(self):
            self.marker, self.count = marker, count

        def runs(self) -> int:
            return len(count.read_text().splitlines()) if count.exists() else 0

    def make(answer=CONFIGURED, body=None, shebang="#!/bin/sh"):
        monkeypatch.setenv("PATH", str(bindir))               # again: a test may have moved it
        env_fence.delenv(monkeypatch, oc.SESSION_BIN_ENV)
        if body is None:
            out = answer if isinstance(answer, str) else json.dumps(answer)
            body = f"printf '%s\\n' {shlex.quote(out)}"
        script = "\n".join([
            shebang,
            f": > {shlex.quote(str(marker))}",
            f"echo run >> {shlex.quote(str(count))}",
            '[ "$1" = operator ] || exit 9',
            body,
            "",
        ])
        Stub.path.write_text(script)
        Stub.path.chmod(0o755)
        return Stub()

    return make


# -- parsing -------------------------------------------------------------------

def test_a_configured_block_is_read(stub):
    s = stub({"operator": " theo@host1.example.ts.net ", "mailbox": " Theo ",
              "privileged": [" Vera ", "THEO", "theo@HOST1.example.ts.net"]})
    op = oc.read()
    assert op == oc.Operator("theo@host1.example.ts.net", "theo",
                             frozenset({"vera", "theo", "theo@host1.example.ts.net"}))
    assert s.runs() == 1


def test_unknown_keys_are_ignored(stub):
    stub(dict(CONFIGURED, speaker="theo", extra={"a": 1}))
    assert oc.read() == oc.parse(CONFIGURED)
    assert oc.read().privileged == frozenset(CONFIGURED["privileged"])


@pytest.mark.parametrize("bad", ["vera", {"vera": True}, None, 3, True])
def test_a_privileged_that_is_not_a_list_is_empty(stub, bad):
    stub(dict(CONFIGURED, privileged=bad))
    op = oc.read()
    assert op.privileged == frozenset()
    assert op.operator == "theo@host1.example.ts.net"          # the rest is still read
    assert oc.fallback_reason() is None                       # it answered: unconfigured, not a failure


def test_non_strings_and_empties_are_dropped_from_privileged(stub):
    stub(dict(CONFIGURED, privileged=["vera", "", "   ", None, 7, ["x"], {"y": 1}, "Ivy "]))
    assert oc.read().privileged == frozenset({"vera", "ivy"})


@pytest.mark.parametrize("v", ["", "   ", None, 5, ["theo"], {"m": 1}])
def test_operator_and_mailbox_must_be_non_empty_strings(stub, v):
    stub(dict(CONFIGURED, operator=v, mailbox=v))
    op = oc.read()
    assert op.operator is None and op.mailbox is None
    assert op.privileged                                       # a bad field removes only itself


# -- every NOTHING case, with its reason -----------------------------------------

def test_nothing_configured_is_nothing_and_unconfigured(stub):
    stub(UNCONFIGURED)
    assert oc.read() == oc.NOTHING
    assert oc._reason == oc.UNCONFIGURED
    assert oc.fallback_reason() is None


def test_no_session_on_path_is_absent(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    env_fence.delenv(monkeypatch, oc.SESSION_BIN_ENV)
    assert oc.read() == oc.NOTHING
    assert oc.fallback_reason() == "absent"


@pytest.mark.parametrize("answer, body", [
    (None, "exit 2"),                                  # a host's sessiontools without the verb (argparse)
    (None, "printf 'usage: session ...\\n'; exit 1"),
    ("not json at all", None),
    ("", None),
    ('{"operator": "theo@host1", ', None),                  # truncated
    ([CONFIGURED], None),                               # JSON, not an object
    ("\"theo\"", None),
    ("null", None),
    ("42", None),
])
def test_every_failure_is_nothing_and_failed(stub, answer, body):
    s = stub(answer, body=body)
    assert oc.read() == oc.NOTHING
    assert oc.fallback_reason() == "failed"
    assert s.runs() == 1


def test_a_configured_answer_on_a_non_zero_exit_is_still_nothing(stub):
    stub(body=f"printf '%s\\n' {shlex.quote(json.dumps(CONFIGURED))}; exit 3")
    assert oc.read() == oc.NOTHING
    assert oc.fallback_reason() == "failed"


def test_a_session_that_cannot_be_executed_is_failed(stub):
    stub(shebang="#!/nonexistent/interpreter")          # exec fails: OSError
    assert oc.read() == oc.NOTHING
    assert oc.fallback_reason() == "failed"


def test_a_hanging_session_times_out_as_failed(stub):
    stub(body="exec /bin/sleep 30")
    t0 = time.monotonic()
    assert oc.read(timeout=0.3) == oc.NOTHING
    assert time.monotonic() - t0 < 10
    assert oc.fallback_reason() == "failed"


def test_the_default_timeout_is_two_seconds():
    assert oc.TIMEOUT_S == 2.0
    assert oc.TTL_S == 60.0


def test_read_never_raises_on_an_unexpected_os_error(stub, monkeypatch):
    stub()

    def boom(*a, **k):
        raise PermissionError("nope")

    monkeypatch.setattr(subprocess, "run", boom)
    assert oc.read() == oc.NOTHING
    assert oc.fallback_reason() == "failed"


# -- the off switch: MOUTH_SESSION_BIN set AT ALL --------------------------

@pytest.mark.parametrize("value", ["", "PATH_TO_STUB"])
def test_off_switch_leak(stub, monkeypatch, value):
    """Set to "" or to a PATH (even the stub's own), nothing runs and nothing is read."""
    s = stub(CONFIGURED)
    monkeypatch.setenv(oc.SESSION_BIN_ENV, str(s.path) if value == "PATH_TO_STUB" else value)
    assert oc.current() == oc.NOTHING
    assert oc.fallback_reason() == "off"
    assert oc.read() == oc.NOTHING
    assert oc.fallback_reason() == "off"
    assert not oc.is_privileged("vera@host1", HOST)
    assert not oc.is_privileged("theo@host1.example.ts.net", HOST)
    assert not s.marker.exists(), "the stub ran with the off switch set"
    assert s.runs() == 0


def test_off_switch_control_unset_reads_the_block(stub, monkeypatch):
    """The control for the leak test: the same stub, the variable unset, is read."""
    s = stub(CONFIGURED)
    env_fence.delenv(monkeypatch, oc.SESSION_BIN_ENV)
    assert oc.current() == oc.parse(CONFIGURED)
    assert s.marker.exists()
    assert s.runs() == 1
    assert oc.fallback_reason() is None


def test_the_conftest_fence_is_on():
    """tests/conftest.py sets the off switch, so a test that forgets to stub reads nothing."""
    assert os.environ.get(oc.SESSION_BIN_ENV) == ""
    assert oc.current() == oc.NOTHING
    assert oc.fallback_reason() == "off"


def test_the_off_switch_is_checked_before_the_cache(stub, monkeypatch):
    s = stub(CONFIGURED)
    assert oc.current().privileged                          # cached, configured
    monkeypatch.setenv(oc.SESSION_BIN_ENV, "")
    assert oc.current() == oc.NOTHING                        # not the cached answer
    assert oc.fallback_reason() == "off"
    env_fence.delenv(monkeypatch, oc.SESSION_BIN_ENV)
    assert oc.current() == oc.parse(CONFIGURED)              # the cache is back, untouched
    assert oc.fallback_reason() is None
    assert s.runs() == 1


# -- the cache -------------------------------------------------------------------

class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def monotonic(self):
        return self.t


def test_two_calls_within_the_ttl_run_session_once(stub):
    s = stub(CONFIGURED)
    a, b = oc.current(), oc.current()
    assert a == b == oc.parse(CONFIGURED)
    assert s.runs() == 1


def test_clear_forces_a_re_read(stub):
    s = stub(CONFIGURED)
    oc.current()
    oc.clear()
    oc.current()
    assert s.runs() == 2


def test_the_ttl_expires_after_sixty_seconds(stub, monkeypatch):
    s = stub(CONFIGURED)
    clock = _Clock()
    monkeypatch.setattr(oc, "time", clock)
    oc.current()
    clock.t += 59.9
    oc.current()
    assert s.runs() == 1
    clock.t += 0.2                                          # 60.1 s after the read
    oc.current()
    assert s.runs() == 2


def test_a_change_to_the_block_is_seen_after_the_ttl(stub, monkeypatch):
    stub(UNCONFIGURED)
    clock = _Clock()
    monkeypatch.setattr(oc, "time", clock)
    assert oc.current() == oc.NOTHING
    stub(CONFIGURED)
    assert oc.current() == oc.NOTHING                        # still cached
    clock.t += 61
    assert oc.current() == oc.parse(CONFIGURED)


@pytest.mark.parametrize("answer, body, reason", [
    (None, "exit 2", "failed"),
    (None, "printf 'junk\\n'", "failed"),
    (UNCONFIGURED, None, None),
])
def test_failures_are_cached_too(stub, answer, body, reason):
    s = stub(answer, body=body)
    assert oc.current() == oc.NOTHING
    assert oc.current() == oc.NOTHING
    assert s.runs() == 1
    assert oc.fallback_reason() == reason


def test_a_hang_is_cached_so_it_costs_once_per_ttl(stub, monkeypatch):
    """A hang times out once, and the cache serves that failure for the TTL.

    Pinned by COUNTS on an injected clock, not by wall clock (EARS-330): this
    asserted the cached call returned in under 0.2 s, and a loaded gate went red
    on it. "Served from the cache" is "the reader did not run again", which is a
    count; the TTL is judged on ``_Clock``, so the hang's own 0.3 s cannot age
    the cache and a slow box cannot either.

    The reader's runs are counted by a spy on ``_read``, not by the stub's
    ``s.runs()``: the stub writes its count only once ``sh`` reaches its first
    line, and the 0.3 s timeout kills it, so on a slow box that count can read 0
    (a wall-clock deadline in disguise). The stub's count is held to "did not
    move" across the cached call instead: a cache hit starts no process at all,
    so that holds however slow the box is."""
    s = stub(body="exec /bin/sleep 30")
    monkeypatch.setattr(oc, "TIMEOUT_S", 0.3)
    clock = _Clock()
    monkeypatch.setattr(oc, "time", clock)
    reads = []
    real = oc._read
    monkeypatch.setattr(oc, "_read", lambda timeout: reads.append(timeout) or real(timeout))
    assert oc.current() == oc.NOTHING and oc.fallback_reason() == "failed"   # it timed out
    assert reads == [0.3]
    ran = s.runs()                           # 1; 0 if sh was killed before its first line
    clock.t += oc.TTL_S - 0.1
    assert oc.current() == oc.NOTHING and oc.fallback_reason() == "failed"
    assert reads == [0.3] and s.runs() == ran                # served from the cache: nothing ran
    clock.t += 0.2                                           # past the TTL: the hang costs again
    assert oc.current() == oc.NOTHING and oc.fallback_reason() == "failed"
    assert reads == [0.3, 0.3]


def test_the_reason_follows_the_cached_answer(stub, monkeypatch):
    """A cache hit gives back the reason recorded with that answer, not a later one."""
    stub(body="exit 2")
    assert oc.current() == oc.NOTHING and oc.fallback_reason() == "failed"
    monkeypatch.setenv(oc.SESSION_BIN_ENV, "")
    oc.current()
    assert oc.fallback_reason() == "off"
    env_fence.delenv(monkeypatch, oc.SESSION_BIN_ENV)
    oc.current()                                            # the cached failure
    assert oc.fallback_reason() == "failed"


def test_the_cache_holds_one_tuple_swapped_whole(stub):
    stub(CONFIGURED)
    oc.current()
    op, why, stamp = oc._cache
    assert isinstance(op, oc.Operator) and why is None and isinstance(stamp, float)


# -- fallback_reason() and the warning text -----------------------------------------

def test_fallback_reason_for_each_reason(stub, tmp_path, monkeypatch):
    # off
    monkeypatch.setenv(oc.SESSION_BIN_ENV, "")
    oc.current()
    assert oc.fallback_reason() == "off"
    # absent
    oc.clear()
    empty = tmp_path / "nothing-here"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    env_fence.delenv(monkeypatch, oc.SESSION_BIN_ENV)
    oc.current()
    assert oc.fallback_reason() == "absent"
    # failed
    oc.clear()
    stub(body="exit 2")
    oc.current()
    assert oc.fallback_reason() == "failed"
    # unconfigured: never a fallback
    oc.clear()
    stub(UNCONFIGURED)
    oc.current()
    assert oc.fallback_reason() is None
    # AGENT_OPERATOR_ADDR alone: unconfigured too
    oc.clear()
    stub(ADDR_ONLY)
    oc.current()
    assert oc.fallback_reason() is None
    # configured
    oc.clear()
    stub(CONFIGURED)
    oc.current()
    assert oc.fallback_reason() is None


def test_fallback_reason_is_none_before_anything_is_read():
    assert oc.fallback_reason() is None


def test_the_reason_lives_beside_the_answer_not_inside_it(stub):
    stub(body="exit 2")
    op = oc.current()
    assert op is oc.NOTHING or op == oc.NOTHING
    assert oc.Operator._fields == ("operator", "mailbox", "privileged")


def test_allow_fallback_text_and_reason_words():
    for code, words in [("off", f"off (${oc.SESSION_BIN_ENV} is set)"),
                        ("absent", "missing (no session on PATH)"),
                        ("failed", "failing (exit, timeout or bad output)")]:
        assert oc.reason_text(code) == words
        line = oc.ALLOW_FALLBACK.format(reason=oc.reason_text(code))
        assert line.startswith(f"mouth: the operator reader is {words}, so $MOUTH_MAIL_ALLOW still ADDS")
        assert "ALLOW does not narrow" in line
    assert oc.reason_text(None) == "" and oc.reason_text("unconfigured") == ""


# -- is_privileged (R2a) ----------------------------------------------------------

@pytest.fixture
def priv(stub):
    stub(dict(CONFIGURED, privileged=["vera", "Theo", "vera@OtherHost.Example"]))


@pytest.mark.parametrize("sender", [
    "vera@otherhost.example",
    "VERA@OTHERHOST.EXAMPLE",
    "Vera@OtherHost.example",
    " vera@otherhost.example ",
])
def test_an_at_entry_matches_exactly_case_insensitive(priv, sender):
    assert oc.is_privileged(sender, HOST)


@pytest.mark.parametrize("sender", [
    "xvera@otherhost.example",
    "vera@otherhost.example.evil.com",
    "vera@sub.otherhost.example",
    "vera@otherhost.exampl",
    "ivy@otherhost.example",
])
def test_an_at_entry_matches_nothing_else(priv, sender):
    assert not oc.is_privileged(sender, HOST)


@pytest.mark.parametrize("sender", [
    "vera@host1",
    "VERA@HOST1",
    "vera@host1.example.ts.net",
    "vera@host1.anything",
    "theo@host1",
    "THEO@host1.example.ts.net",
])
def test_a_bare_entry_is_that_mailbox_on_this_host(priv, sender):
    assert oc.is_privileged(sender, HOST)
    assert oc.is_privileged(sender, "HOST1")                     # the host is matched case-insensitively


@pytest.mark.parametrize("sender", [
    "theo@otherhost.example",                                # bare: never another host
    "vera@host2.example.ts.net",
    "vera@host1x",
    "vera@host1evil.com",
    "vera@xhost1",
    "notvera@host1",
    "vera.x@host1",
    "ivy@host1",                                                # not privileged at all
    "vera",                                                  # no address
    "",
])
def test_a_bare_entry_is_refused_from_another_host(priv, sender):
    assert not oc.is_privileged(sender, HOST)


def test_a_bare_entry_matches_nothing_without_a_host(priv):
    assert not oc.is_privileged("vera@host1", "")
    assert not oc.is_privileged("vera@", "")
    assert oc.is_privileged("vera@otherhost.example", "")     # an @ entry needs no host


def test_nothing_is_privileged_when_unconfigured(stub):
    stub(UNCONFIGURED)
    assert not oc.is_privileged("vera@host1", HOST)
    assert not oc.is_privileged("theo@host1", HOST)


def test_addr_only_grants_nothing(stub):
    stub(ADDR_ONLY)
    op = oc.current()
    assert op.operator == "someone@elsewhere.example" and op.privileged == frozenset()
    assert not oc.is_privileged("someone@elsewhere.example", HOST)


# -- __main__: address|mailbox|json -------------------------------------------------

def _run_main(stub_dir: Path, *args: str, off: bool = False) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != oc.SESSION_BIN_ENV}
    env["PATH"] = str(stub_dir)
    env["PYTHONPATH"] = os.pathsep.join([str(SRC)] + [p for p in [os.environ.get("PYTHONPATH")] if p])
    if off:
        env[oc.SESSION_BIN_ENV] = ""
    return subprocess.run([sys.executable, "-m", "ears.operator_config", *args],
                          capture_output=True, text=True, env=env, timeout=30, check=False)


@pytest.mark.parametrize("answer, address", [
    (CONFIGURED, "theo@host1.example.ts.net"),
    (UNCONFIGURED, ""),
    (ADDR_ONLY, ""),                                         # AGENT_OPERATOR_ADDR alone: the keeper mails nobody
    (dict(CONFIGURED, operator=None), ""),                   # privileged but no address: nothing to print
])
def test_main_address_only_when_configured(stub, answer, address):
    s = stub(answer)
    r = _run_main(s.path.parent, "address")
    assert r.returncode == 0
    assert r.stdout == address + "\n"


@pytest.mark.parametrize("answer, address", [
    (CONFIGURED, "theo@host1.example.ts.net"),
    (UNCONFIGURED, ""),
    (ADDR_ONLY, ""),
    (dict(CONFIGURED, operator=None), ""),
])
def test_address_is_the_operator_only_when_configured(stub, answer, address):
    """``address()`` is what the keeper's notices and ``ears services install`` both take."""
    stub(answer)
    assert oc.address() == address


def test_main_mailbox_and_json(stub):
    s = stub(CONFIGURED)
    r = _run_main(s.path.parent, "mailbox")
    assert (r.returncode, r.stdout) == (0, "theo\n")
    r = _run_main(s.path.parent, "json")
    assert r.returncode == 0
    assert json.loads(r.stdout) == {"operator": "theo@host1.example.ts.net", "mailbox": "theo",
                                    "privileged": ["theo", "theo@host1.example.ts.net", "vera"]}


@pytest.mark.parametrize("body", ["exit 2", "printf 'junk\\n'", "exec /bin/sleep 30"])
@pytest.mark.parametrize("what", ["address", "mailbox"])
def test_main_on_a_failing_session_prints_an_empty_line_and_exits_0(stub, monkeypatch, body, what):
    s = stub(body=body)
    r = _run_main(s.path.parent, what)
    assert (r.returncode, r.stdout) == (0, "\n")


def test_main_json_on_nothing(stub):
    s = stub(body="exit 2")
    r = _run_main(s.path.parent, "json")
    assert r.returncode == 0
    assert json.loads(r.stdout) == {"operator": None, "mailbox": None, "privileged": []}


def test_main_with_no_session_on_path(tmp_path):
    empty = tmp_path / "e"
    empty.mkdir()
    r = _run_main(empty, "address")
    assert (r.returncode, r.stdout) == (0, "\n")


def test_main_honours_the_off_switch(stub):
    s = stub(CONFIGURED)
    r = _run_main(s.path.parent, "address", off=True)
    assert (r.returncode, r.stdout) == (0, "\n")
    assert not s.marker.exists()


@pytest.mark.parametrize("args", [(), ("operator",), ("address", "mailbox"), ("--help",)])
def test_main_bad_usage_still_exits_0(stub, args):
    s = stub(CONFIGURED)
    r = _run_main(s.path.parent, *args)
    assert (r.returncode, r.stdout) == (0, "\n")
    assert "usage" in r.stderr


def test_main_in_process(stub, capsys):
    stub(CONFIGURED)
    assert oc.main(["address"]) == 0
    assert capsys.readouterr().out == "theo@host1.example.ts.net\n"


# -- R7: no sessiontools, no session.json, stdlib only --------------------------------

def test_the_module_imports_only_the_standard_library():
    tree = ast.parse((SRC / "ears" / "operator_config.py").read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[0])
    assert names <= {"__future__", "json", "os", "sys", "time", "typing", "shutil", "subprocess"}, names
    assert "sessiontools" not in names


def test_the_module_opens_no_file():
    """It reads `session operator`'s stdout only: no open(), no Path reads."""
    tree = ast.parse((SRC / "ears" / "operator_config.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
            assert name not in {"open", "read_text", "read_bytes", "load"}, name


def test_only_the_operator_verb_is_run(stub, monkeypatch):
    stub(CONFIGURED)
    seen = []
    real = subprocess.run

    def spy(argv, *a, **k):
        seen.append(list(argv))
        return real(argv, *a, **k)

    monkeypatch.setattr(subprocess, "run", spy)
    oc.read()
    assert len(seen) == 1 and seen[0][1:] == ["operator"]
    assert Path(seen[0][0]).name == "session"
