#!/usr/bin/env python3
"""stock_vectors 재생성 1회 실행기 (야간 스케줄과 동일 로직).

WHY(2026-09-24 실측): app/main.py 가 price/sentiment 벡터라이저에 데이터 대신
stock_code 를 넘겨 **항상 영벡터**를 만들었다. 그 결과 stock_vectors 2773행 중
서로 다른 임베딩이 3개뿐이었고, 유사도 계열 피처(avg_similarity_top10,
max_similarity, similarity_std, similar_count)가 상수로 죽었다.
main.py 는 수정됐지만 기존 임베딩은 그대로이므로 이 스크립트로 한 번 재생성한다.

실행 (호스트):
  docker exec stock_vectorizer python /app/scripts/rebuild_vectors.py --limit 20
  docker exec stock_vectorizer python /app/scripts/rebuild_vectors.py
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time

sys.path.insert(0, "/app")

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("rebuild_vectors")


def main() -> int:
    ap = argparse.ArgumentParser(description="stock_vectors 재생성")
    ap.add_argument("--limit", type=int, default=0, help="점검용 종목 수 제한(0=전체)")
    args = ap.parse_args()

    from app.main import StockVectorizerService
    from app.storage.pgvector_storage import PgvectorStorage
    from app.storage.postgres_storage import PostgresStorage

    svc = StockVectorizerService()
    stocks = svc.pg_storage.get_all_stocks()
    if args.limit:
        stocks = stocks[: args.limit]
    logger.info("재생성 대상 종목 수: %d", len(stocks))

    t0 = time.time()
    ok = 0
    for i, stock in enumerate(stocks):
        code = stock["stock_code"]
        try:
            market_data = svc.pg_storage.get_latest_market_data(code, days=60)
            sentiment = svc.pg_storage.get_latest_sentiment(code, days=30)
            sector_info = svc.pg_storage.get_stock_sector(code)
            stock_data = {
                **stock,
                "market_data": market_data,
                "sentiment": sentiment,
                "sector": (sector_info.get("sector") if sector_info else None)
                or stock.get("sector"),
                "industry": (sector_info.get("industry") if sector_info else None),
            }
            import pandas as pd

            close_prices = None
            volumes = None
            if market_data is not None and not market_data.empty:
                md = market_data.sort_values("trade_date")
                valid = md
                if {"open_price", "high_price", "low_price"} <= set(md.columns):
                    valid = md[~(
                        (md["open_price"] == 0)
                        & (md["high_price"] == 0)
                        & (md["low_price"] == 0)
                    )]
                close_prices = pd.to_numeric(valid["close_price"], errors="coerce").dropna()
                if "volume" in valid.columns:
                    volumes = pd.to_numeric(valid["volume"], errors="coerce").fillna(0.0)

            price_vector = svc.vectorizer.price_vectorizer.vectorize(close_prices, volumes)
            sentiment_vector = svc.vectorizer.sentiment_vectorizer.vectorize(sentiment)
            fundamental_vector = svc.vectorizer.vectorize_fundamentals(stock_data)
            combined = svc.vectorizer.create_combined_embedding(
                price_vector, sentiment_vector, fundamental_vector
            )
            svc.pgvector_storage.save_vector(
                stock_code=code,
                vector_type="combined",
                embedding=combined,
                metadata={"dimensions": len(combined), "builder": "rebuild_vectors"},
            )
            ok += 1
            if (i + 1) % 100 == 0:
                logger.info("진행 %d/%d (%.1fs)", i + 1, len(stocks), time.time() - t0)
        except Exception as e:  # noqa: BLE001
            logger.error("실패 %s: %s", code, e)

    elapsed = time.time() - t0
    logger.info("완료: %d/%d 저장, %.1fs", ok, len(stocks), elapsed)

    # 결과 요약 — 재생성 품질(서로 다른 임베딩 수) 확인
    storage = PgvectorStorage()
    conn = storage._get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT count(*), count(DISTINCT embedding) FROM stock_vectors")
        total, distinct = cur.fetchone()
        cur.close()
    finally:
        storage._put_conn(conn)
    print(f"[rebuild_vectors] stock_vectors rows={total} distinct_embeddings={distinct} "
          f"elapsed={elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
