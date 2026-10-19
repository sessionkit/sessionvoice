"""EARS-136: `ears backends` says apple is NOT ok when SpeechTranscriber has no assets for the
locale. why_not() checked only Darwin, macOS >= 27 and that a helper file existed, so it said
`apple ok` right after the asset install failed, and `stream` then died at the first audio.

The helper is a fake: a /bin/sh script that prints canned `apple-speech locales` output for
the APPLE_SPEECH_LOCALE it is given (lines copied from the real helper on macOS 27.0.1, EARS-136
rca). This exercises the real subprocess and parse with no Speech framework and no download.
"""
import os
import stat

import pytest

from ears import backends, cli
from ears.backends import apple

INSTALLED = "en_AU en_CA en_GB en_IE en_IN en_NZ en_SG en_US en_ZA"

FAKE = r"""#!/bin/sh
[ "$1" = locales ] || { echo "usage: ..." >&2; exit 0; }
case "$APPLE_SPEECH_LOCALE" in
  xx-XX) eq=nil ;;
  *) eq=$(printf %s "$APPLE_SPEECH_LOCALE" | tr - _) ;;
esac
echo "SpeechTranscriber.isAvailable: true"
echo "SpeechTranscriber supported (3): de_DE en_AU en_US"
echo "SpeechTranscriber installed (9): @INSTALLED@"
echo "DictationTranscriber supported: 54, installed: en_AU de_DE"
echo "equivalent to $APPLE_SPEECH_LOCALE: $eq"
echo "status [transcriber]: supported"
echo "reserved: [] max: 5"
"""


def _helper(tmp_path, body):
    p = tmp_path / "apple-speech"
    p.write_text(body)
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return str(p)


@pytest.fixture
def mac27(monkeypatch, tmp_path):
    """A macOS 27 host whose helper is the fake; returns a function to swap the fake's body."""
    monkeypatch.setattr(apple.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(apple.platform, "mac_ver", lambda: ("27.0.1", ("", "", ""), ""))
    monkeypatch.delenv("EARS_LOCALE", raising=False)

    def use(body=FAKE.replace("@INSTALLED@", INSTALLED)):
        monkeypatch.setenv("EARS_APPLE_HELPER", _helper(tmp_path, body))
    use()
    return use


def test_installed_locale_is_ok(mac27):
    assert apple.why_not() is None                  # en-AU, the default
    assert apple.why_not("en-US") is None


def test_supported_but_not_installed_names_the_fix(mac27):
    why = apple.why_not("de-DE")
    assert why == "no SpeechTranscriber assets for de-DE: ears install --apple --locale de-DE"


def test_unsupported_locale_says_so(mac27):
    why = apple.why_not("xx-XX")
    assert why.startswith("locale xx-XX not supported by SpeechTranscriber")


def test_locale_defaults_to_ears_locale_like_the_backend(mac27, monkeypatch):
    monkeypatch.setenv("EARS_LOCALE", "de-DE")
    assert "ears install --apple --locale de-DE" in apple.why_not()


def test_nothing_installed_at_all(mac27):
    mac27(FAKE.replace("@INSTALLED@", ""))
    assert apple.why_not() == "no SpeechTranscriber assets for en-AU: ears install --apple --locale en-AU"


def test_hyphenated_identifiers_still_match(mac27):
    mac27(FAKE.replace("@INSTALLED@", INSTALLED.replace("_", "-")))
    assert apple.why_not("en-AU") is None


@pytest.mark.parametrize("body", [
    "#!/bin/sh\nexit 1\n",                              # crashed
    "#!/bin/sh\necho something else entirely\n",       # nothing parseable
    "#!/bin/sh\necho 'equivalent to en-AU: en_AU'\n",   # no installed line: cannot judge
    "#!/bin/sh\nkill -TRAP $$\n",                       # the Speech framework's SIGTRAP
])
def test_a_probe_that_cannot_answer_fails_open(mac27, body):
    mac27(body)
    assert apple.why_not() is None


def test_a_hung_probe_fails_open_within_its_timeout(mac27, monkeypatch):
    mac27("#!/bin/sh\nexec sleep 30\n")
    monkeypatch.setattr(apple, "PROBE_TIMEOUT", 0.3)
    assert apple.why_not() is None


def test_ears_backends_prints_apple_no(mac27, monkeypatch, capsys):
    monkeypatch.setenv("EARS_LOCALE", "de-DE")
    monkeypatch.setattr(backends._mod("nemotron"), "why_not", lambda: "faked")
    monkeypatch.setattr(backends._mod("whisper"), "why_not", lambda: "faked")
    monkeypatch.setattr(cli, "silero_model_path", lambda: None)
    assert cli.main(["backends"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "apple     no: no SpeechTranscriber assets for de-DE: ears install --apple --locale de-DE" in out


def test_auto_pick_skips_apple_without_assets(mac27, monkeypatch):
    monkeypatch.setenv("EARS_LOCALE", "de-DE")
    monkeypatch.setattr(backends._mod("nemotron"), "why_not", lambda: None)
    assert backends.pick("auto") == "nemotron"


def test_the_probe_runs_only_once_the_helper_is_found(monkeypatch):
    # no helper: the old reason, and nothing is spawned
    monkeypatch.setattr(apple.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(apple.platform, "mac_ver", lambda: ("27.0", ("", "", ""), ""))
    monkeypatch.setattr(apple, "helper_path", lambda: None)
    monkeypatch.setattr(apple.subprocess, "run", lambda *a, **kw: pytest.fail("probed without a helper"))
    assert "`ears install --apple`" in apple.why_not()
