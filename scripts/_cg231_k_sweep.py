#!/usr/bin/env python3
"""CG231 — 현행 경로(A: 익일 시가 청산)에서 진입 폭(k)을 좁히면 자본-세션 기대가 오르는가.

왜 (2026-11-13 04:5x 엔지니어 자율 틱, 청산지평 축 종결 직후)
  청산지평 축은 5수단 전패로 닫혔다(손절 3.08~3.39x · 폭확대 6.0~13.0x · 레짐 2.91x ·
  지평 h2 1.49x → 부트스트랩 중앙 2.34x·18%만 ≤1.5x). 즉 '경로를 바꾸는' 레버는 없다.
  남은 것은 **현행 경로 그대로** 진입 선택을 개선하는 것이다 — 예측 순위의 상위를 몇 개만
  사는지(k)는 트레이더 한도(일 3건·종목 1/3) 안에서 바꿀 수 있는 유일한 축이다.

정의(재현): 진입 = 세션 종가, 청산 = 익일 시가, 체결성 필터 CG224 동일, 수수료 왕복 차감.
  k ∈ {1,2,3,5} · 자본-세션 = mean(세션별 top-k 평균) · 낙폭은 5일 묶음 코호트 MDD(공통 크기).
사전 등록: k<5 중 자본-세션 ≥ k5+0.05%p AND 코호트 낙폭 배수 ≤1.5x → '진입 폭 변경 검토'(트레이더 설정 k).
  없으면 진입 폭 축 종결.

재현:
  docker exec stock_xgboost_ml sh -c 'cd /app && timeout 900 python -u scripts/_cg231_k_sweep.py \
    --preds /app/reports/overnight/cg220_preds.jsonl --tag CO_smooth_d1_h5 --out /app/reports/overnight/cg231_k.json'
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _cg226_stop_path import RT, hl_series, mdd, stat  # noqa: E402
from fillable_topk_expectancy import apply_filters, db_connect, enrich, load_rows  # noqa: E402

KS = (1, 2, 3, 5)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preds", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--regime", choices=("all", "down", "up"), default="all",
                    help="진입 세션을 직전 세션 시장수익률 부호로 거르기(down=직전 음수)")
    a = ap.parse_args(argv)

    rows, bad = load_rows(a.preds, a.tag)
    if not rows:
        print(f"[FAIL] 행 없음 tag={a.tag}", file=sys.stderr)
        return 2
    codes = sorted({r["code"] for r in rows})
    dmin, dmax = min(r["date"] for r in rows), max(r["date"] for r in rows)
    import pandas as pd
    d0 = (pd.Timestamp(dmin) - pd.Timedelta(days=6)).date()
    d1 = (pd.Timestamp(dmax) + pd.Timedelta(days=6)).date()
    conn = db_connect()
    series = hl_series(conn, codes, d0, d1)
    ent, _ = enrich(rows, series, 1, "next_open")
    flt = SimpleNamespace(exclude_limit_up=True, max_day_chg=25.0, min_value=1e9,
                          min_price=None, max_price=None)
    ent, _ = apply_filters(ent, flt, on=True)
    by = {}
    for r in ent:
        by.setdefault(r["date"], []).append(r)
    dates = [d for d in sorted(by) if len(by[d]) >= 20]

    def _k_stats(k, dts):
        vals = [float(np.mean([r["gross_pct"] for r in
                               sorted(by[d], key=lambda x: -x["score"])[:k]])) - RT for d in dts]
        nov = [float(np.mean(vals[i:i + 5])) for i in range(0, len(vals) - len(vals) % 5, 5)]
        return vals, nov

    base_vals, base_nov = _k_stats(5, dates)          # A = k5 전체 세션(회귀 기준)
    a_cap = float(np.mean(base_vals))
    a_nov = mdd(base_nov)
    reg_ok = (abs(a_cap - 0.0521) <= 0.01 and abs(mdd(base_vals) - (-20.176)) <= 0.10)
    if not reg_ok:
        print(f"[FAIL] 회귀 불일치 — k5 {a_cap:+.4f}/MDD {mdd(base_vals)} (기준 +0.0521/-20.176)",
              file=sys.stderr)
        return 2

    dates_f = dates
    if a.regime != "all":
        from _cg228_regime import market_returns
        mkt = market_returns(series)
        ds = sorted(mkt)
        prev = {str(dt): (mkt[ds[u - 1]] if u else None) for u, dt in enumerate(ds)}
        dates_f = [d for d in dates
                   if prev.get(str(d)) is not None
                   and ((prev[str(d)] <= 0) if a.regime == "down" else (prev[str(d)] > 0))]
        if len(dates_f) < 60:
            print(f"[FAIL] 레짐 {a.regime} 세션 부족 {len(dates_f)}", file=sys.stderr)
            return 2

    variants, series_by_k = {}, {}
    for k in (KS if a.regime == "all" else (5,)):
        vals = [float(np.mean([r["gross_pct"] for r in
                               sorted(by[d], key=lambda x: -x["score"])[:k]])) - RT for d in dates_f]
        nov = [float(np.mean(vals[i:i + 5])) for i in range(0, len(vals) - len(vals) % 5, 5)]
        variants[f"k{k}"] = {"sessions": len(vals), **stat(vals),
                             "capital_session": round(float(np.mean(vals)), 4),
                             "mdd_pct": mdd(vals),
                             "nonoverlap": {"n_cohorts": len(nov), "mdd_pct": mdd(nov),
                                            "mean_pct": round(float(np.mean(nov)), 4)}}
        series_by_k[k] = (vals, nov)

    A = {"sessions": len(base_vals), **stat(base_vals), "capital_session": round(a_cap, 4),
         "mdd_pct": mdd(base_vals),
         "nonoverlap": {"n_cohorts": len(base_nov), "mdd_pct": a_nov,
                        "mean_pct": round(float(np.mean(base_nov)), 4)}}
    a_nov = A["nonoverlap"]["mdd_pct"]

    cand = ({k: v for k, v in variants.items() if k != "k5"} if a.regime == "all"
            else dict(variants))
    for v in cand.values():
        v["mdd_ratio"] = round(abs(v["nonoverlap"]["mdd_pct"]) / abs(a_nov), 2) if a_nov else None
    sel = max(cand, key=lambda k: cand[k]["capital_session"])
    B = cand[sel]
    # 짝 Δ 검정(같은 세션 k vs k5) — 자본세션 점추정만으로 '개선'이라 쓰면 노이즈를 승격한다
    base = base_vals
    for kk in list(cand) if a.regime == "all" else []:
        k = int(kk[1:])
        d = np.array(series_by_k[k][0]) - np.array(base[:len(series_by_k[k][0])])
        n = len(d)
        sd = float(np.std(d, ddof=1)) if n > 1 else 0.0
        cand[kk]["paired"] = {"n": n, "mean_delta_pct": round(float(np.mean(d)), 4),
                             "sd": round(sd, 4),
                             "t": round(float(np.mean(d)) / (sd / np.sqrt(n)), 2) if sd else 0.0,
                             "pos_pct": round(100 * float(np.mean(d > 0)), 1),
                             "fold_pos": "n/a"}
    if a.regime != "all":
        for kk, v in cand.items():
            v["paired"] = {"n": v["sessions"], "mean_delta_pct": v["mean"], "t": v["t"],
                           "pos_pct": v["pos_pct"], "note": "부분표본 1표본 t(짝 Δ 아님)"}
    passes = [k for k, v in cand.items()
              if (v["paired"]["t"] or 0) >= 2.0
              and v["capital_session"] >= A["capital_session"] + 0.05
              and v["mdd_ratio"] and v["mdd_ratio"] <= 1.5]
    noise = [k for k, v in cand.items()
             if v["capital_session"] >= A["capital_session"] + 0.05
             and (v["paired"]["t"] or 0) < 2.0]

    out = {"metric_name": "capital_path_compare", "tag": a.tag,
           "k": sel.lstrip("k"), "h5": 1, "fee_roundtrip_pct": round(RT, 4),
           "note": "현행 A 경로(익일 시가 청산) 고정 · 진입 폭 k 만 변경 · 낙폭은 5일 묶음 코호트(공통 크기)",
           "path_a_h1": {"sessions": A["sessions"], "mean": A["mean"], "corr_mean": A["mean"],
                         "sd": A["sd"], "t": A["t"], "pos_pct": A["pos_pct"],
                         "capital_session": A["capital_session"], "mdd_pct": a_nov},
           "path_b_h5": {"sessions": B["sessions"], "mean": B["mean"], "corr_mean": B["mean"],
                         "sd": B["sd"], "t": B["t"], "pos_pct": B["pos_pct"],
                         "capital_session": B["capital_session"],
                         "mdd_pct": B["nonoverlap"]["mdd_pct"]},
           "nonoverlap_compare": {"n_cohorts": B["nonoverlap"]["n_cohorts"],
                                  "a_mdd_pct": a_nov, "b_mdd_pct": B["nonoverlap"]["mdd_pct"],
                                  "mdd_ratio": B["mdd_ratio"],
                                  "a_mean_pct": A["nonoverlap"]["mean_pct"],
                                  "b_mean_pct": B["nonoverlap"]["mean_pct"]},
           "variants": variants, "pass": [k.lstrip("k") for k in passes],
           "noise_arms": [k.lstrip("k") for k in noise],
           "regression": {"k5_matches_cg224": reg_ok,
                          "measured_capital_session": A["capital_session"],
                          "measured_mdd_pct": A["mdd_pct"]},
           "window": {"first": dates[0], "last": dates[-1]}, "rows_bad": bad}
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)

    print(f"A(k5) {A['sessions']}세션 자본세션 {A['capital_session']:+.4f} 코호트MDD {a_nov} "
          f"| 회귀 {'OK' if reg_ok else 'MISMATCH'}")
    for k, v in variants.items():
        pz = v.get("paired") or {}
        print(f"  {k:3s} {v['sessions']}세션 mean {v['mean']:+.4f}%p t {v['t']} 승률 {v['pos_pct']}% "
              f"코호트MDD {v['nonoverlap']['mdd_pct']} 배수 {v.get('mdd_ratio', 1.0):.2f}x"
              + (f" | 짝Δ {pz.get('mean_delta_pct'):+.4f}%p t {pz.get('t')} 승률 {pz.get('pos_pct')}%"
                 if pz else ""))
    print(f"선택 {sel} · 통과 {passes or '없음'} · 노이즈(문턱은 넘고 짝Δ t<2) {noise or '없음'}")
    print(f"[ok] {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
