"""Config layering + killswitch unit tests."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from trader_core import Config, kill_switch_active, kill_switch_reason


class ConfigTest(unittest.TestCase):
    def test_defaults_match_brief(self) -> None:
        cfg = Config()
        self.assertEqual(cfg.bridge_base_url, "http://127.0.0.1:8100")
        self.assertAlmostEqual(cfg.stop_loss_pct, 0.07)
        self.assertAlmostEqual(cfg.take_profit_pct, 0.07)
        self.assertAlmostEqual(cfg.kelly_cap, 0.25)
        self.assertAlmostEqual(cfg.feed_max_age, 24 * 3600)
        self.assertAlmostEqual(cfg.market_order_band_pct, 0.10)
        self.assertEqual(cfg.rank_pct_to, 15)  # matched to the chase ceiling
        self.assertEqual(cfg.market_open, "09:00")
        self.assertEqual(cfg.market_close, "15:30")
        self.assertTrue(cfg.market_hours_only)
        self.assertTrue(cfg.dry_run)
        self.assertEqual(
            cfg.screener_names, ["close", "swing", "daytrading"]
        )
        self.assertIn("journal", cfg.journal_path)

    def test_env_overrides(self) -> None:
        env = {
            "TRADER_CORE_FEED_MAX_AGE": "3600",
            "TRADER_CORE_DRY_RUN": "false",
            "TRADER_CORE_KILL_SWITCH_PATH": "/tmp/ks.txt",
            "TRADER_CORE_SCREENER_NAMES": "close,swing",
            "TRADER_CORE_KELLY_CAP": "0.5",
        }
        cfg = Config.from_env(env)
        self.assertEqual(cfg.feed_max_age, 3600.0)
        self.assertFalse(cfg.dry_run)
        self.assertEqual(cfg.kill_switch_path, "/tmp/ks.txt")
        self.assertEqual(cfg.screener_names, ["close", "swing"])
        self.assertAlmostEqual(cfg.kelly_cap, 0.5)
        # unrelated env vars are ignored
        self.assertEqual(Config.from_env({"PATH": "/bin"}).feed_max_age,
                         24 * 3600)

    def test_file_overrides(self) -> None:
        tmp = tempfile.mkdtemp(prefix="trader-core-cfg-")
        try:
            path = os.path.join(tmp, "config.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"dry_run": False, "daily_max_trades": 3,
                           "feed_url": "http://example.test/feed.json"}, handle)
            cfg = Config.from_file(path)
            self.assertFalse(cfg.dry_run)
            self.assertEqual(cfg.daily_max_trades, 3)
            self.assertEqual(cfg.feed_url, "http://example.test/feed.json")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_with_overrides_returns_copy(self) -> None:
        base = Config()
        changed = base.with_overrides(market_hours_only=False)
        self.assertTrue(base.market_hours_only)
        self.assertFalse(changed.market_hours_only)


class KillSwitchTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="trader-core-ks-")
        self.path = os.path.join(self._tmp, "kill_switch.txt")

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_file_presence_toggles(self) -> None:
        self.assertFalse(kill_switch_active(self.path))
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("manual halt")
        self.assertTrue(kill_switch_active(self.path))
        self.assertEqual(kill_switch_reason(self.path), "manual halt")
        os.remove(self.path)
        self.assertFalse(kill_switch_active(self.path))

    def test_empty_path_fails_closed(self) -> None:
        self.assertTrue(kill_switch_active(""))
        self.assertTrue(kill_switch_active(None))

    def test_missing_reason_defaults(self) -> None:
        self.assertEqual(kill_switch_reason(self.path),
                         "kill switch file present")


if __name__ == "__main__":
    unittest.main()
