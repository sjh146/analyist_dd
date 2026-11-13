"""Config must be settable, not just readable.

``_FIELD_NAMES`` (the whitelist behind ``from_env`` / ``from_file``) is derived
from ``Config.as_dict()``.  When ``as_dict`` listed only a subset of the fields,
every knob outside that list — notably the exposure limits ``max_invested_pct``,
``max_open_positions``, ``max_open_positions_by_screener`` and the trailing
pair — was silently ignored by ``--config`` and ``TRADER_CORE_*`` env vars.
That made the risk limits unportable: a new operator could not lower them.
"""

import dataclasses
import json
import os
import tempfile
import unittest

from trader_core.config import Config


def _write(doc):
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(doc, handle)
    handle.close()
    return handle.name


class ConfigPortabilityTest(unittest.TestCase):
    def test_as_dict_covers_every_field(self):
        cfg = Config()
        expected = {item.name for item in dataclasses.fields(cfg)}
        self.assertEqual(expected, set(cfg.as_dict().keys()))

    def test_risk_limits_from_env(self):
        cfg = Config.from_env({
            "TRADER_CORE_MAX_INVESTED_PCT": "0.30",
            "TRADER_CORE_MAX_OPEN_POSITIONS": "2",
            "TRADER_CORE_SIZING_MODE": "kelly",
            "TRADER_CORE_TRAILING_STOP_PCT": "0.04",
            "TRADER_CORE_MAX_OPEN_POSITIONS_BY_SCREENER": '{"swing": 1, "daytrading": 1}',
        })
        self.assertAlmostEqual(cfg.max_invested_pct, 0.30)
        self.assertEqual(cfg.max_open_positions, 2)
        self.assertEqual(cfg.sizing_mode, "kelly")
        self.assertAlmostEqual(cfg.trailing_stop_pct, 0.04)
        self.assertEqual(cfg.max_open_positions_by_screener, {"swing": 1, "daytrading": 1})

    def test_risk_limits_from_file(self):
        path = _write({
            "max_invested_pct": 0.3,
            "max_position_pct": 0.1,
            "max_open_positions": 3,
            "max_open_positions_by_screener": {"swing": 2, "daytrading": 1},
            "daily_max_trades": 2,
            "dry_run": True,
        })
        try:
            cfg = Config.from_file(path)
        finally:
            os.unlink(path)
        self.assertAlmostEqual(cfg.max_invested_pct, 0.3)
        self.assertAlmostEqual(cfg.max_position_pct, 0.1)
        self.assertEqual(cfg.max_open_positions, 3)
        self.assertEqual(cfg.max_open_positions_by_screener, {"swing": 2, "daytrading": 1})
        self.assertEqual(cfg.daily_max_trades, 2)
        self.assertTrue(cfg.dry_run)

    def test_mapping_fields_survive_round_trip(self):
        cfg = Config().with_overrides(max_open_positions_by_screener={"swing": 1})
        self.assertIsInstance(cfg.max_open_positions_by_screener, dict)
        self.assertEqual(cfg.max_open_positions_by_screener, {"swing": 1})
        # A string that is not JSON must not corrupt the field.
        cfg2 = Config().with_overrides(max_open_positions_by_screener="not-json")
        self.assertIsInstance(cfg2.max_open_positions_by_screener, dict)

    def test_as_dict_copies_mutable_fields(self):
        cfg = Config()
        doc = cfg.as_dict()
        doc["max_open_positions_by_screener"]["swing"] = 99
        doc["screener_names"].append("mutated")
        self.assertNotEqual(cfg.max_open_positions_by_screener.get("swing"), 99)
        self.assertNotIn("mutated", cfg.screener_names)


if __name__ == "__main__":
    unittest.main()
