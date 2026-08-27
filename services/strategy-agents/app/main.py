"""
Strategy Agents Service
- Runs 3 trading strategies: theme, cycle rotation, twin stock
- Generates validated trade signals
- Publishes signals to Redis for Windows VM Trade Executor
"""

import asyncio
import logging
import schedule
import time
import json
from datetime import datetime
from pathlib import Path

import yaml
from typing import Dict

from app.config import Config
from app.strategies.theme_strategy import ThemeStrategy
from app.strategies.cycle_strategy import CycleStrategy
from app.strategies.twin_strategy import TwinStrategy
from app.strategies.value_strategy import ValueStrategy
from app.strategies.quality_strategy import QualityStrategy
from app.strategies.momentum_strategy import MomentumStrategy
from app.strategies.lowvol_strategy import LowVolatilityStrategy
from app.strategies.multifactor_strategy import MultiFactorStrategy
from app.strategies.ackman_strategy import AckmanStrategy
from app.signals.signal_generator import SignalGenerator
from app.signals.signal_validator import SignalValidator
from app.risk_management.position_sizer import PositionSizer
from app.risk_management.stop_loss import StopLoss
from app.storage.redis_storage import RedisStorage
from app.storage.postgres_storage import PostgresStorage
from app.metrics_integration import init_metrics, on_signal_generated

logging.basicConfig(level=Config.LOG_LEVEL)
logger = logging.getLogger(__name__)

_FACTOR_STRATEGY_NAMES = {"value_factor", "quality_factor", "momentum_factor", "lowvol_factor", "multifactor"}
_ACKMAN_STRATEGY_NAME = "ackman_fundamental"


def _load_strategies_yaml() -> Dict:
    """strategies.yaml 경로 탐색 후 파싱 (컨테이너 마운트 우선, repo-relative 폴백)."""
    base = Path(__file__).resolve()
    candidates = [
        # 컨테이너 마운트 (docker compose: ./config/strategies:/app/config/strategies)
        Path("/app/config/strategies/strategies.yaml"),
        base.parents[2] / "config" / "strategies" / "strategies.yaml",
    ]
    # parents[3] 은 경로 깊이에 따라 IndexError 가능(컨테이너 /app 마운트) — 존재 시에만 추가
    if len(base.parents) > 3:
        candidates.append(base.parents[3] / "config" / "strategies" / "strategies.yaml")
    yaml_path = next((p for p in candidates if p.exists()), None)
    if yaml_path is None:
        raise FileNotFoundError("strategies.yaml not found")
    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data


class StrategyAgentService:
    def __init__(self):
        logger.info("Initializing Strategy Agents Service...")
        self.config = Config()
        self.pg_storage = PostgresStorage()
        self.redis = RedisStorage()
        self.signal_gen = SignalGenerator()
        self.signal_validator = SignalValidator()
        self.position_sizer = PositionSizer()
        self.stop_loss = StopLoss(self.pg_storage)

        # Initialize strategies
        self.theme_strategy = ThemeStrategy(self.pg_storage)
        self.cycle_strategy = CycleStrategy(self.pg_storage)
        self.twin_strategy = TwinStrategy(self.pg_storage)

        # Initialize factor strategies (paper-only; never publishes to trade:signals)
        self.value_strategy = ValueStrategy(self.pg_storage)
        self.quality_strategy = QualityStrategy(self.pg_storage)
        self.momentum_strategy = MomentumStrategy(self.pg_storage)
        self.lowvol_strategy = LowVolatilityStrategy(self.pg_storage)
        self.multifactor_strategy = MultiFactorStrategy(self.pg_storage)
        self._register_factor_strategies()

        # Ackman strategy (thesis ledger; signals go to paper:ackman_signals, never trade:signals)
        self.ackman_strategy = AckmanStrategy(self.pg_storage)
        self._register_ackman_strategy()

        init_metrics(9103)

        self._running = False

    def run_all_strategies(self):
        """Run all trading strategies and generate signals."""
        logger.info("=" * 50)
        logger.info("Running all trading strategies...")
        logger.info(f"Time: {datetime.now().isoformat()}")
        logger.info("=" * 50)

        all_signals = []

        # 1. Theme Trading
        try:
            logger.info(">> Theme Strategy running...")
            theme_signals = self.theme_strategy.analyze()
            logger.info(f"   Generated {len(theme_signals)} theme signals")
            all_signals.extend(theme_signals)
        except Exception as e:
            logger.error(f"Theme strategy failed: {e}")

        # 2. Cycle Rotation
        try:
            logger.info(">> Cycle Rotation Strategy running...")
            cycle_signals = self.cycle_strategy.analyze()
            logger.info(f"   Generated {len(cycle_signals)} cycle rotation signals")
            all_signals.extend(cycle_signals)
        except Exception as e:
            logger.error(f"Cycle strategy failed: {e}")

        # 3. Twin Trading
        try:
            logger.info(">> Twin Strategy running...")
            twin_signals = self.twin_strategy.analyze()
            logger.info(f"   Generated {len(twin_signals)} twin trading signals")
            all_signals.extend(twin_signals)
        except Exception as e:
            logger.error(f"Twin strategy failed: {e}")

        # 4. Factor strategies (paper-only; signals go to paper:factor_signals, never trade:signals)
        paper_signals = []
        factor_strategies = [
            ("Value", self.value_strategy),
            ("Quality", self.quality_strategy),
            ("Momentum", self.momentum_strategy),
            ("LowVol", self.lowvol_strategy),
            ("MultiFactor", self.multifactor_strategy),
        ]
        for label, strategy in factor_strategies:
            try:
                logger.info(f">> {label} Factor Strategy running...")
                signals = strategy.analyze()
                logger.info(f"   Generated {len(signals)} {label} factor signals")
                paper_signals.extend(signals)
            except Exception as e:
                logger.error(f"{label} factor strategy failed: {e}")

        # Process and publish signals
        if all_signals:
            self._process_and_publish(all_signals)
        else:
            logger.info("No signals generated this cycle.")

        if paper_signals:
            self._publish_paper_signals(paper_signals)

        # 5. Ackman Strategy (thesis ledger; signals go to paper:ackman_signals, never trade:signals)
        ackman_signals = []
        try:
            logger.info(">> Ackman Strategy (thesis ledger) running...")
            ackman_signals = self.ackman_strategy.analyze()
            logger.info(f"   Generated {len(ackman_signals)} ackman signals")
        except Exception as e:
            logger.error(f"Ackman strategy failed: {e}")
        if ackman_signals:
            self._publish_ackman_signals(ackman_signals)

        try:
            logger.info(">> Stop-Loss Evaluation running...")
            sl_signals = self.stop_loss.evaluate_positions()
            try:
                ackman_codes = {t.get("stock_code") for t in self.pg_storage.get_active_theses(_ACKMAN_STRATEGY_NAME) or []}
            except Exception:
                ackman_codes = set()
            excluded = [s for s in sl_signals if s.get("stock_code") in ackman_codes]
            sl_signals = [s for s in sl_signals if s.get("stock_code") not in ackman_codes]
            if excluded:
                logger.info("Excluded %d stop-loss signals for ackman theses (가격 스톱 없음)", len(excluded))
            if sl_signals:
                logger.info(f"   Generated {len(sl_signals)} stop-loss/take-profit signals")
                for signal in sl_signals:
                    signal["signal_id"] = f"sig_{datetime.now().strftime('%Y%m%d%H%M%S')}_{signal.get('stock_code', 'unknown')}"
                    signal["timestamp"] = datetime.now().isoformat()
                    signal["quantity"] = 0
                    self.redis.publish_signal(signal)
                    logger.info(f"Published SL signal: {json.dumps(signal, ensure_ascii=False)}")
                    on_signal_generated("risk_management")
                logger.info(f"Published {len(sl_signals)} stop-loss signals to Redis.")
            else:
                logger.info("   No stop-loss/take-profit signals needed.")
        except Exception as e:
            logger.error(f"Stop-loss evaluation failed: {e}")

    def _process_and_publish(self, signals: list):
        """Validate, size, and publish signals."""
        published_count = 0
        for signal in signals:
            try:
                # Validate signal
                if not self.signal_validator.validate(signal):
                    logger.debug(f"Signal rejected by validator: {signal}")
                    continue

                # Calculate position size
                signal["quantity"] = self.position_sizer.calculate(signal)

                if signal["quantity"] <= 0:
                    continue

                # Generate signal ID and timestamp
                signal["signal_id"] = f"sig_{datetime.now().strftime('%Y%m%d%H%M%S')}_{published_count}"
                signal["timestamp"] = datetime.now().isoformat()

                # Publish to Redis for Windows VM trade executor
                self.redis.publish_signal(signal)
                logger.info(f"Published signal: {json.dumps(signal, ensure_ascii=False)}")
                published_count += 1
                on_signal_generated(signal.get("strategy_name", "unknown"))

            except Exception as e:
                logger.error(f"Failed to process signal: {e}")
                continue

        logger.info(f"Published {published_count}/{len(signals)} signals to Redis.")

    def _publish_paper_signals(self, signals: list):
        """Publish factor-strategy signals to the paper-only stream (no real-trade path)."""
        published_count = 0
        for signal in signals:
            try:
                signal["signal_id"] = f"paper_{datetime.now().strftime('%Y%m%d%H%M%S')}_{published_count}"
                signal["timestamp"] = datetime.now().isoformat()
                if self.redis.publish_paper_signal(signal):
                    logger.info(f"Published paper signal: {json.dumps(signal, ensure_ascii=False)}")
                    published_count += 1
            except Exception as e:
                logger.error(f"Failed to publish paper signal: {e}")
                continue
        logger.info(f"Published {published_count}/{len(signals)} paper signals to Redis.")

    def _publish_ackman_signals(self, signals: list):
        """Publish ackman thesis signals to the paper-only ackman stream (no real-trade path)."""
        published_count = 0
        for signal in signals:
            try:
                signal["signal_id"] = f"ackman_{datetime.now().strftime('%Y%m%d%H%M%S')}_{published_count}"
                signal["timestamp"] = datetime.now().isoformat()
                if self.redis.publish_ackman_signal(signal):
                    logger.info(f"Published ackman signal: {json.dumps(signal, ensure_ascii=False)}")
                    published_count += 1
            except Exception as e:
                logger.error(f"Failed to publish ackman signal: {e}")
                continue
        logger.info(f"Published {published_count}/{len(signals)} ackman signals to Redis.")

    def _register_factor_strategies(self):
        """Upsert factor strategy configs from strategies.yaml (paper-only, thresholds stay in code)."""
        try:
            data = _load_strategies_yaml()
            strategies = data.get("strategies", {})
            for name, spec in strategies.items():
                if name not in _FACTOR_STRATEGY_NAMES:
                    continue
                self.pg_storage.upsert_strategy_config(
                    strategy_name=name,
                    strategy_type="factor",
                    parameters=spec.get("parameters", {}),
                    is_active=bool(spec.get("is_active", True)),
                )
            logger.info("Registered %d factor strategies in strategy_config", len(_FACTOR_STRATEGY_NAMES))
        except Exception as e:
            logger.warning(f"Factor strategy registration skipped: {e}")

    def _register_ackman_strategy(self):
        """Upsert ackman_fundamental config from strategies.yaml (strategy_type='thesis')."""
        try:
            strategies = _load_strategies_yaml().get("strategies", {})
            spec = strategies.get(_ACKMAN_STRATEGY_NAME)
            if spec is None:
                raise KeyError(_ACKMAN_STRATEGY_NAME)
            self.pg_storage.upsert_strategy_config(
                strategy_name=_ACKMAN_STRATEGY_NAME,
                strategy_type="thesis",
                parameters=spec.get("parameters", {}),
                is_active=bool(spec.get("is_active", True)),
            )
            logger.info("Registered ackman_fundamental in strategy_config (type=thesis)")
        except Exception as e:
            logger.warning(f"Ackman strategy registration skipped: {e}")

    def run_scheduled(self):
        """Run strategies on schedule."""
        # Run every 30 minutes during market hours
        schedule.every(30).minutes.do(self.run_all_strategies)

        logger.info("Strategy Agents Service started. Running every 30 minutes.")
        self._running = True

        # Run once on startup
        self.run_all_strategies()

        while self._running:
            schedule.run_pending()
            time.sleep(30)

    def stop(self):
        self._running = False


def main():
    service = StrategyAgentService()
    try:
        service.run_scheduled()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        service.stop()


if __name__ == "__main__":
    main()
