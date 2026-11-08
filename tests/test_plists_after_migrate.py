"""The plists and the status row around a migrate (design.md D8 half 2, tests P1-P4).

- P1: each of the 6 ``launchd/`` templates rendered for a FRESH HOME names no old state path,
  and ears-mail's ``--state`` is ``<root>/ears-mail.state.json``.
- P2: a render for a LIVE HOME (an old root adopted in place, strays at their old paths)
  names the old path while it is real, and writes nothing.
- P3: on a HOME migrated by ``ears home migrate`` (an S host and a V host, from
  ``tests/test_migrate.py``'s fixtures), ``bin/render-plist`` over all 6 templates and
  ``services.spec()`` over all 9 jobs name no old state path; the speakers job's
  ``SPEAKER_HOME``, working folder and ``SPEAKER_LOGDIR`` are under the new root, and
  ``speaker_py()`` is unchanged.
- P4: ``ears services status`` on an adopted host shows the ``state`` row naming
  ``ears home migrate``, and no such row after a migrate.

The re-render and the re-install are each host's own wave-4 step, never part of a merge.
Every test here runs on a fake HOME under tmp_path. The migrate runs only through
``ears.migrate.run`` on a Host the ``mk`` fixture builds (a fake launchctl that answers
``print`` only, a fake process list). Nothing is installed or loaded, and launchctl is never
run for real.
"""
from __future__ import annotations

import os
import plistlib
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from ears import home as H, migrate as M, services
from test_migrate import mk, run, s_host, strays, tree, v_host  # noqa: F401  (mk: a fixture)
from test_nothing_born_outside import TEMPLATES, _env
from test_services_install_checks import fence  # noqa: F401  (a fixture)
from test_services_speech import sx  # noqa: F401  (a fixture)

ROOT = Path(__file__).resolve().parents[1]
SV, VM = H.OLD_ROOTS
MAIL = "dev.sessionkit.voice.ears-mail.plist.in"
#: Every old state path under HOME. The README names the first three for the render; the rest
#: are the other strays' old homes and VoiceMode's root, which no job may name after a migrate
#: either.
OLD = (".cache/ears/ears-mail.state.json", ".local/state/ears", SV,
       ".local/state/audio", ".local/share/ears/speakers", VM)
#: The one old folder a job may still name, by ruling: the speakers side-car's venv (venvs stay).
VENV = H.SPEAKERS_VENV


def _fake(h: Path, tmp_path: Path) -> dict:
    """The placeholders with no default, filled as a hand install would."""
    return {"SPEAKER_PY": str(h / VENV / "bin/python"), "AGENT_REFS": "alice",
            "TALK": str(tmp_path / "bin/talk")}


def _render(t: Path, h: Path, tmp_path: Path, **extra) -> str:
    """bin/render-plist on template T for HOME H, to stdout (it writes nothing else)."""
    p = subprocess.run([sys.executable, "-B", str(ROOT / "bin/render-plist"), str(t)],
                       env=_env(h, **_fake(h, tmp_path), **extra), capture_output=True,
                       text=True, timeout=60)
    assert p.returncode == 0, (t.name, p.stderr)
    plistlib.loads(p.stdout.encode())
    return p.stdout


def _named(text: str, h: Path) -> list[str]:
    """Each old state path TEXT names: absolute under H, or written as ``~/...``. The speakers
    venv is not one."""
    out = []
    for old in OLD:
        for form in (str(h / old), f"~/{old}"):
            rest = text.replace(str(h / VENV), "").replace(f"~/{VENV}", "")
            if form in rest:
                out.append(form)
    return out


def _state_flag(text: str) -> str:
    a = plistlib.loads(text.encode())["ProgramArguments"]
    return a[a.index("--state") + 1]


def _values(d: dict) -> list[str]:
    """Every string a job's plist carries: environment, arguments, working folder, logs."""
    vals = list(d["EnvironmentVariables"].values()) + [str(a) for a in d["ProgramArguments"]]
    vals += [d.get("WorkingDirectory", ""), d.get("StandardOutPath", ""),
             d.get("StandardErrorPath", "")]
    return [v for a in vals if isinstance(a, str) for v in [a, *shlex.split(a)]]


def test_there_are_six_templates_and_nine_jobs():
    """A check over fewer writers proves less."""
    assert len(TEMPLATES) == 6 and (ROOT / "launchd" / MAIL) in TEMPLATES
    assert len(services.jobs(services.ALL)) == 9


# --- P1 a fresh HOME -------------------------------------------------------------------------

@pytest.mark.parametrize("t", TEMPLATES, ids=lambda t: t.name.split(".")[3])
def test_p1_a_fresh_homes_render_names_no_old_state_path(t, tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    text = _render(t, h, tmp_path)
    assert _named(text, h) == []
    if t.name == MAIL:
        assert _state_flag(text) == str(h / H.NEW / "ears-mail.state.json")
    assert list(h.iterdir()) == [], "a render writes nothing under HOME"


def test_p1_a_root_override_in_the_shell_moves_no_render(tmp_path):
    """The jobs carry no root override, so a render made in a shell that has one names the
    same paths as one made without it."""
    h = tmp_path / "home"
    h.mkdir()
    for t in TEMPLATES:
        plain = _render(t, h, tmp_path)
        assert _render(t, h, tmp_path, SESSIONVOICE_HOME=str(tmp_path / "sv"),
                       VOICEMODE_BASE_DIR=str(tmp_path / "vm")) == plain, t.name


# --- P2 a live HOME --------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["s", "v"])
def test_p2_a_live_homes_render_names_the_old_path_while_it_is_real(mk, tmp_path, kind):
    (s_host if kind == "s" else v_host)(mk)
    strays(mk)
    before = tree(mk.home)
    text = _render(ROOT / "launchd" / MAIL, mk.home, tmp_path)
    assert _state_flag(text) == str(mk.home / ".cache/ears/ears-mail.state.json")
    assert tree(mk.home) == before and not os.path.lexists(mk.new), "the render writes nothing"


@pytest.mark.parametrize("kind", ["s", "v"])
def test_p2_with_no_old_state_file_the_render_names_the_adopted_root(mk, tmp_path, kind):
    """An old root adopted in place holds the items that have no old path of their own."""
    old = (s_host if kind == "s" else v_host)(mk)
    text = _render(ROOT / "launchd" / MAIL, mk.home, tmp_path)
    assert _state_flag(text) == str(old / "ears-mail.state.json")
    assert not os.path.lexists(mk.new)


# --- P3 a migrated HOME ----------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["s", "v"])
def test_p3_after_a_migrate_no_render_and_no_job_names_an_old_state_path(mk, fence, tmp_path,
                                                                        kind):
    (s_host if kind == "s" else v_host)(mk)
    strays(mk)
    py = services.speaker_py()
    code, doc = run(mk.host())
    assert code == 0, doc
    h, new = mk.home, mk.new
    # the six templates, as a hand install renders them again
    for t in TEMPLATES:
        text = _render(t, h, tmp_path)
        assert _named(text, h) == [], t.name
        if t.name == MAIL:
            assert _state_flag(text) == str(new / "ears-mail.state.json")
    # the nine jobs, as `ears services install` builds them again
    built = []
    for job in services.jobs(services.ALL):
        d = services.spec(job)
        built.append(job)
        bad = [v for v in _values(d) for o in _named(v, h)]
        assert bad == [], (job, bad)
    assert len(built) == 9
    d = services.spec("speakers")
    env = d["EnvironmentVariables"]
    assert env["SPEAKER_HOME"] == d["WorkingDirectory"] == str(new / "speakers")
    assert env["SPEAKER_LOGDIR"] == str(new / "logs/conversations")
    assert services.speaker_py() == py == h / VENV / "bin/python"
    assert env["SPEAKER_PY"] == str(py)
    assert _state_flag_spec(services.spec("mail")) == str(new / "ears-mail.state.json")


def _state_flag_spec(d: dict) -> str:
    a = d["ProgramArguments"]
    return a[a.index("--state") + 1]


def test_p3_before_the_migrate_the_same_host_renders_its_old_paths(mk, fence, tmp_path):
    """The twin: the same S host before its migrate. The jobs name the old paths, so the
    re-render after the migrate is what moves them (and a merge moves nothing)."""
    s_host(mk)
    strays(mk)
    h = mk.home
    assert _state_flag(_render(ROOT / "launchd" / MAIL, h, tmp_path)) == \
        str(h / ".cache/ears/ears-mail.state.json")
    d = services.spec("speakers")
    assert d["EnvironmentVariables"]["SPEAKER_HOME"] == str(h / ".local/share/ears/speakers")
    assert d["EnvironmentVariables"]["SPEAKER_LOGDIR"] == str(h / SV / "logs/conversations")


# --- P4 the status row -----------------------------------------------------------------------

def _migrate_rows(out: str) -> list[str]:
    return [ln for ln in out.splitlines() if ln.startswith("state  ") and "ears home migrate" in ln]


@pytest.mark.parametrize("kind", ["s", "v"])
def test_p4_status_names_the_migrate_on_an_adopted_host_and_not_after_it(mk, sx, capsys, kind):
    old = (s_host if kind == "s" else v_host)(mk)
    strays(mk)
    assert services.status() == 0
    rows = _migrate_rows(capsys.readouterr().out)
    assert len(rows) == 1, rows
    assert f"the state is in {old}, " in rows[0]
    for name in H.ITEMS:
        assert str(H.item_old(name, {}, mk.home)) in rows[0], name
    assert f"moves it to {mk.new} (this host's wave-4 step" in rows[0]
    code, doc = run(mk.host())
    assert code == 0, doc
    assert services.status() == 0
    out = capsys.readouterr().out
    assert _migrate_rows(out) == []
    assert f"state  {mk.new} (has the heard log)" in out


def test_p4_a_fresh_host_has_no_migrate_row(mk, sx, capsys):
    assert services.status() == 0
    out = capsys.readouterr().out
    assert _migrate_rows(out) == []
    assert f"state  {mk.new} (the default; no heard log yet)" in out


def test_p4_a_stray_alone_names_the_migrate(mk):
    """A host in the kit with one stray still at its old path: the migrate still has work."""
    old = mk.home / ".local/state/ears/call-tap"
    old.mkdir(parents=True)
    rows = H.status_lines({}, mk.home)
    assert rows[0] == f"state  {mk.new} (the default; no heard log yet)"
    assert rows[1:] == [f"state  the state is in {old}; `ears home migrate` moves it to "
                        f"{mk.new} (this host's wave-4 step, then `ears services install`)"]


@pytest.mark.parametrize("var", H.ENV)
def test_p4_a_root_override_has_no_migrate_row(mk, tmp_path, var):
    """The migrate refuses a root set by hand (refusal 1), so status does not point at it."""
    s_host(mk)
    strays(mk)
    assert H.still_old({var: str(tmp_path / "x")}, mk.home) == []
    rows = H.status_lines({var: str(tmp_path / "x")}, mk.home)
    assert not any("ears home migrate" in r for r in rows)


@pytest.mark.parametrize("kind", ["s", "v", "strays"])
def test_p4_the_row_names_what_the_migrate_plans(mk, kind):
    """still_old() and the migrate's plan cover the same old paths: each planned entry is one
    of the row's paths or under one (the speakers home and VoiceMode's root move entry by
    entry), and each path in the row has an entry."""
    if kind != "strays":
        (s_host if kind == "s" else v_host)(mk)
    strays(mk)
    row = H.still_old({}, mk.home)
    src = [Path(e.src) for e in M.plan(mk.host()).entries]
    assert src and row
    under = lambda p, r: p == r or r in p.parents   # noqa: E731
    assert all(any(under(p, r) for r in row) for p in src), (row, src)
    assert all(any(under(p, r) for p in src) for r in row), (row, src)


# --- the install step, documented ------------------------------------------------------------

def _unreleased() -> str:
    """The newest CHANGELOG section that announces `ears home migrate`: [Unreleased] on a
    branch, the top versioned section at a release tip, whose stamp leaves [Unreleased]
    empty above it (the test_instrumental_docs pattern)."""
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    for body in re.split(r"^## \[", text, flags=re.M)[1:]:
        if "`ears home migrate`" in body:
            return "## [" + body
    raise AssertionError("no CHANGELOG section announces `ears home migrate`")


def test_the_install_step_is_documented_in_order():
    """docs/configuration.md gives the per-host steps in the order the migrate's report has
    them (design.md D7.7), and quotes the status row as home.status_lines() prints it."""
    doc = (ROOT / "docs/configuration.md").read_text(encoding="utf-8")
    sec = doc[doc.index("### Moving an old root: `ears home migrate`"):doc.index("## The ears")]
    order = ["`ears home migrate --dry-run`", "Stop the writers", "`ears home migrate` acts by "
             "default", "Re-arm the readers", "`ears services install`", "`bin/render-plist`"]
    at = [sec.index(x) for x in order]
    assert at == sorted(at), dict(zip(order, at))
    assert "No upgrade or merge runs it." in sec
    assert ("*the state is in `<old>`; `ears home migrate` moves it to\n"
            "`~/.sessionkit/voice/state`*") in sec
    logs = (ROOT / "docs/logs.md").read_text(encoding="utf-8")
    assert "[migrate]: configuration.md#moving-an-old-root-ears-home-migrate" in logs


def test_the_status_row_reads_as_the_docs_quote_it(mk):
    s_host(mk)
    row = H.status_lines({}, mk.home)[-1]
    assert row.startswith(f"state  the state is in {mk.sv}; `ears home migrate` moves it to "
                          f"{mk.new}")


def test_the_changelog_names_the_verb_and_the_per_host_install_step():
    u = _unreleased()
    assert "`ears home migrate`" in u and "acts by default" in u and "`--dry-run`" in u
    assert "The per-host install step after `ears home migrate`" in u
    assert "`bin/render-plist`" in u and "A merge or an\n  upgrade runs neither" in u


def test_the_migrates_help_and_report_name_the_hand_rendered_case(mk, capsys):
    assert M.main(["--help"], h=mk.host()) == 0
    help_ = capsys.readouterr().out
    assert "`ears services install`" in help_ and "bin/render-plist" in help_
    assert "no merge runs it" in help_
    s_host(mk)
    code, doc = run(mk.host())
    assert code == 0, doc
    step = next(x for x in doc["result"]["after"] if x.startswith("3. "))
    assert "`ears services install`" in step and "bin/render-plist" in step


def test_status_warns_for_an_install_written_plist_not_a_hand_render(mk, fence, tmp_path):
    """docs/configuration.md step 5: after a migrate, status warns while a plist that `ears
    services install` wrote still names the old --state, and gives no such warning for one
    rendered by hand from launchd/ (no MARKER): that one is rendered again by hand."""
    s_host(mk)
    strays(mk)
    text = _render(ROOT / "launchd" / MAIL, mk.home, tmp_path)
    old = str(mk.home / ".cache/ears/ears-mail.state.json")
    assert _state_flag(text) == old
    code, doc = run(mk.host())
    assert code == 0, doc
    plist = services.AGENTS / "dev.sessionkit.voice.ears-mail.plist"
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_text(text)
    assert services.installed_state_lines(["mail"]) == [], "a hand render is not checked"
    d = plistlib.loads(text.encode())
    d.setdefault("EnvironmentVariables", {})[services.MARKER[0]] = services.MARKER[1]
    plist.write_bytes(plistlib.dumps(d))
    rows = services.installed_state_lines(["mail"])
    assert len(rows) == 1 and old in rows[0], rows
    assert "voice now uses " + str(mk.new / "ears-mail.state.json") in rows[0]
