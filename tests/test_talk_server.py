"""Tests for talk.server's state poll: the two-tick "rode to" mark (TALK-2).

Run: make test ARGS=tests/test_talk_server.py

Stdlib only. HOME, VOICEMODE_BASE_DIR and SESSIONMAIL_HOME point at a temp dir
(EARS_HOME and SESSIONS_DIR are cleared), sessiontools is hidden from import
unless a test plants a fake one, and every subprocess.run is faked (`session
list --json`, and `session path sessions` answering "unknown verb" unless a test
says otherwise), so nothing here touches a real mail or tools folder,
~/.voicemode, a real `session` command or a live session.
"""
import json, os, shutil, subprocess, sys, tempfile, types, unittest
from datetime import date
from unittest import mock

from talk import server
from tests._anchor import ANCHOR

IVY = "388612b6-62b4-9c67-a5e5-2576659b3d37"
VERA = "534f82f3-84ef-7c31-7781-d6f74f03b7de"


class _Stop(Exception):
    """Raised by the faked time.sleep: ends poll_state() after one pass."""


def heard_mail(seq=None, body="hello", extra=""):
    h = ("From: Ears <heard+airpods@host1>\nTo: heard+airpods@host1\n"
         "Subject: Said something\nEars-Kind: spoken\n")
    if seq is not None:
        h += f"Ears-Seq: {seq}\n"
    return h + extra + "Content-Type: text/plain\n\n" + body + "\n"


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="talk-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self._mk("home")
        self.homes = self._mk("home/.session/agents/home")
        self.cursors = self._mk("vm/state/heard-cursor")     # ears.heard.cursor_dir()
        self.sessions = self._mk("sessions")                 # the legacy root: $SESSIONMAIL_HOME/sessions
        self.logdir = self._mk("vm/logs/conversations")      # ears.heard.log_dir()
        env = {k: v for k, v in os.environ.items()
               if k not in ("EARS_HOME", "SESSIONMAIL_HOME", "SESSIONS_DIR")}
        env.update(HOME=self.home, VOICEMODE_BASE_DIR=os.path.join(self.tmp, "vm"),
                   SESSIONMAIL_HOME=self.tmp)
        patches = [
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(server, "RIDE_CACHE", {}, create=True),
            # rung 1 off: an import of sessiontools raises ImportError
            mock.patch.dict(sys.modules, {"sessiontools": None, "sessiontools.settings": None}),
            mock.patch.object(server.subprocess, "run", side_effect=self._run),
            mock.patch.object(server, "_SUBS", (0.0, (frozenset(), frozenset()))),   # no cache across tests
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.listed = []
        self.calls = []
        self.subs_text = ""          # `message subscription list heard`: nobody subscribed
        # rung 2 off: an older sessiontools has no `path` verb
        self.path_answer = subprocess.CompletedProcess(
            ["session", "path", "sessions"], 2, "\nUsage: session <verb> [args...]\n",
            "session: unknown verb 'path'\n")

    def _run(self, argv, **kw):
        """The faked subprocess.run: `session list --json` and `session path`."""
        self.calls.append(list(argv))
        if list(argv[:2]) == ["session", "list"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.listed), "")
        if list(argv[:3]) == ["message", "subscription", "list"]:
            return subprocess.CompletedProcess(argv, 0, self.subs_text, "")
        if list(argv[:2]) == ["session", "path"]:
            if isinstance(self.path_answer, BaseException):
                raise self.path_answer
            return self.path_answer
        raise AssertionError(f"unexpected subprocess: {argv}")

    def path_calls(self):
        return [c for c in self.calls if c[:2] == ["session", "path"]]

    def sessiontools(self, root):
        """Plant a fake sessiontools whose session_dir(sid) is <root>/<sid>."""
        from pathlib import Path
        pkg = types.ModuleType("sessiontools")
        mod = types.ModuleType("sessiontools.settings")
        mod.session_dir = lambda sid, env=None: Path(root) / sid
        # a real sessiontools answers agent folders too (ears.home.tools_agent, rung a)
        mod.agent_home = lambda agent, env=None: Path(root).parent / "agent" / "home" / agent
        mod.agent_config = lambda agent, env=None: Path(root).parent / "agent" / "config" / f"{agent}.md"
        pkg.settings = mod
        sys.modules["sessiontools"], sys.modules["sessiontools.settings"] = pkg, mod
        return mod

    def put(self, d, name, text):
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "w") as f:
            f.write(text)
        return os.path.join(d, name)

    def _mk(self, name):
        p = os.path.join(self.tmp, name)
        os.makedirs(p)
        return p

    def seat(self, agent):
        os.makedirs(os.path.join(self.homes, agent))
        with open(os.path.join(self.homes, agent, "heard.state"), "w") as f:
            json.dump({"offsets": {}}, f)

    def live(self, agent, sid, status="idle", short=None):
        self.listed.append({"agent": agent, "session_id": sid, "status": status, "short": short})

    def subscribe(self, short):
        """The session SHORT subscribed to the heard box: it can hear the room."""
        self.subs_text += f"    {short}   session subscription, listening\n"

    def cursor(self, sid, seq, file=None):
        file = file or f"heard_{date.today():%Y-%m-%d}.jsonl"
        with open(os.path.join(self.cursors, sid), "w") as f:
            json.dump({"file": file, "seq": seq}, f)

    def done(self, sid, name, text):
        """A ride/done mail in the LEGACY layout: <legacy>/<sid>/ride/done (no mail/)."""
        return self.put(os.path.join(self.sessions, sid, "ride", "done"), name, text)

    def session_done(self, root, sid, name, text):
        """A ride/done mail in the session dir: <root>/<sid>/mail/ride/done."""
        return self.put(os.path.join(root, sid, "mail", "ride", "done"), name, text)

    def poll(self):
        """One pass of poll_state(), the published STATE after it."""
        with mock.patch.object(server.time, "sleep", side_effect=_Stop):
            with self.assertRaises(_Stop):
                server.poll_state()
        return server.state_now()


class PollStateRide(Fixture):
    def test_genie_seat_ticks_from_ride_done(self):
        """Ivy has no heard-cursor; her ride/done max Ears-Seq is her tick.
        This is the TALK-2 bug: on d38e44e ride["ivy"] is never set."""
        self.seat("ivy")
        self.live("ivy", IVY)
        self.done(IVY, "1.M1P1.host1", heard_mail(187600))
        self.done(IVY, "2.M2P2.host1", heard_mail(187686))
        self.done(IVY, "3.M3P3.host1", heard_mail(187650))
        self.done(IVY, "4.M4P4.host1", heard_mail(None, body="agent to agent mail"))
        self.assertEqual(self.poll()["ride"].get("ivy"), 187686)

    def test_cursor_seat_not_regressed(self):
        """Vera keeps her cursor-driven tick when ride/done is lower or absent."""
        self.seat("vera")
        self.live("vera", VERA, "busy")
        self.cursor(VERA, 188100)
        self.assertEqual(self.poll()["ride"].get("vera"), 188100)
        self.done(VERA, "1.M1P1.host1", heard_mail(188050))
        self.assertEqual(self.poll()["ride"].get("vera"), 188100)

    def test_higher_source_wins(self):
        self.seat("vera")
        self.live("vera", VERA)
        self.cursor(VERA, 100)
        self.done(VERA, "1.M1P1.host1", heard_mail(250))
        self.assertEqual(self.poll()["ride"].get("vera"), 250)

    def test_stale_day_cursor_still_ignored_but_ride_done_counts(self):
        """The cursor is filtered to today's log (as before); ride/done is not,
        because seq is global-monotonic across days."""
        self.seat("ivy")
        self.live("ivy", IVY)
        self.cursor(IVY, 999999, file=f"heard_{ANCHOR:%Y-%m-%d}.jsonl")
        self.done(IVY, "1.M1P1.host1", heard_mail(42))
        self.assertEqual(self.poll()["ride"].get("ivy"), 42)

    def test_agent_without_heard_state_still_ticks(self):
        """EARS-242 do-003: ride and presence are no longer gated on a heard.state
        (it was `if a in seats`); a live agent with ride mail gets its tick. Presence is
        gated on HEARING instead: this one is subscribed to the heard box."""
        self.live("worker", "w-sid", "busy", short="0a0b0c0d")
        self.subscribe("0a0b0c0d")
        self.done("w-sid", "1.M1P1.host1", heard_mail(500))
        st = self.poll()
        self.assertEqual(st["ride"].get("worker"), 500)
        self.assertEqual(st["presence"].get("worker"), "thinking")
        self.assertNotIn("worker", st["receipts"])

    def test_a_live_seat_that_cannot_hear_gets_no_presence(self):
        """A user report: the chips showed the general contractor, a foreman, workers, "they
        aren't in this conversation". Live but not subscribed: no presence (its ride stays)."""
        self.live("foreman", "f-sid", "busy", short="0f0f0f0f")
        self.live("reviewer", "c-sid", "idle", short="0c0c0c0c")
        self.subscribe("0c0c0c0c")
        self.done("f-sid", "1.M1P1.host1", heard_mail(77))
        st = self.poll()
        self.assertEqual(st["presence"], {"reviewer": "listening"})
        self.assertEqual(st["ride"].get("foreman"), 77)

    def test_no_heard_mail_no_tick(self):
        self.seat("ivy")
        self.live("ivy", IVY)
        self.done(IVY, "1.M1P1.host1", heard_mail(None))
        self.assertNotIn("ivy", self.poll()["ride"])

    def test_max_across_sessions_of_one_agent(self):
        self.seat("ivy")
        self.live("ivy", IVY)
        self.live("ivy", "ivy-second")
        self.done(IVY, "1.M1P1.host1", heard_mail(10))
        self.done("ivy-second", "1.M1P1.host1", heard_mail(20))
        self.assertEqual(self.poll()["ride"].get("ivy"), 20)


class RideDoneSeq(Fixture):
    def test_headers_only(self):
        """An Ears-Seq line in the BODY is not a header and does not count."""
        self.done(IVY, "1.M1P1.host1", heard_mail(None, body="Ears-Seq: 999999"))
        self.done(IVY, "2.M2P2.host1", heard_mail(7))
        self.assertEqual(server.ride_done_seq(IVY), 7)

    def test_header_case_and_bad_value(self):
        self.done(IVY, "1.M1P1.host1", heard_mail(None, extra="ears-seq: 31\n"))
        self.done(IVY, "2.M2P2.host1", heard_mail(None, extra="Ears-Seq: not-a-number\n"))
        self.assertEqual(server.ride_done_seq(IVY), 31)

    def test_missing_dir_is_zero(self):
        self.assertEqual(server.ride_done_seq("no-such-session"), 0)
        self.assertEqual(server.ride_done_seq(""), 0)

    def test_path_escape_refused(self):
        self.done("x", "1.M1P1.host1", heard_mail(5))
        self.assertEqual(server.ride_done_seq("../sessions/x"), 0)
        self.assertEqual(server.ride_done_seq(".."), 0)

    def test_unreadable_entry_skipped(self):
        d = os.path.dirname(self.done(IVY, "1.M1P1.host1", heard_mail(9)))
        os.makedirs(os.path.join(d, "2.M2P2.host1"))     # open() fails: OSError
        self.assertEqual(server.ride_done_seq(IVY), 9)

    def test_parsed_once_then_cached(self):
        self.done(IVY, "1.M1P1.host1", heard_mail(3))
        self.done(IVY, "2.M2P2.host1", heard_mail(None))
        self.assertEqual(server.ride_done_seq(IVY), 3)
        with mock.patch.object(server, "ears_seq", wraps=server.ears_seq) as spy:
            self.assertEqual(server.ride_done_seq(IVY), 3)
            spy.assert_not_called()                    # nothing re-read, mail without seq included
            self.done(IVY, "3.M3P3.host1", heard_mail(8))
            self.assertEqual(server.ride_done_seq(IVY), 8)
            self.assertEqual(spy.call_count, 1)        # only the new file

    def test_removed_file_leaves_cache(self):
        p = self.done(IVY, "1.M1P1.host1", heard_mail(50))
        self.done(IVY, "2.M2P2.host1", heard_mail(40))
        self.assertEqual(server.ride_done_seq(IVY), 50)
        os.remove(p)
        self.assertEqual(server.ride_done_seq(IVY), 40)


class RideDoneChain(Fixture):
    """ride/done comes from the session dir by the supported resolver
    (sessiontools, then `session path sessions`), and the legacy compatibility
    root only as the fallback of last resort."""

    def setUp(self):
        super().setUp()
        self.root = self._mk("session-root/sessions")     # stands in for ~/.session/sessions

    def path_ok(self, root=None):
        self.path_answer = subprocess.CompletedProcess(
            ["session", "path", "sessions"], 0, (root or self.root) + "\n", "")

    # rung 1: sessiontools importable
    def test_rung1_sessiontools_session_dir(self):
        self.sessiontools(self.root)
        self.session_done(self.root, IVY, "1.M1P1.host1", heard_mail(71))
        self.assertEqual(server.ride_done_seq(IVY), 71)
        self.assertEqual(self.path_calls(), [])           # rung 2 never asked

    def test_rung1_beats_legacy(self):
        self.sessiontools(self.root)
        self.session_done(self.root, IVY, "1.M1P1.host1", heard_mail(5))
        self.done(IVY, "1.M1P1.host1", heard_mail(900))
        self.assertEqual(server.ride_done_seq(IVY), 5)

    def test_rung1_no_ride_dir_falls_to_legacy(self):
        self.sessiontools(self.root)
        os.makedirs(os.path.join(self.root, IVY, "mail"))     # a session dir, but no ride/done
        self.done(IVY, "1.M1P1.host1", heard_mail(12))
        self.assertEqual(server.ride_done_seq(IVY), 12)

    def test_rung1_resolver_raising_falls_to_legacy(self):
        mod = self.sessiontools(self.root)

        def refuse(sid, env=None):
            raise RuntimeError("settings refused")
        mod.session_dir = refuse
        self.done(IVY, "1.M1P1.host1", heard_mail(13))
        self.assertEqual(server.ride_done_seq(IVY), 13)

    # rung 2: `session path sessions`
    def test_rung2_session_path(self):
        self.path_ok()
        self.session_done(self.root, IVY, "1.M1P1.host1", heard_mail(88))
        self.assertEqual(server.ride_done_seq(IVY), 88)
        self.assertEqual(self.path_calls(), [["session", "path", "sessions"]])

    def test_rung2_beats_legacy(self):
        self.path_ok()
        self.session_done(self.root, IVY, "1.M1P1.host1", heard_mail(6))
        self.done(IVY, "1.M1P1.host1", heard_mail(600))
        self.assertEqual(server.ride_done_seq(IVY), 6)

    def test_rung2_root_resolved_once_per_poll(self):
        """poll_state runs every 2 s over every live session: one `session path`
        a pass, never one a session."""
        self.path_ok()
        for i, sid in enumerate((IVY, VERA, "third-sid")):
            self.live(f"agent{i}", sid)
            self.session_done(self.root, sid, "1.M1P1.host1", heard_mail(100 + i))
        ride = self.poll()["ride"]
        self.assertEqual(ride, {"agent0": 100, "agent1": 101, "agent2": 102})
        # one `session path sessions` and one `session path agent` (the seat homes) a pass
        self.assertEqual(sorted(c[2] for c in self.path_calls()), ["agent", "sessions"])

    def test_rung2_not_run_when_sessiontools_importable(self):
        self.sessiontools(self.root)
        self.live("ivy", IVY)
        self.session_done(self.root, IVY, "1.M1P1.host1", heard_mail(3))
        self.assertEqual(self.poll()["ride"].get("ivy"), 3)
        self.assertEqual(self.path_calls(), [])

    def test_rung2_failures_fall_through_to_legacy(self):
        """No `path` verb (an older sessiontools), no `session` at all, a hang, an empty
        or relative or multi-line answer: each is just "next rung"."""
        self.session_done(self.root, IVY, "1.M1P1.host1", heard_mail(999))   # unreachable
        self.done(IVY, "1.M1P1.host1", heard_mail(21))
        unknown_verb = self.path_answer
        answers = [
            unknown_verb,
            FileNotFoundError(2, "No such file or directory", "session"),
            subprocess.TimeoutExpired(["session", "path", "sessions"], 10),
            subprocess.CompletedProcess(["session"], 0, "", ""),
            subprocess.CompletedProcess(["session"], 0, "relative/sessions\n", ""),
            subprocess.CompletedProcess(["session"], 0, self.root + "\n" + self.root + "\n", ""),
            subprocess.CompletedProcess(["session"], 1, self.root + "\n", "boom"),
        ]
        for a in answers:
            with self.subTest(answer=repr(a)[:60]):
                self.path_answer = a
                self.assertIsNone(server.cli_sessions_root())
                self.assertEqual(server.ride_done_seq(IVY), 21)

    def test_unknown_verb_is_quiet(self):
        """Falling through logs nothing: a 2 s poll must not spam the log."""
        self.done(IVY, "1.M1P1.host1", heard_mail(4))
        with mock.patch("sys.stderr") as err, mock.patch("sys.stdout") as out:
            self.assertEqual(server.ride_done_seq(IVY), 4)
        err.write.assert_not_called()
        out.write.assert_not_called()

    # rung 3: only the legacy compatibility link
    def test_only_legacy_link_sessionmail_home(self):
        """Rungs 1-2 unavailable; $SESSIONMAIL_HOME/sessions/<sid>/ride/done."""
        self.done(IVY, "1.M1P1.host1", heard_mail(33))
        decoy = os.path.join(self.home, ".sessionmail", "sessions", IVY, "ride", "done")
        self.put(decoy, "1.M1P1.host1", heard_mail(999))
        self.assertEqual(server.legacy_sessions(), self.sessions)
        self.assertEqual(server.ride_done_seq(IVY), 33)

    def test_no_legacy_rung_without_sessionmail_home(self):
        """SESSIONMAIL_HOME unset: no old-layout rung at all (SK-254): a decoy in the old
        mail folder under HOME is never read, and no mail folder is named."""
        del os.environ["SESSIONMAIL_HOME"]
        decoy = os.path.join(self.home, ".sessionmail", "sessions", IVY, "ride", "done")
        self.put(decoy, "1.M1P1.host1", heard_mail(44))
        self.assertIsNone(server.legacy_sessions())
        self.assertEqual(server.ride_done_seq(IVY), 0)

    def test_tools_old_session_folder_only_when_tools_cannot_answer(self):
        """Rungs 1-2 unavailable: tools' old session folder, read only (rung c, EARS-512).
        When tools answers, the old folder is not read, even with a higher seq."""
        old = os.path.join(self.home, ".session", "sessions")
        self.session_done(old, IVY, "1.M1P1.host1", heard_mail(55))
        self.assertEqual(server.ride_done_seq(IVY), 55)
        self.sessiontools(self.root)
        self.session_done(self.root, IVY, "1.M1P1.host1", heard_mail(7))
        self.assertEqual(server.ride_done_seq(IVY), 7)

    def test_legacy_layout_has_no_mail_layer(self):
        self.put(os.path.join(self.sessions, IVY, "mail", "ride", "done"), "1.M1P1.host1", heard_mail(9))
        self.assertEqual(server.ride_done_seq(IVY), 0)

    # neither
    def test_neither_is_zero(self):
        self.assertEqual(server.ride_done_seq(IVY), 0)
        self.path_ok()
        self.assertEqual(server.ride_done_seq(IVY), 0)
        self.sessiontools(self.root)
        self.assertEqual(server.ride_done_seq(IVY), 0)

    def test_neither_in_poll_no_tick(self):
        self.live("ivy", IVY)
        self.assertNotIn("ivy", self.poll()["ride"])

    def test_sid_guards_kept_on_every_rung(self):
        self.sessiontools(self.root)
        self.session_done(self.root, "x", "1.M1P1.host1", heard_mail(5))
        self.assertEqual(server.ride_done_seq("../sessions/x"), 0)
        self.assertEqual(server.ride_done_seq(".hidden"), 0)


class NoMailLiteral(unittest.TestCase):
    def test_no_sessionmail_folder_literal_in_src_talk(self):
        """The old mail layout is reached ONLY through $SESSIONMAIL_HOME (SK-254): no
        ~/.sessionmail anywhere in src/talk (tests/test_seat_home.py scans all voice code)."""
        src = os.path.dirname(server.__file__)
        hits = []
        for dirpath, _, files in os.walk(src):
            for n in files:
                if n.endswith((".py", ".html", ".js", ".css", ".json", ".md", ".txt")):
                    with open(os.path.join(dirpath, n), encoding="utf-8", errors="replace") as f:
                        hits += [(n, i) for i, line in enumerate(f, 1) if ".sessionmail" in line]
        self.assertEqual(hits, [])
        self.assertEqual((server.LEGACY_ENV, server.LEGACY_SESSIONS), ("SESSIONMAIL_HOME", "sessions"))


if __name__ == "__main__":
    unittest.main()
