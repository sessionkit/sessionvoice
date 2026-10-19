"""Tests for talk.server's state poll: the two-tick "rode to" mark (TALK-2).

Run: make test ARGS=tests/test_talk_server.py

Stdlib only. HOME and VOICEMODE_BASE_DIR point at a temp dir (EARS_HOME is
cleared), SESSIONS is patched, and `session list --json` is faked, so nothing
here touches ~/.sessionmail, ~/.voicemode or a live session.
"""
import json, os, shutil, subprocess, tempfile, unittest
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
        self.sessions = self._mk("sessions")
        self.logdir = self._mk("vm/logs/conversations")      # ears.heard.log_dir()
        env = {k: v for k, v in os.environ.items() if k != "EARS_HOME"}
        env.update(HOME=self.home, VOICEMODE_BASE_DIR=os.path.join(self.tmp, "vm"))
        patches = [
            mock.patch.dict(os.environ, env, clear=True),
            # create=True: d38e44e has no SESSIONS, and the test must FAIL
            # there on the assertion, not error on the patch
            mock.patch.object(server, "SESSIONS", self.sessions, create=True),
            mock.patch.object(server, "RIDE_CACHE", {}, create=True),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.listed = []

    def _mk(self, name):
        p = os.path.join(self.tmp, name)
        os.makedirs(p)
        return p

    def seat(self, agent):
        os.makedirs(os.path.join(self.homes, agent))
        with open(os.path.join(self.homes, agent, "heard.state"), "w") as f:
            json.dump({"offsets": {}}, f)

    def live(self, agent, sid, status="idle"):
        self.listed.append({"agent": agent, "session_id": sid, "status": status})

    def cursor(self, sid, seq, file=None):
        file = file or f"heard_{date.today():%Y-%m-%d}.jsonl"
        with open(os.path.join(self.cursors, sid), "w") as f:
            json.dump({"file": file, "seq": seq}, f)

    def done(self, sid, name, text):
        d = os.path.join(self.sessions, sid, "ride", "done")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "w") as f:
            f.write(text)
        return os.path.join(d, name)

    def poll(self):
        """One pass of poll_state(), the published STATE after it."""
        fake = subprocess.CompletedProcess(["session"], 0, json.dumps(self.listed), "")
        with mock.patch.object(server.subprocess, "run", return_value=fake), \
                mock.patch.object(server.time, "sleep", side_effect=_Stop):
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
        (it was `if a in seats`); a live agent with ride mail gets its tick."""
        self.live("worker", "w-sid", "busy")
        self.done("w-sid", "1.M1P1.host1", heard_mail(500))
        st = self.poll()
        self.assertEqual(st["ride"].get("worker"), 500)
        self.assertEqual(st["presence"].get("worker"), "thinking")
        self.assertNotIn("worker", st["receipts"])

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


if __name__ == "__main__":
    unittest.main()
