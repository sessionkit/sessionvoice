"""Who is in this conversation (a user request: talk should just show who's in this
conversation). Presence, and so a chip before anyone speaks, only for a live session that
can hear the room: subscribed to the heard box, the list Settings' consent page shows as
"Who can hear". Read as Settings' apply.hears() reads it.
"""
import subprocess
from unittest import mock

from talk import server

LIST = """home/heard         project box
├── reviewer       seat subscription, listening
└── +airpods       claimed: only these get +airpods, not the subscribers above
    ├── a39fff74   session subscription, listening
    └── dbabcea6   session subscription, listening
-- 0 hear it; 2 more on 1 claimed tag
"""


def test_the_list_gives_session_shorts_and_seats_tags_included():
    shorts, seats = server.parse_subscribers(LIST)
    assert shorts == {"a39fff74", "dbabcea6"} and seats == {"reviewer"}


def test_an_empty_or_missing_list_is_nobody():
    assert server.parse_subscribers("") == (frozenset(), frozenset())
    assert server.parse_subscribers(None) == (frozenset(), frozenset())


def test_hears_by_short_by_session_id_or_by_seat():
    subs = server.parse_subscribers(LIST)
    assert server.hears({"short": "a39fff74"}, subs)
    assert server.hears({"session_id": "dbabcea6-149c-e874-5d83-576c8ccf69c6"}, subs)
    assert server.hears({"seat": "reviewer", "session_id": "fdf8b742-0000"}, subs)


def test_the_fleets_other_seats_do_not_hear():
    subs = server.parse_subscribers(LIST)
    for s in ({"agent": "foreman", "short": "7978f1b4", "seat": "store+t-1.foreman.7978f1b4"},
              {"agent": "general_contractor", "short": "681426d9", "seat": "store+gc.681426d9"},
              {"agent": "worker", "session_id": "769d5146-aaaa"}, {}):
        assert not server.hears(s, subs)


def test_no_message_command_means_nobody_and_never_raises():
    with mock.patch.object(server, "_SUBS", (0.0, (frozenset(), frozenset()))), \
            mock.patch.object(server.subprocess, "run", side_effect=FileNotFoundError("message")):
        assert server.heard_subscribers() == (frozenset(), frozenset())


def test_the_list_is_kept_thirty_seconds():
    ok = subprocess.CompletedProcess(["message"], 0, LIST, "")
    with mock.patch.object(server, "_SUBS", (0.0, (frozenset(), frozenset()))), \
            mock.patch.object(server.subprocess, "run", return_value=ok) as run:
        first = server.heard_subscribers(); second = server.heard_subscribers()
    assert first == second and first[1] == {"reviewer"} and run.call_count == 1
