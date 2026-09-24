#!/usr/bin/env python3
"""sns_post_features 집계 writer — sns_posts → (종목,일) 피처 upsert.

실행(stock_xgboost_ml 컨테이너, cwd=/app):
    python -u scripts/sns_features_writer.py [--lookback-days 30] [--stock 005930]
                                             [--dry-run]

배경
----
``sns_post_features`` 의 **리더만** 리포에 있었다(xgboost-ml 의
``feature_engine/sns_features.py``, ``sns_lag_features.py``). 이 테이블을 채우는
writer 가 없어서 SNS/관심도 계열 피처가 전부 상수 0 이었다. 이 스크립트가 그
writer 다.

계약 (리더가 기대하는 컬럼 — 절대 임의로 바꾸지 말 것)
------------------------------------------------------
``sns_post_features``::

    stock_code, trade_date, sentiment_score, attention_score, momentum_score,
    author_quality_score, post_count, bot_filtered_count, kalman_sentiment,
    kalman_attention, kalman_momentum, kalman_activity

계산은 **리더 자신의 산식**을 그대로 재사용한다: ``SnsFeatures.get_daily_features``
(규칙 기반 한국어 금융 감정 사전 + attention/momentum/author_quality + 칼만 봇
필터). 별도 근사식을 여기서 다시 구현하지 않는다 → 리더와 writer 의 정의가
어긋날 수 없다.

유료 LLM 은 쓰지 않는다(감성은 규칙/사전 기반). DB 쓰기는
``ON CONFLICT (stock_code, trade_date) DO UPDATE`` 로 멱등하다.
"""
import argparse
import os
import sys
from datetime import date, timedelta

import psycopg2

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.feature_engine.sns_features import SnsFeatures  # noqa: E402

UPSERT_SQL = """
INSERT INTO sns_post_features
  (stock_code, trade_date, sentiment_score, attention_score, momentum_score,
   author_quality_score, post_count, bot_filtered_count, kalman_sentiment,
   kalman_attention, kalman_momentum, kalman_activity)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (stock_code, trade_date) DO UPDATE SET
  sentiment_score      = EXCLUDED.sentiment_score,
  attention_score      = EXCLUDED.attention_score,
  momentum_score       = EXCLUDED.momentum_score,
  author_quality_score = EXCLUDED.author_quality_score,
  post_count           = EXCLUDED.post_count,
  bot_filtered_count   = EXCLUDED.bot_filtered_count,
  kalman_sentiment     = EXCLUDED.kalman_sentiment,
  kalman_attention     = EXCLUDED.kalman_attention,
  kalman_momentum      = EXCLUDED.kalman_momentum,
  kalman_activity      = EXCLUDED.kalman_activity
"""


def connect():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
    )


def fetch_posts(conn, lookback_days, stock_code=None):
    """sns_posts 를 리더가 기대하는 컬럼 순서의 DataFrame 으로 읽는다."""
    import pandas as pd

    params = []
    where = ["stock_code IS NOT NULL", "posted_at IS NOT NULL"]
    if lookback_days and lookback_days > 0:
        since = date.today() - timedelta(days=lookback_days)
        where.append("posted_at >= %s")
        params.append(since)
    if stock_code:
        where.append("stock_code = %s")
        params.append(stock_code)

    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT stock_code, posted_at::date AS trade_date, text, author_id,
               author_followers, comment_count, like_count, retweet_count, source
        FROM sns_posts
        WHERE {' AND '.join(where)}
        ORDER BY stock_code, posted_at
        """,
        params,
    )
    rows = cur.fetchall()
    cur.close()
    cols = ["stock_code", "trade_date", "text", "author_id", "author_followers",
            "comment_count", "like_count", "retweet_count", "source"]
    return pd.DataFrame(rows, columns=cols)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lookback-days", type=int, default=30,
                    help="이 기간의 게시글만 집계 (0 이하 = 전체)")
    ap.add_argument("--stock", type=str, default=None, help="단일 종목만")
    ap.add_argument("--dry-run", action="store_true", help="DB 쓰기 없이 요약만")
    args = ap.parse_args()

    conn = connect()
    df = fetch_posts(conn, args.lookback_days, args.stock)
    if df.empty:
        print("[writer] sns_posts 에 집계할 게시글이 없습니다.")
        conn.close()
        return

    codes = sorted(df["stock_code"].unique())
    print(f"[writer] 게시글 {len(df)}건 / 종목 {len(codes)}개 "
          f"(lookback={args.lookback_days}일)")

    sns = SnsFeatures()
    all_rows = []
    for code in codes:
        sub = df[df["stock_code"] == code]
        # 리더의 계산 경로를 그대로 호출한다 (규칙 감성 + 칼만 포함).
        rows = sns.get_daily_features(code, sub)
        all_rows.extend(rows)

    print(f"[writer] (종목,일) 피처 행 {len(all_rows)}개 생성")

    if args.dry_run:
        for r in all_rows[:5]:
            print("   ", r)
        conn.close()
        return

    cur = conn.cursor()
    for r in all_rows:
        cur.execute(UPSERT_SQL, (
            r["stock_code"], r["trade_date"],
            float(r["sentiment_score"]), float(r["attention_score"]),
            float(r["momentum_score"]), float(r["author_quality_score"]),
            int(r["post_count"]), int(round(float(r["bot_filtered_count"]))),
            float(r["kalman_sentiment"]), float(r["kalman_attention"]),
            float(r["kalman_momentum"]), float(r["kalman_activity"]),
        ))
    conn.commit()
    cur.execute("SELECT count(*) FROM sns_post_features")
    total = cur.fetchone()[0]
    cur.execute(
        """SELECT count(*) FROM sns_post_features
           WHERE attention_score <> 0 OR author_quality_score <> 0
              OR sentiment_score <> 0 OR momentum_score <> 0
              OR kalman_activity <> 0"""
    )
    nonzero = cur.fetchone()[0]
    cur.close()
    conn.close()
    print(f"[writer] upsert 완료: {len(all_rows)}행 / 테이블 총 {total}행 "
          f"(핵심 피처 비영 {nonzero}행)")


if __name__ == "__main__":
    main()
