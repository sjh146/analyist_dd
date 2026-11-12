#!/usr/bin/env python3
"""CG218 — 보유기간 축: 모델 top-k 초과수익이 h1 에서는 0, h5 에서는 +1.5~2.0%p 라는 CG216/217 결과를
**비중첩 표본**으로 재검정하고 h1/h3/h5/h10 곡선을 낸다.

왜 (2026-11-13 01:3x 엔지니어 자율 틱)
  CG216(h1, 225세션): top-k 백분위 0.497~0.509 = 무작위 k 와 구분 불가.
  CG216h5/CG217(h5): top5 +1.78 vs 무작위 k 기대 +0.28 → Δ+1.49 (naive t 4.7) · 고정 상수바스켓
  (quality_roa)은 Δ−0.01(t −0.05)·겹침 6.2% → '정적 종목효과' 가설은 기각.
  그런데 5일 보유는 세션 간 창이 겹쳐 유효표본이 n/5(≈45)다. 겹침을 제거하지 않으면 t 가 최대 √5 배
  부풀려진다 → 비중첩(매 h번째 세션만 진입)으로 다시 재야 한다. 이 곡선이 '어느 보유기간에 정보가
  살아나는가'를 답하고, 배포 경로(현행 next_open h1)가 왜 돈이 안 되는지에 대한 직접 증거가 된다.

읽기 전용·학습 0회. 패널 자체 close 로 계산.
재현:
  docker exec stock_xgboost_ml sh -c 'cd /app && python -u scripts/_cg218_hold.py \
      --panel /app/app/models/wf/panel_prod200.npz --preds /app/reports/overnight/cg214_preds.jsonl \
      --tag CO_smooth_d1_h5 --out /app/reports/overnight/cg218_hold.json'
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
            "pos_pct": round(100 * sum(1 for x in v if x > 0) / n, 1),
            "median": round(sorted(v)[n // 2], 4)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", required=True)
    ap.add_argument("--preds", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--ks", default="3,5")
    ap.add_argument("--horizons", default="1,3,5,10")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    d = np.load(a.panel, allow_pickle=True)
    codes = np.asarray(d["codes"]).astype(str)
    dates = np.asarray(d["dates"]).astype(str)
    px = np.asarray(d["price"], dtype=float)
    close = {(codes[i], dates[i]): px[i] for i in range(len(codes))}
    all_dates = sorted(set(dates.tolist()))
    idx = {dt: n for n, dt in enumerate(all_dates)}
    universe = sorted(set(codes.tolist()))

    score = defaultdict(dict)
    with open(a.preds, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line) if line.strip() else None
            if r and r.get("exp") == a.tag:
                score[str(r["date"])][str(r["code"]).zfill(6)] = float(r["y_pred"])

    def ret(code, dt, h):
        i = idx.get(dt)
        if i is None or i + h >= len(all_dates):
            return None
        c0, c1 = close.get((code, dt)), close.get((code, all_dates[i + h]))
        if not c0 or not c1:
            return None
        return (c1 / c0 - 1.0) * 100.0 - RT

    ks = [int(x) for x in a.ks.split(",")]
    hs = [int(x) for x in a.horizons.split(",")]
    out = {"metric_name": "hold_horizon_curve", "panel": os.path.basename(a.panel),
           "tag": a.tag, "fee_roundtrip_pct": round(RT, 4), "horizons": {}}

    for h in hs:
        blk = {}
        for k in ks:
            ov_d, nov_d, folds = [], [], defaultdict(list)
            for n, dt in enumerate(all_dates):
                sc = score.get(dt)
                if not sc:
                    continue
                cs = [c for c in universe if (c, dt) in close]
                pool = [ret(c, dt, h) for c in cs]
                pool = [x for x in pool if x is not None]
                if len(pool) < 20:
                    continue
                top = sorted(sc, key=lambda c: -sc[c])[:k]
                b = [ret(c, dt, h) for c in top]
                b = [x for x in b if x is not None]
                if len(b) < 2:
                    continue
                dlt = sum(b) / len(b) - sum(pool) / len(pool)
                ov_d.append(dlt)
                if n % h == 0:                      # 비중첩 진입(매 h번째 세션)
                    nov_d.append(dlt)
                    folds[(n // h) % 5].append(dlt)
            blk[str(k)] = {
                "overlap": st(ov_d),
                "nonoverlap": st(nov_d),
                "fold_means_nonoverlap": {str(f): round(float(np.mean(v)), 3) for f, v in sorted(folds.items()) if v},
                "fold_signs_positive": f"{sum(1 for v in folds.values() if v and np.mean(v) > 0)}/{len(folds)}",
            }
        out["horizons"][str(h)] = blk

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    for h, blk in out["horizons"].items():
        for k, v in blk.items():
            o, nv = v["overlap"], v["nonoverlap"]
            print(f" h={h:>2} k={k} 중첩 Δ{o.get('mean'):+.3f}(t {o.get('t')}, n{o.get('n')}) | "
                  f"비중첩 Δ{nv.get('mean'):+.3f}(t {nv.get('t')}, n{nv.get('n')}) 폴드+ {v['fold_signs_positive']} {v['fold_means_nonoverlap']}")
    print(f"[ok] {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
