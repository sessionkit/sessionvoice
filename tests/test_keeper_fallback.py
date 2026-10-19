"""FOLLOW mode never follows the Mac onto a virtual cable (EARS-2).

At Sun 2030-06-09 the AirPods left and macOS made BlackHole 16ch the
default input; the keeper followed it and the ears heard nothing for 12 minutes.
The output was "AirPods + BlackHole 2ch", which without the AirPods plays only
into the cable. These run the keeper's decision functions alone (pick_input,
pick_output), and check_output against a fake SwitchAudioSource on PATH, so no
real audio device is read or changed.
"""
import json
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

KEEPER = Path(__file__).resolve().parents[1] / "keeper" / "ears-keeper.sh"

AIRPODS_IN = "airpods"
OUTPUTS = [
    "airpods", "BlackHole 16ch", "BlackHole 2ch", "MacBook Pro Speakers",
    "Microsoft Teams Audio", "AirPods + BlackHole 16ch", "AirPods + BlackHole 2ch",
    "Speakers + AirPods",
]
NO_AIRPODS = [o for o in OUTPUTS if o != "airpods"]


def _between(start: str, end: str) -> str:
    s = KEEPER.read_text()
    return s[s.index(start):s.index(end)]


def _bash(script: str, env: dict | None = None) -> str:
    # The keeper runs under `set -uo pipefail`; so do these, or a pipeline that only
    # breaks under pipefail passes here and fails live.
    out = subprocess.run(["bash", "-c", "set -uo pipefail\n" + script], env=env or dict(os.environ),
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def _decide(call: str, *args: str) -> str:
    q = " ".join("'" + a.replace("'", "'\\''") + "'" for a in args)
    return _bash(_between("# --- decisions", "# --- end decisions") + f"\n{call} {q}")


# -- input --------------------------------------------------------------------

@pytest.mark.parametrize("dev", ["BlackHole 16ch", "BlackHole 2ch", "Microsoft Teams Audio",
                                 "blackhole 16ch"])
@pytest.mark.parametrize("builtin", ["MacBook Pro Microphone", "MacBook Air Microphone"])
def test_virtual_default_input_falls_back_to_the_builtin_mic(dev, builtin):
    """The built-in the lookup found (EARS-341 do-007): an Air's name works, not a literal."""
    assert _decide("pick_input", "default", dev, builtin) == builtin


def test_with_no_builtin_known_a_virtual_default_starts_nothing():
    """A Mac with no built-in mic (a mini): no name that names nothing, as the old literal did."""
    assert _decide("pick_input", "default", "BlackHole 2ch", "") == ""


def test_EARS_BUILTIN_MIC_still_overrides():
    env = dict(os.environ, EARS_BUILTIN_MIC="My Mic")
    q = _between("# --- decisions", "# --- end decisions")
    assert _bash(q + "\npick_input default 'BlackHole 2ch' ''", env) == "My Mic"
    assert _bash(q + "\nbuiltin_pick 'MacBook Air Microphone' 'MacBook Air Microphone'", env) == "My Mic"


# -- the built-in mic, by transport (EARS-341 do-007, design C6) -----------------

INPUTS = "airpods\nBlackHole 16ch\nMacBook Air Microphone\nUSB Mic"


def test_the_builtin_is_the_input_whose_transport_is_builtin():
    """devices.json's transport wins over any name: a renamed or foreign-language built-in."""
    assert _decide("builtin_pick", "Micro interne", INPUTS) == "Micro interne"


@pytest.mark.parametrize("builtins, want", [
    ("External Microphone\nMacBook Air Microphone", "MacBook Air Microphone"),   # sorted: the jack first
    ("External Microphone\nMacBook Pro Microphone", "MacBook Pro Microphone"),
    ("Built-in Input\nBuilt-in Microphone", "Built-in Microphone"),
    ("External Microphone\nMicro interne", "External Microphone"),   # localized: none fits, the first (residual)
])
def test_of_two_builtin_inputs_the_named_one_is_the_mic(builtins, want):
    """EARS-341 do-010: a headset on the jack ("External Microphone") is likely `builtin` too
    (the Mac's own codec; UNMEASURED on real hardware) and sorts first. The lid does not shut it."""
    assert _decide("builtin_pick", builtins, INPUTS + "\n" + builtins) == want


@pytest.mark.parametrize("name", ["MacBook Air Microphone", "MacBook Pro Microphone", "Built-in Microphone"])
def test_with_devices_json_stale_the_name_pattern_stands_in(name):
    assert _decide("builtin_pick", "", f"airpods\n{name}\nUSB Mic") == name


@pytest.mark.parametrize("inputs", ["airpods\nUSB Mic", "", "MacBook Pro Microphone (2)\nMy MacBook Microphones"])
def test_no_builtin_found_is_empty(inputs):
    assert _decide("builtin_pick", "", inputs) == ""


@pytest.mark.parametrize("dev", ["airpods", "MacBook Pro Microphone", "Jabra Evolve2"])
def test_a_real_default_input_is_followed(dev):
    assert _decide("pick_input", "default", dev, "MacBook Pro Microphone") == dev


def test_no_default_input_stays_none():
    assert _decide("pick_input", "default", "", "MacBook Pro Microphone") == ""


@pytest.mark.parametrize("pin", ["airpods", "BlackHole 2ch"])
def test_an_input_pin_wins_even_over_a_virtual_default(pin):
    """call-tap pins the ears to the AirPods, then sets the Mac's input to BlackHole 16ch."""
    assert _decide("pick_input", pin, "BlackHole 16ch") == pin


# -- output -------------------------------------------------------------------

def _out(want: str, cur: str, outputs: list[str]) -> str:
    return _decide("pick_output", want, cur, "\n".join(outputs))


@pytest.mark.parametrize("dev", ["BlackHole 2ch", "BlackHole 16ch", "Microsoft Teams Audio"])
def test_virtual_default_output_falls_back_to_the_builtin_speakers(dev):
    assert _out("default", dev, OUTPUTS) == "MacBook Pro Speakers"


@pytest.mark.parametrize("multi", ["AirPods + BlackHole 2ch", "AirPods + BlackHole 16ch",
                                   "Speakers + AirPods"])
def test_multi_output_with_its_airpods_gone_falls_back(multi):
    """The case: the aggregate is still listed and still the default."""
    assert _out("default", multi, NO_AIRPODS) == "MacBook Pro Speakers"


@pytest.mark.parametrize("multi", ["AirPods + BlackHole 2ch", "Speakers + AirPods"])
def test_multi_output_with_its_airpods_present_is_left_alone(multi):
    """The call-tap speaker during a call, AirPods in."""
    assert _out("default", multi, OUTPUTS) == ""


def test_multi_output_with_airpods_present_in_a_long_list_is_left_alone():
    """Review find: `grep | grep -q` under pipefail SIGPIPEd on a long list and read
    the AirPods as gone, moving a live speaker to the laptop."""
    many = ["Theo's AirPods Pro"] + OUTPUTS[1:] + [f"Filler device {i}" for i in range(5000)]
    assert _out("default", "AirPods + BlackHole 2ch", many) == ""


def test_a_multi_output_without_airpods_is_left_alone():
    assert _out("default", "Speakers + Display", ["MacBook Pro Speakers", "Speakers + Display"]) == ""


@pytest.mark.parametrize("dev", ["airpods", "MacBook Pro Speakers", ""])
def test_a_real_or_missing_default_output_is_left_alone(dev):
    assert _out("default", dev, NO_AIRPODS) == ""


@pytest.mark.parametrize("cur", ["AirPods + BlackHole 2ch", "BlackHole 2ch"])
def test_an_input_pin_leaves_the_output_alone(cur):
    """Pinned = a call is wired up (docs/call-tap.md step 2): hands off the speaker."""
    assert _out("airpods", cur, NO_AIRPODS) == ""


# -- check_output against a fake SwitchAudioSource ------------------------------
#
# EARS-275: check_output reads the default output and the output list from input-watch's
# JSON file (docs/audio-devices.md) while it is fresh, and asks SwitchAudioSource only when
# it is missing or stale, or to SET. Every case below runs twice, source = file | sas, and
# must decide the same either way. In file mode the fake's reads FAIL, so a decision can
# only come from the file (T0: it would catch AUDIO_DEVICES drifting out of the region
# these tests slice), and zero reads are allowed (T8). AUDIO_DEVICES_FILE is always a tmp
# path: a host's real devices.json never decides a test.

SOURCE_NOTE = "note: output check:"


def _device(name):
    return {"name": name, "uid": None, "transport": "unknown"}


def _devices_doc(current, outputs, **over):
    """A devices.json the way input-watch writes it; pid = this pytest process (alive)."""
    doc = {"version": 1, "writer": "input-watch", "pid": os.getpid(), "started_at": 1.0,
           "written_at": 2.0, "seq": 1, "default_input": None,
           "default_output": _device(current) if current else None,
           "inputs": [], "outputs": [_device(o) for o in outputs]}
    doc.update(over)
    return doc


@dataclass
class Checked:
    out: str            # stdout without the source-flip notes
    sets: str           # one line per `SwitchAudioSource -t output -s X`
    reads: list[str]    # every `-c`/`-a` read, in order
    notes: list[str]    # the source-flip notes


def _fake_sas_bin(tmp_path):
    """bin/SwitchAudioSource: answers reads from tmp files (logging each to reads.log, and
    failing while reads-fail exists), logs every -s to sas.log. Written once per tmp_path."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    sets, reads = tmp_path / "sas.log", tmp_path / "reads.log"
    sas = bindir / "SwitchAudioSource"
    if not sas.exists():
        sas.write_text(f"""#!/bin/bash
case "$*" in
  "-c -t output"|"-a -t output")
    echo "$*" >> {reads}
    [ -e {tmp_path}/reads-fail ] && exit 1
    if [ "$1" = -c ]; then cat {tmp_path}/current; else cat {tmp_path}/all; fi ;;
  "-t output -s "*) echo "$*" >> {sets}; printf '%s\\n' "${{4}}" > {tmp_path}/current ;;
  *) echo "unexpected: $*" >> {sets}; exit 1 ;;
esac
""")
        sas.chmod(sas.stat().st_mode | stat.S_IEXEC)
    return bindir


def _check_output(tmp_path, current, outputs, conf, *, devices=None, reads_fail=False,
                  follow=1, fallback=1, checks=1, between=""):
    """Run check_output `checks` times (shell `between` runs between checks) against a fake
    SwitchAudioSource on PATH. devices: None = no devices.json, a dict = its JSON, a str =
    its raw text."""
    bindir = _fake_sas_bin(tmp_path)
    sets, reads = tmp_path / "sas.log", tmp_path / "reads.log"
    for f in (sets, reads):
        f.unlink(missing_ok=True)
    (tmp_path / "current").write_text(current + "\n")
    (tmp_path / "all").write_text("\n".join(outputs) + "\n")
    (tmp_path / "reads-fail").unlink(missing_ok=True)
    if reads_fail:
        (tmp_path / "reads-fail").touch()
    js = tmp_path / "devices.json"
    js.unlink(missing_ok=True)
    if devices is not None:
        js.write_text(devices if isinstance(devices, str) else json.dumps(devices))
    cf = tmp_path / "ears.json"
    if conf is None:
        cf.unlink(missing_ok=True)
    else:
        cf.write_text(json.dumps(conf))
    s = KEEPER.read_text()
    body = (s[s.index("conf() {"):s.index("target() {")]
            + s[s.index("OUT_EVERY="):s.index("listen_on() {")])
    script = (f"FOLLOW={follow}\nnote() {{ echo \"note: $*\"; }}\n"
              f"mail_me() {{ echo \"mail: $1\"; }}\n{body}\n"
              + f"\n{between}\n".join(["check_output"] * checks) + "\n")
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", EARS_CONF=str(cf),
               EARS_OUTPUT_FALLBACK=str(fallback), AUDIO_DEVICES_FILE=str(js))
    out = _bash("CONF=\"$EARS_CONF\"\n" + script, env)
    lines = out.splitlines()
    return Checked(out="\n".join(l for l in lines if not l.startswith(SOURCE_NOTE)),
                   sets=sets.read_text() if sets.exists() else "",
                   reads=reads.read_text().splitlines() if reads.exists() else [],
                   notes=[l for l in lines if l.startswith(SOURCE_NOTE)])


@pytest.fixture(params=["sas", "file"])
def fake_sas(request, tmp_path):
    """check_output, decided from SwitchAudioSource (no devices.json) or from a fresh
    devices.json (and the fake's reads fail); returns (stdout, the -s log) either way."""
    source = request.param
    _fake_sas_bin(tmp_path)   # now: a test may edit it before it runs (tries-once)

    def run(current: str, outputs: list[str], conf: dict | None, *, follow: int = 1,
            fallback: int = 1, checks: int = 1) -> tuple[str, str]:
        file = source == "file"
        r = _check_output(tmp_path, current, outputs, conf, follow=follow, fallback=fallback,
                          checks=checks, reads_fail=file,
                          devices=_devices_doc(current, outputs) if file else None)
        ran = checks if follow == 1 and fallback == 1 else 0
        if file:
            assert r.reads == [] and r.notes == [], (r.reads, r.notes)   # T8: zero reads
        else:
            assert r.reads == ["-c -t output", "-a -t output"] * ran, r.reads
            assert len(r.notes) == (1 if ran else 0), r.notes          # one note, not one a check
        return r.out, r.sets

    return run


def test_check_output_moves_a_dead_multi_output_to_the_speakers(fake_sas):
    out, log = fake_sas("AirPods + BlackHole 2ch", NO_AIRPODS, {"input": "default"})
    assert log.strip() == "-t output -s MacBook Pro Speakers"
    assert "mail: ears: speaker -> MacBook Pro Speakers" in out


def test_check_output_with_no_ears_json_follows_the_default(fake_sas):
    _, log = fake_sas("BlackHole 2ch", OUTPUTS, None)
    assert log.strip() == "-t output -s MacBook Pro Speakers"


def test_check_output_leaves_a_live_call_tap_speaker_alone(fake_sas):
    out, log = fake_sas("AirPods + BlackHole 2ch", OUTPUTS, {"input": "default"})
    assert log == "" and out == ""


def test_check_output_hands_off_while_the_input_is_pinned(fake_sas):
    out, log = fake_sas("AirPods + BlackHole 2ch", NO_AIRPODS, {"input": "airpods"})
    assert log == "" and out == ""


def test_check_output_does_nothing_in_pinned_mode_or_when_turned_off(fake_sas):
    assert fake_sas("BlackHole 2ch", OUTPUTS, None, follow=0) == ("", "")
    assert fake_sas("BlackHole 2ch", OUTPUTS, None, fallback=0) == ("", "")


def test_check_output_tries_once_when_the_switch_does_not_take(fake_sas, tmp_path):
    """If macOS keeps the dead device, the keeper does not retry every check."""
    sas = tmp_path / "bin" / "SwitchAudioSource"
    sas.write_text(sas.read_text().replace(
        "printf '%s\\n' \"${4}\" > " + f"{tmp_path}/current", "exit 1"))
    out, log = fake_sas("BlackHole 2ch", OUTPUTS, None, checks=3)
    assert log.count("-t output -s") == 1
    assert "FAILED" in out


def test_out_every_zero_does_not_kill_the_loop():
    """EARS_OUT_EVERY=0 means no backstop; `loops % 0` used to end the keeper."""
    s = KEEPER.read_text()
    line = next(l for l in s.splitlines() if "loops % OUT_EVERY" in l).strip()
    assert line.startswith("elif ")
    # Inside a while loop, as in the keeper: bash aborts the whole compound command
    # on an arithmetic error, so the old line ended the loop and the keeper with it.
    script = ("OUT_EVERY=0; out_due=0; loops=1; check_output() { echo checked; }\n"
              "while :; do\nif false; then :\n" + line + "\necho alive; break; done")
    assert _bash(script) == "alive"


def test_check_output_without_speakers_notes_and_switches_nothing(fake_sas):
    out, log = fake_sas("BlackHole 2ch", ["BlackHole 2ch", "airpods"], None)
    assert log == ""
    assert "no 'MacBook Pro Speakers' to fall back to" in out


@pytest.mark.parametrize("stable,due", [("0", "1"), ("1", "1"), ("2", "2"), ("5", "5")])
def test_an_input_change_always_schedules_an_output_check(stable, due):
    """EARS_STABLE=0 set out_due to 0, and `[ out_due -gt 0 ]` never fired: an AirPods
    drop then left the dead multi-output alone until the backstop, or for ever with
    EARS_OUT_EVERY=0. The check is due at least one loop after the change."""
    s = KEEPER.read_text()
    line = next(l for l in s.splitlines() if 'prev="$now"; out_due=' in l).strip()
    assert _bash(f"STABLE={stable}; now=x\n{line}\necho \"$out_due\"") == due


# -- EARS-275: the output check reads devices.json while it is fresh ----------------------

def _a_dead_pid():
    p = subprocess.Popen(["/usr/bin/true"])
    p.wait()
    try:
        os.kill(p.pid, 0)
    except ProcessLookupError:
        return p.pid
    pytest.skip(f"pid {p.pid} was reused at once")


def _audio_outputs(tmp_path, devices):
    """audio_outputs alone: (exit code, stdout) for a devices.json (None = no file)."""
    js = tmp_path / "devices.json"
    js.unlink(missing_ok=True)
    if devices is not None:
        js.write_text(devices if isinstance(devices, str) else json.dumps(devices))
    s = KEEPER.read_text()
    body = s[s.index("OUT_EVERY="):s.index("check_output() {")]
    out = subprocess.run(["bash", "-c", "set -uo pipefail\n" + body + "\naudio_outputs"],
                         env=dict(os.environ, AUDIO_DEVICES_FILE=str(js)),
                         capture_output=True, text=True)
    return out.returncode, out.stdout


def test_T0_audio_devices_is_assigned_inside_the_region_the_tests_slice():
    """Review F-1: outside OUT_EVERY=..listen_on(), set -u kills audio_outputs in every
    sliced test, every case silently falls back, and the file path is never tested."""
    s = KEEPER.read_text()
    region = s[s.index("OUT_EVERY="):s.index("listen_on() {")]
    assert 'AUDIO_DEVICES="${AUDIO_DEVICES_FILE:-' in region
    assert s.count("AUDIO_DEVICES=") == 1


def test_the_watcher_is_started_with_the_json_file_and_never_deletes_it():
    s = KEEPER.read_text()
    up = s[s.index("watch_up() {"):s.index("watch_down() {")]
    assert '"$WATCH_BIN" "$WATCH_STATE" --json "$AUDIO_DEVICES"' in up
    assert 'mkdir -p "$(dirname "$AUDIO_DEVICES")"' in up
    assert not re.search(r'rm [^\n#]*AUDIO_DEVICES', s)     # a dead writer is told by its pid


@pytest.mark.parametrize("path, made", [
    ("devices.json", None),                      # slash-free: the cwd, never a dir named the file
    ("sub/devices.json", "sub"),
    ("{tmp}/a/b/devices.json", "a/b"),
])
def test_watch_up_mkdirs_the_files_directory_never_the_file(tmp_path, path, made):
    """do-002 review N1: `${AUDIO_DEVICES%/*}` is the whole name when there is no slash, so
    `mkdir -p` made a directory where the watcher must rename its file, and the keeper fell
    back to SwitchAudioSource for good. watch_up's own mkdir line, run alone in tmp_path."""
    s = KEEPER.read_text()
    up = s[s.index("watch_up() {"):s.index("watch_down() {")]
    line = next(l for l in up.splitlines() if "mkdir -p" in l and "AUDIO_DEVICES" in l).strip()
    path = path.format(tmp=tmp_path)
    subprocess.run(["bash", "-c", f"set -uo pipefail\nAUDIO_DEVICES={path!r}\n{line}"],
                   cwd=tmp_path, check=True)
    assert not (tmp_path / Path(path).name).exists()
    assert not Path(path if path.startswith("/") else tmp_path / path).exists()
    if made:
        assert (tmp_path / made).is_dir()
    else:
        assert sorted(p.name for p in tmp_path.iterdir()) == []


def test_audio_outputs_prints_the_default_then_every_output(tmp_path):
    doc = _devices_doc("AirPods + BlackHole 2ch", ["MacBook Pro Speakers", "AirPods + BlackHole 2ch"])
    assert _audio_outputs(tmp_path, doc) == (
        0, "AirPods + BlackHole 2ch\nMacBook Pro Speakers\nAirPods + BlackHole 2ch\n")


def test_audio_outputs_takes_a_live_pid_it_may_not_signal_as_alive(tmp_path):
    """kill(pid, 0) fails with EPERM for another user's live process: that is alive."""
    assert _audio_outputs(tmp_path, _devices_doc("x", ["x"], pid=1)) == (0, "x\nx\n")


def test_T10_no_default_output_is_an_empty_first_line_and_is_left_alone(tmp_path):
    doc = _devices_doc("", ["MacBook Pro Speakers"])
    assert doc["default_output"] is None
    assert _audio_outputs(tmp_path, doc) == (0, "\nMacBook Pro Speakers\n")
    r = _check_output(tmp_path, "BlackHole 2ch", OUTPUTS, None, devices=doc, reads_fail=True)
    assert r.sets == "" and r.reads == [] and r.out == ""


def test_T8_fresh_file_switches_with_one_set_and_zero_reads(tmp_path):
    r = _check_output(tmp_path, "AirPods + BlackHole 2ch", NO_AIRPODS, {"input": "default"},
                      devices=_devices_doc("AirPods + BlackHole 2ch", NO_AIRPODS), checks=3)
    assert r.reads == [] and r.notes == []
    assert r.sets.strip() == "-t output -s MacBook Pro Speakers"     # the SET is still SAS


# Stale, each one: the file says all is well (a live call-tap speaker, AirPods in), the Mac
# says the AirPods are gone. Only a fallback to SwitchAudioSource moves the speaker.
_WELL = ("AirPods + BlackHole 2ch", OUTPUTS)
STALE = {
    "missing": None,
    "empty": "",
    "not json": "{half",
    "not an object": "[]",
    "version 2": dict(version=2),
    "version as a string": dict(version="1"),
    "version true": dict(version=True),
    "no version": dict(version=None),
    "dead pid": "DEAD",
    "pid 0": dict(pid=0),
    "pid -1": dict(pid=-1),
    "pid true": dict(pid=True),
    "pid as a string": dict(pid="1"),
    "no pid": dict(pid=None),
    "outputs not a list": dict(outputs={"name": "x"}),
    "a null in outputs": dict(outputs=[None]),
    "a device with no name": dict(default_output={"uid": "x", "transport": "usb"}),
    "a name with a newline": dict(outputs=[_device("Speakers\nAirPods")]),
}


@pytest.mark.parametrize("case", list(STALE))
def test_T9_a_stale_or_missing_file_falls_back_and_still_switches(tmp_path, case):
    over = STALE[case]
    if over == "DEAD":
        devices = _devices_doc(*_WELL, pid=_a_dead_pid())
    elif isinstance(over, dict):
        devices = dict(_devices_doc(*_WELL), **over)
    else:
        devices = over
    assert _audio_outputs(tmp_path, devices)[0] == 1
    r = _check_output(tmp_path, "AirPods + BlackHole 2ch", NO_AIRPODS, {"input": "default"},
                      devices=devices)
    assert r.reads == ["-c -t output", "-a -t output"]
    assert r.sets.strip() == "-t output -s MacBook Pro Speakers"
    assert len(r.notes) == 1 and "missing or stale" in r.notes[0]


def test_the_keeper_log_says_when_the_source_flips_and_only_then(tmp_path):
    js = tmp_path / "devices.json"
    away = f'mv "{js}" "{js}.away"'
    back = f'mv "{js}.away" "{js}"'
    between = f'n=$((${{n:-0}} + 1)); case $n in 1) {away} ;; 3) {back} ;; esac'
    # checks: file, (gone) sas, sas, (back) file, file
    r = _check_output(tmp_path, *_WELL, {"input": "default"}, devices=_devices_doc(*_WELL),
                      checks=5, between=between)
    assert r.reads == ["-c -t output", "-a -t output"] * 2
    assert len(r.notes) == 2
    assert "missing or stale; asking SwitchAudioSource" in r.notes[0]
    assert "reading" in r.notes[1] and "again" in r.notes[1]
    assert r.sets == ""


# -- EARS-341 do-007: the built-in input by transport, from devices.json while it is fresh ----

def _builtin_inputs(tmp_path, devices):
    js = tmp_path / "devices.json"
    js.unlink(missing_ok=True)
    if devices is not None:
        js.write_text(devices if isinstance(devices, str) else json.dumps(devices))
    s = KEEPER.read_text()
    body = s[s.index("OUT_EVERY="):s.index("check_output() {")]
    out = subprocess.run(["bash", "-c", "set -uo pipefail\n" + body + "\naudio_builtin_inputs"],
                         env=dict(os.environ, AUDIO_DEVICES_FILE=str(js)), capture_output=True, text=True)
    return out.returncode, out.stdout


def _inputs_doc(*inputs, **over):
    doc = _devices_doc(None, [], inputs=[{"name": n, "uid": f"uid-{n}", "transport": t} for n, t in inputs])
    doc.update(over)
    return doc


def test_audio_builtin_inputs_names_the_builtin_transport_only(tmp_path):
    doc = _inputs_doc(("airpods", "bluetooth"), ("BlackHole 16ch", "virtual"), ("MacBook Air Microphone", "builtin"),
                      ("USB Mic", "usb"))
    assert _builtin_inputs(tmp_path, doc) == (0, "MacBook Air Microphone\n")


def test_audio_builtin_inputs_with_none_is_fresh_and_empty(tmp_path):
    assert _builtin_inputs(tmp_path, _inputs_doc(("airpods", "bluetooth"))) == (0, "")


@pytest.mark.parametrize("bad", ["none", "not json", "dead pid", "version 2", "inputs not a list"])
def test_audio_builtin_inputs_stale_or_bad_is_exit_1(tmp_path, bad):
    doc = _inputs_doc(("MacBook Air Microphone", "builtin"))
    devices = {"none": None, "not json": "{nope", "dead pid": dict(doc, pid=_a_dead_pid()),
               "version 2": dict(doc, version=2), "inputs not a list": dict(doc, inputs="x")}[bad]
    code, out = _builtin_inputs(tmp_path, devices)
    assert code == 1 and out == ""
