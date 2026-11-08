"""EARS-492 do-004: seat homes, and tools' folders read THROUGH tools (design.md D5).

``ears.home.seat_home(seat)`` is the ONE rule: ``$EARS_HOME``; tools' home for the seat (asked
of tools: ``sessiontools.settings``, else ``session path agent``); ``~/.<seat>``; ``~/.ears``
while it is real; else ``<state_dir(environ={})>/seats/<seat>/``, which only ears-arm makes.
Tools' OLD folders (rung c, :data:`ears.home.TOOLS_OLD`) are read only when tools cannot be
asked at all; EARS-512 deletes them. The readers cut over: ears-arm (and its bin/ shim),
heard-first's seat names, ears-count (no home at all), talk's ``seat_home``, ``waiters``,
faces and ride receipts.

Every test runs on a fake HOME under tmp_path, with a fake ``session`` first on PATH and
sessiontools either hidden or a fake (tests/fake_tools.py). A DECOY tree at tools' literal old
folders, holding a DIFFERENT heard.state, proves that nothing literal is read while tools
answers. No launchctl, no install, no restart, no write under a live home.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from ears import home
from talk import server
from tests import fake_tools, plugin_copy

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
SEAT = "zed"


class _Home(type(Path())):
    """The fake HOME, carrying ``nobin``: the bin folder of its fake ``session``."""


@pytest.fixture
def h(tmp_path, monkeypatch):
    """A fake HOME; no seat or root override from the runner; tools NOT askable (rung c)."""
    d = _Home(str(tmp_path / "home"))
    d.mkdir()
    monkeypatch.setenv("HOME", str(d))
    for v in ("EARS_HOME", *home.ENV, "SESSIONS_DIR"):
        monkeypatch.delenv(v, raising=False)
    d.nobin = fake_tools.no_tools(tmp_path, monkeypatch)
    return d


def tree(*dirs: Path) -> list[str]:
    """Every path under DIRS (links not followed): a before/after snapshot."""
    out = []
    for d in dirs:
        if os.path.lexists(d):
            out.append(str(d))
            out += sorted(str(p) for p in Path(d).rglob("*"))
    return out


def state_file(d: Path, tag: str) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    (d / "heard.state").write_text(json.dumps({"offsets": {}, "tag": tag}))
    return d / "heard.state"


def decoy(h: Path, seat: str = SEAT) -> list[Path]:
    """A heard.state at each of tools' literal old seat homes, tagged DECOY."""
    return [state_file(h / rel / "home" / seat, "DECOY")
            for rel in (home.TOOLS_OLD["agent"], home.TOOLS_OLD["agents"])]


def fresh(h: Path, seat: str = SEAT) -> Path:
    return h / home.NEW / home.SEATS / seat


# ---- the ladder, pinned --------------------------------------------------------------------

def test_the_ladder_in_order_with_tools_unaskable(h, tmp_path, monkeypatch):
    """Each step wins over the ones below it; the steps that are read are returned only when
    they exist; nothing is made."""
    want = [(fresh(h), "fresh")]
    assert home.seat_source(SEAT) == want[-1]
    assert not fresh(h).exists() and not (h / ".ears").exists()
    (h / ".ears").mkdir()
    assert home.seat_source(SEAT) == (h / ".ears", "old")                 # 4: the old shared home
    (h / f".{SEAT}").mkdir()
    assert home.seat_source(SEAT) == (h / f".{SEAT}", "own")             # 3: ~/.<seat>
    (h / home.TOOLS_OLD["agents"] / "home" / SEAT).mkdir(parents=True)
    assert home.seat_source(SEAT) == (h / home.TOOLS_OLD["agents"] / "home" / SEAT, "tools")
    (h / home.TOOLS_OLD["agent"] / "home" / SEAT).mkdir(parents=True)  # tools' own order
    assert home.seat_source(SEAT) == (h / home.TOOLS_OLD["agent"] / "home" / SEAT, "tools")
    monkeypatch.setenv("EARS_HOME", str(tmp_path / "eh"))
    assert home.seat_source(SEAT) == (tmp_path / "eh", "override")       # 1: over everything
    assert not (tmp_path / "eh").exists()


def test_ears_home_empty_is_unset_and_a_tilde_is_the_home(h, monkeypatch):
    monkeypatch.setenv("EARS_HOME", "")
    assert home.seat_home(SEAT) == fresh(h)
    monkeypatch.setenv("EARS_HOME", "~/mine")
    assert home.seat_home(SEAT) == h / "mine"


@pytest.mark.parametrize("var", home.ENV)
def test_the_fallback_leaves_the_root_overrides_out(var, h, tmp_path, monkeypatch):
    """talk runs as a launchd job (no root override) and ears-arm from a shell (maybe one):
    both name ONE folder, <state_dir(environ={})>/seats/<seat>."""
    monkeypatch.setenv(var, str(tmp_path / "elsewhere"))
    assert home.seat_home(SEAT) == home.state_dir(environ={}) / home.SEATS / SEAT == fresh(h)
    r = subprocess.run([sys.executable, str(ROOT / "src/ears/home.py"), "seat-home", SEAT],
                       capture_output=True, text=True, env={**os.environ})
    assert r.returncode == 0 and r.stdout.strip() == str(fresh(h))


def test_the_fallback_follows_an_adopted_old_root(h):
    conv = h / home.OLD_ROOTS[0] / "logs" / "conversations"
    conv.mkdir(parents=True)
    (conv / "heard.lock").write_text("")
    assert home.seat_home(SEAT) == h / home.OLD_ROOTS[0] / home.SEATS / SEAT


def test_the_old_shared_home_linked_into_the_kit_is_not_real(h, tmp_path):
    """A migrate that left ~/.ears a link into the kit leaves no second home; a link to a
    folder outside the kit is still the host's shared home (unchanged)."""
    inside = h / home.NEW / home.SEATS / "shared"
    inside.mkdir(parents=True)
    (h / ".ears").symlink_to(inside)
    assert home.seat_source(SEAT) == (fresh(h), "fresh")
    assert home.shared_seat_home() is None
    (h / ".ears").unlink()
    outside = tmp_path / "ears-elsewhere"
    outside.mkdir()
    (h / ".ears").symlink_to(outside)
    assert home.seat_source(SEAT) == (h / ".ears", "old")
    assert home.shared_seat_home() == h / ".ears"


def test_the_shared_home_is_ears_home_or_the_real_old_one(h, tmp_path, monkeypatch):
    assert home.shared_seat_home() is None                     # a fresh host has none
    (h / ".ears").mkdir()
    assert home.shared_seat_home() == h / ".ears"
    monkeypatch.setenv("EARS_HOME", str(tmp_path / "eh"))
    assert home.shared_seat_home() == tmp_path / "eh"


@pytest.mark.parametrize("bad", ["", "..", "a/b", "-x", ".hidden", "x y"])
def test_a_name_that_is_not_a_seat_is_refused(bad, h, capsys):
    with pytest.raises(ValueError):
        home.seat_home(bad)
    assert home.main(["seat-home", bad]) == 2
    out, err = capsys.readouterr()
    assert out == "" and "not a seat name" in err


def test_the_cli_prints_the_seat_home(h, capsys):
    assert home.main(["seat-home", SEAT]) == 0
    assert capsys.readouterr().out.strip() == str(fresh(h))
    from ears import cli
    assert cli.main(["home", "seat-home", SEAT]) == 0
    assert capsys.readouterr().out.strip() == str(fresh(h))


# ---- tools answers: a fake `session` on PATH (rung b), a fake sessiontools (rung a) ----------

@pytest.fixture(params=["session path", "sessiontools"])
def tools(request, h, tmp_path, monkeypatch):
    """Tools ANSWERS, with its folders under a fake root (not under HOME), one rung each. The
    decoy tree sits at tools' literal old folders under HOME."""
    root = tmp_path / "tools-root"
    if request.param == "sessiontools":
        fake_tools.with_sessiontools(tmp_path, monkeypatch, root)
    else:
        fake_tools.session_bin(h.nobin, agent=root / "agent", sessions=root / "sessions")
    decoy(h)
    tools.root, tools.how, tools.bin = root, request.param, h.nobin
    return tools


def test_tools_is_asked_and_its_seat_home_wins(tools, h):
    t = home.tools_agent()
    assert t.how == tools.how
    seat = state_file(tools.root / "agent" / "home" / SEAT, "TOOLS").parent
    assert home.seat_source(SEAT, tools=t) == (seat, "tools")
    assert home.seat_home(SEAT) == seat
    assert ("DECOY" not in (home.seat_home(SEAT) / "heard.state").read_text())


def test_while_tools_answers_the_old_folders_are_never_read(tools, h):
    """Tools answers but has no home for the seat: the next steps, never the decoy."""
    assert home.seat_source(SEAT) == (fresh(h), "fresh")
    (h / f".{SEAT}").mkdir()
    assert home.seat_source(SEAT) == (h / f".{SEAT}", "own")


def test_seat_names_come_from_tools_and_voices_own_seats(tools, h):
    state_file(tools.root / "agent" / "home" / "ana", "TOOLS")
    (tools.root / "agent" / "home" / "bob").mkdir(parents=True)        # no heard.state: no name
    state_file(fresh(h, "cy"), "VOICE")
    assert home.seat_names() == ["ana", "cy"]                           # never the decoy's zed


def test_a_folder_that_is_not_a_seat_name_names_no_seat(tools, h):
    """(review do-004 r2) A heard.state in a folder whose name is not a seat name (a kept copy,
    ``ana.old``) names no seat. seat_home refuses such a name, so talk's waiters, which feed
    every listed name to it, would raise on every poll pass."""
    state_file(tools.root / "agent" / "home" / "ana", "TOOLS")
    state_file(tools.root / "agent" / "home" / "ana.old", "TOOLS")
    state_file(fresh(h, "cy.bak"), "VOICE")
    assert home.seat_names() == ["ana"]
    assert set(server.waiters([])) == {"ana"}


def test_rung_a_starts_no_process(h, tmp_path, monkeypatch):
    fake_tools.with_sessiontools(tmp_path, monkeypatch, tmp_path / "tools-root")
    assert home.tools_agent().how == "sessiontools"
    home.seat_home(SEAT), home.seat_names(), home.avatar("ana")
    assert fake_tools.calls(h.nobin) == []


def test_rung_b_asks_session_path_agent_once(h, tmp_path):
    fake_tools.session_bin(h.nobin, agent=tmp_path / "tools-root" / "agent")
    t = home.tools_agent()
    assert t.how == "session path" and t.homes == (tmp_path / "tools-root" / "agent" / "home",)
    home.seat_home(SEAT, tools=t), home.seat_names(tools=t), home.avatar("ana", tools=t)
    assert fake_tools.calls(h.nobin) == ["path agent"]


@pytest.mark.parametrize("answer", ["", "relative/agent", "/a\n/b"])
def test_a_bad_session_path_answer_is_the_next_rung(answer, h, tmp_path):
    s = h.nobin / "session"
    s.write_text(f"#!/bin/sh\nprintf '%s\\n' '{answer}'\nexit 0\n")
    assert home.tools_agent().how == "old"


def test_a_failing_session_path_is_the_next_rung(h, tmp_path):
    """(review do-004 r2) An absolute path printed by a `session` that exits non-zero is no
    answer: the next rung."""
    s = h.nobin / "session"
    s.write_text(f"#!/bin/sh\nprintf '%s\\n' '{tmp_path / 'agent'}'\nexit 1\n")
    assert home.tools_agent().how == "old"


def test_with_no_tools_at_all_rung_c_is_read_and_nothing_is_created(h, tmp_path):
    seat = state_file(h / home.TOOLS_OLD["agent"] / "home" / SEAT, "OLD").parent
    state_file(h / home.TOOLS_OLD["agents"] / "home" / "ana", "OLD")
    before = tree(h)
    t = home.tools_agent()
    assert t.how == "old"
    assert home.seat_home(SEAT, tools=t) == seat
    assert home.seat_names(tools=t) == ["ana", SEAT]
    assert home.seat_home("newcomer", tools=t) == fresh(h, "newcomer")
    assert tree(h) == before


# ---- faces ---------------------------------------------------------------------------------

def face(p: Path, tag: bytes) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(tag)
    return p


def test_a_face_is_where_tools_keeps_it_and_never_the_decoy(tools, h):
    face(h / home.TOOLS_OLD["agent"] / "config" / "ana" / "avatar.jpg", b"DECOY")
    face(h / home.TOOLS_OLD["agents"] / "ana" / "avatar.jpg", b"DECOY")
    assert home.avatar("ana") is None
    want = face(tools.root / "agent" / "config" / "ana" / "avatar.jpg", b"TOOLS")
    if tools.how == "sessiontools":
        (tools.root / "agent" / "config" / "ana" / "ana.md").write_text("")
    assert home.avatar("ana") == want
    assert server.avatar_path("ana") == str(want)


def test_a_flat_agent_has_no_face_at_rung_a(h, tmp_path, monkeypatch):
    """(review do-004) sessiontools answers with the flat ``config/<name>.md`` layout: the
    agent has no folder, so no face, and never a stray ``config/avatar.jpg`` beside it."""
    root = tmp_path / "tools-root"
    fake_tools.with_sessiontools(tmp_path, monkeypatch, root)
    face(root / "agent" / "config" / "avatar.jpg", b"STRAY")
    (root / "agent" / "config" / "ana.md").write_text("")
    assert home.tools_agent().how == "sessiontools"
    assert home.avatar("ana") is None and server.avatar_path("ana") is None


def test_with_no_tools_a_face_is_read_from_the_old_folders_in_order(h):
    agents = face(h / home.TOOLS_OLD["agents"] / "ana" / "avatar.jpg", b"OLD")
    assert home.avatar("ana") == agents
    agent = face(h / home.TOOLS_OLD["agent"] / "config" / "ana" / "avatar.jpg", b"OLD")
    assert home.avatar("ana") == agent
    assert home.avatar("../ana") is None and server.avatar_path("nobody") is None


# ---- talk ----------------------------------------------------------------------------------

def test_talk_finds_seats_through_tools_not_the_literal_folders(tools, h):
    ana = state_file(tools.root / "agent" / "home" / "ana", "TOOLS")
    zed = state_file(tools.root / "agent" / "home" / SEAT, "TOOLS")
    assert server.seat_home(SEAT) == str(zed.parent)
    got = server.waiters(["ana", "bob"])
    assert got == {"ana": str(ana), SEAT: str(zed)}
    assert all("DECOY" not in Path(p).read_text() for p in got.values())


def test_talk_reads_a_sessions_receipts_through_tools(h, tmp_path, monkeypatch):
    """`session path sessions` answers a fake root: the ride/done there is read, the decoy
    in tools' literal old session folder (a HIGHER seq) is not, and no mail folder is."""
    monkeypatch.delenv("SESSIONMAIL_HOME", raising=False)
    monkeypatch.setattr(server, "RIDE_CACHE", {})
    sid = "388612b6-62b4-9c67-a5e5-2576659b3d37"

    def done(root: Path, seq: int):
        d = root / sid / "mail" / "ride" / "done"
        d.mkdir(parents=True, exist_ok=True)
        (d / "1.M1P1.host1").write_text(f"From: x\nEars-Seq: {seq}\n\nhi\n")

    done(h / home.TOOLS_OLD["sessions"], 999)                      # the decoy
    fake_tools.session_bin(h.nobin, sessions=tmp_path / "tools-root" / "sessions")
    done(tmp_path / "tools-root" / "sessions", 12)
    assert server.ride_done_seq(sid) == 12
    fake_tools.session_bin(h.nobin)                                 # tools cannot answer: rung c
    assert server.ride_done_seq(sid) == 999


def test_while_tools_answers_talk_reads_no_receipt_from_the_old_session_folder(tools, h,
                                                                              monkeypatch):
    """(review do-004) Tools answers but holds no ride/done for the session: talk shows NO
    receipt (0). The decoy in tools' literal old session folder is never a candidate while
    tools answers, so this holds even when the tools folder is missing, not only when the
    tools folder outranks it."""
    monkeypatch.delenv("SESSIONMAIL_HOME", raising=False)
    monkeypatch.setattr(server, "RIDE_CACHE", {})
    sid = "4a7c0e21-5b3d-4f6e-8a9b-0c1d2e3f4a5b"
    d = h / home.TOOLS_OLD["sessions"] / sid / "mail" / "ride" / "done"
    d.mkdir(parents=True)
    (d / "1.M1P1.host1").write_text("From: x\nEars-Seq: 999\n\nhi\n")   # the decoy
    assert server.session_dir_resolver() is not None
    assert server.ride_done_dir(sid) is None
    assert server.ride_done_seq(sid) == 0


# ---- ears-arm (both copies), run as a seat runs it --------------------------------------------

@pytest.fixture
def arm(h, tmp_path):
    """Run the plugin copy's ears-arm (its heard-first a stub, exit 99) on the fake HOME, with
    the fake `session` bin first on PATH and nothing else of the runner's but /usr/bin:/bin."""
    plugin = plugin_copy.make(tmp_path)
    seat = plugin_copy.probe_seat("ears492seat")

    def run(*args, via_shim=False, name=None, **extra):
        env = {k: v for k, v in os.environ.items()
               if k not in (*plugin_copy.STRIP, "PYTHONPATH", *home.ENV)}
        env.update(HOME=str(h), XDG_STATE_HOME=str(tmp_path / "xdg"),
                   PATH=os.pathsep.join([str(h.nobin), "/usr/bin", "/bin"]), **extra)
        script = plugin / ("bin" if via_shim else "skills/ears/scripts") / "ears-arm"
        return subprocess.run(["bash", str(script), name or seat, *args], capture_output=True,
                              text=True,
                              env=env, timeout=30)

    def state():
        """The --state the stub heard-first was given."""
        argv = (tmp_path / "plugin-ran").read_text().splitlines()[1]
        return Path(re.search(r"--state (\S+)", argv).group(1))

    run.seat, run.state, run.plugin = seat, state, plugin
    return run


@pytest.mark.parametrize("via_shim", [False, True])
def test_ears_arm_on_a_fresh_home_makes_only_seats_seat(arm, h, tmp_path, via_shim):
    before = tree(h)
    r = arm("talk", via_shim=via_shim)
    assert r.returncode == plugin_copy.STUB_RC, r.stderr
    seat = fresh(h, arm.seat)
    assert arm.state() == seat / "heard.state"
    made = sorted(set(tree(h)) - set(before))
    assert made == [str(h / ".sessionkit"), str(h / ".sessionkit/voice"), str(h / home.NEW),
                    str(h / home.NEW / home.SEATS), str(seat)]
    assert not (h / ".ears").exists()


def test_ears_arm_makes_ears_home_and_nothing_else(arm, h, tmp_path):
    before = tree(h)
    r = arm("talk", EARS_HOME=str(h / "mine"))
    assert r.returncode == plugin_copy.STUB_RC, r.stderr
    assert arm.state() == h / "mine" / "heard.state"
    assert sorted(set(tree(h)) - set(before)) == [str(h / "mine")]


@pytest.mark.parametrize("where", ["own", "old-shared", "tools-old"])
def test_ears_arm_makes_nothing_where_a_home_already_is(where, arm, h):
    d = {"own": h / f".{arm.seat}", "old-shared": h / ".ears",
         "tools-old": h / home.TOOLS_OLD["agent"] / "home" / arm.seat}[where]
    d.mkdir(parents=True)
    before = tree(h)
    r = arm("talk")
    assert r.returncode == plugin_copy.STUB_RC, r.stderr
    assert arm.state() == d / "heard.state"
    assert tree(h) == before


@pytest.mark.parametrize("how", ["session path", "sessiontools"])
def test_ears_arm_finds_the_seat_through_tools_not_the_decoy(how, arm, h, tmp_path):
    root = tmp_path / "tools-root"
    seat = state_file(root / "agent" / "home" / arm.seat, "TOOLS").parent
    decoy(h, arm.seat)
    extra = {}
    if how == "sessiontools":
        extra["PYTHONPATH"] = str(fake_tools.sessiontools_pkg(tmp_path / "pkg", root))
    else:
        fake_tools.session_bin(h.nobin, agent=root / "agent")
    before = tree(h, root)
    r = arm("talk", **extra)
    assert r.returncode == plugin_copy.STUB_RC, r.stderr
    assert arm.state() == seat / "heard.state"
    assert tree(h, root) == before
    if how == "sessiontools":
        assert fake_tools.calls(h.nobin) == []          # rung a: no `session` process


def test_ears_arm_with_no_tools_reads_rung_c(arm, h):
    seat = h / home.TOOLS_OLD["agents"] / "home" / arm.seat      # what ears-arm read before
    seat.mkdir(parents=True)
    r = arm("talk")
    assert r.returncode == plugin_copy.STUB_RC, r.stderr
    assert arm.state() == seat / "heard.state"


def test_ears_arm_dry_run_names_the_fallback_and_makes_nothing(arm, h):
    before = tree(h)
    r = arm("talk", "--dry-run")
    assert r.returncode == 0, r.stderr
    assert f"--state {fresh(h, arm.seat)}/heard.state" in r.stdout
    assert tree(h) == before


def test_ears_arm_refuses_a_name_that_is_not_a_seat(arm, h, tmp_path):
    """(review do-004 r2) home.py refuses the name (exit 2) and ears-arm stops there, armed or
    dry: no plan on stdout, no heard-first, nothing made. Without the stop, the plan named
    ``--state /heard.state``."""
    before = tree(h)
    for args in (("talk",), ("talk", "--dry-run")):
        r = arm(*args, name="a.b")
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        assert r.stdout == "" and "not a seat name" in r.stderr
    assert not (tmp_path / "plugin-ran").exists()
    assert tree(h) == before


def test_ears_arm_with_no_home_py_refuses_and_makes_nothing(arm, h):
    (arm.plugin / "src" / "ears" / "home.py").unlink()
    before = tree(h)
    r = arm("talk")
    assert r.returncode == 2
    assert "home.py" in r.stderr and not (Path(arm.plugin) / "..").joinpath("plugin-ran").exists()
    assert tree(h) == before


def test_both_ears_arm_copies_carry_no_home_rule():
    """bin/ears-arm is a shim that execs the one implementation; that one asks home.py."""
    shim = (ROOT / "bin" / "ears-arm").read_text()
    assert 'exec "$root/skills/ears/scripts/ears-arm"' in shim and "home" not in shim.split("set -euo")[1]
    arm_text = (ROOT / "skills/ears/scripts/ears-arm").read_text()
    assert 'home.py" seat-home "$seat"' in arm_text
    assert "$HOME/." not in arm_text


# ---- ears-count: no home at all ---------------------------------------------------------------

def test_ears_count_counts_by_cut_seat_only(tmp_path, h):
    """It names no home and reads no state file: a waiter on any --state counts by cut:<seat>,
    and a waiter on the seat's own home's state for ANOTHER seat does not."""
    text = (ROOT / "skills/ears/scripts/ears-count").read_text()
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert "home" not in code and "heard.state" not in code and "EARS_HOME" not in code
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    seat = plugin_copy.probe_seat("ears492count")
    (stubs / "ps").write_text(
        "#!/bin/sh\n"
        f'echo "  4242 00:01 /x/bin/heard-first --state /anywhere/heard.state --rule cut:{seat} --rule turn"\n'
        f'echo "  4243 00:01 /x/bin/heard-first --state {fresh(h, seat)}/heard.state --rule cut:other"\n')
    (stubs / "ps").chmod(0o755)
    r = subprocess.run(["bash", str(ROOT / "skills/ears/scripts/ears-count"), seat],
                       capture_output=True, text=True, timeout=30,
                       env={"HOME": str(h), "PATH": os.pathsep.join([str(stubs), "/usr/bin", "/bin"])})
    assert r.returncode == 0 and r.stdout.strip() == "1", r.stderr
    assert tree(h) == [str(h)] + sorted(str(p) for p in h.rglob("*"))


# ---- heard-first: the seats a line may be opened to ------------------------------------------

T = datetime(2030, 6, 9, 8, 0).astimezone()
DAY = "heard_2030-06-09.jsonl"


def _heard_first_reply(tmp_path, h, env_extra) -> int:
    """walter spoke; the user's next turn opens "Hey zed, ...". It is NOT walter's reply when
    heard-first knows zed is a seat (124), and it is (0) when it does not."""
    at = lambda s: (T + timedelta(seconds=s)).isoformat()
    root = tmp_path / "vm"
    logs = root / "logs" / "conversations"
    logs.mkdir(parents=True, exist_ok=True)
    recs = [{"ts": at(0), "kind": "saying", "utt": "u1", "agent": "walter", "source": "mouth",
             "text": "I'll write the note now."},
            {"ts": at(5), "kind": "said", "utt": "u1", "agent": "walter", "source": "mouth"},
            {"ts": at(30), "kind": "turn", "text": f"Hey {SEAT}, what is next?", "source": "mic",
             "t0": 100.0, "t1": 102.0}]
    (logs / DAY).write_text("".join(json.dumps(r) + "\n" for r in recs))
    state = tmp_path / "walter.state"
    state.write_text(json.dumps({"offsets": {str(logs / DAY): 0}}))
    env = {"VOICEMODE_BASE_DIR": str(root), "HOME": str(h),
           "PATH": os.pathsep.join([str(h.nobin), "/usr/bin", "/bin"]), **env_extra}
    p = subprocess.run([sys.executable, str(BIN / "heard-first"), "--rule", "reply:walter",
                        "--state", str(state), "--timeout", "1"],
                       capture_output=True, text=True, timeout=30, env=env)
    return p.returncode


def test_heard_first_knows_no_seat_from_the_decoy_while_tools_answers(tmp_path, h):
    decoy(h)
    fake_tools.session_bin(h.nobin, agent=tmp_path / "tools-root" / "agent")
    assert _heard_first_reply(tmp_path, h, {}) == 0


@pytest.mark.parametrize("how", ["session path", "sessiontools", "voice seats"])
def test_heard_first_knows_the_seats_tools_and_voice_list(how, tmp_path, h):
    root = tmp_path / "tools-root"
    extra = {}
    if how == "sessiontools":
        extra["PYTHONPATH"] = str(fake_tools.sessiontools_pkg(tmp_path / "pkg", root))
        state_file(root / "agent" / "home" / SEAT, "TOOLS")
    elif how == "session path":
        fake_tools.session_bin(h.nobin, agent=root / "agent")
        state_file(root / "agent" / "home" / SEAT, "TOOLS")
    else:
        state_file(fresh(h), "VOICE")
    assert _heard_first_reply(tmp_path, h, extra) == 124


def test_heard_first_with_no_tools_reads_rung_c(tmp_path, h):
    state_file(h / home.TOOLS_OLD["agent"] / "home" / SEAT, "OLD")
    assert _heard_first_reply(tmp_path, h, {}) == 124


# ---- the scan: tools' and mail's folders are named only in TOOLS_OLD ---------------------------

SCANNED = ("src", "bin", "keeper", "shadow", "skills/ears/scripts", "speakers", "view", "bench",
           "launchd", "settings")
#: tools' folders under HOME and the old mail folder, in any spelling code would write them:
#: ``~/.session/...``, ``".session"`` in a join, ``.session/`` in a path, and ``.sessionmail``.
LITERAL = re.compile(r"~/\.session(?![A-Za-z0-9_-])|\.session(?=[/\"'])|\.sessionmail")


def _code():
    for d in SCANNED:
        for p in sorted((ROOT / d).rglob("*")):
            if p.is_file() and not p.is_symlink() and "__pycache__" not in p.parts \
                    and ".egg-info" not in str(p) and p.suffix not in (".md", ".onnx", ".wav"):
                yield p


def test_no_tools_or_mail_folder_literal_outside_tools_old():
    """'.session/' and '~/.sessionmail' appear in no voice code except home.py's TOOLS_OLD
    (README criterion; design.md D5). docs/ and the READMEs are prose (do-005's)."""
    bad = []
    for p in _code():
        rel = p.relative_to(ROOT).as_posix()
        try:
            text = p.read_text()
        except UnicodeDecodeError:
            continue
        if rel == "src/ears/home.py":
            start = text.index("TOOLS_OLD = {")
            text = text[:start] + text[text.index("}", start) + 1:]
        bad += [f"{rel}:{i}: {ln.strip()}" for i, ln in enumerate(text.splitlines(), 1)
                if LITERAL.search(ln)]
    assert bad == []


def test_the_scan_sees_each_spelling():
    for line in ('home="$HOME/.session/agents/home/$seat"', 'home(".session", "agents")',
                 'glob.glob(os.path.expanduser("~/.session/agent/home/*/heard.state"))',
                 'LEGACY_SESSIONS = "~/.sessionmail/sessions"', "~/.session agents"):
        assert LITERAL.search(line), line
    for line in ("self.session = session", "a.session,", "~/.sessionkit/voice/state",
                 "heard.session_dir(x)"):
        assert not LITERAL.search(line), line


def test_tools_old_is_the_three_folders_and_cites_their_deletion():
    assert home.TOOLS_OLD == {"agent": ".session/agent", "agents": ".session/agents",
                              "sessions": ".session/sessions"}
    # Their deletion is carded (EARS-512); a shipped file names no tracker id (the export
    # guard), so home.py says WHEN they go, in words.
    assert "once every host's tools has `session path`" in (ROOT / "src/ears/home.py").read_text()
