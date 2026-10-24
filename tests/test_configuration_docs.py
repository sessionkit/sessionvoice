"""The configuration docs, pinned to the README template, the schemas and the code.

docs/configuration.md says which files and variables each part reads; docs/settings.md
lists every key of the three settings files, with its type and default. The README's
Configuration section links both, and its Logs section ends on the Health line. Inside
these two pages a link into this repo is relative or an #anchor, never a full URL.

Each default on the settings page is the schema's, except where the page names a
difference; those are held to the code instead, so the page cannot drift from either.
"""
from __future__ import annotations

import json
import posixpath
import re
from pathlib import Path

import pytest

from tests.md_links import is_local, links
from tests.test_readme_links import SELF_URL, anchors, slug

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
CONFIG_DOC = "docs/configuration.md"
SETTINGS_DOC = "docs/settings.md"
NEW_DOCS = (CONFIG_DOC, SETTINGS_DOC)
KIT_GUIDE = "https://github.com/sessionkit/sessionkit/blob/HEAD/docs/settings.md"

#: Each settings file's section on the settings page, and its schema.
SCHEMAS = {"ears.json": "settings/ears.schema.json",
           "ears-sounds.json": "settings/ears-sounds.schema.json",
           "mouth.json": "settings/mouth.schema.json"}
#: Keys whose default on the page is the code's, not the schema's: each is named
#: under "Where the schema and the code differ" and checked against the code below.
DIFFERS = {"output", "voice", "tts", "barge_in", "barge_duck_recover_s"}


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _section(text: str, title: str, level: str = "##") -> str:
    """The body of the `level title` heading, up to the next heading of that level or higher."""
    stop = "|".join("#" * n + " " for n in range(1, len(level) + 1))
    m = re.search(rf"^{re.escape(level)} {re.escape(title)}\n(.*?)(?=^(?:{stop})|\Z)",
                  text, re.MULTILINE | re.DOTALL)
    assert m, f"no '{level} {title}' section"
    return m.group(1)


def _h2s(text: str) -> list[str]:
    return re.findall(r"^## (.+)$", text, re.MULTILINE)


def _rows(text: str) -> dict[str, list[str]]:
    """A Markdown table's rows keyed by their first cell's `code` name: the other cells."""
    out = {}
    for line in text.splitlines():
        m = re.match(r"^\| `([^`]+)` \|", line)
        if m:
            cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
            out[m.group(1)] = cells[1:]
    return out


def _schema(name: str) -> dict:
    return json.loads(_read(SCHEMAS[name]))["properties"]


# --- the README -----------------------------------------------------------------


def test_readme_sections_follow_the_template_order():
    # templates/README.md: Components, Configuration, Running, Logs. This README has no
    # Running; its Use and Install come after.
    order = ["Components", "Configuration", "Logs", "Use", "Install"]
    assert [h for h in _h2s(README) if h in order] == order


def test_readme_configuration_links_both_pages_relatively():
    section = _section(README, "Configuration")
    assert f"({CONFIG_DOC})" in section
    assert f"({SETTINGS_DOC})" in section


@pytest.mark.parametrize("title", ["Configuration", "Logs"])
def test_readme_new_sections_link_the_repo_relatively(title):
    targets = links(_section(README, title))
    assert targets
    for target in targets:
        assert not SELF_URL.match(target), target


def test_readme_logs_ends_on_the_health_line():
    lines = [ln for ln in _section(README, "Logs").strip().splitlines() if ln.strip()]
    start = max(i for i, ln in enumerate(lines) if ln.startswith("- "))
    assert lines[start].startswith("- **Health:** `ears services status`"), lines[start:]


def test_the_health_line_quotes_the_states_status_prints():
    from ears import services

    section = _section(README, "Logs")
    for state in (services._state(None, "no.such.label"), services._state("-", "x"),
                  services._state("N", "x")):
        assert f"`{state}`" in section, state


def test_readme_logs_names_the_log_dir_the_code_uses():
    assert 'Path.home() / "Library/Logs/ears"' in _read("src/ears/services.py")
    assert "~/Library/Logs/ears/" in _section(README, "Logs")


# --- docs/ ------------------------------------------------------------------------


@pytest.mark.parametrize("name", NEW_DOCS)
def test_docs_link_the_repo_relatively_or_by_anchor(name):
    targets = links(_read(name))
    assert targets, f"{name} has no links: the check would pass on nothing"
    assert any(is_local(t) for t in targets), f"{name}: no relative link to check"
    for target in targets:
        assert not SELF_URL.match(target), f"{name}: {target}"


@pytest.mark.parametrize("name", NEW_DOCS)
def test_docs_links_into_the_repo_resolve(name):
    """Every #anchor here, and every file and #anchor a relative link names, exists."""
    text = _read(name)
    for target in links(text):
        if target.startswith("#"):
            assert target[1:] in anchors(text), f"{name}: {target}"
        elif is_local(target):
            part, _, anchor = target.partition("#")
            path = posixpath.normpath(posixpath.join(posixpath.dirname(name), part))
            assert (ROOT / path).is_file(), f"{name}: {target}"
            if anchor:
                assert anchor in anchors(_read(path)), f"{name}: {target}"


def test_the_anchor_check_can_fail():
    # A zero is a receipt only when the same check is shown to hit.
    assert "no-such-heading" not in anchors(_read(SETTINGS_DOC))
    assert slug("ears-sounds.json") == "ears-soundsjson"
    assert slug("sessionvoice's home") == "sessionvoices-home"


def test_settings_links_the_kit_guide():
    assert KIT_GUIDE in _read(SETTINGS_DOC)


def test_configuration_settings_h2_is_a_real_link_to_the_settings_page():
    section = _section(_read(CONFIG_DOC), "Settings")
    assert "[docs/settings.md][settings]" in section
    assert "\n[settings]: settings.md\n" in _read(CONFIG_DOC)


def test_configuration_names_each_launch_agent_by_its_label():
    from ears import services

    rows = _rows(_section(_read(CONFIG_DOC), "The keeper and the launch agents"))
    for name in services.ALL:   # a service of two agents (sounds) names both labels
        assert rows[name][0] == ", ".join(f"`{services._label(j)}`" for j in services.jobs([name])), name


def test_configuration_names_the_files_the_code_reads(monkeypatch):
    from ears import keys
    from ears.listen import stt
    from mouth import backends, voices

    for v in ("EARS_CONF", "MOUTH_SETTINGS"):
        monkeypatch.delenv(v, raising=False)
    home = str(Path.home())
    text = _read(CONFIG_DOC)
    for path in (stt.conf_path(), str(keys.stt_key_path()), str(voices.home_settings_path()),
                 str(backends.tts_key_path())):
        assert "~" + path[len(home):] in text, path


# --- the settings page: every key, against its schema and the code ----------------


@pytest.mark.parametrize("name", list(SCHEMAS))
def test_settings_lists_every_schema_key(name):
    rows = set(_rows(_section(_read(SETTINGS_DOC), name)))
    keys = set(_schema(name)) - {"$schema"}
    if name == "ears-sounds.json":       # the second table: the fields of each sound
        rows -= set(_schema(name)["sounds"]["additionalProperties"]["properties"])
    assert rows == keys, f"{name}: missing {keys - rows}, extra {rows - keys}"


@pytest.mark.parametrize("name", list(SCHEMAS))
def test_settings_defaults_are_the_schemas(name):
    rows = _rows(_section(_read(SETTINGS_DOC), name))
    for key, prop in _schema(name).items():
        if key == "$schema" or key in DIFFERS or "default" not in prop:
            continue
        default = prop["default"]
        if isinstance(default, list) and default:      # barge_ignore_words: named, not quoted
            continue
        cell = rows[key][1]
        assert cell.startswith(f"`{json.dumps(default)}`"), f"{name} {key}: {cell}"


def test_each_sound_field_is_the_schemas_with_the_codes_default():
    rows = _rows(_section(_read(SETTINGS_DOC), "ears-sounds.json"))
    fields = _schema("ears-sounds.json")["sounds"]["additionalProperties"]["properties"]
    assert set(fields) <= set(rows)
    src = _read("keeper/ears-sounds.py")
    for field in ("freq", "ms", "volume"):
        default = re.search(rf'snd\.get\("{field}", ([0-9.]+)\)', src).group(1)
        assert rows[field][1] == f"`{default}`", field


def test_the_layers_list_is_the_mouths():
    from mouth import voices

    section = _section(_read(SETTINGS_DOC), "The mouth reads some keys from the layers", "###")
    first = section.strip().split("\n\n")[0]
    assert re.findall(r"`([a-z_]+)`", first) == list(voices.LINE_KEYS)
    rows = _rows(_section(_read(SETTINGS_DOC), "mouth.json"))
    for key, cells in rows.items():
        assert cells[-1] == ("yes" if key in voices.LINE_KEYS else "no"), key
    file_only = set(_schema("mouth.json")) - {"$schema"} - set(voices.LINE_KEYS)
    last = section.strip().split("\n\n")[-1]
    assert set(re.findall(r"`([a-z_]+)`", last)) == file_only


def test_the_differences_match_the_code():
    from ears.listen import stt
    from mouth import backends, hold, voices

    rows = _rows(_section(_read(SETTINGS_DOC), "mouth.json"))
    diff = " ".join(_section(_read(SETTINGS_DOC), "Where the schema and the code differ").split())
    assert rows["voice"][1] == f"`{voices.BUILTIN}`"
    assert f"`{backends.KOKORO_URL}`" in rows["tts"][1] and f"`{backends.KOKORO_MODEL}`" in rows["tts"][1]
    recover = hold.BARGE_DUCK_RECOVER_BY_PARTIALS
    assert f'`{recover["final"]}` when the ears\' `partials` is `"final"`' in diff
    assert f'`{recover["nemotron"]}` when it is `"nemotron"`' in diff
    assert f"`{stt.URL_ONLY_MODEL}`" in diff and f"`{stt.FALLBACK_URL}`" in diff
    assert hold.ANYONE == "anyone" and '`{"room": ["<mailbox>"]}`' in diff
    for key in DIFFERS - {"voice", "tts"}:
        assert f"`{key}`" in diff, key


def test_ears_defaults_the_page_states_are_the_codes():
    from ears import recording
    from ears.listen import stt

    rows = _rows(_section(_read(SETTINGS_DOC), "ears.json"))
    assert rows["record"][1] == f"`{json.dumps(recording.DEFAULT_RECORD)}`"
    assert rows["record_days"][1] == f"`{recording.DEFAULT_DAYS}`"
    assert rows["stt_hint"][1] == f"`{json.dumps(stt.HINT_DEFAULT)}`"
    final = json.dumps({"url": stt.FALLBACK_URL, "model": stt.FALLBACK_MODEL})
    assert rows["final"][1] == f"`{final}`"
