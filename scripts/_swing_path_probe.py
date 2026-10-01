#!/usr/bin/env python3
"""_swing_path_probe.py — swing 경로(모델 확률 리스트)의 실현 기대값 측정 (프로브, 읽기 전용).

무엇을 재나
  ml_predictions 의 모델 확률(confidence)을 신호로, 트레이더 swing 프로파일의 규칙을 그대로
  적용했을 때의 성과를 market_data 일봉으로 계산한다:
    · 진입: 신호일 D 다음 거래일 **시가**(entry_window 09:00-15:20 의 근사 — 실제는 장중 체결)
    · 청산: 손절 −7%(저가 터치 시 그 가격) / 익절 +15%(고가 터치 시 그 가격) / 둘 다 아니면 보유
    · R1 문턱(0.58) 통과 여부로 '살 수 있었던 집합'과 '막힌 집합'을 분리
  대조군: 같은 기간 **유니버스 전체 평균** 전방수익률(모델 선별이 실제로 값을 더하는가).

한계(보고에 반드시 함께)
  · ml_predictions 커버리지가 좁다(현재 8거래일). 표본이 작으면 그 사실을 숫자로 밝힌다.
  · 진입가 = 다음날 시가 근사(실제는 09:00-15:20 장중 체결).
  · 일봉으로 손절/익절 터치를 판정 → 같은 봉에서 둘 다 터치하면 **손절 우선**(보수적).
  · 보유 상한은 swing 프로파일에는 없다(max_hold 는 rank_momentum 전용) → 여기서는 최대 hold_days
    세션 뒤 종가로 시간청산하고 '미확정'으로 표시한다.

사용
  cd /home/jhshi/analyist_dd && set -a && . ./.env && set +a
  export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5434
  /usr/bin/python3 scripts/_swing_path_probe.py --topn 3,5,10 --hold-days 10
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date

import numpy as np
import pandas as pd

R1_MIN_PROB = 0.58      # runner/config.py: r1_min_avg_prob["swing"]
STOP = -7.0
TP = 15.0


def pg():
    import psycopg2
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", 5434)),
        dbname=os.environ.get("POSTGRES_DB", "stock_trading"),
        user=os.environ.get("POSTGRES_USER", "stock_user"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )


def stats(vals):
    if not len(vals):
        return {"n": 0}
    a = np.array(vals, dtype=float)
    return {"n": int(len(a)), "win_rate": round(float((a > 0).mean() * 100), 1),
            "avg": round(float(a.mean()), 3), "median": round(float(np.median(a)), 3),
            "p10": round(float(np.percentile(a, 10)), 2),
            "p90": round(float(np.percentile(a, 90)), 2),
            "min": round(float(a.min()), 2), "max": round(float(a.max()), 2)}


def simulate(bars, hold_days):
    """bars: 진입일부터의 (date, open, high, low, close) 오름차순. → (수익률%, 사유, 세션수)."""
    if bars.empty:
        return None
    entry = float(bars.iloc[0]["open_price"])
    if entry <= 0:
        return None
    stop_px = entry * (1 + STOP / 100.0)
    tp_px = entry * (1 + TP / 100.0)
    for i, r in enumerate(bars.iloc[1:hold_days + 1].itertuples()):
        hit_stop = float(r.low_price) <= stop_px
        hit_tp = float(r.high_price) >= tp_px
        if hit_stop:                      # 같은 봉에서 둘 다면 손절 우선(보수적)
            return (STOP, "stop", i + 2)
        if hit_tp:
            return (TP, "tp", i + 2)
    last = bars.iloc[min(hold_days, len(bars) - 1)]
    ret = (float(last["close_price"]) / entry - 1) * 100.0
    return (ret, "open(기간부족/미확정)" if len(bars) - 1 < hold_days else "time(hold)", len(bars))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topn", default="3,5,10")
    ap.add_argument("--hold-days", type=int, default=10)
    ap.add_argument("--since", default="2026-09-01")
    ap.add_argument("--out", default="data/reports/swing_path_probe.json")
    args = ap.parse_args()
    topns = [int(x) for x in args.topn.split(",")]

    conn = pg()
    preds = pd.read_sql(
        "SELECT prediction_date, stock_code, confidence::float8 AS conf "
        "FROM ml_predictions WHERE prediction_date >= %s", conn, params=(args.since,))
    if preds.empty:
        print("ml_predictions 없음"); return 2
    codes = sorted(preds["stock_code"].unique())
    bars = pd.read_sql(
        "SELECT stock_code, trade_date, open_price::float8 AS open_price, "
        "high_price::float8 AS high_price, low_price::float8 AS low_price, "
        "close_price::float8 AS close_price FROM market_data WHERE trade_date >= %s "
        "ORDER BY stock_code, trade_date", conn, params=(args.since,))
    bars = bars[bars["stock_code"].isin(codes)].copy()
    by_code = {c: g.reset_index(drop=True) for c, g in bars.groupby("stock_code")}
    cal = sorted(bars["trade_date"].unique())

    out = {"measured_at": pd.Timestamp.now().isoformat(), "since": args.since,
           "hold_days": args.hold_days, "stop_pct": STOP, "tp_pct": TP,
           "pred_dates": [str(d) for d in sorted(preds["prediction_date"].unique())],
           "n_universe_codes": int(len(codes)), "entry_assumption":
           "신호일 다음 거래일 시가 진입(실제 09:00-15:20 장중 근사)", "topn": {}}

    # 대조군: 유니버스 전체를 같은 규칙으로(임의 진입). 표본이 커서 별도 집계.
    universe_rets = []
    for c, g in by_code.items():
        g = g.reset_index(drop=True)
        for i in range(1, len(g)):
            r = simulate(g.iloc[i:], args.hold_days)
            if r:
                universe_rets.append(r[0])
    out["universe_all_entries"] = stats(universe_rets)

    for n in topns:
        rows = []
        for d in sorted(preds["prediction_date"].unique()):
            day = preds[preds["prediction_date"] == d].sort_values("conf", ascending=False)
            daily = day.head(20)
            r1_ok = float(daily["conf"].head(10).mean()) >= R1_MIN_PROB
            picks = day.head(n)
            for r in picks.itertuples():
                g = by_code.get(r.stock_code)
                if g is None:
                    continue
                nxt = [i for i, x in enumerate(cal) if x > d]
                if not nxt:
                    continue
                i0 = nxt[0]
                sub = g[g["trade_date"] >= cal[i0]].reset_index(drop=True)
                res = simulate(sub, args.hold_days)
                if res:
                    rows.append({"date": str(d), "code": r.stock_code, "conf": round(r.conf, 4),
                                 "ret_pct": round(res[0], 3), "exit": res[1],
                                 "sessions": res[2], "r1_ok": r1_ok,
                                 "avg_conf_top10": round(float(daily["conf"].head(10).mean()), 4)})
        df = pd.DataFrame(rows)
        if df.empty:
            out["topn"][str(n)] = {"n": 0}
            continue
        r1_fail, r1_pass = df[~df["r1_ok"]], df[df["r1_ok"]]
        out["topn"][str(n)] = {
            "all": stats(df["ret_pct"]), "r1_fail_only": stats(r1_fail["ret_pct"]),
            "r1_pass_only": stats(r1_pass["ret_pct"]),
            "exits": {k: int(v) for k, v in df["exit"].value_counts().items()},
            "days": int(df["date"].nunique()),
            "per_day_avg": {d: round(float(g["ret_pct"].mean()), 2) for d, g in df.groupby("date")},
        }
        path = args.out if os.path.isabs(args.out) else os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), args.out)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if "detail_csv" not in out:
            df.to_csv(path.replace(".json", "_detail.csv"), index=False)
        out["detail_csv"] = path.replace(".json", "_detail.csv")

    path = args.out if os.path.isabs(args.out) else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), args.out)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=2)[:6000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
