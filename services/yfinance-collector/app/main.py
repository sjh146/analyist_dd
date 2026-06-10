"""
yfinance Collector Service
- Collects daily OHLCV data for KOSPI/KOSDAQ stocks
- Calculates technical indicators
- Stores data in PostgreSQL
"""

import logging
import schedule
import time
from datetime import datetime

from app.config import Config
from app.collectors.stock_list_collector import StockListCollector
from app.collectors.price_collector import PriceCollector
from app.processors.technical_indicators import TechnicalIndicatorCalculator
from app.processors.data_cleaner import DataCleaner
from app.storage.postgres_storage import PostgresStorage

logging.basicConfig(level=Config().LOG_LEVEL if hasattr(Config, 'LOG_LEVEL') else "INFO")
logger = logging.getLogger(__name__)


class YFinanceCollectorService:
    def __init__(self):
        logger.info("Initializing yfinance Collector Service...")
        self.config = Config()
        self.stock_list_collector = StockListCollector()
        self.price_collector = PriceCollector()
        self.tech_indicator = TechnicalIndicatorCalculator()
        self.data_cleaner = DataCleaner()
        self.storage = PostgresStorage()
        self._running = False

    def run_daily_collection(self):
        """Run daily market data collection for all stocks."""
        logger.info("Starting daily market data collection...")

        # Step 1: Get stock list
        stocks = self.stock_list_collector.get_all_stocks()
        logger.info(f"Total stocks to collect: {len(stocks)}")

        # Step 2: Upsert stock master data
        for stock in stocks:
            self.storage.upsert_stock(stock)

        # Step 3: Collect price data
        df = self.price_collector.collect_all(stocks)
        if df.empty:
            logger.warning("No data collected")
            return

        logger.info(f"Collected data: {len(df)} rows")

        # Step 4: Calculate technical indicators
        df = self.tech_indicator.calculate_all(df)
        logger.info(f"Technical indicators calculated: {len(df.columns)} columns")

        # Step 5: Clean data
        df = self.data_cleaner.clean(df)
        logger.info(f"Data cleaned: {len(df)} rows")

        # Step 6: Store in PostgreSQL
        for stock_code in df["stock_code"].unique():
            stock_df = df[df["stock_code"] == stock_code]
            self.storage.save_market_data(stock_code, stock_df)

        logger.info(f"Daily collection complete. Processed {len(stocks)} stocks.")

    def run_scheduled(self):
        """Run on a schedule."""
        schedule.every().day.at("18:00").do(self.run_daily_collection)
        schedule.every(6).hours.do(self.run_daily_collection)

        logger.info("Collector service started. Running daily at 18:00 and every 6 hours.")
        self._running = True

        # Run once on startup
        self.run_daily_collection()

        while self._running:
            schedule.run_pending()
            time.sleep(60)

    def stop(self):
        self._running = False


def main():
    service = YFinanceCollectorService()
    try:
        service.run_scheduled()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        service.stop()


if __name__ == "__main__":
    main()
