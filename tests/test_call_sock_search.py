"""The ear and the mouth find a Delta bot's call sockets in sessionphone's root
and in an agent's own ~/.<agent>/delta-chat, so both halves of a call pair up.

sessionphone's root (EARS-372 do-001, for PHONE-270): $SESSIONPHONE_ROOT when set,
alone; else BOTH ~/.sessionkit/phone/state (new) and ~/.sessionphone (old), deduped by
realpath (the old becomes a symlink to the new for one release). $DC_ROOT is honoured
when set. The rule is written twice, keeper/phone_roots.py (stdlib; call-ears and
ears-mail.py) and ears.listen.sources (the ears and the mouth): this file holds them equal.

EARS-527: bots outside those places are reached through ~/.sessionkit/phone/run, where
sessionphone publishes run/<bot>[.<profile>]/<name> as symlinks to the bound socket
(PHONE-296), and, until every bot's phone publishes, the fallback ~/.<agent>/phone[/<profile>].
"""
import importlib.machinery
import importlib.util
import os
import sys
from pathlib import Path

import pytest

from ears import envnames
from ears.listen import sources
from mouth import output
from tests import env_fence

KEEPER = Path(__file__).resolve().parent.parent / "keeper"
NEW = (".sessionkit", "phone", "state")
RUN = (".sessionkit", "phone", "run")


def _touch(p):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").close()
    return p


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A fresh HOME with no socket override and no root set: the default search."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv("SESSIONPHONE_ROOT", raising=False)
    monkeypatch.delenv("DC_ROOT", raising=False)
    for k in envnames.group("EARS_CALL_SOCKS"):
        monkeypatch.delenv(k, raising=False)
    env_fence.delenv(monkeypatch, "MOUTH_CALL_SOCKS")
    return h


def _ears_mail():
    """keeper/ears-mail.py, loaded: it carries a stdlib copy of the search (EARS-431)."""
    spec = importlib.util.spec_from_file_location("ears_mail_socks", KEEPER / "ears-mail.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _call_ears():
    """keeper/call-ears, loaded (no .py: a SourceFileLoader)."""
    loader = importlib.machinery.SourceFileLoader("call_ears_socks", str(KEEPER / "call-ears"))
    spec = importlib.util.spec_from_loader("call_ears_socks", loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def _keeper_roots():
    """keeper/phone_roots.py, the module the two keeper scripts import from beside themselves."""
    spec = importlib.util.spec_from_file_location("phone_roots_under_test", KEEPER / "phone_roots.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _all_ears():
    """Every reader's answer for the ear leg: they must agree."""
    em, ce, pr = _ears_mail(), _call_ears(), _keeper_roots()
    got = [sources.call_socks(), em.call_socks(), ce.socks(), pr.bot_socks("call-ears.sock")]
    assert all(g == got[0] for g in got), got
    return got[0]


# ---- the default search, as it was ------------------------------------------------------

def test_both_halves_find_sessionphone_and_agent_bots(home):
    sp_ear = _touch(str(home / ".sessionphone" / "call-ears.sock"))
    sp_mouth = _touch(str(home / ".sessionphone" / "call-mouth.sock"))
    agent_ear = _touch(str(home / ".alice" / "delta-chat" / "public" / "call-ears.sock"))
    agent_mouth = _touch(str(home / ".alice" / "delta-chat" / "call-mouth.sock"))
    assert sources.call_socks() == sorted([sp_ear, agent_ear])
    assert output.call_socks() == sorted([sp_mouth, agent_mouth])


def test_sessionphone_root_is_honoured(tmp_path, home, monkeypatch):
    monkeypatch.setenv("SESSIONPHONE_ROOT", str(tmp_path / "elsewhere"))
    ear = _touch(str(tmp_path / "elsewhere" / "call-ears.sock"))
    mouth = _touch(str(tmp_path / "elsewhere" / "call-mouth.sock"))
    assert sources.call_socks() == [ear]
    assert output.call_socks() == [mouth]


def test_the_env_override_still_wins(monkeypatch):
    monkeypatch.setenv("EARS_CALL_SOCKS", "/a.sock:/b.sock")
    assert sources.call_socks() == ["/a.sock", "/b.sock"]


# ---- the new root (PHONE-270) -----------------------------------------------------------

def test_a_socket_under_the_new_root_only_is_found(home):
    ear = _touch(str(home.joinpath(*NEW) / "call-ears.sock"))
    prof = _touch(str(home.joinpath(*NEW) / "p1" / "call-ears.sock"))
    mouth = _touch(str(home.joinpath(*NEW) / "call-mouth.sock"))
    assert _all_ears() == sorted([ear, prof])
    assert output.call_socks() == [mouth]


def test_a_socket_under_the_old_root_only_is_found(home):
    ear = _touch(str(home / ".sessionphone" / "p1" / "call-ears.sock"))
    mouth = _touch(str(home / ".sessionphone" / "call-mouth.sock"))
    assert _all_ears() == [ear]
    assert output.call_socks() == [mouth]


def test_both_roots_each_with_its_own_socket_are_both_found(home):
    new = _touch(str(home.joinpath(*NEW) / "a" / "call-ears.sock"))
    old = _touch(str(home / ".sessionphone" / "b" / "call-ears.sock"))
    assert _all_ears() == sorted([new, old])


def test_the_old_root_a_symlink_to_the_new_counts_a_socket_once(home):
    """For one release ~/.sessionphone is a symlink to ~/.sessionkit/phone/state: the
    same socket reached by both spellings is one leg, named by the new root."""
    ear = _touch(str(home.joinpath(*NEW) / "call-ears.sock"))
    prof = _touch(str(home.joinpath(*NEW) / "p1" / "call-ears.sock"))
    mouth = _touch(str(home.joinpath(*NEW) / "call-mouth.sock"))
    (home / ".sessionphone").symlink_to(home.joinpath(*NEW), target_is_directory=True)
    assert sources.phone_roots() == [str(home.joinpath(*NEW))]
    assert _all_ears() == sorted([ear, prof])
    assert output.call_socks() == [mouth]


def test_the_new_root_a_symlink_to_the_old_counts_a_socket_once(home):
    """The other way round (a host that linked before it moved): still one leg."""
    old = home / ".sessionphone"
    ear = _touch(str(old / "call-ears.sock"))
    home.joinpath(*NEW).parent.mkdir(parents=True)
    home.joinpath(*NEW).symlink_to(old, target_is_directory=True)
    assert len(_all_ears()) == 1
    assert os.path.realpath(_all_ears()[0]) == os.path.realpath(ear)


def test_sessionphone_root_set_wins_alone(tmp_path, home, monkeypatch):
    """Set, it is the only phone root: neither the new nor the old default is read."""
    _touch(str(home.joinpath(*NEW) / "call-ears.sock"))
    _touch(str(home / ".sessionphone" / "call-ears.sock"))
    _touch(str(home.joinpath(*NEW) / "call-mouth.sock"))
    mine = _touch(str(tmp_path / "explicit" / "call-ears.sock"))
    mouth = _touch(str(tmp_path / "explicit" / "call-mouth.sock"))
    monkeypatch.setenv("SESSIONPHONE_ROOT", str(tmp_path / "explicit"))
    assert sources.phone_roots() == _keeper_roots().phone_roots() == [str(tmp_path / "explicit")]
    assert _all_ears() == [mine]
    assert output.call_socks() == [mouth]


def test_dc_root_is_honoured_beside_the_phone_root(tmp_path, home, monkeypatch):
    phone = _touch(str(home.joinpath(*NEW) / "call-ears.sock"))
    dc = _touch(str(tmp_path / "bot-root" / "prof" / "call-ears.sock"))
    mouth = _touch(str(tmp_path / "bot-root" / "call-mouth.sock"))
    monkeypatch.setenv("DC_ROOT", str(tmp_path / "bot-root"))
    assert _all_ears() == sorted([phone, dc])
    assert output.call_socks() == [mouth]


def test_dc_root_that_is_an_agents_own_bot_is_searched_once(home, monkeypatch):
    ear = _touch(str(home / ".alice" / "delta-chat" / "call-ears.sock"))
    monkeypatch.setenv("DC_ROOT", str(home / ".alice" / "delta-chat"))
    assert _all_ears() == [ear]
    assert sources.bot_roots().count(str(home / ".alice" / "delta-chat")) == 1


def test_the_agent_bot_glob_is_kept_beside_the_new_root(home):
    """PHONE-270 moves phone's own bots, not an agent's ~/.<agent>/delta-chat."""
    phone = _touch(str(home.joinpath(*NEW) / "call-ears.sock"))
    agent = _touch(str(home / ".bob" / "delta-chat" / "call-ears.sock"))
    _touch(str(home / ".carol" / "other" / "call-ears.sock"))          # not a delta-chat dir
    assert _all_ears() == sorted([phone, agent])


# ---- phone's published run/ dir and the ~/.<agent>/phone guess (EARS-527) ---------------

def _link(target, link):
    """LINK -> TARGET, a symlink like the ones sessionphone publishes under run/."""
    os.makedirs(os.path.dirname(link), exist_ok=True)
    os.symlink(target, link)
    return link


def test_a_bot_published_under_run_is_found_wherever_its_socket_is(tmp_path, home):
    """A bot rooted in none of the searched places (a phone.json root, say) is reached
    through the symlink phone publishes under ~/.sessionkit/phone/run."""
    ear = _touch(str(tmp_path / "elsewhere" / "bot" / "call-ears.sock"))
    mouth = _touch(str(tmp_path / "elsewhere" / "bot" / "call-mouth.sock"))
    run_ear = _link(ear, str(home.joinpath(*RUN) / "elsew" / "call-ears.sock"))
    run_mouth = _link(mouth, str(home.joinpath(*RUN) / "elsew" / "call-mouth.sock"))
    assert str(home.joinpath(*RUN)) in sources.bot_roots()
    assert _all_ears() == [run_ear]
    assert output.call_socks() == [run_mouth]


def test_a_run_symlink_and_a_direct_hit_on_one_socket_count_once(home):
    """wimo's private bot: its socket is found at ~/.wimo/phone/private (the guess) and
    through run/wimo.private (phone's symlink to it). One leg, named by run/."""
    ear = _touch(str(home / ".wimo" / "phone" / "private" / "call-ears.sock"))
    mouth = _touch(str(home / ".wimo" / "phone" / "private" / "call-mouth.sock"))
    run_ear = _link(ear, str(home.joinpath(*RUN) / "wimo.private" / "call-ears.sock"))
    run_mouth = _link(mouth, str(home.joinpath(*RUN) / "wimo.private" / "call-mouth.sock"))
    assert _all_ears() == [run_ear]
    assert output.call_socks() == [run_mouth]
    assert os.path.realpath(output.call_socks()[0]) == os.path.realpath(mouth)


def test_a_run_symlink_to_an_agents_delta_chat_bot_counts_once(home):
    """The direct ~/.<agent>/delta-chat hit comes first, so it names the one leg."""
    ear = _touch(str(home / ".ada" / "delta-chat" / "call-ears.sock"))
    _link(ear, str(home.joinpath(*RUN) / "ada" / "call-ears.sock"))
    assert _all_ears() == [ear]


def test_an_agents_own_phone_dir_and_its_profiles_are_found(home):
    """The Doctor's bot at ~/.doc/phone and wimo's at ~/.wimo/phone/private (mba)."""
    doc_ear = _touch(str(home / ".doc" / "phone" / "call-ears.sock"))
    doc_mouth = _touch(str(home / ".doc" / "phone" / "call-mouth.sock"))
    wimo_ear = _touch(str(home / ".wimo" / "phone" / "private" / "call-ears.sock"))
    wimo_mouth = _touch(str(home / ".wimo" / "phone" / "private" / "call-mouth.sock"))
    assert _all_ears() == sorted([doc_ear, wimo_ear])
    assert output.call_socks() == sorted([doc_mouth, wimo_mouth])


def test_an_unknown_dir_is_not_found(tmp_path, home):
    """Not a root, not a published symlink: not found. Nor a socket two dirs under a root."""
    _touch(str(home / ".junk" / "notphone" / "call-ears.sock"))
    _touch(str(home / ".junk" / "notphone" / "call-mouth.sock"))
    _touch(str(home / "elsewhere" / "bot" / "call-mouth.sock"))          # a phone.json root: voice reads no phone.json
    _touch(str(home / ".eve" / "phone" / "a" / "b" / "call-ears.sock"))  # deeper than [/<profile>]
    _touch(str(home / "phone" / "call-ears.sock"))                       # ~/phone is not ~/.<agent>/phone
    assert _all_ears() == []
    assert output.call_socks() == []


def test_sessionphones_own_home_is_not_an_agents_phone(tmp_path, home, monkeypatch):
    """~/.*/phone matches ~/.sessionkit/phone; it is skipped, so an explicit
    $SESSIONPHONE_ROOT still keeps ~/.sessionkit/phone/state out of the search."""
    _touch(str(home.joinpath(*NEW) / "call-ears.sock"))
    _touch(str(home.joinpath(*NEW) / "call-mouth.sock"))
    monkeypatch.setenv("SESSIONPHONE_ROOT", str(tmp_path / "explicit"))
    assert str(home / ".sessionkit" / "phone") not in sources.bot_roots()
    assert str(home / ".sessionkit" / "phone") not in _keeper_roots().bot_roots()
    assert _all_ears() == []
    assert output.call_socks() == []


def test_the_run_root_and_the_guess_come_after_the_known_roots(tmp_path, home, monkeypatch):
    """Order: phone root(s), $DC_ROOT, ~/.*/delta-chat, run/, then ~/.*/phone."""
    monkeypatch.setenv("DC_ROOT", str(tmp_path / "dc"))
    (home / ".ada" / "delta-chat").mkdir(parents=True)
    (home / ".wimo" / "phone").mkdir(parents=True)
    (home / ".doc" / "phone").mkdir(parents=True)
    home.joinpath(*NEW).mkdir(parents=True)
    want = [str(home.joinpath(*NEW)), str(home / ".sessionphone"), str(tmp_path / "dc"),
            str(home / ".ada" / "delta-chat"), str(home.joinpath(*RUN)),
            str(home / ".doc" / "phone"), str(home / ".wimo" / "phone")]
    assert sources.bot_roots() == _keeper_roots().bot_roots() == want


# ---- the web leg ------------------------------------------------------------------------

def test_the_web_leg_at_the_new_root_is_the_web_leg(home):
    em = _ears_mail()
    web = _touch(str(home.joinpath(*NEW) / "web" / "call-ears.sock"))
    bot = _touch(str(home.joinpath(*NEW) / "p1" / "call-ears.sock"))
    assert web in _all_ears()                  # found, as before: ears-mail skips it by web_leg
    assert em.web_leg(web)
    assert not em.web_leg(bot)


def test_the_web_leg_at_the_old_root_is_still_the_web_leg(home):
    em = _ears_mail()
    assert em.web_leg(str(home / ".sessionphone" / "web" / "call-ears.sock"))
    assert not em.web_leg(str(home / ".alice" / "delta-chat" / "web" / "call-ears.sock"))


def test_the_web_leg_through_the_symlink_is_the_web_leg(home):
    _touch(str(home.joinpath(*NEW) / "web" / "call-ears.sock"))
    (home / ".sessionphone").symlink_to(home.joinpath(*NEW), target_is_directory=True)
    em = _ears_mail()
    assert em.web_leg(str(home / ".sessionphone" / "web" / "call-ears.sock"))
    assert em.web_leg(str(home.joinpath(*NEW) / "web" / "call-ears.sock"))


def test_the_web_leg_under_an_explicit_root_only(tmp_path, home, monkeypatch):
    monkeypatch.setenv("SESSIONPHONE_ROOT", str(tmp_path / "explicit"))
    em = _ears_mail()
    assert em.web_leg(str(tmp_path / "explicit" / "web" / "call-ears.sock"))
    assert not em.web_leg(str(home.joinpath(*NEW) / "web" / "call-ears.sock"))


# ---- the copies are equal ---------------------------------------------------------------

def test_the_keeper_copy_of_the_rule_equals_sources(tmp_path, home, monkeypatch):
    """keeper/phone_roots.py and ears.listen.sources give the same roots, in the same
    order, over every case: nothing, new, old, both, the symlink, DC_ROOT, explicit."""
    pr = _keeper_roots()

    def same():
        assert pr.phone_roots() == sources.phone_roots()
        assert pr.bot_roots() == sources.bot_roots()
        for name in ("call-ears.sock", "call-mouth.sock"):
            assert pr.bot_socks(name) == sources.bot_socks(name)

    same()
    _touch(str(home.joinpath(*NEW) / "call-mouth.sock"))
    _touch(str(home / ".sessionphone" / "p" / "call-ears.sock"))
    _touch(str(home / ".alice" / "delta-chat" / "call-ears.sock"))
    same()
    monkeypatch.setenv("DC_ROOT", str(tmp_path / "dc"))
    _touch(str(tmp_path / "dc" / "call-ears.sock"))
    same()
    _touch(str(home / ".wimo" / "phone" / "private" / "call-mouth.sock"))
    _touch(str(home / ".doc" / "phone" / "call-ears.sock"))
    same()
    _link(str(home / ".wimo" / "phone" / "private" / "call-mouth.sock"),
          str(home.joinpath(*RUN) / "wimo.private" / "call-mouth.sock"))
    _link(str(_touch(str(tmp_path / "far" / "call-ears.sock"))),
          str(home.joinpath(*RUN) / "far" / "call-ears.sock"))
    same()
    monkeypatch.setenv("SESSIONPHONE_ROOT", str(tmp_path / "x"))
    same()
    assert pr.NEW_ROOT == sources.PHONE_NEW_ROOT and pr.OLD_ROOT == sources.PHONE_OLD_ROOT
    assert pr.RUN_ROOT == sources.PHONE_RUN_ROOT and pr.PHONE_HOME == sources.PHONE_HOME


def test_the_mouth_finds_what_the_ears_bot_socks_find(tmp_path, home, monkeypatch):
    """The third copy: output.call_socks() == bot_socks("call-mouth.sock"), in both
    twins, over the new roots as well as the old."""
    pr = _keeper_roots()

    def same():
        assert output.call_socks() == sources.bot_socks("call-mouth.sock") == pr.bot_socks("call-mouth.sock")

    same()
    _touch(str(home / ".sessionphone" / "call-mouth.sock"))
    _touch(str(home / ".alice" / "delta-chat" / "p" / "call-mouth.sock"))
    _touch(str(home / ".doc" / "phone" / "call-mouth.sock"))
    wimo = _touch(str(home / ".wimo" / "phone" / "private" / "call-mouth.sock"))
    _link(wimo, str(home.joinpath(*RUN) / "wimo.private" / "call-mouth.sock"))
    _link(_touch(str(tmp_path / "far" / "call-mouth.sock")),
          str(home.joinpath(*RUN) / "far" / "call-mouth.sock"))
    same()
    assert len(output.call_socks()) == 5
    monkeypatch.setenv("DC_ROOT", str(tmp_path / "dc"))
    _touch(str(tmp_path / "dc" / "call-mouth.sock"))
    same()


def test_ears_mail_and_call_ears_share_one_helper():
    """The two keeper scripts import phone_roots, not a copy each."""
    for script in ("ears-mail.py", "call-ears"):
        text = (KEEPER / script).read_text()
        assert "import phone_roots" in text
        assert ".sessionphone" not in text.split('"""', 2)[2], script   # no root spelt outside the helper


def test_the_mouth_searches_the_ears_roots():
    """The mouth's call_socks is the ears' bot_socks, so the two halves of a call pair up."""
    text = Path(output.__file__).read_text()
    assert "bot_socks(\"call-mouth.sock\")" in text
    assert ".sessionphone" not in text


def test_ears_mails_copy_of_the_search_equals_sources(tmp_path, home, monkeypatch):
    """ears-mail finds the same legs as sources.call_socks(), in the same order: the
    sessionphone root (its own and a profile's), every agent's own bot, the override
    and its old name, presence-read."""
    em = _ears_mail()
    assert em.call_socks() == sources.call_socks() == []
    _touch(str(home / ".sessionphone" / "call-ears.sock"))
    _touch(str(home / ".sessionphone" / "web" / "call-ears.sock"))
    _touch(str(home / ".alice" / "delta-chat" / "public" / "call-ears.sock"))
    _touch(str(home / ".bob" / "delta-chat" / "call-ears.sock"))
    _touch(str(home / ".bob" / "delta-chat" / "call-mouth.sock"))
    _touch(str(home / ".carol" / "other" / "call-ears.sock"))       # not a delta-chat dir
    assert em.call_socks() == sources.call_socks()
    assert len(em.call_socks()) == 4
    monkeypatch.setenv("SESSIONPHONE_ROOT", str(tmp_path / "elsewhere"))
    _touch(str(tmp_path / "elsewhere" / "p1" / "call-ears.sock"))
    assert em.call_socks() == sources.call_socks()
    assert em.web_leg(str(tmp_path / "elsewhere" / "web" / "call-ears.sock"))
    assert not em.web_leg(str(tmp_path / "elsewhere" / "p1" / "call-ears.sock"))
    monkeypatch.setenv("VOICEMODE_CALL_EARS_SOCKS", "/old.sock")
    assert em.call_socks() == sources.call_socks() == ["/old.sock"]
    monkeypatch.setenv("EARS_CALL_SOCKS", "/a.sock:/b.sock")
    assert em.call_socks() == sources.call_socks() == ["/a.sock", "/b.sock"]
    monkeypatch.setenv("EARS_CALL_SOCKS", "")                       # set: the old name is not read
    assert em.call_socks() == sources.call_socks()
    assert "/old.sock" not in em.call_socks()


def test_the_keeper_scripts_find_the_helper_without_their_dir_on_sys_path(monkeypatch):
    """A loader (a test, the keeper from a wheel) need not put keeper/ on sys.path."""
    monkeypatch.setattr(sys, "path", [p for p in sys.path if os.path.abspath(p) != str(KEEPER)])
    monkeypatch.delitem(sys.modules, "phone_roots", raising=False)
    assert callable(_ears_mail().call_socks)
    assert callable(_call_ears().socks)
