"""run.py - split one song into its four stems and its instrumental, in the separator venv.

``mouth split`` runs this script with the separator venv's python, never the mouth's own.
It is shipped as package data (``mouth/_separator/run.py``) and imports nothing from the
mouth: the venv holds only what ``requirements.lock`` pins. ``$MOUTH_SEPARATOR`` may name
another command in its place; that command must speak the same protocol.

**The protocol.**

    python run.py --out DIR --model FILE --sha256 HEX [--threads N]

- **stdin:** the song as raw PCM, signed 16-bit little-endian, 44100 Hz, 2 channels
  interleaved (``ffmpeg ... -f s16le -ar 44100 -ac 2 -``). Read to the end. A trailing
  partial frame is dropped.
- **DIR:** an empty folder the caller made, normally ``.<name>.d/.split-<pid>/``. The
  runner writes, as FLAC, 16-bit, 44100 Hz stereo::

      DIR/stems/vocals.flac
      DIR/stems/drums.flac
      DIR/stems/bass.flac
      DIR/stems/other.flac
      DIR/mixes/instrumental.flac      drums + bass + other

  The caller renames ``stems/`` and ``mixes/`` into place and writes the manifest.
- **FILE:** the model checkpoint, by path. ``*.safetensors`` is loaded with
  ``demucs.hf.load_safetensors_model``; ``*.th`` with ``demucs.pretrained.get_model`` over
  the file's own folder as a local repo. Its SHA-256 must equal HEX, in full, or nothing
  is loaded: both loaders run code that the file names, so only the pin makes it safe.
  The runner never fetches anything.
- **stdout:** ONE JSON line. On success ``ok: true`` with ``separator``, ``signature``,
  ``versions`` (demucs, torch), ``device``, ``threads``, ``rate``, ``channels``,
  ``duration_s``, ``split_s``, ``outputs`` (each output's path under DIR), ``levels_db``
  (``mix``, ``vocals``, ``instrumental``) and ``peak_scaled_db``. On failure ``ok: false``
  and ``detail``.
- **Exit:** 0 written; 2 refused before any model was loaded (bad arguments, no model
  file, a checksum that does not match, no audio on stdin); 1 the separation failed
  (loading, separating or writing).

**The outputs.** The instrumental is the sum of the drums, bass and other stems, made
here, so the stems always add up to it. If any of the five would clip in 16 bits, all five
are scaled down by ONE common gain, recorded in dB as ``peak_scaled_db`` (null when none
was needed): a gain per output would break that sum, and the balance of a mix of stems.
``levels_db`` is measured BEFORE that scaling, so the caller's speech check (an
instrumental far below the mix) reads the true ratio. A level is the RMS over both
channels, in dB full scale; digital silence reads :data:`LEVEL_FLOOR_DB`.

It runs on the CPU, with torch's threads set to ``--threads`` (default: the core count
minus two, at least one). It never runs the ``demucs`` command, never uses ``demucs.api``'s
file I/O, and never imports torchaudio. torch and demucs are imported only when a model is
loaded, so the separate-and-write logic runs, and is tested, with numpy alone.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path

import numpy as np

RATE = 44100
CHANNELS = 2
SAMPLE_BYTES = 2                      # s16le
STEMS = ("vocals", "drums", "bass", "other")
INSTRUMENTAL_PARTS = ("drums", "bass", "other")
OUTPUTS = {**{s: f"stems/{s}.flac" for s in STEMS}, "instrumental": "mixes/instrumental.flac"}
CLIP_LIMIT = 32767 / 32768            # the largest value 16-bit PCM holds
LEVEL_FLOOR_DB = -120.0               # the level of digital silence
SEPARATOR = "demucs"
EXIT_FAILED, EXIT_REFUSED = 1, 2


class Refused(Exception):
    """Refused before any model was loaded: exit 2."""


class Failed(Exception):
    """The separation failed: exit 1."""


# ---- the separate-and-write logic: numpy only ------------------------------------------

def read_pcm(stream) -> np.ndarray:
    """STREAM's s16le, 2-channel PCM as float32, shape (channels, samples), in [-1, 1)."""
    raw = stream.read()
    frame = SAMPLE_BYTES * CHANNELS
    raw = raw[: len(raw) - len(raw) % frame]
    pcm = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    return pcm.reshape(-1, CHANNELS).T.copy()


def level_db(x: np.ndarray) -> float:
    """The RMS of X over every channel, in dB full scale; never below LEVEL_FLOOR_DB."""
    if x.size == 0:
        return LEVEL_FLOOR_DB
    rms = float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))
    if rms <= 0.0:
        return LEVEL_FLOOR_DB
    return max(LEVEL_FLOOR_DB, round(20.0 * math.log10(rms), 2))


def separate(mix: np.ndarray, model) -> dict[str, np.ndarray]:
    """MODEL's four sources of MIX (channels, samples), by name.

    MODEL has ``sources`` (its output order) and ``apply(x)``, which takes the normalised
    mix (channels, samples) and returns (sources, channels, samples). The mix is normalised
    as Demucs's own separate does: the mono reference's mean taken off and its standard
    deviation divided out, then put back on every source.

    The sources are four times the mix, so they are put back IN PLACE, in the model's own
    array, and each stem is a view into it: a 20-minute song's sources are about 1.7 GB,
    and ``out * std + mean`` held three of them at once. The normalised mix is dropped as
    soon as the model has run."""
    ref = mix.mean(axis=0)
    mean, std = float(ref.mean()), float(ref.std()) + 1e-8
    del ref
    norm = mix - mean
    norm /= std
    out = model.apply(norm)
    del norm                                  # spent: the model has its sources
    out = np.asarray(out, dtype=np.float32)   # no copy when the model gave float32
    if not out.flags.writeable or 0 in out.strides:
        out = out.copy()                      # read-only, or a broadcast: not ours to scale
    sources = list(model.sources)
    if out.shape != (len(sources), *mix.shape):
        raise Failed(f"the model returned shape {out.shape}, not {(len(sources), *mix.shape)}")
    missing = [s for s in STEMS if s not in sources]
    if missing:
        raise Failed(f"the model has no {', '.join(missing)} source (it has {', '.join(sources)})")
    out *= std                                # in place: no second or third copy of the
    out += mean                               # sources, which are 4x the mix in size
    return {name: out[sources.index(name)] for name in STEMS}


def outputs_of(stems: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """The five outputs: the four stems and their instrumental, drums + bass + other.

    The instrumental is ONE new array, summed into in place in that order, so no
    temporaries of a stem's size are made beside it."""
    first, *rest = INSTRUMENTAL_PARTS
    inst = np.array(stems[first], dtype=np.float32)
    for part in rest:
        inst += stems[part]
    return {**{s: stems[s] for s in STEMS}, "instrumental": inst}


def clip_gain(outputs: dict[str, np.ndarray]) -> float:
    """The ONE gain that keeps every output within 16 bits: 1.0 when none would clip."""
    peak = max((float(np.max(np.abs(a))) if a.size else 0.0) for a in outputs.values())
    return CLIP_LIMIT / peak if peak > CLIP_LIMIT else 1.0


def write_flac(path: Path, data: np.ndarray, rate: int) -> None:
    """DATA (channels, samples) as 16-bit FLAC at PATH. soundfile is the venv's."""
    import soundfile
    soundfile.write(str(path), data.T, rate, subtype="PCM_16", format="FLAC")


def separate_and_write(mix: np.ndarray, model, out_dir, writer=write_flac,
                       rate: int = RATE) -> dict:
    """Separate MIX with MODEL and write the five outputs under OUT_DIR with WRITER.

    The levels are taken before the clip scaling; the scaling is one gain for all five.
    Returns the report's ``outputs``, ``levels_db`` and ``peak_scaled_db``."""
    out_dir = Path(out_dir)
    outputs = outputs_of(separate(mix, model))
    levels = {"mix": level_db(mix), "vocals": level_db(outputs["vocals"]),
              "instrumental": level_db(outputs["instrumental"])}
    gain = clip_gain(outputs)
    for name, rel in OUTPUTS.items():
        path = out_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        data = outputs[name]
        if gain != 1.0:
            data *= np.float32(gain)          # in place: the levels are already taken
        writer(path, data, rate)
    return {"outputs": dict(OUTPUTS), "levels_db": levels,
            "peak_scaled_db": None if gain == 1.0 else round(20.0 * math.log10(gain), 2)}


# ---- the model: torch and demucs, imported only here -------------------------------------

def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def check_model(path: Path, sha256: str) -> None:
    """Refuse a model file that is missing, of an unknown kind, or not the pinned bytes."""
    if path.suffix not in (".safetensors", ".th"):
        raise Refused(f"{path.name}: a model is a .safetensors or a .th file")
    if not path.is_file():
        raise Refused(f"no model file at {path}")
    got = sha256_of(path)
    if got != sha256.lower():
        raise Refused(f"{path.name}: sha256 {got} is not the pinned {sha256.lower()}")


class DemucsModel:
    """A demucs model behind :func:`separate`'s interface, on the CPU."""

    def __init__(self, model, torch):
        self.model, self.torch = model, torch
        self.sources = list(model.sources)
        self.samplerate, self.audio_channels = model.samplerate, model.audio_channels

    def apply(self, x: np.ndarray) -> np.ndarray:
        from demucs.apply import apply_model
        with self.torch.no_grad():
            out = apply_model(self.model, self.torch.from_numpy(np.ascontiguousarray(x))[None],
                              device="cpu", progress=False)
        return out[0].numpy()


def load_demucs(path: Path, threads: int):
    """The model in PATH (already checked), and the demucs and torch versions."""
    import torch
    import demucs
    torch.set_num_threads(threads)
    if path.suffix == ".safetensors":
        from demucs.hf import load_safetensors_model
        model = load_safetensors_model(path)
    else:
        from demucs.pretrained import get_model
        model = get_model(path.stem.split("-")[0], repo=path.parent)
    model.eval()
    wrapped = DemucsModel(model, torch)
    if (wrapped.samplerate, wrapped.audio_channels) != (RATE, CHANNELS):
        raise Failed(f"the model is {wrapped.samplerate} Hz, {wrapped.audio_channels} channels; "
                     f"the protocol is {RATE} Hz, {CHANNELS}")
    return wrapped, {"demucs": demucs.__version__, "torch": torch.__version__}


# ---- the command --------------------------------------------------------------------------

def default_threads() -> int:
    return max(1, (os.cpu_count() or 1) - 2)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Refused(message)


def parse(argv) -> argparse.Namespace:
    p = _Parser(prog="run.py", add_help=False)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--model", required=True, type=Path)
    p.add_argument("--sha256", required=True)
    p.add_argument("--threads", type=int, default=None)
    args = p.parse_args(argv)
    if args.threads is not None and args.threads < 1:
        raise Refused("--threads must be 1 or more")
    if len(args.sha256) != 64 or any(c not in "0123456789abcdefABCDEF" for c in args.sha256):
        raise Refused("--sha256 must be the model file's full sha256, 64 hex digits")
    if not args.out.is_dir():
        raise Refused(f"no output folder at {args.out}")
    return args


def main(argv=None, stdin=None, stdout=None, loader=load_demucs, writer=write_flac) -> int:
    stdin = stdin if stdin is not None else sys.stdin.buffer
    stdout = stdout if stdout is not None else sys.stdout
    t0 = time.monotonic()
    try:
        args = parse(sys.argv[1:] if argv is None else argv)
        check_model(args.model, args.sha256)
        mix = read_pcm(stdin)
        if mix.shape[1] == 0:
            raise Refused("no audio on stdin")
        threads = args.threads or default_threads()
        try:
            model, versions = loader(args.model, threads)
            report = separate_and_write(mix, model, args.out, writer=writer)
        except (Refused, Failed):
            raise
        except Exception as exc:                      # the model, torch or the disk
            raise Failed(f"{type(exc).__name__}: {exc}") from exc
    except Refused as exc:
        print(json.dumps({"ok": False, "detail": str(exc)}), file=stdout, flush=True)
        return EXIT_REFUSED
    except Failed as exc:
        print(json.dumps({"ok": False, "detail": str(exc)}), file=stdout, flush=True)
        return EXIT_FAILED
    line = {"ok": True, "separator": SEPARATOR, "signature": args.model.stem.split("-")[0],
            "versions": versions, "device": "cpu", "threads": threads, "rate": RATE,
            "channels": CHANNELS, "duration_s": round(mix.shape[1] / RATE, 3),
            "split_s": round(time.monotonic() - t0, 3), **report}
    print(json.dumps(line), file=stdout, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
