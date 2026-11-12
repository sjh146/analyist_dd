#!/usr/bin/env python3
"""CG217 — h5(5일 보유) 초과수익이 **시간가변 정보**인가 **종목 고정효과(정적 선택)** 인가.

왜 (2026-11-13 01:2x 엔지니어 자율 틱)
  CG216/h1: 모델 top-k 백분위 0.497~0.509 = 무작위 k 와 구분 불가(225세션).
  그런데 같은 덤프를 **5일 보유(close_h h5)** 로 재면 top5 +1.776 vs 무작위 k 기대 +0.283 →
  Δ+1.494, 백분위 0.592(t 4.71). 즉 '1일엔 정보 없음, 5일엔 정보 있음'이 나왔다.
  이건 두 가지로 설명된다:
    (A) 시간가변 정보: 모델이 5일 드리프트를 실제로 고른다
    (B) 종목 고정효과: 패널 스크린 실측에서 최고 피처가 전부 **종목상수 재무**(quality_roa 0.5534 등)라
        모델 점수가 사실상 '같은 소수 종목'을 매일 지목한다 → 5일 수익은 그 정적 바스켓의 성적이고,
        panel_prod200 이 **현존 200종목**(생존편향)이라 정적 퀄리티 바스켓이 구조적으로 유리하다.
  구분법: 종목상수 피처 하나(quality_roa)만으로 만든 '고정 top5 바스켓'의 h5 초과수익을 같은
  프로토콜로 재고, 모델 top5 와 **지목 종목이 얼마나 겹치는지**(turnover)를 잰다.
    - 상수 바스켓도 Δ≈+1.5%p & 겹침 높음 → (B) 종목 고정효과. 순위 축·데이터 축 모두 무효.
    - 상수 바스켓 Δ≈0 & 겹침 낮음 → (A) 시간가변 정보. h5 축을 검증 실험으로 승격.

읽기 전용·학습 0회. 패널 자체 가격(close)으로 계산(DB 미사용).
재현:
  docker exec stock_xgboost_ml sh -c 'cd /app && python -u scripts/_cg217_const.py \
      --panel /app/app/models/wf/panel_prod200.npz --preds /app/reports/overnight/cg214_preds.jsonl \
      --tag CO_smooth_d1_h5 --out /app/reports/overnight/cg217_const.json'
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


def st(vals):
    v = [x for x in vals if x is not None]
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
    ap.add_argument("--const-feature", default="quality_roa")
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    d = np.load(a.panel, allow_pickle=True)
    fn = [str(x) for x in d["feature_names"]]
    ci = fn.index(a.const_feature)
    codes = np.asarray(d["codes"]).astype(str)
    dates = np.asarray(d["dates"]).astype(str)
    px = np.asarray(d["price"], dtype=float)
    X = np.asarray(d["X"], dtype=float)

    # (code, date) → close / 상수피처값
    close = {}
    cval = defaultdict(list)
    for i in range(len(codes)):
        close[(codes[i], dates[i])] = px[i]
        v = X[i, ci]
        if v == v:
            cval[codes[i]].append(float(v))

    # 종목별 상수피처 평균 → 고정 랭킹
    const_rank = sorted(cval, key=lambda c: -float(np.mean(cval[c])))
    const_top = set(const_rank[:a.k])

    # 모델 점수 로드
    score = defaultdict(dict)
    with open(a.preds, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("exp") != a.tag:
                continue
            score[str(r["date"])][str(r["code"]).zfill(6)] = float(r["y_pred"])

    all_dates = sorted(set(dates.tolist()))
    idx = {dt: n for n, dt in enumerate(all_dates)}
    keys = list(close)

    def ret(code, dt):
        i = idx.get(dt)
        if i is None or i + a.horizon >= len(all_dates):
            return None
        c0 = close.get((code, dt))
        c1 = close.get((code, all_dates[i + a.horizon]))
        if not c0 or not c1:
            return None
        return (c1 / c0 - 1.0) * 100.0 - RT

    pool_means, const_means, model_means, overlaps = [], [], [], []
    for dt in all_dates:
        cs = [c for c in set(codes.tolist()) if (c, dt) in close]
        rs = [ret(c, dt) for c in cs]
        rs = [x for x in rs if x is not None]
        if len(rs) < 20:
            continue
        pool_means.append(sum(rs) / len(rs))
        cm = [ret(c, dt) for c in const_top]
        cm = [x for x in cm if x is not None]
        if len(cm) >= 2:
            const_means.append(sum(cm) / len(cm))
        sc = score.get(dt)
        if sc:
            top = sorted(sc, key=lambda c: -sc[c])[:a.k]
            mm = [ret(c, dt) for c in top]
            mm = [x for x in mm if x is not None]
            if len(mm) >= 2:
                model_means.append(sum(mm) / len(mm))
                overlaps.append(len(set(top) & const_top) / a.k)

    out = {
        "metric_name": "const_feature_basket_decomp",
        "panel": os.path.basename(a.panel), "const_feature": a.const_feature,
        "k": a.k, "horizon": a.horizon, "fee_roundtrip_pct": round(RT, 4),
        "n_sessions": len(pool_means),
        "pool": st(pool_means),
        "const_basket": st(const_means),
        "model_basket": st(model_means),
        "delta_const_minus_pool": st([c - p for c, p in zip(const_means, pool_means)]),
        "delta_model_minus_pool": st([m - p for m, p in zip(model_means, pool_means)]),
        "delta_model_minus_const": st([m - c for m, c in zip(
            [x for x in model_means], [x for x in const_means])]) if len(model_means) == len(const_means) else None,
        "top_overlap_with_const_basket": (round(float(np.mean(overlaps)), 3) if overlaps else None),
        "const_top_codes": sorted(const_top),
    }
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    for kk in ("pool", "const_basket", "model_basket", "delta_const_minus_pool",
               "delta_model_minus_pool", "delta_model_minus_const"):
        if out.get(kk):
            print(f" {kk:>26} {out[kk]}")
    print(f" top5 겹침(모델 vs 상수바스켓): {out['top_overlap_with_const_basket']} · 세션 {out['n_sessions']}")
    print(f"[ok] {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
