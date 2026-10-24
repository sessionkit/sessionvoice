"""The wheel, built, carries what an install from it needs (EARS-160).

tests/test_keeper_in_wheel.py and tests/test_settings_in_wheel.py read pyproject's
package-data and importlib.resources; this one BUILDS the wheel and reads the zip. It builds
offline: setuptools (the dev group, the [build-system] floor) is called in a child
interpreter with no build isolation, so nothing is fetched. The build runs in a copy of the
sources under tmp_path, so the checkout gets no build/ or egg-info.

The wheel must hold the three packages, the keeper's files, the speaker side-car, the
speech lock, the settings schemas and talk's page (each the same bytes as the checkout's),
no swift/ (an Apple helper builds from a checkout only), and a METADATA whose base
Requires-Dist is numpy, pyyaml, httpx and sounddevice, with a smart-turn extra.
"""
from __future__ import annotations

import email.parser
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
KEEPER = ("ears-keeper.sh", "call-ears", "input-watch.swift", "ears-mail.py",
          "ears-sounds.py", "mouth-sounds.py", "held_stream.py")   # the sounds agents: EARS-429
SCHEMAS = ("ears.schema.json", "ears-sounds.schema.json", "mouth.schema.json")
BASE = {"numpy", "pyyaml", "httpx", "sounddevice"}
SMART_TURN = {"onnxruntime", "transformers"}

BUILD = ("import sys\n"
         "from setuptools import build_meta\n"
         "print(build_meta.build_wheel(sys.argv[1]))\n")


def _copy_sources(dst: Path) -> None:
    """What the build reads: pyproject, the readme and licence, src/ (its symlinks kept), and
    every top-level directory a symlink under src/ points into (keeper/, settings/, ...)."""
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        shutil.copy2(REPO / name, dst / name)
    shutil.copytree(SRC, dst / "src", symlinks=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
    linked = set()
    for root, dirs, files in os.walk(SRC):
        for n in dirs + files:
            p = Path(root) / n
            if p.is_symlink():
                linked.add(p.resolve().relative_to(REPO).parts[0])
    assert linked, "no package-data symlinks under src/: the copy would miss what they ship"
    for top in sorted(linked):
        shutil.copytree(REPO / top, dst / top, symlinks=True,
                        ignore=shutil.ignore_patterns("__pycache__"))


@pytest.fixture(scope="module")
def wheel(tmp_path_factory) -> Path:
    pytest.importorskip("setuptools", minversion="77")
    tree, out = tmp_path_factory.mktemp("tree"), tmp_path_factory.mktemp("dist")
    _copy_sources(tree)
    p = subprocess.run([sys.executable, "-c", BUILD, str(out)], cwd=tree,
                       capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, f"the wheel did not build:\n{p.stdout[-2000:]}\n{p.stderr[-4000:]}"
    name = p.stdout.strip().splitlines()[-1]
    assert re.fullmatch(r"sessionvoice-[^-]+-py3-none-any\.whl", name), name
    return out / name


@pytest.fixture(scope="module")
def names(wheel) -> set[str]:
    with zipfile.ZipFile(wheel) as z:
        return set(z.namelist())


def _files(d: Path) -> list[str]:
    return sorted(p.name for p in d.iterdir() if p.is_file() and p.name != "__pycache__")


def test_the_wheel_holds_ears_mouth_and_talk_and_nothing_else(names):
    tops = {n.split("/")[0] for n in names}
    dist_info = {t for t in tops if t.endswith(".dist-info")}
    assert len(dist_info) == 1
    assert tops - dist_info == {"ears", "mouth", "talk"}      # no swift/, keeper/, settings/ at the top


def test_every_module_in_src_is_in_the_wheel(names):
    src = {p.relative_to(SRC).as_posix() for p in SRC.rglob("*.py")
           if "__pycache__" not in p.parts and ".egg-info" not in str(p)}
    assert src - names == set()


@pytest.mark.parametrize("pkg, expected", [
    ("ears/_keeper", sorted(KEEPER)),
    ("ears/_speakers", _files(SRC / "ears" / "_speakers")),
    ("ears/_speech", _files(SRC / "ears" / "_speech")),
    ("ears/_settings", sorted(SCHEMAS)),
])
def test_the_package_data_ships(names, pkg, expected):
    assert expected, f"{pkg} is empty in the checkout"
    got = sorted(n[len(pkg) + 1:] for n in names if n.startswith(pkg + "/") and "/" not in n[len(pkg) + 1:])
    assert got == expected


def test_talk_serves_its_page_and_manifest(names):
    assert {"talk/index.html", "talk/manifest.json"} <= names


def test_shipped_data_is_the_checkouts_bytes(wheel, names):
    # the symlinks under src/ears/_*/ are copied as files: the same bytes as what they point at
    data = [n for n in names if re.match(r"ears/_(keeper|speakers|speech|settings)/", n)]
    data += ["talk/index.html", "talk/manifest.json"]
    with zipfile.ZipFile(wheel) as z:
        for n in data:
            assert z.read(n) == (SRC / n).resolve().read_bytes(), n


def _metadata(wheel):
    with zipfile.ZipFile(wheel) as z:
        meta = next(n for n in z.namelist() if n.endswith(".dist-info/METADATA"))
        return email.parser.Parser().parsestr(z.read(meta).decode())


def _requirement(line: str) -> tuple[str, str]:
    """('name', 'marker or empty') from one Requires-Dist line, the name normalised."""
    spec, _, marker = line.partition(";")
    name = re.match(r"[A-Za-z0-9._-]+", spec.strip()).group(0)
    return re.sub(r"[-_.]+", "-", name).lower(), marker.strip()


def test_the_base_requires_are_numpy_pyyaml_httpx_and_sounddevice(wheel):
    reqs = [_requirement(r) for r in _metadata(wheel).get_all("Requires-Dist")]
    assert {n for n, m in reqs if not m} == BASE


def test_the_smart_turn_extra_is_onnxruntime_and_transformers(wheel):
    meta = _metadata(wheel)
    assert "smart-turn" in meta.get_all("Provides-Extra")
    reqs = [_requirement(r) for r in meta.get_all("Requires-Dist")]
    extra = {n for n, m in reqs if re.search(r"""extra\s*==\s*["']smart[-_]turn["']""", m)}
    assert extra == SMART_TURN
