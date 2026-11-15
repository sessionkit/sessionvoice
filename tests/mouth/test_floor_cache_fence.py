"""The pin for conftest's ``cold_floor_cache``: no mouth test inherits a floor.

``hold._look_floor`` caches the newest floor it scanned in the module-level
``hold._caches`` when ``directory`` is None. A test that heard the user speaking left
that ``speaking`` partial behind, and the next test's player, inside the cache's 0.1 s,
held its first room-held line and prefetched it.

The two tests below run in file order. The first leaves a ``speaking`` floor in every
set of ears' cache, as a leaking test would, and does not clean up. The second checks
at its start that every entry is cold again: only the conftest fixture can have done
that. It is not a race: an entry left behind keeps its ``v`` however long the gap, so
without the fixture the second test fails every time. If the first did not run just
before it (a reordered, filtered or split run), the second skips rather than fail.
"""
from __future__ import annotations

import json
import time
from datetime import datetime

import pytest

from ears import heard
from mouth import hold

#: Every set of ears the first test warmed, and whether it ran (and finished) last.
_warmed: dict = {"ears": (), "done": False}

_SETS = (hold.MIC, hold.sources_for("call:1"))


def test_a_test_that_hears_the_user_leaves_a_speaking_floor_cached(tmp_path, monkeypatch):
    _warmed.update(ears=(), done=False)
    monkeypatch.setenv("VOICEMODE_BASE_DIR", str(tmp_path / "vm"))
    heard.log_dir().mkdir(parents=True, exist_ok=True)
    ts = datetime.fromtimestamp(time.time()).astimezone().isoformat(timespec="milliseconds")
    with open(heard.log_path(), "a") as f:
        for source in ("mic", "call"):
            f.write(json.dumps({"ts": ts, "seq": 1, "kind": "partial", "text": "talk",
                                "source": source}) + "\n")
    for ears in _SETS:
        assert hold._caches.get(ears, {}).get("v") is None, ("started warm", ears)
        assert hold.floor(sources=ears) == "speaking"          # directory=None: cached
        assert hold._caches[ears]["v"] is not None, ("not cached", ears)
    _warmed.update(ears=_SETS, done=True)


def test_the_next_test_starts_with_a_cold_floor_cache():
    if not _warmed["done"]:
        pytest.skip("the warming test did not run just before this one")
    _warmed["done"] = False
    left = {k: c for k, c in hold._caches.items() if c["v"] is not None or c["t"] != 0.0}
    assert not left, f"a floor was left in hold's cache by the test before: {left}"
    assert set(_warmed["ears"]) <= set(hold._caches)           # the same dict, emptied in place
