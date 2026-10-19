"""The audio devices file is a contract (EARS-275, OBSV-52): docs/audio-devices.md and
docs/audio-devices.schema.json list the same fields, and keeper/input-watch.swift writes
exactly that.

Every test that runs the binary builds it into a tmp dir and points it at tmp paths only:
never ~/.cache/ears/run, never the live devices.json, never a running input-watch. The
binary tests use --fixture (no Core Audio at all) except the one --once read, which is a
single read-only Core Audio read that changes nothing.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "keeper/input-watch.swift"
DOC = (ROOT / "docs/audio-devices.md").read_text()
SCHEMA = json.loads((ROOT / "docs/audio-devices.schema.json").read_text())
STAMPS = {"version", "writer", "pid", "started_at", "written_at", "seq"}

# --- a small JSON Schema validator: the subset audio-devices.schema.json uses -------------

TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "null": lambda v: v is None,
}
KNOWN = {"$schema", "$id", "$defs", "$ref", "title", "description", "type", "required",
         "additionalProperties", "properties", "items", "anyOf", "const", "enum", "minimum"}


def errors(v, s, where="$"):
    """Every way v fails schema s (the root SCHEMA's $defs), as 'path: why' strings."""
    if "$ref" in s:
        assert s["$ref"].startswith("#/$defs/"), s["$ref"]
        return errors(v, SCHEMA["$defs"][s["$ref"].split("/")[-1]], where)
    if "anyOf" in s:
        return [] if any(not errors(v, o, where) for o in s["anyOf"]) else [f"{where}: matches no anyOf"]
    out = []
    t = s.get("type")
    if t is not None and not any(TYPES[x](v) for x in ([t] if isinstance(t, str) else t)):
        return [f"{where}: {v!r} is not {t}"]
    if "const" in s and v != s["const"]:
        out.append(f"{where}: {v!r} != const {s['const']!r}")
    if "enum" in s and v not in s["enum"]:
        out.append(f"{where}: {v!r} not in enum")
    if "minimum" in s and v < s["minimum"]:
        out.append(f"{where}: {v!r} < {s['minimum']}")
    if isinstance(v, dict):
        props = s.get("properties", {})
        out += [f"{where}: missing {k}" for k in s.get("required", []) if k not in v]
        if s.get("additionalProperties") is False:
            out += [f"{where}: extra {k}" for k in v if k not in props]
        for k, sub in props.items():
            if k in v:
                out += errors(v[k], sub, f"{where}.{k}")
    if isinstance(v, list) and "items" in s:
        for i, x in enumerate(v):
            out += errors(x, s["items"], f"{where}[{i}]")
    return out


def _keywords(s):
    if isinstance(s, dict):
        yield from (k for k in s if k not in ("properties", "$defs"))
        for k, v in s.items():
            if k in ("properties", "$defs"):
                for sub in v.values():
                    yield from _keywords(sub)
            elif isinstance(v, (dict, list)):
                yield from _keywords(v)
    elif isinstance(s, list):
        for x in s:
            yield from _keywords(x)


# --- T1: the two docs agree --------------------------------------------------------------

def _doc_fields():
    top, dev = set(), set()
    for m in re.finditer(r"^\| (device )?`\.?(\w+)` \|", DOC, re.M):
        (dev if m.group(1) else top).add(m.group(2))
    return top, dev


def test_T1_schema_is_2020_12_and_strict():
    assert SCHEMA["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert SCHEMA["type"] == "object" and SCHEMA["additionalProperties"] is False
    assert set(SCHEMA["required"]) == set(SCHEMA["properties"])      # every field, always there
    dev = SCHEMA["$defs"]["device"]
    assert dev["additionalProperties"] is False and set(dev["required"]) == set(dev["properties"])
    unknown = set(_keywords(SCHEMA)) - KNOWN
    assert not unknown, f"the in-test validator does not check {unknown}: teach it, or drop them"


def test_T1_doc_table_and_schema_name_the_same_fields():
    top, dev = _doc_fields()
    assert top == set(SCHEMA["properties"]), (top ^ set(SCHEMA["properties"]))
    assert dev == set(SCHEMA["$defs"]["device"]["properties"])


def test_T1_doc_and_schema_list_the_same_transports():
    row = next(l for l in DOC.splitlines() if l.startswith("| device `.transport`"))
    named = re.findall(r"`(\w+)`", row.split("one of", 1)[1])
    assert named == SCHEMA["$defs"]["device"]["properties"]["transport"]["enum"]


def test_T1_doc_states_the_stale_rule_and_both_30s_checks():
    # N1 (design review 1747): the backstop and the own-file pid re-read, said together
    assert "**The 30 s backstop.**" in DOC and "**The 30 s own-file check.**" in DOC
    assert "no Core Audio, no spawn" in DOC
    assert "kill(pid, 0)" in DOC and "EPERM" in DOC and "Readers MUST ignore unknown fields" in DOC


# --- the binary --------------------------------------------------------------------------

@pytest.fixture(scope="session")
def watch(tmp_path_factory):
    if sys.platform != "darwin" or not shutil.which("swiftc"):
        pytest.skip("input-watch builds with swiftc on macOS only")
    out = tmp_path_factory.mktemp("input-watch") / "input-watch"
    subprocess.run(["swiftc", "-O", "-swift-version", "5", "-o", str(out), str(SRC)],
                   check=True, capture_output=True, timeout=300)
    return out


def dev(name, uid, transport):
    return {"name": name, "uid": uid, "transport": transport}


MIC = dev("mic", "usb-mic-1", "usb")
BUILTIN_MIC = dev("MacBook Pro Microphone", "BuiltInMicrophoneDevice", "builtin")
SPEAKERS = dev("MacBook Pro Speakers", "BuiltInSpeakerDevice", "builtin")
AIRPODS = dev("airpods", None, "bluetooth")
AGG = dev("AirPods + BlackHole 2ch", "com.example.agg", "aggregate")
TWIN = dev("mic", "usb-mic-2", "usb")            # a second device with the same name

# A: unsorted on purpose; the writer sorts by name, then uid
A = {"default_input": MIC, "default_output": SPEAKERS,
     "inputs": [MIC, BUILTIN_MIC, TWIN, AIRPODS], "outputs": [SPEAKERS, AIRPODS]}
B = dict(A, default_output=AGG, outputs=[SPEAKERS, AIRPODS, AGG])       # outputs only
C = dict(A, default_input=BUILTIN_MIC, inputs=[BUILTIN_MIC])            # inputs change


def expected(step):
    key = lambda d: (d["name"], d["uid"] or "")
    return {"default_input": step["default_input"], "default_output": step["default_output"],
            "inputs": sorted(step["inputs"], key=key), "outputs": sorted(step["outputs"], key=key)}


def run(watch, tmp_path, *args, steps=None):
    """Run the binary; return (rc, stdout, stderr, the child's pid)."""
    argv = [str(watch)]
    if steps is not None:
        fx = tmp_path / "steps.json"
        fx.write_text(json.dumps(steps))
        argv += ["--fixture", str(fx)]
    p = subprocess.Popen(argv + [str(a) for a in args], stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True)
    out, err = p.communicate(timeout=30)
    return p.returncode, out, err, p.pid


def content(doc):
    return {k: v for k, v in doc.items() if k not in STAMPS}


def no_temp_files(d):
    return not [p.name for p in d.iterdir() if ".tmp" in p.name]


def a_dead_pid():
    p = subprocess.Popen(["/usr/bin/true"])
    p.wait()
    try:
        os.kill(p.pid, 0)
    except ProcessLookupError:
        return p.pid
    pytest.skip(f"pid {p.pid} was reused at once")


# --- T2: the binary writes what the schema says ------------------------------------------

def test_T2_fixture_output_matches_the_schema_and_the_snapshot(watch, tmp_path):
    js, st = tmp_path / "devices.json", tmp_path / "input-watch.state"
    before = time.time()
    rc, out, err, pid = run(watch, tmp_path, "--json", js, st, steps=[A])
    assert rc == 0, err
    raw = js.read_text()
    doc = json.loads(raw)
    assert errors(doc, SCHEMA) == []
    assert content(doc) == expected(A)
    assert doc["pid"] == pid and doc["seq"] == 1 and doc["version"] == 1 and doc["writer"] == "input-watch"
    assert before - 1 <= doc["started_at"] <= doc["written_at"] <= time.time() + 1
    # the encoding docs/audio-devices.md promises: one line, sorted keys, ms stamps, 0644
    assert raw.endswith("\n") and raw.count("\n") == 1
    assert list(doc) == sorted(doc) and all(list(d) == sorted(d) for d in doc["inputs"])
    assert re.search(r'"started_at":\d+(\.\d{1,3})?,', raw) and re.search(r'"written_at":\d+(\.\d{1,3})?\}', raw)
    assert js.stat().st_mode & 0o777 == 0o644
    assert no_temp_files(tmp_path)


def test_T2_the_validator_has_teeth(watch, tmp_path):
    js = tmp_path / "devices.json"
    assert run(watch, tmp_path, "--json", js, steps=[A])[0] == 0
    good = json.loads(js.read_text())
    for bad in (dict(good, extra=1), {k: v for k, v in good.items() if k != "seq"},
                dict(good, version=2), dict(good, pid="1"), dict(good, default_output={"name": "x"}),
                dict(good, outputs=[dict(SPEAKERS, transport="tin-can")])):
        assert errors(bad, SCHEMA), bad


def test_T2_no_default_device_is_null(watch, tmp_path):
    js = tmp_path / "devices.json"
    step = {"default_input": None, "default_output": None, "inputs": [], "outputs": []}
    assert run(watch, tmp_path, "--json", js, steps=[step])[0] == 0
    doc = json.loads(js.read_text())
    assert errors(doc, SCHEMA) == [] and content(doc) == step


# --- T3: the legacy file is byte-for-byte as before ---------------------------------------

def test_T3_legacy_state_file_is_unchanged(watch, tmp_path):
    js, st = tmp_path / "devices.json", tmp_path / "input-watch.state"
    assert run(watch, tmp_path, "--json", js, st, steps=[A])[0] == 0
    # line 1 the default input, then input names deduped (two "mic"s) and sorted
    assert st.read_bytes() == b"mic\nMacBook Pro Microphone\nairpods\nmic\n"


def test_T3_legacy_with_no_default_input_has_an_empty_first_line(watch, tmp_path):
    st = tmp_path / "input-watch.state"
    step = dict(A, default_input=None)
    assert run(watch, tmp_path, "--json", tmp_path / "d.json", st, steps=[step])[0] == 0
    assert st.read_bytes() == b"\nMacBook Pro Microphone\nairpods\nmic\n"


# --- T4: written on change only, each file on its own change -----------------------------

def test_T4_writes_only_on_change(watch, tmp_path):
    js, st = tmp_path / "devices.json", tmp_path / "input-watch.state"
    rc, out, err, _ = run(watch, tmp_path, "--json", js, st, steps=[A, A, B, B])
    assert rc == 0, err
    assert len(out.splitlines()) == 2                     # one line per write: A, then B
    doc = json.loads(js.read_text())
    assert doc["seq"] == 2 and content(doc) == expected(B)
    assert "default out: AirPods + BlackHole 2ch" in out.splitlines()[1]
    # an output-only change leaves the legacy file alone: it was last written at A,
    # before the JSON file's write at B (each write is JSON first, then legacy)
    assert st.stat().st_mtime_ns < js.stat().st_mtime_ns
    assert st.read_bytes() == b"mic\nMacBook Pro Microphone\nairpods\nmic\n"
    assert no_temp_files(tmp_path)


def test_T4_an_input_change_rewrites_both(watch, tmp_path):
    js, st = tmp_path / "devices.json", tmp_path / "input-watch.state"
    assert run(watch, tmp_path, "--json", js, st, steps=[A, C])[0] == 0
    assert json.loads(js.read_text())["seq"] == 2
    assert st.stat().st_mtime_ns >= js.stat().st_mtime_ns
    assert st.read_bytes() == b"MacBook Pro Microphone\nMacBook Pro Microphone\n"


# --- T5: one real, read-only Core Audio read ---------------------------------------------

def test_T5_once_reads_core_audio_and_matches_the_schema(watch, tmp_path):
    js, st = tmp_path / "devices.json", tmp_path / "input-watch.state"
    rc, out, err, pid = run(watch, tmp_path, "--once", "--json", js, st)
    assert rc == 0, err
    doc = json.loads(js.read_text())
    assert errors(doc, SCHEMA) == [] and doc["pid"] == pid and doc["seq"] == 1
    assert st.read_text().splitlines()[0] == (doc["default_input"] or {}).get("name", "")
    assert no_temp_files(tmp_path)


# --- T6: the watcher never spawns --------------------------------------------------------

def test_T6_the_watcher_spawns_nothing():
    src = SRC.read_text()
    for call in ("Process(", "posix_spawn", "system(", "NSTask", "popen("):
        assert call not in src, call


def test_usage_errors_exit_2(watch, tmp_path):
    js = tmp_path / "devices.json"
    for argv in (["--once"], ["--fixture", tmp_path / "x.json"], ["--json"], ["--bogus"],
                 ["--once", "--fixture", tmp_path / "x.json", "--json", js], ["a", "b"]):
        assert run(watch, tmp_path, *argv)[0] == 2, argv
    assert not js.exists()


# --- T11: a one-shot never replaces a live writer's file (Q10, foreman ruling) ------------

def _foreign(pid):
    return dict(expected(C), version=1, writer="input-watch", pid=pid, started_at=1.0,
                written_at=2.0, seq=9)


@pytest.mark.parametrize("mode", ["--fixture", "--once"])
def test_T11_refuses_a_file_a_live_pid_owns(watch, tmp_path, mode):
    js, st = tmp_path / "devices.json", tmp_path / "input-watch.state"
    js.write_text(json.dumps(_foreign(os.getpid())))        # pytest: alive, not the child
    before = js.read_bytes()
    steps = [A] if mode == "--fixture" else None
    extra = ["--once"] if mode == "--once" else []
    rc, out, err, _ = run(watch, tmp_path, *extra, "--json", js, st, steps=steps)
    assert rc == 3 and "REFUSED" in err and str(os.getpid()) in err
    assert js.read_bytes() == before and not st.exists() and no_temp_files(tmp_path)


@pytest.mark.parametrize("mode", ["--fixture", "--once"])
@pytest.mark.parametrize("onDisk", ["dead pid", "no file", "not json"])
def test_T11_writes_over_a_dead_pid_or_no_file(watch, tmp_path, mode, onDisk):
    js = tmp_path / "devices.json"
    if onDisk == "dead pid":
        js.write_text(json.dumps(_foreign(a_dead_pid())))
    elif onDisk == "not json":
        js.write_text("{half")
    steps = [A] if mode == "--fixture" else None
    extra = ["--once"] if mode == "--once" else []
    rc, out, err, pid = run(watch, tmp_path, *extra, "--json", js, steps=steps)
    assert rc == 0, err
    assert json.loads(js.read_text())["pid"] == pid


# --- T12: the backstop's re-assert (one function: the 30 s timer and the fixture call it) -

def test_T12_reasserts_over_a_live_foreign_pid(watch, tmp_path):
    js = tmp_path / "devices.json"
    steps = [A, {"foreign": _foreign(os.getpid())}, {"reassert": True}]
    rc, out, err, pid = run(watch, tmp_path, "--json", js, steps=steps)
    assert rc == 0, err
    doc = json.loads(js.read_text())
    assert doc["pid"] == pid and doc["seq"] == 2 and content(doc) == expected(A)
    # N2: over a LIVE foreign pid the daemon says so on stderr
    assert f"live pid {os.getpid()}" in err and "re-asserting" in err
    assert no_temp_files(tmp_path)


def test_T12_reasserts_over_a_dead_foreign_pid_quietly(watch, tmp_path):
    js = tmp_path / "devices.json"
    steps = [A, {"foreign": _foreign(a_dead_pid())}, {"reassert": True}]
    rc, out, err, pid = run(watch, tmp_path, "--json", js, steps=steps)
    assert rc == 0 and err == ""
    doc = json.loads(js.read_text())
    assert doc["pid"] == pid and doc["seq"] == 2 and content(doc) == expected(A)


def test_T12_reasserts_a_deleted_file(watch, tmp_path):
    # N4: someone deleted it; the next backstop tick puts it back
    js = tmp_path / "devices.json"
    rc, out, err, pid = run(watch, tmp_path, "--json", js, steps=[A, {"foreign": None}, {"reassert": True}])
    assert rc == 0, err
    doc = json.loads(js.read_text())
    assert doc["pid"] == pid and doc["seq"] == 2 and content(doc) == expected(A)


def test_T12_reassert_on_its_own_file_writes_nothing(watch, tmp_path):
    js = tmp_path / "devices.json"
    rc, out, err, pid = run(watch, tmp_path, "--json", js, steps=[A, {"reassert": True}, {"reassert": True}])
    assert rc == 0 and err == ""
    assert json.loads(js.read_text())["seq"] == 1 and len(out.splitlines()) == 1


def test_T12_why_the_reassert_exists(watch, tmp_path):
    # without it, the same snapshot again does not rewrite: content is compared in memory,
    # so a stray writer's file would stand until the next real change
    js = tmp_path / "devices.json"
    stray = _foreign(os.getpid())
    assert run(watch, tmp_path, "--json", js, steps=[A, {"foreign": stray}, A])[0] == 0
    assert json.loads(js.read_text()) == stray
