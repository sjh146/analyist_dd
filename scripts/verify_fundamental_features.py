#!/usr/bin/env python3
"""밸류/퀄리티(재무) 피처 실측 검증 — 읽기 전용.

stock_xgboost_ml 컨테이너에서 실행:
  docker exec -i stock_xgboost_ml python - < scripts/verify_fundamental_features.py --codes 005930,000660
  (또는 컨테이너 안에서) python /app/scripts/verify_fundamental_features.py --n 5

리더 코드(app/feature_engine/*)는 호출만 하고 수정하지 않는다.
출력: 피처별 값 + 비영/비상수 판정.
"""
import argparse
import json
import os
import sys
from collections import Counter, OrderedDict

import psycopg2
import pandas as pd

sys.path.insert(0, "/app")

from app.feature_engine.factor_features import FactorFeatures  # noqa: E402
from app.feature_engine.company_features import CompanyFeatures  # noqa: E402
from app.feature_engine.feature_pipeline import FeaturePipeline  # noqa: E402
from app.feature_engine.market_data_filter import MARKET_DATA_VALID  # noqa: E402

TRACKED = [
    "value_per", "value_pbr", "value_psr", "value_pcr", "value_ncav",
    "value_ev_ebit", "value_pfcr",
    "quality_cp_to_assets", "quality_op_to_equity", "quality_roe",
    "quality_roa", "quality_f_score", "quality_asset_growth",
    "quality_debt_ratio_change", "quality_op_growth",
    "quality_earnings_volatility", "quality_price_volatility_60d",
    "quality_beta",
    "momentum_1m_reverse", "momentum_3_12m", "momentum_op", "momentum_ni",
    "per_current", "pbr_current", "roe", "debt_ratio",
    "revenue", "operating_profit", "net_income",
    "op_margin", "net_margin", "revenue_growth_yoy", "op_margin_change_yoy",
    "per_percentile", "pbr_percentile",
    "market_cap_log",
]


def db_conn():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", default="", help="콤마 구분 종목코드")
    ap.add_argument("--n", type=int, default=5, help="무작위 종목 수")
    ap.add_argument("--date", default=None)
    ap.add_argument("--pairs", default="",
                    help="code:date 쌍(콤마 구분) — FeaturePipeline.build_features(code,date) 를 "
                         "그대로 호출하는 프로덕션 경로 검증")
    ap.add_argument("--pipeline", action="store_true", help="FeaturePipeline.build_features 사용")
    ap.add_argument("--json", default="", help="결과 JSON 저장 경로")
    args = ap.parse_args()

    conn = db_conn()
    cur = conn.cursor()

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    else:
        cur.execute(
            "SELECT DISTINCT stock_code FROM market_data ORDER BY stock_code LIMIT %s",
            (args.n,),
        )
        codes = [r[0] for r in cur.fetchall()]
    cur.close()

    pipeline = FeaturePipeline(pg_conn=conn) if (args.pipeline or args.pairs) else None
    factors = FactorFeatures()
    company = CompanyFeatures()

    results = OrderedDict()

    # --pairs: 프로덕션 경로(FeaturePipeline.build_features(code, date)) 그대로 호출
    if args.pairs:
        for p in [x for x in args.pairs.split(",") if x.strip()]:
            code, _, d = p.strip().partition(":")
            feats = pipeline.build_features(code, d or None)
            results[f"{code}@{d}"] = {k: feats.get(k, "MISSING") for k in TRACKED}
        pipeline = None  # 아래 루프는 건너뛴다

    for code in ([] if results else codes):
        cur = conn.cursor()
        cur.execute(
            f"""SELECT trade_date, open_price, high_price, low_price, close_price, volume
                FROM market_data WHERE stock_code = %s AND {MARKET_DATA_VALID}
                ORDER BY trade_date DESC LIMIT 250""",
            (code,),
        )
        rows = cur.fetchall()
        cur.close()
        mdf = pd.DataFrame(rows, columns=["trade_date", "open", "high", "low", "close", "volume"])
        if not mdf.empty:
            mdf = mdf.sort_values("trade_date").reset_index(drop=True)

        cur = conn.cursor()
        cur.execute("SELECT market_cap FROM stocks WHERE stock_code=%s", (code,))
        mc = cur.fetchone()
        cur.close()

        feats = {}
        feats.update(factors.get_all_factors(code, mdf if not mdf.empty else None, conn))
        feats.update(company.get_all_features(code, conn))
        feats["market_cap_log"] = float(mc[0]) if mc and mc[0] else 0.0
        if pipeline is not None:
            feats.update(pipeline.build_features(code, args.date, mdf if not mdf.empty else None))
        results[code] = {k: feats.get(k, "MISSING") for k in TRACKED}

    conn.close()

    print("=" * 78)
    print("종목별 피처 값")
    print("=" * 78)
    for code, vals in results.items():
        print(f"\n[{code}]")
        for k, v in vals.items():
            print(f"  {k:<32} {v}")

    print("\n" + "=" * 78)
    print("요약: 비영(non-zero) / 비상수(non-constant) 판정")
    print("=" * 78)
    print(f"{'feature':<32} {'non-zero':<10} {'values':<40}")
    alive, dead = [], []
    for k in TRACKED:
        vals = [v for v in (r[k] for r in results.values()) if isinstance(v, (int, float))]
        if not vals:
            print(f"{k:<32} {'NO-DATA':<10}")
            dead.append(k)
            continue
        c = Counter(round(v, 6) for v in vals)
        nonzero = any(abs(v) > 1e-12 for v in vals)
        nonconst = len(c) > 1
        tag = "ALIVE" if (nonzero and nonconst) else ("CONST" if not nonconst else "ZERO")
        (alive if tag == "ALIVE" else dead).append(k)
        sample = ",".join(f"{v:.4g}" for v in vals[:4])
        print(f"{k:<32} {str(nonzero):<10} {sample:<40} {tag}")

    print(f"\nALIVE({len(alive)}): {alive}")
    print(f"NOT-ALIVE({len(dead)}): {dead}")

    if args.json:
        with open(args.json, "w") as f:
            json.dump({"results": results, "alive": alive, "dead": dead}, f,
                      ensure_ascii=False, indent=2)
        print(f"저장: {args.json}")


if __name__ == "__main__":
    main()
