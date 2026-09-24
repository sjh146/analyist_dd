#!/usr/bin/env python3
"""종토방 SNS 수집 러너 — NaverBoardCollector → sns_posts 저장 (0.7s 딜레이 내장).

실행(컨테이너, cwd=/app):
    python -u scripts/sns_collect_runner.py [--limit 250] [--max-pages 5]
                                            [--lookback-days 15] [--page-size 100]
                                            [--codes 005930,000660] [--dry-run]

설계 메모
---------
- 수집기는 stock.naver.com JSON API 를 쓴다(2026-09 SPA 전환으로 HTML 경로는
  302). 요청 간 최소 0.7초 딜레이가 수집기 안에 강제되어 있다.
- 유니버스 = 거래대금 상위 N 종목. ``market_data.trading_value`` 는 **날짜별로
  결측이 섞인다**(실측: 2026-09-23 은 2,568행 중 4행만 값이 있음). 그래서
  'trading_value 가 채워진 최신 거래일'을 골라 그 날의 상위 종목을 쓴다.
- ``save_posts`` 는 ``sns_posts`` 스키마와 1:1 (UNIQUE (source, post_id)) 이며
  ``ON CONFLICT DO NOTHING`` 으로 재실행에 안전하다.
"""
import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime

import psycopg2

# 컨테이너에서 `python scripts/xxx.py` 로 실행하면 sys.path[0] 이 scripts/ 라
# `app` 패키지가 안 잡힌다 → /app 을 명시적으로 추가한다.
if "/app" not in sys.path:
    sys.path.insert(0, "/app")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sns_runner")

from app.collectors.sns_naver_board import NaverBoardCollector  # noqa: E402


def _connect():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
    )


def universe_date(conn) -> str:
    """``trading_value`` 가 충분히 채워진 최신 거래일을 고른다."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT trade_date FROM market_data
        GROUP BY trade_date
        HAVING count(trading_value) >= 1000
        ORDER BY trade_date DESC LIMIT 1
        """
    )
    row = cur.fetchone()
    cur.close()
    if row is None:
        # 폴백: trading_value 가 전혀 없으면 최신 거래일을 쓴다.
        cur = conn.cursor()
        cur.execute("SELECT MAX(trade_date) FROM market_data")
        row = cur.fetchone()
        cur.close()
    return str(row[0])


def top_stocks(limit: int = 300, trade_date: str = None) -> list:
    """거래대금 상위 종목 코드 목록."""
    conn = _connect()
    day = trade_date or universe_date(conn)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT stock_code FROM market_data
        WHERE trade_date = %s
        GROUP BY stock_code
        ORDER BY SUM(trading_value) DESC NULLS LAST
        LIMIT %s
        """,
        (day, limit),
    )
    codes = [r[0] for r in cur.fetchall()]
    cur.close()
    conn.close()
    log.info("유니버스 기준일 %s, 상위 %d 종목", day, len(codes))
    return codes


def save_posts(posts) -> int:
    conn = _connect()
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=250, help="거래대금 상위 N 종목")
    ap.add_argument("--max-pages", type=int, default=5, help="종목당 최대 페이지")
    ap.add_argument("--lookback-days", type=int, default=15, help="수집 윈도우(일)")
    ap.add_argument("--page-size", type=int, default=100, help="페이지 크기(<=100)")
    ap.add_argument("--codes", type=str, default=None, help="쉼표 구분 종목코드(테스트)")
    ap.add_argument("--no-enrich", action="store_true", help="공감/댓글 보강 생략")
    ap.add_argument("--dry-run", action="store_true", help="DB 저장 없이 수집만")
    args = ap.parse_args()

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    else:
        codes = top_stocks(args.limit)
    log.info(
        "수집 대상: %d 종목 (윈도우 %d일, 최대 %d페이지, 페이지 %d건, 딜레이 %.1fs)",
        len(codes), args.lookback_days, args.max_pages, args.page_size,
        NaverBoardCollector.REQUEST_DELAY,
    )

    collector = NaverBoardCollector()
    collected = []
    started = datetime.now()
    for i, code in enumerate(codes, 1):
        posts = await collector.collect_all(
            [code],
            lookback_days=args.lookback_days,
            max_pages=args.max_pages,
            page_size=args.page_size,
            enrich=not args.no_enrich,
        )
        collected.extend(posts)
        if i % 10 == 0 or i == len(codes):
            el = (datetime.now() - started).total_seconds()
            log.info("[%d/%d] 누적 %d건 (%.1f분 경과)", i, len(codes), len(collected), el / 60)

    log.info("수집 완료: %d 건", len(collected))
    if args.dry_run:
        log.info("--dry-run: 저장 생략")
        return
    saved = save_posts(collected)
    log.info("저장 완료: %d 건 (중복 제외)", saved)


if __name__ == "__main__":
    asyncio.run(main())
