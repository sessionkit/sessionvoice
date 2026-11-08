"""The mail fence's refusing program (EARS-506): ``tests/mail_fence.py`` puts a bin dir
first on every child's PATH whose ``notmuch``, ``message``, ``memory``, ``pager`` and
``sendmail`` each run this, as

    python -I refuse_mail.py LOG PROGRAM [ARG...]

It indexes nothing, mails nothing and writes nothing but one JSON line to LOG naming
the program, its arguments and the test that was running (``$PYTEST_CURRENT_TEST``),
says so on stderr, and exits 75 (EX_TEMPFAIL), like ``refuse_sendmail.py``. The
pytest session reads LOG: a refusal during a test fails that test, and one no test
was running for fails the run. Stdlib only."""
import json
import os
import select
import sys
import time

EXIT = 75


def _drain(seconds: float = 1.0) -> None:
    """Read what a sender is writing, so it never sees a broken pipe, but never wait
    longer than SECONDS on a stdin that is not closing (a tty, an idle pipe)."""
    try:
        fd = sys.stdin.fileno()
        if os.isatty(fd):
            return
        end = time.monotonic() + seconds
        while (left := end - time.monotonic()) > 0:
            ready, _, _ = select.select([fd], [], [], left)
            if not ready or not os.read(fd, 65536):
                return
    except (OSError, ValueError):
        pass


def main(argv: list) -> int:
    log, prog, args = argv[1], argv[2], argv[3:]
    rec = {"kind": "refused", "where": "child", "prog": os.path.basename(prog), "argv": args[:12],
           "test": os.environ.get("PYTEST_CURRENT_TEST"), "pid": os.getpid(), "ppid": os.getppid(),
           "home": os.environ.get("HOME"), "cwd": os.getcwd()}
    line = (json.dumps(rec) + "\n").encode()
    try:
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    except OSError as exc:
        print(f"mail fence: could not log the refusal to {log}: {exc}", file=sys.stderr)
    print(f"mail fence (EARS-506): refused {rec['prog']} {' '.join(args[:4])} in a test run "
          f"({rec['test'] or 'no test running'}): fake it (tests/mail_fence.py)", file=sys.stderr)
    _drain()
    return EXIT


if __name__ == "__main__":
    sys.exit(main(sys.argv))
