"""The mouth's own copy of VoiceMode's voice resolver (EARS-292), on real clone dirs in tmp.

No VoiceMode installed or imported; no network; no sound. HOME is the fenced tmp
dir (conftest), so ``~/.voicemode`` here is a tmp dir too.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import wave
from pathlib import Path

import pytest

from mouth import backends, resolver, voices
from ears import envnames

SRC = Path(__file__).resolve().parents[2] / "src"


def wav(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\0\0" * 240)
    return path


def voice(root: Path, rel: str, text: str = "hello there", name: str = "default") -> Path:
    d = root / rel
    wav(d / f"{name}.wav")
    if text is not None:
        (d / f"{name}.txt").write_text(text + "\n")
    return d


@pytest.fixture
def vdir(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "voices"
    root.mkdir()
    monkeypatch.setenv("VOICEMODE_VOICES_DIR", str(root))
    for k in ("VOICEMODE_REMOTE_VOICES_DIR", "VOICEMODE_MLX_AUDIO_BASE_URL",
              "VOICEMODE_IMPRESSIONS_MODEL", "VOICEMODE_CAST_UNDECLARED_SEVERITY"):
        monkeypatch.delenv(k, raising=False)
    return root


def envfile(text: str) -> Path:
    p = Path(os.environ["HOME"]) / ".voicemode" / "voicemode.env"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


# -- what resolves ------------------------------------------------------------------

def test_a_clone_folder_is_a_voice_with_its_clip_and_transcript(vdir):
    voice(vdir, "ivy", "hi, it's ivy")
    r = resolver.resolve_voice("ivy")
    assert (r.kind, r.resolved, r.via) == ("clone", "ivy", "leaf")
    assert r.profile.ref_audio == str((vdir / "ivy" / "default.wav").resolve())
    assert r.profile.ref_text == "hi, it's ivy"
    assert r.profile.base_url == "http://127.0.0.1:8890/v1"
    assert r.profile.model == "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-4bit"


def test_a_single_wav_is_the_voice_and_voice_md_gives_the_transcript(vdir):
    d = vdir / "laurie"
    wav(d / "take1.wav")
    (d / "voice.md").write_text("---\nname: laurie\ntranscript: >-\n  folded over\n  two lines\n---\nnotes\n")
    r = resolver.resolve_voice("laurie")
    assert r.profile.ref_audio.endswith("laurie/take1.wav")
    assert r.profile.ref_text == "folded over two lines"


def test_index_file_selector_and_group_qualified_forms(vdir):
    d = voice(vdir, "demo/clone-a", "the default")
    wav(d / "01-a.wav")
    (d / "01-a.txt").write_text("line a")
    wav(d / "02-line.wav")
    (d / "02-line.txt").write_text("then I'll face it")
    assert resolver.resolve_voice("demo/clone-a").via == "qualified"
    r = resolver.resolve_voice("demo/clone-a/02-line.wav")
    assert (r.via, r.profile.ref_text) == ("file-selector", "then I'll face it")
    r = resolver.resolve_voice("clone-a[0]")
    assert (r.via, r.profile.ref_text) == ("leaf+index[0]", "line a")
    with pytest.raises(resolver.IndexOutOfRange, match="valid range 0-2"):
        resolver.resolve_voice("clone-a[3]")


def test_a_cast_resolves_to_its_declared_default(vdir):
    voice(vdir, "crew/picard")
    voice(vdir, "crew/riker")
    (vdir / "crew" / "voice.md").write_text("---\ndefault: riker\n---\n")
    r = resolver.resolve_voice("crew")
    assert (r.resolved, r.via) == ("crew/riker", "cast-default:riker")


def test_a_group_with_no_default_is_not_a_cast_unless_warn(vdir, monkeypatch):
    voice(vdir, "crew/picard")
    voice(vdir, "crew/riker")
    with pytest.raises(resolver.NotACast, match="declares no default"):
        resolver.resolve_voice("crew")
    monkeypatch.setenv("VOICEMODE_CAST_UNDECLARED_SEVERITY", "warn")
    assert resolver.resolve_voice("crew").resolved == "crew/picard"


def test_two_voices_with_one_leaf_are_ambiguous_bare_and_fine_qualified(vdir):
    voice(vdir, "a/sam")
    voice(vdir, "b/sam")
    with pytest.raises(resolver.AmbiguousLeaf, match="'a/sam'"):
        resolver.resolve_voice("sam")
    assert resolver.resolve_voice("b/sam").resolved == "b/sam"


def test_a_sample_bin_names_the_cure(vdir):
    wav(vdir / "bin" / "x.wav")
    wav(vdir / "bin" / "y.wav")
    with pytest.raises(resolver.Unresolvable, match="ln -s x.wav default.wav"):
        resolver.resolve_voice("bin")


def test_provider_names_pass_and_a_typo_is_refused_with_did_you_mean(vdir):
    voice(vdir, "ivy")
    for name in ("af_sky", "af_nicole", "alloy"):
        assert resolver.resolve_voice(name).kind == "provider"
    with pytest.raises(resolver.Unresolvable) as e:
        resolver.resolve_voice("ivyy")
    assert "Did you mean: ivy?" in str(e.value) and "`mouth voices`" in str(e.value)
    assert "voicemode" not in str(e.value).lower().replace("voicemode_", "")


def test_a_path_is_sent_as_is_with_its_sidecar(vdir, tmp_path):
    clip = wav(tmp_path / "clip.wav")
    (tmp_path / "clip.txt").write_text("a clip")
    r = resolver.resolve_voice(str(clip))
    assert (r.kind, r.profile.ref_audio, r.profile.ref_text) == ("clip", str(clip), "a clip")


def test_no_voices_dir_is_no_clones_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICEMODE_VOICES_DIR", str(tmp_path / "nowhere"))
    assert resolver.load_profiles() == {}
    assert voices.clones() == []
    assert resolver.resolve_voice("af_sky").kind == "provider"


# -- settings: the env, else ~/.voicemode/voicemode.env, else VoiceMode's default ----

def test_voicemode_env_gives_the_clone_model_url_and_remote_dir(vdir):
    voice(vdir, "ivy")
    envfile("# a comment\nVOICEMODE_IMPRESSIONS_MODEL=mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16\n"
            "VOICEMODE_MLX_AUDIO_BASE_URL='http://host4:8890/v1'\n"
            'VOICEMODE_REMOTE_VOICES_DIR="/srv/voices"\n'
            "VOICEMODE_IMPRESSIONS_MODEL=second-one-loses\n")
    p = resolver.resolve_voice("ivy").profile
    assert p.model == "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16"
    assert p.base_url == "http://host4:8890/v1"
    assert p.ref_audio == "/srv/voices/ivy/default.wav"


def test_the_process_env_beats_voicemode_env(vdir, monkeypatch):
    voice(vdir, "ivy")
    envfile("VOICEMODE_IMPRESSIONS_MODEL=from-file\nVOICEMODE_MLX_AUDIO_BASE_URL=http://file/v1\n")
    monkeypatch.setenv("VOICEMODE_IMPRESSIONS_MODEL", "from-env")
    p = resolver.resolve_voice("ivy").profile
    assert (p.model, p.base_url) == ("from-env", "http://file/v1")


def test_voicemode_env_can_move_the_voices_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("VOICEMODE_VOICES_DIR", raising=False)
    voice(tmp_path / "elsewhere", "ivy")
    envfile(f"VOICEMODE_VOICES_DIR={tmp_path / 'elsewhere'}\n")
    assert voices.clones() == ["ivy"]


def test_a_multiline_quoted_value_is_read_whole(tmp_path):
    p = tmp_path / "e.env"
    p.write_text('A="one\ntwo"\nB=3\n')
    assert resolver._read_env_file(p) == {"A": "one\ntwo", "B": "3"}


# -- EARS-250 item 4: a clone folder made after the mouth started -----------------

def test_a_folder_made_after_the_first_line_resolves_without_a_restart(vdir):
    voice(vdir, "before")
    assert resolver.resolve_voice("before").resolved == "before"
    voice(vdir, "after", "made later")
    r = resolver.resolve_voice("after")  # the list read at "before" did not have it
    assert (r.resolved, r.profile.ref_text) == ("after", "made later")


def test_the_player_path_speaks_a_late_folder_as_a_clone(vdir, monkeypatch):
    monkeypatch.setattr(backends, "apple_available", lambda: False)  # no server probe
    voice(vdir, "before")
    assert backends.resolve("clone", "before")[0].name == "clone"
    voice(vdir, "after")
    be, _ = backends.resolve("auto", "after")
    assert be.name == "clone" and be.extra_body["ref_audio"].endswith("after/default.wav")


def test_a_real_miss_is_still_refused_after_the_reread(vdir, monkeypatch):
    monkeypatch.setattr(backends, "apple_available", lambda: False)  # no server probe
    voice(vdir, "ivy")
    resolver.resolve_voice("ivy")
    with pytest.raises(ValueError, match="Unresolvable voice 'nobody'"):
        backends.resolve("auto", "nobody")


# -- the copy needs, imports and writes no VoiceMode ---------------------------------

def test_the_mouths_resolver_path_imports_no_voice_mode():
    for f in ("resolver.py", "backends.py", "voices.py"):
        tree = ast.parse((SRC / "mouth" / f).read_text())
        names = [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        names += [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        assert not [m for m in names if m.split(".")[0] == "voice_mode"], f


def test_resolving_imports_no_voice_mode_and_writes_nothing(tmp_path):
    """A fresh interpreter, HOME and voices in tmp: VoiceMode's import wrote
    ~/.voicemode/voicemode.env and made dirs there (CS-11). The copy does neither."""
    home, vd = tmp_path / "home", tmp_path / "voices"
    home.mkdir()
    voice(vd, "ivy")
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    code = ("import sys; from mouth import backends, resolver, voices\n"
            "assert voices.clones() == ['ivy']\n"
            "assert backends.resolve('clone', 'ivy')[0].name == 'clone'\n"
            "assert resolver.resolve_voice('af_sky').kind == 'provider'\n"
            "print(sorted(m for m in sys.modules if m.split('.')[0] == 'voice_mode'))\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("VOICEMODE_") and k not in envnames.ALIASES}
    env.update(HOME=str(home), VOICEMODE_VOICES_DIR=str(vd), PYTHONPATH=str(SRC))
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before
