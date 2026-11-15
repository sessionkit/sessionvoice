"""The install microphone check's docs agree with its code.

docs/troubleshooting.md quotes every line the check prints, taken from the module's own
constants (each template's fixed parts, around its placeholders), so a reworded line
fails here until the page follows. docs/ears.md names --listen as a consent, beside the
other commands whose typing is the consent, and EARS_MIC_CHECK_S=0 beside it.
"""
from __future__ import annotations

import re
import string
from pathlib import Path

import pytest

from ears import mic_check as mc

ROOT = Path(__file__).resolve().parents[1]
TROUBLE = (ROOT / "docs" / "troubleshooting.md").read_text(encoding="utf-8")
EARS_DOC = (ROOT / "docs" / "ears.md").read_text(encoding="utf-8")
CONFIG = (ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
CHANGELOG = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
SECTION_TITLE = "## Install: the microphone was not heard"


def _section(text: str, title: str) -> str:
    start = text.index(title)
    nxt = text.find("\n## ", start + len(title))
    return text[start:nxt if nxt != -1 else len(text)]


SECTION = _section(TROUBLE, SECTION_TITLE)
#: The section with every run of whitespace one space: a phrase survives a re-wrap.
FLAT = " ".join(SECTION.split())

#: Every line the check prints: its screen form, the template with install's label or prefix.
LINES = {
    "waiting": mc.row(mc.WAITING),
    "would-wait": mc.row(mc.WOULD_WAIT),
    "heard": mc.row(mc.HEARD_LINE),
    "not-heard": mc.fail(mc.NOT_HEARD_LINE),
    "lid": mc.fail(mc.LID_LINE),
    "pinned": mc.fail(mc.PINNED_LINE),
    **{name: mc.row(getattr(mc, name)) for name in dir(mc) if name.startswith("NC_")},
}


def _fixed_parts(template: str) -> list[str]:
    """The template's literal text between its placeholders."""
    return [lit for lit, _field, _spec, _conv in string.Formatter().parse(template) if lit.strip()]


def test_every_not_checked_line_is_listed():
    # a new NC_ line is found by name, so it cannot be left out of LINES (and so of the page)
    assert {"NC_LISTEN_OFF", "NC_OFF", "NC_NO_INPUT", "NC_CALL", "NC_HAND_MADE", "NC_KEEPER_FAILED",
            "NC_ONLY_NO_KEEPER", "NC_NO_KEEPER"} <= set(LINES)


@pytest.mark.parametrize("name", sorted(LINES))
def test_the_troubleshooting_page_quotes_every_mic_line(name):
    for part in _fixed_parts(LINES[name]):
        assert part in SECTION, f"{name}: {part!r} is not on the troubleshooting page"


@pytest.mark.parametrize("why", sorted(mc.WHY.values()))
def test_the_page_quotes_each_reason_for_not_heard(why):
    assert f"({why})" in SECTION


def test_the_page_states_the_default_bound_and_the_allow_window():
    assert f"up to {mc.secs(mc.BOUND_S)} s" in SECTION
    assert f"NOT HEARD in {mc.secs(mc.BOUND_S)} s" in SECTION
    assert "about 40 s from the prompt to your click" in FLAT
    assert "click Allow when macOS asks" in FLAT, "the app may not be listed yet: re-run and allow"
    assert "muted input" in FLAT
    assert "prints the listening-off line, not the no-microphone one" in FLAT
    assert "--only ...,speakers" in FLAT, "the second mic user the check does not prove"


def test_the_remedy_is_a_command_of_its_own():
    assert "\n    ears services install --only keeper\n" in SECTION


def test_the_zeros_remedy_links_the_section():
    zeros = TROUBLE[TROUBLE.index("- `zeros`:"):TROUBLE.index("- `beat`:")]
    assert "(#install-the-microphone-was-not-heard)" in zeros


def test_ears_md_names_listen_and_the_off_switch_beside_the_record_example():
    at = EARS_DOC.index("`ears speakers record` and `enrol`")
    near = EARS_DOC[at:at + 1500]
    assert "`ears services install --listen`" in near and "Typing the flag is the consent" in near
    assert "`EARS_MIC_CHECK_S=0` turns that check off" in near
    assert f"{mc.secs(mc.BOUND_S)} by default" in near


def test_the_configuration_page_names_the_variable_and_its_default():
    row = next(ln for ln in CONFIG.splitlines() if ln.startswith(f"| `{mc.BOUND_ENV}` "))
    assert f"| `{mc.secs(mc.BOUND_S)}` |" in row and "`0` turns the check off" in row
    assert f"up to {mc.secs(mc.BOUND_MAX_S)}" in row


def test_the_readme_install_names_listen_and_the_check():
    install = _section(README, "## Install")
    assert "\n    ears services install --agent NAME --listen\n" in install
    assert "Typing `--listen` is the consent" in install
    assert "EARS_MIC_CHECK_S=0" in install and f"up to {mc.secs(mc.BOUND_S)} s" in install


#: The last release that shipped without ``--listen``; the entry belongs in a newer one.
SHIPPED_WITHOUT = (0, 3, 3)


def changelog_section_naming(text: str, needle: str) -> str:
    """The heading of the oldest ``## [...]`` section whose body names ``needle``.

    Found by what it says, not where it sits: a release stamp that empties [Unreleased]
    into ``## [x.y.z] - date`` leaves it found. It must be [Unreleased] or a release newer
    than SHIPPED_WITHOUT: an entry buried in a release that shipped before does not count."""
    sections = re.findall(r"^## (\[[^\]]+\][^\n]*)\n(.*?)(?=^## \[|\Z)", text, re.M | re.S)
    assert sections, "CHANGELOG.md has no ## [...] section"
    for heading, body in reversed(sections):  # oldest first
        if needle in body:
            name = heading.split(" - ")[0]
            if name != "[Unreleased]":
                version = tuple(int(n) for n in name.strip("[]").split("."))
                assert version > SHIPPED_WITHOUT, f"{name} names {needle}, but it shipped without it"
            return name
    raise AssertionError(f"no CHANGELOG section names {needle}")


def test_the_changelog_says_so_under_unreleased_or_its_release():
    """Under [Unreleased] on a branch, and in the release it became once the release
    script stamps [Unreleased] as ``[x.y.z] - date``: one section names both."""
    name = changelog_section_naming(CHANGELOG, "`ears services install --listen`")
    body = re.search(r"^## " + re.escape(name) + r"[^\n]*\n(.*?)(?=^## \[|\Z)", CHANGELOG, re.M | re.S)
    assert body and mc.BOUND_ENV in body.group(1), f"{name} names --listen but not {mc.BOUND_ENV}"


def test_a_stamped_changelog_still_finds_the_entry():
    """The release stamp's shape: [Unreleased] emptied above ``## [0.4.0] - date``."""
    stamped = CHANGELOG.replace("## [Unreleased]\n", "## [Unreleased]\n\n## [0.4.0] - 2030-06-09\n", 1)
    assert changelog_section_naming(stamped, "`ears services install --listen`") == "[0.4.0]"


def test_an_entry_only_in_an_old_release_does_not_count():
    """Moved under a release that shipped without the flag, the entry fails loudly."""
    old = "## [Unreleased]\n\n## [0.3.3] - 2030-06-09\n- `ears services install --listen`\n"
    with pytest.raises(AssertionError, match="shipped without it"):
        changelog_section_naming(old, "`ears services install --listen`")


def test_the_install_docstring_says_who_is_asked_and_the_check():
    from ears import services
    doc = services.__doc__
    assert "capture's own python, not a" in doc and "EARS_MIC_CHECK_S" in doc and "--listen" in doc
