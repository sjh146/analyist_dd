"""RunnerConfig layering + engine-sync unit tests."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from runner.config import RunnerConfig


class RunnerConfigTest(unittest.TestCase):
    def test_defaults_match_brief(self) -> None:
        cfg = RunnerConfig()
        self.assertEqual(cfg.interval_seconds, 300)
        self.assertEqual(cfg.market_open, "09:00")
        self.assertEqual(cfg.market_close, "15:30")
        self.assertEqual(cfg.start_before_open_min, 5)
        self.assertEqual(cfg.end_after_close_min, 10)
        self.assertEqual(cfg.screeners, ["close", "swing"])
        self.assertEqual(cfg.r1_top_n, 10)
        self.assertEqual(cfg.r1_min_avg_score, {"close": 88.0, "swing": 75.0})
        # 확률 스케일 피드(score_kind=calibrated_prob)용 문턱 + 후보별 신선도 상한
        # (2026-09-29: swing 점수가 모델 확률 0.5~0.62 라 75 점 문턱이 경로를 닫았다).
        self.assertEqual(cfg.r1_min_avg_prob, {"swing": 0.58})
        self.assertEqual(cfg.r1_min_avg_rank_pct, {})  # 백분위 문턱은 opt-in
        self.assertEqual(cfg.candidate_max_age_days, {"close": 4, "swing": 5})
        self.assertEqual(cfg.r2_max_entries_per_day, 3)
        self.assertAlmostEqual(cfg.r3_max_deviation_pct, 0.03)
        self.assertAlmostEqual(cfg.r4_loss_limit_pct, 0.03)
        self.assertEqual(cfg.max_consecutive_check_failures, 3)
        self.assertTrue(cfg.dry_run)
        self.assertEqual(cfg.state_path, "loop_state.json")
        self.assertIn("journal", cfg.journal_path)
        self.assertEqual(cfg.webhook_url, "")
        self.assertEqual(cfg.news_gate_url, "http://127.0.0.1:8082/newsgate")

    def test_env_overrides_runner_and_trader(self) -> None:
        env = {
            "RUNNER_INTERVAL_SECONDS": "60",
            "RUNNER_R2_MAX_ENTRIES_PER_DAY": "5",
            "RUNNER_SCREENERS": "close",
            "TRADER_CORE_BRIDGE_BASE_URL": "http://127.0.0.1:9999",
            "TRADER_CORE_KELLY_CAP": "0.5",
            "PATH": "/bin",
        }
        cfg = RunnerConfig.from_env(env)
        self.assertEqual(cfg.interval_seconds, 60)
        self.assertEqual(cfg.r2_max_entries_per_day, 5)
        self.assertEqual(cfg.screeners, ["close"])
        self.assertEqual(cfg.engine.bridge_base_url, "http://127.0.0.1:9999")
        self.assertAlmostEqual(cfg.engine.kelly_cap, 0.5)
        # unrelated env vars ignored
        plain = RunnerConfig.from_env({"PATH": "/bin"})
        self.assertEqual(plain.interval_seconds, 300)

    def test_file_and_cli_overrides_layer(self) -> None:
        tmp = tempfile.mkdtemp(prefix="runner-cfg-")
        try:
            path = os.path.join(tmp, "config.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "interval_seconds": 30,
                        "r1_min_avg_score": {"close": 90.0},
                        "dry_run": False,
                        "feed_url": "http://example.test/feed.json",
                    },
                    handle,
                )
            cfg = RunnerConfig.from_file(path)
            self.assertEqual(cfg.interval_seconds, 30)
            self.assertEqual(cfg.r1_min_avg_score["close"], 90.0)
            self.assertFalse(cfg.dry_run)
            self.assertEqual(cfg.engine.feed_url, "http://example.test/feed.json")

            changed = cfg.with_overrides(interval_seconds=120, dry_run=True)
            self.assertEqual(changed.interval_seconds, 120)
            self.assertTrue(changed.dry_run)
            self.assertFalse(cfg.dry_run)  # original untouched
            # engine field override reaches the embedded config
            moved = cfg.with_overrides(feed_url="http://other.test/f.json")
            self.assertEqual(moved.engine.feed_url, "http://other.test/f.json")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_from_mapping_ignores_unknown_keys(self) -> None:
        cfg = RunnerConfig.from_mapping({"interval_seconds": "abc", "bogus": 1})
        self.assertEqual(cfg.interval_seconds, 300)  # ill-typed falls back
        self.assertTrue(cfg.dry_run)

    def test_as_dict_round_trip(self) -> None:
        base = RunnerConfig(interval_seconds=45, dry_run=False)
        rebuilt = RunnerConfig.from_mapping(base.as_dict())
        self.assertEqual(rebuilt.interval_seconds, 45)
        self.assertFalse(rebuilt.dry_run)
        self.assertEqual(rebuilt.engine.bridge_base_url, base.engine.bridge_base_url)

    def test_engine_config_syncs_runner_fields(self) -> None:
        cfg = RunnerConfig(
            dry_run=False,
            journal_path="/tmp/x.sqlite3",
            market_open="09:30",
            market_close="15:00",
            screeners=["swing"],
        )
        engine_cfg = cfg.engine_config()
        self.assertFalse(engine_cfg.dry_run)
        self.assertEqual(engine_cfg.journal_path, "/tmp/x.sqlite3")
        self.assertEqual(engine_cfg.market_open, "09:30")
        self.assertEqual(engine_cfg.market_close, "15:00")
        self.assertEqual(engine_cfg.screener_names, ["swing"])


if __name__ == "__main__":
    unittest.main()
