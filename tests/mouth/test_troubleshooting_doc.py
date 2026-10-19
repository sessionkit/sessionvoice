"""EARS-284: docs/troubleshooting.md's mouth section names real commands, flags,
fields, variables and lines, and keeps naming them as the code moves.

The doc is read at a live incident, by someone in a hurry: a flag that no longer
exists, or a field the said row stopped writing, costs minutes nobody has. Each
test pins one kind of claim the doc makes to the code at HEAD. Nothing here
plays a sound: the CLI is asked only for ``--help``.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from mouth.__main__ import main

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "troubleshooting.md"
SRC = ROOT / "src"


def doc() -> str:
    return DOC.read_text()


def mouth_section() -> str:
    m = re.search(r"^## Mouth: the voice chops\n(.*?)(?=^## |\Z)", doc(), re.S | re.M)
    assert m, "docs/troubleshooting.md has no '## Mouth: the voice chops' section"
    return m.group(1)


def help_text(capsys, *argv: str) -> str:
    with pytest.raises(SystemExit) as e:
        main([*argv, "--help"])
    assert e.value.code == 0
    return capsys.readouterr().out


def test_readme_links_the_doc():
    assert "docs/troubleshooting.md" in (ROOT / "README.md").read_text()


def test_the_three_causes_and_the_remedies_are_there():
    s = mouth_section()
    for heading in ("### First, read the line's own counts",
                    "### Cause 1: the Mac is starved",
                    "### Cause 2: a clone voice streams slower than real time",
                    "### Cause 3: the Bluetooth link is losing audio",
                    "### Music over HFP always sounds poor",
                    "### Cheapest live remedies, in order"):
        assert heading in s, heading
    for cause in re.split(r"^### Cause \d", s, flags=re.M)[1:]:
        for part in ("**Symptom.**", "**Check", "**Remedy.**"):
            assert part in cause, f"a cause lacks {part}"


def test_every_mouth_subcommand_and_flag_exists(capsys):
    """`mouth <sub> [--flag ...]` as the doc writes it: the sub parses, each flag is in its help."""
    s = mouth_section()
    uses = re.findall(r"`mouth ([^`]+)`", s)                       # inline code
    uses += re.findall(r"^ {4,}(?:\S.*\| )?mouth (.+)$", s, re.M)   # a code block's line
    assert uses, "the doc names no mouth command"
    seen = set()
    for use in uses:
        words = use.split()
        sub, flags = words[0], [w for w in words[1:] if w.startswith("--")]
        out = help_text(capsys, sub)
        for f in flags:
            assert f in out, f"mouth {sub} has no {f}"
        seen.add(sub)
    assert {"stop", "audio", "status", "retract", "say", "voices"} <= seen, seen


def test_the_said_fields_the_doc_reads_are_written():
    """Each field the doc's jq reads, and each count it explains, is on the said row."""
    player = (SRC / "mouth" / "player.py").read_text()
    m = re.search(r"jq -c '(.*?)'", mouth_section(), re.S)
    assert m, "no jq line in the doc"
    fields = re.findall(r"\b([a-z_]+)\b", m.group(1).split("|", 1)[1])
    for f in fields:
        if f in ("ts", "utt", "voice", "backend", "device"):   # the common fields: the writer's, or _common's
            continue
        assert f"{f}=" in player, f"said has no {f}"
    for f in ("underruns", "underrun_s", "device_gaps", "device_gap_s", "device_gap_max_s", "audio"):
        assert f"`{f}`" in mouth_section(), f
        assert f"{f}=" in player, f


def test_every_variable_the_doc_names_is_read_by_the_code():
    code = "\n".join(p.read_text() for p in SRC.rglob("*.py"))
    # the kit's own names (MOUTH_* since EARS-294 T5, SESSIONVOICE_HOME since T7) and any
    # old VOICEMODE_* one still named
    names = set(re.findall(r"\$((?:MOUTH|EARS|SESSIONVOICE|VOICEMODE)_[A-Z_]+)", mouth_section()))
    assert names
    for n in names:
        assert n in code, f"${n} is read nowhere in src/"


def test_every_source_path_and_line_cited_is_real():
    s = mouth_section()
    paths = set(re.findall(r"`(src/[\w/]+\.py)(?::(\d+))?`", s))
    assert paths
    for path, line in paths:
        f = ROOT / path
        assert f.is_file(), path
        if line:
            text = f.read_text().splitlines()[int(line) - 1]
            assert "streaming_interval" in text, f"{path}:{line} is not the streaming_interval line"


def test_the_kept_audio_note_carries_what_the_ratio_reads():
    audio = (SRC / "mouth" / "audio.py").read_text()
    for key in ("requested_ts", "kept_ts", "dur_s"):
        assert f'"{key}"' in audio, key


def test_a_general_stop_cuts_the_music_and_a_barge_in_does_not():
    """The doc's first remedy: `mouth stop` (and `--current`) cut every bed."""
    from mouth.bed import Bed

    bed = Bed.__new__(Bed)
    bed.utt, bed.requested_t = "b1", 100.0
    assert bed.hit_by({"t": 101.0, "reason": "stop", "flush": True})
    assert bed.hit_by({"t": 101.0, "reason": "stop", "flush": False})       # --current
    assert bed.hit_by({"t": 101.0, "reason": "retracted", "utt": "b1"})     # mouth retract UTT
    assert not bed.hit_by({"t": 101.0, "reason": "barge-in"})
