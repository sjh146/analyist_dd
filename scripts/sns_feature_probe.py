#!/usr/bin/env python3
"""SNS 피처 실측 프로브 — 리더의 **날짜 기반 조회**와 커버리지를 숫자로 낸다.

실행(stock_xgboost_ml 컨테이너, cwd=/app):
    python -u scripts/sns_feature_probe.py [--pairs 3] [--reader-stocks 250]

출력
----
[1] sns_post_features 전역 커버리지: 피처별 비영 행수/비율, 표준편차, distinct.
[2] 리더 경로(``SnsFeatures.get_daily_features``)로 재계산한 250종목 커버리지
    (비영 비율·std) + 종목별 커버 일수 분포.
[3] (종목, 날짜) 쌍별 실측값 — ``compute_for_stock(code, conn, date=d)`` 와
    ``SnsLagFeatures.get_all_features(code, conn, date=d)`` (날짜별 변동 확인).
[4] 룩어헤드 차단 검증 — 같은 종목에서 날짜를 바꿔가며 값이 달라지는지.
"""
import argparse
import os
import sys
from statistics import pstdev

import psycopg2

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

import pandas as pd  # noqa: E402

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
    if not df.empty:
        df["stock_code"] = stock_code
    return df


def stats(values, tol: float = 1e-6):
    """비영 통계. ``tol`` 이상만 '비영'으로 센다.

    리더 출력에는 부동소수 잔차(예: 미보강 author_quality = 4.23e-09)가 섞이므로
    ``!= 0.0`` 으로 세면 실제와 크게 어긋난다(실측: author_quality 비영 비율이
    raw 0.922 vs tol 0.371). DB 컬럼은 numeric(6,4) 라 0.0000 으로 반올림된다.
    """
    if not values:
        return {"n": 0}
    raw = sum(1 for v in values if float(v) != 0.0)
    nz = sum(1 for v in values if abs(float(v)) > tol)
    return {
        "n": len(values),
        "nonzero": nz,
        "nonzero_ratio": round(nz / len(values), 4),
        "nonzero_raw": raw,
        "std": round(pstdev([float(v) for v in values]), 5) if len(values) > 1 else 0.0,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", type=int, default=3, help="(종목,날짜) 쌍 수")
    ap.add_argument("--reader-stocks", type=int, default=250,
                    help="리더 경로로 재계산할 종목 수")
    ap.add_argument("--date-stock", type=str, default=None,
                    help="날짜 변동 검증에 쓸 종목 (기본: 커버리지 1위)")
    ap.add_argument("--pair-dates", type=str, default=None,
                    help="쉼표 구분 날짜들 — 각 날짜에서 post_count 최대 종목을 쌍으로")
    args = ap.parse_args()

    conn = connect()
    cur = conn.cursor()
    print("=" * 100)
    cur.execute("""SELECT count(*), count(DISTINCT stock_code),
                          count(DISTINCT posted_at::date),
                          min(posted_at::date)::text, max(posted_at::date)::text
                   FROM sns_posts WHERE posted_at IS NOT NULL""")
    print("[0] sns_posts: rows=%d stocks=%d distinct_days(전역)=%d %s~%s" % cur.fetchone())
    cur.execute("""SELECT count(*), count(DISTINCT stock_code), count(DISTINCT trade_date),
                          min(trade_date)::text, max(trade_date)::text
                   FROM sns_post_features""")
    print("    sns_post_features: rows=%d stocks=%d days=%d %s~%s" % cur.fetchone())
    cur.execute("""SELECT min(d), percentile_disc(0.25) within group (order by d),
                          percentile_disc(0.5) within group (order by d),
                          percentile_disc(0.75) within group (order by d), max(d)
                   FROM (SELECT stock_code, count(DISTINCT posted_at::date) d
                         FROM sns_posts GROUP BY 1) t""")
    print("    종목별 커버 일수(sns_posts): min=%s p25=%s median=%s p75=%s max=%s"
          % cur.fetchone())

    # ── [1] 테이블 전역 커버리지 ────────────────────────────────────────
    print("=" * 100)
    print("[1] sns_post_features 전역 커버리지 (전 행)")
    print(f"  {'feature':<20} {'rows':>6} {'nonzero':>8} {'ratio':>7} {'std':>9} {'distinct':>8} {'min':>9} {'max':>9}")
    for col in FEATURE_COLS:
        cur.execute(
            f"""SELECT count(*), count(*) FILTER (WHERE {col} <> 0),
                       stddev_pop({col}), count(DISTINCT {col}), min({col}), max({col})
                FROM sns_post_features"""
        )
        n, nz, sd, dc, mn, mx = cur.fetchone()
        ratio = (nz / n) if n else 0.0
        print(f"  {col:<20} {n:>6} {nz:>8} {ratio:>7.3f} "
              f"{(float(sd) if sd is not None else 0.0):>9.5f} {dc:>8} "
              f"{(float(mn) if mn is not None else 0.0):>9.4f} "
              f"{(float(mx) if mx is not None else 0.0):>9.4f}")

    # ── [2] 리더 경로 재계산 (전 종목) ──────────────────────────────────
    print("=" * 100)
    cur.execute("SELECT DISTINCT stock_code FROM sns_posts WHERE posted_at IS NOT NULL "
                "ORDER BY stock_code")
    codes = [r[0] for r in cur.fetchall()][: args.reader_stocks]
    sns = SnsFeatures()
    per_feature = {c: [] for c in FEATURE_COLS}
    per_stock_days = {}
    empty = 0
    for code in codes:
        df = load_posts_df(conn, code)
        rows = sns.get_daily_features(code, df) if not df.empty else []
        if not rows:
            empty += 1
            continue
        per_stock_days[code] = len(rows)
        for r in rows:
            for c in FEATURE_COLS:
                per_feature[c].append(r[c])
    print(f"[2] 리더 재계산: 종목 {len(codes)}개 (빈 결과 {empty}개), "
          f"총 (종목,일) 행 {sum(per_stock_days.values())}")
    print(f"  {'feature':<20} {'rows':>7} {'nz>1e-6':>8} {'ratio':>7} {'nz(raw)':>8} {'std':>10}")
    for c in FEATURE_COLS:
        s = stats(per_feature[c])
        if s["n"] == 0:
            print(f"  {c:<20} {0:>7}")
            continue
        print(f"  {c:<20} {s['n']:>7} {s['nonzero']:>8} {s['nonzero_ratio']:>7.3f} "
              f"{s['nonzero_raw']:>8} {s['std']:>10.5f}")
    if per_stock_days:
        ds = sorted(per_stock_days.values())
        print(f"  종목별 일수: min={ds[0]} p25={ds[len(ds)//4]} median={ds[len(ds)//2]} "
              f"p75={ds[3*len(ds)//4]} max={ds[-1]}")

    # ── [3] (종목, 날짜) 쌍 실측 ────────────────────────────────────────
    print("=" * 100)
    cur.execute(
        """SELECT stock_code, trade_date
           FROM sns_post_features
           WHERE attention_score <> 0
           GROUP BY stock_code, trade_date
           ORDER BY trade_date DESC, stock_code
           LIMIT %s""",
        (max(args.pairs, 1),),
    )
    pairs = [(r[0], r[1]) for r in cur.fetchall()]
    if args.pair_dates:
        pairs = []
        for d in [x.strip() for x in args.pair_dates.split(",") if x.strip()]:
            cur.execute(
                """SELECT stock_code FROM sns_post_features
                   WHERE trade_date = %s AND post_count > 0
                   ORDER BY post_count DESC LIMIT 1""",
                (d,),
            )
            row = cur.fetchone()
            if row:
                pairs.append((row[0], d))
            else:
                print(f"  (날짜 {d} 에 sns_post_features 행 없음 — 건너뜀)")
    lag = SnsLagFeatures()
    print(f"[3] (종목, 날짜) {len(pairs)}쌍 — 날짜 인자 조회 실측")
    for code, d in pairs:
        rows = sns.compute_for_stock(code, conn, date=d)
        print(f"  --- {code} @ {d} (compute_for_stock date={d}) → 행 {len(rows)}")
        if rows:
            r = rows[0]
            print("      " + " ".join(f"{c}={round(float(r[c]), 4)}" for c in FEATURE_COLS))
        lr = lag.get_all_features(code, conn, date=d)
        nz = {k: round(float(v), 4) for k, v in lr.items()
              if k != "stock_code" and float(v) != 0.0}
        print(f"      sns_lag nonzero {len(nz)}/16: {nz}")

    # ── [4] 날짜별 변동 ─────────────────────────────────────────────────
    print("=" * 100)
    target = args.date_stock
    if not target and per_stock_days:
        target = max(per_stock_days.items(), key=lambda kv: kv[1])[0]
    print(f"[4] 날짜 변동 검증 — 종목 {target}")
    if target:
        cur.execute(
            """SELECT trade_date FROM sns_post_features
               WHERE stock_code = %s ORDER BY trade_date DESC LIMIT 8""",
            (target,),
        )
        days = [r[0] for r in cur.fetchall()]
        print(f"  {'date':<12} " + " ".join(f"{c[:12]:>13}" for c in
                                            ["sentiment_score", "attention_score",
                                             "momentum_score", "author_quality_score",
                                             "post_count", "kalman_activity"]))
        seen = []
        for d in sorted(days):
            rows = sns.compute_for_stock(target, conn, date=d)
            if not rows:
                print(f"  {str(d):<12} (해당 일자 게시글 없음 → [])")
                continue
            r = rows[0]
            seen.append(tuple(round(float(r[c]), 6) for c in
                              ["sentiment_score", "attention_score", "momentum_score",
                               "author_quality_score", "post_count", "kalman_activity"]))
            print(f"  {str(d):<12} " + " ".join(
                f"{round(float(r[c]), 6):>13}" for c in
                ["sentiment_score", "attention_score", "momentum_score",
                 "author_quality_score", "post_count", "kalman_activity"]))
        print(f"  distinct 값 행(벡터) 수: {len(set(seen))}/{len(seen)}"
              "  → 1보다 크면 날짜별로 값이 변한다")
        # 룩어헤드 검증: date=D 조회의 post_count 가 '그 날짜까지'의 값인지.
        if days:
            d0 = max(days)
            rows = sns.compute_for_stock(target, conn, date=d0)
            if rows:
                cur.execute(
                    """SELECT count(*) FROM sns_posts
                       WHERE stock_code = %s AND posted_at::date = %s""",
                    (target, d0),
                )
                db_day = cur.fetchone()[0]
                print(f"  검증: {d0} post_count(reader)={rows[0]['post_count']} / "
                      f"DB 당일 게시글={db_day} (일치해야 정상)")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
