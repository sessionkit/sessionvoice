"""Talk, uncrowded (per the design mock-up, approved as drawn).

Inside Settings' shell the tab names the room and the strip says what the ears hear, so Talk's own
title row goes and its chips row is the header; the read-only input bar goes; who's here is on the
chips; Debug shows only when Settings says so (talk.json show_debug, off by default).
"""
import json
from pathlib import Path

import pytest

from ears import home, services
from talk import server

ROOT = Path(__file__).resolve().parents[1]
PAGE = (Path(server.__file__).parent / "index.html").read_text()
SCHEMA = json.loads((ROOT / "settings" / "talk.schema.json").read_text())


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    for n in ("EARS_CONF", "EARS_WAKE", "MOUTH_SETTINGS", "EARS_RECORD", "EARS_RECORD_DAYS",
              "EARS_HOME", "SESSIONVOICE_HOME"):
        monkeypatch.delenv(n, raising=False)
    return tmp_path


def write_talk(text):
    d = home.settings_dir(); d.mkdir(parents=True, exist_ok=True)
    (d / "talk.json").write_text(text)


# -- show_debug: talk.json -> the page's config event ------------------------------------

def test_no_talk_json_hides_debug(fake_home):
    assert server.config()["show_debug"] is False


def test_show_debug_true_reaches_the_config(fake_home):
    write_talk('{"show_debug": true}')
    assert server.config()["show_debug"] is True


@pytest.mark.parametrize("text", ['{"show_debug": 1}', '{"show_debug": "yes"}', '{"show_debug": false}',
                                  "not json", "[true]", "{}"])
def test_anything_but_exactly_true_hides_debug(fake_home, text):
    write_talk(text)
    assert server.config()["show_debug"] is False


def test_config_keeps_the_start_settings(fake_home):
    server.configure(person="Zed", settings_url="/settings/")
    try:
        cfg = server.config()
        assert cfg["person"] == "Zed" and cfg["settings_url"] == "/settings/" and "show_debug" in cfg
    finally:
        server.configure()


def test_talk_json_is_read_fresh_each_time(fake_home):
    write_talk('{"show_debug": true}')
    assert server.config()["show_debug"] is True
    write_talk('{"show_debug": false}')
    assert server.config()["show_debug"] is False


# -- the schema Settings draws the row from ----------------------------------------------

def test_the_schema_has_one_boolean_off_by_default():
    p = SCHEMA["properties"]["show_debug"]
    assert p["type"] == "boolean" and p["default"] is False and p["title"] == "Show Debug in Talk"


def test_the_schema_ships_and_installs_beside_talk_json(fake_home):
    assert "talk.schema.json" in services.SCHEMAS
    assert (ROOT / "src" / "ears" / "_settings" / "talk.schema.json").resolve() == ROOT / "settings" / "talk.schema.json"
    services.install_schema()
    assert json.loads((home.settings_dir() / ".talk.schema.json").read_text()) == SCHEMA


# -- the page --------------------------------------------------------------------------

def test_the_read_only_input_bar_is_gone():
    assert "<footer" not in PAGE and 'class="note">read-only' not in PAGE and "What the ears heard" not in PAGE


def test_the_presence_line_is_on_the_chips():
    assert 'id="presence"' not in PAGE and 'getElementById("presence")' not in PAGE
    assert '<div class="agents" id="agents"></div>' in PAGE      # every agent's chip, in its own lane
    assert "[...Object.keys(presence), ...speaking].forEach(chip)" in PAGE   # a chip before they speak


def test_debug_is_hidden_until_the_config_says_show():
    assert '<button class="chip dbg" id="dbg" hidden' in PAGE
    assert 'document.getElementById("dbg").hidden = !cfg.show_debug;' in PAGE
    assert 'if (!cfg.show_debug && debug) { debug = false;' in PAGE   # hidden is also off


def test_framed_drops_the_title_row_before_first_paint():
    head = PAGE[:PAGE.index("<style>")]
    assert '<script id="framed">' in head and 'classList.add("framed")' in head
    assert ".framed .hd { display:none }" in PAGE


def test_framed_status_says_words_only_when_not_live():
    assert '(framed ? "" : mic ? `live · listening on ${mic}` : "live")' in PAGE
    assert ".framed .chips .live { display:none }" in PAGE


def test_the_main_script_is_still_the_first_bare_script_tag():
    assert PAGE.count("<script>") == 1        # tests/test_talk_page_pair.py reads from it


# -- the face states (so a chip doesn't push others off) ------------------------------------

def test_no_word_on_a_chip_so_it_never_changes_width():
    assert 'class="stw"' not in PAGE and '<span class="dots">thinking</span>' not in PAGE


def test_busy_turns_and_speaking_pulses_on_the_face():
    assert ".chip.thinking .pa::before { border-top-color:#ff9f0a;" in PAGE and "animation:turn" in PAGE
    assert ".chip.speaking .pa::before { border-color:#0a84ff; animation:ring" in PAGE


def test_the_rings_stop_for_reduced_motion():
    assert "@media (prefers-reduced-motion: reduce) { .chip .pa::before { animation:none } }" in PAGE


def test_the_long_press_says_ride_along_or_wake():
    assert 'thinking: "busy: your lines ride along"' in PAGE
    assert 'listening: "listening: a line with the name wakes them"' in PAGE
    assert 'away: "can\'t hear the room"' in PAGE
