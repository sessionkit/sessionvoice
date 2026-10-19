"""EARS-202 G2: every launch agent in launchd/ is a template rendered from the user's own
HOME and install paths by bin/render-plist; none carries this machine's paths. The
renderer only renders: it never calls launchctl, and it writes only stdout or -o DIR."""
import os, plistlib, re, subprocess, sys
import xml.dom.minidom
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

from ears.home import SETTINGS
from tests import _scanlist

ROOT = Path(__file__).resolve().parents[1]
LAUNCHD = ROOT / "launchd"
RENDER = ROOT / "bin" / "render-plist"
TEMPLATES = sorted(LAUNCHD.glob("*.plist.in"))
OURS = ("/Users/",)  # any macOS home spelt out
# The only dot-folders under the home a job may name: the kit's own and the XDG ones. An
# agent's own dot-folder is a machine path (EARS-406: a generic shape, no list needed).
HOME_DOTS = {".cache", ".local", ".sessionkit"}


def home_dots(text):
    """The dot-folders under the home (@HOME@/.x or ~/.x) that text names, beyond HOME_DOTS."""
    return sorted(set(re.findall(r"(?:@HOME@|~)/(\.[^/\s<\"']+)", text)) - HOME_DOTS)


def run(*args, env=None, **kw):
    return subprocess.run([sys.executable, str(RENDER), *map(str, args)],
                          capture_output=True, text=True, env=env, timeout=30, **kw)


@pytest.fixture
def fake(tmp_path):
    """A machine that is not ours: HOME and every install path under tmp_path, and no
    placeholder value taken from the real environment."""
    home = tmp_path / "home" / "jim"
    v = {"HOME": home, "EARS_REPO": home / "src" / "ears", "PYTHON": home / "src/ears/.venv/bin/python",
         "BASH": "/opt/homebrew/bin/bash", "RULES": home / SETTINGS / "wake.json",
         "SPEAKER_PY": home / "venv/bin/python",
         "AGENT_REFS": "alice", "TALK": home / ".local/bin/talk"}
    env = {k: os.environ[k] for k in ("PATH", "TMPDIR", "LANG") if k in os.environ}
    env.update({k: str(x) for k, x in v.items()})
    return env


def test_all_six_are_templates():
    """No checked-in machine plist: launchd/ holds templates only (EARS-242 added talk)."""
    files = sorted(p.name for p in LAUNCHD.iterdir())
    assert files and all(f.endswith(".plist.in") for f in files), files
    assert len(TEMPLATES) == 6


@pytest.mark.parametrize("t", TEMPLATES, ids=lambda p: p.name)
def test_template_carries_no_machine_path(t):
    text = t.read_text()
    for s in OURS:
        assert s not in text, f"{t.name} names {s}"
    assert home_dots(text) == [], t.name
    assert "sed -e" not in text, "the header should point at bin/render-plist"
    assert "bin/render-plist" in text


@pytest.mark.parametrize("t", TEMPLATES, ids=lambda p: p.name)
def test_template_renders_to_a_plist(t, fake, tmp_path):
    out = tmp_path / "out"
    p = run("-o", out, t, env=fake)
    assert p.returncode == 0, p.stderr
    dest = out / t.name[: -len(".in")]
    assert p.stdout.strip() == str(dest)
    text = dest.read_text()
    assert not re.search(r"@[A-Z][A-Z0-9_]*@", re.sub(r"<!--.*?-->", "", text, flags=re.S))
    xml.dom.minidom.parseString(text.encode())  # strict: no double hyphen inside a comment
    pl = plistlib.loads(text.encode())
    bare = text.replace(fake["HOME"], "@HOME@")  # the fake HOME may itself sit under /Users/
    for s in OURS:
        assert s not in bare, f"{t.name} rendered {s}"
    assert home_dots(bare) == [], t.name
    # the Label is the template's, unchanged
    assert pl["Label"] == t.name[: -len(".plist.in")]
    assert pl["EnvironmentVariables"]["HOME"] == fake["HOME"]
    assert pl["StandardOutPath"].startswith(fake["HOME"] + "/Library/Logs/ears/")  # as EARS_LOG_DIR
    # stdout gives the same bytes
    assert run(t, env=fake).stdout == text


@pytest.mark.parametrize("name", ["dev.sessionkit.voice.ears-sounds", "dev.sessionkit.voice.mouth-sounds"])
def test_sounds_run_the_repo_keeper_on_the_repo_venv(name, fake):
    pl = plistlib.loads(run(LAUNCHD / f"{name}.plist.in", env=fake).stdout.encode())
    repo = fake["EARS_REPO"]
    script = name.split(".")[-1]
    assert pl["ProgramArguments"] == [fake["PYTHON"], f"{repo}/keeper/{script}.py"]
    assert pl["WorkingDirectory"] == f"{repo}/keeper"
    assert not any(k.startswith("MOUTH_SOUNDS_") for k in pl["EnvironmentVariables"])


def test_defaults_come_from_home_and_this_repo(tmp_path):
    """No flags: HOME is $HOME, EARS_REPO is the repo the renderer sits in, PYTHON is
    that repo's .venv."""
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path / "jim")}
    p = run(LAUNCHD / "dev.sessionkit.voice.ears-sounds.plist.in", env=env)
    assert p.returncode == 0, p.stderr
    pl = plistlib.loads(p.stdout.encode())
    assert pl["ProgramArguments"] == [f"{ROOT}/.venv/bin/python", f"{ROOT}/keeper/ears-sounds.py"]
    assert pl["EnvironmentVariables"]["HOME"] == str(tmp_path / "jim")
    p = run(LAUNCHD / "dev.sessionkit.voice.ears-mail.plist.in", env=env)
    assert p.returncode == 0, p.stderr
    pl = plistlib.loads(p.stdout.encode())
    args = pl["ProgramArguments"]
    # EARS-234: the rules are the USER file, never the repo's seatless default.
    assert args[args.index("--rules") + 1] == str(tmp_path / "jim" / SETTINGS / "wake.json")
    # EARS-381 (rca-001 N3): ears-mail sends by `message`, a uv tool in ~/.local/bin;
    # the carrier is never copied into the plist (N4, ruled (b))
    env = pl["EnvironmentVariables"]
    assert str(tmp_path / "jim/.local/bin") in env["PATH"].split(":")
    assert "SESSIONMAIL_SENDMAIL" not in env and "MAGICDNS_SUFFIX" not in env


def test_set_beats_env_beats_default(fake):
    t = LAUNCHD / "dev.sessionkit.voice.ears-sounds.plist.in"
    pl = plistlib.loads(run("--set", "PYTHON=/usr/bin/python3", t, env=fake).stdout.encode())
    assert pl["ProgramArguments"][0] == "/usr/bin/python3"
    pl = plistlib.loads(run(t, env=fake).stdout.encode())
    assert pl["ProgramArguments"][0] == fake["PYTHON"]


def test_values_are_xml_escaped(fake):
    p = run("--set", "HOME=/Users/a&b", LAUNCHD / "dev.sessionkit.voice.ears-sounds.plist.in", env=fake)
    assert p.returncode == 0, p.stderr
    assert plistlib.loads(p.stdout.encode())["EnvironmentVariables"]["HOME"] == "/Users/a&b"


def test_a_placeholder_without_a_value_is_refused(fake, tmp_path):
    env = {k: v for k, v in fake.items() if k != "SPEAKER_PY"}
    p = run("-o", tmp_path / "out", LAUNCHD / "dev.sessionkit.voice.ears-speakers.plist.in", env=env)
    assert p.returncode == 2 and "@SPEAKER_PY@" in p.stderr
    assert not (tmp_path / "out").exists()


def test_the_ears_keeper_needs_no_voicemode_checkout(fake):
    """EARS-294 T3 (RT-09): the keeper's plist no longer carries EARS_W (the VoiceMode checkout
    voice_mode.listen ran from). It renders with no value but the defaults, and sets no EARS_W."""
    env = {k: v for k, v in fake.items() if k not in ("SPEAKER_PY", "AGENT_REFS", "TALK")}
    p = run(LAUNCHD / "dev.sessionkit.voice.ears-keeper.plist.in", env=env)
    assert p.returncode == 0, p.stderr
    pl = plistlib.loads(p.stdout.encode())
    assert "EARS_W" not in pl["EnvironmentVariables"]
    assert "@EARS_W@" not in (LAUNCHD / "dev.sessionkit.voice.ears-keeper.plist.in").read_text()


def test_talk_runs_the_talk_it_is_given(fake, tmp_path):
    """TALK has no default, even with a talk on PATH (macOS ships an unrelated /usr/bin/talk):
    unset, the render is refused; set, it is the job's program and nothing else moves."""
    t = LAUNCHD / "dev.sessionkit.voice.talk.plist.in"
    standin = tmp_path / "bin" / "talk"
    standin.parent.mkdir()
    standin.write_text("#!/bin/sh\nexit 0\n")
    standin.chmod(0o755)
    env = {k: v for k, v in fake.items() if k != "TALK"}
    env["PATH"] = f"{standin.parent}:{env.get('PATH', '/usr/bin:/bin')}"
    p = run(t, env=env)
    assert p.returncode == 2 and "@TALK@" in p.stderr and not p.stdout
    pl = plistlib.loads(run(t, env=fake).stdout.encode())
    assert pl["ProgramArguments"][0] == fake["TALK"]
    assert pl["ProgramArguments"][1:] == ["serve", "--host", "127.0.0.1", "--port", "8780"]
    assert pl["WorkingDirectory"] == fake["HOME"]
    log = fake["HOME"] + "/Library/Logs/ears/talk.log"
    assert pl["StandardOutPath"] == pl["StandardErrorPath"] == log


def test_refuses_what_it_should(fake, tmp_path):
    assert run(*TEMPLATES[:2], env=fake).returncode == 2  # several to stdout
    bad = tmp_path / "x.plist"
    bad.write_text("<plist/>")
    assert run(bad, env=fake).returncode == 2  # not a template
    broken = tmp_path / "x.plist.in"
    broken.write_text("<plist><dict><key>Label</key></plist>")
    p = run(broken, env=fake)
    assert p.returncode == 2 and "not a plist" in p.stderr
    # an empty --set value is refused, not silently replaced by the env or a default
    p = run("--set", "PYTHON=", LAUNCHD / "dev.sessionkit.voice.ears-sounds.plist.in", env=fake)
    assert p.returncode == 2 and "PYTHON=" in p.stderr and not p.stdout


def test_never_loads_and_never_writes_launch_agents():
    """It renders only: no launchctl, and no LaunchAgents path it could write by default."""
    src = RENDER.read_text()
    code = re.sub(r'"""[\s\S]*?"""', "", src)
    assert "launchctl" not in code and "LaunchAgents" not in code
    assert "subprocess" not in code and "os.system" not in code


# EARS-299: "--" is illegal inside an XML comment, and a scratch checkout or venv can sit
# under a path that holds one (/private/tmp/claude-501/-Users-alice--work/...). The headers
# name placeholders in prose, so a renderer that filled comments too wrote a plist that
# plistlib refuses as not well-formed.
DASHDASH = "-Users-alice--work"


@pytest.fixture
def dashdash(fake, tmp_path):
    """The fake machine again, with every path under a directory whose name holds "--"."""
    home = tmp_path / DASHDASH / "home--jim"
    repo = home / "src" / "ears--x"
    v = {"HOME": home, "EARS_REPO": repo, "PYTHON": home / "venv--y/bin/python",
         "RULES": home / SETTINGS / "wake.json",
         "SPEAKER_PY": home / "venv--y/bin/python", "TALK": home / ".local/bin/talk"}
    env = dict(fake)
    env.update({k: str(x) for k, x in v.items()})
    assert all("--" in env[k] for k in v)
    return env


def comments(text):
    return re.findall(r"<!--.*?-->", text, flags=re.S)


@pytest.mark.parametrize("t", TEMPLATES, ids=lambda p: p.name)
def test_a_dashdash_path_still_renders_a_plist(t, dashdash, tmp_path):
    """Every template, with the repo path AND the venv/python path holding "--", renders
    well-formed: the paths fill the plist body and never enter a comment."""
    p = run("-o", tmp_path / "out", t, env=dashdash)
    assert p.returncode == 0, p.stderr
    text = (tmp_path / "out" / t.name[: -len(".in")]).read_text()
    xml.dom.minidom.parseString(text.encode())  # strict: no "--" inside a comment
    pl = plistlib.loads(text.encode())
    assert comments(text) == comments(t.read_text())  # headers are left as written
    assert not re.search(r"@[A-Z][A-Z0-9_]*@", re.sub(r"<!--.*?-->", "", text, flags=re.S))
    assert pl["EnvironmentVariables"]["HOME"] == dashdash["HOME"]
    args = pl["ProgramArguments"]
    if "@PYTHON@" in t.read_text():
        assert args[0] == dashdash["PYTHON"]
        assert args[1].startswith(dashdash["EARS_REPO"] + "/keeper/")


def test_a_dashdash_checkout_renders_on_its_defaults(tmp_path):
    """The renderer run from a checkout under a "--" path, with nothing set: EARS_REPO and
    PYTHON come from where it sits, as they do in a scratch clone (the case that found it)."""
    root = tmp_path / DASHDASH / "ears"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "render-plist").write_text(RENDER.read_text())
    (root / "launchd").mkdir()
    for t in TEMPLATES:
        (root / "launchd" / t.name).write_text(t.read_text())
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path / DASHDASH / "jim")}
    for name in ("dev.sessionkit.voice.ears-sounds", "dev.sessionkit.voice.mouth-sounds",
                 "dev.sessionkit.voice.ears-mail"):
        p = subprocess.run([sys.executable, str(root / "bin" / "render-plist"),
                            str(root / "launchd" / f"{name}.plist.in")],
                           capture_output=True, text=True, env=env, timeout=30)
        assert p.returncode == 0, p.stderr
        xml.dom.minidom.parseString(p.stdout.encode())
        args = plistlib.loads(p.stdout.encode())["ProgramArguments"]
        assert args[:2] == [f"{root}/.venv/bin/python", f"{root}/keeper/{name.split('.')[-1]}.py"]


def test_a_placeholder_named_only_in_a_comment_is_not_demanded(fake, tmp_path):
    """Comments are prose: @NOPE@ there needs no value and stays as written, while the
    same name in the body is still refused when unset."""
    t = tmp_path / "x.plist.in"
    t.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0">\n'
                 "<!-- @NOPE@ and @HOME@ are explained here -->\n"
                 "<dict><key>Label</key><string>x</string>"
                 "<key>HOME</key><string>@HOME@</string></dict>\n</plist>\n")
    p = run(t, env=fake)
    assert p.returncode == 0, p.stderr
    assert "<!-- @NOPE@ and @HOME@ are explained here -->" in p.stdout
    assert plistlib.loads(p.stdout.encode())["HOME"] == fake["HOME"]
    t.write_text(t.read_text().replace("<string>x</string>", "<string>@NOPE@</string>"))
    p = run(t, env=fake)
    assert p.returncode == 2 and "@NOPE@" in p.stderr


# EARS-388: the templates whose job shells out to sessionmail's `message` (ears-mail's turns,
# the keeper's notices) carry where it lives on PATH: uv's ~/.local/bin, then Homebrew's two
# prefixes, as the keeper's and talk's do. ears-mail's had neither Homebrew prefix.
MESSAGE_CALLERS = ("dev.sessionkit.voice.ears-mail.plist.in", "dev.sessionkit.voice.ears-keeper.plist.in")


@pytest.mark.parametrize("name", MESSAGE_CALLERS)
def test_a_message_callers_rendered_path_finds_message_where_it_is_installed(name, fake):
    p = run(LAUNCHD / name, env=fake)
    assert p.returncode == 0, p.stderr
    path = plistlib.loads(p.stdout.encode())["EnvironmentVariables"]["PATH"].split(":")
    for d in (f"{fake['HOME']}/.local/bin", "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"):
        assert d in path, f"{name}: {d} not on PATH {path}"
    assert path.index(f"{fake['HOME']}/.local/bin") < path.index("/usr/bin")


def test_the_message_callers_are_every_template_that_mails():
    """A template whose job sends by `message` is in MESSAGE_CALLERS: ears-mail runs
    keeper/ears-mail.py, the keeper keeper/ears-keeper.sh (its mail_me)."""
    mails = {t.name for t in TEMPLATES if re.search(r"keeper/ears-(mail\.py|keeper\.sh)",
                                                    re.sub(r"<!--.*?-->", "", t.read_text(), flags=re.S))}
    assert mails == set(MESSAGE_CALLERS)


# EARS-406: no word of the private scan list, in a template or in what it renders to. Every
# row, but for the rows a template's header matches legitimately, for that template alone
# (measured): the tailnet proxy the talk job documents. A row is left out by WHAT IT MATCHES,
# the neutral sample text below, never by its name, so a list that renames its rows leaves
# the same rows out. The agents' names are ruling rows, not P0, so a P0-only scan would miss
# the very names this guard is for.
TEMPLATE_LEGIT = {
    "dev.sessionkit.voice.talk.plist.in": ("tailscale serve", "machine.example.ts.net"),
}


def template_rows(t):
    """The rows t's guard scans with: every row but those t's own TEMPLATE_LEGIT samples
    match. Its FILE NAME is scanned too: that name is its Label (pinned by
    test_template_renders_to_a_plist), and since EARS-407 every Label is
    dev.sessionkit.voice.<job>, so no row is left out for it."""
    return _scanlist.rows(legit=TEMPLATE_LEGIT.get(t.name, ()))


def test_template_legit_names_real_templates():
    """A renamed template drops its TEMPLATE_LEGIT entry loudly, not into a stale key, and
    every sample is text its template really carries: a sample is the subject's own neutral
    text, never a word brought in to switch a row off."""
    assert set(TEMPLATE_LEGIT) <= {t.name for t in TEMPLATES}
    missing = {name: [s for s in samples if s not in (LAUNCHD / name).read_text()]
               for name, samples in TEMPLATE_LEGIT.items()}
    assert {k: v for k, v in missing.items() if v} == {}


def test_home_dots_catches_an_agent_dot_folder():
    text = "@HOME@/.local/bin @HOME@/.sessionkit/voice ~/.cache/x @HOME@/.vera/log ~/.ivy/call-ears"
    assert home_dots(text) == [".ivy", ".vera"]


def rendered(t, env):
    """What t renders to, with the fake HOME put back as @HOME@ (it sits under pytest's tmp dir,
    whose path holds the user's own name)."""
    p = run(t, env=env)
    if p.returncode:
        pytest.fail(f"{t.name} did not render", pytrace=False)   # never its output: it may hold a planted word
    return p.stdout.replace(env["HOME"], "@HOME@")


@pytest.mark.parametrize("t", TEMPLATES, ids=lambda p: p.name)
def test_template_names_no_private_word(t, fake):
    rows = template_rows(t)
    found = _scanlist.hits(t.read_text(), rows)
    assert found == [], f"{t.name} holds a word of row(s) {found}"
    found = _scanlist.hits(rendered(t, fake), rows)
    assert found == [], f"{t.name} renders a word of row(s) {found}"


def test_a_private_word_planted_in_a_template_is_caught(fake, tmp_path):
    """The positive control: each word planted, from every row of the list, as a value in a
    copy of a template that leaves no row out, is a hit in the template and in its render."""
    t = LAUNCHD / "dev.sessionkit.voice.ears-sounds.plist.in"
    rows = template_rows(t)
    assert len(rows) == len(_scanlist.rows()), "the control's template leaves a row out"
    missed = []
    for i, (name, word) in enumerate(_scanlist.controls(rows)):
        planted = tmp_path / str(i) / t.name
        planted.parent.mkdir()
        planted.write_text(t.read_text().replace(
            "<key>Label</key>", f"<key>Planted</key><string>{escape(word)}</string><key>Label</key>", 1))
        if name not in _scanlist.hits(planted.read_text(), rows) \
                or name not in _scanlist.hits(rendered(planted, fake), rows):
            missed.append(name)
    assert missed == [], f"the scan missed a word planted from row(s) {missed}"
