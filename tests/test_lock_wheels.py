"""EARS-328, EARS-358: every package uv.lock installs has a wheel for macOS arm64 on every Python we declare.

`uv lock --check` compares the lock with the metadata; it never asks whether a wheel
exists. onnxruntime 1.24.1-1.24.3 declare Requires-Python >=3.10 but ship no cp310
wheel and no sdist, so the lock pinned 1.24.3 for py<3.11 and `uv sync --python 3.10
--all-extras` could not install (EARS-328). At the top of the range, mlx 0.32.2 and
onnxruntime 1.30.0 ship no cp315 wheel, so an uncapped `>=3.10` claimed a Python 3.15
the extras cannot install on (EARS-358).

This test reads the lock (no network) and walks what `uv sync --all-extras --all-groups`
would install on CPython 3.X, macOS arm64, for every 3.X the declared requires-python
admits. A capped range (`>=3.10,<3.15`) is checked in full. An UNCAPPED range is checked
up to the newest CPython any wheel in the lock names (a cpXY tag), and never below 3.15
(UNCAPPED_FLOOR, the SK-92 rule): an uncapped range is always asked about the next
CPython, even before a wheel names it. The declared range is pyproject.toml's, and it
must equal the lock's, so the two cannot drift.

A package with no sdist must have a wheel whose tags this interpreter accepts:
cp3X-cp3X, cp3Y-abi3 (Y <= X), py3 / py3Y with abi none; platform any, or macosx
arm64 / universal2. Free-threaded builds (cp3Xt) are not the default interpreter.

SESSIONVOICE_LOCK points the check at another lock file (to prove it RED on an old one);
the pyproject.toml beside that lock is the one read.
"""
import os
import re
from pathlib import Path

import pytest
from packaging.markers import Marker
from packaging.specifiers import SpecifierSet

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    tomllib = pytest.importorskip("tomli")

ROOT = Path(__file__).resolve().parents[1]
PLATFORM = {"sys_platform": "darwin", "platform_machine": "arm64", "platform_system": "Darwin",
            "os_name": "posix", "implementation_name": "cpython",
            "platform_python_implementation": "CPython"}
LOCAL_SOURCES = ("editable", "virtual", "directory")
# An uncapped range is checked through at least CPython 3.15 (the module docstring).
UNCAPPED_FLOOR = 15


def lock_path() -> Path:
    return Path(os.environ.get("SESSIONVOICE_LOCK") or ROOT / "uv.lock")


def declared_python(lock: dict) -> str:
    """pyproject.toml's requires-python; it must say what the lock says."""
    pyproject = lock_path().parent / "pyproject.toml"
    declared = tomllib.loads(pyproject.read_text())["project"]["requires-python"]
    assert SpecifierSet(declared) == SpecifierSet(lock["requires-python"]), (
        f"{pyproject} requires-python {declared!r} but the lock says "
        f"{lock['requires-python']!r}: run `uv lock`")
    return declared


def environment(minor: int) -> dict:
    return {**PLATFORM, "python_version": f"3.{minor}", "python_full_version": f"3.{minor}.0",
            "extra": ""}


def applies(marker, env: dict) -> bool:
    return marker is None or Marker(marker).evaluate(env)


def wheel_tags(url: str):
    """(interpreters, abis, platforms) of a wheel file name; each may be a dotted set."""
    stem = url.rsplit("/", 1)[-1].removesuffix(".whl")
    interp, abi, plat = stem.split("-")[-3:]
    return interp.split("."), abi.split("."), plat.split(".")


def wheel_installs(url: str, minor: int) -> bool:
    """Would CPython 3.<minor> on macOS arm64 accept this wheel?"""
    interps, abis, plats = wheel_tags(url)
    if not any(p == "any" or (p.startswith("macosx_") and p.endswith(("_arm64", "_universal2")))
               for p in plats):
        return False
    for interp in interps:
        for abi in abis:
            if abi == f"cp3{minor}" and interp == f"cp3{minor}":
                return True
            m = re.fullmatch(r"cp3(\d+)", interp)
            if abi == "abi3" and m and int(m.group(1)) <= minor:
                return True
            m = re.fullmatch(r"py3(\d*)", interp)
            if abi == "none" and (interp == f"cp3{minor}" or (m and (not m.group(1) or int(m.group(1)) <= minor))):
                return True
    return False


def python_minors(lock: dict, requires: str = None) -> list:
    """Every 3.X requires (default: the lock's) admits; uncapped runs to UNCAPPED_FLOOR or past."""
    spec = SpecifierSet(requires or lock["requires-python"])

    def admits(minor: int) -> bool:
        return spec.contains(f"3.{minor}.0") or spec.contains(f"3.{minor}.999")

    if admits(999):  # uncapped: the next CPython is always asked about
        named = {int(m) for p in lock["package"] for w in p.get("wheels", [])
                 for m in re.findall(r"cp3(\d+)", w["url"].rsplit("/", 1)[-1])}
        top = max([UNCAPPED_FLOOR, *named])
    else:
        top = 99
    return [m for m in range(0, top + 1) if admits(m)]


def installed(lock: dict, env: dict) -> list:
    """The [[package]] entries `uv sync --all-extras --all-groups` installs in env."""
    packages = lock["package"]

    def find(dep):
        hits = [p for p in packages if p["name"] == dep["name"]
                and ("version" not in dep or p["version"] == dep["version"])]
        if len(hits) > 1:
            hits = [p for p in hits if any(applies(m, env) for m in p.get("resolution-markers", [None]))]
        assert len(hits) == 1, f"uv.lock: {dep} matches {len(hits)} packages in {env['python_version']}"
        return hits[0]

    seen, todo = {}, []
    for root in (p for p in packages if set(p["source"]) & set(LOCAL_SOURCES)):
        todo.append((root, None))
        for deps in [*root.get("optional-dependencies", {}).values(),
                     *root.get("dev-dependencies", {}).values()]:
            todo.extend((d, "dep") for d in deps)
    while todo:
        item, kind = todo.pop()
        if kind == "dep":
            if not applies(item.get("marker"), env):
                continue
            pkg, extras = find(item), item.get("extra", [])
        else:
            pkg, extras = item, []
        key = (pkg["name"], pkg["version"])
        new_extras = set(extras) - seen.get(key, set())
        if key in seen and not new_extras:
            continue
        first = key not in seen
        seen.setdefault(key, set()).update(extras)
        deps = list(pkg.get("dependencies", [])) if first else []
        for extra in new_extras:
            deps += pkg.get("optional-dependencies", {}).get(extra, [])
        todo.extend((d, "dep") for d in deps)
    by_key = {(p["name"], p["version"]): p for p in packages}
    return [by_key[k] for k in seen]


def missing_wheels(lock: dict, requires: str = None) -> list:
    """One line per (package, Python) the lock would install but has nothing for."""
    gaps = []
    for minor in python_minors(lock, requires):
        for pkg in installed(lock, environment(minor)):
            if set(pkg["source"]) & set(LOCAL_SOURCES) or "sdist" in pkg:
                continue
            if not any(wheel_installs(w["url"], minor) for w in pkg.get("wheels", [])):
                gaps.append(f"{pkg['name']} {pkg['version']}: no sdist and no wheel for "
                            f"CPython 3.{minor} on macOS arm64")
    return gaps


def test_every_locked_package_installs_on_every_declared_python():
    path = lock_path()
    data = tomllib.loads(path.read_text())
    requires = declared_python(data)
    gaps = missing_wheels(data, requires)
    assert not gaps, (f"requires-python {requires!r}: {path} cannot install everywhere it claims "
                      "(EARS-328, EARS-358):\n  " + "\n  ".join(gaps))


# --- the checker itself, on small hand-built locks -------------------------------------

def lock(*packages, requires=">=3.10,<3.13", anchor=True):
    """A lock whose root needs every package; anchor adds a cp310-cp312 package beside them."""
    root = {"name": "proj", "version": "0.1", "source": {"editable": "."},
            "optional-dependencies": {"all": [{"name": p["name"], "version": p["version"],
                                               **({"marker": p.pop("edge")} if "edge" in p else {})}
                                              for p in packages]}}
    newest = {"name": "anchor", "version": "1", "source": {"registry": "x"},
              "wheels": [{"url": "anchor-1-cp312-cp312-macosx_14_0_arm64.whl"},
                         {"url": "anchor-1-cp311-cp311-macosx_14_0_arm64.whl"},
                         {"url": "anchor-1-cp310-cp310-macosx_14_0_arm64.whl"}]}
    root["dependencies"] = [{"name": "anchor"}] if anchor else []
    return {"requires-python": requires, "package": [root, *([newest] if anchor else []), *packages]}


def binary(name, *wheels, **extra):
    return {"name": name, "version": "1", "source": {"registry": "x"},
            "wheels": [{"url": f"{name}-1-{w}.whl"} for w in wheels], **extra}


def test_onnxruntime_1_24_3_shape_is_caught():
    gaps = missing_wheels(lock(binary("ort", "cp311-cp311-macosx_14_0_arm64",
                                      "cp312-cp312-macosx_14_0_arm64")))
    assert gaps == ["ort 1: no sdist and no wheel for CPython 3.10 on macOS arm64"]


def test_an_sdist_excuses_a_missing_wheel():
    pkg = binary("ort", "cp311-cp311-macosx_14_0_arm64", "cp312-cp312-macosx_14_0_arm64")
    pkg["sdist"] = {"url": "ort-1.tar.gz"}
    assert missing_wheels(lock(pkg)) == []


@pytest.mark.parametrize("tag", ["cp310-abi3-macosx_11_0_arm64", "py3-none-any", "py2.py3-none-any",
                                 "py3-none-macosx_14_0_arm64", "cp310-abi3-macosx_10_9_universal2"])
def test_wheels_any_python_accepts(tag):
    assert missing_wheels(lock(binary("w", tag))) == []


@pytest.mark.parametrize("tag", ["cp311-abi3-macosx_11_0_arm64", "cp310-cp310-macosx_11_0_x86_64",
                                 "cp310-cp310-manylinux_2_28_aarch64", "py3-none-win_amd64"])
def test_wheels_cpython_3_10_on_arm64_refuses(tag):
    other = [f"cp3{m}-cp3{m}-macosx_14_0_arm64" for m in (11, 12)]
    assert missing_wheels(lock(binary("w", tag, *other))) == [
        "w 1: no sdist and no wheel for CPython 3.10 on macOS arm64"]


def test_free_threaded_build_is_not_the_default_interpreter():
    wheels = ["cp310-cp310t-macosx_14_0_arm64", "cp311-cp311-macosx_14_0_arm64",
              "cp312-cp312-macosx_14_0_arm64"]
    assert missing_wheels(lock(binary("w", *wheels)))[0].startswith("w 1: no sdist and no wheel for CPython 3.10")


def test_resolution_markers_pick_the_version_per_python():
    old = binary("ort", "cp310-cp310-macosx_13_0_arm64")
    old["resolution-markers"] = ["python_full_version < '3.11'"]
    new = binary("ort", "cp311-cp311-macosx_14_0_arm64", "cp312-cp312-macosx_14_0_arm64")
    new["version"] = "2"
    new["resolution-markers"] = ["python_full_version >= '3.11'"]
    data = lock(old, new)
    data["package"][0]["optional-dependencies"]["all"] = [
        {"name": "ort", "version": "1", "marker": "python_full_version < '3.11'"},
        {"name": "ort", "version": "2", "marker": "python_full_version >= '3.11'"}]
    assert missing_wheels(data) == []


def test_a_dependency_the_mac_never_installs_is_not_checked():
    win = binary("pywin", "cp310-cp310-win_amd64", edge="sys_platform == 'win32'")
    assert missing_wheels(lock(win)) == []


def test_a_capped_range_is_checked_in_full_and_no_further():
    assert python_minors(lock()) == [10, 11, 12]
    assert python_minors(lock(requires=">=3.11,<3.13")) == [11, 12]
    assert python_minors(lock(requires=">=3.10,<3.15")) == [10, 11, 12, 13, 14]


def test_an_uncapped_range_is_checked_through_3_15_even_with_no_cp315_wheel():
    # The anchor names cp312 at most; uncapped still asks about 3.13, 3.14 and 3.15.
    assert python_minors(lock(requires=">=3.10")) == [10, 11, 12, 13, 14, 15]
    assert python_minors(lock(requires=">=3.11")) == [11, 12, 13, 14, 15]


def test_an_uncapped_range_runs_to_the_newest_cpython_a_wheel_names():
    newer = binary("np", "cp317-cp317-macosx_14_0_arm64")
    newer["sdist"] = {"url": "np-1.tar.gz"}  # an sdist package still names the minor
    assert python_minors(lock(newer, requires=">=3.10")) == list(range(10, 18))


def test_the_requires_argument_overrides_the_locks_range():
    data = lock(requires=">=3.10")
    assert python_minors(data, ">=3.10,<3.13") == [10, 11, 12]


CP310_14 = [f"cp3{m}-cp3{m}-macosx_14_0_arm64" for m in range(10, 15)]


def test_mlx_0_32_2_shape_is_caught_uncapped_and_clean_capped():
    mlx = binary("mlx", *CP310_14)
    assert missing_wheels(lock(mlx, requires=">=3.10", anchor=False)) == [
        "mlx 1: no sdist and no wheel for CPython 3.15 on macOS arm64"]
    assert missing_wheels(lock(binary("mlx", *CP310_14), requires=">=3.10,<3.15",
                               anchor=False)) == []


@pytest.mark.parametrize("tag", ["cp310-abi3-macosx_11_0_arm64", "py3-none-any",
                                 "py2.py3-none-any", "py310-none-macosx_14_0_arm64"])
def test_abi3_and_py3_wheels_cover_3_15(tag):
    assert missing_wheels(lock(binary("w", tag), requires=">=3.10", anchor=False)) == []


def test_a_cp315_free_threaded_wheel_does_not_cover_3_15():
    w = binary("w", *CP310_14, "cp315-cp315t-macosx_14_0_arm64")
    assert missing_wheels(lock(w, requires=">=3.10", anchor=False)) == [
        "w 1: no sdist and no wheel for CPython 3.15 on macOS arm64"]


def test_the_newer_resolution_fork_is_asked_about_3_15():
    # master's onnxruntime shape: 1.23.2 below 3.11, 1.30.0 (cp311-cp314) from 3.11.
    old = binary("ort", "cp310-cp310-macosx_13_0_arm64")
    old["resolution-markers"] = ["python_full_version < '3.11'"]
    new = binary("ort", *CP310_14[1:])
    new["version"] = "2"
    new["resolution-markers"] = ["python_full_version >= '3.11'"]
    data = lock(old, new, requires=">=3.10", anchor=False)
    data["package"][0]["optional-dependencies"]["all"] = [
        {"name": "ort", "version": "1", "marker": "python_full_version < '3.11'"},
        {"name": "ort", "version": "2", "marker": "python_full_version >= '3.11'"}]
    assert missing_wheels(data) == ["ort 2: no sdist and no wheel for CPython 3.15 on macOS arm64"]
    data["requires-python"] = ">=3.10,<3.15"
    assert missing_wheels(data) == []


def test_pyproject_and_lock_must_declare_the_same_range(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.10,<3.15"\n')
    monkeypatch.setenv("SESSIONVOICE_LOCK", str(tmp_path / "uv.lock"))
    assert declared_python({"requires-python": ">=3.10, <3.15"}) == ">=3.10,<3.15"
    with pytest.raises(AssertionError, match="run `uv lock`"):
        declared_python({"requires-python": ">=3.10"})
