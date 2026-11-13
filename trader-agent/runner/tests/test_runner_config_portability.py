"""RunnerConfig must route every engine knob from file/env, not just a subset.

The loop resolves its config as defaults < file < env < CLI, but it merges via
``as_dict()``.  Any engine field missing from ``Config.as_dict()`` was dropped
on the floor, so a config file could not set ``max_invested_pct`` and a new
operator's exposure limits silently stayed at the aggressive defaults.
"""

import json
import os
import tempfile
import unittest

from runner.config import RunnerConfig


def _write(doc):
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(doc, handle)
    handle.close()
    return handle.name


class RunnerConfigPortabilityTest(unittest.TestCase):
    def test_engine_limits_from_env(self):
        cfg = RunnerConfig.from_env({
            "TRADER_CORE_MAX_INVESTED_PCT": "0.3",
            "TRADER_CORE_MAX_OPEN_POSITIONS": "2",
            "TRADER_CORE_MAX_POSITION_PCT": "0.1",
            "RUNNER_INTERVAL_SECONDS": "120",
        })
        self.assertAlmostEqual(cfg.engine.max_invested_pct, 0.3)
        self.assertEqual(cfg.engine.max_open_positions, 2)
        self.assertAlmostEqual(cfg.engine.max_position_pct, 0.1)
        self.assertEqual(cfg.interval_seconds, 120)

    def test_engine_limits_from_file(self):
        path = _write({
            "max_invested_pct": 0.25,
            "max_position_pct": 0.08,
            "max_open_positions_by_screener": {"swing": 1, "daytrading": 1},
            "sizing_mode": "kelly",
            "interval_seconds": 120,
            "dry_run": True,
        })
        try:
            cfg = RunnerConfig.from_file(path)
        finally:
            os.unlink(path)
        self.assertAlmostEqual(cfg.engine.max_invested_pct, 0.25)
        self.assertAlmostEqual(cfg.engine.max_position_pct, 0.08)
        self.assertEqual(cfg.engine.max_open_positions_by_screener,
                         {"swing": 1, "daytrading": 1})
        self.assertEqual(cfg.engine.sizing_mode, "kelly")
        self.assertEqual(cfg.interval_seconds, 120)

    def test_as_dict_exposes_engine_limits(self):
        doc = RunnerConfig().as_dict()
        for name in ("max_invested_pct", "max_open_positions",
                     "max_open_positions_by_screener", "sizing_mode",
                     "trailing_stop_pct", "risk_per_trade_pct"):
            self.assertIn(name, doc)

    def test_engine_config_keeps_mapping_type(self):
        path = _write({"max_open_positions_by_screener": {"swing": 2, "daytrading": 1}})
        try:
            cfg = RunnerConfig.from_file(path)
        finally:
            os.unlink(path)
        mapping = cfg.engine_config().max_open_positions_by_screener
        self.assertIsInstance(mapping, dict)
        self.assertEqual(mapping, {"swing": 2, "daytrading": 1})

    def test_runner_fields_still_win_over_engine_names(self):
        # RUNNER_ prefix must beat TRADER_CORE_ for the same field name.
        cfg = RunnerConfig.from_env({
            "TRADER_CORE_DRY_RUN": "false",
            "RUNNER_DRY_RUN": "true",
        })
        self.assertTrue(cfg.dry_run)


if __name__ == "__main__":
    unittest.main()
