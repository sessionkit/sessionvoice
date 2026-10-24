"""EARS-294 T4 and T5: the env helper (src/ears/envnames.py), the renamed ears names
(ten, and the heard hook's three of EARS-362) and the 39 renamed mouth names (VOICEMODE_MOUTH_<X> -> MOUTH_<X>).

(a) Every alias in the table: the OLD name alone is read, the NEW name alone is read,
    both set to different values means NEW wins, and set-but-empty counts as set.
(b) Hygiene: no code under src/, keeper/, bin/ or shadow/ reads a VOICEMODE_ name
    except through the table, or as one of the names it lists as untouched.
(c) The test fence (tests/env_fence.py, applied by tests/conftest.py) and the alias
    table agree: a fenced variable is popped or set under BOTH its names, so a runner
    that exports the other name cannot get past the fence. So does the mouth tests' own
    per-test fence (env_fence.MOUTH_TEST_FENCE, applied by tests/mouth/conftest.py).
And each reader, and each file that keeps its own copy of a pair, reads the old name.
"""
import importlib.machinery
import importlib.util
import os
import re
from pathlib import Path

import pytest

from ears import envnames, heard, keys, lag
from ears.listen import silero, sources, stt
from tests import env_fence

ROOT = Path(__file__).resolve().parents[1]
PAIRS = sorted(envnames.ALIASES.items())
OLD_TO_NEW = {old: new for new, old in envnames.ALIASES.items()}

# The ten this task renames, and the heard hook's three (EARS-362): the table must hold
# exactly these, NEW -> OLD.
RENAMED = {
    "EARS_LISTEN_STT_URL": "VOICEMODE_LISTEN_STT_URL",
    "EARS_LISTEN_STT_MODEL": "VOICEMODE_LISTEN_STT_MODEL",
    "EARS_LISTEN_STT_KEY": "VOICEMODE_LISTEN_STT_KEY",
    "EARS_LISTEN_SILENCE_S": "VOICEMODE_LISTEN_SILENCE_S",
    "EARS_LISTEN_VAD_AGGRESSIVENESS": "VOICEMODE_LISTEN_VAD_AGGRESSIVENESS",
    "EARS_SILERO_MODEL": "VOICEMODE_SILERO_MODEL",
    "EARS_CALL_SOCKS": "VOICEMODE_CALL_EARS_SOCKS",
    "EARS_LAG_WARN_S": "VOICEMODE_EARS_LAG_WARN_S",
    "EARS_HEARD_EVENT": "VOICEMODE_HEARD_EVENT",
    "EARS_HEARD_CALLER": "VOICEMODE_HEARD_CALLER",
    "EARS_HEARD_HOOK": "VOICEMODE_HEARD_HOOK",
    "EARS_HEARD_BUDGET": "VOICEMODE_HEARD_BUDGET",
    "EARS_HEARD_MAX_LINES": "VOICEMODE_HEARD_MAX_LINES",
}
# ...and the mouth's 39 (T5), spelled out here so the table cannot drop or add one quietly.
MOUTH_RENAMED = (
    "APPLE_VOICE AUDIO_DAYS AUDIO_DIR BACKEND BARGE BARGE_DEVICES BARGE_FRESH_S BARGE_WORDS "
    "BARGE_WORDS_CALL CALL_SOCKS DEVICE DIR DUCK HEARD_BOX HOLD_GRACE_S HOLD_IDLE_S IDLE_S "
    "KEEP_AUDIO KOKORO_MODEL KOKORO_URL LABEL_WAIT_S LOG_DIR MAIL_ALLOW MAIL_LOG MAILBOX "
    "MPV_SOCKET NOTICE PAN REBUFFER_S RESUME_ANSWER_QUESTION RESUME_ANSWER_WORDS RESUME_S "
    "SENDMAIL SESSION_BIN SETTINGS SOUNDS_DIR TTS_KEY VERIFY_BIN VOICE").split()
RENAMED.update({f"MOUTH_{x}": f"VOICEMODE_MOUTH_{x}" for x in MOUTH_RENAMED})
# the MOUTH_ names the repo already had: none of the 39 may be one of them
MOUTH_ALREADY = {"MOUTH_QUEUE", "MOUTH_MAILDIR", "MOUTH_WT", "MOUTH_TIMEOUT_S", "MOUTH_PYTHON",
                 "MOUTH_SCHEMA", "MOUTH_FAKE_MAIL_ROOT", "MOUTH_FAKE_SENDMAIL_EXIT",
                 "MOUTH_FAKE_SENDMAIL_SLEEP", "MOUTH_GOLDEN_CAPTURE", "MOUTH_AGED_CLOCK_S",
                 # the mouth-sounds keeper's knobs (keeper/mouth-sounds.py)
                 "MOUTH_SOUNDS_BACKSTOP", "MOUTH_SOUNDS_CANNED", "MOUTH_SOUNDS_CURSORS",
                 "MOUTH_SOUNDS_DEFAULT_CMD", "MOUTH_SOUNDS_DRY", "MOUTH_SOUNDS_FIRED",
                 "MOUTH_SOUNDS_HEARD_DIR", "MOUTH_SOUNDS_LOG", "MOUTH_SOUNDS_MAX_FAILS",
                 "MOUTH_SOUNDS_SETTINGS", "MOUTH_SOUNDS_STOP", "MOUTH_SOUNDS_WAIT"}


def test_the_table_holds_the_thirteen_ears_names_and_the_39_mouth_names():
    assert len(MOUTH_RENAMED) == len(set(MOUTH_RENAMED)) == 39
    assert envnames.ALIASES == RENAMED


def test_no_mouth_name_is_one_the_repo_already_had():
    """Not a test knob, nor one of the mouth-sounds keeper's. (MOUTH_SOUNDS_DIR, the
    mouth's mail-sound dir, shares that keeper's prefix but is none of its names.)"""
    new = {n for n in envnames.ALIASES if n.startswith("MOUTH_")}
    assert len(new) == 39
    assert not new & MOUTH_ALREADY


def test_no_new_name_is_an_install_knob_or_another_alias():
    """EARS_STT_URL/_MODEL are `ears services install` knobs, with another meaning."""
    assert not {"EARS_STT_URL", "EARS_STT_MODEL", "EARS_SILERO_ONNX"} & set(envnames.ALIASES)
    names = list(envnames.ALIASES) + list(envnames.ALIASES.values())
    assert len(names) == len(set(names)), "a name is on both sides, or twice"
    assert not envnames.UNTOUCHED & set(names)


# --- (a) the alias table ------------------------------------------------------

@pytest.mark.parametrize("new, old", PAIRS)
def test_old_alone_is_read(new, old):
    env = {old: "old-value"}
    assert envnames.get(new, "dflt", env) == "old-value"
    assert envnames.isset(new, env) and envnames.source(new, env) == old


@pytest.mark.parametrize("new, old", PAIRS)
def test_new_alone_is_read(new, old):
    env = {new: "new-value"}
    assert envnames.get(new, "dflt", env) == "new-value"
    assert envnames.isset(new, env) and envnames.source(new, env) == new


@pytest.mark.parametrize("new, old", PAIRS)
def test_both_set_to_different_values_new_wins(new, old):
    env = {new: "new-value", old: "old-value"}
    assert envnames.get(new, "dflt", env) == "new-value"
    assert envnames.source(new, env) == new


@pytest.mark.parametrize("new, old", PAIRS)
def test_set_but_empty_counts_as_set(new, old):
    assert envnames.get(new, "dflt", {new: "", old: "old-value"}) == ""
    assert envnames.isset(new, {new: ""})
    assert envnames.get(new, "dflt", {old: ""}) == ""
    assert envnames.isset(new, {old: ""})


@pytest.mark.parametrize("new, old", PAIRS)
def test_neither_set_is_the_default(new, old):
    assert envnames.get(new, "dflt", {}) == "dflt"
    assert envnames.get(new, environ={}) is None
    assert not envnames.isset(new, {}) and envnames.source(new, {}) is None


@pytest.mark.parametrize("new, old", PAIRS)
def test_the_process_env_is_the_default_env(new, old, monkeypatch):
    monkeypatch.delenv(new, raising=False)
    monkeypatch.setenv(old, "old-value")
    assert envnames.get(new) == "old-value"
    monkeypatch.setenv(new, "")
    assert envnames.get(new, "dflt") == "" and envnames.isset(new)


@pytest.mark.parametrize("new, old", PAIRS)
def test_group_is_the_same_from_either_name(new, old):
    assert envnames.group(new) == envnames.group(old) == (new, old)
    assert envnames.old(new) == old


def test_a_name_with_no_alias_reads_itself():
    assert envnames.group("TALK_UNIX_SOCKET") == ("TALK_UNIX_SOCKET",)
    assert envnames.get("TALK_UNIX_SOCKET", "d", {"TALK_UNIX_SOCKET": "x"}) == "x"
    assert envnames.old("TALK_UNIX_SOCKET") is None


# --- (b) hygiene: no VOICEMODE_ read outside the table --------------------------

SCANNED = ("src", "keeper", "bin", "shadow")
TOKEN = re.compile(r"\bVOICEMODE_[A-Z0-9_]+")
# a read, not a write: ``os.environ["X"] = v`` is the new name being SET (write-new)
DIRECT_READ = re.compile(r"""os\.(?:environ\.get|getenv|environ\[)\(?\s*["']([A-Z0-9_]+)["'](?!\]\s*=[^=])""")
# A module constant holding a variable's name (``SENDMAIL_VAR = "MOUTH_SENDMAIL"``), and a
# read of the environment through one: os.environ.get(X), os.getenv(X), os.environ[X],
# X in os.environ, and <mapping>.get(X) (envnames.get(X) is the table, and allowed).
CONST = re.compile(r"""^[ \t]*([A-Z][A-Z0-9_]*)[ \t]*(?::[ \t]*\w+[ \t]*)?=[ \t]*["']([A-Z][A-Z0-9_]*)["']""", re.M)
CONST_READS = (
    re.compile(r"""os\.(?:environ\.get|getenv)\(\s*(?:\w+\.)?([A-Z][A-Z0-9_]*)\b"""),
    re.compile(r"""os\.environ\[\s*(?:\w+\.)?([A-Z][A-Z0-9_]*)\s*\](?!\s*=[^=])"""),
    re.compile(r"""\b(?:\w+\.)?([A-Z][A-Z0-9_]*)\s+(?:not\s+)?in\s+os\.environ\b"""),
    re.compile(r"""(?<!envnames)\.get\(\s*(?:\w+\.)?([A-Z][A-Z0-9_]*)\s*[,)]"""),
)


def scanned_files():
    for top in SCANNED:
        for p in sorted((ROOT / top).rglob("*")):
            if not p.is_file() or "__pycache__" in p.parts or any(x.endswith(".egg-info") for x in p.parts):
                continue
            try:
                text = p.read_text()
            except UnicodeDecodeError:
                continue
            yield p, text


def is_shell(path, text):
    first = text.split("\n", 1)[0]
    return path.suffix == ".sh" or (first.startswith("#!") and re.search(r"\b(ba|z)?sh\b", first) is not None)


def test_the_scan_sees_the_sites_it_must():
    rel = {str(p.relative_to(ROOT)) for p, _ in scanned_files()}
    assert {"src/ears/envnames.py", "src/ears/heard.py", "keeper/call-ears", "bin/call-tap"} <= rel
    assert is_shell(ROOT / "bin/call-tap", (ROOT / "bin/call-tap").read_text())
    assert not is_shell(ROOT / "keeper/call-ears", (ROOT / "keeper/call-ears").read_text())


def test_no_voicemode_name_is_read_outside_the_table():
    bad = []
    for p, text in scanned_files():
        if p == ROOT / "src/ears/envnames.py":
            continue
        shell = is_shell(p, text)
        for i, line in enumerate(text.splitlines(), 1):
            for tok in TOKEN.findall(line):
                if tok in envnames.UNTOUCHED:
                    continue
                new = OLD_TO_NEW.get(tok)
                if new is None:
                    bad.append(f"{p.relative_to(ROOT)}:{i}: {tok} is in no table: {line.strip()}")
                elif shell and f"${{{new}-${{{tok}" not in line:
                    bad.append(f"{p.relative_to(ROOT)}:{i}: {tok} outside the presence form "
                               f"${{{new}-${{{tok}...}}: {line.strip()}")
                elif not shell and f'"{new}"' not in line:
                    bad.append(f"{p.relative_to(ROOT)}:{i}: {tok} read apart from {new}: {line.strip()}")
    assert not bad, "\n".join(bad)


def test_no_new_name_is_read_around_the_table():
    """A direct os.environ read of a NEW name would skip its old one: only a line that
    names the old name too (a file that keeps its own pair) may do it."""
    bad = []
    for p, text in scanned_files():
        if p == ROOT / "src/ears/envnames.py" or is_shell(p, text):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for name in DIRECT_READ.findall(line):
                if name in envnames.ALIASES and envnames.ALIASES[name] not in line:
                    bad.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()}")
    assert not bad, "\n".join(bad)


def constants():
    """Every module constant under the scanned dirs whose value is a NEW name in the table."""
    out = {}
    for _, text in scanned_files():
        for name, value in CONST.findall(text):
            if value in envnames.ALIASES:
                out[name] = value
    return out


def const_reads(line, consts):
    found = []
    for rx in CONST_READS:
        found += [c for c in rx.findall(line) if c in consts]
    return list(dict.fromkeys(found))   # one read matched by two forms is one read


def test_no_new_name_is_read_around_the_table_through_a_constant():
    """The mouth keeps many names in constants (SENDMAIL_VAR, SESSION_BIN_ENV, VERIFY_VAR):
    a read through one skips the old name just as a literal would (T5)."""
    consts = constants()
    assert {"SENDMAIL_VAR", "NOTICE_VAR", "SESSION_BIN_ENV", "VERIFY_VAR", "LOG_VAR",
            "HEARD_VAR", "BARGE_WORDS_ENV", "TTS_KEY_ENV"} <= set(consts), consts
    bad = []
    for p, text in scanned_files():
        if p == ROOT / "src/ears/envnames.py" or is_shell(p, text):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for c in const_reads(line, consts):
                if envnames.ALIASES[consts[c]] not in line:
                    bad.append(f"{p.relative_to(ROOT)}:{i}: {c} ({consts[c]}): {line.strip()}")
    assert not bad, "\n".join(bad)


def test_the_hygiene_scan_catches_a_bypass(tmp_path):
    """The scans above fail on what they are meant to: proven on made-up lines."""
    assert TOKEN.findall('x = os.environ.get("VOICEMODE_LISTEN_STT_URL")') == ["VOICEMODE_LISTEN_STT_URL"]
    assert DIRECT_READ.findall('os.environ.get("EARS_CALL_SOCKS")') == ["EARS_CALL_SOCKS"]
    assert DIRECT_READ.findall('os.getenv("EARS_LAG_WARN_S", "2")') == ["EARS_LAG_WARN_S"]
    assert DIRECT_READ.findall('os.environ["EARS_HEARD_EVENT"]') == ["EARS_HEARD_EVENT"]
    assert DIRECT_READ.findall('if os.environ["MOUTH_DIR"] == x:') == ["MOUTH_DIR"]
    assert DIRECT_READ.findall('os.environ["MOUTH_MAILBOX"] = str(box)') == []   # a write
    consts = {"SENDMAIL_VAR": "MOUTH_SENDMAIL", "VERIFY_VAR": "MOUTH_VERIFY_BIN"}
    assert const_reads("explicit = os.environ.get(SENDMAIL_VAR)", consts) == ["SENDMAIL_VAR"]
    assert const_reads("x = os.getenv(notice.SENDMAIL_VAR, '')", consts) == ["SENDMAIL_VAR"]
    assert const_reads("if VERIFY_VAR in os.environ:", consts) == ["VERIFY_VAR"]
    assert const_reads("if notice.VERIFY_VAR not in os.environ:", consts) == ["VERIFY_VAR"]
    assert const_reads("v = os.environ[SENDMAIL_VAR]", consts) == ["SENDMAIL_VAR"]
    assert const_reads("v = (os.environ if env is None else env).get(SENDMAIL_VAR)", consts) == ["SENDMAIL_VAR"]
    assert const_reads("v = envnames.get(SENDMAIL_VAR)", consts) == []
    assert const_reads("if envnames.isset(VERIFY_VAR):", consts) == []
    assert CONST.findall('SENDMAIL_VAR = "MOUTH_SENDMAIL"\nX: str = "MOUTH_DIR"') == [
        ("SENDMAIL_VAR", "MOUTH_SENDMAIL"), ("X", "MOUTH_DIR")]


# --- the copies of a pair, and the readers ---------------------------------------

def test_heards_own_pairs_are_the_tables():
    """heard.py imports nothing outside the standard library, so it keeps a copy."""
    mine = (heard.HEARD_EVENT_ENV, heard.HEARD_CALLER_ENV, heard.HEARD_HOOK_ENV,
            heard.HEARD_BUDGET_ENV, heard.HEARD_MAX_LINES_ENV)
    assert heard.HEARD_ENV_OLD == {n: envnames.ALIASES[n] for n in mine}
    # partials-only is new: it has no old name, in either table
    assert heard.HEARD_PARTIALS_ONLY_ENV not in envnames.ALIASES
    assert heard.HEARD_PARTIALS_ONLY_ENV not in heard.HEARD_ENV_OLD


def test_the_heard_hooks_names_are_no_longer_untouched():
    """EARS-362: VOICEMODE_HEARD_HOOK/_BUDGET/_MAX_LINES are old names now, not shared ones."""
    for old in ("VOICEMODE_HEARD_HOOK", "VOICEMODE_HEARD_BUDGET", "VOICEMODE_HEARD_MAX_LINES"):
        assert old not in envnames.UNTOUCHED
        assert old in OLD_TO_NEW


@pytest.mark.parametrize("new", ["EARS_HEARD_BUDGET", "EARS_HEARD_MAX_LINES"])
def test_heard_reads_the_hooks_old_number_and_the_new_wins(new, monkeypatch):
    old = envnames.ALIASES[new]
    monkeypatch.delenv(new, raising=False)
    monkeypatch.setenv(old, "7")
    assert heard._env_int(new, 99) == 7
    monkeypatch.setenv(new, "11")
    assert heard._env_int(new, 99) == 11
    monkeypatch.setenv(new, "")           # set but empty: the old name is not read, the default is
    assert heard._env_int(new, 99) == 99
    monkeypatch.delenv(new)
    monkeypatch.delenv(old)
    assert heard._env_int(new, 99) == 99


def test_heard_reads_the_hooks_old_off_switch_and_the_new_wins(monkeypatch):
    new, old = "EARS_HEARD_HOOK", "VOICEMODE_HEARD_HOOK"
    monkeypatch.delenv(new, raising=False)
    monkeypatch.delenv(old, raising=False)
    assert not heard.hook_disabled()
    monkeypatch.setenv(old, "off")
    assert heard.hook_disabled()
    monkeypatch.setenv(new, "on")         # new wins: on
    assert not heard.hook_disabled()
    monkeypatch.setenv(new, "")           # set but empty: the old "off" is not read
    assert not heard.hook_disabled()
    monkeypatch.setenv(new, "OFF ")
    assert heard.hook_disabled()


@pytest.mark.parametrize("field, new", [("event", "EARS_HEARD_EVENT"), ("caller", "EARS_HEARD_CALLER")])
def test_heard_reads_the_old_name_and_the_new_wins(field, new, monkeypatch):
    old = envnames.ALIASES[new]
    monkeypatch.delenv(new, raising=False)
    monkeypatch.setenv(old, "old-one")
    assert heard.mutator()[field] == "old-one"
    monkeypatch.setenv(new, "new-one")
    assert heard.mutator()[field] == "new-one"
    monkeypatch.setenv(new, "")           # set but empty: the old name is not read
    assert heard.mutator()[field] != "old-one"


def _call_ears():
    loader = importlib.machinery.SourceFileLoader("call_ears_envnames", str(ROOT / "keeper" / "call-ears"))
    spec = importlib.util.spec_from_loader("call_ears_envnames", loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def test_call_ears_reads_the_old_name_and_the_new_wins(monkeypatch):
    m = _call_ears()
    monkeypatch.delenv("EARS_CALL_SOCKS", raising=False)
    monkeypatch.setenv("VOICEMODE_CALL_EARS_SOCKS", "/old/a.sock:/old/b.sock")
    assert m.socks() == ["/old/a.sock", "/old/b.sock"]
    monkeypatch.setenv("EARS_CALL_SOCKS", "/new.sock")
    assert m.socks() == ["/new.sock"]


def test_call_ears_an_empty_new_name_is_not_passed_over_for_the_old(monkeypatch, tmp_path):
    """Presence, as envnames.get: EARS_CALL_SOCKS="" is set, so the old name is not
    read and the socket search runs (here over an empty home: no socket)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SESSIONPHONE_ROOT", str(tmp_path / "sp"))
    m = _call_ears()
    monkeypatch.setenv("VOICEMODE_CALL_EARS_SOCKS", "/old.sock")
    monkeypatch.setenv("EARS_CALL_SOCKS", "")
    assert m.socks() == []
    monkeypatch.delenv("EARS_CALL_SOCKS")
    assert m.socks() == ["/old.sock"]


def test_call_socks_an_empty_new_name_is_not_passed_over_for_the_old(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SESSIONPHONE_ROOT", str(tmp_path / "sp"))
    monkeypatch.setenv("VOICEMODE_CALL_EARS_SOCKS", "/old.sock")
    monkeypatch.setenv("EARS_CALL_SOCKS", "")
    assert "/old.sock" not in sources.call_socks()


def test_call_socks_reads_the_old_name_and_the_new_wins(monkeypatch):
    monkeypatch.delenv("EARS_CALL_SOCKS", raising=False)
    monkeypatch.setenv("VOICEMODE_CALL_EARS_SOCKS", "/old.sock")
    assert sources.call_socks() == ["/old.sock"]
    monkeypatch.setenv("EARS_CALL_SOCKS", "/new.sock")
    assert sources.call_socks() == ["/new.sock"]


def test_lag_warn_reads_the_old_name_and_the_new_wins(monkeypatch):
    monkeypatch.delenv("EARS_LAG_WARN_S", raising=False)
    monkeypatch.setenv("VOICEMODE_EARS_LAG_WARN_S", "3.5")
    assert lag.lag_warn_s() == 3.5
    monkeypatch.setenv("EARS_LAG_WARN_S", "1.5")
    assert lag.lag_warn_s() == 1.5


def test_the_stt_pin_reads_the_old_names(monkeypatch):
    for k in (*envnames.group("EARS_LISTEN_STT_URL"), *envnames.group("EARS_LISTEN_STT_MODEL")):
        monkeypatch.delenv(k, raising=False)
    assert not stt.stt_pinned()
    monkeypatch.setenv("VOICEMODE_LISTEN_STT_MODEL", "m")
    assert stt.stt_pinned()


def test_the_silero_model_reads_the_old_name(monkeypatch, tmp_path):
    old, new = tmp_path / "old.onnx", tmp_path / "new.onnx"
    old.write_bytes(b"x"); new.write_bytes(b"x")
    monkeypatch.delenv("EARS_SILERO_MODEL", raising=False)
    monkeypatch.setenv("VOICEMODE_SILERO_MODEL", str(old))
    assert silero.model_path() == old
    monkeypatch.setenv("EARS_SILERO_MODEL", str(new))
    assert silero.model_path() == new


def test_the_stt_key_names_the_variable_it_came_from(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    for k in envnames.group("EARS_LISTEN_STT_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("VOICEMODE_LISTEN_STT_KEY", "old-key")
    assert keys.stt_key("http://127.0.0.1:1/v1") == "old-key"
    monkeypatch.setenv("VOICEMODE_LISTEN_STT_KEY", "bad key")
    with pytest.raises(ValueError, match=r"\$VOICEMODE_LISTEN_STT_KEY holds"):
        keys.stt_key("http://127.0.0.1:1/v1")
    monkeypatch.setenv("EARS_LISTEN_STT_KEY", "new-key")
    assert keys.stt_key("http://127.0.0.1:1/v1") == "new-key"


# --- (c) the fence and the alias table, held together ---------------------------

FENCED = sorted(env_fence.FENCE)


def hostile(name, new_value="runner-new", old_value="runner-old"):
    """A runner's env that sets every name of ``name``'s group, to different values."""
    g = envnames.group(name)
    env = {g[0]: new_value}
    for other in g[1:]:
        env[other] = old_value
    return env


def assert_fenced(env, name, fence=None):
    value = (env_fence.FENCE if fence is None else fence)[name]
    for n in envnames.group(name):
        if value is None:
            assert n not in env, f"{n} survived the fence on {name}"
        else:
            assert env[n] == value, f"{n} is not the fence's value for {name}"
    new = envnames.group(name)[0]
    assert envnames.get(new, "<unset>", env) == ("<unset>" if value is None else value)


def test_no_two_fence_entries_disagree_about_one_variable():
    by_group = {}
    for name, value in env_fence.FENCE.items():
        by_group.setdefault(envnames.group(name), set()).add(value)
    assert all(len(v) == 1 for v in by_group.values()), by_group


@pytest.mark.parametrize("name", FENCED)
def test_the_fence_holds_when_both_names_are_set_to_different_values(name):
    env = hostile(name)
    env_fence.apply(env)
    assert_fenced(env, name)


@pytest.mark.parametrize("name", FENCED)
def test_the_fence_holds_when_a_name_is_set_but_empty(name):
    g = envnames.group(name)
    for empty in g:
        env = hostile(name)
        env[empty] = ""
        env_fence.apply(env)
        assert_fenced(env, name)


@pytest.mark.parametrize("name", FENCED)
def test_the_fence_is_on_under_every_name_now(name):
    assert_fenced(dict(os.environ), name)


# A made-up variable for the probe below: in no table until a test puts it there.
PROBE = "T4_FENCE_PROBE"
PROBE_NEW = "T4_FENCE_PROBE_NEW"


@pytest.mark.parametrize("value", [None, "", "fence-value"], ids=["popped", "set-empty", "set"])
def test_a_fenced_variable_given_an_alias_is_fenced_under_both(value, monkeypatch):
    """The mechanism T5 relies on: the day a fenced variable gets a NEW name in the
    table, the fence covers it with no edit to the fence. Shown on a SYNTHETIC fenced
    variable with no alias, given one here, so the probe runs whichever real variables
    already have aliases (those are covered by the tests above). monkeypatch takes the
    variable out of both tables again at teardown."""
    assert PROBE not in env_fence.FENCE and not envnames.group(PROBE)[1:]
    monkeypatch.setitem(env_fence.FENCE, PROBE, value)
    monkeypatch.setitem(envnames.ALIASES, PROBE_NEW, PROBE)
    monkeypatch.setitem(envnames._OLD_TO_NEW, PROBE, PROBE_NEW)
    assert envnames.group(PROBE) == envnames.group(PROBE_NEW) == (PROBE_NEW, PROBE)
    for env in (hostile(PROBE), dict(hostile(PROBE), **{PROBE_NEW: ""})):
        env_fence.apply(env)
        assert_fenced(env, PROBE)


def test_both_fence_tables_name_the_new_name_of_each_variable():
    """The fences name each variable by its NEW name; ``apply``/``fence_test`` reach the
    old one through the group."""
    for table in (env_fence.FENCE, env_fence.MOUTH_TEST_FENCE):
        assert not set(table) & set(OLD_TO_NEW), "an old name in a fence table"


def test_every_renamed_mouth_name_the_conftest_fenced_is_still_fenced():
    """The fences T5 found on the old names, each under its new name now (none dropped)."""
    assert {"MOUTH_DEVICE", "MOUTH_SETTINGS", "MOUTH_VOICE", "MOUTH_SESSION_BIN",
            "MOUTH_VERIFY_BIN", "MOUTH_SENDMAIL", "MOUTH_NOTICE"} <= set(env_fence.FENCE)
    assert {"MOUTH_HEARD_BOX", "MOUTH_MAILBOX", "MOUTH_MAIL_LOG", "MOUTH_TTS_KEY",
            "MOUTH_MPV_SOCKET", "MOUTH_SENDMAIL", "MOUTH_NOTICE"} <= set(env_fence.MOUTH_TEST_FENCE)
    for name in ("MOUTH_SENDMAIL", "MOUTH_SESSION_BIN", "MOUTH_VERIFY_BIN"):
        assert env_fence.FENCE[name] is not None, f"{name} must be SET by the fence, not popped"


# --- (c) the mouth tests' own per-test fence (tests/mouth/conftest.py) -------------------

TEST_FENCED = sorted(env_fence.MOUTH_TEST_FENCE)


def fakes():
    return {n: f"/fenced/{n}" for n, kind in env_fence.MOUTH_TEST_FENCE.items() if kind}


def assert_test_fenced(name, values):
    kind = env_fence.MOUTH_TEST_FENCE[name]
    for n in envnames.group(name):
        if kind is None:
            assert n not in os.environ, f"{n} survived the mouth fence on {name}"
        else:
            assert os.environ[n] == values[name], f"{n} is not the mouth fence's value for {name}"
    want = "<unset>" if kind is None else values[name]
    assert envnames.get(envnames.group(name)[0], "<unset>") == want


@pytest.mark.parametrize("name", TEST_FENCED)
def test_the_mouth_fence_holds_when_both_names_are_set_to_different_values(name):
    values = fakes()
    with pytest.MonkeyPatch.context() as mp:
        for n, v in hostile(name).items():
            mp.setenv(n, v)
        env_fence.fence_test(mp, values)
        assert_test_fenced(name, values)


@pytest.mark.parametrize("name", TEST_FENCED)
def test_the_mouth_fence_holds_when_a_name_is_set_but_empty(name):
    values = fakes()
    for empty in envnames.group(name):
        with pytest.MonkeyPatch.context() as mp:
            for n, v in hostile(name).items():
                mp.setenv(n, v)
            mp.setenv(empty, "")
            env_fence.fence_test(mp, values)
            assert_test_fenced(name, values)


def test_the_mouth_fence_names_its_missing_value():
    with pytest.MonkeyPatch.context() as mp, pytest.raises(KeyError, match="MOUTH_SENDMAIL"):
        env_fence.fence_test(mp, {k: v for k, v in fakes().items() if k != "MOUTH_SENDMAIL"})


def test_the_helpers_reach_both_names(monkeypatch):
    monkeypatch.setenv("VOICEMODE_MOUTH_BARGE", "old")
    monkeypatch.setenv("MOUTH_BARGE", "new")
    env_fence.delenv(monkeypatch, "MOUTH_BARGE")
    assert not envnames.isset("MOUTH_BARGE")
    env_fence.setenv(monkeypatch, "VOICEMODE_MOUTH_BARGE", "x")   # either name names the group
    assert os.environ["MOUTH_BARGE"] == os.environ["VOICEMODE_MOUTH_BARGE"] == "x"
    env_fence.delenv(monkeypatch, "TALK_UNIX_SOCKET")             # a name with no alias: itself
    assert "TALK_UNIX_SOCKET" not in os.environ


def test_operator_configs_own_pair_is_the_tables():
    """operator_config imports only the standard library, so it keeps a copy, like heard."""
    from ears import operator_config as oc

    assert oc.SESSION_BIN_ENV_OLD == envnames.ALIASES[oc.SESSION_BIN_ENV]
