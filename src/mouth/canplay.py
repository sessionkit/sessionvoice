"""Can this host play at all? PortAudio's import, and the probe ``mouth say`` runs before an id.

``mouth say`` returns at once: the line is queued, the id printed, and the
resident player opens the device later. On a host that cannot play anything
(no PortAudio library, or PortAudio sees no output device: a headless server,
a VM with no sound card, a Linux login outside the ``audio`` group) the player
can only record ``said reason=device-absent`` where a shell caller never
looks. So ``say``, ``play`` and ``again`` ask here first, in the caller's own
process, and refuse with one line instead of an id (``mouth.__main__``).

The probe is deliberately narrow. It refuses only when nothing at all can
play: PortAudio missing, no output device, or no default output for a line
that asks the default. ``null`` and ``call:<N>`` are never probed (they do not
go through PortAudio), and a NAMED device that is absent while other outputs
exist is left to the player, as it always was.

This module imports no numpy, so the probe costs ``mouth say`` only
sounddevice's own import (PortAudio's initialize, which lists the devices once).
There is no rescan: a fresh process's list is current.

The probe runs PortAudio's initialize in the CALLER's process, and on Linux
ALSA's C library writes ``ALSA lib ...`` lines straight to fd 2 while it does.
So the probe points fd 2 at the null device for exactly that span and puts it
back after (:func:`_quiet_fd2`). Python exceptions are untouched: they still
reach the caller's one-line handler, which prints after fd 2 is restored. The
player opens its devices with fd 2 as it was; nothing here silences it.

``mouth devices`` asks PortAudio in the caller's process too, so it asks the
same way: :func:`asking_portaudio` is that one span, the silencer and the
``PortAudioError`` to :class:`AudioDidNotStart`, for both.
"""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from typing import Iterator, Optional


class DeviceAbsent(RuntimeError):
    """The line's output device is not there to open (``said reason=device-absent``)."""


class PortAudioMissing(DeviceAbsent):
    """``import sounddevice`` found no PortAudio library: nothing on this host can play."""


class NoOutputDevice(DeviceAbsent):
    """PortAudio sees no output device (or no default one, for a ``default`` line)."""


class AudioDidNotStart(RuntimeError):
    """PortAudio is there but raised ``PortAudioError`` at initialize or listing its devices.

    Raised by :func:`asking_portaudio` only: the caller's probe and ``mouth
    devices``. The player meets such an error on its own path, as it always did."""


def _platform(platform: Optional[str]) -> str:
    return platform or sys.platform


def portaudio_missing_line(platform: Optional[str] = None) -> str:
    """What is missing and how to install it, one line (no ``mouth:`` prefix)."""
    p = _platform(platform)
    if p.startswith("linux"):
        return ("PortAudio is not installed, so nothing can play: install your distribution's "
                "package (sudo dnf install portaudio, or sudo apt install libportaudio2)")
    if p == "darwin":
        return ("PortAudio could not be loaded, so nothing can play: reinstall sessionvoice "
                "(its macOS sounddevice wheel bundles PortAudio), or brew install portaudio")
    return "PortAudio is not installed, so nothing can play: install the PortAudio library"


def no_output_line(platform: Optional[str] = None) -> str:
    """No output device at all, and the likely fix, one line (no ``mouth:`` prefix).

    On Linux the usual cause is permission, not hardware: ``/dev/snd`` is
    ``root:audio`` 0660 and a headless login gets no seat ACL, so PortAudio
    sees no device until the user is in the ``audio`` group. The line names how
    to check (``id -nG``, ``ls -l /dev/snd``) and the fix, and runs neither:
    joining a group needs sudo, and that is the user's call."""
    p = _platform(platform)
    if p.startswith("linux"):
        return ("no audio output device, so nothing can play: on Linux, is this user in the "
                "audio group? (check: id -nG, ls -l /dev/snd; to join: "
                "sudo usermod -aG audio $USER, then log in again)")
    if p == "darwin":
        return ("no audio output device, so nothing can play: connect one, or choose one in "
                "System Settings > Sound > Output")
    return "no audio output device, so nothing can play: connect or enable one"


NO_DEFAULT_LINE = ("no default audio output device: name one with --device "
                   "(mouth devices lists them)")


def _sd():
    """``sounddevice``, or :class:`PortAudioMissing` when its PortAudio library is not there.

    The one guard on the import (``output._sd`` calls this). On Linux the
    sounddevice wheel bundles no PortAudio, and the import raises ``OSError``."""
    try:
        import sounddevice as sd
    except OSError as e:
        raise PortAudioMissing(portaudio_missing_line()) from e
    return sd


@contextmanager
def _quiet_fd2() -> Iterator[None]:
    """fd 2 points at the null device inside, and is restored on the way out, raise or not.

    At the OS level, because ALSA writes from C and never sees ``sys.stderr``.
    Text Python already buffered for stderr is flushed first, so none of it is
    lost. If fd 2 cannot be duplicated (closed), nothing is redirected."""
    stream = getattr(sys, "stderr", None)   # tests fake ``sys`` for the platform only
    if stream is not None:
        try:
            stream.flush()
        except (OSError, ValueError):
            pass
    try:
        saved = os.dup(2)
    except OSError:
        yield
        return
    try:
        null = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(null, 2)
        finally:
            os.close(null)
        yield
    finally:
        os.dup2(saved, 2)
        os.close(saved)


def _is_portaudio_error(e: BaseException) -> bool:
    """``sounddevice.PortAudioError``, matched by its class: a failed import leaves no
    ``sounddevice`` module to name it by."""
    return any(c.__name__ == "PortAudioError" and c.__module__ == "sounddevice"
               for c in type(e).__mro__)


def did_not_start_line(e: BaseException) -> str:
    """PortAudio's own message, on one line (no ``mouth:`` prefix)."""
    return "the audio system did not start: " + (" ".join(str(e).split()) or type(e).__name__)


@contextmanager
def asking_portaudio() -> Iterator[None]:
    """Ask PortAudio inside: fd 2 silenced (:func:`_quiet_fd2`), and a ``PortAudioError``
    raised as :class:`AudioDidNotStart`, once fd 2 is back.

    The one way the caller's process asks PortAudio: :func:`check` (before an
    id) and ``mouth devices`` (:func:`mouth.devices.devices`). Every other
    error, :class:`PortAudioMissing` included, is raised as it was."""
    try:
        with _quiet_fd2():
            yield
    except Exception as e:
        if _is_portaudio_error(e):
            raise AudioDidNotStart(did_not_start_line(e)) from e
        raise


def check(device: str) -> None:
    """Raise :class:`PortAudioMissing` or :class:`NoOutputDevice` when DEVICE cannot play
    because this host can play nothing, or :class:`AudioDidNotStart` when PortAudio
    raised ``PortAudioError``; return quietly otherwise.

    ``null`` and ``call``/``call:<N>`` return at once, exactly as
    ``output._plain_output`` routes them away from PortAudio. Any other name
    refuses only when PortAudio lists no output at all, and ``default`` also
    when PortAudio names no default output. PortAudio is asked through
    :func:`asking_portaudio`."""
    if device == "null" or device == "call" or device.startswith("call:"):
        return
    with asking_portaudio():
        sd = _sd()
        devs = sd.query_devices()
        default_out = sd.default.device[1] if device == "default" else None
    if not any(d["max_output_channels"] > 0 for d in devs):
        raise NoOutputDevice(no_output_line())
    if device == "default" and (default_out is None or default_out < 0):
        raise NoOutputDevice(NO_DEFAULT_LINE)
