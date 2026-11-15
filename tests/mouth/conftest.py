"""Fences for every mouth test: no test reads or writes the live mail (EARS-30).

- Every variable below is fenced under its old ``VOICEMODE_MOUTH_`` name as well
  (EARS-294): the table is ``tests/env_fence.py``'s ``MOUTH_TEST_FENCE``.
- ``MOUTH_HEARD_BOX`` is a tmp maildir, so a reply's parent lookup
  never reads the live heard box (``~/.mail/agents/<box>/heard``: the user's
  words). The fake ``message`` (tests/conftest.py) answers from the fake HOME's
  ``.mail`` (EARS-502, SM 0.3.3), so a test that unsets ``MOUTH_HEARD_BOX`` gets the
  default box (``ears.heardbox``) under tmp, never a box the runner's sessionmail names.
- ``MOUTH_MAILBOX`` and ``MOUTH_MAIL_LOG`` are unset, so
  no line or log entry lands in the live mouth box. A test that wants a box
  sets its own, in tmp (its fixtures run after this one).
- ``MOUTH_SENDMAIL`` is ``fixtures/fake_sendmail.py`` (EARS-164): a fake
  ``message send`` (EARS-381) that builds a drop notice and "delivers" it into
  the fenced ``$HOME/.mail/agents/<local part>/new``; it never reaches sessionmail. ``MOUTH_FAKE_MAIL_ROOT`` names that root; without it
  the fake writes nothing. The notice send budget starts fresh for every test.
- ``hold.barge`` is shadowed (EARS-270, `barge_traced`). Every call a test makes
  from its own thread runs twice at one pinned ``now``, once without ``trace=``
  and once with it, and the two must return the same.
- ``HOME`` is a tmp dir as well. The mouth's live box is ``~/.mail/agents/
  mouth`` for the live mouth dir, and ``MOUTH_MAIL_LOG=1`` means
  that box too: with HOME fenced, even a test that slips past the variables
  above lands in tmp, never in ``~/.mail``. With it, the clone voices and
  ``~/.voicemode/voicemode.env`` the resolver reads are the fenced HOME's
  (``fresh_voices`` drops the list a test before read).
- ``MOUTH_TTS_KEY`` and ``OPENAI_API_KEY`` are unset (EARS-310), so
  no mouth test sends a key that happens to be in the runner's env. A test
  that wants a key sets a fake one.
- ``MOUTH_MPV_SOCKET`` is a path in tmp with nothing behind it
  (EARS-293), so a runner's ``MOUTH_DUCK`` never lowers the live
  DJ's music. A test that wants an mpv starts a fake one and names its socket.
- No thread outlives its test (``threads_settled``, EARS-550): teardown waits for every
  thread the test started, before ``monkeypatch`` puts the env back, so a late player's
  ``message`` call meets this test's fake, never the next test's fence.
- ``signed`` (EARS-335, EARS-345; not autouse) fakes ONLY the mail verifier: a mail
  carrying ``X-Session-Signature`` verifies, and its ``--json`` verdict (``verdict_of``)
  goes through the mouth's real ``box.parse_verdict``. ``tests/conftest.py`` sets
  ``MOUTH_VERIFY_BIN=""``, so no test runs this host's ``message verify``.
"""

from __future__ import annotations

import base64
import json
import re
import shlex
import sys
import threading
import time
from pathlib import Path

import pytest

from tests import env_fence

FAKE_SENDMAIL = Path(__file__).parent / "fixtures" / "fake_sendmail.py"


@pytest.fixture(autouse=True)
def heard_box(tmp_path_factory, monkeypatch, fake_message_on_path) -> Path:
    """The heard box every mouth test sees: an empty maildir in tmp."""
    root = tmp_path_factory.mktemp("fence")
    hb = root / "heard"
    for sub in ("tmp", "new", "cur"):
        (hb / sub).mkdir(parents=True)
    home = root / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    agents = home / ".mail" / "agents"
    agents.mkdir(parents=True)        # the fenced mail root's boxes folder: the fake `message`
    from ears import boxes            # (fake_message_on_path) answers every box under it
    boxes.forget()
    # Each variable under every name it has (EARS-294): the new MOUTH_ name wins over
    # the old MOUTH_ one, so a fence on one name alone could be got round.
    # The table, popped or set, is tests/env_fence.py's MOUTH_TEST_FENCE: the mailbox
    # and mail log unset, no runner's TTS key (EARS-310) or OpenAI key ever sent, the
    # live DJ's mpv never reached (EARS-293), and the fake `message send` (EARS-164).
    env_fence.fence_test(monkeypatch, {
        "MOUTH_HEARD_BOX": str(hb),
        "MOUTH_MPV_SOCKET": str(root / "no-mpv.sock"),
        "MOUTH_SENDMAIL": " ".join(shlex.quote(x) for x in (sys.executable, str(FAKE_SENDMAIL))),
        "MOUTH_FAKE_MAIL_ROOT": str(agents),
    })
    from mouth import notice

    monkeypatch.setattr(notice, "_BUDGET", notice._Budget())
    return hb


#: How long a test's teardown waits for the threads it started before it fails, by name.
#: A player run with ``idle_exit_s=0.3`` ends well inside a second on an idle host, and a
#: long line (``LONG`` in test_mouth_notice) plays about 7 s; a host at load 40 runs that
#: several times slower. Past this a thread is stuck, not slow.
SETTLE_S = 60.0


def settle(threads, bound: float) -> list:
    """Join THREADS, all within BOUND seconds in total; the ones still alive after it."""
    end = time.monotonic() + bound
    for t in threads:
        t.join(max(0.0, end - time.monotonic()))
    return [t for t in threads if t.is_alive()]


def started_since(before) -> list:
    """The joinable threads alive now that were not in BEFORE: the ones this test started,
    or a thread of its started. A foreign thread Python only wraps (a ``_DummyThread``:
    an audio callback, a C library's own) cannot be joined and is not counted."""
    me = threading.current_thread()
    return [t for t in threading.enumerate()
            if t not in before and t is not me and not isinstance(t, threading._DummyThread)]


@pytest.fixture(autouse=True)
def threads_settled(monkeypatch, heard_box):
    """No thread a test started outlives it: its teardown waits for each one, up to
    ``SETTLE_S``, and fails the test naming any still running.

    A thread left running carries on into whatever test runs next, and so do its child
    processes. A player outlived its test's ``join(5)`` (its barged line resumed, ~7 s on)
    and dropped a line after the test ended: the drop asked sessionmail for the sender's
    box (``message path ivy``, ``notice.recipient``) once the test's fake ``message`` was
    off PATH and HOME was the session's again. The mail fence refused it, but the refusal
    named a later test, still in its setup, and failed the run with no test to blame.

    The wait is here, at the end of the test's own teardown, and before ``monkeypatch``
    (which ``heard_box`` and this fixture ask for, so it is set up first and torn down
    last) puts the env back: a late thread still meets this test's fake ``message``,
    fenced HOME and fake ``message send``. A fixture the test asked for (a fake server)
    is torn down before this runs, so the thread it was serving ends first."""
    before = set(threading.enumerate())
    yield
    left = settle(started_since(before), SETTLE_S)
    if left:
        names = ", ".join(f"{t.name} ({_target(t)})" for t in left)
        pytest.fail(f"{len(left)} thread(s) this test started still ran {SETTLE_S:.0f} s after it "
                    f"ended: {names}. Join every thread a test starts: one left running runs on "
                    "into the next test, and its sessionmail calls meet the mail fence there.",
                    pytrace=False)


def _target(t: threading.Thread) -> str:
    f = getattr(t, "_target", None)
    return getattr(f, "__qualname__", None) or type(t).__qualname__


@pytest.fixture(autouse=True)
def fresh_voices(monkeypatch):
    """EARS-292: the resolver keeps the voices it read; every test reads its own fenced HOME's."""
    from mouth import resolver

    monkeypatch.setattr(resolver, "_loaded", False)
    monkeypatch.setattr(resolver, "_registry", resolver._Registry())


def _cold_floor(caches: dict) -> None:
    for c in caches.values():
        c.update(t=0.0, v=None)


@pytest.fixture(autouse=True)
def cold_floor_cache():
    """Every mouth test starts and ends with hold's floor cache cold.

    ``hold._look_floor`` keeps the newest floor it scanned in a module-level dict,
    ``hold._caches`` (one entry per set of ears), and trusts it for ``max_age_s``
    (0.1 s) whenever ``directory`` is None. A test that leaves a ``speaking`` partial
    there hands it to whichever test runs next inside that window: that test's player
    reads the user as talking, holds its room-held lines and prefetches the first of
    them, so ``test_mouth.py``'s "the next line is ready" saw its FIRST line marked
    ``prefetched`` in a shuffled order. ``test_floor_cache_fence.py`` pins this fixture.

    Emptied in place (``hold._cache`` is the ``mic`` entry), before and after. A test
    may swap in its own dict with ``monkeypatch``; the dict the module held at the
    start is emptied as well as whichever it holds at the end, so the cache is cold
    whether this runs before or after ``monkeypatch``'s undo. The per-test resets the
    mouth tests make mid-test stay: they cold the cache between a test's own writes."""
    from mouth import hold

    own = hold._caches
    _cold_floor(own)
    yield
    _cold_floor(own)
    _cold_floor(hold._caches)


@pytest.fixture(autouse=True)
def barge_traced(monkeypatch):
    """EARS-270 do-002: tracing never changes what `hold.barge` returns.

    This fixture wraps every barge scenario the mouth tests already have. When
    a test calls `hold.barge` from its own thread without ``trace=``, the call
    runs twice, without and with ``trace=[]``, at the same ``now`` (pinned, so
    a label wait cannot tip between the two calls). The result must be
    identical, and the untraced one is returned. The player's calls already
    pass ``trace=`` and run in its own thread, so they pass straight through.
    Comparing them would race a test's writes to the heard log."""
    from mouth import hold

    real = hold.barge

    def barge(*a, **kw):
        if "trace" in kw or threading.current_thread() is not threading.main_thread():
            return real(*a, **kw)
        if len(a) < 4 and kw.get("now") is None:
            kw["now"] = time.time()
        got = real(*a, **kw)
        trace: list = []
        assert real(*a, trace=trace, **kw) == got, ("barge() changed with trace=", a, kw, trace)
        return got

    barge.shadows = real
    monkeypatch.setattr(hold, "barge", barge)


#: A v2 signature's floor (sessionmail ``_sig.py`` FLOOR) and v1's fixed list.
_SIG_FLOOR = {"1": ("from", "to", "subject", "date", "message-id"),
              "2": ("from", "to", "cc", "subject", "date", "message-id", "in-reply-to",
                    "references", "supersedes", "mime-version", "content-type", "x-session-from")}


#: One p= entry as the sender writes it: standard base64 of a SHA-256, padded.
_SIG_DIGEST = "A" * 43 + "="


def _local_part(v: bytes) -> str:
    """What a v1 signature signs of From and To (sessionmail ``session-mail-verify``
    ``local_part()``): the last ``<...>``'s content, or the whole field, then all before
    the first ``@``, lower-cased."""
    t = " ".join(v.decode("utf-8", "replace").split())
    i = t.rfind("<")
    j = t.find(">", i + 1) if i != -1 else -1
    if i != -1 and j != -1:
        t = " ".join(t[i + 1:j].split())
    return t.split("@", 1)[0].lower()


def _header_fields(raw: bytes) -> list:
    """RAW's header as ``(lower-cased name, value bytes)``, the way sessionmail's
    ``_sig.fields()`` reads it: split on LF alone, continuations joined, a line that is
    no ``NAME:`` skipped. A bare CR is a byte of a value here."""
    lf = raw.replace(b"\r\n", b"\n")
    end = lf.find(b"\n\n")
    block = lf if end == -1 else lf[:end + 1]
    out: list = []
    cur = None
    for line in block.split(b"\n"):
        if line[:1] in (b" ", b"\t"):
            if cur is not None:
                cur[1] += b"\n" + line
            continue
        cur = None
        name, colon, value = line.partition(b":")
        name = name.rstrip(b" \t")
        if colon and re.fullmatch(rb"[!-9;-~]+", name):
            cur = [name.decode("ascii").lower(), value]
            out.append(cur)
    return out


def verdict_of(raw: bytes, rc: int = 4) -> tuple:
    """``(exit, stdout)`` a ``message verify --json --trust`` would give for RAW, if its
    signature verified: a FAKE of the oracle (sessionmail ``docs/verify-json-verdict.md``,
    schema 1), never a second verifier. The version and ``h`` are what the mail's own
    ``X-Session-Signature`` says, read as the verifier read it before SM-787 (the field
    as UTF-8 text, each tag's key ``str.strip()``-ed, the LAST tag of a key kept): the
    most a relay could make it read. Each covered value is read back from the header as
    ``_sig.fields()`` splits it, collapsed (v1: From and To reduced to the local part).
    No signature, or no v1/v2: exit 1 and no object."""
    sig = [v for n, v in _header_fields(raw) if n == "x-session-signature"]
    if not sig:
        return 1, b""
    tags: dict = {}
    for seg in " ".join(sig[0].decode("utf-8", "replace").split()).split(";"):
        k, eq, v = seg.partition("=")
        if eq:
            tags[k.strip()] = "".join(v.split())
    ver = tags.get("v")
    if ver not in ("1", "2"):
        return 1, b""
    h = list(_SIG_FLOOR["1"]) if ver == "1" else [n for n in tags.get("h", "").split(":") if n]
    fields = _header_fields(raw)
    covered = []
    for name in h:
        vals = [v for n, v in fields if n == name] or [b""]
        if ver == "1" and name in ("from", "to"):
            vals = [_local_part(v).encode("utf-8") for v in vals]
        else:
            vals = [b" ".join(v.split()) for v in vals]
        try:
            covered.append({"name": name, "values": [v.decode("utf-8") for v in vals]})
        except UnicodeDecodeError:
            covered.append({"name": name, "values_b64": [base64.b64encode(v).decode() for v in vals]})
    trust = ({"verdict": "FULL", "leg": None, "reason": None, "agent": "ivy",
              "line": "TRUST: FULL"} if rc == 0 else
             {"verdict": "UNBLESSED", "leg": None, "reason": "bless-absent", "agent": "ivy",
              "line": "TRUST: UNBLESSED reason=bless-absent"})
    obj = {"schema": 1, "rc": rc, "result": "good", "reason": trust["reason"], "trust": trust,
           "signature": {"version": ver, "h": h, "covered": covered,
                         "parts": [{"index": 1, "type": "text/plain", "digest": None}],
                         "signer": {"key": "ssh-ed25519 AAAAfake", "session_id": "fake"}}}
    return rc, (json.dumps(obj, sort_keys=True) + "\n").encode("ascii")


class _Signed:
    """``signed(version="2", *extra_names)``: an ``X-Session-Signature`` value, the
    version's floor plus the names. ``signed.settle(d)``: every mail in the box's new/
    is read now, as an acting pass reads it, so its verdict has landed before a test
    reads through a ``Queue`` that never waits (a bed's poll, ``_cut_by_mail``)."""

    def __call__(self, version: str = "2", *names: str) -> str:
        # ": " not ":": a long h= with no space is one word, and EmailMessage then folds
        # it as RFC 2047 encoded words, which the verifier never decodes (nor, since the
        # review of fix-001, does the mouth). Both drop the whitespace in a tag's value.
        # Ruling B (EARS-335): the header is read only as the sender writes it, so the
        # fake carries v2's p= (one 44-character digest) as _sig.format_header does.
        h = ": ".join([*_SIG_FLOOR[version], *(n.lower() for n in names)])
        p = f"p={_SIG_DIGEST}; " if version == "2" else ""
        return f"v={version}; a=sshsig; n=test; h={h}; {p}b=fake"

    def settle(self, where) -> None:
        from mouth import box

        bx = Path(where) if (Path(where) / "new").is_dir() else box.box_dir(Path(where))
        for p in sorted((bx / "new").iterdir()):
            box.read(p, sync=True)


@pytest.fixture
def signed(monkeypatch):
    """A fake verifier (EARS-335, EARS-345): a mail carrying ``X-Session-Signature``
    verifies, and its verdict (``verdict_of``: the version, ``h`` and covered values the
    mail itself carries) goes through the mouth's own ``box.parse_verdict``. Only the
    oracle is faked (no ``message`` runs; ``$MOUTH_VERIFY_BIN`` stays the
    fence)."""
    from mouth import box

    monkeypatch.setattr(box, "verify_signed",
                        lambda path: box.parse_verdict(*verdict_of(Path(path).read_bytes())))
    return _Signed()
