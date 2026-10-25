"""EARS-339: the backend uv fetches to build an sdist-only pin is hash-verified too.

`uv pip install --require-hashes` checks every artifact in the lock, but NOT the build
dependencies uv fetches to build an sdist: measured with uv 0.13.0, a build dependency missing
from `-b/--build-constraints` is fetched with no hash check. So the speech install passes
`-b speech/build-constraints.txt`, and that file must pin, with hashes, the whole build closure
of every sdist-only pin in the lock. These tests hold the two files to each other, offline.

How an sdist-only pin is found in a lock, with no network: by its hash count.
`uv pip compile --generate-hashes` writes a hash for every file PyPI publishes for the pinned
version, every platform's wheels included (one pin in the speech lock has 172). A version that
ships any wheel almost always ships its sdist beside it, so it has two hashes or more. ONE hash
means ONE file, and for an index pin that is an sdist with no wheel: uv must build it. A
direct-URL pin names its own file: a `.whl` URL is a wheel, anything else is built.

- It over-flags, safely: a version published as a lone wheel with no sdist also has one hash.
  The test then fails and a human looks; PyPI's file list for that version settles it.
- What it cannot see: a version with wheels for other platforms only (none for macOS arm64 /
  cp312) and an sdist has two hashes or more, and uv would build it here. The lock does not
  say which file a hash belongs to. Measured against PyPI on 2030-06-09, both locks have no
  such pin. Only docopt==0.6.2 (speech, pure Python) builds from source: the speech lock takes
  webrtcvad-wheels in place of mlx-audio's webrtcvad sdist (a C extension, so a compiler).

The speakers lock has no sdist-only pin, so its install passes no `-b` and it ships no
constraints file: a pin that would need one fails here first."""
import re

import pytest

from ears import services
from tests.test_service_hash_pins import HASH, entries

DECLARED = re.compile(r"^# sdist-only: (\S+==\S+) needs (.+)$", re.M)
LOCKS = {"speech": services._speech_file, "speakers": services._speakers_file}


def sdist_only(lock: str) -> set[str]:
    """The pins in LOCK uv must build: an index pin with one hash, a direct URL not a wheel."""
    out = set()
    for r in entries(lock):
        req = r.split(" --hash")[0]
        if "://" in req:
            if not req.split("://", 1)[1].split("#")[0].endswith(".whl"):
                out.add(req)
        elif len(HASH.findall(r)) == 1:
            out.add(req.split()[0])
    return out


def pinned(constraints: str) -> dict[str, set[str]]:
    """name -> its hashes, for each pin in a build-constraints file."""
    out = {}
    for r in entries(constraints):
        name = re.split(r"[=<>!~ ;\[]", r, maxsplit=1)[0].lower()
        out[name] = set(HASH.findall(r))
    return out


def uncovered(lock: str, constraints: str | None) -> list[str]:
    """Why LOCK's sdist-only pins are not covered by CONSTRAINTS (None: no file ships), or []."""
    text = constraints or ""
    declared = {pin: [p.strip().lower() for p in needs.split(",")]
                for pin, needs in DECLARED.findall(text)}
    pins = pinned(text)
    why = []
    for pin in sorted(sdist_only(lock) - declared.keys()):
        why.append(f"{pin}: sdist-only (one hash) and not declared `# sdist-only: {pin} needs ...` "
                   "in build-constraints.txt; measure its build closure (uv pip install -v) and "
                   "pin it there with every hash")
    for pin in sorted(declared.keys() - sdist_only(lock)):
        why.append(f"{pin}: declared sdist-only but no longer an sdist-only pin in the lock")
    for pin, needs in sorted(declared.items()):
        for name in needs:
            if not pins.get(name):
                why.append(f"{pin} needs {name}, which build-constraints.txt does not pin with a hash")
    why += [f"{name}: pinned with no hash" for name, hashes in sorted(pins.items()) if not hashes]
    return why


def _constraints(name: str) -> str | None:
    try:
        return LOCKS[name]("build-constraints.txt").read_text()
    except (services.NoSpeech, services.NoSpeakers):
        return None


@pytest.mark.parametrize("name", LOCKS)
def test_every_sdist_only_pin_has_its_build_closure_hash_pinned(name):
    assert uncovered(LOCKS[name]("requirements.lock").read_text(), _constraints(name)) == []


def test_the_speech_lock_builds_exactly_docopt():
    """The measured finding the file was written for (PyPI's file lists, 2030-06-09), less
    webrtcvad: the one C extension built from source, so the one pin that needed a compiler."""
    lock = services._speech_file("requirements.lock").read_text()
    assert sdist_only(lock) == {"docopt==0.6.2"}
    assert set(DECLARED.findall(_constraints("speech"))) == {("docopt==0.6.2", "setuptools")}


def test_the_speakers_lock_builds_nothing_and_ships_no_constraints():
    assert sdist_only(services._speakers_file("requirements.lock").read_text()) == set()
    assert _constraints("speakers") is None


def test_the_backend_is_the_locks_own_setuptools_with_every_hash():
    """One setuptools for the venv: the build pin is the runtime pin, with both of its files
    (sdist and wheel, all PyPI publishes for 80.10.2)."""
    lock = pinned(services._speech_file("requirements.lock").read_text())
    build = pinned(_constraints("speech"))
    assert list(build) == ["setuptools"]
    assert "\nsetuptools==80.10.2 \\\n" in _constraints("speech")
    assert "\nsetuptools==80.10.2 \\\n" in services._speech_file("requirements.lock").read_text()
    assert build["setuptools"] == lock["setuptools"] and len(build["setuptools"]) == 2


def test_the_file_says_how_install_uses_it():
    text = _constraints("speech")
    assert "--require-hashes" in text and "-b build-constraints.txt -r requirements.lock" in text


# ---- the check is only as good as uncovered(): each way out is caught ---------------------

H = ["--hash=sha256:" + c * 64 for c in "abcdef"]
LOCK = (f"# head\nfoo==1.0 \\\n    {H[0]} \\\n    {H[1]}\n    # via x\n"
        f"bar==2.0 \\\n    {H[2]}\n    # via x\n")
FILE = f"# sdist-only: bar==2.0 needs setuptools\nsetuptools==80.10.2 \\\n    {H[3]}\n"


def test_a_covered_lock_passes():
    assert sdist_only(LOCK) == {"bar==2.0"}                  # two hashes: wheels, not built
    assert uncovered(LOCK, FILE) == []


def test_a_lock_that_gains_an_sdist_only_pin_is_refused():
    gained = LOCK + f"baz==3.0 \\\n    {H[4]}\n"
    why = uncovered(gained, FILE)
    assert len(why) == 1 and why[0].startswith("baz==3.0: sdist-only")


def test_a_declared_sdist_whose_closure_is_not_pinned_is_refused():
    flit = FILE + "# sdist-only: baz==3.0 needs flit-core\n"
    why = uncovered(LOCK + f"baz==3.0 \\\n    {H[4]}\n", flit)
    assert why == ["baz==3.0 needs flit-core, which build-constraints.txt does not pin with a hash"]


def test_an_unhashed_backend_is_refused():
    assert uncovered(LOCK, FILE.replace(f" \\\n    {H[3]}", "")) == [
        "bar==2.0 needs setuptools, which build-constraints.txt does not pin with a hash",
        "setuptools: pinned with no hash"]


def test_no_constraints_file_refuses_any_sdist_only_pin():
    assert [w.split(":")[0] for w in uncovered(LOCK, None)] == ["bar==2.0"]


def test_a_stale_declaration_is_refused():
    wheeled = LOCK.replace(f"    {H[2]}\n", f"    {H[2]} \\\n    {H[5]}\n")   # bar gains a wheel
    assert sdist_only(wheeled) == set()
    assert uncovered(wheeled, FILE) == [
        "bar==2.0: declared sdist-only but no longer an sdist-only pin in the lock",
    ]


def test_a_direct_url_is_built_unless_it_is_a_wheel():
    urls = (f"w @ https://example.org/w-1.0-py3-none-any.whl \\\n    {H[0]}\n"
            f"s @ https://example.org/s-1.0.tar.gz \\\n    {H[1]}\n")
    assert sdist_only(urls) == {"s @ https://example.org/s-1.0.tar.gz"}
