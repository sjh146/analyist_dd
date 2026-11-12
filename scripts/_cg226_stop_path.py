#!/usr/bin/env python3
"""CG226 — h5 경로에 손절 규칙을 넣으면 낙폭이 1.5배 이내로 내려오는가.

왜 (2026-11-13 03:0x 엔지니어 자율 틱, CG224 후속)
  CG224 실측: h5 는 자본-세션 +0.3000%p(A의 5.8배)지만 비중첩 MDD −15.31% 로 A(−4.01%)의 3.82배
  → 사전문턱(1.5배) 초과로 경로 변경 불가. 낙폭의 원인이 '개별 종목 급락을 5세션 방치'라면
  −5%/−8% 손절로 MDD 를 낮추면서 자본효율 우위의 상당부를 보존할 수 있다.

정의(재현·검증 가능하게 못박음):
  · 진입: 세션 종가(그날 close) — CG224 와 동일. 후보 <20 이면 세션 제외, top-k 동일비중.
  · 체결성 필터: CG224 와 동일(상한가 제외 · 일변동 25% · 거래대금 10억).
  · A(h1): 익일 시가 청산.  B0(h5): 5세션 뒤 종가 청산(손절 없음 = CG224 회귀 기준).
  · 손절 변형: 보유일 t=i+1..i+5 의 **저가**가 진입가×(1−s) 이하이면 그날 청산.
    체결가는 min(시가_t, 손절가) — 갭하락이면 시가(낙관 편향 제거). 미터치면 i+5 종가.
  · 자본-세션 = A: mean(r1) · h5: mean(r5)/5 · MDD 는 비중첩 코호트(5세션 간격) 자본곡선.

사전 등록: 손절 변형 중 자본-세션 ≥ A+0.05%p AND 비중첩 MDD 배수 ≤1.5x 를 만족하는 조합이
  있으면 '경로 변경 검토 근거'(트레이더 경로 → ②승인), 없으면 h5 축 최종 종결.
회귀 기준: B0(손절 없음)가 CG224 와 같은 값(자본세션 +0.3000 · MDD −15.313)을 재현해야 한다.

재현:
  docker exec stock_xgboost_ml sh -c 'cd /app && timeout 900 python -u scripts/_cg226_stop_path.py \\
    --preds /app/reports/overnight/cg220_preds.jsonl --tag CO_smooth_d1_h5 --k 5 --h5 5 \\
    --out /app/reports/overnight/cg226_stop.json'
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fillable_expectancy import FEE_BUY, FEE_SELL, TAX_SELL  # noqa: E402
from fillable_topk_expectancy import (  # noqa: E402
    apply_filters, db_connect, enrich, load_rows, price_series,
)

RT = (FEE_BUY + FEE_SELL + TAX_SELL) * 100.0
STOPS = [("stop_5", 5.0), ("stop_8", 8.0)]


def stat(v):
    v = [x for x in v if x is not None]
    n = len(v)
    if n < 2:
        return {"n": n}
    m = float(np.mean(v))
    sd = float(np.std(v, ddof=1))
    return {"n": n, "mean": round(m, 4), "sd": round(sd, 4),
            "t": round(m / (sd / math.sqrt(n)), 2) if sd else 0.0,
            "pos_pct": round(100 * sum(1 for x in v if x > 0) / n, 1)}


def mdd(returns_pct):
    eq, peak, worst = 1.0, 1.0, 0.0
    for r in returns_pct:
        eq *= (1.0 + r / 100.0)
        peak = max(peak, eq)
        worst = min(worst, eq / peak - 1.0)
    return round(worst * 100.0, 3)


def hl_series(conn, codes, dmin, dmax):
    """open/high/low/close/trading_value 를 종목별 리스트로. price_series 는 high/low 가 없다."""
    q = ("SELECT stock_code, trade_date, open_price, high_price, low_price, close_price, "
         "trading_value FROM market_data WHERE stock_code = ANY(%s::text[]) "
         "AND trade_date BETWEEN %s AND %s")
    px = pd.read_sql(q, conn, params=[list(codes), dmin, dmax])
    px["stock_code"] = px["stock_code"].astype(str).str.zfill(6)
    px = px.sort_values(["stock_code", "trade_date"])
    out = {}
    for code, g in px.groupby("stock_code", sort=False):
        out[code] = {"d": [pd.Timestamp(x).date() for x in g["trade_date"]],
                     "o": list(g["open_price"]), "h": list(g["high_price"]),
                     "l": list(g["low_price"]), "c": list(g["close_price"]),
                     "tv": list(g["trading_value"])}
    return out


def f(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def hold_exit(s, i, h, stop_pct):
    """진입 종가 s['c'][i], 보유 h세션. stop_pct=None 이면 종가청산(=CG224).

    반환 (gross_pct, days_held, stopped) 또는 None(데이터 부족).
    """
    entry = f(s["c"][i])
    if not entry or i + h >= len(s["d"]):
        return None
    if stop_pct:
        stop_px = entry * (1.0 - stop_pct / 100.0)
        for t in range(i + 1, i + h + 1):
            lo = f(s["l"][t])
            if lo is not None and lo <= stop_px:
                op = f(s["o"][t])
                px = min(op, stop_px) if op is not None else stop_px
                return ((px / entry - 1.0) * 100.0, t - i, True)
    exit_px = f(s["c"][i + h])
    if not exit_px:
        return None
    return ((exit_px / entry - 1.0) * 100.0, h, False)


def sessions(rows, sers, h, stop_pct, min_rows=20):
    """세션별 top-k net(%p) 리스트. rows 는 이미 체결성 필터 통과한 dict 목록."""
    by = {}
    for r in rows:
        by.setdefault(r["date"], []).append(r)
    out_dates, out = [], []
    for dt in sorted(by):
        rs = by[dt]
        if len(rs) < min_rows:
            continue
        top = sorted(rs, key=lambda x: -x["score"])[:K]
        vals, days, nstop = [], [], 0
        for r in top:
            s = sers.get(r["code"])
            i = r["i"]
            got = hold_exit(s, i, h, stop_pct)
            if got is None:
                continue
            g, d, st = got
            vals.append(g)
            days.append(d)
            nstop += 1 if st else 0
        if not vals:
            continue
        out_dates.append(dt)
        out.append((float(np.mean(vals)) - RT, nstop / len(vals), float(np.mean(days))))
    return out_dates, out


def main(argv=None) -> int:
    global K
    ap = argparse.ArgumentParser()
    ap.add_argument("--preds", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--h5", type=int, default=5)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    K = a.k

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

    # 진입 행 준비: enrich 로 종가·전일종가·거래대금을 붙이고(청산 모드 무관) 필터를 건다
    ent, _ = enrich(rows, series, a.h5, "close_h")
    flt = SimpleNamespace(exclude_limit_up=True, max_day_chg=25.0, min_value=1e9,
                          min_price=None, max_price=None)
    ent, _ = apply_filters(ent, flt, on=True)
    keep = []
    for r in ent:
        s = series.get(r["code"])
        try:
            i = s["d"].index(pd.Timestamp(r["date"]).date())
        except (KeyError, ValueError):
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

    # A(h1) 경로: CG224 와 동일하게 '익일 시가' 청산 (회귀 기준)
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

    variants = {}
    for name, stop_pct in [("none", None)] + STOPS:
        dts, res = sessions(keep, series, a.h5, stop_pct)
        if len(res) < 30:
            print(f"[FAIL] {name} 세션 부족 {len(res)}", file=sys.stderr)
            return 2
        vals = [x[0] for x in res]
        nov_n = len(vals) - (len(vals) % a.h5)
        r5_nov = [float(np.mean(vals[i:i + a.h5])) for i in range(0, nov_n, a.h5)]
        variants[name] = {"sessions": len(vals), **stat(vals),
                          "capital_session": round(float(np.mean(vals)) / a.h5, 4),
                          "mdd_pct": mdd(vals),
                          "stopped_pct": round(100 * float(np.mean([x[1] for x in res])), 1),
                          "avg_hold_days": round(float(np.mean([x[2] for x in res])), 2),
                          "nonoverlap": {"n_cohorts": len(r5_nov), "mdd_pct": mdd(r5_nov),
                                         "mean_pct": round(float(np.mean(r5_nov)), 4)}}

    # A 경로 비중첩 코호트(선택 변형과 같은 코호트 수) — CG224 와 같은 방식
    n_nov = variants["none"]["nonoverlap"]["n_cohorts"]
    step = max(1, len(r1) // max(1, n_nov))
    r1_nov = [float(np.mean(r1[i * step:(i + 1) * step])) for i in range(n_nov)]

    a_block = {"sessions": len(r1), **stat(r1),
               "capital_session": round(float(np.mean(r1)), 4), "mdd_pct": mdd(r1)}

    # 선택 규칙(사전 등록): 손절 변형 중 자본-세션 최대 → 그 변형을 path_b 로 둔다
    stops_only = {k: v for k, v in variants.items() if k != "none"}
    sel = max(stops_only, key=lambda k: stops_only[k]["capital_session"])
    B = variants[sel]
    b_nov = B["nonoverlap"]["mdd_pct"]
    a_nov = mdd(r1_nov)
    ratio = abs(b_nov) / abs(a_nov) if a_nov else None

    base = variants["none"]
    # 회귀 기준은 CG224 의 실측 3값과 맞춘다(중첩 MDD -66.765 · 비중첩 MDD -15.313 · 자본세션 +0.3000)
    reg_ok = (abs(base["capital_session"] - 0.3000) <= 0.01
              and abs(base["mdd_pct"] - (-66.765)) <= 0.10
              and abs(base["nonoverlap"]["mdd_pct"] - (-15.313)) <= 0.10)

    out = {
        "metric_name": "capital_path_compare",
        "tag": a.tag, "k": a.k, "h5": a.h5, "fee_roundtrip_pct": round(RT, 4),
        "note": ("capital_session = A: mean(r1) · h5: mean(r5)/h5. 손절 체결가 = min(시가, 손절가). "
                 "path_b_h5 = 사전규칙(손절 변형 중 자본-세션 최대) 선택 변형"),
        "selected_variant": sel,
        "path_a_h1": a_block,
        "path_b_h5": {"sessions": B["sessions"], "mean": B["mean"], "sd": B["sd"], "t": B["t"],
                      "pos_pct": B["pos_pct"], "capital_session": B["capital_session"],
                      "mdd_pct": B["mdd_pct"], "stopped_pct": B["stopped_pct"],
                      "avg_hold_days": B["avg_hold_days"]},
        "nonoverlap_compare": {"n_cohorts": n_nov, "a_mdd_pct": a_nov, "b_mdd_pct": b_nov,
                               "a_mean_pct": round(float(np.mean(r1_nov)), 4),
                               "b_mean_pct": B["nonoverlap"]["mean_pct"],
                               "mdd_ratio": round(ratio, 2) if ratio else None},
        "variants": variants,
        "regression": {"none_matches_cg224": reg_ok,
                       "cg224_capital_session": 0.3000, "cg224_mdd_pct": -66.765,
                       "cg224_nonoverlap_mdd_pct": -15.313,
                       "measured_capital_session": base["capital_session"],
                       "measured_mdd_pct": base["mdd_pct"],
                       "measured_nonoverlap_mdd_pct": base["nonoverlap"]["mdd_pct"],
                       "tolerance": "cap 0.01 / mdd 0.10"},
        "pass": [k for k, v in stops_only.items()
                 if v["capital_session"] >= a_block["capital_session"] + 0.05
                 and a_nov and abs(v["nonoverlap"]["mdd_pct"]) <= 1.5 * abs(a_nov)],
        "window": {"first": dates_a[0], "last": dates_a[-1]},
        "rows_bad": bad,
    }
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)

    print(f"A(h1) {len(r1)}세션 cap/sess {a_block['capital_session']:+.4f} MDD {a_block['mdd_pct']} "
          f"| 비중첩 MDD {a_nov} (n={n_nov})")
    for name, v in variants.items():
        print(f"  h5[{name:6s}] {v['sessions']}세션 mean {v['mean']:+.4f}%p cap/sess "
              f"{v['capital_session']:+.4f} MDD {v['mdd_pct']} | 비중첩 MDD "
              f"{v['nonoverlap']['mdd_pct']} 배수 "
              f"{(abs(v['nonoverlap']['mdd_pct'])/abs(a_nov) if a_nov else float('nan')):.2f}x "
              f"손절비율 {v['stopped_pct']}% 보유 {v['avg_hold_days']}일")
    print(f"선택 {sel} · 회귀(CG224 재현) {'OK' if reg_ok else 'MISMATCH'} · 통과 변형 {out['pass'] or '없음'}")
    print(f"[ok] {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
