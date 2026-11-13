"""Presets must be loadable, safe by construction, and reject bad names.

These pin the properties a new operator relies on: the safe preset never
exceeds 30 % total exposure or 10 % per name, never ships live-trading on, and
a typo in ``--preset`` fails loudly instead of silently running defaults.
"""

import json
import os
import tempfile
import unittest

from trader_core import presets


class PresetTest(unittest.TestCase):
    def test_bundled_presets_are_listed(self):
        names = presets.list_presets()
        self.assertIn("paper_safe", names)
        self.assertIn("rank_only", names)

    def test_paper_safe_is_conservative(self):
        doc = presets.load_preset("paper_safe")
        self.assertLessEqual(float(doc["max_invested_pct"]), 0.30)
        self.assertLessEqual(float(doc["max_position_pct"]), 0.10)
        self.assertLessEqual(float(doc["daily_max_trades"]), 5)
        self.assertTrue(doc["dry_run"])
        # per-screener slots cannot exceed the global slot count
        self.assertLessEqual(sum(doc["max_open_positions_by_screener"].values()),
                             int(doc["max_open_positions"]))

    def test_rank_only_needs_no_feed(self):
        doc = presets.load_preset("rank_only")
        self.assertEqual(doc["feed_source"], "rank")
        self.assertLessEqual(float(doc["max_invested_pct"]), 0.30)

    def test_unknown_preset_lists_alternatives(self):
        with self.assertRaises(FileNotFoundError) as ctx:
            presets.load_preset("does_not_exist")
        self.assertIn("paper_safe", str(ctx.exception))

    def test_path_traversal_is_rejected(self):
        for bad in ("../etc/passwd", "a/b", "", "x y"):
            with self.assertRaises(ValueError):
                presets.load_preset(bad)

    def test_custom_preset_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "custom.json"), "w", encoding="utf-8") as handle:
                json.dump({"dry_run": True, "daily_max_trades": 1}, handle)
            previous = os.environ.get("TRADER_PRESET_DIR")
            os.environ["TRADER_PRESET_DIR"] = tmp
            try:
                self.assertEqual(presets.list_presets(), ["custom"])
                self.assertEqual(presets.load_preset("custom")["daily_max_trades"], 1)
            finally:
                if previous is None:
                    os.environ.pop("TRADER_PRESET_DIR", None)
                else:
                    os.environ["TRADER_PRESET_DIR"] = previous


if __name__ == "__main__":
    unittest.main()
