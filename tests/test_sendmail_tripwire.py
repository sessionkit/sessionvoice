"""No code names postfix's binary (EARS-381; PHONE-72's tripwire, by name).

Mail goes by sessionmail's ``message send``, which carries it through postfix or
through the mail drop on a Mac with no postfix (``SESSIONMAIL_SENDMAIL=drop``).
A sender that names ``/usr/sbin/sendmail`` itself can never reach the drop, and
there is no fallback to it, ever (design C).

The tripwire reads the code under SCOPE, the code that runs (Python, shell, Swift
and the keeper's extensionless scripts), and not the docs: a doc may say what
the old path was (rca-001 N8). It covers the mouth's notices, ears-mail's turns
and the services check, which asks postfix's own ``postconf`` instead.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCOPE = ("src", "keeper")
CODE = {".py", ".sh", ".swift", ".in", ""}
SKIP = {"__pycache__"}
PATH = "/usr/sbin/" + "sendmail"   # split, so this file is not its own hit


def _files(root: Path = ROOT) -> list:
    out = []
    for scope in SCOPE:
        for f in sorted((root / scope).rglob("*")):
            rel = f.relative_to(root).parts
            if f.is_file() and f.suffix in CODE and not SKIP & set(rel) \
                    and not any(p.endswith(".egg-info") for p in rel):
                out.append(f)
    return out


def _hits(root: Path = ROOT) -> list:
    found = []
    for f in _files(root):
        try:
            text = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue                   # a binary: no code of ours
        for n, line in enumerate(text.splitlines(), 1):
            if PATH in line:
                found.append(f"{f.relative_to(root)}:{n}: {line.strip()}")
    return found


def test_no_code_names_postfixs_binary():
    assert _hits() == []


def test_the_tripwire_reads_real_files():
    """A tripwire that globbed nothing would always pass: each sender is in scope."""
    names = {f.relative_to(ROOT).as_posix() for f in _files()}
    assert {"src/mouth/notice.py", "keeper/ears-mail.py", "src/ears/services.py",
            "keeper/ears-keeper.sh"} <= names


def test_the_tripwire_trips(tmp_path):
    """The scan finds the path when it is there (the scan, not only the glob), and
    leaves a doc and a build artifact alone."""
    (tmp_path / "keeper").mkdir()
    (tmp_path / "keeper" / "x.py").write_text(f'CMD = ["{PATH}", "-t"]\n')
    (tmp_path / "keeper" / "NOTES.md").write_text(f"it was {PATH}\n")
    (tmp_path / "src" / "x.egg-info").mkdir(parents=True)
    (tmp_path / "src" / "x.egg-info" / "PKG-INFO").write_text(f"{PATH}\n")
    assert _hits(tmp_path) == [f'keeper/x.py:1: CMD = ["{PATH}", "-t"]']
