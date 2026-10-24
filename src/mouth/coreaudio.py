"""The Mac's current audio devices and output volume, asked of CoreAudio live.

``mouth devices`` marks the current output with this, names the current input,
and reports the output volume, so a seat needs no Homebrew
``SwitchAudioSource`` and no ``osascript`` for them.

PortAudio is not asked: it caches the device list AND the default at init
(``mouth.output._PA``), so its default can be stale after a switch. CoreAudio is
reached by ctypes (``AudioObjectGetPropertyData`` on the system object), with
nothing to install.

Every function here returns ``None`` off darwin, and on ANY failure (no
framework, a non-zero OSStatus, a device with no such property). None means
unknown, and a caller omits it; it never raises into the CLI.
"""

from __future__ import annotations

import ctypes
import sys
from typing import Callable, Optional, TypeVar

T = TypeVar("T")


def _fourcc(code: str) -> int:
    return int.from_bytes(code.encode("ascii"), "big")


SYSTEM_OBJECT = 1                                   # kAudioObjectSystemObject
DEFAULT_OUTPUT = _fourcc("dOut")                    # kAudioHardwarePropertyDefaultOutputDevice
DEFAULT_INPUT = _fourcc("dIn ")                     # kAudioHardwarePropertyDefaultInputDevice
NAME = _fourcc("lnam")                              # kAudioObjectPropertyName
VIRTUAL_MAIN_VOLUME = _fourcc("vmvc")               # kAudioHardwareServiceDeviceProperty_VirtualMainVolume
VOLUME_SCALAR = _fourcc("volm")                     # kAudioDevicePropertyVolumeScalar
MUTE = _fourcc("mute")                              # kAudioDevicePropertyMute
SCOPE_GLOBAL = _fourcc("glob")                      # kAudioObjectPropertyScopeGlobal
SCOPE_OUTPUT = _fourcc("outp")                      # kAudioDevicePropertyScopeOutput
ELEMENT_MAIN = 0                                    # kAudioObjectPropertyElementMain
UTF8 = 0x08000100                                   # kCFStringEncodingUTF8

_FRAMEWORKS = "/System/Library/Frameworks/{0}.framework/{0}"


class _Address(ctypes.Structure):
    _fields_ = [("mSelector", ctypes.c_uint32), ("mScope", ctypes.c_uint32),
                ("mElement", ctypes.c_uint32)]


_libs: Optional[tuple] = None


def _load():
    """(CoreAudio, AudioToolbox or None, CoreFoundation), loaded once; None off darwin."""
    global _libs
    if sys.platform != "darwin":
        return None
    if _libs is None:
        ca = ctypes.CDLL(_FRAMEWORKS.format("CoreAudio"))
        ca.AudioObjectGetPropertyData.restype = ctypes.c_int32
        ca.AudioObjectGetPropertyData.argtypes = [
            ctypes.c_uint32, ctypes.POINTER(_Address), ctypes.c_uint32, ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
        ca.AudioObjectHasProperty.restype = ctypes.c_ubyte
        ca.AudioObjectHasProperty.argtypes = [ctypes.c_uint32, ctypes.POINTER(_Address)]
        try:
            at = ctypes.CDLL(_FRAMEWORKS.format("AudioToolbox"))
            at.AudioHardwareServiceGetPropertyData.restype = ctypes.c_int32
            at.AudioHardwareServiceGetPropertyData.argtypes = ca.AudioObjectGetPropertyData.argtypes
            at.AudioHardwareServiceHasProperty.restype = ctypes.c_ubyte
            at.AudioHardwareServiceHasProperty.argtypes = ca.AudioObjectHasProperty.argtypes
        except (OSError, AttributeError):
            at = None
        cf = ctypes.CDLL(_FRAMEWORKS.format("CoreFoundation"))
        cf.CFStringGetCString.restype = ctypes.c_bool
        cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        cf.CFRelease.restype = None
        _libs = (ca, at, cf)
    return _libs


def _quiet(fn: Callable[[], Optional[T]]) -> Optional[T]:
    """fn(), or None off darwin or on any failure: unknown, never an error."""
    try:
        if _load() is None:
            return None
        return fn()
    except Exception:
        return None


def _get(obj: int, selector: int, scope: int, element: int, out, service: bool = False) -> bool:
    """Fill ``out`` with the property; False if the object lacks it or CoreAudio says no."""
    ca, at, _ = _load()
    lib = at if service else ca
    if lib is None:
        return False
    has = lib.AudioHardwareServiceHasProperty if service else lib.AudioObjectHasProperty
    get = lib.AudioHardwareServiceGetPropertyData if service else lib.AudioObjectGetPropertyData
    addr = _Address(selector, scope, element)
    if not has(obj, ctypes.byref(addr)):
        return False
    size = ctypes.c_uint32(ctypes.sizeof(out))
    return get(obj, ctypes.byref(addr), 0, None, ctypes.byref(size), ctypes.byref(out)) == 0


def _default_device(selector: int) -> Optional[int]:
    dev = ctypes.c_uint32(0)
    if not _get(SYSTEM_OBJECT, selector, SCOPE_GLOBAL, ELEMENT_MAIN, dev) or not dev.value:
        return None                                  # 0 is kAudioObjectUnknown: no such device
    return dev.value


def _name(dev: Optional[int]) -> Optional[str]:
    if dev is None:
        return None
    _, _, cf = _load()
    ref = ctypes.c_void_p(None)
    if not _get(dev, NAME, SCOPE_GLOBAL, ELEMENT_MAIN, ref) or not ref.value:
        return None
    try:
        buf = ctypes.create_string_buffer(1024)
        if not cf.CFStringGetCString(ref, buf, len(buf), UTF8):
            return None
        return buf.value.decode("utf-8") or None
    finally:
        cf.CFRelease(ref)                            # a copied CFString: ours to release


def default_output_name() -> Optional[str]:
    """The name of the system's current default output, or None."""
    return _quiet(lambda: _name(_default_device(DEFAULT_OUTPUT)))


def default_input_name() -> Optional[str]:
    """The name of the system's current default input, or None."""
    return _quiet(lambda: _name(_default_device(DEFAULT_INPUT)))


def _volume() -> Optional[float]:
    dev = _default_device(DEFAULT_OUTPUT)
    if dev is None:
        return None
    v = ctypes.c_float(0.0)
    if _get(dev, VIRTUAL_MAIN_VOLUME, SCOPE_OUTPUT, ELEMENT_MAIN, v, service=True):
        return _clamp(v.value)
    if _get(dev, VIRTUAL_MAIN_VOLUME, SCOPE_OUTPUT, ELEMENT_MAIN, v):
        return _clamp(v.value)
    if _get(dev, VOLUME_SCALAR, SCOPE_OUTPUT, ELEMENT_MAIN, v):
        return _clamp(v.value)
    chans = []
    for ch in (1, 2):
        if _get(dev, VOLUME_SCALAR, SCOPE_OUTPUT, ch, v):
            chans.append(v.value)
    return _clamp(sum(chans) / len(chans)) if chans else None


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def output_volume() -> Optional[float]:
    """The default output's volume, 0.0 to 1.0, or None (unknown: many HDMI and BT sinks)."""
    return _quiet(_volume)


def _muted() -> Optional[bool]:
    dev = _default_device(DEFAULT_OUTPUT)
    if dev is None:
        return None
    m = ctypes.c_uint32(0)
    if _get(dev, MUTE, SCOPE_OUTPUT, ELEMENT_MAIN, m):
        return bool(m.value)
    return None


def output_muted() -> Optional[bool]:
    """True if the default output is muted, False if not, None if it has no mute."""
    return _quiet(_muted)
