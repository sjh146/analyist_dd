#!/usr/bin/env python3
"""종토방 SNS 수집 러너 — NaverBoardCollector → sns_posts 저장 (0.7s 딜레이 내장)."""
import asyncio
import json
import logging
import os
from datetime import datetime

import psycopg2

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sns_runner")

from app.collectors.sns_naver_board import NaverBoardCollector  # noqa: E402


def top_stocks(limit: int = 300) -> list:
    conn = psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
    )
    cur = conn.cursor()
    cur.execute(
        """
        SELECT stock_code FROM market_data
        WHERE trade_date = (SELECT MAX(trade_date) FROM market_data)
        GROUP BY stock_code ORDER BY SUM(trading_value) DESC LIMIT %s
        """,
        (limit,),
    )
    codes = [r[0] for r in cur.fetchall()]
    cur.close()
    conn.close()
    return codes


def save_posts(posts) -> int:
    conn = psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
    )
    cur = conn.cursor()
    saved = 0
    for p in posts:
        cur.execute(
            """
            INSERT INTO sns_posts
              (source, post_id, stock_code, author_id, author_name, author_followers,
               posted_at, text, comment_count, like_count, retweet_count, raw_json)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT DO NOTHING
            """,
            (
                p.source, p.post_id, p.stock_code, p.author_id, p.author_name,
                p.author_followers, p.posted_at, p.text, p.comment_count,
                p.like_count, p.retweet_count,
                json.dumps(p.raw_json, ensure_ascii=False, default=str),
            ),
        )
        saved += cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    return saved


async def main():
    codes = top_stocks(300)
    log.info("수집 대상: %d 종목 (상위 300, 0.7s 딜레이)", len(codes))
    collector = NaverBoardCollector()
    posts = await collector.collect_all(codes)
    log.info("수집 완료: %d 건", len(posts))
    saved = save_posts(posts)
    log.info("저장 완료: %d 건 (중복 제외)", saved)


if __name__ == "__main__":
    asyncio.run(main())
