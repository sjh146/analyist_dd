#!/usr/bin/env python3
"""SNS 파이프라인 연결 검증 — ``SnsFeatureBundle`` 이 리더와 **동일한 값**을 내는지.

실행(stock_xgboost_ml 컨테이너, cwd=/app):
    python -u scripts/sns_pipeline_patch_check.py --stocks 005930,000660,005370

검증
----
1. ``feature_names()`` 26개 이름 (파이프라인 ``get_feature_names()`` 에 추가할 목록).
2. (종목,날짜) 쌍에서 bundle.load() 값 == 리더 직접 호출 값(compute_for_stock +
   get_all_features(date=...)) → 어긋나면 실패.
3. 날짜별 값 변동 (같은 종목, 다른 날짜 → 벡터가 다른지).
4. 캐시 워밍 후 호출 시간(종목·날짜 반복 호출 비용).
"""
import argparse
import os
import sys
import time

import psycopg2

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from app.feature_engine.sns_feature_bundle import SnsFeatureBundle, feature_names  # noqa: E402
from app.feature_engine.sns_features import SnsFeatures                            # noqa: E402
from app.feature_engine.sns_lag_features import SnsLagFeatures                     # noqa: E402


_TBL = ["sentiment_score", "attention_score", "momentum_score",
        "author_quality_score", "post_count", "bot_filtered_count",
        "kalman_sentiment", "kalman_attention", "kalman_momentum",
        "kalman_activity"]


def connect():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks", type=str, default="005930,000660,000930")
    ap.add_argument("--pairs", type=int, default=3)
    ap.add_argument("--dates", type=int, default=6)
    args = ap.parse_args()

    conn = connect()
    cur = conn.cursor()

    names = feature_names()
    print("=" * 100)
    print(f"[1] feature_names(): {len(names)}개")
    for n in names:
        print(f"    \"{n}\",")

    # 검증 대상 (종목,날짜): SNS 피처 행이 있는 최신 날짜 위주.
    stocks = [s.strip() for s in args.stocks.split(",") if s.strip()]
    cur.execute(
        """SELECT stock_code, trade_date FROM sns_post_features
           WHERE trade_date = (SELECT max(trade_date) - 1 FROM sns_post_features)
           ORDER BY attention_score DESC LIMIT %s""",
        (args.pairs,),
    )
    pairs = [(r[0], r[1]) for r in cur.fetchall()]
    pairs = pairs or [(stocks[0], None)]

    sns = SnsFeatures()
    lag = SnsLagFeatures()
    bundle = SnsFeatureBundle(pg_conn=conn)

    print("=" * 100)
    print("[2] bundle.load() vs 리더 직접 호출 — 값 일치 검증")
    ok = True
    for code, d in pairs:
        got = bundle.load(code, date=d)
        rows = sns.compute_for_stock(code, conn, date=d)
        ref = {}
        if rows:
            r = rows[0]
            for k in ("sentiment_score", "attention_score", "momentum_score",
                      "author_quality_score", "post_count", "bot_filtered_count"):
                ref[f"sns_{k}"] = float(r[k])
            for k in ("kalman_sentiment", "kalman_attention", "kalman_momentum",
                      "kalman_activity"):
                ref[k] = float(r[k])
        lref = lag.get_all_features(code, conn, date=d)
        for k, v in lref.items():
            if k != "stock_code":
                ref[k] = float(v)
        table_bundle = SnsFeatureBundle(pg_conn=conn, source="table")
        got_table = table_bundle.load(code, date=d)
        table_bundle.clear_cache()

        # (a) window 소스는 리더 직접 호출과 정확히 같아야 한다.
        win_bundle = SnsFeatureBundle(pg_conn=conn, source="window")
        got_win = win_bundle.load(code, date=d)
        win_bundle.clear_cache()
        diff = {k: (round(got_win.get(k, float("nan")), 6), round(v, 6))
                for k, v in ref.items()
                if abs(float(got_win.get(k, float("nan"))) - float(v)) > 1e-6}
        # (b) table 소스는 sns_post_features 행과 같아야 한다.
        cur.execute(
            f"""SELECT {', '.join(_TBL)} FROM sns_post_features
                WHERE stock_code = %s AND trade_date = %s""",
            (code, d),
        )
        trow = cur.fetchone()
        tdiff = {}
        if trow:
            cols = ["sentiment_score", "attention_score", "momentum_score",
                    "author_quality_score", "post_count", "bot_filtered_count",
                    "kalman_sentiment", "kalman_attention", "kalman_momentum",
                    "kalman_activity"]
            for name, val in zip(cols, trow):
                key = name if name.startswith("kalman_") else f"sns_{name}"
                if abs(float(got_table.get(key, float("nan"))) - float(val)) > 1e-6:
                    tdiff[key] = (got_table.get(key), float(val))
        nz = {k: round(v, 4) for k, v in got_table.items() if float(v) != 0.0}
        print(f"  --- {code} @ {d}: 키 {len(got_table)} / 비영 {len(nz)} / "
              f"window불일치 {len(diff)} / table불일치 {len(tdiff)}")
        if diff:
            ok = False
            print(f"      window 불일치: {diff}")
        if tdiff:
            ok = False
            print(f"      table 불일치: {tdiff}")
        print(f"      값(table 소스): {nz}")
    print(f"  결론: {'일치 (PASS)' if ok else '불일치 (FAIL)'}")

    # 날짜별 변동 + 타이밍.
    code = pairs[0][0]
    cur.execute(
        """SELECT trade_date FROM sns_post_features WHERE stock_code = %s
           ORDER BY trade_date DESC LIMIT %s""",
        (code, args.dates),
    )
    days = sorted(r[0] for r in cur.fetchall())
    bundle.clear_cache()
    print("=" * 100)
    print(f"[3] 날짜별 변동 — {code}")
    sigs = []
    for d in days:
        t0 = time.time()
        got = bundle.load(code, date=d)
        dt = (time.time() - t0) * 1000
        sig = tuple(round(got.get(k, 0.0), 6) for k in
                    ("sns_sentiment_score", "sns_attention_score",
                     "sns_momentum_score", "sns_author_quality_score",
                     "sns_post_count", "kalman_activity",
                     "sns_attention_score_corr0"))
        sigs.append(sig)
        print(f"  {d}  sns_sent={sig[0]:>9}  sns_att={sig[1]:>9}  sns_mom={sig[2]:>9}"
              f"  sns_authq={sig[3]:>9}  sns_posts={sig[4]:>7}  kalman_act={sig[5]:>8}"
              f"  att_corr0={sig[6]:>8}  ({dt:.1f}ms)")
    print(f"  distinct 벡터 수: {len(set(sigs))}/{len(sigs)}")
    t0 = time.time()
    for _ in range(20):
        bundle.load(code, date=days[-1])
    print(f"  캐시 워밍 후 호출 20회: {(time.time() - t0) / 20 * 1000:.1f}ms/회")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
