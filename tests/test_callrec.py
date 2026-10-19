"""ears.callrec: the call recording's name, spelt once (EARS-298). caller-call since sessionphone
PHONE-127 renamed mike-call; old call dirs keep mike-call, so it is the fallback."""
import ast
from pathlib import Path

import pytest

from ears import callrec


def test_names_put_caller_call_first_then_mike_call():
    assert callrec.names("wav") == ["caller-call.wav", "mike-call.wav"]
    assert callrec.names("json") == ["caller-call.json", "mike-call.json"]
    assert callrec.looked_for("wav") == "caller-call.wav or mike-call.wav"


@pytest.mark.parametrize("have,want", [
    ([], None),
    (["caller-call"], "caller-call"),
    (["mike-call"], "mike-call"),
    (["mike-call", "caller-call"], "caller-call"),
])
@pytest.mark.parametrize("ext", ["wav", "json"])
def test_call_file_takes_caller_call_else_mike_call(tmp_path, have, want, ext):
    for stem in have:
        (tmp_path / f"{stem}.{ext}").write_bytes(b"x")
    (tmp_path / f"mike-call.{'json' if ext == 'wav' else 'wav'}").write_bytes(b"x")  # the other ext: never it
    got = callrec.call_file(tmp_path, ext)
    assert got == (None if want is None else tmp_path / f"{want}.{ext}")
    assert callrec.call_file(str(tmp_path), ext) == got                              # a str dir too


def test_it_imports_nothing_but_the_stdlib():
    """The side-car's venv and the bot's venv load it by path: no ears, no third party."""
    tree = ast.parse(Path(callrec.__file__).read_text())
    mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not any(isinstance(n, ast.ImportFrom) and n.level for n in ast.walk(tree))
    assert mods <= {"pathlib"}, mods
