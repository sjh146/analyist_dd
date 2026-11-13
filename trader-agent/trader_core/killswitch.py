"""Kill-switch file existence guard.

Presence of the configured file means the engine must refuse new orders and
emit a liquidation plan.  A kill switch may optionally carry a short reason on
its first line which is surfaced for logs/reports.
"""

from __future__ import annotations

import os
from typing import Optional


def kill_switch_active(path: str) -> bool:
    """Return True when the kill-switch *path* exists on disk.

    Fail-closed: a path that cannot be stat'ed (e.g. ``None``/empty) is
    treated as active so the engine never trades without an explicit clear.
    """
    if not path:
        return True
    try:
        return os.path.exists(path)
    except (OSError, ValueError):
        return True


def kill_switch_reason(path: str) -> str:
    """Return the first non-empty line of the kill-switch file as a reason."""
    try:
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    stripped = line.strip()
                    if stripped:
                        return stripped
    except OSError:
        pass
    return "kill switch file present"
