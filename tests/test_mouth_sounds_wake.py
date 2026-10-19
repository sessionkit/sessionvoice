"""mouth-sounds: the loop WAITS on the places it watches, it does not poll (EARS-287).

It used to sleep 50 ms between looks (5% of a core with ~400 cursor files). Now it blocks on
a kqueue over the maildir's new/, the FIRED file, the cursors folder and the
STOP file's folder. These tests run the keeper as launchd does (`python keeper/mouth-sounds.py`),
DRY, with a STUB sounddevice (no audio device opened, so they run without PortAudio) over temp
paths, and set MOUTH_SOUNDS_BACKSTOP=30 so the 250 ms backstop cannot be what wakes it: a sound
that is logged within ~1 s was woken by the kernel event. A queued sound is never lost, even when
its folder vanishes and comes back, or did not exist yet.
"""
from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests._anchor import ANCHOR

pytest.importorskip("numpy")

SCRIPT = Path(__file__).resolve().parents[1] / "keeper" / "mouth-sounds.py"
HEARD = f"heard_{ANCHOR:%Y-%m-%d}.jsonl"
WAKE_S = 1.5   # a kernel wake lands in ~ms; the backstop is 30 s in these runs
needs_kqueue = pytest.mark.skipif(not hasattr(select, "kqueue"), reason="kqueue is macOS/BSD")


class Keeper:
    def __init__(self, tmp: Path, backstop: str = "30", wait: str = "", mk_new: bool = True, before=None, env=None):
        self.t = tmp
        stub = tmp / "stub"
        stub.mkdir()
        (stub / "sounddevice.py").write_text("class PortAudioError(Exception):\n    pass\n")
        for d in ("run", "cursors", "heard") + (("md/new", "md/cur") if mk_new else ()):
            (tmp / d).mkdir(parents=True)
        (tmp / "settings.json").write_text(json.dumps({"sounds": {
            n: {"on": True, "notes": [[1568, 5]], "volume": 0.1} for n in ("drop", "front", "canned", "bud", "scoop")}}))
        (tmp / "run/canned").write_text("")
        self.log, self.stop = tmp / "sounds.jsonl", tmp / "STOP"
        if before:
            before(tmp)
        env = dict(os.environ, PYTHONPATH=str(stub), MOUTH_SOUNDS_DRY="1", MOUTH_SOUNDS_BACKSTOP=backstop,
                   MOUTH_MAILDIR=str(tmp / "md"), EARS_RUN=str(tmp / "run"),
                   MOUTH_SOUNDS_FIRED=str(tmp / "run/mid-turn.fired"), MOUTH_SOUNDS_CANNED=str(tmp / "run/canned"),
                   MOUTH_SOUNDS_SETTINGS=str(tmp / "settings.json"), MOUTH_SOUNDS_LOG=str(self.log),
                   MOUTH_SOUNDS_STOP=str(self.stop), MOUTH_SOUNDS_CURSORS=str(tmp / "cursors"),
                   MOUTH_SOUNDS_HEARD_DIR=str(tmp / "heard"), MOUTH_SOUNDS_WAIT=wait, **(env or {}))
        self.p = subprocess.Popen([sys.executable, str(SCRIPT)], env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True)
        banner = self.p.stdout.readline()
        assert banner.startswith("mouth-sounds: watching"), banner
        time.sleep(0.3)   # the first wait opens the watches and looks once more

    def canned(self, *utts):
        with open(self.t / "run/canned", "a") as f:
            f.write("".join(u + "\n" for u in utts))

    def lines(self, sound=None):
        if not self.log.exists():
            return []
        rows = [json.loads(x) for x in self.log.read_text().splitlines()]
        return [r for r in rows if sound is None or r["sound"] == sound]

    def wait_for(self, n, sound=None, within=WAKE_S):
        end = time.time() + within
        while time.time() < end:
            if len(self.lines(sound)) >= n:
                break
            time.sleep(0.01)
        return self.lines(sound)

    def close(self):
        self.stop.touch()
        try:
            self.p.wait(timeout=35)
        except subprocess.TimeoutExpired:
            self.p.kill()
        return self.p.returncode


@pytest.fixture
def keeper(tmp_path):
    k = Keeper(tmp_path)
    yield k
    k.close()


def _line(new: Path, utt: str) -> None:
    """One mouth line delivered as maildir does it: written aside, then renamed into new/."""
    tmp = new.parent / f"{utt}.tmp"
    tmp.write_text(f"X-Mouth-Utt: {utt}\n\nhi\n")
    os.rename(tmp, new / f"1.{utt}.host")


@needs_kqueue
def test_maildir_line_wakes_the_loop(keeper):
    keeper.canned("uttb")
    t0 = time.time()
    _line(keeper.t / "md/new", "uttb")
    rows = keeper.wait_for(1)
    assert [(r["sound"], r["utt"], r["via"]) for r in rows] == [("canned", "uttb", "maildir")]
    assert time.time() - t0 < WAKE_S


@needs_kqueue
def test_the_old_voice_mode_queue_is_not_watched(tmp_path):
    """EARS-294 T3 (CS-09): the voice_mode.mouth queue folder, ~/.voicemode/mouth/queue, is
    no longer a place a line lands. A file there plays nothing; a maildir line still does."""
    old = tmp_path / "home/.voicemode/mouth/queue"
    k = Keeper(tmp_path, env={"HOME": str(tmp_path / "home")}, before=lambda t: old.mkdir(parents=True))
    try:
        k.canned("oldq1", "new01")
        (old / "0-5-oldq1.json").write_text("{}")
        _line(tmp_path / "md/new", "new01")
        assert [r["utt"] for r in k.wait_for(1)] == ["new01"]
        time.sleep(0.5)
        assert [r["utt"] for r in k.lines()] == ["new01"]
    finally:
        k.close()
    code = SCRIPT.read_text().split('"""', 2)[2]   # past the module docstring, which tells the history
    assert "MOUTH_QUEUE" not in code and "mouth/queue" not in code


@needs_kqueue
def test_fired_touch_wakes_the_loop_and_a_replace_does_too(keeper):
    fired = keeper.t / "run/mid-turn.fired"
    fired.write_text("")                          # the file did not exist: its folder woke us
    assert len(keeper.wait_for(1, "bud")) == 1
    time.sleep(0.05)
    os.utime(fired)                               # a touch changes the FILE, not its folder
    assert len(keeper.wait_for(2, "bud")) == 2
    time.sleep(0.05)
    new = keeper.t / "run/mid-turn.fired.new"
    new.write_text("")
    os.rename(new, fired)                         # replaced by rename: a new inode
    assert len(keeper.wait_for(3, "bud")) == 3
    time.sleep(0.05)
    os.utime(fired)                               # and the NEW file is the one watched now
    assert len(keeper.wait_for(4, "bud")) == 4


@needs_kqueue
def test_stop_file_ends_the_loop_at_once(keeper):
    t0 = time.time()
    keeper.stop.touch()
    assert keeper.p.wait(timeout=WAKE_S + 2) == 0
    assert time.time() - t0 < WAKE_S
    assert "stop file seen" in keeper.p.stdout.read()


@needs_kqueue
def test_a_vanished_and_recreated_maildir_is_rewatched(keeper):
    new = keeper.t / "md/new"
    keeper.canned("u1", "u2", "u3")
    _line(new, "u1")
    assert len(keeper.wait_for(1)) == 1
    (new / "1.u1.host").unlink()
    new.rmdir()
    time.sleep(0.3)
    new.mkdir()                                   # a new folder, a new inode
    _line(new, "u2")
    assert [r["utt"] for r in keeper.wait_for(2)] == ["u1", "u2"]
    _line(new, "u3")                              # and the new folder is watched, not just rescanned
    assert [r["utt"] for r in keeper.wait_for(3)] == ["u1", "u2", "u3"]


@needs_kqueue
def test_a_maildir_that_does_not_exist_yet(tmp_path):
    k = Keeper(tmp_path, mk_new=False)
    try:
        k.canned("late1", "late2")
        (tmp_path / "md/new").mkdir(parents=True)   # md/ and md/new/ appear together
        (tmp_path / "md/new/1.late1.host").write_text("X-Mouth-Utt: late1\n\nhi\n")
        assert [r["utt"] for r in k.wait_for(1)] == ["late1"]
        (tmp_path / "md/new/2.late2.host").write_text("X-Mouth-Utt: late2\n\nhi\n")
        assert [r["utt"] for r in k.wait_for(2)] == ["late1", "late2"]
    finally:
        k.close()


def test_poll_fallback_loses_nothing(tmp_path):
    """No kqueue (MOUTH_SOUNDS_WAIT=poll): the 250 ms backstop alone, every line still plays."""
    k = Keeper(tmp_path, backstop="0.25", wait="poll")
    try:
        k.canned("p1", "p2", "p3")
        for u in ("p1", "p2", "p3"):
            _line(tmp_path / "md/new", u)
        rows = k.wait_for(3, within=3)
        assert sorted(r["utt"] for r in rows) == ["p1", "p2", "p3"]
        assert k.close() == 0
    finally:
        k.close()


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores mode 000")
@pytest.mark.parametrize("locked", ["md", "md/new"])
def test_a_permission_flap_chimes_nothing_that_was_already_there(tmp_path, locked):
    """The maildir's new/ cannot be read for a moment (its folder, or new/ itself, mode 000).
    The loop keeps what it last saw: once it reads again, the three lines already there stay
    quiet and only a line that is truly new chimes. Before, 3.14's exists() (and glob on every
    version, for new/ itself) read 'cannot read' as 'empty', and all three chimed at once."""
    def seed(t: Path) -> None:
        for u in ("old1", "old2", "old3"):
            _line(t / "md/new", u)
    k = Keeper(tmp_path, backstop="0.05", wait="poll", before=seed)
    try:
        k.canned("old1", "old2", "old3", "fresh")
        target = tmp_path / locked
        os.chmod(target, 0)
        try:
            time.sleep(0.5)       # ~10 looks at a new/ it cannot read
        finally:
            os.chmod(target, 0o755)
        time.sleep(0.5)           # ~10 looks once it reads again
        _line(tmp_path / "md/new", "fresh")
        assert [r["utt"] for r in k.wait_for(1, within=3)] == ["fresh"]
        time.sleep(0.3)
        assert [r["utt"] for r in k.lines()] == ["fresh"]
    finally:
        k.close()


def test_listing_reads_the_folder_never_a_path_predicate_or_glob():
    """listing() tells 'absent' from 'cannot read' by os.listdir's error: Path.glob reads an
    unreadable folder as empty, and from 3.14 Path.exists reads an unreachable one as absent."""
    import ast
    tree = ast.parse(SCRIPT.read_text())
    [fn] = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "listing"]
    called = {n.func.attr for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "listdir" in called
    assert not called & {"exists", "is_dir", "is_file", "glob", "iterdir"}, called


def _put(cdir: Path, sess: str, cur: dict, replace: bool) -> None:
    if replace:                                   # as save_cursor: tmp, then rename over
        tmp = cdir / f"{sess}.tmp{os.getpid()}"
        tmp.write_text(json.dumps(cur) + "\n")
        os.replace(tmp, cdir / sess)
    else:                                         # in place, same size: only the mtime moves
        (cdir / sess).write_text(json.dumps(cur) + "\n")


@pytest.mark.parametrize("replace", [True, False])
def test_cursor_move_still_scoops_with_the_stat_cache(tmp_path, replace):
    """The cursors are re-read only when (mtime, size, inode) moved: a rename-over and an
    in-place rewrite of the same size both still scoop, at the 250 ms backstop's pace."""
    rows = [{"seq": 1, "kind": "said"}, {"seq": 2, "kind": "partial", "source": "mic"}]
    first = len((json.dumps(rows[0]) + "\n").encode())

    def seed(t: Path) -> None:
        (t / "heard" / HEARD).write_bytes(b"".join((json.dumps(r) + "\n").encode() for r in rows))
        _put(t / "cursors", "sess-aaaa1111", {"file": HEARD, "offset": first, "seq": 1}, True)
        _put(t / "cursors", "sess-bbbb2222", {"file": HEARD, "offset": first, "seq": 1}, True)   # never moves

    k = Keeper(tmp_path, backstop="0.25", before=seed)
    try:
        end = (tmp_path / "heard" / HEARD).stat().st_size
        time.sleep(0.6)                           # several looks: the cache holds both rows
        assert k.lines("scoop") == []
        _put(tmp_path / "cursors", "sess-aaaa1111", {"file": HEARD, "offset": end, "seq": 2}, replace)
        got = k.wait_for(1, "scoop", within=2)
        assert [(r["session"], r["rows"], r["seq"]) for r in got] == [("sess-aaa", 1, [1, 2])]
        time.sleep(0.6)
        assert len(k.lines("scoop")) == 1         # the unmoved one never fires, the moved one once
    finally:
        k.close()
