#!/usr/bin/env python3
"""
run_e2e.py — Run E2E tests with the broken ROS2 pytest plugin blocked.

The system-wide launch_testing / launch_ros plugins crash pytest at entrypoint
load time because they declare hooks incompatible with this pytest version.
This wrapper patches both importlib.metadata AND the PluginManager to skip them.

Usage:
    python3 tests/run_e2e.py                # run all E2E tests
    python3 tests/run_e2e.py -k scenario_1  # run specific test
"""
import sys
import importlib.metadata


def _is_broken_ros2_ep(ep):
    """Check if an entry point is from the broken ROS2 launch_testing ecosystem."""
    return (
        ep.group == "pytest11"
        and (
            "launch_testing" in ep.name
            or "launch_ros" in ep.name
            or (hasattr(ep, "value") and ep.value.startswith("launch_testing"))
        )
    )


# ── Patch 1: importlib.metadata.entry_points (filter at source) ────────────
_orig_entry_points = importlib.metadata.entry_points


def _patched_entry_points(**kwargs):
    eps = _orig_entry_points(**kwargs)
    if hasattr(eps, "get"):
        # SelectableGroups dict interface
        return {
            group: [ep for ep in entries if not _is_broken_ros2_ep(ep)]
            for group, entries in eps.items()
        }
    else:
        # list interface
        return [ep for ep in eps if not _is_broken_ros2_ep(ep)]


importlib.metadata.entry_points = _patched_entry_points

# ── Patch 2: PytestPluginManager.load_setuptools_entrypoints (defense in depth)
# Import _pytest.config to access the class, but DON'T import pytest yet.
import _pytest.config

_orig_load = _pytest.config.PytestPluginManager.load_setuptools_entrypoints


def _patched_load(self, group):
    """Re-implement load_setuptools_entrypoints, skipping broken ROS2 plugins."""
    eps = importlib.metadata.entry_points(group=group)
    for ep in eps:
        if _is_broken_ros2_ep(ep):
            continue
        try:
            plugin = ep.load()
        except Exception:
            continue
        try:
            self.register(plugin, name=ep.name)
        except Exception:
            continue


# Patch the CLASS method so all future instances use it.
_pytest.config.PytestPluginManager.load_setuptools_entrypoints = _patched_load

# ── Now run pytest ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    args = sys.argv[1:] if len(sys.argv) > 1 else [
        "tests/test_e2e_pipeline.py", "-v", "--tb=short",
    ]
    import pytest
    sys.exit(pytest.main(args))
