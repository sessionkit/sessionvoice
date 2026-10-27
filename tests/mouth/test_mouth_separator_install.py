"""`mouth split --install [--dry-run]`: the separator's venv, its model, and the stamp.

- the dry run prints the plan, sizes included, and builds, fetches and writes nothing;
- refused in one stderr line, exit 2, before anything is built: no uv, not an Apple-silicon
  Mac on macOS 14 or later;
- the stamp, installed.json, is written only after both the venv and the model are in, and
  any old stamp is gone once a real install starts;
- the model is checked against its pinned size and full SHA-256 (and the class its
  metadata names) before it is moved into place: a bad checksum is refused and removed.

No network and no real venv: the host, uv, each command and the download are the module's
seams (`_mac`, `_uv`, `_sh`, `_fetch`), faked here. The model is a small stand-in
safetensors file, with MODEL re-pinned to its bytes. The real `_fetch` is driven through
httpx.MockTransport."""
from __future__ import annotations

import hashlib
import json
import struct
import subprocess
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from mouth import __main__ as cli
from mouth import separator

KLASS = "demucs.htdemucs.HTDemucs"


def _safetensors(klass: str = KLASS, body: bytes = b"\0" * 64) -> bytes:
    """A safetensors-shaped file: the 8-byte header length, the JSON header, the data."""
    header = json.dumps({"__metadata__": {"klass": klass, "args": "[]"}}).encode()
    return struct.pack("<Q", len(header)) + header + body


GOOD = _safetensors()


def _pin(data: bytes, klass: str = KLASS) -> separator.Model:
    return replace(separator.MODEL, sha256=hashlib.sha256(data).hexdigest(), size=len(data),
                   klass=klass)


@pytest.fixture
def rig(monkeypatch, tmp_path):
    """An arm64 host on macOS 15 with uv, a separator home in tmp, MODEL pinned to GOOD.
    `_sh` records each command and makes the venv's python on `uv venv`; `_fetch` writes
    rig["payload"]. Set rig["fail"] to the name of a step to make it fail."""
    home = tmp_path / "sephome"
    monkeypatch.setenv("MOUTH_SEPARATOR_HOME", str(home))
    monkeypatch.setattr(separator, "MODEL", _pin(GOOD))
    state = {"home": home, "calls": [], "fetches": [], "payload": GOOD, "fail": None,
             "versions": {"demucs": "4.1.0", "torch": "2.14.1", "numpy": "2.3.0",
                          "soundfile": "0.13.1"},
             "stamp_seen": []}
    monkeypatch.setattr(separator, "_mac", lambda: ("arm64", "15.1"))
    monkeypatch.setattr(separator, "_uv", lambda: "/fake/bin/uv")

    def sh(argv):
        argv = [str(a) for a in argv]
        state["calls"].append(argv)
        state["stamp_seen"].append(separator.stamp_path(home).exists())
        if argv[:2] == ["/fake/bin/uv", "venv"]:
            if state["fail"] == "venv":
                return subprocess.CompletedProcess(argv, 1, "", "error: no python 3.12\n")
            py = Path(argv[-1]) / "bin/python"
            py.parent.mkdir(parents=True, exist_ok=True)
            py.write_text("#!/bin/sh\n")
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:3] == ["/fake/bin/uv", "pip", "install"]:
            if state["fail"] == "pip":
                return subprocess.CompletedProcess(argv, 1, "", "error: hash mismatch for torch\n")
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[1:2] == ["-c"]:
            if state["fail"] == "check":
                return subprocess.CompletedProcess(argv, 1, "", "ModuleNotFoundError: torch\n")
            return subprocess.CompletedProcess(argv, 0, json.dumps(state["versions"]) + "\n", "")
        raise AssertionError(f"unexpected command {argv}")

    def fetch(url, dest, limit, client=None):
        state["fetches"].append((url, Path(dest), limit))
        state["stamp_seen"].append(separator.stamp_path(home).exists())
        if state["fail"] == "fetch":
            Path(dest).write_bytes(b"half")
            raise separator.FetchFailed("ConnectError: no route to host")
        Path(dest).write_bytes(state["payload"])

    monkeypatch.setattr(separator, "_sh", sh)
    monkeypatch.setattr(separator, "_fetch", fetch)
    return state


def _run(capsys, dry_run=False):
    code = separator.install(dry_run=dry_run)
    cap = capsys.readouterr()
    return code, cap.out, [l for l in cap.err.splitlines() if l.strip()]


def _has_python(home: Path):
    py = home / "venv/bin/python"
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text("#!/bin/sh\n")


# ---- the dry run ------------------------------------------------------------------------------

def test_the_dry_run_prints_the_plan_with_sizes_and_does_nothing(rig, capsys):
    code, out, err = _run(capsys, dry_run=True)
    assert code == 0 and err == []
    home = rig["home"]
    assert str(home) in out and "MOUTH_SEPARATOR_HOME" in out
    assert f"{home / 'venv'}: built with uv" in out
    assert "python 3.12" in out and "demucs 4.1.0" in out and "torch 2.14.1" in out
    pins = sum(1 for l in separator.separator_file("requirements.lock").read_text().splitlines()
               if l[:1].isalnum())
    assert f"{pins} packages pinned" in out and "hash-checked" in out
    assert "about 1 GB" in out                                       # the venv's size
    m = separator.MODEL
    assert f"{m.size:,} bytes" in out and m.sha256 in out and m.revision in out
    assert str(separator.stamp_path(home)) in out
    assert "dry run: nothing built, fetched or written" in out
    assert rig["calls"] == [] and rig["fetches"] == []
    assert not home.exists()


def test_the_dry_run_says_what_is_already_there(rig, capsys):
    home = rig["home"]
    _has_python(home)
    separator.model_path(home).parent.mkdir(parents=True)
    separator.model_path(home).write_bytes(GOOD)
    code, out, err = _run(capsys, dry_run=True)
    assert code == 0 and err == []
    assert "present; " in out and "re-applied" in out
    assert f"model {separator.MODEL.signature}: present at" in out
    assert rig["calls"] == [] and rig["fetches"] == []
    assert not separator.stamp_path(home).exists()


def test_the_real_pins_are_the_hub_file_at_a_commit():
    """The shipped pin: a full SHA-256, a commit (never a branch), the hub, never GitHub."""
    m = separator.MODEL
    assert m.url == ("https://huggingface.co/adefossez/HTDemucs/resolve/"
                     "cbc8a9b1a87023b7fd74e7b3412e6321c0eab003/955717e8.safetensors")
    assert len(m.revision) == 40 and int(m.revision, 16) >= 0
    assert m.sha256 == "d9fa14133cfcc034a6758923bb3a8ca9f8dfd0b582134643bbf83f72c17576dd"
    assert m.size == 84_025_440 and m.klass == KLASS and m.signature == "955717e8"
    assert "github" not in m.url


# ---- the refusals -----------------------------------------------------------------------------

@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("host", [("x86_64", "14.5"), ("arm64", "13.6"), ("aarch64", ""),
                                  ("arm64", "")])
def test_a_host_that_is_not_apple_silicon_on_macos_14_is_refused(rig, capsys, monkeypatch,
                                                                  host, dry_run):
    monkeypatch.setattr(separator, "_mac", lambda: host)
    code, out, err = _run(capsys, dry_run=dry_run)
    assert code == 2 and out == ""
    assert len(err) == 1 and "REFUSED" in err[0] and "arm64" in err[0] and "macOS 14" in err[0]
    assert err[0].startswith("mouth split --install: REFUSED: ")
    assert rig["calls"] == [] and rig["fetches"] == [] and not rig["home"].exists()


@pytest.mark.parametrize("dry_run", [False, True])
def test_no_uv_is_refused_naming_uv(rig, capsys, monkeypatch, dry_run):
    monkeypatch.setattr(separator, "_uv", lambda: None)
    code, out, err = _run(capsys, dry_run=dry_run)
    assert code == 2 and out == ""
    assert len(err) == 1 and "REFUSED" in err[0] and "no uv on PATH" in err[0]
    assert rig["calls"] == [] and rig["fetches"] == [] and not rig["home"].exists()


@pytest.mark.parametrize("ver", ["14", "14.0", "15.1", "26.0.1"])
def test_apple_silicon_on_macos_14_or_later_passes_the_host_check(monkeypatch, ver):
    monkeypatch.setattr(separator, "_mac", lambda: ("arm64", ver))
    assert separator.platform_refusal() is None


# ---- the install, and the stamp only after both steps -----------------------------------------

def test_the_install_builds_the_venv_fetches_the_model_then_stamps(rig, capsys):
    code, out, err = _run(capsys)
    assert code == 0, err
    home = rig["home"]
    venv, py = home / "venv", home / "venv/bin/python"
    lock = separator.separator_file("requirements.lock")
    assert rig["calls"] == [
        ["/fake/bin/uv", "venv", "--python", "3.12", str(venv)],
        ["/fake/bin/uv", "pip", "install", "--python", str(py), "--require-hashes", "--no-build",
         "-r", str(lock)],
        [str(py), "-c", separator.CHECK],
    ]
    model = separator.model_path(home)
    assert model == home / "models" / separator.MODEL.file and model.read_bytes() == GOOD
    assert rig["fetches"] == [(separator.MODEL.url, model.with_name(f".{model.name}.part"),
                               separator.MODEL.size)]
    assert not model.with_name(f".{model.name}.part").exists()
    assert rig["stamp_seen"] and not any(rig["stamp_seen"])   # no stamp while any step ran
    stamp = json.loads(separator.stamp_path(home).read_text())
    assert stamp["schema"] == "mouth.separator/1" and stamp["separator"] == "demucs"
    assert stamp["python"] == str(py) and stamp["versions"] == rig["versions"]
    assert stamp["model"] == {"signature": separator.MODEL.signature, "path": str(model),
                              "sha256": separator.MODEL.sha256, "size": len(GOOD),
                              "repo": "adefossez/HTDemucs",
                              "revision": separator.MODEL.revision}
    assert stamp["lock"]["sha256"] == hashlib.sha256(lock.read_bytes()).hexdigest()
    assert separator.read_stamp() == stamp and separator.installed()
    assert f"separator installed: {separator.stamp_path(home)}" in out
    # sizes first: the plan's lines come before any step's
    assert out.index(f"{len(GOOD):,} bytes") < out.index("building ")


def test_a_second_install_reuses_the_venv_and_a_good_model(rig, capsys):
    assert _run(capsys)[0] == 0
    rig["calls"].clear()
    rig["fetches"].clear()
    code, out, err = _run(capsys)
    assert code == 0, err
    assert [c[:3] for c in rig["calls"]] == [["/fake/bin/uv", "pip", "install"],
                                            [str(rig["home"] / "venv/bin/python"), "-c",
                                             separator.CHECK]]
    assert rig["fetches"] == []                            # the model is checked, not fetched
    assert separator.installed()


def test_a_venv_folder_with_no_python_is_built_over_with_clear(rig, capsys):
    (rig["home"] / "venv").mkdir(parents=True)
    assert _run(capsys)[0] == 0
    assert rig["calls"][0] == ["/fake/bin/uv", "venv", "--python", "3.12", "--clear",
                               str(rig["home"] / "venv")]


@pytest.mark.parametrize("step", ["venv", "pip", "check", "fetch"])
def test_no_stamp_unless_both_steps_succeed(rig, capsys, step):
    rig["fail"] = step
    code, out, err = _run(capsys)
    assert code == 1
    assert len(err) == 1 and err[0].startswith("mouth split --install: FAILED: ")
    assert not separator.stamp_path(rig["home"]).exists() and not separator.installed()
    model = separator.model_path(rig["home"])
    assert not model.exists() and not model.with_name(f".{model.name}.part").exists()
    if step != "fetch":
        assert rig["fetches"] == []                        # the venv first, then the model


def test_a_failed_reinstall_removes_the_old_stamp(rig, capsys):
    assert _run(capsys)[0] == 0 and separator.installed()
    rig["fail"] = "pip"
    assert _run(capsys)[0] == 1
    assert not separator.stamp_path(rig["home"]).exists() and not separator.installed()


def test_the_dry_run_keeps_an_existing_stamp(rig, capsys):
    assert _run(capsys)[0] == 0
    assert _run(capsys, dry_run=True)[0] == 0
    assert separator.installed()


def test_a_venv_with_another_demucs_is_refused(rig, capsys):
    rig["versions"]["demucs"] = "4.0.1"
    code, out, err = _run(capsys)
    assert code == 2 and "REFUSED" in err[0] and "4.0.1" in err[0] and "4.1.0" in err[0]
    assert rig["fetches"] == [] and not separator.installed()


# ---- the model's checks -----------------------------------------------------------------------

def test_a_bad_checksum_is_refused_and_removed(rig, capsys):
    bad = bytearray(GOOD)
    bad[-1] ^= 0xFF                                       # the same size, other bytes
    rig["payload"] = bytes(bad)
    code, out, err = _run(capsys)
    assert code == 2
    assert len(err) == 1 and "REFUSED" in err[0] and "sha256" in err[0]
    assert separator.MODEL.sha256 in err[0]
    model = separator.model_path(rig["home"])
    assert not model.exists() and not model.with_name(f".{model.name}.part").exists()
    assert not separator.stamp_path(rig["home"]).exists()


def test_a_download_of_the_wrong_size_is_refused(rig, capsys):
    rig["payload"] = GOOD + b"\0"
    code, out, err = _run(capsys)
    assert code == 2 and "bytes, not the pinned" in err[0]
    assert not separator.model_path(rig["home"]).exists() and not separator.installed()


def test_a_file_naming_another_class_is_refused_even_at_its_pin(rig, capsys, monkeypatch):
    evil = _safetensors(klass="os.system")
    rig["payload"] = evil
    monkeypatch.setattr(separator, "MODEL", _pin(evil, klass=KLASS))   # sha and size match
    code, out, err = _run(capsys)
    assert code == 2 and "os.system" in err[0] and KLASS in err[0]
    assert not separator.model_path(rig["home"]).exists() and not separator.installed()


def test_a_corrupt_model_already_there_is_fetched_again(rig, capsys):
    model = separator.model_path(rig["home"])
    model.parent.mkdir(parents=True)
    model.write_bytes(b"truncated")
    code, out, err = _run(capsys)
    assert code == 0, err
    assert "is not the pinned file" in out and len(rig["fetches"]) == 1
    assert model.read_bytes() == GOOD and separator.installed()


def test_safetensors_klass_reads_the_header_only(tmp_path):
    f = tmp_path / "m.safetensors"
    f.write_bytes(GOOD)
    assert separator.safetensors_klass(f) == KLASS
    f.write_bytes(b"\x05")
    assert separator.safetensors_klass(f) is None
    f.write_bytes(struct.pack("<Q", 4) + b"null")
    assert separator.safetensors_klass(f) is None


# ---- the home and the stamp's reader ----------------------------------------------------------

def test_mouth_separator_home_moves_the_home(monkeypatch, tmp_path):
    monkeypatch.setenv("MOUTH_SEPARATOR_HOME", str(tmp_path / "x"))
    assert separator.separator_home() == tmp_path / "x"
    assert separator.separator_py() == tmp_path / "x/venv/bin/python"
    assert separator.model_path() == tmp_path / "x/models/955717e8.safetensors"
    assert separator.stamp_path() == tmp_path / "x/installed.json"
    monkeypatch.setenv("MOUTH_SEPARATOR_HOME", "~/sep")
    assert separator.separator_home() == Path.home() / "sep"
    monkeypatch.delenv("MOUTH_SEPARATOR_HOME")
    assert separator.separator_home() == Path.home() / ".local/share/ears/separator"


def test_read_stamp_says_none_unless_the_install_is_whole(rig, capsys):
    home = rig["home"]
    assert separator.read_stamp() is None                 # no home at all
    assert _run(capsys)[0] == 0 and separator.installed()
    stamp = separator.stamp_path(home)
    good = stamp.read_text()

    stamp.write_text("{not json")
    assert separator.read_stamp() is None
    stamp.write_text(json.dumps({**json.loads(good), "schema": "mouth.separator/0"}))
    assert separator.read_stamp() is None
    stamp.write_text(good)
    separator.model_path(home).write_bytes(b"short")     # the model's size no longer matches
    assert separator.read_stamp() is None
    separator.model_path(home).write_bytes(GOOD)
    assert separator.installed()
    (home / "venv/bin/python").unlink()                    # the venv's python is gone
    assert separator.read_stamp() is None


# ---- the verb ---------------------------------------------------------------------------------

def test_the_verb_runs_the_install(rig, capsys):
    assert cli.main(["split", "--install", "--dry-run"]) == 0
    assert "dry run: nothing built" in capsys.readouterr().out
    assert cli.main(["split", "--install"]) == 0
    assert separator.installed()


@pytest.mark.parametrize("argv, words", [
    (["split", "--dry-run"], "--dry-run goes with --install"),
    (["split", "--install", "song.flac"], "takes no FILE"),
])
def test_the_verb_refuses_a_bad_mix_of_arguments(rig, capsys, argv, words):
    assert cli.main(argv) == 2
    assert words in capsys.readouterr().err
    assert rig["calls"] == [] and rig["fetches"] == []


# ---- the real download, offline ---------------------------------------------------------------

def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_fetch_follows_the_redirect_and_writes_the_bytes(tmp_path):
    def handler(req):
        if req.url.host == "huggingface.co":
            return httpx.Response(302, headers={"location": "https://cdn.example/blob"})
        return httpx.Response(200, content=GOOD)
    dest = tmp_path / "m.part"
    separator._fetch(separator.MODEL.url, dest, len(GOOD), client=_client(handler))
    assert dest.read_bytes() == GOOD


def test_fetch_refuses_an_http_error(tmp_path):
    with pytest.raises(separator.FetchFailed, match="HTTP 404"):
        separator._fetch("https://huggingface.co/x", tmp_path / "m.part", 10,
                         client=_client(lambda req: httpx.Response(404)))


def test_fetch_turns_a_transport_error_into_fetch_failed(tmp_path):
    def handler(req):
        raise httpx.ConnectError("no route to host", request=req)
    with pytest.raises(separator.FetchFailed, match="ConnectError"):
        separator._fetch("https://huggingface.co/x", tmp_path / "m.part", 10,
                         client=_client(handler))


def test_fetch_stops_past_the_limit(tmp_path):
    big = b"\0" * (3 << 20)
    dest = tmp_path / "m.part"
    separator._fetch("https://huggingface.co/x", dest, 10,
                     client=_client(lambda req: httpx.Response(200, content=big)))
    assert 10 < dest.stat().st_size < len(big)            # one chunk past it, then stopped
