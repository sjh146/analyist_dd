#!/usr/bin/env python3
"""fillable_expectancy.py — 체결 가능성(fillability) 조건 하 순기대값 판정 도구 (읽기 전용).

WHAT
  종가스크리너 후보(또는 data/reports/close_gate_probe/trades.csv)를 입력받아
  "발굴일 종가 매수 → 청산 시가/종가 매도" 수익률을 수수료·거래세 반영(net)으로 계산하고,
  **세션 단위 통계**(세션수·평균·중앙·양(+)세션 비율·t통계·최악 세션)를 낸다.
  상한가 부근(+25% 이상)처럼 '종가에 실제로 살 수 없는' 후보를 제외하는
  체결 가능성 필터를 1급 옵션으로 제공한다.

체결 가능성 필터(1급 옵션)
  --max-day-chg FLOAT   당일등락 상한(%%): 이 이상 오른 후보 제외 (예: 25 → +25% 이상 제외)
  --exclude-limit-up    상한가 부근 제외 (당일등락 >= --limit-up-pct, 기본 29.9)
  --min-price FLOAT     종가 하한(원)
  --max-price FLOAT     종가 상한(원)
  --min-value FLOAT     거래대금 하한(원) — 예: 1e9 = 10억
  필터 항목(종가·거래대금·다음종가/T+2시가 수익률)은 trades.csv 에 없으면 market_data(일봉)로 조인해 채운다.

수수료 모델(pnl_backtest.py 와 동일, CLI 로 조정 가능)
  net(%) = ret(%) − (매수수수료 + 매도수수료 + 거래세)×100
  기본: 0.015% + 0.015% + 0.18% = 0.21%p

한계(보고에 함께 쓴다)
  · 진입가 근사: 실제 진입은 14:50~15:29 체결가인데 여기서는 발굴일 종가를 쓴다(변경 불가 가정).
  · 후보는 재생성물(당시 발행본 아님)이며, trades.csv 는 87세션·1,736후보 단일 구간(2026-05-26~09-30).
  · 슬리피지·호가 갭은 수수료 모델에 없다(순기대가 보수적으로 낮게 잡힌다).
  · 여러 조합을 훑어 최고를 고르는 것 자체가 과최적화 위험(다중비교)이다.

사용
  cd /home/jhshi/analyist_dd && set -a && . ./.env && set +a
  export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434
  python3 scripts/fillable_expectancy.py \
      --trades data/reports/close_gate_probe/trades.csv \
      --max-day-chg 25 --topk 3 --exit next_open
  python3 scripts/fillable_expectancy.py --sweep  # 전체 축 조합 스윕 + 분할 표본 안정성
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import defaultdict
from datetime import date, timedelta

import numpy as np
import pandas as pd

# 수수료 모델 — scripts/pnl_backtest.py 와 같은 값(한국 주식)
FEE_BUY = 0.00015
FEE_SELL = 0.00015
TAX_SELL = 0.0018
LIMIT_UP_PCT = 29.9          # 한국 상한가 +30%(반올림 → 관측 최대 ~29.99)

EXIT_COLS = {
    "next_open": "ret_next_open_pct",
    "next_close": "ret_next_close_pct",
    "t2_open": "ret_t2_open_pct",
}
SWEEP_K = [1, 2, 3, 5, 10]
SWEEP_DAYCHG_CAP = [None, 25.0, 20.0, 15.0]
SWEEP_MIN_VALUE = [None, 1e9, 5e9, 1e10]   # 거래대금 하한(원): 없음/10억/50억/100억
SWEEP_EXIT = ["next_open", "next_close", "t2_open"]


def db_connect():
    import psycopg2
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", 5434)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def load_trades(path):
    t = pd.read_csv(path)
    t["code"] = t["code"].astype(str).str.zfill(6)
    t["date"] = t["date"].astype(str)
    return t


def fetch_price_frame(conn, codes, dmin, dmax):
    q = ("SELECT stock_code, trade_date, open_price, close_price, trading_value "
         "FROM market_data WHERE stock_code = ANY(%s::text[]) "
         "AND trade_date BETWEEN %s AND %s")
    px = pd.read_sql(q, conn, params=[list(codes), dmin, dmax])
    px["stock_code"] = px["stock_code"].astype(str).str.zfill(6)
    return px.sort_values(["stock_code", "trade_date"])


def enrich_from_db(t, conn):
    """종가·거래대금 + 다음시가/다음종가/T+2시가 수익률을 일봉으로 채운다."""
    codes = t["code"].unique().tolist()
    dmin = (pd.to_datetime(t["date"]).min().date() - timedelta(days=5))
    dmax = (pd.to_datetime(t["date"]).max().date() + timedelta(days=8))
    px = fetch_price_frame(conn, codes, dmin, dmax)

    nxt_o, nxt_c, t2_o, close_map, tv_map = {}, {}, {}, {}, {}
    for code, g in px.groupby("stock_code", sort=False):
        d = list(g["trade_date"]); o = list(g["open_price"])
        cl = list(g["close_price"]); tv = list(g["trading_value"])
        for j in range(len(d)):
            close_map[(code, d[j])] = cl[j]
            tv_map[(code, d[j])] = tv[j]
        for j in range(len(d) - 1):
            nxt_o[(code, d[j])] = o[j + 1]
            nxt_c[(code, d[j])] = cl[j + 1]
        for j in range(len(d) - 2):
            t2_o[(code, d[j])] = o[j + 2]

    dd = pd.to_datetime(t["date"]).dt.date
    t = t.copy()
    t["close"] = [close_map.get((c_, d), np.nan) for c_, d in zip(t["code"], dd)]
    t["trading_value"] = [tv_map.get((c_, d), np.nan) for c_, d in zip(t["code"], dd)]
    t["ret_next_open_db"] = [_ret(nxt_o, c_, d, cl) for c_, d, cl in
                             zip(t["code"], dd, t["close"])]
    t["ret_next_close_pct"] = [_ret(nxt_c, c_, d, cl) for c_, d, cl in
                               zip(t["code"], dd, t["close"])]
    t["ret_t2_open_pct"] = [_ret(t2_o, c_, d, cl) for c_, d, cl in
                            zip(t["code"], dd, t["close"])]
    # next_open 은 trades.csv 의 기존 값을 기준값으로, DB 재계산이 일치하는지 검증만 남긴다.
    diff = (t["ret_next_open_pct"] - t["ret_next_open_db"]).abs().max()
    if diff > 1e-6:
        print(f"[warn] next_open trades.csv vs DB 최대 차이 {diff:.4f}%p — 일봉이 갱신됐을 수 있음",
              file=sys.stderr)
    return t.drop(columns=["ret_next_open_db"])


def _ret(m, code, d, close):
    if close is None or close != close or close == 0:
        return np.nan
    v = m.get((code, d))
    if v is None:
        return np.nan
    return (v / close - 1.0) * 100.0


def apply_filters(t, a):
    n0 = len(t)
    if a.exclude_limit_up:
        cap = getattr(a, "limit_up_pct", LIMIT_UP_PCT)
        t = t[t["day_change_pct"] < cap]
    if a.max_day_chg is not None:
        t = t[t["day_change_pct"] < a.max_day_chg]
    if a.min_price is not None:
        t = t[t["close"] >= a.min_price]
    if a.max_price is not None:
        t = t[t["close"] <= a.max_price]
    if a.min_value is not None:
        t = t[t["trading_value"] >= a.min_value]
    return t, n0 - len(t)


def round_trip(a):
    return (a.fee_buy + a.fee_sell + a.tax_sell) * 100.0


def session_stats(per_session, dates=None):
    vals = [v for v in per_session if v is not None and not math.isnan(v)]
    n = len(vals)
    if not n:
        return {"n_sessions": 0}
    mean = sum(vals) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / (n - 1)) if n > 1 else 0.0
    t = (mean / (sd / math.sqrt(n))) if sd > 0 else 0.0
    sv = sorted(vals)
    worst_i = min(range(len(vals)), key=lambda i: vals[i])
    out = {
        "n_sessions": n,
        "avg_pct": round(mean, 4),
        "median_pct": round(sv[n // 2], 4),
        "pos_sessions_pct": round(100 * sum(1 for v in vals if v > 0) / n, 1),
        "t_stat": round(t, 2),
        "sd_pct": round(sd, 3),
        "worst_session_pct": round(min(vals), 4),
        "best_session_pct": round(max(vals), 4),
    }
    if dates is not None:
        out["worst_session_date"] = str(dates[worst_i])
    return out


def simulate(t, topk, exit_col, rt):
    """세션별 score 상위 topk 동일비중 net 평균 리스트 + 세션 날짜 리스트."""
    by_date = defaultdict(list)
    for r in t.itertuples():
        if getattr(r, exit_col) is None or (getattr(r, exit_col) != getattr(r, exit_col)):
            continue
        by_date[r.date].append((r.score, getattr(r, exit_col) - rt))
    dates = sorted(by_date)
    per_session, detail = [], {}
    for d in dates:
        picked = sorted(by_date[d], key=lambda x: -x[0])[:topk]
        if not picked:
            per_session.append(None)
            continue
        sess = sum(v for _, v in picked) / len(picked)
        per_session.append(sess)
        detail[d] = {"n_pick": len(picked), "avg_net_pct": round(sess, 4)}
    return per_session, dates, detail


def split_half(per_session, dates):
    """연속 구간 앞/뒤 절반의 세션 통계와 양쪽 모두 양(+)인지 판정."""
    n = len(per_session)
    if n < 2:
        return None
    half = n // 2
    front = session_stats(per_session[:half])
    back = session_stats(per_session[half:])
    front_pos = front.get("avg_pct", 0) > 0
    back_pos = back.get("avg_pct", 0) > 0
    stable = "both_positive" if (front_pos and back_pos) else \
             ("neither" if (not front_pos and not back_pos) else "unstable")
    return {"front": front, "back": back, "stable": stable,
            "split_date": str(dates[half]) if half < len(dates) else None}


def describe_filter(a):
    parts = []
    if a.exclude_limit_up:
        parts.append(f"excl_limit_up(>{getattr(a, 'limit_up_pct', LIMIT_UP_PCT)})")
    if a.max_day_chg is not None:
        parts.append(f"daychg<{a.max_day_chg}")
    if a.min_price is not None:
        parts.append(f"price>={a.min_price}")
    if a.max_price is not None:
        parts.append(f"price<={a.max_price}")
    if a.min_value is not None:
        parts.append(f"value>={a.min_value:g}")
    return "_".join(parts) if parts else "none"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="체결 가능성 조건 하 순기대값 판정")
    ap.add_argument("--trades", default="data/reports/close_gate_probe/trades.csv")
    ap.add_argument("--topk", type=int, default=3, help="세션당 score 상위 K 종목")
    ap.add_argument("--exit", choices=list(EXIT_COLS), default="next_open",
                    help="청산: 다음 시가 / 다음 종가 / T+2 시가")
    ap.add_argument("--max-day-chg", type=float, default=None,
                    help="당일등락 상한(%%): 이 이상 오른 후보 제외")
    ap.add_argument("--exclude-limit-up", action="store_true", help="상한가 부근 제외")
    ap.add_argument("--limit-up-pct", type=float, default=LIMIT_UP_PCT)
    ap.add_argument("--min-price", type=float, default=None, help="종가 하한(원)")
    ap.add_argument("--max-price", type=float, default=None, help="종가 상한(원)")
    ap.add_argument("--min-value", type=float, default=None, help="거래대금 하한(원)")
    ap.add_argument("--fee-buy", type=float, default=FEE_BUY)
    ap.add_argument("--fee-sell", type=float, default=FEE_SELL)
    ap.add_argument("--tax-sell", type=float, default=TAX_SELL)
    ap.add_argument("--no-db", action="store_true", help="DB 조인 생략(next_open 외 측정 불가)")
    ap.add_argument("--sweep", action="store_true", help="전체 축 조합 스윕 + 분할 표본 안정성")
    ap.add_argument("--worst-tolerable", type=float, default=-5.0,
                    help="감내 가능한 최악 세션 손실(%p) 판정 기준")
    ap.add_argument("--json-out", default="data/reports/fillable_expectancy.json")
    a = ap.parse_args(argv)

    if not os.path.exists(a.trades):
        print(f"trades 파일 없음: {a.trades}", file=sys.stderr)
        return 2
    t = load_trades(a.trades)
    if not a.no_db:
        t = enrich_from_db(t, db_connect())
    rt = round_trip(a)

    if a.sweep:
        return run_sweep(t, a, rt)

    t_f, dropped = apply_filters(t, a)
    exit_col = EXIT_COLS[a.exit]
    per_session, dates, detail = simulate(t_f, a.topk, exit_col, rt)
    st = session_stats(per_session, dates)
    sh = split_half(per_session, dates)
    print(f"표본: {len(t_f)}후보 / {len({r.date for r in t_f.itertuples()})}세션 "
          f"(필터 '{describe_filter(a)}' 제외 {dropped}건, 원본 {len(t)}건) "
          f"· 수수료 왕복 {rt:.2f}%p · topk={a.topk} · exit={a.exit}")
    print(json.dumps({"filter": describe_filter(a), "topk": a.topk, "exit": a.exit,
                      "round_trip_pct": rt, "n_rows": len(t_f), **st,
                      "split_half": sh}, ensure_ascii=False, indent=2))
    os.makedirs(os.path.dirname(a.json_out), exist_ok=True)
    with open(a.json_out, "w", encoding="utf-8") as f:
        json.dump({"filter": describe_filter(a), "topk": a.topk, "exit": a.exit,
                   "round_trip_pct": rt, "n_rows": len(t_f), "stats": st,
                   "split_half": sh}, f, ensure_ascii=False, indent=2)
    return 0


def run_sweep(t, a, rt):
    rows = []
    n_combos = len(SWEEP_K) * len(SWEEP_DAYCHG_CAP) * len(SWEEP_MIN_VALUE) * len(SWEEP_EXIT)
    for k in SWEEP_K:
        for cap in SWEEP_DAYCHG_CAP:
            for mv in SWEEP_MIN_VALUE:
                for ex in SWEEP_EXIT:
                    f = t
                    f = f[f["day_change_pct"] < cap] if cap is not None else f
                    f = f[f["trading_value"] >= mv] if mv is not None else f
                    exit_col = EXIT_COLS[ex]
                    ps, dates, _ = simulate(f, k, exit_col, rt)
                    st = session_stats(ps, dates)
                    sh = split_half(ps, dates)
                    rows.append({
                        "K": k, "cap": cap, "min_value": mv, "exit": ex,
                        "n_sessions": st.get("n_sessions", 0),
                        "avg_pct": st.get("avg_pct", None),
                        "median_pct": st.get("median_pct", None),
                        "pos_pct": st.get("pos_sessions_pct", None),
                        "t_stat": st.get("t_stat", None),
                        "worst": st.get("worst_session_pct", None),
                        "best": st.get("best_session_pct", None),
                        "split_stable": (sh or {}).get("stable"),
                        "split_front_avg": (sh or {}).get("front", {}).get("avg_pct"),
                        "split_back_avg": (sh or {}).get("back", {}).get("avg_pct"),
                    })
    R = pd.DataFrame(rows)
    R = R.sort_values("avg_pct", ascending=False).reset_index(drop=True)

    def fmt(v):
        return f"{v:+.3f}" if v is not None and v == v else "  n/a"

    print(f"스윕 축 조합 수 N = {n_combos} (K×당일등락상한×거래대금하한×청산)")
    print("상위 25개(세션 평균 net 기준):")
    print(f"{'K':>3} {'cap<':>6} {'min값':>9} {'exit':>10} {'세션':>4} "
          f"{'avg%':>9} {'med%':>8} {'양세션%':>7} {'t':>7} {'worst%':>8} {'best%':>8} {'안정':>11}")
    for _, r in R.head(25).iterrows():
        print(f"{r.K:>3} {str(r.cap):>6} {fmt_val(r.min_value):>9} {r.exit:>10} "
              f"{r.n_sessions:>4} {fmt(r.avg_pct):>9} {fmt(r.median_pct):>8} "
              f"{fmt(r.pos_pct):>7} {fmt(r.t_stat):>7} {fmt(r.worst):>8} {fmt(r.best):>8} "
              f"{r.split_stable:>11}")

    pass_ = R[(R.avg_pct > 0) & (R.t_stat >= 2) & (R.worst >= a.worst_tolerable)]
    has_cap = ~R.cap.isna()
    pass_fillable = R[has_cap & (R.avg_pct > 0) & (R.t_stat >= 2) & (R.worst >= a.worst_tolerable)]
    n_fillable = int(has_cap.sum())
    print(f"\n판정 기준: 세션 평균 net > 0 AND t ≥ 2 AND 최악 세션 ≥ {a.worst_tolerable}%p")
    print(f"충족 조합 수: {len(pass_)} / {len(R)}  "
          f"(그중 체결 가능·캡 적용 조합: {len(pass_fillable)} / {n_fillable})")
    best_t = R.t_stat.max()
    best_t_fill = R.loc[has_cap, 't_stat'].max() if n_fillable else float('nan')
    print(f"전체 최고 t = {best_t:+.2f} · 체결 가능 조합 최고 t = {best_t_fill:+.2f} "
          f"(N={len(R)} 조합 훑음 — 다중비교 과최적화 위험)")
    if len(pass_):
        print("\n[충족 조합 — 1순위 제시]")
        for _, r in pass_.head(10).iterrows():
            tag = "체결가능" if r.cap == r.cap else "비체결(상한가 포함)"
            print(f"  K={r.K} cap<{fmt_val(r.cap)} min값={fmt_val(r.min_value)} exit={r.exit} "
                  f"[{tag}] → avg {r.avg_pct:+.3f}% t={r.t_stat:+.2f} worst {r.worst:+.2f}% "
                  f"안정={r.split_stable}")
    else:
        print("\n[충족 조합 없음] — 체결 가능 조건 하 순기대값이 양(+)인 조합은 관측되지 않음")

    os.makedirs(os.path.dirname(a.json_out), exist_ok=True)
    out = {"n_combos": n_combos, "n_fillable_combos": n_fillable,
           "criteria": {"avg_gt_0": True, "t_ge_2": 2.0,
                        "worst_ge_pct": a.worst_tolerable},
           "best_t_stat": float(best_t),
           "best_t_stat_fillable": float(best_t_fill) if n_fillable else None,
           "n_pass": int(len(pass_)),
           "n_pass_fillable": int(len(pass_fillable)),
           "pass_combos": _json_safe(pass_.to_dict(orient="records")),
           "all_combos": _json_safe(R.to_dict(orient="records"))}
    with open(a.json_out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n기록: {a.json_out}")
    return 0


def fmt_val(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "없음"
    if v >= 1e8:
        return f"{v / 1e8:g}억"
    return f"{v:g}"


def _json_safe(o):
    """NaN → None (strict JSON 을 위해)."""
    if isinstance(o, dict):
        return {k: _json_safe(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_json_safe(v) for v in o]
    if isinstance(o, float) and math.isnan(o):
        return None
    return o


if __name__ == "__main__":
    sys.exit(main())
