"""A host that can play nothing says so: a non-zero exit and one line, never an id.

On a host with no audio output device (a VM with no sound card, a headless
server, a Linux login outside the ``audio`` group) ``mouth say`` used to exit 0
and print an id while nothing played, and ``mouth devices`` printed an empty
list with exit 0. Without the PortAudio library, ``mouth devices`` crashed with
a traceback and ``mouth say`` was silent the same way.

- **RC1:** ``say`` (and ``play``, ``again``) now ask PortAudio, in the caller's
  process, before the id: ``mouth.canplay.check``. Narrowly: only no PortAudio,
  no output at all, or no default for a ``default`` line. ``null`` and
  ``call:<N>`` are never probed; a named device merely absent is the player's.
- **RC2:** ``import sounddevice`` raising ``OSError`` (no PortAudio) is one guard,
  ``canplay._sd`` (``output._sd`` calls it): :class:`canplay.PortAudioMissing`.
- **RC3:** ``mouth devices`` with no output exits 1 with one line on stderr;
  ``--json`` still prints its object.

Fakes only: a fake PortAudio (``canplay._sd``) or a stub ``sounddevice`` that
raises ``OSError`` at import (in tmp, so the audio fence allows it), a tmp mouth
dir and settings file, no player (``ensure_player`` stubbed), the ``silence``
backend. No real device, no real HOME.
"""

from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

import pytest

import mouth
from mouth import __main__ as cli
from mouth import box as _box
from mouth import canplay, coreaudio, output
from tests import env_fence


class FakeSd:
    """A fake PortAudio: these outputs (plus one input), and a default output index."""

    def __init__(self, outputs=("Speakers",), default_out=None):
        self.devs = [{"name": "Microphone", "max_output_channels": 0}]
        self.devs += [{"name": n, "max_output_channels": 2} for n in outputs]
        if default_out is None:
            default_out = 1 if outputs else -1
        self.default = SimpleNamespace(device=[0 if outputs else -1, default_out])
        self.asked = 0

    def query_devices(self, i=None):
        self.asked += 1
        return list(self.devs) if i is None else self.devs[i]

    def _terminate(self):
        pass

    def _initialize(self):
        pass


def no_portaudio(tmp_path, monkeypatch):
    """A ``sounddevice`` whose import fails as the Linux wheel's does with no PortAudio."""
    stub = tmp_path / "stub_sd"
    stub.mkdir()
    (stub / "sounddevice.py").write_text("raise OSError('PortAudio library not found')\n")
    monkeypatch.delitem(sys.modules, "sounddevice", raising=False)
    monkeypatch.syspath_prepend(str(stub))


def never_asked(monkeypatch):
    def refuse():
        raise AssertionError("PortAudio was asked")
    monkeypatch.setattr(canplay, "_sd", refuse)


def on(monkeypatch, platform):
    """canplay's wording as on PLATFORM; nothing else in the process sees it."""
    monkeypatch.setattr(canplay, "sys", SimpleNamespace(platform=platform))


@pytest.fixture
def rig(tmp_path, monkeypatch):
    d = tmp_path / "mouth"
    monkeypatch.setenv("MOUTH_DIR", str(d))
    monkeypatch.setenv("MOUTH_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MOUTH_SETTINGS", str(tmp_path / "mouth.json"))
    monkeypatch.setenv("MOUTH_SESSION_BIN", "")
    env_fence.delenv(monkeypatch, "MOUTH_DEVICE")
    spawned = []
    monkeypatch.setattr(mouth, "ensure_player", lambda d=None: spawned.append(d))
    return SimpleNamespace(d=d, tmp=tmp_path, spawned=spawned)


def queued(d) -> list[dict]:
    return [_box.public(x) for x in _box.Queue(d).lines()] if d.exists() else []


def run(capsys, *argv):
    rc = cli.main(list(argv))
    out, err = capsys.readouterr()
    return rc, out, err


def refused(rig, rc, out, err, *words):
    """rc 1, nothing on stdout (no id), ONE line on stderr naming WORDS, nothing queued."""
    assert rc == 1
    assert out == ""
    assert err.startswith("mouth: ") and err.endswith("\n") and err.count("\n") == 1, err
    assert "Traceback" not in err
    for w in words:
        assert w in err, (w, err)
    assert queued(rig.d) == [] and rig.spawned == []


# -- RC1: say, play and again refuse before the id -------------------------------------------

def test_say_with_no_output_device_exits_1_with_one_line_and_no_id(rig, monkeypatch, capsys):
    """FAILED BEFORE: rc 0, an id on stdout, the line queued for a player that cannot play."""
    monkeypatch.setattr(canplay, "_sd", lambda: FakeSd(outputs=()))
    refused(rig, *run(capsys, "say", "--backend", "silence", "hello"), "no audio output device")


def test_on_linux_the_line_names_the_audio_group(rig, monkeypatch, capsys):
    on(monkeypatch, "linux")
    monkeypatch.setattr(canplay, "_sd", lambda: FakeSd(outputs=()))
    refused(rig, *run(capsys, "say", "--backend", "silence", "hello"),
            "no audio output device", "audio group", "id -nG", "ls -l /dev/snd",
            "usermod -aG audio")


def test_on_macos_the_line_names_sound_settings_not_the_audio_group(rig, monkeypatch, capsys):
    on(monkeypatch, "darwin")
    monkeypatch.setattr(canplay, "_sd", lambda: FakeSd(outputs=()))
    rc, out, err = run(capsys, "say", "--backend", "silence", "hello")
    refused(rig, rc, out, err, "no audio output device", "System Settings")
    assert "audio group" not in err


def test_say_with_outputs_but_no_default_refuses_a_default_line(rig, monkeypatch, capsys):
    monkeypatch.setattr(canplay, "_sd", lambda: FakeSd(outputs=("Speakers",), default_out=-1))
    refused(rig, *run(capsys, "say", "--backend", "silence", "hello"),
            "no default audio output device", "--device")


def test_a_named_device_on_a_host_with_no_output_is_refused_too(rig, monkeypatch, capsys):
    monkeypatch.setattr(canplay, "_sd", lambda: FakeSd(outputs=()))
    refused(rig, *run(capsys, "say", "--device", "AirPods", "--backend", "silence", "hello"),
            "no audio output device")


def test_a_named_device_merely_absent_keeps_todays_path(rig, monkeypatch, capsys):
    """Other outputs exist: the line is queued, and the player says device-absent, as before."""
    monkeypatch.setattr(canplay, "_sd", lambda: FakeSd(outputs=("Speakers",)))
    rc, out, err = run(capsys, "say", "--device", "AirPods", "--backend", "silence", "hello")
    assert rc == 0 and err == ""
    [item] = queued(rig.d)
    assert out.strip() == item["utt"] and item["device"] == "AirPods"


def test_a_host_with_a_device_says_as_before(rig, monkeypatch, capsys):
    sd = FakeSd(outputs=("Speakers",))
    monkeypatch.setattr(canplay, "_sd", lambda: sd)
    rc, out, err = run(capsys, "say", "--backend", "silence", "hello")
    assert rc == 0 and err == ""
    [item] = queued(rig.d)
    assert out.strip() == item["utt"] and item["device"] == "default"
    assert sd.asked == 1 and rig.spawned          # one look, no rescan; the player started


@pytest.mark.parametrize("device", ["null", "call", "call:3"])
def test_null_and_a_call_are_never_probed(rig, monkeypatch, capsys, device):
    never_asked(monkeypatch)
    assert canplay.check(device) is None
    rc, out, err = run(capsys, "say", "--device", device, "--backend", "silence", "hello")
    assert rc == 0 and "PortAudio was asked" not in err
    assert [x["device"] for x in queued(rig.d)] == [device]


def test_null_from_the_env_is_never_probed(rig, monkeypatch, capsys):
    monkeypatch.setenv("MOUTH_DEVICE", "null")
    never_asked(monkeypatch)
    rc, _, _ = run(capsys, "say", "--backend", "silence", "hello")
    assert rc == 0 and [x["device"] for x in queued(rig.d)] == ["null"]


def test_say_wait_is_unchanged_the_waiter_hears_the_players_refusal(rig, monkeypatch, capsys):
    """--wait already exits 1 with the player's own line; the probe stays out of its way."""
    never_asked(monkeypatch)
    monkeypatch.setattr(cli, "say", lambda text, **kw: {"utt": "u1", "reason": "done", "wait": kw["wait"]})
    rc, out, _ = run(capsys, "say", "--wait", "--backend", "silence", "hello")
    assert rc == 0 and json.loads(out)["wait"] is True


def test_play_with_no_output_device_is_refused_before_the_id(rig, monkeypatch, capsys):
    monkeypatch.setattr(canplay, "_sd", lambda: FakeSd(outputs=()))
    snd = rig.tmp / "ding.wav"
    snd.write_bytes(b"RIFF")
    refused(rig, *run(capsys, "play", str(snd)), "no audio output device")


def test_again_with_no_output_device_is_refused(rig, monkeypatch, capsys):
    monkeypatch.setattr(canplay, "_sd", lambda: FakeSd(outputs=()))
    refused(rig, *run(capsys, "again", "r0e5u1e00001", "--text", "hi"), "no audio output device")


# -- RC2: no PortAudio is one line naming it and its install, never a traceback ---------------

def test_no_portaudio_is_a_typed_error_from_the_one_guard(tmp_path, monkeypatch):
    """FAILED BEFORE: output._sd() let the import's OSError out as it was."""
    no_portaudio(tmp_path, monkeypatch)
    with pytest.raises(canplay.PortAudioMissing, match="PortAudio") as e:
        output._sd()
    assert isinstance(e.value, output.DeviceAbsent)     # the player files it device-absent
    assert isinstance(e.value.__cause__, OSError)


def test_the_player_files_no_portaudio_as_device_absent_naming_it(tmp_path, monkeypatch):
    no_portaudio(tmp_path, monkeypatch)
    with pytest.raises(output.DeviceAbsent, match="PortAudio"):
        output.resolve("default")


def test_say_with_no_portaudio_exits_1_naming_it(rig, monkeypatch, capsys):
    """FAILED BEFORE: rc 0 and an id; the player alone met the OSError."""
    no_portaudio(rig.tmp, monkeypatch)
    refused(rig, *run(capsys, "say", "--backend", "silence", "hello"), "PortAudio")


def test_devices_with_no_portaudio_exits_1_with_one_line(rig, monkeypatch, capsys):
    """FAILED BEFORE: an OSError traceback out of `mouth devices`."""
    on(monkeypatch, "linux")
    no_portaudio(rig.tmp, monkeypatch)
    rc, out, err = run(capsys, "devices")
    refused(rig, rc, out, err, "PortAudio is not installed", "dnf install portaudio",
            "apt install libportaudio2")


@pytest.mark.parametrize("platform, words", [
    ("linux", ["sudo dnf install portaudio", "libportaudio2"]),
    ("darwin", ["bundles PortAudio", "brew install portaudio"]),
    ("freebsd14", ["install the PortAudio library"]),
])
def test_the_portaudio_line_is_one_line_per_platform(platform, words):
    line = canplay.portaudio_missing_line(platform)
    assert "\n" not in line and "PortAudio" in line
    for w in words:
        assert w in line


@pytest.mark.parametrize("platform, words, never", [
    ("linux", ["audio group", "id -nG", "ls -l /dev/snd"], "System Settings"),
    ("darwin", ["System Settings"], "audio group"),
    ("freebsd14", ["connect or enable one"], "audio group"),
])
def test_the_no_output_line_is_one_line_per_platform(platform, words, never):
    line = canplay.no_output_line(platform)
    assert "\n" not in line and line.startswith("no audio output device")
    assert never not in line
    for w in words:
        assert w in line


@pytest.mark.parametrize("platform", ["darwin", "freebsd14"])
def test_only_linux_gets_the_audio_group_check(platform):
    line = canplay.no_output_line(platform)
    for w in ("id -nG", "/dev/snd", "usermod"):
        assert w not in line, (w, line)


# -- RC3: devices with no output is a state, exit 1 --------------------------------------------

@pytest.fixture
def silent_machine(monkeypatch):
    """No PortAudio output, no call, and CoreAudio naming nothing."""
    monkeypatch.setattr(canplay, "_sd", lambda: FakeSd(outputs=()))
    monkeypatch.setattr(output, "live_calls", lambda: [])
    for f in ("default_output_name", "default_input_name", "output_volume", "output_muted"):
        monkeypatch.setattr(coreaudio, f, lambda: None)


def test_devices_with_no_output_exits_1_and_says_so(rig, silent_machine, capsys):
    """FAILED BEFORE: one empty line on stdout, rc 0."""
    refused(rig, *run(capsys, "devices"), "no audio output device")


def test_devices_json_with_no_output_still_prints_its_object_and_exits_1(rig, silent_machine, capsys):
    rc, out, err = run(capsys, "devices", "--json")
    assert rc == 1
    assert json.loads(out)["outputs"] == []
    assert err.count("\n") == 1 and "no audio output device" in err


def test_devices_with_no_output_still_lists_a_live_call(rig, silent_machine, monkeypatch, capsys):
    monkeypatch.setattr(output, "live_calls", lambda: [(3, "sessionvoice+super", None)])
    rc, out, err = run(capsys, "devices")
    assert rc == 1 and out == "call:3\t# live call, sessionvoice+super\n"
    assert "no audio output device" in err


def test_devices_with_an_output_is_unchanged(rig, silent_machine, monkeypatch, capsys):
    monkeypatch.setattr(canplay, "_sd", lambda: FakeSd(outputs=("Speakers",)))
    rc, out, err = run(capsys, "devices")
    assert (rc, out, err) == (0, "Speakers\t# current output\n", "")


# -- The probe is quiet on fd 2: ALSA's C chatter never reaches the caller --------------------
#
# PortAudio's initialize runs in the caller's process, and on Linux ALSA writes
# ``ALSA lib ...`` straight to fd 2. These fakes write to fd 2 as ALSA does, and
# ``capfd`` reads what really reached fd 2.

ALSA = b"ALSA lib pcm.c:2722:(snd_pcm_open_noupdate) Unknown PCM default\n"


class ChattySd(FakeSd):
    """A fake PortAudio that writes to fd 2 from 'C' as it lists its devices."""

    def query_devices(self, i=None):
        os.write(2, ALSA)
        return super().query_devices(i)


def run_fd(capfd, *argv):
    rc = cli.main(list(argv))
    out, err = capfd.readouterr()
    return rc, out, err


def fd2():
    st = os.fstat(2)
    return st.st_dev, st.st_ino


def test_alsa_chatter_from_the_probe_never_reaches_a_working_say(rig, monkeypatch, capfd):
    monkeypatch.setattr(canplay, "_sd", lambda: ChattySd(outputs=("Speakers",)))
    rc, out, err = run_fd(capfd, "say", "--backend", "silence", "hello")
    assert rc == 0 and err == ""
    [item] = queued(rig.d)
    assert out.strip() == item["utt"]


def test_a_refusal_is_still_its_one_line_with_no_alsa_chatter(rig, monkeypatch, capfd):
    monkeypatch.setattr(canplay, "_sd", lambda: ChattySd(outputs=()))
    rc, out, err = run_fd(capfd, "say", "--backend", "silence", "hello")
    refused(rig, rc, out, err, "no audio output device")
    assert "ALSA" not in err


def test_chatter_at_the_import_itself_is_silenced(rig, monkeypatch, capfd):
    def chatty_import():
        os.write(2, ALSA)
        return FakeSd(outputs=())
    monkeypatch.setattr(canplay, "_sd", chatty_import)
    refused(rig, *run_fd(capfd, "say", "--backend", "silence", "hello"), "no audio output device")


@pytest.mark.parametrize("outputs, raises", [(("Speakers",), None), ((), canplay.NoOutputDevice)])
def test_fd2_is_restored_after_the_probe_passes_or_refuses(monkeypatch, capfd, outputs, raises):
    monkeypatch.setattr(canplay, "_sd", lambda: ChattySd(outputs=outputs))
    before = fd2()
    if raises:
        with pytest.raises(raises):
            canplay.check("default")
    else:
        canplay.check("default")
    assert fd2() == before
    os.write(2, b"after the probe\n")
    assert capfd.readouterr().err == "after the probe\n"


def test_fd2_is_restored_when_the_probe_meets_an_error_it_does_not_own(monkeypatch, capfd):
    """Not a PortAudio error: it is raised as it was (never turned into a refusal), fd 2 back."""
    def broken():
        os.write(2, ALSA)
        raise RuntimeError("a bug, not the audio system")
    monkeypatch.setattr(canplay, "_sd", broken)
    before = fd2()
    with pytest.raises(RuntimeError, match="a bug"):
        canplay.check("default")
    assert fd2() == before
    os.write(2, b"after\n")
    assert capfd.readouterr().err == "after\n"


def test_stderr_python_already_buffered_is_not_lost_to_the_silencer(monkeypatch, capfd):
    monkeypatch.setattr(canplay, "_sd", lambda: FakeSd(outputs=("Speakers",)))
    sys.stderr.write("written before the probe")   # no newline: still in Python's buffer
    canplay.check("default")
    assert capfd.readouterr().err == "written before the probe"


def test_the_player_path_is_never_silenced(monkeypatch, capfd):
    """Only the caller's probe quiets fd 2; output._sd (the player's) leaves it as it was."""
    def chatty_import():
        os.write(2, ALSA)
        return FakeSd()
    monkeypatch.setattr(canplay, "_sd", chatty_import)
    output._sd()
    assert capfd.readouterr().err == ALSA.decode()


# -- PortAudioError in the probe: one line, never a traceback ---------------------------------

PortAudioError = type("PortAudioError", (Exception,), {"__module__": "sounddevice"})


def portaudio_fails_to_start(tmp_path, monkeypatch):
    """A ``sounddevice`` whose import fails in Pa_Initialize, after ALSA has written to fd 2."""
    stub = tmp_path / "stub_sd"
    stub.mkdir()
    (stub / "sounddevice.py").write_text(
        "import os\n"
        "class PortAudioError(Exception):\n"
        "    pass\n"
        f"os.write(2, {ALSA!r})\n"
        "raise PortAudioError('Error initializing PortAudio: Unanticipated host error'\n"
        "                     ' [PaErrorCode -9999]')\n")
    monkeypatch.delitem(sys.modules, "sounddevice", raising=False)
    monkeypatch.syspath_prepend(str(stub))


def test_portaudio_error_at_initialize_is_one_line(rig, monkeypatch, capfd):
    """FAILED BEFORE: a PortAudioError traceback out of `mouth say`."""
    portaudio_fails_to_start(rig.tmp, monkeypatch)
    rc, out, err = run_fd(capfd, "say", "--backend", "silence", "hello")
    refused(rig, rc, out, err, "mouth: the audio system did not start: ",
            "Error initializing PortAudio", "PaErrorCode -9999")
    assert "ALSA" not in err


def test_portaudio_error_listing_the_devices_is_one_line(rig, monkeypatch, capfd):
    """FAILED BEFORE: a PortAudioError traceback out of `mouth say`."""
    class Failing(FakeSd):
        def query_devices(self, i=None):
            os.write(2, ALSA)
            raise PortAudioError("Error querying device -1\n  (host API detail)")
    monkeypatch.setattr(canplay, "_sd", lambda: Failing())
    rc, out, err = run_fd(capfd, "say", "--backend", "silence", "hello")
    refused(rig, rc, out, err, "mouth: the audio system did not start: Error querying device -1 "
            "(host API detail)")


def test_play_and_again_meet_a_portaudio_error_the_same_way(rig, monkeypatch, capfd):
    class Failing(FakeSd):
        def query_devices(self, i=None):
            raise PortAudioError("Error querying device -1")
    monkeypatch.setattr(canplay, "_sd", lambda: Failing())
    snd = rig.tmp / "ding.wav"
    snd.write_bytes(b"RIFF")
    refused(rig, *run_fd(capfd, "play", str(snd)), "the audio system did not start")
    refused(rig, *run_fd(capfd, "again", "r0e5u1e00001", "--text", "hi"),
            "the audio system did not start")


def test_the_did_not_start_error_keeps_its_cause(monkeypatch):
    err = PortAudioError("Error querying device -1")

    class Failing(FakeSd):
        def query_devices(self, i=None):
            raise err
    monkeypatch.setattr(canplay, "_sd", lambda: Failing())
    with pytest.raises(canplay.AudioDidNotStart) as e:
        canplay.check("default")
    assert e.value.__cause__ is err


def test_an_error_merely_named_portaudioerror_elsewhere_is_not_owned():
    other = type("PortAudioError", (Exception,), {"__module__": "somewhere_else"})
    assert not canplay._is_portaudio_error(other("x"))
    assert canplay._is_portaudio_error(PortAudioError("x"))


# -- mouth devices asks PortAudio as the probe does: quiet fd 2, one line ---------------------
#
# ``mouth devices`` runs PortAudio's initialize in the caller's process too, so
# it goes through the same span as the probe (``canplay.asking_portaudio``):
# ALSA's fd 2 chatter is silenced, and a PortAudioError is one line.

from mouth import devices as _devices  # noqa: E402

_REAL_SD = canplay._sd    # the real import guard; ``silent_machine`` fakes it


def chatty_no_portaudio(tmp_path, monkeypatch):
    """A ``sounddevice`` whose import writes ALSA's line to fd 2, then fails with OSError."""
    stub = tmp_path / "stub_sd"
    stub.mkdir()
    (stub / "sounddevice.py").write_text(
        "import os\n"
        f"os.write(2, {ALSA!r})\n"
        "raise OSError('PortAudio library not found')\n")
    monkeypatch.delitem(sys.modules, "sounddevice", raising=False)
    monkeypatch.syspath_prepend(str(stub))


class FailsListing(FakeSd):
    """PortAudio starts, writes ALSA's line, then raises PortAudioError listing its devices."""

    def query_devices(self, i=None):
        os.write(2, ALSA)
        raise PortAudioError("Error querying device -1\n  (host API detail)")


def _pa_error_at_import(rig, monkeypatch):
    monkeypatch.setattr(canplay, "_sd", _REAL_SD)
    portaudio_fails_to_start(rig.tmp, monkeypatch)


def _pa_error_listing(rig, monkeypatch):
    monkeypatch.setattr(canplay, "_sd", lambda: FailsListing())


def _os_error_at_import(rig, monkeypatch):
    on(monkeypatch, "linux")
    monkeypatch.setattr(canplay, "_sd", _REAL_SD)
    chatty_no_portaudio(rig.tmp, monkeypatch)


def _no_device(rig, monkeypatch):
    monkeypatch.setattr(canplay, "_sd", lambda: ChattySd(outputs=()))


DEVICES_FAILURES = {
    "portaudio-error-at-import": (_pa_error_at_import, [
        "mouth: the audio system did not start: Error initializing PortAudio",
        "PaErrorCode -9999"]),
    "portaudio-error-listing": (_pa_error_listing, [
        "mouth: the audio system did not start: Error querying device -1 (host API detail)"]),
    "oserror-at-import": (_os_error_at_import, [
        "mouth: PortAudio is not installed", "dnf install portaudio"]),
    "no-output-device": (_no_device, ["mouth: no audio output device"]),
}


@pytest.mark.parametrize("as_json", [False, True], ids=["text", "json"])
@pytest.mark.parametrize("case", list(DEVICES_FAILURES))
def test_devices_failure_is_one_line_with_no_alsa_chatter(rig, silent_machine, monkeypatch, capfd,
                                                          case, as_json):
    """FAILED BEFORE: ALSA's line on stderr every time, and a PortAudioError traceback."""
    arrange, words = DEVICES_FAILURES[case]
    arrange(rig, monkeypatch)
    before = fd2()
    rc, out, err = run_fd(capfd, "devices", *(["--json"] if as_json else []))
    assert fd2() == before
    assert rc == 1
    assert "ALSA" not in err and "Traceback" not in err
    assert err.startswith("mouth: ") and err.endswith("\n") and err.count("\n") == 1, err
    for w in words:
        assert w in err, (w, err)
    if case == "no-output-device":           # a state: the listing still prints
        if as_json:
            assert json.loads(out)["outputs"] == []
        else:
            assert out == ""
    else:                                    # PortAudio never answered: nothing to list
        assert out == ""
    os.write(2, b"after mouth devices\n")    # fd 2 is the caller's again
    assert capfd.readouterr().err == "after mouth devices\n"


@pytest.mark.parametrize("as_json", [False, True], ids=["text", "json"])
def test_devices_with_an_output_is_unchanged_and_quiet(rig, silent_machine, monkeypatch, capfd,
                                                       as_json):
    """ALSA chatter as the list and PortAudio's default are read; the output is as before."""
    monkeypatch.setattr(canplay, "_sd", lambda: ChattySd(outputs=("Speakers",)))
    rc, out, err = run_fd(capfd, "devices", *(["--json"] if as_json else []))
    assert rc == 0 and err == ""
    if as_json:
        d = json.loads(out)
        assert d["outputs"] == ["Speakers"] and d["current_output"] == "Speakers"
    else:
        assert out == "Speakers\t# current output\n"


def test_devices_portaudio_error_is_did_not_start_with_its_cause(monkeypatch):
    err = PortAudioError("Error querying device -1")

    class Failing(FakeSd):
        def query_devices(self, i=None):
            raise err
    monkeypatch.setattr(canplay, "_sd", lambda: Failing())
    with pytest.raises(canplay.AudioDidNotStart) as e:
        _devices.devices()
    assert e.value.__cause__ is err


def test_devices_leaves_an_error_it_does_not_own_as_it_was(monkeypatch, capfd):
    """Not a PortAudio error: raised as it was, fd 2 back."""
    def broken():
        os.write(2, ALSA)
        raise RuntimeError("a bug, not the audio system")
    monkeypatch.setattr(canplay, "_sd", broken)
    before = fd2()
    with pytest.raises(RuntimeError, match="a bug"):
        _devices.devices()
    assert fd2() == before
    assert capfd.readouterr().err == ""


def test_devices_and_the_probe_share_one_span():
    """Reused, not copied: neither path carries its own silencer or PortAudioError catch."""
    import inspect

    for f in (canplay.check, _devices.devices):
        src = inspect.getsource(f)
        assert "asking_portaudio()" in src, f
        assert "_quiet_fd2" not in src and "_is_portaudio_error" not in src, f
