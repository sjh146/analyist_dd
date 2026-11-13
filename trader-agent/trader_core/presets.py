"""Named config presets — the safe starting points a new operator can run.

A clone of this repo carries the *operating* defaults of the original account
(100 % of equity deployable, 1/3 of equity per name, 3 trades a day).  Those
numbers were tuned for a live account with a specific risk appetite; handing
them to a new operator unchanged is how a first test becomes a 33 % single-name
position.  Presets are plain JSON field mappings, layered by the CLI as

    defaults  <  preset  <  --config file  <  TRADER_CORE_*/RUNNER_* env  <  CLI flags

so an operator can start from ``paper_safe`` and still override one field.
Adding a preset is just dropping ``<name>.json`` into ``presets/``.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def preset_dir() -> str:
    """Directory holding preset JSON files (``TRADER_PRESET_DIR`` overrides)."""
    override = os.environ.get("TRADER_PRESET_DIR", "").strip()
    if override:
        return override
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), "presets")


def list_presets() -> List[str]:
    """Return available preset names (without the ``.json`` suffix)."""
    try:
        names = [entry[:-5] for entry in os.listdir(preset_dir()) if entry.endswith(".json")]
    except OSError:
        return []
    return sorted(names)


def load_preset(name: str) -> Dict[str, Any]:
    """Load ``presets/<name>.json`` as a field mapping.

    Raises ``ValueError`` for an invalid name (path traversal is rejected) and
    ``FileNotFoundError`` — listing what *is* available — for an unknown one.
    """
    target = str(name or "").strip()
    if not _NAME_RE.match(target):
        raise ValueError("invalid preset name: {0!r} (allowed: letters, digits, _ and -)".format(name))
    path = os.path.join(preset_dir(), target + ".json")
    if not os.path.isfile(path):
        available = ", ".join(list_presets()) or "(none)"
        raise FileNotFoundError("preset '{0}' not found in {1}; available: {2}".format(
            target, preset_dir(), available))
    with open(path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError("preset '{0}' must contain a JSON object".format(target))
    return {str(key).strip(): value for key, value in raw.items()}
