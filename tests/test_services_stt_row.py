"""`ears services status`'s `stt` row while its agent is not installed (EARS-488).

The `stt` job is the optional Apple STT server (`ears services install --apple`), so a default
install has no plist for it and the row read `stt    not installed`, while the speech server on
:8890 was the ears' final STT. A reader took that for "no speech-to-text". The row keeps its
leading words and now says which server it means and what the ears hear through, read from
ears.json's final, never probed.

Offline: test_services_speech's fence (a temp HOME, launchctl faked, the speech probe faked)."""
import json
import re
from pathlib import Path

import pytest

from ears import services
from ears.listen.stt import conf_path
from test_services_install_checks import fence  # noqa: F401  (a fixture)
from test_services_speech import sx  # noqa: F401  (a fixture)

SPEECH = "http://127.0.0.1:8890/v1"
PARAKEET = services.DEFAULT_STT_MODEL
OPTIONAL = "stt    not installed (the optional --apple STT server)"


def _conf(fence, d):
    """Write ears.json where the fence points EARS_CONF (under the temp HOME)."""
    p = Path(conf_path())
    assert str(p).startswith(str(fence["home"]))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(d if isinstance(d, str) else json.dumps(d))


def _stt_rows(capsys):
    assert services.status() == 0
    return [l for l in capsys.readouterr().out.splitlines() if l.startswith("stt ")]


def test_the_speech_server_as_final_is_named_on_the_stt_row(sx, fence, capsys):
    """The SK-226 walk: no stt plist, final = the speech server. The row says what serves STT."""
    _conf(fence, {"final": {"url": SPEECH, "model": PARAKEET}})
    assert not (services.AGENTS / "dev.sessionkit.voice.ears-stt.plist").exists()
    assert _stt_rows(capsys) == [
        f"{OPTIONAL}; the ears hear through {SPEECH} ({PARAKEET}), the speech server"]


def test_the_leading_words_still_match(sx, fence, capsys):
    """Anything that matched `stt    not installed` keeps matching."""
    _conf(fence, {"final": {"url": SPEECH}})
    rows = _stt_rows(capsys)
    assert len(rows) == 1 and re.match(r"^stt\s+not installed\b", rows[0])
    assert rows[0] == f"{OPTIONAL}; the ears hear through {SPEECH}, the speech server"   # no model, none said


def test_the_old_name_stt_is_read_too(sx, fence, capsys):
    _conf(fence, {"stt": {"url": SPEECH, "model": PARAKEET}})
    assert _stt_rows(capsys)[0].endswith(f"through {SPEECH} ({PARAKEET}), the speech server")


@pytest.mark.parametrize("url", ["http://127.0.0.1:2023/v1", "http://10.0.0.5:8890/v1"])
def test_another_server_is_named_but_not_called_the_speech_server(sx, fence, capsys, url):
    _conf(fence, {"final": {"url": url}})
    assert _stt_rows(capsys) == [f"{OPTIONAL}; the ears hear through {url}"]


def test_a_speech_port_moved_by_env_is_still_the_speech_server(sx, fence, capsys, monkeypatch):
    monkeypatch.setenv("EARS_SPEECH_PORT", "18890")
    _conf(fence, {"final": {"url": "http://localhost:18890/v1"}})
    assert _stt_rows(capsys)[0].endswith("through http://localhost:18890/v1, the speech server")


@pytest.mark.parametrize("conf", [None, "{not json", {"final": {}}, {"final": {"url": ""}},
                                  {"final": "http://x"}, ["a list"]])
def test_no_readable_final_says_only_which_server_the_row_means(sx, fence, capsys, conf):
    if conf is not None:
        _conf(fence, conf)
    assert _stt_rows(capsys) == [OPTIONAL]


def test_a_control_character_in_the_url_is_not_printed(sx, fence, capsys):
    _conf(fence, {"final": {"url": "http://127.0.0.1:8890/v1\x1b[2J", "model": "m\nx"}})
    row = _stt_rows(capsys)[0]
    assert "\x1b" not in row and "\n" not in row and row.endswith("(mx), the speech server")


def test_an_installed_stt_agent_keeps_its_plain_state(sx, fence, capsys):
    """--apple installed it: the row is that agent's status, nothing appended."""
    _conf(fence, {"final": {"url": SPEECH}})
    services.AGENTS.mkdir(parents=True, exist_ok=True)
    (services.AGENTS / "dev.sessionkit.voice.ears-stt.plist").write_text("<plist/>")
    assert _stt_rows(capsys) == ["stt    loaded, not running"]


def test_other_rows_keep_plain_not_installed(sx, fence, capsys):
    _conf(fence, {"final": {"url": SPEECH}})
    services.status()
    out = capsys.readouterr().out
    assert re.search(r"^mouth\s+not installed$", out, re.M), out
