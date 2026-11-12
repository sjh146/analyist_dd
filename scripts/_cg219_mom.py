#!/usr/bin/env python3
"""CG219 — 모델 top-k 의 h5 초과수익(+1.9~2.4%p)이 **단순 모멘텀 팩터**로 재현되는가.

왜 (2026-11-13 01:4x 엔지니어 자율 틱)
  CG218: 보유기간 곡선 실측(비중첩) — h1 Δ+0.533(t 2.83) · h3 +0.883(t 2.15) · h5 +2.391(t 3.38, 폴드 5/5) ·
  h10 +4.159(t 3.25). 즉 모델 랭킹에는 5일 축에 돈 정보가 있다. 그런데 모델 점수의 상위는 대개
  '최근 오른 종목'이다. 그러면 이득은 모델이 아니라 **모멘텀 팩터**이고, CG182 실측(모멘텀 부호가
  8~9월 +0.09 → 10~11월 −0.15 로 반전)이 배포 챔피언의 최근 음(−) 기대를 설명할 수 있다.
  대조군: 같은 날짜·같은 유니버스에서 **return_5d 상위 k**(모델 없음) 바스켓.
    - 모멘텀 대조군 Δ 도 비슷하면 → 모델 엣지 = 모멘텀. 순위 축은 '팩터'로 흡수되고,
      승격 후보는 모델이 아니라 팩터 규칙(비용·보유기간 최적화)이다.
    - 모델만 유의하면 → 모델 고유 정보. h5 축 검증 실험으로 승격.

읽기 전용·학습 0회. 패널 자체 close·feature 로 계산.
재현:
  docker exec stock_xgboost_ml sh -c 'cd /app && python -u scripts/_cg219_mom.py \
      --panel /app/app/models/wf/panel_prod200.npz --preds /app/reports/overnight/cg214_preds.jsonl \
      --tag CO_smooth_d1_h5 --out /app/reports/overnight/cg219_mom.json'
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

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
    ap.add_argument("--factor", default="return_5d")
    ap.add_argument("--ks", default="5")
    ap.add_argument("--horizons", default="1,5")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    d = np.load(a.panel, allow_pickle=True)
    fn = [str(x) for x in d["feature_names"]]
    fi = fn.index(a.factor)
    codes = np.asarray(d["codes"]).astype(str)
    dates = np.asarray(d["dates"]).astype(str)
    px = np.asarray(d["price"], dtype=float)
    X = np.asarray(d["X"], dtype=float)
    close, fval = {}, {}
    for i in range(len(codes)):
        close[(codes[i], dates[i])] = px[i]
        v = X[i, fi]
        if v == v:
            fval[(codes[i], dates[i])] = float(v)
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

    out = {"metric_name": "momentum_control", "panel": os.path.basename(a.panel),
           "factor": a.factor, "fee_roundtrip_pct": round(RT, 4), "tau_spearman": {}, "horizons": {}}

    ks = [int(x) for x in a.ks.split(",")]
    hs = [int(x) for x in a.horizons.split(",")]
    for h in hs:
        blk = {}
        for k in ks:
            md, fd, ov = [], [], []
            for n, dt in enumerate(all_dates):
                cs = [c for c in universe if (c, dt) in close]
                pool = [ret(c, dt, h) for c in cs]
                pool = [x for x in pool if x is not None]
                if len(pool) < 20:
                    continue
                pm = sum(pool) / len(pool)
                sc = score.get(dt)
                if sc:
                    top = sorted(sc, key=lambda c: -sc[c])[:k]
                    b = [x for x in (ret(c, dt, h) for c in top) if x is not None]
                    if len(b) >= 2:
                        md.append(sum(b) / len(b) - pm)
                        if n % h == 0:
                            ov.append(sum(b) / len(b) - pm)
                fsub = {c: fval[(c, dt)] for c in cs if (c, dt) in fval}
                if len(fsub) >= 10:
                    ftop = sorted(fsub, key=lambda c: -fsub[c])[:k]
                    fr = [x for x in (ret(c, dt, h) for c in ftop) if x is not None]
                    if len(fr) >= 2:
                        fd.append(sum(fr) / len(fr) - pm)
            blk[str(k)] = {"model_overlap": st(md), "model_nonoverlap": st(ov),
                           f"{a.factor}_topk": st(fd)}
        out["horizons"][str(h)] = blk

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    for h, blk in out["horizons"].items():
        for k, v in blk.items():
            m, mo, fct = v["model_overlap"], v["model_nonoverlap"], v[f"{a.factor}_topk"]
            print(f" h={h} k={k} 모델 Δ{m.get('mean'):+.3f}(t {m.get('t')}) · 비중첩 Δ{mo.get('mean'):+.3f}(t {mo.get('t')}, n{mo.get('n')})"
                  f" | {a.factor} Δ{fct.get('mean'):+.3f}(t {fct.get('t')}, n{fct.get('n')})")
    print(f"[ok] {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
