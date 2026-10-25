"""The capture's watchdog: STOP and SIGTERM are honoured even when the frame loop is stuck.

Every heartbeat, every STOP-file check and every effect of SIGTERM happens in the frame
loop, on the main thread. A wait there that never returns is a wedge: no beat,
STOP and SIGTERM ignored, and only SIGKILL ends it. The waits we know of are bounded,
but a wait we do not know of, or one inside C (a PortAudio stop), would wedge
the capture the same way. This thread makes any of them harmless.

The frame loop calls :meth:`Watchdog.progress` on every frame. The watchdog thread, off
the main thread, polls the capture's own stop check (the stop Event SIGTERM sets, and the
STOP file). It TRIPS when:

- ``wedge``: no frame for ``wedge_s`` (:data:`WEDGE_S`);
- ``stop-ignored``: a stop was asked for and the capture had not finished ``stop_grace_s``
  (:data:`STOP_GRACE_S`) later;
- ``exit``: only when armed with ``exit_grace_s`` (the CLI): the capture has finished, yet
  the process is still alive ``exit_grace_s`` later (a thread joined at interpreter exit).

A trip, once: a line on stderr, every thread's stack on stderr (``faulthandler``), a
``capture-wedged`` heard row (best effort, and NEVER blocking: skipped when ``heard.lock``
is held, and given at most ``row_wait_s``), SIGKILL to the streaming child the capture owns
(a stopped nemotron child would not exit on its own), a flush of stdout and stderr
(at most ``FLUSH_WAIT_S``), then ``os._exit(3)``.

A trip owns the exit code. Its SIGKILL can free the very call the frame loop was stuck in
(a blocking write to the child's stdin), and the capture then finishes in the moment before
``os._exit(3)``. So a capture that ends while a trip is under way does not go on to exit 0:
:meth:`Watchdog.run_ended` waits for the trip's exit (at most :meth:`trip_wait_s`), and the
CLI returns 3 whenever its watchdog tripped. ``run_ended`` and the start of a
trip take one lock, so each sees the other whole: once the capture has ended, only the
``exit`` trip is left.

``capture()`` runs without one unless given one: a library caller (and the test suite) is
never ``os._exit``-ed. The CLI gives one only when it owns its process (``main()`` called
with no argv, as ``python -m ears.listen capture`` and the ``ears`` script do).

The clock is ``time.monotonic``, which on macOS does not advance while the machine sleeps,
so a laptop that sleeps does not trip it.
"""

from __future__ import annotations

import errno
import fcntl
import faulthandler
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

# No frame for this long is a wedge. Above the longest wait the frame loop may make on
# its own bounded waits at one end of a turn (a chunk that queued behind a stuck
# call is waited for twice at STT_WAIT_S, then the re-decode at REDECODE_WAIT_S, about
# 40-50 s), with room to spare. Below the keeper's beat-stale DEAF report (120 s, with a
# 15 s heartbeat), so a wedged capture ends itself, and the keeper starts a fresh one,
# before the keeper would report it, and far below the keeper's 300 s kill.
WEDGE_S = 90.0
# A stop not honoured in this long is a wedge. A healthy capture stops in well under a
# second. The keeper TERMs 15 s after it touches STOP (EARS_STOP_WAIT), so the capture
# ends itself, with its stack and its heard row, before the keeper has to.
STOP_GRACE_S = 10.0
POLL_S = 0.25
ROW_WAIT_S = 1.0
FLUSH_WAIT_S = 1.0
EXIT_CODE = 3
ROW_EVENT = "capture-wedged"
LOCK_NAME = "heard.lock"


def _say(msg: str) -> None:
    try:
        os.write(2, f"ears capture watchdog: {msg}\n".encode())
    except OSError:
        pass


def _dump_stacks() -> None:
    try:
        faulthandler.dump_traceback(all_threads=True)
    except (AttributeError, OSError, ValueError, RuntimeError):  # no usable sys.stderr: fd 2
        try:
            faulthandler.dump_traceback(file=2, all_threads=True)
        except (OSError, ValueError, RuntimeError):
            pass


def _flush_std(wait_s: float) -> None:
    """Flush stdout and stderr before ``os._exit``, waiting at most ``wait_s``.

    The keeper sends stdout to a file, which is block-buffered, so an exit without a
    flush drops the result line a finished capture has printed (the ``exit`` trip). The
    flush runs on its own thread: a stdout that blocks (a pipe nobody reads) must not
    hold up the exit."""
    done = threading.Event()

    def flush() -> None:
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except Exception:  # noqa: BLE001 - a closed or broken stream must not stop the exit
                pass
        done.set()

    threading.Thread(target=flush, name="listen-watchdog-flush", daemon=True).start()
    done.wait(wait_s)


def heard_lock_busy(directory: Optional[Path]) -> bool:
    """True when another writer holds ``heard.lock`` in ``directory``: a probe that never
    waits (``LOCK_NB``), and lets go at once. Any other error is False: let the write try."""
    if directory is None:
        return False
    try:
        fd = os.open(str(Path(directory) / LOCK_NAME), os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        return e.errno in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


class Watchdog:
    """See the module docstring. ``exit``, ``kill``, ``dump`` and ``clock`` are seams for
    tests; ``exit`` is ``os._exit`` in a capture's own process."""

    def __init__(
        self,
        *,
        wedge_s: Optional[float] = None,
        stop_grace_s: Optional[float] = None,
        exit_grace_s: Optional[float] = None,
        poll_s: Optional[float] = None,
        row_wait_s: Optional[float] = None,
        exit: Callable[[int], None] = os._exit,
        kill: Callable[[int, int], None] = os.kill,
        dump: Callable[[], None] = _dump_stacks,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        # read at construction, not at import: a test may set the module's values
        self.wedge_s = WEDGE_S if wedge_s is None else wedge_s
        self.stop_grace_s = STOP_GRACE_S if stop_grace_s is None else stop_grace_s
        self.exit_grace_s = exit_grace_s
        self.poll_s = POLL_S if poll_s is None else poll_s
        self.row_wait_s = ROW_WAIT_S if row_wait_s is None else row_wait_s
        self._exit, self._kill, self._dump, self._clock = exit, kill, dump, clock
        self._last = clock()
        self._stop_seen: Optional[float] = None
        self._ended: Optional[float] = None
        self._halt = threading.Event()
        self._lock = threading.Lock()  # run_ended vs the start of a trip: one or the other first
        self._trip_done = threading.Event()  # set only if the trip's ``exit`` returns (a test's)
        self._thread: Optional[threading.Thread] = None
        self.tripped: Optional[str] = None
        self._stop_requested: Callable[[], bool] = lambda: False
        self._write_row: Optional[Callable[[dict], object]] = None
        self._children: Callable[[], Iterable[int]] = lambda: ()
        self._lock_dir: Optional[Path] = None
        self._stream_s: Callable[[], float] = lambda: 0.0

    # -- the capture's side ----------------------------------------------------------------

    def start(
        self,
        *,
        stop_requested: Callable[[], bool],
        write_row: Optional[Callable[[dict], object]] = None,
        children: Callable[[], Iterable[int]] = lambda: (),
        lock_dir: Optional[Path] = None,
        stream_s: Callable[[], float] = lambda: 0.0,
    ) -> None:
        """Start watching. ``stop_requested`` must be a pure read (it is called off the main
        thread); ``write_row(fields)`` writes the heard row; ``children()`` are the pids to
        SIGKILL; ``lock_dir`` is where ``heard.lock`` is, for the never-blocking probe."""
        self._stop_requested, self._write_row, self._children = stop_requested, write_row, children
        self._lock_dir, self._stream_s = lock_dir, stream_s
        self._last = self._clock()
        self._thread = threading.Thread(target=self._watch, name="listen-watchdog", daemon=True)
        self._thread.start()

    def progress(self) -> None:
        """The frame loop is alive: called once a frame."""
        self._last = self._clock()

    def run_ended(self) -> None:
        """The capture has finished. With no ``exit_grace_s`` the watchdog stops now (a
        library caller goes on). With one (the CLI), the process has that long to be gone.

        If a trip is already under way, the capture finished because the trip's SIGKILL
        freed it: wait for the trip's exit (3) instead, at most :meth:`trip_wait_s`, so the
        process never ends 0 after a trip. In a capture's own process ``os._exit(3)`` ends
        this wait, and the process with it; with an injected ``exit`` (a test), it returns
        once the trip's ``exit`` has returned."""
        with self._lock:
            tripped = self.tripped
            if tripped is None:
                if self.exit_grace_s is None:
                    self._halt.set()  # under the lock: no trip can start after this
                else:
                    self._ended = self._clock()  # from now on only the ``exit`` trip is left
        if tripped is not None:
            self._trip_done.wait(self.trip_wait_s())
        elif self.exit_grace_s is None:
            self.stop()

    def trip_wait_s(self) -> float:
        """The longest :meth:`run_ended` waits for a trip under way to exit: the trip's own
        bounded steps (the row, then the flush) and a second for the stack dump. Past it the
        caller goes on, and the CLI still returns 3 (``tripped`` is set)."""
        return self.row_wait_s + FLUSH_WAIT_S + 1.0

    def stop(self) -> None:
        self._halt.set()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=max(1.0, 4 * self.poll_s))

    # -- the thread ------------------------------------------------------------------------

    def _asked_to_stop(self) -> bool:
        try:
            return bool(self._stop_requested())
        except Exception:  # noqa: BLE001 - a stop check that fails asks for nothing
            return False

    def _verdict(self, asked: bool) -> Optional[tuple[str, float]]:
        now = self._clock()
        if self._ended is not None:
            waited = now - self._ended
            return ("exit", waited) if waited >= self.exit_grace_s else None
        if self._stop_seen is None and asked:
            self._stop_seen = now
        if self._stop_seen is not None and now - self._stop_seen >= self.stop_grace_s:
            return "stop-ignored", now - self._stop_seen
        if now - self._last >= self.wedge_s:
            return "wedge", now - self._last
        return None

    def _watch(self) -> None:
        while not self._halt.wait(self.poll_s):
            asked = self._stop_seen is None and self._asked_to_stop()  # a file stat: not under the lock
            with self._lock:  # a trip starts whole, before or after run_ended, never during
                if self._halt.is_set():
                    return
                verdict = self._verdict(asked)
                if verdict is not None:
                    self.tripped = verdict[0]
            if verdict is not None:
                self._trip(*verdict)
                return

    def _trip(self, why: str, waited: float) -> None:
        stalled = self._clock() - self._last
        _say(f"{why} ({waited:.1f}s; no frame for {stalled:.1f}s); every thread's stack follows, "
             f"then any streaming child is killed and the capture exits {EXIT_CODE}")
        try:
            self._dump()
        except Exception:  # noqa: BLE001 - nothing may stop the exit
            pass
        self._row(why, waited, stalled)
        for pid in self._child_pids():
            try:
                self._kill(pid, signal.SIGKILL)
                _say(f"SIGKILL streaming child pid {pid}")
            except (ProcessLookupError, PermissionError):
                pass
            except Exception:  # noqa: BLE001
                pass
        _flush_std(FLUSH_WAIT_S)
        try:
            self._exit(EXIT_CODE)
        finally:  # reached only when ``exit`` returns (a test's): os._exit ends the wait itself
            self._trip_done.set()

    def _child_pids(self) -> list[int]:
        try:
            return [int(p) for p in self._children() if p]
        except Exception:  # noqa: BLE001
            return []

    def _row(self, why: str, waited: float, stalled: float) -> None:
        """The ``capture-wedged`` row: skipped if heard.lock is held, and never waited on for
        more than ``row_wait_s``: the write runs on its own thread, and ``os._exit`` ends it."""
        if self._write_row is None:
            return
        if heard_lock_busy(self._lock_dir):
            _say(f"{ROW_EVENT} row skipped: {LOCK_NAME} is held by another writer")
            return
        try:
            stream_s = round(float(self._stream_s()), 3)
        except Exception:  # noqa: BLE001
            stream_s = None
        fields = {"why": why, "waited_s": round(waited, 3), "stalled_s": round(stalled, 3),
                  "stream_s": stream_s, "pid": os.getpid(), "exit": EXIT_CODE}
        done = threading.Event()

        def write() -> None:
            try:
                self._write_row(fields)
            except Exception as exc:  # noqa: BLE001
                _say(f"{ROW_EVENT} row not written: {type(exc).__name__}: {exc}")
            finally:
                done.set()

        threading.Thread(target=write, name="listen-watchdog-row", daemon=True).start()
        if not done.wait(self.row_wait_s):
            _say(f"{ROW_EVENT} row not written in {self.row_wait_s:.1f}s; exiting without it")
