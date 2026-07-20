import logging
import schedule
import time
from datetime import datetime

from app.config import Config
from app.collectors.investor_collector import InvestorCollector
from app.collectors.program_trading_collector import ProgramTradingCollector
from app.collectors.short_selling_collector import ShortSellingCollector
from app.collectors.derivatives_collector import DerivativesCollector
from app.storage.postgres_storage import PostgresStorage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class KrxCollectorService:
    def __init__(self):
        logger.info("Initializing KRX Collector Service...")
        self.config = Config()
        self.investor_collector = InvestorCollector(market=self.config.KRX_MARKET)
        self.program_collector = ProgramTradingCollector(market=self.config.KRX_MARKET)
        self.short_selling_collector = ShortSellingCollector(market=self.config.KRX_MARKET)
        self.derivatives_collector = DerivativesCollector()
        self.storage = PostgresStorage()
        self._running = False

    def run_daily_collection(self):
        logger.info("Starting daily KRX data collection...")

        counts = {"investor_trading": 0, "investor_net": 0, "program": 0, "short_selling": 0, "derivatives": 0}

        df = self.investor_collector.collect_trading_value()
        if not df.empty:
            self.storage.save_trading_data(df)
            counts["investor_trading"] = len(df)
        else:
            logger.warning("No investor trading value data to store")

        df = self.investor_collector.collect_net_purchases()
        if not df.empty:
            self.storage.save_trading_data(df)
            counts["investor_net"] = len(df)
        else:
            logger.warning("No net purchase data to store")

        df = self.program_collector.collect_exhaustion_rates()
        if not df.empty:
            self.storage.save_program_trading_data(df)
            counts["program"] = len(df)
        else:
            logger.warning("No program trading data to store")

        df = self.short_selling_collector.collect_all()
        if not df.empty:
            self.storage.save_short_selling_data(df)
            counts["short_selling"] = len(df)
        else:
            logger.warning("No short selling data to store")

        df = self.derivatives_collector.collect_index_ohlcv()
        if not df.empty:
            self.storage.save_derivatives_data(df)
            counts["derivatives"] = len(df)
        else:
            logger.warning("No derivatives data to store")

        logger.info(
            f"KRX collection summary — "
            f"investor_trading: {counts['investor_trading']} rows, "
            f"investor_net: {counts['investor_net']} rows, "
            f"program: {counts['program']} rows, "
            f"short_selling: {counts['short_selling']} rows, "
            f"derivatives: {counts['derivatives']} rows"
        )

    def run_scheduled(self):
        schedule.every().day.at("19:00").do(self.run_daily_collection)
        schedule.every(self.config.COLLECTION_INTERVAL_HOURS).hours.do(self.run_daily_collection)

        logger.info(
            f"KRX Collector started. Running daily at 19:00 "
            f"and every {self.config.COLLECTION_INTERVAL_HOURS} hours."
        )
        self._running = True

        self.run_daily_collection()

        while self._running:
            schedule.run_pending()
            time.sleep(60)

    def stop(self):
        self._running = False


def main():
    service = KrxCollectorService()
    try:
        service.run_scheduled()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        service.stop()


if __name__ == "__main__":
    main()
