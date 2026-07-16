"""
Stock Vectorizer Service
- Vectorizes all KOSPI/KOSDAQ stocks using price, fundamental, and sentiment data
- Stores vectors in pgvector for similarity search
- Runs nightly to update vectors
"""

import logging
import schedule
import time
import numpy as np
from datetime import datetime

from app.config import Config
from app.vectorizers.combined_vectorizer import CombinedVectorizer
from app.storage.pgvector_storage import PgvectorStorage
from app.storage.postgres_storage import PostgresStorage

logging.basicConfig(level=Config.LOG_LEVEL)
logger = logging.getLogger(__name__)


class StockVectorizerService:
    def __init__(self):
        logger.info("Initializing Stock Vectorizer Service...")
        self.config = Config()
        self.vectorizer = CombinedVectorizer()
        self.pg_storage = PostgresStorage()
        self.pgvector_storage = PgvectorStorage()
        self._running = False

    def run_vectorization(self):
        """Vectorize all stocks and store in pgvector."""
        logger.info("Starting full stock vectorization...")

        # Step 1: Get all stocks
        stocks = self.pg_storage.get_all_stocks()
        logger.info(f"Total stocks to vectorize: {len(stocks)}")

        # Step 2: Vectorize each stock
        success_count = 0
        for i, stock in enumerate(stocks):
            try:
                stock_code = stock["stock_code"]

                # Get market data
                market_data = self.pg_storage.get_latest_market_data(
                    stock_code, days=60
                )

                # Get sentiment data
                sentiment = self.pg_storage.get_latest_sentiment(stock_code, days=30)

                # Get sector info from Neo4j
                sector_info = self.pg_storage.get_stock_sector(stock_code)

                # Combine all data
                stock_data = {
                    **stock,
                    "market_data": market_data,
                    "sentiment": sentiment,
                    "sector": sector_info.get("sector") if sector_info else stock.get("sector"),
                    "industry": sector_info.get("industry") if sector_info else None,
                }

                # Generate embeddings for each type
                price_vector = self.vectorizer.vectorize_price_pattern(stock_code)
                sentiment_vector = self.vectorizer.vectorize_sentiment(stock_code)
                fundamental_vector = self.vectorizer.vectorize_fundamentals(stock_data)
                combined_vector = self.vectorizer.create_combined_embedding(
                    price_vector, sentiment_vector, fundamental_vector
                )

                # Store combined vector (1024-d) only
                # Note: individual vector types (256-d) skipped because
                # DB column expects 1024-d for all types
                self.pgvector_storage.save_vector(
                    stock_code=stock_code,
                    vector_type="combined",
                    embedding=combined_vector,
                    metadata={"dimensions": len(combined_vector)},
                )

                success_count += 1
                if (i + 1) % 10 == 0:
                    logger.info(f"Vectorized {i+1}/{len(stocks)} stocks")

            except Exception as e:
                logger.error(f"Failed to vectorize {stock.get('stock_code')}: {e}")
                continue

        # Step 3: Run similarity demo
        self._demo_similarity_search()

        logger.info(f"Vectorization complete. {success_count}/{len(stocks)} stocks vectorized.")

    def _demo_similarity_search(self):
        """Demo: Find similar stocks for top stocks."""
        demo_stocks = ["005930", "000660", "035420"]  # 삼전, SK하닉, 네이버
        for code in demo_stocks:
            try:
                similar = self.pgvector_storage.find_similar_stocks(
                    stock_code=code,
                    vector_type="combined",
                    top_k=5,
                )
                stock_name = self.pg_storage.get_stock_name(code)
                logger.info(f"Similar stocks to {code} ({stock_name}):")
                for s in similar:
                    s_name = self.pg_storage.get_stock_name(s["stock_code"])
                    logger.info(f"  {s['stock_code']} ({s_name}): similarity={s['similarity']:.4f}")
            except Exception as e:
                logger.error(f"Demo search failed for {code}: {e}")

    def run_scheduled(self):
        """Run on schedule."""
        schedule.every().day.at("20:00").do(self.run_vectorization)

        logger.info("Vectorizer service started. Running daily at 20:00.")
        self._running = True

        # Run once on startup
        self.run_vectorization()

        while self._running:
            schedule.run_pending()
            time.sleep(60)

    def stop(self):
        self._running = False


def main():
    service = StockVectorizerService()
    try:
        service.run_scheduled()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        service.stop()


if __name__ == "__main__":
    main()
