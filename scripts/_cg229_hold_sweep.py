#!/usr/bin/env python3
"""CG229 — 보유 지평 스윕(1~5세션): h5 낙폭은 '지평' 탓인가, 아니면 신호 자체의 변동성인가.

왜 (2026-11-13 04:1x 엔지니어 자율 틱, CG228 후속·h5 축 종결 직후)
  실측 사슬: A(h1) 자본세션 +0.0521%p·비중첩 MDD −4.013 / h5 자본세션 +0.3000(5.8배)·MDD −15.313(3.82배).
  낙폭 억제 3수단 전패 — 손절 −5/−8%: 3.08/3.39x(CG226) · 폭 k=10/20/50: 6.0/7.0/13.0x(CG227) ·
  시장레짐 조기청산 −1.5%: 2.91x(CG228). 즉 'h5'라는 특정 지평에서만 재본 것이 원인일 수 있다 →
  h=1..5 를 같은 예측·같은 필터·같은 비용으로 전수 측정해 위험/수익 경계를 그린다.

정의(재현 가능):
  · 진입: 세션 종가(그날 close) · 후보 <20 이면 세션 제외 · top-k 동일비중 · 체결성 필터 CG224 동일.
  · 청산: h세션 뒤 종가. 손절 없음(단순 지평 비교).
  · 자본-세션 = mean(r_h)/h (회전 정규화) · 비중첩 MDD = h세션 간격 코호트 자본곡선(단리합).
  · A(h1) = h=1 의 자본-세션·비중첩 MDD (CG224 회귀 기준 +0.0521 / −4.013).

사전 등록: h∈{2,3,4,5} 중 자본-세션 ≥ A+0.05%p AND 비중첩 MDD 배수 ≤1.5x → '경로 변경 검토 근거'(②승인).
  없으면 청산지평 축 전체 종결(손절·폭·레짐·지평 4수단) 근거로 기록.

재현:
  docker exec stock_xgboost_ml sh -c 'cd /app && timeout 900 python -u scripts/_cg229_hold_sweep.py \
    --preds /app/reports/overnight/cg220_preds.jsonl --tag CO_smooth_d1_h5 --k 5 --out /app/reports/overnight/cg229_hold.json'
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _cg226_stop_path as cg226  # noqa: E402
from _cg226_stop_path import RT, hl_series, mdd, stat  # noqa: E402
from fillable_topk_expectancy import apply_filters, db_connect, enrich, load_rows  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preds", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--out", required=True)
    ap.add_argument("--bootstrap", type=int, default=0,
                    help="h2 코호트 쌍 블록 부트스트랩 draws 수(0=끔). 낙폭 배수의 취약성 측정")
    a = ap.parse_args(argv)
    cg226.K = a.k

    rows, bad = load_rows(a.preds, a.tag)
    if not rows:
        print(f"[FAIL] 행 없음 tag={a.tag}", file=sys.stderr)
        return 2
    codes = sorted({r["code"] for r in rows})
    dmin, dmax = min(r["date"] for r in rows), max(r["date"] for r in rows)
    d0 = (pd.Timestamp(dmin) - pd.Timedelta(days=6)).date()
    d1 = (pd.Timestamp(dmax) + pd.Timedelta(days=6 * 8)).date()
    conn = db_connect()
    series = hl_series(conn, codes, d0, d1)
    flt = SimpleNamespace(exclude_limit_up=True, max_day_chg=25.0, min_value=1e9,
                          min_price=None, max_price=None)

    variants, dates_by_h = {}, {}
    # A(h1) = CG224 경로(익일 **시가** 청산) — 회귀 기준
    ent_no, _ = enrich(rows, series, 1, "next_open")
    ent_no, _ = apply_filters(ent_no, flt, on=True)
    by_o = {}
    for r in ent_no:
        by_o.setdefault(r["date"], []).append(r)
    dates_a, r1 = [], []
    for dt in sorted(by_o):
        if len(by_o[dt]) < 20:
            continue
        top = sorted(by_o[dt], key=lambda x: -x["score"])[:a.k]
        dates_a.append(dt)
        r1.append(float(np.mean([r["gross_pct"] for r in top])) - RT)
    a_mean = float(np.mean(r1))
    reg_ok = (abs(a_mean - 0.0521) <= 0.01 and abs(mdd(r1) - (-20.176)) <= 0.10)
    if not reg_ok:
        print(f"[FAIL] 회귀 불일치 — A(h1) {a_mean:+.4f}%p/세션 · MDD {mdd(r1)} "
              f"(기준 +0.0521 / -20.176). 경로 정의가 CG224 와 다르면 비율 비교가 무효다.",
              file=sys.stderr)
        return 2

    for h in (2, 3, 4, 5):
        ent, _ = enrich(rows, series, h, "close_h")
        ent, _ = apply_filters(ent, flt, on=True)
        keep = []
        for r in ent:
            s = series.get(r["code"])
            try:
                i = s["d"].index(pd.Timestamp(r["date"]).date())
            except (KeyError, ValueError, AttributeError):
                bad += 1
                continue
            if i + h >= len(s["d"]):
                bad += 1
                continue
            r = dict(r)
            r["i"] = i
            keep.append(r)
        if not keep:
            print(f"[FAIL] h={h} 필터 후 행 0", file=sys.stderr)
            return 2
        dts, res = cg226.sessions(keep, series, h, None)
        vals = [x[0] for x in res]
        if len(vals) < 30:
            print(f"[FAIL] h={h} 세션 부족 {len(vals)}", file=sys.stderr)
            return 2
        nov_n = len(vals) - (len(vals) % h)
        r_nov = [float(np.mean(vals[i:i + h])) for i in range(0, nov_n, h)]
        # A 의 같은 크기 코호트(=h일 묶음 평균) — 같은 코호트 수로 낙폭을 비교해야 공정하다
        a_nov_h = [float(np.mean(r1[i:i + h])) for i in range(0, len(r1) - len(r1) % h, h)]
        variants[f"h{h}"] = {"sessions": len(vals), **stat(vals),
                             "capital_session": round(float(np.mean(vals)) / h, 4),
                             "mdd_pct": mdd(vals),
                             "nonoverlap": {"n_cohorts": len(r_nov),
                                            "mean_pct": round(float(np.mean(r_nov)), 4),
                                            "mdd_pct": mdd(r_nov)},
                             "_b_coh": r_nov, "_a_coh": a_nov_h,
                             "a_overlap": {"n_cohorts": len(a_nov_h),
                                           "mean_pct": round(float(np.mean(a_nov_h)), 4),
                                           "mdd_pct": mdd(a_nov_h)}}
        dates_by_h[h] = dts

    A = {"sessions": len(r1), **stat(r1), "capital_session": round(a_mean, 4),
         "mdd_pct": mdd(r1), "overlap": True}
    passes = []
    for k, v in variants.items():
        ao = v["a_overlap"]["mdd_pct"]
        r = abs(v["nonoverlap"]["mdd_pct"]) / abs(ao) if ao else None
        v["mdd_ratio"] = round(r, 2) if r else None
        if v["capital_session"] >= A["capital_session"] + 0.05 and r and r <= 1.5:
            passes.append(k)
    cand = variants
    sel = max(cand, key=lambda k: cand[k]["capital_session"])
    B = cand[sel]
    a_nov = B["a_overlap"]["mdd_pct"]  # 같은 코호트 크기로 만든 A 낙폭(비교 기준)

    boot = None
    if a.bootstrap and "h2" in variants:
        rng = np.random.default_rng(20261113)
        b2, a2 = variants["h2"]["_b_coh"], variants["h2"]["_a_coh"]
        n = min(len(b2), len(a2))
        b2, a2 = b2[:n], a2[:n]
        ratios = []
        for _ in range(a.bootstrap):
            idx = rng.integers(0, n, n)
            bm = mdd([b2[i] for i in idx])
            am = mdd([a2[i] for i in idx])
            if am:
                ratios.append(abs(bm) / abs(am))
        ratios_np = np.array(ratios)
        boot = {"draws": int(a.bootstrap), "seed": 20261113, "n_cohorts": n,
                "median_ratio": round(float(np.median(ratios_np)), 2),
                "p05": round(float(np.percentile(ratios_np, 5)), 2),
                "p95": round(float(np.percentile(ratios_np, 95)), 2),
                "share_le_1p5": round(float(np.mean(ratios_np <= 1.5)), 3),
                "note": "h2 코호트(A·B 쌍) 블록 부트스트랩 — 1.49x 가 문턱에 붙은 값인지 확인용"}

    pass_raw = list(passes)
    if boot:
        gate_ok = (boot["median_ratio"] <= 1.5 and boot["share_le_1p5"] >= 0.5)
        if not gate_ok:
            passes = [k for k in passes if k != "h2"]
    else:
        gate_ok = None

    out = {"metric_name": "hold_horizon_capital", "tag": a.tag, "k": a.k, "h5": sel.lstrip("h"),
           "fee_roundtrip_pct": round(RT, 4),
           "note": ("자본-세션 = mean(r_h)/h. 비중첩 MDD = h세션 간격 코호트. "
                    "비교 기준 A 낙폭 = A 일수익을 h일 묶음으로 평균한 같은 크기 코호트의 MDD "
                    "(일별 MDD 와 h 코호트 MDD 를 섞으면 비율이 무효 — 1차 실행 실패 원인)."),
           "selected_variant": sel,
           "path_a_h1": A,
           "path_b_h5": B,
           "nonoverlap_compare": {"n_cohorts": B["nonoverlap"]["n_cohorts"],
                                  "a_mdd_pct": a_nov,
                                  "b_mdd_pct": B["nonoverlap"]["mdd_pct"],
                                  "mdd_ratio": B.get("mdd_ratio"),
                                  "a_mean_pct": B["a_overlap"]["mean_pct"],
                                  "b_mean_pct": B["nonoverlap"]["mean_pct"]},
           "variants": {k: {k2: v2 for k2, v2 in v.items() if not k2.startswith("_")}
                        for k, v in variants.items()},
           "bootstrap_h2": boot,
           "diagnostics": {"grid": {k: {"capital_session": v["capital_session"],
                                        "b_mdd_pct": v["nonoverlap"]["mdd_pct"],
                                        "a_mdd_pct": v["a_overlap"]["mdd_pct"],
                                        "ratio": v.get("mdd_ratio")} for k, v in variants.items()}},
           "regression": {"a_matches_cg224": reg_ok,
                          "measured_capital_session": A["capital_session"],
                          "measured_daily_mdd_pct": A["mdd_pct"]},
           "pass": passes, "pass_raw": pass_raw,
           "bootstrap_gate_ok": gate_ok, "window": {"first": dates_a[0], "last": dates_a[-1]},
           "rows_bad": bad}
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)

    print(f"A(h1) {A['sessions']}세션 cap/sess {A['capital_session']:+.4f} 일별MDD {A['mdd_pct']} "
          f"| 회귀(CG224 +0.0521/-20.176) {'OK' if reg_ok else 'MISMATCH'}")
    for k, v in variants.items():
        print(f"  {k:3s} {v['sessions']}세션 mean {v['mean']:+.4f}%p(cap/sess {v['capital_session']:+.4f}) "
              f"MDD {v['mdd_pct']} | 코호트 MDD {v['nonoverlap']['mdd_pct']} vs A {v['a_overlap']['mdd_pct']} "
              f"배수 {v.get('mdd_ratio', 1.0):.2f}x 코호트 {v['nonoverlap']['n_cohorts']}")
    if boot:
        print(f"부트스트랩 h2 낙폭배수: 중앙 {boot['median_ratio']}x "
              f"[p05 {boot['p05']} / p95 {boot['p95']}] · {boot['share_le_1p5']*100:.0f}% draw 가 1.5x 이하")
    print(f"선택 {sel} · 통과 {passes or '없음'} (부트스트랩 게이트 {gate_ok})")
    print(f"[ok] {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
