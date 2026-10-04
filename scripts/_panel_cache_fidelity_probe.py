#!/usr/bin/env python3
"""_panel_cache_fidelity_probe.py — 패널 캐시가 **값을 바꾸지 않는지** 직접 검증한다.

WHY(2026-10-04): 캐시는 조용히 틀린 숫자를 만들 수 있는 종류의 최적화다(가장 나쁜 실패 유형).
그래서 "캐시 on/off 로 같은 지표가 나오나"를 큰 격자로 두 번 돌리는 대신(≈26분), **객체 수준**에서
같은 행 집합을 ① 캐시 없이 새로 빌드 ② 캐시되어 저장된 것을 재로드 → 피처 해시가 동일한지 본다.
해시가 같으면 이후 계산은 정의상 같다.

컨테이너 안에서 실행:
  docker exec -w /app -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/_panel_cache_fidelity_probe.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

import model_metric_protocol_audit as mma  # noqa: E402
import swing_screener as ss  # noqa: E402
from app.feature_engine.feature_pipeline import FeaturePipeline  # noqa: E402


def digest(features_by_date) -> str:
    """피처행렬의 결정적 해시 — 키 정렬 + float 은 반올림해 부동소수 잡음 제거."""
    h = hashlib.sha256()
    for date in sorted(features_by_date):
        h.update(str(date).encode())
        for code in sorted(features_by_date[date]):
            f = features_by_date[date][code]
            items = f.get("features") if isinstance(f, dict) else None
            if isinstance(items, dict):
                payload = {k: (round(v, 10) if isinstance(v, float) else v)
                           for k, v in items.items()}
            else:
                payload = f
            h.update(json.dumps({"c": code, "f": payload}, sort_keys=True,
                                default=str).encode())
    return h.hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", type=int, default=3)
    ap.add_argument("--codes", type=int, default=6)
    a = ap.parse_args(argv)

    conn = ss.get_pg_conn()
    pipeline = FeaturePipeline(pg_conn=conn)

    # 행 집합: 최근 거래일 × 유동성 상위 종목(작게 — 해시 비교면 충분하다)
    with conn.cursor() as cur:
        cur.execute("select distinct trade_date from market_data order by trade_date desc limit %s",
                    (a.dates,))
        dates = [r[0] for r in cur.fetchall()]
        cur.execute("select distinct stock_code from market_data where trade_date = %s "
                    "order by stock_code limit %s", (dates[0], a.codes))
        codes = [r[0] for r in cur.fetchall()]
    rows = [(c, d) for d in dates for c in codes]
    print("[probe] 행 {0}개 (날짜 {1} × 종목 {2})".format(len(rows), len(dates), len(codes)))

    args = SimpleNamespace(folds=a.dates, dates_per_fold=1, stocks=a.codes,
                           horizon=5, label_kind="rel", topk=3)
    mma.set_cache_signature(args, conn)

    # ① 캐시 없이 새로 빌드
    mma._CACHE["enabled"] = False
    t0 = time.time()
    fresh = mma.build_features_for(list(rows), pipeline)
    t_fresh = time.time() - t0
    d_fresh = digest(fresh)

    # ② 캐시 켜고 같은 행 집합 → MISS(빌드+저장)
    mma._CACHE["enabled"] = True
    t0 = time.time()
    built = mma.build_features_for(list(rows), pipeline)
    t_build = time.time() - t0
    d_built = digest(built)

    # ③ 캐시 켜고 같은 행 집합 → HIT(재로드) — 값이 같아야 한다
    t0 = time.time()
    cached = mma.build_features_for(list(rows), pipeline)
    t_hit = time.time() - t0
    d_cached = digest(cached)

    ok = (d_fresh == d_built) and (d_cached == d_fresh)
    print("[probe] fresh   {0} ({1:.1f}s)".format(d_fresh[:16], t_fresh))
    print("[probe] built   {0} ({1:.1f}s)".format(d_built[:16], t_build))
    print("[probe] cached  {0} ({1:.1f}s)".format(d_cached[:16], t_hit))
    print("[probe] 일치: {0} · 속도 이득: {1:.1f}s → {2:.1f}s".format(
        "OK" if ok else "FAIL", t_build, t_hit))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
