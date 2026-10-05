#!/usr/bin/env python3
"""regime_ic_screen.py — 모델 점수의 세션별 횡단면 랭크 IC 를 **시장 국면별로 분해**한다.

WHY (2026-10-06, 엔지니어 · CG126)
  모델측 AUC 축(변환·HP·앙상블 가중 포함·선별규칙·표본가중·목적함수·라벨·유니버스·창·정규화)과
  돈 축(top-k vs 세션 풀평균, 팩터 랭킹, 하위꼬리)이 실측으로 닫혔다. 남아 있던 유일한 '재개
  조건'은 XR11 노트의 **국면 조건화**(거시·시장레벨은 그대로 투입하면 정보 0 이지만 '국면 조건화로
  재설계해 사전등록할 때' 재개)다.

  다만 종목 피처 × 날짜상수 국면의 **곱**은 트리(per-day 단조변환 불변)와 IC(순위 불변) 모두에서
  수학적 no-op 이다 → 검정할 것은 '곱 피처'가 아니라 **국면별로 랭킹의 효력이 다른가**다.
  이것이 참이면 소비 측 레버(국면 게이트: 하위 국면엔 진입 금지)가 생기고, 거짓이면 축을 닫는다.

정의 (전부 as-of — t 시점 종가까지의 정보만)
  풀 = 덤프의 종목집합. 일별 동일가중 지수수익률 r_t ← market_data 종가(종목별 전일 종가 대비).
  국면A(추세): trail5_t = prod(1+r_{t-4..t}) − 1  → 부호로 up/down
  국면B(변동성): vol20_t = pstdev(r_{t-19..t}) → 전 날짜 3분위(low/mid/high)
  IC_t = Spearman(y_pred, fwd_ret) 그 날짜 (n>=min_n)
  국면별 mean IC · sd · t · 세션수 · 앞/뒤 절반 · **풀평균 fwd_ret**(베타 확인).
  ΔIC = up − down · t 는 이분산 Welch.

사전등록 (이 스크립트 기본)
  신호있음 ⟺ |ΔIC| >= --min-delta-ic(0.03) · |t| >= --min-t(2.0) · 두 국면 세션수 >= --min-sessions(15)
  그 밖은 '국면 조건화도 정보 없음' → 축 종결.

사용(컨테이너 — psycopg2 필요)
  docker exec -e PYTHONPATH=/app stock_xgboost_ml python /app/scripts/regime_ic_screen.py \
      --arm-jsonl /app/reports/overnight/cg108_at_preds.jsonl --fillable \
      --json-out /app/reports/overnight/cg126_regime.json
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rank_ic_money as rim  # noqa: E402


def build_index_returns(series):
    """{code: [(date, close, tv)]} → [(date, mean_ret)] (전일 종가 대비, 동일가중)."""
    acc = defaultdict(list)
    for _code, s in series.items():
        prev = None
        for d, c, _tv in s:
            if c and prev:
                acc[d].append(c / prev - 1.0)
            if c:
                prev = c
    return [(d, statistics.mean(v)) for d, v in sorted(acc.items()) if v]


def regime_series(idx, trend_win=5, vol_win=20):
    """[(date, ret)] → {date: {'trend': float, 'vol': float}} (as-of t 포함 trailing)."""
    out = {}
    rets = [r for _d, r in idx]
    dates = [d for d, _r in idx]
    for i, d in enumerate(dates):
        if i + 1 < trend_win or i + 1 < vol_win:
            continue
        tw = rets[i - trend_win + 1: i + 1]
        trend = 1.0
        for r in tw:
            trend *= (1.0 + r)
        trend -= 1.0
        vw = rets[i - vol_win + 1: i + 1]
        vol = statistics.pstdev(vw) if len(vw) > 1 else None
        out[d] = {"trend": trend, "vol": vol}
    return out


def _bucket_agg(ics):
    return rim._agg(ics)


def _mean_pool(by_date, dates):
    vals = [statistics.mean(by_date[d][1]) * 100.0 for d in dates if d in by_date and by_date[d][1]]
    if not vals:
        return None
    return statistics.mean(vals)


def _welch_t(a, b):
    """두 IC 리스트의 이분산 t(평균차)."""
    if len(a) < 2 or len(b) < 2:
        return None, None
    ma, mb = statistics.mean(a), statistics.mean(b)
    sa = statistics.pstdev(a) / math.sqrt(len(a))
    sb = statistics.pstdev(b) / math.sqrt(len(b))
    delta = ma - mb
    se = math.sqrt(sa * sa + sb * sb)
    return delta, (delta / se if se > 0 else None)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm-jsonl", required=True)
    ap.add_argument("--arm-tag")
    ap.add_argument("--min-n", type=int, default=10)
    ap.add_argument("--fwd-cap", type=float, default=None)
    ap.add_argument("--fillable", action="store_true",
                    help="체결성 필터 적용(fillable_topk_expectancy 와 같은 규칙, DB 필요)")
    ap.add_argument("--max-day-chg", type=float, default=25.0)
    ap.add_argument("--min-value", type=float, default=1e9)
    ap.add_argument("--trend-win", type=int, default=5)
    ap.add_argument("--vol-win", type=int, default=20)
    ap.add_argument("--min-delta-ic", type=float, default=0.03)
    ap.add_argument("--min-t", type=float, default=2.0)
    ap.add_argument("--min-sessions", type=int, default=15)
    ap.add_argument("--json-out")
    a = ap.parse_args(argv)

    rows, n_skip = rim.load_rows(a.arm_jsonl, a.arm_tag, a.fwd_cap)
    if not rows:
        print(json.dumps({"error": "행 없음"}, ensure_ascii=False))
        return 2
    dates_all = sorted({r["date"] for r in rows})
    codes = sorted({r["code"] for r in rows})
    dmin = (datetime.date.fromisoformat(dates_all[0]) - datetime.timedelta(days=45)).isoformat()
    dmax = dates_all[-1]
    series = rim.load_market_series(codes, dmin, dmax)

    fs = None
    if a.fillable:
        keep, dropped, st = rim.fillable_filter(rows, series, a.max_day_chg, a.min_value, None)
        fs = {"dropped": dropped, "kept": len(keep), "reasons": st}
        rows = keep
    by_date = rim.group_rows(rows)
    ics = rim.ic_series(by_date, a.min_n)

    idx = build_index_returns(series)
    reg = regime_series(idx, a.trend_win, a.vol_win)

    # 국면 분류
    up, down, unknown = [], [], []
    for d in sorted(ics):
        if ics[d] is None:
            continue
        r = reg.get(d)
        if not r:
            unknown.append(d)
            continue
        (up if r["trend"] > 0 else down).append(d)
    # 변동성 3분위
    vols = sorted((reg[d]["vol"], d) for d in ics
                  if ics.get(d) is not None and reg.get(d, {}).get("vol") is not None)
    terc = {"low": [], "mid": [], "high": []}
    if len(vols) >= 6:
        n3 = len(vols) // 3
        for j, (_v, d) in enumerate(vols):
            terc["low" if j < n3 else ("high" if j >= 2 * n3 else "mid")].append(d)

    def pack(dts):
        sub = {d: ics[d] for d in dts if ics.get(d) is not None}
        agg = _bucket_agg(sub)
        agg["pool_mean_fwd_pct"] = _mean_pool(by_date, dts)
        return agg

    trend_b = {"up": pack(up), "down": pack(down)}
    vol_b = {k: pack(v) for k, v in terc.items()}
    # up/down 짝 t
    up_v = [ics[d] for d in up if ics[d] is not None]
    dn_v = [ics[d] for d in down if ics[d] is not None]
    d_ic, d_t = _welch_t(up_v, dn_v)
    # vol low vs rest 짝 t (사전등록 대상 2 = 저변동 국면 우위, CG127)
    low_v = [ics[d] for d in terc["low"] if ics[d] is not None]
    rest_dates = terc["mid"] + terc["high"]
    rest_v = [ics[d] for d in rest_dates if ics[d] is not None]
    dv_ic, dv_t = _welch_t(low_v, rest_v)

    out = {
        "metric_name": "regime_ic_screen",
        "arm": a.arm_tag or os.path.basename(a.arm_jsonl),
        "arm_jsonl": a.arm_jsonl,
        "fillable": bool(a.fillable),
        "fillable_filter": fs,
        "n_rows": len(rows), "n_rows_skipped": n_skip,
        "n_sessions_total": sum(1 for v in ics.values() if v is not None),
        "n_sessions_no_regime": len(unknown),
        "trend_win": a.trend_win, "vol_win": a.vol_win,
        "all": _bucket_agg({d: ics[d] for d in ics if ics[d] is not None}),
        "trend": trend_b,
        "volatility": vol_b,
        "delta_ic_up_minus_down": d_ic,
        "delta_t": d_t,
        "delta_ic_vol_low_minus_rest": dv_ic,
        "delta_t_vol": dv_t,
        "thresholds": {"min_delta_ic": a.min_delta_ic, "min_t": a.min_t,
                       "min_sessions": a.min_sessions},
    }

    # 사전등록 판정
    ok = False
    why = ""
    if d_ic is not None and d_t is not None:
        enough = (trend_b["up"]["n_sessions"] >= a.min_sessions
                  and trend_b["down"]["n_sessions"] >= a.min_sessions)
        ok = (abs(d_ic) >= a.min_delta_ic and abs(d_t) >= a.min_t and enough)
        why = ("ΔIC %+.4f · t %+.2f · 세션 up/down %d/%d · 충분표본 %s"
               % (d_ic, d_t, trend_b["up"]["n_sessions"], trend_b["down"]["n_sessions"], enough))
    else:
        why = "국면 표본 부족(ΔIC 계산 불가)"
    out["verdict"] = "신호있음" if ok else "노이즈"
    out["detail"] = why + (" — 국면 의존 IC(소비측 국면 게이트 후보)" if ok
                           else " — 국면 조건화도 정보 없음(축 종결)")

    def _fmt(b):
        return "IC %s(t %s, n %s, pool %s%%)" % (
            None if b.get("mean_ic") is None else round(b["mean_ic"], 4),
            None if b.get("t") is None else round(b["t"], 2),
            b.get("n_sessions"),
            None if b.get("pool_mean_fwd_pct") is None else round(b["pool_mean_fwd_pct"], 3))

    print("arm=%s fillable=%s rows=%d sessions=%d" % (out["arm"], out["fillable"], out["n_rows"],
                                                      out["n_sessions_total"]))
    print("  ALL      ", _fmt(out["all"]))
    print("  trend up ", _fmt(trend_b["up"]))
    print("  trend dn ", _fmt(trend_b["down"]))
    for k in ("low", "mid", "high"):
        print("  vol %-4s " % k, _fmt(vol_b[k]))
    print("  ΔIC(up−down) = %s · t = %s" % (
        None if d_ic is None else round(d_ic, 4), None if d_t is None else round(d_t, 2)))
    print("  ΔIC(vol low−rest) = %s · t = %s · n low/rest %d/%d" % (
        None if dv_ic is None else round(dv_ic, 4), None if dv_t is None else round(dv_t, 2),
        len(low_v), len(rest_v)))
    print("  판정: %s — %s" % (out["verdict"], out["detail"]))

    if a.json_out:
        os.makedirs(os.path.dirname(a.json_out) or ".", exist_ok=True)
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print("  wrote %s" % a.json_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
