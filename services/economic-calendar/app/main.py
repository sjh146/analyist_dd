import logging
import schedule
import time
from datetime import datetime, timezone, timedelta

from app.config import Config
from app.collectors.fomc_collector import FOMCCollector
from app.collectors.earnings_collector import EarningsCollector
from app.collectors.economic_indicators_collector import EconomicIndicatorsCollector
from app.collectors.dividend_collector import DividendCollector
from app.storage.postgres_storage import PostgresStorage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


class EconomicCalendarService:
    def __init__(self):
        logger.info("Initializing EconomicCalendarService...")
        self.config = Config()
        self.fomc = FOMCCollector()
        self.earnings = EarningsCollector()
        self.indicators = EconomicIndicatorsCollector()
        self.dividends = DividendCollector()
        self.storage = PostgresStorage()
        self._running = False

    def run_daily_update(self):
        logger.info("Running daily economic calendar update...")
        collectors = [
            ("FOMC", self.fomc),
            ("Earnings", self.earnings),
            ("Indicators", self.indicators),
            ("Dividends", self.dividends),
        ]
        total = 0
        for name, collector in collectors:
            try:
                events = collector.collect()
                saved = self.storage.save_events(events)
                total += saved
                logger.info("%s: %d events saved", name, saved)
            except Exception as e:
                logger.error("%s collector failed: %s", name, e)
        logger.info("Daily update complete. Total new events: %d", total)

    def get_upcoming_events(self, days: int = 14) -> list[dict]:
        return self.storage.get_upcoming_events(days)

    def run_scheduled(self):
        # 08:15 KST = 23:15 UTC (KST=UTC+9)
        schedule.every().day.at("23:15").do(self.run_daily_update)

        logger.info("EconomicCalendarService scheduled daily at 08:15 KST (23:15 UTC)")
        self._running = True

        self.run_daily_update()

        while self._running:
            schedule.run_pending()
            time.sleep(60)

    def stop(self):
        self._running = False


def main():
    service = EconomicCalendarService()
    try:
        service.run_scheduled()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        service.stop()


if __name__ == "__main__":
    main()
