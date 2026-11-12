#!/usr/bin/env python3
"""CG228 — h5 낙폭이 '시장 레짐' 때문인가: 시장 하락 시 조기청산으로 낙폭 배수를 1.5x 이내로 낮출 수 있는가.

왜 (2026-11-13 03:1x 엔지니어 자율 틱, CG226/CG227 후속)
  CG224 실측: h5 자본-세션 +0.3000%p(A의 5.8배)지만 비중첩 MDD −15.313% = A(−4.013%)의 3.82배 → 경로 불가.
  CG226(손절 −5/−8%): 최선 3.39x (문턱 미달). CG227(폭 k=10/20/50): 6.01/6.98/12.97x (더 악화).
  남은 설명: 손실이 '개별 종목'이 아니라 '시장 하락 구간'에 몰려 있을 수 있다 → 레짐 청산 규칙으로
  낙폭만 깎을 수 있는지 측정한다.

정의(재현 가능):
  · 진입·체결성 필터·비용 = CG224/CG226 과 동일 (종가 진입, 5세션 종가청산, top-k 동일비중).
  · 시장 레짐 = 그날 체결가능 유니버스의 **동일가중 평균 일간수익률**(20종목 미만 날짜 제외).
  · 규칙 변형: 보유 중 어느 날 종가에서 (진입일 이후) 누적 시장수익률 ≤ −M% 이면 **다음 거래일 시가** 청산.
    t+1 이 보유창을 넘으면 i+h 종가청산. M ∈ {1.5, 2.5, 4.0}.
  · A(h1) = CG224 회귀 기준(익일 시가 청산, 240세션 +0.0521%p/세션 · 비중첩 MDD −4.013).

사전 등록: 변형 중 자본-세션 ≥ A+0.05%p AND 비중첩 MDD 배수 ≤1.5x → '경로 변경 검토 근거'(트레이더 경로 ②승인).
  없으면 h5 축 최종 종결(손절·폭·레짐 3수단 모두 실패) 근거로 기록.
진단(판정 아님): 보유구간 시장수익률 <0 인 코호트 vs ≥0 코호트의 평균 코호트수익.

재현:
  docker exec stock_xgboost_ml sh -c 'cd /app && timeout 900 python -u scripts/_cg228_regime.py \
    --preds /app/reports/overnight/cg220_preds.jsonl --tag CO_smooth_d1_h5 --k 5 --h5 5 \
    --out /app/reports/overnight/cg228_regime.json'
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
import _cg226_stop_path as cg226  # noqa: E402  (f, hl_series, mdd, stat, RT 재사용)
from _cg226_stop_path import RT, hl_series, mdd, stat  # noqa: E402
from fillable_topk_expectancy import apply_filters, db_connect, enrich, load_rows  # noqa: E402

REGIMES = [("reg_1p5", 1.5), ("reg_2p5", 2.5), ("reg_4p0", 4.0)]


def f(x):
    return cg226.f(x)


def market_returns(series):
    """날짜별 동일가중 평균 일간수익률(체결가능 유니버스 근사). >=20종목 날짜만."""
    acc = {}
    for _code, s in series.items():
        d, c = s["d"], s["c"]
        for t in range(1, len(d)):
            p0, p1 = f(c[t - 1]), f(c[t])
            if p0 and p1 and p0 > 0:
                acc.setdefault(d[t], []).append(p1 / p0 - 1.0)
    return {dt: float(np.mean(v)) for dt, v in acc.items() if len(v) >= 20}


def hold_regime(s, i, h, mkt, cum_mkt, m_cut):
    """보유 h세션. 보유 중 누적 시장수익률 ≤ −m_cut% 이면 다음 시가 청산(불가하면 종가).

    반환 (gross_pct, days_held, regime_exit) or None.
    """
    entry = f(s["c"][i])
    if not entry or i + h >= len(s["d"]):
        return None
    if m_cut:
        for t in range(i + 1, i + h + 1):
            r = mkt.get(s["d"][t])
            if r is None:
                continue
            cum = 1.0
            for u in range(i + 1, t + 1):
                rr = mkt.get(s["d"][u])
                if rr is not None:
                    cum *= (1.0 + rr)
            if (cum - 1.0) * 100.0 <= -m_cut:
                t_exit = t + 1 if t + 1 <= i + h else i + h
                if t_exit > t:
                    op = f(s["o"][t_exit])
                    if op is not None:
                        return ((op / entry - 1.0) * 100.0, t_exit - i, True)
                px = f(s["c"][t])
                if px is not None:
                    return ((px / entry - 1.0) * 100.0, t - i, True)
                break
    exit_px = f(s["c"][i + h])
    if not exit_px:
        return None
    return ((exit_px / entry - 1.0) * 100.0, h, False)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preds", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--h5", type=int, default=5)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    rows, bad = load_rows(a.preds, a.tag)
    if not rows:
        print(f"[FAIL] 행 없음 tag={a.tag}", file=sys.stderr)
        return 2
    codes = sorted({r["code"] for r in rows})
    dmin, dmax = min(r["date"] for r in rows), max(r["date"] for r in rows)
    d0 = (pd.Timestamp(dmin) - pd.Timedelta(days=6)).date()
    d1 = (pd.Timestamp(dmax) + pd.Timedelta(days=6 * (a.h5 + 2))).date()
    conn = db_connect()
    series = hl_series(conn, codes, d0, d1)
    mkt = market_returns(series)

    ent, _ = enrich(rows, series, a.h5, "close_h")
    flt = SimpleNamespace(exclude_limit_up=True, max_day_chg=25.0, min_value=1e9,
                          min_price=None, max_price=None)
    ent, _ = apply_filters(ent, flt, on=True)
    keep = []
    for r in ent:
        s = series.get(r["code"])
        try:
            i = s["d"].index(pd.Timestamp(r["date"]).date())
        except (KeyError, ValueError, AttributeError):
            bad += 1
            continue
        if i + a.h5 >= len(s["d"]):
            bad += 1
            continue
        r = dict(r)
        r["i"] = i
        keep.append(r)
    if not keep:
        print("[FAIL] 필터 후 행 0", file=sys.stderr)
        return 2

    # A(h1) 회귀 기준
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

    def run_variant(m_cut):
        by = {}
        for r in keep:
            by.setdefault(r["date"], []).append(r)
        dts, vals, exit_frac, mkt_neg = [], [], [], []
        for dt in sorted(by):
            rs = by[dt]
            if len(rs) < 20:
                continue
            top = sorted(rs, key=lambda x: -x["score"])[:a.k]
            v, nex, mm = [], 0, []
            for r in top:
                s = series.get(r["code"])
                got = hold_regime(s, r["i"], a.h5, mkt, None, m_cut)
                if got is None:
                    continue
                g, _d, ex = got
                v.append(g)
                nex += 1 if ex else 0
                cum = 1.0
                for u in range(r["i"] + 1, r["i"] + a.h5 + 1):
                    rr = mkt.get(s["d"][u])
                    if rr is not None:
                        cum *= (1.0 + rr)
                mm.append(cum - 1.0)
            if not v:
                continue
            dts.append(dt)
            vals.append(float(np.mean(v)) - RT)
            exit_frac.append(nex / len(v))
            mkt_neg.append(float(np.mean(mm)) < 0)
        return dts, vals, exit_frac, mkt_neg

    variants = {}
    for name, m_cut in [("none", None)] + REGIMES:
        dts, vals, exits, neg = run_variant(m_cut)
        if len(vals) < 30:
            print(f"[FAIL] {name} 세션 부족 {len(vals)}", file=sys.stderr)
            return 2
        nov_n = len(vals) - (len(vals) % a.h5)
        r5_nov = [float(np.mean(vals[i:i + a.h5])) for i in range(0, nov_n, a.h5)]
        variants[name] = {"sessions": len(vals), **stat(vals),
                          "capital_session": round(float(np.mean(vals)) / a.h5, 4),
                          "mdd_pct": mdd(vals),
                          "regime_exit_pct": round(100 * float(np.mean(exits)), 1),
                          "nonoverlap": {"n_cohorts": len(r5_nov),
                                         "mean_pct": round(float(np.mean(r5_nov)), 4),
                                         "mdd_pct": mdd(r5_nov)},
                          "dates": dts}

    n_nov = variants["none"]["nonoverlap"]["n_cohorts"]
    step = max(1, len(r1) // max(1, n_nov))
    r1_nov = [float(np.mean(r1[i * step:(i + 1) * step])) for i in range(n_nov)]
    a_block = {"sessions": len(r1), **stat(r1),
               "capital_session": round(float(np.mean(r1)), 4),
               "mdd_pct": mdd(r1), "nonoverlap_mdd_pct": mdd(r1_nov)}
    a_nov = mdd(r1_nov)

    # 진단: 코호트의 보유구간 시장수익률 부호별 코호트 수익(판정 아님)
    _, vals0, _, neg = run_variant(None)
    nov0 = [float(np.mean(vals0[i:i + a.h5])) for i in range(0, len(vals0) - len(vals0) % a.h5, a.h5)]
    neg_m = [x for x, n in zip(nov0, neg[:len(nov0)]) if n]
    pos_m = [x for x, n in zip(nov0, neg[:len(nov0)]) if not n]
    diag = {"cohorts_market_down": len(neg_m), "mean_pct_market_down": round(float(np.mean(neg_m)), 4) if neg_m else None,
            "cohorts_market_up": len(pos_m), "mean_pct_market_up": round(float(np.mean(pos_m)), 4) if pos_m else None,
            "share_of_cohorts_market_down": round(100 * len(neg_m) / max(1, len(neg_m) + len(pos_m)), 1)}

    reg_only = {k: v for k, v in variants.items() if k != "none"}
    sel = max(reg_only, key=lambda k: reg_only[k]["capital_session"])
    B = reg_only[sel]
    ratio = (abs(B["nonoverlap"]["mdd_pct"]) / abs(a_nov)) if a_nov else None

    base = variants["none"]
    reg_ok = (abs(base["capital_session"] - 0.3000) <= 0.01
              and abs(base["nonoverlap"]["mdd_pct"] - (-15.313)) <= 0.10)

    passes = [k for k, v in reg_only.items()
              if v["capital_session"] >= a_block["capital_session"] + 0.05
              and a_nov and abs(v["nonoverlap"]["mdd_pct"]) <= 1.5 * abs(a_nov)]

    out = {"metric_name": "h5_regime_exit", "tag": a.tag, "k": a.k, "h5": a.h5,
           "fee_roundtrip_pct": round(RT, 4),
           "note": "시장 레짐 = 체결가능 유니버스 동일가중 일간수익률. 규칙: 누적 시장 ≤ −M% → 다음 시가 청산.",
           "selected_variant": sel, "path_a_h1": a_block,
           "path_b_h5": {kk: vv for kk, vv in B.items() if kk != "dates"},
           "nonoverlap_compare": {"n_cohorts": n_nov, "a_mdd_pct": a_nov,
                                  "b_mdd_pct": B["nonoverlap"]["mdd_pct"],
                                  "mdd_ratio": round(ratio, 2) if ratio else None,
                                  "a_mean_pct": round(float(np.mean(r1_nov)), 4),
                                  "b_mean_pct": B["nonoverlap"]["mean_pct"]},
           "variants": {kk: {k2: v2 for k2, v2 in vv.items() if k2 != "dates"} for kk, vv in variants.items()},
           "diagnostics": diag,
           "regression": {"none_matches_cg224": reg_ok,
                          "measured_capital_session": base["capital_session"],
                          "measured_nonoverlap_mdd_pct": base["nonoverlap"]["mdd_pct"]},
           "pass": passes, "window": {"first": dates_a[0], "last": dates_a[-1]}, "rows_bad": bad}
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)

    print(f"A(h1) {len(r1)}세션 cap/sess {a_block['capital_session']:+.4f} MDD {a_block['mdd_pct']} "
          f"| 비중첩 MDD {a_nov} (n={n_nov})")
    for name, v in variants.items():
        print(f"  h5[{name:8s}] {v['sessions']}세션 mean {v['mean']:+.4f}%p cap/sess "
              f"{v['capital_session']:+.4f} MDD {v['mdd_pct']} | 비중첩 MDD {v['nonoverlap']['mdd_pct']} "
              f"배수 {(abs(v['nonoverlap']['mdd_pct'])/abs(a_nov) if a_nov else float('nan')):.2f}x "
              f"레짐청산 {v['regime_exit_pct']}%")
    print(f"진단: 시장하락 코호트 {diag['cohorts_market_down']}개 평균 {diag['mean_pct_market_down']}%p vs "
          f"상승 {diag['cohorts_market_up']}개 {diag['mean_pct_market_up']}%p "
          f"(하락 코호트 비중 {diag['share_of_cohorts_market_down']}%)")
    print(f"선택 {sel} · 회귀(CG224) {'OK' if reg_ok else 'MISMATCH'} · 통과 {passes or '없음'}")
    print(f"[ok] {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
