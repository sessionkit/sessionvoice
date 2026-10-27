"""The instrumental, the split and the stems, named on their pages.

Every flag, verb, setting and variable this feature added is named on the page a
reader looks for it on, and each name is first checked against the code, so the
list here cannot outlive the thing it names:

- the verb and its flags, and ``mouth play``'s new flags: ``docs/mouth.md``, read
  from the CLI's own ``--help``;
- the setting: a row of ``docs/settings.md``'s ``mouth.json`` table, a key of the
  schema;
- the variables: rows of ``docs/configuration.md``'s variables table;
- the sidecar: ``docs/sidecar.md`` names the layout the reader walks and every key
  the writer puts in ``split.json``;
- the model: ``docs/models.md`` names the pin;
- the row fields: ``docs/logs.md``;
- the README's one line, the ears skill and the CHANGELOG's ``[Unreleased]``.
"""
from __future__ import annotations

import ast
import contextlib
import io
import json
import re
from pathlib import Path

import pytest

from mouth import separator, sidecar, split as _split, voices
from mouth.__main__ import main

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _help(*argv: str) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), pytest.raises(SystemExit) as e:
        main([*argv, "--help"])
    assert e.value.code == 0
    return out.getvalue()


def _options(text: str) -> set[str]:
    return set(re.findall(r"(?<![\w-])--[a-z][a-z-]*", text))


def _rows(text: str) -> set[str]:
    """Every `code` name in a table row's first cell (a cell may name several)."""
    out = set()
    for line in text.splitlines():
        if line.startswith("| "):
            first = line.split("|")[1]
            out.update(re.findall(r"`([^`]+)`", first))
    return out


def _section(text: str, title: str) -> str:
    m = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert m, f"no '## {title}' section"
    return m.group(1)


# ---- the verb and the flags: docs/mouth.md ---------------------------------------------------

PLAY_FLAGS = ("--instrumental", "--no-instrumental", "--stem")
SPLIT_FLAGS = ("--install", "--dry-run", "--force")


def test_the_flags_are_the_clis():
    assert set(PLAY_FLAGS) <= _options(_help("play"))
    assert set(SPLIT_FLAGS) <= _options(_help("split"))
    assert "split" in _help()


@pytest.mark.parametrize("flag", PLAY_FLAGS)
def test_each_play_flag_is_on_the_mouth_page(flag):
    play = _section(_read("docs/mouth.md"), "Play music or a sound")
    assert f"`{flag}" in play, f"docs/mouth.md, 'Play music or a sound', never names {flag}"


def test_the_split_verb_and_its_flags_are_on_the_mouth_page():
    play = _section(_read("docs/mouth.md"), "Play music or a sound")
    assert "`mouth split FILE" in play
    for flag in SPLIT_FLAGS:
        assert f"`{flag}`" in play or f"mouth split {flag}" in play, f"{flag} is not named"


def test_the_play_section_leads_with_the_default():
    play = _section(_read("docs/mouth.md"), "Play music or a sound")
    lead = play.split("\n\n")[0]
    assert "instrumental" in lead and "--no-instrumental" in lead
    assert "(sidecar.md)" in lead


def test_every_refusal_of_instrumental_is_named():
    play = _section(_read("docs/mouth.md"), "Play music or a sound")
    bullet = play[play.index("**`--instrumental` makes it first**"):]
    bullet = bullet[:bullet.index("\n  - ")]
    for case in ("call device", "no separator", "no duration"):
        assert case in bullet, f"the --instrumental refusals never name '{case}'"


# ---- the setting: docs/settings.md -----------------------------------------------------------

def test_the_setting_is_a_schema_key_and_a_settings_row():
    schema = json.loads(_read("settings/mouth.schema.json"))["properties"]
    assert "music_instrumental" in schema and "music_instrumental" in voices.LINE_KEYS
    assert "music_instrumental" in _rows(_read("docs/settings.md"))


# ---- the variables: docs/configuration.md ----------------------------------------------------

VARIABLES = (voices.INSTRUMENTAL_ENV, _split.SEPARATOR_ENV, separator.HOME_ENV)


def test_the_variables_are_the_codes():
    assert VARIABLES == ("MOUTH_MUSIC_INSTRUMENTAL", "MOUTH_SEPARATOR", "MOUTH_SEPARATOR_HOME")


@pytest.mark.parametrize("name", VARIABLES)
def test_each_variable_is_a_configuration_row(name):
    assert name in _rows(_read("docs/configuration.md")), f"docs/configuration.md has no {name} row"


# ---- the sidecar contract: docs/sidecar.md ---------------------------------------------------

def _manifest_keys() -> set[str]:
    """The keys of the ``manifest`` dict that ``mouth split`` writes, read from its source."""
    tree = ast.parse((ROOT / "src/mouth/split.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict)
                and any(isinstance(t, ast.Name) and t.id == "manifest" for t in node.targets)):
            keys = {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
            for k, v in zip(node.value.keys, node.value.values):
                if isinstance(k, ast.Constant) and k.value == "source" and isinstance(v, ast.Dict):
                    keys |= {s.value for s in v.keys if isinstance(s, ast.Constant)}
            return keys
    raise AssertionError("src/mouth/split.py builds no manifest dict")


def test_the_sidecar_page_names_every_manifest_key():
    page = _read("docs/sidecar.md")
    keys = _manifest_keys()
    assert {"version", "source", "outputs", "verdict", "mtime_ns", "duration_s"} <= keys
    missing = sorted(k for k in keys if f"`{k}`" not in page)
    assert not missing, f"docs/sidecar.md never names manifest keys {missing}"
    assert f"`{sidecar.MANIFEST}`" in page and f"`{sidecar.MANIFEST_VERSION}`" in page


def test_the_sidecar_page_names_the_layout_the_reader_walks():
    page = _read("docs/sidecar.md")
    for name in (*sidecar.STEMS, *sidecar.INSTRUMENTAL_NAMES, *sidecar.EXTENSIONS):
        assert f"`{name}" in page, f"docs/sidecar.md never names {name}"
    for folder in (".<name>.d/", "<name>.d/", "<base>.d/", "htdemucs*/<base>/", "mlx-roformer/"):
        assert folder in page, f"docs/sidecar.md never names {folder}"
    assert f"{sidecar.SPEECH_GAP_DB:g} dB" in page and f"{sidecar.STALE_TOLERANCE_S:g} s" in page


def test_the_sidecar_page_is_linked_from_the_mouth_page():
    assert "(sidecar.md)" in _read("docs/mouth.md")


# ---- the model: docs/models.md ---------------------------------------------------------------

def test_the_models_page_names_the_pin_and_its_terms():
    page = _read("docs/models.md")
    entry = _section(page, "HTDemucs")
    m = separator.MODEL
    for fact in (m.file, m.repo, m.revision, m.sha256, f"{m.size:,}", m.klass, separator.INSTALL):
        assert fact in entry, f"docs/models.md's HTDemucs entry never names {fact}"
    assert "**Licence:**" in entry
    assert "[HTDemucs][htdemucs]" in page and "[htdemucs]: #htdemucs" in page


def test_every_page_states_the_codes_ceiling():
    """The auto-split ceiling is stated as the code's: the mouth page, the CHANGELOG's
    newest section with entries ([Unreleased], or after a release bump the release it
    became) and the models page's memory note never name another."""
    from mouth import autosplit
    minutes = f"{autosplit.CEILING_S / 60:g} min"
    heading, newest = _newest_changelog_entries(_read("CHANGELOG.md"))
    pages = {"docs/mouth.md": _read("docs/mouth.md"), f"CHANGELOG.md {heading}": newest,
             "docs/models.md HTDemucs": _section(_read("docs/models.md"), "HTDemucs")}
    pattern = re.compile(r"(?:file of|files? over|over|than) (\d+) min(?:utes)?\b")
    for where, text in pages.items():
        stated = set(pattern.findall(text.replace("\n  ", " ").replace("\n", " ")))
        assert stated == {minutes.split()[0]}, f"{where} states the ceiling as {stated}"


# ---- the row fields: docs/logs.md ------------------------------------------------------------

@pytest.mark.parametrize("field", ("source_file", "instrumental_from", "stem", "split"))
def test_each_row_field_is_on_the_logs_page(field):
    assert f"`{field}`" in _read("docs/logs.md")


# ---- the README, the skill, the CHANGELOG ----------------------------------------------------

def test_the_dependencies_page_lists_the_separator_as_optional_without_a_new_prerequisite():
    # The README's Requirements are the operating systems and a link to
    # docs/dependencies.md; the separator is a row of that page's Optional table.
    deps = _read("docs/dependencies.md")
    optional = deps.split("\n## Optional\n", 1)[1].split("\n## ", 1)[0]
    row = next((ln for ln in optional.splitlines() if ln.startswith("| The separator |")), None)
    assert row, "docs/dependencies.md's Optional table has no separator row"
    assert "mouth split --install" in row
    assert not re.search(r"homebrew|\bbrew\b|ffmpeg", row, re.I), (
        "the separator row must not make Homebrew or ffmpeg a prerequisite")
    head = deps.split("\n## ", 1)[0]
    assert "the separator" in head.split("**Optional:**", 1)[1].split("**Pending", 1)[0]


def test_the_ears_skill_says_to_relay_the_split_line():
    skill = _read("skills/ears/SKILL.md")
    for name in ("--no-instrumental", "mouth split --install", "--stem", "stderr"):
        assert name in skill, f"the ears skill never names {name}"


def test_the_changelog_unreleased_has_added_and_changed():
    """The newest CHANGELOG section with entries carries the split's Added and Changed:
    [Unreleased] until the release bump, then the release it was moved into."""
    heading, body = _newest_changelog_entries(_read("CHANGELOG.md"))
    added = _section_of(body, "Added", heading)
    changed = _section_of(body, "Changed", heading)
    for name in ("mouth split", "--install", "--stem", "music_instrumental",
                 "--instrumental", *VARIABLES):
        assert name in added, f"{heading} Added never names {name}"
    assert "music_instrumental\": false" in changed and "--no-instrumental" in changed


def _newest_changelog_entries(text: str) -> tuple[str, str]:
    """The heading and body of the newest ``## [...]`` section that holds an entry.

    [Unreleased] when it has any; else (a release bump has emptied it into
    ``## [x.y.z] - <date>``) the latest released section, which is the next one down."""
    sections = re.findall(r"^## (\[[^\]]+\][^\n]*)\n(.*?)(?=^## \[|\Z)", text, re.M | re.S)
    assert sections, "CHANGELOG.md has no ## [...] section"
    for heading, body in sections:
        if re.search(r"^- ", body, re.M):  # a bullet, not an empty ### stub
            return heading.split(" - ")[0], body
    raise AssertionError("no CHANGELOG section holds an entry")


def _section_of(body: str, title: str, heading: str = "[Unreleased]") -> str:
    m = re.search(rf"^### {title}\n(.*?)(?=^### |\Z)", body, re.M | re.S)
    assert m, f"{heading} has no '### {title}'"
    return m.group(1)
