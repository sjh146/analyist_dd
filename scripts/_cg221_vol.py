#!/usr/bin/env python3
"""CG221 — h5 초과수익(비중첩 Δ+2.391%p, t 3.38)이 **변동성 노출**의 부산물인가.

왜 (2026-11-13 01:2x 엔지니어 자율 틱)
  CG219 는 '단순 모멘텀'(return_5d)과 '종목 고정효과'(quality_roa)를 기각했다. 남은 교란은 변동성·베타다.
  고변동 종목은 5일 수익의 평균과 분산이 함께 커지므로, top5 의 부호 있는 평균 Δ 가 '선택 실력'이 아니라
  '변동성 베팅'일 수 있다(CG213 에서 top-k sd 2.12 vs 풀 0.92 로 이미 같은 정황).
  구분법:
    (a) 원 Δ (top5 − 풀)
    (b) **변동성 매칭 널** — top5 각 종목을 같은 변동성 5분위의 무작위 종목으로 치환한 바스켓과 비교
    (c) **위험조정 Δ** — 수익을 20일 실현변동성으로 나눈 값으로 비교

읽기 전용·학습 0회. 패널 close 로 20일 변동성 산출.
재현:
  docker exec stock_xgboost_ml sh -c 'cd /app && python -u scripts/_cg221_vol.py \
      --panel /app/app/models/wf/panel_prod200.npz --preds /app/reports/overnight/cg214_preds.jsonl \
      --tag CO_smooth_d1_h5 --out /app/reports/overnight/cg221_vol.json'
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fillable_expectancy import FEE_BUY, FEE_SELL, TAX_SELL  # noqa: E402

RT = (FEE_BUY + FEE_SELL + TAX_SELL) * 100.0


def st(v):
    v = [x for x in v if x is not None]
    n = len(v)
    if n < 2:
        return {"n": n}
    m = sum(v) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in v) / (n - 1))
    return {"n": n, "mean": round(m, 4), "sd": round(sd, 4),
            "t": round(m / (sd / math.sqrt(n)), 2) if sd else 0.0,
            "pos_pct": round(100 * sum(1 for x in v if x > 0) / n, 1)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", required=True)
    ap.add_argument("--preds", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--vol-win", type=int, default=20)
    ap.add_argument("--draws", type=int, default=200)
    ap.add_argument("--seed", type=int, default=20261113)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    rng = np.random.default_rng(a.seed)
    d = np.load(a.panel, allow_pickle=True)
    codes = np.asarray(d["codes"]).astype(str)
    dates = np.asarray(d["dates"]).astype(str)
    px = np.asarray(d["price"], dtype=float)
    close = {(codes[i], dates[i]): px[i] for i in range(len(codes))}
    all_dates = sorted(set(dates.tolist()))
    idx = {dt: n for n, dt in enumerate(all_dates)}
    universe = sorted(set(codes.tolist()))

    score = {}
    with open(a.preds, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line) if line.strip() else None
            if r and r.get("exp") == a.tag:
                score.setdefault(str(r["date"]), {})[str(r["code"]).zfill(6)] = float(r["y_pred"])

    def ret(c, dt, h):
        i = idx.get(dt)
        if i is None or i + h >= len(all_dates):
            return None
        c0, c1 = close.get((c, dt)), close.get((c, all_dates[i + h]))
        if not c0 or not c1:
            return None
        return (c1 / c0 - 1.0) * 100.0 - RT

    def vol(c, dt):
        i = idx.get(dt)
        if i is None or i - a.vol_win < 1:
            return None
        r = []
        for j in range(i - a.vol_win + 1, i + 1):
            c0, c1 = close.get((c, all_dates[j - 1])), close.get((c, all_dates[j]))
            if c0 and c1:
                r.append(c1 / c0 - 1.0)
        if len(r) < a.vol_win // 2:
            return None
        return float(np.std(r) * 100.0)

    raw, matched, adj = [], [], []
    top_vols, pool_vols = [], []
    for n, dt in enumerate(all_dates):
        sc = score.get(dt)
        if not sc:
            continue
        cs = [c for c in universe if (c, dt) in close]
        pr = {c: ret(c, dt, a.horizon) for c in cs}
        pr = {c: v for c, v in pr.items() if v is not None}
        if len(pr) < 20:
            continue
        pm = float(np.mean(list(pr.values())))
        top = sorted(sc, key=lambda c: -sc[c])[:a.k]
        top = [c for c in top if c in pr]
        if len(top) < 2:
            continue
        bm = float(np.mean([pr[c] for c in top]))
        raw.append(bm - pm)

        vols = {c: vol(c, dt) for c in pr}
        vols = {c: v for c, v in vols.items() if v}
        if len(vols) < 20 or any(c not in vols for c in top):
            continue
        arr = np.array(sorted(vols.values()))
        edges = [arr[int(q * (len(arr) - 1))] for q in (0.2, 0.4, 0.6, 0.8, 1.0)]

        def dec(v):
            for qi, e in enumerate(edges):
                if v <= e + 1e-12:
                    return qi
            return 4

        buckets = defaultdict(list)
        for c, v in vols.items():
            buckets[dec(v)].append(c)
        picks = []
        for c in top:
            pool_c = buckets[dec(vols[c])]
            if pool_c:
                picks.append(pr[rng.choice(pool_c)])
        if len(picks) == a.k:
            matched.append(bm - float(np.mean(picks)))
        # 위험조정: 수익/변동성
        adj.append(float(np.mean([pr[c] / max(vols[c], 1e-9) for c in top]))
                   - float(np.mean([pr[c] / max(vols[c], 1e-9) for c in vols])))
        top_vols.append(float(np.mean([vols[c] for c in top])))
        pool_vols.append(float(np.mean(list(vols.values()))))

    out = {"metric_name": "vol_beta_decomp", "tag": a.tag, "k": a.k, "horizon": a.horizon,
           "fee_roundtrip_pct": round(RT, 4), "n_sessions": len(raw),
           "raw_delta": st(raw), "vol_matched_delta": st(matched),
           "risk_adjusted_delta": st(adj),
           "top_vol_mean": round(float(np.mean(top_vols)), 3) if top_vols else None,
           "pool_vol_mean": round(float(np.mean(pool_vols)), 3) if pool_vols else None}
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f" n={out['n_sessions']} raw Δ{out['raw_delta']} | vol-matched Δ{out['vol_matched_delta']} | "
          f"risk-adj Δ{out['risk_adjusted_delta']} | top vol {out['top_vol_mean']} vs pool {out['pool_vol_mean']}")
    print(f"[ok] {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
