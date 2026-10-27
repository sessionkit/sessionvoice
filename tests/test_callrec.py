"""ears.callrec: the call recording's name, spelt once (EARS-298). caller-call since sessionphone
PHONE-127, and the only name read: a call dir from before the rename is not read (EARS-490).
OTHER stands for any other recording in a call dir, an old one included: it is never read."""
import ast
from pathlib import Path

import pytest

from ears import callrec

OTHER = "other-call"     # a stand-in for any recording not named caller-call


def test_the_one_stem_is_caller_call():
    """The contract is ONE stem: no fallback name, no list of them."""
    assert callrec.STEM == "caller-call"
    assert not hasattr(callrec, "STEMS")


def test_name_is_caller_call_with_the_extension():
    assert callrec.name("wav") == "caller-call.wav"
    assert callrec.name("json") == "caller-call.json"


@pytest.mark.parametrize("have,want", [
    ([], None),
    (["caller-call"], "caller-call"),
    ([OTHER], None),                               # only another recording: not read
    ([OTHER, "caller-call"], "caller-call"),       # both: caller-call, never the other
])
@pytest.mark.parametrize("ext", ["wav", "json"])
def test_call_file_takes_caller_call_and_nothing_else(tmp_path, have, want, ext):
    for stem in have:
        (tmp_path / f"{stem}.{ext}").write_bytes(b"x")
    (tmp_path / f"caller-call.{'json' if ext == 'wav' else 'wav'}").write_bytes(b"x")  # the other ext: never it
    got = callrec.call_file(tmp_path, ext)
    assert got == (None if want is None else tmp_path / f"{want}.{ext}")
    assert callrec.call_file(str(tmp_path), ext) == got                              # a str dir too


def test_a_dir_of_unrelated_wavs_and_jsons_is_not_read(tmp_path):
    """A call dir holding recordings under any other name is not a call recording."""
    for n in ("recording", "call", "caller", "caller-call-1", "x-caller-call"):
        (tmp_path / f"{n}.wav").write_bytes(b"x"); (tmp_path / f"{n}.json").write_bytes(b"x")
    assert callrec.call_file(tmp_path, "wav") is None
    assert callrec.call_file(tmp_path, "json") is None


def test_it_imports_nothing_but_the_stdlib():
    """The side-car's venv and the bot's venv load it by path: no ears, no third party."""
    tree = ast.parse(Path(callrec.__file__).read_text())
    mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not any(isinstance(n, ast.ImportFrom) and n.level for n in ast.walk(tree))
    assert mods <= {"pathlib"}, mods
