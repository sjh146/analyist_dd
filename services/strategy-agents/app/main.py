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

from app.config import Config
from app.strategies.theme_strategy import ThemeStrategy
from app.strategies.cycle_strategy import CycleStrategy
from app.strategies.twin_strategy import TwinStrategy
from app.signals.signal_generator import SignalGenerator
from app.signals.signal_validator import SignalValidator
from app.risk_management.position_sizer import PositionSizer
from app.storage.redis_storage import RedisStorage
from app.storage.postgres_storage import PostgresStorage

logging.basicConfig(level=Config.LOG_LEVEL)
logger = logging.getLogger(__name__)


class StrategyAgentService:
    def __init__(self):
        logger.info("Initializing Strategy Agents Service...")
        self.config = Config()
        self.pg_storage = PostgresStorage()
        self.redis = RedisStorage()
        self.signal_gen = SignalGenerator()
        self.signal_validator = SignalValidator()
        self.position_sizer = PositionSizer()

        # Initialize strategies
        self.theme_strategy = ThemeStrategy(self.pg_storage)
        self.cycle_strategy = CycleStrategy(self.pg_storage)
        self.twin_strategy = TwinStrategy(self.pg_storage)

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

        # Process and publish signals
        if all_signals:
            self._process_and_publish(all_signals)
        else:
            logger.info("No signals generated this cycle.")

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

            except Exception as e:
                logger.error(f"Failed to process signal: {e}")
                continue

        logger.info(f"Published {published_count}/{len(signals)} signals to Redis.")

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
