"""The env fence: the variables ``tests/conftest.py`` pops or sets before any test
module is imported, so the runner's own environment never reaches a test.

:data:`FENCE` is the table, ``name -> None`` (popped) or the value it is set to.
:func:`apply` does each entry to EVERY name in the variable's alias group
(``ears.envnames.group``): the NEW name wins over its OLD one (EARS-294), so a fence
that set only one of them would be bypassed by a runner that exports the other, and
a fenced sendmail would mail for real. ``tests/test_envnames.py`` holds this table
and the alias table together.
"""
import os
import shlex
import sys

from ears import envnames

_HERE = os.path.dirname(os.path.abspath(__file__))

FENCE: dict = {
    # ...nor reaches a real audio device (EARS-320): the host's choice of output device
    # never reaches a test.
    "MOUTH_DEVICE": None,
    # ...nor its settings file's `output` (EARS-361): with no --device and no env, `mouth say`
    # plays on it, so a runner that exports $MOUTH_SETTINGS would answer every
    # test that expects a refusal. The tests that want one set their own, in tmp.
    "MOUTH_SETTINGS": None,
    # ...nor a runner's talk socket mode (EARS-338 S6): TALK_UNIX_SOCKET would turn every
    # `talk serve` into socket mode and make `talk pair` refuse its 127.0.0.1 default.
    # tests/test_talk_unix.py sets it where it means to.
    "TALK_UNIX_SOCKET": None,
    # ...nor the runner's own voice (EARS-329): a caller's $MOUTH_VOICE outranks
    # the host file for `mouth say`, so a seat that exports one would answer over every
    # fixture's settings file. The env's own tests set it back with monkeypatch.
    "MOUTH_VOICE": None,
    # ...nor this machine's settings cascade (EARS-144): `mouth say` would read the
    # session, seat and agent layers of whatever seat runs the suite. "" turns it off;
    # the cascade's own tests set it back.
    "MOUTH_SESSION_BIN": "",
    # ...nor this machine's mail verifier (EARS-335): set at all, `message verify --json --trust`
    # never runs and no mail is signed. A test that wants a signed mail fakes the oracle
    # (tests/mouth/conftest.py `signed`), or puts its own `message` on PATH and unsets it.
    "MOUTH_VERIFY_BIN": "",
    # ...nor mail anyone (EARS-164): a dropped line's notice goes through this command
    # (it takes `message send`'s arguments since EARS-381), which ignores them, writes
    # nothing and exits 75. tests/mouth's fence points it at a fake `message send` that
    # delivers into the fenced HOME instead.
    "MOUTH_SENDMAIL": " ".join(shlex.quote(x) for x in (
        sys.executable, os.path.join(_HERE, "fixtures", "refuse_sendmail.py"))),
    "MOUTH_NOTICE": None,
    # ...nor sign with a runner's mouth key (EARS-381): set, every notice a test sends
    # would carry it and must sign. The child env's own tests set fakes.
    "MOUTH_SIGNER_SOCK": None,
    "MOUTH_SIGNER_PUB": None,
    # ...nor mail a heard turn (EARS-381): keeper/ears-mail.py sends through this
    # command (`message send`'s arguments), so a test that runs it without --dry-run
    # reaches the refusing fixture, never the real `message`. Its own tests point it
    # at tests/fixtures/fake_message.py.
    "EARS_MAIL_MESSAGE": " ".join(shlex.quote(x) for x in (
        sys.executable, os.path.join(_HERE, "fixtures", "refuse_sendmail.py"))),
    # ...nor sign with a runner's keeper key (EARS-381), as MOUTH_SIGNER_* above.
    "EARS_MAIL_SIGNER_SOCK": None,
    "EARS_MAIL_SIGNER_PUB": None,
    # ...nor the runner's own sessionvoice home (EARS-294 T7): $SESSIONVOICE_HOME outranks
    # $VOICEMODE_BASE_DIR, so a runner that exports it would send every test that sets only
    # the old name to the runner's real heard log. Tests set the one they mean.
    "SESSIONVOICE_HOME": None,
    "VOICEMODE_BASE_DIR": None,
    # ...nor the runner's recording switch (EARS-387): EARS_RECORD outranks ears.json, so a
    # runner that exports EARS_RECORD=1 would turn every default-off test on. The switch's
    # own tests set it where they mean to.
    "EARS_RECORD": None,
    "EARS_RECORD_DAYS": None,
    # ...and the call bot's two notices, which `ears recording status` reports (default on)
    "EARS_RECORD_NOTICE": None,
    "EARS_RECORD_SPOKEN_NOTICE": None,
}


#: What ``tests/mouth/conftest.py`` fences for EVERY mouth test (its ``heard_box``
#: fixture, through :func:`fence_test`): ``"set"`` to a fake the fixture makes in tmp,
#: or ``None``, popped. Each under every name it has, like :data:`FENCE`; the reasons
#: are in that conftest's docstring. ``tests/test_envnames.py`` holds this table to the
#: alias table too.
MOUTH_TEST_FENCE: dict = {
    "MOUTH_HEARD_BOX": "set",
    "MOUTH_MAILBOX": None,
    "MOUTH_MAIL_LOG": None,
    "MOUTH_TTS_KEY": None,
    "OPENAI_API_KEY": None,
    "MOUTH_MPV_SOCKET": "set",
    "MOUTH_SENDMAIL": "set",
    "MOUTH_FAKE_MAIL_ROOT": "set",
    "MOUTH_NOTICE": None,
    "MOUTH_FAKE_SENDMAIL_SLEEP": None,
    "MOUTH_FAKE_SENDMAIL_EXIT": None,
}


def setenv(monkeypatch, name, value) -> None:
    """``monkeypatch.setenv`` under every name of ``name``'s alias group, so neither
    name a runner exports survives (EARS-294: the new name wins over the old)."""
    for n in envnames.group(name):
        monkeypatch.setenv(n, value)


def delenv(monkeypatch, name) -> None:
    """``monkeypatch.delenv`` under every name of ``name``'s alias group: a test that
    clears only the new name would still read a runner's old one."""
    for n in envnames.group(name):
        monkeypatch.delenv(n, raising=False)


def fence_test(monkeypatch, values: dict) -> None:
    """Apply :data:`MOUTH_TEST_FENCE` through ``monkeypatch``. ``values`` gives each
    ``"set"`` entry its fake (a KeyError names one the caller forgot)."""
    for name, kind in MOUTH_TEST_FENCE.items():
        if kind is None:
            delenv(monkeypatch, name)
        else:
            setenv(monkeypatch, name, values[name])


def apply(environ=None, fence=None) -> None:
    """Pop or set each fenced variable under every one of its names."""
    env = os.environ if environ is None else environ
    for name, value in (FENCE if fence is None else fence).items():
        for n in envnames.group(name):
            if value is None:
                env.pop(n, None)
            else:
                env[n] = value
