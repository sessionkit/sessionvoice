"""EARS-242 do-004: the launchd template for `talk serve`.

The render under test is the one the template's own header documents (its bin/render-plist
line, EARS-202 fix-009), run by bash in a temp HOME with its TALK= value swapped for a
stand-in `talk`, writing to a temp file instead of ~/Library/LaunchAgents. Nothing is
installed or loaded; launchd is never called. The generic checks (a template, no home path,
every placeholder filled, logs under Logs/ears) are tests/test_launchd_no_machine_paths.py's
and tests/test_launchd_templates.py's, which cover this file too.
"""
import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "launchd" / "dev.sessionkit.voice.talk.plist.in"
LABEL = "dev.sessionkit.voice.talk"


def header_render_command():
    """The header's render command, continuation lines joined, its target swapped for $OUT."""
    lines = TEMPLATE.read_text().splitlines()
    start = next(i for i, l in enumerate(lines) if l.strip().startswith("TALK="))
    cmd = []
    for l in lines[start:]:
        cmd.append(l.strip().rstrip("\\").strip())
        if not l.rstrip().endswith("\\"):
            break
    cmd = " ".join(cmd)
    target = f"> ~/Library/LaunchAgents/{LABEL}.plist"
    assert cmd.endswith(target), cmd
    return cmd[: -len(target)] + '> "$OUT"'


def render(tmp_path):
    home, bin_ = tmp_path / "home", tmp_path / "bin"
    home.mkdir()
    bin_.mkdir()
    talk = bin_ / "talk"
    talk.write_text("#!/bin/sh\nexit 0\n")
    talk.chmod(0o755)
    out = tmp_path / f"{LABEL}.plist"
    cmd = re.sub(r"^TALK=\S+", 'TALK="$STANDIN"', header_render_command())
    py = Path(sys.executable).parent  # bin/render-plist runs on `env python3`
    env = {"HOME": str(home), "PATH": f"{py}:/usr/bin:/bin", "OUT": str(out), "STANDIN": str(talk)}
    subprocess.run(["bash", "-c", cmd], cwd=REPO, env=env, check=True)
    return out.read_text(), home, talk


def test_header_render_gives_a_plist_with_no_placeholder_left(tmp_path):
    text, home, talk = render(tmp_path)
    assert "@" not in re.sub(r"<!--.*?-->", "", text, flags=re.S)
    plist = plistlib.loads(text.encode())          # the whole file, header comment included
    assert plist["Label"] == LABEL
    assert plist["ProgramArguments"][0] == str(talk)
    assert plist["EnvironmentVariables"]["HOME"] == str(home)
    assert plist["StandardOutPath"] == str(home) + "/Library/Logs/ears/talk.log"  # G21


def test_runs_talk_serve_on_loopback_only(tmp_path):
    plist = plistlib.loads(render(tmp_path)[0].encode())
    args = plist["ProgramArguments"][1:]
    assert args[0] == "serve"
    assert args[args.index("--host") + 1] == "127.0.0.1"
    assert plist["KeepAlive"] is True and plist["RunAtLoad"] is True


def test_every_placeholder_is_filled_by_the_header_line():
    """@HOME@ is the renderer's own default; every other placeholder the header line sets."""
    body = TEMPLATE.read_text()
    body = body[body.index("<plist"):]
    used = set(re.findall(r"@([A-Z_]+)@", body))
    cmd = header_render_command()
    assert "bin/render-plist" in cmd and "sed " not in cmd
    filled = set(re.findall(r"(?:^|\s)([A-Z_]+)=", cmd))
    assert used and used - {"HOME"} == filled == {"TALK"}


def test_no_user_specific_path():
    """No home directory and no dotdir but the kit's own: the label is pinned by the render
    test above, and names and hosts are release-scan's to find (its terms are not repeated
    here, so this test adds no scrub hit of its own)."""
    text = TEMPLATE.read_text()
    assert "/Users/" not in text
    dotdirs = set(re.findall(r"(?:~|@HOME@)/(\.[A-Za-z0-9_-]+)", text))
    assert dotdirs <= {".local"}, dotdirs
