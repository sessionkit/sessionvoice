"""AppleBackend: SpeechAnalyzer / SpeechTranscriber through the apple-speech helper.

Why a process, not bindings: the Speech framework TRAPS on some bad input
(SIGTRAP in AnalyzerInput.data(from:), measured 2030-06-09) - in a helper that
kills the helper, in-process it would kill the capture. The helper speaks ears
events v1 itself (proc.py relays them); SIGUSR1 = commit.
"""
import os, platform, shutil, subprocess
from .proc import ProcessBackend

def helper_path():
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (os.environ.get("EARS_APPLE_HELPER"),
              shutil.which("apple-speech"),
              os.path.expanduser("~/.cache/ears/bin/apple-speech"),
              os.path.normpath(os.path.join(here, "..", "..", "..", "swift", ".build", "release", "apple-speech"))):
        if p and os.path.exists(p) and os.access(p, os.X_OK):
            return p
    return None

PROBE_TIMEOUT = 5      # s for `apple-speech locales`; measured 0.05-0.08 s

def why_not(locale=None):
    """None if the Apple backend can run here, else the reason. `locale` defaults to
    EARS_LOCALE, as AppleBackend's does."""
    if platform.system() != "Darwin":
        return "not macOS"
    major = int((platform.mac_ver()[0] or "0").split(".")[0])
    if major < 27:
        return f"macOS {platform.mac_ver()[0]} < 27 (SpeechTranscriber)"
    helper = helper_path()
    if not helper:
        return "no apple-speech helper: `ears install --apple` builds it (needs Xcode's swift)"
    return assets_why_not(helper, locale or os.environ.get("EARS_LOCALE", "en-AU"))

def assets_why_not(helper, locale):
    """Ask the helper's `locales` verb whether SpeechTranscriber has `locale`'s assets:
    without them `stream` fails at the first audio. Only the two answers it parses say no: the
    locale is unsupported (`equivalent to L: nil`), or its equivalent is missing from the
    `SpeechTranscriber installed` line. A probe that times out, crashes or prints nothing
    parseable fails OPEN (None), so no working setup regresses. AssetInventory.status is not
    used: it says `supported` for a locale that streams fine."""
    try:
        r = subprocess.run([helper, "locales"], env=dict(os.environ, APPLE_SPEECH_LOCALE=locale),
                           capture_output=True, text=True, timeout=PROBE_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    eq = installed = None
    for line in r.stdout.splitlines():
        if line.startswith("equivalent to ") and ":" in line:
            eq = line.split(":", 1)[1].strip()
        elif line.startswith("SpeechTranscriber installed (") and ":" in line:
            installed = {x.replace("-", "_") for x in line.split(":", 1)[1].split()}
    if eq == "nil":
        return f"locale {locale} not supported by SpeechTranscriber (`apple-speech locales` lists the supported ones)"
    if eq and installed is not None and eq.replace("-", "_") not in installed:
        return f"no SpeechTranscriber assets for {locale}: ears install --apple --locale {locale}"
    return None


class AppleBackend(ProcessBackend):
    name = "apple"

    def __init__(self, rate=16000, locale=None):
        locale = locale or os.environ.get("EARS_LOCALE", "en-AU")
        super().__init__([helper_path(), "stream", "--rate", str(rate)], env=dict(os.environ, APPLE_SPEECH_LOCALE=locale))
