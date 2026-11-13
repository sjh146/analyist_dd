"""JSON state file for the market-hours loop (``loop_state.json``).

The loop persists its HALT flag, daily counters (start equity / entries /
close summary) and consecutive check-failure count here so a restart or a
remote operator (CLI ``halt``/``resume``, or BC-250 over ssh) can inspect and
steer it.  Writes are atomic (temp file + ``os.replace``) and guarded by a
lock - enough for a single loop process plus occasional external reads/writes.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict, Optional

_STATE_VERSION = 1

_EMPTY_STATE: Dict[str, Any] = {
    "version": _STATE_VERSION,
    "loop": {"halt": False, "halt_reason": ""},
    "daily": {
        "date": None,
        "start_equity": None,
        # Live fills counted toward the R2 entry cap, and simulated fills of a
        # dry-run cycle kept apart so a dry-run never consumes the real daily
        # entry budget (see runner/loop.py::_bump_entries).
        "entries": 0,
        "dry_run_entries": 0,
        "closed_pnl": None,
        "closed": False,
        "summary": None,
    },
    "check": {"consecutive_failures": 0, "last_error": None},
    # Entry orders the broker had not filled when they were placed (the order
    # echo is not a fill confirmation).  Verified every cycle: a row that never
    # filled releases its concurrent-position slot in-cycle instead of being
    # repaired only by the day-close reconcile.
    "pending_entries": {},
    "last_cycle": None,
    "last_summary": None,
    "updated_at": None,
}


def default_state() -> Dict[str, Any]:
    """Return a fresh, deep-copied empty state structure."""
    return json.loads(json.dumps(_EMPTY_STATE))


class StateStore:
    """Read/write a JSON state file with atomic replace and a write lock."""

    def __init__(self, path: str) -> None:
        """Point the store at *path* (created on first write)."""
        self.path = path
        self._lock = threading.Lock()

    def exists(self) -> bool:
        """Return True when the state file exists on disk."""
        return bool(self.path) and os.path.exists(self.path)

    def read(self) -> Dict[str, Any]:
        """Read and return the state (a fresh empty state when missing).

        A corrupt/unreadable file is treated as empty (the loop recreates it);
        a missing file is *not* created here (``read`` is side-effect free).
        """
        if not self.path or not os.path.exists(self.path):
            return default_state()
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError):
            return default_state()
        if not isinstance(raw, dict):
            return default_state()
        return _merge_defaults(raw)

    def write(self, state: Dict[str, Any]) -> None:
        """Atomically persist *state* (temp file + ``os.replace``)."""
        if not self.path:
            return
        state = _merge_defaults(state)
        state["updated_at"] = _now_iso()
        parent = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(parent, exist_ok=True)
        tmp_path = "{0}.tmp.{1}".format(self.path, os.getpid())
        with self._lock:
            try:
                with open(tmp_path, "w", encoding="utf-8") as handle:
                    json.dump(state, handle, ensure_ascii=False, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp_path, self.path)
            finally:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:  # pragma: no cover - best effort cleanup
                        pass


def _merge_defaults(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Fill any missing nested keys so callers can rely on the full shape."""
    merged = default_state()
    merged.update(raw)
    for section in ("loop", "daily", "check"):
        if isinstance(raw.get(section), dict):
            merged[section].update(raw[section])
    return merged


def _now_iso() -> str:
    from datetime import datetime  # local import keeps module import cheap

    from trader_core.config import KST

    return datetime.now(KST).isoformat()
