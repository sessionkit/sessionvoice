"""The optional extras: an import one of them ships fails naming it.

A bare wheel carries the base dependencies only (pyproject.toml). Anything an extra
brings is imported late, through :func:`need`, so a missing one says which extra to
install instead of a bare ``No module named ...``:

    ort = need("onnxruntime", "silero", "Silero VAD")

:class:`MissingExtra` is an ``ImportError``: a caller that falls back when a module
is absent (``vad.open_vad("auto")``, ``detector.default_classifier``, the Smart Turn
re-decode) still falls back.
"""
from __future__ import annotations

import importlib
from types import ModuleType
from typing import Optional

DIST = "sessionvoice"


class MissingExtra(ImportError):
    """A module an optional extra installs is not here."""

    def __init__(self, module: str, extra: str, what: Optional[str] = None) -> None:
        self.module, self.extra = module, extra
        self.install = f"pip install '{DIST}[{extra}]'"
        top = module.split(".")[0]
        lead = f"{what} needs {top}" if what else f"{top} is needed"
        super().__init__(f"{lead}, which is not installed: {self.install}", name=top)


def need(module: str, extra: str, what: Optional[str] = None) -> ModuleType:
    """``import module`` or raise :class:`MissingExtra` naming ``extra``.

    Only the module itself (or its package) being absent is translated. When it is
    installed but one of ITS dependencies is missing, the original error stands: that
    is a broken install, and the extra would not fix it.
    """
    try:
        return importlib.import_module(module)
    except ModuleNotFoundError as exc:
        if (exc.name or "").split(".")[0] != module.split(".")[0]:
            raise
        raise MissingExtra(module, extra, what) from exc
