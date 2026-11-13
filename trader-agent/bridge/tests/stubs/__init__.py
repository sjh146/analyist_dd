"""Test double package: a fake ``win32com`` tree injected via ``sys.modules``.

``bridge/tests/stubs/win32com/client.py`` fakes the pywin32 ``Dispatch``
entry point so the bridge's lazy ``import win32com.client`` path can be
exercised on Linux.  Every fake object records ``SetInputValue`` /
``BlockRequest`` calls for assertions.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from typing import Optional


def install_stub() -> None:
    """Inject the fake ``win32com`` package into ``sys.modules``."""
    if "win32com.client" in sys.modules:
        # Make sure ``import win32com.client`` also binds the attribute on the
        # parent package (it is not set automatically for sys.modules entries).
        parent = sys.modules.get("win32com")
        if parent is not None and not hasattr(parent, "client"):
            setattr(parent, "client", sys.modules["win32com.client"])
        return  # already installed
    root = pathlib.Path(__file__).parent / "win32com"

    pkg = _load_module("win32com", root / "__init__.py")
    pkg.__path__ = [str(root)]  # type: ignore[attr-defined]
    client_mod = _load_module("win32com.client", root / "client.py")
    setattr(pkg, "client", client_mod)


def _load_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
