"""An unsigned mail through the MTA cannot fetch, route into a call or cut (EARS-335).

The repro of the three holes, measured at e2ccfb1. Each mail here is what any sender
who can reach the MTA can write: a From on this host (``allowed()`` admits it), no
signature, and, where a case says so, a forged ``X-Mouth-Raw: 1`` (a header, so a
mail can carry it as well as ``drop()`` can).

1. ``X-Mouth-File`` (or an extra's ``file``) reaches ``ffmpeg -i`` as given, and the
   decoder passes no ``-protocol_whitelist``: a URL is fetched (SSRF), and any file the
   user can read is decoded and played.
2. ``X-Mouth-Device: call:N`` (or an extra's ``device``, or a mail To a call's box)
   speaks into a live call.
3. ``X-Mouth-Priority: now`` (or an extra's ``priority``) jumps the queue and cuts the
   line playing.
4. ``Supersedes:`` is ``now`` by another route (the FOREMAN's ruling on review F2):
   an empty-bodied supersede (a retract) of the line playing cuts it; a supersede or a
   retract of another sender's queued line takes it away; and a replacement inherits
   its target's place, a local ``now`` line's rank of -1 included.

Each test asks what the mouth DOES (``Queue.lines()``, ``_cut_by_mail``, the decoder's
argv), not how a fix spells it, so it holds whether the fix refuses the line or drops
the ask. Each one failed at e2ccfb1 for the hole it names (they were committed as
strict xfails first); since fix-001 they pass. ``test_mouth_mail_trust_uses.py`` holds
the other side: what a local or signed line may still do.

In tmp_path, on the ``null`` device and the ``silence`` backend; ``subprocess.Popen`` is
a fake, so no ffmpeg runs and nothing is fetched.
"""

import json

import pytest

from mouth import backends, box, player

from maildir_put import put
from tests import env_fence

#: Not routable (TEST-NET-1): never fetched, because ffmpeg never runs here.
URL = "http://192.0.2.1/latest/meta-data/"


@pytest.fixture
def d(tmp_path, monkeypatch):
    d = tmp_path / "mouth"
    monkeypatch.setenv("MOUTH_DIR", str(d))
    monkeypatch.setenv("MOUTH_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MOUTH_DEVICE", "null")
    monkeypatch.setenv("MOUTH_BACKEND", "silence")
    env_fence.delenv(monkeypatch, "MOUTH_MAILBOX")
    env_fence.delenv(monkeypatch, "MOUTH_MAIL_LOG")
    box.ensure(box.box_dir(d))
    return d


def forged(d, name, headers, body="hello there", to=None, frm=None):
    """A mail any sender can put through the MTA: an on-host From, unsigned."""
    host = box._host()
    lines = [f"From: {frm or f'ivy@{host}'}", f"To: {to or f'mouth@{host}'}", f"Subject: {body[:40]}",
             f"Message-ID: <{name}@forged.example>"]
    lines += [f"{k}: {v}" for k, v in headers.items()]
    p = put(box.box_dir(d), name, "\n".join(lines) + "\n\n" + body + "\n")
    return p


class FakePopen:
    """ffmpeg that is never run: it records its argv and decodes nothing."""
    argv: list = []

    def __init__(self, cmd, **kw):
        FakePopen.argv.append(list(cmd))
        self.stdout = self.stderr = self
        self.returncode = 0

    def read(self, *a):
        return b""

    def wait(self):
        return 0

    def poll(self):
        return 0

    def kill(self):
        pass


def decoded(monkeypatch, line) -> list:
    """The ffmpeg argv the player's decoder runs for LINE (``player.py:378-380``)."""
    FakePopen.argv = []
    monkeypatch.setattr("subprocess.Popen", FakePopen)
    list(backends.FileSound(line["file"], line.get("start"), line.get("end")).stream("", "", None))
    return FakePopen.argv[0]


# -- 1. a file or a URL into ffmpeg ---------------------------------------------

@pytest.mark.parametrize("route", ["header-url", "header-file", "extra-url", "forged-raw-file"])
def test_a_forged_mail_cannot_make_ffmpeg_fetch_a_url_or_read_a_file(d, tmp_path, monkeypatch, route):
    secret = tmp_path / "elsewhere" / "private.wav"   # a file the user can read, not a sound
    secret.parent.mkdir()
    secret.write_bytes(b"RIFF....WAVE")
    want = URL if route.endswith("url") else str(secret)
    headers = {"extra-url": {"X-Mouth-Extra": json.dumps({"file": URL})},
               "forged-raw-file": {"X-Mouth-Raw": "1", "X-Mouth-File": want}}.get(
        route, {"X-Mouth-File": want})
    forged(d, "1.f", headers)

    asked = [x for x in box.Queue(d).lines() if x.get("file")]
    for line in asked:   # the line still asks the player to decode it: what would ffmpeg get?
        argv = decoded(monkeypatch, line)
        assert want not in argv, f"{route}: an unsigned mail handed ffmpeg {want!r}: {argv}"
    # and the decoder never fetches, whoever asked: an input option, so before -i
    argv = decoded(monkeypatch, {"file": str(secret)})
    assert "-protocol_whitelist" in argv, f"ffmpeg runs with no -protocol_whitelist: {argv}"
    i = argv.index("-protocol_whitelist")
    assert argv[i + 1] == "file" and i < argv.index("-i"), argv


# -- 2. call:N --------------------------------------------------------------------

@pytest.mark.parametrize("route", ["header", "extra", "call-box", "forged-raw"])
def test_a_forged_mail_cannot_speak_into_a_live_call(d, route):
    to = None
    headers = {"header": {"X-Mouth-Device": "call:1317"},
               "extra": {"X-Mouth-Extra": json.dumps({"device": "call:1317"})},
               "call-box": {},
               "forged-raw": {"X-Mouth-Raw": "1", "X-Mouth-Device": "call:1317"}}[route]
    if route == "call-box":   # a mail To a call's box defaults its device to call:N (box.py:568)
        to = f"calls+ivy-1317@{box._host()}"
    forged(d, "1.c", headers, to=to)

    into_call = [x for x in box.Queue(d).lines()
                 if str(x.get("device") or "").strip().lower().startswith("call")]
    assert not into_call, f"{route}: an unsigned mail plays into {into_call[0]['device']}"


# -- 3. now: jump the queue and cut the line playing -------------------------------

@pytest.mark.parametrize("route", ["header", "extra", "forged-raw", "negative"])
def test_a_forged_mail_cannot_cut_the_line_playing(d, route):
    headers = {"header": {"X-Mouth-Priority": "now"},
               "extra": {"X-Mouth-Extra": json.dumps({"priority": "now"})},
               "forged-raw": {"X-Mouth-Raw": "1", "X-Mouth-Priority": "now"},
               # a negative number sorts ahead of every now (-1) without cutting (review F3)
               "negative": {"X-Mouth-Priority": "-5"}}[route]
    forged(d, "1.a", {}, body="queued first")
    forged(d, "2.n", headers, body="forged now")
    playing = {"utt": "playing1", "mail_id": "<playing@test>", "requested_t": 1.0,
               "device": "null"}

    q = box.Queue(d)
    assert player._cut_by_mail(q, playing) is None, \
        f"{route}: an unsigned mail cuts the line playing"
    order = [x["text"] for x in q.lines()]
    assert order == ["queued first", "forged now"], f"{route}: an unsigned mail jumps the queue: {order}"


# -- 4. Supersedes: a retract cuts, a replace takes a line and its place ---------
#
# The FOREMAN's ruling on review F2 (Sun 2030-06-09): (a) cutting the PLAYING
# line by retract needs a local or signed mail, as ``now`` does; (b) an unsigned
# supersede or retract of a QUEUED line counts only from that line's own From; (c) a
# replacement that is not local or signed never inherits a rank below 0.

PLAYING_ID = "<playing@test>"


@pytest.mark.parametrize("route", ["poll", "act"])
def test_a_forged_retract_cannot_cut_the_line_playing(d, route):
    # the playing line's own From, even: ruling (a) has no same-From door
    forged(d, "1.r", {"Supersedes": PLAYING_ID}, body="")
    playing = {"utt": "playing1", "mail_id": PLAYING_ID, "requested_t": 1.0, "device": "null",
               "mail_from": f"ivy@{box._host()}"}

    q = box.Queue(d)
    if route == "poll":   # play_one's 100 ms look (player.py:547)
        assert player._cut_by_mail(q, playing) is None, "an unsigned retract cuts the line playing"
    else:                 # serve's acting pass names what plays (box.py:795-804)
        q.lines(act=True, playing=playing)
        assert not (d / "stop").exists(), \
            f"an unsigned retract stops the line playing: {(d / 'stop').read_text()}"


@pytest.mark.parametrize("route", ["replace", "retract"])
def test_a_forged_supersede_cannot_take_another_senders_line(d, route):
    host = box._host()
    forged(d, "1.c", {}, body="vera's own line", frm=f"vera@{host}")
    forged(d, "2.p", {"Supersedes": "<1.c@forged.example>"},
           body="ivy's words instead" if route == "replace" else "")

    texts = [x["text"] for x in box.Queue(d).lines()]
    assert "vera's own line" in texts, f"{route}: ivy's unsigned mail took vera's line: {texts}"


def test_a_forged_replace_cannot_inherit_a_now_lines_place(d):
    import mouth

    forged(d, "1.x", {"X-Mouth-Priority": "next"}, body="queued next")   # ungated: rank 0
    now = mouth.say("local now", priority="now", d=d, spawn=False)        # a local drop: rank -1
    (mine,) = [x for x in box.Queue(d).lines() if x["utt"] == now["utt"]]
    # its own From, so ruling (b) lets it replace: the test is of (c) alone
    forged(d, "2.r", {"Supersedes": now["mail_id"]}, body="forged replacement", frm=mine["_from"])

    lines = box.Queue(d).lines()
    (rep,) = [x for x in lines if x["text"] == "forged replacement"]
    assert rep["_rank"] >= 0, f"an unsigned replace inherits a now line's rank {rep['_rank']}"
    assert [x["text"] for x in lines] == ["queued next", "forged replacement"], \
        "an unsigned replace jumps a queued next line"
