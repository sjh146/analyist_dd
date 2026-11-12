#!/usr/bin/env python3
"""CG216 — 모델 top-k 가 **무작위 top-k 대비** 유의한가 (같은 세션·같은 체결가능 집합·같은 수수료).

왜 (2026-11-13 01:1x 엔지니어 자율 틱)
  CG214 가 돈 지표 표본을 225 세션으로 늘렸고(MDE 0.110~0.200%p), CG215 는 그 위의 유일한 문턱
  통과 후보(k=5 Δ+0.135%p)가 **상위 5세션 기여 100.5% · 5세션 제외 시 Δ−0.001** 임을 보여 축을 닫았다.
  남은 질문은 원초적이다: **모델 점수로 고른 k종목이 같은 세션의 무작위 k종목보다 나은가?**
  프로토콜의 널 기준선은 '풀평균'이지만 풀평균은 k개 바스켓의 분산을 말해주지 않는다. 무작위 k 를
  2,000회 뽑아 세션별 백분위 p 를 만들면, 실력이 없을 때 p~Uniform(0,1)(평균 0.5)이므로
  '모델 top-k 의 p 평균'이 곧 검정 통계량이 된다. 이건 AUC 도 풀평균도 아닌 **직접적 순위 검정**이다.

읽기 전용·학습 0회·승격 없음.

재현:
  docker exec stock_xgboost_ml sh -c 'cd /app && python -u scripts/_cg216_null.py \
      --jsonl /app/reports/overnight/cg214_preds.jsonl --tags CO_smooth_d1_h5,CO_core30_h5 \
      --out /app/reports/overnight/cg216_null.json'
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fillable_topk_expectancy as F  # noqa: E402

import numpy as np  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", required=True)
    ap.add_argument("--tags", required=True)
    ap.add_argument("--k", default="3,5,10")
    ap.add_argument("--draws", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20261113)
    ap.add_argument("--exit", default="next_open", choices=F.EXITS)
    ap.add_argument("--horizon", type=int, default=1)
    ap.add_argument("--min-value", type=float, default=1e9)
    ap.add_argument("--max-day-chg", type=float, default=25.0)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    rng = np.random.default_rng(a.seed)
    ks = [int(x) for x in a.k.split(",") if x.strip()]
    tags = [t.strip() for t in a.tags.split(",") if t.strip()]
    rt = (F.FEE_BUY + F.FEE_SELL + F.TAX_SELL) * 100.0
    ns = SimpleNamespace(exclude_limit_up=True, max_day_chg=a.max_day_chg,
                         min_value=a.min_value, min_price=None, max_price=None)

    out = {"metric_name": "fillable_topk_random_null", "exit": a.exit, "horizon": a.horizon,
           "fee_roundtrip_pct": round(rt, 4), "draws": a.draws, "seed": a.seed, "tags": {}}

    for tag in tags:
        rows, bad = F.load_rows(a.jsonl, tag)
        if not rows:
            out["tags"][tag] = {"error": "no rows"}
            continue
        codes = sorted({r["code"] for r in rows})
        import pandas as pd
        d0 = (pd.Timestamp(min(r["date"] for r in rows)) - pd.Timedelta(days=6)).date()
        d1 = (pd.Timestamp(max(r["date"] for r in rows)) + pd.Timedelta(days=6 * (a.horizon + 2))).date()
        conn = F.db_connect()
        series = F.price_series(conn, codes, d0, d1)
        en, _ = F.enrich(rows, series, a.horizon, a.exit)
        fa, _ = F.apply_filters(en, ns, True)
        by_sess = defaultdict(list)
        for r in fa:
            by_sess[(r["fold"], r["date"])].append(r)

        blk = {}
        for k in ks:
            pcts, obs, nulls = [], [], []
            for key, rs in sorted(by_sess.items()):
                if len(rs) < max(k * 3, 12):
                    continue
                g = np.array([x["gross_pct"] for x in rs]) - rt
                sc = np.array([x["score"] for x in rs])
                idx = np.argsort(-sc)[:k]
                o = float(g[idx].mean())
                draws = np.array([g[rng.choice(len(g), size=k, replace=False)].mean()
                                  for _ in range(a.draws)])
                pcts.append(float((draws <= o).mean()))
                obs.append(o)
                nulls.append(float(draws.mean()))
            if not pcts:
                continue
            arr = np.array(pcts)
            se = float(arr.std(ddof=1) / np.sqrt(len(arr))) if len(arr) > 1 else None
            blk[str(k)] = {
                "n_sessions": len(arr),
                "obs_mean": round(float(np.mean(obs)), 4),
                "null_mean": round(float(np.mean(nulls)), 4),
                "obs_minus_null": round(float(np.mean(obs) - np.mean(nulls)), 4),
                "mean_percentile": round(float(arr.mean()), 4),
                "median_percentile": round(float(np.median(arr)), 4),
                "pct_above_95": round(float((arr >= 0.95).mean() * 100), 1),
                "pct_below_5": round(float((arr <= 0.05).mean() * 100), 1),
                "t_vs_uniform_half": (round(float((arr.mean() - 0.5) / se), 2) if se else None),
            }
        out["tags"][tag] = blk

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    for tag, blk in out["tags"].items():
        print(f"== {tag}")
        for k, v in blk.items():
            print(f" k={k:>2} n={v['n_sessions']} obs {v['obs_mean']:+.3f} null {v['null_mean']:+.3f} "
                  f"Δ{v['obs_minus_null']:+.3f} p평균 {v['mean_percentile']:.3f} "
                  f"(t {v['t_vs_uniform_half']}) >p95 {v['pct_above_95']}% <p05 {v['pct_below_5']}%")
    print(f"[ok] {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
