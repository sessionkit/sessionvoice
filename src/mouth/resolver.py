"""The mouth's clone-voice resolver: a voice expression -> a reference clip, or a loud refusal.

Provenance: copied from VoiceMode's ``voice_mode/voice_profiles.py`` at commit
86cb3da2 (the voicemode checkout), the copy the mouth venv was pinned
to.
sessionvoice has no dependency on VoiceMode. Only what
``resolve_voice``, ``VoiceResolutionError`` and ``load_profiles`` need came
over; the old best-effort wrappers (``get_profile``, ``is_clone_voice``,
``parse_voice_expr``, ``list_profiles``) did not. What changed from the source:

* No ``import voice_mode``. VoiceMode's config import WROTE
  ``~/.voicemode/voicemode.env`` and made dirs under ``~/.voicemode``.
  This module writes nothing and makes no directory, on import or ever.
* Settings are read when the voices are (re)loaded, not at import. Each one is
  the process env, else the same key in ``~/.voicemode/voicemode.env`` (read,
  never written), else VoiceMode's default. That file is where a live
  mouth got its clone model (``VOICEMODE_IMPRESSIONS_MODEL``) from: VoiceMode's
  config import loaded it into the env. The keys:
  ``VOICEMODE_VOICES_DIR`` (~/.voicemode/voices), ``VOICEMODE_REMOTE_VOICES_DIR``,
  ``VOICEMODE_MLX_AUDIO_BASE_URL`` (http://127.0.0.1:8890/v1),
  ``VOICEMODE_IMPRESSIONS_MODEL`` (mlx-community/Qwen3-TTS-12Hz-1.7B-Base-4bit),
  ``VOICEMODE_CAST_UNDECLARED_SEVERITY`` (error).
* Provider-native names are VoiceMode's static seeds: OpenAI's six voices and
  its known Kokoro voices. VoiceMode also asked its configured TTS endpoints
  (provider discovery); on one host those were a Kokoro endpoint and OpenAI, whose
  seeds are exactly these lists.
* A miss re-reads the voices dir once: the long-lived
  ``mouth mail`` player loaded the list at its first line and refused a clone
  folder made after that ("Unresolvable voice") until it restarted.
* Errors point at ``mouth voices``, not at VoiceMode's CLI.

Voices live in the voices dir. Each subdirectory with a ``default.wav`` (or a
single ``*.wav``) is a voice; its transcript is ``<basename>.txt``,
``default.txt`` or the ``transcript:`` of its ``voice.md`` front matter. A
directory of voices is a group; a group whose ``voice.md`` declares
``default:`` is a cast. Voice expressions:

* ``samantha``           the voice's ``default.wav``
* ``samantha[2]``        the third ``*.wav`` (sorted)
* ``samantha/angry.wav`` an explicit file inside the voice dir
* ``group/samantha``     a group-qualified voice (also with ``[N]`` or ``/file.wav``)
* ``group``              a cast: its declared default member, recursively
* ``/abs/path.wav``      a path, sent as is (also ``./``, ``../`` and ``~/``, made absolute)

:func:`resolve_voice` is total: it returns a :class:`VoiceResolution` or raises
a :class:`VoiceResolutionError`. Nothing is silently swapped for another voice.

A remote TTS server needs the clip's path on ITS filesystem:
``VOICEMODE_REMOTE_VOICES_DIR`` is where the voices dir is mirrored there.
"""

from __future__ import annotations

import difflib
import logging
import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)

#: VoiceMode's defaults for the keys this module reads (voice_profiles.py:64-79, config.py:931-934).
DEFAULTS = {
    "VOICEMODE_VOICES_DIR": "~/.voicemode/voices",
    "VOICEMODE_REMOTE_VOICES_DIR": "",
    "VOICEMODE_MLX_AUDIO_BASE_URL": "http://127.0.0.1:8890/v1",
    "VOICEMODE_IMPRESSIONS_MODEL": "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-4bit",
}

# Set by _configure() each time the voices are loaded (never at import).
VOICES_DIR = Path(os.path.expanduser(DEFAULTS["VOICEMODE_VOICES_DIR"]))
REMOTE_VOICES_DIR = ""
DEFAULT_CLONE_BASE_URL = DEFAULTS["VOICEMODE_MLX_AUDIO_BASE_URL"]
DEFAULT_CLONE_MODEL = DEFAULTS["VOICEMODE_IMPRESSIONS_MODEL"]

# Matches ``name[0]``, ``name[12]`` as the FINAL path segment.
_INDEX_RE = re.compile(r"^([^/\[\]]+)\[(\d+)\]$")

# "warn": an undeclared group resolves to its alphabetically-first member, with
# a warning, instead of raising NotACast. Read at call time.
UNDECLARED_GROUP_SEVERITY_ENV = "VOICEMODE_CAST_UNDECLARED_SEVERITY"

# OpenAI's six original TTS voices (VoiceMode's OPENAI_NATIVE_VOICES and OPENAI_SEED_VOICES).
OPENAI_NATIVE_VOICES = frozenset({"alloy", "echo", "fable", "nova", "onyx", "shimmer"})

# VoiceMode's KNOWN_KOKORO_VOICES (provider_discovery.py:34-48 @ 86cb3da2): what a
# kokoro-family endpoint can say. A superset of its KOKORO_MAPPED_VOICES.
KNOWN_KOKORO_VOICES = frozenset({
    "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jadzia", "af_jessica",
    "af_kore", "af_nicole", "af_nova", "af_river", "af_sarah", "af_sky",
    "af_v0", "af_v0bella", "af_v0irulan", "af_v0nicole", "af_v0sarah",
    "af_v0sky", "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam",
    "am_michael", "am_onyx", "am_puck", "am_santa", "am_v0adam", "am_v0gurney",
    "am_v0michael", "bf_alice", "bf_emma", "bf_lily", "bf_v0emma",
    "bf_v0isabella", "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
    "bm_v0george", "bm_v0lewis", "ef_dora", "em_alex", "em_santa", "ff_siwis",
    "hf_alpha", "hf_beta", "hm_omega", "hm_psi", "if_sara", "im_nicola",
    "jf_alpha", "jf_gongitsune", "jf_nezumi", "jf_tebukuro", "jm_kumo",
    "pf_dora", "pm_alex", "pm_santa", "zf_xiaobei", "zf_xiaoni", "zf_xiaoxiao",
    "zf_xiaoyi", "zm_yunjian", "zm_yunxi", "zm_yunxia", "zm_yunyang",
})


# -- settings: the env, else ~/.voicemode/voicemode.env (read only), else the default ---

def _env_file() -> Path:
    return Path.home() / ".voicemode" / "voicemode.env"


def _read_env_file(path: Path) -> Dict[str, str]:
    """``KEY=VALUE`` lines, as VoiceMode's loader reads them (config.py:480-532).

    Comments and blank lines are skipped; a value in matching quotes loses
    them; a multi-line quoted value runs to its closing quote. The first
    occurrence of a key wins, as it did there (a key already set is kept). A
    missing or unreadable file is no settings, never an error.
    """
    try:
        lines = path.read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return {}
    out: Dict[str, str] = {}
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (s.strip() for s in line.split("=", 1))
        if value and value[0] in ('"', "'"):
            q = value[0]
            if len(value) > 1 and value[-1] == q:
                value = value[1:-1]
            else:
                parts = [value[1:]]
                while i < len(lines):
                    nxt = lines[i].rstrip("\n")
                    i += 1
                    if nxt.endswith(q):
                        parts.append(nxt[:-1])
                        break
                    parts.append(nxt)
                value = "\n".join(parts)
        if key and key not in out:
            out[key] = value
    return out


def setting(key: str, file_values: Optional[Dict[str, str]] = None) -> str:
    """One setting: the process env, else voicemode.env, else VoiceMode's default."""
    if key in os.environ:
        return os.environ[key]
    if file_values is None:
        file_values = _read_env_file(_env_file())
    if key in file_values:
        return file_values[key]
    return DEFAULTS.get(key, "")


def _configure() -> None:
    global VOICES_DIR, REMOTE_VOICES_DIR, DEFAULT_CLONE_BASE_URL, DEFAULT_CLONE_MODEL
    f = _read_env_file(_env_file())
    VOICES_DIR = Path(os.path.expanduser(setting("VOICEMODE_VOICES_DIR", f)))
    REMOTE_VOICES_DIR = setting("VOICEMODE_REMOTE_VOICES_DIR", f)
    DEFAULT_CLONE_BASE_URL = setting("VOICEMODE_MLX_AUDIO_BASE_URL", f)
    DEFAULT_CLONE_MODEL = setting("VOICEMODE_IMPRESSIONS_MODEL", f)


# -- results and errors ----------------------------------------------------------

@dataclass
class VoiceProfile:
    """A voice cloning profile."""
    name: str
    ref_audio: str       # Absolute path to reference audio (server-side)
    ref_text: str        # Transcript of reference audio
    model: str           # TTS model to use
    base_url: str        # TTS endpoint URL
    description: str = ""
    voice_dir: str = ""  # Absolute path to the voice's own directory


@dataclass(frozen=True)
class VoiceResolution:
    """The total result of resolving a voice expression; failure raises instead."""
    requested: str                    # verbatim caller expression
    resolved: str                     # canonical id / provider name: what will actually sound
    kind: str                         # "clone" | "provider" | "clip"
    via: str                          # "leaf" | "qualified" | "cast-default:<chain>" |
                                      #   "index[N]" | "file-selector" | "provider-native" | "path-escape"
    profile: Optional[VoiceProfile] = None


class VoiceResolutionError(Exception):
    """Base class for every :func:`resolve_voice` failure; each says what to do."""


class Unresolvable(VoiceResolutionError):
    """Nothing matched: a leaf, a group, or a provider-native name."""

    def __init__(self, expr: str, detail: str, nearest: Optional[List[str]] = None):
        self.expr = expr
        self.detail = detail
        self.nearest = nearest or []
        msg = f"Unresolvable voice {expr!r}: {detail}."
        if self.nearest:
            msg += f" Did you mean: {', '.join(self.nearest)}?"
        msg += " See `mouth voices`."
        super().__init__(msg)


class AmbiguousLeaf(VoiceResolutionError):
    """A bare leaf name matches two or more canonical ids."""

    def __init__(self, expr: str, name: str, candidates: List[str]):
        self.expr = expr
        self.name = name
        self.candidates = candidates
        super().__init__(
            f"Ambiguous voice {name!r} in {expr!r}: matches {len(candidates)} entries "
            f"({', '.join(candidates)}). Use a qualified form, e.g. {candidates[0]!r}."
        )


class NotACast(VoiceResolutionError):
    """A group was named but has no declared ``default:``: not yet a cast."""

    def __init__(self, expr: str, group: str, members: List[str]):
        self.expr = expr
        self.group = group
        self.members = members
        label = group or "<voices root>"
        members_str = ", ".join(members) if members else "(no members)"
        hint = (
            f" Declare one: `default: <member>` in the front matter of {label}/voice.md, "
            f"or address a member directly (e.g. {label}/{members[0]})."
            if members else ""
        )
        super().__init__(
            f"{label!r} is a group of {len(members)} voice(s) but declares no "
            f"default — not yet a cast. Members: {members_str}.{hint}"
        )


class CastDefaultMissing(VoiceResolutionError):
    """A cast's declared default names a member that doesn't exist."""

    def __init__(self, expr: str, group: str, declared: str, members: List[str]):
        self.expr = expr
        self.group = group
        self.declared = declared
        self.members = members
        members_str = ", ".join(members) if members else "(no members)"
        super().__init__(
            f"Cast {group!r}'s declared default {declared!r} does not exist. "
            f"Actual members: {members_str}. Fix {group}/voice.md's `default:` field."
        )


class IndexOutOfRange(VoiceResolutionError):
    """``name[N]`` where N is outside the available sample range."""

    def __init__(self, expr: str, canon: str, index: int, count: int):
        self.expr = expr
        self.canon = canon
        self.index = index
        self.count = count
        range_str = f"valid range 0-{count - 1}" if count else "no samples available"
        super().__init__(
            f"Sample index {index} out of range for {canon!r} in {expr!r}: "
            f"{count} sample(s) available ({range_str})."
        )


class BadSelector(VoiceResolutionError):
    """A file selector or path segment shape that doesn't make sense."""

    def __init__(self, expr: str, detail: str):
        self.expr = expr
        self.detail = detail
        super().__init__(f"Bad selector in {expr!r}: {detail}")


@dataclass
class _Registry:
    """What one walk of the voices dir found."""
    profiles: Dict[str, VoiceProfile] = field(default_factory=dict)          # canonical id -> profile
    leaf_index: Dict[str, List[str]] = field(default_factory=dict)           # voice leaf -> [canonical id]
    group_children: Dict[str, Dict[str, str]] = field(default_factory=dict)  # group canon -> {child: "voice"|"group"|"sample_bin"}
    group_default: Dict[str, Optional[str]] = field(default_factory=dict)    # group canon -> declared default leaf (or None)
    group_leaf_index: Dict[str, List[str]] = field(default_factory=dict)     # group leaf -> [group canonical id]
    sample_bins: Dict[str, str] = field(default_factory=dict)                # sample-bin canon -> hint wav filename
    sample_bin_leaf_index: Dict[str, List[str]] = field(default_factory=dict)  # sample-bin leaf -> [canonical id]


_registry = _Registry()
_loaded = False


# -- reading the voices dir --------------------------------------------------------

def _resolve_default_wav(voice_dir: Path) -> Optional[Path]:
    """The reference WAV of a voice dir: ``default.wav``, else its only ``*.wav``.

    Two or more WAVs and no ``default.wav`` is a sample bin, not a voice: skipped,
    with a warning naming the cure.
    """
    default = voice_dir / "default.wav"
    if default.exists():
        return default
    wavs = sorted(voice_dir.glob("*.wav"))
    if not wavs:
        return None
    if len(wavs) == 1:
        return wavs[0]
    logger.warning(
        f"Skipping {voice_dir.name!r}: {len(wavs)} WAVs and no default.wav "
        f"(treated as a sample bin, not a voice — add a default.wav symlink "
        f"to register it, e.g. `ln -s {wavs[0].name} default.wav`)."
    )
    return None


def _sample_bin_hint(voice_dir: Path) -> Optional[str]:
    """The first ``*.wav`` name if ``voice_dir`` is a sample bin, else None."""
    if (voice_dir / "default.wav").exists():
        return None
    wavs = sorted(voice_dir.glob("*.wav"))
    return wavs[0].name if len(wavs) >= 2 else None


def _resolve_transcript(wav_path: Path) -> str:
    """``<basename>.txt``, else ``default.txt``, else ``voice.md``'s ``transcript:``; else ''."""
    same_name = wav_path.with_suffix(".txt")
    if same_name.exists():
        return same_name.read_text().strip()
    fallback = wav_path.parent / "default.txt"
    if fallback.exists():
        return fallback.read_text().strip()
    return _transcript_from_voice_md(wav_path.parent / "voice.md")


def _extract_frontmatter(text: str) -> Optional[str]:
    """The YAML between the first two ``---`` fences, or None."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[1:i])
    return None


def _voice_md_field(voice_md: Path, key: str) -> Optional[str]:
    """A non-empty string field of a ``voice.md``'s front matter, stripped, or None."""
    if not voice_md.exists():
        return None
    try:
        front = _extract_frontmatter(voice_md.read_text())
    except OSError:
        return None
    if front is None:
        return None
    try:
        data = yaml.safe_load(front)
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    v = data.get(key)
    if not isinstance(v, str) or not v.strip():
        return None
    return v.strip()


def _transcript_from_voice_md(voice_md: Path) -> str:
    return _voice_md_field(voice_md, "transcript") or ""


def _read_group_default(group_dir: Path) -> Optional[str]:
    """A group's declared ``default:`` (what makes it a cast), or None."""
    return _voice_md_field(group_dir / "voice.md", "default")


def _read_description(voice_dir: Path) -> str:
    desc_path = voice_dir / "description.txt"
    if desc_path.exists():
        return desc_path.read_text().strip()
    return ""


def _derive_group(voice_dir: Path) -> str:
    """The voice's group: its parent's path under the voices dir ('' at the top)."""
    s = voice_dir.parent.relative_to(VOICES_DIR).as_posix()
    return "" if s == "." else s


def _format_description(base_desc: str, group: str) -> str:
    if not group:
        return base_desc
    suffix = f"(from {group})"
    return f"{base_desc}\n{suffix}" if base_desc else suffix


def _canonical_id(path: Path) -> str:
    """The registry key: the path under the voices dir (``laurie``, ``star-trek/tng/picard``)."""
    return path.relative_to(VOICES_DIR).as_posix()


def _build_profile(voice_dir: Path, wav: Path) -> VoiceProfile:
    transcript = _resolve_transcript(wav)
    if not transcript:
        logger.warning(
            f"Voice {voice_dir.name!r}: no transcript found "
            f"(expected {wav.with_suffix('.txt').name} or default.txt). "
            f"ref_text will be empty."
        )
    return VoiceProfile(
        name=voice_dir.name,
        ref_audio=_translate_path(wav),
        ref_text=transcript,
        model=DEFAULT_CLONE_MODEL,
        base_url=DEFAULT_CLONE_BASE_URL,
        description=_format_description(_read_description(voice_dir), _derive_group(voice_dir)),
        voice_dir=str(voice_dir),
    )


def _is_dot_dir(path: Path) -> bool:
    return path.name.startswith(".")


def _has_voice_like_content(path: Path) -> bool:
    if _resolve_default_wav(path) is not None:
        return True
    try:
        children = [p for p in path.iterdir() if p.is_dir() and not _is_dot_dir(p)]
    except OSError:
        return False
    return any(_has_voice_like_content(c) for c in children)


def _load_dir_profiles() -> _Registry:
    """Walk the voices dir: a dir with a resolvable WAV is a voice (not descended
    into); a dir of dirs is a group. Dot-dirs are never walked. Two voices with
    one leaf name both register; the bare name then raises AmbiguousLeaf."""
    reg = _Registry()
    conflicts: Dict[str, List[str]] = {}

    if not VOICES_DIR.is_dir():
        logger.debug(f"Voices directory not found at {VOICES_DIR}")
        return reg

    def register_child(parent_canon: str, name: str, kind: str) -> None:
        reg.group_children.setdefault(parent_canon, {})[name] = kind

    def walk(dir_path: Path, parent_canon: str) -> None:
        wav = _resolve_default_wav(dir_path)
        if wav is not None:
            canon = _canonical_id(dir_path)
            leaf = dir_path.name
            if leaf in reg.leaf_index:
                conflicts.setdefault(leaf, list(reg.leaf_index[leaf])).append(canon)
            reg.leaf_index.setdefault(leaf, []).append(canon)
            reg.profiles[canon] = _build_profile(dir_path, wav)
            register_child(parent_canon, leaf, "voice")
            try:
                visible = [p for p in dir_path.iterdir() if p.is_dir() and not _is_dot_dir(p)]
            except OSError:
                visible = []
            for sub in visible:
                if _has_voice_like_content(sub):
                    logger.warning(
                        f"{canon!r} is a voice but contains voice-like subdir "
                        f"{sub.name!r} — unreachable (voices and groups are "
                        f"disjoint by design). Move {sub.name!r} out of "
                        f"{canon!r} or remove {canon!r}'s top-level wavs."
                    )
            return  # do NOT descend into a voice dir

        try:
            children = sorted(p for p in dir_path.iterdir() if p.is_dir() and not _is_dot_dir(p))
        except OSError:
            return
        if not children:
            bin_hint = _sample_bin_hint(dir_path)
            if bin_hint is not None:
                canon = _canonical_id(dir_path)
                leaf = dir_path.name
                reg.sample_bins[canon] = bin_hint
                reg.sample_bin_leaf_index.setdefault(leaf, []).append(canon)
                register_child(parent_canon, leaf, "sample_bin")
            return

        this_canon = _canonical_id(dir_path)
        reg.group_default[this_canon] = _read_group_default(dir_path)
        reg.group_leaf_index.setdefault(dir_path.name, []).append(this_canon)
        register_child(parent_canon, dir_path.name, "group")
        for child in children:
            walk(child, this_canon)

    try:
        tops = sorted(p for p in VOICES_DIR.iterdir() if p.is_dir() and not _is_dot_dir(p))
    except OSError:
        return reg
    for top in tops:
        walk(top, "")

    for leaf, canons in conflicts.items():
        logger.warning(
            f"Voice leaf {leaf!r} is ambiguous — appears at {canons}. Bare "
            f"{leaf!r} raises AmbiguousLeaf at resolve time; use a qualified "
            f"form ({canons[0]!r}, ...) to address one directly."
        )
    if reg.profiles:
        logger.info(f"Loaded {len(reg.profiles)} voice profiles from {VOICES_DIR}: "
                    f"{sorted(reg.profiles.keys())}")
    return reg


def load_profiles() -> Dict[str, VoiceProfile]:
    """(Re)read the settings and the voices dir: {canonical id: profile}."""
    global _registry, _loaded
    _configure()
    _registry = _load_dir_profiles()
    _loaded = True
    return _registry.profiles


def _translate_path(local_path: Path) -> str:
    """The clip's path as the TTS server sees it (``VOICEMODE_REMOTE_VOICES_DIR``)."""
    abs_local = local_path.resolve() if local_path.exists() else local_path
    if not REMOTE_VOICES_DIR:
        return str(abs_local)
    try:
        rel = abs_local.relative_to(VOICES_DIR.resolve())
    except ValueError:
        return str(abs_local)  # not under the voices dir: untranslated
    return str(Path(REMOTE_VOICES_DIR) / rel)


# -- resolve_voice ------------------------------------------------------------------

def _split_index_suffix(expr: str) -> Tuple[str, Optional[int]]:
    """Split a trailing ``[N]`` off the expression's FINAL path segment."""
    if "/" in expr:
        head, _, tail = expr.rpartition("/")
        m = _INDEX_RE.match(tail)
        if m:
            return f"{head}/{m.group(1)}", int(m.group(2))
        return expr, None
    m = _INDEX_RE.match(expr)
    if m:
        return m.group(1), int(m.group(2))
    return expr, None


def _undeclared_group_severity() -> str:
    val = setting(UNDECLARED_GROUP_SEVERITY_ENV).strip().lower() or "error"
    return val if val in ("error", "warn") else "error"


def _finalize_voice(expr: str, canon: str, via: str, index: Optional[int]) -> VoiceResolution:
    profile = _registry.profiles[canon]
    if index is None:
        return VoiceResolution(requested=expr, resolved=canon, kind="clone", via=via, profile=profile)
    voice_dir = Path(profile.voice_dir) if profile.voice_dir else VOICES_DIR / canon
    samples = sorted(voice_dir.glob("*.wav"))
    if index < 0 or index >= len(samples):
        raise IndexOutOfRange(expr, canon, index, len(samples))
    wav = samples[index]
    indexed = replace(profile, ref_audio=_translate_path(wav), ref_text=_resolve_transcript(wav))
    return VoiceResolution(requested=expr, resolved=canon, kind="clone",
                           via=f"{via}+index[{index}]", profile=indexed)


def _finalize_file_selector(expr: str, canon: str, filename: str) -> VoiceResolution:
    profile = _registry.profiles[canon]
    voice_dir = Path(profile.voice_dir) if profile.voice_dir else VOICES_DIR / canon
    wav = voice_dir / filename
    if not wav.exists():
        # The file may exist on a remote TTS host's mirror only: send it anyway, loudly.
        logger.warning(f"Voice expr {expr!r}: {wav} does not exist locally — "
                       f"sending path to server anyway in case it's mirrored.")
    new_profile = replace(profile, ref_audio=_translate_path(wav),
                          ref_text=_resolve_transcript(wav) if wav.exists() else "")
    return VoiceResolution(requested=expr, resolved=canon, kind="clone", via="file-selector", profile=new_profile)


def _resolve_cast(expr: str, group_canon: str) -> VoiceResolution:
    """Follow a cast's default chain, at most 5 hops (a symlink loop is the only cycle)."""
    chain_names: List[str] = []
    current = group_canon
    for _ in range(6):  # 5 real hops + 1 to detect overflow
        members = sorted(_registry.group_children.get(current, {}).keys())
        default_leaf = _registry.group_default.get(current)
        warn_mode_used = False

        if not default_leaf:
            if _undeclared_group_severity() == "warn" and members:
                default_leaf = members[0]
                warn_mode_used = True
                logger.warning(
                    f"{current!r} is a group of {len(members)} voice(s) with no "
                    f"declared default ({UNDECLARED_GROUP_SEVERITY_ENV}=warn) — "
                    f"resolving to {default_leaf!r} arbitrarily. Declare a real "
                    f"default (`default:` in {current}/voice.md) before relying on this."
                )
            else:
                raise NotACast(expr, current, members)

        child_kind = _registry.group_children.get(current, {}).get(default_leaf)
        if child_kind is None:
            raise CastDefaultMissing(expr, current, default_leaf, members)

        chain_names.append(default_leaf)
        child_canon = f"{current}/{default_leaf}" if current else default_leaf

        if child_kind == "voice":
            via = "cast-default:" + "→".join(chain_names)
            if warn_mode_used:
                via += "(undeclared,warn-mode)"
            return _finalize_voice(expr, child_canon, via=via, index=None)

        current = child_canon

    raise Unresolvable(expr, f"cast default chain exceeds max depth (5): {' -> '.join(chain_names)}")


def _sample_bin_error(expr: str, canon: str) -> Unresolvable:
    hint = _registry.sample_bins[canon]
    return Unresolvable(
        expr,
        f"{canon!r} is a sample bin (multiple WAVs, no default.wav) — add a "
        f"default.wav symlink to register it, e.g. `ln -s {hint} default.wav`",
    )


def _resolve_segment_walk(expr: str, body: str, index: Optional[int]) -> VoiceResolution:
    segments = body.split("/")
    cur_group = ""
    for i, seg in enumerate(segments):
        if not seg:
            raise BadSelector(expr, "empty path segment (double slash?)")
        remaining = segments[i + 1:]
        child_kind = _registry.group_children.get(cur_group, {}).get(seg)
        if child_kind is None:
            existing = sorted(_registry.group_children.get(cur_group, {}).keys())
            where = cur_group or "<voices root>"
            raise Unresolvable(
                expr, f"no {seg!r} under {where} — it has: {', '.join(existing) if existing else '(nothing)'}")
        child_canon = f"{cur_group}/{seg}" if cur_group else seg

        if child_kind == "sample_bin":
            raise _sample_bin_error(expr, child_canon)

        if child_kind == "voice":
            if not remaining:
                return _finalize_voice(expr, child_canon, via="qualified" if "/" in body else "leaf", index=index)
            if len(remaining) > 1:
                raise BadSelector(
                    expr,
                    f"{child_canon!r} is a voice; only one file-selector segment "
                    f"is allowed after it, got {len(remaining)}: {'/'.join(remaining)}",
                )
            if index is not None:
                raise BadSelector(
                    expr,
                    f"{child_canon!r}/{remaining[0]} combines a file selector with "
                    f"an index — pick one addressing form",
                )
            return _finalize_file_selector(expr, child_canon, remaining[0])

        # a group
        if not remaining:
            if index is not None:
                raise BadSelector(expr, f"{child_canon!r} is a group; an index selector needs a resolved voice")
            return _resolve_cast(expr, child_canon)
        cur_group = child_canon

    raise Unresolvable(expr, "empty expression")  # pragma: no cover - defensive


def _nearest_matches(name: str, limit: int = 5) -> List[str]:
    candidates = sorted(set(_registry.leaf_index) | set(_registry.group_leaf_index))
    return difflib.get_close_matches(name, candidates, n=limit)


def _is_provider_native(name: str) -> bool:
    return name in OPENAI_NATIVE_VOICES or name in KNOWN_KOKORO_VOICES


def _resolve_bare(expr: str, name: str, index: Optional[int]) -> VoiceResolution:
    voice_candidates = _registry.leaf_index.get(name, [])
    if len(voice_candidates) == 1:
        return _finalize_voice(expr, voice_candidates[0], via="leaf", index=index)
    if len(voice_candidates) > 1:
        raise AmbiguousLeaf(expr, name, sorted(voice_candidates))

    group_candidates = _registry.group_leaf_index.get(name, [])
    if len(group_candidates) == 1:
        if index is not None:
            raise BadSelector(expr, f"{name!r} is a group; an index selector needs a resolved voice")
        return _resolve_cast(expr, group_candidates[0])
    if len(group_candidates) > 1:
        raise AmbiguousLeaf(expr, name, sorted(group_candidates))

    bin_candidates = _registry.sample_bin_leaf_index.get(name, [])
    if bin_candidates:
        raise _sample_bin_error(expr, bin_candidates[0])

    if index is None and _is_provider_native(name):
        return VoiceResolution(requested=expr, resolved=name, kind="provider", via="provider-native", profile=None)

    raise Unresolvable(expr, f"no voice, group, or known provider voice named {name!r}", _nearest_matches(name))


def _resolve(expr: str) -> VoiceResolution:
    if not expr:
        raise Unresolvable(expr, "empty voice expression")

    if expr.startswith(("/", "./", "../", "~")):
        selector = expr if expr.startswith("/") else os.path.abspath(os.path.expanduser(expr))
        clip_path = Path(selector)
        profile = VoiceProfile(
            name=expr,
            ref_audio=selector,
            ref_text=_resolve_transcript(clip_path) if clip_path.exists() else "",
            model=DEFAULT_CLONE_MODEL,
            base_url=DEFAULT_CLONE_BASE_URL,
            description="(absolute path)" if expr.startswith("/") else "(relative path)",
        )
        return VoiceResolution(requested=expr, resolved=selector, kind="clip", via="path-escape", profile=profile)

    body, index = _split_index_suffix(expr)
    if not body:
        raise Unresolvable(expr, "empty voice name")
    if "/" in body:
        return _resolve_segment_walk(expr, body, index)
    return _resolve_bare(expr, body, index)


def resolve_voice(expr: str) -> VoiceResolution:
    """Resolve a voice expression to a :class:`VoiceResolution`, or raise.

    Precedence: a path (``/``, ``./``, ``../``, ``~``); a trailing ``[N]``; a
    ``/``-qualified walk (voice, group's voice, or cast); a bare name (a voice
    leaf, then a group leaf); a provider-native name, checked LAST; else
    :class:`Unresolvable` with the nearest names.

    The voices are read once and kept. When an expression fails against a list
    read earlier, the voices dir is read again and the expression tried once
    more: a clone folder made after the mouth started speaks
    on its first line, not after a restart.
    """
    if not _loaded:
        load_profiles()
        return _resolve(expr)
    try:
        return _resolve(expr)
    except VoiceResolutionError:
        load_profiles()
        return _resolve(expr)
