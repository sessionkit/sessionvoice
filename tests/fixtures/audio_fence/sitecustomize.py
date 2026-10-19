"""The audio fence in every child Python a test starts (EARS-320).

``tests/audio_fence.py`` puts this directory FIRST on the PYTHONPATH of every
subprocess the pytest process spawns, including the ones whose test replaced
PYTHONPATH or the whole env. ``site`` imports ``sitecustomize`` at startup, so the
child gets the same import hook as the pytest process (``_audio_fence.py``): a keeper,
a sidecar or a detached ``mouth serve`` cannot load the real sounddevice either.

Another ``sitecustomize`` further down the path (a venv's or a test's own) still runs:
it is looked up past this directory and executed after the fence is in.
"""
import os as _os
import sys as _sys

import _audio_fence

_audio_fence.install()


def _chain() -> None:
    here = _os.path.dirname(_os.path.realpath(__file__))
    rest = [p for p in _sys.path if _os.path.realpath(p or _os.curdir) != here]
    import _frozen_importlib as _bootstrap  # both loaded at every startup: no new import
    import _frozen_importlib_external as _external

    spec = _external.PathFinder.find_spec("sitecustomize", rest)
    if spec is None or spec.loader is None:
        return
    mod = _bootstrap.module_from_spec(spec)
    _sys.modules["sitecustomize"] = mod
    spec.loader.exec_module(mod)


_chain()
