#!/usr/bin/env python3
"""SNS 피처 실측 검증 — 리더를 **그대로 호출**해 피처가 비영·비상수인지 확인한다.

실행(stock_xgboost_ml 컨테이너, cwd=/app):
    python -u scripts/sns_feature_verify.py [--stocks 005930,000660,005370]

검증 대상 (리더 코드는 수정하지 않는다)
---------------------------------------
1. ``app.feature_engine.sns_features.SnsFeatures.get_daily_features``
   → sns_post_features 의 4대 피처 + 칼만 계열을 계산하는 리더 경로.
2. ``app.feature_engine.sns_lag_features.SnsLagFeatures.get_all_features``
   → DB(sns_post_features × market_data) 를 직접 읽는 리더 경로.
   ``sns_<feature>_best_lag / _max_corr / _lag_sign / _corr0`` 16개 키.

출력
----
- sns_posts / sns_post_features 행수
- sns_post_features 의 피처별 비영(non-zero)·비상수(distinct count) 통계
- 종목×일 3쌍 이상의 피처 실측값 (before/after 비교용으로 그대로 덤프)
"""
import argparse
import os
import sys
from collections import defaultdict

import psycopg2

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.feature_engine.sns_features import SnsFeatures          # noqa: E402
from app.feature_engine.sns_lag_features import SnsLagFeatures    # noqa: E402

FEATURE_COLS = [
    "sentiment_score", "attention_score", "momentum_score",
    "author_quality_score", "post_count", "bot_filtered_count",
    "kalman_sentiment", "kalman_attention", "kalman_momentum",
    "kalman_activity",
]


def connect():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
    )


def load_posts_df(conn, stock_code):
    """sns_posts → 리더가 기대하는 DataFrame (stock_code, trade_date, ...)."""
    import pandas as pd
    cur = conn.cursor()
    cur.execute(
        """
        SELECT posted_at::date AS trade_date, text, author_id, author_followers,
               comment_count, like_count, retweet_count, source
        FROM sns_posts
        WHERE stock_code = %s AND posted_at IS NOT NULL
        ORDER BY posted_at
        """,
        (stock_code,),
    )
    rows = cur.fetchall()
    cur.close()
    df = pd.DataFrame(
        rows,
        columns=["trade_date", "text", "author_id", "author_followers",
                 "comment_count", "like_count", "retweet_count", "source"],
    )
    df["stock_code"] = stock_code
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks", type=str, default=None,
                    help="검증 종목(쉼표). 미지정 시 피처 행이 많은 종목 자동 선택")
    ap.add_argument("--n-stocks", type=int, default=4)
    ap.add_argument("--pairs", type=int, default=6, help="출력할 (종목,일) 쌍 수")
    args = ap.parse_args()

    conn = connect()
    cur = conn.cursor()

    print("=" * 96)
    print("[0] 테이블 행수")
    for t in ("sns_posts", "sns_post_features"):
        cur.execute(f"SELECT count(*) FROM {t}")
        print(f"  {t}: {cur.fetchone()[0]}")
    cur.execute("SELECT count(DISTINCT stock_code), count(DISTINCT trade_date) "
                "FROM sns_post_features")
    ns, nd = cur.fetchone()
    print(f"  sns_post_features distinct stock/date: {ns} / {nd}")

    print("=" * 96)
    print("[1] sns_post_features 피처 통계 (비영 행수 / distinct 값 수 / min~max)")
    for col in FEATURE_COLS:
        cur.execute(
            f"""SELECT count(*), count(*) FILTER (WHERE {col} <> 0),
                       count(DISTINCT {col}), min({col}), max({col})
                FROM sns_post_features"""
        )
        total, nz, distinct, mn, mx = cur.fetchone()
        print(f"  {col:<20} rows={total:<6} nonzero={nz:<6} distinct={distinct:<6} "
              f"min={mn} max={mx}")

    # 검증 종목 선택.
    if args.stocks:
        stocks = [s.strip() for s in args.stocks.split(",") if s.strip()]
    else:
        cur.execute(
            """
            SELECT stock_code, count(*) n FROM sns_post_features
            WHERE attention_score <> 0 OR author_quality_score <> 0
            GROUP BY stock_code ORDER BY n DESC LIMIT %s
            """,
            (args.n_stocks,),
        )
        stocks = [r[0] for r in cur.fetchall()]
    print("=" * 96)
    print(f"[2] 리더 실측 — SnsFeatures.get_daily_features (대상 {stocks})")

    sns = SnsFeatures()
    printed = 0
    for code in stocks:
        df = load_posts_df(conn, code)
        rows = sns.get_daily_features(code, df) if not df.empty else []
        if not rows:
            print(f"  {code}: posts={len(df)} → reader rows=0")
            continue
        nonzero_rows = [r for r in rows
                        if any(float(r[c]) != 0.0 for c in FEATURE_COLS)]
        print(f"  {code}: posts={len(df)} dates={len(rows)} "
              f"nonzero_dates={len(nonzero_rows)}")
        for r in rows[-min(len(rows), 2):]:
            vals = {k: round(float(r[k]), 4) for k in FEATURE_COLS}
            print(f"      {r['trade_date']} {vals}")
            printed += 1

    print("=" * 96)
    print("[3] 리더 실측 — SnsLagFeatures.get_all_features (DB 직접 조회)")
    lag = SnsLagFeatures()
    for code in stocks:
        res = lag.get_all_features(code, conn)
        nz = {k: v for k, v in res.items() if k != "stock_code" and float(v) != 0.0}
        print(f"  {code}: nonzero_keys={len(nz)}/{len(res) - ('stock_code' in res)}")
        print(f"      {nz}")

    print("=" * 96)
    print(f"[4] (종목,일) 쌍 {args.pairs}개 피처값 — 리더 계산 결과")
    shown = 0
    for code in stocks:
        df = load_posts_df(conn, code)
        rows = sns.get_daily_features(code, df) if not df.empty else []
        for r in rows:
            if shown >= args.pairs:
                break
            if float(r["attention_score"]) == 0.0 and float(r["sentiment_score"]) == 0.0:
                continue
            print(f"  {code} {r['trade_date']} "
                  + " ".join(f"{c}={round(float(r[c]), 4)}" for c in FEATURE_COLS))
            shown += 1
        if shown >= args.pairs:
            break

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
