"""`ears install` skips the Apple helper's swift build unless asked (--apple): the build fails
on macOS 26 (VM-2430 B1), and the default engine is Parakeet on mlx-audio, so the
Swift build is out for now."""
import subprocess

from ears import cli


def _install(monkeypatch, tmp_path, argv):
    monkeypatch.setenv("HOME", str(tmp_path))      # install makes ~/.cache/ears/bin (EARS-313)
    ran = []
    monkeypatch.setattr(cli.subprocess, "run", lambda cmd, **kw: ran.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    monkeypatch.setattr(cli.shutil, "copy2", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "silero_model_path", lambda: "/x/silero_vad.onnx")
    monkeypatch.setattr(cli.sys, "platform", "darwin")
    assert cli.main(argv) == 0
    return ran


def test_install_builds_no_swift_by_default(monkeypatch, tmp_path):
    assert not [c for c in _install(monkeypatch, tmp_path, ["install"]) if "swift" in c]


def test_install_apple_builds_the_helper(monkeypatch, tmp_path):
    assert [c for c in _install(monkeypatch, tmp_path, ["install", "--apple"]) if c[:2] == ["swift", "build"]]


def test_install_apple_without_swift_refuses_naming_the_checkout(monkeypatch, tmp_path, capsys):
    """EARS-160: the wheel does not ship swift/. From the kit, --apple used to skip the helper
    without a word, fetch Silero and print `done`, exit 0. Now it refuses before anything."""
    monkeypatch.setenv("HOME", str(tmp_path))
    site = tmp_path / "venv" / "lib" / "python3.12" / "site-packages"
    monkeypatch.setattr(cli, "__file__", str(site / "ears" / "cli.py"))   # installed: no swift/ beside it
    ran = []
    monkeypatch.setattr(cli.subprocess, "run", lambda cmd, **kw: ran.append(cmd))
    monkeypatch.setattr(cli, "fetch_silero", lambda dst: ran.append(["fetch_silero", dst]))
    monkeypatch.setattr(cli.sys, "platform", "darwin")
    assert cli.main(["install", "--apple"]) == 2
    err = capsys.readouterr().err
    assert "REFUSED" in err and "source checkout" in err and "git checkout" in err
    assert str(site.parent / "swift") in err                     # where it looked
    assert ran == [] and not (tmp_path / ".cache").exists()     # nothing built, fetched or made


def test_install_without_apple_needs_no_swift(monkeypatch, tmp_path):
    # the refusal is --apple's alone: a plain install from the kit still fetches Silero
    monkeypatch.setattr(cli, "__file__", str(tmp_path / "site-packages" / "ears" / "cli.py"))
    assert not [c for c in _install(monkeypatch, tmp_path, ["install"]) if "swift" in c]
