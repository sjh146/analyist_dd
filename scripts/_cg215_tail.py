#!/usr/bin/env python3
"""CG215 — CG214 의 짝 Δ 가 **꼬리 소수 세션**이 만든 값인지 분해한다 (읽기 전용, 학습 0회).

왜 (2026-11-13 01:0x 엔지니어 자율 틱, CG214 실측 근거)
  CG214 는 표본을 11→225 세션으로 늘렸다(MDE 1.276%p → 0.110~0.200%p). 그 위에서
  CO_smooth_d1_h5 vs CO_core30_h5 의 k=5 짝 Δ = **+0.135%p (t 1.50)** 로 문턱(+0.1%p)을 넘었으나
  t<2 로 미검출 판정됐다. 그런데 같은 표의 **중앙값이 부호 반대**다:
    arm k=5 median +0.1006 · control +0.1702 → 중앙값 Δ ≈ **−0.070**.
  평균이 양(+)이고 중앙값이 음(−)이면 그 평균은 소수 세션의 큰 값이 만든 것이다(세션 최악 −13.2%p).
  승격 문턱은 평균으로 판정하지만, 승격 근거가 '꼬리 5세션'에 걸려 있으면 그 arm 은 배포 후 재현되지 않는다.
  그래서 여기서는 (a) 중앙값·10% 절사평균 (b) 최고 Δ 세션 5개 제외 (c) 상위 꼬리 기여 집중도
  로 짝 Δ 를 분해한다. 판정 입력은 mean_delta_leave5out 이다.

재현:
  docker exec stock_xgboost_ml sh -c 'cd /app && python -u scripts/_cg215_tail.py \
      --jsonl /app/reports/overnight/cg214_preds.jsonl --arm CO_smooth_d1_h5 --control CO_core30_h5 \
      --out /app/reports/overnight/cg215_tail.json'
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fillable_topk_expectancy as F  # noqa: E402

import numpy as np  # noqa: E402


def trimmed(vals, frac=0.10):
    v = sorted(vals)
    n = len(v)
    cut = int(n * frac)
    core = v[cut:n - cut] if n - 2 * cut > 2 else v
    return float(np.mean(core)), len(core)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", required=True)
    ap.add_argument("--arm", required=True)
    ap.add_argument("--control", required=True)
    ap.add_argument("--k", default="3,5,10")
    ap.add_argument("--exit", default="next_open", choices=F.EXITS)
    ap.add_argument("--horizon", type=int, default=1)
    ap.add_argument("--min-value", type=float, default=1e9)
    ap.add_argument("--max-day-chg", type=float, default=25.0)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    ks = [int(x) for x in a.k.split(",") if x.strip()]
    rt = (F.FEE_BUY + F.FEE_SELL + F.TAX_SELL) * 100.0
    ns = SimpleNamespace(exclude_limit_up=True, max_day_chg=a.max_day_chg,
                         min_value=a.min_value, min_price=None, max_price=None)

    out = {"metric_name": "fillable_topk_tail_decomp", "exit": a.exit, "horizon": a.horizon,
           "fee_roundtrip_pct": round(rt, 4), "arm_tag": a.arm, "control_tag": a.control,
           "conditions": {}}

    for side, on in (("fillable", True), ("unfiltered", False)):
        arm, bad_a = F.load_rows(a.jsonl, a.arm)
        ctl, bad_c = F.load_rows(a.jsonl, a.control)
        codes = sorted({r["code"] for r in arm} | {r["code"] for r in ctl})
        import pandas as pd
        dates = [r["date"] for r in arm] + [r["date"] for r in ctl]
        d0 = (pd.Timestamp(min(dates)) - pd.Timedelta(days=6)).date()
        d1 = (pd.Timestamp(max(dates)) + pd.Timedelta(days=6 * (a.horizon + 2))).date()
        conn = F.db_connect()
        series = F.price_series(conn, codes, d0, d1)
        ea, _ = F.enrich(arm, series, a.horizon, a.exit)
        ec, _ = F.enrich(ctl, series, a.horizon, a.exit)
        fa, _ = F.apply_filters(ea, ns, on)
        fc, _ = F.apply_filters(ec, ns, on)
        A = F.baskets(fa, ks, rt)
        C = F.baskets(fc, ks, rt)
        blk = {}
        for k in ks:
            keys = sorted(set(A[k]) & set(C[k]))
            dl = [A[k][x] - C[k][x] for x in keys]
            if not dl:
                continue
            arr = np.array(dl)
            base = F.stat(dl)
            med = float(np.median(arr))
            tm, n_tm = trimmed(dl, 0.10)
            # 최고 Δ 세션 5개 제외(가장 후한 5세션을 빼도 남는가)
            order = np.argsort(-arr)
            lo5 = float(np.mean(np.delete(arr, order[:5]))) if len(arr) > 10 else None
            # 상위 꼬리 기여 집중도: Δ 합에서 상위 5세션이 차지하는 비중
            tot = float(arr.sum())
            top5 = float(arr[order[:5]].sum())
            blk[str(k)] = {
                "n_sessions": len(dl),
                "mean_delta": base.get("mean"), "t": base.get("t"),
                "pos": base.get("pos"), "neg": base.get("neg"),
                "median_delta": med,
                "trimmed10_delta": tm, "trimmed10_n": n_tm,
                "mean_delta_leave5out": lo5,
                "top5_share_of_sum_pct": (round(100.0 * top5 / tot, 1) if tot else None),
                "top5_sessions": [{"key": list(keys[i]), "delta": round(float(arr[i]), 3)} for i in order[:5]],
                "arm_mean": round(float(np.mean([A[k][x] for x in keys])), 4),
                "control_mean": round(float(np.mean([C[k][x] for x in keys])), 4),
                "arm_median": round(float(np.median([A[k][x] for x in keys])), 4),
                "control_median": round(float(np.median([C[k][x] for x in keys])), 4),
            }
        out["conditions"][side] = {"k": blk}

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    for side in out["conditions"]:
        print(f"== {side}")
        for k, v in out["conditions"][side]["k"].items():
            print(f" k={k:>2} n={v['n_sessions']} mean Δ{v['mean_delta']:+.3f}(t {v['t']}) "
                  f"median Δ{v['median_delta']:+.3f} trim10 Δ{v['trimmed10_delta']:+.3f} "
                  f"leave5out Δ{v['mean_delta_leave5out']:+.3f} top5share {v['top5_share_of_sum_pct']}%")
    print(f"[ok] {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
