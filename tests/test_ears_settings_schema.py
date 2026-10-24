"""settings/*.schema.json: well-formed, they accept what the ears write, and install puts each
beside its settings file as a dot file."""
import json
from pathlib import Path
from ears.home import SETTINGS
from tests import env_fence

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "settings/ears.schema.json").read_text())
SOUNDS = json.loads((ROOT / "settings/ears-sounds.schema.json").read_text())
MOUTH = json.loads((ROOT / "settings/mouth.schema.json").read_text())


def test_schema_names_its_controls():
    p = SCHEMA["properties"]
    assert p["listen"]["type"] == "boolean" and p["input"]["type"] == "string"
    titles = [o["title"] for o in p["final"]["anyOf"]]
    assert titles[0].startswith("Parakeet") and "Apple (ears serve, :2023)" in titles and titles[-1] == "Other"
    assert "stt" not in p                           # final's old name: still read, no longer drawn
    assert [o["const"] for o in p["partials"]["anyOf"]] == ["final", "nemotron"] and p["partials"]["default"] == "final"


def test_schema_has_the_heard_hooks_partials_only_switch():
    """EARS-362: ears.json's heard_partials_only, which `ears heard hook` reads (heard.py
    keeps the key's name itself; the file is additionalProperties: false)."""
    from ears import heard
    p = SCHEMA["properties"][heard.PARTIALS_ONLY_KEY]
    assert p["type"] == "boolean" and p["default"] is False
    assert p["title"] and "--partials-only" in p["description"]
    assert heard.HEARD_PARTIALS_ONLY_ENV in p["description"]
    assert SCHEMA["additionalProperties"] is False


def test_a_seeded_file_is_a_preset(monkeypatch):
    from ears import services                     # what install writes must be one of the picker's choices
    monkeypatch.delenv("EARS_STT_URL", raising=False); monkeypatch.delenv("EARS_STT_MODEL", raising=False)
    consts = [o["const"] for o in SCHEMA["properties"]["final"]["anyOf"] if "const" in o]
    seeded = {"url": services._stt_url(), "model": services._stt_model()}
    assert seeded in consts and SCHEMA["properties"]["final"]["default"] == seeded


def test_install_puts_each_schema_beside_the_settings_as_a_dot_file(tmp_path, monkeypatch):
    from ears import services
    d, m = tmp_path / "ears", tmp_path / "mouth"
    monkeypatch.setenv("EARS_CONF", str(d / "ears.json"))
    monkeypatch.setenv("MOUTH_SETTINGS", str(m / "mouth.json"))
    d.mkdir()
    (d / "ears.schema.json").write_text("{}")          # the old visible name goes
    out = services.install_schema()
    assert json.loads((d / ".ears.schema.json").read_text()) == SCHEMA
    assert json.loads((d / ".ears-sounds.schema.json").read_text()) == SOUNDS
    assert json.loads((m / ".mouth.schema.json").read_text()) == MOUTH   # the mouth's, beside its own
    assert not (d / ".mouth.schema.json").exists()
    assert not (d / "ears.schema.json").exists() and not list(d.glob("*.tmp"))
    assert ".ears.schema.json" in out and ".ears-sounds.schema.json" in out


def _no_mouth_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("EARS_CONF", str(tmp_path / "ears" / "ears.json"))
    monkeypatch.setenv("MOUTH_DIR", str(home / ".voicemode" / "mouth"))
    env_fence.delenv(monkeypatch, "MOUTH_SETTINGS")
    return home


def test_install_puts_the_mouth_schema_in_settings_mouth_before_any_settings_file(tmp_path, monkeypatch):
    # EARS-180: no mouth.json yet and no symlink; the schema still goes to ~/.sessionkit/voice/settings
    # (made), never beside settings_path()'s old fallback, ~/.voicemode/mouth.
    from ears import services
    home = _no_mouth_env(tmp_path, monkeypatch)
    services.install_schema()
    assert json.loads((home / SETTINGS / ".mouth.schema.json").read_text()) == MOUTH
    assert not (home / ".voicemode").exists()


def test_install_puts_the_mouth_schema_in_settings_mouth_when_only_the_old_file_exists(tmp_path, monkeypatch):
    from ears import services
    home = _no_mouth_env(tmp_path, monkeypatch)
    old = home / ".voicemode" / "mouth"
    old.mkdir(parents=True)
    (old / "settings.json").write_text('{"voice": "bf_emma"}')
    services.install_schema()
    assert (home / SETTINGS / ".mouth.schema.json").exists()
    assert not (old / ".mouth.schema.json").exists()


def test_install_puts_the_mouth_schema_beside_an_existing_mouth_json(tmp_path, monkeypatch):
    from ears import services
    home = _no_mouth_env(tmp_path, monkeypatch)
    m = home / SETTINGS
    m.mkdir(parents=True)
    (m / "mouth.json").write_text('{"voice": "bm_george"}')
    services.install_schema()
    assert json.loads((m / ".mouth.schema.json").read_text()) == MOUTH
    assert not (home / ".voicemode").exists()


def test_sounds_schema_covers_every_key_and_sound_the_ears_write():
    p = SOUNDS["properties"]
    assert {"allow", "to", "pair", "sounds"} <= set(p) and SOUNDS["additionalProperties"] is False
    names = set(p["sounds"]["properties"])
    assert {"turn", "partial", "event:device-change", "event:turn-discarded", "event:listen-stopped"} <= names
    for name in names:
        knobs = p["sounds"]["properties"][name]["properties"]
        assert {"on", "freq", "ms", "volume"} <= set(knobs), name


def test_input_draws_as_the_device_picker():
    # settings draws Input as its folded device picker from this hint; without it, a text
    # field (settings 31da5c6)
    assert SCHEMA["properties"]["input"]["x-widget"] == "audio-input"


def test_mouth_schema_covers_barge_in():
    """Every barge_in the schema offers, the mouth reads."""
    from mouth import hold
    p = MOUTH["properties"]
    assert {"output", "voice", "barge_in"} <= set(p)
    assert p["barge_in"]["default"] == "anyone" and hold.barge_in(settings={}) is None
    who = MOUTH["$defs"]["who"]["anyOf"]
    # EARS-202 G24: the only default is the code's, "anyone"; a list branch has none
    # (it named one person's voice, while the mouth reads anyone).
    assert who[0]["const"] == hold.ANYONE and "default" not in who[1]
    assert hold.barge_in(settings={"barge_in": ["alice"]}) == {"alice"}
    assert set(p["barge_in"]["anyOf"][1]["properties"]) == {"room", "call"}


def test_mouth_schema_defaults_are_the_codes():
    """EARS-202 G24: every default in the mouth schema is what the mouth does when unset;
    no default names a person."""
    from mouth import hold
    found = []

    def walk(node, path):
        if isinstance(node, dict):
            if "default" in node:
                found.append((path, node["default"]))
            for k, v in node.items():
                walk(v, f"{path}/{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}/{i}")

    walk(MOUTH, "")
    assert ("/properties/barge_in", hold.ANYONE) in found
    assert hold.barge_in(settings={}) is None
    assert all("theo" not in json.dumps(d).lower() for _, d in found), found


def test_mouth_schema_covers_duck_and_volume(tmp_path, monkeypatch):
    """EARS-152 (seam schema-covers-duck-volume): every default the schema offers
    for the duck and the level is what the mouth reads with nothing set, and a
    value the schema refuses falls through to the next layer."""
    from mouth import voices
    p = MOUTH["properties"]
    keys = {"music_duck", "music_duck_level", "music_duck_restore_s", "volume", "play_volume"}
    assert keys <= set(p)
    for k in keys:
        assert p[k]["title"] and p[k]["description"], k
    assert p["music_duck"]["type"] == "boolean"
    for k in ("music_duck_level", "volume", "play_volume"):
        assert (p[k]["type"], p[k]["minimum"], p[k]["maximum"]) == ("integer", 0, 100), k
    assert p["music_duck_restore_s"]["type"] == "number" and p["music_duck_restore_s"]["minimum"] == 0
    assert "default" not in p["play_volume"]          # unset follows volume

    monkeypatch.setenv("MOUTH_SETTINGS", str(tmp_path / "none.json"))
    duck = voices.duck_setting()
    assert duck["on"] is p["music_duck"]["default"]
    assert duck["level"] == p["music_duck_level"]["default"]
    assert duck["restore_s"] == p["music_duck_restore_s"]["default"]
    assert voices.volume_setting(file=False) == (p["volume"]["default"], "builtin")
    assert voices.volume_setting(file=True) == (p["volume"]["default"], "builtin")

    # out of the schema's range: the layer is skipped, the next answers
    s = tmp_path / "mouth.json"
    s.write_text(json.dumps({"music_duck": "yes", "music_duck_level": 101, "music_duck_restore_s": -1,
                             "volume": 101, "play_volume": -1}))
    monkeypatch.setenv("MOUTH_SETTINGS", str(s))
    assert voices.duck_setting() == {"on": True, "level": 20, "restore_s": 1.0, "from": "builtin"}
    assert voices.volume_setting(file=True) == (100, "builtin")
    s.write_text(json.dumps({"music_duck": False, "music_duck_level": 0, "volume": 0, "play_volume": 100}))
    assert voices.duck_setting()["on"] is False and voices.duck_setting()["level"] == 0
    assert voices.volume_setting(file=False) == (0, "settings")
    assert voices.volume_setting(file=True) == (100, "settings")


def test_mouth_schema_covers_the_barge_duck_and_words():
    """EARS-238: every barge default the schema offers is what the mouth reads
    with nothing set, and BARGE_WORDS is 2 (EARS-274;
    3 before it)."""
    from mouth import hold
    p = MOUTH["properties"]
    for k in ("barge_words", "barge_duck_level", "barge_duck_recover_s"):
        assert p[k]["title"] and p[k]["description"], k
    assert (p["barge_words"]["type"], p["barge_words"]["minimum"]) == ("integer", 1)
    assert (p["barge_duck_level"]["type"], p["barge_duck_level"]["minimum"],
            p["barge_duck_level"]["maximum"]) == ("integer", 0, 100)
    assert (p["barge_duck_recover_s"]["type"], p["barge_duck_recover_s"]["minimum"]) == ("number", 0)
    assert p["barge_words"]["default"] == hold.BARGE_WORDS == hold.barge_words(None, {}) == 2
    assert p["barge_duck_level"]["default"] == hold.barge_duck_level({})
    assert p["barge_duck_recover_s"]["default"] == hold.barge_duck_recover_s({})
    # EARS-315 s9a: the cap's reader and its schema entry land together (do-002)
    mx = p["barge_duck_max_s"]
    assert mx["title"] and mx["description"]
    assert (mx["type"], mx["minimum"]) == ("number", 0)
    assert mx["default"] == hold.BARGE_DUCK_MAX_S == hold.barge_duck_max_s({}) == 4
    # the env sits below the user's setting (EARS-315 R7), and the schema says so
    assert "beats $MOUTH_BARGE_WORDS" in p["barge_words"]["description"]


def test_mouth_schema_covers_normalize(tmp_path, monkeypatch):
    """EARS-208 R1/R2: the settings app offers ``normalize`` (on) and
    ``normalize_lufs`` (-16), the defaults are what the mouth reads with nothing
    set, and a value outside the schema's range falls to the next layer."""
    from mouth import voices
    p = MOUTH["properties"]
    for k in ("normalize", "normalize_lufs"):
        assert p[k]["title"] and p[k]["description"], k
    assert (p["normalize"]["type"], p["normalize"]["default"]) == ("boolean", True)
    lufs = p["normalize_lufs"]
    assert (lufs["type"], lufs["default"], lufs["minimum"], lufs["maximum"]) == ("number", -16, -40, -5)

    monkeypatch.setenv("MOUTH_SETTINGS", str(tmp_path / "none.json"))
    assert voices.normalize_setting() == (p["normalize"]["default"], "builtin")
    assert voices.lufs_setting() == (lufs["default"], "builtin")

    s = tmp_path / "mouth.json"
    monkeypatch.setenv("MOUTH_SETTINGS", str(s))
    for edge in (lufs["minimum"], lufs["maximum"]):
        s.write_text(json.dumps({"normalize": False, "normalize_lufs": edge}))
        assert voices.normalize_setting() == (False, "settings")
        assert voices.lufs_setting() == (float(edge), "settings")
    s.write_text(json.dumps({"normalize": "off", "normalize_lufs": lufs["minimum"] - 1}))
    assert voices.normalize_setting() == (True, "builtin")
    s.write_text(json.dumps({"normalize_lufs": lufs["maximum"] + 1}))
    assert voices.lufs_setting() == (-16.0, "builtin")


def test_mouth_schema_covers_the_lead(tmp_path, monkeypatch):
    """EARS-285 fix-002: ``lead_s`` is a setting, default 0,
    what the mouth reads with nothing set; below the minimum falls
    to the default."""
    from mouth import voices
    lead = MOUTH["properties"]["lead_s"]
    assert lead["title"] and lead["description"]
    assert (lead["type"], lead["minimum"], lead["default"]) == ("number", 0, 0)
    monkeypatch.setenv("MOUTH_SETTINGS", str(tmp_path / "none.json"))
    assert voices.lead_setting() == (lead["default"], "builtin") == (voices.BUILTIN_LEAD_S, "builtin")
    s = tmp_path / "mouth.json"
    monkeypatch.setenv("MOUTH_SETTINGS", str(s))
    s.write_text(json.dumps({"lead_s": 2.5}))
    assert voices.lead_setting() == (2.5, "settings")
    s.write_text(json.dumps({"lead_s": lead["minimum"] - 1}))
    assert voices.lead_setting() == (0.0, "builtin")
