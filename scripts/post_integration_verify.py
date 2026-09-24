#!/usr/bin/env python3
"""post_integration_verify — feature_pipeline 통합 패치 후 최종 검증.

여러 (종목, 날짜)에 대해 build_features 를 실제로 돌려
① 피처 개수(계약) ② 비영 피처 수 ③ 날짜에 따라 변하는 피처 수
④ 특정 그룹(이벤트/매크로/SNS/그래프/재무/시장폭)의 실측 값을 출력한다.
"""

import json
import os
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

import psycopg2  # noqa: E402

from app.feature_engine.feature_pipeline import FeaturePipeline  # noqa: E402

PAIRS = [
    ("005930", "2026-05-15"),
    ("005930", "2026-09-23"),
    ("000250", "2026-07-15"),
    ("000250", "2026-09-23"),
    ("247540", "2026-07-31"),
    ("247540", "2026-09-23"),
]

GROUPS = {
    "event": ("event_", "market_impact_score", "theme_exposure_5d"),
    "macro": ("fx_usd_krw", "fx_change_1m", "fx_change_3m", "oil_wti",
              "oil_change_1m", "oil_change_3m", "interest_rate"),
    "sns": ("sns_", "kalman_sentiment", "kalman_attention", "kalman_activity"),
    "graph": ("theme_count", "theme_max_relevance", "theme_momentum",
              "twin_count", "twin_avg_correlation", "cycle_up", "cycle_down"),
    "fund": ("value_", "quality_", "momentum_ni", "momentum_op", "per_current",
             "pbr_current", "roe", "momentum_3_12m"),
    "breadth": ("market_breadth", "krx_advance_decline_ratio",
                "krx_total_trading_value", "relative_strength"),
    "sent": ("sentiment_avg", "sentiment_avg_5d", "news_count_5d", "news_count_20d"),
}


def _is_zero(v):
    try:
        return float(v) == 0.0
    except (TypeError, ValueError):
        return v is None or v == ""


def _norm(v):
    try:
        return round(float(v), 10)
    except (TypeError, ValueError):
        return str(v)


def main():
    conn = psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )
    pipe = FeaturePipeline(pg_conn=conn)
    names = pipe.get_feature_names()
    print(f"[contract] get_feature_names() = {len(names)} 개")

    rows = {}
    for code, d in PAIRS:
        try:
            f = pipe.build_features(code, d)
        except Exception as e:
            print(f"[FAIL] {code} {d}: {type(e).__name__}: {e}")
            continue
        rows[(code, d)] = f

    if not rows:
        print("[FAIL] no feature rows built")
        return 1

    keys = sorted(set().union(*[set(f.keys()) for f in rows.values()]))
    print(f"[build] {len(rows)} (종목,날짜) 빌드 성공, 피처 키 {len(keys)} 개")

    by_stock = {}
    for (code, d), f in rows.items():
        by_stock.setdefault(code, []).append(((code, d), f))

    varying = set()
    constant_zero = set()
    allkeys = set()
    for code, items in by_stock.items():
        items.sort(key=lambda x: x[0][1])
        for k in set().union(*[set(f.keys()) for _, f in items]):
            vals = [f.get(k) for _, f in items]
            allkeys.add(k)
            if all(_is_zero(v) for v in vals):
                constant_zero.add(k)
            elif len(set(_norm(v) for v in vals)) > 1:
                varying.add(k)
    print(f"[date-varying] 날짜에 따라 변하는 피처 = {len(varying)} / {len(allkeys)}")
    print(f"[all-zero]      표본에서 전부 0 인 피처 = {len(constant_zero)}")

    print("\n[groups]")
    for g, pat in GROUPS.items():
        sel = [k for k in allkeys if any(k == p or k.startswith(p) for p in pat)]
        nz = [k for k in sel if any((f.get(k) or 0) != 0 for _, f in rows.items())]
        var = [k for k in sel if k in varying]
        print(f"  {g:8s} 피처 {len(sel):3d} | 비영 {len(nz):3d} | 날짜변화 {len(var):3d}")
        for k in sorted(sel)[:40]:
            if k in nz:
                sample = [round(float(f.get(k) or 0), 5) for (c, d), f in sorted(rows.items())]
                print(f"      {k:32s} {sample}")

    with open("/app/reports/post_integration_verify.json", "w") as fh:
        json.dump({
            "contract_count": len(names),
            "built_keys": len(allkeys),
            "date_varying": sorted(varying),
            "all_zero": sorted(constant_zero),
        }, fh, ensure_ascii=False, indent=2)
    print("\n[saved] /app/reports/post_integration_verify.json")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
