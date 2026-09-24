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
import pandas as pd
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
                # ── 실측 수정(2026-09-24) ────────────────────────────────────
                # 종전: vectorize_price_pattern(stock_code) 는 내부에서
                #   PriceVectorizer.vectorize(close_prices=None) → **항상 영벡터**,
                # vectorize_sentiment(stock_code) 는 vectorize([]) → **항상 영벡터**
                # 였다. 그래서 combined 1024d 의 앞 512차원이 항상 0 이고,
                # stock_vectors 2773행 중 서로 다른 임베딩이 **3개**뿐이었다
                # (실측: count(DISTINCT embedding)=3). 그 결과 유사도 계열 피처
                # (avg_similarity_top10=1.0, max_similarity=1.0, similarity_std=0.0)
                # 가 상수로 죽었다. 이제 실제 시세/감성 데이터를 넘긴다.
                close_prices = None
                volumes = None
                if market_data is not None and not market_data.empty:
                    md = market_data.sort_values("trade_date")
                    valid = md
                    # 거래정지/무거래 행(시가·고가·저가 전부 0) 제거 — 기준가만 있는
                    # 행이면 수익률/변동성이 왜곡된다(0 나눗셈 포함).
                    if {"open_price", "high_price", "low_price"} <= set(md.columns):
                        valid = md[~(
                            (md["open_price"] == 0)
                            & (md["high_price"] == 0)
                            & (md["low_price"] == 0)
                        )]
                    close_prices = pd.to_numeric(
                        valid["close_price"], errors="coerce"
                    ).dropna()
                    if "volume" in valid.columns:
                        volumes = pd.to_numeric(
                            valid["volume"], errors="coerce"
                        ).fillna(0.0)

                price_vector = self.vectorizer.price_vectorizer.vectorize(
                    close_prices, volumes
                )
                sentiment_vector = self.vectorizer.sentiment_vectorizer.vectorize(
                    sentiment
                )
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
