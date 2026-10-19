"""The text the shipped code emits is neutral: no tracker id, no he/him/his, no dated stamp.

Emitted text is what a user or an agent sees printed, logged or spoken: a notice, a
warning, an error, a status line. In the code it is a string constant that is not a
docstring, f-string parts included. A tracker id ("ABC-123") belongs in a comment or the
CHANGELOG, never in that text. The person the text is about is "the user" when it
speaks to an agent, and "you" when it speaks to the user, never he, him or his. A
weekday beside a date ("Sun 2030-06-09" in form) is a stamp, and leaves too.

The shipped code is every Python file under the repo (a ``.py`` file, or a file whose
first line is a python shebang), outside tests/, docs/internal/ and tool directories.
A symlink is skipped: it is a second name for a file that is read under its own.
Docstrings and comments are not scanned, so ids may stay there.

The settings schemas (``settings/*.schema.json``) are emitted text too: Settings draws
its screen from their titles and descriptions. Their other values (defaults, enums,
the stop-word lists) are data and are not scanned.

Two kinds of string are allowed through:

- ``ALLOWED_IDS``: words shaped like an id that are not one (an encoding name).
- A string assigned to a name in ``WORD_LIST_NAMES``: a stop-word list, whose "he" and
  "his" are words to match in a heard turn, not words about anyone.

A hit fails the test, naming each path, line and what matched. The fix is to reword the
text, or to move the id into a comment beside it.
"""
import ast
import json
import os
import re
from pathlib import Path

from tests._anchor import ANCHOR

ROOT = Path(__file__).resolve().parent.parent

#: Directories never scanned: tests, internal notes, and tooling that is not shipped.
SKIP_DIRS = {"tests", ".git", ".venv", "venv", "node_modules", "__pycache__", "build",
             "dist", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
SKIP_PATHS = {Path("docs/internal")}

#: A tracker id: a capitalised project code, a dash, a number.
ID_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,7}-[0-9]+\b")
#: Id-shaped words that are not tracker ids.
ALLOWED_IDS = {"UTF-8", "UTF-16", "UTF-32"}
#: A bare he, him or his, in any case.
HE_RE = re.compile(r"\b(he|him|his)\b", re.IGNORECASE)
#: A weekday beside a date: a stamp of when something was ruled or done.
DATED_RE = re.compile(r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun) [0-9]{4}-[0-9]{2}-[0-9]{2}\b")
#: Names whose string value is a word list to match against, not text to emit.
WORD_LIST_NAMES = {"STOP_WORDS"}


def _is_python(path: Path) -> bool:
    if path.suffix == ".py":
        return True
    if path.suffix:
        return False
    try:
        with path.open("rb") as f:
            first = f.readline(200)
    except OSError:
        return False
    return first.startswith(b"#!") and b"python" in first


def shipped_python(root: Path = ROOT):
    """Each shipped Python file under ``root``, as a path relative to it, sorted."""
    out = []
    for top, dirs, files in os.walk(root):
        here = Path(top).relative_to(root)
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.endswith(".egg-info")
                   and here / d not in SKIP_PATHS and not (Path(top) / d).is_symlink()]
        for name in files:
            path = Path(top) / name
            if not path.is_symlink() and path.is_file() and _is_python(path):
                out.append(here / name)
    return sorted(out)


def _docstrings(tree: ast.AST) -> set:
    nodes = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, nodes) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                out.add(id(first.value))
    return out


def _word_lists(tree: ast.AST) -> set:
    """Every string constant inside an assignment to a WORD_LIST_NAMES name."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id in WORD_LIST_NAMES for t in targets):
                out.update(id(n) for n in ast.walk(node.value) if isinstance(n, ast.Constant))
    return out


def text_hits(text: str):
    """Each (kind, matched) in one piece of emitted ``text``."""
    hits = [("tracker id", m.group(0)) for m in ID_RE.finditer(text)
            if m.group(0) not in ALLOWED_IDS]
    hits += [("he/him/his", m.group(0)) for m in HE_RE.finditer(text)]
    hits += [("dated stamp", m.group(0)) for m in DATED_RE.finditer(text)]
    return hits


def emitted_hits(source: str):
    """Each (line, kind, matched) in ``source``'s non-docstring string constants."""
    tree = ast.parse(source)
    skip = _docstrings(tree) | _word_lists(tree)
    hits = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)) or id(node) in skip:
            continue
        hits += [(node.lineno, kind, matched) for kind, matched in text_hits(node.value)]
    return sorted(hits)


#: The keys of a settings schema whose text Settings draws on its screen. A value
#: under any other key (a default, an enum, a stop-word list) is data, not text.
SCHEMA_TEXT_KEYS = {"title", "description"}


def schema_hits(schema, where: str = ""):
    """Each (JSON pointer, kind, matched) in a schema's titles and descriptions."""
    hits = []
    if isinstance(schema, dict):
        for key, value in schema.items():
            here = f"{where}/{key}"
            if key in SCHEMA_TEXT_KEYS and isinstance(value, str):
                hits += [(here, kind, matched) for kind, matched in text_hits(value)]
            else:
                hits += schema_hits(value, here)
    elif isinstance(schema, list):
        for i, value in enumerate(schema):
            hits += schema_hits(value, f"{where}/{i}")
    return hits


def scan(root: Path = ROOT) -> dict:
    """{relative path: hits} for every shipped Python file under ``root`` with a hit."""
    found = {}
    for rel in shipped_python(root):
        hits = emitted_hits((root / rel).read_text(encoding="utf-8", errors="replace"))
        if hits:
            found[str(rel)] = hits
    return found


def test_the_shipped_code_emits_no_tracker_id_he_or_dated_stamp():
    found = scan()
    assert not found, "emitted text that is not neutral:\n" + "\n".join(
        f"  {path}:{line}: {kind} {matched!r}"
        for path, hits in found.items() for line, kind, matched in hits)


def test_the_settings_screens_are_neutral_too():
    """Settings draws its screen from each shipped schema's titles and descriptions
    (`ears services install` copies them beside the settings), so they are emitted text."""
    schemas = sorted((ROOT / "settings").glob("*.schema.json"))
    assert schemas
    found = {str(p.relative_to(ROOT)): hits for p in schemas
             if (hits := schema_hits(json.loads(p.read_text(encoding="utf-8"))))}
    assert not found, "settings text that is not neutral:\n" + "\n".join(
        f"  {path}:{where}: {kind} {matched!r}"
        for path, hits in found.items() for where, kind, matched in hits)


def test_a_schema_description_is_scanned_and_its_word_lists_are_not():
    schema = {"title": "T", "properties": {
        "a": {"description": "What writes his words", "default": ["he", "him", "his"]},
        "b": {"title": f"Since {ID}", "enum": [ID]}}}
    assert schema_hits(schema) == [("/properties/a/description", "he/him/his", "his"),
                                   ("/properties/b/title", "tracker id", ID)]


def test_the_scan_reads_the_shipped_code():
    files = {str(p) for p in shipped_python()}
    assert {"src/mouth/notice.py", "src/mouth/hold.py", "keeper/ears-mail.py",
            "shadow/score"} <= files                       # a shebang script counts
    assert "src/ears/_keeper/ears-mail.py" not in files     # a symlink: read once, by its target
    assert not any(f.startswith("tests/") for f in files)


# -- the matcher on its own samples. The samples are built from their parts, so this
#    file holds no id of its own beyond the ones the patterns are made to find.

ID = "ABC" + "-" + "123"


def test_an_id_in_a_message_is_found():
    src = f'raise ValueError("one recipient per send ({ID}), got x")\n'
    assert emitted_hits(src) == [(1, "tracker id", ID)]


def test_an_id_in_an_f_string_part_is_found():
    src = f'def f(n):\n    return f"ignored ({ID}): {{n}}"\n'
    assert emitted_hits(src) == [(2, "tracker id", ID)]


def test_ids_in_docstrings_and_comments_are_not_scanned():
    src = (f'"""Module notes ({ID})."""\n'
           f'# a comment: {ID}\n'
           f'def f():\n    """Since {ID}."""\n    return "neutral"\n'
           f'class C:\n    """Per {ID}."""\n')
    assert emitted_hits(src) == []


def test_an_encoding_name_is_not_an_id():
    assert emitted_hits('MSG = "is not UTF-8 text"\n') == []


def test_he_him_and_his_are_found_in_any_case():
    src = 'A = "when he speaks"\nB = "His words cut it"\nC = "CUT HIM OFF"\n'
    assert [(n, m) for n, _, m in emitted_hits(src)] == [(1, "he"), (2, "His"), (3, "HIM")]


def test_words_that_hold_he_are_not_he():
    assert emitted_hits('A = "the user heard them here; she has this"\n') == []


def test_a_stop_word_list_is_not_text():
    src = 'STOP_WORDS = frozenset("""\na he him his the\n""".split())\n'
    assert emitted_hits(src) == []
    assert emitted_hits('OTHER = frozenset("""\na he\n""".split())\n') != []


def test_a_weekday_beside_a_date_is_found():
    stamp = f"{ANCHOR:%a} {ANCHOR:%Y-%m-%d}"
    assert emitted_hits(f'W = "retired ({stamp})"\n') == [(1, "dated stamp", stamp)]
    assert emitted_hits(f'W = "{ANCHOR:%Y-%m-%d}"\n') == []     # a bare ISO date may be data
